"""정산용 환율 — 출처를 속이지 않는다.

왜 따로 두나
  `update_rates.get_rates_for_date()` 는 **오늘 날짜만** 서울외국환중개(smbs.biz)를
  긁는다. 그 사이트는 공개 API 가 없고 과거 조회도 안 되기 때문에, 과거 날짜는
  조용히 fawazahmed0 CDN 으로 폴백한다. 그런데 정산서에는 '서울외국환중개
  매매기준율' 이라고 찍힌다 — 대외 문서에 틀린 출처가 박히는 셈이다.

그래서
  1) 실무자가 서울외국환중개 `TodayExRate.xls` 를 올리면 그 날짜 공식 환율로 저장하고
     (기존 Node 정산 자동화가 쓰던 방식 그대로), 정산은 **이걸 최우선**으로 쓴다.
  2) 공식 환율이 없으면 자동 조회값을 쓰되 **출처를 그대로 표기**한다.

관련: CURRENT-PROJECTS/IP-정산서-생성.md · 지라 CO-288
"""
from __future__ import annotations

import io
import re
from datetime import datetime

from json_store import JsonStore

_store = JsonStore("settlement_fx.json", default={"dates": {}})

SRC_OFFICIAL = "서울외국환중개 매매기준율"          # 실무자가 올린 공식 파일
SRC_SCRAPE = "서울외국환중개 매매기준율"            # 사이트에서 그 날짜로 직접 조회
SRC_FALLBACK = "참고 환율(자동 조회)"               # 서울외국환중개 조회 실패 시

# update_rates.CURRENCIES 와 같은 범위만 받는다.
_CUR_RE = re.compile(r"\b([A-Z]{3})\b")


def get(rate_date: str) -> dict | None:
    """그 날짜의 공식 환율. 없으면 None."""
    return (_store.load().get("dates") or {}).get(rate_date)


def save(rate_date: str, rates: dict, by: str, memo: str = "") -> None:
    rec = {"rates": {k: float(v) for k, v in rates.items() if v},
           "by": by, "memo": memo,
           "at": datetime.now().isoformat(timespec="seconds")}
    _store.mutate(lambda d: d.setdefault("dates", {}).update({rate_date: rec}))


def dates() -> list[str]:
    return sorted((_store.load().get("dates") or {}).keys(), reverse=True)


def version() -> float:
    return _store.version()


def parse_upload(data: bytes, filename: str = "") -> tuple[dict, str]:
    """서울외국환중개 `TodayExRate.xls` 파싱 → ({통화: 원화}, 기준일 or '').

    이 파일은 확장자만 .xls 이고 실제로는 HTML 표인 경우가 많다.
    엑셀 → HTML → CSV 순으로 시도한다.
    """
    import pandas as pd

    tables = []
    # 1) 진짜 엑셀
    try:
        x = pd.read_excel(io.BytesIO(data), sheet_name=None, header=None)
        tables = list(x.values())
    except Exception:
        pass
    # 2) HTML 표(.xls 로 위장한 경우)
    if not tables:
        for enc in ("euc-kr", "cp949", "utf-8"):
            try:
                tables = pd.read_html(io.StringIO(data.decode(enc, "replace")))
                if tables:
                    break
            except Exception:
                continue
    # 3) CSV
    if not tables:
        for enc in ("euc-kr", "cp949", "utf-8"):
            try:
                tables = [pd.read_csv(io.BytesIO(data), encoding=enc, header=None)]
                break
            except Exception:
                continue
    if not tables:
        raise ValueError("환율 파일을 읽지 못했어요. 엑셀·HTML·CSV 어느 쪽도 아니에요.")

    out: dict[str, float] = {"KRW": 1.0}
    ref_date = ""
    for t in tables:
        flat = t.astype(str)
        for _, row in flat.iterrows():
            cells = [c.strip() for c in row.tolist() if c and c != "nan"]
            if not cells:
                continue
            joined = " ".join(cells)
            if not ref_date:
                m = re.search(r"(20\d{2})[.\-/](\d{1,2})[.\-/](\d{1,2})", joined)
                if m:
                    ref_date = (f"{m.group(1)}-{int(m.group(2)):02d}"
                                f"-{int(m.group(3)):02d}")
            m = _CUR_RE.search(joined)
            if not m:
                continue
            code = m.group(1)
            # 통화 뒤 첫 숫자 = 매매기준율. 단위(100)가 붙으면 나눈다.
            unit = 100 if re.search(rf"{code}\s*\(?\s*100", joined) else 1
            nums = [c.replace(",", "") for c in cells
                    if re.fullmatch(r"[\d,]+\.?\d*", c.replace(",", ""))]
            vals = []
            for n in nums:
                try:
                    v = float(n)
                except ValueError:
                    continue
                if 0.01 < v < 100000:
                    vals.append(v)
            if vals:
                out[code] = round(vals[0] / unit, 4)
    if len(out) < 3:
        raise ValueError("통화를 찾지 못했어요. 서울외국환중개 환율 파일이 맞는지 "
                         "확인해 주세요.")
    return out, ref_date


# 서울외국환중개가 고시하지 않는 통화. 비워두면 SQL CASE 가 ELSE 1 로 떨어져
# 라오스 1,820,000 LAK 가 1,820,000원으로 계산된다(실제 ≈118,300원, 15배 부풀림).
UNLISTED = ("LAK", "PEN")


def _backfill(rates: dict, asof: str) -> None:
    """미고시 통화를 채운다. 못 채우면 **키를 아예 넣지 않는다** —
    1.0 으로 두면 조용히 15배 부풀어 오히려 위험하다. missing() 이 잡아낸다."""
    import update_rates as ur

    need = [c for c in UNLISTED if c not in rates]
    if not need:
        return
    # ① 야후 USD 크로스 (기존 정산 자동화와 같은 계산식)
    try:
        for cur, v in ur.fetch_yahoo_cross(rates.get("USD")).items():
            if cur in need and v:
                rates[cur] = v
    except Exception:
        pass
    # ② 그래도 없으면 날짜별 참고 환율에서 가져온다
    need = [c for c in UNLISTED if c not in rates]
    if need:
        try:
            ref = ur.get_rates_for_date(asof)
            for cur in need:
                if ref.get(cur):
                    rates[cur] = ref[cur]
        except Exception:
            pass


def missing(rates: dict, units) -> list[str]:
    """매출에 실제로 쓰인 통화 중 환율이 없는 것.

    ★이걸 안 잡으면 조용히 틀린다 — 환율이 없으면 1.0 이 적용되는데,
      현지 매출과 KRW 매출이 같은 값이 되어 검증식도 통과해 버린다."""
    have = {k for k, v in rates.items()
            if isinstance(v, (int, float)) and v > 0}
    return sorted({str(u).strip() for u in units
                   if str(u).strip() and str(u).strip() not in have})


def resolve(rate_date: str) -> tuple[dict, str, str]:
    """(환율, 실제 기준일, 출처 문구).

    ① 저장된 공식 환율 → ② 오늘이면 smbs.biz 당일 스크래핑 → ③ 자동 조회 폴백.
    어느 쪽이든 **출처를 있는 그대로** 돌려준다.
    """
    from datetime import date as _date

    import update_rates as ur

    eff = ur.get_effective_date(rate_date) if rate_date else ""
    off = get(eff) if eff else None
    if off:
        return off["rates"], eff, SRC_OFFICIAL

    if eff:
        # 서울외국환중개는 공개 API 가 없지만 날짜 검색 폼이 있어 과거도 조회된다.
        # 사이트가 휴장일을 직전 영업일로 당겨 주므로 _asof 를 실제 기준일로 쓴다.
        today = _date.today().isoformat()
        got = ur.fetch_smbs_rates(None if eff == today else eff)
        if got:
            asof = got.pop("_asof", eff)
            _backfill(got, asof)
            return got, asof, SRC_SCRAPE
        return ur.get_rates_for_date(eff), eff, SRC_FALLBACK

    import json
    from pathlib import Path
    cfg = json.loads((Path(__file__).parent / "config.json").read_text("utf-8"))
    return cfg.get("exchange_rates", {"KRW": 1}), "", SRC_FALLBACK
