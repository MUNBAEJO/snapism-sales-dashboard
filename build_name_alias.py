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

import difflib
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


# ── 로마자 근사(개정식) — ③ 추정 짝을 만들 때만 쓴다 ──────────────
# 사람 이름 표기가 제각각이라 정확할 필요는 없다. '닮았나' 만 재면 된다.
_CHO = ["g", "kk", "n", "d", "tt", "r", "m", "b", "pp", "s", "ss", "",
        "j", "jj", "ch", "k", "t", "p", "h"]
_JUNG = ["a", "ae", "ya", "yae", "eo", "e", "yeo", "ye", "o", "wa", "wae", "oe",
         "yo", "u", "wo", "we", "wi", "yu", "eu", "ui", "i"]
_JONG = ["", "k", "k", "k", "n", "n", "n", "t", "l", "l", "l", "l", "l", "l",
         "l", "l", "m", "p", "p", "t", "t", "ng", "t", "t", "k", "t", "p", "t"]
# 표기 흔들림 흡수: SOOBIN=수빈, WONHEE=원희 처럼 같은 소리를 달리 적는다.
_SUB = [("eo", "o"), ("eu", "u"), ("ae", "e"), ("oo", "u"), ("ee", "i"),
        ("ou", "u"), ("l", "r"), ("gg", "kk"), ("jj", "j"), ("ck", "k")]


def _rom(s) -> str:
    out = []
    for ch in str(s):
        o = ord(ch)
        if 0xAC00 <= o <= 0xD7A3:
            i = o - 0xAC00
            out.append(_CHO[i // 588] + _JUNG[(i % 588) // 28] + _JONG[i % 28])
        elif ch.isalnum():
            out.append(ch.lower())
    return "".join(out)


def _loose(t) -> str:
    t = _key(t)
    for a, b in _SUB:
        t = t.replace(a, b)
    return t


# 끝의 구좌 표식(A/B/C). ★앞에 공백이나 한글이 있을 때만 표식으로 본다 —
#   `\s*([A-Z])$` 로 뒀더니 'YEONJUN' 의 N 을 표식으로 읽어 '연준' 과 안 맺어졌다.
#   그 바람에 TXT·TWS·제로베이스원이 통째로 후보에서 빠져 있었다.
_SUFX = re.compile(r"(?<=[\s가-힣])([A-Za-z])\s*$")


def _sfx(s) -> str:
    m = _SUFX.search(str(s).strip())
    return m.group(1).upper() if m else ""


def _base(s) -> str:
    return _SUFX.sub("", str(s).strip()).strip()


def _key(s) -> str:
    return "".join(c for c in str(s).lower() if c.isalnum())


def _blocklist() -> set:
    """`_제외` — 손으로 "이 둘은 다른 사람이다" 라고 적어 둔 짝. 다시 돌려도 살아남는다.
    형식: [[IP, 이름1, 이름2, "왜"], …]"""
    try:
        cur = json.loads(OUT.read_text(encoding="utf-8"))
    except Exception:
        return set()
    out = set()
    for row in cur.get("_제외", []):
        if len(row) >= 3:
            out.add((str(row[0]), str(row[1]), str(row[2])))
    return out


def _json_list(key: str) -> list:
    try:
        return json.loads(OUT.read_text(encoding="utf-8")).get(key, [])
    except Exception:
        return []


def _skip_ips() -> list:
    """`_표기통합_제외IP` — 표기만 달라 보여도 손대지 않을 IP.
    T1 매장 프레임은 FAKER/Faker 가 넉 달을 나란히 팔려 디자인이 둘인지 알 수 없다."""
    return _json_list("_표기통합_제외IP")


def _write_csv(rows, name, cols) -> None:
    p = BASE_DIR / "reports"
    p.mkdir(exist_ok=True)
    pd.DataFrame(rows, columns=cols).to_csv(p / name, index=False, encoding="utf-8-sig")


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


def _guess(cc, dc, axis, ip, rev, settled, block):
    """③ 로마자 추정 짝. 근거가 약하므로 **데이터에 두 번 더 물어본다.**

    ㄱ) 두 표기가 **같은 나라**에 같이 있나? 나라별 표기 차이라면 있을 리 없다.
    ㄴ) 나라가 겹치면, **같은 날**에도 같이 있었나? 날짜가 갈리면 중간에 이름을 바꾼 것이다.
    둘 다 겹치면(C) 진짜 딴 것일 수 있어 **합치지 않고 넘긴다.**
    """
    ko = sorted([n for n in rev if HAN.search(n) and "(" not in n],
                key=lambda n: -rev[n])
    # ②로 임자가 정해진 영문은 뺀다 — 띄어쓰기만 다른 표기가 남아 엉뚱한 한글이
    # 주워 간다(여상(YEOSANG) 합친 뒤 남은 'YEO SANG' 을 우영이 물었다).
    en = [n for n in rev if not HAN.search(n) and _loose(_base(n)) not in settled]
    pairs, held, same, used = {}, [], [], set()
    for k in ko:
        ks, kr = _sfx(k), _loose(_rom(_base(k)))
        if len(kr) < 3:
            continue
        best = None
        for e in en:
            if e in used or _sfx(e) != ks:
                continue
            er = _loose(_base(e))
            if len(er) < 3:
                continue
            r = difflib.SequenceMatcher(None, kr, er).ratio()
            if best is None or r > best[0]:
                best = (r, e)
        if not best or best[0] < 0.68:
            continue
        e = best[1]
        if (ip, k, e) in block or (ip, e, k) in block:
            held.append((axis, ip, f"{k} + {e}", "손으로 뺀 짝(_제외)"))
            continue
        used.add(e)
        # ★나라·날짜 집합은 **미리 한 번** 만들어 둔다. 짝마다 원본을 훑으면
        #   (짝 1,100개 × 136만 행) 이라 몇 분이 걸린다.
        if cc.get((ip, k), frozenset()) & cc.get((ip, e), frozenset()):
            ad = dc.get((ip, k), frozenset()) & dc.get((ip, e), frozenset())
            if ad:                                    # C — 같은 날 공존
                # 2026-08-19 사용자 확정: 같은 날 겹치는 건 "딴 상품" 근거가 못 된다.
                # 21건을 손으로 다 본 결과 하나(렌탈 fromis 9)만 진짜 다른 상품이었고
                # 나머지는 그냥 한/영 병기였다. → 막지 말고 합치되 **기록은 남긴다**.
                # 새로 생기는 짝은 reports/한영통합_C_합침.csv 로 눈에 띄게 한다.
                same.append((axis, ip, f"{k} + {e}",
                             f"같은 날 같은 나라에 둘 다 있었지만 합쳤어요({len(ad)}일)"))
        # 접두어가 같으면 괄호 안에선 뗀다 — '260624_라이즈(260624_RIIZE)' 는 눈이 아프다.
        kb, eb = _base(k), _base(e)
        _pre = re.match(r"^([0-9]{5,8}_|wide_|[A-Za-z]+_)", kb)
        if _pre and eb.startswith(_pre.group(1)):
            eb = eb[len(_pre.group(1)):]
        rep = f"{kb}({eb})" + (f" {ks}" if ks else "")
        pairs[k] = rep
        pairs[e] = rep
    return pairs, held, same


# ── ④ 같은 글자끼리 갈린 것 — 대소문자·띄어쓰기·날짜접두어만 다른 표기 ──────────
#
# ③(로마자)이 못 잡는 갈래다. `260420_REDRED`(26.2억) 와 `REDRED`(9.9억) 는
# 같은 테마인데 회차가 넘어가며 이름에 날짜를 붙인 것이다.
#
# ★`$` `%` 는 지우면 안 된다 — 잡음이 아니라 **구분 표식**이다.
#   타이틀 `260227 BOYNEXTDOOR` 안에 BOYNEXTDOOR / $ / $$ / $$$$ / $$$$$ 가
#   같은 기간에 프레임 12개씩 나란히 팔린다. 지웠으면 5개가 한 줄로 뭉갰다.
#
# 그래서 근거는 **한 타이틀 안에서 하루라도 겹치는가**로 잡는다.
#   겹친다   → 나란히 팔린 다른 것. 손대지 않는다.
#   안 겹친다 → 도중에 표기만 바꾼 것. 합친다.
_VDATE = re.compile(r"^(\d{5,8})[_\s-]*")


def _vkey(n: str) -> str:
    """★글자 종류로 거르면 안 된다. 처음에 `[^0-9a-z가-힣$%]` 로 지웠더니
    가타카나가 통째로 날아가 `モンチッチ VER1`·`チムたん VER1`·`ベビチッチ VER1`
    셋이 전부 `ver1` 이 돼 한 묶음이 됐다. **글자냐 아니냐**로만 가른다."""
    t = _VDATE.sub("", str(n).strip().lower())
    return "".join(c for c in t if c.isalnum() or c in "$%")


def _variants(d, axis, ip_col, amt_col, skip_ips, block):
    """{IP: {이름: 대표}} 와 기록용 목록. 대표는 매출이 가장 큰 표기."""
    a = d[~d[axis].astype(str).isin(["", "None", "nan", "<NA>"])]
    amt = a.groupby([ip_col, axis], observed=True)[amt_col].sum()
    td = {}
    for ip, nm, ti, dt in zip(a[ip_col], a[axis], a["타이틀"], a["날짜"]):
        td.setdefault((ip, str(nm)), {}).setdefault(ti, set()).add(dt)

    buckets = {}
    for (ip, nm), v in amt.items():
        k = _vkey(nm)
        if len(k) >= 2:
            buckets.setdefault((ip, k), []).append((str(nm), float(v)))

    out, log, held = {}, [], []
    for (ip, _k), mem in buckets.items():
        if len(mem) < 2:
            continue
        mem.sort(key=lambda x: -x[1])
        names = [n for n, _ in mem]
        if ip in skip_ips:
            held.append((axis, ip, " + ".join(names), "손대지 않기로 한 IP"))
            continue
        if any((ip, x, y) in block or (ip, y, x) in block
               for x in names for y in names if x != y):
            held.append((axis, ip, " + ".join(names), "손으로 뺀 짝(_제외)"))
            continue
        ov = 0
        for i in range(len(names)):
            for j in range(i + 1, len(names)):
                A, B = td.get((ip, names[i]), {}), td.get((ip, names[j]), {})
                for ti in set(A) & set(B):
                    ov = max(ov, len(A[ti] & B[ti]))
        if ov:
            held.append((axis, ip, " + ".join(names),
                         f"한 타이틀 안에서 {ov}일 겹쳐요 — 나란히 팔린 다른 것"))
            continue
        rep_name = names[0]
        for n in names[1:]:
            out.setdefault(ip, {})[n] = rep_name
        log.append((axis, ip, " → ".join(names[1:]) + f" → {rep_name}",
                    int(sum(v for _, v in mem))))
    return out, log, held


# ── ⑤ 통합이름 잔여 — `한글(ENG)` 와 그 한쪽만 적힌 이름 ───────────────────────
#
# ②③ 이 `코르티스 ver 2` + `CORTIS ver 2` 를 `코르티스 ver 2(CORTIS ver 2)` 로
# 합쳐 놓으면, 같은 것을 영문으로만 적은 `CORTIS ver2` 가 글자가 달라져 ④에도
# 안 걸린다. 그래서 괄호 **양쪽**을 각각 열쇠로 한 번 더 본다.
#
# ★괄호 안이 늘 번역인 건 아니다(`브레드이발소 B ver (포토카드)`). 그래서
#   **우리가 ②③ 으로 만든 이름**에만 적용한다 — 그건 괄호 안이 영문 이름인 게 확실하다.
# ★대표는 매출이 큰 쪽이 아니라 **통합 이름** 쪽으로 둔다. 한글·영문이 다 보이는 게 낫다.
# 기존 COMBO 는 `)` 로 끝나야 해서 `아사히(ASAHI) A` 같은 표식 붙은 걸 못 잡는다.
_RCOMBO = re.compile(r"^(?P<a>[^()]+)\((?P<b>[^()]+)\)\s*(?P<s>[A-Za-z])?$")
_GENERIC = re.compile(
    r"(^|[\s_(])(ver\.?\s*\d*|[a-z]\s*ver\.?|v\d|버전\s*\d*|타입\s*\d*|type\s*\d*)([\s_)]|$)",
    re.I)


def _residual(d, axis, t, skip_ips, block, ok_generic):
    """★`ver 1` `A ver` 같은 **자리표**는 회차마다 다시 쓰인다.
    2025년 회차의 `CORTIS ver2` 와 2026년 회차의 `코르티스 ver 2` 는 다른 작품이다.
    그래서 자리표는 기본으로 막고, 사용자가 확인한 IP 만 `_자리표_허용IP` 로 연다."""
    a = d[~d[axis].astype(str).isin(["", "None", "nan", "<NA>"])]
    made = {(ip, v) for ip, m in t.items() for v in m.values()}
    amt = a.groupby(["_ip", axis], observed=True)["최종결제금액"].sum()
    td = {}
    for ip, nm, ti, dt in zip(a["_ip"], a[axis], a["타이틀"], a["날짜"]):
        td.setdefault((ip, str(nm)), {}).setdefault(ti, set()).add(dt)
    byip = {}
    for (ip, nm), v in amt.items():
        byip.setdefault(ip, {})[str(nm)] = float(v)

    out, log, held, seen = {}, [], [], set()
    for ip, names in byip.items():
        if ip in skip_ips:
            continue
        idx = {}
        for n in names:
            idx.setdefault(_vkey(n), []).append(n)
        for n in sorted(names, key=lambda x: -names[x]):
            if (ip, n) not in made:
                continue
            m = _RCOMBO.match(n)
            if not m:
                continue
            sfx = m.group("s") or ""
            for side in (m.group("a"), m.group("b")):
                for other in idx.get(_vkey(side + sfx), []):
                    if other == n or (ip, n, other) in seen:
                        continue
                    seen.add((ip, n, other))
                    seen.add((ip, other, n))
                    if (ip, n, other) in block or (ip, other, n) in block:
                        held.append((axis, ip, f"{n} + {other}", "손으로 뺀 짝(_제외)"))
                        continue
                    A, B = td.get((ip, n), {}), td.get((ip, other), {})
                    ov = max([len(A[ti] & B[ti]) for ti in set(A) & set(B)] or [0])
                    if ov:
                        held.append((axis, ip, f"{n} + {other}",
                                     f"한 타이틀 안에서 {ov}일 겹쳐요 — 나란히 팔린 다른 것"))
                        continue
                    if (_GENERIC.search(n) or _GENERIC.search(other)) \
                            and ip not in ok_generic:
                        held.append((axis, ip, f"{n} + {other}",
                                     "자리표 이름(ver·타입) — 회차마다 다시 써요"))
                        continue
                    out.setdefault(ip, {})[other] = n
                    log.append((axis, ip, f"{other} → {n}",
                                int(names[n] + names[other])))
    return out, log, held


def photoism() -> tuple[dict, list, list, list]:
    f = DATA_DIR / "theme_daily.parquet"
    if not f.exists():
        return {}, [], [], []
    d = pd.read_parquet(f, columns=["날짜", "국가코드", "타이틀",
                                    "테마", "프레임", "최종결제금액"])
    d["_ip"] = d["타이틀"].map(ip_name_of)
    block = _blocklist()
    _skip = set(_skip_ips())
    _okgen = set(_json_list("_자리표_허용IP"))
    out, took, skipped, cmerged, vtook = {}, [], [], [], []
    for axis in ("테마", "프레임"):
        d[axis] = d[axis].astype(str).map(name_alias.fold)
        g = d[~d[axis].isin(["", "None", "nan", "<NA>"])]
        agg = (g.groupby(["_ip", axis], observed=True)["최종결제금액"]
               .sum().reset_index())
        by = {ip: dict(zip(o[axis], o["최종결제금액"])) for ip, o in agg.groupby("_ip")}
        t, tk, sk = _build(by)
        # ②를 먼저 태운 이름으로 ③을 찾는다 — 안 그러면 이미 정해진 짝을 또 건드린다.
        d[axis] = [t.get(i, {}).get(n, n) for i, n in zip(d["_ip"], d[axis])]
        # (IP, 이름) → 나라 집합 / (날짜,나라) 집합. 한 번만 만든다.
        _cc, _dc = {}, {}
        for _ip, _nm, _dt, _c in zip(d["_ip"], d[axis], d["날짜"], d["국가코드"]):
            _cc.setdefault((_ip, _nm), set()).add(_c)
            _dc.setdefault((_ip, _nm), set()).add((_dt, _c))
        for ip, rev in by.items():
            m = t.get(ip, {})
            rev2 = {}
            for n, v in rev.items():
                rev2[m.get(n, n)] = rev2.get(m.get(n, n), 0) + v
            settled = {_loose(v.split("(")[-1].rstrip(")")) for v in m.values()}
            gp, hd, sm = _guess(_cc, _dc, axis, ip, rev2, settled, block)
            cmerged += [(axis, x[1], x[2], x[3]) for x in sm]
            if gp:
                t.setdefault(ip, {}).update(gp)
                tk += [(ip, k, v, rev2.get(k, 0)) for k, v in gp.items()]
            sk += [(x[1], x[2], x[3]) for x in hd]
        # ④ ②③ 을 태운 이름 위에서 표기 차이를 마저 합친다.
        d[axis] = [t.get(i, {}).get(n, n) for i, n in zip(d["_ip"], d[axis])]
        vm, vlog, vheld = _variants(d, axis, "_ip", "최종결제금액", _skip, block)
        for ip, mm in vm.items():
            cur = t.setdefault(ip, {})
            for k, v in list(cur.items()):     # 이미 ②③ 으로 옮긴 이름도 따라 옮긴다
                if v in mm:
                    cur[k] = mm[v]
            for k, v in mm.items():
                cur.setdefault(k, v)
        vtook += vlog          # vlog 는 이미 (축, IP, …) 로 온다
        sk += [(x[1], x[2], x[3]) for x in vheld]
        # ⑤ 통합이름과 그 한쪽만 적힌 이름
        d[axis] = [t.get(i, {}).get(n, n) for i, n in zip(d["_ip"], d[axis])]
        rm, rlog, rheld = _residual(d, axis, t, _skip, block, _okgen)
        for ip, mm in rm.items():
            cur = t.setdefault(ip, {})
            for k, v in list(cur.items()):
                if v in mm:
                    cur[k] = mm[v]
            for k, v in mm.items():
                cur.setdefault(k, v)
        vtook += rlog
        sk += [(x[1], x[2], x[3]) for x in rheld]
        out[axis] = {k: dict(sorted(v.items())) for k, v in t.items() if v}
        took += [(axis, *x) for x in tk]
        skipped += [(axis, *x) for x in sk]
    _write_csv(vtook, '표기통합_합침.csv', ['축', 'IP', '합친것', '합계'])
    return out, took, skipped, cmerged


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
    ph, ph_t, ph_s, ph_c = photoism()
    sn, sn_t, sn_s = snapism()
    try:
        _cur = json.loads(OUT.read_text(encoding="utf-8"))
    except Exception:
        _cur = {}
    doc = {
        "_meta": {
            "만든날": str(date.today()),
            "만든법": "build_name_alias.py — ② 데이터에 실재하는 '한글(ENG)' 결합 표기 "
                     "+ ③ 로마자 추정(나라·날짜가 안 겹칠 때만)",
            "주의": "손으로 고쳐도 되지만, 다시 돌리면 덮어써요. "
                   "영영 빼려면 아래 _제외 에 적으세요 — 그건 다시 돌려도 남아요.",
            "짝수": {"포토이즘": len(ph_t), "스내피즘": len(sn_t)},
            "보류": len(ph_s) + len(sn_s),
        },
        "_제외": _cur.get("_제외", []),
        "_표기통합_제외IP": _cur.get("_표기통합_제외IP", []),
        "_자리표_허용IP": _cur.get("_자리표_허용IP", []),
        "포토이즘": ph,
        "스내피즘": sn,
    }
    (BASE_DIR / "reports" / "한영통합_보류.csv").parent.mkdir(exist_ok=True)
    pd.DataFrame(ph_s + sn_s, columns=["축", "IP", "이름", "왜"]).to_csv(
        BASE_DIR / "reports" / "한영통합_보류.csv", index=False, encoding="utf-8-sig")
    # 같은 날 겹쳤는데도 합친 짝 — 새로 생기면 여기서 눈에 띄게(2026-08-19)
    pd.DataFrame(ph_c, columns=["축", "IP", "이름", "왜"]).to_csv(
        BASE_DIR / "reports" / "한영통합_C_합침.csv", index=False, encoding="utf-8-sig")
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
