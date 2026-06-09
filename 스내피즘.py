"""
CMS 매출 대시보드 — 진입/네비게이션 라우터
st.navigation 으로 사이드바 페이지 순서를 직접 제어한다.
(.bat 은 그대로 `streamlit run 스내피즘.py` 실행)
"""
import streamlit as st
import streamlit.components.v1 as components

st.set_page_config(
    page_title="CMS 매출 대시보드",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

INK = "#1a1a2e"
st.markdown(f"""
<style>
@import url("https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/static/pretendard.min.css");

/* ── 사이드바 상단 타이틀 (CMS 매출 대시보드) ── */
[data-testid="stSidebarNav"]::before {{
    content: "📊 CMS 매출 대시보드";
    display: block; padding: 16px 14px 12px; margin-bottom: 4px;
    font-family: 'Pretendard', 'Malgun Gothic', sans-serif;
    font-size: 1.12rem; font-weight: 800; color: {INK}; letter-spacing: -0.3px;
    border-bottom: 1px solid #e6eaf2; white-space: nowrap;
}}
/* 사이드바 페이지 이름 폰트 */
[data-testid="stSidebarNav"] a {{ font-weight: 600 !important; }}
[data-testid="stSidebarNav"] a span, [data-testid="stSidebarNav"] a p {{
    font-family: 'Pretendard', 'Malgun Gothic', sans-serif !important;
    font-size: 0.93rem !important; font-weight: 600 !important;
}}

[data-testid="stDeployButton"] {{ display: none !important; }}
[data-testid="stSidebar"] {{ background: #fbfcfe; border-right: 1px solid #eceff5; }}

/* ── 사이드바 필터 구분 강화 ── */
[data-testid="stSidebar"] h2 {{
    font-size: 1.02rem !important; font-weight: 800 !important; color: {INK} !important;
    background: #eef2fb !important; border-radius: 8px !important;
    padding: 8px 12px !important; margin: 6px 0 12px !important;
}}
[data-testid="stSidebar"] label p {{ font-weight: 700 !important; color: #3a3a52 !important; font-size: 0.88rem !important; }}
[data-testid="stSidebar"] [data-testid="stDateInput"],
[data-testid="stSidebar"] [data-testid="stSelectbox"],
[data-testid="stSidebar"] [data-testid="stMultiSelect"] {{
    padding-bottom: 11px !important; margin-bottom: 9px !important;
    border-bottom: 1px solid #e9edf5 !important;
}}

/* ── 전역 톤다운 (글자 굵기 완화) ── */
h1 {{ font-weight: 700 !important; }}
[data-testid="stMetricValue"] {{ font-weight: 700 !important; }}

/* ── 섹션 카드 박스 (st.container(border=True)) — 플랫·미니멀 ── */
[data-testid="stVerticalBlockBorderWrapper"] {{
    background: #ffffff !important;
    border: 1px solid #cfd6e6 !important;
    border-radius: 12px !important;
    padding: 2px 18px 12px !important;
    box-shadow: none !important;
    margin-bottom: 12px !important;
}}
/* 카드 안의 카드(중첩 테두리) 방지 — 안쪽은 테두리 제거 */
[data-testid="stVerticalBlockBorderWrapper"] [data-testid="stVerticalBlockBorderWrapper"] {{
    border: none !important; box-shadow: none !important; padding: 0 !important; margin: 0 !important;
}}

/* ── 섹션 제목 (가볍게: 플랫 + 좌측 액센트) ── */
.section-title {{
    font-size: 1.05rem !important; font-weight: 700 !important; color: {INK} !important;
    margin: 10px 0 12px !important; padding-left: 11px !important;
    border-left: 4px solid #4361ee !important; line-height: 1.4 !important;
}}
.section-title.purple {{ border-left-color: #7209b7 !important; }}
.section-title.pink {{ border-left-color: #f72585 !important; }}
.sub-label {{
    font-weight: 700 !important; color: #45456a !important;
    margin: 14px 0 6px !important; padding-left: 9px !important;
    border-left: 3px solid #c7d0ee !important;
}}
[data-testid="stTabs"] [data-baseweb="tab-panel"] {{ padding-top: 8px !important; }}
</style>
""", unsafe_allow_html=True)

# Streamlit 상단 메뉴 한글화 (전 페이지 공통)
components.html("""
<script>
(function() {
    const T = {'Rerun':'새로고침','Settings':'설정','Print':'인쇄',
        'Record a screencast':'화면 녹화','About':'정보',
        'Developer options':'개발자 옵션','Clear cache':'캐시 초기화'};
    function tr(root){try{const doc=root.ownerDocument||root;
        const w=doc.createTreeWalker(root,NodeFilter.SHOW_TEXT);const ns=[];
        while(w.nextNode())ns.push(w.currentNode);
        ns.forEach(n=>{const t=n.textContent.trim();if(T[t])n.textContent=T[t];});}catch(e){}}
    function init(){try{const doc=window.parent.document;
        const obs=new MutationObserver(ms=>{ms.forEach(m=>{m.addedNodes.forEach(nd=>{
            if(nd.nodeType===1)tr(nd);});});});
        obs.observe(doc.body,{childList:true,subtree:true});}catch(e){}}
    init();
})();
</script>
""", height=0)

# ── 공통 Plotly 테마 (전 페이지 차트 베이스 통일) ──
import plotly.io as pio
import plotly.graph_objects as _go
pio.templates["premium"] = _go.layout.Template(layout=dict(
    font=dict(family="Pretendard, Malgun Gothic, sans-serif", size=12, color="#2b2b3a"),
    paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
    xaxis=dict(showgrid=False, zeroline=False),
    yaxis=dict(gridcolor="#eef1f6", zeroline=False),
    hoverlabel=dict(font_family="Pretendard, Malgun Gothic, sans-serif", font_size=12),
))
pio.templates.default = "plotly_white+premium"

# ── 페이지 순서 (KPI목표 → 스내피즘 → 포토이즘 → IP정산 → 기간후 → 주간) ──
# url_path 를 명시해 경로 충돌 방지, KPI 를 기본 진입 페이지로 지정
pages = [
    st.Page("views/0_🎯_KPI목표.py",               title="KPI목표",            icon="🎯", url_path="kpi", default=True),
    st.Page("views/0_📊_스내피즘.py",              title="스내피즘",           icon="📊", url_path="snapism"),
    st.Page("views/1_📸_포토이즘.py",              title="포토이즘",           icon="📸", url_path="photoism"),
    st.Page("views/2_💰_IP정산현황_(스내피즘).py", title="IP정산현황 (스내피즘)", icon="💰", url_path="settlement"),
    st.Page("views/3_⚠️_기간_후_매출분석.py",       title="기간 후 매출분석",      icon="⚠️", url_path="expired"),
    st.Page("views/4_📋_주간리포트.py",            title="주간리포트",          icon="📋", url_path="weekly"),
]

pg = st.navigation(pages)
pg.run()
