"""
KPI 목표 달성률 대시보드  (상단 요약 + 4탭)
  상단 요약(벤토)— 합계·팀/항목 카드(목표 달성률·월말 예상), 카드 클릭 시 IP 상세,
                  재무 한 줄·이번 달 IP 무버(둘 다 접기)
  탭1 📊 전체     — A팀+C팀+픽+스내피즘 합산 월별 추이·요약표(누적 합계 행)
  탭2 👥 팀별     — A/C팀=포토이즘+스내피즘(카테고리) 합산·월별 목표 점선, 픽 별도
  탭3 📅 주차별   — 최근 14주(월~일) + 국내·해외 분리·국가별 매출 비중
  탭4 📈 전년비   — 전사(포토이즘 TTL) 기준 25 vs 26 (엑셀 25년 vs CMS 26년).
                  ※ 앞 3탭(A/C/스내피즘)과 집계 범위가 다름 — 2025 CMS·스내피즘
                    데이터가 없어 전년비는 전사 TTL 기준으로만 가능.

실적 산출(실시간):
  · A팀=포토이즘 CMS IP구분 '아티스트', C팀='캐릭터' (KRW 환산, 취소 제외)
  · 픽=IP구분 'PICK' (A·C로 안 나뉘어 하나로 묶은 독립 팀/항목)
  · 스내피즘=master.csv 정산금액(KRW환산+쿠폰, 취소 제외)
  ※ 렌탈·기획(P)·제외는 미포함 (TEAM_GUBUN 확장 지점)

파일 관리(소유자 전용): IPX MASTER DATA.xlsx 업로드 시 목표/실적/주차 CSV 파싱·저장.
  사이드바 '목표 직접 수정'으로 화면에서 월별 목표 편집 가능. 목표 달성률은 A·C팀만(스코프 일치).
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

INK = "#1b2330"; PRIMARY = "#4f46e5"; SECONDARY = "#6366f1"; PINK = "#d24d8b"
st.markdown("""
<style>
@import url("https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/variable/pretendardvariable-dynamic-subset.min.css");
:root{
  --bg:#f4f5f7; --surface:#fff; --surface-2:#f8fafc; --surface-3:#eef1f5;
  --border:#e7e9ee; --border-strong:#d7dae1;
  --text:#1b2330; --text-2:#5b6573; --text-3:#98a0af;
  --brand:#4f46e5; --brand-2:#6366f1; --brand-soft:#eef0fe;
  --green:#15803d; --red:#c0322b; --amber:#b45309; --teal:#0f9d77; --sky:#38a3e8;
}
/* Pretendard 강제 (시안 톤) */
html, body, [class*="css"], [data-testid="stAppViewContainer"], [data-testid="stSidebar"],
button, input, select, textarea, label, p, span, div, h1, h2, h3, h4, li, a,
[data-baseweb], [data-testid="stMarkdownContainer"], [data-testid="stMetricValue"]{
  font-family:'Pretendard Variable','Pretendard',-apple-system,BlinkMacSystemFont,
              'Segoe UI','Malgun Gothic','Apple SD Gothic Neo',sans-serif !important;
}
html, body{ letter-spacing:-0.02em; }
.stApp, [data-testid="stAppViewContainer"], [data-testid="stMain"], .stMain, section.main{ background:var(--bg) !important; }
[data-testid="stMainBlockContainer"], .block-container{ background:transparent !important;
  max-width:1680px !important; padding-top:1.6rem !important; padding-bottom:3rem !important; }
h1{ font-size:24px !important; font-weight:800 !important; letter-spacing:-0.03em !important; color:var(--text) !important; }
h2,h3{ letter-spacing:-0.02em !important; }
[data-testid="stCaptionContainer"], [data-testid="stCaptionContainer"] p{ font-size:13.5px !important; color:#8b95a1 !important; }
[data-testid="stDeployButton"], [data-testid="stElementToolbar"]{ display:none !important; }

/* 카드 = st.container(border=True) → 시안 카드(중첩은 테두리 제거) */
[data-testid="stMain"] [data-testid="stVerticalBlockBorderWrapper"]{
  border:1px solid var(--border) !important; border-radius:14px !important; background:#fff !important;
  box-shadow:0 1px 2px rgba(20,28,45,.04),0 1px 3px rgba(20,28,45,.06) !important;
  padding:16px 20px !important; margin-bottom:14px !important; }
[data-testid="stMain"] [data-testid="stVerticalBlockBorderWrapper"] [data-testid="stVerticalBlockBorderWrapper"]{
  border:none !important; box-shadow:none !important; padding:0 !important; margin:0 !important; background:transparent !important; }

/* 섹션 제목 = 인디고 좌측 액센트 */
.section-title{ font-size:1.02rem !important; font-weight:800 !important; color:var(--text) !important;
  margin:6px 0 13px !important; padding-left:11px !important; border-left:4px solid var(--brand) !important;
  line-height:1.4 !important; letter-spacing:-0.02em !important; }
.section-title.purple{ border-left-color:var(--brand-2) !important; }
.section-title.pink{ border-left-color:#d24d8b !important; }
.sub-label{ font-weight:700 !important; color:var(--text-2) !important; margin:14px 0 6px !important;
  padding-left:9px !important; border-left:3px solid #c7cbf3 !important; }

/* st.metric = 시안 톤 카드 */
[data-testid="stMetric"], [data-testid="metric-container"]{ background:var(--surface) !important;
  border:1px solid var(--border) !important; border-radius:12px !important; padding:14px 18px !important;
  box-shadow:0 1px 2px rgba(20,28,45,.04),0 1px 3px rgba(20,28,45,.06) !important; }
[data-testid="stMetricLabel"] p{ font-size:12.5px !important; font-weight:600 !important; color:var(--text-2) !important; }
[data-testid="stMetricValue"]{ font-size:1.7rem !important; font-weight:800 !important; color:var(--text) !important; letter-spacing:-0.02em !important; }
[data-testid="stMetricDelta"]{ font-size:12px !important; }

/* 탭 = 시안 언더라인 */
[data-baseweb="tab-list"]{ gap:2px; border-bottom:1px solid var(--border); }
button[data-baseweb="tab"]{ padding:10px 15px; }
button[data-baseweb="tab"] p{ font-size:14px !important; font-weight:700 !important; color:var(--text-2) !important; }
button[data-baseweb="tab"][aria-selected="true"] p{ color:var(--brand) !important; }
[data-baseweb="tab-highlight"]{ background:var(--brand) !important; height:2.5px !important; }
[data-baseweb="tab-list"] button[data-baseweb="tab"]:first-child{ background:var(--brand-soft) !important; border-radius:9px 9px 0 0 !important; }
[data-baseweb="tab-list"] button[data-baseweb="tab"]:first-child p{ color:var(--brand) !important; }

/* 컴팩트 위젯 */
[data-testid="stSelectbox"] div[data-baseweb="select"] > div:first-child{
  min-height:34px !important; background:var(--surface-2) !important;
  border:1px solid var(--border-strong) !important; border-radius:8px !important; }
[data-testid="stButtonGroup"]{ background:var(--surface-3) !important; border-radius:8px !important; padding:2px !important; }
[data-testid="stButtonGroup"] button{ border:none !important; background:transparent !important; box-shadow:none !important; }
[data-testid="stButtonGroup"] button[kind="segmented_controlActive"]{ background:var(--surface) !important; box-shadow:0 1px 3px rgba(20,28,45,.08) !important; }
[data-testid="stButtonGroup"] button[kind="segmented_controlActive"] p{ color:var(--brand) !important; font-weight:700 !important; }
[data-testid="stDataFrame"]{ border-radius:12px; overflow:hidden; }
hr{ margin:1.2rem 0 !important; border:none !important; border-top:1px solid var(--border) !important; }
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


def _clear_target_caches():
    """목표/실적 CSV 관련 캐시만 무효화. 전역 st.cache_data.clear() 는 접속 중인 모든
    사용자의 무거운 매출 캐시(포토이즘 agg·스내피즘 master 등)까지 날려 동시 재로딩(프리징)을
    유발하므로 쓰지 않는다. 무거운 캐시는 파일 mtime 키라 원본이 바뀌면 알아서 무효화됨."""
    for _f in (load_targets, load_monthly_actual, load_yoy):
        try:
            _f.clear()
        except Exception:
            pass


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
DIV_COLORS = {"A팀": "#6366f1", "C팀": "#0f9d77", "픽": "#b45309", "스내피즘": "#38a3e8"}
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


def _region_daily() -> pd.DataFrame:
    """일별 국내/해외 매출 (포토이즘 A·C·픽 + 스내피즘 합산). 날짜·_kr·매출액.
    지역 필터와 무관하게 항상 전체(국내+해외)를 담는다 — 국내/해외 비교가 목적."""
    frames = []
    ip = photoism_ip_daily()
    ip = ip[ip["IP구분"].isin(["아티스트", "캐릭터", "PICK"])]
    if not ip.empty:
        frames.append(ip[["날짜", "_kr", "매출액"]])
    sp = snapism_daily()
    if not sp.empty:
        frames.append(sp[["날짜", "_kr", "매출액"]])
    if not frames:
        return pd.DataFrame(columns=["날짜", "_kr", "매출액"])
    out = pd.concat(frames, ignore_index=True)
    out["날짜"] = pd.to_datetime(out["날짜"])
    return out


@st.cache_data(show_spinner=False)
def _photoism_country_since(_agg_mtime, _cfg_mtime, _since: str) -> pd.DataFrame:
    """포토이즘(A·C·픽) 국가별 매출액 — _since(YYYY-MM-DD) 이후. 매출 산식은 벤토와 동일."""
    cols = ["국가", "매출액"]
    if not AGG_FILE.exists():
        return pd.DataFrame(columns=cols)
    try:
        df = pq.read_table(str(AGG_FILE), columns=[
            "날짜", "IP구분", "국가", "국가코드", "취소 여부", "결제 단위",
            "최종 결제 금액", "쿠폰 할인 금액", "서비스코인"]).to_pandas()
    except Exception:
        return pd.DataFrame(columns=cols)
    df["날짜"] = pd.to_datetime(df["날짜"], errors="coerce")
    df = df[df["날짜"].notna() & ~df["취소 여부"].astype(bool)]
    df = df[df["날짜"] >= pd.Timestamp(_since)]
    df = df[df["IP구분"].astype(str).isin(["아티스트", "캐릭터", "PICK"])]
    if df.empty:
        return pd.DataFrame(columns=cols)
    ex   = load_exchange_rates()
    rate = df["결제 단위"].astype(str).str.strip().replace("nan", "KRW").map(ex).fillna(1)
    cc   = df["국가코드"].astype(str).str.lower().str.strip()
    for c in ["최종 결제 금액", "쿠폰 할인 금액", "서비스코인"]:
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)
    df["매출액"] = ((df["최종 결제 금액"] * rate).round(0)
        + (df["쿠폰 할인 금액"] * rate).round(0) * cc.isin(_COUPON_CC)
        + (df["서비스코인"]     * rate).round(0) * cc.isin(_COIN_CC))
    out = df.groupby("국가", as_index=False, observed=True)["매출액"].sum()
    out["매출액"] = out["매출액"].astype("int64")
    return out.sort_values("매출액", ascending=False)


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


@st.cache_data(show_spinner=False)
def _snapism_team_daily(_m_csv, _m_parq, _cfg_mtime) -> pd.DataFrame:
    """스내피즘 master → 날짜·국내여부·팀별 정산금액. 카테고리로 팀 배정:
    아티스트·기타 → A팀, 캐릭터 → C팀. (산식은 _snapism_daily 와 동일)"""
    import data_io
    cols = ["날짜", "_kr", "팀", "매출액"]
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
    cat  = df.get("카테고리", "").astype(str).str.strip()
    team = pd.Series("A팀", index=df.index)
    team[cat == "캐릭터"] = "C팀"
    base = pd.DataFrame({
        "날짜":   df["날짜"].dt.normalize().values,
        "_kr":   df.get("소스", "").astype(str).str.strip().eq("한국").values,
        "팀":    team.values,
        "매출액": amt.values,
    })[keep.values]
    return base.groupby(["날짜", "_kr", "팀"], as_index=False)["매출액"].sum()


def snapism_team_daily() -> pd.DataFrame:
    return _snapism_team_daily(
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


def team_monthly_src(seg: str = "TTL") -> pd.DataFrame:
    """월별 팀(A/C/픽)·출처(포토이즘/스내피즘)별 실적.
    A·C팀 = 포토이즘 IP구분 + 스내피즘(카테고리 기준) 합산. 픽 = 포토이즘 PICK만."""
    frames = []
    _pmap = {"아티스트": "A팀", "캐릭터": "C팀", "PICK": "픽"}
    ip = _seg_filter(photoism_ip_daily(), seg)
    ip = ip[ip["IP구분"].isin(_pmap)].copy()
    if not ip.empty:
        ip["팀"]  = ip["IP구분"].map(_pmap)
        ip["출처"] = "포토이즘"
        frames.append(ip[["날짜", "팀", "출처", "_kr", "매출액"]])
    sp = _seg_filter(snapism_team_daily(), seg)
    if not sp.empty:
        sp = sp.copy()
        sp["출처"] = "스내피즘"
        frames.append(sp[["날짜", "팀", "출처", "_kr", "매출액"]])
    if not frames:
        return pd.DataFrame(columns=["연도", "월", "팀", "출처", "_kr", "실적"])
    out = pd.concat(frames, ignore_index=True)
    out["날짜"] = pd.to_datetime(out["날짜"])
    out["연도"] = out["날짜"].dt.year
    out["월"]   = out["날짜"].dt.month
    g = (out.groupby(["연도", "월", "팀", "출처", "_kr"], as_index=False)["매출액"].sum()
         .rename(columns={"매출액": "실적"}))
    g["실적"] = pd.to_numeric(g["실적"], errors="coerce").fillna(0).astype("int64")
    return g[g["연도"] > 0]


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

            _clear_target_caches()
            st.rerun()

        elif ext == ".csv":
            new_tgt = pd.read_csv(uploaded, encoding="utf-8-sig")
            if not {"연도","월","매출목표"}.issubset(new_tgt.columns):
                st.sidebar.error("CSV에 연도·월·매출목표 컬럼이 필요해요. 세 컬럼을 채워 다시 올려 주세요.")
            else:
                new_tgt.to_csv(KPI_FILE, index=False, encoding="utf-8-sig")
                st.sidebar.success("✅ 목표 CSV를 저장했어요. 대시보드에 바로 반영돼요.")
                _clear_target_caches()
                st.rerun()

    except Exception as e:
        st.sidebar.error(f"파일을 처리하지 못했어요. 파일 형식을 확인한 뒤 다시 올려 주세요. ({e})")

if _is_owner:
    if ACTUALS_FILE.exists() and st.sidebar.button("🗑 엑셀 실적 초기화 (CMS로 전환)"):
        ACTUALS_FILE.unlink()
        _clear_target_caches()
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
            _clear_target_caches()
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
            pc   = "#15803d" if proj >= 100 else ("#b45309" if proj >= 85 else "#c0322b")
            _fill = min(100, max(2, mtd))     # 달성 채움(0~100 시각, 초과는 100)
            _mark = min(99, max(0, proj))     # 월말 예상 마커 위치
            return (f'<div class="kbz-goal">🎯 목표 {fmt_krw(tg)} · 달성 <b style="color:{pc}">{mtd:.0f}%</b> '
                    f'<span style="color:{pc};">· 예상 {proj:.0f}%</span></div>'
                    f'<div class="kbz-gauge"><i style="width:{_fill:.0f}%;background:{pc};"></i>'
                    f'<u style="left:{_mark:.0f}%;" title="월말 예상 {proj:.0f}%"></u></div>')

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
            # 타일 전체를 링크로 — 클릭 시 ?focus=<area> 로 그 팀/항목 상세를 연다
            _tiles += (
                f'<a href="?focus={_areas.get(_s, "")}" target="_self" class="kbz-tile" '
                f'style="grid-area:{_areas.get(_s, "")};'
                f'border-left:6px solid {DIV_COLORS.get(_s, "#888")};">'
                f'<div class="kbz-row"><span class="kbz-dot" style="background:{DIV_COLORS.get(_s, "#888")};"></span>'
                f'<span class="kbz-lbl">{DIV_LABEL.get(_s, _s)}</span>'
                f'<span class="kbz-go">상세 ›</span></div>'
                f'<div class="kbz-val">{fmt_krw(_cv)}</div>'
                f'{_delta(_cv, _pv)}'
                f'{_goal_line(_cv, _s)}</a>'
            )
        _seg_note = "" if _seg == "TTL" else f" · {_seg}"
        st.markdown(f"""
<style>
#kpi-bento .kbz-grid{{display:grid;grid-template-columns:1.7fr 1fr 1fr;gap:12px;
  grid-template-areas:"hero a c" "hero pick snap";margin:4px 0 6px;}}
#kpi-bento .kbz-tile{{background:#ffffff;border:1px solid #e7e9ee;border-radius:14px;
  padding:16px 18px;box-shadow:0 1px 2px rgba(20,28,45,.04),0 1px 3px rgba(20,28,45,.06);min-height:104px;
  display:flex;flex-direction:column;justify-content:center;}}
#kpi-bento .kbz-hero{{grid-area:hero;background:linear-gradient(180deg,#fbfbff,#f4f5ff);
  border:1px solid #dcdcfb;padding:22px 26px;justify-content:center;}}
#kpi-bento .kbz-row{{display:flex;align-items:center;gap:6px;margin-bottom:7px;}}
#kpi-bento .kbz-dot{{width:10px;height:10px;border-radius:3px;display:inline-block;}}
#kpi-bento .kbz-lbl{{font-size:12.5px;color:#5b6573;font-weight:600;}}
#kpi-bento .kbz-val{{font-size:1.3rem;font-weight:800;color:#1b2330;
  letter-spacing:-0.02em;line-height:1.1;margin-bottom:5px;}}
#kpi-bento .kbz-bar{{display:flex;height:13px;border-radius:6px;overflow:hidden;
  gap:2px;margin-top:16px;}}
#kpi-bento .kbz-bar>div{{height:100%;}}
#kpi-bento .kbz-leg{{display:flex;flex-wrap:wrap;gap:14px;margin-top:11px;}}
#kpi-bento .kbz-leg span{{font-size:12px;color:#5b6573;display:flex;align-items:center;gap:6px;}}
#kpi-bento .kbz-goal{{font-size:11px;color:#5b6573;font-weight:600;margin-top:8px;}}
#kpi-bento .kbz-goal.muted{{color:#98a0af;font-weight:500;}}
/* 목표 달성 미니 게이지 (채움=현재 달성률, 세로 마커=월말 예상) */
#kpi-bento .kbz-gauge{{position:relative;height:6px;background:#eef1f5;border-radius:4px;margin-top:6px;}}
#kpi-bento .kbz-gauge i{{display:block;height:100%;border-radius:4px;transition:width .3s ease;}}
#kpi-bento .kbz-gauge u{{position:absolute;top:-2px;width:2px;height:10px;background:#1b2330;
  border-radius:1px;opacity:.65;}}
#kpi-bento .kbz-rr{{font-size:13px;color:#4f46e5;font-weight:700;margin-top:9px;}}
#kpi-bento .kbz-rr span{{font-weight:500;opacity:.7;}}
#kpi-bento a.kbz-tile{{text-decoration:none;color:inherit;cursor:pointer;
  transition:transform .08s ease, box-shadow .08s ease;}}
#kpi-bento a.kbz-tile:hover{{transform:translateY(-2px);
  box-shadow:0 6px 18px rgba(79,70,229,0.14);}}
#kpi-bento .kbz-go{{font-size:11px;color:#98a0af;font-weight:600;margin-left:auto;}}
#kpi-bento a.kbz-tile:hover .kbz-go{{color:#4f46e5;}}
@media (max-width:760px){{#kpi-bento .kbz-grid{{grid-template-columns:1fr 1fr;
  grid-template-areas:"hero hero" "a c" "pick snap";}}}}
</style>
<div id="kpi-bento"><div class="kbz-grid">
  <div class="kbz-tile kbz-hero">
    <div style="font-size:12.5px;color:#5b6573;font-weight:600;">{today.month}월 합계 실적
      <span style="color:#98a0af;">(1일~{today.day}일 · 진행 중){_seg_note}</span></div>
    <div style="font-size:2.1rem;font-weight:800;color:#4f46e5;letter-spacing:-0.02em;line-height:1.05;margin:7px 0 6px;">
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
        st.caption("👆 카드를 클릭하면 그 팀/항목의 이번 달 IP가 아래에 펼쳐져요. "
                   "※ 진행 중인 달이라 ‘전월 같은 기간(1일~오늘)’과 비교해요. "
                   "‘월말 예상’은 현재 일평균 × 당월 일수로 단순 추정한 값이에요. "
                   "목표 달성률은 목표가 같은 범위로 잡힌 A팀·C팀만 표시해요(픽·스내피즘은 목표 미설정). "
                   "정밀 숫자·월별 추이는 아래 탭에서 보세요.")
except Exception:
    pass

# ── 카드 클릭 시 상세: 그 팀/항목의 이번 달 IP (?focus=<area>) ──────
_AREA2SEG = {"a": "A팀", "c": "C팀", "pick": "픽", "snap": "스내피즘"}
try:
    _focus = _AREA2SEG.get(str(st.query_params.get("focus", "")))
except Exception:
    _focus = None
if _focus:
    try:
        with st.container(border=True):
            _fc1, _fc2 = st.columns([6, 1])
            _fc1.markdown(f"#### 🔎 {DIV_LABEL.get(_focus, _focus)} · 이번 달 상세")
            if _fc2.button("✕ 닫기", key="focus_close", use_container_width=True):
                st.query_params.clear()
                st.rerun()
            if _focus == "스내피즘":
                st.info("스내피즘은 IP별 분해가 없어요. 일자·정산 상세는 "
                        "📊 스내피즘 / 💰 IP정산현황 페이지에서 보세요.")
            else:
                _fg  = {"A팀": "아티스트", "C팀": "캐릭터", "픽": "PICK"}[_focus]
                _fi  = _seg_filter(photoism_ipname_daily(), _seg)
                _fi  = _fi[_fi["IP구분"].astype(str) == _fg].copy()
                if _fi.empty:
                    st.caption("이번 달 매출이 있는 IP가 없어요.")
                else:
                    _fi["_y"] = _fi["날짜"].dt.year
                    _fi["_m"] = _fi["날짜"].dt.month
                    _fi["_d"] = _fi["날짜"].dt.day
                    _pmf = today.month - 1 if today.month > 1 else 12
                    _pyf = today.year if today.month > 1 else today.year - 1
                    _fcur = (_fi[(_fi["_y"] == today.year) & (_fi["_m"] == today.month)]
                             .groupby("IP명", as_index=False, observed=True)["매출액"].sum()
                             .rename(columns={"매출액": "이번달"}))
                    _fprv = (_fi[(_fi["_y"] == _pyf) & (_fi["_m"] == _pmf) & (_fi["_d"] <= today.day)]
                             .groupby("IP명", as_index=False, observed=True)["매출액"].sum()
                             .rename(columns={"매출액": "전월동기"}))
                    _fmv = _fcur.merge(_fprv, on="IP명", how="outer")
                    _fmv["이번달"]   = _fmv["이번달"].fillna(0)
                    _fmv["전월동기"] = _fmv["전월동기"].fillna(0)
                    _fmv["증감"]     = _fmv["이번달"] - _fmv["전월동기"]
                    _fmv = _fmv[_fmv["이번달"] > 0].sort_values("이번달", ascending=False)
                    if _fmv.empty:
                        st.caption("이번 달 매출이 있는 IP가 없어요.")
                    else:
                        _stt = _ip_end_status(_mtime(AGG_FILE), _mtime(JIRA_CACHE),
                                              f"{today.year}-{today.month:02d}")
                        _ft = pd.DataFrame()
                        _ft["IP"]     = list(_fmv["IP명"].astype(str))
                        _ft["상태"]   = [_stt.get(n, "—") for n in _fmv["IP명"].astype(str)]
                        _ft["이번달"] = [fmt_krw(v) for v in _fmv["이번달"]]
                        _ft["전월동기"] = [fmt_krw(v) for v in _fmv["전월동기"]]
                        _ft["증감"]   = [f"{v/1e8:+,.1f}억" for v in _fmv["증감"]]
                        st.dataframe(_ft, hide_index=True, use_container_width=True,
                                     height=min(460, len(_ft) * 36 + 45))
                        st.caption(f"{DIV_LABEL.get(_focus, _focus)} 이번 달 IP {len(_ft)}개 · 매출 내림차순 · "
                                   "상태 🔚 종료 / 🔴 판매중 / — 미확인 · "
                                   "일자·국가·단가 세부는 📸 포토이즘 페이지에서 보세요.")
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

        with st.expander("📊 이번 달 IP 무버 — 어디서 오르고 빠졌나 (펼치기)", expanded=False):
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
            f"지역: **{_seg}**  ·  목표 달성률·월말 예상은 맨 위 요약 카드에서 봐요. (새로고침 F5)"
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
        # 맨 아래에 표시 기간 전체의 누적 합계 행 추가
        _piv_tot = piv.sum(axis=0)
        _piv_tot.name = "누적 합계"
        piv_all = pd.concat([piv, _piv_tot.to_frame().T])
        disp = piv_all.map(lambda x: fmt_krw(x) if x > 0 else "—")
        disp.columns = [DIV_LABEL.get(c, c) for c in piv_all.columns]
        st.dataframe(disp, use_container_width=True, height=min(520, len(disp)*40 + 55))
        st.caption("※ A팀=포토이즘 아티스트 IP · C팀=포토이즘 캐릭터 IP · 픽=PICK · 스내피즘=정산금액(실시간). "
                   "렌탈·기획(P)·제외는 미포함.")


    # ════════════════════════════════════════════════════════════
    # TAB 2 — 팀별 (팀/항목별 실적 상세)
    # ════════════════════════════════════════════════════════════
with tab_team:
    with st.container(border=True):
        tms = team_monthly_src("TTL")   # 팀별 탭은 항상 전체(국내+해외) — 사이드바 지역 필터와 무관

        if tms.empty:
            st.info("아직 표시할 실적이 없어요. 데이터가 들어오면 팀별 실적이 보여요.")
        else:
            tms["연월"] = tms["연도"].astype(str) + "-" + tms["월"].apply(lambda x: f"{x:02d}")
            order_ym = sorted(tms["연월"].unique())
            st.caption("아래 **하위 탭에서 팀별로** 봐요(국내+해외 전체 기준 · 사이드바 지역 필터와 무관). "
                       "A팀·C팀은 **포토이즘 + 스내피즘(카테고리 기준)** 합산이에요 "
                       "(스내피즘: 아티스트·기타→A팀, 캐릭터→C팀). "
                       "막대는 ‘지역(국내·해외)’ 또는 ‘출처(포토이즘·스내피즘)’로 바꿔 볼 수 있고, 점선은 팀 전체 월별 목표(A·C팀만)예요.")

            _SNAP_COLOR = {"A팀": "#c3aef0", "C팀": "#f9aecb"}  # 팀색 연한 톤

            def _render_team(team):
                sub = tms[tms["팀"] == team]
                if sub.empty:
                    st.info("이 팀/항목은 표시할 실적이 없어요.")
                    return
                # ── 기준 월 선택 (카드 박스가 선택한 달로 바뀜) ──
                _mon = st.selectbox(
                    "기준 월", order_ym, index=len(order_ym) - 1, key=f"teammonth_{team}",
                    format_func=lambda s: f"{int(s[:4])}년 {int(s[5:7])}월")
                _y, _m = int(_mon[:4]), int(_mon[5:7])
                _selm = sub[(sub["연도"] == _y) & (sub["월"] == _m)]
                _ct   = int(_selm["실적"].sum())
                _cdom = int(_selm[_selm["_kr"]]["실적"].sum())
                _cext = int(_selm[~_selm["_kr"]]["실적"].sum())
                _cum  = int(sub[sub["연월"] <= _mon]["실적"].sum())   # 선택 월까지 누적
                _tgt = None
                if team in ("A팀", "C팀"):
                    _t = load_targets(team)
                    _r = _t[(_t["연도"] == _y) & (_t["월"] == _m)]
                    if not _r.empty and int(_r["매출목표"].iloc[0]) > 0:
                        _tgt = int(_r["매출목표"].iloc[0])
                # ── 요약 지표 (선택 월 기준) ──
                _mc = st.columns(4 if _tgt else 2)
                _mc[0].metric(f"{_m}월 합계", fmt_krw(_ct),
                              f"국내 {fmt_krw(_cdom)} · 해외 {fmt_krw(_cext)}", delta_color="off")
                _mc[1].metric(f"누적 합계 (~{_m}월)", fmt_krw(_cum))
                if _tgt:
                    _mc[2].metric(f"{_m}월 목표(팀 전체)", fmt_krw(_tgt))
                    _mc[3].metric("달성률", f"{_ct/_tgt*100:.0f}%", delta_color="off")
                # ── 보기 토글: 지역 / 출처 ──
                _view = st.radio("막대 구분", ["지역 (국내·해외)", "출처 (포토이즘·스내피즘)"],
                                 horizontal=True, key=f"teamview_{team}")
                # ── 월별 차트 (선택 기준으로 스택) + 목표 점선 ──
                fig = go.Figure()
                if _view.startswith("지역"):
                    for _kr, _lbl, _col in [(True, "국내", "#4361ee"), (False, "해외", "#f9a826")]:
                        _d = (sub[sub["_kr"] == _kr].groupby("연월")["실적"].sum()
                              .reindex(order_ym).fillna(0))
                        fig.add_trace(go.Bar(x=order_ym, y=_d.values, name=_lbl, marker_color=_col,
                            hovertemplate=f"{_lbl} %{{x}}<br>%{{y:,}}원<extra></extra>"))
                else:
                    _srcs = [("포토이즘", DIV_COLORS.get(team, "#888888"))]
                    if int(sub[sub["출처"] == "스내피즘"]["실적"].sum()) > 0:
                        _srcs.append(("스내피즘", _SNAP_COLOR.get(team, "#cccccc")))
                    for _src, _col in _srcs:
                        _d = (sub[sub["출처"] == _src].groupby("연월")["실적"].sum()
                              .reindex(order_ym).fillna(0))
                        fig.add_trace(go.Bar(x=order_ym, y=_d.values, name=_src, marker_color=_col,
                            hovertemplate=f"{_src} %{{x}}<br>%{{y:,}}원<extra></extra>"))
                if team in ("A팀", "C팀"):
                    _tg  = load_targets(team)
                    _tgm = {f"{int(r['연도'])}-{int(r['월']):02d}": int(r['매출목표'])
                            for _, r in _tg.iterrows() if int(r['매출목표']) > 0}
                    _tv  = [_tgm.get(ym) for ym in order_ym]
                    if any(v for v in _tv):
                        fig.add_trace(go.Scatter(
                            x=order_ym, y=_tv, name="목표(팀 전체)", mode="lines+markers",
                            connectgaps=False, line=dict(color="#1a1a2e", width=2, dash="dot"),
                            marker=dict(size=6),
                            hovertemplate="목표 %{x}<br>%{y:,}원<extra></extra>"))
                fig.update_layout(height=400, barmode="stack",
                                  yaxis=dict(tickformat=",", title="실적 (KRW)"),
                                  legend=dict(orientation="h", y=1.12), margin=dict(t=20, b=0))
                st.plotly_chart(fig, use_container_width=True)

            _sub_a, _sub_c, _sub_p = st.tabs(["🟣 A팀 (아티스트)", "🔴 C팀 (캐릭터)", "🟠 픽 (PICK)"])
            with _sub_a:
                _render_team("A팀")
            with _sub_c:
                _render_team("C팀")
            with _sub_p:
                _render_team("픽")

            st.markdown('<div class="section-title">팀/항목별 누적 요약</div>', unsafe_allow_html=True)
            rows = []
            for team in ["A팀", "C팀", "픽"]:
                sub = tms[tms["팀"] == team]
                if sub.empty:
                    continue
                dom = int(sub[sub["_kr"]]["실적"].sum())
                ext = int(sub[~sub["_kr"]]["실적"].sum())
                pho = int(sub[sub["출처"] == "포토이즘"]["실적"].sum())
                snp = int(sub[sub["출처"] == "스내피즘"]["실적"].sum())
                rows.append({"팀": DIV_LABEL.get(team, team),
                             "국내": fmt_krw(dom), "해외": fmt_krw(ext),
                             "포토이즘": fmt_krw(pho), "스내피즘": fmt_krw(snp) if snp > 0 else "—",
                             "합계": fmt_krw(dom + ext)})
            _dom = int(tms[tms["_kr"]]["실적"].sum())
            _ext = int(tms[~tms["_kr"]]["실적"].sum())
            _tp  = int(tms[tms["출처"] == "포토이즘"]["실적"].sum())
            _ts  = int(tms[tms["출처"] == "스내피즘"]["실적"].sum())
            rows.append({"팀": "합계", "국내": fmt_krw(_dom), "해외": fmt_krw(_ext),
                         "포토이즘": fmt_krw(_tp), "스내피즘": fmt_krw(_ts), "합계": fmt_krw(_dom + _ext)})
            st.dataframe(pd.DataFrame(rows).set_index("팀"), use_container_width=True, height=210)
            st.caption("※ A팀=포토이즘 아티스트 + 스내피즘(아티스트·기타) · C팀=포토이즘 캐릭터 + 스내피즘(캐릭터) · "
                       "픽=포토이즘 PICK(스내피즘 없음). 렌탈·기획(P)·제외 미포함. "
                       "목표는 **팀 전체(국내+해외) 기준**이에요 — 팀별 국내/해외 따로 잡힌 목표는 아직 없어요.")


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

            # ── 지역(국내/해외) 보기 + 국가별 비중 (전체 기준, 최근 14주) ──
            try:
                _rd = _region_daily()
                if not _rd.empty:
                    _rd = _rd.copy()
                    _rd["주시작"] = (_rd["날짜"] - pd.to_timedelta(_rd["날짜"].dt.weekday, unit="D")).dt.normalize()
                    _recent = sorted(_rd["주시작"].unique())[-14:]
                    _rd = _rd[_rd["주시작"].isin(_recent)]

                    def _wlbl(ws):
                        ws = pd.Timestamp(ws); we = ws + pd.Timedelta(days=6)
                        return f"{ws.month}/{ws.day}~{we.month}/{we.day}"

                    _ow = [_wlbl(w) for w in _recent]
                    st.markdown('<div class="section-title">주차별 국내·해외 + 국가별 비중 '
                                '<span style="font-weight:500;color:#8a8aa0;font-size:.85rem">(전체 기준 · 최근 14주)</span></div>',
                                unsafe_allow_html=True)
                    _gc1, _gc2 = st.columns([1.4, 1])
                    with _gc1:
                        _rg = _rd.groupby(["주시작", "_kr"], as_index=False)["매출액"].sum()
                        _wm = {w: _wlbl(w) for w in _recent}
                        _rg["주차"] = _rg["주시작"].map(_wm)
                        fig_r = go.Figure()
                        for _kr, _lbl, _col in [(True, "국내", "#4361ee"), (False, "해외", "#f9a826")]:
                            _d = (_rg[_rg["_kr"] == _kr].set_index("주차").reindex(_ow)["매출액"].fillna(0))
                            fig_r.add_trace(go.Bar(
                                x=_ow, y=_d.values, name=_lbl, marker_color=_col,
                                hovertemplate=f"{_lbl} %{{x}}<br>%{{y:,}}원<extra></extra>"))
                        fig_r.update_layout(
                            height=420, barmode="stack",
                            yaxis=dict(tickformat=",", title="실적 (KRW)"),
                            xaxis=dict(title="", tickangle=-45, tickfont=dict(size=10)),
                            legend=dict(orientation="h", y=1.08), margin=dict(t=30, b=70))
                        st.plotly_chart(fig_r, use_container_width=True)
                        st.caption("주차별 국내·해외 매출 (포토이즘 A·C·픽 + 스내피즘 합산)")
                    with _gc2:
                        _since = pd.Timestamp(min(_recent)).strftime("%Y-%m-%d")
                        _nat = _photoism_country_since(_mtime(AGG_FILE), _mtime(CONFIG_FILE), _since)
                        if _nat.empty:
                            st.caption("국가별 데이터가 없어요.")
                        else:
                            _TN = 7
                            if len(_nat) > _TN:
                                _np = pd.concat([
                                    _nat.head(_TN),
                                    pd.DataFrame([{"국가": f"기타 {len(_nat)-_TN}개국",
                                                   "매출액": int(_nat.iloc[_TN:]["매출액"].sum())}]),
                                ], ignore_index=True)
                            else:
                                _np = _nat
                            fig_n = go.Figure(go.Pie(
                                labels=_np["국가"], values=_np["매출액"], hole=0.5, sort=False,
                                marker=dict(colors=["#4361ee", "#7209b7", "#f72585", "#f9a826",
                                                    "#3a0ca3", "#4895ef", "#f8961e", "#ced4da"]),
                                texttemplate="%{percent}", textposition="inside",
                                hovertemplate="%{label}<br>%{value:,}원 (%{percent})<extra></extra>"))
                            fig_n.update_layout(height=420, margin=dict(t=10, b=0),
                                                legend=dict(orientation="v", y=0.5, x=1.0, font_size=11))
                            st.plotly_chart(fig_n, use_container_width=True)
                            st.caption("국가별 매출 비중 (포토이즘 · 상위 7개국 + 기타)")
            except Exception:
                pass

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
