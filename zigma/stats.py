"""Judging accuracy properly — ideas from David Aronson (Evidence-Based Technical Analysis, 2006),
Van K. Tharp (Trade Your Way to Financial Freedom) and Robert Pardo (The Evaluation and Optimization
of Trading Strategies)."""

import math

import pandas as pd


def wilson(wins: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% confidence range for a true win rate after `wins` out of `n` trades."""
    if n == 0:
        return 0.0, 1.0
    p = wins / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0.0, centre - half), min(1.0, centre + half)


def r_stats(trades: pd.DataFrame) -> dict:
    """Tharp's expectancy: the average R-multiple per trade (R = money lost if the stop is hit)."""
    r = pd.to_numeric(trades.get("R"), errors="coerce").dropna() if "R" in trades else pd.Series(dtype=float)
    if r.empty:
        return {}
    return {"expectancy_R": r.mean(), "avg_win_R": r[r > 0].mean() if (r > 0).any() else 0.0,
            "avg_loss_R": r[r <= 0].mean() if (r <= 0).any() else 0.0, "best_R": r.max(), "worst_R": r.min()}


def t_stat(values: pd.Series) -> float | None:
    """How many standard errors the average result is above zero (≥ 2 ≈ unlikely to be luck)."""
    v = pd.to_numeric(values, errors="coerce").dropna()
    if len(v) < 5 or v.std(ddof=1) == 0:
        return None
    return float(v.mean() / (v.std(ddof=1) / math.sqrt(len(v))))


FILTERS = [
    ("With the 1h trend (class rule)", lambda t: t["with_trend"] == True),
    ("ADX ≥ 20 (Wilder)", lambda t: pd.to_numeric(t["adx"], errors="coerce") >= 20),
    ("Volume ≥ 1.5× average (Murphy)", lambda t: pd.to_numeric(t["vol_ratio"], errors="coerce") >= 1.5),
    ("Previous day NR7 (Crabel)", lambda t: t["nr7"] == True),
    ("Narrow CPR < 0.5% (Ochoa)", lambda t: pd.to_numeric(t["cpr_width"], errors="coerce") < 0.5),
]


def filter_impact(trades: pd.DataFrame) -> pd.DataFrame:
    """For each filter: results of trades that passed it vs trades that didn't."""
    rows = []
    for name, rule in FILTERS:
        try:
            mask = rule(trades).fillna(False).astype(bool)
        except KeyError:
            continue
        for label, part in (("pass", trades[mask]), ("fail", trades[~mask])):
            n = len(part)
            rows.append({"filter": name, "group": label, "trades": n,
                         "win_rate": (part.net > 0).mean() if n else None,
                         "avg_R": pd.to_numeric(part.get("R"), errors="coerce").mean() if n and "R" in part else None,
                         "net": part.net.sum() if n else 0.0})
    return pd.DataFrame(rows)


def split_halves(trades: pd.DataFrame, date_col: str) -> pd.DataFrame:
    """Pardo's out-of-sample check: does the first half look like the second half?"""
    t = trades.sort_values(date_col)
    k = len(t) // 2
    out = []
    for label, part in (("First half", t.iloc[:k]), ("Second half", t.iloc[k:])):
        out.append({"period": label, "trades": len(part), "win_rate": (part.net > 0).mean() if len(part) else None,
                    "net": part.net.sum()})
    return pd.DataFrame(out)
