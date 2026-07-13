# -*- coding: utf-8 -*-
"""스내피즘 매출 대시보드 — 재디자인(시안 snapism-hybrid 기준).

구조: 필터바(멀티셀렉트) + KPI 3카드 + 5탭(매출 한눈에·상품 카테고리·국가별·매장별·시간대).
'이번 달 변화'는 탭이 아니라 사이드바에 국가별로 분리.
매출 기준 = 실결제(KRW환산, 쿠폰 제외). 쿠폰·취소는 별도 KPI.
데이터 로직은 기존 로더/헬퍼를 그대로 사용(비파괴).
"""
import json
import sys
from contextlib import contextmanager
from pathlib import Path
from datetime import date, timedelta

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from guide_content import render_guide
import data_io

# ══════════════════════════════════════════════════════════════
#  디자인 시스템 (시안 토큰 이식)
# ══════════════════════════════════════════════════════════════
st.markdown("""
<style>
:root{
  --bg:#f4f5f7; --surface:#fff; --surface-2:#f8fafc; --surface-3:#eef1f5;
  --border:#e7e9ee; --border-strong:#d7dae1;
  --text:#1b2330; --text-2:#5b6573; --text-3:#98a0af;
  --brand:#3182f6; --brand-2:#4e8ef8; --brand-soft:#e8f1fe;
  --red:#f04452; --green:#15b76e; --sky:#93c5fd;
}
html, body, [class*="css"], [data-testid="stAppViewContainer"]{
  font-family:'Pretendard',-apple-system,BlinkMacSystemFont,'Segoe UI','Malgun Gothic','Apple SD Gothic Neo',sans-serif;
  letter-spacing:-0.01em;
}
/* 본문 가운데 정렬 + 시안 폭(~1060px) — layout=wide 를 강제로 좁힘 */
[data-testid="stMainBlockContainer"], .stMainBlockContainer, section.main .block-container, .block-container{
  max-width:1060px !important; margin-left:auto !important; margin-right:auto !important;
  padding-top:1.3rem !important; padding-bottom:3rem !important; }
h1{ font-weight:800 !important; letter-spacing:-0.5px; color:var(--text); }
[data-testid="stDeployButton"]{ display:none !important; }
[data-testid="stElementToolbar"]{ display:none; }
[data-testid="stSidebar"]{ background:#fbfcfe; border-right:1px solid #eceff5; }
.num{ font-variant-numeric:tabular-nums; }

/* KPI 카드 */
.kpis{ display:grid; grid-template-columns:2fr 1fr 1fr; gap:12px; margin:4px 0 6px; }
.kpi{ background:var(--surface); border:1px solid var(--border); border-radius:12px; padding:15px 17px;
      box-shadow:0 1px 3px rgba(20,28,45,.06); }
.kpi.hero{ background:linear-gradient(180deg,#fbfbff,#fff); border-color:#dcdcfb; }
.kpi .l{ font-size:12.5px; color:var(--text-2); font-weight:600; }
.kpi .v{ font-size:24px; font-weight:800; letter-spacing:-0.02em; margin-top:6px; line-height:1.05; color:var(--text); }
.kpi.hero .v{ font-size:32px; color:var(--brand); }
.kpi .d{ font-size:12px; font-weight:700; margin-top:7px; color:var(--text-3); }
@media(max-width:720px){ .kpis{ grid-template-columns:1fr; } }

/* 범위 배너 */
.scope{ background:var(--brand-soft); border:1px solid #cdd0fb; color:var(--brand); font-size:12.5px;
        font-weight:600; padding:9px 14px; border-radius:10px; margin:6px 0 2px; }

/* 섹션 헤더 */
.sechd{ display:flex; align-items:center; gap:10px; margin:20px 0 2px; }
.secn{ font-size:12px; font-weight:800; color:#fff; background:var(--brand); width:22px; height:22px;
       border-radius:7px; display:inline-flex; align-items:center; justify-content:center; flex:0 0 auto; }
.sect{ font-size:18px; font-weight:800; letter-spacing:-0.02em; color:var(--text); }
.secq{ font-size:12.5px; color:var(--text-3); margin:2px 0 10px 32px; }

/* 카드 제목 */
.ct{ font-size:14.5px; font-weight:700; display:flex; align-items:center; gap:7px; margin:2px 0 10px; color:var(--text); }

/* 비중막대 내장 표 (.ntbl) */
.ntbl{ border:1px solid var(--border); border-radius:12px; overflow:hidden; margin:2px 0 4px; }
.ntr{ display:grid; align-items:center; gap:10px; padding:12px 16px; border-bottom:1px solid var(--border);
      font-size:13px; color:var(--text); }
.ntr:last-child{ border-bottom:none; }
.ntr.nth{ background:var(--surface-2); font-size:11px; font-weight:700; color:var(--text-3); letter-spacing:.02em; }
.ntr:not(.nth):hover{ background:var(--surface-2); }
.ntr .r{ text-align:right; } .ntr .c{ text-align:center; }
.nname{ font-weight:700; }
.cur{ font-size:11px; font-weight:700; color:var(--text-2); background:var(--surface-3); padding:2px 8px; border-radius:6px; }
.rk{ font-weight:800; color:var(--text-3); font-variant-numeric:tabular-nums; }
.rk.top{ color:var(--brand); }
.npct{ display:flex; align-items:center; gap:9px; }
.npct-bar{ flex:1; height:7px; background:var(--surface-3); border-radius:5px; overflow:hidden; }
.npct-bar i{ display:block; height:100%; background:var(--brand-2); border-radius:5px; }
.npct .p{ font-size:12.5px; font-weight:700; font-variant-numeric:tabular-nums; min-width:44px; text-align:right; }

/* 도넛 오른쪽 범례 (시안) */
.lgd-wrap{ display:flex; flex-direction:column; gap:1px; justify-content:center; height:100%; padding:8px 2px; }
.lgd{ display:flex; align-items:center; gap:9px; padding:8px 4px; border-bottom:1px solid #f2f4f8; font-size:13px; }
.lgd:last-child{ border-bottom:none; }
.lgd-dot{ width:11px; height:11px; border-radius:3px; flex:0 0 auto; }
.lgd-n{ font-weight:600; color:var(--text); }
.lgd-p{ margin-left:auto; font-weight:800; font-variant-numeric:tabular-nums; color:var(--text); }

/* 가로 막대 순위 (시안 TOP — 트랙+채움) */
.hb-wrap{ display:flex; flex-direction:column; gap:12px; padding:6px 2px; }
.hb{ display:grid; grid-template-columns:104px 1fr 116px; align-items:center; gap:12px; font-size:13px; }
.hb-n{ font-weight:700; color:var(--text); text-align:right; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.hb-track{ height:22px; background:#eef1f5; border-radius:7px; overflow:hidden; }
.hb-track i{ display:block; height:100%; border-radius:7px; }
.hb-v{ text-align:right; font-weight:700; color:var(--text); font-variant-numeric:tabular-nums; }

/* 사이드바 변화 */
.mv{ display:flex; align-items:center; gap:8px; font-size:12.5px; padding:6px 2px; border-bottom:1px solid #eef1f5; }
.mv:last-child{ border-bottom:none; }
.mv .t{ font-size:10px; font-weight:700; color:var(--text-3); background:var(--surface-3); padding:1px 6px;
        border-radius:5px; flex:0 0 auto; }
.mv .n{ font-weight:600; color:var(--text); overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.mv .p{ margin-left:auto; font-weight:800; font-variant-numeric:tabular-nums; flex:0 0 auto; }
.mv .up{ color:var(--green); } .mv .down{ color:var(--red); }

/* Streamlit 기본 크롬 정리 (시안 느낌으로) */
[data-testid="stToolbar"]{ display:none !important; }
#MainMenu, footer{ display:none !important; }
[data-testid="stHeader"]{ background:transparent; height:0 !important; }
/* 사이드바 토스 톤(상시 노출) */
[data-testid="stSidebar"]{ background:#ffffff !important; border-right:1px solid #e5e8eb !important; }
[data-testid="stSidebarNav"] a{ border-radius:10px !important; padding:9px 12px !important; margin:1px 0 !important; }
[data-testid="stSidebarNav"] a:hover{ background:#f2f4f8 !important; }
[data-testid="stSidebar"] hr{ border-color:#eef1f5 !important; }
/* 사이드바 접힘 상태의 펼치기(>) 버튼 — 반드시 보이고 눌리게(페이지 이동 통로) */
[data-testid="stSidebarCollapsedControl"], [data-testid="collapsedControl"]{
  display:block !important; visibility:visible !important; opacity:1 !important;
  position:fixed !important; top:10px !important; left:10px !important; z-index:999999 !important; }
[data-testid="stSidebarCollapsedControl"] button, [data-testid="collapsedControl"] button{
  background:var(--brand) !important; color:#fff !important; border-radius:10px !important;
  box-shadow:0 2px 8px rgba(79,70,229,.35) !important; width:38px !important; height:38px !important; }

/* 탭 = 시안 언더라인 스타일 */
[data-baseweb="tab-list"]{ gap:2px; border-bottom:1px solid var(--border); }
button[data-baseweb="tab"]{ padding:10px 15px; }
button[data-baseweb="tab"] p{ font-size:14px !important; font-weight:700 !important; color:var(--text-2) !important; }
button[data-baseweb="tab"][aria-selected="true"] p{ color:var(--brand) !important; }
[data-baseweb="tab-highlight"]{ background:var(--brand) !important; height:2.5px !important; }

/* 인라인 필터바 (시안 칩 느낌) */
.fbar-label{ font-size:12.5px; font-weight:700; color:var(--text-2); margin:2px 0 6px; }
[data-testid="stPopover"] button, [data-testid="stPopoverButton"]{
  border:1px solid var(--border-strong) !important; background:var(--surface-2) !important;
  border-radius:9px !important; font-weight:600 !important; color:var(--text) !important;
  min-height:38px !important; }
/* 칩 글자 한 줄 유지(줄바꿈 방지) */
[data-testid="stPopover"] button p, [data-testid="stPopoverButton"] p{
  white-space:nowrap !important; overflow:hidden !important; text-overflow:ellipsis !important; }
/* 기간 date_input 도 칩 높이에 맞춤 */
.stDateInput input{ font-size:13px !important; }
</style>
""", unsafe_allow_html=True)

BASE_DIR = Path(__file__).parent.parent
MASTER_FILE = BASE_DIR / "data" / "master.csv"
CONFIG_FILE = BASE_DIR / "config.json"

CURRENCY_SYMBOLS = {
    "KRW": "₩", "CNY": "¥", "JPY": "¥", "IDR": "Rp", "TWD": "NT$", "THB": "฿",
    "HKD": "HK$", "MYR": "RM", "USD": "$", "EUR": "€", "GBP": "£", "VND": "₫",
    "PHP": "₱", "SGD": "S$", "AUD": "A$", "CAD": "C$", "AED": "AED", "MXN": "$",
    "PEN": "S/", "CLP": "$", "LAK": "₭", "MNT": "₮", "MOP": "MOP$", "BND": "B$",
}
COUNTRY_ISO = {
    "대한민국": "kr", "한국": "kr", "일본": "jp", "중국": "cn", "대만": "tw",
    "인도네시아": "id", "홍콩": "hk", "태국": "th", "말레이시아": "my",
}
PAL = ["#3182f6", "#f2a63b", "#12b886", "#f06595", "#7048e8", "#22b8cf", "#e8590c", "#868e96"]
BRAND, BRAND2, SKY = "#3182f6", "#4e8ef8", "#93c5fd"


def flag_img(name, h=13):
    iso = COUNTRY_ISO.get(str(name).strip())
    if not iso:
        return ""
    return (f'<img src="https://flagcdn.com/40x30/{iso}.png" height="{h}" '
            'style="vertical-align:middle;margin-right:7px;border:1px solid #eee;border-radius:2px;">')


def load_config():
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def load_exchange_rates():
    return load_config().get("exchange_rates", {"KRW": 1})


@st.cache_data(ttl=900)
def _load_data(_v):
    if not MASTER_FILE.exists():
        return pd.DataFrame()
    df = data_io.read_master(MASTER_FILE)
    df["날짜"] = pd.to_datetime(df["날짜"], errors="coerce").dt.date
    df["결제일시"] = pd.to_datetime(df["결제일시"], format="%Y.%m.%d %H:%M", errors="coerce")
    df["취소 여부"] = df["취소 여부"].astype(str).str.lower().isin(["true", "1", "yes"])
    for col in ["최종 결제 금액", "상품 단가", "쿠폰 할인 금액"]:
        df[col] = pd.to_numeric(df.get(col, 0), errors="coerce").fillna(0).astype(int)
    ex = load_exchange_rates()
    df["결제 단위"] = df["결제 단위"].fillna("KRW").astype(str).str.strip()
    df["환율"] = df["결제 단위"].map(ex).fillna(1)
    df["KRW환산금액"] = (df["최종 결제 금액"] * df["환율"]).round(0).astype(int)   # 실결제(원화)
    df["쿠폰KRW"] = (df["쿠폰 할인 금액"] * df["환율"]).round(0).astype(int)
    df["정산금액"] = df["KRW환산금액"] + df["쿠폰KRW"]
    df["총원화금액"] = df["최종 결제 금액"] + df["쿠폰 할인 금액"]
    return df


def load_data():
    return _load_data(data_io.file_version(MASTER_FILE))


def paid_sales(df):
    return df[~df["취소 여부"] & (df["최종 결제 금액"] > 0)]


def coupon_txns(df):
    return df[~df["취소 여부"] & (df["최종 결제 금액"] == 0) & (df["쿠폰 할인 금액"] > 0)]


def fmt_krw(n):
    return f"₩{int(n):,}"


def fmt_orig(amount, currency):
    sym = CURRENCY_SYMBOLS.get(str(currency).strip(), str(currency) + " ")
    return f"{sym}{int(amount):,}"


def pct_bar(frac, maxfrac=1.0):
    w = 0 if maxfrac <= 0 else min(100, max(2, frac / maxfrac * 100))
    return (f'<div class="npct"><div class="npct-bar"><i style="width:{w:.0f}%"></i></div>'
            f'<span class="p">{frac * 100:.1f}%</span></div>')


def sec(n, title, q=""):
    st.markdown(f'<div class="sechd"><span class="secn">{n}</span><span class="sect">{title}</span></div>'
                + (f'<div class="secq">{q}</div>' if q else ""), unsafe_allow_html=True)


@contextmanager
def card(title=None):
    c = st.container(border=True)
    if title:
        c.markdown(f'<div class="ct">{title}</div>', unsafe_allow_html=True)
    with c:
        yield


def style_fig(fig, height, legend=True):
    fig.update_layout(
        height=height,
        font=dict(family="Pretendard, Malgun Gothic, sans-serif", size=12, color="#2b2b3a"),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        margin=dict(t=18, b=4, l=4, r=8),
        hoverlabel=dict(font_size=12, font_family="Pretendard, Malgun Gothic, sans-serif"),
    )
    if legend:
        fig.update_layout(legend=dict(orientation="h", y=1.12, x=0, bgcolor="rgba(0,0,0,0)", font_size=11))
    else:
        fig.update_layout(showlegend=False)
    fig.update_xaxes(showgrid=False, zeroline=False)
    fig.update_yaxes(gridcolor="#eef1f6", zeroline=False)
    return fig


def cat3(series):
    s = series.astype(str).str.strip()
    return s.where(s.isin(["아티스트", "캐릭터"]), "기타")


def donut(dfg, names, values, height=250, showlegend=True):
    fig = px.pie(dfg, names=names, values=values, hole=0.58, color_discrete_sequence=PAL)
    fig.update_traces(sort=False, textposition="inside", texttemplate="%{percent}",
                      marker=dict(line=dict(color="#fff", width=2)),
                      hovertemplate="%{label}<br>%{value:,}원 (%{percent})<extra></extra>")
    style_fig(fig, height, legend=showlegend)
    if showlegend:
        fig.update_layout(legend=dict(orientation="h", y=-0.08, x=0.5, xanchor="center", font_size=10))
    return fig


def legend_list(dframe, name_col):
    """도넛 오른쪽 범례(이름 + %). 도넛 슬라이스 색(PAL)과 순서 일치."""
    d = dframe.sort_values("매출", ascending=False).reset_index(drop=True)
    tot = d["매출"].sum()
    html = '<div class="lgd-wrap">'
    for i, r in d.iterrows():
        pct = (r["매출"] / tot * 100) if tot else 0
        html += (f'<div class="lgd"><span class="lgd-dot" style="background:{PAL[i % len(PAL)]}"></span>'
                 f'<span class="lgd-n">{r[name_col]}</span><span class="lgd-p">{pct:.1f}%</span></div>')
    st.markdown(html + "</div>", unsafe_allow_html=True)


def hbar_list(dframe, name_col, top=None):
    """시안 TOP 스타일 가로막대(이름 | 트랙+채움 | 금액). 1위=브랜드색, 나머지=연한 블루."""
    d = dframe.sort_values("매출", ascending=False).reset_index(drop=True)
    if top:
        d = d.head(top)
    mx = d["매출"].max() or 1
    html = '<div class="hb-wrap">'
    for i, r in d.iterrows():
        w = max(3, r["매출"] / mx * 100)
        col = BRAND if i == 0 else "#bcd3fb"
        html += (f'<div class="hb"><span class="hb-n">{r[name_col]}</span>'
                 f'<span class="hb-track"><i style="width:{w:.0f}%;background:{col}"></i></span>'
                 f'<span class="hb-v">{fmt_krw(r["매출"])}</span></div>')
    st.markdown(html + "</div>", unsafe_allow_html=True)


def rank_table(dframe, name_col, top=None):
    """비중막대 내장 순위표(.ntbl)."""
    d = dframe.sort_values("매출", ascending=False).reset_index(drop=True)
    if top:
        d = d.head(top)
    tot = d["매출"].sum()
    mx = (d["매출"] / tot).max() if tot else 1.0
    grid = "grid-template-columns:36px 1.7fr 1.2fr 1.5fr"
    html = (f'<div class="ntbl"><div class="ntr nth" style="{grid}">'
            '<span>#</span><span>이름</span><span class="r">매출</span><span>비중</span></div>')
    for i, r in d.iterrows():
        frac = (r["매출"] / tot) if tot else 0
        rk = f'<span class="rk {"top" if i == 0 else ""}">{i + 1}</span>'
        html += (f'<div class="ntr" style="{grid}">{rk}'
                 f'<span class="nname">{r[name_col]}</span>'
                 f'<span class="r num">{fmt_krw(r["매출"])}</span>{pct_bar(frac, mx)}</div>')
    st.markdown(html + "</div>", unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════
#  데이터 로드
# ══════════════════════════════════════════════════════════════
df_all = load_data()
ex_rates = load_exchange_rates()

st.title("📊 스내피즘 매출 대시보드")
st.caption("기간·국가·매장·상품·IP를 골라 매출을 봐요. 매출 = 실결제(쿠폰 제외) 기준이에요.")

if df_all.empty:
    st.warning("아직 불러온 매출 데이터가 없어요. `raw` 폴더에 CSV를 넣고 `데이터추가.bat`을 실행한 뒤 새로고침해 주세요.")
    st.stop()

last_date = df_all["날짜"].max()
first_date = df_all["날짜"].min()

# ══════════════════════════════════════════════════════════════
#  사이드바: 필터(멀티셀렉트)
# ══════════════════════════════════════════════════════════════
KNOWN_COUNTRIES = ["대한민국", "일본", "중국", "대만", "인도네시아", "홍콩", "태국", "말레이시아"]


def _uniq(col):
    if col not in df_all.columns:
        return []
    s = df_all[col].dropna().astype(str).str.strip()
    return sorted([v for v in s.unique() if v and v != "nan"])


_country_opts = sorted(set(_uniq("국가")) | set(KNOWN_COUNTRIES)) if "국가" in df_all.columns else []

# ── 인라인 필터바 (시안 상단 칩) ──
st.markdown('<div class="fbar-label">🔎 필터 — 여러 개 고르면 그 값들로 전 화면이 좁혀져요 (안 고르면 전체)</div>',
            unsafe_allow_html=True)
default_start = max(last_date - timedelta(days=29), first_date)
_fb = st.columns([1.6, 1, 1, 1, 1])
with _fb[0]:
    date_range = st.date_input("기간", value=[default_start, last_date],
                               min_value=first_date, max_value=last_date, label_visibility="collapsed")


def _fpop(col, label, key, options):
    prev = [v for v in st.session_state.get(key, []) if v in options]
    cap = "전체" if not prev else (prev[0] if len(prev) == 1 else f"{len(prev)}개")
    with col.popover(f"{label} · {cap}", use_container_width=True):
        return st.multiselect(label, options, key=key, placeholder="전체 (선택 안 함)")


sel_country = _fpop(_fb[1], "국가", "f_country", _country_opts)
sel_store = _fpop(_fb[2], "매장", "f_store", _uniq("매장 이름"))
sel_prod = _fpop(_fb[3], "상품", "f_prod", _uniq("상품 카테고리"))
sel_ip = _fpop(_fb[4], "IP", "f_ip", _uniq("프레임 이름"))

_cfg = load_config()

# ── 필터 적용 ──
df = df_all.copy()
if len(date_range) == 2:
    df = df[(df["날짜"] >= date_range[0]) & (df["날짜"] <= date_range[1])]
if sel_country and "국가" in df.columns:
    df = df[df["국가"].isin(sel_country)]
if sel_store:
    df = df[df["매장 이름"].isin(sel_store)]
if sel_prod:
    df = df[df["상품 카테고리"].isin(sel_prod)]
if sel_ip:
    df = df[df["프레임 이름"].isin(sel_ip)]

sales = paid_sales(df)
coupons = coupon_txns(df)
cpn_all = pd.concat([coupons, sales[sales["쿠폰 할인 금액"] > 0]])

# ══════════════════════════════════════════════════════════════
#  KPI 3카드 + 범위 배너
# ══════════════════════════════════════════════════════════════
_period_days = ((date_range[1] - date_range[0]).days + 1) if len(date_range) == 2 else "-"
_dr = (f"{date_range[0]} ~ {date_range[1]}" if len(date_range) == 2 else "전체")
rev_real = int(sales["KRW환산금액"].sum())
cpn_krw = int(cpn_all["쿠폰KRW"].sum())
cpn_cnt = int(len(cpn_all))
cancel_krw = int(df[df["취소 여부"]]["KRW환산금액"].sum())

st.markdown(
    '<div class="kpis">'
    f'<div class="kpi hero"><div class="l">조회기간 매출 (합계)</div>'
    f'<div class="v num">{fmt_krw(rev_real)}</div>'
    f'<div class="d">{_dr} · {_period_days}일 · 실결제 기준</div></div>'
    f'<div class="kpi"><div class="l">쿠폰 매출 (할인)</div>'
    f'<div class="v num">{fmt_krw(cpn_krw)}</div><div class="d">{cpn_cnt:,}건</div></div>'
    f'<div class="kpi"><div class="l">취소 매출</div>'
    f'<div class="v num">{fmt_krw(cancel_krw)}</div><div class="d">환불·취소분</div></div>'
    '</div>', unsafe_allow_html=True)

_scope_bits = []
if sel_country:
    _scope_bits.append("국가: " + " · ".join(sel_country))
if sel_store:
    _scope_bits.append("매장: " + " · ".join(sel_store[:4]) + (" 외" if len(sel_store) > 4 else ""))
if sel_prod:
    _scope_bits.append("상품: " + " · ".join(sel_prod))
if sel_ip:
    _scope_bits.append("IP: " + " · ".join(sel_ip[:4]) + (" 외" if len(sel_ip) > 4 else ""))
if _scope_bits:
    st.markdown('<div class="scope">🌐 범위 — ' + "  |  ".join(_scope_bits) + '</div>', unsafe_allow_html=True)

# ══════════════════════════════════════════════════════════════
#  사이드바: 이번 달 변화 (국가별) — 전월 동기(1일~같은날) 기준
# ══════════════════════════════════════════════════════════════
st.sidebar.divider()
st.sidebar.subheader("🔺 이번 달 변화")
_mv_country = st.sidebar.selectbox("국가별로 보기", ["전체"] + _country_opts, key="mv_country")
st.sidebar.caption("전월 같은 기간(1일~오늘) 대비예요.")

_today = date.today()
_mstart = _today.replace(day=1)
_py = _today.year if _today.month > 1 else _today.year - 1
_pm = _today.month - 1 if _today.month > 1 else 12
_pmstart = date(_py, _pm, 1)
_pdays = pd.Period(f"{_py}-{_pm:02d}", freq="M").days_in_month
_psame_end = date(_py, _pm, min(_today.day, _pdays))

if _mv_country != "전체" and "국가" in df_all.columns:
    _mv_src = df_all[df_all["국가"] == _mv_country]
else:
    _mv_src = df_all
_cur_m = paid_sales(_mv_src[(_mv_src["날짜"] >= _mstart) & (_mv_src["날짜"] <= _today)])
_prev_m = paid_sales(_mv_src[(_mv_src["날짜"] >= _pmstart) & (_mv_src["날짜"] <= _psame_end)])


def _movers(dim):
    a = _cur_m.groupby(dim)["KRW환산금액"].sum()
    b = _prev_m.groupby(dim)["KRW환산금액"].sum()
    idx = a.index.union(b.index)
    a = a.reindex(idx, fill_value=0)
    b = b.reindex(idx, fill_value=0)
    out = []
    for name in idx:
        name_s = str(name).strip()
        if not name_s or name_s == "nan":
            continue
        cur_v, prev_v = int(a[name]), int(b[name])
        if prev_v <= 0 and cur_v <= 0:
            continue
        pct = 100.0 if prev_v <= 0 else (cur_v - prev_v) / prev_v * 100
        out.append((name_s, pct))
    return out


_mv = _movers("프레임 이름")
_up = sorted([m for m in _mv if m[1] > 0], key=lambda x: -x[1])[:5]
_down = sorted([m for m in _mv if m[1] < 0], key=lambda x: x[1])[:5]


def _mv_rows(items, cls):
    if not items:
        return '<div class="mv" style="color:#98a0af">해당 없음</div>'
    r = ""
    for name, pct in items:
        nm = name if len(name) <= 12 else name[:11] + "…"
        r += (f'<div class="mv"><span class="t">IP</span><span class="n">{nm}</span>'
              f'<span class="p {cls}">{pct:+.0f}%</span></div>')
    return r


st.sidebar.markdown('<div style="font-size:12px;font-weight:700;color:#15803d;margin:6px 0 2px">▲ 오른 IP</div>'
                    + _mv_rows(_up, "up"), unsafe_allow_html=True)
st.sidebar.markdown('<div style="font-size:12px;font-weight:700;color:#c0322b;margin:10px 0 2px">▼ 내린 IP</div>'
                    + _mv_rows(_down, "down"), unsafe_allow_html=True)

st.sidebar.divider()
st.sidebar.caption(f"💱 실시간 환율{'  ·  ' + _cfg.get('rates_updated', '') if _cfg.get('rates_updated') else ''}")
for _cur, _rate in ex_rates.items():
    if _cur != "KRW":
        st.sidebar.caption(f"  1 {_cur} = ₩{_rate:,.2f}")

# ══════════════════════════════════════════════════════════════
#  탭 5개
# ══════════════════════════════════════════════════════════════
tab_home, tab_cat, tab_nat, tab_store, tab_etc = st.tabs([
    "📊 매출 한눈에", "🧩 상품 카테고리 분석", "🌏 국가별 분석", "🏬 매장별 분석", "⏰ 시간대 · 데이터",
])

# ════════════ 탭 1: 매출 한눈에 ════════════
with tab_home:
    sec("1", "매출 동향", "잘 가고 있나? — 기간별 실결제·쿠폰 흐름")
    with card("📈 매출 추이"):
        _, _h2 = st.columns([3, 1])
        with _h2:
            gran = st.segmented_control("기간", ["월", "주", "일"], default="월",
                                        key="trend_gran", label_visibility="collapsed") or "월"

        def _pkey(dates, g):
            d = pd.to_datetime(dates)
            return d.dt.to_period("M") if g == "월" else (d.dt.to_period("W") if g == "주" else d.dt.date)

        s_paid = sales.assign(_p=_pkey(sales["날짜"], gran)).groupby("_p")["KRW환산금액"].sum().rename("실결제")
        _cp = cpn_all.assign(_p=_pkey(cpn_all["날짜"], gran)).groupby("_p")["쿠폰KRW"].sum().rename("쿠폰")
        trend = pd.concat([s_paid, _cp], axis=1).fillna(0).sort_index()
        if trend.empty:
            st.info("선택한 조건에 맞는 데이터가 없어요. 기간·필터를 바꿔 보세요.")
        else:
            trend = trend.reset_index()
            if gran == "월":
                trend["label"] = trend["_p"].apply(lambda p: f"{p.year}.{p.month:02d}")
            elif gran == "주":
                trend["label"] = trend["_p"].apply(lambda p: p.start_time.strftime("%m/%d") + "주")
            else:
                trend["label"] = trend["_p"].astype(str)
            fig = go.Figure()
            fig.add_trace(go.Bar(x=trend["label"], y=trend["실결제"], name="실결제",
                                 marker_color=BRAND2, hovertemplate="%{x}<br>실결제 %{y:,}원<extra></extra>"))
            fig.add_trace(go.Bar(x=trend["label"], y=trend["쿠폰"], name="쿠폰 할인",
                                 marker_color=SKY, hovertemplate="%{x}<br>쿠폰 %{y:,}원<extra></extra>"))
            fig.update_layout(barmode="stack", yaxis_tickformat=",", bargap=0.55, barcornerradius=6)
            style_fig(fig, 320)
            fig.update_xaxes(type="category")
            st.plotly_chart(fig, use_container_width=True, key="ch_trend")

    sec("2", "무엇이 매출을 만드나", "비중 — 어떤 상품·종류가 매출을 끄나")
    _c1, _c2 = st.columns(2)
    with _c1:
        with card("🧩 상품 카테고리 비중"):
            pc = (sales.groupby("상품 카테고리")["KRW환산금액"].sum().rename("매출")
                  .reset_index().sort_values("매출", ascending=False))
            if pc["매출"].sum() > 0:
                _dd, _ll = st.columns([1, 1])
                with _dd:
                    st.plotly_chart(donut(pc, "상품 카테고리", "매출", showlegend=False),
                                    use_container_width=True, key="ch_home_prodcat")
                with _ll:
                    legend_list(pc, "상품 카테고리")
            else:
                st.info("데이터가 없어요.")
    with _c2:
        with card("🎨 아티스트/캐릭터 비중"):
            _s = sales.assign(_c=cat3(sales["카테고리"]))
            ac = (_s.groupby("_c")["KRW환산금액"].sum().rename("매출").reset_index()
                  .sort_values("매출", ascending=False))
            ac = ac[ac["매출"] > 0]
            if not ac.empty:
                _dd, _ll = st.columns([1, 1])
                with _dd:
                    st.plotly_chart(donut(ac, "_c", "매출", showlegend=False),
                                    use_container_width=True, key="ch_home_ac")
                with _ll:
                    legend_list(ac, "_c")
                _m = {r["_c"]: int(r["매출"]) for _, r in ac.iterrows()}
                st.caption("아티스트 " + fmt_krw(_m.get("아티스트", 0)) + " · 캐릭터 " + fmt_krw(_m.get("캐릭터", 0)))
            else:
                st.info("데이터가 없어요.")

    with card("🖼 카테고리별 TOP 프레임(IP)"):
        _fsrc = sales[sales["프레임 이름"].astype(str).str.strip().replace("nan", "").ne("")]
        fr = _fsrc.groupby("프레임 이름")["KRW환산금액"].sum().rename("매출").reset_index()
        fr = fr[fr["매출"] > 0]
        if not fr.empty:
            hbar_list(fr, "프레임 이름", top=5)
        else:
            st.info("프레임 데이터가 없어요.")

    sec("3", "어디서 파나", "지역 — 국가·매장별 매출 (원화 기준)")
    _n1, _n2 = st.columns(2)
    with _n1:
        with card("🌏 국가별 매출 TOP 6"):
            nat6 = (sales.groupby("국가")["KRW환산금액"].sum().rename("매출").reset_index()
                    .sort_values("매출", ascending=False).head(6)) if "국가" in sales.columns else pd.DataFrame()
            if not nat6.empty and nat6["매출"].sum() > 0:
                figb = px.bar(nat6.sort_values("매출"), x="매출", y="국가", orientation="h",
                              color_discrete_sequence=[BRAND2])
                figb.update_traces(hovertemplate="%{y}<br>%{x:,}원<extra></extra>")
                figb.update_layout(xaxis_tickformat=",", yaxis_title="", bargap=0.45)
                style_fig(figb, 300, legend=False)
                st.plotly_chart(figb, use_container_width=True, key="ch_home_nat6")
            else:
                st.info("데이터가 없어요.")
    with _n2:
        with card("🏬 국가별 매출 TOP 5 매장"):
            _opts = (sales.groupby("국가")["KRW환산금액"].sum().sort_values(ascending=False).index.tolist()
                     if "국가" in sales.columns else [])
            if _opts:
                _pick = st.selectbox("국가 선택", _opts, key="home_store_country", label_visibility="collapsed")
                _ss = (sales[sales["국가"] == _pick].groupby("매장 이름")["KRW환산금액"].sum().rename("매출")
                       .reset_index().sort_values("매출", ascending=False).head(5))
                if not _ss.empty:
                    rank_table(_ss, "매장 이름")
                else:
                    st.info("이 국가의 매장 데이터가 없어요.")
            else:
                st.info("데이터가 없어요.")
    st.caption("※ 여긴 요약(TOP)이에요. 전체 순위는 '상품 카테고리 분석'·'매장별 분석' 탭에서 봐요.")

# ════════════ 탭 2: 상품 카테고리 분석 (상세, 전체) ════════════
with tab_cat:
    with card("🎨 아티스트/캐릭터 · 프레임(IP) 전체 순위"):
        _s = sales.assign(_c=cat3(sales["카테고리"]))
        _ta, _ = st.columns([3, 7])
        with _ta:
            _tog = st.segmented_control("구분", ["전체", "아티스트", "캐릭터"], default="전체",
                                        key="cat_frame_tog", label_visibility="collapsed") or "전체"
        _fs = _s if _tog == "전체" else _s[_s["_c"] == _tog]
        fr_all = (_fs[_fs["프레임 이름"].astype(str).str.strip().replace("nan", "").ne("")]
                  .groupby("프레임 이름")["KRW환산금액"].sum().rename("매출").reset_index())
        fr_all = fr_all[fr_all["매출"] > 0]
        _d1, _d2 = st.columns([5, 5])
        with _d1:
            ac = _s.groupby("_c")["KRW환산금액"].sum().rename("매출").reset_index()
            ac = ac[ac["매출"] > 0]
            if not ac.empty:
                st.plotly_chart(donut(ac, "_c", "매출", height=240), use_container_width=True, key="ch_cat_ac")
        with _d2:
            st.caption(f"프레임(IP) {len(fr_all):,}개 · 매출순 전체")
            if not fr_all.empty:
                rank_table(fr_all, "프레임 이름")
            else:
                st.info("데이터가 없어요.")

    with card("🧩 상품 카테고리 (비중 · 매출)"):
        pc = (sales.groupby("상품 카테고리")["KRW환산금액"].sum().rename("매출")
              .reset_index().sort_values("매출", ascending=False))
        _p1, _p2 = st.columns([5, 5])
        with _p1:
            if pc["매출"].sum() > 0:
                st.plotly_chart(donut(pc, "상품 카테고리", "매출", height=250), use_container_width=True, key="ch_cat_prodcat")
        with _p2:
            if not pc.empty:
                rank_table(pc, "상품 카테고리")
            else:
                st.info("데이터가 없어요.")

    @st.fragment
    def _prod_rank():
        with card("📦 카테고리별 상품 순위"):
            cats = [c for c in sorted(sales["상품 카테고리"].dropna().astype(str).unique().tolist())
                    if c and c != "nan"]
            if not cats:
                st.info("데이터가 없어요.")
                return
            _d = "미니스티커" if "미니스티커" in cats else cats[0]
            _ca, _ = st.columns([3, 7])
            with _ca:
                pick = st.selectbox("카테고리", cats, index=cats.index(_d),
                                    key="prod_rank_pick", label_visibility="collapsed")
            pr = (sales[sales["상품 카테고리"] == pick].groupby("상품 이름")["KRW환산금액"].sum()
                  .rename("매출").reset_index())
            pr = pr[pr["매출"] > 0]
            if pr.empty:
                st.info("이 카테고리에는 데이터가 없어요.")
            else:
                rank_table(pr, "상품 이름")

    _prod_rank()

# ════════════ 탭 3: 국가별 분석 (상세, 전체) ════════════
with tab_nat:
    if "국가" not in sales.columns or sales.empty:
        st.info("국가 데이터가 없어요.")
    else:
        nat = (pd.concat([sales, coupons]).groupby(["국가", "결제 단위"])
               .agg(건수=("KRW환산금액", "count"), 현지=("총원화금액", "sum"), 매출=("KRW환산금액", "sum"))
               .reset_index())
        nat = nat[nat["매출"] > 0].sort_values("매출", ascending=False)
        tot = nat["매출"].sum()
        mx = (nat["매출"] / tot).max() if tot else 1.0

        with card("🌏 국가별 매출"):
            grid = "grid-template-columns:1.4fr .7fr .8fr 1.3fr 1.3fr 1.5fr"
            html = (f'<div class="ntbl"><div class="ntr nth" style="{grid}">'
                    '<span>국가</span><span class="c">통화</span><span class="r">건수</span>'
                    '<span class="r">현지 매출</span><span class="r">KRW 매출</span><span>비중</span></div>')
            for _, r in nat.iterrows():
                frac = (r["매출"] / tot) if tot else 0
                html += (f'<div class="ntr" style="{grid}">'
                         f'<span class="nname">{flag_img(r["국가"])}{r["국가"]}</span>'
                         f'<span class="c"><span class="cur">{r["결제 단위"]}</span></span>'
                         f'<span class="r num">{int(r["건수"]):,}</span>'
                         f'<span class="r num">{fmt_orig(r["현지"], r["결제 단위"])}</span>'
                         f'<span class="r num">{fmt_krw(r["매출"])}</span>{pct_bar(frac, mx)}</div>')
            st.markdown(html + "</div>", unsafe_allow_html=True)

        with card("🍩 국가별 매출 비중"):
            _pie = nat[["국가", "매출"]].copy()
            if len(_pie) > 7:
                _pie = pd.concat([_pie.head(7), pd.DataFrame(
                    [{"국가": f"기타 {len(nat) - 7}개국", "매출": int(nat.iloc[7:]["매출"].sum())}])],
                    ignore_index=True)
            fig = px.pie(_pie, names="국가", values="매출", hole=0.5, color_discrete_sequence=PAL)
            fig.update_traces(sort=False, textposition="inside", texttemplate="%{percent}",
                              hovertemplate="%{label}<br>%{value:,}원 (%{percent})<extra></extra>")
            style_fig(fig, 340)
            fig.update_layout(legend=dict(orientation="h", y=-0.05, x=0.5, xanchor="center", font_size=10))
            st.plotly_chart(fig, use_container_width=True, key="ch_nat_pie")

        cpn_by = pd.concat([coupons, sales[sales["쿠폰 할인 금액"] > 0]])
        if not cpn_by.empty:
            st.info(f"🎟 쿠폰 총 할인 **{fmt_krw(int(cpn_by['쿠폰KRW'].sum()))}**  ·  {len(cpn_by):,}건")

# ════════════ 탭 4: 매장별 분석 (상세, 전체) ════════════
with tab_store:
    with card("🏬 국가별 매장 전체 순위"):
        _opts = (sales.groupby("국가")["KRW환산금액"].sum().sort_values(ascending=False).index.tolist()
                 if "국가" in sales.columns else [])
        if not _opts:
            st.info("데이터가 없어요.")
        else:
            _ca, _ = st.columns([3, 7])
            with _ca:
                pick = st.selectbox("국가", ["전체"] + _opts, key="store_country", label_visibility="collapsed")
            _src = sales if pick == "전체" else sales[sales["국가"] == pick]
            ss = _src.groupby("매장 이름")["KRW환산금액"].sum().rename("매출").reset_index()
            ss = ss[ss["매출"] > 0]
            st.caption(f"매장 {len(ss):,}개 · 매출순 전체" + ("" if pick == "전체" else f" · {pick}"))
            if ss.empty:
                st.info("이 국가의 매장 데이터가 없어요.")
            else:
                rank_table(ss, "매장 이름")

# ════════════ 탭 5: 시간대 · 데이터 ════════════
with tab_etc:
    with card("⏰ 시간대별 매출 분포"):
        hourly = (sales.assign(시간대=sales["결제일시"].dt.hour).groupby("시간대")["KRW환산금액"].sum()
                  .reindex(range(24), fill_value=0).reset_index()
                  .rename(columns={"시간대": "시간", "KRW환산금액": "매출"}))
        hourly["label"] = hourly["시간"].apply(lambda h: f"{h:02d}:00")
        _peak = hourly["매출"].max()
        hourly["clr"] = hourly["매출"].apply(lambda v: BRAND if (v == _peak and _peak > 0) else "#c7cbf5")
        fig = go.Figure(go.Bar(x=hourly["label"], y=hourly["매출"], marker_color=hourly["clr"],
                               hovertemplate="%{x}<br>%{y:,}원<extra></extra>"))
        fig.update_layout(yaxis_tickformat=",", xaxis_title="")
        style_fig(fig, 260, legend=False)
        st.plotly_chart(fig, use_container_width=True, key="ch_hourly")

    with st.expander("🗃 원본 데이터 보기 / 내려받기"):
        cols = ["날짜", "결제일시", "국가", "매장 이름", "상품 카테고리", "상품 이름",
                "상품 단가", "쿠폰 할인 금액", "최종 결제 금액", "결제 단위",
                "KRW환산금액", "결제 수단", "프레임 이름", "카테고리", "취소 여부"]
        avail = [c for c in cols if c in df.columns]
        st.dataframe(df[avail].sort_values("결제일시", ascending=False).reset_index(drop=True),
                     use_container_width=True, height=400)
        st.download_button("CSV 다운로드",
                           df[avail].to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig"),
                           "snapism_filtered.csv", "text/csv")
