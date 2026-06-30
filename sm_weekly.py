# -*- coding: utf-8 -*-
"""매주 월요일 자동 실행 — 최근 2주 SM 촬영수를 CMS에서 재수집(덮어쓰기) + 부서 공유용 엑셀 생성.

Windows 작업 스케줄러에 등록해 매주 월요일 새벽에 돌린다(ExecutionTimeLimit 넉넉히).
- 최근 14일을 다시 받아 시차/정착 변동을 덮어씀.
- reports/SM촬영현황_최신.xlsx (고정명, 덮어쓰기) + 날짜본을 저장.
"""
import json
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

import sm_collect
import sm_report

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE_DIR = Path(__file__).parent
LOOKBACK_DAYS = 14


def main():
    today = date.today()
    start = today - timedelta(days=LOOKBACK_DAYS)
    end = today - timedelta(days=1)  # 어제까지(오늘은 정착 전)

    cfg = json.load(open(BASE_DIR / "config.json", encoding="utf-8"))["photoism"]
    codes = list(cfg["countries"].keys())

    # 변동 감지용 — 수집(덮어쓰기) 전 직전 값 스냅샷
    prev = None
    if sm_report.DAILY_PARQUET.exists():
        try:
            prev = sm_report.aggregate_members(sm_report.load_daily())
        except Exception as ex:
            sm_collect.log(f"직전 스냅샷 실패: {ex}")

    sm_collect.log(f"### 주간 자동 수집 시작: {start}~{end} ({len(codes)}개국) ###")
    sm_collect.collect(start, end, codes, delay=8)

    # 수집 후 — 직전 대비 변동(같은 날짜·국가·멤버의 값 변화)을 변경내역에 기록
    if prev is not None and not prev.empty:
        new = sm_report.aggregate_members(sm_report.load_daily())
        m = prev.merge(new, on=["날짜", "국가코드", "아티스트", "멤버"], suffixes=("_old", "_new"))
        chg = m[m["촬영수_old"].astype(int) != m["촬영수_new"].astype(int)]
        if not chg.empty:
            rec = pd.DataFrame({
                "갱신일": today.isoformat(), "날짜": chg["날짜"].values,
                "국가코드": chg["국가코드"].values, "아티스트": chg["아티스트"].values,
                "멤버": chg["멤버"].values,
                "이전": chg["촬영수_old"].astype(int).values,
                "신규": chg["촬영수_new"].astype(int).values})
            allch = pd.concat([sm_report.load_changes(), rec], ignore_index=True)
            allch.to_parquet(sm_report.CHANGES_PARQUET, index=False)
            sm_collect.log(f"### 변동 {len(rec)}건 기록 (변경내역 누적 {len(allch)}건) ###")
        else:
            sm_collect.log("### 변동 없음 ###")

    # 누적 전체로 공유 엑셀 생성 (고정명 + 날짜본)
    df = sm_report.load_daily()
    if df.empty:
        sm_collect.log("주간 리포트: 데이터 없음")
        return
    xlsx = sm_report.build_xlsx(df)
    REPORT_DIR = BASE_DIR / "reports"
    REPORT_DIR.mkdir(exist_ok=True)
    (REPORT_DIR / "SM촬영현황_최신.xlsx").write_bytes(xlsx)
    (REPORT_DIR / f"SM촬영현황_{df['날짜'].min()}_{df['날짜'].max()}.xlsx").write_bytes(xlsx)
    sm_collect.log(f"### 주간 리포트 저장 완료: reports/SM촬영현황_최신.xlsx (행 {len(df):,}) ###")


if __name__ == "__main__":
    main()
