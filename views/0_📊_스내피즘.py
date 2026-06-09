import json
import streamlit as st
import streamlit.components.v1 as components
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from pathlib import Path
from datetime import date, timedelta
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from guide_content import render_guide

# (set_page_config / 상단메뉴 한글화는 라우터 스내피즘.py에서 처리)

# ══════════════════════════════════════════════════════════════
#  디자인 시스템 (색상 · 타이포 · 카드 · 구분선)
# ══════════════════════════════════════════════════════════════
PRIMARY   = "#4361ee"   # 메인 블루
SECONDARY = "#7209b7"   # 보라
ACCENT    = "#4cc9f0"   # 시안
PINK      = "#f72585"   # 강조 핑크
INK       = "#1a1a2e"   # 진한 텍스트

st.markdown(f"""
<style>
/* ---- 폰트 ---- */
html, body, [class*="css"], [data-testid="stAppViewContainer"] {{
    font-family: 'Pretendard', -apple-system, BlinkMacSystemFont,
                 'Segoe UI', 'Malgun Gothic', 'Apple SD Gothic Neo', sans-serif;
}}

/* ---- 본문 여백 ---- */
[data-testid="stAppViewContainer"] .main .block-container {{
    padding-top: 2.2rem; padding-bottom: 3rem; max-width: 1500px;
}}

/* ---- 타이틀 ---- */
h1 {{ font-weight: 800 !important; letter-spacing: -0.5px; color: {INK}; }}

/* ---- 섹션 타이틀 (좌측 컬러 바) ---- */
.section-title {{
    font-size: 1.12rem; font-weight: 700; color: {INK};
    margin: 4px 0 12px; padding-left: 12px;
    border-left: 4px solid {PRIMARY}; line-height: 1.4;
}}
.section-title.purple {{ border-left-color: {SECONDARY}; }}
.section-title.pink   {{ border-left-color: {PINK}; }}
.sub-label {{ font-size: .9rem; font-weight: 600; color: #5a5a72; margin-bottom: 6px; }}

/* ---- KPI 메트릭 카드 ---- */
[data-testid="stMetric"], [data-testid="metric-container"] {{
    background: linear-gradient(135deg, #ffffff 0%, #f5f8ff 100%);
    border: 1px solid #e7ecf7; border-radius: 16px;
    padding: 16px 20px;
    box-shadow: 0 2px 10px rgba(67,97,238,0.06);
    transition: transform .15s ease, box-shadow .15s ease;
}}
[data-testid="stMetric"]:hover, [data-testid="metric-container"]:hover {{
    transform: translateY(-3px);
    box-shadow: 0 8px 20px rgba(67,97,238,0.14);
}}
[data-testid="stMetricLabel"] p {{ font-weight: 600; color: #6b7280; font-size: .82rem; }}
[data-testid="stMetricValue"] {{ font-weight: 800; color: {INK}; letter-spacing: -0.5px; }}
[data-testid="stMetricDelta"] {{ font-size: 0.82rem; }}

/* ---- 구분선 ---- */
hr {{ margin: 1.4rem 0 1.2rem; border: none;
      border-top: 1px solid #e9edf5; }}

/* ---- 탭/세그먼트 ---- */
[data-testid="stElementToolbar"] {{ display: none; }}
[data-testid="stDeployButton"] {{ display: none !important; }}

/* ---- 사이드바 ---- */
[data-testid="stSidebar"] {{ background: #fbfcfe; border-right: 1px solid #eceff5; }}

/* ---- 데이터프레임 ---- */
[data-testid="stDataFrame"] {{ border-radius: 12px; overflow: hidden; }}
/* ---- 상단 탭 ---- */
button[data-baseweb="tab"] p {{ font-size: 1.0rem !important; font-weight: 700 !important; }}
</style>
""", unsafe_allow_html=True)

BASE_DIR = Path(__file__).parent.parent
MASTER_FILE = BASE_DIR / "data" / "master.csv"
CONFIG_FILE = BASE_DIR / "config.json"

# 통화 기호 (확장 대비)
CURRENCY_SYMBOLS = {
    "KRW": "₩", "CNY": "¥", "JPY": "¥", "IDR": "Rp", "TWD": "NT$",
    "THB": "฿", "HKD": "HK$", "MYR": "RM", "USD": "$", "EUR": "€",
    "GBP": "£", "VND": "₫", "PHP": "₱", "SGD": "S$", "AUD": "A$",
    "CAD": "C$", "AED": "AED", "MXN": "$", "PEN": "S/", "CLP": "$",
    "LAK": "₭", "MNT": "₮", "MOP": "MOP$", "BND": "B$",
}

# 국가명 → ISO alpha-2 코드 (국기 이미지용, 확장 대비)
# ※ Windows+Chrome 에선 국기 이모지가 'KR' 글자로 보이므로 실제 이미지 사용
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
    """국가명 → 국기 이미지 URL (flagcdn). 미매핑 시 빈 문자열."""
    iso = COUNTRY_ISO.get(str(name).strip())
    return f"https://flagcdn.com/32x24/{iso}.png" if iso else ""


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
    if not MASTER_FILE.exists():
        return pd.DataFrame()

    df = pd.read_csv(MASTER_FILE, encoding="utf-8-sig", low_memory=False)
    df["날짜"] = pd.to_datetime(df["날짜"], errors="coerce").dt.date
    df["결제일시"] = pd.to_datetime(df["결제일시"], format="%Y.%m.%d %H:%M", errors="coerce")
    df["취소 여부"] = df["취소 여부"].astype(str).str.lower().isin(["true", "1", "yes"])

    for col in ["최종 결제 금액", "상품 단가", "쿠폰 할인 금액"]:
        df[col] = pd.to_numeric(df.get(col, 0), errors="coerce").fillna(0).astype(int)

    ex = load_exchange_rates()
    df["결제 단위"] = df["결제 단위"].fillna("KRW").astype(str).str.strip()
    df["환율"] = df["결제 단위"].map(ex).fillna(1)
    df["KRW환산금액"] = (df["최종 결제 금액"] * df["환율"]).round(0).astype(int)
    df["쿠폰KRW"] = (df["쿠폰 할인 금액"] * df["환율"]).round(0).astype(int)
    df["정산금액"] = df["KRW환산금액"] + df["쿠폰KRW"]
    df["총원화금액"] = df["최종 결제 금액"] + df["쿠폰 할인 금액"]  # 현지 통화 기준 합

    return df


def paid_sales(df):
    return df[~df["취소 여부"] & (df["최종 결제 금액"] > 0)]


def coupon_txns(df):
    return df[~df["취소 여부"] & (df["최종 결제 금액"] == 0) & (df["쿠폰 할인 금액"] > 0)]


def total_rev(df):
    p = paid_sales(df)
    c = coupon_txns(df)
    return int(p["KRW환산금액"].sum() + p["쿠폰KRW"].sum() + c["쿠폰KRW"].sum())


def fmt_krw(num):
    return f"₩{int(num):,}"


def fmt_orig(amount, currency):
    sym = CURRENCY_SYMBOLS.get(currency, currency + " ")
    return f"{sym}{int(amount):,}"


def style_fig(fig, height, legend=True):
    """플롯 공통 스타일 (premium look)"""
    fig.update_layout(
        height=height,
        font=dict(family="Pretendard, Malgun Gothic, sans-serif", size=12, color="#2b2b3a"),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        margin=dict(t=24, b=4, l=4, r=8),
        hoverlabel=dict(font_size=12, font_family="Pretendard, Malgun Gothic, sans-serif"),
    )
    if legend:
        fig.update_layout(legend=dict(orientation="h", y=1.1, x=0,
                                      bgcolor="rgba(0,0,0,0)", font_size=11))
    else:
        fig.update_layout(showlegend=False)
    fig.update_xaxes(showgrid=False, zeroline=False)
    fig.update_yaxes(gridcolor="#eef1f6", zeroline=False)
    return fig


def section(title, cls=""):
    st.markdown(f'<div class="section-title {cls}">{title}</div>', unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════
#  데이터 로드
# ══════════════════════════════════════════════════════════════
df_all = load_data()
ex_rates = load_exchange_rates()

st.title("📊 스내피즘 매출 대시보드")
render_guide("snapism")

if df_all.empty:
    st.warning("데이터가 없습니다. `raw` 폴더에 CSV를 넣고 `데이터추가.bat`을 실행하세요.")
    st.stop()

last_date = df_all["날짜"].max()
first_date = df_all["날짜"].min()
_cfg = load_config()
_updated = _cfg.get("rates_updated", "")
st.caption(
    f"📆 데이터 범위 **{first_date} ~ {last_date}**  ·  "
    f"총 **{len(df_all):,}건**  ·  새로고침 F5"
)

# ── 사이드바 필터 ────────────────────────────────────────────
st.sidebar.header("🔍 필터")

default_start = max(last_date - timedelta(days=29), first_date)
date_range = st.sidebar.date_input(
    "날짜 범위", value=[default_start, last_date],
    min_value=first_date, max_value=last_date,
)

KNOWN_COUNTRIES = ["대한민국", "일본", "중국", "대만", "인도네시아", "홍콩", "태국", "말레이시아"]
if "국가" in df_all.columns:
    data_countries = set(df_all["국가"].dropna().replace("nan", pd.NA).dropna().unique().tolist())
    all_countries = sorted(data_countries | set(KNOWN_COUNTRIES))
    selected_country = st.sidebar.selectbox("국가", ["전체"] + all_countries)
else:
    selected_country = "전체"

selected_store = st.sidebar.selectbox(
    "매장", ["전체"] + sorted(df_all["매장 이름"].dropna().unique().tolist()))
selected_cat = st.sidebar.selectbox(
    "카테고리 (아티스트/캐릭터)",
    ["전체"] + sorted(df_all["카테고리"].dropna().replace("nan", pd.NA).dropna().unique().tolist()))
selected_prod = st.sidebar.selectbox(
    "상품 카테고리", ["전체"] + sorted(df_all["상품 카테고리"].dropna().unique().tolist()))
selected_frame = st.sidebar.selectbox(
    "프레임 (IP)",
    ["전체"] + sorted(df_all["프레임 이름"].dropna().replace("nan", pd.NA).dropna().unique().tolist()))

st.sidebar.divider()
st.sidebar.caption(f"💱 실시간 환율{'  ·  ' + _updated if _updated else ''}")
for cur, rate in ex_rates.items():
    if cur != "KRW":
        st.sidebar.caption(f"  1 {cur} = ₩{rate:,.2f}")

# 필터 적용
df = df_all.copy()
if len(date_range) == 2:
    df = df[(df["날짜"] >= date_range[0]) & (df["날짜"] <= date_range[1])]
if selected_country != "전체" and "국가" in df.columns:
    df = df[df["국가"] == selected_country]
if selected_store != "전체":
    df = df[df["매장 이름"] == selected_store]
if selected_cat != "전체":
    df = df[df["카테고리"] == selected_cat]
if selected_prod != "전체":
    df = df[df["상품 카테고리"] == selected_prod]
if selected_frame != "전체" and "프레임 이름" in df.columns:
    df = df[df["프레임 이름"] == selected_frame]

sales = paid_sales(df)
coupons = coupon_txns(df)
all_txns = pd.concat([sales, coupons])

# ══════════════════════════════════════════════════════════════
#  KPI 카드
# ══════════════════════════════════════════════════════════════
today = date.today()
yesterday = today - timedelta(days=1)
month_start = today.replace(day=1)

yest_sales = paid_sales(df_all[df_all["날짜"] == yesterday])
today_amt = total_rev(df_all[df_all["날짜"] == today])
yest_amt = total_rev(df_all[df_all["날짜"] == yesterday])
month_amt = total_rev(df_all[df_all["날짜"] >= month_start])
delta_pct = ((today_amt - yest_amt) / yest_amt * 100) if yest_amt > 0 else 0

cancelled_amt = df[df["취소 여부"]]["KRW환산금액"].sum()
coupon_amt = coupons["쿠폰KRW"].sum() + sales["쿠폰KRW"].sum()
period_amt = int(sales["KRW환산금액"].sum()) + int(coupon_amt)
coupon_cnt = len(coupons) + len(sales[sales["쿠폰 할인 금액"] > 0])

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("오늘 매출+쿠폰", fmt_krw(today_amt), f"{delta_pct:+.1f}% vs 어제")
c2.metric("어제 매출+쿠폰", fmt_krw(yest_amt), f"{len(yest_sales):,}건")
c3.metric("이번 달 누적", fmt_krw(month_amt), f"{month_start.strftime('%m/%d')}~오늘")
c4.metric("쿠폰 할인 총액", fmt_krw(coupon_amt), f"{coupon_cnt:,}건 사용")
c5.metric("조회기간 합계", fmt_krw(period_amt), f"취소 {fmt_krw(cancelled_amt)}")

st.divider()

# ══════════════════════════════════════════════════════════════
#  상단 탭 (포토이즘과 동일한 기본 형태)
# ══════════════════════════════════════════════════════════════
tab_ov, tab_nat, tab_cat, tab_etc = st.tabs([
    "📊 매출 개요", "🌏 국가별 분석", "🧩 상품 카테고리", "⏰ 시간대 · 데이터",
])

# ════════════ 탭 1: 매출 개요 ════════════
with tab_ov:
    with st.container(border=True):
        col_left, col_right = st.columns([3, 2])

        with col_left:
            head_l, head_r = st.columns([3, 2])
            with head_l:
                section("📈 매출 추이")
            with head_r:
                gran = st.segmented_control(
                    "기간", ["월", "주", "일"], default="월",
                    key="trend_gran", label_visibility="collapsed",
                )
            if gran is None:
                gran = "월"

            def period_key(dates, g):
                d = pd.to_datetime(dates)
                if g == "월":
                    return d.dt.to_period("M")
                if g == "주":
                    return d.dt.to_period("W")
                return d.dt.date

            s_paid = sales.assign(_p=period_key(sales["날짜"], gran)).groupby("_p")["KRW환산금액"].sum().rename("실결제")
            c100 = coupons.assign(_p=period_key(coupons["날짜"], gran)).groupby("_p")["쿠폰KRW"].sum()
            cpart_src = sales[sales["쿠폰 할인 금액"] > 0]
            cpart = cpart_src.assign(_p=period_key(cpart_src["날짜"], gran)).groupby("_p")["쿠폰KRW"].sum()
            s_coupon = c100.add(cpart, fill_value=0).rename("쿠폰할인")

            trend = pd.concat([s_paid, s_coupon], axis=1).fillna(0).sort_index()
            if trend.empty:
                st.info("해당 조건의 데이터가 없습니다.")
            else:
                trend["합계"] = trend["실결제"] + trend["쿠폰할인"]
                win = {"월": 3, "주": 4, "일": 7}[gran]
                ma_unit = {"월": "개월", "주": "주", "일": "일"}[gran]
                trend["평균"] = trend["합계"].rolling(win, min_periods=1).mean().round(0)
                trend = trend.reset_index()
                if gran == "월":
                    trend["label"] = trend["_p"].apply(lambda p: f"{p.year}.{p.month:02d}")
                elif gran == "주":
                    trend["label"] = trend["_p"].apply(lambda p: p.start_time.strftime("%m/%d") + "주")
                else:
                    trend["label"] = trend["_p"].astype(str)

                fig = go.Figure()
                fig.add_trace(go.Bar(
                    x=trend["label"], y=trend["실결제"], name="실결제",
                    marker_color=PRIMARY, opacity=0.9,
                    hovertemplate="%{x}<br>실결제 %{y:,}원<extra></extra>"))
                fig.add_trace(go.Bar(
                    x=trend["label"], y=trend["쿠폰할인"], name="쿠폰할인",
                    marker_color=ACCENT, opacity=0.9,
                    hovertemplate="%{x}<br>쿠폰할인 %{y:,}원<extra></extra>"))
                if len(trend) >= 2:
                    fig.add_trace(go.Scatter(
                        x=trend["label"], y=trend["평균"], name=f"{win}{ma_unit} 평균",
                        line=dict(color=PINK, width=2.5), mode="lines",
                        hovertemplate="%{x}<br>평균 %{y:,.0f}원<extra></extra>"))
                fig.update_layout(barmode="stack", yaxis_tickformat=",")
                style_fig(fig, 340)
                fig.update_xaxes(type="category")
                st.plotly_chart(fig, use_container_width=True)

        with col_right:
            section("🛍 상품 카테고리 비중", "purple")
            cat_pie = (all_txns.groupby("상품 카테고리")["정산금액"].sum()
                       .reset_index().sort_values("정산금액", ascending=False))
            fig2 = px.pie(cat_pie, values="정산금액", names="상품 카테고리",
                          color_discrete_sequence=px.colors.qualitative.Set2, hole=0.45)
            fig2.update_traces(
                textposition="inside", textinfo="percent",
                hovertemplate="%{label}<br>%{value:,}원 (%{percent})<extra></extra>")
            style_fig(fig2, 340)
            st.plotly_chart(fig2, use_container_width=True)

    # 매장 / 프레임 TOP 10
    with st.container(border=True):
        col_a, col_b = st.columns(2)
        with col_a:
            section("🏬 매장별 매출 TOP 10")
            store_df = (
                all_txns.groupby("매장 이름")
                .agg(매출=("정산금액", "sum"), 건수=("정산금액", "count"))
                .reset_index().nlargest(10, "매출").sort_values("매출")
            )
            fig3 = px.bar(store_df, x="매출", y="매장 이름", orientation="h",
                          color="매출", color_continuous_scale="Blues", custom_data=["건수"])
            fig3.update_traces(hovertemplate="%{y}<br>%{x:,}원 · %{customdata[0]:,}건<extra></extra>")
            fig3.update_layout(coloraxis_showscale=False, xaxis_tickformat=",", yaxis_title="")
            style_fig(fig3, 380, legend=False)
            st.plotly_chart(fig3, use_container_width=True)

        with col_b:
            section("🎬 프레임(아티스트/IP) TOP 10", "purple")
            frame_all_df = (
                all_txns[all_txns["프레임 이름"].notna() & (all_txns["프레임 이름"] != "nan")]
                .groupby("프레임 이름")
                .agg(매출=("정산금액", "sum"), 건수=("정산금액", "count"))
                .reset_index().sort_values("매출", ascending=False)
            )
            frame_df = frame_all_df.nlargest(10, "매출").sort_values("매출")
            fig4 = px.bar(frame_df, x="매출", y="프레임 이름", orientation="h",
                          color="매출", color_continuous_scale="Purples", custom_data=["건수"])
            fig4.update_traces(hovertemplate="%{y}<br>%{x:,}원 · %{customdata[0]:,}건<extra></extra>")
            fig4.update_layout(coloraxis_showscale=False, xaxis_tickformat=",", yaxis_title="")
            style_fig(fig4, 380, legend=False)
            st.plotly_chart(fig4, use_container_width=True)

            with st.expander(f"📋 전체 프레임 보기 ({len(frame_all_df):,}개)"):
                fshow = frame_all_df.reset_index(drop=True)
                fshow.index = fshow.index + 1
                st.dataframe(
                    fshow, use_container_width=True, height=400,
                    column_config={
                        "매출": st.column_config.NumberColumn("매출 (₩)", format="localized"),
                        "건수": st.column_config.NumberColumn("건수", format="localized"),
                    },
                )

# ════════════ 탭 2: 국가별 분석 ════════════
with tab_nat:
    with st.container(border=True):
        section("🌏 국가별 매출 분석")
        col_nat, col_coupon = st.columns([3, 2])

        with col_nat:
            nat = (
                all_txns.groupby(["국가", "결제 단위"])
                .agg(건수=("정산금액", "count"),
                     현지통화=("총원화금액", "sum"),
                     KRW환산=("정산금액", "sum"))
                .reset_index().sort_values("KRW환산", ascending=False)
            )
            nat["국기"] = nat["국가"].apply(flag_url)
            nat["현지 통화 금액"] = nat.apply(lambda r: fmt_orig(r["현지통화"], r["결제 단위"]), axis=1)
            tot_krw = nat["KRW환산"].sum()
            nat["비중"] = (nat["KRW환산"] / tot_krw) if tot_krw else 0

            st.dataframe(
                nat[["국기", "국가", "결제 단위", "건수", "현지 통화 금액", "KRW환산", "비중"]],
                use_container_width=True, hide_index=True, height=420,
                column_config={
                    "국기": st.column_config.ImageColumn(" ", width="small"),
                    "국가": st.column_config.TextColumn("국가"),
                    "결제 단위": st.column_config.TextColumn("통화", width="small"),
                    "건수": st.column_config.NumberColumn("건수", format="localized"),
                    "현지 통화 금액": st.column_config.TextColumn("현지 통화 금액"),
                    "KRW환산": st.column_config.NumberColumn("KRW 환산 (₩)", format="localized"),
                    "비중": st.column_config.ProgressColumn(
                        "비중", format="percent", min_value=0,
                        max_value=float(nat["비중"].max()) if len(nat) else 1.0),
                },
            )

            TOPN = 10
            nat_bar = nat.copy()
            if len(nat_bar) > TOPN:
                top = nat_bar.head(TOPN)
                rest = nat_bar.iloc[TOPN:]
                others = pd.DataFrame([{
                    "국가": f"기타 ({len(rest)}개국)", "결제 단위": "-",
                    "건수": int(rest["건수"].sum()), "현지 통화 금액": "-",
                    "KRW환산": int(rest["KRW환산"].sum()), "비중": 0,
                }])
                nat_bar = pd.concat([top, others], ignore_index=True)

            fig_nat = px.bar(
                nat_bar.sort_values("KRW환산"),
                x="KRW환산", y="국가", orientation="h",
                color="KRW환산", color_continuous_scale="Teal",
                custom_data=["현지 통화 금액", "결제 단위", "건수"])
            fig_nat.update_traces(
                hovertemplate="%{y}<br>정산기준 %{x:,}원<br>현지 %{customdata[0]} · %{customdata[2]:,}건<extra></extra>")
            fig_nat.update_layout(coloraxis_showscale=False, xaxis_tickformat=",", yaxis_title="")
            style_fig(fig_nat, 320, legend=False)
            st.plotly_chart(fig_nat, use_container_width=True)

        with col_coupon:
            st.markdown('<div class="sub-label">🎟 쿠폰 할인 현황</div>', unsafe_allow_html=True)
            all_coupon = pd.concat([coupons, sales[sales["쿠폰 할인 금액"] > 0]])
            if not all_coupon.empty:
                cpn = (
                    all_coupon.groupby(["국가", "결제 단위"])
                    .agg(쿠폰건수=("쿠폰 할인 금액", "count"),
                         할인금액=("쿠폰 할인 금액", "sum"),
                         할인KRW=("쿠폰KRW", "sum"))
                    .reset_index().sort_values("할인KRW", ascending=False)
                )
                cpn["국기"] = cpn["국가"].apply(flag_url)
                cpn["할인금액(현지)"] = cpn.apply(lambda r: fmt_orig(r["할인금액"], r["결제 단위"]), axis=1)
                st.dataframe(
                    cpn[["국기", "국가", "쿠폰건수", "할인금액(현지)", "할인KRW"]],
                    use_container_width=True, hide_index=True,
                    column_config={
                        "국기": st.column_config.ImageColumn(" ", width="small"),
                        "국가": st.column_config.TextColumn("국가"),
                        "쿠폰건수": st.column_config.NumberColumn("쿠폰건수", format="localized"),
                        "할인금액(현지)": st.column_config.TextColumn("할인(현지)"),
                        "할인KRW": st.column_config.NumberColumn("할인 (₩)", format="localized"),
                    },
                )
                total_cpn = all_coupon["쿠폰KRW"].sum()
                st.info(f"쿠폰 총 할인 **{fmt_krw(total_cpn)}**  ·  {len(all_coupon):,}건")
            else:
                st.info("조회 기간 내 쿠폰 사용 없음")

# ════════════ 탭 3: 상품 카테고리 ════════════
with tab_cat:
    with st.container(border=True):
        section("🧩 상품 카테고리 분석", "pink")
        cat_bar_df = (
            all_txns.groupby("상품 카테고리")
            .agg(매출=("정산금액", "sum"), 건수=("정산금액", "count"))
            .reset_index().sort_values("매출", ascending=False)
        )

        col_cat_chart, col_cat_tbl = st.columns([6, 4])
        with col_cat_chart:
            fig_cat = px.bar(cat_bar_df.sort_values("매출"),
                             x="매출", y="상품 카테고리", orientation="h",
                             color="매출", color_continuous_scale="Greens", custom_data=["건수"])
            fig_cat.update_traces(hovertemplate="%{y}<br>%{x:,}원 · %{customdata[0]:,}건<extra></extra>")
            fig_cat.update_layout(coloraxis_showscale=False, xaxis_tickformat=",", yaxis_title="")
            style_fig(fig_cat, max(260, len(cat_bar_df) * 46 + 80), legend=False)
            st.plotly_chart(fig_cat, use_container_width=True)
        with col_cat_tbl:
            ctbl = cat_bar_df.reset_index(drop=True)
            csum = ctbl["매출"].sum()
            ctbl["비중"] = (ctbl["매출"] / csum) if csum else 0
            ctbl.index = ctbl.index + 1
            st.dataframe(
                ctbl, use_container_width=True, height=max(260, len(cat_bar_df) * 46 + 80),
                column_config={
                    "매출": st.column_config.NumberColumn("매출 (₩)", format="localized"),
                    "건수": st.column_config.NumberColumn("건수", format="localized"),
                    "비중": st.column_config.ProgressColumn(
                        "비중", format="percent", min_value=0,
                        max_value=float(ctbl["비중"].max()) if len(ctbl) else 1.0),
                },
            )

    with st.container(border=True):
        st.markdown('<div class="sub-label">📦 카테고리별 상품 순위</div>', unsafe_allow_html=True)
        avail_cats = sorted(all_txns["상품 카테고리"].dropna().unique().tolist())
        default_cat = "미니스티커" if "미니스티커" in avail_cats else (avail_cats[0] if avail_cats else None)

        col_pcat, _ = st.columns([2, 8])
        with col_pcat:
            pick_cat = st.selectbox(
                "카테고리 선택", avail_cats,
                index=avail_cats.index(default_cat) if default_cat in avail_cats else 0,
                key="prod_rank_cat", label_visibility="collapsed")

        prod_rank_df = (
            all_txns[all_txns["상품 카테고리"] == pick_cat]
            .groupby("상품 이름")
            .agg(매출=("정산금액", "sum"), 건수=("정산금액", "count"))
            .reset_index().sort_values("매출", ascending=False)
        )

        if prod_rank_df.empty:
            st.info("해당 카테고리의 데이터가 없습니다.")
        else:
            col_pr_chart, col_pr_tbl = st.columns([6, 4])
            with col_pr_chart:
                top_prod = prod_rank_df.head(15).sort_values("매출")
                fig_pr = px.bar(top_prod, x="매출", y="상품 이름", orientation="h",
                                color="매출", color_continuous_scale="Oranges", custom_data=["건수"])
                fig_pr.update_traces(hovertemplate="%{y}<br>%{x:,}원 · %{customdata[0]:,}건<extra></extra>")
                fig_pr.update_layout(coloraxis_showscale=False, xaxis_tickformat=",", yaxis_title="")
                style_fig(fig_pr, max(300, len(top_prod) * 36 + 60), legend=False)
                st.plotly_chart(fig_pr, use_container_width=True)
            with col_pr_tbl:
                pr = prod_rank_df.head(15).reset_index(drop=True)
                psum = prod_rank_df["매출"].sum()
                pr["비중"] = (pr["매출"] / psum) if psum else 0
                pr.index = pr.index + 1
                st.dataframe(
                    pr, use_container_width=True, height=max(300, len(top_prod) * 36 + 60),
                    column_config={
                        "매출": st.column_config.NumberColumn("매출 (₩)", format="localized"),
                        "건수": st.column_config.NumberColumn("건수", format="localized"),
                        "비중": st.column_config.ProgressColumn(
                            "비중", format="percent", min_value=0,
                            max_value=float(pr["비중"].max()) if len(pr) else 1.0),
                    },
                )
            if len(prod_rank_df) > 15:
                with st.expander(f"📋 전체 보기 ({len(prod_rank_df):,}개)"):
                    full = prod_rank_df.reset_index(drop=True)
                    full.index = full.index + 1
                    st.dataframe(
                        full, use_container_width=True, height=400,
                        column_config={
                            "매출": st.column_config.NumberColumn("매출 (₩)", format="localized"),
                            "건수": st.column_config.NumberColumn("건수", format="localized"),
                        },
                    )

    with st.expander("🔍 IP별 상품 카테고리 상세", expanded=(selected_frame != "전체")):
        frame_src = all_txns[all_txns["프레임 이름"].notna() & (all_txns["프레임 이름"] != "nan")]
        ip_options = ["전체"] + sorted(frame_src["프레임 이름"].unique().tolist())
        col_ip1, _ = st.columns([2, 8])
        with col_ip1:
            ip_pick = st.selectbox(
                "IP / 프레임 선택", ip_options,
                index=ip_options.index(selected_frame) if selected_frame in ip_options else 0,
                key="ip_detail_select", label_visibility="collapsed")
        ip_data = frame_src if ip_pick == "전체" else frame_src[frame_src["프레임 이름"] == ip_pick]
        if ip_data.empty:
            st.info("해당 기간에 데이터가 없습니다.")
        else:
            ip_cat = (
                ip_data.groupby("상품 카테고리")
                .agg(매출=("정산금액", "sum"), 건수=("정산금액", "count"))
                .reset_index().sort_values("매출", ascending=False)
            )
            col_ic, col_it = st.columns([6, 4])
            with col_ic:
                title_label = ip_pick if ip_pick != "전체" else "전체 IP"
                fig_ip = px.bar(ip_cat.sort_values("매출"),
                                x="매출", y="상품 카테고리", orientation="h",
                                color="매출", color_continuous_scale="Teal", custom_data=["건수"],
                                title=f"{title_label} · 상품 카테고리별 매출")
                fig_ip.update_traces(hovertemplate="%{y}<br>%{x:,}원 · %{customdata[0]:,}건<extra></extra>")
                fig_ip.update_layout(coloraxis_showscale=False, xaxis_tickformat=",", yaxis_title="")
                style_fig(fig_ip, max(250, len(ip_cat) * 40 + 80), legend=False)
                st.plotly_chart(fig_ip, use_container_width=True)
            with col_it:
                itbl = ip_cat.reset_index(drop=True)
                itbl.index = itbl.index + 1
                st.dataframe(
                    itbl, use_container_width=True,
                    column_config={
                        "매출": st.column_config.NumberColumn("매출 (₩)", format="localized"),
                        "건수": st.column_config.NumberColumn("건수", format="localized"),
                    },
                )

# ════════════ 탭 4: 시간대 · 데이터 ════════════
with tab_etc:
    with st.container(border=True):
        section("⏰ 시간대별 매출 분포")
        hourly = (
            all_txns.assign(시간대=all_txns["결제일시"].dt.hour)
            .groupby("시간대")["정산금액"].agg(["sum", "count"])
            .reindex(range(24), fill_value=0).reset_index()
            .rename(columns={"시간대": "시간", "sum": "매출", "count": "건수"})
        )
        hourly["시간_label"] = hourly["시간"].apply(lambda h: f"{h:02d}:00")
        fig5 = px.bar(hourly, x="시간_label", y="매출",
                      color="매출", color_continuous_scale="Oranges", custom_data=["건수"])
        fig5.update_traces(hovertemplate="%{x}<br>%{y:,}원 · %{customdata[0]:,}건<extra></extra>")
        fig5.update_layout(coloraxis_showscale=False, xaxis_title="", yaxis_tickformat=",")
        style_fig(fig5, 260, legend=False)
        st.plotly_chart(fig5, use_container_width=True)

    with st.expander("🗃 원본 데이터 보기"):
        show_cols = [
            "날짜", "결제일시", "국가", "매장 이름", "상품 카테고리", "상품 이름",
            "상품 단가", "쿠폰 할인 금액", "최종 결제 금액", "결제 단위",
            "KRW환산금액", "결제 수단", "프레임 이름", "카테고리", "취소 여부",
        ]
        available = [c for c in show_cols if c in df.columns]
        st.dataframe(
            df[available].sort_values("결제일시", ascending=False).reset_index(drop=True),
            use_container_width=True, height=400,
        )
        csv_export = df[available].to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")
        st.download_button("CSV 다운로드", csv_export, "filtered_sales.csv", "text/csv")
