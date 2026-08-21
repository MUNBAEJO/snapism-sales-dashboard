"""
master_photoism.parquet → 2개의 경량 집계 parquet 생성
  - master_photoism_agg.parquet     : 날짜/국가/매장/타이틀 기준 (1.7M행 → ~90 MB)
  - master_photoism_hourly.parquet  : 날짜/시간대 기준 (소형, 시간대 차트용)

DuckDB → Arrow → 딕셔너리 인코딩 → parquet 저장
string 컬럼이 category로 읽혀서 메모리 90% 절약

실행: python build_photoism_agg.py
"""
import os
import sys
import time

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
from pathlib import Path

import ip_classify  # IP구분/IP명 분류 공용 모듈
import store_rules  # 테스트·본사 매장 가리기(장비 쪽과 같은 규칙)

BASE_DIR      = Path(__file__).parent
PARQ_IN       = BASE_DIR / "data" / "master_photoism.parquet"
PARQ_AGG      = BASE_DIR / "data" / "master_photoism_agg.parquet"
PARQ_HOURLY   = BASE_DIR / "data" / "master_photoism_hourly.parquet"
PARQ_ORIG     = BASE_DIR / "data" / "master_photoism_orig.parquet"   # 오리지널 프레임별(경량)

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


def _write_atomic(arrow, path):
    """★같은 파일에 직접 쓰지 않는다 (2026-08-20).

    `pq.write_table` 은 대상 파일을 **먼저 비우고** 쓴다. 쓰는 중에 죽으면(OOM ·
    파일락 · 정전) 반쪽짜리 parquet 이 남아 대시보드가 통째로 못 뜬다. 하루 지난
    파일이라도 온전한 편이 낫다 — 임시파일에 다 쓴 뒤 한 번에 바꿔치기한다.
    교체는 대시보드(8503)가 그 파일을 열고 있으면 PermissionError 가 나므로
    photoism_ingest 의 master 교체와 같은 모양으로 몇 번 기다린다.
    """
    tmp = path.with_suffix(path.suffix + ".tmp")
    pq.write_table(arrow, tmp, compression="snappy")
    for i in range(6):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if i == 5:
                raise
            print(f"     {path.name} 이 잠겨 있어요 — {(i + 1) * 5}초 뒤 다시 시도")
            time.sleep((i + 1) * 5)


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
    # 별칭맵(별칭→대표명)을 소형 테이블로 등록 → SQL JOIN 으로 한·영 통합.
    # 자기참조(대표명→대표명) 포함이라 매핑 없는 이름도 COALESCE 로 원본 유지된다.
    _amap = ip_classify.load_alias_map()
    _keys = pa.array([str(k).strip() for k in _amap.keys()], type=pa.string())
    _vals = pa.array([str(v).strip() for v in _amap.values()], type=pa.string())
    con.register("alias_map", pa.table({"k": _keys, "v": _vals}))
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
                ({ip_classify.IP_PREFIX_SQL})                                   AS "접두어",
                TRY_CAST("최종 결제 금액" AS BIGINT)                            AS "_amt",
                TRY_CAST("쿠폰 할인 금액" AS BIGINT)                            AS "_cpn",
                {COIN_FIX}                                                      AS "_coin"
            FROM read_parquet('{parq}')
            WHERE "날짜" IS NOT NULL AND TRIM(CAST("날짜" AS VARCHAR)) != ''
              -- ★테스트 매장은 매출에서도 뺀다 (store_rules 주석 참고).
              --   장비 목록은 진작 빼고 있었는데 매출만 남아
              --   '1대당 매출' 의 분자·분모가 어긋나 있었다.
              {store_rules.not_test_sql()}
        ),
        tagged AS (
            SELECT *,
                -- 오리지널·제외는 IP명 단위로 쪼개지 않는다(구분별 매출만 쓰고, 타이틀 순위엔
                -- 안 넣는다). 안 그러면 기본_색상·자체 프레임 이름이 살아나 그룹이 폭증 → 재집계 OOM.
                CASE WHEN "IP구분" IN ('제외','오리지널(기본)','오리지널(포토이즘)')
                     THEN '' ELSE "IP명_raw" END  AS "IP명_c",
                CASE WHEN "IP구분" IN ('제외','오리지널(기본)','오리지널(포토이즘)')
                     THEN '' ELSE "날짜코드"  END  AS "날짜코드_c",
                CASE WHEN "IP구분" IN ('제외','오리지널(기본)','오리지널(포토이즘)')
                     THEN '' ELSE "접두어"   END  AS "접두어_c"
            FROM base
        ),
        grouped AS (
        SELECT
            "날짜","국가","국가코드","브랜드","대분류","타이틀명","매장 이름",
            "결제 단위","구좌","IP구분","날짜코드_c" AS "날짜코드","IP명_c" AS "IP명_raw",
            "접두어_c" AS "접두어","취소 여부",
            -- ★★취소는 **음수 거래**로 들어온다(포토이즘은 '취소 여부' 플래그가 안 붙는다).
            --   그냥 SUM 하면 같은 그룹의 정상 매출과 상쇄돼 **취소가 통째로 사라진다** —
            --   원본 518건/-300만원이 집계에서는 45행/-29만원으로만 남아 있었다.
            --   금액은 계속 net(상쇄분 반영)으로 두고, '얼마가 취소됐는지'를 별도 열로 보존한다.
            CAST(COALESCE(SUM(CASE WHEN "_amt" < 0 THEN 0 ELSE 1 END),0)
                                                   AS BIGINT) AS "건수",
            CAST(COALESCE(SUM(CASE WHEN "_amt" < 0 THEN -"_amt" ELSE 0 END),0)
                                                   AS BIGINT) AS "취소금액",
            CAST(COALESCE(SUM(CASE WHEN "_amt" < 0 THEN 1 ELSE 0 END),0)
                                                   AS BIGINT) AS "취소건수",
            CAST(COALESCE(SUM("_amt"),0)           AS BIGINT) AS "최종 결제 금액",
            CAST(COALESCE(SUM("_cpn"),0)           AS BIGINT) AS "쿠폰 할인 금액",
            CAST(COALESCE(SUM("_coin"),0)          AS BIGINT) AS "서비스코인"
        FROM tagged
        GROUP BY 1,2,3,4,5,6,7,8,9,10,11,12,13,14
    )
    -- ★한·영 통합(별칭)·타이틀 조립·최종 재집계를 전부 DuckDB 에서 끝낸다.
    --   예전엔 여기서 9M 행을 pandas 로 넘겨 apply_alias+groupby 했는데, 데이터가
    --   2년치(36M→9M)로 커지자 pandas→arrow 변환이 ArrowMemoryError 로 터졌다.
    --   alias_map 은 아래 register 로 붙인 소형 테이블(별칭→대표명).
    , aliased AS (
        SELECT
            "날짜","국가","국가코드","브랜드","대분류","타이틀명","매장 이름",
            "결제 단위","구좌","IP구분","취소 여부",
            -- 별칭 있으면 대표명, 없으면 원본. 원본이 null·공백이면 ''(빈 IP).
            -- ★안 그러면 이름 추출 실패한 아티스트/PICK 행이 'nan'·'None' 이라는
            --   가짜 IP 로 화면에 뜬다(기존 pandas 방식의 잠복 버그).
            COALESCE(m."v", NULLIF(TRIM(g."IP명_raw"), ''), '') AS "_ip",
            g."날짜코드" AS "_date", g."접두어" AS "_pfx",
            g."건수", g."취소금액", g."취소건수",
            g."최종 결제 금액", g."쿠폰 할인 금액", g."서비스코인"
        FROM grouped g
        LEFT JOIN alias_map m ON TRIM(g."IP명_raw") = m."k"
    )
    SELECT
        "날짜","국가","국가코드","브랜드","대분류","타이틀명","매장 이름",
        "결제 단위","구좌","IP구분","취소 여부",
        "_ip" AS "IP명",
        -- ★접두어(PW/L/SP/렌탈…)를 타이틀 앞에 살린다. 안 그러면 다른 제품이
        --   같은 타이틀로 합쳐져 남의 정산에 딸려 들어간다(2026-08-07, ip_classify 주석 참고).
        --   IP명은 접두어 없이 두므로 같은 IP 롤업·필터는 그대로 묶인다.
        CASE WHEN "_ip"='' THEN ''
             WHEN "_date"='' THEN NULLIF(TRIM("_pfx" || ' ' || "_ip"), '')
             ELSE TRIM("_pfx" || ' ' || "_date" || ' ' || "_ip") END AS "타이틀",
        CAST(SUM("건수")           AS BIGINT) AS "건수",
        CAST(SUM("취소금액")       AS BIGINT) AS "취소금액",
        CAST(SUM("취소건수")       AS BIGINT) AS "취소건수",
        CAST(SUM("최종 결제 금액") AS BIGINT) AS "최종 결제 금액",
        CAST(SUM("쿠폰 할인 금액") AS BIGINT) AS "쿠폰 할인 금액",
        CAST(SUM("서비스코인")     AS BIGINT) AS "서비스코인"
    FROM aliased
    GROUP BY 1,2,3,4,5,6,7,8,9,10,11,12,13
    ORDER BY "날짜" DESC, "국가" ASC, "매장 이름" ASC
    """).to_arrow_table()
    arrow = dict_encode_strings(df)
    _write_atomic(arrow, PARQ_AGG)
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
              -- ★테스트 매장은 매출에서도 뺀다 (store_rules 주석 참고).
              --   장비 목록은 진작 빼고 있었는데 매출만 남아
              --   '1대당 매출' 의 분자·분모가 어긋나 있었다.
              {store_rules.not_test_sql()}
        GROUP BY 1,2,3
        ORDER BY 1 DESC, 2
    """).to_arrow_table()

    _write_atomic(arrow, PARQ_HOURLY)
    mb = PARQ_HOURLY.stat().st_size / 1024 / 1024
    print(f"     저장: {PARQ_HOURLY.name}  ({mb:.1f} MB,  {arrow.num_rows:,}행)")


def build_orig(con, parq: str):
    """오리지널(BASIC) 프레임별 매출 — '구좌타입 분석' 타이틀 상세의 오리지널 탭 전용.

    본 집계(build_agg)는 오리지널을 IP명='' 로 접어 프레임 단위가 없다(그룹 폭증/OOM 방지).
    여기서 **매장 차원을 뺀 경량 집계**(날짜·국가·브랜드·IP구분·프레임)를 따로 만들어
    오리지널 프레임 순위만 보여준다. (매장 필터는 이 탭에 적용 안 됨 — 날짜·국가·브랜드만.)
    ※ 브랜드(Box/Colored…)는 매장별 탭의 '상품' 전용 필터가 오리지널에도 걸리도록 2026-07-28 추가.
    """
    print("  [3/3] 오리지널 프레임 집계(경량)...")
    arrow = con.execute(f"""
        SELECT
            TRY_CAST("날짜" AS DATE)                              AS "날짜",
            COALESCE("국가", '')                                  AS "국가",
            COALESCE("국가코드", '')                              AS "국가코드",
            COALESCE("브랜드", '')                                AS "브랜드",
            COALESCE("결제 단위", 'KRW')                          AS "결제 단위",
            ({ip_classify.IP_GUBUN_SQL})                          AS "IP구분",
            COALESCE(TRIM(CAST("프레임 이름" AS VARCHAR)), '')    AS "프레임",
            CAST(COUNT(*)                                            AS BIGINT) AS "건수",
            CAST(COALESCE(SUM(TRY_CAST("최종 결제 금액" AS BIGINT)),0) AS BIGINT) AS "최종 결제 금액",
            CAST(COALESCE(SUM(TRY_CAST("쿠폰 할인 금액" AS BIGINT)),0) AS BIGINT) AS "쿠폰 할인 금액",
            CAST(COALESCE(SUM({COIN_FIX}),0)                       AS BIGINT) AS "서비스코인"
        FROM read_parquet('{parq}')
        WHERE "날짜" IS NOT NULL AND TRIM(CAST("날짜" AS VARCHAR)) != ''
              -- ★테스트 매장은 매출에서도 뺀다 (store_rules 주석 참고).
              --   장비 목록은 진작 빼고 있었는데 매출만 남아
              --   '1대당 매출' 의 분자·분모가 어긋나 있었다.
              {store_rules.not_test_sql()}
          AND ({ip_classify.IP_GUBUN_SQL}) IN ('오리지널(포토이즘)','오리지널(기본)')
        GROUP BY 1,2,3,4,5,6,7
    """).to_arrow_table()
    arrow = dict_encode_strings(arrow)
    _write_atomic(arrow, PARQ_ORIG)
    mb = PARQ_ORIG.stat().st_size / 1024 / 1024
    print(f"     저장: {PARQ_ORIG.name}  ({mb:.1f} MB,  {arrow.num_rows:,}행)")


def _connect(limit: str):
    """OOM 방지: 메모리 상한 + 디스크 스필(temp_directory). master 가 커지면
    (수천만 행) 기본 무제한 설정으로는 GROUP BY 중 OutOfMemory 로 집계가 조용히
    실패한다 (photoism_ingest 와 동일한 안전장치)."""
    con = duckdb.connect()
    tdir = BASE_DIR / "data" / "_duckdb_tmp"
    tdir.mkdir(parents=True, exist_ok=True)
    con.execute(f"PRAGMA memory_limit='{limit}'")
    con.execute("PRAGMA threads=1")
    con.execute("PRAGMA preserve_insertion_order=false")
    con.execute(f"PRAGMA temp_directory='{str(tdir).replace(chr(92), '/')}'")
    con.execute("PRAGMA max_temp_directory_size='20GB'")
    return con


# 1GB 로 두 번, 그래도 안 되면 한도를 올려 한 번. 처음부터 올리지 않는 건 대시보드
# (8503)와 같은 16GB 를 나눠 쓰기 때문이다 — 서로 잡아먹으면 둘 다 죽는다.
_LIMITS = ("1GB", "1GB", "2GB")


def _retry(fn, parq):
    """★★간헐적으로 죽는다 — 같은 데이터로 다시 돌리면 지나간다 (2026-08-20 실측).

    그날 09:00 수집에서 DuckDB 가 **13.5 GiB 짜리 블록 하나**를 요청하며
    OutOfMemory 로 죽었는데, 같은 파일·같은 설정으로 다시 돌리니 112초에 끝났다
    (카디널리티 오추정 + 그때그때 다른 여유 메모리로 보인다).

    한 번 실패로 끝내면 **원장만 갱신되고 집계는 어제 것**으로 남는다. 대시보드는
    집계본을 읽으므로 최신인 척 옛 숫자를 보여 준다 — 실제로 그날 08-19 매출이
    54,153건 대신 **66건**으로 찍혀 있었고, 수집은 성공으로 기록돼 아무도 몰랐다.

    단계별로 재시도한다. build_agg 는 됐는데 build_hourly 만 죽었을 때 성공한
    단계까지 다시 돌리면 그만큼(약 2분) 헛돈다.
    """
    last = None
    for i, limit in enumerate(_LIMITS):
        con = _connect(limit)
        try:
            fn(con, parq)
            return
        except Exception as e:                                # noqa: BLE001
            last = e
            print(f"  [재시도 {i + 1}/{len(_LIMITS)}] {fn.__name__} 실패 "
                  f"({type(e).__name__}: {str(e)[:120]})")
        finally:
            con.close()
        time.sleep(10)
    raise RuntimeError(
        f"{fn.__name__} 를 {len(_LIMITS)}번 시도해도 못 만들었어요") from last


def main():
    if not PARQ_IN.exists():
        print(f"[오류] 파일 없음: {PARQ_IN}")
        sys.exit(1)

    in_mb = PARQ_IN.stat().st_size / 1024 / 1024
    print(f"집계 시작: {PARQ_IN.name}  ({in_mb:.0f} MB)")

    parq = str(PARQ_IN).replace("\\", "/")
    for fn in (build_agg, build_hourly, build_orig):
        _retry(fn, parq)

    print("[완료] 집계 파일 3개 생성")


if __name__ == "__main__":
    main()
