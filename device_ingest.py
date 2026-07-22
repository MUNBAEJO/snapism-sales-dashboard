"""장비관리 엑셀(data/devices/*.xlsx) → data/devices.parquet 정규화.

핵심 파생:
  설치일  = 기기 S/N 앞 6자리(YYMMDD). CMS에 설치일 컬럼이 없어 이걸로 대체한다.
  국가코드 = 기기정보 앞부분(KR-BK → KR). 비어 있으면 파일의 지역(kr 파일=KR)으로 보정.
  매칭매장 = 매출 데이터(master.parquet)의 '매장 이름'. 표기가 미묘하게 달라
             영숫자·한글만 남긴 키로 대조하고, 그래도 안 붙는 건 store_aliases.json 으로 수동 연결.

실행: python device_ingest.py
"""
import json
import re
import unicodedata
from datetime import datetime
from pathlib import Path

import pandas as pd

BASE_DIR   = Path(__file__).parent
DEV_DIR    = BASE_DIR / "data" / "devices"
OUT_FILE   = BASE_DIR / "data" / "devices.parquet"
ALIAS_FILE = BASE_DIR / "store_aliases.json"
SALES_FILE = BASE_DIR / "data" / "master_photoism_agg.parquet"

# 실매장이 아닌 것 — 대당 매출 분모에 들어가면 안 된다.
TEST_RE = re.compile(r"테스트|test|샘플|sample|데모|demo|본사|오피스|창고|회의실|시험", re.I)

# 여러 나라가 섞여 오는 지역 파일 — 기기정보가 비어 있어도 파일명으로 국가를 단정하면 안 된다.
MULTI_COUNTRY_SRC = {"tw", "ph", "id", "jp"}


def norm_key(s: str) -> str:
    """매장명 대조용 키 — 대소문자·공백·기호 차이를 흡수한다.
    '대구 동성로 3호점' == '대구 동성로3호점', 'TH_Siam Scape' == 'TH Siam scape'.
    반대로 '청당점' vs '청당동점' 처럼 글자가 다르면 안 붙는다(의도된 보수성)."""
    s = unicodedata.normalize("NFKC", str(s)).lower()
    return re.sub(r"[^0-9a-z가-힣一-鿿぀-ヿ]", "", s)


def load_raw() -> pd.DataFrame:
    files = sorted(DEV_DIR.glob("device_*.xlsx"))
    if not files:
        raise FileNotFoundError(f"{DEV_DIR} 에 장비 엑셀이 없습니다. device_collect.py 먼저 실행하세요.")
    frames = []
    for f in files:
        x = pd.read_excel(f, dtype=str)
        x["지역파일"] = f.name.split("device_")[1][:2]
        frames.append(x)
    return pd.concat(frames, ignore_index=True)


def build() -> pd.DataFrame:
    d = load_raw()

    d["지점명"] = d["지점명"].astype(str).str.replace(r"\s+", " ", regex=True).str.strip()

    # ★ 같은 장비가 두 지역 파일에 걸쳐 나오는 경우가 있다(라오스처럼 전용 호스트와
    #    지역 호스트 양쪽에 등록). 지점·부스·기기ID가 같으면 같은 장비다.
    d = d.drop_duplicates(["지점명", "부스번호", "기기ID"], keep="first").reset_index(drop=True)

    d["국가코드"] = (d["기기정보"].astype(str).str.split("-").str[0]
                     .replace({"nan": pd.NA, "": pd.NA}))
    # 기기정보가 비어 있으면 단일 국가 파일에 한해 파일명으로 보정한다.
    single = ~d["지역파일"].isin(MULTI_COUNTRY_SRC)
    d.loc[single, "국가코드"] = d.loc[single, "국가코드"].fillna(
        d.loc[single, "지역파일"].str.upper())

    # 설치일: S/N 앞 6자리. 앞자리가 안 맞는 소수(자릿수 오입력)는 결측 처리.
    sn = d["기기 S/N"].astype(str).str.extract(r"^(\d{6})")[0]
    d["설치일"] = pd.to_datetime(sn, format="%y%m%d", errors="coerce")
    # 미래 날짜는 오입력 (예: 300101)
    d.loc[d["설치일"] > pd.Timestamp.today(), "설치일"] = pd.NaT

    d["가동중"] = d["상태(한글)"].eq("가동 중")
    d["테스트장비"] = d["지점명"].str.contains(TEST_RE)
    d["렌탈"] = d["대분류"].astype(str).str.startswith("렌탈")

    # ★ 기기ID(=모델_S/N)는 지역이 다르면 우연히 겹친다(설치일+부스번호가 같으면 동일 문자열).
    #    전역 유일키는 '지역파일 + 기기ID'.
    d["장비키"] = d["지역파일"] + ":" + d["기기ID"].astype(str)

    # 매출 매장명과 연결 — 포토이즘 매출(집계본)의 '매장 이름' 기준.
    # 스내피즘은 어드민이 아예 달라 매장명 체계도 다르므로 여기서 붙이지 않는다.
    sales = pd.read_parquet(SALES_FILE, columns=["매장 이름"])
    store_names = (sales["매장 이름"].astype(str)
                   .str.replace(r"\s+", " ", regex=True).str.strip().unique())
    by_key = {}
    for s in store_names:
        by_key.setdefault(norm_key(s), s)

    aliases = {}
    if ALIAS_FILE.exists():
        aliases = json.loads(ALIAS_FILE.read_text(encoding="utf-8"))

    def match(name):
        if name in aliases:
            return aliases[name] or None
        return by_key.get(norm_key(name))

    d["매출매장명"] = d["지점명"].map(match)

    cols = ["장비키", "지역파일", "국가코드", "지점명", "매출매장명", "부스번호",
            "브랜드", "대분류", "상태(한글)", "가동중", "테스트장비", "렌탈",
            "기기ID", "기기 S/N", "설치일", "부스 컬러", "카메라 종류", "프린터 사양"]
    return d[cols]


def main():
    d = build()
    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    d.to_parquet(OUT_FILE, index=False)

    real = d[~d["테스트장비"]]
    linked = real["매출매장명"].notna()
    print(f"장비 {len(d):,}대 저장 → {OUT_FILE.name}")
    print(f"  테스트 장비 제외 {len(real):,}대 / 지점 {real['지점명'].nunique():,}곳")
    print(f"  매출 매장 연결 {linked.sum():,}대 ({linked.mean()*100:.1f}%)"
          f" · 지점 {real.loc[linked, '지점명'].nunique():,}곳")
    print(f"  설치일 확보 {d['설치일'].notna().sum():,}대"
          f" ({d['설치일'].min():%Y-%m-%d} ~ {d['설치일'].max():%Y-%m-%d})")
    print("\n국가별 가동 중 장비(렌탈·팝업 제외):")
    act = real[real["가동중"] & ~real["렌탈"]]
    print(act.groupby("국가코드", dropna=False).size().sort_values(ascending=False).to_string())


if __name__ == "__main__":
    main()
