# -*- coding: utf-8 -*-
"""SM(이름에 'SM ent' 포함) 타이틀 — 날짜 × 테마 × 프레임 × 국가별 촬영수 집계.

촬영수 = 거래(주문) 행 수. 우리 일별 거래 데이터(master_photoism.parquet)에
테마는 없으므로 theme_map(타이틀,프레임→테마)을 조인해 붙인다.
(거래 데이터는 매일 받으므로 CMS 추가 다운로드 없이 산출 가능.)
"""
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq

BASE_DIR = Path(__file__).parent
TX_PARQUET = BASE_DIR / "data" / "master_photoism.parquet"
THEME_MAP = BASE_DIR / "data" / "theme_map.parquet"

# 거래에서 읽을 컬럼만 (메모리 절약)
_TX_COLS = ["날짜", "국가", "국가코드", "타이틀명", "프레임 이름", "구좌"]

# SM 타이틀 식별: 이름에 'sm ent' 포함(대소문자·공백 무시) — 'SM Ent', 'SM ENTERTAINMENT' 모두 매치
_SM_REGEX = r"sm\s*ent"


def load_sm_shooting(start=None, end=None, countries=None) -> pd.DataFrame:
    """SM 촬영수 일별 집계 반환.

    반환 컬럼: 날짜 · 테마 · 프레임 · 국가 · 국가코드 · 촬영수
    start/end: 'YYYY-MM-DD' (포함). countries: 국가명 리스트(None=전체).
    """
    df = pq.read_table(TX_PARQUET, columns=_TX_COLS).to_pandas()
    df["타이틀명"] = df["타이틀명"].astype(str)
    df["프레임 이름"] = df["프레임 이름"].astype(str)

    sm = df[df["타이틀명"].str.contains(_SM_REGEX, case=False, na=False, regex=True)].copy()
    if start:
        sm = sm[sm["날짜"].astype(str) >= str(start)]
    if end:
        sm = sm[sm["날짜"].astype(str) <= str(end)]
    if countries:
        sm = sm[sm["국가"].isin(countries)]

    # 테마 조인 (타이틀명, 프레임이름 → 테마)
    tmap = pd.read_parquet(THEME_MAP)
    sm = sm.merge(
        tmap.rename(columns={"프레임이름": "프레임 이름"})[["타이틀명", "프레임 이름", "테마"]],
        on=["타이틀명", "프레임 이름"], how="left",
    )
    sm["테마"] = sm["테마"].fillna("(미분류)").replace("", "(미분류)")

    g = (sm.groupby(["날짜", "테마", "프레임 이름", "국가", "국가코드"], as_index=False)
            .size().rename(columns={"size": "촬영수", "프레임 이름": "프레임"}))
    return g.sort_values(["날짜", "테마", "프레임", "국가"]).reset_index(drop=True)


def sm_titles(start=None, end=None) -> list:
    """기간 내 매칭된 SM 타이틀 목록."""
    df = pq.read_table(TX_PARQUET, columns=["날짜", "타이틀명"]).to_pandas()
    df["타이틀명"] = df["타이틀명"].astype(str)
    m = df["타이틀명"].str.contains(_SM_REGEX, case=False, na=False, regex=True)
    if start:
        m &= df["날짜"].astype(str) >= str(start)
    if end:
        m &= df["날짜"].astype(str) <= str(end)
    return sorted(df.loc[m, "타이틀명"].unique().tolist())


if __name__ == "__main__":
    import io
    buf = io.StringIO()
    g = load_sm_shooting()
    buf.write("집계 행수: %d\n" % len(g))
    buf.write("기간: %s ~ %s\n" % (g["날짜"].min(), g["날짜"].max()))
    buf.write("총 촬영수: %d\n" % g["촬영수"].sum())
    buf.write("국가수: %d | 테마수: %d | 프레임수: %d\n" % (
        g["국가"].nunique(), g["테마"].nunique(), g["프레임"].nunique()))
    buf.write("미분류 촬영수: %d (%.1f%%)\n\n" % (
        g.loc[g["테마"] == "(미분류)", "촬영수"].sum(),
        100 * g.loc[g["테마"] == "(미분류)", "촬영수"].sum() / g["촬영수"].sum()))
    buf.write("테마별 촬영수 TOP10:\n")
    for t, c in g.groupby("테마")["촬영수"].sum().sort_values(ascending=False).head(10).items():
        buf.write("  %8d  %s\n" % (c, t))
    buf.write("\n샘플 10행:\n")
    buf.write(g.head(10).to_string(index=False))
    Path("scratch_sm_agg.txt").write_text(buf.getvalue(), encoding="utf-8")
    print("done")
