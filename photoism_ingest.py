"""
포토이즘 엑셀 → master_photoism.csv 누적 처리

실행: python photoism_ingest.py [YYYY-MM-DD]
"""
import sys
import json
import pandas as pd
from pathlib import Path
from datetime import datetime, timedelta

BASE_DIR    = Path(__file__).parent
CONFIG_FILE = BASE_DIR / "config.json"
RAW_DIR     = BASE_DIR / "raw_photoism"
DATA_DIR    = BASE_DIR / "data"
MASTER_FILE = DATA_DIR / "master_photoism.csv"

def load_config():
    with open(CONFIG_FILE, encoding="utf-8") as f:
        return json.load(f)

def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}")

# 국가코드 → 국가명 역방향 매핑 (파일명에서 추출)
def get_country_info(config, country_code):
    countries = config.get("photoism", {}).get("countries", {})
    return countries.get(country_code, {"name": country_code.upper(), "currency": "KRW"})

def parse_excel(filepath: Path, country_code: str, config: dict) -> pd.DataFrame:
    """엑셀 파일 1개 → 정규화된 DataFrame"""
    try:
        df = pd.read_excel(filepath, engine="openpyxl")
    except Exception as e:
        log(f"  [오류] {filepath.name} 읽기 실패: {e}")
        return pd.DataFrame()

    if df.empty:
        return pd.DataFrame()

    info = get_country_info(config, country_code)
    currency = info.get("currency", "KRW")
    country_name = info.get("name", country_code.upper())

    # 취소 여부: '취소 날짜' 컬럼이 있고 값이 있으면 취소
    if "취소 날짜" in df.columns:
        cancelled = df["취소 날짜"].notna() & (df["취소 날짜"].astype(str).str.strip() != "")
    elif "원거래 취소 여부" in df.columns:
        cancelled = df["원거래 취소 여부"].notna()
    else:
        cancelled = pd.Series(False, index=df.index)

    # 결제일시 파싱 (형식: 2026-06-01T10:11:54)
    결제일시 = pd.to_datetime(df.get("결제일", pd.Series(dtype=str)), errors="coerce")

    # 금액 컬럼 정수화
    def to_int(col):
        return pd.to_numeric(df.get(col, 0), errors="coerce").fillna(0).astype(int)

    out = pd.DataFrame({
        "날짜":          결제일시.dt.date,
        "결제일시":       결제일시,
        "국가":          country_name,
        "매장 이름":      df.get("매장명", ""),
        "대분류":         df.get("대분류", ""),
        "중분류":         df.get("중분류", ""),
        "소분류":         df.get("소분류", ""),
        "브랜드":         df.get("브랜드", ""),
        "구좌":           df.get("구좌", ""),
        # KR: 타이틀명/프레임명, 해외: 타이틀/프레임 (컬럼명 통일)
        "타이틀명":       df["타이틀명"] if "타이틀명" in df.columns else df.get("타이틀", ""),
        "프레임 이름":    df["프레임명"] if "프레임명" in df.columns else df.get("프레임", ""),
        "상품 단가":      to_int("프레임 단가"),
        "상품총액":       to_int("상품총액"),
        "쿠폰 할인 금액": to_int("쿠폰"),
        "마일리지":       to_int("마일리지"),
        "서비스코인":     to_int("서비스코인"),
        "최종 결제 금액": to_int("최종결제금액"),
        "결제 단위":      currency,
        "결제 수단":      df.get("결제수단", ""),
        "취소 여부":      cancelled,
        "지역":           df.get("지역", ""),
        "국가코드":       country_code,
    })

    return out


def main():
    # 날짜 결정 (ingest는 날짜 인수 선택적)
    if len(sys.argv) >= 2:
        try:
            target_date = sys.argv[1]
            datetime.strptime(target_date, "%Y-%m-%d")
        except ValueError:
            target_date = None
    else:
        target_date = None

    config = load_config()
    DATA_DIR.mkdir(exist_ok=True)

    # raw_photoism 폴더의 모든 엑셀 파일 수집
    all_files = list(RAW_DIR.glob("photoism_*.xlsx"))
    if not all_files:
        log("처리할 파일이 없습니다.")
        return

    log(f"처리 대상 파일: {len(all_files)}개")

    frames = []
    for fp in sorted(all_files):
        # 파일명에서 country_code 추출: photoism_{code}_{date}.xlsx
        parts = fp.stem.split("_")
        if len(parts) < 3:
            continue
        country_code = parts[1]
        df = parse_excel(fp, country_code, config)
        if not df.empty:
            frames.append(df)
            log(f"  OK {fp.name}  ({len(df):,}건)  [{country_code}]")

    if not frames:
        log("유효한 데이터 없음")
        return

    combined = pd.concat(frames, ignore_index=True)

    # 중복 제거 (국가코드 + 결제일시 + 매장 이름 + 프레임 이름 + 최종 결제 금액 기준)
    # 국가코드 포함: 다른 국가에서 동일 금액/시각 거래가 있어도 중복으로 처리되지 않도록
    combined["결제일시_str"] = combined["결제일시"].astype(str)
    dedup_cols = ["결제일시_str", "매장 이름", "프레임 이름", "최종 결제 금액"]
    if "국가코드" in combined.columns:
        dedup_cols = ["국가코드"] + dedup_cols
    before = len(combined)
    combined = combined.drop_duplicates(subset=dedup_cols, keep="last")
    combined = combined.drop(columns=["결제일시_str"])
    log(f"  중복 제거: {before:,} → {len(combined):,}건")

    # 저장
    combined.to_csv(MASTER_FILE, index=False, encoding="utf-8-sig")
    log(f"[완료] 누적 {len(combined):,}건 저장 → {MASTER_FILE.name}")

    # parquet 변환 + 집계 파일 갱신
    try:
        from convert_photoism_parquet import main as convert_parquet
        from build_photoism_agg import main as build_agg
        log("parquet 변환 중...")
        convert_parquet()
        log("집계 파일 갱신 중...")
        build_agg()
        log("집계 완료")
    except Exception as e:
        log(f"[경고] 집계 파일 갱신 실패 (수동 실행 필요): {e}")


if __name__ == "__main__":
    main()
