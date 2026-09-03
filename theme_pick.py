"""정산 대상을 **테마 축 · 멤버(프레임) 축** 두 갈래로 고르게 해 주는 표.

왜 두 축인가 (2026-08-24, 사용자 확정)
--------------------------------------
한 타이틀 안에서 무엇을 정산할지 고를 때, **테마의 뜻이 타이틀마다 다르다.**

  · `260710 에이티즈` — 테마 `260710_산` · `여상` 이 **멤버**고, 프레임 `산 A`/`산 B` 는 판형이다.
  · `260505 CORTIS` — 테마 `REDRED`·`GREENGREEN`·`ver 1~3` 은 **버전**이고,
    프레임 `MARTIN`·`JAMES`… 가 멤버다. 그리고 같은 멤버 5명이 **모든 테마에 반복된다.**

즉 CORTIS 는 계층이 아니라 **격자**(테마 5 × 멤버 5)다. 트리로 그리면 `MARTIN` 을
다섯 번 체크해야 한다. 그래서 두 축을 따로 두고 **교집합**으로 잡는다.
2026년 기준 (타이틀×테마) 3,420 조합 중 55%가 프레임 3개 이상이고 그게 금액의 86.4% —
즉 돈의 대부분이 격자 모양이라, 테마 하나로 갈음할 수 없다.

테마는 어디에 있나 — 원장에는 **없다**
--------------------------------------
★★`master_photoism.parquet` 에 테마 열이 없다(대·중·소분류는 매장 유형이다).
  테마는 `theme_daily.parquet`(CMS 테마 수집본)에만 있다.
  다행히 두 곳의 금액이 **(타이틀 × 국가 × 프레임) 단위로 맞는다** —
  2026-07 전량 대조: 11,392 조합 중 98.60% 가 원 단위까지 일치, 어긋난 합
  ₩108,770 / ₩87.7억 = **0.0012%**(알려진 자정 경계 오차).
  그래서 이 모듈은 **theme_daily 로 고를 것을 정하고, 금액은 원장에서 낸다.**

★테마 이름은 타이틀을 건너 재사용된다 — `NCT WISH` 하나가 SM ent 타이틀 23개에 걸쳐
  있고, 전체 테마의 25.6% 가 이런 경우다. 그래서 고르는 단위는 이름이 아니라
  **(타이틀 × 테마)** 다. 안 그러면 지금 티켓으로 고를 때와 똑같이 남의 회차가 딸려 온다.

무엇을 WHERE 로 옮길 수 있나
----------------------------
원장은 `프레임 이름` 으로만 걸 수 있으므로, **고른 테마가 프레임을 통째로 가를 때만**
정확한 WHERE 가 된다.
  · (타이틀×프레임)의 93.1% 는 테마가 딱 하나다 → 그대로 WHERE 로 간다.
  · 나머지 6.9%(금액으로는 38.9% · CORTIS 계열)는 한 프레임이 여러 테마에 걸쳐 있어
    **원장에서 가를 수 없다.** 이 모듈은 그걸 `straddling` 으로 따로 돌려준다 —
    조용히 반올림하거나 비율로 나눠 담지 않는다. 화면이 그대로 알려 주게 한다.
"""
from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd

BASE_DIR = Path(__file__).parent
THEME_FILE = BASE_DIR / "data" / "theme_daily.parquet"

# 금액이 0인 조합은 고를 이유가 없다(취소만 남은 자리 등) — 목록을 어지럽힌다.
_MIN_AMT = 0


def available() -> bool:
    """테마 수집본이 있나. 없으면 화면이 두 축을 아예 안 그린다."""
    return THEME_FILE.exists()


def _sqlist(xs) -> str:
    return ",".join("'" + str(x).replace("'", "''") + "'" for x in xs) or "''"


def _variants(titles, start: str, end: str) -> list[str]:
    """정산 타이틀 → **원장에 실제로 적힌 타이틀명들**.

    ★★한 정산 타이틀이 원장에서는 한/영 두 이름으로 갈려 있다 (2026-08-24 실측):
      `260505 코르티스` → 원장 `260505 코르티스`(한국 ₩1.65억) + `260505 CORTIS`(해외 22.9억).
      멤버 이름도 같이 갈린다 — `건호` 와 `KEONHO` 가 따로 있다.
      표시 이름 하나로만 테마본을 찾으면 **78%가 통째로 빠진다**(실제로 그랬다).
      그래서 정산 쿼리가 쓰는 것과 **똑같은 이름 집합**(`_title_map`)을 쓴다.
    """
    out = set(titles)
    try:
        import settlement_calc as sc
        m = sc._title_map(start, end)          # 기간 단위 캐시라 여기서 불러도 싸다
        for t in titles:
            tn, fr = m.get(t, (set(), set()))
            out |= set(tn) | set(fr)
    except Exception:                          # noqa: BLE001
        pass                                   # 못 얻으면 표시 이름만으로 — 적게 나올 뿐 틀리진 않는다
    return sorted(out)


def _rows(titles, start: str, end: str) -> pd.DataFrame:
    """그 기간·그 타이틀의 (타이틀 × 테마 × 프레임) 금액. 없으면 빈 표.

    ★★타이틀을 **버리면 안 된다** (2026-08-28 수정). 전엔 `GROUP BY 테마, 프레임` 이라
      타이틀이 뭉개졌고, 그래서 같은 멤버가 여러 회차에 나오면 회차를 건너 한 덩어리가
      됐다. 실제 사고: `루네이트` 4개 타이틀 중 `260722` 두 개는 테마가 하나뿐이라
      통째로 골라낼 수 있는데, `260729`(ON·OFF 두 테마)의 같은 멤버와 뭉쳐지면서
      **844,844원이 '못 가른다'로 막혀** 있었다. 원장은 타이틀명을 갖고 있으므로
      (타이틀 × 프레임) 이면 정확히 걸 수 있다.
    """
    if not titles or not available():
        return pd.DataFrame(columns=["타이틀", "테마", "프레임", "금액"])
    titles = _variants(titles, start, end)
    con = duckdb.connect(config={"memory_limit": "600MB", "threads": 2})
    try:
        return con.execute(f"""
            SELECT CAST("타이틀" AS VARCHAR)                                        AS "타이틀",
                   COALESCE(NULLIF(TRIM(CAST("테마" AS VARCHAR)), ''), '(테마 없음)') AS "테마",
                   CAST("프레임" AS VARCHAR)                                        AS "프레임",
                   CAST(COALESCE(SUM("최종결제금액"), 0) AS BIGINT)                  AS "금액"
            FROM read_parquet('{THEME_FILE.as_posix()}')
            WHERE CAST("타이틀" AS VARCHAR) IN ({_sqlist(titles)})
              AND TRY_CAST("날짜" AS DATE) BETWEEN DATE '{start}' AND DATE '{end}'
              -- ★★미니스티커는 정산 대상이 아니다 (2026-09-03 사용자 확정).
              --   지금은 무료라 금액이 0이어서 아래 HAVING 에 우연히 걸러지지만,
              --   **나중에 추가금액이 붙으면 말없이 선택지에 나타난다.**
              --   금액이 아니라 **이름으로** 막아야 그때도 계속 빠져 있다.
              --   (금액 쪽 차단은 settlement_calc._mini_filter 에 같이 있다)
              AND UPPER(TRIM(CAST("프레임" AS VARCHAR))) NOT LIKE '%-MINI'
            GROUP BY 1, 2, 3
            HAVING SUM("최종결제금액") > {_MIN_AMT}
        """).df()
    finally:
        con.close()


def artist_groups(themes) -> dict:
    """테마 목록 → `{아티스트: [테마…]}` (SM 정의에 걸리는 것만).

    ★★왜 필요한가 — **한 타이틀에 여러 아티스트가 섞이고, 그 안에서 또 한/영 테마가
      쌍으로 갈린다.** 실측(`260804 SM ent` · 2026-08):
          RIIZE      `260624_RIIZE` 3,297만 + `260624_라이즈` 1,995만
          Red Velvet `260804_Red Velvet` 1,709만 + `260804_레드벨벳` 1,421만
      손으로 고르면 한글 쌍을 놓치기 쉽고 그러면 **30% 넘게 덜 정산된다.**
      아티스트 한 줄을 켜면 그 쌍이 **같이** 켜진다.
    ★매칭 규칙은 `sm_artists.py` 한 곳에 있다(촬영수 리포트와 같은 규칙).
      정의를 못 읽으면 조용히 빈 dict — 아티스트 축만 안 보이고 나머지는 그대로 돈다.
    """
    try:
        import sm_artists
        return sm_artists.group_themes(themes)
    except Exception:                                        # noqa: BLE001
        return {}


def axes(titles, start: str, end: str) -> dict:
    """화면에 그릴 축.

    돌려주는 값
      themes  : [{이름, 금액, 멤버수}]   — 큰 순
      frames  : [{이름, 금액, 테마수}]   — 큰 순. 테마수>1 이면 여러 테마에 걸친 멤버
      artists : [{이름, 금액, 테마들}]   — SM 정의에 걸린 것만. 없으면 빈 목록
      grid    : True 면 격자(한 프레임이 여러 테마에 걸침) — 화면이 안내를 다르게 낸다
    """
    d = _rows(titles, start, end)
    if d.empty:
        return {"themes": [], "frames": [], "artists": [], "grid": False,
                "frames_by_theme": {}}

    th = (d.groupby("테마", as_index=False)
            .agg(금액=("금액", "sum"), 멤버수=("프레임", "nunique"))
            .sort_values("금액", ascending=False))
    fr = (d.groupby("프레임", as_index=False)
            .agg(금액=("금액", "sum"), 테마수=("테마", "nunique"))
            .sort_values("금액", ascending=False))
    # 아티스트 축 — 테마 금액을 아티스트로 굴려 올린다(한/영 쌍이 여기서 합쳐진다).
    _amt = dict(zip(th["테마"], th["금액"]))
    _grp = artist_groups(list(th["테마"]))
    arts = sorted(
        ({"이름": a, "금액": int(sum(_amt.get(t, 0) for t in ts)), "테마들": ts}
         for a, ts in _grp.items()),
        key=lambda x: -x["금액"])

    # ★테마 → 그 테마에 실제로 있는 멤버들 (2026-09-03).
    #   화면에서 테마를 고르면 멤버 후보를 그 테마 것으로 좁히는 데 쓴다.
    #   전엔 멤버 칸에 늘 전체(예: 490명)가 떠서, 고른 테마와 상관없는 이름 중에서
    #   찾아야 했다. **금액 계산에는 안 쓴다** — 고르는 것을 돕는 목록일 뿐이고,
    #   실제 필터는 `resolve()` 가 (타이틀 × 프레임) 으로 만든다.
    fbt = {t: tuple(sorted(g["프레임"].unique()))
           for t, g in d.groupby("테마", sort=False)}

    return {
        "themes": [{"이름": r["테마"], "금액": int(r["금액"]), "멤버수": int(r["멤버수"])}
                   for _, r in th.iterrows()],
        "frames": [{"이름": r["프레임"], "금액": int(r["금액"]), "테마수": int(r["테마수"])}
                   for _, r in fr.iterrows()],
        "artists": arts,
        "grid": bool((fr["테마수"] > 1).any()),
        "frames_by_theme": fbt,
    }


def resolve(titles, start: str, end: str,
            sel_themes=None, sel_frames=None) -> dict:
    """고른 두 축 → 원장에 걸 **(타이틀별) 프레임 이름 목록**.

    돌려주는 값
      title_frames: 원장에 걸 필터. `((타이틀, (프레임…)), …)` 로 정렬된 튜플.
                    **None 이면 거르지 않는다**(=전부). 캐시 키·서명에 그대로 쓰려고
                    해시 가능한 튜플로 돌려준다.
      frames      : 위의 프레임을 이름만 모은 평평한 목록(**표시용**). 필터로 쓰지 말 것 —
                    타이틀 짝이 사라져서, 회차를 건너 남의 테마까지 물고 온다.
      straddling  : 고른 테마와 안 고른 테마에 **걸쳐 있어** 원장에서 못 가른 멤버.
                    금액과 함께 돌려준다 — 화면이 이걸 그대로 보여 줘야 한다.
      dropped_amt : 안 고른 축 때문에 빠지는 금액(테마 수집본 기준, 참고값)

    ★`None` 과 `()` 는 다르다. None = 축을 안 건드림(전부), () = 하나도 안 고름.
      이걸 섞으면 '전부'가 '아무것도 아님'이 돼 문서가 0원으로 나온다.
    ★★판정 단위는 **(타이틀 × 프레임)** 이다. 프레임만으로 묶으면 회차가 뭉개져,
      한 회차에서 깨끗하게 떨어지는 멤버까지 다른 회차 때문에 걸침이 된다(위 _rows 주석).
    """
    d = _rows(titles, start, end)
    if d.empty:
        return {"frames": None, "title_frames": None,
                "straddling": [], "dropped_amt": 0}

    all_th = set(d["테마"])
    all_fr = set(d["프레임"])
    th_pick = all_th if sel_themes is None else set(sel_themes)
    fr_pick = all_fr if sel_frames is None else set(sel_frames)

    # 두 축 다 전부면 아무것도 거르지 않는다 — 지금 동작 그대로여야 한다(검증 기준).
    if th_pick >= all_th and fr_pick >= all_fr:
        return {"frames": None, "title_frames": None,
                "straddling": [], "dropped_amt": 0}

    keep: dict[str, list[str]] = {}
    strad: dict[str, dict] = {}
    picked_amt = 0
    for (t, f), g in d.groupby(["타이틀", "프레임"]):
        if f not in fr_pick:                       # 멤버 축에서 빠진 멤버
            continue
        mine = set(g["테마"])
        inside = mine & th_pick
        if not inside:                             # 고른 테마에 아예 없음
            continue
        if mine <= th_pick:                        # 통째로 들어옴 → 원장에서 정확히 잡힌다
            keep.setdefault(t, []).append(f)
            picked_amt += int(g["금액"].sum())
            continue
        # 걸쳐 있다 — 원장은 테마를 모르므로 이 멤버를 반쪽만 가져올 수가 없다.
        # 같은 멤버가 여러 회차에서 걸치면 합쳐서 한 줄로 보여 준다.
        e = strad.setdefault(f, {"이름": f, "고른 테마 금액": 0, "전체 금액": 0})
        e["고른 테마 금액"] += int(g[g["테마"].isin(inside)]["금액"].sum())
        e["전체 금액"] += int(g["금액"].sum())

    tf = tuple(sorted((t, tuple(sorted(fs))) for t, fs in keep.items()))
    return {"frames": sorted({f for fs in keep.values() for f in fs}),
            "title_frames": tf,
            "straddling": sorted(strad.values(), key=lambda x: -x["전체 금액"]),
            "dropped_amt": int(d["금액"].sum()) - picked_amt}
