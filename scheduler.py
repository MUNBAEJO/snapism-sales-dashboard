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
import socket
import subprocess
import sys
import time
from datetime import datetime, date, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).parent
CONFIG_FILE = BASE_DIR / "config.json"
LOG_DIR = BASE_DIR / "logs"
STATE_FILE            = LOG_DIR / "last_run.txt"
RETRY_STATE_FILE      = LOG_DIR / "retry_today.txt"
PHOTOISM_STATE_FILE   = LOG_DIR / "photoism_last_run.txt"
PHOTOISM_RETRY_FILE   = LOG_DIR / "photoism_retry_today.txt"

# 단일 인스턴스 가드 — 고정 포트 바인드. 이미 스케줄러가 돌면 두 번째는 즉시 종료.
# (부팅 런처 + 워치독이 동시에 띄우려는 경쟁에서 이중 실행=이중 크롤을 방지.
#  소켓은 프로세스가 죽으면 OS가 자동 해제 → 스테일 락 문제 없음.)
_SINGLETON_PORT = 47615
_SINGLETON_SOCK = None


def _ensure_single_instance():
    global _SINGLETON_SOCK
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", _SINGLETON_PORT))
        s.listen(1)
        _SINGLETON_SOCK = s          # 프로세스 수명 동안 보유(GC 방지)
    except OSError:
        print("스케줄러가 이미 실행 중입니다. 이 인스턴스는 종료합니다.")
        sys.exit(0)


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


def already_retried_today():
    if not RETRY_STATE_FILE.exists():
        return False
    try:
        last = date.fromisoformat(RETRY_STATE_FILE.read_text(encoding="utf-8").strip())
        return last == date.today()
    except Exception:
        return False


def mark_retried_today():
    LOG_DIR.mkdir(exist_ok=True)
    RETRY_STATE_FILE.write_text(date.today().isoformat(), encoding="utf-8")


def run_retry():
    """1시간 후 재시도 실행"""
    schedule.clear("retry")  # 반복 방지
    mark_retried_today()
    log("=== 1시간 후 재시도 시작 ===")
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
            log("재시도 완료.")
        else:
            log(f"재시도도 실패 (exit {result.returncode}). 오늘 수집 종료.")
            mark_ran_today()
    except subprocess.TimeoutExpired:
        log("재시도 타임아웃. 오늘 수집 종료.")
        mark_ran_today()
    except Exception as e:
        log(f"재시도 오류: {e}")
        mark_ran_today()


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
            _schedule_retry()
    except subprocess.TimeoutExpired:
        log("크롤러 타임아웃 (10분 초과)")
        _schedule_retry()
    except Exception as e:
        log(f"실행 오류: {e}")


def _schedule_retry():
    """실패 시 1시간 후 재시도 예약 (오늘 최초 1회만)"""
    if already_retried_today():
        return
    retry_at = (datetime.now() + timedelta(hours=1)).strftime("%H:%M")
    log(f"1시간 후 재시도 예약: {retry_at}")
    schedule.every().day.at(retry_at).do(run_retry).tag("retry")


# ── 포토이즘 크롤러 ───────────────────────────────────────────
def photoism_ran_today():
    if not PHOTOISM_STATE_FILE.exists():
        return False
    try:
        return date.fromisoformat(PHOTOISM_STATE_FILE.read_text(encoding="utf-8").strip()) == date.today()
    except Exception:
        return False

def mark_photoism_ran():
    LOG_DIR.mkdir(exist_ok=True)
    PHOTOISM_STATE_FILE.write_text(date.today().isoformat(), encoding="utf-8")

def photoism_retried_today():
    if not PHOTOISM_RETRY_FILE.exists():
        return False
    try:
        return date.fromisoformat(PHOTOISM_RETRY_FILE.read_text(encoding="utf-8").strip()) == date.today()
    except Exception:
        return False

def mark_photoism_retried():
    LOG_DIR.mkdir(exist_ok=True)
    PHOTOISM_RETRY_FILE.write_text(date.today().isoformat(), encoding="utf-8")

def run_photoism_retry():
    schedule.clear("photoism_retry")
    mark_photoism_retried()
    log("=== 포토이즘 1시간 후 재시도 ===")
    try:
        result = subprocess.run(
            [sys.executable, str(BASE_DIR / "photoism_crawler.py")],
            cwd=str(BASE_DIR), capture_output=True, text=True, timeout=3600,
        )
        log(result.stdout.strip() if result.stdout else "(출력 없음)")
        mark_photoism_ran()
        log("포토이즘 재시도 완료." if result.returncode == 0 else "포토이즘 재시도도 일부 실패.")
    except subprocess.TimeoutExpired:
        log("포토이즘 재시도 타임아웃.")
        mark_photoism_ran()
    except Exception as e:
        log(f"포토이즘 재시도 오류: {e}")
        mark_photoism_ran()

def run_photoism_crawler():
    if photoism_ran_today():
        log("포토이즘 오늘 이미 실행됨. 건너뜀.")
        return
    log("포토이즘 크롤러 실행 시작...")
    try:
        result = subprocess.run(
            [sys.executable, str(BASE_DIR / "photoism_crawler.py")],
            cwd=str(BASE_DIR), capture_output=True, text=True, timeout=3600,
        )
        log(result.stdout.strip() if result.stdout else "(출력 없음)")
        if result.returncode == 0:
            mark_photoism_ran()
            log("포토이즘 크롤러 완료.")
        else:
            log(f"포토이즘 일부 실패 (exit {result.returncode})")
            mark_photoism_ran()
            if not photoism_retried_today():
                retry_at = (datetime.now() + timedelta(hours=1)).strftime("%H:%M")
                log(f"포토이즘 1시간 후 재시도 예약: {retry_at}")
                schedule.every().day.at(retry_at).do(run_photoism_retry).tag("photoism_retry")
    except subprocess.TimeoutExpired:
        log("포토이즘 크롤러 타임아웃 (60분 초과)")
        mark_photoism_ran()
    except Exception as e:
        log(f"포토이즘 실행 오류: {e}")


# ── SM 촬영수 주간 수집 (매주 월요일) ───────────────────────────
def run_sm_weekly():
    """매주 월요일: 최근 2주 SM 촬영수 CMS 재수집(덮어쓰기) + 부서 공유 엑셀 생성."""
    log("SM 주간 수집 시작...")
    try:
        result = subprocess.run(
            [sys.executable, str(BASE_DIR / "sm_weekly.py")],
            cwd=str(BASE_DIR), capture_output=True, text=True, timeout=3600,
        )
        log(result.stdout.strip() if result.stdout else "(출력 없음)")
        log("SM 주간 완료." if result.returncode == 0 else f"SM 주간 일부 실패 (exit {result.returncode})")
    except subprocess.TimeoutExpired:
        log("SM 주간 수집 타임아웃 (60분 초과)")
    except Exception as e:
        log(f"SM 주간 수집 오류: {e}")


def run_jira_cache_warm():
    """Jira IP 일정 캐시 예열 — 대시보드 첫 접속자가 기다리지 않게.

    brand='all' 은 4,200여 건을 100건씩 페이징으로 받아 콜드 조회에 20초쯤 걸린다.
    캐시가 비어 있으면 그 시간을 '그때 접속한 사람'이 그대로 기다린다(실제로 타임아웃까지 났음).
    TTL(12h)에 맞춰 하루 두 번 미리 채워두면 사용자는 항상 캐시 히트다.
    """
    log("Jira 일정 캐시 예열 시작...")
    try:
        from jira_ip_dates import fetch_ip_dates
        for brand in ("all", "photoism", "snapism"):
            try:
                n = len(fetch_ip_dates(brand=brand, force_refresh=True))
                log(f"  {brand}: {n:,}건")
            except Exception as e:
                log(f"  {brand}: 실패 ({e})")   # 한 브랜드 실패해도 나머지는 계속
        log("Jira 일정 캐시 예열 완료.")
    except Exception as e:
        log(f"Jira 캐시 예열 오류: {e}")


# ── 매출 딥 재수집 (매주 월요일) ─────────────────────────────────
def run_sales_deep_resync():
    """매주: 매출을 더 긴 기간(기본 60일) 재수집해 '늦은 취소·정정'까지 반영.
    일일 크롤은 최근 14일 롤링이라 대부분 잡히지만, 그 이후 발생한 취소를 이 주간 딥이 보완.
    crawler.py 를 명시적 날짜범위로 호출 → ingest.py(keep=last)가 옛 행을 덮어씀."""
    try:
        deep = int(load_config().get("schedule", {}).get("sales_deep_days", 60))
    except Exception:
        deep = 60
    end = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    start = (datetime.now() - timedelta(days=deep)).strftime("%Y-%m-%d")
    log(f"매출 딥 재수집 시작: {start} ~ {end} ({deep}일)")
    try:
        result = subprocess.run(
            [sys.executable, str(BASE_DIR / "crawler.py"), start, end],
            cwd=str(BASE_DIR), capture_output=True, text=True, timeout=1800,
        )
        log(result.stdout.strip() if result.stdout else "(출력 없음)")
        log("매출 딥 재수집 완료." if result.returncode == 0
            else f"매출 딥 재수집 일부 실패 (exit {result.returncode})")
    except subprocess.TimeoutExpired:
        log("매출 딥 재수집 타임아웃 (30분 초과)")
    except Exception as e:
        log(f"매출 딥 재수집 오류: {e}")


# ── [1회성] SM PICK 백필 (자정 실행) ─────────────────────────────
#  sm_collect 가 PICK 구좌('PW ...' 타이틀)를 건너뛰던 버그로 2026-01-23~07-19 촬영수가 불완전.
#  sm_backfill_monthly.bat 이 월별 청크로 재수집(각 청크마다 저장, upsert 라 재실행 안전).
#  완료 플래그로 1회만 실행 — 다시 돌리려면 logs/sm_backfill_done.txt 삭제.
SM_BACKFILL_FLAG = LOG_DIR / "sm_backfill_done.txt"


def run_sm_backfill_once():
    if SM_BACKFILL_FLAG.exists():
        return
    LOG_DIR.mkdir(exist_ok=True)
    SM_BACKFILL_FLAG.write_text(datetime.now().isoformat(), encoding="utf-8")  # 선기록(중복 실행 방지)
    log("=== [1회성] SM PICK 백필 시작 (월별 청크, 최대 8시간) ===")
    try:
        r = subprocess.run(
            ["cmd", "/c", str(BASE_DIR / "sm_backfill_monthly.bat")],
            cwd=str(BASE_DIR), capture_output=True, text=True, timeout=8 * 3600,
        )
        log("SM PICK 백필 완료." if r.returncode == 0
            else f"SM PICK 백필 종료 (exit {r.returncode}) — logs/sm_backfill.log 확인")
    except subprocess.TimeoutExpired:
        log("SM PICK 백필 타임아웃(8시간 초과)")
    except Exception as e:
        log(f"SM PICK 백필 오류: {e}")


def main():
    _ensure_single_instance()   # 이중 실행 방지(이미 돌면 여기서 종료)
    run_time = load_schedule_time()
    log(f"스케줄러 시작 - 매일 {run_time}에 크롤러 실행 (환율 포함)")
    log(f"로그 파일: {LOG_DIR / 'scheduler.log'}")

    schedule.every().day.at(run_time).do(run_crawler)
    schedule.every().day.at(run_time).do(run_photoism_crawler)
    schedule.every().monday.at("07:00").do(run_sm_weekly)          # SM 촬영수 주간 갱신
    schedule.every().monday.at("05:00").do(run_sales_deep_resync)  # 매출 60일 딥 재수집(늦은 취소 반영)
    # Jira 일정 캐시 예열 — TTL 12h 에 맞춰 하루 두 번(업무 시작 전 / 저녁).
    # 안 해두면 캐시 만료 후 첫 접속자가 20초쯤 기다린다.
    schedule.every().day.at("08:40").do(run_jira_cache_warm)
    schedule.every().day.at("20:40").do(run_jira_cache_warm)
    if not SM_BACKFILL_FLAG.exists():                              # [1회성] SM PICK 백필
        schedule.every().day.at("00:00").do(run_sm_backfill_once)
        log("→ [1회성] SM PICK 백필 예약됨: 오늘 자정 00:00 (완료 후 자동 비활성)")

    # 시작 즉시 환율 1회 갱신
    run_update_rates()

    # 실행 시각이 이미 지났고 오늘 아직 크롤링 안 했으면 즉시 실행
    now = datetime.now()
    h, m = map(int, run_time.split(":"))
    if now.hour > h or (now.hour == h and now.minute >= m):
        if not already_ran_today():
            log(f"실행 시각({run_time})이 이미 지남 - 즉시 보충 실행")
            run_crawler()
        if not photoism_ran_today():
            log(f"포토이즘 실행 시각({run_time})이 이미 지남 - 즉시 보충 실행")
            run_photoism_crawler()

    while True:
        schedule.run_pending()
        time.sleep(30)


if __name__ == "__main__":
    main()
