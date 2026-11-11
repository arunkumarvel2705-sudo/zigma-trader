"""Intraday Camarilla breakout — the rules from the 06/12, 07/12 and 24/01 class notes.

Entry (15-minute chart)
  • Levels from the PREVIOUS day's high, low, close.
  • No trade in the first 30 minutes: the first usable candle is 9:30–9:45 (closes at 9:45).
  • BUY when a green candle closes above R3. SELL (short) when a red candle closes below S3.
  • No new entries after 12:00.
  • Don't go against the trend: the 1-hour trend must be up for buys / down for sells
    (sideways → no trade).
Exit (whichever comes first)
  • Target: +1% or R4/S4, whichever is closer.
  • Stop loss (not in the notes — added because a strategy without one is untestable):
      "close_back_inside": a 15-min candle closes back below R3 (above S3 for shorts),
      plus a hard stop at `stop_pct`.
      "fixed": only the hard stop at `stop_pct`.
  • Square off at 15:15 if still open (brokers auto-close intraday positions ~15:20).
"""

from dataclasses import asdict, dataclass
from datetime import time, timedelta

import numpy as np
import pandas as pd

from .charges import round_trip_cost
from .levels import Camarilla
from .patterns import swing_points, trend_at

BAR = timedelta(minutes=15)


@dataclass
class IntradayRules:
    first_bar_start: time = time(9, 30)   # candle 9:30–9:45 is the first allowed signal
    last_entry_close: time = time(12, 0)
    square_off: time = time(15, 15)
    target_pct: float = 1.0
    use_r4_target: bool = True
    stop_mode: str = "close_back_inside"  # or "fixed"
    stop_pct: float = 1.0
    trend_filter: bool = True
    allow_short: bool = True
    capital_per_trade: float = 10_000.0
    swing_n: int = 2                       # bars each side to confirm a swing on the 1h chart


def _prev_day_levels(daily: pd.DataFrame, day) -> Camarilla | None:
    prior = daily[daily.index.date < day]
    if prior.empty:
        return None
    p = prior.iloc[-1]
    return Camarilla(float(p.High), float(p.Low), float(p.Close))


def _trend_lookup(h1: pd.DataFrame | None, n: int):
    if h1 is None or h1.empty:
        return lambda t: "unknown"
    sw = swing_points(h1, n)
    ends = h1.index + pd.Timedelta(hours=1)   # a 1h bar is only usable once it has closed
    ends_utc = ends.tz_convert("UTC").tz_localize(None).values if ends.tz is not None else ends.values

    def at_utc(t):
        tt = (t.tz_convert("UTC").tz_localize(None) if t.tzinfo else t).to_datetime64()
        k = int(np.searchsorted(ends_utc, tt, side="right")) - 1
        return trend_at(sw, k) if k >= 0 else "unknown"
    return at_utc


def run_day(day_bars: pd.DataFrame, lv: Camarilla, rules: IntradayRules, trend_of=lambda t: "unknown"):
    """Simulate one day. Returns (trade dict or None, status dict for the scanner)."""
    r3, r4, s3, s4 = lv.r(3), lv.r(4), lv.s(3), lv.s(4)
    status = {"R3": r3, "R4": r4, "S3": s3, "S4": s4, "signal": "", "note": "Waiting for 9:45 candle"}
    trade = None
    skipped = ""
    for ts, b in day_bars.iterrows():
        start = ts.time()
        close_t = (ts + BAR).time()
        o, h, l, c = float(b.Open), float(b.High), float(b.Low), float(b.Close)

        if trade is None:
            if start < rules.first_bar_start:
                continue
            if close_t > rules.last_entry_close:
                status["note"] = skipped or "No setup before 12:00"
                break
            side = None
            if c > r3 and c > o:
                side = "BUY"
            elif rules.allow_short and c < s3 and c < o:
                side = "SELL"
            if not side:
                status["note"] = skipped or "Price inside R3–S3 (no-trade zone)"
                continue
            tr = trend_of(ts + BAR)
            if rules.trend_filter and not ((side == "BUY" and tr == "up") or (side == "SELL" and tr == "down")):
                skipped = f"{side} setup at {close_t:%H:%M} skipped — 1h trend is {tr}"
                status["note"] = skipped
                continue
            entry = c
            if side == "BUY":
                tgt = entry * (1 + rules.target_pct / 100)
                if rules.use_r4_target and r4 > entry:
                    tgt = min(tgt, r4)
                hard = entry * (1 - rules.stop_pct / 100)
            else:
                tgt = entry * (1 - rules.target_pct / 100)
                if rules.use_r4_target and s4 < entry:
                    tgt = max(tgt, s4)
                hard = entry * (1 + rules.stop_pct / 100)
            trade = {"side": side, "entry_time": ts + BAR, "entry": entry, "target": tgt, "stop": hard,
                     "trend": tr, "R3": r3, "R4": r4, "S3": s3, "S4": s4}
            status.update(signal=side, note=f"{side} at {close_t:%H:%M} @ {entry:.2f} (1h trend {tr})",
                          entry=entry, target=tgt, stop=hard)
            continue

        # ---- manage open trade ----
        long = trade["side"] == "BUY"
        hit_stop = l <= trade["stop"] if long else h >= trade["stop"]
        hit_tgt = h >= trade["target"] if long else l <= trade["target"]
        exit_px, reason = None, None
        if hit_stop:                       # same-bar ambiguity → assume the worse outcome
            exit_px, reason = trade["stop"], "Stop loss"
        elif hit_tgt:
            exit_px = trade["target"]
            reason = "Target R4/S4" if abs(trade["target"] - (r4 if long else s4)) < 1e-9 else f"Target {rules.target_pct:g}%"
        elif rules.stop_mode == "close_back_inside" and ((long and c < r3) or (not long and c > s3)):
            exit_px, reason = c, "Closed back inside R3/S3"
        elif close_t >= rules.square_off:
            exit_px, reason = c, "Square-off 15:15"
        if exit_px is not None:
            trade.update(exit_time=ts + BAR, exit=exit_px, reason=reason)
            break

    if trade is not None and "exit" not in trade:
        last_ts = day_bars.index[-1]
        trade.update(exit_time=last_ts + BAR, exit=float(day_bars.iloc[-1].Close), reason="Open (day not over)")
    if trade is not None:
        qty = int(rules.capital_per_trade // trade["entry"])
        trade["qty"] = qty
        if qty <= 0:
            return None, status
        sign = 1 if trade["side"] == "BUY" else -1
        trade["gross"] = sign * (trade["exit"] - trade["entry"]) * qty
        buy_v = qty * (trade["entry"] if sign == 1 else trade["exit"])
        sell_v = qty * (trade["exit"] if sign == 1 else trade["entry"])
        trade["charges"] = round_trip_cost(buy_v, sell_v, "intraday")
        trade["net"] = trade["gross"] - trade["charges"]
        trade["net_pct"] = trade["net"] / (qty * trade["entry"]) * 100
        status["result"] = trade["reason"]
    return trade, status


def backtest(symbol: str, m15: pd.DataFrame, daily: pd.DataFrame, h1: pd.DataFrame | None,
             rules: IntradayRules) -> pd.DataFrame:
    trend_of = _trend_lookup(h1, rules.swing_n)
    rows = []
    for day, bars in m15.groupby(m15.index.date):
        lv = _prev_day_levels(daily, day)
        if lv is None or lv.range <= 0:
            continue
        t, _ = run_day(bars, lv, rules, trend_of)
        if t is not None and t["reason"] != "Open (day not over)":
            rows.append({"symbol": symbol, "date": day, **t})
    return pd.DataFrame(rows)


def today_status(symbol: str, m15: pd.DataFrame, daily: pd.DataFrame, h1: pd.DataFrame | None,
                 rules: IntradayRules) -> dict:
    if m15.empty:
        return {"symbol": symbol, "note": "No data"}
    last_day = m15.index[-1].date()
    bars = m15[m15.index.date == last_day]
    lv = _prev_day_levels(daily, last_day)
    if lv is None:
        return {"symbol": symbol, "note": "No previous-day data"}
    _, st = run_day(bars, lv, rules, _trend_lookup(h1, rules.swing_n))
    st.update(symbol=symbol, day=last_day, last=float(bars.iloc[-1].Close),
              last_time=(bars.index[-1] + BAR).strftime("%H:%M"))
    return st


def _drawdown(trades: pd.DataFrame) -> float:
    col = "entry_time" if "entry_time" in trades else "entry_date"
    eq = trades.sort_values(col).net.cumsum()
    return float(min((eq - eq.cummax()).min(), 0.0))


def summarize(trades: pd.DataFrame) -> dict:
    if trades.empty:
        return {"trades": 0}
    wins = trades[trades.net > 0]
    losses = trades[trades.net <= 0]
    return {
        "trades": len(trades),
        "win_rate": len(wins) / len(trades),
        "avg_win": wins.net.mean() if len(wins) else 0.0,
        "avg_loss": losses.net.mean() if len(losses) else 0.0,
        "net": trades.net.sum(),
        "gross": trades.gross.sum(),
        "charges": trades.charges.sum(),
        "expectancy": trades.net.mean(),
        "profit_factor": (wins.net.sum() / -losses.net.sum()) if len(losses) and losses.net.sum() < 0 else float("inf"),
        "max_drawdown": _drawdown(trades),
    }


def rules_dict(r: IntradayRules) -> dict:
    return {k: (v.strftime("%H:%M") if isinstance(v, time) else v) for k, v in asdict(r).items()}
