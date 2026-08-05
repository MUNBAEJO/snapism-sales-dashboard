# -*- coding: utf-8 -*-
"""포토이즘 전량 재수집 (2025-01-01 ~ 어제) — 30일씩 끊어서, 중단하면 이어서

    python photoism_resync_all.py            # 이어서 실행
    python photoism_resync_all.py --status   # 진행 현황만
    python photoism_resync_all.py --reset    # 처음부터 다시

왜 필요한가(2026-08-05 규명)
  photoism_crawler 의 일일 롤링이 3일뿐이라, 거래일 +3일이 지나 CMS 에 반영된
  거래는 다시 받을 기회가 없어 영구 누락된다. 7월 한 달만 재수집했더니
  **266건 / 507,970원**이 새로 들어왔다. 다른 달도 비슷할 것으로 본다.

설계 메모
  · 30일씩 끊는다. 크롤러가 국가 간 2초를 쉬어 하루 30개국에 약 54초 걸리므로
    한 청크가 대략 27분, 전체 582일이면 8~9시간이다.
  · **청크마다 적재하지 않는다**(PHOTOISM_SKIP_INGEST=1). 적재는 406MB parquet 을
    통째로 다시 만들어 1회 14분이라, 20청크면 그것만 4시간 넘게 붙는다.
    다 받은 뒤 한 번만 돌린다.
  · 진행 상태를 파일에 남겨 **중단해도 이어서** 간다. 청크 하나가 실패하면
    거기서 멈추지 않고 기록만 남기고 다음으로 간다(막판에 실패분만 다시 돌리면 된다).
  · 낮에 돌려도 되게 만들었지만, 서버 부담을 생각하면 밤이 낫다.
"""
import json
import os
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE_DIR = Path(__file__).parent
STATE = BASE_DIR / "logs" / "resync_all_state.json"
CHUNK_DAYS = 30
START = date(2025, 1, 1)


def _chunks():
    end = date.today() - timedelta(days=1)
    out, cur = [], START
    while cur <= end:
        last = min(cur + timedelta(days=CHUNK_DAYS - 1), end)
        out.append((cur.isoformat(), last.isoformat()))
        cur = last + timedelta(days=1)
    return out


def _load():
    if STATE.exists():
        try:
            return json.loads(STATE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"done": [], "failed": [], "started": None}


def _save(s):
    STATE.parent.mkdir(exist_ok=True)
    STATE.write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    chunks = _chunks()
    st = _load()
    if "--reset" in sys.argv:
        st = {"done": [], "failed": [], "started": None}
        _save(st)
        print("진행 상태를 초기화했어요.")
        return 0

    done = set(map(tuple, st["done"]))
    todo = [c for c in chunks if tuple(c) not in done]
    if "--status" in sys.argv:
        print(f"전체 {len(chunks)}청크 · 완료 {len(done)} · 남음 {len(todo)}")
        if st.get("failed"):
            print(f"실패 {len(st['failed'])}: " + ", ".join(f"{a}~{b}" for a, b in st["failed"]))
        for a, b in todo[:5]:
            print(f"  다음: {a} ~ {b}")
        return 0

    if not todo:
        print("이미 전부 받았어요. 적재만 하면 됩니다:")
        print("  python photoism_ingest.py 2025-01-01")
        return 0

    if not st.get("started"):
        st["started"] = datetime.now().isoformat(timespec="seconds")
    print(f"전량 재수집: {len(chunks)}청크 중 {len(todo)}개 남음 "
          f"(청크당 {CHUNK_DAYS}일, 약 27분 예상)")
    print("적재는 건너뜁니다. 다 받은 뒤 한 번만 돌리세요.\n")

    env = dict(os.environ, PHOTOISM_SKIP_INGEST="1")
    for i, (a, b) in enumerate(todo, 1):
        t0 = datetime.now()
        print(f"[{i}/{len(todo)}] {a} ~ {b} 시작 {t0:%H:%M:%S}", flush=True)
        r = subprocess.run([sys.executable, str(BASE_DIR / "photoism_crawler.py"), a, b],
                           cwd=str(BASE_DIR), env=env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        mins = (datetime.now() - t0).total_seconds() / 60
        if r.returncode == 0:
            st["done"].append([a, b])
            print(f"    완료 ({mins:.0f}분)", flush=True)
        else:
            # ★실패해도 멈추지 않는다. 8시간짜리가 중간 한 번에 죽으면 손해가 크다.
            st["failed"].append([a, b])
            print(f"    실패 exit={r.returncode} ({mins:.0f}분) — 계속 진행", flush=True)
        _save(st)

    print("\n" + "=" * 58)
    print(f"수집 종료 · 완료 {len(st['done'])}청크 · 실패 {len(st['failed'])}청크")
    if st["failed"]:
        print("실패 구간(직접 다시):")
        for a, b in st["failed"]:
            print(f"  python photoism_crawler.py {a} {b}")
    print("\n마지막으로 적재 — 대시보드(8503)를 내리고 돌려 주세요:")
    print("  python photoism_ingest.py 2025-01-01")
    return 0


if __name__ == "__main__":
    sys.exit(main())
