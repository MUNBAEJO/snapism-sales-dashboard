# -*- coding: utf-8 -*-
"""스내피즘 매출 대시보드 — 재디자인(시안 snapism-hybrid 기준).

구조: 필터바(멀티셀렉트) + KPI 3카드 + 5탭(매출 한눈에·상품 카테고리·국가별·매장별·시간대).
'이번 달 변화'는 탭이 아니라 사이드바에 국가별로 분리.
매출 기준 = 실결제(KRW환산, 쿠폰 제외). 쿠폰·취소는 별도 KPI.
데이터 로직은 기존 로더/헬퍼를 그대로 사용(비파괴).
"""
import json
import sys
from contextlib import contextmanager
from pathlib import Path
from datetime import date, timedelta

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from guide_content import render_guide
import data_io
import auth

# ══════════════════════════════════════════════════════════════
#  디자인 시스템 (시안 토큰 이식)
# ══════════════════════════════════════════════════════════════
st.markdown("""
<style>
@import url("https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/variable/pretendardvariable-dynamic-subset.min.css");
:root{
  --bg:#f4f5f7; --surface:#fff; --surface-2:#f8fafc; --surface-3:#eef1f5;
  --border:#e7e9ee; --border-strong:#d7dae1;
  --text:#1b2330; --text-2:#5b6573; --text-3:#98a0af;
  --brand:#4f46e5; --brand-2:#6366f1; --brand-soft:#eef0fe;
  --red:#c0322b; --green:#15803d; --amber:#b45309; --sky:#38a3e8; --teal:#0f9d77;
}
/* Pretendard 강제 적용(맑은고딕 폴백 방지) — 시안의 부드러운 느낌 */
html, body, [class*="css"], [data-testid="stAppViewContainer"], [data-testid="stSidebar"],
button, input, select, textarea, label, p, span, div, h1, h2, h3, h4, li, a,
[data-baseweb], [data-testid="stMarkdownContainer"], [data-testid="stMetricValue"]{
  font-family:'Pretendard Variable','Pretendard',-apple-system,BlinkMacSystemFont,
              'Segoe UI','Malgun Gothic','Apple SD Gothic Neo',sans-serif !important;
}
html, body{ letter-spacing:-0.02em; }
/* 페이지 배경 회색(#f4f5f7) — 흰 카드가 떠 보이게(시안 표면 분리).
   ※ config.toml 의 테마 backgroundColor=#fff 가 stMain 을 흰색으로 덮으므로 메인영역까지 회색 강제. */
.stApp, [data-testid="stAppViewContainer"], [data-testid="stMain"], .stMain, section.main{
  background:var(--bg) !important; }
[data-testid="stMainBlockContainer"], .block-container{ background:transparent !important; }
/* 본문 가운데 정렬 + 시안 폭(~1060px) — layout=wide 를 강제로 좁힘 */
[data-testid="stMainBlockContainer"], .stMainBlockContainer, section.main .block-container, .block-container{
  max-width:1680px !important; margin-left:auto !important; margin-right:auto !important;
  padding-top:1.4rem !important; padding-bottom:3rem !important;
  padding-left:1.6rem !important; padding-right:1.6rem !important; }
h1{ font-size:24px !important; font-weight:800 !important; letter-spacing:-0.03em !important; color:var(--text); }
h2, h3{ letter-spacing:-0.02em !important; }
/* 카드 = 시안 톤. ※ 라우터 중첩규칙 + Streamlit 칼럼 래퍼가 섹션 카드 테두리를 지우거나
   엉뚱한 칼럼에 붙여서, 메인의 모든 border-wrapper를 무력화한 뒤
   card()·필터바(key=scard-*)에만 실제 카드 스타일을 준다. */
[data-testid="stMain"] [data-testid="stVerticalBlockBorderWrapper"]{
  border:none !important; box-shadow:none !important; background:transparent !important;
  padding:0 !important; margin:0 !important;
}
[data-testid="stMain"] [class*="st-key-scard-"]{
  border:1px solid var(--border) !important; border-radius:14px !important;
  box-shadow:0 1px 2px rgba(20,28,45,.04),0 1px 3px rgba(20,28,45,.06) !important;
  padding:15px 18px !important; margin-bottom:14px !important; background:#fff !important;
}
/* 캡션(부제)도 시안 톤 */
[data-testid="stCaptionContainer"], [data-testid="stCaptionContainer"] p{ font-size:14px !important; color:#8b95a1 !important; }
[data-testid="stDeployButton"]{ display:none !important; }
[data-testid="stElementToolbar"]{ display:none; }
[data-testid="stSidebar"]{ background:#fbfcfe; border-right:1px solid #eceff5; }
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

/* 비중막대 내장 표 (.ntbl) */
.ntbl{ border:1px solid var(--border); border-radius:12px; overflow:hidden; margin:2px 0 4px; }
.ntr{ display:grid; align-items:center; gap:10px; padding:13px 18px; border-bottom:1px solid var(--border);
      font-size:13px; color:var(--text); }
.ntr:last-child{ border-bottom:none; }
.ntr.nth{ background:var(--surface-2); font-size:11px; font-weight:700; color:var(--text-3); letter-spacing:.02em; }
.ntr:not(.nth):hover{ background:var(--surface-2); }
.ntr .r{ text-align:right; } .ntr .c{ text-align:center; }
.nname{ font-weight:700; }
/* 타이틀 상태 배지 + 판매기간 (프레임 순위표) */
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

/* 도넛 오른쪽 범례 (시안) */
.lgd-wrap{ display:flex; flex-direction:column; gap:1px; justify-content:center; height:100%; padding:8px 2px; }
.lgd{ display:flex; align-items:center; gap:9px; padding:8px 4px; border-bottom:1px solid #f2f4f8; font-size:13px; }
.lgd:last-child{ border-bottom:none; }
.lgd-dot{ width:11px; height:11px; border-radius:3px; flex:0 0 auto; }
.lgd-n{ font-weight:600; color:var(--text); }
.lgd-p{ margin-left:auto; font-weight:800; font-variant-numeric:tabular-nums; color:var(--text); }

/* 가로 막대 순위 (시안 .hbar 그대로) */
.hb-wrap{ display:flex; flex-direction:column; gap:5px; padding:4px 0; height:100%; justify-content:center; }
.hb{ display:grid; grid-template-columns:140px 1fr 112px; align-items:center; gap:12px; font-size:13px; padding:8px 0; }
.hb-n{ font-weight:600; color:var(--text-2); text-align:right; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; font-size:13px; }
.hb-track{ height:22px; background:var(--surface-3); border-radius:6px; overflow:hidden; }
.hb-track i{ display:block; height:100%; border-radius:6px; }
.hb-v{ text-align:right; font-weight:700; color:var(--text); font-variant-numeric:tabular-nums; font-size:13px; }
/* 나란한 2열 카드는 같은 높이로 — 짧은 카드가 옆 카드에 맞춰 늘어남(작아 보임 방지) */
[data-testid="stColumn"] [class*="st-key-scard-"]{ height:100% !important; }

/* ── 시안과 동일한 CSS 차트 (Plotly 대체) ── */
/* 도넛(conic-gradient) + 오른쪽 범례 */
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
/* 스택 막대 추이(flexbox) */
.legend{ display:flex; gap:16px; font-size:12px; color:var(--text-2); margin-bottom:10px; flex-wrap:wrap; }
.legend span{ display:inline-flex; align-items:center; gap:6px; }
.chart{ display:flex; align-items:flex-end; height:200px; padding:6px 4px 0; border-bottom:1px solid var(--border); }
.col{ flex:1; display:flex; flex-direction:column; justify-content:flex-end; align-items:center; height:100%; }
.stack{ width:58%; max-width:70px; display:flex; flex-direction:column; justify-content:flex-end;
        border-radius:5px 5px 0 0; overflow:hidden; }
.seg-real{ background:var(--brand-2); } .seg-cp{ background:var(--sky); }
.xlab{ font-size:11px; color:var(--text-3); margin-top:7px; font-weight:600; }
/* 시간대 막대 */
.hours{ display:flex; align-items:flex-end; gap:5px; height:180px; border-bottom:1px solid var(--border); padding-top:8px; }
.hours .hc{ flex:1; display:flex; flex-direction:column; justify-content:flex-end; align-items:center; height:100%; }
.hours .hb2{ width:70%; border-radius:3px 3px 0 0; }
.hours .hx{ font-size:9.5px; color:var(--text-3); margin-top:4px; }
/* 정보 스트립(시안 .strip) */
.strip{ font-size:12.5px; color:var(--text-2); background:var(--surface-2); border:1px solid var(--border);
        border-radius:10px; padding:9px 14px; margin-top:12px; }
.strip b{ color:var(--text); font-weight:700; }

/* ── 즉시(hover) 매출 툴팁 — 딜레이 없이 커서 올리면 바로 박스 ── */
/* 행 형태(가로막대·도넛 범례): 요소 위쪽에 즉시 박스 */
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
/* 세로 막대(추이·시간대): 막대 바로 위에 뜨는 박스(막대 높이에 맞춤) */
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

/* 사이드바 변화 */
.mv{ display:flex; align-items:center; gap:8px; font-size:12.5px; padding:6px 2px; border-bottom:1px solid #eef1f5; }
.mv:last-child{ border-bottom:none; }
.mv .t{ font-size:10px; font-weight:700; color:var(--text-3); background:var(--surface-3); padding:1px 6px;
        border-radius:5px; flex:0 0 auto; }
.mv .n{ font-weight:600; color:var(--text); overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.mv .p{ margin-left:auto; font-weight:800; font-variant-numeric:tabular-nums; flex:0 0 auto; }
.mv .up{ color:var(--green); } .mv .down{ color:var(--red); }

/* Streamlit 기본 크롬 정리 (시안 느낌으로) */
[data-testid="stToolbar"]{ display:none !important; }
#MainMenu, footer{ display:none !important; }
[data-testid="stHeader"]{ background:transparent; height:0 !important; }
/* 사이드바 토스 톤(상시 노출) */
[data-testid="stSidebar"]{ background:#ffffff !important; border-right:1px solid #e5e8eb !important; }
[data-testid="stSidebarNav"] a{ border-radius:10px !important; padding:9px 12px !important; margin:1px 0 !important; }
[data-testid="stSidebarNav"] a:hover{ background:#f2f4f8 !important; }
[data-testid="stSidebar"] hr{ border-color:#eef1f5 !important; }
/* 사이드바 접힘 상태의 펼치기(>) 버튼 — 반드시 보이고 눌리게(페이지 이동 통로) */
[data-testid="stSidebarCollapsedControl"], [data-testid="collapsedControl"]{
  display:block !important; visibility:visible !important; opacity:1 !important;
  position:fixed !important; top:10px !important; left:10px !important; z-index:999999 !important; }
[data-testid="stSidebarCollapsedControl"] button, [data-testid="collapsedControl"] button{
  background:var(--brand) !important; color:#fff !important; border-radius:10px !important;
  box-shadow:0 2px 8px rgba(79,70,229,.35) !important; width:38px !important; height:38px !important; }

/* 탭 = 시안 언더라인 스타일 */
[data-baseweb="tab-list"]{ gap:2px; border-bottom:1px solid var(--border); }
button[data-baseweb="tab"]{ padding:10px 15px; }
button[data-baseweb="tab"] p{ font-size:14px !important; font-weight:700 !important; color:var(--text-2) !important; }
button[data-baseweb="tab"][aria-selected="true"] p{ color:var(--brand) !important; }
[data-baseweb="tab-highlight"]{ background:var(--brand) !important; height:2.5px !important; }
/* 첫 탭 '매출 한눈에' = 요약이라 연한 브랜드 배경으로 구분(시안 .homie) */
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
.fbar-label{ font-size:12.5px; font-weight:700; color:var(--text-2); margin:4px 0 10px; }
/* 필터 칩(팝오버) = 시안 .chip (작고 회색) */
[data-testid="stPopover"] button, [data-testid="stPopoverButton"]{
  border:1px solid var(--border-strong) !important; background:var(--surface-2) !important;
  border-radius:8px !important; font-weight:600 !important; color:var(--text-2) !important;
  font-size:12px !important; min-height:31px !important; height:31px !important;
  padding:2px 10px !important; }
[data-testid="stPopover"] button p, [data-testid="stPopoverButton"] p{
  white-space:nowrap !important; overflow:hidden !important; text-overflow:ellipsis !important; }
/* 필터 라벨(.fbl) */
.fbl{ font-size:11px !important; font-weight:700; color:var(--text-2); margin:0 0 3px 2px; line-height:1.2; }
/* 필터바 팝오버 칩 = 칼럼 폭 꽉·높이 33 */
.st-key-scard-filter [data-testid="stPopover"]{ width:100% !important; }
.st-key-scard-filter [data-testid="stPopover"] button{
  width:100% !important; min-height:33px !important; height:33px !important;
  justify-content:space-between !important; }
/* 팝오버 안 검색+체크리스트 컴팩트 */
[data-testid="stPopover"] [data-testid="stCheckbox"]{ margin-bottom:0 !important; }
[data-testid="stPopover"] [data-testid="stCheckbox"] label{ padding:3px 2px !important; gap:8px !important; align-items:center !important; }
[data-testid="stPopover"] [data-testid="stCheckbox"] label p{ font-size:12.5px !important; }
[data-testid="stPopover"] [data-testid="stTextInput"] input{ font-size:12.5px !important; }
[data-testid="stPopover"] [data-testid="stButton"] button{ font-size:11px !important; padding:2px 6px !important;
  min-height:28px !important; height:28px !important; }
/* 필터바 '적용' 버튼(팝오버 밖) = 칩 높이와 정렬 */
.st-key-scard-filter [data-testid="stButton"] button{ min-height:33px !important; height:33px !important;
  font-size:12px !important; font-weight:700 !important; border-radius:8px !important; }
/* 필터바: 간격 좁게·바닥정렬·높이 통일(34) */
.st-key-scard-filter [data-testid="stHorizontalBlock"]{ align-items:flex-end !important; gap:0.5rem !important; }
.st-key-scard-filter [data-testid="stPopover"] button,
.st-key-scard-filter [data-testid="stDateInput"] div[data-baseweb="input"],
.st-key-scard-filter [data-testid="stButton"] button{ height:34px !important; min-height:34px !important; }
.st-key-scard-filter [data-testid="stColumn"]{ display:block !important; }
.st-key-scard-filter label{
  font-size:11px !important; font-weight:700 !important; color:var(--text-2) !important;
  margin:0 0 3px 2px !important; padding:0 !important; min-height:0 !important; line-height:1.2 !important; }
.st-key-scard-filter [data-testid="stSelectbox"],
.st-key-scard-filter [data-testid="stMultiSelect"],
.st-key-scard-filter [data-testid="stDateInput"]{ max-width:none !important; width:100% !important; }
.st-key-scard-filter [data-testid="stDateInput"] div[data-baseweb="input"]{
  min-height:33px !important; height:33px !important; border-radius:8px !important;
  background:var(--surface-2) !important; border:1px solid var(--border-strong) !important; }
.st-key-scard-filter [data-testid="stDateInput"] input{
  font-size:12px !important; font-weight:600 !important; color:var(--text-2) !important; }
.st-key-scard-filter [data-testid="stMultiSelect"] div[data-baseweb="select"]{
  min-height:33px !important; background:var(--surface-2) !important;
  border:1px solid var(--border-strong) !important; border-radius:8px !important; }
.st-key-scard-filter [data-testid="stMultiSelect"] div[data-baseweb="select"] *{ font-size:12px !important; }

/* 세그먼트 컨트롤(월/주/일·전체/아티스트/캐릭터) = 시안 .seg (작은 회색 pill·활성 흰색) */
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

/* 셀렉트박스(국가·카테고리) = 시안 .minisel (작은 회색) */
[data-testid="stSelectbox"]{ max-width:210px !important; }
[data-testid="stSelectbox"] div[data-baseweb="select"] > div:first-child{
  min-height:33px !important; height:33px !important; background:var(--surface-2) !important;
  border:1px solid var(--border-strong) !important; border-radius:8px !important;
  display:flex !important; align-items:center !important; }   /* 글자 세로 중앙정렬 */
[data-testid="stSelectbox"] div[data-baseweb="select"] > div:first-child > div{
  display:flex !important; align-items:center !important; }
[data-testid="stSelectbox"] div[data-baseweb="select"] div{ font-size:12.5px !important; font-weight:600 !important; }
/* 제목 옆 컨트롤(세그먼트·셀렉트)은 오른쪽 끝으로(시안 margin-left:auto) */
[data-testid="stElementContainer"]:has(> [data-testid="stButtonGroup"]),
[data-testid="stElementContainer"]:has(> [data-testid="stSelectbox"]){
  display:flex !important; justify-content:flex-end !important; }
/* 카드 헤더 드롭다운 = 카드 제목 옆(우상단)에 절대배치.
   제목은 모든 카드 표준(card 타이틀)이라 카드끼리 높이 일치, 드롭다운만 겹쳐 올림. */
.st-key-scard-hstore, .st-key-scard-prodsel, .st-key-scard-storesel{ position:relative; }
.st-key-scard-hstore [data-testid="stElementContainer"]:has(> [data-testid="stSelectbox"]),
.st-key-scard-prodsel [data-testid="stElementContainer"]:has(> [data-testid="stSelectbox"]),
.st-key-scard-storesel [data-testid="stElementContainer"]:has(> [data-testid="stSelectbox"]){
  position:absolute !important; top:16px !important; right:18px !important; width:auto !important;
  margin:0 !important; z-index:5 !important; }
/* 드롭다운 박스를 내용 크기로 축소(글자+화살표 딱 붙게) — 시안 컴팩트 톤 */
.st-key-scard-hstore [data-testid="stSelectbox"], .st-key-scard-prodsel [data-testid="stSelectbox"],
.st-key-scard-storesel [data-testid="stSelectbox"]{ width:auto !important; min-width:0 !important; }
.st-key-scard-hstore [data-testid="stSelectbox"] div[data-baseweb="select"],
.st-key-scard-prodsel [data-testid="stSelectbox"] div[data-baseweb="select"],
.st-key-scard-storesel [data-testid="stSelectbox"] div[data-baseweb="select"]{
  width:fit-content !important; min-width:96px !important; }
/* 칩 글자 한 줄 유지(줄바꿈 방지) */
[data-testid="stPopover"] button p, [data-testid="stPopoverButton"] p{
  white-space:nowrap !important; overflow:hidden !important; text-overflow:ellipsis !important; }
/* 기간 date_input = 다른 칩과 같은 모양(컴팩트) */
[data-testid="stDateInput"] div[data-baseweb="input"]{
  border-radius:9px !important; background:var(--surface-2) !important;
  border:1px solid var(--border-strong) !important; }
.stDateInput input{ font-size:12.5px !important; font-weight:600 !important; color:var(--text) !important; }
/* 기간(date_input)이 라벨/헬퍼 공간을 아래에 예약해 칩보다 위로 뜨는 것 방지 → 박스만 남기고 정렬 */
.st-key-scard-filter [data-testid="stDateInput"] label{ display:none !important; }
.st-key-scard-filter [data-testid="stDateInput"] [data-testid="InputInstructions"]{ display:none !important; }
.st-key-scard-filter [data-testid="stDateInput"] > div{ margin-bottom:0 !important; }

/* ── 사이드바 '관리자 전용' 카드 ── */
[data-testid="stSidebar"] .st-key-sb-admin{
  background:#f6f7ff !important; border:1px solid #e4e7fb !important; border-radius:12px !important;
  padding:11px 12px 7px !important; margin-top:10px !important;
  box-shadow:0 1px 2px rgba(79,70,229,.05) !important; }
.sb-admin-hd{ font-size:10.5px; font-weight:800; letter-spacing:.04em; color:var(--brand);
  text-transform:uppercase; margin:0 0 8px 1px; display:flex; align-items:center; gap:5px; }
.st-key-sb-admin [data-testid="stCheckbox"]{ margin-bottom:2px; }
.st-key-sb-admin [data-testid="stCheckbox"] label{ font-size:12.5px !important; font-weight:600 !important; }
/* 카드 안 환율 expander는 테두리 없이 카드에 녹아들게 */
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
  /* 넓은 표는 가로 스크롤(찌그러짐 방지) */
  [data-testid="stMarkdownContainer"]:has(.ntbl){ overflow-x:auto; -webkit-overflow-scrolling:touch; }
  .ntbl{ min-width:620px; }
  /* 가로막대 순위 — 이름·금액칸 축소 */
  .hb{ grid-template-columns:92px 1fr 82px !important; gap:8px !important; }
  .hb-n, .hb-v{ font-size:12px !important; }
  /* 도넛 + 범례 세로 스택 */
  .donut-wrap{ flex-direction:column; align-items:flex-start; gap:12px; }
  .leg2{ width:100%; }
  .chart{ height:168px; }
  /* 상단 탭 가로 스크롤 + 컴팩트 */
  [data-baseweb="tab-list"]{ overflow-x:auto; overflow-y:hidden; }
  button[data-baseweb="tab"]{ padding:8px 10px !important; }
  button[data-baseweb="tab"] p{ font-size:12.5px !important; }
  /* 범위 배너·캡션 줄바꿈 여유 */
  .scope{ font-size:11.5px; }
}
</style>
""", unsafe_allow_html=True)

BASE_DIR = Path(__file__).parent.parent
MASTER_FILE = BASE_DIR / "data" / "master.csv"
CONFIG_FILE = BASE_DIR / "config.json"
DEVICE_FILE = BASE_DIR / "data" / "devices_snapism.parquet"   # device_ingest_snapism.py

# 어드민 국가코드 → 매출 데이터의 '국가' 표기 (매출이 발생한 국가만)
CC_TO_NAT = {"KR": "대한민국", "JP": "일본", "CN": "중국", "TW": "대만", "HK": "홍콩",
             "TH": "태국", "ID": "인도네시아", "MY": "말레이시아", "VN": "베트남"}

CURRENCY_SYMBOLS = {
    "KRW": "₩", "CNY": "¥", "JPY": "¥", "IDR": "Rp", "TWD": "NT$", "THB": "฿",
    "HKD": "HK$", "MYR": "RM", "USD": "$", "EUR": "€", "GBP": "£", "VND": "₫",
    "PHP": "₱", "SGD": "S$", "AUD": "A$", "CAD": "C$", "AED": "AED", "MXN": "$",
    "PEN": "S/", "CLP": "$", "LAK": "₭", "MNT": "₮", "MOP": "MOP$", "BND": "B$",
}
COUNTRY_ISO = {
    "대한민국": "kr", "한국": "kr", "일본": "jp", "중국": "cn", "대만": "tw",
    "인도네시아": "id", "홍콩": "hk", "태국": "th", "말레이시아": "my",
}
PAL = ["#6366f1", "#b45309", "#0f9d77", "#d24d8b", "#38a3e8", "#7c77ee", "#c98a2e", "#5f6b7a"]
BRAND, BRAND2, SKY = "#4f46e5", "#6366f1", "#38a3e8"


def flag_img(name, h=13):
    iso = COUNTRY_ISO.get(str(name).strip())
    if not iso:
        return ""
    return (f'<img src="https://flagcdn.com/40x30/{iso}.png" height="{h}" '
            'style="vertical-align:middle;margin-right:7px;border:1px solid #eee;border-radius:2px;">')


def load_config():
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def load_exchange_rates():
    return load_config().get("exchange_rates", {"KRW": 1})


@st.cache_data(ttl=900, max_entries=1)   # 파일 버전 키 → 최신 1개만 유효(옛 항목은 메모리만 차지)
def _load_data(_v):
    if not MASTER_FILE.exists():
        return pd.DataFrame()
    df = data_io.read_master(MASTER_FILE)
    df["날짜"] = pd.to_datetime(df["날짜"], errors="coerce").dt.date
    df["결제일시"] = pd.to_datetime(df["결제일시"], format="%Y.%m.%d %H:%M", errors="coerce")
    df["취소 여부"] = df["취소 여부"].astype(str).str.lower().isin(["true", "1", "yes"])
    for col in ["최종 결제 금액", "상품 단가", "쿠폰 할인 금액"]:
        df[col] = pd.to_numeric(df.get(col, 0), errors="coerce").fillna(0).astype(int)
    ex = load_exchange_rates()
    df["결제 단위"] = df["결제 단위"].fillna("KRW").astype(str).str.strip()
    df["환율"] = df["결제 단위"].map(ex).fillna(1)
    df["KRW환산금액"] = (df["최종 결제 금액"] * df["환율"]).round(0).astype(int)   # 실결제(원화)
    df["쿠폰KRW"] = (df["쿠폰 할인 금액"] * df["환율"]).round(0).astype(int)
    df["정산금액"] = df["KRW환산금액"] + df["쿠폰KRW"]
    df["총원화금액"] = df["최종 결제 금액"] + df["쿠폰 할인 금액"]
    return df


def load_data():
    return _load_data(data_io.file_version(MASTER_FILE))


def paid_sales(df):
    return df[~df["취소 여부"] & (df["최종 결제 금액"] > 0)]


def coupon_txns(df):
    return df[~df["취소 여부"] & (df["최종 결제 금액"] == 0) & (df["쿠폰 할인 금액"] > 0)]


# ── 키오스크(스내피즘 어드민) ──────────────────────────────────
# 포토이즘과 어드민이 아예 달라 별도 파일이다. 대신 이쪽은 **계약 기간(시작~종료)**이
# 있어서 가동 구간을 어림하지 않고 정확히 자를 수 있다. (device_ingest_snapism.py)
@st.cache_data(ttl=1800, show_spinner=False, max_entries=1)
def _load_devices(_mtime):
    if not DEVICE_FILE.exists():
        return pd.DataFrame()
    try:
        d = pd.read_parquet(DEVICE_FILE, columns=["국가코드", "가동중", "테스트장비", "렌탈",
                                                  "시작일", "종료일", "매출매장명"])
        d = d[~d["테스트장비"]].copy()
        for c in ("시작일", "종료일"):
            d[c] = pd.to_datetime(d[c], errors="coerce")
        return d.reset_index(drop=True)
    except Exception:
        return pd.DataFrame()


def load_devices():
    try:
        _m = DEVICE_FILE.stat().st_mtime
    except Exception:
        _m = 0.0
    return _load_devices(_m)


def device_days(dev, p0, p1):
    """국가코드별 '대·일'(가동 키오스크 × 실가동일수)·대수·기간 내 신규/종료.

    계약 [시작일, 종료일] 과 조회기간의 겹치는 날짜만 센다. 포토이즘은 철거일이 없어
    중지 장비를 통째로 뺐지만, 스내피즘은 계약 종료일이 있어 그럴 필요가 없다."""
    empty = pd.DataFrame(columns=["국가코드", "대수", "대일", "신규", "종료"])
    if dev.empty or not p0 or not p1:
        return empty
    s0, s1 = pd.Timestamp(p0), pd.Timestamp(p1)
    d = dev
    beg = d["시작일"].fillna(s0).clip(lower=s0)
    # ★계약 종료일 ≠ 폐점. 가맹 계약이 대부분 1년이라 오늘도 89대가 '종료일'을 맞는데,
    #   그건 갱신일이지 문 닫는 날이 아니다. 실제로 끝난 건 운영 상태가 '가맹 해지'인 것뿐이라
    #   해지 매장만 종료일로 자르고, 나머지는 조회기간 끝까지 돌린 것으로 본다.
    closed = ~d["가동중"]
    end = d["종료일"].where(closed).fillna(s1).clip(upper=s1)
    days = (end - beg).dt.days + 1
    t = pd.DataFrame({"국가코드": d["국가코드"], "대일": days,
                      "신규": d["시작일"].between(s0, s1).astype(int),
                      "종료": (closed & d["종료일"].between(s0, s1)).astype(int)})
    t = t[t["대일"] > 0]
    if t.empty:
        return empty
    return (t.groupby("국가코드").agg(대수=("대일", "size"), 대일=("대일", "sum"),
                                      신규=("신규", "sum"), 종료=("종료", "sum"))
            .reset_index())


def fmt_krw(n):
    return f"₩{int(n):,}"


def fmt_orig(amount, currency):
    sym = CURRENCY_SYMBOLS.get(str(currency).strip(), str(currency) + " ")
    return f"{sym}{int(amount):,}"


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
    # 실제 카드에만 st-key-scard-* 클래스를 달아 CSS에서 카드 테두리를 정확히 겨냥한다.
    # (Streamlit 칼럼 래퍼가 같은 testid 라 leaf 판별로는 구분 불가)
    if key is None:
        _CARDN[0] += 1
        key = f"scard-{_CARDN[0]}"
    c = st.container(border=True, key=key)
    if title:
        c.markdown(f'<div class="ct">{title}</div>', unsafe_allow_html=True)
    with c:
        yield


def style_fig(fig, height, legend=False):
    """시안 Plotly 스타일 — 회색 격자 없음·투명 배경·여백 최소·y축 눈금 숨김."""
    fig.update_layout(
        height=height,
        font=dict(family="Pretendard, Malgun Gothic, sans-serif", size=12, color="#5b6573"),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        margin=dict(t=26 if legend else 10, b=6, l=8, r=8),
        showlegend=legend,
        hoverlabel=dict(font_size=12, font_family="Pretendard, Malgun Gothic, sans-serif"),
    )
    if legend:
        fig.update_layout(legend=dict(orientation="h", y=1.16, x=0, bgcolor="rgba(0,0,0,0)", font_size=11))
    fig.update_xaxes(showgrid=False, zeroline=False, showline=False, ticks="",
                     tickfont=dict(size=11, color="#98a0af"), title=None)
    fig.update_yaxes(showgrid=False, zeroline=False, showline=False, ticks="",
                     showticklabels=False, title=None)
    return fig


def cat3(series):
    s = series.astype(str).str.strip()
    return s.where(s.isin(["아티스트", "캐릭터"]), "기타")


def donut(dfg, names, values, height=250, showlegend=True):
    fig = px.pie(dfg, names=names, values=values, hole=0.62, color_discrete_sequence=PAL)
    fig.update_traces(sort=False, textinfo="none",
                      marker=dict(line=dict(color="#fff", width=2)),
                      hovertemplate="%{label}<br>%{value:,}원 (%{percent})<extra></extra>")
    style_fig(fig, height, legend=showlegend)
    if showlegend:
        fig.update_layout(legend=dict(orientation="h", y=-0.08, x=0.5, xanchor="center", font_size=10))
    return fig


def legend_list(dframe, name_col):
    """도넛 오른쪽 범례(이름 + %). 도넛 슬라이스 색(PAL)과 순서 일치."""
    d = dframe.sort_values("매출", ascending=False).reset_index(drop=True)
    tot = d["매출"].sum()
    html = '<div class="lgd-wrap">'
    for i, r in d.iterrows():
        pct = (r["매출"] / tot * 100) if tot else 0
        html += (f'<div class="lgd"><span class="lgd-dot" style="background:{PAL[i % len(PAL)]}"></span>'
                 f'<span class="lgd-n">{r[name_col]}</span><span class="lgd-p">{pct:.1f}%</span></div>')
    st.markdown(html + "</div>", unsafe_allow_html=True)


def hbar_list(dframe, name_col, top=None, collapse_after=None):
    """시안 TOP 스타일 가로막대(이름 | 트랙+채움 | 금액). 1위=브랜드색, 나머지=연한 블루.
    collapse_after=N 이면 상위 N개만 보이고 나머지는 '더보기' 접기."""
    d = dframe.sort_values("매출", ascending=False).reset_index(drop=True)
    if top:
        d = d.head(top)
    mx = d["매출"].max() or 1

    def _rows(sub):
        h = '<div class="hb-wrap">'
        for i, r in sub.iterrows():
            w = max(3, r["매출"] / mx * 100)
            col = BRAND if i == 0 else "#a9c7ef"   # 전체 1위만 브랜드색(원본 인덱스 유지)
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
    """비중막대 내장 순위표(.ntbl). collapse_after=N 이면 상위 N개만 보이고 나머지는 '더보기' 접기.
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


def css_donut(pairs, colors, size=128, hole=38, legend_fs=13, sub=None):
    """시안과 동일한 CSS conic-gradient 도넛 + 오른쪽 범례.
    pairs=[(name, value)] (그리는 순서), colors=슬라이스별 색."""
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


def css_trend(rows, gran):
    """시안과 동일한 CSS 스택 막대 추이. rows=[(label, 실결제, 쿠폰)]."""
    if not rows:
        st.info("선택한 조건에 맞는 데이터가 없어요. 기간·필터를 바꿔 보세요.")
        return
    mx = max((r[1] + r[2]) for r in rows) or 1
    gap = "6px" if gran == "일" else ("12px" if gran == "주" else "26px")
    fs = "10px" if gran == "일" else "11px"
    cols = ""
    for label, real, cp in rows:
        tot = real + cp
        h = max(2, round(tot / mx * 100))
        cpp = round(cp / tot * 100) if tot else 0
        _tb = min(h, 80)   # 막대가 아주 높으면 툴팁이 카드 밖으로 나가지 않게 상한
        _tip = f'{label} · 실결제 {fmt_krw(real)}' + (f' · 쿠폰 {fmt_krw(cp)}' if cp else '')
        cols += (f'<div class="col"><div class="vtip" style="bottom:{_tb}%">{_tip}</div>'
                 f'<div class="stack" style="height:{h}%">'
                 f'<div class="seg-cp" style="height:{cpp}%"></div>'
                 f'<div class="seg-real" style="height:{100 - cpp}%"></div></div>'
                 f'<div class="xlab" style="font-size:{fs}">{label}</div></div>')
    st.markdown(
        '<div class="legend"><span><i class="dot" style="background:var(--brand-2)"></i>실결제</span>'
        '<span><i class="dot" style="background:var(--sky)"></i>쿠폰 할인</span></div>'
        f'<div class="chart" style="gap:{gap}">{cols}</div>', unsafe_allow_html=True)


def css_hours(vals):
    """시안과 동일한 시간대(00~23) 막대. 최고 시간대만 진하게. vals=길이24."""
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


# ══════════════════════════════════════════════════════════════
#  데이터 로드
# ══════════════════════════════════════════════════════════════
df_all = load_data()
ex_rates = load_exchange_rates()

st.title("📊 스내피즘 매출 대시보드")
st.caption("기간·국가·매장·상품·IP를 골라 매출을 봐요. 매출 = 실결제(쿠폰 제외) 기준이에요.")

if df_all.empty:
    st.warning("아직 불러온 매출 데이터가 없어요. `raw` 폴더에 CSV를 넣고 `데이터추가.bat`을 실행한 뒤 새로고침해 주세요.")
    st.stop()

last_date = df_all["날짜"].max()
first_date = df_all["날짜"].min()

# ══════════════════════════════════════════════════════════════
#  사이드바: 필터(멀티셀렉트)
# ══════════════════════════════════════════════════════════════
KNOWN_COUNTRIES = ["대한민국", "일본", "중국", "대만", "인도네시아", "홍콩", "태국", "말레이시아"]


def _uniq(col):
    if col not in df_all.columns:
        return []
    s = df_all[col].dropna().astype(str).str.strip()
    return sorted([v for v in s.unique() if v and v != "nan"])


_country_opts = sorted(set(_uniq("국가")) | set(KNOWN_COUNTRIES)) if "국가" in df_all.columns else []
_store_all = _uniq("매장 이름")
_prod_opts = _uniq("상품 카테고리")
_ip_opts = _uniq("프레임 이름")


@st.cache_data(ttl=900, max_entries=1)   # 파일 버전 키 → 최신 1개만 유효
def _stores_by_country(_v):
    """국가 → 매장 목록 (매장 필터를 선택 국가로 좁히기용)."""
    if "국가" not in df_all.columns or "매장 이름" not in df_all.columns:
        return {}
    out = {}
    for c, grp in df_all.dropna(subset=["국가"]).groupby("국가"):
        vals = sorted(str(v) for v in grp["매장 이름"].dropna().unique())
        out[str(c)] = [v for v in vals if v and v != "nan"]
    return out


_sbc = _stores_by_country(data_io.file_version(MASTER_FILE))


def cbfilter(col, label, options, key):
    """검색 + 체크박스 다중선택 필터. 선택 단일출처 = 각 체크박스 위젯(key=…__cb__옵션).
    목록을 항상 펼쳐 보여주고(상위 200개), 검색은 좁히는 용도. 선택 리스트 반환."""
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
                st.session_state[pfx + str(o)] = True
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


# ── 필터바를 @st.fragment 로 격리 → 체크는 이 조각만 가볍게 재실행, '적용'에서 본문 갱신 ──
default_start = max(last_date - timedelta(days=29), first_date)


@st.fragment
def _filterbar():
    with st.container(border=True, key="scard-filter"):
        # 필터(5개)는 폭을 넉넉히 채우고 오른쪽 스페이서는 작게(포토이즘 톤)
        _fb = st.columns([1.05, 0.95, 0.95, 0.95, 0.95, 0.55, 1.35], gap="small")
        with _fb[0]:
            st.markdown('<div class="fbl">기간</div>', unsafe_allow_html=True)
            st.date_input("기간", value=[default_start, last_date],
                          min_value=first_date, max_value=last_date,
                          key="f_date", label_visibility="collapsed")
        cbfilter(_fb[1], "국가", _country_opts, "f_country")
        _dc = [c for c in _country_opts if st.session_state.get(f"f_country__cb__{c}", False)]
        _std = (sorted(set().union(*[set(_sbc.get(c, [])) for c in _dc])) if _dc else _store_all)
        cbfilter(_fb[2], "매장", _std, "f_store")
        cbfilter(_fb[3], "상품", _prod_opts, "f_prod")
        cbfilter(_fb[4], "IP", _ip_opts, "f_ip")
        with _fb[5]:
            st.markdown('<div class="fbl">&nbsp;</div>', unsafe_allow_html=True)
            if st.button("✓ 적용", key="f_apply", use_container_width=True, type="primary"):
                st.rerun()


_filterbar()

# ── 적용된 필터 = 현재 위젯 상태 (체크 중엔 본문 안 바뀜) ──
_dv = st.session_state.get("f_date", [default_start, last_date])
date_range = list(_dv) if isinstance(_dv, (list, tuple)) else [default_start, last_date]
sel_country = [o for o in _country_opts if st.session_state.get(f"f_country__cb__{o}", False)]
if sel_country:
    _store_opts = sorted(set().union(*[set(_sbc.get(c, [])) for c in sel_country]))
else:
    _store_opts = _store_all
sel_store = [o for o in _store_opts if st.session_state.get(f"f_store__cb__{o}", False)]
sel_prod = [o for o in _prod_opts if st.session_state.get(f"f_prod__cb__{o}", False)]
sel_ip = [o for o in _ip_opts if st.session_state.get(f"f_ip__cb__{o}", False)]

_cfg = load_config()

# ── 필터 적용 ──
df = df_all.copy()
if len(date_range) == 2:
    df = df[(df["날짜"] >= date_range[0]) & (df["날짜"] <= date_range[1])]
if sel_country and "국가" in df.columns:
    df = df[df["국가"].isin(sel_country)]
if sel_store:
    df = df[df["매장 이름"].isin(sel_store)]
if sel_prod:
    df = df[df["상품 카테고리"].isin(sel_prod)]
if sel_ip:
    df = df[df["프레임 이름"].isin(sel_ip)]

sales = paid_sales(df)
coupons = coupon_txns(df)
cpn_all = pd.concat([coupons, sales[sales["쿠폰 할인 금액"] > 0]])


# ── 타이틀 판매기간·상태 (프레임 순위표에 표시) ──────────────
# 매출이 빠졌을 때 '끝나서'인지 '안 끝났는데'인지 가르려고 Jira 종료일을 함께 본다.
# ★ 기간으로 자르지 않은 df_all 을 넘긴다 — 기간으로 자르면 첫 거래일이
#   전부 기간 시작일이 돼서 죄다 '신규'로 나온다.
# max_entries=16 — 기간·국가·매장 조합마다 항목이 생긴다(반환값은 작은 dict).
@st.cache_data(ttl=900, show_spinner=False, max_entries=16)
def _title_status(_v, _p0, _p1, _countries, _stores):
    from title_runs import title_status
    from jira_ip_dates import fetch_ip_dates
    base = paid_sales(df_all)
    if _countries:
        base = base[base["국가"].isin(list(_countries))]
    if _stores:
        base = base[base["매장 이름"].isin(list(_stores))]
    try:
        # brand="all" 인 이유 — Jira 브랜드 필드로는 거를 수 없다.
        # 스내피즘에서 팔린 IP인데 티켓 브랜드가 Photoism 인 경우가 많고
        # (TREASURE·tripleS·KISS OF LIFE 등), 아예 비어 있는 것도 있다(10CM).
        # brand="snapism" 으로 좁히면 매출 커버리지가 93% → 82% 로 떨어진다.
        # 엉뚱한 티켓이 붙는 건 '런 기간과 실제로 겹칠 때만 연결' 규칙이 막아준다.
        jira = fetch_ip_dates(brand="all", force_refresh=False)
    except Exception:
        jira = {}          # Jira 가 죽어도 판매기간(실측)은 그대로 나온다
    return title_status(base, jira, _p0, _p1)


try:
    _tstat = _title_status(data_io.file_version(MASTER_FILE),
                           date_range[0] if len(date_range) == 2 else None,
                           date_range[1] if len(date_range) == 2 else None,
                           tuple(sel_country), tuple(sel_store))
except Exception:
    _tstat = {}

# ══════════════════════════════════════════════════════════════
#  관리자 전용: '계산 방식 설명' 토글 + helpbox 헬퍼
#  - 소유자(auth.is_owner)에게만 사이드바 체크박스를 노출.
#  - 체크 시에만 각 카드 아래에 '이 값이 어떻게 계산되는지'를 접기(expander)로 표시.
#  - 일반 사용자/토글 OFF면 아예 렌더링 안 됨(흔적·부하 없음).
#  ※ expander 중첩 불가 → helpbox 는 다른 expander(더보기·원본) 바깥(카드/섹션 레벨)에 둔다.
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
_period_days = ((date_range[1] - date_range[0]).days + 1) if len(date_range) == 2 else "-"
_dr = (f"{date_range[0]} ~ {date_range[1]}" if len(date_range) == 2 else "전체")
rev_real = int(sales["KRW환산금액"].sum())
cpn_krw = int(cpn_all["쿠폰KRW"].sum())
cpn_cnt = int(len(cpn_all))
cancel_krw = int(df[df["취소 여부"]]["KRW환산금액"].sum())

st.markdown(
    '<div class="kpis">'
    f'<div class="kpi hero"><div class="l">조회기간 매출 (합계)</div>'
    f'<div class="v num">{fmt_krw(rev_real)}</div>'
    f'<div class="d">{_dr} · {_period_days}일 · 실결제 기준</div></div>'
    f'<div class="kpi"><div class="l">쿠폰 매출 (할인)</div>'
    f'<div class="v num">{fmt_krw(cpn_krw)}</div><div class="d">{cpn_cnt:,}건</div></div>'
    f'<div class="kpi"><div class="l">취소 매출</div>'
    f'<div class="v num">{fmt_krw(cancel_krw)}</div><div class="d">환불·취소분</div></div>'
    '</div>', unsafe_allow_html=True)

_scope_bits = []
if sel_country:
    _scope_bits.append("국가: " + " · ".join(sel_country))
if sel_store:
    _scope_bits.append("매장: " + " · ".join(sel_store[:4]) + (" 외" if len(sel_store) > 4 else ""))
if sel_prod:
    _scope_bits.append("상품: " + " · ".join(sel_prod))
if sel_ip:
    _scope_bits.append("IP: " + " · ".join(sel_ip[:4]) + (" 외" if len(sel_ip) > 4 else ""))
if _scope_bits:
    st.markdown('<div class="scope">🌐 범위 — ' + "  |  ".join(_scope_bits) + '</div>', unsafe_allow_html=True)

helpbox("""
**KPI 3카드 — 조회기간 매출 · 쿠폰 매출 · 취소 매출**

**공통 기준 (이하 모든 카드 동일)**
- **원본**: 스내피즘 매장 거래 상세(매일 오전 9시 자동 수집) · 15분 캐시(`ttl=900`).
- **환율**: `config.json` 실시간 환율표로 결제 통화(`결제 단위`)를 원화 환산 → `KRW환산금액 = 최종 결제 금액 × 환율`.
- **필터 반영**: 필터바(기간·국가·매장·상품·IP)로 거른 뒤 계산. 미선택 = 전체.

**각 카드 계산**
- **조회기간 매출(합계)** = `실결제` 합. 실결제 = *취소 아님* & *최종 결제 금액 > 0* 거래의 `KRW환산금액` 합 → **쿠폰·취소는 제외**.
- **쿠폰 매출(할인)** = 쿠폰이 붙은 모든 거래(`cpn_all`)의 `쿠폰KRW`(= 쿠폰 할인 금액 × 환율) 합. 건수 = 해당 거래 수.
- **취소 매출** = `취소 여부 = True` 거래의 `KRW환산금액` 합 (참고용 — 매출에는 이미 미포함).

**검증** — 이 '조회기간 매출'이 아래 모든 매출 차트·표의 기준값이에요. 각 차트를 국가/카테고리 등으로 쪼갠 합계를 더하면 이 값과 일치해야 정상.
""")

# ══════════════════════════════════════════════════════════════
#  사이드바: 실시간 환율 (접기) — 소유자(본인)만 노출
#  ※ '이번 달 변화'는 잠시 제거 — 나중에 사이드에 다시 추가 예정
#    (전월 동기 대비 mover 로직·`.mv` CSS 는 그대로 남겨둠)
# ══════════════════════════════════════════════════════════════
if _is_owner:
    with _sb_admin:
        with st.expander("💱 실시간 환율", expanded=False):
            if _cfg.get("rates_updated"):
                st.caption(f"업데이트 {_cfg.get('rates_updated')}")
            for _cur, _rate in ex_rates.items():
                if _cur != "KRW":
                    st.caption(f"1 {_cur} = ₩{_rate:,.2f}")

# ══════════════════════════════════════════════════════════════
#  탭 5개
# ══════════════════════════════════════════════════════════════
# [보류] '시간대 · 데이터' 탭 — 숨김 처리(코드·데이터는 그대로 보존).
#         다시 살리려면 SHOW_TAB_ETC = True 로만 바꾸면 됨.
SHOW_TAB_ETC = False
_tab_labels = ["📊 매출 한눈에", "🧩 상품 카테고리 분석", "🌏 국가별 분석", "🏬 매장별 분석"]
if SHOW_TAB_ETC:
    _tab_labels.append("⏰ 시간대 · 데이터")
_tabs = st.tabs(_tab_labels)
tab_home, tab_cat, tab_nat, tab_store = _tabs[0], _tabs[1], _tabs[2], _tabs[3]
tab_etc = _tabs[4] if SHOW_TAB_ETC else None

# ════════════ 탭 1: 매출 한눈에 ════════════
with tab_home:
    sec("1", "매출 동향", "잘 가고 있나? — 기간별 실결제·쿠폰 흐름")
    with card():
        # @st.fragment — 기간(월·주·일) 토글을 눌러도 이 조각만 다시 그린다.
        # 없으면 전체 재실행 → st.tabs(1.45)가 선택을 못 기억해 첫 탭으로 튕긴다.
        @st.fragment
        def _trend():  # 기간 토글 → 매출 추이 차트
            _th, _tg = st.columns([2.4, 1])
            with _th:
                st.markdown('<div class="ct" style="margin-bottom:0">📈 매출 추이</div>', unsafe_allow_html=True)
            with _tg:
                gran = st.segmented_control("기간", ["월", "주", "일"], default="월",
                                            key="trend_gran", label_visibility="collapsed") or "월"

            def _pkey(dates, g):
                d = pd.to_datetime(dates)
                return d.dt.to_period("M") if g == "월" else (d.dt.to_period("W") if g == "주" else d.dt.date)

            s_paid = sales.assign(_p=_pkey(sales["날짜"], gran)).groupby("_p")["KRW환산금액"].sum().rename("실결제")
            _cp = cpn_all.assign(_p=_pkey(cpn_all["날짜"], gran)).groupby("_p")["쿠폰KRW"].sum().rename("쿠폰")
            trend = pd.concat([s_paid, _cp], axis=1).fillna(0).sort_index()
            if trend.empty:
                css_trend([], gran)
            else:
                trend = trend.reset_index()
                if gran == "월":
                    trend["label"] = trend["_p"].apply(lambda p: f"{p.year}.{p.month:02d}")
                elif gran == "주":
                    trend["label"] = trend["_p"].apply(lambda p: p.start_time.strftime("%m/%d") + "주")
                else:
                    trend["label"] = trend["_p"].astype(str)
                css_trend(list(zip(trend["label"], trend["실결제"].astype(int),
                                   trend["쿠폰"].astype(int))), gran)
                # 시안: 차트 아래 인사이트 한 줄 (예: 월 단위 · 6월 ₩329M → 7월(10일) ₩45M, 월초라 낮아요)
                if gran == "월" and len(trend) >= 2:
                    _pp, _lp = trend["_p"].iloc[-2], trend["_p"].iloc[-1]
                    _pv, _cv = int(trend["실결제"].iloc[-2]), int(trend["실결제"].iloc[-1])
                    _end = date_range[1] if len(date_range) == 2 else last_date
                    _partial = (_lp.year == _end.year and _lp.month == _end.month
                                and _end.day < _lp.days_in_month)
                    _txt = (f"월 단위 · {_pp.month}월 ₩{_pv / 1e6:,.0f}M → "
                            f"{_lp.month}월{f'({_end.day}일)' if _partial else ''} ₩{_cv / 1e6:,.0f}M")
                    if _partial and _cv < _pv:
                        _txt += ", 월초라 낮아요" if _end.day <= 12 else " (진행 중이에요)"
                    st.caption(_txt)
            helpbox("""
    **매출 추이 (실결제 + 쿠폰, 월/주/일)**
    - **실결제 막대** = `실결제` 거래를 기간(월=`to_period('M')`, 주=`to_period('W')`, 일=날짜)으로 묶어 `KRW환산금액` 합.
    - **쿠폰 할인 막대** = 같은 기간으로 `cpn_all`의 `쿠폰KRW` 합. 실결제 위에 쌓아 정가 대비 할인 규모를 표시.
    - **하단 인사이트** = '월' 보기에서 직전월 → 최근월 실결제 증감. 최근월이 진행 중이면 `(N일)`로 부분집계임을 표기.
    - ※ 공통 기준(원본·환율·실결제 정의)은 상단 'KPI 카드' 설명 참고.
    """)

        _trend()

    sec("2", "무엇이 매출을 만드나", "비중 — 어떤 상품·종류가 매출을 끄나")
    _c1, _c2 = st.columns(2)
    with _c1:
        with card("🧩 상품 카테고리 비중"):
            pc = (sales.groupby("상품 카테고리")["KRW환산금액"].sum().rename("매출")
                  .reset_index().sort_values("매출", ascending=False))
            if len(pc) > 4:   # 시안: 요약에선 TOP3 + '기타 N종' 묶음 (전체는 상세 탭)
                pc = pd.concat([pc.head(3), pd.DataFrame([{
                    "상품 카테고리": f"기타 {len(pc) - 3}종",
                    "매출": int(pc.iloc[3:]["매출"].sum())}])], ignore_index=True)
                pc = pc.sort_values("매출", ascending=False).reset_index(drop=True)
            if pc["매출"].sum() > 0:
                css_donut(list(zip(pc["상품 카테고리"], pc["매출"])),
                          ["var(--brand-2)", "var(--amber)", "#7c77ee", "#c7ccd6"])
            else:
                st.info("데이터가 없어요.")
            helpbox("""
**상품 카테고리 비중**
- 실결제 거래를 `상품 카테고리`로 묶어 `KRW환산금액` 합 → 비중(도넛).
- 요약 화면이라 **매출 상위 3종 + '기타 N종' 묶음**만 표시. 전체는 '상품 카테고리 분석' 탭.
""")
    with _c2:
        with card("🎨 아티스트/캐릭터 비중"):
            _s = sales.assign(_c=cat3(sales["카테고리"]))
            ac_full = (_s.groupby("_c")["KRW환산금액"].sum().rename("매출").reset_index()
                       .sort_values("매출", ascending=False))
            # 시안: 도넛은 아티스트·캐릭터 딱 2조각(기타는 캡션으로만)
            ac = ac_full[ac_full["_c"].isin(["아티스트", "캐릭터"]) & (ac_full["매출"] > 0)]
            if not ac.empty:
                _m = {r["_c"]: int(r["매출"]) for _, r in ac_full.iterrows()}
                _sub = "아티스트 " + fmt_krw(_m.get("아티스트", 0)) + " · 캐릭터 " + fmt_krw(_m.get("캐릭터", 0))
                if _m.get("기타", 0) > 0:
                    _sub += f" · 기타 {fmt_krw(_m['기타'])} 제외"
                css_donut(list(zip(ac["_c"], ac["매출"])),
                          ["var(--brand-2)", "var(--teal)"], sub=_sub)
            else:
                st.info("데이터가 없어요.")
            helpbox("""
**아티스트/캐릭터 비중**
- 거래의 `카테고리` 값을 `cat3()`으로 **아티스트 / 캐릭터 / 기타** 3분류로 정규화한 뒤 `KRW환산금액` 합.
- 도넛은 **아티스트·캐릭터 2조각만** 그리고, '기타'는 조각에서 빼고 캡션에 금액만 표기.
""")

    with card("🖼 카테고리별 TOP 프레임(IP)"):
        _fsrc = sales[sales["프레임 이름"].astype(str).str.strip().replace("nan", "").ne("")]
        fr = _fsrc.groupby("프레임 이름")["KRW환산금액"].sum().rename("매출").reset_index()
        fr = fr[fr["매출"] > 0]
        if not fr.empty:
            hbar_list(fr, "프레임 이름", top=5)
        else:
            st.info("프레임 데이터가 없어요.")
        helpbox("""
**카테고리별 TOP 프레임(IP)**
- 실결제 거래 중 `프레임 이름`이 비어있지 않은 것만 대상으로 `KRW환산금액` 합 → 상위 5개.
- '프레임 이름' = 사진 프레임(=IP) 식별자.
""")

    sec("3", "어디서 파나", "지역 — 국가·매장별 매출 (원화 기준)")
    _n1, _n2 = st.columns(2)
    with _n1:
        with card("🌏 국가별 매출 TOP 6"):
            nat6 = (sales.groupby("국가")["KRW환산금액"].sum().rename("매출").reset_index()
                    ) if "국가" in sales.columns else pd.DataFrame()
            if not nat6.empty and nat6["매출"].sum() > 0:
                hbar_list(nat6, "국가", top=6)
            else:
                st.info("데이터가 없어요.")
            # 쿠폰으로만 들어온 국가(실결제 0) — 홈에서도 놓치지 않게 요약 스트립(대만 케이스)
            _paid_nat = set(nat6[nat6["매출"] > 0]["국가"].astype(str)) if not nat6.empty else set()
            if "국가" in coupons.columns and not coupons.empty:
                _co = [(str(k), int(v)) for k, v
                       in coupons.groupby("국가")["쿠폰KRW"].sum().sort_values(ascending=False).items()
                       if str(k) not in _paid_nat and v > 0]
                if _co:
                    _bits = " · ".join(f'{flag_img(k)}{k} <b>{fmt_krw(v)}</b>' for k, v in _co[:6])
                    st.markdown('<div class="strip">🎟 쿠폰으로만 들어온 국가 (실결제 0) — '
                                + _bits + '</div>', unsafe_allow_html=True)
            helpbox("""
**국가별 매출 TOP 6**
- 실결제 거래를 `국가`로 묶어 `KRW환산금액`(원화) 합 → 상위 6개국. 나라 비교는 항상 원화 기준.
- 하단 🎟 스트립 = 실결제 0(전액 쿠폰)이라 위 순위엔 안 잡히는 국가의 쿠폰 매출(대만 등).
""")
    with _n2:
        with card("🏬 국가별 매출 TOP 5 매장", key="scard-hstore"):
            # @st.fragment — 안의 위젯을 조작해도 이 조각만 다시 그린다.
            # 없으면 전체 재실행 → st.tabs(1.45)가 선택을 못 기억해 첫 탭으로 튕긴다.
            @st.fragment
            def _home_store():  # 국가 선택 → TOP5 매장
                _opts = (sales.groupby("국가")["KRW환산금액"].sum().sort_values(ascending=False).index.tolist()
                         if "국가" in sales.columns else [])
                if _opts:
                    _pick = st.selectbox("국가", _opts, key="home_store_country", label_visibility="collapsed")
                    _ss = (sales[sales["국가"] == _pick].groupby("매장 이름")
                           .agg(매출=("KRW환산금액", "sum"), 건수=("KRW환산금액", "count"))
                           .reset_index().sort_values("매출", ascending=False).head(5))
                    if not _ss.empty:
                        hbar_list(_ss, "매장 이름", top=5)
                        st.caption("선택한 국가의 매출 상위 5개 매장")
                    else:
                        st.info("이 국가의 매장 데이터가 없어요.")
                else:
                    st.info("데이터가 없어요.")
                helpbox("""
    **국가별 매출 TOP 5 매장**
    - 위 셀렉트박스에서 고른 국가의 실결제 거래를 `매장 이름`으로 묶어 `KRW환산금액` 합·건수 → 상위 5개 매장.

            _home_store()
""")
    st.caption("※ 여긴 요약(TOP)이에요. 전체 순위는 '상품 카테고리 분석'·'매장별 분석' 탭에서 봐요.")

# ════════════ 탭 2: 상품 카테고리 분석 (상세, 전체) ════════════
with tab_cat:
    with card("🎨 아티스트/캐릭터 비중 · 🖼 프레임(IP) 전체 순위"):
        _s = sales.assign(_c=cat3(sales["카테고리"]))
        # 시안: 도넛(아티스트/캐릭터) 상단 전체폭
        ac = _s.groupby("_c")["KRW환산금액"].sum().rename("매출").reset_index()
        ac2 = (ac[ac["_c"].isin(["아티스트", "캐릭터"]) & (ac["매출"] > 0)]
               .sort_values("매출", ascending=False))
        if not ac2.empty:
            _m = {r["_c"]: int(r["매출"]) for _, r in ac.iterrows()}
            _sub = "아티스트 " + fmt_krw(_m.get("아티스트", 0)) + " · 캐릭터 " + fmt_krw(_m.get("캐릭터", 0))
            css_donut(list(zip(ac2["_c"], ac2["매출"])), ["var(--brand-2)", "var(--teal)"], sub=_sub)
        # @st.fragment — 안의 위젯을 조작해도 이 조각만 다시 그린다.
        # 없으면 전체 재실행 → st.tabs(1.45)가 선택을 못 기억해 첫 탭으로 튕긴다.
        @st.fragment
        def _frame_rank():  # 구분·상태 토글 → 프레임 순위
            # 구분선 + 프레임 전체 순위(토글 + 전체폭 표)
            st.markdown('<div style="border-top:1px solid var(--border);margin-top:16px"></div>',
                        unsafe_allow_html=True)
            _hh, _tt = st.columns([4.2, 5.8], vertical_alignment="center")
            with _hh:
                st.markdown('<div class="ct" style="margin:0;transform:translateY(-8px)">'
                            '🖼 프레임(IP) 전체 순위</div>', unsafe_allow_html=True)
            with _tt:
                _tog = st.segmented_control("구분", ["전체", "아티스트", "캐릭터"], default="전체",
                                            key="cat_frame_tog", label_visibility="collapsed") or "전체"
            _fs = _s if _tog == "전체" else _s[_s["_c"] == _tog]
            fr_all = (_fs[_fs["프레임 이름"].astype(str).str.strip().replace("nan", "").ne("")]
                      .groupby("프레임 이름").agg(매출=("KRW환산금액", "sum"), 건수=("KRW환산금액", "count")).reset_index())
            fr_all = fr_all[fr_all["매출"] > 0]

            # 상태 필터 — 실제로 존재하는 상태만 칩으로 노출(빈 필터 클릭 방지)
            _sc = fr_all["프레임 이름"].map(lambda t: (_tstat.get(t) or {}).get("상태", "")) if _tstat else None
            if _tstat and _sc is not None:
                _have = [s for s in ["🔴 확인필요", "⚠️ 기간후판매", "🔚 종료", "⏳ 종료예정", "🆕 신규", "🟢 판매중", "⚪ 미확인"]
                         if (_sc == s).any()]
                _cnt = " · ".join(f"{s} {int((_sc == s).sum())}" for s in _have)
                _pick = st.segmented_control("상태", ["전체"] + _have, default="전체",
                                             key="cat_frame_stat", label_visibility="collapsed") or "전체"
                if _pick != "전체":
                    fr_all = fr_all[(_sc == _pick).reindex(fr_all.index, fill_value=False)]
                st.caption(f"프레임(IP) {len(fr_all):,}개 · {_cnt}")
            else:
                st.caption(f"프레임(IP) {len(fr_all):,}개 · TOP 10 + 나머지 접기")

            if not fr_all.empty:
                rank_table(fr_all, "프레임 이름", collapse_after=10, status_map=_tstat or None)
            else:
                st.info("데이터가 없어요.")
            helpbox("""
    **아티스트/캐릭터 비중 · 프레임(IP) 전체 순위**
    - 상단 도넛 = 탭1과 동일(아티스트·캐릭터 2조각, `cat3()` 분류).
    - 하단 표 = `전체 / 아티스트 / 캐릭터` 토글로 거른 뒤 `프레임 이름`별 `KRW환산금액` 합·건수. TOP 10 + 나머지 접기.

    **판매기간 · 상태** — 매출이 빠졌을 때 *끝나서* 빠진 건지, *안 끝났는데* 빠진 건지 가르려고 붙였어요.
    - **판매기간** = 그 타이틀의 **실제 첫·마지막 거래일**(조회 기간이 아니라 전체 이력 기준, 결측 0%).
    - **상태**는 실측 거래일 + **Jira 종료일**(`duedate`)로 판정해요. 마지막 거래일만으론 '종료'인지 '그냥 안 팔리는 중'인지 구분이 안 되거든요.
      - **🔚 종료** — Jira 종료일이 지났고 거래도 멈춤 → **급감이 예정된 것**
      - **⚠️ 기간후판매** — 종료일이 지났는데 **아직 팔리는 중** → 계약·정산에서 확인이 필요해요
      - **🆕 신규** — 첫 거래일이 조회 기간 안 → 올라간 게 정상
      - **⏳ 종료예정** — 30일 안에 종료 예정
      - **🔴 확인필요** — 판매기간이 남았는데 **7일 이상 거래 없음** → 점검 대상
      - **🟢 판매중** / **⚪ 미확인**(Jira 미연결이라 종료 여부 단정 불가)
    - Jira 매칭은 타이틀명 정규화 + `ip_aliases.json` 별칭 기준이라 **매출의 약 84%** 가 연결돼요. 나머지는 `⚪ 미확인`으로 두고 **추측하지 않아요.**
    """)

        _frame_rank()

    with card("🧩 상품 카테고리 (비중 · 매출)"):
        pc = (sales.groupby("상품 카테고리").agg(매출=("KRW환산금액", "sum"), 건수=("KRW환산금액", "count"))
              .reset_index().sort_values("매출", ascending=False))
        _p1, _p2 = st.columns([5, 5])
        with _p1:
            if pc["매출"].sum() > 0:
                _pcd = pc.copy()
                if len(_pcd) > 4:
                    _pcd = pd.concat([_pcd.head(3), pd.DataFrame([{
                        "상품 카테고리": f"기타 {len(_pcd) - 3}종", "매출": int(_pcd.iloc[3:]["매출"].sum())}])],
                        ignore_index=True)
                css_donut(list(zip(_pcd["상품 카테고리"], _pcd["매출"])),
                          ["var(--brand-2)", "var(--amber)", "#7c77ee", "#c7ccd6"])
        with _p2:
            if not pc.empty:
                hbar_list(pc, "상품 카테고리")   # 시안: 비중(도넛)+매출액(막대)
            else:
                st.info("데이터가 없어요.")
        helpbox("""
**상품 카테고리 (비중 · 매출) — 전체**
- 실결제를 `상품 카테고리`로 묶어 `KRW환산금액` 합·건수.
- 왼쪽 도넛 = 비중(상위 3 + 기타 묶음), 오른쪽 막대 = 카테고리별 매출액 전체.
""")

    @st.fragment
    def _prod_rank():
        with card("📦 카테고리별 상품 순위", key="scard-prodsel"):
            cats = [c for c in sorted(sales["상품 카테고리"].dropna().astype(str).unique().tolist())
                    if c and c != "nan"]
            if not cats:
                st.info("데이터가 없어요.")
                return
            _d = "미니스티커" if "미니스티커" in cats else cats[0]
            pick = st.selectbox("카테고리", cats, index=cats.index(_d),
                                key="prod_rank_pick", label_visibility="collapsed")
            pr = (sales[sales["상품 카테고리"] == pick].groupby("상품 이름")
                  .agg(매출=("KRW환산금액", "sum"), 건수=("KRW환산금액", "count")).reset_index())
            pr = pr[pr["매출"] > 0]
            if pr.empty:
                st.info("이 카테고리에는 데이터가 없어요.")
            else:
                rank_table(pr, "상품 이름", collapse_after=10)
            helpbox("""
**카테고리별 상품 순위**
- 위에서 고른 `상품 카테고리`에 속한 실결제 거래를 `상품 이름`으로 묶어 `KRW환산금액` 합·건수 → 순위. TOP 10 + 나머지 접기.
""")

    _prod_rank()

# ════════════ 탭 3: 국가별 분석 (상세, 전체) ════════════
with tab_nat:
    if "국가" not in sales.columns or sales.empty:
        st.info("국가 데이터가 없어요.")
    else:
        nat = (pd.concat([sales, coupons]).groupby(["국가", "결제 단위"])
               .agg(건수=("KRW환산금액", "count"), 현지=("총원화금액", "sum"),
                    매출=("KRW환산금액", "sum"), 쿠폰=("쿠폰KRW", "sum"))
               .reset_index())
        # 실결제(매출)나 쿠폰 매출 중 하나라도 있으면 표시. ★대만처럼 전액 쿠폰 결제(실결제 0)인
        #   국가가 '매출 0'으로 걸러져 사라지던 문제 수정 — 쿠폰 매출도 함께 보이게.★
        nat = nat[(nat["매출"] > 0) | (nat["쿠폰"] > 0)].copy()
        nat["_합"] = nat["매출"] + nat["쿠폰"]
        nat = nat.sort_values("_합", ascending=False)
        tot = nat["매출"].sum()
        mx = (nat["매출"] / tot).max() if tot else 1.0
        _has_cpn_only = bool(((nat["매출"] == 0) & (nat["쿠폰"] > 0)).any())

        with card("🌏 국가별 매출"):
            grid = "grid-template-columns:1.6fr .6fr .7fr 1.2fr 1.2fr 1.2fr 1.2fr"
            html = (f'<div class="ntbl"><div class="ntr nth" style="{grid}">'
                    '<span>국가</span><span class="c">통화</span><span class="r">건수</span>'
                    '<span class="r">현지 매출</span><span class="r">실결제(KRW)</span>'
                    '<span class="r">쿠폰(KRW)</span><span>실결제 비중</span></div>')
            for _, r in nat.iterrows():
                frac = (r["매출"] / tot) if tot else 0
                _only_cpn = (r["매출"] == 0 and r["쿠폰"] > 0)
                _badge = (' <span style="font-size:10px;font-weight:700;color:#b45309;'
                          'background:#fdf3e7;padding:1px 6px;border-radius:5px;margin-left:4px">쿠폰만</span>'
                          if _only_cpn else '')
                _cpn_cell = (f'<b style="color:var(--amber)">{fmt_krw(int(r["쿠폰"]))}</b>'
                             if r["쿠폰"] > 0 else '<span style="color:var(--text-3)">—</span>')
                html += (f'<div class="ntr" style="{grid}">'
                         f'<span class="nname">{flag_img(r["국가"])}{r["국가"]}{_badge}</span>'
                         f'<span class="c"><span class="cur">{r["결제 단위"]}</span></span>'
                         f'<span class="r num">{int(r["건수"]):,}</span>'
                         f'<span class="r num">{fmt_orig(r["현지"], r["결제 단위"])}</span>'
                         f'<span class="r num">{fmt_krw(int(r["매출"]))}</span>'
                         f'<span class="r num">{_cpn_cell}</span>'
                         f'{pct_bar(frac, mx)}</div>')
            st.markdown(html + "</div>", unsafe_allow_html=True)
            if _has_cpn_only:
                st.caption("💡 **쿠폰만** 표시된 국가(예: 대만)는 전액 쿠폰으로 결제돼 실결제는 0이에요. "
                           "매출 기준이 실결제(쿠폰 제외)라 예전엔 안 보였는데, 이제 쿠폰 매출로 함께 표시돼요. "
                           "실결제 비중은 0%가 맞아요.")
            helpbox("""
**국가별 매출 표**
- 실결제 + 순수 쿠폰거래(`coupons`)를 합쳐 `국가`·`결제 단위`(통화)로 묶음.
- **건수** = 거래 수 · **현지 매출** = `총원화금액`(= 최종 결제 금액 + 쿠폰 할인 금액, 현지통화 정가) 합.
- **실결제(KRW)** = `KRW환산금액` 합(쿠폰 제외) · **쿠폰(KRW)** = `쿠폰KRW` 합 · **실결제 비중** = 그 나라 실결제 ÷ 전체 실결제.
- ★전액 쿠폰 결제 국가는 실결제=0이라 예전엔 `매출>0` 필터에 걸려 사라졌음 → 이제 `실결제 또는 쿠폰`이 있으면 표시하고 '쿠폰만' 배지를 붙임(대만 케이스).
""")

        # ── 키오스크 1대당 매출 ────────────────────────────────
        _dev = load_devices()
        if not _dev.empty and len(date_range) == 2:
            _dd = device_days(_dev[~_dev["렌탈"]], date_range[0], date_range[1])
            per = pd.DataFrame()
            if not _dd.empty:
                _dd["국가"] = _dd["국가코드"].map(CC_TO_NAT)
                # ★정산금액(실결제+쿠폰)으로 나눈다. 실결제만 쓰면 전액 쿠폰 결제인
                #   대만이 1대당 0원으로 나와 비교가 망가진다(국가별 매출 표의 '쿠폰만' 케이스).
                _base = pd.concat([sales, coupons])
                _rev = (_base.groupby("국가")
                        .agg(매출=("정산금액", "sum"), 건수=("정산금액", "size"))
                        .reset_index())
                per = _dd.merge(_rev, on="국가", how="inner")
                per = per[(per["대일"] > 0) & (per["매출"] > 0)].copy()
                per["대당월"] = (per["매출"] / per["대일"] * 30).round(0).astype("int64")
                per["대당건"] = (per["건수"] / per["대일"] * 30).round(1)
                per = per.sort_values("대당월", ascending=False)

            if not per.empty:
                with card("🎰 키오스크 1대당 매출 <span class='muted'>(팝업·렌탈 제외)</span>",
                          key="scard-perkiosk"):
                    _mx = per["대당월"].max()
                    grid = ("grid-template-columns:1.3fr .65fr .95fr .85fr 1.15fr "
                            ".8fr 1.05fr")
                    html = (f'<div class="ntbl"><div class="ntr nth" style="{grid}">'
                            '<span>국가</span><span class="r">가동 대수</span>'
                            '<span class="r">기간 내 변동</span>'
                            '<span class="r">대·일</span><span class="r">1대당 월매출</span>'
                            '<span class="r">1대당 월건수</span><span>상대 수준</span></div>')
                    for _, r in per.iterrows():
                        _new, _end = int(r["신규"]), int(r["종료"])
                        _bits = []
                        if _new:
                            _bits.append(f'<span style="color:var(--green)">+{_new}</span>')
                        if _end:
                            _bits.append(f'<span style="color:var(--red)">-{_end}</span>')
                        _chg = " ".join(_bits) or '<span style="color:var(--text-3)">–</span>'
                        html += (f'<div class="ntr" style="{grid}">'
                                 f'<span class="nname">{flag_img(r["국가"])}{r["국가"]}</span>'
                                 f'<span class="r num">{int(r["대수"]):,}대</span>'
                                 f'<span class="r num" style="font-size:12px">{_chg}</span>'
                                 f'<span class="r num">{int(r["대일"]):,}</span>'
                                 f'<span class="r num">{fmt_krw(int(r["대당월"]))}</span>'
                                 f'<span class="r num">{r["대당건"]:,.1f}건</span>'
                                 f'{pct_bar(r["대당월"] / _mx if _mx else 0, 1.0)}</div>')
                    st.markdown(html + "</div>", unsafe_allow_html=True)
                    st.caption("키오스크 1대가 30일 돌았을 때의 매출로 환산했어요. "
                               "매장 수가 달라도 국가끼리 바로 비교할 수 있어요. "
                               "'기간 내 변동'은 이 기간에 새로 계약한 대수(+)와 계약이 끝난 대수(-)예요.")

                    with st.expander("📜 키오스크 계약 이력 (최근 12개월, 월별 신규·종료)"):
                        _h = _dev[~_dev["렌탈"]].copy()
                        _endm = pd.Timestamp(date_range[1]).to_period("M")
                        rows = []
                        for cc, g in _h.groupby("국가코드"):
                            # ★ 여기서 nat 을 쓰면 바깥 국가별 매출표의 nat(DataFrame)을 덮어써
                            #    아래 도넛이 터진다. 루프 변수는 반드시 다른 이름으로.
                            _natname = CC_TO_NAT.get(cc, cc)
                            # 종료는 실제 해지만 — 계약 종료일은 대부분 갱신일이다.
                            for col, lab, mask in (("시작일", "신규", g["시작일"].notna()),
                                                   ("종료일", "종료", ~g["가동중"])):
                                mm = g.loc[mask, col].dropna().dt.to_period("M")
                                mm = mm[(mm <= _endm) & (mm > _endm - 12)]
                                for k, v in mm.value_counts().items():
                                    rows.append({"국가": _natname, "구분": lab,
                                                 "월": str(k)[2:].replace("-", "."),
                                                 "대수": int(v)})
                        if not rows:
                            st.caption("최근 12개월 안에 신규·종료된 계약이 없어요.")
                        else:
                            _piv = (pd.DataFrame(rows)
                                    .pivot_table(index=["국가", "구분"], columns="월",
                                                 values="대수", aggfunc="sum", fill_value=0))
                            _piv["합계"] = _piv.sum(axis=1)
                            st.dataframe(_piv.sort_values("합계", ascending=False),
                                         use_container_width=True)
                            st.caption("계약 기간(시작~종료) 기준이에요. 신규가 몰린 달 뒤로 "
                                       "그 나라 매출이 함께 올랐는지 보면 증설 효과를 가늠할 수 있어요.")
                    helpbox("""
**키오스크 1대당 매출**
- **대·일** = 키오스크 × 조회기간과 계약 기간이 겹치는 날짜 수. 기간 중간에 계약이 시작·종료되면 그만큼만 세요.
- **1대당 월매출** = 기간 매출 ÷ 대·일 × 30. 매장 수가 많은 나라가 무조건 위로 가지 않게 맞춘 값이에요.
- 여기서 매출은 **실결제 + 쿠폰(정산금액)** 이에요. 실결제만 쓰면 전액 쿠폰으로 결제되는 국가(대만)가 1대당 0원이 돼요.
- **팝업·렌탈은 분자·분모 모두 제외**했어요. 며칠만 도는 행사 장비라 상시 매장과 섞으면 왜곡돼요.
- **기간 내 변동** `+N` = 신규 계약, `-N` = 실제 해지. 신규가 많은 나라는 대·일이 짧아 대당 매출이 눌려 보일 수 있어요.
- ★**계약 종료일 ≠ 폐점**이에요. 가맹 계약이 대부분 1년이라 오늘도 89대가 종료일을 맞는데, 그건 갱신일이에요.
  그래서 운영 상태가 **'가맹 해지'인 것만** 종료로 보고 분모를 잘라요.
""")

        with card("🍩 국가별 매출 비중"):
            # 도넛은 실결제 비중 — 쿠폰만(실결제 0) 국가는 제외해 비중이 왜곡되지 않게.
            _natp = nat[nat["매출"] > 0].sort_values("매출", ascending=False)
            _pie = _natp[["국가", "매출"]].copy()
            if len(_pie) > 7:
                _pie = pd.concat([_pie.head(7), pd.DataFrame(
                    [{"국가": f"기타 {len(_natp) - 7}개국", "매출": int(_natp.iloc[7:]["매출"].sum())}])],
                    ignore_index=True)
            _pie = _pie.sort_values("매출", ascending=False).reset_index(drop=True)
            css_donut(list(zip(_pie["국가"], _pie["매출"])), PAL, size=190, hole=62, legend_fs=14)
            cpn_by = pd.concat([coupons, sales[sales["쿠폰 할인 금액"] > 0]])
            if not cpn_by.empty:
                st.markdown(f'<div class="strip">🎟 쿠폰 총 할인 '
                            f'<b>{fmt_krw(int(cpn_by["쿠폰KRW"].sum()))}</b> · {len(cpn_by):,}건</div>',
                            unsafe_allow_html=True)
            helpbox("""
**국가별 매출 비중 (도넛)**
- 위 표의 국가별 KRW 매출로 비중 계산. 상위 7개국 + '기타 N개국' 묶음.
- 하단 🎟 스트립 = 쿠폰 붙은 전체 거래의 `쿠폰KRW` 합·건수.
""")

        # 키오스크당 매출 — 대당 효율. 키오스크 대수 데이터 미적재라 대수·당매출은 '준비중' 표기.
        _pend_badge = ('<span style="font-size:11px;font-weight:700;color:#b45309;'
                       'background:#fdf3e7;padding:2px 8px;border-radius:6px;margin-left:7px">준비중</span>')
        with card("🖥 키오스크당 매출 (대당 효율)" + _pend_badge):
            st.caption("키오스크 대수 데이터를 준비 중이에요. 대수가 연결되면 '키오스크당 매출'과 "
                       "'총매출 1위 ≠ 대당 효율 1위' 인사이트가 자동으로 채워져요.")
            kdf = (sales.groupby("국가")["KRW환산금액"].sum().rename("매출").reset_index()
                   .sort_values("매출", ascending=False)) if "국가" in sales.columns else pd.DataFrame()
            kdf = kdf[kdf["매출"] > 0] if not kdf.empty else kdf
            if kdf.empty:
                st.info("데이터가 없어요.")
            else:
                _pend = '<span class="cur" style="color:#b45309;background:#fdf3e7">준비중</span>'
                grid = "grid-template-columns:1.5fr 1.5fr 1.1fr 1.4fr"
                khtml = (f'<div class="ntbl"><div class="ntr nth" style="{grid}">'
                         '<span>국가</span><span class="r">총 매출</span>'
                         '<span class="c">키오스크 대수</span><span class="r">키오스크당 매출</span></div>')
                for _, r in kdf.iterrows():
                    khtml += (f'<div class="ntr" style="{grid}">'
                              f'<span class="nname">{flag_img(r["국가"])}{r["국가"]}</span>'
                              f'<span class="r num">{fmt_krw(r["매출"])}</span>'
                              f'<span class="c">{_pend}</span>'
                              f'<span class="r">{_pend}</span></div>')
                st.markdown(khtml + "</div>", unsafe_allow_html=True)
            helpbox("""
**키오스크당 매출 (준비중)**
- '총 매출' = 국가별 실결제 `KRW환산금액` 합.
- **키오스크 대수 데이터가 아직 적재 안 됨** → '대당 매출'은 계산 불가라 '준비중' 표기. 대수가 연결되면 `총매출 ÷ 대수`로 자동 계산돼요.
""")

# ════════════ 탭 4: 매장별 분석 (상세, 전체) ════════════
with tab_store:
    with card("🏬 국가별 매장 전체 순위", key="scard-storesel"):
        # @st.fragment — 안의 위젯을 조작해도 이 조각만 다시 그린다.
        # 없으면 전체 재실행 → st.tabs(1.45)가 선택을 못 기억해 첫 탭으로 튕긴다.
        @st.fragment
        def _store_rank():  # 국가 선택 → 매장 순위
            # 실결제+쿠폰을 함께 봐서 전액 쿠폰(실결제 0) 매장·국가도 놓치지 않게(대만 케이스)
            _base = pd.concat([sales, coupons])
            _opts = ([str(c) for c in _base.assign(_t=_base["KRW환산금액"] + _base["쿠폰KRW"])
                      .groupby("국가")["_t"].sum().sort_values(ascending=False).index.tolist()]
                     if "국가" in _base.columns else [])
            if not _opts:
                st.info("데이터가 없어요.")
            else:
                pick = st.selectbox("국가", ["전체"] + _opts, key="store_country", label_visibility="collapsed")
                _src = _base if pick == "전체" else _base[_base["국가"] == pick]
                ss = (_src.groupby("매장 이름")
                      .agg(매출=("KRW환산금액", "sum"), 쿠폰=("쿠폰KRW", "sum"), 건수=("KRW환산금액", "count"))
                      .reset_index())
                ss = ss[(ss["매출"] > 0) | (ss["쿠폰"] > 0)]
                ss_paid = ss[ss["매출"] > 0].sort_values("매출", ascending=False)
                ss_cpn = ss[(ss["매출"] == 0) & (ss["쿠폰"] > 0)].sort_values("쿠폰", ascending=False)
                st.caption(f"매장 {len(ss):,}개 · TOP 10 + 나머지 접기" + ("" if pick == "전체" else f" · {pick}"))
                if ss_paid.empty and ss_cpn.empty:
                    st.info("이 국가의 매장 데이터가 없어요.")
                else:
                    if not ss_paid.empty:
                        hbar_list(ss_paid, "매장 이름", collapse_after=10)   # 시안: 가로 막대
                    if not ss_cpn.empty:
                        _bits = " · ".join(f'{r["매장 이름"]} <b>{fmt_krw(int(r["쿠폰"]))}</b>'
                                           for _, r in ss_cpn.head(12).iterrows())
                        _more = f' 외 {len(ss_cpn) - 12}곳' if len(ss_cpn) > 12 else ''
                        st.markdown('<div class="strip">🎟 쿠폰만 매장 (실결제 0) — '
                                    + _bits + _more + '</div>', unsafe_allow_html=True)
            helpbox("""
    **국가별 매장 전체 순위**
    - 실결제+쿠폰 거래를 `매장 이름`으로 묶어 실결제(`KRW환산금액`)·쿠폰(`쿠폰KRW`) 합.
    - 실결제 있는 매장 = 막대 순위(TOP10 + 나머지 접기). 전액 쿠폰(실결제 0) 매장 = 하단 🎟 스트립에 쿠폰 매출로 표시.
    - ★국가 드롭다운도 실결제+쿠폰 기준이라 대만처럼 쿠폰만 있는 국가도 고를 수 있음.
    """)

        _store_rank()

# ════════════ 탭 5: 시간대 · 데이터 ════════════ [보류: SHOW_TAB_ETC 로 부활]
if SHOW_TAB_ETC:
    with tab_etc:
        with card("⏰ 시간대별 매출 분포"):
            _hv = (sales.assign(시간대=sales["결제일시"].dt.hour).groupby("시간대")["KRW환산금액"].sum()
                   .reindex(range(24), fill_value=0))
            css_hours([int(v) for v in _hv.tolist()])
            st.caption("최고 시간대만 진하게 강조했어요.")
            helpbox("""
**시간대별 매출 분포**
- 실결제 거래의 `결제일시`에서 **시(hour)** 만 뽑아 0~23시로 묶어 `KRW환산금액` 합(빈 시간대는 0).
- 매출이 가장 큰 시간대만 진하게 강조.
""")

        helpbox("""
**원본 데이터**
- 현재 필터가 적용된 거래 전체(`df`)를 결제일시 내림차순으로 표시. CSV로 내려받기 가능.
- 표시 컬럼: 날짜·결제일시·국가·매장·카테고리·상품·단가·쿠폰·최종결제·통화·KRW환산·결제수단·프레임·카테고리·취소여부.
""")
        with st.expander("🗃 원본 데이터 보기 / 내려받기"):
            cols = ["날짜", "결제일시", "국가", "매장 이름", "상품 카테고리", "상품 이름",
                    "상품 단가", "쿠폰 할인 금액", "최종 결제 금액", "결제 단위",
                    "KRW환산금액", "결제 수단", "프레임 이름", "카테고리", "취소 여부"]
            avail = [c for c in cols if c in df.columns]
            st.dataframe(df[avail].sort_values("결제일시", ascending=False).reset_index(drop=True),
                         use_container_width=True, height=400)
            st.download_button("CSV 다운로드",
                               df[avail].to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig"),
                               "snapism_filtered.csv", "text/csv")
