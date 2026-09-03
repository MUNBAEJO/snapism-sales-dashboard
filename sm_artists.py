# -*- coding: utf-8 -*-
"""SM 아티스트 정의와 **테마 → 아티스트 매칭 규칙의 단일 출처**.

왜 따로 뺐나
  이 규칙이 두 곳에서 필요해졌다 —
    · `sm_report.py`  : 촬영수 엑셀을 아티스트별 탭으로 나눌 때
    · `theme_pick.py` : 정산서에서 **아티스트 단위로 프레임을 고를** 때
  그런데 `sm_report` 는 openpyxl 을 끌고 와서 정산 화면에 붙이기엔 무겁고,
  규칙을 복사하면 나중에 갈린다(쿠폰 국가 목록에서 이미 겪은 병).
  그래서 규칙만 여기 한 벌 두고 양쪽이 이걸 부른다. 무거운 import 는 없다.

왜 이 매칭이 필요한가 — **한 타이틀에 여러 아티스트가 섞이고, 그 안에서 또
한/영 테마가 쌍으로 갈린다.** 실측(`260804 SM ent`, 2026-08):
    RIIZE       `260624_RIIZE` 3,297만  +  `260624_라이즈` 1,995만
    Red Velvet  `260804_Red Velvet` 1,709만  +  `260804_레드벨벳` 1,421만
손으로 고르면 한글 쌍을 놓치기 쉽고, 그러면 **30% 넘게 덜 정산된다.**
`kws` 에 한/영을 같이 적어 두면 한 번에 잡힌다(`riize` · `라이즈`).
"""
from __future__ import annotations

import json
from pathlib import Path

BASE_DIR = Path(__file__).parent
ARTISTS_FILE = BASE_DIR / "sm_artists.json"


def load(fallback=None) -> list:
    """`sm_artists.json` 을 읽어 아티스트 목록을 만든다(코드 수정 없이 IP 추가).

    파일이 없거나 형식이 깨지면 `fallback` 으로 안전하게 떨어진다 —
    정의를 못 읽었다고 리포트·정산이 통째로 멈추면 안 된다.
    """
    if ARTISTS_FILE.exists():
        try:
            data = json.loads(ARTISTS_FILE.read_text(encoding="utf-8"))
            arts = data.get("artists", data) if isinstance(data, dict) else data
            ok = []
            for a in arts:
                if (isinstance(a, dict) and a.get("name") and a.get("kws")
                        and isinstance(a.get("members"), dict)):
                    a.setdefault("countries", None)
                    ok.append(a)
            if ok:
                return ok
        except Exception:                                    # noqa: BLE001
            pass
    return list(fallback or [])


def match_theme(theme: str, cc: str | None = None, artists: list | None = None):
    """테마 이름 → 아티스트(dict) 또는 None.

    ★규칙은 **부분일치(소문자)** 다 — 테마에 날짜 접두어가 붙기 때문이다
      (`260624_RIIZE` 에 `riize` 가 들어 있다).
    ★`countries` 가 있는 아티스트는 그 나라에서만 인정한다. `cc` 를 안 주면
      나라 조건은 **건너뛴다**(정산은 전 국가를 합치므로 나라로 거르면 안 된다).
    """
    tl = str(theme or "").lower()
    for a in (artists if artists is not None else load()):
        if any(str(k).lower() in tl for k in a.get("kws", [])):
            if cc is not None and a.get("countries") and cc not in a["countries"]:
                return None
            return a
    return None


def group_themes(themes, artists: list | None = None) -> dict:
    """테마 목록 → `{아티스트 이름: [테마…]}`. 어디에도 안 걸린 건 넣지 않는다.

    정산 화면이 '아티스트 한 줄' 을 그릴 때 쓴다 — 한 줄을 켜면 그 아티스트의
    한/영 테마가 **같이** 켜진다.
    """
    arts = artists if artists is not None else load()
    out: dict[str, list] = {}
    for t in themes:
        a = match_theme(t, None, arts)
        if a:
            out.setdefault(a["name"], []).append(t)
    return {k: sorted(v) for k, v in sorted(out.items())}
