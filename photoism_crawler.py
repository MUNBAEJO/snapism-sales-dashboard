"""
포토이즘 CMS 30개국 매출 자동 수집 크롤러
- UI 로그인 후 JWT 토큰 추출
- cmsapi 직접 호출로 정확한 날짜 데이터 다운로드

실행: python photoism_crawler.py [YYYY-MM-DD]
  날짜 미지정 시 전날 데이터 자동 다운로드
"""
import json
import os
import subprocess
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

# 롤링 재수집: 무인수 일일 실행 시 '어제'부터 최근 N일을 다시 받아 덮어쓴다.
# 목적은 시차 절단(실측상 없음)이 아니라 CMS '사후 매출'(뒤늦게 반영되는 정산분,
# 전 국가 공통 0.1~0.4%) 자동 보정. SM(sm_daily)과 동일한 롤링·덮어쓰기 방식.
LOOKBACK_DAYS = 3
COUNTRY_DELAY = 2   # 국가 간 대기(초) — 서버 부담 완화


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
    # ★★현지 결제시각 (2026-08-05 추가). CMS 화면의 '결제일 (지역)' 이고
    #   퀵사이트가 보여주는 값도 이것이다. `paymentDt` 는 시차가 반영 안 된 값이라
    #   미국처럼 시차 큰 나라에서 **같은 거래가 다른 날짜로 잡힌다**
    #   (KFA 2건: paymentDt 07-02 23:24 ↔ localPaymentDt 07-03 13:24).
    #   정산은 현지 기준이어야 하므로 이 열을 받아 `날짜` 산출에 쓴다.
    #   국가별 오프셋 표를 두지 않아도 30개국이 전부 자동으로 맞는다.
    {"columnId": "localPaymentDt",    "headerDesc": "결제일(지역)"},
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


LOGIN_TRIES = 3          # 로그인 재시도 횟수 (30s → 60s → 90s)


def get_jwt_token(page, url: str, username: str, password: str, country_code: str) -> str:
    """Playwright로 로그인 후 JWT 토큰 추출. **실패하면 시간을 늘려 다시 시도한다.**

    ★한 번 실패하면 그 나라의 **그 창(LOOKBACK_DAYS=3일) 전체가 빈 채로 굳는다.**
      다음 날 실행은 창이 하루 밀려 옛 날짜를 다시 안 받기 때문이다. 실제로
      스페인이 그렇게 2026-08-07~09 사흘이 비었고, 커버리지 점검 메일이 매일 왔다.
    ★호스트가 죽은 게 아니라 **30초 제한 앞뒤에서 오락가락**하는 것이다 —
      같은 날 09:07 성공 / 09:07 실패 / 10:19 실패 / 10:21 성공(37초 걸림).
      유럽 CMS 가 특히 느리다. 그래서 재시도할 때 제한도 같이 늘린다.
    """
    for i in range(LOGIN_TRIES):
        timeout = 30000 * (i + 1)
        log(f"로그인: {url}" + (f"  (재시도 {i + 1}/{LOGIN_TRIES} · {timeout // 1000}s)" if i else ""))
        try:
            page.goto(url, timeout=timeout)
            page.wait_for_load_state("networkidle")
        except Exception as e:
            log(f"[오류] 페이지 로드 실패: {str(e).splitlines()[0][:120]}")
            if i < LOGIN_TRIES - 1:
                time.sleep(5 * (i + 1))
                continue
            return ""

        if "/home" in page.url:
            log("이미 로그인됨 → 토큰 추출")
        else:
            try:
                page.fill('input[type="text"]', username)
                page.fill('input[type="password"]', password)
                with page.expect_navigation(timeout=15000 * (i + 1)):
                    page.click('button[type="submit"]')
                log(f"로그인 성공 → {page.url}")
            except PWTimeout:
                log("[실패] 로그인 타임아웃")
                if i < LOGIN_TRIES - 1:
                    time.sleep(5 * (i + 1))
                    continue
                return ""
            except Exception as e:
                log(f"[오류] 로그인 중 오류: {str(e).splitlines()[0][:120]}")
                if i < LOGIN_TRIES - 1:
                    time.sleep(5 * (i + 1))
                    continue
                return ""

        token = page.evaluate("() => localStorage.getItem('token') || ''")
        if token:
            return token
        log("[경고] 토큰이 비어 있음")
        if i < LOGIN_TRIES - 1:
            time.sleep(5 * (i + 1))
    return ""


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

        log(f"토큰 확보 ({len(token)}자)")

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


def crawl_country_days(browser, country_code, country_info, username, password, dates):
    """단일 국가를 로그인 1회로 여러 날짜 순차 다운로드(롤링 재수집).

    같은 파일명(photoism_{code}_{YYYYMMDD}.xlsx)에 덮어쓰므로 중복 파일이 안 생기고,
    이후 ingest 가 최소 날짜(cutoff)부터 교체 → 사후 매출이 자동 반영된다.
    반환: 성공한 날짜 수."""
    url  = country_info["url"].rstrip("/")
    name = country_info["name"]

    log(f"\n{'='*45}")
    log(f"[{country_code.upper()}] {name}  ({url})  · {len(dates)}일")
    log(f"{'='*45}")

    ctx  = browser.new_context(viewport={"width": 1440, "height": 900})
    page = ctx.new_page()
    ok_days = 0

    try:
        token = get_jwt_token(page, url, username, password, country_code)
        if not token:
            log("[실패] JWT 토큰 추출 실패")
            return 0
        log(f"토큰 확보 ({len(token)}자)")

        tz_offset  = country_info.get("timezone_offset", 9)
        cmsapi_url = country_info.get("cmsapi") or get_cmsapi_url(url)
        api_cc     = get_country_code(url)
        region     = country_info.get("region")

        for date_str in dates:
            start_utc = date_to_utc(date_str, is_end=False, tz_offset=tz_offset)
            end_utc   = date_to_utc(date_str, is_end=True,  tz_offset=tz_offset)
            for attempt in range(1, 4):
                try:
                    excel_data = download_excel_api(cmsapi_url, token, api_cc, start_utc, end_utc, region=region)
                    dest = RAW_DIR / f"photoism_{country_code}_{date_str.replace('-', '')}.xlsx"
                    if len(excel_data) < 512:
                        raise ValueError(f"응답 크기가 너무 작음 ({len(excel_data)} bytes)")
                    dest.write_bytes(excel_data)
                    log(f"  {date_str} 완료 ({len(excel_data):,} bytes)")
                    ok_days += 1
                    break
                except urllib.error.HTTPError as e:
                    if e.code in (401, 403):
                        token = get_jwt_token(page, url, username, password, country_code) or token
                    log(f"  {date_str} HTTP {e.code} ({attempt}/3)")
                    if attempt < 3:
                        time.sleep(10)
                except Exception as e:
                    log(f"  {date_str} 실패({attempt}/3): {str(e)[:80]}")
                    if attempt < 3:
                        time.sleep(10)
    finally:
        ctx.close()

    return ok_days


def main():
    # 날짜 결정: 인수 없으면 '어제'부터 최근 LOOKBACK_DAYS일 롤링 재수집.
    #   photoism_crawler.py                    → 최근 3일(어제~3일 전)
    #   photoism_crawler.py 2026-07-05         → 그 하루만(하위호환)
    #   photoism_crawler.py 2026-07-01 2026-07-05 → 기간 지정
    #   photoism_crawler.py 2026-08-07 2026-08-09 --only es → 그 나라만(구멍 메우기)
    args = sys.argv[1:]
    only = []
    if "--only" in args:
        _i = args.index("--only")
        only = [c.strip().lower() for c in args[_i + 1].split(",") if c.strip()]
        args = args[:_i] + args[_i + 2:]
    try:
        if len(args) >= 1:
            start = datetime.strptime(args[0], "%Y-%m-%d").date()
            end   = datetime.strptime(args[1], "%Y-%m-%d").date() if len(args) >= 2 else start
        else:
            end   = datetime.now(KST).date() - timedelta(days=1)
            start = end - timedelta(days=LOOKBACK_DAYS - 1)
    except ValueError as e:
        print(f"날짜 형식 오류: {e}  (올바른 형식: YYYY-MM-DD)")
        sys.exit(1)
    if start > end:
        start, end = end, start
    dates = [(start + timedelta(days=i)).isoformat() for i in range((end - start).days + 1)]

    log(f"크롤링 대상: {dates[0]} ~ {dates[-1]} ({len(dates)}일 · 롤링 재수집)")

    config   = load_config()
    photoism = config.get("photoism", {})
    username = photoism.get("username", "")
    password = photoism.get("password", "")
    countries = photoism.get("countries", {})

    if not countries:
        log("[오류] config.json에 photoism.countries 설정 없음")
        sys.exit(1)

    if only:      # 구멍 난 나라만 다시 받을 때 — 전 국가를 훑지 않아 CMS 부담이 적다
        countries = {c: v for c, v in countries.items() if c.lower() in only}
        if not countries:
            log(f"[오류] --only {','.join(only)} 에 해당하는 국가가 설정에 없어요")
            sys.exit(1)
        log(f"대상 국가 한정: {', '.join(countries)}")

    RAW_DIR.mkdir(exist_ok=True)
    LOG_DIR.mkdir(exist_ok=True)

    results = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)

        for code, info in countries.items():
            results[code] = crawl_country_days(browser, code, info, username, password, dates)
            if COUNTRY_DELAY:
                time.sleep(COUNTRY_DELAY)

        browser.close()

    # 결과 요약 (국가별 성공 날짜 수)
    log(f"\n{'='*45}")
    log("크롤링 완료 요약")
    success_list = [c for c, n in results.items() if n > 0]
    fail_list    = [c for c, n in results.items() if n == 0]
    partial      = [f"{c}({n}/{len(dates)})" for c, n in results.items() if 0 < n < len(dates)]
    log(f"  성공 ({len(success_list)}개): {', '.join(success_list)}")
    if partial:
        log(f"  일부일자 실패: {', '.join(partial)}")
    if fail_list:
        log(f"  실패 ({len(fail_list)}개): {', '.join(fail_list)}")
    log(f"{'='*45}")

    # ★대량 재수집 때는 적재를 건너뛴다(PHOTOISM_SKIP_INGEST=1).
    #   적재는 406MB parquet 을 통째로 다시 만들어 1회 14분이 걸린다. 기간을 나눠
    #   여러 번 돌리면 그것만 몇 시간이 붙는다. 전량 재수집은 다 받은 뒤 **한 번만**
    #   적재하는 게 맞다. 일일 수집은 이 변수가 없으니 지금까지와 똑같이 동작한다.
    if success_list and os.environ.get("PHOTOISM_SKIP_INGEST") != "1":
        log("\n데이터 누적 처리 시작 (photoism_ingest.py)...")
        subprocess.run(
            [sys.executable, str(BASE_DIR / "photoism_ingest.py"), dates[0]],
            cwd=str(BASE_DIR),
        )
    elif success_list:
        log("\n적재 건너뜀 (PHOTOISM_SKIP_INGEST=1) — 끝나면 직접 돌려 주세요.")

    # SM 촬영수 일일 수집을 독립 프로세스로 분리 실행(fire-and-forget).
    # 이 크롤러의 실행시간 제한(PT1H)·종료코드와 무관하게 SM 데이터도 매일 갱신되도록.
    # (전용 작업 스케줄러 등록은 관리자 권한이 필요해, 매일 도는 이 작업에 얹어 트리거)
    try:
        # ★여기서 import 하면 os/subprocess 가 **함수 전체**의 지역변수가 되어
        #   위쪽 os.environ 사용이 UnboundLocalError 로 죽는다(2026-08-05 실제 발생).
        #   모듈 최상단 import 를 쓴다. 지역 import 를 되살리지 말 것.
        flags = (subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP) if os.name == "nt" else 0
        subprocess.Popen(
            [sys.executable, str(BASE_DIR / "sm_daily.py")],
            cwd=str(BASE_DIR), creationflags=flags, close_fds=True,
        )
        log("SM 촬영수 일일 수집(sm_daily.py) 분리 실행 시작")
    except Exception as e:
        log(f"SM 일일 수집 실행 실패: {e}")

    # ★★부분 실패도 **실패로 끝낸다** (2026-08-20). 전엔 `fail_list`(성공 0일인
    #   국가)만 봤다. 그래서 "이 나라는 됐는데 그중 사흘이 비었다" 는 경우가
    #   **종료코드 0** = 스케줄러가 성공으로 기록 → 재시도도 안 걸렸다.
    #   유럽 19개월 결손이 정확히 이 모양이다. 부분 실패도 다시 받아야 한다.
    if fail_list or partial:
        if partial and not fail_list:
            log(f"  일부 일자만 받았습니다 — 재시도 대상입니다: {', '.join(partial)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
