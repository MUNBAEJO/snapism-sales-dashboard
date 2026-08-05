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

# ★★매장 개명 매핑 (옛 이름 → 현재 이름) — 2026-08-04 추가
#
# 왜 필요한가: 아래 중복제거 키에 **`매장 이름`이 들어간다**. 어드민에서 매장 이름을
# 바꾸고 과거분을 새 이름으로 다시 내려주면, 같은 거래가 키가 달라져 **둘 다 살아남는다.**
# 그러면 매출이 두 번 잡히고 그대로 정산서에 실린다(실제로 FLARE U 정산서가 ₩125,000 과대였다).
#
# 아래 6쌍은 추측이 아니라 실측이다. `결제일시 + 단말기번호 + 승인번호 + 상품 이름 + 금액`
# 이 완전히 같고 매장 이름만 다른 거래를 전수 조사해 찾았다(3,444건, 2026-04-16~06-08).
# 카드 승인번호는 단말기·시각당 유일하므로 물리적으로 별개 거래일 수 없다.
# 같은 기간 월별 집계도 양쪽이 완전히 일치한다(2026-05: 대전 은행 595건/₩2,967,500 …).
#
# ★옛 이름 데이터를 '지우면' 안 된다. 2026-01-01~04-15 구간은 새 이름 재수집본이 없어
#   옛 이름으로만 1회 존재한다(2,704건). 지우면 1~3월 매출이 통째로 사라진다.
#   그래서 삭제가 아니라 **이름을 바꿔서** 중복제거가 알아서 합치게 한다.
#
# 새 개명이 생기면 여기 한 줄 추가하면 된다. 탐지 방법:
#   같은 (결제일시, 단말기번호, 승인번호, 상품 이름, 최종 결제 금액) 인데
#   `매장 이름` 이 2개 이상인 그룹을 찾는다.
STORE_RENAME = {
    "포트 대전 은행점":       "대전 은행 2호점",
    "포트 경남 창원 상남점":   "경남 창원 상남 2호점",
    "포트 부산 부산대점":     "부산 부산대 2호점",
    "포트 대구 동성로점":     "대구 동성로 3호점",
    "포트 전북 전주 전북대점": "전북 전주 전북대 2호점",
    "포트 경기 광주 경안점":   "경기 광주 경안 컬러드",
}


# ★★중복제거 키가 '진짜 별개 거래'를 뭉개던 문제 — 2026-08-05 수정
#
# 옛 키는 `결제일시|매장|상품|단가|결제수단|승인번호` 였다. 카드 결제는 승인번호가
# 있어서 구분이 되는데, **FREE·COIN 은 승인번호가 비어 있다.** 그러면 같은 분(分)에
# 같은 매장에서 같은 상품이 두 번 팔리면 키가 완전히 같아져 **한 건으로 합쳐진다.**
#
# 실측(2026-08-05, raw 162개 파일 1,806,340행):
#   · 파일 내부 자기충돌 25,318건. 나쁜 날은 한 파일의 21.3%(kr_20260610).
#   · 충돌 그룹 9,051개를 매출 ID 로 확인하니 **100% 가 서로 다른 거래**였다.
#   · 결과적으로 12,4xx건(약 +3%)이 통째로 사라져 있었다. 거의 전부 ₩0(FREE·COIN)이라
#     매출액은 사실상 안 변하지만, **정산서 수량은 '건수' 기준**이라 정산에도 걸린다.
#
# 고치는 방법: 키에 **파일 안에서의 순번**을 붙인다.
#   · 같은 파일에 똑같아 보이는 줄이 3개면 #0 #1 #2 로 갈라져 셋 다 산다.
#   · 같은 거래가 두 파일에 겹쳐 들어와도 양쪽에서 같은 순번을 받아 그대로 합쳐진다.
#   · 순번은 **매출 ID 오름차순**으로 매긴다. 파일마다 정렬이 달라도 결과가 같아야 해서다.
#     옛 포맷(24열)엔 매출 ID 가 아예 없는데, 그건 파일에 적힌 순서를 그대로 쓴다.
#
# 검증: 매출 ID 가 있는 행만 놓고 (소스, 매출 ID) 유일수 328,965 를 정답으로 봤을 때
#       옛 키는 325,664(-3,301), **새 키는 328,965(오차 0)** 였다.
#
# ★`_seq` 는 master.csv 에 컬럼으로 남긴다. 다음 수집 때 옛 행과 새 행이 같은 규칙으로
#   키를 만들어야 겹침 판정이 되기 때문이다. 지우지 말 것.
SEQ_COL = "_seq"


def _dup_key_base(df: pd.DataFrame) -> pd.Series:
    """중복제거 키의 앞부분(순번 제외). 파일 단위·전체 단위에서 **똑같이** 나와야 한다."""
    def col(name):
        return df[name].astype(str) if name in df.columns else pd.Series("", index=df.index)

    store = col("매장 이름").str.strip().replace(STORE_RENAME)
    return (col("결제일시")
            + "|" + store
            + "|" + col("상품 이름").str.strip()
            + "|" + (df["상품 단가"].map(clean_amount).astype(str)
                     if "상품 단가" in df.columns else "")
            + "|" + col("결제 수단")
            + "|" + (df["승인번호"].fillna("").astype(str)
                     if "승인번호" in df.columns else ""))


def _assign_seq(df: pd.DataFrame) -> pd.Series:
    """파일 하나 안에서 '같아 보이는' 거래에 0,1,2… 순번. 매출 ID 순으로 매긴다.

    ★**승인번호가 있는 행은 손대지 않는다(항상 0).** 왜 이렇게까지 조심하나:
      승인번호가 붙은 채로 키가 겹치는 행이 전체 25,318건 중 **74건** 있는데,
      들여다보니 같은 단말기 · 같은 승인번호 · 같은 분 · 같은 금액인데 매출 ID 만
      다르다(186106/186103 등). 카드 승인 하나에 매출 레코드가 둘 달린 건지 정말
      별개 결제인지 CSV 만으로는 못 가린다.
      가르면 매출이 ₩309,800 늘고, 안 가르면 건수만 손해다. **정산 금액이 걸린
      쪽으로 틀리면 안 되니** 안 가르는 쪽을 택했다(사용자 확인 전까지).
      실익도 거의 없다 — 진짜 손실은 승인번호 없는 25,244건(99.7%)에 있다.
      ※ CMS 에 물어 '별개 결제'로 확인되면 아래 `_ap` 조건만 빼면 된다.
    """
    base = _dup_key_base(df)
    sid = (pd.to_numeric(df["매출 ID"], errors="coerce")
           if "매출 ID" in df.columns else pd.Series(float("nan"), index=df.index))
    t = pd.DataFrame({"k": base, "sid": sid}).sort_values(
        "sid", na_position="last", kind="stable")
    seq = t.groupby("k").cumcount().reindex(df.index)
    _ap = (df["승인번호"].fillna("").astype(str).str.strip() != ""
           if "승인번호" in df.columns else pd.Series(False, index=df.index))
    return seq.where(~_ap, 0)


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

    # ★수정시각(mtime) 오름차순 정렬 — 가장 최근 다운로드가 맨 뒤로 가서 keep="last"에서 이김.
    #   파일명순으로 정렬하면 옛 일별파일(kr_20260708.csv)이 최신 범위파일(kr_20260416_20260714.csv)보다
    #   뒤로 가 취소 전 값이 살아남는 버그가 있었음(대만/한국 취소 미반영의 진짜 원인).
    csv_files = sorted(glob.glob(str(RAW_DIR / "*.csv")), key=lambda f: Path(f).stat().st_mtime)
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
            # ★순번은 **파일 단위**로 매겨야 한다. 전체를 합친 뒤에 매기면 겹쳐 들어온
            #   같은 거래끼리도 #0 #1 로 갈라져 중복이 그대로 살아남는다.
            df[SEQ_COL] = _assign_seq(df)
            new_dfs.append(df)
            _dup = int((df[SEQ_COL] > 0).sum())
            print(f"  OK {Path(f).name}  ({len(df):,}건)  [{source}]"
                  + (f"  동일키 {_dup:,}건 분리" if _dup else ""))
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

    # ★매장 개명 정규화 — 중복제거 **전에** 해야 한다(키에 매장 이름이 들어가므로).
    #   여기서 옛 이름을 현재 이름으로 바꿔 놓으면 아래 drop_duplicates 가 알아서 합친다.
    if "매장 이름" in combined.columns and STORE_RENAME:
        _hit = combined["매장 이름"].isin(STORE_RENAME)
        _n = int(_hit.sum())
        if _n:
            combined.loc[_hit, "매장 이름"] = combined.loc[_hit, "매장 이름"].map(STORE_RENAME)
            # ★콘솔이 cp949 라 em대시(—) 를 쓰면 UnicodeEncodeError 로 죽는다(실제로 죽었다).
            print(f"  매장 개명 정규화: {_n:,}건 "
                  f"({len(STORE_RENAME)}개 매장, 옛 이름 -> 현재 이름)")

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

    # 중복 제거 (결제일시+매장+상품+결제수단+승인번호 +파일 내 순번)
    # ★순번을 왜 붙이는지는 파일 위쪽 SEQ_COL 주석 참고. 빼면 FREE·COIN 이 뭉개진다.
    #   옛 master(순번 없음)를 읽었을 땐 0 으로 채운다 — 그 행들은 이미 옛 키로
    #   1건씩만 남아 있어서 신규 파일의 #0 과 정확히 맞물린다(#1 이상은 새로 추가된다).
    if SEQ_COL not in combined.columns:
        combined[SEQ_COL] = 0
    combined[SEQ_COL] = pd.to_numeric(combined[SEQ_COL], errors="coerce").fillna(0).astype(int)
    combined["_key"] = _dup_key_base(combined) + "#" + combined[SEQ_COL].astype(str)
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
