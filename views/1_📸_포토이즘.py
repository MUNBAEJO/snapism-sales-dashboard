# -*- coding: utf-8 -*-
"""포토이즘 매출 대시보드 — 재디자인(시안 snapism-hybrid 기준, 스내피즘과 동일 디자인 시스템).

구조: 인라인 필터바(컴팩트 칩) + KPI 3카드 + 6탭
      (매출 한눈에·IP·타이틀 분석·국가별·매장별·세부 항목·시간대/데이터).
매출 = 실결제 + 쿠폰기여 + 코인기여(지정 국가만 가산). 데이터 로직·로더·DuckDB 세부검색은
기존 그대로 보존(비파괴). 표현 계층만 스내피즘 시안형(CSS 차트·카드key·컴팩트 위젯)으로 교체.
"""
import json
import sys
import os
from contextlib import contextmanager
from pathlib import Path
from datetime import date, timedelta

import pyarrow.parquet as pq
import pandas as pd
import streamlit as st

# set_page_config 는 라우터(스내피즘.py)에서 처리
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from guide_content import render_guide
import ip_classify  # IP구분/IP명 분류 공용 모듈
import photoism_rules  # 매출액 가산 규칙(쿠폰·코인 국가)
import auth

# ══════════════════════════════════════════════════════════════
#  디자인 시스템 (시안 토큰 이식 — 스내피즘과 동일)
# ══════════════════════════════════════════════════════════════
st.markdown("""
<style>
@import url("https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/variable/pretendardvariable-dynamic-subset.min.css");
:root{
  --bg:#f4f5f7; --surface:#fff; --surface-2:#f8fafc; --surface-3:#eef1f5;
  --border:#e7e9ee; --border-strong:#d7dae1;
  --text:#1b2330; --text-2:#5b6573; --text-3:#98a0af;
  --brand:#4f46e5; --brand-2:#6366f1; --brand-soft:#eef0fe;
  --red:#c0322b; --green:#15803d; --amber:#b45309; --sky:#38a3e8; --teal:#0f9d77; --pink:#d24d8b;
}
/* Pretendard 강제 적용(맑은고딕 폴백 방지) — 시안의 부드러운 느낌 */
html, body, [class*="css"], [data-testid="stAppViewContainer"], [data-testid="stSidebar"],
button, input, select, textarea, label, p, span, div, h1, h2, h3, h4, li, a,
[data-baseweb], [data-testid="stMarkdownContainer"], [data-testid="stMetricValue"]{
  font-family:'Pretendard Variable','Pretendard',-apple-system,BlinkMacSystemFont,
              'Segoe UI','Malgun Gothic','Apple SD Gothic Neo',sans-serif !important;
}
html, body{ letter-spacing:-0.02em; }
/* 페이지 배경 회색(#f4f5f7) — 흰 카드가 떠 보이게(시안 표면 분리). */
.stApp, [data-testid="stAppViewContainer"], [data-testid="stMain"], .stMain, section.main{
  background:var(--bg) !important; }
[data-testid="stMainBlockContainer"], .block-container{ background:transparent !important; }
[data-testid="stMainBlockContainer"], .stMainBlockContainer, section.main .block-container, .block-container{
  max-width:1680px !important; margin-left:auto !important; margin-right:auto !important;
  padding-top:1.4rem !important; padding-bottom:3rem !important;
  padding-left:1.6rem !important; padding-right:1.6rem !important; }
h1{ font-size:24px !important; font-weight:800 !important; letter-spacing:-0.03em !important; color:var(--text); }
h2, h3{ letter-spacing:-0.02em !important; }
/* 카드 = 시안 톤. 메인의 모든 border-wrapper 무력화 후 card()(key=scard-*)에만 카드 스타일. */
[data-testid="stMain"] [data-testid="stVerticalBlockBorderWrapper"]{
  border:none !important; box-shadow:none !important; background:transparent !important;
  padding:0 !important; margin:0 !important;
}
[data-testid="stMain"] [class*="st-key-scard-"]{
  border:1px solid var(--border) !important; border-radius:14px !important;
  box-shadow:0 1px 2px rgba(20,28,45,.04),0 1px 3px rgba(20,28,45,.06) !important;
  padding:15px 18px !important; margin-bottom:14px !important; background:#fff !important;
}
[data-testid="stCaptionContainer"], [data-testid="stCaptionContainer"] p{ font-size:14px !important; color:#8b95a1 !important; }
[data-testid="stDeployButton"]{ display:none !important; }
[data-testid="stElementToolbar"]{ display:none; }
.num{ font-variant-numeric:tabular-nums; }

/* KPI 카드 */
.kpis{ display:grid; grid-template-columns:2fr 1fr 1fr; gap:12px; margin:14px 0 8px; }
.kpi{ background:var(--surface); border:1px solid var(--border); border-radius:12px; padding:15px 17px;
      box-shadow:0 1px 2px rgba(20,28,45,.04),0 1px 3px rgba(20,28,45,.06); }
.kpi.hero{ background:linear-gradient(180deg,#fbfbff,#fff); border-color:#dcdcfb; }
.kpi .l{ font-size:12.5px; color:var(--text-2); font-weight:600; }
.kpi .v{ font-size:24px; font-weight:800; letter-spacing:-0.02em; margin-top:6px; line-height:1.05; color:var(--text); }
.kpi.hero .v{ font-size:33px; color:var(--brand); }
.kpi .d{ font-size:12px; font-weight:700; margin-top:7px; color:var(--text-3); }
.kpi .d.up{ color:var(--green); } .kpi .d.down{ color:var(--red); }
@media(max-width:720px){ .kpis{ grid-template-columns:1fr; } }

/* 범위 배너 */
.scope{ background:var(--brand-soft); border:1px solid #cdd0fb; color:var(--brand); font-size:12.5px;
        font-weight:600; padding:9px 14px; border-radius:10px; margin:6px 0 2px; }

/* 섹션 헤더 */
.sechd{ display:flex; align-items:center; gap:10px; margin:28px 0 2px; }
.secn{ font-size:12px; font-weight:800; color:#fff; background:var(--brand); width:22px; height:22px;
       border-radius:7px; display:inline-flex; align-items:center; justify-content:center; flex:0 0 auto; }
.sect{ font-size:18px; font-weight:800; letter-spacing:-0.02em; color:var(--text); }
.secq{ font-size:12.5px; color:var(--text-3); margin:2px 0 10px 32px; }

/* 카드 제목 */
.ct{ font-size:14.5px; font-weight:700; display:flex; align-items:center; gap:7px; margin:2px 0 10px; color:var(--text); }
.ct .muted{ font-weight:500; color:var(--text-3); font-size:12.5px; }

/* 미니 지표 3칸 (IP 상세 등) */
.mstrow{ display:flex; gap:12px; margin:2px 0 12px; flex-wrap:wrap; }
.mst{ flex:1; min-width:110px; background:var(--surface-2); border:1px solid var(--border); border-radius:10px; padding:10px 14px; }
.mst-l{ font-size:11.5px; color:var(--text-2); font-weight:600; }
.mst-v{ font-size:18px; font-weight:800; color:var(--text); margin-top:3px; }

/* 비중막대 내장 표 (.ntbl) */
.ntbl{ border:1px solid var(--border); border-radius:12px; overflow:hidden; margin:2px 0 4px; }
.ntr{ display:grid; align-items:center; gap:10px; padding:13px 18px; border-bottom:1px solid var(--border);
      font-size:13px; color:var(--text); }
.ntr:last-child{ border-bottom:none; }
.ntr.nth{ background:var(--surface-2); font-size:11px; font-weight:700; color:var(--text-3); letter-spacing:.02em; }
.ntr:not(.nth):hover{ background:var(--surface-2); }
.ntr .r{ text-align:right; } .ntr .c{ text-align:center; }
.nname{ font-weight:700; }
/* 타이틀 상태 배지 + 판매기간 (타이틀 순위표) */
.tstat{ display:inline-block; margin-left:7px; font-size:10.5px; font-weight:700;
        border-radius:6px; padding:1.5px 6px; white-space:nowrap; vertical-align:middle; }
.tstat.end{  background:#f1f2f5; color:#6b7280; }
.tstat.warn{ background:#fdecea; color:var(--red); }
.tstat.post{ background:#fff4e6; color:#c2410c; }
.tstat.new{  background:var(--brand-soft); color:var(--brand); }
.tstat.soon{ background:#fdf3e7; color:var(--amber); }
.tstat.live{ background:#eefaf4; color:var(--green); }
.tstat.unk{  background:#f6f7f9; color:var(--text-3); }
.tper{ font-size:11.5px; color:var(--text-2); white-space:nowrap; }
.cur{ font-size:11px; font-weight:700; color:var(--text-2); background:var(--surface-3); padding:2px 8px; border-radius:6px; }
.rk{ font-weight:800; color:var(--text-3); font-variant-numeric:tabular-nums; }
.rk.top{ color:var(--brand); }
.npct{ display:flex; align-items:center; gap:9px; }
.npct-bar{ flex:1; height:7px; background:var(--surface-3); border-radius:5px; overflow:hidden; }
.npct-bar i{ display:block; height:100%; background:var(--brand-2); border-radius:5px; }
.npct .p{ font-size:12.5px; font-weight:700; font-variant-numeric:tabular-nums; min-width:44px; text-align:right; }

/* 가로 막대 순위 (시안 .hbar) */
.hb-wrap{ display:flex; flex-direction:column; gap:5px; padding:4px 0; height:100%; justify-content:center; }
.hb{ display:grid; grid-template-columns:150px 1fr 118px; align-items:center; gap:12px; font-size:13px; padding:8px 0; }
.hb-n{ font-weight:600; color:var(--text-2); text-align:right; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; font-size:13px; }
.hb-track{ height:22px; background:var(--surface-3); border-radius:6px; overflow:hidden; }
.hb-track i{ display:block; height:100%; border-radius:6px; }
.hb-v{ text-align:right; font-weight:700; color:var(--text); font-variant-numeric:tabular-nums; font-size:13px; }
[data-testid="stColumn"] [class*="st-key-scard-"]{ height:100% !important; }

/* CSS 차트 (Plotly 대체) */
.donut-wrap{ display:flex; align-items:center; gap:18px; padding:2px 0; }
.donut{ border-radius:50%; flex:0 0 auto; }
/* 범례 폭 상한 — flex:1 로 두면 전체폭 카드에서 라벨과 %가 양끝으로 밀려
   가운데가 텅 빈다(margin-left:auto 때문). 좁은 칼럼에선 상한이라 영향 없음. */
.leg2{ display:flex; flex-direction:column; gap:8px; font-size:13px;
       flex:1 1 auto; max-width:420px; }
.leg2 .row{ display:flex; align-items:center; gap:9px; color:var(--text); }
.leg2 .row b{ margin-left:auto; font-weight:800; font-variant-numeric:tabular-nums; }
.leg2 .sub{ color:var(--text-3); font-size:12px; }
.dot{ width:10px; height:10px; border-radius:3px; display:inline-block; flex:0 0 auto; }
.legend{ display:flex; gap:16px; font-size:12px; color:var(--text-2); margin-bottom:10px; flex-wrap:wrap; }
.legend span{ display:inline-flex; align-items:center; gap:6px; }
.chart{ display:flex; align-items:flex-end; height:200px; padding:6px 4px 0; border-bottom:1px solid var(--border); }
.col{ flex:1; display:flex; flex-direction:column; justify-content:flex-end; align-items:center; height:100%; }
.stack{ width:58%; max-width:70px; display:flex; flex-direction:column; justify-content:flex-end;
        border-radius:5px 5px 0 0; overflow:hidden; }
.xlab{ font-size:11px; color:var(--text-3); margin-top:7px; font-weight:600; }
.hours{ display:flex; align-items:flex-end; gap:5px; height:180px; border-bottom:1px solid var(--border); padding-top:8px; }
.hours .hc{ flex:1; display:flex; flex-direction:column; justify-content:flex-end; align-items:center; height:100%; }
.hours .hb2{ width:70%; border-radius:3px 3px 0 0; }
.hours .hx{ font-size:9.5px; color:var(--text-3); margin-top:4px; }
.strip{ font-size:12.5px; color:var(--text-2); background:var(--surface-2); border:1px solid var(--border);
        border-radius:10px; padding:9px 14px; margin-top:12px; }
.strip b{ color:var(--text); font-weight:700; }

/* ── 즉시(hover) 매출 툴팁 — 딜레이 없이 커서 올리면 바로 박스 ── */
.tip{ position:relative; }
.tip::after{
  content:attr(data-tip);
  position:absolute; left:50%; bottom:100%; transform:translateX(-50%) translateY(-7px);
  background:#1b2330; color:#fff; font-size:11.5px; font-weight:600; line-height:1.4;
  padding:6px 10px; border-radius:8px; white-space:nowrap; text-align:center;
  opacity:0; pointer-events:none; transition:opacity .07s ease;
  box-shadow:0 6px 18px rgba(20,28,45,.22); z-index:60; }
.tip::before{
  content:""; position:absolute; left:50%; bottom:100%; transform:translateX(-50%) translateY(-1px);
  border:5px solid transparent; border-top-color:#1b2330;
  opacity:0; pointer-events:none; transition:opacity .07s ease; z-index:60; }
.tip:hover::after, .tip:hover::before{ opacity:1; }
/* ★표(.ntbl)는 모서리를 둥글리려 overflow:hidden 이라, 위로 뜨는 기본 툴팁이 통째로
   잘려 안 보인다. 표 헤더에는 .dn 을 붙여 '아래쪽·오른쪽 정렬'로 표 안에서 펼친다. */
.tip.dn::after{ bottom:auto; top:100%; left:auto; right:0; transform:translateY(7px);
  white-space:normal; width:max-content; max-width:260px; text-align:left; font-weight:600; }
.tip.dn::before{ bottom:auto; top:100%; left:auto; right:12px; transform:translateY(2px);
  border-top-color:transparent; border-bottom-color:#1b2330; }
.col, .hours .hc{ position:relative; }
.vtip{
  position:absolute; left:50%; transform:translateX(-50%) translateY(-8px);
  background:#1b2330; color:#fff; font-size:11.5px; font-weight:600; line-height:1.4;
  padding:6px 10px; border-radius:8px; white-space:nowrap; text-align:center;
  opacity:0; pointer-events:none; transition:opacity .07s ease;
  box-shadow:0 6px 18px rgba(20,28,45,.22); z-index:60; }
.vtip::after{
  content:""; position:absolute; left:50%; top:100%; transform:translateX(-50%);
  border:5px solid transparent; border-top-color:#1b2330; }
.col:hover .vtip, .hours .hc:hover .vtip{ opacity:1; }

/* Streamlit 기본 크롬 정리 */
[data-testid="stToolbar"]{ display:none !important; }
#MainMenu, footer{ display:none !important; }
[data-testid="stHeader"]{ background:transparent; height:0 !important; }
[data-testid="stSidebar"]{ background:#ffffff !important; border-right:1px solid #e5e8eb !important; }
[data-testid="stSidebarNav"] a{ border-radius:10px !important; padding:9px 12px !important; margin:1px 0 !important; }
[data-testid="stSidebarNav"] a:hover{ background:#f2f4f8 !important; }
[data-testid="stSidebar"] hr{ border-color:#eef1f5 !important; }

/* 탭 = 시안 언더라인 스타일 */
[data-baseweb="tab-list"]{ gap:2px; border-bottom:1px solid var(--border); }
button[data-baseweb="tab"]{ padding:10px 15px; }
button[data-baseweb="tab"] p{ font-size:14px !important; font-weight:700 !important; color:var(--text-2) !important; }
button[data-baseweb="tab"][aria-selected="true"] p{ color:var(--brand) !important; }
[data-baseweb="tab-highlight"]{ background:var(--brand) !important; height:2.5px !important; }
[data-baseweb="tab-list"] button[data-baseweb="tab"]:first-child{
  background:var(--brand-soft) !important; border-radius:9px 9px 0 0 !important; }
[data-baseweb="tab-list"] button[data-baseweb="tab"]:first-child p{ color:var(--brand) !important; }
/* 스크롤해도 상단 탭 고정 — baseweb이 tab-list를 짧은 래퍼(높이 51px)로 감싸 sticky가
   그 안에 갇히므로, 높은 Root의 직속 자식 래퍼(=tab-list 감싼 div)를 sticky로. 카드 안 내부탭은 제외. */
[data-testid="stMain"] [data-testid="stTabs"] > div > div:has(> [data-baseweb="tab-list"]){
  position:sticky !important; top:0 !important; z-index:50 !important;
  background:var(--bg) !important; padding-top:8px !important;
  box-shadow:0 6px 10px -7px rgba(20,28,45,.18) !important; }
[data-testid="stMain"] [class*="st-key-scard-"] [data-testid="stTabs"] > div > div:has(> [data-baseweb="tab-list"]){
  position:static !important; padding-top:0 !important; background:transparent !important; box-shadow:none !important; }

/* 인라인 필터바 (시안 칩 느낌) */
[data-testid="stPopover"] button, [data-testid="stPopoverButton"]{
  border:1px solid var(--border-strong) !important; background:var(--surface-2) !important;
  border-radius:8px !important; font-weight:600 !important; color:var(--text-2) !important;
  font-size:12px !important; min-height:31px !important; height:31px !important;
  padding:2px 10px !important; }
[data-testid="stPopover"] button p, [data-testid="stPopoverButton"] p{
  white-space:nowrap !important; overflow:hidden !important; text-overflow:ellipsis !important; }
/* 필터 라벨(.fbl) — 위젯 위 작은 회색 라벨 */
.fbl{ font-size:11px !important; font-weight:700; color:var(--text-2); margin:0 0 3px 2px; line-height:1.2; }
/* 필터바 팝오버 칩 = 칼럼 폭 꽉·높이 33 */
.st-key-scard-filter [data-testid="stPopover"]{ width:100% !important; }
.st-key-scard-filter [data-testid="stPopover"] button{
  width:100% !important; min-height:33px !important; height:33px !important;
  justify-content:space-between !important; }
/* 팝오버 안 검색+체크리스트 컴팩트(대형 목록은 스크롤) */
[data-testid="stPopover"] [data-testid="stCheckbox"]{ margin-bottom:0 !important; }
[data-testid="stPopover"] [data-testid="stCheckbox"] label{ padding:3px 2px !important; gap:8px !important; align-items:center !important; }
[data-testid="stPopover"] [data-testid="stCheckbox"] label p{ font-size:12.5px !important; }
[data-testid="stPopover"] [data-testid="stTextInput"] input{ font-size:12.5px !important; }
[data-testid="stPopover"] [data-testid="stButton"] button{ font-size:11px !important; padding:2px 6px !important;
  min-height:28px !important; height:28px !important; }
/* 필터바 '적용' 버튼(팝오버 밖) = 칩 높이와 정렬 */
.st-key-scard-filter [data-testid="stButton"] button{ min-height:33px !important; height:33px !important;
  font-size:12px !important; font-weight:700 !important; border-radius:8px !important; }
/* 필터바: 라벨 붙은 컴팩트 위젯 — 간격 좁게·바닥정렬·높이 통일(34) */
.st-key-scard-filter [data-testid="stHorizontalBlock"]{ align-items:flex-end !important; gap:0.5rem !important; }
.st-key-scard-filter [data-testid="stPopover"] button,
.st-key-scard-filter [data-testid="stDateInput"] div[data-baseweb="input"],
.st-key-scard-filter [data-testid="stButton"] button{ height:34px !important; min-height:34px !important; }
.st-key-scard-filter [data-testid="stColumn"]{ display:block !important; }
.st-key-scard-filter label{
  font-size:11px !important; font-weight:700 !important; color:var(--text-2) !important;
  margin:0 0 3px 2px !important; padding:0 !important; min-height:0 !important; line-height:1.2 !important; }
/* 각 위젯이 칼럼 폭을 꽉 채우게(전역 max-width:240 해제) */
.st-key-scard-filter [data-testid="stSelectbox"],
.st-key-scard-filter [data-testid="stMultiSelect"],
.st-key-scard-filter [data-testid="stDateInput"]{ max-width:none !important; width:100% !important; }
.st-key-scard-filter [data-testid="stElementContainer"]:has(> [data-testid="stSelectbox"]){
  justify-content:stretch !important; }
/* 날짜·멀티셀렉트도 셀렉트와 동일한 컴팩트 회색 톤(높이 33) */
.st-key-scard-filter [data-testid="stDateInput"] div[data-baseweb="input"]{
  min-height:33px !important; height:33px !important; border-radius:8px !important;
  background:var(--surface-2) !important; border:1px solid var(--border-strong) !important; }
.st-key-scard-filter [data-testid="stDateInput"] input{
  font-size:12px !important; font-weight:600 !important; color:var(--text-2) !important; }
.st-key-scard-filter [data-testid="stMultiSelect"] div[data-baseweb="select"]{
  min-height:33px !important; background:var(--surface-2) !important;
  border:1px solid var(--border-strong) !important; border-radius:8px !important; }
.st-key-scard-filter [data-testid="stMultiSelect"] div[data-baseweb="select"] *{ font-size:12px !important; }

/* 세그먼트 컨트롤(월/주/일) = 시안 .seg */
[data-testid="stButtonGroup"]{
  display:inline-flex !important; gap:2px !important; background:var(--surface-3) !important;
  border-radius:8px !important; padding:2px !important; width:auto !important; }
[data-testid="stButtonGroup"] button{
  border:none !important; background:transparent !important; box-shadow:none !important;
  min-height:0 !important; height:auto !important; padding:4px 12px !important; border-radius:6px !important; }
[data-testid="stButtonGroup"] button p{ font-size:12px !important; font-weight:600 !important; color:var(--text-2) !important; }
[data-testid="stButtonGroup"] button[kind="segmented_controlActive"]{
  background:var(--surface) !important; box-shadow:0 1px 3px rgba(20,28,45,.08) !important; }
[data-testid="stButtonGroup"] button[kind="segmented_controlActive"] p{
  color:var(--brand) !important; font-weight:700 !important; }

/* 셀렉트박스 = 시안 .minisel (컴팩트·글자 세로중앙) */
[data-testid="stSelectbox"]{ max-width:240px !important; }
[data-testid="stSelectbox"] div[data-baseweb="select"] > div:first-child{
  min-height:33px !important; height:33px !important; background:var(--surface-2) !important;
  border:1px solid var(--border-strong) !important; border-radius:8px !important;
  display:flex !important; align-items:center !important; }
[data-testid="stSelectbox"] div[data-baseweb="select"] > div:first-child > div{
  display:flex !important; align-items:center !important; }
[data-testid="stSelectbox"] div[data-baseweb="select"] div{ font-size:12.5px !important; font-weight:600 !important; }
[data-testid="stElementContainer"]:has(> [data-testid="stButtonGroup"]),
[data-testid="stElementContainer"]:has(> [data-testid="stSelectbox"]){
  display:flex !important; justify-content:flex-end !important; }
/* 카드 헤더 드롭다운 = 카드 제목 옆(우상단) 절대배치 */
.st-key-scard-natsel, .st-key-scard-titlesel, .st-key-scard-nattitle, .st-key-scard-storesel{ position:relative; }
.st-key-scard-natsel [data-testid="stElementContainer"]:has(> [data-testid="stSelectbox"]),
.st-key-scard-titlesel [data-testid="stElementContainer"]:has(> [data-testid="stSelectbox"]),
.st-key-scard-nattitle [data-testid="stElementContainer"]:has(> [data-testid="stSelectbox"]),
.st-key-scard-storesel [data-testid="stElementContainer"]:has(> [data-testid="stSelectbox"]){
  position:absolute !important; top:16px !important; right:18px !important; width:auto !important;
  margin:0 !important; z-index:5 !important; }
.st-key-scard-natsel [data-testid="stSelectbox"], .st-key-scard-titlesel [data-testid="stSelectbox"],
.st-key-scard-nattitle [data-testid="stSelectbox"], .st-key-scard-storesel [data-testid="stSelectbox"]{
  width:auto !important; min-width:0 !important; }
.st-key-scard-natsel [data-testid="stSelectbox"] div[data-baseweb="select"],
.st-key-scard-titlesel [data-testid="stSelectbox"] div[data-baseweb="select"],
.st-key-scard-nattitle [data-testid="stSelectbox"] div[data-baseweb="select"],
.st-key-scard-storesel [data-testid="stSelectbox"] div[data-baseweb="select"]{
  width:fit-content !important; min-width:110px !important; }

/* ── 사이드바 '관리자 전용' 카드 ── */
[data-testid="stSidebar"] .st-key-sb-admin{
  background:#f6f7ff !important; border:1px solid #e4e7fb !important; border-radius:12px !important;
  padding:11px 12px 7px !important; margin-top:10px !important;
  box-shadow:0 1px 2px rgba(79,70,229,.05) !important; }
.sb-admin-hd{ font-size:10.5px; font-weight:800; letter-spacing:.04em; color:var(--brand);
  text-transform:uppercase; margin:0 0 8px 1px; display:flex; align-items:center; gap:5px; }
.st-key-sb-admin [data-testid="stCheckbox"]{ margin-bottom:2px; }
.st-key-sb-admin [data-testid="stCheckbox"] label{ font-size:12.5px !important; font-weight:600 !important; }
.st-key-sb-admin [data-testid="stExpander"]{ border:none !important; background:transparent !important; box-shadow:none !important; }
.st-key-sb-admin [data-testid="stExpander"] details{ border:none !important; background:transparent !important; }
.st-key-sb-admin [data-testid="stExpander"] summary{ padding:4px 2px !important; font-size:12.5px !important; font-weight:600 !important; }

/* ══ 모바일(폰) 최적화 — 좁은 화면에서 표·카드·차트가 깨지지 않게 ══ */
@media (max-width:720px){
  [data-testid="stMainBlockContainer"], .block-container{
    padding-left:.7rem !important; padding-right:.7rem !important; padding-top:.7rem !important; }
  h1{ font-size:20px !important; }
  .kpis{ grid-template-columns:1fr !important; gap:8px; }
  .kpi.hero .v{ font-size:26px; } .kpi .v{ font-size:20px; }
  [data-testid="stMain"] [class*="st-key-scard-"]{ padding:12px 12px !important; }
  .sect{ font-size:16px !important; } .secn{ width:20px; height:20px; }
  .secq{ margin-left:0 !important; }
  [data-testid="stMarkdownContainer"]:has(.ntbl){ overflow-x:auto; -webkit-overflow-scrolling:touch; }
  .ntbl{ min-width:640px; }
  .hb{ grid-template-columns:92px 1fr 82px !important; gap:8px !important; }
  .hb-n, .hb-v{ font-size:12px !important; }
  .donut-wrap{ flex-direction:column; align-items:flex-start; gap:12px; }
  .leg2{ width:100%; }
  .chart{ height:168px; }
  [data-baseweb="tab-list"]{ overflow-x:auto; overflow-y:hidden; }
  button[data-baseweb="tab"]{ padding:8px 10px !important; }
  button[data-baseweb="tab"] p{ font-size:12.5px !important; }
  .scope{ font-size:11.5px; }
}
</style>
""", unsafe_allow_html=True)

BASE_DIR     = Path(__file__).parent.parent
AGG_FILE     = BASE_DIR / "data" / "master_photoism_agg.parquet"
HOURLY_FILE  = BASE_DIR / "data" / "master_photoism_hourly.parquet"
PARQUET_FILE = BASE_DIR / "data" / "master_photoism.parquet"
MASTER_FILE  = BASE_DIR / "data" / "master_photoism.csv"
CONFIG_FILE  = BASE_DIR / "config.json"
DEVICE_FILE  = BASE_DIR / "data" / "devices.parquet"   # 장비관리 CMS(device_ingest.py)

# 국가별 매출액 가산 규칙 (쿠폰/서비스코인 포함 국가)
# ★정의는 photoism_rules.py 한 곳에 둔다 — 런 비교 페이지도 같은 값을 써야
#   두 화면의 매출이 어긋나지 않는다.
_COUPON_CC = photoism_rules.COUPON_CC
_COIN_CC   = photoism_rules.COIN_CC

# 국가명 → ISO alpha-2 (국기 이미지용, 30개국 대응)
COUNTRY_ISO = {
    "대한민국": "kr", "한국": "kr", "일본": "jp", "중국": "cn", "대만": "tw",
    "인도네시아": "id", "홍콩": "hk", "태국": "th", "말레이시아": "my",
    "미국": "us", "베트남": "vn", "필리핀": "ph", "싱가포르": "sg", "괌": "gu",
    "캐나다": "ca", "호주": "au", "독일": "de", "프랑스": "fr", "영국": "gb",
    "스페인": "es", "네덜란드": "nl", "멕시코": "mx", "페루": "pe", "칠레": "cl",
    "라오스": "la", "몽골": "mn", "마카오": "mo", "아랍에미리트": "ae", "아랍": "ae",
    "룩셈부르크": "lu", "브루나이": "bn", "라트비아": "lv",
}

# 팔레트 (스내피즘과 동일 인디고 시스템)
PAL = ["#6366f1", "#b45309", "#0f9d77", "#d24d8b", "#38a3e8", "#7c77ee", "#c98a2e", "#5f6b7a"]
BRAND, BRAND2, SKY, TEAL, AMBER, PINK = "#4f46e5", "#6366f1", "#38a3e8", "#0f9d77", "#b45309", "#d24d8b"
_GUB_COLORS = {"아티스트": BRAND2, "캐릭터": TEAL, "렌탈": SKY, "PICK": PINK, "기획(P)": AMBER}
_GUB_EMOJI  = {"아티스트": "🎤", "캐릭터": "🧸", "렌탈": "🏪", "PICK": "⭐", "기획(P)": "🎨"}


def flag_url(name):
    iso = COUNTRY_ISO.get(str(name).strip())
    return f"https://flagcdn.com/40x30/{iso}.png" if iso else ""


def flag_img(name, h=13):
    u = flag_url(name)
    return (f'<img src="{u}" height="{h}" '
            f'style="vertical-align:middle;margin-right:7px;border:1px solid #eee;border-radius:2px;">'
            if u else "")


CURRENCY_SYMBOLS = {
    "KRW": "₩", "CNY": "¥", "JPY": "¥", "IDR": "Rp", "TWD": "NT$", "THB": "฿",
    "HKD": "HK$", "MYR": "RM", "USD": "$", "EUR": "€", "GBP": "£", "VND": "₫",
    "PHP": "₱", "SGD": "S$", "AUD": "A$", "CAD": "C$", "AED": "AED", "MXN": "$",
    "PEN": "S/", "CLP": "$", "LAK": "₭", "MNT": "₮", "MOP": "MOP$", "BND": "B$",
}


def fmt_orig(amount, currency):
    sym = CURRENCY_SYMBOLS.get(str(currency).strip(), str(currency) + " ")
    return f"{sym}{int(amount):,}"


def josa(word, with_jong, without_jong):
    """한글 조사 자동 선택 — '일본예요/대한민국는' 같은 어색한 표기 방지.
    받침이 있으면 with_jong, 없으면 without_jong."""
    w = str(word).strip()
    if not w:
        return without_jong
    ch = w[-1]
    if not ("가" <= ch <= "힣"):
        return without_jong          # 영문·숫자로 끝나면 받침 없는 쪽이 대체로 자연스럽다
    return with_jong if (ord(ch) - 0xAC00) % 28 else without_jong


def fmt_krw(n):
    return f"₩{int(n):,}"


def load_config():
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def load_exchange_rates():
    return load_config().get("exchange_rates", {"KRW": 1})


def _file_mtime(p):
    try:
        return p.stat().st_mtime
    except Exception:
        return 0.0


# max_entries=1 — 반환 DataFrame 이 370만행·314MB 다. 캐시 키가 파일 mtime 이라
# 파일이 바뀌면 옛 항목은 쓸모없는데, 상한이 없으면 그대로 메모리에 남아 쌓인다.
@st.cache_data(ttl=1800, show_spinner=False, max_entries=1)
def _load_data(_agg_mtime, _cfg_mtime):
    """집계 parquet 로드 (category 인코딩). 캐시 키 = 집계·환율 파일 mtime →
    파일이 바뀔 때만 재계산(매일 ingest/환율 갱신 시). 평소엔 즉시 캐시 히트."""
    if AGG_FILE.exists():
        try:
            table = pq.read_table(str(AGG_FILE))
            df = table.to_pandas(strings_to_categorical=True)
            # ★노출 대상만 남기고 캐시한다. @st.cache_data 는 반환값을 피클로 직렬화하는데,
            #   373만행 전체(314MB)를 넘기면 그 직렬화에서 MemoryError 가 났다(실측).
            #   어차피 화면엔 아티스트·캐릭터·PICK 만 쓰므로 여기서 거르면 행이 절반 이하가 된다.
            #   (원본 parquet 은 그대로 — 되살리려면 IP_GUBUN_SHOWN 만 고치면 된다)
            if "IP구분" in df.columns:
                df = df[df["IP구분"].isin(ip_classify.IP_GUBUN_SHOWN)]
        except Exception as e:
            st.warning(f"집계 파일을 불러오지 못했어요. 파일을 다시 만든 뒤 새로고침해 주세요. (원인: {e})")
            return pd.DataFrame()
    else:
        st.error("집계 데이터가 아직 없어요. 아래 명령으로 집계 파일을 먼저 만들어 주세요.")
        return pd.DataFrame()

    df["날짜"] = pd.to_datetime(df["날짜"], errors="coerce").dt.date
    df = df[df["날짜"].notna()]
    df["취소 여부"] = df["취소 여부"].astype(bool)
    # int32 로 낮춘다 — 219만행 × 4컬럼이 int64면 67MB, int32면 33MB.
    # 캐시는 이 프레임을 피클로 들고 있어서 메모리 압박이 곧 OOM 으로 이어진다.
    # 현지통화 최댓값(VND 수백만)도 int32 상한(21.4억)에 한참 못 미친다.
    for col in ["건수", "최종 결제 금액", "쿠폰 할인 금액", "서비스코인"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype("int32")
        else:
            df[col] = 0

    ex = load_exchange_rates()
    # ⚡ 결제단위·국가코드는 categorical(고유값 24/30개)이라 3.5M행 문자열 변환(.astype(str).str.…)이
    #    로드의 최대 병목(≈2.6s). '카테고리 단위'로 환율·가산대상을 계산해 7배 가속(결과 동일 검증).
    _unit = df["결제 단위"] if "결제 단위" in df.columns else None
    if _unit is not None and hasattr(_unit, "cat"):
        _rate_map = {c: ex.get(str(c).strip(), 1) for c in _unit.cat.categories}
        df["환율"] = _unit.map(_rate_map).astype(float).fillna(1.0)
    elif _unit is not None:
        df["환율"] = _unit.astype(str).str.strip().map(ex).fillna(1.0)
    else:
        df["결제 단위"] = "KRW"
        df["환율"] = 1.0
    # 파생 금액도 int32 — 원화 환산액은 집계 한 행 기준 수천만 원 수준이라 상한(21.4억)에 여유가 크다.
    df["KRW환산금액"] = (df["최종 결제 금액"] * df["환율"]).round(0).astype("int32")
    df["쿠폰KRW"]    = (df["쿠폰 할인 금액"] * df["환율"]).round(0).astype("int32")
    df["정산금액"]   = (df["KRW환산금액"] + df["쿠폰KRW"]).astype("int32")
    df["서비스코인KRW"] = (df["서비스코인"] * df["환율"]).round(0).astype("int32")

    # 쿠폰·코인 가산대상(지정 국가) — 카테고리만 검사해 3.5M 문자열 변환 회피
    if "국가코드" in df.columns:
        _codes = df["국가코드"]
        if hasattr(_codes, "cat"):
            _coup = [c for c in _codes.cat.categories if str(c).lower().strip() in _COUPON_CC]
            _coin = [c for c in _codes.cat.categories if str(c).lower().strip() in _COIN_CC]
            _is_coup = _codes.isin(_coup).to_numpy()
            _is_coin = _codes.isin(_coin).to_numpy()
        else:
            _cc = _codes.astype(str).str.lower().str.strip()
            _is_coup = _cc.isin(_COUPON_CC).to_numpy()
            _is_coin = _cc.isin(_COIN_CC).to_numpy()
    else:
        _is_coup = _is_coin = False
    # 매출 구성: 실결제(순수) + 쿠폰기여 + 코인기여 (지정 국가만 쿠폰·코인 가산)
    df["쿠폰기여"] = (df["쿠폰KRW"]       * _is_coup).astype("int32")
    df["코인기여"] = (df["서비스코인KRW"] * _is_coin).astype("int32")
    df["매출액"]   = (df["KRW환산금액"] + df["쿠폰기여"] + df["코인기여"]).astype("int32")
    return df


def load_data():
    return _load_data(_file_mtime(AGG_FILE), _file_mtime(CONFIG_FILE))


@st.cache_data(ttl=1800, show_spinner=False, max_entries=1)   # mtime 키 → 최신 1개만 유효
def _sidebar_options(_agg_mtime):
    """필터 드롭다운 옵션을 데이터 버전당 한 번만 계산(캐시)."""
    d = _load_data(_file_mtime(AGG_FILE), _file_mtime(CONFIG_FILE))
    if d.empty:
        return {"countries": [], "stores": [], "brands": [], "ip_by_gubun": {"_ALL": []}}

    def uniq(col, drop_empty=False):
        vals = sorted(str(v) for v in d[col].dropna().unique())
        return [v for v in vals if v not in ("", "nan")] if drop_empty else vals

    # 노출 대상 구분만 — 기획(P)·렌탈·제외의 IP명이 필터 목록에 남지 않게.
    nonex = d[d["IP구분"].isin(ip_classify.IP_GUBUN_SHOWN)]

    def ip_list(frame):
        return sorted(
            v for v in (str(x) for x in frame["IP명"].dropna().unique())
            if v.strip() and v not in ("nan", "")
        )

    ipmap = {"_ALL": ip_list(nonex)}
    for g in ip_classify.IP_GUBUN_ORDER:
        ipmap[g] = ip_list(nonex[nonex["IP구분"] == g])

    # 국가 → 매장 목록 (매장 필터를 선택 국가로 좁히기용)
    sbc = {}
    for c, grp in d.groupby("국가", observed=True):
        vals = sorted(str(v) for v in grp["매장 이름"].dropna().unique())
        sbc[str(c)] = [v for v in vals if v not in ("", "nan")]

    return {
        "countries": uniq("국가"),
        "stores": uniq("매장 이름"),
        "stores_by_country": sbc,
        "brands": uniq("브랜드", drop_empty=True),
        "ip_by_gubun": ipmap,
    }


@st.cache_data(ttl=1800, show_spinner=False, max_entries=1)   # mtime 키 → 최신 1개만 유효
def _load_hourly(_mtime):
    """시간대 집계 parquet 로드 (시간대 차트 전용). 캐시 키 = 파일 mtime."""
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


def load_hourly():
    return _load_hourly(_file_mtime(HOURLY_FILE))


# ── 장비(키오스크) ─────────────────────────────────────────────
# 대당 매출의 분모. 장비관리 CMS 에는 설치일 컬럼이 없어 기기 S/N 앞 6자리(YYMMDD)를
# 설치일로 쓴다(device_ingest.py). 철거일은 아예 없고 '중지' 여부만 있다 — 그래서
# 언제 멈췄는지 모르는 중지 장비는 분모에서 뺀다(아래 device_days 주석 참고).
@st.cache_data(ttl=1800, show_spinner=False, max_entries=1)
def _load_devices(_mtime):
    if not DEVICE_FILE.exists():
        return pd.DataFrame()
    try:
        d = pd.read_parquet(DEVICE_FILE, columns=["국가코드", "가동중", "테스트장비", "렌탈",
                                                  "설치일", "지점명", "부스번호"])
        # 가동중은 남겨둔다 — 대당 매출 분모엔 가동 장비만 쓰지만, 이력 표에는
        # '중지 N대'도 같이 보여줘야 숫자를 읽는 사람이 배경을 알 수 있다.
        d = d[~d["테스트장비"] & ~d["렌탈"]].copy()
        d["국가코드"] = d["국가코드"].astype(str).str.lower().str.strip()
        d["설치일"] = pd.to_datetime(d["설치일"], errors="coerce")
        return d.reset_index(drop=True)
    except Exception:
        return pd.DataFrame()


def load_devices():
    return _load_devices(_file_mtime(DEVICE_FILE))


def device_days(dev, p0, p1):
    """국가코드별 '대·일'(가동 키오스크 × 가동일수)과 대수를 구한다.

    한 대가 기간 내내 있었으면 기간 전체 일수, 중간에 설치됐으면 설치일부터만 센다.
    ★설치일을 무시하고 대수로만 나누면, 최근 증설한 국가가 실제보다 낮게 나온다.
    설치일 미상(19대)은 기간 시작 전부터 있던 것으로 본다."""
    if dev.empty or not p0 or not p1:
        return pd.DataFrame(columns=["국가코드", "대수", "대일", "신규", "중지"])
    s0, s1 = pd.Timestamp(p0), pd.Timestamp(p1)
    act = dev[dev["가동중"]]
    inst = act["설치일"].fillna(s0).clip(lower=s0)
    days = (s1 - inst).dt.days + 1
    t = pd.DataFrame({"국가코드": act["국가코드"], "대일": days.clip(lower=0),
                      "신규": act["설치일"].between(s0, s1).astype(int)})
    t = t[t["대일"] > 0]
    g = (t.groupby("국가코드").agg(대수=("대일", "size"), 대일=("대일", "sum"),
                                   신규=("신규", "sum")).reset_index())
    stop = (dev[~dev["가동중"]].groupby("국가코드").size().rename("중지").reset_index())
    return g.merge(stop, on="국가코드", how="left").fillna({"중지": 0})


# 세부 항목 분류 기준 화이트리스트 (UI 라벨 → 실제 컬럼/파생키)
DETAIL_DIMS = {
    "타이틀 (날짜+IP·한영통합)": "타이틀",
    "IP명 (날짜 합산·한영통합)": "IP명",
    "IP 구분 (아티스트/캐릭터/…)": "IP구분",
    "프레임 이름": "프레임 이름",
    "구좌 (BASIC/WITH/EVENT)": "구좌",
    "타이틀 (원본 그대로)": "타이틀명",
    "타이틀 (이름+단가별)": "타이틀_단가",
    "상품 카테고리 (브랜드)": "브랜드",
    "채널 (중분류)": "중분류",
    "사업형태 (소분류)": "소분류",
}

# 전체 parquet에는 타이틀/IP구분/IP명 컬럼이 없으므로 분류식을 직접 주입 (ip_classify 공용)
_DETAIL_EXPR = {
    "타이틀": ip_classify.IP_TITLE_RAW_SQL,
    "IP명":  ip_classify.IP_NAMECORE_SQL,
    "IP구분": ip_classify.IP_GUBUN_SQL,
    "타이틀_단가": (
        "CONCAT("
        "COALESCE(NULLIF(TRIM(CAST(\"타이틀명\" AS VARCHAR)), ''), '(타이틀명 없음)'),"
        "' · ',"
        "CAST(CAST(ROUND(COALESCE(TRY_CAST(\"상품 단가\" AS DOUBLE), 0)) AS BIGINT) AS VARCHAR),"
        "' ', COALESCE(NULLIF(TRIM(CAST(\"결제 단위\" AS VARCHAR)), ''), 'KRW')"
        ")"
    ),
}


# max_entries=32 — 파라미터가 7개라 필터 조합마다 새 항목이 생긴다.
# 상한이 없으면 사용자가 필터를 만질수록(여러 명이면 곱으로) 무한정 쌓인다.
@st.cache_data(ttl=60, max_entries=32)
def load_sales_detail(group_col, start_date, end_date, ip_list=None,
                      countries=(), stores=(), brands=(), gubuns=()):
    """전체 parquet에서 세부 판매 항목(IP명/프레임/테마 등) DuckDB on-demand 집계.
    countries/stores/brands 는 다중선택 리스트(빈 값=전체)."""
    if group_col not in DETAIL_DIMS.values() or not PARQUET_FILE.exists():
        return pd.DataFrame()
    try:
        import duckdb
    except Exception:
        return pd.DataFrame()
    parq = str(PARQUET_FILE).replace("\\", "/")

    def esc(v):
        return str(v).replace("'", "''")

    def _in_clause(colexpr, vals):
        if not vals:
            return None
        inner = ",".join("'" + esc(v) + "'" for v in vals)
        return f"CAST({colexpr} AS VARCHAR) IN ({inner})"

    group_expr = _DETAIL_EXPR.get(group_col, f'"{group_col}"')

    _need_ip = bool(ip_list) and group_col not in ("타이틀", "타이틀_단가", "IP명")
    _ipname_sel = f', ({ip_classify.IP_NAMECORE_SQL}) AS "_ipname"' if _need_ip else ""
    _ipname_grp = ", 4" if _need_ip else ""

    where = [
        f"TRY_CAST(\"날짜\" AS DATE) BETWEEN DATE '{start_date}' AND DATE '{end_date}'",
        "LOWER(CAST(\"취소 여부\" AS VARCHAR)) NOT IN ('true','1','yes')",
        "TRY_CAST(\"최종 결제 금액\" AS DOUBLE) >= 0",
    ]
    for _c in (_in_clause('"국가"', countries), _in_clause('"매장 이름"', stores),
               _in_clause('"브랜드"', brands)):
        if _c:
            where.append(_c)
    # 세부검색은 원본 parquet 을 DuckDB 로 직접 읽으므로 df_all 의 노출 필터가
    # 적용되지 않는다. 여기서도 같은 조건을 걸어야 다른 카드와 숫자가 맞는다.
    _shown = gubuns if gubuns else ip_classify.IP_GUBUN_SHOWN
    _g_in = ",".join("'" + esc(g) + "'" for g in _shown)
    where.append(f"({ip_classify.IP_GUBUN_SQL}) IN ({_g_in})")
    where_sql = " AND ".join(where)

    con = duckdb.connect()
    # 1,385만행 parquet 을 스캔하므로 상한 없이 두면 쿼리 하나가 메모리를 크게 가져간다.
    # (build_photoism_agg.py 와 같은 방식 — 넘치면 OOM 대신 디스크로 스필)
    try:
        con.execute("PRAGMA memory_limit='512MB'")
        con.execute("PRAGMA threads=2")
        con.execute("PRAGMA preserve_insertion_order=false")
    except Exception:
        pass       # 옛 DuckDB 라 PRAGMA 가 없어도 쿼리는 그대로 진행
    try:
        df = con.execute(f"""
            SELECT
                COALESCE(CAST(({group_expr}) AS VARCHAR), '') AS "항목",
                COALESCE(CAST("결제 단위" AS VARCHAR), 'KRW') AS "결제 단위",
                LOWER(COALESCE(CAST("국가코드" AS VARCHAR), '')) AS "국가코드"{_ipname_sel},
                SUM(TRY_CAST("최종 결제 금액" AS DOUBLE)) AS "최종 결제 금액",
                SUM(TRY_CAST("쿠폰 할인 금액" AS DOUBLE)) AS "쿠폰 할인 금액",
                SUM(CASE WHEN TRY_CAST("서비스코인" AS DOUBLE) > TRY_CAST("상품총액" AS DOUBLE)
                              AND TRY_CAST("상품총액" AS DOUBLE) > 0
                         THEN TRY_CAST("상품총액" AS DOUBLE)
                         ELSE COALESCE(TRY_CAST("서비스코인" AS DOUBLE), 0) END) AS "서비스코인",
                COUNT(*) AS "건수",
                SUM(CASE WHEN TRY_CAST("서비스코인" AS DOUBLE) > 0 THEN 1 ELSE 0 END) AS "코인건"
            FROM read_parquet('{parq}')
            WHERE {where_sql}
            GROUP BY 1, 2, 3{_ipname_grp}
        """).df()
    finally:
        con.close()

    if df.empty:
        return df

    if group_col == "타이틀":
        df["항목"] = ip_classify.apply_alias_title(df["항목"].astype(str))
    elif group_col == "IP명":
        df["항목"] = ip_classify.apply_alias(df["항목"].astype(str))
    if ip_list:
        ipset = set(str(x) for x in ip_list)
        if group_col in ("타이틀", "타이틀_단가"):
            df = df[df["항목"].astype(str).apply(
                lambda t: any(name in t for name in ipset))]
        elif group_col == "IP명":
            df = df[df["항목"].astype(str).isin(ipset)]
        else:
            _ipn = ip_classify.apply_alias(df["_ipname"].astype(str))
            df = df[_ipn.isin(ipset)]
        if df.empty:
            return df

    ex = load_exchange_rates()
    df["결제 단위"] = df["결제 단위"].astype(str).str.strip().replace("nan", "KRW")
    df["환율"] = df["결제 단위"].map(ex).fillna(1)
    for c in ["최종 결제 금액", "쿠폰 할인 금액", "서비스코인", "건수", "코인건"]:
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)
    df["KRW_순수"] = (df["최종 결제 금액"] * df["환율"]).round(0)
    df["KRW_쿠폰"] = (df["쿠폰 할인 금액"] * df["환율"]).round(0)
    df["KRW_코인"] = (df["서비스코인"]     * df["환율"]).round(0)
    cc = df["국가코드"].astype(str).str.lower().str.strip()
    df["매출액"] = (
        df["KRW_순수"]
        + df["KRW_쿠폰"] * cc.isin(_COUPON_CC).astype(int)
        + df["KRW_코인"] * cc.isin(_COIN_CC).astype(int)
    )
    out = (
        df.groupby("항목", as_index=False)
        .agg(매출=("매출액", "sum"), 건수=("건수", "sum"), 코인건=("코인건", "sum"))
    )
    out = out[out["항목"].astype(str).str.strip() != ""]
    out["매출"] = out["매출"].astype("int64")
    out["건수"] = out["건수"].astype("int64")
    out["코인건"] = out["코인건"].astype("int64")
    return out.sort_values("매출", ascending=False).reset_index(drop=True)


def paid_sales(df):
    return df[~df["취소 여부"] & (df["최종 결제 금액"] >= 0)]


def tx_count(df):
    return int(df["건수"].sum()) if "건수" in df.columns else len(df)


# ══════════════════════════════════════════════════════════════
#  표현 헬퍼 (스내피즘 시안형)
# ══════════════════════════════════════════════════════════════
def pct_bar(frac, maxfrac=1.0):
    w = 0 if maxfrac <= 0 else min(100, max(2, frac / maxfrac * 100))
    return (f'<div class="npct"><div class="npct-bar"><i style="width:{w:.0f}%"></i></div>'
            f'<span class="p">{frac * 100:.1f}%</span></div>')


def sec(n, title, q=""):
    st.markdown(f'<div class="sechd"><span class="secn">{n}</span><span class="sect">{title}</span></div>'
                + (f'<div class="secq">{q}</div>' if q else ""), unsafe_allow_html=True)


_CARDN = [0]


@contextmanager
def card(title=None, key=None):
    if key is None:
        _CARDN[0] += 1
        key = f"scard-{_CARDN[0]}"
    c = st.container(border=True, key=key)
    if title:
        c.markdown(f'<div class="ct">{title}</div>', unsafe_allow_html=True)
    with c:
        yield


def statrow(items):
    """미니 지표 3~4칸. items=[(label, value)]."""
    cells = "".join(
        f'<div class="mst"><div class="mst-l">{l}</div><div class="mst-v num">{v}</div></div>'
        for l, v in items)
    st.markdown(f'<div class="mstrow">{cells}</div>', unsafe_allow_html=True)


def css_donut(pairs, colors, size=128, hole=38, legend_fs=13, sub=None):
    """시안 CSS conic-gradient 도넛 + 오른쪽 범례. pairs=[(name, value)]."""
    total = sum(v for _, v in pairs) or 1
    segs, acc = [], 0.0
    for i, (_, v) in enumerate(pairs):
        f0 = acc / total * 100
        acc += v
        segs.append(f"{colors[i % len(colors)]} {f0:.2f}% {acc / total * 100:.2f}%")
    grad = "conic-gradient(" + ",".join(segs) + ")"
    mask = f"radial-gradient(circle {hole}px at center,transparent 98%,#000 100%)"
    rows = ""
    for i, (name, v) in enumerate(pairs):
        rows += (f'<div class="row tip" data-tip="{name} · {fmt_krw(v)} ({v / total * 100:.1f}%)">'
                 f'<i class="dot" style="background:{colors[i % len(colors)]}"></i>'
                 f'{name} <b>{v / total * 100:.1f}%</b></div>')
    if sub:
        rows += f'<div class="row sub">{sub}</div>'
    st.markdown(
        f'<div class="donut-wrap"><div class="donut" style="width:{size}px;height:{size}px;'
        f'-webkit-mask:{mask};mask:{mask};background:{grad}"></div>'
        f'<div class="leg2" style="font-size:{legend_fs}px">{rows}</div></div>',
        unsafe_allow_html=True)


def css_stack(labels, data, series, gran):
    """시안 CSS 스택 막대 추이 (IP구분별 다중 시리즈).
    labels=x축, data={시리즈:[값...]} labels 순서, series=그릴 순서."""
    if not labels:
        st.info("선택한 조건에 맞는 데이터가 없어요. 기간·구분을 바꿔 보세요.")
        return
    n = len(labels)
    totals = [sum(data[s][i] for s in series) for i in range(n)]
    mx = max(totals) or 1
    gap = "6px" if gran == "일" else ("12px" if gran == "주" else "24px")
    fs = "10px" if gran == "일" else "11px"
    leg = "".join(f'<span><i class="dot" style="background:{_GUB_COLORS.get(s, "#888")}"></i>{s}</span>'
                  for s in series)
    cols = ""
    for i, lab in enumerate(labels):
        tot = totals[i]
        h = max(2, round(tot / mx * 100))
        seg = ""
        for s in series:
            v = data[s][i]
            sp = round(v / tot * 100) if tot else 0
            if sp > 0:
                seg += (f'<div style="height:{sp}%;background:{_GUB_COLORS.get(s, "#888")}"></div>')
        _tb = min(h, 80)   # 막대가 아주 높으면 툴팁이 카드 밖으로 나가지 않게 상한
        _parts = " · ".join(f'{s} {fmt_krw(data[s][i])}' for s in series if data[s][i] > 0)
        _tip = f'{lab} · 합계 {fmt_krw(tot)}' + (f' · {_parts}' if _parts else '')
        cols += (f'<div class="col"><div class="vtip" style="bottom:{_tb}%">{_tip}</div>'
                 f'<div class="stack" style="height:{h}%">{seg}</div>'
                 f'<div class="xlab" style="font-size:{fs}">{lab}</div></div>')
    st.markdown(f'<div class="legend">{leg}</div><div class="chart" style="gap:{gap}">{cols}</div>',
                unsafe_allow_html=True)


def css_series(rows, color=PINK, gran="일"):
    """단일 시리즈 막대(선택 IP 일별 등). rows=[(label, value)]."""
    if not rows:
        st.info("선택한 조건에 맞는 데이터가 없어요.")
        return
    mx = max(v for _, v in rows) or 1
    gap = "5px" if gran == "일" else ("10px" if gran == "주" else "22px")
    fs = "9.5px" if len(rows) > 20 else "11px"
    cols = ""
    for lab, v in rows:
        h = max(2, round(v / mx * 100))
        _tb = min(h, 80)
        cols += (f'<div class="col"><div class="vtip" style="bottom:{_tb}%">{lab} · {fmt_krw(v)}</div>'
                 f'<div class="stack" style="height:{h}%;background:{color}"></div>'
                 f'<div class="xlab" style="font-size:{fs}">{lab}</div></div>')
    st.markdown(f'<div class="chart" style="gap:{gap}">{cols}</div>', unsafe_allow_html=True)


def css_hours(vals):
    """시간대(00~23) 막대. 최고 시간대만 진하게. vals=길이24."""
    mx = max(vals) or 1
    cols = ""
    for h, v in enumerate(vals):
        hp = round(v / mx * 100)
        col = "var(--brand)" if (v >= mx and mx > 0) else "var(--brand-2)"
        _tb = min(hp, 80)
        cols += (f'<div class="hc"><div class="vtip" style="bottom:{_tb}%">{h:02d}:00 · {fmt_krw(v)}</div>'
                 f'<div class="hb2" style="height:{hp}%;background:{col}"></div>'
                 f'<div class="hx">{h:02d}</div></div>')
    st.markdown(f'<div class="hours">{cols}</div>', unsafe_allow_html=True)


def hbar_list(dframe, name_col, top=None, collapse_after=None):
    """시안 TOP 가로막대(이름 | 트랙+채움 | 금액). 1위=브랜드색, 나머지=연한 블루."""
    d = dframe.sort_values("매출", ascending=False).reset_index(drop=True)
    if top:
        d = d.head(top)
    mx = d["매출"].max() or 1

    def _rows(sub):
        h = '<div class="hb-wrap">'
        for i, r in sub.iterrows():
            w = max(3, r["매출"] / mx * 100)
            col = BRAND if i == 0 else "#a9c7ef"
            _t = f'{r[name_col]} · {fmt_krw(r["매출"])}'
            if "건수" in sub.columns:
                _t += f' · {int(r["건수"]):,}건'
            h += (f'<div class="hb tip" data-tip="{_t}"><span class="hb-n">{r[name_col]}</span>'
                  f'<span class="hb-track"><i style="width:{w:.0f}%;background:{col}"></i></span>'
                  f'<span class="hb-v">{fmt_krw(r["매출"])}</span></div>')
        return h + '</div>'

    if collapse_after and len(d) > collapse_after:
        st.markdown(_rows(d.iloc[:collapse_after]), unsafe_allow_html=True)
        with st.expander(f"나머지 {len(d) - collapse_after:,}개 더보기  ·  {collapse_after + 1}~{len(d):,}위"):
            st.markdown(_rows(d.iloc[collapse_after:]), unsafe_allow_html=True)
    else:
        st.markdown(_rows(d), unsafe_allow_html=True)


_STAT_CLS = {"🔚": "end", "🔴": "warn", "⚠️": "post", "🆕": "new",
             "⏳": "soon", "🟢": "live", "⚪": "unk"}


def _md(dt):
    return f"{dt.month:02d}-{dt.day:02d}" if dt else ""


def rank_table(dframe, name_col, top=None, collapse_after=None, status_map=None):
    """비중막대 내장 순위표(.ntbl). collapse_after=N 이면 상위 N개 + 나머지 접기.
    status_map={이름:{상태,첫거래일,마지막거래일,...}} 를 주면 상태 배지 + 판매기간 칸이 붙는다."""
    d = dframe.sort_values("매출", ascending=False).reset_index(drop=True)
    if top:
        d = d.head(top)
    tot = d["매출"].sum()
    mx = (d["매출"] / tot).max() if tot else 1.0
    has_cnt = "건수" in d.columns
    has_st = bool(status_map)
    if has_st:
        grid = "grid-template-columns:34px 1.75fr 1.2fr .65fr 1.25fr 1.1fr"
        head = (f'<div class="ntr nth" style="{grid}">'
                '<span>#</span><span>이름</span><span class="r">매출</span>'
                '<span class="r">건수</span><span>판매기간</span><span>비중</span></div>')
    elif has_cnt:
        grid = "grid-template-columns:34px 1.7fr 1.3fr .8fr 1.5fr"
        head = (f'<div class="ntr nth" style="{grid}">'
                '<span>#</span><span>이름</span><span class="r">매출</span>'
                '<span class="r">건수</span><span>비중</span></div>')
    else:
        grid = "grid-template-columns:36px 1.7fr 1.2fr 1.5fr"
        head = (f'<div class="ntr nth" style="{grid}">'
                '<span>#</span><span>이름</span><span class="r">매출</span><span>비중</span></div>')

    def _rows(sub):
        h = ""
        for i, r in sub.iterrows():
            frac = (r["매출"] / tot) if tot else 0
            rk = f'<span class="rk {"top" if i == 0 else ""}">{i + 1}</span>'
            cnt = (f'<span class="r num" style="color:var(--text-2)">{int(r["건수"]):,}</span>'
                   if has_cnt else "")
            nm = f'<span class="nname">{r[name_col]}</span>'
            per = ""
            if has_st:
                s = status_map.get(r[name_col]) or {}
                stat = s.get("상태", "")
                if stat:
                    ic, _, tx = stat.partition(" ")
                    nm = (f'<span><span class="nname">{r[name_col]}</span>'
                          f'<span class="tstat {_STAT_CLS.get(ic, "unk")}">{ic} {tx}</span></span>')
                # 종료된 건 끝 날짜를 굵게 — '언제 끝났나'가 급감 해석의 핵심
                _e = _md(s.get("마지막거래일"))
                _e = f"<b>{_e}</b>" if stat.startswith("🔚") else _e
                per = (f'<span class="tper num">{_md(s.get("첫거래일"))} ~ {_e}</span>'
                       if s else '<span class="tper num">—</span>')
            h += (f'<div class="ntr" style="{grid}">{rk}{nm}'
                  f'<span class="r num">{fmt_krw(r["매출"])}</span>{cnt}{per}{pct_bar(frac, mx)}</div>')
        return h

    if collapse_after and len(d) > collapse_after:
        top_d, rest_d = d.iloc[:collapse_after], d.iloc[collapse_after:]
        st.markdown(f'<div class="ntbl">{head}{_rows(top_d)}</div>', unsafe_allow_html=True)
        with st.expander(f"나머지 {len(rest_d):,}개 더보기  ·  {collapse_after + 1}~{len(d):,}위"):
            st.markdown(f'<div class="ntbl">{head}{_rows(rest_d)}</div>', unsafe_allow_html=True)
    else:
        st.markdown(f'<div class="ntbl">{head}{_rows(d)}</div>', unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════
#  데이터 로드
# ══════════════════════════════════════════════════════════════
df_all = load_data()

# 노출 대상 IP구분(아티스트·캐릭터·PICK) 필터는 _load_data 안에서 이미 적용됨.
# 캐시 '이전'에 걸어야 안 쓰는 행까지 직렬화하지 않는다(그러다 MemoryError 가 났었다).

st.title("📸 포토이즘 매출 대시보드")
st.caption("기간·국가·매장·IP를 골라 매출을 봐요. 매출 = 실결제 + 쿠폰 + 서비스코인(지정 국가 가산) 기준이에요.")
render_guide("photoism")

if df_all.empty:
    st.warning("표시할 데이터가 아직 없어요. 아래 명령으로 집계 파일을 먼저 만들어 주세요.")
    st.code("python build_photoism_agg.py")
    st.stop()

last_date  = df_all["날짜"].dropna().max()
first_date = df_all["날짜"].dropna().min()
cfg        = load_config()
ex         = load_exchange_rates()

# ══════════════════════════════════════════════════════════════
#  인라인 필터바 (시안: 흰 카드 안 컴팩트 칩들)
# ══════════════════════════════════════════════════════════════
_opts = _sidebar_options(_file_mtime(AGG_FILE))

# IP 구분(다중선택) — 비우면 노출 대상 전체, 고르면 그 구분만.
# 노출 대상은 IP_GUBUN_SHOWN(아티스트·캐릭터·PICK) 으로 이미 df_all 에서 걸러져 있다.
IP_GUBUN_VIEW = [g for g in ip_classify.IP_GUBUN_ORDER if g in ip_classify.IP_GUBUN_SHOWN]

default_start = max(last_date - timedelta(days=29), first_date)


def cbfilter(col, label, options, key):
    """검색 + 체크박스 다중선택 필터. col 안에 라벨 + 팝오버(검색창·체크리스트).
    ★선택 상태의 단일 출처 = 각 체크박스 위젯(key=…__cb__옵션)★ — 별도 리스트를 두지 않아
    '선택 해제'·필터변경 시 상태 불일치가 없음. 선택 리스트 반환.
    목록을 항상 펼쳐 보여주고(상위 200개), 검색은 좁히는 용도."""
    options = list(options)
    pfx = f"{key}__cb__"

    def _sel():
        return [o for o in options if st.session_state.get(pfx + str(o), False)]

    sel = _sel()
    cap = "전체" if not sel else (str(sel[0]) if len(sel) == 1 else f"{len(sel)}개 선택")
    col.markdown(f'<div class="fbl">{label}</div>', unsafe_allow_html=True)
    with col.popover(cap, use_container_width=True):
        q = ""
        if len(options) > 6:
            q = st.text_input("검색", key=f"{key}__q", placeholder=f"🔍 {label} 검색",
                              label_visibility="collapsed").strip().lower()
        pool = [o for o in options if q in str(o).lower()] if q else list(options)
        merged = list(dict.fromkeys([*sel, *pool]))
        shown = merged[:200]
        over = len(merged) - len(shown)
        _b = st.columns(2)
        if _b[0].button("전체", key=f"{key}__all", use_container_width=True, disabled=not pool):
            for o in pool:
                st.session_state[pfx + str(o)] = True     # 체크박스 생성 前이라 초기값 설정 OK
        if _b[1].button("해제", key=f"{key}__clr", use_container_width=True, disabled=not sel):
            for o in options:
                st.session_state[pfx + str(o)] = False
        if not shown:
            st.caption("옵션이 없어요.")
        elif over:
            st.caption(f"상위 200개 표시 · 나머지 {over}개는 검색해서 찾아요.")
        for o in shown:
            st.checkbox(str(o), key=pfx + str(o))
    return _sel()


# ── 필터바를 @st.fragment 로 격리 → 체크박스 조작은 이 조각만 가볍게 재실행되고,
#    무거운 본문(탭·차트)은 건드리지 않는다. '적용' 버튼을 눌러야 본문이 갱신된다.
@st.fragment
def _filterbar():
    with st.container(border=True, key="scard-filter"):
        # 필터는 왼쪽으로 모아 컴팩트하게(마지막은 빈 스페이서)
        _fb = st.columns([0.92, 0.8, 0.8, 0.8, 0.86, 0.86, 0.5, 2.5], gap="small")
        with _fb[0]:
            st.markdown('<div class="fbl">기간</div>', unsafe_allow_html=True)
            st.date_input("기간", value=[default_start, last_date],
                          min_value=first_date, max_value=last_date,
                          key="ph_f_date", label_visibility="collapsed")
        cbfilter(_fb[1], "국가", _opts["countries"], "ph_f_country")
        # 매장 후보: (초안) 선택 국가의 매장만(없으면 전체)
        _dc = [c for c in _opts["countries"] if st.session_state.get(f"ph_f_country__cb__{c}", False)]
        _sbc = _opts.get("stores_by_country", {})
        _std = (sorted(set().union(*[set(_sbc.get(c, [])) for c in _dc])) if _dc else _opts["stores"])
        cbfilter(_fb[2], "매장", _std, "ph_f_store")
        cbfilter(_fb[3], "카테고리", _opts["brands"], "ph_f_brand")
        cbfilter(_fb[4], "IP구분", IP_GUBUN_VIEW, "ph_f_gubun")
        # IP명 후보: (초안) 선택된 IP구분들의 IP명 합집합
        _dg = [g for g in IP_GUBUN_VIEW if st.session_state.get(f"ph_f_gubun__cb__{g}", False)]
        _ipd = (sorted(set().union(*[set(_opts["ip_by_gubun"].get(g, [])) for g in _dg]))
                if _dg else _opts["ip_by_gubun"].get("_ALL", []))
        cbfilter(_fb[5], "IP명", _ipd, "ph_f_ip")
        with _fb[6]:
            st.markdown('<div class="fbl">&nbsp;</div>', unsafe_allow_html=True)
            if st.button("✓ 적용", key="ph_f_apply", use_container_width=True, type="primary"):
                st.rerun()   # scope 기본=app → 본문(탭·차트) 한 번에 갱신


_filterbar()

# ── 적용된 필터 = 현재 위젯 상태 (본문 재실행 시 읽음. 체크 중에는 본문 안 바뀜) ──
_dv = st.session_state.get("ph_f_date", [default_start, last_date])
date_range = list(_dv) if isinstance(_dv, (list, tuple)) else [default_start, last_date]
sel_countries = [o for o in _opts["countries"] if st.session_state.get(f"ph_f_country__cb__{o}", False)]
# 매장: 선택 국가의 매장으로 좁혀 읽기(국가 미선택 시 전체)
if sel_countries:
    _store_opts = sorted(set().union(*[set(_opts.get("stores_by_country", {}).get(c, [])) for c in sel_countries]))
else:
    _store_opts = _opts["stores"]
sel_stores = [o for o in _store_opts if st.session_state.get(f"ph_f_store__cb__{o}", False)]
sel_brands = [o for o in _opts["brands"] if st.session_state.get(f"ph_f_brand__cb__{o}", False)]
sel_gubuns = [g for g in IP_GUBUN_VIEW if st.session_state.get(f"ph_f_gubun__cb__{g}", False)]
if sel_gubuns:
    _ip_all = sorted(set().union(*[set(_opts["ip_by_gubun"].get(g, [])) for g in sel_gubuns]))
else:
    _ip_all = _opts["ip_by_gubun"].get("_ALL", [])
selected_ips = [o for o in _ip_all if st.session_state.get(f"ph_f_ip__cb__{o}", False)]

# ── 필터 적용 (scope = 날짜 외 모든 필터, df = scope + 날짜) ──
scope = df_all
if sel_countries:
    scope = scope[scope["국가"].isin(sel_countries)]
if sel_brands:
    scope = scope[scope["브랜드"].isin(sel_brands)]
if sel_stores:
    scope = scope[scope["매장 이름"].isin(sel_stores)]
if sel_gubuns:
    scope = scope[scope["IP구분"].isin(sel_gubuns)]
if selected_ips:
    scope = scope[scope["IP명"].isin(selected_ips)]

df = scope
if len(date_range) == 2:
    df = scope[(scope["날짜"] >= date_range[0]) & (scope["날짜"] <= date_range[1])]

sales = paid_sales(df)


# ── 타이틀 판매기간·상태 (타이틀 순위표에 표시) ────────────────
# 매출이 빠졌을 때 '끝나서'인지 '안 끝났는데'인지 가르려고 Jira 종료일을 함께 본다.
# ★ 날짜로 자르지 않은 scope 를 넘긴다 — 기간으로 자른 df 를 주면 첫 거래일이
#   전부 기간 시작일이 돼서 죄다 '신규'로 나온다.
@st.cache_data(ttl=1800, show_spinner=False, max_entries=8)
def _title_status_ph(_agg_mtime, _p0, _p1, _countries, _brands, _stores, _gubuns, _ips):
    from title_runs import title_status
    from jira_ip_dates import fetch_ip_dates
    base = scope[~scope["취소 여부"].astype(bool)]
    try:
        # brand="all" — 브랜드 필드로 거르면 오히려 놓친다(스내피즘 쪽에서 확인된 사실).
        jira = fetch_ip_dates(brand="all", force_refresh=False)
    except Exception:
        jira = {}        # Jira 가 죽어도 판매기간(실측)은 그대로 나온다
    # prefer_brand="photoism" — 같은 IP가 양 브랜드에 있으면 포토이즘 티켓을 써야 한다
    # (안 그러면 AG-ENT·AND2BLE 처럼 Snapism 티켓의 종료일이 붙는다).
    return title_status(base, jira, _p0, _p1, title_col="타이틀", prefer_brand="photoism")


try:
    _tstat = _title_status_ph(
        _file_mtime(AGG_FILE),
        date_range[0] if len(date_range) == 2 else None,
        date_range[1] if len(date_range) == 2 else None,
        tuple(sel_countries), tuple(sel_brands), tuple(sel_stores),
        tuple(sel_gubuns), tuple(selected_ips))
except Exception:
    _tstat = {}

# ══════════════════════════════════════════════════════════════
#  관리자 전용: '계산 방식 설명' 토글 + helpbox 헬퍼
#  - 소유자에게만 사이드바 체크박스 노출. 체크 시에만 각 카드에 접기 설명 표시.
#  - 일반 사용자/토글 OFF면 렌더링 안 됨(흔적·부하 없음).
#  ※ expander 중첩 불가 → helpbox 는 다른 expander(더보기·데이터) 바깥에 둔다.
# ══════════════════════════════════════════════════════════════
_is_owner = auth.is_owner(getattr(getattr(st, "user", None), "email", None))
if _is_owner:
    # 관리자 전용 도구를 하나의 카드로 묶음(계산설명 토글 + 실시간 환율). 아래 환율 expander도 여기에 넣음.
    _sb_admin = st.sidebar.container(border=True, key="sb-admin")
    with _sb_admin:
        st.markdown('<div class="sb-admin-hd">🔧 관리자 전용</div>', unsafe_allow_html=True)
        st.checkbox(
            "계산 방식 설명", key="show_calc_help",
            help="각 카드 아래에 '이 값이 어떻게 계산·검증되는지' 설명을 접기로 보여줘요. 관리자에게만 보입니다.")


def helpbox(md):
    """관리자가 토글을 켰을 때만, 접기로 '이 값 계산 방식'을 보여준다."""
    if _is_owner and st.session_state.get("show_calc_help"):
        with st.expander("ℹ️ 이 값은 어떻게 계산되나요?", expanded=False):
            st.markdown(md)


# ══════════════════════════════════════════════════════════════
#  KPI 3카드 + 범위 배너
# ══════════════════════════════════════════════════════════════
def period_rev(d):
    return int(paid_sales(d)["매출액"].sum())


period_amt = period_rev(df)
_period_days = ((date_range[1] - date_range[0]).days + 1) if len(date_range) == 2 else "-"
_dr = (f"{date_range[0]} ~ {date_range[1]}" if len(date_range) == 2 else "전체")
# 총매출 = 실결제(카드·현금) + 쿠폰·코인 정산분 으로 구성 표시(취소는 집계에 없음)
pure_krw = int(sales["KRW환산금액"].sum())
cc_krw = int((sales["쿠폰기여"] + sales["코인기여"]).sum())
cc_cnt = int(sales[(sales["쿠폰기여"] > 0) | (sales["코인기여"] > 0)]["건수"].sum())

st.markdown(
    '<div class="kpis">'
    f'<div class="kpi hero"><div class="l">조회기간 매출 (합계)</div>'
    f'<div class="v num">{fmt_krw(period_amt)}</div>'
    f'<div class="d">{_dr} · {_period_days}일 · {tx_count(sales):,}건</div></div>'
    f'<div class="kpi"><div class="l">실결제 매출 (카드·현금)</div>'
    f'<div class="v num">{fmt_krw(pure_krw)}</div><div class="d">쿠폰·코인 제외분</div></div>'
    f'<div class="kpi"><div class="l">쿠폰·코인 매출 (정산분)</div>'
    f'<div class="v num">{fmt_krw(cc_krw)}</div><div class="d">{cc_cnt:,}건 · 지정국가 정산</div></div>'
    '</div>', unsafe_allow_html=True)

_scope_bits = []
if sel_countries:
    _scope_bits.append("국가: " + " · ".join(sel_countries[:4]) + (" 외" if len(sel_countries) > 4 else ""))
if sel_stores:
    _scope_bits.append("매장: " + " · ".join(sel_stores[:3]) + (" 외" if len(sel_stores) > 3 else ""))
if sel_brands:
    _scope_bits.append("카테고리: " + " · ".join(sel_brands))
if sel_gubuns:
    _scope_bits.append("IP구분: " + " · ".join(sel_gubuns))
if selected_ips:
    _scope_bits.append("IP: " + " · ".join(selected_ips[:4]) + (" 외" if len(selected_ips) > 4 else ""))
if _scope_bits:
    st.markdown('<div class="scope">🌐 범위 — ' + "  |  ".join(_scope_bits) + '</div>',
                unsafe_allow_html=True)

helpbox("""
**KPI 3카드 — 조회기간 매출 · 실결제 · 쿠폰·코인**

**공통 기준 (이하 모든 카드 동일)**
- **원본**: 30개국 포토이즘 매장 거래(매일 자동 수집)를 **DuckDB로 집계**한 값 · 환율은 `config.json` 실시간 환율표.
- **매출액 = 실결제 + 쿠폰기여 + 코인기여** — `KRW환산금액`(= 최종 결제 금액 × 환율)에, **지정 국가에서만** 쿠폰(`쿠폰기여`)·서비스코인(`코인기여`)을 더함(나라마다 정산 규칙이 달라서).
- **필터 반영**: 필터바(기간·국가·매장·카테고리·IP구분·IP)로 거른 뒤 계산. 미선택 = 전체.
- **취소 없음**: 집계 데이터에 취소행이 없어 취소 KPI는 없어요(스내피즘과 다른 점).

**각 카드 계산**
- **조회기간 매출(합계)** = 매출액 합(실결제 + 지정국가 쿠폰·코인). 건수 = 거래 건수.
- **실결제 매출(카드·현금)** = `KRW환산금액` 합 (쿠폰·코인 제외한 순수 결제분).
- **쿠폰·코인 매출(정산분)** = `쿠폰기여 + 코인기여` 합 = 매출 합계 − 실결제. 지정 국가에서만 잡혀요.
""")

# ── 사이드바: 실시간 환율(접기) — 소유자(본인)만, 위 '관리자 전용' 카드 안에 함께 ──
if _is_owner:
    with _sb_admin:
        with st.expander("💱 실시간 환율", expanded=False):
            if cfg.get("rates_updated"):
                st.caption(f"업데이트 {cfg.get('rates_updated')}")
            for _cur, _rate in ex.items():
                if _cur != "KRW":
                    st.caption(f"1 {_cur} = ₩{_rate:,.2f}")

# ── IP 구분 요약(탭에서 사용) ──
gub = pd.DataFrame()
present = []
present_all = []
if "IP구분" in sales.columns:
    gub = (
        sales[sales["IP구분"] != "제외"]
        .groupby("IP구분", observed=True)
        .agg(매출=("매출액", "sum"), 건수=("건수", "sum"))
        .reset_index()
    )
    gub = gub[gub["매출"] > 0]
    if not gub.empty:
        gub["_o"] = gub["IP구분"].astype(str).map(
            {g: i for i, g in enumerate(ip_classify.IP_GUBUN_ORDER)}).fillna(99)
        gub = gub.sort_values("_o")
        # present = 단독 필터/상세탭용(기획P 제외). present_all = 집계 뷰(추이·합계)용(기획P 포함).
        _gset = set(gub["IP구분"].astype(str))
        present = [g for g in IP_GUBUN_VIEW if g in _gset]
        present_all = [g for g in ip_classify.IP_GUBUN_ORDER if g in _gset]


def _pkey(dates, g):
    d = pd.to_datetime(dates)
    return d.dt.to_period("M") if g == "월" else (d.dt.to_period("W") if g == "주" else d.dt.date)


def _plabel(p, g):
    if g == "월":
        return f"{p.year}.{p.month:02d}"
    if g == "주":
        return p.start_time.strftime("%m/%d") + "주"
    return str(p)


# ══════════════════════════════════════════════════════════════
#  탭 6개
# ══════════════════════════════════════════════════════════════
# [보류] '시간대 · 데이터' 탭 — 숨김 처리(코드·데이터는 그대로 보존).
#         다시 살리려면 SHOW_TAB_ETC = True 로만 바꾸면 됨.
SHOW_TAB_ETC = False
_tab_labels = ["📊 매출 한눈에", "🎬 IP · 타이틀 분석", "🌏 국가별 분석",
               "🏬 매장별 분석", "🔎 세부 항목"]
if SHOW_TAB_ETC:
    _tab_labels.append("⏰ 시간대 · 데이터")
_tabs = st.tabs(_tab_labels)
tab_home, tab_ip, tab_nat, tab_store, tab_detail = _tabs[0], _tabs[1], _tabs[2], _tabs[3], _tabs[4]
tab_etc = _tabs[5] if SHOW_TAB_ETC else None

# ════════════ 탭 1: 매출 한눈에 ════════════
with tab_home:
    sec("1", "매출 동향", "잘 가고 있나? — 기간별 IP구분 매출 흐름")
    with card():
        _th, _tg = st.columns([2.4, 1])
        with _th:
            st.markdown('<div class="ct" style="margin-bottom:0">📈 매출 추이 '
                        '<span class="muted">IP구분별</span></div>', unsafe_allow_html=True)
        with _tg:
            gran = st.segmented_control("기간", ["월", "주", "일"], default="월",
                                        key="ph_trend_gran", label_visibility="collapsed") or "월"
        _tsrc = sales[sales["IP구분"].astype(str).isin(present_all)].copy() if present_all else pd.DataFrame()
        if _tsrc.empty:
            css_stack([], {}, [], gran)
        else:
            _tsrc["_p"] = _pkey(_tsrc["날짜"], gran)
            g2 = (_tsrc.groupby(["_p", "IP구분"], observed=True)["매출액"].sum()
                  .rename("매출").reset_index())
            periods = sorted(g2["_p"].unique())
            labels = [_plabel(p, gran) for p in periods]
            pidx = {p: i for i, p in enumerate(periods)}
            data = {g: [0] * len(periods) for g in present_all}
            for _, r in g2.iterrows():
                _g = str(r["IP구분"])
                if _g in data:
                    data[_g][pidx[r["_p"]]] = int(r["매출"])
            css_stack(labels, data, present_all, gran)
            st.caption("막대는 IP구분(아티스트·캐릭터·렌탈·PICK)별로 쌓았어요. "
                       "전체 순위는 'IP · 타이틀 분석' 탭에서 봐요.")
        helpbox("""
**매출 추이 (IP구분별 스택)**
- 매출액(실결제 + 쿠폰기여 + 코인기여)을 기간(월/주/일)·`IP구분`으로 묶어 쌓은 막대.
- IP구분 = 아티스트·캐릭터·렌탈·PICK·기획P. **추이·합계 집계엔 기획P 포함**(`present_all`), 단독 필터/상세 탭은 기획P 제외(`present`).
- ※ 공통 기준(원본·환율·매출액 정의)은 상단 'KPI 카드' 설명 참고.
""")

    sec("2", "무엇이 매출을 만드나", "비중 — 어떤 IP구분·카테고리가 매출을 끄나")
    _c1, _c2 = st.columns(2)
    with _c1:
        with card("🎬 IP 구분 비중"):
            if not gub.empty:
                gg = gub.sort_values("매출", ascending=False)
                colors = [_GUB_COLORS.get(str(g), "#c7ccd6") for g in gg["IP구분"]]
                css_donut(list(zip(gg["IP구분"].astype(str), gg["매출"])), colors)
            else:
                st.info("데이터가 없어요.")
            helpbox("""
**IP 구분 비중**
- `IP구분`별 매출액 합의 비중(도넛). 색은 아티스트·캐릭터·렌탈·PICK·기획P 고정색.
""")
    with _c2:
        with card("🏷 상품 카테고리 비중"):
            pc = (sales.groupby("브랜드", observed=True)["매출액"].sum().rename("매출")
                  .reset_index().sort_values("매출", ascending=False))
            pc = pc[pc["브랜드"].astype(str).str.strip().replace("nan", "").ne("") & (pc["매출"] > 0)]
            if len(pc) > 4:
                pc = pd.concat([pc.head(3), pd.DataFrame([{
                    "브랜드": f"기타 {len(pc) - 3}종",
                    "매출": int(pc.iloc[3:]["매출"].sum())}])], ignore_index=True)
            if not pc.empty and pc["매출"].sum() > 0:
                css_donut(list(zip(pc["브랜드"].astype(str), pc["매출"])),
                          ["var(--brand-2)", "var(--amber)", "#7c77ee", "#c7ccd6"])
            else:
                st.info("데이터가 없어요.")
            helpbox("""
**상품 카테고리 비중**
- `브랜드`(상품 카테고리)별 매출액 합. 요약이라 상위 3 + '기타 N종' 묶음.
""")

    with card("🎞 타이틀 TOP 5 <span class='muted'>(날짜+IP · 한·영 통합)</span>"):
        _tsrc2 = sales[(sales["타이틀"] != "") & sales["타이틀"].notna()]
        tr = (_tsrc2.groupby("타이틀", observed=True)["매출액"].sum().rename("매출")
              .reset_index())
        tr = tr[tr["매출"] > 0]
        if not tr.empty:
            hbar_list(tr, "타이틀", top=5)
        else:
            st.info("타이틀 데이터가 없어요.")
        helpbox("""
**타이틀 TOP 5**
- `타이틀`(날짜+IP 기준, 한·영 통합)별 매출액 합 → 상위 5개.
""")

    sec("3", "어디서 파나", "지역 — 국가·매장별 매출 (원화 기준)")
    _n1, _n2 = st.columns(2)
    with _n1:
        with card("🌏 국가별 매출 TOP 6"):
            nat6 = (sales.groupby("국가", observed=True)["매출액"].sum().rename("매출").reset_index()
                    ) if "국가" in sales.columns else pd.DataFrame()
            nat6 = nat6[nat6["매출"] > 0] if not nat6.empty else nat6
            if not nat6.empty:
                hbar_list(nat6, "국가", top=6)
            else:
                st.info("데이터가 없어요.")
            helpbox("""
**국가별 매출 TOP 6**
- `국가`별 매출액(원화) 합 → 상위 6개국. 나라 비교는 항상 원화 기준.
""")
    with _n2:
        with card("🏬 국가별 매출 TOP 5 매장", key="scard-natsel"):
            _cs = (sales.groupby("국가", observed=True)["매출액"].sum().sort_values(ascending=False).index.tolist()
                   if "국가" in sales.columns else [])
            _cs = [str(c) for c in _cs]
            if _cs:
                _pick = st.selectbox("국가", _cs, key="ph_home_store_country", label_visibility="collapsed")
                _ss = (sales[sales["국가"] == _pick].groupby("매장 이름", observed=True)
                       .agg(매출=("매출액", "sum"), 건수=("건수", "sum"))
                       .reset_index().sort_values("매출", ascending=False).head(5))
                _ss = _ss[_ss["매출"] > 0]
                if not _ss.empty:
                    hbar_list(_ss, "매장 이름", top=5)
                    st.caption("선택한 국가의 매출 상위 5개 매장")
                else:
                    st.info("이 국가의 매장 데이터가 없어요.")
            else:
                st.info("데이터가 없어요.")
            helpbox("""
**국가별 매출 TOP 5 매장**
- 위 셀렉트박스에서 고른 국가의 `매장 이름`별 매출액 합·건수 → 상위 5개.
""")
    st.caption("※ 여긴 요약(TOP)이에요. 전체 순위는 '국가별 분석'·'매장별 분석' 탭에서 봐요.")

# ════════════ 탭 2: IP · 타이틀 분석 ════════════
with tab_ip:
    with card("🎭 IP 구분 (비중 · 매출)"):
        if not gub.empty:
            _g1, _g2 = st.columns([5, 5])
            gg = gub.sort_values("매출", ascending=False)
            with _g1:
                colors = [_GUB_COLORS.get(str(g), "#c7ccd6") for g in gg["IP구분"]]
                css_donut(list(zip(gg["IP구분"].astype(str), gg["매출"])), colors)
            with _g2:
                hbar_list(gg.rename(columns={"IP구분": "_n"}), "_n")
        else:
            st.info("데이터가 없어요.")
        helpbox("""
**IP 구분 (비중 · 매출)**
- `IP구분`별 매출액 합. 왼쪽 도넛=비중, 오른쪽 막대=구분별 매출액.
""")

    if present:
        with card("🎬 IP 구분별 타이틀 상세 <span class='muted'>(구분 선택 → 타이틀별 매출)</span>"):
            _gtabs = st.tabs([f"{_GUB_EMOJI.get(g, '🎬')} {g}" for g in present])
            for _i, _g in enumerate(present):
                with _gtabs[_i]:
                    _sub = sales[sales["IP구분"] == _g]
                    _t = (_sub[(_sub["타이틀"] != "") & _sub["타이틀"].notna()]
                          .groupby("타이틀", observed=True)
                          .agg(매출=("매출액", "sum"), 건수=("건수", "sum")).reset_index())
                    _t = _t[_t["매출"] > 0]
                    statrow([("매출", fmt_krw(int(_sub["매출액"].sum()))),
                             ("건수", f"{tx_count(_sub):,}건"),
                             ("타이틀 수", f"{len(_t):,}개")])
                    if _t.empty:
                        st.info("해당 조건에 맞는 데이터가 없어요. 날짜·국가·매장 필터를 넓혀 보세요.")
                    else:
                        rank_table(_t, "타이틀", collapse_after=10)
        helpbox("""
**IP 구분별 타이틀 상세**
- 구분 하위탭 선택 → 그 구분(`IP구분`)의 `타이틀`별 매출액·건수 순위(TOP10 + 나머지 접기). 상단 요약 = 구분 총매출·건수·타이틀 수.
""")

    @st.fragment
    def _title_rank():
        with card("🎞 타이틀 전체 순위", key="scard-titlesel"):
            _gopts = ["전체"] + present
            pick = st.selectbox("구분", _gopts, key="ph_title_gubun", label_visibility="collapsed")
            _src = sales if pick == "전체" else sales[sales["IP구분"] == pick]
            _src = _src[(_src["타이틀"] != "") & _src["타이틀"].notna()]
            tt = (_src.groupby("타이틀", observed=True)
                  .agg(매출=("매출액", "sum"), 건수=("건수", "sum")).reset_index())
            tt = tt[tt["매출"] > 0]

            # 상태 필터 — 실제로 존재하는 상태만 칩으로 노출(빈 필터 클릭 방지)
            _sc = tt["타이틀"].map(lambda t: (_tstat.get(t) or {}).get("상태", "")) if _tstat else None
            if _tstat and _sc is not None:
                _have = [s for s in ["🔴 확인필요", "⚠️ 기간후판매", "🔚 종료", "⏳ 종료예정", "🆕 신규", "🟢 판매중", "⚪ 미확인"]
                         if (_sc == s).any()]
                _cnt = " · ".join(f"{s} {int((_sc == s).sum())}" for s in _have)
                _spick = st.segmented_control("상태", ["전체"] + _have, default="전체",
                                              key="ph_title_stat", label_visibility="collapsed") or "전체"
                if _spick != "전체":
                    tt = tt[(_sc == _spick).reindex(tt.index, fill_value=False)]
                st.caption(f"타이틀 {len(tt):,}개 · {_cnt}"
                           + ("" if pick == "전체" else f" · {pick}"))
            else:
                st.caption(f"타이틀 {len(tt):,}개 · TOP 10 + 나머지 접기"
                           + ("" if pick == "전체" else f" · {pick}"))

            if tt.empty:
                st.info("데이터가 없어요.")
            else:
                rank_table(tt, "타이틀", collapse_after=10, status_map=_tstat or None)
            helpbox("""
**타이틀 전체 순위**
- '전체' 또는 선택 구분의 `타이틀`별 매출액·건수 → 순위(TOP10 + 나머지 접기).

**판매기간 · 상태** — 매출이 빠졌을 때 *끝나서* 빠진 건지, *안 끝났는데* 빠진 건지 가르려고 붙였어요.
- **판매기간** = 그 타이틀의 **실제 첫·마지막 거래일**(조회 기간이 아니라 전체 이력 기준, 결측 0%).
- **상태**는 실측 거래일 + **Jira 종료일**(`duedate`)로 판정해요. 마지막 거래일만으론 '종료'인지 '그냥 안 팔리는 중'인지 구분이 안 되거든요.
  - **🔚 종료** — Jira 종료일이 지났고 거래도 멈춤 → **급감이 예정된 것**
  - **⚠️ 기간후판매** — 종료일이 지났는데 **아직 팔리는 중** → 계약·정산에서 확인이 필요해요
  - **🆕 신규** — 첫 거래일이 조회 기간 안 → 올라간 게 정상
  - **⏳ 종료예정** — 30일 안에 종료 예정
  - **🔴 확인필요** — 판매기간이 남았는데 **7일 이상 거래 없음** → 점검 대상
  - **🟢 판매중** / **⚪ 미확인**(Jira 미연결이라 종료 여부 단정 불가)
- 타이틀명(`260601 김혜빈 작가`)에서 날짜 접두를 떼고 Jira 타이틀과 맞춰요. **매출의 약 90%** 가 연결돼요. 생일·계절 프레임처럼 IP가 아닌 자체 제작 타이틀은 Jira 티켓이 없어 `⚪ 미확인`으로 남고, **추측하지 않아요.**
""")

    _title_rank()

    # 선택 IP명 상세 (사이드바/필터에서 IP명 고른 경우)
    if selected_ips:
        _lbl = (f"🔥 [{selected_ips[0]}] IP 상세 분석" if len(selected_ips) == 1
                else f"🔥 [{' + '.join(selected_ips)}] 합산 분석")
        with card(_lbl):
            ip_detail = sales[sales["IP명"].isin(selected_ips)]
            if ip_detail.empty:
                st.info("해당 조건에 맞는 데이터가 없어요. 날짜·국가·매장 필터를 넓혀 보세요.")
            else:
                statrow([("합산 총 매출", fmt_krw(int(ip_detail["매출액"].sum()))),
                         ("총 결제 건수", f"{tx_count(ip_detail):,}건"),
                         ("판매 국가 수", f"{ip_detail['국가'].nunique()}개국"),
                         ("판매 매장 수", f"{ip_detail['매장 이름'].nunique()}개")])
                _i1, _i2 = st.columns([5, 5])
                with _i1:
                    st.markdown('<div class="ct">📅 일별 매출 추이</div>', unsafe_allow_html=True)
                    _dl = (ip_detail.groupby("날짜", observed=True)["매출액"].sum()
                           .reset_index().sort_values("날짜"))
                    _rows = [(pd.to_datetime(d).strftime("%m/%d"), int(v))
                             for d, v in zip(_dl["날짜"], _dl["매출액"])]
                    css_series(_rows, color=PINK, gran="일")
                with _i2:
                    st.markdown('<div class="ct">🌏 국가별 매출 분포</div>', unsafe_allow_html=True)
                    _in = (ip_detail.groupby("국가", observed=True)["매출액"].sum().rename("매출")
                           .reset_index().sort_values("매출", ascending=False))
                    _in = _in[_in["매출"] > 0]
                    if len(_in) > 7:
                        _in = pd.concat([_in.head(7), pd.DataFrame([{
                            "국가": f"기타 {len(_in) - 7}개국", "매출": int(_in.iloc[7:]["매출"].sum())}])],
                            ignore_index=True)
                    if not _in.empty:
                        css_donut(list(zip(_in["국가"].astype(str), _in["매출"])), PAL, size=150, hole=48)
            helpbox("""
**선택 IP 상세 분석**
- 필터바에서 고른 `IP명`(복수면 합산)의 매출액 기준 상세: 일별 추이·국가별 분포·합산 지표(총매출·건수·판매 국가/매장 수).
""")
    else:
        st.caption("💡 상단 필터바에서 **IP명**을 고르면 여기에 IP 상세 분석(일별·국가별·합산 비교)이 나와요.")

# ════════════ 탭 3: 국가별 분석 ════════════
with tab_nat:
    if "국가" not in sales.columns or sales.empty:
        st.info("국가 데이터가 없어요. 필터를 넓혀 보세요.")
    else:
        nat = (
            sales.groupby(["국가", "결제 단위"], observed=True)
            .agg(건수=("건수", "sum"), 현지=("최종 결제 금액", "sum"), 매출=("매출액", "sum"),
                 _쿠폰=("쿠폰기여", "sum"), _코인=("코인기여", "sum"))
            .reset_index()
        )
        nat = nat[nat["매출"] > 0].sort_values("매출", ascending=False)
        tot = nat["매출"].sum()
        mx = (nat["매출"] / tot).max() if tot else 1.0

        with card("🌏 국가별 매출"):
            grid = "grid-template-columns:1.4fr .6fr .7fr 1.2fr 1.2fr .8fr 1.4fr"
            html = (f'<div class="ntbl"><div class="ntr nth" style="{grid}">'
                    '<span>국가</span><span class="c">통화</span><span class="r">건수</span>'
                    '<span class="r">현지 매출</span><span class="r">KRW 매출</span>'
                    '<span class="r">쿠폰·코인</span><span>비중</span></div>')
            for _, r in nat.iterrows():
                frac = (r["매출"] / tot) if tot else 0
                _cc = ((r["_쿠폰"] + r["_코인"]) / r["매출"]) if r["매출"] else 0
                html += (f'<div class="ntr" style="{grid}">'
                         f'<span class="nname">{flag_img(r["국가"])}{r["국가"]}</span>'
                         f'<span class="c"><span class="cur">{r["결제 단위"]}</span></span>'
                         f'<span class="r num">{int(r["건수"]):,}</span>'
                         f'<span class="r num">{fmt_orig(r["현지"], r["결제 단위"])}</span>'
                         f'<span class="r num">{fmt_krw(r["매출"])}</span>'
                         f'<span class="r num">{_cc * 100:.0f}%</span>{pct_bar(frac, mx)}</div>')
            st.markdown(html + "</div>", unsafe_allow_html=True)
            st.caption(f"전체 {len(nat)}개국 · 매출 내림차순. "
                       "'쿠폰·코인'이 높은 국가는 매출 대부분이 쿠폰·코인 정산분이에요.")
            helpbox("""
**국가별 매출 표**
- `국가`·`결제 단위`(통화)로 묶어: **건수**, **현지 매출**(`최종 결제 금액` 합, 현지통화), **KRW 매출**(매출액 합), **쿠폰·코인**(=(쿠폰기여+코인기여)/매출액 비율), **비중**(전체 KRW 대비).
- '쿠폰·코인'이 높은 국가(예: 라오스)는 매출 대부분이 쿠폰·코인 정산분이에요.
""")

        # ── 키오스크 1대당 매출 ────────────────────────────────
        # 분자·분모 모두 렌탈·팝업을 뺀다. 렌탈은 행사 기간만 도는 장비라 남겨두면
        # 분모가 계속 살아 있는 것으로 잡혀 대당 매출이 실제보다 낮게 나온다.
        _dev = load_devices()
        if not _dev.empty and len(date_range) == 2 and "국가코드" in sales.columns:
            _dd = device_days(_dev, date_range[0], date_range[1])
            _pkd = (date_range[1] - date_range[0]).days + 1
            _box = sales[sales["브랜드"].astype(str) != "Rentals and pop-ups"]
            _rev = (_box.groupby("국가코드", observed=True)
                    .agg(매출=("매출액", "sum"), 건수=("건수", "sum"), 국가=("국가", "first"))
                    .reset_index())
            _rev["국가코드"] = _rev["국가코드"].astype(str).str.lower().str.strip()
            per = _rev.merge(_dd, on="국가코드", how="inner")
            per = per[(per["대일"] > 0) & (per["매출"] > 0)].copy()
            per["대당월"] = (per["매출"] / per["대일"] * 30).round(0).astype("int64")
            per["대당건"] = (per["건수"] / per["대일"] * 30).round(1)
            per = per.sort_values("대당월", ascending=False)

            if not per.empty:
                with card("🎰 키오스크 1대당 매출 <span class='muted'>(렌탈·팝업 제외)</span>",
                          key="scard-perkiosk"):
                    # ★표본 하한 — 몇 대뿐인 나라는 매장 하나 성적이 그대로 국가 대표값이 돼
                    #   1위로 튄다(포토이즘에서 4대짜리 영국이 1위였다). 3대 고정은 한국 1,600대
                    #   옆에서 너무 얕아 최대 보유국의 1%(최소 3대)로 규모에 맞춘다.
                    #   숨기지는 않는다 — 기준 미달 국가는 표 아래쪽에 '표본 적음'으로 따로 보인다.
                    _MIN_DEV = max(3, int(-(-per["대수"].max() // 100)))
                    _big   = per[per["대수"] >= _MIN_DEV]
                    _small = per[per["대수"] < _MIN_DEV]
                    if _big.empty:                      # 전부 소규모면 하한을 접는다
                        _big, _small = per, per.iloc[0:0]
                    per = pd.concat([_big, _small])     # 둘 다 이미 대당월 내림차순
                    # 100%·헤더 이름은 기준을 넘긴 국가에서만 잡는다.
                    _mx   = _big["대당월"].max()
                    _lead = str(_big.iloc[0]["국가"])
                    grid = ("grid-template-columns:1.3fr .65fr .85fr .95fr 1.15fr "
                            ".85fr 1.1fr")
                    html = (f'<div class="ntbl"><div class="ntr nth" style="{grid}">'
                            '<span>국가</span><span class="r">가동 대수</span>'
                            '<span class="r">기간 내 변동</span>'
                            '<span class="r tip dn" data-tip="장비마다 실제로 돈 날짜를 모두 더한 값'
                            ' · 예: 3대가 30일씩 = 90">총 가동일 ⓘ</span>'
                            '<span class="r">1대당 월매출</span>'
                            '<span class="r">1대당 월건수</span>'
                            f'<span class="tip dn" data-tip="1대당 월매출이 가장 높은 {_lead}{josa(_lead, '을', '를')} 100%로 둔 비율 · 총매출 1위와는 다른 순위예요">{_lead} 대비 ⓘ</span></div>')
                    for _, r in per.iterrows():
                        # 왜 이 숫자가 나왔는지 읽히도록 장비 변동을 같은 줄에 둔다.
                        # 신규가 많으면 그 나라 대·일이 대수 대비 짧아 대당 매출이 눌린다.
                        _new, _stop = int(r["신규"]), int(r["중지"])
                        _chg = (f'<span style="color:var(--green)">+{_new}</span>' if _new else
                                '<span style="color:var(--text-3)">–</span>')
                        if _stop:
                            _chg += f'<span style="color:var(--text-3)"> / 중지 {_stop}</span>'
                        _thin = ('<span style="font-size:10px;font-weight:700;color:#b45309;'
                                 'background:#fdf3e7;padding:1px 6px;border-radius:5px;'
                                 'margin-left:5px">표본 적음</span>'
                                 if r["대수"] < _MIN_DEV else '')
                        html += (f'<div class="ntr" style="{grid}">'
                                 f'<span class="nname">{flag_img(r["국가"])}{r["국가"]}{_thin}</span>'
                                 f'<span class="r num">{int(r["대수"]):,}대</span>'
                                 f'<span class="r num" style="font-size:12px">{_chg}</span>'
                                 f'<span class="r num">{int(r["대일"]):,}</span>'
                                 f'<span class="r num">{fmt_krw(int(r["대당월"]))}</span>'
                                 f'<span class="r num">{r["대당건"]:,.1f}건</span>'
                                 f'{pct_bar(r["대당월"] / _mx if _mx else 0)}</div>')
                    st.markdown(html + "</div>", unsafe_allow_html=True)

                    # 총매출 1위와 대당 효율 1위가 갈리는 게 이 카드의 핵심이다.
                    # (물량은 한국이 압도적인데 1대당으로는 하위 — 이걸 놓치면 카드를 봐도 남는 게 없다)
                    _cand = _big          # 표본 하한을 넘긴 국가끼리만 비교
                    _top_rev = per.loc[per["매출"].idxmax()]
                    _top_eff = _cand.iloc[0]
                    if _top_rev["국가"] != _top_eff["국가"] and _top_rev["대당월"]:
                        _x  = _top_eff["대당월"] / _top_rev["대당월"]
                        _rk = list(_cand["국가"]).index(_top_rev["국가"]) + 1                             if _top_rev["국가"] in list(_cand["국가"]) else None
                        _rev_n, _eff_n = _top_rev["국가"], _top_eff["국가"]
                        _tail = (f'{_rev_n}{josa(_rev_n, "은", "는")} {len(_cand)}개국 중 '
                                 f'<b>{_rk}위</b>{josa("위", "이에요", "예요")}.'
                                 if _rk else f'{_rev_n}{josa(_rev_n, "은", "는")} 표본이 작아 순위에서 빠졌어요.')
                        _note = (f' <span style="color:var(--text-3)">({_MIN_DEV}대 이상인 국가끼리 비교)</span>'
                                 if len(_cand) < len(per) else '')
                        st.markdown(
                            '<div class="strip">💡 총매출 1위는 '
                            f'<b>{_rev_n}</b>({fmt_krw(int(_top_rev["매출"]))}){josa(_rev_n, "인데", "인데")}, '
                            f'1대당 효율 1위는 <b>{_eff_n}</b>{josa(_eff_n, "이에요", "예요")} — '
                            f'1대당으로는 {_eff_n}{josa(_eff_n, "이", "가")} <b>{_x:.1f}배</b>, '
                            f'{_tail}{_note}</div>',
                            unsafe_allow_html=True)

                    st.caption(f"조회기간({_pkd}일) **실제 매출**을 장비 1대·30일 기준으로 환산한 값이에요"
                               "(예상치가 아니에요). "
                               f"'{_lead} 대비'는 1대당 매출 1위인 **{_lead}**{josa(_lead, '을', '를')} 100%로 둔 비율이에요 — "
                               "총매출 1위와는 다른 나라일 수 있고, 위 국가별 매출표의 "
                               "'비중'(전체 대비 점유율)과도 다른 값이에요.")

                    # 숫자 배경이 되는 설치 이력 — 매출이 오르내린 이유를 같이 보게 한다.
                    with st.expander("📜 장비 설치 이력 (최근 12개월, 월별 신규 설치 대수)"):
                        _h = _dev[_dev["설치일"].notna()].copy()
                        _end = pd.Timestamp(date_range[1]).to_period("M")
                        _h["월"] = _h["설치일"].dt.to_period("M")
                        _h = _h[(_h["월"] <= _end) & (_h["월"] > _end - 12)]
                        if _h.empty:
                            st.caption("이 기간 이전 12개월 안에 새로 설치된 장비가 없어요.")
                        else:
                            _cc2nat = dict(zip(per["국가코드"], per["국가"]))
                            _piv = (_h.assign(국가=_h["국가코드"].map(_cc2nat))
                                    .dropna(subset=["국가"])
                                    .pivot_table(index="국가", columns="월", values="가동중",
                                                 aggfunc="size", fill_value=0))
                            _piv.columns = [str(c)[2:].replace("-", ".") for c in _piv.columns]
                            _piv["합계"] = _piv.sum(axis=1)
                            _piv = _piv.sort_values("합계", ascending=False)
                            st.dataframe(_piv, use_container_width=True)
                            st.caption("설치일은 기기 S/N 앞 6자리(YYMMDD) 기준이에요. "
                                       "숫자가 늘어난 달 뒤로 그 나라 매출이 함께 올랐는지 보면 "
                                       "증설 효과를 가늠할 수 있어요.")
                    helpbox("""
**키오스크 1대당 매출**
- **총 가동일** = 장비마다 그 기간에 실제로 돈 날짜를 모두 더한 값이에요. 3대가 30일씩 돌았으면 90이에요.
  - 기간 중간에 설치된 장비는 **설치일부터만** 세요. 그래서 20일 늦게 깔린 장비는 30이 아니라 10만 더해져요.
  - 왜 대수로 안 나누냐면 — 이번 달에 깐 장비를 한 달 내내 있던 것처럼 세면 그 나라 대당 매출이 실제보다 낮게 나오거든요.
- **1대당 월매출** = 기간 **실제** 매출 ÷ 총 가동일 × 30. **예상치가 아니에요.** 조회기간이 30일이면 그대로 한 달 실적이고, 30일이 아니면 30일치로 환산해요(7일만 보면 ×30/7). 월건수도 같은 방식이에요.
  - 그래서 **짧은 기간을 보면 그 며칠의 편차(주말·이벤트)가 30배로 커져** 보여요. 최소 2~4주로 보는 걸 권해요.
- **'○○ 대비'** = 이 표의 1위, 즉 **1대당 매출이 가장 높은 국가**를 100%로 둔 비율이에요. 헤더에 그 나라 이름이 그대로 나와요.
  - ★**총매출 1위와 다른 나라일 수 있어요.** 한국은 총매출은 1위지만 1대당으로는 아래쪽이라 100%가 아니에요.
  - ⚠️ **표본이 적은 국가**(가동 대수가 기준 미만)는 `표본 적음` 배지를 달고 표 아래쪽으로 내려요. 매장 한 곳 성적이 그대로 국가 대표값이 돼서 1위로 튀거든요. 기준은 **최대 보유국의 1%**(최소 3대)라 나라 규모가 커지면 같이 올라가요. 100%와 헤더 국가명도 기준을 넘긴 나라에서만 잡아요.
  - 위 '국가별 매출' 표의 **비중(전체 대비 점유율)과도 다른 값**이에요.
- **기간 내 변동** = 이 기간에 새로 깔린 대수(+)와 지금 '중지' 상태인 대수. 신규가 많은 나라는 총 가동일이 대수 대비 짧아요 — 그래서 대당 매출이 눌려 보일 수 있어요.
- 아래 **설치 이력**(월별 신규 설치)을 펼치면 어느 달에 증설했는지 보여요. 매출이 뛴 시점과 겹치는지 보면 증설 효과를 가늠할 수 있어요.
- 분자·분모 모두 **렌탈·팝업을 뺐어요**. 행사용 장비는 잠깐만 도는데 분모에 계속 남아 왜곡돼요.
- 설치일은 CMS에 컬럼이 없어 **기기 S/N 앞 6자리**(YYMMDD)로 봐요.
- ⚠️ **'중지' 장비는 분모에서 빠져요.** CMS에 철거일이 없어 언제 멈췄는지 알 수 없어서예요.
  그래서 **오래된 기간을 볼수록 분모가 실제보다 작아** 대당 매출이 높게 보일 수 있어요.
  최근 1~3개월로 보면 가장 정확해요.
""")

        with card("🍩 국가별 매출 비중"):
            _pie = nat[["국가", "매출"]].copy()
            if len(_pie) > 7:
                _pie = pd.concat([_pie.head(7), pd.DataFrame(
                    [{"국가": f"기타 {len(nat) - 7}개국", "매출": int(nat.iloc[7:]["매출"].sum())}])],
                    ignore_index=True)
            _pie = _pie.sort_values("매출", ascending=False).reset_index(drop=True)
            css_donut(list(zip(_pie["국가"].astype(str), _pie["매출"])), PAL, size=190, hole=62, legend_fs=14)
            helpbox("""
**국가별 매출 비중 (도넛)**
- 위 표의 국가별 매출액으로 비중. 상위 7개국 + '기타 N개국' 묶음.
""")

        with card("🏆 국가별 타이틀 TOP 10 <span class='muted'>(날짜+IP)</span>", key="scard-nattitle"):
            ip_src = sales[(sales["타이틀"] != "") & sales["타이틀"].notna()]
            if ip_src.empty:
                st.info("해당 조건에 맞는 데이터가 없어요. 날짜·국가·매장 필터를 넓혀 보세요.")
            else:
                nat_choices = [str(c) for c in
                               ip_src.groupby("국가", observed=True)["매출액"].sum()
                               .sort_values(ascending=False).index.tolist()]
                sel_nat = st.selectbox("국가", nat_choices, key="ph_ip_nat_sel", label_visibility="collapsed")
                cdf = (ip_src[ip_src["국가"] == sel_nat].groupby("타이틀", observed=True)
                       .agg(매출=("매출액", "sum"), 건수=("건수", "sum"))
                       .reset_index())
                cdf = cdf[cdf["매출"] > 0]
                _fl = flag_img(sel_nat, h=14)
                st.markdown(
                    '<div style="font-size:13px;color:var(--text-2);margin:8px 0 16px;'
                    'display:flex;align-items:center;gap:2px">'
                    f'{_fl}<b style="color:var(--text)">{sel_nat}</b>'
                    '<span style="color:var(--text-3);margin:0 8px">·</span>'
                    f'총 매출 <b style="color:var(--text);margin-left:4px">{fmt_krw(int(cdf["매출"].sum()))}</b>'
                    '<span style="color:var(--text-3);margin:0 8px">·</span>'
                    f'타이틀 {len(cdf):,}개</div>', unsafe_allow_html=True)
                if cdf.empty:
                    st.info("이 국가의 타이틀 데이터가 없어요.")
                else:
                    rank_table(cdf, "타이틀", collapse_after=10)
            helpbox("""
**국가별 타이틀 TOP 10**
- 선택 국가의 `타이틀`별 매출액·건수 → 순위(TOP10 + 나머지 접기).
""")

# ════════════ 탭 4: 매장별 분석 ════════════
with tab_store:
    with card("🏬 국가별 매장 전체 순위", key="scard-storesel"):
        _cs = (sales.groupby("국가", observed=True)["매출액"].sum().sort_values(ascending=False).index.tolist()
               if "국가" in sales.columns else [])
        _cs = [str(c) for c in _cs]
        if not _cs:
            st.info("데이터가 없어요.")
        else:
            pick = st.selectbox("국가", ["전체"] + _cs, key="ph_store_country", label_visibility="collapsed")
            _src = sales if pick == "전체" else sales[sales["국가"] == pick]
            ss = (_src.groupby("매장 이름", observed=True)
                  .agg(매출=("매출액", "sum"), 건수=("건수", "sum")).reset_index())
            ss = ss[ss["매출"] > 0]
            st.caption(f"매장 {len(ss):,}개 · TOP 10 + 나머지 접기" + ("" if pick == "전체" else f" · {pick}"))
            if ss.empty:
                st.info("이 국가의 매장 데이터가 없어요.")
            else:
                hbar_list(ss, "매장 이름", collapse_after=10)
        helpbox("""
**국가별 매장 전체 순위**
- '전체' 또는 선택 국가의 `매장 이름`별 매출액 합·건수 → 순위(TOP10 + 나머지 접기).
""")

# ════════════ 탭 5: 세부 항목 검색 ════════════
with tab_detail:
    @st.fragment
    def _detail_search(date_range, selected_ips, sel_countries,
                       sel_stores, sel_brands, sel_gubuns):
        with card("🔎 세부 판매 항목 검색 <span class='muted'>(프레임 / 구좌 등)</span>"):
            st.caption("전체 거래에서 프레임·구좌 등 세부 항목을 분류별로 모아 봐요. "
                       "위 필터바(날짜·국가·매장·카테고리·IP)가 그대로 적용돼요.  "
                       "※ 같은 타이틀명이 단가만 다르게 등록된 경우(예: 마카오)는 "
                       "**「타이틀 (이름+단가별)」** 을 고르면 단가별로 나눠서 볼 수 있어요.")
            helpbox("""
**세부 판매 항목 검색**
- 선택한 `분류 기준`(프레임·구좌·타이틀 등)으로 **DuckDB에서 원거래를 직접 집계**(필터바 날짜·국가·매장·카테고리·IP 반영). 매출액 = 실결제 + 지정국가 쿠폰·코인.
- 검색어는 항목명 부분일치. 같은 타이틀명이 단가만 다른 경우 '타이틀(이름+단가별)'로 분리해 단가별로 봐요.
- 결과 표는 CSV로 내려받기 가능.
""")
            dcol1, dcol2 = st.columns([1, 2])
            with dcol1:
                sel_dim_label = st.selectbox("분류 기준", list(DETAIL_DIMS.keys()), key="ph_detail_dim")
            with dcol2:
                search_kw = st.text_input("🔍 검색어 (항목명 일부)", key="ph_detail_search",
                                          placeholder="예: 메인, 화이트, ENHYPEN, EVENT …")

            if len(date_range) == 2:
                detail_df = load_sales_detail(
                    DETAIL_DIMS[sel_dim_label], date_range[0], date_range[1],
                    ip_list=selected_ips or None,
                    countries=tuple(sel_countries), stores=tuple(sel_stores),
                    brands=tuple(sel_brands), gubuns=tuple(sel_gubuns),
                )
            else:
                detail_df = pd.DataFrame()

            if detail_df.empty:
                st.info("해당 조건에 맞는 데이터가 없어요. 날짜·국가·매장 필터를 넓혀 보세요.")
                return
            if search_kw.strip():
                detail_df = detail_df[
                    detail_df["항목"].astype(str).str.contains(search_kw.strip(), case=False, na=False)]
            if detail_df.empty:
                st.warning(f"'{search_kw}'에 대한 검색 결과가 없어요. 다른 검색어로 다시 찾아보세요.")
                return

            statrow([("검색 항목 수", f"{len(detail_df):,}개"),
                     ("합계 매출", fmt_krw(int(detail_df["매출"].sum()))),
                     ("합계 건수", f"{int(detail_df['건수'].sum()):,}건")])
            st.caption(f"매출 TOP 10 + 나머지 접기 (전체 {len(detail_df):,}개)")
            rank_table(detail_df.rename(columns={"항목": "_n"}), "_n", collapse_after=10)

            with st.expander(f"📋 전체 표 · CSV ({len(detail_df):,}개)"):
                tbl = detail_df.copy()
                tbl.insert(0, "순위", range(1, len(tbl) + 1))
                tbl["건당 평균"] = (tbl["매출"] / tbl["건수"].replace(0, 1)).round(0).astype("int64")
                tbl["비중"] = (tbl["매출"] / tbl["매출"].sum() * 100).round(1).apply(lambda x: f"{x:.1f}%")
                tbl["매출"] = tbl["매출"].apply(fmt_krw)
                tbl["건당 평균"] = tbl["건당 평균"].apply(fmt_krw)
                tbl = tbl.rename(columns={"항목": sel_dim_label})
                st.dataframe(tbl, use_container_width=True, height=460, hide_index=True)
                st.caption("※ **건당 평균** = 매출 ÷ 건수(장당 단가 아님). 한 주문에 2장 이상이면 "
                           "단가(예: 7,000원)보다 높게, 0원(코인·무료) 거래가 섞이면 낮게 보여요.")
                csv_d = detail_df.rename(columns={"항목": sel_dim_label}).to_csv(
                    index=False, encoding="utf-8-sig").encode("utf-8-sig")
                st.download_button("세부 항목 CSV 다운로드", csv_d,
                                   f"photoism_detail_{DETAIL_DIMS[sel_dim_label]}.csv", "text/csv",
                                   key="ph_detail_csv")

    _detail_search(date_range, selected_ips, sel_countries,
                   sel_stores, sel_brands, sel_gubuns)

# ════════════ 탭 6: 시간대 · 데이터 ════════════ [보류: SHOW_TAB_ETC 로 부활]
if SHOW_TAB_ETC:
    with tab_etc:
        with card("⏰ 시간대별 매출 분포"):
            df_hourly = load_hourly()
            if not df_hourly.empty and len(date_range) == 2:
                df_hourly = df_hourly[
                    (df_hourly["날짜"] >= date_range[0])
                    & (df_hourly["날짜"] <= date_range[1])
                    & (~df_hourly["취소 여부"])
                ]
            if df_hourly.empty:
                st.info("선택한 기간에 시간대 데이터가 없어요. 날짜 범위를 넓혀 보세요.")
            else:
                hourly = (
                    df_hourly[df_hourly["시간대"] >= 0]
                    .groupby("시간대")["최종 결제 금액"].sum()
                    .reindex(range(24), fill_value=0)
                )
                css_hours([int(v) for v in hourly.tolist()])
                st.caption("최고 시간대만 진하게 강조했어요."
                           + ("  ·  ℹ️ 시간대 차트는 날짜 필터만 적용돼요(국가/매장 필터 미적용)."
                              if (sel_countries or sel_stores or sel_brands) else ""))

        with st.expander("🗃 집계 데이터 보기 / 내려받기"):
            if st.checkbox("데이터 표 불러오기", key="ph_show_raw"):
                show_cols = ["날짜", "국가", "브랜드", "IP구분", "타이틀", "IP명", "매장 이름",
                             "타이틀명", "결제 단위", "건수", "최종 결제 금액", "KRW환산금액", "매출액"]
                available = [c for c in show_cols if c in df.columns]
                view = df[available].sort_values(
                    ["날짜", "매출액"], ascending=[False, False]).reset_index(drop=True)
                st.caption(f"총 {len(view):,}행 · 표는 상위 2,000행만 표시 (전체는 CSV)")
                st.dataframe(view.head(2000), use_container_width=True, height=400)
                csv_export = view.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")
                st.download_button("CSV 다운로드 (전체)", csv_export,
                                   "photoism_filtered.csv", "text/csv")
            else:
                st.caption("체크하면 현재 필터 기준 집계 데이터를 표로 불러와요.")
