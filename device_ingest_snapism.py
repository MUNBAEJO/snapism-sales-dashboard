"""스내피즘 어드민 매장·키오스크 CSV → data/devices_snapism.parquet 정규화.

포토이즘(device_ingest.py)과 출력 컬럼을 맞춰 대시보드 쪽 계산을 공유한다. 다만
스내피즘 쪽이 정보가 낫다 — **계약 기간(시작~종료)**이 있어서 설치일을 S/N 에서
유추할 필요도, 철거일을 몰라 분모를 어림할 필요도 없다.

키 구조:
  매장(store) 1행에 '키오스크 ID' 가 "270, 271, 272" 처럼 묶여 있다 → 펼쳐서 키오스크와 연결.
  ★매출 매장명은 매장의 '매장 이름'이 아니라 **키오스크의 '연결된 가게 이름'** 이다.
    중국 어드민은 매장명이 한글 음차(난징진링중환)라 매출(JS_NJ_南京金陵中环_POP_01)과
    안 붙는데, 키오스크 쪽에는 실제 매출 매장명이 들어 있다.

실행: python device_ingest_snapism.py
"""
import glob
import json
import re
import unicodedata
from pathlib import Path

import pandas as pd

BASE_DIR   = Path(__file__).parent
DEV_DIR    = BASE_DIR / "data" / "devices"
OUT_FILE   = BASE_DIR / "data" / "devices_snapism.parquet"
SALES_FILE = BASE_DIR / "data" / "master.parquet"
ALIAS_FILE = BASE_DIR / "store_aliases_snapism.json"

TEST_RE = re.compile(r"테스트|test|샘플|sample|데모|demo|본사|오피스|창고|회의실|시험", re.I)
# 국가 표기: "🇰🇷 대한민국(KR)" → KR
CC_RE = re.compile(r"\(([A-Z]{2})\)\s*$")


def norm_key(s: str) -> str:
    s = unicodedata.normalize("NFKC", str(s)).lower()
    return re.sub(r"[^0-9a-z가-힣一-鿿぀-ヿ]", "", s)


def _read(pattern):
    files = sorted(DEV_DIR.glob(pattern))
    if not files:
        raise FileNotFoundError(f"{DEV_DIR}/{pattern} 없음. snapism_store_collect.py 먼저 실행하세요.")
    out = []
    for f in files:
        x = pd.read_csv(f, dtype=str, encoding="utf-8-sig")
        x["사이트"] = f.name.split("snapism_")[1][:2]
        out.append(x)
    return pd.concat(out, ignore_index=True)


def build() -> pd.DataFrame:
    st = _read("snapism_*_store_*.csv")
    ki = _read("snapism_*_kiosk_*.csv")

    # 매장의 '키오스크 ID' 묶음을 펼쳐 키오스크와 1:1 로 만든다.
    st["_kid"] = st["키오스크 ID"].fillna("").astype(str).str.split(r"\s*,\s*")
    ex = st.explode("_kid")
    ex["_kid"] = ex["_kid"].str.strip()
    ex = ex[ex["_kid"] != ""]
    ex["장비키"] = ex["사이트"] + ":" + ex["_kid"]

    ki["장비키"] = ki["사이트"] + ":" + ki["키오스크 ID"].astype(str).str.strip()
    d = ki.merge(
        ex[["장비키", "매장 ID", "매장 이름", "운영 상태", "계약 형태", "국가", "계약 기간"]],
        on="장비키", how="left")

    # 계약 기간 "2026.07.13 00:00 ~ 2027.07.12 00:00" → 시작일 / 종료일
    per = d["계약 기간"].astype(str).str.split("~", n=1, expand=True)
    d["시작일"] = pd.to_datetime(per[0].str.strip(), format="%Y.%m.%d %H:%M", errors="coerce")
    d["종료일"] = pd.to_datetime(per[1].str.strip() if per.shape[1] > 1 else None,
                                 format="%Y.%m.%d %H:%M", errors="coerce")

    d["국가코드"] = d["국가"].astype(str).str.extract(CC_RE)[0]
    d["지점명"] = d["연결된 가게 이름"].astype(str).str.replace(r"\s+", " ", regex=True).str.strip()
    d["부스번호"] = d["키오스크 ID"].astype(str).str.strip()

    # 가동 여부: '가맹 해지'는 끝난 매장, '점검 중'은 잠깐 멈춘 장비.
    # 분모에서 '점검 중'까지 빼면 매출은 있는데 대수가 0인 매장이 생겨 대당 매출이 튄다.
    # 그래서 점검 중은 가동으로 두고 플래그로만 남긴다.
    d["가동중"] = d["운영 상태"].ne("가맹 해지")
    d["점검중"] = d["점검 중"].astype(str).str.lower().eq("true")
    d["테스트장비"] = d["지점명"].str.contains(TEST_RE)
    d["렌탈"] = d["계약 형태"].isin(["팝업", "렌탈"])

    # 매출 매장명 연결
    sales = pd.read_parquet(SALES_FILE, columns=["매장 이름"])
    names = (sales["매장 이름"].astype(str)
             .str.replace(r"\s+", " ", regex=True).str.strip().unique())
    by_key = {}
    for s in names:
        by_key.setdefault(norm_key(s), s)
    aliases = json.loads(ALIAS_FILE.read_text(encoding="utf-8")) if ALIAS_FILE.exists() else {}

    def match(name):
        if name in aliases:
            return aliases[name] or None
        return by_key.get(norm_key(name))

    d["매출매장명"] = d["지점명"].map(match)

    cols = ["장비키", "사이트", "국가코드", "매장 ID", "매장 이름", "지점명", "매출매장명",
            "부스번호", "계약 형태", "운영 상태", "가동중", "점검중", "테스트장비", "렌탈",
            "시작일", "종료일", "색상", "카드 리더"]
    return d[[c for c in cols if c in d.columns]]


def main():
    d = build()
    d.to_parquet(OUT_FILE, index=False)
    real = d[~d["테스트장비"]]
    linked = real["매출매장명"].notna()
    print(f"키오스크 {len(d):,}대 저장 → {OUT_FILE.name}")
    print(f"  테스트 제외 {len(real):,}대 / 매장 {real['지점명'].nunique():,}곳")
    print(f"  매출 매장 연결 {linked.sum():,}대 ({linked.mean()*100:.1f}%)")
    print(f"  계약 시작일 {d['시작일'].notna().sum():,} · 종료일 {d['종료일'].notna().sum():,}")
    print("\n국가별 가동 키오스크(팝업·렌탈 제외):")
    act = real[real["가동중"] & ~real["렌탈"]]
    print(act.groupby("국가코드", dropna=False).size().sort_values(ascending=False).to_string())


if __name__ == "__main__":
    main()
