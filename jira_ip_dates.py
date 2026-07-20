"""
Jira CANDIP에서 IP별 오픈일(시작 날짜)·종료일(duedate) 수집
WBS 필드의 타이틀명을 키로 {startdate, duedate, ticket_key, brand, status} 매핑
"""
import json
import re
import base64
import urllib.request
from pathlib import Path
from datetime import datetime

BASE_DIR    = Path(__file__).parent
CONFIG_FILE = BASE_DIR / "config.json"
CACHE_FILE  = BASE_DIR / "data" / "jira_ip_dates_cache.json"

_STATUSES = [
    "할 일", "진행 중", "송출 중", "완료",
    "TEST 맵핑", "검수 완료", "배포 완료", "In Review", "리소스 업로드 완료",
]
_STATUS_JQL = ", ".join(f'"{s}"' for s in _STATUSES)

# Jira '시작 날짜' 커스텀 필드. (Target start=10022, Actual start=10008 은 미사용)
_STARTDATE_FIELD = "customfield_10015"

# [KR], [GLO], [Global], [JP], [CN] 등 지역 태그 제거
_TAG_RE   = re.compile(r'^\s*\[[A-Za-z가-힣]{2,10}\]\s*')
# WBS 항목 구분: 2개 이상 공백
_SEP_RE   = re.compile(r'\s{2,}')


def _parse_wbs_titles(wbs_text: str) -> list:
    """
    WBS 텍스트 → 타이틀명 목록
    예) "[KR]260601 트리플에스  [GLO]260601 TripleS"
        → ["260601 트리플에스", "260601 TripleS"]
    """
    if not wbs_text:
        return []
    items = _SEP_RE.split(wbs_text.strip())
    result = []
    for item in items:
        item = item.strip()
        if not item:
            continue
        cleaned = _TAG_RE.sub("", item).strip()
        if cleaned:
            result.append(cleaned)
    return result


def _extract_wbs_text(field_val) -> str:
    """ADF 또는 문자열 WBS → 평문 (첫 단락만)"""
    if not field_val:
        return ""
    if isinstance(field_val, str):
        return field_val.strip()

    def _inline(node) -> str:
        if isinstance(node, dict):
            if node.get("type") == "text":
                return node.get("text", "")
            return " ".join(_inline(c) for c in node.get("content", []))
        if isinstance(node, list):
            return " ".join(_inline(i) for i in node)
        return ""

    if isinstance(field_val, dict):
        for block in field_val.get("content", []):
            text = _inline(block).strip()
            if text:
                return text
    return ""


def _load_cfg():
    with open(CONFIG_FILE, encoding="utf-8") as f:
        return json.load(f)["jira"]


def _headers(cfg):
    cred = base64.b64encode(f"{cfg['email']}:{cfg['api_token']}".encode()).decode()
    return {
        "Authorization": f"Basic {cred}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def _search_all(cfg, jql, fields, page_size=100):
    all_issues, next_token = [], None
    url = f"{cfg['url']}/rest/api/3/search/jql"
    headers = _headers(cfg)
    while True:
        payload = {"jql": jql, "maxResults": page_size, "fields": fields}
        if next_token:
            payload["nextPageToken"] = next_token
        body = json.dumps(payload).encode()
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=30) as r:
            res = json.loads(r.read())
        batch = res.get("issues", [])
        all_issues.extend(batch)
        next_token = res.get("nextPageToken")
        if not next_token or not batch:
            break
    return all_issues


def fetch_ip_dates(brand: str = "all", force_refresh: bool = False) -> dict:
    """
    타이틀명(WBS 기반) → {title, duedate, ticket_key, brand, status, ip_name} 매핑 반환
    brand: "snapism" | "photoism" | "all"
    캐시 1시간 재사용
    """
    # v2: startdate(시작 날짜) 추가 — 옛 캐시는 이 키를 안 가지므로 자동 재조회됨
    cache_key = f"ip_dates_v2_{brand}"
    if not force_refresh and CACHE_FILE.exists():
        try:
            with open(CACHE_FILE, encoding="utf-8") as f:
                cache = json.load(f)
            if cache_key in cache:
                age_h = (datetime.now() - datetime.fromisoformat(
                    cache[cache_key]["cached_at"])).total_seconds() / 3600
                if age_h < 1:
                    return cache[cache_key]["data"]
        except Exception:
            pass

    cfg = _load_cfg()

    if brand == "snapism":
        brand_jql = '"브랜드[select list (multiple choices)]" IN (Snapism, "사용 X (구 \'Sticker\')")'
    elif brand == "photoism":
        brand_jql = '"브랜드[select list (multiple choices)]" IN (Photoism, "Photoism Colored")'
    else:
        brand_jql = (
            '"브랜드[select list (multiple choices)]" IN '
            '(Snapism, "사용 X (구 \'Sticker\')", Photoism, "Photoism Colored")'
        )

    jql = (
        f'project = {cfg["project_key"]} AND issuetype = Sub-task '
        f'AND summary ~ "프로그램 및 검수" '
        f'AND {brand_jql} '
        f'AND status IN ({_STATUS_JQL}) '
        f'ORDER BY duedate DESC'
    )

    try:
        issues = _search_all(cfg, jql, fields=[
            cfg["wbs_field"], "summary", "parent", "duedate",
            "customfield_10390", "status", _STARTDATE_FIELD,
        ])
    except Exception as e:
        raise RuntimeError(f"Jira 조회 실패: {e}")

    mapping = {}  # title_name → entry

    for issue in issues:
        f = issue["fields"]
        parent_title = (f.get("parent") or {}).get("fields", {}).get("summary", "")
        wbs_raw      = _extract_wbs_text(f.get(cfg["wbs_field"]))
        duedate      = f.get("duedate")
        startdate    = f.get(_STARTDATE_FIELD)   # 오픈(시작) 예정일 — 88% 정도만 채워져 있음
        status       = (f.get("status") or {}).get("name", "")

        brand_val  = f.get("customfield_10390") or []
        brand_str  = ", ".join(b.get("value", "") for b in brand_val) if isinstance(brand_val, list) else ""

        entry_base = {
            "startdate":  startdate,
            "duedate":    duedate,
            "ticket_key": issue["key"],
            "parent":     parent_title,
            "brand":      brand_str,
            "status":     status,
        }

        if wbs_raw:
            # WBS에서 타이틀명 목록 추출 → 각각 개별 키로 저장
            titles = _parse_wbs_titles(wbs_raw)
            for title in titles:
                if title and title not in mapping:
                    mapping[title] = {**entry_base, "title": title, "wbs_raw": wbs_raw}
        else:
            # WBS 없으면 부모 제목을 키로 (fallback)
            if parent_title and parent_title not in mapping:
                mapping[parent_title] = {**entry_base, "title": parent_title, "wbs_raw": ""}

    # 캐시 저장
    CACHE_FILE.parent.mkdir(exist_ok=True)
    try:
        with open(CACHE_FILE, encoding="utf-8") as f:
            cache = json.load(f)
    except Exception:
        cache = {}
    cache[cache_key] = {"cached_at": datetime.now().isoformat(), "data": mapping}
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)

    return mapping


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    data = fetch_ip_dates(brand="all", force_refresh=True)
    with_date = {k: v for k, v in data.items() if v["duedate"]}
    print(f"전체: {len(data)}개 | 기한 있음: {len(with_date)}개")
    print("\n--- 포토이즘 샘플 ---")
    photo = {k: v for k, v in with_date.items() if "Photoism" in v.get("brand", "")}
    for k, v in list(photo.items())[:15]:
        print(f"  {k!r:40} → 기한={v['duedate']} | {v['ticket_key']}")
