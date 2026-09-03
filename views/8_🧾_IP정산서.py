# -*- coding: utf-8 -*-
"""IP 정산서.

화면은 **한 칸에 넣고 체크만 하면 끝나게** 짰다.
  ① 티켓번호 또는 IP명 넣기 → ② 붙일 타이틀 체크 → ③ 요율 확인 → ④ 만들기

★한 IP 가 티켓 여러 장으로 쪼개져 있는 일이 잦다(회차·전환·렌탈). 같이 정산하는
  건이면 **한 문서에 담아야** 하므로, 이름으로 찾아 필요한 티켓의 타이틀만 고른다.
  번호를 직접 적으면 그 티켓만(기본 선택), 이름으로 찾으면 관련 티켓 전부를
  펼쳐 주되 **기본 해제**다 — 렌탈처럼 따로 정산하는 건이 섞여 나오기 때문.

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
import theme_pick as tp  # 테마 축 · 멤버 축
import member_match as mm  # 멤버 한/영 로마자 추천
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
# ★★환율 조회를 캐시한다 (2026-08-19). `sm.load_rates` → `settlement_fx.resolve`
#   는 **저장된 공식환율이 있을 때만** 즉시 반환하고, 없으면 smbs.biz 를 매번
#   스크래핑한다(timeout 20초). 지금 `data/settlement_fx.json` 에 저장된 기준일이
#   0건이라 **전체 재실행마다 1.2~1.6초씩 네트워크를 물고 있었다**(실측).
#   모듈 최상단이라 위젯을 하나 건드릴 때마다 발생한다.
#   키에 fx.version() 을 넣어 공식환율을 저장하면 바로 반영되게 한다.
@st.cache_data(ttl=1800, max_entries=8, show_spinner=False)
def _rates(rate_date, fx_key):
    return sm.load_rates(rate_date)


@st.cache_data(ttl=900, max_entries=8, show_spinner="매출을 집계하는 중이에요…")
def _titles(brand, start, end, rate_key, fx_key, dataver=0.0):
    rates, eff, src = _rates(end, fx_key)        # 위 캐시를 그대로 탄다
    return sm.title_revenue(brand, start, end, rates), eff, src


def load_titles(brand, start, end):
    # ★dataver — 이 페이지의 캐시엔 **데이터 버전 키가 없었다**. ttl 900 뿐이라
    #   수집이 막 끝난 직후에 뽑으면 최대 15분 전 매출로 대외 문서가 나갔다.
    #   다른 페이지는 전부 file_version 을 키로 넘긴다(2026-08-19 맞춤).
    return _titles(brand, start, end, end, fx.version(), sc.data_version())


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

RATES, EFF, SRC = _rates(E, fx.version())
_official = SRC != fx.SRC_FALLBACK
c3.markdown(
    f"<div style='padding-top:26px;font-size:12.5px;color:var(--text-2)'>"
    f"환율 기준일 <b>{EFF or '—'}</b>"
    f"<span class='st-chip {'st-ok' if _official else 'st-warn'}'>{SRC}</span>"
    f"<div style='color:var(--text-3);font-size:11.5px;margin-top:2px'>"
    f"종료일 당일, 휴장일이면 직전 영업일</div></div>", unsafe_allow_html=True)

# ★환율표가 아예 비었으면(= settlement_map 이 '환율 없음' 을 돌려줬으면) 여기서
#   멈춘다 (2026-08-20). 그대로 두면 DuckDB `CASE … ELSE 1` 로 **해외 매출이 1:1
#   원화**가 된 미리보기가 뜬다 — fx.missing() 이 발행 버튼은 막지만, 그 전에
#   틀린 금액을 눈으로 보게 된다.
if len([v for v in (RATES or {}).values()
        if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0]) < 2:
    st.error("💱 " + str(EFF or E) + " 기준 환율을 구하지 못했어요 — 해외 매출을 "
             "원화로 바꿀 수 없어 정산서를 만들 수 없어요." + "  \n" +
             "잠시 뒤 다시 시도하거나, `config.json` 의 `exchange_rates` 를 "
             "확인해 주세요.")
    st.stop()

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
                    # ★전역 `st.cache_data.clear()` 를 부르고 있었다 — **앱 전체**
                    #   캐시라 접속 중인 다른 사람의 무거운 매출 캐시(포토이즘 agg·
                    #   스내피즘 master)까지 날려 동시 재로딩을 유발한다. 다른 페이지
                    #   둘은 이미 안 쓰기로 해 뒀다(views/0:291 · views/2:354).
                    #   여기 캐시는 전부 `fx.version()` 을 키로 받는데 fx.save 가
                    #   그 값을 올리므로 **알아서 무효화된다.** 부를 이유가 없다.
                    st.rerun()
            except Exception as e:                          # noqa: BLE001
                st.error(f"읽지 못했어요 — {e}")

if not CAN_EDIT:
    st.info("🔎 보기 전용이에요. 확정·발행은 편집 권한이 있어야 해요.")


# ══════════════════════════════════════════════════════════════════════════
# 정산서 만들기
# ══════════════════════════════════════════════════════════════════════════
# 'CANDIP-27201' 같은 티켓번호 꼴. 쉼표·공백·줄바꿈 아무거나로 나눠 담아도 잡힌다.
_TK_RE = re.compile(r"[A-Za-z]{2,}-\d+")


@st.cache_data(ttl=900, max_entries=8, show_spinner=False)
def _title_tickets(brand, start, end, rate_key, fx_key, mapver, dataver=0.0):
    """{타이틀: (확정티켓|None, [후보티켓...])} — 전 타이틀을 **한 번만** 훑는다.

    ★티켓마다 `suggest_titles` 를 부르면 그때마다 매출을 다시 집계한다.
      티켓 4장이면 4번이라 화면이 눈에 띄게 느려진다. 한 번 훑어 캐시한다.
    """
    df, _, _ = _titles(brand, start, end, rate_key, fx_key, dataver)
    mp = sm.load_mapping()["mappings"].get(brand, {})
    out = {}
    for t in df["타이틀"]:
        rec = mp.get(t) or {}
        if rec.get("excluded"):
            out[t] = ("__excluded__", [])
            continue
        fixed = str(rec.get("ticket") or "").upper()
        out[t] = (fixed or None, [c["ticket_key"] for c in sm.candidates(brand, t)])
    return out


def _ticket_box(brand: str):
    """티켓번호 **또는 IP명**으로 찾아 붙일 타이틀을 고른다.

    반환: (쓰인 티켓 목록, 고른 타이틀 목록, {타이틀: 그 타이틀이 붙을 티켓})

    ★한 IP 를 회차별로 나눠 등록하면 티켓이 여러 장이 된다(예: 베이온 —
      데뷔 기념 CANDIP-27201 + 전지점 전환 CANDIP-31739). 같이 정산하는 건이라
      **한 문서에 담아야** 한다. 이름으로 찾으면 관련 티켓이 다 나오니 필요한
      것만 체크하면 된다.
    ★단, 이름 검색은 **기본 해제**다. 렌탈처럼 같은 IP 라도 따로 정산하는 건이
      섞여 나오는데, 기본 선택이면 모르고 같이 넣게 된다.
    """
    icon = "📸" if brand == "photoism" else "📊"
    st.markdown(f"**{icon} {sm.BRAND_LABEL[brand]}**")
    q = st.text_input(
        "티켓번호 또는 IP명", key=f"tk_{brand}", label_visibility="collapsed",
        placeholder="CANDIP-12345 · 여러 장이면 쉼표로 · IP명으로 찾아도 돼요").strip()
    # ★찾는 대상이 바뀌면 **이전 IP의 체크 상태를 버린다**(2026-08-13).
    #   위젯 키가 한 번 생기면 스트림릿은 `value=` 를 무시하고 세션 값을 쓴다.
    #   그래서 이름으로 찾아 해제해 둔 타이틀이, 나중에 티켓번호를 직접 넣어도
    #   (그때는 기본 선택인데) 해제된 채로 남았다. 고른 게 없으면 만들기 구역이
    #   통째로 사라져 화면에서는 '눌러도 반응이 없다'로 보인다.
    # ★기간도 축에 넣는다 (2026-08-19). 전엔 검색어(q)만 봤다 — 7월에 특정 타이틀을
    #   일부러 해제해 두고 **기간만 8월로 바꾸면** 그 해제가 그대로 살아남아
    #   8월 매출이 조용히 빠진다. 타이틀 목록 자체가 기간마다 달라지므로
    #   '다른 건을 보는 중'으로 봐야 한다.
    _qsig = f"{q}‖{S}‖{E}"
    if st.session_state.get(f"_q_{brand}") != _qsig:
        st.session_state[f"_q_{brand}"] = _qsig
        for _k in [k for k in st.session_state
                   if k.startswith((f"ck_{brand}_", f"tkpick_{brand}_",
                                    f"tkpicks_{brand}_"))]:
            del st.session_state[_k]
    if not q:
        return [], [], {}, []

    typed = []
    for m in _TK_RE.findall(q.upper()):
        if m not in typed:
            typed.append(m)

    if typed:                       # 번호를 직접 적었으면 그것만, 기본 선택
        tickets, default_on = typed, True
        missing = [t for t in tickets if not sm.lookup_ticket(brand, t)]
        if missing:
            st.error(f"못 찾은 번호: {', '.join(missing)} — 확인해 주세요.")
        tickets = [t for t in tickets if t not in missing]
    else:                           # 이름 검색 — 관련 티켓을 늘어놓고 고르게
        tickets = [f["ticket"] for f in sc.find_tickets(brand, q, limit=12)]
        default_on = False
        if not tickets:
            st.caption("그 이름으로는 티켓을 못 찾았어요.")
    if not tickets:
        return [], [], {}, []

    tt = _title_tickets(brand, S, E, E, fx.version(), sm.mapping_version(),
                        sc.data_version())
    rev = dict(zip(*_rev_index(brand)))          # 타이틀 → (매출액, 국가수)

    # ★★목록의 축은 **타이틀**이다(티켓이 아니라).
    #   전엔 티켓마다 블록을 그렸는데, 스내피즘 '베이온' 은 티켓 6장이 같은 타이틀을
    #   물고 있어 **똑같은 줄이 6번** 나왔다. 금액도 구성도 다 같아서 무엇을 고르라는
    #   건지 알 수 없다는 지적을 받았다(2026-08-11).
    #   확정 매핑이 '타이틀 1 : 티켓 1' 이므로 화면도 그 모양이어야 한다 —
    #   **타이틀 한 줄 + 그 타이틀을 담을 티켓 고르기.**
    claimed = {}                       # 타이틀 → [그 타이틀을 무는 티켓...]
    for tk in tickets:
        for t, (fixed, cands) in tt.items():
            if fixed == "__excluded__":
                continue
            # ★★확정은 **대표를 정하는 것이지 나머지를 가리는 게 아니다** (2026-09-03).
            #   전엔 `fixed` 가 있으면 그 한 장만 통과시켰다. 타이틀 1 : 티켓 1 이던
            #   시절의 규칙인데, 상품별로 티켓이 나뉘는 건이 생기면서 탈이 났다 —
            #   더윈드는 `폴라릿`(31888)로 확정돼 있어서, 번호를 직접 적어 불러온
            #   `스티커 프레임, 포토카드`(31552)까지 후보에서 걸러져 **고를 칸에 한
            #   장밖에 안 떴다.** 확정은 아래 multiselect 의 기본값(대표)으로만 쓰고,
            #   후보 자체는 막지 않는다. 기본 선택은 여전히 확정 한 장뿐이라
            #   모르는 새 딸려 들어가지는 않는다.
            if (fixed == tk) or (tk in cands):
                claimed.setdefault(t, []).append(tk)
    if not claimed:
        st.warning("이 기간에 그 티켓으로 잡히는 매출이 없어요.")
        return tickets, [], {}, []

    # 매출 큰 타이틀부터
    order = sorted(claimed, key=lambda t: -rev.get(t, (0, 0))[0])
    breakdown = _cat_breakdown(brand)
    chosen, tmap = [], {}
    extra_tks = []           # 대표 외에 '같이 담은' 티켓들 — 문서에 번호로만 들어간다
    for t in order:
        cands = claimed[t]
        fixed = tt[t][0]
        amt, ncc = rev.get(t, (0, 0))
        on = st.checkbox(
            f"{t} · {_fmt(amt)}원 · {ncc}개국" + ("  ✅" if fixed in cands else ""),
            value=(fixed in cands) or default_on,
            key=f"ck_{brand}_{t}", disabled=not CAN_EDIT)
        # 스내피즘은 한 타이틀 안에 판매 항목이 여러 가지다(단가가 서로 다르다).
        # 무엇이 들어 있는지 보여야 어느 티켓에 담을지 판단할 수 있다.
        if breakdown.get(t):
            st.caption("　└ " + " · ".join(f"{c} {_fmt(v)}원"
                                          for c, v in breakdown[t]))

        def _lab(x, _b=brand):
            e = sm.lookup_ticket(_b, x) or {}
            p = (e.get("parent") or " / ".join(e.get("titles") or []))[:34]
            return f"{x} · {p} · {(e.get('startdate') or '?')[5:]}~{(e.get('duedate') or '?')[5:]}"

        idx = cands.index(fixed) if fixed in cands else 0
        # ★후보 수는 고르는 칸 **위**에 적는다. 전엔 아래에 caption 으로 달았는데
        #   '후보 2장' 이 다음 줄의 제목처럼 읽혀서, 뒤에 아무것도 없으니 화면이
        #   잘린 줄 알았다는 지적을 받았다(2026-08-28). 나머지 후보는 드롭다운
        #   안에 있으니, 어디를 열어야 보이는지까지 문구에 담는다.
        if len(cands) > 1:
            st.caption(f"　└ 후보 티켓 {len(cands)}장 — **여러 장을 같이 담을 수 있어요**")
        # ★★한 타이틀에 티켓을 **여러 장** 담는다 (2026-09-03 요청).
        #   한 타이틀 안에 상품이 여러 가지고 티켓이 상품별로 나뉘는 경우가 있다 —
        #   더윈드: `폴라릿`(CANDIP-31888) · `스티커 프레임, 포토카드`(CANDIP-31552).
        #   한 장만 고를 수 있으면 나머지 티켓이 문서에서 통째로 빠진다.
        #   ★매출은 **타이틀 기준**이라 티켓을 더 골라도 **금액은 안 변한다.**
        #     바뀌는 건 문서에 적히는 티켓번호와 확정 저장 대상뿐이다.
        #   ★위젯 키를 `tkpicks_` 로 바꿨다 — 예전 `tkpick_` 에는 세션에 **문자열**이
        #     남아 있어서, 같은 키로 multiselect 를 그리면 타입이 부딪힌다.
        picked = st.multiselect(
            "담을 티켓", cands, default=([cands[idx]] if cands else []),
            format_func=_lab, key=f"tkpicks_{brand}_{t}", disabled=not CAN_EDIT,
            label_visibility="collapsed",
            help="이 타이틀 매출을 어느 티켓으로 정산할지 골라요. 상품별로 티켓이 "
                 "나뉘어 있으면 **여러 장을 같이** 고르세요 — 매출은 타이틀 기준이라 "
                 "금액은 그대로이고, 문서에 티켓번호가 모두 적혀요. "
                 "계약 티켓이 아니라 실제 상품 티켓을 고르세요.")
        if on:
            chosen.append(t)
            if picked:
                # ★확정 저장·요율은 **대표 한 장**(맨 앞)을 따른다. 요율이 하나라는
                #   전제이고(사용자 확정), 매핑 저장소도 타이틀당 티켓 하나를 쥔다.
                #   나머지는 문서에 티켓번호로만 함께 적힌다.
                tmap[t] = picked[0]
                extra_tks.extend(picked[1:])
            else:
                st.warning(f"`{t}` — 담을 티켓을 안 골랐어요. 한 장 이상 골라 주세요.")

    # 대표 티켓들 먼저, 그 뒤에 같이 담은 티켓들
    used = list(dict.fromkeys(list(tmap.values()) + extra_tks))
    if len(used) > 1:
        st.caption("🧾 티켓 " + f"{len(used)}장을 **한 장으로** 정산해요 — "
                   + " · ".join(f"`{x}`" for x in used))
    return used, chosen, tmap, []


@st.cache_data(ttl=900, max_entries=4, show_spinner=False)
def _cat_breakdown_cached(brand, start, end, dataver=0.0):
    """{타이틀: [(판매항목, 현지합), ...]} — 스내피즘만. 어느 티켓에 담을지 고를 때
    한 타이틀 안에 무엇이 들어 있는지 보이게 한다(와이드 스티커·포토카드·폴라릿).

    ※원화 환산 전 현지통화 합이라 정산 기준액과는 다르다 — 구성만 보는 용도다.
    """
    if brand != "snapism":
        return {}
    con = sm.duck()
    try:
        d = con.execute(f"""
            SELECT "프레임 이름" t, "상품 카테고리" c,
                   CAST(SUM(TRY_CAST("최종 결제 금액" AS BIGINT)
                            + TRY_CAST("쿠폰 할인 금액" AS BIGINT)) AS BIGINT) v
            FROM read_parquet('{sm.SN_MASTER.as_posix()}')
            WHERE CAST("날짜" AS DATE) BETWEEN DATE '{start}' AND DATE '{end}'
              AND NOT COALESCE("취소 여부", FALSE)
            GROUP BY 1, 2 HAVING v > 0
        """).df()
    finally:
        con.close()
    out = {}
    for t, g in d.groupby("t"):
        g = g.sort_values("v", ascending=False)
        if len(g) > 1:                    # 항목이 하나면 굳이 안 적는다
            out[t] = [(str(c), int(v)) for c, v in zip(g["c"], g["v"])]
    return out


def _cat_breakdown(brand):
    return _cat_breakdown_cached(brand, S, E, sc.data_version())


@st.cache_data(ttl=900, max_entries=8, show_spinner=False)
def _rev_index_cached(brand, start, end, rate_key, fx_key, dataver=0.0):
    df, _, _ = _titles(brand, start, end, rate_key, fx_key, dataver)
    return (list(df["타이틀"]),
            list(zip(df["매출액"].astype(int), df["국가수"].astype(int))))


def _rev_index(brand):
    return _rev_index_cached(brand, S, E, E, fx.version(), sc.data_version())


# ★★미리보기의 국가별 상세를 캐시한다 (2026-08-19). 이 블록은 `make_panel`
#   프래그먼트 안이라, 체크박스·요율 칸(국가 수만큼 최대 30개) **아무거나 건드릴
#   때마다** 통째로 다시 돌았다. 한 번이 `master_photoism.parquet`(441MB) 풀스캔 +
#   집계본 스캔이고 브랜드가 둘이면 두 배다 — 국가별 요율을 한 칸 채울 때마다
#   1GB 가까이 다시 읽고 있었다.
# ★키에 dataver 를 반드시 넣는다. 대외 문서를 만드는 화면이라 캐시가 옛 매출을
#   물고 있으면 slow 보다 훨씬 나쁘다.
@st.cache_data(ttl=900, max_entries=16, show_spinner=False)
def _members_cached(brand, titles_key, start, end, dataver, frames_key=None):
    """멤버 이름 목록 — 매핑 안내용. 키에 dataver 를 넣는 이유는 아래와 같다."""
    return sc.member_names(brand, list(titles_key), start, end,
                           frames=list(frames_key) if frames_key is not None else None)


@st.cache_data(ttl=900, max_entries=16, show_spinner=False)
def _axes_cached(titles_key, start, end, dataver):
    """테마 축 · 멤버 축 목록. 테마 수집본(theme_daily)에서 뽑는다.

    ★원장에는 테마가 없다 — 대·중·소분류는 매장 유형이다. 테마는 CMS 테마
      수집본에만 있고, 두 곳 금액이 (타이틀×국가×프레임) 단위로 0.0012% 안에서
      맞는다(2026-07 전량 대조). 그래서 **고를 것은 테마본이 정하고 금액은 원장이 낸다.**
    """
    return tp.axes(list(titles_key), start, end)


@st.cache_data(ttl=900, max_entries=16, show_spinner=False)
def _detail_cached(brand, titles_key, start, end, fx_key, dataver, frames_key=None):
    rates, _, _ = _rates(end, fx_key)
    return sc.fill_open(
        sc.country_detail(brand, list(titles_key), start, end, rates,
                          frames=list(frames_key) if frames_key is not None else None),
        sc.open_countries(brand, start, end))


@st.cache_data(ttl=900, max_entries=16, show_spinner=False)
def _rev_nats_cached(brand, titles_key, start, end, dataver):
    return sc.revenue_countries(brand, list(titles_key), start, end)


@st.fragment
def make_panel():
    picks, tmaps = {}, {}
    cols = st.columns(2)
    for col, b in zip(cols, sm.BRANDS):
        with col:
            tks, titles, tmap, _ = _ticket_box(b)
            picks[b], tmaps[b] = (tks, titles), tmap

    if not any(t for t, _ in picks.values()):
        st.info("위에 티켓번호나 IP명을 넣어 주세요. 포토이즘·스내피즘 중 한쪽만 있어도 돼요.")
        return


    # ── 테마 · 멤버로 좁히기 ───────────────────────────────────────────
    # ★★한 타이틀 안에서 **테마 축**과 **멤버(프레임) 축**을 따로 고른다
    #   (2026-08-24, 사용자 확정). 트리로 파고들지 않는 이유:
    #   테마의 뜻이 타이틀마다 다르다 — `260710 에이티즈` 는 테마가 곧 멤버지만
    #   `260505 코르티스` 는 테마 5(버전) × 멤버 5 의 **격자**라, 트리로 그리면
    #   같은 멤버를 다섯 번 체크해야 한다. 금액의 86.4% 가 격자 쪽이다.
    # ★기본은 두 축 다 '전체' 다. 그러면 아래로 넘어가는 frames 가 None 이 되어
    #   **지금까지와 1원도 다르지 않다**(검증 기준). 실제로 세 타이틀에서 확인함.
    frames_by_brand = {}
    _axes_by_brand = {}
    for b, (tks, titles) in picks.items():
        if not titles:
            continue
        ax = _axes_cached(tuple(titles), S, E, sc.data_version())
        _axes_by_brand[b] = ax
        if not ax["frames"]:
            continue                      # 테마 수집 대상이 아닌 IP — 축을 안 그린다
        _thn = [x["이름"] for x in ax["themes"]]
        _frn = [x["이름"] for x in ax["frames"]]
        _amt = {x["이름"]: x["금액"] for x in ax["frames"]}
        _arts = ax.get("artists") or []
        _alab = f" · 아티스트 {len(_arts)}명" if _arts else ""
        with st.expander(
                f"{sm.BRAND_LABEL.get(b, b)} — 테마 · 멤버로 좁히기"
                f"  ({len(_thn)}개 테마 · {len(_frn)}명{_alab})", expanded=False):
            st.caption("비워 두면 **전체**예요. 테마와 멤버를 같이 고르면 겹치는 것만 잡아요.")
            # ── 아티스트로 한 번에 고르기 (SM) ─────────────────────────
            # ★★한 타이틀에 여러 아티스트가 섞이고, 그 안에서 **한/영 테마가 쌍으로
            #   갈린다**(`260624_RIIZE` + `260624_라이즈`). 손으로 고르면 한글 쌍을
            #   놓치기 쉽고 그러면 **30% 넘게 덜 정산된다** — 실측 라이즈 5,292만 중
            #   한글 테마가 1,995만(37.7%)이다.
            #   그래서 아티스트를 고르면 그 쌍을 **테마 칸에 같이 채워 준다.**
            # ★채우기만 하고 **고르는 건 사람이 한다** — 채운 뒤 테마 칸에서 눈으로
            #   확인·수정할 수 있다(별칭 추천과 같은 철학).
            if _arts:
                _amap = {a["이름"]: a["테마들"] for a in _arts}
                ac1, ac2 = st.columns([3, 1])
                _pick_art = ac1.multiselect(
                    "아티스트로 고르기 (SM)", [a["이름"] for a in _arts], default=[],
                    key=f"pk_art_{b}",
                    format_func=lambda x: (
                        f"{x}  ({next(a['금액'] for a in _arts if a['이름'] == x):,}"
                        f" · 테마 {len(_amap[x])})"))
                if ac2.button("테마 칸에 넣기", key=f"pk_artgo_{b}",
                              disabled=not _pick_art, use_container_width=True):
                    _want = sorted({t for a in _pick_art for t in _amap[a]})
                    st.session_state[f"pk_th_{b}"] = _want
                    st.rerun()
                if _pick_art:
                    _n = sum(len(_amap[a]) for a in _pick_art)
                    ac1.caption(f"→ 테마 {_n}개가 채워져요 "
                                f"(한글·영문 표기가 갈린 건 **같이** 들어가요)")
            c1, c2 = st.columns(2)
            with c1:
                _th = st.multiselect("테마", _thn, default=[], key=f"pk_th_{b}",
                                     format_func=lambda x: x)
            with c2:
                _fr = st.multiselect("멤버", _frn, default=[], key=f"pk_fr_{b}",
                                     format_func=lambda x: f"{x}  ({_amt.get(x, 0):,})")
            r = tp.resolve(titles, S, E,
                           sel_themes=_th or None, sel_frames=_fr or None)
            # ★원장에 거는 값은 **타이틀과 짝지어진** title_frames 다. frames 는
            #   이름만 모은 표시용이라 필터로 쓰면 회차를 건너 남의 테마를 물고 온다.
            if r["title_frames"] is not None:
                frames_by_brand[b] = r["title_frames"]
                _n = sum(len(fs) for _, fs in r["title_frames"])
                st.caption(f"고른 멤버 {len(r['frames'])}명 · 타이틀 "
                           f"{len(r['title_frames'])}개에서 {_n}자리 — "
                           + (", ".join(r["frames"][:8]) or "없음")
                           + (" 외" if len(r["frames"]) > 8 else ""))
            # ★★비율로 나눠 담지 않는다. 원장에는 테마가 없어서 한 멤버가 여러 테마에
            #   걸쳐 있으면 **반쪽만 가져올 방법이 없다.** 조용히 반올림하면 대외 문서가
            #   틀린다 — 그래서 그대로 알려 주고 사람이 정하게 한다.
            if r["straddling"]:
                _tot = sum(x["전체 금액"] for x in r["straddling"])
                st.warning(
                    f"이 {len(r['straddling'])}명은 고른 테마와 다른 테마에 **걸쳐 있어요** — "
                    f"원장에는 테마가 없어서 반쪽만 떼어낼 수가 없어요(합 {_tot:,}). "
                    "멤버 축에서 직접 고르거나, 테마를 다 골라 주세요.\n\n"
                    + " · ".join(f"{x['이름']} {x['고른 테마 금액']:,}/{x['전체 금액']:,}"
                                 for x in r["straddling"][:6]))

    # ── 대상이 바뀌면 앞 건의 흔적을 지운다 ────────────────────────────
    # ★★안 지우면 **다른 IP 문서를 그대로 물고 간다**(2026-08-13 발견).
    #   `_pdfs`·`_meta` 는 발행할 때 넣고 어디서도 지우지 않았다. A 를 만든 뒤
    #   티켓을 B 로 바꾸면 화면 아래 '✅ A 정산서 완성'과 내려받기, **메일 첨부까지
    #   A 것**이 남는다. 그 상태로 보내면 B 담당자에게 A 문서가 간다.
    # ★IP명도 같은 병이다 — key 가 있으면 스트림릿이 `value=` 를 무시해서
    #   다음 IP 로 넘어가도 이름 칸이 앞 IP 그대로였다(문서 제목·발행 이력이 어긋난다).
    # 축을 둘로 나눈다 — 타이틀 체크를 하나 더 넣었다고 **손으로 적은 IP명까지
    # 되돌리면** 그것도 사고다. 이름은 '다른 건으로 옮겼을 때'만 다시 잡는다.
    # ★★축은 **찾는 대상**이지 '지금 체크된 티켓' 이 아니다 (2026-08-19 수정).
    #   전엔 `tks`(= 체크된 타이틀의 티켓)를 썼는데, 이름 검색으로 티켓 여러 장을
    #   펼쳐 놓고 타이틀을 하나씩 체크해 나가면 **새 티켓이 붙는 순간 _tsig 가 바뀌어
    #   방금 손으로 고쳐 쓴 IP명이 자동값으로 되돌아갔다.** 바로 아래 주석이
    #   "타이틀 체크를 하나 더 넣었다고 손으로 적은 IP명까지 되돌리면 사고"라고
    #   적어 둔 그 경우다 — 의도는 맞았는데 축을 잘못 골랐다.
    #   검색창 내용이 바뀌었을 때만 = 정말 '다른 건으로 옮겼을 때'만 다시 잡는다.
    _tsig = "‖".join(f"{b}:{st.session_state.get(f'tk_{b}', '')}" for b in sm.BRANDS)
    # ★★만들어 둔 PDF 를 버리는 기준에 **요율·파트너사·부가세**도 넣는다
    #   (2026-08-24, 전수검사 low #12). 전엔 브랜드·티켓·타이틀·기간뿐이라,
    #   정산서를 만든 뒤 ② 에서 요율 %만 고치고 💾 저장하면 `_sig` 가 그대로여서
    #   **옛 요율로 만든 PDF 를 계속 들고 있었다.** 바로 위 ③ 금액 확인 KPI 는
    #   새 요율로 다시 계산되니, 화면엔 새 금액이 뜨는데 첨부는 옛 문서였다.
    #   그 상태로 '📧 보내기' 를 누르면 파트너사에 옛 요율 문서가 나간다.
    #   ★저장값(get_rs/get_partner)을 축으로 쓴다 — PDF 를 만드는 sc.build_context
    #     가 읽는 게 바로 그 값이라, 화면 위젯이 아니라 이쪽을 봐야 어긋나지 않는다.
    def _rsig(b, tks):
        out = []
        for tk in tks:
            try:
                r = sc.get_rs(b, tk) or {}
                p = sc.get_partner(b, tk) or {}
            except Exception:                                   # noqa: BLE001
                r, p = {}, {}
            out.append(f"{tk}:{sorted(r.items(), key=str)}"
                       f"/{p.get('agency_name','')}|{p.get('mgmt_name','')}"
                       f"|{int(bool(p.get('vat', True)))}")
        return ";".join(out)

    # ★고른 멤버도 축에 넣는다 — 안 넣으면 대상을 좁혀 놓고 '만들기' 를 다시 안 눌렀을 때
    #   **넓은 범위로 만든 옛 PDF** 가 그대로 첨부된다(low #12 와 같은 병).
    # 서명에 넣는 프레임은 (타이틀, 프레임…) 짝이라 문자열로 펴서 넣는다.
    def _fsig(b):
        tf = frames_by_brand.get(b)
        return "" if tf is None else ";".join(f"{t}={','.join(fs)}" for t, fs in tf)

    _sig = "‖".join(f"{b}:{','.join(tks)}>{','.join(titles)}>{_rsig(b, tks)}"
                    f">{_fsig(b)}"
                    for b, (tks, titles) in sorted(picks.items())) + f"‖{S}‖{E}"
    if st.session_state.get("_sig") != _sig:
        st.session_state["_sig"] = _sig
        for _k in ("_pdfs", "_meta", "reason",
                   "mail_to", "mail_cc", "mail_note", "mail_files"):
            st.session_state.pop(_k, None)      # mail_files 는 남으면 예외까지 난다
    _t0 = next((t[0] for _, t in picks.values() if t), "")
    _auto = re.sub(r"^\s*\d{5,8}\s*", "", str(_t0)).strip()
    if st.session_state.get("_tsig") != _tsig:
        st.session_state["_tsig"] = _tsig          # 다른 건 → 자동값으로 다시 잡는다
        st.session_state["_ipname_keep"] = _auto
        st.session_state["ipname"] = _auto
    elif "ipname" not in st.session_state:
        # ★★`or "ipname" not in st.session_state` 였다 — 그런데 이 조건은
        #   '다른 건으로 옮겼다'가 아니라 **위젯이 잠깐 안 그려졌다**는 뜻이다.
        #   타이틀 체크를 다 뺐다가 되돌리면 '④ 정산서 만들기' 구역이 통째로
        #   사라졌다 생기고, 스트림릿은 **안 그려진 위젯의 값을 지운다** →
        #   손으로 적어 둔 IP명이 자동값으로 되돌아갔다(2026-08-19 실측).
        #   같은 건이면 **적어 둔 이름을 되살린다**(위젯 밖 `_ipname_keep` 에 보관).
        st.session_state["ipname"] = st.session_state.get("_ipname_keep") or _auto

    # ── 확정 저장 ─────────────────────────────────────────────────────
    # ★확정 매핑은 **타이틀 하나에 티켓 하나**다. 티켓을 여러 장 담아도 저장은
    #   대표 티켓 한 곳에만 한다 — 나머지는 이번 문서에 같이 실릴 뿐이다.
    #   (타이틀별로 티켓을 쪼개 저장하려 들면 다른 티켓의 확정을 지워 버린다)
    need_save = []                       # (brand, title, ticket|None) — None 은 해제
    for b, (tks, titles) in picks.items():
        tmap = tmaps.get(b, {})
        mp = sm.load_mapping()["mappings"].get(b, {})
        for t in titles:
            tk = tmap.get(t)
            if tk and str((mp.get(t) or {}).get("ticket") or "").upper() != tk:
                need_save.append((b, t, tk))
        for tk in tks:                   # 체크를 뺀 타이틀은 확정도 푼다
            for t in sc.titles_for_ticket(b, tk):
                # ★★`t in tmap` 을 반드시 같이 본다 (2026-08-24, 전수검사 low #11).
                #   체크박스 목록은 **그 기간에 매출이 있는 타이틀**로만 만들어진다
                #   (title_revenue 가 HAVING 매출액>0). 그래서 확정은 돼 있는데
                #   이번 기간 매출이 0인 타이틀은 목록에 아예 안 뜨고, 그걸
                #   '체크를 뺐다'로 읽어 **확정을 강제로 해제**하고 있었다.
                #   게다가 need_save 가 남으면 '정산서 만들기' 가 잠기니, 실무자는
                #   '확정' 을 누를 수밖에 없고 그 순간 매핑이 지워진다.
                #   지금 데이터에선 후보로 되살아나 금액 영향이 0이지만, 후보로
                #   안 잡히는 타이틀(접두어·표기가 지라 제목과 다른 것)이 걸리면
                #   다음 정산에서 그 매출이 조용히 빠진다.
                #   → **화면에 떠 있는데 체크를 뺀 것**만 해제한다.
                if t not in titles and t in tmap:
                    need_save.append((b, t, None))
    if need_save and CAN_EDIT:
        if st.button(f"✔️ 위 구성으로 확정 ({len(need_save)}개 타이틀)",
                     use_container_width=True):
            for b, t, tk in need_save:
                if tk:
                    sm.approve(b, t, tk, _email)
                else:
                    sm.unapprove(b, t)
            for b, t, tk in need_save:
                if tk:
                    auth.log_event(_email, f"settlemap:{b}:{tk}:{t}")
            _rerun()
        st.caption("확정하면 다음 달부터 같은 티켓에 자동으로 붙어요.")

    ui_theme.sec(2, "요율 확인", "지라에 있으면 자동으로 채워요")
    rs, rs_cc = {}, {}
    # ★get_rs 는 저장값이 없으면 지라를 부른다(네트워크). 아래에서 또 부르면
    #   화면이 눈에 띄게 느려진다 — 여기서 한 번 부른 결과를 재사용한다.
    saved_rs = {}
    for b, (tks, titles) in picks.items():
        if not tks or not titles:
            continue
        tk = tks[0]                      # 대표 티켓 — 문서·저장의 기준
        cur = sc.get_rs(b, tk)
        saved_rs[b] = cur
        # ★`[...]` 로 꺼내면 안 된다 — source 가 하나 늘 때마다 KeyError 로 죽는다.
        tag = {"화면 입력": "🖊 저장된 값", "지라": "🔗 지라",
               "없음": "⚠️ 없음 — 직접 넣어 주세요",
               "조회실패": "🚫 지라 조회 실패 — <b>요율이 없는 게 아니라 못 물어봤어요</b>"
               }.get(cur["source"], cur["source"])
        st.markdown(f"**{sm.BRAND_LABEL[b]}** `{tk}`"
                    + (f" <span class='muted'>외 {len(tks) - 1}장</span>"
                       if len(tks) > 1 else "")
                    + f" <span class='muted'>{tag}</span>", unsafe_allow_html=True)
        # ★티켓마다 요율이 따로 저장돼 있다. 합쳐서 정산하면 대표 티켓 값만 쓰이므로
        #   다른 값이 들어 있으면 조용히 무시된다 — 그건 알려 줘야 한다.
        _others = {t: sc.get_rs(b, t) for t in tks[1:]}      # 티켓당 한 번만 조회
        # ★'조회실패' 도 값이 없는 것이라 비교에서 뺀다 — 안 빼면 지라가 잠깐
        #   죽었을 때 "티켓마다 요율이 달라요" 경고가 헛나온다.
        _diff = [t for t, o in _others.items()
                 if o["source"] not in ("없음", "조회실패")
                 and (o["agency"], o["mgmt"]) != (cur["agency"], cur["mgmt"])]
        if _diff:
            ui_theme.nbox("warn", "⚠️ <b>티켓마다 요율이 달라요</b> — "
                          + " · ".join(f"<code>{t}</code>" for t in _diff)
                          + f"<div class='sub'>아래 값(<b>{tk}</b> 기준)으로 "
                            "한 장을 만들어요. 맞는지 확인해 주세요.</div>")
        # ★★위젯 키에 **티켓**을 넣는다 (2026-08-19 수정). 전엔 `ra_{b}` 처럼
        #   브랜드만 넣어서, IP 를 바꿔도 키가 같아 스트림릿이 `value=` 를 무시하고
        #   **앞 IP 의 요율·파트너사명을 그대로 들고 있었다.** 화면은 25%인데 문서는
        #   저장값 30% 로 나가고(미리보기 ≠ PDF), 그 화면을 믿고 💾 를 누르면
        #   새 IP 의 모든 티켓에 앞 IP 값이 덮어써졌다. 파트너사명이 남으면
        #   '제출처 ○○ 귀중'에 **남의 회사 이름**이 박혀 나간다.
        #   같은 병을 331줄이 `ipname` 에 대해 이미 적어 뒀는데 여기만 빠져 있었다.
        _wk = f"{b}_{tk}"
        k1, k2, k3, k4 = st.columns([1, 1, 1, 1.2])
        a = k1.number_input("소속사 %", 0.0, 100.0, float((cur["agency"] or 0) * 100),
                            0.5, key=f"ra_{_wk}")
        m = k2.number_input("대행사 %", 0.0, 100.0, float((cur["mgmt"] or 0) * 100),
                            0.5, key=f"rm_{_wk}")
        mg_cur = sc.get_mg(b, tk)
        has = k3.checkbox("MG 있음", value=mg_cur["has_mg"], key=f"mgh_{_wk}")
        amt = k4.number_input("MG 금액", 0, step=1_000_000,
                              value=int(mg_cur["amount"] or 0), disabled=not has,
                              key=f"mga_{_wk}")
        # 파트너사명 — 문서에 '제출처 ○○ 귀중' 과 정산 내역표 출자자명으로 들어간다.
        pt = sc.get_partner(b, tk)
        p1, p2, p3 = st.columns([1.3, 1.3, 0.9])
        an = p1.text_input("소속사명", value=pt["agency_name"], key=f"pa_{_wk}",
                           placeholder="예: 제이와이드컴퍼니")
        mn = p2.text_input("대행사명", value=pt["mgmt_name"], key=f"pm_{_wk}",
                           placeholder="선택")
        vt = p3.checkbox("부가세 적용", value=pt["vat"], key=f"vt_{_wk}",
                         help="총 지급액을 부가세 포함으로 보고 공급가액·VAT 를 나눠 적어요. "
                              "해외 파트너면 꺼 주세요.")
        # ── 국가별 예외 요율 (소속사) ──────────────────────────────
        # ★캐릭터 IP 는 나라마다 요율이 다르다(가나디: 한국 7 · 일본 10 · 중국 12).
        #   전 세계 30칸을 매번 채우게 하면 안 쓰므로 **그 기간에 매출이 난 나라만**
        #   줄 세우고, 기본 요율로 미리 채워 둔다. 다른 나라만 고치면 된다.
        _saved_cc = dict(cur.get("agency_cc") or {})
        _nats = _rev_nats_cached(b, tuple(titles), S, E, sc.data_version())
        _cc_new = {}
        with st.expander(f"🌏 국가별 소속사 요율 "
                         + (f"— {len(_saved_cc)}개국 지정됨" if _saved_cc else "— 기본 요율 일괄"),
                         expanded=bool(_saved_cc)):
            if not _nats:
                st.caption("이 기간에 매출이 난 국가가 없어요.")
            else:
                st.caption(f"**비워 두면 기본 요율({a:g}%)** 을 써요 — 다른 나라만 채우세요. "
                           "값을 하나라도 넣으면 문서의 '요율' 칸은 **국가별**로 적히고, "
                           "지급액은 나라마다 따로 반올림해 더해요. "
                           "**0 을 넣으면 그 나라는 정말 0%** 로 봐요(빈칸과 달라요).")
                _cols = st.columns(4)
                for _i, _n in enumerate(_nats):
                    _c = _cols[_i % 4]
                    # ★value=None 이라 빈칸이 유지된다. 0 과 '안 정함'을 구분해야
                    #   '이 나라는 0%' 계약을 표현할 수 있다.
                    _v = _c.number_input(
                        _n, 0.0, 100.0,
                        (float(_saved_cc[_n]) * 100 if _n in _saved_cc else None),
                        0.5, key=f"rcc_{_wk}_{_n}", placeholder=f"기본 {a:g}")
                    _eff = _v if _v is not None else a
                    _c.caption(("↳ " + f"{_eff:g}%") if _v is not None
                               else f"↳ 기본 {a:g}%")
                    if _v is not None:
                        _cc_new[_n] = _v / 100
                if _cc_new:
                    st.caption("지정한 나라: " + " · ".join(f"**{k} {v * 100:g}%**"
                                                        for k, v in _cc_new.items())
                               + f" · 나머지 {len(_nats) - len(_cc_new)}개국 {a:g}%")

        if st.button(f"💾 {sm.BRAND_LABEL[b]} 저장", key=f"sv_{_wk}",
                     disabled=not CAN_EDIT):
            # 여러 장을 합쳐 정산하는 건이면 **전부에 같은 값을 저장**한다.
            # 대표 티켓에만 넣으면, 다음에 순서를 바꿔 넣었을 때 요율이 비어 보인다.
            for _t in tks:
                sc.set_rs(b, _t, a / 100 or None, m / 100 or None, _email, _cc_new)
                sc.set_mg(b, _t, has, amt, mg_cur.get("note", ""), _email)
                sc.set_partner(b, _t, an, mn, vt, _email)
            _rerun()
        # ★★화면값과 저장값이 다르면 **미리보기와 문서가 어긋난다** (2026-08-19).
        #   아래 '금액 확인'은 화면값으로 세는데, PDF 는 build_context 가
        #   `get_rs(brand, ticket)` 로 **저장값**을 읽는다(settlement_calc:936).
        #   `stop` 게이트는 "저장값이 있나"만 보므로 버튼이 멀쩡히 켜져 있어서,
        #   저장을 안 누른 채 발행하면 화면과 다른 금액이 대외로 나간다.
        _pend = []
        if (a / 100 or None) != cur["agency"]:
            _pend.append(f"소속사 {a:g}% <span class='muted'>(저장값 "
                         f"{(cur['agency'] or 0) * 100:g}%)</span>")
        if (m / 100 or None) != cur["mgmt"]:
            _pend.append(f"대행사 {m:g}% <span class='muted'>(저장값 "
                         f"{(cur['mgmt'] or 0) * 100:g}%)</span>")
        if _cc_new != _saved_cc:
            _pend.append("국가별 요율")
        if (an, mn, vt) != (pt["agency_name"], pt["mgmt_name"], pt["vat"]):
            _pend.append("파트너사명 · 부가세")
        if _pend:
            ui_theme.nbox("warn", "💾 <b>아직 저장 안 한 값이 있어요</b> — "
                          + " · ".join(_pend)
                          + "<div class='sub'>아래 금액은 <b>화면 값</b>으로 계산하지만 "
                            "정산서는 <b>저장된 값</b>으로 만들어요. "
                            f"<b>💾 {sm.BRAND_LABEL[b]} 저장</b>을 눌러 주세요.</div>")
        rs[b] = (a / 100 or None, m / 100 or None)
        rs_cc[b] = _cc_new or dict(_saved_cc)

    # ── 미리보기 ──────────────────────────────────────────────────────
    ui_theme.sec(3, "금액 확인")
    tot_base = tot_a = tot_m = 0
    warns, miss, shown = [], [], []
    for b, (tks, titles) in picks.items():
        if not tks or not titles:
            continue
        _fk = frames_by_brand.get(b)      # 이미 해시 가능한 (타이틀, 프레임…) 튜플
        d = _detail_cached(b, tuple(titles), S, E, fx.version(), sc.data_version(),
                           _fk)
        if d.empty:                 # 그 기간 매출 행이 없으면 문서에도 안 들어간다
            continue
        shown.append(b)
        warns += sc.verify(d[d["매출액"] > 0], RATES)
        miss += fx.missing(RATES, d["unit"])
        base = int(d["매출액"].sum())
        ra, rm = rs.get(b, (None, None))
        tot_base += base
        # ★국가별 요율이 있으면 총액×요율이 성립하지 않는다 — 문서와 같은 식으로 낸다.
        _cc = rs_cc.get(b) or None
        if _cc:
            tot_a += sum(sp._alloc_settle(
                [int(x) for x in d["매출액"]], ra,
                [_cc.get(n, ra) for n in d["국가"]]))
        else:
            # ★파이썬 내장 round() 는 0.5 를 짝수로 보낸다(은행식). 문서는
            #   `_round_half_up`(엑셀 ROUND)을 쓰므로 그대로 두면 **화면과 PDF 가
            #   1원 어긋난다** — settlement_calc:214 가 브루나이 건으로 이미
            #   못 박아 둔 그 함수를 여기만 안 쓰고 있었다(2026-08-19 수정).
            tot_a += sc._round_half_up(base * ra) if ra else 0
        tot_m += sc._round_half_up(base * rm) if rm else 0
        # ★여기서 쓰는 '수량'은 문서(settlement_pdf:189)가 쓰는 값과 **같은 것**이다.
        #   `floor(현지 ÷ 평균단가)` 라 한 거래에 두 장이면 2로 세고, 그래서
        #   country_detail 의 `건수`(거래 행 수)보다 크다(대한축구협회 3,709 vs 3,654).
        #   2026-07-31 '건수로 통일'은 **라벨**을 바꾼 것이지 값을 바꾼 게 아니다.
        #   화면만 건수로 바꾸면 문서와 어긋나므로 건드리지 말 것.
        qty = "건수"          # 정산서와 같은 표기로 통일(브랜드 구분 없음)
        # ★★국가 수는 **행 수가 아니라 국가 이름의 가짓수**다 (2026-09-03 수정).
        #   전엔 `(d['매출액'] > 0).sum()` 으로 **행을 셌다.** 포토이즘은 국가당
        #   한 행이라 우연히 맞았지만, **스내피즘은 (판매항목 × 국가)로 행이 난다.**
        #   더윈드 실측: 포토카드 6 + 미니스티커 6 + 폴라로이드 1 = 13행이라
        #   6개국이 **13개국**으로 찍혔다. 판매 항목이 늘수록 더 부풀어 오른다.
        _ncc = int(d.loc[d["매출액"] > 0, "국가"].nunique())
        with st.expander(f"{sm.BRAND_LABEL[b]} · {_fmt(base)}원 · "
                         f"{qty} {_fmt(d['수량'].sum())} · "
                         f"매출발생 {_ncc}개국"):
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
    _pt = next((sc.get_partner(b, t[0]) for b, (t, _) in picks.items() if t), None)
    if _pt and _pt["vat"] and (tot_a or tot_m):
        rows = []
        for lab, v in (("소속사", tot_a), ("대행사", tot_m)):
            if not v:
                continue
            sup, vat = sc.vat_split(v, _pt["vat"])
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

    # ── 멤버 이름 정리 ────────────────────────────────────────────────
    # ★★`unmapped_members`·`set_member_alias` 는 만들어져 있는데 **화면에서
    #   부르는 곳이 없었다** (2026-08-21). 그래서 매핑할 방법이 자체가 없었고
    #   member_aliases.json 이 빈 채로 남아, 같은 멤버가 한글·영문 두 열로
    #   발행되고 있었다(2026-07 상위 12개 중 10개가 해당 — TWICE 는 NAYEON 과
    #   '나연' 이 따로, ATEEZ 는 'SAN A' 와 '산 A' 가 따로).
    #   **절사 단위가 국가 × 멤버**라 열이 갈리면 금액도 어긋난다.
    _mem_all = []
    for b, (tks, titles) in picks.items():
        if tks and titles:
            try:
                _mem_all += _members_cached(b, tuple(titles), S, E,
                                            sc.data_version())
            except Exception:                                  # noqa: BLE001
                pass
    # 이 문서가 담는 타이틀 전부 — IP 단위 별칭의 저장/조회 키다.
    _all_titles = tuple(sorted({t for _, ts in picks.values() for t in (ts or [])}))
    _un = sc.unmapped_names(_mem_all, _all_titles)
    if _un:
        _en = [m for m in sorted(set(_mem_all)) if m not in _un]
        # ★로마자로 '짐작' 한 추천을 얹는다 — 단, **저장은 사람이 한다**(name_alias 철학).
        #   로마자가 정확히 일치하는 짝만 '강함' 으로 미리 채우고 일괄 저장에 넣고,
        #   나머지는 '확인 필요' 로만 보여 주고 기본값은 (고르기) 그대로 둔다. 추천이
        #   틀려도 저장 전이라 매출이 조용히 옮겨 가지 않는다. (2026-08-24)
        try:
            _sug = mm.suggest(_un, _en)
        except Exception:                                      # noqa: BLE001
            _sug = {}
        _strong = {k: v["en"] for k, v in _sug.items() if v.get("strong")}
        # ★★전역 저장이 위험한 이름을 데이터로 가려낸다 (2026-09-02).
        #   기준은 '여러 IP에 나오나' 가 **아니다** — 그러면 거의 모든 이름이 걸린다
        #   (슬기는 `SM ent`·`성수중앙_SM ent`·`연남3호_SM ent` 셋에 나오는데 같은 사람이다).
        #   진짜 기준은 **이 문서 밖의 IP에도 그 이름이 있는가** 다.
        #     · 타쿠마 → 루네이트·비보이즈 · 이 문서가 루네이트면 **밖(비보이즈)에도 있다**
        #       → 전역으로 걸면 비보이즈의 타쿠마가 딸려간다 → 이 IP에만 저장
        #     · 슬기  → SM ent 3종 · 전부 이 문서 안 → 밖에 없으니 전역도 안전
        #   절사 단위가 국가 × 멤버라, 엉뚱하게 합쳐지면 대외 문서 금액이 틀어진다.
        try:
            _spread = sc.member_ip_spread(_un)
            _doc_ips = set(sc.titles_to_ips(_all_titles))
        except Exception:                                      # noqa: BLE001
            _spread, _doc_ips = {}, set()
        # 이 문서 밖 IP에도 있는 이름 = 전역 저장 금지
        _multi = {k for k, v in _spread.items() if set(v) - _doc_ips}

        def _save_alias(ko, en):
            """문서 밖 IP에도 있는 이름이면 이 IP들에만, 아니면 전역."""
            sc.set_member_alias(ko, en,
                                titles=_all_titles if ko in _multi else None)

        with st.expander(f"🔤 멤버 이름 정리 필요 {len(_un)}명 — 한글·영문이 "
                         "따로 잡혀 있어요", expanded=False):
            st.caption("같은 사람인데 한글 이름과 영문 이름이 각각 한 명으로 세어져요. "
                       "짝을 맞춰 두면 다음 발행부터 한 열로 합쳐져요. "
                       "**절사가 멤버 단위라 금액도 조금 달라져요.**")
            if _multi:
                st.caption(f"🔒 이 중 **{len(_multi)}명**은 같은 이름이 여러 IP에 있어요 "
                           f"({', '.join(sorted(_multi)[:4])}"
                           f"{' 외' if len(_multi) > 4 else ''}). "
                           "다른 IP의 동명이인이 딸려가지 않게 **이 IP에서만** 적용해요.")
            if _strong and CAN_EDIT:
                _cA, _cB = st.columns([3, 2])
                if _cA.button(f"✅ 추천 {len(_strong)}명 한 번에 저장 "
                              "(로마자 정확 일치)", key="mal_bulk"):
                    for _k, _e in _strong.items():
                        _save_alias(_k, _e)
                    _n_ip = sum(1 for _k in _strong if _k in _multi)
                    st.success(f"{len(_strong)}명 저장했어요."
                               + (f" (그중 {_n_ip}명은 이 IP에서만)" if _n_ip else ""))
                    st.rerun()
                _cB.caption("아래에서 하나씩 확인해 저장해도 돼요.")
            for _i, _ko in enumerate(_un[:30]):
                _s = _sug.get(_ko)
                c1, c2, c3 = st.columns([2, 3, 1])
                _lbl = f"**{_ko}**"
                if _s:
                    _lbl += (f"　↔ `{_s['en']}`"
                             + ("" if _s.get("strong") else " · 확인 필요"))
                if _ko in _multi:
                    _lbl += (f"　<span style='font-size:11px;color:#b45309'>🔒 이 IP에서만"
                             f" · {len(_spread.get(_ko, []))}개 IP에 있는 이름</span>")
                c1.markdown(_lbl, unsafe_allow_html=True)
                _opts = ["(고르기)"] + _en
                # 강한 추천만 미리 고른다. 약한 추천은 라벨로만 알리고 기본은 (고르기).
                _idx = (_opts.index(_s["en"]) if (_s and _s.get("strong")
                                                  and _s["en"] in _opts) else 0)
                _pick = c2.selectbox("영문 이름", _opts, index=_idx,
                                     key=f"mal_{_ko}", label_visibility="collapsed")
                if c3.button("저장", key=f"malb_{_ko}", disabled=not CAN_EDIT
                             or _pick == "(고르기)"):
                    _save_alias(_ko, _pick)
                    st.rerun()
            if len(_un) > 30:
                st.caption(f"… 외 {len(_un) - 30}명")

    # ── 만들기 ────────────────────────────────────────────────────────
    ui_theme.sec(4, "정산서 만들기")
    # 기본값은 위 '대상이 바뀌면' 블록에서 채운다 — 여기서 value= 를 주면
    # 세션 값과 부딪혀 스트림릿이 무시하고, 앞 IP 이름이 그대로 남는다.
    ip = st.text_input("정산서에 표기할 IP명", key="ipname")
    ipn = ip.strip()
    # 위젯 값이 GC 되어도 되살릴 수 있게 위젯 **밖** 키에 함께 적어 둔다(위 주석 참고).
    st.session_state["_ipname_keep"] = ipn

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
    # ★★화면 입력만으로는 문서가 안 나온다 — build_context 는 **저장된** 요율을 본다.
    #   전엔 이걸 안 막아서 금액은 보이는데 PDF 만 0부인 상태가 됐다(대한축구협회 v1~v4).
    else:
        _un = [sm.BRAND_LABEL[b] for b, (tk, ti) in picks.items()
               if tk and ti and not ((saved_rs.get(b) or {}).get("agency")
                                     or (saved_rs.get(b) or {}).get("mgmt"))]
        if _un:
            stop.append(f"요율 저장 필요({' · '.join(_un)}) — 위 💾 저장을 눌러 주세요")
    if not ipn:
        stop.append("IP명 미입력")
    if nextv > 1 and not reason.strip():
        stop.append("정정 사유 미입력")
    if need_save:
        stop.append("타이틀 확정 필요")
    # ★버튼이 **꺼져 있다는 걸 버튼 자리에서** 말해야 한다(2026-08-13).
    #   전엔 이유를 위쪽에 노란 줄로만 띄웠는데, 버튼과 이어져 보이지 않아
    #   "눌러도 반응이 없다"는 문의가 왔다. 실제로는 남은 일이 있어 꺼진 것이었다.
    if stop:
        ui_theme.nbox("warn", "⛔ <b>아직 못 만들어요</b> — 아래 버튼이 꺼져 있어요."
                              "<div class='sub'>" + " · ".join(stop) + "</div>")

    if st.button("📄 정산서 만들기", type="primary", use_container_width=True,
                 disabled=not CAN_EDIT or bool(stop),
                 help=("남은 일: " + " · ".join(stop)) if stop else
                      ("편집 권한이 있어야 발행할 수 있어요." if not CAN_EDIT else None)):
        with st.spinner("PDF 를 만드는 중이에요…"):
            # 티켓 목록을 그대로 넘긴다 — build_context 가 타이틀을 합쳐 한 장으로 만든다.
            ctx = sc.build_context({b: t for b, (t, _) in picks.items() if t},
                                   S, E, ipn, RATES, EFF or E,
                                   date.today().isoformat(), SRC,
                                   frames=frames_by_brand)
            # ★★먼저 만들어 보고, 성공했을 때만 발행 기록을 남긴다 (2026-08-05).
            #   전엔 record_issue 가 앞에 있어서, 한 부도 못 만들어도 버전이 올라갔다.
            #   실제로 대한축구협회가 v1~v4 까지 쌓이는 동안 PDF 는 0부였다.
            ctx["version"], ctx["reason"] = nextv, reason.strip()
            made = {}
            _leaks = []
            for kind, lab in (("agency", "소속사"), ("mgmt", "대행사")):
                fld = "agency" if kind == "agency" else "mgmt"
                if not any((ctx["rs"].get(b) or {}).get(fld) for b in ctx["details"]):
                    continue        # 요율 없는 수취처는 문서를 만들지 않는다
                _html = sp.build_html(ctx, kind)
                # ★스펙 절대 규칙 2번 — **상대방 요율이 문서에 남으면 안 된다.**
                #   `verify_secrecy` 는 정의만 있고 아무 데서도 안 불리고 있었다
                #   (2026-08-19 연결). 만드는 쪽은 자기 rs_key 만 읽으므로 실제로
                #   샐 경로는 안 보이지만, 대외 문서라 확인은 붙여 둔다.
                _o = "mgmt" if kind == "agency" else "agency"
                for _r in {(ctx["rs"].get(b) or {}).get(_o) for b in ctx["details"]}:
                    if _r:
                        for _hit in sp.verify_secrecy(_html, _r):
                            _leaks.append((lab, _hit))
                made[lab] = sp.render_pdf(_html,
                                          f"IP 정산서({lab}) · {ipn} · {S}~{E}")
            if _leaks:
                # ★막지는 않는다 — 비중 칸 숫자가 우연히 같은 값일 수 있다
                #   (요율 10% ↔ 어느 나라 비중 10.0%). 사람이 확인할 몫이다.
                ui_theme.nbox("warn", "🔍 <b>상대 요율과 같은 숫자가 문서에 있어요</b> — "
                              + " · ".join(f"{l} 문서에 <code>{h}</code>"
                                           for l, h in _leaks)
                              + "<div class='sub'>비중 칸 숫자와 우연히 같을 수 있어요. "
                                "보내기 전에 해당 문서를 한 번 확인해 주세요.</div>")
        if not made:
            # ★조용히 0부로 끝나면 '발행됐는데 문서가 없다'가 된다. 원인을 짚어 준다.
            _none = [sm.BRAND_LABEL[b] for b, v in saved_rs.items()
                     if not (v.get("agency") or v.get("mgmt"))]
            ui_theme.nbox("err",
                          "정산서를 한 부도 만들지 못했어요 — <b>저장된 요율이 없어요</b>"
                          + (f" ({' · '.join(_none)})" if _none else "")
                          + "<div class='sub'>위 <b>② 요율 확인</b>에서 %를 넣고 "
                            "<b>💾 저장</b>을 누른 뒤 다시 만들어 주세요. 화면에 입력만 "
                            "하면 금액은 보이지만 문서에는 안 들어가요.</div>")
            st.stop()
        with st.spinner("발행 기록을 남기는 중이에요…"):
            # 스냅샷을 남긴다. 매출은 매일 갱신되고 취소도 뒤늦게 붙어서,
            # 얼려두지 않으면 보낸 문서를 다시 뽑을 수 없다.
            rec = sc.record_issue(ctx, _email, reason.strip())
            # ★세션에만 두면 새로고침 한 번에 사라진다. 대외로 나간 문서라
            #   나중에 원본 그대로 다시 꺼낼 수 있어야 한다(발행 이력에서 재다운로드).
            sc.save_issued_pdfs(rec["key"], rec["version"], made)
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
        # ★정산서 PDF 는 대외 문서라 특히 누가 받아 갔는지 남아야 한다.
        auth.download_button(f"⬇️ {lab} 정산서", data, key=f"dl_{lab}",
                             file_name=f"정산서_{meta.get('ip','IP')}_{S[:7]}_{lab}.pdf",
                             mime="application/pdf", use_container_width=True,
                             page="settledoc", container=c)

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
            # ★발행분 다시 받기 — 예전엔 만든 그 자리에서 못 받으면 방법이 없었다.
            #   보관 시작(2026-08-05) 이전 건은 파일이 없어 안 뜬다.
            _opts = {f'{h["key"].replace("|", " · ")} · v{h["version"]}': h
                     for h in hist[:50]}
            _pick = st.selectbox("발행분 다시 받기", list(_opts), key="hist_pick",
                                 help="발행할 때 만든 PDF 원본이에요. 지금 다시 계산한 게 "
                                      "아니라 그때 그대로예요.")
            _h = _opts.get(_pick) or {}
            _files = sc.issued_pdfs(_h.get("key", ""), _h.get("version", 0)) if _h else {}
            if _files:
                _hc = st.columns(len(_files))
                for _c, (_lab, _data) in zip(_hc, _files.items()):
                    auth.download_button(
                        f"⬇️ {_lab}", _data, key=f"hist_dl_{_pick}_{_lab}",
                        file_name=f'정산서_{_h["key"].split("|")[0]}_'
                                  f'{_h["key"].split("|")[1][:7]}_{_lab}_v{_h["version"]}.pdf',
                        mime="application/pdf", use_container_width=True,
                        page="settledoc", container=_c)
            else:
                st.caption("이 건은 보관된 PDF 가 없어요. 보관은 2026-08-05 발행분부터예요 — "
                           "위에서 같은 조건으로 다시 만들면 정정본이 되니 주의해 주세요.")
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
