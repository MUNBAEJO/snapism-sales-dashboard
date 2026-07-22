"""대시보드 페이지 목록 — 한 곳에서만 정의한다.

라우터(스내피즘.py)와 팀 권한 UI(auth.render_admin_console)가 같은 목록을 봐야
"관리 화면에는 있는데 실제로는 없는 페이지" 같은 어긋남이 안 생긴다.

페이지를 추가할 때 여기 한 줄만 넣으면 라우터·권한 체크박스에 동시에 반영된다.
key 는 팀 권한 저장에 쓰이는 식별자라 **한 번 정하면 바꾸지 말 것**
(바꾸면 기존 팀에 저장된 권한이 그 페이지를 못 찾는다).
"""

PAGES = [
    # key            file                                    title              icon  url_path       default_on
    ("kpi",        "views/0_🎯_KPI목표.py",                  "KPI목표",          "🎯", "kpi",         True),
    ("snapism",    "views/0_📊_스내피즘.py",                 "스내피즘",          "📊", "snapism",     True),
    ("photoism",   "views/1_📸_포토이즘.py",                 "포토이즘",          "📸", "photoism",    True),
    ("weekly",     "views/4_📋_주간리포트.py",                "주간리포트",        "📋", "weekly",      True),
    ("runs",       "views/7_🆚_타이틀_런_비교.py",            "타이틀 런 비교",     "🆚", "runs",        False),
    ("settlement", "views/2_💰_IP정산현황_(스내피즘).py",      "IP정산현황 (스내피즘)", "💰", "settlement", False),
    ("expired",    "views/3_⚠️_기간_후_매출분석.py",           "기간 후 매출분석",   "⚠️", "expired",     False),
    ("sm",         "views/6_🎬_SM촬영현황.py",                "SM 촬영현황",       "🎬", "sm-shooting", False),
]

# 관리 화면은 언제나 소유자 전용 — 팀 권한으로 열어줄 수 있으면 안 된다.
ADMIN_PAGE = ("admin", "views/5_🔐_접속관리.py", "접속·계정 관리", "🔐", "admin")

PAGE_KEYS   = [p[0] for p in PAGES]
PAGE_TITLE  = {p[0]: p[2] for p in PAGES}
PAGE_ICON   = {p[0]: p[3] for p in PAGES}
URL_TO_KEY  = {p[4]: p[0] for p in PAGES}
URL_TO_KEY[ADMIN_PAGE[4]] = ADMIN_PAGE[0]

# 팀이 없는(=아직 배정 안 된) 승인 계정이 기본으로 보는 페이지.
# 지금까지 전원에게 열려 있던 4개 그대로 — 팀 기능을 켜도 기존 사용자가 갑자기
# 아무것도 못 보게 되는 일이 없도록 하는 안전장치.
DEFAULT_PAGES = [p[0] for p in PAGES if p[5]]
