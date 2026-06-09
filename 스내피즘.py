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
[data-testid="stSidebarNav"]::before {{
    content: "📊 CMS 매출 대시보드";
    display: block; padding: 14px 14px 10px; margin-bottom: 6px;
    font-size: 1.0rem; font-weight: 800; color: {INK};
    border-bottom: 1px solid #e9edf5; white-space: nowrap;
}}
[data-testid="stDeployButton"] {{ display: none !important; }}
[data-testid="stSidebar"] {{ background: #fbfcfe; border-right: 1px solid #eceff5; }}

/* ── 전역 섹션 헤더 (모든 대시보드 공통, 구분 강화) ── */
.section-title {{
    font-size: 1.08rem !important; font-weight: 800 !important; color: {INK} !important;
    background: linear-gradient(135deg, #f4f7ff 0%, #eef2fb 100%) !important;
    border: 1px solid #e3e9f7 !important;
    border-left: 5px solid #4361ee !important;
    border-radius: 10px !important;
    padding: 11px 16px !important;
    margin: 28px 0 14px !important;
    box-shadow: 0 1px 5px rgba(67,97,238,0.07) !important;
    line-height: 1.35 !important;
}}
.section-title.purple {{ border-left-color: #7209b7 !important;
    background: linear-gradient(135deg, #faf5ff 0%, #f3ecfb 100%) !important; }}
.section-title.pink {{ border-left-color: #f72585 !important;
    background: linear-gradient(135deg, #fff5fa 0%, #fdecf4 100%) !important; }}
/* 첫 섹션은 위 여백 줄임 */
[data-testid="stVerticalBlock"] > [data-testid="stElementContainer"]:first-child .section-title {{ margin-top: 6px !important; }}
/* 하위 라벨도 구분감 부여 */
.sub-label {{
    font-weight: 700 !important; color: #45456a !important;
    margin: 16px 0 6px !important; padding-left: 10px !important;
    border-left: 3px solid #c7d0ee !important;
}}
/* 탭 패널 안쪽 여백 */
[data-testid="stTabs"] [data-baseweb="tab-panel"] {{ padding-top: 6px !important; }}
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
