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

SRC_OFFICIAL = "서울외국환중개 매매기준율"
SRC_SCRAPE = "서울외국환중개 매매기준율(당일 조회)"
SRC_FALLBACK = "참고 환율(자동 조회)"

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

    if eff and eff == _date.today().isoformat():
        got = ur.fetch_smbs_rates()
        if got:
            return got, eff, SRC_SCRAPE

    if eff:
        return ur.get_rates_for_date(eff), eff, SRC_FALLBACK

    import json
    from pathlib import Path
    cfg = json.loads((Path(__file__).parent / "config.json").read_text("utf-8"))
    return cfg.get("exchange_rates", {"KRW": 1}), "", SRC_FALLBACK
