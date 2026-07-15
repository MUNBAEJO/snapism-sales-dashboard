import pandas as pd
import glob
from pathlib import Path

BASE_DIR = Path(__file__).parent
RAW_DIR = BASE_DIR / "raw"
DATA_DIR = BASE_DIR / "data"
MASTER_FILE = DATA_DIR / "master.csv"

KEEP_COLS = [
    "결제일시", "날짜", "연월", "시간", "소스",
    "매장 이름", "상품 카테고리", "상품 이름",
    "상품 단가", "최종 결제 금액", "쿠폰 할인 금액",
    "결제 단위", "결제 수단", "취소 여부", "취소자 아이디",
    "프레임 이름", "매입사 이름", "카테고리",
]

# 크롤러가 생성하는 파일명 prefix → 소스 이름 매핑
SOURCE_MAP = {
    "kr_": "한국",
    "cn_": "해외",  # 대만, 말레이시아, 인도네시아, 일본, 태국, 홍콩 등
}


def clean_amount(val):
    if pd.isna(val) or str(val).strip() == "":
        return 0
    try:
        return int(str(val).replace(",", "").strip())
    except ValueError:
        return 0


def load_csv(filepath):
    for enc in ["utf-8-sig", "cp949", "euc-kr", "utf-8"]:
        try:
            return pd.read_csv(filepath, encoding=enc, dtype=str)
        except Exception:
            continue
    return None


def ingest():
    RAW_DIR.mkdir(exist_ok=True)
    DATA_DIR.mkdir(exist_ok=True)

    if MASTER_FILE.exists():
        master = pd.read_csv(MASTER_FILE, encoding="utf-8-sig", dtype=str)
        print(f"기존 누적 데이터: {len(master):,}건")
    else:
        master = pd.DataFrame()

    csv_files = sorted(glob.glob(str(RAW_DIR / "*.csv")))
    if not csv_files:
        print("raw 폴더에 CSV 파일이 없습니다.")
        print(f"  -> {RAW_DIR} 에 어드민에서 다운받은 CSV를 넣어주세요.")
        return

    new_dfs = []
    for f in csv_files:
        df = load_csv(f)
        if df is not None:
            # 파일명으로 소스(한국/중국) 자동 태깅
            fname = Path(f).name.lower()
            source = next(
                (v for k, v in SOURCE_MAP.items() if fname.startswith(k)),
                "한국",  # 기본값: 파일명 prefix 없으면 한국 어드민 데이터로 간주
            )
            df["소스"] = source
            new_dfs.append(df)
            print(f"  OK {Path(f).name}  ({len(df):,}건)  [{source}]")
        else:
            print(f"  NG {Path(f).name}  (인코딩 오류)")

    if not new_dfs:
        return

    new_data = pd.concat(new_dfs, ignore_index=True)

    combined = (
        pd.concat([master, new_data], ignore_index=True)
        if not master.empty
        else new_data
    )

    # 금액 정제
    for col in ["상품 단가", "최종 결제 금액", "쿠폰 할인 금액"]:
        if col in combined.columns:
            combined[col] = combined[col].apply(clean_amount)

    # 날짜 파생 컬럼
    dt = pd.to_datetime(combined["결제일시"], format="%Y.%m.%d %H:%M", errors="coerce")
    combined["날짜"] = dt.dt.strftime("%Y-%m-%d")
    combined["연월"] = dt.dt.strftime("%Y-%m")
    combined["시간"] = dt.dt.hour.astype("Int64")

    # 공백 제거
    for col in ["프레임 이름", "매장 이름", "상품 이름", "카테고리"]:
        if col in combined.columns:
            combined[col] = combined[col].astype(str).str.strip()

    # 국가명 정제: "대한민국(ko)" → "대한민국", "중국(zh)" → "중국" 등
    if "국가" in combined.columns:
        combined["국가"] = (
            combined["국가"].astype(str)
            .str.replace(r"\(.*?\)", "", regex=True)
            .str.strip()
        )

    # 결제 단위 누락 시 국가로 자동 보완 (CN 어드민은 결제 단위 컬럼 미제공)
    COUNTRY_CURRENCY = {
        "대한민국": "KRW", "중국": "CNY", "일본": "JPY",
        "대만": "TWD", "인도네시아": "IDR", "홍콩": "HKD",
        "태국": "THB", "말레이시아": "MYR",
    }
    if "결제 단위" in combined.columns and "국가" in combined.columns:
        missing_unit = combined["결제 단위"].isna() | (combined["결제 단위"].astype(str).str.strip() == "")
        combined.loc[missing_unit, "결제 단위"] = combined.loc[missing_unit, "국가"].map(COUNTRY_CURRENCY)
    combined["결제 단위"] = combined["결제 단위"].fillna("KRW")

    # 중복 제거 (결제일시+매장+상품+결제수단+승인번호 조합 기준)
    combined["_key"] = (
        combined["결제일시"].astype(str)
        + "|" + combined["매장 이름"].astype(str)
        + "|" + combined["상품 이름"].astype(str)
        + "|" + (combined["상품 단가"].astype(str) if "상품 단가" in combined.columns else "")
        + "|" + combined["결제 수단"].astype(str)
        + "|" + (combined["승인번호"].fillna("").astype(str) if "승인번호" in combined.columns else "")
    )
    before = len(combined)
    # ★keep="last": 나중에 재수집된(=최신) 행이 이김 → 판매 후 발생한 취소·정정이 옛 행을 덮어씀.
    #   (concat 순서가 [기존master, 신규]라 last=신규가 승리. 키 필드는 취소돼도 안 바뀌어 정확히 매칭됨.)
    #   과거엔 keep="first"라 취소 전 옛 행이 유지돼 취소가 영영 반영 안 됐음(대만 사례).
    combined = combined.drop_duplicates(subset=["_key"], keep="last")
    combined = combined.drop(columns=["_key", "No"], errors="ignore")
    removed = before - len(combined)
    if removed:
        print(f"  중복 제거(최신 우선): {removed:,}건")

    # 컬럼 정리 (KEEP_COLS 중 존재하는 것만 유지, 나머지는 append)
    existing_keep = [c for c in KEEP_COLS if c in combined.columns]
    extra = [c for c in combined.columns if c not in KEEP_COLS]
    combined = combined[existing_keep + extra]

    combined = combined.sort_values("날짜", ascending=False).reset_index(drop=True)

    combined.to_csv(MASTER_FILE, index=False, encoding="utf-8-sig")
    print(f"\n[완료] 누적 {len(combined):,}건 저장 완료  ->  data/master.csv")

    # 대시보드 로딩 가속용 parquet 동시 생성 (master.csv 원본 그대로)
    try:
        import data_io
        print(data_io.rebuild_parquet(MASTER_FILE))
    except Exception as e:
        print(f"[경고] master.parquet 생성 실패(대시보드는 csv 폴백): {e}")


if __name__ == "__main__":
    ingest()
