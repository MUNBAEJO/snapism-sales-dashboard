"""
주간 매출 분석 + Gemini Search Grounding 리포트 생성 모듈
스내피즘 / 포토이즘 분리 분석
"""
import json
import os
import re
import sys
import pandas as pd
from pathlib import Path
from datetime import datetime, timedelta, date

sys.path.insert(0, str(Path(__file__).parent))
import data_io  # parquet 우선 로딩 헬퍼

BASE_DIR     = Path(__file__).parent
DATA_DIR     = BASE_DIR / "data"
CONFIG_FILE  = BASE_DIR / "config.json"
INSIGHT_FILE = DATA_DIR / "weekly_insight.json"
MASTER_SNAP  = DATA_DIR / "master.csv"
MASTER_PHOTO = DATA_DIR / "master_photoism.csv"
ALIAS_FILE   = DATA_DIR / "frame_alias.json"

# 커스텀 카테고리 — IP 분석에서 제외 (스내피즘)
CUSTOM_CATEGORIES = {"포토카드(커스텀)", "스티커(커스텀)"}

# 포토이즘 타이틀명 날짜/코드 prefix 제거 패턴
# "260530 SM ent" → "SM ent"  /  "L 260416 가나디" → "가나디"  /  "PW 260518 cortis" → "cortis"
_PHOTO_PREFIX = re.compile(r"^(?:[A-Z]{1,2}\s+)?\d{6}\s+")


# ─────────────────────────────────────────────
# 설정 로드
# ─────────────────────────────────────────────

def load_config() -> dict:
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_gemini_key(api_key: str):
    cfg = load_config()
    cfg["gemini_api_key"] = api_key
    _tmp = str(CONFIG_FILE) + ".tmp"          # 원자적 저장(torn read 방지)
    with open(_tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(_tmp, CONFIG_FILE)


def load_frame_alias() -> dict:
    if not ALIAS_FILE.exists():
        return {}
    with open(ALIAS_FILE, encoding="utf-8") as f:
        raw = json.load(f)
    return {k: v for k, v in raw.items() if not k.startswith("_")}


# ─────────────────────────────────────────────
# 날짜 유틸
# ─────────────────────────────────────────────

def get_week_range(offset: int = 0):
    """offset=0: 이번주(월~일), offset=-1: 지난주"""
    today = date.today()
    monday = today - timedelta(days=today.weekday()) + timedelta(weeks=offset)
    sunday = monday + timedelta(days=6)
    return monday, sunday


# ─────────────────────────────────────────────
# KRW 환산 공통 로직
# ─────────────────────────────────────────────

def _apply_krw(df: pd.DataFrame, rates: dict) -> pd.DataFrame:
    """KRW 환산 — 벡터화(행단위 apply 금지: 1천만 행에서 사실상 멈춤)."""
    df["금액"] = pd.to_numeric(df["최종 결제 금액"], errors="coerce").fillna(0)
    df["결제 단위"] = df["결제 단위"].fillna("KRW").astype(str).str.strip()

    rate_map = {}
    for k, v in (rates or {}).items():
        try:
            rate_map[k] = float(v)
        except (TypeError, ValueError):
            continue
    rate_map["KRW"] = 1.0  # KRW·미등록 통화는 1배(원본 로직 유지)

    rate = df["결제 단위"].map(rate_map).fillna(1.0)
    df["금액_KRW"] = df["금액"] * rate
    return df


# ─────────────────────────────────────────────
# 스내피즘 데이터 로드
# ─────────────────────────────────────────────

def load_snapism(ip_only: bool = True) -> pd.DataFrame:
    """
    master.csv 로드
    IP = 프레임 이름 (IP 이름 컬럼은 0.6%만 채워져 있어 프레임 이름 사용)
    ip_only=True: 포토카드(커스텀), 스티커(커스텀) 제외
    """
    if not MASTER_SNAP.exists():
        return pd.DataFrame()

    cfg   = load_config()
    rates = cfg.get("exchange_rates", {})

    # 필요한 컬럼만 읽어 메모리 절약
    cols = ["프레임 이름", "상품 카테고리", "취소 여부", "최종 결제 금액", "결제 단위", "날짜"]
    df = data_io.read_master(MASTER_SNAP, columns=cols)  # parquet 우선(없으면 csv)

    # 취소 제거
    df = df[df["취소 여부"].astype(str).str.lower() != "true"].copy()

    # 커스텀 제외
    if ip_only and "상품 카테고리" in df.columns:
        df = df[~df["상품 카테고리"].isin(CUSTOM_CATEGORIES)].copy()

    # IP 컬럼
    df["IP"] = df["프레임 이름"].astype(str).str.strip()

    # 날짜 정규화
    df["날짜_dt"] = pd.to_datetime(df["날짜"], errors="coerce").dt.date

    # KRW 환산
    df = _apply_krw(df, rates)

    # 빈/NaN IP 제거
    df = df[(df["IP"].str.len() > 0) & (df["IP"].str.lower() != "nan")].copy()

    return df


# ─────────────────────────────────────────────
# 포토이즘 데이터 로드
# ─────────────────────────────────────────────

def _clean_photo_title(title: str) -> str:
    """
    "260530 SM ent"   → "SM ent"
    "L 260416 가나디"  → "가나디"
    "PW 260518 cortis" → "cortis"
    """
    return _PHOTO_PREFIX.sub("", str(title).strip()).strip()


def load_photoism(ip_only: bool = True) -> pd.DataFrame:
    """
    master_photoism.csv 로드
    IP = 타이틀명 (날짜 prefix 제거)  + frame_alias.json 적용
    ip_only=True: 타이틀명 없는 건 제외 (커스텀 상당)
    """
    if not MASTER_PHOTO.exists():
        return pd.DataFrame()

    cfg   = load_config()
    rates = cfg.get("exchange_rates", {})
    alias = load_frame_alias()

    # 1천만+ 행 상세라 필요한 컬럼만 읽는다 (전체 24컬럼 로드 시 메모리 부족으로 멈춤)
    cols = ["타이틀명", "취소 여부", "최종 결제 금액", "결제 단위", "날짜"]
    ph = data_io.read_master(MASTER_PHOTO, columns=cols)  # 124MB parquet 우선(2GB csv 회피)

    # 취소 제거
    ph = ph[ph["취소 여부"].astype(str).str.lower() != "true"].copy()

    # IP = 타이틀명 (날짜 prefix 제거 + alias 적용) — 벡터화(1천만 행 apply 회피)
    ph["IP_raw"] = ph["타이틀명"].astype(str).str.strip()
    ph["IP"] = ph["IP_raw"].str.replace(_PHOTO_PREFIX, "", regex=True).str.strip()
    if alias:
        ph["IP"] = ph["IP"].map(alias).fillna(ph["IP"])

    # ip_only: 타이틀명 없는 건 제외
    if ip_only:
        ph = ph[(ph["IP"].str.len() > 0) & (ph["IP"].str.lower() != "nan")].copy()

    # 날짜 정규화
    ph["날짜_dt"] = pd.to_datetime(ph["날짜"], errors="coerce").dt.date

    # KRW 환산
    ph = _apply_krw(ph, rates)

    return ph


# ─────────────────────────────────────────────
# 주간 분석 (브랜드 공통)
# ─────────────────────────────────────────────

def analyze_weekly(
    df: pd.DataFrame,
    this_start: date,
    this_end: date,
    prev_start: date,
    prev_end: date,
) -> dict:
    """이번 주 vs 지난 주 IP별 매출 분석"""

    this = df[(df["날짜_dt"] >= this_start) & (df["날짜_dt"] <= this_end)].copy()
    prev = df[(df["날짜_dt"] >= prev_start) & (df["날짜_dt"] <= prev_end)].copy()

    this_ip  = this.groupby("IP")["금액_KRW"].sum().rename("이번주")
    prev_ip  = prev.groupby("IP")["금액_KRW"].sum().rename("지난주")
    this_cnt = this.groupby("IP")["금액_KRW"].count().rename("이번주_건수")

    ip_df = pd.concat([this_ip, prev_ip, this_cnt], axis=1).fillna(0)
    ip_df["변동"] = ip_df["이번주"] - ip_df["지난주"]
    ip_df["변동률"] = ip_df.apply(
        lambda r: (r["이번주"] - r["지난주"]) / r["지난주"] * 100
        if r["지난주"] > 1000 else (100.0 if r["이번주"] > 0 else 0.0),
        axis=1,
    )
    ip_df = ip_df[ip_df["이번주"] > 0].sort_values("이번주", ascending=False)

    total_this = int(this["금액_KRW"].sum())
    total_prev = int(prev["금액_KRW"].sum())
    wow = (total_this - total_prev) / total_prev * 100 if total_prev > 0 else 0.0

    summary = {
        "이번주_총매출": total_this,
        "지난주_총매출": total_prev,
        "wow_pct":       round(wow, 1),
        "이번주_건수":   len(this),
        "지난주_건수":   len(prev),
        "활성IP수":      int((ip_df["이번주"] > 0).sum()),
        "분석기간":      f"{this_start} ~ {this_end}",
        "비교기간":      f"{prev_start} ~ {prev_end}",
    }

    top5 = ip_df.head(5)

    # 급등: 이번주 100만원+ & +10% 이상
    rising = ip_df[
        (ip_df["이번주"] >= 1_000_000) & (ip_df["변동률"] >= 10)
    ].nlargest(5, "변동률")

    # 급락: 전주 100만원+ & -10% 이하
    falling = ip_df[
        (ip_df["지난주"] >= 1_000_000) & (ip_df["변동률"] <= -10)
    ].nsmallest(3, "변동률")

    return {
        "summary": summary,
        "ip_df":   ip_df,
        "top5":    top5,
        "rising":  rising,
        "falling": falling,
    }


# ─────────────────────────────────────────────
# Gemini 프롬프트 빌더 (이중 브랜드)
# ─────────────────────────────────────────────

def _brand_section(brand: str, a: dict) -> str:
    s  = a["summary"]
    t5 = a["top5"]
    ri = a["rising"]
    fa = a["falling"]

    def row_str(i, row, show_chg=True):
        base = f"  {i}. {row.name}: {int(row['이번주']):,}원"
        if show_chg and row["지난주"] > 0:
            sign = "+" if row["변동률"] >= 0 else ""
            base += f" ({sign}{row['변동률']:.0f}%)"
        return base

    t5_lines = "\n".join([row_str(i+1, r, False) for i, (_, r) in enumerate(t5.iterrows())])
    ri_lines = "\n".join([row_str(i+1, r) for i, (_, r) in enumerate(ri.iterrows())]) if len(ri) else "  (없음)"
    fa_lines = "\n".join([row_str(i+1, r) for i, (_, r) in enumerate(fa.iterrows())]) if len(fa) else "  (없음)"
    wow_sign = "+" if s["wow_pct"] >= 0 else ""

    return f"""
【{brand}】  {s['분석기간']}
총 매출: {s['이번주_총매출']:,}원  |  전주대비: {wow_sign}{s['wow_pct']}%
거래 건수: {s['이번주_건수']:,}건  |  활성 IP: {s['활성IP수']}개

Top 5:
{t5_lines}

급등:
{ri_lines}

급락:
{fa_lines}""".strip()


def build_dual_prompt(snap_a: dict, photo_a: dict) -> str:
    snap_sec  = _brand_section("스내피즘 (포토카드·스티커 굿즈 IP)", snap_a)
    photo_sec = _brand_section("포토이즘 (포토부스 IP 프레임)", photo_a)

    return f"""
당신은 K-pop·엔터테인먼트 포토부스 비즈니스 매출 분석가입니다.

스내피즘(Snapism): 아이돌·캐릭터 IP 기반 포토카드·스티커 굿즈를 키오스크에서 출력
포토이즘(Photoism): IP 테마 프레임을 적용한 포토부스 서비스

━━━ 이번 주 매출 데이터 ━━━

{snap_sec}

{photo_sec}

━━━━━━━━━━━━━━━━━━━━━━━━━

Google 검색으로 각 브랜드별 급등·급락 IP의 최근 1~2주 이슈(컴백, 행사, SNS 화제, 미디어 노출)를 파악한 뒤,
아래 형식으로 주간 회의 자료를 작성해주세요.

---

## 🟦 스내피즘 분석

### 이번 주 요약
(2~3문장)

### 급등 IP 원인 분석
(각 IP: 최근 이슈 검색 → 매출 급등 연결)

### 하락 IP 분석 및 대응
(각 IP: 원인 추정 + 단기 대응안)

### 다음 주 예상 포인트
(예정 컴백·행사·이슈 2~3개)

---

## 🟩 포토이즘 분석

### 이번 주 요약
(2~3문장)

### 급등 IP 원인 분석

### 하락 IP 분석 및 대응

### 다음 주 예상 포인트

---

## 💡 통합 인사이트 및 회의 아젠다

### 공통 관찰
(두 브랜드 간 공통 IP 성과 비교, 트렌드 교차점 등)

### 회의 아젠다 (3개)
1.
2.
3.

---
※ 한국어로 작성. 실무 회의에서 바로 활용 가능한 수준으로 구체적으로 작성.
※ 섹션 제목(## ###)은 그대로 유지.
    """.strip()


# ─────────────────────────────────────────────
# Gemini 호출
# ─────────────────────────────────────────────

# 무료 티어에서 Google Search Grounding이 되는 모델만.
# gemini-2.0-flash 는 무료 한도가 0이라(항상 429) 폴백에서 제외.
_GEMINI_MODELS = ["gemini-2.5-flash", "gemini-2.5-flash-lite"]

_QUOTA_MSG = (
    "Gemini 무료 사용량(분당·일일 요청 한도)을 초과했어요. "
    "1~2분 뒤 다시 시도하거나, Google AI Studio에서 결제를 등록하면 한도가 크게 늘어나요."
)


def _is_quota_error(e) -> bool:
    m = str(e)
    return "RESOURCE_EXHAUSTED" in m or "429" in m


def generate_gemini_report(api_key: str, snap_a: dict, photo_a: dict) -> dict:
    """Gemini 2.5 Flash + Google Search Grounding — 이중 브랜드 리포트"""
    try:
        import time
        from google import genai
        from google.genai.types import Tool, GenerateContentConfig, GoogleSearch
    except ImportError:
        return {"text": "", "error": "google-genai 패키지 없음: pip install google-genai"}

    if not api_key or not api_key.strip():
        return {"text": "", "error": "Gemini API 키가 설정되지 않았습니다."}

    prompt = build_dual_prompt(snap_a, photo_a)
    client = genai.Client(api_key=api_key.strip())
    gen_cfg = GenerateContentConfig(
        tools=[Tool(google_search=GoogleSearch())],
        temperature=0.4,
    )

    quota_hit = False
    last_err = None
    for model in _GEMINI_MODELS:
        for attempt in range(2):  # 일시적 분당 한도 대비 1회 재시도
            try:
                resp = client.models.generate_content(
                    model=model, contents=prompt, config=gen_cfg,
                )
                return {"text": resp.text, "error": None, "model": model}
            except Exception as e:
                last_err = e
                if _is_quota_error(e):
                    quota_hit = True
                    if attempt == 0:
                        time.sleep(6)   # 짧게 대기 후 같은 모델 1회 재시도
                        continue
                break  # 비-쿼터 에러거나 재시도 소진 → 다음 모델로

    if quota_hit:
        return {"text": "", "error": _QUOTA_MSG, "quota": True}
    return {"text": "", "error": f"Gemini 오류: {last_err}"}


# ─────────────────────────────────────────────
# 저장 / 로드
# ─────────────────────────────────────────────

def save_insight(payload: dict):
    DATA_DIR.mkdir(exist_ok=True)
    with open(INSIGHT_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, default=str)


def load_insight() -> dict | None:
    if not INSIGHT_FILE.exists():
        return None
    with open(INSIGHT_FILE, encoding="utf-8") as f:
        return json.load(f)
