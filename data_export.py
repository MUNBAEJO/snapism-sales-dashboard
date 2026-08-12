# -*- coding: utf-8 -*-
"""필터바 오른쪽 '⬇ 내려받기' — 지금 화면에 적용된 필터 그대로 CSV 한 장으로.

왜 이 자리인가
    다운로드는 결국 "지금 이 조건으로 보고 있는 것" 을 받는 일이라, 조건을 고르는
    줄 끝이 의미상 맞다. 예전엔 '시간대·데이터' 탭 안에 있었는데 그 탭을 감추면서
    (SHOW_TAB_ETC=False) 아무도 못 받게 됐다. 탭을 되살리면 탭이 다시 6개가 된다.

왜 한 장인가
    CMS 에서 받는 것과 같은 모양이다 — **열을 다 붙여서 한 번에** 준다. 국가별·
    타이틀별로 쪼개 여러 파일로 주다가 되돌렸다(사용자 요청). 받는 쪽은 어차피
    엑셀에서 피벗을 돌리므로, 축을 우리가 미리 정해 줄 이유가 없다.

왜 두 단계('만들기' → '받기') 인가
    ★st.download_button 은 **바이트를 먼저 받아야** 그린다. 팝오버에 바로 두면
      아무도 안 눌러도 rerun 마다 CSV 를 만든다 — 포토이즘은 30일치가 41만 행이라
      화면을 켜 두기만 해도 메모리가 터진다. 그래서 만든 것만 세션에 들고 있는다.
"""
from __future__ import annotations

import streamlit as st

import auth

# 한 번에 들고 있을 수 있는 상한. 넘으면 만들지 않고 좁히라고 말한다 —
# 조용히 잘라 주면 받은 사람은 그게 전부인 줄 안다.
# (포토이즘 실측: 30일 41만 행 · 90일 126만 행 → 90일은 기간을 나눠 받아야 한다)
MAX_ROWS = 500_000
MAX_BYTES = 120 * 1024 * 1024

_CSS = """
<style>
[data-testid="stPopoverBody"]:has(.dlpanel){ min-width:520px !important; }
.dlpanel-hd{ font-size:15px; font-weight:800; color:var(--text,#141c2d); margin:0 0 2px; }
.dlpanel-sub{ font-size:12px; color:var(--text-2,#5b6577); margin:0 0 12px; }
.dlstat{ display:flex; gap:18px; align-items:baseline; margin:0 0 8px; }
.dlstat b{ font-size:20px; font-weight:800; color:var(--text,#141c2d); letter-spacing:-.01em; }
.dlstat span{ font-size:12px; color:var(--text-2,#5b6577); }
.dlcols{ font-size:11.5px; line-height:1.7; color:var(--text-2,#5b6577);
  background:var(--surface-2,#f5f7fb); border:1px solid var(--border,#e6e9f0);
  border-radius:8px; padding:8px 10px; margin:0 0 12px; }
.dlcols i{ font-style:normal; color:var(--text,#141c2d); font-weight:600; }
</style>
"""


def _human(n):
    return f"{n / 1048576:.1f}MB" if n >= 1048576 else f"{max(1, n // 1024):,}KB"


def control(slot, *, page, prefix, get_base, cols, note=""):
    """필터바 칸(slot) 안에 '⬇ 내려받기' 패널을 그린다.

    get_base()  적용된 필터가 걸린 프레임을 돌려주는 함수.
                ★함수로 받는다 — 필터바는 본문보다 먼저 그려져 값을 인자로 못 받고,
                  실제로 쓰는 건 버튼을 누른 뒤(본문이 한 번 돈 뒤)라 그때는 값이 있다.
    cols        내보낼 열 순서. 프레임에 없는 열은 조용히 빠진다.
    """
    kb, km = f"{page}__dl_bytes", f"{page}__dl_meta"
    with slot:
        st.markdown('<div class="fbl">&nbsp;</div>', unsafe_allow_html=True)
        with st.popover("⬇ 내려받기", use_container_width=True):
            st.markdown(_CSS, unsafe_allow_html=True)
            st.markdown('<div class="dlpanel"><div class="dlpanel-hd">📥 데이터 내려받기</div>'
                        f'<div class="dlpanel-sub">{note}</div></div>', unsafe_allow_html=True)

            try:
                base = get_base()
            except Exception:                    # noqa: BLE001
                st.info("화면을 불러오는 중이에요. 잠시 뒤 다시 열어 주세요.")
                return

            use = [c for c in cols if c in base.columns]
            n = len(base)
            st.markdown(
                f'<div class="dlpanel"><div class="dlstat"><b>{n:,}행</b>'
                f'<span>열 {len(use)}개</span></div>'
                f'<div class="dlcols">' + " · ".join(f"<i>{c}</i>" for c in use)
                + '</div></div>', unsafe_allow_html=True)

            if n == 0:
                st.info("조건에 맞는 데이터가 없어요. 필터를 넓혀 보세요.")
                return
            if n > MAX_ROWS:
                st.warning(f"{n:,}행이라 한 번에 못 만들어요(**{MAX_ROWS:,}행**까지). "
                           "기간을 좁히거나 국가·매장을 골라서 나눠 받아 주세요.")
                return

            made = st.session_state.get(km)      # (행수, 파일명, 서명)
            data = st.session_state.get(kb)
            sig = (n, tuple(use), prefix)        # 필터가 바뀌면 옛 파일은 못 쓴다

            if data is not None and made and made[2] == sig:
                auth.download_button(
                    f"⬇ 받기 · {made[0]:,}행 · {_human(len(data))}", data, made[1], "text/csv",
                    key=f"{page}__dl_get", use_container_width=True, type="primary",
                    page=page, rows=made[0])
                st.caption("엑셀에서 바로 열려요(UTF-8 BOM).")
            elif st.button("📦 CSV 만들기", key=f"{page}__dl_make",
                           use_container_width=True, type="primary"):
                st.session_state.pop(kb, None)   # 옛 파일부터 버린다(메모리)
                st.session_state.pop(km, None)
                try:
                    with st.spinner(f"{n:,}행을 만드는 중이에요…"):
                        buf = base[use].to_csv(index=False).encode("utf-8-sig")
                    if len(buf) > MAX_BYTES:
                        raise ValueError(f"{_human(len(buf))} 라 너무 커요. 기간을 좁혀 주세요.")
                    st.session_state[kb] = buf
                    st.session_state[km] = (n, f"{prefix}.csv", sig)
                    st.rerun(scope="fragment")   # 버튼을 '받기'로 바꿔 그린다
                except Exception as ex:          # noqa: BLE001
                    st.warning(str(ex) or "만들지 못했어요. 조건을 바꿔 보세요.")
            else:
                st.caption("누르면 지금 조건 그대로 만들어요.")
