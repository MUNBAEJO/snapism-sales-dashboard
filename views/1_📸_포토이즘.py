# -*- coding: utf-8 -*-
"""포토이즘 매출 대시보드 — 재디자인(시안 snapism-hybrid 기준, 스내피즘과 동일 디자인 시스템).

구조: 인라인 필터바(컴팩트 칩) + KPI 3카드 + 6탭
      (매출 한눈에·IP·타이틀 분석·국가별·매장별·세부 항목·시간대/데이터).
매출 = 실결제 + 쿠폰기여 + 코인기여(지정 국가만 가산). 데이터 로직·로더·DuckDB 세부검색은
기존 그대로 보존(비파괴). 표현 계층만 스내피즘 시안형(CSS 차트·카드key·컴팩트 위젯)으로 교체.
"""
import json
import re
import sys
import os
from contextlib import contextmanager
from pathlib import Path
from datetime import date, timedelta

import pyarrow.parquet as pq
import numpy as np
import pandas as pd
import streamlit as st

# set_page_config 는 라우터(스내피즘.py)에서 처리
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from guide_content import render_guide
import ip_classify  # IP구분/IP명 분류 공용 모듈
import name_alias  # 테마·프레임 한/영 통합 + 글자깨짐 교정
import photoism_rules  # 매출액 가산 규칙(쿠폰·코인 국가)
import auth
import xlsx_export  # 내려받기 → 엑셀(.xlsx)
import trend_chart  # '매출 추이' 카드(두 대시보드 공용)

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
  /* ★축약형 `background:` 금지 — background-image 까지 none 으로 지워
     auth.render_watermark 의 워터마크가 통째로 사라진다. 색만 지정할 것. */
  background-color:var(--bg) !important; }
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
/* k4 = 합계+실결제+쿠폰·코인+취소 4칸 (스내피즘과 동일 구성) */
.kpis.k4{ grid-template-columns:1.8fr 1fr 1fr 1fr; }
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
/* ── 누를 수 있는 순위줄 — 표 모양은 그대로 두고 투명 버튼을 겹친다 ──
   (오픈 캘린더 ip_calendar_ui 에서 쓰던 방법을 그대로 가져왔다)
   ★높이는 --rrh 한 곳에서만 정한다. 줄과 버튼이 같은 값을 써야 아래쪽이
     안 눌리는 일이 없다 — 캘린더에서 16px 이 죽어 있던 그 문제다. */
/* ★줄 상자에 높이를 준다 — 안 주면 상자(36px)가 표(46px)보다 낮아서
     덮개(inset:0)가 아래 10px 을 못 덮는다. 높이는 여기 한 곳에서만 정한다. */
div[class*="st-key-rrrow"]{ --rrh:46px; position:relative; min-height:var(--rrh); }
div[class*="st-key-rrrow"] .rr1{ border:0; }
div[class*="st-key-rrrow"] .ntr{ min-height:var(--rrh); align-items:center;
  border-radius:9px; transition:background .12s; }
div[class*="st-key-rrrow"]:hover .ntr{ background:var(--surface-2); }
div[class*="st-key-rrrow"] .ntr.rr-on{ background:var(--brand-soft); }
/* 버튼 묶음을 줄 위에 통째로 덮는다. inset:0 이라 줄 높이가 바뀌어도 따라간다.
   ★중간 래퍼가 하나라도 안 늘어나면 그만큼이 안 눌린다 — **높이도 폭도** 편다.
     help= 을 주면 스트림릿이 버튼을 툴팁 상자로 한 번 더 감싸는데, 그 상자는
     글자 폭(50px)만큼만 넓어진다. 그래서 이름 칸은 안 눌리고 숫자 칸만 눌렸다. */
div[class*="st-key-rrrow"] div[class*="st-key-rrbtn"]{
  position:absolute; inset:0; z-index:5; }
div[class*="st-key-rrrow"] div[class*="st-key-rrbtn"] div{
  height:100% !important; width:100% !important; }
div[class*="st-key-rrrow"] div[class*="st-key-rrbtn"] button{
  width:100% !important; height:100% !important; opacity:0; min-height:0 !important;
  padding:0 !important; margin:0 !important; border:none;
  background:transparent; cursor:pointer; }
.rrcaret{ margin-left:6px; font-size:11px; color:var(--text-3); }
/* 타이틀 → 테마 → 프레임 계층. 서랍을 겹칠 수 없어 들여쓰기로 층을 낸다. */
.thtree .thr{ background:#fbfcff; border-top:1px solid #eef0f7; }
.thtree .thr .nname{ font-weight:800; }
.thtree .fmr{ padding-top:3px; padding-bottom:3px; }
.thtree .fname{ padding-left:15px; font-size:12.5px; color:var(--text-2); }
.thtree .fmore{ padding-left:15px; font-size:12px; color:var(--text-3); }
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
/* ★건수(우측정렬)와 판매기간(좌측정렬)이 gap 10px 만 두고 맞붙어 한 값처럼 읽혔다.
   칸 경계선 + 넉넉한 안쪽 여백으로 확실히 끊는다. 세로선이 행 높이를 꽉 채워야
   눈에 걸리므로 align-self:stretch + flex 로 글자를 다시 가운데 맞춘다. */
.ntr .vs{ border-left:1px solid var(--border); padding-left:16px; margin-left:6px;
          align-self:stretch; display:flex; align-items:center; }
.ntr.nth .vs{ border-left-color:var(--border-strong); }
.tper{ font-size:11.5px; color:var(--text-2); white-space:nowrap; }
.cur{ font-size:11px; font-weight:700; color:var(--text-2); background:var(--surface-3); padding:2px 8px; border-radius:6px; }
/* 값이 0이라 굳이 읽을 필요 없는 칸 — 숫자 대신 흐린 대시 */
.dash{ color:#c8cdd6; }
.rk{ font-weight:800; color:var(--text-3); font-variant-numeric:tabular-nums; }
.rk.top{ color:var(--brand); }
.npct{ display:flex; align-items:center; gap:9px; }
.npct-bar{ flex:1; height:7px; background:var(--surface-3); border-radius:5px; overflow:hidden; }
.npct-bar i{ display:block; height:100%; background:var(--brand-2); border-radius:5px; }
.npct .p{ font-size:12.5px; font-weight:700; font-variant-numeric:tabular-nums; min-width:44px; text-align:right; }

/* 가로 막대 순위 (시안 .hbar) */
.hb-wrap{ display:flex; flex-direction:column; gap:5px; padding:4px 0; height:100%; justify-content:center; }
/* .pct = 매장 전체순위처럼 비중까지 보여주는 변형(칸 하나 더) */
.hb.pct{ grid-template-columns:150px 1fr 118px 60px !important; }
.hb-p{ text-align:right; font-weight:700; color:var(--text-3); font-variant-numeric:tabular-nums; font-size:12px; }
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
/* 추이 카드 요약 줄 — 그래프를 못 봐도(터치 기기) 답이 되게 항상 띄운다 */
.tsum{ display:flex; gap:14px; flex-wrap:wrap; align-items:baseline; font-size:12.5px;
       color:var(--text-2); margin:2px 0 6px; }
.tsum b{ font-size:15px; color:var(--text); font-weight:800; }
.xlab{ font-size:11px; color:var(--text-3); margin-top:7px; font-weight:600; }
.vlab{ font-size:10.5px; color:var(--text-2); font-weight:700; margin-top:2px; font-variant-numeric:tabular-nums; white-space:nowrap; }
/* 면적 선그래프 — 주·일처럼 점이 많을 때. 막대로 그리면 341개가 서로 붙어 못 읽는다. */
.lchart{ position:relative; height:200px; border-bottom:1px solid var(--border); }
.lchart svg{ display:block; width:100%; height:100%; }
/* hover 는 SVG 밖 HTML 열로 받는다 — SVG 안 요소로는 바깥 툴팁을 못 살린다(형제가 아님) */
.lhits{ position:absolute; inset:0; display:flex; }
.hcol{ flex:1 1 0; position:relative; cursor:crosshair; }
.hcol::after{ content:""; position:absolute; left:50%; top:0; bottom:0; width:1px;
              background:var(--text-3); opacity:0; }
.hcol:hover::after{ opacity:.35; }
.hcol .ltip{ position:absolute; left:50%; transform:translate(-50%,-8px); opacity:0;
             pointer-events:none; background:var(--text); color:#fff; font-size:11px;
             font-weight:700; line-height:1.55; padding:6px 9px; border-radius:7px;
             white-space:nowrap; z-index:6; transition:opacity .08s; }
.hcol:hover .ltip{ opacity:1; }
.hcol.st .ltip{ left:0; transform:translate(0,-8px); }      /* 왼쪽 끝 — 잘리지 않게 */
.hcol.en .ltip{ left:auto; right:0; transform:translate(0,-8px); }
.lxlab{ display:flex; justify-content:space-between; font-size:11px; color:var(--text-3);
        font-weight:600; margin-top:7px; }
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

/* 엑셀 다운로드 버튼 — 카드 머리줄에 얹히는 보조 버튼이라 작게(요청 2026-08-12) */
.st-key-dlbtn [data-testid="stButton"] button,
.st-key-dlbtn [data-testid="stDownloadButton"] button{
  min-height:0 !important; height:27px !important; padding:0 8px !important;
  border-radius:7px !important; }
.st-key-dlbtn [data-testid="stButton"] button p,
.st-key-dlbtn [data-testid="stDownloadButton"] button p{
  font-size:11px !important; font-weight:700 !important; letter-spacing:-.01em !important; }

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
.st-key-scard-natsel, .st-key-scard-titlesel, .st-key-scard-nattitle{ position:relative; }
.st-key-scard-natsel [data-testid="stElementContainer"]:has(> [data-testid="stSelectbox"]),
.st-key-scard-titlesel [data-testid="stElementContainer"]:has(> [data-testid="stSelectbox"]),
.st-key-scard-nattitle [data-testid="stElementContainer"]:has(> [data-testid="stSelectbox"]){
  position:absolute !important; top:16px !important; right:18px !important; width:auto !important;
  margin:0 !important; z-index:5 !important; }
.st-key-scard-natsel [data-testid="stSelectbox"], .st-key-scard-titlesel [data-testid="stSelectbox"],
.st-key-scard-nattitle [data-testid="stSelectbox"]{
  width:auto !important; min-width:0 !important; }
.st-key-scard-natsel [data-testid="stSelectbox"] div[data-baseweb="select"],
.st-key-scard-titlesel [data-testid="stSelectbox"] div[data-baseweb="select"],
.st-key-scard-nattitle [data-testid="stSelectbox"] div[data-baseweb="select"]{
  width:fit-content !important; min-width:110px !important; }

/* ── 구좌타입 분석 — 칸 머리 미니카드 ── */
.gzc{ border:1px solid var(--border); border-left-width:3px; border-radius:10px;
  padding:9px 11px 8px; margin:0 0 9px; background:var(--surface); }
/* 국가 × 구좌타입 표의 한 줄 누적막대 — 그 나라 안에서의 구성비(100% 기준이 나라마다 다름) */
.gzbar{ display:flex; height:8px; background:var(--surface-3); border-radius:5px;
  overflow:hidden; min-width:70px; }
.gzbar i{ display:block; height:100%; }
.gzc-l{ font-size:12px; font-weight:800; color:#39406b; display:flex; align-items:center; gap:6px; }
.gzc-b{ font-size:9.5px; font-weight:800; letter-spacing:.05em; color:var(--text-3);
  background:var(--surface-3); border-radius:5px; padding:1px 5px; }
.gzc-v{ font-size:17px; font-weight:800; color:var(--text); margin-top:3px; letter-spacing:-.02em; }
.gzc-d{ font-size:11px; color:var(--text-3); margin-top:1px; }

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
  .hb.pct{ grid-template-columns:92px 1fr 82px 44px !important; }
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
ORIG_FILE    = BASE_DIR / "data" / "master_photoism_orig.parquet"   # 오리지널 프레임별(경량)
PARQUET_FILE = BASE_DIR / "data" / "master_photoism.parquet"
MASTER_FILE  = BASE_DIR / "data" / "master_photoism.csv"
CONFIG_FILE  = BASE_DIR / "config.json"
DEVICE_FILE  = BASE_DIR / "data" / "devices.parquet"   # 장비관리 CMS(device_ingest.py)
THEME_FILE   = BASE_DIR / "data" / "theme_daily.parquet"  # CMS 프레임 리포트(테마 축)

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
_GUB_COLORS = {"아티스트": BRAND2, "캐릭터": TEAL, "PICK": PINK,
               "오리지널(포토이즘)": AMBER, "오리지널(기본)": "#7c77ee", "렌탈": SKY}
_GUB_EMOJI  = {"아티스트": "🎤", "캐릭터": "🧸", "PICK": "⭐",
               "오리지널(포토이즘)": "🎨", "오리지널(기본)": "🖼", "렌탈": "🏪"}
# 테마 비중 도넛용 색 — IP구분 색과 같은 계열에서 돌려 쓴다(화면 전체가 한 팔레트).
# 마지막 회색은 '기타 N개' 몫이다.
_GUB_CYCLE = [BRAND2, TEAL, PINK, AMBER, "#7c77ee", SKY, "#c7ccd6"]


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


# 브랜드(=상품 카테고리) 한글 라벨. 값 자체는 영문 원본을 그대로 쓰고 '보이는 이름'만 바꾼다
# (필터·집계 키가 바뀌면 안 되니까). 모르는 값은 원본 그대로.
_BRAND_KO = {"Box": "박스", "Colored": "컬러드",
             "Rentals and pop-ups": "렌탈·팝업", "Sticker Machine": "스티커머신"}


def brand_ko(b):
    return _BRAND_KO.get(str(b).strip(), str(b))


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
#
# ★ 인자 이름에 밑줄(_)을 붙이면 안 된다 — st.cache_data 는 **밑줄로 시작하는 인자를
#   해시에서 제외**한다. 예전엔 `_agg_mtime` 이라 mtime 이 캐시 키에 아예 안 들어갔고,
#   그래서 재집계해도 화면이 안 바뀌어 매번 서버를 재시작해야 했다(2026-07-28 수정).
# ★★cache_data 가 아니라 cache_resource 다 — 반환 프레임을 **절대 수정하지 말 것**.
#   cache_data 는 히트마다 피클을 다시 풀어 복사본을 주지만 cache_resource 는
#   같은 객체를 그대로 준다. 즉 여기 나온 df 를 in-place 로 고치면 그 오염이
#   모든 사용자·모든 rerun 에 영구히 남는다. 파생 컬럼은 이 함수 안에서 만들고,
#   밖에서 꼭 고쳐야 하면 .copy() 부터 해라.
#   (전환 근거: 이 프레임이 713MB 라 cache_data 의 피클 직렬화에만 7.3초가 들었고
#    — 실측 — 히트마다 역직렬화로 복사본을 또 만들어 메모리도 배로 썼다.
#    사용처를 전수 감사해 in-place 수정이 0건임을 확인하고 바꿨다. 2026-08-03)
@st.cache_resource(ttl=1800, show_spinner=False, max_entries=1)
def _load_data(agg_mtime, cfg_mtime):
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
    # ★취소금액·취소건수는 2026-07-30 집계에 추가된 열이다. 예전 집계 파일이 남아 있어도
    #   죽지 않게 없으면 0 으로 채운다(그 경우 취소 카드가 0원으로 나온다 → 재집계 안내).
    for col in ["건수", "최종 결제 금액", "쿠폰 할인 금액", "서비스코인",
                "취소금액", "취소건수"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype("int32")
        else:
            df[col] = 0

    # ── IP명 별칭 통합 ────────────────────────────────────────────
    # ★집계(build_photoism_agg)도 별칭을 태우지만 **만들 때 한 번**이다. 그래서
    #   ip_aliases.json 에 새 별칭을 넣어도 재집계 전까지는 화면이 옛 이름으로 남는다.
    #   여기서 한 번 더 태워 **파일을 다시 만들지 않아도 즉시 반영**되게 한다.
    #   (별칭 표는 자기참조를 포함해 멱등이라 두 번 태워도 결과가 같다)
    # ★실제로 SM 이 이것 때문에 넷으로 갈려 있었다 — CMS 등록 표기가 시기마다
    #   달라서다: 'sm'(25-04~) · 'SM Ent'(25-06~) · 'SM ent'(26-02~) ·
    #   'SM ENTERTAINMENT'(26-02~03). 화면에선 서로 다른 IP 로 보였다.
    # ★코드(codes)만 바꿔 치운다 — 370만 행에 .map/.astype(str) 을 태우면
    #   그 한 줄이 로딩의 병목이 된다(같은 실수를 환율 계산에서 한 번 했다).
    if "IP명" in df.columns and hasattr(df["IP명"], "cat"):
        _amap = ip_classify.load_alias_map()
        if _amap:
            _cats = df["IP명"].cat.categories
            _new = pd.Index([_amap.get(str(c).strip(), str(c).strip()) for c in _cats])
            if not _new.equals(pd.Index([str(c) for c in _cats])):
                _uniq = pd.Index(pd.unique(_new))
                _remap = _uniq.get_indexer(_new)
                _codes = df["IP명"].cat.codes.to_numpy()
                df["IP명"] = pd.Categorical.from_codes(
                    np.where(_codes >= 0, _remap[np.clip(_codes, 0, None)], -1),
                    categories=_uniq)

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
    # 취소액은 양수로 들고 다닌다(표기용). 매출액에는 이미 음수 거래로 차감돼 있다.
    df["취소KRW"] = (df["취소금액"] * df["환율"]).round(0).astype("int32")

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


# ★★첫/마지막 날짜를 캐시한다 (2026-08-19). `df_all["날짜"].max()/.min()` 두 줄이
#   **전체 재실행 4.7초 중 0.96초**였다(구간별 실측). `날짜` 가 object dtype —
#   `_load_data` 가 `.dt.date` 로 끝내서 datetime.date **객체 705만 개**라,
#   min/max 가 파이썬 비교로 전수를 훑는다. 값은 집계 파일이 바뀔 때만 변한다.
# ※근본 해결은 datetime64 로 바꾸는 것이다(같은 min/max 가 0.15초 · 이 열만
#   282MB→56MB). 다만 pandas 2.x 는 `datetime64 >= datetime.date` 를
#   **TypeError 로 거절**해서, 이 파일의 기간 비교를 전부 pd.Timestamp 로 감싸고
#   첫거래일·마지막거래일 표시(구좌별 상세·내려받기)도 손봐야 한다. 별건으로 미뤘다.
@st.cache_data(ttl=1800, show_spinner=False, max_entries=1)
def _date_bounds(agg_mtime, cfg_mtime):
    d = _load_data(agg_mtime, cfg_mtime)["날짜"]
    return d.min(), d.max()


@st.cache_data(ttl=1800, show_spinner=False, max_entries=1)   # mtime 키 → 최신 1개만 유효
def _sidebar_options(agg_mtime):
    """필터 드롭다운 옵션을 데이터 버전당 한 번만 계산(캐시)."""
    d = _load_data(_file_mtime(AGG_FILE), _file_mtime(CONFIG_FILE))
    if d.empty:
        return {"countries": [], "stores": [], "brands": [], "ip_by_gubun": {"_ALL": []}}

    def uniq(col, drop_empty=False):
        vals = sorted(str(v) for v in d[col].dropna().unique())
        return [v for v in vals if v not in ("", "nan")] if drop_empty else vals

    # 노출 대상 구분만 — '제외'·스티커머신의 IP명이 필터 목록에 남지 않게.
    # (렌탈은 2026-08-04 부터 노출 대상이라 여기 포함된다)
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
def _load_hourly(mtime):
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


@st.cache_data(ttl=1800, show_spinner=False, max_entries=1)   # mtime 키 → 최신 1개만 유효
def _load_orig(mtime, cfg_mtime):
    """오리지널 프레임별 경량 집계 로드 — 구좌타입 분석의 오리지널 탭 전용.
    매출액 = 실결제 + 지정국가 쿠폰·코인(본 집계와 동일 규칙). 매장 차원은 없음(날짜·국가만)."""
    if not ORIG_FILE.exists():
        return pd.DataFrame()
    try:
        df = pq.read_table(str(ORIG_FILE)).to_pandas(strings_to_categorical=True)
    except Exception:
        return pd.DataFrame()
    df["날짜"] = pd.to_datetime(df["날짜"], errors="coerce").dt.date
    df = df[df["날짜"].notna()]
    for col in ["건수", "최종 결제 금액", "쿠폰 할인 금액", "서비스코인"]:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype("int64")
    ex = load_exchange_rates()
    _unit = df["결제 단위"]
    if hasattr(_unit, "cat"):
        _rate_map = {c: ex.get(str(c).strip(), 1) for c in _unit.cat.categories}
        df["환율"] = _unit.map(_rate_map).astype(float).fillna(1.0)
    else:
        df["환율"] = _unit.astype(str).str.strip().map(ex).fillna(1.0)
    _krw = (df["최종 결제 금액"] * df["환율"]).round(0)
    _cpn = (df["쿠폰 할인 금액"] * df["환율"]).round(0)
    _coin = (df["서비스코인"] * df["환율"]).round(0)
    _cc = df["국가코드"].astype(str).str.lower().str.strip()
    _is_coup = _cc.isin(_COUPON_CC).to_numpy()
    _is_coin = _cc.isin(_COIN_CC).to_numpy()
    df["매출액"] = (_krw + _cpn * _is_coup + _coin * _is_coin).astype("int64")
    return df


def load_orig():
    return _load_orig(_file_mtime(ORIG_FILE), _file_mtime(CONFIG_FILE))


# ── 타이틀 하나의 프레임별 매출 (원본 on-demand) ─────────────────────────
# ※2026-08-13 현재 **화면에서 안 쓴다.** 프레임을 테마 리포트 쪽에서 캐도록 바꿨다
#   (타이틀 → 테마 → 프레임 층이 한 표에 있어야 어긋나지 않는다). 다만 이쪽은
#   **매장·브랜드 필터가 걸린다** — 테마 리포트엔 매장이 없다. 매장별 프레임을
#   다시 봐야 하면 이 함수를 그대로 쓰면 된다. 그래서 지우지 않고 남겨 둔다.
# ★집계(agg)에는 프레임이 없다 — 넣으면 그룹 수가 폭증해 파일이 감당이 안 된다.
#   그래서 프레임을 볼 땐 **고른 타이틀만** 원본 parquet 에서 캔다. 범위가 좁아
#   1,385만 행을 훑어도 0.5초쯤이고, 펼칠 때만 부르니 평소엔 부담이 0이다.
@st.cache_data(ttl=300, max_entries=16, show_spinner="프레임을 세는 중이에요…")
def load_frames(raw_titles, start_date, end_date, countries=(), stores=(), brands=()):
    if not raw_titles or not PARQUET_FILE.exists():
        return pd.DataFrame()
    try:
        import duckdb
    except Exception:
        return pd.DataFrame()

    def _q(vals):
        return ",".join("'" + str(v).replace("'", "''") + "'" for v in vals)

    where = [f"\"타이틀명\" IN ({_q(raw_titles)})",
             f"TRY_CAST(\"날짜\" AS DATE) BETWEEN DATE '{start_date}' AND DATE '{end_date}'",
             "LOWER(CAST(\"취소 여부\" AS VARCHAR)) NOT IN ('true','1','yes')"]
    for col, vals in (('"국가"', countries), ('"매장 이름"', stores), ('"브랜드"', brands)):
        if vals:
            where.append(f"CAST({col} AS VARCHAR) IN ({_q(vals)})")
    con = duckdb.connect()
    try:
        con.execute("PRAGMA memory_limit='512MB'")
        con.execute("PRAGMA threads=2")
    except Exception:
        pass
    try:
        d = con.execute(f"""
            SELECT COALESCE(NULLIF(TRIM(CAST("프레임 이름" AS VARCHAR)), ''), '(이름 없음)') AS "프레임",
                   CAST(SUM(TRY_CAST("최종 결제 금액" AS DOUBLE)) AS BIGINT) AS "매출",
                   CAST(COUNT(*) AS BIGINT) AS "건수"
            FROM read_parquet('{str(PARQUET_FILE).replace(chr(92), "/")}')
            WHERE {" AND ".join(where)}
            GROUP BY 1 ORDER BY "매출" DESC
        """).df()
    except Exception:
        d = pd.DataFrame()
    finally:
        con.close()
    return d[d["매출"] > 0] if not d.empty else d


# ── 타이틀 하나의 테마별 매출 (CMS 프레임 리포트) ────────────────────────
# ★거래 원장에는 **테마가 없다.** 테마는 CMS 프레임 리포트에만 있는 값이라
#   원장과 붙일 수가 없다 — (타이틀, 프레임) 조합이 테마에 유일하지 않아서
#   조인하면 매출이 불어난다. 그래서 **별도 집계**로 나란히 보여 준다.
# ★금액은 **현지통화**로 들어 있다. 원장과 같은 잣대로 보려면 원화로 환산해야 한다.
#   환산은 국가코드 → 결제단위 → config 환율 순서로, 원장(`_load_data`)과 같은 표를 쓴다.
@st.cache_data(ttl=300, max_entries=16, show_spinner="테마를 세는 중이에요…")
def _load_themes(raw_titles, start_date, end_date, ccodes=()):
    """[테마, 프레임, 국가코드, 건수, 촬영수, 현지 금액 3종] — 환산·가산은 부르는 쪽에서.

    ★타이틀 → 테마 → 프레임이 **한 표에 다 있다.** 그래서 계층을 그대로 낼 수 있다.
      원장으로는 못 하는 일이다 — 원장엔 테마가 아예 없다."""
    if not raw_titles or not THEME_FILE.exists():
        return pd.DataFrame()
    try:
        import duckdb
    except Exception:
        return pd.DataFrame()

    def _q(vals):
        return ",".join("'" + str(v).replace("'", "''") + "'" for v in vals)

    where = [f'"타이틀" IN ({_q(raw_titles)})',
             f"TRY_CAST(\"날짜\" AS DATE) BETWEEN DATE '{start_date}' AND DATE '{end_date}'"]
    if ccodes:
        where.append(f'LOWER(CAST("국가코드" AS VARCHAR)) IN ({_q(c.lower() for c in ccodes)})')
    con = duckdb.connect()
    try:
        con.execute("PRAGMA memory_limit='512MB'")
        con.execute("PRAGMA threads=2")
    except Exception:
        pass
    try:
        d = con.execute(f"""
            SELECT COALESCE(NULLIF(TRIM(CAST("테마"   AS VARCHAR)), ''), '(이름 없음)') AS "테마",
                   COALESCE(NULLIF(TRIM(CAST("프레임" AS VARCHAR)), ''), '(이름 없음)') AS "프레임",
                   LOWER(CAST("국가코드" AS VARCHAR)) AS "국가코드",
                   CAST(SUM(TRY_CAST("주문수"       AS DOUBLE)) AS BIGINT) AS "건수",
                   CAST(SUM(TRY_CAST("촬영수"       AS DOUBLE)) AS BIGINT) AS "촬영수",
                          SUM(TRY_CAST("최종결제금액" AS DOUBLE))          AS "최종 결제 금액",
                          SUM(TRY_CAST("쿠폰할인금액" AS DOUBLE))          AS "쿠폰 할인 금액",
                          SUM(TRY_CAST("서비스코인"   AS DOUBLE))          AS "서비스코인"
            FROM read_parquet('{str(THEME_FILE).replace(chr(92), "/")}')
            WHERE {" AND ".join(where)}
            GROUP BY 1, 2, 3
        """).df()
    except Exception:
        d = pd.DataFrame()
    finally:
        con.close()
    return d


def _theme_revenue(d, unit_map=None):
    """테마 리포트 행에 **원장과 똑같은 규칙**으로 '매출액' 을 붙인다.

    ★전엔 실결제(최종결제금액)만 환산해서, 구좌타입 분석과 2% 가까이 벌어졌다
      (SM ent 30일 기준 1,423만 원). 담당자 문의가 들어온 게 이 차이다.
    ★리포트에 쿠폰할인금액·서비스코인이 **같이 들어 있다.** 그래서 photoism_rules
      한 곳에 있는 가산 규칙(8개국)을 그대로 태우면 같은 잣대가 된다 — 적용 후
      차이 0.011%. 남는 건 자정 경계라 두 API 구조상 못 없앤다.
    ★규칙을 여기서 다시 쓰지 않는다. photoism_rules 를 부르는 이유가 그것이다.
    """
    d = d.copy()
    d["결제 단위"] = d["국가코드"].map(lambda c: str((unit_map or {}).get(c, "")).strip() or "KRW")
    return photoism_rules.add_revenue(d, load_exchange_rates())


def load_themes(raw_titles, start_date, end_date, ccodes=(), unit_map=None):
    """[테마, 프레임, 매출(원), 건수] — 원화 환산까지 끝난 표.
    unit_map = {국가코드: 결제단위} (원장에서 뽑아 넘긴다 — 표를 두 벌 두면 어긋난다)."""
    d = _load_themes(tuple(raw_titles), start_date, end_date, tuple(ccodes))
    if d.empty:
        return d
    d = _theme_revenue(d, unit_map)
    g = (d.groupby(["테마", "프레임"], as_index=False)
           .agg(매출=("매출액", "sum"), 건수=("건수", "sum")))
    g["매출"] = g["매출"].astype("int64")
    return g[g["매출"] > 0].sort_values("매출", ascending=False)


@st.cache_data(ttl=300, max_entries=8, show_spinner="테마를 모으는 중이에요…")
def _load_theme_all(start_date, end_date, ccodes=()):
    """[타이틀명, 테마, 프레임, 국가코드, 건수, 현지 금액 3종] — **전 타이틀** 한 번에.

    ★구좌별 상세의 줄마다 접기를 붙이는데, 접기 본문은 **펼치지 않아도 먼저 돈다**
      (스트림릿 함정 — 내려받기 패널에서 한 번 밟았다). 줄마다 질의하면 열 줄이면
      열 번이다. 기간 전체를 한 번만 읽어 두고 메모리에서 쪼갠다.
    """
    if not THEME_FILE.exists():
        return pd.DataFrame()
    try:
        import duckdb
    except Exception:
        return pd.DataFrame()
    where = [f"TRY_CAST(\"날짜\" AS DATE) BETWEEN DATE '{start_date}' AND DATE '{end_date}'"]
    if ccodes:
        _q = ",".join("'" + str(c).lower().replace("'", "''") + "'" for c in ccodes)
        where.append(f'LOWER(CAST("국가코드" AS VARCHAR)) IN ({_q})')
    con = duckdb.connect()
    try:
        con.execute("PRAGMA memory_limit='512MB'")
        con.execute("PRAGMA threads=2")
    except Exception:
        pass
    try:
        d = con.execute(f"""
            SELECT CAST("타이틀" AS VARCHAR) AS "타이틀명",
                   COALESCE(NULLIF(TRIM(CAST("테마"   AS VARCHAR)), ''), '(이름 없음)') AS "테마",
                   COALESCE(NULLIF(TRIM(CAST("프레임" AS VARCHAR)), ''), '(이름 없음)') AS "프레임",
                   LOWER(CAST("국가코드" AS VARCHAR)) AS "국가코드",
                   CAST(SUM(TRY_CAST("주문수" AS DOUBLE)) AS BIGINT) AS "건수",
                          SUM(TRY_CAST("최종결제금액" AS DOUBLE))     AS "최종 결제 금액",
                          SUM(TRY_CAST("쿠폰할인금액" AS DOUBLE))     AS "쿠폰 할인 금액",
                          SUM(TRY_CAST("서비스코인"   AS DOUBLE))     AS "서비스코인"
            FROM read_parquet('{str(THEME_FILE).replace(chr(92), "/")}')
            WHERE {" AND ".join(where)}
            GROUP BY 1, 2, 3, 4
        """).df()
    except Exception:
        d = pd.DataFrame()
    finally:
        con.close()
    return d


def theme_all(start_date, end_date, ccodes=(), unit_map=None):
    """[타이틀명, 테마, 프레임, 매출, 건수] — 원장과 같은 기준으로 환산까지 끝난 표."""
    d = _load_theme_all(start_date, end_date, tuple(ccodes))
    if d.empty:
        return d
    d = _theme_revenue(d, unit_map)
    g = (d.groupby(["타이틀명", "테마", "프레임"], as_index=False)
           .agg(매출=("매출액", "sum"), 건수=("건수", "sum")))
    g["매출"] = g["매출"].astype("int64")
    return g[g["매출"] > 0]


_TDATE = re.compile(r"^\d{5,8}_")


def _short_theme(names):
    """표시용으로 테마 앞 날짜접두어를 뗀다(`260624_라이즈(RIIZE)` → `라이즈(RIIZE)`).

    ★떼서 **겹치는 이름은 그대로 둔다.** `260727_izna` 와 `260811_izna` 는 다른 회차인데
      둘 다 'izna' 가 되면 화면에서 구분이 안 된다. 6개 IP · 14줄이 여기 해당한다
      (&TEAM · LE SSERAFIM · izna · 보넥도 · 이즈나 · 큐티 스트리트).
    ★**표시만 바꾼다.** 집계·이름통합은 원래 이름 그대로다.
    """
    byshort = {}
    for n in names:
        byshort.setdefault(_TDATE.sub("", str(n)), []).append(n)
    return {n: (sh if len(orig) == 1 else n)
            for sh, orig in byshort.items() for n in orig}


def _theme_portion(one, key_label=""):
    """한 IP/타이틀의 **테마 · 프레임 구성**을 '매출 한눈에' 처럼 그린다.
    one = theme_all 결과에서 그 대상만 잘라 온 표(타이틀명·테마·프레임·매출·건수)."""
    if one is None or one.empty:
        st.caption("이 조건에선 테마 데이터가 없어요.")
        return
    _tot = int(one["매출"].sum())
    # 화면에서만 날짜접두어를 뗀다 — 겹치는 것은 남긴다(위 _short_theme 주석 참고)
    one = one.assign(테마=one["테마"].astype(str).map(_short_theme(one["테마"].unique())))
    _th = (one.groupby("테마", as_index=False).agg(매출=("매출", "sum"), 건수=("건수", "sum"))
           .sort_values("매출", ascending=False))
    _fr = (one.groupby("프레임", as_index=False).agg(매출=("매출", "sum"), 건수=("건수", "sum"))
           .sort_values("매출", ascending=False))
    st.caption(f"테마 {len(_th):,}개 · 프레임 {len(_fr):,}개 · 매출 {fmt_krw(_tot)}"
               " · 실결제+쿠폰·코인 기준 · 매장 필터는 안 걸려요")
    c1, c2 = st.columns([4.2, 5.8])
    with c1:
        st.markdown('<div class="ct">🎨 테마 비중</div>', unsafe_allow_html=True)
        _top = _th.head(6)
        _etc = int(_th["매출"].iloc[6:].sum()) if len(_th) > 6 else 0
        _pairs = list(zip(_top["테마"].astype(str), _top["매출"]))
        if _etc:
            _pairs.append((f"기타 {len(_th) - 6}개", _etc))
        css_donut(_pairs, _GUB_CYCLE[:len(_pairs)], size=118, hole=34, legend_fs=12)
    with c2:
        st.markdown('<div class="ct">🎨 테마별 매출</div>', unsafe_allow_html=True)
        hbar_list(_th, "테마", top=8, show_pct=True)
    # ★예전엔 여기서 프레임을 **테마 구분 없이 통째로** 순위 매겼다. 그러면
    #   "이 테마 안에서 누가 팔렸나" 를 볼 방법이 아예 없었다(2026-08-19 요청).
    st.markdown('<div class="ct" style="margin-top:10px">🎨 테마 → 🖼 프레임 '
                '<span class="muted">테마 안에서 어느 프레임이 팔렸는지 · '
                '아티스트는 멤버 단위예요</span></div>',
                unsafe_allow_html=True)
    theme_tree(one, top_themes=6, top_frames=6)

def theme_tree(dframe, top_themes=8, top_frames=6):
    """타이틀 → 테마 → 프레임 계층을 한 덩어리로 그린다.

    ★서랍(expander)을 겹칠 수 없어서 계층을 **들여쓴 한 표**로 낸다 — 클릭 없이
      한눈에 읽히는 게 이 화면의 목적이기도 하다.
    비중 막대는 **테마는 타이틀 안에서**, **프레임은 그 테마 안에서** 잰다.
    """
    th = (dframe.groupby("테마", as_index=False)
          .agg(매출=("매출", "sum"), 건수=("건수", "sum"))
          .sort_values("매출", ascending=False))
    tot = int(th["매출"].sum()) or 1
    html = ['<div class="ntbl thtree">']
    for _, t in th.head(top_themes).iterrows():
        _tf = (t["매출"] / tot)
        html.append(
            f'<div class="ntr thr" style="grid-template-columns:1.7fr 1.3fr .8fr 1.5fr">'
            f'<span class="nname">🎨 {t["테마"]}</span>'
            f'<span class="r num">{fmt_krw(t["매출"])}</span>'
            f'<span class="r num" style="color:var(--text-2)">{int(t["건수"]):,}</span>'
            f'{pct_bar(_tf, 1.0)}</div>')
        # ★프레임으로 **합쳐야** 한다. 원본 한 줄이 (타이틀명 × 테마 × 프레임)이라
        #   같은 IP 안에 타이틀이 여럿이면(260624 SM ent · 260624 라이즈 · PW …)
        #   한 테마 밑에 `앤톤(ANTON)` 이 세 줄로 나온다(2026-08-19 발견).
        fr = (dframe[dframe["테마"] == t["테마"]]
              .groupby("프레임", as_index=False)
              .agg(매출=("매출", "sum"), 건수=("건수", "sum"))
              .sort_values("매출", ascending=False))
        fmx = (fr["매출"] / t["매출"]).max() if t["매출"] else 1.0
        for _, f in fr.head(top_frames).iterrows():
            html.append(
                f'<div class="ntr fmr" style="grid-template-columns:1.7fr 1.3fr .8fr 1.5fr">'
                f'<span class="fname">└ {f["프레임"]}</span>'
                f'<span class="r num">{fmt_krw(f["매출"])}</span>'
                f'<span class="r num" style="color:var(--text-3)">{int(f["건수"]):,}</span>'
                f'{pct_bar((f["매출"] / t["매출"]) if t["매출"] else 0, fmx)}</div>')
        if len(fr) > top_frames:
            html.append(f'<div class="ntr fmr"><span class="fmore">└ 외 프레임 '
                        f'{len(fr) - top_frames}개 · {fmt_krw(int(fr["매출"].iloc[top_frames:].sum()))}'
                        f'</span></div>')
    if len(th) > top_themes:
        _rest = th["매출"].iloc[top_themes:]
        html.append(f'<div class="ntr thr"><span class="fmore">🎨 외 테마 {len(_rest)}개 · '
                    f'{fmt_krw(int(_rest.sum()))}</span></div>')
    html.append("</div>")
    st.markdown("".join(html), unsafe_allow_html=True)


# ── 장비(키오스크) ─────────────────────────────────────────────
# 대당 매출의 분모. 장비관리 CMS 에는 설치일 컬럼이 없어 기기 S/N 앞 6자리(YYMMDD)를
# 설치일로 쓴다(device_ingest.py). 철거일은 아예 없고 '중지' 여부만 있다 — 그래서
# 언제 멈췄는지 모르는 중지 장비는 분모에서 뺀다(아래 device_days 주석 참고).
@st.cache_data(ttl=1800, show_spinner=False, max_entries=1)
def _load_devices(mtime):
    if not DEVICE_FILE.exists():
        return pd.DataFrame()
    try:
        # 매출매장명 = 거래 데이터의 '매장 이름' 과 맞춰볼 유일한 열.
        # 거래 쪽에 장비 번호가 없어서 '매출 발생 대수'는 이 이름으로만 이을 수 있다.
        d = pd.read_parquet(DEVICE_FILE, columns=["국가코드", "가동중", "테스트장비", "렌탈",
                                                  "설치일", "지점명", "부스번호", "매출매장명"])
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


def device_days(dev, p0, p1, sold=None):
    """국가코드별 '대·일'(가동 키오스크 × 가동일수)과 대수를 구한다.

    한 대가 기간 내내 있었으면 기간 전체 일수, 중간에 설치됐으면 설치일부터만 센다.
    ★설치일을 무시하고 대수로만 나누면, 최근 증설한 국가가 실제보다 낮게 나온다.
    설치일 미상(19대)은 기간 시작 전부터 있던 것으로 본다.

    sold: {(국가코드, 매장이름)} — 조회기간에 매출이 난 매장 집합. 주면 '매출대수'
          컬럼을 함께 낸다. ★거래 데이터에 **장비 번호가 없어서** 매장 단위로만
          이을 수 있다 — 한 매장에 2대가 있고 1대만 돌았어도 2대로 잡힌다.
          그래서 이 값은 '매출이 난 매장에 있는 장비 수'이지 '실제로 찍은 장비 수'가
          아니다. 화면 문구도 그렇게 적어 둘 것.
    """
    _cols = ["국가코드", "대수", "대일", "신규", "중지", "매출대수"]
    if dev.empty or not p0 or not p1:
        return pd.DataFrame(columns=_cols)
    s0, s1 = pd.Timestamp(p0), pd.Timestamp(p1)
    act = dev[dev["가동중"]]
    inst = act["설치일"].fillna(s0).clip(lower=s0)
    days = (s1 - inst).dt.days + 1
    t = pd.DataFrame({"국가코드": act["국가코드"], "대일": days.clip(lower=0),
                      "신규": act["설치일"].between(s0, s1).astype(int)})
    if sold is not None and "매출매장명" in act.columns:
        _m = act["매출매장명"].astype(str).str.strip()
        t["매출대수"] = [1 if (c, s) in sold else 0
                         for c, s in zip(act["국가코드"], _m)]
    else:
        t["매출대수"] = 0
    t = t[t["대일"] > 0]
    g = (t.groupby("국가코드").agg(대수=("대일", "size"), 대일=("대일", "sum"),
                                   신규=("신규", "sum"),
                                   매출대수=("매출대수", "sum")).reset_index())
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
    """매출 집계 대상 행.

    ★예전엔 `최종 결제 금액 >= 0` 으로 음수 행을 버렸다. 그런데 포토이즘 취소는
      **음수 거래**로 들어오고 집계 단계에서 같은 그룹의 정상 매출과 이미 상쇄된다.
      그래서 net 이 음수로 남은 45행만 버려지는 셈이었고 — 취소가 어떤 그룹에
      떨어졌는지에 따라 차감되기도, 통째로 빠지기도 했다(합계가 29만원 부풀어 있었다).
      지금은 전부 남긴다 → 합계·국가별·매장별이 모두 **취소 반영 net** 으로 일치한다.
      (취소 여부 플래그는 포토이즘엔 안 붙지만 다른 소스가 붙일 수 있어 가드로 남긴다.)
    """
    return df[~df["취소 여부"]]


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


def fmt_short(n):
    """막대 밑에 붙일 짧은 금액. 일 단위면 막대가 30개가 넘어 원 단위는 안 들어간다."""
    n = int(n or 0)
    if abs(n) >= 100_000_000:
        return f"₩{n / 100_000_000:,.1f}억"
    if abs(n) >= 10_000:
        return f"₩{n / 10_000:,.0f}만"
    return f"₩{n:,}"


def _area_chart(labels, data, series):
    """스택 면적 선그래프(SVG). data={시리즈:[값...]}, series=아래부터 쌓을 순서.

    ★왜 막대가 아니라 선인가 — 추이 차트가 조회 기간과 무관하게 최근 1년이 되면서
      '일' 보기는 점이 341개다. 막대로 그리면 서로 붙어 형태도 라벨도 안 읽힌다.
      선은 점이 많을수록 오히려 흐름이 또렷해진다. '월'(12개)은 막대가 낫다."""
    n = len(labels)
    W, H, PAD = 1000.0, 180.0, 6.0            # viewBox 좌표(실제 폭은 CSS 가 100%)
    tots = [sum(data[s][i] for s in series) for i in range(n)]
    mx = max(tots) or 1
    x = (lambda i: (W * i / (n - 1)) if n > 1 else W / 2)
    y = (lambda v: PAD + (H - PAD) * (1 - v / mx))

    body, base = "", [0.0] * n
    for s in series:
        color = _GUB_COLORS.get(s, "#888")
        top = [base[i] + data[s][i] for i in range(n)]
        up = " ".join(f"{x(i):.2f},{y(top[i]):.2f}" for i in range(n))
        dn = " ".join(f"{x(i):.2f},{y(base[i]):.2f}" for i in range(n - 1, -1, -1))
        # ★vector-effect — viewBox 를 preserveAspectRatio="none" 로 늘리면 선 두께가
        #   가로·세로 배율만큼 달라져 들쭉날쭉해 보인다. 이걸 주면 화면 기준 2px 로 고정된다.
        body += (f'<polygon points="{up} {dn}" fill="{color}" fill-opacity=".26"/>'
                 f'<polyline points="{up}" fill="none" stroke="{color}" stroke-width="1.8" '
                 f'vector-effect="non-scaling-stroke" stroke-linejoin="round" '
                 f'stroke-linecap="round"/>')
        base = top

    grid = "".join(f'<line x1="0" y1="{y(mx * f):.1f}" x2="{W}" y2="{y(mx * f):.1f}" '
                   f'stroke="var(--border)" stroke-width="1" stroke-dasharray="3 4" '
                   f'vector-effect="non-scaling-stroke"/>' for f in (0.5, 1.0))
    hits = ""
    for i, lab in enumerate(labels):
        _parts = " · ".join(f"{s} {fmt_krw(int(data[s][i]))}"
                            for s in series if data[s][i] > 0)
        _cls = "hcol st" if i < n * 0.06 else ("hcol en" if i > n * 0.94 else "hcol")
        _bot = max(4.0, min(88.0, tots[i] / mx * 88.0))
        hits += (f'<div class="{_cls}"><div class="ltip" style="bottom:{_bot:.1f}%">'
                 f'{lab}<br>합계 {fmt_krw(int(tots[i]))}'
                 + (f'<br>{_parts}' if _parts else '') + '</div></div>')
    st.markdown(
        f'<div class="lchart">'
        f'<svg viewBox="0 0 {W:.0f} {H:.0f}" preserveAspectRatio="none">{grid}{body}</svg>'
        f'<div class="lhits">{hits}</div></div>'
        f'<div class="lxlab"><span>{labels[0]}</span>'
        f'<span>최고 {fmt_short(int(mx))}</span>'
        f'<span>{labels[-1]}</span></div>', unsafe_allow_html=True)


def css_stack(labels, data, series, gran):
    """시안 CSS 스택 막대 추이 (IP구분별 다중 시리즈).
    labels=x축, data={시리즈:[값...]} labels 순서, series=그릴 순서.
    ★'주'·'일'은 점이 많아 막대가 안 읽힌다 → 면적 선그래프로 넘긴다."""
    if not labels:
        st.info("선택한 조건에 맞는 데이터가 없어요. 기간·구분을 바꿔 보세요.")
        return
    n = len(labels)
    if gran != "월" and n > 20:
        leg = "".join(f'<span><i class="dot" style="background:{_GUB_COLORS.get(s, "#888")}"></i>{s}</span>'
                      for s in series)
        st.markdown(f'<div class="legend">{leg}</div>', unsafe_allow_html=True)
        _area_chart(labels, data, series)
        return
    totals = [sum(data[s][i] for s in series) for i in range(n)]
    mx = max(totals) or 1
    # ★막대가 많아지면(최근 1년 · 일 단위면 365개) 라벨이 겹쳐 아무것도 못 읽는다.
    #   x축 라벨은 26개쯤만 남기고, 막대 위 금액은 아예 뺀다(툴팁으로 본다).
    #   라벨 목표는 14개쯤 — '07/28주' 같은 라벨이 40px 남짓이라 그 이상은 겹친다.
    step = 1 if n <= 20 else max(1, -(-n // 14))
    show_val = n <= 20
    gap = ("2px" if n > 120 else "4px") if n > 40 else (
        "6px" if gran == "일" else ("12px" if gran == "주" else "24px"))
    fs = ("9px" if n > 60 else "10px") if gran == "일" else "11px"
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
        _xl = lab if (i % step == 0 or i == n - 1) else ""
        cols += (f'<div class="col"><div class="vtip" style="bottom:{_tb}%">{_tip}</div>'
                 f'<div class="stack" style="height:{h}%">{seg}</div>'
                 f'<div class="xlab" style="font-size:{fs}">{_xl}</div>'
                 + (f'<div class="vlab">{fmt_short(tot)}</div>' if show_val else '')
                 + '</div>')
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


def hbar_list(dframe, name_col, top=None, collapse_after=None, show_pct=False):
    """시안 TOP 가로막대(이름 | 트랙+채움 | 금액). 1위=브랜드색, 나머지=연한 블루."""
    d = dframe.sort_values("매출", ascending=False).reset_index(drop=True)
    # ★분모는 **자르기 전 전체 합**이다. 예전엔 head(top) 뒤에 합을 냈다 —
    #   테마 13개 중 8개만 그리면서 그 8개 합을 분모로 써서, 같은 값이 도넛에선
    #   43.5%, 막대에선 45.1% 로 **다르게 나왔다**(2026-08-19). 프레임은 42개 중
    #   10개만 보여 16.8% 로 나왔는데 실제는 11.5% 였다.
    _tot = d["매출"].sum() if show_pct else 0
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
            _p = (f'<span class="hb-p">{r["매출"] / _tot * 100:.1f}%</span>'
                  if show_pct and _tot else '')
            h += (f'<div class="hb tip{" pct" if show_pct else ""}" data-tip="{_t}">'
                  f'<span class="hb-n">{r[name_col]}</span>'
                  f'<span class="hb-track"><i style="width:{w:.0f}%;background:{col}"></i></span>'
                  f'<span class="hb-v">{fmt_krw(r["매출"])}</span>{_p}</div>')
        return h + '</div>'

    if collapse_after and len(d) > collapse_after:
        st.markdown(_rows(d.iloc[:collapse_after]), unsafe_allow_html=True)
        with st.expander(f"나머지 {len(d) - collapse_after:,}개 더보기  ·  {collapse_after + 1}~{len(d):,}위"):
            st.markdown(_rows(d.iloc[collapse_after:]), unsafe_allow_html=True)
    else:
        st.markdown(_rows(d), unsafe_allow_html=True)


# (상태 배지 제거로 _STAT_CLS 는 더 쓰지 않는다 — 판매기간만 표기.)
_STAT_CLS_UNUSED = {"🔚": "end", "🔴": "warn", "⚠️": "post", "🆕": "new",
             "⏳": "soon", "🟢": "live", "⚪": "unk"}


def _md(dt):
    return f"{dt.month:02d}-{dt.day:02d}" if dt else ""


def _frag_rerun():
    """조각만 다시 그린다. 조각 밖(전체 실행) 이면 통째로 다시 그린다.

    ★`st.rerun(scope="fragment")` 는 **조각 재실행 중일 때만** 된다 — 전체 실행
      도중에 부르면 StreamlitAPIException 이다. 성공하면 RerunException(BaseException)
      이라 아래 except 에 안 걸리고, 실패했을 때만 전체 재실행으로 넘어간다.
    """
    try:
        st.rerun(scope="fragment")
    except Exception:          # noqa: BLE001 — 위 주석 참고(성공 경로는 안 걸린다)
        st.rerun()


def _period_str(o, e):
    """판매기간 '오픈 ~ 종료' 문자열. 두 날짜의 연도가 다르면 연도(YY)를 붙인다 —
    안 붙이면 해를 넘긴 티켓이 '05-05 ~ 03-31' 처럼 거꾸로 읽힌다."""
    if not o and not e:
        return ""
    _cross = bool(o and e and o.year != e.year)

    def _f(d):
        if not d:
            return ""
        return f"{d.year % 100:02d}.{d.month:02d}.{d.day:02d}" if _cross else _md(d)

    return f'{_f(o) or "?"} ~ {_f(e) or "진행중"}'


def rank_table(dframe, name_col, top=None, collapse_after=None, status_map=None,
               nested_key=None):
    """비중막대 내장 순위표(.ntbl). collapse_after=N 이면 상위 N개 + 나머지 접기.
    status_map={이름:{오픈일,종료일,...}} 를 주면 **판매기간(지라 오픈~종료)** 칸이 붙는다.
    (상태 배지 신규/확인필요/판매중/종료 는 2026-07-28 제거 — 스내피즘과 동일 기준.)"""
    d = dframe.sort_values("매출", ascending=False).reset_index(drop=True)
    if top:
        d = d.head(top)
    tot = d["매출"].sum()
    mx = (d["매출"] / tot).max() if tot else 1.0
    has_cnt = "건수" in d.columns
    has_st = bool(status_map)
    if has_st:
        grid = "grid-template-columns:34px 1.7fr 1.2fr .7fr 1.3fr 1.1fr"
        head = (f'<div class="ntr nth" style="{grid}">'
                '<span>#</span><span>이름</span><span class="r">매출</span>'
                '<span class="r">건수</span><span class="vs">판매기간</span>'
                '<span>비중</span></div>')
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
                # 상태 배지(신규/확인필요/판매중/종료 등) 제거 — 스내피즘과 동일 기준.
                # 판매기간은 **지라 티켓의 계획 오픈일~종료일**만 쓴다(실측 첫·마지막 거래일 아님).
                _ps = _period_str(s.get("오픈일"), s.get("종료일")) if s else ""
                per = f'<span class="tper num vs">{_ps or "—"}</span>'
            h += (f'<div class="ntr" style="{grid}">{rk}{nm}'
                  f'<span class="r num">{fmt_krw(r["매출"])}</span>{cnt}{per}{pct_bar(frac, mx)}</div>')
        return h

    if collapse_after and len(d) > collapse_after:
        top_d, rest_d = d.iloc[:collapse_after], d.iloc[collapse_after:]
        st.markdown(f'<div class="ntbl">{head}{_rows(top_d)}</div>', unsafe_allow_html=True)
        _lab = f"나머지 {len(rest_d):,}개 더보기  ·  {collapse_after + 1}~{len(d):,}위"
        # ★이미 expander 안이면 expander 를 또 열 수 없다(Streamlit 이 막는다).
        #   그런 자리에선 nested_key 를 주고 체크박스로 편다.
        if nested_key:
            _open = st.checkbox(_lab, key=nested_key)
        else:
            _open = None
        if _open is None:
            with st.expander(_lab):
                st.markdown(f'<div class="ntbl">{head}{_rows(rest_d)}</div>', unsafe_allow_html=True)
        elif _open:
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

first_date, last_date = _date_bounds(_file_mtime(AGG_FILE), _file_mtime(CONFIG_FILE))
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


def rank_rows_clickable(dframe, name_col, tot, mx, status_map=None, key_prefix="rr",
                        top=10, open_key="ph_slot_open", on_open=None):
    """순위표를 **원래 모양 그대로** 그리되, 줄마다 투명 버튼을 겹쳐 누를 수 있게.

    ★HTML 표는 예쁘지만 눌리지 않는다. 접기(expander)로 바꿔 봤더니 막대와 칸이
      사라져 "기존 화면에서 눌리면 좋겠다" 는 얘기가 나왔다. 그래서 표는 그대로 두고
      **오픈 캘린더에서 쓰던 투명 버튼 겹치기**를 그대로 가져온다(ip_calendar_ui).
    ★높이를 --rrh 한 곳에서만 정한다. 줄·버튼이 같은 값을 써야 아래쪽이 안 눌리는
      일이 없다 — 캘린더에서 16px 이 죽어 있던 그 문제다.
    ★CSS 가 안 먹어도 버튼은 남는다. 기능이 죽지는 않고 모양만 투박해진다.
    """
    _open = st.session_state.get(open_key)
    for i, (_, r) in enumerate(dframe.head(top).iterrows(), 1):
        nm = str(r[name_col])
        frac = (r["매출"] / tot) if tot else 0
        per = ""
        if status_map and status_map.get(nm):
            _s = status_map[nm]
            _ps = _period_str(_s.get("오픈일"), _s.get("종료일"))
            per = f'<span class="tper num vs">{_ps or "—"}</span>'
        grid = ("grid-template-columns:34px 1.7fr 1.2fr .7fr 1.3fr 1.1fr" if per else
                "grid-template-columns:34px 1.7fr 1.3fr .8fr 1.5fr")
        _sel = " rr-on" if _open == nm else ""
        # ★키가 반드시 `rrrow` 로 **시작**해야 한다 — CSS 가 st-key-rrrow 를 찾는다.
        #   전엔 f"{key_prefix}row{i}" 라 'rr0row1' 이 돼서 선택자가 안 걸렸고,
        #   버튼이 겹치지 않고 줄 아래에 그대로 보였다.
        with st.container(key=f"rrrow-{key_prefix}-{i}"):
            st.markdown(
                f'<div class="ntbl rr1"><div class="ntr{_sel}" style="{grid}">'
                f'<span class="rk {"top" if i == 1 else ""}">{i}</span>'
                f'<span class="nname">{nm}<span class="rrcaret">{"▾" if _sel else "▸"}</span></span>'
                f'<span class="r num">{fmt_krw(r["매출"])}</span>'
                f'<span class="r num" style="color:var(--text-2)">{int(r["건수"]):,}</span>'
                f'{per}{pct_bar(frac, mx)}</div></div>', unsafe_allow_html=True)
            if st.button(nm, key=f"rrbtn-{key_prefix}-{i}", help="눌러서 펼치기"):
                st.session_state[open_key] = None if _open == nm else nm
                _frag_rerun()
        if _open == nm and on_open is not None:
            on_open(nm)

def cbfilter(col, label, options, key, fmt=None, parent_sig=None):
    """검색 + 체크박스 다중선택 필터. col 안에 라벨 + 팝오버(검색창·체크리스트).
    fmt=함수 를 주면 **보이는 이름만** 바꾼다(값·세션키는 원본 유지 — 예: 브랜드 한글 라벨).
    ★선택 상태의 단일 출처 = 각 체크박스 위젯(key=…__cb__옵션)★ — 별도 리스트를 두지 않아
    '선택 해제'·필터변경 시 상태 불일치가 없음. 선택 리스트 반환.
    목록을 항상 펼쳐 보여주고(상위 200개), 검색은 좁히는 용도."""
    options = list(options)
    _lab = fmt or (lambda o: str(o))
    pfx = f"{key}__cb__"

    def _sel():
        return [o for o in options if st.session_state.get(pfx + str(o), False)]

    # ★상위 필터가 바뀌면 **하위 선택을 초기화한다**(2026-08-18).
    #   parent_sig = 상위 필터의 현재 선택. 이게 달라지면 이 필터의 체크를 전부 끈다.
    #   ※아래 '목록에서 빠진 항목 끄기'(2026-08-11)만으로는 **안 잡힌다** — 그 코드는
    #     목록이 *좁아질* 때만 듣는다. 정작 주석이 말하던 상황은 반대였다:
    #     국가=한국 → 매장 고르기 → 국가 해제 → 매장 목록이 *넓어져* 한국 매장이
    #     그대로 목록에 남으니 안 지워지고, 사용자는 국가만 풀었는데 매장 필터가
    #     몰래 걸린 상태로 남았다. 그래서 상위가 바뀌면 그냥 끊는다.
    if parent_sig is not None:
        _psk = f"{key}__psig"
        if st.session_state.get(_psk) != parent_sig:
            if _psk in st.session_state:          # 첫 렌더에는 지울 게 없다
                for _k in [k for k in st.session_state if str(k).startswith(pfx)]:
                    st.session_state[_k] = False
            st.session_state[_psk] = parent_sig

    # ★상위 필터가 바뀌어 **목록에서 빠진 항목의 체크를 끈다**(2026-08-11).
    #   예: 국가=한국 → 매장 '전체' → 국가 해제. 매장 목록은 전 세계로 넓어지는데
    #   한국 매장 체크가 세션에 남아, 사용자는 국가만 풀었는데 매장 필터가 몰래
    #   걸린 상태가 됐다. 지금 목록에 없는 선택은 어차피 본문에도 안 먹으므로
    #   여기서 끊어 화면과 상태를 일치시킨다.
    _valid = {pfx + str(o) for o in options}
    for _k in [k for k in st.session_state
               if str(k).startswith(pfx) and k not in _valid
               and st.session_state.get(k)]:
        st.session_state[_k] = False

    sel = _sel()
    cap = "전체" if not sel else (_lab(sel[0]) if len(sel) == 1 else f"{len(sel)}개 선택")
    col.markdown(f'<div class="fbl">{label}</div>', unsafe_allow_html=True)
    with col.popover(cap, use_container_width=True):
        q = ""
        if len(options) > 6:
            q = st.text_input("검색", key=f"{key}__q", placeholder=f"🔍 {label} 검색",
                              label_visibility="collapsed").strip().lower()
        pool = [o for o in options if q in str(o).lower() or q in _lab(o).lower()] if q else list(options)
        # ★목록 순서를 **고정**한다(2026-08-11). 예전엔 고른 항목을 맨 위로 끌어올려
        #   (`[*sel, *pool]`) 체크하면 위로 튀고 풀면 제자리로 내려갔다 — 연달아
        #   고를 때 방금 누른 줄이 사라져 다른 걸 잘못 누르게 된다.
        #   대신 상위 200개 밖으로 밀린 '이미 고른' 항목만 뒤에 붙여 안 놓치게 한다.
        shown = pool[:200]
        _off = [o for o in sel if o not in shown]
        over = len(pool) - len(shown)
        shown = shown + _off
        _b = st.columns(2)
        if _b[0].button("전체", key=f"{key}__all", use_container_width=True, disabled=not pool):
            for o in pool:
                st.session_state[pfx + str(o)] = True     # 체크박스 생성 前이라 초기값 설정 OK
        if _b[1].button("해제", key=f"{key}__clr", use_container_width=True, disabled=not sel):
            # ★목록에 지금 보이는 것만 지우면 안 된다 — 매장·IP명은 상위 필터(국가·
            #   IP구분)에 따라 목록이 좁아지므로, 좁아진 사이에 빠진 항목이 True 로
            #   남아 있으면 '해제' 를 눌러도 안 없어진다. 접두어가 같은 키를 전부 끈다.
            for _k in [k for k in st.session_state if str(k).startswith(pfx)]:
                st.session_state[_k] = False
        if not shown:
            st.caption("옵션이 없어요.")
        elif over:
            st.caption(f"상위 200개 표시 · 나머지 {over}개는 검색해서 찾아요.")
        for o in shown:
            st.checkbox(_lab(o), key=pfx + str(o))
    return _sel()


# [제거] 필터바 오른쪽 '⬇ 내려받기' — 뺐다(요청, 2026-08-12).
#        '구좌별 상세' 카드 머리줄에 있는 내려받기가 대신한다.
#        되살리려면 `git show a49d147` 의 data_export.py · _DL_RAW_COLS ·
#        _dl_control 을 되돌리고 필터바 컬럼을 한 칸 늘리면 된다.


# ── 필터바를 @st.fragment 로 격리 → 체크박스 조작은 이 조각만 가볍게 재실행되고,
#    무거운 본문(탭·차트)은 건드리지 않는다. '적용' 버튼을 눌러야 본문이 갱신된다.
@st.fragment
def _filterbar():
    with st.container(border=True, key="scard-filter"):
        # 필터는 왼쪽으로 모아 컴팩트하게(마지막은 빈 스페이서)
        _fb = st.columns([0.92, 0.8, 0.8, 0.8, 0.86, 0.5, 3.36], gap="small")
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
        cbfilter(_fb[2], "매장", _std, "ph_f_store", parent_sig=tuple(_dc))
        # 포5: 스내피즘과 라벨 통일 — 브랜드(Box/Colored) = 상품 종류라 "상품" 으로 부른다.
        cbfilter(_fb[3], "상품", _opts["brands"], "ph_f_brand", fmt=brand_ko)
        cbfilter(_fb[4], "IP구분", IP_GUBUN_VIEW, "ph_f_gubun")
        # [제거] 'IP명' 필터 — 기획에 없던 항목이라 뺐다(요청). IP 하나를 파고드는
        #        건 '구좌별 상세' 의 검색창이 대신한다. 아래 selected_ips 는 빈
        #        목록으로 고정되고, 그걸 쓰던 카드들은 조건문이 있어 조용히 빠진다.
        with _fb[5]:
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
# [제거] IP명 필터 — 위 필터바에서 뺐다. ★필터가 사라져도 **세션에 남은 체크는
#        그대로라** 그걸 계속 읽으면 화면에 없는 필터가 몰래 걸린다(같은 종류의
#        버그를 2026-08-11 에 국가↔매장에서 잡았다). 남은 키를 지우고 빈 목록으로 고정.
for _k in [k for k in st.session_state if k.startswith("ph_f_ip__cb__")]:
    del st.session_state[_k]
selected_ips = []
# ★매장별 탭 전용 필터(국가·상품)도 뺐다(2026-08-18) — 같은 이유로 세션에 남은
#   선택을 지운다. 위젯이 사라져도 값은 남고, 나중에 되살리면 옛 선택이 되살아난다.
for _k in ("ph_st_nat", "ph_st_prd"):
    st.session_state.pop(_k, None)

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


# ── 타이틀 판매기간 (타이틀 순위표에 표시) ────────────────
# 매출이 빠졌을 때 '끝나서'인지 '안 끝났는데'인지 가르려고 Jira 종료일을 함께 본다.
# ★ 날짜로 자르지 않은 scope 를 넘긴다 — 기간으로 자른 df 를 주면 첫 거래일이
#   전부 기간 시작일이 돼서 죄다 '신규'로 나온다.
@st.cache_data(ttl=1800, show_spinner=False, max_entries=8)
def _title_status_ph(agg_mtime, p0, p1, countries, brands, stores, gubuns, ips):
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
    return title_status(base, jira, p0, p1, title_col="타이틀", prefer_brand="photoism")


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
# 데이터 내려받기 권한(소유자·팀장·에디터). 버튼을 숨기지 않고 **비활성**으로 둔다.
_CAN_DL = auth.can_download(getattr(getattr(st, "user", None), "email", None))
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
# 총매출 = 실결제(카드·현금) + 쿠폰·코인 정산분. ★취소는 음수 거래로 이미 차감돼 있다.
pure_krw = int(sales["KRW환산금액"].sum())
cc_krw = int((sales["쿠폰기여"] + sales["코인기여"]).sum())
cc_cnt = int(sales[(sales["쿠폰기여"] > 0) | (sales["코인기여"] > 0)]["건수"].sum())
cancel_krw = int(sales["취소KRW"].sum())
cancel_cnt = int(sales["취소건수"].sum())

st.markdown(
    '<div class="kpis k4">'
    f'<div class="kpi hero"><div class="l">조회기간 매출 (합계)</div>'
    f'<div class="v num">{fmt_krw(period_amt)}</div>'
    f'<div class="d">{_dr} · {_period_days}일 · {tx_count(sales):,}건 · 취소 반영</div></div>'
    f'<div class="kpi"><div class="l">실결제 매출 (카드·현금)</div>'
    f'<div class="v num">{fmt_krw(pure_krw)}</div><div class="d">쿠폰·코인 제외분</div></div>'
    f'<div class="kpi"><div class="l">쿠폰·코인 매출 (정산분)</div>'
    f'<div class="v num">{fmt_krw(cc_krw)}</div><div class="d">{cc_cnt:,}건 · 지정국가 정산</div></div>'
    f'<div class="kpi"><div class="l">취소 매출</div>'
    f'<div class="v num">{fmt_krw(cancel_krw)}</div>'
    f'<div class="d">{cancel_cnt:,}건 · 합계에서 차감됨</div></div>'
    '</div>', unsafe_allow_html=True)

_scope_bits = []
if sel_countries:
    _scope_bits.append("국가: " + " · ".join(sel_countries[:4]) + (" 외" if len(sel_countries) > 4 else ""))
if sel_stores:
    _scope_bits.append("매장: " + " · ".join(sel_stores[:3]) + (" 외" if len(sel_stores) > 3 else ""))
if sel_brands:
    _scope_bits.append("상품: " + " · ".join(brand_ko(b) for b in sel_brands))
if sel_gubuns:
    _scope_bits.append("IP구분: " + " · ".join(sel_gubuns))
if selected_ips:
    _scope_bits.append("IP: " + " · ".join(selected_ips[:4]) + (" 외" if len(selected_ips) > 4 else ""))
if _scope_bits:
    st.markdown('<div class="scope">🌐 범위 — ' + "  |  ".join(_scope_bits) + '</div>',
                unsafe_allow_html=True)

helpbox("""
**KPI 4카드 — 조회기간 매출 · 실결제 · 쿠폰·코인 · 취소**

**공통 기준 (이하 모든 카드 동일)**
- **원본**: 30개국 포토이즘 매장 거래(매일 자동 수집)를 **DuckDB로 집계**한 값 · 환율은 `config.json` 실시간 환율표.
- **매출액 = 실결제 + 쿠폰기여 + 코인기여** — `KRW환산금액`(= 최종 결제 금액 × 환율)에, **지정 국가에서만** 쿠폰(`쿠폰기여`)·서비스코인(`코인기여`)을 더함(나라마다 정산 규칙이 달라서).
- **필터 반영**: 필터바(기간·국가·매장·상품·IP구분·IP)로 거른 뒤 계산. 미선택 = 전체.

**각 카드 계산**
- **조회기간 매출(합계)** = 매출액 합(실결제 + 지정국가 쿠폰·코인) — **취소가 차감된 순매출**이에요. 건수 = 취소 아닌 거래 건수.
- **실결제 매출(카드·현금)** = `KRW환산금액` 합 (쿠폰·코인 제외한 순수 결제분).
- **쿠폰·코인 매출(정산분)** = `쿠폰기여 + 코인기여` 합 = 매출 합계 − 실결제. 지정 국가에서만 잡혀요.
- **취소 매출** = 취소된 금액의 합(양수로 표기). **위 합계에서 이미 빠져 있어요** — '얼마가 취소됐나'를 보는 참고값이에요. 되돌리려면 `합계 + 취소 매출`.

**★취소를 어떻게 잡나 (스내피즘과 다른 점)**
- 포토이즘 취소는 `취소 여부` 플래그가 아니라 **음수 거래**로 들어와요(스내피즘은 플래그).
- 그래서 집계에서 그냥 더하면 **같은 그룹의 정상 매출과 상쇄돼 취소가 사라져요** — 원본 518건·300만원이 집계엔 45행·29만원으로만 남아 있었어요. 지금은 `취소금액`·`취소건수` 열로 따로 보존해요(`build_photoism_agg.py`).
- 취소액 정의는 **IP 정산서와 동일**(`최종 결제 금액 < 0` 인 거래의 절댓값)이라 정산서 표지의 '취소 금액'과 같은 기준이에요.
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
        # 데이터는 _load_data 에서 이미 IP_GUBUN_SHOWN 으로 걸러져 있다
        # (2026-08-04 부터 렌탈 포함 · '제외'와 스티커머신은 여전히 안 들어옴).
        # 예전엔 추이용 present_all(기획P 포함)을 따로 뒀는데, 기획P가 오리지널(포토이즘)로
        # 바뀌면서 present 와 완전히 같아져서 하나로 합쳤다.
        _gset = set(gub["IP구분"].astype(str))
        present = [g for g in IP_GUBUN_VIEW if g in _gset]


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
# [제거] '세부 항목' 탭 — 의미 낮아 UI에서 뺌(포8). 코드·데이터는 그대로 보존.
#         되살리려면 SHOW_TAB_DETAIL = True (아래 세부항목 블록도 함께 켜짐).
SHOW_TAB_DETAIL = False
# [숨김] '국가별 분석' 탭의 '🏆 국가별 타이틀 TOP 10' 카드 — UI 에서만 뺌(요청).
#         계산 코드는 그대로 남겨 뒀으니 True 로만 바꾸면 되살아난다.
SHOW_NAT_TITLE = False
# 포7: 런 비교를 사이드바에서 빼고 대시보드 탭으로. st.tabs 는 안 열어도 매 rerun 마다
#      모든 탭을 실행(런 빌드가 무겁다)하므로, 탭엔 무거운 연산 대신 전용 페이지 링크만 둔다.
_tab_labels = ["📊 매출 한눈에", "🎫 구좌타입 분석", "🌏 국가별 분석", "🏬 매장별 분석",
               "🆚 런 비교"]
if SHOW_TAB_DETAIL:
    _tab_labels.append("🔎 세부 항목")
if SHOW_TAB_ETC:
    _tab_labels.append("⏰ 시간대 · 데이터")
_tabs = st.tabs(_tab_labels)
tab_home, tab_ip, tab_nat, tab_store, tab_runs = (_tabs[0], _tabs[1], _tabs[2],
                                                  _tabs[3], _tabs[4])
_ti = 5
tab_detail = _tabs[_ti] if SHOW_TAB_DETAIL else None
_ti += 1 if SHOW_TAB_DETAIL else 0
tab_etc = _tabs[_ti] if SHOW_TAB_ETC else None

# ════════════ 탭: 런 비교 (별도 페이지 링크 — 성능 위해 탭엔 링크만) ════════════
with tab_runs:
    with card("🆚 타이틀 런 비교"):
        st.markdown(
            '<div style="padding:4px 2px 14px;color:var(--text-2);font-size:13.5px;line-height:1.75">'
            '같은 IP의 <b style="color:var(--text)">회차(런)별 성과</b>를 나란히 비교해요 — '
            '런마다 기간이 달라 <b>일평균</b> 기준으로 봐요.<br>'
            '연산이 무거워 대시보드가 느려지지 않도록 <b>전용 페이지</b>로 열려요.</div>',
            unsafe_allow_html=True)
        # ★st.page_link 를 직접 부르면 안 된다 — runs 권한이 없는 계정에서
        #   StreamlitPageNotFoundError 로 이 화면 전체가 죽는다(auth.safe_page_link 주석 참고).
        auth.safe_page_link("runs", "런 비교 페이지 열기", icon="🆚",
                            denied="이 기능은 권한이 필요해요. 필요하면 관리자에게 요청해 주세요.")

# ════════════ 탭 1: 매출 한눈에 ════════════
with tab_home:
    sec("1", "매출 동향",
        "잘 가고 있나? — <b>조회 기간과 무관하게 항상 최근 1년</b>이에요 "
        "(국가·매장·상품·IP 필터는 그대로 적용돼요)")
    with card():
        # ★이 차트만 **상단 조회 기간을 안 따른다 — 항상 최근 1년**이다(2026-08-07).
        #   흐름은 길게 봐야 읽히는데, 기간을 좁히면 막대 서너 개만 남아 추이가 안 보였다.
        #   국가·브랜드·매장·IP 필터는 그대로 적용된다(scope = 날짜만 빠진 프레임).
        _t_end = last_date
        _t_start = (pd.Timestamp(_t_end).normalize()
                    - pd.DateOffset(months=11)).replace(day=1).date()
        # ★★쓰는 컬럼만 떼어 복사한다 (2026-08-11, OOM 대응).
        #   캐시 프레임은 694만 행 × 26열 = 835MB 다. 통째로 마스킹하면 1년 창
        #   433만 행 × 26열이 새로 복사되고, 뒤이은 paid_sales·assign 이 또 복사해
        #   한 rerun 에 2~3GB 가 겹친다. 실제로 여기서 ArrayMemoryError 가 났다.
        #   이 카드가 보는 건 다섯 열뿐이다(취소 여부는 paid_sales 가 본다).
        _TREND_COLS = [c for c in ("날짜", "IP구분", "매출액", "국가", "취소 여부")
                       if c in scope.columns]
        _tw = scope.loc[(scope["날짜"] >= _t_start) & (scope["날짜"] <= _t_end),
                        _TREND_COLS]
        _tsales = paid_sales(_tw)
        if _tsales.empty:
            st.info("선택한 조건에 맞는 데이터가 없어요. 필터를 바꿔 보세요.")
        else:
            # ★구분 목록은 **1년 창에서** 뽑는다. present 는 조회 기간 기준이라,
            #   이번 달에만 안 팔린 구분이 1년 차트에서 통째로 빠진다.
            # ★유니크만 문자열로 바꾼다 — .astype(str) 을 열 전체에 걸면 433만 개
            #   파이썬 문자열 객체가 생긴다(categorical 을 푸는 셈).
            _tset = ({str(x) for x in _tsales["IP구분"].unique()} - {"제외"}
                     if "IP구분" in _tsales.columns else set())
            _tpresent = [g for g in IP_GUBUN_VIEW if g in _tset]
            _tsales = _tsales.assign(_d=pd.to_datetime(_tsales["날짜"]))
            _g = _tsales.groupby("_d")["매출액"].sum().rename("total").to_frame()
            # 구분별 값은 **툴팁용**으로만 붙인다. 5계열을 쌓으면 아래 계열이 위를
            # 통째로 밀어올려 어느 것도 자기 값으로 안 읽힌다(구성은 아래 비중 카드 몫).
            # ★구분마다 전체를 훑지 않고 **groupby 한 번**으로 전 구분을 동시에 낸다.
            #   예전엔 구분 수(6)만큼 433만 행 문자열 변환 + 스캔을 반복했다.
            if _tpresent:
                _tp = (_tsales.groupby(["_d", "IP구분"], observed=True)["매출액"]
                       .sum().unstack(fill_value=0))
                for _gb in _tpresent:
                    _g[_gb] = (_tp[_gb].reindex(_g.index).fillna(0)
                               if _gb in _tp.columns else 0)
            if "국가" in _tsales.columns:
                _g["한국"] = (_tsales[_tsales["국가"] == "한국"]
                              .groupby("_d")["매출액"].sum().reindex(_g.index).fillna(0))
            # 빈 날을 0으로 — 안 채우면 이동평균·주 집계가 날짜를 건너뛴다.
            _g = _g.asfreq("D").fillna(0) if len(_g) > 1 else _g
            trend_chart.render(st, _g, key="ph_trend", color="#4f46e5",
                               parts_cols=_tpresent,
                               kr_col="한국" if "한국" in _g.columns else None)
        helpbox("""
**매출 추이**
- ★**이 차트만 상단 조회 기간을 안 따라요 — 항상 최근 1년이에요.** 흐름은 길게 봐야 읽히는데 기간을 좁히면 막대가 서너 개만 남아서요. **국가·브랜드·매장·IP 필터는 그대로 적용돼요.**
- **보기 3가지** — `12개월·월`(막대 12개) · `12개월·주`(선 48점) · `최근 90일·일`(선 90점 + 7일 이동평균). 뭘 골라도 점이 12~90개예요.
  - ★**12개월을 일 단위로는 안 그려요.** 점이 341개라 화면 폭(점당 3px)보다 많아 읽을 수가 없고, 주말이 평일의 1.5배라 요일 흔들림이 추세보다 커서 오히려 방해가 돼요.
  - `주` 보기는 **양 끝의 잘린 주를 빼요.** 안 빼면 실제로 없는 U자 모양이 항상 생겨요.
  - `월` 보기의 마지막 달은 **사선**이에요 — 아직 진행 중인 부분 집계라 '급락'으로 오해하기 쉬워서요.
- **선은 하나(매출액 합계)**예요. 예전엔 IP구분 5개를 쌓았는데, 스택은 아래 계열이 위를 통째로 밀어올려서 **어느 구분도 자기 값으로 안 읽혀요.** 구분별 금액은 **툴팁에 숫자로** 나오고, 구성 비교는 바로 아래 '무엇이 매출을 만드나' 카드가 답해요.
- **위쪽 요약 줄**(최근 4주 · 직전 4주 대비)은 그래프를 안 봐도 답이 되게 항상 띄워요. 터치 기기에선 툴팁을 못 띄우거든요.
- 매출액 = 실결제 + 쿠폰기여 + 코인기여.
- IP구분 = **아티스트 · 캐릭터 · PICK · 오리지널(포토이즘) · 오리지널(기본) · 렌탈** (`IP_GUBUN_SHOWN`).
  - **렌탈·팝업도 2026-08-04 부터 포함**돼요. 다만 **'키오스크 1대당 매출' 카드에서만 빠져요** — 행사 기간만 도는 장비라 분모에 남으면 대당 매출이 실제보다 낮게 나와요.
  - 목록을 바꾸려면 `ip_classify.IP_GUBUN_SHOWN` 만 고치고 서버를 재시작하면 돼요(재집계 불필요).
  - 이 화면의 모든 카드가 같은 목록을 써요(추이·비중·상세 전부 동일 기준).
- ※ 공통 기준(원본·환율·매출액 정의)은 상단 'KPI 카드' 설명 참고.
""")

    sec("2", "무엇이 매출을 만드나",
        f"비중 — 어떤 구좌 타입·브랜드가 매출을 끄나 · "
        f"<b>조회 기간 {_dr}</b> 기준이에요")
    _c1, _c2 = st.columns(2)
    with _c1:
        with card("🎫 구좌 타입별 비중 <span class='muted'>IP구분별</span>"):
            # ★구좌(BASIC/WITH/EVENT) 3분류로 그리다가 **IP구분 5분류로 되돌렸다** —
            #   바로 위 '매출 추이'가 IP구분(아티스트·캐릭터·PICK·오리지널 2종)으로
            #   쌓이는데 그 아래 도넛만 다른 축이라, 같은 화면에서 두 분류를 눈으로
            #   맞춰야 했다. 색도 _GUB_COLORS 로 통일해 추이 막대와 그대로 짝이 맞는다.
            # ★categorical 에 .astype(str) 을 걸면 행 수만큼 문자열 객체가 생긴다.
            #   카테고리가 이미 문자열이라 그냥 isin 으로 같은 결과다(실측 동일·3배 빠름).
            gz = (sales[sales["IP구분"].isin(present)]
                  .groupby("IP구분", observed=True)["매출액"].sum()
                  .rename("매출").reset_index()) if present else pd.DataFrame()
            gz = gz[gz["매출"] > 0] if not gz.empty else gz
            if not gz.empty:
                gz = gz.sort_values("매출", ascending=False)
                colors = [_GUB_COLORS.get(str(g), "#c7ccd6") for g in gz["IP구분"]]
                css_donut(list(zip(gz["IP구분"].astype(str), gz["매출"])), colors)
            else:
                st.info("데이터가 없어요.")
            helpbox("""
**구좌 타입별 비중 (IP구분별)**
- `IP구분`별 매출액 비중(도넛) — **아티스트 · 캐릭터 · PICK · 오리지널(포토이즘) · 오리지널(기본)**.
- 위 '매출 추이'와 **같은 분류·같은 색**이에요. 추이는 시간축, 이 도넛은 기간 합계 비중이라 짝으로 봐요.
- 구좌(BASIC/WITH/EVENT)는 이 IP구분을 더 크게 묶은 상위 개념이에요 — 구좌 기준 숫자는 '매장별 분석' 탭의 구좌타입 분석에서 봐요.
""")
    with _c2:
        with card("🏷 브랜드 비중"):
            pc = (sales.groupby("브랜드", observed=True)["매출액"].sum().rename("매출")
                  .reset_index().sort_values("매출", ascending=False))
            pc = pc[pc["브랜드"].astype(str).str.strip().replace("nan", "").ne("") & (pc["매출"] > 0)]
            if len(pc) > 4:
                pc = pd.concat([pc.head(3), pd.DataFrame([{
                    "브랜드": f"기타 {len(pc) - 3}종",
                    "매출": int(pc.iloc[3:]["매출"].sum())}])], ignore_index=True)
            if not pc.empty and pc["매출"].sum() > 0:
                css_donut(list(zip(pc["브랜드"].map(brand_ko), pc["매출"])),
                          ["var(--brand-2)", "var(--amber)", "#7c77ee", "#c7ccd6"])
            else:
                st.info("데이터가 없어요.")
            helpbox("""
**브랜드 비중**
- `브랜드`(박스·컬러드 등 상품 종류)별 매출액 합. 요약이라 상위 3 + '기타 N종' 묶음.
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

    sec("3", "어디서 파나",
        f"지역 — 국가·매장별 매출(원화) · <b>조회 기간 {_dr}</b> 기준이에요")
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

# ════════════ 탭 2: 구좌타입 분석 (IP구분 = 구좌 세분) ════════════
with tab_ip:
    with card("🎭 IP 구분 (비중 · 매출) <span class='muted'>(아티스트·캐릭터·PICK·오리지널·렌탈)</span>"):
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

    # 포3.3/포4: 하위탭 = 전체 + 아티스트/캐릭터/PICK(타이틀 단위, 판매기간 지라) + 오리지널 2종(프레임 단위).
    #   오리지널은 본 집계에 타이틀이 없어(그룹 폭증 방지) 경량 오리지널 집계(load_orig)에서 프레임별로 뽑는다.
    # ★화이트리스트로 두지 않는다. 예전엔 ("아티스트","캐릭터","PICK") 를 나열했는데,
    #   IP_GUBUN_SHOWN 에 렌탈을 되살렸을 때 여기만 그대로라 렌탈 탭이 통째로 안 생겼다.
    #   오리지널만 프레임 단위로 따로 빼고, **나머지는 전부 타이틀 단위**로 돈다.
    #   (렌탈도 타이틀이 99.99% 채워져 있어 아티스트·캐릭터와 같은 방식이면 된다)
    _ORIG_GUBUNS = ("오리지널(포토이즘)", "오리지널(기본)")
    _orig_gubuns = [g for g in present if g in _ORIG_GUBUNS]
    _detail_gubuns = [g for g in present if g not in _ORIG_GUBUNS]
    # ★위젯(묶기·검색·프레임)을 탭 안에 두므로 **프래그먼트로 격리**한다.
    #   안 그러면 조작할 때마다 전체가 재실행돼 st.tabs 선택이 첫 탭으로 튕긴다.
    @st.fragment
    def _slot_detail():
      if _detail_gubuns or _orig_gubuns:
        with card("🎬 구좌별 상세 <span class='muted'>(전체·구좌별 → 타이틀/프레임별 매출)</span>"):
            # ── 묶기 · 검색 ────────────────────────────────────────────
            # ★한 IP 가 회차마다 다른 타이틀로 갈린다(260711 SM ent · 260721 SM ent …).
            #   같은 IP 인데 줄이 흩어져 규모가 안 보였다 → 'IP명' 으로 합칠 수 있게.
            #   기간 구분은 상단 조회 기간이 하므로 합쳐도 헷갈리지 않는다(사용자 확인).
            # 마지막 칸이 내려받기 — 버튼이 크다는 얘기가 있어 폭을 줄였다(1.0 → 0.78)
            _q1, _q2, _sp, _q3 = st.columns([1.5, 2.5, 1.74, 0.66])
            #   ★기본을 'IP명(회차 합산)' 으로 뒀다(요청) — 평소 보고 싶은 건 IP 규모지
            #     회차별로 쪼개진 줄이 아니다. 회차를 봐야 할 때만 '타이틀' 로 바꾼다.
            _grp = (_q1.segmented_control(
                "묶기", ["IP명(회차 합산)", "타이틀"], default="IP명(회차 합산)",
                key="ph_slot_grp", label_visibility="collapsed") or "IP명(회차 합산)")
            _kw = _q2.text_input("검색", key="ph_slot_q", label_visibility="collapsed",
                                 placeholder="🔍 타이틀·IP 이름으로 찾기").strip()
            _KEY = "타이틀" if _grp == "타이틀" else "IP명"
            # 검색은 Enter(또는 칸 밖 클릭)에 걸린다 — 스트림릿 text_input 의 기본이다.
            # 안내가 없어 "검색이 안 된다" 는 얘기가 나왔다(2026-08-19).
            st.caption("🔍 검색어를 넣고 **Enter** 를 눌러야 걸려요 · "
                       "이름 일부만 넣어도 돼요(`아이들` → `i-dle (아이들)`).")
            _gall = ["전체"] + _detail_gubuns + _orig_gubuns

            # ── 테마 표는 여기서 **한 번만** 읽는다 ────────────────────
            # 줄마다 접기를 붙이는데 접기 본문은 펼치지 않아도 먼저 돈다.
            # 줄마다 질의하면 열 줄이면 열 번이라, 기간 전체를 한 번 읽어 두고 쪼갠다.
            _cc = sales[["국가", "국가코드", "결제 단위"]].drop_duplicates("국가코드")
            _UNIT_MAP = {str(k).lower(): str(v).strip()
                         for k, v in zip(_cc["국가코드"], _cc["결제 단위"])}
            _sel_ccodes = (sorted({str(c).lower() for c in
                                   _cc.loc[_cc["국가"].isin(list(sel_countries)), "국가코드"]})
                           if sel_countries else [])
            _thall = (theme_all(date_range[0], date_range[1], _sel_ccodes, _UNIT_MAP)
                      if len(date_range) == 2 else pd.DataFrame())
            if not _thall.empty:
                # 테마 리포트엔 IP명이 없다 — 원장의 타이틀→IP 로 잇는다.
                # 묶기가 '타이틀' 이면 타이틀명 자체가 키다.
                _ipof = (sales[["타이틀명", "타이틀" if _KEY == "타이틀" else "IP명"]]
                         .dropna().astype(str).drop_duplicates("타이틀명")
                         .set_index("타이틀명").iloc[:, 0].to_dict())
                _thall = _thall.assign(_ip=_thall["타이틀명"].map(_ipof))
                _thall = _thall[_thall["_ip"].notna()]
                # ── 한/영 이름 통합 · 글자깨짐 교정 ───────────────────
                # 같은 멤버가 `리쿠(RIKU)` · `리쿠` · `RIKU` 로 갈려 있었다.
                # ★짝표는 **IP명** 으로 찾는다 — 묶기가 '타이틀' 이면 _ip 가
                #   '260624 SM ent' 라 표를 못 찾는다. 그래서 IP명을 따로 붙인다.
                # ★이름이 합쳐지면 줄이 겹치므로 **다시 합산**해야 한다.
                if not _thall.empty:
                    _ipname = (sales[["타이틀명", "IP명"]].dropna().astype(str)
                               .drop_duplicates("타이틀명")
                               .set_index("타이틀명")["IP명"].to_dict())
                    _thall = _thall.assign(
                        _ipn=_thall["타이틀명"].map(_ipname).fillna(""))
                    for _ax in ("테마", "프레임"):
                        _thall = name_alias.apply(_thall, _ax, "포토이즘", ip_col="_ipn")
                    _thall = (_thall.groupby(["타이틀명", "테마", "프레임", "_ip"],
                                             observed=True, as_index=False)
                              [["매출", "건수"]].sum())

            # 테마 리포트는 국가를 **코드**로, 금액을 **현지통화**로 들고 있다.
            # 원장에 둘 다 있으니 여기서 뽑아 넘긴다 — 따로 표를 두면 어긋난다.
            _cc = sales[["국가", "국가코드", "결제 단위"]].drop_duplicates("국가코드")
            _UNIT_MAP = {str(k).lower(): str(v).strip()
                         for k, v in zip(_cc["국가코드"], _cc["결제 단위"])}
            _sel_ccodes = (sorted({str(c).lower() for c in
                                   _cc.loc[_cc["국가"].isin(list(sel_countries)), "국가코드"]})
                           if sel_countries else [])

            # ★오리지널 집계를 **머리줄보다 먼저** 만든다 — 내려받기가 이걸 읽는데,
            #   버튼은 머리줄에서 눌리므로 그 시점에 값이 있어야 한다.
            #   (같은 실수를 필터바 내려받기에서 한 번 했다 — 2026-08-12)
            _od = pd.DataFrame()
            if _orig_gubuns:
                _od = load_orig()
                if not _od.empty and len(date_range) == 2:
                    _od = _od[(_od["날짜"] >= date_range[0]) & (_od["날짜"] <= date_range[1])]
                if not _od.empty and sel_countries:
                    _od = _od[_od["국가"].isin(list(sel_countries))]

            def _slot_export():
                """구좌별 상세를 **한 줄 = IP × 국가** 로 편다.

                ★화면 표를 그대로 뱉으면(이름·매출·건수) 받아서 할 게 없다. 담당자가
                  이 파일로 실제로 하는 일은 넷이다 —
                    ① IP사 보고: "어느 나라에서 얼마"  → 국가로 쪼개야 한다
                    ② 회차 비교: 260710 vs 260809      → 회차수·첫/마지막 거래일
                    ③ 반응 판단: 비싸게 팔렸나          → 건당 평균·매장수
                    ④ 엑셀 피벗                          → 합계행을 안 섞고 긴 형태로
                  그래서 국가로 펴고, 쓸 만한 파생 열을 붙여서 준다.
                탭은 st.tabs 라 어느 게 열려 있는지 알 수 없어 `구분` 열을 붙여 다 넣는다.
                """
                _rs = []
                for _gg in _detail_gubuns:
                    _s2 = sales[sales["IP구분"] == _gg]
                    _s2 = _s2[(_s2[_KEY] != "") & _s2[_KEY].notna()]
                    if _s2.empty:
                        continue
                    _t2 = (_s2.groupby([_KEY, "국가"], observed=True)
                           .agg(매출=("매출액", "sum"), 건수=("건수", "sum"),
                                매장수=("매장 이름", "nunique"),
                                첫거래일=("날짜", "min"), 마지막거래일=("날짜", "max"))
                           .reset_index())
                    _t2 = _t2[_t2["매출"] > 0].rename(columns={_KEY: "이름"})
                    _t2.insert(0, "구분", _gg)
                    if _KEY == "IP명":     # 몇 회차가 합쳐졌는지 — 합치면 안 보인다
                        _c2 = _s2.groupby("IP명", observed=True)["타이틀"].nunique()
                        _t2["회차수"] = _t2["이름"].map(_c2).fillna(1).astype("int64")
                    _rs.append(_t2)
                for _gg in _orig_gubuns:   # 오리지널은 프레임 단위 · 매장 정보가 없다
                    _o2 = _od[_od["IP구분"] == _gg] if not _od.empty else _od
                    if _o2.empty:
                        continue
                    _f2 = (_o2.groupby(["프레임", "국가"], observed=True)
                           .agg(매출=("매출액", "sum"), 건수=("건수", "sum"),
                                첫거래일=("날짜", "min"), 마지막거래일=("날짜", "max"))
                           .reset_index())
                    _f2 = _f2[(_f2["매출"] > 0) & _f2["프레임"].astype(str).str.strip().ne("")]
                    _f2 = _f2.rename(columns={"프레임": "이름"})
                    _f2.insert(0, "구분", _gg)
                    _rs.append(_f2)
                if not _rs:
                    return pd.DataFrame(columns=["구분", "이름", "국가", "매출", "건수"])
                _out = pd.concat(_rs, ignore_index=True)
                if _kw:
                    _out = _out[_out["이름"].astype(str).str.contains(_kw, case=False, na=False, regex=False)]
                if _out.empty:
                    return _out
                # 판매기간(지라)은 타이틀에만 붙는다 — IP명으로 합치면 회차가 여럿이라 못 적는다
                if _KEY == "타이틀" and _tstat:
                    _out["판매기간"] = _out["이름"].map(
                        lambda n: _period_str((_tstat.get(n) or {}).get("오픈일"),
                                              (_tstat.get(n) or {}).get("종료일")) or "")
                _out["매출"] = _out["매출"].round(0).astype("int64")
                _out["건수"] = _out["건수"].astype("int64")
                # 이름 단위 합계를 각 줄에 반복해 넣는다 — 합계행을 섞으면 피벗이 깨진다.
                # ★(구분, 이름) 으로 묶는다. 이름만으로 묶으면 오리지널 프레임 이름이
                #   IP명과 우연히 같을 때 합계가 섞인다. observed=True 는 카테고리형에
                #   필수 — 없으면 안 쓰는 조합까지 만들어 낸다.
                _g = _out.groupby(["구분", "이름"], observed=True)
                _tot = _g["매출"].transform("sum")
                _out["IP 매출 합계"] = _tot
                _out["국가 비중(%)"] = (_out["매출"] / _tot.replace(0, 1) * 100).round(1)
                _out["건당 평균"] = (_out["매출"] / _out["건수"].replace(0, 1)).round(0).astype("int64")
                _out["판매 국가 수"] = _g["국가"].transform("nunique")
                # ★'IP 매출 합계' 는 **정렬에만 쓰고 열로는 안 낸다**(요청). 줄마다 같은
                #   금액이 수십 번 반복돼 눈에 거슬린다 — 크기는 '국가 비중(%)' 로 읽는다.
                _cols = [c for c in ["구분", "이름", "회차수", "판매기간", "판매 국가 수",
                                     "국가", "매출", "국가 비중(%)", "건수",
                                     "건당 평균", "매장수", "첫거래일", "마지막거래일"]
                         if c in _out.columns]
                _out = (_out.sort_values(["IP 매출 합계", "이름", "매출"],
                                         ascending=[False, True, False])[_cols]
                        .reset_index(drop=True))
                # 머리줄에 단위를 박는다 — 받은 파일만 보고도 원인지 건인지 알게.
                return _out.rename(columns={
                    "매출": "매출(원)",
                    "건당 평균": "건당 평균(원)", "건수": "건수(건)",
                    "매장수": "매장수(개)", "회차수": "회차수(회)",
                    "판매 국가 수": "판매 국가 수(개국)"})

            def _theme_export():
                """줄을 눌러서 보는 **테마 · 프레임 구성**을 그대로 편다.

                ★1번 시트와 **합치지 않는다.** 원장(1번)과 테마 리포트(2번)는 출처가
                  달라 자정 경계에서 0.005% 어긋나고, 테마 리포트엔 오리지널이 아예
                  없다. 한 장에 섞으면 국가 합계가 조용히 두 번 세진다.
                ★이름은 화면과 같은 통합 표기(`리쿠(RIKU)`)로 나간다 — name_alias 를
                  이미 태운 _thall 을 쓰기 때문이다.
                """
                if _thall.empty:
                    return pd.DataFrame(columns=["이름", "테마", "프레임", "매출(원)"])
                _t = _thall.rename(columns={"_ip": "이름"})
                if _kw:
                    _t = _t[_t["이름"].astype(str).str.contains(_kw, case=False, na=False, regex=False)]
                if _t.empty:
                    return pd.DataFrame(columns=["이름", "테마", "프레임", "매출(원)"])
                _t = (_t.groupby(["이름", "테마", "프레임"], observed=True, as_index=False)
                      [["매출", "건수"]].sum())
                _t = _t[_t["매출"] > 0]
                if _t.empty:
                    return _t
                _t["매출"] = _t["매출"].round(0).astype("int64")
                _t["건수"] = _t["건수"].astype("int64")
                # 합계행을 섞지 않고 각 줄에 반복해 넣는다 — 피벗이 깨지지 않게.
                _ig = _t.groupby("이름", observed=True)["매출"].transform("sum")
                _tg = _t.groupby(["이름", "테마"], observed=True)["매출"].transform("sum")
                _t["IP 매출 합계"] = _ig
                _t["테마 매출 합계"] = _tg
                _t["IP 내 비중(%)"] = (_t["매출"] / _ig.replace(0, 1) * 100).round(1)
                _t["테마 내 비중(%)"] = (_t["매출"] / _tg.replace(0, 1) * 100).round(1)
                _t["건당 평균"] = (_t["매출"] / _t["건수"].replace(0, 1)).round(0).astype("int64")
                # 합계 두 열('IP 매출 합계'·'테마 매출 합계')은 **정렬에만 쓰고 안 낸다**(요청).
                # 줄마다 같은 금액이 반복돼 눈에 거슬린다 — 크기는 비중 두 열이 말해 준다.
                _t = (_t.sort_values(["IP 매출 합계", "이름", "테마 매출 합계", "매출"],
                                     ascending=[False, True, False, False])
                      [["이름", "테마", "프레임", "매출",
                        "IP 내 비중(%)", "테마 내 비중(%)", "건수", "건당 평균"]]
                      .reset_index(drop=True))
                return _t.rename(columns={
                    "매출": "매출(원)", "건수": "건수(건)", "건당 평균": "건당 평균(원)"})

            with _q3, st.container(key="dlbtn"):
                _dlb, _dlm = "ph_slot_dl_b", "ph_slot_dl_m"
                _sig = (_KEY, _kw, str(date_range), tuple(sel_countries), tuple(sel_stores))
                _mm = st.session_state.get(_dlm)
                _per = f"{date_range[0]}_{date_range[1]}" if len(date_range) == 2 else "전체기간"
                _HELP = (
                    "지금 이 표를 **엑셀 파일(.xlsx)** 로 받아요.\n\n"
                    "· 한 줄이 **IP × 국가** 라 국가별로 나눠 보거나 피벗을 바로 돌릴 수 있어요\n"
                    "· 매출·건수는 **쉼표가 찍힌 숫자**로 들어가요(합계·수식 그대로 돼요)\n"
                    "· 건당 평균 · 국가 비중 · 매장수 · 첫/마지막 거래일도 같이 들어가요\n"
                    "· 지금 화면의 **묶기 · 검색 · 기간 · 국가 · 매장** 조건이 그대로 반영돼요\n"
                    "· 시트가 **두 장**이에요 — `IP×국가` 와 "
                    "`테마·프레임`(줄을 눌러 보던 그 구성)\n"
                    "· 두 장은 출처가 달라요(원장 / CMS 프레임 리포트) — **더하지 마세요**")
                if st.session_state.get(_dlb) is not None and _mm and _mm[1] == _sig:
                    # ★받고 나면 '내려받기' 로 되돌린다 — 안 그러면 다 받은 뒤에도
                    #   '⬇ 4,436줄' 인 채로 남아 방금 만든 건지 헷갈린다(요청).
                    #   들고 있던 바이트도 같이 버려서 메모리도 돌려준다.
                    if auth.download_button(
                            f"⬇ 받기 · {_mm[0]:,}줄", st.session_state[_dlb],
                            f"포토이즘_구좌별상세_{_per}.xlsx",
                            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                            key="ph_slot_dl_get", use_container_width=True,
                            page="photoism", rows=_mm[0], help=_HELP):
                        st.session_state.pop(_dlb, None)
                        st.session_state.pop(_dlm, None)
                        _frag_rerun()
                # ★'만들기' 도 같이 막는다. 여기만 열어 두면 한참 만든 뒤에야
                #   받기 버튼이 비활성인 걸 알게 된다(auth.download_button 이 막는다).
                elif st.button("📗 엑셀 다운로드", key="ph_slot_dl_make",
                               use_container_width=True, disabled=not _CAN_DL,
                               help=_HELP if _CAN_DL else
                               "엑셀 다운로드는 팀장 권한이 있어야 해요."):
                    _d, _d2 = _slot_export(), _theme_export()
                    _cond = ("포토이즘 · 구좌별 상세  |  조회기간 "
                             + (f"{date_range[0]} ~ {date_range[1]}"
                                if len(date_range) == 2 else "전체")
                             + f"  |  묶기 {_KEY}" + (f"  |  검색 '{_kw}'" if _kw else "")
                             + (f"  |  국가 {', '.join(sel_countries)}"
                                if sel_countries else "")
                             + (f"  |  매장 {len(sel_stores)}곳 선택" if sel_stores else ""))
                    _money = ("금액 단위: 원(KRW) — 현지 통화 매출을 대시보드 환율표로 "
                              "원화 환산한 값이에요(정산서의 기준일 환율과 다를 수 있어요). "
                              "매출 = 실결제 + 쿠폰 + 서비스코인(지정 국가 가산) · 취소 반영.")
                    _sheets = {"IP×국가": _d}
                    _notes = {"IP×국가": [_cond, _money,
                                          "한 줄 = 이름 × 국가. 원장(결제 원본) 기준이에요."]}
                    if not _d2.empty:      # 줄을 눌러 보던 구성을 그대로 한 장 더
                        _sheets["테마·프레임"] = _d2
                        _notes["테마·프레임"] = [
                            _cond, _money,
                            "한 줄 = 이름 × 테마 × 프레임. 출처가 **CMS 프레임 리포트**라 "
                            "1번 시트(원장)와 자정 경계에서 0.005%쯤 달라요. "
                            "오리지널·매장 필터는 안 들어가요 — 두 장을 더하지 마세요.",
                            "이름 표기는 화면과 같아요 — 한/영으로 갈려 있던 건 "
                            "`리쿠(RIKU)` 처럼 합쳐서 나가요."]
                    st.session_state[_dlb] = xlsx_export.to_xlsx(_sheets, note=_notes)
                    st.session_state[_dlm] = (len(_d) + len(_d2), _sig)
                    _frag_rerun()

            _gtabs = st.tabs([("🗂 전체" if g == "전체" else f"{_GUB_EMOJI.get(g, '🎬')} {g}") for g in _gall])
            for _i, _g in enumerate(_gall):
                with _gtabs[_i]:
                    if _g in _orig_gubuns:
                        _os = _od[_od["IP구분"] == _g] if not _od.empty else _od
                        _f = pd.DataFrame()
                        if not _os.empty:
                            _f = (_os.groupby("프레임", observed=True)
                                  .agg(매출=("매출액", "sum"), 건수=("건수", "sum")).reset_index())
                            _f = _f[(_f["매출"] > 0) & _f["프레임"].astype(str).str.strip().ne("")]
                        statrow([("매출", fmt_krw(int(_os["매출액"].sum()) if not _os.empty else 0)),
                                 ("건수", f"{int(_os['건수'].sum()) if not _os.empty else 0:,}건"),
                                 ("프레임 수", f"{len(_f):,}개")])
                        st.caption("오리지널은 **프레임별** 매출 순위예요 · 매장 필터는 이 탭엔 적용 안 돼요(날짜·국가만).")
                        if _f.empty:
                            st.info("해당 조건에 맞는 데이터가 없어요. 날짜·국가 필터를 넓혀 보세요.")
                        else:
                            rank_table(_f.rename(columns={"프레임": "_n"}), "_n", collapse_after=10)
                    else:
                        _sub = sales if _g == "전체" else sales[sales["IP구분"] == _g]
                        _t = (_sub[(_sub[_KEY] != "") & _sub[_KEY].notna()]
                              .groupby(_KEY, observed=True)
                              .agg(매출=("매출액", "sum"), 건수=("건수", "sum")).reset_index())
                        _t = _t[_t["매출"] > 0]
                        if _kw:
                            _t = _t[_t[_KEY].astype(str).str.contains(_kw, case=False, na=False, regex=False)]
                            # ★카드도 같이 좁힌다. 안 그러면 '0개인데 매출 140억' 이 찍힌다.
                            _sub = _sub[_sub[_KEY].astype(str).isin(set(_t[_KEY].astype(str)))]
                        statrow([("매출", fmt_krw(int(_sub["매출액"].sum()))),
                                 ("건수", f"{tx_count(_sub):,}건"),
                                 (f"{_KEY} 수", f"{len(_t):,}개")])
                        if _t.empty:
                            st.info("해당 조건에 맞는 게 없어요. 검색어나 날짜·국가·매장 필터를 바꿔 보세요.")
                        else:
                            # ── 줄을 눌러 그 자리에서 펼치기 ──────────────
                            # ★표 모양(막대·칸)은 그대로 두고 **투명 버튼을 겹친다**.
                            #   접기(expander)로 바꿔 봤더니 막대가 사라져 "기존 화면에서
                            #   눌리면 좋겠다" 는 얘기가 나왔다. 오픈 캘린더에서 쓰던
                            #   방법을 그대로 가져왔다(ip_calendar_ui).
                            # ★정렬을 여기서 한다 — 전엔 rank_table 이 **안에서** 정렬해
                            #   줘서 이 표는 정렬 없이 넘겨도 됐다. 안 넘겨받아 1위 아일릿·
                            #   3위 코르티스로 뒤죽박죽이 된 적이 있다.
                            # ★오리지널 구분은 이 블록에 안 온다(위 분기에서 갈림) —
                            #   테마 리포트에 오리지널이 없어 열어도 빈 화면이다.
                            _t = _t.sort_values("매출", ascending=False)
                            _tot = int(_t["매출"].sum()) or 1
                            _mx = (_t["매출"] / _tot).max()

                            def _open_row(_nm, _g=_g):
                                _one = (_thall[_thall["_ip"].astype(str) == _nm]
                                        if not _thall.empty else pd.DataFrame())
                                with card(key=f"scard-open-{_g}"):
                                    _theme_portion(_one, _nm)

                            rank_rows_clickable(
                                _t, _KEY, _tot, _mx,
                                status_map=(_tstat or None) if _KEY == "타이틀" else None,
                                key_prefix=f"rr{_i}", top=10,
                                open_key=f"ph_slot_open_{_g}", on_open=_open_row)
                            if len(_t) > 10:
                                with st.expander(f"나머지 {len(_t) - 10:,}개 더보기 "
                                                 f"· 11~{len(_t):,}위"):
                                    rank_table(_t.iloc[10:], _KEY, collapse_after=40,
                                               nested_key=f"ph_slot_rest_{_g}",
                                               status_map=(_tstat or None)
                                               if _KEY == "타이틀" else None)
                                    st.caption("11위부터는 펼치기가 없어요 — "
                                               "검색으로 위로 올리면 눌러서 펼칠 수 있어요.")
        helpbox("""
**구좌별 상세**
- **묶기 `타이틀` / `IP명(회차 합산)`** — 같은 IP가 회차마다 다른 타이틀로 갈려요
  (`260711 SM ent` · `260721 SM ent` …). `IP명` 으로 바꾸면 한 줄로 합쳐져 규모가 보여요.
  **2026-06 이후 기준 132개 IP · 326개 타이틀이 합쳐져요**(SM ent 11회차 · 코르티스 6회차 …).
  기간 구분은 **위 조회 기간**이 하므로 합쳐도 섞이지 않아요.
- **🔍 검색** — 타이틀·IP 이름 일부로 걸러요.
- **테마·프레임**은 위쪽 **`🎨 테마 분석` 탭**으로 옮겼어요(2026-08-18). IP별로 묶어서 테마 → 프레임까지 한 화면에서 봐요.
- **전체 / 아티스트 / 캐릭터 / PICK / 렌탈** = `타이틀`(날짜+IP)별 매출액·건수 순위 + **판매기간**.
  - `IP명` 으로 묶으면 **판매기간은 안 붙어요** — 회차가 여럿이라 한 기간으로 못 적어요.
  - 오리지널을 뺀 나머지 구분은 자동으로 탭이 생겨요 — `IP_GUBUN_SHOWN` 만 고치면 돼요.
- **오리지널(포토이즘) / 오리지널(기본)** = `프레임`별 매출액·건수 순위(경량 집계). 오리지널은 타이틀이 아니라 프레임 단위라 따로 봐요.
  - ⚠️ 오리지널 탭은 **매장 필터가 적용되지 않아요**(날짜·국가만). 다른 탭은 필터바 전체 반영.
- '전체' 탭은 타이틀이 있는 구분(아티스트·캐릭터·PICK)만 합쳐요(오리지널은 프레임이라 제외).

**판매기간(오픈~종료)** — 지라 티켓의 **계획 오픈일(`startdate`) ~ 종료일(`duedate`)** 기준이에요.
- 실제 거래일이 아니라 **지라에 등록된 오픈·종료일**을 그대로 보여줘요. (예: `07-01 ~ 07-30`)
- 종료일이 비어 있으면 `진행중`, 지라가 연결 안 된 타이틀은 `—` 로 두고 **추측하지 않아요**
  (매칭은 타이틀명 정규화 + `ip_aliases.json` 별칭, 같은 IP가 양 브랜드에 있으면 포토이즘 티켓 우선).
- (이전의 신규/확인필요/판매중/종료 등 **상태 배지는 뺐어요.**)
""")

    _slot_detail()

    # 포3.3: '🎞 타이틀 전체 순위' 카드 제거 — 위 '구분별 타이틀 상세'의 '전체' 탭이 대체.
    #        (상태 배지·필터도 함께 제거, 판매기간은 지라 오픈~종료로 상세 탭에 표기.)

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
    # [제거] IP명 필터가 없어져 selected_ips 는 항상 비어 있다 → 위 카드는 안 그려진다.
    #        안내 문구도 뺐다(없는 필터를 가리키게 된다). 필터를 되살리면 그대로 부활.

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

        # 포3.2: 그래프(비중 도넛)를 국가별 탭 최상단으로 — 표보다 먼저 시각으로 보여준다.
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
- 국가별 매출액 기준 비중. 상위 7개국 + '기타 N개국' 묶음.
""")

        with card("🌏 국가별 매출"):
            # ★'쿠폰·코인'과 '비중'이 둘 다 %라 나란히 두면 같은 값의 다른 표현처럼 읽힌다
            #   (실사용자 지적). 셋으로 갈라 놓는다:
            #   ⑴ 머리글에 무엇의 비율인지 명시 + 툴팁  ⑵ 사이에 세로 구분선
            #   ⑶ 쿠폰·코인이 0인 나라(대부분)는 '—' 로 죽여 눈에 안 띄게.
            grid = "grid-template-columns:1.4fr .6fr .7fr 1.2fr 1.2fr .9fr 1.4fr"
            _sepc = "border-left:1px solid var(--border);padding-left:10px"
            html = (f'<div class="ntbl"><div class="ntr nth" style="{grid}">'
                    '<span>국가</span><span class="c">통화</span><span class="r">건수</span>'
                    '<span class="r">현지 매출</span><span class="r">KRW 매출</span>'
                    f'<span class="r tip dn" style="{_sepc}" data-tip="그 나라 매출 중 '
                    '쿠폰·서비스코인 정산분이 차지하는 몫이에요. 나라끼리 비교하는 값이 아니에요">'
                    '쿠폰·코인 <span style="font-weight:600">(그 나라 안)</span> ⓘ</span>'
                    f'<span class="tip dn" style="{_sepc}" data-tip="전체 매출에서 이 나라가 '
                    '차지하는 몫이에요. 다 더하면 100% 가 돼요">'
                    '전체 대비 비중 ⓘ</span></div>')
            for _, r in nat.iterrows():
                frac = (r["매출"] / tot) if tot else 0
                _cc = ((r["_쿠폰"] + r["_코인"]) / r["매출"]) if r["매출"] else 0
                _cctxt = (f'{_cc * 100:.0f}%' if _cc > 0.005
                          else '<span class="dash">—</span>')
                html += (f'<div class="ntr" style="{grid}">'
                         f'<span class="nname">{flag_img(r["국가"])}{r["국가"]}</span>'
                         f'<span class="c"><span class="cur">{r["결제 단위"]}</span></span>'
                         f'<span class="r num">{int(r["건수"]):,}</span>'
                         f'<span class="r num">{fmt_orig(r["현지"], r["결제 단위"])}</span>'
                         f'<span class="r num">{fmt_krw(r["매출"])}</span>'
                         f'<span class="r num" style="{_sepc}">{_cctxt}</span>'
                         f'<span style="{_sepc}">{pct_bar(frac, mx)}</span></div>')
            st.markdown(html + "</div>", unsafe_allow_html=True)
            st.caption(f"전체 {len(nat)}개국 · 매출 내림차순. "
                       "**두 %는 서로 다른 값이에요** — '쿠폰·코인'은 그 나라 매출 안에서의 "
                       "비율이고, '전체 대비 비중'은 전 세계 매출에서 그 나라가 차지하는 몫이에요.")
            helpbox("""
**국가별 매출 표**
- `국가`·`결제 단위`(통화)로 묶어: **건수**, **현지 매출**(`최종 결제 금액` 합, 현지통화), **KRW 매출**(매출액 합), **쿠폰·코인**, **전체 대비 비중**.
- ★**끝의 두 %를 헷갈리지 마세요.**
  - **쿠폰·코인 (그 나라 안)** = (쿠폰기여 + 코인기여) ÷ 그 나라 매출액. **분모가 그 나라**예요. 라오스처럼 높은 곳은 매출 대부분이 쿠폰·코인 정산분이에요. 쿠폰·코인을 정산하지 않는 나라는 `—` 로 나와요.
  - **전체 대비 비중** = 그 나라 매출액 ÷ 전체 매출액. **분모가 전체**예요. 다 더하면 100%가 돼요. 막대 길이는 1위 국가를 꽉 찬 것으로 둔 상대 길이예요.
""")

        # ── 키오스크 1대당 매출 ────────────────────────────────
        # 분자·분모 모두 렌탈·팝업을 뺀다. 렌탈은 행사 기간만 도는 장비라 남겨두면
        # 분모가 계속 살아 있는 것으로 잡혀 대당 매출이 실제보다 낮게 나온다.
        _dev = load_devices()
        if not _dev.empty and len(date_range) == 2 and "국가코드" in sales.columns:
            _pkd = (date_range[1] - date_range[0]).days + 1
            # ★렌탈 제외는 **IP구분 기준**이다. 브랜드로만 거르면 IP구분='렌탈'인데
            #   브랜드가 Box(5,654건)·Colored(13건)인 행이 새어 들어온다(2026년 실측).
            #   화면 전체는 렌탈을 살리되(IP_GUBUN_SHOWN) 이 카드만 계속 뺀다.
            #   ※ categorical 은 astype(str) 없이 그대로 비교한다(350만행 문자열 변환 회피).
            _keep = pd.Series(True, index=sales.index)
            if "IP구분" in sales.columns:
                _keep &= sales["IP구분"] != "렌탈"
            if "브랜드" in sales.columns:
                _keep &= sales["브랜드"] != "Rentals and pop-ups"
            _box = sales[_keep]
            # 조회기간에 매출이 난 (국가, 매장) — '매출 발생 대수'의 근거.
            # 먼저 중복을 지우고 문자열로 바꾼다(350만행을 그대로 astype(str) 하면 느리다).
            _sp = _box[["국가코드", "매장 이름"]].drop_duplicates()
            _sold = set(zip(_sp["국가코드"].astype(str).str.lower().str.strip(),
                            _sp["매장 이름"].astype(str).str.strip()))
            _dd = device_days(_dev, date_range[0], date_range[1], sold=_sold)
            _rev = (_box.groupby("국가코드", observed=True)
                    .agg(매출=("매출액", "sum"), 건수=("건수", "sum"), 국가=("국가", "first"))
                    .reset_index())
            _rev["국가코드"] = _rev["국가코드"].astype(str).str.lower().str.strip()
            per = _rev.merge(_dd, on="국가코드", how="inner")
            # 스4·포3.1: 분모를 '총 가동일'이 아니라 '대수'로. 조회기간이 30일이 아닐 때만
            #            30일로 환산(_pkd)해 '1대당 월매출' 라벨을 유지한다.
            # ★분모는 '가동 대수'가 아니라 **매출 발생 대수**다(2026-08-07 요청).
            #   계약상 가동중이어도 그 기간에 한 건도 안 판 장비가 분모에 남으면,
            #   장비를 많이 깔아둔 나라일수록 대당 매출이 실제보다 낮게 나온다.
            #   '실제로 돈을 번 장비 한 대가 얼마를 버는가'를 보는 지표로 통일한다.
            per = per[(per["매출대수"] > 0) & (per["매출"] > 0)].copy()
            per["대당월"] = (per["매출"] / per["매출대수"] / _pkd * 30).round(0).astype("int64")
            per["대당건"] = (per["건수"] / per["매출대수"] / _pkd * 30).round(1)
            per = per.sort_values("대당월", ascending=False)

            if not per.empty:
                with card("🎰 키오스크 1대당 매출 <span class='muted'>(렌탈·팝업 제외)</span>",
                          key="scard-perkiosk"):
                    # ★표본 하한 — 몇 대뿐인 나라는 매장 하나 성적이 그대로 국가 대표값이 돼
                    #   1위로 튄다(포토이즘에서 4대짜리 영국이 1위였다). 3대 고정은 한국 1,600대
                    #   옆에서 너무 얕아 최대 보유국의 1%(최소 3대)로 규모에 맞춘다.
                    #   숨기지는 않는다 — 기준 미달 국가는 표 아래쪽에 '표본 적음'으로 따로 보인다.
                    _MIN_DEV = max(3, int(-(-per["매출대수"].max() // 100)))
                    _big   = per[per["매출대수"] >= _MIN_DEV]
                    _small = per[per["매출대수"] < _MIN_DEV]
                    if _big.empty:                      # 전부 소규모면 하한을 접는다
                        _big, _small = per, per.iloc[0:0]
                    per = pd.concat([_big, _small])     # 둘 다 이미 대당월 내림차순
                    # 100%·헤더 이름은 기준을 넘긴 국가에서만 잡는다.
                    _mx   = _big["대당월"].max()
                    _lead = str(_big.iloc[0]["국가"])
                    grid = ("grid-template-columns:1.3fr .62fr .78fr .9fr 1.15fr "
                            ".85fr 1.05fr")
                    html = (f'<div class="ntbl"><div class="ntr nth" style="{grid}">'
                            '<span>국가</span><span class="r">가동 대수</span>'
                            '<span class="r tip dn" data-tip="조회기간에 매출이 난 매장에 '
                            '있는 장비 수예요. 거래 데이터에 장비 번호가 없어 매장 단위로 세요 — '
                            '한 매장에 2대가 있고 1대만 돌았어도 2대로 잡혀요">'
                            '매출 발생 ⓘ</span>'
                            '<span class="r">기간 내 변동</span>'
                            '<span class="r tip dn" data-tip="매출 ÷ 매출 발생 대수예요. '
                            '가동 대수가 아니라 실제로 판 장비만 나눠요 — 깔아만 두고 안 판 '
                            '장비까지 세면 대당 매출이 실제보다 낮게 나와요">1대당 월매출 ⓘ</span>'
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
                        # '표본 적음' 배지 제거(요청). 정렬(기준 미달 국가는 아래쪽)은
                        # 그대로 둔다 — 4대짜리 나라가 1위로 튀는 걸 막는 장치라서,
                        # 빼면 순위 자체가 못 믿을 값이 된다. 이유는 캡션에 남긴다.
                        # 매출 발생 대수 — 놀고 있는 장비가 많으면 눈에 띄게 색을 준다.
                        _act, _all = int(r.get("매출대수", 0)), int(r["대수"])
                        _ratio = (_act / _all) if _all else 0
                        _acol = ("var(--text-2)" if _ratio >= 0.9 else
                                 "var(--amber)" if _ratio >= 0.7 else "var(--red)")
                        html += (f'<div class="ntr" style="{grid}">'
                                 f'<span class="nname">{flag_img(r["국가"])}{r["국가"]}</span>'
                                 f'<span class="r num">{_all:,}대</span>'
                                 f'<span class="r num" style="color:{_acol}">{_act:,}대'
                                 f'<span style="font-size:11px;opacity:.75"> '
                                 f'{_ratio * 100:.0f}%</span></span>'
                                 f'<span class="r num" style="font-size:12px">{_chg}</span>'
                                 f'<span class="r num">{fmt_krw(int(r["대당월"]))}</span>'
                                 f'<span class="r num">{r["대당건"]:,.1f}건</span>'
                                 f'{pct_bar(r["대당월"] / _mx if _mx else 0)}</div>')
                    st.markdown(html + "</div>", unsafe_allow_html=True)

                    # 표 아래 '💡 총매출 1위 vs 1대당 1위' 안내와 긴 설명 캡션은 뺐다(요청).
                    # 같은 내용은 아래 helpbox(계산 방식 설명)에 그대로 남아 있다.

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
- **1대당 월매출 = (조회기간 매출 ÷ 매출 발생 대수) ÷ 조회일수 × 30**. **예상치가 아니에요.** 30일 조회면 ×30/30=×1 이라 그대로 한 달 실적, 아니면 30일치로 환산해요(7일만 보면 ×30/7). 월건수도 같은 식(매출 대신 건수).
  - **분자 = 조회기간 매출 = 실결제 + 쿠폰·코인(정산금액)** · **분모 = 매출 발생 대수 = 그 기간에 실제로 판 장비 수**(렌탈·팝업 제외).
  - ★**가동 대수가 아니라 매출 발생 대수로 나눠요**(2026-08-07 변경). 계약상 가동중이어도 한 건도 안 판 장비까지 세면, 장비를 많이 깔아둔 나라일수록 대당 매출이 실제보다 낮게 나와요. '실제로 돈을 번 한 대가 얼마를 버는가'를 보는 값이에요.
  - 그래서 **짧은 기간을 보면 그 며칠의 편차(주말·이벤트)가 30배로 커져** 보여요. 최소 2~4주로 보는 걸 권해요.
  - ⚠️ 매출 발생 대수는 **매장 단위**로 세요. 거래 데이터에 장비 번호가 없어서, 한 매장에 2대가 있고 1대만 돌았어도 2대로 잡혀요.
- **'○○ 대비'** = 이 표의 1위, 즉 **1대당 매출이 가장 높은 국가**를 100%로 둔 비율이에요. 헤더에 그 나라 이름이 그대로 나와요.
  - ★**총매출 1위와 다른 나라일 수 있어요.** 한국은 총매출은 1위지만 1대당으로는 아래쪽이라 100%가 아니에요.
  - ⚠️ **표본이 적은 국가**(매출 발생 대수가 기준 미만)는 표 아래쪽으로 내려요. 매장 한 곳 성적이 그대로 국가 대표값이 돼서 1위로 튀거든요. 기준은 **최대 보유국의 1%**(최소 3대)라 나라 규모가 커지면 같이 올라가요. 100%와 헤더 국가명도 기준을 넘긴 나라에서만 잡아요. (배지 표기는 뺐고, 몇 개국이 내려갔는지는 표 아래 캡션에 나와요.)
  - 위 '국가별 매출' 표의 **비중(전체 대비 점유율)과도 다른 값**이에요.
- **기간 내 변동** = 이 기간에 새로 깔린 대수(+)와 지금 '중지' 상태인 대수.
- 아래 **설치 이력**(월별 신규 설치)을 펼치면 어느 달에 증설했는지 보여요. 매출이 뛴 시점과 겹치는지 보면 증설 효과를 가늠할 수 있어요.
- 분자·분모 모두 **렌탈·팝업을 뺐어요**. 행사용 장비는 잠깐만 도는데 분모에 계속 남아 왜곡돼요.
- 가동 대수는 CMS 장비관리 기준이에요(설치일은 기기 S/N 앞 6자리 YYMMDD). '중지' 상태 장비는 분모(가동 대수)에서 빠져요.
""")

        # ── 국가 × IP구분 ───────────────────────────────────────────
        # ★매장별 탭에 있던 표를 여기로 옮겼다(2026-08-18). 축이 **국가**라 여기가
        #   제자리다 — 매장별 탭에 있으니 탭 이름과 내용이 어긋나 있었다.
        # ★옮기면서 기준이 바뀐다: 전엔 매장별 탭 전용 필터(국가·상품)를 탔고
        #   지금은 **상단 필터바**를 탄다. 표 모양은 같아도 숫자가 달라진다.
        _nsegs = [g for g in present if g in _GUB_COLORS] or list(present)
        if _nsegs and "IP구분" in sales.columns:
          with card("🎫 국가 × IP구분 <span class='muted'>나라마다 구성이 어떻게 다른가</span>",
                    key="scard-natgz"):
            # IP구분이 노출 대상이 아닌 행이 소수 섞일 수 있다. 그대로 두면 칸별 비중
            # 합이 100%가 안 되므로 걷어내고, 얼마를 뺐는지 캡션에 밝힌다.
            _gsrc = sales[sales["IP구분"].isin(_nsegs)]
            _drop = int(sales["매출액"].sum()) - int(_gsrc["매출액"].sum())
            _ng = (_gsrc.groupby(["국가", "IP구분"], observed=True)["매출액"].sum()
                   .reset_index())
            _ng = _ng[_ng["매출액"] > 0]
            if _ng.empty:
                st.info("해당 조건에 데이터가 없어요. 필터를 넓혀 보세요.")
            else:
                _piv = (_ng.pivot_table(index="국가", columns="IP구분", values="매출액",
                                        aggfunc="sum", fill_value=0, observed=True)
                        .reindex(columns=_nsegs, fill_value=0))
                _piv["합계"] = _piv.sum(axis=1)
                _piv = _piv.sort_values("합계", ascending=False)
                _grid = f"grid-template-columns:1.3fr repeat({len(_nsegs)},1fr) 1.05fr 1.1fr"
                _h = (f'<div class="ntbl"><div class="ntr nth" style="{_grid}">'
                      '<span>국가</span>'
                      + "".join(f'<span class="r">{x}</span>' for x in _nsegs)
                      + '<span class="r">합계</span><span class="vs">구성</span></div>')
                for _n, _r in _piv.iterrows():
                    _t3 = _r["합계"] or 1
                    _bar = "".join(
                        f'<i style="width:{_r[x] / _t3 * 100:.2f}%;'
                        f'background:{_GUB_COLORS.get(x, "#c7ccd6")}"></i>'
                        for x in _nsegs if _r[x] > 0)
                    _h += (f'<div class="ntr" style="{_grid}">'
                           f'<span class="nname">{flag_img(_n)}{_n}</span>'
                           + "".join(
                               f'<span class="r num">{fmt_krw(int(_r[x]))}</span>' if _r[x]
                               else '<span class="r num" style="color:var(--text-3)">–</span>'
                               for x in _nsegs)
                           + f'<span class="r num"><b>{fmt_krw(int(_r["합계"]))}</b></span>'
                           f'<span class="vs"><span class="gzbar" style="flex:1">{_bar}'
                           '</span></span></div>')
                st.markdown(_h + "</div>", unsafe_allow_html=True)
                _c2 = (f"{len(_piv)}개국 · 매출 내림차순. 막대는 그 나라 안에서의 "
                       "구성비예요(나라끼리의 크기 비교가 아니에요). "
                       "분류·색은 '매출 추이'와 같아요.")
                if _drop:
                    _c2 += (f" IP구분이 분류되지 않은 {fmt_krw(_drop)}은 이 표에서만 "
                            "빠져 있어요.")
                st.caption(_c2)
            helpbox("""
**국가 × IP구분**
- 나라마다 **무엇으로 매출이 나는지** 구성을 비교해요. 맨 오른쪽 막대는 **그 나라 안에서의 구성비**(100% 기준이 나라마다 달라요) — 나라끼리 크기를 비교하는 막대가 아니에요.
- 나누는 축은 `IP구분` 5종 — 아티스트 · 캐릭터 · PICK · 오리지널(포토이즘) · 오리지널(기본). **'매출 추이'·'구좌 타입별 비중'과 같은 분류·같은 색**이에요.
- 원래 **매장별 분석 탭**에 있던 표예요(2026-08-18 이동). 그땐 그 탭의 전용 필터(국가·상품)를 탔지만, 지금은 **상단 필터바**를 타요 — 그래서 예전과 숫자가 다를 수 있어요.
""")

        # [숨김] '국가별 타이틀 TOP 10' — UI 에서만 뺐다(요청). 코드·데이터는 그대로다.
        #        되살리려면 SHOW_NAT_TITLE = True 로만 바꾸면 된다(위 SHOW_TAB_ETC 와 같은 방식).
        if SHOW_NAT_TITLE:
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
# ★전용 필터(국가·상품)를 뺐다(2026-08-18 요청). **상단 필터바를 그대로 따른다.**
#   원래는 "매장 순위를 국가·상품 조합으로 빠르게 훑어보라"고 둔 것이었는데,
#   상단 필터바에 이미 국가·상품이 있어 같은 걸 두 겹으로 걸게 돼 있었다.
#   두 겹이면 화면의 숫자가 어느 필터의 결과인지 헷갈리고, 실제로 상단을 풀어도
#   여기 선택이 남아 몰래 걸리는 사고가 난다(같은 종류를 오늘 필터바에서 고쳤다).
# ※위젯이 없어졌으니 @st.fragment 도 뗀다 — 격리할 조작이 더는 없다.
def _store_tab(sales, date_range, sel_countries):
    with card("🏬 매장 전체 순위"):
        ss = (sales.groupby("매장 이름", observed=True)
              .agg(매출=("매출액", "sum"), 건수=("건수", "sum")).reset_index())
        ss = ss[ss["매출"] > 0]
        # ★이 탭의 축은 **매장**이다. 매출·건수는 맨 위 요약과 다른 탭에 이미 있고,
        #   여기서 알고 싶은 건 '이 조건에 매장이 몇 개인가' 하나다(요청).
        statrow([("매장 수", f"{sales['매장 이름'].nunique():,}개"),
                 ("매출 발생 매장", f"{len(ss):,}개")])
        st.caption("상단 필터바(기간·국가·매장·상품·IP)를 그대로 따라요.")
        if ss.empty:
            st.info("해당 조건에 맞는 매장이 없어요. 위 필터바를 넓혀 보세요.")
        else:
            # 매장 전체 순위 = 전체 목록이라 비중을 켜도 분모가 맞다.
            hbar_list(ss, "매장 이름", collapse_after=10, show_pct=True)
        helpbox("""
**매장 전체 순위**
- **상단 필터바**(기간·국가·매장·상품·IP)로 거른 결과의 `매장 이름`별 매출액 합·건수 순위예요.
  (예전엔 이 탭에만 있는 국가·상품 필터를 한 겹 더 걸었는데, 상단 필터바와 겹쳐서 뺐어요.)
- **매장 수** = 그 조건에 나타난 매장 개수, **매출 발생 매장** = 그중 매출이 0보다 큰 곳이에요.
  둘이 다르면 그 차이만큼은 **기간 안에 거래가 없던 매장**이에요.
""")


with tab_store:
    _store_tab(sales, date_range, sel_countries)

# ════════════ [제거] 세부 항목 검색 — SHOW_TAB_DETAIL=True 로 부활 ════════════
#   (부활 시 탭에 그리려면 아래 블록을 `with tab_detail:` 로 감싸 주세요.)
if SHOW_TAB_DETAIL:
    @st.fragment
    def _detail_search(date_range, selected_ips, sel_countries,
                       sel_stores, sel_brands, sel_gubuns):
        with card("🔎 세부 판매 항목 검색 <span class='muted'>(프레임 / 구좌 등)</span>"):
            st.caption("전체 거래에서 프레임·구좌 등 세부 항목을 분류별로 모아 봐요. "
                       "위 필터바(날짜·국가·매장·상품·IP)가 그대로 적용돼요.  "
                       "※ 같은 타이틀명이 단가만 다르게 등록된 경우(예: 마카오)는 "
                       "**「타이틀 (이름+단가별)」** 을 고르면 단가별로 나눠서 볼 수 있어요.")
            helpbox("""
**세부 판매 항목 검색**
- 선택한 `분류 기준`(프레임·구좌·타이틀 등)으로 **DuckDB에서 원거래를 직접 집계**(필터바 날짜·국가·매장·상품·IP 반영). 매출액 = 실결제 + 지정국가 쿠폰·코인.
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
                    detail_df["항목"].astype(str).str.contains(
                        search_kw.strip(), case=False, na=False, regex=False)]
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
                auth.download_button("세부 항목 CSV 다운로드", csv_d,
                                     f"photoism_detail_{DETAIL_DIMS[sel_dim_label]}.csv", "text/csv",
                                     key="ph_detail_csv",
                                     page="photoism", rows=len(d_show))

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
                auth.download_button("CSV 다운로드 (전체)", csv_export,
                                     "photoism_filtered.csv", "text/csv",
                                     page="photoism", rows=len(view))
            else:
                st.caption("체크하면 현재 필터 기준 집계 데이터를 표로 불러와요.")
