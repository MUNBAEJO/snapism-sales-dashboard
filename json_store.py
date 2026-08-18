"""data/*.json 설정 파일용 안전한 읽기·쓰기.

`auth.py` 의 파일락 + 원자적 저장 패턴을 파일 하나에 묶어 재사용할 수 있게 뺀 것이다.
`auth.py` 자체는 이미 잘 돌고 있으므로 건드리지 않는다.

왜 필요한가 — 지금 `data/frame_mapping.json` 등은 read → modify → write 를
아무 보호 없이 한다. 두 사람이 동시에 저장을 누르면 나중 쓰기가 앞의 것을 통째로
덮어쓴다(lost update). 정산 매핑·MG 는 실무자 여러 명이 같이 만지는 파일이라
그대로 두면 조용히 값이 사라진다.

사용:
    store = JsonStore("settlement_mapping.json", default={"mappings": {}})
    data = store.load()
    store.mutate(lambda d: d["mappings"].update({...}))
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Callable

BASE_DIR = Path(__file__).parent
# ★개발 서버(SNAPISM_ENV=dev)면 data_dev/ 로 간다 — 테스트가 실서비스 정산·계정
#   데이터를 덮어쓰지 않게. 큰 parquet 은 여기 안 걸린다(그건 실서버 것을 읽는다).
import dev_mode
DATA_DIR = dev_mode.data_dir()

_LOCK_TIMEOUT = 5.0     # 락 획득 대기 상한
_LOCK_STALE = 15.0      # 이보다 오래된 락은 죽은 것으로 보고 제거


class JsonStore:
    """JSON 파일 하나에 대한 락·원자적 저장 래퍼.

    락은 **파일마다 따로** 잡는다. 하나의 전역 락을 쓰면 서로 무관한 저장까지
    줄을 서게 된다.
    """

    def __init__(self, name: str, default: Any = None):
        self.path = DATA_DIR / name
        self.lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        self._default = default if default is not None else {}

    # ── 락 ────────────────────────────────────────────────────────────────
    def _acquire(self):
        """단순 파일락(O_CREAT|O_EXCL). 실패해도 None 을 반환하고 진행한다 —
        원자적 저장이 최소한의 안전망이라 저장 자체를 막을 이유는 없다."""
        deadline = time.time() + _LOCK_TIMEOUT
        while time.time() < deadline:
            try:
                fd = os.open(str(self.lock_path),
                             os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.close(fd)
                return self.lock_path
            except FileExistsError:
                try:
                    if time.time() - os.path.getmtime(self.lock_path) > _LOCK_STALE:
                        os.unlink(self.lock_path)
                        continue
                except OSError:
                    pass
                time.sleep(0.05)
            except OSError:
                return None
        return None

    @staticmethod
    def _release(lock) -> None:
        if lock:
            try:
                os.unlink(lock)
            except OSError:
                pass

    # ── 읽기 ──────────────────────────────────────────────────────────────
    def load(self) -> Any:
        """파싱 실패 시 3회 재시도. 다른 세션이 쓰는 중이라 반쯤 읽힌 경우를 넘긴다."""
        if not self.path.exists():
            return json.loads(json.dumps(self._default))   # 깊은 복사
        for _ in range(3):
            try:
                return json.loads(self.path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                time.sleep(0.05)
        return json.loads(json.dumps(self._default))

    def version(self) -> float:
        """캐시 키로 쓸 mtime. 파일이 없으면 0.0.
        ★st.cache_data 는 밑줄로 시작하는 인자를 해시에서 제외하므로,
          이 값을 넘길 때 인자명에 밑줄을 붙이면 캐시 키가 죽는다."""
        try:
            return self.path.stat().st_mtime
        except OSError:
            return 0.0

    # ── 쓰기 ──────────────────────────────────────────────────────────────
    def _save(self, data: Any) -> None:
        # 임시파일 → os.replace 로 원자적 교체. 다른 세션의 torn read 방지.
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        os.replace(tmp, self.path)

    def mutate(self, fn: Callable[[Any], Any]) -> Any:
        """락 하에서 load → fn(data) → 원자적 save.

        fn 이 값을 반환하면 그 값을 저장하고, None 을 반환하면 in-place 수정으로 본다.
        저장된 최종 데이터를 반환한다.
        """
        lock = self._acquire()
        try:
            data = self.load()
            ret = fn(data)
            if ret is not None:
                data = ret
            self._save(data)
            return data
        finally:
            self._release(lock)
