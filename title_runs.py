"""
타이틀 '런(run)' 인식 — 같은 IP가 여러 번 출시된 걸 회차별로 분리.

연도로 자르면 안 되는 이유: 원위 25.12 런은 2025-12-25~2026-01-14 로 연말을 넘어감.
연말에 자르면 "26년에 급감"으로 잘못 읽힘.
그래서 런 = 실제 연속 판매 구간(공백 기준 분할) + Jira 시작/종료일로 경계 보정.
"""
import re
import json
from pathlib import Path

import pandas as pd

BASE_DIR    = Path(__file__).parent
ALIAS_FILE  = BASE_DIR / "ip_aliases.json"

# 런 분리 기준: 판매가 이 일수 이상 끊기면 다른 런으로 본다.
GAP_DAYS = 21

# Jira 타이틀 정리용 — "25.10 QWER 아티스트 프레임" → "QWER"
# 출시월 접두가 세 형태로 섞여 있다: "25.10 " / "26.2 "(월 1자리) / "260605 "(YYMMDD).
# 앞에 "렌탈 "이 더 붙기도 한다("렌탈 260518 태양 팝업부스").
_DATE_RE  = re.compile(r"^\s*(렌탈\s*)?(\d{6}|\d{2}[.\-/]\d{1,2})\s*")
_TAG_RE   = re.compile(r"\[[^\]]*\]")                    # [KR] [캐릭터/스내피즘]
_PAREN_RE = re.compile(r"\([^)]*\)")                     # (한국, 일본) (스내피즘)
_SUFFIX_RE = re.compile(
    r"((아티스트|캐릭터)\s*)?(스티커\s*프레임|포토\s*카드|스티커|프레임|스내피즘|포토이즘|굿즈|포카)[\s,·]*",
    re.IGNORECASE,
)


def _load_aliases():
    """ip_aliases.json → {별칭(정규화): 대표명(정규화)}"""
    try:
        raw = json.loads(ALIAS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}
    out = {}
    for canon, variants in raw.items():
        if canon.startswith("_"):      # _comment 등 메타 키 제외
            continue
        key = _squash(canon)
        for v in variants:
            out[_squash(v)] = key
    return out


def _squash(s):
    return re.sub(r"[\s,·]+", "", str(s)).strip().lower()


_ALIASES = _load_aliases()


def normalize_title(name):
    """매출 '프레임 이름' 과 Jira 타이틀을 같은 키로 맞춘다."""
    s = _TAG_RE.sub(" ", str(name))
    for _ in range(2):                 # "[KR]260710 …" 처럼 태그를 걷어내면 날짜가 다시 앞에 온다
        s = _DATE_RE.sub("", s.strip())
    s = _PAREN_RE.sub(" ", s)
    for _ in range(4):                 # "아티스트 스티커 프레임" 처럼 겹쳐 붙은 접미 제거
        s = _SUFFIX_RE.sub(" ", s)
    s = _squash(s)
    return _ALIASES.get(s, s)


def _jira_by_key(jira_map):
    """Jira 매핑 → {정규화키: [엔트리...]} (한 IP에 회차별 티켓이 여러 개일 수 있음)"""
    out = {}
    for title, entry in (jira_map or {}).items():
        e = dict(entry)
        e.setdefault("title", title)
        out.setdefault(normalize_title(title), []).append(e)
    return out


def _pick_ticket(entries, first_day, last_day):
    """
    런 기간과 실제로 겹치는 Jira 티켓 중 가장 많이 겹치는 걸 고른다.

    겹치는 티켓이 없으면 None — 억지로 붙이지 않는다.
    (같은 IP라도 회차 티켓이 없을 수 있는데, 아무거나 붙이면
     '오픈지연 -418일' 같은 헛값이 나온다.)
    한쪽 날짜만 있는 티켓은 그 날짜 하루짜리로 보고 판단한다.
    런 기간을 last_day 로 늘려잡으면 겹침이 부풀려져서 오탐이 생김.
    """
    best, best_ov = None, 0
    for e in entries:
        s = pd.to_datetime(e.get("startdate"), errors="coerce")
        d = pd.to_datetime(e.get("duedate"),   errors="coerce")
        if pd.isna(s) and pd.isna(d):
            continue
        s = s.date() if not pd.isna(s) else d.date()
        d = d.date() if not pd.isna(d) else s
        if d < s:
            s, d = d, s
        ov = (min(d, last_day) - max(s, first_day)).days + 1   # 겹치는 일수
        if ov > best_ov:
            best, best_ov = e, ov
    return best, best_ov


def build_runs(df, jira_map=None, gap_days=GAP_DAYS,
               title_col="프레임 이름", date_col="날짜", amount_col="KRW환산금액"):
    """
    거래 데이터 → 타이틀×런 단위 요약.

    반환 컬럼:
      타이틀, 런번호, 첫거래일, 마지막거래일, 판매일수, 건수, 매출, 일평균매출,
      매장수, 일평균건수, 계획시작일, 계획종료일, 티켓, 오픈지연일
    """
    if df is None or df.empty or title_col not in df.columns:
        return pd.DataFrame()

    d = df[df[title_col].notna()].copy()
    if d.empty:
        return pd.DataFrame()

    d["_날짜"] = pd.to_datetime(d[date_col], errors="coerce").dt.date
    d = d[d["_날짜"].notna()]
    if amount_col not in d.columns:
        d[amount_col] = 0
    d["_매출"] = pd.to_numeric(d[amount_col], errors="coerce").fillna(0)

    jira_keyed = _jira_by_key(jira_map)
    rows = []

    for title, g in d.groupby(title_col, sort=False):
        g = g.sort_values("_날짜")
        days = pd.Series(sorted(g["_날짜"].unique()))
        # 판매 공백이 gap_days 이상이면 새 런 (date 객체라 datetime 으로 올려서 차이 계산)
        gaps = pd.to_datetime(days).diff().dt.days.fillna(0)
        run_no = (gaps >= gap_days).cumsum() + 1
        day2run = dict(zip(days, run_no))
        g["_런"] = g["_날짜"].map(day2run)

        entries = jira_keyed.get(normalize_title(title), [])

        for rn, rg in g.groupby("_런"):
            first, last = rg["_날짜"].min(), rg["_날짜"].max()
            span = (last - first).days + 1
            ticket, _ = _pick_ticket(entries, first, last)

            plan_s = pd.to_datetime((ticket or {}).get("startdate"), errors="coerce")
            plan_d = pd.to_datetime((ticket or {}).get("duedate"),   errors="coerce")
            plan_s = plan_s.date() if not pd.isna(plan_s) else None
            plan_d = plan_d.date() if not pd.isna(plan_d) else None

            rows.append({
                "타이틀":     title,
                "런번호":     int(rn),
                "첫거래일":   first,
                "마지막거래일": last,
                "판매일수":   span,
                "건수":       len(rg),
                "매출":       int(rg["_매출"].sum()),
                "일평균매출": int(rg["_매출"].sum() / span) if span else 0,
                "일평균건수": round(len(rg) / span, 1) if span else 0,
                "매장수":     rg["매장 이름"].nunique() if "매장 이름" in rg.columns else 0,
                "계획시작일": plan_s,
                "계획종료일": plan_d,
                "티켓":       (ticket or {}).get("ticket_key"),
                # 계획보다 실제 판매가 며칠 늦게 시작됐나 (음수면 계획보다 빨리 열림)
                "오픈지연일": (first - plan_s).days if plan_s else None,
            })

    runs = pd.DataFrame(rows)
    if runs.empty:
        return runs
    return runs.sort_values(["타이틀", "런번호"]).reset_index(drop=True)


# 마지막 거래 후 이 일수 넘게 조용하면 '멈춘' 것으로 본다.
IDLE_DAYS = 7

# 상태 기호는 KPI 페이지(포토이즘 IP 무버)와 같은 뜻으로 맞춘다.
STATUS_ORDER = ["🔴 확인필요", "🔚 종료", "⏳ 종료예정", "🆕 신규", "🟢 판매중", "⚪ 미확인"]


def title_status(df, jira_map=None, period_start=None, period_end=None,
                 title_col="프레임 이름", date_col="날짜", idle_days=IDLE_DAYS):
    """
    타이틀별 판매기간 + 상태 → {타이틀: {...}}

    매출이 빠졌을 때 '끝나서 빠진 것'인지 '안 끝났는데 빠진 것'인지 가르는 게 목적.
    실제 거래일만으론 그 둘을 구분할 수 없어서(마지막 거래일이 같아 보임)
    Jira 종료일을 함께 본다. Jira 가 없는 타이틀은 단정하지 않고 '미확인'.

    ★ df 는 '기간으로 자르지 않은' 전체 이력이어야 한다.
      (국가·매장 같은 다른 필터는 적용해도 됨)
      기간으로 자른 걸 넘기면 첫 거래일이 전부 기간 시작일로 잡혀서
      죄다 '신규'로 나온다. 기간은 period_start/period_end 로만 판정한다.
    """
    if df is None or df.empty or title_col not in df.columns:
        return {}

    d = df[df[title_col].notna()].copy()
    d["_날짜"] = pd.to_datetime(d[date_col], errors="coerce").dt.date
    d = d[d["_날짜"].notna()]
    if d.empty:
        return {}

    ref = period_end or d["_날짜"].max()          # 판정 기준일 = 조회 기간 끝
    jira_keyed = _jira_by_key(jira_map)
    out = {}

    for title, g in d.groupby(title_col, sort=False):
        first, last = g["_날짜"].min(), g["_날짜"].max()

        # 이 타이틀의 Jira 종료일 중 기준일에 가장 가까운(=현재 회차) 것
        due = None
        for e in jira_keyed.get(normalize_title(title), []):
            v = pd.to_datetime(e.get("duedate"), errors="coerce")
            if pd.isna(v):
                continue
            v = v.date()
            if due is None or abs((v - ref).days) < abs((due - ref).days):
                due = v

        # 조회 기간 뒤로도 팔리고 있으면(last > ref) 유휴 아님 → 0
        idle = max(0, (ref - last).days)
        is_new = period_start is not None and first >= period_start

        if due and due < ref:
            status = "🔚 종료"          # 예정된 하락 — 급감해도 정상
        elif is_new:
            status = "🆕 신규"
        elif due and idle >= idle_days:
            status = "🔴 확인필요"      # 판매기간이 남았는데 멈춤 → 점검 대상
        elif due:
            status = "⏳ 종료예정" if (due - ref).days <= 30 else "🟢 판매중"
        elif idle >= idle_days:
            status = "⚪ 미확인"        # Jira 미연결이라 종료인지 단정 못 함
        else:
            status = "🟢 판매중"

        out[title] = {
            "상태":       status,
            "첫거래일":   first,
            "마지막거래일": last,
            "종료일":     due,
            "유휴일":     idle,
        }
    return out


def coverage(runs):
    """Jira 매칭 커버리지 — 화면에 '몇 %가 일정 연결됨'을 표시하기 위한 값."""
    if runs is None or runs.empty:
        return {"런수": 0, "매칭런": 0, "매칭률": 0.0, "매출커버율": 0.0}
    matched = runs["티켓"].notna()
    total_rev = runs["매출"].sum()
    return {
        "런수":       len(runs),
        "매칭런":     int(matched.sum()),
        "매칭률":     round(matched.mean() * 100, 1),
        "매출커버율": round(runs.loc[matched, "매출"].sum() / total_rev * 100, 1) if total_rev else 0.0,
    }
