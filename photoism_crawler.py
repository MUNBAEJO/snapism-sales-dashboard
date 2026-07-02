"""
포토이즘 CMS 30개국 매출 자동 수집 크롤러
- UI 로그인 후 JWT 토큰 추출
- cmsapi 직접 호출로 정확한 날짜 데이터 다운로드

실행: python photoism_crawler.py [YYYY-MM-DD]
  날짜 미지정 시 전날 데이터 자동 다운로드
"""
import json
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime, timedelta, timezone
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

BASE_DIR    = Path(__file__).parent
CONFIG_FILE = BASE_DIR / "config.json"
RAW_DIR     = BASE_DIR / "raw_photoism"
LOG_DIR     = BASE_DIR / "logs"

KST = timezone(timedelta(hours=9))


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
    with open(LOG_DIR / "photoism_crawler.log", "a", encoding="utf-8") as f:
        f.write(line + "\n")


def date_to_utc(date_str: str, is_end: bool, tz_offset: int = 9) -> str:
    """YYYY-MM-DD → UTC ISO 문자열 (국가 시간대 기준)
    시작: 00:00:00 현지 → UTC
    종료: 23:59:59 현지 → UTC
    tz_offset: UTC 기준 시간 오프셋 (기본 9 = KST, 미국은 -5 = EST)
    """
    local_tz = timezone(timedelta(hours=tz_offset))
    if is_end:
        local_dt = datetime.strptime(f"{date_str} 23:59:59", "%Y-%m-%d %H:%M:%S").replace(tzinfo=local_tz)
    else:
        local_dt = datetime.strptime(f"{date_str} 00:00:00", "%Y-%m-%d %H:%M:%S").replace(tzinfo=local_tz)
    utc_dt = local_dt.astimezone(timezone.utc)
    ms = "999" if is_end else "000"
    return utc_dt.strftime(f"%Y-%m-%dT%H:%M:%S.{ms}Z")


def get_cmsapi_url(cms_url: str) -> str:
    """CMS URL → cmsapi URL 변환 (항상 HTTPS)
    http(s)://cms-jp.seobuk.kr → https://cmsapi-jp.seobuk.kr
    http(s)://cms.seobuk.kr   → https://cmsapi.seobuk.kr
    http(s)://cms.photoism.cn → https://cmsapi.photoism.cn
    """
    url = cms_url.replace("http://", "https://").replace("https://cms.", "https://cmsapi.").replace("https://cms-", "https://cmsapi-")
    return url


def get_country_code(cms_url: str) -> str:
    """CMS URL에서 국가코드 추출 (대문자)"""
    import re
    m = re.search(r'cms-([a-z]+)\.seobuk', cms_url)
    if m:
        return m.group(1).upper()
    if 'photoism.cn' in cms_url:
        return 'CN'
    return 'KR'


# 엑셀 컬럼 정의 (실제 API columnId 기준, 한국어 헤더)
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


def get_jwt_token(page, url: str, username: str, password: str, country_code: str) -> str:
    """Playwright로 로그인 후 JWT 토큰 추출"""
    log(f"로그인: {url}")
    try:
        page.goto(url, timeout=30000)
        page.wait_for_load_state("networkidle")
    except Exception as e:
        log(f"[오류] 페이지 로드 실패: {e}")
        return ""

    if "/home" in page.url:
        log("이미 로그인됨 → 토큰 추출")
    else:
        try:
            page.fill('input[type="text"]', username)
            page.fill('input[type="password"]', password)
            with page.expect_navigation(timeout=15000):
                page.click('button[type="submit"]')
            log(f"로그인 성공 → {page.url}")
        except PWTimeout:
            log("[실패] 로그인 타임아웃")
            return ""
        except Exception as e:
            log(f"[오류] 로그인 중 오류: {e}")
            return ""

    # localStorage에서 JWT 토큰 추출
    token = page.evaluate("() => localStorage.getItem('token') || ''")
    return token or ""


def download_excel_api(cmsapi_url: str, token: str, country_code: str,
                       start_utc: str, end_utc: str, region: str = None) -> bytes:
    """cmsapi 직접 호출로 엑셀 다운로드
    region: 일부 국가(미국 등)에서 필요한 타임존 지역 코드 (예: "EST")
    """
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


def crawl_country(browser, country_code, country_info, username, password, date_str):
    """단일 국가 크롤링"""
    url  = country_info["url"].rstrip("/")
    name = country_info["name"]

    log(f"\n{'='*45}")
    log(f"[{country_code.upper()}] {name}  ({url})")
    log(f"{'='*45}")

    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    page = ctx.new_page()
    success = False

    try:
        # 1. 로그인 + JWT 토큰 추출
        token = get_jwt_token(page, url, username, password, country_code)
        if not token:
            log("[실패] JWT 토큰 추출 실패")
            return False

        log(f"토큰 확보 ({token[:30]}...)")

        # 2. UTC 날짜 계산 (국가별 시간대 적용)
        tz_offset = country_info.get("timezone_offset", 9)   # 기본 KST(+9)
        start_utc = date_to_utc(date_str, is_end=False, tz_offset=tz_offset)
        end_utc   = date_to_utc(date_str, is_end=True,  tz_offset=tz_offset)
        log(f"날짜 범위: {start_utc} ~ {end_utc}  (UTC{tz_offset:+d})")

        # 3. cmsapi URL (config에서 직접 읽음) 및 국가코드 계산
        cmsapi_url   = country_info.get("cmsapi") or get_cmsapi_url(url)
        api_cc       = get_country_code(url)
        region       = country_info.get("region")  # 일부 국가만 필요 (예: US → "EST")

        # 4. 엑셀 다운로드 API 호출
        log("엑셀 다운로드 시작 (API)...")
        excel_data = download_excel_api(cmsapi_url, token, api_cc, start_utc, end_utc, region=region)

        dest = RAW_DIR / f"photoism_{country_code}_{date_str.replace('-', '')}.xlsx"
        if len(excel_data) < 512:
            raise ValueError(f"응답 크기가 너무 작음 ({len(excel_data)} bytes) — 서버 오류 응답일 수 있음")
        dest.write_bytes(excel_data)
        log(f"다운로드 완료: {dest.name} ({len(excel_data):,} bytes)")
        success = True

    except urllib.error.HTTPError as e:
        log(f"[오류] {country_code}: HTTP {e.code} — {e.reason}")
    except Exception as e:
        log(f"[오류] {country_code}: {str(e)[:200]}")
    finally:
        ctx.close()

    return success


def main():
    # 날짜 결정
    try:
        if len(sys.argv) == 2:
            date_str = sys.argv[1]
            datetime.strptime(date_str, "%Y-%m-%d")
        else:
            date_str = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    except ValueError as e:
        print(f"날짜 형식 오류: {e}  (올바른 형식: YYYY-MM-DD)")
        sys.exit(1)

    log(f"크롤링 대상: {date_str}")

    config   = load_config()
    photoism = config.get("photoism", {})
    username = photoism.get("username", "")
    password = photoism.get("password", "")
    countries = photoism.get("countries", {})

    if not countries:
        log("[오류] config.json에 photoism.countries 설정 없음")
        sys.exit(1)

    RAW_DIR.mkdir(exist_ok=True)
    LOG_DIR.mkdir(exist_ok=True)

    results = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)

        for code, info in countries.items():
            for attempt in range(1, 4):
                ok = crawl_country(browser, code, info, username, password, date_str)
                if ok:
                    results[code] = True
                    break
                if attempt < 3:
                    log(f"[{code.upper()}] 실패 — {attempt}/3 재시도 (30초 후...)")
                    time.sleep(30)
            else:
                results[code] = False
                log(f"[{code.upper()}] 3회 시도 후 최종 실패")

        browser.close()

    # 결과 요약
    log(f"\n{'='*45}")
    log("크롤링 완료 요약")
    success_list = [c for c, ok in results.items() if ok]
    fail_list    = [c for c, ok in results.items() if not ok]
    log(f"  성공 ({len(success_list)}개): {', '.join(success_list)}")
    if fail_list:
        log(f"  실패 ({len(fail_list)}개): {', '.join(fail_list)}")
    log(f"{'='*45}")

    if success_list:
        log("\n데이터 누적 처리 시작 (photoism_ingest.py)...")
        import subprocess
        subprocess.run(
            [sys.executable, str(BASE_DIR / "photoism_ingest.py"), date_str],
            cwd=str(BASE_DIR),
        )

    # SM 촬영수 일일 수집을 독립 프로세스로 분리 실행(fire-and-forget).
    # 이 크롤러의 실행시간 제한(PT1H)·종료코드와 무관하게 SM 데이터도 매일 갱신되도록.
    # (전용 작업 스케줄러 등록은 관리자 권한이 필요해, 매일 도는 이 작업에 얹어 트리거)
    try:
        import subprocess, os
        flags = (subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP) if os.name == "nt" else 0
        subprocess.Popen(
            [sys.executable, str(BASE_DIR / "sm_daily.py")],
            cwd=str(BASE_DIR), creationflags=flags, close_fds=True,
        )
        log("SM 촬영수 일일 수집(sm_daily.py) 분리 실행 시작")
    except Exception as e:
        log(f"SM 일일 수집 실행 실패: {e}")

    if fail_list:
        sys.exit(1)


if __name__ == "__main__":
    main()
