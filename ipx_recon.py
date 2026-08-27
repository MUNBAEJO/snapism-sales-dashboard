# -*- coding: utf-8 -*-
"""퀵사이트 `ipx 정산` CSV ↔ 우리 원장 대조 (정산 발행 전 점검).

왜 필요한가
-----------
정산서를 만들기 전에 담당자가 퀵사이트 CSV를 받아 **눈으로** 대조해 왔다. 그걸 자동화한다.
퀵사이트는 원천(RDS `seobuk.revenue_raw_data`)을 보고, 우리는 CMS 수집분을 본다 —
같은 거래를 두 경로로 받는 셈이라, 어긋나면 둘 중 하나가 틀린 것이다.

★**이건 커버리지 감시(coverage_audit)와 다른 검증이다.** 커버리지는 '어제 대비 오늘이
  이상한가'를 보는 자기 대조라 **1건 단위 누락을 절대 못 잡는다**. 실제로 2026-08-27 에
  `렌탈 260322 FC서울·송민규` 7,000원 1건이 빠져 있었는데 커버리지는 매일 정상이라 했고,
  이 대조가 잡았다(원인 = 키오스크가 12일 늦게 올림 · 지금은 양쪽 다 갖고 있다).

검증된 매핑 규칙 (2026-08-27 실측 · 21,998행 / 76억 대조)
--------------------------------------------------------
· ★`거래액` = 우리 **`최종 결제 금액`** 이다(상품총액 아님 — 그건 76.6%만 맞는다).
  14,362 조합 중 14,361 일치, 총합 7,629,080,780 vs 7,629,073,780 (차 7,000원 = 위 1건).
· `결제건수` ≈ 우리 **행수**(98.3%). 완전히 같지는 않아 **경보 대상이 아니고 참고만** 한다.
· ★**0원 거래를 빼면 안 된다.** 빼면 오히려 791조합이 어긋난다(0원은 합계에 영향이 없고
  ipx 는 '전부 0원인 조합'만 안 싣는다). 그래서 **0원 포함이 맞는 모델**이다.
· 국가는 ipx 가 `KR`, 우리는 `국가코드`(소문자) → upper 로 맞춘다.
· 취소는 제외한다(ipx 는 정산 대상만 싣는다).
· ★비교 축은 **(국가 × 타이틀 × 프레임)** 이다. ipx 에는 테마가 있지만 **우리 원장엔 없어서**
  테마는 합산해 버린다. 같은 (월·타이틀·프레임)이 테마별로 여러 줄 나오므로 **반드시 먼저 합칠 것.**

무엇을 경보로 보는가
--------------------
★차이가 곧 사고는 아니다. 실측에서 나온 차이는 **대부분 정상**이었다:
  · 원장에만 있고 **금액 0원** → 정상(쿠폰·코인 전액결제 조합. ipx 는 안 싣는다)
경보는 아래 둘뿐이다. 이 분류를 안 하면 매일 수백 건짜리 오탐이 나간다.
  · **금액이 다른 조합**
  · **한쪽에만 있으면서 금액 > 0**

실행
----
    python ipx_recon.py <ipx_csv> [시작일 종료일]
    python ipx_recon.py "C:/.../CSV_Download__1787812696686.csv" 2026-07-21 2026-08-23

★시작·종료일은 **퀵사이트에서 CSV 를 받을 때 건 필터와 똑같이** 줘야 한다. ipx 는 월 단위로
  집계돼 나오므로 파일만 봐서는 실제 기간을 알 수 없다. 생략하면 `결제일(월)` 로 월 전체를
  가정하는데, 부분 월을 받았다면 그 가정이 틀리므로 경고를 띄운다.

종료코드: 경보 0건이면 0, 있으면 1 (스케줄러가 알아챌 수 있게).
"""
from __future__ import annotations

import sys
from pathlib import Path

import duckdb

# ★윈도 콘솔 기본이 cp949 라 그냥 두면 한글이 통째로 깨진다(스케줄러 로그에서도 마찬가지).
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:                                   # noqa: BLE001 — 구버전 파이썬 대비
    pass

BASE_DIR = Path(__file__).parent
LEDGER = BASE_DIR / "data" / "master_photoism.parquet"
OUT_DIR = BASE_DIR / "reports"

# 금액 비교 허용 오차. 통화 단위가 나라마다 달라 반올림이 섞일 수 있어 1 미만은 같게 본다.
EPS = 0.99


def _log(msg: str = "") -> None:
    print(msg, flush=True)


def _months(con, csv: str) -> list[str]:
    """CSV 안의 `결제일 (월)` 값들 → ['2026-07', '2026-08']

    ★엉뚱한 탭(raw data 등)을 넣으면 이 열이 없다. 그때 트레이스백을 뱉으면 쓰는 사람이
      '무슨 파일을 넣어야 하나'를 알 수 없으므로, 빈 목록을 돌려 호출부가 안내하게 한다.
    """
    try:
        rows = con.execute(f"""
            SELECT DISTINCT substr(CAST("결제일 (월)" AS VARCHAR), 1, 7) m
            FROM read_csv_auto('{csv}', all_varchar=true) ORDER BY 1
        """).fetchall()
    except Exception:                               # noqa: BLE001 — 열 없음·파싱 실패 등
        return []
    return [r[0] for r in rows if r[0]]


def _has_cols(con, csv: str) -> bool:
    """ipx 정산 탭인지 — 필요한 열이 다 있나."""
    need = {"결제일 (월)", "국가", "타이틀", "프레임", "결제건수", "거래액"}
    try:
        cols = {d[0] for d in con.execute(
            f"SELECT * FROM read_csv_auto('{csv}', all_varchar=true) LIMIT 0").description}
    except Exception:                               # noqa: BLE001
        return False
    return need <= cols


def run(csv_path: str, start: str | None, end: str | None) -> int:
    csv = Path(csv_path).as_posix()
    if not Path(csv_path).exists():
        _log(f"[중단] 파일이 없어요: {csv_path}")
        return 2
    if not LEDGER.exists():
        _log(f"[중단] 원장이 없어요: {LEDGER}")
        return 2

    con = duckdb.connect(config={"memory_limit": "900MB", "threads": 2})

    # ── 올바른 탭에서 받은 파일인가 ────────────────────────────────────────
    if not _has_cols(con, csv):
        _log("[중단] ipx 정산 탭 파일이 아니에요.")
        _log("       퀵사이트 → [photoism] Admin CSV 다운로드 → **ipx 정산 csv Download** 탭에서")
        _log("       표 우측 상단 점 3개 → CSV로 내보내기 로 받은 파일이 필요해요.")
        _log("       (필요한 열: 결제일 (월) · 국가 · 타이틀 · 프레임 · 결제건수 · 거래액)")
        return 2

    # ── 기간 정하기 ────────────────────────────────────────────────────────
    months = _months(con, csv)
    if not months:
        _log("[중단] CSV 에서 `결제일 (월)` 을 못 읽었어요.")
        return 2
    if not (start and end):
        start = months[0] + "-01"
        # 월말은 DuckDB 에 맡긴다(윤년·30/31일 계산을 직접 하지 않는다)
        end = con.execute(
            f"SELECT CAST(last_day(DATE '{months[-1]}-01') AS VARCHAR)").fetchone()[0]
        _log(f"[주의] 기간을 안 줘서 월 전체로 가정했어요 → {start} ~ {end}")
        _log("       퀵사이트에서 부분 기간으로 받았다면 결과가 틀려요. 인자로 기간을 주세요.")

    _log(f"기간 {start} ~ {end} · 월 {', '.join(months)}")

    # ── 두 소스 ───────────────────────────────────────────────────────────
    # ipx: 테마별로 여러 줄이라 (국가·타이틀·프레임) 으로 먼저 합친다.
    con.execute(f"""
        CREATE VIEW ipx AS
        SELECT "국가" cc, "타이틀" title, "프레임" frame,
               SUM(TRY_CAST("결제건수" AS BIGINT))  AS cnt,
               SUM(TRY_CAST("거래액"   AS DOUBLE)) AS amt
        FROM read_csv_auto('{csv}', all_varchar=true)
        GROUP BY 1, 2, 3
    """)
    # 원장: ipx 에 있는 타이틀만, 같은 기간, 취소 제외, 0원 포함.
    con.execute(f"""
        CREATE VIEW led AS
        SELECT upper(CAST("국가코드" AS VARCHAR))  cc,
               CAST("타이틀명"   AS VARCHAR)       title,
               CAST("프레임 이름" AS VARCHAR)      frame,
               COUNT(*)                            AS cnt,
               SUM(TRY_CAST("최종 결제 금액" AS DOUBLE)) AS amt
        FROM read_parquet('{LEDGER.as_posix()}')
        WHERE CAST("결제일시" AS VARCHAR) >= '{start}'
          AND CAST("결제일시" AS VARCHAR) <= '{end} 23:59:59'
          AND NOT "취소 여부"
          AND CAST("타이틀명" AS VARCHAR) IN (SELECT DISTINCT title FROM ipx)
        GROUP BY 1, 2, 3
    """)

    # ── 대조 + 판정 ───────────────────────────────────────────────────────
    con.execute(f"""
        CREATE VIEW cmp AS
        SELECT COALESCE(i.cc, l.cc)       cc,
               COALESCE(i.title, l.title) title,
               COALESCE(i.frame, l.frame) frame,
               i.amt AS ipx_amt, l.amt AS led_amt,
               i.cnt AS ipx_cnt, l.cnt AS led_cnt,
               CASE
                 WHEN i.cc IS NULL AND COALESCE(l.amt, 0) = 0 THEN '정상·원장0원'
                 WHEN i.cc IS NULL                            THEN '경보·원장에만'
                 WHEN l.cc IS NULL AND COALESCE(i.amt, 0) = 0 THEN '정상·ipx0원'
                 WHEN l.cc IS NULL                            THEN '경보·ipx에만'
                 WHEN abs(i.amt - l.amt) > {EPS}              THEN '경보·금액상이'
                 ELSE '일치'
               END AS verdict
        FROM ipx i FULL OUTER JOIN led l USING (cc, title, frame)
    """)

    total = con.execute("""
        SELECT COALESCE(SUM(ipx_amt), 0), COALESCE(SUM(led_amt), 0), COUNT(*)
        FROM cmp""").fetchone()
    _log(f"\n조합 {total[2]:,}개 · ipx 합 {total[0]:,.0f} · 원장 합 {total[1]:,.0f} "
         f"· 차 {total[0] - total[1]:,.0f}")
    _log("  ※ 통화가 나라마다 달라 합계 자체는 참고용이에요. 판정은 조합 단위로 해요.")

    _log("\n판정별 조합 수")
    for v, n, a in con.execute("""
            SELECT verdict, COUNT(*), COALESCE(SUM(COALESCE(ipx_amt,0) - COALESCE(led_amt,0)), 0)
            FROM cmp GROUP BY 1 ORDER BY 2 DESC""").fetchall():
        mark = "!!" if v.startswith("경보") else "  "
        _log(f"  {mark} {v:<14} {n:>7,}개   차액 {a:>16,.0f}")

    alerts = con.execute("SELECT COUNT(*) FROM cmp WHERE verdict LIKE '경보%'").fetchone()[0]

    if alerts:
        _log(f"\n=== 경보 {alerts:,}건 (차액 큰 순 · 상위 20) ===")
        for r in con.execute("""
                SELECT verdict, cc, title, frame, ipx_amt, led_amt
                FROM cmp WHERE verdict LIKE '경보%'
                ORDER BY abs(COALESCE(ipx_amt,0) - COALESCE(led_amt,0)) DESC LIMIT 20""").fetchall():
            _log(f"  [{r[0]}] {r[1]} · {str(r[2])[:30]} · {str(r[3])[:20]}"
                 f"  ipx {r[4] if r[4] is not None else '-'} / 원장 {r[5] if r[5] is not None else '-'}")
        OUT_DIR.mkdir(exist_ok=True)
        out = OUT_DIR / f"ipx_recon_{start}_{end}.csv"
        con.execute(f"""COPY (SELECT * FROM cmp WHERE verdict LIKE '경보%'
                          ORDER BY abs(COALESCE(ipx_amt,0) - COALESCE(led_amt,0)) DESC)
                     TO '{out.as_posix()}' (HEADER, DELIMITER ',')""")
        _log(f"\n전체 목록: {out}")
        _log("\n★경보가 곧 우리 잘못은 아니에요. 대개는 **CMS 가 늦게 받은 거래**예요"
             "(최대 12일 관측). `photoism_reget.py` 로 해당 기간을 다시 받으면 대개 해소돼요.")
    else:
        _log("\n이상 없어요. 정산 발행해도 괜찮아요.")

    con.close()
    return 1 if alerts else 0


def main() -> int:
    if len(sys.argv) < 2:
        _log(__doc__)
        return 2
    csv_path = sys.argv[1]
    start = sys.argv[2] if len(sys.argv) > 2 else None
    end = sys.argv[3] if len(sys.argv) > 3 else None
    return run(csv_path, start, end)


if __name__ == "__main__":
    sys.exit(main())
