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


_CSS = """
<style>
/* 내려받기 패널 — 목록을 한 화면에 쭉 깔아 보여준다(CMS 다운로드 화면 톤) */
[data-testid="stPopoverBody"]:has(.dlpanel){ min-width:600px !important; }
.dlpanel-hd{ font-size:15px; font-weight:800; color:var(--text,#141c2d); margin:0 0 2px; }
.dlpanel-sub{ font-size:12px; color:var(--text-2,#5b6577); margin:0 0 10px; }
.dlsec{ font-size:11px; font-weight:800; letter-spacing:.04em; color:var(--text-3,#8a93a5);
  text-transform:none; margin:12px 0 2px; padding-top:8px; border-top:1px solid var(--border,#e6e9f0); }
.dlsec.first{ border-top:none; padding-top:0; margin-top:2px; }
.dlname{ font-size:13.5px; font-weight:700; color:var(--text,#141c2d); line-height:1.35; }
.dldesc{ font-size:11.5px; color:var(--text-2,#5b6577); line-height:1.35; }
/* 버튼은 패널 안에서만 작게. .dlpanel 은 위 markdown 이 심는 표식이라
   `:has()` 로 패널을 짚은 뒤 그 안의 버튼을 고른다(버튼 자체는 감쌀 수 없다). */
[data-testid="stPopoverBody"]:has(.dlpanel) [data-testid="stButton"] button,
[data-testid="stPopoverBody"]:has(.dlpanel) [data-testid="stDownloadButton"] button{
  min-height:0 !important; padding:5px 10px !important; font-size:12px !important;
  border-radius:8px !important; }
</style>
"""


def control(slot, *, page, prefix, sections, note=""):
    """필터바 칸(slot) 안 '⬇ 내려받기' → **자료 목록을 통째로 펼친 패널**.

    sections = [(구분, [(라벨, 파일명조각, 설명, 표를 만드는 함수), …]), …]
        ★만드는 건 함수로 받는다 — 패널을 여는 것만으로 CSV 를 만들면 안 된다
          (포토이즘 694만 행). 누른 줄 하나만 만든다.
        ★들고 있는 건 **한 개뿐**이다. 여러 개를 동시에 세션에 쥐면 사람 수만큼
          곱해져 메모리가 터진다 — 다른 줄을 만들면 앞의 것은 버린다.
    """
    kb, km = f"{page}__dl_bytes", f"{page}__dl_meta"
    with slot:
        st.markdown('<div class="fbl">&nbsp;</div>', unsafe_allow_html=True)
        with st.popover("⬇ 내려받기", use_container_width=True):
            st.markdown(_CSS, unsafe_allow_html=True)
            st.markdown('<div class="dlpanel"><div class="dlpanel-hd">📥 데이터 내려받기</div>'
                        f'<div class="dlpanel-sub">{note}</div></div>', unsafe_allow_html=True)

            made = st.session_state.get(km)          # (키, 행수, 파일명)
            data = st.session_state.get(kb)
            err = None

            for si, (sec, items) in enumerate(sections):
                st.markdown(f'<div class="dlsec{" first" if si == 0 else ""}">{sec}</div>',
                            unsafe_allow_html=True)
                for lab, frag, desc, fn in items:
                    key = f"{sec}/{lab}"
                    c1, c2 = st.columns([2.9, 1.1], vertical_alignment="center")
                    c1.markdown(f'<div class="dlpanel"><div class="dlname">{lab}</div>'
                                f'<div class="dldesc">{desc}</div></div>', unsafe_allow_html=True)
                    with c2:
                        if made and made[0] == key and data is not None:
                            auth.download_button(
                                f"⬇ {made[1]:,}행 · {_human(len(data))}", data, made[2], "text/csv",
                                key=f"{page}__dlget__{key}", use_container_width=True,
                                page=page, rows=made[1])
                        elif st.button("CSV 만들기", key=f"{page}__dlmake__{key}",
                                       use_container_width=True):
                            st.session_state.pop(kb, None)   # 옛 파일부터 버린다
                            st.session_state.pop(km, None)
                            try:
                                with st.spinner("만드는 중이에요…"):
                                    d = fn()
                                    buf = d.to_csv(index=False).encode("utf-8-sig")
                                if len(buf) > MAX_BYTES:
                                    raise ValueError(f"{_human(len(buf))} 라 너무 커요. 조건을 좁혀 주세요.")
                                st.session_state[kb] = buf
                                st.session_state[km] = (key, len(d), f"{prefix}_{frag}.csv")
                                st.rerun(scope="fragment")   # 그 줄을 '받기'로 바꿔 그린다
                            except Exception as ex:          # noqa: BLE001
                                err = str(ex) or "만들지 못했어요. 조건을 바꿔 보세요."

            if err:
                st.warning(err)
            st.caption("엑셀에서 바로 열려요(UTF-8 BOM) · 한 번에 한 개씩 만들어요.")
