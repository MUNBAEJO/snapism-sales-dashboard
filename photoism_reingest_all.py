# -*- coding: utf-8 -*-
"""포토이즘 전량 재적재 — 월 단위로 끊어서, 운영 parquet 은 마지막에 한 번만 교체

    python photoism_reingest_all.py            # 이어서 실행
    python photoism_reingest_all.py --status   # 진행 현황
    python photoism_reingest_all.py --finish   # 다 끝난 뒤 운영본으로 교체 + 집계
    python photoism_reingest_all.py --reset    # 처음부터

왜 이렇게 하나 (2026-08-06)
  · `photoism_ingest.py 2025-01-01` 처럼 **종료일 없이** 부르면 raw 22,620개를
    전부 메모리에 올려 concat 한다 → 16GB 서버에서 MemoryError 로 죽는다(실제 발생).
    종료일을 주면 파일 선택이 그 구간으로 좁혀지므로 월 단위면 1,200개 안팎이다.
  · 매달 운영 parquet(406MB)을 교체하면 그때마다 대시보드가 파일을 잡고 있어
    실패하고(WinError 5) 서비스도 끊긴다. 그래서 **별도 파일**에 쌓고 마지막에
    한 번만 바꾼다. 재적재 도중에도 대시보드는 계속 살아 있다.
  · 집계(6.8M행)는 월마다 만들 이유가 없어 건너뛰고 마지막에 한 번만 만든다.

안전장치
  · 병합 자체는 기존 코드가 이미 월 단위로 안전하다 — 구간 밖 날짜는 그대로 둔다.
  · 한 달이 실패해도 멈추지 않고 기록만 남기고 계속 간다.
  · 중단해도 상태 파일로 이어서 간다.
"""
import json
import os
import shutil
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = Path(__file__).parent
DATA = BASE / "data"
LIVE = DATA / "master_photoism.parquet"
WORK = DATA / "_reingest_master.parquet"
STATE = BASE / "logs" / "reingest_all_state.json"
START = date(2025, 1, 1)


def _months():
    end = date.today()
    out, y, m = [], START.year, START.month
    while (y, m) <= (end.year, end.month):
        first = date(y, m, 1)
        last = date(y + (m == 12), (m % 12) + 1, 1) - timedelta(days=1)
        out.append((first.isoformat(), min(last, end).isoformat()))
        y, m = (y + (m == 12), (m % 12) + 1)
    return out


def _load():
    if STATE.exists():
        try:
            return json.loads(STATE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"done": [], "failed": []}


def _save(s):
    STATE.parent.mkdir(exist_ok=True)
    STATE.write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")


def _rows(f: Path) -> int:
    if not f.exists():
        return 0
    import duckdb
    con = duckdb.connect()
    try:
        return con.execute(f"SELECT COUNT(*) FROM read_parquet('{f.as_posix()}')").fetchone()[0]
    finally:
        con.close()


def main() -> int:
    months = _months()
    st = _load()

    if "--reset" in sys.argv:
        _save({"done": [], "failed": []})
        WORK.unlink(missing_ok=True)
        print("초기화했어요. 다음 실행 때 운영본을 복사해 처음부터 쌓습니다.")
        return 0

    if "--status" in sys.argv:
        done = {tuple(c) for c in st["done"]}
        print(f"전체 {len(months)}개월 · 완료 {len(done)} · 남음 {len(months) - len(done)}")
        if st["failed"]:
            print("실패: " + ", ".join(f"{a[:7]}" for a, b in st["failed"]))
        if WORK.exists():
            print(f"작업본 {WORK.name}: {_rows(WORK):,}행 · {WORK.stat().st_size / 1024**2:.0f} MB")
        print(f"운영본 {LIVE.name}: {_rows(LIVE):,}행")
        return 0

    if "--finish" in sys.argv:
        if not WORK.exists():
            print("작업본이 없어요. 먼저 재적재를 돌려 주세요.")
            return 1
        w, l = _rows(WORK), _rows(LIVE)
        print(f"작업본 {w:,}행 · 운영본 {l:,}행 ({w - l:+,})")
        # ★조금 줄어드는 건 정상이다 — 날짜 기준을 현지시각으로 바꾸면서 2025-01-01
        #   이전으로 밀려난 행은 범위 밖이라 빠진다. 다만 크게 줄면 뭔가 잘못된 것이라
        #   사람이 확인하기 전엔 안 바꾼다.
        LOSS_OK = 0.001                       # 0.1% (약 3.7만 행)
        if l and (l - w) > max(1000, l * LOSS_OK):
            print(f"작업본이 {l - w:,}행 적어요(허용 {int(max(1000, l * LOSS_OK)):,}행 초과).")
            print("교체하지 않습니다 — 먼저 확인해 주세요.")
            return 1
        if w < l:
            print(f"※ {l - w:,}행 줄었어요. 시차 전환으로 범위 밖(2024-12-31)으로 "
                  "밀려난 행이라 정상 범위로 봅니다.")
        bak = DATA / "_backup_20260805" / "master_photoism.parquet.pre_reingest"
        bak.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(LIVE, bak)
        print(f"운영본 백업: {bak.name}")
        try:
            os.replace(WORK, LIVE)
        except PermissionError:
            print("교체 실패 — 대시보드(8503)가 parquet 을 잡고 있어요.")
            print("  대시보드를 내리고 --finish 를 다시 실행해 주세요.")
            return 1
        print("교체 완료. 집계를 다시 만듭니다...")
        r = subprocess.run([sys.executable, str(BASE / "build_photoism_agg.py")], cwd=str(BASE))
        print(f"집계 종료코드 {r.returncode}")
        return r.returncode

    # ── 재적재 ──
    if not WORK.exists():
        print(f"운영본을 작업본으로 복사합니다 ({LIVE.stat().st_size / 1024**2:.0f} MB)...")
        shutil.copy2(LIVE, WORK)

    done = {tuple(c) for c in st["done"]}
    todo = [m for m in months if tuple(m) not in done]
    if not todo:
        print("모든 달 완료. 마무리하세요:  python photoism_reingest_all.py --finish")
        return 0

    env = dict(os.environ, PHOTOISM_MASTER=str(WORK), PHOTOISM_SKIP_AGG="1")
    print(f"전량 재적재: {len(months)}개월 중 {len(todo)}개 남음")
    print(f"작업본: {WORK.name} (운영 대시보드는 계속 살아 있습니다)\n")

    for i, (a, b) in enumerate(todo, 1):
        t0 = datetime.now()
        before = _rows(WORK)
        print(f"[{i}/{len(todo)}] {a[:7]} 시작 {t0:%H:%M:%S} (현재 {before:,}행)", flush=True)
        r = subprocess.run([sys.executable, str(BASE / "photoism_ingest.py"), a, b],
                           cwd=str(BASE), env=env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        mins = (datetime.now() - t0).total_seconds() / 60
        after = _rows(WORK)
        if r.returncode == 0:
            st["done"].append([a, b])
            # ★재시도로 성공했으면 실패 목록에서 빼야 한다. 안 지우면 다 됐는데도
            #   '실패 7개월' 이 그대로 찍혀서 뭐가 남았는지 알 수 없다.
            st["failed"] = [f for f in st["failed"] if list(f) != [a, b]]
            print(f"    완료 ({mins:.0f}분) {before:,} -> {after:,}행 ({after - before:+,})", flush=True)
        else:
            st["failed"].append([a, b])
            tail = (r.stderr or "").strip().splitlines()[-1:] or ["(원인 미상)"]
            print(f"    실패 exit={r.returncode} ({mins:.0f}분) — {tail[0][:100]}", flush=True)
        _save(st)

    print("\n" + "=" * 58)
    print(f"재적재 종료 · 완료 {len(st['done'])} · 실패 {len(st['failed'])}개월")
    if st["failed"]:
        print("실패한 달:")
        for a, b in st["failed"]:
            print(f"  python photoism_ingest.py {a} {b}   (PHOTOISM_MASTER 설정 필요)")
    print("\n마지막 단계 — 확인 후 교체:")
    print("  python photoism_reingest_all.py --status")
    print("  python photoism_reingest_all.py --finish   # 대시보드 내리고")
    return 0


if __name__ == "__main__":
    sys.exit(main())
