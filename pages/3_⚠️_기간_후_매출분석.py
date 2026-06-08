"""
종료일 이후 매출 분석
- Jira WBS 타이틀명 ↔ 매출 타이틀명 직접 비교
- 포토이즘: 매출 타이틀명 = WBS 타이틀명 (정확 매칭)
- 스내피즘: 매출 프레임명 = WBS 타이틀명 (정확 매칭 → 부분 매칭)
"""
import streamlit as st
import pandas as pd
import plotly.express as px
from pathlib import Path
from datetime import date
import json, sys, re

sys.path.insert(0, str(Path(__file__).parent.parent))
from jira_ip_dates import fetch_ip_dates

st.set_page_config(
    page_title="기간 후 매출 분석",
    page_icon="⚠️",
    layout="wide",
    initial_sidebar_state="expanded",
)
st.markdown("""
<style>
[data-testid="metric-container"] {
    background:#fff3cd; border:1px solid #ffc107;
    border-radius:10px; padding:12px 20px;
}
.section-title { font-size:1.05rem; font-weight:600; margin-bottom:4px; }
[data-testid="stDeployButton"] { display:none !important; }
[data-testid="stSidebarNav"] ul li:first-child a::before { content: "📊 "; }
</style>
""", unsafe_allow_html=True)

BASE_DIR     = Path(__file__).parent.parent
SNAP_MASTER  = BASE_DIR / "data" / "master.csv"
PHOTO_MASTER = BASE_DIR / "data" / "master_photoism.csv"
_DATE_RE     = re.compile(r"^\s*\d{6,8}\s*")


# ── 데이터 로더 ────────────────────────────────────────────────
@st.cache_data(ttl=300)
def load_jira():
    return fetch_ip_dates(brand="all", force_refresh=False)


@st.cache_data(ttl=60)
def load_snap():
    if not SNAP_MASTER.exists():
        return pd.DataFrame()
    df = pd.read_csv(SNAP_MASTER, encoding="utf-8-sig", low_memory=False)
    df["날짜"] = pd.to_datetime(df["날짜"], errors="coerce").dt.date
    df["취소 여부"] = df["취소 여부"].astype(str).str.lower().isin(["true","1","yes"])
    for col in ["KRW환산금액", "쿠폰KRW"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)
        else:
            df[col] = 0
    df["정산금액"]   = df["KRW환산금액"] + df["쿠폰KRW"]
    df["브랜드소스"] = "스내피즘"
    df["타이틀명_비교"] = df["프레임 이름"].fillna("").astype(str) if "프레임 이름" in df.columns else ""
    return df[~df["취소 여부"]]


@st.cache_data(ttl=60)
def load_photo():
    if not PHOTO_MASTER.exists():
        return pd.DataFrame()
    df = pd.read_csv(PHOTO_MASTER, encoding="utf-8-sig", low_memory=False)
    df["날짜"] = pd.to_datetime(df["날짜"], errors="coerce").dt.date
    df["취소 여부"] = df["취소 여부"].astype(str).str.lower().isin(["true","1","yes"])

    try:
        with open(BASE_DIR / "config.json", encoding="utf-8") as f:
            ex = json.load(f).get("exchange_rates", {"KRW": 1})
    except (FileNotFoundError, KeyError, json.JSONDecodeError):
        ex = {"KRW": 1}
    defaults = {
        "PHP": 24.0,  "VND": 0.054, "CAD": 1050.0,"USD": 1380.0,
        "AED": 375.0, "CLP": 1.5,   "EUR": 1500.0, "AUD": 890.0,
        "SGD": 1020.0,"GBP": 1750.0,"PEN": 370.0,  "LAK": 0.065,
        "MXN": 70.0,  "BND": 1020.0,"MNT": 0.40,   "MOP": 171.0,
    }
    for k, v in defaults.items():
        ex.setdefault(k, v)

    if "결제 단위" not in df.columns:
        df["결제 단위"] = "KRW"
    df["결제 단위"] = df["결제 단위"].fillna("KRW").astype(str)
    df["환율"] = df["결제 단위"].map(ex).fillna(1)
    for col in ["최종 결제 금액", "쿠폰 할인 금액"]:
        if col not in df.columns:
            df[col] = 0
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)
    df["KRW환산금액"] = (df["최종 결제 금액"] * df["환율"]).round(0).astype(int)
    df["쿠폰KRW"]    = (df["쿠폰 할인 금액"] * df["환율"]).round(0).astype(int)
    df["정산금액"]   = df["KRW환산금액"] + df["쿠폰KRW"]
    df["브랜드소스"] = "포토이즘"
    df["타이틀명_비교"] = df["타이틀명"].fillna("").astype(str) if "타이틀명" in df.columns else ""
    return df[~df["취소 여부"]]


# ── 헤더 ──────────────────────────────────────────────────────
st.title("⚠️ 종료일 이후 매출 분석")
st.caption("Jira WBS 타이틀명의 종료일(duedate) 이후 실제 매출 발생 건을 탐지합니다.")

# ── 사이드바 ──────────────────────────────────────────────────
st.sidebar.header("🔍 필터")
brand_filter  = st.sidebar.radio("분석 대상", ["전체", "포토이즘만", "스내피즘만"])
min_amount    = st.sidebar.number_input("최소 기한후 금액 (KRW)", value=0, step=10000)
exclude_far   = st.sidebar.checkbox("기한 2099년 이상 제외 (무기한)", value=True)

with st.sidebar:
    if st.button("🔄 Jira 새로고침"):
        fetch_ip_dates(brand="all", force_refresh=True)
        st.cache_data.clear()
        st.rerun()

# ── 로드 ─────────────────────────────────────────────────────
with st.spinner("Jira 데이터 로딩..."):
    jira_map = load_jira()

snap_df  = load_snap()
photo_df = load_photo()

if brand_filter == "포토이즘만":
    frames = [photo_df] if not photo_df.empty else []
elif brand_filter == "스내피즘만":
    frames = [snap_df]  if not snap_df.empty  else []
else:
    frames = [df for df in [snap_df, photo_df] if not df.empty]

if not frames:
    st.warning("매출 데이터가 없습니다.")
    st.stop()

all_sales = pd.concat(frames, ignore_index=True)

# ── 기한 초과 분석 ────────────────────────────────────────────
rows = []
unique_titles = all_sales["타이틀명_비교"].dropna().unique()
unique_titles = [t for t in unique_titles if t and t not in ("nan", "")]

prog = st.progress(0, text="분석 중...")
for i, title in enumerate(unique_titles):
    prog.progress((i + 1) / len(unique_titles), text=f"분석 중... ({i+1}/{len(unique_titles)})")

    entry = jira_map.get(title)
    if entry is None:
        continue

    duedate = entry.get("duedate")
    if not duedate:
        continue
    if exclude_far and duedate >= "2099-01-01":
        continue

    due = date.fromisoformat(duedate)
    t_sales = all_sales[all_sales["타이틀명_비교"] == title]
    after   = t_sales[t_sales["날짜"] > due]
    if after.empty:
        continue

    amt = int(after["정산금액"].sum())
    if amt < min_amount:
        continue

    rows.append({
        "타이틀명":      title,
        "종료일":        duedate,
        "브랜드":        entry.get("brand", ""),
        "소스":          after["브랜드소스"].iloc[0],
        "기한후 건수":   len(after),
        "기한후 금액":   amt,
        "최초 초과일":   str(after["날짜"].min()),
        "최근 초과일":   str(after["날짜"].max()),
        "Jira 티켓":    entry.get("ticket_key", ""),
        "Jira 상태":    entry.get("status", ""),
    })

prog.empty()

st.divider()

if not rows:
    st.success("✅ 기한 초과 매출이 없습니다!")
    st.stop()

result_df = pd.DataFrame(rows).sort_values("기한후 금액", ascending=False).reset_index(drop=True)

# ── KPI ──────────────────────────────────────────────────────
c1, c2, c3, c4 = st.columns(4)
c1.metric("⚠️ 초과 타이틀 수",  f"{len(result_df):,}개")
c2.metric("⚠️ 초과 거래 건수",  f"{result_df['기한후 건수'].sum():,}건")
c3.metric("⚠️ 초과 총 금액",    f"₩{result_df['기한후 금액'].sum():,}")
c4.metric("📊 분석된 타이틀",    f"{len(unique_titles):,}개")

st.divider()

# ── 소스별 비교 차트 ──────────────────────────────────────────
col_l, col_r = st.columns(2)
with col_l:
    st.markdown('<div class="section-title">소스별 기한 초과 금액</div>', unsafe_allow_html=True)
    src_grp = result_df.groupby("소스")["기한후 금액"].sum().reset_index()
    fig1 = px.bar(src_grp, x="소스", y="기한후 금액",
                  color="소스", color_discrete_map={"스내피즘": "#4361ee", "포토이즘": "#7209b7"},
                  text_auto=True)
    fig1.update_traces(texttemplate="₩%{y:,}", textposition="outside")
    fig1.update_layout(height=280, yaxis_tickformat=",", showlegend=False,
                       margin=dict(t=20, b=0))
    st.plotly_chart(fig1, use_container_width=True)

with col_r:
    st.markdown('<div class="section-title">기한 초과 금액 TOP 10</div>', unsafe_allow_html=True)
    top10 = result_df.head(10).sort_values("기한후 금액")
    fig2  = px.bar(top10, x="기한후 금액", y="타이틀명", orientation="h",
                   color="소스", color_discrete_map={"스내피즘": "#4361ee", "포토이즘": "#7209b7"},
                   custom_data=["종료일", "기한후 건수"])
    fig2.update_traces(hovertemplate="%{y}<br>₩%{x:,}  (%{customdata[1]}건)<br>종료일: %{customdata[0]}<extra></extra>")
    fig2.update_layout(height=280, xaxis_tickformat=",",
                       yaxis_title="", margin=dict(t=20, b=0))
    st.plotly_chart(fig2, use_container_width=True)

# ── 결과 테이블 ───────────────────────────────────────────────
st.markdown('<div class="section-title">기한 초과 타이틀 목록</div>', unsafe_allow_html=True)

disp = result_df.copy()
disp["기한후 금액"] = disp["기한후 금액"].apply(lambda x: f"₩{x:,}")
disp["Jira 링크"]  = disp["Jira 티켓"].apply(
    lambda t: f"https://seobukcorp.atlassian.net/browse/{t}" if t else ""
)

st.dataframe(
    disp[["타이틀명", "종료일", "소스", "브랜드", "기한후 건수",
          "기한후 금액", "최초 초과일", "최근 초과일", "Jira 상태", "Jira 티켓"]],
    use_container_width=True, hide_index=True, height=450,
)

# ── 상세 거래 내역 ─────────────────────────────────────────────
st.divider()
st.markdown('<div class="section-title">🔍 상세 거래 내역</div>', unsafe_allow_html=True)

sel = st.selectbox(
    "타이틀 선택",
    ["전체"] + result_df["타이틀명"].tolist(),
    key="detail_sel",
)

entry_map = {r["타이틀명"]: r for r in rows}

if sel == "전체":
    detail = all_sales[all_sales["타이틀명_비교"].isin(result_df["타이틀명"])]
    # 기한 초과 건만
    detail_rows = []
    for title, entry in entry_map.items():
        due = date.fromisoformat(entry["종료일"])
        part = all_sales[(all_sales["타이틀명_비교"] == title) & (all_sales["날짜"] > due)]
        detail_rows.append(part)
    detail = pd.concat(detail_rows, ignore_index=True) if detail_rows else pd.DataFrame()
else:
    e   = entry_map.get(sel, {})
    due = date.fromisoformat(e["종료일"]) if e.get("종료일") else None
    t_s = all_sales[all_sales["타이틀명_비교"] == sel]
    detail = t_s[t_s["날짜"] > due] if due else t_s
    st.info(
        f"**{sel}**  종료일: `{e.get('종료일')}` | "
        f"{len(detail)}건 · ₩{detail['정산금액'].sum():,}  "
        f"([Jira ↗](https://seobukcorp.atlassian.net/browse/{e.get('Jira 티켓', '')}))"
    )

if not detail.empty:
    show_cols = ["날짜", "브랜드소스", "국가", "매장 이름", "타이틀명_비교", "정산금액", "결제 수단"]
    avail = [c for c in show_cols if c in detail.columns]
    st.dataframe(
        detail[avail].sort_values("날짜", ascending=False).reset_index(drop=True),
        use_container_width=True, height=400,
    )
    csv = detail[avail].to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")
    st.download_button("CSV 다운로드", csv, "기한초과매출.csv", "text/csv")
else:
    st.info("상세 데이터 없음")
