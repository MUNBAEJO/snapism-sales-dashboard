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
    st.markdown(
        """
        <style>
        [data-testid="stSidebar"], [data-testid="stSidebarNav"] { display:none !important; }
        [data-testid="stHeader"] { background: transparent; }
        .login-card {
            max-width: 420px; margin: 9vh auto 0; padding: 40px 38px 34px;
            background:#fff; border:1px solid #e5e7eb; border-radius:16px;
            box-shadow:0 8px 30px rgba(20,30,60,.06); text-align:center;
            font-family:'Pretendard','Malgun Gothic',sans-serif;
        }
        .login-card .logo { font-size:44px; }
        .login-card h1 { font-size:1.35rem; font-weight:800; color:#1a1a2e; margin:10px 0 4px; }
        .login-card p  { color:#64748b; font-size:.92rem; line-height:1.6; margin:0 0 6px; }
        </style>
        <div class="login-card">
          <div class="logo">📊</div>
          <h1>CMS 매출 대시보드</h1>
          <p>허용된 <b>Google 계정</b>만 접근할 수 있어요.<br>회사 계정으로 로그인해 주세요.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )
    _, mid, _ = st.columns([1, 1.2, 1])
    with mid:
        st.button(
            "  Google 계정으로 로그인  ",
            type="primary",
            use_container_width=True,
            on_click=st.login,
            args=["google"],
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


def render_account_bar() -> None:
    """우측 상단 고정 영역: 현재 계정 + 로그아웃 (사이드바 박스 대신).
    로그아웃은 Streamlit 기본 경로(/auth/logout) 링크로 처리."""
    email = (st.user.email or "").strip().lower()
    role = " · 소유자" if is_owner(email) else ""
    st.markdown(
        f"""
        <style>
        .account-bar {{
            position: fixed; top: 11px; right: 260px; z-index: 999992;
            display: flex; align-items: center; gap: 8px;
            font-family: 'Pretendard','Malgun Gothic',sans-serif;
        }}
        .account-bar .who {{
            font-size: .82rem; font-weight: 600; color: #45456a;
            background: #f4f6fb; border: 1px solid #e6eaf2;
            border-radius: 8px; padding: 4px 11px; white-space: nowrap;
        }}
        .account-bar .who b {{ color:#1a1a2e; font-weight:800; }}
        .account-bar a.logout {{
            font-size: .82rem; font-weight: 700; color: #e03131; text-decoration: none;
            background: #fff; border: 1px solid #f0c2c2; border-radius: 8px; padding: 4px 12px;
            white-space: nowrap; transition: background .12s;
        }}
        .account-bar a.logout:hover {{ background:#fff5f5; }}
        /* 모바일 좁은 화면에선 이메일 숨기고 로그아웃만 */
        @media (max-width: 640px) {{ .account-bar .who {{ display:none; }} }}
        </style>
        <div class="account-bar">
          <span class="who">👤 <b>{email}</b>{role}</span>
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
