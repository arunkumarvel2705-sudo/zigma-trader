"""Swing trade — Fibonacci pullback in an uptrend (13/12 and 24/01 class notes).

  • Daily chart trend must be UP (higher high + higher low). Fibonacci is buy-side only.
  • Take the last confirmed up-move: swing low A → swing high B.
  • BUY when price pulls back into the 0.382–0.618 retracement zone and the day closes green
    (optionally with a hammer / bullish engulfing for confirmation).
  • Stop: a close below the 0.786 level (maximum retracement — below it the move has failed).
  • Target: Fibonacci extension 1.0 ("character level") from the pullback low; 0.618 / 1.618 shown too.
  • Holding period up to ~1 month (20 trading days), as swing trades are defined in class.
"""

from dataclasses import dataclass

import pandas as pd

from .charges import round_trip_cost
from .levels import fib_extensions, fib_retracements
from .patterns import candle_patterns, swing_points, trend_at


@dataclass
class SwingRules:
    swing_n: int = 3
    zone_low: float = 0.618      # deepest retracement allowed for entry
    zone_high: float = 0.382     # shallowest retracement allowed for entry
    stop_level: float = 0.786
    target_ext: float = 1.0
    max_days: int = 20
    need_pattern: bool = False
    capital_per_trade: float = 10_000.0


def _setup_at(df, sw, i, rules):
    """Return (A, B) of the latest confirmed up-move if day i is in uptrend, else None."""
    if trend_at(sw, i) != "up":
        return None
    s = sw[sw.confirmed_at <= i]
    highs, lows = s[s.kind == "H"], s[s.kind == "L"]
    if highs.empty or lows.empty:
        return None
    b = highs.iloc[-1]
    prior_lows = lows[lows.pos < b.pos]
    if prior_lows.empty:
        return None
    a = prior_lows.iloc[-1]
    if b.price <= a.price:
        return None
    return float(a.price), float(b.price), int(b.pos)


def backtest(symbol: str, daily: pd.DataFrame, rules: SwingRules) -> pd.DataFrame:
    if len(daily) < 30:
        return pd.DataFrame()
    sw = swing_points(daily, rules.swing_n)
    pats = candle_patterns(daily)
    o, h, l, c = (daily[k].to_numpy() for k in ("Open", "High", "Low", "Close"))
    rows, i = [], 0
    while i < len(daily) - 1:
        setup = _setup_at(daily, sw, i, rules)
        if setup:
            a, b, bpos = setup
            fib = fib_retracements(a, b)
            in_zone = fib[rules.zone_low] <= c[i] <= fib[rules.zone_high]
            green = c[i] > o[i]
            pattern_ok = (not rules.need_pattern) or pats.iloc[i] in ("Hammer", "Bullish engulfing")
            if in_zone and green and pattern_ok and i > bpos:
                entry = c[i]
                pull_low = float(l[bpos:i + 1].min())
                target = fib_extensions(a, b, pull_low)[rules.target_ext]
                stop = fib[rules.stop_level]
                exit_px, reason, j = None, None, i
                for j in range(i + 1, min(i + 1 + rules.max_days, len(daily))):
                    if c[j] < stop:
                        exit_px, reason = c[j], "Closed below 0.786"
                        break
                    if h[j] >= target:
                        exit_px, reason = target, f"Target ext {rules.target_ext:g}"
                        break
                if exit_px is None:
                    if j >= len(daily) - 1 and j - i < rules.max_days:
                        break  # still open at the end of data — don't count it
                    exit_px, reason = c[j], f"Time exit ({rules.max_days} days)"
                qty = int(rules.capital_per_trade // entry)
                if qty > 0:
                    gross = (exit_px - entry) * qty
                    chg = round_trip_cost(qty * entry, qty * exit_px, "delivery")
                    rows.append({"symbol": symbol, "entry_date": daily.index[i].date(), "entry": entry,
                                 "exit_date": daily.index[j].date(), "exit": exit_px, "reason": reason,
                                 "days": j - i, "qty": qty, "gross": gross, "charges": chg,
                                 "net": gross - chg, "net_pct": (gross - chg) / (qty * entry) * 100,
                                 "pattern": pats.iloc[i], "swing_low": a, "swing_high": b,
                                 "stop": stop, "target": target, "side": "BUY"})
                i = j + 1
                continue
        i += 1
    return pd.DataFrame(rows)


def today_status(symbol: str, daily: pd.DataFrame, rules: SwingRules) -> dict:
    if len(daily) < 30:
        return {"symbol": symbol, "note": "Not enough history"}
    sw = swing_points(daily, rules.swing_n)
    i = len(daily) - 1
    last = daily.iloc[-1]
    out = {"symbol": symbol, "day": daily.index[-1].date(), "last": float(last.Close),
           "trend": trend_at(sw, i), "pattern": candle_patterns(daily).iloc[-1]}
    setup = _setup_at(daily, sw, i, rules)
    if not setup:
        out["note"] = f"Trend {out['trend']} — swing setup needs an uptrend"
        return out
    a, b, bpos = setup
    fib = fib_retracements(a, b)
    out.update(swing_low=a, swing_high=b, **{f"fib {k}": v for k, v in fib.items()})
    c = float(last.Close)
    if fib[rules.zone_low] <= c <= fib[rules.zone_high]:
        green = c > float(last.Open)
        out["signal"] = "BUY ZONE" if green else "IN ZONE (wait for green day)"
        pull_low = float(daily.Low.iloc[bpos:].min())
        out["target"] = fib_extensions(a, b, pull_low)[rules.target_ext]
        out["stop"] = fib[rules.stop_level]
        out["note"] = "Price in 0.382–0.618 pullback zone"
    elif c > fib[rules.zone_high]:
        out["note"] = "Above the pullback zone — wait for a dip"
    else:
        out["note"] = "Below 0.618 — weak pullback, avoid"
    return out
