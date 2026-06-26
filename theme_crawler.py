# -*- coding: utf-8 -*-
"""포토이즘 CMS 테마 리포트(매출정보 프레임) 다운로더 — TITLE/THEME/FRAME 집계.
거래 크롤러(photoism_crawler.py)와 동일한 로그인/엔드포인트, excelEnumId=XLSX012.
서버 부담 방지를 위해 3일치씩(기본) 청크로 받고 청크 사이에 딜레이를 둔다.

실행:
  python theme_crawler.py 2025-12-31 2026-06-25        # 기간 백필(국내)
  python theme_crawler.py 2026-06-22 2026-06-24 5 kr   # start end chunk국가

보안: 요청 본문(자격증명 포함)은 절대 로깅하지 않는다.
"""
import json
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime, timedelta, timezone

from playwright.sync_api import sync_playwright

# 한글 콘솔(cp949)에서 '—','→' 등 출력 시 UnicodeEncodeError 방지
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE_DIR    = Path(__file__).parent
CONFIG_FILE = BASE_DIR / "config.json"
OUT_DIR     = BASE_DIR / "raw_theme"
LOG_DIR     = BASE_DIR / "logs"

# XLSX012 테마 리포트 컬럼 (탐지된 실제 columnId)
THEME_COLUMNS = [
    {"columnId": "titleName",           "headerDesc": "TITLE"},
    {"columnId": "themeName",           "headerDesc": "THEME"},
    {"columnId": "frameName",           "headerDesc": "FRAME"},
    {"columnId": "countryNmEn",         "headerDesc": "국가"},
    {"columnId": "framePrice",          "headerDesc": "프레임 금액"},
    {"columnId": "totalCouponDiscount", "headerDesc": "쿠폰 할인"},
    {"columnId": "totalServiceCoin",    "headerDesc": "서비스 코인"},
    {"columnId": "totalPrice",          "headerDesc": "최종 결제 금액"},
    {"columnId": "totalOrderCount",     "headerDesc": "주문횟수"},
    {"columnId": "sumSavePoint",        "headerDesc": "총 CJ ONE 적립 포인트"},
    {"columnId": "sumRedeemPoint",      "headerDesc": "총 CJ ONE 사용 포인트"},
]


def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    LOG_DIR.mkdir(exist_ok=True)
    with open(LOG_DIR / "theme_crawler.log", "a", encoding="utf-8") as f:
        f.write(line + "\n")


def to_utc(date_str, is_end, off=9):
    tz = timezone(timedelta(hours=off))
    t = "23:59:59" if is_end else "00:00:00"
    dt = datetime.strptime(f"{date_str} {t}", "%Y-%m-%d %H:%M:%S").replace(tzinfo=tz)
    u = dt.astimezone(timezone.utc)
    ms = "999" if is_end else "000"
    return u.strftime(f"%Y-%m-%dT%H:%M:%S.{ms}Z")


def get_token(url, user, pw):
    """Playwright로 로그인해 JWT 토큰만 받고 브라우저는 즉시 닫는다(장시간 점유 방지)."""
    with sync_playwright() as pw_ctx:
        b = pw_ctx.chromium.launch(headless=True)
        page = b.new_context(viewport={"width": 1440, "height": 900}).new_page()
        try:
            page.goto(url, timeout=40000)
            page.wait_for_load_state("networkidle")
            if "/home" not in page.url:
                page.fill('input[type="text"]', user)
                page.fill('input[type="password"]', pw)
                try:
                    with page.expect_navigation(timeout=20000):
                        page.click('button[type="submit"]')
                except Exception:
                    pass
            return page.evaluate("() => localStorage.getItem('token') || ''") or ""
        finally:
            b.close()


def download_chunk(cmsapi, token, cc, s_date, e_date, off):
    """[s_date, e_date] 구간 테마 리포트 xlsx 바이트 반환. frameTypes=All(전체)."""
    body = json.dumps({
        "excelCellInfo": THEME_COLUMNS,
        "excelEnumId": "XLSX012",
        "exlFileNm": f"theme_{cc}_{s_date}_{e_date}.xlsx",
        "sheetName": "Sheet1",
        "reqSql": {
            "countryCd": cc, "frameId": None, "frameTypes": ["All"], "layoutId": None,
            "paymentStartDate": to_utc(s_date, False, off),
            "paymentEndDate":   to_utc(e_date, True, off),
            "localStartDate": f"{s_date}T00:00:00",
            "localEndDate":   f"{e_date}T23:59:59",
            "storeName": None, "themeId": None, "titleId": None,
        },
    }, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        f"{cmsapi}/v1/etc/excelDownload", data=body,
        headers={"Content-Type": "application/json",
                 "Accept": "application/json, text/plain, */*",
                 "x-api-token": token},
        method="POST")
    with urllib.request.urlopen(req, timeout=300) as r:
        return r.read()


def daterange_chunks(start, end, days):
    cur = start
    while cur <= end:
        chunk_end = min(cur + timedelta(days=days - 1), end)
        yield cur, chunk_end
        cur = chunk_end + timedelta(days=1)


def _dest(cc, s, e):
    return OUT_DIR / f"theme_{cc}_{s.strftime('%Y%m%d')}_{e.strftime('%Y%m%d')}.xlsx"


def crawl_country(code, info, user, pw, chunks, delay):
    """한 국가의 모든 청크를 다운로드. (성공, 건너뜀, 실패) 반환."""
    url = info["url"].rstrip("/")
    cmsapi = info.get("cmsapi") or url.replace("http://", "https://").replace("https://cms.", "https://cmsapi.").replace("https://cms-", "https://cmsapi-")
    off = info.get("timezone_offset", 9)
    api_cc = code.upper()

    pending = [(s, e) for (s, e) in chunks
               if not (_dest(code, s, e).exists() and _dest(code, s, e).stat().st_size > 512)]
    if not pending:
        log(f"[{api_cc}] 전체 완료 — 건너뜀")
        return 0, len(chunks), 0

    token = get_token(url, user, pw)
    if not token:
        log(f"[{api_cc}] 로그인 실패 — 건너뜀")
        return 0, 0, len(pending)
    log(f"[{api_cc}] 로그인 OK · 남은 {len(pending)}/{len(chunks)}청크")

    ok = skip = fail = 0
    for s, e in chunks:
        dest = _dest(code, s, e)
        if dest.exists() and dest.stat().st_size > 512:
            skip += 1
            continue
        s_str, e_str = s.strftime("%Y-%m-%d"), e.strftime("%Y-%m-%d")
        for attempt in range(1, 3):
            try:
                data = download_chunk(cmsapi, token, api_cc, s_str, e_str, off)
                if len(data) < 512:
                    raise ValueError(f"too small ({len(data)}b)")
                dest.write_bytes(data)
                log(f"[{api_cc}] {s_str}~{e_str}  {len(data):,}b")
                ok += 1
                break
            except urllib.error.HTTPError as ex:
                if ex.code in (401, 403) and attempt == 1:
                    token = get_token(url, user, pw)
                    continue
                log(f"[{api_cc}] {s_str}~{e_str} 실패 HTTP {ex.code}")
                fail += 1
                break
            except Exception as ex:
                log(f"[{api_cc}] {s_str}~{e_str} 실패 {str(ex)[:100]}")
                fail += 1
                break
        time.sleep(delay)  # 서버 부담 방지 — 한 번에 하나씩 순차 처리
    return ok, skip, fail


def main():
    args = sys.argv[1:]
    if len(args) < 2:
        print("사용법: python theme_crawler.py START END [CHUNK일=31] [국가=all|kr,jp,..] [딜레이초=15]")
        sys.exit(1)
    start = datetime.strptime(args[0], "%Y-%m-%d").date()
    end   = datetime.strptime(args[1], "%Y-%m-%d").date()
    chunk = int(args[2]) if len(args) > 2 else 31
    cc_spec = (args[3] if len(args) > 3 else "all").lower()
    delay = int(args[4]) if len(args) > 4 else 15

    cfg = json.load(open(CONFIG_FILE, encoding="utf-8"))
    p = cfg["photoism"]
    user, pw = p["username"], p["password"]
    countries = p["countries"]
    codes = list(countries.keys()) if cc_spec == "all" else [c.strip() for c in cc_spec.split(",")]

    OUT_DIR.mkdir(exist_ok=True)
    chunks = list(daterange_chunks(start, end, chunk))
    log(f"=== 테마 백필: {len(codes)}개국 × {len(chunks)}청크({chunk}일) · 간격 {delay}s · {start}~{end} ===")

    G_ok = G_skip = G_fail = 0
    for code in codes:
        info = countries.get(code)
        if not info:
            log(f"[{code.upper()}] config 없음 — 건너뜀")
            continue
        try:
            o, s, f = crawl_country(code, info, user, pw, chunks, delay)
        except Exception as ex:
            log(f"[{code.upper()}] 국가 처리 오류: {str(ex)[:120]}")
            o, s, f = 0, 0, len(chunks)
        G_ok += o; G_skip += s; G_fail += f

    log(f"=== 전체 완료: 성공 {G_ok} · 건너뜀 {G_skip} · 실패 {G_fail} → {OUT_DIR} ===")


if __name__ == "__main__":
    main()
