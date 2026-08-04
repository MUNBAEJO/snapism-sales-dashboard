# -*- coding: utf-8 -*-
"""IP 오픈 캘린더 — Jira CANDIP '시작 날짜' 기준 상품 오픈 일정.

데이터: jira_ip_dates.fetch_ip_schedule (12시간 캐시) → ip_calendar 가 정리
        상품 하나에 티켓이 2개씩 잡히므로 (오픈일·브랜드·이름)으로 합친다.
필터는 전부 본문에 둔다 — 사이드바는 페이지 이동 전용으로 비워 뒀다.
"""
import os
import sys
from datetime import date

import pandas as pd
import streamlit as st

# set_page_config 는 라우터(스내피즘.py)에서 처리
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import auth
import ip_calendar as ipc
import ip_calendar_ui as ipu
import ui_theme

_email = (st.user.email or "").strip().lower() if getattr(st, "user", None) else ""
if not auth.can_view_page(_email, "ipcal"):
    st.error("🔒 이 페이지에 접근할 권한이 없어요. 필요하면 관리자에게 요청해 주세요.")
    st.stop()

ui_theme.inject()
ipu.inject()

TODAY = date.today()
SEL = "ipcal_day"

st.markdown('<div style="font-size:26px;font-weight:800;letter-spacing:-.03em;'
            'color:var(--text);margin:2px 0 4px">📅 IP 오픈 캘린더</div>'
            '<div style="font-size:13px;color:var(--text-3);margin-bottom:6px">'
            'Jira <b>CANDIP</b> 의 <b>시작 날짜</b>를 오픈일로 봐요. '
            '스내피즘 · 포토이즘 상품 일정을 함께 보여드려요.</div>',
            unsafe_allow_html=True)

df = ipu.load()
if df.empty:
    ui_theme.nbox("warn", "Jira 일정을 불러오지 못했어요. 잠시 뒤 다시 시도해 주세요.")
    st.stop()

# ── 필터 (본문) ──
ui_theme.sec(1, "무엇을 볼까요", "브랜드 · 진행 상태 · 이름으로 좁힐 수 있어요")
brands_all = [b for b in ipc.BRAND_ORDER if b in set(df["브랜드"])]
statuses_all = sorted(set(df["상태"]))

with ui_theme.card():
    # 상태는 9종이라 그대로 펼치면 태그가 세 줄로 부풀어 필터 카드가 무너진다 → 팝오버로.
    f1 = st.columns([4, 4, 2], vertical_alignment="bottom")
    with f1[0]:
        sel_brands = st.pills("브랜드", brands_all, selection_mode="multi",
                              default=brands_all, key="cal_brand")
    with f1[1]:
        kw = st.text_input("IP 이름 검색", placeholder="예) 세븐틴", key="cal_kw")
    with f1[2]:
        _n = len(st.session_state.get("cal_status", statuses_all))
        with st.popover(f"진행 상태 ({_n}/{len(statuses_all)})", use_container_width=True):
            sel_status = st.multiselect(
                "표시할 상태", statuses_all, default=statuses_all, key="cal_status",
                label_visibility="collapsed",
                help="Jira 티켓 상태예요. '완료 · 송출 중'은 이미 지나간 건이에요.")

f = df[df["브랜드"].isin(sel_brands or []) & df["상태"].isin(sel_status or [])]
if kw.strip():
    f = f[f["IP"].str.contains(kw.strip(), case=False, na=False, regex=False)]

# ── 월 이동 ──
if "ipcal_ym" not in st.session_state:
    st.session_state.ipcal_ym = (TODAY.year, TODAY.month)


def _shift(delta: int):
    yy, mm = st.session_state.ipcal_ym
    idx = yy * 12 + (mm - 1) + delta
    st.session_state.ipcal_ym = (idx // 12, idx % 12 + 1)
    st.session_state[SEL] = None          # 달을 넘기면 열어 둔 날짜는 닫는다


def _to_today():
    st.session_state.ipcal_ym = (TODAY.year, TODAY.month)
    st.session_state[SEL] = None


y, m = st.session_state.ipcal_ym
first, last = ipc.month_bounds(y, m)
month_df = ipc.in_range(f, first, last)

# ── 요약 ──
mon, sun = ipc.week_bounds(TODAY)
ui_theme.kpis([
    ui_theme.kpi(f"{y}년 {m}월 오픈", f"{len(month_df):,}건",
                 f"전체 <b>{len(f):,}건</b> 중", hero=True),
    ui_theme.kpi("오늘", f"{len(ipc.in_range(f, TODAY, TODAY)):,}건"),
    ui_theme.kpi("이번 주", f"{len(ipc.in_range(f, mon, sun)):,}건",
                 f"{mon.month}/{mon.day}~{sun.month}/{sun.day}"),
    ui_theme.kpi("앞으로 30일", f"{len(ipc.upcoming(f, TODAY, 30)):,}건"),
], "k4")

# ── 달력 ──
ui_theme.sec(2, "달력", "날짜를 누르면 그날 오픈하는 IP 를 전부 펼쳐 보여드려요")
with ui_theme.card():
    # 달 이동 · 제목 · 범례를 한 줄로 — 버튼만 따로 떠 있으면 급조한 화면처럼 보인다.
    nav = st.columns([0.7, 0.7, 0.7, 8], vertical_alignment="center")
    nav[0].button("◀", use_container_width=True, on_click=_shift, args=(-1,),
                  help="이전 달")
    nav[1].button("오늘", use_container_width=True, on_click=_to_today)
    nav[2].button("▶", use_container_width=True, on_click=_shift, args=(1,),
                  help="다음 달")
    with nav[3]:
        ipu.header(y, m, len(month_df), brands_all)
    picked = ipu.render_month(month_df, y, m, TODAY, sel_key=SEL)

if picked is not None:
    ipu.render_day_detail(f, picked, sel_key=SEL)

# ── 목록 ──
# 달력이 주인공이라 목록은 접어 둔다 — 펼쳐 두면 스크롤이 길어져 달력이 밀린다.
def _table(d: pd.DataFrame):
    if d.empty:
        st.info("해당하는 오픈 일정이 없어요. 필터를 넓혀 보세요.")
        return
    # 계약(상위)은 화면에서 빼고 CSV 에만 남긴다 — 서브태스크 쪽은 비어 있어 열이 휑하다.
    v = d[["오픈일", "브랜드", "IP", "상태", "티켓"]].copy()
    v.insert(1, "요일", [ipu._WD[x.weekday()] for x in v["오픈일"]])
    st.dataframe(v, use_container_width=True, hide_index=True, height=460,
                 column_config={"IP": st.column_config.TextColumn(width="large")})


with st.expander(f"📋 목록으로 보기 — {m}월 {len(month_df):,}건 · 필터가 그대로 적용돼요"):
    tab_month, tab_up = st.tabs([f"{m}월 전체 ({len(month_df)}건)", "⏭️ 다가오는 60일"])
    with tab_month:
        _table(month_df)
        st.download_button("📥 이 달 일정 CSV",
                           data=month_df.to_csv(index=False).encode("utf-8-sig"),
                           file_name=f"IP오픈일정_{y}-{m:02d}.csv", mime="text/csv")
    with tab_up:
        _table(ipc.upcoming(f, TODAY, 60))

# ── 안내 ──
st.markdown("")
c = st.columns([5, 1], vertical_alignment="center")
c[0].caption(
    "ℹ️ **오픈일**은 Jira 의 '시작 날짜' 필드예요. 상품 하나에 티켓이 보통 2개(상위 작업 + "
    "'프로그램 및 검수' 서브태스크)라 **같은 날 · 같은 이름은 한 줄로 합쳤어요.** "
    "상태는 그중 가장 진행된 값이에요. Jira 값은 12시간마다 갱신돼요.")
if c[1].button("🔄 지금 새로고침", use_container_width=True,
               help="Jira 에서 일정을 다시 받아와요. 몇십 초 걸려요."):
    ipu.load.clear()
    with st.spinner("Jira 에서 일정을 다시 받는 중…"):
        try:
            ipc.load_openings(brand="all", force_refresh=True)
        except Exception as e:
            st.warning(f"새로고침에 실패했어요. 기존 값으로 계속 볼게요. ({e})")
    st.rerun()
