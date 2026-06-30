# -*- coding: utf-8 -*-
"""SM 'SM ent' 타이틀 — 멤버(프레임)별 일일 촬영수를 CMS에서 직접 수집(CMS-정확).

CMS `/v1/revenue/frame` 의 totalShootCount(=Artist별 촬영수)를 국가별로 조회한다.
인계받은 fill.py 방식을 우리 환경(config.json + theme_crawler 웹토큰)에 이식.

- 인증: techadmin 계정 API sign-in이 안 먹어서(웹과 비번 경로 다름),
  theme_crawler.get_token(Playwright 웹로그인)으로 국가별 토큰을 받는다.
- 시차: 서버는 paymentStart/End(UTC)만 보므로, 각 국가의 '하루'를
  그 국가 표준시(OFFSET)로 UTC 창을 만들어 보낸다 → CMS 화면과 일치.
- 범위: titleName에 'sm ent' 포함 타이틀만(렌탈/테스트 제외). 최근 창을 받으므로
  '계속 판매되는' 테마(아티스트)는 자동으로 잡힌다.
- 저장: data/sm_shoot_daily.parquet 에 (날짜·국가·테마·프레임) 단위로 upsert(덮어쓰기).

실행:
  python sm_collect.py 2026-06-20 2026-06-29            # 전 30개국
  python sm_collect.py 2026-06-20 2026-06-29 kr,jp 8    # 국가지정 · 국가간 8초
보안: 자격증명/토큰/요청본문은 절대 출력하지 않는다.
"""
import json
import re
import sys
import time
import urllib.request
import urllib.error
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

import theme_crawler as tc  # get_token(웹 로그인) 재사용

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE_DIR = Path(__file__).parent
CONFIG_FILE = BASE_DIR / "config.json"
OUT_PARQUET = BASE_DIR / "data" / "sm_shoot_daily.parquet"
LOG_DIR = BASE_DIR / "logs"

SM_REGEX = re.compile(r"sm\s*ent", re.I)
EXCLUDE_TITLE = ("렌탈", "test", "테스트")

# 국가별 UTC 오프셋(시간) — fill.py 인계분(2026-06, 북반구 서머타임 반영)
OFFSET = {
    "kr": 9, "jp": 9, "cn": 8, "hk": 8, "tw": 8, "mo": 8, "sg": 8, "my": 8,
    "ph": 8, "bn": 8, "mn": 8, "th": 7, "vn": 7, "id": 7, "la": 7,
    "ae": 4, "lv": 3, "de": 2, "fr": 2, "es": 2, "nl": 2, "lu": 2, "gb": 1,
    "au": 10, "gu": 10, "mx": -6, "cl": -4, "pe": -5, "us": -7, "ca": -7,
}


def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    LOG_DIR.mkdir(exist_ok=True)
    with open(LOG_DIR / "sm_collect.log", "a", encoding="utf-8") as f:
        f.write(line + "\n")


def _utc_window(d: date, off: int):
    delta = timedelta(hours=off)
    s = datetime(d.year, d.month, d.day, 0, 0, 0) - delta
    e = datetime(d.year, d.month, d.day, 23, 59, 59, 999000) - delta
    return s.strftime("%Y-%m-%dT%H:%M:%S.000Z"), e.strftime("%Y-%m-%dT%H:%M:%S.999Z")


def _post(url, body, token, timeout=90):
    req = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json", "x-api-token": token})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def fetch_day(cmsapi, token, cc, d: date, off: int):
    """그 국가/그날 SM ent 타이틀의 (테마, 프레임)별 촬영수 행 리스트."""
    s, e = _utc_window(d, off)
    rows_out = []
    page = 0
    while True:
        body = {
            "countryCd": cc.upper(), "frameId": None, "frameTypes": ["All"], "layoutId": None,
            "paymentStartDate": s, "paymentEndDate": e,
            "localStartDate": f"{d.isoformat()}T00:00:00", "localEndDate": f"{d.isoformat()}T23:59:59",
            "storeName": None, "themeId": None, "titleId": None,
        }
        j = _post(f"{cmsapi}/v1/revenue/frame?page={page}&size=2000", body, token)
        ct = j.get("content") or {}
        rows = ct.get("revenueList") or []
        for x in rows:
            ti = str(x.get("titleName", ""))
            if not SM_REGEX.search(ti):
                continue
            if any(k in ti.lower() for k in EXCLUDE_TITLE):
                continue
            rows_out.append({
                "날짜": d.isoformat(), "국가코드": cc.lower(),
                "테마": str(x.get("themeName", "")).strip(),
                "프레임": str(x.get("frameName", "")).strip(),
                "촬영수": int(x.get("totalShootCount") or 0),
                "주문수": int(x.get("totalOrderCount") or 0),
                "최종결제금액": float(x.get("totalPrice") or 0),
            })
        if ct.get("last", True) or page + 1 >= (ct.get("totalPages") or 1):
            break
        page += 1
    return rows_out


def daterange(a: date, b: date):
    d = a
    while d <= b:
        yield d
        d += timedelta(days=1)


def collect(start: date, end: date, codes, delay: int):
    cfg = json.load(open(CONFIG_FILE, encoding="utf-8"))["photoism"]
    user, pw, countries = cfg["username"], cfg["password"], cfg["countries"]
    dates = list(daterange(start, end))
    log(f"=== SM 촬영수 수집: {len(codes)}개국 × {len(dates)}일 ({start}~{end}) · 간격 {delay}s ===")

    all_rows = []
    for cc in codes:
        info = countries.get(cc)
        if not info:
            log(f"[{cc.upper()}] config 없음 — 건너뜀")
            continue
        off = OFFSET.get(cc)
        if off is None:
            log(f"[{cc.upper()}] 오프셋 미정의 — 건너뜀")
            continue
        url = info["url"].rstrip("/")
        cmsapi = info.get("cmsapi")
        try:
            token = tc.get_token(url, user, pw)
        except Exception as ex:
            log(f"[{cc.upper()}] 로그인 오류: {str(ex)[:80]} — 건너뜀")
            continue
        if not token:
            log(f"[{cc.upper()}] 로그인 실패 — 건너뜀")
            continue
        cc_rows, day_tot = [], {}
        for d in dates:
            for attempt in range(2):
                try:
                    rs = fetch_day(cmsapi, token, cc, d, off)
                    cc_rows.extend(rs)
                    day_tot[d.isoformat()] = sum(r["촬영수"] for r in rs)
                    break
                except urllib.error.HTTPError as ex:
                    if ex.code in (401, 403) and attempt == 0:
                        token = tc.get_token(url, user, pw)
                        continue
                    log(f"[{cc.upper()}] {d} HTTP {ex.code}")
                except Exception as ex:
                    if attempt == 0:
                        continue
                    log(f"[{cc.upper()}] {d} 오류 {str(ex)[:60]}")
        all_rows.extend(cc_rows)
        log(f"[{cc.upper()}] 일별 촬영수합 {day_tot}")
        time.sleep(delay)

    new = pd.DataFrame(all_rows)
    if new.empty:
        log("수집 결과 없음 — 저장 생략")
        return new
    # (날짜·국가·테마·프레임) 단위로 합산(타이틀 전환 시 같은 멤버 합치기)
    new = (new.groupby(["날짜", "국가코드", "테마", "프레임"], as_index=False)
              .agg({"촬영수": "sum", "주문수": "sum", "최종결제금액": "sum"}))

    OUT_PARQUET.parent.mkdir(exist_ok=True)
    if OUT_PARQUET.exists():
        old = pd.read_parquet(OUT_PARQUET)
        # 이번에 받은 (날짜·국가) 조합은 덮어쓰기 — 시차/정착 변동 반영
        pulled = set(zip(new["날짜"], new["국가코드"]))
        mask = [(dd, cc) not in pulled for dd, cc in zip(old["날짜"], old["국가코드"])]
        merged = pd.concat([old[mask], new], ignore_index=True)
    else:
        merged = new
    merged = merged.sort_values(["날짜", "국가코드", "테마", "프레임"]).reset_index(drop=True)
    merged.to_parquet(OUT_PARQUET, index=False)
    log(f"=== 저장: {OUT_PARQUET.name} (총 {len(merged):,}행, 이번 {len(new):,}행) ===")
    return new


def main():
    a = sys.argv[1:]
    if len(a) < 2:
        print("사용법: python sm_collect.py START END [국가=all|kr,jp,..] [딜레이초=8]")
        sys.exit(1)
    start = datetime.strptime(a[0], "%Y-%m-%d").date()
    end = datetime.strptime(a[1], "%Y-%m-%d").date()
    spec = (a[2] if len(a) > 2 else "all").lower()
    delay = int(a[3]) if len(a) > 3 else 8
    cfg = json.load(open(CONFIG_FILE, encoding="utf-8"))["photoism"]
    codes = list(cfg["countries"].keys()) if spec == "all" else [c.strip() for c in spec.split(",")]
    collect(start, end, codes, delay)


if __name__ == "__main__":
    main()
