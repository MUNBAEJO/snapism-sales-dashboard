# -*- coding: utf-8 -*-
"""수집 커버리지 감사 — '조용한 결손'을 찾아낸다.

★왜 필요한가 (2026-08-03)
  기존 신선도 체크(`views/0_🎯_KPI목표.py`)는 **브랜드 전체 최종일** 하나만 본다.
  30개국 중 한국만 빠져도 최종일은 오늘이라 절대 안 걸린다. 실제로 포토이즘
  2025년의 02-02·03-01·06-02·07-02·08-01·11-02·12-02 **7일이 ~99% 비어 있었는데**
  1년 반 동안 아무도 몰랐다(원본 엑셀은 다 있었고 ingest 가 흘린 것).

  '하루가 통째로 0건'인 날은 0일이었다. 소규모 국가 몇 개가 남아 날짜 자체는
  존재했기 때문이다. 그래서 **행수 급감**과 **국가 이탈**을 따로 봐야 한다.

검사 항목
  1) 완전 결손일  — 기간 안에 날짜가 아예 없음
  2) 부분 결손일  — 전후 7일 이동중앙값 대비 THRESH 미만 (위 7일이 여기 걸린다)
  3) 국가 이탈    — 평소 있던 국가가 특정 날짜에만 빠짐
  4) 신선도       — 최종일이 며칠 밀렸나

실행
  python coverage_audit.py                 # 두 브랜드 전체 기간
  python coverage_audit.py --days 180      # 최근 180일 데이터만 읽는다
  python coverage_audit.py --recent 14     # 비교는 전 기간, 보고만 최근 14일
  python coverage_audit.py --json          # 기계용(스케줄러 알림에서 사용)

읽기 전용이다. 어떤 파일도 쓰지 않는다.
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"

# 브랜드별 canonical parquet 과 국가 컬럼
SOURCES = {
    "포토이즘": {"parq": DATA_DIR / "master_photoism.parquet", "nat": "국가"},
    "스내피즘": {"parq": DATA_DIR / "master.parquet", "nat": "국가"},
}

THRESH = 0.30      # 같은 요일 중앙값 대비 이 비율 미만이면 부분 결손 의심
WINDOW = 7         # 비교 창: 앞뒤 각 7주(같은 요일끼리)
STALE_DAYS = 2     # 최종일이 이만큼 밀리면 신선도 경고
NAT_MIN_DAYS = 30      # 국가 이탈: 최근 이 기간의 날짜를 검사한다
NAT_WEEKDAY_DAYS = 84  # 국가 이탈: 요일별 등장률은 이 기간(12주)으로 학습한다

# ★소음 차단 기준 — 이게 없으면 도구가 못 쓰게 된다.
#   스내피즘 오픈 직후(2025-05)는 하루 30건 수준이라 '0건인 날'이 정상이었고,
#   몽골·룩셈부르크처럼 드문드문 팔리는 국가는 안 팔린 날이 수두룩하다.
#   그걸 다 경고로 올리면 진짜 사고(2025년 7일 결손)가 묻힌다.
MIN_MEDIAN = 200   # 주변 중앙값이 이보다 작은 구간은 '아직 규모가 없다'고 보고 건너뛴다
NAT_MIN_ROWS = 30  # 국가 이탈: 평소 하루 이만큼은 나오는 국가만 대상으로 본다

# ★최근 며칠은 아예 판정하지 않는다 — 시차 때문이다.
#   CMS 파일은 한국 날짜로 끊기는데 서쪽 국가는 그보다 늦다. 실제로
#   photoism_de_20260802.xlsx 안에 들어 있는 건 08-01 거래다(독일 = 한국-7h).
#   그래서 유럽·미주는 **가장 최근 하루가 늘 비어 있다가 다음 날 채워진다.**
#   이걸 안 빼면 매일 아침 '독일 이탈' 같은 헛알림이 뜬다.
LAG_DAYS = 2


def _con():
    import duckdb
    con = duckdb.connect()
    con.execute("PRAGMA memory_limit='512MB'")   # 대시보드와 같은 서버라 넉넉히 안 잡는다
    con.execute("PRAGMA threads=2")
    return con


def audit_brand(name: str, parq: Path, nat: str, days: int | None = None,
                report_days: int | None = None) -> dict:
    """브랜드 하나를 감사해 문제 목록을 돌려준다. 파일이 없으면 skipped.

    days        : 이 기간만 읽는다(비교 기준을 만들려면 넉넉해야 한다).
    report_days : 문제를 이 기간 안의 것만 보고한다. **매일 도는 알림용** —
                  안 그러면 1년 전 결손을 매일 다시 알린다. 비교용 이력은
                  그대로 쓰면서 보고만 좁히는 게 핵심이라 인자를 둘로 나눴다.
    """
    if not parq.exists():
        return {"brand": name, "skipped": f"{parq.name} 없음", "problems": []}

    src = str(parq).replace("\\", "/")
    con = _con()
    try:
        where = ""
        if days:
            since = date.today() - timedelta(days=days)
            where = f"WHERE TRY_CAST(\"날짜\" AS DATE) >= DATE '{since}'"

        rows = con.execute(f"""
            SELECT TRY_CAST("날짜" AS DATE) d, COUNT(*) n, COUNT(DISTINCT "{nat}") c
            FROM read_parquet('{src}') {where}
            GROUP BY 1 HAVING d IS NOT NULL ORDER BY 1""").fetchall()
        if not rows:
            return {"brand": name, "skipped": "데이터 없음", "problems": []}

        # 국가 이탈 판정용: 날짜별 국가 집합
        nat_rows = con.execute(f"""
            SELECT TRY_CAST("날짜" AS DATE) d, "{nat}" g, COUNT(*) n
            FROM read_parquet('{src}') {where}
            GROUP BY 1,2 HAVING d IS NOT NULL""").fetchall()
    finally:
        con.close()

    first, last = rows[0][0], rows[-1][0]
    counts = {d: n for d, n, _ in rows}
    problems = []
    settled = last - timedelta(days=LAG_DAYS)   # 이 날짜까지만 판정한다(시차 여유)

    # ── 0) 날짜별 '주변 규모' 계산 (1·2 에서 같이 쓴다) ───────────────
    # 빠진 날도 자리를 채워야 그 앞뒤 규모를 볼 수 있다.
    span, d = [], first
    while d <= last:
        span.append((d, counts.get(d, 0)))
        d += timedelta(days=1)

    def _median_around(i: int) -> int:
        """★같은 요일끼리만 비교한다.

        그냥 앞뒤 7일 중앙값을 쓰면 주말이 평일의 3~5배인 이 데이터에서
        월요일이 늘 '결손 의심'으로 걸린다(실제로 그렇게 오탐이 났다).
        전후 WINDOW 주 범위의 같은 요일들과 견준다.
        """
        step, need = 7, WINDOW
        neigh = sorted(span[j][1] for j in range(i - step * need, i + step * need + 1, step)
                       if 0 <= j < len(span) and j != i)
        return neigh[len(neigh) // 2] if len(neigh) >= 5 else 0

    # ── 1) 완전 결손일 ────────────────────────────────────────────────
    for i, (d, n) in enumerate(span):
        if n or d > settled:
            continue
        med = _median_around(i)
        if med < MIN_MEDIAN:      # 오픈 초기처럼 원래 거래가 거의 없던 구간
            continue
        problems.append({"kind": "완전결손", "date": str(d),
                         "detail": f"그날 데이터가 한 건도 없음 (주변 중앙값 {med:,}건)"})

    # ── 2) 부분 결손일 ────────────────────────────────────────────────
    # 마지막 날은 수집이 아직 안 끝났을 수 있어 제외한다.
    for i, (d, n) in enumerate(span):
        if d > settled or not n:        # 0건은 위 '완전결손'에서 이미 처리
            continue
        med = _median_around(i)
        if med < MIN_MEDIAN:
            continue
        if n < med * THRESH:
            problems.append({
                "kind": "부분결손", "date": str(d),
                "detail": f"{n:,}건 (주변 중앙값 {med:,}건의 {n / med * 100:.1f}%)"})

    # ── 3) 국가 이탈 ──────────────────────────────────────────────────
    # '상시 국가' = 최근 NAT_MIN_DAYS 중 8할 이상 등장 **그리고** 평소 하루 NAT_MIN_ROWS 이상.
    # 뒤 조건이 없으면 몽골·룩셈부르크처럼 드문드문 팔리는 곳의 정상적인 0건이 계속 걸린다.
    by_day: dict[date, set] = {}
    vol: dict[str, list] = {}
    for d, g, n in nat_rows:
        by_day.setdefault(d, set()).add(g)
        vol.setdefault(g, []).append(n)
    recent = [d for d in sorted(by_day)
              if last - timedelta(days=NAT_MIN_DAYS) < d <= settled]
    if len(recent) >= 10:
        def _typical(g: str) -> int:
            v = sorted(vol.get(g, []))
            return v[len(v) // 2] if v else 0

        # ★규모도 **요일별로** 본다. 등장률만 요일별로 보면 아직 오탐이 난다.
        #   독일은 월~토 112~368행인데 일요일만 5행이다(거의 안 판다). 전체 중앙값
        #   109행으로 '상시 국가' 문턱(30행)을 넘겨 버리니, 일요일에 0행이면
        #   "평소 109건이 빠졌다"고 알림이 뜬다 — 2026-08-02 에 실제로 그랬다.
        #   요일별 중앙값(일요일 5행)으로 재면 문턱 아래라 애초에 대상이 아니다.
        vol_wd: dict[tuple, list] = {}
        for d, g, n in nat_rows:
            vol_wd.setdefault((g, d.weekday()), []).append(n)

        def _typical_on(g: str, w: int) -> int:
            v = sorted(vol_wd.get((g, w), []))
            return v[len(v) // 2] if v else 0

        # ★요일별 등장률로 본다. 룩셈부르크는 일요일 0/9 로 아예 안 파는데,
        #   요일을 안 보면 일요일마다 '국가 이탈'로 잡혀 알림이 못 쓰게 된다.
        wk_days: dict[int, list] = {}
        for d in sorted(by_day):
            if d > last - timedelta(days=NAT_WEEKDAY_DAYS):
                wk_days.setdefault(d.weekday(), []).append(d)

        def _regular_on(w: int) -> set:
            ds = wk_days.get(w, [])
            if len(ds) < 4:
                return set()
            seen: dict[str, int] = {}
            for d in ds:
                for g in by_day[d]:
                    seen[g] = seen.get(g, 0) + 1
            return {g for g, c in seen.items()
                    if c >= len(ds) * 0.8 and _typical_on(g, w) >= NAT_MIN_ROWS}

        for d in recent:
            w = d.weekday()
            gone = sorted(_regular_on(w) - by_day[d], key=lambda g: -_typical_on(g, w))
            # 부분·완전 결손으로 이미 잡힌 날은 중복 보고하지 않는다
            if gone and not any(p["date"] == str(d) for p in problems):
                problems.append({
                    "kind": "국가이탈", "date": str(d),
                    "detail": f"평소 있던 {len(gone)}개국 빠짐: "
                              + ", ".join(f"{g}({'월화수목금토일'[w]}요일 평소 "
                                          f"{_typical_on(g, w):,}건)" for g in gone[:6])})

    # ── 4) 신선도 ─────────────────────────────────────────────────────
    lag = (date.today() - last).days
    if lag >= STALE_DAYS:
        problems.append({"kind": "신선도", "date": str(last),
                         "detail": f"최종일이 {lag}일 밀림"})

    if report_days:
        cut = str(date.today() - timedelta(days=report_days))
        problems = [p for p in problems if p["date"] >= cut]

    return {"brand": name, "first": str(first), "last": str(last),
            "days": len(rows), "rows": sum(counts.values()), "problems": problems}


def audit(days: int | None = None, report_days: int | None = None) -> list[dict]:
    """전 브랜드 감사. 스케줄러 알림에서 이 함수를 부른다."""
    return [audit_brand(n, s["parq"], s["nat"], days, report_days)
            for n, s in SOURCES.items()]


def summary_text(results: list[dict]) -> str:
    """알림 메일 본문으로 쓸 한 덩어리 텍스트. 문제가 없으면 빈 문자열."""
    out = []
    for r in results:
        if not r.get("problems"):
            continue
        out.append(f"[{r['brand']}] 문제 {len(r['problems'])}건")
        for p in r["problems"][:20]:
            out.append(f"  · {p['date']} {p['kind']} — {p['detail']}")
        if len(r["problems"]) > 20:
            out.append(f"  … 외 {len(r['problems']) - 20}건")
    return "\n".join(out)


def main() -> int:
    # 콘솔이 cp949 라 '—' 같은 글자에서 UnicodeEncodeError 로 죽는다(실제로 죽었다).
    # 알림은 이미 나간 뒤라 조용히 지나갔지만, 사람이 직접 돌리면 마지막에 터진다.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    args = sys.argv[1:]
    as_json = "--json" in args
    days = report_days = None
    for flag, setter in (("--days", "days"), ("--recent", "report_days")):
        if flag in args:
            try:
                v = int(args[args.index(flag) + 1])
            except (IndexError, ValueError):
                print(f"{flag} 뒤에 숫자를 주세요"); return 2
            if setter == "days":
                days = v
            else:
                report_days = v

    results = audit(days, report_days)

    if as_json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return 1 if any(r.get("problems") for r in results) else 0

    bad = 0
    for r in results:
        print(f"\n=== {r['brand']} ===")
        if r.get("skipped"):
            print(f"  건너뜀 — {r['skipped']}")
            continue
        print(f"  기간 {r['first']} ~ {r['last']} · {r['days']}일 · {r['rows']:,}건")
        ps = r["problems"]
        if not ps:
            print("  문제 없음")
            continue
        bad += len(ps)
        for kind in ("완전결손", "부분결손", "국가이탈", "신선도"):
            sel = [p for p in ps if p["kind"] == kind]
            if not sel:
                continue
            print(f"  ── {kind} {len(sel)}건")
            for p in sel[:15]:
                print(f"     {p['date']}  {p['detail']}")
            if len(sel) > 15:
                print(f"     … 외 {len(sel) - 15}건")

    print(f"\n{'문제 없음' if not bad else f'★ 총 {bad}건 — 확인이 필요합니다'}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
