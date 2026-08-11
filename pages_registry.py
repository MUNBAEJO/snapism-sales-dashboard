"""대시보드 페이지 목록 — 한 곳에서만 정의한다.

라우터(스내피즘.py)와 팀 권한 UI(auth.render_admin_console)가 같은 목록을 봐야
"관리 화면에는 있는데 실제로는 없는 페이지" 같은 어긋남이 안 생긴다.

페이지를 추가할 때 여기 한 줄만 넣으면 라우터·권한 체크박스에 동시에 반영된다.
key 는 팀 권한 저장에 쓰이는 식별자라 **한 번 정하면 바꾸지 말 것**
(바꾸면 기존 팀에 저장된 권한이 그 페이지를 못 찾는다).

★목록 순서 = 사이드바 순서다. 2026-08-11 에 **실제로 쓰는 것부터** 위로 올렸다.
"""

PAGES = [
    # key            file                                    title              icon  url_path       default_on
    ("photoism",   "views/1_📸_포토이즘.py",                 "포토이즘",          "📸", "photoism",    True),
    ("snapism",    "views/0_📊_스내피즘.py",                 "스내피즘",          "📊", "snapism",     True),
    ("settledoc",  "views/8_🧾_IP정산서.py",                 "IP정산서",         "🧾", "settlement-doc", False),
    ("ipcal",      "views/9_📅_오픈캘린더.py",                "IP 오픈 캘린더",    "📅", "calendar",    True),
    ("sm",         "views/6_🎬_SM촬영현황.py",                "SM 촬영현황",       "🎬", "sm-shooting", False),
    ("runs",       "views/7_🆚_타이틀_런_비교.py",            "타이틀 런 비교",     "🆚", "runs",        False),
    ("kpi",        "views/0_🎯_KPI목표.py",                  "KPI목표(수정중)",   "🎯", "kpi",         True),
    # ↓ settledoc 으로 대체 예정. 2026-06 정산을 새 페이지로 끝내고 숫자를 대조한 뒤 제거한다.
    #   (여기는 스내피즘 전용 + 렌탈 필터가 없어 같은 IP에 다른 금액이 나온다)
    ("settlement", "views/2_💰_IP정산현황_(스내피즘).py",      "IP매출 조회 (구)",   "💰", "settlement", False),
    ("weekly",     "views/4_📋_주간리포트.py",                "주간리포트",        "📋", "weekly",      True),
    ("expired",    "views/3_⚠️_기간_후_매출분석.py",           "기간 후 매출분석",   "⚠️", "expired",     False),
]

# ★사이드바에서 뺄 페이지 (2026-08-11 사용자 요청).
#   **지우지 않고 감춘다** — 파일·데이터·권한 설정을 그대로 두고 여기서만 빼면
#   되돌릴 때 이 집합에서 key 를 지우기만 하면 된다.
#   라우터가 st.navigation 에 안 올리므로 url 직접 접근도 같이 막힌다.
HIDDEN = {"weekly", "expired"}

# 사이드바에 실제로 오르는 목록(순서 유지).
VISIBLE_PAGES = [p for p in PAGES if p[0] not in HIDDEN]

# 관리 화면은 언제나 소유자 전용 — 팀 권한으로 열어줄 수 있으면 안 된다.
# 목록 맨 끝에 붙는다(라우터가 마지막에 append 한다).
ADMIN_PAGE = ("admin", "views/5_🔐_접속관리.py", "접속·계정 관리", "🔐", "admin")

PAGE_KEYS   = [p[0] for p in PAGES]
PAGE_TITLE  = {p[0]: p[2] for p in PAGES}
PAGE_ICON   = {p[0]: p[3] for p in PAGES}
URL_TO_KEY  = {p[4]: p[0] for p in PAGES}
URL_TO_KEY[ADMIN_PAGE[4]] = ADMIN_PAGE[0]

# 팀이 없는(=아직 배정 안 된) 승인 계정이 기본으로 보는 페이지.
# 지금까지 전원에게 열려 있던 것 그대로 — 팀 기능을 켜도 기존 사용자가 갑자기
# 아무것도 못 보게 되는 일이 없도록 하는 안전장치. 감춘 페이지는 뺀다.
DEFAULT_PAGES = [p[0] for p in PAGES if p[5] and p[0] not in HIDDEN]
