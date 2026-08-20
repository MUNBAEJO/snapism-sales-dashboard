"""
실시간 환율 업데이트 스크립트

오늘 환율  → 한국수출입은행 SMBS (매매기준율) 우선, fawazahmed0 API fallback
과거 환율  → fawazahmed0 날짜별 API (duedate 기준 정산용)

실행: python update_rates.py
"""
import json
import os
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


def fetch_smbs_rates(day: str | None = None) -> dict | None:
    """서울외국환중개(smbs.biz) 매매기준율 스크래핑. 공개 API 가 없어 페이지를 읽는다.

    day: "YYYY-MM-DD". 생략하면 오늘.
      ★과거 날짜도 된다 — 페이지에 날짜 검색 폼(StrSch_Year/Month/Day)이 있고
        POST 로 조회된다. 게다가 **사이트가 영업일 보정까지 해준다**
        (2026-06-28 일요일 요청 → 2026-06-26 금요일 값을 돌려줌).
      실제로 적용된 날짜는 반환 dict 의 "_asof" 에 담는다 — 요청일과 다를 수 있으므로
      정산서에 찍을 땐 이 값을 써야 한다.

    반환: {"KRW": 1, "JPY": 9.46, "USD": 1511.3, ..., "_asof": "2026-06-26"}
          실패 시 None.
    """
    try:
        ctx = urllib.request.ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = urllib.request.ssl.CERT_NONE
        headers = {"User-Agent": "Mozilla/5.0", "Referer": SMBS_URL}
        if day:
            y, m, d = day.split("-")
            body = urllib.parse.urlencode({
                "StrSch_Year": y, "StrSch_Month": m, "StrSch_Day": d,
                "StrSchFull": f"{y}.{m}.{d}",
            }).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
            req = urllib.request.Request(SMBS_URL, data=body, method="POST",
                                         headers=headers)
        else:
            req = urllib.request.Request(SMBS_URL, headers=headers)
        with urllib.request.urlopen(req, timeout=20, context=ctx) as r:
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

    # 페이지가 실제로 적용한 날짜를 되돌려준다(휴장일이면 직전 영업일로 당겨져 있다).
    ym = re.search(r'name="StrSch_Year"\s*value="(\d{4})"', html)
    mm = re.search(r'name="StrSch_Month"\s*value="(\d{1,2})"', html)
    dm = re.search(r'name="StrSch_Day"\s*value="(\d{1,2})"', html)
    if ym and mm and dm:
        rates["_asof"] = f"{ym.group(1)}-{int(mm.group(1)):02d}-{int(dm.group(1)):02d}"

    log(f"[SMBS] {len(rates)-1}개 통화 파싱 완료"
        + (f" (기준일 {rates['_asof']})" if "_asof" in rates else ""))
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


def _yahoo_close(symbol: str, day: str | None) -> float | None:
    """야후 파이낸스 종가. day 를 주면 **그 날짜 기준** 종가를 쓴다.

    ★기준일 종가를 써야 한다 — 오늘 시세에 과거 USD/KRW 를 곱하면 서로 다른 날짜가
      섞인다. 기존 정산 자동화(Code.gs fetchYahooClose)와 같은 방식으로,
      기준일 -7일 ~ +2일 구간을 받아 **기준일 이하 중 가장 최근 종가**를 고른다
      (주말·공휴일이면 직전 거래일 값이 잡힌다).
    """
    import urllib.request
    try:
        if day:
            t = int(datetime.strptime(day, "%Y-%m-%d").timestamp())
            url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
                   f"?period1={t - 86400 * 7}&period2={t + 86400 * 2}&interval=1d")
        else:
            url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
                   f"?interval=1d&range=1d")
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        d = json.loads(urllib.request.urlopen(req, timeout=15).read())
        res = d["chart"]["result"][0]
        if not day:
            return res["meta"].get("regularMarketPrice")
        ts = res.get("timestamp") or []
        closes = res["indicators"]["quote"][0].get("close") or []
        target = int(datetime.strptime(day, "%Y-%m-%d").timestamp()) + 86400
        best, best_t = None, -1
        for i, tv in enumerate(ts):
            if tv <= target and i < len(closes) and closes[i] is not None and tv > best_t:
                best, best_t = closes[i], tv
        return best
    except Exception as e:
        log(f"[야후 {symbol}] 조회 실패: {e}")
        return None


def _sig(v: float, digits: int = 8) -> float:
    """유효숫자 기준 반올림.

    ★왜 소수점 4자리 고정을 쓰면 안 되나(2026-08-05): 라오스 킵은 1원의 0.06 수준이라
      0.0001 눈금이 **값의 0.16%** 다. 실제로 0.0638502 를 0.0639 로 올리는 바람에
      980,000 LAK 이 62,573원 대신 62,622원으로 나와 정산서가 49원 과대계상됐다
      (사용자 시트와 대조하다 발견). USD(1,441)는 같은 4자리가 0.000007% 라 무해하니,
      자릿수가 아니라 **유효숫자**로 잡아야 통화 크기와 무관하게 일정해진다.
    ※ SMBS 고시분은 원래 소수 둘째 자리까지만 주므로 4자리 반올림이어도 손실이 없다.
      나눗셈으로 만들어내는 이 크로스레이트만 정밀도가 깎였다.
    """
    if not v:
        return 0.0
    return float(f"%.{digits}g" % v)


def fetch_yahoo_cross(usd_krw: float, day: str | None = None) -> dict:
    """SMBS 미고시 통화(LAK/PEN)를 야후 USD 크로스레이트로 보강.
    Yahoo '<CUR>=X' = 1 USD 당 해당 통화 수량 → 1 통화 = (1/price) USD → × USD_KRW.
    (정산 자동화 Code.gs 와 동일한 계산식·동일한 날짜 기준)"""
    out: dict = {}
    if not usd_krw or usd_krw <= 0:
        return out
    for cur, sym in {"LAK": "LAK=X", "PEN": "PEN=X"}.items():
        price = _yahoo_close(sym, day)
        if price and price > 0:
            out[cur] = _sig(( 1.0 / price) * usd_krw)
    return out


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

    # LAK/PEN 등 SMBS·fawazahmed0 미지원 통화 → 야후 USD 크로스레이트로 보강
    cross = fetch_yahoo_cross(rates.get("USD"))
    for cur, v in cross.items():
        rates[cur] = v
        log(f"[야후 보강] 1 {cur} = {v:,.4f} KRW")

    # config.json 저장
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            config = json.load(f)
    except Exception as e:
        log(f"[오류] config.json 읽기 실패: {e}")
        return False

    asof = rates.pop("_asof", "")          # 메타는 환율 dict 에 섞어 두지 않는다
    config["exchange_rates"] = rates
    config["rates_updated"]  = datetime.now().strftime("%Y-%m-%d %H:%M")
    if asof:
        config["rates_asof"] = asof

    # 원자적 저장(임시파일 → os.replace): 대시보드가 읽는 순간과 겹쳐도
    # 반쯤 쓰인 config.json 을 읽어 환율이 1로 왜곡되는 사고를 방지.
    _tmp = str(CONFIG_FILE) + ".tmp"
    with open(_tmp, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
    os.replace(_tmp, CONFIG_FILE)

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

    # ★SMBS 는 과거 날짜도 조회된다(POST 로 날짜 폼 전송). 오늘/과거 모두 여기서 끝낸다.
    #   사이트가 휴장일을 직전 영업일로 당겨 주므로 그 날짜(_asof)로 캐시한다.
    rates = fetch_smbs_rates(None if eff == today else eff)
    if rates:
        asof = rates.pop("_asof", eff)
        _save_cache(cache, asof, rates)
        if asof != eff:
            _save_cache(cache, eff, rates)   # 요청일로도 찾을 수 있게
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
        # ★최종 폴백을 `{"KRW": 1}` 로 두면 **안 된다** (2026-08-20).
        #   원화만 든 환율표는 `.map(rates).fillna(1)` · DuckDB `ELSE 1` 로 떨어져
        #   엔·달러·바트가 전부 1:1 원화가 되는데, 값이 있으니 아무 검사에도 안 걸린다.
        #   settlement_fx.missing() 이 "없으면 잡는다"는 전제로 만들어져 있으므로
        #   **빈 표를 돌려줘 그쪽이 잡게 한다.**
        try:
            with open(CONFIG_FILE, encoding="utf-8") as f:
                _cfg = json.load(f).get("exchange_rates") or {}
            _ok = [k for k, v in _cfg.items()
                   if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0]
            return _cfg if len(_ok) >= 2 else {}
        except Exception:
            return {}

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
