# -*- coding: utf-8 -*-
"""SM 촬영 현황 — 'SM ent' 타이틀의 날짜 × 테마 × 프레임 × 국가별 촬영수.
대행사·소속사 공유용. 화면에서 보고, 엑셀로 내려받는다.
데이터: 일별 거래(master_photoism.parquet) + theme_map 조인 (sm_shooting.py).
"""
import io
import os
import sys
from pathlib import Path

import pandas as pd
import streamlit as st
import plotly.express as px

# set_page_config 는 라우터(스내피즘.py)에서 처리
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sm_shooting

BASE_DIR = Path(__file__).parent.parent
TX_PARQUET = BASE_DIR / "data" / "master_photoism.parquet"
THEME_MAP = BASE_DIR / "data" / "theme_map.parquet"
INK = "#1a1a2e"

st.markdown(f"""
<style>
.section-title {{
    font-size: 1.12rem; font-weight: 700; color: {INK};
    margin: 4px 0 12px; padding-left: 12px;
    border-left: 4px solid #4361ee; line-height: 1.4;
}}
</style>
""", unsafe_allow_html=True)

st.title("🎬 SM 촬영 현황")
st.caption("이름에 **'SM ent'** 가 들어간 타이틀의 **테마 · 프레임(멤버) · 국가별 촬영수**를 일별로 봐요. "
           "촬영수는 주문(거래) 건수예요.")


@st.cache_data(show_spinner="SM 촬영 데이터를 불러오는 중…")
def _load(_tx_mtime: float, _map_mtime: float) -> pd.DataFrame:
    return sm_shooting.load_sm_shooting()


def _mtime(p: Path) -> float:
    try:
        return p.stat().st_mtime
    except OSError:
        return 0.0


try:
    g = _load(_mtime(TX_PARQUET), _mtime(THEME_MAP))
except Exception as e:
    st.error(f"데이터를 불러오지 못했어요. 잠시 후 다시 시도해 주세요. ({e})")
    st.stop()

if g.empty:
    st.warning("표시할 SM 촬영 데이터가 없어요.")
    st.stop()

# ── 사이드바 필터 ──
st.sidebar.header("🔍 필터")
days = sorted(g["날짜"].astype(str).unique())
d_min, d_max = days[0], days[-1]
date_range = st.sidebar.date_input(
    "날짜 범위",
    value=(pd.to_datetime(d_min).date(), pd.to_datetime(d_max).date()),
    min_value=pd.to_datetime(d_min).date(), max_value=pd.to_datetime(d_max).date(),
)
if isinstance(date_range, (list, tuple)) and len(date_range) == 2:
    s_str, e_str = str(date_range[0]), str(date_range[1])
else:
    s_str, e_str = d_min, d_max

all_countries = sorted(g["국가"].unique())
sel_countries = st.sidebar.multiselect("국가", all_countries, default=all_countries)
all_themes = sorted(g["테마"].unique())
sel_themes = st.sidebar.multiselect("테마", all_themes, default=all_themes)

f = g[(g["날짜"].astype(str) >= s_str) & (g["날짜"].astype(str) <= e_str)]
if sel_countries:
    f = f[f["국가"].isin(sel_countries)]
if sel_themes:
    f = f[f["테마"].isin(sel_themes)]

if f.empty:
    st.warning("선택한 조건에 해당하는 촬영 데이터가 없어요. 필터를 넓혀 보세요.")
    st.stop()

# ── KPI ──
c1, c2, c3, c4 = st.columns(4)
c1.metric("총 촬영수", f"{int(f['촬영수'].sum()):,}")
c2.metric("국가", f"{f['국가'].nunique()}")
c3.metric("테마", f"{f['테마'].nunique()}")
c4.metric("프레임(멤버)", f"{f['프레임'].nunique()}")
st.divider()

tab_pivot, tab_trend, tab_raw = st.tabs(["📊 테마·프레임 × 국가", "📈 일별 추이", "📋 원본"])

with tab_pivot:
    st.markdown('<div class="section-title">테마 · 프레임별 국가 촬영수</div>', unsafe_allow_html=True)
    pivot = pd.pivot_table(
        f, index=["테마", "프레임"], columns="국가", values="촬영수",
        aggfunc="sum", fill_value=0, margins=True, margins_name="합계",
    ).astype(int)
    # 합계 행을 맨 위로
    pivot = pivot.sort_values("합계", ascending=False)
    st.dataframe(pivot, use_container_width=True, height=520)
    st.caption("값 = 촬영수(주문 건수). 마지막 **합계** 행/열은 소계예요.")

with tab_trend:
    st.markdown('<div class="section-title">일별 촬영수 추이</div>', unsafe_allow_html=True)
    daily = f.groupby(["날짜", "테마"], as_index=False)["촬영수"].sum()
    top_themes = (f.groupby("테마")["촬영수"].sum().sort_values(ascending=False).head(8).index.tolist())
    daily_top = daily[daily["테마"].isin(top_themes)]
    fig = px.area(daily_top, x="날짜", y="촬영수", color="테마",
                  title="상위 8개 테마 일별 촬영수")
    fig.update_layout(legend_title_text="테마", height=420, margin=dict(t=46, l=8, r=8, b=8))
    st.plotly_chart(fig, use_container_width=True)
    st.caption("상위 8개 테마만 표시해요. 전체는 원본 탭/엑셀에서 볼 수 있어요.")

with tab_raw:
    st.markdown('<div class="section-title">원본 (날짜 · 테마 · 프레임 · 국가 · 촬영수)</div>', unsafe_allow_html=True)
    st.dataframe(f.reset_index(drop=True), use_container_width=True, height=520, hide_index=True)

# ── 엑셀 다운로드 ──
st.divider()


@st.cache_data(show_spinner=False)
def _build_xlsx(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    pv = pd.pivot_table(df, index=["테마", "프레임"], columns="국가", values="촬영수",
                        aggfunc="sum", fill_value=0, margins=True, margins_name="합계").astype(int)
    pv_day = pd.pivot_table(df, index=["테마", "프레임"], columns="날짜", values="촬영수",
                            aggfunc="sum", fill_value=0, margins=True, margins_name="합계").astype(int)
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        df.to_excel(xw, sheet_name="원본", index=False)
        pv.to_excel(xw, sheet_name="피벗_국가")
        pv_day.to_excel(xw, sheet_name="피벗_일별")
    return buf.getvalue()


xlsx = _build_xlsx(f)
st.download_button(
    "📥 엑셀 다운로드 (SM 촬영 현황)",
    data=xlsx,
    file_name=f"SM촬영현황_{s_str}_{e_str}.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    use_container_width=True,
)
st.caption("엑셀 3시트: **원본**(날짜·테마·프레임·국가·촬영수) · **피벗_국가** · **피벗_일별**. "
           "공유 시트 형태에 맞춰 더 다듬을 수 있어요.")
