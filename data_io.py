"""대시보드 공용 데이터 입출력 헬퍼.

목적
  - 스내피즘 master.csv(41MB) 대신 master.parquet 을 우선 읽어 로딩 5~15배 단축.
  - @st.cache_data 캐시가 데일리 크롤로 데이터가 바뀌면 자동 무효화되도록 파일
    변경시각(mtime)을 캐시 키에 넣는 표준 방식 제공.

비파괴 원칙: parquet 는 master.csv 를 '원본 그대로'(로더와 동일하게 read_csv) 저장한
것이라, 로더의 후처리(to_datetime/to_numeric 등)는 변경 없이 그대로 동작한다.
parquet 가 없거나 csv 가 더 최신이면 자동으로 csv 로 폴백한다(정확성 우선).
"""
import os
from pathlib import Path

import pandas as pd

CSV_READ_KWARGS = {"encoding": "utf-8-sig", "low_memory": False}


def file_version(path) -> float:
    """파일 변경시각. @st.cache_data 인자로 넘기면 파일이 바뀔 때 캐시 자동 무효화.
    파일이 없으면 0.0. parquet/csv 중 더 최신 것을 기준으로 한다."""
    p = Path(path)
    vers = [0.0]
    for cand in (p, _parquet_for(p)):
        try:
            vers.append(os.path.getmtime(cand))
        except OSError:
            pass
    return max(vers)


def _parquet_for(csv_path: Path) -> Path:
    return Path(csv_path).with_suffix(".parquet")


def read_master(csv_path, parquet_path=None, columns=None, **csv_kwargs) -> pd.DataFrame:
    """parquet 우선(존재하고 csv 보다 오래되지 않았으면) → 아니면 csv. 원본 그대로 반환.

    columns: 필요한 컬럼만 읽어 메모리를 줄인다(예: 1천만 행 상세 parquet).
             None이면 전체 컬럼. parquet/csv 양쪽에 적용된다."""
    csv_path = Path(csv_path)
    parquet_path = Path(parquet_path) if parquet_path else _parquet_for(csv_path)

    if parquet_path.exists():
        use_parquet = (not csv_path.exists()) or (
            parquet_path.stat().st_mtime >= csv_path.stat().st_mtime - 1
        )
        if use_parquet:
            try:
                return pd.read_parquet(parquet_path, columns=columns)
            except Exception:
                pass  # 손상 시 csv 폴백

    if csv_path.exists():
        kw = dict(CSV_READ_KWARGS)
        kw.update(csv_kwargs)
        if columns is not None:
            kw["usecols"] = columns
        return pd.read_csv(csv_path, **kw)

    return pd.DataFrame()


def rebuild_parquet(csv_path, parquet_path=None) -> str:
    """master.csv 를 로더와 동일하게 읽어 parquet 로 저장(데일리 ingest 후 호출).
    반환: 결과 메시지."""
    csv_path = Path(csv_path)
    parquet_path = Path(parquet_path) if parquet_path else _parquet_for(csv_path)
    if not csv_path.exists():
        return f"[skip] csv 없음: {csv_path}"
    df = pd.read_csv(csv_path, **CSV_READ_KWARGS)
    # object 컬럼은 문자열로 통일(혼합형 parquet 저장 안정화)
    for c in df.columns:
        if df[c].dtype == "object":
            df[c] = df[c].astype("string")
    df.to_parquet(parquet_path, compression="snappy", index=False)
    mb = parquet_path.stat().st_size / 1024 / 1024
    return f"[완료] {parquet_path.name}  ({mb:.1f} MB, {len(df):,}행)"


if __name__ == "__main__":
    # 단독 실행 시 스내피즘 master.csv → master.parquet 재생성
    base = Path(__file__).parent / "data"
    print(rebuild_parquet(base / "master.csv"))
