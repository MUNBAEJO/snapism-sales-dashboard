# -*- coding: utf-8 -*-
"""raw_theme/*.xlsx (CMS 테마 리포트, 30개국·월단위) →
  · data/theme_revenue.parquet : 테마별 매출 팩트테이블 (국가·기간·TITLE·THEME·FRAME·금액)
  · data/theme_map.parquet     : (타이틀명, 프레임이름) → 테마 룩업
파일명 규칙: theme_{국가코드}_{YYYYMMDD시작}_{YYYYMMDD종료}.xlsx
"""
import glob
import re
import sys
from pathlib import Path

import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")
BASE = Path(__file__).parent
RAW = BASE / "raw_theme"
REV_OUT = BASE / "data" / "theme_revenue.parquet"
MAP_OUT = BASE / "data" / "theme_map.parquet"

_FN = re.compile(r"theme_([a-z]+)_(\d{8})_(\d{8})\.xlsx$", re.I)
NUMCOLS = {"프레임 금액": "프레임금액", "쿠폰 할인": "쿠폰할인",
           "서비스 코인": "서비스코인", "최종 결제 금액": "최종결제금액",
           "주문횟수": "주문횟수"}


def main():
    files = sorted(glob.glob(str(RAW / "*.xlsx")))
    if not files:
        print("raw_theme에 파일이 없습니다.")
        return
    parts, _failed = [], []
    for f in files:
        m = _FN.search(Path(f).name)
        if not m:
            continue
        cc, s, e = m.group(1).lower(), m.group(2), m.group(3)
        try:
            df = pd.read_excel(f)
        except Exception as ex:
            # ★★건너뛰고 끝내면 안 된다 (2026-08-20). 아래에서 팩트테이블을
            #   **전량 재작성**하므로, 읽기 실패한 파일 몫이 테마 매출에서 빠진 채
            #   덮어써진다. coverage_audit 의 테마 교차확인도 같이 눈이 먼다.
            #   실패한 파일을 모아 두었다가 마지막에 **저장을 막는다.**
            print(f"[읽기 실패] {Path(f).name}: {ex}")
            _failed.append(Path(f).name)
            continue
        if df.empty:
            continue
        df = df.rename(columns={"TITLE": "타이틀명", "THEME": "테마", "FRAME": "프레임이름",
                                **NUMCOLS})
        for c in ["타이틀명", "테마", "프레임이름"]:
            df[c] = df.get(c, "").astype(str).str.strip()
        for c in NUMCOLS.values():
            df[c] = pd.to_numeric(df.get(c, 0), errors="coerce").fillna(0)
        df["국가코드"] = cc
        df["기간시작"] = pd.to_datetime(s, format="%Y%m%d").date()
        df["기간종료"] = pd.to_datetime(e, format="%Y%m%d").date()
        parts.append(df)

    # ★★한 장이라도 못 읽었으면 **저장하지 않는다** (2026-08-20).
    #   아래 `to_parquet` 은 팩트테이블을 통째로 다시 쓴다 — 빠진 파일 몫이
    #   테마 매출에서 사라진 채 덮어써지고, 그 뒤로는 원본을 봐도 뭐가 빠졌는지
    #   알 수 없다. 파일을 고쳐 다시 돌리는 게 맞다.
    if _failed:
        print(f"[중단] 읽지 못한 파일 {len(_failed)}개 — "
              f"{' · '.join(_failed[:5])}{' 외' if len(_failed) > 5 else ''}")
        print("       그대로 저장하면 이 파일들의 매출이 빠진 채 팩트테이블이 "
              "덮어써집니다. 파일을 고친 뒤 다시 돌려 주세요.")
        sys.exit(1)

    raw = pd.concat(parts, ignore_index=True)
    # 테스트 데이터 제외
    raw = raw[~raw["타이틀명"].str.contains("테스트", na=False)]
    raw = raw[(raw["테마"] != "") & (raw["테마"].str.lower() != "nan")]
    print(f"파일 {len(parts)}개 · 행 {len(raw):,} · 국가 {raw['국가코드'].nunique()}")

    # ── 매출 팩트테이블 ──
    rev = raw[["국가코드", "기간시작", "기간종료", "타이틀명", "테마", "프레임이름",
               "프레임금액", "쿠폰할인", "서비스코인", "최종결제금액", "주문횟수"]].copy()
    REV_OUT.parent.mkdir(exist_ok=True)
    rev.to_parquet(REV_OUT, index=False)
    print(f"매출 팩트 저장: {REV_OUT.name}  ({len(rev):,}행)")

    # ── (타이틀,프레임) → 테마 매핑 (주문횟수 최다 테마 채택) ──
    agg = raw.groupby(["타이틀명", "프레임이름", "테마"], as_index=False)["주문횟수"].sum()
    agg = agg.sort_values("주문횟수", ascending=False)
    mapping = agg.drop_duplicates(subset=["타이틀명", "프레임이름"], keep="first")[
        ["타이틀명", "프레임이름", "테마"]].reset_index(drop=True)
    mapping.to_parquet(MAP_OUT, index=False)
    print(f"매핑 저장: {MAP_OUT.name}  ({len(mapping):,}행 · 테마 {mapping['테마'].nunique():,})")


if __name__ == "__main__":
    main()
