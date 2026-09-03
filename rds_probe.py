# -*- coding: utf-8 -*-
"""RDS(datatool) 직결 점검기 — Phase 1 의 첫 삽.

무엇을 하나
  `config.json` 의 `rds` 설정으로 MySQL 에 붙어 **읽기만** 하고,
  우리가 쓸 원천(`seobuk.revenue_raw_data`)이 기대한 모양인지 확인한다.
  쓰기·변경 쿼리는 한 줄도 없다.

왜 필요한가
  퀵사이트 CSV 로는 **거래 고유 ID 가 없어** 복합키 근사(0.007% 불가분)가 한계다.
  원천에 직접 붙으면 매출ID 로 거래 단위 정합이 완전해진다.
  (배경: CURRENT-PROJECTS 의 대사 마트 메모 · 관리자 회신 2026-08-27)

쓰는 법
    python rds_probe.py            # 연결 + 스키마·건수 확인
    python rds_probe.py --sample   # 최근 몇 줄을 마스킹해 훑어보기

★자격증명은 `config.json` 에만 둔다(gitignore 됨). 이 스크립트는 **값을 절대
  화면에 찍지 않는다** — 호스트도 앞 6글자만 보여 준다.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

BASE_DIR = Path(__file__).parent
CONFIG_FILE = BASE_DIR / "config.json"

# 원천 — 관리자 확인(2026-08-27): SP `DAILY_LOAD_REVENUE_DATA` 가 여기에 INSERT IGNORE 한다.
TARGET_DB = "seobuk"
TARGET_TABLE = "revenue_raw_data"

# config.json 에 이 모양으로 넣는다(값은 사람이 직접 채운다).
SHAPE = """
  "rds": {
    "host": "…rds.amazonaws.com",     ← SSM 터널을 쓰면 "127.0.0.1"
    "port": 3306,                      ← 터널이면 로컬 포트(예: 13306)
    "user": "…",
    "password": "…",
    "database": "seobuk"
  }
"""


def log(msg):
    print(msg, flush=True)


def _mask(s, keep=6):
    s = str(s or "")
    return (s[:keep] + "…" + f"({len(s)}자)") if s else "(비어 있음)"


def load_cfg():
    if not CONFIG_FILE.exists():
        log("★ config.json 이 없어요.")
        raise SystemExit(2)
    cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    rds = cfg.get("rds")
    if not rds:
        log("★ config.json 에 `rds` 설정이 없어요. 아래 모양으로 넣어 주세요:")
        log(SHAPE)
        log("  ※ config.json 은 gitignore 되어 있어 커밋되지 않아요.")
        raise SystemExit(2)
    miss = [k for k in ("host", "user", "password") if not str(rds.get(k, "")).strip()]
    if miss:
        log(f"★ `rds` 에 빠진 값: {', '.join(miss)}")
        log(SHAPE)
        raise SystemExit(2)
    return rds


def connect(rds):
    import pymysql
    host, port = str(rds["host"]), int(rds.get("port", 3306))
    log(f"접속 시도 — {_mask(host, 8)}:{port} · db={rds.get('database', TARGET_DB)}")
    if host in ("127.0.0.1", "localhost"):
        log("  (로컬 주소 = SSM/SSH 터널을 통해 붙는 설정이에요. 터널이 떠 있어야 해요.)")
    return pymysql.connect(
        host=host, port=port, user=rds["user"], password=rds["password"],
        database=rds.get("database") or TARGET_DB,
        charset="utf8mb4", connect_timeout=10,
        cursorclass=pymysql.cursors.Cursor,
        read_default_file=None,
    )


def q(cur, sql, args=None):
    cur.execute(sql, args or ())
    return cur.fetchall()


def main():
    rds = load_cfg()
    try:
        conn = connect(rds)
    except Exception as e:                                   # noqa: BLE001
        log(f"\n★ 접속 실패: {type(e).__name__}: {str(e)[:200]}")
        log("\n확인할 것")
        log("  · 터널을 쓰는 설정이면 터널이 떠 있나 (SSM 이면 aws ssm start-session)")
        log("  · 보안그룹이 이 서버 IP를 허용하나")
        log("  · 계정이 읽기 권한을 가진 DB 가 맞나")
        raise SystemExit(1)

    try:
        with conn.cursor() as cur:
            log("\n" + "=" * 62)
            log("1) 서버")
            log("=" * 62)
            log(f"  버전     {q(cur, 'SELECT VERSION()')[0][0]}")
            log(f"  접속계정  {q(cur, 'SELECT CURRENT_USER()')[0][0]}")
            log(f"  현재 DB   {q(cur, 'SELECT DATABASE()')[0][0]}")

            log("\n" + "=" * 62)
            log("2) 보이는 데이터베이스")
            log("=" * 62)
            for (d,) in q(cur, "SHOW DATABASES"):
                mark = "  ←원천" if d == TARGET_DB else ""
                log(f"  {d}{mark}")

            log("\n" + "=" * 62)
            log(f"3) {TARGET_DB}.{TARGET_TABLE} 스키마")
            log("=" * 62)
            cols = q(cur, """
                SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE
                FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s
                ORDER BY ORDINAL_POSITION
            """, (TARGET_DB, TARGET_TABLE))
            if not cols:
                log("  ★ 테이블이 안 보여요 — 권한이나 이름을 확인해 주세요.")
            else:
                log(f"  열 {len(cols)}개")
                for n, t, nul in cols:
                    log(f"    {n:<28} {t:<22} {'NULL' if nul == 'YES' else 'NOT NULL'}")

                log("\n" + "=" * 62)
                log("4) 규모·기간 (읽기 전용)")
                log("=" * 62)
                names = {c[0].lower() for c in cols}
                n = q(cur, f"SELECT COUNT(*) FROM `{TARGET_DB}`.`{TARGET_TABLE}`")[0][0]
                log(f"  행수 {n:,}")
                for c in ("local_payment_dt", "payment_dt", "seq", "id"):
                    if c in names:
                        lo, hi = q(cur, f"SELECT MIN(`{c}`), MAX(`{c}`) "
                                        f"FROM `{TARGET_DB}`.`{TARGET_TABLE}`")[0]
                        log(f"  {c:<20} {lo}  ~  {hi}")

            if "--sample" in sys.argv and cols:
                log("\n" + "=" * 62)
                log("5) 표본 3줄 (열 이름만 보고 값은 줄여서)")
                log("=" * 62)
                cn = [c[0] for c in cols][:8]
                rows = q(cur, f"SELECT {', '.join('`%s`' % c for c in cn)} "
                              f"FROM `{TARGET_DB}`.`{TARGET_TABLE}` LIMIT 3")
                log("  " + " | ".join(cn))
                for r in rows:
                    log("  " + " | ".join(str(x)[:18] for x in r))
    finally:
        conn.close()
    log("\n끝 — 쓰기 쿼리는 하나도 실행하지 않았어요.")


if __name__ == "__main__":
    main()
