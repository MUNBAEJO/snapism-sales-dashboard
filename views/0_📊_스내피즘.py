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
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from guide_content import render_guide
import name_alias  # 프레임 한/영 통합 + 글자깨짐 교정
import data_io
import auth
import xlsx_export  # 내려받기 → 엑셀(.xlsx)
import trend_chart

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
  /* ★축약형 `background:` 금지 — background-image 까지 none 으로 지워
     auth.render_watermark 의 워터마크가 통째로 사라진다. 색만 지정할 것. */
  background-color:var(--bg) !important; }
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
/* k4 = 합계+실결제+쿠폰+취소 4칸 (포토이즘은 취소가 없어 3칸) */
.kpis.k4{ grid-template-columns:1.8fr 1fr 1fr 1fr; }
.kpi{ background:var(--surface); border:1px solid var(--border); border-radius:12px; padding:15px 17px;
      box-shadow:0 1px 2px rgba(20,28,45,.04),0 1px 3px rgba(20,28,45,.06); }
.kpi.hero{ background:linear-gradient(180deg,#fbfbff,#fff); border-color:#dcdcfb; }
.kpi .l{ font-size:12.5px; color:var(--text-2); font-weight:600; }
.kpi .v{ font-size:24px; font-weight:800; letter-spacing:-0.02em; margin-top:6px; line-height:1.05; color:var(--text); }
.kpi.hero .v{ font-size:33px; color:var(--brand); }
.kpi .d{ font-size:12px; font-weight:700; margin-top:7px; color:var(--text-3); }
@media(max-width:1100px){ .kpis.k4{ grid-template-columns:1fr 1fr; } }
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
/* ★건수(우측정렬)와 판매기간(좌측정렬)이 gap 10px 만 두고 맞붙어 한 값처럼 읽혔다.
   칸 경계선 + 넉넉한 안쪽 여백으로 확실히 끊는다. 세로선이 행 높이를 꽉 채워야
   눈에 걸리므로 align-self:stretch + flex 로 글자를 다시 가운데 맞춘다. */
.ntr .vs{ border-left:1px solid var(--border); padding-left:16px; margin-left:6px;
          align-self:stretch; display:flex; align-items:center; }
.ntr.nth .vs{ border-left-color:var(--border-strong); }
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
/* .pct = 매장 전체순위처럼 비중까지 보여주는 변형(칸 하나 더) */
.hb.pct{ grid-template-columns:140px 1fr 112px 60px !important; }
.hb-p{ text-align:right; font-weight:700; color:var(--text-3); font-variant-numeric:tabular-nums; font-size:12px; }
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
/* 시간대 막대 */
.hours{ display:flex; align-items:flex-end; gap:5px; height:180px; border-bottom:1px solid var(--border); padding-top:8px; }
.hours .hc{ flex:1; display:flex; flex-direction:column; justify-content:flex-end; align-items:center; height:100%; }
.hours .hb2{ width:70%; border-radius:3px 3px 0 0; }
.hours .hx{ font-size:9.5px; color:var(--text-3); margin-top:4px; }
/* 정보 스트립(시안 .strip) */
.strip{ font-size:12.5px; color:var(--text-2); background:var(--surface-2); border:1px solid var(--border);
        border-radius:10px; padding:9px 14px; margin-top:12px; }
.strip b{ color:var(--text); font-weight:700; }

/* 미니 지표 3~4칸 (포토이즘과 동일) */
.mstrow{ display:flex; gap:12px; margin:2px 0 12px; flex-wrap:wrap; }
.mst{ flex:1; min-width:110px; background:var(--surface-2); border:1px solid var(--border);
      border-radius:10px; padding:10px 14px; }
.mst-l{ font-size:11.5px; color:var(--text-2); font-weight:600; }
.mst-v{ font-size:18px; font-weight:800; color:var(--text); margin-top:3px; }


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
/* ★표(.ntbl)는 모서리를 둥글리려 overflow:hidden 이라, 위로 뜨는 기본 툴팁이 통째로
   잘려 안 보인다. 표 헤더에는 .dn 을 붙여 '아래쪽·오른쪽 정렬'로 표 안에서 펼친다. */
.tip.dn::after{ bottom:auto; top:100%; left:auto; right:0; transform:translateY(7px);
  white-space:normal; width:max-content; max-width:260px; text-align:left; font-weight:600; }
.tip.dn::before{ bottom:auto; top:100%; left:auto; right:12px; transform:translateY(2px);
  border-top-color:transparent; border-bottom-color:#1b2330; }
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
/* 엑셀 다운로드 버튼 — 카드 머리줄에 얹히는 보조 버튼이라 작게(요청 2026-08-12) */
.st-key-dlbtn [data-testid="stButton"] button,
.st-key-dlbtn [data-testid="stDownloadButton"] button{
  min-height:0 !important; height:27px !important; padding:0 8px !important;
  border-radius:7px !important; }
.st-key-dlbtn [data-testid="stButton"] button p,
.st-key-dlbtn [data-testid="stDownloadButton"] button p{
  font-size:11px !important; font-weight:700 !important; letter-spacing:-.01em !important; }
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
.st-key-scard-hstore, .st-key-scard-prodsel, .st-key-scard-natframe{ position:relative; }
.st-key-scard-hstore [data-testid="stElementContainer"]:has(> [data-testid="stSelectbox"]),
.st-key-scard-prodsel [data-testid="stElementContainer"]:has(> [data-testid="stSelectbox"]),
.st-key-scard-natframe [data-testid="stElementContainer"]:has(> [data-testid="stSelectbox"]){
  position:absolute !important; top:16px !important; right:18px !important; width:auto !important;
  margin:0 !important; z-index:5 !important; }
/* 드롭다운 박스를 내용 크기로 축소(글자+화살표 딱 붙게) — 시안 컴팩트 톤 */
.st-key-scard-hstore [data-testid="stSelectbox"], .st-key-scard-prodsel [data-testid="stSelectbox"],
.st-key-scard-natframe [data-testid="stSelectbox"]{ width:auto !important; min-width:0 !important; }
.st-key-scard-hstore [data-testid="stSelectbox"] div[data-baseweb="select"],
.st-key-scard-prodsel [data-testid="stSelectbox"] div[data-baseweb="select"],
.st-key-scard-natframe [data-testid="stSelectbox"] div[data-baseweb="select"]{
  width:fit-content !important; min-width:96px !important; }
/* ★겹침 수정: 우상단 드롭다운이 1위 값과 겹치던 문제.
   원인 = 드롭다운 절대위치의 기준(offsetParent)이 카드가 아니라 @st.fragment 가 만든
   내부 stVerticalBlock(제목 아래 지점) 이었다 → top:16px 가 제목 밑에서 시작해 첫 줄과 겹침.
   내부 블록(카드 바로 밑 한 겹)을 static 으로 두면 기준이 카드가 돼 드롭다운이 제목 줄로 올라간다.
   (expander 등 더 깊은 블록은 건드리지 않게 직계 자식만 겨냥.) */
.st-key-scard-hstore > [data-testid="stVerticalBlockBorderWrapper"] > [data-testid="stVerticalBlock"],
.st-key-scard-prodsel > [data-testid="stVerticalBlockBorderWrapper"] > [data-testid="stVerticalBlock"],
.st-key-scard-natframe > [data-testid="stVerticalBlockBorderWrapper"] > [data-testid="stVerticalBlock"]{ position:static !important; }
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
  .hb.pct{ grid-template-columns:92px 1fr 82px 44px !important; }
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


# ★ 인자 이름에 밑줄(_)을 붙이면 안 된다 — st.cache_data 는 **밑줄로 시작하는 인자를
#   해시에서 제외**한다. `_v` 라 파일 버전이 캐시 키에 안 들어갔고, 그래서 09:00 수집
#   이후에도 ttl(15분)이 지나야 새 데이터가 보였다. 포토이즘은 2026-07-28 에 고쳤는데
#   여기만 남아 있었다(2026-08-03 수정).
# ★★cache_resource 다 — 반환 프레임을 **절대 in-place 로 수정하지 말 것**.
#   같은 객체가 전 사용자에게 공유되므로 한 곳에서 열을 붙이면 모두에게 번진다.
#   '필터 적용' 절 첫 줄의 `df = df_all.copy()` 가 유일한 격리막이니 지우지 말 것.
@st.cache_resource(ttl=900, max_entries=1)   # 파일 버전 키 → 최신 1개만 유효
def _load_data(v):
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
    # 프레임 이름의 한/영 통합 + 글자깨짐 교정. **캐시에 넣기 전** 한 번만 태운다 —
    # 필터·순위·내려받기가 전부 이 열을 보므로 여기서 통일해야 다 같이 맞는다.
    # ★행마다 부르면 안 된다. fold() 는 NFKC 정규화 + 글자별 치환 + split/join 이라
    #   45만 행에 태우면 콜드 로드가 눈에 띄게 늘어난다. 프레임 이름 고유값은
    #   수천 개뿐이니 **고유값에서 한 번 만들고 map 으로 붙인다**(결과 동일).
    _s = df["프레임 이름"].astype(str)
    _fm = name_alias.mapping("스내피즘", "프레임")
    _fold = {v: _fm.get(name_alias.fold(v), name_alias.fold(v)) for v in _s.unique()}
    df["프레임 이름"] = _s.map(_fold)
    return df


def load_data():
    return _load_data(data_io.file_version(MASTER_FILE))


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


def paid_sales(df):
    return df[~df["취소 여부"] & (df["최종 결제 금액"] > 0)]


def coupon_txns(df):
    return df[~df["취소 여부"] & (df["최종 결제 금액"] == 0) & (df["쿠폰 할인 금액"] > 0)]


def revenue_txns(df):
    """★매출 집계의 공통 기준 거래 = 취소 아님 AND (실결제>0 **또는** 쿠폰>0).

    2026-07-28 개편: 예전엔 카드들이 `paid_sales`(실결제>0) + `KRW환산금액` 을 써서
    **전액 쿠폰 결제 국가(대만·말레이시아·홍콩·태국)가 전부 0원으로 사라졌다**
    (대만은 매출 2위인데 순위·비중에서 빠져 있었음). KPI 큰 숫자만 정산금액이라
    카드 합계와 10% 어긋나기도 했다.
    이제 모든 카드가 이 함수 + `정산금액`(= 실결제 + 쿠폰)으로 계산한다 — 포토이즘의
    `매출액` 과 같은 역할. (쿠폰 가산 국가 규칙은 브랜드마다 달라 공식 자체는 따로 둔다:
    스내피즘=전 국가 가산, 포토이즘=지정 국가만.)
    """
    return df[~df["취소 여부"] & ((df["최종 결제 금액"] > 0) | (df["쿠폰 할인 금액"] > 0))]


# ── 키오스크(스내피즘 어드민) ──────────────────────────────────
# 포토이즘과 어드민이 아예 달라 별도 파일이다. 대신 이쪽은 **계약 기간(시작~종료)**이
# 있어서 가동 구간을 어림하지 않고 정확히 자를 수 있다. (device_ingest_snapism.py)
@st.cache_data(ttl=1800, show_spinner=False, max_entries=1)
def _load_devices(mtime):
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


def device_days(dev, p0, p1, sold=None):
    """국가코드별 '대·일'(가동 키오스크 × 실가동일수)·대수·기간 내 신규/종료.

    계약 [시작일, 종료일] 과 조회기간의 겹치는 날짜만 센다. 포토이즘은 철거일이 없어
    중지 장비를 통째로 뺐지만, 스내피즘은 계약 종료일이 있어 그럴 필요가 없다.

    sold: {(국가코드, 매장이름)} — 조회기간에 매출이 난 매장 집합. 주면 '매출대수'를
          함께 낸다. ★거래 데이터에 **장비 번호가 없어서** 매장 단위로만 이을 수 있다 —
          한 매장에 2대가 있고 1대만 돌았어도 2대로 잡힌다. 포토이즘 views/1 도 같다.
    """
    empty = pd.DataFrame(columns=["국가코드", "대수", "대일", "신규", "종료", "매출대수"])
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
    if sold is not None and "매출매장명" in d.columns:
        # ★스내피즘 거래 데이터엔 국가코드가 없다(국가 이름만) → 장비쪽 코드를 이름으로 바꿔 맞춘다.
        _m = d["매출매장명"].astype(str).str.strip()
        _n = d["국가코드"].map(CC_TO_NAT)
        t["매출대수"] = [1 if (n, s) in sold else 0
                         for n, s in zip(_n, _m)]
    else:
        t["매출대수"] = 0
    t = t[t["대일"] > 0]
    if t.empty:
        return empty
    return (t.groupby("국가코드").agg(대수=("대일", "size"), 대일=("대일", "sum"),
                                      신규=("신규", "sum"), 종료=("종료", "sum"),
                                      매출대수=("매출대수", "sum"))
            .reset_index())


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


def statrow(items):
    """미니 지표 3~4칸. items=[(label, value)]. (포토이즘과 동일)"""
    cells = "".join(
        f'<div class="mst"><div class="mst-l">{l}</div><div class="mst-v num">{v}</div></div>'
        for l, v in items)
    st.markdown(f'<div class="mstrow">{cells}</div>', unsafe_allow_html=True)


def cat3(series):
    s = series.astype(str).str.strip()
    return s.where(s.isin(["아티스트", "캐릭터"]), "기타")


# ※ Plotly 헬퍼 style_fig/donut/legend_list 는 2026-08-03 에 지웠다.
#   차트가 CSS(css_donut 등)로 전부 넘어가면서 셋 다 호출처가 0이 됐고,
#   plotly import 2줄도 같이 나갔다(포토이즘 페이지엔 애초에 plotly 가 없다).


def hbar_list(dframe, name_col, top=None, collapse_after=None, show_pct=False):
    """시안 TOP 스타일 가로막대(이름 | 트랙+채움 | 금액). 1위=브랜드색, 나머지=연한 블루.
    collapse_after=N 이면 상위 N개만 보이고 나머지는 '더보기' 접기."""
    d = dframe.sort_values("매출", ascending=False).reset_index(drop=True)
    if top:
        d = d.head(top)
    mx = d["매출"].max() or 1
    # show_pct: 전체 대비 비중을 오른쪽에 덧붙인다. **전체 목록일 때만 켤 것** —
    # TOP 5 같은 부분집합에 켜면 분모가 5개가 돼 "1위 60%" 같은 오해를 부른다.
    _tot = d["매출"].sum() if show_pct else 0

    def _rows(sub):
        h = '<div class="hb-wrap">'
        for i, r in sub.iterrows():
            w = max(3, r["매출"] / mx * 100)
            col = BRAND if i == 0 else "#a9c7ef"   # 전체 1위만 브랜드색(원본 인덱스 유지)
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


# ※ _STAT_CLS(상태 배지 색 매핑)는 배지 자체를 걷어내면서 참조가 0이 됐다 — 2026-08-03 삭제.
#   포토이즘 쪽은 같은 상수를 _STAT_CLS_UNUSED 로 이름만 바꿔 두었다.


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
                # 스2: 상태 배지(신규/확인필요/종료 등) 제거. 이름은 그대로 두고,
                #      판매기간은 지라 티켓의 '오픈~종료'(계획 시작~종료일) 기준으로 표기한다.
                _o = _md(s.get("오픈일"))
                _e = _md(s.get("종료일"))
                _ps = f'{_o or "?"} ~ {_e or "진행중"}' if s and (_o or _e) else "—"
                per = f'<span class="tper num vs">{_ps}</span>'
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


def fmt_short(n):
    """막대 밑에 붙일 짧은 금액. 일 단위면 막대가 30개가 넘어 원 단위는 안 들어간다."""
    n = int(n or 0)
    if abs(n) >= 100_000_000:
        return f"₩{n / 100_000_000:,.1f}억"
    if abs(n) >= 10_000:
        return f"₩{n / 10_000:,.0f}만"
    return f"₩{n:,}"


def _area_chart(rows, series):
    """면적 선그래프(SVG). rows=[(label, v1, v2, ...)], series=[(이름, 색), ...].

    ★왜 막대가 아니라 선인가 — 추이 차트가 조회 기간과 무관하게 최근 1년이 되면서
      '일' 보기는 점이 341개다. 막대로 그리면 서로 붙어 형태도 라벨도 안 읽힌다.
      선은 점이 많을수록 오히려 흐름이 또렷해진다. '월'(12개)은 막대가 낫다.
    누적(스택)이라 값은 아래에서부터 쌓는다 — 맨 위 선이 합계다."""
    n = len(rows)
    W, H, PAD = 1000.0, 180.0, 6.0            # viewBox 좌표(실제 폭은 CSS 가 100%)
    tots = [sum(r[1:]) for r in rows]
    mx = max(tots) or 1
    x = (lambda i: (W * i / (n - 1)) if n > 1 else W / 2)
    y = (lambda v: PAD + (H - PAD) * (1 - v / mx))

    body, base = "", [0.0] * n
    for si, (name, color) in enumerate(series):
        top = [base[i] + rows[i][si + 1] for i in range(n)]
        up = " ".join(f"{x(i):.2f},{y(top[i]):.2f}" for i in range(n))
        dn = " ".join(f"{x(i):.2f},{y(base[i]):.2f}" for i in range(n - 1, -1, -1))
        # ★vector-effect — viewBox 를 preserveAspectRatio="none" 로 늘리면 선 두께가
        #   가로·세로 배율만큼 달라져 들쭉날쭉해 보인다. 이걸 주면 화면 기준 2px 로 고정된다.
        body += (f'<polygon points="{up} {dn}" fill="{color}" fill-opacity=".22"/>'
                 f'<polyline points="{up}" fill="none" stroke="{color}" stroke-width="2" '
                 f'vector-effect="non-scaling-stroke" stroke-linejoin="round" '
                 f'stroke-linecap="round"/>')
        base = top

    # 가로 격자 — 값 눈금 없이 선만 있으면 크기를 가늠할 수 없다. 최고점 라벨을 같이 준다.
    grid = "".join(f'<line x1="0" y1="{y(mx * f):.1f}" x2="{W}" y2="{y(mx * f):.1f}" '
                   f'stroke="var(--border)" stroke-width="1" stroke-dasharray="3 4" '
                   f'vector-effect="non-scaling-stroke"/>' for f in (0.5, 1.0))
    # hover 열 — 점마다 하나. 열 안에 툴팁을 넣어야 CSS :hover 로 띄울 수 있다.
    hits = ""
    for i, r in enumerate(rows):
        _parts = " · ".join(f"{nm} {fmt_krw(int(r[si + 1]))}"
                            for si, (nm, _) in enumerate(series) if r[si + 1])
        _cls = "hcol st" if i < n * 0.06 else ("hcol en" if i > n * 0.94 else "hcol")
        _bot = max(4.0, min(88.0, tots[i] / mx * 88.0))     # 툴팁을 점 근처에
        hits += (f'<div class="{_cls}"><div class="ltip" style="bottom:{_bot:.1f}%">'
                 f'{r[0]}<br>합계 {fmt_krw(int(tots[i]))}'
                 + (f'<br>{_parts}' if _parts else '') + '</div></div>')
    st.markdown(
        f'<div class="lchart">'
        f'<svg viewBox="0 0 {W:.0f} {H:.0f}" preserveAspectRatio="none">{grid}{body}</svg>'
        f'<div class="lhits">{hits}</div></div>'
        f'<div class="lxlab"><span>{rows[0][0]}</span>'
        f'<span>최고 {fmt_short(int(mx))}</span>'
        f'<span>{rows[-1][0]}</span></div>',
        unsafe_allow_html=True)


def css_trend(rows, gran):
    """시안과 동일한 CSS 스택 막대 추이. rows=[(label, 실결제, 쿠폰)].
    ★'주'·'일'은 점이 많아 막대가 안 읽힌다 → 면적 선그래프로 넘긴다."""
    if not rows:
        st.info("선택한 조건에 맞는 데이터가 없어요. 기간·필터를 바꿔 보세요.")
        return
    if gran != "월" and len(rows) > 20:
        st.markdown(
            '<div class="legend"><span><i class="dot" style="background:var(--brand-2)"></i>실결제</span>'
            '<span><i class="dot" style="background:var(--sky)"></i>쿠폰 할인</span></div>',
            unsafe_allow_html=True)
        _area_chart(rows, [("실결제", "var(--brand-2)"), ("쿠폰 할인", "var(--sky)")])
        return
    mx = max((r[1] + r[2]) for r in rows) or 1
    # ★막대가 많아지면(최근 1년 · 일 단위면 365개) 라벨이 겹쳐 아무것도 못 읽는다.
    #   x축 라벨은 26개쯤만 남기고, 막대 위 금액은 아예 뺀다(툴팁으로 본다).
    #   라벨 목표는 14개쯤 — '07/28주' 같은 라벨이 40px 남짓이라 그 이상은 겹친다.
    n = len(rows)
    step = 1 if n <= 20 else max(1, -(-n // 14))
    show_val = n <= 20
    gap = ("2px" if n > 120 else "4px") if n > 40 else (
        "6px" if gran == "일" else ("12px" if gran == "주" else "26px"))
    fs = ("9px" if n > 60 else "10px") if gran == "일" else "11px"
    cols = ""
    for _i, (label, real, cp) in enumerate(rows):
        tot = real + cp
        h = max(2, round(tot / mx * 100))
        cpp = round(cp / tot * 100) if tot else 0
        _tb = min(h, 80)   # 막대가 아주 높으면 툴팁이 카드 밖으로 나가지 않게 상한
        _tip = f'{label} · 실결제 {fmt_krw(real)}' + (f' · 쿠폰 {fmt_krw(cp)}' if cp else '')
        _xl = label if (_i % step == 0 or _i == n - 1) else ""
        cols += (f'<div class="col"><div class="vtip" style="bottom:{_tb}%">{_tip}</div>'
                 f'<div class="stack" style="height:{h}%">'
                 f'<div class="seg-cp" style="height:{cpp}%"></div>'
                 f'<div class="seg-real" style="height:{100 - cpp}%"></div></div>'
                 f'<div class="xlab" style="font-size:{fs}">{_xl}</div>'
                 + (f'<div class="vlab">{fmt_short(tot)}</div>' if show_val else '')
                 + '</div>')
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
# guide_content 에 'snapism' 가이드가 있는데 호출이 빠져 있어 메인 화면에만 설명서가
# 안 뜨고 있었다(포토이즘은 1_📸_포토이즘.py:1125 에서 부른다) — 2026-08-03 추가.
render_guide("snapism")

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


@st.cache_data(ttl=1800, show_spinner=False, max_entries=1)   # 파일 버전 키 → 최신 1개만
def _filter_options(ver):
    """필터 목록을 **데이터 버전당 한 번만** 만든다.

    ★전엔 모듈 최상위에서 `_uniq` 를 5번 불렀다 — rerun 마다 45만 행에
      `.astype(str).str.strip()` 을 다섯 번 돌린 셈이다. 포토이즘은 같은 걸
      `_sidebar_options` 로 캐시해 두었는데 여기만 빠져 있었다(2026-08-19).
    """
    return {c: _uniq(c) for c in ("국가", "매장 이름", "상품 카테고리",
                                  "프레임 이름", "카테고리")}


_opts_all = _filter_options(data_io.file_version(MASTER_FILE))
_country_opts = (sorted(set(_opts_all["국가"]) | set(KNOWN_COUNTRIES))
                 if "국가" in df_all.columns else [])
_store_all = _opts_all["매장 이름"]
_prod_opts = _opts_all["상품 카테고리"]
_ip_opts = _opts_all["프레임 이름"]
# ★'상품 카테고리'(미니스티커·포토카드 = 무엇을 샀나)와 '카테고리'(아티스트·캐릭터·
#   반팔입고나와 = 어떤 IP 기획인가)는 **다른 열**이다. 이름이 비슷해 자주 헷갈린다.
#   화면 라벨을 각각 '상품' / 'IP구분' 으로 갈라 놓은 이유다.
_cat_opts = _opts_all["카테고리"]


@st.cache_data(ttl=900, max_entries=1)   # 파일 버전 키 → 최신 1개만 유효
def _stores_by_country(v):               # ★밑줄 금지 — 위 _load_data 주석 참고
    """국가 → 매장 목록 (매장 필터를 선택 국가로 좁히기용)."""
    if "국가" not in df_all.columns or "매장 이름" not in df_all.columns:
        return {}
    out = {}
    for c, grp in df_all.dropna(subset=["국가"]).groupby("국가"):
        vals = sorted(str(v) for v in grp["매장 이름"].dropna().unique())
        out[str(c)] = [v for v in vals if v and v != "nan"]
    return out


_sbc = _stores_by_country(data_io.file_version(MASTER_FILE))


def cbfilter(col, label, options, key, parent_sig=None):
    """검색 + 체크박스 다중선택 필터. 선택 단일출처 = 각 체크박스 위젯(key=…__cb__옵션).
    목록을 항상 펼쳐 보여주고(상위 200개), 검색은 좁히는 용도. 선택 리스트 반환."""
    options = list(options)
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
    cap = "전체" if not sel else (str(sel[0]) if len(sel) == 1 else f"{len(sel)}개 선택")
    col.markdown(f'<div class="fbl">{label}</div>', unsafe_allow_html=True)
    with col.popover(cap, use_container_width=True):
        q = ""
        if len(options) > 6:
            q = st.text_input("검색", key=f"{key}__q", placeholder=f"🔍 {label} 검색",
                              label_visibility="collapsed").strip().lower()
        pool = [o for o in options if q in str(o).lower()] if q else list(options)
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
                st.session_state[pfx + str(o)] = True
        if _b[1].button("해제", key=f"{key}__clr", use_container_width=True, disabled=not sel):
            # ★목록에 지금 보이는 것만 지우면 안 된다 — 상위 필터에 따라 목록이
            #   좁아지므로, 빠진 사이 True 로 남은 항목이 안 없어진다.
            for _k in [k for k in st.session_state if str(k).startswith(pfx)]:
                st.session_state[_k] = False
        if not shown:
            st.caption("옵션이 없어요.")
        elif over:
            st.caption(f"상위 200개 표시 · 나머지 {over}개는 검색해서 찾아요.")
        for o in shown:
            st.checkbox(str(o), key=pfx + str(o))
    return _sel()


# [제거] 필터바 오른쪽 '⬇ 내려받기' — 뺐다(요청, 2026-08-12).
#        '프레임(IP) 전체 순위' 카드 머리줄의 내려받기가 대신한다.
#        되살리려면 `git show a49d147` 의 data_export.py · _DL_RAW_COLS ·
#        _dl_control 을 되돌리고 필터바 컬럼을 한 칸 늘리면 된다.


default_start = max(last_date - timedelta(days=29), first_date)


@st.fragment
def _filterbar():
    with st.container(border=True, key="scard-filter"):
        # 필터(5개)는 폭을 넉넉히 채우고 오른쪽 스페이서는 작게(포토이즘 톤)
        _fb = st.columns([1.0, 0.9, 0.9, 0.9, 0.95, 0.9, 0.5, 1.05], gap="small")
        with _fb[0]:
            st.markdown('<div class="fbl">기간</div>', unsafe_allow_html=True)
            st.date_input("기간", value=[default_start, last_date],
                          min_value=first_date, max_value=last_date,
                          key="f_date", label_visibility="collapsed")
        cbfilter(_fb[1], "국가", _country_opts, "f_country")
        _dc = [c for c in _country_opts if st.session_state.get(f"f_country__cb__{c}", False)]
        _std = (sorted(set().union(*[set(_sbc.get(c, [])) for c in _dc])) if _dc else _store_all)
        cbfilter(_fb[2], "매장", _std, "f_store", parent_sig=tuple(_dc))
        cbfilter(_fb[3], "상품", _prod_opts, "f_prod")
        # 도넛은 아티스트·캐릭터·기타 3분류로 두되, '기타' 안을 파고들 수 있게 필터를 준다
        # (후드입고나와 ₩4,747만 같은 기획전이 기타에 묻혀 개별 성과가 안 보였다).
        cbfilter(_fb[4], "IP구분", _cat_opts, "f_cat")
        cbfilter(_fb[5], "IP", _ip_opts, "f_ip")
        with _fb[6]:
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
sel_cat = [o for o in _cat_opts if st.session_state.get(f"f_cat__cb__{o}", False)]
sel_ip = [o for o in _ip_opts if st.session_state.get(f"f_ip__cb__{o}", False)]

_cfg = load_config()

# ★매장별 탭 전용 필터를 뺐다(2026-08-18) — 위젯이 사라져도 세션 값은 남는다.
#   나중에 되살리면 옛 선택이 같이 되살아나므로 여기서 지운다.
for _k in ("sn_st_nat", "sn_st_prd"):
    st.session_state.pop(_k, None)

# ── 필터 적용 ──
# ★이 .copy() 는 낭비가 아니다. df_all 은 cache_resource 로 전 사용자가 공유하는
#   객체라, 이 한 줄이 없으면 아래 가공이 남의 화면까지 오염시킨다. 지우지 말 것.
df = df_all.copy()
# ★날짜 외 필터를 먼저 걸어 scope 를 만든다(포토이즘 views/1 과 같은 구조).
#   '조회기간과 무관한 26년 누적' 같은 숫자를 뽑으려면 날짜만 안 걸린 프레임이 필요하다.
#   필터는 전부 행 마스크라 순서를 바꿔도 결과는 같다.
if sel_country and "국가" in df.columns:
    df = df[df["국가"].isin(sel_country)]
if sel_store:
    df = df[df["매장 이름"].isin(sel_store)]
if sel_prod:
    df = df[df["상품 카테고리"].isin(sel_prod)]
if sel_cat and "카테고리" in df.columns:
    df = df[df["카테고리"].astype(str).str.strip().isin(sel_cat)]
if sel_ip:
    df = df[df["프레임 이름"].isin(sel_ip)]
scope = df                      # 날짜 외 모든 필터
if len(date_range) == 2:
    df = df[(df["날짜"] >= date_range[0]) & (df["날짜"] <= date_range[1])]

sales = paid_sales(df)          # 실결제(카드·현금) 거래 — KPI '실결제' 카드 전용
coupons = coupon_txns(df)       # 전액 쿠폰 결제 거래
cpn_all = pd.concat([coupons, sales[sales["쿠폰 할인 금액"] > 0]])
rev = revenue_txns(df)          # ★모든 카드의 공통 기준 (정산금액 = 실결제 + 쿠폰)


# ── 타이틀 판매기간·상태 (프레임 순위표에 표시) ──────────────
# 매출이 빠졌을 때 '끝나서'인지 '안 끝났는데'인지 가르려고 Jira 종료일을 함께 본다.
# ★ 기간으로 자르지 않은 df_all 을 넘긴다 — 기간으로 자르면 첫 거래일이
#   전부 기간 시작일이 돼서 죄다 '신규'로 나온다.
# ★인자가 전부 밑줄이라 **캐시 키가 비어 있다** — 기간·국가·매장을 바꿔도 첫 결과가
#   그대로 재사용된다(max_entries=16 도 무의미). 지금 화면이 읽는 값은 지라의
#   오픈일·종료일뿐이고 그건 필터와 무관해서 증상이 안 보이지만, 반환 dict 의
#   첫거래일·마지막거래일을 쓰기 시작하면 그 순간 남의 필터 결과가 섞인다.
#   ⚠️밑줄을 떼면 필터 조합마다 재계산이라 비용이 크다(5.75MB 지라 캐시 파싱 +
#     41만 행 to_datetime). 로딩 개선 작업에서 같이 판단할 것 — 2026-08-03.
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
_period_days = ((date_range[1] - date_range[0]).days + 1) if len(date_range) == 2 else "-"
_dr = (f"{date_range[0]} ~ {date_range[1]}" if len(date_range) == 2 else "전체")
rev_real = int(sales["KRW환산금액"].sum())          # 실결제(카드·현금, 쿠폰·취소 제외)
cpn_krw = int(cpn_all["쿠폰KRW"].sum())              # 쿠폰 할인 금액(회사 정산분)
cpn_cnt = int(len(cpn_all))
cancel_krw = int(df[df["취소 여부"]]["KRW환산금액"].sum())
rev_total = rev_real + cpn_krw                       # 스내피즘-1: 조회기간 매출 = 실결제 + 쿠폰 합산

# 포5: 포토이즘과 카드 구성을 맞춘다 — 합계 / 실결제 / 쿠폰 (+ 취소는 스내피즘만).
#      포토이즘엔 취소 데이터가 아예 없어서(리포트 미포함) 4번째 카드는 여기에만 있다.
st.markdown(
    '<div class="kpis k4">'
    f'<div class="kpi hero"><div class="l">조회기간 매출 (합계)</div>'
    f'<div class="v num">{fmt_krw(rev_total)}</div>'
    f'<div class="d">{_dr} · {_period_days}일 · {len(rev):,}건</div></div>'
    f'<div class="kpi"><div class="l">실결제 매출 (카드·현금)</div>'
    f'<div class="v num">{fmt_krw(rev_real)}</div><div class="d">쿠폰 제외분</div></div>'
    f'<div class="kpi"><div class="l">쿠폰 매출 (정산분)</div>'
    f'<div class="v num">{fmt_krw(cpn_krw)}</div><div class="d">{cpn_cnt:,}건 · 위 합계에 포함</div></div>'
    f'<div class="kpi"><div class="l">취소 매출</div>'
    f'<div class="v num">{fmt_krw(cancel_krw)}</div><div class="d">환불·취소분 (합계 제외)</div></div>'
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
**조회기간 매출 · 쿠폰 매출 · 취소 매출 — 데이터 흐름 · 계산 · 검증 (상세)**

**① 데이터를 어떻게 가져오나 (로딩·검증 경로)**
- 원본 = 스내피즘 어드민(CMS)의 매장 거래 상세. 수집기가 **매일 오전 9시 자동 수집** → `data/master.parquet`(거래 1건 = 1행)에 누적해요.
  - 누적은 **upsert**(같은 거래 재수집 시 덮어쓰기) + **취소 반영**(취소된 거래는 취소 상태로 갱신) + mtime 정렬 롤링이라, 같은 날 여러 번 돌려도 중복이 안 쌓여요.
- 대시보드는 CMS 를 실시간 조회하지 **않고** 이 parquet 을 읽어요(부하·속도). **15분 캐시**(`ttl=900`) + 파일이 바뀌면 캐시 자동 무효화.
- 필터바(기간·국가·매장·상품·IP)로 **먼저 거른 거래 집합** 위에서 아래 값을 계산해요. 미선택 = 전체.

**② 통화 환산 (환율)**
- 해외 매장은 현지 통화라 나라 비교를 위해 전부 원화로 환산해요. `KRW환산금액 = 최종 결제 금액 × 환율`.
- 환율표는 `config.json`(실시간 갱신). 사이드바 **'실시간 환율'** 에서 현재 적용 환율을 볼 수 있어요.

**③ 각 값 계산식**
- **정산금액** = `KRW환산금액`(실결제) + `쿠폰KRW`(= `쿠폰 할인 금액 × 환율`). **이 화면 모든 매출의 기준**이에요.
  - 쿠폰은 손님이 할인받은 만큼 **회사엔 정산으로 들어오는 돈**이라 매출에 더해요.
- **▶ 조회기간 매출(합계)** = *취소 아님* **AND** *(실결제>0 **또는** 쿠폰>0)* 인 거래의 `정산금액` 합.
- **실결제** = 그중 카드·현금 실입금분(`KRW환산금액`)만. **쿠폰 매출** = 그중 `쿠폰KRW`만. 둘은 합계의 **구성 내역**이에요.
- **취소 매출** = `취소 여부 = True` 거래의 `KRW환산금액` 합. **합계엔 포함하지 않아요**(환불분, 규모 참고용).

**④ 검증 — 숫자가 맞는지 확인하는 법**
- `헤드라인(합계) − 쿠폰 매출 = 실결제` 가 항상 성립해요.
- 아래 매출 차트·표(국가별·카테고리·매장별)는 **전부 같은 `정산금액` 기준**이에요 → 국가/카테고리/매장으로 쪼갠 합을 다 더하면 **헤드라인과 정확히 일치**해야 정상.
  - *(2026-07-28 이전에는 카드만 실결제 기준이라 헤드라인과 약 10% 어긋났고, **대만·말레이시아·홍콩·태국처럼 전액 쿠폰으로 결제되는 나라가 0원으로 사라졌어요.** 지금은 같은 잣대라 그 나라들도 순위·비중에 정상으로 들어와요.)*
- 취소분은 합계·차트 **어디에도 안 더해져요**(전부 제외). 취소 카드는 규모 참고용.
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

# 세그먼트 컨트롤을 기존 언더라인 탭과 같은 모양으로(포토이즘과 동일).
# ★선택 상태는 aria-checked 가 아니라 kind="segmented_controlActive" 이고,
#   묶음은 stButtonGroup 이다. 일반 규칙에 요소선택자 `button` 이 있어 더 세므로
#   선택 규칙에도 `button` 을 붙여야 밑줄이 먹는다.
_LAZYTAB_CSS = """
<style>
.st-key-sn-maintab{ position:sticky; top:0; z-index:50; background:var(--bg);
  padding-top:8px; margin-bottom:2px;
  box-shadow:0 6px 10px -7px rgba(20,28,45,.18); }
.st-key-sn-maintab [data-testid="stButtonGroup"] > div{
  gap:2px !important; border-bottom:1px solid var(--border) !important;
  border-radius:0 !important; background:transparent !important; }
.st-key-sn-maintab [data-testid="stButtonGroup"] button{
  background:transparent !important; border:none !important;
  border-radius:0 !important; padding:9px 15px !important;
  min-height:0 !important; box-shadow:none !important;
  border-bottom:2.5px solid transparent !important; }
.st-key-sn-maintab [data-testid="stButtonGroup"] button p{
  font-size:14px !important; font-weight:700 !important; color:var(--text-2) !important; }
.st-key-sn-maintab button[data-testid="stBaseButton-segmented_controlActive"]{
  border-bottom-color:var(--brand) !important; }
.st-key-sn-maintab button[data-testid="stBaseButton-segmented_controlActive"] p{
  color:var(--brand) !important; }
.st-key-sn-maintab [data-testid="stButtonGroup"] button:first-child{
  background:var(--brand-soft) !important; border-radius:9px 9px 0 0 !important; }
.st-key-sn-maintab [data-testid="stButtonGroup"] button:first-child p{
  color:var(--brand) !important; }
.st-key-sn-maintab [data-testid="stButtonGroup"] button > div{
  display:flex !important; flex-direction:row !important;
  align-items:center !important; gap:6px !important; }
</style>
"""
# 포7: 런 비교를 사이드바에서 빼고 대시보드 탭으로. 단, st.tabs 는 안 열어도 매 rerun
#      마다 모든 탭을 실행해 무거워지므로(런 빌드), 탭엔 무거운 연산 대신 별도 페이지 링크만 둔다.
_tab_labels = ["📊 매출 한눈에", "🧩 상품 카테고리 분석", "🌏 국가별 분석", "🏬 매장별 분석", "🆚 런 비교"]
if SHOW_TAB_ETC:
    _tab_labels.append("⏰ 시간대 · 데이터")
# ★★st.tabs 는 **안 열린 탭의 본문도 매 rerun 전부 실행한다**(화면에서만 숨김).
#   그래서 고른 탭만 그린다 — 모양은 CSS 로 기존 언더라인 탭과 같게 맞췄다.
#   자세한 배경과 함정은 포토이즘 쪽 같은 자리 주석에 적어 뒀다.
# ★안 그려진 탭의 위젯은 스트림릿이 세션에서 지운다 → 탭을 왕복하면 선택이
#   초기화되므로 값을 한 번 다시 써서 살린다. **버튼 키는 절대 넣지 말 것**
#   (st.button / st.download_button 은 session_state 대입이 예외다).
for _k in ("sn_trend", "home_store_country", "cat_frame_tog",
           "prod_rank_pick", "prod_rank_lvl", "nat_frame_sel"):
    if _k in st.session_state:
        st.session_state[_k] = st.session_state[_k]

st.markdown(_LAZYTAB_CSS, unsafe_allow_html=True)
with st.container(key="sn-maintab"):
    _TABSEL = st.segmented_control("보기", _tab_labels, default=_tab_labels[0],
                                   key="sn_maintab", label_visibility="collapsed")
# ★`_sel` 로 두면 안 된다 — 국가별 탭 안에 같은 이름의 selectbox 가 있어 덮인다.
_TABSEL = _TABSEL or _tab_labels[0]

# ════════════ 탭 5: 런 비교 (별도 페이지 링크 — 성능 위해 탭엔 링크만) ════════════
if _TABSEL == "🆚 런 비교":
    with card("🆚 타이틀 런 비교"):
        st.markdown(
            '<div style="padding:4px 2px 14px;color:var(--text-2);font-size:13.5px;line-height:1.75">'
            '같은 타이틀의 <b style="color:var(--text)">회차(런)별 성과</b>를 나란히 비교해요 — '
            '예: <b>25년 QWER vs 26년 QWER</b>. 런마다 기간이 달라 <b>일평균</b> 기준으로 봐요.<br>'
            '연산이 무거워 대시보드가 느려지지 않도록 <b>전용 페이지</b>로 열려요.</div>',
            unsafe_allow_html=True)
        # ★st.page_link 를 직접 부르면 안 된다 — runs 권한이 없는 계정에서
        #   StreamlitPageNotFoundError 로 이 화면 전체가 죽는다(auth.safe_page_link 주석 참고).
        auth.safe_page_link("runs", "런 비교 페이지 열기", icon="🆚",
                            denied="이 기능은 권한이 필요해요. 필요하면 관리자에게 요청해 주세요.")

# ════════════ 탭 1: 매출 한눈에 ════════════
if _TABSEL == "📊 매출 한눈에":
    sec("1", "매출 동향",
        "잘 가고 있나? — <b>조회 기간과 무관하게 항상 최근 1년</b>이에요 "
        "(국가·매장·카테고리 필터는 그대로 적용돼요)")
    with card():
        # @st.fragment — 기간(월·주·일) 토글을 눌러도 이 조각만 다시 그린다.
        # 없으면 전체 재실행 → st.tabs(1.45)가 선택을 못 기억해 첫 탭으로 튕긴다.
        @st.fragment
        def _trend():  # 프리셋 토글 → 매출 추이 차트
            # ★이 차트만 **상단 조회 기간을 안 따른다 — 항상 최근 1년**이다(2026-08-07).
            #   흐름은 길게 봐야 읽히는데, 기간을 좁히면 막대 서너 개만 남아 추이가 안 보였다.
            #   국가·매장·카테고리 필터는 그대로 적용된다(scope = 날짜만 빠진 프레임).
            _t_end = last_date
            _t_start = (pd.Timestamp(_t_end).normalize()
                        - pd.DateOffset(months=11)).replace(day=1).date()
            _tw = scope[(scope["날짜"] >= _t_start) & (scope["날짜"] <= _t_end)]
            _r = revenue_txns(_tw)          # 정산금액 = 실결제 + 쿠폰 (다른 카드와 같은 기준)
            if _r.empty:
                st.info("선택한 조건에 맞는 데이터가 없어요. 필터를 바꿔 보세요.")
                return
            _r = _r.assign(_d=pd.to_datetime(_r["날짜"]))
            _g = _r.groupby("_d").agg(
                total=("정산금액", "sum"),
                실결제=("KRW환산금액", "sum"),
                쿠폰=("쿠폰KRW", "sum"),
                한국=("정산금액", lambda s: 0)).sort_index()
            # 한국분은 따로 — 한국이 88%라 '전체' 흐름이 사실상 한국 흐름이다.
            _kr = (_r[_r["국가"] == "대한민국"].groupby("_d")["정산금액"].sum()
                   if "국가" in _r.columns else pd.Series(dtype=float))
            _g["한국"] = _kr.reindex(_g.index).fillna(0)
            # 빈 날을 0으로 채운다 — 안 채우면 이동평균·주 집계가 날짜를 건너뛴다.
            _g = _g.asfreq("D").fillna(0) if len(_g) > 1 else _g
            trend_chart.render(st, _g, key="sn_trend", color="#4f46e5",
                               parts_cols=["실결제", "쿠폰"], kr_col="한국")
            helpbox("""
    **매출 추이**
    - ★**이 차트만 상단 조회 기간을 안 따라요 — 항상 최근 1년이에요.** 흐름은 길게 봐야 읽히는데 기간을 좁히면 막대가 서너 개만 남아서요. **국가·매장·카테고리 필터는 그대로 적용돼요.**
    - **보기 3가지** — `12개월·월`(막대 12개) · `12개월·주`(선 48점) · `최근 90일·일`(선 90점 + 7일 이동평균). 뭘 골라도 점이 12~90개예요.
      - ★**12개월을 일 단위로는 안 그려요.** 점이 341개라 화면 폭(점당 3px)보다 많아 읽을 수가 없고, 주말이 평일의 1.5배라 요일 흔들림이 추세보다 커서 오히려 방해가 돼요.
      - `주` 보기는 **양 끝의 잘린 주를 빼요.** 안 빼면 실제로 없는 U자 모양이 항상 생겨요.
      - `월` 보기의 마지막 달은 **사선**이에요 — 아직 진행 중인 부분 집계라 '급락'으로 오해하기 쉬워서요.
    - **선은 하나(정산금액 = 실결제 + 쿠폰)**예요. 예전엔 실결제 위에 쿠폰을 쌓았는데, 그러면 제일 또렷한 위쪽 선이 '정가 총액'이 돼서 정작 봐야 할 값이 가려졌어요. 실결제·쿠폰 내역은 **툴팁에 숫자로** 나와요.
    - **위쪽 요약 줄**(최근 4주 · 직전 4주 대비)은 그래프를 안 봐도 답이 되게 항상 띄워요. 터치 기기에선 툴팁을 못 띄우거든요. 4주 이동합이라 '이번 달이 아직 안 끝나서 낮아 보이는' 문제도 안 생겨요.
    - **한국 제외** 토글 — 한국이 88%라 '전체'가 사실상 한국이에요. 해외만 보려면 켜세요.
    - ※ 공통 기준(원본·환율·정산금액 정의)은 상단 'KPI 카드' 설명 참고.
    """)

        _trend()

    sec("2", "무엇이 매출을 만드나",
        f"비중 — 어떤 상품·종류가 매출을 끄나 · "
        f"<b>조회 기간 {_dr}</b> 기준이에요")
    _c1, _c2 = st.columns(2)
    with _c1:
        with card("🧩 상품 카테고리 비중"):
            pc = (rev.groupby("상품 카테고리")["정산금액"].sum().rename("매출")
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
- 매출 거래를 `상품 카테고리`로 묶어 `정산금액`(실결제+쿠폰) 합 → 비중(도넛).
- 요약 화면이라 **매출 상위 3종 + '기타 N종' 묶음**만 표시. 전체는 '상품 카테고리 분석' 탭.
""")
    with _c2:
        with card("🎨 아티스트/캐릭터 비중"):
            _s = rev.assign(_c=cat3(rev["카테고리"]))
            ac_full = (_s.groupby("_c")["정산금액"].sum().rename("매출").reset_index()
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
            # ★'기타'가 뭔지 안 보이면 찾을 수가 없다. 기간 한정 기획전이 여기 묻혀 있다
            #   (후드입고나와 ₩4,747만 등). 접어서 내역을 보여주고 필터로 안내한다.
            _etc = rev[cat3(rev["카테고리"]) == "기타"]
            if not _etc.empty and _etc["정산금액"].sum() > 0:
                # ★'카테고리'는 nullable string 이라 결측이 <NA> 다. astype(str) 하면
                #   'nan' 이 아니라 '<NA>' 가 나와서, 그것만 거르면 라벨이 빈칸으로 보인다.
                _kk = _etc["카테고리"].fillna("").astype(str).str.strip()
                _kk = _kk.mask(_kk.isin(["", "nan", "None", "<NA>"]), "(미지정)")
                _eg = (_etc.assign(_k=_kk)
                       .groupby("_k")["정산금액"].sum().sort_values(ascending=False))
                with st.expander(f"기타 {len(_eg)}종 열어보기 · {fmt_krw(int(_eg.sum()))}"):
                    for _k, _v in _eg.items():
                        st.markdown(
                            f'<div style="display:flex;justify-content:space-between;'
                            f'font-size:12.5px;padding:3px 0;border-bottom:1px solid var(--surface-3)">'
                            f'<span style="color:var(--text-2);font-weight:600">{_k}</span>'
                            f'<b style="color:var(--text)">{fmt_krw(int(_v))}</b></div>',
                            unsafe_allow_html=True)
                    st.caption("위 필터바의 **IP구분**에서 골라 보면 그 기획만 전 화면에 적용돼요.")
            helpbox("""
**아티스트/캐릭터 비중**
- 거래의 `카테고리` 값을 `cat3()`으로 **아티스트 / 캐릭터 / 기타** 3분류로 정규화한 뒤 `정산금액` 합.
- 도넛은 **아티스트·캐릭터 2조각만** 그리고, '기타'는 조각에서 빼고 캡션에 금액만 표기.
- '기타'는 기간 한정 기획전(반팔입고나와·후드입고나와 등)이라 종류가 계속 늘어요.
  개별 성과는 아래 **'기타 N종 열어보기'** 또는 필터바 **IP구분**에서 봐요.
""")

    with card("🖼 카테고리별 TOP 프레임(IP)"):
        _fsrc = rev[rev["프레임 이름"].astype(str).str.strip().replace("nan", "").ne("")]
        fr = _fsrc.groupby("프레임 이름")["정산금액"].sum().rename("매출").reset_index()
        fr = fr[fr["매출"] > 0]
        if not fr.empty:
            hbar_list(fr, "프레임 이름", top=5)
        else:
            st.info("프레임 데이터가 없어요.")
        helpbox("""
**카테고리별 TOP 프레임(IP)**
- 매출 거래 중 `프레임 이름`이 비어있지 않은 것만 대상으로 `정산금액` 합 → 상위 5개.
- '프레임 이름' = 사진 프레임(=IP) 식별자.
""")

    sec("3", "어디서 파나",
        f"지역 — 국가·매장별 매출(원화) · <b>조회 기간 {_dr}</b> 기준이에요")
    _n1, _n2 = st.columns(2)
    with _n1:
        with card("🌏 국가별 매출 TOP 6"):
            # 정산금액(실결제+쿠폰) 기준 — 전액 쿠폰 국가(대만 등)도 같은 순위에 들어온다.
            #   예전엔 실결제만 세서 대만이 0원으로 빠지고, 따로 '🎟 쿠폰으로만' 스트립을
            #   붙여 보완했는데 이제 필요 없다.
            nat6 = (rev.groupby("국가")["정산금액"].sum().rename("매출").reset_index()
                    ) if "국가" in rev.columns else pd.DataFrame()
            if not nat6.empty and nat6["매출"].sum() > 0:
                hbar_list(nat6, "국가", top=6)
            else:
                st.info("데이터가 없어요.")
            helpbox("""
**국가별 매출 TOP 6**
- 매출 거래를 `국가`로 묶어 `정산금액`(실결제+쿠폰, 원화) 합 → 상위 6개국. 나라 비교는 항상 원화 기준.
- 대만·말레이시아·홍콩·태국처럼 **전액 쿠폰 결제인 국가도 같은 기준으로** 순위에 들어와요.
""")
    with _n2:
        with card("🏬 국가별 매출 TOP 5 매장", key="scard-hstore"):
            # @st.fragment — 안의 위젯을 조작해도 이 조각만 다시 그린다.
            # 없으면 전체 재실행 → st.tabs(1.45)가 선택을 못 기억해 첫 탭으로 튕긴다.
            @st.fragment
            def _home_store():  # 국가 선택 → TOP5 매장
                _opts = (rev.groupby("국가")["정산금액"].sum().sort_values(ascending=False).index.tolist()
                         if "국가" in rev.columns else [])
                if _opts:
                    _pick = st.selectbox("국가", _opts, key="home_store_country", label_visibility="collapsed")
                    _ss = (rev[rev["국가"] == _pick].groupby("매장 이름")
                           .agg(매출=("정산금액", "sum"), 건수=("정산금액", "count"))
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
- 위 셀렉트박스에서 고른 국가의 매출 거래를 `매장 이름`으로 묶어 `정산금액`(실결제+쿠폰) 합·건수 → 상위 5개 매장.
- 국가 목록도 정산금액 순이라 전액 쿠폰 국가(대만 등)도 고를 수 있어요.
""")

            # ★이 호출이 위 helpbox 문자열 안에 딸려 들어가 있었다(닫는 따옴표 위치 실수).
            #   함수는 정의만 되고 실행이 안 돼 카드가 통째로 비어 있었다.
            _home_store()
    st.caption("※ 여긴 요약(TOP)이에요. 전체 순위는 '상품 카테고리 분석'·'매장별 분석' 탭에서 봐요.")

# ════════════ 탭 2: 상품 카테고리 분석 (상세, 전체) ════════════
if _TABSEL == "🧩 상품 카테고리 분석":
    with card("🎨 아티스트/캐릭터 비중 · 🖼 프레임(IP) 전체 순위"):
        _s = rev.assign(_c=cat3(rev["카테고리"]))
        # 시안: 도넛(아티스트/캐릭터) 상단 전체폭
        ac = _s.groupby("_c")["정산금액"].sum().rename("매출").reset_index()
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
            # ★내려받기 칸은 **자리만 먼저 잡고 표를 만든 뒤에 채운다.** 컬럼 객체는
            #   컨테이너라 순서를 건너뛰어 나중에 써 넣을 수 있다 — 버튼이 여기서
            #   눌리는데 그 시점에 fr_all 이 없으면 빈 파일이 나간다(같은 함정을
            #   필터바·구좌별 상세에서 한 번씩 밟았다).
            # 마지막 칸이 엑셀 다운로드 — 버튼이 크다는 얘기가 있어 폭을 줄였다(1.6 → 1.25)
            _hh, _tt, _dlc = st.columns([3.3, 5.65, 1.05], vertical_alignment="center")
            with _hh:
                st.markdown('<div class="ct" style="margin:0;transform:translateY(-8px)">'
                            '🖼 프레임(IP) 전체 순위</div>', unsafe_allow_html=True)
            with _tt:
                _tog = st.segmented_control("구분", ["전체", "아티스트", "캐릭터"], default="전체",
                                            key="cat_frame_tog", label_visibility="collapsed") or "전체"
            _fs = _s if _tog == "전체" else _s[_s["_c"] == _tog]
            fr_all = (_fs[_fs["프레임 이름"].astype(str).str.strip().replace("nan", "").ne("")]
                      .groupby("프레임 이름").agg(매출=("정산금액", "sum"), 건수=("정산금액", "count")).reset_index())
            fr_all = fr_all[fr_all["매출"] > 0]

            def _fr_export():
                """프레임(IP) 순위를 **한 줄 = IP × 국가** 로 편다(포토이즘 views/1 과 같은 형태).

                ★화면 표를 그대로 뱉으면 받아서 할 게 없다 — 담당자는 이 파일로
                  IP사 보고(어느 나라에서 얼마)·반응 판단(건당 평균·매장수)·
                  엑셀 피벗을 한다. 그래서 국가로 펴고 파생 열을 붙인다.
                  합계행은 안 섞는다(피벗이 깨진다) — 합계는 열로 반복해 넣는다.
                """
                _src = _fs[_fs["프레임 이름"].astype(str).str.strip().replace("nan", "").ne("")]
                if _src.empty:
                    return pd.DataFrame(columns=["구분", "이름", "국가", "매출", "건수"])
                _d = (_src.groupby(["_c", "프레임 이름", "국가"], observed=True)
                      .agg(매출=("정산금액", "sum"), 건수=("정산금액", "count"),
                           매장수=("매장 이름", "nunique"),
                           첫거래일=("날짜", "min"), 마지막거래일=("날짜", "max")).reset_index())
                _d = _d[_d["매출"] > 0].rename(columns={"_c": "구분", "프레임 이름": "이름"})
                if _d.empty:
                    return _d
                if _tstat:
                    def _per_of(n):
                        s = _tstat.get(n) or {}
                        o, e = _md(s.get("오픈일")), _md(s.get("종료일"))
                        return f'{o or "?"} ~ {e or "진행중"}' if s and (o or e) else ""
                    _d["판매기간"] = _d["이름"].map(_per_of)
                _d["매출"] = _d["매출"].round(0).astype("int64")
                _d["건수"] = _d["건수"].astype("int64")
                # ★(구분, 이름) 으로 묶고 observed=True — 이름만 쓰면 같은 이름이 두
                #   구분에 걸릴 때 합계가 섞이고, observed 없으면 카테고리형에서
                #   안 쓰는 조합까지 만들어 낸다.
                _g = _d.groupby(["구분", "이름"], observed=True)
                _t = _g["매출"].transform("sum")
                _d["IP 매출 합계"] = _t
                _d["국가 비중(%)"] = (_d["매출"] / _t.replace(0, 1) * 100).round(1)
                _d["건당 평균"] = (_d["매출"] / _d["건수"].replace(0, 1)).round(0).astype("int64")
                _d["판매 국가 수"] = _g["국가"].transform("nunique")
                # ★'IP 매출 합계' 는 **정렬에만 쓰고 열로는 안 낸다**(포토이즘과 같음).
                #   줄마다 같은 금액이 반복돼 눈에 거슬린다 — 크기는 '국가 비중(%)' 로 읽는다.
                _cs = [c for c in ["구분", "이름", "판매기간", "판매 국가 수",
                                   "국가", "매출", "국가 비중(%)", "건수", "건당 평균",
                                   "매장수", "첫거래일", "마지막거래일"] if c in _d.columns]
                _d = (_d.sort_values(["IP 매출 합계", "이름", "매출"],
                                     ascending=[False, True, False])[_cs]
                      .reset_index(drop=True))
                # 머리줄에 단위를 박는다 — 받은 파일만 보고도 원인지 건인지 알게.
                return _d.rename(columns={
                    "매출": "매출(원)",
                    "건당 평균": "건당 평균(원)", "건수": "건수(건)",
                    "매장수": "매장수(개)", "판매 국가 수": "판매 국가 수(개국)"})

            with _dlc, st.container(key="dlbtn"):
                _db, _dm = "sn_fr_dl_b", "sn_fr_dl_m"
                _sig = (_tog, str(date_range), tuple(sel_country), tuple(sel_store),
                        tuple(sel_prod), tuple(sel_cat), tuple(sel_ip))
                _mm = st.session_state.get(_dm)
                _pf = f"{date_range[0]}_{date_range[1]}" if len(date_range) == 2 else "전체기간"
                _HELP = (
                    "지금 이 표를 **엑셀 파일(.xlsx)** 로 받아요.\n\n"
                    "· 한 줄이 **IP × 국가** 라 국가별로 나눠 보거나 피벗을 바로 돌릴 수 있어요\n"
                    "· 매출·건수는 **쉼표가 찍힌 숫자**로 들어가요(합계·수식 그대로 돼요)\n"
                    "· 건당 평균 · 국가 비중 · 매장수 · 첫/마지막 거래일도 같이 들어가요\n"
                    "· 지금 화면의 **구분 · 기간 · 국가 · 매장 · 상품 · IP** 조건이 그대로 반영돼요")
                if st.session_state.get(_db) is not None and _mm and _mm[1] == _sig:
                    # ★받고 나면 '엑셀 다운로드' 로 되돌린다(포토이즘 views/1 과 같음).
                    #   들고 있던 바이트도 같이 버려 메모리를 돌려준다.
                    if auth.download_button(
                            f"⬇ 받기 · {_mm[0]:,}줄", st.session_state[_db],
                            f"스내피즘_프레임순위_{_pf}.xlsx",
                            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                            key="sn_fr_dl_get", use_container_width=True,
                            page="snapism", rows=_mm[0], help=_HELP):
                        st.session_state.pop(_db, None)
                        st.session_state.pop(_dm, None)
                        _frag_rerun()
                elif st.button("📗 엑셀 다운로드", key="sn_fr_dl_make",
                               use_container_width=True, disabled=not _CAN_DL,
                               help=_HELP if _CAN_DL else
                               "엑셀 다운로드는 팀장 권한이 있어야 해요."):
                    _d = _fr_export()
                    st.session_state[_db] = xlsx_export.to_xlsx(
                        _d, "프레임 순위", note=[
                            "스내피즘 · 프레임(IP) 전체 순위  |  조회기간 "
                            + (f"{date_range[0]} ~ {date_range[1]}" if len(date_range) == 2
                               else "전체")
                            + f"  |  구분 {_tog}"
                            + (f"  |  국가 {', '.join(sel_country)}" if sel_country else "")
                            + (f"  |  매장 {len(sel_store)}곳 선택" if sel_store else ""),
                            "금액 단위: 원(KRW) — 현지 통화 매출을 대시보드 환율표로 "
                            "원화 환산한 값이에요(정산서의 기준일 환율과 다를 수 있어요). "
                            "매출 = 정산금액(실결제 + 쿠폰) · 취소 제외.",
                        ])
                    st.session_state[_dm] = (len(_d), _sig)
                    _frag_rerun()

            # 스2: 상태값(신규/확인필요/종료 등) 필터·배지 제거. 판매기간은 지라 오픈~종료로만 표기.
            st.caption(f"프레임(IP) {len(fr_all):,}개 · TOP 10 + 나머지 접기")

            if not fr_all.empty:
                rank_table(fr_all, "프레임 이름", collapse_after=10, status_map=_tstat or None)
            else:
                st.info("데이터가 없어요.")
            helpbox("""
    **아티스트/캐릭터 비중 · 프레임(IP) 전체 순위**
    - 상단 도넛 = 탭1과 동일(아티스트·캐릭터 2조각, `cat3()` 분류).
    - 하단 표 = `전체 / 아티스트 / 캐릭터` 토글로 거른 뒤 `프레임 이름`별 `정산금액`(실결제+쿠폰) 합·건수. TOP 10 + 나머지 접기.

    **판매기간(오픈~종료)** — 지라 티켓의 **계획 오픈일(`startdate`) ~ 종료일(`duedate`)** 기준이에요.
    - 실제 거래일이 아니라 **지라에 등록된 실제 오픈·종료일**을 그대로 보여줘요.
    - 지라가 연결 안 된 타이틀은 `—` 로 두고 **추측하지 않아요**(매칭은 타이틀명 정규화 + `ip_aliases.json` 별칭 기준, 매출의 약 84% 연결).
    - (이전의 신규/확인필요/종료 등 **상태 배지·필터는 뺐어요.**)
    """)

        _frame_rank()

    with card("🧩 상품 카테고리 (비중 · 매출)"):
        pc = (rev.groupby("상품 카테고리").agg(매출=("정산금액", "sum"), 건수=("정산금액", "count"))
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
- 매출 거래를 `상품 카테고리`로 묶어 `정산금액`(실결제+쿠폰) 합·건수.
- 왼쪽 도넛 = 비중(상위 3 + 기타 묶음), 오른쪽 막대 = 카테고리별 매출액 전체.
""")

    @st.fragment
    def _prod_rank():
        with card("📦 카테고리별 상품 순위", key="scard-prodsel"):
            cats = [c for c in sorted(rev["상품 카테고리"].dropna().astype(str).unique().tolist())
                    if c and c != "nan"]
            if not cats:
                st.info("데이터가 없어요.")
                return
            _d = "미니스티커" if "미니스티커" in cats else cats[0]
            _c1, _c2 = st.columns([3, 2])
            pick = _c1.selectbox("카테고리", cats, index=cats.index(_d),
                                 key="prod_rank_pick", label_visibility="collapsed")
            # ★어느 타이틀에서 팔린 건지 안 보였다(2026-08-11). 상품 이름만으로는
            #   '센'·'마사토' 가 어느 IP 것인지 알 수 없다 → 타이틀(프레임 이름)을 앞에 붙인다.
            #   ※'테마' 는 거래 원장에 없다 — CMS 촬영수 리포트에만 있는 값이라
            #     타이틀 → 상품 2단이 데이터로 가능한 최대다.
            _lvl = _c2.segmented_control(
                "묶기", ["타이틀 · 상품", "타이틀"], default="타이틀 · 상품",
                key="prod_rank_lvl", label_visibility="collapsed") or "타이틀 · 상품"
            _src = rev[rev["상품 카테고리"] == pick]
            _fr = _src["프레임 이름"].astype(str).str.strip().replace("nan", "")
            if _lvl == "타이틀":
                pr = (_src.assign(_n=_fr.where(_fr.ne(""), "(타이틀 없음)"))
                      .groupby("_n").agg(매출=("정산금액", "sum"),
                                         건수=("정산금액", "count")).reset_index()
                      .rename(columns={"_n": "이름"}))
            else:
                _nm = _src["상품 이름"].astype(str).str.strip()
                pr = (_src.assign(_n=_fr.where(_fr.ne(""), "(타이틀 없음)") + " · " + _nm)
                      .groupby("_n").agg(매출=("정산금액", "sum"),
                                         건수=("정산금액", "count")).reset_index()
                      .rename(columns={"_n": "이름"}))
            pr = pr[pr["매출"] > 0]
            if pr.empty:
                st.info("이 카테고리에는 데이터가 없어요.")
            else:
                rank_table(pr, "이름", collapse_after=10)
            helpbox("""
**카테고리별 상품 순위**
- 위에서 고른 `상품 카테고리`에 속한 매출 거래를 `정산금액`(실결제+쿠폰) 합·건수로 묶어 순위. TOP 10 + 나머지 접기.
- **`타이틀 · 상품`**(기본) = `프레임 이름`(타이틀/IP) + `상품 이름`. 같은 이름의 멤버가 여러 IP에 있어서, 타이틀을 같이 봐야 어느 IP 것인지 알 수 있어요.
- **`타이틀`** = 타이틀 단위로만 합산. 어느 IP가 이 카테고리에서 잘 팔리는지 볼 때 써요.
- ※ **'테마'는 이 표에 없어요.** 테마는 CMS 촬영수 리포트에만 있는 값이라 거래 원장으로는 못 나눠요.
""")

    _prod_rank()

# ════════════ 탭 3: 국가별 분석 (상세, 전체) ════════════
if _TABSEL == "🌏 국가별 분석":
    if "국가" not in sales.columns or sales.empty:
        st.info("국가 데이터가 없어요.")
    else:
        # ★정산금액(실결제+쿠폰) 기준으로 통일. 예전엔 '매출'=실결제라 전액 쿠폰 국가(대만 등)가
        #   0원으로 잡혀 '쿠폰만' 배지·안내문으로 보완했는데, 이제 같은 잣대라 그 예외가 없다.
        nat = (rev.groupby(["국가", "결제 단위"])
               .agg(건수=("정산금액", "count"), 현지=("총원화금액", "sum"),
                    매출=("정산금액", "sum"), 쿠폰=("쿠폰KRW", "sum"))
               .reset_index())
        nat = nat[nat["매출"] > 0].copy().sort_values("매출", ascending=False)
        tot = nat["매출"].sum()
        mx = (nat["매출"] / tot).max() if tot else 1.0

        # 포5: 비중 도넛을 탭 맨 위로(포토이즘과 동일한 순서 — 전체 그림 먼저, 표는 그 다음).
        with card("🍩 국가별 매출 비중"):
            # 정산금액 비중 — 전액 쿠폰 국가(대만 등)도 포함. 예전엔 실결제 기준이라
            # 그 나라들을 통째로 빼고 그렸는데, 이제 같은 잣대라 뺄 이유가 없다.
            # ※ nat 은 국가+통화 단위라 한 나라가 여러 통화면 행이 나뉜다 → 국가로 다시 합친다.
            _natp = (nat.groupby("국가", as_index=False)["매출"].sum()
                     .sort_values("매출", ascending=False).reset_index(drop=True))
            _pie = _natp[["국가", "매출"]].copy()
            if len(_pie) > 7:
                _pie = pd.concat([_pie.head(7), pd.DataFrame(
                    [{"국가": f"기타 {len(_natp) - 7}개국", "매출": int(_natp.iloc[7:]["매출"].sum())}])],
                    ignore_index=True)
            _pie = _pie.sort_values("매출", ascending=False).reset_index(drop=True)
            css_donut(list(zip(_pie["국가"], _pie["매출"])), PAL, size=190, hole=62, legend_fs=14)
            if not cpn_all.empty:
                st.markdown(f'<div class="strip">🎟 이 중 쿠폰 정산분 '
                            f'<b>{fmt_krw(int(cpn_all["쿠폰KRW"].sum()))}</b> · {len(cpn_all):,}건</div>',
                            unsafe_allow_html=True)
            helpbox("""
**국가별 매출 비중 (도넛)**
- 아래 표의 국가별 `정산금액`(실결제+쿠폰)으로 비중 계산. 상위 7개국 + '기타 N개국' 묶음.
- 한 나라에 통화가 여러 개면 국가로 다시 합쳐서 한 조각으로 그려요.
- 하단 🎟 스트립 = 위 매출에 **포함된** 쿠폰 정산분(`쿠폰KRW`) 합·건수.
""")

        with card("🌏 국가별 매출"):
            grid = "grid-template-columns:1.6fr .6fr .7fr 1.2fr 1.2fr 1.2fr 1.2fr"
            html = (f'<div class="ntbl"><div class="ntr nth" style="{grid}">'
                    '<span>국가</span><span class="c">통화</span><span class="r">건수</span>'
                    '<span class="r">현지 매출</span><span class="r">매출(KRW)</span>'
                    '<span class="r">쿠폰 포함분</span><span>매출 비중</span></div>')
            for _, r in nat.iterrows():
                frac = (r["매출"] / tot) if tot else 0
                _cpn_cell = (f'<b style="color:var(--amber)">{fmt_krw(int(r["쿠폰"]))}</b>'
                             if r["쿠폰"] > 0 else '<span style="color:var(--text-3)">—</span>')
                html += (f'<div class="ntr" style="{grid}">'
                         f'<span class="nname">{flag_img(r["국가"])}{r["국가"]}</span>'
                         f'<span class="c"><span class="cur">{r["결제 단위"]}</span></span>'
                         f'<span class="r num">{int(r["건수"]):,}</span>'
                         f'<span class="r num">{fmt_orig(r["현지"], r["결제 단위"])}</span>'
                         f'<span class="r num">{fmt_krw(int(r["매출"]))}</span>'
                         f'<span class="r num">{_cpn_cell}</span>'
                         f'{pct_bar(frac, mx)}</div>')
            st.markdown(html + "</div>", unsafe_allow_html=True)
            st.caption("💡 **매출(KRW)** 은 실결제+쿠폰(정산금액)이에요. 대만·말레이시아·홍콩·태국처럼 "
                       "**전액 쿠폰으로 결제되는 나라**는 '쿠폰 포함분'이 매출과 같아요.")
            helpbox("""
**국가별 매출 표**
- 매출 거래(취소 아님 & 실결제>0 또는 쿠폰>0)를 `국가`·`결제 단위`(통화)로 묶음.
- **건수** = 거래 수 · **현지 매출** = `총원화금액`(= 최종 결제 금액 + 쿠폰 할인 금액, 현지통화 정가) 합.
- **매출(KRW)** = `정산금액`(= 실결제 + 쿠폰) 합 · **쿠폰 포함분** = 그중 `쿠폰KRW` 합 · **매출 비중** = 그 나라 매출 ÷ 전체 매출.
- ★대시보드 전 카드가 이 `정산금액` 하나로 통일돼 있어요. 전액 쿠폰 국가(대만·말레이시아·홍콩·태국)도 같은 잣대로 순위·비중에 들어와요.
""")

        # ── 키오스크 1대당 매출 ────────────────────────────────
        _dev = load_devices()
        if not _dev.empty and len(date_range) == 2:
            # 조회기간에 매출이 난 (국가, 매장) — '매출 발생 대수'의 근거.
            _sp = rev[["국가", "매장 이름"]].drop_duplicates()
            _sold = set(zip(_sp["국가"].astype(str).str.strip(),
                            _sp["매장 이름"].astype(str).str.strip()))
            _dd = device_days(_dev[~_dev["렌탈"]], date_range[0], date_range[1], sold=_sold)
            _pkd = (date_range[1] - date_range[0]).days + 1
            per = pd.DataFrame()
            if not _dd.empty:
                _dd["국가"] = _dd["국가코드"].map(CC_TO_NAT)
                # 정산금액(실결제+쿠폰) 기준 — 다른 카드와 동일(rev).
                _rev = (rev.groupby("국가")
                        .agg(매출=("정산금액", "sum"), 건수=("정산금액", "size"))
                        .reset_index())
                per = _dd.merge(_rev, on="국가", how="inner")
                # 스4: 분모를 '총 가동일'이 아니라 '대수'로. 조회기간이 30일이 아닐 때만
                #      30일로 환산(_pkd)해 '1대당 월매출' 라벨을 유지한다.
                # ★분모는 '가동 대수'가 아니라 **매출 발생 대수**다(2026-08-07 요청).
                #   계약상 가동중이어도 그 기간에 한 건도 안 판 장비가 분모에 남으면,
                #   장비를 많이 깔아둔 나라일수록 대당 매출이 실제보다 낮게 나온다.
                #   '실제로 돈을 번 장비 한 대가 얼마를 버는가'를 보는 지표로 통일한다.
                per = per[(per["매출대수"] > 0) & (per["매출"] > 0)].copy()
                per["대당월"] = (per["매출"] / per["매출대수"] / _pkd * 30).round(0).astype("int64")
                per["대당건"] = (per["건수"] / per["매출대수"] / _pkd * 30).round(1)
                per = per.sort_values("대당월", ascending=False)

            if not per.empty:
                with card("🎰 키오스크 1대당 매출 <span class='muted'>(팝업·렌탈 제외)</span>",
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
                    grid = ("grid-template-columns:1.3fr .62fr .78fr .85fr 1.15fr "
                            ".85fr 1.0fr")
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
                        _new, _end = int(r["신규"]), int(r["종료"])
                        _bits = []
                        if _new:
                            _bits.append(f'<span style="color:var(--green)">+{_new}</span>')
                        if _end:
                            _bits.append(f'<span style="color:var(--red)">-{_end}</span>')
                        _chg = " ".join(_bits) or '<span style="color:var(--text-3)">–</span>'
                        # '표본 적음' 배지 제거(요청). 정렬(기준 미달 국가는 아래쪽)은
                        # 그대로 둔다 — 몇 대뿐인 나라가 1위로 튀는 걸 막는 장치라서,
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
                                 f'{pct_bar(r["대당월"] / _mx if _mx else 0, 1.0)}</div>')
                    st.markdown(html + "</div>", unsafe_allow_html=True)

                    # 표 아래 '💡 총매출 1위 vs 1대당 1위' 안내와 긴 설명 캡션은 뺐다(요청).
                    # 포토이즘 쪽도 같이 뺐다 — 같은 내용은 helpbox(계산 방식 설명)에 남아 있다.

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
- **1대당 월매출 = (조회기간 매출 ÷ 매출 발생 대수) ÷ 조회일수 × 30**. **예상치가 아니에요.** 30일 조회면 ×30/30=×1 이라 그대로 한 달 실적, 아니면 30일치로 환산해요(7일만 보면 ×30/7). 월건수도 같은 식(매출 대신 건수).
  - **분자 = 조회기간 매출 = 실결제 + 쿠폰(정산금액)** · **분모 = 매출 발생 대수 = 그 기간에 실제로 판 키오스크 수**(팝업·렌탈 제외).
  - ★**가동 대수가 아니라 매출 발생 대수로 나눠요**(2026-08-07 변경). 계약상 가동중이어도 한 건도 안 판 키오스크까지 세면, 장비를 많이 깔아둔 나라일수록 대당 매출이 실제보다 낮게 나와요. '실제로 돈을 번 한 대가 얼마를 버는가'를 보는 값이에요.
  - ⚠️ 매출 발생 대수는 **매장 단위**로 세요. 거래 데이터에 장비 번호가 없어서, 한 매장에 2대가 있고 1대만 돌았어도 2대로 잡혀요.
  - 그래서 **짧은 기간을 보면 그 며칠의 편차(주말·이벤트)가 30배로 커져** 보여요. 최소 2~4주로 보는 걸 권해요.
  - ⚠️ 이번 기간에 **막 계약한(증설한) 나라**는 며칠만 돈 키오스크도 온전히 한 대로 세어져 대당 매출이 실제보다 **눌려(낮게)** 보일 수 있어요. 아래 '기간 내 변동'·'계약 이력'을 함께 보세요.
- **'○○ 대비'** = 이 표의 1위, 즉 **1대당 매출이 가장 높은 국가**를 100%로 둔 비율이에요. 헤더에 그 나라 이름이 그대로 나와요.
  - ★**총매출 1위와 다른 나라일 수 있어요.** 한국은 총매출은 1위지만 1대당으로는 아래쪽이라 100%가 아니에요.
  - ⚠️ **표본이 적은 국가**(매출 발생 대수가 기준 미만)는 표 아래쪽으로 내려요. 매장 한 곳 성적이 그대로 국가 대표값이 돼서 1위로 튀거든요. 기준은 **최대 보유국의 1%**(최소 3대)라 나라 규모가 커지면 같이 올라가요. 100%와 헤더 국가명도 기준을 넘긴 나라에서만 잡아요. (배지 표기는 뺐고, 몇 개국이 내려갔는지는 표 아래 캡션에 나와요.)
  - 위 '국가별 매출' 표의 **실결제 비중(전체 대비 점유율)과도 다른 값**이에요.
- 여기서 매출은 **실결제 + 쿠폰(정산금액)** 이에요. 실결제만 쓰면 전액 쿠폰으로 결제되는 국가(대만)가 1대당 0원이 돼요.
- **팝업·렌탈은 분자·분모 모두 제외**했어요. 며칠만 도는 행사 장비라 상시 매장과 섞으면 왜곡돼요.
- **기간 내 변동** `+N` = 신규 계약, `-N` = 실제 해지.
- ★**계약 종료일 ≠ 폐점**이에요. 가맹 계약이 대부분 1년이라 오늘도 89대가 종료일을 맞는데, 그건 갱신일이에요.
  그래서 운영 상태가 **'가맹 해지'인 것만** 종료(가동 대수에서 제외)로 봐요.
""")

        # 포5: 포토이즘의 '🏆 국가별 타이틀 TOP 10' 과 짝을 맞춘 카드.
        #      스내피즘엔 '타이틀(날짜+IP)' 개념이 없어 같은 자리를 **프레임(IP)** 로 채운다.
        @st.fragment
        def _nat_frame():
            with card("🏆 국가별 TOP 프레임(IP)", key="scard-natframe"):
                _fsrc = rev[rev["프레임 이름"].astype(str).str.strip().replace("nan", "").ne("")]
                if _fsrc.empty:
                    st.info("해당 조건에 맞는 프레임 데이터가 없어요.")
                    return
                _nc = [str(c) for c in _fsrc.groupby("국가")["정산금액"].sum()
                       .sort_values(ascending=False).index.tolist()]
                _sel = st.selectbox("국가", _nc, key="nat_frame_sel", label_visibility="collapsed")
                _cdf = (_fsrc[_fsrc["국가"] == _sel].groupby("프레임 이름")
                        .agg(매출=("정산금액", "sum"), 건수=("정산금액", "count")).reset_index())
                _cdf = _cdf[_cdf["매출"] > 0]
                st.markdown(
                    '<div style="font-size:13px;color:var(--text-2);margin:8px 0 16px;'
                    'display:flex;align-items:center;gap:2px">'
                    f'{flag_img(_sel, h=14)}<b style="color:var(--text)">{_sel}</b>'
                    '<span style="color:var(--text-3);margin:0 8px">·</span>'
                    f'총 매출 <b style="color:var(--text);margin-left:4px">{fmt_krw(int(_cdf["매출"].sum()))}</b>'
                    '<span style="color:var(--text-3);margin:0 8px">·</span>'
                    f'프레임 {len(_cdf):,}개</div>', unsafe_allow_html=True)
                if _cdf.empty:
                    st.info("이 국가의 프레임 데이터가 없어요.")
                else:
                    rank_table(_cdf, "프레임 이름", collapse_after=10)
                helpbox("""
**국가별 TOP 프레임(IP)**
- 선택 국가의 `프레임 이름`(=IP)별 `정산금액`(실결제+쿠폰) 합·건수 → 순위(TOP10 + 나머지 접기).
- 포토이즘의 '국가별 타이틀 TOP 10' 과 같은 자리예요. 스내피즘은 타이틀(날짜+IP) 개념이 없어 **프레임 단위**로 봐요.
""")

        _nat_frame()

        # ※ 예전 '키오스크당 매출(준비중)' 카드는 위 '키오스크 1대당 매출'로 대체됐다.
        #    대수 데이터(devices_snapism.parquet)가 붙어 실제 계산이 되므로 자리표시자는 제거.
        #    거기서 예고했던 '총매출 1위 ≠ 대당 효율 1위' 인사이트는 그 카드 안 스트립으로 옮겼다.

# ════════════ 탭 4: 매장별 분석 (상세, 전체) ════════════
# ════════════ 탭: 매장별 분석 ════════════
# ★전용 필터(국가·상품)를 뺐다(2026-08-18 요청) — **상단 필터바를 그대로 따른다.**
#   상단에 이미 국가·상품이 있어 같은 걸 두 겹으로 걸고 있었다. 두 겹이면 화면의
#   숫자가 어느 필터의 결과인지 헷갈리고, 상단을 풀어도 여기 선택이 남아 몰래
#   걸리는 사고가 난다. 포토이즘 매장별 탭도 같이 정리했다.
# ★'🧩 카테고리별 프레임(IP) TOP 5' 도 뺐다 — '상품 카테고리 분석' 탭의
#   **프레임(IP) 전체 순위**가 같은 것을 전부 보여준다. 요약본이 두 군데 있을 이유가 없다.
# ※위젯이 없어졌으니 @st.fragment 도 뗀다 — 격리할 조작이 더는 없다.
def _store_tab():
    with card("🏬 매장 전체 순위"):
        # 정산금액(실결제+쿠폰) 하나로 순위 — 전액 쿠폰 매장도 같은 막대에 들어온다.
        ss = (rev.groupby("매장 이름")
              .agg(매출=("정산금액", "sum"), 건수=("정산금액", "count"))
              .reset_index())
        ss = ss[ss["매출"] > 0].sort_values("매출", ascending=False)
        # ★이 탭의 축은 **매장**이다. 매출·건수는 맨 위 요약과 다른 탭에 이미 있다.
        statrow([("매장 수", f"{rev['매장 이름'].nunique():,}개"),
                 ("매출 발생 매장", f"{len(ss):,}개")])
        st.caption("상단 필터바(기간·국가·매장·상품·IP)를 그대로 따라요.")
        if ss.empty:
            st.info("해당 조건에 맞는 매장이 없어요. 위 필터바를 넓혀 보세요.")
        else:
            # 매장 전체 순위 = 전체 목록이라 비중을 켜도 분모가 맞다.
            hbar_list(ss, "매장 이름", collapse_after=10, show_pct=True)
        helpbox("""
**매장 전체 순위**
- **상단 필터바**(기간·국가·매장·상품·IP)로 거른 결과의 `매장 이름`별 `정산금액`(실결제+쿠폰) 합·건수 순위예요.
  (예전엔 이 탭에만 있는 국가·상품 필터를 한 겹 더 걸었는데, 상단 필터바와 겹쳐서 뺐어요.)
- **매장 수** = 그 조건에 나타난 매장 개수, **매출 발생 매장** = 그중 매출이 0보다 큰 곳이에요.
  둘이 다르면 그 차이만큼은 **기간 안에 거래가 없던 매장**이에요.
- 전액 쿠폰 결제 매장(대만 등)도 **같은 막대 순위**에 들어와요.
- 카테고리별 프레임(IP)은 **'🧩 상품 카테고리 분석' 탭**에서 전체를 봐요.
""")


if _TABSEL == "🏬 매장별 분석":
    _store_tab()

# ════════════ 탭 5: 시간대 · 데이터 ════════════ [보류: SHOW_TAB_ETC 로 부활]
if SHOW_TAB_ETC and _TABSEL == "⏰ 시간대 · 데이터":
    if True:                       # 들여쓰기 유지(예전 `with tab_etc:` 자리)
        with card("⏰ 시간대별 매출 분포"):
            _hv = (rev.assign(시간대=rev["결제일시"].dt.hour).groupby("시간대")["정산금액"].sum()
                   .reindex(range(24), fill_value=0))
            css_hours([int(v) for v in _hv.tolist()])
            st.caption("최고 시간대만 진하게 강조했어요.")
            helpbox("""
**시간대별 매출 분포**
- 매출 거래의 `결제일시`에서 **시(hour)** 만 뽑아 0~23시로 묶어 `정산금액`(실결제+쿠폰) 합(빈 시간대는 0).
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
            auth.download_button("CSV 다운로드",
                                 df[avail].to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig"),
                                 "snapism_filtered.csv", "text/csv",
                                 page="snapism", rows=len(df))
