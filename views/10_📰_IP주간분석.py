# -*- coding: utf-8 -*-
"""IP 주간분석 — 매주 엑셀로 만들던 `IP 매출 분석_MMDD.xlsx` 를 자동으로.

계산은 전부 `weekly_report.py` 가 한다(엑셀과 어디가 왜 다른지도 거기 주석에).
이 파일은 **고르고 · 보여주고 · 내려받는 것**만 맡는다.

★주 단위로 매번 다시 도는 화면이다. 그래서 두 가지를 화면이 책임진다:
  ① 매주 새로 나오는 IP 의 팀(A/C)을 **물어보고 예외표에 남긴다** — 안 그러면
     규칙이 늙는다(`weekly_report.unknown_teams` 주석 참고).
  ② 전주·전년과 나란히 보여준다 — 엑셀 COVER 가 그렇게 생겼다.
"""
import os
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
c1, c2, c3 = st.columns([1.2, 1.2, 2])
S = c1.date_input("시작(월)", value=_last_mon, key="wk_s")
E = c2.date_input("끝(일)", value=_last_sun, key="wk_e")
if S > E:
    st.error("시작일이 끝일보다 늦어요.")
    st.stop()
S, E = S.isoformat(), E.isoformat()
_pS = (date.fromisoformat(S) - timedelta(days=7)).isoformat()
_pE = (date.fromisoformat(E) - timedelta(days=7)).isoformat()
# ★전년은 **같은 요일**로 맞춘다(364일 = 52주). 날짜로 맞추면 요일이 어긋나
#   주말이 하루 더 든 주와 비교하게 된다 — 커버리지 조사에서 이미 겪은 함정이다.
_yS = (date.fromisoformat(S) - timedelta(days=364)).isoformat()
_yE = (date.fromisoformat(E) - timedelta(days=364)).isoformat()
c3.caption(f"전주 {_pS} ~ {_pE} · 전년 {_yS} ~ {_yE} "
           f"(전년은 **같은 요일**로 364일 전이에요)")

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
# theme_daily 엔 `결제 단위` 가 없어 국가코드로 통화를 잇는다.
_CC_UNIT = {"kr": "KRW", "cn": "CNH", "tw": "TWD", "jp": "JPY", "id": "IDR",
            "vn": "VND", "us": "USD", "th": "THB", "hk": "HKD", "my": "MYR",
            "ph": "PHP", "la": "LAK", "mn": "MNT", "lv": "EUR", "de": "EUR",
            "fr": "EUR", "nl": "EUR", "lu": "EUR", "bn": "BND", "au": "AUD"}
for _d in (cur, prv, yoy):
    if not _d.empty:
        _u = _d["cc"].map(_CC_UNIT)
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
    p = p.reindex(columns=[c for c in _ccs if c in p.columns], fill_value=0)
    p["TTL"] = p.sum(axis=1)
    return p


_m = _matrix(cur)
_mp = _matrix(prv)
tot_cur = float(cur["원화"].sum(skipna=True))
tot_prv = float(prv["원화"].sum(skipna=True)) if not prv.empty else 0.0
tot_yoy = float(yoy["원화"].sum(skipna=True)) if not yoy.empty else 0.0


def _pct(a, b):
    return f"{(a / b - 1) * 100:+.1f}%" if b else "—"


ui_theme.kpis([
    ui_theme.kpi("이번 주", f"{tot_cur:,.0f}원",
                 f"{S[5:]} ~ {E[5:]}", hero=True),
    ui_theme.kpi("전주 대비", _pct(tot_cur, tot_prv), f"{tot_prv:,.0f}원"),
    ui_theme.kpi("전년 대비", _pct(tot_cur, tot_yoy), f"{tot_yoy:,.0f}원"),
    ui_theme.kpi("나라", f"{cur['cc'].nunique()}개국",
                 f"환율 {RDATE} · {RSRC}"),
])

if not _m.empty:
    st.caption(f"**(단위: 원)** · 팝업 매장에서 판 정규 IP 매출은 **포함**하고, "
               f"렌탈 IP 는 **뺐어요**(이번 주 {_rent_amt:,.0f}원). "
               f"엑셀 리포트와 같은 기준이에요.")
    st.dataframe(_m.round(0).style.format("{:,.0f}"), use_container_width=True)

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
        #   ★단위를 바꾸는 대신 **Styler 로 쉼표만** 넣는다 — 문자열로 만들면
        #     표에서 정렬이 죽고, 그냥 두면 지수표기(`9.13e+07`)로 새어 못 읽는다.
        show = pd.DataFrame({
            "IP": g["ip"],
            "매출": g["원화"].round(0),
            "전주": g["전주"].round(0),
            "증감": [_pct(a, b) for a, b in zip(g["원화"], g["전주"])],
            "비중": (g["원화"] / q["원화"].sum() * 100).round(1),
            "건수": g["건수"].astype(int),
        })
        st.caption("**(매출·전주 단위: 원 · 비중: %)**")
        st.dataframe(show.style.format({"매출": "{:,.0f}", "전주": "{:,.0f}",
                                        "비중": "{:.1f}", "건수": "{:,d}"}),
                     use_container_width=True, hide_index=True)

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
        st.caption("**(매출·전주 단위: 원)** · 포토이즘 자체 프레임이에요. "
                   "`P ` 는 기획 프레임, 나머지는 기본 디자인이고요 — "
                   "**IP 협업(그냥집사)과 자체 시즌 디자인(민트 도트)은 데이터로는 "
                   "안 갈려요.** 갈라야 하면 알려 주세요.")
        st.dataframe(pd.DataFrame({
            "프레임": g["ip"], "구분": g["구분"],
            "매출": g["원화"].round(0),
            "전주": g["전주"].round(0),
            "증감": [_pct(a, b) for a, b in zip(g["원화"], g["전주"])],
            "건수": g["건수"].astype(int),
        }).style.format({"매출": "{:,.0f}", "전주": "{:,.0f}", "건수": "{:,d}"}),
            use_container_width=True, hide_index=True)

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
    sp["TTL"] = sp.sum(axis=1)
    sp = sp.sort_values("TTL", ascending=False)
    st.caption("**(단위: 원)**")
    st.dataframe(sp.round(0).style.format("{:,.0f}"), use_container_width=True)
    st.markdown("**상품별 IP TOP 10**")
    _stab = st.tabs(list(sp.index[:4]))
    for t, cat in zip(_stab, sp.index[:4]):
        with t:
            g = (sn[(sn["상품"] == cat) & sn["원화"].notna()]
                 .groupby("타이틀", as_index=False)
                 .agg(원화=("원화", "sum"), 건수=("건수", "sum"))
                 .sort_values("원화", ascending=False).head(10))
            st.caption("**(매출 단위: 원)**")
            st.dataframe(pd.DataFrame({
                "IP": g["타이틀"],
                "매출": g["원화"].round(0),
                "건수": g["건수"].astype(int),
            }).style.format({"매출": "{:,.0f}", "건수": "{:,d}"}),
                use_container_width=True, hide_index=True)

# ── ④ 새로 나온 IP 팀 확인 ─────────────────────────────────────────────────
unk = cur[cur["근거"] == "접두어"].groupby(
    ["타이틀", "구분", "팀"], as_index=False)["원화"].sum().sort_values(
    "원화", ascending=False)
ui_theme.sec("4", "팀 확인이 필요해요", f"{len(unk)}개")
if unk.empty:
    ui_theme.nbox("ok", "✅ <b>이번 주는 다 정해져 있어요</b>")
else:
    st.caption("규칙으로 짐작한 것들이에요. 한 번 정해 두면 다음 주부터 그대로 써요. "
               "**`L `·`P ` 표식이 없는 캐릭터 IP** 가 여기 걸리기 쉬워요.")
    for _, r in unk.head(20).iterrows():
        k = str(r["타이틀"])
        cc1, cc2 = st.columns([3, 1])
        cc1.markdown(f"`{k}` · {r['구분']} · "
                     f"{0 if pd.isna(r['원화']) else int(r['원화']):,}원 "
                     f"— 지금은 **{r['팀']}** 으로 봐요")
        pick = cc2.radio("팀", ["A", "C"], index=0 if r["팀"] == "A" else 1,
                         key=f"tm_{k}", horizontal=True, label_visibility="collapsed",
                         disabled=not CAN_EDIT)
        if pick != r["팀"] and CAN_EDIT:
            wr.set_team(k, pick, _email)
            st.rerun()

# ── ⑤ 내려받기 ────────────────────────────────────────────────────────────
ui_theme.sec("5", "내려받기", "엑셀 한 장")
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
    st.download_button("📥 엑셀 내려받기", buf.getvalue(),
                       file_name=f"IP주간분석_{S}_{E}.xlsx",
                       mime="application/vnd.openxmlformats-officedocument."
                            "spreadsheetml.sheet",
                       disabled=not auth.can_download(_email))
except Exception as e:                                          # noqa: BLE001
    st.caption(f"엑셀을 못 만들었어요 — {type(e).__name__}")
