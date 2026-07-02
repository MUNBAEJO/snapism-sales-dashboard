# -*- coding: utf-8 -*-
"""매주 월요일 SM 촬영현황 주간 엑셀을 담당부서에 자동 메일 발송.

- 무거운 재수집 없이, 이미 쌓인 data/sm_shoot_daily.parquet(일일 자동수집분)로
  최근 2주 엑셀을 생성해 첨부 → SMTP로 발송.
- 설정은 config.json 의 "mail" 섹션에서 읽는다(비밀번호는 파일에만, 코드/로그 미출력).
    "mail": {
      "smtp_host": "smtp.gmail.com",
      "smtp_port": 587,
      "sender": "ansqo34@seobuk.kr",
      "sender_name": "스내피즘 대시보드",
      "app_password": "<Google 앱 비밀번호 16자리>",
      "recipients": ["dept1@seobuk.kr", "dept2@seobuk.kr"],
      "cc": [],
      "weeks": 2
    }
- 설정(수신자·앱비번)이 비어 있으면 발송하지 않고 로그만 남긴다.

실행:  python sm_mail.py           # 최근 2주 발송
       python sm_mail.py --dry     # 엑셀만 만들고 발송은 생략(점검용)
Windows 작업 스케줄러 weekly(월 11:00) 트리거 권장. 보안: 비밀번호/토큰 미출력.
"""
import json
import smtplib
import ssl
import sys
from datetime import date, datetime, timedelta
from email.message import EmailMessage
from email.utils import formataddr
from pathlib import Path

import sm_report

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE_DIR = Path(__file__).parent
CONFIG_FILE = BASE_DIR / "config.json"
LOG_DIR = BASE_DIR / "logs"
REPORT_DIR = BASE_DIR / "reports"


def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    LOG_DIR.mkdir(exist_ok=True)
    with open(LOG_DIR / "sm_mail.log", "a", encoding="utf-8") as f:
        f.write(line + "\n")


def _load_mail_cfg():
    cfg = json.load(open(CONFIG_FILE, encoding="utf-8"))
    return cfg.get("mail", {})


def build_weekly_xlsx(weeks: int = 2):
    """최근 N주 SM 촬영현황 엑셀 생성 → (bytes, start, end, 파일명)."""
    end = date.today() - timedelta(days=1)          # 어제까지(발송일 당일 제외)
    start = end - timedelta(days=weeks * 7 - 1)
    df = sm_report.load_daily(start.isoformat(), end.isoformat())
    if df.empty:
        return None, start, end, None
    data = sm_report.build_xlsx(df)
    fname = f"SM촬영현황_주간_{df['날짜'].min()}_{df['날짜'].max()}.xlsx"
    # 발송본을 reports/ 에도 저장(감사·재발송용)
    REPORT_DIR.mkdir(exist_ok=True)
    (REPORT_DIR / fname).write_bytes(data)
    return data, start, end, fname


def send(dry: bool = False):
    m = _load_mail_cfg()
    weeks = int(m.get("weeks", 2))

    data, start, end, fname = build_weekly_xlsx(weeks)
    if data is None:
        log("해당 기간 데이터가 없어 발송을 건너뜁니다.")
        return
    log(f"엑셀 생성 완료: {fname} ({len(data):,} bytes)")

    recipients = [r for r in m.get("recipients", []) if r and "@" in r]
    cc = [r for r in m.get("cc", []) if r and "@" in r]
    sender = (m.get("sender") or "").strip()
    app_pw = (m.get("app_password") or "").strip()
    host = m.get("smtp_host", "smtp.gmail.com")
    port = int(m.get("smtp_port", 587))

    if dry:
        log(f"[DRY] 발송 생략. 수신자={len(recipients)}명, 첨부={fname}")
        return
    if not (sender and app_pw and recipients):
        log("발송 설정 미완료(sender/app_password/recipients) — 발송 생략. "
            "config.json 의 mail 섹션을 채워 주세요.")
        return

    period = f"{start.isoformat()} ~ {end.isoformat()}"
    msg = EmailMessage()
    msg["Subject"] = f"[포토이즘] SM 촬영현황 주간 리포트 ({period})"
    msg["From"] = formataddr((m.get("sender_name", "포토이즘 대시보드"), sender))
    msg["To"] = ", ".join(recipients)
    if cc:
        msg["Cc"] = ", ".join(cc)
    msg.set_content(
        "안녕하세요 :)\n\n"
        "이번 주 SM 촬영현황 리포트 보내드려요. 아티스트별·국가별로 정리해뒀어요.\n\n"
        "시차 있는 국가는 최근 1~2일 수치가 나중에 조금 바뀔 수 있으니 참고만 해주세요!\n\n"
        "편하게 보시고 궁금한 점 있으면 언제든 말씀 주세요.\n"
        "감사합니다 🙂\n\n"
        "(매주 월요일 오전 11시에 자동으로 보내드리는 메일이에요)"
    )
    msg.add_attachment(
        data, maintype="application",
        subtype="vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=fname,
    )

    all_rcpts = recipients + cc
    ctx = ssl.create_default_context()
    with smtplib.SMTP(host, port, timeout=60) as s:
        s.ehlo()
        s.starttls(context=ctx)
        s.login(sender, app_pw)
        s.send_message(msg, to_addrs=all_rcpts)
    log(f"발송 완료: {len(all_rcpts)}명 (To {len(recipients)} / Cc {len(cc)}), 첨부 {fname}")


def main():
    dry = "--dry" in sys.argv[1:]
    try:
        send(dry=dry)
    except Exception as e:
        log(f"발송 오류: {type(e).__name__}: {str(e)[:160]}")
        sys.exit(1)


if __name__ == "__main__":
    main()
