"""포토이즘 매출 계산 규칙 — 여러 페이지가 같은 값을 쓰도록 한 곳에 둔다.

포토이즘 '매출액'은 실결제만이 아니다. 일부 국가는 쿠폰·서비스코인이 실제 정산분이라
매출에 더해야 한다(안 더하면 그 나라 매출이 0에 가깝게 나온다). 어느 국가를 더할지가
이 규칙이고, 대시보드와 런 비교가 다른 값을 내면 안 되므로 여기서만 정의한다.
"""

# 쿠폰 할인 금액을 매출로 가산하는 국가 (소문자 국가코드)
COUPON_CC = frozenset({"la", "gb", "de", "th", "lv", "mx"})

# 서비스코인을 매출로 가산하는 국가
COIN_CC = frozenset({"cl", "la", "pe", "gb", "de", "lv", "mx"})


def add_revenue(df, rates, cc_col="국가코드"):
    """환율 적용 + '매출액' 컬럼 추가. df 를 그 자리에서 고치고 돌려준다.

    필요한 컬럼: 결제 단위, 최종 결제 금액, 쿠폰 할인 금액, 서비스코인, <cc_col>
    """
    import pandas as pd

    unit = df["결제 단위"].astype(str).str.strip().replace({"nan": "KRW", "": "KRW"})
    rate = unit.map(rates).astype(float).fillna(1.0)

    krw   = (pd.to_numeric(df["최종 결제 금액"], errors="coerce").fillna(0) * rate).round(0)
    coup  = (pd.to_numeric(df.get("쿠폰 할인 금액", 0), errors="coerce").fillna(0) * rate).round(0)
    coin  = (pd.to_numeric(df.get("서비스코인", 0), errors="coerce").fillna(0) * rate).round(0)

    cc = df[cc_col].astype(str).str.lower().str.strip()
    df["매출액"] = (krw
                    + coup * cc.isin(COUPON_CC).astype(int)
                    + coin * cc.isin(COIN_CC).astype(int)).astype("int64")
    return df
