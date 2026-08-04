# -*- coding: utf-8 -*-
"""오픈 캘린더 화면 조각 — 월간 달력 격자(날짜를 누르면 그날 전체가 펼쳐진다).

톤앤매너는 ui_theme 토큰(인디고 시안)을 그대로 쓴다. 스내피즘·포토이즘과 같은
글꼴·색·라운드·그림자여야 한 대시보드로 보인다.

데이터 가공은 ip_calendar.py 가 한다. 여기는 그리기만 한다.
사이드바에는 아무것도 안 그린다 — 필터도 요약도 전부 페이지 본문에 있다.
"""
import calendar as _cal
import re
from datetime import date
from html import escape

import pandas as pd
import streamlit as st

import ip_calendar as ipc

_WD = "월화수목금토일"
GRID_KEY = "calgrid"          # st.container(key=) → .st-key-calgrid 로 CSS 를 좁힌다
_BTN_PREFIX = "cd_"


@st.cache_data(ttl=1800, show_spinner=False)
def load(refresh_token: int = 0) -> pd.DataFrame:
    """오픈 일정. 30분 캐시 — Jira 쪽이 12시간 캐시라 실제 호출은 하루 두 번쯤이다.

    ★인자 이름에 밑줄을 붙이지 말 것. st.cache_data 는 밑줄로 시작하는 인자를
      해시에서 빼버려서, 값을 바꿔도 캐시가 안 갈린다(이 저장소에서 여러 번 당했다).
    """
    return ipc.load_openings(brand="all")


# ── 스타일 ────────────────────────────────────────────────────────────────
# ui_theme 의 --brand/--border/--text 토큰을 그대로 쓴다(ui_theme.inject() 선행 필요).
_CSS = """
.calhd{display:flex;align-items:center;justify-content:space-between;gap:12px;
  margin:0 0 2px;padding-left:6px;}
.calhd .ttl{font-size:19px;font-weight:800;letter-spacing:-.03em;color:var(--text);
  font-variant-numeric:tabular-nums;}
.calhd .ttl em{font-style:normal;color:var(--text-3);font-size:13px;font-weight:700;
  margin-left:8px;}
.callg{display:flex;gap:13px;align-items:center;font-size:12px;font-weight:700;
  color:var(--text-2);}
.callg i{width:8px;height:8px;border-radius:3px;display:inline-block;margin-right:6px;
  vertical-align:middle;}
.calwd{font-size:11px;font-weight:800;color:var(--text-3);letter-spacing:.06em;
  text-align:center;padding:0 0 8px;}
.calwd.sat{color:#3b82f6;} .calwd.sun{color:#e11d48;}
/* 날짜 칸 */
.cd{border:1px solid var(--border);border-radius:11px;background:var(--surface);
  padding:8px 9px 9px;min-height:122px;transition:box-shadow .12s,border-color .12s;}
.cd.off{background:var(--surface-2);border-color:transparent;}
.cd.empty{background:#fcfcfe;}
.cd.today{border-color:#c7d2fe;box-shadow:0 0 0 1px #c7d2fe inset;}
.cd.sel{border-color:var(--brand);background:var(--brand-soft);
  box-shadow:0 2px 10px rgba(79,70,229,.16);}
.cd-h{display:flex;align-items:center;justify-content:space-between;margin-bottom:6px;
  min-height:20px;}
.cd-n{font-size:13px;font-weight:800;color:var(--text);font-variant-numeric:tabular-nums;
  line-height:20px;}
.cd-n.sat{color:#3b82f6;} .cd-n.sun{color:#e11d48;} .cd-n.dim{color:#c9cfd9;}
.cd-n.td{background:var(--brand);color:#fff;border-radius:7px;padding:0 7px;
  min-width:20px;text-align:center;display:inline-block;}
.cd-c{font-size:10.5px;font-weight:800;color:var(--brand);background:var(--brand-soft);
  border-radius:6px;padding:2px 6px;line-height:1.25;}
.cd.sel .cd-c{background:var(--brand);color:#fff;}
.cc{display:flex;align-items:center;gap:6px;font-size:11.5px;color:var(--text-2);
  line-height:1.62;font-weight:600;}
.cc b{width:3px;height:11px;border-radius:2px;flex:0 0 3px;display:inline-block;}
.cc span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
.cd-more{font-size:10.5px;font-weight:800;color:var(--brand);margin-top:3px;
  padding-left:9px;}
/* 칸 전체를 누를 수 있게 — 보이지 않는 버튼을 칸 위에 겹친다.
   CSS 가 안 먹어도 버튼은 남으니 기능이 죽지는 않는다(모양만 투박해진다). */
.st-key-calgrid [data-testid="stColumn"]{position:relative;}
.st-key-calgrid [data-testid="stColumn"]>div{gap:0 !important;}
.st-key-calgrid div[class*="st-key-cd_"]{position:absolute;inset:0;z-index:5;}
/* ★버튼과 래퍼 사이에 div 가 4겹(툴팁 래퍼 포함) 끼어 있다. 하나라도 높이가
   안 늘어나면 버튼이 26px 로 쪼그라들어 칸의 위쪽만 눌린다 — 전부 100% 로 편다. */
.st-key-calgrid div[class*="st-key-cd_"] div{height:100% !important;}
.st-key-calgrid div[class*="st-key-cd_"] button{width:100%;height:100%;opacity:0;
  min-height:0 !important;padding:0 !important;margin:0 !important;border:none;
  background:transparent;cursor:pointer;}
.st-key-calgrid div[class*="st-key-cd_"] button:disabled{cursor:default;}
"""


def inject() -> None:
    """달력 전용 CSS. ui_theme.inject() 뒤에 부른다.

    ★빈 줄을 없애고 넣는다 — 마크다운이 빈 줄에서 raw HTML 블록을 끊어버려서
      뒷부분 CSS 가 화면에 글자로 새어 나온다(ui_theme.inject 주석 참고).
    """
    css = " ".join(ln.strip() for ln in _CSS.splitlines() if ln.strip())
    css = re.sub(r"/\*.*?\*/", "", css)
    st.markdown(f"<style>{css}</style>", unsafe_allow_html=True)


def _legend(brands) -> str:
    return '<div class="callg">' + "".join(
        f'<span><i style="background:{ipc.BRAND_COLOR.get(b, "#94a3b8")}"></i>'
        f'{escape(b)}</span>' for b in brands) + "</div>"


def header(y: int, m: int, total: int, brands) -> None:
    """달력 상단 — '2026년 8월  237건' + 브랜드 범례."""
    st.markdown(
        f'<div class="calhd"><div class="ttl">{y}년 {m}월<em>오픈 {total:,}건</em></div>'
        f'{_legend(brands)}</div>', unsafe_allow_html=True)


def _cell_html(d: date, g, today: date, in_month: bool, selected: bool,
               per_cell: int) -> str:
    n = 0 if g is None else len(g)
    cls = ["cd"]
    if not in_month:
        cls.append("off")
    elif n == 0:
        cls.append("empty")
    if in_month and d == today:
        cls.append("today")
    if in_month and selected:
        cls.append("sel")

    ncls = "dim" if not in_month else ("td" if d == today else
                                       "sat" if d.weekday() == 5 else
                                       "sun" if d.weekday() == 6 else "")
    badge = f'<span class="cd-c">{n}</span>' if n else ""
    body = ""
    if n:
        for r in g.head(per_cell).itertuples():
            color = ipc.BRAND_COLOR.get(r.브랜드, "#94a3b8")
            name = escape(str(r.IP))
            body += (f'<div class="cc"><b style="background:{color}"></b>'
                     f'<span title="{name}">{name}</span></div>')
        if n > per_cell:
            body += f'<div class="cd-more">+{n - per_cell}건 더</div>'

    return (f'<div class="{" ".join(cls)}">'
            f'<div class="cd-h"><span class="cd-n {ncls}">{d.day}</span>{badge}</div>'
            f'{body}</div>')


def render_month(df: pd.DataFrame, y: int, m: int, today: date,
                 sel_key: str = "ipcal_day", per_cell: int = 3) -> date | None:
    """월간 격자를 그리고 **선택된 날짜**를 돌려준다. df 는 필터가 끝난 상태로 넘긴다.

    ★HTML 표 대신 컬럼 격자로 그린다. 표는 예쁘지만 칸을 누를 수가 없어서
      '+13건 더'가 무엇인지 확인할 방법이 없다. 칸마다 투명 버튼을 겹쳐
      누른 날짜를 session_state 로 받는다.
    """
    first, last = ipc.month_bounds(y, m)
    days = ipc.by_day(ipc.in_range(df, first, last))

    def _toggle(d: date):
        st.session_state[sel_key] = None if st.session_state.get(sel_key) == d else d

    with st.container(key=GRID_KEY):
        head = st.columns(7, gap="small")
        for i, c in enumerate(head):
            k = "sat" if i == 5 else "sun" if i == 6 else ""
            c.markdown(f'<div class="calwd {k}">{_WD[i]}</div>', unsafe_allow_html=True)

        sel = st.session_state.get(sel_key)
        for week in _cal.Calendar(firstweekday=0).monthdatescalendar(y, m):
            cols = st.columns(7, gap="small")
            for c, d in zip(cols, week):
                in_month = d.month == m
                g = days.get(d) if in_month else None
                n = len(g) if g is not None else 0
                with c:
                    st.markdown(
                        _cell_html(d, g, today, in_month, sel == d, per_cell),
                        unsafe_allow_html=True)
                    st.button(
                        f"{d.day}", key=f"{_BTN_PREFIX}{y}_{m}_{d.isoformat()}",
                        use_container_width=True,
                        disabled=(not in_month or n == 0),
                        on_click=_toggle, args=(d,),
                        help=(f"{d.month}월 {d.day}일 오픈 {n}건 — 눌러서 전부 보기"
                              if n else None),
                    )

    return st.session_state.get(sel_key)


def render_day_detail(df: pd.DataFrame, d: date, sel_key: str = "ipcal_day") -> None:
    """선택한 날짜의 전체 목록. 칸에서 접힌 '+N건 더'를 여기서 전부 편다."""
    g = ipc.in_range(df, d, d)
    with st.container(border=True):
        c = st.columns([5, 1], vertical_alignment="center")
        c[0].markdown(
            f'<div style="font-size:15px;font-weight:800;color:var(--text)">'
            f'📌 {d.month}월 {d.day}일 ({_WD[d.weekday()]}) '
            f'<span style="color:var(--brand)">오픈 {len(g)}건</span></div>',
            unsafe_allow_html=True)
        c[1].button("닫기", use_container_width=True, key="cal_close",
                    on_click=lambda: st.session_state.update({sel_key: None}))
        if g.empty:
            st.info("이 날짜에는 오픈 예정이 없어요.")
            return
        # 종료일은 뺀다 — 오픈 캘린더에서 볼 건 '언제 여는가'지 끝나는 날이 아니다.
        # 계약(상위)도 뺀다 — 서브태스크 쪽은 값이 없어 열 대부분이 비어 보인다(CSV 에는 남긴다).
        v = g[["브랜드", "IP", "상태", "티켓"]].reset_index(drop=True)
        st.dataframe(v, use_container_width=True, hide_index=True,
                     height=min(430, 44 + 35 * len(v)),
                     column_config={"IP": st.column_config.TextColumn(width="large")})
