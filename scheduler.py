"""
스내피즘 매출 자동 수집 스케줄러

- 매일 config.json 에 설정된 시각(기본 09:00)에 crawler.py를 자동 실행
- 컴퓨터가 켜져 있는 한 계속 실행
- PC 재시작시: '스케줄러시작.bat' 을 시작프로그램에 등록하면 자동 재시작

실행: pythonw scheduler.py  (창 없이 백그라운드 실행)
     python   scheduler.py  (콘솔 창 표시)
"""
import json
import schedule
import subprocess
import sys
import time
from datetime import datetime, date
from pathlib import Path

BASE_DIR = Path(__file__).parent
CONFIG_FILE = BASE_DIR / "config.json"
LOG_DIR = BASE_DIR / "logs"
STATE_FILE = LOG_DIR / "last_run.txt"


def load_schedule_time():
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            cfg = json.load(f)
        h = cfg.get("schedule", {}).get("hour", 9)
        m = cfg.get("schedule", {}).get("minute", 0)
        return f"{h:02d}:{m:02d}"
    except Exception:
        return "09:00"


def log(msg):
    LOG_DIR.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG_DIR / "scheduler.log", "a", encoding="utf-8") as f:
        f.write(line + "\n")


def already_ran_today():
    if not STATE_FILE.exists():
        return False
    try:
        last = date.fromisoformat(STATE_FILE.read_text(encoding="utf-8").strip())
        return last == date.today()
    except Exception:
        return False


def mark_ran_today():
    LOG_DIR.mkdir(exist_ok=True)
    STATE_FILE.write_text(date.today().isoformat(), encoding="utf-8")


def run_update_rates():
    """환율 API 호출 → config.json 업데이트"""
    log("환율 업데이트 시작...")
    try:
        result = subprocess.run(
            [sys.executable, str(BASE_DIR / "update_rates.py")],
            cwd=str(BASE_DIR),
            capture_output=True,
            text=True,
            timeout=30,
        )
        log(result.stdout.strip() if result.stdout else "(출력 없음)")
        if result.returncode != 0:
            log(f"환율 업데이트 실패: {result.stderr[:200]}")
    except Exception as e:
        log(f"환율 업데이트 오류: {e}")


def run_crawler():
    if already_ran_today():
        log("오늘 이미 실행됨. 건너뜀.")
        return

    # 크롤 전 환율 먼저 갱신
    run_update_rates()

    log("크롤러 실행 시작...")
    try:
        result = subprocess.run(
            [sys.executable, str(BASE_DIR / "crawler.py")],
            cwd=str(BASE_DIR),
            capture_output=True,
            text=True,
            timeout=600,
        )
        log(result.stdout.strip() if result.stdout else "(출력 없음)")
        if result.returncode == 0:
            mark_ran_today()
            log("크롤러 완료.")
        else:
            log(f"크롤러 오류 (exit {result.returncode}): {result.stderr[:300]}")
    except subprocess.TimeoutExpired:
        log("크롤러 타임아웃 (10분 초과)")
    except Exception as e:
        log(f"실행 오류: {e}")


def main():
    run_time = load_schedule_time()
    log(f"스케줄러 시작 - 매일 {run_time}에 크롤러 실행 (환율 포함)")
    log(f"로그 파일: {LOG_DIR / 'scheduler.log'}")

    schedule.every().day.at(run_time).do(run_crawler)

    # 시작 즉시 환율 1회 갱신
    run_update_rates()

    # 실행 시각이 이미 지났고 오늘 아직 크롤링 안 했으면 즉시 실행
    now = datetime.now()
    h, m = map(int, run_time.split(":"))
    if now.hour > h or (now.hour == h and now.minute >= m):
        if not already_ran_today():
            log(f"실행 시각({run_time})이 이미 지남 - 즉시 보충 실행")
            run_crawler()

    while True:
        schedule.run_pending()
        time.sleep(30)


if __name__ == "__main__":
    main()
