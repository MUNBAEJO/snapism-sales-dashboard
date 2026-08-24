# -*- coding: utf-8 -*-
"""IP 오픈 일정 — Jira CANDIP 의 '시작 날짜'를 캘린더용 표로 정리한다.

수집은 jira_ip_dates.fetch_ip_schedule() 이 한다(12시간 캐시, 티켓 1건당 1개).
여기서는 **화면에 뿌릴 수 있는 모양으로 다듬기만** 한다.

다듬는 게 왜 필요한지 — Jira 쪽 이름이 세 군데에 흩어져 있어서다.
  · 서브태스크는 summary 가 '4. 프로그램 및 검수' 라 쓸모없고, 상품명은 부모에 있다.
  · 작업(Task)은 반대다. 부모가 '1차 계약_투어스'(계약 이름)이고
    상품명은 summary('24.12 TWS VER2 2차 아티스트 프레임')에 있다.
  · WBS 가 채워져 있으면 그게 제일 짧고 정확하다('260803 우주소녀').
    다만 한·영·일 여러 개가 들어 있어 하나를 골라야 한다.
"""
import re
from datetime import date, timedelta

import pandas as pd

import jira_ip_dates

# 달력 칩 색.
BRAND_COLOR = {
    "포토이즘": "#2563eb",
    "스내피즘": "#7c3aed",
    "기타":     "#94a3b8",
}
BRAND_ORDER = ["포토이즘", "스내피즘", "기타"]

# 이름 앞 날짜 접두어: '260803 ', '26.07 ', '20260803 '
_PREFIX_RE = re.compile(r"^\s*(?:\d{8}|\d{6}|\d{2}\.\d{2})\s+")
_HANGUL_RE = re.compile(r"[가-힣]")
# 서브태스크 summary 는 공정 이름이라 상품명이 아니다. ('4. 프로그램 및 검수')
_STEP_RE = re.compile(r"^\s*\d*\.?\s*프로그램\s*및\s*검수")


def _strip_prefix(s: str) -> str:
    """'260803 우주소녀' → '우주소녀'. 떼고 나서 비면 원본을 그대로 둔다."""
    out = _PREFIX_RE.sub("", str(s or "")).strip()
    return out or str(s or "").strip()


def _pick_title(titles: list) -> str:
    """WBS 에 한·영·일 이름이 여러 개 들어있을 때 하나를 고른다.

    한글이 든 걸 먼저 본다 — 부서에서 쓰는 이름이 한글이라서다.
    같으면 짧은 쪽. ('260608 ENCHIN (エンチン) A' 보다 '260608 ENCHIN(엔친)')
    """
    names = [_strip_prefix(t) for t in titles if str(t).strip()]
    if not names:
        return ""
    return sorted(names, key=lambda n: (0 if _HANGUL_RE.search(n) else 1, len(n)))[0]


def _norm(s) -> str:
    """이름 대조용 — 공백·괄호·구분기호를 걷어낸다."""
    return _NORM_RE.sub("", str(s)).lower()


def _product_of(item: dict) -> str:
    """티켓이 가리키는 **상품** 이름. 서브태스크면 부모(상품), 작업이면 summary.

    WBS 는 일부러 안 본다 — WBS 는 기획전 이름일 때가 있어 상품을 못 가른다.
    """
    summary = str(item.get("summary") or "")
    parent = str(item.get("parent") or "")
    # summary 가 공정 이름이면 상품명이 아니다 → 부모(상품)로 간다.
    if _STEP_RE.match(summary) or not summary:
        return _strip_prefix(parent) or _strip_prefix(summary)
    return _strip_prefix(summary)


def _shared_wbs(items) -> set:
    """서로 다른 상품이 둘 이상 매달린 WBS(정규화). 이런 WBS 는 **기획전 이름**이다.

    ★★2026-08-21 — 달력의 '오픈 N건' 이 실제 상품 수와 달랐던 원인이다.
      `260801 반팔 입고 나와` 하나에 크래비티·에잇턴·비투비·템페스트… 15개 상품이,
      `DIVE IN PHOTOISM` 에 16개가 매달려 있다. WBS 를 표시 이름으로 쓰면 이것들이
      **한 줄로 뭉쳐** 상품이 화면에서 사라진다.
      실측: 오픈일 있는 티켓 6,511장 · WBS 938개 중 **193개가 상품 2개 이상**.
    """
    by: dict = {}
    for it in items or []:
        w = _pick_title(it.get("wbs_titles") or [])
        if not w:
            continue
        by.setdefault(_norm(w), set()).add(_norm(_product_of(it)))
    return {k for k, v in by.items() if len(v) > 1}


def _name_of(item: dict, shared_wbs: set | None = None) -> str:
    """티켓 하나의 표시 이름. WBS → (서브태스크면 부모 / 작업이면 summary) 순.

    ★단, **기획전 이름인 WBS 는 쓰지 않는다**(`_shared_wbs`). 그걸 쓰면 서로 다른
      상품이 같은 이름이 되어 화면에서 한 줄로 보인다.
    """
    wbs = _pick_title(item.get("wbs_titles") or [])
    if wbs and _norm(wbs) not in (shared_wbs or set()):
        return wbs
    return _product_of(item) or wbs


def _brand_of(raw: str) -> str:
    """브랜드 필드는 다중 선택이라 'Photoism, Snapism' 처럼 여러 개가 온다.

    ★'공통' 칸은 두지 않는다(2026-08-04 사용자 지정). 두 브랜드가 같이 걸린 티켓은
      3,327건 중 8건뿐인데, 그것 때문에 범례에 색이 하나 더 늘면 읽기만 나빠진다.
      8건 **전부 Photoism 이 들어 있어** 포토이즘으로 본다.
    ★'사용 X (구 Sticker)' 는 폐기된 값이라 **마지막 단서로만** 쓴다.
      · 브랜드 판정에 같이 쓰면 포토이즘 단독 상품이 '공통'으로 잡힌다
        (CANDIP-20397 변우석 · 22421 LE SSERAFIM).
      · 그렇다고 아예 무시하면 이 값만 달린 스내피즘 스티커 상품 4건이
        '기타'로 빠진다(비아이 스내피즘 · 쇼미더머니12 스티커프레임 등).
      그래서 Photoism/Snapism 이 **둘 다 없을 때만** 스내피즘으로 본다.
    """
    s = str(raw or "")
    if "Photoism" in s:
        return "포토이즘"
    if "Snapism" in s or "Sticker" in s:
        return "스내피즘"
    return "기타"


def _country_names() -> dict:
    """국가코드(대문자) → 한글 이름. config 의 포토이즘 국가 목록을 그대로 쓴다.
    읽기만 하고 자격증명은 건드리지 않는다. 없으면 코드를 그대로 보여준다."""
    try:
        import json
        from pathlib import Path
        cfg = json.loads((Path(__file__).parent / "config.json").read_text(encoding="utf-8"))
        return {str(k).upper(): (v.get("name") or str(k).upper())
                for k, v in (cfg.get("photoism", {}).get("countries") or {}).items()}
    except Exception:
        return {}


CC_NAME = _country_names()
# config 의 포토이즘 국가 목록에 없는 나라. Jira Country 선택지가 더 넓다.
CC_NAME.setdefault("AR", "아르헨티나")
CC_NAME.setdefault("CO", "콜롬비아")
# 이만큼 고르면 나라 이름을 다 쓰지 않고 한 덩어리로 줄인다(사용자 확인).
ALL_COUNTRY_N = 28
# ★★그런데 '줄인다' 와 '전 국가라고 부른다' 는 다른 얘기다 (2026-08-24, 전수검사 #15).
#   실제로 쓰이는 Jira Country 코드는 **32종**인데 문턱이 28 이라, 3~4개국이 빠졌는데도
#   '전 국가' 로 찍혔다. 그중 **한국이 빠진 게 27건** — 제목에 '한국제외' 라고 적힌
#   상품까지 '전 국가' 로 보여서, 어디에 여는지 보러 온 사람이 정반대로 읽었다.
#   (실측 코드 32종: AE AR AU BN CA CL CN CO DE ES FR GB GU HK ID JP KR LA LU LV
#    MN MO MX MY NL PE PH SG TH TW US VN — Jira 에 나라가 늘면 이 값도 올려야 한다)
ALL_COUNTRY_UNIVERSE = 32


def cc_name(code: str) -> str:
    """국가코드 → 한글 이름. 모르면 코드를 그대로."""
    return CC_NAME.get(str(code).upper(), str(code))


# ── 권역 ──────────────────────────────────────────────────────────────────
# **나열 순서를 잡는 데만 쓴다.** 나라 이름 20개가 아무 순서 없이 늘어서 있으면
# 눈이 못 따라가는데, 권역별로 뭉쳐 놓으면 '아시아 덩어리 | 유럽 덩어리 | 미주 덩어리'
# 로 보여서 훑기가 된다.
#
# ★한때 '아시아 8' 처럼 권역 이름으로 **줄여서** 보여줬는데(2026-08-04 1차),
#   그게 어느 나라인지 알 수가 없다는 지적을 받고 되돌렸다. 개수는 국가수 열이
#   이미 주고 있으니 국가 열은 이름을 줘야 한다. 요약 용도로 다시 쓰지 말 것.
REGION_ORDER = ["아시아", "중동", "유럽", "미주", "오세아니아"]
_REGION_MEMBERS = {
    "아시아": ["KR", "JP", "CN", "TW", "HK", "MO", "MN",
               "TH", "VN", "MY", "SG", "ID", "PH", "BN", "LA"],
    "중동": ["AE"],
    "유럽": ["GB", "FR", "DE", "ES", "NL", "LU", "LV"],
    "미주": ["US", "CA", "MX", "CL", "PE", "AR", "CO"],
    # 괌은 미국령이지만 지도상 오세아니아다. 대시보드 다른 화면과 같은 감각으로 둔다.
    "오세아니아": ["AU", "GU"],
}
REGION_OF = {c: r for r, cs in _REGION_MEMBERS.items() for c in cs}
# 권역 안에서도 이 순서로 보여준다(위 목록 순서 = 우리 매출 큰 순서).
_CC_RANK = {c: i for i, c in enumerate(sum(_REGION_MEMBERS.values(), []))}


def region_of(code: str) -> str:
    return REGION_OF.get(str(code).upper(), "기타")


def group_by_region(codes) -> list:
    """[(권역, [국가코드…])] — REGION_ORDER 순, 권역 안은 _CC_RANK 순.

    새 나라가 Jira 에 추가되면 '기타'로 떨어져 화면에는 나오되 눈에 띈다
    (조용히 사라지지 않게 하려고 일부러 버리지 않는다).
    """
    buckets: dict = {}
    for c in (codes or []):
        buckets.setdefault(region_of(c), []).append(str(c).upper())
    out = []
    for r in REGION_ORDER + ["기타"]:
        if buckets.get(r):
            out.append((r, sorted(buckets[r], key=lambda c: _CC_RANK.get(c, 999))))
    return out


def sort_countries(codes) -> list:
    """권역 순으로 정렬한 국가코드. 아시아 → 중동 → 유럽 → 미주 → 오세아니아.

    이름을 늘어놓을 때 순서가 없으면 눈이 못 따라간다. 권역별로 뭉쳐 놓으면
    '한국 일본 중국 대만 … | 영국 프랑스 독일 … | 미국 캐나다 …' 로 덩어리가 보인다.
    """
    return [c for _, cs in group_by_region(codes) for c in cs]


def country_label(codes, limit: int = 12) -> str:
    """표 한 칸에 넣을 국가 문구. **실제 나라 이름을 쓴다.**

    ★'아시아 8' 같은 권역 요약으로 줄여 봤더니(2026-08-04 1차) 그게 어느 나라인지
      알 수가 없다는 지적을 받았다. 규모만 알아서는 쓸모가 없다 — 국가수 열이
      이미 숫자를 주고 있으니 이 열은 **이름**을 줘야 한다.
    ★단, 28개국 이상은 이름을 다 쓰는 게 오히려 방해라 한 덩어리로 줄인다.
      ★줄이더라도 **'전 국가' 라고 부르는 건 진짜 전 국가일 때만**이다 —
        빠진 나라가 있는데 '전 국가' 라고 적으면 정반대로 읽힌다(#15 주석 참고).
    """
    codes = list(codes or [])
    if not codes:
        return ""
    if len(codes) >= ALL_COUNTRY_N:
        if len(codes) >= ALL_COUNTRY_UNIVERSE:
            return f"전 국가 {len(codes)}개국"
        if "KR" not in {str(c).strip().upper() for c in codes}:
            # 가장 많이 오해받는 갈래라 따로 적는다('한국제외' 기획전).
            return f"한국 제외 {len(codes)}개국"
        return f"{len(codes)}개국(일부 제외)"
    names = [cc_name(c) for c in sort_countries(codes)]
    head = " · ".join(names[:limit])
    return head if len(names) <= limit else f"{head} 외 {len(names) - limit}"


def _to_date(v):
    if not v:
        return None
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def load_openings(brand: str = "all", force_refresh: bool = False) -> pd.DataFrame:
    """오픈일이 있는 IP 를 티켓 단위로 1행씩. **실패해도 예외를 던지지 않는다.**

    ★사이드바 위젯이 모든 페이지에서 이걸 부른다. 여기서 예외가 새어 나가면
      Jira 가 잠깐 죽었을 때 대시보드 전체가 같이 멈춘다 — 빈 표를 주고 넘어간다.

    컬럼: 오픈일 · IP · 브랜드 · 상태 · 티켓 · 종료일 · 계약
    """
    cols = ["오픈일", "IP", "브랜드", "상태", "티켓", "종료일", "계약", "국가", "국가수"]
    try:
        items = jira_ip_dates.fetch_ip_schedule(brand=brand, force_refresh=force_refresh)
    except Exception:
        return pd.DataFrame(columns=cols)

    rows = []
    no_date = 0                      # 오픈일 미정 — 아래에서 화면에 알려 준다
    shared = _shared_wbs(items)
    for it in items or []:
        start = _to_date(it.get("startdate"))
        if start is None:            # 오픈일이 없으면 달력에 찍을 수 없다
            # ★찍을 수 없는 건 맞지만 **말없이 빠지면 안 된다** (2026-08-24,
            #   전수검사 #16). 실측 169장(할 일 107 · 진행 중 34 · 완료 27 ·
            #   송출 중 1)이 아무 안내 없이 사라졌다. 완료·송출 중인 것까지 있어서
            #   '이 달력이 전부' 라고 믿으면 안 되는데 그걸 알 방법이 없었다.
            if _name_of(it, shared):     # 공정 티켓 말고 상품으로 보이는 것만
                no_date += 1
            continue
        name = _name_of(it, shared)
        if not name:
            continue
        # 서브태스크는 부모가 상품, 작업은 부모가 계약이다. '계약' 칸에는 후자만 넣는다.
        parent = str(it.get("parent") or "")
        contract = parent if "계약" in parent else ""
        rows.append({
            "오픈일": start,
            "IP":    name,
            "브랜드": _brand_of(it.get("brand")),
            "상태":   it.get("status") or "",
            "티켓":   it.get("ticket_key") or "",
            "종료일": _to_date(it.get("duedate")),
            "계약":   contract,
            # 오픈 국가(Jira Country 필드). 6,312건 중 98.6% 가 채워져 있다.
            "국가":   list(it.get("countries") or []),
            "국가수": len(it.get("countries") or []),
            # ★묶는 기준은 **상품**이다(표시 이름이 아니라). 아래 주석 참고.
            "_상품": _norm(_product_of(it)) or _norm(name),
        })

    # ★`_상품` 은 묶기 전용 내부 열이다. columns 에서 빠지면 그대로 잘려 나가
    #   _merge_same_product 가 옛 방식(표시 이름)으로 되돌아간다 — 실제로 그랬다.
    df = pd.DataFrame(rows, columns=cols + ["_상품"])
    if df.empty:
        df.attrs["no_date"] = no_date
        return df
    out = _merge_same_product(df)
    # ★_merge_same_product 가 새 프레임을 돌려주므로 **그 위에** 다시 붙인다.
    out.attrs["no_date"] = no_date
    return out


# 공정 순서 — 같은 상품이 여러 줄일 때 '제일 진행된' 상태를 대표로 삼는다.
_STATUS_RANK = {
    "할 일": 0, "검토 중": 1, "진행 중": 2, "리소스 업로드 완료": 3,
    "TEST 맵핑": 4, "검수 완료": 5, "배포 완료": 6, "송출 중": 7, "완료": 8,
}
_NORM_RE = re.compile(r"[\s()\[\]·,_\-/]+")


def _merge_same_product(df: pd.DataFrame) -> pd.DataFrame:
    """같은 날 여는 같은 상품을 한 줄로 합친다.

    ★왜 필요한가: 상품 하나에 티켓이 보통 2개다. 상위 '작업'과 그 아래
      '프로그램 및 검수' 서브태스크가 JQL 에 둘 다 걸린다. 그대로 두면
      달력에 '십란'이 두 번 찍혀 오픈 건수가 배로 보인다. (6,149 → 3,325)

    묶는 기준은 (오픈일, 브랜드, **상품**) 이다. 티켓 사이에 부모-자식 관계를
    알려주는 id 가 안 넘어와서(부모는 제목만 온다) 이름으로 맞출 수밖에 없다.
    '십란 (10CM X SORAN)' 과 '십란(10CM X SORAN)' 처럼 띄어쓰기만 다른 경우가
    있어 공백·괄호·구분기호를 걷어내고 비교한다.

    ★★예전엔 **표시 이름**으로 묶었는데, 그 이름이 티켓마다 들쭉날쭉했다
      (WBS 가 있으면 WBS, 없으면 부모/summary). 그래서 양쪽으로 어긋났다 —
        · 같은 상품인데 한쪽만 WBS 가 있어 **두 줄로 갈리고**(110일 · 188줄)
        · 다른 상품인데 기획전 WBS 를 공유해 **한 줄로 뭉쳤다**(19일 · 39개 상품)
      상품 키(`_상품`)는 WBS 를 안 보므로 티켓마다 흔들리지 않는다(2026-08-21).
    """
    d = df.copy()
    d["_n"] = d["_상품"] if "_상품" in d.columns else d["IP"].map(_norm)
    d["_r"] = d["상태"].map(lambda s: _STATUS_RANK.get(str(s), -1))
    # 진행이 앞선 줄을 위로 → 그룹 첫 줄이 대표가 된다.
    d = d.sort_values("_r", ascending=False, kind="stable")
    d["티켓수"] = d.groupby(["오픈일", "브랜드", "_n"])["티켓"].transform("size")
    d = d.drop_duplicates(subset=["오픈일", "브랜드", "_n"], keep="first")
    d = d.drop(columns=["_n", "_r", "_상품"], errors="ignore")
    return d.sort_values(["오픈일", "브랜드", "IP"], kind="stable").reset_index(drop=True)


def in_range(df: pd.DataFrame, start: date, end: date) -> pd.DataFrame:
    """오픈일이 [start, end] 안에 드는 행만. 양 끝 포함."""
    if df.empty:
        return df
    return df[(df["오픈일"] >= start) & (df["오픈일"] <= end)]


def upcoming(df: pd.DataFrame, today: date, days: int = 14) -> pd.DataFrame:
    """오늘부터 days 일간(오늘 포함)."""
    return in_range(df, today, today + timedelta(days=days - 1))


def by_day(df: pd.DataFrame) -> dict:
    """{date: DataFrame} — 날짜별로 잘라 놓는다. 달력 칸 채울 때 쓴다."""
    if df.empty:
        return {}
    return {d: g for d, g in df.groupby("오픈일", sort=True)}


def week_bounds(today: date) -> tuple:
    """이번 주 월요일~일요일. 사이드바 '이번 주 N건' 기준."""
    mon = today - timedelta(days=today.weekday())
    return mon, mon + timedelta(days=6)


def month_bounds(y: int, m: int) -> tuple:
    """그 달 1일과 말일."""
    first = date(y, m, 1)
    last = date(y + (m == 12), (m % 12) + 1, 1) - timedelta(days=1)
    return first, last
