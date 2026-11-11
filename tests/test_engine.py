"""Checks the class rules on hand-made price data (no internet needed)."""

from datetime import date, datetime, time, timedelta

import numpy as np
import pandas as pd
import pytest

from zigma import fundamentals, intraday, swing
from zigma.charges import round_trip_cost
from zigma.levels import Camarilla, Traditional, fib_extensions, fib_retracements
from zigma.patterns import candle_patterns, swing_points, trend_at, trend_series

IST = "Asia/Kolkata"


# ------------------------------------------------------------------ levels
def test_camarilla_matches_published_formula():
    lv = Camarilla(h=110, l=90, c=105)  # range 20
    assert lv.r(3) == pytest.approx(105 + 20 * 1.1 / 4)   # 110.5
    assert lv.r(4) == pytest.approx(116.0)
    assert lv.s(3) == pytest.approx(99.5)
    assert lv.s(4) == pytest.approx(94.0)
    assert lv.r(1) < lv.r(2) < lv.r(3) < lv.r(4) < lv.r(5)
    assert lv.s(1) > lv.s(2) > lv.s(3) > lv.s(4) > lv.s(5)
    assert lv.pivot == pytest.approx(305 / 3)


def test_traditional_pivot():
    d = Traditional(110, 90, 100).as_dict()
    assert d["Pivot"] == pytest.approx(100)
    assert d["R1"] == pytest.approx(110) and d["S1"] == pytest.approx(90)


def test_fibonacci():
    r = fib_retracements(100, 200)
    assert r[0.382] == pytest.approx(161.8) and r[0.618] == pytest.approx(138.2)
    e = fib_extensions(100, 200, 140)
    assert e[1.0] == pytest.approx(240) and e[1.618] == pytest.approx(301.8)


# ------------------------------------------------------------------ trend
def _zigzag(points, steps=4):
    """Daily OHLC that walks linearly between the given turning points."""
    closes = []
    for a, b in zip(points, points[1:]):
        closes += list(np.linspace(a, b, steps, endpoint=False))
    closes.append(points[-1])
    c = np.array(closes)
    idx = pd.bdate_range("2025-01-01", periods=len(c))
    return pd.DataFrame({"Open": c, "High": c + 0.5, "Low": c - 0.5, "Close": c, "Volume": 1}, index=idx)


def test_trend_up_down_sideways():
    up = _zigzag([100, 110, 105, 118, 112, 125, 120, 130])
    down = _zigzag([130, 120, 125, 112, 118, 105, 110, 100])
    side = _zigzag([100, 110, 100, 110, 100, 110, 100, 110])
    for df, want in ((up, "up"), (down, "down"), (side, "sideways")):
        sw = swing_points(df, 2)
        assert trend_at(sw, len(df) - 1) == want


def test_trend_has_no_lookahead():
    df = _zigzag([100, 110, 105, 118, 112, 125])
    ts = trend_series(df, 2)
    # first bars can't know a trend yet
    assert ts.iloc[0] == "unknown"


def test_candles():
    df = pd.DataFrame({"Open": [97, 98, 105, 100], "High": [101, 101, 110.5, 100.5],
                       "Low": [96, 90, 104.8, 99], "Close": [100, 100.5, 105.3, 100.2]})
    pats = candle_patterns(df).tolist()
    assert pats[1] == "Hammer"
    assert pats[2] == "Shooting star"
    eng = pd.DataFrame({"Open": [105, 99], "High": [106, 108], "Low": [99, 98], "Close": [100, 107]})
    assert candle_patterns(eng).iloc[1] == "Bullish engulfing"


# ------------------------------------------------------------------ intraday
def _day_bars(day: date, path: list[tuple[float, float, float, float]]):
    idx = [pd.Timestamp(datetime.combine(day, time(9, 15)) + timedelta(minutes=15 * i), tz=IST)
           for i in range(len(path))]
    return pd.DataFrame(path, columns=["Open", "High", "Low", "Close"], index=idx).assign(Volume=1000)


def _flat(price, n):
    return [(price, price + 0.2, price - 0.2, price)] * n


PREV = Camarilla(h=110, l=90, c=105)  # R3 110.5 R4 116 S3 99.5 S4 94
RULES = intraday.IntradayRules(trend_filter=False, capital_per_trade=10_000)


def test_buy_breakout_hits_one_percent_target():
    path = _flat(108, 1) + [(109, 111.2, 108.8, 111.0)]          # 9:30 candle closes 9:45 above R3, green → BUY 111
    path += [(111, 112.3, 110.9, 112.0)]                           # high ≥ 112.11 (1%) → target
    path += _flat(112, 22)
    t, st = intraday.run_day(_day_bars(date(2026, 1, 5), path), PREV, RULES)
    assert t["side"] == "BUY" and t["entry"] == 111.0
    assert t["exit"] == pytest.approx(111 * 1.01) and t["reason"].startswith("Target")
    assert t["entry_time"].time() == time(9, 45)
    assert t["net"] < t["gross"]                                   # charges taken off


def test_no_entry_in_first_half_hour():
    path = [(108, 112, 108, 111.5)] + _flat(108, 24)               # only the 9:15 candle breaks out
    t, st = intraday.run_day(_day_bars(date(2026, 1, 5), path), PREV, RULES)
    assert t is None


def test_red_candle_above_r3_is_not_a_buy():
    path = _flat(108, 2) + [(112, 112.5, 110.6, 111.0)] + _flat(108, 22)  # close > R3 but red
    t, _ = intraday.run_day(_day_bars(date(2026, 1, 5), path), PREV, RULES)
    assert t is None


def test_stop_close_back_inside_r3():
    path = _flat(108, 2) + [(109, 111.2, 108.8, 111.0), (111, 111.1, 110.0, 110.2)] + _flat(110, 21)
    t, _ = intraday.run_day(_day_bars(date(2026, 1, 5), path), PREV, RULES)
    assert t["reason"] == "Closed back inside R3/S3" and t["exit"] == 110.2 and t["net"] < 0


def test_hard_stop_beats_target_on_same_bar():
    path = _flat(108, 2) + [(109, 111.2, 108.8, 111.0), (111, 113, 109.5, 111)] + _flat(111, 21)
    t, _ = intraday.run_day(_day_bars(date(2026, 1, 5), path), PREV, RULES)
    assert t["reason"] == "Stop loss" and t["exit"] == pytest.approx(111 * 0.99)


def test_short_below_s3():
    path = _flat(101, 2) + [(100, 100.2, 98.8, 99.0), (99, 99.1, 97.9, 98.2)] + _flat(98, 21)
    t, _ = intraday.run_day(_day_bars(date(2026, 1, 5), path), PREV, RULES)
    assert t["side"] == "SELL" and t["exit"] == pytest.approx(99 * 0.99) and t["gross"] > 0


def test_no_entry_after_noon_and_square_off():
    path = _flat(108, 12) + [(109, 111.2, 108.8, 111.0)] + _flat(111, 12)   # breakout at 12:15 close
    t, st = intraday.run_day(_day_bars(date(2026, 1, 5), path), PREV, RULES)
    assert t is None and "12:00" in st["note"]
    path = _flat(108, 2) + [(109, 111.2, 108.8, 111.0)] + _flat(111.3, 22)   # never hits target/stop
    t, _ = intraday.run_day(_day_bars(date(2026, 1, 5), path), PREV, RULES)
    assert t["reason"] == "Square-off 15:15" and t["exit_time"].time() == time(15, 15)


def test_trend_filter_blocks_counter_trend():
    rules = intraday.IntradayRules(trend_filter=True)
    path = _flat(108, 2) + [(109, 111.2, 108.8, 111.0)] + _flat(112, 22)
    t, st = intraday.run_day(_day_bars(date(2026, 1, 5), path), PREV, rules, trend_of=lambda ts: "down")
    assert t is None and "trend is down" in st["note"]
    t, _ = intraday.run_day(_day_bars(date(2026, 1, 5), path), PREV, rules, trend_of=lambda ts: "up")
    assert t is not None


def test_backtest_uses_previous_day_levels():
    days = [date(2026, 1, 5), date(2026, 1, 6)]
    daily = pd.DataFrame({"Open": [100, 105], "High": [110, 107], "Low": [90, 104], "Close": [105, 106]},
                         index=pd.to_datetime(["2026-01-02", "2026-01-05"]))
    m15 = pd.concat([_day_bars(days[0], _flat(108, 2) + [(109, 111.2, 108.8, 111.0), (111, 112.3, 110.9, 112)]
                               + _flat(112, 21)),
                     _day_bars(days[1], _flat(106, 25))])
    tr = intraday.backtest("TEST", m15, daily, None, RULES)
    assert len(tr) == 1 and tr.iloc[0].date == days[0]
    s = intraday.summarize(tr)
    assert s["trades"] == 1 and s["win_rate"] == 1.0


# ------------------------------------------------------------------ swing
def test_swing_pullback_entry_and_target():
    # up-move 100→150, pullback to ~128 (between 0.382=130.9 and 0.618=119.1), then rally
    pts = [90, 100, 95, 120, 110, 150, 128, 175]
    df = _zigzag(pts, steps=5)
    df["Open"] = df["Close"] - 0.3          # every day closes green
    tr = swing.backtest("TEST", df, swing.SwingRules(swing_n=2))
    assert not tr.empty
    assert (tr.reason.str.startswith("Target") | tr.reason.str.startswith("Time")).any()


# ------------------------------------------------------------------ charges & fundamentals
def test_charges_reasonable():
    c = round_trip_cost(100_000, 101_000, "intraday")
    assert 70 < c < 95          # ≈ ₹83 on a ₹1 lakh intraday round trip (₹40 is brokerage)


def test_fundamental_checklist():
    m = {"market_cap": 30_000 * 1e7, "total_debt": 3_000 * 1e7, "pe": 18,
         "sales": [100, 120, 150], "profit": [10, 12, 15], "reserves": [50, 60, 70],
         "borrowings": [40, 35, 30], "assets": [200, 220, 260], "insiders": 0.55, "institutions": 0.25}
    r = fundamentals.evaluate(m, industry_pe=25)
    assert r["slab"] == "Large cap" and r["score"] == r["checked"] == 9
    m["total_debt"] = 9_000 * 1e7
    assert fundamentals.evaluate(m, 25)["Debt < 25% of mcap"] is False


# ------------------------------------------------------------------ stats & indicators
def test_wilson_interval():
    from zigma.stats import wilson
    lo, hi = wilson(16, 20)              # 80% on 20 trades
    assert lo < 0.6 and hi > 0.9         # far too wide to trust
    lo, hi = wilson(160, 200)
    assert 0.73 < lo < 0.8 < hi < 0.86


def test_adx_and_nr7():
    from zigma.indicators import adx, nr7, cpr_width_pct
    n = 80
    trend = pd.DataFrame({"High": np.arange(n) + 1.0, "Low": np.arange(n) - 1.0, "Close": np.arange(n) * 1.0})
    assert adx(trend).iloc[-1] > 40      # steady climb = strong trend
    d = pd.DataFrame({"High": [10, 10, 10, 10, 10, 10, 9.2], "Low": [8, 8, 8, 8, 8, 8, 8.9]})
    assert bool(nr7(d).iloc[-1])
    assert cpr_width_pct(110, 90, 100) == pytest.approx(0.0)


def test_trade_records_features_and_R():
    path = _flat(108, 1) + [(109, 111.2, 108.8, 111.0), (111, 112.3, 110.9, 112.0)] + _flat(112, 22)
    ctx = intraday.DayContext(nr7=True, cpr_width=0.3, adx_of=lambda t: 27.0, vol_of=lambda t: 2.0)
    t, _ = intraday.run_day(_day_bars(date(2026, 1, 5), path), PREV, RULES, ctx=ctx)
    assert t["adx"] == 27.0 and t["vol_ratio"] == 2.0 and t["nr7"] is True
    assert t["R"] == pytest.approx(t["net"] / (111 * 0.01 * t["qty"]))
    strict = intraday.IntradayRules(trend_filter=False, min_adx=30)
    t2, st = intraday.run_day(_day_bars(date(2026, 1, 5), path), PREV, strict, ctx=ctx)
    assert t2 is None and "ADX" in st["note"]
