"""
포토이즘 IP 분류 공용 모듈.

- IP구분: 구좌(BASIC/WITH/EVENT) + 타이틀명/프레임명 접두어로 5종 분류
    아티스트 / 캐릭터 / 렌탈 / PICK / 기획(P)  (그 외 기본·자체 프레임은 '제외')

- 타이틀: **날짜 + IP명** 단위 (예: '260527 우주소녀'). 각 출시(날짜)별로 구분 유지.
    한국·글로벌 동시오픈이라 날짜가 동일 → 같은 (날짜 + IP)면 한·영 표기를 통합
    (예: '260527 WJSN' = '260527 우주소녀'). 날짜가 다르면(다른 출시) 절대 합치지 않음.
- IP명: 날짜를 뗀 대표 IP명 (예: '우주소녀'). 같은 IP의 여러 타이틀 롤업·필터용.

한·영 통합은 IP명 토큰에 ip_aliases.json(별칭→대표명)을 적용. 날짜는 그대로 유지.
집계(build_photoism_agg.py)와 대시보드가 동일 기준을 쓰도록 한 곳에서 관리.
"""
import re
import json
from functools import lru_cache
from pathlib import Path

BASE_DIR    = Path(__file__).parent
ALIAS_FILE  = BASE_DIR / "ip_aliases.json"

# IP 매출에 포함되는 구분(제외 제외)
IP_GUBUN_ORDER = ["아티스트", "캐릭터", "렌탈", "PICK", "기획(P)"]

# 대시보드에 '노출'하는 구분 (2026-07-21 사용자 결정).
# 기획(P)·렌탈·제외(기본/자체 프레임)는 수집·저장은 그대로 하되 화면에서만 뺀다.
# 되살리려면 이 목록에 다시 넣으면 된다.
IP_GUBUN_SHOWN = ["아티스트", "캐릭터", "PICK"]

# ── DuckDB SQL 표현식 ────────────────────────────────────────────
# IP구분: 구좌 + 타이틀명(WITH/EVENT) / 프레임명(BASIC) 접두어 기준
IP_GUBUN_SQL = """
CASE
  WHEN "구좌"='EVENT' THEN 'PICK'
  WHEN "구좌"='WITH' AND CAST("타이틀명" AS VARCHAR) LIKE '렌탈%'   THEN '렌탈'
  WHEN "구좌"='WITH' AND CAST("타이틀명" AS VARCHAR) LIKE 'L %'      THEN '캐릭터'
  WHEN "구좌"='WITH'                                                THEN '아티스트'
  WHEN "구좌"='BASIC' AND CAST("프레임 이름" AS VARCHAR) LIKE 'L %'  THEN '캐릭터'
  WHEN "구좌"='BASIC' AND CAST("프레임 이름" AS VARCHAR) LIKE 'P %'  THEN '기획(P)'
  ELSE '제외'
END
"""

# IP명 원천: BASIC 은 프레임명, 그 외(WITH/EVENT)는 타이틀명
_IP_SRC_SQL = "CASE WHEN \"구좌\"='BASIC' THEN \"프레임 이름\" ELSE \"타이틀명\" END"

# 접두어(렌탈/PW/L7/L/P/B/SP) 제거 → '날짜 이름' 형태
_PREFIX_STRIPPED_SQL = (
    f"regexp_replace(TRIM(CAST({_IP_SRC_SQL} AS VARCHAR)), "
    f"'^(렌탈|PW|L7|L|P|B|SP)\\s+', '')"
)
# 선두 날짜코드(YYMMDD 등 5~8자리). 없으면 ''
IP_DATE_SQL = f"regexp_extract({_PREFIX_STRIPPED_SQL}, '^[0-9]{{5,8}}')"
# 날짜 뗀 IP명 토큰
IP_NAMECORE_SQL = (
    f"TRIM(regexp_replace({_PREFIX_STRIPPED_SQL}, '^[0-9]{{5,8}}\\s*', ''))"
)
# 접두어만 뗀 전체(날짜+이름) — 세부검색 '타이틀' 그룹용
IP_TITLE_RAW_SQL = f"TRIM({_PREFIX_STRIPPED_SQL})"

_DATE_NAME_RE = re.compile(r"^([0-9]{5,8})\s*(.*)$")


@lru_cache(maxsize=1)
def load_alias_map():
    """ip_aliases.json → {별칭: 대표명} 평면 딕셔너리 (대표명→대표명 자기참조 포함)."""
    if not ALIAS_FILE.exists():
        return {}
    try:
        with open(ALIAS_FILE, encoding="utf-8") as f:
            raw = json.load(f)
    except Exception:
        return {}
    flat = {}
    for canonical, aliases in raw.items():
        if canonical.startswith("_"):
            continue
        flat[canonical] = canonical
        for a in aliases:
            flat[str(a)] = canonical
    return flat


def _canon_name(name):
    """IP명 토큰 → 대표명(별칭 통합)."""
    return load_alias_map().get(str(name).strip(), str(name).strip())


def apply_alias(series):
    """pandas Series(IP명 토큰) → 대표명으로 통합. 매핑 없으면 원본."""
    amap = load_alias_map()
    if not amap:
        return series.map(lambda x: str(x).strip())
    return series.map(lambda x: amap.get(str(x).strip(), str(x).strip()))


def make_title(date_code, ip_name):
    """날짜코드 + 대표 IP명 → 타이틀 문자열 ('260527 우주소녀'). 둘 중 빈 값 처리."""
    d = str(date_code).strip()
    n = str(ip_name).strip()
    if not n:
        return ""
    return f"{d} {n}".strip() if d else n


def normalize_title(title_raw):
    """'날짜 이름'(접두어 제거 상태) 문자열 → '날짜 대표IP명'. 이름 토큰만 별칭 통합."""
    s = str(title_raw).strip()
    if not s:
        return ""
    m = _DATE_NAME_RE.match(s)
    if m:
        date, name = m.group(1), m.group(2).strip()
        return make_title(date, _canon_name(name))
    return _canon_name(s)


def apply_alias_title(series):
    """pandas Series(날짜+이름) → 날짜 유지하며 이름만 별칭 통합한 타이틀."""
    return series.map(normalize_title)
