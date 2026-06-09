"""
master_photoism.parquet → 2개의 경량 집계 parquet 생성
  - master_photoism_agg.parquet     : 날짜/국가/매장/타이틀 기준 (1.7M행 → ~90 MB)
  - master_photoism_hourly.parquet  : 날짜/시간대 기준 (소형, 시간대 차트용)

DuckDB → Arrow → 딕셔너리 인코딩 → parquet 저장
string 컬럼이 category로 읽혀서 메모리 90% 절약

실행: python build_photoism_agg.py
"""
import sys
import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
from pathlib import Path

BASE_DIR      = Path(__file__).parent
PARQ_IN       = BASE_DIR / "data" / "master_photoism.parquet"
PARQ_AGG      = BASE_DIR / "data" / "master_photoism_agg.parquet"
PARQ_HOURLY   = BASE_DIR / "data" / "master_photoism_hourly.parquet"

# ── 서비스코인 보정 ──────────────────────────────────────────────
# 입력 오류로 서비스코인이 상품총액을 비정상 초과하는 행(예: 페루 Feria
# 코인 224,224 / 상품총액 24)을 상품총액(=실제 단가×수량)으로 클립.
# → 매출을 제외하지 않고 '정상 판매가'로 보정. 코인 <= 총액인 정상 행은 불변.
COIN_FIX = (
    'CASE WHEN TRY_CAST("서비스코인" AS DOUBLE) > TRY_CAST("상품총액" AS DOUBLE) '
    '          AND TRY_CAST("상품총액" AS DOUBLE) > 0 '
    '     THEN CAST(TRY_CAST("상품총액" AS BIGINT) AS BIGINT) '
    '     ELSE COALESCE(TRY_CAST("서비스코인" AS BIGINT), 0) END'
)


def dict_encode_strings(table: pa.Table) -> pa.Table:
    """string 컬럼을 딕셔너리 인코딩으로 변환 (읽을 때 category 자동 변환)"""
    for i, col in enumerate(table.schema):
        if pa.types.is_string(col.type) or pa.types.is_large_string(col.type):
            table = table.set_column(i, col.name, table.column(i).dictionary_encode())
    return table


def build_agg(con, parq: str):
    """날짜/국가/매장/타이틀 기준 집계 (시간대 제외 → 행 수 최소화)"""
    print("  [1/2] 메인 집계 (날짜/국가/매장/타이틀)...")
    arrow = con.execute(f"""
        SELECT
            TRY_CAST("날짜" AS DATE)                                           AS "날짜",
            COALESCE("국가",    '')                                             AS "국가",
            COALESCE("국가코드",'')                                             AS "국가코드",
            COALESCE("브랜드",  '')                                             AS "브랜드",
            COALESCE("대분류",  '')                                             AS "대분류",
            COALESCE("타이틀명",'')                                             AS "타이틀명",
            COALESCE("매장 이름",'')                                            AS "매장 이름",
            COALESCE("결제 단위",'KRW')                                         AS "결제 단위",
            CASE WHEN LOWER(CAST("취소 여부" AS VARCHAR))
                 IN ('true','1','yes') THEN TRUE ELSE FALSE END                 AS "취소 여부",
            CAST(COUNT(*)                                             AS BIGINT) AS "건수",
            CAST(COALESCE(SUM(TRY_CAST("최종 결제 금액" AS BIGINT)),0) AS BIGINT) AS "최종 결제 금액",
            CAST(COALESCE(SUM(TRY_CAST("쿠폰 할인 금액" AS BIGINT)),0) AS BIGINT) AS "쿠폰 할인 금액",
            CAST(COALESCE(SUM({COIN_FIX}),0) AS BIGINT) AS "서비스코인"
        FROM read_parquet('{parq}')
        WHERE "날짜" IS NOT NULL AND TRIM(CAST("날짜" AS VARCHAR)) != ''
        GROUP BY 1,2,3,4,5,6,7,8,9
        ORDER BY 1 DESC, 2, 7
    """).to_arrow_table()

    arrow = dict_encode_strings(arrow)
    pq.write_table(arrow, PARQ_AGG, compression="snappy")
    mb = PARQ_AGG.stat().st_size / 1024 / 1024
    print(f"     저장: {PARQ_AGG.name}  ({mb:.1f} MB,  {arrow.num_rows:,}행)")


def build_hourly(con, parq: str):
    """날짜/시간대 기준 집계 (시간대 차트 전용, 초소형)"""
    print("  [2/2] 시간대 집계...")
    arrow = con.execute(f"""
        SELECT
            TRY_CAST("날짜" AS DATE)                                           AS "날짜",
            CAST(COALESCE(HOUR(TRY_CAST("결제일시" AS TIMESTAMP)), -1) AS INT) AS "시간대",
            CASE WHEN LOWER(CAST("취소 여부" AS VARCHAR))
                 IN ('true','1','yes') THEN TRUE ELSE FALSE END                 AS "취소 여부",
            CAST(COUNT(*)                                             AS BIGINT) AS "건수",
            CAST(COALESCE(SUM(TRY_CAST("최종 결제 금액" AS BIGINT)),0) AS BIGINT) AS "최종 결제 금액",
            CAST(COALESCE(SUM(TRY_CAST("쿠폰 할인 금액" AS BIGINT)),0) AS BIGINT) AS "쿠폰 할인 금액",
            CAST(COALESCE(SUM({COIN_FIX}),0) AS BIGINT) AS "서비스코인"
        FROM read_parquet('{parq}')
        WHERE "날짜" IS NOT NULL AND TRIM(CAST("날짜" AS VARCHAR)) != ''
        GROUP BY 1,2,3
        ORDER BY 1 DESC, 2
    """).to_arrow_table()

    pq.write_table(arrow, PARQ_HOURLY, compression="snappy")
    mb = PARQ_HOURLY.stat().st_size / 1024 / 1024
    print(f"     저장: {PARQ_HOURLY.name}  ({mb:.1f} MB,  {arrow.num_rows:,}행)")


def main():
    if not PARQ_IN.exists():
        print(f"[오류] 파일 없음: {PARQ_IN}")
        sys.exit(1)

    in_mb = PARQ_IN.stat().st_size / 1024 / 1024
    print(f"집계 시작: {PARQ_IN.name}  ({in_mb:.0f} MB)")

    parq = str(PARQ_IN).replace("\\", "/")
    con  = duckdb.connect()
    try:
        build_agg(con, parq)
        build_hourly(con, parq)
    finally:
        con.close()

    print("[완료] 집계 파일 2개 생성")


if __name__ == "__main__":
    main()
