"""
CMS 매출 대시보드 — Google 로그인 + 승인제 접근 통제
(deploy-checker(dashboard.js)의 권한 모델을 Streamlit 네이티브 인증으로 이식)

권한 3단계
  (1) 소유자(OWNER_EMAILS)     : 전체 열람 + 계정 승인 권한
  (2) 승인된 계정(approved)    : 전체 열람 (allowed-users.json)
  (3) 승인 대기(pending)       : 로그인은 됐으나 미승인 → '승인 대기' 화면
  (선택) ALLOWED_DOMAIN 비우면 승인제만, 채우면 그 도메인 전체 허용

Google OAuth 클라이언트/콘솔 설정은 .streamlit/secrets.toml 에 있다.
"""
import json
import os
import time
import datetime
from pathlib import Path

import pages_registry

import streamlit as st

BASE_DIR           = Path(__file__).parent
ALLOWED_USERS_PATH = BASE_DIR / "allowed-users.json"
ACCESS_LOG_PATH    = BASE_DIR / "logs" / "dashboard_access.log"

# 소유자 — 전체 권한 + 계정 승인 권한 (deploy-checker ALLOWED_EMAILS 와 동일)
OWNER_EMAILS = {"ansqo34@seobuk.kr", "kyung@seobuk.kr", "cbi9406@seobuk.kr"}
# (선택) 도메인 통째 허용. 비우면 승인제만. 예: "seobuk.kr"
ALLOWED_DOMAIN = ""

# 로그인 유지 시간 (초). 이 시간이 지나면 강제 로그아웃 → 재로그인.
SESSION_MAX_SECONDS = 2 * 60 * 60  # 2시간


# ── 승인 계정 스토어 ──────────────────────────────────────────────
# 역할(role): owner=코드 고정(OWNER_EMAILS, 최고권한) / editor=열람+일부 편집 / viewer=열람 전용.
# allowed-users.json 스키마: {"approved": {"email": "editor|viewer"}, "pending": ["email"]}
# (구버전 approved=["email", ...] 리스트도 자동으로 viewer 로 승격해 읽음 → 마이그레이션 불필요)
ROLES = ("editor", "viewer")
_LOCK_PATH = ALLOWED_USERS_PATH.with_suffix(".json.lock")


def _normalize_users(v: dict) -> dict:
    ap = v.get("approved", {})
    if isinstance(ap, list):                       # 구버전(평면 리스트) → 전부 viewer
        ap = {str(e).strip().lower(): "viewer" for e in ap if str(e).strip()}
    elif isinstance(ap, dict):
        ap = {str(e).strip().lower(): (r if r in ROLES else "viewer")
              for e, r in ap.items() if str(e).strip()}
    else:
        ap = {}
    pend = [str(e).strip().lower() for e in v.get("pending", []) if str(e).strip()]

    # 팀: {"팀이름": {"pages": ["kpi", ...]}} — 없는 페이지 키는 버린다
    # (registry 에서 페이지를 지웠는데 팀에 남아 있으면 유령 권한이 된다)
    teams = {}
    for name, cfg in (v.get("teams") or {}).items():
        n = str(name).strip()
        if not n:
            continue
        pages = [str(k) for k in (cfg or {}).get("pages", []) if str(k) in pages_registry.PAGE_KEYS]
        teams[n] = {"pages": pages}

    # 배정: {"email": "팀이름"} — 승인 계정이 아니거나 없는 팀이면 버린다
    memb = {}
    for e, t in (v.get("member_team") or {}).items():
        e2, t2 = str(e).strip().lower(), str(t).strip()
        if e2 in ap and t2 in teams:
            memb[e2] = t2

    return {"approved": ap, "pending": pend, "teams": teams, "member_team": memb}


def _load_users() -> dict:
    try:
        return _normalize_users(json.loads(ALLOWED_USERS_PATH.read_text(encoding="utf-8")))
    except FileNotFoundError:
        return {"approved": {}, "pending": [], "teams": {}, "member_team": {}}
    except Exception:
        # 파싱 실패(쓰기 도중 등). 원자적 저장으로 거의 없지만, 만약 발생하면 짧게 재시도해
        # 반쯤 쓰인 파일 때문에 승인된 사용자가 '승인 대기'로 튕기는 사고를 막는다.
        for _ in range(3):
            time.sleep(0.05)
            try:
                return _normalize_users(json.loads(ALLOWED_USERS_PATH.read_text(encoding="utf-8")))
            except Exception:
                continue
        return {"approved": {}, "pending": []}


def _save_users(u: dict) -> None:
    # 임시파일에 쓰고 os.replace 로 원자적 교체 → 다른 세션의 torn read(반쯤 쓰인 파일) 방지.
    tmp = ALLOWED_USERS_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(u, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, ALLOWED_USERS_PATH)


def _acquire_lock(timeout: float = 5.0):
    """단순 파일락(O_CREAT|O_EXCL). read-modify-write 경합(lost update) 방지용.
    15초 넘은 락은 스테일로 보고 제거. 실패해도 None 반환(원자적 저장이 최소 보장)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            fd = os.open(str(_LOCK_PATH), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            return _LOCK_PATH
        except FileExistsError:
            try:
                if time.time() - os.path.getmtime(_LOCK_PATH) > 15:
                    os.unlink(_LOCK_PATH)
                    continue
            except OSError:
                pass
            time.sleep(0.05)
        except OSError:
            return None
    return None


def _release_lock(lock) -> None:
    if lock:
        try:
            os.unlink(lock)
        except OSError:
            pass


def _mutate_users(fn) -> None:
    """락 하에서 load → fn(u) 로 in-place 수정 → 원자적 save. 동시 승인/역할변경 경합 방지."""
    lock = _acquire_lock()
    try:
        u = _load_users()
        fn(u)
        _save_users(u)
    finally:
        _release_lock(lock)


def is_owner(email: str | None) -> bool:
    return bool(email) and email.strip().lower() in OWNER_EMAILS


def get_role(email: str | None) -> str | None:
    """owner / editor / viewer / None(미승인). owner 는 코드 고정."""
    if not email:
        return None
    e = email.strip().lower()
    if e in OWNER_EMAILS:
        return "owner"
    if ALLOWED_DOMAIN and e.endswith("@" + ALLOWED_DOMAIN):
        return "viewer"
    return _load_users()["approved"].get(e)


def can_edit(email: str | None) -> bool:
    """편집 권한(목표 수정·RS율 등) — owner·editor 만."""
    return get_role(email) in ("owner", "editor")


def list_teams() -> dict:
    """{"팀이름": {"pages": [...]}} — 화면·권한 판정 공용."""
    return _load_users()["teams"]


def get_team(email: str | None) -> str | None:
    if not email:
        return None
    return _load_users()["member_team"].get(email.strip().lower())


def allowed_pages(email: str | None) -> list[str]:
    """이 계정이 볼 수 있는 페이지 key 목록.

    소유자는 전부. 팀이 배정돼 있으면 그 팀의 목록, 없으면 DEFAULT_PAGES.
    ★팀에 아무 페이지도 안 붙어 있으면 '전부 차단'이 아니라 기본값으로 되돌린다 —
      팀을 갓 만들고 체크를 안 한 상태에서 팀원들이 통째로 잠기는 사고를 막는다.
    """
    if is_owner(email):
        return list(pages_registry.PAGE_KEYS)
    if not can_access(email):
        return []
    t = get_team(email)
    if t:
        pages = _load_users()["teams"].get(t, {}).get("pages", [])
        if pages:
            return [k for k in pages_registry.PAGE_KEYS if k in pages]
    return list(pages_registry.DEFAULT_PAGES)


def can_view_page(email: str | None, page_key: str) -> bool:
    if page_key == pages_registry.ADMIN_PAGE[0]:
        return is_owner(email)          # 관리 화면은 팀 권한으로 못 연다
    return page_key in allowed_pages(email)


def set_team_pages(team: str, pages: list[str]) -> None:
    t = str(team).strip()
    keep = [k for k in pages if k in pages_registry.PAGE_KEYS]

    def _fn(u):
        u.setdefault("teams", {})[t] = {"pages": keep}

    _mutate_users(_fn)


def delete_team(team: str) -> None:
    t = str(team).strip()

    def _fn(u):
        u.get("teams", {}).pop(t, None)
        # 그 팀 소속이던 계정은 배정 해제 → 기본 페이지로 돌아간다(잠기지 않는다)
        u["member_team"] = {e: v for e, v in u.get("member_team", {}).items() if v != t}

    _mutate_users(_fn)


def assign_team(email: str, team: str | None) -> None:
    e = str(email).strip().lower()
    t = (team or "").strip()

    def _fn(u):
        m = u.setdefault("member_team", {})
        if t:
            m[e] = t
        else:
            m.pop(e, None)

    _mutate_users(_fn)


def can_access(email: str | None) -> bool:
    if not email:
        return False
    e = email.strip().lower()
    if e in OWNER_EMAILS:
        return True
    if ALLOWED_DOMAIN and e.endswith("@" + ALLOWED_DOMAIN):
        return True
    return e in _load_users()["approved"]


def _add_pending(email: str) -> None:
    e = email.strip().lower()

    def _fn(u):
        if e in u["approved"] or e in u["pending"]:
            return
        u["pending"].append(e)

    _mutate_users(_fn)


def _user_claim(key: str):
    """st.user 에서 OIDC 클레임 안전 추출 (.get / [] 순서 시도)."""
    u = getattr(st, "user", None)
    if u is None:
        return None
    try:
        v = u.get(key)
        if v is not None:
            return v
    except Exception:
        pass
    try:
        return u[key]
    except Exception:
        return None


def _enforce_session_timeout() -> None:
    """Google id_token 발급시각(iat) 기준 SESSION_MAX_SECONDS 경과 시 강제 로그아웃."""
    iat = _user_claim("iat")
    if not iat:
        return
    try:
        import time
        if time.time() - float(iat) > SESSION_MAX_SECONDS:
            _log_access((st.user.email or "").strip().lower(), "session-expired")
            st.logout()
            st.stop()
    except (TypeError, ValueError):
        pass


def _log_access(email: str, event: str) -> None:
    try:
        ACCESS_LOG_PATH.parent.mkdir(exist_ok=True)
        ts = datetime.datetime.now().isoformat(timespec="seconds")
        with ACCESS_LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(f"{ts}\t{event}\t{email}\n")
    except Exception:
        pass


def log_page_view(email: str, page_key: str) -> None:
    """페이지 열람 기록. ★Streamlit 은 위젯을 건드릴 때마다 스크립트를 통째로 다시
    돌리므로, 그대로 적으면 체크박스 한 번에 수십 줄이 쌓인다. 세션에 마지막 페이지를
    들고 있다가 '바뀌었을 때만' 남긴다."""
    try:
        if st.session_state.get("_last_page_logged") == page_key:
            return
        st.session_state["_last_page_logged"] = page_key
        _log_access(email, f"view:{page_key}")
    except Exception:
        pass


# ── 화면 ──────────────────────────────────────────────────────────
def _render_login_page() -> None:
    import urllib.parse
    g_svg = (
        "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 48 48'>"
        "<path fill='#EA4335' d='M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z'/>"
        "<path fill='#4285F4' d='M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z'/>"
        "<path fill='#FBBC05' d='M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z'/>"
        "<path fill='#34A853' d='M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z'/></svg>"
    )
    g_uri = "data:image/svg+xml;charset=utf-8," + urllib.parse.quote(g_svg)

    css = """
    <style>
    @import url("https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/static/pretendard.min.css");
    [data-testid="stSidebar"], [data-testid="stSidebarNav"], [data-testid="stHeader"], [data-testid="stToolbar"] { display:none !important; }
    .stApp { background:#eef1f6; font-family:'Pretendard','Malgun Gothic',sans-serif; }
    /* 화면 정중앙 정렬 */
    section[data-testid="stMain"] { display:flex; flex-direction:column; justify-content:center; align-items:center; min-height:100vh; }
    .block-container { width:100% !important; max-width: 1220px !important; padding: 3vh 1.4rem !important; }
    section[data-testid="stMain"] [data-testid="stVerticalBlock"] { width:100% !important; }

    /* 2단 카드 */
    [data-testid="stHorizontalBlock"] {
        width:100% !important; min-height:540px;
        gap:0 !important; background:#fff; border-radius:26px; overflow:hidden;
        box-shadow:0 34px 80px -28px rgba(30,45,100,.45); border:1px solid #e9ecf3;
        align-items:stretch !important;
    }
    [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:first-child {
        background:linear-gradient(155deg,#3b62f6 0%, #5840ee 55%, #7a35e0 100%);
        padding:60px 56px !important;
        display:flex !important; flex-direction:column !important; justify-content:center !important;
    }
    [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:last-child {
        padding:66px 58px 50px !important;
        display:flex !important; flex-direction:column !important; justify-content:center !important;
    }

    /* 좌 패널 */
    .lp-badge { width:70px;height:70px;border-radius:20px;background:rgba(255,255,255,.16);
        border:1px solid rgba(255,255,255,.28);
        display:flex;align-items:center;justify-content:center;font-size:34px;margin-bottom:30px; }
    .lp-title { font-size:2.2rem;font-weight:800;line-height:1.16;letter-spacing:-.5px;color:#fff;margin:0 0 16px; }
    .lp-desc { font-size:1rem;line-height:1.65;color:rgba(255,255,255,.84);margin:0 0 38px; }
    .lp-feat { display:flex;align-items:flex-start;gap:14px;margin:18px 0; }
    .lp-feat .ic { width:36px;height:36px;flex:0 0 36px;border-radius:11px;background:rgba(255,255,255,.16);
        display:flex;align-items:center;justify-content:center;font-size:17px; }
    .lp-feat b { display:block;font-size:1rem;font-weight:700;color:#fff;margin-bottom:2px; }
    .lp-feat span { font-size:.85rem;color:rgba(255,255,255,.74); }

    /* 우 패널 */
    .rp-label { color:#4361ee;font-weight:800;font-size:.86rem;letter-spacing:.2px;margin-bottom:15px; }
    .rp-title { font-size:2rem;font-weight:800;color:#16182e;margin:0 0 10px; }
    .rp-sub { color:#6b7390;font-size:.98rem;line-height:1.6;margin:0; }
    .rp-note { background:#f4f6fb;border:1px solid #e7ebf4;border-radius:13px;
        padding:15px 17px;color:#5c6480;font-size:.86rem;line-height:1.62;margin-top:8px; }
    .rp-note b { color:#3a3f5c; }
    .rp-foot { color:#a6acbe;font-size:.78rem;margin-top:16px; }

    /* Google 버튼 */
    div[data-testid="stButton"] > button {
        background:#fff !important;color:#3c4043 !important;border:1px solid #dadce0 !important;
        border-radius:13px !important;font-family:'Pretendard','Malgun Gothic',sans-serif !important;
        font-weight:700 !important;font-size:1.05rem !important;padding:15px 18px !important;
        box-shadow:0 1px 2px rgba(20,30,60,.05) !important;
        transition:box-shadow .14s,border-color .14s,transform .04s !important;
    }
    div[data-testid="stButton"] > button:hover { border-color:#c2c9d6 !important;box-shadow:0 5px 16px -3px rgba(40,55,120,.22) !important; }
    div[data-testid="stButton"] > button:active { transform:translateY(1px) !important; }
    div[data-testid="stButton"] > button::before {
        content:"";display:inline-block;width:21px;height:21px;margin-right:11px;vertical-align:-5px;
        background:url("__GG__") center/contain no-repeat;
    }
    </style>
    """.replace("__GG__", g_uri)
    st.markdown(css, unsafe_allow_html=True)

    left, right = st.columns(2)
    with left:
        st.markdown(
            """
            <div class="lp-badge">📊</div>
            <div class="lp-title">CMS 매출<br>대시보드</div>
            <div class="lp-desc">스내피즘·포토이즘 매출을 한곳에서<br>집계·분석하는 내부 매출 분석 도구입니다.</div>
            <div class="lp-feat"><div class="ic">🔒</div><div><b>구글 계정 인증</b><span>안전한 OAuth 로그인</span></div></div>
            <div class="lp-feat"><div class="ic">✅</div><div><b>승인제 접근</b><span>관리자가 승인한 계정만 이용</span></div></div>
            <div class="lp-feat"><div class="ic">📋</div><div><b>접속 로그</b><span>접속·행동 감사 기록</span></div></div>
            """,
            unsafe_allow_html=True,
        )
    with right:
        st.markdown(
            """
            <div class="rp-label">SEOBUK · 콘텐츠운영팀</div>
            <div class="rp-title">로그인</div>
            <div class="rp-sub">계속하려면 회사 구글 계정으로<br>로그인하세요.</div>
            """,
            unsafe_allow_html=True,
        )
        st.button("Google로 로그인", use_container_width=True, on_click=st.login, args=["google"])
        st.markdown(
            """
            <div class="rp-note">🔒 <b>승인된 계정만</b> 로그인됩니다. 처음 로그인하면 승인 대기로 접수되며, 관리자 승인 후 이용할 수 있어요.</div>
            <div class="rp-foot">© SEOBUK · 콘텐츠운영팀</div>
            """,
            unsafe_allow_html=True,
        )


def _render_pending_page(email: str) -> None:
    st.markdown(
        f"""
        <style>
        [data-testid="stSidebar"], [data-testid="stSidebarNav"] {{ display:none !important; }}
        .pend-card {{
            max-width: 440px; margin: 9vh auto 0; padding: 38px 40px 30px;
            background:#fff; border:1px solid #e5e7eb; border-radius:16px;
            box-shadow:0 8px 30px rgba(20,30,60,.06); text-align:center;
            font-family:'Pretendard','Malgun Gothic',sans-serif;
        }}
        .pend-card .lock {{ font-size:42px; }}
        .pend-card h2 {{ margin:12px 0 6px; color:#0f172a; font-weight:800; }}
        .pend-card p  {{ color:#64748b; font-size:.92rem; line-height:1.7; }}
        .pend-card b  {{ color:#1a1a2e; }}
        </style>
        <div class="pend-card">
          <div class="lock">🔒</div>
          <h2>승인 대기 중</h2>
          <p><b>{email}</b><br>관리자 승인 후 로그인할 수 있어요.<br>승인 요청이 접수되었습니다.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )
    _, mid, _ = st.columns([1, 1.2, 1])
    with mid:
        st.button("다른 계정으로 로그인", use_container_width=True, on_click=st.logout)


# ── 라우터 진입점 ─────────────────────────────────────────────────
def require_login() -> str:
    """라우터 최상단에서 호출. 통과 못 하면 화면 렌더 후 st.stop()."""
    if not getattr(st, "user", None) or not st.user.is_logged_in:
        _render_login_page()
        st.stop()

    # 2시간 경과 세션은 강제 로그아웃
    _enforce_session_timeout()

    email = (st.user.email or "").strip().lower()
    if not can_access(email):
        _add_pending(email)
        if not st.session_state.get("_pending_logged"):
            _log_access(email, "pending")
            st.session_state["_pending_logged"] = True
        _render_pending_page(email)
        st.stop()

    if not st.session_state.get("_access_logged"):
        _log_access(email, "login")
        st.session_state["_access_logged"] = True
    return email


def render_sidebar_account() -> None:
    """사이드바 좌하단 고정: 현재 계정(아바타·이메일·권한) + 로그아웃.
    st.sidebar 안에 그려서 사이드바를 접으면 함께 사라지고 너비도 사이드바에 맞춰진다.
    로그아웃은 Streamlit 기본 경로(/auth/logout) 링크로 처리."""
    email = (st.user.email or "").strip().lower()
    _RL = {"owner": "소유자", "editor": "에디터", "viewer": "뷰어"}
    role = _RL.get(get_role(email), "승인 계정")
    initial = (email[:1] or "?").upper()
    st.sidebar.markdown(
        f"""
        <style>
        /* 좌하단 계정 바가 가리지 않게 사이드바 본문 아래 여백 확보 */
        [data-testid="stSidebarUserContent"],
        [data-testid="stSidebarContent"] {{ padding-bottom: 66px !important; }}
        .sb-account {{
            position: fixed; left: 0; bottom: 0; width: 100%; z-index: 999990;
            box-sizing: border-box;
            display: flex; align-items: center; gap: 9px;
            padding: 9px 14px; border-top: 1px solid #e6eaf2; background: #fbfcfe;
            font-family: 'Pretendard','Malgun Gothic',sans-serif;
        }}
        .sb-account .avatar {{
            width: 30px; height: 30px; flex: 0 0 30px; border-radius: 50%;
            background: #e7ebf9; color: #4361ee; font-weight: 800; font-size: .85rem;
            display: flex; align-items: center; justify-content: center;
        }}
        .sb-account .meta {{ min-width: 0; line-height: 1.25; }}
        .sb-account .meta .nm {{
            font-size: .8rem; font-weight: 700; color: #1a1a2e;
            white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 132px;
        }}
        .sb-account .meta .rl {{ font-size: .7rem; color: #8a8aa3; }}
        .sb-account a.logout {{
            margin-left: auto; flex: 0 0 auto;
            font-size: .72rem; font-weight: 700; color: #e03131; text-decoration: none;
            background: #fff; border: 1px solid #f0c2c2; border-radius: 7px; padding: 3px 9px;
            white-space: nowrap; transition: background .12s;
        }}
        .sb-account a.logout:hover {{ background:#fff5f5; }}
        </style>
        <div class="sb-account">
          <div class="avatar">{initial}</div>
          <div class="meta">
            <div class="nm" title="{email}">{email}</div>
            <div class="rl">{role}</div>
          </div>
          <a class="logout" href="/auth/logout" target="_self">로그아웃</a>
        </div>
        """,
        unsafe_allow_html=True,
    )


# ── 접속 로그 ──────────────────────────────────────────────────────
_EVENT_LABEL = {
    "login":   "✅ 로그인",
    "pending": "⏳ 승인 요청",
}


def _pretty_event(ev: str) -> str:
    if ev in _EVENT_LABEL:
        return _EVENT_LABEL[ev]
    if ev.startswith("approve:"):
        return "👍 승인 → " + ev.split(":", 1)[1]
    if ev.startswith("reject:"):
        return "🚫 거절 → " + ev.split(":", 1)[1]
    if ev.startswith("revoke:"):
        return "⛔ 해제 → " + ev.split(":", 1)[1]
    if ev.startswith("role:"):
        return "🔧 역할변경 → " + ev.split(":", 1)[1]
    if ev.startswith("team:"):
        return "👥 팀배정 → " + ev.split(":", 1)[1]
    if ev.startswith("teamset:"):
        return "🗂 팀권한 변경 → " + ev.split(":", 1)[1]
    if ev.startswith("teamdel:"):
        return "🗑 팀삭제 → " + ev.split(":", 1)[1]
    if ev.startswith("view:"):
        k = ev.split(":", 1)[1]
        return "👁 열람 → " + pages_registry.PAGE_TITLE.get(k, k)
    return ev


def read_access_log(limit: int = 1000) -> list[dict]:
    """접속 로그를 최신순으로 파싱해 반환."""
    try:
        lines = ACCESS_LOG_PATH.read_text(encoding="utf-8").splitlines()
    except Exception:
        return []
    rows = []
    for ln in reversed(lines):
        parts = ln.split("\t")
        if len(parts) >= 3:
            ts = parts[0].replace("T", " ")
            rows.append({"시각": ts, "이벤트": _pretty_event(parts[1]), "계정": parts[2]})
        if len(rows) >= limit:
            break
    return rows


# ── 소유자 전용 관리 콘솔 (접속 로그 + 계정 승인) ─────────────────
def render_admin_console() -> None:
    """소유자 전용 페이지 본문. 비소유자는 차단."""
    email = (st.user.email or "").strip().lower()
    if not is_owner(email):
        st.error("🔒 이 페이지는 소유자만 볼 수 있어요.")
        st.stop()

    st.markdown('<div class="section-title">🔐 접속·계정 관리</div>', unsafe_allow_html=True)
    st.caption("접속 로그 열람과 계정 승인은 소유자(나)만 가능합니다.")

    tab_users, tab_teams, tab_logs = st.tabs(["👥 계정 승인", "🗂 팀·권한", "📜 활동 로그"])

    # ── 계정 승인 ──
    _ROLE_LABEL = {"editor": "✏️ 에디터(편집)", "viewer": "👁 뷰어(열람)"}
    with tab_users:
        u = _load_users()
        with st.container(border=True):
            pend_n = len(u["pending"])
            st.markdown(f"**승인 대기**  ({pend_n}건)")
            if u["pending"]:
                for e in u["pending"]:
                    c1, c2, c3, c4 = st.columns([3.4, 1.7, 1, 1])
                    c1.write(e)
                    _r = c2.selectbox("역할", list(ROLES), key=f"aprole_{e}",
                                      format_func=lambda x: _ROLE_LABEL.get(x, x),
                                      label_visibility="collapsed")
                    if c3.button("승인", key=f"ap_{e}", type="primary"):
                        _approve(e, _r); _log_access(email, f"approve:{e}={_r}"); st.rerun()
                    if c4.button("거절", key=f"rj_{e}"):
                        _reject(e); _log_access(email, f"reject:{e}"); st.rerun()
            else:
                st.caption("대기 중인 계정이 없어요.")

        with st.container(border=True):
            st.markdown(f"**승인된 계정**  ({len(u['approved'])}명)")
            if u["approved"]:
                _tnames = ["(팀 없음)"] + sorted(u["teams"])
                for e, r in u["approved"].items():
                    c1, c2, c3, c4 = st.columns([3.2, 2.0, 1.0, 0.9])
                    c1.write(e)
                    _cur = u["member_team"].get(e) or "(팀 없음)"
                    _nt = c2.selectbox("팀", _tnames,
                                       index=_tnames.index(_cur) if _cur in _tnames else 0,
                                       key=f"team_{e}", label_visibility="collapsed")
                    if c3.button("팀 배정", key=f"tset_{e}", disabled=(_nt == _cur)):
                        assign_team(e, None if _nt == "(팀 없음)" else _nt)
                        _log_access(email, f"team:{e}={_nt}"); st.rerun()
                    if c4.button("해제", key=f"rv_{e}"):
                        _revoke(e); _log_access(email, f"revoke:{e}"); st.rerun()
                    _pg = allowed_pages(e)
                    c1.caption("볼 수 있는 페이지: "
                               + " · ".join(pages_registry.PAGE_TITLE[k] for k in _pg))
            else:
                st.caption("승인된 계정이 없어요.")
            st.caption("· 소유자 계정은 항상 최고 권한이며 목록에 표시되지 않습니다.  "
                       "· 팀을 배정하면 그 팀에 체크된 페이지만 보여요. "
                       "팀이 없으면 기본 페이지(KPI·스내피즘·포토이즘·주간리포트)를 봐요.")

    # ── 팀·권한 ──
    with tab_teams:
        u = _load_users()
        with st.container(border=True):
            st.markdown("**새 팀 만들기**")
            c1, c2 = st.columns([3, 1])
            _new = c1.text_input("팀 이름", key="newteam", label_visibility="collapsed",
                                 placeholder="예: 마케팅팀, 해외영업팀, 정산팀")
            if c2.button("만들기", type="primary", disabled=not _new.strip()):
                if _new.strip() in u["teams"]:
                    st.warning("같은 이름의 팀이 이미 있어요.")
                else:
                    # 새 팀은 기본 페이지로 시작 — 빈 채로 두면 배정하는 순간 아무것도 못 본다
                    set_team_pages(_new.strip(), list(pages_registry.DEFAULT_PAGES))
                    _log_access(email, f"teamset:{_new.strip()}"); st.rerun()

        if not u["teams"]:
            st.caption("아직 만든 팀이 없어요. 팀을 만들고 볼 페이지를 체크한 뒤, "
                       "'계정 승인' 탭에서 팀을 배정하면 돼요.")
        for tname, cfg in sorted(u["teams"].items()):
            with st.container(border=True):
                _mem = [e for e, t in u["member_team"].items() if t == tname]
                h1, h2 = st.columns([4, 1])
                h1.markdown(f"**{tname}**  ·  {len(_mem)}명")
                _sel = []
                cols = st.columns(4)
                for i, k in enumerate(pages_registry.PAGE_KEYS):
                    lbl = f"{pages_registry.PAGE_ICON[k]} {pages_registry.PAGE_TITLE[k]}"
                    if cols[i % 4].checkbox(lbl, value=(k in cfg["pages"]), key=f"tp_{tname}_{k}"):
                        _sel.append(k)
                b1, b2, _ = st.columns([1, 1, 3])
                if b1.button("저장", key=f"tsave_{tname}", type="primary",
                             disabled=(_sel == cfg["pages"])):
                    set_team_pages(tname, _sel)
                    _log_access(email, f"teamset:{tname}={','.join(_sel)}"); st.rerun()
                if b2.button("팀 삭제", key=f"tdel_{tname}"):
                    delete_team(tname)
                    _log_access(email, f"teamdel:{tname}"); st.rerun()
                if _mem:
                    st.caption("소속: " + " · ".join(sorted(_mem)))
                if not _sel:
                    st.caption("⚠️ 한 장도 체크하지 않으면 팀원이 기본 페이지를 보게 돼요 "
                               "(전부 차단이 아니에요 — 실수로 잠기는 걸 막으려고요).")
        st.caption("· 관리 화면(접속·계정 관리)은 팀 권한으로 열 수 없어요. 항상 소유자 전용이에요.")

    # ── 활동 로그 ──
    with tab_logs:
        with st.container(border=True):
            rows = read_access_log(3000)
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("총 기록", f"{len(rows):,}")
            c2.metric("로그인", f"{sum(1 for r in rows if '로그인' in r['이벤트']):,}")
            c3.metric("페이지 열람", f"{sum(1 for r in rows if '열람' in r['이벤트']):,}")
            c4.metric("관리 활동", f"{sum(1 for r in rows if any(x in r['이벤트'] for x in ('승인', '거절', '해제', '역할', '팀')) ):,}")
            _kinds = ["전체", "페이지 열람", "로그인", "관리 활동"]
            _k = st.radio("종류", _kinds, horizontal=True, key="logkind",
                          label_visibility="collapsed")
            if _k == "페이지 열람":
                rows = [r for r in rows if "열람" in r["이벤트"]]
            elif _k == "로그인":
                rows = [r for r in rows if "로그인" in r["이벤트"] or "승인 요청" in r["이벤트"]]
            elif _k == "관리 활동":
                rows = [r for r in rows if any(x in r["이벤트"] for x in ("승인", "거절", "해제", "역할", "팀"))]
            if rows:
                st.dataframe(rows, use_container_width=True, hide_index=True, height=460)
            else:
                st.caption("해당하는 기록이 없어요.")


def _approve(email: str, role: str = "viewer") -> None:
    e = email.strip().lower()
    role = role if role in ROLES else "viewer"

    def _fn(u):
        u["pending"] = [x for x in u["pending"] if x != e]
        u["approved"][e] = role

    _mutate_users(_fn)


def _reject(email: str) -> None:
    e = email.strip().lower()
    _mutate_users(lambda u: u.__setitem__("pending", [x for x in u["pending"] if x != e]))


def _revoke(email: str) -> None:
    e = email.strip().lower()
    _mutate_users(lambda u: u["approved"].pop(e, None))


def set_role(email: str, role: str) -> None:
    e = email.strip().lower()
    role = role if role in ROLES else "viewer"

    def _fn(u):
        if e in u["approved"]:
            u["approved"][e] = role

    _mutate_users(_fn)
