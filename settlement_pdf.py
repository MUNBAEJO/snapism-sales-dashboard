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
QTY_LABEL = {"photoism": "프레임수", "snapism": "건수"}
QTY_UNIT = {"photoism": "프레임", "snapism": "건"}
# 100단위로 고시하는 통화 — 표기만 100배로 하고 ' /100' 을 붙인다.
FX_100 = {"JPY", "IDR", "VND", "LAK", "MNT"}
# 표지 '적용 환율' 그리드에 올릴 통화(8칸)
FX_SHOW = ["CNY", "JPY", "IDR", "TWD", "MYR", "THB", "HKD", "USD"]


def _f(n) -> str:
    try:
        return f"{int(round(float(n))):,}"
    except (TypeError, ValueError):
        return "—"


def rhe(x) -> int:
    """원 미만 은행가 반올림. 파이썬 round() 가 half-to-even 이라 그대로 쓴다."""
    return int(round(float(x)))


def _fx_cell(unit: str, local, krw) -> str:
    """적용 환율 = 매출KRW ÷ 현지매출. KRW 행은 '—'."""
    u = str(unit or "").strip().upper()
    if u == "KRW" or not local:
        return "—"
    v = krw / local * (100 if u in FX_100 else 1)
    return f"{v:,.2f}" + (" <small>/100</small>" if u in FX_100 else "")


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


def _country_table(rows, qty_label, rate_pct, rs):
    """국가 | 통화 | 수량 | 현지 매출 | 적용 환율 | 매출(KRW) | 요율 | 정산액 | 비중

    ★소계 정산액은 각 행 정산액의 합. 기준액×요율로 내면 1원 어긋난다.
    """
    tot_krw = sum(r["매출액"] for r in rows) or 1
    tq = tk = ts = 0
    # 한 페이지에 들어갈 만큼 자동으로 조인다(기본 27행까지 여유).
    dens = " d2" if len(rows) > 33 else (" d1" if len(rows) > 26 else "")
    h = (f'<table class="t2 num{dens}"><tr><th>국가</th><th>통화</th>'
         f'<th>{qty_label}</th><th>현지 매출</th><th>적용 환율</th>'
         '<th>매출(KRW)</th><th>요율</th><th>정산액</th>'
         '<th>비중</th></tr>')
    for r in rows:
        krw, loc, q = int(r["매출액"]), int(r["현지"]), int(r["수량"])
        amt = rhe(krw * rs) if rs else 0
        tq += q; tk += krw; ts += amt
        p = krw / tot_krw * 100
        zero = krw == 0
        dash = '<span style="color:#c3c9d4">—</span>'
        h += (f'<tr><td>{r["국가"]}'
              + ('<small style="font-weight:500"> 매출 없음</small>' if zero else '')
              + f'</td><td class="cur">{r["unit"]}</td>'
              f'<td>{_f(q) if q else dash}</td>'
              f'<td class="dim">{_f(loc) if loc else dash}</td>'
              f'<td class="dim">{_fx_cell(r["unit"], loc, krw) if krw else dash}</td>'
              f'<td>{_f(krw) if krw else dash}</td>'
              f'<td class="dim">{rate_pct}</td>'
              f'<td>{"<b>" + _f(amt) + "</b>" if amt else dash}</td>'
              '<td><span class="sharecell"><span class="bar">'
              f'<i style="width:{max(p, 1.2):.1f}%"></i></span>{p:.1f}%</span>'
              '</td></tr>')
    h += (f'<tr class="sum"><td>소계</td><td></td><td>{_f(tq)}</td><td></td>'
          f'<td></td><td>{_f(tk)}</td><td></td><td>{_f(ts)}</td><td></td>'
          '</tr></table>')
    return h, tk, ts, tq


def _matrix(piv, members):
    if piv is None or piv.empty:
        return ""
    tot = [0] * len(members)
    h = ('<table class="t3 num"><tr><th>국가</th>'
         + "".join(f"<th>{m}</th>" for m in members) + "<th>합계</th></tr>")
    for nat, row in piv.iterrows():
        vals = [int(row.get(m, 0) or 0) for m in members]
        tot = [a + b for a, b in zip(tot, vals)]
        h += (f"<tr><td>{nat}</td>"
              + "".join(f"<td>{_f(v)}</td>" if v else '<td class="z">—</td>'
                        for v in vals)
              + f"<td>{_f(sum(vals))}</td></tr>")
    h += ('<tr class="sum"><td>합계</td>'
          + "".join(f"<td>{_f(v)}</td>" for v in tot)
          + f"<td>{_f(sum(tot))}</td></tr></table>")
    return h


def _price_table(items):
    h = ('<table class="t4 num"><tr><th>국가</th><th>통화</th>'
         '<th>포토이즘 프레임</th><th>스내피즘</th></tr>')
    for nat, unit, ph, sn in items:
        sn_c = (_f(list(sn.values())[0]) if sn
                else '<span style="color:#c3c9d4">—</span>')
        h += (f'<tr><td>{nat}</td><td class="cur">{unit}</td>'
              f'<td>{_f(ph) if ph else "—"}</td><td>{sn_c}</td></tr>')
    return h + "</table>"


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
.kpi .v{font-size:14px;font-weight:800;margin-top:3px;white-space:nowrap}
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
.pricegrid{display:grid;grid-template-columns:1fr 1fr;gap:0 24px}
.t4 th{font-size:10px;font-weight:700;color:var(--text-2);background:var(--surface-2);
border-top:1.5px solid var(--ink);border-bottom:1px solid var(--border-2);
padding:6px 8px;text-align:right;white-space:nowrap}
.t4 th:first-child{text-align:left}
.t4 td{font-size:10.5px;padding:4.5px 8px;border-bottom:1px solid var(--border);
text-align:right;white-space:nowrap}
.t4 td:first-child{text-align:left;font-weight:700}
.t4 td.cur{color:var(--text-3);font-size:9.5px;text-align:center}
.method{margin-top:16px;background:var(--surface-2);border-radius:10px;
padding:12px 16px;font-size:11px;color:var(--text-2);line-height:1.85}
.method b{color:var(--text)}
"""


def build_html(ctx: dict, kind: str, sample: bool = True) -> str:
    """kind='agency'(소속사) | 'mgmt'(대행사). **자기 요율만** 문서에 넣는다."""
    # ★수취처 구분(소속사/대행사)은 **문서에 쓰지 않는다.** 요율이 다른 두 부를
    #   따로 만들지만 그건 내부 사정이고, 받는 쪽은 자기 정산서만 보면 된다.
    #   구분은 파일명·문서번호(L/A)에만 남긴다.
    party = "소속사" if kind == "agency" else "대행사"      # 내부용
    rs_key = "agency" if kind == "agency" else "mgmt"
    rs = next((v for v in ((ctx["rs"].get(b) or {}).get(rs_key)
                           for b in ctx["details"]) if v), None)
    rate_pct = f"{rs * 100:g}%" if rs else "—"
    ip, S, E = ctx["ip"], ctx["start"], ctx["end"]
    ym = f"{int(E[:4])}년 {int(E[5:7])}월"
    docno = ctx.get("docno") or ""
    iss = ctx.get("issuer") or {}
    _p = ctx.get("partner") or {}
    recv = (_p.get("agency_name") if kind == "agency" else _p.get("mgmt_name")) or ""
    use_vat = bool(_p.get("vat", True))

    used = [b for b in ("photoism", "snapism")
            if ctx["details"].get(b) is not None and not ctx["details"][b].empty]
    used_label = " + ".join(BRAND_LABEL[b] for b in used)
    multi = len(used) > 1

    # ── 브랜드별 계산 (행 단위 반올림 → 합) ──────────────────────────────
    calc = {}
    for b in used:
        rows = ctx["details"][b].sort_values("매출액", ascending=False) \
            .to_dict("records")
        tbl, krw, setl, qty = _country_table(rows, QTY_LABEL[b],
                                             rate_pct, rs)
        calc[b] = {"rows": rows, "tbl": tbl, "krw": krw, "set": setl, "qty": qty}
    base = sum(c["krw"] for c in calc.values())
    total = sum(c["set"] for c in calc.values())
    cancel = sum(int(v or 0) for v in (ctx.get("cancel") or {}).values())

    def foot(n, tot=4):
        return (f'<div class="foot"><span>IP 정산서 · {ip} · {ym}'
                + (f' · {docno}' if docno else '')
                + f'</span><span>{n} / {tot}</span></div>')

    # ── 1p ────────────────────────────────────────────────────────────
    brand_rows = ""
    for b in used:
        d = ctx["details"][b]
        opened, earned = len(d), int((d["매출액"] > 0).sum())
        brand_rows += (f'<tr><td>{BRAND_LABEL[b]}</td>'
                       f'<td>{opened}개국 · {earned}개국</td>'
                       f'<td>{_f(calc[b]["qty"])} <small>{QTY_UNIT[b]}</small></td>'
                       f'<td>{_f(calc[b]["krw"])}</td>'
                       f'<td><small>{rate_pct}</small></td>'
                       f'<td>{_f(calc[b]["set"])}</td></tr>')
    if multi:
        brand_rows += (f'<tr class="sum"><td>합계</td><td></td><td></td>'
                       f'<td>{_f(base)}</td><td></td><td>{_f(total)}</td></tr>')

    fxr = ctx.get("rates") or {}
    fx_cells = ""
    for cur in FX_SHOW:
        v = fxr.get(cur)
        if not v:
            continue
        shown = v * 100 if cur in FX_100 else v
        fx_cells += (f'<div><span>{cur}{" (100)" if cur in FX_100 else ""}</span>'
                     f'<b>{shown:,.2f}</b></div>')

    # 부가세 — 정산액이 포함인지 별도인지 문서에 못박는다.
    sup, vat_amt = ((round(total / 1.1), total - round(total / 1.1))
                    if use_vat else (total, 0))
    vat_note = (f"정산액은 부가세 포함 금액입니다 (공급가액 {_f(sup)}원 · "
                f"부가세 {_f(vat_amt)}원)." if use_vat
                else "정산액은 부가세 별도 금액입니다.")

    ver = int(ctx.get("version") or 1)
    badges = f'<span class="badge">{ym} 정산분</span>'
    if ver > 1:
        badges += f'<span class="badge sample">정정본 v{ver}</span>'
    if sample:
        badges += '<span class="badge sample">샘플 · 검토용</span>'

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

    guide_no = {2: "② 포토이즘 국가별 내역", 3: "③ 스내피즘 국가별 내역 및 국가별 단가"}
    parts = []
    for i, b in enumerate(used):
        parts.append(f"{'②③'[i]} {BRAND_LABEL[b]} 국가별 내역"
                     + (" 및 국가별 단가" if i == len(used) - 1 else ""))
    parts.append(f"{'②③④'[len(used)]} 별첨(국가 × 멤버 수량)")

    html = [f"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<link rel="stylesheet" href="{PRETENDARD}"><style>{CSS}</style></head><body>
<div class="page">
  <div class="hd">
    <div class="co">{iss.get('company','')}<small>{iss.get('company_en','')}</small></div>
    <div class="meta">{'문서번호 <b>' + docno + '</b> · ' if docno else ''}발행일 <b>{ctx['issued']}</b><br>
    발행: {iss.get('company','')} {iss.get('team','')}
    {'(담당 ' + iss.get('name','') + (' · ' + iss.get('email','') if iss.get('email') else '') + ')' if iss.get('name') or iss.get('email') else ''}<br>
    수신: {recv + ' ' if recv else ''}{ip} 정산 담당</div>
  </div>
  <div style="margin-top:20px">
    {badges}
    <div class="title">{ip}<span>IP 정산서</span></div>
    <div class="subtitle num">정산기간 {S} ~ {E}{sale_txt}</div>
  </div>
  <div class="intro">{ip}의 {ym} 정산 내역을 아래와 같이 송부드립니다.
   {'포토이즘·스내피즘 두 브랜드의 매출을 합산하였으며' if multi else used_label + ' 매출 기준이며'},
   국가별 상세 내역과 멤버별 수량은 다음 장에 기재하였습니다.</div>
  <div class="hero">
    <div class="l">
      <div class="k">정산액 · {used_label}</div>
      <div class="v num">{_f(total)}원</div>
    </div>
    <div class="r num">
      <div><span>정산기준액 (취소 제외)</span><b>{_f(base)}원</b></div>
      <div><span>요율</span><b>{rate_pct}</b></div>
      <div><span>취소 금액</span><b>{_f(cancel)}원</b></div>
      <div><span>부가세</span><b>{'포함' if use_vat else '별도'}</b></div>
    </div>
  </div>
  <div class="sec">
    <h3>{'브랜드별 요약' if multi else '요약'}<small>단위: 원</small></h3>
    <table class="t1 num">
      <tr><th>브랜드</th><th>오픈 · 매출발생</th><th>수량</th><th>매출(KRW)</th>
      <th>요율</th><th>정산액</th></tr>
      {brand_rows}
    </table>
  </div>
  <div class="sec">
    <h3>적용 환율<small>서울외국환중개 매매기준율 · {ctx['rate_date']} · KRW 기준</small></h3>
    <div class="fxgrid num">{fx_cells}</div>
    <div class="fxnote">국가별 전체 적용 환율은 국가별 내역의 '적용 환율' 열에 기재하였습니다.</div>
  </div>
  <div class="guide">
    <b>이 문서의 구성</b> — {' · '.join(parts)}<br>
    본 정산서에 대한 이의는 수신일로부터 7영업일 이내에 회신하여 주시기 바랍니다.
    기한 내 회신이 없는 경우 확정 처리됩니다.
  </div>
  <div class="notes">
    · 매출액은 취소분을 제외한 실판매 금액입니다. &nbsp;· 매출이 발생하지 않은 오픈 국가도
    국가별 내역에 함께 기재하였습니다.<br>
    · 해외 매출은 정산 종료일 기준 매매기준율로 원화 환산하여 합산합니다.<br>
    · {vat_note}
    {'<br>· 본 문서는 레이아웃 검토용 샘플이며 요율·환율 등 일부 수치는 예시값입니다.' if sample else ''}
  </div>
  {foot(1, 2 + len(used))}
</div>"""]

    # ── 2p·3p 브랜드별 상세 ────────────────────────────────────────────
    for i, b in enumerate(used):
        c = calc[b]
        pos = [r for r in c["rows"] if r["매출액"] > 0]
        bars = _share_chart(pos, c["krw"])
        d = ctx["details"][b]
        top = pos[0] if pos else None
        price_sec = ""
        if i == len(used) - 1:                     # 마지막 브랜드 장에 단가표
            order, items = [], []
            for bb in used:
                for r in calc[bb]["rows"]:
                    if r["국가"] not in order:
                        order.append(r["국가"])
            ph = ctx["prices"].get("photoism", {})
            sn = ctx["prices"].get("snapism", {})
            for nat in order:
                if nat not in ph and nat not in sn:
                    continue
                pv = ph.get(nat) or {}
                items.append((nat, ctx["units"].get(nat, ""),
                              list(pv.values())[0] if pv else 0, sn.get(nat) or {}))
            half = (len(items) + 1) // 2
            price_sec = (f'<div class="sec"><h3>국가별 단가'
                         f'<small>현지통화 · 평균 · {len(items)}개국</small></h3>'
                         f'<div class="pricegrid">{_price_table(items[:half])}'
                         f'{_price_table(items[half:])}</div></div>')
        html.append(f"""<div class="page">
  {_page_head(f'상세 내역 {"①②"[i]}', BRAND_LABEL[b],
              (ctx['titles'].get(b) or [''])[0],
              f'오픈 <b>{len(d)}개국</b> 중 매출발생 <b>{len(pos)}개국</b>')}
  <div class="duo">
    <div class="donutbox">{bars}</div>
    <div class="kpis num">
      <div class="kpi"><div class="k">매출(KRW)</div><div class="v">{_f(c['krw'])}원</div></div>
      <div class="kpi hl"><div class="k">정산액</div><div class="v">{_f(c['set'])}원</div></div>
      <div class="kpi"><div class="k">{QTY_LABEL[b]}</div><div class="v">{_f(c['qty'])}</div></div>
      <div class="kpi"><div class="k">1위 국가</div><div class="v">{
        (top['국가'] + ' ' + f"{top['매출액'] / c['krw'] * 100:.1f}%") if top else '—'}</div></div>
    </div>
  </div>
  <div class="sec">
    <h3>국가별 내역<small>단위: 원 · 정산액 = 매출(KRW) × {rate_pct} (원 미만 반올림)</small></h3>
    {c['tbl']}
  </div>
  {price_sec}
  {foot(2 + i, 2 + len(used))}
</div>""")

    # ── 마지막 장: 별첨 ────────────────────────────────────────────────
    members = sorted({m for p in ctx["pivots"].values()
                      if p is not None and not p.empty for m in p.columns})
    # ★별첨 합계는 국가별 내역 소계와 다를 수 있다.
    #   국가별은 '국가 분자 ÷ 국가 평균단가'를 내림하고, 별첨은 '멤버 분자 ÷ 멤버
    #   평균단가'를 멤버마다 내림해 더하기 때문에 내림이 여러 번 일어난다.
    #   같은 문서에 두 숫자가 다르면 오해받으므로 별첨 머리에는 **별첨 합계**를 적고
    #   차이가 나면 산출 방식에 그 이유를 밝힌다.
    mx, mx_tot, diff = "", {}, []
    for b in used:
        p = ctx["pivots"].get(b)
        if p is None or p.empty:
            continue
        mx_tot[b] = int(p.values.sum())
        mx += (f'<div class="sec"><h3>{BRAND_LABEL[b]} {QTY_LABEL[b]}'
               f'<small>{len(p)}개국</small></h3>{_matrix(p, members)}</div>')
        if mx_tot[b] != calc[b]["qty"]:
            diff.append(f"{BRAND_LABEL[b]} {_f(calc[b]['qty'] - mx_tot[b])}"
                        f"{QTY_UNIT[b]}")
    right = " · ".join(f"{BRAND_LABEL[b]} <b class='num'>"
                       f"{_f(mx_tot.get(b, calc[b]['qty']))} {QTY_UNIT[b]}</b>"
                       for b in used)
    # 산출 방식 박스는 뺐다(요청). 다만 별첨 합계가 국가별 소계와 다르면 그 이유는
    # 반드시 남겨야 오해가 없다 → 별첨 표 아래 한 줄로만 붙인다.
    diff_note = (f'<div class="zeroline" style="margin-top:10px">별첨 수량은 멤버 '
                 f'단위로 각각 내림하여 산출하므로 국가별 내역 소계보다 '
                 f"{' · '.join(diff)} 적습니다. 정산액은 국가별 내역 기준입니다."
                 '</div>' if diff else "")
    html.append(f"""<div class="page">
  {_page_head('별첨', '국가 × 멤버 수량', used_label, right)}
  {mx}
  {diff_note}

  {foot(2 + len(used), 2 + len(used))}
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
