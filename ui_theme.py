"""대시보드 공용 디자인 토큰 · 컴포넌트.

스내피즘·포토이즘 페이지에 쓰던 인디고 시안을 새 페이지에서도 쓰려고 뽑아냈다.
기존 두 페이지는 각자 style 블록을 그대로 두고 **건드리지 않는다** —
잘 돌고 있는 화면을 리팩터링하다 깨뜨릴 이유가 없다. 새 페이지만 여기를 쓴다.

사용:
    import ui_theme
    ui_theme.inject()
    ui_theme.sec(1, "티켓 매핑", "타이틀마다 정산할 티켓을 확정해요")
    with ui_theme.card("브랜드별 요약"):
        ...
"""
from __future__ import annotations

from contextlib import contextmanager

import streamlit as st

PRETENDARD = ("https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9"
              "/dist/web/variable/pretendardvariable-dynamic-subset.min.css")

CSS = """
:root{
  --bg:#f4f5f7; --surface:#fff; --surface-2:#f8fafc; --surface-3:#eef1f5;
  --border:#e7e9ee; --border-strong:#d7dae1;
  --text:#1b2330; --text-2:#5b6573; --text-3:#98a0af;
  --brand:#4f46e5; --brand-2:#6366f1; --brand-soft:#eef0fe;
  --red:#c0322b; --green:#15803d; --amber:#b45309; --sky:#38a3e8; --teal:#0f9d77;
}
/* Pretendard 강제(맑은고딕 폴백 방지) — 시안의 부드러운 느낌 */
html, body, [class*="css"], [data-testid="stAppViewContainer"],
button, input, select, textarea, label, p, span, div, h1, h2, h3, h4, li, a,
[data-baseweb], [data-testid="stMarkdownContainer"]{
  font-family:'Pretendard Variable','Pretendard',-apple-system,BlinkMacSystemFont,
              'Segoe UI','Malgun Gothic',sans-serif;
}
html, body{ letter-spacing:-0.02em; }
[data-testid="stMainBlockContainer"], .block-container{ background:transparent !important; }

/* 섹션 헤더 */
.sechd{ display:flex; align-items:center; gap:10px; margin:26px 0 2px; }
.secn{ font-size:12px; font-weight:800; color:#fff; background:var(--brand);
       width:22px; height:22px; border-radius:7px; display:inline-flex;
       align-items:center; justify-content:center; flex:0 0 auto; }
.sect{ font-size:18px; font-weight:800; letter-spacing:-0.02em; color:var(--text); }
.secq{ font-size:12.5px; color:var(--text-3); margin:2px 0 10px 32px; }

/* 카드 */
.uicard{ background:var(--surface); border:1px solid var(--border); border-radius:14px;
  padding:16px 18px; margin:6px 0 14px;
  box-shadow:0 1px 2px rgba(20,28,45,.04),0 1px 3px rgba(20,28,45,.06); }
.cardt{ font-size:14px; font-weight:800; color:var(--text); margin-bottom:10px; }

/* KPI */
.kpis{ display:grid; grid-template-columns:2fr 1fr 1fr; gap:12px; margin:12px 0 8px; }
.kpis.k2{ grid-template-columns:1fr 1fr; }
.kpis.k4{ grid-template-columns:1.8fr 1fr 1fr 1fr; }
.kpis.k5{ grid-template-columns:1.6fr 1fr 1fr 1fr 1fr; }
.kpis.k3{ grid-template-columns:1fr 1fr 1fr; }
.kpi{ background:var(--surface); border:1px solid var(--border); border-radius:12px;
  padding:15px 17px; box-shadow:0 1px 2px rgba(20,28,45,.04),0 1px 3px rgba(20,28,45,.06); }
.kpi.hero{ background:linear-gradient(180deg,#fbfbff,#fff); border-color:#dcdcfb; }
.kpi .l{ font-size:12.5px; color:var(--text-2); font-weight:600; }
.kpi .v{ font-size:24px; font-weight:800; letter-spacing:-0.02em; margin-top:6px;
  line-height:1.05; color:var(--text); }
.kpi.hero .v{ color:var(--brand); font-size:27px; }
.kpi .d{ font-size:12px; font-weight:700; margin-top:7px; color:var(--text-3); }
.kpi .d b{ color:var(--text-2); }

/* 표 */
.ntbl{ border:1px solid var(--border); border-radius:12px; overflow:hidden; margin:2px 0 4px; }
.ntr{ display:grid; align-items:center; gap:10px; padding:11px 16px;
  border-bottom:1px solid var(--border); font-size:13px; color:var(--text); }
.ntr:last-child{ border-bottom:none; }
.ntr.nth{ background:var(--surface-2); font-size:11px; font-weight:700;
  color:var(--text-3); letter-spacing:.02em; }
.ntr.sum{ background:var(--surface-2); font-weight:800; color:var(--brand); }
.ntr .r{ text-align:right; } .ntr .c{ text-align:center; }
.ntr .r, .ntr .num{ white-space:nowrap; font-variant-numeric:tabular-nums; }
.nname{ font-weight:700; } .dim{ color:var(--text-3); }
.dash{ color:#c8cdd6; text-align:right; }
.npct{ display:flex; align-items:center; gap:9px; }
.npct-bar{ flex:1; height:6px; background:var(--surface-3); border-radius:4px; overflow:hidden; }
.npct-bar i{ display:block; height:100%; background:var(--brand-2); border-radius:4px; }
.npct .p{ font-size:11.5px; font-weight:800; min-width:38px; text-align:right;
  font-variant-numeric:tabular-nums; }

/* 상태 배지 */
.st-chip{ display:inline-block; font-size:11px; font-weight:800; border-radius:6px;
  padding:2px 8px; margin-left:6px; }
.st-ok{ background:#eefbf3; color:var(--green); }
.st-warn{ background:#fff7ed; color:var(--amber); }
.st-info{ background:var(--brand-soft); color:var(--brand); }
.st-mute{ background:var(--surface-3); color:var(--text-3); }

/* 알림 박스 */
.nbox{ border-radius:12px; padding:14px 18px; margin:6px 0 14px; border:1px solid;
  font-size:13px; }
.nbox.ok{ background:#eefbf3; border-color:#b6e6c8; color:#166534; }
.nbox.warn{ background:#fff7ed; border-color:#fcd9a8; color:#92400e; }
.nbox .big{ font-size:22px; font-weight:800; letter-spacing:-.02em; }
.nbox .sub{ color:inherit; opacity:.75; font-size:12px; }

/* 위젯 다듬기 */
[data-testid="stMetricValue"]{ font-size:20px !important; font-weight:800 !important; }
[data-testid="stMetricLabel"]{ font-size:12px !important; color:var(--text-2) !important; }
[data-testid="stExpander"] details{ border:1px solid var(--border) !important;
  border-radius:12px !important; background:var(--surface) !important; }
[data-testid="stExpander"] summary{ font-size:13px !important; }
[data-testid="stPopover"] button{ border:1px solid var(--border-strong) !important;
  background:var(--surface-2) !important; border-radius:8px !important;
  font-weight:600 !important; color:var(--text-2) !important; font-size:12px !important; }
"""


def inject() -> None:
    """페이지 상단에서 한 번 호출."""
    st.markdown(f'<link rel="stylesheet" href="{PRETENDARD}">'
                f"<style>{CSS}</style>", unsafe_allow_html=True)


def sec(n, title: str, q: str = "") -> None:
    """번호 붙은 섹션 헤더. 스내피즘·포토이즘과 같은 모양."""
    st.markdown(f'<div class="sechd"><span class="secn">{n}</span>'
                f'<span class="sect">{title}</span></div>'
                + (f'<div class="secq">{q}</div>' if q else ""),
                unsafe_allow_html=True)


@contextmanager
def card(title: str | None = None):
    """흰 카드. Streamlit 컨테이너를 감싸 시안과 같은 여백·그림자를 준다."""
    box = st.container(border=True)
    with box:
        if title:
            st.markdown(f'<div class="cardt">{title}</div>', unsafe_allow_html=True)
        yield box


def kpi(label: str, value: str, desc: str = "", hero: bool = False) -> str:
    return (f'<div class="kpi{" hero" if hero else ""}"><div class="l">{label}</div>'
            f'<div class="v">{value}</div>'
            + (f'<div class="d">{desc}</div>' if desc else "") + "</div>")


def kpis(cards: list[str], cls: str = "") -> None:
    st.markdown(f'<div class="kpis {cls}">' + "".join(cards) + "</div>",
                unsafe_allow_html=True)


def nbox(kind: str, html: str) -> None:
    st.markdown(f'<div class="nbox {kind}">{html}</div>', unsafe_allow_html=True)


def bar(frac: float, mx: float) -> str:
    w = 0 if mx <= 0 else min(100, max(2, frac / mx * 100))
    return (f'<div class="npct"><div class="npct-bar"><i style="width:{w:.0f}%"></i>'
            f'</div><span class="p">{frac * 100:.1f}%</span></div>')
