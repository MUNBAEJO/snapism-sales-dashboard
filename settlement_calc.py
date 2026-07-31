"""IP 정산서 — 2단계: 계산 엔진.

확정 매핑(`settlement_map`)으로 고른 타이틀만 가지고
국가별 매출·수량·단가·멤버별 상세와 수취처별 정산액을 낸다.

★수량은 두 브랜드 같은 공식 `floor(현지 분자 ÷ 현지 평균단가)` 이고,
  문서 표기도 **'건수'로 통일**한다(2026-07-31). 포토이즘은 프레임, 스내피즘은
  스티커·포토카드로 물건만 다를 뿐 둘 다 '팔린 개수'라 같은 지표다.
★'현지 매출' 은 매출액과 **같은 분자**를 현지통화로 적는다. 실결제만 쓰면
  전액 쿠폰 결제인 국가가 0으로 보여 "매출 없는데 정산액이 있다"가 된다.
  검증식: 현지 × 환율 == 매출(KRW).

관련: CURRENT-PROJECTS/IP-정산서-생성.md · 지라 CO-288
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

import ip_classify
import settlement_map as smap
from json_store import JsonStore

BASE_DIR = Path(__file__).parent
PH_AGG = BASE_DIR / "data" / "master_photoism_agg.parquet"
PH_RAW = BASE_DIR / "data" / "master_photoism.parquet"
SN_MASTER = BASE_DIR / "data" / "master.parquet"

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

    ★_raw_titles 는 '타이틀명' 으로만 좁히는데, 같은 타이틀명 안에 렌탈 행이 섞여 있다
      (예: 260628 추영우 → 아티스트 8,610,000 + 렌탈 98,000). 집계본은 IP구분으로
      걸러지지만 원거래는 안 걸러져서 렌탈이 정산에 다시 들어왔다.
      집계본과 같은 분류식을 원거래에도 그대로 적용한다.
    """
    g = ",".join(f"'{x}'" for x in smap.SETTLE_GUBUN)
    return f"AND ({ip_classify.IP_GUBUN_SQL}) IN ({g})"


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


def _raw_titles(titles: list[str], start: str, end: str) -> list[str]:
    """포토이즘 파생 '타이틀' → 원거래의 '타이틀명' 들.

    집계본의 `타이틀` 은 날짜코드 + 별칭 적용 IP명이라 원거래 컬럼과 다르다.
    멤버·단가처럼 원거래에만 있는 값을 뽑으려면 이 다리를 건너야 한다.
    """
    if not titles:
        return []
    con = _con()
    try:
        df = con.execute(f"""
            SELECT DISTINCT "타이틀명"
            FROM read_parquet('{PH_AGG.as_posix()}')
            WHERE "날짜" BETWEEN DATE '{start}' AND DATE '{end}'
              AND "타이틀" IN ({_sqlist(titles)})
              AND COALESCE("타이틀명",'') <> ''
        """).df()
    finally:
        con.close()
    return df["타이틀명"].tolist()


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
            raws = _raw_titles(titles, start, end)
            if not raws:
                return pd.DataFrame(columns=["국가", "unit", "수량", "현지", "매출액", "건수"])
            df = con.execute(f"""
                WITH t AS (
                  SELECT "국가", lower(trim("국가코드")) AS cc, "결제 단위" AS unit,
                         {rate} AS r,
                         CAST("최종 결제 금액" AS BIGINT) AS pay,
                         CAST("쿠폰 할인 금액"  AS BIGINT) AS cpn,
                         CAST("서비스코인"      AS BIGINT) AS coin,
                         TRY_CAST("상품 단가" AS DOUBLE) AS up
                  FROM read_parquet('{PH_RAW.as_posix()}')
                  WHERE TRY_CAST("날짜" AS DATE) BETWEEN DATE '{start}' AND DATE '{end}'
                    AND "타이틀명" IN ({_sqlist(raws)})
                    {_gubun_filter()}
                    AND lower(CAST("취소 여부" AS VARCHAR)) NOT IN ('true','1')
                ), f AS (
                  SELECT *, pay
                         + CASE WHEN cc IN ({cpn})  THEN cpn  ELSE 0 END
                         + CASE WHEN cc IN ({coin}) THEN coin ELSE 0 END AS num
                  FROM t
                )
                SELECT "국가", any_value(unit) AS unit,
                       CAST(SUM(num) AS BIGINT) AS 현지,
                       CAST(ROUND(SUM(num * r)) AS BIGINT) AS 매출액,
                       CAST(SUM(CASE WHEN num < 0 THEN -1 ELSE 1 END) AS BIGINT) AS 건수,
                       AVG(NULLIF(up, 0)) AS 단가
                -- 음수(취소)를 살려야 차감된다
                FROM f WHERE num <> 0 GROUP BY 1
            """).df()
        else:
            df = con.execute(f"""
                WITH t AS (
                  SELECT "국가", "결제 단위" AS unit, {rate} AS r,
                         CAST("최종 결제 금액" AS BIGINT)
                         + CAST("쿠폰 할인 금액" AS BIGINT) AS num,
                         TRY_CAST("상품 단가" AS DOUBLE) AS up
                  FROM read_parquet('{SN_MASTER.as_posix()}')
                  WHERE CAST("날짜" AS DATE) BETWEEN DATE '{start}' AND DATE '{end}'
                    AND "프레임 이름" IN ({_sqlist(titles)})
                    AND NOT COALESCE("취소 여부", FALSE)
                )
                SELECT "국가", any_value(unit) AS unit,
                       CAST(SUM(num) AS BIGINT) AS 현지,
                       CAST(ROUND(SUM(num * r)) AS BIGINT) AS 매출액,
                       CAST(SUM(CASE WHEN num < 0 THEN -1 ELSE 1 END) AS BIGINT) AS 건수,
                       AVG(NULLIF(up, 0)) AS 단가
                FROM t WHERE num <> 0 GROUP BY 1
            """).df()
    finally:
        con.close()

    if df.empty:
        return pd.DataFrame(columns=["국가", "unit", "수량", "현지", "매출액", "건수"])
    df["국가"] = df["국가"].map(lambda x: NAT_KO.get(x, x))
    # ★수량은 원화가 아니라 현지통화끼리 나눈다. 환산 후 나누면 환율배수만큼 부푼다.
    df["수량"] = (df["현지"] / df["단가"].replace(0, pd.NA)).fillna(0).astype("int64")
    return df.sort_values("매출액", ascending=False).reset_index(drop=True)


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
    """매출 0인 오픈 국가를 0행으로 채운다."""
    have = set(detail["국가"])
    add = [{"국가": r["국가"], "unit": r["unit"], "수량": 0, "현지": 0,
            "매출액": 0, "건수": 0, "단가": None}
           for _, r in opened.iterrows() if r["국가"] not in have]
    if not add:
        return detail.sort_values("매출액", ascending=False).reset_index(drop=True)
    # 빈/전부-NA 열이 섞이면 concat 이 dtype 을 흔든다 → 원본 열 구성에 맞춰 붙인다.
    extra = pd.DataFrame(add).reindex(columns=detail.columns)
    detail = pd.concat([detail, extra.astype(detail.dtypes.to_dict(), errors="ignore")],
                       ignore_index=True)
    return detail.sort_values("매출액", ascending=False).reset_index(drop=True)


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


def _allocate_krw(df: "pd.DataFrame") -> "pd.Series":
    """멤버별 매출(원)을 **국가 합계에 정확히 맞춰** 정수로 배분한다(최대잔여법).

    ★멤버마다 반올림해 더하면 국가 합계가 국가별 내역과 1원씩 어긋난다(중국
      68,816,170 vs 68,816,169). 같은 문서 안에서 1원이 안 맞으면 반드시 질문이
      들어오므로, 국가 합계를 먼저 정하고 그 안에서 나눈다. 수량과 같은 방식.
    """
    import math
    out = pd.Series(0, index=df.index, dtype="int64")
    for _, g in df.groupby("국가"):
        target = int(round(g["krw_raw"].sum()))
        base = g["krw_raw"].map(math.floor).astype("int64")
        gap = target - int(base.sum())
        if gap > 0:
            frac = g["krw_raw"] - base
            order = sorted(g.index, key=lambda i: (-frac[i], str(g.loc[i, "member"])))
            for i in order[:gap]:
                base[i] += 1
        out.loc[base.index] = base
    return out


def _allocate(df: "pd.DataFrame") -> "pd.Series":
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
        target = int(g["num"].sum() / nat_price) if nat_price else 0
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
                 rates: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
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
            raws = _raw_titles(titles, start, end)
            if not raws:
                return pd.DataFrame(), pd.DataFrame()
            df = con.execute(f"""
                WITH t AS (
                  SELECT "국가", lower(trim("국가코드")) AS cc,
                         "결제 단위",
                         trim("프레임 이름") AS member,
                         CAST("최종 결제 금액" AS BIGINT) AS pay,
                         CAST("쿠폰 할인 금액"  AS BIGINT) AS cpn,
                         CAST("서비스코인"      AS BIGINT) AS coin,
                         TRY_CAST("상품 단가" AS DOUBLE) AS up
                  FROM read_parquet('{PH_RAW.as_posix()}')
                  WHERE TRY_CAST("날짜" AS DATE) BETWEEN DATE '{start}' AND DATE '{end}'
                    AND "타이틀명" IN ({_sqlist(raws)})
                    {_gubun_filter()}
                    AND lower(CAST("취소 여부" AS VARCHAR)) NOT IN ('true','1')
                ), f AS (
                  SELECT *, pay
                         + CASE WHEN cc IN ({cpn})  THEN cpn  ELSE 0 END
                         + CASE WHEN cc IN ({coin}) THEN coin ELSE 0 END AS num
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
                  AND "프레임 이름" IN ({_sqlist(titles)})
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
    df["수량"] = _allocate(df)
    df["매출"] = _allocate_krw(df)
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
            raws = _raw_titles(titles, start, end)
            if not raws:
                return pd.DataFrame(columns=["국가", "unit", "형태", "단가"])
            df = con.execute(f"""
                SELECT "국가", any_value("결제 단위") AS unit, '프레임' AS 형태,
                       AVG(NULLIF(TRY_CAST("상품 단가" AS DOUBLE), 0)) AS 단가
                FROM read_parquet('{PH_RAW.as_posix()}')
                WHERE TRY_CAST("날짜" AS DATE) BETWEEN DATE '{start}' AND DATE '{end}'
                  AND "타이틀명" IN ({_sqlist(raws)})
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
                  AND "프레임 이름" IN ({_sqlist(titles)})
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
                "source": "화면 입력", "by": saved.get("by"), "at": saved.get("at")}
    try:
        import jira_client
        for e in jira_client.fetch_rs_data(brand=brand).values():
            if str(e.get("ticket_key") or "").upper() == tk:
                return {"agency": e.get("rs_agency"), "mgmt": e.get("rs_mgmt"),
                        "source": "지라", "by": None, "at": None}
    except Exception:
        pass
    return {"agency": None, "mgmt": None, "source": "없음", "by": None, "at": None}


def set_rs(brand: str, ticket: str, agency, mgmt, by: str) -> None:
    from datetime import datetime
    tk = str(ticket or "").strip().upper()
    key = f"{brand}:{tk}"
    now = datetime.now().isoformat(timespec="seconds")

    def _fn(d):
        cur = d.setdefault("rates", {}).get(key) or {}
        cur.update({"agency": agency, "mgmt": mgmt, "by": by, "at": now})
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
    """
    if not titles:
        return 0
    rate = smap._rate_case(rates)
    con = _con()
    try:
        if brand == "photoism":
            raws = _raw_titles(titles, start, end)
            if not raws:
                return 0
            q = f"""
                SELECT CAST(ROUND(SUM(-CAST("최종 결제 금액" AS BIGINT) * {rate}))
                       AS BIGINT) AS v
                FROM read_parquet('{PH_RAW.as_posix()}')
                WHERE TRY_CAST("날짜" AS DATE) BETWEEN DATE '{start}' AND DATE '{end}'
                  AND "타이틀명" IN ({_sqlist(raws)}) {_gubun_filter()}
                  AND CAST("최종 결제 금액" AS BIGINT) < 0"""
        else:
            q = f"""
                SELECT CAST(ROUND(SUM(-(CAST("최종 결제 금액" AS BIGINT)
                       + CAST("쿠폰 할인 금액" AS BIGINT)) * {rate})) AS BIGINT) AS v
                FROM read_parquet('{SN_MASTER.as_posix()}')
                WHERE CAST("날짜" AS DATE) BETWEEN DATE '{start}' AND DATE '{end}'
                  AND "프레임 이름" IN ({_sqlist(titles)})
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
    supply = round(pay / 1.1)
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


# ── 정산액 ────────────────────────────────────────────────────────────────
def settle(detail: pd.DataFrame, rs: float | None) -> pd.DataFrame:
    """국가별 정산액. 요율이 없으면 열을 만들지 않는다(0원으로 오해하면 안 된다)."""
    out = detail.copy()
    out["정산액"] = (out["매출액"] * rs).round().astype("int64") if rs else pd.NA
    return out


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
        ctx["pivots"][brand], ctx["pivots_rev"][brand] = member_pivot(
            brand, titles, start, end, rates)
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
        ctx["tickets"][brand] = smap.lookup_ticket(brand, ticket)
        for _, r in d.iterrows():
            ctx["units"].setdefault(r["국가"], r["unit"])
        pt = price_table(brand, titles, start, end)
        ctx["prices"][brand] = {
            nat: dict(zip(g["형태"], g["단가"])) for nat, g in pt.groupby("국가")
        }
        ctx.setdefault("cancel", {})[brand] = cancel_amount(
            brand, titles, start, end, rates)
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
