import json
import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from pathlib import Path
from datetime import date, timedelta

st.set_page_config(
    page_title="포토이즘 매출 대시보드",
    page_icon="📸",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
[data-testid="metric-container"] {
    background: #f8f9fa; border: 1px solid #e9ecef;
    border-radius: 10px; padding: 12px 20px;
}
[data-testid="stMetricDelta"] { font-size: 0.85rem; }
.section-title { font-size: 1.05rem; font-weight: 600; margin-bottom: 2px; }
[data-testid="stDeployButton"] { display: none !important; }
</style>
""", unsafe_allow_html=True)

BASE_DIR    = Path(__file__).parent.parent
MASTER_FILE = BASE_DIR / "data" / "master_photoism.csv"
CONFIG_FILE = BASE_DIR / "config.json"


def load_config():
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def load_exchange_rates():
    cfg = load_config()
    return cfg.get("exchange_rates", {"KRW": 1})


@st.cache_data(ttl=30)
def load_data():
    if not MASTER_FILE.exists():
        return pd.DataFrame()
    df = pd.read_csv(MASTER_FILE, encoding="utf-8-sig", low_memory=False)
    df["날짜"]    = pd.to_datetime(df["날짜"], errors="coerce").dt.date
    df = df[df["날짜"].notna()]   # 날짜 파싱 실패 행 제거
    df["결제일시"] = pd.to_datetime(df["결제일시"], errors="coerce")
    df["취소 여부"] = df["취소 여부"].astype(str).str.lower().isin(["true", "1", "yes"])
    for col in ["최종 결제 금액", "상품 단가", "쿠폰 할인 금액", "상품총액"]:
        df[col] = pd.to_numeric(df.get(col, 0), errors="coerce").fillna(0).astype(int)
    ex = load_exchange_rates()
    df["결제 단위"] = df["결제 단위"].fillna("KRW").astype(str).str.strip()
    df["환율"]      = df["결제 단위"].map(ex).fillna(1)
    df["KRW환산금액"] = (df["최종 결제 금액"] * df["환율"]).round(0).astype(int)
    df["쿠폰KRW"]    = (df["쿠폰 할인 금액"] * df["환율"]).round(0).astype(int)
    df["정산금액"]   = df["KRW환산금액"] + df["쿠폰KRW"]

    # 서비스코인 KRW 환산 (컬럼 없는 경우 0)
    df["서비스코인KRW"] = (
        pd.to_numeric(df.get("서비스코인", 0), errors="coerce").fillna(0)
        * df["환율"]
    ).round(0).astype(int)

    # ── 국가별 매출액 공식 ──────────────────────────────────────
    # 기본: 최종결제금액만 계산 (KRW환산금액)
    # 쿠폰 포함: la gb de th lv mx
    # 서비스코인 포함: cl la pe gb de lv mx
    _cc        = df["국가코드"].astype(str).str.lower().fillna("")
    _coupon_cc = {"la", "gb", "de", "th", "lv", "mx"}
    _coin_cc   = {"cl", "la", "pe", "gb", "de", "lv", "mx"}
    df["매출액"] = (
        df["KRW환산금액"]
        + df["쿠폰KRW"]       * _cc.isin(_coupon_cc).astype(int)
        + df["서비스코인KRW"] * _cc.isin(_coin_cc).astype(int)
    )
    return df


def paid_sales(df):
    return df[~df["취소 여부"] & (df["최종 결제 금액"] >= 0)]

def fmt_krw(n): return f"₩{int(n):,}"


# ── 데이터 로드 ──────────────────────────────────────────────
df_all = load_data()

st.title("📸 포토이즘 매출 대시보드")

if df_all.empty:
    st.warning("데이터가 없습니다. 포토이즘 크롤러를 먼저 실행하세요.")
    st.code("python photoism_crawler.py")
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
st.caption(f"데이터 범위: {first_date} ~ {last_date}  |  총 {len(df_all):,}건  |  새로고침: F5")
st.caption(f"💱 환율 기준: **{rates_upd} 업데이트**   {_rate_info}")

# ── 사이드바 필터 ─────────────────────────────────────────────
st.sidebar.header("🔍 필터")
default_start = max(last_date - timedelta(days=29), first_date)
date_range = st.sidebar.date_input(
    "날짜 범위",
    value=[default_start, last_date],
    min_value=first_date, max_value=last_date,
)

countries = ["전체"] + sorted(df_all["국가"].dropna().unique().tolist())
selected_country = st.sidebar.selectbox("국가", countries)

brands = ["전체"] + sorted(df_all["브랜드"].dropna().unique().tolist())
selected_brand = st.sidebar.selectbox("브랜드", brands)

stores = ["전체"] + sorted(df_all["매장 이름"].dropna().unique().tolist())
selected_store = st.sidebar.selectbox("매장", stores)

대분류_list = ["전체"] + sorted(df_all["대분류"].dropna().unique().tolist())
selected_대분류 = st.sidebar.selectbox("대분류", 대분류_list)

# 필터 적용
df = df_all.copy()
if len(date_range) == 2:
    df = df[(df["날짜"] >= date_range[0]) & (df["날짜"] <= date_range[1])]
if selected_country != "전체":
    df = df[df["국가"] == selected_country]
if selected_brand != "전체":
    df = df[df["브랜드"] == selected_brand]
if selected_store != "전체":
    df = df[df["매장 이름"] == selected_store]
if selected_대분류 != "전체":
    df = df[df["대분류"] == selected_대분류]

sales = paid_sales(df)

# ── KPI 카드 ──────────────────────────────────────────────────
today     = date.today()
yesterday = today - timedelta(days=1)
month_start = today.replace(day=1)

def period_rev(d):
    s = paid_sales(d)
    return int(s["매출액"].sum())

today_amt  = period_rev(df_all[df_all["날짜"] == today])
yest_amt   = period_rev(df_all[df_all["날짜"] == yesterday])
month_amt  = period_rev(df_all[df_all["날짜"] >= month_start])
period_amt = period_rev(df)
delta_pct  = ((today_amt - yest_amt) / yest_amt * 100) if yest_amt > 0 else 0
yest_cnt   = len(paid_sales(df_all[df_all["날짜"] == yesterday]))

c1, c2, c3, c4 = st.columns(4)
c1.metric("오늘 매출 (KRW)", fmt_krw(today_amt), f"{delta_pct:+.1f}% vs 어제")
c2.metric("어제 매출 (KRW)", fmt_krw(yest_amt), f"{yest_cnt:,}건")
c3.metric("이번 달 누적", fmt_krw(month_amt), f"{month_start.strftime('%m/%d')}~오늘")
c4.metric("조회기간 합계", fmt_krw(period_amt), f"{len(sales):,}건")

st.divider()

# ── 일별 매출 추이 + 국가별 파이 ──────────────────────────────
col_left, col_right = st.columns([3, 2])

with col_left:
    st.markdown('<div class="section-title">일별 매출 추이</div>', unsafe_allow_html=True)
    daily = (
        sales.groupby("날짜")["매출액"].sum()
        .reset_index().rename(columns={"매출액": "매출"})
    )
    daily["7일 평균"] = daily["매출"].rolling(7, min_periods=1).mean().round(0)
    daily["날짜_str"] = daily["날짜"].astype(str)

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=daily["날짜_str"], y=daily["매출"],
        name="매출", marker_color="#7209b7", opacity=0.85,
        hovertemplate="%{x}<br>%{y:,}원<extra></extra>",
    ))
    fig.add_trace(go.Scatter(
        x=daily["날짜_str"], y=daily["7일 평균"],
        name="7일 이동평균", line=dict(color="#f72585", width=2),
        hovertemplate="%{x}<br>평균 %{y:,.0f}원<extra></extra>",
    ))
    fig.update_layout(
        height=320, yaxis_tickformat=",",
        legend=dict(orientation="h", y=1.08),
        margin=dict(t=20, b=0),
    )
    st.plotly_chart(fig, use_container_width=True)

with col_right:
    st.markdown('<div class="section-title">국가별 매출 비중</div>', unsafe_allow_html=True)
    nat_pie = (
        sales.groupby("국가")["매출액"].sum()
        .reset_index().sort_values("매출액", ascending=False)
    )
    fig2 = px.pie(
        nat_pie, values="매출액", names="국가",
        color_discrete_sequence=px.colors.qualitative.Pastel, hole=0.4,
    )
    fig2.update_traces(hovertemplate="%{label}<br>%{value:,}원 (%{percent})<extra></extra>")
    fig2.update_layout(height=320, margin=dict(t=20, b=0))
    st.plotly_chart(fig2, use_container_width=True)

# ── 국가별 TOP 10 ─────────────────────────────────────────────
st.markdown('<div class="section-title">국가별 매출 TOP 10</div>', unsafe_allow_html=True)
nat_df = (
    sales.groupby("국가")
    .agg(매출=("매출액", "sum"), 건수=("매출액", "count"))
    .reset_index().nlargest(10, "매출").sort_values("매출")
)
fig3 = px.bar(
    nat_df, x="매출", y="국가", orientation="h",
    color="매출", color_continuous_scale="Purples", custom_data=["건수"],
)
fig3.update_traces(hovertemplate="%{y}<br>%{x:,}원  (%{customdata[0]}건)<extra></extra>")
fig3.update_layout(
    height=360, coloraxis_showscale=False,
    xaxis_tickformat=",", yaxis_title="", margin=dict(t=10, b=0),
)
st.plotly_chart(fig3, use_container_width=True)

# ── 매장별 TOP 10 + 타이틀명 TOP 10 ──────────────────────────
col_c, col_d = st.columns(2)

with col_c:
    st.markdown('<div class="section-title">매장별 매출 TOP 10</div>', unsafe_allow_html=True)
    store_df = (
        sales.groupby("매장 이름")
        .agg(매출=("매출액", "sum"), 건수=("매출액", "count"))
        .reset_index().nlargest(10, "매출").sort_values("매출")
    )
    fig5 = px.bar(
        store_df, x="매출", y="매장 이름", orientation="h",
        color="매출", color_continuous_scale="Blues", custom_data=["건수"],
    )
    fig5.update_traces(hovertemplate="%{y}<br>%{x:,}원  (%{customdata[0]}건)<extra></extra>")
    fig5.update_layout(
        height=380, coloraxis_showscale=False,
        xaxis_tickformat=",", yaxis_title="", margin=dict(t=10, b=0),
    )
    st.plotly_chart(fig5, use_container_width=True)

with col_d:
    st.markdown('<div class="section-title">타이틀(IP) TOP 10</div>', unsafe_allow_html=True)
    title_src = sales[sales["타이틀명"].notna() & (sales["타이틀명"].astype(str).str.strip() != "nan")]
    title_all = (
        title_src.groupby("타이틀명")
        .agg(매출=("매출액", "sum"), 건수=("매출액", "count"))
        .reset_index().sort_values("매출", ascending=False)
    )
    title_df = title_all.nlargest(10, "매출").sort_values("매출")
    fig6 = px.bar(
        title_df, x="매출", y="타이틀명", orientation="h",
        color="매출", color_continuous_scale="Oranges", custom_data=["건수"],
    )
    fig6.update_traces(hovertemplate="%{y}<br>%{x:,}원  (%{customdata[0]}건)<extra></extra>")
    fig6.update_layout(
        height=380, coloraxis_showscale=False,
        xaxis_tickformat=",", yaxis_title="", margin=dict(t=10, b=0),
    )
    st.plotly_chart(fig6, use_container_width=True)
    with st.expander(f"📋 전체 타이틀 보기 ({len(title_all)}개)"):
        t_show = title_all.reset_index(drop=True)
        t_show.index = t_show.index + 1
        t_show["매출"] = t_show["매출"].apply(lambda x: f"₩{x:,}")
        st.dataframe(t_show, use_container_width=True, height=400)

# ── 국가별 IP TOP 10 ──────────────────────────────────────────
st.markdown('<div class="section-title">국가별 IP TOP 10</div>', unsafe_allow_html=True)

ip_src = sales[
    sales["타이틀명"].notna() &
    ~sales["타이틀명"].astype(str).str.strip().isin(["", "nan"])
].copy()
ip_src["타이틀명"] = ip_src["타이틀명"].astype(str).str.strip()

if ip_src.empty:
    st.info("해당 기간 IP 데이터가 없습니다.")
else:
    # 국가별 IP 매출 순위 계산
    ip_rev = ip_src.groupby(["국가", "타이틀명"])["매출액"].sum().reset_index()
    ip_rev["순위"] = (
        ip_rev.groupby("국가")["매출액"]
        .rank(ascending=False, method="first").astype(int)
    )
    ip_rev = ip_rev[ip_rev["순위"] <= 10]
    # 셀 표시: "IP명 (XX만)"
    ip_rev["표시"] = (
        ip_rev["타이틀명"].str[:20]
        + "  ("
        + (ip_rev["매출액"] / 10000).round(0).astype(int).map(lambda x: f"{x:,}만")
        + ")"
    )
    ip_rev["순위_label"] = ip_rev["순위"].apply(lambda x: f"{x}위")

    # 피벗: 국가(행) × 순위(열)
    pivot_rank = ip_rev.pivot(index="국가", columns="순위_label", values="표시")
    rank_cols  = [f"{i}위" for i in range(1, 11)]
    pivot_rank = pivot_rank.reindex(columns=[c for c in rank_cols if c in pivot_rank.columns])

    # 국가 총매출 기준 행 정렬 (높은 순)
    nat_total  = ip_src.groupby("국가")["매출액"].sum().sort_values(ascending=False)
    pivot_rank = pivot_rank.reindex(nat_total.index).dropna(how="all").fillna("-")
    pivot_rank.columns.name = None

    st.dataframe(pivot_rank, use_container_width=True,
                 height=min(700, len(pivot_rank) * 35 + 50))

    # 국가 선택 → 상세 바차트
    with st.expander("▸ 국가별 상세 IP 순위 바차트"):
        ip_nations    = sorted(ip_src["국가"].dropna().unique().tolist())
        sel_ip_nation = st.selectbox("국가 선택", ["전체"] + ip_nations, key="ip_nation_detail")
        detail_src    = ip_src if sel_ip_nation == "전체" else ip_src[ip_src["국가"] == sel_ip_nation]
        detail_ip     = (
            detail_src.groupby("타이틀명")
            .agg(매출=("매출액", "sum"), 건수=("매출액", "count"))
            .reset_index().nlargest(10, "매출").sort_values("매출")
        )
        fig_ip = px.bar(
            detail_ip, x="매출", y="타이틀명", orientation="h",
            color="매출", color_continuous_scale="Purples", custom_data=["건수"],
        )
        fig_ip.update_traces(hovertemplate="%{y}<br>%{x:,}원  (%{customdata[0]}건)<extra></extra>")
        fig_ip.update_layout(
            height=380, coloraxis_showscale=False,
            xaxis_tickformat=",", yaxis_title="", margin=dict(t=10, b=0),
        )
        st.plotly_chart(fig_ip, use_container_width=True)

# ── 시간대별 분포 ────────────────────────────────────────────
st.markdown('<div class="section-title">시간대별 매출 분포</div>', unsafe_allow_html=True)
hourly = (
    sales.assign(시간대=sales["결제일시"].dt.hour)
    .groupby("시간대")["매출액"]
    .agg(["sum", "count"])
    .reindex(range(24), fill_value=0)
    .reset_index()
    .rename(columns={"시간대": "시간", "sum": "매출", "count": "건수"})
)
hourly["시간_label"] = hourly["시간"].apply(lambda h: f"{h:02d}:00")
fig8 = px.bar(
    hourly, x="시간_label", y="매출",
    color="매출", color_continuous_scale="Oranges", custom_data=["건수"],
)
fig8.update_traces(hovertemplate="%{x}<br>%{y:,}원  (%{customdata[0]}건)<extra></extra>")
fig8.update_layout(
    height=250, coloraxis_showscale=False,
    xaxis_title="", yaxis_tickformat=",", margin=dict(t=10, b=0),
)
st.plotly_chart(fig8, use_container_width=True)

# ── 원본 데이터 ──────────────────────────────────────────────
with st.expander("🗃 원본 데이터 보기"):
    show_cols = ["날짜", "결제일시", "국가", "브랜드", "대분류", "매장 이름",
                 "타이틀명", "프레임 이름", "상품 단가", "쿠폰 할인 금액",
                 "최종 결제 금액", "결제 단위", "KRW환산금액", "결제 수단", "취소 여부"]
    available = [c for c in show_cols if c in df.columns]
    st.dataframe(
        df[available].sort_values("결제일시", ascending=False).reset_index(drop=True),
        use_container_width=True, height=400,
    )
    csv_export = df[available].to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")
    st.download_button("CSV 다운로드", csv_export, "photoism_filtered.csv", "text/csv")
