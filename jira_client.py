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
    body = json.dumps({"jql": jql, "maxResults": max_results, "fields": fields}).encode()
    req = urllib.request.Request(
        f"{cfg['url']}/rest/api/3/search/jql",
        data=body, headers=_headers(cfg), method="POST"
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def _extract_text(doc):
    """Atlassian Document Format → 평문 추출"""
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


def fetch_rs_data(force_refresh: bool = False) -> dict:
    """
    CANDIP 프로젝트에서 RS 율이 있는 작업 티켓을 가져와
    IP명 → {rs_agency, rs_mgmt, ticket_key, title, wbs} 매핑 반환.

    캐시(data/jira_rs_cache.json)가 있으면 재사용 (force_refresh=True면 강제 갱신).
    """
    # 캐시 확인
    if not force_refresh and CACHE_FILE.exists():
        try:
            with open(CACHE_FILE, encoding="utf-8") as f:
                cache = json.load(f)
            cached_at = cache.get("cached_at", "")
            if cached_at:
                age_hours = (datetime.now() - datetime.fromisoformat(cached_at)).total_seconds() / 3600
                if age_hours < 1:   # 1시간 이내면 캐시 사용
                    return cache.get("data", {})
        except Exception:
            pass

    cfg = _load_cfg()
    wbs_f = cfg["wbs_field"]
    rs_a_f = cfg["rs_agency_field"]
    rs_m_f = cfg["rs_mgmt_field"]
    proj   = cfg["project_key"]

    # 작업(Task) 타입이면서 RS 있는 티켓 (최대 200건)
    jql = (
        f'project = {proj} AND issuetype = Task '
        f'AND (cf[11058] is not EMPTY OR cf[11059] is not EMPTY) '
        f'ORDER BY created DESC'
    )
    try:
        res = _search(cfg, jql, fields=[wbs_f, rs_a_f, rs_m_f, "summary"], max_results=200)
    except Exception as e:
        raise RuntimeError(f"Jira 조회 실패: {e}")

    mapping = {}   # ip_name → entry
    for issue in res.get("issues", []):
        f = issue["fields"]
        rs_a = f.get(rs_a_f)
        rs_m = f.get(rs_m_f)
        wbs_raw = _extract_text(f.get(wbs_f))
        summary = f.get("summary", "")

        # IP 이름: WBS 우선, 없으면 제목에서 추출
        if wbs_raw:
            ip_name = _clean_wbs(wbs_raw)
        else:
            ip_name = _clean_title(summary)

        if not ip_name:
            continue

        # 같은 IP에 여러 티켓이 있을 경우 가장 최신 것 (이미 DESC 정렬)을 사용
        if ip_name not in mapping:
            mapping[ip_name] = {
                "ip_name":    ip_name,
                "rs_agency":  float(rs_a) if rs_a is not None else None,
                "rs_mgmt":    float(rs_m) if rs_m is not None else None,
                "ticket_key": issue["key"],
                "title":      summary,
                "wbs":        wbs_raw,
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
        print(f"  {ip!r:30} | 소속사RS={entry['rs_agency']} | 대행사RS={entry['rs_mgmt']} | {entry['ticket_key']}")
