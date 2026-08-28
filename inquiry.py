# -*- coding: utf-8 -*-
"""대시보드 문의 창구 — 접수 → 메일 + 목록.

왜 만들었나 (2026-08-28): 화면에 이상한 숫자가 떠도 **사용자가 알릴 방법이 화면
안에 없었다.** 구두나 메신저로 오면 "어느 화면에서, 어떤 기간으로 보고 있었는지"를
되묻느라 하루가 간다. 그래서 맥락(계정·역할·팀·화면·시각)은 **자동으로 붙이고**
사람은 분류와 내용만 적게 한다.

두 곳에 남긴다 — 하나만으로는 부족하다.
  · 메일   담당자가 바로 알아채라고. 받는 사람은 config.json 의 `inquiry.to`
  · 파일   `data/inquiries.json`. 메일을 지워도 이력이 남고, 관리 화면에서
           처리 상태를 달 수 있다. 메일함은 '무엇이 아직 안 끝났는지'를 못 보여준다.

개발 서버(SNAPISM_ENV=dev)에서 안전한 이유
  · 저장은 JsonStore 가 알아서 `data_dev/` 로 보낸다
  · 메일은 `settlement_mail.send` 가 막는다 — dev 에서 눌러 본 문의가
    진짜 담당자에게 가지 않는다(2026-08-18 에 그 한 곳을 막아 뒀다)
"""
from __future__ import annotations

import datetime
import json
from pathlib import Path

import streamlit as st

import auth
from json_store import JsonStore

BASE_DIR = Path(__file__).parent

# 분류는 **적게** 둔다. 고를 게 많으면 사람들이 아무거나 고르고, 그러면 분류가
# 없느니만 못하다. '무엇을 해 주길 바라는가' 로만 가른다.
CATEGORIES = [
    "🔢 숫자가 이상해요",
    "🐞 화면이 안 되거나 느려요",
    "✨ 이런 기능이 있으면 좋겠어요",
    "❓ 사용법을 모르겠어요",
    "💬 그 밖의 문의",
]

STATUSES = ("열림", "처리중", "완료")
_DEFAULT_TO = ["ansqo34@seobuk.kr"]

_store = JsonStore("inquiries.json", default={"items": []})


# ── 설정 ──────────────────────────────────────────────────────────
def recipients() -> list[str]:
    """문의를 받을 주소. config.json 의 `inquiry.to` 가 있으면 그것,
    없으면 대시보드 담당자에게 간다. 문자열 한 줄로 적어도 되게 쉼표를 푼다."""
    try:
        c = json.loads((BASE_DIR / "config.json").read_text(encoding="utf-8"))
        v = (c.get("inquiry") or {}).get("to")
        if isinstance(v, str):
            v = [x.strip() for x in v.split(",")]
        if v:
            return [x.strip().lower() for x in v if str(x).strip()]
    except Exception:                                   # 설정이 없어도 접수는 돼야 한다
        pass
    return list(_DEFAULT_TO)


# ── 저장소 ────────────────────────────────────────────────────────
def load_all() -> list[dict]:
    """최근 것부터. 파일이 없으면 빈 목록.

    ★시각만으로 정렬하면 안 된다 — `at` 이 초 단위라 같은 초에 들어온 두 건은
      키가 같고, 안정 정렬이라 **접수 순서 그대로(오래된 것부터)** 나온다.
      번호가 일련번호이므로 시각 다음에 번호를 함께 본다.
    """
    items = (_store.load() or {}).get("items") or []
    return sorted(items, key=lambda r: (r.get("at", ""), r.get("id", "")),
                  reverse=True)


def open_count() -> int:
    return sum(1 for r in load_all() if r.get("status") != "완료")


def _new_id(items: list[dict], today: str) -> str:
    """INQ-20260828-003. 락 안에서 세야 같은 번호가 두 번 나오지 않는다."""
    n = sum(1 for r in items if str(r.get("id", "")).startswith(f"INQ-{today}-"))
    return f"INQ-{today}-{n + 1:03d}"


def submit(email: str, category: str, body: str, page_label: str) -> dict:
    """접수하고 저장한다. **메일은 별개다** — 저장이 됐으면 접수된 것으로 본다.

    ★메일 실패로 접수까지 실패시키지 않는다. 메일이 안 가면 담당자가 늦게 알 뿐이지만,
      저장이 안 되면 사용자가 쓴 글이 통째로 사라진다. 무엇을 지킬지가 다르다.
    """
    now = datetime.datetime.now()
    rec = {
        "id": "",
        "at": now.isoformat(timespec="seconds"),
        "email": (email or "").strip().lower(),
        "role": auth.get_role(email) or "",
        "team": auth.get_team(email) or "",
        "page": page_label or "",
        "category": category,
        "body": (body or "").strip(),
        "status": "열림",
        "handled_by": "",
        "handled_at": "",
        "reply": "",
    }

    def _add(d):
        d.setdefault("items", [])
        rec["id"] = _new_id(d["items"], now.strftime("%Y%m%d"))
        d["items"].append(rec)

    _store.mutate(_add)
    _mail(rec)
    return rec


def set_status(iid: str, status: str, by: str, reply: str = "") -> None:
    def _upd(d):
        for r in d.get("items", []):
            if r.get("id") == iid:
                r["status"] = status
                r["handled_by"] = (by or "").strip().lower()
                r["handled_at"] = datetime.datetime.now().isoformat(timespec="seconds")
                if reply:
                    r["reply"] = reply
    _store.mutate(_upd)


# ── 알림 ──────────────────────────────────────────────────────────
def _mail(rec: dict) -> None:
    """담당자에게 한 통. auth 의 알림 경로를 그대로 탄다(스레드·실패무시·dev차단)."""
    who = rec.get("email") or "(알 수 없음)"
    lines = [
        f"{rec['category']}",
        "",
        rec["body"],
        "",
        "─" * 30,
        f"보낸 사람 : {who}"
        + (f" · {rec['team']}" if rec.get("team") else "")
        + (f" · {rec['role']}" if rec.get("role") else ""),
        f"보던 화면 : {rec.get('page') or '(모름)'}",
        f"접수 시각 : {rec['at'].replace('T', ' ')}",
        f"문의 번호 : {rec['id']}",
    ]
    subject = f"[대시보드 문의] {rec['category']} · {rec.get('page') or '?'}"
    for to in recipients():
        auth.notify(to, subject, lines, cta="inquiry")


# ── 화면: 사이드바 ────────────────────────────────────────────────
# 창 안쪽 모양은 **여기서 못 박는다.**
# ★왜: `views/1_📸_포토이즘.py` 가 화면 범위 없이 전역으로
#   `[data-testid="stSelectbox"]{max-width:240px}` 와 우측정렬을 걸어 둔다. 그 화면에서
#   창을 열면 분류 드롭다운이 240px 로 쪼그라들어 오른쪽에 붙는다. 같은 창이 화면마다
#   달라 보이면 안 되므로, 창 안에서만 되돌린다(전역 규칙을 건드리면 포토이즘 화면의
#   카드 헤더 배치가 통째로 틀어진다).
_DIALOG_CSS = """
<style>
div[data-testid="stDialog"] div[role="dialog"]{
  width: min(760px, 94vw) !important; max-width: 94vw !important; }
div[data-testid="stDialog"] [data-testid="stSelectbox"]{
  max-width: none !important; width: 100% !important; }
div[data-testid="stDialog"]
  [data-testid="stElementContainer"]:has(> [data-testid="stSelectbox"]){
  display: block !important; }
div[data-testid="stDialog"]
  [data-testid="stSelectbox"] div[data-baseweb="select"] > div:first-child{
  min-height: 44px !important; height: 44px !important; border-radius: 10px !important; }
div[data-testid="stDialog"]
  [data-testid="stSelectbox"] div[data-baseweb="select"] div{
  font-size: 14.5px !important; font-weight: 500 !important; }
div[data-testid="stDialog"] [data-testid="stWidgetLabel"] p{
  font-size: 14px !important; font-weight: 700 !important; }
div[data-testid="stDialog"] textarea{
  font-size: 14.5px !important; line-height: 1.65 !important; }
div[data-testid="stDialog"] button{ height: 44px !important; font-weight: 700 !important; }
</style>
"""


@st.dialog("문의하기", width="large")
def _dialog(email: str, page_label: str) -> None:
    st.markdown(_DIALOG_CSS, unsafe_allow_html=True)
    st.caption(
        f"**{page_label}** 화면에서 문의해요. "
        "계정·화면·시각은 자동으로 함께 가니 따로 안 적으셔도 돼요."
    )
    cat = st.selectbox("무엇을 도와드릴까요?", CATEGORIES, key="inq_cat")
    body = st.text_area(
        "내용", key="inq_body", height=240,
        placeholder="예) 8월 20일 포토이즘 일본 매출이 어제 보던 값과 달라요.",
        help="언제 · 어느 숫자가 · 무엇과 다른지 적어 주시면 훨씬 빨리 찾아요.")

    c1, c2 = st.columns([1, 1])
    if c1.button("보내기", type="primary", use_container_width=True):
        if len(body.strip()) < 5:
            st.warning("내용을 조금만 더 적어 주세요.")
        else:
            try:
                rec = submit(email, cat, body, page_label)
                st.session_state["_inq_done"] = rec["id"]
            except Exception as ex:                     # noqa: BLE001
                # 저장까지 실패하면 사용자가 쓴 글이 날아간다 — 화면에 남겨 둔다.
                st.error(f"접수하지 못했어요({type(ex).__name__}). "
                         "적으신 내용은 그대로 있으니 잠시 뒤 다시 눌러 주세요.")
            else:
                st.rerun()
    if c2.button("닫기", use_container_width=True):
        st.rerun()


def render_sidebar(email: str, page_label: str) -> None:
    """사이드바 맨 아래(고정 계정 바 위)에 문의 버튼. **모든 화면에서 같은 자리.**

    ★계정 바는 position:fixed 로 그려지는 HTML 이라 버튼을 그 안에 못 넣는다.
      사이드바 본문 마지막에 두면 계정 바 바로 위에 앉는다(본문에 아래 여백 66px 이
      이미 잡혀 있다).
    """
    done = st.session_state.pop("_inq_done", None)
    if done:
        st.toast(f"문의를 접수했어요 · {done}", icon="✅")

    with st.sidebar:
        st.markdown("<div style='height:6px'></div>", unsafe_allow_html=True)
        if st.button("💬 문의하기", use_container_width=True,
                     help="화면이 이상하거나 궁금한 게 있으면 알려 주세요."):
            _dialog(email, page_label)


# ── 화면: 관리자 목록 ─────────────────────────────────────────────
_BADGE = {"열림": "🔴 열림", "처리중": "🟡 처리중", "완료": "🟢 완료"}


def render_admin(viewer: str) -> None:
    """소유자 화면의 '문의함' 탭 본문."""
    items = load_all()
    if not items:
        st.info("아직 들어온 문의가 없어요.")
        return

    only_open = st.checkbox("안 끝난 것만 보기", value=True, key="inq_only_open")
    rows = [r for r in items if r.get("status") != "완료"] if only_open else items
    st.caption(f"전체 {len(items)}건 · 안 끝난 것 {open_count()}건")
    if not rows:
        st.success("남은 문의가 없어요. 전부 처리했습니다.")
        return

    for r in rows:
        head = (f"{_BADGE.get(r.get('status'), r.get('status'))} · {r['id']} · "
                f"{r.get('category', '')} · {r.get('page') or '?'}")
        with st.expander(head, expanded=(r.get("status") == "열림")):
            st.caption(
                f"{r.get('email', '')}"
                + (f" · {r['team']}" if r.get("team") else "")
                + f" · {r.get('at', '').replace('T', ' ')}")
            st.write(r.get("body", ""))
            if r.get("reply"):
                st.caption(f"메모: {r['reply']}")
            if r.get("handled_by"):
                st.caption(f"처리: {r['handled_by']} · "
                           f"{r.get('handled_at', '').replace('T', ' ')}")

            note = st.text_input("처리 메모(선택)", key=f"inqnote_{r['id']}",
                                 value=r.get("reply", ""),
                                 placeholder="무엇을 어떻게 했는지 한 줄")
            c1, c2, c3 = st.columns(3)
            for col, s in zip((c1, c2, c3), STATUSES):
                if col.button(_BADGE[s], key=f"inqst_{r['id']}_{s}",
                              use_container_width=True,
                              disabled=(r.get("status") == s)):
                    set_status(r["id"], s, viewer, note)
                    st.rerun()
