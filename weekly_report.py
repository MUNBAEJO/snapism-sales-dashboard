# -*- coding: utf-8 -*-
"""주간 IP 매출 리포트 — 데이터 계층.

매주 사람이 엑셀(`IP 매출 분석_MMDD.xlsx`)로 만들던 리포트를 자동으로 만든다.
**주 단위로 매번 다시 도는 것이 전제다** — 기간만 바꿔 부르면 그 주가 나온다.

## 이 모듈이 엑셀과 다른 점 (전부 대조해서 정한 것, 2026-09-03)

1. **팝업·렌탈 매장 매출을 포함한다**(사용자 지정).
   `ip_classify.IP_GUBUN_SQL` 은 `브랜드='Rentals and pop-ups'` 를 **맨 위**에서
   봐서 IP와 무관하게 '렌탈'로 뺀다(2026-07-28 지정, 대시보드용). 리포트는 그
   매출도 그 IP 것으로 세므로 **분류식을 따로 쓴다** — 그래서 `master_photoism_agg`
   가 아니라 원장에서 직접 뽑는다. 실측: `260505 코르티스` 아티스트 23,390,000 +
   렌탈 1,168,000(밀리오레 팝업부스 등) = 원장 24,558,000 = CMS = 엑셀.

2. **BASIC/ORIGINAL 도 IP까지 쪼갠다**(사용자 지정).
   엑셀의 ORIGINAL 은 TITLE·THEME·FRAME 이 전부 빈 **총액 한 덩어리**다(50줄).
   우리는 프레임 이름이 IP라 나눌 수 있다.

3. **팀(A/C)** — 엑셀은 `구분` 시트 838줄을 손으로 관리한다. 여기서는
   `ip_team_map.json` 예외표 + 규칙으로 정하고, **모르는 건 화면이 물어본다.**
   `team_of` 주석 참고.

## 왜 `master_photoism_agg` 를 안 쓰나
위 1번(렌탈 분류)과 **이름**이다. `agg` 는 `ip_aliases.json` 으로 한/영을 통합해
`260804 SEVENTEEN` 을 `260804 세븐틴` 으로 바꾼다. CMS·엑셀은 원문을 쓰므로
리포트가 원문을 써야 대조가 된다(theme_daily 도 원문이라 180개 타이틀 0원 차이).
"""
from __future__ import annotations

import re
from pathlib import Path

import duckdb
import pandas as pd

import json_store

BASE_DIR = Path(__file__).parent
PH_RAW = BASE_DIR / "data" / "master_photoism.parquet"
SN_RAW = BASE_DIR / "data" / "master.parquet"
THEME_DAILY = BASE_DIR / "data" / "theme_daily.parquet"

# ★팀 예외표는 **프로젝트 루트**에 둔다(`ip_aliases.json`·`sm_artists.json` 과 같은 자리).
#   `data/` 는 .gitignore 라, 거기 두면 541줄을 손으로 채운 것이 clone 때 사라진다.
#   JsonStore 는 `data/` 기준이라 `../` 로 올라간다 — 락·원자적 저장을 그대로 쓰려고
#   따로 만들지 않았다. dev 에서도 같은 파일을 본다(별칭표와 마찬가지로 설정이라 공유).
TEAM_STORE = json_store.JsonStore("../ip_team_map.json", {"teams": {}})

# 리포트가 쓰는 구분. `ip_classify.IP_GUBUN_SQL` 과 **일부러 다르다** — 위 1번 참고.
# ★`NX ` 를 캐릭터로 넣었다(`NX 251120 던전앤파이터`). 본 분류식엔 빠져 있어
#   오리지널로 흘렀는데, 엑셀은 C/BASIC 으로 센다.
GUBUN_SQL = """
CASE
  WHEN "브랜드"='Sticker Machine'                                      THEN '스티커머신'
  WHEN "구좌"='EVENT'                                                  THEN 'PICK'
  WHEN "구좌"='WITH'  AND CAST("타이틀명"   AS VARCHAR) LIKE '렌탈%'      THEN '렌탈'
  WHEN "구좌"='WITH'  AND CAST("타이틀명"   AS VARCHAR) LIKE 'L %'        THEN '캐릭터'
  WHEN "구좌"='WITH'                                                    THEN '아티스트'
  WHEN "구좌"='BASIC' AND CAST("프레임 이름" AS VARCHAR) LIKE 'L %'       THEN '캐릭터'
  WHEN "구좌"='BASIC' AND CAST("프레임 이름" AS VARCHAR) LIKE 'NX %'      THEN '캐릭터'
  WHEN "구좌"='BASIC' AND CAST("프레임 이름" AS VARCHAR) LIKE '라이선스%'   THEN '캐릭터'
  WHEN "구좌"='BASIC' AND CAST("프레임 이름" AS VARCHAR) LIKE 'P %'        THEN '오리지널(포토이즘)'
  WHEN "구좌"='BASIC'                                                    THEN '오리지널(기본)'
  ELSE '제외'
END"""

# IP 이름의 원천: BASIC 은 프레임 이름, 그 밖은 타이틀명 (ip_classify 와 같은 규칙)
IP_SRC_SQL = "CASE WHEN \"구좌\"='BASIC' THEN \"프레임 이름\" ELSE \"타이틀명\" END"

# ★취소 여부가 원장엔 **문자열 'False'** 로 들어 있다(집계본은 BOOLEAN).
#   `= false` 로 걸면 한 건도 안 잡힌다 — 실제로 처음에 전부 0원이 나왔다.
NOT_CANCELLED = "lower(CAST(\"취소 여부\" AS VARCHAR)) NOT IN ('true','1')"

_PREFIX_RE = re.compile(r"^(렌탈|PW|L7|L|P|B|SP|NX)\s+")


# ── 팀(A/C) ───────────────────────────────────────────────────────────────
def load_teams() -> dict:
    """{타이틀: 'A'|'C'} 예외표."""
    return dict((TEAM_STORE.load() or {}).get("teams") or {})


def team_version() -> float:
    """캐시 키용 mtime."""
    return TEAM_STORE.version()


def set_team(title: str, team: str, by: str = "") -> None:
    """예외표에 한 줄 저장. 화면에서 새 IP 를 정할 때 부른다."""
    t = str(title or "").strip()
    g = str(team or "").strip().upper()
    if not t or g not in ("A", "C"):
        return

    def _mut(d):
        d.setdefault("teams", {})[t] = g
        d.setdefault("by", {})[t] = by
        return d

    TEAM_STORE.mutate(_mut)


def team_of(title: str, gubun: str = "", teams: dict | None = None) -> tuple[str, str]:
    """타이틀 → (팀, 근거). 팀은 'A'(아티스트) 또는 'C'(캐릭터).

    3단이다. 정답(엑셀 `구분` 시트 등 541개)으로 전수 평가해 **168/170 (98.8%)**.
      ① 예외표      — 사람이 정한 것. 무조건 이긴다.
      ② 구분         — 아티스트→A · 캐릭터/오리지널→C. 정답 133개에 99.2%.
      ③ 접두어       — `L `/`P ` 는 캐릭터 표식이라 C, 그 밖은 A. 28개에 96.4%.

    ★★③ 이 틀리는 건 언제나 같은 모양이다 — **`L `/`P ` 표식 없는 캐릭터 IP**
      (`260213 다크문$` · `PW 260316 체인소맨 극장판 레제편` · `260612 MINITEEN` ·
      `260608 ENCHIN(엔친)`). 그래서 화면이 ③으로 정한 것을 **'확인 필요'로 띄우고**
      사람이 한 번 정하면 예외표에 남는다(`unknown_teams` 참고).
      주간 리포트는 매주 새 IP 가 나오므로 이 되먹임이 없으면 규칙이 늙는다.
    ★PICK·렌탈 구좌는 팀 축과 **직교**해서 ②로는 못 정한다 — 실제로 정답 25개 중
      PICK 3·렌탈 22 가 그랬다. 그때는 ③으로 떨어진다.
    """
    t = str(title or "").strip()
    tm = load_teams() if teams is None else teams
    if t in tm:
        return tm[t], "예외표"
    if gubun == "아티스트":
        return "A", "구분"
    if gubun in ("캐릭터", "오리지널(포토이즘)", "오리지널(기본)"):
        return "C", "구분"
    return ("C", "접두어") if re.match(r"^(L|P|L7|B|NX)\s", t) else ("A", "접두어")


def ip_name(title: str) -> str:
    """접두어와 날짜코드를 뗀 대표 IP명. TOP 표에서 회차를 합칠 때 쓴다."""
    s = _PREFIX_RE.sub("", str(title or "").strip())
    return re.sub(r"^[0-9]{5,8}[_\s]*", "", s).strip()


# ── 집계 ──────────────────────────────────────────────────────────────────
def _duck():
    con = duckdb.connect()
    con.execute("PRAGMA memory_limit='512MB'")
    con.execute("PRAGMA threads=2")
    return con


def photoism_rows(start: str, end: str, ccs: list[str] | None = None) -> pd.DataFrame:
    """그 기간의 (구분 × 구좌 × 타이틀 × 국가) 매출. **현지통화**다.

    ★환산은 여기서 안 한다 — 화면이 환율을 골라 곱한다(엑셀도 `환율` 시트를 따로 둔다).
    """
    cc = ""
    if ccs:
        cc = " AND lower(국가코드) IN (" + ",".join(f"'{c.lower()}'" for c in ccs) + ")"
    con = _duck()
    try:
        return con.execute(f"""
            SELECT {GUBUN_SQL} AS 구분, "구좌",
                   trim(CAST({IP_SRC_SQL} AS VARCHAR)) AS 타이틀,
                   lower(국가코드) AS cc, "결제 단위" AS unit,
                   CAST(SUM(CAST("최종 결제 금액" AS BIGINT)) AS BIGINT) AS 현지,
                   CAST(COUNT(*) AS BIGINT) AS 건수
            FROM read_parquet('{PH_RAW.as_posix()}')
            WHERE CAST(날짜 AS VARCHAR) BETWEEN '{start}' AND '{end}'
              AND {NOT_CANCELLED}{cc}
            GROUP BY 1, 2, 3, 4, 5
            HAVING SUM(CAST("최종 결제 금액" AS BIGINT)) <> 0
        """).df()
    finally:
        con.close()


def snapism_rows(start: str, end: str) -> pd.DataFrame:
    """스내피즘: (상품 카테고리 × IP × 국가). 구분은 `카테고리` 열을 그대로 쓴다.

    ★IP 는 반드시 `프레임 이름` 이다 — `IP 이름` 열은 2026-08 기준 97% 가 비어 있다
      (`settlement_map.SNAPISM_SETTLE_SQL` 의 같은 주석 참고).
    ★엑셀은 커스텀 상품(`포토카드(커스텀)`·`스티커(커스텀)`)을 뺀다 — IP 가 없어서다.
      프레임 이름이 비면 빠지므로 같은 결과가 된다(2026년 15,205건 확인).
    """
    con = _duck()
    try:
        return con.execute(f"""
            SELECT COALESCE(NULLIF(trim(CAST("카테고리" AS VARCHAR)), ''), '기타') AS 기획전,
                   COALESCE(NULLIF(trim(CAST("상품 카테고리" AS VARCHAR)), ''), '기타') AS 상품,
                   trim(CAST("프레임 이름" AS VARCHAR)) AS 타이틀,
                   국가, "결제 단위" AS unit,
                   CAST(SUM(CAST("최종 결제 금액" AS BIGINT)) AS BIGINT) AS 현지,
                   CAST(COUNT(*) AS BIGINT) AS 건수
            FROM read_parquet('{SN_RAW.as_posix()}')
            WHERE CAST(날짜 AS VARCHAR) BETWEEN '{start}' AND '{end}'
              AND COALESCE(TRIM(CAST("프레임 이름" AS VARCHAR)), '') <> ''
              AND lower(CAST("취소 여부" AS VARCHAR)) NOT IN ('true','1')
            GROUP BY 1, 2, 3, 4, 5
            HAVING SUM(CAST("최종 결제 금액" AS BIGINT)) <> 0
        """).df()
    finally:
        con.close()


def photoism_detail(start: str, end: str, ccs: list[str] | None = None,
                    split_sm: bool = True) -> pd.DataFrame:
    """리포트의 본 표. 열: 구분 · 구좌 · 타이틀 · 표시IP · cc · 현지 · 건수

    ★★출처를 둘로 나눈다 — 실측한 커버리지대로다(2026-08-24~30 KR):
        WITH·EVENT(픽)·렌탈 → `theme_daily` (타이틀 114/114 · 85/85 · 14/14 · 42/42)
        BASIC               → 원장          (theme_daily 에 0/57 · 0/43 · 3/21)
      엑셀도 시트를 이렇게 나눠 둔다(`KR_금주` = 테마 단위 export · `B O_금주` = BASIC).
    ★theme_daily 를 쓰는 이유는 **CMS 원문 그대로**라서다 — 금액도 이름도 엑셀과 같다
      (180개 타이틀 0원 차이). 집계본(`agg`)은 별칭으로 이름을 바꿔 대조가 안 된다.
    ★`split_sm` — SM 은 우리가 `260825 SM ent` 한 덩어리로 갖고 있는데 엑셀은
      아티스트별로 나눈다. 테마 이름이 곧 아티스트라(`260624_라이즈`) `sm_artists`
      로 갈라 준다. 검증: 8명 전원 0원 차이 · 미매칭 테마 0개.
      지점별로 쪼개진 것(`성수중앙_NCT 127`)도 같은 아티스트로 합쳐진다.
    """
    con = _duck()
    try:
        cc_th = ""
        cc_raw = ""
        if ccs:
            lst = ",".join(f"'{c.lower()}'" for c in ccs)
            cc_th = f" AND lower(국가코드) IN ({lst})"
            cc_raw = f" AND lower(국가코드) IN ({lst})"
        # ① 타이틀 → (구분, 구좌). 원장이 정한다 — theme_daily 엔 구좌가 없다.
        gm = con.execute(f"""
            SELECT trim(CAST("타이틀명" AS VARCHAR)) AS 타이틀,
                   any_value({GUBUN_SQL}) AS 구분, any_value("구좌") AS 구좌
            FROM read_parquet('{PH_RAW.as_posix()}')
            WHERE CAST(날짜 AS VARCHAR) BETWEEN '{start}' AND '{end}'
              AND {NOT_CANCELLED} AND "구좌" <> 'BASIC'{cc_raw}
            GROUP BY 1
        """).df()
        th = con.execute(f"""
            SELECT 타이틀, 테마, 프레임, lower(국가코드) AS cc,
                   CAST(SUM(최종결제금액) AS BIGINT) AS 현지,
                   CAST(SUM(주문수) AS BIGINT) AS 건수
            FROM read_parquet('{THEME_DAILY.as_posix()}')
            WHERE CAST(날짜 AS VARCHAR) BETWEEN '{start}' AND '{end}'{cc_th}
            GROUP BY 1, 2, 3, 4
        """).df()
        # ② BASIC 은 원장에서. 프레임 이름이 곧 IP라 테마 축이 없다.
        ba = con.execute(f"""
            SELECT {GUBUN_SQL} AS 구분, '' AS 테마,
                   trim(CAST("프레임 이름" AS VARCHAR)) AS 타이틀,
                   lower(국가코드) AS cc,
                   CAST(SUM(CAST("최종 결제 금액" AS BIGINT)) AS BIGINT) AS 현지,
                   CAST(COUNT(*) AS BIGINT) AS 건수
            FROM read_parquet('{PH_RAW.as_posix()}')
            WHERE CAST(날짜 AS VARCHAR) BETWEEN '{start}' AND '{end}'
              AND {NOT_CANCELLED} AND "구좌" = 'BASIC'{cc_raw}
            GROUP BY 1, 2, 3, 4
            HAVING SUM(CAST("최종 결제 금액" AS BIGINT)) <> 0
        """).df()
    finally:
        con.close()

    th = th.merge(gm, on="타이틀", how="inner")      # 구좌를 못 찾은 건 BASIC 쪽에 있다
    th["표시IP"] = th["타이틀"]
    if split_sm and not th.empty:
        import sm_artists as sa
        arts = sa.load()
        if arts:
            def _disp(r):
                a = sa.match_theme(r["테마"], artists=arts)
                return a["name"] if a else r["타이틀"]
            _sm = th["타이틀"].str.contains("SM ent", na=False)
            th.loc[_sm, "표시IP"] = th.loc[_sm].apply(_disp, axis=1)
    ba["구좌"] = "BASIC"
    ba["프레임"] = ba["타이틀"]
    ba["표시IP"] = ba["타이틀"]
    cols = ["구분", "구좌", "타이틀", "표시IP", "테마", "프레임", "cc", "현지", "건수"]
    out = pd.concat([th.reindex(columns=cols), ba.reindex(columns=cols)],
                    ignore_index=True)
    return out[~out["구분"].isin(["제외", "스티커머신"])].reset_index(drop=True)


def cc_units(start: str, end: str) -> dict:
    """{국가코드(소문자): 결제 통화}. **원장이 말하는 대로** 쓴다.

    ★★손으로 적은 국가→통화 표를 쓰다가 10개국을 통째로 놓쳤다(2026-09-03).
      2026-08-24~30 기준 `ca`·`mx`·`mo`·`ae`·`sg`·`gu`·`gb`·`es` 등 8,800건(1.89%)이
      환율을 못 찾아 합계에서 빠졌다. `cn` 은 원장이 **CNY** 인데 표에는 CNH 로
      적혀 있기까지 했다. 나라가 늘 때마다 표를 고쳐야 하는 구조 자체가 틀렸다.
    ★환율표에는 25개 통화가 다 있었다 — 없던 건 **우리 표**였다.
      이 함수로 바꾸면 그 주 30개국이 전부 환산된다(못 찾는 나라 0개).
    """
    con = _duck()
    try:
        d = con.execute(f"""
            SELECT lower(국가코드) AS cc,
                   any_value(UPPER(TRIM(CAST("결제 단위" AS VARCHAR)))) AS unit
            FROM read_parquet('{PH_RAW.as_posix()}')
            WHERE CAST(날짜 AS VARCHAR) BETWEEN '{start}' AND '{end}'
              AND {NOT_CANCELLED}
            GROUP BY 1
        """).df()
    finally:
        con.close()
    return dict(zip(d["cc"], d["unit"]))


def unknown_teams(start: str, end: str, teams: dict | None = None) -> pd.DataFrame:
    """그 주에 **팀을 규칙으로 짐작한** 타이틀 목록. 화면이 확인을 받는다.

    ★짐작이 맞을 때가 대부분이라 전부 물으면 지친다 — **③접두어로 정한 것만** 낸다.
      ②구분(아티스트/캐릭터)은 정답률 99.2% 라 굳이 안 묻는다.
    """
    tm = load_teams() if teams is None else teams
    d = photoism_rows(start, end)
    if d.empty:
        return pd.DataFrame(columns=["타이틀", "구분", "팀", "현지"])
    d = d[~d["구분"].isin(["제외", "스티커머신"])]
    g = (d.groupby(["타이틀", "구분"], as_index=False)
           .agg(현지=("현지", "sum")))
    g[["팀", "근거"]] = g.apply(
        lambda r: pd.Series(team_of(r["타이틀"], r["구분"], tm)), axis=1)
    return (g[g["근거"] == "접두어"]
            .sort_values("현지", ascending=False)
            .reset_index(drop=True))
