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
      12개월·월(막대 12) · 12개월·주(선 48) · 최근 90일·일(선 90 + 7일 이동평균)
    12개월을 일 단위로 보는 건 **안 만든다.** 읽을 수 없는데 자리만 차지한다.
  · 계열은 **하나**(합계). 구성은 바로 아래 비중 카드가 이미 답하고 있다.
    구성 값은 툴팁에 숫자로 딸려 나오므로 정보가 사라지지 않는다.
  · 그래프를 안 봐도 되는 요약 줄을 항상 띄운다(터치 대비). 4주 이동합이라
    '이번 달이 아직 안 끝나서 낮아 보이는' 문제도 구조적으로 안 생긴다.
  · Plotly 를 쓴다 — 0 기준 y축·눈금·월 라벨·최근접 스냅 툴팁·터치가 전부 딸려온다.
    다른 4개 화면이 이미 Plotly 라 새로 까는 것도 없다.
"""
from __future__ import annotations

import math

import pandas as pd
import plotly.graph_objects as go

INK, MUTED, GRID = "#1b2330", "#9aa3b2", "#e8eaef"
PRESETS = ["12개월 · 월", "12개월 · 주", "최근 90일 · 일"]
# ★두 대시보드의 CSS 툴팁(.vtip)과 같은 톤으로 맞춘다.
#   #1b2330 배경 · 흰 글자 · 11.5px · weight 600 · Pretendard.
#   (다른 화면의 Plotly 는 '1,234,567원' 을 쓰지만, 이 카드는 스내피즘·포토이즘
#    페이지에 얹히므로 그 페이지 방식인 '₩1,234,567' 을 따른다.)
FONT = "Pretendard, 'Malgun Gothic', -apple-system, sans-serif"


def money(v) -> str:
    """축 눈금용 짧은 표기(fmt_short 와 같은 규칙)."""
    v = float(v or 0)
    if abs(v) >= 1e8:
        return f"₩{v / 1e8:,.1f}억"
    if abs(v) >= 1e4:
        return f"₩{v / 1e4:,.0f}만"
    return f"₩{v:,.0f}"


def won(v) -> str:
    """툴팁용 원 단위 표기(fmt_krw 와 같은 규칙)."""
    return f"₩{int(v or 0):,}"


def _nice_top(v: float) -> float:
    """눈금 상단을 딱 떨어지는 수로. max 를 그대로 쓰면 이상치 하루가 축을 지배한다."""
    if not v or v <= 0:
        return 1.0
    e = 10 ** math.floor(math.log10(v))
    for m in (1, 1.5, 2, 2.5, 3, 4, 5, 7.5, 10):
        if v <= m * e:
            return m * e
    return 10 * e


def _month_ticks(fig, lo, hi):
    """월 경계 라벨. ★D3(plotly tickformat)는 '%-m' 을 모르고 윈도 strftime 도 안 받는다
    → 눈금값을 직접 만든다. 1월엔 연도를 붙여 해가 바뀌는 지점을 보이게 한다."""
    ticks = pd.date_range(pd.Timestamp(lo).replace(day=1), hi, freq="MS")
    fig.update_xaxes(tickvals=list(ticks),
                     ticktext=[(f"{t.year % 100}년 {t.month}월" if t.month == 1
                                else f"{t.month}월") for t in ticks])


def _shell(fig, top: float, height: int = 300, hoverfmt: str = "%Y-%m-%d"):
    """hoverfmt — 'x unified' 툴팁 머리의 날짜 표기. 안 주면 'Sep 1, 2025' 처럼
    영문으로 나와 화면 톤과 안 맞는다. D3 포맷이라 %-m 같은 건 못 쓴다."""
    ticks = [0, top / 2, top]
    fig.update_layout(
        height=height, margin=dict(l=66, r=14, t=6, b=32),
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
        font=dict(family=FONT, size=12, color=INK),
        showlegend=False, hovermode="x unified", dragmode=False,
        # bordercolor 를 배경과 같게 — 안 주면 Plotly 가 계열색 테두리를 둘러
        # 집 툴팁(.vtip, 테두리 없음)과 달라 보인다.
        hoverlabel=dict(bgcolor=INK, bordercolor=INK, align="left",
                        font=dict(family=FONT, size=11.5, color="#fff")),
        xaxis=dict(showgrid=False, showline=True, linecolor=GRID, ticks="outside",
                   tickcolor=GRID, tickfont=dict(color=MUTED, size=11),
                   hoverformat=hoverfmt),
        # ★0 기준 고정. 0을 안 깔면 5% 오르내림이 두 배처럼 보인다.
        yaxis=dict(range=[0, top * 1.04], tickvals=ticks,
                   ticktext=[money(t) for t in ticks], gridcolor=GRID,
                   zeroline=False, tickfont=dict(color=MUTED, size=11)),
    )
    return fig


def _tip(row_total, parts: dict) -> str:
    """툴팁 본문. 계열을 하나로 줄이는 대신 구성은 여기에 숫자로 남긴다.
    금액은 **원 단위**로 적는다 — 같은 페이지의 다른 툴팁이 전부 그렇다."""
    s = f"<b>{won(row_total)}</b>"
    for k, v in parts.items():
        if v:
            s += f'<br><span style="opacity:.72">{k}</span> {won(v)}'
    return s


def render(st, daily: pd.DataFrame, *, key: str, color: str,
           parts_cols: list[str] | None = None, title: str = "📈 매출 추이",
           kr_col: str | None = None):
    """daily: 날짜 인덱스(일 단위, 빈 날 0) · 'total' 컬럼 필수 · parts_cols 는 툴팁용.

    kr_col 을 주면 **한국 비중 배지**가 붙는다 — 한국이 88%라 '전체'라고 써 있어도
    사실상 한국 그래프이기 때문이다.

    ★'한국 제외' 토글은 뺐다(2026-08-11, 사용자 요청). 상단 국가 필터에서 한국을
      빼면 같은 걸 볼 수 있어 중복이었고, 토글을 켜면 구성(툴팁) 값이 한국을 못
      덜어내 아예 사라지는 부작용도 있었다. 배지는 남긴다 — '전체'가 사실상
      한국이라는 사실은 계속 알려줘야 하니까.
    """
    parts_cols = [c for c in (parts_cols or []) if c in daily.columns]
    if daily.empty or float(daily["total"].sum()) == 0:
        st.info("선택한 조건에 맞는 데이터가 없어요. 필터를 바꿔 보세요.")
        return

    kr_share = None
    if kr_col and kr_col in daily.columns:
        tot = float(daily["total"].sum())
        kr_share = (float(daily[kr_col].sum()) / tot) if tot else 0.0

    head, tog = st.columns([2.05, 1.25])
    with head:
        badge = (f' <span class="muted">최근 1년'
                 + (f' · 한국 {kr_share * 100:.0f}%' if kr_share is not None else "")
                 + "</span>")
        st.markdown(f'<div class="ct" style="margin-bottom:0">{title}{badge}</div>',
                    unsafe_allow_html=True)
    with tog:
        preset = st.segmented_control("보기", PRESETS, default=PRESETS[0],
                                      key=f"{key}_preset",
                                      label_visibility="collapsed") or PRESETS[0]

    d = daily
    end = d.index.max()

    # ── 항상 보이는 요약 — 그래프를 못 봐도 답이 되게 ──────────────────
    cur = float(d["total"].tail(28).sum())
    prv = float(d["total"].tail(56).head(28).sum())
    bits = [f"<b>최근 4주 {money(cur)}</b>"]
    if prv:
        _d = cur / prv - 1
        _c = "var(--green)" if _d >= 0 else "var(--red)"
        bits.append(f'직전 4주 대비 <b style="color:{_c}">{_d * 100:+.0f}%</b>')
    ly = d[(d.index >= end - pd.Timedelta(days=392))
           & (d.index <= end - pd.Timedelta(days=365))]["total"].sum()
    if ly:                       # 데이터가 1년 넘게 쌓이면 자동으로 붙는다
        _d = cur / float(ly) - 1
        _c = "var(--green)" if _d >= 0 else "var(--red)"
        bits.append(f'작년 같은 기간 대비 <b style="color:{_c}">{_d * 100:+.0f}%</b>')
    st.markdown('<div class="tsum">' + " · ".join(bits) + "</div>",
                unsafe_allow_html=True)

    rgba = "rgba(79,70,229,"          # --brand 계열. fill 은 투명도가 필요해 hex 로 못 쓴다.
    if preset == PRESETS[0]:                                   # 12개월 · 월
        g = d.resample("MS").sum()
        cd = [_tip(r["total"], {c: r[c] for c in parts_cols}) for _, r in g.iterrows()]
        # 진행 중인 마지막 달은 사선 — 부분 집계를 '급락'으로 오해하는 걸 막는다.
        pat = [""] * len(g)
        pat[-1] = "/"
        fig = go.Figure(go.Bar(
            x=g.index, y=g["total"], customdata=cd,
            marker=dict(color=color, pattern=dict(shape=pat, fgcolor="#fff",
                                                  size=4, solidity=.25)),
            hovertemplate="%{customdata}<extra></extra>"))
        fig.update_layout(bargap=.35)
        _month_ticks(fig, g.index[0], g.index[-1])
        top = _nice_top(float(g["total"].max()))
        hfmt = "%Y년 %m월"
        cap = f"마지막 달은 사선이에요 — {end.month}월 {end.day}일까지만 집계된 부분치예요."
    elif preset == PRESETS[1]:                                 # 12개월 · 주
        g = d.resample("W-MON", label="left", closed="left").sum()
        cnt = d["total"].resample("W-MON", label="left", closed="left").size()
        g = g[cnt == 7]                    # ★부분주 제거 — 양 끝이 꺾이는 가짜 U자의 원인
        if g.empty:
            st.info("주 단위로 볼 만큼 기간이 길지 않아요.")
            return
        cd = [_tip(r["total"], {c: r[c] for c in parts_cols}) for _, r in g.iterrows()]
        fig = go.Figure(go.Scatter(
            x=g.index, y=g["total"], mode="lines", customdata=cd,
            line=dict(width=2.4, color=color, shape="spline", smoothing=.4),
            fill="tozeroy", fillcolor=rgba + ".10)",
            hovertemplate="%{customdata}<extra></extra>"))
        _month_ticks(fig, g.index[0], g.index[-1])
        top = _nice_top(float(g["total"].max()))
        hfmt = "%Y-%m-%d 부터 한 주"
        cap = "주로 묶으면 요일 효과가 사라져 추세가 가장 깨끗해요. 양 끝의 잘린 주는 뺐어요."
    else:                                                      # 최근 90일 · 일
        g = d.tail(90)
        ma = d["total"].rolling(7, center=True, min_periods=4).mean().tail(90)
        cd = [_tip(r["total"], {c: r[c] for c in parts_cols}) for _, r in g.iterrows()]
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=g.index, y=g["total"], mode="lines", opacity=.28,
                                 line=dict(width=1, color=color), hoverinfo="skip"))
        fig.add_trace(go.Scatter(
            x=ma.index, y=ma, mode="lines", customdata=cd,
            line=dict(width=2.8, color=color),
            hovertemplate="%{customdata}<extra></extra>"))
        _month_ticks(fig, g.index[0], g.index[-1])
        top = _nice_top(float(max(g["total"].max(), ma.max())))
        hfmt = "%Y-%m-%d"
        cap = ("굵은 선이 7일 평균(추세), 옅은 선이 그날 실제값이에요. "
               "12개월을 일 단위로는 안 그려요 — 점이 341개라 읽을 수가 없어서요.")

    st.plotly_chart(_shell(fig, top, hoverfmt=hfmt), use_container_width=True,
                    config={"displayModeBar": False}, key=f"{key}_fig")
    st.caption(cap)
