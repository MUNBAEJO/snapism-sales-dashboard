# -*- coding: utf-8 -*-
"""IP 주간분석 — 매주 엑셀로 만들던 `IP 매출 분석_MMDD.xlsx` 를 자동으로.

계산은 전부 `weekly_report.py` 가 한다(엑셀과 어디가 왜 다른지도 거기 주석에).
이 파일은 **고르고 · 보여주고 · 내려받는 것**만 맡는다.

★주 단위로 매번 다시 도는 화면이다. 그래서 두 가지를 화면이 책임진다:
  ① 매주 새로 나오는 IP 의 팀(A/C)을 **물어보고 예외표에 남긴다** — 안 그러면
     규칙이 늙는다(`weekly_report.unknown_teams` 주석 참고).
  ② 전주·전년과 나란히 보여준다 — 엑셀 COVER 가 그렇게 생겼다.
"""
import html as _html_mod
import os
import re
import sys
from datetime import date, timedelta

import pandas as pd
import streamlit as st

# set_page_config 는 라우터(스내피즘.py)에서 처리
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import auth
import settlement_fx as fx
import ui_theme
import weekly_report as wr

_email = (st.user.email or "").strip().lower() if getattr(st, "user", None) else ""
if not auth.can_view_page(_email, "ipweekly"):
    st.error("🔒 이 페이지에 접근할 권한이 없어요. 필요하면 관리자에게 요청해 주세요.")
    st.stop()

CAN_EDIT = auth.can_edit(_email)
ui_theme.inject()

# ── 페이지 전용 스타일 (2026-09-03 재디자인) ─────────────────────────────
# ★ui_theme 공용 CSS 는 안 건드린다 — 다른 페이지가 같이 흔들린다. 여기 것만.
_WK_CSS = """
.wkhero{ background:linear-gradient(135deg,#f5f4ff,#eef0fe); border:1px solid #dcdefc;
  border-radius:14px; padding:18px 22px; display:flex; gap:24px; align-items:center;
  margin:10px 0 6px; }
.wkhero .wl{ flex:1.35; } .wkhero .wk{ font-size:12.5px; font-weight:700; color:var(--brand); }
.wkhero .wv{ font-size:32px; font-weight:800; color:var(--brand); letter-spacing:-.01em;
  margin-top:4px; font-variant-numeric:tabular-nums; }
.wkhero .wb{ display:flex; gap:8px; margin-top:10px; }
.wkbdg{ font-size:12px; font-weight:800; border-radius:99px; padding:4px 12px; }
.wkbdg small{ font-weight:600; opacity:.85; margin-left:4px; }
.wk-dnb{ background:#fdf0ef; color:var(--red); } .wk-upb{ background:#edf7f0; color:var(--green); }
.wkhero .wr{ flex:1; background:#fff; border:1px solid var(--border); border-radius:12px;
  padding:11px 15px; font-size:12px; color:var(--text-2); }
.wkhero .wr div{ display:flex; justify-content:space-between; padding:3px 0; }
.wkhero .wr b{ color:var(--text); font-variant-numeric:tabular-nums; }
.wk-up{ display:inline-block; font-size:11px; font-weight:800; border-radius:99px;
  padding:2px 9px; background:#edf7f0; color:var(--green); white-space:nowrap; }
.wk-dn{ display:inline-block; font-size:11px; font-weight:800; border-radius:99px;
  padding:2px 9px; background:#fdf0ef; color:var(--red); white-space:nowrap; }
.wk-new{ display:inline-block; font-size:11px; font-weight:800; border-radius:99px;
  padding:2px 9px; background:var(--brand-soft); color:var(--brand); }
.wkcnt{ font-size:12px; font-weight:800; color:#fff; background:var(--red);
  border-radius:99px; padding:1px 10px; vertical-align:2px; margin-left:2px; }
.wk-top1, .wk-top1 b{ color:var(--brand) !important; }
.wknm{ font-weight:700; } .wktag{ display:inline-block; font-size:10.5px; font-weight:700;
  border-radius:99px; padding:1px 8px; background:var(--surface-3); color:var(--text-2);
  margin-left:6px; }
.wkcode{ font-size:11px; color:var(--text-3); font-family:Consolas,monospace; }
.wkhr{ border-bottom:1px solid var(--border); margin:2px 0 10px; }
"""
st.markdown("<style>" + " ".join(ln.strip() for ln in _WK_CSS.splitlines() if ln.strip())
            + "</style>", unsafe_allow_html=True)

# ★제목은 왼쪽 · 주 선택은 오른쪽 한 줄 — 조회 조건이 첫 화면 주인공 자리를
#   차지하지 않게 한다(2026-09-03 재디자인). 설명문은 짧게 캡션으로만.
_h1, _h2 = st.columns([2.4, 2.0], vertical_alignment="bottom")
with _h1:
    st.markdown('<div style="font-size:26px;font-weight:800;letter-spacing:-.03em;'
                'color:var(--text);margin:2px 0 4px">📰 IP 주간분석</div>'
                '<div style="font-size:13px;color:var(--text-3);margin-bottom:6px">'
                '팀(<b>A</b> 아티스트 · <b>C</b> 캐릭터) × 구좌 × 국가로 한 주를 봐요. '
                '팝업·렌탈 매장 매출도 그 IP 것으로 <b>포함</b>해요.</div>',
                unsafe_allow_html=True)


# ── 주 고르기 ─────────────────────────────────────────────────────────────
def _week(d: date) -> tuple[date, date]:
    """그 날짜가 든 주(월~일)."""
    mon = d - timedelta(days=d.weekday())
    return mon, mon + timedelta(days=6)


# 기본은 **지난 주**다 — 이번 주는 아직 안 끝나서 전주 대비가 헛나온다.
_last_mon, _last_sun = _week(date.today() - timedelta(days=7))
_this_mon = _last_mon + timedelta(days=7)      # ▶ 는 이번 주까지만 — 그 앞은 빈 화면이다


def _shift_week(days: int) -> None:
    """◀▶ 버튼 — 시작·끝을 한 주씩 같이 민다."""
    st.session_state["wk_s"] = st.session_state["wk_s"] + timedelta(days=days)
    st.session_state["wk_e"] = st.session_state["wk_e"] + timedelta(days=days)


with _h2:
    b1, c1, c2, b2 = st.columns([0.5, 1.4, 1.4, 0.5], vertical_alignment="bottom")
    S = c1.date_input("시작(월)", value=_last_mon, key="wk_s")
    E = c2.date_input("끝(일)", value=_last_sun, key="wk_e")
    b1.button("◀", key="wk_prev", help="전주", use_container_width=True,
              on_click=_shift_week, args=(-7,))
    # ★▶ 는 이번 주에서 멈춘다 — 계속 누르면 아직 오지 않은 주를 조회해
    #   "매출이 없어요" 빈 화면만 나온다(데이터가 없는 게 아니라 아직 안 판 것).
    b2.button("▶", key="wk_next", help="다음 주", use_container_width=True,
              on_click=_shift_week, args=(7,), disabled=(S >= _this_mon))
if S > E:
    st.error("시작일이 끝일보다 늦어요.")
    st.stop()
S, E = S.isoformat(), E.isoformat()
_pS = (date.fromisoformat(S) - timedelta(days=7)).isoformat()
_pE = (date.fromisoformat(E) - timedelta(days=7)).isoformat()
# ★전년은 **같은 요일**로 맞춘다(364일 = 52주). 날짜로 맞추면 요일이 어긋나
#   주말이 하루 더 든 주와 비교하게 된다 — 커버리지 조사에서 이미 겪은 함정이다.
#   ★기준 안내문은 히어로 카드 오른쪽에 함께 적는다 — 머리에 떠 있을 이유가 없다.
_yS = (date.fromisoformat(S) - timedelta(days=364)).isoformat()
_yE = (date.fromisoformat(E) - timedelta(days=364)).isoformat()

RATES, RDATE, RSRC = fx.resolve(E)


# ── 데이터 ────────────────────────────────────────────────────────────────
@st.cache_data(ttl=1800, max_entries=12, show_spinner=False)
def _detail(start, end, teamver, dataver):
    d = wr.photoism_detail(start, end)
    if d.empty:
        return d
    tm = wr.load_teams()
    d[["팀", "근거"]] = d.apply(
        lambda r: pd.Series(wr.team_of(r["타이틀"], r["구분"], tm)), axis=1)
    return d


@st.cache_data(ttl=1800, max_entries=12, show_spinner=False)
def _sn(start, end, dataver):
    return wr.snapism_rows(start, end)


_dv = os.path.getmtime(wr.PH_RAW) if wr.PH_RAW.exists() else 0.0
cur = _detail(S, E, wr.team_version(), _dv)
prv = _detail(_pS, _pE, wr.team_version(), _dv)
yoy = _detail(_yS, _yE, wr.team_version(), _dv)
if cur.empty:
    st.info("그 주에는 포토이즘 매출이 없어요.")
    st.stop()

# ★★환율이 없는 통화를 **1:1 로 떨어뜨리지 않는다.** `{"KRW":1}` 같은 폴백은
#   해외 매출을 원화로 둔갑시키는데, 값이 있어 아무도 못 알아챈다(2026-08 에
#   실제로 5곳에 있었다). 없으면 NaN 으로 두고 화면이 **어느 나라가 빠졌는지 알린다.**
# ★★국가→통화는 **원장이 말하는 대로** 쓴다. 손으로 적은 표를 쓰다가 10개국
#   8,800건(1.89%)을 놓쳤다 — 자세한 경위는 `weekly_report.cc_units` 주석에.
#   theme_daily 엔 `결제 단위` 가 없어 원장에서 그 기간의 표를 만들어 붙인다.
for _d, _s, _e in ((cur, S, E), (prv, _pS, _pE), (yoy, _yS, _yE)):
    if not _d.empty:
        _u = _d["cc"].map(wr.cc_units(_s, _e))
        _r = _u.map(lambda u: RATES.get(str(u).upper()) if u else None)
        _d["원화"] = (_d["현지"] * _r).where(_r.notna())
        _d["환율없음"] = _r.isna()

_miss = sorted(cur.loc[cur["환율없음"], "cc"].unique()) if "환율없음" in cur else []
if _miss:
    ui_theme.nbox("warn", "환율을 못 찾은 나라가 있어요 — "
                          f"<code>{', '.join(_miss)}</code>"
                          "<div class='sub'>그 나라 매출은 합계에서 빠져 있어요.</div>")


# ── ① 팀 × 구좌 × 국가 ─────────────────────────────────────────────────────
ui_theme.sec("1", "주별 매출", f"{S} ~ {E}")

# ★★**렌탈 IP 는 뺀다** — 엑셀 COVER 머리에도 `**렌탈 제외` 라고 적혀 있다.
#   ★사용자가 '포함'하기로 한 것과 헷갈리면 안 된다. 둘은 다른 얘기다:
#     · **팝업·렌탈 매장에서 판 정규 IP**(코르티스 밀리오레 팝업부스) → **포함**한다.
#       `weekly_report.GUBUN_SQL` 이 브랜드를 안 보므로 이미 아티스트/캐릭터로 잡힌다.
#     · **렌탈 IP 자체**(타이틀이 `렌탈 …` 로 시작) → 뺀다. 행사용 별건이라
#       주간 매출 추이에 섞으면 주마다 들쭉날쭉해진다.
#   빼기만 하고 숨기지는 않는다 — 아래 캡션에 금액을 적어 준다.
_RENT = cur["구분"] == "렌탈"
_rent_amt = float(cur.loc[_RENT, "원화"].sum(skipna=True))
cur, prv, yoy = (d[d["구분"] != "렌탈"] if not d.empty else d for d in (cur, prv, yoy))

# ★'단독' 프레임(특정 장소 전용)은 **포함**한다(2026-09-03 사용자 확정).
#   `KT위즈 단독`(수원KT 파크 팝업부스) · `카이스트 단독`(카이스트 팝업부스) 처럼
#   대부분 팝업부스 매장이라, 이미 정한 '팝업 매장 정규 IP 포함' 과 같은 얘기다.
#   ★엑셀의 CMS 내려받기본에는 이게 **없어서**(26-a 누적시트엔 있다) 우리 합계가
#     더 크다. 오차가 아니라 우리 쪽이 더 완전한 것이라, 매주 "왜 다르지" 하지
#     않도록 금액을 적어 둔다.
#   ★`렌탈 … 단독` 은 위에서 이미 빠졌다 — '단독'이라서가 아니라 렌탈 IP 라서다.
_solo_amt = float(cur.loc[cur["타이틀"].str.contains("단독", na=False),
                          "원화"].sum(skipna=True))

_ccs = [c for c in cur.groupby("cc")["원화"].sum().sort_values(ascending=False).index][:8]


def _matrix(d: pd.DataFrame) -> pd.DataFrame:
    if d.empty:
        return pd.DataFrame()
    q = d[d["원화"].notna()].copy()
    q["구좌표시"] = q["구좌"].map({"EVENT": "픽", "WITH": "WITH", "BASIC": "BASIC"})
    # BASIC 은 구분까지 나눠 적는다 — 엑셀의 BASIC/ORIGINAL 두 줄이 이것이다.
    q.loc[q["구좌"] == "BASIC", "구좌표시"] = q.loc[q["구좌"] == "BASIC", "구분"]
    p = q.pivot_table(index=["팀", "구좌표시"], columns="cc", values="원화",
                      aggfunc="sum", fill_value=0)
    # ★★상위 8개국만 열로 세우고 나머지는 **`기타` 한 칸에 담는다.**
    #   전엔 그냥 잘라내서 매트릭스 TTL(26.2억)과 위 KPI 총액(28.5억)이 안 맞았다.
    #   합계가 두 개인 화면은 **어느 쪽이 맞는지 아무도 모른다** — 잘라낸 나라가
    #   30개국 중 22개라 금액도 작지 않다(그 주 2.3억).
    _keep = [c for c in _ccs if c in p.columns]
    _rest = [c for c in p.columns if c not in _keep]
    out = p.reindex(columns=_keep, fill_value=0)
    if _rest:
        out["기타"] = p[_rest].sum(axis=1)
    out["TTL"] = out.sum(axis=1)
    return out


_m = _matrix(cur)
_mp = _matrix(prv)
tot_cur = float(cur["원화"].sum(skipna=True))
tot_prv = float(prv["원화"].sum(skipna=True)) if not prv.empty else 0.0
tot_yoy = float(yoy["원화"].sum(skipna=True)) if not yoy.empty else 0.0


def _pct(a, b):
    return f"{(a / b - 1) * 100:+.1f}%" if b else "—"


# ── 표현 도우미 (2026-09-03 재디자인) ─────────────────────────────────────
# ★증감은 **색이 말하게 한다** — 하락 빨강 · 상승 초록. 전주 0원(신규)은 NEW.
def _delta_pill(a: float, b: float) -> str:
    if not b:
        return '<span class="wk-new">NEW</span>'
    p = (a / b - 1) * 100
    if p >= 0:
        return f'<span class="wk-up">▲ {p:.1f}%</span>'
    return f'<span class="wk-dn">▼ {abs(p):.1f}%</span>'


# ★국가는 **이름으로** 적는다 — `kr/cn` 코드는 스내피즘 재디자인 때 이미 금지한
#   문법이다(임시 티도 난다). 모르는 코드는 대문자로라도 보여 준다.
_CC_KO = {"kr": "한국", "cn": "중국", "tw": "대만", "jp": "일본", "id": "인니",
          "us": "미국", "vn": "베트남", "hk": "홍콩", "th": "태국", "sg": "싱가포르",
          "my": "말련", "ph": "필리핀", "mo": "마카오", "ae": "아랍", "gb": "영국",
          "ca": "캐나다", "mx": "멕시코", "au": "호주", "es": "스페인", "gu": "괌"}


def _cc_ko(cc: str) -> str:
    return _CC_KO.get(str(cc).lower(), str(cc).upper())


def _n(v) -> str:
    return f"{v:,.0f}" if pd.notna(v) and v else '<span class="dash">—</span>'


# ★표를 st.dataframe 에서 직접 HTML 로 바꿨으니 **값은 반드시 이스케이프**한다.
#   IP·상품 이름은 CMS 에서 온 남의 문자열이라 `&`·`<` 가 섞일 수 있고, 그러면
#   그 줄이 통째로 깨진다(dataframe 은 알아서 해 주던 일이다).
def _e(v) -> str:
    return _html_mod.escape(str(v), quote=False)


# ── 히어로: 이번 주 성적표 한 장 ─────────────────────────────────────────
# ★전주·전년 비교를 히어로 카드 **안에** 붙인다 — 카드 셋으로 흩으면
#   "이번 주 성적" 이라는 한 문장이 세 조각 난다(2026-09-03 재디자인).
st.markdown(
    '<div class="wkhero"><div class="wl">'
    f'<div class="wk">이번 주 매출 · {S[5:]} ~ {E[5:]}</div>'
    f'<div class="wv">{tot_cur:,.0f}원</div>'
    '<div class="wb">'
    + (f'<span class="wkbdg wk-upb">▲ {(tot_cur / tot_prv - 1) * 100:.1f}%'
       '<small>전주 대비</small></span>' if tot_prv and tot_cur >= tot_prv else
       f'<span class="wkbdg wk-dnb">▼ {abs((tot_cur / tot_prv - 1) * 100):.1f}%'
       '<small>전주 대비</small></span>' if tot_prv else "")
    + (f'<span class="wkbdg wk-upb">▲ {(tot_cur / tot_yoy - 1) * 100:.1f}%'
       '<small>전년 대비</small></span>' if tot_yoy and tot_cur >= tot_yoy else
       f'<span class="wkbdg wk-dnb">▼ {abs((tot_cur / tot_yoy - 1) * 100):.1f}%'
       '<small>전년 대비</small></span>' if tot_yoy else "")
    + '</div></div><div class="wr">'
    f'<div><span>전주 ({_pS[5:]} ~ {_pE[5:]})</span><b>{tot_prv:,.0f}원</b></div>'
    f'<div><span>전년 같은 주 ({_yS[2:]} ~ {_yE[5:]})</span><b>{tot_yoy:,.0f}원</b></div>'
    f'<div><span>{cur["cc"].nunique()}개국 · 환율 {RDATE}</span>'
    f'<b style="font-weight:600;color:var(--text-3)">{RSRC}</b></div>'
    '</div></div>', unsafe_allow_html=True)

# ★긴 집계 각주는 접어 둔다 — 매주 같은 문장이 본문 가운데 떠 있을 이유가 없다.
#   내용은 그대로다(단독·렌탈 금액이 매주 갱신되는 것도 그대로).
with st.expander("ⓘ 집계 기준 — 단독 포함 · 렌탈 제외 · 엑셀과 다른 점"):
    st.markdown(f"**(단위: 원)** · 팝업 매장에서 판 정규 IP 와 "
                f"**단독 프레임**(KT위즈·카이스트 등)은 **포함**했어요"
                f"(이번 주 단독 {_solo_amt:,.0f}원). "
                f"렌탈 IP 는 **뺐어요**(이번 주 {_rent_amt:,.0f}원). "
                f"단독은 엑셀 CMS 내려받기본에 빠져 있어 **엑셀보다 그만큼 커요.** "
                f"전년은 **같은 요일**로 364일 전({_yS} ~ {_yE})이에요.")

if not _m.empty:
    # ★st.dataframe(줄무늬 그리드) 대신 ui_theme 의 ntbl 문법 — 국가명 헤더 +
    #   합계 굵게 + 오른쪽 끝에 주 전체 대비 비중 막대. TTL 이라는 말도 안 쓴다.
    _cols = [c for c in _m.columns if c not in ("TTL",)]
    _gt = f"34px 100px repeat({len(_cols)},1fr) 1.05fr 108px"
    _rows_h = "".join(f'<span class="r">{_e(_cc_ko(c)) if c != "기타" else "기타"}</span>'
                      for c in _cols)
    _html = [f'<div class="ntbl"><div class="ntr nth" style="grid-template-columns:{_gt}">'
             f'<span>팀</span><span>구좌</span>{_rows_h}'
             f'<span class="r">합계</span><span class="r">비중</span></div>']
    _mx_frac = float((_m["TTL"] / tot_cur).max()) if tot_cur else 0
    for (tm_, gz_), r in _m.iterrows():
        cells = "".join(f'<span class="r">{_n(r[c])}</span>' for c in _cols)
        _html.append(
            f'<div class="ntr" style="grid-template-columns:{_gt}">'
            f'<span class="nname">{_e(tm_)}</span><span>{_e(gz_)}</span>{cells}'
            f'<span class="r"><b>{r["TTL"]:,.0f}</b></span>'
            f'<span>{ui_theme.bar((r["TTL"] / tot_cur) if tot_cur else 0, _mx_frac)}</span>'
            '</div>')
    _sum_cells = "".join(f'<span class="r">{_n(_m[c].sum())}</span>' for c in _cols)
    _html.append(f'<div class="ntr sum" style="grid-template-columns:{_gt}">'
                 f'<span></span><span>합계</span>{_sum_cells}'
                 f'<span class="r">{_m["TTL"].sum():,.0f}</span><span></span></div></div>')
    st.markdown("".join(_html), unsafe_allow_html=True)
    st.caption("(단위: 원) · 비중은 이번 주 전체 매출 대비예요.")

# ── ② 팀별 TOP 10 ─────────────────────────────────────────────────────────
ui_theme.sec("2", "팀별 TOP 10", "회차가 나뉘어 있으면 IP 이름으로 합쳐요")
# ★★TOP 10 에서는 **오리지널(기본)을 뺀다** — `블랙`·`민트 도트`·`화이트` 처럼
#   IP 가 아니라 기본 프레임 디자인이다. 안 빼면 금액이 커서 목록을 통째로 차지하고
#   'IP TOP 10' 이라는 말이 무의미해진다(엑셀 TOP 10 에도 없다).
#   ★위 매트릭스에는 그대로 둔다 — 매출 자체는 있는 것이니 합계에서 빼면 안 된다.
#   `P ` 프레임(오리지널(포토이즘))은 `그냥집사`·`다죽` 같은 **진짜 IP** 라 남긴다.
_ORIG = ["오리지널(기본)", "오리지널(포토이즘)"]
# ★★오리지널은 **탭을 따로** 준다. 캐릭터에 섞으면 목록을 통째로 차지한다 —
#   실측: `민트 도트` 224.6백만 vs 엑셀 캐릭터 1위 `우땅이` 45.1백만.
#   엑셀의 캐릭터 TOP 10 에도 오리지널은 없다(우땅이·그냥집사·다죽·가나디…).
#   ★그렇다고 숨기지는 않는다 — 사용자가 'IP까지 쪼개 보기'로 정했으므로
#     오리지널 탭에서 프레임별로 다 보인다. 매트릭스에도 그대로 있다.
#   ★`P ` 프레임 안에서 **작가 협업(그냥집사)과 자체 시즌 디자인(민트 도트)은
#     데이터로 안 갈린다** — 둘 다 `P ` BASIC 이다. 가르려면 사람이 정해 줘야 한다.
_prev_ip = (prv.assign(ip=lambda d: d["표시IP"].map(wr.ip_name))
              .groupby(["ip"])["원화"].sum() if not prv.empty else pd.Series(dtype=float))
tabs = st.tabs(["A · 아티스트", "C · 캐릭터", "오리지널(포토이즘 자체)"])
for tab, team in zip(tabs[:2], ("A", "C")):
    with tab:
        q = cur[~cur["구분"].isin(_ORIG) & (cur["팀"] == team)
                & cur["원화"].notna()].copy()
        if q.empty:
            st.caption("이 팀 매출이 없어요.")
            continue
        q["ip"] = q["표시IP"].map(wr.ip_name)
        g = (q.groupby("ip", as_index=False)
               .agg(원화=("원화", "sum"), 건수=("건수", "sum"))
               .sort_values("원화", ascending=False).head(10))
        g["전주"] = g["ip"].map(_prev_ip).fillna(0)
        # ★★금액은 **원 단위 + 쉼표**다. 매주 보시던 엑셀 리포트가 원 단위 맨숫자라
        #   여기서 백만원으로 바꾸면 같은 표를 두 단위로 읽게 된다(2026-09-03 지적).
        # ★순위·증감 색·비중 막대를 넣는다(2026-09-03 재디자인) — 1위와 10위가
        #   같은 무게로 보이면 TOP 10 이라는 말이 심심해진다. 1위는 브랜드색.
        _tt = float(q["원화"].sum())
        _gt10 = "26px minmax(0,1.6fr) 1fr 1fr 96px 130px 74px"
        _h10 = [f'<div class="ntbl"><div class="ntr nth" style="grid-template-columns:{_gt10}">'
                '<span>#</span><span>IP</span><span class="r">매출</span>'
                '<span class="r">전주</span><span class="r">증감</span>'
                '<span class="r">비중</span><span class="r">건수</span></div>']
        _mx10 = float((g["원화"] / _tt).max()) if _tt else 0
        for _i, (_, r) in enumerate(g.iterrows(), start=1):
            top = " wk-top1" if _i == 1 else ""
            _h10.append(
                f'<div class="ntr" style="grid-template-columns:{_gt10}">'
                f'<span class="dim{top}">{_i}</span>'
                f'<span class="nname{top}">{_e(r["ip"])}</span>'
                f'<span class="r"><b>{r["원화"]:,.0f}</b></span>'
                f'<span class="r dim">{_n(r["전주"])}</span>'
                f'<span class="r">{_delta_pill(r["원화"], r["전주"])}</span>'
                f'<span>{ui_theme.bar((r["원화"] / _tt) if _tt else 0, _mx10)}</span>'
                f'<span class="r">{int(r["건수"]):,d}</span></div>')
        _h10.append("</div>")
        st.markdown("".join(_h10), unsafe_allow_html=True)
        st.caption("(매출·전주 단위: 원) · 비중은 이 팀 매출 대비예요.")

with tabs[2]:
    q = cur[cur["구분"].isin(_ORIG) & cur["원화"].notna()].copy()
    if q.empty:
        st.caption("이 주에는 오리지널 매출이 없어요.")
    else:
        q["ip"] = q["표시IP"].map(wr.ip_name)
        g = (q.groupby(["ip", "구분"], as_index=False)
               .agg(원화=("원화", "sum"), 건수=("건수", "sum"))
               .sort_values("원화", ascending=False).head(15))
        g["전주"] = g["ip"].map(_prev_ip).fillna(0)
        # ★A/C 탭과 같은 문법 — 표가 탭마다 다르게 생기면 눈이 매번 다시 배운다.
        _gto = "26px minmax(0,1.6fr) 150px 1fr 1fr 96px 74px"
        _ho = [f'<div class="ntbl"><div class="ntr nth" style="grid-template-columns:{_gto}">'
               '<span>#</span><span>프레임</span><span>구분</span>'
               '<span class="r">매출</span><span class="r">전주</span>'
               '<span class="r">증감</span><span class="r">건수</span></div>']
        for _i, (_, r) in enumerate(g.iterrows(), start=1):
            top = " wk-top1" if _i == 1 else ""
            _ho.append(
                f'<div class="ntr" style="grid-template-columns:{_gto}">'
                f'<span class="dim{top}">{_i}</span>'
                f'<span class="nname{top}">{_e(r["ip"])}</span>'
                f'<span class="dim">{_e(r["구분"])}</span>'
                f'<span class="r"><b>{r["원화"]:,.0f}</b></span>'
                f'<span class="r dim">{_n(r["전주"])}</span>'
                f'<span class="r">{_delta_pill(r["원화"], r["전주"])}</span>'
                f'<span class="r">{int(r["건수"]):,d}</span></div>')
        _ho.append("</div>")
        st.markdown("".join(_ho), unsafe_allow_html=True)
        st.caption("(매출·전주 단위: 원) · 포토이즘 자체 프레임이에요. "
                   "`P ` 는 기획 프레임, 나머지는 기본 디자인이고요 — "
                   "**IP 협업(그냥집사)과 자체 시즌 디자인(민트 도트)은 데이터로는 "
                   "안 갈려요.** 갈라야 하면 알려 주세요.")

# ── ③ 스내피즘 ────────────────────────────────────────────────────────────
ui_theme.sec("3", "스내피즘", "판매 항목 × 국가")
sn = _sn(S, E, os.path.getmtime(wr.SN_RAW) if wr.SN_RAW.exists() else 0.0)
if sn.empty:
    st.caption("그 주에는 스내피즘 매출이 없어요.")
else:
    _r = sn["unit"].map(lambda u: RATES.get(str(u).upper()))
    sn["원화"] = (sn["현지"] * _r).where(_r.notna())
    sp = sn[sn["원화"].notna()].pivot_table(index="상품", columns="국가",
                                            values="원화", aggfunc="sum", fill_value=0)
    sp["합계"] = sp.sum(axis=1)
    sp = sp.sort_values("합계", ascending=False)
    # ★①과 같은 ntbl 문법 + 비중 막대 — 국가는 이미 이름이라 그대로 쓴다.
    _snc = [c for c in sp.columns if c != "합계"]
    _sngt = f"minmax(0,1.2fr) repeat({len(_snc)},1fr) 1.05fr 108px"
    _sntt = float(sp["합계"].sum())
    _snh = [f'<div class="ntbl"><div class="ntr nth" style="grid-template-columns:{_sngt}">'
            '<span>상품</span>'
            + "".join(f'<span class="r">{_e(c)}</span>' for c in _snc)
            + '<span class="r">합계</span><span class="r">비중</span></div>']
    _snmx = float((sp["합계"] / _sntt).max()) if _sntt else 0
    for idx, r in sp.iterrows():
        cells = "".join(f'<span class="r">{_n(r[c])}</span>' for c in _snc)
        _snh.append(f'<div class="ntr" style="grid-template-columns:{_sngt}">'
                    f'<span class="nname">{_e(idx)}</span>{cells}'
                    f'<span class="r"><b>{r["합계"]:,.0f}</b></span>'
                    f'<span>{ui_theme.bar((r["합계"] / _sntt) if _sntt else 0, _snmx)}</span>'
                    '</div>')
    _snh.append(f'<div class="ntr sum" style="grid-template-columns:{_sngt}">'
                f'<span>합계</span>'
                + "".join(f'<span class="r">{_n(sp[c].sum())}</span>' for c in _snc)
                + f'<span class="r">{_sntt:,.0f}</span><span></span></div></div>')
    st.markdown("".join(_snh), unsafe_allow_html=True)
    st.caption("(단위: 원)")
    st.markdown("**상품별 IP TOP 10**")
    _stab = st.tabs(list(sp.index[:4]))
    for t, cat in zip(_stab, sp.index[:4]):
        with t:
            g = (sn[(sn["상품"] == cat) & sn["원화"].notna()]
                 .groupby("타이틀", as_index=False)
                 .agg(원화=("원화", "sum"), 건수=("건수", "sum"))
                 .sort_values("원화", ascending=False).head(10))
            _ct = float(g["원화"].sum())
            _cgt = "26px minmax(0,1.6fr) 1fr 130px 74px"
            _ch = [f'<div class="ntbl"><div class="ntr nth" style="grid-template-columns:{_cgt}">'
                   '<span>#</span><span>IP</span><span class="r">매출</span>'
                   '<span class="r">비중</span><span class="r">건수</span></div>']
            _cmx = float((g["원화"] / _ct).max()) if _ct else 0
            for _i, (_, r) in enumerate(g.iterrows(), start=1):
                top = " wk-top1" if _i == 1 else ""
                _ch.append(f'<div class="ntr" style="grid-template-columns:{_cgt}">'
                           f'<span class="dim{top}">{_i}</span>'
                           f'<span class="nname{top}">{_e(r["타이틀"])}</span>'
                           f'<span class="r"><b>{r["원화"]:,.0f}</b></span>'
                           f'<span>{ui_theme.bar((r["원화"] / _ct) if _ct else 0, _cmx)}</span>'
                           f'<span class="r">{int(r["건수"]):,d}</span></div>')
            _ch.append("</div>")
            st.markdown("".join(_ch), unsafe_allow_html=True)
            st.caption("(매출 단위: 원) · 비중은 이 상품 매출 대비예요.")

# ── ④ 새로 나온 IP 팀 확인 ─────────────────────────────────────────────────
unk = cur[cur["근거"] == "접두어"].groupby(
    ["타이틀", "구분", "팀"], as_index=False)["원화"].sum().sort_values(
    "원화", ascending=False)
# ★건수는 빨간 pill — 이건 '보는 정보'가 아니라 '처리할 일감'이라서다.
ui_theme.sec("4", f'팀 확인이 필요해요 <span class="wkcnt">{len(unk)}건</span>',
             "규칙으로 짐작한 것들이에요 — 지정하면 다음 주부터 자동 반영돼요")
if unk.empty:
    ui_theme.nbox("ok", "✅ <b>이번 주는 다 정해져 있어요</b>")
else:
    st.caption("**`L `·`P ` 표식이 없는 캐릭터 IP** 가 여기 걸리기 쉬워요.")
    # ★행마다 [IP명(굵게)+구분 태그+코드(작은 회색)] · 금액 · A/C 선택을 한 줄로.
    #   전엔 항목과 라디오가 화면 양끝으로 찢어져 어느 행 것인지 눈으로 이어야 했다.
    _CODE_RE = re.compile(
        r"^((?:렌탈|PW|L7|L|P|B|SP|NX)\s+)?(\d{6,8}\s*)?(.*)$")
    for _i, (_, r) in enumerate(unk.head(20).iterrows()):
        k = str(r["타이틀"])
        m = _CODE_RE.match(k)
        _code = ((m.group(1) or "") + (m.group(2) or "")).strip()
        _name = (m.group(3) or k).strip() or k
        if _i:
            st.markdown('<div class="wkhr"></div>', unsafe_allow_html=True)
        cc1, cc2, cc3 = st.columns([3.1, 1.0, 1.3], vertical_alignment="center")
        cc1.markdown(f'<span class="wknm">{_e(_name)}</span>'
                     f'<span class="wktag">{_e(r["구분"])}</span>'
                     + (f'<br><span class="wkcode">{_e(_code)}</span>' if _code else ""),
                     unsafe_allow_html=True)
        cc2.markdown(f'<div class="r num" style="text-align:right;font-weight:700">'
                     f'{0 if pd.isna(r["원화"]) else int(r["원화"]):,}원</div>',
                     unsafe_allow_html=True)
        pick = cc3.radio("팀", ["A", "C"], index=0 if r["팀"] == "A" else 1,
                         key=f"tm_{k}", horizontal=True, label_visibility="collapsed",
                         disabled=not CAN_EDIT)
        if pick != r["팀"] and CAN_EDIT:
            wr.set_team(k, pick, _email)
            st.rerun()

# ── ⑤ 주차별 추이 ─────────────────────────────────────────────────────────
# 엑셀의 `26-a`(아티스트) · `26-c`(캐릭터) 누적 시트를 대신한다 — IP 행 × 주차 열에
# 매주 한 칸씩 쌓이는 그 표다.
# ★★계산은 **주간 숫자와 같은 함수**(`photoism_detail(by_week=True)`)를 쓴다.
#   추이용 집계를 따로 만들면 같은 화면에서 두 숫자가 갈린다 — 엑셀도 26-a/26-c 와
#   COVER 가 같은 원천을 본다. 검증: 주차별 합 = 통짜 합(9주 45,210,709,467 동일),
#   개별 주도 그 주만 조회한 값과 같다.
# ★기간이 길수록 느리다(35주 15.2초). 그래서 **기본 12주**에 mtime 캐시를 건다 —
#   원장이 안 바뀌면 다시 안 돈다.
ui_theme.sec("5", "주차별 추이", "엑셀 26-a · 26-c 를 대신해요")


@st.cache_data(ttl=3600, max_entries=4, show_spinner="주차별로 모으는 중이에요…")
def _trend(t_start, t_end, teamver, dataver):
    d = wr.photoism_detail(t_start, t_end, by_week=True)
    if d.empty:
        return d
    tm = wr.load_teams()
    d[["팀", "_"]] = d.apply(
        lambda r: pd.Series(wr.team_of(r["타이틀"], r["구분"], tm)), axis=1)
    d = d[d["구분"] != "렌탈"]
    _u = d["cc"].map(wr.cc_units(t_start, t_end))
    _r = _u.map(lambda u: RATES.get(str(u).upper()) if u else None)
    d["원화"] = (d["현지"] * _r).where(_r.notna())
    d["ip"] = d["표시IP"].map(wr.ip_name)
    d["주"] = pd.to_datetime(d["주"]).dt.strftime("%m/%d")
    return d[d["원화"].notna()]


_nw = st.slider("몇 주를 볼까요", 4, 52, 12, step=4, key="wk_n",
                help="길수록 느려요. 원장이 바뀌지 않으면 다시 계산하지 않아요.")
_tS = (date.fromisoformat(E) - timedelta(weeks=_nw - 1)).isoformat()
_tS = _week(date.fromisoformat(_tS))[0].isoformat()
tr = _trend(_tS, E, wr.team_version(), _dv)
if tr.empty:
    st.caption("그 기간에 매출이 없어요.")
else:
    _wks = sorted(tr["주"].unique())
    _ttab = st.tabs(["A · 아티스트", "C · 캐릭터"])
    for _tb, _tm_ in zip(_ttab, ("A", "C")):
        with _tb:
            q = tr[(tr["팀"] == _tm_) & ~tr["구분"].isin(_ORIG)]
            if q.empty:
                st.caption("이 팀 매출이 없어요.")
                continue
            # ★TOP 은 **마지막 주**가 아니라 기간 전체 합으로 고른다 — 마지막 주만
            #   보면 그 주에 반짝한 IP 가 올라오고 흐름이 안 보인다.
            top = (q.groupby("ip")["원화"].sum()
                     .sort_values(ascending=False).head(12).index.tolist())
            piv = (q[q["ip"].isin(top)]
                   .pivot_table(index="ip", columns="주", values="원화",
                                aggfunc="sum", fill_value=0)
                   .reindex(index=top, columns=_wks, fill_value=0))
            st.caption("**(단위: 원)** · 기간 전체 매출 큰 순 12개예요")
            st.dataframe(piv.round(0).style.format("{:,.0f}"),
                         use_container_width=True)
            _sel = st.multiselect("그래프로 볼 IP", top, default=top[:5],
                                  key=f"tr_ip_{_tm_}")
            if _sel:
                st.line_chart(piv.loc[[i for i in top if i in _sel]].T,
                              height=280)


# ── ⑥ 내려받기 ────────────────────────────────────────────────────────────
ui_theme.sec("6", "내려받기", "엑셀 한 장")
try:
    import io

    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        if not _m.empty:
            _m.round(0).to_excel(xw, sheet_name="주별매출")
        cur.drop(columns=["환율없음"], errors="ignore").to_excel(
            xw, sheet_name="포토이즘_상세", index=False)
        if not sn.empty:
            sn.to_excel(xw, sheet_name="스내피즘_상세", index=False)
        # 엑셀 26-a/26-c 를 대신하는 시트 — IP 행 × 주차 열
        if not tr.empty:
            for _tm2, _nm in (("A", "26-a_아티스트"), ("C", "26-c_캐릭터")):
                _q = tr[(tr["팀"] == _tm2) & ~tr["구분"].isin(_ORIG)]
                if _q.empty:
                    continue
                (_q.pivot_table(index="ip", columns="주", values="원화",
                                aggfunc="sum", fill_value=0)
                   .sort_values(by=list(sorted(_q["주"].unique()))[-1],
                                ascending=False)
                   .to_excel(xw, sheet_name=_nm))
    st.download_button("📥 엑셀 내려받기", buf.getvalue(),
                       file_name=f"IP주간분석_{S}_{E}.xlsx",
                       mime="application/vnd.openxmlformats-officedocument."
                            "spreadsheetml.sheet",
                       disabled=not auth.can_download(_email))
except Exception as e:                                          # noqa: BLE001
    st.caption(f"엑셀을 못 만들었어요 — {type(e).__name__}")
