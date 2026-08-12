# -*- coding: utf-8 -*-
"""필터바 오른쪽 '⬇ 내려받기' — 지금 화면에 적용된 필터 그대로 CSV 로 준다.

왜 이 자리인가
    다운로드는 결국 "지금 이 조건으로 보고 있는 것" 을 받는 일이라, 조건을 고르는
    줄 끝이 의미상 맞다. 예전엔 '시간대·데이터' 탭 안에 있었는데 그 탭을 감추면서
    (SHOW_TAB_ETC=False) 아무도 못 받게 됐다. 탭을 되살리면 탭이 다시 6개가 된다.

왜 두 단계('만들기' → '받기') 인가
    ★st.download_button 은 **바이트를 먼저 받아야** 그린다. 팝오버 안에 바로 두면
      아무도 안 눌러도 rerun 마다 CSV 를 만든다 — 포토이즘 기준 694만 행이라
      화면을 켜 두기만 해도 메모리가 터진다. 그래서 만든 것만 세션에 들고 있는다.

왜 화면 숫자와 맞는가
    집계를 여기서 다시 하지 않는다. 호출부가 **카드들이 쓰는 바로 그 프레임**을
    넘긴다(포토이즘 `paid_sales`, 스내피즘 `revenue_txns`). 기준이 갈릴 일이 없다.
"""
from __future__ import annotations

import streamlit as st

import auth

# 한 번에 들고 있을 수 있는 상한. 넘으면 만들지 않고 좁히라고 말한다 —
# 조용히 잘라 주면 받은 사람은 그게 전부인 줄 안다.
MAX_ROWS = 500_000
MAX_BYTES = 120 * 1024 * 1024


def agg(base, keys, *, money, count=None, by_date=False):
    """`keys` 로 묶어 매출액·건수를 낸 표. base 는 이미 걸러진 프레임이어야 한다."""
    keys = [k for k in keys if k in base.columns]
    if not keys:
        raise ValueError("이 자료에 필요한 열이 없어요.")
    g = base.groupby(keys, observed=True)
    # 포토이즘 집계엔 '건수' 열이 있고, 스내피즘은 거래 단위라 행 수가 곧 건수다.
    out = (g.agg(매출액=(money, "sum"), 건수=(count, "sum")).reset_index()
           if count and count in base.columns
           else g.agg(매출액=(money, "sum"), 건수=(money, "size")).reset_index())
    out["매출액"] = out["매출액"].round(0).astype("int64")
    return (out.sort_values(keys[0]) if by_date
            else out.sort_values("매출액", ascending=False)).reset_index(drop=True)


def raw(base, cols):
    """거래(집계) 원본 그대로. 너무 크면 만들지 않는다."""
    if len(base) > MAX_ROWS:
        raise ValueError(
            f"{len(base):,}행이라 한 번에 못 만들어요({MAX_ROWS:,}행까지). "
            "기간을 좁히거나 국가·매장을 골라 주세요.")
    avail = [c for c in cols if c in base.columns]
    return base[avail].reset_index(drop=True)


def _human(n):
    return f"{n / 1048576:.1f}MB" if n >= 1048576 else f"{max(1, n // 1024):,}KB"


def control(slot, *, page, prefix, datasets, note=""):
    """필터바 칸(slot) 안에 '⬇ 내려받기' 팝오버를 그린다.

    datasets = {라벨: (파일명조각, 표를 만드는 함수)}
        ★함수로 받는다 — 고르지도 않은 자료를 미리 만들지 않으려고.
    """
    kb, km = f"{page}__dl_bytes", f"{page}__dl_meta"
    with slot:
        st.markdown('<div class="fbl">&nbsp;</div>', unsafe_allow_html=True)
        with st.popover("⬇ 내려받기", use_container_width=True):
            st.markdown("**데이터 내려받기**")
            if note:
                st.caption(note)
            lab = st.radio("받을 자료", list(datasets), key=f"{page}__dl_pick")

            if st.button("📦 CSV 만들기", key=f"{page}__dl_make",
                         use_container_width=True, type="primary"):
                st.session_state.pop(kb, None)      # 옛 파일부터 버린다(메모리)
                st.session_state.pop(km, None)
                try:
                    with st.spinner("만드는 중이에요…"):
                        d = datasets[lab][1]()
                        buf = d.to_csv(index=False).encode("utf-8-sig")
                    if len(buf) > MAX_BYTES:
                        raise ValueError(f"{_human(len(buf))} 라 너무 커요. 조건을 좁혀 주세요.")
                    st.session_state[kb] = buf
                    st.session_state[km] = (lab, len(d), f"{prefix}_{datasets[lab][0]}.csv")
                except Exception as ex:            # noqa: BLE001
                    st.warning(str(ex) or "만들지 못했어요. 조건을 바꿔 보세요.")

            meta = st.session_state.get(km)
            data = st.session_state.get(kb)
            if data is not None and meta and meta[0] == lab:
                auth.download_button(
                    f"⬇ 받기 · {meta[1]:,}행 · {_human(len(data))}", data, meta[2], "text/csv",
                    key=f"{page}__dl_get", use_container_width=True,
                    page=page, rows=meta[1])
                st.caption("엑셀에서 바로 열려요(UTF-8 BOM). 다른 자료는 고른 뒤 다시 만들어 주세요.")
            else:
                st.caption("고른 뒤 **CSV 만들기**를 눌러 주세요.")
