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
import time
import json
import pandas as pd
from pathlib import Path
from datetime import datetime, timedelta

BASE_DIR    = Path(__file__).parent
CONFIG_FILE = BASE_DIR / "config.json"
RAW_DIR     = BASE_DIR / "raw_photoism"
DATA_DIR    = BASE_DIR / "data"
MASTER_FILE = DATA_DIR / "master_photoism.csv"        # 레거시(대용량) — 더 이상 갱신 안 함
# ★대량 재적재용 우회로 (2026-08-06).
#   PHOTOISM_MASTER 로 다른 parquet 을 지정하면 운영본을 안 건드리고 거기에 쌓는다.
#   전량 재적재는 월 단위로 20번 돌려야 하는데, 매번 운영 parquet 을 교체하면
#   그때마다 대시보드가 파일을 잡고 있어 실패하고(WinError 5) 서비스도 끊긴다.
#   별도 파일에 다 쌓은 뒤 **마지막에 한 번만** 바꾸는 게 맞다.
# ★PHOTOISM_SKIP_AGG=1 이면 집계 생성을 건너뛴다. 월마다 6.8M행 집계를 다시
#   만들 이유가 없다 — 마지막에 한 번만 만든다.
MASTER_PARQ = Path(os.environ.get("PHOTOISM_MASTER")
                   or (DATA_DIR / "master_photoism.parquet"))    # canonical

def load_config():
    with open(CONFIG_FILE, encoding="utf-8") as f:
        return json.load(f)

def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    # ★콘솔이 cp949 라 em대시(—) 같은 글자에서 print 가 죽는다(예약작업/백그라운드에서
    #   완료 직전 크래시 → 후속 단계 미실행). 콘솔로 못 쓰는 글자는 치환해 흘린다.
    import sys
    enc = getattr(sys.stdout, "encoding", None) or "utf-8"
    try:
        print(line)
    except UnicodeEncodeError:
        print(line.encode(enc, errors="replace").decode(enc, errors="replace"))

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
    # ★format="ISO8601" 을 반드시 지정한다.
    #   CMS 는 **취소 거래에만** 밀리초를 붙여 준다(2026-07-11T17:07:21.863).
    #   포맷을 안 주면 pandas 가 첫 행 기준으로 단일 포맷을 추론해서 밀리초가 붙은
    #   행을 전부 NaT 로 만들고, 아래 cutoff 필터(날짜 notna)에서 통째로 잘려나간다.
    #   그 결과 **취소가 한 건도 수집되지 않아** 취소된 매출이 그대로 정산에 남았다.
    #   (2026-07-11 한국 하루치에서만 취소 14건 유실 확인)
    # ★날짜 기준은 '결제일(지역)' = CMS 의 localPaymentDt 다 (2026-08-06 전환).
    #   `결제일` 은 나라마다 기준 시간대가 제각각이다. 한국 인스턴스는 KST 를,
    #   멕시코 인스턴스는 UTC 를 담아 준다. 그래서 `결제일` 로 날짜를 끊으면
    #   현지 7/31 밤 거래가 8/1 로 밀려 정산 기간에서 빠진다(멕시코 2건 실측,
    #   미국 KFA 도 같은 원인). 퀵사이트가 보여주는 값도 '결제일(지역)' 이라,
    #   담당자가 손으로 만든 정산 시트와 어긋나는 것도 전부 이 때문이었다.
    #
    #   ★전환 조건이었던 '전 기간 같은 기준' 은 2026-08-06 전량 재수집으로 충족했다.
    #     현지시각 열이 없는 옛 범위 파일 5,190개는 raw_photoism/_old_range 로
    #     빼 뒀다. 그게 섞이면 아래 폴백이 조용히 옛 기준을 되살려 한 테이블에
    #     두 기준이 앉는다 — 되돌리지 말 것.
    _old = pd.to_datetime(df.get("결제일", pd.Series(index=df.index, dtype=str)),
                          format="ISO8601", errors="coerce")
    if "결제일(지역)" in df.columns:
        결제일시 = pd.to_datetime(df["결제일(지역)"], format="ISO8601", errors="coerce")
        # ★빈 칸은 옛 열로 메운다. 안 메우면 날짜가 NaT 라 아래 필터에서 통째로 빠진다.
        if 결제일시.isna().any():
            결제일시 = 결제일시.fillna(_old)
    else:
        # 여기 오면 안 된다(_old_range 로 뺀 옛 파일). 조용히 옛 기준을 쓰지 말고 알린다.
        log(f"  [경고] 현지시각 열 없음 → 옛 '결제일' 기준으로 적재: {filepath.name}")
        결제일시 = _old

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
    """파일명에서 **마지막** 날짜(=구간 끝) 추출."""
    m = re.search(r"_(\d{8})\.xlsx$", fp.name)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%Y%m%d").date()
    except ValueError:
        return None


def _file_span(fp: Path):
    """파일이 담는 날짜 구간 (시작, 끝).

    ★파일명이 두 가지다.
        photoism_kr_20260711.xlsx            하루치 (2026-05-31 이후 수집분)
        photoism_kr_20260629_20260701.xlsx   3일치 (그 이전 수집분)
      `_file_date` 는 **끝 날짜**만 돌려주므로, 구간 파일을 끝 날짜로만 걸러내면
      월 경계에 걸친 파일이 통째로 빠진다. 그런데 병합은 그 구간의 옛 행을 지우므로
      **데이터가 사라진다**(6월 재수집에서 336행 유실 확인). 구간이 겹치면 읽고,
      행 단위 날짜 필터로 정확히 잘라낸다.
    """
    end = _file_date(fp)
    if end is None:
        return None, None
    m = re.search(r"_(\d{8})_(\d{8})\.xlsx$", fp.name)
    if m:
        try:
            return datetime.strptime(m.group(1), "%Y%m%d").date(), end
        except ValueError:
            pass
    return end, end


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
    # 두 번째 인수(선택)로 **종료일**을 주면 그 구간만 처리한다.
    #   ★대량 재수집용. 2026년 전체는 raw 3,300개 1.8GB 라 한 번에 concat 하면
    #     대시보드와 같은 서버에서 메모리가 터진다. 월 단위로 끊어 돌리기 위한 것.
    target_date = end_date = None
    if len(sys.argv) >= 2:
        try:
            target_date = datetime.strptime(sys.argv[1], "%Y-%m-%d").date()
        except ValueError:
            target_date = None
    if len(sys.argv) >= 3:
        try:
            end_date = datetime.strptime(sys.argv[2], "%Y-%m-%d").date()
        except ValueError:
            end_date = None

    config = load_config()
    DATA_DIR.mkdir(exist_ok=True)

    cutoff = target_date or _master_max_date()
    if cutoff:
        log(f"증분 누적: {cutoff} 이후 날짜만 처리 (기존 parquet 기준)")
    else:
        log("기존 parquet 없음 → raw 전체 처리")

    if end_date:
        log(f"종료일 지정: {end_date} 까지만 처리")

    all_files = sorted(RAW_DIR.glob("photoism_*.xlsx"))

    # ★파일 선택에 앞뒤 여유를 둔다.
    #   CMS 파일은 타임존 경계 때문에 인접일 거래가 섞여 들어온다(스필오버).
    #   예: 6/30 매출 일부가 photoism_xx_20260701_20260703.xlsx 에 들어 있다.
    #   여유 없이 자르면 그 행들이 통째로 사라진다(6/30 하루에만 834행 유실 확인).
    #   행 단위 날짜 필터가 아래에서 정확히 잘라내므로 넉넉히 읽어도 안전하다.
    MARGIN = timedelta(days=3)

    def _in_range(f):
        s, e = _file_span(f)
        if e is None:
            return False
        if cutoff is not None and e < cutoff - MARGIN:
            return False
        return not (end_date and s > end_date + MARGIN)

    sel = [f for f in all_files if cutoff is None or _in_range(f)]
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
        keep = _nd.notna() & (_nd >= cutoff)
        if end_date:                      # 타임존 경계로 딸려온 뒷날짜도 잘라낸다
            keep &= _nd <= end_date
        new_df = new_df[keep]
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
            # ★종료일을 준 경우 그 이후 날짜는 **기존 데이터를 그대로 남긴다.**
            #   안 그러면 이번에 안 읽은 뒷날짜가 통째로 지워진다(월 단위 재수집 사고).
            keep = f'TRY_CAST("날짜" AS DATE) < DATE \'{cutoff}\''
            if end_date:
                keep += f' OR TRY_CAST("날짜" AS DATE) > DATE \'{end_date}\''
            con.execute(f"""
                COPY (
                    SELECT * FROM read_parquet('{src}') WHERE {keep}
                    UNION ALL BY NAME
                    SELECT * FROM read_parquet('{tnew}')
                ) TO '{out}' (FORMAT PARQUET, COMPRESSION SNAPPY)
            """)
        else:
            con.execute(f"COPY (SELECT * FROM read_parquet('{tnew}')) TO '{out}' (FORMAT PARQUET, COMPRESSION SNAPPY)")
        total = con.execute(f"SELECT COUNT(*) FROM read_parquet('{out}')").fetchone()[0]
    finally:
        con.close()

    # ★★대시보드(8503)가 parquet 을 열고 있으면 os.replace 가 WinError 5 로 죽는다
    #   (2026-08-05 실제 발생). 그대로 두면 새 데이터가 통째로 버려지고 .tmp 만 남는다.
    #   윈도는 열린 파일을 못 지우므로 잠깐 기다렸다 다시 시도하고, 그래도 안 되면
    #   **새 파일을 지우지 말고** 무엇을 하면 되는지 알려 준다.
    for _try in range(6):
        try:
            os.replace(new_master, MASTER_PARQ)
            break
        except PermissionError:
            if _try == 5:
                log(f"[실패] parquet 교체 불가 — 다른 프로세스가 열고 있어요.")
                log(f"        대시보드(8503)를 잠깐 내리고 아래를 실행하면 이어집니다:")
                log(f'        python -c "import os;os.replace(r\'{new_master}\',r\'{MASTER_PARQ}\')"')
                log(f"        새 데이터는 {new_master} 에 그대로 보관돼 있어요.")
                return
            log(f"parquet 이 잠겨 있어요 — {(_try + 1) * 10}초 뒤 다시 시도 ({_try + 1}/5)")
            time.sleep((_try + 1) * 10)
    tmp_new.unlink(missing_ok=True)
    mb = MASTER_PARQ.stat().st_size / 1024 / 1024
    log(f"[완료] master_photoism.parquet 갱신 — 누적 {total:,}건 ({mb:.0f} MB)")

    # 집계 parquet 갱신 (build_photoism_agg 는 DuckDB 로 parquet 직접 읽음 → 메모리 안전)
    if os.environ.get("PHOTOISM_SKIP_AGG") == "1":
        log("집계 생성 건너뜀 (PHOTOISM_SKIP_AGG=1)")
        return
    try:
        from build_photoism_agg import main as build_agg
        log("집계 파일 갱신 중...")
        build_agg()
        log("집계 완료")
    except Exception as e:
        log(f"[경고] 집계 파일 갱신 실패 (수동 실행 필요): {e}")


if __name__ == "__main__":
    main()
