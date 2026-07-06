# -*- coding: utf-8 -*-
"""포토이즘 거래 CMS 재동기화(덮어쓰기) — 하루(전 국가)씩 순차 재수집.

CMS 쪽에 나중에 추가/보정된 매출을 우리 데이터에 반영하기 위해, 지정 기간을
하루 단위로 다시 받아 raw_photoism 의 '같은 파일명'에 덮어쓴다(중복 파일 안 생김).
photoism_crawler.crawl_country 를 직접 호출 → main() 의 sm_daily 훅/일일 ingest는 안 돎.
전부 받은 뒤 photoism_ingest 로 START 이후를 한 번에 교체(덮어쓰기)한다.

재시작 대비: 완료한 날짜를 logs/reget_state.txt 에 기록해 이어받는다.

실행:
  python photoism_reget.py                          # 2026-06-01~07-01, 하루 간격 180초
  python photoism_reget.py 2026-06-01 2026-07-01 180
보안: 자격증명/토큰은 출력하지 않는다.
"""
import subprocess
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from playwright.sync_api import sync_playwright

import photoism_crawler as pc

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE_DIR = Path(__file__).parent
STATE_FILE = BASE_DIR / "logs" / "reget_state.txt"
COUNTRY_DELAY = 5   # 국가 간 대기(초)


def _daterange(a: date, b: date):
    d = a
    while d <= b:
        yield d
        d += timedelta(days=1)


def _done() -> set:
    if not STATE_FILE.exists():
        return set()
    return set(STATE_FILE.read_text(encoding="utf-8").split())


def _mark(ds: str):
    STATE_FILE.parent.mkdir(exist_ok=True)
    with open(STATE_FILE, "a", encoding="utf-8") as f:
        f.write(ds + "\n")


def main():
    a = sys.argv[1:]
    start = datetime.strptime(a[0], "%Y-%m-%d").date() if len(a) > 0 else date(2026, 6, 1)
    end = datetime.strptime(a[1], "%Y-%m-%d").date() if len(a) > 1 else date(2026, 7, 1)
    gap = int(a[2]) if len(a) > 2 else 180

    cfg = pc.load_config().get("photoism", {})
    user, pw, countries = cfg.get("username", ""), cfg.get("password", ""), cfg.get("countries", {})
    if not countries:
        pc.log("[재동기화] countries 설정 없음 — 중단")
        return

    days = list(_daterange(start, end))
    done = _done()
    todo = [d for d in days if d.isoformat() not in done]
    pc.log(f"### 포토이즘 재동기화: {start}~{end} · {len(countries)}개국 · 하루간격 {gap}s "
           f"(전체 {len(days)}일 중 남은 {len(todo)}일) ###")

    for i, d in enumerate(todo):
        ds = d.isoformat()
        ok = 0
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            for code, info in countries.items():
                for attempt in range(1, 4):
                    try:
                        if pc.crawl_country(browser, code, info, user, pw, ds):
                            ok += 1
                            break
                    except Exception as ex:
                        pc.log(f"[{code.upper()}] {ds} 오류: {str(ex)[:80]}")
                    if attempt < 3:
                        time.sleep(15)
                if COUNTRY_DELAY:
                    time.sleep(COUNTRY_DELAY)
            browser.close()
        _mark(ds)
        pc.log(f"[재동기화] {ds} 완료 {ok}/{len(countries)}개국  ({i + 1}/{len(todo)})")
        if i < len(todo) - 1:
            time.sleep(gap)

    pc.log(f"### 재수집 끝 — {start} 이후 재인제스트(덮어쓰기) 시작 ###")
    subprocess.run([sys.executable, str(BASE_DIR / "photoism_ingest.py"), start.isoformat()],
                   cwd=str(BASE_DIR))
    pc.log("### 포토이즘 재동기화 완료 ###")


if __name__ == "__main__":
    main()
