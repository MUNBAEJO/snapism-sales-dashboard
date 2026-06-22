# -*- coding: utf-8 -*-
"""Streamlit 대시보드 런처 (ASCII 경로로 호출하기 위한 래퍼).
한글 파일명(스내피즘.py)을 bat에 직접 넣으면 인코딩이 깨지므로,
파일명을 UTF-8 파이썬 소스 안의 문자열로만 둔다. `python -m streamlit run`과 동일하게 동작."""
import os
import sys

os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.argv = [
    "streamlit", "run", "스내피즘.py",
    "--server.port", "8503",
    "--browser.gatherUsageStats", "false",
    "--server.headless", "true",
]
from streamlit.web import cli as stcli

sys.exit(stcli.main())
