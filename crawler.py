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


LOGIN_TRIES = 3          # 로그인 재시도 횟수 (20s → 40s → 60s)


def login(page, url, username, password, site_key):
    """로그인. **실패하면 시간을 늘려 다시 시도한다**(2026-08-13 추가).

    ★포토이즘 쪽에서 같은 자리가 사고를 냈다 — 로그인 한 번 실패로 스페인이
      사흘 통째로 비었고, 롤링 창이 지나가 영구 구멍이 됐다. 여기는 롤링이
      14일이라 훨씬 너그럽지만, 2025-05-10~29 처럼 19일이 빈 적이 실제로 있다.
    ★goto 예외를 잡지 않고 있었다 — 페이지 로드가 실패하면 그대로 터졌다.
    """
    for i in range(LOGIN_TRIES):
        timeout = 20000 * (i + 1)
        log(f"로그인: {url}/login" + (f"  (재시도 {i + 1}/{LOGIN_TRIES} · {timeout // 1000}s)" if i else ""))
        try:
            page.goto(f"{url}/login", timeout=timeout)
            page.wait_for_load_state("networkidle")
            # ★서버에 /login 라우트가 없는 SPA(중국 admin 은 Vite/React·Tengine)면 여기가
            #   404 라 로그인 폼이 없다 — 폼은 루트 '/'로 들어가야 SPA 가 클라이언트
            #   라우팅으로 그린다(2026-08-27 확인). 폼이 안 뜨면 루트로 재진입한다.
            #   ★KR 은 /login 이 폼을 바로 주므로 아래 대기가 즉시 통과 → 재진입을 안 탄다
            #     (두 곳의 필드·버튼 셀렉터가 완전히 같다: user_id·password·submit).
            try:
                page.wait_for_selector('input[name="user_id"]', timeout=6000)
            except PWTimeout:
                log(f"  {url}/login 에 로그인 폼 없음 → 루트로 재진입")
                page.goto(f"{url}/", timeout=timeout)
                page.wait_for_load_state("networkidle")
            page.fill('input[name="user_id"]', username)
            page.fill('input[name="password"]', password)
            with page.expect_navigation(timeout=15000 * (i + 1)):
                page.click('button[type="submit"]')
            log(f"로그인 성공 -> {page.url}")
            return True
        except PWTimeout:
            log(f"[실패] 로그인 타임아웃 ({i + 1}/{LOGIN_TRIES})")
        except Exception as e:                      # noqa: BLE001 — 페이지 로드 실패 등
            log(f"[오류] 로그인 중 오류 ({i + 1}/{LOGIN_TRIES}): {str(e).splitlines()[0][:120]}")
        if i < LOGIN_TRIES - 1:
            time.sleep(5 * (i + 1))
    try:
        page.screenshot(path=str(LOG_DIR / f"{site_key}_login_fail.png"))
        log("[실패] 로그인 최종 실패. 스크린샷 저장됨.")
    except Exception:                               # noqa: BLE001
        log("[실패] 로그인 최종 실패.")
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
    except Exception as e:                            # noqa: BLE001
        # ★결과 건수를 못 읽었다고 그냥 넘어가면 **0건 조회**도 그대로 다운로드로
        #   이어져 빈/부분 CSV 가 raw/ 에 떨어진다. ingest 는 mtime 최신이라며
        #   그걸 채택한다 → 그날 매출이 조용히 줄어든다. 최소한 남긴다(2026-08-20).
        log(f"[경고] 조회 결과 건수를 읽지 못했습니다 — 빈 CSV 를 받을 수 있습니다: {e}")


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
    ingest_failed = False
    if any(results.values()):
        log("\n데이터 누적 처리 시작 (ingest.py)...")
        # ★★적재가 죽어도 크롤러는 성공으로 끝나고 있었다 (2026-08-21 실제 사고).
        #   그날 스내피즘 적재가 MemoryError 로 죽었는데(커밋 한도 초과),
        #   `subprocess.run` 의 반환값을 받지도 `check=True` 를 주지도 않아
        #   scheduler.log 에는 "크롤러 완료" 로 찍혔고 실패 메일도 안 갔다.
        #   원장이 하루치 통째로 비었는데 아무도 몰랐다 — 파일 시각을 보고서야 찾았다.
        #   받아 온 CSV 가 멀쩡해도 **적재가 실패하면 그날 데이터는 없다.**
        _r = subprocess.run(
            [sys.executable, str(BASE_DIR / "ingest.py")],
            cwd=str(BASE_DIR),
        )
        if _r.returncode != 0:
            ingest_failed = True
            log(f"[실패] 적재가 종료코드 {_r.returncode} 로 끝났어요 — "
                "받은 CSV 는 raw/ 에 있지만 **원장에는 안 들어갔어요.**")
    else:
        log("[주의] 다운로드된 파일이 없습니다. logs/ 폴더를 확인하세요.")

    # 일부 실패 시 exit code 1 → scheduler가 1시간 후 재시도 예약
    #   ★적재 실패도 같이 본다 — 다운로드가 다 됐어도 적재가 죽으면 그날 데이터는 없다.
    if ingest_failed or not all(results.values()):
        sys.exit(1)


if __name__ == "__main__":
    main()
