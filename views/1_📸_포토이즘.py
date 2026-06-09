import json
import pyarrow.parquet as pq
import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from pathlib import Path
from datetime import date, timedelta

# set_page_config 는 라우터(스내피즘.py)에서 처리
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from guide_content import render_guide

# ── 디자인 시스템 (스내피즘과 동일) ──
PRIMARY = "#4361ee"; SECONDARY = "#7209b7"; ACCENT = "#4cc9f0"; PINK = "#f72585"; INK = "#1a1a2e"

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
    font-size: 1.12rem; font-weight: 700; color: {INK};
    margin: 4px 0 12px; padding-left: 12px;
    border-left: 4px solid {PRIMARY}; line-height: 1.4;
}}
.section-title.purple {{ border-left-color: {SECONDARY}; }}
.section-title.pink   {{ border-left-color: {PINK}; }}
.sub-label {{ font-size: .9rem; font-weight: 600; color: #5a5a72; margin-bottom: 6px; }}
[data-testid="stMetric"], [data-testid="metric-container"] {{
    background: linear-gradient(135deg, #ffffff 0%, #f5f8ff 100%);
    border: 1px solid #e7ecf7; border-radius: 16px; padding: 16px 20px;
    box-shadow: 0 2px 10px rgba(67,97,238,0.06);
    transition: transform .15s ease, box-shadow .15s ease;
}}
[data-testid="stMetric"]:hover, [data-testid="metric-container"]:hover {{
    transform: translateY(-3px); box-shadow: 0 8px 20px rgba(67,97,238,0.14);
}}
[data-testid="stMetricLabel"] p {{ font-weight: 600; color: #6b7280; font-size: .82rem; }}
[data-testid="stMetricValue"] {{ font-weight: 800; color: {INK}; letter-spacing: -0.5px; }}
[data-testid="stMetricDelta"] {{ font-size: 0.82rem; }}
hr {{ margin: 1.4rem 0 1.2rem; border: none; border-top: 1px solid #e9edf5; }}
[data-testid="stElementToolbar"] {{ display: none; }}
[data-testid="stDeployButton"] {{ display: none !important; }}
[data-testid="stSidebar"] {{ background: #fbfcfe; border-right: 1px solid #eceff5; }}
[data-testid="stDataFrame"] {{ border-radius: 12px; overflow: hidden; }}
button[data-baseweb="tab"] p {{ font-size: 1.0rem !important; font-weight: 700 !important; }}
</style>
""", unsafe_allow_html=True)

BASE_DIR     = Path(__file__).parent.parent
AGG_FILE     = BASE_DIR / "data" / "master_photoism_agg.parquet"
HOURLY_FILE  = BASE_DIR / "data" / "master_photoism_hourly.parquet"
PARQUET_FILE = BASE_DIR / "data" / "master_photoism.parquet"
MASTER_FILE  = BASE_DIR / "data" / "master_photoism.csv"
CONFIG_FILE  = BASE_DIR / "config.json"

# 국가별 매출액 가산 규칙 (쿠폰/서비스코인 포함 국가)
_COUPON_CC = {"la", "gb", "de", "th", "lv", "mx"}
_COIN_CC   = {"cl", "la", "pe", "gb", "de", "lv", "mx"}

# 국가명 → ISO alpha-2 (국기 이미지용, 30개국 대응)
COUNTRY_ISO = {
    "대한민국": "kr", "한국": "kr", "일본": "jp", "중국": "cn", "대만": "tw",
    "인도네시아": "id", "홍콩": "hk", "태국": "th", "말레이시아": "my",
    "미국": "us", "베트남": "vn", "필리핀": "ph", "싱가포르": "sg", "괌": "gu",
    "캐나다": "ca", "호주": "au", "독일": "de", "프랑스": "fr", "영국": "gb",
    "스페인": "es", "네덜란드": "nl", "멕시코": "mx", "페루": "pe", "칠레": "cl",
    "라오스": "la", "몽골": "mn", "마카오": "mo", "아랍에미리트": "ae", "아랍": "ae",
    "룩셈부르크": "lu", "브루나이": "bn", "라트비아": "lv",
}


def flag_url(name):
    iso = COUNTRY_ISO.get(str(name).strip())
    return f"https://flagcdn.com/32x24/{iso}.png" if iso else ""


CURRENCY_SYMBOLS = {
    "KRW": "₩", "CNY": "¥", "JPY": "¥", "IDR": "Rp", "TWD": "NT$", "THB": "฿",
    "HKD": "HK$", "MYR": "RM", "USD": "$", "EUR": "€", "GBP": "£", "VND": "₫",
    "PHP": "₱", "SGD": "S$", "AUD": "A$", "CAD": "C$", "AED": "AED", "MXN": "$",
    "PEN": "S/", "CLP": "$", "LAK": "₭", "MNT": "₮", "MOP": "MOP$", "BND": "B$",
}


def fmt_orig(amount, currency):
    sym = CURRENCY_SYMBOLS.get(str(currency).strip(), str(currency) + " ")
    return f"{sym}{int(amount):,}"


def load_config():
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def load_exchange_rates():
    return load_config().get("exchange_rates", {"KRW": 1})


@st.cache_data(ttl=30)
def load_data():
    """집계 parquet 로드 (category 인코딩)"""
    if AGG_FILE.exists():
        try:
            table = pq.read_table(str(AGG_FILE))
            df = table.to_pandas(strings_to_categorical=True)
        except Exception as e:
            st.warning(f"집계 파일 로드 실패: {e}")
            return pd.DataFrame()
    else:
        st.error("집계 파일 없음. `python build_photoism_agg.py` 를 먼저 실행하세요.")
        return pd.DataFrame()

    df["날짜"] = pd.to_datetime(df["날짜"], errors="coerce").dt.date
    df = df[df["날짜"].notna()]
    df["취소 여부"] = df["취소 여부"].astype(bool)
    for col in ["건수", "최종 결제 금액", "쿠폰 할인 금액", "서비스코인"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)
        else:
            df[col] = 0

    ex = load_exchange_rates()
    if "결제 단위" in df.columns:
        df["결제 단위"] = df["결제 단위"].astype(str).fillna("KRW").str.strip()
    else:
        df["결제 단위"] = "KRW"
    df["환율"]      = df["결제 단위"].map(ex).fillna(1)
    df["KRW환산금액"] = (df["최종 결제 금액"] * df["환율"]).round(0).astype(int)
    df["쿠폰KRW"]    = (df["쿠폰 할인 금액"] * df["환율"]).round(0).astype(int)
    df["정산금액"]   = df["KRW환산금액"] + df["쿠폰KRW"]
    df["서비스코인KRW"] = (df["서비스코인"] * df["환율"]).round(0).astype(int)

    _cc = (
        df["국가코드"].astype(str).str.lower().str.strip().replace("nan", "")
        if "국가코드" in df.columns else pd.Series("", index=df.index)
    )
    df["매출액"] = (
        df["KRW환산금액"]
        + df["쿠폰KRW"]       * _cc.isin(_COUPON_CC).astype(int)
        + df["서비스코인KRW"] * _cc.isin(_COIN_CC).astype(int)
    )
    return df


@st.cache_data(ttl=30)
def load_hourly():
    """시간대 집계 parquet 로드 (시간대 차트 전용)"""
    if not HOURLY_FILE.exists():
        return pd.DataFrame()
    try:
        table = pq.read_table(str(HOURLY_FILE))
        df = table.to_pandas()
        df["날짜"] = pd.to_datetime(df["날짜"], errors="coerce").dt.date
        df = df[df["날짜"].notna()]
        df["취소 여부"] = df["취소 여부"].astype(bool)
        return df
    except Exception:
        return pd.DataFrame()


# 세부 항목 분류 기준 화이트리스트 (UI 라벨 → 실제 컬럼)
DETAIL_DIMS = {
    "프레임 이름": "프레임 이름",
    "테마 (구좌: BASIC/WITH/EVENT)": "구좌",
    "타이틀 (IP)": "타이틀명",
    "상품 카테고리 (브랜드)": "브랜드",
    "채널 (중분류)": "중분류",
    "사업형태 (소분류)": "소분류",
}


@st.cache_data(ttl=60)
def load_sales_detail(group_col, start_date, end_date,
                      ip_list=None, country="전체", store="전체",
                      brand="전체", daebun="전체"):
    """전체 parquet에서 세부 판매 항목(프레임/테마 등) DuckDB on-demand 집계."""
    if group_col not in DETAIL_DIMS.values() or not PARQUET_FILE.exists():
        return pd.DataFrame()
    try:
        import duckdb
    except Exception:
        # DuckDB 초기화 실패(드물게 동시 초기화 충돌 등) 시 이 섹션만 건너뜀
        return pd.DataFrame()
    parq = str(PARQUET_FILE).replace("\\", "/")

    def esc(v):
        return str(v).replace("'", "''")

    where = [
        f"TRY_CAST(\"날짜\" AS DATE) BETWEEN DATE '{start_date}' AND DATE '{end_date}'",
        "LOWER(CAST(\"취소 여부\" AS VARCHAR)) NOT IN ('true','1','yes')",
        "TRY_CAST(\"최종 결제 금액\" AS DOUBLE) >= 0",
    ]
    # 이상치 제외: 행별 KRW 환산액이 비정상적으로 큰 행(입력 오류) 차단
    _exr = load_exchange_rates()
    _rc = ("CASE COALESCE(\"결제 단위\",'KRW') "
           + " ".join(f"WHEN '{c}' THEN {float(r)}" for c, r in _exr.items() if c != "KRW" and r)
           + " ELSE 1 END")
    where += [
        f"COALESCE(TRY_CAST(\"최종 결제 금액\" AS DOUBLE),0)*({_rc}) <= 1000000",
        f"COALESCE(TRY_CAST(\"서비스코인\"     AS DOUBLE),0)*({_rc}) <= 1000000",
        f"COALESCE(TRY_CAST(\"쿠폰 할인 금액\" AS DOUBLE),0)*({_rc}) <= 1000000",
    ]
    if ip_list:
        ins = ",".join(f"'{esc(x)}'" for x in ip_list)
        where.append(f"CAST(\"타이틀명\" AS VARCHAR) IN ({ins})")
    if country and country != "전체":
        where.append(f"CAST(\"국가\" AS VARCHAR) = '{esc(country)}'")
    if store and store != "전체":
        where.append(f"CAST(\"매장 이름\" AS VARCHAR) = '{esc(store)}'")
    if brand and brand != "전체":
        where.append(f"CAST(\"브랜드\" AS VARCHAR) = '{esc(brand)}'")
    if daebun and daebun != "전체":
        where.append(f"CAST(\"대분류\" AS VARCHAR) = '{esc(daebun)}'")
    where_sql = " AND ".join(where)

    con = duckdb.connect()
    try:
        df = con.execute(f"""
            SELECT
                COALESCE(CAST("{group_col}" AS VARCHAR), '') AS "항목",
                COALESCE(CAST("결제 단위" AS VARCHAR), 'KRW') AS "결제 단위",
                LOWER(COALESCE(CAST("국가코드" AS VARCHAR), '')) AS "국가코드",
                SUM(TRY_CAST("최종 결제 금액" AS DOUBLE)) AS "최종 결제 금액",
                SUM(TRY_CAST("쿠폰 할인 금액" AS DOUBLE)) AS "쿠폰 할인 금액",
                SUM(TRY_CAST("서비스코인" AS DOUBLE))     AS "서비스코인",
                COUNT(*) AS "건수"
            FROM read_parquet('{parq}')
            WHERE {where_sql}
            GROUP BY 1, 2, 3
        """).df()
    finally:
        con.close()

    if df.empty:
        return df

    ex = load_exchange_rates()
    df["결제 단위"] = df["결제 단위"].astype(str).str.strip().replace("nan", "KRW")
    df["환율"] = df["결제 단위"].map(ex).fillna(1)
    for c in ["최종 결제 금액", "쿠폰 할인 금액", "서비스코인", "건수"]:
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)
    df["KRW_순수"] = (df["최종 결제 금액"] * df["환율"]).round(0)
    df["KRW_쿠폰"] = (df["쿠폰 할인 금액"] * df["환율"]).round(0)
    df["KRW_코인"] = (df["서비스코인"]     * df["환율"]).round(0)
    cc = df["국가코드"].astype(str).str.lower().str.strip()
    df["매출액"] = (
        df["KRW_순수"]
        + df["KRW_쿠폰"] * cc.isin(_COUPON_CC).astype(int)
        + df["KRW_코인"] * cc.isin(_COIN_CC).astype(int)
    )
    out = (
        df.groupby("항목", as_index=False)
        .agg(매출=("매출액", "sum"), 건수=("건수", "sum"))
    )
    out = out[out["항목"].astype(str).str.strip() != ""]
    out["매출"] = out["매출"].astype("int64")
    out["건수"] = out["건수"].astype("int64")
    return out.sort_values("매출", ascending=False).reset_index(drop=True)


def paid_sales(df):
    return df[~df["취소 여부"] & (df["최종 결제 금액"] >= 0)]


def tx_count(df):
    return int(df["건수"].sum()) if "건수" in df.columns else len(df)


def fmt_krw(n):
    return f"₩{int(n):,}"


# ── 데이터 로드 ──────────────────────────────────────────────
df_all = load_data()

st.title("📸 포토이즘 매출 대시보드")
render_guide("photoism")

if df_all.empty:
    st.warning("데이터가 없습니다. 집계 파일을 먼저 생성하세요.")
    st.code("python build_photoism_agg.py")
    st.stop()

last_date  = df_all["날짜"].dropna().max()
first_date = df_all["날짜"].dropna().min()

cfg        = load_config()
ex         = load_exchange_rates()
rates_upd  = cfg.get("rates_updated", "-")
_rate_info = "  |  ".join(
    f"1 {cur} = {rate:,.2f} KRW"
    for cur, rate in ex.items()
    if cur != "KRW"
)
st.caption(f"데이터 범위: {first_date} ~ {last_date}  |  총 {tx_count(df_all):,}건  |  새로고침: F5")
st.caption(f"💱 환율 기준: **{rates_upd} 업데이트**   {_rate_info}")

# ── 사이드바 필터 ─────────────────────────────────────────────
st.sidebar.header("🔍 필터")
default_start = max(last_date - timedelta(days=29), first_date)
date_range = st.sidebar.date_input(
    "날짜 범위",
    value=[default_start, last_date],
    min_value=first_date, max_value=last_date,
)

countries = ["전체"] + sorted(df_all["국가"].dropna().astype(str).unique().tolist())
selected_country = st.sidebar.selectbox("국가", countries)

stores = ["전체"] + sorted(df_all["매장 이름"].dropna().astype(str).unique().tolist())
selected_store = st.sidebar.selectbox("매장", stores)

대분류_list = ["전체"] + sorted(
    [v for v in df_all["대분류"].dropna().astype(str).unique() if v not in ("", "nan")]
)
selected_대분류 = st.sidebar.selectbox("카테고리 (아티스트/캐릭터)", 대분류_list)

brands = ["전체"] + sorted(
    [v for v in df_all["브랜드"].dropna().astype(str).unique() if v not in ("", "nan")]
)
selected_brand = st.sidebar.selectbox("상품 카테고리", brands)

_ip_all = sorted([
    v for v in df_all["타이틀명"].dropna().astype(str).unique()
    if v.strip() and v not in ("nan", "")
])
selected_ips = st.sidebar.multiselect(
    "프레임 (IP)", options=_ip_all,
    placeholder="전체 (선택 없음 = 전체)",
)

# 필터 적용
df = df_all.copy()
if len(date_range) == 2:
    df = df[(df["날짜"] >= date_range[0]) & (df["날짜"] <= date_range[1])]
if selected_country != "전체":
    df = df[df["국가"].astype(str) == selected_country]
if selected_brand != "전체":
    df = df[df["브랜드"].astype(str) == selected_brand]
if selected_store != "전체":
    df = df[df["매장 이름"].astype(str) == selected_store]
if selected_대분류 != "전체":
    df = df[df["대분류"].astype(str) == selected_대분류]
if selected_ips:
    df = df[df["타이틀명"].astype(str).isin(selected_ips)]

sales = paid_sales(df)

# ── KPI 카드 ──────────────────────────────────────────────────
today      = date.today()
yesterday  = today - timedelta(days=1)
month_start = today.replace(day=1)


def period_rev(d):
    return int(paid_sales(d)["매출액"].sum())


today_amt  = period_rev(df_all[df_all["날짜"] == today])
yest_amt   = period_rev(df_all[df_all["날짜"] == yesterday])
month_amt  = period_rev(df_all[df_all["날짜"] >= month_start])
period_amt = period_rev(df)
delta_pct  = ((today_amt - yest_amt) / yest_amt * 100) if yest_amt > 0 else 0
yest_cnt   = tx_count(paid_sales(df_all[df_all["날짜"] == yesterday]))

c1, c2, c3, c4 = st.columns(4)
c1.metric("오늘 매출 (KRW)", fmt_krw(today_amt), f"{delta_pct:+.1f}% vs 어제")
c2.metric("어제 매출 (KRW)", fmt_krw(yest_amt), f"{yest_cnt:,}건")
c3.metric("이번 달 누적", fmt_krw(month_amt), f"{month_start.strftime('%m/%d')}~오늘")
c4.metric("조회기간 합계", fmt_krw(period_amt), f"{tx_count(sales):,}건")

st.divider()

# ══════════════════════════════════════════════════════════════
#  상단 탭 (스내피즘과 동일한 기본 형태)
# ══════════════════════════════════════════════════════════════
tab_ov, tab_nat, tab_ip, tab_etc = st.tabs([
    "📊 매출 개요", "🌏 국가별 분석", "🎬 IP · 세부 항목", "⏰ 시간대 · 데이터",
])

# ════════════ 탭 1: 매출 개요 ════════════
with tab_ov:
    with st.container(border=True):
        col_left, col_right = st.columns([3, 2])

        with col_left:
            head_l, head_r = st.columns([3, 2])
            with head_l:
                st.markdown('<div class="section-title">📈 매출 추이</div>', unsafe_allow_html=True)
            with head_r:
                gran = st.segmented_control(
                    "기간", ["월", "주", "일"], default="월",
                    key="ph_trend_gran", label_visibility="collapsed",
                )
            if gran is None:
                gran = "월"

            def _pkey(dates, g):
                d = pd.to_datetime(dates)
                if g == "월":
                    return d.dt.to_period("M")
                if g == "주":
                    return d.dt.to_period("W")
                return d.dt.date

            trend = (
                sales.assign(_p=_pkey(sales["날짜"], gran))
                .groupby("_p", observed=True)["매출액"].sum()
                .rename("매출").reset_index().sort_values("_p")
            )
            if trend.empty:
                st.info("해당 조건의 데이터가 없습니다.")
            else:
                win = {"월": 3, "주": 4, "일": 7}[gran]
                ma_unit = {"월": "개월", "주": "주", "일": "일"}[gran]
                trend["평균"] = trend["매출"].rolling(win, min_periods=1).mean().round(0)
                if gran == "월":
                    trend["label"] = trend["_p"].apply(lambda p: f"{p.year}.{p.month:02d}")
                elif gran == "주":
                    trend["label"] = trend["_p"].apply(lambda p: p.start_time.strftime("%m/%d") + "주")
                else:
                    trend["label"] = trend["_p"].astype(str)

                fig = go.Figure()
                fig.add_trace(go.Bar(
                    x=trend["label"], y=trend["매출"], name="매출",
                    marker_color=SECONDARY, opacity=0.9,
                    hovertemplate="%{x}<br>%{y:,}원<extra></extra>",
                ))
                if len(trend) >= 2:
                    fig.add_trace(go.Scatter(
                        x=trend["label"], y=trend["평균"], name=f"{win}{ma_unit} 평균",
                        line=dict(color=PINK, width=2.5),
                        hovertemplate="%{x}<br>평균 %{y:,.0f}원<extra></extra>",
                    ))
                fig.update_layout(
                    height=320, yaxis_tickformat=",",
                    legend=dict(orientation="h", y=1.1, x=0), margin=dict(t=24, b=4),
                )
                fig.update_xaxes(type="category")
                st.plotly_chart(fig, use_container_width=True)

        with col_right:
            st.markdown('<div class="section-title purple">🌏 국가별 매출 비중</div>', unsafe_allow_html=True)
            nat_pie_full = (
                sales.groupby("국가", observed=True)["매출액"].sum()
                .reset_index().sort_values("매출액", ascending=False)
            )
            TOPN_PIE = 7
            if len(nat_pie_full) > TOPN_PIE:
                head_p = nat_pie_full.head(TOPN_PIE)
                etc_sum = int(nat_pie_full.iloc[TOPN_PIE:]["매출액"].sum())
                nat_pie = pd.concat(
                    [head_p, pd.DataFrame([{"국가": f"기타 {len(nat_pie_full) - TOPN_PIE}개국", "매출액": etc_sum}])],
                    ignore_index=True,
                )
            else:
                nat_pie = nat_pie_full
            fig2 = px.pie(
                nat_pie, values="매출액", names="국가", hole=0.5,
                color_discrete_sequence=[PRIMARY, SECONDARY, ACCENT, PINK,
                                         "#3a0ca3", "#4895ef", "#f8961e", "#ced4da"],
            )
            fig2.update_traces(
                sort=False, textposition="inside", texttemplate="%{percent}",
                hovertemplate="%{label}<br>%{value:,}원 (%{percent})<extra></extra>",
            )
            fig2.update_layout(
                height=320, margin=dict(t=10, b=0),
                legend=dict(orientation="v", y=0.5, x=1.0, font_size=11),
            )
            st.plotly_chart(fig2, use_container_width=True)
            st.caption("상위 7개국 + 기타. 국가별 전체 수치는 ‘🌏 국가별 분석’ 탭 참고.")

    # 매장 TOP10 + 타이틀(IP) TOP10
    with st.container(border=True):
        col_c, col_d = st.columns(2)
        with col_c:
            st.markdown('<div class="section-title">🏬 매장별 매출 TOP 10</div>', unsafe_allow_html=True)
            store_df = (
                sales.groupby("매장 이름", observed=True)
                .agg(매출=("매출액", "sum"), 건수=("건수", "sum"))
                .reset_index().nlargest(10, "매출").sort_values("매출")
            )
            fig5 = px.bar(
                store_df, x="매출", y="매장 이름", orientation="h",
                color="매출", color_continuous_scale="Blues", custom_data=["건수"],
            )
            fig5.update_traces(hovertemplate="%{y}<br>%{x:,}원 · %{customdata[0]:,}건<extra></extra>")
            fig5.update_layout(height=380, coloraxis_showscale=False,
                               xaxis_tickformat=",", yaxis_title="", margin=dict(t=10, b=0))
            st.plotly_chart(fig5, use_container_width=True)

        with col_d:
            st.markdown('<div class="section-title">🎬 타이틀(IP) TOP 10</div>', unsafe_allow_html=True)
            title_src = sales[
                sales["타이틀명"].notna()
                & ~sales["타이틀명"].astype(str).str.strip().isin(["", "nan"])
            ]
            title_all = (
                title_src.groupby("타이틀명", observed=True)
                .agg(매출=("매출액", "sum"), 건수=("건수", "sum"))
                .reset_index().sort_values("매출", ascending=False)
            )
            title_df = title_all.nlargest(10, "매출").sort_values("매출")
            fig6 = px.bar(
                title_df, x="매출", y="타이틀명", orientation="h",
                color="매출", color_continuous_scale="Oranges", custom_data=["건수"],
            )
            fig6.update_traces(hovertemplate="%{y}<br>%{x:,}원 · %{customdata[0]:,}건<extra></extra>")
            fig6.update_layout(height=380, coloraxis_showscale=False,
                               xaxis_tickformat=",", yaxis_title="", margin=dict(t=10, b=0))
            st.plotly_chart(fig6, use_container_width=True)
            with st.expander(f"📋 전체 타이틀 보기 ({len(title_all):,}개)"):
                t_show = title_all.reset_index(drop=True)
                t_show.index = t_show.index + 1
                st.dataframe(
                    t_show, use_container_width=True, height=400,
                    column_config={
                        "매출": st.column_config.NumberColumn("매출 (₩)", format="localized"),
                        "건수": st.column_config.NumberColumn("건수", format="localized"),
                    },
                )

# ════════════ 탭 2: 국가별 분석 ════════════
with tab_nat:
    with st.container(border=True):
        st.markdown('<div class="section-title">🌏 국가별 매출 분석</div>', unsafe_allow_html=True)
        col_nat_tbl, col_nat_bar = st.columns([3, 2])

        nat = (
            sales.groupby(["국가", "결제 단위"], observed=True)
            .agg(건수=("건수", "sum"), 현지통화=("최종 결제 금액", "sum"), 매출=("매출액", "sum"))
            .reset_index().sort_values("매출", ascending=False)
        )
        nat["국기"] = nat["국가"].astype(str).apply(flag_url)
        nat["현지 통화 금액"] = nat.apply(lambda r: fmt_orig(r["현지통화"], r["결제 단위"]), axis=1)
        _tot = nat["매출"].sum()
        nat["비중"] = (nat["매출"] / _tot) if _tot else 0

        with col_nat_tbl:
            st.dataframe(
                nat[["국기", "국가", "결제 단위", "건수", "현지 통화 금액", "매출", "비중"]],
                use_container_width=True, hide_index=True, height=460,
                column_config={
                    "국기": st.column_config.ImageColumn(" ", width="small"),
                    "국가": st.column_config.TextColumn("국가"),
                    "결제 단위": st.column_config.TextColumn("통화", width="small"),
                    "건수": st.column_config.NumberColumn("건수", format="localized"),
                    "현지 통화 금액": st.column_config.TextColumn("현지 통화 금액"),
                    "매출": st.column_config.NumberColumn("매출 (₩)", format="localized"),
                    "비중": st.column_config.ProgressColumn(
                        "비중", format="percent", min_value=0,
                        max_value=float(nat["비중"].max()) if len(nat) else 1.0),
                },
            )
            st.caption(f"전체 {len(nat)}개국 · 표 헤더를 클릭하면 정렬됩니다.")

        with col_nat_bar:
            TOPN = 10
            nat_bar = nat.copy()
            if len(nat_bar) > TOPN:
                top = nat_bar.head(TOPN)
                rest = nat_bar.iloc[TOPN:]
                others = pd.DataFrame([{
                    "국가": f"기타 ({len(rest)}개국)", "결제 단위": "-",
                    "건수": int(rest["건수"].sum()), "현지 통화 금액": "-",
                    "매출": int(rest["매출"].sum()), "비중": 0,
                }])
                nat_bar = pd.concat([top, others], ignore_index=True)
            fig3 = px.bar(
                nat_bar.sort_values("매출"), x="매출", y="국가", orientation="h",
                color="매출", color_continuous_scale="Purples", custom_data=["건수"],
            )
            fig3.update_traces(hovertemplate="%{y}<br>%{x:,}원 · %{customdata[0]:,}건<extra></extra>")
            fig3.update_layout(height=460, coloraxis_showscale=False,
                               xaxis_tickformat=",", yaxis_title="", margin=dict(t=10, b=0))
            st.plotly_chart(fig3, use_container_width=True)

    st.divider()
    with st.container(border=True):
        st.markdown('<div class="section-title purple">🏆 국가별 IP TOP 10</div>', unsafe_allow_html=True)

        ip_src = sales[
            sales["타이틀명"].notna()
            & ~sales["타이틀명"].astype(str).str.strip().isin(["", "nan"])
        ].copy()
        ip_src["타이틀명"] = ip_src["타이틀명"].astype(str).str.strip()

        if ip_src.empty:
            st.info("해당 기간 IP 데이터가 없습니다.")
        else:
            nat_order = (
                ip_src.groupby("국가", observed=True)["매출액"].sum()
                .sort_values(ascending=False)
            )
            nat_choices = [str(c) for c in nat_order.index.tolist()]
            sel_nat = st.selectbox(
                f"국가 선택 (전체 {len(nat_choices)}개국)", nat_choices, key="ip_nat_sel",
            )
            cdf = (
                ip_src[ip_src["국가"].astype(str) == sel_nat]
                .groupby("타이틀명", observed=True)
                .agg(매출=("매출액", "sum"), 건수=("건수", "sum"))
                .reset_index().sort_values("매출", ascending=False)
            )
            _furl = flag_url(sel_nat)
            _flag = (f'<img src="{_furl}" width="24" style="vertical-align:middle;'
                     f'margin-right:6px;border-radius:2px">' if _furl else "")
            st.markdown(
                f'{_flag}<b>{sel_nat}</b> · 총 매출 {fmt_krw(int(cdf["매출"].sum()))} · IP {len(cdf):,}개',
                unsafe_allow_html=True,
            )
            col_ipb, col_ipt = st.columns([3, 2])
            with col_ipb:
                top = cdf.head(10).sort_values("매출")
                fig_c = px.bar(
                    top, x="매출", y="타이틀명", orientation="h",
                    color="매출", color_continuous_scale="Purples", custom_data=["건수"],
                )
                fig_c.update_traces(hovertemplate="%{y}<br>%{x:,}원 · %{customdata[0]:,}건<extra></extra>")
                fig_c.update_layout(height=max(340, len(top) * 34 + 70), coloraxis_showscale=False,
                                    xaxis_tickformat=",", yaxis_title="", margin=dict(t=10, b=0))
                st.plotly_chart(fig_c, use_container_width=True)
            with col_ipt:
                ct = cdf.head(15).reset_index(drop=True)
                ct.index = ct.index + 1
                st.dataframe(
                    ct, use_container_width=True, height=max(340, len(top) * 34 + 70),
                    column_config={
                        "매출": st.column_config.NumberColumn("매출 (₩)", format="localized"),
                        "건수": st.column_config.NumberColumn("건수", format="localized"),
                    },
                )

# ════════════ 탭 3: IP · 세부 항목 ════════════
with tab_ip:
    with st.container(border=True):
        if selected_ips:
            if len(selected_ips) == 1:
                section_label = f"🔥 [{selected_ips[0]}] IP 상세 분석"
            else:
                section_label = f"🔥 [{' + '.join(selected_ips)}] 합산 분석"
            st.markdown(f'<div class="section-title">{section_label}</div>', unsafe_allow_html=True)

            ip_detail = sales[sales["타이틀명"].astype(str).isin(selected_ips)]
            if ip_detail.empty:
                st.info("선택된 기간에 해당 IP 데이터가 없습니다.")
            else:
                tot_rev = int(ip_detail["매출액"].sum())
                tot_cnt = tx_count(ip_detail)
                nat_cnt = ip_detail["국가"].nunique()
                st_cnt  = ip_detail["매장 이름"].nunique()
                ic1, ic2, ic3, ic4 = st.columns(4)
                ic1.metric("합산 총 매출", fmt_krw(tot_rev))
                ic2.metric("총 결제 건수", f"{tot_cnt:,}건")
                ic3.metric("판매 국가 수", f"{nat_cnt}개국")
                ic4.metric("판매 매장 수", f"{st_cnt}개")

                if len(selected_ips) >= 2:
                    st.markdown('<div class="section-title">📊 IP별 매출 비교</div>', unsafe_allow_html=True)
                    ip_compare = (
                        ip_detail.groupby("타이틀명", observed=True)
                        .agg(매출=("매출액", "sum"), 건수=("건수", "sum"))
                        .reset_index().sort_values("매출", ascending=False)
                    )
                    ip_compare["비중"] = (ip_compare["매출"] / ip_compare["매출"].sum() * 100).round(1)
                    _cmp_colors = ["#7209b7", "#f72585", "#4cc9f0", "#4361ee", "#3a0ca3", "#560bad"]
                    col_cmp1, col_cmp2 = st.columns([3, 2])
                    with col_cmp1:
                        fig_cmp = px.bar(
                            ip_compare.sort_values("매출"), x="매출", y="타이틀명", orientation="h",
                            color="타이틀명", color_discrete_sequence=_cmp_colors,
                            custom_data=["건수", "비중"],
                        )
                        fig_cmp.update_traces(
                            hovertemplate="%{y}<br>%{x:,}원  (%{customdata[0]:,}건, %{customdata[1]:.1f}%)<extra></extra>")
                        fig_cmp.update_layout(height=max(220, len(selected_ips) * 60 + 40), showlegend=False,
                                              xaxis_tickformat=",", yaxis_title="", margin=dict(t=10, b=0))
                        st.plotly_chart(fig_cmp, use_container_width=True)
                    with col_cmp2:
                        fig_pie_cmp = px.pie(ip_compare, values="매출", names="타이틀명",
                                             color_discrete_sequence=_cmp_colors, hole=0.45)
                        fig_pie_cmp.update_traces(hovertemplate="%{label}<br>%{value:,}원 (%{percent})<extra></extra>")
                        fig_pie_cmp.update_layout(height=max(220, len(selected_ips) * 60 + 40), margin=dict(t=10, b=0))
                        st.plotly_chart(fig_pie_cmp, use_container_width=True)
                    tbl_cmp = ip_compare.copy()
                    tbl_cmp.index = range(1, len(tbl_cmp) + 1)
                    tbl_cmp["비중"] = tbl_cmp["비중"].apply(lambda x: f"{x:.1f}%")
                    tbl_cmp["매출"] = tbl_cmp["매출"].apply(fmt_krw)
                    st.dataframe(tbl_cmp, use_container_width=True)

                col_ip1, col_ip2 = st.columns(2)
                with col_ip1:
                    st.markdown('<div class="section-title">일별 매출 추이</div>', unsafe_allow_html=True)
                    if len(selected_ips) == 1:
                        ip_daily = (
                            ip_detail.groupby("날짜", observed=True)["매출액"].sum()
                            .reset_index().rename(columns={"매출액": "매출"})
                        )
                        ip_daily["날짜_str"] = ip_daily["날짜"].astype(str)
                        fig_ip_daily = go.Figure()
                        fig_ip_daily.add_trace(go.Bar(
                            x=ip_daily["날짜_str"], y=ip_daily["매출"],
                            marker_color=PINK, opacity=0.85,
                            hovertemplate="%{x}<br>%{y:,}원<extra></extra>"))
                        fig_ip_daily.update_layout(height=280, yaxis_tickformat=",",
                                                   margin=dict(t=10, b=0), showlegend=False)
                    else:
                        _colors = ["#7209b7", "#f72585", "#4cc9f0", "#4361ee", "#3a0ca3", "#560bad"]
                        fig_ip_daily = go.Figure()
                        for idx, ip_name in enumerate(selected_ips):
                            ip_d = (
                                ip_detail[ip_detail["타이틀명"].astype(str) == ip_name]
                                .groupby("날짜", observed=True)["매출액"].sum()
                                .reset_index().rename(columns={"매출액": "매출"})
                            )
                            ip_d["날짜_str"] = ip_d["날짜"].astype(str)
                            fig_ip_daily.add_trace(go.Scatter(
                                x=ip_d["날짜_str"], y=ip_d["매출"], name=ip_name[:18], mode="lines+markers",
                                line=dict(color=_colors[idx % len(_colors)], width=2),
                                hovertemplate=f"{ip_name[:18]}<br>%{{x}}<br>%{{y:,}}원<extra></extra>"))
                        ip_total = (
                            ip_detail.groupby("날짜", observed=True)["매출액"].sum()
                            .reset_index().rename(columns={"매출액": "매출"})
                        )
                        ip_total["날짜_str"] = ip_total["날짜"].astype(str)
                        fig_ip_daily.add_trace(go.Bar(
                            x=ip_total["날짜_str"], y=ip_total["매출"], name="합산",
                            marker_color="rgba(0,0,0,0.1)",
                            hovertemplate="합산<br>%{x}<br>%{y:,}원<extra></extra>"))
                        fig_ip_daily.update_layout(height=280, yaxis_tickformat=",",
                                                   legend=dict(orientation="h", y=1.12, font_size=11),
                                                   margin=dict(t=30, b=0))
                    st.plotly_chart(fig_ip_daily, use_container_width=True)

                with col_ip2:
                    st.markdown('<div class="section-title">국가별 매출 분포</div>', unsafe_allow_html=True)
                    ip_nat = (
                        ip_detail.groupby("국가", observed=True)["매출액"].sum()
                        .reset_index().sort_values("매출액", ascending=False)
                    )
                    fig_ip_nat = px.pie(ip_nat, values="매출액", names="국가",
                                        color_discrete_sequence=px.colors.qualitative.Pastel, hole=0.4)
                    fig_ip_nat.update_traces(hovertemplate="%{label}<br>%{value:,}원 (%{percent})<extra></extra>")
                    fig_ip_nat.update_layout(height=280, margin=dict(t=10, b=0))
                    st.plotly_chart(fig_ip_nat, use_container_width=True)

                ip_nat_tbl = (
                    ip_detail.groupby("국가", observed=True)
                    .agg(매출=("매출액", "sum"), 건수=("건수", "sum"))
                    .reset_index().sort_values("매출", ascending=False).reset_index(drop=True)
                )
                ip_nat_tbl.index = ip_nat_tbl.index + 1
                ip_nat_tbl["비중"] = (ip_nat_tbl["매출"] / ip_nat_tbl["매출"].sum() * 100).round(1).apply(lambda x: f"{x:.1f}%")
                ip_nat_tbl["매출"] = ip_nat_tbl["매출"].apply(fmt_krw)
                st.dataframe(ip_nat_tbl, use_container_width=True, height=min(400, len(ip_nat_tbl) * 40 + 55))
        else:
            st.info("👈 사이드바에서 **프레임 (IP)** 를 선택하면 IP 상세 분석이 여기에 표시됩니다. "
                    "여러 개 선택 시 합산·비교 분석도 제공됩니다.")

    st.divider()
    with st.container(border=True):
        st.markdown('<div class="section-title pink">🔎 세부 판매 항목 검색 (프레임 / 테마)</div>',
                    unsafe_allow_html=True)
        st.caption("전체 거래 데이터에서 프레임·테마 등 세부 항목을 분류별로 집계합니다. "
                   "현재 사이드바 필터(날짜·국가·매장·카테고리·IP)가 그대로 적용됩니다.")

        dcol1, dcol2 = st.columns([1, 2])
        with dcol1:
            sel_dim_label = st.selectbox("분류 기준", list(DETAIL_DIMS.keys()), key="detail_dim")
        with dcol2:
            search_kw = st.text_input(
                "🔍 검색어 (항목명 일부)", key="detail_search",
                placeholder="예: 메인, 화이트, ENHYPEN, EVENT …",
            )

        if len(date_range) == 2:
            detail_df = load_sales_detail(
                DETAIL_DIMS[sel_dim_label], date_range[0], date_range[1],
                ip_list=selected_ips or None, country=selected_country,
                store=selected_store, brand=selected_brand, daebun=selected_대분류,
            )
        else:
            detail_df = pd.DataFrame()

        if detail_df.empty:
            st.info("해당 조건의 세부 항목 데이터가 없습니다.")
        else:
            if search_kw.strip():
                detail_df = detail_df[
                    detail_df["항목"].astype(str).str.contains(search_kw.strip(), case=False, na=False)
                ]
            if detail_df.empty:
                st.warning(f"'{search_kw}' 검색 결과가 없습니다.")
            else:
                d_rev = int(detail_df["매출"].sum())
                d_cnt = int(detail_df["건수"].sum())
                dm1, dm2, dm3 = st.columns(3)
                dm1.metric("검색 항목 수", f"{len(detail_df):,}개")
                dm2.metric("합계 매출", fmt_krw(d_rev))
                dm3.metric("합계 건수", f"{d_cnt:,}건")

                top_n = detail_df.nlargest(20, "매출").sort_values("매출")
                fig_d = px.bar(top_n, x="매출", y="항목", orientation="h",
                               color="매출", color_continuous_scale="Tealgrn", custom_data=["건수"])
                fig_d.update_traces(hovertemplate="%{y}<br>%{x:,}원 · %{customdata[0]:,}건<extra></extra>")
                fig_d.update_layout(height=max(320, len(top_n) * 26 + 60), coloraxis_showscale=False,
                                    xaxis_tickformat=",", yaxis_title="", margin=dict(t=10, b=0))
                st.plotly_chart(fig_d, use_container_width=True)
                if len(detail_df) > 20:
                    st.caption(f"※ 차트는 매출 TOP 20만 표시 (전체 {len(detail_df):,}개는 아래 표·CSV 참고)")

                tbl = detail_df.copy()
                tbl.insert(0, "순위", range(1, len(tbl) + 1))
                tbl["평균단가"] = (tbl["매출"] / tbl["건수"].replace(0, 1)).round(0).astype("int64")
                tbl["비중"] = (tbl["매출"] / tbl["매출"].sum() * 100).round(1).apply(lambda x: f"{x:.1f}%")
                tbl["매출"]     = tbl["매출"].apply(fmt_krw)
                tbl["평균단가"] = tbl["평균단가"].apply(fmt_krw)
                tbl = tbl.rename(columns={"항목": sel_dim_label})
                st.dataframe(tbl, use_container_width=True, height=480, hide_index=True)

                csv_d = detail_df.rename(columns={"항목": sel_dim_label}).to_csv(
                    index=False, encoding="utf-8-sig").encode("utf-8-sig")
                st.download_button("세부 항목 CSV 다운로드", csv_d,
                                   f"photoism_detail_{DETAIL_DIMS[sel_dim_label]}.csv", "text/csv",
                                   key="detail_csv")

# ════════════ 탭 4: 시간대 · 데이터 ════════════
with tab_etc:
    with st.container(border=True):
        st.markdown('<div class="section-title">⏰ 시간대별 매출 분포</div>', unsafe_allow_html=True)
        df_hourly = load_hourly()
        if not df_hourly.empty and len(date_range) == 2:
            df_hourly = df_hourly[
                (df_hourly["날짜"] >= date_range[0])
                & (df_hourly["날짜"] <= date_range[1])
                & (~df_hourly["취소 여부"])
            ]
        if df_hourly.empty:
            st.info("시간대 데이터가 없습니다.")
        else:
            hourly = (
                df_hourly[df_hourly["시간대"] >= 0]
                .groupby("시간대")
                .agg(매출=("최종 결제 금액", "sum"), 건수=("건수", "sum"))
                .reindex(range(24), fill_value=0).reset_index()
                .rename(columns={"시간대": "시간"})
            )
            hourly["시간_label"] = hourly["시간"].apply(lambda h: f"{h:02d}:00")
            fig8 = px.bar(hourly, x="시간_label", y="매출",
                          color="매출", color_continuous_scale="Oranges", custom_data=["건수"])
            fig8.update_traces(hovertemplate="%{x}<br>%{y:,}원 · %{customdata[0]:,}건<extra></extra>")
            fig8.update_layout(height=280, coloraxis_showscale=False,
                               xaxis_title="", yaxis_tickformat=",", margin=dict(t=10, b=0))
            st.plotly_chart(fig8, use_container_width=True)
            if selected_country != "전체" or selected_store != "전체" or selected_brand != "전체":
                st.caption("ℹ️ 시간대 차트는 날짜 필터만 적용됩니다 (국가/매장 필터 미적용)")

    st.divider()
    with st.expander("🗃 집계 데이터 보기"):
        show_cols = ["날짜", "국가", "브랜드", "대분류", "매장 이름",
                     "타이틀명", "결제 단위", "건수", "최종 결제 금액", "KRW환산금액", "매출액"]
        available = [c for c in show_cols if c in df.columns]
        st.dataframe(
            df[available].sort_values(["날짜", "매출액"], ascending=[False, False]).reset_index(drop=True),
            use_container_width=True, height=400,
        )
        csv_export = df[available].to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")
        st.download_button("CSV 다운로드", csv_export, "photoism_filtered.csv", "text/csv")
