"""Trend (higher highs / higher lows), sideways detection and the four candlestick patterns."""

import numpy as np
import pandas as pd


def swing_points(df: pd.DataFrame, n: int = 2) -> pd.DataFrame:
    """Mark swing highs/lows: a bar whose high (low) is the highest (lowest) of n bars each side.

    A swing at bar i is only *known* at bar i + n, so we also return `confirmed_at`
    (the integer position when it becomes visible) — this avoids peeking into the future.
    """
    h, l = df["High"].to_numpy(), df["Low"].to_numpy()
    rows = []
    for i in range(n, len(df) - n):
        # strictly beyond the bars before it, at least equal to the bars after (handles ties)
        if h[i] > h[i - n:i].max() and h[i] >= h[i + 1:i + n + 1].max():
            rows.append((i, "H", h[i], i + n))
        if l[i] < l[i - n:i].min() and l[i] <= l[i + 1:i + n + 1].min():
            rows.append((i, "L", l[i], i + n))
    return pd.DataFrame(rows, columns=["pos", "kind", "price", "confirmed_at"])


def trend_at(swings: pd.DataFrame, upto_pos: int) -> str:
    """Trend using only swings confirmed by `upto_pos` (29/11 notes).

    Up   = higher high AND higher low
    Down = lower high AND lower low
    Otherwise sideways (range-bound) → no trade.
    """
    s = swings[swings.confirmed_at <= upto_pos]
    hs = s[s.kind == "H"].price.to_numpy()
    ls = s[s.kind == "L"].price.to_numpy()
    if len(hs) < 2 or len(ls) < 2:
        return "unknown"
    hh, hl = hs[-1] > hs[-2], ls[-1] > ls[-2]
    lh, ll = hs[-1] < hs[-2], ls[-1] < ls[-2]
    if hh and hl:
        return "up"
    if lh and ll:
        return "down"
    return "sideways"


def trend_series(df: pd.DataFrame, n: int = 2) -> pd.Series:
    """Trend label for every bar, using only information available at that bar."""
    sw = swing_points(df, n)
    return pd.Series([trend_at(sw, i) for i in range(len(df))], index=df.index)


# ---------------- candlestick patterns (confirmation only) ----------------

def _parts(o, h, l, c):
    body = abs(c - o)
    rng = max(h - l, 1e-9)
    upper = h - max(o, c)
    lower = min(o, c) - l
    return body, rng, upper, lower


def is_hammer(o, h, l, c) -> bool:
    body, rng, upper, lower = _parts(o, h, l, c)
    return lower >= 2 * body and upper <= max(body, rng * 0.1) and body / rng < 0.4


def is_shooting_star(o, h, l, c) -> bool:
    body, rng, upper, lower = _parts(o, h, l, c)
    return upper >= 2 * body and lower <= max(body, rng * 0.1) and body / rng < 0.4


def is_bullish_engulfing(po, pc, o, c) -> bool:
    return pc < po and c > o and o <= pc and c >= po


def is_bearish_engulfing(po, pc, o, c) -> bool:
    return pc > po and c < o and o >= pc and c <= po


def candle_patterns(df: pd.DataFrame) -> pd.Series:
    """Name of the pattern on each bar ('' if none)."""
    o, h, l, c = (df[k].to_numpy() for k in ("Open", "High", "Low", "Close"))
    out = np.array([""] * len(df), dtype=object)
    for i in range(len(df)):
        if i > 0 and is_bullish_engulfing(o[i - 1], c[i - 1], o[i], c[i]):
            out[i] = "Bullish engulfing"
        elif i > 0 and is_bearish_engulfing(o[i - 1], c[i - 1], o[i], c[i]):
            out[i] = "Bearish engulfing"
        elif is_hammer(o[i], h[i], l[i], c[i]):
            out[i] = "Hammer"
        elif is_shooting_star(o[i], h[i], l[i], c[i]):
            out[i] = "Shooting star"
    return pd.Series(out, index=df.index)
