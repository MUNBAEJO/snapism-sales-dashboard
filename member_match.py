# -*- coding: utf-8 -*-
"""멤버 한/영 **추천 짝** — 자동 저장이 아니라 사람이 승인할 후보만 만든다.

왜 여기서는 로마자로 '짐작' 하나
--------------------------------
`name_alias`·`build_name_alias` 는 **로마자로 짐작해서 잇지 않는다** — 틀린 짝이
조용히 남의 매출을 옮기기 때문이다. 그쪽은 데이터에 실재하는 `한글(ENG)` 결합
표기가 있을 때만 잇는다. 그런데 멤버 별칭은 상황이 정반대다: 원장에 `나연`(한국)과
`NAYEON`(해외)이 **각각 따로** 있고 결합표기가 아예 없다. 결합표기 규칙으로는 한
명도 못 잇는다.

여기서 그 원칙을 어기지 않는 길은 하나다 — **추천만 하고, 저장은 사람이 한다.**
이 모듈은 화면 드롭다운의 기본값으로 얹을 후보를 만들 뿐이고, `member_aliases.json`
에 쓰는 건 사람이 누르는 저장 버튼이다. 그래서 틀린 짝이 조용히 매출을 옮기는 일이
없다. 확실한 짝(로마자 **정확 일치**)만 `strong=True` 로 표시하고, 애매한 건
`strong=False` 로 내려 사람이 반드시 눈으로 확인하게 한다.

매칭
----
· 한글 → 개정식 로마자 근사(`build_name_alias._rom`) → 표기 흔들림 흡수(`_loose`)
· 영문 → 같은 `_loose`
· 끝의 구좌 표식(A/B)은 **양쪽 다 있으면 같아야** 한다(`산 A` ↔ `SAN A`).
· loose 가 정확히 같으면 `strong`, 아주 가까우면(difflib ≥ 0.72) 약한 후보, 아니면 없음.
· 정확 일치가 **양방향으로 유일**할 때만 `strong` 으로 둔다 — 둘 이상이 같은 loose
  로 겹치면(예: 정호·종호가 축약 후 같아짐) 자동 채우기가 위험하므로 확인으로 내린다.

검증(2026-08-24)
----------------
이미 손으로 맺어 둔 24쌍을 정답셋으로 돌려 봤다: 6쌍이 정확 일치(전부 맞음),
12쌍이 0.72↑ 근접(성한빈·마틴·건호·도영·최현석 …), 6쌍은 0.72 미만이라 못 잡는다
(제임스·트레저·석매튜처럼 한글이 영어 단어의 음차라 로마자로는 원천 불가 — 수동).
"""
from __future__ import annotations

import difflib

import build_name_alias as _bna   # _rom·_loose·_sfx·_base 한 곳(로마자 규칙 단일 출처)
import name_alias                 # 키릴·그리스 동형글자 되돌림(fold)

# 약한 후보로 띄울 하한. 이 아래는 아예 안 보여 준다(오히려 헷갈린다).
# 정답셋 24쌍에서 최현석(0.727)·박정우(0.778)까지 살리고, 제임스(0.545) 같은
# 음차는 버리는 경계다. 약한 후보는 저장이 안 되므로 하한이 낮아도 위험하지 않다.
_WEAK_MIN = 0.72


def _lkey(s) -> str:
    """비교용 loose 키 — 로마자로 옮긴 뒤 표기 흔들림을 흡수한다.

    영문이면 `_rom` 이 그냥 소문자 alnum 만 남기므로 한글·영문 모두 같은 함수로
    처리된다(둘을 같은 잣대로 재야 비교가 된다).
    """
    return _bna._loose(_bna._rom(s))


def suggest(korean_names, english_names) -> dict:
    """{한글이름: {"en": 추천영문, "score": 0~1, "strong": bool}}.

    후보가 없는 이름은 결과에서 빠진다. `strong=True` 는 로마자가 구좌 표식까지
    정확히 일치하고 **양방향으로 유일**한 짝 — 화면이 이것만 미리 채우고 '일괄
    저장' 에 넣는다. 나머지는 사람이 눈으로 확인한다.
    """
    # 영문 후보를 미리 (원본, base loose, 표식) 으로 준비 — 매 한글마다 다시 안 푼다.
    ens = []
    for e in sorted({str(x).strip() for x in english_names if str(x).strip()}):
        e2 = name_alias.fold(e)                 # 키릴·그리스 동형글자 → 라틴
        ens.append((e, _lkey(_bna._base(e2)), _bna._sfx(e2)))

    raw = {}                                     # ko -> (strong_exact, score, en, en_loose)
    for ko in korean_names:
        ks = _bna._sfx(ko)
        kk = _lkey(_bna._base(ko))
        if not kk:
            continue
        best = None                              # (exact, score, en, ek)
        for e, ek, es in ens:
            if (ks or es) and ks != es:          # 한쪽이라도 표식이 있으면 같아야 한다
                continue
            if not ek:
                continue
            if ek == kk:
                cand = (True, 1.0, e, ek)
            else:
                r = difflib.SequenceMatcher(None, kk, ek).ratio()
                if r < _WEAK_MIN:
                    continue
                cand = (False, r, e, ek)
            if best is None or cand[:2] > best[:2]:
                best = cand
        if best:
            raw[ko] = best

    # ── 정확 일치의 '유일성' 확인 — 겹치면 자동 채우기가 위험하다 ────────────
    # 같은 영문을 정확 일치로 가리키는 한글이 둘 이상이거나, 한 한글의 loose 가
    # 여러 영문과 정확히 겹치면(있을 수 있다) 사람이 골라야 한다 → strong 해제.
    exact_en = {}                                # en -> [ko …]  (정확 일치만)
    exact_kk = {}                                # ek -> 등장 횟수(정확 일치 영문 쪽)
    for ko, (ex, _s, en, ek) in raw.items():
        if ex:
            exact_en.setdefault(en, []).append(ko)
    for _e, ek, _es in ens:
        exact_kk[ek] = exact_kk.get(ek, 0) + 1

    out = {}
    for ko, (ex, score, en, ek) in raw.items():
        strong = bool(ex) and len(exact_en.get(en, [])) == 1 and exact_kk.get(ek, 0) == 1
        out[ko] = {"en": en, "score": round(float(score), 3), "strong": strong}
    return out
