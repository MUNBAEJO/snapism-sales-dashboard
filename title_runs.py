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
# Jira 매칭 전용 별칭(한글 IP명 등). ip_aliases.json 은 포토이즘 집계가 쓰므로
# 그쪽을 건드리지 않으려고 파일을 분리했다.
TITLE_ALIAS_FILE = BASE_DIR / "title_aliases.json"

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
    """ip_aliases.json + title_aliases.json → {별칭(정규화): 대표명(정규화)}"""
    out = {}
    for path in (ALIAS_FILE, TITLE_ALIAS_FILE):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
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


def _exact_key(name):
    """날짜 접두를 '살린' 키. 포토이즘 타이틀('260605 AG-ENT')은 Jira 에도
    같은 날짜로 등록돼 있어(CANDIP-25986 '260605 AG-ENT') 회차까지 정확히 맞출 수 있다.
    날짜를 떼면 같은 IP의 다른 회차 티켓과 섞인다."""
    s = _TAG_RE.sub(" ", str(name))
    m = re.match(r"^\s*(렌탈\s*)?(\d{6})\s*(.+)$", s.strip())
    if not m:
        return None
    rest = _PAREN_RE.sub(" ", m.group(3))
    for _ in range(4):
        rest = _SUFFIX_RE.sub(" ", rest)
    rest = _squash(rest)
    rest = _ALIASES.get(rest, rest)
    return f"{m.group(2)}|{rest}" if rest else None


def _jira_by_key(jira_map):
    """Jira 매핑 → {정규화키: [엔트리...]} (한 IP에 회차별 티켓이 여러 개일 수 있음)"""
    out = {}
    for title, entry in (jira_map or {}).items():
        e = dict(entry)
        e.setdefault("title", title)
        out.setdefault(normalize_title(title), []).append(e)
        ek = _exact_key(title)
        if ek:
            out.setdefault(ek, []).append(e)
    return out


# 렌탈·팝업부스 행사 티켓 표식. 일반 타이틀 매출에 이런 티켓이 붙으면 기간이 딴판이다.
# 예: 매출 '260226 티니핑'(캐릭터)에 CANDIP-17621
#     '26.02 티니핑 캐릭터 팝업 포토부스 (rt-2509-g)' 이 붙어 종료일이 어긋났다.
# RT-2509-G 같은 코드는 렌탈부스 식별자(스내피즘 매장명에도 'RT-2510-D 렌탈부스'로 나온다).
_RENTAL_RE = re.compile(r"(렌탈|대여|팝업\s*부스|포토\s*부스|부스|rt-?\d{4})", re.IGNORECASE)


def _is_rental(name):
    return bool(_RENTAL_RE.search(str(name)))


def _tokens(name):
    """접두(날짜)·접미(상품유형)·괄호를 뗀 뒤 토큰 목록. 접두 매칭용."""
    s = _TAG_RE.sub(" ", str(name))
    for _ in range(2):
        s = _DATE_RE.sub("", s.strip())
    s = _PAREN_RE.sub(" ", s)
    for _ in range(4):
        s = _SUFFIX_RE.sub(" ", s)
    return [t for t in re.split(r"[\s,·_]+", s.strip().lower()) if t]


def _entries_for(jira_keyed, title, token_index=None):
    """
    그 타이틀의 후보 티켓.
      ① 날짜까지 맞는 키가 있으면 그것만(회차 정확)
      ② 정규화 키 완전일치
      ③ 그래도 없으면 '토큰 접두' 일치 — Jira 쪽에 수식어가 더 붙은 경우.
         예: 매출 'NAZE' ↔ Jira 'NAZE 신오쿠보 특별관',
             매출 '코르티스' ↔ Jira '코르티스 2026년 계약 2차'.
         ★문자 단위가 아니라 토큰 단위라 '로아'가 '로아온라인'에 붙지 않는다.
    """
    # 렌탈·팝업부스 행사 티켓은 일반 타이틀에 붙이지 않는다(기간이 딴판).
    # 매출 쪽이 렌탈이면 그대로 둔다 — 그때는 맞는 티켓이니까.
    src_rental = _is_rental(title)

    def _ok(es):
        return es if src_rental else [e for e in es
                                      if not _is_rental(e.get("title", "") or e.get("wbs_raw", ""))]

    ek = _exact_key(title)
    if ek and _ok(jira_keyed.get(ek, [])):
        return _ok(jira_keyed[ek])
    hit = _ok(jira_keyed.get(normalize_title(title), []))
    if token_index:
        # ★정규화로 찾았어도 토큰 후보를 '합친다'. 이름이 짧은 티켓만 잡히고
        #  수식어가 붙은 진짜 회차 티켓을 놓치기 때문 — 코르티스는 '코르티스'로
        #  잡히는 옛 티켓(종료 06-30) 때문에 '코르티스 2026년 계약 2차'
        #  (CANDIP-22032, 종료 2027-03-31)가 후보에서 빠졌다.
        tk = tuple(_tokens(title))
        if tk:
            seen = {id(e) for e in hit}
            keys = {e.get("ticket_key") for e in hit if e.get("ticket_key")}
            for e in _ok(token_index.get(tk, [])):
                if id(e) not in seen and e.get("ticket_key") not in keys:
                    hit.append(e)
    return hit


def _token_prefix_index(jira_map):
    """{매출쪽 토큰열: [엔트리...]} — Jira 타이틀의 앞부분 토큰열을 전부 키로 등록."""
    out = {}
    for title, entry in (jira_map or {}).items():
        toks = _tokens(title)
        e = dict(entry)
        e.setdefault("title", title)
        # 앞에서부터 1개, 2개 … 토큰을 키로 (전체 일치는 이미 다른 경로에서 처리)
        for n in range(1, len(toks)):
            out.setdefault(tuple(toks[:n]), []).append(e)
    return out


def _pick_ticket(entries, first_day, last_day, prefer_brand="snapism"):
    """
    런 기간과 실제로 겹치는 Jira 티켓 중 가장 많이 겹치는 걸 고른다.

    겹치는 티켓이 없으면 None — 억지로 붙이지 않는다.
    (같은 IP라도 회차 티켓이 없을 수 있는데, 아무거나 붙이면
     '오픈지연 -418일' 같은 헛값이 나온다.)
    한쪽 날짜만 있는 티켓은 그 날짜 하루짜리로 보고 판단한다.
    런 기간을 last_day 로 늘려잡으면 겹침이 부풀려져서 오탐이 생김.
    """
    best, best_key = None, None
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
        if ov <= 0:
            continue
        # 같은 IP가 두 브랜드에 동시 출시되면 티켓이 둘 다 잡힌다.
        # 보고 있는 매출의 브랜드 티켓을 먼저 쓴다 — 브랜드마다 종료일이 다르다
        # (TREASURE: Snapism 08-31 / Photoism 09-30).
        # ★prefer_brand 를 고정하면 반대쪽 브랜드 화면에서 엉뚱한 티켓이 붙는다
        #  (포토이즘 AG-ENT 매출에 Snapism 티켓이 붙던 버그).
        same = prefer_brand in str(e.get("brand", "")).lower()
        key = (same, ov)
        if best_key is None or key > best_key:
            best, best_key = e, key
    return best, (best_key[1] if best_key else 0)


def build_runs(df, jira_map=None, gap_days=GAP_DAYS,
               title_col="프레임 이름", date_col="날짜", amount_col="KRW환산금액",
               prefer_brand="snapism", count_col=None):
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
    # 스내피즘은 거래 1건 = 1행이라 행 수가 곧 건수지만, 포토이즘은 집계본이라
    # 한 행이 여러 건을 담는다. count_col 이 오면 그 합을 건수로 쓴다.
    if count_col and count_col in d.columns:
        d["_건수"] = pd.to_numeric(d[count_col], errors="coerce").fillna(0)
    else:
        d["_건수"] = 1

    jira_keyed = _jira_by_key(jira_map)
    tok_idx = _token_prefix_index(jira_map)
    rows = []

    for title, g in d.groupby(title_col, sort=False, observed=True):
        g = g.sort_values("_날짜")
        days = pd.Series(sorted(g["_날짜"].unique()))
        # 판매 공백이 gap_days 이상이면 새 런 (date 객체라 datetime 으로 올려서 차이 계산)
        gaps = pd.to_datetime(days).diff().dt.days.fillna(0)
        run_no = (gaps >= gap_days).cumsum() + 1
        day2run = dict(zip(days, run_no))
        g["_런"] = g["_날짜"].map(day2run)

        entries = _entries_for(jira_keyed, title, tok_idx)

        for rn, rg in g.groupby("_런"):
            first, last = rg["_날짜"].min(), rg["_날짜"].max()
            span = (last - first).days + 1
            ticket, _ = _pick_ticket(entries, first, last, prefer_brand)

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
                "건수":       int(rg["_건수"].sum()),
                "매출":       int(rg["_매출"].sum()),
                "일평균매출": int(rg["_매출"].sum() / span) if span else 0,
                "일평균건수": round(rg["_건수"].sum() / span, 1) if span else 0,
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

# 종료일 이후 이 일수까지는 '기간 후 판매'로 보지 않는다.
# 포토이즘은 30개국이라 타임존 차이로 KST 기준 종료 다음날 새벽 거래가 찍히고,
# 월말 일괄 종료 뒤 며칠간 마무리 판매가 도는 운영 패턴도 있다
# (260601 시리즈 6개가 전부 '06-30 종료 → 07-03 마지막'으로 동일).
POST_GRACE_DAYS = 4

# 상태 기호는 KPI 페이지(포토이즘 IP 무버)와 같은 뜻으로 맞춘다.
STATUS_ORDER = ["🔴 확인필요", "⚠️ 기간후판매", "🔚 종료", "⏳ 종료예정",
                "🆕 신규", "🟢 판매중", "⚪ 미확인"]


def title_status(df, jira_map=None, period_start=None, period_end=None,
                 title_col="프레임 이름", date_col="날짜", idle_days=IDLE_DAYS,
                 prefer_brand="snapism", gap_days=GAP_DAYS):
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
    tok_idx = _token_prefix_index(jira_map)
    out = {}

    for title, g in d.groupby(title_col, sort=False, observed=True):
        # ★'가장 최근 런'만 본다. 타이틀 전체(first~last)로 보면 같은 이름으로
        #  두 번 출시된 게 한 덩어리가 된다 — 스내피즘 '허성범'은 2025-08~2026-07로
        #  11개월이 붙어 2025년 티켓이 매칭되고 2026년 거래가 '기간후판매'로 오탐났다.
        entries = _entries_for(jira_keyed, title, tok_idx)

        days = pd.Series(sorted(g["_날짜"].unique()))
        gaps = pd.to_datetime(days).diff().dt.days.fillna(0)
        run_no = (gaps >= gap_days).cumsum()
        cur = days[run_no == run_no.max()]

        # ★판매가 끊기지 않아도 회차가 바뀔 수 있다. Jira 에 이름이 같은 티켓이
        #  여러 개면 '가장 늦은 시작일'부터를 현재 회차로 본다.
        #  (로이킴: 05-09~06-30 티켓과 07-14~08-13 티켓이 있는데 거래는 연속이라
        #   공백 기준으로는 안 갈린다 → 07-14 부터가 새 회차)
        starts = []
        for e in entries:
            sd = pd.to_datetime(e.get("startdate"), errors="coerce")
            if not pd.isna(sd):
                starts.append(sd.date())
        if starts:
            cut = max(s for s in starts if s <= cur.max()) if any(s <= cur.max() for s in starts) else None
            if cut and cut > cur.min() and (cur >= cut).any():
                cur = cur[cur >= cut]

        first, last = cur.min(), cur.max()

        # 이 타이틀의 Jira 티켓 중 '실제 판매 기간과 겹치는' 것을 고른다.
        # ★기준일에 가장 가까운 종료일을 고르면 안 된다 — 정규화가 날짜를 떼기 때문에
        #  회차가 다른 타이틀이 같은 키로 뭉친다(포토이즘 '251201 다마고치'와
        #  '260701 다마고치'가 같은 '다마고치'). 그러면 2025년 출시분에 2026년
        #  종료일이 붙는다. _pick_ticket 은 기간이 겹칠 때만 연결한다.
        ticket, _ = _pick_ticket(entries, first, last, prefer_brand)
        due = pd.to_datetime((ticket or {}).get("duedate"), errors="coerce")
        due = due.date() if not pd.isna(due) else None

        # 조회 기간 뒤로도 팔리고 있으면(last > ref) 유휴 아님 → 0
        idle = max(0, (ref - last).days)
        is_new = period_start is not None and first >= period_start

        if due and due < ref and (last - due).days > POST_GRACE_DAYS:
            # 판매기간이 끝난 '뒤에도' 거래가 찍혔다 = '기간 후 매출'.
            # ★기준은 '종료일 이후 거래'다. '최근에 거래가 있음'으로 잡으면
            #  종료일 당일까지만 팔린 정상 종료까지 여기로 딸려온다.
            status = "⚠️ 기간후판매"
        elif due and due < ref:
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
