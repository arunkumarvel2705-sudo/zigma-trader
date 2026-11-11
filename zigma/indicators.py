"""Extra measurements from the trading books, used as optional filters and to study what helps.

ADX / ATR   J. Welles Wilder Jr., New Concepts in Technical Trading Systems (1978)
NR7         Toby Crabel, Day Trading with Short Term Price Patterns and Opening Range Breakout (1990)
CPR width   Frank Ochoa, Secrets of a Pivot Boss (2010)
Volume      John J. Murphy, Technical Analysis of the Financial Markets (1999)
"""

import numpy as np
import pandas as pd


def wilder(series: pd.Series, n: int) -> pd.Series:
    """Wilder's smoothing (an EMA with alpha = 1/n)."""
    return series.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    prev_c = df["Close"].shift()
    tr = pd.concat([df["High"] - df["Low"], (df["High"] - prev_c).abs(), (df["Low"] - prev_c).abs()], axis=1).max(axis=1)
    return wilder(tr, n)


def adx(df: pd.DataFrame, n: int = 14) -> pd.Series:
    """Trend strength 0–100. Wilder: above 25 trending, below 20 no trend (sideways)."""
    up = df["High"].diff()
    down = -df["Low"].diff()
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=df.index)
    tr = atr(df, n)
    plus_di = 100 * wilder(plus_dm, n) / tr
    minus_di = 100 * wilder(minus_dm, n) / tr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return wilder(dx, n)


def nr7(daily: pd.DataFrame) -> pd.Series:
    """True on a day whose high–low range is the narrowest of the last 7 days (Crabel)."""
    rng = daily["High"] - daily["Low"]
    return rng == rng.rolling(7, min_periods=7).min()


def cpr_width_pct(h: float, l: float, c: float) -> float:
    """Central Pivot Range width as % of pivot. Narrow (< ~0.25–0.5%) tends to precede trending days (Ochoa)."""
    p = (h + l + c) / 3
    bc = (h + l) / 2
    tc = 2 * p - bc
    return abs(tc - bc) / p * 100


def volume_ratio(bars: pd.DataFrame, i: int, lookback: int = 20) -> float | None:
    """Volume of bar i divided by the average of the previous `lookback` bars."""
    if i < 1 or "Volume" not in bars:
        return None
    prev = bars["Volume"].iloc[max(0, i - lookback):i]
    avg = prev.mean()
    if not avg or np.isnan(avg):
        return None
    return float(bars["Volume"].iloc[i] / avg)
