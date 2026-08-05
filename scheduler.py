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


def load_config() -> dict:
    """config.json 을 읽어 dict 로. 실패하면 빈 dict.

    ★run_sales_deep_resync() 가 이 이름으로 부르는데 정의가 없어서 NameError 가
      났고, 그 함수의 광역 except 가 그걸 삼켜 `schedule.sales_deep_days` 설정이
      항상 무시되고 60일로 고정돼 있었다(2026-07-31 확인).
    """
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def load_schedule_time():
    cfg = load_config()
    h = cfg.get("schedule", {}).get("hour", 9)
    m = cfg.get("schedule", {}).get("minute", 0)
    try:
        return f"{int(h):02d}:{int(m):02d}"
    except (TypeError, ValueError):
        return "09:00"


def log(msg):
    LOG_DIR.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG_DIR / "scheduler.log", "a", encoding="utf-8") as f:
        f.write(line + "\n")


def fail(job: str, detail: str = ""):
    """로그를 남기고 **관리자에게 메일로도 알린다**.

    ★2026-08-03 추가. 그전까지 모든 실패는 로그 한 줄로 끝나서, 데이터가 며칠
      끊겨도 아무도 몰랐다. 되돌릴 수 없는 지점(재시도까지 소진했거나 재시도를
      예약하지 않는 경로)에서만 부른다 — 곧 재시도할 실패까지 알리면 소음이 된다.
      sm_mail 은 pandas 를 끌고 오므로 **필요할 때만** import 한다.
    """
    log(f"{job} 실패: {detail}" if detail else f"{job} 실패")
    try:
        import sm_mail
        sm_mail.alert_failure(job, detail)
    except Exception as e:
        log(f"[경고] 실패 알림 호출 불가: {type(e).__name__}: {str(e)[:120]}")


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
            fail("스내피즘 수집", f"재시도도 실패 (exit {result.returncode}). 오늘 수집 종료.")
            mark_ran_today()
    except subprocess.TimeoutExpired:
        fail("스내피즘 수집", "재시도 타임아웃. 오늘 수집 종료.")
        mark_ran_today()
    except Exception as e:
        fail("스내피즘 수집", f"재시도 오류: {e}")
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
        # 여기는 재시도를 예약하지 않는 경로다 → 알리지 않으면 그대로 묻힌다.
        fail("스내피즘 수집", f"실행 오류: {e}")


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
        if result.returncode == 0:
            log("포토이즘 재시도 완료.")
        else:
            fail("포토이즘 수집", f"재시도도 일부 실패 (exit {result.returncode}). "
                                  "일부 국가 데이터가 빠졌을 수 있습니다.")
    except subprocess.TimeoutExpired:
        fail("포토이즘 수집", "재시도 타임아웃(1시간 초과).")
        mark_photoism_ran()
    except Exception as e:
        fail("포토이즘 수집", f"재시도 오류: {e}")
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
def run_photoism_deep_resync():
    """매주: 포토이즘 매출을 30일치 재수집해 **늦게 반영된 거래**까지 채운다.

    ★왜 필요한가(2026-08-05 규명): photoism_crawler 의 롤링은 LOOKBACK_DAYS=3 뿐이라
      거래일 +3일이 지나 CMS 에 올라온 건은 **영구히 누락**된다. 실제로
      L-CA-LA-PHOTOISMKTP-KPOPNATION 의 2026-07-03 13:24·13:29 KFA 2건이
      퀵사이트엔 있는데 우리 XLSX 에는 없었다(파일을 07-10 에 받았는데도 없음).
      정산서가 그만큼 과소계상된다.
      스내피즘은 일일 14일 롤링 + 주간 60일 딥이 있는데 포토이즘만 대응이 없었다.

    ★기간을 30일로 잡은 이유: 30개국 × N일이라 60일이면 1,800회 다운로드다.
      서버 부담을 줄이려고 절반으로 잡았다(사용자 지정). config 로 조절 가능.
    ★크롤러 자체가 국가 간 COUNTRY_DELAY=2초를 두므로 여기서 더 조이지는 않는다.
      심야에 돌려 낮 트래픽과 겹치지 않게 한다.
    """
    try:
        deep = int(load_config().get("schedule", {}).get("photoism_deep_days", 30))
    except Exception:
        deep = 30
    end = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    start = (datetime.now() - timedelta(days=deep)).strftime("%Y-%m-%d")
    log(f"포토이즘 딥 재수집 시작: {start} ~ {end} ({deep}일)")
    try:
        result = subprocess.run(
            [sys.executable, str(BASE_DIR / "photoism_crawler.py"), start, end],
            cwd=str(BASE_DIR), capture_output=True, text=True, timeout=7200,
        )
        log(result.stdout.strip() if result.stdout else "(출력 없음)")
        log("포토이즘 딥 재수집 완료." if result.returncode == 0
            else f"포토이즘 딥 재수집 일부 실패 (exit {result.returncode})")
    except subprocess.TimeoutExpired:
        log("포토이즘 딥 재수집 타임아웃 (2시간 초과)")
    except Exception as e:
        log(f"포토이즘 딥 재수집 오류: {e}")


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


LOG_MAX_MB = 5      # 이보다 커지면 넘긴다
LOG_KEEP = 3        # 보관 세대 수 (.1 ~ .3)


def run_log_rotation():
    """logs/*.log 가 커지면 세대별로 넘긴다.

    ★모듈마다 log() 가 따로 있고 전부 open(...,"a") 라 무한히 쌓인다. 실제로
      photoism_crawler.log 2.7MB / backfill.log 2.67MB / scheduler.log 1MB 까지
      갔다. 9개 log() 를 다 뜯는 대신 여기서 한 번에 넘긴다 — 수집 경로를
      건드리지 않는 게 안전하다.

    쓰는 쪽이 open→write→close 라 파일을 계속 잡고 있지 않지만, 하필 그 순간이면
    rename 이 실패할 수 있다. 그건 그냥 넘기고 다음 날 다시 시도한다.
    """
    rotated = []
    for fp in sorted(LOG_DIR.glob("*.log")):
        try:
            mb = fp.stat().st_size / 1024 / 1024
            if mb < LOG_MAX_MB:
                continue
            oldest = fp.with_suffix(f".log.{LOG_KEEP}")
            if oldest.exists():
                oldest.unlink()
            for i in range(LOG_KEEP - 1, 0, -1):
                src = fp.with_suffix(f".log.{i}")
                if src.exists():
                    src.replace(fp.with_suffix(f".log.{i + 1}"))
            fp.replace(fp.with_suffix(".log.1"))     # 이 뒤로 fp 는 없다
            rotated.append(f"{fp.name}({mb:.1f}MB)")
        except Exception as e:
            log(f"[경고] 로그 회전 실패 {fp.name}: {type(e).__name__}")
    if rotated:
        log(f"로그 회전: {', '.join(rotated)}")


def run_coverage_audit():
    """수집이 끝난 뒤 커버리지 점검 — '조용한 결손'을 하루 안에 잡는다.

    ★2026-08-03 추가. 포토이즘 2025년 7일이 ~99% 비어 있었는데 1년 반 동안
      아무도 몰랐다. 최종일만 보는 신선도 체크로는 못 잡는 종류였다.
      비교 기준은 전 기간을 쓰고 **보고만 최근 14일**로 좁힌다 — 안 그러면
      옛날 결손을 매일 다시 알린다.
    """
    log("커버리지 점검 시작...")
    try:
        import coverage_audit
        res = coverage_audit.audit(report_days=14)
        txt = coverage_audit.summary_text(res)
        if txt:
            fail("커버리지 점검", "최근 14일에서 수집 이상이 보입니다.\n\n" + txt)
        else:
            log("커버리지 점검: 이상 없음")
    except Exception as e:
        log(f"[경고] 커버리지 점검 실패: {type(e).__name__}: {str(e)[:200]}")


def main():
    _ensure_single_instance()   # 이중 실행 방지(이미 돌면 여기서 종료)
    run_time = load_schedule_time()
    log(f"스케줄러 시작 - 매일 {run_time}에 크롤러 실행 (환율 포함)")
    log(f"로그 파일: {LOG_DIR / 'scheduler.log'}")

    schedule.every().day.at(run_time).do(run_crawler)
    schedule.every().day.at(run_time).do(run_photoism_crawler)
    schedule.every().monday.at("07:00").do(run_sm_weekly)          # SM 촬영수 주간 갱신
    schedule.every().monday.at("05:00").do(run_sales_deep_resync)  # 매출 60일 딥 재수집(늦은 취소 반영)
    # 포토이즘 딥 30일 — 일일 롤링이 3일뿐이라 늦게 올라온 거래가 영구 누락된다.
    # 스내피즘 딥(05:00)과 겹치면 CMS 부하가 몰려서 시간을 벌려 둔다.
    schedule.every().monday.at("02:00").do(run_photoism_deep_resync)
    # Jira 일정 캐시 예열 — TTL 12h 에 맞춰 하루 두 번(업무 시작 전 / 저녁).
    # 안 해두면 캐시 만료 후 첫 접속자가 20초쯤 기다린다.
    schedule.every().day.at("08:40").do(run_jira_cache_warm)
    schedule.every().day.at("20:40").do(run_jira_cache_warm)
    # 커버리지 점검 — 포토이즘 크롤(09:00~09:15)과 재시도(+1h)까지 끝난 뒤에 본다.
    schedule.every().day.at("11:00").do(run_coverage_audit)
    schedule.every().day.at("04:30").do(run_log_rotation)   # 수집이 안 도는 시간대
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
