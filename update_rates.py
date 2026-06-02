"""
실시간 환율 업데이트 스크립트

오늘 환율  → 한국수출입은행 SMBS (매매기준율) 우선, fawazahmed0 API fallback
과거 환율  → fawazahmed0 날짜별 API (duedate 기준 정산용)

실행: python update_rates.py
"""
import json
import re
import sys
import urllib.parse
import urllib.request
from datetime import datetime, date, timedelta
from pathlib import Path

BASE_DIR        = Path(__file__).parent
CONFIG_FILE     = BASE_DIR / "config.json"
LOG_DIR         = BASE_DIR / "logs"
RATE_CACHE_FILE = BASE_DIR / "data" / "rates_date_cache.json"

# 스내피즘 + 포토이즘 30개국 전체 통화
CURRENCIES = [
    # 스내피즘
    "CNY", "CNH", "JPY", "IDR", "TWD", "THB", "HKD", "MYR",
    # 포토이즘 추가
    "PHP", "VND", "CAD", "USD", "AED", "CLP", "EUR",
    "AUD", "SGD", "GBP", "PEN", "LAK", "MXN", "BND", "MNT", "MOP",
]

SMBS_URL = "http://www.smbs.biz/ExRate/TodayExRate.jsp"

# fawazahmed0 API (오늘/과거 fallback)
FAWAZ_URLS = [
    "https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@latest/v1/currencies/krw.json",
    "https://latest.currency-api.pages.dev/v1/currencies/krw.json",
]


# ── SMBS 파서 ─────────────────────────────────────────────────────────────────

def _decode_smbs(s: str) -> str:
    """%u_X[4hex] (유니코드) + %_X[2hex] (ASCII) 인코딩 → 문자열 변환."""
    # 유니코드 먼저: %u_X + 4자리 hex → chr
    decoded = re.sub(
        r"%u_[A-Za-z]([0-9a-fA-F]{4})",
        lambda m: chr(int(m.group(1), 16)),
        s,
    )
    # ASCII: %_X → % 후 URL 디코드
    decoded = re.sub(r"%_[A-Za-z]", "%", decoded)
    return urllib.parse.unquote(decoded)


def fetch_smbs_rates() -> dict | None:
    """SMBS(한국수출입은행) 매매기준율 스크래핑.

    반환: {"KRW": 1, "JPY": 9.46, "USD": 1511.3, ...}  실패 시 None.
    오늘 환율만 지원 (과거 날짜 조회 불가).
    """
    try:
        req = urllib.request.Request(
            SMBS_URL,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": SMBS_URL,
            },
        )
        ctx = urllib.request.ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = urllib.request.ssl.CERT_NONE
        with urllib.request.urlopen(req, timeout=15, context=ctx) as r:
            html = r.read().decode("euc-kr", errors="replace")
    except Exception as e:
        log(f"[SMBS 접속 실패] {e}")
        return None

    rates: dict = {"KRW": 1}
    # HTML 주석 제거 (주석 안 d4() 호출이 중복 파싱되는 문제 방지)
    html_clean = re.sub(r"<!--.*?-->", "", html, flags=re.DOTALL)
    tr_blocks = re.findall(r"<tr[^>]*>.*?</tr>", html_clean, re.DOTALL)

    for tr in tr_blocks:
        scripts = re.findall(r"d\d\(\s*'([^']*)'\s*\)", tr)
        if not scripts:
            continue

        # ── 통화 이름 추출 ──────────────────────────────────────────
        # case A: 첫 번째 script가 통화명 (스크립트 인코딩)
        name = _decode_smbs(scripts[0])
        code_m = re.search(r"\(([A-Z]{2,4})\)", name)

        # case B: plain-text <td>에 통화명 (CNH 등)
        if not code_m:
            plain = re.search(r"<td[^>]*>([^<]+\([A-Z]{2,4}\)[^<]*)</td>", tr)
            if plain:
                name = plain.group(1).strip()
                code_m = re.search(r"\(([A-Z]{2,4})\)", name)
                # 이 경우 scripts[0]이 환율값
                scripts = ["_plain_", *scripts]   # 인덱스 맞추기

        if not code_m:
            continue
        code = code_m.group(1)
        if len(code) != 3 or code not in CURRENCIES:
            continue

        # ── 환율값: scripts[1] ─────────────────────────────────────
        if len(scripts) < 2:
            continue
        rate_raw = _decode_smbs(scripts[1]).replace(",", "").strip()

        # 단위 (예: JPY(100) → 100)
        unit_m = re.search(r"\((\d+)\)\s*$", name.strip())
        unit = int(unit_m.group(1)) if unit_m else 1

        try:
            rate_val = round(float(rate_raw) / unit, 4)
            rates[code] = rate_val
            # CNH(역외 위안화) → CNY도 동일 적용
            if code == "CNH":
                rates["CNY"] = rate_val
        except (ValueError, ZeroDivisionError):
            pass

    if len(rates) < 3:        # KRW 외에 2개 미만이면 실패로 간주
        log("[SMBS] 파싱 결과 부족 — fallback 사용")
        return None

    log(f"[SMBS] {len(rates)-1}개 통화 파싱 완료")
    return rates


# ── fawazahmed0 API (today / 날짜별) ─────────────────────────────────────────

def _fetch_fawaz_latest() -> dict | None:
    """fawazahmed0 API에서 오늘 krw 기준 환율 반환. 실패 시 None."""
    for url in FAWAZ_URLS:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
            return data.get("krw", {})
        except Exception as e:
            log(f"[fawazahmed0 실패] {url} → {e}")
    return None


def _krw_dict_to_rates(krw_dict: dict) -> dict:
    """krw 기준 역수 → 1통화당 KRW 금액 dict."""
    rates: dict = {"KRW": 1}
    for cur in CURRENCIES:
        v = krw_dict.get(cur.lower())
        if v and v > 0:
            rates[cur] = round(1 / v, 2)
    return rates


# ── 공개 함수 ─────────────────────────────────────────────────────────────────

def log(msg: str):
    ts   = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    LOG_DIR.mkdir(exist_ok=True)
    with open(LOG_DIR / "rates.log", "a", encoding="utf-8") as f:
        f.write(line + "\n")


def update_exchange_rates() -> bool:
    """오늘 환율을 SMBS → fawazahmed0 순으로 가져와 config.json 갱신."""
    # 1순위: SMBS
    rates = fetch_smbs_rates()

    # 2순위: fawazahmed0
    if rates is None:
        log("[fallback] fawazahmed0 API 시도")
        krw_dict = _fetch_fawaz_latest()
        if krw_dict:
            rates = _krw_dict_to_rates(krw_dict)
        else:
            log("[환율 업데이트 실패] 기존 값 유지")
            return False

    # config.json 저장
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            config = json.load(f)
    except Exception as e:
        log(f"[오류] config.json 읽기 실패: {e}")
        return False

    config["exchange_rates"] = rates
    config["rates_updated"]  = datetime.now().strftime("%Y-%m-%d %H:%M")

    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)

    log(f"[환율 업데이트 완료] {config['rates_updated']}")
    for cur, rate in rates.items():
        if cur != "KRW":
            log(f"  1 {cur} = {rate:,.4f} KRW")

    return True


def get_effective_date(date_str: str) -> str:
    """YYYY-MM-DD → 주말/공휴일 보정 후 가장 가까운 이전 영업일 반환.
    미래 날짜는 오늘로 처리.
    """
    today = date.today()

    try:
        d = date.fromisoformat(date_str)
    except Exception:
        return today.isoformat()

    if d > today:
        d = today

    kr_holidays: set = set()
    try:
        import holidays as _hol
        kr = _hol.KR(years=range(max(d.year - 1, 2020), d.year + 2))
        kr_holidays = set(kr.keys())
    except ImportError:
        pass

    while d.weekday() >= 5 or d in kr_holidays:
        d -= timedelta(days=1)

    return d.isoformat()


def get_rates_for_date(date_str: str) -> dict:
    """특정 날짜 환율 반환. 로컬 캐시 우선.

    - 오늘 날짜 → SMBS 우선, fawazahmed0 fallback
    - 과거 날짜 → fawazahmed0 날짜별 API, 실패 시 config.json 기본값
    반환: {"KRW": 1, "JPY": 9.47, ...}
    """
    eff   = get_effective_date(date_str)
    today = date.today().isoformat()

    # 캐시 확인
    cache: dict = {}
    if RATE_CACHE_FILE.exists():
        try:
            with open(RATE_CACHE_FILE, encoding="utf-8") as f:
                cache = json.load(f)
        except Exception:
            pass

    if eff in cache:
        return cache[eff]

    # 오늘 날짜 → SMBS 시도
    if eff == today:
        rates = fetch_smbs_rates()
        if rates:
            _save_cache(cache, eff, rates)
            return rates

    # 과거(또는 SMBS 실패) → fawazahmed0
    urls = [
        f"https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@{eff}/v1/currencies/krw.json",
        *FAWAZ_URLS,  # latest fallback
    ]
    krw_dict = None
    for url in urls:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
            krw_dict = data.get("krw", {})
            if krw_dict:
                break
        except Exception as e:
            log(f"[날짜환율 조회 실패] {url}: {e}")

    if not krw_dict:
        try:
            with open(CONFIG_FILE, encoding="utf-8") as f:
                return json.load(f).get("exchange_rates", {"KRW": 1})
        except Exception:
            return {"KRW": 1}

    rates = _krw_dict_to_rates(krw_dict)
    _save_cache(cache, eff, rates)
    return rates


def _save_cache(cache: dict, key: str, rates: dict):
    cache[key] = rates
    RATE_CACHE_FILE.parent.mkdir(exist_ok=True)
    try:
        with open(RATE_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log(f"[캐시 저장 실패] {e}")


if __name__ == "__main__":
    ok = update_exchange_rates()
    sys.exit(0 if ok else 1)
