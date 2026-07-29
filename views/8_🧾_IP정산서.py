# -*- coding: utf-8 -*-
"""IP 정산서 — 1단계: 티켓↔타이틀 매핑.

정산은 **지라 티켓번호를 정(正)** 으로 한다. 이 화면에서 실무자가
타이틀마다 티켓을 확정하고, 어느 티켓에도 안 붙은 매출(잔여)이 0원이 될 때까지
정산서 발행을 막는다. 조용한 누락을 원천 차단하는 게 목적이다.

자동 매칭은 **후보 제안까지만** 한다 — 동점일 때 캐시 파일 순서로 승자가 갈려서
재크롤하면 결과가 바뀌기 때문이다. 확정은 사람이 누른 것만 인정한다.

기획: CURRENT-PROJECTS/IP-정산서-생성.md · 지라 CO-288
"""
import os
import sys
from datetime import date, timedelta

import streamlit as st
from streamlit.errors import StreamlitAPIException

# set_page_config 는 라우터(스내피즘.py)에서 처리
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import auth
import settlement_calc as sc
import settlement_map as sm

# 금액을 다루는 페이지다. 사이드바에서 이미 걸러지지만 url 직접 입력도 막는다.
_email = (st.user.email or "").strip().lower() if getattr(st, "user", None) else ""
if not auth.can_view_page(_email, "settledoc"):
    st.error("🔒 이 페이지에 접근할 권한이 없어요. 필요하면 관리자에게 요청해 주세요.")
    st.stop()

CAN_EDIT = auth.can_edit(_email)     # 승인·저장은 owner/editor 만

INK = "#1a1a2e"
BRAND = "#4f46e5"

st.markdown(f"""
<style>
.section-title {{ font-size:1.12rem; font-weight:700; color:{INK};
  margin:4px 0 12px; padding-left:12px; border-left:4px solid {BRAND}; line-height:1.4; }}
.res-ok, .res-warn {{ border-radius:12px; padding:14px 18px; margin:6px 0 14px;
  border:1px solid; font-size:.92rem; }}
.res-ok   {{ background:#eefbf3; border-color:#b6e6c8; color:#166534; }}
.res-warn {{ background:#fff7ed; border-color:#fcd9a8; color:#92400e; }}
.res-big  {{ font-size:1.5rem; font-weight:800; letter-spacing:-.02em; }}
.tk {{ font-family:ui-monospace,Menlo,monospace; font-weight:700; color:{BRAND}; }}
.muted {{ color:#8a93a3; font-size:.83rem; }}
</style>
""", unsafe_allow_html=True)

st.markdown('<div class="section-title">🧾 IP 정산서 · 티켓 매핑</div>',
            unsafe_allow_html=True)
st.caption("정산은 지라 티켓번호를 기준으로 해요. 타이틀마다 티켓을 확정하면 "
           "아래 잔여 매출이 줄어들고, 0원이 되면 정산서를 낼 수 있어요.")


# ── 데이터 로딩 ────────────────────────────────────────────────────────────
# ★st.cache_data 는 밑줄로 시작하는 인자를 해시에서 제외한다.
#   버전 값을 넘길 땐 절대 밑줄을 붙이지 말 것(캐시 키가 죽는다).
@st.cache_data(ttl=900, max_entries=8, show_spinner="매출을 집계하는 중이에요…")
def _titles(brand, start, end, rate_key):
    # 종료일 환율은 캐시에 없으면 조회를 시도한다(최대 수십 초). 캐시가 900초라
    # 실무자가 기다리는 건 기간을 처음 바꿀 때 한 번뿐이다.
    rates, eff = sm.load_rates(end)
    return sm.title_revenue(brand, start, end, rates), eff


def load_titles(brand, start, end):
    return _titles(brand, start, end, end)


# ── 기간 ─────────────────────────────────────────────────────────────────
_today = date.today()
_dflt_end = _today.replace(day=1) - timedelta(days=1)      # 지난달 말일
_dflt_start = _dflt_end.replace(day=1)

c1, c2, c3 = st.columns([1.1, 1.1, 2.2])
start = c1.date_input("정산 시작", _dflt_start, format="YYYY-MM-DD")
end = c2.date_input("정산 종료", _dflt_end, format="YYYY-MM-DD")
if start > end:
    st.error("시작일이 종료일보다 늦어요.")
    st.stop()
S, E = start.isoformat(), end.isoformat()

_, _eff = load_titles("photoism", S, E)
c3.markdown(f"<div class='muted' style='padding-top:30px'>환율 기준일 "
            f"<b>{_eff or '—'}</b> · 서울외국환중개 매매기준율<br>"
            f"종료일 당일, 휴장일이면 직전 영업일</div>", unsafe_allow_html=True)

if not CAN_EDIT:
    st.info("🔎 보기 전용이에요. 승인·저장은 편집 권한이 있어야 해요.")


# ── 브랜드별 탭 ────────────────────────────────────────────────────────────
STATE_ICON = {"확정": "✅", "제외": "⛔", "선택필요": "🔀",
              "확인필요": "🟡", "미연결": "❓"}
STATE_HELP = {
    "선택필요": "후보 티켓이 여러 개예요. 어느 회차인지 골라 주세요.",
    "확인필요": "후보가 하나예요. 맞는지 확인하고 승인해 주세요.",
    "미연결": "후보를 못 찾았어요. 티켓번호를 직접 넣어 주세요.",
}


def _fmt(v):
    return f"{int(v):,}"


def _rerun():
    """확정 직후 목록 갱신.

    `scope="fragment"` 는 **프래그먼트 재실행 중에만** 쓸 수 있다. 전체 스크립트가
    도는 중에 버튼이 눌리면 StreamlitAPIException 이 난다 → 전체 rerun 으로 떨어진다.
    (rerun 신호인 RerunException 은 StreamlitAPIException 이 아니라 그대로 통과한다)
    """
    try:
        st.rerun(scope="fragment")
    except StreamlitAPIException:
        st.rerun()


@st.fragment
def brand_panel(brand: str):
    df, _ = load_titles(brand, S, E)
    if df.empty:
        st.info("이 기간에 정산 대상 매출이 없어요.")
        return
    ann = sm.annotate(df, brand, S, E)
    r = sm.residual(ann)

    # ── 잔여 검증 ─────────────────────────────────────────────────────
    if r["잔여매출"] == 0:
        st.markdown(
            f"<div class='res-ok'>✅ <b>잔여 매출 0원</b> — 모든 타이틀이 확정됐어요. "
            f"정산서를 낼 수 있어요.<br><span class='muted'>확정 {r['확정건']}건 · "
            f"총 {_fmt(r['총매출'])}원</span></div>", unsafe_allow_html=True)
    else:
        st.markdown(
            f"<div class='res-warn'>미확정 매출 <span class='res-big'>"
            f"{_fmt(r['잔여매출'])}원</span> · 전체의 {r['잔여비중']:.1f}%<br>"
            f"<span class='muted'>남은 타이틀 {r['잔여건']}개 · 확정 {r['확정건']}개 · "
            f"총 {_fmt(r['총매출'])}원 — 0원이 되면 발행할 수 있어요</span></div>",
            unsafe_allow_html=True)

    # ── 진행 현황 ─────────────────────────────────────────────────────
    cols = st.columns(5)
    for i, s in enumerate(["확정", "선택필요", "확인필요", "미연결", "제외"]):
        sub = ann[ann["상태"] == s]
        cols[i].metric(f"{STATE_ICON[s]} {s}", f"{len(sub)}건",
                       f"{_fmt(sub['매출액'].sum())}원" if len(sub) else None,
                       delta_color="off")

    # ── 대기열 ────────────────────────────────────────────────────────
    # 매출 큰 것부터 처리하는 게 맞다 — 상위 73개면 전체 금액의 80% 가 잠긴다.
    f1, f2 = st.columns([2, 1.4])
    only_todo = f1.checkbox("미확정만 보기", value=True, key=f"todo_{brand}")
    q = f2.text_input("타이틀 검색", key=f"q_{brand}", placeholder="이름 일부")

    view = ann[~ann["상태"].isin(["확정", "제외"])] if only_todo else ann
    if q.strip():
        view = view[view["타이틀"].str.contains(q.strip(), case=False, na=False)]
    view = view.sort_values("매출액", ascending=False)

    if view.empty:
        st.success("처리할 타이틀이 없어요.")
        return

    # 파레토 안내 — 어디까지 하면 되는지 보여주면 끝이 보인다
    cum = ann.sort_values("매출액", ascending=False)["매출액"].cumsum()
    n80 = int((cum <= ann["매출액"].sum() * 0.8).sum()) + 1
    st.caption(f"매출 큰 순서예요. 상위 **{n80}개**만 확정하면 이 브랜드 매출의 "
               f"80%가 잠겨요. (전체 {len(ann)}개)")

    for _, row in view.head(50).iterrows():
        _title_row(brand, row)

    if len(view) > 50:
        st.caption(f"…외 {len(view) - 50}개. 검색으로 좁혀 보세요.")


def _title_row(brand: str, row):
    """타이틀 1건 — 후보 선택 / 직접 입력 / 제외."""
    t = row["타이틀"]
    state = row["상태"]
    label = (f"{STATE_ICON[state]} **{t or '(빈 타이틀)'}** · "
             f"{_fmt(row['매출액'])}원 · {row['IP구분']} · {row['국가수']}개국")
    if state == "확정":
        label += f" → `{row['티켓']}`"

    with st.expander(label, expanded=False):
        if state in STATE_HELP:
            st.caption(STATE_HELP[state])

        cands = sm.candidates(brand, t)
        cands.sort(key=lambda e: -sm.overlap_days(e, S, E))

        if cands:
            opts, meta = [], {}
            for e in cands:
                ov = sm.overlap_days(e, S, E)
                per = f"{e.get('startdate') or '?'} ~ {e.get('duedate') or '?'}"
                # 겹침 일수는 '이 티켓이 정산 기간을 실제로 덮는지' 를 보여준다.
                # 0일이면 기간이 안 겹치는 티켓이라 대개 다른 회차다.
                mark = "⚠️ 기간 안 겹침" if ov == 0 else f"겹침 {ov}일"
                lab = (f"{e['ticket_key']} · {e.get('title', '')} · {per} · {mark}"
                       f" · {e.get('brand', '') or '브랜드 미상'}")
                opts.append(lab)
                meta[lab] = e
            pick = st.radio("후보 티켓", opts, key=f"r_{brand}_{t}",
                            label_visibility="collapsed")
            chosen = meta[pick]
        else:
            st.warning("자동으로 찾은 후보가 없어요. 티켓번호를 직접 넣어 주세요.")
            chosen = None

        c1, c2, c3 = st.columns([1.5, 1.1, 1.1])
        manual = c1.text_input("티켓번호 직접 입력", key=f"m_{brand}_{t}",
                               placeholder="CANDIP-12345")

        # 직접 입력이 있으면 그쪽이 우선 — 사람이 명시적으로 친 값이니까
        target, verified = chosen, None
        if manual.strip():
            verified = sm.lookup_ticket(brand, manual)
            if verified:
                target = verified
                st.success(f"**{verified['ticket_key']}** · "
                           f"{' / '.join(verified['titles'][:3])} · "
                           f"{verified.get('startdate') or '?'} ~ "
                           f"{verified.get('duedate') or '?'}")
            else:
                st.error("그 번호를 캐시에서 못 찾았어요. 번호를 확인하거나 "
                         "지라 동기화가 필요할 수 있어요.")
                target = None

        if c2.button("✅ 확정", key=f"ok_{brand}_{t}",
                     disabled=not (CAN_EDIT and target), use_container_width=True):
            sm.approve(brand, t, target["ticket_key"], _email,
                       jira_title=target.get("title") or
                       (target.get("titles") or [""])[0])
            auth.log_event(_email, f"settlemap:{brand}:{t}→{target['ticket_key']}")
            _rerun()

        with c3.popover("⛔ 제외", disabled=not CAN_EDIT, use_container_width=True):
            why = st.text_input("제외 사유", key=f"why_{brand}_{t}",
                                placeholder="예: 자사 오리지널")
            if st.button("제외 확정", key=f"exok_{brand}_{t}",
                         disabled=not why.strip()):
                sm.exclude(brand, t, _email, why.strip())
                auth.log_event(_email, f"settleskip:{brand}:{t}")
                _rerun()

        if row["상태"] in ("확정", "제외"):
            if st.button("되돌리기", key=f"un_{brand}_{t}", disabled=not CAN_EDIT):
                sm.unapprove(brand, t)
                _rerun()


# ── 정산 계산 ─────────────────────────────────────────────────────────────
def _ticket_pick(brand: str):
    """확정된 티켓 중에서 정산 대상을 고른다. 확정 안 된 건 애초에 후보에 없다."""
    tks = sc.confirmed_tickets(brand)
    if not tks:
        return None, []
    opts = ["(없음)"] + [f"{k} · {' / '.join(v[:2])}"
                        + (f" 외 {len(v) - 2}" if len(v) > 2 else "")
                        for k, v in tks.items()]
    keys = [None] + list(tks.keys())
    i = st.selectbox(f"{sm.BRAND_LABEL[brand]} 티켓", range(len(opts)),
                     format_func=lambda x: opts[x], key=f"pk_{brand}")
    return keys[i], tks.get(keys[i], [])


def _rate_input(brand: str, ticket: str):
    """요율 입력. 지라값을 채워두되 화면 저장값이 이긴다(지라 입력률이 낮다)."""
    cur = sc.get_rs(brand, ticket)
    src = {"화면 입력": "🖊 화면 입력값", "지라": "🔗 지라값",
           "없음": "⚠️ 요율 없음 — 직접 넣어 주세요"}[cur["source"]]
    st.caption(f"{sm.BRAND_LABEL[brand]} · {src}")
    c1, c2, c3 = st.columns([1, 1, 1])
    a = c1.number_input("소속사 %", 0.0, 100.0,
                        float((cur["agency"] or 0) * 100), 0.5,
                        key=f"ra_{brand}_{ticket}")
    m = c2.number_input("대행사 %", 0.0, 100.0,
                        float((cur["mgmt"] or 0) * 100), 0.5,
                        key=f"rm_{brand}_{ticket}")
    if c3.button("💾 요율 저장", key=f"rsv_{brand}_{ticket}",
                 disabled=not CAN_EDIT, use_container_width=True):
        sc.set_rs(brand, ticket, a / 100 or None, m / 100 or None, _email)
        auth.log_event(_email, f"settlerate:{brand}:{ticket}")
        _rerun()
    return (a / 100 or None), (m / 100 or None)


def _mg_input(brand: str, ticket: str):
    """MG — v1은 있음/없음 + 수기 입력. 소진·이월 자동계산은 2차."""
    cur = sc.get_mg(brand, ticket)
    c1, c2, c3 = st.columns([0.8, 1.2, 1.6])
    has = c1.checkbox("MG 있음", value=cur["has_mg"], key=f"mgh_{brand}_{ticket}")
    amt = c2.number_input("MG 금액(원)", 0, step=1_000_000,
                          value=int(cur["amount"] or 0),
                          disabled=not has, key=f"mga_{brand}_{ticket}")
    note = c3.text_input("메모", value=cur["note"], disabled=not has,
                         key=f"mgn_{brand}_{ticket}")
    if st.button("💾 MG 저장", key=f"mgs_{brand}_{ticket}", disabled=not CAN_EDIT):
        sc.set_mg(brand, ticket, has, amt, note, _email)
        _rerun()
    return has, amt


@st.fragment
def calc_panel():
    picks = {}
    cols = st.columns(2)
    for col, b in zip(cols, sm.BRANDS):
        with col:
            picks[b] = _ticket_pick(b)
    if not any(t for t, _ in picks.values()):
        st.info("먼저 **티켓 매핑** 탭에서 타이틀을 확정해 주세요. "
                "확정된 티켓만 여기 나와요.")
        return

    rates, eff = sm.load_rates(E)
    total_base = total_a = total_m = 0
    details, pivots, warns = {}, {}, []

    for b, (tk, titles) in picks.items():
        if not tk:
            continue
        st.markdown(f"##### {sm.BRAND_LABEL[b]} · `{tk}`")
        st.caption("타이틀: " + " / ".join(titles))
        ra, rm = _rate_input(b, tk)
        _mg_input(b, tk)

        d = sc.country_detail(b, titles, S, E, rates)
        d = sc.fill_open(d, sc.open_countries(b, S, E))
        details[b] = d
        pivots[b] = sc.member_pivot(b, titles, S, E, rates)
        warns += sc.verify(d[d["매출액"] > 0], rates)

        base = int(d["매출액"].sum())
        total_base += base
        total_a += round(base * ra) if ra else 0
        total_m += round(base * rm) if rm else 0

        qty = "프레임수" if b == "photoism" else "건수"
        k = st.columns(4)
        k[0].metric("매출(KRW)", f"{_fmt(base)}원")
        k[1].metric(qty, _fmt(d["수량"].sum()))
        k[2].metric("소속사", f"{_fmt(round(base * ra))}원" if ra else "요율 없음")
        k[3].metric("대행사", f"{_fmt(round(base * rm))}원" if rm else "요율 없음")
        with st.expander(f"국가별 내역 · 오픈 {len(d)}개국 중 "
                         f"매출발생 {int((d['매출액'] > 0).sum())}개국"):
            st.dataframe(d[["국가", "unit", "수량", "현지", "매출액"]],
                         hide_index=True, use_container_width=True)
        st.divider()

    # ── 합산 ──────────────────────────────────────────────────────────
    st.markdown("##### 🧾 합산")
    k = st.columns(3)
    k[0].metric("정산기준액", f"{_fmt(total_base)}원")
    k[1].metric("소속사 정산액", f"{_fmt(total_a)}원")
    k[2].metric("대행사 정산액", f"{_fmt(total_m)}원")
    st.caption(f"환율 기준일 {eff or '—'} · 서울외국환중개 매매기준율")

    # ── 검증 ──────────────────────────────────────────────────────────
    if warns:
        st.error("환율 검증 실패 — 현지 매출 × 환율이 매출(KRW)과 안 맞아요:\n\n"
                 + "\n".join(f"- {w}" for w in warns[:5]))
    else:
        st.success("✅ 검증 통과 — 모든 국가에서 현지 매출 × 환율 = 매출(KRW)")

    # ── 멤버명 정규화 ─────────────────────────────────────────────────
    unmapped = sc.unmapped_members(*pivots.values())
    if unmapped:
        st.warning(f"한글 멤버명 {len(unmapped)}개가 영문과 따로 잡혀 있어요. "
                   "매핑하면 두 브랜드 표가 한 줄로 합쳐져요.")
        for ko in unmapped[:10]:
            c1, c2 = st.columns([1, 2])
            c1.text(ko)
            en = c2.text_input("영문 표기", key=f"al_{ko}",
                               label_visibility="collapsed",
                               placeholder="예: ASAHI")
            if en.strip() and CAN_EDIT:
                sc.set_member_alias(ko, en)
                _rerun()
    for b, p in pivots.items():
        if p is not None and not p.empty:
            with st.expander(f"{sm.BRAND_LABEL[b]} 국가 × 멤버"):
                st.dataframe(p, use_container_width=True)


t1, t2 = st.tabs(["🔗 티켓 매핑", "🧮 정산 계산"])
with t1:
    tabs = st.tabs(["📸 포토이즘", "📊 스내피즘"])
    for tab, b in zip(tabs, sm.BRANDS):
        with tab:
            brand_panel(b)
with t2:
    calc_panel()
