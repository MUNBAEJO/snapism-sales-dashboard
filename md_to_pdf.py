# -*- coding: utf-8 -*-
"""마크다운 문서 → 인쇄용 PDF.

정산서와 **같은 방식**으로 찍는다(`settlement_pdf.render_pdf` = Playwright chromium).
문서 하나 뽑자고 마크다운 라이브러리를 새로 깔지 않으려고, 우리가 쓰는 문법만
직접 변환한다 — 제목 · 표 · 목록(중첩) · 코드블록 · 인용 · 굵게 · 링크 · 구분선.

★표는 줄 안에서 안 잘리게 `break-inside: avoid` 를 건다. 권한 표가 페이지 경계에
  걸려 헤더만 앞장에 남는 걸 막는다.

실행:
  python md_to_pdf.py CURRENT-PROJECTS/대시보드_권한_가이드.md
  python md_to_pdf.py 문서.md 나올파일.pdf --footer "SEOBUK · 콘텐츠운영팀"
"""
from __future__ import annotations

import html as _html
import re
import sys
from pathlib import Path

CSS = """
@page { size: A4; margin: 16mm 15mm 18mm; }
* { box-sizing: border-box; }
body {
  font-family: 'Pretendard','Malgun Gothic','Apple SD Gothic Neo',sans-serif;
  font-size: 10.5pt; line-height: 1.62; color: #1d2433; margin: 0;
  -webkit-print-color-adjust: exact; print-color-adjust: exact;
}
h1 { font-size: 20pt; font-weight: 800; letter-spacing: -.02em; margin: 0 0 4pt;
     color: #10142a; }
h1 + p { color: #6b7488; font-size: 9.5pt; margin: 0 0 16pt; }
h2 { font-size: 13.5pt; font-weight: 800; margin: 22pt 0 8pt; padding-top: 10pt;
     border-top: 2px solid #e8ebf2; color: #10142a; break-after: avoid; }
h3 { font-size: 11.5pt; font-weight: 800; margin: 14pt 0 6pt; color: #2b3350;
     break-after: avoid; }
p { margin: 0 0 8pt; }
ul { margin: 0 0 9pt; padding-left: 16pt; }
li { margin: 0 0 3pt; }
li > ul { margin-top: 3pt; }
strong { font-weight: 800; color: #10142a; }
code { font-family: 'Consolas','D2Coding',monospace; font-size: 9pt;
       background: #f2f4f9; border: 1px solid #e4e8f0; border-radius: 4px;
       padding: 0 3px; color: #3a4463; }
pre { background: #f7f9fc; border: 1px solid #e4e8f0; border-left: 3px solid #6c74f5;
      border-radius: 6px; padding: 9pt 11pt; margin: 0 0 10pt; overflow: hidden;
      break-inside: avoid; }
pre code { background: none; border: 0; padding: 0; font-size: 8.6pt;
           line-height: 1.5; color: #2b3350; white-space: pre-wrap; }
blockquote { margin: 0 0 10pt; padding: 8pt 12pt; background: #fff8e6;
             border-left: 3px solid #f0b429; border-radius: 0 6px 6px 0;
             color: #5b4a1e; font-size: 9.8pt; break-inside: avoid; }
blockquote p:last-child { margin: 0; }
table { width: 100%; border-collapse: collapse; margin: 0 0 12pt; font-size: 9.3pt;
        break-inside: avoid; }
th { background: #eef1f8; color: #2b3350; font-weight: 800; text-align: left;
     padding: 5pt 7pt; border: 1px solid #d9dfea; }
td { padding: 5pt 7pt; border: 1px solid #e4e8f0; vertical-align: top; }
tr:nth-child(even) td { background: #fbfcfe; }
hr { border: 0; border-top: 1px solid #e8ebf2; margin: 16pt 0; }
.foot { position: fixed; bottom: -12mm; left: 0; right: 0; text-align: center;
        font-size: 7.6pt; color: #9aa2b4; }
"""

def _inline(s: str) -> str:
    """인라인 서식(코드 · 굵게 · 기울임 · 링크).

    ★코드를 **자리표시자로 빼 두고** 나머지를 처리한 뒤 되돌린다. 코드를 먼저
      잘라내고 조각마다 굵게를 걸면, `**사이드바 `코드` 카드**` 처럼 굵게가 코드를
      감싸는 경우 양끝이 다른 조각에 떨어져 `**` 가 그대로 남는다(실제로 남았다).
    """
    holds: list[str] = []

    def _hold(m):
        holds.append("<code>" + _html.escape(m.group(1)) + "</code>")
        return f"\x00{len(holds) - 1}\x00"

    s = re.sub(r"`([^`]+)`", _hold, s)
    s = _html.escape(s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"<em>\1</em>", s)
    s = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1", s)   # 인쇄물이라 링크는 글자만
    return re.sub(r"\x00(\d+)\x00", lambda m: holds[int(m.group(1))], s)


def _table(rows: list[str]) -> str:
    def cells(line):
        return [c.strip() for c in line.strip().strip("|").split("|")]

    head = cells(rows[0])
    align = []
    for a in cells(rows[1]):
        align.append("center" if a.startswith(":") and a.endswith(":")
                     else "right" if a.endswith(":") else "left")
    align += ["left"] * (len(head) - len(align))
    out = ["<table><thead><tr>"]
    out += [f'<th style="text-align:{align[i]}">{_inline(h)}</th>' for i, h in enumerate(head)]
    out.append("</tr></thead><tbody>")
    for r in rows[2:]:
        cs = cells(r)
        out.append("<tr>" + "".join(
            f'<td style="text-align:{align[i] if i < len(align) else "left"}">{_inline(c)}</td>'
            for i, c in enumerate(cs)) + "</tr>")
    out.append("</tbody></table>")
    return "".join(out)


def md_to_html(md: str, title: str = "", footer: str = "") -> str:
    lines = md.replace("\r\n", "\n").split("\n")
    body, i = [], 0
    while i < len(lines):
        ln = lines[i]

        if ln.startswith("```"):                      # 코드블록
            i += 1
            buf = []
            while i < len(lines) and not lines[i].startswith("```"):
                buf.append(lines[i]); i += 1
            i += 1
            body.append("<pre><code>" + _html.escape("\n".join(buf)) + "</code></pre>")
            continue

        if ln.strip().startswith("|") and i + 1 < len(lines) and re.match(
                r"^\s*\|[\s:\-|]+\|\s*$", lines[i + 1]):
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append(lines[i]); i += 1
            body.append(_table(rows))
            continue

        if re.match(r"^\s*(-{3,}|\*{3,})\s*$", ln):
            body.append("<hr>"); i += 1; continue

        m = re.match(r"^(#{1,4})\s+(.*)$", ln)
        if m:
            lv = len(m.group(1))
            body.append(f"<h{lv}>{_inline(m.group(2))}</h{lv}>"); i += 1; continue

        if ln.startswith(">"):                         # 인용(여러 줄)
            buf = []
            while i < len(lines) and lines[i].startswith(">"):
                buf.append(lines[i].lstrip(">").strip()); i += 1
            body.append("<blockquote><p>" + _inline(" ".join(buf)) + "</p></blockquote>")
            continue

        if re.match(r"^\s*[-*]\s+", ln):               # 목록(2칸 들여쓰기 = 한 단계)
            items, depth = [], []
            while i < len(lines) and (re.match(r"^\s*[-*]\s+", lines[i])
                                      or (lines[i].startswith("  ") and lines[i].strip()
                                          and items)):
                if re.match(r"^\s*[-*]\s+", lines[i]):
                    ind = len(lines[i]) - len(lines[i].lstrip())
                    items.append([ind // 2, re.sub(r"^\s*[-*]\s+", "", lines[i]).strip()])
                else:                                  # 이어지는 줄은 앞 항목에 붙인다
                    items[-1][1] += " " + lines[i].strip()
                i += 1
            html_out, cur = [], -1
            for lvl, txt in items:
                lvl = min(lvl, cur + 1)
                while cur < lvl:
                    html_out.append("<ul>"); depth.append(1); cur += 1
                while cur > lvl:
                    html_out.append("</ul>"); depth.pop(); cur -= 1
                html_out.append("<li>" + _inline(txt) + "</li>")
            html_out += ["</ul>"] * len(depth)
            body.append("".join(html_out))
            continue

        if ln.strip():
            # ★첫 줄은 조건을 안 보고 무조건 먹는다. 조건부터 걸면 `**굵게**` 로
            #   시작하는 줄이 아래 `[-*#>]` 에 걸려 buf 가 비고 i 가 안 늘어
            #   **무한 루프**가 된다(실제로 걸렸다).
            buf = [ln.strip()]
            i += 1
            while i < len(lines) and lines[i].strip() and not re.match(
                    r"^\s*([-*#>]|\||```)", lines[i]):
                buf.append(lines[i].strip()); i += 1
            body.append("<p>" + _inline(" ".join(buf)) + "</p>")
            continue
        i += 1

    ft = f'<div class="foot">{_html.escape(footer)}</div>' if footer else ""
    return (f'<!doctype html><html><head><meta charset="utf-8">'
            f"<title>{_html.escape(title)}</title><style>{CSS}</style></head>"
            f"<body>{''.join(body)}{ft}</body></html>")


def main():
    a = sys.argv[1:]
    if not a:
        print(__doc__); return
    src = Path(a[0])
    out = Path(a[1]) if len(a) > 1 and not a[1].startswith("--") else src.with_suffix(".pdf")
    footer = a[a.index("--footer") + 1] if "--footer" in a else ""
    md = src.read_text(encoding="utf-8")
    html = md_to_html(md, title=src.stem, footer=footer)
    if "--html" in a:                                  # 확인용
        p = out.with_suffix(".html"); p.write_text(html, encoding="utf-8")
        print("HTML:", p); return
    from settlement_pdf import render_pdf
    out.write_bytes(render_pdf(html))
    print(f"PDF: {out}  ({out.stat().st_size:,}B)")


if __name__ == "__main__":
    main()
