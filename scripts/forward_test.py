"""Daily forward test — runs after market close (GitHub Actions) and records what the frozen rules did.

Why: a backtest can be tuned to the past without meaning to. A forward test only uses days that
happen AFTER the rules were frozen, so it's the honest accuracy check (Aronson; Pardo).

Writes
  data/forward_intraday.csv  one row per trade per variant, appended daily (never rewritten)
  data/forward_days.csv      which days were checked and how many setups appeared
  data/forward_swing.csv     swing trades entered on or after START_DATE (recomputed daily)

Variants
  class  — the class rules exactly (1h trend filter ON)
  raw    — every R3/S3 breakout with no trend filter, so filters can be judged later
"""

import sys
from dataclasses import replace
from datetime import date
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from zigma import data, intraday, swing  # noqa: E402

RULES_VERSION = "v1"                 # bump if you change the rules — results are kept separate
START_DATE = date(2026, 11, 12)      # forward test starts here; nothing before counts
UNIVERSE = "Nifty 100"
OUT = ROOT / "data"
CLASS_RULES = intraday.IntradayRules()                       # defaults = the class rules
VARIANTS = {"class": CLASS_RULES, "raw": replace(CLASS_RULES, trend_filter=False)}


def main():
    OUT.mkdir(exist_ok=True)
    uni, note = data.universe(UNIVERSE)
    syms = uni.symbol.tolist()
    print(note)
    m15 = data.prices(syms, "5d", "15m")
    h1 = data.prices(syms, "180d", "1h")
    d1 = data.prices(syms, "6mo", "1d")
    print(f"prices: 15m {len(m15)}, 1h {len(h1)}, 1d {len(d1)} of {len(syms)}")
    if not m15:
        print("No intraday data — nothing to do.")
        return

    log_path, days_path = OUT / "forward_intraday.csv", OUT / "forward_days.csv"
    log = pd.read_csv(log_path) if log_path.exists() else pd.DataFrame()
    done = set(zip(log.date.astype(str), log.symbol, log.variant)) if not log.empty else set()
    days_log = pd.read_csv(days_path) if days_path.exists() else pd.DataFrame(columns=["date", "checked", "class", "raw"])
    today = pd.Timestamp.now(tz="Asia/Kolkata")

    new_rows, day_counts = [], {}
    for s in syms:
        if s not in m15 or s not in d1:
            continue
        bars_all = m15[s]
        trend_of = intraday._trend_lookup(h1.get(s), CLASS_RULES.swing_n)
        adx_of, vol_of = intraday.lookups(h1.get(s), bars_all)
        for day, bars in bars_all.groupby(bars_all.index.date):
            if day < START_DATE:
                continue
            last = bars.index[-1] + intraday.BAR
            if day == today.date() and last.time() < CLASS_RULES.square_off:
                continue                         # day not finished yet
            lv = intraday._prev_day_levels(d1[s], day)
            if lv is None or lv.range <= 0:
                continue
            ctx = intraday.build_context(day, d1[s], adx_of, vol_of)
            c = day_counts.setdefault(str(day), {"checked": 0, "class": 0, "raw": 0})
            c["checked"] += 1
            for name, rules in VARIANTS.items():
                if (str(day), s, name) in done:
                    continue
                t, _ = intraday.run_day(bars, lv, rules, trend_of, ctx)
                if t is None or t["reason"].startswith("Open"):
                    continue
                c[name] += 1
                new_rows.append({"date": str(day), "symbol": s, "variant": name, "rules_version": RULES_VERSION,
                                 "side": t["side"], "entry_time": t["entry_time"].strftime("%H:%M"),
                                 "entry": round(t["entry"], 2), "target": round(t["target"], 2),
                                 "stop": round(t["stop"], 2), "exit": round(t["exit"], 2),
                                 "exit_time": t["exit_time"].strftime("%H:%M"), "reason": t["reason"],
                                 "qty": t["qty"], "gross": round(t["gross"], 2), "charges": round(t["charges"], 2),
                                 "net": round(t["net"], 2), "net_pct": round(t["net_pct"], 3),
                                 "R": round(t["R"], 3) if t["R"] is not None else None,
                                 "trend": t["trend"], "with_trend": t["with_trend"],
                                 "adx": None if t["adx"] is None else round(t["adx"], 1),
                                 "vol_ratio": None if t["vol_ratio"] is None else round(t["vol_ratio"], 2),
                                 "nr7": t["nr7"], "cpr_width": None if t["cpr_width"] is None else round(t["cpr_width"], 3)})

    if new_rows:
        log = pd.concat([log, pd.DataFrame(new_rows)], ignore_index=True)
        log.sort_values(["date", "symbol", "variant"]).to_csv(log_path, index=False)
    known = set(days_log.date.astype(str))
    add = [{"date": d, **v} for d, v in day_counts.items() if d not in known]
    if add:
        days_log = pd.concat([days_log, pd.DataFrame(add)], ignore_index=True).sort_values("date")
        days_log.to_csv(days_path, index=False)
    print(f"intraday: +{len(new_rows)} trades, +{len(add)} days")

    # swing: recompute from daily history, keep trades entered on/after START_DATE
    d2 = data.prices(syms, "2y", "1d")
    sw = [swing.backtest(s, d2[s], swing.SwingRules()) for s in syms if s in d2]
    sw = [t for t in sw if not t.empty]
    if sw:
        st = pd.concat(sw, ignore_index=True)
        st = st[pd.to_datetime(st.entry_date).dt.date >= START_DATE]
        st.assign(rules_version=RULES_VERSION).to_csv(OUT / "forward_swing.csv", index=False)
        print(f"swing: {len(st)} trades since {START_DATE}")


if __name__ == "__main__":
    main()
