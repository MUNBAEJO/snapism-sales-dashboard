# -*- coding: utf-8 -*-
"""개발 서버 스위치 — `SNAPISM_ENV=dev` 일 때만 켜진다.

왜 필요한가 — 대시보드는 **읽기만 하지 않는다.** 정산 매핑·요율·발행 이력·계정
권한·접속 로그를 실제로 쓴다. 개발 서버를 그냥 포트만 바꿔 띄우면 테스트가
**실서비스 데이터를 오염시킨다.** 특히 정산서를 한 번 만들어 보면 진짜 발행
버전이 올라가고, 메일은 진짜 담당자에게 간다.

무엇을 가르나
  - **작은 JSON 저장소**(정산 매핑·요율·MG·발행이력·환율·발송로그·별칭·프레임매핑)
    → `data_dev/`
  - 계정 파일 → `allowed-users.dev.json`   · 접속 로그 → `logs/dev_access.log`
  - 발행 PDF → `reports/issued_dev/`
  - **메일 발송은 아예 막는다** — 실수할 여지를 남기지 않는다.

무엇을 안 가르나
  - **큰 parquet 은 실서버 것을 그대로 읽는다.** 대시보드는 이걸 읽기만 하고,
    복사하면 4GB가 더 든다. 쓰는 건 수집기뿐이고 수집기는 dev 로 안 돈다.

로그인
  구글 OIDC 리디렉션 주소가 실서버 도메인 하나에 묶여 있어 dev 로는 로그인이
  안 된다. 그래서 dev 는 `st.user` 를 가짜로 끼워 넣는다(권한 로직 자체는 그대로
  돈다 — 신분만 고정이다). **그래서 dev 는 127.0.0.1 에만 띄우고 절대 외부에
  노출하지 않는다.** 노출되면 인증 없이 매출이 열린다.
"""
import os
import shutil
from pathlib import Path

BASE_DIR = Path(__file__).parent

IS_DEV = os.environ.get("SNAPISM_ENV", "").strip().lower() == "dev"


def assert_local_only() -> str:
    """dev 인데 외부 주소에 바인딩돼 있으면 그 사실을 돌려준다(빈 문자열이면 정상).

    ★dev 는 구글 로그인을 **건너뛴다**(auth._DevUser). 그래서 외부에 열리면
      인증 없이 매출이 보인다. 지금은 run_dashboard_dev.py 가 127.0.0.1 로만
      띄워서 막고 있지만, 켜지는 조건이 **환경변수 하나**뿐이다 —
      실서버 세션에 SNAPISM_ENV=dev 가 실수로 남으면 그대로 사고다.
      그래서 주소를 직접 확인한다(2026-08-19 외부 점검 지적).
    """
    if not IS_DEV:
        return ""
    try:
        from streamlit import config as _cfg
        addr = str(_cfg.get_option("server.address") or "").strip()
    except Exception:
        return ""     # 주소를 못 읽으면 판단하지 않는다 — 오탐으로 실서버를 막지 않게
    if addr in ("127.0.0.1", "localhost", "::1"):
        return ""
    return addr or "(전체 주소 0.0.0.0)"
# dev 로 접속한 사람의 신분. allowed-users.dev.json 의 역할을 그대로 탄다.
DEV_EMAIL = (os.environ.get("SNAPISM_DEV_EMAIL") or "dev@local").strip().lower()


def data_dir() -> Path:
    """작은 JSON 저장소가 놓일 곳. 큰 parquet 경로는 여기서 안 바꾼다."""
    return BASE_DIR / ("data_dev" if IS_DEV else "data")


def seed() -> list:
    """dev 폴더에 없는 JSON 만 실서버 것에서 한 번 복사해 온다.

    빈 채로 시작하면 확정 매핑·요율이 없어서 정산서 화면을 테스트할 수가 없다.
    **이미 있는 파일은 절대 덮지 않는다** — dev 에서 만든 값이 날아가면 안 된다.
    """
    if not IS_DEV:
        return []
    src, dst = BASE_DIR / "data", data_dir()
    dst.mkdir(exist_ok=True)
    made = []
    for p in sorted(src.glob("*.json")):
        q = dst / p.name
        if not q.exists():
            shutil.copy2(p, q)
            made.append(p.name)
    # 계정 파일도 같은 규칙 — 없으면 한 번만 복사한다
    a_src, a_dst = BASE_DIR / "allowed-users.json", BASE_DIR / "allowed-users.dev.json"
    if a_src.exists() and not a_dst.exists():
        shutil.copy2(a_src, a_dst)
        made.append(a_dst.name)
    return made


def banner() -> str:
    return (f"🧪 개발 서버 · 신분 {DEV_EMAIL} · 저장은 data_dev/ 로만 가고 "
            "메일은 안 나가요") if IS_DEV else ""
