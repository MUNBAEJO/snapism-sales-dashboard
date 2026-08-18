# -*- coding: utf-8 -*-
"""매일 자동 실행 — 최근 며칠치 SM 촬영수를 CMS에서 재수집(덮어쓰기)해 데이터를 신선하게 유지.

주간(sm_weekly, 14일 재수집 + 발송용 엑셀 생성)과 달리 이 스크립트는 '가벼운 일일 갱신'만 한다:
- 최근 LOOKBACK_DAYS(기본 3)일을 다시 받아 시차 정착 변동을 덮어씀.
- 어제까지만 받는다(오늘은 아직 미확정 → 정착 전).
- 시차 정착으로 값이 바뀐 (날짜·국가·멤버)는 변경내역에 기록(메모용).
- 발송용 엑셀은 만들지 않음(대시보드가 parquet을 직접 읽고, 발송본은 주간 작업이 생성).

실행:  python sm_daily.py            # 기본 최근 3일
       python sm_daily.py 5         # 최근 5일
Windows 작업 스케줄러 daily 트리거로 매일 새벽 실행 권장.
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
LOOKBACK_DAYS = 3
MAX_CATCHUP_DAYS = 45      # 밀린 구간을 메울 때의 상한 — CMS 부담을 막는 안전선


def _catchup_start(default_start):
    """마지막으로 받은 날짜를 보고 **빈 만큼 창을 앞으로 늘린다.**

    ★롤링 창(3일)만 받으면 그보다 오래 멈춘 구간은 **영구히 빈다.** 창이 지나가
      버리면 아무도 다시 안 받기 때문이다. 실제로 테마가 그랬다 — 매일 수집에
      테마가 들어간 게 2026-08-12 라, 2026-08-01~11 이 통째로 비어 있었고
      월 단위 백필은 '이번 달은 매일 수집이 채운다'며 건너뛰어서 아무도 안 채웠다.
    ★상한을 두는 이유 — 파일이 비었거나 처음 도는 경우 1년 치를 한 번에 받으려
      들면 CMS 에 부담이 된다. 그런 건 백필(theme_backfill.py)이 할 일이다.
    ※한계: 파일 전체의 마지막 날짜만 본다. **한 나라만** 빠진 구간은 못 잡는다 —
      그건 수집 때 남는 skipped 목록과 coverage_audit.py 가 볼 몫이다.
    """
    try:
        import pandas as _pd
        last = _pd.read_parquet(sm_collect.THEME_PARQUET, columns=["날짜"])["날짜"]
        last = _pd.to_datetime(last, errors="coerce").max().date()
    except Exception as ex:
        sm_collect.log(f"밀린 구간 확인 실패(평소대로 진행): {ex}")
        return default_start
    want = last + timedelta(days=1)          # 마지막 받은 날 다음 날부터
    if want >= default_start:
        return default_start                  # 안 밀렸다
    floor = date.today() - timedelta(days=MAX_CATCHUP_DAYS)
    start = max(want, floor)
    sm_collect.log(f"### 밀린 구간 감지: 마지막 {last} → {start} 부터 받습니다"
                   + (f" (상한 {MAX_CATCHUP_DAYS}일로 잘림)" if want < floor else "") + " ###")
    return start


def main():
    lookback = int(sys.argv[1]) if len(sys.argv) > 1 else LOOKBACK_DAYS
    today = date.today()
    end = today - timedelta(days=1)          # 어제까지(오늘은 정착 전)
    start = end - timedelta(days=lookback - 1)

    cfg = json.load(open(BASE_DIR / "config.json", encoding="utf-8"))["photoism"]
    codes = list(cfg["countries"].keys())

    # 변동 감지용 — 수집(덮어쓰기) 전 직전 값 스냅샷
    prev = None
    if sm_report.DAILY_PARQUET.exists():
        try:
            prev = sm_report.aggregate_members(sm_report.load_daily())
        except Exception as ex:
            sm_collect.log(f"직전 스냅샷 실패: {ex}")

    start = _catchup_start(start)       # 밀렸으면 창을 앞으로 늘린다
    sm_collect.log(f"### 일일 자동 수집 시작: {start}~{end} ({len(codes)}개국, "
                   f"{(end - start).days + 1}일) ###")
    sm_collect.collect(start, end, codes, delay=8)

    # 수집 후 — 직전 대비 변동(같은 날짜·국가·멤버의 값 변화)을 변경내역에 기록
    if prev is not None and not prev.empty:
        try:
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
        except Exception as ex:
            sm_collect.log(f"변동 기록 실패: {ex}")

    # 신규 IP 자동 처리 — 솔로는 sm_artists.json 자동 등록, 그룹은 관리자 메일 알림
    try:
        info = sm_report.analyze_unmatched(sm_report.load_daily())
        added = sm_report.add_artists_to_json(info["solos"])
        if added:
            sm_collect.log(f"### 신규 솔로 IP 자동 등록: {', '.join(added)} (sm_artists.json) ###")
        if info["groups"]:
            import sm_mail
            keys = sm_mail.alert_new_groups(info["groups"])
            if keys:
                sm_collect.log(f"### 신규 그룹 IP 감지 — 관리자 메일 알림: {keys} ###")
    except Exception as ex:
        sm_collect.log(f"신규 IP 자동처리 실패: {ex}")

    sm_collect.log("### 일일 자동 수집 완료 ###")


if __name__ == "__main__":
    main()
