import json
import streamlit as st
import streamlit.components.v1 as components
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from pathlib import Path
from datetime import date, timedelta

st.set_page_config(
    page_title="스내피즘 매출 대시보드",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
[data-testid="metric-container"] {
    background: #f8f9fa;
    border: 1px solid #e9ecef;
    border-radius: 10px;
    padding: 12px 20px;
}
[data-testid="stMetricDelta"] { font-size: 0.85rem; }
.section-title { font-size: 1.05rem; font-weight: 600; margin-bottom: 2px; }
[data-testid="stDeployButton"] { display: none !important; }
/* 메인 페이지 사이드바 아이콘 */
[data-testid="stSidebarNav"] ul li:first-child a::before { content: "📊 "; }
</style>
""", unsafe_allow_html=True)

components.html("""
<script>
(function() {
    const T = {
        'Rerun': '새로고침',
        'Settings': '설정',
        'Print': '인쇄',
        'Record a screencast': '화면 녹화',
        'About': '정보',
        'Developer options': '개발자 옵션',
        'Clear cache': '캐시 초기화',
    };

    function translateNode(root) {
        try {
            const doc = root.ownerDocument || root;
            const walker = doc.createTreeWalker(root, NodeFilter.SHOW_TEXT);
            const nodes = [];
            while (walker.nextNode()) nodes.push(walker.currentNode);
            nodes.forEach(n => {
                const t = n.textContent.trim();
                if (T[t]) n.textContent = T[t];
            });
        } catch(e) {}
    }

    function init() {
        try {
            const doc = window.parent.document;
            const obs = new MutationObserver(mutations => {
                mutations.forEach(m => {
                    m.addedNodes.forEach(node => {
                        if (node.nodeType === 1) translateNode(node);
                    });
                });
            });
            obs.observe(doc.body, {childList: true, subtree: true});
        } catch(e) {}
    }

    init();
})();
</script>
""", height=0)

BASE_DIR = Path(__file__).parent
MASTER_FILE = BASE_DIR / "data" / "master.csv"
CONFIG_FILE = BASE_DIR / "config.json"

CURRENCY_SYMBOLS = {
    "KRW": "₩", "CNY": "¥", "JPY": "¥",
    "IDR": "Rp", "TWD": "NT$", "THB": "฿",
    "HKD": "HK$", "MYR": "RM",
}


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

    # 통화 → KRW 환산
    ex = load_exchange_rates()
    df["결제 단위"] = df["결제 단위"].fillna("KRW").astype(str).str.strip()
    df["환율"] = df["결제 단위"].map(ex).fillna(1)
    df["KRW환산금액"] = (df["최종 결제 금액"] * df["환율"]).round(0).astype(int)
    df["쿠폰KRW"] = (df["쿠폰 할인 금액"] * df["환율"]).round(0).astype(int)
    # 정산 기준 합계 (실결제 + 쿠폰할인)
    df["정산금액"] = df["KRW환산금액"] + df["쿠폰KRW"]
    df["총원화금액"] = df["최종 결제 금액"] + df["쿠폰 할인 금액"]

    return df


def paid_sales(df):
    """취소 제외 + 실제 결제금액 > 0 (국가/결제수단 무관)"""
    return df[~df["취소 여부"] & (df["최종 결제 금액"] > 0)]


def coupon_txns(df):
    """100% 쿠폰 결제 (최종금액 0, 쿠폰할인 > 0)"""
    return df[~df["취소 여부"] & (df["최종 결제 금액"] == 0) & (df["쿠폰 할인 금액"] > 0)]


def total_rev(df):
    """정산 기준 합계: 실결제 KRW + 쿠폰할인 KRW 전체 (100% 쿠폰 + 부분 쿠폰)"""
    p = paid_sales(df)
    c = coupon_txns(df)
    return int(p["KRW환산금액"].sum() + p["쿠폰KRW"].sum() + c["쿠폰KRW"].sum())


def fmt_krw(num):
    return f"₩{int(num):,}"


def fmt_orig(amount, currency):
    sym = CURRENCY_SYMBOLS.get(currency, currency)
    return f"{sym}{amount:,}"


# ── 데이터 로드 ──────────────────────────────────────────────
df_all = load_data()
ex_rates = load_exchange_rates()

st.title("📊 스내피즘 매출 대시보드")

if df_all.empty:
    st.warning("데이터가 없습니다. `raw` 폴더에 CSV를 넣고 `데이터추가.bat`을 실행하세요.")
    st.stop()

last_date = df_all["날짜"].max()
first_date = df_all["날짜"].min()
st.caption(f"데이터 범위: {first_date} ~ {last_date}  |  총 {len(df_all):,}건  |  새로고침: F5")

# ── 사이드바 필터 ────────────────────────────────────────────
st.sidebar.header("🔍 필터")

default_start = max(last_date - timedelta(days=29), first_date)
date_range = st.sidebar.date_input(
    "날짜 범위",
    value=[default_start, last_date],
    min_value=first_date,
    max_value=last_date,
)

KNOWN_COUNTRIES = [
    "대한민국", "일본", "중국", "대만", "인도네시아", "홍콩", "태국", "말레이시아"
]

if "국가" in df_all.columns:
    data_countries = set(
        df_all["국가"].dropna().replace("nan", pd.NA).dropna().unique().tolist()
    )
    all_countries = sorted(data_countries | set(KNOWN_COUNTRIES))
    countries = ["전체"] + all_countries
    selected_country = st.sidebar.selectbox("국가", countries)
else:
    selected_country = "전체"

stores = ["전체"] + sorted(df_all["매장 이름"].dropna().unique().tolist())
selected_store = st.sidebar.selectbox("매장", stores)

cats = ["전체"] + sorted(
    df_all["카테고리"].dropna().replace("nan", pd.NA).dropna().unique().tolist()
)
selected_cat = st.sidebar.selectbox("카테고리 (아티스트/캐릭터)", cats)

prod_cats = ["전체"] + sorted(df_all["상품 카테고리"].dropna().unique().tolist())
selected_prod = st.sidebar.selectbox("상품 카테고리", prod_cats)

frames_list = ["전체"] + sorted(
    df_all["프레임 이름"].dropna().replace("nan", pd.NA).dropna().unique().tolist()
)
selected_frame = st.sidebar.selectbox("프레임 (IP)", frames_list)

st.sidebar.divider()
_cfg = load_config()
_updated = _cfg.get("rates_updated", "")
st.sidebar.caption(f"💱 실시간 환율{'  |  ' + _updated if _updated else ''}")
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
all_txns = pd.concat([sales, coupons])   # 정산 기준 전체 (실결제 + 100% 쿠폰)

# ── KPI 카드 ─────────────────────────────────────────────────
today = date.today()
yesterday = today - timedelta(days=1)
month_start = today.replace(day=1)

today_sales  = paid_sales(df_all[df_all["날짜"] == today])
yest_sales   = paid_sales(df_all[df_all["날짜"] == yesterday])

today_amt  = total_rev(df_all[df_all["날짜"] == today])
yest_amt   = total_rev(df_all[df_all["날짜"] == yesterday])
month_amt  = total_rev(df_all[df_all["날짜"] >= month_start])

delta_pct = ((today_amt - yest_amt) / yest_amt * 100) if yest_amt > 0 else 0

cancelled_amt = df[df["취소 여부"]]["KRW환산금액"].sum()
coupon_amt    = coupons["쿠폰KRW"].sum() + sales["쿠폰KRW"].sum()
period_amt    = int(sales["KRW환산금액"].sum()) + int(coupon_amt)
coupon_cnt    = len(coupons) + len(sales[sales["쿠폰 할인 금액"] > 0])

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("오늘 매출+쿠폰 (KRW)", fmt_krw(today_amt), f"{delta_pct:+.1f}% vs 어제")
c2.metric("어제 매출+쿠폰 (KRW)", fmt_krw(yest_amt), f"{len(yest_sales):,}건")
c3.metric("이번 달 누적", fmt_krw(month_amt), f"{month_start.strftime('%m/%d')}~오늘")
c4.metric("쿠폰 할인 총액", fmt_krw(coupon_amt), f"{coupon_cnt:,}건 사용")
c5.metric("조회기간 합계", fmt_krw(period_amt), f"매출+쿠폰 | 취소 {fmt_krw(cancelled_amt)}")

st.divider()

# ── 일별 트렌드 + 카테고리 파이 ─────────────────────────────
col_left, col_right = st.columns([3, 2])

with col_left:
    st.markdown('<div class="section-title">일별 매출 추이 (실결제 + 쿠폰할인 합산)</div>', unsafe_allow_html=True)

    daily_paid = sales.groupby("날짜")["KRW환산금액"].sum().rename("실결제")
    # 쿠폰 합산: 100% 쿠폰(쿠폰KRW) + 부분쿠폰(sales의 쿠폰KRW)
    daily_cpn_100  = coupons.groupby("날짜")["쿠폰KRW"].sum()
    daily_cpn_part = sales[sales["쿠폰 할인 금액"] > 0].groupby("날짜")["쿠폰KRW"].sum()
    daily_coupon   = daily_cpn_100.add(daily_cpn_part, fill_value=0).rename("쿠폰할인")

    daily = pd.DataFrame({"실결제": daily_paid, "쿠폰할인": daily_coupon}).fillna(0)
    daily["합계"] = daily["실결제"] + daily["쿠폰할인"]
    daily["7일 평균"] = daily["합계"].rolling(7, min_periods=1).mean().round(0)
    daily = daily.reset_index()
    daily["날짜_str"] = daily["날짜"].astype(str)

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=daily["날짜_str"], y=daily["실결제"],
        name="실결제", marker_color="#4361ee", opacity=0.85,
        hovertemplate="%{x}<br>실결제 %{y:,}원<extra></extra>",
    ))
    fig.add_trace(go.Bar(
        x=daily["날짜_str"], y=daily["쿠폰할인"],
        name="쿠폰할인", marker_color="#4cc9f0", opacity=0.85,
        hovertemplate="%{x}<br>쿠폰할인 %{y:,}원<extra></extra>",
    ))
    fig.add_trace(go.Scatter(
        x=daily["날짜_str"], y=daily["7일 평균"],
        name="7일 이동평균(합계)", line=dict(color="#f72585", width=2),
        hovertemplate="%{x}<br>평균 %{y:,.0f}원<extra></extra>",
    ))
    fig.update_layout(
        barmode="stack",
        height=340, yaxis_tickformat=",",
        legend=dict(orientation="h", y=1.08),
        margin=dict(t=20, b=0),
    )
    st.plotly_chart(fig, use_container_width=True)

with col_right:
    st.markdown('<div class="section-title">상품 카테고리별 매출</div>', unsafe_allow_html=True)
    cat_df = (
        all_txns.groupby("상품 카테고리")["정산금액"]
        .sum().reset_index().sort_values("정산금액", ascending=False)
    )
    fig2 = px.pie(
        cat_df, values="정산금액", names="상품 카테고리",
        color_discrete_sequence=px.colors.qualitative.Set2, hole=0.4,
    )
    fig2.update_traces(hovertemplate="%{label}<br>%{value:,}원 (%{percent})<extra></extra>")
    fig2.update_layout(height=340, margin=dict(t=20, b=0))
    st.plotly_chart(fig2, use_container_width=True)

# ── 국가별 분석 ──────────────────────────────────────────────
st.markdown('<div class="section-title">🌏 국가별 매출 분석</div>', unsafe_allow_html=True)

col_nat, col_coupon = st.columns([3, 2])

with col_nat:
    # 실매출 + 쿠폰 합산
    nat = (
        all_txns.groupby(["국가", "결제 단위"])
        .agg(
            건수=("정산금액", "count"),
            원화금액=("총원화금액", "sum"),
            KRW환산=("정산금액", "sum"),
        )
        .reset_index()
        .sort_values("KRW환산", ascending=False)
    )
    nat["원화금액_표시"] = nat.apply(
        lambda r: fmt_orig(r["원화금액"], r["결제 단위"]), axis=1
    )
    nat["KRW환산_표시"] = nat["KRW환산"].apply(fmt_krw)
    nat["비중"] = (nat["KRW환산"] / nat["KRW환산"].sum() * 100).round(1).astype(str) + "%"

    st.dataframe(
        nat[["국가", "결제 단위", "건수", "원화금액_표시", "KRW환산_표시", "비중"]]
        .rename(columns={"원화금액_표시": "원화금액", "KRW환산_표시": "KRW 환산"}),
        use_container_width=True, hide_index=True,
    )

    # 국가별 정산금액 막대 차트
    fig_nat = px.bar(
        nat.sort_values("KRW환산"),
        x="KRW환산", y="국가", orientation="h",
        color="KRW환산", color_continuous_scale="Teal",
        custom_data=["원화금액_표시", "결제 단위", "건수"],
    )
    fig_nat.update_traces(
        hovertemplate="%{y}<br>정산기준: %{x:,}원<br>원화: %{customdata[0]}  (%{customdata[2]}건)<extra></extra>"
    )
    fig_nat.update_layout(
        height=280, coloraxis_showscale=False,
        xaxis_tickformat=",", yaxis_title="",
        margin=dict(t=5, b=0),
    )
    st.plotly_chart(fig_nat, use_container_width=True)

with col_coupon:
    st.markdown("**쿠폰 할인 현황**")

    # 쿠폰 사용: 100% 할인 건 + 일부 할인 건 합산
    all_coupon = pd.concat([coupons, sales[sales["쿠폰 할인 금액"] > 0]])
    if not all_coupon.empty:
        cpn = (
            all_coupon.groupby(["국가", "결제 단위"])
            .agg(
                쿠폰건수=("쿠폰 할인 금액", "count"),
                할인금액=("쿠폰 할인 금액", "sum"),
                할인KRW=("쿠폰KRW", "sum"),
            )
            .reset_index()
            .sort_values("할인KRW", ascending=False)
        )
        cpn["할인금액_표시"] = cpn.apply(
            lambda r: fmt_orig(r["할인금액"], r["결제 단위"]), axis=1
        )
        cpn["할인KRW_표시"] = cpn["할인KRW"].apply(fmt_krw)
        st.dataframe(
            cpn[["국가", "결제 단위", "쿠폰건수", "할인금액_표시", "할인KRW_표시"]]
            .rename(columns={"할인금액_표시": "할인금액", "할인KRW_표시": "KRW 환산"}),
            use_container_width=True, hide_index=True,
        )

        total_cpn = all_coupon["쿠폰KRW"].sum()
        st.info(f"쿠폰 총 할인: **{fmt_krw(total_cpn)}** ({len(all_coupon):,}건)")
    else:
        st.info("조회 기간 내 쿠폰 사용 없음")

# ── 매장별 / 프레임별 ────────────────────────────────────────
col_a, col_b = st.columns(2)

with col_a:
    st.markdown('<div class="section-title">매장별 매출 TOP 10</div>', unsafe_allow_html=True)
    store_df = (
        all_txns.groupby("매장 이름")
        .agg(매출=("정산금액", "sum"), 건수=("정산금액", "count"))
        .reset_index()
        .nlargest(10, "매출").sort_values("매출")
    )
    fig3 = px.bar(
        store_df, x="매출", y="매장 이름", orientation="h",
        color="매출", color_continuous_scale="Blues", custom_data=["건수"],
    )
    fig3.update_traces(hovertemplate="%{y}<br>%{x:,}원  (%{customdata[0]}건)<extra></extra>")
    fig3.update_layout(
        height=380, coloraxis_showscale=False,
        xaxis_tickformat=",", yaxis_title="", margin=dict(t=10, b=0),
    )
    st.plotly_chart(fig3, use_container_width=True)

with col_b:
    st.markdown('<div class="section-title">프레임(아티스트/IP) TOP 10</div>', unsafe_allow_html=True)
    frame_all_df = (
        all_txns[all_txns["프레임 이름"].notna() & (all_txns["프레임 이름"] != "nan")]
        .groupby("프레임 이름")
        .agg(매출=("정산금액", "sum"), 건수=("정산금액", "count"))
        .reset_index()
        .sort_values("매출", ascending=False)
    )
    frame_df = frame_all_df.nlargest(10, "매출").sort_values("매출")
    fig4 = px.bar(
        frame_df, x="매출", y="프레임 이름", orientation="h",
        color="매출", color_continuous_scale="Purples", custom_data=["건수"],
    )
    fig4.update_traces(hovertemplate="%{y}<br>%{x:,}원  (%{customdata[0]}건)<extra></extra>")
    fig4.update_layout(
        height=380, coloraxis_showscale=False,
        xaxis_tickformat=",", yaxis_title="", margin=dict(t=10, b=0),
    )
    st.plotly_chart(fig4, use_container_width=True)

    with st.expander(f"📋 전체 프레임 보기 ({len(frame_all_df)}개)"):
        frame_show = frame_all_df.copy().reset_index(drop=True)
        frame_show.index = frame_show.index + 1
        frame_show["매출"] = frame_show["매출"].apply(lambda x: f"₩{x:,}")
        frame_show.columns = ["프레임 이름", "매출", "건수"]
        st.dataframe(frame_show, use_container_width=True, height=400)

# ── 카테고리 내 상품 이름 순위 ───────────────────────────────────
st.markdown('<div class="section-title">카테고리별 상품 순위</div>', unsafe_allow_html=True)

avail_cats = sorted(all_txns["상품 카테고리"].dropna().unique().tolist())
default_cat = "미니스티커" if "미니스티커" in avail_cats else (avail_cats[0] if avail_cats else None)

col_pcat, _ = st.columns([2, 8])
with col_pcat:
    pick_cat = st.selectbox(
        "카테고리 선택",
        avail_cats,
        index=avail_cats.index(default_cat) if default_cat in avail_cats else 0,
        key="prod_rank_cat",
    )

prod_rank_df = (
    all_txns[all_txns["상품 카테고리"] == pick_cat]
    .groupby("상품 이름")
    .agg(매출=("정산금액", "sum"), 건수=("정산금액", "count"))
    .reset_index()
    .sort_values("매출", ascending=False)
)

if prod_rank_df.empty:
    st.info("해당 카테고리의 데이터가 없습니다.")
else:
    col_pr_chart, col_pr_tbl = st.columns([7, 3])
    with col_pr_chart:
        top_prod = prod_rank_df.head(15).sort_values("매출")
        fig_pr = px.bar(
            top_prod, x="매출", y="상품 이름", orientation="h",
            color="매출", color_continuous_scale="Oranges", custom_data=["건수"],
        )
        fig_pr.update_traces(hovertemplate="%{y}<br>%{x:,}원  (%{customdata[0]}건)<extra></extra>")
        fig_pr.update_layout(
            height=max(300, len(top_prod) * 38 + 60),
            coloraxis_showscale=False,
            xaxis_tickformat=",", yaxis_title="",
            margin=dict(t=10, b=20, l=10, r=10),
        )
        st.plotly_chart(fig_pr, use_container_width=True)
    with col_pr_tbl:
        pr_tbl = prod_rank_df.head(15).reset_index(drop=True)
        pr_tbl.index = pr_tbl.index + 1
        total_pr = pd.DataFrame([{"상품 이름": "합계", "매출": prod_rank_df["매출"].sum(), "건수": prod_rank_df["건수"].sum()}])
        pr_tbl = pd.concat([pr_tbl, total_pr], ignore_index=True)
        pr_tbl.index = list(range(1, min(len(prod_rank_df), 15) + 1)) + ["∑"]
        pr_tbl["비중"] = (pr_tbl["매출"] / prod_rank_df["매출"].sum() * 100).round(1).astype(str) + "%"
        pr_tbl.loc["∑", "비중"] = "100%"
        pr_tbl["매출"] = pr_tbl["매출"].apply(lambda x: f"₩{int(x):,}")
        st.dataframe(pr_tbl, use_container_width=True, height=max(300, len(top_prod) * 38 + 80))

    if len(prod_rank_df) > 15:
        with st.expander(f"📋 전체 보기 ({len(prod_rank_df)}개)"):
            full_tbl = prod_rank_df.reset_index(drop=True)
            full_tbl.index = full_tbl.index + 1
            full_tbl["비중"] = (full_tbl["매출"] / full_tbl["매출"].sum() * 100).round(1).astype(str) + "%"
            full_tbl["매출"] = full_tbl["매출"].apply(lambda x: f"₩{int(x):,}")
            st.dataframe(full_tbl, use_container_width=True, height=400)

# ── 상품 카테고리별 매출 (전체 너비) ─────────────────────────────
st.markdown('<div class="section-title">상품 카테고리별 매출</div>', unsafe_allow_html=True)
cat_bar_df = (
    all_txns.groupby("상품 카테고리")
    .agg(매출=("정산금액", "sum"), 건수=("정산금액", "count"))
    .reset_index()
    .sort_values("매출")
)
col_cat_chart, col_cat_tbl = st.columns([7, 3])
with col_cat_chart:
    fig_cat = px.bar(
        cat_bar_df, x="매출", y="상품 카테고리", orientation="h",
        color="매출", color_continuous_scale="Greens", custom_data=["건수"],
    )
    fig_cat.update_traces(hovertemplate="%{y}<br>%{x:,}원  (%{customdata[0]}건)<extra></extra>")
    fig_cat.update_layout(
        height=max(260, len(cat_bar_df) * 48 + 80),
        coloraxis_showscale=False,
        xaxis_tickformat=",", yaxis_title="",
        margin=dict(t=10, b=0),
    )
    st.plotly_chart(fig_cat, use_container_width=True)
with col_cat_tbl:
    tbl = cat_bar_df.sort_values("매출", ascending=False).reset_index(drop=True)
    tbl.index = tbl.index + 1
    total = pd.DataFrame([{"상품 카테고리": "합계", "매출": tbl["매출"].sum(), "건수": tbl["건수"].sum()}])
    tbl = pd.concat([tbl, total], ignore_index=True)
    tbl.index = list(range(1, len(cat_bar_df) + 1)) + ["∑"]
    tbl["비중"] = (tbl["매출"] / tbl["매출"].iloc[:-1].sum() * 100).round(1).astype(str) + "%"
    tbl.loc["∑", "비중"] = "100%"
    tbl["매출"] = tbl["매출"].apply(lambda x: f"₩{int(x):,}")
    st.dataframe(tbl, use_container_width=True, height=max(260, len(cat_bar_df) * 48 + 80))

# ── IP별 상품 카테고리 상세 ────────────────────────────────────
with st.expander("🔍 IP별 상품 카테고리 상세", expanded=(selected_frame != "전체")):
    frame_src = all_txns[all_txns["프레임 이름"].notna() & (all_txns["프레임 이름"] != "nan")]
    ip_options = ["전체"] + sorted(frame_src["프레임 이름"].unique().tolist())

    col_ip1, col_ip2 = st.columns([2, 8])
    with col_ip1:
        ip_pick = st.selectbox(
            "IP / 프레임 선택",
            ip_options,
            index=ip_options.index(selected_frame) if selected_frame in ip_options else 0,
            key="ip_detail_select",
        )

    ip_data = frame_src if ip_pick == "전체" else frame_src[frame_src["프레임 이름"] == ip_pick]

    if ip_data.empty:
        st.info("해당 기간에 데이터가 없습니다.")
    else:
        ip_cat = (
            ip_data.groupby("상품 카테고리")
            .agg(매출=("정산금액", "sum"), 건수=("정산금액", "count"))
            .reset_index()
            .sort_values("매출", ascending=False)
        )

        col_ip_chart, col_ip_tbl = st.columns([6, 4])
        with col_ip_chart:
            title_label = ip_pick if ip_pick != "전체" else "전체 IP"
            fig_ip = px.bar(
                ip_cat.sort_values("매출"),
                x="매출", y="상품 카테고리", orientation="h",
                color="매출", color_continuous_scale="Teal", custom_data=["건수"],
                title=f"{title_label} · 상품 카테고리별 매출",
            )
            fig_ip.update_traces(hovertemplate="%{y}<br>%{x:,}원  (%{customdata[0]}건)<extra></extra>")
            fig_ip.update_layout(
                height=max(250, len(ip_cat) * 40 + 80),
                coloraxis_showscale=False,
                xaxis_tickformat=",", yaxis_title="",
                margin=dict(t=40, b=0),
            )
            st.plotly_chart(fig_ip, use_container_width=True)

        with col_ip_tbl:
            ip_tbl = ip_cat.copy().reset_index(drop=True)
            ip_tbl.index = ip_tbl.index + 1
            total_row = pd.DataFrame([{
                "상품 카테고리": "합계",
                "매출": ip_tbl["매출"].sum(),
                "건수": ip_tbl["건수"].sum(),
            }])
            ip_tbl = pd.concat([ip_tbl, total_row], ignore_index=True)
            ip_tbl.index = list(range(1, len(ip_cat) + 1)) + ["∑"]
            ip_tbl["매출"] = ip_tbl["매출"].apply(lambda x: f"₩{int(x):,}")
            st.dataframe(ip_tbl, use_container_width=True)

# ── 시간대별 ─────────────────────────────────────────────────
st.markdown('<div class="section-title">시간대별 매출 분포</div>', unsafe_allow_html=True)
hourly = (
    all_txns.assign(시간대=all_txns["결제일시"].dt.hour)
    .groupby("시간대")["정산금액"]
    .agg(["sum", "count"])
    .reindex(range(24), fill_value=0)
    .reset_index()
    .rename(columns={"시간대": "시간", "sum": "매출", "count": "건수"})
)
hourly["시간_label"] = hourly["시간"].apply(lambda h: f"{h:02d}:00")

fig5 = px.bar(
    hourly, x="시간_label", y="매출",
    color="매출", color_continuous_scale="Oranges", custom_data=["건수"],
)
fig5.update_traces(hovertemplate="%{x}<br>%{y:,}원  (%{customdata[0]}건)<extra></extra>")
fig5.update_layout(
    height=260, coloraxis_showscale=False,
    xaxis_title="", yaxis_tickformat=",", margin=dict(t=10, b=0),
)
st.plotly_chart(fig5, use_container_width=True)

# ── 주간 비교 ────────────────────────────────────────────────
with st.expander("📅 주간 매출 비교"):
    weekly_all = pd.concat([paid_sales(df_all), coupon_txns(df_all)])
    weekly = (
        weekly_all
        .assign(주차=lambda d: d["결제일시"].dt.to_period("W").astype(str))
        .groupby("주차")["정산금액"].sum()
        .reset_index().tail(8)
    )
    fig6 = px.bar(weekly, x="주차", y="정산금액", color_discrete_sequence=["#4cc9f0"])
    fig6.update_layout(yaxis_tickformat=",", height=280, margin=dict(t=10, b=0))
    fig6.update_traces(hovertemplate="%{x}<br>%{y:,}원<extra></extra>")
    st.plotly_chart(fig6, use_container_width=True)

# ── 원본 데이터 ──────────────────────────────────────────────
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
