# -*- coding: utf-8 -*-
"""SM 촬영 현황 — CMS '매출정보(Artist별 촬영수)' 기준 일일 촬영수.
'SM ent' 타이틀의 테마 · 프레임(멤버) · 국가별 촬영수를 일별로 본다.
데이터: sm_collect.py 가 CMS /v1/revenue/frame 에서 직접 받은 sm_shoot_daily.parquet
        (CMS 화면값과 일치. 매주 월요일 갱신·덮어쓰기). 엑셀은 sm_report.build_xlsx.
"""
import os
import sys
from datetime import timedelta
from pathlib import Path

import pandas as pd
import streamlit as st
import plotly.express as px

# set_page_config 는 라우터(스내피즘.py)에서 처리
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sm_report
import auth

# 소유자 전용 — URL 직접 접근 차단
_email = (st.user.email or "").strip().lower() if getattr(st, "user", None) else ""
# 팀에 이 페이지가 허용됐으면 소유자가 아니어도 들어올 수 있다.
# (라우터가 사이드바에서 이미 걸러주지만, url 직접 입력 대비로 여기서도 막는다)
if not auth.can_view_page(_email, "sm"):
    st.error("🔒 이 페이지에 접근할 권한이 없어요. 필요하면 관리자에게 요청해 주세요.")
    st.stop()

BASE_DIR = Path(__file__).parent.parent
DAILY_PARQUET = BASE_DIR / "data" / "sm_shoot_daily.parquet"
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
st.caption("CMS **매출정보(Artist별 촬영수)** 기준이에요. 이름에 **'SM ent'** 들어간 타이틀의 "
           "**테마 · 프레임(멤버) · 국가별 일일 촬영수**로, **CMS 화면값과 일치**해요. (매일 자동 갱신)")

if not DAILY_PARQUET.exists():
    st.warning("아직 CMS 수집 데이터가 없어요. 터미널에서 `python sm_collect.py 시작일 종료일` 로 먼저 수집해 주세요.")
    st.stop()


@st.cache_data(show_spinner="CMS 촬영 데이터를 불러오는 중…")
def _load(_mtime: float) -> pd.DataFrame:
    return sm_report.load_daily()


try:
    g = _load(DAILY_PARQUET.stat().st_mtime)
except Exception as e:
    st.error(f"데이터를 불러오지 못했어요. 잠시 후 다시 시도해 주세요. ({e})")
    st.stop()

if g.empty:
    st.warning("표시할 SM 촬영 데이터가 없어요.")
    st.stop()

# 갱신 시점 안내
last_day = g["날짜"].max()
st.caption(f"데이터는 **오픈부터 계속 누적**돼요(현재 {g['날짜'].min()} ~ {last_day}). 좁히면 주간, **전체로 두면 정산용**이에요. "
           "⚠️ 최근 1~2일 값은 CMS 정착 전이라 며칠 뒤 바뀔 수 있어요(갱신 시 덮어쓰고 변경내역 기록).")

# 🆕 미분류 SM IP 감지 알림 — 팔리는데 아직 아티스트 탭에 없는 새 테마
_um = sm_report.detect_unmatched(g)
if not _um.empty:
    _lst = "".join(f"\n- **{t}** (최근7일 {int(r)} · 누적 {int(tot)})"
                   for t, r, tot in zip(_um["테마"], _um["최근판매"], _um["총촬영수"]))[:1500]
    st.warning(
        f"🆕 아직 분류에 없는 **새 SM 테마 {len(_um)}건**이 감지됐어요:{_lst}\n\n"
        "정식 아티스트 탭으로 넣으려면 `sm_artists.json`에 한 줄 추가하면 돼요. "
        "자세한 내역은 다운로드 엑셀의 **미분류IP** 시트에서 볼 수 있어요."
    )

# ── 사이드바 필터 ──
st.sidebar.header("🔍 필터")
days = sorted(g["날짜"].astype(str).unique())
dmin = pd.to_datetime(days[0]).date()
dmax = pd.to_datetime(days[-1]).date()

preset = st.sidebar.radio("보기 기간", ["전체 (정산용)", "최근 4주", "최근 2주", "이번 주(7일)", "직접 지정"], index=0)
if preset == "직접 지정":
    dr = st.sidebar.date_input("날짜 범위", value=(dmin, dmax), min_value=dmin, max_value=dmax)
    if isinstance(dr, (list, tuple)) and len(dr) == 2:
        s_d, e_d = dr
    else:
        s_d, e_d = dmin, dmax
else:
    span = {"전체 (정산용)": None, "최근 4주": 27, "최근 2주": 13, "이번 주(7일)": 6}[preset]
    e_d = dmax
    s_d = dmin if span is None else max(dmin, dmax - timedelta(days=span))
s_str, e_str = str(s_d), str(e_d)

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
    ).astype(int).sort_values("합계", ascending=False)
    st.dataframe(pivot, use_container_width=True, height=520)
    st.caption("값 = 촬영수(Artist별 촬영수). 마지막 **합계** 행/열은 소계예요.")

with tab_trend:
    st.markdown('<div class="section-title">일별 촬영수 추이</div>', unsafe_allow_html=True)
    daily = f.groupby(["날짜", "테마"], as_index=False)["촬영수"].sum()
    top_themes = f.groupby("테마")["촬영수"].sum().sort_values(ascending=False).head(8).index.tolist()
    fig = px.area(daily[daily["테마"].isin(top_themes)], x="날짜", y="촬영수", color="테마",
                  title="상위 8개 테마 일별 촬영수")
    fig.update_layout(legend_title_text="테마", height=420, margin=dict(t=46, l=8, r=8, b=8))
    st.plotly_chart(fig, use_container_width=True)
    st.caption("상위 8개 테마만 표시해요. 전체는 원본 탭/엑셀에서 볼 수 있어요.")

with tab_raw:
    st.markdown('<div class="section-title">원본 (날짜 · 국가 · 테마 · 프레임 · 촬영수)</div>', unsafe_allow_html=True)
    st.dataframe(
        f[["날짜", "국가", "테마", "프레임", "촬영수", "주문수", "최종결제금액"]].reset_index(drop=True),
        use_container_width=True, height=520, hide_index=True)

# ── 엑셀 다운로드 ──
st.divider()


@st.cache_data(show_spinner=False, max_entries=4)   # 다운로드용 바이트 — 몇 개만 보관
def _xlsx(df: pd.DataFrame) -> bytes:
    return sm_report.build_xlsx(df)


st.download_button(
    "📥 엑셀 다운로드 (부서 공유용)",
    data=_xlsx(f),
    file_name=f"SM촬영현황_{s_str}_{e_str}.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    use_container_width=True,
)
st.caption("엑셀: **요약** + **아티스트별 시트**(NCT WISH·라이즈·아이린·승한·태용·샤이니·NCT 재민제노) + **국가별**. "
           "멤버 한·영 통합, CMS 값과 일치해요. (현재 오픈 IP만)")
