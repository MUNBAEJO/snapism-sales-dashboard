# -*- coding: utf-8 -*-
"""원천(RDS) ↔ 우리 원장 대사 — Phase 1.

Phase 0 은 퀵사이트에서 손으로 내려받은 CSV 와 맞췄다(`ipx_recon.py`).
그건 **거래 고유 ID 가 없어** 복합키 근사에서 0.007% 가 원리적으로 안 갈렸고,
퀵사이트 자체가 늦어서 '경보'가 곧 사고인지 지연인지 구분이 안 됐다.
이제 원천에 직접 붙어 **같은 축(국가 × 타이틀 × 프레임)** 으로 맞춘다.

    python rds_recon.py 2026-08-01 2026-08-31

★★개인정보 열은 **쿼리에 넣지 않는다** — 대사에 필요한 건 금액·건수뿐이다.
  (`phone_number`·`coupon_num`·`cancel_reason`·`is_extra_paid`)
★쓰기 쿼리는 하나도 없다. 집계는 **서버에서** 한다 — 원시 행을 끌어오면
  수백만 행이 오가고 RDS 에도 부담이다.
★기준 시각은 `local_payment_dt`(현지시각)다. `payment_dt` 는 UTC 라 우리 원장·
  퀵사이트와 기준이 달라진다(서머타임 미반영 문제도 있다).
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).parent
CONFIG_FILE = BASE_DIR / "config.json"
LEDGER = BASE_DIR / "data" / "master_photoism.parquet"
OUT_DIR = BASE_DIR / "reports"


def log(m):
    print(m, flush=True)


def _conn():
    r = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))["rds"]
    import pymysql
    return pymysql.connect(
        host=r["host"], port=int(r["port"]), user=r["user"],
        password=r["password"], database=r.get("database") or "seobuk",
        charset="utf8mb4", connect_timeout=20, read_timeout=1800)


def source_rows(start: str, end: str):
    """원천 집계 — (국가, 타이틀, 프레임) → 건수·금액. **서버에서** 묶는다."""
    sql = """
        SELECT country_cd, title_name, frame_name,
               COUNT(*) AS n, SUM(total_price) AS amt
        FROM revenue_raw_data
        WHERE local_payment_dt >= %s AND local_payment_dt < DATE_ADD(%s, INTERVAL 1 DAY)
        GROUP BY 1, 2, 3
    """
    con = _conn()
    try:
        cur = con.cursor()
        t = time.time()
        cur.execute(sql, (start, end))
        rows = cur.fetchall()
        log(f"  원천 {len(rows):,}조합 · {time.time() - t:.1f}s")
        return rows
    finally:
        con.close()


def ledger_rows(start: str, end: str):
    import duckdb
    con = duckdb.connect(config={"memory_limit": "1500MB", "threads": 4})
    try:
        t = time.time()
        df = con.execute(f"""
            SELECT UPPER(CAST("국가코드" AS VARCHAR)) cc,
                   CAST("타이틀명" AS VARCHAR) t,
                   CAST("프레임 이름" AS VARCHAR) f,
                   COUNT(*) n, SUM(TRY_CAST("최종 결제 금액" AS BIGINT)) amt
            FROM read_parquet('{LEDGER.as_posix()}')
            WHERE CAST("날짜" AS VARCHAR) BETWEEN '{start}' AND '{end}'
            GROUP BY 1, 2, 3
        """).fetchall()
        log(f"  원장 {len(df):,}조합 · {time.time() - t:.1f}s")
        return df
    finally:
        con.close()


def main():
    a = sys.argv[1:]
    start = a[0] if a else "2026-08-01"
    end = a[1] if len(a) > 1 else "2026-08-31"
    log(f"기간 {start} ~ {end}  (기준 local_payment_dt · 현지시각)\n")

    src = {(str(c or "").upper(), str(t or ""), str(f or "")): (int(n), int(m or 0))
           for c, t, f, n, m in source_rows(start, end)}
    led = {(str(c or "").upper(), str(t or ""), str(f or "")): (int(n), int(m or 0))
           for c, t, f, n, m in ledger_rows(start, end)}

    keys = set(src) | set(led)
    same = both = only_s = only_l = 0
    diff_amt = 0
    rows = []
    for k in keys:
        s = src.get(k)
        l = led.get(k)
        if s and l:
            both += 1
            if s == l:
                same += 1
            else:
                diff_amt += s[1] - l[1]
                rows.append((k, s, l, "금액·건수 상이"))
        elif s:
            only_s += 1
            diff_amt += s[1]
            rows.append((k, s, (0, 0), "원장에 없음"))
        else:
            only_l += 1
            diff_amt -= l[1]
            rows.append((k, (0, 0), l, "원천에 없음"))

    log("\n" + "=" * 70)
    log("판정")
    log("=" * 70)
    log(f"  조합 {len(keys):,}개")
    log(f"  ✅ 완전 일치        {same:>8,}  ({same / max(len(keys),1) * 100:.2f}%)")
    log(f"  ⚠️ 금액·건수 상이   {both - same:>8,}")
    log(f"  ⚠️ 원장에 없음      {only_s:>8,}   ← 우리가 아직 못 받은 것")
    log(f"  ⚠️ 원천에 없음      {only_l:>8,}   ← 원장에만 있는 것(테스트·정정 등)")
    log(f"\n  금액 차(원천−원장) {diff_amt:>+14,}   ※통화 혼재라 참고값")

    if rows:
        rows.sort(key=lambda x: -abs(x[1][1] - x[2][1]))
        log(f"\n=== 차이 상위 15 ===")
        for (cc, t, f), s, l, why in rows[:15]:
            log(f"  [{why}] {cc} · {t} · {f}")
            log(f"      원천 {s[1]:>12,}/{s[0]:<6}  원장 {l[1]:>12,}/{l[0]:<6}"
                f"  차 {s[1]-l[1]:+,}")
        OUT_DIR.mkdir(exist_ok=True)
        out = OUT_DIR / f"rds_recon_{start}_{end}.csv"
        import csv
        with open(out, "w", newline="", encoding="utf-8-sig") as fp:
            w = csv.writer(fp)
            w.writerow(["cc", "title", "frame", "src_n", "src_amt",
                        "led_n", "led_amt", "verdict"])
            for (cc, t, f), s, l, why in rows:
                w.writerow([cc, t, f, s[0], s[1], l[0], l[1], why])
        log(f"\n전체 목록: {out}")
    return 0 if not rows else 1


if __name__ == "__main__":
    raise SystemExit(main())
