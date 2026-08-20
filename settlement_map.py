"""IP 정산서 — 티켓↔타이틀 매핑 코어.

정산은 **지라 티켓번호를 정(正)** 으로 잡는다. 타이틀은 교차검증 보조일 뿐이다.
현행 `title_runs` 의 자동 매칭은 동점일 때 캐시 파일 순서로 승자가 갈리므로
(재크롤하면 결과가 바뀐다) **정산에는 확정 매핑만 쓰고, 자동 매칭은 후보 제안에만** 쓴다.

핵심 개념
  후보(candidate)  자동 매칭이 제안하는 티켓들. 참고용.
  확정(mapping)    실무자가 승인해 저장한 타이틀→티켓. 정산은 이것만 본다.
  잔여(residual)   어느 티켓에도 확정되지 않은 매출. 0원이 될 때까지 발행을 막는다.

관련: CURRENT-PROJECTS/IP-정산서-생성.md · 지라 CO-288
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime
from pathlib import Path

import duckdb
import pandas as pd

import title_runs
from json_store import JsonStore

BASE_DIR = Path(__file__).parent
CONFIG = BASE_DIR / "config.json"
PH_AGG = BASE_DIR / "data" / "master_photoism_agg.parquet"
SN_MASTER = BASE_DIR / "data" / "master.parquet"
JIRA_CACHE = BASE_DIR / "data" / "jira_ip_dates_cache.json"

BRANDS = ("photoism", "snapism")
BRAND_LABEL = {"photoism": "포토이즘", "snapism": "스내피즘"}

# 정산 대상 IP 구분.
# ★렌탈을 **포함**한다 — 계약은 타이틀 단위라 그 타이틀에서 난 매출이면 어디서
#   팔렸든 IP 매출이다. 렌탈 브랜드(RNP) 안에는 롯데월드·스카이호텔 팝업부스점처럼
#   정상가로 파는 상설 매장과 일회성 렌탈부스가 섞여 있는데, 소분류
#   (Short/Long-term contract)로는 깔끔히 안 갈린다. 매장 화이트리스트를 관리하는
#   대신 타이틀을 기준으로 삼는다. 실무자가 티켓에 매핑한 타이틀만 정산되므로
#   '렌탈 260605 더세임_TREASURE' 처럼 이름부터 다른 건 사람이 판단해서 넣고 뺀다.
# 오리지널(자사 프레임)·스티커머신(별개 사업)만 제외한다.
#
# ★'폴라릿' 도 넣는다 (2026-08-11, 사용자 확인). 스내피즘의 폴라로이드 상품인데
#   프레임은 그대로 IP 다(베이온·루네이트·더윈드). 담당자는 **같은 IP 정산서에
#   폴라릿까지 한 번에** 보길 원한다. 빼 두면 베이온 8건이 조용히 새어나간다.
#   2026-07-23 에 시작한 신상품이라 전 기간 79건뿐이고, 포토이즘에는 이 구분이
#   아예 없어 영향이 스내피즘에만 미친다.
SETTLE_GUBUN = ("아티스트", "캐릭터", "PICK", "렌탈", "폴라릿")

# ★대시보드와 같은 프로세스에서 돈다. 정산 조회가 메모리를 크게 물면 매출 대시보드
#   동시접속자가 같이 죽는다(예전에 실제로 OOM 이 났다).
#   한도를 낮게 잡고 넘치면 디스크로 흘린다 — 조금 느려도 같이 죽는 것보다 낫다.
DUCK_MEM = "512MB"
DUCK_THREADS = 2
_TMP = BASE_DIR / "data" / "_duckdb_tmp"


def duck():
    """정산용 DuckDB 연결. 메모리 한도·스풀 경로를 한 곳에서 관리한다."""
    _TMP.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.execute(f"PRAGMA memory_limit='{DUCK_MEM}'")
    con.execute(f"PRAGMA threads={DUCK_THREADS}")
    con.execute(f"PRAGMA temp_directory='{_TMP.as_posix()}'")
    return con

_store = JsonStore("settlement_mapping.json",
                   default={"version": 1, "mappings": {b: {} for b in BRANDS}})


# ── 환율 ──────────────────────────────────────────────────────────────────
def load_rates(rate_date: str | None = None) -> tuple[dict, str, str]:
    """(환율dict, 실제 적용 기준일, 출처 문구).

    ★출처를 함께 돌려주는 이유 — 서울외국환중개(smbs.biz)는 공개 API 가 없고
      **과거 날짜 조회도 안 된다.** 과거 정산은 자동 조회값으로 폴백하는데,
      문서에 '서울외국환중개' 라고 찍으면 대외 문서에 틀린 출처가 박힌다.
      settlement_fx.resolve() 가 실제 출처를 판별해 준다.
    """
    try:
        import settlement_fx
        return settlement_fx.resolve(rate_date or "")
    except Exception:
        pass
    # ★`{"KRW": 1}` 을 돌려주면 안 된다 (2026-08-20). 아래 `_rate_case` 가
    #   `CASE … ELSE 1` 을 만들어 **엔·달러·바트가 전부 1:1 원화**가 되는데,
    #   '참고 환율' 이라는 라벨은 그대로라 화면상 멀쩡해 보인다.
    #   쓸 수 있는 통화가 2개 미만이면 **빈 표 + '환율 없음'** 으로 돌려주고,
    #   부르는 쪽(views/8)이 발행을 막게 한다.
    try:
        cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
        r = cfg.get("exchange_rates") or {}
        ok = [k for k, v in r.items()
              if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0]
        if len(ok) >= 2:
            return r, (rate_date or ""), "참고 환율"
    except Exception:
        pass
    return {}, (rate_date or ""), "환율 없음"


def _rate_case(rates: dict, col: str = '"결제 단위"') -> str:
    """DuckDB CASE 식으로 통화→원화 배수. 파라미터 바인딩이 안 되는 자리라 직접 만든다."""
    arms = " ".join(f"WHEN '{k}' THEN {float(v)}" for k, v in rates.items()
                    if isinstance(v, (int, float)) and v > 0)
    return f"CASE trim({col}) {arms} ELSE 1 END"


# ── 타이틀별 매출 ──────────────────────────────────────────────────────────
def title_revenue(brand: str, start: str, end: str, rates: dict) -> pd.DataFrame:
    """정산 대상 타이틀별 매출. 반환 컬럼: 타이틀 · IP구분 · 매출액 · 건수 · 국가수

    ★매출 기여가 0원인 거래는 제외한다. 안 빼면 건수만 부풀고 단가 평균이 내려간다.
    """
    from photoism_rules import COIN_CC, COUPON_CC

    rate = _rate_case(rates)
    gubun = ",".join(f"'{g}'" for g in SETTLE_GUBUN)
    con = duck()
    try:
        if brand == "photoism":
            cpn = ",".join(f"'{c}'" for c in sorted(COUPON_CC))
            coin = ",".join(f"'{c}'" for c in sorted(COIN_CC))
            df = con.execute(f"""
                WITH t AS (
                  SELECT trim(COALESCE("타이틀", '')) AS 타이틀, "IP구분",
                         lower(trim("국가코드")) AS cc, "국가",
                         "건수", "최종 결제 금액" AS pay, "쿠폰 할인 금액" AS cpn,
                         "서비스코인" AS coin, {rate} AS r
                  FROM read_parquet('{PH_AGG.as_posix()}')
                  WHERE "날짜" BETWEEN DATE '{start}' AND DATE '{end}'
                    AND "IP구분" IN ({gubun})
                    AND NOT COALESCE("취소 여부", FALSE)
                )
                SELECT 타이틀, any_value("IP구분") AS "IP구분",
                       CAST(ROUND(SUM(
                         pay * r
                         + CASE WHEN cc IN ({cpn})  THEN cpn  * r ELSE 0 END
                         + CASE WHEN cc IN ({coin}) THEN coin * r ELSE 0 END
                       )) AS BIGINT) AS 매출액,
                       CAST(SUM(CASE WHEN pay < 0 THEN -"건수" ELSE "건수" END) AS BIGINT) AS 건수,
                       COUNT(DISTINCT "국가") AS 국가수
                FROM t
                -- ★취소는 음수 거래로 들어온다. '> 0' 으로 거르면 차감이 안 된다.
                WHERE pay <> 0
                   OR (cc IN ({cpn})  AND cpn  <> 0)
                   OR (cc IN ({coin}) AND coin <> 0)
                GROUP BY 1 HAVING 매출액 > 0 ORDER BY 매출액 DESC
            """).df()
        else:
            # 스내피즘은 전 국가 쿠폰 가산. IP 필터는 반드시 '프레임 이름' —
            # 'IP 이름' 열은 대부분 비어 있어 쓰면 매출의 97% 가 날아간다.
            df = con.execute(f"""
                WITH t AS (
                  SELECT trim(COALESCE("프레임 이름", '')) AS 타이틀,
                         "카테고리" AS "IP구분", "국가",
                         CAST("최종 결제 금액" AS BIGINT) AS pay,
                         CAST("쿠폰 할인 금액"  AS BIGINT) AS cpn,
                         {rate} AS r
                  FROM read_parquet('{SN_MASTER.as_posix()}')
                  WHERE CAST("날짜" AS DATE) BETWEEN DATE '{start}' AND DATE '{end}'
                    AND "카테고리" IN ({gubun})
                    AND NOT COALESCE("취소 여부", FALSE)
                )
                SELECT 타이틀, any_value("IP구분") AS "IP구분",
                       CAST(ROUND(SUM((pay + cpn) * r)) AS BIGINT) AS 매출액,
                       CAST(SUM(CASE WHEN pay + cpn < 0 THEN -1 ELSE 1 END) AS BIGINT) AS 건수,
                       COUNT(DISTINCT "국가") AS 국가수
                FROM t
                WHERE pay + cpn <> 0          -- 음수(취소)를 살려 차감되게 한다
                GROUP BY 1 HAVING 매출액 > 0 ORDER BY 매출액 DESC
            """).df()
    finally:
        con.close()
    return df


# ── 지라 티켓 색인 ─────────────────────────────────────────────────────────
def _jira_map(brand: str) -> dict:
    """로컬 캐시만 읽는다(네트워크 호출 없음). 브랜드별 키가 없으면 all 로 폴백."""
    try:
        cache = json.loads(JIRA_CACHE.read_text(encoding="utf-8"))
    except Exception:
        return {}
    for key in (f"ip_dates_v3_{brand}", "ip_dates_v3_all"):
        blk = cache.get(key)
        if blk:
            return blk.get("data", blk)
    return {}


def _sched(brand: str) -> list:
    """티켓 1건당 1개인 일정 목록(로컬 캐시만). 없으면 빈 목록.

    ★`_jira_map` 만으로는 부족하다 — 저건 **타이틀이 키**라 같은 WBS 를 쓰는 티켓이
      서로를 덮는다. 실제로 티켓 2,226장이 조회에서 통째로 빠져 있었다.
      이쪽(`fetch_ip_schedule`)은 티켓별 리스트라 덮일 일이 없다.
    """
    try:
        cache = json.loads(JIRA_CACHE.read_text(encoding="utf-8"))
    except Exception:
        return []
    for key in (f"sched_v2_{brand}", "sched_v2_all"):
        blk = cache.get(key)
        if not blk:
            continue
        rows = blk.get("data", blk) or []
        if key.endswith(f"_{brand}"):
            return rows
        # all 캐시를 쓸 땐 브랜드로 걸러낸다(브랜드 필드는 다중값 문자열).
        want = ("photoism",) if brand == "photoism" else ("snapism", "sticker")
        return [r for r in rows
                if any(w in str(r.get("brand", "")).lower() for w in want)]
    return []


def _jira_pairs(brand: str) -> list:
    """색인의 원본 — `(타이틀, 엔트리)` 쌍. 타이틀 중복을 허용한다.

    타이틀 키 맵(`_jira_map`)에 일정 목록(`_sched`)을 덧댄다. 덮이지 않게 쌍으로 쌓고,
    (타이틀, 티켓) 조합이 같을 때만 건너뛴다.
    """
    pairs = [(t, e) for t, e in _jira_map(brand).items()]
    seen = {(t, str(e.get("ticket_key") or "").strip().upper()) for t, e in pairs}
    for s in _sched(brand):
        tk = str(s.get("ticket_key") or "").strip().upper()
        if not tk:
            continue
        titles = [t for t in (s.get("wbs_titles") or []) if t]
        if not titles and s.get("parent"):      # WBS 가 비면 부모 제목으로 (수집기와 같은 규칙)
            titles = [s["parent"]]
        for t in titles:
            if (t, tk) in seen:
                continue
            seen.add((t, tk))
            pairs.append((t, {
                "startdate": s.get("startdate"), "duedate": s.get("duedate"),
                "ticket_key": tk, "parent": s.get("parent", ""),
                "brand": s.get("brand", ""), "status": s.get("status", ""),
                "title": t, "wbs_raw": " ".join(titles),
            }))
    return pairs


# 티켓 색인 캐시 — 아래 `_indexes` 와 같은 방식(캐시 파일 mtime 키).
# ★★없으면 안 된다 (2026-08-19 추가). `ticket_index` 는 12.5MB 지라 캐시를
#   매번 다시 파싱해 **호출당 0.24초**가 든다(실측). 그런데 IP정산서 화면의
#   `st.selectbox(format_func=_lab)` 가 **옵션 하나하나마다** _lab → lookup_ticket
#   → ticket_index 를 부른다. 타이틀 10개면 그것만 2.4초다.
#   바로 아래 `_indexes` 에는 같은 캐시가 이미 있었는데 여기만 빠져 있었다.
# ★반환 dict 를 **고치면 안 된다** — 호출부 전수 확인 결과 지금은 읽기만 한다
#   (settlement_calc:97·948, views/8:202·253 · `{**e}` 로 복사해 쓴다).
_TIDX: dict[tuple, dict] = {}


def ticket_index(brand: str) -> dict:
    """{티켓번호: 대표 엔트리}. 캐시는 **타이틀이 키**라 티켓으로 찾으려면 뒤집어야 한다.

    한 티켓이 WBS 분리 때문에 여러 타이틀 키에 중복 등장한다 →
    타이틀을 모아 `titles` 로 넘기고, 날짜는 있는 쪽을 남긴다.
    """
    try:
        mt = JIRA_CACHE.stat().st_mtime
    except OSError:
        mt = 0.0
    key = (brand, mt)
    hit = _TIDX.get(key)
    if hit is not None:
        return hit

    out: dict = {}
    for title, e in _jira_pairs(brand):
        tk = str(e.get("ticket_key") or "").strip().upper()
        if not tk:
            continue
        cur = out.get(tk)
        if cur is None:
            cur = {**e, "ticket_key": tk, "titles": []}
            out[tk] = cur
        if title and title not in cur["titles"]:
            cur["titles"].append(title)
        # 날짜가 비어 있던 레코드는 값이 있는 쪽으로 채운다
        for f in ("startdate", "duedate"):
            if not cur.get(f) and e.get(f):
                cur[f] = e[f]
    # 캐시가 갱신되면 옛 mtime 키는 쓸모없다. 브랜드별 최신 것만 남긴다.
    for k in [k for k in _TIDX if k[0] == brand]:
        del _TIDX[k]
    _TIDX[key] = out
    return out


def lookup_ticket(brand: str, ticket: str) -> dict | None:
    """티켓번호 → 엔트리. **이게 화면의 주 입력 경로**다(타이틀이 아니라 번호가 정)."""
    tk = str(ticket or "").strip().upper()
    if not tk:
        return None
    idx = ticket_index(brand)
    if tk in idx:
        return idx[tk]
    # 브랜드 캐시에 없으면 전체에서 한 번 더 (브랜드 필드가 다중값이라 누락될 수 있다)
    return ticket_index("all").get(tk)


# 색인 캐시 — 타이틀마다 다시 만들면 548개 × 4천 엔트리라 1분이 넘게 걸린다.
# 캐시 파일 mtime 이 바뀌면 자동으로 다시 만든다.
_IDX: dict[tuple, tuple] = {}


def _indexes(brand: str) -> tuple[dict, dict]:
    try:
        mt = JIRA_CACHE.stat().st_mtime
    except OSError:
        mt = 0.0
    key = (brand, mt)
    hit = _IDX.get(key)
    if hit is None:
        jm = _jira_pairs(brand)     # dict 가 아니라 쌍 목록 — 타이틀이 겹쳐도 안 덮인다
        hit = (title_runs._jira_by_key(jm), title_runs._token_prefix_index(jm))
        # 캐시가 갱신되면 옛 mtime 키는 쓸모없다. 브랜드별 최신 것만 남긴다.
        for k in [k for k in _IDX if k[0] == brand]:
            del _IDX[k]
        _IDX[key] = hit
    return hit


def candidates(brand: str, title: str) -> list[dict]:
    """그 타이틀의 후보 티켓들. **제안일 뿐 확정이 아니다.**

    ★빈 타이틀 가드 — `normalize_title("")` 은 `""` 라서 빈 키에 등록된 티켓
      12장이 통째로 붙는다(CANDIP-26514 등). 후보 탐색 자체를 건너뛴다.
    """
    t = str(title or "").strip()
    if not t:
        return []
    keyed, tokidx = _indexes(brand)
    seen, out = set(), []
    # ★접두어(PW/L/SP/렌탈…)를 뗀 이름으로도 찾는다 (2026-08-07).
    #   집계에서 접두어를 타이틀에 살리자 'PW 260710 TWICE' 가 정규화되면서
    #   'pw260710twice' 가 돼 지라 제목('260710 TWICE')과 안 맞았다. 그 결과
    #   같은 IP 를 아티스트 구좌만 후보로 띄우고 **PICK 구좌는 통째로 안 보였다**
    #   — 한꺼번에 정산해야 하는데 반쪽만 잡힌다.
    #   접두어는 '어느 제품 라인인가'를 가르는 표시이지 IP 이름이 아니므로,
    #   티켓을 찾을 때는 떼고 본다. 확정은 여전히 사람이 체크한 것만 인정한다.
    _bare = re.sub(r"^(렌탈|PW|L7|L|P|B|SP)\s+", "", t)
    for probe in ([t] if _bare == t else [t, _bare]):
        for e in title_runs._entries_for(keyed, probe, tokidx):
            tk = str(e.get("ticket_key") or "").strip().upper()
            if not tk or tk in seen:
                continue
            seen.add(tk)
            out.append({**e, "ticket_key": tk})
    return out


def overlap_days(entry: dict, start: str, end: str) -> int:
    """정산 기간과 티켓 기간이 겹치는 일수. 정렬·표시용."""
    s = pd.to_datetime(entry.get("startdate"), errors="coerce")
    d = pd.to_datetime(entry.get("duedate"), errors="coerce")
    if pd.isna(s) and pd.isna(d):
        return 0
    s = s.date() if not pd.isna(s) else d.date()
    d = d.date() if not pd.isna(d) else s
    if d < s:
        s, d = d, s
    p0, p1 = date.fromisoformat(start), date.fromisoformat(end)
    return max(0, (min(d, p1) - max(s, p0)).days + 1)


# ── 확정 매핑 저장 ─────────────────────────────────────────────────────────
def load_mapping() -> dict:
    m = _store.load()
    m.setdefault("mappings", {})
    for b in BRANDS:
        m["mappings"].setdefault(b, {})
    return m


def mapping_version() -> float:
    """st.cache_data 키용 mtime. ★인자명에 밑줄을 붙이면 해시에서 빠지니 주의."""
    return _store.version()


def approve(brand: str, title: str, ticket: str, by: str,
            jira_title: str = "", note: str = "") -> None:
    """타이틀 → 티켓 확정. 같은 타이틀을 다시 승인하면 덮어쓴다(이력은 log 로)."""
    tk = str(ticket or "").strip().upper()
    rec = {"ticket": tk, "jira_title": jira_title, "by": by,
           "at": datetime.now().isoformat(timespec="seconds"), "note": note}

    def _fn(d):
        d.setdefault("mappings", {}).setdefault(brand, {})[title] = rec

    _store.mutate(_fn)


def exclude(brand: str, title: str, by: str, reason: str) -> None:
    """정산 대상이 아니라고 확정. 사유가 없으면 나중에 왜 뺐는지 알 수 없으니 필수."""
    rec = {"ticket": None, "excluded": True, "by": by,
           "at": datetime.now().isoformat(timespec="seconds"), "note": reason}

    def _fn(d):
        d.setdefault("mappings", {}).setdefault(brand, {})[title] = rec

    _store.mutate(_fn)


def unapprove(brand: str, title: str) -> None:
    def _fn(d):
        d.get("mappings", {}).get(brand, {}).pop(title, None)

    _store.mutate(_fn)


# ── 잔여 검증 ─────────────────────────────────────────────────────────────
def annotate(df: pd.DataFrame, brand: str, start: str, end: str) -> pd.DataFrame:
    """타이틀 매출표에 상태·후보·확정티켓을 붙인다.

    상태: 확정 / 제외 / 선택필요(후보 2개+) / 확인필요(후보 1개) / 미연결(0개)
    ★후보가 1개여도 자동 확정하지 않는다. 사람이 눌러야 확정이다.
    """
    mp = load_mapping()["mappings"].get(brand, {})
    rows = []
    for t in df["타이틀"]:
        fixed = mp.get(t)
        if fixed and fixed.get("excluded"):
            rows.append(("제외", 0, "", fixed.get("note", "")))
            continue
        if fixed and fixed.get("ticket"):
            rows.append(("확정", 0, fixed["ticket"], fixed.get("jira_title", "")))
            continue
        cands = candidates(brand, t)
        n = len(cands)
        state = "미연결" if n == 0 else ("확인필요" if n == 1 else "선택필요")
        best = ""
        if n:
            top = max(cands, key=lambda e: overlap_days(e, start, end))
            best = top["ticket_key"]
        rows.append((state, n, best, ""))
    out = df.copy()
    out[["상태", "후보수", "티켓", "비고"]] = pd.DataFrame(rows, index=out.index)
    return out


def residual(annotated: pd.DataFrame) -> dict:
    """미확정 매출 요약. **0원이 되기 전에는 발행을 막는다.**"""
    done = annotated["상태"].isin(["확정", "제외"])
    left = annotated[~done]
    total = int(annotated["매출액"].sum())
    rest = int(left["매출액"].sum())
    return {"총매출": total, "잔여매출": rest, "잔여건": int(len(left)),
            "확정건": int(done.sum()),
            "잔여비중": (rest / total * 100) if total else 0.0}
