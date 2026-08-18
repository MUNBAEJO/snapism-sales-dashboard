# -*- coding: utf-8 -*-
"""개발 서버 실행기 — 포트 8504 · **127.0.0.1 에만** 바인딩.

  python run_dashboard_dev.py                 # dev@local 신분으로
  python run_dashboard_dev.py ansqo34@seobuk.kr   # 다른 신분으로(권한 확인용)

★외부 주소에 바인딩하지 않는다. dev 는 구글 로그인을 건너뛰므로 열리면 인증 없이
  매출이 보인다. ngrok 에도 붙이지 말 것.
★큰 parquet 은 실서버 data/ 를 그대로 읽고, 쓰기는 전부 data_dev/ 로 간다.
  메일은 dev_mode 가 아예 막는다(logs/dev_mail_blocked.log 에 기록만).
"""
import os
import sys

os.chdir(os.path.dirname(os.path.abspath(__file__)))
os.environ["SNAPISM_ENV"] = "dev"
if len(sys.argv) > 1 and "@" in sys.argv[1]:
    os.environ["SNAPISM_DEV_EMAIL"] = sys.argv[1].strip().lower()

import dev_mode                                    # noqa: E402

made = dev_mode.seed()
print(f"[dev] 신분 {dev_mode.DEV_EMAIL} · 저장 {dev_mode.data_dir().name}/")
if made:
    print(f"[dev] 실서버에서 처음 복사해 온 파일 {len(made)}개: {', '.join(made)}")
print("[dev] http://127.0.0.1:8504  (외부 노출 금지)")

sys.argv = [
    "streamlit", "run", "스내피즘.py",
    "--server.port", "8504",
    "--server.address", "127.0.0.1",
    "--browser.gatherUsageStats", "false",
    "--server.headless", "true",
]
from streamlit.web import cli as stcli            # noqa: E402

sys.exit(stcli.main())
