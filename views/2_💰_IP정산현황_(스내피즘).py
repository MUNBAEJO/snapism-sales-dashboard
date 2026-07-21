import json
import re
import sys
import streamlit as st
import pandas as pd
import plotly.express as px
from pathlib import Path
from datetime import date, timedelta

sys.path.insert(0, str(Path(__file__).parent.parent))
from jira_client import fetch_rs_data
from update_rates import get_rates_for_date, get_effective_date

# set_page_config 는 라우터(스내피즘.py)에서 처리
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from guide_content import render_guide
import data_io

BASE_DIR    = Path(__file__).parent.parent
MASTER      = BASE_DIR / "data" / "master.csv"
CONFIG      = BASE_DIR / "config.json"
MAPPING_FILE = BASE_DIR / "data" / "frame_mapping.json"   # 수동 저장 매핑

CURRENCY_SYMBOLS = {
    "KRW": "₩", "CNY": "¥", "JPY": "¥",
    "IDR": "Rp", "TWD": "NT$", "THB": "฿", "HKD": "HK$", "MYR": "RM",
}

STATUS_COLORS = {
    "송출 중":           "🟢",
    "배포 완료":         "🟢",
    "완료":              "⚫",
    "검수 완료":         "🔵",
    "리소스 업로드 완료": "🔵",
    "진행 중":           "🟡",
    "In Review":         "🟡",
    "TEST 맵핑":         "🟠",
    "할 일":             "⚪",
}

INK = "#1a1a2e"; PRIMARY = "#4361ee"; SECONDARY = "#7209b7"; PINK = "#f72585"
st.markdown(f"""
<style>
html, body, [class*="css"], [data-testid="stAppViewContainer"] {{
    font-family: 'Pretendard', -apple-system, BlinkMacSystemFont,
                 'Segoe UI', 'Malgun Gothic', 'Apple SD Gothic Neo', sans-serif;
}}
[data-testid="stAppViewContainer"] .main .block-container {{
    padding-top: 2.2rem; padding-bottom: 3rem; max-width: 1500px;
}}
h1 {{ font-weight: 800 !important; letter-spacing: -0.5px; color: {INK}; }}
.section-title {{
    font-size: 1.12rem; font-weight: 700; color: {INK};
    margin: 6px 0 12px; padding-left: 12px; border-left: 4px solid {PRIMARY}; line-height: 1.4;
}}
.section-title.purple {{ border-left-color: {SECONDARY}; }}
.section-title.pink   {{ border-left-color: {PINK}; }}
.sub-label {{ font-size: .9rem; font-weight: 600; color: #5a5a72; margin-bottom: 6px; }}
[data-testid="stMetric"], [data-testid="metric-container"] {{
    background: linear-gradient(135deg, #ffffff 0%, #f5f8ff 100%);
    border: 1px solid #e7ecf7; border-radius: 16px; padding: 16px 20px;
    box-shadow: 0 2px 10px rgba(67,97,238,0.06);
    transition: transform .15s ease, box-shadow .15s ease;
}}
[data-testid="stMetric"]:hover, [data-testid="metric-container"]:hover {{
    transform: translateY(-3px); box-shadow: 0 8px 20px rgba(67,97,238,0.14);
}}
[data-testid="stMetricLabel"] p {{ font-weight: 600; color: #6b7280; font-size: .82rem; }}
[data-testid="stMetricValue"] {{ font-weight: 800; color: {INK}; letter-spacing: -0.5px; }}
[data-testid="stMetricDelta"] {{ font-size: 0.82rem; }}
hr {{ margin: 1.4rem 0 1.2rem; border: none; border-top: 1px solid #e9edf5; }}
[data-testid="stDeployButton"] {{ display: none !important; }}
[data-testid="stElementToolbar"] {{ display: none; }}
[data-testid="stSidebar"] {{ background: #fbfcfe; border-right: 1px solid #eceff5; }}
[data-testid="stDataFrame"] {{ border-radius: 12px; overflow: hidden; }}
button[data-baseweb="tab"] p {{ font-size: 1.0rem !important; font-weight: 700 !important; }}
</style>
""", unsafe_allow_html=True)


# ── 헬퍼 ──────────────────────────────────────────────────────
def load_exchange_rates():
    try:
        with open(CONFIG, encoding="utf-8") as f:
            return json.load(f).get("exchange_rates", {"KRW": 1})
    except Exception:
        return {"KRW": 1}


def load_saved_mapping() -> dict:
    """저장된 IP → 프레임 목록 매핑 불러오기"""
    if MAPPING_FILE.exists():
        try:
            with open(MAPPING_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_mapping(ip_name: str, frames: list):
    """IP → 프레임 매핑 저장"""
    mapping = load_saved_mapping()
    mapping[ip_name] = frames
    MAPPING_FILE.parent.mkdir(exist_ok=True)
    with open(MAPPING_FILE, "w", encoding="utf-8") as f:
        json.dump(mapping, f, ensure_ascii=False, indent=2)


@st.cache_data(ttl=900, max_entries=1)   # 파일 버전 키 → 최신 1개만 유효
def _load_sales(_v):
    if not MASTER.exists():
        return pd.DataFrame()
    df = data_io.read_master(MASTER)  # parquet 우선(없으면 csv)
    df["날짜"] = pd.to_datetime(df["날짜"], errors="coerce").dt.date
    df["결제일시"] = pd.to_datetime(df["결제일시"], format="%Y.%m.%d %H:%M", errors="coerce")
    df["취소 여부"] = df["취소 여부"].astype(str).str.lower().isin(["true", "1", "yes"])
    for col in ["최종 결제 금액", "쿠폰 할인 금액"]:
        df[col] = pd.to_numeric(df.get(col, 0), errors="coerce").fillna(0).astype(int)
    ex = load_exchange_rates()
    df["결제 단위"] = df["결제 단위"].fillna("KRW").astype(str).str.strip()
    df["환율"] = df["결제 단위"].map(ex).fillna(1)
    df["KRW환산"] = (df["최종 결제 금액"] * df["환율"]).round(0).astype(int)
    df["쿠폰KRW"]  = (df["쿠폰 할인 금액"]  * df["환율"]).round(0).astype(int)
    df["프레임 이름"] = df["프레임 이름"].astype(str).str.strip().replace("nan", "")
    return df


def load_sales():
    return _load_sales(data_io.file_version(MASTER))


@st.cache_data(ttl=3600, max_entries=1)
def load_jira():
    try:
        return fetch_rs_data()
    except Exception as e:
        return {"_error": str(e)}


def fmt_krw(n):
    return f"₩{int(n):,}"


def fmt_pct(v):
    if v is None:
        return "-"
    return f"{v*100:.1f}%"


def auto_match_frames(ip_name: str, all_frames: list) -> list:
    """IP 이름으로 어드민 프레임 후보 탐색 (대소문자 무시, 부분 포함)"""
    ip_lower = ip_name.lower()
    return [fr for fr in all_frames if ip_lower in fr.lower() or fr.lower() in ip_lower]


# ── 데이터 로드 ────────────────────────────────────────────────
st.title("💰 IP 정산 현황")
render_guide("settle")
st.caption("Snapism 브랜드 IP — 어드민 매출 × Jira RS율 정산")

df_all   = load_sales()
jira_raw = load_jira()

if df_all.empty:
    st.warning("아직 불러올 매출 데이터가 없어요. 어드민에서 매출 CSV를 먼저 올려 주세요.")
    st.stop()

if "_error" in jira_raw:
    st.error(f"Jira 연결 오류: {jira_raw['_error']}")
    st.info("Jira 연결 설정을 확인해 주세요.")
    jira_raw = {}

# ── 프레임 목록 및 저장 매핑 ───────────────────────────────────
all_frames    = sorted(fr for fr in df_all["프레임 이름"].unique() if fr and fr != "nan")
saved_mapping = load_saved_mapping()

# ── Jira IP × 어드민 매칭 테이블 구성 ──────────────────────────
#   우선순위: 저장 매핑 > 자동 매칭
#   어드민에 1개 이상 프레임이 있는 Jira IP만 목록에 표시
matched_jira   = {}   # ip_name → {"entry": ..., "frames": [...], "source": "saved"/"auto"}
unmatched_jira = []

for ip_name, entry in jira_raw.items():
    if ip_name in saved_mapping:
        frames = [f for f in saved_mapping[ip_name] if f in all_frames]
        source = "saved"
    else:
        frames = auto_match_frames(ip_name, all_frames)
        source = "auto"

    if frames:
        matched_jira[ip_name] = {"entry": entry, "frames": frames, "source": source}
    else:
        unmatched_jira.append(ip_name)

jira_covered     = set(fr for v in matched_jira.values() for fr in v["frames"])
unmatched_frames = [fr for fr in all_frames if fr not in jira_covered]

# ── 사이드바 ──────────────────────────────────────────────────
st.sidebar.header("🔍 정산 조건")

first_date    = df_all["날짜"].min()
last_date     = df_all["날짜"].max()
default_start = max(last_date - timedelta(days=29), first_date)
date_range    = st.sidebar.date_input(
    "정산 기간",
    value=[default_start, last_date],
    min_value=first_date, max_value=last_date,
)

st.sidebar.divider()

if not matched_jira:
    st.warning("매출과 연결할 Snapism Jira IP가 아직 없어요. Jira 티켓 이름과 어드민 프레임 이름을 맞춰 주세요.")
    st.stop()

# ── 선택된 프레임 → Jira IP 자동 탐지 ───────────────────────────
ip_options = sorted(matched_jira.keys())

def ip_label(k):
    e      = matched_jira[k]["entry"]
    icon   = STATUS_COLORS.get(e.get("status", ""), "⚪")
    rs_a   = fmt_pct(e["rs_agency"])
    src    = " 💾" if matched_jira[k]["source"] == "saved" else ""
    title  = e.get("title", "")
    title  = re.sub(r"^(?:20)?\d{2}\.\d{2}\s+", "", title).strip()
    title_short = title[:30] + "…" if len(title) > 30 else title
    return f"{icon}{src} {k}  —  {title_short}  [{rs_a}]"

# 현재 선택된 프레임으로 가장 잘 매칭되는 Jira IP 탐색
_cur_frames = st.session_state.get("frames_multiselect", [])
_auto_ip = ip_options[0]
if _cur_frames:
    _best = 0
    for _ip in ip_options:
        _cnt = sum(1 for f in _cur_frames if f in matched_jira[_ip]["frames"])
        if _cnt > _best:
            _best = _cnt
            _auto_ip = _ip
    # 매칭 IP가 바뀌면 expander 선택값도 동기화
    if _best > 0 and st.session_state.get("_auto_detected_ip") != _auto_ip:
        st.session_state["_auto_detected_ip"] = _auto_ip
        st.session_state["jira_ip_select"] = _auto_ip

with st.sidebar.expander("⚙️ Jira IP 연결", expanded=False):
    selected_ip = st.selectbox(
        "IP 선택", options=ip_options, format_func=ip_label,
        key="jira_ip_select",
    )

sel            = matched_jira[selected_ip]
entry          = sel["entry"]
default_frames = sel["frames"]

# ── RS율 입력 (IP 바뀔 때만 Jira 값으로 리셋, 수동 수정 유지) ────
st.sidebar.divider()
st.sidebar.markdown("**📊 R/S율 설정**")
_jira_rs_a = (entry.get("rs_agency") or 0) * 100
_jira_rs_m = (entry.get("rs_mgmt")   or 0) * 100

# IP가 달라질 때만 RS 입력값 초기화
if st.session_state.get("_rs_ip_ref") != selected_ip:
    st.session_state["_rs_ip_ref"]   = selected_ip
    st.session_state["_rs_a_input"]  = float(round(_jira_rs_a, 2))
    st.session_state["_rs_m_input"]  = float(round(_jira_rs_m, 2))

# 세션 스테이트 잔존값이 범위 초과할 수 있으므로 클램핑
for _k in ["_rs_a_input", "_rs_m_input"]:
    if _k in st.session_state:
        st.session_state[_k] = float(min(100.0, max(0.0, st.session_state[_k])))

_rs_a_pct = st.sidebar.number_input(
    "소속사 RS (%)", min_value=0.0, max_value=100.0,
    step=0.1, format="%.2f", key="_rs_a_input",
    help="선택한 프레임의 Jira IP 값이 자동으로 채워져요. 필요하면 직접 고칠 수 있어요.",
)
_rs_m_pct = st.sidebar.number_input(
    "대행사 RS (%)", min_value=0.0, max_value=100.0,
    step=0.1, format="%.2f", key="_rs_m_input",
    help="선택한 프레임의 Jira IP 값이 자동으로 채워져요. 필요하면 직접 고칠 수 있어요.",
)
if _jira_rs_a > 0 or _jira_rs_m > 0:
    st.sidebar.caption(f"Jira 원본: 소속사 {_jira_rs_a:.2f}% / 대행사 {_jira_rs_m:.2f}%")

# ── duedate 기준 환율 결정 (자동 탐지된 IP 기준) ─────────────────
_today_str = date.today().isoformat()
_duedate   = entry.get("duedate")   # YYYY-MM-DD or None

if _duedate and _duedate <= _today_str:
    _rate_base = "duedate"
    _rate_ref  = _duedate
else:
    _rate_base = "today"
    _rate_ref  = _today_str

_eff_date  = get_effective_date(_rate_ref)
_ip_rates  = get_rates_for_date(_eff_date)


# ── 프레임 매핑 섹션 ──────────────────────────────────────────
st.markdown("""
<div style="background:#f0f4ff;border-left:4px solid #4361ee;padding:10px 16px;border-radius:6px;margin-bottom:12px">
<b>🔗 정산 연결 설정</b><br>
<span style="font-size:0.88rem;color:#555">
아래에서 <b>어드민 프레임</b>을 선택하면 해당 IP의 Jira 정보(RS율·종료일)가 자동으로 반영됩니다.
자동 매핑되지만 수동으로 수정 가능합니다.
</span>
</div>
""", unsafe_allow_html=True)

col_map1, col_map2, col_map3 = st.columns([1, 3, 1])
with col_map1:
    src_badge = "💾 저장된 매핑" if sel["source"] == "saved" else "🔍 자동 매핑"
    st.caption(src_badge)
    _dd_disp = entry.get("duedate") or "-"
    st.caption(f"📅 종료일: **{_dd_disp}**")
    st.caption(f"Jira RS: **{fmt_pct(entry['rs_agency'])}** / **{fmt_pct(entry['rs_mgmt'])}**")

with col_map2:
    selected_frames = st.multiselect(
        "어드민 프레임 선택 (매출 데이터 출처)",
        options=all_frames,
        key="frames_multiselect",
        help="여러 프레임을 선택하면 합산해서 정산해요. '매핑 저장'을 누르면 다음번에 자동으로 적용돼요.",
    )

with col_map3:
    st.write("")
    st.write("")
    if st.button("💾 매핑 저장", use_container_width=True):
        save_mapping(selected_ip, selected_frames)
        st.success("매핑을 저장했어요. 다음번 정산에 자동으로 적용돼요.")
        # 매핑 파일은 캐시하지 않으므로(load_mapping 무캐시) 전역 캐시를 비울 필요가 없다.
        # 전역 st.cache_data.clear() 는 다른 사용자의 무거운 매출 캐시까지 날려 제거함.

st.divider()

if not selected_frames:
    st.info("위에서 정산할 프레임을 선택해 주세요.")
    st.stop()

# ── 매출 필터링 ───────────────────────────────────────────────
df = df_all.copy()
if len(date_range) == 2:
    df = df[(df["날짜"] >= date_range[0]) & (df["날짜"] <= date_range[1])]

df_ip = df[df["프레임 이름"].isin(selected_frames)].copy()

# duedate 기준 환율로 KRW환산 재계산
df_ip["환율"] = df_ip["결제 단위"].map(_ip_rates).fillna(1)
df_ip["KRW환산"] = (df_ip["최종 결제 금액"] * df_ip["환율"]).round(0).astype(int)
df_ip["쿠폰KRW"]  = (df_ip["쿠폰 할인 금액"]  * df_ip["환율"]).round(0).astype(int)

paid       = df_ip[~df_ip["취소 여부"] & (df_ip["최종 결제 금액"] > 0)]
coupons    = df_ip[~df_ip["취소 여부"] & (df_ip["최종 결제 금액"] == 0) & (df_ip["쿠폰 할인 금액"] > 0)]
all_coupon = pd.concat([coupons, paid[paid["쿠폰 할인 금액"] > 0]])

rs_agency = _rs_a_pct / 100 if _rs_a_pct > 0 else None
rs_mgmt   = _rs_m_pct / 100 if _rs_m_pct > 0 else None

total_sales_krw   = paid["KRW환산"].sum()
total_coupon_krw  = all_coupon["쿠폰KRW"].sum()
settlement_base   = total_sales_krw + total_coupon_krw
agency_settlement = settlement_base * rs_agency if rs_agency else 0
mgmt_settlement   = settlement_base * rs_mgmt   if rs_mgmt   else 0

# ── KPI 카드 ──────────────────────────────────────────────────
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("매출 합계 (KRW)", fmt_krw(total_sales_krw), f"{len(paid):,}건")
c2.metric("쿠폰 할인 (KRW)", fmt_krw(total_coupon_krw), f"{len(all_coupon):,}건")
c3.metric("정산 기준액", fmt_krw(settlement_base), "매출+쿠폰")
c4.metric(f"소속사 정산 ({fmt_pct(rs_agency)})", fmt_krw(agency_settlement))
c5.metric(f"대행사 정산 ({fmt_pct(rs_mgmt)})", fmt_krw(mgmt_settlement))

# 환율 기준 안내
_rate_icon = "📅" if _rate_base == "duedate" else "📆"
_rate_label = f"종료일 기준 ({_duedate} → 적용일 {_eff_date})" if _rate_base == "duedate" else f"오늘 기준 ({_eff_date})"
_rate_detail = "  |  ".join(
    f"1 {cur} = {rate:,.2f} KRW"
    for cur, rate in _ip_rates.items()
    if cur != "KRW"
)
st.caption(f"{_rate_icon} 환율 기준: **{_rate_label}**   {_rate_detail if _rate_detail else ''}")

st.divider()

# ── 국가별 정산 테이블 ─────────────────────────────────────────
with st.container(border=True):
    st.markdown('<div class="section-title">🌏 국가별 정산 내역</div>', unsafe_allow_html=True)

    if paid.empty and coupons.empty:
        st.warning("이 기간에는 선택한 프레임의 매출이 없어요. 정산 기간이나 프레임을 바꿔 보세요.")
    else:
        if not paid.empty:
            nat_paid = (
                paid.groupby(["국가", "결제 단위"])
                .agg(
                    결제건수=("최종 결제 금액", "count"),
                    원화매출=("최종 결제 금액", "sum"),
                    KRW매출=("KRW환산", "sum"),
                    쿠폰건수=("쿠폰 할인 금액", lambda x: (x > 0).sum()),
                    원화쿠폰=("쿠폰 할인 금액", "sum"),
                    쿠폰KRW=("쿠폰KRW", "sum"),
                )
                .reset_index()
            )
        else:
            nat_paid = pd.DataFrame(
                columns=["국가", "결제 단위", "결제건수", "원화매출", "KRW매출", "쿠폰건수", "원화쿠폰", "쿠폰KRW"]
            )

        if not coupons.empty:
            nat_cpn = (
                coupons.groupby(["국가", "결제 단위"])
                .agg(
                    결제건수=("쿠폰 할인 금액", lambda x: 0),
                    원화매출=("최종 결제 금액", "sum"),
                    KRW매출=("KRW환산", "sum"),
                    쿠폰건수=("쿠폰 할인 금액", "count"),
                    원화쿠폰=("쿠폰 할인 금액", "sum"),
                    쿠폰KRW=("쿠폰KRW", "sum"),
                )
                .reset_index()
            )
            nat = pd.concat([nat_paid, nat_cpn], ignore_index=True)
            nat = nat.groupby(["국가", "결제 단위"], as_index=False).sum()
        else:
            nat = nat_paid

        nat = nat.sort_values("KRW매출", ascending=False).reset_index(drop=True)
        nat["정산기준KRW"] = nat["KRW매출"] + nat["쿠폰KRW"]
        nat["소속사정산"] = (nat["정산기준KRW"] * (rs_agency or 0)).round(0).astype(int)
        nat["대행사정산"] = (nat["정산기준KRW"] * (rs_mgmt   or 0)).round(0).astype(int)

        def orig_fmt(r, col):
            sym = CURRENCY_SYMBOLS.get(r["결제 단위"], r["결제 단위"])
            return f"{sym}{int(r[col]):,}"

        nat["매출(원화)"]    = nat.apply(lambda r: orig_fmt(r, "원화매출"), axis=1)
        nat["매출(KRW)"]    = nat["KRW매출"].apply(fmt_krw)
        nat["쿠폰(원화)"]    = nat.apply(lambda r: orig_fmt(r, "원화쿠폰"), axis=1)
        nat["쿠폰(KRW)"]    = nat["쿠폰KRW"].apply(fmt_krw)
        nat["정산기준(KRW)"] = nat["정산기준KRW"].apply(fmt_krw)
        nat["소속사 정산액"]  = nat["소속사정산"].apply(fmt_krw)
        nat["대행사 정산액"]  = nat["대행사정산"].apply(fmt_krw)

        display_cols = [
            "국가", "결제 단위", "결제건수", "매출(원화)", "매출(KRW)",
            "쿠폰건수", "쿠폰(원화)", "쿠폰(KRW)",
            "정산기준(KRW)", "소속사 정산액", "대행사 정산액",
        ]
        st.dataframe(nat[display_cols], use_container_width=True, hide_index=True)

        total_row = {
            "결제건수": nat["결제건수"].sum(),
            "매출(KRW)": fmt_krw(nat["KRW매출"].sum()),
            "쿠폰건수": nat["쿠폰건수"].sum(),
            "쿠폰(KRW)": fmt_krw(nat["쿠폰KRW"].sum()),
            "정산기준(KRW)": fmt_krw(nat["정산기준KRW"].sum()),
            "소속사 정산액": fmt_krw(nat["소속사정산"].sum()),
            "대행사 정산액": fmt_krw(nat["대행사정산"].sum()),
        }
        st.markdown(
            f"**합계** | 결제 {total_row['결제건수']:,}건 | "
            f"매출 {total_row['매출(KRW)']} | 쿠폰 {total_row['쿠폰(KRW)']} | "
            f"정산기준 **{total_row['정산기준(KRW)']}** | "
            f"소속사 **{total_row['소속사 정산액']}** | "
            f"대행사 **{total_row['대행사 정산액']}**"
        )

        st.divider()
        st.markdown('<div class="section-title">국가별 정산 기준액 비교</div>', unsafe_allow_html=True)
        fig = px.bar(
            nat.sort_values("정산기준KRW"),
            x="정산기준KRW", y="국가", orientation="h",
            color="정산기준KRW", color_continuous_scale="Teal",
            custom_data=["소속사 정산액", "대행사 정산액"],
        )
        fig.update_traces(
            hovertemplate="%{y}<br>정산기준: %{x:,}원<br>소속사: %{customdata[0]}<br>대행사: %{customdata[1]}<extra></extra>"
        )
        fig.update_layout(
            height=max(200, len(nat) * 50),
            coloraxis_showscale=False,
            xaxis_tickformat=",", yaxis_title="",
            margin=dict(t=10, b=0),
        )
        st.plotly_chart(fig, use_container_width=True)

    # ── 상품 카테고리별 정산 ───────────────────────────────────────
st.divider()
with st.container(border=True):
    st.markdown('<div class="section-title">📦 상품 카테고리별 정산</div>', unsafe_allow_html=True)

    if not paid.empty and "상품 카테고리" in paid.columns:
        cat_paid = (
            paid.groupby("상품 카테고리")
            .agg(결제건수=("KRW환산","count"), 매출KRW=("KRW환산","sum"), 쿠폰KRW=("쿠폰KRW","sum"))
            .reset_index()
        )
        # 100% 쿠폰 건도 합산
        if not coupons.empty and "상품 카테고리" in coupons.columns:
            cat_cpn = (
                coupons.groupby("상품 카테고리")
                .agg(결제건수=("쿠폰KRW", lambda x: 0), 매출KRW=("KRW환산","sum"), 쿠폰KRW=("쿠폰KRW","sum"))
                .reset_index()
            )
            cat_df = pd.concat([cat_paid, cat_cpn], ignore_index=True)
            cat_df = cat_df.groupby("상품 카테고리", as_index=False).sum()
        else:
            cat_df = cat_paid

        cat_df["정산기준KRW"] = cat_df["매출KRW"] + cat_df["쿠폰KRW"]
        cat_df["소속사정산"]  = (cat_df["정산기준KRW"] * (rs_agency or 0)).round(0).astype(int)
        cat_df["대행사정산"]  = (cat_df["정산기준KRW"] * (rs_mgmt   or 0)).round(0).astype(int)
        cat_df = cat_df.sort_values("정산기준KRW", ascending=False).reset_index(drop=True)

        cat_display = cat_df.copy()
        cat_display["매출(KRW)"]    = cat_display["매출KRW"].apply(fmt_krw)
        cat_display["쿠폰(KRW)"]    = cat_display["쿠폰KRW"].apply(fmt_krw)
        cat_display["정산기준(KRW)"] = cat_display["정산기준KRW"].apply(fmt_krw)
        cat_display["소속사 정산액"]  = cat_display["소속사정산"].apply(fmt_krw)
        cat_display["대행사 정산액"]  = cat_display["대행사정산"].apply(fmt_krw)

        # 합계 행 추가
        total = pd.DataFrame([{
            "상품 카테고리": "합계",
            "결제건수": cat_df["결제건수"].sum(),
            "매출(KRW)": fmt_krw(cat_df["매출KRW"].sum()),
            "쿠폰(KRW)": fmt_krw(cat_df["쿠폰KRW"].sum()),
            "정산기준(KRW)": fmt_krw(cat_df["정산기준KRW"].sum()),
            "소속사 정산액": fmt_krw(cat_df["소속사정산"].sum()),
            "대행사 정산액": fmt_krw(cat_df["대행사정산"].sum()),
        }])
        cat_show = pd.concat(
            [cat_display[["상품 카테고리","결제건수","매출(KRW)","쿠폰(KRW)","정산기준(KRW)","소속사 정산액","대행사 정산액"]], total],
            ignore_index=True,
        )
        st.dataframe(cat_show, use_container_width=True, hide_index=True)

        # 카테고리별 바 차트
        fig_cat = px.bar(
            cat_df.sort_values("정산기준KRW"),
            x="정산기준KRW", y="상품 카테고리", orientation="h",
            color="정산기준KRW", color_continuous_scale="Blues",
            text="결제건수",
        )
        fig_cat.update_traces(texttemplate="%{text}건", textposition="inside",
                              hovertemplate="%{y}<br>정산기준: %{x:,}원<extra></extra>")
        fig_cat.update_layout(height=max(180, len(cat_df)*50), coloraxis_showscale=False,
                              xaxis_tickformat=",", yaxis_title="", margin=dict(t=10,b=0))
        st.plotly_chart(fig_cat, use_container_width=True)
    else:
        st.caption("이 기간에는 상품 카테고리별 매출이 없어요.")

    # ── 상품 이름별 정산 ────────────────────────────────────────────
st.divider()
with st.container(border=True):
    st.markdown('<div class="section-title">🏷 상품 이름별 정산</div>', unsafe_allow_html=True)

    if not paid.empty and "상품 이름" in paid.columns:
        name_df = (
            paid.groupby(["상품 이름", "상품 카테고리"] if "상품 카테고리" in paid.columns else ["상품 이름"])
            .agg(결제건수=("KRW환산","count"), 매출KRW=("KRW환산","sum"), 쿠폰KRW=("쿠폰KRW","sum"))
            .reset_index()
            .sort_values("매출KRW", ascending=False)
            .reset_index(drop=True)
        )
        name_df["정산기준KRW"] = name_df["매출KRW"] + name_df["쿠폰KRW"]
        name_df["소속사정산"]  = (name_df["정산기준KRW"] * (rs_agency or 0)).round(0).astype(int)
        name_df["대행사정산"]  = (name_df["정산기준KRW"] * (rs_mgmt   or 0)).round(0).astype(int)

        name_display = name_df.copy()
        name_display["매출(KRW)"]    = name_display["매출KRW"].apply(fmt_krw)
        name_display["정산기준(KRW)"] = name_display["정산기준KRW"].apply(fmt_krw)
        name_display["소속사 정산액"]  = name_display["소속사정산"].apply(fmt_krw)
        name_display["대행사 정산액"]  = name_display["대행사정산"].apply(fmt_krw)

        show_cols_name = ["상품 이름"]
        if "상품 카테고리" in name_display.columns:
            show_cols_name.append("상품 카테고리")
        show_cols_name += ["결제건수", "매출(KRW)", "정산기준(KRW)", "소속사 정산액", "대행사 정산액"]

        col_n1, col_n2 = st.columns([3, 1])
        with col_n1:
            st.dataframe(name_display[show_cols_name], use_container_width=True,
                         hide_index=True, height=350)
        with col_n2:
            # TOP5 파이 차트
            top5 = name_df.head(5)
            fig_pie = px.pie(top5, values="매출KRW", names="상품 이름",
                             color_discrete_sequence=px.colors.qualitative.Pastel)
            fig_pie.update_traces(textposition="inside", textinfo="label+percent")
            fig_pie.update_layout(height=320, margin=dict(t=10,b=0,l=0,r=0),
                                  showlegend=False)
            st.caption("매출 TOP 5")
            st.plotly_chart(fig_pie, use_container_width=True)
    else:
        st.caption("이 기간에는 상품 이름별 매출이 없어요.")

    # ── 일별 추이 ─────────────────────────────────────────────────
    with st.expander("📅 일별 매출 추이"):
        daily = (
            paid.groupby("날짜")["KRW환산"].sum()
            .reset_index().rename(columns={"KRW환산": "매출"})
        )
        daily["날짜_str"] = daily["날짜"].astype(str)
        fig2 = px.bar(daily, x="날짜_str", y="매출", color_discrete_sequence=["#4361ee"])
        fig2.update_layout(yaxis_tickformat=",", height=260, margin=dict(t=10, b=0))
        fig2.update_traces(hovertemplate="%{x}<br>%{y:,}원<extra></extra>")
        st.plotly_chart(fig2, use_container_width=True)

    # ── 원본 데이터 ───────────────────────────────────────────────
    with st.expander("🗃 원본 데이터 보기"):
        show_cols = [
            "날짜", "국가", "매장 이름", "프레임 이름",
            "최종 결제 금액", "쿠폰 할인 금액", "결제 단위", "KRW환산", "쿠폰KRW",
            "결제 수단", "취소 여부",
        ]
        available = [c for c in show_cols if c in df_ip.columns]
        st.dataframe(
            df_ip[available].sort_values("날짜", ascending=False).reset_index(drop=True),
            use_container_width=True, height=350,
        )
        csv_out = df_ip[available].to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")
        st.download_button(
            f"CSV 다운로드 ({selected_ip})",
            csv_out,
            f"settlement_{selected_ip}.csv",
            "text/csv",
        )

    # ── Jira RS 미등록 프레임 안내 ─────────────────────────────────
    if unmatched_frames:
        with st.expander(f"⚠️ Jira 미매칭 어드민 프레임 ({len(unmatched_frames)}개) — 클릭해서 확인"):
            st.caption(
                "어드민에 매출이 있지만 Snapism Jira 티켓과 자동 매칭되지 않은 프레임입니다. "
                "위에서 IP를 선택한 뒤 프레임을 직접 추가하고 '💾 매핑 저장'을 눌러두면 다음번에 자동으로 적용돼요."
            )
            st.dataframe(
                pd.DataFrame({"프레임 이름": unmatched_frames}),
                use_container_width=True, hide_index=True,
            )
