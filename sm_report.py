# -*- coding: utf-8 -*-
"""SM 촬영수(sm_shoot_daily.parquet) → 대행사/부서 공유용 엑셀 생성.

받은 'Artist별 촬영수' 형식(테마·프레임 행 × 날짜 열)을 그대로 만든다.
시트: 국가합산 / 국가별 / 원본.

실행:
  python sm_report.py                      # 전체 기간, reports/ 에 저장
  python sm_report.py 2026-06-16 2026-06-29
"""
import io
import json
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

BASE_DIR = Path(__file__).parent
DAILY_PARQUET = BASE_DIR / "data" / "sm_shoot_daily.parquet"
CONFIG_FILE = BASE_DIR / "config.json"
REPORT_DIR = BASE_DIR / "reports"


def country_name_map() -> dict:
    try:
        cfg = json.load(open(CONFIG_FILE, encoding="utf-8"))["photoism"]["countries"]
        return {cc: info.get("name", cc.upper()) for cc, info in cfg.items()}
    except Exception:
        return {}


def load_daily(start=None, end=None) -> pd.DataFrame:
    df = pd.read_parquet(DAILY_PARQUET)
    if start:
        df = df[df["날짜"].astype(str) >= str(start)]
    if end:
        df = df[df["날짜"].astype(str) <= str(end)]
    nm = country_name_map()
    df = df.copy()
    df["국가"] = df["국가코드"].map(lambda c: nm.get(c, c.upper()))
    return df


def _pivot(df, index_cols):
    if df.empty:
        return pd.DataFrame()
    pv = pd.pivot_table(df, index=index_cols, columns="날짜", values="촬영수",
                        aggfunc="sum", fill_value=0).astype(int)
    pv["합계"] = pv.sum(axis=1)
    return pv.sort_values("합계", ascending=False)


def build_xlsx(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    by_sum = _pivot(df, ["테마", "프레임"])
    by_ctry = _pivot(df, ["국가", "테마", "프레임"])
    flat = df[["날짜", "국가", "테마", "프레임", "촬영수", "주문수", "최종결제금액"]] \
        .sort_values(["날짜", "국가", "테마", "프레임"])
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        (by_sum if not by_sum.empty else pd.DataFrame({"안내": ["데이터 없음"]})) \
            .to_excel(xw, sheet_name="국가합산")
        if not by_ctry.empty:
            by_ctry.to_excel(xw, sheet_name="국가별")
        flat.to_excel(xw, sheet_name="원본", index=False)
    return buf.getvalue()


def main():
    a = sys.argv[1:]
    start = a[0] if len(a) > 0 else None
    end = a[1] if len(a) > 1 else None
    if not DAILY_PARQUET.exists():
        print("sm_shoot_daily.parquet 가 없어요. 먼저 python sm_collect.py 로 수집하세요.")
        sys.exit(1)
    df = load_daily(start, end)
    if df.empty:
        print("해당 기간 데이터가 없어요.")
        sys.exit(0)
    s = df["날짜"].min()
    e = df["날짜"].max()
    REPORT_DIR.mkdir(exist_ok=True)
    out = REPORT_DIR / f"SM촬영현황_{s}_{e}.xlsx"
    out.write_bytes(build_xlsx(df))
    print(f"저장: {out}  (행 {len(df):,} · 국가 {df['국가'].nunique()} · 테마 {df['테마'].nunique()})")


if __name__ == "__main__":
    main()
