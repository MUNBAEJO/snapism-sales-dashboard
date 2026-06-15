"""
KPI 목표 달성률 대시보드  (4탭 · 실적 모델)
  탭1 📊 전체     — A팀(아티스트)+C팀(캐릭터)+스내피즘 합산 실적·월별 추이
  탭2 👥 팀별     — 팀/항목별 실적 카드·월별 추이·누적 요약
  탭3 📅 주차별   — 최근 14주 팀/항목별 실적(월~일)
  탭4 📈 전년비   — 전사(포토이즘 TTL) 기준 25 vs 26 (엑셀 25년 vs CMS 26년).
                  ※ 앞 3탭(A/C/스내피즘)과 집계 범위가 다름 — 2025 CMS·스내피즘
                    데이터가 없어 전년비는 전사 TTL 기준으로만 가능.

실적 산출(실시간):
  · A팀=포토이즘 CMS IP구분 '아티스트', C팀='캐릭터' (KRW 환산, 취소 제외)
  · 픽=IP구분 'PICK' (A·C로 안 나뉘어 하나로 묶은 독립 팀/항목)
  · 스내피즘=master.csv 정산금액(KRW환산+쿠폰, 취소 제외)
  ※ 렌탈·기획(P)·제외는 미포함 (TEAM_GUBUN 확장 지점)

파일 관리(소유자 전용): IPX MASTER DATA.xlsx 업로드 시 목표/실적/주차 CSV 파싱·저장.
  현재 화면은 실적만 표시 — 목표·달성률은 추후.
"""
import io, json, re
import streamlit as st
import pandas as pd
import pyarrow.parquet as pq
import plotly.graph_objects as go
from pathlib import Path
from datetime import date

# set_page_config 는 라우터(스내피즘.py)에서 처리
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from guide_content import render_guide

INK = "#1a1a2e"; PRIMARY = "#4361ee"; SECONDARY = "#7209b7"; PINK = "#f72585"
st.markdown(f"""
<style>
html, body, [class*="css"], [data-testid="stAppViewContainer"] {{
    font-family: 'Pretendard', -apple-system, BlinkMacSystemFont,
                 'Segoe UI', 'Malgun Gothic', 'Apple SD Gothic Neo', sans-serif;
}}
[data-testid="stAppViewContainer"] .main .block-container {{
    padding-top: 2.2rem; padding-bottom: 3rem; max-width: 1500px;
}}
h1 {{ font-weight: 800 !important; letter-spacing: -0.5px; color: {INK}; }}
.section-title {{
    font-size: 1.15rem; font-weight: 700; color: {INK};
    margin: 6px 0 12px; padding-left: 12px; border-left: 4px solid {PRIMARY}; line-height: 1.4;
}}
.section-title.purple {{ border-left-color: {SECONDARY}; }}
.section-title.pink   {{ border-left-color: {PINK}; }}
/* KPI 카드 (TV 가독성 위해 값 글씨 크게 유지) */
[data-testid="stMetric"], [data-testid="metric-container"] {{
    background: linear-gradient(135deg, #ffffff 0%, #f5f8ff 100%);
    border: 1px solid #e7ecf7; border-radius: 16px; padding: 16px 24px;
    box-shadow: 0 2px 10px rgba(67,97,238,0.06);
    transition: transform .15s ease, box-shadow .15s ease;
}}
[data-testid="stMetric"]:hover, [data-testid="metric-container"]:hover {{
    transform: translateY(-3px); box-shadow: 0 8px 20px rgba(67,97,238,0.14);
}}
[data-testid="stMetricLabel"] p {{ font-size: 1.0rem !important; font-weight: 600 !important; color: #6b7280; }}
[data-testid="stMetricValue"] {{ font-size: 2.0rem !important; font-weight: 800 !important; color: {INK} !important; letter-spacing: -0.5px; }}
[data-testid="stMetricDelta"] {{ font-size: 0.9rem !important; }}
hr {{ margin: 1.4rem 0 1.2rem; border: none; border-top: 1px solid #e9edf5; }}
[data-testid="stDeployButton"] {{ display: none !important; }}
[data-testid="stElementToolbar"] {{ display: none; }}
[data-testid="stSidebar"] {{ background: #fbfcfe; border-right: 1px solid #eceff5; }}
[data-testid="stDataFrame"] {{ border-radius: 12px; overflow: hidden; }}
button[data-baseweb="tab"] p {{ font-size: 1.0rem !important; font-weight: 700 !important; }}
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
JIRA_CACHE    = BASE_DIR / "data" / "jira_ip_dates_cache.json"   # 타이틀명→종료일(로컬 캐시)

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


# ══════════════════════════════════════════════════════════════
# 사업부 실적 (A팀=아티스트 · C팀=캐릭터 · 픽=PICK · 스내피즘) — 실시간 산출
#   · A/C팀: 포토이즘 CMS의 IP구분으로 산출 (CMS엔 팀 컬럼이 없음)
#   · 픽: IP구분 'PICK'. A·C로 안 나뉘어 하나(통합)로 묶은 독립 팀/항목
#   · 스내피즘: master.csv 정산금액(KRW환산+쿠폰), 취소 제외
#   ※ 렌탈·기획(P)·제외는 미배정(일단 제외). 추후 구분이
#     확정되면 TEAM_GUBUN 에 해당 IP구분을 추가하면 자동 편입된다.
# ══════════════════════════════════════════════════════════════
SNAP_MASTER = BASE_DIR / "data" / "master.csv"

# 팀/항목 정의 — 확장 지점(렌탈/기획 편입 시 여기 수정)
#   픽(PICK)은 A·C(아티스트/캐릭터)로 나뉘지 않아 하나로 묶은 독립 팀/항목.
TEAM_GUBUN = {"A팀": ["아티스트"], "C팀": ["캐릭터"], "픽": ["PICK"]}
DIV_ORDER  = ["A팀", "C팀", "픽", "스내피즘"]
DIV_COLORS = {"A팀": "#7209b7", "C팀": "#f72585", "픽": "#f9a826", "스내피즘": "#4cc9f0"}
DIV_LABEL  = {"A팀": "A팀 (아티스트)", "C팀": "C팀 (캐릭터)", "픽": "픽 (PICK)", "스내피즘": "스내피즘"}


def _mtime(p) -> float:
    try:
        return os.path.getmtime(p)
    except OSError:
        return 0.0


@st.cache_data(show_spinner=False)
def _photoism_ip_daily(_agg_mtime, _cfg_mtime) -> pd.DataFrame:
    """포토이즘 agg → 날짜·IP구분·국내여부별 KRW 매출액 (취소 제외, 일 단위 집계)."""
    cols = ["날짜", "IP구분", "_kr", "매출액"]
    if not AGG_FILE.exists():
        return pd.DataFrame(columns=cols)
    df = pq.read_table(str(AGG_FILE)).to_pandas(strings_to_categorical=True)
    df["날짜"] = pd.to_datetime(df["날짜"], errors="coerce")
    df = df[df["날짜"].notna()]
    df = df[~df["취소 여부"].astype(bool)]
    if df.empty:
        return pd.DataFrame(columns=cols)
    ex   = load_exchange_rates()
    unit = df["결제 단위"].astype(str).str.strip().replace("nan", "KRW")
    rate = unit.map(ex).fillna(1)
    cc   = df["국가코드"].astype(str).str.lower().str.strip()
    for c in ["최종 결제 금액", "쿠폰 할인 금액", "서비스코인"]:
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)
    base = pd.DataFrame({
        "날짜":   df["날짜"].dt.normalize().values,
        "IP구분":  df["IP구분"].astype(str).values,
        "_kr":    cc.eq("kr").values,
        "매출액": (
            (df["최종 결제 금액"] * rate).round(0)
            + (df["쿠폰 할인 금액"] * rate).round(0) * cc.isin(_COUPON_CC)
            + (df["서비스코인"]     * rate).round(0) * cc.isin(_COIN_CC)
        ).values,
    })
    return base.groupby(["날짜", "IP구분", "_kr"], as_index=False)["매출액"].sum()


def photoism_ip_daily() -> pd.DataFrame:
    return _photoism_ip_daily(_mtime(AGG_FILE), _mtime(CONFIG_FILE))


@st.cache_data(show_spinner=False)
def _photoism_ipname_daily(_agg_mtime, _cfg_mtime) -> pd.DataFrame:
    """포토이즘 agg → 날짜·IP구분·IP명·국내여부별 KRW 매출액 (취소 제외).
    개별 IP 증감(무버) 산출용. 매출 산식은 _photoism_ip_daily 와 100% 동일."""
    cols = ["날짜", "IP구분", "IP명", "_kr", "매출액"]
    if not AGG_FILE.exists():
        return pd.DataFrame(columns=cols)
    df = pq.read_table(str(AGG_FILE)).to_pandas(strings_to_categorical=True)
    df["날짜"] = pd.to_datetime(df["날짜"], errors="coerce")
    df = df[df["날짜"].notna()]
    df = df[~df["취소 여부"].astype(bool)]
    if df.empty:
        return pd.DataFrame(columns=cols)
    ex   = load_exchange_rates()
    unit = df["결제 단위"].astype(str).str.strip().replace("nan", "KRW")
    rate = unit.map(ex).fillna(1)
    cc   = df["국가코드"].astype(str).str.lower().str.strip()
    for c in ["최종 결제 금액", "쿠폰 할인 금액", "서비스코인"]:
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)
    base = pd.DataFrame({
        "날짜":   df["날짜"].dt.normalize().values,
        "IP구분":  df["IP구분"].astype(str).values,
        "IP명":    df["IP명"].astype(str).values,
        "_kr":    cc.eq("kr").values,
        "매출액": (
            (df["최종 결제 금액"] * rate).round(0)
            + (df["쿠폰 할인 금액"] * rate).round(0) * cc.isin(_COUPON_CC)
            + (df["서비스코인"]     * rate).round(0) * cc.isin(_COIN_CC)
        ).values,
    })
    return base.groupby(["날짜", "IP구분", "IP명", "_kr"], as_index=False)["매출액"].sum()


def photoism_ipname_daily() -> pd.DataFrame:
    return _photoism_ipname_daily(_mtime(AGG_FILE), _mtime(CONFIG_FILE))


@st.cache_data(show_spinner=False)
def _jira_due_map(_cache_mtime) -> dict:
    """타이틀명 → 종료일(YYYY-MM-DD). 로컬 Jira 캐시에서만 읽음(네트워크 X).
    2099년 이후(무기한)·미설정은 제외."""
    if not JIRA_CACHE.exists():
        return {}
    try:
        with open(JIRA_CACHE, encoding="utf-8") as f:
            c = json.load(f)
        data = c.get("ip_dates_all", {}).get("data", {})
    except Exception:
        return {}
    out = {}
    for title, v in data.items():
        dd = v.get("duedate") if isinstance(v, dict) else None
        if dd and str(dd) < "2099-01-01":
            out[str(title)] = str(dd)
    return out


@st.cache_data(show_spinner=False)
def _ip_end_status(_agg_mtime, _cache_mtime, _ym: str) -> dict:
    """IP명 → 종료상태. 포토이즘 A·C·픽 한정.
    그 IP의 (종료일이 등록된) 모든 타이틀이 이번 달 전에 끝났으면 '🔚 종료',
    하나라도 이번 달 이후까지 살아있으면 '🔴 판매중'. 종료일 정보가 없으면 키 없음(→'—')."""
    due = _jira_due_map(_cache_mtime)
    if not due or not AGG_FILE.exists():
        return {}
    try:
        df = pq.read_table(str(AGG_FILE), columns=["IP구분", "IP명", "타이틀명"]).to_pandas()
    except Exception:
        return {}
    df = df[df["IP구분"].astype(str).isin(["아티스트", "캐릭터", "PICK"])]
    df = df[["IP명", "타이틀명"]].astype(str).drop_duplicates()
    first_this = f"{_ym}-01"
    by_ip: dict = {}
    for ipn, ttl in zip(df["IP명"], df["타이틀명"]):
        d = due.get(ttl)
        if d:
            by_ip.setdefault(ipn, []).append(d)
    return {ipn: ("🔚 종료" if max(ds) < first_this else "🔴 판매중")
            for ipn, ds in by_ip.items()}


@st.cache_data(show_spinner=False)
def _postperiod_mtd(_agg_mtime, _cfg_mtime, _cache_mtime, _ym: str) -> int:
    """이번 달 포토이즘(A·C·픽) 매출 중 'Jira 종료일 지난 뒤 발생'분 합계(KRW).
    매출 산식은 벤토와 동일(쿠폰·코인 포함). 종료일 매칭은 타이틀명 직접 비교."""
    due = _jira_due_map(_cache_mtime)
    if not due or not AGG_FILE.exists():
        return 0
    try:
        df = pq.read_table(str(AGG_FILE), columns=[
            "날짜", "IP구분", "타이틀명", "취소 여부", "결제 단위", "국가코드",
            "최종 결제 금액", "쿠폰 할인 금액", "서비스코인"]).to_pandas()
    except Exception:
        return 0
    df["날짜"] = pd.to_datetime(df["날짜"], errors="coerce")
    df = df[df["날짜"].notna() & ~df["취소 여부"].astype(bool)]
    y, m = int(_ym[:4]), int(_ym[5:7])
    df = df[(df["날짜"].dt.year == y) & (df["날짜"].dt.month == m)]
    df = df[df["IP구분"].astype(str).isin(["아티스트", "캐릭터", "PICK"])]
    if df.empty:
        return 0
    _duemap = {t: pd.Timestamp(d) for t, d in due.items()}
    _dd = df["타이틀명"].astype(str).map(_duemap)
    df = df[_dd.notna() & (df["날짜"] > _dd)]
    if df.empty:
        return 0
    ex   = load_exchange_rates()
    rate = df["결제 단위"].astype(str).str.strip().replace("nan", "KRW").map(ex).fillna(1)
    cc   = df["국가코드"].astype(str).str.lower().str.strip()
    for c in ["최종 결제 금액", "쿠폰 할인 금액", "서비스코인"]:
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)
    rev = ((df["최종 결제 금액"] * rate).round(0)
           + (df["쿠폰 할인 금액"] * rate).round(0) * cc.isin(_COUPON_CC)
           + (df["서비스코인"]     * rate).round(0) * cc.isin(_COIN_CC))
    return int(rev.sum())


@st.cache_data(show_spinner=False)
def _snapism_daily(_m_csv, _m_parq, _cfg_mtime) -> pd.DataFrame:
    """스내피즘 master → 날짜·국내여부별 정산금액(KRW환산+쿠폰, 취소 제외, 일 단위)."""
    import data_io
    cols = ["날짜", "_kr", "매출액"]
    df = data_io.read_master(SNAP_MASTER)
    if df.empty:
        return pd.DataFrame(columns=cols)
    df["날짜"] = pd.to_datetime(df["날짜"], errors="coerce")
    df = df[df["날짜"].notna()]
    cancel = df["취소 여부"].astype(str).str.lower().isin(["true", "1", "yes"])
    for c in ["최종 결제 금액", "쿠폰 할인 금액"]:
        df[c] = pd.to_numeric(df.get(c, 0), errors="coerce").fillna(0)
    ex   = load_exchange_rates()
    unit = df["결제 단위"].fillna("KRW").astype(str).str.strip()
    rate = unit.map(ex).fillna(1)
    amt  = (df["최종 결제 금액"] * rate).round(0) + (df["쿠폰 할인 금액"] * rate).round(0)
    keep = (~cancel) & ((df["최종 결제 금액"] > 0) | (df["쿠폰 할인 금액"] > 0))
    base = pd.DataFrame({
        "날짜":   df["날짜"].dt.normalize().values,
        "_kr":   df.get("소스", "").astype(str).str.strip().eq("한국").values,
        "매출액": amt.values,
    })[keep.values]
    return base.groupby(["날짜", "_kr"], as_index=False)["매출액"].sum()


def snapism_daily() -> pd.DataFrame:
    return _snapism_daily(
        _mtime(SNAP_MASTER), _mtime(SNAP_MASTER.with_suffix(".parquet")), _mtime(CONFIG_FILE)
    )


def _seg_filter(df: pd.DataFrame, seg: str) -> pd.DataFrame:
    if seg == "국내":
        return df[df["_kr"]]
    if seg == "해외":
        return df[~df["_kr"]]
    return df


def _division_daily(seg: str = "TTL") -> pd.DataFrame:
    """A팀(아티스트)·C팀(캐릭터)·스내피즘 일별 실적 long-form: 날짜, 팀/항목, 매출액."""
    frames = []
    ip = _seg_filter(photoism_ip_daily(), seg)
    for team, gubuns in TEAM_GUBUN.items():
        sub = ip[ip["IP구분"].isin(gubuns)]
        g = sub.groupby("날짜", as_index=False)["매출액"].sum()
        g["팀/항목"] = team
        frames.append(g)
    sp = _seg_filter(snapism_daily(), seg)
    gs = sp.groupby("날짜", as_index=False)["매출액"].sum()
    gs["팀/항목"] = "스내피즘"
    frames.append(gs)
    out = pd.concat(frames, ignore_index=True)
    if out.empty:
        return pd.DataFrame(columns=["날짜", "팀/항목", "매출액"])
    out["날짜"] = pd.to_datetime(out["날짜"])
    return out


def division_monthly(seg: str = "TTL") -> pd.DataFrame:
    """월별 실적 long-form: 연도, 월, 팀/항목, 실적."""
    d = _division_daily(seg)
    if d.empty:
        return pd.DataFrame(columns=["연도", "월", "팀/항목", "실적"])
    d["연도"] = d["날짜"].dt.year
    d["월"]   = d["날짜"].dt.month
    out = d.groupby(["연도", "월", "팀/항목"], as_index=False)["매출액"].sum()
    out = out.rename(columns={"매출액": "실적"})
    out["실적"] = pd.to_numeric(out["실적"], errors="coerce").fillna(0).astype("int64")
    return out[out["연도"] > 0]


def division_weekly(seg: str = "TTL", weeks: int = 14) -> pd.DataFrame:
    """주차별 실적 long-form(최근 weeks주): 주시작(월요일), 주차, 팀/항목, 실적."""
    d = _division_daily(seg)
    if d.empty:
        return pd.DataFrame(columns=["주시작", "주차", "팀/항목", "실적"])
    d["주시작"] = (d["날짜"] - pd.to_timedelta(d["날짜"].dt.weekday, unit="D")).dt.normalize()
    out = d.groupby(["주시작", "팀/항목"], as_index=False)["매출액"].sum()
    out = out.rename(columns={"매출액": "실적"})
    out["실적"] = pd.to_numeric(out["실적"], errors="coerce").fillna(0).astype("int64")
    recent = sorted(out["주시작"].unique())[-weeks:]
    out = out[out["주시작"].isin(recent)].copy()

    def _wlabel(ws):
        we = ws + pd.Timedelta(days=6)
        return f"{ws.month}/{ws.day}~{we.month}/{we.day}"

    out["주차"] = out["주시작"].apply(_wlabel)
    return out.sort_values("주시작")


def fmt_krw(n: float) -> str:
    return f"₩{int(n):,}"


# ── 프레임 alias 관리 ─────────────────────────────────────────
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
import auth
_email    = (st.user.email or "").strip().lower() if getattr(st, "user", None) else ""
_is_owner = auth.is_owner(_email)

st.sidebar.header("📊 보기 설정")
col_yr, col_seg = st.sidebar.columns([1, 2])
target_year = col_yr.number_input("연도", min_value=2024, max_value=2030, value=2026, step=1)
seg_choice  = col_seg.radio("지역 기준", ["TTL (국내+해외)", "국내", "해외"], index=0)
SEG_MAP = {"TTL (국내+해외)": "TTL", "국내": "국내", "해외": "해외"}

# ── 파일 관리: 소유자(나)만 노출 ──────────────────────────────
if _is_owner:
    st.sidebar.divider()
    st.sidebar.header("🔒 파일 관리 (소유자)")
    st.sidebar.caption("**IPX MASTER DATA.xlsx** 업로드 시 REPORT·A팀·C팀 시트 자동 파싱")
    uploaded = st.sidebar.file_uploader("데이터 파일 올리기 (.xlsx / .csv)", type=["xlsx", "csv"])
else:
    uploaded = None

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
            st.sidebar.success(f"✅ 목표·실적을 불러왔어요: {', '.join(parsed_segs)}")

            # ── YoY 파싱 ──────────────────────────────────────
            try:
                buf.seek(0)
                yoy_df = parse_yoy_25(buf)
                if not yoy_df.empty:
                    yoy_df.to_csv(YOY_FILE, index=False, encoding="utf-8-sig")
                    st.sidebar.success(f"✅ 25년 전년비를 불러왔어요: {yoy_df['구분'].unique().tolist()}")
            except Exception as e:
                st.sidebar.warning(f"전년비 데이터를 읽지 못했어요. 시트 구성을 확인한 뒤 다시 올려 주세요. ({e})")

            # ── 주차별 파싱 ────────────────────────────────────
            try:
                buf.seek(0)
                weekly_df = parse_weekly_data(buf)
                if not weekly_df.empty:
                    weekly_df.to_csv(WEEKLY_FILE, index=False, encoding="utf-8-sig")
                    w_cnt = weekly_df.groupby("팀")["주차"].count().to_dict()
                    st.sidebar.success(f"✅ 주차별 실적을 불러왔어요: {w_cnt}")
            except Exception as e:
                st.sidebar.warning(f"주차별 데이터를 읽지 못했어요. ({e})")

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
                st.sidebar.error("CSV에 연도·월·매출목표 컬럼이 필요해요. 세 컬럼을 채워 다시 올려 주세요.")
            else:
                new_tgt.to_csv(KPI_FILE, index=False, encoding="utf-8-sig")
                st.sidebar.success("✅ 목표 CSV를 저장했어요. 대시보드에 바로 반영돼요.")
                st.cache_data.clear()
                st.rerun()

    except Exception as e:
        st.sidebar.error(f"파일을 처리하지 못했어요. 파일 형식을 확인한 뒤 다시 올려 주세요. ({e})")

if _is_owner:
    if ACTUALS_FILE.exists() and st.sidebar.button("🗑 엑셀 실적 초기화 (CMS로 전환)"):
        ACTUALS_FILE.unlink()
        st.cache_data.clear()
        st.rerun()

    _template = pd.DataFrame({"연도": [2026]*12, "월": list(range(1, 13)), "매출목표": [0]*12})
    st.sidebar.download_button(
        "📥 목표 CSV 템플릿",
        _template.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig"),
        "kpi_targets.csv", "text/csv",
    )

    # ── 목표 직접 수정 (엑셀 재업로드 없이 인라인 편집) ──
    with st.sidebar.expander("🎯 목표 직접 수정", expanded=False):
        st.caption("구분·연도를 고르고 월별 목표를 고쳐 저장해요. 모든 탭·요약에 바로 반영돼요.")
        _es = st.selectbox("구분", ["TTL", "국내", "해외", "A팀", "C팀", "오리지널"], key="tgt_edit_seg")
        _ey = st.number_input("연도", min_value=2024, max_value=2030,
                              value=int(target_year), step=1, key="tgt_edit_yr")
        if KPI_FILE.exists():
            _full_t = pd.read_csv(KPI_FILE, encoding="utf-8-sig")
        else:
            _full_t = pd.DataFrame(columns=["연도", "월", "매출목표", "구분"])
        if "구분" not in _full_t.columns:
            _full_t["구분"] = "TTL"
        for _c in ["연도", "월", "매출목표"]:
            _full_t[_c] = pd.to_numeric(_full_t.get(_c), errors="coerce")
        _cur_t  = _full_t[(_full_t["구분"] == _es) & (_full_t["연도"] == _ey)][["월", "매출목표"]]
        _edit_t = pd.DataFrame({"월": list(range(1, 13))}).merge(_cur_t, on="월", how="left")
        _edit_t["매출목표"] = _edit_t["매출목표"].fillna(0).astype("int64")
        _edited_t = st.data_editor(
            _edit_t, hide_index=True, use_container_width=True, key="tgt_editor",
            column_config={
                "월": st.column_config.NumberColumn("월", disabled=True),
                "매출목표": st.column_config.NumberColumn("매출목표(원)", min_value=0, step=1_000_000, format="%d"),
            },
        )
        if st.button("💾 목표 저장", key="tgt_save", use_container_width=True):
            _save = _edited_t.copy()
            _save["연도"], _save["구분"] = int(_ey), _es
            _save["매출목표"] = pd.to_numeric(_save["매출목표"], errors="coerce").fillna(0).astype("int64")
            _save = _save[["연도", "월", "매출목표", "구분"]]
            _keep_t = (_full_t[~((_full_t["구분"] == _es) & (_full_t["연도"] == _ey))]
                       if not _full_t.empty else _full_t)
            _out_t = pd.concat([_keep_t.reindex(columns=["연도", "월", "매출목표", "구분"]), _save],
                               ignore_index=True).dropna(subset=["연도", "월"])
            _out_t["연도"] = _out_t["연도"].astype("int64")
            _out_t["월"]   = _out_t["월"].astype("int64")
            _out_t["매출목표"] = pd.to_numeric(_out_t["매출목표"], errors="coerce").fillna(0).astype("int64")
            _out_t = _out_t.sort_values(["구분", "연도", "월"])
            _out_t.to_csv(KPI_FILE, index=False, encoding="utf-8-sig")
            st.cache_data.clear()
            st.toast(f"✅ {int(_ey)}년 {_es} 목표를 저장했어요.")
            st.rerun()


# ══════════════════════════════════════════════════════════════
# 메인 대시보드
# ══════════════════════════════════════════════════════════════
st.title("🎯 KPI 목표 달성률")
render_guide("kpi")

_seg  = SEG_MAP[seg_choice]
today = date.today()

# ── 데이터 기준일 (신선도) ──────────────────────────────────────
#   "이 숫자가 며칠까지냐" — 모든 수치 신뢰의 전제. 마지막 매출일 + 집계 갱신시각.
try:
    import datetime as _dtm
    _pho_d = photoism_ip_daily()
    _snp_d = snapism_daily()

    def _d10(s):
        return pd.to_datetime(s).strftime("%Y-%m-%d") if s is not None and pd.notna(s) else "—"

    _pho_last = _pho_d["날짜"].max() if not _pho_d.empty else None
    _snp_last = _snp_d["날짜"].max() if not _snp_d.empty else None
    _agg_mt   = _mtime(AGG_FILE)
    _refresh  = _dtm.datetime.fromtimestamp(_agg_mt).strftime("%m/%d %H:%M") if _agg_mt else "—"
    _stale    = (_pho_last is not None and pd.notna(_pho_last)
                 and (today - pd.to_datetime(_pho_last).date()).days >= 2)
    _warn     = "  ⚠️ 데이터가 2일 이상 밀렸어요" if _stale else ""
    st.caption(f"🗂 데이터 기준 · 포토이즘 ~**{_d10(_pho_last)}** (집계 갱신 {_refresh}) · "
               f"스내피즘 ~**{_d10(_snp_last)}**{_warn}")
except Exception:
    pass

# ── 상단 벤토 요약 (이번 달 실적 한눈에) ────────────────────────
#   합계(히어로 + 구성 막대) + 팀/항목 4타일. 상세 차트·표는 아래 탭에서.
#   진행 중인 달이라 전월 '같은 기간(1일~오늘)'과 비교해 착시를 없앤다.
#   요약이 실패해도 본 화면은 떠야 하므로 통째로 try/except 로 감싼다.
try:
    _dd = _division_daily(_seg)
    if not _dd.empty:
        _dd = _dd.copy()
        _dd["_y"] = _dd["날짜"].dt.year
        _dd["_m"] = _dd["날짜"].dt.month
        _dd["_d"] = _dd["날짜"].dt.day
        _pm = today.month - 1 if today.month > 1 else 12
        _py = today.year if today.month > 1 else today.year - 1
        _curm  = _dd[(_dd["_y"] == today.year) & (_dd["_m"] == today.month)]
        _prevm = _dd[(_dd["_y"] == _py) & (_dd["_m"] == _pm) & (_dd["_d"] <= today.day)]

        def _bv(d, s):
            return int(d[d["팀/항목"] == s]["매출액"].sum())

        def _eok(v):
            return f"{v/1e8:,.1f}"

        def _delta(cur, prev, big=False):
            fs = "13px" if big else "11px"
            if prev <= 0:
                return f'<span style="font-size:{fs};color:#9aa0aa;">전월 같은 기간 비교 없음</span>'
            pct = (cur - prev) / prev * 100
            up = pct >= 0
            color = "#1d8a4e" if up else "#c0392b"
            return (f'<span style="font-size:{fs};color:{color};font-weight:600;">'
                    f'{"▲" if up else "▼"} 전월 같은 기간 {pct:+.1f}%</span>')

        _vals  = {s: _bv(_curm, s) for s in DIV_ORDER}
        _tot_c = sum(_vals.values())
        _tot_p = int(_prevm["매출액"].sum())

        # ── 월말 예상(run-rate) + 목표 달성률 ──
        #   월말 예상 = 현재 누적 ÷ 경과일 × 당월 일수 (단순 선형 추정)
        #   목표는 A팀·C팀만 구분(스코프)이 일치 → 그 둘만, 그리고 지역=TTL일 때만 표시.
        #   (TTL 목표=포토이즘 전사 범위라 벤토 합계와 스코프가 달라 전체 달성률은 두지 않음)
        _dim     = pd.Period(f"{today.year}-{today.month:02d}", freq="M").days_in_month
        _elapsed = max(today.day, 1)

        def _runrate(cur):
            return int(cur / _elapsed * _dim)

        _TGT_SEG = {"A팀": "A팀", "C팀": "C팀"}

        def _tgt(s):
            g = _TGT_SEG.get(s)
            if not g or _seg != "TTL":
                return None
            t = load_targets(g)
            r = t[(t["연도"] == today.year) & (t["월"] == today.month)]
            if r.empty:
                return None
            v = int(r["매출목표"].iloc[0])
            return v if v > 0 else None

        def _goal_line(cur, s):
            tg = _tgt(s)
            if tg is None:
                return '<div class="kbz-goal muted">🎯 목표 미설정</div>'
            mtd  = cur / tg * 100
            proj = _runrate(cur) / tg * 100
            pc   = "#1d8a4e" if proj >= 100 else ("#c77700" if proj >= 85 else "#c0392b")
            return (f'<div class="kbz-goal">🎯 목표 {mtd:.0f}% '
                    f'<span style="color:{pc};">· 예상 {proj:.0f}%</span></div>')

        _segbar = "".join(
            f'<div style="width:{(_vals[s]/_tot_c*100) if _tot_c else 0:.2f}%;'
            f'background:{DIV_COLORS.get(s, "#888")};"></div>' for s in DIV_ORDER
        )
        _legend = "".join(
            f'<span><span class="kbz-dot" style="background:{DIV_COLORS.get(s, "#888")};"></span>'
            f'{DIV_LABEL.get(s, s).split(" ")[0]} {((_vals[s]/_tot_c*100) if _tot_c else 0):.0f}%</span>'
            for s in DIV_ORDER
        )

        _areas = {"A팀": "a", "C팀": "c", "픽": "pick", "스내피즘": "snap"}
        _tiles = ""
        for _s in DIV_ORDER:
            _cv, _pv = _vals[_s], _bv(_prevm, _s)
            _tiles += (
                f'<div class="kbz-tile" style="grid-area:{_areas.get(_s, "")};'
                f'border-left:6px solid {DIV_COLORS.get(_s, "#888")};">'
                f'<div class="kbz-row"><span class="kbz-dot" style="background:{DIV_COLORS.get(_s, "#888")};"></span>'
                f'<span class="kbz-lbl">{DIV_LABEL.get(_s, _s)}</span></div>'
                f'<div class="kbz-val">{fmt_krw(_cv)}</div>'
                f'{_delta(_cv, _pv)}'
                f'{_goal_line(_cv, _s)}</div>'
            )
        _seg_note = "" if _seg == "TTL" else f" · {_seg}"
        st.markdown(f"""
<style>
#kpi-bento .kbz-grid{{display:grid;grid-template-columns:1.7fr 1fr 1fr;gap:14px;
  grid-template-areas:"hero a c" "hero pick snap";margin:4px 0 6px;}}
#kpi-bento .kbz-tile{{background:#ffffff;border:1px solid #e7ecf7;border-radius:18px;
  padding:18px 20px;box-shadow:0 3px 14px rgba(67,97,238,0.07);min-height:108px;
  display:flex;flex-direction:column;justify-content:center;}}
#kpi-bento .kbz-hero{{grid-area:hero;background:#eef2fe;border:none;padding:24px 28px;
  justify-content:center;}}
#kpi-bento .kbz-row{{display:flex;align-items:center;gap:6px;margin-bottom:7px;}}
#kpi-bento .kbz-dot{{width:10px;height:10px;border-radius:50%;display:inline-block;}}
#kpi-bento .kbz-lbl{{font-size:12.5px;color:#6b7280;}}
#kpi-bento .kbz-val{{font-size:1.25rem;font-weight:800;color:#1a1a2e;
  letter-spacing:-0.3px;line-height:1.1;margin-bottom:5px;}}
#kpi-bento .kbz-bar{{display:flex;height:14px;border-radius:7px;overflow:hidden;
  gap:2px;margin-top:16px;}}
#kpi-bento .kbz-bar>div{{height:100%;}}
#kpi-bento .kbz-leg{{display:flex;flex-wrap:wrap;gap:14px;margin-top:11px;}}
#kpi-bento .kbz-leg span{{font-size:12px;color:#6b7280;display:flex;align-items:center;gap:6px;}}
#kpi-bento .kbz-goal{{font-size:11px;color:#5b6470;font-weight:600;margin-top:5px;}}
#kpi-bento .kbz-goal.muted{{color:#b3b9c4;font-weight:500;}}
#kpi-bento .kbz-rr{{font-size:13px;color:#185FA5;font-weight:700;margin-top:9px;}}
#kpi-bento .kbz-rr span{{font-weight:500;opacity:.7;}}
@media (max-width:760px){{#kpi-bento .kbz-grid{{grid-template-columns:1fr 1fr;
  grid-template-areas:"hero hero" "a c" "pick snap";}}}}
</style>
<div id="kpi-bento"><div class="kbz-grid">
  <div class="kbz-tile kbz-hero">
    <div style="font-size:12.5px;color:#185FA5;">{today.month}월 합계 실적
      <span style="color:#85B7EB;">(1일~{today.day}일 · 진행 중){_seg_note}</span></div>
    <div style="font-size:2.1rem;font-weight:800;color:#1a1a2e;letter-spacing:-0.8px;line-height:1.05;margin:7px 0 6px;">
      {fmt_krw(_tot_c)}</div>
    {_delta(_tot_c, _tot_p, big=True)}
    <div class="kbz-rr">🎯 월말 예상 {fmt_krw(_runrate(_tot_c))}
      <span>· 경과 {today.day}일 평균 기준</span></div>
    <div class="kbz-bar">{_segbar}</div>
    <div class="kbz-leg">{_legend}</div>
  </div>
  {_tiles}
</div></div>
""", unsafe_allow_html=True)
        st.caption("※ 진행 중인 달이라 ‘전월 같은 기간(1일~오늘)’과 비교해요. "
                   "‘월말 예상’은 현재 일평균 × 당월 일수로 단순 추정한 값이에요. "
                   "목표 달성률은 목표가 같은 범위로 잡힌 A팀·C팀만 표시해요(픽·스내피즘은 목표 미설정). "
                   "정밀 숫자·월별 추이는 아래 탭에서 보세요.")
except Exception:
    pass

# ── 재무 정합성 한 줄 (전체 기준) ───────────────────────────────
#   합계의 성격(명목/혼합)·해외 비중·기간 후 매출 리스크를 한 줄로.
#   실수령(RS 차감)은 IP정산현황 페이지 영역이라 여기선 안내만 한다.
try:
    _ym  = f"{today.year}-{today.month:02d}"
    _pif = photoism_ip_daily()
    _pif = _pif[_pif["IP구분"].isin(["아티스트", "캐릭터", "PICK"])]
    _spf = snapism_daily()
    _dom = _ext = 0
    for _d in (_pif, _spf):
        if _d.empty:
            continue
        _dm = _d[(_d["날짜"].dt.year == today.year) & (_d["날짜"].dt.month == today.month)]
        _dom += int(_dm[_dm["_kr"]]["매출액"].sum())
        _ext += int(_dm[~_dm["_kr"]]["매출액"].sum())
    _tot_fin  = _dom + _ext
    _ext_pct  = (_ext / _tot_fin * 100) if _tot_fin else 0
    _post     = _postperiod_mtd(_mtime(AGG_FILE), _mtime(CONFIG_FILE), _mtime(JIRA_CACHE), _ym)
    _post_pct = (_post / _tot_fin * 100) if _tot_fin else 0
    _post_txt = (f" · ⚠️ 이 중 기간 후 매출 {fmt_krw(_post)}({_post_pct:.1f}%) 포함"
                 if _post > 0 else "")
    st.caption(
        f"💰 재무 관점(전체 기준) · 합계는 **명목 거래액**(쿠폰·서비스코인 포함, "
        f"A·C·픽=매출·스내피즘=정산금액 혼합) · 해외 {_ext_pct:.0f}%{_post_txt} · "
        f"실수령(정산 후 자사 귀속)은 💰 IP정산현황, 기간 후 매출 상세는 ⚠️ 기간 후 매출분석에서 보세요."
    )
except Exception:
    pass

# ══ 이번 달 IP 무버 (Top/Bottom) ════════════════════════════════
#   팀 합계로는 안 보이는 '어느 IP가 올리고 빠졌나'를 IP 단위로 — 회의 원인 분석용.
#   포토이즘(A팀=아티스트·C팀=캐릭터·픽=PICK) 한정, 전월 같은 기간(1일~오늘) 대비 증감액 기준.
#   요약이 실패해도 본 화면은 떠야 하므로 통째로 try/except 로 감싼다.
try:
    _PG  = {"아티스트": "A팀", "캐릭터": "C팀", "PICK": "픽"}
    _ipn = _seg_filter(photoism_ipname_daily(), _seg)
    _ipn = _ipn[_ipn["IP구분"].isin(_PG.keys())].copy()
    if not _ipn.empty:
        _pm2 = today.month - 1 if today.month > 1 else 12
        _py2 = today.year if today.month > 1 else today.year - 1
        _ipn["_y"] = _ipn["날짜"].dt.year
        _ipn["_m"] = _ipn["날짜"].dt.month
        _ipn["_d"] = _ipn["날짜"].dt.day
        _c = (_ipn[(_ipn["_y"] == today.year) & (_ipn["_m"] == today.month)]
              .groupby(["IP명", "IP구분"], as_index=False, observed=True)["매출액"].sum()
              .rename(columns={"매출액": "이번달"}))
        _p = (_ipn[(_ipn["_y"] == _py2) & (_ipn["_m"] == _pm2) & (_ipn["_d"] <= today.day)]
              .groupby(["IP명", "IP구분"], as_index=False, observed=True)["매출액"].sum()
              .rename(columns={"매출액": "전월동기"}))
        # 같은 IP명이 구좌에 따라 여러 IP구분에 걸칠 수 있어 IP명+IP구분으로 매칭(중복 합산 방지)
        _mv = _c.merge(_p, on=["IP명", "IP구분"], how="outer")
        _mv["이번달"]   = _mv["이번달"].fillna(0)
        _mv["전월동기"] = _mv["전월동기"].fillna(0)
        _mv["IP구분"]   = _mv["IP구분"].fillna("")
        _mv["증감"]     = _mv["이번달"] - _mv["전월동기"]
        # 신규: 데이터 전체에서 해당 IP명 첫 등장이 이번 달
        _fm  = _ipn.groupby("IP명", observed=True)["날짜"].min()
        _new = set(_fm[(_fm.dt.year == today.year) & (_fm.dt.month == today.month)].index)
        # IP 종료상태(Jira 종료일) — '빠진 IP'가 판매기간 종료 때문인지 vs 아직 파는데 빠진 건지 구분
        _status = _ip_end_status(_mtime(AGG_FILE), _mtime(JIRA_CACHE),
                                 f"{today.year}-{today.month:02d}")

        def _eok2(v):
            return f"{v/1e8:,.1f}억"

        def _mv_table(d, status=False):
            out = pd.DataFrame()
            out["IP"]   = [("🆕 " if n in _new else "") + str(n) for n in d["IP명"]]
            out["팀"]   = [_PG.get(g, "—") if g else "—" for g in d["IP구분"]]
            if status:
                out["상태"] = [_status.get(str(n), "—") for n in d["IP명"]]
            out["이번달"] = [_eok2(v) for v in d["이번달"]]
            out["증감"]   = [f"{v/1e8:+,.1f}억" for v in d["증감"]]
            out["증감률"] = [
                ("—" if pv <= 0 else f"{(cv-pv)/pv*100:+.0f}%")
                for cv, pv in zip(d["이번달"], d["전월동기"])
            ]
            return out

        _N  = 7
        _up = _mv[_mv["증감"] > 0].sort_values("증감", ascending=False).head(_N)
        _dn = _mv[_mv["증감"] < 0].sort_values("증감", ascending=True).head(_N)

        st.markdown('<div class="section-title">이번 달 IP 무버 — 어디서 오르고 빠졌나</div>',
                    unsafe_allow_html=True)
        _mc1, _mc2 = st.columns(2)
        with _mc1:
            st.markdown("**📈 가장 많이 오른 IP**")
            if _up.empty:
                st.caption("증가한 IP가 없어요.")
            else:
                st.dataframe(_mv_table(_up), hide_index=True, use_container_width=True)
        with _mc2:
            st.markdown("**📉 가장 많이 빠진 IP**")
            if _dn.empty:
                st.caption("감소한 IP가 없어요.")
            else:
                st.dataframe(_mv_table(_dn, status=True), hide_index=True, use_container_width=True)
        st.caption("※ 전월 같은 기간(1일~오늘) 대비 증감액 순 · 포토이즘 A·C·픽 IP만 · 🆕=이번 달 첫 등장 · "
                   "‘—’(증감률)=전월 같은 기간 매출 없음. "
                   "상태: 🔚 종료=판매기간이 끝난 IP(예정된 하락) · 🔴 판매중=아직 파는데 빠짐(점검 필요) · —=종료일 미확인. "
                   "IP별 일자/국가/단가 세부는 📸 포토이즘 페이지에서 보세요.")
except Exception:
    pass

tab_all, tab_team, tab_weekly, tab_yoy = st.tabs([
    "📊 전체", "👥 팀별", "📅 주차별", "📈 전년비 (전사)"
])


# ════════════════════════════════════════════════════════════
# TAB 1 — 전체 (A팀·C팀·스내피즘 합산 실적)
# ════════════════════════════════════════════════════════════
with tab_all:
    with st.container(border=True):
        div = division_monthly(_seg)
        st.caption(
            f"실적 기준: **포토이즘 CMS(IP구분) + 스내피즘 실시간**  ·  "
            f"지역: **{_seg}**  ·  목표·달성률은 준비 중이라 지금은 실적만 보여요. (새로고침 F5)"
        )

        if div.empty:
            st.warning("아직 표시할 실적이 없어요. 포토이즘·스내피즘 데이터가 들어왔는지 확인해 주세요.")
            st.stop()

        div["연월"] = div["연도"].astype(str) + "-" + div["월"].apply(lambda x: f"{x:02d}")

        # 이번 달 합계·팀/항목 스냅샷은 화면 맨 위 '벤토 요약'으로 일원화(중복 제거).
        # 이 탭은 추이·요약표에 집중한다.

        # ── 월별 실적 추이 (팀/항목 스택 + 합계 라인) ──
        st.markdown('<div class="section-title">월별 실적 추이 (A팀·C팀·픽·스내피즘)</div>', unsafe_allow_html=True)
        order_ym = sorted(div["연월"].unique())
        fig_m = go.Figure()
        for seg_name in DIV_ORDER:
            d = (div[div["팀/항목"] == seg_name]
                 .set_index("연월").reindex(order_ym)["실적"].fillna(0))
            fig_m.add_trace(go.Bar(
                x=order_ym, y=d.values, name=DIV_LABEL[seg_name],
                marker_color=DIV_COLORS[seg_name],
                hovertemplate=f"{DIV_LABEL[seg_name]}<br>%{{x}}<br>%{{y:,}}원<extra></extra>",
            ))
        tot_ym = div.groupby("연월")["실적"].sum().reindex(order_ym).fillna(0)
        fig_m.add_trace(go.Scatter(
            x=order_ym, y=tot_ym.values, name="합계",
            mode="lines+markers+text",
            text=[fmt_krw(v) for v in tot_ym.values],
            textposition="top center", textfont=dict(size=10, color="#1a1a2e"),
            line=dict(color="#1a1a2e", width=2), marker=dict(size=7),
            hovertemplate="%{x}<br>합계: %{y:,}원<extra></extra>",
        ))
        fig_m.update_layout(height=440, barmode="stack",
                            yaxis=dict(tickformat=",", title="실적 (KRW)"),
                            legend=dict(orientation="h", y=1.1), margin=dict(t=20, b=0))
        st.plotly_chart(fig_m, use_container_width=True)

        # ── 월별 요약 테이블 ──
        st.markdown('<div class="section-title">월별 실적 요약</div>', unsafe_allow_html=True)
        piv = div.pivot_table(index="연월", columns="팀/항목", values="실적", aggfunc="sum").fillna(0)
        for s in DIV_ORDER:
            if s not in piv.columns:
                piv[s] = 0
        piv = piv[DIV_ORDER]
        piv["합계"] = piv.sum(axis=1)
        disp = piv.map(lambda x: fmt_krw(x) if x > 0 else "—")
        disp.columns = [DIV_LABEL.get(c, c) for c in piv.columns]
        st.dataframe(disp, use_container_width=True, height=min(480, len(disp)*40 + 55))
        st.caption("※ A팀=포토이즘 아티스트 IP · C팀=포토이즘 캐릭터 IP · 픽=PICK · 스내피즘=정산금액(실시간). "
                   "렌탈·기획(P)·제외는 미포함.")


    # ════════════════════════════════════════════════════════════
    # TAB 2 — 팀별 (팀/항목별 실적 상세)
    # ════════════════════════════════════════════════════════════
with tab_team:
    with st.container(border=True):
        div = division_monthly(_seg)

        if div.empty:
            st.info("아직 표시할 실적이 없어요. 데이터가 들어오면 팀별 실적이 보여요.")
        else:
            div["연월"] = div["연도"].astype(str) + "-" + div["월"].apply(lambda x: f"{x:02d}")
            prev_m = today.month - 1 if today.month > 1 else 12
            prev_y = today.year if today.month > 1 else today.year - 1
            cur  = div[(div["연도"] == today.year) & (div["월"] == today.month)]
            prev = div[(div["연도"] == prev_y) & (div["월"] == prev_m)]

            st.markdown(f"#### {today.year}년 {today.month}월 팀/항목별 실적")
            cols = st.columns(len(DIV_ORDER))
            for i, seg_name in enumerate(DIV_ORDER):
                cv = int(cur[cur["팀/항목"] == seg_name]["실적"].sum())
                pv = int(prev[prev["팀/항목"] == seg_name]["실적"].sum())
                cols[i].metric(
                    DIV_LABEL[seg_name], fmt_krw(cv),
                    f"{(cv-pv)/pv*100:+.1f}% 전월비" if pv > 0 else "",
                )
            st.divider()

            st.markdown('<div class="section-title">월별 팀/항목별 실적</div>', unsafe_allow_html=True)
            order_ym = sorted(div["연월"].unique())
            fig_t = go.Figure()
            for seg_name in DIV_ORDER:
                d = (div[div["팀/항목"] == seg_name]
                     .set_index("연월").reindex(order_ym)["실적"].fillna(0))
                fig_t.add_trace(go.Bar(
                    x=order_ym, y=d.values, name=DIV_LABEL[seg_name],
                    marker_color=DIV_COLORS[seg_name],
                    text=[fmt_krw(v) if v > 0 else "" for v in d.values],
                    textposition="outside", textfont=dict(size=9),
                    hovertemplate=f"{DIV_LABEL[seg_name]}<br>%{{x}}<br>%{{y:,}}원<extra></extra>",
                ))
            fig_t.update_layout(height=420, barmode="group",
                                yaxis=dict(tickformat=",", title="실적 (KRW)"),
                                legend=dict(orientation="h", y=1.1), margin=dict(t=20, b=0))
            st.plotly_chart(fig_t, use_container_width=True)

            st.markdown('<div class="section-title">팀/항목별 누적 요약</div>', unsafe_allow_html=True)
            rows = []
            for seg_name in DIV_ORDER:
                tot = int(div[div["팀/항목"] == seg_name]["실적"].sum())
                rows.append({"팀/항목": DIV_LABEL[seg_name], "누적 실적": fmt_krw(tot)})
            rows.append({"팀/항목": "합계", "누적 실적": fmt_krw(int(div["실적"].sum()))})
            st.dataframe(pd.DataFrame(rows).set_index("팀/항목"), use_container_width=True, height=210)
            st.caption("※ A팀=포토이즘 아티스트 IP · C팀=포토이즘 캐릭터 IP · 픽=PICK · 스내피즘=정산금액(실시간). "
                       "렌탈·기획(P)·제외는 미포함.")


    # ════════════════════════════════════════════════════════════
    # TAB 3 — 주차별
    # ════════════════════════════════════════════════════════════
with tab_weekly:
    with st.container(border=True):
        wk = division_weekly(_seg, weeks=14)

        if wk.empty:
            st.info("아직 주차별 실적이 없어요. 데이터가 쌓이면 주차별 추이가 보여요.")
        else:
            st.caption(f"주차 = 월~일 기준  ·  지역: **{_seg}**  ·  "
                       "팀/항목: A팀(아티스트)·C팀(캐릭터)·픽(PICK)·스내피즘 합산")
            tot_wk = (wk.groupby(["주시작", "주차"], as_index=False)["실적"].sum()
                        .sort_values("주시작"))
            order_w = tot_wk["주차"].tolist()

            # KPI 카드: 최근 주 / 전주 / 전주대비 / 누적
            if len(tot_wk) >= 2:
                curr_w_val = int(tot_wk.iloc[-1]["실적"]); curr_w_lbl = tot_wk.iloc[-1]["주차"]
                prev_w_val = int(tot_wk.iloc[-2]["실적"]); prev_w_lbl = tot_wk.iloc[-2]["주차"]
                wow = (curr_w_val - prev_w_val) / prev_w_val * 100 if prev_w_val > 0 else 0

                c1, c2, c3, c4 = st.columns(4)
                c1.metric(f"최근 주 ({curr_w_lbl})", fmt_krw(curr_w_val), f"전주 대비 {wow:+.1f}%",
                          delta_color="normal" if wow >= 0 else "inverse")
                c2.metric(f"전주 ({prev_w_lbl})", fmt_krw(prev_w_val))
                c3.metric("전주 대비", f"{wow:+.1f}%",
                          "↑ 증가" if wow >= 0 else "↓ 감소",
                          delta_color="normal" if wow >= 0 else "inverse")
                c4.metric("최근 14주 합계", fmt_krw(int(tot_wk["실적"].sum())))
                st.divider()

            # 주차별 팀/항목 스택 바 + 합계 3주 이동평균
            st.markdown('<div class="section-title">주차별 실적 추이 (팀/항목 스택)</div>', unsafe_allow_html=True)
            fig_w = go.Figure()
            for seg_name in DIV_ORDER:
                d = (wk[wk["팀/항목"] == seg_name]
                     .set_index("주차").reindex(order_w)["실적"].fillna(0))
                fig_w.add_trace(go.Bar(
                    x=order_w, y=d.values, name=DIV_LABEL[seg_name],
                    marker_color=DIV_COLORS[seg_name],
                    hovertemplate=f"{DIV_LABEL[seg_name]} %{{x}}<br>%{{y:,}}원<extra></extra>",
                ))
            if len(tot_wk) >= 3:
                ma = tot_wk["실적"].rolling(3, min_periods=1).mean()
                fig_w.add_trace(go.Scatter(
                    x=order_w, y=ma.values, name="합계 3주 이동평균",
                    mode="lines", line=dict(color="#1a1a2e", width=2, dash="dot"),
                    hovertemplate="%{x}<br>이동평균: %{y:,.0f}원<extra></extra>",
                ))
            fig_w.update_layout(
                height=460, barmode="stack",
                yaxis=dict(tickformat=",", title="실적 (KRW)"),
                xaxis=dict(title="", tickangle=-45, tickfont=dict(size=10)),
                legend=dict(orientation="h", y=1.08),
                margin=dict(t=30, b=70),
            )
            st.plotly_chart(fig_w, use_container_width=True)

            # 주차별 상세 (팀/항목 피벗)
            st.markdown('<div class="section-title">주차별 상세</div>', unsafe_allow_html=True)
            piv_w = (wk.pivot_table(index="주차", columns="팀/항목", values="실적", aggfunc="sum")
                       .reindex(order_w).fillna(0))
            for s in DIV_ORDER:
                if s not in piv_w.columns:
                    piv_w[s] = 0
            piv_w = piv_w[DIV_ORDER]
            piv_w["합계"] = piv_w.sum(axis=1)
            disp_w = piv_w.map(lambda x: fmt_krw(x) if x > 0 else "—")
            disp_w.columns = [DIV_LABEL.get(c, c) for c in piv_w.columns]
            st.dataframe(disp_w, use_container_width=True, height=min(560, len(disp_w)*38 + 55))


# ════════════════════════════════════════════════════════════
# TAB 4 — 전년비 (전사 TTL 기준) · 25년 엑셀 vs 26년 CMS
# ════════════════════════════════════════════════════════════
with tab_yoy:
    with st.container(border=True):
        st.caption("⚠️ **전사(포토이즘 TTL) 기준** — 앞 탭(A·C·스내피즘)과 집계 범위가 다릅니다. "
                   "25년=IPX 엑셀 확정값 · 26년=엑셀(확정월)+CMS 실시간.")
        yoy_df    = load_yoy()
        tgt_26    = load_targets(_seg)
        act_26, _ = load_monthly_actual(_seg)

        if yoy_df.empty:
            st.info("아직 25년 비교 데이터가 없어요. 사이드바에서 **IPX MASTER DATA.xlsx**를 올려 주세요.")
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
