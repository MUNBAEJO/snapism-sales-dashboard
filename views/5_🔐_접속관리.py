"""소유자 전용 — 접속 로그 + 계정 승인 관리.
메뉴에는 소유자에게만 노출되며(라우터에서 조건부 추가), 페이지 자체에서도 권한을 재검사한다.
"""
import streamlit as st
import auth

auth.render_admin_console()
