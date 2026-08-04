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


def _name_of(item: dict) -> str:
    """티켓 하나의 표시 이름. WBS → (서브태스크면 부모 / 작업이면 summary) 순."""
    wbs = _pick_title(item.get("wbs_titles") or [])
    if wbs:
        return wbs
    summary = str(item.get("summary") or "")
    parent = str(item.get("parent") or "")
    # summary 가 공정 이름이면 상품명이 아니다 → 부모(상품)로 간다.
    if _STEP_RE.match(summary) or not summary:
        return _strip_prefix(parent) or _strip_prefix(summary)
    return _strip_prefix(summary)


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
# Jira Country 선택지가 30개국이라, 그만큼 다 고른 건 사실상 '전 국가'다.
ALL_COUNTRY_N = 28


def country_label(codes, limit: int = 6) -> str:
    """표에 넣을 짧은 국가 문구. '전 국가(30)' 또는 '한국 · 일본 · 베트남 외 3'."""
    codes = list(codes or [])
    if not codes:
        return ""
    if len(codes) >= ALL_COUNTRY_N:
        return f"전 국가({len(codes)})"
    names = [CC_NAME.get(c, c) for c in codes]
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
    for it in items or []:
        start = _to_date(it.get("startdate"))
        if start is None:            # 오픈일이 없으면 달력에 찍을 수 없다
            continue
        name = _name_of(it)
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
        })

    df = pd.DataFrame(rows, columns=cols)
    if df.empty:
        return df
    return _merge_same_product(df)


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

    묶는 기준은 (오픈일, 브랜드, 이름) 이다. 티켓 사이에 부모-자식 관계를
    알려주는 id 가 안 넘어와서(부모는 제목만 온다) 이름으로 맞출 수밖에 없다.
    '십란 (10CM X SORAN)' 과 '십란(10CM X SORAN)' 처럼 띄어쓰기만 다른 경우가
    있어 공백·괄호·구분기호를 걷어내고 비교한다.
    """
    d = df.copy()
    d["_n"] = d["IP"].map(lambda s: _NORM_RE.sub("", str(s)).lower())
    d["_r"] = d["상태"].map(lambda s: _STATUS_RANK.get(str(s), -1))
    # 진행이 앞선 줄을 위로 → 그룹 첫 줄이 대표가 된다.
    d = d.sort_values("_r", ascending=False, kind="stable")
    d["티켓수"] = d.groupby(["오픈일", "브랜드", "_n"])["티켓"].transform("size")
    d = d.drop_duplicates(subset=["오픈일", "브랜드", "_n"], keep="first")
    d = d.drop(columns=["_n", "_r"])
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
