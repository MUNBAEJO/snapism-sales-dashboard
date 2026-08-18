# -*- coding: utf-8 -*-
"""테마·프레임 이름 짝표(data/name_alias.json) 만들기 — 데이터에서 근거를 뽑아.

같은 사람이 `리쿠(RIKU)` · `리쿠` · `RIKU` 로 갈려 있다. 합쳐야 순위가 맞는다.

★근거는 **데이터 안에 있는 결합 표기 하나뿐**이다.
  `리쿠(RIKU)` 라는 이름이 실제로 팔린 적이 있으면, 리쿠와 RIKU 는 같은 사람이다.
  로마자로 짐작해서 잇지 않는다 — 틀린 짝은 조용히 남의 매출을 옮긴다.

★짝은 **IP 안에서만** 맺는다. `JAEHYUN` 은 SM ent 에선 재현, 보넥도에선 명재현이다.
  전역 표로 만들었다가 이런 충돌을 8건 봤다.

★안 맺는 것 — 괄호 안이 발음이 아니라 **그룹 이름**인 경우.
  `에스쿱스X민규(SEVENTEEN)` 를 `SEVENTEEN` 과 합치면 세븐틴 전체 매출이
  조합 프레임 하나에 붙는다. 그래서 (ㄱ) 괄호 안 == IP 이름 이거나
  (ㄴ) 앞이 'X' 가 든 합작 이름이면 건너뛴다.

    python build_name_alias.py            # 만들고 저장
    python build_name_alias.py --dry      # 저장 안 하고 요약만
"""
from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

import pandas as pd

import ip_classify
import name_alias

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
OUT = BASE_DIR / "name_alias.json"      # 손으로 볼 표라 data/ 아닌 코드 옆에 둔다

HAN = re.compile(r"[가-힣]")
# 타이틀 → IP명. ip_classify 의 SQL(IP_NAMECORE_SQL)과 같은 순서로 벗긴다.
_PFX = re.compile(r"^(렌탈|PW|L7|L|P|B|SP)\s+")
_DATE = re.compile(r"^\d{5,8}\s*")


def ip_name_of(title) -> str:
    s = _DATE.sub("", _PFX.sub("", str(title).strip())).strip()
    return ip_classify._canon_name(s)


COMBO = re.compile(r"^(?P<a>[^()]+?)\s*\(\s*(?P<b>[^()]+?)\s*\)$")


def _key(s) -> str:
    return "".join(c for c in str(s).lower() if c.isalnum())


def _pair(name: str):
    """'리쿠(RIKU)' → ('리쿠', 'RIKU'). 한/영 짝일 때만."""
    m = COMBO.match(name)
    if not m:
        return None
    a, b = m.group("a").strip(), m.group("b").strip()
    if not a or not b:
        return None
    if HAN.search(a) and not HAN.search(b):
        return a, b
    if HAN.search(b) and not HAN.search(a):
        return b, a
    return None


def _build(names_by_ip: dict) -> tuple[dict, list, list]:
    """{IP: {이름: 매출}} → ({IP: {이름: 대표}}, 채택목록, 보류목록)"""
    table, took, skipped = {}, [], []
    for ip, rev in names_by_ip.items():
        cand = defaultdict(set)                 # 조각 이름 → 대표 후보들
        for n in rev:
            p = _pair(n)
            if not p:
                continue
            ko, en = p
            # ★'괄호 안 == IP 이름' 은 거르지 않는다. 실제로 걸린 셋(잇지(ITZY) ·
            #   NOWZ (나우즈) · xikers(싸이커스))이 전부 **합쳐야 맞는** 것이었다 —
            #   그룹 이름을 한글/영문 두 벌로 쓴 것뿐이다.
            #   정작 막아야 할 '에스쿱스X민규 (SEVENTEEN)' 은 아래 X 규칙이 잡는다.
            if re.search(r"[Xx×]", ko):
                skipped.append((ip, n, "합작 이름 같아요(X)"))
                continue
            parts = [x for x in (ko, en) if x in rev]
            if not parts:                        # 결합형만 있으면 합칠 게 없다
                continue
            for x in parts:
                cand[x].add(n)
        m = {}
        for x, reps in cand.items():
            if len(reps) > 1:
                # 띄어쓰기만 다른 건 같은 걸로 본다 — 차우민(CHA WOOMIN) / 차우민(CHAWOOMIN).
                if len({_key(r) for r in reps}) > 1:
                    skipped.append((ip, x, f"대표가 둘이에요: {' / '.join(sorted(reps))}"))
                    continue
                reps = {max(reps, key=lambda r: rev.get(r, 0))}   # 많이 팔린 표기로
            rep = next(iter(reps))
            m[x] = rep
            took.append((ip, x, rep, rev.get(x, 0)))
        if m:
            table[ip] = dict(sorted(m.items()))
    return table, took, skipped


def photoism() -> tuple[dict, list, list]:
    f = DATA_DIR / "theme_daily.parquet"
    if not f.exists():
        return {}, [], []
    d = pd.read_parquet(f, columns=["타이틀", "테마", "프레임", "최종결제금액"])
    d["_ip"] = d["타이틀"].map(ip_name_of)
    out, took, skipped = {}, [], []
    for axis in ("테마", "프레임"):
        d[axis] = d[axis].astype(str).map(name_alias.fold)
        g = d[~d[axis].isin(["", "None", "nan", "<NA>"])]
        agg = (g.groupby(["_ip", axis], observed=True)["최종결제금액"]
               .sum().reset_index())
        by = {ip: dict(zip(o[axis], o["최종결제금액"])) for ip, o in agg.groupby("_ip")}
        t, tk, sk = _build(by)
        out[axis] = t
        took += [(axis, *x) for x in tk]
        skipped += [(axis, *x) for x in sk]
    return out, took, skipped


def snapism() -> tuple[dict, list, list]:
    f = DATA_DIR / "master.parquet"
    if not f.exists():
        return {}, [], []
    d = pd.read_parquet(f, columns=["프레임 이름", "최종 결제 금액", "취소 여부"])
    d = d[d["취소 여부"] != True]                                    # noqa: E712
    d["프레임 이름"] = d["프레임 이름"].astype(str).map(name_alias.fold)
    d = d[~d["프레임 이름"].isin(["", "None", "nan", "<NA>"])]
    rev = d.groupby("프레임 이름")["최종 결제 금액"].sum().to_dict()
    # 스내피즘은 프레임이 곧 IP 이름이라 IP 축이 없다 → 전역 한 장('*')
    t, tk, sk = _build({"*": rev})
    return ({"프레임": t} if t else {},
            [("프레임", *x) for x in tk], [("프레임", *x) for x in sk])


def main() -> None:
    dry = "--dry" in sys.argv
    ph, ph_t, ph_s = photoism()
    sn, sn_t, sn_s = snapism()
    doc = {
        "_meta": {
            "만든날": str(date.today()),
            "만든법": "build_name_alias.py — 데이터에 실재하는 '한글(ENG)' 결합 표기만 근거로 씀",
            "주의": "손으로 고쳐도 되지만, 다시 돌리면 덮어써요. IP 안에서만 짝이 맞아요.",
            "짝수": {"포토이즘": len(ph_t), "스내피즘": len(sn_t)},
        },
        "포토이즘": ph,
        "스내피즘": sn,
    }
    print(f"포토이즘 짝 {len(ph_t):,}개 · 스내피즘 {len(sn_t):,}개")
    for axis in ("테마", "프레임"):
        n = sum(len(v) for v in ph.get(axis, {}).values())
        print(f"  포토이즘/{axis}: IP {len(ph.get(axis, {})):,}개 · 이름 {n:,}개")
    if ph_s or sn_s:
        print(f"\n보류 {len(ph_s) + len(sn_s)}건 (합치지 않음):")
        for row in (ph_s + sn_s)[:20]:
            print("   ", " · ".join(str(x) for x in row))
    if dry:
        print("\n--dry — 저장 안 함")
        return
    OUT.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n저장: {OUT}")


if __name__ == "__main__":
    main()
