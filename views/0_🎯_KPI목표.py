"""
KPI 목표 달성률 대시보드  (5탭 버전)
  탭1 📊 전체     — TTL/국내/해외 월별 목표·실적·달성률 (게이지·추이)
  탭2 👥 팀별     — A팀/C팀/오리지널 달성률 + 누적 요약
  탭3 📅 주차별   — A팀·C팀 시트 주차별 실적 합계 추이
  탭4 📈 25 vs 26 — YoY 전년비교 + 누적 비교 테이블
  탭5 🏷 브랜드   — 팀별 포토이즘 브랜드 목표·실적 상세

IPX MASTER DATA.xlsx 업로드 시 자동 파싱:
  REPORT 시트  → kpi_targets.csv / kpi_actuals.csv / kpi_yoy.csv / kpi_brand.csv
  A팀·C팀 시트  → kpi_weekly.csv
"""
import io, json, calendar, re
import streamlit as st
import pandas as pd
import pyarrow.parquet as pq
import plotly.graph_objects as go
import plotly.express as px
from pathlib import Path
from datetime import date

# set_page_config 는 라우터(스내피즘.py)에서 처리
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from guide_content import render_guide

st.markdown("""
<style>
/* TV 최적화: 메트릭 카드 크게 */
[data-testid="metric-container"] {
    background: #f8f9fa; border: 1px solid #e9ecef;
    border-radius: 12px; padding: 16px 24px;
}
[data-testid="stMetricLabel"]  { font-size: 1.05rem !important; font-weight: 600 !important; }
[data-testid="stMetricValue"]  { font-size: 2.1rem  !important; font-weight: 700 !important; color: #1a1a2e !important; }
[data-testid="stMetricDelta"]  { font-size: 0.95rem !important; }
.section-title { font-size: 1.15rem; font-weight: 700; margin-bottom: 6px; color: #1a1a2e; }
[data-testid="stDeployButton"] { display: none !important; }
/* 탭 글씨 크게 */
button[data-baseweb="tab"] p { font-size: 1.0rem !important; font-weight: 600 !important; }
/* 사이드바 헤더/아이콘은 라우터(스내피즘.py) + st.navigation 에서 처리 */
</style>
""", unsafe_allow_html=True)

# ── 경로 상수 ─────────────────────────────────────────────────
BASE_DIR      = Path(__file__).parent.parent
MASTER_FILE   = BASE_DIR / "data" / "master_photoism.csv"
AGG_FILE      = BASE_DIR / "data" / "master_photoism_agg.parquet"   # 7.2 MB 집계
PARQ_FILE     = BASE_DIR / "data" / "master_photoism.parquet"       # 114 MB 전체 (드릴다운용)
KPI_FILE      = BASE_DIR / "data" / "kpi_targets.csv"
ACTUALS_FILE  = BASE_DIR / "data" / "kpi_actuals.csv"
YOY_FILE      = BASE_DIR / "data" / "kpi_yoy.csv"
BRAND_FILE    = BASE_DIR / "data" / "kpi_brand.csv"
WEEKLY_FILE   = BASE_DIR / "data" / "kpi_weekly.csv"
ALIAS_FILE    = BASE_DIR / "data" / "frame_alias.json"
CONFIG_FILE   = BASE_DIR / "config.json"

_COUPON_CC = {"la", "gb", "de", "th", "lv", "mx"}
_COIN_CC   = {"cl", "la", "pe", "gb", "de", "lv", "mx"}


# ══════════════════════════════════════════════════════════════
# 데이터 로드 함수
# ══════════════════════════════════════════════════════════════

def load_exchange_rates():
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            return json.load(f).get("exchange_rates", {"KRW": 1})
    except Exception:
        return {"KRW": 1}


def _calc_revenue_from_cms(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for col in ["최종 결제 금액", "쿠폰 할인 금액"]:
        df[col] = pd.to_numeric(df.get(col, 0), errors="coerce").fillna(0)
    df = df[df["최종 결제 금액"] >= 0]
    ex = load_exchange_rates()
    df["결제 단위"]     = df["결제 단위"].fillna("KRW").astype(str).str.strip()
    df["환율"]          = df["결제 단위"].map(ex).fillna(1)
    df["KRW환산금액"]   = (df["최종 결제 금액"] * df["환율"]).round(0)
    df["쿠폰KRW"]       = (df["쿠폰 할인 금액"] * df["환율"]).round(0)
    df["서비스코인KRW"] = (
        pd.to_numeric(df.get("서비스코인", 0), errors="coerce").fillna(0) * df["환율"]
    ).round(0)
    _cc = df["국가코드"].astype(str).str.lower().fillna("")
    df["매출액"] = (
        df["KRW환산금액"]
        + df["쿠폰KRW"]       * _cc.isin(_COUPON_CC).astype(int)
        + df["서비스코인KRW"] * _cc.isin(_COIN_CC).astype(int)
    )
    monthly = (
        df.groupby(["연도", "월"])["매출액"].sum()
        .reset_index().rename(columns={"매출액": "실제매출"})
    )
    monthly["실제매출"] = monthly["실제매출"].astype(int)
    return monthly


@st.cache_data(ttl=30)
def load_monthly_actual(seg: str = "TTL"):
    """월별 실적 집계. agg parquet(7.2 MB) 우선 → CSV fallback."""
    today     = date.today()
    cur_month = today.month
    cms_monthly = pd.DataFrame()

    # ① agg parquet 우선 (7.2 MB → 빠름)
    if AGG_FILE.exists():
        try:
            df = pq.read_table(str(AGG_FILE)).to_pandas(strings_to_categorical=True)
            df["날짜"] = pd.to_datetime(df["날짜"], errors="coerce").dt.date
            df = df[df["날짜"].notna()]
            df["취소 여부"] = df["취소 여부"].astype(bool)
            df = df[~df["취소 여부"]]
            # seg 필터
            _cc_s = df["국가코드"].astype(str).str.lower().str.strip()
            if seg == "국내":
                df = df[_cc_s == "kr"]
            elif seg == "해외":
                df = df[_cc_s != "kr"]
            df = df.reset_index(drop=True)
            df["연도"] = pd.to_datetime(df["날짜"]).dt.year
            df["월"]   = pd.to_datetime(df["날짜"]).dt.month
            # KRW 환산
            ex = load_exchange_rates()
            df["결제 단위"] = df["결제 단위"].astype(str).str.strip().replace("nan","KRW")
            df["환율"]      = df["결제 단위"].map(ex).fillna(1)
            df["KRW환산금액"]   = (df["최종 결제 금액"] * df["환율"]).round(0)
            df["쿠폰KRW"]       = (df["쿠폰 할인 금액"] * df["환율"]).round(0)
            df["서비스코인KRW"] = (df["서비스코인"]     * df["환율"]).round(0)
            _cc = df["국가코드"].astype(str).str.lower().str.strip()
            df["매출액"] = (
                df["KRW환산금액"]
                + df["쿠폰KRW"]       * _cc.isin(_COUPON_CC).astype(int)
                + df["서비스코인KRW"] * _cc.isin(_COIN_CC).astype(int)
            )
            cms_monthly = (
                df.groupby(["연도","월"])["매출액"].sum()
                .reset_index().rename(columns={"매출액":"실제매출"})
            )
            cms_monthly["실제매출"] = cms_monthly["실제매출"].astype(int)
        except Exception as e:
            cms_monthly = pd.DataFrame()

    # ② fallback: CSV (agg 없을 때만)
    if cms_monthly.empty and MASTER_FILE.exists():
        df = pd.read_csv(MASTER_FILE, encoding="utf-8-sig", low_memory=False)
        df["날짜"]      = pd.to_datetime(df["날짜"], errors="coerce").dt.date
        df              = df[df["날짜"].notna()]
        df["취소 여부"] = df["취소 여부"].astype(str).str.lower().isin(["true","1","yes"])
        df              = df[~df["취소 여부"]]
        df["연도"]      = pd.to_datetime(df["날짜"]).dt.year
        df["월"]        = pd.to_datetime(df["날짜"]).dt.month
        _cc = df["국가코드"].astype(str).str.lower().fillna("")
        if seg == "국내":
            df = df[_cc == "kr"]
        elif seg == "해외":
            df = df[_cc != "kr"]
        cms_monthly = _calc_revenue_from_cms(df)

    excel_monthly = pd.DataFrame()
    if ACTUALS_FILE.exists():
        ex = pd.read_csv(ACTUALS_FILE, encoding="utf-8-sig")
        ex["연도"]    = ex["연도"].astype(int)
        ex["월"]      = ex["월"].astype(int)
        ex["실제매출"] = pd.to_numeric(ex["실제매출"], errors="coerce").fillna(0).astype(int)
        if "구분" in ex.columns:
            ex = ex[ex["구분"] == seg]
        excel_monthly = ex[ex["실제매출"] > 0][["연도", "월", "실제매출"]]

    if cms_monthly.empty and excel_monthly.empty:
        return pd.DataFrame(), "없음"
    if excel_monthly.empty:
        return cms_monthly.sort_values(["연도","월"]).reset_index(drop=True), "📸 포토이즘 CMS"
    if cms_monthly.empty:
        return excel_monthly.sort_values(["연도","월"]).reset_index(drop=True), "📊 IPX 엑셀"

    merged = pd.merge(cms_monthly, excel_monthly, on=["연도","월"], how="outer", suffixes=("_cms","_excel"))
    merged["실제매출"] = merged["실제매출_excel"].where(
        merged["실제매출_excel"].notna() & (merged["실제매출_excel"] > 0),
        merged["실제매출_cms"]
    ).fillna(0).astype(int)
    merged = merged[["연도","월","실제매출"]].sort_values(["연도","월"]).reset_index(drop=True)
    excel_max_m = int(excel_monthly["월"].max())
    label = f"📊 IPX 엑셀 (1~{excel_max_m}월)  +  📸 CMS 실시간 ({cur_month}월~)"
    return merged, label


TEAM_LIST = ["A팀", "C팀", "오리지널"]

@st.cache_data(ttl=30)
def load_team_kpi(year: int) -> pd.DataFrame:
    if not KPI_FILE.exists() or not ACTUALS_FILE.exists():
        return pd.DataFrame()
    tgt = pd.read_csv(KPI_FILE, encoding="utf-8-sig")
    act = pd.read_csv(ACTUALS_FILE, encoding="utf-8-sig")
    if "구분" not in tgt.columns or "구분" not in act.columns:
        return pd.DataFrame()
    for df in [tgt, act]:
        df["연도"] = df["연도"].astype(int)
        df["월"]   = df["월"].astype(int)
    tgt["매출목표"] = pd.to_numeric(tgt["매출목표"], errors="coerce").fillna(0).astype(int)
    act["실제매출"] = pd.to_numeric(act["실제매출"], errors="coerce").fillna(0).astype(int)
    frames = []
    for team in TEAM_LIST:
        t = tgt[(tgt["구분"] == team) & (tgt["연도"] == year)][["월","매출목표"]]
        a = act[(act["구분"] == team) & (act["연도"] == year)][["월","실제매출"]]
        m = pd.merge(t, a, on="월", how="outer").fillna(0)
        m["팀"]    = team
        m["연도"]  = year
        m["달성률"] = m.apply(
            lambda r: round(r["실제매출"]/r["매출목표"]*100, 1) if r["매출목표"] > 0 else None, axis=1
        )
        frames.append(m)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True).sort_values(["팀","월"])


@st.cache_data(ttl=30)
def load_targets(seg: str = "TTL"):
    if not KPI_FILE.exists():
        return pd.DataFrame(columns=["연도","월","매출목표"])
    df = pd.read_csv(KPI_FILE, encoding="utf-8-sig")
    df["연도"]    = df["연도"].astype(int)
    df["월"]      = df["월"].astype(int)
    df["매출목표"] = pd.to_numeric(df["매출목표"], errors="coerce").fillna(0).astype(int)
    if "구분" in df.columns:
        df = df[df["구분"] == seg]
    return df[["연도","월","매출목표"]]


@st.cache_data(ttl=30)
def load_yoy() -> pd.DataFrame:
    if not YOY_FILE.exists():
        return pd.DataFrame()
    df = pd.read_csv(YOY_FILE, encoding="utf-8-sig")
    for c in ["연도","월","실적"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)
    return df


@st.cache_data(ttl=30)
def load_brand() -> pd.DataFrame:
    if not BRAND_FILE.exists():
        return pd.DataFrame()
    df = pd.read_csv(BRAND_FILE, encoding="utf-8-sig")
    df["값"] = pd.to_numeric(df["값"], errors="coerce")
    return df


@st.cache_data(ttl=30)
def load_weekly() -> pd.DataFrame:
    if not WEEKLY_FILE.exists():
        return pd.DataFrame()
    df = pd.read_csv(WEEKLY_FILE, encoding="utf-8-sig")
    df["실적"] = pd.to_numeric(df["실적"], errors="coerce").fillna(0).astype(int)
    return df


@st.cache_data(ttl=60)
def load_ip_agg_data() -> pd.DataFrame:
    """IP 트렌드 탭용: agg parquet(7.2 MB) 기반 KRW 환산 DataFrame.
    타이틀명/국가코드/날짜/건수/KRW 열 포함. 프레임 이름은 없음 (드릴다운은 별도 쿼리).
    """
    if not AGG_FILE.exists():
        return pd.DataFrame()
    df = pq.read_table(str(AGG_FILE)).to_pandas(strings_to_categorical=True)
    df["날짜"] = pd.to_datetime(df["날짜"], errors="coerce").dt.date
    df = df[df["날짜"].notna()]
    df["취소 여부"] = df["취소 여부"].astype(bool)
    df = df[~df["취소 여부"]]

    ex = load_exchange_rates()
    df["결제 단위"] = df["결제 단위"].astype(str).str.strip().replace("nan","KRW")
    df["환율"] = df["결제 단위"].map(ex).fillna(1)

    for col in ["최종 결제 금액", "쿠폰 할인 금액", "서비스코인", "건수"]:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

    df["KRW_순수"] = (df["최종 결제 금액"] * df["환율"]).round(0)
    df["KRW_쿠폰"] = (df["쿠폰 할인 금액"] * df["환율"]).round(0)
    df["KRW_코인"] = (df["서비스코인"]     * df["환율"]).round(0)

    _cc = df["국가코드"].astype(str).str.lower().str.strip()
    df["KRW_총매출"] = (
        df["KRW_순수"]
        + df["KRW_쿠폰"] * _cc.isin(_COUPON_CC).astype(int)
        + df["KRW_코인"] * _cc.isin(_COIN_CC).astype(int)
    )

    df["타이틀명"] = df["타이틀명"].astype(str).str.strip()
    df["국가코드"] = df["국가코드"].astype(str).str.upper()
    df["국가"]     = df["국가"].astype(str)
    df["날짜_dt"]  = pd.to_datetime(df["날짜"])
    return df


@st.cache_data(ttl=60)
def load_ip_drilldown(ip_name: str, start_date, end_date) -> pd.DataFrame:
    """특정 IP + 날짜 범위의 프레임별 상세 데이터.
    DuckDB on master_photoism.parquet (필터 후 소량 로드).
    """
    import duckdb
    if not PARQ_FILE.exists():
        return pd.DataFrame()
    parq = str(PARQ_FILE).replace("\\", "/")
    safe_ip = ip_name.replace("'", "''")
    con = duckdb.connect()
    try:
        arrow = con.execute(f"""
            SELECT
                TRY_CAST("날짜" AS DATE)                                             AS "날짜",
                COALESCE(CAST("프레임 이름" AS VARCHAR), '')                          AS "프레임 이름",
                COALESCE(CAST("결제 단위"  AS VARCHAR), 'KRW')                        AS "결제 단위",
                COALESCE(CAST("국가코드"   AS VARCHAR), '')                           AS "국가코드",
                CAST(COALESCE(TRY_CAST("최종 결제 금액" AS BIGINT), 0) AS BIGINT)     AS "최종 결제 금액",
                CAST(COALESCE(TRY_CAST("쿠폰 할인 금액" AS BIGINT), 0) AS BIGINT)     AS "쿠폰 할인 금액",
                CAST(COALESCE(TRY_CAST("서비스코인"     AS BIGINT), 0) AS BIGINT)     AS "서비스코인"
            FROM read_parquet('{parq}')
            WHERE TRY_CAST("날짜" AS DATE) BETWEEN DATE '{start_date}' AND DATE '{end_date}'
              AND CAST("타이틀명" AS VARCHAR) = '{safe_ip}'
              AND LOWER(CAST("취소 여부" AS VARCHAR)) NOT IN ('true','1','yes')
        """).to_arrow_table()
    finally:
        con.close()

    df = arrow.to_pandas()
    ex = load_exchange_rates()
    df["환율"]     = df["결제 단위"].str.strip().map(ex).fillna(1)
    df["KRW_순수"] = (df["최종 결제 금액"] * df["환율"]).round(0)
    df["KRW_쿠폰"] = (df["쿠폰 할인 금액"] * df["환율"]).round(0)
    df["KRW_코인"] = (df["서비스코인"]     * df["환율"]).round(0)
    _cc = df["국가코드"].str.lower().str.strip()
    df["KRW_총매출"] = (
        df["KRW_순수"]
        + df["KRW_쿠폰"] * _cc.isin(_COUPON_CC).astype(int)
        + df["KRW_코인"] * _cc.isin(_COIN_CC).astype(int)
    )
    # 프레임 alias 적용
    alias = load_frame_alias()
    if alias:
        df["프레임 이름"] = df["프레임 이름"].map(
            lambda x: alias.get(str(x).strip(), str(x).strip())
        )
    return df


@st.cache_data(ttl=60)
def load_photoism_full() -> pd.DataFrame:
    """[레거시] agg parquet이 없을 때 fallback. 가급적 load_ip_agg_data() 사용."""
    if AGG_FILE.exists():
        return load_ip_agg_data()
    if not MASTER_FILE.exists():
        return pd.DataFrame()
    df = pd.read_csv(MASTER_FILE, encoding="utf-8-sig", low_memory=False)
    df["날짜"] = pd.to_datetime(df["날짜"], errors="coerce").dt.date
    df = df[df["날짜"].notna()]
    df["취소 여부"] = df["취소 여부"].astype(str).str.lower().isin(["true","1","yes"])
    df = df[~df["취소 여부"]]
    ex = load_exchange_rates()
    df["결제 단위"] = df["결제 단위"].fillna("KRW").astype(str).str.strip()
    df["환율"] = df["결제 단위"].map(ex).fillna(1)
    for col in ["최종 결제 금액", "쿠폰 할인 금액", "서비스코인"]:
        df[col] = pd.to_numeric(df.get(col, 0), errors="coerce").fillna(0)
    df["KRW_순수"] = (df["최종 결제 금액"] * df["환율"]).round(0)
    df["KRW_쿠폰"] = (df["쿠폰 할인 금액"] * df["환율"]).round(0)
    df["KRW_코인"] = (df["서비스코인"] * df["환율"]).round(0)
    _cc = df["국가코드"].astype(str).str.lower().fillna("")
    df["KRW_총매출"] = (
        df["KRW_순수"]
        + df["KRW_쿠폰"] * _cc.isin(_COUPON_CC).astype(int)
        + df["KRW_코인"] * _cc.isin(_COIN_CC).astype(int)
    )
    df["타이틀명"] = df["타이틀명"].fillna("").astype(str).str.strip()
    df["국가코드"] = df["국가코드"].fillna("").astype(str).str.upper()
    df["국가"]     = df["국가"].fillna("").astype(str)
    df["날짜_dt"]  = pd.to_datetime(df["날짜"])
    df["건수"]     = 1  # 개별 행이라 건수=1
    alias = load_frame_alias()
    if alias:
        df["프레임 이름"] = df["프레임 이름"].map(lambda x: alias.get(str(x).strip(), str(x).strip()))
    return df


def fmt_krw(n: float) -> str:
    return f"₩{int(n):,}"


# ── 프레임 alias 관리 ─────────────────────────────────────────
def load_frame_alias() -> dict:
    """frame_alias.json 로드 (comment 키 제외)"""
    if not ALIAS_FILE.exists():
        return {}
    try:
        with open(ALIAS_FILE, encoding="utf-8") as f:
            raw = json.load(f)
        return {k: v for k, v in raw.items() if not k.startswith("_")}
    except Exception:
        return {}


def save_frame_alias(alias: dict):
    """frame_alias.json 저장 (comment 보존)"""
    existing = {}
    if ALIAS_FILE.exists():
        try:
            with open(ALIAS_FILE, encoding="utf-8") as f:
                existing = json.load(f)
        except Exception:
            pass
    # _comment 보존, 나머지 교체
    out = {k: v for k, v in existing.items() if k.startswith("_")}
    out.update(alias)
    with open(ALIAS_FILE, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)


# ══════════════════════════════════════════════════════════════
# 엑셀 파서 유틸
# ══════════════════════════════════════════════════════════════

def _find_month_cols(raw: pd.DataFrame) -> list:
    for _, row in raw.iterrows():
        vals = [str(v).strip() for v in row]
        if "1월" in vals and "12월" in vals:
            cols = []
            for m in range(1, 13):
                for ci, v in enumerate(vals):
                    if v == f"{m}월":
                        cols.append(ci)
                        break
            if cols:
                return cols
    return list(range(3, 15))


def _extract_monthly_vals(row, month_cols: list) -> list:
    vals = []
    for ci in month_cols[:12]:
        v = row.iloc[ci] if ci < len(row) else None
        try:
            vals.append(int(float(v)) if pd.notna(v) and str(v).strip() not in ("","nan") else 0)
        except Exception:
            vals.append(0)
    return vals


def parse_report_targets(file, segment: str, year: int) -> pd.DataFrame:
    raw = pd.read_excel(file, sheet_name="REPORT", header=None, engine="openpyxl")
    month_cols = _find_month_cols(raw)
    target_row = None
    for _, row in raw.iterrows():
        if str(row.iloc[1]).strip() == segment and str(row.iloc[2]).strip() == "목표":
            target_row = row; break
    if target_row is None:
        raise ValueError(f"'{segment} 목표' 행을 찾지 못했습니다.")
    vals = _extract_monthly_vals(target_row, month_cols)
    while len(vals) < 12: vals.append(0)
    return pd.DataFrame({"연도":[year]*12, "월":list(range(1,13)), "매출목표":vals[:12]})


def parse_report_actuals(file, segment: str, year: int) -> pd.DataFrame:
    raw = pd.read_excel(file, sheet_name="REPORT", header=None, engine="openpyxl")
    month_cols = _find_month_cols(raw)
    target_idx = None
    for idx, row in raw.iterrows():
        if str(row.iloc[1]).strip() == segment and str(row.iloc[2]).strip() == "목표":
            target_idx = idx; break
    if target_idx is None:
        raise ValueError(f"'{segment} 목표' 행을 찾지 못했습니다.")
    actual_row = None
    for idx in range(target_idx+1, target_idx+6):
        if idx >= len(raw): break
        r = raw.iloc[idx]
        if str(r.iloc[2]).strip() == "실적":
            actual_row = r; break
    if actual_row is None:
        raise ValueError(f"'{segment} 실적' 행을 찾지 못했습니다.")
    vals = []
    for ci in month_cols[:12]:
        v = actual_row.iloc[ci] if ci < len(actual_row) else 0
        try:
            fv = float(v) if pd.notna(v) and str(v).strip() not in ("","nan") else 0.0
            vals.append(int(fv) if fv > 0 else 0)
        except Exception:
            vals.append(0)
    while len(vals) < 12: vals.append(0)
    return pd.DataFrame({"연도":[year]*12, "월":list(range(1,13)), "실제매출":vals[:12]})


def parse_yoy_25(file) -> pd.DataFrame:
    """REPORT 시트 → 2025년 실적 행 파싱
    (1) col[2]=='25년 실적' 행
    (2) '2025 실적' 섹션 안의 A팀/C팀/TTL 실적 행
    """
    raw = pd.read_excel(file, sheet_name="REPORT", header=None, engine="openpyxl")
    month_cols = _find_month_cols(raw)
    rows = []
    seen = set()   # (구분) 중복 방지

    # 전략 1: col[2]=='25년 실적'
    for _, row in raw.iterrows():
        col1 = str(row.iloc[1]).strip() if pd.notna(row.iloc[1]) else ""
        col2 = str(row.iloc[2]).strip() if pd.notna(row.iloc[2]) else ""
        if col2 == "25년 실적":
            label = col1 if col1 not in ("","nan") else "TTL"
            if label in seen: continue
            seen.add(label)
            for i, v in enumerate(_extract_monthly_vals(row, month_cols)):
                rows.append({"구분": label, "연도": 2025, "월": i+1, "실적": v})

    # 전략 2: '2025 실적' 섹션 내 팀별 행
    in_2025 = False
    for _, row in raw.iterrows():
        col1 = str(row.iloc[1]).strip() if pd.notna(row.iloc[1]) else ""
        col2 = str(row.iloc[2]).strip() if pd.notna(row.iloc[2]) else ""
        # 섹션 진입
        if "2025" in col1 and "실적" in col1:
            in_2025 = True
            continue
        # 섹션 탈출 (다른 섹션 헤더 감지)
        if in_2025 and col1 not in ("","nan","A팀","C팀","TTL") and not col1.startswith("2025"):
            in_2025 = False
        if in_2025 and col1 in ("A팀","C팀","TTL") and col2 in ("실적","달성률(M)"):
            if col2 == "실적" and col1 not in seen:
                seen.add(col1)
                for i, v in enumerate(_extract_monthly_vals(row, month_cols)):
                    rows.append({"구분": col1, "연도": 2025, "월": i+1, "실적": v})

    return pd.DataFrame(rows) if rows else pd.DataFrame(columns=["구분","연도","월","실적"])


def parse_brand_detail(file, year: int = 2026) -> pd.DataFrame:
    """REPORT 시트 → 팀별 브랜드 목표·실적·달성률 파싱"""
    raw = pd.read_excel(file, sheet_name="REPORT", header=None, engine="openpyxl")
    month_cols = _find_month_cols(raw)

    BRANDS = [
        "포토이즘_스탠다드","포토이즘_WITH","포토이즘_BASIC","포토이즘_철수",
        "포토이즘_ BASIC","포토이즘_ ORIGINAL","포토이즘_ WITH","포토이즘_ 스탠다드",
    ]
    ROW_TYPES = ["목표","실적","달성률","달성률(M)"]

    rows = []
    current_team = None

    for _, row in raw.iterrows():
        col1 = str(row.iloc[1]).strip() if pd.notna(row.iloc[1]) else ""
        col2 = str(row.iloc[2]).strip() if pd.notna(row.iloc[2]) else ""

        # 팀 헤더 감지
        if "브랜드별" in col1:
            if "A팀" in col1: current_team = "A팀"
            elif "C팀" in col1: current_team = "C팀"
        elif col1 == "A팀" and col2 in ROW_TYPES:
            current_team = "A팀"
        elif col1 == "C팀" and col2 in ROW_TYPES:
            current_team = "C팀"

        # 브랜드 행 감지
        col1_n = col1.replace(" ","").lower()
        is_brand = any(col1_n == b.replace(" ","").lower() for b in BRANDS)

        if is_brand and col2 in ROW_TYPES:
            vals = []
            for ci in month_cols[:12]:
                v = row.iloc[ci] if ci < len(row) else None
                try:
                    vals.append(float(v) if pd.notna(v) and str(v).strip() not in ("","nan") else None)
                except Exception:
                    vals.append(None)
            for i, v in enumerate(vals):
                rows.append({
                    "팀": current_team or "?",
                    "브랜드": col1.strip(),
                    "행타입": col2,
                    "월": i + 1,
                    "값": v,
                    "연도": year,
                })

    return pd.DataFrame(rows) if rows else pd.DataFrame()


def parse_weekly_data(file) -> pd.DataFrame:
    """A팀·C팀 시트 → 주차별 합계 실적 파싱"""
    week_pat = re.compile(r"^\d{1,2}/\d{1,2}[-~]\d{1,2}/\d{1,2}$")
    all_rows = []

    for team_sheet in ["A팀", "C팀"]:
        try:
            raw = pd.read_excel(file, sheet_name=team_sheet, header=None, engine="openpyxl")
        except Exception:
            continue
        if len(raw) < 4:
            continue

        # row index 2 = 주차 날짜 헤더
        week_header = raw.iloc[2]
        week_cols = []
        for ci, val in enumerate(week_header):
            v = str(val).strip()
            if week_pat.match(v):
                week_cols.append({"ci": ci, "label": v})

        if not week_cols:
            continue

        # 월 그룹 헤더 (row 1) — 주차가 몇 월인지 매핑
        month_header = raw.iloc[1]

        data = raw.iloc[3:]   # 실데이터 rows
        for wc in week_cols:
            ci = wc["ci"]
            if ci >= data.shape[1]:
                continue
            col_vals = pd.to_numeric(data.iloc[:, ci], errors="coerce").fillna(0)
            total = int(col_vals.sum())

            # 월 레이블 추출 (col1 헤더에서)
            month_label = str(month_header.iloc[ci]).strip() if ci < len(month_header) else ""
            if month_label in ("","nan"): month_label = ""

            all_rows.append({
                "팀":     team_sheet,
                "주차":   wc["label"],
                "월그룹": month_label,
                "실적":   total,
            })

    return pd.DataFrame(all_rows) if all_rows else pd.DataFrame()


# ══════════════════════════════════════════════════════════════
# 사이드바
# ══════════════════════════════════════════════════════════════
st.sidebar.header("📂 파일 관리")

col_yr, col_seg = st.sidebar.columns([1, 2])
target_year = col_yr.number_input("연도", min_value=2024, max_value=2030, value=2026, step=1)
seg_choice  = col_seg.radio("목표 기준", ["TTL (국내+해외)", "국내", "해외"], index=0)
SEG_MAP = {"TTL (국내+해외)": "TTL", "국내": "국내", "해외": "해외"}

st.sidebar.caption("**IPX MASTER DATA.xlsx** 업로드 시 REPORT·A팀·C팀 시트 자동 파싱")
uploaded = st.sidebar.file_uploader("파일 업로드 (.xlsx / .csv)", type=["xlsx", "csv"])

if uploaded:
    ext = Path(uploaded.name).suffix.lower()
    yr  = int(target_year)
    try:
        if ext == ".xlsx":
            # ── 기존 파싱: 목표·실적 (TTL/국내/해외/A팀/C팀/오리지널) ──
            ALL_SEGS = ["TTL", "국내", "해외", "A팀", "C팀", "오리지널"]
            tgt_frames, act_frames = [], []

            uploaded_bytes = uploaded.read()
            buf = io.BytesIO(uploaded_bytes)

            for seg_key in ALL_SEGS:
                try:
                    buf.seek(0)
                    t = parse_report_targets(buf, seg_key, yr)
                    t["구분"] = seg_key
                    tgt_frames.append(t)
                except Exception:
                    pass
                try:
                    buf.seek(0)
                    a = parse_report_actuals(buf, seg_key, yr)
                    a["구분"] = seg_key
                    act_frames.append(a)
                except Exception:
                    pass

            if tgt_frames:
                pd.concat(tgt_frames, ignore_index=True).to_csv(KPI_FILE, index=False, encoding="utf-8-sig")
            if act_frames:
                pd.concat(act_frames, ignore_index=True).to_csv(ACTUALS_FILE, index=False, encoding="utf-8-sig")

            parsed_segs = [f["구분"].iloc[0] for f in tgt_frames]
            st.sidebar.success(f"✅ 목표·실적 파싱: {', '.join(parsed_segs)}")

            # ── YoY 파싱 ──────────────────────────────────────
            try:
                buf.seek(0)
                yoy_df = parse_yoy_25(buf)
                if not yoy_df.empty:
                    yoy_df.to_csv(YOY_FILE, index=False, encoding="utf-8-sig")
                    st.sidebar.success(f"✅ 25년 YoY 파싱: {yoy_df['구분'].unique().tolist()}")
            except Exception as e:
                st.sidebar.warning(f"YoY 파싱 오류: {e}")

            # ── 브랜드 파싱 ────────────────────────────────────
            try:
                buf.seek(0)
                brand_df = parse_brand_detail(buf, yr)
                if not brand_df.empty:
                    brand_df.to_csv(BRAND_FILE, index=False, encoding="utf-8-sig")
                    brands_found = brand_df["브랜드"].unique().tolist()
                    st.sidebar.success(f"✅ 브랜드 파싱: {', '.join(brands_found[:4])}{'...' if len(brands_found)>4 else ''}")
            except Exception as e:
                st.sidebar.warning(f"브랜드 파싱 오류: {e}")

            # ── 주차별 파싱 ────────────────────────────────────
            try:
                buf.seek(0)
                weekly_df = parse_weekly_data(buf)
                if not weekly_df.empty:
                    weekly_df.to_csv(WEEKLY_FILE, index=False, encoding="utf-8-sig")
                    w_cnt = weekly_df.groupby("팀")["주차"].count().to_dict()
                    st.sidebar.success(f"✅ 주차별 파싱: {w_cnt}")
            except Exception as e:
                st.sidebar.warning(f"주차별 파싱 오류: {e}")

            # 미리보기 (현재 seg)
            cur_seg = SEG_MAP[seg_choice]
            tgt_cur = next((t for t in tgt_frames if t["구분"].iloc[0] == cur_seg), pd.DataFrame())
            act_cur = next((a for a in act_frames if a["구분"].iloc[0] == cur_seg), pd.DataFrame())
            if not tgt_cur.empty and not act_cur.empty:
                preview = pd.merge(
                    tgt_cur.rename(columns={"매출목표":"목표"}),
                    act_cur.rename(columns={"실제매출":"실적"}),
                    on=["연도","월"],
                )
                preview["달성률"] = preview.apply(
                    lambda r: f"{r['실적']/r['목표']*100:.1f}%" if r["목표"]>0 else "—", axis=1
                )
                preview["목표"] = preview["목표"].apply(lambda x: fmt_krw(x) if x>0 else "—")
                preview["실적"] = preview["실적"].apply(lambda x: fmt_krw(x) if x>0 else "—")
                st.sidebar.caption(f"미리보기: **{cur_seg}**")
                st.sidebar.dataframe(preview[["월","목표","실적","달성률"]], use_container_width=True, height=280)

            st.cache_data.clear()
            st.rerun()

        elif ext == ".csv":
            new_tgt = pd.read_csv(uploaded, encoding="utf-8-sig")
            if not {"연도","월","매출목표"}.issubset(new_tgt.columns):
                st.sidebar.error("필수 컬럼(연도, 월, 매출목표) 누락")
            else:
                new_tgt.to_csv(KPI_FILE, index=False, encoding="utf-8-sig")
                st.sidebar.success("✅ CSV 저장 완료!")
                st.cache_data.clear()
                st.rerun()

    except Exception as e:
        st.sidebar.error(f"오류: {e}")

if ACTUALS_FILE.exists():
    if st.sidebar.button("🗑 엑셀 실적 초기화 (CMS로 전환)"):
        ACTUALS_FILE.unlink()
        st.cache_data.clear()
        st.rerun()

template = pd.DataFrame({"연도":[2026]*12,"월":list(range(1,13)),"매출목표":[0]*12})
st.sidebar.download_button(
    "📥 목표 CSV 템플릿",
    template.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig"),
    "kpi_targets.csv", "text/csv",
)


# ══════════════════════════════════════════════════════════════
# 메인 대시보드
# ══════════════════════════════════════════════════════════════
st.title("🎯 KPI 목표 달성률")
render_guide("kpi")

_seg  = SEG_MAP[seg_choice]
today = date.today()

tab_all, tab_team, tab_weekly, tab_yoy, tab_brand, tab_ip = st.tabs([
    "📊 전체", "👥 팀별", "📅 주차별", "📈 25 vs 26", "🏷 브랜드 상세", "🔥 IP 트렌드"
])


# ════════════════════════════════════════════════════════════
# TAB 1 — 전체 (기존 유지)
# ════════════════════════════════════════════════════════════
with tab_all:
    actual_df, actual_src = load_monthly_actual(_seg)
    targets               = load_targets(_seg)

    st.caption(f"실적 기준: **{actual_src}**  |  목표 기준: **REPORT ({_seg})**  |  새로고침: F5")

    if actual_df.empty:
        st.warning("실적 데이터가 없습니다. 엑셀을 업로드하거나 포토이즘 크롤러를 실행하세요.")
        st.stop()

    has_targets = not targets.empty and (targets["매출목표"] > 0).any()
    if not has_targets:
        st.info("📋 목표가 없습니다. 사이드바에서 **IPX MASTER DATA.xlsx** 를 업로드해주세요.")

    merged = pd.merge(actual_df, targets, on=["연도","월"], how="outer")
    merged["실제매출"] = merged["실제매출"].fillna(0).astype(int)
    merged["매출목표"] = merged["매출목표"].fillna(0).astype(int)
    merged["달성률"]   = merged.apply(
        lambda r: round(r["실제매출"]/r["매출목표"]*100, 1) if r["매출목표"]>0 else None, axis=1
    )
    merged["연월"] = merged["연도"].astype(str) + "-" + merged["월"].apply(lambda x: f"{x:02d}")
    merged = merged.sort_values(["연도","월"]).reset_index(drop=True)

    days_total = calendar.monthrange(today.year, today.month)[1]
    pace_ratio = today.day / days_total
    this_row   = merged[(merged["연도"]==today.year) & (merged["월"]==today.month)]

    if not this_row.empty:
        r           = this_row.iloc[0]
        actual_this = int(r["실제매출"])
        target_this = int(r["매출목표"])
        achieve_pct = r["달성률"]
        pace_target = int(target_this * pace_ratio)
        gap         = actual_this - target_this
        pace_gap    = actual_this - pace_target

        prev_m   = today.month - 1 if today.month > 1 else 12
        prev_y   = today.year if today.month > 1 else today.year - 1
        prev_row = merged[(merged["연도"]==prev_y) & (merged["월"]==prev_m)]
        prev_actual = int(prev_row.iloc[0]["실제매출"]) if not prev_row.empty else 0

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("이번달 목표", fmt_krw(target_this) if target_this>0 else "미설정")
        c2.metric(
            "이번달 실적", fmt_krw(actual_this),
            f"{(actual_this-prev_actual)/prev_actual*100:+.1f}% 전월비" if prev_actual>0 else "",
        )
        c3.metric(
            "목표 달성률",
            f"{achieve_pct:.1f}%" if achieve_pct is not None else "—",
            f"페이스({today.day}/{days_total}일) 대비 {pace_gap/target_this*100:+.1f}%"
            if target_this>0 else "",
        )
        c4.metric(
            "목표 대비 Gap",
            fmt_krw(abs(gap)) if target_this>0 else "—",
            "초과 달성 ✅" if gap>=0 else "미달 ⚠️",
            delta_color="normal" if gap>=0 else "inverse",
        )

        st.divider()
        col_g, col_b = st.columns([4, 6])
        with col_g:
            st.markdown('<div class="section-title">이번달 달성률</div>', unsafe_allow_html=True)
            gv = float(min(achieve_pct or 0, 150))
            fig_g = go.Figure(go.Indicator(
                mode="gauge+number+delta", value=gv,
                number={"suffix":"%","font":{"size":48,"color":"#7209b7"}},
                delta={"reference":100,"suffix":"%"},
                gauge={
                    "axis":{"range":[0,150],"ticksuffix":"%","nticks":7},
                    "bar":{"color":"#7209b7","thickness":0.28},
                    "bgcolor":"white","borderwidth":0,
                    "steps":[
                        {"range":[0,70],"color":"#ffe0e0"},
                        {"range":[70,90],"color":"#fff3cd"},
                        {"range":[90,100],"color":"#d4edda"},
                        {"range":[100,150],"color":"#cce5ff"},
                    ],
                    "threshold":{"line":{"color":"#e74c3c","width":3},"thickness":0.8,"value":100},
                },
                title={"text":f"{today.year}년 {today.month}월  ({today.day}/{days_total}일 경과)","font":{"size":13}},
            ))
            fig_g.update_layout(height=300, margin=dict(t=50,b=0,l=30,r=30))
            st.plotly_chart(fig_g, use_container_width=True)
            st.caption("🔴 0~70%  🟡 70~90%  🟢 90~100%  🔵 100%↑")

        with col_b:
            st.markdown('<div class="section-title">실적 / 페이스 기준 / 월 목표 비교</div>', unsafe_allow_html=True)
            fig_b = go.Figure()
            for lbl, val, col in zip(
                ["이번달 실적", f"페이스 기준\n({today.day}/{days_total}일)", "월 목표"],
                [actual_this, pace_target, target_this],
                ["#7209b7","#adb5bd","#dee2e6"],
            ):
                fig_b.add_trace(go.Bar(
                    x=[lbl], y=[val], marker_color=col,
                    text=[fmt_krw(val)], textposition="outside",
                    hovertemplate=f"{lbl}<br>{fmt_krw(val)}<extra></extra>",
                    showlegend=False,
                ))
            fig_b.update_layout(height=300, yaxis=dict(tickformat=",",title="",showgrid=True),
                                xaxis_title="", margin=dict(t=30,b=0), bargap=0.35)
            st.plotly_chart(fig_b, use_container_width=True)
        st.divider()

    # 월별 추이
    st.markdown('<div class="section-title">월별 목표 vs 실적 추이</div>', unsafe_allow_html=True)
    plot_df = merged[merged["매출목표"]>0].copy()

    if plot_df.empty:
        st.info("목표가 입력된 월이 없습니다. IPX MASTER DATA.xlsx 를 업로드해주세요.")
    else:
        max_pct = plot_df["달성률"].dropna().max() if plot_df["달성률"].notna().any() else 100
        y2_max  = max(160, round(float(max_pct)*1.15, -1))
        fig_m = go.Figure()
        fig_m.add_trace(go.Bar(x=plot_df["연월"], y=plot_df["매출목표"], name="목표", marker_color="#dee2e6",
                               hovertemplate="%{x}<br>목표: %{y:,}원<extra></extra>"))
        fig_m.add_trace(go.Bar(x=plot_df["연월"], y=plot_df["실제매출"], name="실적", marker_color="#7209b7",
                               opacity=0.85, hovertemplate="%{x}<br>실적: %{y:,}원<extra></extra>"))
        fig_m.add_trace(go.Scatter(x=plot_df["연월"], y=plot_df["달성률"], name="달성률", yaxis="y2",
                                   mode="lines+markers+text",
                                   line=dict(color="#f72585",width=2), marker=dict(size=9),
                                   text=plot_df["달성률"].apply(lambda x: f"{x:.0f}%" if x is not None else ""),
                                   textposition="top center", textfont=dict(size=11,color="#f72585"),
                                   hovertemplate="%{x}<br>달성률: %{y:.1f}%<extra></extra>"))
        fig_m.add_trace(go.Scatter(x=[plot_df["연월"].iloc[0], plot_df["연월"].iloc[-1]], y=[100,100],
                                   yaxis="y2", mode="lines",
                                   line=dict(color="#e74c3c",width=1,dash="dash"),
                                   name="목표 100%", hoverinfo="skip"))
        fig_m.update_layout(height=440, barmode="group",
                            yaxis=dict(tickformat=",",title="매출 (KRW)"),
                            yaxis2=dict(title="달성률 (%)",overlaying="y",side="right",
                                        range=[0,y2_max],ticksuffix="%",showgrid=False),
                            legend=dict(orientation="h",y=1.1), margin=dict(t=20,b=0))
        st.plotly_chart(fig_m, use_container_width=True)

        st.markdown('<div class="section-title">월별 요약</div>', unsafe_allow_html=True)
        def status(x):
            if x is None: return "—"
            if x >= 100:  return "✅ 달성"
            if x >= 80:   return "⚠️ 근접"
            return "❌ 미달"
        tbl = plot_df[["연월","매출목표","실제매출","달성률"]].copy().reset_index(drop=True)
        tbl.index = tbl.index + 1
        tbl["상태"] = tbl["달성률"].apply(status)
        tbl["Gap"]  = (plot_df["실제매출"].values - plot_df["매출목표"].values)
        tbl["Gap"]  = tbl["Gap"].apply(lambda x: ("+" if x>=0 else "") + fmt_krw(x))
        tbl["매출목표"] = tbl["매출목표"].apply(fmt_krw)
        tbl["실제매출"] = tbl["실제매출"].apply(fmt_krw)
        tbl["달성률"]   = tbl["달성률"].apply(lambda x: f"{x:.1f}%" if x is not None else "—")
        tbl.columns = ["연월","목표","실적","달성률","상태","Gap"]
        st.dataframe(tbl, use_container_width=True, height=min(480, len(tbl)*40+55))


# ════════════════════════════════════════════════════════════
# TAB 2 — 팀별 (기존 유지)
# ════════════════════════════════════════════════════════════
with tab_team:
    TEAM_COLORS = {"A팀":"#7209b7","C팀":"#f72585","오리지널":"#4cc9f0"}
    team_df = load_team_kpi(int(target_year))

    if team_df.empty:
        st.info("팀별 데이터가 없습니다. 사이드바에서 **IPX MASTER DATA.xlsx** 를 업로드해주세요.")
    else:
        st.markdown(f"#### {today.year}년 {today.month}월 팀별 달성률")
        cur_teams = team_df[team_df["월"]==today.month]
        cols = st.columns(len(TEAM_LIST))
        for i, team in enumerate(TEAM_LIST):
            row = cur_teams[cur_teams["팀"]==team]
            if row.empty: cols[i].metric(team,"—"); continue
            r        = row.iloc[0]
            rate_str = f"{r['달성률']:.1f}%" if r["달성률"] is not None else "—"
            gap_v    = int(r["실제매출"]) - int(r["매출목표"])
            gap_str  = ("+" if gap_v>=0 else "") + fmt_krw(gap_v) if r["매출목표"]>0 else ""
            cols[i].metric(team, rate_str,
                           f"실적 {fmt_krw(r['실제매출'])}  /  {gap_str}",
                           delta_color="normal" if gap_v>=0 else "inverse")
        st.divider()

        st.markdown('<div class="section-title">월별 팀별 달성률 추이</div>', unsafe_allow_html=True)
        plot_t = team_df[team_df["달성률"].notna() & (team_df["매출목표"]>0)].copy()
        plot_t["월_label"] = plot_t["월"].apply(lambda x: f"{x}월")
        fig_t = go.Figure()
        for team in TEAM_LIST:
            td = plot_t[plot_t["팀"]==team]
            fig_t.add_trace(go.Bar(x=td["월_label"], y=td["달성률"], name=team,
                                   marker_color=TEAM_COLORS.get(team,"#adb5bd"),
                                   text=td["달성률"].apply(lambda x: f"{x:.0f}%"),
                                   textposition="outside",
                                   hovertemplate=f"{team}<br>%{{x}}<br>달성률: %{{y:.1f}}%<extra></extra>"))
        fig_t.add_hline(y=100, line_dash="dash", line_color="#e74c3c",
                        annotation_text="100%", annotation_position="right")
        fig_t.update_layout(height=400, barmode="group",
                            yaxis=dict(ticksuffix="%",title="달성률"),
                            xaxis_title="", legend=dict(orientation="h",y=1.1),
                            margin=dict(t=20,b=0))
        st.plotly_chart(fig_t, use_container_width=True)

        st.markdown('<div class="section-title">팀별 누적 요약</div>', unsafe_allow_html=True)
        summary_rows = []
        for team in TEAM_LIST:
            td   = team_df[team_df["팀"]==team]
            ytd  = td[td["실제매출"]>0]
            tot_tgt = int(ytd["매출목표"].sum())
            tot_act = int(ytd["실제매출"].sum())
            rate    = round(tot_act/tot_tgt*100, 1) if tot_tgt>0 else None
            summary_rows.append({
                "팀": team, "누적 목표": fmt_krw(tot_tgt),
                "누적 실적": fmt_krw(tot_act),
                "달성률":   f"{rate:.1f}%" if rate is not None else "—",
                "상태":     ("✅ 달성" if rate>=100 else ("⚠️ 근접" if rate>=80 else "❌ 미달")) if rate is not None else "—",
            })
        st.dataframe(pd.DataFrame(summary_rows).set_index("팀"), use_container_width=True, height=175)


# ════════════════════════════════════════════════════════════
# TAB 3 — 주차별
# ════════════════════════════════════════════════════════════
with tab_weekly:
    weekly_df = load_weekly()

    if weekly_df.empty:
        st.info("주차별 데이터가 없습니다. 사이드바에서 **IPX MASTER DATA.xlsx** 를 업로드해주세요.")
    else:
        team_w = st.radio("팀", ["전체 (A+C)", "A팀", "C팀"], horizontal=True, key="weekly_team")

        if team_w == "전체 (A+C)":
            plot_w = weekly_df.groupby("주차", as_index=False)["실적"].sum()
            plot_w["팀"] = "전체"
        else:
            plot_w = weekly_df[weekly_df["팀"] == team_w].copy()

        # 실적 있는 주차만
        active_w = plot_w[plot_w["실적"] > 0]

        # KPI 카드: 이번주 / 지난주 / 전주대비
        if len(active_w) >= 2:
            prev_w_val = int(active_w.iloc[-2]["실적"])
            curr_w_val = int(active_w.iloc[-1]["실적"])
            curr_w_lbl = active_w.iloc[-1]["주차"]
            prev_w_lbl = active_w.iloc[-2]["주차"]
            wow = (curr_w_val - prev_w_val) / prev_w_val * 100 if prev_w_val > 0 else 0

            c1, c2, c3, c4 = st.columns(4)
            c1.metric(f"최근 주 ({curr_w_lbl})", fmt_krw(curr_w_val), f"전주 대비 {wow:+.1f}%",
                      delta_color="normal" if wow >= 0 else "inverse")
            c2.metric(f"전주 ({prev_w_lbl})", fmt_krw(prev_w_val))
            c3.metric("전주 대비", f"{wow:+.1f}%",
                      "↑ 증가" if wow >= 0 else "↓ 감소",
                      delta_color="normal" if wow >= 0 else "inverse")
            c4.metric("누적 합계", fmt_krw(active_w["실적"].sum()))
            st.divider()

        # 주차별 바 차트
        st.markdown('<div class="section-title">주차별 실적 추이</div>', unsafe_allow_html=True)
        fig_w = go.Figure()

        if team_w == "전체 (A+C)":
            for team, color in [("A팀","#7209b7"),("C팀","#f72585")]:
                td = weekly_df[weekly_df["팀"] == team]
                fig_w.add_trace(go.Bar(
                    x=td["주차"], y=td["실적"], name=team,
                    marker_color=color,
                    hovertemplate=f"{team} %{{x}}<br>%{{y:,}}원<extra></extra>",
                ))
            fig_w.update_layout(barmode="stack")
        else:
            fig_w.add_trace(go.Bar(
                x=plot_w["주차"], y=plot_w["실적"],
                marker_color="#7209b7",
                text=plot_w["실적"].apply(lambda x: fmt_krw(x) if x > 0 else ""),
                textposition="outside",
                hovertemplate="%{x}<br>%{y:,}원<extra></extra>",
            ))

        # 7주 이동평균
        if len(active_w) >= 3:
            all_weeks = plot_w if team_w != "전체 (A+C)" else \
                weekly_df.groupby("주차", as_index=False)["실적"].sum()
            ma = all_weeks["실적"].rolling(3, min_periods=1).mean()
            fig_w.add_trace(go.Scatter(
                x=all_weeks["주차"], y=ma, name="3주 이동평균",
                mode="lines", line=dict(color="#f4a261", width=2, dash="dot"),
                hovertemplate="%{x}<br>이동평균: %{y:,.0f}원<extra></extra>",
            ))

        fig_w.update_layout(
            height=460,
            yaxis=dict(tickformat=",", title="실적 (KRW)"),
            xaxis=dict(title="", tickangle=-45, tickfont=dict(size=10)),
            legend=dict(orientation="h", y=1.05),
            margin=dict(t=30, b=70),
        )
        st.plotly_chart(fig_w, use_container_width=True)

        # 월별 주차 요약 테이블
        if "월그룹" in weekly_df.columns:
            st.markdown('<div class="section-title">월별 주차 합계</div>', unsafe_allow_html=True)
            if team_w == "전체 (A+C)":
                monthly_w = weekly_df.groupby("월그룹", as_index=False)["실적"].sum()
            else:
                monthly_w = weekly_df[weekly_df["팀"]==team_w].groupby("월그룹", as_index=False)["실적"].sum()
            monthly_w = monthly_w[monthly_w["실적"] > 0]
            if not monthly_w.empty:
                monthly_w["실적_표시"] = monthly_w["실적"].apply(fmt_krw)
                st.dataframe(monthly_w[["월그룹","실적_표시"]].rename(columns={"월그룹":"월","실적_표시":"실적 합계"}),
                             use_container_width=True, hide_index=True, height=300)


# ════════════════════════════════════════════════════════════
# TAB 4 — 25 vs 26 YoY 비교
# ════════════════════════════════════════════════════════════
with tab_yoy:
    yoy_df    = load_yoy()
    tgt_26    = load_targets(_seg)
    act_26, _ = load_monthly_actual(_seg)

    if yoy_df.empty:
        st.info("25년 비교 데이터가 없습니다. 사이드바에서 **IPX MASTER DATA.xlsx** 를 업로드해주세요.")
    else:
        avail_groups = sorted(yoy_df["구분"].unique().tolist())
        default_idx  = avail_groups.index("TTL") if "TTL" in avail_groups else 0
        yoy_group    = st.radio("비교 기준", avail_groups, index=default_idx, horizontal=True, key="yoy_group")

        yoy_25 = yoy_df[yoy_df["구분"] == yoy_group][["월","실적"]].rename(columns={"실적":"25년 실적"})

        # 26년 실적
        if not act_26.empty:
            act26 = act_26[["월","실제매출"]].rename(columns={"실제매출":"26년 실적"})
            merged_yoy = pd.merge(yoy_25, act26, on="월", how="outer")
        else:
            merged_yoy = yoy_25.copy(); merged_yoy["26년 실적"] = 0

        # 26년 목표
        if not tgt_26.empty:
            t26 = tgt_26[["월","매출목표"]].rename(columns={"매출목표":"26년 목표"})
            merged_yoy = pd.merge(merged_yoy, t26, on="월", how="outer")

        merged_yoy = merged_yoy.sort_values("월").fillna(0).reset_index(drop=True)
        merged_yoy["월_label"] = merged_yoy["월"].apply(lambda x: f"{x}월")

        # YoY %
        merged_yoy["YoY%"] = merged_yoy.apply(
            lambda r: round((r["26년 실적"] - r["25년 실적"]) / r["25년 실적"] * 100, 1)
            if r.get("25년 실적", 0) > 0 else None, axis=1
        )

        # 누적
        merged_yoy["25년 누적"] = merged_yoy["25년 실적"].cumsum().astype(int)
        merged_yoy["26년 누적"] = merged_yoy["26년 실적"].cumsum().astype(int)

        # KPI 카드
        tot25  = int(merged_yoy["25년 실적"].sum())
        tot26  = int(merged_yoy["26년 실적"].sum())
        tot26t = int(merged_yoy["26년 목표"].sum()) if "26년 목표" in merged_yoy.columns else 0
        yoy_cum = (tot26 - tot25) / tot25 * 100 if tot25 > 0 else 0
        act_rate = tot26 / tot26t * 100 if tot26t > 0 else None

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("25년 누적 실적", fmt_krw(tot25))
        c2.metric("26년 누적 실적", fmt_krw(tot26), f"전년대비 {yoy_cum:+.1f}%",
                  delta_color="normal" if yoy_cum >= 0 else "inverse")
        c3.metric("26년 목표", fmt_krw(tot26t) if tot26t > 0 else "미설정")
        c4.metric("26년 목표 달성률",
                  f"{act_rate:.1f}%" if act_rate is not None else "—",
                  "✅" if act_rate and act_rate >= 100 else ("⚠️" if act_rate and act_rate >= 80 else ""))
        st.divider()

        # 25 vs 26 월별 바 차트
        st.markdown('<div class="section-title">25년 vs 26년 월별 실적 비교</div>', unsafe_allow_html=True)
        fig_yoy = go.Figure()
        fig_yoy.add_trace(go.Bar(x=merged_yoy["월_label"], y=merged_yoy["25년 실적"],
                                  name="25년 실적", marker_color="#adb5bd",
                                  hovertemplate="%{x}<br>25년: %{y:,}원<extra></extra>"))
        fig_yoy.add_trace(go.Bar(x=merged_yoy["월_label"], y=merged_yoy["26년 실적"],
                                  name="26년 실적", marker_color="#7209b7", opacity=0.9,
                                  hovertemplate="%{x}<br>26년: %{y:,}원<extra></extra>"))
        if "26년 목표" in merged_yoy.columns:
            fig_yoy.add_trace(go.Scatter(x=merged_yoy["월_label"], y=merged_yoy["26년 목표"],
                                          name="26년 목표", mode="lines+markers",
                                          line=dict(color="#e74c3c", width=2, dash="dash"),
                                          hovertemplate="%{x}<br>목표: %{y:,}원<extra></extra>"))
        # YoY% 라인
        yoy_valid = merged_yoy[merged_yoy["YoY%"].notna() & (merged_yoy["25년 실적"] > 0)]
        if not yoy_valid.empty:
            fig_yoy.add_trace(go.Scatter(
                x=yoy_valid["월_label"], y=yoy_valid["YoY%"],
                name="YoY %", yaxis="y2", mode="lines+markers+text",
                line=dict(color="#f72585", width=2),
                text=yoy_valid["YoY%"].apply(lambda x: f"{x:+.0f}%"),
                textposition="top center", textfont=dict(size=11, color="#f72585"),
                hovertemplate="%{x}<br>YoY: %{y:+.1f}%<extra></extra>",
            ))
            fig_yoy.add_hline(y=0, line_color="#dee2e6", line_width=1, yref="y2")

        fig_yoy.update_layout(
            height=460, barmode="group",
            yaxis=dict(tickformat=",", title="매출 (KRW)"),
            yaxis2=dict(title="YoY %", overlaying="y", side="right",
                        ticksuffix="%", showgrid=False, zeroline=True, zerolinecolor="#ccc"),
            legend=dict(orientation="h", y=1.08),
            margin=dict(t=20, b=0),
        )
        st.plotly_chart(fig_yoy, use_container_width=True)

        st.divider()

        # 누적 비교 테이블
        st.markdown('<div class="section-title">월별 누적 비교 테이블</div>', unsafe_allow_html=True)
        tbl_yoy = merged_yoy[["월_label","25년 실적","26년 실적","25년 누적","26년 누적"]].copy()
        if "26년 목표" in merged_yoy.columns:
            tbl_yoy.insert(3, "26년 목표", merged_yoy["26년 목표"].astype(int))
        tbl_yoy["YoY (전년대비)"] = merged_yoy["YoY%"].apply(
            lambda x: f"{x:+.1f}%" if x is not None else "—"
        )
        tbl_yoy["누적 YoY"] = merged_yoy.apply(
            lambda r: f"{(r['26년 누적']-r['25년 누적'])/r['25년 누적']*100:+.1f}%"
            if r["25년 누적"] > 0 else "—", axis=1
        )
        # 달성률 컬럼 추가
        if "26년 목표" in tbl_yoy.columns:
            tbl_yoy["달성률"] = merged_yoy.apply(
                lambda r: f"{r['26년 실적']/r['26년 목표']*100:.1f}%" if r.get("26년 목표",0)>0 else "—", axis=1
            )
        # 금액 포맷
        for col in ["25년 실적","26년 실적","25년 누적","26년 누적"]:
            tbl_yoy[col] = tbl_yoy[col].apply(lambda x: fmt_krw(x) if x > 0 else "—")
        if "26년 목표" in tbl_yoy.columns:
            tbl_yoy["26년 목표"] = merged_yoy["26년 목표"].apply(lambda x: fmt_krw(x) if x > 0 else "—")
        tbl_yoy = tbl_yoy.rename(columns={"월_label":"월"})
        tbl_yoy.index = range(1, len(tbl_yoy)+1)
        st.dataframe(tbl_yoy, use_container_width=True, height=min(530, len(tbl_yoy)*40+55))


# ════════════════════════════════════════════════════════════
# TAB 5 — 브랜드 상세
# ════════════════════════════════════════════════════════════
with tab_brand:
    brand_df = load_brand()

    if brand_df.empty:
        st.info("브랜드 데이터가 없습니다. 사이드바에서 **IPX MASTER DATA.xlsx** 를 업로드해주세요.")
    else:
        teams_avail = [t for t in ["A팀","C팀"] if t in brand_df["팀"].values]
        if not teams_avail:
            st.warning("팀 정보를 파싱하지 못했습니다.")
        else:
            team_b = st.radio("팀 선택", teams_avail, horizontal=True, key="brand_team")
            bdf    = brand_df[brand_df["팀"] == team_b].copy()
            brands = sorted(bdf["브랜드"].dropna().unique())

            BRAND_COLORS = ["#7209b7","#f72585","#4cc9f0","#4361ee","#3a0ca3","#f4a261"]

            if not brands:
                st.info(f"{team_b} 브랜드 데이터가 없습니다.")
            else:
                actual_b = bdf[bdf["행타입"]=="실적"].dropna(subset=["값"])
                target_b = bdf[bdf["행타입"]=="목표"].dropna(subset=["값"])

                # ── 브랜드별 누적 목표·실적·달성률 카드 ────────────
                st.markdown(f'<div class="section-title">{team_b} 브랜드별 달성 현황</div>', unsafe_allow_html=True)
                card_cols = st.columns(min(len(brands), 4))
                for i, brand in enumerate(brands[:4]):
                    tgt_v = target_b[target_b["브랜드"]==brand]["값"].sum()
                    act_v = actual_b[actual_b["브랜드"]==brand]["값"].sum()
                    rate  = round(act_v/tgt_v*100, 1) if tgt_v > 0 else None
                    delta = f"목표 달성률 {rate:.1f}%" if rate else ""
                    card_cols[i].metric(
                        brand.replace("포토이즘_","").replace("포토이즘_ ",""),
                        fmt_krw(act_v) if act_v else "—",
                        delta,
                        delta_color="normal" if rate and rate>=100 else "off",
                    )

                st.divider()

                # ── 브랜드별 월별 실적 스택 바 ────────────────────
                st.markdown(f'<div class="section-title">{team_b} 브랜드별 월별 실적</div>', unsafe_allow_html=True)
                fig_b = go.Figure()
                for i, brand in enumerate(brands):
                    bd = actual_b[actual_b["브랜드"]==brand].sort_values("월")
                    if bd.empty: continue
                    fig_b.add_trace(go.Bar(
                        x=bd["월"].apply(lambda x: f"{x}월"), y=bd["값"],
                        name=brand.replace("포토이즘_","").replace("포토이즘_ ",""),
                        marker_color=BRAND_COLORS[i % len(BRAND_COLORS)],
                        hovertemplate=f"{brand}<br>%{{x}}: %{{y:,}}원<extra></extra>",
                    ))
                fig_b.update_layout(height=400, barmode="stack",
                                    yaxis=dict(tickformat=",", title="실적 (KRW)"),
                                    legend=dict(orientation="h", y=1.08),
                                    margin=dict(t=20,b=0))
                st.plotly_chart(fig_b, use_container_width=True)

                # ── 목표 vs 실적 비교 바 (브랜드별) ─────────────────
                st.markdown(f'<div class="section-title">{team_b} 브랜드별 목표 vs 실적</div>', unsafe_allow_html=True)
                fig_bt = go.Figure()
                brand_labels_short = [b.replace("포토이즘_","").replace("포토이즘_ ","") for b in brands]
                tgt_vals = [target_b[target_b["브랜드"]==b]["값"].sum() for b in brands]
                act_vals = [actual_b[actual_b["브랜드"]==b]["값"].sum() for b in brands]
                fig_bt.add_trace(go.Bar(x=brand_labels_short, y=tgt_vals, name="목표",
                                        marker_color="#dee2e6",
                                        hovertemplate="%{x}<br>목표: %{y:,}원<extra></extra>"))
                fig_bt.add_trace(go.Bar(x=brand_labels_short, y=act_vals, name="실적",
                                        marker_color="#7209b7", opacity=0.85,
                                        hovertemplate="%{x}<br>실적: %{y:,}원<extra></extra>"))
                # 달성률 표시
                for i, (t, a) in enumerate(zip(tgt_vals, act_vals)):
                    if t > 0:
                        rate = a/t*100
                        fig_bt.add_annotation(
                            x=brand_labels_short[i], y=max(t, a)*1.05,
                            text=f"{rate:.0f}%", showarrow=False,
                            font=dict(size=12, color="#f72585", family="Arial"),
                        )
                fig_bt.update_layout(height=380, barmode="group",
                                     yaxis=dict(tickformat=",", title="금액 (KRW)"),
                                     legend=dict(orientation="h", y=1.08),
                                     margin=dict(t=40,b=0))
                st.plotly_chart(fig_bt, use_container_width=True)

                # ── 브랜드별 월별 상세 테이블 ─────────────────────
                st.divider()
                with st.expander("📅 브랜드별 월별 상세 테이블"):
                    if not actual_b.empty:
                        pivot_b = actual_b.pivot_table(index="브랜드", columns="월",
                                                        values="값", aggfunc="sum").fillna(0)
                        pivot_b.columns = [f"{c}월" for c in pivot_b.columns]
                        pivot_b["합계"] = pivot_b.sum(axis=1)
                        pivot_b.index  = [idx.replace("포토이즘_","").replace("포토이즘_ ","")
                                           for idx in pivot_b.index]
                        pivot_disp = pivot_b.applymap(lambda x: fmt_krw(x) if x > 0 else "—")
                        st.dataframe(pivot_disp, use_container_width=True)


# ════════════════════════════════════════════════════════════
# TAB 6 — IP 트렌드 분석
# ════════════════════════════════════════════════════════════
with tab_ip:
    from datetime import timedelta as _td

    ph = load_ip_agg_data()   # agg parquet (7.2 MB) 기반 — load_photoism_full() 대비 훨씬 빠름

    # ── 프레임 이름 통합 설정 UI ───────────────────────────────
    with st.expander("⚙️ 프레임 이름 통합 설정 (영어 ↔ 한글 매핑)", expanded=False):
        st.caption("영어 프레임 이름을 한글 이름으로 통합합니다. 같은 아티스트의 국내/글로벌 데이터가 합산됩니다.")
        cur_alias = load_frame_alias()

        # 현재 매핑 테이블 표시
        if cur_alias:
            alias_df = pd.DataFrame(
                [{"영어 이름 (원본)": k, "한글 이름 (통합)": v} for k, v in sorted(cur_alias.items())]
            )
            st.dataframe(alias_df, use_container_width=True, hide_index=True, height=220)
        else:
            st.info("등록된 매핑이 없습니다.")

        st.divider()
        col_a1, col_a2, col_a3 = st.columns([3, 3, 2])
        new_eng = col_a1.text_input("영어 이름 (원본)", placeholder="예: KARINA", key="alias_eng")
        new_kor = col_a2.text_input("한글 이름 (통합)", placeholder="예: 카리나",  key="alias_kor")
        with col_a3:
            st.write("")
            st.write("")
            if st.button("➕ 추가 저장", use_container_width=True):
                if new_eng.strip() and new_kor.strip():
                    cur_alias[new_eng.strip()] = new_kor.strip()
                    save_frame_alias(cur_alias)
                    st.success(f"✅ {new_eng.strip()} → {new_kor.strip()} 저장됨")
                    st.cache_data.clear()
                    st.rerun()
                else:
                    st.warning("영어/한글 이름을 모두 입력하세요.")

        # 삭제
        if cur_alias:
            del_key = st.selectbox(
                "삭제할 매핑 선택",
                options=["— 선택 —"] + sorted(cur_alias.keys()),
                key="alias_del",
            )
            if del_key != "— 선택 —":
                if st.button(f"🗑 '{del_key}' 삭제", use_container_width=False):
                    cur_alias.pop(del_key, None)
                    save_frame_alias(cur_alias)
                    st.success(f"'{del_key}' 삭제됨")
                    st.cache_data.clear()
                    st.rerun()

    if ph.empty:
        st.info("포토이즘 데이터가 없습니다. 크롤러를 실행하거나 master_photoism.csv를 확인해주세요.")
    else:
        # ── 기간 설정 ──────────────────────────────────────────
        min_date = ph["날짜"].min()
        max_date = ph["날짜"].max()

        c_d1, c_d2, c_d3 = st.columns([2, 2, 2])
        analysis_end  = c_d1.date_input("기준 종료일", value=max_date,
                                         min_value=min_date, max_value=max_date, key="ip_end")
        period_days   = c_d2.selectbox("비교 기간", [7, 14, 30], index=0,
                                        format_func=lambda x: f"최근 {x}일 vs 이전 {x}일", key="ip_period")
        top_n_ip      = c_d3.slider("TOP N 개수", 5, 30, 10, key="ip_topn")

        curr_end   = analysis_end
        curr_start = curr_end - _td(days=period_days - 1)
        prev_end   = curr_start - _td(days=1)
        prev_start = prev_end - _td(days=period_days - 1)

        st.caption(
            f"📅 이번 기간: **{curr_start} ~ {curr_end}**  ·  "
            f"이전 기간: **{prev_start} ~ {prev_end}**"
        )

        # 타이틀명 없는 건 제외 (IP 미지정 결제)
        ph_ip = ph[ph["타이틀명"].astype(str).str.strip() != ""]
        curr_df = ph_ip[(ph_ip["날짜"] >= curr_start) & (ph_ip["날짜"] <= curr_end)]
        prev_df = ph_ip[(ph_ip["날짜"] >= prev_start) & (ph_ip["날짜"] <= prev_end)]

        # 제외 건수 안내 (agg parquet에서는 건수 컬럼으로 계산)
        ph_range = ph[(ph["날짜"] >= curr_start) & (ph["날짜"] <= curr_end)]
        excluded = int(ph_range["건수"].sum()) - int(curr_df["건수"].sum())
        if excluded > 0:
            st.caption(f"ℹ️ 타이틀명 미지정 건 {excluded:,}건 제외 (IP 없는 일반 결제)")

        # IP별 집계 (agg parquet에서는 건수 컬럼 합산)
        def _agg_ip(df):
            return (
                df.groupby("타이틀명")
                .agg(매출=("KRW_총매출","sum"), 건수=("건수","sum"))
                .reset_index()
                .sort_values("매출", ascending=False)
                .reset_index(drop=True)
            )

        curr_agg = _agg_ip(curr_df)
        prev_agg = _agg_ip(prev_df)
        curr_agg["이번순위"] = range(1, len(curr_agg) + 1)
        prev_agg["이전순위"] = range(1, len(prev_agg) + 1)

        rank_df = pd.merge(
            curr_agg[["타이틀명","매출","건수","이번순위"]],
            prev_agg[["타이틀명","이전순위"]], on="타이틀명", how="left"
        )

        def _rank_badge(row):
            if pd.isna(row["이전순위"]):
                return "🆕"
            diff = int(row["이전순위"]) - int(row["이번순위"])
            if diff > 0:   return f"🔺+{diff}"
            elif diff < 0: return f"🔻{diff}"
            return "➖"

        rank_df["변동"] = rank_df.apply(_rank_badge, axis=1)

        # ── Section 1: TOP IP 순위표 ───────────────────────────
        st.markdown('<div class="section-title">🏆 이번 기간 IP 순위 + 변동</div>', unsafe_allow_html=True)

        top_rank = rank_df.head(top_n_ip).copy()
        top_rank.index = range(1, len(top_rank) + 1)

        # 순위 바 차트 (가로)
        fig_rank = go.Figure()
        colors_rank = [
            "#7209b7" if "🔺" in str(r["변동"]) or "🆕" in str(r["변동"])
            else ("#e74c3c" if "🔻" in str(r["변동"]) else "#adb5bd")
            for _, r in top_rank.iterrows()
        ]
        fig_rank.add_trace(go.Bar(
            y=top_rank["타이틀명"].apply(lambda x: x[:30]),
            x=top_rank["매출"],
            orientation="h",
            marker_color=colors_rank,
            text=top_rank.apply(
                lambda r: f"{r['변동']}  {fmt_krw(r['매출'])}",axis=1
            ),
            textposition="outside",
            hovertemplate="%{y}<br>매출: %{x:,}원<extra></extra>",
        ))
        fig_rank.update_layout(
            height=max(350, top_n_ip * 36),
            yaxis=dict(autorange="reversed", tickfont=dict(size=11)),
            xaxis=dict(tickformat=",", title="매출 (KRW)"),
            margin=dict(t=20, b=0, l=220, r=160),
        )
        st.plotly_chart(fig_rank, use_container_width=True)

        # 상세 테이블 (접기)
        with st.expander("📋 순위 상세 테이블"):
            tbl_rank = top_rank[["타이틀명","매출","건수","변동"]].copy()
            tbl_rank["매출"] = tbl_rank["매출"].apply(fmt_krw)
            tbl_rank.columns = ["IP 이름","매출","결제건수","순위 변동"]
            st.dataframe(tbl_rank, use_container_width=True, hide_index=False)

        st.divider()

        # ── Section 2: 급상승 / 급하락 ─────────────────────────
        st.markdown('<div class="section-title">🔥 급상승 · 📉 급하락 (전기간 대비)</div>', unsafe_allow_html=True)

        speed_df = pd.merge(
            curr_agg[["타이틀명","매출"]].rename(columns={"매출":"이번"}),
            prev_agg[["타이틀명","매출"]].rename(columns={"매출":"이전"}),
            on="타이틀명", how="outer"
        ).fillna(0)
        speed_df = speed_df[(speed_df["이번"] > 0) | (speed_df["이전"] > 0)]
        speed_df["증가율"] = speed_df.apply(
            lambda r: (r["이번"] - r["이전"]) / r["이전"] * 100 if r["이전"] > 0 else 9999,
            axis=1
        )
        # 새로 등장 IP (이전=0, 이번>0)
        new_ips = speed_df[(speed_df["이전"] == 0) & (speed_df["이번"] > 0)].sort_values("이번", ascending=False)
        rising  = speed_df[(speed_df["이전"] > 0) & (speed_df["이번"] > 0)].nlargest(5, "증가율")
        falling = speed_df[(speed_df["이전"] > 0) & (speed_df["이번"] > 0)].nsmallest(5, "증가율")

        col_r, col_f, col_n = st.columns([2, 2, 1])

        with col_r:
            st.markdown("**🔥 급상승 TOP 5**")
            if rising.empty:
                st.caption("데이터 없음")
            else:
                for _, row in rising.iterrows():
                    st.metric(
                        row["타이틀명"][:22],
                        fmt_krw(row["이번"]),
                        f"+{row['증가율']:.0f}%  (전기간 {fmt_krw(row['이전'])})",
                        delta_color="normal",
                    )

        with col_f:
            st.markdown("**📉 급하락 TOP 5**")
            if falling.empty:
                st.caption("데이터 없음")
            else:
                for _, row in falling.iterrows():
                    st.metric(
                        row["타이틀명"][:22],
                        fmt_krw(row["이번"]),
                        f"{row['증가율']:.0f}%  (전기간 {fmt_krw(row['이전'])})",
                        delta_color="inverse",
                    )

        with col_n:
            st.markdown("**🆕 신규 등장**")
            if new_ips.empty:
                st.caption("없음")
            else:
                for _, row in new_ips.head(5).iterrows():
                    st.markdown(f"• {row['타이틀명'][:20]}  \n  `{fmt_krw(row['이번'])}`")

        st.divider()

        # ── Section 3: 국가별 인기 IP 히트맵 ──────────────────
        st.markdown('<div class="section-title">🌏 국가별 IP 인기도 히트맵</div>', unsafe_allow_html=True)

        top_ips_heat = curr_agg.head(15)["타이틀명"].tolist()
        heat_src = curr_df[curr_df["타이틀명"].isin(top_ips_heat)]
        pivot_heat = (
            heat_src.groupby(["국가코드","타이틀명"])["KRW_총매출"]
            .sum().reset_index()
            .pivot(index="타이틀명", columns="국가코드", values="KRW_총매출")
            .fillna(0)
        )
        # 정렬: 국가 = 합계 큰 순, IP = 합계 큰 순
        pivot_heat = pivot_heat[pivot_heat.sum().sort_values(ascending=False).index]
        pivot_heat = pivot_heat.loc[pivot_heat.sum(axis=1).sort_values(ascending=False).index]

        fig_heat = px.imshow(
            pivot_heat.values,
            x=list(pivot_heat.columns),
            y=[t[:25] for t in pivot_heat.index],
            color_continuous_scale="Purples",
            aspect="auto",
            labels={"color":"매출(KRW)"},
            text_auto=False,
        )
        fig_heat.update_traces(
            hovertemplate="IP: %{y}<br>국가: %{x}<br>매출: %{z:,.0f}원<extra></extra>"
        )
        fig_heat.update_layout(
            height=max(380, len(pivot_heat) * 32),
            margin=dict(t=20, b=10, l=210, r=20),
            coloraxis_colorbar=dict(title="KRW"),
            xaxis=dict(tickfont=dict(size=11)),
            yaxis=dict(tickfont=dict(size=11)),
        )
        st.plotly_chart(fig_heat, use_container_width=True)
        st.caption("색이 진할수록 해당 국가에서 해당 IP 매출이 높음")

        st.divider()

        # ── Section 4: 매출 원인 분석 ──────────────────────────
        st.markdown('<div class="section-title">💡 매출 구성 분석 — 순수 매출 / 쿠폰 기여 / 서비스코인 기여</div>', unsafe_allow_html=True)

        cause_src = curr_df[curr_df["타이틀명"].isin(curr_agg.head(top_n_ip)["타이틀명"]) & (curr_df["타이틀명"] != "")]
        cause_agg = (
            cause_src.groupby("타이틀명")
            .agg(순수=("KRW_순수","sum"), 쿠폰=("KRW_쿠폰","sum"), 코인=("KRW_코인","sum"))
            .reset_index()
        )
        cause_agg["합계"] = cause_agg[["순수","쿠폰","코인"]].sum(axis=1)
        cause_agg = cause_agg.sort_values("합계", ascending=False)
        cause_agg["쿠폰비율"] = (cause_agg["쿠폰"] / cause_agg["합계"] * 100).round(1)
        cause_agg["코인비율"] = (cause_agg["코인"] / cause_agg["합계"] * 100).round(1)

        fig_cause = go.Figure()
        short_names = cause_agg["타이틀명"].apply(lambda x: x[:22])
        fig_cause.add_trace(go.Bar(x=short_names, y=cause_agg["순수"],
                                    name="순수 매출", marker_color="#7209b7",
                                    hovertemplate="%{x}<br>순수: %{y:,}원<extra></extra>"))
        fig_cause.add_trace(go.Bar(x=short_names, y=cause_agg["쿠폰"],
                                    name="쿠폰 기여", marker_color="#f72585",
                                    hovertemplate="%{x}<br>쿠폰: %{y:,}원<extra></extra>"))
        fig_cause.add_trace(go.Bar(x=short_names, y=cause_agg["코인"],
                                    name="서비스코인", marker_color="#4cc9f0",
                                    hovertemplate="%{x}<br>코인: %{y:,}원<extra></extra>"))
        fig_cause.update_layout(
            height=420, barmode="stack",
            yaxis=dict(tickformat=",", title="금액 (KRW)"),
            xaxis=dict(tickangle=-30, tickfont=dict(size=10)),
            legend=dict(orientation="h", y=1.08),
            margin=dict(t=30, b=90),
        )
        st.plotly_chart(fig_cause, use_container_width=True)

        # 쿠폰 의존도 경고
        high_coupon = cause_agg[cause_agg["쿠폰비율"] > 30].sort_values("쿠폰비율", ascending=False)
        if not high_coupon.empty:
            names = ", ".join(
                f"**{r['타이틀명'][:15]}** ({r['쿠폰비율']:.0f}%)"
                for _, r in high_coupon.head(4).iterrows()
            )
            st.warning(f"⚠️ 쿠폰 의존도 30%↑ IP: {names}  — 쿠폰 없으면 실매출 하락 가능")

        # 구성 비율 요약 테이블
        with st.expander("📊 IP별 매출 구성 비율 테이블"):
            tbl_cause = cause_agg[["타이틀명","합계","순수","쿠폰","코인","쿠폰비율","코인비율"]].copy()
            for c in ["합계","순수","쿠폰","코인"]:
                tbl_cause[c] = tbl_cause[c].apply(fmt_krw)
            tbl_cause["쿠폰비율"] = tbl_cause["쿠폰비율"].apply(lambda x: f"{x:.1f}%")
            tbl_cause["코인비율"] = tbl_cause["코인비율"].apply(lambda x: f"{x:.1f}%")
            tbl_cause.columns = ["IP","총매출","순수매출","쿠폰기여","코인기여","쿠폰%","코인%"]
            tbl_cause.index = range(1, len(tbl_cause)+1)
            st.dataframe(tbl_cause, use_container_width=True)

        st.divider()

        # ── Section 5: IP 상세 드릴다운 (테마/프레임별) ────────────
        st.markdown('<div class="section-title">🔍 IP 상세 드릴다운 — 테마(프레임)별 매출</div>', unsafe_allow_html=True)

        ip_list = curr_agg[curr_agg["타이틀명"] != ""]["타이틀명"].tolist()
        if not ip_list:
            st.info("선택 가능한 IP가 없습니다.")
        else:
            sel_ip = st.selectbox(
                "분석할 IP 선택",
                options=ip_list,
                format_func=lambda x: f"{x}  (이번기간 {fmt_krw(curr_agg.loc[curr_agg['타이틀명']==x,'매출'].values[0])})",
                key="drill_ip",
            )

            # 드릴다운: master_photoism.parquet에서 IP+날짜 필터 쿼리 (프레임 이름 포함)
            drill_curr = load_ip_drilldown(sel_ip, curr_start, curr_end)
            drill_prev = load_ip_drilldown(sel_ip, prev_start, prev_end)

            if drill_curr.empty:
                st.info(f"선택 기간에 **{sel_ip}** 데이터가 없습니다.")
            else:
                # 요약 KPI
                tot_curr  = drill_curr["KRW_총매출"].sum()
                tot_prev  = drill_prev["KRW_총매출"].sum() if not drill_prev.empty else 0
                wow_ip    = (tot_curr - tot_prev) / tot_prev * 100 if tot_prev > 0 else None
                frame_cnt = drill_curr["프레임 이름"].nunique()
                order_cnt = len(drill_curr)   # parquet 개별 거래 행 수

                kc1, kc2, kc3, kc4 = st.columns(4)
                kc1.metric(f"{sel_ip[:20]} 총 매출", fmt_krw(tot_curr),
                           f"전기간 대비 {wow_ip:+.1f}%" if wow_ip is not None else "",
                           delta_color="normal" if wow_ip and wow_ip >= 0 else "inverse")
                kc2.metric("전기간 매출", fmt_krw(tot_prev) if tot_prev > 0 else "—")
                kc3.metric("테마(프레임) 수", f"{frame_cnt}개")
                kc4.metric("결제 건수", f"{order_cnt:,}건")

                st.divider()

                # 프레임별 집계
                frame_agg = (
                    drill_curr.groupby("프레임 이름")
                    .agg(
                        매출=("KRW_총매출", "sum"),
                        건수=("KRW_총매출", "count"),
                        쿠폰=("KRW_쿠폰", "sum"),
                        코인=("KRW_코인", "sum"),
                    )
                    .reset_index()
                    .sort_values("매출", ascending=False)
                    .reset_index(drop=True)
                )
                frame_agg.index = range(1, len(frame_agg) + 1)

                # 전기간 프레임 매출 (변동 계산용)
                if not drill_prev.empty:
                    prev_frame = (
                        drill_prev.groupby("프레임 이름")["KRW_총매출"]
                        .sum().reset_index().rename(columns={"KRW_총매출": "전매출"})
                    )
                    frame_agg = pd.merge(frame_agg, prev_frame, on="프레임 이름", how="left")
                    frame_agg["전매출"] = frame_agg["전매출"].fillna(0)
                    frame_agg["변동률"] = frame_agg.apply(
                        lambda r: f"{(r['매출']-r['전매출'])/r['전매출']*100:+.0f}%"
                        if r["전매출"] > 0 else "🆕",
                        axis=1,
                    )
                else:
                    frame_agg["전매출"] = 0
                    frame_agg["변동률"] = "—"

                frame_agg["비중"] = (frame_agg["매출"] / frame_agg["매출"].sum() * 100).round(1)
                frame_agg["쿠폰비율"] = (frame_agg["쿠폰"] / frame_agg["매출"] * 100).round(1).where(frame_agg["매출"] > 0, 0)

                col_bar, col_pie = st.columns([6, 4])

                with col_bar:
                    st.markdown(f'<div class="section-title">{sel_ip[:25]} — 테마별 매출 순위</div>',
                                unsafe_allow_html=True)
                    top_frames = frame_agg.head(20)
                    fig_drill = go.Figure(go.Bar(
                        y=top_frames["프레임 이름"].apply(lambda x: str(x)[:30]),
                        x=top_frames["매출"],
                        orientation="h",
                        marker=dict(
                            color=top_frames["매출"],
                            colorscale="Purples",
                            showscale=False,
                        ),
                        text=top_frames.apply(
                            lambda r: f"{r['변동률']}  {fmt_krw(r['매출'])}  ({r['비중']:.1f}%)", axis=1
                        ),
                        textposition="outside",
                        hovertemplate="%{y}<br>매출: %{x:,}원<extra></extra>",
                    ))
                    fig_drill.update_layout(
                        height=max(350, len(top_frames) * 34),
                        yaxis=dict(autorange="reversed", tickfont=dict(size=10)),
                        xaxis=dict(tickformat=","),
                        margin=dict(t=10, b=0, l=220, r=160),
                    )
                    st.plotly_chart(fig_drill, use_container_width=True)

                with col_pie:
                    st.markdown(f'<div class="section-title">테마별 비중</div>', unsafe_allow_html=True)
                    top5_pie   = frame_agg.head(7).copy()
                    other_sales = frame_agg.iloc[7:]["매출"].sum() if len(frame_agg) > 7 else 0
                    if other_sales > 0:
                        other_row = pd.DataFrame([{"프레임 이름": "기타", "매출": other_sales}])
                        top5_pie = pd.concat([top5_pie, other_row], ignore_index=True)
                    fig_pie = go.Figure(go.Pie(
                        labels=top5_pie["프레임 이름"].apply(lambda x: str(x)[:20]),
                        values=top5_pie["매출"],
                        hole=0.4,
                        textinfo="label+percent",
                        textfont=dict(size=11),
                    ))
                    fig_pie.update_layout(
                        height=max(350, len(top_frames) * 34),
                        margin=dict(t=10, b=0, l=0, r=0),
                        showlegend=False,
                    )
                    st.plotly_chart(fig_pie, use_container_width=True)

                # 테마별 상세 테이블
                st.markdown(f'<div class="section-title">테마별 전체 상세 테이블</div>', unsafe_allow_html=True)
                tbl_drill = frame_agg.copy()
                tbl_drill["매출"]   = tbl_drill["매출"].apply(fmt_krw)
                tbl_drill["전매출"] = tbl_drill["전매출"].apply(lambda x: fmt_krw(x) if x > 0 else "—")
                tbl_drill["쿠폰"]   = tbl_drill["쿠폰"].apply(lambda x: fmt_krw(x) if x > 0 else "—")
                tbl_drill["비중"]   = tbl_drill["비중"].apply(lambda x: f"{x:.1f}%")
                tbl_drill["쿠폰비율"] = tbl_drill["쿠폰비율"].apply(lambda x: f"{x:.1f}%" if x > 0 else "—")
                tbl_drill = tbl_drill.rename(columns={
                    "프레임 이름": "테마(프레임)", "건수": "결제건수",
                    "전매출": "전기간 매출", "변동률": "전기간 대비",
                    "비중": "매출 비중", "쿠폰비율": "쿠폰 비중",
                })
                show_cols = ["테마(프레임)", "매출", "결제건수", "전기간 매출", "전기간 대비", "매출 비중", "쿠폰", "쿠폰 비중"]
                st.dataframe(tbl_drill[show_cols], use_container_width=True,
                             height=min(600, len(tbl_drill) * 40 + 55))
