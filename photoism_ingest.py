"""
포토이즘 엑셀 → master_photoism.parquet 증분 누적 처리

핵심 설계(2026-06-11 개편): 예전엔 매번 raw_photoism 의 모든 엑셀(수천 개)을 다시
읽어 2GB CSV 를 통째로 재생성 → 파일이 쌓이며 메모리 부족(OOM)으로 누적이 막혔다.
이제는 **기존 parquet(경량) + 신규 날짜분만** DuckDB 로 교체한다.

  1. master_photoism.parquet 에서 최신 누적일(cutoff)을 읽는다.
  2. raw_photoism 에서 cutoff 이후 날짜 파일만 파싱한다(엑셀은 개별로 작음).
  3. 새로 파싱한 '날짜들'에 대해서만 master 의 해당 날짜를 교체(DuckDB UNION).
     → 같은 날 재실행해도 중복이 쌓이지 않고(idempotent), 누락분이 자동 채워진다.
  4. build_photoism_agg 로 집계 parquet 갱신.

canonical 은 parquet 로 전환했다(대용량 CSV 미사용). 기존 master_photoism.csv 는
레거시로 남겨둔다(대시보드는 parquet 우선). 전체 재빌드가 필요하면
`python photoism_ingest.py 2026-01-01` 처럼 시작일을 주면 그 이후를 모두 재구성한다.

실행: python photoism_ingest.py [YYYY-MM-DD]   (날짜 생략 시 최신 누적일부터)
"""
import io
import os
import re
import sys
import json
import pandas as pd
from pathlib import Path
from datetime import datetime, timedelta

BASE_DIR    = Path(__file__).parent
CONFIG_FILE = BASE_DIR / "config.json"
RAW_DIR     = BASE_DIR / "raw_photoism"
DATA_DIR    = BASE_DIR / "data"
MASTER_FILE = DATA_DIR / "master_photoism.csv"        # 레거시(대용량) — 더 이상 갱신 안 함
MASTER_PARQ = DATA_DIR / "master_photoism.parquet"    # canonical

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


def _file_date(fp: Path):
    """파일명 photoism_{code}_{YYYYMMDD}.xlsx 에서 날짜 추출."""
    m = re.search(r"_(\d{8})\.xlsx$", fp.name)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%Y%m%d").date()
    except ValueError:
        return None


def _master_max_date():
    """기존 parquet 의 최신 누적일(없으면 None)."""
    if not MASTER_PARQ.exists():
        return None
    import pyarrow.parquet as pq
    col = pq.read_table(MASTER_PARQ, columns=["날짜"]).to_pandas()["날짜"]
    d = pd.to_datetime(col, errors="coerce").dt.date.dropna()
    return d.max() if len(d) else None


def main():
    # 시작일 결정: 인수가 있으면 그날부터 재구성, 없으면 기존 최신일부터(증분)
    target_date = None
    if len(sys.argv) >= 2:
        try:
            target_date = datetime.strptime(sys.argv[1], "%Y-%m-%d").date()
        except ValueError:
            target_date = None

    config = load_config()
    DATA_DIR.mkdir(exist_ok=True)

    cutoff = target_date or _master_max_date()
    if cutoff:
        log(f"증분 누적: {cutoff} 이후 날짜만 처리 (기존 parquet 기준)")
    else:
        log("기존 parquet 없음 → raw 전체 처리")

    all_files = sorted(RAW_DIR.glob("photoism_*.xlsx"))
    sel = [f for f in all_files
           if cutoff is None or (_file_date(f) is not None and _file_date(f) >= cutoff)]
    if not sel:
        log("새로 처리할 파일이 없습니다 (이미 최신).")
        return
    log(f"처리 대상 파일: {len(sel)}개 / 전체 {len(all_files)}개")

    frames = []
    for fp in sel:
        parts = fp.stem.split("_")
        if len(parts) < 3:
            continue
        df = parse_excel(fp, parts[1], config)
        if not df.empty:
            frames.append(df)
    if not frames:
        log("유효한 데이터 없음")
        return

    new_df = pd.concat(frames, ignore_index=True)
    new_df["_k"] = new_df["결제일시"].astype(str)
    before = len(new_df)
    new_df = new_df.drop_duplicates(
        subset=["국가코드", "_k", "매장 이름", "프레임 이름", "최종 결제 금액"], keep="last"
    ).drop(columns=["_k"])
    # 스필오버 방지(타임존 경계로 파일에 섞인 인접일): cutoff 미만은 기존 master 유지,
    # cutoff 이상만 신규로 교체. → 완결된 과거일(예: 06-08)을 부분 데이터로 덮어쓰지 않는다.
    if cutoff is not None:
        _nd = pd.to_datetime(new_df["날짜"], errors="coerce").dt.date
        new_df = new_df[_nd.notna() & (_nd >= cutoff)]
    if new_df.empty:
        log("cutoff 이후 신규 데이터 없음")
        return
    new_dates = sorted(set(pd.to_datetime(new_df["날짜"], errors="coerce").dt.date.dropna()))
    log(f"  반영 대상: {len(new_df):,}건 · 날짜 {[str(d) for d in new_dates]}")

    # CSV 직렬화와 동일한 문자열 포맷으로 변환(기존 parquet 이 전부 문자열 스키마라 일치 필요)
    buf = io.StringIO()
    new_df.to_csv(buf, index=False, encoding="utf-8-sig")
    buf.seek(0)
    new_str = pd.read_csv(buf, dtype=str, keep_default_na=False)
    tmp_new = DATA_DIR / "_photoism_new.parquet"
    new_str.to_parquet(tmp_new, compression="snappy", index=False)

    import duckdb
    new_master = MASTER_PARQ.with_suffix(".parquet.tmp")
    src  = str(MASTER_PARQ).replace("\\", "/")
    tnew = str(tmp_new).replace("\\", "/")
    out  = str(new_master).replace("\\", "/")
    tdir = DATA_DIR / "_duckdb_tmp"
    tdir.mkdir(exist_ok=True)
    con = duckdb.connect()
    con.execute("PRAGMA memory_limit='1GB'")     # 초과분은 디스크로 스필 → OOM 방지
    con.execute("PRAGMA threads=2")
    con.execute(f"PRAGMA temp_directory='{str(tdir).replace(chr(92), '/')}'")
    try:
        if MASTER_PARQ.exists() and cutoff is not None:
            con.execute(f"""
                COPY (
                    SELECT * FROM read_parquet('{src}')
                      WHERE TRY_CAST("날짜" AS DATE) < DATE '{cutoff}'
                    UNION ALL BY NAME
                    SELECT * FROM read_parquet('{tnew}')
                ) TO '{out}' (FORMAT PARQUET, COMPRESSION SNAPPY)
            """)
        else:
            con.execute(f"COPY (SELECT * FROM read_parquet('{tnew}')) TO '{out}' (FORMAT PARQUET, COMPRESSION SNAPPY)")
        total = con.execute(f"SELECT COUNT(*) FROM read_parquet('{out}')").fetchone()[0]
    finally:
        con.close()

    os.replace(new_master, MASTER_PARQ)
    tmp_new.unlink(missing_ok=True)
    mb = MASTER_PARQ.stat().st_size / 1024 / 1024
    log(f"[완료] master_photoism.parquet 갱신 — 누적 {total:,}건 ({mb:.0f} MB)")

    # 집계 parquet 갱신 (build_photoism_agg 는 DuckDB 로 parquet 직접 읽음 → 메모리 안전)
    try:
        from build_photoism_agg import main as build_agg
        log("집계 파일 갱신 중...")
        build_agg()
        log("집계 완료")
    except Exception as e:
        log(f"[경고] 집계 파일 갱신 실패 (수동 실행 필요): {e}")


if __name__ == "__main__":
    main()
