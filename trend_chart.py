# -*- coding: utf-8 -*-
"""'매출 추이' 카드 — 두 대시보드가 같이 쓴다.

왜 새로 만들었나 (2026-08-07)
  추이 차트를 조회 기간과 무관하게 최근 1년으로 바꿨더니 "뭐가 뭔지 모르겠다"는
  말이 나왔다. 스타일 문제가 아니라 **읽을 수 없게 그린 것**이 원인이었다.

    · 잡음 > 신호 — 주말이 평일의 1.5배, 일별 변동계수 1.05. 12개월 추세 변화(±10~30%)
      보다 요일 흔들림이 크다. 원자료 341점을 그대로 그리면 추세를 눈으로 못 뽑는다.
    · 쌓아서 가림 — 실결제 위에 쿠폰을 올리니 제일 또렷한 위쪽 선이 '정가 총액'이 됐다.
      정작 봐야 할 값은 중간 경계선으로 밀렸다. 스택은 구성비를 보는 형태지 추세용이 아니다.
    · 축이 없음 — 0 기준선도 금액 눈금도 없었다. 크기를 가늠할 방법이 없다.
    · 점이 화면보다 많음 — 341점 / 1000px = 점당 2.9px. 마우스로 겨냥조차 안 된다.
    · 없는 모양 — 첫 주·마지막 주가 잘린 부분주라 항상 U자로 꺾여 보였다.
    · 터치 불가 — CSS :hover 툴팁이라 폰·태블릿에선 값을 못 본다.

설계
  · 토글을 '단위'가 아니라 **'단위 + 기간' 프리셋**으로. 뭘 골라도 점이 12~90개다.
  · 그래프를 안 봐도 되는 요약 줄을 띄웠다(터치 대비) — **2026-08-21 걷어냄**:
    합계는 상단 KPI 카드가 이미 말하고, 4주 비교는 고른 기간과 무관했다.
  · Plotly 를 쓴다 — 0 기준 y축·눈금·월 라벨·최근접 스냅 툴팁·터치가 전부 딸려온다.

2026-08-21 개편 (요청)
  · 프리셋의 '12개월 · 월' 을 **연도**로 바꿨다 — `2026년` `2025년` … 기본은 올해다.
    "어차피 올해 것이 12개월과 거의 같으니, 지난해를 통째로 보는 쪽이 낫다" 는 요청.
    ★연도를 고르면 **화면이 그 해만 잘라서 넘긴다**(`window()`). 그래서 프레임이
      늘 1년치 이하로 유지된다 — 2년을 통째로 들고 있으면 메모리가 감당이 안 된다.
  · 구성(구좌·카테고리)은 **`구성 고르기` 로 골라 보고**, 그래프는 한 색으로 둔다.
    ★잠깐 쌓아 봤다가 되돌렸다 — 아래 층이 위를 통째로 밀어올려 어느 층도 자기
      값으로 안 읽힌다(스택의 구조적 한계). 대신 **마우스를 올리면** 툴팁이
      구분별 금액·비중을 낸다. 막대 색은 브랜드색 하나다.
  · 금액은 **백만원 단위 · 보고서 정식 표기**다 — `(단위: 백만원)` 을 오른쪽 위에
    한 번 적고 숫자는 맨숫자로 둔다. 여기까지 오는 데 다섯 번 갈아 끼웠다(`amt` 주석).
  · 막대 **아래에 그 달 금액**을 적는다 — 그래프를 안 봐도 달마다 숫자가 읽힌다.
"""
from __future__ import annotations

import math
from datetime import date

import pandas as pd
import plotly.graph_objects as go

INK, MUTED, GRID = "#1b2330", "#9aa3b2", "#e8eaef"
PRESET_Y1 = "최근 1년 · 월"
PRESET_WEEK, PRESET_DAY = "12개월 · 주", "최근 90일 · 일"
# ★두 대시보드의 CSS 툴팁(.vtip)과 같은 톤으로 맞춘다.
#   #1b2330 배경 · 흰 글자 · 11.5px · weight 600 · Pretendard.
FONT = "Pretendard, 'Malgun Gothic', -apple-system, sans-serif"


# ★억/만 축약(money)·원 단위(won) 표기는 지웠다 — 카드 전체를 천원 단위로
#   통일하면서(2026-08-21) 부르는 곳이 하나도 안 남았다.
# 회계·보고서 정식 표기 — 표 오른쪽 위에 `(단위: 백만원)` 을 한 번 적고,
# 숫자에는 통화 기호도 자릿말도 붙이지 않는다. 콜론 뒤에만 한 칸 띈다.
UNIT = "(단위: 백만원)"


def amt(v) -> str:
    """이 카드의 모든 금액 표기 — **백만원 단위 · 정식 표기**(2026-08-21 요청).

    12,612,051,380원 → `12,612`. 단위는 `(단위: 백만원)` 으로 오른쪽 위에 한 번.

    ★★단위를 다섯 번 갈아 봤다. 남겨 둔다 — 또 물으면 이 표가 답이다.
        억 축약 `₩126.1억`        → 백만 원 아래가 날아가고 툴팁 숫자와 자가 안 맞았다
        천원 `₩12,612,051`        → **1,261만원으로 읽어 버린다**(100배 차이)
        천원 + 자릿말 `…051천`     → 안 틀리지만 자릿수를 세게 돼 "너무 어렵다"
        원 `₩12,612,051,380원`    → 안 틀리지만 15~17자라 막대 아래가 비좁다
        만원 `1,261,205`          → 자리는 줄었지만 **쉼표가 둘이라 여전히 세야 한다**
      결론은 **보고서 방식 + 백만원**이다. 쉼표가 하나(또는 없음)로 줄어야 숫자를
      세지 않고 통째로 읽는다. 회사 문서와 같은 모양이라 그대로 옮겨 붙일 수도 있다.
    ★반올림이다 — 정산서의 **절사**(내림)와 규칙이 다르니 대조에 쓰면 안 된다.
    ★백만원 미만은 **소수 한 자리**로 낸다(`0.5`). 그냥 반올림하면 매출이 있는
      항목이 `0` 으로 보인다 — 스내피즘 툴팁에 실제로 `스티커(커스텀) 0 1%` 가
      떴다. 0 원과 "백만원이 안 되는 금액" 은 다른 얘기다.
      그래도 작은 금액을 세는 화면에 이 함수를 가져다 쓰면 안 된다.
    """
    v = float(v or 0)
    if v and abs(v) < 1_000_000:
        return f"{v / 1_000_000:.1f}"
    return f"{int(v / 1_000_000 + (0.5 if v >= 0 else -0.5)):,}"


# ── 프리셋 · 창 ────────────────────────────────────────────────────────
def presets(first: date, last: date) -> list[str]:
    """고를 수 있는 보기 목록.

    ★지난 연도 버튼은 뺐다(2026-08-21 요청) — 올해 하나만 두고, 해를 걸친 흐름은
      `최근 1년 · 월` 이 본다. 버튼 넷이 한 줄에 들어가는 한계도 있다.
    """
    return [f"{last.year}년", PRESET_Y1, PRESET_WEEK, PRESET_DAY]


def is_year(preset: str) -> bool:
    return bool(preset) and preset.endswith("년") and preset[:-1].isdigit()


def window(preset: str, first: date, last: date) -> tuple[date, date]:
    """그 프리셋이 그릴 창(시작, 끝).

    ★화면은 **이 창만 잘라서** `render()` 에 넘긴다. 연도 프리셋을 넣으면서도
      한 번에 들고 있는 행이 1년치를 안 넘게 하려는 것이다(포토이즘은 1년 창이
      이미 433만 행 × 5열이라, 2년을 통째로 뜨면 ArrayMemoryError 가 난다).
    """
    if is_year(preset):
        y = int(preset[:-1])
        return max(first, date(y, 1, 1)), min(last, date(y, 12, 31))
    start = (pd.Timestamp(last).normalize()
             - pd.DateOffset(months=11)).replace(day=1).date()
    return max(first, start), last


def current(st, key: str, first: date, last: date) -> str:
    """이번 실행에서 쓸 프리셋.

    ★위젯을 그리기 **전에** 창을 잘라야 해서 세션 상태에서 미리 읽는다.
      토글을 누르면 재실행이 걸리고, 그때는 이미 새 값이 들어와 있다.
    """
    opts = presets(first, last)
    v = st.session_state.get(f"{key}_preset")
    return v if v in opts else opts[0]


def _nice_top(v: float) -> float:
    """눈금 상단을 딱 떨어지는 수로. max 를 그대로 쓰면 이상치 하루가 축을 지배한다."""
    if not v or v <= 0:
        return 1.0
    e = 10 ** math.floor(math.log10(v))
    for m in (1, 1.5, 2, 2.5, 3, 4, 5, 7.5, 10):
        if v <= m * e:
            return m * e
    return 10 * e


def _month_ticks(fig, lo, hi, amounts=None):
    """월 경계 라벨. ★D3(plotly tickformat)는 '%-m' 을 모르고 윈도 strftime 도 안 받는다
    → 눈금값을 직접 만든다. 1월엔 연도를 붙여 해가 바뀌는 지점을 보이게 한다.

    amounts={Timestamp: 금액} 을 주면 라벨 **아랫줄에 그 달 금액**을 붙인다(요청).
    """
    ticks = pd.date_range(pd.Timestamp(lo).replace(day=1), hi, freq="MS")
    # 백만원 단위는 '12,612' 로 6자다. 막대가 12개라 칸이 59px 여도 넉넉하다.
    n = max(1, len(ticks))
    fs = 10.5 if n <= 8 else 9.5
    txt = []
    for t in ticks:
        lab = f"{t.year % 100}년 {t.month}월" if t.month == 1 else f"{t.month}월"
        if amounts is not None:
            v = amounts.get(t)
            if v:
                lab += (f"<br><span style='color:{INK};font-weight:700;"
                        f"font-size:{fs}px'>{amt(v)}</span>")
        txt.append(lab)
    fig.update_xaxes(tickvals=list(ticks), ticktext=txt)


def _shell(fig, top: float, height: int = 300, hoverfmt: str = "%Y-%m-%d",
           bmargin: int = 32):
    """hoverfmt — 'x unified' 툴팁 머리의 날짜 표기. 안 주면 'Sep 1, 2025' 처럼
    영문으로 나와 화면 톤과 안 맞는다. D3 포맷이라 %-m 같은 건 못 쓴다."""
    ticks = [0, top / 2, top]
    fig.update_layout(
        height=height, margin=dict(l=62, r=14, t=6, b=bmargin),
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
        font=dict(family=FONT, size=12, color=INK),
        showlegend=False, hovermode="x unified", dragmode=False,
        barmode="stack", bargap=.35,
        # bordercolor 를 배경과 같게 — 안 주면 Plotly 가 계열색 테두리를 둘러
        # 집 툴팁(.vtip, 테두리 없음)과 달라 보인다.
        # ★색 점·비중까지 들어가면서 11.5px 는 빽빽했다 — 12.5px 로 키운다.
        hoverlabel=dict(bgcolor=INK, bordercolor=INK, align="left",
                        font=dict(family=FONT, size=12.5, color="#fff")),
        xaxis=dict(showgrid=False, showline=True, linecolor=GRID, ticks="outside",
                   tickcolor=GRID, tickfont=dict(color=MUTED, size=11),
                   hoverformat=hoverfmt),
        # ★0 기준 고정. 0을 안 깔면 5% 오르내림이 두 배처럼 보인다.
        yaxis=dict(range=[0, top * 1.04], tickvals=ticks,
                   ticktext=[amt(t) for t in ticks], gridcolor=GRID,
                   zeroline=False, tickfont=dict(color=MUTED, size=11)),
    )
    return fig


def _tip(row_total, parts: dict, colors: dict | None = None) -> str:
    """툴팁 본문. 계열을 하나로 줄이는 대신 구성은 여기에 숫자로 남긴다.
    ★금액은 **백만원 단위**다 — 카드 전체(축·막대 라벨)와 같은 자다. 툴팁은
      그래프에서 떨어져 뜨므로 첫 줄에만 `(백만원)` 을 적어 자를 밝힌다.

    ★Plotly 툴팁은 **SVG 텍스트**다 — 표·flex 로 자리를 맞출 수가 없다(색·크기·
      굵기만 먹는다). 그래서 정렬 대신 **읽히는 순서**로 푼다(2026-08-21):
        · 막대와 **같은 색 점**을 앞에 찍어 어느 층인지 눈으로 바로 잇는다
        · **금액 큰 순**으로 세운다 — 입력 순서(구분 목록 순)는 뜻이 없다
        · 이름은 흐리게, 금액은 굵게, **비중(%)** 은 더 흐리게 — 세 단계로 읽힌다
    """
    s = (f'<span style="font-size:14.5px"><b>{amt(row_total)}</b></span>'
         f'<span style="color:#8d97a8;font-size:11px"> 합계 (백만원)</span>')
    items = sorted(((k, v) for k, v in parts.items() if v),
                   key=lambda kv: -abs(kv[1]))
    for k, v in items:
        dot = (f'<span style="color:{colors[k]};font-size:13px">●</span> '
               if colors and colors.get(k) else "")
        pct = (f' <span style="color:#8d97a8">{v / row_total * 100:.0f}%</span>'
               if row_total else "")
        s += (f'<br>{dot}<span style="color:#c3cad6">{k}</span>'
              f'  <b>{amt(v)}</b>{pct}')
    return s


def _carry(fig, x, y, cd) -> None:
    """툴팁 전담 계열 — **보이지 않는 선** 하나가 툴팁을 문다.

    ★보이는 계열에 물리면 Plotly 가 'x unified' 툴팁 왼쪽에 **그 계열의 색 칩**을
      세로 가운데에 그린다. 여러 줄짜리 툴팁에선 그게 엉뚱한 줄 옆에 붙어
      "저 줄만 색이 있다" 로 읽혔다(2026-08-21). 투명한 선이 물면 칩도 투명하다.
      Scatter 는 barmode='stack' 에 안 쌓이므로 y 에 총계를 그대로 준다.
    """
    fig.add_trace(go.Scatter(
        x=x, y=y, mode="lines", customdata=cd, showlegend=False,
        line=dict(width=0, color="rgba(0,0,0,0)"),
        hovertemplate="%{customdata}<extra></extra>"))


def render(st, daily: pd.DataFrame, *, key: str, color: str,
           parts_cols: list[str] | None = None, title: str = "📈 매출 추이",
           preset: str | None = None,
           options: list[str] | None = None, data_last=None,
           stack_cols: list[str] | None = None,
           stack_colors: list[str] | None = None):
    """daily: 날짜 인덱스(일 단위, 빈 날 0) · 'total' 컬럼 필수 · parts_cols 는 툴팁용.

    preset/options 를 주면 그 목록으로 토글을 그린다(화면이 `current()`·`window()`
    로 이미 창을 잘라 넘겼다는 뜻). 안 주면 예전처럼 최근 1년 3종이다.

    stack_cols 를 주면 **월 막대만** 그 구성으로 색을 나눠 쌓는다.

    data_last — 데이터의 마지막 날. ★이게 없으면 '최근 4주' 를 못 가린다:
      넘어오는 프레임은 이미 잘린 창이라 2025년을 골라도 그 창의 끝이 늘
      `index.max()` 라, 2025년 12월을 '최근 4주' 라고 부르게 된다.

    ★'한국 제외' 토글은 뺐고(2026-08-11), **한국 비중 배지도 뺐다**(2026-08-21
      요청). 상단 국가 필터에서 한국을 빼면 같은 걸 볼 수 있어 중복이었다.
      (`kr_col` 인자도 같이 없앴다 — 화면 쪽 '한국' 열 계산도 지웠다)
    """
    parts_cols = [c for c in (parts_cols or []) if c in daily.columns]
    stack_cols = [c for c in (stack_cols or []) if c in daily.columns]
    _scol = list(stack_colors or [])
    _scol += [color] * max(0, len(stack_cols) - len(_scol))
    _cmap = dict(zip(stack_cols, _scol))
    # ★구성 고르기 (2026-08-21 요청) — "색 구분이 모호하다".
    #   쌓아 놓으면 아래 계열이 위를 밀어올려 각 층을 눈으로는 못 읽는다(스택의 한계라
    #   색을 아무리 잘 골라도 안 풀린다). 그래서 **골라서 그것만 보게** 한다.
    #   위젯을 그리기 전에 값이 필요해서(제목 배지·요약 줄) 세션에서 미리 읽는다.
    _picks = ["전체"] + stack_cols
    pick = st.session_state.get(f"{key}_part")
    pick = pick if pick in _picks else "전체"
    opts = options or [PRESET_WEEK, PRESET_DAY]
    if daily.empty or float(daily["total"].sum()) == 0:
        # 토글은 그려 준다 — 안 그리면 빈 해를 골랐을 때 되돌아올 방법이 없다.
        _h, _t = st.columns([2.05, 1.25])
        with _h:
            st.markdown(f'<div class="ct" style="margin-bottom:0">{title}</div>',
                        unsafe_allow_html=True)
        with _t:
            st.segmented_control("보기", opts, default=preset or opts[0],
                                 key=f"{key}_preset", label_visibility="collapsed")
        st.info("선택한 조건에 맞는 데이터가 없어요. 필터나 기간을 바꿔 보세요.")
        return

    d = daily
    end, start = d.index.max(), d.index.min()
    cur = preset or opts[0]
    _yr = is_year(cur)
    _span = (f"{start:%Y-%m-%d} ~ {end:%Y-%m-%d}" if _yr else "최근 1년")

    head, tog = st.columns([2.05, 1.25])
    with head:
        badge = (f' <span class="muted">{_span}'
                 + (f' · <b>{pick}</b>만' if pick != "전체" else "")
                 + "</span>")
        st.markdown(f'<div class="ct" style="margin-bottom:0">{title}{badge}</div>',
                    unsafe_allow_html=True)
    with tog:
        st.segmented_control("보기", opts, default=cur, key=f"{key}_preset",
                             label_visibility="collapsed")
    if stack_cols:
        st.segmented_control("구성", _picks, default=pick, key=f"{key}_part",
                             label_visibility="collapsed")
    if pick != "전체":
        # 고른 구분 하나만 — 색도 그 구분 색으로 바꾼다(범례가 필요 없어진다).
        d = daily[[pick]].rename(columns={pick: "total"})
        color = _cmap.get(pick, color)
        stack_cols, parts_cols = [], []

    # ── 그래프 위 한 줄 — 단위만 ────────────────────────────────────────
    # ★요약 줄을 통째로 걷어냈다(2026-08-21 요청).
    #   · '최근 4주 · 직전 4주 대비 -9%' — 고른 기간과 무관하게 늘 최근 4주라
    #     기간을 바꿔도 안 변하는 숫자였다.
    #   · '합계' — **상단 필터로 기간을 조회하면 KPI 카드에 그대로 나온다**(사용자).
    #     같은 값을 한 화면에서 두 번 말할 이유가 없다.
    #   남는 건 단위 표시 하나다. 그래프 오른쪽 위에 붙인다.
    st.markdown('<div class="tsum" style="text-align:right">'
                '<span class="muted" style="font-size:11.5px;white-space:nowrap">'
                + UNIT + '</span></div>', unsafe_allow_html=True)

    rgba = "rgba(79,70,229,"          # --brand 계열. fill 은 투명도가 필요해 hex 로 못 쓴다.
    _bars = _yr or cur not in (PRESET_WEEK, PRESET_DAY)
    if _bars:                                                  # 연도·최근1년 → 월 막대
        g = d.resample("MS").sum()
        # 구성은 툴팁이 맡는다(아래 주석 참고).
        tip_cols = stack_cols or parts_cols
        _tipcol = _cmap or None
        cd = [_tip(r["total"], {c: r[c] for c in tip_cols}, _tipcol)
              for _, r in g.iterrows()]
        # 진행 중인 마지막 달은 사선 — 부분 집계를 '급락'으로 오해하는 걸 막는다.
        _partial = ((data_last is None or end.date() >= data_last)
                    and end.day < end.days_in_month)
        pat = [""] * len(g)
        if _partial:
            pat[-1] = "/"
        # ★★막대는 **한 색**이다 (2026-08-21, 쌓았다가 되돌렸다).
        #   구분별로 쌓아 봤더니 아래 층이 위를 통째로 밀어올려 어느 층도 자기
        #   값으로 안 읽혔다 — 스택의 구조적 한계라 색을 아무리 갈라도 안 풀린다.
        #   구분을 정확히 볼 길은 이미 **'구성 고르기'** 로 열어 뒀으니, 막대는
        #   흐름만 보여 주고 **구성은 마우스를 올렸을 때만** 숫자로 낸다.
        fig = go.Figure()
        fig.add_trace(go.Bar(
            x=g.index, y=g["total"], hoverinfo="skip",
            marker=dict(color=color, pattern=dict(shape=pat, fgcolor="#fff",
                                                  size=4, solidity=.25))))
        _carry(fig, g.index, g["total"], cd)
        # 막대 아래 그 달 금액(요청) — 그래프를 안 봐도 달마다 숫자가 읽힌다.
        _month_ticks(fig, g.index[0], g.index[-1],
                     amounts={i: float(v) for i, v in g["total"].items()})
        top = _nice_top(float(g["total"].max()))
        hfmt = "%Y년 %m월"
        cap = (f"마지막 달은 사선이에요 — {end.month}월 {end.day}일까지만 집계된 부분치예요."
               if _partial else "막대 아래 숫자가 그 달 합계예요.")
        if tip_cols:
            cap += " 막대에 마우스를 올리면 구분별 금액이 나와요."
    elif cur == PRESET_WEEK:                                   # 12개월 · 주
        g = d.resample("W-MON", label="left", closed="left").sum()
        cnt = d["total"].resample("W-MON", label="left", closed="left").size()
        g = g[cnt == 7]                    # ★부분주 제거 — 양 끝이 꺾이는 가짜 U자의 원인
        if g.empty:
            st.info("주 단위로 볼 만큼 기간이 길지 않아요.")
            return
        cd = [_tip(r["total"], {c: r[c] for c in parts_cols}, _cmap or None)
              for _, r in g.iterrows()]
        fig = go.Figure(go.Scatter(
            x=g.index, y=g["total"], mode="lines", hoverinfo="skip",
            line=dict(width=2.4, color=color, shape="spline", smoothing=.4),
            fill="tozeroy", fillcolor=rgba + ".10)"))
        _carry(fig, g.index, g["total"], cd)
        _month_ticks(fig, g.index[0], g.index[-1])
        top = _nice_top(float(g["total"].max()))
        hfmt = "%Y-%m-%d 부터 한 주"
        cap = "주로 묶으면 요일 효과가 사라져 추세가 가장 깨끗해요. 양 끝의 잘린 주는 뺐어요."
    else:                                                      # 최근 90일 · 일
        g = d.tail(90)
        ma = d["total"].rolling(7, center=True, min_periods=4).mean().tail(90)
        cd = [_tip(r["total"], {c: r[c] for c in parts_cols}, _cmap or None)
              for _, r in g.iterrows()]
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=g.index, y=g["total"], mode="lines", opacity=.28,
                                 line=dict(width=1, color=color), hoverinfo="skip"))
        fig.add_trace(go.Scatter(x=ma.index, y=ma, mode="lines", hoverinfo="skip",
                                 line=dict(width=2.8, color=color)))
        _carry(fig, g.index, g["total"], cd)
        _month_ticks(fig, g.index[0], g.index[-1])
        top = _nice_top(float(max(g["total"].max(), ma.max())))
        hfmt = "%Y-%m-%d"
        cap = ("굵은 선이 7일 평균(추세), 옅은 선이 그날 실제값이에요. "
               "12개월을 일 단위로는 안 그려요 — 점이 341개라 읽을 수가 없어서요.")

    # 막대 아래 두 줄짜리 라벨(달 + 금액)이라 아래 여백을 더 준다.
    st.plotly_chart(_shell(fig, top, hoverfmt=hfmt,
                           bmargin=46 if _bars else 32),
                    use_container_width=True,
                    config={"displayModeBar": False}, key=f"{key}_fig")
    st.caption(cap)
