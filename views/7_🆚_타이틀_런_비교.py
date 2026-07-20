"""
타이틀 런(run) 비교 — "25년 QWER vs 26년 QWER" 처럼 회차별 성과를 나란히 본다.

연도 축이 아니라 '런' 축인 이유:
  런은 연말을 넘기기도 한다(원위 25.12 런 = 2025-12-25~2026-01-14).
  연도로 자르면 하나의 런이 쪼개져 "26년에 급감"으로 잘못 읽힌다.
런 길이가 제각각(QWER 22일 vs 76일)이라 총매출로 비교하면 긴 쪽이 무조건 이긴다.
그래서 기본 지표는 '일평균'.
"""
from pathlib import Path
from contextlib import contextmanager

import pandas as pd
import streamlit as st

import auth
import data_io
from jira_ip_dates import fetch_ip_dates
from title_runs import build_runs, coverage

# 소유자 전용 — URL 직접 접근 차단
_email = (st.user.email or "").strip().lower() if getattr(st, "user", None) else ""
if not auth.is_owner(_email):
    st.error("🔒 이 페이지는 소유자만 볼 수 있어요.")
    st.stop()

BASE_DIR    = Path(__file__).parent.parent
MASTER_FILE = BASE_DIR / "data" / "master.parquet"
CONFIG_FILE = BASE_DIR / "config.json"

st.markdown("""
<style>
@import url("https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/variable/pretendardvariable-dynamic-subset.min.css");
:root{
  --bg:#f4f5f7; --surface:#fff; --surface-2:#f8fafc;
  --border:#e7e9ee; --text:#1b2330; --text-2:#5b6573; --text-3:#98a0af;
  --brand:#4f46e5; --brand-2:#6366f1; --brand-soft:#eef0fe;
  --red:#c0322b; --green:#15803d; --amber:#b45309;
}
html, body, [class*="css"], [data-testid="stAppViewContainer"], [data-testid="stSidebar"],
button, input, select, textarea, label, p, span, div, h1, h2, h3, h4, li, a{
  font-family:'Pretendard Variable','Pretendard',-apple-system,BlinkMacSystemFont,
              'Segoe UI','Malgun Gothic',sans-serif !important;
}
html, body{ letter-spacing:-0.02em; }
.num{ font-variant-numeric:tabular-nums; }
.ct{ font-size:14.5px; font-weight:700; display:flex; align-items:center; gap:7px;
     margin:2px 0 10px; color:var(--text); }
.pghd{ font-size:21px; font-weight:800; color:var(--text); margin:2px 0 3px; letter-spacing:-.03em; }
.pgsub{ font-size:12.5px; color:var(--text-2); margin-bottom:14px; }

/* 런 헤더(A/B) */
.rhd{ display:flex; align-items:baseline; gap:8px; margin-bottom:2px; }
.rbadge{ font-size:10.5px; font-weight:800; color:#fff; background:var(--brand);
         border-radius:5px; padding:2px 7px; letter-spacing:.03em; }
.rbadge.b{ background:#0f9d77; }
.rname{ font-size:15px; font-weight:800; color:var(--text); }
.rdate{ font-size:12px; color:var(--text-2); margin-bottom:9px; }

/* 비교 표 */
.cmp{ border:1px solid var(--border); border-radius:12px; overflow:hidden; margin:2px 0 4px; }
.cmpr{ display:grid; grid-template-columns:1.15fr 1fr 1fr 0.85fr; align-items:center;
       gap:10px; padding:12px 16px; border-bottom:1px solid var(--border); font-size:13.5px; }
.cmpr:last-child{ border-bottom:none; }
.cmpr.hd{ background:var(--surface-2); font-size:11px; font-weight:700;
          color:var(--text-3); letter-spacing:.02em; }
.cmpr .lb{ font-weight:700; color:var(--text-2); font-size:12.5px; }
.cmpr .va{ text-align:right; font-weight:800; color:var(--brand); }
.cmpr .vb{ text-align:right; font-weight:800; color:#0f9d77; }
.cmpr .dl{ text-align:right; font-weight:800; font-size:12.5px; }
.dl.up{ color:var(--green); } .dl.dn{ color:var(--red); } .dl.na{ color:var(--text-3); }
.cmpr.key{ background:#fbfbff; }

/* 일정(계획 vs 실제) */
.sch{ display:flex; flex-wrap:wrap; gap:7px; margin:4px 0 2px; }
.chip{ font-size:11.5px; font-weight:700; border-radius:7px; padding:3px 9px;
       background:var(--surface-2); color:var(--text-2); border:1px solid var(--border); }
.chip.ok{ background:#eefaf4; color:var(--green); border-color:#cfeee1; }
.chip.warn{ background:#fdf3e7; color:var(--amber); border-color:#f6e0c2; }
.chip.miss{ background:#f6f7f9; color:var(--text-3); }

@media (max-width:720px){
  .cmpr{ grid-template-columns:1fr 1fr 1fr; font-size:12.5px; padding:10px 12px; }
  .cmpr .dl{ display:none; }
}
</style>
""", unsafe_allow_html=True)

_is_owner = auth.is_owner(getattr(getattr(st, "user", None), "email", None))


@contextmanager
def card(title=None, key=None):
    c = st.container(border=True, key=key)
    if title:
        c.markdown(f'<div class="ct">{title}</div>', unsafe_allow_html=True)
    with c:
        yield


def helpbox(md):
    if _is_owner and st.session_state.get("show_calc_help"):
        with st.expander("ℹ️ 이 값은 어떻게 계산되나요?", expanded=False):
            st.markdown(md)


def fmt_krw(n):
    return f"₩{int(n):,}"


# ── 데이터 ──────────────────────────────────────────────────
@st.cache_data(ttl=900)
def _load(_v):
    if not MASTER_FILE.exists():
        return pd.DataFrame()
    import json
    df = data_io.read_master(MASTER_FILE)
    df["날짜"] = pd.to_datetime(df["날짜"], errors="coerce").dt.date
    df["취소 여부"] = df["취소 여부"].astype(str).str.lower().isin(["true", "1", "yes"])
    for col in ["최종 결제 금액", "쿠폰 할인 금액"]:
        df[col] = pd.to_numeric(df.get(col, 0), errors="coerce").fillna(0).astype(int)
    try:
        ex = json.loads(CONFIG_FILE.read_text(encoding="utf-8")).get("exchange_rates", {"KRW": 1})
    except Exception:
        ex = {"KRW": 1}
    df["결제 단위"] = df["결제 단위"].fillna("KRW").astype(str).str.strip()
    df["환율"] = df["결제 단위"].map(ex).fillna(1)
    df["KRW환산금액"] = (df["최종 결제 금액"] * df["환율"]).round(0).astype(int)
    # 실결제만 — 취소·전액쿠폰(0원) 제외. 런 성과 비교라 실제 판매액 기준이 맞다.
    return df[~df["취소 여부"] & (df["최종 결제 금액"] > 0)]


@st.cache_data(ttl=900)
def _runs(_v):
    df = _load(_v)
    if df.empty:
        return pd.DataFrame()
    try:
        jira = fetch_ip_dates(brand="snapism", force_refresh=False)
    except Exception:
        jira = {}       # Jira 가 죽어도 런 분리 자체는 매출만으로 된다
    return build_runs(df, jira)


st.markdown('<div class="pghd">🆚 타이틀 런 비교</div>'
            '<div class="pgsub">같은 IP를 여러 번 냈을 때 회차별 성과를 나란히 봐요. '
            '런 길이가 달라서 <b>일평균</b>이 기본 지표예요.</div>', unsafe_allow_html=True)

_v = data_io.file_version(MASTER_FILE)
df = _load(_v)
if df.empty:
    st.warning("매출 데이터가 없어요.")
    st.stop()

runs = _runs(_v)
if runs.empty:
    st.warning("런을 만들 수 없어요. `프레임 이름`이 있는 거래가 필요해요.")
    st.stop()

cov = coverage(runs)

# ── 선택 ────────────────────────────────────────────────────
runs["_라벨"] = (runs["타이틀"] + "  #" + runs["런번호"].astype(str)
                + "  (" + runs["첫거래일"].astype(str) + " ~ " + runs["마지막거래일"].astype(str) + ")")

multi = runs.groupby("타이틀").size()
multi_titles = sorted(multi[multi > 1].index.tolist())

with card("① 비교할 런 고르기", key="scard-pick"):
    only_multi = st.checkbox(f"여러 번 출시된 타이틀만 보기 ({len(multi_titles)}개)", value=True,
                             help="같은 IP의 회차별 비교가 목적이면 켜 두세요. 끄면 서로 다른 IP끼리도 비교할 수 있어요.")
    pool = runs[runs["타이틀"].isin(multi_titles)] if only_multi else runs
    if pool.empty:
        st.info("조건에 맞는 런이 없어요.")
        st.stop()

    labels = pool["_라벨"].tolist()
    # 기본값: 매출이 가장 큰 멀티런 타이틀의 1회차 vs 2회차
    if multi_titles:
        top = (pool[pool["타이틀"].isin(multi_titles)]
               .groupby("타이틀")["매출"].sum().sort_values(ascending=False).index[0])
        dft = pool[pool["타이틀"] == top].sort_values("런번호")
        d_a = labels.index(dft.iloc[0]["_라벨"])
        d_b = labels.index(dft.iloc[1]["_라벨"]) if len(dft) > 1 else min(1, len(labels) - 1)
    else:
        d_a, d_b = 0, min(1, len(labels) - 1)

    c1, c2 = st.columns(2)
    la = c1.selectbox("🅐 런 A", labels, index=d_a)
    lb = c2.selectbox("🅑 런 B", labels, index=d_b)

A = pool[pool["_라벨"] == la].iloc[0]
B = pool[pool["_라벨"] == lb].iloc[0]

if la == lb:
    st.info("서로 다른 런을 골라주세요.")
    st.stop()


# ── 비교 표 ─────────────────────────────────────────────────
def _delta(a, b):
    """B 대비 A가 아니라, A→B 변화율. 기준(A)이 0이면 계산 불가."""
    if not a:
        return '<span class="dl na">—</span>'
    p = (b - a) / a * 100
    cls = "up" if p > 0 else ("dn" if p < 0 else "na")
    return f'<span class="dl {cls}">{p:+.0f}%</span>'


def _row(label, a_txt, b_txt, a_val, b_val, key=False):
    return (f'<div class="cmpr{" key" if key else ""}"><span class="lb">{label}</span>'
            f'<span class="va num">{a_txt}</span><span class="vb num">{b_txt}</span>'
            f'{_delta(a_val, b_val)}</div>')


with card("② 성과 비교", key="scard-cmp"):
    h1, h2 = st.columns(2)
    h1.markdown(f'<div class="rhd"><span class="rbadge">A</span>'
                f'<span class="rname">{A["타이틀"]} #{A["런번호"]}</span></div>'
                f'<div class="rdate">{A["첫거래일"]} ~ {A["마지막거래일"]} · {A["판매일수"]}일</div>',
                unsafe_allow_html=True)
    h2.markdown(f'<div class="rhd"><span class="rbadge b">B</span>'
                f'<span class="rname">{B["타이틀"]} #{B["런번호"]}</span></div>'
                f'<div class="rdate">{B["첫거래일"]} ~ {B["마지막거래일"]} · {B["판매일수"]}일</div>',
                unsafe_allow_html=True)

    html = ('<div class="cmp"><div class="cmpr hd"><span>지표</span>'
            '<span style="text-align:right">🅐 A</span><span style="text-align:right">🅑 B</span>'
            '<span style="text-align:right">변화</span></div>')
    # 일평균이 먼저 — 런 길이가 다르면 총매출 비교는 의미가 없다.
    html += _row("일평균 매출", fmt_krw(A["일평균매출"]), fmt_krw(B["일평균매출"]),
                 A["일평균매출"], B["일평균매출"], key=True)
    html += _row("일평균 건수", f'{A["일평균건수"]:,}', f'{B["일평균건수"]:,}',
                 A["일평균건수"], B["일평균건수"], key=True)
    a_ps = A["매출"] / A["매장수"] if A["매장수"] else 0
    b_ps = B["매출"] / B["매장수"] if B["매장수"] else 0
    html += _row("매장당 매출", fmt_krw(a_ps), fmt_krw(b_ps), a_ps, b_ps, key=True)
    html += _row("총 매출", fmt_krw(A["매출"]), fmt_krw(B["매출"]), A["매출"], B["매출"])
    html += _row("총 건수", f'{A["건수"]:,}', f'{B["건수"]:,}', A["건수"], B["건수"])
    html += _row("판매일수", f'{A["판매일수"]}일', f'{B["판매일수"]}일', A["판매일수"], B["판매일수"])
    html += _row("판매 매장수", f'{A["매장수"]:,}', f'{B["매장수"]:,}', A["매장수"], B["매장수"])
    st.markdown(html + "</div>", unsafe_allow_html=True)

    helpbox("""
**런 비교 계산 방식**
- **런** = 같은 `프레임 이름`의 연속 판매 구간. 판매가 **21일 이상 끊기면** 다음 런으로 나눠요.
  (연도로 자르지 않는 이유 — 원위 25.12 런은 `2025-12-25 ~ 2026-01-14`로 연말을 넘어가요)
- **일평균 매출** = `총매출 ÷ 판매일수`. 런 길이가 제각각이라(22일 vs 76일) 총매출 비교는 긴 쪽이 무조건 이겨서, 일평균을 기본으로 둬요.
- **매장당 매출** = `총매출 ÷ 판매 매장수`. 매장이 늘어난 효과를 걷어내요.
- **변화** = A 대비 B의 증감률 `(B-A)/A`.
- 대상은 **실결제만** — 취소 건과 전액 쿠폰(0원) 거래는 빼요.
""")

# ── 일정 (Jira 계획 vs 실제) ─────────────────────────────────
with card("③ 일정 — 계획 vs 실제", key="scard-sch"):
    def _chips(R, tag):
        out = [f'<span class="chip">{tag}</span>']
        if R["계획시작일"]:
            d = R["오픈지연일"]
            cls = "ok" if d is not None and abs(d) <= 7 else "warn"
            txt = "일정대로" if d == 0 else (f"{abs(int(d))}일 {'지연' if d > 0 else '앞당김'}")
            out.append(f'<span class="chip">계획 {R["계획시작일"]} ~ {R["계획종료일"] or "미정"}</span>')
            out.append(f'<span class="chip {cls}">실제 오픈 {R["첫거래일"]} · {txt}</span>')
            if R["티켓"]:
                out.append(f'<span class="chip">{R["티켓"]}</span>')
        else:
            out.append('<span class="chip miss">Jira 일정 연결 안 됨 — 실제 거래일만 사용</span>')
        return '<div class="sch">' + "".join(out) + "</div>"

    st.markdown(_chips(A, "🅐 A"), unsafe_allow_html=True)
    st.markdown(_chips(B, "🅑 B"), unsafe_allow_html=True)
    st.caption(f"전체 런 {cov['런수']}개 중 {cov['매칭런']}개가 Jira 일정에 연결됐어요 "
               f"(런 {cov['매칭률']}% · 매출 기준 {cov['매출커버율']}%). "
               "연결 안 된 런은 실제 첫·마지막 거래일로만 계산해요.")

    helpbox("""
**일정 연결 방식**
- Jira `CANDIP` 프로젝트의 `프로그램 및 검수` 하위 작업에서 **시작 날짜(`customfield_10015`)** 와 **종료일(`duedate`)** 을 가져와요.
- Jira 타이틀(`25.10 QWER 아티스트 프레임`)과 매출 `프레임 이름`(`QWER`)이 달라서, **출시월 접두 · 상품유형 접미 · 지역 태그 · 괄호**를 떼고 `ip_aliases.json` 별칭까지 적용해 맞춰요.
- 한 IP에 회차별 티켓이 여러 개면 **런 기간과 실제로 겹치는** 티켓을 골라요. 겹치는 게 없으면 **연결하지 않아요** — 억지로 붙이면 '오픈지연 -418일' 같은 헛값이 나와서요.
- **오픈지연일** = `첫 거래일 − 계획 시작일`. 음수면 계획보다 빨리 열린 거예요.
""")

# ── 경과일 정렬 추이 ─────────────────────────────────────────
with card("④ 런 경과일별 매출 추이", key="scard-trend"):
    st.caption("시작일을 0일로 맞춰 겹쳐 봐요. 날짜가 달라도 '초반 반응'과 '지속력'을 바로 비교할 수 있어요.")

    def _series(R):
        m = df[(df["프레임 이름"] == R["타이틀"]) &
               (df["날짜"] >= R["첫거래일"]) & (df["날짜"] <= R["마지막거래일"])]
        s = m.groupby("날짜")["KRW환산금액"].sum()
        idx = pd.date_range(R["첫거래일"], R["마지막거래일"], freq="D").date
        s = s.reindex(idx, fill_value=0)                  # 판매 없는 날도 0으로 채워 축을 맞춘다
        return pd.DataFrame({"경과일": range(len(s)), "매출": s.values})

    sa, sb = _series(A), _series(B)
    plot = pd.DataFrame({"경과일": range(max(len(sa), len(sb)))})
    plot[f'🅐 {A["타이틀"]} #{A["런번호"]}'] = sa["매출"].reindex(plot.index)
    plot[f'🅑 {B["타이틀"]} #{B["런번호"]}'] = sb["매출"].reindex(plot.index)
    st.line_chart(plot.set_index("경과일"), height=300, color=["#4f46e5", "#0f9d77"])

    ca, cb = st.columns(2)
    for col, R, s, tag in ((ca, A, sa, "🅐"), (cb, B, sb, "🅑")):
        peak = int(s["매출"].idxmax()) if len(s) else 0
        first7 = int(s.head(7)["매출"].sum())
        share = first7 / R["매출"] * 100 if R["매출"] else 0
        col.markdown(f'<div class="rdate"><b>{tag} {R["타이틀"]} #{R["런번호"]}</b><br>'
                     f'최고 매출일 <b>{peak}일차</b> · 첫 7일이 전체의 <b>{share:.0f}%</b></div>',
                     unsafe_allow_html=True)

    helpbox("""
**경과일 추이**
- 각 런의 **첫 거래일을 0일차**로 맞춰 일별 실결제 매출을 겹쳐 그려요. 출시 시점이 달라도 곡선 모양을 직접 비교할 수 있어요.
- 판매가 없는 날도 **0으로 채워** 축을 맞춰요 (빈 날을 건너뛰면 그래프가 짧아 보여 잘못 읽혀요).
- **첫 7일 비중**이 높을수록 초반에 몰리는(화제성 중심) 런, 낮을수록 오래 팔리는 런이에요.
""")
