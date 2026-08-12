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
# 테마별 전량 (2026-08-12 신설). 같은 응답에서 함께 만든다 — CMS 요청은 안 늘어난다.
# 단위: 날짜 · 국가 · 타이틀 · 테마 · 프레임.  ※오리지널은 이 API 에 안 온다.
THEME_PARQUET = BASE_DIR / "data" / "theme_daily.parquet"
LOG_DIR = BASE_DIR / "logs"

SM_REGEX = re.compile(r"sm\s*ent", re.I)
EXCLUDE_TITLE = ("렌탈", "test", "테스트")
ARTISTS_FILE = BASE_DIR / "sm_artists.json"


def _artist_keys():
    """sm_artists.json 의 아티스트 키워드(공백/기호 제거·소문자). PICK(PW) 타이틀 매칭용."""
    try:
        arts = json.loads(ARTISTS_FILE.read_text(encoding="utf-8")).get("artists", [])
    except Exception:
        return []
    out = []
    for a in arts:
        out += [re.sub(r"[\s_\-]", "", str(k)).lower() for k in a.get("kws", []) if str(k).strip()]
    return [k for k in out if k]


_ARTIST_KEYS = _artist_keys()


def is_sm_title(title: str) -> bool:
    """수집 대상 판정.
      ① 'sm ent' 타이틀 (WITH 구좌 — 기존 방식)
      ② PICK 구좌 중 SM 아티스트 — 타이틀이 'PW'로 시작하고 아티스트 키워드 포함.
    ★'PW' 접두는 CMS상 PICK 전용이라(검증: PW 타이틀 IP구분 100% PICK) 오탐이 없다.
      PW 한정을 빼면 '안재현'(→재현)·'넥스트라이즈'(→라이즈) 같은 오탐이 섞인다."""
    t = str(title)
    if any(k in t.lower() for k in EXCLUDE_TITLE):
        return False
    if SM_REGEX.search(t):
        return True
    n = re.sub(r"[\s_\-]", "", t).lower()
    return n.startswith("pw") and any(k in n for k in _ARTIST_KEYS)

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
    """그 국가/그날의 (타이틀, 테마, 프레임)별 행 **전부**.

    ★예전엔 여기서 `is_sm_title` 로 SM 것만 남기고 나머지를 버렸다. 그런데 요청
      본문에 타이틀 필터가 없어 **CMS 는 이미 전 타이틀을 보내주고 있었다** —
      한국 하루 1,132행 중 SM 은 41행뿐이라 96%를 그냥 버린 셈이다(2026-08-12 실측).
      그래서 전부 돌려주고, 나눠 담는 건 collect() 가 한다. **요청 수는 그대로다.**

    ★금액이 우리 매출 원장과 맞는 것도 확인했다 — 한국 2025-01-15 기준
      결제 48,268,000 · 쿠폰 108,000 · 코인 276,000 이 **1원까지 일치**한다.
      (어제 날짜는 아직 정착 전이라 조금 어긋날 수 있다)

    ※오리지널(자사 프레임)은 이 API 에 안 들어온다 — 타이틀이 없는 거래라서다.
    """
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
            rows_out.append({
                "날짜": d.isoformat(), "국가코드": cc.lower(),
                "타이틀": str(x.get("titleName", "")).strip(),
                "테마": str(x.get("themeName", "")).strip(),
                "프레임": str(x.get("frameName", "")).strip(),
                "촬영수": int(x.get("totalShootCount") or 0),
                "주문수": int(x.get("totalOrderCount") or 0),
                "최종결제금액": float(x.get("totalPrice") or 0),
                "쿠폰할인금액": float(x.get("totalCouponDiscount") or 0),
                "서비스코인": float(x.get("totalServiceCoin") or 0),
                "종이수": int(x.get("totalPaperCount") or 0),
                "프레임단가": float(x.get("framePrice") or 0),
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


def collect(start: date, end: date, codes, delay: int, write_sm: bool = True):
    """수집 후 저장. write_sm=False 면 **테마 파일만** 쓴다.

    ★백필용 안전장치 — 2025년을 훑으면 그때의 SM 타이틀까지 sm_shoot_daily 에
      들어가 **SM 리포트의 과거가 갑자기 늘어난다**. 담당자에게 나가는 문서라
      말없이 바뀌면 안 되므로, 백필은 기본으로 테마 파일만 채운다.
    """
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

    raw = pd.DataFrame(all_rows)
    if raw.empty:
        log("수집 결과 없음 — 저장 생략")
        return raw

    # ── ① 테마별 전량 (신규) ────────────────────────────────────────────
    # 타이틀까지 남긴 전량. 같은 응답으로 만드는 것이라 **요청은 더 안 나간다.**
    theme = (raw.groupby(["날짜", "국가코드", "타이틀", "테마", "프레임"], as_index=False)
             .agg({"촬영수": "sum", "주문수": "sum", "최종결제금액": "sum",
                   "쿠폰할인금액": "sum", "서비스코인": "sum", "종이수": "sum",
                   "프레임단가": "max"}))
    _upsert(THEME_PARQUET, theme, ["날짜", "국가코드", "타이틀", "테마", "프레임"])

    # ── ② SM 촬영수 (기존 그대로) ───────────────────────────────────────
    # ★스키마·집계 단위를 바꾸지 않는다 — SM 리포트가 이 파일을 그대로 읽는다.
    if not write_sm:
        return theme
    sm = raw[raw["타이틀"].map(is_sm_title)]
    if sm.empty:
        log("SM 타이틀 없음 — sm_shoot_daily 저장 생략")
        return theme
    # (날짜·국가·테마·프레임) 단위로 합산(타이틀 전환 시 같은 멤버 합치기)
    sm = (sm.groupby(["날짜", "국가코드", "테마", "프레임"], as_index=False)
          .agg({"촬영수": "sum", "주문수": "sum", "최종결제금액": "sum"}))
    _upsert(OUT_PARQUET, sm, ["날짜", "국가코드", "테마", "프레임"])
    return sm


def _upsert(path, new, sort_keys):
    """이번에 받은 (날짜·국가) 조합을 통째로 갈아끼운다 — 시차·정착 변동 반영.

    ★부분 갱신이 아니라 **조합 단위 교체**다. 그 날·그 나라에서 사라진 행
      (취소로 0이 된 테마 등)이 옛 값으로 남지 않게 하려는 것.
    """
    path.parent.mkdir(exist_ok=True)
    if path.exists():
        try:
            old = pd.read_parquet(path)
        except Exception as ex:                       # noqa: BLE001
            log(f"[경고] {path.name} 읽기 실패({ex}) — 이번 수집분만 저장한다")
            old = pd.DataFrame(columns=new.columns)
        if not old.empty:
            # 옛 파일에 없는 열(타이틀 등)이 생겼어도 concat 이 깨지지 않게 맞춰 준다
            for c in new.columns:
                if c not in old.columns:
                    old[c] = 0 if new[c].dtype.kind in "if" else ""
            old = old[new.columns]
        pulled = set(zip(new["날짜"], new["국가코드"]))
        keep = [(dd, cc) not in pulled for dd, cc in zip(old["날짜"], old["국가코드"])] \
            if not old.empty else []
        merged = pd.concat([old[keep], new], ignore_index=True) if not old.empty else new
    else:
        merged = new
    merged = merged.sort_values(sort_keys).reset_index(drop=True)
    merged.to_parquet(path, index=False)
    log(f"=== 저장: {path.name} (총 {len(merged):,}행, 이번 {len(new):,}행) ===")


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
