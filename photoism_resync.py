# -*- coding: utf-8 -*-
"""포토이즘 기간 재수집 — 늦게 반영된 거래 채우기 (수동 실행)

    python photoism_resync.py 2026-07-01 2026-07-31
    python photoism_resync.py 2026-07-01 2026-07-31 --check   # 받지 않고 현황만

왜 필요한가(2026-08-05 규명)
  photoism_crawler 의 일일 롤링은 LOOKBACK_DAYS=3 뿐이다. 거래일 +3일이 지나
  CMS 에 반영된 건은 다시 받을 기회가 없어 **영구히 누락**된다. 실제로
  L-CA-LA-PHOTOISMKTP-KPOPNATION 의 2026-07-03 13:24·13:29 KFA 2건이
  퀵사이트엔 있는데 우리 XLSX 에는 없었다(그 파일을 07-10 에 받았는데도).
  정산서가 그만큼 과소계상된다.

이 스크립트가 하는 일
  1) 재수집 전 상태를 기록한다(파일별 행수·해시)
  2) photoism_crawler.py 를 기간 지정으로 부른다
  3) 무엇이 늘었는지 날짜별로 보여준다
  4) ingest 는 **하지 않는다** — 결과를 보고 직접 돌리게 남겨 둔다
     (build_photoism_agg 까지 자동으로 태우면 되돌리기 어렵다)

★서버 부담: 크롤러가 국가 간 2초를 쉬므로 30개국 31일이면 30분 이상 걸린다.
  낮에 돌리지 말 것. 중간에 끊겨도 날짜 단위로 파일이 남아 이어받기가 된다.
"""
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).parent
RAW = BASE_DIR / "raw_photoism"


def _snapshot(start: date, end: date) -> dict:
    """기간 내 날짜별 (파일수, 총 바이트). 행수는 열어봐야 알아서 크기로 갈음한다."""
    out = {}
    d = start
    while d <= end:
        key = d.strftime("%Y%m%d")
        fs = sorted(RAW.glob(f"photoism_*_{key}.xlsx"))
        out[key] = (len(fs), sum(f.stat().st_size for f in fs))
        d += timedelta(days=1)
    return out


def _parse(a: str) -> date:
    return datetime.strptime(a, "%Y-%m-%d").date()


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    check_only = "--check" in sys.argv
    if len(args) < 2:
        print(__doc__)
        return 1
    try:
        start, end = _parse(args[0]), _parse(args[1])
    except ValueError as e:
        print(f"날짜 형식 오류: {e}  (YYYY-MM-DD)")
        return 1
    if start > end:
        start, end = end, start
    days = (end - start).days + 1
    if days > 45:
        print(f"기간이 {days}일입니다. 서버 부담이 커서 45일까지만 허용해요.")
        return 1

    before = _snapshot(start, end)
    print(f"재수집 대상: {start} ~ {end} ({days}일)")
    print(f"현재 보유: 파일 {sum(v[0] for v in before.values()):,}개 · "
          f"{sum(v[1] for v in before.values()) / 1024 / 1024:.1f} MB")
    if check_only:
        for k, (n, sz) in before.items():
            print(f"  {k}  파일 {n:2d}개 · {sz / 1024:8,.0f} KB")
        return 0

    print("\n크롤러 실행 — 30개국을 순차로 받습니다. 30분 이상 걸릴 수 있어요.\n")
    r = subprocess.run([sys.executable, str(BASE_DIR / "photoism_crawler.py"),
                        start.isoformat(), end.isoformat()], cwd=str(BASE_DIR))

    after = _snapshot(start, end)
    print("\n" + "=" * 62)
    print("날짜별 변화 (파일수 · 크기)")
    grew = 0
    for k in sorted(before):
        b, a = before[k], after.get(k, (0, 0))
        if b != a:
            grew += 1
            print(f"  {k}  파일 {b[0]}→{a[0]}  "
                  f"{b[1] / 1024:,.0f}→{a[1] / 1024:,.0f} KB  "
                  f"({(a[1] - b[1]) / 1024:+,.0f} KB)")
    if not grew:
        print("  변화 없음 — 이미 최신이거나 CMS 에 추가분이 없어요.")
    print("=" * 62)
    print(f"\n크롤러 종료코드 {r.returncode}")
    print("\n다음 단계 — 결과를 확인한 뒤 직접 실행해 주세요:")
    print("  python photoism_ingest.py        # 파일 → master_photoism")
    print("  python build_photoism_agg.py     # 집계 재생성(대시보드·정산서 반영)")
    return r.returncode


if __name__ == "__main__":
    sys.exit(main())
