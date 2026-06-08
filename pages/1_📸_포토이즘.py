import json
import pyarrow.parquet as pq
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
[data-testid="stSidebarNav"] ul li:first-child a::before { content: "📊 "; }
</style>
""", unsafe_allow_html=True)

BASE_DIR     = Path(__file__).parent.parent
AGG_FILE     = BASE_DIR / "data" / "master_photoism_agg.parquet"
HOURLY_FILE  = BASE_DIR / "data" / "master_photoism_hourly.parquet"
PARQUET_FILE = BASE_DIR / "data" / "master_photoism.parquet"
MASTER_FILE  = BASE_DIR / "data" / "master_photoism.csv"
CONFIG_FILE  = BASE_DIR / "config.json"


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
    """집계 parquet 로드 (132 MB, category 인코딩)"""
    if AGG_FILE.exists():
        try:
            table = pq.read_table(str(AGG_FILE))
            df = table.to_pandas(strings_to_categorical=True)
        except Exception as e:
            st.warning(f"집계 파일 로드 실패: {e}")
            return pd.DataFrame()
    else:
        # 집계 파일이 없으면 build_photoism_agg.py 안내
        st.error("집계 파일 없음. `python build_photoism_agg.py` 를 먼저 실행하세요.")
        return pd.DataFrame()

    # 날짜 정리
    df["날짜"] = pd.to_datetime(df["날짜"], errors="coerce").dt.date
    df = df[df["날짜"].notna()]
    # 취소 여부: 이미 bool
    df["취소 여부"] = df["취소 여부"].astype(bool)
    # 수치 컬럼: 이미 int64
    for col in ["건수", "최종 결제 금액", "쿠폰 할인 금액", "서비스코인"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)
        else:
            df[col] = 0

    # 환율 환산
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

    # 국가별 매출액 공식
    _cc = (
        df["국가코드"].astype(str).str.lower().str.strip().replace("nan", "")
        if "국가코드" in df.columns else pd.Series("", index=df.index)
    )
    _coupon_cc = {"la", "gb", "de", "th", "lv", "mx"}
    _coin_cc   = {"cl", "la", "pe", "gb", "de", "lv", "mx"}
    df["매출액"] = (
        df["KRW환산금액"]
        + df["쿠폰KRW"]       * _cc.isin(_coupon_cc).astype(int)
        + df["서비스코인KRW"] * _cc.isin(_coin_cc).astype(int)
    )
    return df


@st.cache_data(ttl=30)
def load_hourly():
    """시간대 집계 parquet 로드 (0.1 MB, 시간대 차트 전용)"""
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


def paid_sales(df):
    return df[~df["취소 여부"] & (df["최종 결제 금액"] >= 0)]

def tx_count(df):
    """집계 데이터에서 실거래 건수 합산"""
    return int(df["건수"].sum()) if "건수" in df.columns else len(df)

def fmt_krw(n): return f"₩{int(n):,}"


# ── 데이터 로드 ──────────────────────────────────────────────
df_all = load_data()

st.title("📸 포토이즘 매출 대시보드")

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

# 타이틀명 (IP) 필터 — 전체 기간 기준으로 목록 생성 (멀티셀렉트)
_ip_all = sorted([
    v for v in df_all["타이틀명"].dropna().astype(str).unique()
    if v.strip() and v not in ("nan", "")
])
selected_ips = st.sidebar.multiselect(
    "프레임 (IP)",
    options=_ip_all,
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

# ── 일별 매출 추이 + 국가별 파이 ──────────────────────────────
col_left, col_right = st.columns([3, 2])

with col_left:
    st.markdown('<div class="section-title">일별 매출 추이</div>', unsafe_allow_html=True)
    daily = (
        sales.groupby("날짜", observed=True)["매출액"].sum()
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
        sales.groupby("국가", observed=True)["매출액"].sum()
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
    sales.groupby("국가", observed=True)
    .agg(매출=("매출액", "sum"), 건수=("건수", "sum"))
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
        sales.groupby("매장 이름", observed=True)
        .agg(매출=("매출액", "sum"), 건수=("건수", "sum"))
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
    title_src = sales[
        sales["타이틀명"].notna() &
        ~sales["타이틀명"].astype(str).str.strip().isin(["", "nan"])
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

# ── IP 선택 시: IP 상세 분석 ──────────────────────────────────
if selected_ips:
    st.divider()

    # 단일 vs 복수 타이틀 표기
    if len(selected_ips) == 1:
        section_label = f"🔥 [{selected_ips[0]}] IP 상세 분석"
    else:
        joined = " + ".join(selected_ips)
        section_label = f"🔥 [{joined}] 합산 분석"

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

        # ── 복수 선택 시: IP별 비교 breakdown ─────────────────
        if len(selected_ips) >= 2:
            st.markdown('<div class="section-title">📊 IP별 매출 비교</div>', unsafe_allow_html=True)
            ip_compare = (
                ip_detail.groupby("타이틀명", observed=True)
                .agg(매출=("매출액", "sum"), 건수=("건수", "sum"))
                .reset_index().sort_values("매출", ascending=False)
            )
            ip_compare["비중"] = (ip_compare["매출"] / ip_compare["매출"].sum() * 100).round(1)

            col_cmp1, col_cmp2 = st.columns([3, 2])
            with col_cmp1:
                fig_cmp = px.bar(
                    ip_compare.sort_values("매출"),
                    x="매출", y="타이틀명", orientation="h",
                    color="타이틀명",
                    color_discrete_sequence=["#7209b7", "#f72585", "#4cc9f0",
                                             "#4361ee", "#3a0ca3", "#560bad"],
                    custom_data=["건수", "비중"],
                )
                fig_cmp.update_traces(
                    hovertemplate="%{y}<br>%{x:,}원  (%{customdata[0]}건, %{customdata[1]:.1f}%)<extra></extra>"
                )
                fig_cmp.update_layout(
                    height=max(200, len(selected_ips) * 60 + 40),
                    showlegend=False,
                    xaxis_tickformat=",", yaxis_title="",
                    margin=dict(t=10, b=0),
                )
                st.plotly_chart(fig_cmp, use_container_width=True)
            with col_cmp2:
                fig_pie_cmp = px.pie(
                    ip_compare, values="매출", names="타이틀명",
                    color_discrete_sequence=["#7209b7", "#f72585", "#4cc9f0",
                                             "#4361ee", "#3a0ca3", "#560bad"],
                    hole=0.45,
                )
                fig_pie_cmp.update_traces(
                    hovertemplate="%{label}<br>%{value:,}원 (%{percent})<extra></extra>"
                )
                fig_pie_cmp.update_layout(height=max(200, len(selected_ips)*60+40), margin=dict(t=10, b=0))
                st.plotly_chart(fig_pie_cmp, use_container_width=True)

            # 각 IP 상세 수치
            tbl_cmp = ip_compare.copy()
            tbl_cmp.index = range(1, len(tbl_cmp)+1)
            tbl_cmp["비중"] = tbl_cmp["비중"].apply(lambda x: f"{x:.1f}%")
            tbl_cmp["매출"] = tbl_cmp["매출"].apply(fmt_krw)
            st.dataframe(tbl_cmp, use_container_width=True)

        col_ip1, col_ip2 = st.columns(2)

        with col_ip1:
            st.markdown('<div class="section-title">일별 매출 추이</div>', unsafe_allow_html=True)
            if len(selected_ips) == 1:
                # 단일: 합산 bar
                ip_daily = (
                    ip_detail.groupby("날짜", observed=True)["매출액"].sum()
                    .reset_index().rename(columns={"매출액": "매출"})
                )
                ip_daily["날짜_str"] = ip_daily["날짜"].astype(str)
                fig_ip_daily = go.Figure()
                fig_ip_daily.add_trace(go.Bar(
                    x=ip_daily["날짜_str"], y=ip_daily["매출"],
                    marker_color="#f72585", opacity=0.85,
                    hovertemplate="%{x}<br>%{y:,}원<extra></extra>",
                ))
                fig_ip_daily.update_layout(
                    height=280, yaxis_tickformat=",",
                    margin=dict(t=10, b=0), showlegend=False,
                )
            else:
                # 복수: IP별 Line + 합산 Bar
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
                        x=ip_d["날짜_str"], y=ip_d["매출"],
                        name=ip_name[:18], mode="lines+markers",
                        line=dict(color=_colors[idx % len(_colors)], width=2),
                        hovertemplate=f"{ip_name[:18]}<br>%{{x}}<br>%{{y:,}}원<extra></extra>",
                    ))
                # 합산
                ip_total = (
                    ip_detail.groupby("날짜", observed=True)["매출액"].sum()
                    .reset_index().rename(columns={"매출액": "매출"})
                )
                ip_total["날짜_str"] = ip_total["날짜"].astype(str)
                fig_ip_daily.add_trace(go.Bar(
                    x=ip_total["날짜_str"], y=ip_total["매출"],
                    name="합산", marker_color="rgba(0,0,0,0.1)",
                    hovertemplate="합산<br>%{x}<br>%{y:,}원<extra></extra>",
                ))
                fig_ip_daily.update_layout(
                    height=280, yaxis_tickformat=",",
                    legend=dict(orientation="h", y=1.12, font_size=11),
                    margin=dict(t=30, b=0),
                )
            st.plotly_chart(fig_ip_daily, use_container_width=True)

        with col_ip2:
            st.markdown('<div class="section-title">국가별 매출 분포</div>', unsafe_allow_html=True)
            ip_nat = (
                ip_detail.groupby("국가", observed=True)["매출액"].sum()
                .reset_index().sort_values("매출액", ascending=False)
            )
            fig_ip_nat = px.pie(
                ip_nat, values="매출액", names="국가",
                color_discrete_sequence=px.colors.qualitative.Pastel, hole=0.4,
            )
            fig_ip_nat.update_traces(hovertemplate="%{label}<br>%{value:,}원 (%{percent})<extra></extra>")
            fig_ip_nat.update_layout(height=280, margin=dict(t=10, b=0))
            st.plotly_chart(fig_ip_nat, use_container_width=True)

        # 국가별 상세 테이블
        ip_nat_tbl = (
            ip_detail.groupby("국가", observed=True)
            .agg(매출=("매출액","sum"), 건수=("건수","sum"))
            .reset_index().sort_values("매출", ascending=False)
            .reset_index(drop=True)
        )
        ip_nat_tbl.index = ip_nat_tbl.index + 1
        ip_nat_tbl["비중"] = (ip_nat_tbl["매출"] / ip_nat_tbl["매출"].sum() * 100).round(1).apply(lambda x: f"{x:.1f}%")
        ip_nat_tbl["매출"] = ip_nat_tbl["매출"].apply(fmt_krw)
        st.dataframe(ip_nat_tbl, use_container_width=True, height=min(400, len(ip_nat_tbl)*40+55))

    st.divider()

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
    ip_rev = ip_src.groupby(["국가", "타이틀명"], observed=True)["매출액"].sum().reset_index()
    ip_rev["순위"] = (
        ip_rev.groupby("국가", observed=True)["매출액"]
        .rank(ascending=False, method="first").astype(int)
    )
    ip_rev = ip_rev[ip_rev["순위"] <= 10]
    ip_rev["표시"] = (
        ip_rev["타이틀명"].str[:20]
        + "  ("
        + (ip_rev["매출액"] / 10000).round(0).astype(int).map(lambda x: f"{x:,}만")
        + ")"
    )
    ip_rev["순위_label"] = ip_rev["순위"].apply(lambda x: f"{x}위")

    pivot_rank = ip_rev.pivot(index="국가", columns="순위_label", values="표시")
    rank_cols  = [f"{i}위" for i in range(1, 11)]
    pivot_rank = pivot_rank.reindex(columns=[c for c in rank_cols if c in pivot_rank.columns])

    nat_total  = ip_src.groupby("국가", observed=True)["매출액"].sum().sort_values(ascending=False)
    pivot_rank = pivot_rank.reindex(nat_total.index).dropna(how="all").fillna("-")
    pivot_rank.columns.name = None

    st.dataframe(pivot_rank, use_container_width=True,
                 height=min(700, len(pivot_rank) * 35 + 50))

    with st.expander("▸ 국가별 상세 IP 순위 바차트"):
        ip_nations    = sorted(ip_src["국가"].dropna().unique().tolist())
        sel_ip_nation = st.selectbox("국가 선택", ["전체"] + ip_nations, key="ip_nation_detail")
        detail_src    = ip_src if sel_ip_nation == "전체" else ip_src[ip_src["국가"] == sel_ip_nation]
        detail_ip     = (
            detail_src.groupby("타이틀명", observed=True)
            .agg(매출=("매출액", "sum"), 건수=("건수", "sum"))
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

df_hourly = load_hourly()
if not df_hourly.empty and len(date_range) == 2:
    # 날짜 필터만 적용 (국가/매장 필터는 hourly 파일에 없음)
    df_hourly = df_hourly[
        (df_hourly["날짜"] >= date_range[0]) &
        (df_hourly["날짜"] <= date_range[1]) &
        (~df_hourly["취소 여부"])
    ]

if df_hourly.empty:
    st.info("시간대 데이터가 없습니다.")
else:
    hourly = (
        df_hourly[df_hourly["시간대"] >= 0]
        .groupby("시간대")
        .agg(매출=("최종 결제 금액", "sum"), 건수=("건수", "sum"))
        .reindex(range(24), fill_value=0)
        .reset_index()
        .rename(columns={"시간대": "시간"})
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
    if selected_country != "전체" or selected_store != "전체" or selected_brand != "전체":
        st.caption("ℹ️ 시간대 차트는 날짜 필터만 적용됩니다 (국가/매장 필터 미적용)")

# ── 집계 데이터 보기 ──────────────────────────────────────────
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
