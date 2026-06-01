"""
포토이즘 KPI 목표 달성률 대시보드

IPX MASTER DATA.xlsx 업로드 시:
  - REPORT 시트  → 월별 목표  (data/kpi_targets.csv)
  - A팀 + C팀 시트 → 월별 실적 (data/kpi_actuals.csv)
"""
import json
import calendar
import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from pathlib import Path
from datetime import date

st.set_page_config(
    page_title="KPI 목표 달성률",
    page_icon="🎯",
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
.section-title { font-size: 1.05rem; font-weight: 600; margin-bottom: 4px; }
[data-testid="stDeployButton"] { display: none !important; }
</style>
""", unsafe_allow_html=True)

BASE_DIR      = Path(__file__).parent.parent
MASTER_FILE   = BASE_DIR / "data" / "master_photoism.csv"
KPI_FILE      = BASE_DIR / "data" / "kpi_targets.csv"
ACTUALS_FILE  = BASE_DIR / "data" / "kpi_actuals.csv"
CONFIG_FILE   = BASE_DIR / "config.json"

_COUPON_CC = {"la", "gb", "de", "th", "lv", "mx"}
_COIN_CC   = {"cl", "la", "pe", "gb", "de", "lv", "mx"}


# ── 데이터 로드 함수들 ────────────────────────────────────────

def load_exchange_rates():
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            return json.load(f).get("exchange_rates", {"KRW": 1})
    except Exception:
        return {"KRW": 1}


def _calc_revenue_from_cms(df: pd.DataFrame) -> pd.DataFrame:
    """master_photoism.csv DataFrame → 월별 매출 합산 (공통 유틸)"""
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
    """
    블렌딩 전략:
      - CMS(master_photoism.csv)를 전 기간 베이스로 사용 (seg 기준 국내/해외 필터 포함)
      - kpi_actuals.csv 에 값이 있는 달은 엑셀 확정값으로 덮어쓰기
      → 매월 엑셀 재업로드 불필요. 엑셀은 공식 정산값 교정 시에만 올리면 됨
    반환: (DataFrame[연도,월,실제매출], source_label)
    """
    today     = date.today()
    cur_month = today.month

    # ── ① CMS 전 기간 베이스 (seg 기준 국내/해외 필터) ──────────
    cms_monthly = pd.DataFrame()
    if MASTER_FILE.exists():
        df = pd.read_csv(MASTER_FILE, encoding="utf-8-sig", low_memory=False)
        df["날짜"]      = pd.to_datetime(df["날짜"], errors="coerce").dt.date
        df              = df[df["날짜"].notna()]
        df["취소 여부"] = df["취소 여부"].astype(str).str.lower().isin(["true", "1", "yes"])
        df              = df[~df["취소 여부"]]
        df["연도"]      = pd.to_datetime(df["날짜"]).dt.year
        df["월"]        = pd.to_datetime(df["날짜"]).dt.month
        # 국내/해외 분리
        _cc = df["국가코드"].astype(str).str.lower().fillna("")
        if seg == "국내":
            df = df[_cc == "kr"]
        elif seg == "해외":
            df = df[_cc != "kr"]
        cms_monthly = _calc_revenue_from_cms(df)

    # ── ② IPX 엑셀 확정값 (해당 seg + 값 있는 달만) ────────────
    excel_monthly = pd.DataFrame()
    if ACTUALS_FILE.exists():
        ex = pd.read_csv(ACTUALS_FILE, encoding="utf-8-sig")
        ex["연도"]    = ex["연도"].astype(int)
        ex["월"]      = ex["월"].astype(int)
        ex["실제매출"] = pd.to_numeric(ex["실제매출"], errors="coerce").fillna(0).astype(int)
        if "구분" in ex.columns:
            ex = ex[ex["구분"] == seg]
        excel_monthly = ex[ex["실제매출"] > 0][["연도", "월", "실제매출"]]

    # ── ③ 머지: 엑셀 있는 달 → 엑셀 우선, 나머지 → CMS ─────────
    if cms_monthly.empty and excel_monthly.empty:
        return pd.DataFrame(), "없음"

    if excel_monthly.empty:
        return cms_monthly.sort_values(["연도","월"]).reset_index(drop=True), "📸 포토이즘 CMS"

    if cms_monthly.empty:
        return excel_monthly.sort_values(["연도","월"]).reset_index(drop=True), "📊 IPX 엑셀"

    merged = pd.merge(
        cms_monthly, excel_monthly,
        on=["연도", "월"], how="outer", suffixes=("_cms", "_excel")
    )
    merged["실제매출"] = merged["실제매출_excel"].where(
        merged["실제매출_excel"].notna() & (merged["실제매출_excel"] > 0),
        merged["실제매출_cms"]
    ).fillna(0).astype(int)
    merged = (
        merged[["연도", "월", "실제매출"]]
        .sort_values(["연도", "월"])
        .reset_index(drop=True)
    )

    excel_max_m = int(excel_monthly["월"].max())
    label = f"📊 IPX 엑셀 (1~{excel_max_m}월)  +  📸 CMS 실시간 ({cur_month}월~)"
    return merged, label


TEAM_LIST = ["A팀", "C팀", "오리지널"]

@st.cache_data(ttl=30)
def load_team_kpi(year: int) -> pd.DataFrame:
    """팀별(A팀/C팀/오리지널) 월별 목표·실적 로드 (엑셀 기반 전용)"""
    if not KPI_FILE.exists() or not ACTUALS_FILE.exists():
        return pd.DataFrame()

    tgt = pd.read_csv(KPI_FILE, encoding="utf-8-sig")
    act = pd.read_csv(ACTUALS_FILE, encoding="utf-8-sig")

    if "구분" not in tgt.columns or "구분" not in act.columns:
        return pd.DataFrame()

    for df in [tgt, act]:
        df["연도"]    = df["연도"].astype(int)
        df["월"]      = df["월"].astype(int)

    tgt["매출목표"] = pd.to_numeric(tgt["매출목표"], errors="coerce").fillna(0).astype(int)
    act["실제매출"] = pd.to_numeric(act["실제매출"], errors="coerce").fillna(0).astype(int)

    frames = []
    for team in TEAM_LIST:
        t = tgt[(tgt["구분"] == team) & (tgt["연도"] == year)][["월", "매출목표"]]
        a = act[(act["구분"] == team) & (act["연도"] == year)][["월", "실제매출"]]
        m = pd.merge(t, a, on="월", how="outer").fillna(0)
        m["팀"]    = team
        m["연도"]  = year
        m["달성률"] = m.apply(
            lambda r: round(r["실제매출"] / r["매출목표"] * 100, 1)
            if r["매출목표"] > 0 else None, axis=1
        )
        frames.append(m)

    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True).sort_values(["팀", "월"])


@st.cache_data(ttl=30)
def load_targets(seg: str = "TTL"):
    if not KPI_FILE.exists():
        return pd.DataFrame(columns=["연도", "월", "매출목표"])
    df = pd.read_csv(KPI_FILE, encoding="utf-8-sig")
    df["연도"]    = df["연도"].astype(int)
    df["월"]      = df["월"].astype(int)
    df["매출목표"] = pd.to_numeric(df["매출목표"], errors="coerce").fillna(0).astype(int)
    # 구분 컬럼 있으면 필터, 없으면 구버전(TTL 단일) 그대로 사용
    if "구분" in df.columns:
        df = df[df["구분"] == seg]
    return df[["연도", "월", "매출목표"]]


def fmt_krw(n: float) -> str:
    return f"₩{int(n):,}"


# ── 엑셀 파싱 함수들 ──────────────────────────────────────────

def parse_report_targets(file, segment: str, year: int) -> pd.DataFrame:
    """REPORT 시트 → 월별 목표 DataFrame"""
    raw = pd.read_excel(file, sheet_name="REPORT", header=None, engine="openpyxl")

    # 헤더에서 월 컬럼 위치 탐색
    month_cols = []
    for _, row in raw.iterrows():
        vals = [str(v).strip() for v in row]
        if "1월" in vals and "12월" in vals:
            for m in range(1, 13):
                for ci, v in enumerate(vals):
                    if v == f"{m}월":
                        month_cols.append(ci)
                        break
            break
    if not month_cols:
        month_cols = list(range(3, 15))

    # segment 목표 행 탐색
    target_row = None
    for _, row in raw.iterrows():
        if str(row.iloc[1]).strip() == segment and str(row.iloc[2]).strip() == "목표":
            target_row = row
            break
    if target_row is None:
        raise ValueError(f"'{segment} 목표' 행을 REPORT 시트에서 찾지 못했습니다.")

    vals = []
    for ci in month_cols[:12]:
        v = target_row.iloc[ci] if ci < len(target_row) else 0
        try:
            vals.append(int(float(v)) if pd.notna(v) else 0)
        except (ValueError, TypeError):
            vals.append(0)
    while len(vals) < 12:
        vals.append(0)

    return pd.DataFrame({"연도": [year]*12, "월": list(range(1,13)), "매출목표": vals[:12]})


def parse_report_actuals(file, segment: str, year: int) -> pd.DataFrame:
    """REPORT 시트 → 월별 실적 DataFrame
    목표 행(segment + '목표')을 찾은 뒤 바로 아래 '실적' 행 값을 읽음
    """
    raw = pd.read_excel(file, sheet_name="REPORT", header=None, engine="openpyxl")

    # 헤더에서 월 컬럼 위치 탐색
    month_cols = []
    for _, row in raw.iterrows():
        vals = [str(v).strip() for v in row]
        if "1월" in vals and "12월" in vals:
            for m in range(1, 13):
                for ci, v in enumerate(vals):
                    if v == f"{m}월":
                        month_cols.append(ci)
                        break
            break
    if not month_cols:
        month_cols = list(range(3, 15))

    # segment 목표 행 인덱스 탐색
    target_idx = None
    for idx, row in raw.iterrows():
        if str(row.iloc[1]).strip() == segment and str(row.iloc[2]).strip() == "목표":
            target_idx = idx
            break
    if target_idx is None:
        raise ValueError(f"'{segment} 목표' 행을 REPORT 시트에서 찾지 못했습니다.")

    # 목표 행 이후 실적 행 탐색 (최대 5행 이내)
    actual_row = None
    for idx in range(target_idx + 1, target_idx + 6):
        if idx >= len(raw):
            break
        r = raw.iloc[idx]
        if str(r.iloc[2]).strip() == "실적":
            actual_row = r
            break
    if actual_row is None:
        raise ValueError(f"'{segment} 실적' 행을 REPORT 시트에서 찾지 못했습니다.")

    vals = []
    for ci in month_cols[:12]:
        v = actual_row.iloc[ci] if ci < len(actual_row) else 0
        try:
            fv = float(v) if pd.notna(v) and str(v).strip() not in ("", "nan") else 0.0
            vals.append(int(fv) if fv > 0 else 0)
        except (ValueError, TypeError):
            vals.append(0)
    while len(vals) < 12:
        vals.append(0)

    return pd.DataFrame({"연도": [year]*12, "월": list(range(1, 13)), "실제매출": vals[:12]})


# ── 사이드바 ──────────────────────────────────────────────────
st.sidebar.header("📂 파일 관리")

col_yr, col_seg = st.sidebar.columns([1, 2])
target_year = col_yr.number_input("연도", min_value=2024, max_value=2030, value=2026, step=1)
seg_choice  = col_seg.radio("목표 기준", ["TTL (국내+해외)", "국내", "해외"], index=0)
SEG_MAP = {"TTL (국내+해외)": "TTL", "국내": "국내", "해외": "해외"}

st.sidebar.caption(
    "**IPX MASTER DATA.xlsx** 업로드 시 REPORT 시트 → 목표 + 실적 자동 파싱"
)
uploaded = st.sidebar.file_uploader("파일 업로드 (.xlsx / .csv)", type=["xlsx", "csv"])

if uploaded:
    ext = Path(uploaded.name).suffix.lower()
    yr  = int(target_year)
    try:
        if ext == ".xlsx":
            # ① 전체 구분 파싱: 전체(TTL/국내/해외) + 팀별(A팀/C팀/오리지널)
            ALL_SEGS = ["TTL", "국내", "해외", "A팀", "C팀", "오리지널"]
            tgt_frames, act_frames = [], []
            for seg_key in ALL_SEGS:
                try:
                    t = parse_report_targets(uploaded, seg_key, yr)
                    t["구분"] = seg_key
                    tgt_frames.append(t)
                except Exception:
                    pass   # 해당 구분이 없는 경우 조용히 skip
                try:
                    a = parse_report_actuals(uploaded, seg_key, yr)
                    a["구분"] = seg_key
                    act_frames.append(a)
                except Exception:
                    pass

            if tgt_frames:
                pd.concat(tgt_frames, ignore_index=True).to_csv(
                    KPI_FILE, index=False, encoding="utf-8-sig"
                )
            if act_frames:
                pd.concat(act_frames, ignore_index=True).to_csv(
                    ACTUALS_FILE, index=False, encoding="utf-8-sig"
                )

            parsed = [f["구분"].iloc[0] for f in tgt_frames]
            st.sidebar.success(f"✅ 파싱 완료: {', '.join(parsed)}")

            # 현재 선택 segment 미리보기
            cur_seg = SEG_MAP[seg_choice]
            tgt_cur = next((t for t in tgt_frames if t["구분"].iloc[0] == cur_seg), pd.DataFrame())
            act_cur = next((a for a in act_frames if a["구분"].iloc[0] == cur_seg), pd.DataFrame())
            if not tgt_cur.empty and not act_cur.empty:
                preview = pd.merge(
                    tgt_cur.rename(columns={"매출목표": "목표"}),
                    act_cur.rename(columns={"실제매출": "실적"}),
                    on=["연도", "월"],
                )
                preview["달성률"] = preview.apply(
                    lambda r: f"{r['실적']/r['목표']*100:.1f}%" if r["목표"] > 0 else "—", axis=1
                )
                preview["목표"] = preview["목표"].apply(lambda x: fmt_krw(x) if x > 0 else "—")
                preview["실적"] = preview["실적"].apply(lambda x: fmt_krw(x) if x > 0 else "—")
                st.sidebar.caption(f"미리보기: **{cur_seg}**")
                st.sidebar.dataframe(
                    preview[["월", "목표", "실적", "달성률"]],
                    use_container_width=True, height=280,
                )
            st.cache_data.clear()
            st.rerun()

        elif ext == ".csv":
            new_tgt = pd.read_csv(uploaded, encoding="utf-8-sig")
            if not {"연도", "월", "매출목표"}.issubset(new_tgt.columns):
                st.sidebar.error("필수 컬럼(연도, 월, 매출목표) 누락")
            else:
                new_tgt.to_csv(KPI_FILE, index=False, encoding="utf-8-sig")
                st.sidebar.success("✅ CSV 저장 완료!")
                st.cache_data.clear()
                st.rerun()

    except Exception as e:
        st.sidebar.error(f"오류: {e}")

# 실적 소스 초기화 버튼
if ACTUALS_FILE.exists():
    if st.sidebar.button("🗑 엑셀 실적 초기화 (CMS로 전환)"):
        ACTUALS_FILE.unlink()
        st.cache_data.clear()
        st.rerun()

# CSV 템플릿
template = pd.DataFrame({"연도":[2026]*12,"월":list(range(1,13)),"매출목표":[0]*12})
st.sidebar.download_button(
    "📥 목표 CSV 템플릿",
    template.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig"),
    "kpi_targets.csv", "text/csv",
)


# ── 메인 대시보드 ────────────────────────────────────────────
st.title("🎯 KPI 목표 달성률")

_seg  = SEG_MAP[seg_choice]
today = date.today()

tab_all, tab_team = st.tabs(["📊 전체", "👥 팀별"])

# ════════════════════════════════════════════════════════════
# TAB 1 — 전체
# ════════════════════════════════════════════════════════════
with tab_all:
    actual_df, actual_src = load_monthly_actual(_seg)
    targets               = load_targets(_seg)

    st.caption(f"실적 기준: **{actual_src}**  |  목표 기준: **REPORT ({_seg})**  |  새로고침: F5")

    if actual_df.empty:
        st.warning("실적 데이터가 없습니다. 엑셀을 업로드하거나 포토이즘 크롤러를 실행하세요.")
        st.stop()

    has_targets = not targets.empty and (targets["매출목표"] > 0).any()
    if not has_targets:
        st.info("📋 아직 목표가 없습니다. 사이드바에서 **IPX MASTER DATA.xlsx** 를 업로드해주세요.")

    merged = pd.merge(actual_df, targets, on=["연도", "월"], how="outer")
    merged["실제매출"] = merged["실제매출"].fillna(0).astype(int)
    merged["매출목표"] = merged["매출목표"].fillna(0).astype(int)
    merged["달성률"]   = merged.apply(
        lambda r: round(r["실제매출"] / r["매출목표"] * 100, 1) if r["매출목표"] > 0 else None,
        axis=1,
    )
    merged["연월"] = merged["연도"].astype(str) + "-" + merged["월"].apply(lambda x: f"{x:02d}")
    merged = merged.sort_values(["연도", "월"]).reset_index(drop=True)

    days_total = calendar.monthrange(today.year, today.month)[1]
    pace_ratio = today.day / days_total
    this_row   = merged[(merged["연도"] == today.year) & (merged["월"] == today.month)]

    # ── 이번달 KPI 카드 ──────────────────────────────────────
    if not this_row.empty:
        r           = this_row.iloc[0]
        actual_this = int(r["실제매출"])
        target_this = int(r["매출목표"])
        achieve_pct = r["달성률"]
        pace_target = int(target_this * pace_ratio)
        gap         = actual_this - target_this
        pace_gap    = actual_this - pace_target

        prev_m      = today.month - 1 if today.month > 1 else 12
        prev_y      = today.year if today.month > 1 else today.year - 1
        prev_row    = merged[(merged["연도"] == prev_y) & (merged["월"] == prev_m)]
        prev_actual = int(prev_row.iloc[0]["실제매출"]) if not prev_row.empty else 0

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("이번달 목표", fmt_krw(target_this) if target_this > 0 else "미설정")
        c2.metric(
            "이번달 실적", fmt_krw(actual_this),
            f"{(actual_this-prev_actual)/prev_actual*100:+.1f}% 전월비" if prev_actual > 0 else "",
        )
        c3.metric(
            "목표 달성률",
            f"{achieve_pct:.1f}%" if achieve_pct is not None else "—",
            f"페이스({today.day}/{days_total}일) 대비 {pace_gap/target_this*100:+.1f}%"
            if target_this > 0 else "",
        )
        c4.metric(
            "목표 대비 Gap",
            fmt_krw(abs(gap)) if target_this > 0 else "—",
            "초과 달성 ✅" if gap >= 0 else "미달 ⚠️",
            delta_color="normal" if gap >= 0 else "inverse",
        )

        st.divider()

        col_g, col_b = st.columns([4, 6])
        with col_g:
            st.markdown('<div class="section-title">이번달 달성률</div>', unsafe_allow_html=True)
            gv = float(min(achieve_pct or 0, 150))
            fig_g = go.Figure(go.Indicator(
                mode  = "gauge+number+delta",
                value = gv,
                number= {"suffix": "%", "font": {"size": 44, "color": "#7209b7"}},
                delta = {"reference": 100, "suffix": "%"},
                gauge = {
                    "axis"     : {"range": [0, 150], "ticksuffix": "%", "nticks": 7},
                    "bar"      : {"color": "#7209b7", "thickness": 0.28},
                    "bgcolor"  : "white",
                    "borderwidth": 0,
                    "steps"    : [
                        {"range": [0,   70], "color": "#ffe0e0"},
                        {"range": [70,  90], "color": "#fff3cd"},
                        {"range": [90, 100], "color": "#d4edda"},
                        {"range": [100,150], "color": "#cce5ff"},
                    ],
                    "threshold": {
                        "line"     : {"color": "#e74c3c", "width": 3},
                        "thickness": 0.8, "value": 100,
                    },
                },
                title={"text": f"{today.year}년 {today.month}월  ({today.day}/{days_total}일 경과)",
                       "font": {"size": 13}},
            ))
            fig_g.update_layout(height=290, margin=dict(t=50, b=0, l=30, r=30))
            st.plotly_chart(fig_g, use_container_width=True)
            st.caption("🔴 0~70%  🟡 70~90%  🟢 90~100%  🔵 100%↑")

        with col_b:
            st.markdown('<div class="section-title">실적 / 페이스 기준 / 월 목표 비교</div>',
                        unsafe_allow_html=True)
            fig_b = go.Figure()
            for lbl, val, col in zip(
                ["이번달 실적", f"페이스 기준\n({today.day}/{days_total}일)", "월 목표"],
                [actual_this, pace_target, target_this],
                ["#7209b7", "#adb5bd", "#dee2e6"],
            ):
                fig_b.add_trace(go.Bar(
                    x=[lbl], y=[val], marker_color=col,
                    text=[fmt_krw(val)], textposition="outside",
                    hovertemplate=f"{lbl}<br>{fmt_krw(val)}<extra></extra>",
                    showlegend=False,
                ))
            fig_b.update_layout(
                height=290,
                yaxis=dict(tickformat=",", title="", showgrid=True),
                xaxis_title="", margin=dict(t=30, b=0), bargap=0.35,
            )
            st.plotly_chart(fig_b, use_container_width=True)

        st.divider()

    # ── 월별 목표 vs 실적 추이 ────────────────────────────────
    st.markdown('<div class="section-title">월별 목표 vs 실적 추이</div>', unsafe_allow_html=True)
    plot_df = merged[merged["매출목표"] > 0].copy()

    if plot_df.empty:
        st.info("목표가 입력된 월이 없습니다. 사이드바에서 IPX MASTER DATA.xlsx 를 업로드해주세요.")
    else:
        max_pct = plot_df["달성률"].dropna().max() if plot_df["달성률"].notna().any() else 100
        y2_max  = max(160, round(float(max_pct) * 1.15, -1))

        fig_m = go.Figure()
        fig_m.add_trace(go.Bar(
            x=plot_df["연월"], y=plot_df["매출목표"],
            name="목표", marker_color="#dee2e6",
            hovertemplate="%{x}<br>목표: %{y:,}원<extra></extra>",
        ))
        fig_m.add_trace(go.Bar(
            x=plot_df["연월"], y=plot_df["실제매출"],
            name="실적", marker_color="#7209b7", opacity=0.85,
            hovertemplate="%{x}<br>실적: %{y:,}원<extra></extra>",
        ))
        fig_m.add_trace(go.Scatter(
            x=plot_df["연월"], y=plot_df["달성률"],
            name="달성률", yaxis="y2",
            mode="lines+markers+text",
            line=dict(color="#f72585", width=2), marker=dict(size=9),
            text=plot_df["달성률"].apply(lambda x: f"{x:.0f}%" if x is not None else ""),
            textposition="top center",
            textfont=dict(size=11, color="#f72585"),
            hovertemplate="%{x}<br>달성률: %{y:.1f}%<extra></extra>",
        ))
        fig_m.add_trace(go.Scatter(
            x=[plot_df["연월"].iloc[0], plot_df["연월"].iloc[-1]], y=[100, 100],
            yaxis="y2", mode="lines",
            line=dict(color="#e74c3c", width=1, dash="dash"),
            name="목표 100%", hoverinfo="skip",
        ))
        fig_m.update_layout(
            height=420, barmode="group",
            yaxis =dict(tickformat=",", title="매출 (KRW)"),
            yaxis2=dict(title="달성률 (%)", overlaying="y", side="right",
                        range=[0, y2_max], ticksuffix="%", showgrid=False),
            legend=dict(orientation="h", y=1.1),
            margin=dict(t=20, b=0),
        )
        st.plotly_chart(fig_m, use_container_width=True)

        st.markdown('<div class="section-title">월별 요약</div>', unsafe_allow_html=True)

        def status(x):
            if x is None: return "—"
            if x >= 100:  return "✅ 달성"
            if x >= 80:   return "⚠️ 근접"
            return "❌ 미달"

        tbl = plot_df[["연월", "매출목표", "실제매출", "달성률"]].copy().reset_index(drop=True)
        tbl.index = tbl.index + 1
        tbl["상태"] = tbl["달성률"].apply(status)
        tbl["Gap"]  = (plot_df["실제매출"].values - plot_df["매출목표"].values)
        tbl["Gap"]  = tbl["Gap"].apply(lambda x: ("+" if x >= 0 else "") + fmt_krw(x))
        tbl["매출목표"] = tbl["매출목표"].apply(fmt_krw)
        tbl["실제매출"] = tbl["실제매출"].apply(fmt_krw)
        tbl["달성률"]   = tbl["달성률"].apply(lambda x: f"{x:.1f}%" if x is not None else "—")
        tbl.columns = ["연월", "목표", "실적", "달성률", "상태", "Gap"]
        st.dataframe(tbl, use_container_width=True, height=min(480, len(tbl) * 40 + 55))


# ════════════════════════════════════════════════════════════
# TAB 2 — 팀별
# ════════════════════════════════════════════════════════════
with tab_team:
    TEAM_COLORS = {"A팀": "#7209b7", "C팀": "#f72585", "오리지널": "#4cc9f0"}
    team_df = load_team_kpi(int(target_year))

    if team_df.empty:
        st.info("팀별 데이터가 없습니다. 사이드바에서 **IPX MASTER DATA.xlsx** 를 업로드해주세요.")
    else:
        # ── 이번달 팀별 달성률 카드 ──────────────────────────
        st.markdown(f"#### {today.year}년 {today.month}월 팀별 달성률")
        cur_teams = team_df[team_df["월"] == today.month]
        cols = st.columns(len(TEAM_LIST))
        for i, team in enumerate(TEAM_LIST):
            row = cur_teams[cur_teams["팀"] == team]
            if row.empty:
                cols[i].metric(team, "—")
                continue
            r        = row.iloc[0]
            rate_str = f"{r['달성률']:.1f}%" if r["달성률"] is not None else "—"
            gap_v    = int(r["실제매출"]) - int(r["매출목표"])
            gap_str  = ("+" if gap_v >= 0 else "") + fmt_krw(gap_v) if r["매출목표"] > 0 else ""
            cols[i].metric(
                team,
                rate_str,
                f"실적 {fmt_krw(r['실제매출'])}  /  {gap_str}",
                delta_color="normal" if gap_v >= 0 else "inverse",
            )

        st.divider()

        # ── 월별 팀별 달성률 차트 ─────────────────────────────
        st.markdown('<div class="section-title">월별 팀별 달성률 추이</div>', unsafe_allow_html=True)
        plot_t = team_df[team_df["달성률"].notna() & (team_df["매출목표"] > 0)].copy()
        plot_t["월_label"] = plot_t["월"].apply(lambda x: f"{x}월")

        fig_t = go.Figure()
        for team in TEAM_LIST:
            td = plot_t[plot_t["팀"] == team]
            fig_t.add_trace(go.Bar(
                x=td["월_label"], y=td["달성률"],
                name=team,
                marker_color=TEAM_COLORS.get(team, "#adb5bd"),
                text=td["달성률"].apply(lambda x: f"{x:.0f}%"),
                textposition="outside",
                hovertemplate=f"{team}<br>%{{x}}<br>달성률: %{{y:.1f}}%<extra></extra>",
            ))
        fig_t.add_hline(
            y=100, line_dash="dash", line_color="#e74c3c",
            annotation_text="100%", annotation_position="right",
        )
        fig_t.update_layout(
            height=380, barmode="group",
            yaxis=dict(ticksuffix="%", title="달성률"),
            xaxis_title="", legend=dict(orientation="h", y=1.1),
            margin=dict(t=20, b=0),
        )
        st.plotly_chart(fig_t, use_container_width=True)

        # ── 팀별 누적 요약 테이블 ─────────────────────────────
        st.markdown('<div class="section-title">팀별 누적 요약</div>', unsafe_allow_html=True)
        summary_rows = []
        for team in TEAM_LIST:
            td   = team_df[team_df["팀"] == team]
            ytd  = td[td["실제매출"] > 0]
            tot_tgt = int(ytd["매출목표"].sum())
            tot_act = int(ytd["실제매출"].sum())
            rate    = round(tot_act / tot_tgt * 100, 1) if tot_tgt > 0 else None
            summary_rows.append({
                "팀":      team,
                "누적 목표": fmt_krw(tot_tgt),
                "누적 실적": fmt_krw(tot_act),
                "달성률":   f"{rate:.1f}%" if rate else "—",
                "상태":     ("✅ 달성" if rate >= 100 else ("⚠️ 근접" if rate >= 80 else "❌ 미달"))
                             if rate else "—",
            })
        st.dataframe(
            pd.DataFrame(summary_rows).set_index("팀"),
            use_container_width=True, height=175,
        )
