"""IP 정산서 PDF — 디자인 정밀스펙 구현.

스펙: dashboard-redesign 레포 settlement/정산서-AI-구현-정밀스펙.md
정답 화면: settlement-label.html(소속사) / settlement-agency.html(대행사)

절대 규칙
  1. 수신처별로 완전히 분리된 별도 문서. 한 파일에 합치지 않는다.
  2. **상대방 요율 기밀** — 대행사본 어디에도 소속사 요율이 있으면 안 된다.
     생성 후 문자열 검색으로 확인한다(verify_secrecy).
  3. 원 미만 반올림은 **은행가 반올림(half-to-even)**. 파이썬 내장 round() 가
     그 방식이라 그대로 쓴다. 소계는 **각 행 정산액의 합**(기준액×요율 아님).
  4. **이모지 금지 · 격식체(합니다체)**. 해요체 금지.
  5. Pretendard 고정, 숫자는 tabular-nums.
  6. A4 4페이지. 하단에 큰 여백을 남기지 않는다.

★사내 예외: 매출 미발생 오픈 국가는 스펙(표 아래 한 줄)과 달리 **표에 행으로**
  넣는다. 미오픈으로 오해받지 않게 해달라는 요청이 앞서 있었다.

관련: CURRENT-PROJECTS/IP-정산서-생성.md · 지라 CO-288
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path

BASE_DIR = Path(__file__).parent
PRETENDARD = ("https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9"
              "/dist/web/variable/pretendardvariable-dynamic-subset.min.css")

# 도넛 팔레트. 스펙 순서에서 6번째만 바꿨다 — 원래 #7c77ee 는 1번(#6366f1)과
# 거의 같은 보라라서 조각과 범례를 맞추기 어렵다는 피드백이 있었다. 금색으로 교체.
PIE = ["#6366f1", "#b45309", "#0f9d77", "#d24d8b", "#38a3e8", "#c98a2e",
       "#7c77ee", "#5f6b7a"]
BRAND_LABEL = {"photoism": "포토이즘", "snapism": "스내피즘"}
# ★수량 표기는 **두 브랜드 다 '건수'로 통일**한다(요청). 포토이즘은 프레임, 스내피즘은
#   스티커·포토카드로 물건은 다르지만, 둘 다 '매출 ÷ 평균단가'로 구한 **판매 수량**이라
#   같은 뜻이다. 브랜드마다 단어가 다르면 표를 나란히 볼 때 다른 지표로 오해받는다.
QTY_LABEL = {"photoism": "건수", "snapism": "건수"}
QTY_UNIT = {"photoism": "건", "snapism": "건"}
# 단가표 열 이름. 한 브랜드만 정산할 때는 브랜드명을 빼도 문서 전체가 그 브랜드다.
PRICE_HEAD = {"photoism": "건당 단가", "snapism": "상품 형태별 단가"}
# 100단위로 고시하는 통화 — 표기만 100배로 하고 ' /100' 을 붙인다.
FX_100 = {"JPY", "IDR", "VND", "LAK", "MNT"}
# ★서울외국환중개 **미고시** 통화. 라오스·페루 둘뿐이고 USD 크로스로 채운다.
#   (칠레 CLP 는 고시 통화라 여기 없다 — settlement_fx.UNLISTED 와 같은 목록)
FX_UNLISTED = ("LAK", "PEN")


def used_brands(ctx: dict) -> list[str]:
    """이 문서가 **실제로** 다루는 브랜드.

    ★티켓을 골랐다고 그 브랜드가 문서에 들어가는 게 아니다. 그 기간에 매출 행이
      하나도 없으면 장 자체가 안 만들어진다. 문구·장 번호·메일 본문은 전부
      이 목록을 기준으로 써야 '포토이즘 + 스내피즘' 이 헛나오지 않는다.
    """
    return [b for b in ("photoism", "snapism")
            if (ctx.get("details") or {}).get(b) is not None
            and not ctx["details"][b].empty]


def brands_label(ctx: dict, sep: str = " + ") -> str:
    return sep.join(BRAND_LABEL[b] for b in used_brands(ctx))


def _f(n) -> str:
    try:
        return f"{int(round(float(n))):,}"
    except (TypeError, ValueError):
        return "—"


# [삭제 2026-08-19] `rhe()` — 은행가 반올림 헬퍼. 문서 규칙이 **엑셀 half-up**
#   (`settlement_calc._round_half_up`)으로 통일되면서 쓰이지 않게 됐다. 이름이 짧아
#   무심코 쓰기 쉬운데 쓰면 1원이 어긋난다.


def vat3(amount: int, use_vat: bool) -> tuple[int, int]:
    """정산액 → (공급가액, 부가세). **계산식은 settlement_calc.vat_split 한 곳에만 둔다.**

    ★2026-08-03 수정. 예전엔 여기서 use_vat=False 를 '부가세 별도'로 보고 정산액의
      10% 를 얹었는데, 화면(vat_split)은 같은 플래그를 '부가세 없음'(0원)으로 봤다.
      같은 파트너 설정을 두 함수가 정반대로 해석해, vat=False 티켓이 생기는 순간
      화면과 PDF 의 지급액이 10% 갈릴 상태였다.
      체크박스 도움말이 "해외 파트너면 꺼 주세요"라 **끄면 부가세 자체가 없는 것**이 맞다.
    """
    from settlement_calc import vat_split
    return vat_split(int(amount or 0), bool(use_vat))


def _fx_cell(unit: str, local, krw, rates=None) -> str:
    """적용 환율. KRW 행은 '—'.

    ★★**고시값을 그대로 적는다** (2026-08-24, 전수검사 low #13).
      전엔 `매출KRW ÷ 현지매출` 로 역산했다. 그런데 매출KRW 는 반올림된 값이라
      금액이 작은 나라일수록 역산값이 흔들려, **같은 통화가 한 문서 안에서 두
      값으로 인쇄됐다** — 실제 발행본(USD 1,441.1)에서 미국 1,441.10 · 괌
      1,441.09 가 같이 찍혔다(괌은 32달러 × 1441.1 = 46,115.2 → 46,115 로
      반올림된 뒤 ÷32 = 1,441.09375). 부록의 '국가별 적용 환율' 표는 고시값을
      쓰므로 본문과도 어긋났다. 금액은 맞았고 표기만 틀렸다.
      ※config 환율(USD 1,402.5)로는 32×1402.5 가 딱 떨어져 재현이 안 된다 —
        환율값에 따라 나타났다 사라지는 종류라 눈으로는 못 잡는다.
      고시값을 못 찾을 때만(통화가 환율표에 없음) 예전처럼 역산한다.
    """
    u = str(unit or "").strip().upper()
    if u == "KRW":
        return "—"
    per = 100 if u in FX_100 else 1
    v = (rates or {}).get(u)
    if not v:
        if not local:
            return "—"
        v = krw / local                      # 폴백 — 부록 표와 어긋날 수 있다
    return f"{v * per:,.2f}" + (" <small>/100</small>" if per == 100 else "")


def _chart_items(rows, total, top_n=6):
    """비중 차트 공통 데이터 — 상위 N개 + '기타 M개국'. (이름, 비중%, 금액)"""
    top = rows[:top_n]
    rest = len(rows) - len(top)
    items = [(r["국가"], (r["매출액"] / total * 100) if total else 0,
              int(r["매출액"])) for r in top]
    if rest > 0:
        items.append((f"기타 {rest}개국",
                      max(0.0, 100 - sum(p for _, p, _ in items)),
                      total - sum(v for _, _, v in items)))
    return items


def _chart_donut(rows, total):
    """원형(도넛) + 범례. conic-gradient 라 이미지 없이 인쇄된다."""
    items = _chart_items(rows, total)
    stops, acc = [], 0.0
    for i, (_, p, _) in enumerate(items):
        col = PIE[i] if i < len(PIE) else PIE[-1]
        stops.append(f"{col} {acc:.2f}% {acc + p:.2f}%")
        acc += p
    leg = "".join(
        f'<div class="lg"><i style="background:{PIE[i] if i < len(PIE) else PIE[-1]}"></i>'
        f'<span class="ln">{n}</span><b class="num">{p:.1f}%</b>'
        f'<span class="la num">{_f(v)}</span></div>'
        for i, (n, p, v) in enumerate(items))
    return (f'<div class="dn"><div class="dnc" '
            f'style="background:conic-gradient({",".join(stops)})"></div>'
            f'<div class="dnl">{leg}</div></div>')


def _chart_column(rows, total):
    """세로 막대. 높이로 크기를, 아래 이름으로 국가를 읽는다."""
    items = _chart_items(rows, total)
    mx = max((p for _, p, _ in items), default=1) or 1
    cols = "".join(
        f'<div class="cl"><b class="num">{p:.1f}%</b>'
        f'<span class="cb"><i style="height:{max(p / mx * 100, 2):.1f}%;'
        f'background:{PIE[i] if i < len(PIE) else PIE[-1]}"></i></span>'
        f'<span class="cn">{n}</span><span class="ca num">{_f(v)}</span></div>'
        for i, (n, p, v) in enumerate(items))
    return f'<div class="cols">{cols}</div>'


def _share_chart(rows, total):
    """국가별 매출 비중 — **가로 막대**.

    ★도넛 + 범례는 조각과 이름을 눈으로 짝지어야 해서 한눈에 안 들어온다는
      피드백이 있었다. 가로 막대는 길이만 보면 순위와 격차가 바로 읽히고
      이름·수치가 같은 줄에 있어 시선을 옮길 필요가 없다.
      (디자인 스펙의 도넛에서 의도적으로 벗어난 부분)
    """
    top = rows[:6]
    rest = len(rows) - len(top)
    items = [(r["국가"], (r["매출액"] / total * 100) if total else 0,
              int(r["매출액"])) for r in top]
    if rest > 0:
        items.append((f"기타 {rest}개국",
                      max(0.0, 100 - sum(p for _, p, _ in items)),
                      total - sum(v for _, _, v in items)))
    mx = max((p for _, p, _ in items), default=1) or 1
    h = ""
    for i, (name, p, amt) in enumerate(items):
        col = PIE[i] if i < len(top) else PIE[7]
        h += (f'<div class="brow"><span class="bn">{name}</span>'
              f'<span class="bt"><i style="width:{max(p / mx * 100, 1.5):.1f}%;'
              f'background:{col}"></i></span>'
              f'<span class="bp num">{p:.1f}%</span>'
              f'<span class="ba num">{_f(amt)}</span></div>')
    return f'<div class="bars">{h}</div>'


def _alloc_settle(krws, rs, rates=None):
    """국가별 정산액. 합계가 **총 기준액 × 요율을 한 번 반올림한 값**과 같도록 배분.

    ★rates 를 주면(국가마다 요율이 다른 계약) 총액×요율이 성립하지 않는다.
      그땐 **국가별로 반올림해 더한다** — 담당자도 나라마다 따로 계산하므로 같은 값이 나온다.
      (가나디처럼 한국 7% · 일본 10% · 중국 12% 인 캐릭터 IP 계약용.)

    ★국가마다 따로 반올림해 더하면 총액 기준과 어긋난다(김준수 13,555,438 vs
      13,555,436). 담당자 시트는 총액에 요율을 한 번 곱해 지급액을 내므로 그쪽에
      맞춘다. 그러면서도 표의 세로 합은 그대로 맞아야 해서, 목표를 먼저 정하고
      최대잔여법으로 국가에 나눈다(멤버 배분과 같은 방식).
    """
    from settlement_calc import _round_half_up
    if rates is not None:                      # 국가별 요율 — 행마다 따로 반올림
        return [_round_half_up(k * (r or 0)) for k, r in zip(krws, rates)]
    if not rs:
        return [0] * len(krws)
    target = _round_half_up(sum(krws) * rs)
    raw = [k * rs for k in krws]
    base = [int(x) for x in raw]                 # 0 쪽 절사
    gap = target - sum(base)
    if gap > 0:
        order = sorted(range(len(raw)), key=lambda i: (-(raw[i] - base[i]), -krws[i]))
        for i in order[:gap]:
            base[i] += 1
    elif gap < 0:
        order = sorted((i for i in range(len(raw)) if base[i] > 0),
                       key=lambda i: (raw[i] - base[i], -krws[i]))
        for i in order[:-gap]:
            base[i] -= 1
    return base


def _pct(v) -> str:
    """요율 표기. 7.5% 처럼 소수도 그대로, 정수는 정수로.
    ★0 과 None 을 구분한다 — 0% 계약인 나라를 '—'(요율 없음)로 적으면 안 된다."""
    return "—" if v is None else f"{v * 100:g}%"


NOSALE = "매출 없음"        # settlement_calc.fill_open 이 붙이는 표식


def _country_table(rows, qty_label, rate_pct, rs, rs_cc=None, rates=None):
    """국가 | 통화 | 수량 | 현지 매출 | 적용 환율 | 매출(KRW) | 요율 | 정산액 | 비중

    ★소계 정산액은 각 행 정산액의 합이면서 총액×요율과도 같다(_alloc_settle).
    """
    # ★`or 1` 이었다 — 매출과 취소가 정확히 상쇄돼 합이 0 이면 분모가 1 이 돼
    #   `100,000,000.0%` 가 찍힌다. 총합이 0 이하면 비중을 아예 안 적는다.
    # ★★분모는 **양수 행의 합**이다 (2026-08-21). 전엔 음수(취소가 더 많은 나라)까지
    #   더한 값을 분모로 썼는데, 그러면 분모가 작아져 양수 나라들의 비중 합이 100%를
    #   넘는다 — 같은 장에 도넛은 100.0%, 표는 **145.3%** 로 찍혀 있었다.
    #   도넛(_share_rows)은 이미 양수만 분모로 쓰므로 그쪽에 맞춘다. 음수 행은
    #   비중을 안 적는다(‘-’). 정산액·합계는 음수를 그대로 반영하므로 영향 없다.
    _t = sum(r["매출액"] for r in rows if r["매출액"] > 0)
    tot_krw = _t if _t > 0 else 0
    # 국가별 요율이 있으면 행마다 다른 요율을 쓴다. 없으면 전 행이 기본 요율.
    _rates = ([(rs_cc or {}).get(r["국가"], rs) for r in rows] if rs_cc else None)
    amts = _alloc_settle([int(r["매출액"]) for r in rows], rs, _rates)
    tq = tk = ts = 0
    # 한 페이지에 들어갈 만큼 자동으로 조인다(기본 27행까지 여유).
    # ★항목별로 묶으면 머리글 + 소계가 묶음마다 2행씩 더 붙는다. 그걸 안 세면
    #   페이지를 뚫는다(실제로 잘렸다).
    _ngrp = len({r.get("구분") for r in rows
                 if r.get("구분") and r.get("구분") != NOSALE})
    _eff = len(rows) + _ngrp * 2
    dens = " d2" if _eff > 33 else (" d1" if _eff > 26 else "")
    h = (f'<table class="t2 num{dens}"><tr><th>국가</th><th>통화</th>'
         f'<th>{qty_label}</th><th>현지 매출</th><th>적용 환율</th>'
         '<th>매출(KRW)</th><th>요율</th><th>정산액</th>'
         '<th>비중</th></tr>')
    # ★판매 항목('구분')이 있으면 항목마다 머리글 + 소계를 넣는다. 스내피즘은 한 IP 를
    #   와이드 스티커·포토카드·폴라릿으로 나눠 파는데 단가가 서로 달라, 한 덩어리로
    #   적으면 무엇으로 얼마가 났는지 안 보인다(담당자 요청). 총 매출은 맨 아래 합계.
    grouped = any(c and c != NOSALE for c in (r.get("구분") for r in rows))
    _sub = {}
    if grouped:
        for _i, r in enumerate(rows):
            k = r.get("구분") or "기타"
            if k == NOSALE:
                continue
            a, b_, c_ = _sub.get(k, (0, 0, 0))
            _sub[k] = (a + int(r["수량"]), b_ + int(r["매출액"]), c_ + amts[_i])
    _cur = None
    for _i, r in enumerate(rows):
        krw, loc, q = int(r["매출액"]), int(r["현지"]), int(r["수량"])
        amt = amts[_i]
        if grouped:
            k = r.get("구분") or "기타"
            # ★매출 0인 나라는 묶음으로 만들지 않는다 — 포토이즘처럼 표 안에 그냥
            #   한 줄로 둔다('매출 없음' 머리글·소계까지 붙이면 정신없다는 지적).
            if k == NOSALE:
                if _cur is not None:
                    sq, sk, ss = _sub[_cur]
                    h += (f'<tr class="csum"><td>{_cur} 소계</td><td></td>'
                          f'<td>{_f(sq)}</td><td></td><td></td><td>{_f(sk)}</td>'
                          f'<td></td><td>{_f(ss)}</td><td></td></tr>')
                    _cur = None
            elif k != _cur:
                if _cur is not None:            # 앞 묶음 소계
                    sq, sk, ss = _sub[_cur]
                    h += (f'<tr class="csum"><td>{_cur} 소계</td><td></td>'
                          f'<td>{_f(sq)}</td><td></td><td></td><td>{_f(sk)}</td>'
                          f'<td></td><td>{_f(ss)}</td><td></td></tr>')
                _cur = k
                h += (f'<tr class="cathd"><td colspan="9">{k}'
                      f'<span>{_f(_sub[k][1])}원</span></td></tr>')
        tq += q; tk += krw; ts += amt
        # 음수(취소가 더 많은 나라)는 비중을 안 적는다 — 분모가 양수 합이라
        # 음수 비중을 같이 적으면 합이 100%에 안 맞아 더 헷갈린다.
        p = (krw / tot_krw * 100) if (tot_krw and krw > 0) else None
        zero = krw == 0
        dash = '<span style="color:#c3c9d4">—</span>'
        h += (f'<tr><td>{r["국가"]}'
              + ('<small style="font-weight:500"> 매출 없음</small>' if zero else '')
              + f'</td><td class="cur">{r["unit"]}</td>'
              f'<td>{_f(q) if q else dash}</td>'
              f'<td class="dim">{_f(loc) if loc else dash}</td>'
              f'<td class="dim">{_fx_cell(r["unit"], loc, krw, rates) if krw else dash}</td>'
              f'<td>{_f(krw) if krw else dash}</td>'
              f'<td class="dim">{_pct(_rates[_i]) if _rates else rate_pct}</td>'
              f'<td>{"<b>" + _f(amt) + "</b>" if amt else dash}</td>'
              + ('<td>' + dash + '</td></tr>' if p is None else
                 '<td><span class="sharecell"><span class="bar">'
                 f'<i style="width:{max(p, 1.2):.1f}%"></i></span>{p:.1f}%</span>'
                 '</td></tr>'))
    if grouped and _cur is not None:            # 마지막 묶음 소계
        sq, sk, ss = _sub[_cur]
        h += (f'<tr class="csum"><td>{_cur} 소계</td><td></td><td>{_f(sq)}</td>'
              f'<td></td><td></td><td>{_f(sk)}</td><td></td><td>{_f(ss)}</td>'
              '<td></td></tr>')
    h += (f'<tr class="sum"><td>{"합계" if grouped else "소계"}</td><td></td>'
          f'<td>{_f(tq)}</td><td></td>'
          f'<td></td><td>{_f(tk)}</td><td></td><td>{_f(ts)}</td><td></td>'
          '</tr></table>')
    return h, tk, ts, tq


def _k(n) -> str:
    """원 → 천원. **내림**(1차 피드백 스펙 · 검증 체크리스트 기준).

    ★`//` 를 쓰면 안 된다 — 음수에서 -∞ 쪽으로 내려서 -1,500원이 **-2천원**이 된다.
      취소만 남은 칸이 실제보다 크게 깎여 보인다. 문서 전체 규칙(`settlement_calc._trunc`)
      대로 **0 쪽 절사**로 맞춘다(양수는 결과가 같다). 2026-08-19.
    """
    try:
        return f"{int(int(float(n)) / 1000):,}"
    except (TypeError, ValueError):
        return "—"


# 별첨 한 면에 넣을 국가 수. D-3 는 국가마다 2행이라 27개국이 한 면을 넘긴다
# (실측 +99px). 시안의 행 간격을 줄이는 대신 스펙이 허용한 면 분할을 택했다.
ANNEX_PER_PAGE = 22


def _matrix(piv, members, rev=None, totals=None, head_only=False):
    """국가 × 멤버 — D-3 방식. 국가마다 **2행**(위 건수 / 아래 매출).

    ★한 칸에 건수+매출을 겹쳐 쓰던 방식은 1차 피드백에서 반려됐다. 값이 두 개씩
      들어가 어느 쪽을 읽는지 헷갈린다는 이유. 행을 나누고 매출 행만 보라색·천원
      단위로 눌러, 한 칸에는 언제나 값이 하나만 있게 한다.
      정답 시안: dashboard-redesign 레포 settlement/settlement-matrix-d3-final.html
    """
    if piv is None or piv.empty:
        return ""
    has_rev = rev is not None and not rev.empty

    h = ('<table class="t3 num d3"><tr><th>국가</th>'
         + "".join(f"<th>{m}</th>" for m in members) + "<th>합계</th></tr>")
    for nat, row in piv.iterrows():
        vals = [int(row.get(m, 0) or 0) for m in members]
        rr = rev.loc[nat] if has_rev and nat in rev.index else None
        revs = [int((rr.get(m, 0) if rr is not None else 0) or 0) for m in members]
        h += (f'<tr class="r1"><td>{nat}</td>'
              + "".join(f"<td>{_f(v)}</td>" if v else '<td class="z">—</td>'
                        for v in vals)
              + f"<td>{_f(sum(vals))}</td></tr>")
        if has_rev:
            h += ('<tr class="r2"><td>매출</td>'
                  + "".join(f"<td>{_k(v)}</td>" if v else '<td class="z">—</td>'
                            for v in revs)
                  + f"<td>{_k(sum(revs))}</td></tr>")
    # 합계는 **전체 국가 기준**이라 면을 나눠도 마지막 면에만 한 번 붙인다.
    if totals is not None:
        tot, tot_r = totals
        h += ('<tr class="sum"><td>합계 (건수)</td>'
              + "".join(f"<td>{_f(v)}</td>" for v in tot)
              + f"<td>{_f(sum(tot))}</td></tr>")
        if has_rev:
            h += ('<tr class="sum s2"><td>합계 (매출)</td>'
                  + "".join(f"<td>{_k(v)}</td>" for v in tot_r)
                  + f"<td>{_k(sum(tot_r))}</td></tr>")
    return h + "</table>"


def _fx_table(items, rates):
    """정산에 적용한 **모든 국가**의 환율. 표지 그리드는 8개만 보여 준다.

    100단위 고시 통화(엔·루피아 등)는 고시 그대로 100단위로 적는다 — 1엔당
    9.4원 식으로 적으면 고시표와 대조가 안 된다. 원화는 환산 자체가 없다.
    """
    def cell(unit):
        u = str(unit or "").strip().upper()
        if u == "KRW":
            return '<td class="base">기준통화</td>'
        v = rates.get(u)
        if not v:
            return '<td class="base">—</td>'
        per = 100 if u in FX_100 else 1
        return (f"<td>{v * per:,.2f}"
                + (' <small>/100</small>' if per == 100 else "") + "</td>")

    def one(part):
        h = ('<table class="t5 num"><tr><th>국가</th><th>통화</th>'
             '<th>적용 환율</th></tr>')
        for nat, unit, _ in part:
            h += (f'<tr><td>{nat}</td><td class="cur">{unit}</td>{cell(unit)}</tr>')
        return h + "</table>"

    n = len(items)
    if n <= 10:
        return one(items)
    k = -(-n // 3)
    return ('<div class="fxtbl">' + "".join(one(items[i:i + k])
            for i in range(0, n, k)) + "</div>")


def _price_table(items, used, split=True):
    """국가별 단가. **정산에 들어간 브랜드의 열만** 만든다.

    ★스내피즘은 상품 형태(미니 스티커·와이드 스티커·포토카드…)마다 단가가 다르다.
      형태명 없이 숫자만 적으면 어느 상품 가격인지 알 수 없고, 한 값만 뽑아 쓰면
      나머지 형태를 숨기는 셈이 된다 → 형태별로 전부 적는다.
    """
    multi = len(used) > 1
    head = "".join(f"<th>{(BRAND_LABEL[b] + ' ') if multi else ''}"
                   f"{PRICE_HEAD[b]}</th>" for b in used)
    # 국가가 많으면 세로로 길어져 페이지를 넘긴다 → 여러 단으로 접는다.
    # ★★한 번만 쪼갠다(split=False). 예전엔 재귀라 30개국이 15/15 로 갈린 뒤
    #   15도 14보다 커서 또 8/7 로 갈렸고, 2단 그리드 칸 안에 2단 그리드가 겹쳐
    #   사실상 4단이 됐다 → 페이지 폭을 넘어 **맨 오른쪽 단이 잘렸다**(실측).
    if split and len(items) > 14:
        # 스내피즘이 섞이면 '형태별 단가' 칸이 넓어 3단은 안 들어간다.
        ncol = 3 if (not multi and len(items) > 30) else 2
        k = -(-len(items) // ncol)
        cols = [items[i:i + k] for i in range(0, len(items), k)]
        return (f'<div class="pricegrid c{len(cols)}">'
                + "".join(_price_table(c, used, split=False) for c in cols)
                + "</div>")
    h = f'<table class="t4 num"><tr><th>국가</th><th>통화</th>{head}</tr>'
    for nat, unit, pv in items:
        tds = ""
        for b in used:
            d = pv.get(b) or {}
            if not d:
                tds += '<td class="z">—</td>'
            elif b == "photoism":
                tds += f"<td>{_f(list(d.values())[0])}</td>"
            else:
                tds += ('<td class="pl">' + "".join(
                    f'<span class="pill">{c}<b>{_f(v)}</b></span>'
                    for c, v in sorted(d.items(), key=lambda x: -x[1])) + "</td>")
        h += f'<tr><td>{nat}</td><td class="cur">{unit}</td>{tds}</tr>'
    return h + "</table>"


def _mk(label: str, on: bool) -> tuple[str, str]:
    """변경 표시본용. 요소에 덧붙일 (클래스, 배지) 한 쌍을 준다.

    검토 때 '뭐가 바뀌었는지' 눈으로 찾게 하지 않으려고 넣었다. 실제 발행본은
    on=False 라 클래스도 배지도 붙지 않는다(문서에 흔적이 남으면 안 된다).
    """
    return (" mk", f'<span class="mkb">{label}</span>') if on else ("", "")


def _page_head(chip, name, sub, right):
    return (f'<div class="phd"><div class="l"><div class="chip">{chip}</div>'
            f'<h2>{name}<span>{sub}</span></h2></div>'
            f'<div class="r">{right}</div></div>')


CSS = """
:root{--brand:#4f46e5;--brand-2:#6366f1;--brand-soft:#eef0fe;--ink:#1e2a44;
--text:#1b2330;--text-2:#5b6573;--text-3:#8a93a3;--surface-2:#f8fafc;
--border:#e2e5ea;--border-2:#cdd2da;--amber:#b45309}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Pretendard Variable',Pretendard,sans-serif;color:var(--text);
-webkit-print-color-adjust:exact;print-color-adjust:exact;background:#fff}
.num{font-variant-numeric:tabular-nums}
.page{width:794px;height:1123px;background:#fff;padding:46px 52px;position:relative;
overflow:hidden;page-break-after:always}
.page:last-child{page-break-after:auto}
@page{size:A4;margin:0}
.foot{position:absolute;left:52px;right:52px;bottom:26px;display:flex;
justify-content:space-between;font-size:10px;color:var(--text-3);
border-top:1px solid var(--border);padding-top:7px}
.hd{display:flex;justify-content:space-between;align-items:flex-start;
padding-bottom:12px;border-bottom:2px solid var(--ink)}
.hd .co{font-size:15px;font-weight:800;color:var(--ink)}
.hd .co small{font-weight:500;font-size:10.5px;color:var(--text-3);
letter-spacing:.12em;margin-left:6px}
.hd .meta{text-align:right;font-size:10.5px;color:var(--text-2);line-height:1.65}
.hd .meta b{color:var(--text)}
/* 발행일·발행자 줄은 뺐다(요청). 받는 곳을 크게, 문서번호는 식별용으로 작게. */
.hd .docno{font-size:9.5px;color:var(--text-3);letter-spacing:.03em}
.hd .to{margin-top:6px;line-height:1.3}
.hd .to span{display:block;font-size:10px;font-weight:700;color:var(--text-3);
letter-spacing:.06em}
.hd .to b{font-size:15px;font-weight:800;color:var(--ink)}
.badge{display:inline-block;font-size:11px;font-weight:700;color:var(--brand);
background:var(--brand-soft);border-radius:99px;padding:4px 12px}
.badge.sample{color:var(--amber);background:#fdf3e7;margin-left:6px}
.title{font-size:27px;font-weight:800;margin-top:12px;letter-spacing:-.01em}
.title span{font-size:15px;font-weight:600;color:var(--text-2);margin-left:4px}
.subtitle{font-size:12.5px;color:var(--text-2);margin-top:5px}
.intro{margin-top:14px;font-size:12.5px;color:var(--text-2);line-height:1.7}
.hero{margin-top:16px;background:linear-gradient(135deg,#f5f4ff,#eef0fe);
border:1px solid #dcdefc;border-radius:14px;padding:20px 24px;display:flex;
align-items:center;gap:24px}
.hero .k{font-size:12.5px;font-weight:700;color:var(--brand)}
.hero .v{font-size:36px;font-weight:800;color:var(--brand);letter-spacing:-.01em;
margin-top:4px}
.hero .l{flex:1.35}
.hero .r{flex:1;background:#fff;border:1px solid var(--border);border-radius:12px;
padding:12px 16px;font-size:12px;color:var(--text-2)}
.hero .r div{display:flex;justify-content:space-between;padding:3px 0}
.hero .r b{color:var(--text)}
.sec{margin-top:20px}
.sec h3{font-size:14px;font-weight:800;color:var(--ink);margin-bottom:8px}
.sec h3 small{font-size:11px;font-weight:500;color:var(--text-3);margin-left:6px}
table{border-collapse:collapse;width:100%}
.t1 th{font-size:11px;font-weight:700;color:var(--text-2);background:var(--surface-2);
border-top:1.5px solid var(--ink);border-bottom:1px solid var(--border-2);
padding:8px 10px;text-align:right}
.t1 th:first-child{text-align:left}
.t1 td{font-size:12.5px;padding:9px 10px;border-bottom:1px solid var(--border);
text-align:right}
.t1 td:first-child{text-align:left;font-weight:700}
.t1 td small{color:var(--text-3);font-weight:500}
.t1 tr.sum td{font-weight:800;color:var(--ink);background:var(--surface-2);
border-bottom:1.5px solid var(--ink)}
.fxgrid{display:grid;grid-template-columns:repeat(4,1fr);border:1px solid var(--border);
border-radius:8px;overflow:hidden}
.fxgrid div{font-size:11px;padding:7px 10px;display:flex;justify-content:space-between;
border-bottom:1px solid var(--border);border-right:1px solid var(--border);
color:var(--text-2)}
.fxgrid div b{color:var(--text)}
.fxnote{font-size:10.5px;color:var(--text-3);margin-top:6px}
.fxnote b{color:var(--text-2);font-weight:700}
.guide{margin-top:18px;background:var(--surface-2);border-radius:10px;
padding:12px 16px;font-size:11.5px;color:var(--text-2);line-height:1.85}
.guide b{color:var(--text)}
.notes{margin-top:14px;font-size:10.5px;color:var(--text-2);line-height:1.8}
.phd{display:flex;justify-content:space-between;align-items:flex-end;
padding-bottom:10px;border-bottom:2px solid var(--ink)}
.phd .l .chip{font-size:10.5px;font-weight:700;color:var(--text-3);letter-spacing:.06em}
.phd .l h2{font-size:19px;font-weight:800;color:var(--ink);margin-top:3px}
.phd .l h2 span{font-size:12.5px;font-weight:600;color:var(--text-2);margin-left:6px}
.phd .r{font-size:11px;color:var(--text-2);text-align:right;line-height:1.6}
.phd .r b{color:var(--text)}
.duo{display:flex;gap:20px;margin-top:16px;align-items:center}
.donutbox{flex:1.28 1 0;min-width:0}
/* KPI 4칸을 뺀 뒤 비중 그래프가 쓰는 전폭 자리 */
.chartwide{margin-top:16px}
/* ── 비중 그래프 A안: 원형(도넛) ── */
.dn{display:flex;align-items:center;gap:26px}
.dnc{width:132px;height:132px;border-radius:50%;flex:none;position:relative}
.dnc:after{content:"";position:absolute;left:50%;top:50%;width:74px;height:74px;
margin:-37px 0 0 -37px;background:#fff;border-radius:50%}
.dnl{flex:1;display:grid;grid-template-columns:1fr 1fr;gap:3px 20px}
.lg{display:flex;align-items:center;gap:6px;font-size:10.5px;color:var(--text)}
.lg i{width:8px;height:8px;border-radius:2px;flex:none}
.lg .ln{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.lg b{font-size:10.5px;font-weight:800}
.lg .la{color:var(--text-3);font-size:9.5px;min-width:58px;text-align:right}
/* ── 비중 그래프 C안: 세로 막대 ── */
.cols{display:flex;align-items:flex-end;gap:10px;height:150px}
.cl{flex:1;display:flex;flex-direction:column;align-items:center;height:100%}
.cl b{font-size:10.5px;font-weight:800;margin-bottom:3px}
.cl .cb{flex:1;width:100%;display:flex;align-items:flex-end;
background:linear-gradient(var(--surface-2),var(--surface-2));border-radius:4px}
.cl .cb i{display:block;width:100%;border-radius:4px}
.cl .cn{font-size:10px;font-weight:700;margin-top:5px;white-space:nowrap;
overflow:hidden;text-overflow:ellipsis;max-width:100%}
.cl .ca{font-size:9px;color:var(--text-3)}
.bars{display:flex;flex-direction:column;gap:5px}
.brow{display:flex;align-items:center;gap:8px;font-size:11.5px;color:var(--text)}
.bn{width:70px;flex:none;font-weight:700;white-space:nowrap;overflow:hidden;
text-overflow:ellipsis}
.bt{flex:1;height:11px;background:#eef1f5;border-radius:3px;overflow:hidden}
.bt i{display:block;height:100%;border-radius:3px}
.bp{width:42px;text-align:right;font-weight:800;flex:none}
.ba{width:78px;text-align:right;color:var(--text-3);font-size:10.2px;flex:none}
.kpis{flex:1 1 0;min-width:0;display:grid;grid-template-columns:1fr 1fr;gap:8px}
.kpi{border:1px solid var(--border);border-radius:10px;padding:9px 12px}
.kpi .k{font-size:10.5px;font-weight:700;color:var(--text-2)}
.kpi .k .vt{font-weight:600;color:var(--text-3);font-size:9.6px}
.kpi .v{font-size:14px;font-weight:800;margin-top:3px;white-space:nowrap}
.kpi .sub{font-size:9.4px;font-weight:600;color:var(--text-2);margin-top:2px;
white-space:nowrap}
.zeroline{margin-top:7px;font-size:10.5px;color:var(--text-2)}
.kpi.hl{background:var(--brand-soft);border-color:#dcdefc}
.kpi.hl .v{color:var(--brand)}
.t2 th{font-size:10px;font-weight:700;color:var(--text-2);background:var(--surface-2);
border-top:1.5px solid var(--ink);border-bottom:1px solid var(--border-2);
padding:6px 8px;text-align:right;white-space:nowrap}
.t2 th:first-child{text-align:left}
.t2 td{font-size:10.8px;padding:5.5px 8px;border-bottom:1px solid var(--border);
text-align:right;white-space:nowrap}
.t2 td:first-child{text-align:left;font-weight:700}
.t2 td.cur{color:var(--text-3);font-size:9.5px;text-align:center}
.t2 td.dim{color:var(--text-2)}
.t2 tr.sum td{font-weight:800;color:var(--ink);background:var(--surface-2);
border-bottom:1.5px solid var(--ink)}
/* 판매 항목 머리글 — 표 안에서 묶음이 눈에 바로 들어오게. 소계는 한 톤 눌러
   전체 합계(tr.sum)와 구분한다. */
.t2 tr.cathd td{background:var(--surface-2);color:var(--ink);font-weight:800;
font-size:10.5px;padding:6px 8px;border-top:1.5px solid var(--ink);
border-bottom:1px solid var(--border);letter-spacing:-0.01em}
.t2 tr.cathd td span{float:right;font-weight:700;color:var(--text-2);
font-size:10px}
.t2 tr.csum td{font-weight:700;color:var(--text-1);background:#fbfbfd;
border-bottom:1px solid var(--ink)}
/* ★매출 0인 오픈 국가도 행으로 넣기 때문에 국가 수가 스펙 예시(27)보다 늘어난다.
   페이지 높이가 고정(1123px)이라 그대로 두면 소계 행이 푸터를 뚫는다.
   행이 많으면 자동으로 조인다. */
.t2.d1 td{font-size:10.2px;padding:4.2px 8px}
.t2.d1 th{padding:5px 8px}
.t2.d2 td{font-size:9.7px;padding:3.1px 8px}
.t2.d2 th{padding:4px 8px;font-size:9.4px}
.t2.d2 td.cur{font-size:8.8px}
.sharecell{display:inline-flex;align-items:center;gap:6px;justify-content:flex-end}
.sharecell .bar{width:52px;height:5px;background:#eef1f5;border-radius:99px;
overflow:hidden}
.sharecell .bar i{display:block;height:100%;background:var(--brand-2);border-radius:99px}
.t3 th{font-size:8.6px;font-weight:700;color:var(--text-2);background:var(--surface-2);
border-top:1.5px solid var(--ink);border-bottom:1px solid var(--border-2);
padding:5px 4px;text-align:right;white-space:nowrap}
.t3 th:first-child{text-align:left;padding-left:6px}
.t3 td{font-size:9.6px;padding:3.6px 4px;border-bottom:1px solid var(--border);
text-align:right;white-space:nowrap}
.t3 td:first-child{text-align:left;font-weight:700;padding-left:6px}
.t3 td.z{color:#c3c9d4}
.t3 td:last-child{font-weight:800}
.t3 tr.sum td{font-weight:800;color:var(--ink);background:var(--surface-2);
border-bottom:1.5px solid var(--ink)}
/* ── D-3: 국가마다 2행(위 건수 / 아래 매출). 시안 settlement-matrix-d3-final.html ──
   ★멤버 이름(PARK JEONG WOO 등)이 길어 nowrap 이면 표가 페이지 밖으로 밀려
   합계 열이 통째로 잘린다(.page 가 overflow:hidden 이라 조용히 사라진다).
   머리글만 줄바꿈을 허용해 폭을 되찾는다. */
.t3.d3 th{white-space:normal;padding:5px 3px;line-height:1.2}
.t3.d3 td{padding:3.2px 3px;border-bottom:none}
/* 건수 행 — 연회색 바탕. 건수↔매출 사이엔 선을 두지 않아 한 덩어리로 보인다. */
.t3.d3 tr.r1 td{background:#f4f5f7}
.t3.d3 tr.r1 td:last-child{font-weight:800}
/* 매출 행 — 흰 바탕·보라 글자·천원 단위. 아래에만 구분선. */
.t3.d3 tr.r2 td{color:var(--brand);border-bottom:1px solid var(--border)}
.t3.d3 tr.r2 td:first-child{font-weight:500;font-size:8.5px;color:var(--text-3)}
.t3.d3 tr.r2 td:last-child{font-weight:700}
.t3.d3 tr.r2 td.z{color:#c3c9d4}
.t3.d3 tr.sum td{border-bottom:1.5px solid var(--ink)}
.t3.d3 tr.sum.s2 td{font-size:9px;border-top:1px solid var(--border-2)}
/* 표 우측 상단 단위 표기 */
.sechead{display:flex;justify-content:space-between;align-items:baseline}
.unit{font-size:10px;color:var(--text-2);font-weight:600;white-space:nowrap}
.pricegrid{display:grid;gap:0 24px;align-items:start}
.pricegrid.c2{grid-template-columns:1fr 1fr}
.pricegrid.c3{grid-template-columns:1fr 1fr 1fr;gap:0 16px}
/* 단이 늘어도 폭을 넘지 않게 — 칸 안에서 표가 스스로 줄어들게 둔다. */
.pricegrid > table{width:100%;min-width:0;table-layout:fixed}
.t4 th{font-size:10px;font-weight:700;color:var(--text-2);background:var(--surface-2);
border-top:1.5px solid var(--ink);border-bottom:1px solid var(--border-2);
padding:6px 8px;text-align:right;white-space:nowrap}
.t4 th:first-child{text-align:left}
.t4 td{font-size:10.5px;padding:4.5px 8px;border-bottom:1px solid var(--border);
text-align:right;white-space:nowrap}
.t4 td:first-child{text-align:left;font-weight:700}
.t4 td.cur{color:var(--text-3);font-size:9.5px;text-align:center}
.t1 td.dim{color:var(--text-2);font-weight:600}
.t4 td.z{color:#c3c9d4}
.fxtbl{display:grid;grid-template-columns:1fr 1fr 1fr;gap:0 20px}
.t5 th{font-size:9.5px;font-weight:700;color:var(--text-2);background:var(--surface-2);
border-top:1.5px solid var(--ink);border-bottom:1px solid var(--border-2);
padding:5px 7px;text-align:right;white-space:nowrap}
.t5 th:first-child{text-align:left}
.t5 td{font-size:10.5px;padding:4px 7px;border-bottom:1px solid var(--border);
text-align:right;white-space:nowrap}
.t5 td:first-child{text-align:left;font-weight:700}
.t5 td.cur{color:var(--text-3);font-size:9.5px;text-align:center}
.t5 td.base{color:var(--text-3);font-weight:600}
.t4 td.pl{white-space:normal;padding:3.5px 8px}
.pill{display:inline-block;font-size:9px;font-weight:600;color:var(--text-2);
background:var(--surface-2);border:1px solid var(--border);border-radius:99px;
padding:1px 7px;margin:1px 0 1px 3px;white-space:nowrap}
.pill b{color:var(--text);font-weight:800;margin-left:4px}
.mk{position:relative;outline:1.6px dashed #16a34a;outline-offset:5px;border-radius:6px}
.mkb{position:absolute;right:-4px;top:-19px;background:#16a34a;color:#fff;
font-size:8.6px;font-weight:700;letter-spacing:-.01em;padding:2px 8px;
border-radius:99px;white-space:nowrap;z-index:2}
.badge.mkv{color:#16a34a;background:#e8f7ee}
.method{margin-top:16px;background:var(--surface-2);border-radius:10px;
padding:12px 16px;font-size:11px;color:var(--text-2);line-height:1.85}
.method b{color:var(--text)}
"""


# 비중 그래프 3안 중 **원형(도넛) 채택**(2026-07-31). 가로 막대(_share_chart)와
# 세로 막대는 그대로 두니 chart= 만 바꾸면 언제든 되돌릴 수 있다.
CHARTS = {"donut": _chart_donut, "bar": None, "column": _chart_column}


def build_html(ctx: dict, kind: str, sample: bool = False,
               marks: bool = False, chart: str = "donut") -> str:
    """kind='agency'(소속사) | 'mgmt'(대행사). **자기 요율만** 문서에 넣는다.

    sample — '샘플 · 검토용' 배지와 각주. 레이아웃을 검토하던 동안 기본값이 True
    였는데, 값이 실제와 맞는 걸 확인해 **2026-08-11 부터 기본 꺼짐**이다.
    시안을 다시 돌려볼 일이 있으면 True 로 부르면 된다.
    """
    # ★수취처 구분(소속사/대행사)은 **문서에 쓰지 않는다.** 요율이 다른 두 부를
    #   따로 만들지만 그건 내부 사정이고, 받는 쪽은 자기 정산서만 보면 된다.
    #   구분은 파일명·문서번호(L/A)에만 남긴다.
    party = "소속사" if kind == "agency" else "대행사"      # 내부용
    rs_key = "agency" if kind == "agency" else "mgmt"

    # ★★요율은 **브랜드마다 따로**다 (2026-08-19 수정).
    #   전엔 `next(... for b in ctx["details"])` 로 **처음 만난 브랜드의 요율 하나**를
    #   뽑아 두 브랜드 표에 똑같이 썼다. BRANDS 순서가 photoism 먼저라 포토이즘이
    #   항상 이겼고, 실제로 포토이즘은 20·25·30·35% 네 종류인데 스내피즘은 30% 하나라
    #   섞인 문서에서 스내피즘 정산액이 어긋났다. 표지 요약표의 스내피즘 줄에도
    #   포토이즘 요율이 인쇄됐다 — 대외 문서에 계약과 다른 요율이 박히는 것이다.
    #   국가별 예외 요율(agency_cc)도 dict.update 라 뒤 브랜드가 앞을 덮었다.
    # ★한 브랜드에만 요율이 있으면 다른 브랜드는 **0** 이다(빌려 쓰지 않는다).
    #   화면 미리보기(views/8 `round(base * rm) if rm else 0`)가 이미 그렇게 센다.
    def _b_rs(b):
        return (ctx["rs"].get(b) or {}).get(rs_key)

    def _b_cc(b):
        # 국가별 예외 요율은 소속사 문서에만 있다
        if rs_key != "agency":
            return None
        return ((ctx["rs"].get(b) or {}).get("agency_cc") or None)

    def _b_pct(b):
        # 국가별이 있으면 한 숫자로 못 쓴다 — 대표값을 적으면 받는 쪽이 그 값으로
        # 검산하다 안 맞는다. '국가별'이라고 적고 상세 표에서 나라마다 보여 준다.
        _r = _b_rs(b)
        return "국가별" if _b_cc(b) else (f"{_r * 100:g}%" if _r else "—")
    ip, S, E = ctx["ip"], ctx["start"], ctx["end"]
    ym = f"{int(E[:4])}년 {int(E[5:7])}월"
    # ★★문서번호는 **여기서 만든다** (2026-08-24, 전수검사 low #14).
    #   settlement_calc.doc_number() 는 규칙까지 다 짜 놓고 **호출부가 0건**이라
    #   ctx["docno"] 를 채우는 곳이 없었다 — 대외 문서 머리말·꼬리말의 번호 자리가
    #   늘 비어 있었고(HTML 에 'SB-SET' 0건), 정정본을 주고받을 때 어느 문서를
    #   말하는지 특정할 수가 없었다.
    #   ★ctx 에 미리 넣지 않고 여기서 만드는 이유: 번호에 종류(L/A)가 들어가는데
    #     같은 ctx 로 대행사용·관리용 두 장을 만든다. ctx 에 하나로 박으면 둘이
    #     같은 번호를 달게 된다.
    docno = ctx.get("docno") or ""
    if not docno:
        try:
            from settlement_calc import doc_number
            docno = doc_number(ip, E, kind)
        except Exception:                                   # noqa: BLE001
            docno = ""
    iss = ctx.get("issuer") or {}
    _p = ctx.get("partner") or {}
    recv = (_p.get("agency_name") if kind == "agency" else _p.get("mgmt_name")) or ""
    use_vat = bool(_p.get("vat", True))

    used = used_brands(ctx)
    # 표지 hero 의 '요율' 칸은 한 칸뿐이다. 브랜드가 서로 다른 요율이면 한 숫자로
    # 못 쓰므로 '브랜드별'이라고 적는다 — 바로 아래 브랜드별 요약표에 각자 값이 있다.
    _pcts = list(dict.fromkeys(_b_pct(b) for b in used))
    rate_pct = _pcts[0] if len(_pcts) == 1 else "브랜드별"
    used_label = " + ".join(BRAND_LABEL[b] for b in used)
    prose = "·".join(BRAND_LABEL[b] for b in used)      # 문장 안에서 쓸 표기
    multi = len(used) > 1

    # ── 브랜드별 계산 (행 단위 반올림 → 합) ──────────────────────────────
    calc = {}
    for b in used:
        _d = ctx["details"][b]
        # ★판매 항목별로 묶은 표는 **다시 정렬하면 안 된다.** 매출액 순으로 재정렬하면
        #   와이드 스티커 → 포토카드 → 와이드 스티커 … 로 뒤섞여 묶음이 깨진다
        #   (country_detail 이 이미 '항목 순 → 항목 안에서 매출 순' 으로 정렬해 둔다).
        rows = (_d if "구분" in _d.columns
                else _d.sort_values("매출액", ascending=False)).to_dict("records")
        tbl, krw, setl, qty = _country_table(rows, QTY_LABEL[b],
                                             _b_pct(b), _b_rs(b), _b_cc(b),
                                             ctx.get("rates") or {})
        calc[b] = {"rows": rows, "tbl": tbl, "krw": krw, "set": setl, "qty": qty}
    base = sum(c["krw"] for c in calc.values())
    total = sum(c["set"] for c in calc.values())
    cancel = sum(int(v or 0) for v in (ctx.get("cancel") or {}).values())

    def foot(n, tot=4):
        return (f'<div class="foot"><span>IP 정산서 · {ip} · {ym}'
                + (f' · {docno}' if docno else '')
                + f'</span><span>{n} / {tot}</span></div>')

    # ── 1p ────────────────────────────────────────────────────────────
    # ★'오픈 · 매출발생' 열은 뺐다(요청). 같은 정보가 상세 장 머리에 이미 있다.
    #   대신 정산액 옆에 **공급가액·부가세를 항상 쪼개서** 적는다 — 세금계산서를
    #   끊을 때 그대로 쓰는 숫자라 문서에서 바로 읽혀야 한다.
    brand_rows = ""
    for b in used:
        s1, v1 = vat3(calc[b]["set"], use_vat)
        brand_rows += (f'<tr><td>{BRAND_LABEL[b]}</td>'
                       f'<td>{_f(calc[b]["qty"])}</td>'
                       f'<td>{_f(calc[b]["krw"])}</td>'
                       f'<td><small>{_b_pct(b)}</small></td>'
                       f'<td>{_f(calc[b]["set"])}</td>'
                       f'<td class="dim">{_f(s1)}</td>'
                       f'<td class="dim">{_f(v1)}</td></tr>')
    if multi:
        st_, vt_ = vat3(total, use_vat)
        brand_rows += (f'<tr class="sum"><td>합계</td><td></td>'
                       f'<td>{_f(base)}</td><td></td><td>{_f(total)}</td>'
                       f'<td>{_f(st_)}</td><td>{_f(vt_)}</td></tr>')

    # 표지의 환율 그리드는 뺐다(요청) — 국가별 전체 환율이 부록에 있어 중복이었다.
    # 부가세 — 정산액이 포함인지 별도인지 문서에 못박고, 금액도 함께 적는다.
    sup, vat_amt = vat3(total, use_vat)
    vat_word = "포함" if use_vat else "없음"
    # 괄호 안 금액은 바로 위 hero 박스(공급가액·부가세)와 중복이라 뺐다(1차 피드백 v2).
    vat_note = ("정산액은 부가세 포함 금액입니다." if use_vat
                else "부가세는 적용하지 않았습니다.")

    ver = int(ctx.get("version") or 1)
    badges = f'<span class="badge">{ym} 정산분</span>'
    if ver > 1:
        badges += f'<span class="badge sample">정정본 v{ver}</span>'
    if sample:
        badges += '<span class="badge sample">샘플 · 검토용</span>'
    if marks:
        badges += '<span class="badge mkv">변경 표시본</span>'

    # 이번 판(2차 피드백)에서 바뀐 자리들. marks=False 면 전부 빈 문자열이 된다.
    # 지난 판에서만 바뀐 자리는 False 로 꺼 둔다 — 표시본은 '이번에 뭐가 바뀌었나'만 보여야 한다.
    k_pd, b_pd = _mk("변경 · 띄어쓰기 '정산 기간'", marks)
    k_hero, b_hero = _mk("변경 · '부가세(포함)' 붙여쓰기", marks)
    k_nt, b_nt = _mk("변경 · 각주 괄호 금액 삭제", marks)
    k_qty, b_qty = _mk("변경 · 캡션(단위·산식·부가세) 삭제", marks)
    k_mx, b_mx = _mk("변경 · 왼쪽 설명 삭제 · '매출 단위: 천원'", marks)
    k_fx, b_fx = _mk("변경 · '환산하였습니다'로 통일", marks)
    k_to, b_to = _mk("", False)
    k_in, b_in = _mk("", False)
    k_sum, b_sum = _mk("", False)
    k_ch, b_ch = _mk("", False)
    k_pr, b_pr = _mk("", False)

    sale = ctx.get("tickets", {}).get(used[0]) if used else None
    sale_txt = ""
    if sale:
        s0, e0 = sale.get("startdate") or "—", sale.get("duedate") or "—"
        ing = ""
        try:
            ing = " (진행 중)" if date.fromisoformat(e0) > date.today() else ""
        except (TypeError, ValueError):
            ing = ""
        sale_txt = f" · 판매기간 {s0} ~ {e0}{ing}"

    # ★★티켓번호를 문서에 적는다 (2026-09-03).
    #   전엔 `ticket_keys` 를 **만들어 놓고 어디에도 안 찍었다**(HTML 전체에 티켓번호
    #   0회). 한 타이틀을 티켓 여러 장으로 정산하는 경우가 있는데
    #   (더윈드 = `폴라릿`(31888) + `스티커 프레임, 포토카드`(31552)),
    #   문서만 봐서는 **어느 계약을 덮는지 알 수가 없다.** 받는 쪽도, 나중에 우리도.
    #   금액은 타이틀 기준이라 이미 다 들어가 있다 — 빠진 건 '무엇을 덮었나' 라는 사실뿐.
    _tks = []
    for _b in used:
        for _k in ((ctx.get("tickets", {}).get(_b) or {}).get("ticket_keys") or []):
            if _k not in _tks:
                _tks.append(_k)
    if _tks:
        sale_txt += " · 티켓 " + ", ".join(_tks)

    # 장 순서: 표지 → 브랜드 상세 → 별첨(브랜드마다 한 장) → 부록(환율·단가).
    # 멤버별 내역은 상대가 바로 보는 값이라 앞으로, 환율·단가는 참고자료라 맨 뒤로.
    # 별첨은 D-3(국가마다 2행)이라 국가가 많으면 면을 나눈다 → 면 수를 미리 센다.
    _n_annex = 0
    for _b in used:
        _p = ctx["pivots"].get(_b)
        if _p is not None and not _p.empty:
            _n_annex += -(-len(_p) // ANNEX_PER_PAGE)
    NPAGES = 2 + len(used) + _n_annex

    html = [f"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<link rel="stylesheet" href="{PRETENDARD}"><style>{CSS}</style></head><body>
<div class="page">
  <div class="hd">
    <div class="co">{iss.get('company','')}<small>{iss.get('company_en','')}</small></div>
    <div class="meta{k_to}">{b_to}
      {'<div class="docno num">' + docno + '</div>' if docno else ''}
      <div class="to"><span>To.</span><b>{recv or (ip + ' 정산 담당')}</b></div>
    </div>
  </div>
  <div style="margin-top:20px">
    {badges}
    <div class="title">{ip}<span>IP 정산서</span></div>
    <div class="subtitle num{k_pd}">{b_pd}정산 기간 {S} ~ {E}{sale_txt}</div>
  </div>
  <div class="intro{k_in}">{b_in}{ip}의 {ym} 정산 내역을 아래와 같이 송부드립니다.
   {prose + ' 두 브랜드의 매출을 합산하였으며' if multi else prose + ' 매출 기준이며'},
   국가별 상세 내역과 멤버별 수량은 다음 장에 기재하였습니다.</div>
  <div class="hero">
    <div class="l">
      <div class="k">정산액 · {used_label}</div>
      <div class="v num">{_f(total)}원</div>
    </div>
    <div class="r num{k_hero}">{b_hero}
      <div><span>매출액</span><b>{_f(base)}원</b></div>
      <div><span>요율</span><b>{rate_pct}</b></div>
      <div class="vl"><span>공급가액</span><b>{_f(sup)}원</b></div>
      <div><span>부가세({vat_word})</span><b>{_f(vat_amt)}원</b></div>
    </div>
  </div>
  <div class="sec{k_sum}">{b_sum}
    <h3>{'브랜드별 요약' if multi else '요약'}<small>단위: 원 · 부가세 {vat_word}</small></h3>
    <table class="t1 num">
      <tr><th>브랜드</th><th>수량</th><th>매출(KRW)</th>
      <th>요율</th><th>정산액</th><th>공급가액</th><th>부가세</th></tr>
      {brand_rows}
    </table>
  </div>
  <div class="notes{k_nt}">{b_nt}
    · 해외 매출은 정산 종료일 기준 매매기준율로 원화 환산하여 합산합니다.<br>
    · {vat_note}
    {'<br>· 본 문서는 레이아웃 검토용 샘플이며 요율·환율 등 일부 수치는 예시값입니다.' if sample else ''}
  </div>
  {foot(1, NPAGES)}
</div>"""]

    # ── 2p·3p 브랜드별 상세 ────────────────────────────────────────────
    for i, b in enumerate(used):
        c = calc[b]
        # 비중 그래프는 **나라 단위**다. 항목별로 쪼갠 행을 그대로 넘기면 같은
        # 나라가 여러 번 나와 범례에 '한국'이 두 줄로 찍힌다(실제로 그랬다).
        _agg = {}
        for r in c["rows"]:
            k = r["국가"]
            _agg[k] = {"국가": k, "unit": r["unit"],
                       "매출액": _agg.get(k, {}).get("매출액", 0) + int(r["매출액"])}
        # ★0 이하는 **나라별로 합친 뒤에** 뺀다 (2026-08-19 수정). 전엔 행 단위로
        #   먼저 뺐는데, 스내피즘처럼 한 나라가 항목별로 여러 줄이면 음수 항목만
        #   사라져 그 나라가 부풀어 올랐다.
        pos = sorted((v for v in _agg.values() if v["매출액"] > 0),
                     key=lambda r: -r["매출액"])
        # ★★분모는 **실제로 그린 것의 합**이다. 전엔 c["krw"](음수 국가까지 든 총합)를
        #   썼다 — 분자에선 음수를 빼고 분모엔 남겨서 **비중 합이 100%를 넘었다.**
        #   실측: 260601 FT아일랜드 2026-07 = 한국 -14,000 · 나머지 +46,115 →
        #   분모 32,115 로 143.6% (도넛이 한 바퀴를 넘어 그려진다).
        #   음수는 취소만 남은 나라에서 난다(2025년 이후 5칸 · 전부 한국).
        _pos_tot = sum(v["매출액"] for v in pos)
        bars = (CHARTS.get(chart) or _share_chart)(pos, _pos_tot)
        d = ctx["details"][b]
        top = pos[0] if pos else None
        bs, bv = vat3(c["set"], use_vat)
        # KPI 4칸은 뺐다(요청) — 같은 숫자가 표지 요약·국가별 소계에 이미 있다.
        # 비중 그래프는 칸을 넘겨받아 전폭으로 그린다.
        html.append(f"""<div class="page">
  {_page_head(f'상세 내역 {"①②"[i]}', BRAND_LABEL[b],
              (ctx['titles'].get(b) or [''])[0], '')}
  <div class="chartwide{k_ch}">{b_ch}{bars}</div>
  <div class="sec{k_qty}">{b_qty}
    <h3>국가별 내역</h3>
    {c['tbl']}
  </div>
  {foot(2 + i, NPAGES)}
</div>""")

    # ── 별첨: 국가 × 멤버 (수량 · 매출) ─────────────────────────────────
    # 멤버 열은 **브랜드마다 따로** 잡는다 — 장을 나눴는데 열까지 합치면 그 브랜드에
    # 없는 멤버가 '—' 로 줄줄이 붙어 폭만 먹는다.
    def _members(b):
        p = ctx["pivots"].get(b)
        return sorted(p.columns) if p is not None and not p.empty else []
    # ★별첨 합계는 국가별 내역 소계와 다를 수 있다.
    #   국가별은 '국가 분자 ÷ 국가 평균단가'를 내림하고, 별첨은 '멤버 분자 ÷ 멤버
    #   평균단가'를 멤버마다 내림해 더하기 때문에 내림이 여러 번 일어난다.
    #   같은 문서에 두 숫자가 다르면 오해받으므로 별첨 머리에는 **별첨 합계**를 적고
    #   차이가 나면 산출 방식에 그 이유를 밝힌다.
    # ★한 칸에 수량+매출 두 줄이 들어가면서 표 높이가 두 배가 됐다 → 두 브랜드를
    #   한 장에 넣으면 넘쳐서 잘린다. **브랜드마다 한 장**으로 뺀다.
    annex = [b for b in used
             if ctx["pivots"].get(b) is not None and not ctx["pivots"][b].empty]
    _pno = 2 + len(used)
    for j, b in enumerate(annex):
        p = ctx["pivots"][b]
        rv = (ctx.get("pivots_rev") or {}).get(b)
        mem = _members(b)
        tot_q = int(p.values.sum())
        gap = calc[b]["qty"] - tot_q
        # 산출 방식 박스는 뺐다(요청). 다만 별첨 합계가 국가별 소계와 다르면 그 이유는
        # 반드시 남겨야 오해가 없다 → 별첨 표 아래 한 줄로만 붙인다.
        note = (f'<div class="zeroline" style="margin-top:10px">별첨 수량은 멤버 '
                f'단위로 각각 내림하여 산출하므로 국가별 내역 소계보다 '
                f'{_f(gap)}{QTY_UNIT[b]} 적습니다. 정산액은 국가별 내역 기준입니다.'
                '</div>' if gap else "")
        # 합계는 면을 나눠도 **전체 기준 한 번**만. 여기서 미리 계산해 마지막 면에 넘긴다.
        tot = [int(p[m].sum()) if m in p.columns else 0 for m in mem]
        tot_r = ([int(rv[m].sum()) if rv is not None and m in rv.columns else 0
                  for m in mem] if rv is not None and not rv.empty else [])
        # 면 수를 먼저 정하고 **균등하게** 쪼갠다 — 22+5 처럼 뒷면이 텅 비면
        # 잘린 것처럼 보인다(27개국이면 14+13).
        _np = max(1, -(-len(p) // ANNEX_PER_PAGE))
        _per = -(-len(p) // _np)
        chunks = [p.iloc[k:k + _per] for k in range(0, len(p), _per)] or [p]
        for ci, ch in enumerate(chunks):
            last = ci == len(chunks) - 1
            cont = f' <span style="font-weight:500">({ci + 1}/{len(chunks)})</span>' \
                   if len(chunks) > 1 else ""
            html.append(f"""<div class="page">
  {_page_head('별첨' + (f' {"①②"[j]}' if len(annex) > 1 else ''),
              BRAND_LABEL[b], '국가 × 멤버', '')}
  <div class="sec{k_mx}">{b_mx}
    <div class="sechead"><h3>멤버별 내역{cont}</h3>
      <span class="unit num">매출 단위: 천원</span></div>
    {_matrix(ch, mem, rv, totals=(tot, tot_r) if last else None)}
  </div>
  {note if last else ''}
  {foot(_pno, NPAGES)}
</div>""")
            _pno += 1

    # ── 마지막 장: 부록(국가별 적용 환율 · 단가) ─────────────────────────
    # ★단가표를 상세 장 밑에 붙이면 국가가 많을 때 페이지 밖으로 밀려 **조용히
    #   잘린다**(.page 가 height 고정 + overflow:hidden). 환율표 요청도 같이 와서
    #   한 장으로 뺐다 — 잘릴 일도 없고 참고표가 한곳에 모인다.
    order = []
    for bb in used:
        for r in calc[bb]["rows"]:
            if r["국가"] not in order:
                order.append(r["국가"])
    price = {bb: ctx["prices"].get(bb) or {} for bb in used}
    items = [(nat, ctx["units"].get(nat, ""),
              {bb: price[bb].get(nat) or {} for bb in used}) for nat in order]
    # 미고시 통화 안내 — 출처만 한 줄로. 산식까지 적었더니 과하다는 피드백이 있었다.
    _rt = ctx.get("rates") or {}
    _unl = [c for c in FX_UNLISTED
            if c in {str(it[1]).strip().upper() for it in items} and _rt.get(c)]
    _nm = {"LAK": "라오스", "PEN": "페루"}
    _unl_note = (
        '<div class="fxnote">'
        + " · ".join(f"{_nm.get(c, c)}({c})" for c in _unl)
        + '는 매매기준율 미고시로, 같은 기준일의 '
          '<b>야후 파이낸스(Yahoo Finance)</b> 환율을 기준으로 환산하였습니다.'
          '</div>') if _unl else ""
    html.append(f"""<div class="page">
  {_page_head('부록', '국가별 적용 환율 · 단가', used_label,
              f'{len(items)}개국 · 기준일 <b>{ctx["rate_date"]}</b>')}
  <div class="sec{k_fx}">{b_fx}
    <h3>국가별 적용 환율<small>{ctx.get('rate_source') or '서울외국환중개 매매기준율'}
     · {ctx['rate_date']} · 현지통화 1단위당 원</small></h3>
    {_fx_table(items, ctx.get('rates') or {})}
    {_unl_note}
  </div>
  <div class="sec{k_pr}">{b_pr}
    <h3>국가별 단가<small>현지통화 · 기간 내 평균</small></h3>
    {_price_table([it for it in items if any(it[2].values())], used)}
  </div>
  {foot(NPAGES, NPAGES)}
</div>
</body></html>""")
    return "".join(html)


def verify_secrecy(html: str, other_rate) -> list[str]:
    """상대방 요율이 문서에 남아 있지 않은지 확인. 스펙 절대 규칙 2번.

    ★style 속성과 <style> 블록은 걷어내고 본다 — 막대 너비 `width:10.0%` 나
      `letter-spacing:-.01em` 같은 CSS 수치가 요율로 오인되면 매번 헛경보가 난다.
    """
    import re as _re
    if not other_rate:
        return []
    body = _re.sub(r"<style>.*?</style>", " ", html, flags=_re.S)
    body = _re.sub(r'\sstyle="[^"]*"', " ", body)
    body = _re.sub(r"<[^>]+>", " ", body)          # 태그 제거 → 보이는 텍스트만
    pcts = {f"{other_rate * 100:g}%", f"{other_rate * 100:.1f}%",
            f"{other_rate:.2f}"}
    return [p for p in pcts if p in body]


def _render_to_file(html_path: str, out_path: str, footer: str) -> None:
    """실제 인쇄. **별도 프로세스에서만** 호출된다(아래 __main__ 참고).
    푸터는 HTML 안에 있으므로 브라우저 머리말/꼬리말은 쓰지 않는다."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page()
        page.goto(Path(html_path).resolve().as_uri(), wait_until="networkidle")
        page.pdf(path=out_path, format="A4", print_background=True,
                 margin={"top": "0", "bottom": "0", "left": "0", "right": "0"})
        b.close()


def render_pdf(html: str, footer: str = "", timeout: int = 240) -> bytes:
    """Playwright chromium 으로 인쇄해 바이트로 돌려준다.

    ★반드시 **별도 프로세스**에서 돌린다. Streamlit 서버(Tornado)가 Windows 에서
      asyncio 정책을 SelectorEventLoop 로 바꿔 놓는데 그 루프는 서브프로세스를
      못 띄운다 → chromium 실행 순간 NotImplementedError. 정책은 프로세스 전역이라
      스레드로는 못 피한다.
    """
    tmp = Path(tempfile.mkdtemp(prefix="settle_pdf_"))
    try:
        hp, op = tmp / "doc.html", tmp / "out.pdf"
        hp.write_text(html, encoding="utf-8")
        r = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()),
             "--render", str(hp), str(op), footer],
            capture_output=True, timeout=timeout)
        if not op.exists() or op.stat().st_size == 0:
            msg = (r.stderr or b"").decode("utf-8", "replace").strip()
            raise RuntimeError("PDF 생성 실패" + (f" — {msg[-500:]}" if msg else ""))
        return op.read_bytes()
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"PDF 생성이 {timeout}초 안에 끝나지 않았어요.")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    if len(sys.argv) >= 4 and sys.argv[1] == "--render":
        _render_to_file(sys.argv[2], sys.argv[3],
                        sys.argv[4] if len(sys.argv) > 4 else "")
