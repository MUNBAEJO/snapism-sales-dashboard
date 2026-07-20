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
    initial_sidebar_state="expanded",   # 왼쪽 사이드바 상시 노출(토스 형태) — 사용자 선호
)

# ── Google 로그인 + 승인제 접근 통제 ──
# 통과하지 못하면 로그인/승인대기 화면을 그리고 여기서 멈춘다.
import auth
auth.require_login()

INK = "#1a1a2e"
st.markdown(f"""
<style>
@import url("https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/static/pretendard.min.css");

/* ── 사이드바 = 토스/인디고 톤 (새 카드 디자인과 통일) ── */
[data-testid="stSidebar"] {{ background: #ffffff !important; border-right: 1px solid #eceff4 !important; }}
/* 상단 타이틀 */
[data-testid="stSidebarNav"]::before {{
    content: "📊 CMS 매출 대시보드";
    display: block; padding: 18px 16px 14px; margin: 0 6px 6px;
    font-family: 'Pretendard', 'Malgun Gothic', sans-serif;
    font-size: 1.06rem; font-weight: 800; color: {INK}; letter-spacing: -0.03em;
    border-bottom: 1px solid #eef1f5; white-space: nowrap;
}}
/* nav 항목 = 라운드 pill, 비활성은 회색 톤 다운 */
[data-testid="stSidebarNav"] ul {{ padding: 4px 0 !important; }}
[data-testid="stSidebarNav"] a {{
    position: relative; isolation: isolate;
    font-weight: 600 !important; border-radius: 10px !important;
    padding: 10px 12px !important; margin: 2px 8px !important; color: #4b5563 !important;
    transition: background .12s, box-shadow .12s;
}}
[data-testid="stSidebarNav"] a span, [data-testid="stSidebarNav"] a p {{
    font-family: 'Pretendard', 'Malgun Gothic', sans-serif !important;
    font-size: 0.92rem !important; font-weight: 600 !important; letter-spacing: -0.02em;
}}
[data-testid="stSidebarNav"] a:hover {{ background: #f4f5f7 !important; }}
/* 활성 = 인디고 굵은 글자 + 왼쪽 인디고 액센트 바.
   ※ Streamlit이 aria-current 없이 emotion 해시로만 활성 구분 → components.html 스크립트가
   현재 URL과 일치하는 링크에 data-active="1" 표시 + .nav-accent 자식 바를 삽입한다(버전 안정).
   (활성 pill 배경은 Streamlit 기본 회색을 CSS로 못 덮어서 액센트 바로 강조) */
[data-testid="stSidebarNavLink"][data-active="1"] span,
[data-testid="stSidebarNavLink"][data-active="1"] p {{ color: #4f46e5 !important; font-weight: 700 !important; }}
[data-testid="stSidebarNav"] .nav-accent {{
    position: absolute; left: 0; top: 8px; bottom: 8px; width: 3px;
    background: #4f46e5; border-radius: 0 3px 3px 0; z-index: 2;
}}
/* 활성 pill 배경 = 연인디고 (Streamlit 기본 회색을 CSS로 못 덮어 자식 오버레이로 덮음) */
[data-testid="stSidebarNav"] .nav-fill {{
    position: absolute; left: 0; top: 0; width: 100%; height: 100%;
    border-radius: 10px; background: #eef0fe; z-index: -1; pointer-events: none;
}}
/* 사이드바 expander(실시간 환율) = 테두리 박스 제거, nav와 통일된 플랫 톤 */
[data-testid="stSidebar"] [data-testid="stExpander"] details {{
    border: none !important; border-radius: 10px !important; background: #f6f7f9 !important;
}}
[data-testid="stSidebar"] [data-testid="stExpander"] summary {{
    padding: 8px 12px !important; font-size: 0.86rem !important;
    font-weight: 600 !important; color: #4b5563 !important;
}}
[data-testid="stSidebar"] [data-testid="stExpander"] summary:hover {{ background: #eef1f5 !important; }}

[data-testid="stDeployButton"] {{ display: none !important; }}
/* 사이드바 접기(<) 버튼 잘 보이게 */
[data-testid="stSidebarCollapseButton"] button {{ color: #4f46e5 !important; }}

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

/* ── 모바일(좁은 화면) 최적화 ── 데스크탑 레이아웃은 그대로 두고 640px 이하만 조정 */
@media (max-width: 640px) {{
    /* 본문 좌우 여백 축소 — 좁은 화면 폭을 최대한 활용 */
    [data-testid="stMainBlockContainer"], .block-container {{
        padding-left: 0.7rem !important;
        padding-right: 0.7rem !important;
        padding-top: 3.2rem !important;
    }}
    /* 제목·지표 글자 한 단계 축소(가로 넘침 방지) */
    h1 {{ font-size: 1.5rem !important; line-height: 1.25 !important; }}
    h2 {{ font-size: 1.2rem !important; }}
    [data-testid="stMetricValue"] {{ font-size: 1.4rem !important; }}
    [data-testid="stMetricLabel"] p {{ font-size: 0.8rem !important; }}
    /* 탭 목록이 많아도 가로 스크롤로 모두 접근 가능하게 */
    [data-testid="stTabs"] [data-baseweb="tab-list"] {{
        overflow-x: auto !important; flex-wrap: nowrap !important;
    }}
    [data-testid="stTabs"] [data-baseweb="tab"] {{ white-space: nowrap !important; }}
    /* 섹션 카드 안쪽 여백 축소 */
    [data-testid="stVerticalBlockBorderWrapper"] {{ padding: 2px 11px 10px !important; }}
    /* 사이드바를 화면 대부분 폭으로 펼쳐 필터 조작 편하게 */
    [data-testid="stSidebar"] {{ min-width: 84vw !important; }}
    /* 핵심: 여러 칼럼(지표 카드·병렬 차트·2단 비교)을 세로로 쌓아 가독성 확보.
       데스크탑은 그대로, 640px 이하에서만 한 줄에 하나씩 내려 쌓는다. */
    [data-testid="stHorizontalBlock"] {{ flex-wrap: wrap !important; }}
    [data-testid="stHorizontalBlock"] > [data-testid="stColumn"],
    [data-testid="stHorizontalBlock"] > [data-testid="column"] {{
        flex: 1 1 100% !important;
        width: 100% !important;
        min-width: 100% !important;
    }}
    /* 가로로 넓은 표(국가별 6~7컬럼): 내용 폭으로 펴서 래퍼 안에서 가로 스크롤
       → 칸 폭에 욱여넣어 글자가 칸 선 넘는 현상 방지 */
    .natbl {{ width: max-content !important; min-width: 100% !important; }}
    /* plotly 모드바(카메라·줌 아이콘 툴바)가 차트 위에 겹쳐 보이는 것 숨김 */
    .modebar, .modebar-container {{ display: none !important; }}
    /* plotly 차트가 칸 폭을 넘지 않도록 */
    [data-testid="stPlotlyChart"], .js-plotly-plot {{ max-width: 100% !important; }}
}}
/* 국가별 표 가로 스크롤 래퍼(전 화면 공통, 데스크탑은 표가 100%라 스크롤 안 생김) */
.natbl-wrap {{ overflow-x: auto; -webkit-overflow-scrolling: touch; }}
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
    // 활성 nav 링크에 data-active 표시 (Streamlit이 aria-current 를 안 써서 URL 매칭으로 직접 표시)
    function markNav(doc){try{
        const path=(window.parent.location.pathname||'').replace(/[/]+$/,'');
        doc.querySelectorAll('[data-testid="stSidebarNavLink"]').forEach(a=>{
            let ap='';try{ap=new URL(a.href).pathname.replace(/[/]+$/,'');}catch(e){}
            if(ap&&ap===path){
                a.setAttribute('data-active','1');
                if(!a.querySelector('.nav-fill')){
                    const fill=a.ownerDocument.createElement('span');
                    fill.className='nav-fill';
                    a.insertBefore(fill,a.firstChild);
                }
                if(!a.querySelector('.nav-accent')){
                    const bar=a.ownerDocument.createElement('span');
                    bar.className='nav-accent';
                    a.insertBefore(bar,a.firstChild);
                }
            }else{
                a.removeAttribute('data-active');
                a.querySelectorAll('.nav-accent,.nav-fill').forEach(function(e){e.remove();});
            }
        });}catch(e){}}
    function init(){try{const doc=window.parent.document;
        markNav(doc);
        const obs=new MutationObserver(ms=>{ms.forEach(m=>{m.addedNodes.forEach(nd=>{
            if(nd.nodeType===1)tr(nd);});}); markNav(doc);});
        obs.observe(doc.body,{childList:true,subtree:true});}catch(e){}}
    init();
})();
</script>
""", height=0)

# ── 공통 Plotly 테마 (전 페이지 차트 베이스 통일) ──
# plotly 가 템플릿 검증 시 pandas(pd.Series)를 lazy import 하는데, Streamlit
# 멀티스레드 환경에서 pandas 초기화와 경합하면 'partially initialized module
# pandas' 오류가 난다. 템플릿을 만지기 전에 pandas 를 완전히 import 해 둔다.
import pandas as _pd  # noqa: F401
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
_is_owner = auth.is_owner(st.user.email if getattr(st, "user", None) else None)

pages = [
    st.Page("views/0_🎯_KPI목표.py",               title="KPI목표",            icon="🎯", url_path="kpi", default=True),
    st.Page("views/0_📊_스내피즘.py",              title="스내피즘",           icon="📊", url_path="snapism"),
    st.Page("views/1_📸_포토이즘.py",              title="포토이즘",           icon="📸", url_path="photoism"),
    st.Page("views/7_🆚_타이틀_런_비교.py",        title="타이틀 런 비교",      icon="🆚", url_path="runs"),
]
# IP정산현황·기간 후 매출분석 = 소유자 전용 (다른 계정 사이드바엔 숨김 + url 접근도 차단)
if _is_owner:
    pages.append(st.Page("views/2_💰_IP정산현황_(스내피즘).py", title="IP정산현황 (스내피즘)", icon="💰", url_path="settlement"))
    pages.append(st.Page("views/3_⚠️_기간_후_매출분석.py",       title="기간 후 매출분석",      icon="⚠️", url_path="expired"))
pages.append(st.Page("views/4_📋_주간리포트.py",            title="주간리포트",          icon="📋", url_path="weekly"))

# SM 촬영현황·접속 로그도 소유자 전용
if _is_owner:
    pages.append(st.Page("views/6_🎬_SM촬영현황.py", title="SM 촬영현황", icon="🎬", url_path="sm-shooting"))
    pages.append(st.Page("views/5_🔐_접속관리.py", title="접속·계정 관리", icon="🔐", url_path="admin"))

pg = st.navigation(pages)

# 사이드바 좌하단 고정: 계정 표시 + 로그아웃
auth.render_sidebar_account()

pg.run()
