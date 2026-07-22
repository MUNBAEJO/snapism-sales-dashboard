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

# set_page_config 는 라우터(스내피즘.py)에서 처리
import os as _os
sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
from guide_content import render_guide
import data_io
st.markdown("""
<style>
html, body, [class*="css"], [data-testid="stAppViewContainer"] {
    font-family: 'Pretendard', -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Malgun Gothic', 'Apple SD Gothic Neo', sans-serif;
}
[data-testid="stAppViewContainer"] .main .block-container { padding-top: 2.2rem; padding-bottom: 3rem; max-width: 1500px; }
h1 { font-weight: 800 !important; letter-spacing: -0.5px; color: #1a1a2e; }
.section-title { font-size: 1.12rem; font-weight: 700; color: #1a1a2e; margin: 6px 0 12px; padding-left: 12px; border-left: 4px solid #f59e0b; line-height: 1.4; }
/* 경고 톤 KPI 카드 (종료일 초과 알림 페이지) */
[data-testid="stMetric"], [data-testid="metric-container"] {
    background: linear-gradient(135deg, #fffdf5 0%, #fff8e6 100%);
    border: 1px solid #ffe3a3; border-radius: 16px; padding: 16px 20px;
    box-shadow: 0 2px 10px rgba(245,158,11,0.08);
    transition: transform .15s ease, box-shadow .15s ease;
}
[data-testid="stMetric"]:hover, [data-testid="metric-container"]:hover { transform: translateY(-3px); box-shadow: 0 8px 20px rgba(245,158,11,0.16); }
[data-testid="stMetricLabel"] p { font-weight: 600; color: #92722a; font-size: .82rem; }
[data-testid="stMetricValue"] { font-weight: 800; color: #1a1a2e; letter-spacing: -0.5px; }
[data-testid="stMetricDelta"] { font-size: 0.82rem; }
hr { margin: 1.4rem 0 1.2rem; border: none; border-top: 1px solid #e9edf5; }
[data-testid="stDeployButton"] { display: none !important; }
[data-testid="stElementToolbar"] { display: none; }
[data-testid="stSidebar"] { background: #fbfcfe; border-right: 1px solid #eceff5; }
[data-testid="stDataFrame"] { border-radius: 12px; overflow: hidden; }
</style>
""", unsafe_allow_html=True)

BASE_DIR      = Path(__file__).parent.parent
SNAP_MASTER   = BASE_DIR / "data" / "master.csv"
PHOTO_MASTER  = BASE_DIR / "data" / "master_photoism.csv"          # 1,970 MB (최종 fallback)
PHOTO_PARQUET = BASE_DIR / "data" / "master_photoism.parquet"      # 116 MB (중간 fallback)
PHOTO_AGG     = BASE_DIR / "data" / "master_photoism_agg.parquet"  # 7.3 MB (우선 사용)
_DATE_RE      = re.compile(r"^\s*\d{6,8}\s*")


# ── 데이터 로더 ────────────────────────────────────────────────
@st.cache_data(ttl=300, max_entries=1)
def load_jira():
    return fetch_ip_dates(brand="all", force_refresh=False)


@st.cache_data(ttl=900, max_entries=1)
def _load_snap(_v):
    if not SNAP_MASTER.exists():
        return pd.DataFrame()
    df = data_io.read_master(SNAP_MASTER)  # parquet 우선(없으면 csv)
    df["날짜"] = pd.to_datetime(df["날짜"], errors="coerce").dt.date
    df["취소 여부"] = df["취소 여부"].astype(str).str.lower().isin(["true","1","yes"])
    for col in ["KRW환산금액", "쿠폰KRW"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)
        else:
            df[col] = 0
    df["정산금액"]   = df["KRW환산금액"] + df["쿠폰KRW"]
    df["브랜드소스"] = "스내피즘"
    df["건수"]       = 1   # 개별 거래 행 → 1건
    df["타이틀명_비교"] = df["프레임 이름"].fillna("").astype(str) if "프레임 이름" in df.columns else ""
    # ★쓰는 컬럼만 남겨 반환한다. @st.cache_data 는 반환값을 '피클로 직렬화'해 저장하는데,
    #   39만행 × 36컬럼을 통째로 넘기면 그 직렬화에만 2.7초가 든다(프로파일 실측).
    #   이 페이지가 실제로 쓰는 건 아래 8개뿐이다.
    # 상세 표(show_cols)가 국가·매장 이름·결제 수단까지 쓰므로 반드시 포함한다.
    # 빠뜨리면 avail 필터에 걸려 화면에서 조용히 사라진다.
    keep = ["날짜", "타이틀명_비교", "정산금액", "건수", "브랜드소스",
            "국가", "매장 이름", "결제 수단"]
    out = df[~df["취소 여부"]]
    return out[[c for c in keep if c in out.columns]]


def load_snap():
    return _load_snap(data_io.file_version(SNAP_MASTER))


@st.cache_data(ttl=60, max_entries=1)
def load_photo():
    """포토이즘 매출. 집계 parquet(7.3 MB, 일·타이틀 단위) 우선 → 전체 parquet → CSV fallback.
    기간후 분석은 '타이틀별·날짜별 매출 합계'면 충분하므로 집계본 사용 (1.2초/219MB).
    집계 행은 다수 거래의 묶음이므로 '건수' 컬럼으로 실거래 수를 보존한다."""
    import pyarrow.parquet as pq
    need = ["날짜", "취소 여부", "결제 단위", "최종 결제 금액", "쿠폰 할인 금액",
            "타이틀명", "국가", "매장 이름", "건수", "결제 수단"]
    df = pd.DataFrame()
    for src in (PHOTO_AGG, PHOTO_PARQUET):
        if src.exists():
            try:
                avail = pq.read_schema(str(src)).names
                cols  = [c for c in need if c in avail]
                df = pq.read_table(str(src), columns=cols).to_pandas(strings_to_categorical=True)
                break
            except Exception:
                df = pd.DataFrame()
    if df.empty:
        # 최후 폴백인 2GB master_photoism.csv 직접 로드는 20~60초 멈춤을 유발하므로
        # 읽지 않는다. (parquet 집계/전체가 모두 실패한 비정상 상황 → 빈 DF 반환,
        #  집계 parquet 을 재생성하면 정상 복구됨)
        return pd.DataFrame()

    # 날짜/취소 정리 후 취소 건 먼저 제거 (이후 연산 메모리 절감)
    df["날짜"] = pd.to_datetime(df["날짜"].astype("object"), errors="coerce").dt.date
    df["취소 여부"] = df["취소 여부"].astype(str).str.lower().isin(["true", "1", "yes"])
    df = df[~df["취소 여부"]].copy()

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
    df["결제 단위"] = df["결제 단위"].astype("object").fillna("KRW").astype(str)
    rate = df["결제 단위"].map(ex).fillna(1)
    fin  = pd.to_numeric(df.get("최종 결제 금액", 0), errors="coerce").fillna(0)
    cou  = pd.to_numeric(df.get("쿠폰 할인 금액", 0), errors="coerce").fillna(0)
    df["KRW환산금액"] = (fin * rate).round(0).astype("int64")
    df["쿠폰KRW"]    = (cou * rate).round(0).astype("int64")
    df["정산금액"]   = df["KRW환산금액"] + df["쿠폰KRW"]
    df["브랜드소스"] = "포토이즘"
    # 건수: 집계본엔 존재, 전체/CSV fallback이면 1건씩
    if "건수" in df.columns:
        df["건수"] = pd.to_numeric(df["건수"], errors="coerce").fillna(1).astype("int64")
    else:
        df["건수"] = 1

    # 타이틀명_비교: categorical 유지 (메모리 절감), fillna 안전 처리
    if "타이틀명" in df.columns:
        s = df["타이틀명"]
        if str(s.dtype) == "category":
            if "" not in s.cat.categories:
                s = s.cat.add_categories([""])
            df["타이틀명_비교"] = s.fillna("")
        else:
            df["타이틀명_비교"] = s.fillna("").astype(str)
    else:
        df["타이틀명_비교"] = ""
    # ★스내피즘 쪽과 같은 이유 — 캐시 직렬화 비용을 줄이려고 쓰는 컬럼만 반환.
    # 상세 표(show_cols)가 국가·매장 이름·결제 수단까지 쓰므로 반드시 포함한다.
    # 빠뜨리면 avail 필터에 걸려 화면에서 조용히 사라진다.
    keep = ["날짜", "타이틀명_비교", "정산금액", "건수", "브랜드소스",
            "국가", "매장 이름", "결제 수단"]
    return df[[c for c in keep if c in df.columns]]


# ── 헤더 ──────────────────────────────────────────────────────
st.title("⚠️ 종료일 이후 매출 분석")
render_guide("expired")
st.caption("Jira에 등록된 타이틀 종료일이 지난 뒤에도 매출이 발생한 건을 찾아요.")

# ── 사이드바 ──────────────────────────────────────────────────
st.sidebar.header("🔍 필터")
brand_filter  = st.sidebar.radio("분석 대상", ["전체", "포토이즘만", "스내피즘만"])
min_amount    = st.sidebar.number_input("최소 기한 초과 금액 (KRW)", value=0, step=10000)
exclude_far   = st.sidebar.checkbox("무기한 타이틀 제외", value=True,
                                    help="종료일이 2099년 이후인 타이틀은 분석에서 빼요.")

with st.sidebar:
    if st.button("🔄 Jira 새로고침"):
        fetch_ip_dates(brand="all", force_refresh=True)
        load_jira.clear()   # Jira 캐시만 국소 무효화(전역 clear 로 타 사용자 매출 캐시까지 날리지 않게)
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
    st.warning("불러올 매출 데이터가 없어요. 사이드바에서 분석 대상을 바꾸거나 데이터를 업로드해 주세요.")
    st.stop()

all_sales = pd.concat(frames, ignore_index=True)

# ── 기한 초과 분석 (벡터화) ────────────────────────────────────
unique_titles = [t for t in all_sales["타이틀명_비교"].dropna().unique()
                 if t and t not in ("nan", "")]

# Jira 종료일이 유효한 타이틀만 매핑 (무기한/미설정 제외)
due_map = {}
for title in unique_titles:
    entry = jira_map.get(title)
    if not entry:
        continue
    dd = entry.get("duedate")
    if not dd:
        continue
    if exclude_far and dd >= "2099-01-01":
        continue
    due_map[title] = dd

rows = []
with st.spinner("기한 초과 분석 중..."):
    if due_map:
        due_date_map = {t: date.fromisoformat(d) for t, d in due_map.items()}
        # 종료일 있는 타이틀 행만 추출 → 행별 종료일 비교 (벡터화)
        sub = all_sales[all_sales["타이틀명_비교"].isin(due_map)].copy()
        sub["타이틀명_비교"] = sub["타이틀명_비교"].astype("object")
        due_d = sub["타이틀명_비교"].map(due_date_map)
        after = sub[sub["날짜"] > due_d]

        if not after.empty:
            grp = after.groupby("타이틀명_비교", observed=True).agg(
                기한후_금액=("정산금액", "sum"),
                기한후_건수=("건수",     "sum"),
                최초=("날짜", "min"),
                최근=("날짜", "max"),
                소스=("브랜드소스", "first"),
            )
            for title, g in grp.iterrows():
                amt = int(g["기한후_금액"])
                if amt < min_amount:
                    continue
                entry = jira_map.get(title, {})
                rows.append({
                    "타이틀명":      title,
                    "종료일":        due_map[title],
                    "브랜드":        entry.get("brand", ""),
                    "소스":          g["소스"],
                    "기한후 건수":   int(g["기한후_건수"]),
                    "기한후 금액":   amt,
                    "최초 초과일":   str(g["최초"]),
                    "최근 초과일":   str(g["최근"]),
                    "Jira 티켓":    entry.get("ticket_key", ""),
                    "Jira 상태":    entry.get("status", ""),
                })

st.divider()

if not rows:
    st.success("✅ 기한을 넘긴 매출이 없어요.")
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
with st.container(border=True):
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
with st.container(border=True):
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
with st.container(border=True):
    st.markdown('<div class="section-title">🔍 상세 거래 내역</div>', unsafe_allow_html=True)
    st.caption("포토이즘은 일·타이틀·매장 단위로 묶어서, 스내피즘은 거래 건별로 보여줘요. (건수 = 실거래 수)")

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
        _cnt = int(detail["건수"].sum()) if "건수" in detail.columns else len(detail)
        st.info(
            f"**{sel}**  종료일: `{e.get('종료일')}` | "
            f"{_cnt:,}건 · ₩{detail['정산금액'].sum():,}  "
            f"([Jira ↗](https://seobukcorp.atlassian.net/browse/{e.get('Jira 티켓', '')}))"
        )

    if not detail.empty:
        show_cols = ["날짜", "브랜드소스", "국가", "매장 이름", "타이틀명_비교",
                     "건수", "정산금액", "결제 수단"]
        avail = [c for c in show_cols if c in detail.columns]
        st.dataframe(
            detail[avail].sort_values("날짜", ascending=False).reset_index(drop=True),
            use_container_width=True, height=400,
        )
        csv = detail[avail].to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")
        st.download_button("CSV 다운로드", csv, "기한초과매출.csv", "text/csv")
    else:
        st.info("이 타이틀에는 기한 초과 거래가 없어요.")
