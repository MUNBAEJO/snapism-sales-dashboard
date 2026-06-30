# -*- coding: utf-8 -*-
"""SM 촬영수(sm_shoot_daily.parquet) → 부서 공유용 엑셀(아티스트별 탭).

현재 오픈 중인 IP만, 한 엑셀에 아티스트별 시트로 나눠 보기 좋게 만든다.
멤버·테마의 한/영 표기를 한국어로 통합. 값 = 촬영수(Artist별 촬영수, CMS와 일치).

시트: 요약 → (아티스트별) → 국가별
실행:
  python sm_report.py                      # 전체 기간 → reports/
  python sm_report.py 2026-06-16 2026-06-29
"""
import io
import json
import sys
from pathlib import Path

import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

BASE_DIR = Path(__file__).parent
DAILY_PARQUET = BASE_DIR / "data" / "sm_shoot_daily.parquet"
CONFIG_FILE = BASE_DIR / "config.json"
REPORT_DIR = BASE_DIR / "reports"

# ── 현재 오픈 IP (순서 = 시트 순서). kws=테마 부분일치(소문자), members=한국어 통합명→별칭 ──
ARTISTS = [
    {"name": "NCT WISH", "kws": ["nct wish"], "countries": None, "members": {
        "시온": ["시온", "sion"], "리쿠": ["리쿠", "riku"], "유우시": ["유우시", "유시", "yushi"],
        "사쿠야": ["사쿠야", "sakuya"], "재희": ["재희", "jaehee"], "료": ["료", "ryo"]}},
    {"name": "라이즈", "kws": ["riize", "라이즈"], "countries": None, "members": {
        "쇼타로": ["쇼타로", "shotaro"], "은석": ["은석", "eunseok"], "성찬": ["성찬", "sungchan"],
        "원빈": ["원빈", "wonbin"], "앤톤": ["앤톤", "안톤", "anton"], "소희": ["소희", "sohee"]}},
    {"name": "아이린", "kws": ["irene", "아이린"], "countries": None, "members": {
        "아이린": ["아이린", "irene"]}},
    {"name": "승한", "kws": ["xnghan", "승한", "seunghan"], "countries": None, "members": {
        "승한": ["승한", "xnghan", "seunghan"]}},
    {"name": "태용", "kws": ["taeyong", "태용"], "countries": None, "members": {
        "태용": ["태용", "taeyong"]}},
    {"name": "샤이니", "kws": ["shinee", "샤이니"], "countries": None, "members": {
        "온유": ["온유", "onew"], "키": ["키", "key"], "민호": ["민호", "minho"], "태민": ["태민", "taemin"]}},
    {"name": "NCT 재민제노", "kws": ["jnj", "재민제노"], "countries": ["jp"], "members": {
        "재민": ["재민", "jaemin"], "제노": ["제노", "jeno"], "재민제노(듀오)": ["jnjm", "재민제노"]}},
]

# ── 서식 ──
_BLUE = PatternFill("solid", fgColor="4361EE")
_LIGHT = PatternFill("solid", fgColor="EEF2FB")
_TOTAL = PatternFill("solid", fgColor="DDE6FA")
_WHITE_BOLD = Font(bold=True, color="FFFFFF", name="맑은 고딕")
_BOLD = Font(bold=True, name="맑은 고딕")
_NORM = Font(name="맑은 고딕")
_CENTER = Alignment(horizontal="center", vertical="center")
_LEFT = Alignment(horizontal="left", vertical="center")
_NUMFMT = '#,##0;-#,##0;"-"'
_thin = Side(style="thin", color="D7DEEE")
_BORDER = Border(left=_thin, right=_thin, top=_thin, bottom=_thin)


def country_name_map() -> dict:
    try:
        cfg = json.load(open(CONFIG_FILE, encoding="utf-8"))["photoism"]["countries"]
        return {cc: info.get("name", cc.upper()) for cc, info in cfg.items()}
    except Exception:
        return {}


def load_daily(start=None, end=None) -> pd.DataFrame:
    df = pd.read_parquet(DAILY_PARQUET)
    if start:
        df = df[df["날짜"].astype(str) >= str(start)]
    if end:
        df = df[df["날짜"].astype(str) <= str(end)]
    nm = country_name_map()
    df = df.copy()
    df["국가"] = df["국가코드"].map(lambda c: nm.get(c, c.upper()))
    return df


def _match_artist(theme: str, cc: str):
    tl = str(theme).lower()
    for a in ARTISTS:
        if any(k in tl for k in a["kws"]):
            if a["countries"] and cc not in a["countries"]:
                return None
            return a
    return None


def _canon_member(artist, frame: str) -> str:
    fl = str(frame).strip().lower()
    for canon, aliases in artist["members"].items():
        if fl in [x.lower() for x in aliases]:
            return canon
    return str(frame).strip()  # 미정의 멤버는 원문 유지


def annotate(df: pd.DataFrame) -> pd.DataFrame:
    """오픈 아티스트만 남기고 아티스트·멤버(한국어 통합) 컬럼 부여."""
    arts, mems = [], []
    for theme, cc, frame in zip(df["테마"], df["국가코드"], df["프레임"]):
        a = _match_artist(theme, cc)
        if a:
            arts.append(a["name"])
            mems.append(_canon_member(a, frame))
        else:
            arts.append(None)
            mems.append(None)
    out = df.copy()
    out["아티스트"] = arts
    out["멤버"] = mems
    return out[out["아티스트"].notna()]


def _member_order(name: str, present: set):
    """정의된 멤버 순서 + 데이터에만 있는 멤버 뒤에 추가."""
    for a in ARTISTS:
        if a["name"] == name:
            order = [m for m in a["members"].keys() if m in present]
            extra = sorted(present - set(order))
            return order + extra
    return sorted(present)


def _style_header(ws, ncols):
    for c in range(1, ncols + 1):
        cell = ws.cell(2, c)
        cell.fill = _BLUE
        cell.font = _WHITE_BOLD
        cell.alignment = _CENTER
        cell.border = _BORDER


def _write_pivot(ws, pivot: pd.DataFrame, title: str, row_label: str):
    """pivot: index=행라벨, columns=날짜/국가, 값=정수. 보기좋게 렌더 + 합계."""
    dates = list(pivot.columns)
    ncols = 1 + len(dates) + 1  # 라벨 + 날짜들 + 합계
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=ncols)
    t = ws.cell(1, 1, title)
    t.font = Font(bold=True, size=13, name="맑은 고딕", color="1A1A2E")
    t.alignment = _LEFT
    # 헤더
    ws.cell(2, 1, row_label)
    for j, d in enumerate(dates):
        ws.cell(2, 2 + j, str(d)[5:] if str(d)[:2] == "20" else str(d))  # 날짜는 MM-DD
    ws.cell(2, ncols, "합계")
    _style_header(ws, ncols)
    # 데이터
    col_tot = [0] * len(dates)
    r0 = 3
    for i, (label, row) in enumerate(pivot.iterrows()):
        r = r0 + i
        lc = ws.cell(r, 1, label)
        lc.font = _BOLD
        lc.alignment = _LEFT
        lc.border = _BORDER
        if i % 2 == 1:
            lc.fill = _LIGHT
        rtot = 0
        for j, d in enumerate(dates):
            v = int(row[d])
            rtot += v
            col_tot[j] += v
            cell = ws.cell(r, 2 + j, v)
            cell.number_format = _NUMFMT
            cell.font = _NORM
            cell.alignment = _CENTER
            cell.border = _BORDER
            if i % 2 == 1:
                cell.fill = _LIGHT
        tc = ws.cell(r, ncols, rtot)
        tc.number_format = _NUMFMT
        tc.font = _BOLD
        tc.alignment = _CENTER
        tc.border = _BORDER
        tc.fill = _TOTAL
    # 합계 행
    rt = r0 + len(pivot)
    ws.cell(rt, 1, "합계").font = _BOLD
    ws.cell(rt, 1).fill = _TOTAL
    ws.cell(rt, 1).border = _BORDER
    for j in range(len(dates)):
        cell = ws.cell(rt, 2 + j, col_tot[j])
        cell.number_format = _NUMFMT
        cell.font = _BOLD
        cell.alignment = _CENTER
        cell.fill = _TOTAL
        cell.border = _BORDER
    g = ws.cell(rt, ncols, sum(col_tot))
    g.number_format = _NUMFMT
    g.font = _BOLD
    g.alignment = _CENTER
    g.fill = _TOTAL
    g.border = _BORDER
    # 폭·고정
    ws.column_dimensions["A"].width = max(12, min(22, max((len(str(x)) for x in pivot.index), default=8) + 4))
    for j in range(len(dates)):
        ws.column_dimensions[get_column_letter(2 + j)].width = 7.5
    ws.column_dimensions[get_column_letter(ncols)].width = 10
    ws.freeze_panes = "B3"


def _write_artist_sheet(ws, sub: pd.DataFrame, artist: dict, all_dates):
    """아티스트 시트: 국가 블록 × 멤버 행 × 날짜 열 (+ 국가 소계, 전체 합계)."""
    dates = [d for d in all_dates if d in set(sub["날짜"])]
    ncols = 2 + len(dates) + 1
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=ncols)
    note = " (일본)" if artist["countries"] == ["jp"] else ""
    t = ws.cell(1, 1, f"{artist['name']} — 국가·멤버별 일별 촬영수{note}")
    t.font = Font(bold=True, size=13, name="맑은 고딕", color="1A1A2E")
    t.alignment = _LEFT
    ws.cell(2, 1, "국가")
    ws.cell(2, 2, "멤버")
    for j, d in enumerate(dates):
        ws.cell(2, 3 + j, str(d)[5:])
    ws.cell(2, ncols, "합계")
    _style_header(ws, ncols)

    ctot = sub.groupby("국가")["촬영수"].sum().sort_values(ascending=False)
    countries = [c for c in ctot.index if ctot[c] > 0]
    r = 3
    grand = [0] * len(dates)

    def _put(row, col, val, *, bold=False, fill=None, num=False, left=False):
        c = ws.cell(row, col, val)
        c.font = _BOLD if bold else _NORM
        c.alignment = _LEFT if left else _CENTER
        c.border = _BORDER
        if num:
            c.number_format = _NUMFMT
        if fill:
            c.fill = fill
        return c

    for cc in countries:
        pv = pd.pivot_table(sub[sub["국가"] == cc], index="멤버", columns="날짜",
                            values="촬영수", aggfunc="sum", fill_value=0)
        members = _member_order(artist["name"], set(pv.index))
        csub = [0] * len(dates)
        for m in members:
            _put(r, 1, cc, left=True)
            _put(r, 2, m, left=True)
            rtot = 0
            for j, d in enumerate(dates):
                v = int(pv.loc[m].get(d, 0))
                rtot += v
                csub[j] += v
                grand[j] += v
                _put(r, 3 + j, v, num=True)
            _put(r, ncols, rtot, bold=True, num=True, fill=_LIGHT)
            r += 1
        _put(r, 1, cc, bold=True, fill=_TOTAL, left=True)
        _put(r, 2, "소계", bold=True, fill=_TOTAL, left=True)
        for j in range(len(dates)):
            _put(r, 3 + j, csub[j], bold=True, num=True, fill=_TOTAL)
        _put(r, ncols, sum(csub), bold=True, num=True, fill=_TOTAL)
        r += 1

    # 전체 합계
    for col, val in ((1, "전체"), (2, "합계")):
        c = ws.cell(r, col, val)
        c.font = _WHITE_BOLD
        c.fill = _BLUE
        c.border = _BORDER
        c.alignment = _LEFT
    for j in range(len(dates)):
        c = ws.cell(r, 3 + j, grand[j])
        c.font = _WHITE_BOLD
        c.fill = _BLUE
        c.border = _BORDER
        c.alignment = _CENTER
        c.number_format = _NUMFMT
    gc = ws.cell(r, ncols, sum(grand))
    gc.font = _WHITE_BOLD
    gc.fill = _BLUE
    gc.border = _BORDER
    gc.alignment = _CENTER
    gc.number_format = _NUMFMT

    ws.column_dimensions["A"].width = 11
    ws.column_dimensions["B"].width = 13
    for j in range(len(dates)):
        ws.column_dimensions[get_column_letter(3 + j)].width = 7.5
    ws.column_dimensions[get_column_letter(ncols)].width = 10
    ws.freeze_panes = "C3"


def build_xlsx(df: pd.DataFrame) -> bytes:
    a = annotate(df)
    all_dates = sorted(a["날짜"].unique())
    wb = Workbook()
    wb.remove(wb.active)

    # 요약: 아티스트 × 날짜 (전 국가 합산)
    summ = pd.pivot_table(a, index="아티스트", columns="날짜", values="촬영수",
                          aggfunc="sum", fill_value=0)
    order = [art["name"] for art in ARTISTS if art["name"] in summ.index]
    ws = wb.create_sheet("요약")
    _write_pivot(ws, summ.reindex(order).astype(int),
                 "SM 촬영 현황 — 아티스트별 일별 합계 (전 국가)", "아티스트")

    # 아티스트별 시트: 국가 × 멤버 × 날짜
    for art in ARTISTS:
        sub = a[a["아티스트"] == art["name"]]
        if sub.empty:
            continue
        ws = wb.create_sheet(art["name"][:31])
        _write_artist_sheet(ws, sub, art, all_dates)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def main():
    a = sys.argv[1:]
    start = a[0] if len(a) > 0 else None
    end = a[1] if len(a) > 1 else None
    if not DAILY_PARQUET.exists():
        print("sm_shoot_daily.parquet 가 없어요. 먼저 python sm_collect.py 로 수집하세요.")
        sys.exit(1)
    df = load_daily(start, end)
    if df.empty:
        print("해당 기간 데이터가 없어요.")
        sys.exit(0)
    REPORT_DIR.mkdir(exist_ok=True)
    out = REPORT_DIR / f"SM촬영현황_{df['날짜'].min()}_{df['날짜'].max()}.xlsx"
    out.write_bytes(build_xlsx(df))
    print(f"저장: {out}  (행 {len(df):,} · 국가 {df['국가'].nunique()})")


if __name__ == "__main__":
    main()
