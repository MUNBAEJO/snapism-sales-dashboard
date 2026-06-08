"""
master_photoism.csv → master_photoism.parquet 변환 스크립트
청크 단위로 처리하여 메모리 부담 없이 변환합니다.
"""
import sys
from pathlib import Path
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

BASE_DIR   = Path(__file__).parent
CSV_PATH   = BASE_DIR / "data" / "master_photoism.csv"
PARQ_PATH  = BASE_DIR / "data" / "master_photoism.parquet"
CHUNK_SIZE = 300_000

def main():
    if not CSV_PATH.exists():
        print(f"[오류] 파일 없음: {CSV_PATH}")
        sys.exit(1)

    size_mb = CSV_PATH.stat().st_size / 1024 / 1024
    print(f"변환 시작: {CSV_PATH.name}  ({size_mb:.0f} MB)")
    print(f"청크 크기: {CHUNK_SIZE:,}행 단위")

    # 첫 청크로 스키마 확정 (dtype=str로 읽어 타입 불일치 방지)
    reader = pd.read_csv(CSV_PATH, encoding="utf-8-sig", dtype=str,
                         chunksize=CHUNK_SIZE)

    writer = None
    total  = 0
    fixed_schema = None

    try:
        for i, chunk in enumerate(reader):
            table = pa.Table.from_pandas(chunk, preserve_index=False)
            if writer is None:
                # null 타입 컬럼은 string으로 교체 (빈 컬럼 처리)
                fields = []
                for field in table.schema:
                    if pa.types.is_null(field.type):
                        fields.append(field.with_type(pa.string()))
                    else:
                        fields.append(field)
                fixed_schema = pa.schema(fields)
                table = table.cast(fixed_schema)
                writer = pq.ParquetWriter(
                    PARQ_PATH, fixed_schema, compression="snappy"
                )
            else:
                # 스키마 강제 통일 (null 타입 컬럼은 string으로 캐스팅)
                casted_arrays = []
                for i_col, field in enumerate(fixed_schema):
                    col = table.column(i_col)
                    if col.type != field.type:
                        col = col.cast(field.type, safe=False)
                    casted_arrays.append(col)
                table = pa.table(
                    {field.name: arr for field, arr in zip(fixed_schema, casted_arrays)},
                    schema=fixed_schema
                )
            writer.write_table(table)
            total += len(chunk)
            print(f"  청크 {i+1}: {total:,}행 처리됨", end="\r")
    finally:
        if writer:
            writer.close()

    out_mb = PARQ_PATH.stat().st_size / 1024 / 1024
    print(f"\n[완료] {total:,}행 → {PARQ_PATH.name}  ({out_mb:.0f} MB)")
    print(f"압축률: {size_mb:.0f} MB → {out_mb:.0f} MB  ({out_mb/size_mb*100:.0f}%)")

if __name__ == "__main__":
    main()
