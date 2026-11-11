"""Indian equity trading charges for a discount broker (Zerodha-style), so results are net of costs.

Rates change from time to time — check your broker's charges page and edit RATES if needed.
"""

RATES = {
    "intraday": {
        "brokerage_pct": 0.03, "brokerage_cap": 20.0,   # per executed order: lower of 0.03% or ₹20
        "stt_sell_pct": 0.025,                          # sell side only
        "exchange_pct": 0.00297,                        # NSE transaction charge, both sides
        "sebi_per_cr": 10.0,                            # ₹10 per crore, both sides
        "stamp_buy_pct": 0.003,                         # buy side only
        "gst_pct": 18.0,                                # on brokerage + exchange + SEBI
    },
    "delivery": {
        "brokerage_pct": 0.0, "brokerage_cap": 0.0,
        "stt_sell_pct": 0.1, "stt_buy_pct": 0.1,
        "exchange_pct": 0.00297,
        "sebi_per_cr": 10.0,
        "stamp_buy_pct": 0.015,
        "gst_pct": 18.0,
        "dp_charge": 15.34,                             # per scrip on the sell day (approx.)
    },
}


def round_trip_cost(buy_value: float, sell_value: float, kind: str = "intraday") -> float:
    r = RATES[kind]
    brokerage = sum(min(v * r["brokerage_pct"] / 100, r["brokerage_cap"]) for v in (buy_value, sell_value)) \
        if r["brokerage_cap"] else 0.0
    stt = sell_value * r["stt_sell_pct"] / 100 + buy_value * r.get("stt_buy_pct", 0) / 100
    exchange = (buy_value + sell_value) * r["exchange_pct"] / 100
    sebi = (buy_value + sell_value) * r["sebi_per_cr"] / 1e7
    stamp = buy_value * r["stamp_buy_pct"] / 100
    gst = (brokerage + exchange + sebi) * r["gst_pct"] / 100
    return brokerage + stt + exchange + sebi + stamp + gst + r.get("dp_charge", 0.0)
