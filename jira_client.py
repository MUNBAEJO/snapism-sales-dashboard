"""
Jira CANDIP 프로젝트에서 IP별 R/S 율을 가져오는 클라이언트
"""
import json
import re
import urllib.request
import urllib.error
import base64
from pathlib import Path
from datetime import datetime

BASE_DIR = Path(__file__).parent
CONFIG_FILE = BASE_DIR / "config.json"
CACHE_FILE  = BASE_DIR / "data" / "jira_rs_cache.json"


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


def _search(cfg, jql, fields, max_results=100):
    """단일 페이지 검색 (내부 호환용)"""
    body = json.dumps({"jql": jql, "maxResults": max_results, "fields": fields}).encode()
    req = urllib.request.Request(
        f"{cfg['url']}/rest/api/3/search/jql",
        data=body, headers=_headers(cfg), method="POST"
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def _search_all(cfg, jql, fields, page_size=100):
    """nextPageToken 페이지네이션으로 전체 결과 수집"""
    all_issues = []
    next_token = None
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


def _extract_text(doc):
    """Atlassian Document Format → 평문 추출 (전체 텍스트 병합)"""
    if doc is None:
        return ""
    if isinstance(doc, str):
        return doc.strip()
    texts = []
    if isinstance(doc, dict):
        if doc.get("type") == "text":
            texts.append(doc.get("text", ""))
        for child in doc.get("content", []):
            texts.append(_extract_text(child))
    elif isinstance(doc, list):
        for item in doc:
            texts.append(_extract_text(item))
    return " ".join(t for t in texts if t).strip()


def _extract_wbs_text(doc) -> str:
    """WBS 필드 전용 추출 — ADF 첫 번째 단락 텍스트만 반환.
    단락이 여러 개일 때 중복 이어붙임(예: 'ONEWE ONEWE') 방지.
    """
    if doc is None:
        return ""
    if isinstance(doc, str):
        return doc.strip()

    def _inline_text(node) -> str:
        """단락 안의 인라인 텍스트 노드들을 이어붙임"""
        if isinstance(node, dict):
            if node.get("type") == "text":
                return node.get("text", "")
            return " ".join(_inline_text(c) for c in node.get("content", []))
        if isinstance(node, list):
            return " ".join(_inline_text(i) for i in node)
        return ""

    # ADF doc 최상위 처리
    if isinstance(doc, dict):
        content = doc.get("content", [])
        for child in content:
            text = _inline_text(child).strip()
            if text:
                return text   # 첫 번째 비어있지 않은 단락만 반환
    return ""


# WBS 앞 날짜/태그 제거 패턴: [KR]260601, [GLO]260601, 렌탈 260602, [Global]260608 등
_WBS_PREFIX = re.compile(
    r"(\[(?:KR|GLO|Global|글로벌|글)\][0-9]{6,8}\s*|렌탈\s*[0-9]{6,8}\s*)",
    re.IGNORECASE,
)
# 티켓 제목 앞 날짜 패턴: "26.06 ", "2026.06 "
_TITLE_DATE  = re.compile(r"^\s*(?:20)?\d{2}\.\d{2}\s+")
# 티켓 제목 뒤 일반 접미사 제거
_TITLE_SUFFIX = re.compile(
    r"\s*(?:아티스트|스티커|포토카드|특별관|픽구좌|기간미정|프레임|스내피즘[A-Z]?|스티커\s*프레임"
    r"|아티스트\s*프레임|콜라보|콜라보레이션|한정판|시즌\d+|\(.*?\))\s*$",
    re.IGNORECASE,
)


def _clean_wbs(wbs_raw: str) -> str:
    """WBS 문자열에서 IP 이름만 추출"""
    # 여러 구역([KR]/[GLO])이 있을 경우 첫 번째 구역의 IP만 사용
    cleaned = _WBS_PREFIX.sub("", wbs_raw).strip()
    # 두 번째 이후 구역 제거 (첫 번째 공백 이후 [KR] 등이 오는 경우)
    cleaned = re.split(r"\s*\[(?:KR|GLO|Global)\]", cleaned)[0].strip()
    return cleaned


def _clean_title(summary: str) -> str:
    """티켓 제목에서 IP 이름 추출"""
    s = _TITLE_DATE.sub("", summary)
    # 반복적으로 접미사 제거
    for _ in range(5):
        new = _TITLE_SUFFIX.sub("", s).strip()
        if new == s:
            break
        s = new
    return s.strip()


BRAND_FIELD = "customfield_10390"

# 스내피즘 활성 상태 목록
_SNAPISM_STATUSES = [
    "할 일", "진행 중", "송출 중", "완료",
    "TEST 맵핑", "검수 완료", "배포 완료", "In Review", "리소스 업로드 완료",
]
_STATUS_JQL = ", ".join(f'"{s}"' for s in _SNAPISM_STATUSES)


def fetch_rs_data(force_refresh: bool = False) -> dict:
    """
    CANDIP 프로젝트에서 Snapism 브랜드 '프로그램 및 검수' Sub-task를 가져와
    IP명 → {rs_agency, rs_mgmt, ticket_key, title, wbs, status} 매핑 반환.

    - 브랜드 필드(customfield_10390) = Snapism 또는 사용X(구Sticker)
    - issuetype = Sub-task, summary ~ "프로그램 및 검수"
    - 날짜 제한 없음 (전체 기간)
    - IP명은 부모 태스크 제목에서 추출
    - 캐시(data/jira_rs_cache.json) 1시간 재사용
    """
    # 캐시 확인
    if not force_refresh and CACHE_FILE.exists():
        try:
            with open(CACHE_FILE, encoding="utf-8") as f:
                cache = json.load(f)
            cached_at = cache.get("cached_at", "")
            if cached_at:
                age_hours = (datetime.now() - datetime.fromisoformat(cached_at)).total_seconds() / 3600
                if age_hours < 1:
                    return cache.get("data", {})
        except Exception:
            pass

    cfg = _load_cfg()
    wbs_f  = cfg["wbs_field"]
    rs_a_f = cfg["rs_agency_field"]
    rs_m_f = cfg["rs_mgmt_field"]
    proj   = cfg["project_key"]

    # Snapism 브랜드 + 프로그램 및 검수 Sub-task + 전체 기간
    jql = (
        f'project = {proj} AND issuetype = Sub-task '
        f'AND summary ~ "프로그램 및 검수" '
        f'AND "브랜드[select list (multiple choices)]" IN (Snapism, "사용 X (구 \'Sticker\')") '
        f'AND status IN ({_STATUS_JQL}) '
        f'ORDER BY created DESC'
    )
    try:
        issues = _search_all(
            cfg, jql,
            fields=[wbs_f, rs_a_f, rs_m_f, "summary", "parent", BRAND_FIELD, "status", "duedate"],
        )
    except Exception as e:
        raise RuntimeError(f"Jira 조회 실패: {e}")

    mapping = {}   # ip_name → entry
    for issue in issues:
        f = issue["fields"]

        # IP 이름: WBS 우선 → 없으면 부모 태스크 제목 파싱 → 없으면 서브태스크 제목 파싱
        parent         = f.get("parent") or {}
        parent_fields  = parent.get("fields") or {}
        parent_summary = parent_fields.get("summary", "")
        wbs_raw        = _extract_wbs_text(f.get(wbs_f))

        if wbs_raw:
            ip_name = _clean_wbs(wbs_raw)
        elif parent_summary:
            ip_name = _clean_title(parent_summary)
        else:
            ip_name = _clean_title(f.get("summary", ""))

        if not ip_name:
            continue

        rs_a   = f.get(rs_a_f)
        rs_m   = f.get(rs_m_f)
        status = (f.get("status") or {}).get("name", "")

        # 같은 IP에 여러 티켓이 있을 경우 가장 최신 것 (DESC 정렬) 사용
        if ip_name not in mapping:
            mapping[ip_name] = {
                "ip_name":    ip_name,
                "rs_agency":  float(rs_a) if rs_a is not None else None,
                "rs_mgmt":    float(rs_m) if rs_m is not None else None,
                "ticket_key": issue["key"],
                "title":      parent_summary or f.get("summary", ""),
                "wbs":        wbs_raw,
                "status":     status,
                "duedate":    f.get("duedate"),   # YYYY-MM-DD or None
            }

    # 캐시 저장
    CACHE_FILE.parent.mkdir(exist_ok=True)
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump({"cached_at": datetime.now().isoformat(), "data": mapping}, f, ensure_ascii=False, indent=2)

    return mapping


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    data = fetch_rs_data(force_refresh=True)
    print(f"IP 수: {len(data)}\n")
    for ip, entry in sorted(data.items()):
        print(
            f"  {ip!r:30} | 소속사RS={entry['rs_agency']} | 대행사RS={entry['rs_mgmt']}"
            f" | {entry['ticket_key']} | 상태={entry['status']}"
        )
