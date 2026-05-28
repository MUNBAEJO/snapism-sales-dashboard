import json
import sys
import streamlit as st
import pandas as pd
import plotly.express as px
from pathlib import Path
from datetime import date, timedelta

sys.path.insert(0, str(Path(__file__).parent.parent))
from jira_client import fetch_rs_data

st.set_page_config(page_title="IP 정산 현황", page_icon="💰", layout="wide")

BASE_DIR   = Path(__file__).parent.parent
MASTER     = BASE_DIR / "data" / "master.csv"
CONFIG     = BASE_DIR / "config.json"

CURRENCY_SYMBOLS = {
    "KRW": "₩", "CNY": "¥", "JPY": "¥",
    "IDR": "Rp", "TWD": "NT$", "THB": "฿", "HKD": "HK$", "MYR": "RM",
}

# ── CSS ─────────────────────────────────────────────────────────
st.markdown("""
<style>
[data-testid="metric-container"] {
    background:#f8f9fa; border:1px solid #e9ecef;
    border-radius:10px; padding:12px 20px;
}
.section-title { font-size:1.05rem; font-weight:600; margin-bottom:4px; }
[data-testid="stDeployButton"] { display:none !important; }
</style>
""", unsafe_allow_html=True)


def load_exchange_rates():
    try:
        with open(CONFIG, encoding="utf-8") as f:
            return json.load(f).get("exchange_rates", {"KRW": 1})
    except Exception:
        return {"KRW": 1}


@st.cache_data(ttl=30)
def load_sales():
    if not MASTER.exists():
        return pd.DataFrame()
    df = pd.read_csv(MASTER, encoding="utf-8-sig", low_memory=False)
    df["날짜"] = pd.to_datetime(df["날짜"], errors="coerce").dt.date
    df["결제일시"] = pd.to_datetime(df["결제일시"], format="%Y.%m.%d %H:%M", errors="coerce")
    df["취소 여부"] = df["취소 여부"].astype(str).str.lower().isin(["true", "1", "yes"])
    for col in ["최종 결제 금액", "쿠폰 할인 금액"]:
        df[col] = pd.to_numeric(df.get(col, 0), errors="coerce").fillna(0).astype(int)
    ex = load_exchange_rates()
    df["결제 단위"] = df["결제 단위"].fillna("KRW").astype(str).str.strip()
    df["환율"] = df["결제 단위"].map(ex).fillna(1)
    df["KRW환산"] = (df["최종 결제 금액"] * df["환율"]).round(0).astype(int)
    df["쿠폰KRW"]  = (df["쿠폰 할인 금액"]  * df["환율"]).round(0).astype(int)
    df["프레임 이름"] = df["프레임 이름"].astype(str).str.strip().replace("nan", "")
    return df


@st.cache_data(ttl=3600)
def load_jira():
    try:
        return fetch_rs_data()
    except Exception as e:
        return {"_error": str(e)}


def fmt_krw(n):
    return f"₩{int(n):,}"


def fmt_pct(v):
    if v is None:
        return "-"
    return f"{v*100:.1f}%"


def find_matching_frames(ip_name: str, all_frames: list) -> list:
    """IP 이름으로 매출 데이터 프레임 이름 후보 탐색 (대소문자 무시, 부분 포함)"""
    ip_lower = ip_name.lower()
    matches = []
    for fr in all_frames:
        fr_lower = fr.lower()
        if ip_lower in fr_lower or fr_lower in ip_lower:
            matches.append(fr)
    return matches


# ── 데이터 로드 ───────────────────────────────────────────────
st.title("💰 IP 정산 현황")

df_all = load_sales()
jira_data = load_jira()

if df_all.empty:
    st.warning("매출 데이터가 없습니다.")
    st.stop()

if "_error" in jira_data:
    st.error(f"Jira 연결 오류: {jira_data['_error']}")
    st.info("config.json의 jira 설정을 확인하세요.")
    jira_data = {}

# ── 사이드바 ─────────────────────────────────────────────────
st.sidebar.header("🔍 정산 조건")

# 날짜 범위
first_date = df_all["날짜"].min()
last_date  = df_all["날짜"].max()
default_start = max(last_date - timedelta(days=29), first_date)
date_range = st.sidebar.date_input(
    "정산 기간",
    value=[default_start, last_date],
    min_value=first_date, max_value=last_date,
)

# Jira IP 선택
if jira_data:
    ip_options = sorted(jira_data.keys())
    selected_ip = st.sidebar.selectbox(
        "Jira IP 선택",
        options=ip_options,
        format_func=lambda k: f"{k}  [{fmt_pct(jira_data[k]['rs_agency'])} / {fmt_pct(jira_data[k]['rs_mgmt'])}]"
    )
    entry = jira_data[selected_ip]

    st.sidebar.divider()
    st.sidebar.markdown(f"**티켓**: [{entry['ticket_key']}]({BASE_DIR.parent})")
    st.sidebar.markdown(f"**제목**: {entry['title']}")
    st.sidebar.markdown(f"**소속사 RS**: {fmt_pct(entry['rs_agency'])}")
    st.sidebar.markdown(f"**대행사 RS**: {fmt_pct(entry['rs_mgmt'])}")
else:
    selected_ip = None
    entry = {}

# ── 프레임 이름 매핑 ────────────────────────────────────────
all_frames = sorted(
    fr for fr in df_all["프레임 이름"].unique() if fr and fr != "nan"
)

st.markdown('<div class="section-title">📌 프레임 이름 매핑</div>', unsafe_allow_html=True)

col_map1, col_map2 = st.columns([1, 2])
with col_map1:
    st.caption(f"Jira IP: **{selected_ip or '-'}**")
    if entry.get("wbs"):
        st.caption(f"WBS: {entry['wbs']}")

auto_matches = find_matching_frames(selected_ip or "", all_frames) if selected_ip else []

with col_map2:
    selected_frames = st.multiselect(
        "매핑할 프레임 이름 선택 (자동 추천 포함, 추가/수정 가능)",
        options=all_frames,
        default=auto_matches,
        help="여러 프레임을 동시에 선택하면 합산 정산됩니다",
    )

st.divider()

if not selected_frames:
    st.info("위에서 정산할 프레임 이름을 선택하세요.")
    st.stop()

# ── 매출 필터링 ──────────────────────────────────────────────
df = df_all.copy()
if len(date_range) == 2:
    df = df[(df["날짜"] >= date_range[0]) & (df["날짜"] <= date_range[1])]

df_ip = df[df["프레임 이름"].isin(selected_frames)]

paid    = df_ip[~df_ip["취소 여부"] & (df_ip["최종 결제 금액"] > 0)]
coupons = df_ip[~df_ip["취소 여부"] & (df_ip["최종 결제 금액"] == 0) & (df_ip["쿠폰 할인 금액"] > 0)]

# 쿠폰 일부 할인 포함
all_coupon = pd.concat([coupons, paid[paid["쿠폰 할인 금액"] > 0]])

rs_agency = entry.get("rs_agency")
rs_mgmt   = entry.get("rs_mgmt")

total_sales_krw   = paid["KRW환산"].sum()
total_coupon_krw  = all_coupon["쿠폰KRW"].sum()
settlement_base   = total_sales_krw + total_coupon_krw
agency_settlement = settlement_base * rs_agency if rs_agency else 0
mgmt_settlement   = settlement_base * rs_mgmt   if rs_mgmt   else 0

# ── KPI 카드 ─────────────────────────────────────────────────
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("매출 합계 (KRW)", fmt_krw(total_sales_krw), f"{len(paid):,}건")
c2.metric("쿠폰 할인 (KRW)", fmt_krw(total_coupon_krw), f"{len(all_coupon):,}건")
c3.metric("정산 기준액", fmt_krw(settlement_base), "매출+쿠폰")
c4.metric(f"소속사 정산 ({fmt_pct(rs_agency)})", fmt_krw(agency_settlement))
c5.metric(f"대행사 정산 ({fmt_pct(rs_mgmt)})", fmt_krw(mgmt_settlement))

st.divider()

# ── 국가별 정산 테이블 ────────────────────────────────────────
st.markdown('<div class="section-title">🌏 국가별 정산 내역</div>', unsafe_allow_html=True)

if paid.empty and coupons.empty:
    st.warning("해당 기간에 해당 프레임의 매출이 없습니다.")
else:
    # 실매출 집계
    if not paid.empty:
        nat_paid = (
            paid.groupby(["국가", "결제 단위"])
            .agg(
                결제건수=("최종 결제 금액", "count"),
                원화매출=("최종 결제 금액", "sum"),
                KRW매출=("KRW환산", "sum"),
                쿠폰건수=("쿠폰 할인 금액", lambda x: (x > 0).sum()),
                원화쿠폰=("쿠폰 할인 금액", "sum"),
                쿠폰KRW=("쿠폰KRW", "sum"),
            )
            .reset_index()
        )
    else:
        nat_paid = pd.DataFrame(columns=["국가", "결제 단위", "결제건수", "원화매출", "KRW매출", "쿠폰건수", "원화쿠폰", "쿠폰KRW"])

    # 100% 쿠폰 집계 (최종금액=0)
    if not coupons.empty:
        nat_cpn = (
            coupons.groupby(["국가", "결제 단위"])
            .agg(
                결제건수=("쿠폰 할인 금액", lambda x: 0),
                원화매출=("최종 결제 금액", "sum"),
                KRW매출=("KRW환산", "sum"),
                쿠폰건수=("쿠폰 할인 금액", "count"),
                원화쿠폰=("쿠폰 할인 금액", "sum"),
                쿠폰KRW=("쿠폰KRW", "sum"),
            )
            .reset_index()
        )
        nat = pd.concat([nat_paid, nat_cpn], ignore_index=True)
        nat = nat.groupby(["국가", "결제 단위"], as_index=False).sum()
    else:
        nat = nat_paid

    nat = nat.sort_values("KRW매출", ascending=False).reset_index(drop=True)
    nat["정산기준KRW"] = nat["KRW매출"] + nat["쿠폰KRW"]
    nat["소속사정산"] = (nat["정산기준KRW"] * (rs_agency or 0)).round(0).astype(int)
    nat["대행사정산"] = (nat["정산기준KRW"] * (rs_mgmt   or 0)).round(0).astype(int)

    # 표시용 포맷
    def orig_fmt(r, col):
        sym = CURRENCY_SYMBOLS.get(r["결제 단위"], r["결제 단위"])
        return f"{sym}{int(r[col]):,}"

    nat["매출(원화)"]   = nat.apply(lambda r: orig_fmt(r, "원화매출"), axis=1)
    nat["매출(KRW)"]   = nat["KRW매출"].apply(fmt_krw)
    nat["쿠폰(원화)"]   = nat.apply(lambda r: orig_fmt(r, "원화쿠폰"), axis=1)
    nat["쿠폰(KRW)"]   = nat["쿠폰KRW"].apply(fmt_krw)
    nat["정산기준(KRW)"] = nat["정산기준KRW"].apply(fmt_krw)
    nat["소속사 정산액"]  = nat["소속사정산"].apply(fmt_krw)
    nat["대행사 정산액"]  = nat["대행사정산"].apply(fmt_krw)

    display_cols = [
        "국가", "결제 단위", "결제건수", "매출(원화)", "매출(KRW)",
        "쿠폰건수", "쿠폰(원화)", "쿠폰(KRW)",
        "정산기준(KRW)", "소속사 정산액", "대행사 정산액",
    ]
    st.dataframe(nat[display_cols], use_container_width=True, hide_index=True)

    # 합계 행
    total_row = {
        "국가": "합계", "결제 단위": "",
        "결제건수": nat["결제건수"].sum(),
        "매출(KRW)": fmt_krw(nat["KRW매출"].sum()),
        "쿠폰건수": nat["쿠폰건수"].sum(),
        "쿠폰(KRW)": fmt_krw(nat["쿠폰KRW"].sum()),
        "정산기준(KRW)": fmt_krw(nat["정산기준KRW"].sum()),
        "소속사 정산액": fmt_krw(nat["소속사정산"].sum()),
        "대행사 정산액": fmt_krw(nat["대행사정산"].sum()),
    }
    st.markdown(
        f"**합계** | 결제 {total_row['결제건수']:,}건 | "
        f"매출 {total_row['매출(KRW)']} | 쿠폰 {total_row['쿠폰(KRW)']} | "
        f"정산기준 **{total_row['정산기준(KRW)']}** | "
        f"소속사 **{total_row['소속사 정산액']}** | "
        f"대행사 **{total_row['대행사 정산액']}**"
    )

    # 국가별 KRW 막대 차트
    st.divider()
    st.markdown('<div class="section-title">국가별 정산 기준액 비교</div>', unsafe_allow_html=True)
    fig = px.bar(
        nat.sort_values("정산기준KRW"),
        x="정산기준KRW", y="국가", orientation="h",
        color="정산기준KRW", color_continuous_scale="Teal",
        custom_data=["소속사 정산액", "대행사 정산액"],
    )
    fig.update_traces(
        hovertemplate="%{y}<br>정산기준: %{x:,}원<br>소속사: %{customdata[0]}<br>대행사: %{customdata[1]}<extra></extra>"
    )
    fig.update_layout(
        height=max(200, len(nat) * 50),
        coloraxis_showscale=False,
        xaxis_tickformat=",", yaxis_title="",
        margin=dict(t=10, b=0),
    )
    st.plotly_chart(fig, use_container_width=True)

# ── 일별 추이 ────────────────────────────────────────────────
with st.expander("📅 일별 매출 추이"):
    daily = (
        paid.groupby("날짜")["KRW환산"].sum()
        .reset_index().rename(columns={"KRW환산": "매출"})
    )
    daily["날짜_str"] = daily["날짜"].astype(str)
    fig2 = px.bar(daily, x="날짜_str", y="매출", color_discrete_sequence=["#4361ee"])
    fig2.update_layout(yaxis_tickformat=",", height=260, margin=dict(t=10, b=0))
    fig2.update_traces(hovertemplate="%{x}<br>%{y:,}원<extra></extra>")
    st.plotly_chart(fig2, use_container_width=True)

# ── 원본 데이터 ──────────────────────────────────────────────
with st.expander("🗃 원본 데이터 보기"):
    show_cols = [
        "날짜", "국가", "매장 이름", "프레임 이름",
        "최종 결제 금액", "쿠폰 할인 금액", "결제 단위", "KRW환산", "쿠폰KRW",
        "결제 수단", "취소 여부",
    ]
    available = [c for c in show_cols if c in df_ip.columns]
    st.dataframe(
        df_ip[available].sort_values("날짜", ascending=False).reset_index(drop=True),
        use_container_width=True, height=350,
    )
    csv_out = df_ip[available].to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")
    st.download_button(
        f"CSV 다운로드 ({selected_ip})",
        csv_out,
        f"settlement_{selected_ip}.csv",
        "text/csv",
    )
