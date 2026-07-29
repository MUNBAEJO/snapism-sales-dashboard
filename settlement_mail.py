"""IP 정산서 — 5단계: 메일 발송.

`sm_mail.py` 의 SMTP 설정(config.json 의 mail 섹션)을 그대로 쓴다.
정산서는 **금액이 담긴 대외 문서**라 발송을 함부로 하면 안 되므로
sm_mail 과 달리 수신자를 화면에서 직접 받고, 미리보기(dry-run)를 기본으로 둔다.

관련: CURRENT-PROJECTS/IP-정산서-생성.md · 지라 CO-288
"""
from __future__ import annotations

import json
import smtplib
import ssl
from datetime import datetime
from email.headerregistry import Address           # noqa: F401  (형식 참고)
from email.message import EmailMessage
from email.utils import formataddr
from pathlib import Path

from json_store import JsonStore

BASE_DIR = Path(__file__).parent
CONFIG = BASE_DIR / "config.json"

_log_store = JsonStore("settlement_mail_log.json", default={"sent": []})


def mail_config() -> dict:
    try:
        return json.loads(CONFIG.read_text(encoding="utf-8")).get("mail", {}) or {}
    except Exception:
        return {}


def config_ready() -> tuple[bool, str]:
    """발송 가능 여부. **비밀번호 값 자체는 절대 돌려주지 않는다.**"""
    m = mail_config()
    if not (m.get("sender") or "").strip():
        return False, "config.json 의 mail.sender 가 비어 있어요."
    if not (m.get("app_password") or "").strip():
        return False, "config.json 의 mail.app_password 가 비어 있어요."
    return True, (m.get("sender") or "").strip()


def build_message(ip: str, start: str, end: str, files: dict[str, bytes],
                  to: list[str], cc: list[str], version: int = 1,
                  reason: str = "", note: str = "") -> EmailMessage:
    """정산서 메일 한 통. files = {수취처라벨: PDF 바이트}."""
    m = mail_config()
    sender = (m.get("sender") or "").strip()
    tag = f" (정정본 v{version})" if version > 1 else ""

    msg = EmailMessage()
    msg["Subject"] = f"[정산서] {ip} · {start} ~ {end}{tag}"
    msg["From"] = formataddr((m.get("sender_name", "스내피즘·포토이즘"), sender))
    msg["To"] = ", ".join(to)
    if cc:
        msg["Cc"] = ", ".join(cc)

    body = [
        "안녕하세요 :)",
        "",
        f"{ip} 의 {start} ~ {end} 정산서를 보내드려요.",
        "포토이즘·스내피즘 두 브랜드 매출을 합쳐 계산했고, "
        "국가별 상세와 수량은 문서 뒷장에 담았어요.",
    ]
    if version > 1:
        body += ["", f"이번 건은 정정본(v{version})이에요."
                 + (f" 정정 사유: {reason}" if reason else "")]
    if note.strip():
        body += ["", note.strip()]
    body += ["", "확인해 보시고 궁금한 점은 편하게 알려주세요.", "감사합니다 🙂"]
    msg.set_content("\n".join(body))

    for label, data in files.items():
        msg.add_attachment(data, maintype="application", subtype="pdf",
                           filename=f"정산서_{ip}_{start[:7]}_{label}.pdf")
    return msg


def send(msg: EmailMessage, to: list[str], cc: list[str]) -> None:
    m = mail_config()
    sender = (m.get("sender") or "").strip()
    app_pw = (m.get("app_password") or "").strip()
    host = m.get("smtp_host", "smtp.gmail.com")
    port = int(m.get("smtp_port", 587))
    if not (sender and app_pw):
        raise RuntimeError("메일 설정이 비어 있어요(config.json 의 mail 섹션).")
    ctx = ssl.create_default_context()
    with smtplib.SMTP(host, port, timeout=60) as s:
        s.ehlo()
        s.starttls(context=ctx)
        s.login(sender, app_pw)
        s.send_message(msg, to_addrs=list(to) + list(cc))


def log_sent(ip: str, start: str, end: str, to: list[str], cc: list[str],
             version: int, by: str, files: list[str]) -> None:
    rec = {"ip": ip, "start": start, "end": end, "to": to, "cc": cc,
           "version": version, "by": by, "files": files,
           "at": datetime.now().isoformat(timespec="seconds")}
    _log_store.mutate(lambda d: d.setdefault("sent", []).append(rec))


def sent_log(limit: int = 50) -> list[dict]:
    return list(reversed(_log_store.load().get("sent", [])))[:limit]


def already_sent(ip: str, start: str, end: str, version: int) -> dict | None:
    """같은 건을 또 보내려 할 때 경고하려고. 중복 발송은 대외 사고다."""
    for r in _log_store.load().get("sent", []):
        if (r["ip"], r["start"], r["end"], r["version"]) == (ip, start, end, version):
            return r
    return None
