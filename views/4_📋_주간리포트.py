"""
📋 주간 회의 자료 — 스내피즘 / 포토이즘 분리 분석
Gemini 2.5 Flash + Google Search Grounding
"""
import json
import sys
import streamlit as st
import pandas as pd
from pathlib import Path
from datetime import datetime

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from weekly_insight import (
    load_config, save_gemini_key,
    get_week_range,
    load_snapism, load_photoism,
    analyze_weekly,
    generate_gemini_report,
    save_insight, load_insight,
)

# ─────────────────────────────────────────────
# 페이지 설정
# ─────────────────────────────────────────────
# set_page_config 는 라우터(스내피즘.py)에서 처리
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from guide_content import render_guide

st.markdown("""
<style>
html, body, [class*="css"], [data-testid="stAppViewContainer"] {
    font-family: 'Pretendard', -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Malgun Gothic', 'Apple SD Gothic Neo', sans-serif;
}
[data-testid="stAppViewContainer"] .main .block-container { padding-top: 2.2rem; padding-bottom: 3rem; max-width: 1500px; }
h1 { font-weight: 800 !important; letter-spacing: -0.5px; color: #1a1a2e; }
.section-title { font-size: 1.12rem; font-weight: 700; color: #1a1a2e; margin: 6px 0 12px; padding-left: 12px; border-left: 4px solid #4361ee; line-height: 1.4; }
[data-testid="stMetric"], [data-testid="metric-container"] {
    background: linear-gradient(135deg, #ffffff 0%, #f5f8ff 100%);
    border: 1px solid #e7ecf7; border-radius: 16px; padding: 16px 20px;
    box-shadow: 0 2px 10px rgba(67,97,238,0.06);
}
[data-testid="stMetricLabel"] p { font-weight: 600; color: #6b7280; font-size: .82rem; }
[data-testid="stMetricValue"] { font-size: 1.7rem !important; font-weight: 800; color: #1a1a2e; }
hr { margin: 1.4rem 0 1.2rem; border: none; border-top: 1px solid #e9edf5; }
[data-testid="stDeployButton"] { display: none !important; }
[data-testid="stSidebar"] { background: #fbfcfe; border-right: 1px solid #eceff5; }
[data-testid="stDataFrame"] { border-radius: 12px; overflow: hidden; }
/* ── 주간리포트 전용 카드 (유지) ── */
.rise-card {
    background: linear-gradient(135deg, #e8f5e9, #f1f8e9);
    border-left: 4px solid #43a047;
    border-radius: 8px; padding: 10px 14px; margin-bottom: 6px;
}
.fall-card {
    background: linear-gradient(135deg, #fce4ec, #fdf3f5);
    border-left: 4px solid #e53935;
    border-radius: 8px; padding: 10px 14px; margin-bottom: 6px;
}
.brand-header-snap  { color: #1565c0; font-size: 1.15rem; font-weight: 700; margin-bottom: 4px; }
.brand-header-photo { color: #2e7d32; font-size: 1.15rem; font-weight: 700; margin-bottom: 4px; }
.report-box {
    background: #f8f9fa; border: 1px solid #dee2e6;
    border-radius: 10px; padding: 20px 24px; margin-top: 8px;
}
.gen-time { font-size: 0.78rem; color: #888; }
</style>
""", unsafe_allow_html=True)

# ─────────────────────────────────────────────
# 사이드바
# ─────────────────────────────────────────────
with st.sidebar:
    st.header("⚙️ 설정")

    cfg       = load_config()
    saved_key = cfg.get("gemini_api_key", "")

    key_input = st.text_input(
        "Gemini API Key", value=saved_key, type="password", placeholder="AIza...",
        help="Google AI Studio (aistudio.google.com) 에서 발급",
    )
    if st.button("💾 키 저장", use_container_width=True):
        save_gemini_key(key_input)
        st.success("저장 완료!")
        st.rerun()

    st.divider()
    st.subheader("📅 분석 기간")
    week_mode = st.radio(
        "기준 주",
        ["지난 주 (완성된 주)", "이번 주 to date"],
        index=0,
    )

    st.divider()
    st.subheader("🔧 필터")
    ip_only = st.checkbox(
        "🎭 IP/캐릭터만",
        value=True,
        help="스내피즘: 포토카드(커스텀)·스티커(커스텀) 제외\n포토이즘: 타이틀명 없는 건 제외",
    )

    st.divider()
    st.caption("💡 Gemini 2.5 Flash + Google Search Grounding\n검색+분석 약 30초~2분 소요")

# ─────────────────────────────────────────────
# 날짜 범위
# ─────────────────────────────────────────────
if week_mode == "지난 주 (완성된 주)":
    this_start, this_end = get_week_range(-1)
    prev_start, prev_end = get_week_range(-2)
    week_label = "지난 주"
else:
    this_start, this_end = get_week_range(0)
    prev_start, prev_end = get_week_range(-1)
    week_label = "이번 주"

# ─────────────────────────────────────────────
# 헤더
# ─────────────────────────────────────────────
st.title("📋 주간 회의 자료")
render_guide("weekly")
st.caption(
    f"📅 **{week_label}**: {this_start} ~ {this_end} &nbsp;|&nbsp; "
    f"**비교**: {prev_start} ~ {prev_end} &nbsp;|&nbsp; "
    f"**대상**: {'IP/캐릭터만' if ip_only else '전체'}"
)

# ─────────────────────────────────────────────
# 저장된 리포트
# ─────────────────────────────────────────────
insight      = load_insight()
current_key  = cfg.get("gemini_api_key", "") or key_input or ""

# ─────────────────────────────────────────────
# 생성 버튼
# ─────────────────────────────────────────────
col_btn, col_info = st.columns([2, 8])
with col_btn:
    gen_btn = st.button("🔄 리포트 생성", type="primary", use_container_width=True)
if insight:
    with col_info:
        gen_at = insight.get("generated_at", "")[:19].replace("T", " ")
        period = insight.get("period", "")
        st.markdown(
            f'<span class="gen-time">마지막 생성: {gen_at} &nbsp;|&nbsp; 기간: {period}</span>',
            unsafe_allow_html=True,
        )

# ─────────────────────────────────────────────
# 리포트 생성 실행
# ─────────────────────────────────────────────
if gen_btn:
    if not current_key:
        st.error("⚠️ 사이드바에서 Gemini API 키를 먼저 설정해 주세요.")
        st.stop()

    prog = st.progress(0, text="📂 데이터 로딩 중...")

    try:
        snap_df  = load_snapism(ip_only=ip_only)
        photo_df = load_photoism(ip_only=ip_only)
    except Exception as e:
        st.error(f"데이터 로드 오류: {e}")
        st.stop()

    prog.progress(20, text="📊 스내피즘 분석 중...")
    snap_a = analyze_weekly(snap_df, this_start, this_end, prev_start, prev_end)

    prog.progress(40, text="📊 포토이즘 분석 중...")
    photo_a = analyze_weekly(photo_df, this_start, this_end, prev_start, prev_end)

    prog.progress(55, text="🔍 Google 검색 + Gemini AI 분석 중... (30초~2분 소요)")

    try:
        gemini = generate_gemini_report(current_key, snap_a, photo_a)
    except Exception as e:
        gemini = {"text": "", "error": str(e)}

    prog.progress(95, text="💾 저장 중...")

    def _to_records(a: dict) -> dict:
        return {
            "summary": a["summary"],
            "top5":    a["top5"].reset_index().to_dict(orient="records"),
            "rising":  a["rising"].reset_index().to_dict(orient="records"),
            "falling": a["falling"].reset_index().to_dict(orient="records"),
        }

    payload = {
        "generated_at": datetime.now().isoformat(),
        "period":       f"{this_start} ~ {this_end}",
        "snap":         _to_records(snap_a),
        "photo":        _to_records(photo_a),
        "ai_report":    gemini,
    }
    save_insight(payload)
    prog.progress(100, text="✅ 완료!")

    if gemini.get("error") and not gemini.get("text"):
        st.warning(f"⚠️ Gemini 오류: {gemini['error']}")
    else:
        st.success(f"✅ 리포트 생성 완료! (모델: {gemini.get('model','gemini-2.5-flash')})")

    insight = payload
    st.rerun()

# ─────────────────────────────────────────────
# 리포트가 없는 경우
# ─────────────────────────────────────────────
if not insight:
    st.info("👆 '리포트 생성' 버튼을 눌러 주간 회의 자료를 만들어 보세요.")
    st.stop()

snap_data  = insight.get("snap",  {})
photo_data = insight.get("photo", {})
ai         = insight.get("ai_report", {})

snap_s  = snap_data.get("summary",  {})
photo_s = photo_data.get("summary", {})


# ─────────────────────────────────────────────
# 1. 통합 요약
# ─────────────────────────────────────────────
st.divider()
with st.container(border=True):
    st.subheader("📊 통합 요약")

    total_this = snap_s.get("이번주_총매출", 0) + photo_s.get("이번주_총매출", 0)
    total_prev = snap_s.get("지난주_총매출", 0) + photo_s.get("지난주_총매출", 0)
    total_wow  = (total_this - total_prev) / total_prev * 100 if total_prev > 0 else 0

    mc1, mc2, mc3, mc4 = st.columns(4)
    mc1.metric("💰 통합 총매출", f"₩{total_this:,.0f}")
    mc2.metric("📈 전주 대비",
               f"{'+' if total_wow >= 0 else ''}{total_wow:.1f}%",
               delta=f"{'+' if total_wow >= 0 else ''}{total_wow:.1f}%")
    mc3.metric("🟦 스내피즘", f"₩{snap_s.get('이번주_총매출',0):,.0f}",
               delta=f"{'+' if snap_s.get('wow_pct',0) >= 0 else ''}{snap_s.get('wow_pct',0):.1f}%")
    mc4.metric("🟩 포토이즘", f"₩{photo_s.get('이번주_총매출',0):,.0f}",
               delta=f"{'+' if photo_s.get('wow_pct',0) >= 0 else ''}{photo_s.get('wow_pct',0):.1f}%")


# ─────────────────────────────────────────────
# 2. 브랜드별 Top / 급등 / 급락 (2열)
# ─────────────────────────────────────────────
st.divider()
with st.container(border=True):
    col_snap, col_div, col_photo = st.columns([10, 1, 10])

    def render_brand(col, label, css_cls, brand_data):
        with col:
            st.markdown(f'<div class="{css_cls}">{label}</div>', unsafe_allow_html=True)

            s     = brand_data.get("summary", {})
            top5  = brand_data.get("top5",  [])
            rising= brand_data.get("rising", [])
            falling=brand_data.get("falling",[])

            wow = s.get("wow_pct", 0)
            st.caption(
                f"₩{s.get('이번주_총매출',0):,.0f} "
                f"({'+'  if wow >= 0 else ''}{wow:.1f}%) &nbsp;|&nbsp; "
                f"{s.get('이번주_건수',0):,}건 &nbsp;|&nbsp; "
                f"활성 IP {s.get('활성IP수',0)}개"
            )

            # Top 5
            st.markdown("**🏆 Top 5**")
            for i, row in enumerate(top5):
                ip  = row.get("IP", row.get("index", ""))
                amt = int(row.get("이번주", 0))
                pct = row.get("변동률", 0)
                clr = "#43a047" if pct > 0 else ("#e53935" if pct < 0 else "#888")
                arrow = "▲" if pct > 0 else ("▼" if pct < 0 else "➖")
                st.markdown(
                    f"**{i+1}. {ip}** &nbsp; ₩{amt:,.0f} "
                    f"<span style='color:{clr}'>{arrow} {pct:+.0f}%</span>",
                    unsafe_allow_html=True,
                )

            # 급등
            if rising:
                st.markdown("**🔥 급등**")
                for row in rising:
                    ip  = row.get("IP", row.get("index", ""))
                    pct = row.get("변동률", 0)
                    amt = int(row.get("이번주", 0))
                    st.markdown(
                        f'<div class="rise-card"><b>{ip}</b> &nbsp; ₩{amt:,.0f}'
                        f'<br><span style="color:#43a047;font-weight:700">▲ +{pct:.0f}%</span></div>',
                        unsafe_allow_html=True,
                    )

            # 급락
            if falling:
                st.markdown("**📉 하락 주의**")
                for row in falling:
                    ip  = row.get("IP", row.get("index", ""))
                    pct = row.get("변동률", 0)
                    amt = int(row.get("이번주", 0))
                    st.markdown(
                        f'<div class="fall-card"><b>{ip}</b> &nbsp; ₩{amt:,.0f}'
                        f'<br><span style="color:#e53935;font-weight:700">▼ {pct:.0f}%</span></div>',
                        unsafe_allow_html=True,
                    )

            if not rising and not falling:
                st.caption("급등/급락 IP 없음")

    render_brand(col_snap,  "🟦 스내피즘",  "brand-header-snap",  snap_data)
    with col_div:
        st.markdown("<div style='border-left:1px solid #ddd; height:600px; margin: auto;'></div>",
                    unsafe_allow_html=True)
    render_brand(col_photo, "🟩 포토이즘",  "brand-header-photo", photo_data)


# ─────────────────────────────────────────────
# 3. AI 분석 리포트
# ─────────────────────────────────────────────
st.divider()
with st.container(border=True):
    st.subheader("🤖 AI 분석 리포트")
    st.caption("Google Search Grounding 기반 — 아티스트/IP 최근 이슈 검색 후 브랜드별 분리 분석")

    ai_text  = ai.get("text", "")
    ai_error = ai.get("error", "")

    if ai_error and not ai_text:
        st.error(f"⚠️ {ai_error}")
    elif ai_text:
        model_used = ai.get("model", "gemini-2.5-flash")
        if model_used != "gemini-2.5-flash":
            st.info(f"ℹ️ {model_used} 로 생성됨 (fallback)")

        st.markdown(f'<div class="report-box">{ai_text}</div>', unsafe_allow_html=True)

        st.download_button(
            label="📄 텍스트 저장 (회의 준비용)",
            data=ai_text,
            file_name=f"주간리포트_{insight.get('period',str(this_start)).replace(' ','')}.txt",
            mime="text/plain",
        )
    else:
        st.warning("AI 리포트가 없습니다. 리포트 생성 버튼을 눌러주세요.")


# ─────────────────────────────────────────────
# 4. 원본 데이터 (접기)
# ─────────────────────────────────────────────
with st.expander("🔍 원본 분석 데이터", expanded=False):
    tab_s, tab_p = st.tabs(["🟦 스내피즘", "🟩 포토이즘"])

    def show_raw(tab, brand_data):
        with tab:
            rows = (brand_data.get("top5", [])
                    + brand_data.get("rising", [])
                    + brand_data.get("falling", []))
            seen, unique = set(), []
            for r in rows:
                ip = r.get("IP", r.get("index", ""))
                if ip not in seen:
                    seen.add(ip)
                    unique.append(r)
            if not unique:
                st.caption("데이터 없음")
                return
            ddf = pd.DataFrame(unique)
            show_cols = [c for c in ["IP","이번주","지난주","변동","변동률","이번주_건수"] if c in ddf.columns]
            ddf = ddf[show_cols].copy()
            for c in ["이번주","지난주","변동"]:
                if c in ddf.columns:
                    ddf[c] = ddf[c].apply(lambda x: f"₩{int(x):,}")
            if "변동률" in ddf.columns:
                ddf["변동률"] = ddf["변동률"].apply(lambda x: f"{x:+.1f}%")
            st.dataframe(ddf, use_container_width=True, hide_index=True)

    show_raw(tab_s, snap_data)
    show_raw(tab_p, photo_data)
