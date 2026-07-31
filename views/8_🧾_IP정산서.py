# -*- coding: utf-8 -*-
"""IP 정산서.

화면은 **티켓번호 하나로 끝나게** 짰다.
  ① 티켓번호 넣기 → ② 찾아온 타이틀 확인 → ③ 요율 확인 → ④ 만들기

한 IP 정산에 464개 대기열을 먼저 다 처리하라고 요구하면 실무자가 납득하기 어렵다.
전체 매출이 빠짐없이 귀속됐는지 보는 '월 마감 점검' 은 별도 탭으로 분리했다.

정산은 지라 티켓번호를 정(正)으로 한다. 자동 매칭은 **후보 제안까지만** —
동점일 때 캐시 순서로 승자가 갈려 재크롤하면 결과가 바뀌기 때문이다.
확정은 사람이 체크한 것만 인정한다.

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
import settlement_mail as smail
import settlement_map as sm
import settlement_pdf as sp
import ui_theme

# 금액을 다루는 페이지다. 사이드바에서 이미 걸러지지만 url 직접 입력도 막는다.
_email = (st.user.email or "").strip().lower() if getattr(st, "user", None) else ""
if not auth.can_view_page(_email, "settledoc"):
    st.error("🔒 이 페이지에 접근할 권한이 없어요. 필요하면 관리자에게 요청해 주세요.")
    st.stop()

CAN_EDIT = auth.can_edit(_email)

ui_theme.inject()
st.markdown('<div class="sechd"><span class="secn">🧾</span>'
            '<span class="sect">IP 정산서</span></div>'
            '<div class="secq">티켓번호를 넣으면 해당 타이틀과 매출을 알아서 모아 와요. '
            '확인하고 만들기만 누르면 돼요.</div>', unsafe_allow_html=True)


def _fmt(v):
    try:
        return f"{int(v):,}"
    except (TypeError, ValueError):
        return "—"


def _rerun():
    """`scope="fragment"` 는 프래그먼트 재실행 중에만 유효하다.
    전체 스크립트가 도는 중 버튼이 눌리면 예외가 나므로 전체 rerun 으로 떨어진다."""
    try:
        st.rerun(scope="fragment")
    except StreamlitAPIException:
        st.rerun()


# ── 공통 데이터 ────────────────────────────────────────────────────────────
# ★st.cache_data 는 밑줄로 시작하는 인자를 해시에서 제외한다. 버전 값에 밑줄 금지.
@st.cache_data(ttl=900, max_entries=8, show_spinner="매출을 집계하는 중이에요…")
def _titles(brand, start, end, rate_key, fx_key):
    rates, eff, src = sm.load_rates(end)
    return sm.title_revenue(brand, start, end, rates), eff, src


def load_titles(brand, start, end):
    return _titles(brand, start, end, end, fx.version())


# ── 기간 ─────────────────────────────────────────────────────────────────
_today = date.today()
_dflt_end = _today.replace(day=1) - timedelta(days=1)      # 지난달 말일
_dflt_start = _dflt_end.replace(day=1)

c1, c2, c3 = st.columns([1.1, 1.1, 2.4])
start = c1.date_input("정산 시작", _dflt_start, format="YYYY-MM-DD")
end = c2.date_input("정산 종료", _dflt_end, format="YYYY-MM-DD")
if start > end:
    st.error("시작일이 종료일보다 늦어요.")
    st.stop()
S, E = start.isoformat(), end.isoformat()

RATES, EFF, SRC = sm.load_rates(E)
_official = SRC != fx.SRC_FALLBACK
c3.markdown(
    f"<div style='padding-top:26px;font-size:12.5px;color:var(--text-2)'>"
    f"환율 기준일 <b>{EFF or '—'}</b>"
    f"<span class='st-chip {'st-ok' if _official else 'st-warn'}'>{SRC}</span>"
    f"<div style='color:var(--text-3);font-size:11.5px;margin-top:2px'>"
    f"종료일 당일, 휴장일이면 직전 영업일</div></div>", unsafe_allow_html=True)

if not _official:
    with st.expander("💱 서울외국환중개 환율 조회에 실패했어요 — 파일로 올리기",
                     expanded=True):
        st.caption(f"`TodayExRate.xls` 를 올리면 {EFF} 기준 공식 환율로 정산해요.")
        up = st.file_uploader("TodayExRate.xls", type=["xls", "xlsx", "csv", "html"],
                              key="fxup", disabled=not CAN_EDIT)
        if up is not None:
            try:
                parsed, ref = fx.parse_upload(up.getvalue(), up.name)
                st.success(f"{len(parsed) - 1}개 통화를 읽었어요"
                           + (f" · 파일 기준일 {ref}" if ref else ""))
                if st.button("💾 이 환율로 저장", disabled=not CAN_EDIT):
                    fx.save(EFF, parsed, _email, memo=up.name)
                    auth.log_event(_email, f"settlefx:{EFF}")
                    st.cache_data.clear()
                    st.rerun()
            except Exception as e:                          # noqa: BLE001
                st.error(f"읽지 못했어요 — {e}")

if not CAN_EDIT:
    st.info("🔎 보기 전용이에요. 확정·발행은 편집 권한이 있어야 해요.")


# ══════════════════════════════════════════════════════════════════════════
# 정산서 만들기
# ══════════════════════════════════════════════════════════════════════════
def _ticket_box(brand: str):
    """티켓번호 입력 + 찾은 타이틀 확인. 반환: 확정에 쓸 (티켓, [타이틀])."""
    icon = "📸" if brand == "photoism" else "📊"
    key = f"tk_{brand}"
    st.markdown(f"**{icon} {sm.BRAND_LABEL[brand]}**")
    tk = st.text_input("티켓번호", key=key, placeholder="CANDIP-12345",
                       label_visibility="collapsed").strip().upper()

    with st.popover("🔍 번호를 모르겠어요", use_container_width=True):
        q = st.text_input("IP명 일부", key=f"q_{brand}", placeholder="예: TREASURE")
        if q.strip():
            found = sc.find_tickets(brand, q)
            if not found:
                st.caption("못 찾았어요.")
            for f in found[:8]:
                st.markdown(f"`{f['ticket']}` · {' / '.join(f['titles'][:2])}  \n"
                            f"<span class='muted'>{f['start'] or '?'} ~ "
                            f"{f['due'] or '?'}</span>", unsafe_allow_html=True)

    if not tk:
        return None, []

    info = sm.lookup_ticket(brand, tk)
    if not info:
        st.error("그 번호를 못 찾았어요. 번호를 확인해 주세요.")
        return None, []
    st.caption(f"📌 {' / '.join(info['titles'][:3])} · "
               f"{info.get('startdate') or '?'} ~ {info.get('duedate') or '?'}")

    sug = sc.suggest_titles(brand, tk, S, E, RATES)
    if sug.empty:
        st.warning("이 기간에 이 티켓으로 잡히는 매출이 없어요.")
        return tk, []

    chosen = []
    for _, r in sug.iterrows():
        t = r["타이틀"]
        done = r["상태"] == "확정"
        on = st.checkbox(
            f"{t} · {_fmt(r['매출액'])}원 · {r['국가수']}개국"
            + ("  ✅" if done else ""),
            value=True, key=f"ck_{brand}_{t}", disabled=not CAN_EDIT)
        if on:
            chosen.append(t)
    return tk, chosen


@st.fragment
def make_panel():
    picks = {}
    cols = st.columns(2)
    for col, b in zip(cols, sm.BRANDS):
        with col:
            picks[b] = _ticket_box(b)

    if not any(t for t, _ in picks.values()):
        st.info("위에 티켓번호를 넣어 주세요. 포토이즘·스내피즘 중 한쪽만 있어도 돼요.")
        return

    # ── 확정 저장 ─────────────────────────────────────────────────────
    # 체크한 타이틀을 그 티켓에 확정한다. 여기서 저장해야 다음 달에도 자동 적용된다.
    need_save = []
    for b, (tk, titles) in picks.items():
        if not tk:
            continue
        cur = set(sc.titles_for_ticket(b, tk))
        if set(titles) != cur:
            need_save.append((b, tk, titles))
    if need_save and CAN_EDIT:
        n = sum(len(t) for _, _, t in need_save)
        if st.button(f"✔️ 위 구성으로 확정 ({n}개 타이틀)", use_container_width=True):
            for b, tk, titles in need_save:
                for t in set(sc.titles_for_ticket(b, tk)) - set(titles):
                    sm.unapprove(b, t)
                for t in titles:
                    sm.approve(b, t, tk, _email)
                auth.log_event(_email, f"settlemap:{b}:{tk}({len(titles)})")
            _rerun()
        st.caption("확정하면 다음 달부터 같은 티켓에 자동으로 붙어요.")

    ui_theme.sec(2, "요율 확인", "지라에 있으면 자동으로 채워요")
    rs = {}
    for b, (tk, titles) in picks.items():
        if not tk or not titles:
            continue
        cur = sc.get_rs(b, tk)
        tag = {"화면 입력": "🖊 저장된 값", "지라": "🔗 지라",
               "없음": "⚠️ 없음 — 직접 넣어 주세요"}[cur["source"]]
        st.markdown(f"**{sm.BRAND_LABEL[b]}** `{tk}` <span class='muted'>{tag}</span>",
                    unsafe_allow_html=True)
        k1, k2, k3, k4 = st.columns([1, 1, 1, 1.2])
        a = k1.number_input("소속사 %", 0.0, 100.0, float((cur["agency"] or 0) * 100),
                            0.5, key=f"ra_{b}")
        m = k2.number_input("대행사 %", 0.0, 100.0, float((cur["mgmt"] or 0) * 100),
                            0.5, key=f"rm_{b}")
        mg_cur = sc.get_mg(b, tk)
        has = k3.checkbox("MG 있음", value=mg_cur["has_mg"], key=f"mgh_{b}")
        amt = k4.number_input("MG 금액", 0, step=1_000_000,
                              value=int(mg_cur["amount"] or 0), disabled=not has,
                              key=f"mga_{b}")
        # 파트너사명 — 문서에 '제출처 ○○ 귀중' 과 정산 내역표 출자자명으로 들어간다.
        pt = sc.get_partner(b, tk)
        p1, p2, p3 = st.columns([1.3, 1.3, 0.9])
        an = p1.text_input("소속사명", value=pt["agency_name"], key=f"pa_{b}",
                           placeholder="예: 제이와이드컴퍼니")
        mn = p2.text_input("대행사명", value=pt["mgmt_name"], key=f"pm_{b}",
                           placeholder="선택")
        vt = p3.checkbox("부가세 적용", value=pt["vat"], key=f"vt_{b}",
                         help="총 지급액을 부가세 포함으로 보고 공급가액·VAT 를 나눠 적어요. "
                              "해외 파트너면 꺼 주세요.")
        if st.button(f"💾 {sm.BRAND_LABEL[b]} 저장", key=f"sv_{b}",
                     disabled=not CAN_EDIT):
            sc.set_rs(b, tk, a / 100 or None, m / 100 or None, _email)
            sc.set_mg(b, tk, has, amt, mg_cur.get("note", ""), _email)
            sc.set_partner(b, tk, an, mn, vt, _email)
            _rerun()
        rs[b] = (a / 100 or None, m / 100 or None)

    # ── 미리보기 ──────────────────────────────────────────────────────
    ui_theme.sec(3, "금액 확인")
    tot_base = tot_a = tot_m = 0
    warns, miss, shown = [], [], []
    for b, (tk, titles) in picks.items():
        if not tk or not titles:
            continue
        d = sc.fill_open(sc.country_detail(b, titles, S, E, RATES),
                         sc.open_countries(b, S, E))
        if d.empty:                 # 그 기간 매출 행이 없으면 문서에도 안 들어간다
            continue
        shown.append(b)
        warns += sc.verify(d[d["매출액"] > 0], RATES)
        miss += fx.missing(RATES, d["unit"])
        base = int(d["매출액"].sum())
        ra, rm = rs.get(b, (None, None))
        tot_base += base
        tot_a += round(base * ra) if ra else 0
        tot_m += round(base * rm) if rm else 0
        qty = "건수"          # 정산서와 같은 표기로 통일(브랜드 구분 없음)
        with st.expander(f"{sm.BRAND_LABEL[b]} · {_fmt(base)}원 · "
                         f"{qty} {_fmt(d['수량'].sum())} · "
                         f"매출발생 {int((d['매출액'] > 0).sum())}개국"):
            st.dataframe(d[["국가", "unit", "수량", "현지", "매출액"]],
                         hide_index=True, use_container_width=True)

    # ★한 브랜드만 정산하는 경우가 흔하다. 없는 브랜드를 적으면 안 된다.
    #   티켓을 골랐는지가 아니라 **매출 행이 있는지**로 판단한다(문서와 같은 기준).
    used = " + ".join(sm.BRAND_LABEL[b] for b in sm.BRANDS if b in shown)
    ui_theme.kpis([
        ui_theme.kpi("정산기준액", f"{_fmt(tot_base)}원", used, hero=True),
        ui_theme.kpi("소속사 정산액", f"{_fmt(tot_a)}원"),
        ui_theme.kpi("대행사 정산액", f"{_fmt(tot_m)}원"),
    ], cls="k3")

    # 세금계산서용 분해 — 문서 하단 표와 같은 값을 미리 보여준다.
    _pt = next((sc.get_partner(b, t) for b, (t, _) in picks.items() if t), None)
    if _pt and _pt["vat"] and (tot_a or tot_m):
        rows = []
        for lab, v in (("소속사", tot_a), ("대행사", tot_m)):
            if not v:
                continue
            sup, vat = sc.vat_split(v, True)
            rows.append(f"{lab} {_fmt(v)}원 = 공급가액 {_fmt(sup)} + 부가세 {_fmt(vat)}")
        if rows:
            st.caption("💳 " + " · ".join(rows))

    miss = sorted(set(miss))
    if miss:
        ui_theme.nbox("warn", f"⚠️ <b>환율이 없는 통화 {', '.join(miss)}</b> — "
                              "그대로 두면 1:1 로 계산돼 금액이 크게 부풀어요.")
    if warns:
        ui_theme.nbox("warn", "⚠️ <b>환율 검증 실패</b><div class='sub'>"
                      + "<br>".join(warns[:4]) + "</div>")

    # ── 만들기 ────────────────────────────────────────────────────────
    ui_theme.sec(4, "정산서 만들기")
    _t = next((t[0] for _, t in picks.values() if t), "")
    ip = st.text_input("정산서에 표기할 IP명", key="ipname",
                       value=re.sub(r"^\s*\d{5,8}\s*", "", str(_t)).strip())
    ipn = ip.strip()

    nextv = sc.issue_version(ipn, S, E) if ipn else 1
    reason = ""
    if nextv > 1:
        h0 = sc.list_issues(ipn, S, E)
        ui_theme.nbox("warn", f"이미 <b>{nextv - 1}번</b> 발행됐어요. 다시 만들면 "
                              f"<b>정정본 v{nextv}</b> 가 돼요."
                              f"<div class='sub'>최근 {h0[0]['at'][:16]} · "
                              f"{h0[0]['by']}</div>")
        reason = st.text_input("정정 사유 (문서 첫 장에 표기돼요)", key="reason")

    stop = []
    if warns:
        stop.append("환율 검증 실패")
    if miss:
        stop.append(f"환율 없는 통화({', '.join(miss)})")
    if not any(v for pair in rs.values() for v in pair):
        stop.append("요율 없음")
    if not ipn:
        stop.append("IP명 미입력")
    if nextv > 1 and not reason.strip():
        stop.append("정정 사유 미입력")
    if need_save:
        stop.append("타이틀 확정 필요")
    if stop:
        st.warning("먼저 정리할 게 있어요 — " + " · ".join(stop))

    if st.button("📄 정산서 만들기", type="primary", use_container_width=True,
                 disabled=not CAN_EDIT or bool(stop)):
        with st.spinner("PDF 를 만드는 중이에요…"):
            ctx = sc.build_context({b: t for b, (t, _) in picks.items() if t},
                                   S, E, ipn, RATES, EFF or E,
                                   date.today().isoformat(), SRC)
            # ★스냅샷을 먼저 남긴다. 매출은 매일 갱신되고 취소도 뒤늦게 붙어서,
            #   얼려두지 않으면 보낸 문서를 다시 뽑을 수 없다.
            rec = sc.record_issue(ctx, _email, reason.strip())
            ctx["version"], ctx["reason"] = rec["version"], reason.strip()
            made = {}
            for kind, lab in (("agency", "소속사"), ("mgmt", "대행사")):
                fld = "agency" if kind == "agency" else "mgmt"
                if not any((ctx["rs"].get(b) or {}).get(fld) for b in ctx["details"]):
                    continue        # 요율 없는 수취처는 문서를 만들지 않는다
                made[lab] = sp.render_pdf(sp.build_html(ctx, kind),
                                          f"IP 정산서({lab}) · {ipn} · {S}~{E}")
            st.session_state["_pdfs"] = made
            st.session_state["_meta"] = {"ip": ipn, "v": rec["version"],
                                         "reason": reason.strip(),
                                         # ★티켓을 고른 브랜드가 아니라 **문서에
                                         #   실제로 들어간** 브랜드를 적는다.
                                         "brands": sp.brands_label(ctx)}
            auth.log_event(_email, f"settleissue:{ipn} v{rec['version']}")
        _rerun()

    _deliver()


def _deliver():
    """만든 뒤 — 내려받기 / 메일. 만들기 전에는 아무것도 안 보인다."""
    pdfs = st.session_state.get("_pdfs") or {}
    meta = st.session_state.get("_meta") or {}
    if not pdfs:
        return
    ui_theme.nbox("ok", f"✅ <b>{meta.get('ip')}</b> 정산서 {len(pdfs)}부 완성"
                  + (f" · 정정본 v{meta['v']}" if meta.get("v", 1) > 1 else "")
                  + f"<div class='sub'>{' · '.join(pdfs)}</div>")
    cols = st.columns(len(pdfs))
    for c, (lab, data) in zip(cols, pdfs.items()):
        c.download_button(f"⬇️ {lab} 정산서", data, key=f"dl_{lab}",
                          file_name=f"정산서_{meta.get('ip','IP')}_{S[:7]}_{lab}.pdf",
                          mime="application/pdf", use_container_width=True)

    ui_theme.sec(5, "메일 보내기", "원하는 사람에게 바로 보낼 수 있어요")
    ok, who = smail.config_ready()
    if not ok:
        st.info(f"메일 설정이 아직이에요 — {who}")
        return
    st.caption(f"보내는 사람 · {who}")
    m1, m2 = st.columns(2)
    to = [x.strip() for x in m1.text_input(
        "받는 사람", key="mail_to", placeholder="a@b.com, c@d.com"
    ).replace(";", ",").split(",") if "@" in x]
    cc = [x.strip() for x in m2.text_input(
        "참조", key="mail_cc", placeholder="선택"
    ).replace(";", ",").split(",") if "@" in x]
    picked = st.multiselect("첨부", list(pdfs), default=list(pdfs), key="mail_files")
    note = st.text_area("덧붙일 말 (선택)", key="mail_note", height=80)

    dup = smail.already_sent(meta.get("ip", ""), S, E, meta.get("v", 1))
    if dup:
        st.warning(f"{dup['at'][:16]} 에 이미 보냈어요 ({', '.join(dup['to'])}).")

    b1, b2 = st.columns(2)
    if b1.button("👀 미리보기", use_container_width=True,
                 disabled=not (to and picked)):
        msg = smail.build_message(meta.get("ip", ""), S, E,
                                  {k: pdfs[k] for k in picked}, to, cc,
                                  meta.get("v", 1), meta.get("reason", ""), note,
                                  meta.get("brands", ""))
        st.code(f"제목: {msg['Subject']}\n받는 사람: {msg['To']}\n"
                f"참조: {msg.get('Cc') or '-'}\n첨부: {', '.join(picked)}\n\n"
                + msg.get_body().get_content(), language=None)
    if b2.button("📧 보내기", type="primary", use_container_width=True,
                 disabled=not (CAN_EDIT and to and picked)):
        try:
            msg = smail.build_message(meta.get("ip", ""), S, E,
                                      {k: pdfs[k] for k in picked}, to, cc,
                                      meta.get("v", 1), meta.get("reason", ""), note,
                                      meta.get("brands", ""))
            smail.send(msg, to, cc)
            smail.log_sent(meta.get("ip", ""), S, E, to, cc, meta.get("v", 1),
                           _email, picked)
            auth.log_event(_email, f"settlemail:{meta.get('ip')}→{len(to)}명")
            st.success(f"보냈어요 — {', '.join(to)}")
        except Exception as e:                                  # noqa: BLE001
            st.error(f"발송 실패: {e}")


# ══════════════════════════════════════════════════════════════════════════
# 월 마감 점검 — 전체 매출이 빠짐없이 귀속됐는지. 정산서를 만들 땐 필요 없다.
# ══════════════════════════════════════════════════════════════════════════
STATE_ICON = {"확정": "✅", "제외": "⛔", "선택필요": "🔀",
              "확인필요": "🟡", "미연결": "❓"}


@st.fragment
def audit_panel(brand: str):
    df, _, _ = load_titles(brand, S, E)
    if df.empty:
        st.info("이 기간에 정산 대상 매출이 없어요.")
        return
    ann = sm.annotate(df, brand, S, E)
    r = sm.residual(ann)
    if r["잔여매출"] == 0:
        ui_theme.nbox("ok", "✅ <b>잔여 매출 0원</b> — 모든 매출이 티켓에 귀속됐어요."
                            f"<div class='sub'>확정 {r['확정건']}건 · "
                            f"총 {_fmt(r['총매출'])}원</div>")
    else:
        ui_theme.nbox("warn",
                      f"어느 티켓에도 안 붙은 매출 "
                      f"<span class='big'>{_fmt(r['잔여매출'])}원</span> "
                      f"· 전체의 {r['잔여비중']:.1f}%"
                      f"<div class='sub'>타이틀 {r['잔여건']}개 · "
                      f"확정 {r['확정건']}개 — 월 마감 때 0원이 되면 좋아요</div>")

    ui_theme.kpis([
        ui_theme.kpi(f"{STATE_ICON[s]} {s}", f"{len(ann[ann['상태'] == s])}건",
                     f"{_fmt(ann[ann['상태'] == s]['매출액'].sum())}원",
                     hero=(s == "확정"))
        for s in ["확정", "선택필요", "확인필요", "미연결", "제외"]
    ], cls="k5")

    view = ann[~ann["상태"].isin(["확정", "제외"])].sort_values(
        "매출액", ascending=False)
    if view.empty:
        st.success("남은 게 없어요.")
        return
    cum = ann.sort_values("매출액", ascending=False)["매출액"].cumsum()
    n80 = int((cum <= ann["매출액"].sum() * 0.8).sum()) + 1
    st.caption(f"매출 큰 순서예요. 상위 **{n80}개**면 이 브랜드 매출의 80%가 잠겨요.")
    st.dataframe(view[["타이틀", "매출액", "상태", "후보수", "티켓"]].head(80),
                 hide_index=True, use_container_width=True)
    st.caption("여기 있는 타이틀은 **정산서 만들기** 탭에서 티켓번호를 넣으면 "
               "체크 한 번으로 확정할 수 있어요.")


t1, t2 = st.tabs(["🧾 정산서 만들기", "🗂 월 마감 점검"])
with t1:
    ui_theme.sec(1, "정산 대상", "티켓번호를 넣으면 타이틀과 매출을 모아 와요")
    make_panel()
    hist = sc.list_issues()
    if hist:
        with st.expander(f"🗂 발행 이력 ({len(hist)}건)"):
            st.dataframe([{"IP·기간": h["key"].replace("|", " · "),
                           "버전": h["version"], "발행일시": h["at"][:16],
                           "발행자": h["by"], "정정사유": h["reason"] or "—"}
                          for h in hist[:50]],
                         hide_index=True, use_container_width=True)
    sent = smail.sent_log()
    if sent:
        with st.expander(f"📧 발송 이력 ({len(sent)}건)"):
            st.dataframe([{"IP": r["ip"], "기간": f"{r['start']}~{r['end']}",
                           "버전": r["version"], "받는 사람": ", ".join(r["to"]),
                           "보낸일시": r["at"][:16], "보낸이": r["by"]}
                          for r in sent],
                         hide_index=True, use_container_width=True)
with t2:
    st.caption("전체 매출이 빠짐없이 티켓에 귀속됐는지 보는 화면이에요. "
               "정산서를 만들 때는 여기를 거치지 않아도 돼요.")
    for tab, b in zip(st.tabs(["📸 포토이즘", "📊 스내피즘"]), sm.BRANDS):
        with tab:
            audit_panel(b)
