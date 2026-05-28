"""
실시간 환율 업데이트 스크립트

무료 API (fawazahmed0/currency-api, 키 불필요) 사용
1 KRW 기준 각 통화 역산 → config.json 의 exchange_rates 갱신

실행: python update_rates.py
"""
import json
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).parent
CONFIG_FILE = BASE_DIR / "config.json"
LOG_DIR = BASE_DIR / "logs"

CURRENCIES = ["CNY", "JPY", "IDR", "TWD", "THB", "HKD", "MYR"]

# 무료 환율 API (동일 데이터, 두 엔드포인트 미러)
RATE_URLS = [
    "https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@latest/v1/currencies/krw.json",
    "https://latest.currency-api.pages.dev/v1/currencies/krw.json",
]


def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    LOG_DIR.mkdir(exist_ok=True)
    with open(LOG_DIR / "rates.log", "a", encoding="utf-8") as f:
        f.write(line + "\n")


def fetch_rates():
    """API에서 krw 기준 환율 dict 반환. 실패 시 None."""
    for url in RATE_URLS:
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "Mozilla/5.0"}
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
            return data.get("krw", {})   # {"usd": 0.00072, "cny": 0.00526, ...}
        except Exception as e:
            log(f"[시도 실패] {url}  → {e}")
    return None


def update_exchange_rates():
    krw_rates = fetch_rates()
    if krw_rates is None:
        log("[환율 업데이트 실패] 기존 값 유지")
        return False

    rates = {"KRW": 1}
    for cur in CURRENCIES:
        rate = krw_rates.get(cur.lower())
        if rate and rate > 0:
            rates[cur] = round(1 / rate, 2)   # 1 currency = X KRW

    # config.json 읽기 → exchange_rates 교체 → 저장
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            config = json.load(f)
    except Exception as e:
        log(f"[오류] config.json 읽기 실패: {e}")
        return False

    config["exchange_rates"] = rates
    config["rates_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M")

    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)

    log(f"[환율 업데이트 완료] {config['rates_updated']}")
    for cur, rate in rates.items():
        if cur != "KRW":
            log(f"  1 {cur} = {rate:,.2f} KRW")

    return True


if __name__ == "__main__":
    ok = update_exchange_rates()
    sys.exit(0 if ok else 1)
