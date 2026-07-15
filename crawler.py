"""
스내피즘 어드민 자동 CSV 다운로드 크롤러

실행: python crawler.py [YYYY-MM-DD]
  날짜 미지정시 전날 데이터를 자동 다운로드
  예) python crawler.py 2026-05-27
"""
import json
import sys
import subprocess
import time
from pathlib import Path
from datetime import datetime, timedelta
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

BASE_DIR = Path(__file__).parent
CONFIG_FILE = BASE_DIR / "config.json"
RAW_DIR = BASE_DIR / "raw"
LOG_DIR = BASE_DIR / "logs"


def load_config():
    if not CONFIG_FILE.exists():
        print("[오류] config.json이 없습니다.")
        sys.exit(1)
    with open(CONFIG_FILE, encoding="utf-8") as f:
        return json.load(f)


def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    LOG_DIR.mkdir(exist_ok=True)
    with open(LOG_DIR / "crawler.log", "a", encoding="utf-8") as f:
        f.write(line + "\n")


def login(page, url, username, password, site_key):
    log(f"로그인: {url}/login")
    page.goto(f"{url}/login", timeout=20000)
    page.wait_for_load_state("networkidle")

    page.fill('input[name="user_id"]', username)
    page.fill('input[name="password"]', password)

    try:
        with page.expect_navigation(timeout=15000):
            page.click('button[type="submit"]')
        log(f"로그인 성공 -> {page.url}")
        return True
    except PWTimeout:
        page.screenshot(path=str(LOG_DIR / f"{site_key}_login_fail.png"))
        log(f"[실패] 로그인 타임아웃. 스크린샷 저장됨.")
        return False


def set_date_range(page, start_str, end_str):
    """날짜 범위 설정. start_str ~ end_str (YYYY-MM-DD 형식)"""
    inputs = page.locator("input[placeholder='YYYY-MM-DD']")
    count = inputs.count()
    if count < 2:
        log(f"[주의] 날짜 input이 {count}개만 발견됨")
        return False

    for i, date_val in enumerate([start_str, end_str]):
        inp = inputs.nth(i)
        inp.click()
        inp.fill(date_val)
        inp.press("Tab")
        page.wait_for_timeout(300)

    log(f"날짜 설정 완료: {start_str} ~ {end_str}")
    return True


def click_search(page):
    btn = page.locator("button:has-text('조회')").first
    btn.click()
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(2000)

    # 결과 건수 확인
    try:
        count_text = page.locator("text=/총 [0-9,]+개/").first.inner_text()
        log(f"검색 결과: {count_text}")
    except Exception:
        pass


def download_csv(page, site_key, start_str, end_str):
    if start_str == end_str:
        filename = f"{site_key}_{start_str.replace('-', '')}.csv"
    else:
        filename = f"{site_key}_{start_str.replace('-', '')}_{end_str.replace('-', '')}.csv"
    dest = RAW_DIR / filename

    log("CSV 다운로드 시작...")
    try:
        with page.expect_download(timeout=60000) as dl_info:
            page.locator("button:has-text('CSV 다운로드')").click()
        dl = dl_info.value
        dl.save_as(str(dest))
        size = dest.stat().st_size
        log(f"다운로드 완료: {filename} ({size:,} bytes)")
        return True
    except PWTimeout:
        page.screenshot(path=str(LOG_DIR / f"{site_key}_download_fail.png"))
        log(f"[실패] 다운로드 타임아웃. 스크린샷 저장됨.")
        return False
    except Exception as e:
        log(f"[실패] 다운로드 오류: {e}")
        return False


def crawl_site(browser, site_key, config, start_str, end_str):
    site = config.get(site_key, {})
    username = site.get("username", "")
    password = site.get("password", "")

    if username in ("여기에_아이디_입력", "", None):
        log(f"[{site_key}] 계정 정보 없음. 건너뜀.")
        return False

    log(f"\n{'='*45}")
    log(f"[{site_key.upper()}] {site['url']}")
    log(f"{'='*45}")

    ctx = browser.new_context(
        accept_downloads=True,
        viewport={"width": 1440, "height": 900},
    )
    page = ctx.new_page()
    success = False

    try:
        # 1. 로그인
        if not login(page, site["url"], username, password, site_key):
            return False

        # 2. 매출 통계 페이지 이동
        stats_url = f"{site['url']}/store-management/sales-statistics"
        page.goto(stats_url, timeout=20000)
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(1000)

        # 3. 초기화 버튼 클릭 → 국가/상품구분 등 이전 세션 필터 리셋
        try:
            reset_btn = page.locator("button:has-text('초기화')").first
            if reset_btn.is_visible():
                reset_btn.click()
                page.wait_for_timeout(600)
                log("필터 초기화 완료")
        except Exception:
            pass

        # 4. 날짜 범위 설정
        if not set_date_range(page, start_str, end_str):
            log("[주의] 날짜 자동 설정 실패. 기본 조건으로 진행.")

        # 5. 조회 클릭
        click_search(page)

        # 6. CSV 다운로드
        success = download_csv(page, site_key, start_str, end_str)

    except Exception as e:
        log(f"[오류] {site_key}: {e}")
        try:
            page.screenshot(path=str(LOG_DIR / f"{site_key}_error.png"))
        except Exception:
            pass
    finally:
        ctx.close()

    return success


def _refresh_rates():
    """일일 크롤 전에 환율을 갱신(update_rates.py)한다.
    환율 갱신을 scheduler.py 데몬에만 두면 데몬이 죽었을 때 환율이 묵는다
    (실제로 2026-06-08 데몬 중단으로 환율이 3일 묵음). 그래서 매일 도는
    Windows 작업이 실행하는 이 크롤러가 직접 호출한다. 실패해도 크롤은 계속한다."""
    try:
        log("환율 갱신 중 (update_rates.py)...")
        r = subprocess.run(
            [sys.executable, str(BASE_DIR / "update_rates.py")],
            cwd=str(BASE_DIR), capture_output=True, text=True, timeout=60,
        )
        if r.returncode == 0:
            tail = [x for x in (r.stdout or "").strip().splitlines() if x.strip()]
            log("환율 갱신 완료" + (f" — {tail[-1].strip()}" if tail else ""))
        else:
            log(f"[경고] 환율 갱신 실패(크롤은 계속): {(r.stderr or '')[:160]}")
    except Exception as e:
        log(f"[경고] 환율 갱신 오류(크롤은 계속): {e}")


def main():
    _refresh_rates()    # 데몬에 의존하지 않고 크롤마다 환율 최신화
    # 날짜 결정
    # 사용법:
    #   python crawler.py              → 어제 하루치
    #   python crawler.py 2026-05-27  → 특정 하루
    #   python crawler.py 2026-05-21 2026-05-27  → 날짜 범위
    try:
        if len(sys.argv) == 3:
            start_str = sys.argv[1]
            end_str = sys.argv[2]
            datetime.strptime(start_str, "%Y-%m-%d")
            datetime.strptime(end_str, "%Y-%m-%d")
        elif len(sys.argv) == 2:
            start_str = end_str = sys.argv[1]
            datetime.strptime(start_str, "%Y-%m-%d")
        else:
            # 기본: '최근 N일 롤링 재수집'. 판매 후 나중에 생긴 취소·정정을 다시 받아
            # ingest.py(keep=last)가 옛 행을 덮어쓰게 함. 어제까지만(오늘은 미확정).
            # N = config.json schedule.sales_lookback_days (기본 14). CMS는 기간 1건으로 내려줌(요청 1회).
            try:
                _roll = max(1, int(load_config().get("schedule", {}).get("sales_lookback_days", 14)))
            except Exception:
                _roll = 14
            end_str   = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
            start_str = (datetime.now() - timedelta(days=_roll)).strftime("%Y-%m-%d")
    except ValueError as e:
        print(f"날짜 형식 오류: {e}  (올바른 형식: YYYY-MM-DD)")
        sys.exit(1)

    log(f"크롤링 대상: {start_str} ~ {end_str}")

    config = load_config()
    RAW_DIR.mkdir(exist_ok=True)
    LOG_DIR.mkdir(exist_ok=True)

    results = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        for site_key in ["kr", "cn"]:
            for attempt in range(1, 4):  # 최대 3회 시도
                ok = crawl_site(browser, site_key, config, start_str, end_str)
                if ok:
                    results[site_key] = True
                    break
                if attempt < 3:
                    log(f"[{site_key.upper()}] 실패 - {attempt}/3 재시도 (30초 후...)")
                    time.sleep(30)
            else:
                results[site_key] = False
                log(f"[{site_key.upper()}] 3회 시도 후 최종 실패")
        browser.close()

    # 결과 요약
    log("\n" + "="*45)
    log("크롤링 완료 요약")
    for site, ok in results.items():
        log(f"  {site.upper()}: {'성공' if ok else '실패'}")
    log("="*45)

    # 성공 건이 있으면 자동으로 ingest 실행
    if any(results.values()):
        log("\n데이터 누적 처리 시작 (ingest.py)...")
        subprocess.run(
            [sys.executable, str(BASE_DIR / "ingest.py")],
            cwd=str(BASE_DIR),
        )
    else:
        log("[주의] 다운로드된 파일이 없습니다. logs/ 폴더를 확인하세요.")

    # 일부 실패 시 exit code 1 → scheduler가 1시간 후 재시도 예약
    if not all(results.values()):
        sys.exit(1)


if __name__ == "__main__":
    main()
