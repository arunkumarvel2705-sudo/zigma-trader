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
from .indicators import adx, cpr_width_pct, nr7
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
    # Optional filters from the books (0 / False = off). Every trade records these values anyway,
    # so the backtest and forward test can show whether each filter actually helps.
    min_adx: float = 0.0                   # Wilder: ADX(14) on 1h ≥ 20–25 means a real trend
    min_volume_ratio: float = 0.0          # Murphy: breakout candle volume vs previous 20 candles
    nr7_only: bool = False                 # Crabel: previous day was the narrowest of 7
    max_cpr_width: float = 0.0             # Ochoa: narrow central pivot range → trending day


@dataclass
class DayContext:
    """Information about a day known before the open, plus lookups that respect time."""
    nr7: bool | None = None
    cpr_width: float | None = None
    adx_of: object = None      # callable(timestamp) -> ADX of the last closed 1h bar
    vol_of: object = None      # callable(timestamp) -> volume ratio of the 15m bar starting at timestamp


def _prev_day_levels(daily: pd.DataFrame, day) -> Camarilla | None:
    prior = daily[daily.index.date < day]
    if prior.empty:
        return None
    p = prior.iloc[-1]
    return Camarilla(float(p.High), float(p.Low), float(p.Close))


def _asof(index: pd.DatetimeIndex, values: np.ndarray, offset: pd.Timedelta):
    """Lookup of `values` for the last bar that has CLOSED by time t (no peeking)."""
    ends = index + offset
    ends_utc = ends.tz_convert("UTC").tz_localize(None).values if ends.tz is not None else ends.values

    def at(t):
        tt = (t.tz_convert("UTC").tz_localize(None) if t.tzinfo else t).to_datetime64()
        k = int(np.searchsorted(ends_utc, tt, side="right")) - 1
        if k < 0:
            return None
        v = values[k]
        return None if v is None or (isinstance(v, float) and np.isnan(v)) else float(v)
    return at


def lookups(h1: pd.DataFrame | None, m15: pd.DataFrame | None):
    """Time-safe ADX and volume-ratio lookups, computed once per stock."""
    adx_of = vol_of = None
    if h1 is not None and len(h1) > 30:
        adx_of = _asof(h1.index, adx(h1).to_numpy(), pd.Timedelta(hours=1))
    if m15 is not None and "Volume" in m15:
        vol = m15["Volume"].astype(float)
        ratio = (vol / vol.shift(1).rolling(20, min_periods=10).mean()).to_numpy()
        idx = {ts: k for k, ts in enumerate(m15.index)}
        vol_of = lambda ts: (None if ts not in idx or np.isnan(ratio[idx[ts]]) else float(ratio[idx[ts]]))
    return adx_of, vol_of


def build_context(day, daily: pd.DataFrame, adx_of=None, vol_of=None) -> DayContext:
    prior = daily[daily.index.date < day]
    ctx = DayContext(adx_of=adx_of, vol_of=vol_of)
    if len(prior):
        p = prior.iloc[-1]
        ctx.cpr_width = cpr_width_pct(float(p.High), float(p.Low), float(p.Close))
        n7 = nr7(prior.tail(10))
        ctx.nr7 = bool(n7.iloc[-1]) if len(n7) >= 7 and not pd.isna(n7.iloc[-1]) else None
    return ctx


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


def run_day(day_bars: pd.DataFrame, lv: Camarilla, rules: IntradayRules, trend_of=lambda t: "unknown",
            ctx: DayContext | None = None):
    """Simulate one day. Returns (trade dict or None, status dict for the scanner)."""
    ctx = ctx or DayContext()
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
            feat = {"trend": tr,
                    "adx": ctx.adx_of(ts + BAR) if ctx.adx_of else None,
                    "vol_ratio": ctx.vol_of(ts) if ctx.vol_of else None,
                    "nr7": ctx.nr7, "cpr_width": ctx.cpr_width,
                    "with_trend": (side == "BUY" and tr == "up") or (side == "SELL" and tr == "down")}
            why = None
            if rules.trend_filter and not feat["with_trend"]:
                why = f"1h trend is {tr}"
            elif rules.min_adx and (feat["adx"] is None or feat["adx"] < rules.min_adx):
                why = f"ADX {feat['adx'] or 0:.0f} < {rules.min_adx:g}"
            elif rules.min_volume_ratio and (feat["vol_ratio"] is None or feat["vol_ratio"] < rules.min_volume_ratio):
                why = f"volume only {feat['vol_ratio'] or 0:.1f}× average"
            elif rules.nr7_only and not ctx.nr7:
                why = "previous day wasn't NR7"
            elif rules.max_cpr_width and (ctx.cpr_width is None or ctx.cpr_width > rules.max_cpr_width):
                why = f"CPR width {ctx.cpr_width or 0:.2f}% too wide"
            if why:
                skipped = f"{side} setup at {close_t:%H:%M} skipped — {why}"
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
                     "R3": r3, "R4": r4, "S3": s3, "S4": s4, **feat}
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
        risk = abs(trade["entry"] - trade["stop"]) * qty          # Tharp's R: money lost if the stop is hit
        trade["R"] = trade["net"] / risk if risk else None
        status["result"] = trade["reason"]
    return trade, status


def backtest(symbol: str, m15: pd.DataFrame, daily: pd.DataFrame, h1: pd.DataFrame | None,
             rules: IntradayRules) -> pd.DataFrame:
    trend_of = _trend_lookup(h1, rules.swing_n)
    adx_of, vol_of = lookups(h1, m15)
    rows = []
    for day, bars in m15.groupby(m15.index.date):
        lv = _prev_day_levels(daily, day)
        if lv is None or lv.range <= 0:
            continue
        t, _ = run_day(bars, lv, rules, trend_of, build_context(day, daily, adx_of, vol_of))
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
    _, st = run_day(bars, lv, rules, _trend_lookup(h1, rules.swing_n), build_context(last_day, daily, *lookups(h1, m15)))
    st.update(symbol=symbol, day=last_day, last=float(bars.iloc[-1].Close),
              last_time=(bars.index[-1] + BAR).strftime("%H:%M"))
    return st


def baseline(symbol: str, m15: pd.DataFrame, daily: pd.DataFrame, rules: IntradayRules) -> pd.DataFrame:
    """Benchmark (Aronson): BUY every day at the 9:45 close, same target, hard stop and square-off.
    If the R3 rule can't beat this, the levels aren't adding anything."""
    rows = []
    for day, bars in m15.groupby(m15.index.date):
        if _prev_day_levels(daily, day) is None:
            continue
        first = bars[[t.time() >= rules.first_bar_start for t in bars.index]]
        if first.empty:
            continue
        entry = float(first.iloc[0].Close)
        tgt, stop = entry * (1 + rules.target_pct / 100), entry * (1 - rules.stop_pct / 100)
        exit_px, reason = None, None
        for ts, b in first.iloc[1:].iterrows():
            if b.Low <= stop:
                exit_px, reason = stop, "Stop loss"
            elif b.High >= tgt:
                exit_px, reason = tgt, "Target"
            elif (ts + BAR).time() >= rules.square_off:
                exit_px, reason = float(b.Close), "Square-off"
            if exit_px is not None:
                break
        if exit_px is None:
            continue
        qty = int(rules.capital_per_trade // entry)
        if qty <= 0:
            continue
        gross = (exit_px - entry) * qty
        net = gross - round_trip_cost(qty * entry, qty * exit_px, "intraday")
        rows.append({"symbol": symbol, "date": day, "side": "BUY", "entry": entry, "exit": exit_px,
                     "reason": reason, "gross": gross, "net": net, "R": net / ((entry - stop) * qty)})
    return pd.DataFrame(rows)


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
