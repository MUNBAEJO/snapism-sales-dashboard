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
import datetime
from pathlib import Path

import streamlit as st

BASE_DIR           = Path(__file__).parent
ALLOWED_USERS_PATH = BASE_DIR / "allowed-users.json"
ACCESS_LOG_PATH    = BASE_DIR / "logs" / "dashboard_access.log"

# 소유자 — 전체 권한 + 계정 승인 권한 (deploy-checker ALLOWED_EMAILS 와 동일)
OWNER_EMAILS = {"ansqo34@seobuk.kr"}
# (선택) 도메인 통째 허용. 비우면 승인제만. 예: "seobuk.kr"
ALLOWED_DOMAIN = ""

# 로그인 유지 시간 (초). 이 시간이 지나면 강제 로그아웃 → 재로그인.
SESSION_MAX_SECONDS = 2 * 60 * 60  # 2시간


# ── 승인 계정 스토어 ──────────────────────────────────────────────
def _load_users() -> dict:
    try:
        v = json.loads(ALLOWED_USERS_PATH.read_text(encoding="utf-8"))
        return {
            "approved": [str(e).strip().lower() for e in v.get("approved", []) if str(e).strip()],
            "pending":  [str(e).strip().lower() for e in v.get("pending",  []) if str(e).strip()],
        }
    except Exception:
        return {"approved": [], "pending": []}


def _save_users(u: dict) -> None:
    ALLOWED_USERS_PATH.write_text(
        json.dumps(u, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def is_owner(email: str | None) -> bool:
    return bool(email) and email.strip().lower() in OWNER_EMAILS


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
    u = _load_users()
    if e in u["approved"] or e in u["pending"]:
        return
    u["pending"].append(e)
    _save_users(u)


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
    role = "소유자" if is_owner(email) else "승인 계정"
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

    tab_users, tab_logs = st.tabs(["👥 계정 승인", "📜 접속 로그"])

    # ── 계정 승인 ──
    with tab_users:
        u = _load_users()
        with st.container(border=True):
            pend_n = len(u["pending"])
            st.markdown(f"**승인 대기**  ({pend_n}건)")
            if u["pending"]:
                for e in u["pending"]:
                    c1, c2, c3 = st.columns([4, 1, 1])
                    c1.write(e)
                    if c2.button("승인", key=f"ap_{e}", type="primary"):
                        _approve(e); _log_access(email, f"approve:{e}"); st.rerun()
                    if c3.button("거절", key=f"rj_{e}"):
                        _reject(e); _log_access(email, f"reject:{e}"); st.rerun()
            else:
                st.caption("대기 중인 계정이 없어요.")

        with st.container(border=True):
            st.markdown(f"**승인된 계정**  ({len(u['approved'])}명)")
            if u["approved"]:
                for e in u["approved"]:
                    c1, c2 = st.columns([5, 1])
                    c1.write(e)
                    if c2.button("해제", key=f"rv_{e}"):
                        _revoke(e); _log_access(email, f"revoke:{e}"); st.rerun()
            else:
                st.caption("승인된 계정이 없어요.")
            st.caption("· 소유자 계정은 항상 접근 가능하며 목록에 표시되지 않습니다.")

    # ── 접속 로그 ──
    with tab_logs:
        with st.container(border=True):
            rows = read_access_log(1000)
            c1, c2, c3 = st.columns(3)
            c1.metric("총 기록", f"{len(rows):,}")
            c2.metric("로그인 성공", f"{sum(1 for r in rows if '로그인' in r['이벤트']):,}")
            c3.metric("승인 요청", f"{sum(1 for r in rows if '승인 요청' in r['이벤트']):,}")
            if rows:
                st.dataframe(rows, use_container_width=True, hide_index=True, height=460)
            else:
                st.caption("아직 접속 기록이 없어요.")


def _approve(email: str) -> None:
    e = email.strip().lower()
    u = _load_users()
    u["pending"] = [x for x in u["pending"] if x != e]
    if e not in u["approved"]:
        u["approved"].append(e)
    _save_users(u)


def _reject(email: str) -> None:
    e = email.strip().lower()
    u = _load_users()
    u["pending"] = [x for x in u["pending"] if x != e]
    _save_users(u)


def _revoke(email: str) -> None:
    e = email.strip().lower()
    u = _load_users()
    u["approved"] = [x for x in u["approved"] if x != e]
    _save_users(u)
