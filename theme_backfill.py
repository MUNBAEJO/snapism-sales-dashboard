# -*- coding: utf-8 -*-
"""테마별 매출(theme_daily.parquet) 백필 — 2025-01 부터 월 단위로.

배경: `/v1/revenue/frame` 은 원래도 전 타이틀을 보내주는데 sm_collect 가 SM 것만
남기고 96%를 버리고 있었다. 이제 sm_collect 가 같은 응답으로 theme_daily 를 함께
쓰므로, **과거분만 채우면** 타이틀 → 테마 → 프레임 축이 전 기간에서 열린다.

- **월 단위**로 끊어 돌린다. 한 달이 끝날 때마다 저장하므로 중간에 죽어도
  거기까지는 남는다(옛 SM 백필은 마지막에 한 번만 저장해 죽으면 전부 날아갔다).
- 상태는 `logs/theme_backfill_state.json`. 다시 실행하면 **안 끝난 달부터** 이어간다.
- ★기본은 `write_sm=False` — SM 촬영수 파일은 안 건드린다. 2025년을 훑으면
  그때 SM 타이틀이 sm_shoot_daily 에 새로 들어가 **담당자에게 나가는 리포트의
  과거가 말없이 늘어난다.** 그건 따로 판단할 일이라 여기서 섞지 않는다.

실행:
  python theme_backfill.py --status              # 진행 상황만
  python theme_backfill.py                       # 안 끝난 달부터 이어서
  python theme_backfill.py --months 2            # 이번 실행은 2개월만
  python theme_backfill.py --from 2025-01        # 시작 월 지정(기본 2025-01)
"""
import json
import sys
from datetime import date, timedelta
from pathlib import Path

import sm_collect as S

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE_DIR = Path(__file__).parent
STATE = BASE_DIR / "logs" / "theme_backfill_state.json"
DEFAULT_FROM = "2025-01"
DELAY = 8                      # 국가 사이 간격(초) — CMS 부담을 낮춘다


def _load():
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:
        return {"done": [], "failed": {}}


def _save(st):
    STATE.parent.mkdir(exist_ok=True)
    STATE.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")


def _months(since: str):
    """since(YYYY-MM) 부터 **지난달까지**. 이번 달은 매일 수집이 채우므로 뺀다."""
    y, m = int(since[:4]), int(since[5:7])
    today = date.today()
    out = []
    while (y, m) < (today.year, today.month):
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def _span(ym: str):
    y, m = int(ym[:4]), int(ym[5:7])
    a = date(y, m, 1)
    b = (date(y + 1, 1, 1) if m == 12 else date(y, m + 1, 1)) - timedelta(days=1)
    return a, b


def _rows():
    try:
        import pandas as pd
        return len(pd.read_parquet(S.THEME_PARQUET))
    except Exception:
        return 0


def status(since):
    st = _load()
    ms = _months(since)
    done = [m for m in ms if m in st["done"]]
    left = [m for m in ms if m not in st["done"]]
    print(f"대상 {len(ms)}개월 ({ms[0]} ~ {ms[-1]})")
    print(f"  완료 {len(done)}개월 · 남음 {len(left)}개월")
    if st["failed"]:
        print(f"  실패 이력: {st['failed']}")
    if left:
        print(f"  다음: {left[0]}")
    print(f"  현재 {S.THEME_PARQUET.name} {_rows():,}행")


def main():
    a = sys.argv[1:]
    since = DEFAULT_FROM
    if "--from" in a:
        since = a[a.index("--from") + 1]
    if "--status" in a:
        status(since)
        return
    limit = int(a[a.index("--months") + 1]) if "--months" in a else 999

    cfg = json.load(open(S.CONFIG_FILE, encoding="utf-8"))["photoism"]
    codes = list(cfg["countries"].keys())

    st = _load()
    todo = [m for m in _months(since) if m not in st["done"]][:limit]
    if not todo:
        print("남은 달이 없어요. --status 로 확인해 보세요.")
        return
    print(f"이번 실행: {len(todo)}개월 ({todo[0]} ~ {todo[-1]}) · {len(codes)}개국 · 간격 {DELAY}s")

    for ym in todo:
        s, e = _span(ym)
        S.log(f"########## 테마 백필 {ym} ({s} ~ {e}) ##########")
        try:
            # ★write_sm=False — SM 촬영수 파일은 안 건드린다(위 주석 참고).
            S.collect(s, e, codes, DELAY, write_sm=False)
            st["done"].append(ym)
            st["failed"].pop(ym, None)      # 재시도로 성공하면 실패 목록에서 뺀다
        except Exception as ex:             # noqa: BLE001
            st["failed"][ym] = str(ex)[:200]
            S.log(f"########## {ym} 실패: {str(ex)[:200]} ##########")
        _save(st)                            # 한 달 끝날 때마다 기록 — 죽어도 여기까진 남는다
        S.log(f"########## {ym} 끝 · 누적 {_rows():,}행 ##########")

    status(since)


if __name__ == "__main__":
    main()
