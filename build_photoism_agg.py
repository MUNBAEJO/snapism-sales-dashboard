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

import ip_classify  # IP구분/IP명 분류 공용 모듈

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
    """날짜/국가/매장/타이틀 기준 집계 (시간대 제외 → 행 수 최소화).

    기존 컬럼(대분류·타이틀명 등)은 그대로 두고, IP 분석용 파생 컬럼을 추가:
      - 구좌  : BASIC/WITH/EVENT (원본)
      - IP구분: 아티스트/캐릭터/렌탈/PICK/기획(P)/제외
      - 타이틀: 날짜 + 대표 IP명 (예 '260527 우주소녀'). 출시(날짜)별 구분 유지,
               같은 날짜+IP면 한·영 통합. '제외' 행은 ''.
      - IP명  : 날짜 뗀 대표 IP명 (롤업·필터용). '제외' 행은 ''.
    """
    print("  [1/2] 메인 집계 (날짜/국가/매장 + IP구분/타이틀)...")
    df = con.execute(f"""
        WITH base AS (
            SELECT
                TRY_CAST("날짜" AS DATE)                                       AS "날짜",
                COALESCE("국가",    '')                                         AS "국가",
                COALESCE("국가코드",'')                                         AS "국가코드",
                COALESCE("브랜드",  '')                                         AS "브랜드",
                COALESCE("대분류",  '')                                         AS "대분류",
                COALESCE("타이틀명",'')                                         AS "타이틀명",
                COALESCE("매장 이름",'')                                        AS "매장 이름",
                COALESCE("결제 단위",'KRW')                                     AS "결제 단위",
                COALESCE(CAST("구좌" AS VARCHAR), '')                           AS "구좌",
                CASE WHEN LOWER(CAST("취소 여부" AS VARCHAR))
                     IN ('true','1','yes') THEN TRUE ELSE FALSE END             AS "취소 여부",
                ({ip_classify.IP_GUBUN_SQL})                                    AS "IP구분",
                ({ip_classify.IP_DATE_SQL})                                     AS "날짜코드",
                ({ip_classify.IP_NAMECORE_SQL})                                AS "IP명_raw",
                TRY_CAST("최종 결제 금액" AS BIGINT)                            AS "_amt",
                TRY_CAST("쿠폰 할인 금액" AS BIGINT)                            AS "_cpn",
                {COIN_FIX}                                                      AS "_coin"
            FROM read_parquet('{parq}')
            WHERE "날짜" IS NOT NULL AND TRIM(CAST("날짜" AS VARCHAR)) != ''
        ),
        tagged AS (
            SELECT *,
                CASE WHEN "IP구분"='제외' THEN '' ELSE "IP명_raw" END  AS "IP명_c",
                CASE WHEN "IP구분"='제외' THEN '' ELSE "날짜코드"  END  AS "날짜코드_c"
            FROM base
        )
        SELECT
            "날짜","국가","국가코드","브랜드","대분류","타이틀명","매장 이름",
            "결제 단위","구좌","IP구분","날짜코드_c" AS "날짜코드","IP명_c" AS "IP명_raw","취소 여부",
            CAST(COUNT(*)                          AS BIGINT) AS "건수",
            CAST(COALESCE(SUM("_amt"),0)           AS BIGINT) AS "최종 결제 금액",
            CAST(COALESCE(SUM("_cpn"),0)           AS BIGINT) AS "쿠폰 할인 금액",
            CAST(COALESCE(SUM("_coin"),0)          AS BIGINT) AS "서비스코인"
        FROM tagged
        GROUP BY 1,2,3,4,5,6,7,8,9,10,11,12,13
    """).df()

    # 한·영 통합(별칭→대표명): IP명(롤업) + 타이틀(날짜 유지). '제외'는 빈 값.
    df["IP명"] = ip_classify.apply_alias(df["IP명_raw"])
    _date = df["날짜코드"].astype(str).str.strip()
    _name = df["IP명"].astype(str).str.strip()
    df["타이틀"] = (_date + " " + _name).str.strip()
    df.loc[_name == "", ["IP명", "타이틀"]] = ""
    df = df.drop(columns=["날짜코드", "IP명_raw"])

    group_dims = ["날짜","국가","국가코드","브랜드","대분류","타이틀명","매장 이름",
                  "결제 단위","구좌","IP구분","타이틀","IP명","취소 여부"]
    df = (df.groupby(group_dims, dropna=False, observed=True)[
              ["건수","최종 결제 금액","쿠폰 할인 금액","서비스코인"]]
            .sum().reset_index())
    df = df.sort_values(["날짜","국가","매장 이름"], ascending=[False, True, True])

    arrow = pa.Table.from_pandas(df, preserve_index=False)
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
