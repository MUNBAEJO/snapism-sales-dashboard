"""IP 정산서 — 2단계: 계산 엔진.

확정 매핑(`settlement_map`)으로 고른 타이틀만 가지고
국가별 매출·수량·단가·멤버별 상세와 수취처별 정산액을 낸다.

★수량은 **단가별로 나눠** `ROUNDDOWN(현지 분자 ÷ 단가)` 하고 합친다(2026-08-06).
  문서 표기는 **'건수'로 통일**한다(2026-07-31). 포토이즘은 프레임, 스내피즘은
  스티커·포토카드로 물건만 다를 뿐 둘 다 '팔린 개수'라 같은 지표다.
★'현지 매출' 은 그 건수에 단가를 곱한 **정산 기준액**이다. 나누어떨어지지 않는
  자투리는 버린다 — 담당자가 손으로 해 오던 방식(`ROUNDDOWN(금액/단가)`)이고,
  1,600엔짜리 1,000엔 프레임은 1건 1,000엔으로 친다.
  ★단가를 하나로 뭉치면 안 된다. 한 나라에 단가가 섞인 타이틀(KBO 5,000·7,000)에서
    평균단가로 한 번에 나누면 KBO 한국이 18,103 → 18,056건으로 47건 사라진다.
  검증식: 현지 × 환율 == 매출(KRW).

관련: CURRENT-PROJECTS/IP-정산서-생성.md · 지라 CO-288
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pandas as pd

import ip_classify
import store_rules
import settlement_map as smap
from json_store import JsonStore

BASE_DIR = Path(__file__).parent
PH_AGG = BASE_DIR / "data" / "master_photoism_agg.parquet"
PH_RAW = BASE_DIR / "data" / "master_photoism.parquet"
SN_MASTER = BASE_DIR / "data" / "master.parquet"


def data_version() -> float:
    """계산이 읽는 parquet 3종 중 **가장 최근 변경시각**.

    ★@st.cache_data 키에 넣으라고 만든 값이다. 정산서 화면의 캐시는 ttl 만 있고
      데이터 버전이 없어서, 수집이 막 끝난 직후에 뽑으면 **최대 15분 전 매출로
      대외 문서가 나갈 수 있었다**(2026-08-19). 다른 페이지는 전부
      `data_io.file_version(...)` 을 키로 넘기는데 이 페이지만 빠져 있었다.
    """
    v = 0.0
    for p in (PH_AGG, PH_RAW, SN_MASTER):
        try:
            v = max(v, p.stat().st_mtime)
        except OSError:
            pass
    return v

# 국가명이 브랜드마다 다르다 — 한 문서에 같이 실리므로 통일한다.
NAT_KO = {"대한민국": "한국", "KOREA": "한국", "Korea": "한국"}

_rates_store = JsonStore("settlement_rates.json", default={"rates": {}})
_mg_store = JsonStore("settlement_mg.json", default={"mg": {}})
_alias_store = JsonStore("member_aliases.json", default={"aliases": {}})


def _con():
    # 메모리 한도·스풀 경로는 settlement_map.duck() 한 곳에서 관리한다.
    return smap.duck()


def _gubun_filter() -> str:
    """원거래에도 IP구분 필터를 건다.

    ★타이틀만으로 좁히면 같은 타이틀 안에 렌탈 행이 섞여 있다
      (예: 260628 추영우 → 아티스트 8,610,000 + 렌탈 98,000). 집계본은 IP구분으로
      걸러지지만 원거래는 안 걸러져서 렌탈이 정산에 다시 들어왔다.
      집계본과 같은 분류식을 원거래에도 그대로 적용한다.
    """
    g = ",".join(f"'{x}'" for x in smap.SETTLE_GUBUN)
    return f"AND ({ip_classify.IP_GUBUN_SQL}) IN ({g})"


def _sn_gubun() -> str:
    """스내피즘 원거래에도 **카테고리 필터**를 건다.

    ★2026-08-04 추가. 같은 IP(=프레임 이름)라도 **카테고리가 다르면 정산 조건이 다르다**
      (사용자 확정). 그런데 후보 목록(settlement_map.title_revenue)은
      `카테고리 IN SETTLE_GUBUN` 으로 거르는데 **정산 집계만 안 걸러서**,
      화면에서 본 적 없는 매출이 정산액에 조용히 더해지고 있었다.
      예: 이민혁(HUTA)·로이킴 의 'DIVE IN PHOTOISM', 루네이트의 '폴라릿'.

    포토이즘은 _gubun_filter() 로 이미 같은 취지의 필터를 걸고 있었다(IP구분 기준).
    스내피즘은 원본에 '카테고리' 열이 그대로 있어 그걸 쓴다.
    """
    g = ",".join(f"'{x}'" for x in smap.SETTLE_GUBUN)
    return f'AND "카테고리" IN ({g})'


def _sqlist(vals) -> str:
    """문자열 리스트 → SQL IN 절. 작은따옴표는 두 번 써서 이스케이프."""
    esc = [str(v).replace("'", "''") for v in vals]
    return ",".join(f"'{v}'" for v in esc) or "''"


# ── 확정 매핑 → 타이틀 ─────────────────────────────────────────────────────
def titles_for_ticket(brand: str, ticket: str) -> list[str]:
    """그 티켓에 **확정된** 타이틀들. 후보가 아니라 사람이 승인한 것만."""
    tk = str(ticket or "").strip().upper()
    mp = smap.load_mapping()["mappings"].get(brand, {})
    return [t for t, v in mp.items()
            if not v.get("excluded") and str(v.get("ticket") or "").upper() == tk]


def find_tickets(brand: str, query: str, limit: int = 20) -> list[dict]:
    """IP명 일부로 티켓 찾기. 티켓번호를 모를 때 쓰는 보조 경로."""
    q = str(query or "").strip().lower()
    if len(q) < 2:
        return []
    out = []
    for tk, e in smap.ticket_index(brand).items():
        hay = " ".join(e.get("titles") or []) + " " + str(e.get("parent") or "")
        if q in hay.lower():
            out.append({"ticket": tk, "titles": e.get("titles") or [],
                        "start": e.get("startdate"), "due": e.get("duedate")})
    out.sort(key=lambda x: (x["start"] or ""), reverse=True)
    return out[:limit]


def suggest_titles(brand: str, ticket: str, start: str, end: str,
                   rates: dict) -> pd.DataFrame:
    """티켓번호 → 그 티켓에 붙을 매출 타이틀 후보.

    ★이게 화면의 주 동선이다 — 464개 대기열을 먼저 다 처리해야 정산서를 만들 수 있는
      구조는 실무자가 납득하기 어렵다. 티켓 하나만 넣으면 관련 타이틀을 알아서 모아
      보여주고, 체크한 것만 확정 저장한다.

    반환 컬럼: 타이틀 · 매출액 · 건수 · 국가수 · 상태(확정/후보) · 충돌티켓
    """
    tk = str(ticket or "").strip().upper()
    if not tk:
        return pd.DataFrame()
    df = smap.title_revenue(brand, start, end, rates)
    mp = smap.load_mapping()["mappings"].get(brand, {})
    rows = []
    for _, r in df.iterrows():
        t = r["타이틀"]
        fixed = mp.get(t)
        if fixed:
            if fixed.get("excluded"):
                continue
            cur = str(fixed.get("ticket") or "").upper()
            if cur == tk:
                rows.append({**r.to_dict(), "상태": "확정", "충돌티켓": ""})
            # 다른 티켓에 이미 확정된 타이틀은 조용히 뺀다 — 뺏어오면 사고다
            continue
        if any(c["ticket_key"] == tk for c in smap.candidates(brand, t)):
            rows.append({**r.to_dict(), "상태": "후보", "충돌티켓": ""})
    out = pd.DataFrame(rows)
    return out.sort_values("매출액", ascending=False) if len(out) else out


def confirmed_tickets(brand: str) -> dict[str, list[str]]:
    """{티켓: [타이틀…]} — 화면에서 정산 대상을 고르게 할 때 쓴다."""
    out: dict[str, list[str]] = {}
    for t, v in smap.load_mapping()["mappings"].get(brand, {}).items():
        if v.get("excluded"):
            continue
        tk = str(v.get("ticket") or "").upper()
        if tk:
            out.setdefault(tk, []).append(t)
    return out


_KEYCACHE: dict = {}


def _mem_sql(col: str) -> str:
    """멤버 이름을 꺼내는 식. **타이틀 모양이면 IP명만 남긴다.**

    ★BASIC 구좌는 `프레임 이름` 이 멤버가 아니라 **타이틀 문자열**이다
      (`L 260701 왈맹이`). 아티스트 IP 는 BASIC 이라도 멤버명이 들어가지만
      (코르티스 → JAMES·JUHOON…), 캐릭터·렌탈은 타이틀이 그대로 들어온다.
      그대로 두면 같은 캐릭터가 멤버 두 명으로 갈린다 —
      `빤쮸토끼` 가 `L 260601 빤쮸토끼`(BASIC 6,515,000) + `빤쮸토끼`(WITH 1,340,000)
      두 열로 발행된다(2026-08-21). **절사 단위가 국가 × 멤버**라 금액도 어긋난다.

    ★날짜코드가 앞에 붙어 있을 때만 벗긴다. 멤버 이름에 5~8자리 숫자가 앞에
      오는 경우는 없으므로, 진짜 멤버명(`JAMES`·`빤쮸토끼`)은 건드리지 않는다.
      접두어만 보고 벗기면 `L`·`P` 로 시작하는 멤버명을 깎을 위험이 있어서다.
    """
    pfx = r"'^(렌탈|PW|L7|L|P|B|SP)\s+'"
    return (f"CASE WHEN regexp_matches({col}, "
            f"'^((렌탈|PW|L7|L|P|B|SP)\\s+)?[0-9]{{5,8}}\\s') "
            f"THEN trim(regexp_replace(regexp_replace({col}, {pfx}, ''), "
            f"'^[0-9]{{5,8}}\\s*', '')) ELSE {col} END")


def _title_map(start: str, end: str) -> dict:
    """그 기간의 **타이틀 → (타이틀명 집합, 프레임이름 집합)** 표. 기간 단위로 한 번만 만든다.

    ★★왜 이 다리가 필요한가 (2026-08-21)
      예전엔 집계의 `타이틀` → 원거래의 `타이틀명` 하나로만 갈아탔다. 그런데
      **BASIC 구좌는 타이틀명이 비어 있다** — IP 이름이 `프레임 이름` 에 들어 있다
      (ip_classify: WITH/EVENT 는 타이틀명, BASIC 은 프레임명 기준). 빈 값은
      `COALESCE("타이틀명",'') <> ''` 로 걸러 냈으니 다리가 통째로 끊겼다.
      실측 — `L 260701 왈맹이` 화면 29,068,000 → 정산 **0**,
             `L 260601 빤쮸토끼` 7,855,000 → 1,340,000(83% 누락).
      아티스트 IP 도 BASIC 몫이 빠져 있었다(`260505 코르티스` +6,717,711).
      2026-07 한 달 158개 타이틀 1억 9,122만원 · 2026년 424개 18억 594만원.

    ★어떻게 만드나: `build_photoism_agg.build_agg` 가 타이틀을 만드는 **바로 그 식**을
      원거래에 적용해 (타이틀 → 타이틀명·프레임이름) 짝을 뽑는다. 2026-07 전량
      대조에서 타이틀 776개 · 23,164,238,813원이 집계본과 한 푼도 다르지 않았다.

    ★왜 이 식을 하류 쿼리에 **직접** 안 쓰나 — 써 봤는데 한 번에 94초가 걸렸다.
      정규식이라 parquet 필터로 밀려나지 않아 3,860만 행을 다 훑는다. 값 목록으로
      바꾸면 DuckDB 가 로우그룹을 건너뛰어 0.2초로 돌아온다.
    ★왜 **타이틀로 안 좁히고** 기간 전체를 만드나 — `WHERE (식) IN (타이틀)` 로 좁히면
      어차피 전 행에 식을 돌려야 해서 89초가 걸렸다. 좁히지 않고 GROUP BY 로 한 번에
      뽑으면 4초다(열 4개만 읽는다). 게다가 기간이 같으면 **문서 여러 장이 나눠 쓴다.**

    ★이 갈아타기가 정확한 근거: 두 방향 다 충돌이 없다(2026년 전량 확인).
      타이틀명 2,337개 → 각각 타이틀 1개 · 빈 타이틀명 행의 프레임이름 360개 →
      각각 타이틀 1개. 날짜코드가 이름 안에 들어 있어서 회차가 갈린다.
    """
    key = (start, end, data_version())
    if key in _KEYCACHE:
        return _KEYCACHE[key]

    nm = f"TRIM({ip_classify.IP_NAMECORE_SQL})"
    amap = ip_classify.load_alias_map()
    pairs = ", ".join(
        "'{}': '{}'".format(str(k).strip().replace("'", "''"),
                            str(v).strip().replace("'", "''"))
        for k, v in amap.items() if str(k).strip() and str(v).strip()
    )
    ip = (f"COALESCE(map_extract(MAP {{{pairs}}}, {nm})[1], NULLIF({nm}, ''), '')"
          if pairs else f"COALESCE(NULLIF({nm}, ''), '')")
    dt, pfx = ip_classify.IP_DATE_SQL, ip_classify.IP_PREFIX_SQL
    expr = (f"CASE WHEN ({ip}) = '' THEN ''"
            f" WHEN ({dt}) = '' THEN NULLIF(TRIM({pfx} || ' ' || ({ip})), '')"
            f" ELSE TRIM({pfx} || ' ' || {dt} || ' ' || ({ip})) END")

    con = _con()
    try:
        df = con.execute(f"""
            SELECT {expr}                                    AS t,
                   COALESCE("타이틀명", '')                    AS tn,
                   COALESCE(CAST("프레임 이름" AS VARCHAR), '') AS fr
            FROM read_parquet('{PH_RAW.as_posix()}')
            WHERE TRY_CAST("날짜" AS DATE) BETWEEN DATE '{start}' AND DATE '{end}'
              -- ★테스트 매장은 매출에서도 뺀다 (store_rules 주석 참고).
              --   장비 목록은 진작 빼고 있었는데 매출만 남아
              --   '1대당 매출' 의 분자·분모가 어긋나 있었다.
              {store_rules.not_test_sql()}
            GROUP BY 1, 2, 3
        """).df()
    finally:
        con.close()

    out: dict = {}
    for t, tn, fr in zip(df["t"], df["tn"], df["fr"]):
        if not t:
            continue
        cur = out.setdefault(t, (set(), set()))
        if tn:
            cur[0].add(tn)
        elif fr:
            cur[1].add(fr)          # 타이틀명이 빈 행(BASIC)만 프레임으로 잡는다
    _KEYCACHE[key] = out
    return out


def _title_pred(titles: list[str], start: str, end: str) -> str:
    """원거래를 그 타이틀의 행으로 좁히는 WHERE 조각. 근거는 `_title_map` 주석 참고.

    타이틀명으로 잡고, **타이틀명이 빈 행은 프레임 이름으로** 잡는다.
    둘 다 값 목록이라 parquet 단계에서 걸러진다.
    """
    if not titles:
        return "AND FALSE"
    m = _title_map(start, end)
    tn: set = set()
    fr: set = set()
    for t in titles:
        a, b = m.get(t, (set(), set()))
        tn |= a
        fr |= b
    parts = []
    if tn:
        parts.append(f'"타이틀명" IN ({_sqlist(sorted(tn))})')
    if fr:
        parts.append('(COALESCE("타이틀명", \'\') = \'\''
                     f' AND CAST("프레임 이름" AS VARCHAR) IN ({_sqlist(sorted(fr))}))')
    if not parts:
        return "AND FALSE"
    return "AND (" + " OR ".join(parts) + ")"


# ★★쿠폰·코인 결제분은 '상품총액'으로 센다 (2026-08-05, 사용자 확정)
#
# 무엇이 문제였나: 쿠폰·코인 정산국에서 **쿠폰/코인 액면가**를 매출로 잡고 있었다.
# 그런데 CMS 가 적어 주는 액면가는 상품값과 안 맞을 때가 있다.
#   · 클라씨 영국 2건: 단가 16 · 상품총액 16 인데 **쿠폰 18** → 정산이 36 으로 잡혔다.
#     (사용자 시트는 32. 환율은 양쪽 다 1,940.15 로 같았고 차이는 여기서 났다.)
#   · 페루: 단가 24 · 총액 24 인데 **코인 224224** 같은 입력 오류가 섞여 있다.
#     이런 몇 건이 페루 정산액을 3배로 부풀리고 있었다.
#
# 왜 '상품 단가'가 아니라 '상품총액'인가: 단가는 **1개분**이라 여러 장 산 거래에서
# 수량이 통째로 날아간다. 라오스는 단가 70,000 에 코인 140,000/210,000/280,000 …
# 이 흔한데, 이건 전부 상품총액과 정확히 일치한다(= 2장·3장·4장). 단가로 바꾸면
# 라오스만 -1.4억이 깎인다. 상품총액은 수량도 살리고 액면가 오류도 걸러낸다.
#
# 적용 범위는 **쿠폰·코인이 실제로 붙은 행만**이다. 카드로 낸 행은 손대지 않는다
# (전 국가 3,651만 행 중 80만 행은 상품총액 ≠ 결제금액이라, 전부 바꾸면 카드 매출까지 흔들린다).
# 상품총액이 0/누락이면 옛 식으로 되돌린다 — 설명 못 하는 값을 임의로 0 처리하지 않는다.
#
# 전 기간 영향(현지통화): 페루 -64%(오류 교정) · 멕시코 -6.9% · 영국 -1.6% ·
#   독일 -0.8% · 칠레 -0.5% · 라오스 -0.08% · 태국·라트비아 거의 0.
def _ph_num(cpn: str, coin: str, pay="pay", c1="cpn", c2="coin", tot="tot") -> str:
    """포토이즘 매출 인식액 SQL. cpn/coin 은 국가코드 목록 문자열."""
    add_c = f"CASE WHEN cc IN ({cpn})  THEN {c1} ELSE 0 END"
    add_i = f"CASE WHEN cc IN ({coin}) THEN {c2} ELSE 0 END"
    return (f"CASE WHEN ({add_c}) + ({add_i}) > 0 AND {tot} > 0 THEN {tot}"
            f" ELSE {pay} + ({add_c}) + ({add_i}) END")


# ── 절사·반올림 (엑셀과 같은 규칙) ─────────────────────────────────────────
def _trunc(x) -> int:
    """엑셀 ROUNDDOWN(x, 0). **0 쪽으로** 버린다.

    ★floor 를 쓰면 안 된다. 취소는 음수로 들어오는데 floor(-1.5) = -2 라
      실제보다 한 건 더 깎인다. int() 는 0 쪽 절사라 엑셀과 같다.
    """
    try:
        return int(float(x))
    except (TypeError, ValueError):
        return 0


def _round_half_up(x) -> int:
    """엑셀 ROUND(x, 0). 0.5 는 **올린다**.

    ★파이썬 내장 round() 는 0.5 를 짝수로 보낸다(은행식). 브루나이
      10 BND × 1,124.45 = 11,244.5 가 11,244 로 내려가 시트(11,245)와
      1원이 어긋났다. 같은 문서에서 1원이 안 맞으면 반드시 질문이 들어온다.
    """
    from decimal import Decimal, ROUND_HALF_UP
    try:
        return int(Decimal(str(float(x))).quantize(Decimal("1"),
                                                   rounding=ROUND_HALF_UP))
    except (TypeError, ValueError):
        return 0


# [삭제 2026-08-19] `_settle_by_price` — **국가 × 단가** 로 절사하던 옛 함수.
#   절사 단위가 **국가 × 멤버**로 확정되면서(2026-08-07) `_fold_prices` 로 대체됐고
#   그 뒤로 아무 데서도 안 불렸다. 남겨 두면 나중에 "이미 있네" 하고 잘못 쓸 수 있어
#   지운다. 되살릴 일이 있으면 `git show 7604496`.


def _fold_prices(df: pd.DataFrame, rates: dict) -> pd.DataFrame:
    """(국가 × 멤버) 행을 국가 단위로 접는다. 절사는 여기서 한 번만 일어난다.

    ★★절사 단위는 **국가 × 멤버**다 (2026-08-07, 담당자 피벗으로 확정).
      담당자는 CSV 를 `행=국가 · 열=멤버` 로 피벗해 칸마다 `ROUNDDOWN(금액/평균단가)`
      를 내고 그걸 더한다. 국가 단위로 한 번만 나누면 자투리가 덜 버려져 값이 커진다.
        트와이스(멤버 9명) 일본: 국가단위 3,040 vs 멤버별 3,038 — 시트는 3,038.
        243+171+213+180+454+592+336+541+308 = 3,038 (멤버별 몫의 합)
      멤버가 한 명인 솔로 IP 는 두 방식이 같아서 여태 안 드러났다. 허남준·김준수·
      정대현이 국가 단위로도 맞았던 이유다(그래서 이 규칙을 늦게 찾았다).
      네 건 전부 이 방식으로 시트와 일치한다.

    ★단가는 멤버별 **평균**을 쓴다(피벗 머리에 '평균 : 프레임단가' 라고 쓰여 있다).
      한 멤버가 여러 단가로 팔렸어도 평균으로 한 번 나눈다 — 단가별로 또 쪼개면
      담당자 값과 어긋난다.
    """
    # ★스내피즘은 한 IP 안에 판매 항목이 여러 가지다(와이드 스티커·포토카드·폴라릿).
    #   단가가 서로 달라 문서에 따로 적어야 한다 → '구분' 열이 있으면 절사도
    #   (구분 × 국가 × 멤버) 로 내려간다. 합계 차이는 반올림 수준이다(베이온 +1원).
    by_cat = "구분" in df.columns
    keys = (["구분", "국가"] if by_cat else ["국가"])
    out = []
    for key, g in df.groupby(keys, sort=False):
        # ★키가 하나여도 pandas 는 튜플로 준다(2.x). isinstance 로 갈라 보면
        #   국가명이 '구분' 으로 둔갑한다 — 실제로 포토이즘에 구분 열이 생겼었다.
        key = key if isinstance(key, tuple) else (key,)
        cat, nat = (key[0], key[1]) if by_cat else (None, key[0])
        unit = g["unit"].iloc[0]
        up = pd.to_numeric(g["up"], errors="coerce")
        q = (g["현지"] / up.where(up > 0)).map(_trunc)     # 멤버별 몫
        loc = (q * up.where(up > 0)).fillna(0)             # 그 몫에 다시 단가를 곱한 정산 기준액
        # 단가를 못 구한 멤버(전부 0/누락)는 금액만 살린다 — 버리면 돈이 사라진다.
        loc = loc.where(up > 0, g["현지"])
        # ★내장 round 금지(위 _round_half_up 주석). 현지통화 소계도 문서와
        #   같은 규칙으로 맞춘다(2026-08-19).
        _tot = _round_half_up(loc.sum())
        _big = g.loc[g["현지"].abs().idxmax(), "up"] if len(g) else None
        rec = {"국가": nat, "unit": unit, "수량": int(q.fillna(0).sum()),
               "현지": _tot,
               "매출액": _round_half_up(_tot * float(rates.get(unit, 1))),
               "건수": int(g["건수"].sum()),
               "단가": (float(_big) if _big and _big == _big else None)}
        if cat is not None:
            rec["구분"] = cat
        out.append(rec)
    return pd.DataFrame(out)


# ── 국가별 상세 ────────────────────────────────────────────────────────────
def country_detail(brand: str, titles: list[str], start: str, end: str,
                   rates: dict) -> pd.DataFrame:
    """국가 · 통화 · 수량 · 현지매출 · 매출(KRW). 수량은 현지통화끼리 나눈다."""
    if not titles:
        return pd.DataFrame(columns=["국가", "unit", "수량", "현지", "매출액", "건수"])
    from photoism_rules import COIN_CC, COUPON_CC

    rate = smap._rate_case(rates)
    con = _con()
    try:
        if brand == "photoism":
            cpn, coin = _sqlist(sorted(COUPON_CC)), _sqlist(sorted(COIN_CC))
            df = con.execute(f"""
                WITH t AS (
                  SELECT "국가", lower(trim("국가코드")) AS cc, "결제 단위" AS unit,
                         {_mem_sql('trim(CAST("프레임 이름" AS VARCHAR))')} AS mem,
                         {rate} AS r,
                         CAST("최종 결제 금액" AS BIGINT) AS pay,
                         CAST("쿠폰 할인 금액"  AS BIGINT) AS cpn,
                         CAST("서비스코인"      AS BIGINT) AS coin,
                         CAST("상품총액"        AS BIGINT) AS tot,
                         TRY_CAST("상품 단가" AS DOUBLE) AS up
                  FROM read_parquet('{PH_RAW.as_posix()}')
                  WHERE TRY_CAST("날짜" AS DATE) BETWEEN DATE '{start}' AND DATE '{end}'
              -- ★테스트 매장은 매출에서도 뺀다 (store_rules 주석 참고).
              --   장비 목록은 진작 빼고 있었는데 매출만 남아
              --   '1대당 매출' 의 분자·분모가 어긋나 있었다.
              {store_rules.not_test_sql()}
                    {_title_pred(titles, start, end)}
                    {_gubun_filter()}
                    AND lower(CAST("취소 여부" AS VARCHAR)) NOT IN ('true','1')
                ), f AS (
                  SELECT *, {_ph_num(cpn, coin)} AS num
                  FROM t
                )
                SELECT "국가", any_value(unit) AS unit, mem,
                       AVG(NULLIF(up, 0)) AS up,
                       CAST(SUM(num) AS BIGINT) AS 현지,
                       CAST(SUM(CASE WHEN num < 0 THEN -1 ELSE 1 END) AS BIGINT) AS 건수
                -- 음수(취소)를 살려야 차감된다. 절사는 파이썬에서 **멤버 단위**로.
                FROM f WHERE num <> 0 GROUP BY 1, 3
            """).df()
        else:
            df = con.execute(f"""
                WITH t AS (
                  SELECT "국가", "결제 단위" AS unit, {rate} AS r,
                         -- ★판매 항목(와이드 스티커·포토카드·폴라릿)을 축으로 남긴다.
                         --   같은 IP 라도 상품마다 단가가 달라 문서에 따로 적어야 한다.
                         NULLIF(trim(CAST("상품 카테고리" AS VARCHAR)), '') AS 구분,
                         trim(CAST("상품 이름" AS VARCHAR)) AS mem,
                         CAST("최종 결제 금액" AS BIGINT)
                         + CAST("쿠폰 할인 금액" AS BIGINT) AS num,
                         TRY_CAST("상품 단가" AS DOUBLE) AS up
                  FROM read_parquet('{SN_MASTER.as_posix()}')
                  WHERE CAST("날짜" AS DATE) BETWEEN DATE '{start}' AND DATE '{end}'
                    AND "프레임 이름" IN ({_sqlist(titles)}) {_sn_gubun()}
                    AND NOT COALESCE("취소 여부", FALSE)
                )
                SELECT COALESCE(구분, '기타') AS 구분, "국가",
                       any_value(unit) AS unit, mem,
                       AVG(NULLIF(up, 0)) AS up,
                       CAST(SUM(num) AS BIGINT) AS 현지,
                       CAST(SUM(CASE WHEN num < 0 THEN -1 ELSE 1 END) AS BIGINT) AS 건수
                FROM t WHERE num <> 0 GROUP BY 1, 2, 4
            """).df()
    finally:
        con.close()

    if df.empty:
        return pd.DataFrame(columns=["국가", "unit", "수량", "현지", "매출액", "건수"])
    df["국가"] = df["국가"].map(lambda x: NAT_KO.get(x, x))
    # ★★멤버 이름을 **별첨과 같은 기준**으로 맞춘다 (2026-08-21).
    #   `_fold_prices` 의 절사 단위가 국가 × 멤버인데 여기서 정규화를 안 하면,
    #   키릴문자가 섞인 이름(HARUTО)이나 한글/영문 표기가 갈려 **절사가 두 번**
    #   일어난다. 별첨(member_pivot)은 `_norm_member` 를 태우므로 본문과 별첨의
    #   멤버 수가 서로 달라진다 — 같은 문서에서 기준이 두 개면 안 된다.
    if "mem" in df.columns:
        df["mem"] = df["mem"].map(_norm_member)
    # ★절사는 현지통화끼리 한다. 환산 후 나누면 환율배수만큼 부푼다.
    df = _fold_prices(df, rates)
    if "구분" in df.columns:
        # 판매 항목 묶음이 흩어지지 않게: 큰 항목부터, 그 안에서 매출 큰 나라부터.
        order = (df.groupby("구분")["매출액"].sum().sort_values(ascending=False)
                 .index.tolist())
        df["_o"] = df["구분"].map({c: i for i, c in enumerate(order)})
        return (df.sort_values(["_o", "매출액"], ascending=[True, False])
                .drop(columns="_o").reset_index(drop=True))
    return df.sort_values("매출액", ascending=False).reset_index(drop=True)


def revenue_countries(brand: str, titles: list[str], start: str, end: str) -> list[str]:
    """그 기간에 **매출이 난 국가** 이름만. 국가별 요율 입력칸을 그릴 때 쓴다.

    ★country_detail 을 부르면 안 된다 — 요율 화면은 금액 미리보기보다 먼저 뜨는데
      거기서 무거운 집계를 또 돌리면 화면이 두 번 느려진다. 국가 이름만 필요하므로
      집계본에서 가볍게 긁는다(포토이즘 기준 0.2초).
    """
    if not titles:
        return []
    con = _con()
    try:
        if brand == "photoism":
            sql = f"""SELECT DISTINCT "국가" FROM read_parquet('{PH_AGG.as_posix()}')
                      WHERE "날짜" BETWEEN DATE '{start}' AND DATE '{end}'
                        AND "타이틀" IN ({_sqlist(titles)})
                        AND COALESCE("최종 결제 금액",0) + COALESCE("쿠폰 할인 금액",0) <> 0"""
        else:
            sql = f"""SELECT DISTINCT "국가" FROM read_parquet('{SN_MASTER.as_posix()}')
                      WHERE CAST("날짜" AS DATE) BETWEEN DATE '{start}' AND DATE '{end}'
                        AND "프레임 이름" IN ({_sqlist(titles)}) {_sn_gubun()}
                        AND NOT COALESCE("취소 여부", FALSE)"""
        names = con.execute(sql).df()["국가"].astype(str).tolist()
    finally:
        con.close()
    return sorted({NAT_KO.get(n, n) for n in names if n and n != "nan"})


def open_countries(brand: str, start: str, end: str) -> pd.DataFrame:
    """그 기간에 브랜드가 실제 영업한 국가 전체.
    매출 0인 국가를 빼면 '거긴 안 열었다'로 오해된다."""
    con = _con()
    try:
        if brand == "photoism":
            q = f"""SELECT "국가", any_value("결제 단위") AS unit
                    FROM read_parquet('{PH_AGG.as_posix()}')
                    WHERE "날짜" BETWEEN DATE '{start}' AND DATE '{end}' GROUP BY 1"""
        else:
            q = f"""SELECT "국가", any_value("결제 단위") AS unit
                    FROM read_parquet('{SN_MASTER.as_posix()}')
                    WHERE CAST("날짜" AS DATE) BETWEEN DATE '{start}' AND DATE '{end}'
                    GROUP BY 1"""
        df = con.execute(q).df()
    finally:
        con.close()
    df["국가"] = df["국가"].map(lambda x: NAT_KO.get(x, x))
    return df.drop_duplicates("국가")


def fill_open(detail: pd.DataFrame, opened: pd.DataFrame) -> pd.DataFrame:
    """매출 0인 오픈 국가를 0행으로 채운다.

    ★판매 항목별로 나눠 적는 문서(스내피즘)에서는 **항목마다 반복하지 않는다.**
      3개 항목 × 안 팔린 20개국 = 60줄이 되면 표가 못 읽게 된다. 0원 국가는
      맨 뒤 '매출 없음' 묶음에 한 번만 적는다.
    """
    NOSALE = "매출 없음"
    have = set(detail["국가"])
    base = {"수량": 0, "현지": 0, "매출액": 0, "건수": 0, "단가": None}
    add = [{"국가": r["국가"], "unit": r["unit"], **base,
            **({"구분": NOSALE} if "구분" in detail.columns else {})}
           for _, r in opened.iterrows() if r["국가"] not in have]
    if not add:
        return detail.reset_index(drop=True)
    # 빈/전부-NA 열이 섞이면 concat 이 dtype 을 흔든다 → 원본 열 구성에 맞춰 붙인다.
    extra = pd.DataFrame(add).reindex(columns=detail.columns)
    out = pd.concat([detail, extra.astype(detail.dtypes.to_dict(), errors="ignore")],
                    ignore_index=True)
    if "구분" in out.columns:
        return out.reset_index(drop=True)      # 항목 순서는 country_detail 이 이미 잡았다
    return out.sort_values("매출액", ascending=False).reset_index(drop=True)


# ── 멤버별 상세 ────────────────────────────────────────────────────────────
def _member_alias() -> dict:
    return _alias_store.load().get("aliases", {})


# ★CMS 데이터에 키릴문자가 섞여 있다. 라틴 알파벳과 글꼴이 같아 눈으로는 못 잡는데
#   코드포인트가 달라 같은 멤버가 두 열로 갈라진다.
#   실측(2026-06): HARUTО(О) · KAZUHА(А) · HONG EUNCHAЕ(Е) · SANGAН(Н) · Friend or Faux А
_CYRILLIC = str.maketrans({
    "А": "A", "В": "B", "Е": "E", "К": "K", "М": "M", "Н": "H", "О": "O",
    "Р": "P", "С": "C", "Т": "T", "Х": "X", "І": "I", "Ѕ": "S", "Ј": "J",
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "х": "x", "у": "y",
    "і": "i", "ѕ": "s", "ј": "j",
})


def _norm_member(s: str) -> str:
    """멤버명 정규화 — 키릴문자 치환 → 별칭(한글→영문) 적용."""
    s = str(s or "").translate(_CYRILLIC).strip()
    return _member_alias().get(s, s)


def unmapped_members(*pivots) -> list[str]:
    """멤버 피벗에 남은 **한글 이름 중 별칭이 없는 것**.

    한 IP가 두 브랜드에서 동시에 팔리면 한쪽은 한글, 한쪽은 영문으로 들어와
    같은 멤버가 두 열로 쪼개진다. 화면에서 이 목록을 띄워 한 번 매핑받는다.
    """
    import re
    alias = _member_alias()
    out = set()
    for p in pivots:
        if p is None or getattr(p, "empty", True):
            continue
        for m in p.columns:
            if re.search(r"[가-힣]", str(m)) and str(m) not in alias:
                out.add(str(m))
    return sorted(out)


def set_member_alias(ko: str, en: str) -> None:
    """한글 멤버명 → 영문 표기 저장. 저장하면 다음 집계부터 열이 합쳐진다."""
    ko, en = str(ko).strip(), str(en).strip()
    if not ko or not en:
        return
    _alias_store.mutate(lambda d: d.setdefault("aliases", {}).update({ko: en}))


def _allocate_krw(df: "pd.DataFrame", targets: dict | None = None) -> "pd.Series":
    """멤버별 매출(원)을 **국가 합계에 정확히 맞춰** 정수로 배분한다(최대잔여법).

    ★멤버마다 반올림해 더하면 국가 합계가 국가별 내역과 1원씩 어긋난다(중국
      68,816,170 vs 68,816,169). 같은 문서 안에서 1원이 안 맞으면 반드시 질문이
      들어오므로, 국가 합계를 먼저 정하고 그 안에서 나눈다. 수량과 같은 방식.
    """
    import math
    out = pd.Series(0, index=df.index, dtype="int64")
    for nat, g in df.groupby("국가"):
        # ★국가 소계는 단가별 절사를 거친 값이다(country_detail). 원금액을 그대로
        #   더하면 별첨 합계가 본문보다 커진다(일본 3,541,762 vs 본문 3,536,350).
        #   소계를 목표로 받아 **비례 축소**한 뒤 최대잔여법으로 정수를 맞춘다.
        raw = g["krw_raw"].astype(float)
        tot = float(raw.sum())
        target = (int(targets[nat][1]) if targets and nat in targets
                  else int(round(tot)))
        scaled = raw * (target / tot) if tot else raw * 0.0
        base = scaled.map(math.floor).astype("int64")
        gap = target - int(base.sum())
        frac = scaled - base
        if gap > 0:
            order = sorted(g.index, key=lambda i: (-frac[i], str(g.loc[i, "member"])))
            for i in order[:gap]:
                base[i] += 1
        elif gap < 0:
            order = sorted([i for i in g.index if base[i] > 0],
                           key=lambda i: (frac[i], str(g.loc[i, "member"])))
            for i in order[:-gap]:
                base[i] -= 1
        out.loc[base.index] = base
    return out


def _allocate(df: "pd.DataFrame", targets: dict | None = None) -> "pd.Series":
    """멤버별 수량을 **국가 소계에 정확히 맞춰** 배분한다(최대잔여법).

    ★왜 필요한가 — 국가별 내역은 '국가 전체 분자 ÷ 국가 평균단가'를 한 번 내림하고,
      별첨은 멤버마다 내림해 더한다. 소수가 여러 번 버려져 별첨 합계가 더 작아진다
      (한국: 아사히 0.857 + 윤재혁 0.286 이 버려져 10,708 → 10,707).
      같은 문서에 다른 숫자가 보이면 오해받으므로, 멤버별로 내림한 뒤 남는 몫을
      **소수가 큰 멤버부터** 하나씩 얹어 국가 소계와 일치시킨다.
      (선거 의석 배분에 쓰는 방식과 같다)
    """
    out = pd.Series(0, index=df.index, dtype="int64")
    for nat, g in df.groupby("국가"):
        cnt = g["up_cnt"].sum()
        if not cnt:
            continue
        nat_price = g["up_sum"].sum() / cnt            # 국가 평균단가(행 기준)
        # ★국가 소계는 단가별로 따로 절사한 값이라 평균단가로 다시 내면 어긋난다
        #   (KBO 한국 18,103 vs 평균단가 18,056). 소계를 그대로 목표로 받는다.
        target = (int(targets[nat][0]) if targets and nat in targets
                  else (int(g["num"].sum() / nat_price) if nat_price else 0))
        q = g.apply(lambda r: (r["num"] / (r["up_sum"] / r["up_cnt"]))
                    if r["up_cnt"] else 0.0, axis=1)
        base = q.astype("int64")
        gap = target - int(base.sum())
        if gap > 0:
            # 소수가 큰 순 → 동률이면 분자가 큰 순(재현 가능하게 이름까지 본다)
            order = sorted(g.index, key=lambda i: (-(q[i] - base[i]),
                                                   -g.loc[i, "num"],
                                                   str(g.loc[i, "member"])))
            for i in order[:gap]:
                base[i] += 1
        elif gap < 0:
            order = sorted([i for i in g.index if base[i] > 0],
                           key=lambda i: (q[i] - base[i], g.loc[i, "num"],
                                          str(g.loc[i, "member"])))
            for i in order[:-gap]:
                base[i] -= 1
        out.loc[g.index] = base
    return out


def member_pivot(brand: str, titles: list[str], start: str, end: str,
                 rates: dict,
                 targets: dict | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """국가 × 멤버의 **(수량, 매출KRW)** 두 표. 두 브랜드 다 나온다 —
    포토이즘은 '프레임 이름', 스내피즘은 '상품 이름' 이 멤버다.

    ★수량만 주면 '이 멤버가 얼마를 벌었나'를 문서에서 알 수 없어 매출을 함께 낸다.
      매출은 국가별 내역과 같은 분자(실결제+쿠폰·코인)를 원화로 환산한 값이다.
    """
    if not titles:
        return pd.DataFrame(), pd.DataFrame()
    from photoism_rules import COIN_CC, COUPON_CC

    rate = smap._rate_case(rates)
    con = _con()
    try:
        if brand == "photoism":
            cpn, coin = _sqlist(sorted(COUPON_CC)), _sqlist(sorted(COIN_CC))
            df = con.execute(f"""
                WITH t AS (
                  SELECT "국가", lower(trim("국가코드")) AS cc,
                         "결제 단위",
                         {_mem_sql('trim("프레임 이름")')} AS member,
                         CAST("최종 결제 금액" AS BIGINT) AS pay,
                         CAST("쿠폰 할인 금액"  AS BIGINT) AS cpn,
                         CAST("서비스코인"      AS BIGINT) AS coin,
                         CAST("상품총액"        AS BIGINT) AS tot,
                         TRY_CAST("상품 단가" AS DOUBLE) AS up
                  FROM read_parquet('{PH_RAW.as_posix()}')
                  WHERE TRY_CAST("날짜" AS DATE) BETWEEN DATE '{start}' AND DATE '{end}'
              -- ★테스트 매장은 매출에서도 뺀다 (store_rules 주석 참고).
              --   장비 목록은 진작 빼고 있었는데 매출만 남아
              --   '1대당 매출' 의 분자·분모가 어긋나 있었다.
              {store_rules.not_test_sql()}
                    {_title_pred(titles, start, end)}
                    {_gubun_filter()}
                    AND lower(CAST("취소 여부" AS VARCHAR)) NOT IN ('true','1')
                ), f AS (
                  SELECT *, {_ph_num(cpn, coin)} AS num
                  FROM t
                )
                SELECT "국가", member, SUM(num) AS num,
                       SUM(num * {rate}) AS krw_raw,
                       SUM(CASE WHEN up > 0 THEN up ELSE 0 END) AS up_sum,
                       SUM(CASE WHEN up > 0 THEN 1  ELSE 0 END) AS up_cnt
                FROM f WHERE num <> 0 GROUP BY 1,2
            """).df()
        else:
            df = con.execute(f"""
                SELECT "국가", trim("상품 이름") AS member,
                       SUM(CAST("최종 결제 금액" AS BIGINT)
                           + CAST("쿠폰 할인 금액" AS BIGINT)) AS num,
                       SUM((CAST("최종 결제 금액" AS BIGINT)
                           + CAST("쿠폰 할인 금액" AS BIGINT)) * {rate}) AS krw_raw,
                       SUM(CASE WHEN TRY_CAST("상품 단가" AS DOUBLE) > 0
                                THEN TRY_CAST("상품 단가" AS DOUBLE) ELSE 0 END) AS up_sum,
                       SUM(CASE WHEN TRY_CAST("상품 단가" AS DOUBLE) > 0
                                THEN 1 ELSE 0 END) AS up_cnt
                FROM read_parquet('{SN_MASTER.as_posix()}')
                WHERE CAST("날짜" AS DATE) BETWEEN DATE '{start}' AND DATE '{end}'
                  AND "프레임 이름" IN ({_sqlist(titles)}) {_sn_gubun()}
                  AND NOT COALESCE("취소 여부", FALSE)
                  AND CAST("최종 결제 금액" AS BIGINT)
                      + CAST("쿠폰 할인 금액" AS BIGINT) <> 0
                GROUP BY 1,2
            """).df()
    finally:
        con.close()

    if df.empty:
        return pd.DataFrame(), pd.DataFrame()
    df["국가"] = df["국가"].map(lambda x: NAT_KO.get(x, x))
    df["member"] = df["member"].map(_norm_member)
    # 별칭·키릴 정규화로 같은 멤버가 합쳐질 수 있으므로 다시 모은다.
    df = df.groupby(["국가", "member"], as_index=False).agg(
        num=("num", "sum"), krw_raw=("krw_raw", "sum"),
        up_sum=("up_sum", "sum"), up_cnt=("up_cnt", "sum"))
    df["수량"] = _allocate(df, targets)
    df["매출"] = _allocate_krw(df, targets)
    piv = df.pivot_table(index="국가", columns="member", values="수량",
                         aggfunc="sum", fill_value=0).astype(int)
    rev = df.pivot_table(index="국가", columns="member", values="매출",
                         aggfunc="sum", fill_value=0).astype("int64")
    order = piv.sum(axis=1).sort_values(ascending=False).index
    # 두 표의 행·열 순서를 맞춰 둔다 — 화면에서 같은 칸을 겹쳐 그리기 때문.
    return piv.loc[order], rev.reindex(index=order, columns=piv.columns,
                                       fill_value=0)


def price_table(brand: str, titles: list[str], start: str, end: str) -> pd.DataFrame:
    """국가별 평균 단가. 스내피즘은 **상품 형태마다 단가가 다르다** → 형태까지 쪼갠다.
    ★0원 거래는 평균에서 뺀다(AVG(NULLIF(...,0))). 넣으면 실제보다 낮아진다."""
    if not titles:
        return pd.DataFrame(columns=["국가", "unit", "형태", "단가"])
    con = _con()
    try:
        if brand == "photoism":
            df = con.execute(f"""
                SELECT "국가", any_value("결제 단위") AS unit, '프레임' AS 형태,
                       AVG(NULLIF(TRY_CAST("상품 단가" AS DOUBLE), 0)) AS 단가
                FROM read_parquet('{PH_RAW.as_posix()}')
                WHERE TRY_CAST("날짜" AS DATE) BETWEEN DATE '{start}' AND DATE '{end}'
              -- ★테스트 매장은 매출에서도 뺀다 (store_rules 주석 참고).
              --   장비 목록은 진작 빼고 있었는데 매출만 남아
              --   '1대당 매출' 의 분자·분모가 어긋나 있었다.
              {store_rules.not_test_sql()}
                  {_title_pred(titles, start, end)}
                  {_gubun_filter()}
                  AND lower(CAST("취소 여부" AS VARCHAR)) NOT IN ('true','1')
                GROUP BY 1,3
            """).df()
        else:
            df = con.execute(f"""
                SELECT "국가", any_value("결제 단위") AS unit,
                       "상품 카테고리" AS 형태,
                       AVG(NULLIF(TRY_CAST("상품 단가" AS DOUBLE), 0)) AS 단가
                FROM read_parquet('{SN_MASTER.as_posix()}')
                WHERE CAST("날짜" AS DATE) BETWEEN DATE '{start}' AND DATE '{end}'
                  AND "프레임 이름" IN ({_sqlist(titles)}) {_sn_gubun()}
                  AND NOT COALESCE("취소 여부", FALSE)
                GROUP BY 1,3
            """).df()
    finally:
        con.close()
    if df.empty:
        return pd.DataFrame(columns=["국가", "unit", "형태", "단가"])
    df = df.dropna(subset=["단가"])
    df["국가"] = df["국가"].map(lambda x: NAT_KO.get(x, x))
    df["단가"] = df["단가"].round().astype("int64")
    return df.sort_values(["국가", "단가"], ascending=[True, False])


# ── 요율 · MG ─────────────────────────────────────────────────────────────
def get_rs(brand: str, ticket: str) -> dict:
    """요율. **화면 저장값이 지라보다 우선**한다 — 지라 입력률이 낮아
    (스내피즘 26% / 포토이즘 7%) 실무자가 직접 넣는 경로가 정상 경로다."""
    tk = str(ticket or "").strip().upper()
    saved = _rates_store.load().get("rates", {}).get(f"{brand}:{tk}")
    if saved:
        return {"agency": saved.get("agency"), "mgmt": saved.get("mgmt"),
                # ★국가별 예외 요율 {국가명: 0.07}. 캐릭터 IP 는 나라마다 요율이 다르다
                #   (가나디: 한국 7% · 일본 10% · 중국 12%). 여기 없는 나라는 기본 요율.
                "agency_cc": dict(saved.get("agency_cc") or {}),
                "source": "화면 입력", "by": saved.get("by"), "at": saved.get("at")}
    try:
        import jira_client
        for e in jira_client.fetch_rs_data(brand=brand).values():
            if str(e.get("ticket_key") or "").upper() == tk:
                return {"agency": e.get("rs_agency"), "mgmt": e.get("rs_mgmt"),
                        "agency_cc": {}, "source": "지라", "by": None, "at": None}
    except Exception as _e:                       # noqa: BLE001
        # ★jira_client 는 이 상황에서 **일부러** 예외를 던진다(:252 주석 —
        #   "조회 실패로 '요율 없음'이 되면 정산액이 0원으로 뒤바뀐다").
        #   그걸 여기서 삼켜 '없음' 으로 만들면 상류의 방어가 무력해진다 —
        #   실무자는 "지라에 요율이 없구나" 로 읽고 직접 넣어 버리는데,
        #   실제로는 **못 물어본 것**이라 계약과 다른 값을 넣을 수 있다.
        #   (만료 캐시라도 있으면 jira_client 가 그걸 주므로 여기까지 안 온다)
        return {"agency": None, "mgmt": None, "agency_cc": {},
                "source": "조회실패", "by": None, "at": str(_e)[:200]}
    return {"agency": None, "mgmt": None, "agency_cc": {},
            "source": "없음", "by": None, "at": None}


def set_rs(brand: str, ticket: str, agency, mgmt, by: str,
           agency_cc: dict | None = None) -> None:
    """agency_cc = {국가명: 요율} 국가별 예외. 빈 dict 면 전 국가 기본 요율."""
    from datetime import datetime
    tk = str(ticket or "").strip().upper()
    key = f"{brand}:{tk}"
    now = datetime.now().isoformat(timespec="seconds")
    # ★0 도 저장한다 — '이 나라는 0%' 와 '안 정함(기본 요율)' 은 다른 뜻이다.
    cc = {str(k): float(v) for k, v in (agency_cc or {}).items() if v is not None}

    def _fn(d):
        cur = d.setdefault("rates", {}).get(key) or {}
        cur.update({"agency": agency, "mgmt": mgmt, "agency_cc": cc,
                    "by": by, "at": now})
        d["rates"][key] = cur          # 파트너사명 등 다른 필드는 보존한다

    _rates_store.mutate(_fn)


# ── 파트너사 · 부가세 ──────────────────────────────────────────────────────
def get_partner(brand: str, ticket: str) -> dict:
    """수취처별 회사명과 부가세 적용 여부.

    문서에 '제출처 ○○ 귀중' 으로 찍히고, 하단 정산 내역표의 출자자명이 된다.
    부가세는 국내 파트너만 해당하므로 티켓별로 끌 수 있게 둔다.
    """
    tk = str(ticket or "").strip().upper()
    rec = (_rates_store.load().get("rates", {}) or {}).get(f"{brand}:{tk}") or {}
    return {"agency_name": rec.get("agency_name", ""),
            "mgmt_name": rec.get("mgmt_name", ""),
            "vat": rec.get("vat", True)}


def set_partner(brand: str, ticket: str, agency_name: str, mgmt_name: str,
                vat: bool, by: str) -> None:
    from datetime import datetime
    tk = str(ticket or "").strip().upper()
    key = f"{brand}:{tk}"
    now = datetime.now().isoformat(timespec="seconds")

    def _fn(d):
        cur = d.setdefault("rates", {}).get(key) or {}
        cur.update({"agency_name": str(agency_name or "").strip(),
                    "mgmt_name": str(mgmt_name or "").strip(),
                    "vat": bool(vat), "by": by, "at": now})
        d["rates"][key] = cur

    _rates_store.mutate(_fn)


def cancel_amount(brand: str, titles: list[str], start: str, end: str,
                  rates: dict) -> int:
    """그 기간 취소 금액(원화, 양수). 정산서 표지에 '취소 금액'으로 적는다.

    취소는 음수 거래로 들어온다. 매출액에는 이미 차감돼 있고, 이 값은
    '얼마가 취소됐는지' 를 보여주기 위한 별도 표기다.

    ★분자는 매출액과 **같은 식**이어야 한다 — 실결제 + (지정국 쿠폰) + (지정국 코인).
      실결제만 세면 쿠폰·코인 가산 국가의 취소가 과소 표기된다. 지금 데이터엔
      취소가 한국(가산 대상 아님)에만 있어 값이 같지만, 가산국에서 취소가 한 건
      생기는 순간 어긋난다.
    """
    if not titles:
        return 0
    rate = smap._rate_case(rates)
    con = _con()
    try:
        if brand == "photoism":
            from photoism_rules import COIN_CC, COUPON_CC
            cpn, coin = _sqlist(sorted(COUPON_CC)), _sqlist(sorted(COIN_CC))
            # 위 두 곳과 **같은 식**을 써야 취소 차감이 매출과 어긋나지 않는다.
            num = _ph_num(cpn, coin,
                          pay='CAST("최종 결제 금액" AS BIGINT)',
                          c1='CAST("쿠폰 할인 금액" AS BIGINT)',
                          c2='CAST("서비스코인" AS BIGINT)',
                          tot='CAST("상품총액" AS BIGINT)')
            q = f"""
                SELECT CAST(ROUND(SUM(-({num}) * {rate})) AS BIGINT) AS v
                FROM (SELECT *, lower(trim("국가코드")) AS cc
                      FROM read_parquet('{PH_RAW.as_posix()}'))
                WHERE TRY_CAST("날짜" AS DATE) BETWEEN DATE '{start}' AND DATE '{end}'
              -- ★테스트 매장은 매출에서도 뺀다 (store_rules 주석 참고).
              --   장비 목록은 진작 빼고 있었는데 매출만 남아
              --   '1대당 매출' 의 분자·분모가 어긋나 있었다.
              {store_rules.not_test_sql()}
                  {_title_pred(titles, start, end)} {_gubun_filter()}
                  AND CAST("최종 결제 금액" AS BIGINT) < 0"""
        else:
            q = f"""
                SELECT CAST(ROUND(SUM(-(CAST("최종 결제 금액" AS BIGINT)
                       + CAST("쿠폰 할인 금액" AS BIGINT)) * {rate})) AS BIGINT) AS v
                FROM read_parquet('{SN_MASTER.as_posix()}')
                WHERE CAST("날짜" AS DATE) BETWEEN DATE '{start}' AND DATE '{end}'
                  AND "프레임 이름" IN ({_sqlist(titles)}) {_sn_gubun()}
                  AND CAST("최종 결제 금액" AS BIGINT)
                      + CAST("쿠폰 할인 금액" AS BIGINT) < 0"""
        v = con.execute(q).fetchone()[0]
    finally:
        con.close()
    return int(v or 0)


def doc_number(ip: str, end: str, kind: str, seq: int = 1) -> str:
    """문서번호 SB-SET-{YYYYMM}-{IP코드}-{L|A}{일련번호}.

    IP코드는 영문/숫자만 남겨 앞 3글자를 대문자로. 한글만 있으면 IPX 로 둔다.
    """
    import re as _re
    code = _re.sub(r"[^A-Za-z0-9]", "", str(ip or ""))[:3].upper()
    if len(code) < 3:
        code = (code + "IPX")[:3]
    return f"SB-SET-{end[:7].replace('-', '')}-{code}-{'L' if kind == 'agency' else 'A'}{seq:02d}"


def issuer() -> dict:
    """발행처 정보. config.json 의 settlement.issuer 를 쓰고 없으면 기본값."""
    try:
        cfg = json.loads((BASE_DIR / "config.json").read_text(encoding="utf-8"))
        s = (cfg.get("settlement") or {}).get("issuer") or {}
    except Exception:
        s = {}
    return {"company": s.get("company", "㈜서북"),
            "company_en": s.get("company_en", "SEOBUK CORP."),
            "team": s.get("team", "콘텐츠운영팀"),
            "name": s.get("name", ""),
            "email": s.get("email", "")}


def vat_split(pay: int, vat: bool = True) -> tuple[int, int]:
    """(공급가액, 부가세). 총 지급액은 **부가세 포함** 금액으로 본다.

    기존 정산서 엑셀과 같은 계산 — 3,683,854 → 공급가액 3,348,958 · VAT 334,896.
    """
    pay = int(pay or 0)
    if not vat:
        return pay, 0
    supply = _round_half_up(pay / 1.1)     # 내장 round(은행식) 금지 — 위 주석
    return supply, pay - supply


def get_mg(brand: str, ticket: str) -> dict:
    """MG. v1은 있음/없음 체크 + 담당자 수기 입력. 소진·이월 자동계산은 2차."""
    tk = str(ticket or "").strip().upper()
    return _mg_store.load().get("mg", {}).get(f"{brand}:{tk}") or {
        "has_mg": False, "amount": 0, "note": "", "by": None, "at": None}


def set_mg(brand: str, ticket: str, has_mg: bool, amount, note: str, by: str) -> None:
    from datetime import datetime
    tk = str(ticket or "").strip().upper()
    rec = {"has_mg": bool(has_mg), "amount": int(amount or 0), "note": note,
           "by": by, "at": datetime.now().isoformat(timespec="seconds")}
    _mg_store.mutate(
        lambda d: d.setdefault("mg", {}).update({f"{brand}:{tk}": rec}))


def store_versions() -> tuple[float, float, float]:
    """캐시 키용. ★밑줄로 시작하는 인자명에 넣으면 해시에서 빠지니 주의."""
    return (_rates_store.version(), _mg_store.version(), smap.mapping_version())


# [삭제 2026-08-19] `settle()` — 국가별 정산액을 한 줄로 내던 옛 함수. 지금은
#   `settlement_pdf._alloc_settle`(최대잔여법 + half-up)이 문서와 화면 양쪽을 맡고,
#   이건 아무 데서도 안 불렸다. 내장 round(은행식)를 쓰고 있어 되살리면 1원이 어긋난다.


def build_context(picks: dict, start: str, end: str, ip_name: str,
                  rates: dict, rate_date: str, issued: str,
                  rate_source: str = "") -> dict:
    """PDF 한 부를 만드는 데 필요한 값을 한 번에 모은다.

    picks = {brand: ticket 또는 [ticket, ...]}.
    ★티켓을 여러 장 넘길 수 있다 — 한 IP를 회차별로 나눠 등록한 경우 합쳐서 한 장으로
      정산해야 한다. 타이틀은 전부 합치고 요율·MG 는 첫 티켓 기준으로 잡는다
      (요율이 서로 다르면 화면에서 미리 경고한다).
    """
    ctx = {"ip": ip_name, "start": start, "end": end, "issued": issued,
           "rate_date": rate_date, "rate_source": rate_source,
           "details": {}, "pivots": {}, "pivots_rev": {}, "prices": {},
           "rs": {}, "mg": {}, "tickets": {}, "titles": {}, "units": {}}
    for brand, sel in picks.items():
        tickets = [sel] if isinstance(sel, str) else list(sel or [])
        tickets = [t for t in tickets if t]
        if not tickets:
            continue
        titles = []
        for tk in tickets:
            titles += [t for t in titles_for_ticket(brand, tk) if t not in titles]
        if not titles:
            continue
        ticket = tickets[0]
        d = fill_open(country_detail(brand, titles, start, end, rates),
                      open_countries(brand, start, end))
        ctx["details"][brand] = d
        # 별첨 합계를 본문 국가 소계에 맞춘다 — 같은 문서에서 두 숫자가 다르면 안 된다.
        # ★판매 항목별로 나뉘면 한 나라가 여러 행이다. 나라별로 더해야 한다
        #   (그냥 dict 로 만들면 마지막 항목만 남아 별첨 합계가 어긋난다).
        _tg = {}
        for _, r in d.iterrows():
            q, k = _tg.get(r["국가"], (0, 0))
            _tg[r["국가"]] = (q + int(r["수량"]), k + int(r["매출액"]))
        ctx["pivots"][brand], ctx["pivots_rev"][brand] = member_pivot(
            brand, titles, start, end, rates, _tg)
        ctx["titles"][brand] = titles
        ctx["rs"][brand] = get_rs(brand, ticket)
        ctx["mg"][brand] = get_mg(brand, ticket)
        # 파트너사명·부가세는 티켓마다 있지만 문서는 한 장이므로 먼저 채워진 값을 쓴다.
        p = get_partner(brand, ticket)
        cur = ctx.setdefault("partner", {"agency_name": "", "mgmt_name": "",
                                         "vat": True})
        for f in ("agency_name", "mgmt_name"):
            if not cur[f] and p[f]:
                cur[f] = p[f]
        cur["vat"] = cur["vat"] and p["vat"]
        # 판매기간은 **넘긴 티켓 전체를 감싸게** 잡는다. 한 IP를 회차별로 나눠
        # 등록하면(예: 데뷔 기념 + 전지점 전환) 첫 티켓만 보고 적을 경우 문서의
        # 판매기간이 실제보다 짧게 찍힌다.
        _ents = [e for e in (smap.lookup_ticket(brand, t) for t in tickets) if e]
        if _ents:
            _st = [e["startdate"] for e in _ents if e.get("startdate")]
            _du = [e["duedate"] for e in _ents if e.get("duedate")]
            ctx["tickets"][brand] = {
                **_ents[0],
                "startdate": min(_st) if _st else None,
                "duedate": max(_du) if _du else None,
                "ticket_keys": [e["ticket_key"] for e in _ents],
            }
        else:
            ctx["tickets"][brand] = None
        for _, r in d.iterrows():
            ctx["units"].setdefault(r["국가"], r["unit"])
        pt = price_table(brand, titles, start, end)
        ctx["prices"][brand] = {
            nat: dict(zip(g["형태"], g["단가"])) for nat, g in pt.groupby("국가")
        }
        # [중단 2026-08-19] `cancel_amount(...)` — 441MB 원본을 한 번 더 풀스캔하는데
        #   **결과가 문서 어디에도 안 들어간다**(settlement_pdf 가 `cancel` 로 받아
        #   놓고 HTML 에 안 쓴다 — grep 확인). 표지에 '취소 금액'을 되살릴 일이 생기면
        #   이 두 줄만 풀면 된다. 함수 자체는 그대로 둔다.
        ctx.setdefault("cancel", {})[brand] = 0
    ctx["issuer"] = issuer()
    ctx["rates"] = {k: v for k, v in rates.items()
                    if isinstance(v, (int, float)) and v > 0}
    return ctx


# ── 발행 이력 · 스냅샷 잠금 ────────────────────────────────────────────────
# 왜 얼려야 하나 — 매출 데이터는 매일 갱신되고 취소도 뒤늦게 반영된다.
# 발행 시점 값을 남겨두지 않으면 "지난달에 보낸 정산서"를 다시 뽑을 수 없고,
# 파트너가 금액을 물어왔을 때 대조할 원본이 없다.
_issue_store = JsonStore("settlement_issues.json", default={"issues": {}})


def _ctx_key(ip: str, start: str, end: str) -> str:
    return f"{ip}|{start}|{end}"


def ctx_to_json(ctx: dict) -> dict:
    """DataFrame 을 JSON 으로. 발행 시점 값을 그대로 재현할 수 있어야 한다."""
    def _piv(src):
        return {b: {"index": list(p.index), "columns": list(p.columns),
                    "values": p.values.tolist()}
                for b, p in (src or {}).items() if p is not None and not p.empty}

    out = {k: v for k, v in ctx.items()
           if k not in ("details", "pivots", "pivots_rev")}
    out["details"] = {b: d.to_dict("records") for b, d in ctx["details"].items()}
    out["pivots"] = _piv(ctx.get("pivots"))
    out["pivots_rev"] = _piv(ctx.get("pivots_rev"))
    return out


def ctx_from_json(d: dict) -> dict:
    def _piv(src):
        return {b: pd.DataFrame(v["values"], index=v["index"], columns=v["columns"])
                for b, v in (src or {}).items()}

    ctx = dict(d)
    ctx["details"] = {b: pd.DataFrame(v) for b, v in (d.get("details") or {}).items()}
    ctx["pivots"] = _piv(d.get("pivots"))
    # 옛 스냅샷엔 매출 피벗이 없다 — 없으면 빈 dict 로 두고 수량만 그린다.
    ctx["pivots_rev"] = _piv(d.get("pivots_rev"))
    return ctx


def record_issue(ctx: dict, by: str, reason: str = "") -> dict:
    """발행 기록 + 스냅샷 저장. 같은 IP·기간을 다시 내면 정정본 v2, v3… 이 된다."""
    from datetime import datetime
    key = _ctx_key(ctx["ip"], ctx["start"], ctx["end"])
    snap = ctx_to_json(ctx)

    box = {}

    def _fn(d):
        issues = d.setdefault("issues", {})
        cur = issues.get(key) or {"versions": []}
        v = len(cur["versions"]) + 1
        cur["versions"].append({
            "version": v, "by": by,
            "at": datetime.now().isoformat(timespec="seconds"),
            "reason": reason, "snapshot": snap,
        })
        issues[key] = cur
        box["version"] = v

    _issue_store.mutate(_fn)
    return {"version": box.get("version", 1), "key": key}


# ── 발행된 PDF 보관 ────────────────────────────────────────────────────────
# ★왜 필요한가(2026-08-05 사용자 제보): 만든 PDF 는 st.session_state 에만 있었다.
#   새로고침·세션만료·서버재시작이면 그대로 사라져서, **발행은 됐는데 문서를 다시
#   받을 방법이 없었다.** 스냅샷(JSON)만 남기고 결과물은 안 남긴 셈이다.
#   대외로 나간 문서라 원본 그대로 다시 꺼낼 수 있어야 한다 → 파일로 보관한다.
#   reports/ 는 .gitignore 에 있어 커밋되지 않는다.
import dev_mode  # noqa: E402
# ★개발 서버는 발행분을 따로 쌓는다 — 대외로 나간 실제 문서와 섞이면 안 된다.
ISSUED_DIR = BASE_DIR / "reports" / ("issued_dev" if dev_mode.IS_DEV else "issued")
_SAFE_RE = re.compile(r'[\\/:*?"<>|\s]+')


def _issue_stem(key: str, version: int) -> str:
    """'IP|start|end' + 버전 → 파일명 앞부분. 윈도 금지문자를 걷어낸다."""
    return _SAFE_RE.sub("_", f"{key.replace('|', '_')}_v{int(version)}").strip("_")


def save_issued_pdfs(key: str, version: int, pdfs: dict) -> list:
    """발행 직후 PDF 를 디스크에 남긴다. 실패해도 발행 자체를 막지 않는다."""
    out = []
    try:
        ISSUED_DIR.mkdir(parents=True, exist_ok=True)
        for lab, data in (pdfs or {}).items():
            p = ISSUED_DIR / f"{_issue_stem(key, version)}__{_SAFE_RE.sub('_', lab)}.pdf"
            p.write_bytes(data)
            out.append(p)
    except Exception:
        pass
    return out


def issued_pdfs(key: str, version: int) -> dict:
    """보관해 둔 PDF 를 {라벨: bytes} 로. 없으면 빈 딕셔너리."""
    out = {}
    try:
        for p in sorted(ISSUED_DIR.glob(f"{_issue_stem(key, version)}__*.pdf")):
            out[p.stem.split("__", 1)[-1]] = p.read_bytes()
    except Exception:
        pass
    return out


def list_issues(ip: str = "", start: str = "", end: str = "") -> list[dict]:
    """발행 이력. 인자를 주면 그 건만, 없으면 전부(최신순)."""
    issues = _issue_store.load().get("issues", {})
    keys = [_ctx_key(ip, start, end)] if ip else list(issues)
    out = []
    for k in keys:
        blk = issues.get(k)
        if not blk:
            continue
        for v in blk["versions"]:
            out.append({"key": k, **{x: v[x] for x in
                                     ("version", "by", "at", "reason")}})
    return sorted(out, key=lambda x: x["at"], reverse=True)


def issue_version(ip: str, start: str, end: str) -> int:
    """다음에 발행하면 몇 번째가 되는지. 1이면 최초 발행."""
    blk = _issue_store.load().get("issues", {}).get(_ctx_key(ip, start, end))
    return len(blk["versions"]) + 1 if blk else 1


def load_snapshot(ip: str, start: str, end: str, version: int | None = None) -> dict | None:
    """발행 당시 스냅샷을 되살린다. version 을 안 주면 최신본."""
    blk = _issue_store.load().get("issues", {}).get(_ctx_key(ip, start, end))
    if not blk or not blk["versions"]:
        return None
    vs = blk["versions"]
    rec = vs[-1] if version is None else next(
        (v for v in vs if v["version"] == version), None)
    if not rec:
        return None
    ctx = ctx_from_json(rec["snapshot"])
    ctx["version"] = rec["version"]
    ctx["reason"] = rec.get("reason", "")
    return ctx


def issues_version() -> float:
    return _issue_store.version()


def verify(detail: pd.DataFrame, rates: dict, tol: float = 0.002) -> list[str]:
    """현지 × 환율 == 매출(KRW) 검증. 어긋나면 환율·통화 매핑이 깨진 것이다."""
    bad = []
    for _, r in detail.iterrows():
        if not r["매출액"]:
            continue
        exp = round(r["현지"] * float(rates.get(r["unit"], 1)))
        if abs(exp - r["매출액"]) > max(2, r["매출액"] * tol):
            bad.append(f"{r['국가']}: 현지 {r['현지']:,} × {rates.get(r['unit'])} "
                       f"= {exp:,} ≠ {r['매출액']:,}")
    return bad
