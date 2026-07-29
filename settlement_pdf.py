"""IP 정산서 — 3단계: PDF 생성.

`settlement_calc` 가 낸 값을 HTML 로 조립해 Playwright chromium 으로 인쇄한다.
톤앤매너는 대시보드와 같다(Pretendard · --brand:#4f46e5 · .kpi · .ntbl).

구성 (수취처마다 파일 1개 — 요율이 달라 문서를 나눈다)
  1p 요약 · 인사말 · 브랜드별
  2p 📸 포토이즘  도넛 + 국가별
  3p 📊 스내피즘  도넛 + 국가별
  4p 포토이즘 국가 × 멤버 프레임수
  5p 스내피즘 국가 × 멤버 건수 + 산출 방식
  6p 국가별 단가 (두 브랜드 한 표)

문서 문구는 최소로 둔다. 서비스코인·쿠폰 예외국, 티켓번호, '대행사는 별도 발행'
같은 내부 사정은 적지 않는다.

관련: CURRENT-PROJECTS/IP-정산서-생성.md · 지라 CO-288
"""
from __future__ import annotations

import math
import shutil
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path

import pandas as pd

BASE_DIR = Path(__file__).parent
CSS_PATH = BASE_DIR / "assets" / "settlement.css"
PRETENDARD = ("https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9"
              "/dist/web/variable/pretendardvariable-dynamic-subset.min.css")

PAL = ["#6366f1", "#b45309", "#0f9d77", "#d24d8b", "#38a3e8", "#7c77ee",
       "#c98a2e", "#5f6b7a"]

BRAND_LABEL = {"photoism": "포토이즘", "snapism": "스내피즘"}
BRAND_ICON = {"photoism": "📸", "snapism": "📊"}
# 수량은 같은 공식으로 뽑되 부르는 이름이 다르다.
QTY_LABEL = {"photoism": "프레임수", "snapism": "건수"}
QTY_UNIT = {"photoism": "프레임", "snapism": "건"}

# ★두 브랜드 표가 같은 격자를 써야 세로선이 안 어긋난다.
GRID = "1.45fr .5fr .8fr 1.15fr 1.2fr 1.2fr 1.3fr"


def _won(v):
    try:
        return f"{int(round(float(v))):,}"
    except (TypeError, ValueError):
        return "—"


def _bar(frac, mx):
    w = 0 if mx <= 0 else min(100, max(2, frac / mx * 100))
    return (f'<div class="npct"><div class="npct-bar"><i style="width:{w:.0f}%">'
            f'</i></div><span class="p">{frac * 100:.1f}%</span></div>')


def _donut(pairs, size=140, hole=0.56):
    """SVG 도넛 — chromium 인쇄에서 conic-gradient 보다 안전하다."""
    tot = sum(v for _, v in pairs) or 1
    r = cx = size / 2
    ang, segs = -math.pi / 2, ""
    for i, (_, v) in enumerate(pairs):
        sweep = v / tot * 2 * math.pi
        x1, y1 = cx + r * math.cos(ang), cx + r * math.sin(ang)
        ang += sweep
        x2, y2 = cx + r * math.cos(ang), cx + r * math.sin(ang)
        segs += (f'<path d="M {cx} {cx} L {x1:.2f} {y1:.2f} A {r} {r} 0 '
                 f'{1 if sweep > math.pi else 0} 1 {x2:.2f} {y2:.2f} Z" '
                 f'fill="{PAL[i % len(PAL)]}"/>')
    segs += f'<circle cx="{cx}" cy="{cx}" r="{r * hole:.1f}" fill="#fff"/>'
    leg = "".join(
        f'<div class="row"><i class="dot" style="background:{PAL[i % len(PAL)]}">'
        f'</i>{n} <b>{v / tot * 100:.1f}%</b></div>'
        for i, (n, v) in enumerate(pairs))
    return (f'<div class="donut-wrap"><svg width="{size}" height="{size}" '
            f'viewBox="0 0 {size} {size}">{segs}</svg>'
            f'<div class="leg2">{leg}</div></div>')


def _pairs(detail: pd.DataFrame, top=6):
    d = detail[detail["매출액"] > 0].sort_values("매출액", ascending=False)
    head = [(r["국가"], int(r["매출액"])) for _, r in d.head(top).iterrows()]
    rest = d.iloc[top:]
    if len(rest):
        head.append((f"기타 {len(rest)}개국", int(rest["매출액"].sum())))
    return head


def _country_table(detail: pd.DataFrame, rs: float | None, label: str,
                   qty: str) -> str:
    tot = int(detail["매출액"].sum()) or 1
    mx = max((int(v) for v in detail["매출액"]), default=0) / tot
    head = (f'<div class="ntr nth" style="grid-template-columns:{GRID}">'
            '<span>국가</span><span class="c">통화</span>'
            f'<span class="r">{qty}</span><span class="r">현지 매출</span>'
            '<span class="r">매출(KRW)</span>'
            f'<span class="r">{label} 정산액</span><span>비중</span></div>')
    body, tq, tr, ta = "", 0, 0, 0
    for _, r in detail.iterrows():
        rev = int(r["매출액"])
        amt = round(rev * rs) if rs else 0
        q, loc = int(r["수량"]), int(r["현지"])
        tq += q; tr += rev; ta += amt
        zero = rev == 0
        cell = (lambda v, cls="": f'<span class="r num {cls}">{_won(v)}</span>'
                if v else '<span class="r dash">—</span>')
        body += (f'<div class="ntr{" z" if zero else ""}" '
                 f'style="grid-template-columns:{GRID}">'
                 f'<span class="nname">{r["국가"]}'
                 + ('<span class="zb">매출 없음</span>' if zero else '')
                 + '</span>'
                 f'<span class="c"><span class="cur">{r["unit"]}</span></span>'
                 f'{cell(q)}{cell(loc, "dim")}{cell(rev)}{cell(amt, "b")}'
                 f'{_bar(rev / tot, mx)}</div>')
    body += (f'<div class="ntr sum" style="grid-template-columns:{GRID}">'
             '<span>소계</span><span></span>'
             f'<span class="r num">{_won(tq)}</span><span></span>'
             f'<span class="r num">{_won(tr)}</span>'
             f'<span class="r num">{_won(ta) if rs else "—"}</span>'
             '<span></span></div>')
    return f'<div class="ntbl">{head}{body}</div>'


def _pivot_table(piv: pd.DataFrame, members: list[str]) -> str:
    """국가 × 멤버. 열은 두 브랜드 합집합으로 고정해 나란히 비교되게 한다."""
    g = "1.15fr " + "1fr " * len(members) + ".9fr"
    h = (f'<div class="ntr nth" style="grid-template-columns:{g}"><span>국가</span>'
         + "".join(f'<span class="r">{m}</span>' for m in members)
         + '<span class="r">합계</span></div>')
    tot = [0] * len(members)
    for nat, row in piv.iterrows():
        vals = [int(row.get(m, 0) or 0) for m in members]
        tot = [a + b for a, b in zip(tot, vals)]
        h += (f'<div class="ntr" style="grid-template-columns:{g}">'
              f'<span class="nname">{nat}</span>'
              + "".join(f'<span class="r num">{_won(v)}</span>' if v
                        else '<span class="r dash">—</span>' for v in vals)
              + f'<span class="r num b">{_won(sum(vals))}</span></div>')
    h += (f'<div class="ntr sum" style="grid-template-columns:{g}"><span>합계</span>'
          + "".join(f'<span class="r num">{_won(v)}</span>' for v in tot)
          + f'<span class="r num">{_won(sum(tot))}</span></div>')
    return f'<div class="ntbl">{h}</div>'


def _price_table(prices: dict, order: list[str], units: dict) -> tuple[str, int]:
    """국가별 단가 — 두 브랜드를 **한 표에 열로**. 쪼개면 페이지 경계에서 잘린다.
    스내피즘은 상품 형태마다 단가가 다르므로 형태명을 함께 적는다."""
    ph, sn = prices.get("photoism", {}), prices.get("snapism", {})
    keys = [k for k in order if k in ph or k in sn]
    keys += [k for k in ({**ph, **sn}) if k not in keys]
    g = "1.35fr .5fr .95fr 1.7fr"
    h = (f'<div class="ntr nth" style="grid-template-columns:{g}">'
         '<span>국가</span><span class="c">통화</span>'
         '<span class="r">📸 포토이즘 <span style="font-weight:600">프레임</span></span>'
         '<span class="r">📊 스내피즘 <span style="font-weight:600">상품 형태별</span>'
         '</span></div>')
    for k in keys:
        pv = ph.get(k) or {}
        p_c = (f'<span class="r num">{_won(list(pv.values())[0])}</span>' if pv
               else '<span class="r dash">—</span>')
        sv = sn.get(k) or {}
        s_c = ('<span class="r">' + " ".join(
            f'<span class="pill">{c}&nbsp;<b>{_won(v)}</b></span>'
            for c, v in sorted(sv.items(), key=lambda x: -x[1])) + '</span>'
            ) if sv else '<span class="r dash">—</span>'
        h += (f'<div class="ntr" style="grid-template-columns:{g}">'
              f'<span class="nname">{k}</span>'
              f'<span class="c"><span class="cur">{units.get(k, "")}</span></span>'
              f'{p_c}{s_c}</div>')
    return f'<div class="ntbl">{h}</div>', len(keys)


def _period(entry) -> tuple[str, str, bool]:
    """지라 티켓의 실제 판매기간. 안 끝났으면 '진행 중'."""
    if not entry:
        return "—", "—", False
    s = entry.get("startdate") or "—"
    d = entry.get("duedate") or "—"
    ongoing = False
    try:
        ongoing = date.fromisoformat(d) > date.today()
    except (TypeError, ValueError):
        ongoing = d == "—"
    return s, d, ongoing


def build_html(ctx: dict, kind: str) -> str:
    """kind='agency'(소속사) | 'mgmt'(대행사)."""
    label = "소속사" if kind == "agency" else "대행사"
    rs_key = "agency" if kind == "agency" else "mgmt"
    css = CSS_PATH.read_text(encoding="utf-8")

    total_base = total_amt = 0
    brand_rows, pages = "", ""
    members = sorted({m for p in ctx["pivots"].values()
                      if p is not None and not p.empty for m in p.columns})

    for b in ("photoism", "snapism"):
        d = ctx["details"].get(b)
        if d is None or d.empty:
            continue
        rs = (ctx["rs"].get(b) or {}).get(rs_key)
        base = int(d["매출액"].sum())
        amt = round(base * rs) if rs else 0
        total_base += base
        total_amt += amt
        opened = len(d)
        earned = int((d["매출액"] > 0).sum())
        s, e, ing = _period(ctx["tickets"].get(b))
        brand_rows += (
            '<div class="ntr" style="grid-template-columns:1.3fr .9fr .9fr 1.1fr 1.1fr">'
            f'<span class="nname">{BRAND_ICON[b]} {BRAND_LABEL[b]}</span>'
            f'<span class="r num">{opened}개국 · <b>{earned}</b>개국</span>'
            f'<span class="r num">{_won(d["수량"].sum())} '
            f'<span class="cur">{QTY_UNIT[b]}</span></span>'
            f'<span class="r num">{_won(base)}</span>'
            f'<span class="r num b">{_won(amt) if rs else "—"}</span></div>')

        pages += f"""
<div class="page">
<div class="eyebrow">{BRAND_ICON[b]} {BRAND_LABEL[b]}</div>
<h2c>{ctx["titles"].get(b, [""])[0] if ctx["titles"].get(b) else ""}</h2c>
<div class="bsum">판매기간 <b>{s} ~ {e}</b>{'<span class="ing">진행 중</span>' if ing else ''}
 &nbsp;·&nbsp; 오픈 <b>{opened}개국</b> 중 매출발생 <b>{earned}개국</b></div>
<div class="bsum">매출 <b>{_won(base)}원</b>{f' &nbsp;·&nbsp; {label} 정산액 <b style="color:var(--brand)">{_won(amt)}원</b>' if rs else ''}</div>
<div class="ct">🍩 국가별 매출 비중</div>
{_donut(_pairs(d))}
<div class="ct">🌏 국가별 내역 <span class="muted">단위 원</span></div>
{_country_table(d, rs, label, QTY_LABEL[b])}
</div>"""

    # 별첨 — 멤버 피벗 (브랜드마다 한 장)
    for b in ("photoism", "snapism"):
        p = ctx["pivots"].get(b)
        if p is None or p.empty:
            continue
        extra = ""
        if b == "snapism":
            extra = f"""
<div class="ct" style="margin-top:14px">ℹ️ 산출 방식</div>
<div class="notes" style="margin-top:0;font-size:8.5px">
<b>국가별 매출액 ÷ 그 국가의 평균 단가</b>를 내림해서 구해요.
매출액·단가 모두 <b>현지통화 기준</b>이에요.<br>
포토이즘은 1건에 여러 장이 나올 수 있어 <b>프레임수</b>로, 스내피즘은 상품 1개가
 1건이라 <b>건수</b>로 적어요.<br>
스내피즘은 상품 형태마다 단가가 달라요.
</div>"""
        pages += f"""
<div class="page">
<div class="eyebrow">[별첨] {BRAND_ICON[b]} {BRAND_LABEL[b]} {QTY_LABEL[b]}</div>
<div class="ct">🖼 국가 × 멤버 {QTY_LABEL[b]}
 <span class="muted">{len(p)}개국</span></div>
{_pivot_table(p, members)}
{extra}
</div>"""

    order = []
    for b in ("photoism", "snapism"):
        d = ctx["details"].get(b)
        if d is not None and not d.empty:
            order += [x for x in d["국가"] if x not in order]
    ptbl, pn = _price_table(ctx["prices"], order, ctx["units"])
    pages += f"""
<div class="page">
<div class="eyebrow">[별첨] 국가별 단가</div>
<div class="ct">💴 국가별 단가
 <span class="muted">현지통화 · 평균 · {pn}개국</span></div>
{ptbl}
</div>"""

    # 세금계산서 발행용 — 총 지급액은 부가세 포함 금액으로 보고 1.1 로 나눈다.
    # (기존 정산서 엑셀과 같은 계산: 3,683,854 → 3,348,958 + 334,896)
    _p2 = ctx.get("partner") or {}
    supply, vat_amt = ((round(total_amt / 1.1), total_amt - round(total_amt / 1.1))
                       if _p2.get("vat", True) else (total_amt, 0))
    _rs_vals = [(ctx["rs"].get(b) or {}).get(rs_key) for b in ctx["details"]]
    _rs_vals = [v for v in _rs_vals if v]
    rs_pct = f"{_rs_vals[0] * 100:.2f}%" if _rs_vals else "—"

    mg_note = ""
    for b, m in ctx.get("mg", {}).items():
        if m and m.get("has_mg"):
            mg_note = (f'<div class="final" style="background:#fff7ed;'
                       f'border-color:#fcd9a8;color:#92400e">MG '
                       f'<b>{_won(m["amount"])}원</b>'
                       + (f' · {m["note"]}' if m.get("note") else '') + '</div>')
            break

    # 정정본은 첫 장에 명시한다. 파트너가 두 문서를 받았을 때 어느 게 최신인지
    # 못 알아보면 오히려 혼란이 커진다.
    # ★한 브랜드만 정산하는 경우가 흔하다. 실제로 들어간 브랜드만 적는다 —
    #   없는 브랜드가 문서에 적히면 "왜 스내피즘이 0원이지?" 하는 문의가 생긴다.
    used = [b for b in ("photoism", "snapism")
            if ctx["details"].get(b) is not None
            and not ctx["details"][b].empty]
    used_label = " + ".join(BRAND_LABEL[b] for b in used)
    multi = len(used) > 1

    # 제출처(파트너사명). 없으면 수취처 유형으로 대신한다.
    _p = ctx.get("partner") or {}
    partner = (_p.get("agency_name") if kind == "agency"
               else _p.get("mgmt_name")) or ""
    use_vat = bool(_p.get("vat", True))

    ver = int(ctx.get("version") or 1)
    badge = ""
    if ver > 1:
        why = ctx.get("reason") or ""
        badge = (f'<span class="badge">정정본 v{ver}</span>'
                 + (f'<div class="meta" style="margin-top:4px">정정 사유 · {why}</div>'
                    if why else ""))

    return f"""<!doctype html><html><head><meta charset="utf-8">
<link rel="stylesheet" href="{PRETENDARD}">
<style>{css}</style></head><body>

{badge}
<div class="eyebrow">IP 정산서 · {label}</div>
<h1>{ctx["ip"]}</h1>
<div class="who">{ctx["start"]} ~ {ctx["end"]} 정산분</div>
<div class="meta">발행일 {ctx["issued"]}{f' &nbsp;·&nbsp; 제출처 <b>{partner}</b> 귀중' if partner else ''}</div>

<div class="greet">
  <div class="g1">안녕하세요, {partner or label} 담당자님</div>
  <div class="g2"><b>{ctx["ip"]}</b> 의 {ctx["start"]} ~ {ctx["end"]} 정산 내역을
   정리해 드려요.<br>
   {"포토이즘·스내피즘 두 브랜드 매출을 합쳐 계산했고, " if multi
    else f"{used_label} 매출 기준이고, "}국가별 상세와 수량은 뒷장에 담았어요.
   확인해 보시고 궁금한 점은 편하게 알려주세요.</div>
</div>

<div class="kpi">
  <div class="l">{label} 정산액 <span style="color:var(--text-3)">· {used_label}</span></div>
  <div class="v">{_won(total_amt)}원</div>
  <div class="d">정산기준액 <b>{_won(total_base)}원</b></div>
</div>

<div class="ct">🧾 {"브랜드별 요약" if multi else "요약"} <span class="muted">단위 원</span></div>
<div class="ntbl">
<div class="ntr nth" style="grid-template-columns:1.3fr .9fr .9fr 1.1fr 1.1fr">
<span>브랜드</span><span class="r">오픈 · 매출발생</span>
<span class="r">{"프레임수 · 건수" if multi else (QTY_LABEL[used[0]] if used else "수량")}</span>
<span class="r">매출(KRW)</span><span class="r">{label} 정산액</span></div>
{brand_rows}
{'''<div class="ntr sum" style="grid-template-columns:1.3fr .9fr .9fr 1.1fr 1.1fr">
<span>합계</span><span></span><span></span>
<span class="r num">''' + _won(total_base) + '''</span>
<span class="r num">''' + _won(total_amt) + '''</span></div>''' if multi else ""}
</div>

<div class="ct">💳 정산 내역 <span class="muted">단위 원</span></div>
<div class="ntbl">
<div class="ntr nth" style="grid-template-columns:1.6fr .7fr 1.1fr 1.1fr .9fr">
<span>출자자</span><span class="c">지분율</span><span class="r">총 지급액</span>
<span class="r">공급가액</span><span class="r">부가세</span></div>
<div class="ntr" style="grid-template-columns:1.6fr .7fr 1.1fr 1.1fr .9fr">
<span class="nname">{partner or label}</span>
<span class="c num">{rs_pct}</span>
<span class="r num b">{_won(total_amt)}</span>
<span class="r num">{_won(supply)}</span>
<span class="r num">{_won(vat_amt) if use_vat else '—'}</span></div>
</div>

<div class="final">최종 정산액 &nbsp; <b>{_won(total_amt)}원</b></div>
{mg_note}

<div class="notes">
※ 매출액은 취소분을 제외한 실제 판매 금액이에요.<br>
※ 국가별 내역에는 <b>매출이 발생하지 않은 오픈 국가도 함께</b> 실었어요.<br>
※ 해외 매출은 원화로 환산해 합산해요. 환율은
 <b>{ctx.get("rate_source") or "서울외국환중개 매매기준율"} {ctx["rate_date"]}</b>
 기준이에요.
</div>
{pages}
</body></html>"""


def _render_to_file(html_path: str, out_path: str, footer: str) -> None:
    """실제 인쇄. **별도 프로세스에서만** 호출된다(아래 __main__ 참고)."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page()
        page.goto(Path(html_path).resolve().as_uri(), wait_until="networkidle")
        page.pdf(path=out_path, format="A4", print_background=True,
                 display_header_footer=True, header_template="<div></div>",
                 footer_template=(
                     '<div style="width:100%;font-size:7px;color:#98a0af;'
                     'font-family:sans-serif;padding:0 12mm;display:flex;'
                     'justify-content:space-between">'
                     f'<span>{footer}</span>'
                     '<span><span class="pageNumber"></span> / '
                     '<span class="totalPages"></span></span></div>'),
                 margin={"top": "13mm", "bottom": "13mm",
                         "left": "12mm", "right": "12mm"})
        b.close()


def render_pdf(html: str, footer: str, timeout: int = 240) -> bytes:
    """Playwright chromium 으로 인쇄해 바이트로 돌려준다.

    ★반드시 **별도 프로세스**에서 돌린다.
      Streamlit 서버(Tornado)가 Windows 에서 asyncio 정책을 SelectorEventLoop 로
      바꿔 놓는데, 이 루프는 서브프로세스를 못 띄운다 → Playwright 가 브라우저를
      실행하는 순간 NotImplementedError. 정책은 프로세스 전역이라 스레드로는 못 피하고,
      정책을 바꾸면 Streamlit 서버 쪽이 위험하다. 프로세스를 나누는 게 가장 안전하고,
      덤으로 chromium 메모리가 대시보드 프로세스에 안 쌓인다.
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
            raise RuntimeError("PDF 생성 실패"
                               + (f" — {msg[-500:]}" if msg else ""))
        return op.read_bytes()
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"PDF 생성이 {timeout}초 안에 끝나지 않았어요.")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    # 부모(Streamlit)가 서브프로세스로 부른다. 여기선 asyncio 정책이 깨끗하다.
    if len(sys.argv) >= 5 and sys.argv[1] == "--render":
        _render_to_file(sys.argv[2], sys.argv[3], sys.argv[4])
