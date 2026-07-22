"""포토이즘 CMS 장비관리 엑셀 수집 (국가별).

CMS 호스트가 국가별로 분리돼 있어 reqSql 은 비어 있다(XLSX004). 국가당 로그인 1회 +
엑셀 1회면 끝. 서버 부담을 감안해 국가 사이에 대기를 둔다.

실행: python device_collect.py [국가코드 ...]     (미지정 시 매출 발생 8개국)
저장: data/devices/device_<cc>_<YYYYMMDD>.xlsx
"""
import json
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime

from playwright.sync_api import sync_playwright

from photoism_crawler import get_jwt_token, log

BASE_DIR    = Path(__file__).parent
CONFIG_FILE = BASE_DIR / "config.json"
DEST_DIR    = BASE_DIR / "data" / "devices"

# ★ cmsapi 는 국가가 아니라 '지역' 단위다. 한 호스트가 그 지역 전 국가 장비를 통째로
#   돌려주므로(tw 로 받으면 HK 까지, id 로 받으면 TH·MY·VN·PH·SG·LA 까지) 호스트당
#   1회만 받으면 된다. 8개국 다 돌리면 같은 파일을 세 번 받는 꼴.
#   아래는 매출 발생 8개국(KR·JP·CN·TW·HK·TH·ID·MY)을 덮는 최소 집합.
DEFAULT_COUNTRIES = ["kr", "jp", "cn", "tw", "id"]
COUNTRY_DELAY = 5   # 국가 간 대기(초) — 서버 부담 완화

# 캡처한 실제 요청 규격(_device_excel_spec.json)과 동일
EXCEL_COLUMNS = [
    {"columnId": "id",                 "headerDesc": "id"},
    {"columnId": "storeName",          "headerDesc": "지점명"},
    {"columnId": "brandNmKr",          "headerDesc": "브랜드"},
    {"columnId": "storeType01NmKr",    "headerDesc": "대분류"},
    {"columnId": "statusNmKr",         "headerDesc": "상태(한글)"},
    {"columnId": "boothNum",           "headerDesc": "부스번호"},
    {"columnId": "deviceModel",        "headerDesc": "기기 모델명"},
    {"columnId": "deviceSerial",       "headerDesc": "기기 S/N"},
    {"columnId": "deviceId",           "headerDesc": "기기ID"},
    {"columnId": "license",            "headerDesc": "라이센스"},
    {"columnId": "anydesk",            "headerDesc": "애니데스크"},
    {"columnId": "colorNmKr",          "headerDesc": "부스 컬러"},
    {"columnId": "cameraNmKr",         "headerDesc": "카메라 종류"},
    {"columnId": "cameraSerial",       "headerDesc": "카메라 S/N"},
    {"columnId": "cardSerialNo",       "headerDesc": "카드 단말기 번호"},
    {"columnId": "cameraLensZoom",     "headerDesc": "카메라 렌즈 줌"},
    {"columnId": "cameraISO",          "headerDesc": "카메라 ISO"},
    {"columnId": "cameraShutterSpeed", "headerDesc": "카메라 셔터 스피드"},
    {"columnId": "cameraAperture",     "headerDesc": "카메라 조리개"},
    {"columnId": "cameraColorTemp",    "headerDesc": "카메라 색온도"},
    {"columnId": "cameraWbCal",        "headerDesc": "카메라 WB보정"},
    {"columnId": "strobeIntensity",    "headerDesc": "에스라이트 (스트로브 세기)"},
    {"columnId": "printerNmKr",        "headerDesc": "프린터 사양"},
    {"columnId": "printerCnt",         "headerDesc": "프린터 대수"},
    {"columnId": "routerNmKr",         "headerDesc": "라우터 사양"},
    {"columnId": "pcNmKr",             "headerDesc": "PC본체 사양"},
    {"columnId": "ioBoardNmKr",        "headerDesc": "I/O보드 사양"},
    {"columnId": "monitorNmKr",        "headerDesc": "모니터 사양"},
    {"columnId": "routerSerial",       "headerDesc": "라우터 S/N"},
    {"columnId": "deviceNo",           "headerDesc": "기기정보"},
    {"columnId": "exportHistories",    "headerDesc": "히스토리"},
]


def download_device_excel(cmsapi_url: str, token: str) -> bytes:
    ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
    body = json.dumps({
        "excelCellInfo": EXCEL_COLUMNS,
        "excelEnumId": "XLSX004",
        "exlFileNm": f"device_{ts}.xlsx",
        "sheetName": "Sheet1",
        "reqSql": {},
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


def collect(codes):
    cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    ph  = cfg["photoism"]
    DEST_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now().strftime("%Y%m%d")

    ok, fail = [], []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        for i, cc in enumerate(codes):
            info = ph["countries"].get(cc)
            if not info:
                log(f"[건너뜀] {cc}: config 에 없음")
                fail.append(cc)
                continue
            url  = info["url"].rstrip("/")
            log(f"\n{'='*45}\n[{cc.upper()}] {info['name']}  ({url})\n{'='*45}")

            ctx = browser.new_context(viewport={"width": 1440, "height": 900})
            pg  = ctx.new_page()
            try:
                token = get_jwt_token(pg, url, ph["username"], ph["password"], cc)
                if not token:
                    raise RuntimeError("JWT 토큰 추출 실패")
                data = download_device_excel(info["cmsapi"], token)
                if len(data) < 512:
                    raise ValueError(f"응답이 너무 작음 ({len(data)} bytes)")
                dest = DEST_DIR / f"device_{cc}_{today}.xlsx"
                dest.write_bytes(data)
                log(f"저장: {dest.name} ({len(data):,} bytes)")
                ok.append(cc)
            except urllib.error.HTTPError as e:
                log(f"[오류] {cc}: HTTP {e.code} — {e.reason}")
                fail.append(cc)
            except Exception as e:
                log(f"[오류] {cc}: {str(e)[:200]}")
                fail.append(cc)
            finally:
                ctx.close()

            if i < len(codes) - 1:
                time.sleep(COUNTRY_DELAY)
        browser.close()

    log(f"\n완료: 성공 {len(ok)} ({','.join(ok)}) / 실패 {len(fail)} ({','.join(fail)})")
    return ok, fail


if __name__ == "__main__":
    codes = [c.lower() for c in sys.argv[1:]] or DEFAULT_COUNTRIES
    collect(codes)
