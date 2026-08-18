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

import dev_mode

BASE_DIR           = Path(__file__).parent
# ★개발 서버는 계정·접속로그를 따로 쓴다. 실서비스 권한을 테스트로 건드리면 안 된다.
ALLOWED_USERS_PATH = BASE_DIR / ("allowed-users.dev.json" if dev_mode.IS_DEV
                                 else "allowed-users.json")
ACCESS_LOG_PATH    = BASE_DIR / "logs" / ("dev_access.log" if dev_mode.IS_DEV
                                          else "dashboard_access.log")

# 소유자 — 전체 권한 + 계정 승인 권한 (deploy-checker ALLOWED_EMAILS 와 동일)
OWNER_EMAILS = {"ansqo34@seobuk.kr", "kyung@seobuk.kr", "cbi9406@seobuk.kr"}
# 개발 서버의 **기본 신분만** 소유자로 넣는다 — 안 그러면 dev 를 띄우자마자
# 가입 신청 화면이 떠서 아무것도 못 본다. 신분을 직접 지정해 띄우면
# (`python run_dashboard_dev.py viewer@…`) 그 계정의 진짜 역할을 그대로 타므로
# 뷰어·에디터·미승인 화면도 dev 에서 그대로 시험할 수 있다.
if dev_mode.IS_DEV and dev_mode.DEV_EMAIL == "dev@local":
    OWNER_EMAILS = OWNER_EMAILS | {dev_mode.DEV_EMAIL}

# ── 2단계 가입 승인 (2026-08-07) ─────────────────────────────────
# 신규 가입 → 1차 승인 → 2차 승인 → 접속 가능. **둘 다 끝나야** 열린다.
# 값은 config.json 의 approval 섹션으로 덮어쓸 수 있다(코드 수정 없이 담당자 교체).
#   "approval": {"stage1": "...", "stage2": "...", "dashboard_url": "https://..."}
_APPROVAL_DEFAULT = {"stage1": "kyung@seobuk.kr",    # 1차 · 유경민
                     "stage2": "cbi9406@seobuk.kr"}  # 2차 · 최병인


def _dashboard_url() -> str:
    """대시보드 접속 주소. **로그인 리다이렉트 주소에서 끌어온다.**

    ★2026-08-18 까지 승인 메일에 링크가 없었다 — config 에 `approval.dashboard_url`
      을 적게 해 뒀는데 아무도 안 적어서 계속 빈 값이었다. 주소를 두 군데 적게 하면
      언젠가 어긋나기도 한다(ngrok 도메인이 바뀌면 메일만 옛 주소를 가리킨다).
    ★리다이렉트 주소는 **틀리면 로그인 자체가 안 되므로** 항상 맞다. 그걸 쓴다.
      config 에 값을 넣으면 그게 우선한다(다른 주소로 안내해야 할 때를 위해).
    """
    try:
        uri = str(st.secrets["auth"]["redirect_uri"]).strip()
    except Exception:
        return ""
    return uri[:-len("/oauth2callback")] if uri.endswith("/oauth2callback") else uri


def approval_cfg() -> dict:
    v = dict(_APPROVAL_DEFAULT)
    v["dashboard_url"] = _dashboard_url()
    try:
        c = json.loads((BASE_DIR / "config.json").read_text(encoding="utf-8"))
        v.update({k: s for k, s in (c.get("approval") or {}).items() if s})
    except Exception:
        pass
    return v


def approver_of(stage: int) -> str:
    return (approval_cfg().get(f"stage{stage}") or "").strip().lower()


def can_approve(email: str | None, stage: int) -> bool:
    """그 단계를 누를 수 있는 사람인가. 소유자는 두 단계 다 가능(비상시 대행)."""
    e = (email or "").strip().lower()
    return bool(e) and (e == approver_of(stage) or is_owner(e))
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
    # 2단계 승인(2026-08-07) — 1차를 통과했지만 2차가 남은 계정.
    # {"email": {"role": "viewer", "by": "1차승인자", "at": "..."}}
    # ★stage1 에 있어도 can_access 는 여전히 False 다. 둘 다 끝나야 approved 로 간다.
    st1 = {}
    for e, meta in (v.get("stage1") or {}).items():
        e2 = str(e).strip().lower()
        if not e2 or e2 in ap:
            continue
        meta = meta if isinstance(meta, dict) else {}
        st1[e2] = {"role": meta.get("role") if meta.get("role") in ROLES else "viewer",
                   "by": str(meta.get("by") or ""), "at": str(meta.get("at") or "")}
    pend = [e for e in pend if e not in st1]      # 같은 계정이 두 줄에 걸치지 않게

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

    # 신청서(2026-08-12) — 본인이 고른 소속 팀·메모. {"email": {"team","note","at"}}
    # ★'신청'이지 '배정'이 아니다. 최종 승인 때 승인자가 확인하고 그대로 넣어 준다.
    #   자동으로 넣어 버리면 아무나 페이지가 제일 넓은 팀을 골라서 들어온다.
    #   대기·1차통과 목록에 없는 사람의 신청서는 버린다(거절·승인 뒤 찌꺼기).
    _live = set(pend) | set(st1)
    pmeta = {}
    for e, meta in (v.get("pending_meta") or {}).items():
        e2 = str(e).strip().lower()
        if e2 not in _live:
            continue
        meta = meta if isinstance(meta, dict) else {}
        t2 = str(meta.get("team") or "").strip()
        pmeta[e2] = {"team": t2 if t2 in teams else "",
                     "note": str(meta.get("note") or "")[:200],
                     "at": str(meta.get("at") or "")}

    # 팀장(2026-08-12) — **데이터 내려받기**를 할 수 있는 사람. 역할·팀과 별개 축이다.
    # 승인 계정만 남긴다(해제된 계정이 목록에 남아 유령 권한이 되면 안 된다).
    leads = sorted({str(e).strip().lower() for e in (v.get("leaders") or [])
                    if str(e).strip().lower() in ap})

    return {"approved": ap, "pending": pend, "stage1": st1,
            "teams": teams, "member_team": memb, "pending_meta": pmeta,
            "leaders": leads}


def _load_users() -> dict:
    try:
        return _normalize_users(json.loads(ALLOWED_USERS_PATH.read_text(encoding="utf-8")))
    except FileNotFoundError:
        return {"approved": {}, "pending": [], "stage1": {}, "teams": {},
                "member_team": {}, "pending_meta": {}, "leaders": []}
    except Exception:
        # 파싱 실패(쓰기 도중 등). 원자적 저장으로 거의 없지만, 만약 발생하면 짧게 재시도해
        # 반쯤 쓰인 파일 때문에 승인된 사용자가 '승인 대기'로 튕기는 사고를 막는다.
        for _ in range(3):
            time.sleep(0.05)
            try:
                return _normalize_users(json.loads(ALLOWED_USERS_PATH.read_text(encoding="utf-8")))
            except Exception:
                continue
        # ★위 FileNotFoundError 분기와 **같은 7키**여야 한다. teams/member_team 을
        #   빼면 list_teams()·allowed_pages() 가 u["teams"] 로 직접 인덱싱하다
        #   KeyError → 로그인 전원이 에러 화면이 된다(2026-07-31 확인).
        return {"approved": {}, "pending": [], "stage1": {}, "teams": {},
                "member_team": {}, "pending_meta": {}, "leaders": []}


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


def is_leader(email: str | None) -> bool:
    """팀장으로 지정된 계정인가. 소유자는 항상 참."""
    if not email:
        return False
    e = email.strip().lower()
    return is_owner(e) or e in _load_users()["leaders"]


def can_download(email: str | None) -> bool:
    """**데이터 내려받기** 권한 — 소유자 · 팀장 · 에디터.

    ★2026-08-12 신설(상급자 요청: "팀장까지만"). 에디터를 같이 넣은 이유 —
      정산 담당이 IP정산서 PDF 를 못 받으면 정산 업무가 멈춘다. 에디터는 이미
      승인자가 콕 집어 준 소수라 '아무나'가 아니다.
    ※역할·팀과 **별개 축**이다. 팀장이라고 편집이 되지도, 메뉴가 늘지도 않는다.
    """
    return is_leader(email) or can_edit(email)


def set_leader(email: str, on: bool) -> None:
    e = email.strip().lower()

    def _fn(u):
        ls = [x for x in u.get("leaders", []) if x != e]
        if on:
            ls.append(e)
        u["leaders"] = sorted(ls)

    _mutate_users(_fn)


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


def _notify(to: str, subject: str, lines: list[str], cta: str = "approve") -> None:
    """승인 흐름 알림 메일. **실패해도 절대 화면을 죽이지 않는다.**

    ★로그인 도중에 불리므로 별도 스레드로 보낸다. Gmail SMTP 가 1~2초 걸리는데
      그동안 로그인 화면이 멈춰 있으면 '느린 대시보드'가 된다.
    ★메일 안에서 바로 승인시키지 않는다(사용자 결정). 링크는 대시보드로만 보내고
      승인은 구글 로그인을 거친 화면에서 누른다 — 메일이 전달되거나 새도 남이
      대신 누를 수 없다.
    """
    to = (to or "").strip()
    if not to:
        return

    def _run():
        try:
            import settlement_mail as sm
            from email.message import EmailMessage
            ok, sender = sm.config_ready()
            if not ok:
                return
            url = (approval_cfg().get("dashboard_url") or "").strip()
            body = [ln for ln in lines]
            body.append("")
            # ★받는 사람에 따라 마지막 줄이 달라야 한다. 예전엔 전부 '승인하러 가기'
            #   였는데, **승인 완료 메일을 받는 신청자에게도** 그게 붙어서
            #   "접속·계정 관리에서 처리해 주세요" 라는, 그 사람은 못 하는 안내가 갔다.
            if cta == "open":                       # 신청자에게 가는 승인 완료 메일
                body.append(f"들어가기: {url}" if url
                            else "대시보드 주소로 접속해 구글 계정으로 로그인해 주세요.")
            else:                                   # 승인자에게 가는 요청 메일
                body.append(f"승인하러 가기: {url}" if url
                            else "대시보드 → 🔐 접속·계정 관리 → 계정 승인 에서 처리해 주세요.")
            body.append("")
            body.append("— CMS 매출 대시보드")
            msg = EmailMessage()
            msg["Subject"] = subject
            msg["From"] = f"CMS 매출 대시보드 <{sender}>"
            msg["To"] = to
            msg.set_content("\n".join(body))
            sm.send(msg, [to], [])
        except Exception as ex:                 # 메일 실패로 로그인이 막히면 안 된다
            try:
                _log_access("system", f"mailfail:{subject}:{type(ex).__name__}")
            except Exception:
                pass

    try:
        import threading
        threading.Thread(target=_run, daemon=True).start()
    except Exception:
        pass


def _add_pending(email: str, team: str = "", note: str = "") -> None:
    """가입 신청 접수. ★로그인만으로는 안 올라간다 — 본인이 '신청하기' 를 눌러야
    한다(2026-08-12). 예전엔 로그인하는 순간 자동 접수돼서, 승인자는 이 사람이
    누구고 어느 팀인지 이메일만 보고 판단해야 했다."""
    e = email.strip().lower()
    t = (team or "").strip()
    n = (note or "").strip()[:200]
    _added = False

    def _fn(u):
        nonlocal _added
        if e in u["approved"] or e in u["pending"] or e in u.get("stage1", {}):
            return
        u["pending"].append(e)
        u.setdefault("pending_meta", {})[e] = {
            "team": t, "note": n,
            "at": datetime.datetime.now().isoformat(timespec="seconds")}
        _added = True

    _mutate_users(_fn)
    # ★메일은 **행동이 필요한 사람 한 명에게만** 간다. 단계마다 전원에게 뿌리는 게
    #   아니라, 지금 눌러야 할 사람에게만 한 통이다(1차 → 2차 → 신청자).
    #   이게 없으면 신청이 들어와도 아무도 모른 채 묻힌다.
    if _added:                       # 같은 사람이 새로고침할 때마다 메일이 가면 안 된다
        _notify(approver_of(1), f"[대시보드] 새 가입 요청 — {e}",
                [f"{e} 님이 대시보드 접속을 요청했어요.",
                 f"신청한 소속 팀: {t or '(안 고름)'}",
                 f"메모: {n}" if n else "",
                 "",
                 "1차 승인이 필요합니다. 승인하시면 2차 승인자에게 자동으로 넘어가요.",
                 "둘 다 승인해야 접속할 수 있어요.",
                 "※ 팀은 신청한 값이에요. 최종 승인 때 그대로 배정되니 확인해 주세요."])


def requested_team(email: str) -> str:
    """그 사람이 신청서에 고른 팀(없으면 '')."""
    return (_load_users().get("pending_meta", {}).get((email or "").strip().lower(), {})
            .get("team") or "")


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
    """미승인 계정 화면. **신청 전**이면 신청서, **신청 후**면 대기 안내."""
    _u = _load_users()
    _e = email.strip().lower()
    _waiting = _e in _u["pending"] or _e in _u.get("stage1", {})
    if _waiting:
        _ico, _hd = "🔒", "승인 대기 중"
        _t = _u.get("pending_meta", {}).get(_e, {}).get("team") or ""
        _msg = ("승인 요청이 접수됐어요. 담당자에게 메일이 갔어요.<br>"
                + (f"신청한 소속 팀 — <b>{_t}</b><br>" if _t else "")
                + ("<b>2차 승인</b>만 남았어요.<br>" if _e in _u.get("stage1", {})
                   else "승인은 <b>2단계</b>로 진행돼요 — 1차·2차가 모두 승인하면 바로 쓸 수 있어요.<br>")
                + "완료되면 이 메일 주소로 알려드려요.")
    else:
        _ico, _hd = "📝", "접속 신청"
        _msg = ("아직 신청하지 않은 계정이에요.<br>"
                "소속 팀을 고르고 <b>신청하기</b>를 눌러 주세요.<br>"
                "승인은 <b>2단계</b>로 진행되고, 끝나면 이 메일 주소로 알려드려요.")
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
          <div class="lock">{_ico}</div>
          <h2>{_hd}</h2>
          <p><b>{email}</b><br>{_msg}</p>
        </div>
        """,
        unsafe_allow_html=True,
    )
    _, mid, _ = st.columns([1, 1.2, 1])
    with mid:
        if not _waiting:
            # ── 신청서 ──────────────────────────────────────────────
            # 팀을 본인이 고른다. 승인자가 이메일만 보고 짐작하지 않아도 된다.
            _teams = sorted(list_teams())
            _pick = ""
            if _teams:
                _pick = st.selectbox("소속 팀", _teams, key="_req_team",
                                     index=None, placeholder="소속 팀을 골라 주세요")
            else:
                st.caption("아직 등록된 팀이 없어요. 그냥 신청하면 관리자가 배정해요.")
            _note = st.text_input("메모 (선택)", key="_req_note", max_chars=200,
                                  placeholder="예: 정산 업무로 IP정산서가 필요해요")
            if st.button("신청하기", use_container_width=True, type="primary",
                         disabled=bool(_teams) and not _pick):
                _add_pending(email, _pick or "", _note)
                _log_access(email, f"request:{_pick or '팀없음'}")
                st.rerun()
            if _teams and not _pick:
                st.caption("소속 팀을 골라야 신청할 수 있어요.")
        st.button("다른 계정으로 로그인", use_container_width=True, on_click=st.logout)


# ── 라우터 진입점 ─────────────────────────────────────────────────
class _DevUser(dict):
    """dev 전용 가짜 신분. `st.user` 를 이걸로 갈아끼운다.

    ★페이지 6곳이 `st.user.email` 을 직접 읽는다. 그 자리를 다 고치는 대신
      `st.user` 하나만 바꾸면 **권한 로직은 진짜 그대로 돈다** — 신분만 고정이다.
      역할·팀·페이지 권한은 allowed-users.dev.json 을 그대로 타므로,
      '뷰어에게 이게 보이나' 같은 것도 그 파일만 고쳐 시험할 수 있다.
    """
    is_logged_in = True

    def __init__(self, email: str):
        super().__init__(email=email, name="개발", given_name="개발")

    def __getattr__(self, k):
        return self.get(k)


def require_login() -> str:
    """라우터 최상단에서 호출. 통과 못 하면 화면 렌더 후 st.stop()."""
    # ★개발 서버는 구글 로그인을 못 한다(OIDC 리디렉션이 실서버 도메인에 묶여 있다).
    #   가짜 신분을 끼우고 넘어간다. **그래서 dev 는 127.0.0.1 에만 띄운다** —
    #   외부에 열리면 인증 없이 매출이 보인다.
    if dev_mode.IS_DEV and not isinstance(getattr(st, "user", None), _DevUser):
        dev_mode.seed()                     # dev 저장소가 비어 있으면 한 번 채운다
        st.user = _DevUser(dev_mode.DEV_EMAIL)

    if not getattr(st, "user", None) or not st.user.is_logged_in:
        _render_login_page()
        st.stop()

    # 2시간 경과 세션은 강제 로그아웃
    _enforce_session_timeout()

    email = (st.user.email or "").strip().lower()
    if not can_access(email):
        # ★자동 접수하지 않는다(2026-08-12) — 본인이 팀을 고르고 '신청하기' 를
        #   눌러야 대기 목록에 올라간다. 로그인만 한 사람은 신청서 화면을 본다.
        if not st.session_state.get("_pending_logged"):
            _log_access(email, "pending")
            st.session_state["_pending_logged"] = True
        _render_pending_page(email)
        st.stop()

    if not st.session_state.get("_access_logged"):
        _log_access(email, "login")
        st.session_state["_access_logged"] = True
    return email


_BOOT_AT = datetime.datetime.now().strftime("%m-%d %H:%M")


def _code_stamp() -> str:
    """이 프로세스가 물고 있는 코드가 어느 것인지 — 기동 시각 + 커밋 7자리.

    ★2026-08-13 여기서 한 시간을 태웠다. 정산서를 고쳐 놓고도 화면이 그대로라
      '서버가 옛 코드를 물고 있나'를 **확인할 방법이 없어** 추측으로 재시작했다.
      (재시작하니 풀렸지만 캐시·세션도 같이 지워져서 원인은 끝내 못 갈랐다.)
      한 줄 찍어 두면 다음엔 눈으로 바로 갈린다.
    ★subprocess 대신 .git 을 직접 읽는다 — 매 rerun 마다 git 을 띄울 순 없고,
      모듈 임포트는 프로세스당 한 번이라 '지금 도는 코드'를 정확히 가리킨다.
    """
    try:
        head = (BASE_DIR / ".git" / "HEAD").read_text(encoding="utf-8").strip()
        if head.startswith("ref:"):
            head = (BASE_DIR / ".git" / head.split(" ", 1)[1].strip()
                    ).read_text(encoding="utf-8").strip()
        return f"기동 {_BOOT_AT} · {head[:7]}"
    except Exception:                                   # noqa: BLE001
        return f"기동 {_BOOT_AT}"


def render_sidebar_account() -> None:
    """사이드바 좌하단 고정: 현재 계정(아바타·이메일·권한) + 로그아웃.
    st.sidebar 안에 그려서 사이드바를 접으면 함께 사라지고 너비도 사이드바에 맞춰진다.
    로그아웃은 Streamlit 기본 경로(/auth/logout) 링크로 처리."""
    email = (st.user.email or "").strip().lower()
    _RL = {"owner": "소유자", "editor": "에디터", "viewer": "뷰어"}
    role = _RL.get(get_role(email), "승인 계정")
    initial = (email[:1] or "?").upper()
    # 운영 정보라 소유자에게만 보인다 — 담당자 화면에 버전 문자열이 뜰 이유가 없다.
    _stamp = f" · {_code_stamp()}" if is_owner(email) else ""
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
            <div class="rl">{role}{_stamp}</div>
          </div>
          <a class="logout" href="/auth/logout" target="_self">로그아웃</a>
        </div>
        """,
        unsafe_allow_html=True,
    )


# ── 접속 로그 ──────────────────────────────────────────────────────
_EVENT_LABEL = {
    "login":   "✅ 로그인",
    # 로그인은 했지만 아직 신청서를 안 낸 상태. 신청은 아래 'request:' 로 따로 남는다.
    "pending": "👀 미승인 접속 시도",
}


def _pretty_event(ev: str) -> str:
    if ev in _EVENT_LABEL:
        return _EVENT_LABEL[ev]
    if ev.startswith("request:"):
        return "⏳ 가입 신청 · 팀 " + ev.split(":", 1)[1]
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
    if ev.startswith("leader:"):
        _w, _, _v = ev.split(":", 1)[1].rpartition("=")
        return f"📥 팀장(다운로드) {'부여' if _v == 'on' else '회수'} → {_w}"
    if ev.startswith("teamset:"):
        return "🗂 팀권한 변경 → " + ev.split(":", 1)[1]
    if ev.startswith("teamdel:"):
        return "🗑 팀삭제 → " + ev.split(":", 1)[1]
    if ev.startswith("view:"):
        k = ev.split(":", 1)[1]
        return "👁 열람 → " + pages_registry.PAGE_TITLE.get(k, k)
    # 데이터 반출은 열람과 성격이 달라 눈에 띄게 둔다.
    if ev.startswith("download:"):
        _, _, rest = ev.partition(":")
        pk, _, what = rest.partition(":")
        return f"📥 다운로드 [{pages_registry.PAGE_TITLE.get(pk, pk)}] {what}"
    # 정산 매핑은 금액에 직접 영향을 주므로 누가 무엇을 확정했는지 남긴다.
    if ev.startswith("settlemap:"):
        return "🧾 정산 매핑 → " + ev.split(":", 1)[1]
    if ev.startswith("settleskip:"):
        return "⛔ 정산 제외 → " + ev.split(":", 1)[1]
    return ev


def log_event(email: str, event: str) -> None:
    """다른 모듈에서 활동 로그를 남길 때 쓰는 공개 진입점."""
    _log_access(email, event)


# ── 다운로드 기록 ──────────────────────────────────────────────────
# 왜 필요한가(2026-08-05): 외부인은 로그인 게이트에서 막히지만, **승인된 계정이
# 통째로 받아가는 것**은 아무 흔적이 안 남았다. 화면에 다운로드 버튼이 10개고
# 그중엔 기간 전체를 내려주는 것도 있는데, 로그에는 login·view 밖에 없었다.
# 막는 대신 **보이게** 만든다 — 정상 업무 다운로드를 막으면 일이 안 돌아가지만,
# 기록이 남으면 이상 징후는 사후에라도 잡힌다.
def _current_email() -> str:
    """현재 세션 계정. 로그인 전이면 ''."""
    try:
        return (st.user.email or "").strip().lower() if getattr(st, "user", None) else ""
    except Exception:
        return ""


def log_download(page: str, name: str, rows=None, nbytes=None) -> None:
    """다운로드 1건 기록. 계정은 현재 세션에서 알아서 읽는다."""
    email = _current_email()
    tail = f"·{int(rows):,}행" if rows not in (None, "") else ""
    if nbytes:
        # 1KB 미만이 '0KB' 로 찍히면 안 받은 것처럼 보인다 → 그땐 바이트로.
        tail += (f"·{int(nbytes) // 1024:,}KB" if nbytes >= 1024
                 else f"·{int(nbytes):,}B")
    _log_access(email, f"download:{page}:{name}{tail}")


def download_button(label, data, file_name=None, mime=None, *,
                    page: str = "", rows=None, container=None, **kw):
    """기록을 남기는 st.download_button. 인자는 그대로 통과시킨다.

    container 로 st.sidebar 나 컬럼을 넘길 수 있다 — 안 넘기면 현재 위치에 그린다.
    ★on_click 을 이미 넘긴 호출부가 있으면 덮지 않고 **둘 다** 부른다.
      (지금은 없지만, 나중에 생겼을 때 조용히 기록이 끊기면 못 알아챈다.)

    ★권한 게이트(2026-08-12) — 여기 한 곳만 막으면 화면의 모든 다운로드에 걸린다.
      **숨기지 않고 비활성**으로 둔다. 버튼이 사라지면 "고장났다"고 문의가 오고,
      왜 못 받는지도 알 수 없다. gate=False 를 주면 이 검사를 건너뛴다.
    """
    if kw.pop("gate", True) and not can_download(_current_email()):
        kw["disabled"] = True
        kw.setdefault("help", "데이터 내려받기는 팀장 권한이 있어야 해요. "
                              "필요하면 관리자에게 요청해 주세요.")
    try:
        nbytes = len(data) if isinstance(data, (bytes, bytearray, str)) else None
    except Exception:
        nbytes = None
    _name = file_name or label
    _prev = kw.pop("on_click", None)

    def _cb(*a, **k):
        log_download(page, _name, rows, nbytes)
        if callable(_prev):
            _prev(*a, **k)

    tgt = container if container is not None else st
    return tgt.download_button(label, data, file_name, mime, on_click=_cb, **kw)


def read_access_log(limit: int = 1000) -> list[dict]:
    """접속 로그를 최신순으로 파싱해 반환.

    ★파일 전체를 read_text 로 읽던 걸 **끝부분만** 읽도록 바꿨다(2026-08-03).
      로그는 무한 append 라, 관리 콘솔을 열 때마다 커진 파일을 통째로 올리면
      페이지가 점점 느려진다. 어차피 최신 limit 줄만 쓴다.
    """
    try:
        size = ACCESS_LOG_PATH.stat().st_size
        # 한 줄이 100바이트 안팎이라 넉넉히 300바이트로 잡고 끝에서만 읽는다.
        want = min(size, max(limit, 1) * 300)
        with ACCESS_LOG_PATH.open("rb") as f:
            f.seek(size - want)
            chunk = f.read()
        if want < size:
            chunk = chunk.split(b"\n", 1)[-1]      # 잘린 첫 줄은 버린다
        lines = chunk.decode("utf-8", errors="replace").splitlines()
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
        _cfg = approval_cfg()
        _can1, _can2 = can_approve(email, 1), can_approve(email, 2)
        st.caption(f"가입 승인은 **2단계**예요 — 1차 {_cfg.get('stage1', '(미지정)')} → "
                   f"2차 {_cfg.get('stage2', '(미지정)')}. 둘 다 승인해야 접속할 수 있어요. "
                   "메일은 **지금 눌러야 할 사람에게만** 한 통씩 가요"
                   "(신청 → 1차 · 1차통과 → 2차 · 최종 → 신청자).")

        # ── 1차 대기 ──
        with st.container(border=True):
            st.markdown(f"**1차 승인 대기**  ({len(u['pending'])}건)")
            if u["pending"]:
                for e in u["pending"]:
                    c1, c2, c3, c4 = st.columns([3.4, 1.7, 1, 1])
                    c1.write(e)
                    _rq = u.get("pending_meta", {}).get(e, {})
                    c1.caption("신청 팀 **" + (_rq.get("team") or "(안 고름)") + "**"
                               + (" · " + _rq.get("note") if _rq.get("note") else ""))
                    _r = c2.selectbox("역할", list(ROLES), key=f"aprole_{e}",
                                      format_func=lambda x: _ROLE_LABEL.get(x, x),
                                      label_visibility="collapsed")
                    if c3.button("1차 승인", key=f"ap1_{e}", type="primary",
                                 disabled=not _can1,
                                 help=None if _can1 else "1차 승인자만 누를 수 있어요."):
                        _approve_stage1(e, _r, email)
                        _log_access(email, f"approve1:{e}={_r}"); st.rerun()
                    if c4.button("거절", key=f"rj_{e}", disabled=not (_can1 or _can2)):
                        _reject(e); _log_access(email, f"reject:{e}"); st.rerun()
            else:
                st.caption("1차 대기 중인 계정이 없어요.")

        # ── 2차 대기 ──
        _s1 = u.get("stage1", {})
        with st.container(border=True):
            st.markdown(f"**2차 승인 대기**  ({len(_s1)}건)")
            if _s1:
                _tall = ["(팀 없음)"] + sorted(u["teams"])
                for e, meta in _s1.items():
                    c1, c2, ct, c3, c4 = st.columns([2.8, 1.4, 1.6, 1, 1])
                    c1.write(e)
                    c1.caption(f"1차 {meta.get('by', '')} · {meta.get('at', '')[:16].replace('T', ' ')}")
                    _rq2 = u.get("pending_meta", {}).get(e, {})
                    if _rq2.get("note"):
                        c1.caption("메모: " + _rq2["note"])
                    _r2 = c2.selectbox("역할", list(ROLES), key=f"ap2role_{e}",
                                       index=list(ROLES).index(meta.get("role", "viewer")),
                                       format_func=lambda x: _ROLE_LABEL.get(x, x),
                                       label_visibility="collapsed")
                    # 신청한 팀을 기본값으로 — 승인자는 확인만 하면 되고, 바꿀 수도 있다
                    _rt = _rq2.get("team") or ""
                    _t2 = ct.selectbox("팀", _tall, key=f"ap2team_{e}",
                                       index=_tall.index(_rt) if _rt in _tall else 0,
                                       label_visibility="collapsed")
                    if c3.button("최종 승인", key=f"ap2_{e}", type="primary",
                                 disabled=not _can2,
                                 help=None if _can2 else "2차 승인자만 누를 수 있어요."):
                        _tv = "" if _t2 == "(팀 없음)" else _t2
                        _approve(e, _r2, _tv)
                        _log_access(email, f"approve2:{e}={_r2}@{_tv or '팀없음'}"); st.rerun()
                    if c4.button("거절", key=f"rj2_{e}", disabled=not (_can1 or _can2)):
                        _reject(e); _log_access(email, f"reject2:{e}"); st.rerun()
            else:
                st.caption("2차 대기 중인 계정이 없어요.")

        with st.container(border=True):
            st.markdown(f"**승인된 계정**  ({len(u['approved'])}명)")
            if u["approved"]:
                _tnames = ["(팀 없음)"] + sorted(u["teams"])
                _leads = set(u["leaders"])
                for e, r in u["approved"].items():
                    c1, c2, cl, c3, c4 = st.columns([2.9, 1.8, 1.1, 0.9, 0.8])
                    c1.write(e)
                    _cur = u["member_team"].get(e) or "(팀 없음)"
                    _nt = c2.selectbox("팀", _tnames,
                                       index=_tnames.index(_cur) if _cur in _tnames else 0,
                                       key=f"team_{e}", label_visibility="collapsed")
                    # 팀장 = 데이터 내려받기 권한. 역할·팀과 별개 축이라 따로 둔다.
                    _ld = cl.checkbox("📥 팀장", value=e in _leads, key=f"lead_{e}",
                                      help="켜면 이 계정이 데이터를 내려받을 수 있어요.")
                    if _ld != (e in _leads):
                        set_leader(e, _ld)
                        _log_access(email, f"leader:{e}={'on' if _ld else 'off'}"); st.rerun()
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
                       "팀이 없으면 기본 페이지(KPI·스내피즘·포토이즘·주간리포트)를 봐요.  "
                       "· **📥 팀장** 을 켜야 데이터를 내려받을 수 있어요"
                       "(소유자·에디터는 켜지 않아도 받을 수 있어요).")

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
                # ★사이드바에서 감춘 페이지는 체크박스에도 안 띄운다 — 켜 봐야 안 열려
                #   혼란만 준다. 다만 **저장된 권한은 건드리지 않는다**(_hidden 으로
                #   그대로 되돌려 넣는다). 나중에 감춤을 풀면 예전 설정이 살아난다.
                _hidden = [k for k in cfg["pages"] if k in pages_registry.HIDDEN]
                _sel = []
                _keys = [p[0] for p in pages_registry.VISIBLE_PAGES]
                cols = st.columns(4)
                for i, k in enumerate(_keys):
                    lbl = f"{pages_registry.PAGE_ICON[k]} {pages_registry.PAGE_TITLE[k]}"
                    if cols[i % 4].checkbox(lbl, value=(k in cfg["pages"]), key=f"tp_{tname}_{k}"):
                        _sel.append(k)
                # 감춘 것까지 합친 뒤 **목록 순서로 정렬**한다 — 안 그러면 저장값과
                # 순서만 달라 '저장' 버튼이 늘 켜져 있다.
                _sel = [k for k in pages_registry.PAGE_KEYS if k in set(_sel + _hidden)]
                b1, b2, _ = st.columns([1, 1, 3])
                if b1.button("저장", key=f"tsave_{tname}", type="primary",
                             disabled=(_sel == [k for k in pages_registry.PAGE_KEYS
                                                if k in cfg["pages"]])):
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
            _dl = [r for r in rows if "다운로드" in r["이벤트"]]
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("총 기록", f"{len(rows):,}")
            c2.metric("로그인", f"{sum(1 for r in rows if '로그인' in r['이벤트']):,}")
            c3.metric("페이지 열람", f"{sum(1 for r in rows if '열람' in r['이벤트']):,}")
            # 데이터 반출은 따로 세운다 — 관리 활동에 섞이면 눈에 안 띈다.
            c4.metric("다운로드", f"{len(_dl):,}",
                      help="누가 어떤 자료를 내려받았는지예요. 평소보다 많으면 살펴봐 주세요.")
            _kinds = ["전체", "📥 다운로드", "페이지 열람", "로그인", "관리 활동"]
            _k = st.radio("종류", _kinds, horizontal=True, key="logkind",
                          label_visibility="collapsed")
            if _k == "📥 다운로드":
                rows = _dl
                if _dl:
                    _by = {}
                    for r in _dl:
                        _by[r["계정"]] = _by.get(r["계정"], 0) + 1
                    _top = sorted(_by.items(), key=lambda x: -x[1])[:5]
                    st.caption("계정별 · " + " / ".join(f"{e} {n}건" for e, n in _top))
            elif _k == "페이지 열람":
                rows = [r for r in rows if "열람" in r["이벤트"]]
            elif _k == "로그인":
                rows = [r for r in rows if any(x in r["이벤트"] for x in
                                               ("로그인", "가입 신청", "미승인 접속"))]
            elif _k == "관리 활동":
                rows = [r for r in rows if any(x in r["이벤트"] for x in ("승인", "거절", "해제", "역할", "팀"))]
            if rows:
                st.dataframe(rows, use_container_width=True, hide_index=True, height=460)
            else:
                st.caption("해당하는 기록이 없어요.")


def _approve_stage1(email: str, role: str, by: str) -> None:
    """1차 승인 — pending 에서 빼서 stage1 로. **아직 접속은 안 된다.**"""
    e = email.strip().lower()
    role = role if role in ROLES else "viewer"
    now = datetime.datetime.now().isoformat(timespec="seconds")

    def _fn(u):
        u["pending"] = [x for x in u["pending"] if x != e]
        u.setdefault("stage1", {})[e] = {"role": role, "by": by, "at": now}

    _mutate_users(_fn)
    _notify(approver_of(2), f"[대시보드] 2차 승인 요청 — {e}",
            [f"{e} 님의 가입 요청이 1차 승인을 통과했어요.",
             f"1차 승인: {by}",
             f"부여 예정 권한: {'편집' if role == 'editor' else '열람'}",
             "",
             "2차 승인을 하면 그때부터 접속할 수 있어요."])


def _approve(email: str, role: str = "viewer", team: str | None = None) -> None:
    """최종(2차) 승인 — 여기서 비로소 접속이 열린다.

    ★신청서에 고른 팀을 여기서 배정한다. member_team 은 **승인 계정만** 남기므로
      (정규화 규칙) approved 에 넣기 전에 쓰면 조용히 지워진다 — 같은 _fn 안에서
      순서대로 넣는다.
    """
    e = email.strip().lower()
    role = role if role in ROLES else "viewer"
    _t = (team if team is not None else requested_team(e) or "").strip()
    _set = ""

    def _fn(u):
        nonlocal _set
        u["pending"] = [x for x in u["pending"] if x != e]
        u.get("stage1", {}).pop(e, None)
        u.get("pending_meta", {}).pop(e, None)
        u["approved"][e] = role
        if _t and _t in u.get("teams", {}):
            u.setdefault("member_team", {})[e] = _t
            _set = _t

    _mutate_users(_fn)
    _notify(e, "[대시보드] 접속이 승인됐어요",
            ["요청하신 CMS 매출 대시보드 접속이 승인됐어요.",
             f"권한: {'편집' if role == 'editor' else '열람'}",
             f"소속 팀: {_set}" if _set else "",
             "",
             "이제 구글 계정으로 로그인하면 바로 쓸 수 있어요."],
            cta="open")     # 신청자에게 가는 메일 — '승인하러 가기' 가 붙으면 안 된다


def _reject(email: str) -> None:
    """거절 — 어느 단계에 있든 목록에서 뺀다."""
    e = email.strip().lower()

    def _fn(u):
        u["pending"] = [x for x in u["pending"] if x != e]
        u.get("stage1", {}).pop(e, None)
        u.get("pending_meta", {}).pop(e, None)   # 신청서도 같이 지운다

    _mutate_users(_fn)


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


def safe_page_link(page_key: str, label: str, icon: str | None = None,
                   denied: str | None = None) -> bool:
    """권한이 있을 때만 다른 페이지로 가는 링크를 건다. 링크를 그렸으면 True.

    ★★2026-08-04 실사용자 장애. `st.page_link` 는 **st.navigation 에 올라간
      페이지만** 가리킬 수 있다. 권한이 없어 목록에서 빠진 페이지를 가리키면
      StreamlitPageNotFoundError 가 나면서 **그 페이지 전체가 죽는다.**
      스내피즘·포토이즘 본문에 '런 비교 페이지 열기' 링크가 박혀 있었는데,
      runs 권한이 없는 계정(기본값이 꺼짐)은 매출 화면이 통째로 안 떴다.
      소유자는 전 페이지가 보이니 **개발 중엔 절대 재현되지 않는다.**

    경로는 pages_registry 에서 가져온다 — 파일명을 바꿔도 여기만 맞으면 된다.
    """
    email = getattr(getattr(st, "user", None), "email", None)
    if not can_view_page(email, page_key):
        if denied:
            st.caption(denied)
        return False
    file = next((p[1] for p in pages_registry.PAGES if p[0] == page_key), None)
    if not file:
        return False
    try:
        st.page_link(file, label=label, icon=icon)
        return True
    except Exception:
        # 멀티페이지 컨텍스트가 없는 경우(미리보기 하네스 등) — 화면을 죽이진 않는다.
        return False


# ══════════════════════════════════════════════════════════════
#  화면 워터마크 (캡처 유출 대비)
# ══════════════════════════════════════════════════════════════
def render_watermark(email: str | None = None) -> None:
    """로그인 계정과 시각을 화면 전체에 옅게 깔아 둔다. 라우터에서 한 번만 부른다.

    ★무엇을 위한 기능인지 분명히 해 둔다 — **유출 방지가 아니라 유출 추적**이다.
      화면에 그린 것은 무엇이든 지울 수 있다. 개발자도구로 노드를 지워도 되고,
      AI 인페인팅으로 지워도 된다(규칙적으로 반복되는 옅은 무늬는 특히 지우기 쉽다).
      그래서 '못 지우게' 만드는 대신 **지우지 않은 캡처가 돌아다닐 때 누구 화면인지
      드러나게** 하는 데 목적을 둔다. 실제 통제는 접근 승인·감사 로그·다운로드
      기록이 하고, 이건 그 위에 얹는 억지력이다.

    ★**여백에만 깐다**(2026-08-07 사용자 결정). 앱 배경에 그리므로 불투명한 흰
      카드가 그 위를 덮고, 카드 사이 여백에서만 보인다. 표·그래프 위에는 안 얹힌다.
      대신 **카드 하나만 잘라 찍은 캡처에는 안 남는다** — 그건 감수하기로 했다.
      데이터 위에 겹치려면 다시 fixed 오버레이(z-index 999998)로 되돌리면 된다.

    타일 원점을 계정마다 다르게 흔든다(아래 _jitter). 지우고 다시 칠한 캡처라도
    남은 격자 간격으로 어느 계정 화면이었는지 좁힐 수 있다.
    """
    import html as _html
    from datetime import datetime

    email = (email or getattr(getattr(st, "user", None), "email", "") or "").strip().lower()
    if not email:
        return
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    label = _html.escape(f"{email} · {stamp}")

    # 계정마다 타일 원점을 다르게 — 같은 화면이라도 격자 위치가 달라진다.
    _jitter = sum(ord(c) for c in email) % 40

    # SVG 한 장을 배경으로 반복시킨다. div 를 수백 개 그리면 스크롤이 무거워진다.
    svg = (
        "<svg xmlns='http://www.w3.org/2000/svg' width='420' height='210'>"
        "<text x='0' y='120' transform='rotate(-24 0 120)' "
        "font-family='Pretendard, Malgun Gothic, sans-serif' font-size='15' "
        "font-weight='600' fill='%231b2330' fill-opacity='0.085'>" + label + "</text>"
        "</svg>"
    )
    src = "data:image/svg+xml;utf8," + svg.replace("#", "%23").replace('"', "'")
    # ★오버레이 div 가 아니라 **앱 배경**에 그린다. 그래야 불투명한 카드가 위를
    #   덮어 여백에서만 보인다. background-color 위에 background-image 가 얹히므로
    #   config.toml 의 배경색(#f4f5f7)과 같이 쓸 수 있다.
    #   attachment:fixed — 스크롤해도 무늬가 제자리에 있어 눈에 덜 거슬린다.
    # ★stMain 에도 같이 깐다 — 컨테이너에만 깔면 그 위의 stMain 이 불투명한 회색
    #   (#f4f5f7)으로 덮어 무늬가 통째로 가려진다. 2026-08-07~11 두 메인 화면에서
    #   실제로 안 보였다(AppTest 는 CSS 를 렌더하지 않아 검증에서 놓쳤다).
    #   덧붙여 화면 쪽 CSS 는 `background:` 축약형을 쓰면 안 된다 —
    #   축약형은 background-image 까지 none 으로 되돌린다(background-color 를 쓸 것).
    st.markdown(
        f"""<style>
        [data-testid="stAppViewContainer"], [data-testid="stMain"] {{
            background-image: url("{src}") !important;
            background-repeat: repeat !important;
            background-attachment: fixed !important;
            background-position: {_jitter}px {_jitter}px !important;
        }}
        /* 사이드바는 흰 패널이라 무늬가 비치면 지저분하다 — 거기선 끈다. */
        [data-testid="stSidebar"] {{ background-image: none !important; }}
        </style>""",
        unsafe_allow_html=True,
    )
