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
import re
import sys
from datetime import date, timedelta

import streamlit as st
from streamlit.errors import StreamlitAPIException

# set_page_config 는 라우터(스내피즘.py)에서 처리
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import auth
import settlement_calc as sc
import settlement_fx as fx
import settlement_map as sm
import settlement_mail as smail
import settlement_pdf as sp
import ui_theme

# 금액을 다루는 페이지다. 사이드바에서 이미 걸러지지만 url 직접 입력도 막는다.
_email = (st.user.email or "").strip().lower() if getattr(st, "user", None) else ""
if not auth.can_view_page(_email, "settledoc"):
    st.error("🔒 이 페이지에 접근할 권한이 없어요. 필요하면 관리자에게 요청해 주세요.")
    st.stop()

CAN_EDIT = auth.can_edit(_email)     # 승인·저장은 owner/editor 만

ui_theme.inject()
st.markdown('<div class="sechd"><span class="secn">🧾</span>'
            '<span class="sect">IP 정산서</span></div>'
            '<div class="secq">지라 티켓번호를 기준으로 정산해요. '
            '타이틀을 확정하면 잔여 매출이 줄고, 0원이 되면 정산서를 낼 수 있어요.</div>',
            unsafe_allow_html=True)


# ── 데이터 로딩 ────────────────────────────────────────────────────────────
# ★st.cache_data 는 밑줄로 시작하는 인자를 해시에서 제외한다.
#   버전 값을 넘길 땐 절대 밑줄을 붙이지 말 것(캐시 키가 죽는다).
@st.cache_data(ttl=900, max_entries=8, show_spinner="매출을 집계하는 중이에요…")
def _titles(brand, start, end, rate_key, fx_key):
    # 종료일 환율은 캐시에 없으면 조회를 시도한다(최대 수십 초). 캐시가 900초라
    # 실무자가 기다리는 건 기간을 처음 바꿀 때 한 번뿐이다.
    # fx_key = 공식 환율 저장 파일 mtime — 환율을 올리면 캐시가 자동으로 풀린다.
    rates, eff, src = sm.load_rates(end)
    return sm.title_revenue(brand, start, end, rates), eff, src


def load_titles(brand, start, end):
    return _titles(brand, start, end, end, fx.version())


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

_, _eff, _src = load_titles("photoism", S, E)
# 서울외국환중개 값이면 초록, 폴백이면 주황.
_official = _src != fx.SRC_FALLBACK
_chip = "st-ok" if _official else "st-warn"
c3.markdown(
    f"<div style='padding-top:26px;font-size:12.5px;color:var(--text-2)'>"
    f"환율 기준일 <b>{_eff or '—'}</b>"
    f"<span class='st-chip {_chip}'>{_src}</span>"
    f"<div style='color:var(--text-3);font-size:11.5px;margin-top:2px'>"
    f"종료일 당일, 휴장일이면 직전 영업일</div></div>",
    unsafe_allow_html=True)

# 서울외국환중개는 공개 API 가 없지만 페이지에 날짜 검색 폼이 있어 과거도 조회된다.
# 조회가 막혔을 때만(사이트 점검·차단 등) 수동 업로드로 메운다.
if not _official:
    with st.expander("💱 서울외국환중개 공식 환율 올리기 "
                     "— 지금은 참고 환율을 쓰고 있어요", expanded=True):
        st.caption("서울외국환중개 조회에 실패했어요. `TodayExRate.xls` 를 올리면 "
                   f"{_eff} 기준 공식 환율로 정산해요.")
        up = st.file_uploader("TodayExRate.xls", type=["xls", "xlsx", "csv", "html"],
                              key="fxup", disabled=not CAN_EDIT)
        if up is not None:
            try:
                parsed, ref = fx.parse_upload(up.getvalue(), up.name)
                st.success(f"{len(parsed) - 1}개 통화를 읽었어요"
                           + (f" · 파일 기준일 {ref}" if ref else ""))
                st.dataframe([{"통화": k, "원화": v} for k, v in
                              sorted(parsed.items()) if k != "KRW"],
                             hide_index=True, use_container_width=True)
                if ref and ref != _eff:
                    st.warning(f"파일 기준일({ref})이 정산 기준일({_eff})과 달라요. "
                               "그래도 저장하면 정산 기준일 환율로 씁니다.")
                if st.button("💾 이 환율로 저장", disabled=not CAN_EDIT):
                    fx.save(_eff, parsed, _email, memo=up.name)
                    auth.log_event(_email, f"settlefx:{_eff}")
                    st.cache_data.clear()
                    st.rerun()
            except Exception as e:  # noqa: BLE001
                st.error(f"읽지 못했어요 — {e}")

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
    df, _, _ = load_titles(brand, S, E)
    if df.empty:
        st.info("이 기간에 정산 대상 매출이 없어요.")
        return
    ann = sm.annotate(df, brand, S, E)
    r = sm.residual(ann)

    # ── 잔여 검증 ─────────────────────────────────────────────────────
    if r["잔여매출"] == 0:
        ui_theme.nbox("ok", f"✅ <b>잔여 매출 0원</b> — 모든 타이틀이 확정됐어요. "
                            f"정산서를 낼 수 있어요."
                            f"<div class='sub'>확정 {r['확정건']}건 · "
                            f"총 {_fmt(r['총매출'])}원</div>")
    else:
        ui_theme.nbox("warn",
                      f"미확정 매출 <span class='big'>{_fmt(r['잔여매출'])}원</span> "
                      f"· 전체의 {r['잔여비중']:.1f}%"
                      f"<div class='sub'>남은 타이틀 {r['잔여건']}개 · "
                      f"확정 {r['확정건']}개 · 총 {_fmt(r['총매출'])}원 — "
                      f"0원이 되면 발행할 수 있어요</div>")

    # ── 진행 현황 ─────────────────────────────────────────────────────
    ui_theme.kpis([
        ui_theme.kpi(f"{STATE_ICON[s]} {s}", f"{len(ann[ann['상태'] == s])}건",
                     f"{_fmt(ann[ann['상태'] == s]['매출액'].sum())}원",
                     hero=(s == "확정"))
        for s in ["확정", "선택필요", "확인필요", "미연결", "제외"]
    ], cls="k5")

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
    """확정된 티켓 중에서 정산 대상을 고른다.

    ★여러 개 고를 수 있다 — 한 IP를 회차별로 여러 티켓에 나눠 등록하는 경우가 있고,
      그때는 합쳐서 한 장으로 정산해야 한다. 고른 티켓들의 타이틀은 전부 합친다.
    """
    tks = sc.confirmed_tickets(brand)
    if not tks:
        return [], []
    def _lab(k):
        # 모르는 키가 들어와도 죽지 않게 — 매핑이 그 사이 바뀌었을 수 있다.
        v = tks.get(k)
        if not v:
            return str(k)
        return (f"{k} · {' / '.join(v[:2])}"
                + (f" 외 {len(v) - 2}" if len(v) > 2 else ""))
    sel = st.multiselect(f"{sm.BRAND_LABEL[brand]} 티켓", list(tks),
                         format_func=_lab, key=f"pk_{brand}")
    titles = []
    for k in sel:
        titles += [t for t in tks[k] if t not in titles]
    return sel, titles


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

    rates, eff, src = sm.load_rates(E)
    total_base = total_a = total_m = 0
    details, pivots, warns, miss = {}, {}, [], []

    for b, (tks, titles) in picks.items():
        if not tks:
            continue
        with ui_theme.card(f"{'📸' if b == 'photoism' else '📊'} "
                           f"{sm.BRAND_LABEL[b]} · {len(tks)}개 티켓"):
            st.caption("티켓: " + " · ".join(tks))
            st.caption(f"타이틀 {len(titles)}개 — " + " / ".join(titles[:6])
                       + (f" 외 {len(titles) - 6}" if len(titles) > 6 else ""))
            # 요율·MG 는 티켓마다 다를 수 있어 각각 받는다.
            # 여러 장이면 매출 비중이 큰 티켓의 요율을 대표로 쓰되 화면에 다 보여준다.
            rates_seen = []
            for tk in tks:
                if len(tks) > 1:
                    st.markdown(f"**`{tk}`**")
                rates_seen.append(_rate_input(b, tk))
                _mg_input(b, tk)
            # 요율이 티켓마다 다르면 정산액이 갈리므로 그냥 넘어가면 안 된다.
            uniq = {r for r in rates_seen if any(x is not None for x in r)}
            if len(uniq) > 1:
                st.warning("고른 티켓들의 요율이 서로 달라요. 같은 문서로 묶으면 "
                           "어느 요율을 쓸지 모호해집니다 — 요율을 맞추거나 "
                           "티켓을 나눠서 발행해 주세요.")
            ra, rm = rates_seen[0] if rates_seen else (None, None)

            d = sc.country_detail(b, titles, S, E, rates)
            d = sc.fill_open(d, sc.open_countries(b, S, E))
            details[b] = d
            pivots[b] = sc.member_pivot(b, titles, S, E, rates)
            warns += sc.verify(d[d["매출액"] > 0], rates)
            miss += fx.missing(rates, d["unit"])

            base = int(d["매출액"].sum())
            total_base += base
            total_a += round(base * ra) if ra else 0
            total_m += round(base * rm) if rm else 0

            qty = "프레임수" if b == "photoism" else "건수"
            ui_theme.kpis([
                ui_theme.kpi("매출(KRW)", f"{_fmt(base)}원", hero=True),
                ui_theme.kpi(qty, _fmt(d["수량"].sum())),
                ui_theme.kpi("소속사", f"{_fmt(round(base * ra))}원" if ra else "—",
                             f"요율 {ra * 100:.1f}%" if ra else "요율 없음"),
                ui_theme.kpi("대행사", f"{_fmt(round(base * rm))}원" if rm else "—",
                             f"요율 {rm * 100:.1f}%" if rm else "요율 없음"),
            ], cls="k4")
            with st.expander(f"국가별 내역 · 오픈 {len(d)}개국 중 "
                             f"매출발생 {int((d['매출액'] > 0).sum())}개국"):
                st.dataframe(d[["국가", "unit", "수량", "현지", "매출액"]],
                             hide_index=True, use_container_width=True)

    # ── 합산 ──────────────────────────────────────────────────────────
    ui_theme.sec("합", "합산", f"환율 기준일 {eff or '—'} · {src}")
    ui_theme.kpis([
        ui_theme.kpi("정산기준액", f"{_fmt(total_base)}원", "포토이즘 + 스내피즘", hero=True),
        ui_theme.kpi("소속사 정산액", f"{_fmt(total_a)}원"),
        ui_theme.kpi("대행사 정산액", f"{_fmt(total_m)}원"),
    ], cls="k3")

    # ── 검증 ──────────────────────────────────────────────────────────
    miss = sorted(set(miss))
    if miss:
        ui_theme.nbox("warn",
                      f"⚠️ <b>환율이 없는 통화 {', '.join(miss)}</b> — 그대로 두면 "
                      f"1:1 로 계산돼 금액이 크게 부풀어요."
                      "<div class='sub'>서울외국환중개가 고시하지 않는 통화예요. "
                      "공식 환율 파일을 올리거나 해당 국가를 빼고 발행해 주세요.</div>")
    if warns:
        ui_theme.nbox("warn", "⚠️ <b>환율 검증 실패</b> — 현지 매출 × 환율이 "
                              "매출(KRW)과 안 맞아요.<div class='sub'>"
                      + "<br>".join(warns[:5]) + "</div>")
    else:
        ui_theme.nbox("ok", "✅ <b>검증 통과</b> — 모든 국가에서 "
                            "현지 매출 × 환율 = 매출(KRW)")

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

    # ── 발행 ──────────────────────────────────────────────────────────
    # 타이틀은 '260605 TREASURE' 처럼 날짜 접두가 붙어 있다. 문서에는 IP명만 쓴다.
    _t = (picks["photoism"][1] or picks["snapism"][1] or [""])[0]
    ui_theme.sec("발", "정산서 발행")
    ip = st.text_input("정산서에 표기할 IP명", key="ipname",
                       value=re.sub(r"^\s*\d{5,8}\s*", "", str(_t)).strip())
    ipn = ip.strip()

    # 같은 IP·기간을 다시 내면 정정본이다. 몇 번째가 되는지 먼저 알려준다.
    nextv = sc.issue_version(ipn, S, E) if ipn else 1
    reason = ""
    if nextv > 1:
        hist0 = sc.list_issues(ipn, S, E)
        ui_theme.nbox("warn",
                      f"이 건은 이미 <b>{nextv - 1}번</b> 발행됐어요. 다시 만들면 "
                      f"<b>정정본 v{nextv}</b> 가 돼요."
                      f"<div class='sub'>최근 발행 {hist0[0]['at'][:16]} · "
                      f"{hist0[0]['by']}</div>")
        reason = st.text_input("정정 사유 (문서 첫 장에 표기돼요)", key="reason",
                               placeholder="예: 대만 취소분 반영")

    blockers = []
    if warns:
        blockers.append("환율 검증 실패")
    if not any((r or {}).get("agency") or (r or {}).get("mgmt")
               for r in (sc.get_rs(b, t) for b, (tl, _) in picks.items()
                         for t in tl)):
        blockers.append("요율 없음")
    if not ipn:
        blockers.append("IP명 미입력")
    if nextv > 1 and not reason.strip():
        blockers.append("정정 사유 미입력")
    if miss:
        blockers.append(f"환율 없는 통화({', '.join(miss)})")
    if blockers:
        st.warning("발행 전에 정리할 게 있어요 — " + " · ".join(blockers))

    if st.button("📄 정산서 만들기", type="primary",
                 disabled=not CAN_EDIT or bool(blockers)):
        with st.spinner("PDF 를 만드는 중이에요…"):
            ctx = sc.build_context({b: tl for b, (tl, _) in picks.items()},
                                   S, E, ipn, rates, eff or E,
                                   date.today().isoformat(), src)
            # ★스냅샷을 먼저 남긴다. 매출 데이터는 매일 바뀌고 취소도 뒤늦게 붙으므로
            #   발행 시점 값을 얼려두지 않으면 보낸 문서를 다시 뽑을 수 없다.
            rec = sc.record_issue(ctx, _email, reason.strip())
            ctx["version"], ctx["reason"] = rec["version"], reason.strip()
            made = {}
            for kind, lab in (("agency", "소속사"), ("mgmt", "대행사")):
                key = "agency" if kind == "agency" else "mgmt"
                if not any((ctx["rs"].get(b) or {}).get(key) for b in ctx["details"]):
                    continue    # 그 수취처 요율이 없으면 문서를 만들지 않는다
                made[lab] = sp.render_pdf(
                    sp.build_html(ctx, kind),
                    f"IP 정산서({lab}) · {ipn} · {S}~{E}")
            st.session_state["_pdfs"] = made
            st.session_state["_pdf_meta"] = {"ip": ipn, "v": rec["version"],
                                             "reason": reason.strip()}
            auth.log_event(_email, f"settleissue:{ipn} v{rec['version']}")
        _rerun()

    pdfs = st.session_state.get("_pdfs") or {}
    meta = st.session_state.get("_pdf_meta") or {}
    if pdfs:
        ui_theme.nbox("ok",
                      f"✅ <b>{meta.get('ip')}</b> 정산서 {len(pdfs)}부를 만들었어요"
                      + (f" · 정정본 v{meta.get('v')}" if meta.get("v", 1) > 1 else "")
                      + f"<div class='sub'>{' · '.join(pdfs)}</div>")
        cols = st.columns(len(pdfs))
        for c, (lab, data) in zip(cols, pdfs.items()):
            c.download_button(
                f"⬇️ {lab} 정산서", data,
                file_name=f"정산서_{meta.get('ip', 'IP')}_{S[:7]}_{lab}.pdf",
                mime="application/pdf", key=f"dl_{lab}",
                use_container_width=True)

        # ── 메일 발송 ─────────────────────────────────────────────────
        # 정산서를 만든 뒤에만 보낼 수 있다. 수신자는 화면에서 직접 받는다 —
        # 금액이 담긴 대외 문서라 고정 목록으로 자동 발송하면 안 된다.
        ui_theme.sec("메", "메일 발송", "방금 만든 정산서를 원하는 사람에게 보내요")
        ok, who = smail.config_ready()
        if not ok:
            st.info(f"메일 설정이 아직이에요 — {who}")
        else:
            st.caption(f"보내는 사람 · {who}")
            m1, m2 = st.columns(2)
            to_raw = m1.text_input("받는 사람", key="mail_to",
                                   placeholder="a@b.com, c@d.com")
            cc_raw = m2.text_input("참조", key="mail_cc", placeholder="선택")
            picked = st.multiselect("첨부할 정산서", list(pdfs),
                                    default=list(pdfs), key="mail_files")
            note = st.text_area("덧붙일 말 (선택)", key="mail_note", height=80)

            to = [x.strip() for x in to_raw.replace(";", ",").split(",") if "@" in x]
            cc = [x.strip() for x in cc_raw.replace(";", ",").split(",") if "@" in x]
            dup = smail.already_sent(meta.get("ip", ""), S, E, meta.get("v", 1))
            if dup:
                st.warning(f"이 건은 {dup['at'][:16]} 에 이미 보냈어요 "
                           f"({', '.join(dup['to'])}). 다시 보내면 중복이에요.")

            b1, b2 = st.columns(2)
            if b1.button("👀 미리보기", use_container_width=True,
                         disabled=not (to and picked)):
                msg = smail.build_message(
                    meta.get("ip", ""), S, E, {k: pdfs[k] for k in picked},
                    to, cc, meta.get("v", 1), meta.get("reason", ""), note)
                st.code(f"제목: {msg['Subject']}\n"
                        f"받는 사람: {msg['To']}\n"
                        f"참조: {msg.get('Cc') or '-'}\n"
                        f"첨부: {', '.join(picked)}\n\n"
                        + msg.get_body().get_content(), language=None)

            if b2.button("📧 보내기", type="primary", use_container_width=True,
                         disabled=not (CAN_EDIT and to and picked)):
                try:
                    msg = smail.build_message(
                        meta.get("ip", ""), S, E, {k: pdfs[k] for k in picked},
                        to, cc, meta.get("v", 1), meta.get("reason", ""), note)
                    smail.send(msg, to, cc)
                    smail.log_sent(meta.get("ip", ""), S, E, to, cc,
                                   meta.get("v", 1), _email, picked)
                    auth.log_event(_email,
                                   f"settlemail:{meta.get('ip')}→{len(to)}명")
                    st.success(f"보냈어요 — {', '.join(to)}")
                except Exception as e:                      # noqa: BLE001
                    st.error(f"발송 실패: {e}")

    # ── 이력 ──────────────────────────────────────────────────────────
    hist = sc.list_issues()
    if hist:
        with st.expander(f"🗂 발행 이력 ({len(hist)}건)"):
            st.dataframe(
                [{"IP·기간": h["key"].replace("|", " · "), "버전": h["version"],
                  "발행일시": h["at"][:16], "발행자": h["by"],
                  "정정사유": h["reason"] or "—"} for h in hist[:50]],
                hide_index=True, use_container_width=True)
    sent = smail.sent_log()
    if sent:
        with st.expander(f"📧 발송 이력 ({len(sent)}건)"):
            st.dataframe(
                [{"IP": r["ip"], "기간": f"{r['start']}~{r['end']}",
                  "버전": r["version"], "받는 사람": ", ".join(r["to"]),
                  "보낸일시": r["at"][:16], "보낸이": r["by"]} for r in sent],
                hide_index=True, use_container_width=True)


t1, t2 = st.tabs(["🔗 티켓 매핑", "🧮 정산 계산"])
with t1:
    tabs = st.tabs(["📸 포토이즘", "📊 스내피즘"])
    for tab, b in zip(tabs, sm.BRANDS):
        with tab:
            brand_panel(b)
with t2:
    calc_panel()
