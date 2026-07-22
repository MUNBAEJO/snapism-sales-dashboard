"""스내피즘 어드민 매장·키오스크 목록 CSV 수집.

포토이즘 CMS(device_collect.py)와 어드민이 아예 달라 따로 받는다. 대신 스내피즘 쪽이
정보가 더 좋다 — 매장 목록에 **계약 기간(시작~종료)**과 **키오스크 ID 목록**이 있어서
포토이즘처럼 설치일을 S/N 에서 유추할 필요가 없다.

실행: python snapism_store_collect.py [kr|cn ...]
저장: data/devices/snapism_<site>_<page>_<YYYYMMDD>.csv
"""
import json
import sys
import time
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

from crawler import login, log

BASE_DIR    = Path(__file__).parent
CONFIG_FILE = BASE_DIR / "config.json"
DEST_DIR    = BASE_DIR / "data" / "devices"

DEFAULT_SITES = ["kr"]
PAGE_DELAY = 3

# 사이드바 메뉴 텍스트로 이동한다 — 경로가 바뀌어도 따라간다.
TARGETS = [
    {"key": "store",   "group": "매장 관리", "menu": "매장 등록 및 관리"},
    {"key": "kiosk",   "group": "매장 관리", "menu": "키오스크 등록 및 관리"},
]


def goto_menu(page, group, menu):
    """사이드바 그룹을 펼치고 하위 메뉴를 클릭한다."""
    try:
        page.get_by_text(group, exact=True).first.click(timeout=8000)
        page.wait_for_timeout(600)
    except Exception:
        pass    # 이미 펼쳐져 있으면 클릭이 실패할 수 있다
    page.get_by_text(menu, exact=True).first.click(timeout=10000)
    page.wait_for_load_state("networkidle")
    return page.url


def grab_csv(page, dest: Path) -> bool:
    try:
        with page.expect_download(timeout=90000) as dl:
            page.locator("button:has-text('CSV 다운로드')").first.click()
        dl.value.save_as(str(dest))
        log(f"저장: {dest.name} ({dest.stat().st_size:,} bytes)")
        return True
    except Exception as e:
        log(f"[오류] CSV 다운로드 실패: {str(e)[:200]}")
        return False


def collect(sites):
    cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    DEST_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now().strftime("%Y%m%d")
    ok, fail = [], []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        for site_key in sites:
            site = cfg.get(site_key)
            if not site:
                log(f"[건너뜀] {site_key}: config 에 없음")
                continue
            log(f"\n{'='*45}\n[{site_key.upper()}] {site['url']}\n{'='*45}")
            ctx = browser.new_context(viewport={"width": 1600, "height": 1000},
                                      accept_downloads=True)
            page = ctx.new_page()
            try:
                if not login(page, site["url"], site["username"], site["password"], site_key):
                    raise RuntimeError("로그인 실패")
                for t in TARGETS:
                    try:
                        url = goto_menu(page, t["group"], t["menu"])
                        log(f"{t['menu']} → {url}")
                        page.wait_for_timeout(1500)
                        dest = DEST_DIR / f"snapism_{site_key}_{t['key']}_{today}.csv"
                        (ok if grab_csv(page, dest) else fail).append(f"{site_key}/{t['key']}")
                    except Exception as e:
                        log(f"[오류] {t['menu']}: {str(e)[:200]}")
                        fail.append(f"{site_key}/{t['key']}")
                    time.sleep(PAGE_DELAY)
            except Exception as e:
                log(f"[오류] {site_key}: {str(e)[:200]}")
                fail.append(site_key)
            finally:
                ctx.close()
        browser.close()

    log(f"\n완료: 성공 {len(ok)} ({', '.join(ok)}) / 실패 {len(fail)} ({', '.join(fail)})")


if __name__ == "__main__":
    collect([s.lower() for s in sys.argv[1:]] or DEFAULT_SITES)
