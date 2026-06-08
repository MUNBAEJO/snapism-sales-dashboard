"""
포토이즘 과거 데이터 백필 크롤러
- 기존 photoism_crawler.py 와 동일한 방식 (Playwright 로그인 -> JWT -> API)
- 3일 단위로 순차 수집, logs/backfill_state.json 에 진행 상태 저장
- Windows 작업 스케줄러로 20분마다 실행 권장

사용법:
  최초 시작:    python photoism_backfill.py 2026-01-01 2026-05-31
  이어서 실행:  python photoism_backfill.py          (상태파일 자동 로드)
  상태 확인:    python photoism_backfill.py --status
  처음부터 다시: python photoism_backfill.py --reset 2026-01-01 2026-05-31
"""
import io
import json
import sys
import time
import urllib.request
import urllib.error
import subprocess
from pathlib import Path
from datetime import datetime, timedelta, timezone, date

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

BASE_DIR    = Path(__file__).parent
CONFIG_FILE = BASE_DIR / "config.json"
RAW_DIR     = BASE_DIR / "raw_photoism"
LOG_DIR     = BASE_DIR / "logs"
STATE_FILE  = LOG_DIR / "backfill_state.json"

CHUNK_DAYS    = 3    # 한번에 수집할 일 수
COUNTRY_DELAY = 5    # 국가 간 대기 시간(초) - 서버 부하 방지

# Windows 콘솔 UTF-8 강제 설정
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


# ─────────────────────────────────────────────
# 로그
# ─────────────────────────────────────────────

def log(msg: str):
    ts   = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    try:
        print(line, flush=True)
    except Exception:
        sys.stdout.buffer.write((line + "\n").encode("utf-8", errors="replace"))
        sys.stdout.buffer.flush()
    LOG_DIR.mkdir(exist_ok=True)
    with open(LOG_DIR / "backfill.log", "a", encoding="utf-8") as f:
        f.write(line + "\n")


# ─────────────────────────────────────────────
# 상태 파일
# ─────────────────────────────────────────────

def load_state():
    if not STATE_FILE.exists():
        return None
    with open(STATE_FILE, encoding="utf-8") as f:
        return json.load(f)


def save_state(state: dict):
    LOG_DIR.mkdir(exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def init_state(start_str: str, end_str: str) -> dict:
    start = date.fromisoformat(start_str)
    end   = date.fromisoformat(end_str)
    days  = (end - start).days + 1
    total = (days + CHUNK_DAYS - 1) // CHUNK_DAYS
    state = {
        "start_date":   start_str,
        "end_date":     end_str,
        "next_date":    start_str,
        "completed":    False,
        "total_chunks": total,
        "done_chunks":  0,
        "last_run":     None,
    }
    save_state(state)
    log(f"백필 시작: {start_str} ~ {end_str}  총 {total}청크 ({days}일, {CHUNK_DAYS}일씩)")
    return state


def print_status():
    state = load_state()
    if not state:
        print("상태 파일 없음. 아직 백필을 시작하지 않았습니다.")
        print("시작: python photoism_backfill.py YYYY-MM-DD YYYY-MM-DD")
        return
    done  = state.get("done_chunks", 0)
    total = state.get("total_chunks", 1)
    pct   = done / total * 100 if total else 0
    status_str = "[완료]" if state["completed"] else "[진행중]"
    print(f"\n{'='*50}")
    print(f" 백필 진행 상태")
    print(f"{'='*50}")
    print(f" 대상 기간:   {state['start_date']} ~ {state['end_date']}")
    print(f" 다음 날짜:   {state['next_date']}")
    print(f" 진행률:      {done}/{total} 청크  ({pct:.1f}%)")
    print(f" 완료 여부:   {status_str}")
    print(f" 마지막 실행: {state.get('last_run', '없음')}")
    print(f"{'='*50}\n")


# ─────────────────────────────────────────────
# photoism_crawler.py 공통 로직 재사용
# ─────────────────────────────────────────────

def load_config() -> dict:
    with open(CONFIG_FILE, encoding="utf-8") as f:
        return json.load(f)


def date_to_utc(date_str: str, is_end: bool, tz_offset: int = 9) -> str:
    local_tz = timezone(timedelta(hours=tz_offset))
    if is_end:
        local_dt = datetime.strptime(f"{date_str} 23:59:59", "%Y-%m-%d %H:%M:%S").replace(tzinfo=local_tz)
    else:
        local_dt = datetime.strptime(f"{date_str} 00:00:00", "%Y-%m-%d %H:%M:%S").replace(tzinfo=local_tz)
    utc_dt = local_dt.astimezone(timezone.utc)
    ms = "999" if is_end else "000"
    return utc_dt.strftime(f"%Y-%m-%dT%H:%M:%S.{ms}Z")


def get_country_code(cms_url: str) -> str:
    import re
    m = re.search(r'cms-([a-z]+)\.seobuk', cms_url)
    if m:
        return m.group(1).upper()
    if 'photoism.cn' in cms_url:
        return 'CN'
    return 'KR'


EXCEL_COLUMNS = [
    {"columnId": "cityNmEn",          "headerDesc": "지역"},
    {"columnId": "brandNmEn",         "headerDesc": "브랜드"},
    {"columnId": "storeType01NmEn",   "headerDesc": "대분류"},
    {"columnId": "storeType02NmEn",   "headerDesc": "중분류"},
    {"columnId": "storeType03NmEn",   "headerDesc": "소분류"},
    {"columnId": "storeName",         "headerDesc": "매장명"},
    {"columnId": "hqRoyalty",         "headerDesc": "본사 로열티,"},
    {"columnId": "colorNmEn",         "headerDesc": "부스 색상"},
    {"columnId": "boothNum",          "headerDesc": "키오스크 ID"},
    {"columnId": "frameType",         "headerDesc": "구좌"},
    {"columnId": "titleName",         "headerDesc": "타이틀"},
    {"columnId": "frameName",         "headerDesc": "프레임"},
    {"columnId": "framePrice",        "headerDesc": "프레임 단가"},
    {"columnId": "orderCount",        "headerDesc": "주문횟수"},
    {"columnId": "totalFramePrice",   "headerDesc": "상품총액"},
    {"columnId": "couponDiscount",    "headerDesc": "쿠폰"},
    {"columnId": "mileageUsage",      "headerDesc": "마일리지"},
    {"columnId": "ppayUsage",         "headerDesc": "P-pay"},
    {"columnId": "totalPriceQr",      "headerDesc": "QR 결제금액"},
    {"columnId": "totalPrice",        "headerDesc": "최종결제금액"},
    {"columnId": "totalPriceCard",    "headerDesc": "카드결제금액"},
    {"columnId": "paymentMeans",      "headerDesc": "결제수단"},
    {"columnId": "sales",             "headerDesc": "공급가액"},
    {"columnId": "surtax",            "headerDesc": "세액"},
    {"columnId": "paymentDt",         "headerDesc": "결제일"},
    {"columnId": "frameFeePrice",     "headerDesc": "수수료"},
    {"columnId": "frameRoyaltyPrice", "headerDesc": "로열티"},
    {"columnId": "cash",              "headerDesc": "투입현금"},
    {"columnId": "breakageIncome",    "headerDesc": "낙전"},
    {"columnId": "serviceCoin",       "headerDesc": "서비스코인"},
    {"columnId": "cancelDate",        "headerDesc": "취소 날짜"},
    {"columnId": "isCanceledRevenue", "headerDesc": "원거래 취소 여부"},
    {"columnId": "otherRevenueId",    "headerDesc": "원본/취소 거래 ID"},
    {"columnId": "savePoint",         "headerDesc": "CJ ONE 적립 포인트"},
    {"columnId": "redeemPoint",       "headerDesc": "CJ ONE 사용 포인트"},
    {"columnId": "approvalNo",        "headerDesc": "승인 번호"},
    {"columnId": "acquirerName",      "headerDesc": "매입사"},
    {"columnId": "transactionDate",   "headerDesc": "카드 결제 시간"},
]


def get_jwt_token(page, url: str, username: str, password: str) -> str:
    log(f"로그인: {url}")
    try:
        page.goto(url, timeout=30000)
        page.wait_for_load_state("networkidle")
    except Exception as e:
        log(f"[오류] 페이지 로드 실패: {e}")
        return ""

    if "/home" in page.url:
        log("이미 로그인됨 -> 토큰 추출")
    else:
        try:
            page.fill('input[type="text"]', username)
            page.fill('input[type="password"]', password)
            with page.expect_navigation(timeout=15000):
                page.click('button[type="submit"]')
            log(f"로그인 성공 -> {page.url}")
        except PWTimeout:
            log("[실패] 로그인 타임아웃")
            return ""
        except Exception as e:
            log(f"[오류] 로그인 중 오류: {e}")
            return ""

    token = page.evaluate("() => localStorage.getItem('token') || ''")
    return token or ""


def download_excel_api(cmsapi_url: str, token: str, country_code: str,
                       start_utc: str, end_utc: str, region: str = None) -> bytes:
    from datetime import datetime as _dt
    file_ts = _dt.now().strftime("%Y%m%d_%H%M%S")
    req_sql = {
        "boothNum": None,
        "countryCd": country_code,
        "paymentEndDate": end_utc,
        "paymentStartDate": start_utc,
        "storeName": None,
    }
    if region:
        req_sql["region"] = region
    body = json.dumps({
        "excelCellInfo": EXCEL_COLUMNS,
        "excelEnumId": "XLSX011",
        "exlFileNm": f"RevenueManagement_{file_ts}.xlsx",
        "sheetName": "Sheet1",
        "reqSql": req_sql,
    }, ensure_ascii=False).encode("utf-8")

    req = urllib.request.Request(
        f"{cmsapi_url}/v1/etc/excelDownload",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/plain, */*",
            "x-api-token": token,
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=300) as r:
        return r.read()


# ─────────────────────────────────────────────
# 3일 범위 국가별 다운로드
# ─────────────────────────────────────────────

def crawl_country_range(browser, country_code: str, country_info: dict,
                        username: str, password: str,
                        chunk_start: str, chunk_end: str) -> bool:
    url  = country_info["url"].rstrip("/")
    name = country_info["name"]

    log(f"[{country_code.upper()}] {name}  {chunk_start} ~ {chunk_end}")

    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    page = ctx.new_page()
    success = False

    try:
        token = get_jwt_token(page, url, username, password)
        if not token:
            log(f"  [실패] JWT 토큰 추출 실패")
            return False

        tz_offset  = country_info.get("timezone_offset", 9)
        start_utc  = date_to_utc(chunk_start, is_end=False, tz_offset=tz_offset)
        end_utc    = date_to_utc(chunk_end,   is_end=True,  tz_offset=tz_offset)
        cmsapi_url = country_info.get("cmsapi") or \
                     url.replace("http://cms.", "https://cmsapi.").replace("http://cms-", "https://cmsapi-")
        api_cc     = get_country_code(url)
        region     = country_info.get("region")

        log(f"  UTC: {start_utc} ~ {end_utc}")
        excel_data = download_excel_api(cmsapi_url, token, api_cc, start_utc, end_utc, region=region)

        start_tag = chunk_start.replace("-", "")
        end_tag   = chunk_end.replace("-", "")
        dest = RAW_DIR / f"photoism_{country_code}_{start_tag}_{end_tag}.xlsx"
        if len(excel_data) < 512:
            raise ValueError(f"응답 크기가 너무 작음 ({len(excel_data)} bytes) — 서버 오류 응답일 수 있음")
        dest.write_bytes(excel_data)
        log(f"  [OK] {dest.name}  ({len(excel_data):,} bytes)")
        success = True

    except urllib.error.HTTPError as e:
        log(f"  [오류] HTTP {e.code} - {e.reason}")
    except Exception as e:
        log(f"  [오류] {str(e)[:200]}")
    finally:
        ctx.close()

    return success


def crawl_chunk(chunk_start_str: str, chunk_end_str: str) -> dict:
    config    = load_config()
    photoism  = config.get("photoism", {})
    username  = photoism.get("username", "")
    password  = photoism.get("password", "")
    countries = photoism.get("countries", {})

    RAW_DIR.mkdir(exist_ok=True)

    results = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)

        for code, info in countries.items():
            start_tag = chunk_start_str.replace("-", "")
            end_tag   = chunk_end_str.replace("-", "")
            dest = RAW_DIR / f"photoism_{code}_{start_tag}_{end_tag}.xlsx"
            if dest.exists() and dest.stat().st_size >= 512:
                log(f"[{code.upper()}] 이미 존재 -> 스킵 ({dest.name})")
                results[code] = True
                continue

            for attempt in range(1, 4):
                ok = crawl_country_range(browser, code, info, username, password,
                                         chunk_start_str, chunk_end_str)
                if ok:
                    results[code] = True
                    break
                if attempt < 3:
                    log(f"  [{code.upper()}] 실패 - {attempt}/3 재시도 (15초 후)")
                    time.sleep(15)
            else:
                results[code] = False
                log(f"  [{code.upper()}] 3회 시도 후 최종 실패")

            if COUNTRY_DELAY > 0:
                time.sleep(COUNTRY_DELAY)

        browser.close()

    success_list = [c for c, ok in results.items() if ok]
    fail_list    = [c for c, ok in results.items() if not ok]
    log(f"청크 완료: 성공 {len(success_list)}개, 실패 {len(fail_list)}개")
    if fail_list:
        log(f"  실패 국가: {', '.join(fail_list)}")

    return results


# ─────────────────────────────────────────────
# ingest 실행
# ─────────────────────────────────────────────

def run_ingest():
    log("ingest 시작 (raw_photoism -> master_photoism.csv)...")
    try:
        result = subprocess.run(
            [sys.executable, str(BASE_DIR / "photoism_ingest.py")],
            cwd=str(BASE_DIR),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=300,
        )
        if result.stdout:
            for line in result.stdout.strip().splitlines():
                log(f"  [ingest] {line}")
        if result.returncode != 0 and result.stderr:
            log(f"  [ingest 오류] {result.stderr[:300]}")
        else:
            log("  ingest 완료 [OK]")
    except subprocess.TimeoutExpired:
        log("  [ingest] 타임아웃 (5분 초과)")
    except Exception as e:
        log(f"  [ingest 오류] {e}")


# ─────────────────────────────────────────────
# 메인
# ─────────────────────────────────────────────

def main():
    args = sys.argv[1:]

    if args and args[0] == "--status":
        print_status()
        return

    if args and args[0] == "--reset":
        args = args[1:]
        if STATE_FILE.exists():
            STATE_FILE.unlink()
            log("상태 파일 초기화")

    if len(args) >= 2:
        try:
            start_str = args[0]
            end_str   = args[1]
            date.fromisoformat(start_str)
            date.fromisoformat(end_str)
        except ValueError as e:
            print(f"날짜 형식 오류: {e}  (올바른 형식: YYYY-MM-DD)")
            sys.exit(1)
        state = init_state(start_str, end_str)

    elif len(args) == 0:
        state = load_state()
        if not state:
            print("사용법: python photoism_backfill.py YYYY-MM-DD YYYY-MM-DD")
            print("       python photoism_backfill.py --status")
            sys.exit(1)

    else:
        print("사용법: python photoism_backfill.py [시작날짜] [종료날짜]")
        sys.exit(1)

    if state.get("completed"):
        log(f"백필 완료 ({state['start_date']} ~ {state['end_date']}). 할 일 없음.")
        print_status()
        return

    next_date = date.fromisoformat(state["next_date"])
    end_date  = date.fromisoformat(state["end_date"])

    chunk_start     = next_date
    chunk_end       = min(next_date + timedelta(days=CHUNK_DAYS - 1), end_date)
    chunk_start_str = chunk_start.isoformat()
    chunk_end_str   = chunk_end.isoformat()

    done  = state.get("done_chunks", 0)
    total = state.get("total_chunks", 1)
    log(f"\n{'='*50}")
    log(f"백필 진행: {done+1}/{total} 청크  ({chunk_start_str} ~ {chunk_end_str})")
    log(f"{'='*50}")

    state["last_run"] = datetime.now().isoformat()
    save_state(state)

    crawl_chunk(chunk_start_str, chunk_end_str)

    state["done_chunks"] = done + 1
    next_after = chunk_end + timedelta(days=1)

    if next_after > end_date:
        state["completed"] = True
        state["next_date"] = chunk_end_str
        log(f"[완료] 백필 완료! {state['start_date']} ~ {state['end_date']} 전체 수집 완료")
        run_ingest()
    else:
        state["next_date"] = next_after.isoformat()
        remaining = total - (done + 1)
        log(f"다음 실행 예정: {state['next_date']} ~  (남은 청크: {remaining}개)")

    save_state(state)
    print_status()


if __name__ == "__main__":
    main()
