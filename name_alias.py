# -*- coding: utf-8 -*-
"""테마·프레임 이름의 **한/영 통합**과 **글자깨짐 교정**.

같은 멤버가 화면에서 셋으로 갈려 있었다 — `리쿠(RIKU)` · `리쿠` · `RIKU`.
합치면 8.9억이 한 줄인데, 갈려 있으니 1위가 아니라 3위에 흩어져 보였다.

두 가지를 한다.

① **글자깨짐** — `SUNOO` 와 `SUNOО` 는 마지막 글자가 러시아 글자다. 눈으로는 같다.
   키릴·그리스 글자 중 **라틴과 모양이 같은 것만** 라틴으로 되돌린다. 규칙이라
   표가 필요 없다. (`Ω` `β` 처럼 일부러 쓴 글자는 목록에 없어 안 건드린다.)

② **한/영 통합** — 데이터 안에 `리쿠(RIKU)` 라는 표기가 **실제로 있을 때만** 짝으로 본다.
   ★로마자로 짐작해서 잇지 않는다. 틀린 짝은 조용히 남의 매출을 옮긴다.
     `JAEHYUN` 은 SM ent 에선 재현, 보넥도에선 명재현이다 — 그래서 **IP 안에서만** 짝짓는다.
   짝표는 build_name_alias.py 가 데이터에서 뽑아 data/name_alias.json 에 적어 둔다.
"""
from __future__ import annotations

import json
import unicodedata
from functools import lru_cache
from pathlib import Path

BASE_DIR = Path(__file__).parent

# 라틴과 모양이 같은 키릴·그리스 글자 → 라틴. 모양이 다른 건 넣지 않는다.
_HOMOGLYPH = {
    "А": "A", "В": "B", "Е": "E", "К": "K", "М": "M", "Н": "H", "О": "O",
    "Р": "P", "С": "C", "Т": "T", "Х": "X", "Ѕ": "S", "Ј": "J", "І": "I",
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "х": "x", "у": "y", "і": "i",
    # ★대문자 У(U+0423)가 빠져 있었다 — 소문자 у 만 있었다. 그리고 Ү(U+04AE,
    #   STRAIGHT U)는 아예 다른 글자라 따로 넣어야 한다: 실데이터에
    #   `ZANMANG LOOРҮ В`(381,749원) 하나가 이것 때문에 깨진 채 남아 있었다
    #   — 81개 키릴 오염 이름 중 fold 로 안 고쳐지던 **유일한 건**이다(2026-08-20).
    "У": "Y", "Ү": "Y",
    "Α": "A", "Β": "B", "Ε": "E", "Η": "H", "Ι": "I", "Κ": "K", "Μ": "M",
    "Ν": "N", "Ο": "O", "Ρ": "P", "Τ": "T", "Υ": "Y", "Χ": "X",
}


def fold(s) -> str:
    """글자깨짐 교정 + 공백 정리. 이름 하나를 넣으면 '바른 표기' 가 나온다."""
    t = unicodedata.normalize("NFKC", str(s)).strip()
    t = "".join(_HOMOGLYPH.get(c, c) for c in t)
    return " ".join(t.split())


def _path() -> Path:
    return BASE_DIR / "name_alias.json"   # ip_aliases.json 옆 — data/ 는 gitignore 라 안 따라간다


@lru_cache(maxsize=1)
def _table(mtime: float) -> dict:
    """짝표를 읽는다. ★**파일이 있는데 못 읽으면 예외를 던진다** (2026-08-20).

    전엔 무조건 `{}` 였다. 그러면 이름 통합이 통째로 꺼지는데 합계는 그대로라
    아무 데도 티가 안 난다 — 같은 테마·프레임이 표기별로 쪼개져 순위만 조용히
    뒤바뀐다("왜 이게 안 보이지" 로만 드러난다). 이 파일은 git 에 들어 있어서
    깨질 일이 거의 없고, 깨졌다면 손으로 잘못 고친 것이라 바로 알아야 한다.
    파일이 아예 없는 것(첫 설치·별칭 미사용)은 정상이라 `{}` 그대로 둔다.
    """
    p = _path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        raise RuntimeError(f"{p.name} 을 읽지 못했어요 — 이름 통합이 통째로 "
                           f"꺼지면 순위가 조용히 틀어집니다. ({e})") from e


def load() -> dict:
    try:
        m = _path().stat().st_mtime
    except OSError:
        m = 0.0
    return _table(m)


def mapping(brand: str, axis: str, ip: str = "*") -> dict:
    """(브랜드, 축, IP) 의 짝표. 없으면 빈 dict."""
    t = load().get(brand, {}).get(axis, {})
    return t.get(str(ip)) or t.get("*") or {}


def apply(df, axis_col: str, brand: str, ip_col: str | None = None,
          axis: str | None = None):
    """df 의 `axis_col` 을 통합 이름으로 바꿔 **새 df** 를 돌려준다(원본 안 건드림).

    ip_col 을 주면 줄마다 그 IP 의 짝표를 쓴다(같은 영문이 IP마다 딴 사람인 경우).
    ★합계를 다시 내는 건 부르는 쪽 몫이다 — 이름이 합쳐지면 줄이 겹친다.
    """
    if df is None or len(df) == 0 or axis_col not in df.columns:
        return df
    axis = axis or axis_col
    out = df.copy()
    names = out[axis_col].astype(str).map(fold)          # ① 글자깨짐
    if ip_col and ip_col in out.columns:                 # ② IP 안에서만 짝짓기
        ips = out[ip_col].astype(str)
        tbl = {ip: mapping(brand, axis, ip) for ip in ips.unique()}
        out[axis_col] = [tbl.get(i, {}).get(n, n) for i, n in zip(ips, names)]
    else:
        m = mapping(brand, axis)
        out[axis_col] = [m.get(n, n) for n in names]
    return out
