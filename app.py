"""Zigma trader — the Zigma Traders Class rules as a testable app.

Pages
  Today          intraday Camarilla scanner (after 9:45) + swing Fibonacci scanner
  Backtest       test the rules on past data, net of charges
  Fundamentals   long-term checklist from the 23/11 class
  Paper trades   journal + "ready for real money?" check
  Rules          every rule the app uses, in plain words
"""

from datetime import datetime, time

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from zigma import data, fundamentals, intraday, journal, stats, swing
from zigma.charges import round_trip_cost
from zigma.levels import Camarilla

st.set_page_config(page_title="Zigma trader", page_icon="📈", layout="centered")

NAVY, PINK, GREEN, RED, GREY = "#1E1B5E", "#E8318F", "#1D9E75", "#D14343", "#8C8A99"
CFG = {"displayModeBar": False}
UNIVERSES = ["Nifty 50", "Nifty Next 50", "Nifty 100", "Nifty 200", "Nifty 500", "All NSE equities"]


def now_ist() -> datetime:
    return pd.Timestamp.now(tz="Asia/Kolkata").to_pydatetime()


def inr(v) -> str:
    try:
        v = float(v)
    except (TypeError, ValueError):
        return "—"
    sign = "-" if v < 0 else ""
    v = abs(round(v))
    s = str(int(v))
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        head = ",".join([head[max(i - 2, 0):i] for i in range(len(head), 0, -2)][::-1])
        s = f"{head},{tail}"
    return f"{sign}₹{s}"


# ------------------------------------------------------------------ cached data
@st.cache_data(ttl=6 * 3600, show_spinner=False)
def get_universe(name):
    return data.universe(name)


@st.cache_data(ttl=600, show_spinner=False)
def get_prices(symbols: tuple, period: str, interval: str):
    return data.prices(list(symbols), period, interval)


@st.cache_data(ttl=24 * 3600, show_spinner=False)
def get_fundamentals(symbol: str):
    return data.fundamentals(symbol)


def pick_universe(key: str, default="Nifty 50", max_n=None):
    name = st.selectbox("Stocks to check", UNIVERSES, index=UNIVERSES.index(default), key=f"u_{key}")
    uni, note = get_universe(name)
    st.caption(note)
    syms = uni.symbol.tolist()
    if max_n and len(syms) > max_n:
        st.warning(f"That's {len(syms)} stocks — checking the first {max_n} to stay within free data limits.")
        syms = syms[:max_n]
    extra = st.text_input("Or type symbols (comma separated)", key=f"x_{key}",
                          placeholder="e.g. RELIANCE, TCS, INFY").strip()
    if extra:
        syms = [s.strip().upper() for s in extra.split(",") if s.strip()]
    return syms, uni


def intraday_rules_form(key: str) -> intraday.IntradayRules:
    with st.expander("Rules (from your class notes — change to experiment)"):
        c1, c2 = st.columns(2)
        target = c1.number_input("Target %", 0.2, 5.0, 1.0, 0.1, key=f"t_{key}")
        stop = c2.number_input("Hard stop %", 0.2, 5.0, 1.0, 0.1, key=f"s_{key}")
        mode = st.radio("Stop loss", ["Close back inside R3/S3 (+ hard stop)", "Hard stop only"],
                        key=f"m_{key}", horizontal=True)
        c3, c4 = st.columns(2)
        trend = c3.toggle("Trade only with 1h trend", True, key=f"tr_{key}")
        short = c4.toggle("Allow sell (short) side", True, key=f"sh_{key}")
        cap = st.number_input("Money per trade (₹)", 1000, 1_000_000, 10_000, 1000, key=f"c_{key}")
        st.markdown("**Extra filters from the books** (off = 0)")
        c5, c6 = st.columns(2)
        min_adx = c5.number_input("Min ADX (Wilder)", 0.0, 60.0, 0.0, 5.0, key=f"adx_{key}",
                                  help="ADX below 20 = no real trend. 0 = off.")
        min_vol = c6.number_input("Min volume × avg (Murphy)", 0.0, 5.0, 0.0, 0.5, key=f"vol_{key}",
                                  help="Breakout candle volume vs the previous 20 candles. 0 = off.")
        c7, c8 = st.columns(2)
        nr7_only = c7.toggle("Only after NR7 day (Crabel)", False, key=f"nr7_{key}")
        max_cpr = c8.number_input("Max CPR width % (Ochoa)", 0.0, 2.0, 0.0, 0.25, key=f"cpr_{key}",
                                  help="Narrow CPR hints at a trending day. 0 = off.")
    return intraday.IntradayRules(target_pct=target, stop_pct=stop, trend_filter=trend, allow_short=short,
                                  stop_mode="close_back_inside" if mode.startswith("Close") else "fixed",
                                  capital_per_trade=float(cap), min_adx=min_adx, min_volume_ratio=min_vol,
                                  nr7_only=nr7_only, max_cpr_width=max_cpr)


def level_chart(sym, bars, lv: Camarilla, title=""):
    fig = go.Figure(go.Candlestick(x=bars.index, open=bars.Open, high=bars.High, low=bars.Low, close=bars.Close,
                                   increasing_line_color=GREEN, decreasing_line_color=RED, name=sym))
    for name, val, col in [("R4", lv.r(4), RED), ("R3", lv.r(3), PINK), ("S3", lv.s(3), NAVY), ("S4", lv.s(4), GREY)]:
        fig.add_hline(y=val, line_dash="dot", line_color=col, annotation_text=f"{name} {val:.1f}",
                      annotation_position="right")
    fig.update_layout(height=320, margin=dict(l=0, r=60, t=30, b=0), title=title, xaxis_rangeslider_visible=False,
                      showlegend=False)
    st.plotly_chart(fig, width="stretch", config=CFG)


def log_button(row: dict, key: str):
    if st.button("📝 Log as paper trade", key=key):
        st.session_state["prefill"] = row
        st.switch_page(PAGES["journal"])


# ------------------------------------------------------------------ pages
def page_today():
    st.title("Today")
    now = now_ist()
    st.caption(f"{now:%a %d %b %Y, %H:%M} IST · Yahoo prices may lag a few minutes — confirm on your broker app.")
    mode = st.segmented_control("Strategy", ["Intraday (Camarilla)", "Swing (Fibonacci)"],
                                default="Intraday (Camarilla)") or "Intraday (Camarilla)"
    syms, _ = pick_universe("today", max_n=200)

    if mode.startswith("Intraday"):
        rules = intraday_rules_form("today")
        if now.time() < time(9, 45) and now.weekday() < 5:
            st.info("Before 9:45 — no trades yet (first 30 minutes are too volatile). Levels below are for today.")
        if not st.button("Scan now", type="primary"):
            st.caption("Tap **Scan now** after 9:45. It checks every stock against your entry rules.")
            return
        with st.spinner(f"Fetching {len(syms)} stocks…"):
            m15 = get_prices(tuple(syms), "5d", "15m")
            h1 = get_prices(tuple(syms), "60d", "1h")
            d1 = get_prices(tuple(syms), "3mo", "1d")
        rows = []
        for s in syms:
            if s in m15 and s in d1:
                rows.append(intraday.today_status(s, m15[s], d1[s], h1.get(s), rules))
        if not rows:
            st.error("Couldn't download prices. Yahoo may be busy — try again in a minute.")
            return
        df = pd.DataFrame(rows)
        sig = df[df.get("signal", "").astype(str) != ""] if "signal" in df else df.iloc[0:0]
        day = df["day"].dropna().max() if "day" in df else None
        if day is not None and day != now.date():
            st.info(f"Market isn't open now — showing the last session ({day:%a %d %b}).")
        st.metric("Stocks with a setup", len(sig), f"of {len(df)} checked", delta_color="off")
        if sig.empty:
            st.success("No stock meets all the rules right now. No trade is a valid decision.")
        for _, r in sig.iterrows():
            with st.container(border=True):
                st.markdown(f"**{r.symbol}** · {r.signal} · {r.note}")
                c1, c2, c3 = st.columns(3)
                c1.metric("Entry", f"{r.entry:.2f}")
                c2.metric("Target", f"{r.target:.2f}")
                c3.metric("Stop", f"{r.stop:.2f}")
                if isinstance(r.get("result"), str):
                    st.caption(f"Status now: {r.result} (last {r['last']:.2f} at {r.last_time})")
                bars = m15[r.symbol][m15[r.symbol].index.date == r.day]
                lv = intraday._prev_day_levels(d1[r.symbol], r.day)
                with st.expander("Chart"):
                    level_chart(r.symbol, bars, lv)
                qty = int(rules.capital_per_trade // r.entry)
                log_button({"date": str(r.day), "symbol": r.symbol, "strategy": "Intraday", "side": r.signal,
                            "entry": round(r.entry, 2), "stop": round(r.stop, 2), "target": round(r.target, 2),
                            "qty": qty}, key=f"log_{r.symbol}")
        with st.expander(f"All {len(df)} stocks"):
            show = df[["symbol", "last", "R3", "S3", "note"]].copy()
            st.dataframe(show, hide_index=True, width="stretch",
                         column_config={c: st.column_config.NumberColumn(format="%.2f") for c in ["last", "R3", "S3"]})
    else:
        rules = swing.SwingRules()
        if not st.button("Scan now", type="primary"):
            st.caption("Swing setups use daily candles — scan any time, ideally after 3:30 PM.")
            return
        with st.spinner(f"Fetching {len(syms)} stocks…"):
            d1 = get_prices(tuple(syms), "1y", "1d")
        rows = [swing.today_status(s, d1[s], rules) for s in syms if s in d1]
        df = pd.DataFrame(rows)
        if df.empty:
            st.error("Couldn't download prices. Try again in a minute.")
            return
        zone = df[df.get("signal", pd.Series(dtype=str)).astype(str).str.contains("ZONE", na=False)] if "signal" in df else df.iloc[0:0]
        ups = (df.trend == "up").sum() if "trend" in df else 0
        a, b = st.columns(2)
        a.metric("In an uptrend", int(ups))
        b.metric("In the buy zone", len(zone))
        for _, r in zone.iterrows():
            with st.container(border=True):
                st.markdown(f"**{r.symbol}** · {r.signal} · {r.note}" + (f" · {r.pattern}" if r.pattern else ""))
                c1, c2, c3 = st.columns(3)
                c1.metric("Price", f"{r['last']:.2f}")
                c2.metric("Target", f"{r.target:.2f}")
                c3.metric("Stop (0.786)", f"{r.stop:.2f}")
                qty = int(rules.capital_per_trade // r["last"])
                log_button({"date": str(r.day), "symbol": r.symbol, "strategy": "Swing", "side": "BUY",
                            "entry": round(r["last"], 2), "stop": round(r.stop, 2), "target": round(r.target, 2),
                            "qty": qty}, key=f"slog_{r.symbol}")
        with st.expander(f"All {len(df)} stocks"):
            st.dataframe(df[[c for c in ["symbol", "last", "trend", "note"] if c in df]], hide_index=True, width="stretch")


def page_backtest():
    st.title("Backtest")
    st.caption("How would the class rules have done in the past? Results include brokerage, STT, GST and other charges.")
    mode = st.segmented_control("Strategy", ["Intraday (Camarilla)", "Swing (Fibonacci)"],
                                default="Intraday (Camarilla)", key="bt_mode") or "Intraday (Camarilla)"
    syms, _ = pick_universe("bt", max_n=200)
    if mode.startswith("Intraday"):
        rules = intraday_rules_form("bt")
        st.caption("Free data covers the **last ~60 days** of 15-minute candles.")
    else:
        rules = swing.SwingRules()
        with st.expander("Rules"):
            rules.need_pattern = st.toggle("Require hammer / bullish engulfing on entry day", False)
            rules.max_days = st.slider("Max holding days", 5, 40, 20)
            rules.capital_per_trade = float(st.number_input("Money per trade (₹)", 1000, 1_000_000, 10_000, 1000))
        st.caption("Uses **2 years** of daily candles.")
    if not st.button("Run backtest", type="primary"):
        return

    trades = []
    prog = st.progress(0.0, text="Downloading prices…")
    if mode.startswith("Intraday"):
        m15 = get_prices(tuple(syms), "60d", "15m")
        h1 = get_prices(tuple(syms), "180d", "1h")
        d1 = get_prices(tuple(syms), "6mo", "1d")
        base = []
        for i, s in enumerate(syms):
            prog.progress((i + 1) / len(syms), text=f"Testing {s}")
            if s in m15 and s in d1:
                t = intraday.backtest(s, m15[s], d1[s], h1.get(s), rules)
                if not t.empty:
                    trades.append(t)
                b = intraday.baseline(s, m15[s], d1[s], rules)
                if not b.empty:
                    base.append(b)
    else:
        d1 = get_prices(tuple(syms), "2y", "1d")
        for i, s in enumerate(syms):
            prog.progress((i + 1) / len(syms), text=f"Testing {s}")
            if s in d1:
                t = swing.backtest(s, d1[s], rules)
                if not t.empty:
                    trades.append(t)
    prog.empty()
    if not trades:
        st.warning("No trades matched the rules (or prices couldn't be downloaded).")
        return
    tr = pd.concat(trades, ignore_index=True)
    date_col = "entry_time" if "entry_time" in tr else "entry_date"
    tr = tr.sort_values(date_col).reset_index(drop=True)
    s = intraday.summarize(tr)
    st.session_state["last_backtest"] = tr

    c1, c2 = st.columns(2)
    c1.metric("Trades", s["trades"])
    c2.metric("Win rate (after charges)", f"{s['win_rate']:.0%}")
    c3, c4 = st.columns(2)
    c3.metric("Net profit", inr(s["net"]), f"charges {inr(s['charges'])}", delta_color="off")
    c4.metric("Per trade", inr(s["expectancy"]), help="Average net result per trade")
    c5, c6 = st.columns(2)
    c5.metric("Avg win", inr(s["avg_win"]))
    c6.metric("Avg loss", inr(s["avg_loss"]))

    lo, hi = stats.wilson(int((tr.net > 0).sum()), len(tr))
    rs = stats.r_stats(tr)
    ts_ = stats.t_stat(tr.net)
    st.markdown("#### How sure can we be?")
    sure = [f"True win rate is likely between **{lo:.0%} and {hi:.0%}** (95% range on {len(tr)} trades). "
            "The fewer the trades, the wider this range."]
    if rs:
        sure.append(f"Expectancy **{rs['expectancy_R']:+.2f}R** per trade — wins average {rs['avg_win_R']:+.2f}R, "
                    f"losses {rs['avg_loss_R']:+.2f}R (R = what you lose if the stop is hit; Van Tharp).")
    if ts_ is not None:
        sure.append(f"Average result is **{ts_:.1f}** standard errors from zero — "
                    + ("unlikely to be luck." if abs(ts_) >= 2 else "could easily be luck (needs ≥ 2)."))
    st.markdown("\n".join(f"- {x}" for x in sure))

    st.markdown("#### Verdict")
    claim = 0.80
    lines = []
    if mode.startswith("Intraday"):
        lines.append(f"Class notes claim about **{claim:.0%}** accuracy for R3/S3 breakouts. This test: "
                     f"**{s['win_rate']:.0%}** winning trades after charges.")
    if s["net"] > 0 and s["profit_factor"] > 1.2 and s["trades"] >= 50:
        lines.append("✅ Profitable over a decent sample. Next: paper-trade it live for 2–3 months.")
    elif s["net"] > 0:
        lines.append("🟠 Profitable, but the edge is thin or the sample is small — not proof yet.")
    else:
        lines.append("🔴 Lost money after charges. Change the rules or don't trade this setup.")
    if s["gross"] > 0 and s["net"] <= 0:
        lines.append(f"Before charges it made **{inr(s['gross'])}** — charges of **{inr(s['charges'])}** "
                     "turned that into a loss. Small trades are hit hardest.")
    elif s["gross"] > 0 and s["charges"] > s["gross"] * 0.3:
        lines.append(f"Charges took **{s['charges'] / s['gross']:.0%}** of the gross profit.")
    elif s["gross"] <= 0:
        lines.append(f"It lost money even before charges; charges added another **{inr(s['charges'])}**.")
    lines.append(f"Worst losing streak cost **{inr(s['max_drawdown'])}**.")
    st.markdown("\n".join(f"- {x}" for x in lines))

    if mode.startswith("Intraday") and base:
        b = pd.concat(base, ignore_index=True)
        st.markdown("#### Does the R3 rule beat a simple benchmark?")
        bw = (b.net > 0).mean()
        st.markdown(f"Benchmark: **buy every stock at 9:45 every day**, same target and stop (David Aronson's idea — "
                    f"a rule only has value if it beats doing something simple). Benchmark: {len(b)} trades, "
                    f"win rate **{bw:.0%}**, average **{inr(b.net.mean())}** per trade. "
                    f"Your rules: win rate **{s['win_rate']:.0%}**, average **{inr(s['expectancy'])}**.")
        if s["expectancy"] > b.net.mean():
            st.success("The rules did better than the benchmark per trade.")
        else:
            st.error("The rules did no better than buying at 9:45 — the levels aren't adding value here.")

        imp = stats.filter_impact(tr)
        if not imp.empty:
            st.markdown("#### Which filter helps?")
            st.caption("Every trade records these readings. Compare trades that passed each filter with those that "
                       "didn't — keep a filter only if the 'pass' row is clearly better on a decent number of trades.")
            st.dataframe(imp, hide_index=True, width="stretch", column_config={
                "win_rate": st.column_config.ProgressColumn("win rate", format="percent", min_value=0, max_value=1),
                "avg_R": st.column_config.NumberColumn("avg R", format="%.2f"),
                "net": st.column_config.NumberColumn(format="₹%d")})

    halves = stats.split_halves(tr, date_col)
    if len(tr) >= 20:
        st.markdown("#### First half vs second half")
        st.caption("Robert Pardo's check: a real edge shows up in both halves, not just one lucky stretch.")
        st.dataframe(halves, hide_index=True, width="stretch", column_config={
            "win_rate": st.column_config.ProgressColumn("win rate", format="percent", min_value=0, max_value=1),
            "net": st.column_config.NumberColumn(format="₹%d")})

    st.markdown("#### Running profit")
    curve = tr.net.cumsum()
    fig = go.Figure(go.Scatter(x=tr[date_col], y=curve, mode="lines", line=dict(color=PINK, width=2)))
    fig.add_hline(y=0, line_color=GREY)
    fig.update_layout(height=260, margin=dict(l=0, r=0, t=10, b=0), yaxis_tickprefix="₹")
    st.plotly_chart(fig, width="stretch", config=CFG)

    st.markdown("#### By stock")
    by = tr.groupby("symbol").agg(trades=("net", "size"), win_rate=("net", lambda x: (x > 0).mean()),
                                  net=("net", "sum")).sort_values("net", ascending=False).reset_index()
    by["screener"] = by.symbol.map(data.screener_url)
    st.dataframe(by, hide_index=True, width="stretch", column_config={
        "win_rate": st.column_config.ProgressColumn("win rate", format="percent", min_value=0, max_value=1),
        "net": st.column_config.NumberColumn(format="₹%d"),
        "screener": st.column_config.LinkColumn("Screener", display_text="open")})
    st.caption("Careful: picking the top stocks from a backtest and expecting the same next month is the most "
               "common trap. Use this to drop stocks that clearly don't suit the setup, then paper-trade the rest.")

    with st.expander("By exit reason"):
        st.dataframe(tr.groupby("reason").agg(trades=("net", "size"), net=("net", "sum")).reset_index(),
                     hide_index=True, width="stretch",
                     column_config={"net": st.column_config.NumberColumn(format="₹%d")})
    with st.expander(f"All {len(tr)} trades"):
        cols = [c for c in ["symbol", date_col, "side", "entry", "exit", "reason", "qty", "net", "R", "trend", "adx",
                            "vol_ratio", "nr7", "cpr_width"] if c in tr]
        st.dataframe(tr[cols], hide_index=True, width="stretch")
    st.download_button("Download trades (CSV)", tr.to_csv(index=False), "backtest_trades.csv", "text/csv")


def page_fundamentals():
    st.title("Fundamentals")
    st.caption("Long-term investment checklist from the 23/11 class. Fundamentals are for investing (years), not trading.")
    syms, uni = pick_universe("fa", max_n=100)
    with st.expander("The checklist"):
        st.markdown("\n".join(f"- {c}" for c in fundamentals.CHECKS) +
                    "\n\nYahoo doesn't split FII vs DII, so total institutional holding > 20% stands in for "
                    "'FII > 10% and DII > 10%'. Tap **Screener** on any row to see the exact split. "
                    "Debt rules are skipped for banks and NBFCs — borrowing is their business.")
    if not st.button("Check fundamentals", type="primary"):
        st.caption("Takes about 1–2 seconds per stock the first time, then it's cached for a day.")
        return
    rows = []
    prog = st.progress(0.0)
    for i, s in enumerate(syms):
        prog.progress((i + 1) / len(syms), text=f"Reading {s}")
        try:
            rows.append(get_fundamentals(s))
        except Exception:
            continue
    prog.empty()
    if not rows:
        st.error("Couldn't read company data. Yahoo may be busy — try again later.")
        return
    m = pd.DataFrame(rows)
    ind_map = dict(zip(uni.symbol, uni.industry)) if "industry" in uni else {}
    m["industry"] = m.symbol.map(ind_map).replace("", pd.NA).fillna(m.yf_industry)
    pe_med = m[m.pe > 0].groupby("industry").pe.median()
    peers = m.groupby("industry").symbol.count()
    out = []
    for _, r in m.iterrows():
        ipe = pe_med.get(r.industry) if peers.get(r.industry, 0) >= 3 else None
        ev = fundamentals.evaluate(r.to_dict(), ipe)
        out.append({"symbol": r.symbol, "name": r["name"], "slab": ev["slab"],
                    "score": f"{ev['score']}/{ev['checked']}", "_s": ev["score"] / max(ev["checked"], 1),
                    "P/E": r.pe, "industry P/E": ipe, "debt %": ev["debt_pct"],
                    "promoters %": (r.insiders or 0) * 100 if r.insiders is not None else None,
                    "institutions %": (r.institutions or 0) * 100 if r.institutions is not None else None,
                    **{c: ("✅" if ev[c] else "❌") if ev[c] is not None else "–" for c in fundamentals.CHECKS},
                    "screener": data.screener_url(r.symbol)})
    res = pd.DataFrame(out).sort_values("_s", ascending=False).drop(columns="_s")
    top = res[res.score.str.split("/").map(lambda x: int(x[0]) >= max(int(x[1]) - 1, 1))]
    st.metric("Pass all or all-but-one checks", len(top), f"of {len(res)}", delta_color="off")
    num = {c: st.column_config.NumberColumn(format="%.1f") for c in ["P/E", "industry P/E", "debt %", "promoters %", "institutions %"]}
    st.dataframe(res, hide_index=True, width="stretch",
                 column_config={**num, "screener": st.column_config.LinkColumn("Screener", display_text="open")})
    st.download_button("Download (CSV)", res.to_csv(index=False), "fundamentals.csv", "text/csv")
    st.caption("Data from Yahoo Finance, annual statements (up to 4 years). Always confirm on Screener and the "
               "annual report before investing. This is a checklist, not a recommendation.")


def _notion():
    try:
        tok, db = st.secrets.get("NOTION_TOKEN"), st.secrets.get("NOTION_DATABASE_ID")
    except Exception:
        tok = db = None
    return journal.Notion(tok, db) if tok and db else None


def page_journal():
    st.title("Paper trades")
    nt = _notion()
    if "journal" not in st.session_state:
        st.session_state.journal = nt.all() if nt else journal.empty()
    st.caption("Saved to Notion." if nt else
               "Saved in this session only — download the CSV at the end of the day and upload it next time.")
    if not nt:
        up = st.file_uploader("Load your journal CSV", type=["csv"])
        if up is not None and st.session_state.get("_loaded") != up.name:
            st.session_state.journal = journal.load_csv(up)
            st.session_state._loaded = up.name

    pre = st.session_state.pop("prefill", None) or {}
    with st.form("add", clear_on_submit=True):
        st.markdown("**Add a paper trade**")
        c1, c2 = st.columns(2)
        sym = c1.text_input("Symbol", pre.get("symbol", "")).upper()
        d = c2.date_input("Date", pd.to_datetime(pre.get("date", now_ist().date())))
        c3, c4 = st.columns(2)
        strat = c3.selectbox("Strategy", ["Intraday", "Swing", "Positional"],
                             index=["Intraday", "Swing", "Positional"].index(pre.get("strategy", "Intraday")))
        side = c4.selectbox("Side", ["BUY", "SELL"], index=0 if pre.get("side", "BUY") == "BUY" else 1)
        c5, c6, c7 = st.columns(3)
        entry = c5.number_input("Entry", 0.0, value=float(pre.get("entry", 0.0)), format="%.2f")
        stop = c6.number_input("Stop", 0.0, value=float(pre.get("stop", 0.0)), format="%.2f")
        target = c7.number_input("Target", 0.0, value=float(pre.get("target", 0.0)), format="%.2f")
        c8, c9 = st.columns(2)
        qty = c8.number_input("Qty", 0, value=int(pre.get("qty", 0)))
        exit_px = c9.number_input("Exit (0 if still open)", 0.0, format="%.2f")
        ok = st.checkbox("I followed every rule (time, trend, levels, stop)", True)
        c10, c11 = st.columns(2)
        feel = c10.selectbox("How I felt", ["Calm", "Confident", "Anxious", "FOMO", "Revenge", "Bored"])
        mistake = c11.selectbox("Mistake", ["None", "Entered early", "Entered late", "Moved stop",
                                            "Exited early", "Ignored trend", "Too big size", "Not in plan"])
        notes = st.text_input("Notes", placeholder="Why I took it / what I learned")
        if st.form_submit_button("Save", type="primary"):
            if not sym or entry <= 0 or qty <= 0:
                st.error("Enter a symbol, entry price and quantity.")
            else:
                row = {"date": str(d), "symbol": sym, "strategy": strat, "side": side, "entry": entry,
                       "stop": stop, "target": target, "exit": exit_px or None, "qty": qty,
                       "status": "Closed" if exit_px else "Open", "followed_rules": ok,
                       "notes": f"[{feel} | {mistake}] {notes}".strip()}
                row["net"] = journal.compute_net(row, round_trip_cost) if exit_px else None
                st.session_state.journal = pd.concat([st.session_state.journal, pd.DataFrame([row])],
                                                     ignore_index=True)
                if nt and not nt.add(row):
                    st.warning("Saved here, but Notion didn't accept it — check the database properties.")
                st.success(f"Saved {sym}.")

    j = st.session_state.journal.copy()
    if j.empty:
        st.info("No paper trades yet. Use **Today → Log as paper trade**, or add one above.")
        return
    j["net"] = pd.to_numeric(j.net, errors="coerce")
    closed = j[j.status == "Closed"]
    st.markdown("#### Results")
    c1, c2 = st.columns(2)
    c1.metric("Closed trades", len(closed))
    c2.metric("Win rate", f"{(closed.net > 0).mean():.0%}" if len(closed) else "—")
    c3, c4 = st.columns(2)
    c3.metric("Net after charges", inr(closed.net.sum()) if len(closed) else "—")
    fr = j.followed_rules.astype(str).str.lower().isin(["true", "1", "yes"]).mean()
    c4.metric("Followed the rules", f"{fr:.0%}")

    st.markdown("#### Ready for real money?")
    weeks = (pd.to_datetime(closed.date).max() - pd.to_datetime(closed.date).min()).days / 7 if len(closed) > 1 else 0
    checks = [
        (len(closed) >= 50, f"At least 50 closed paper trades ({len(closed)} so far)"),
        (weeks >= 8, f"Over at least 8 weeks ({weeks:.0f} so far)"),
        (len(closed) > 0 and closed.net.sum() > 0, "Profitable after charges"),
        (fr >= 0.9, "Followed the rules on 90%+ of trades"),
    ]
    for passed, text in checks:
        st.markdown(f"{'✅' if passed else '⬜'} {text}")
    if all(p for p, _ in checks):
        st.success("All four met. If you start, use money you can afford to lose entirely — never borrowed money.")
    else:
        st.caption("Until all four are ticked, keep paper trading.")

    st.markdown("#### Journal")
    st.dataframe(j.sort_values("date", ascending=False), hide_index=True, width="stretch",
                 column_config={"net": st.column_config.NumberColumn(format="₹%d")})
    st.download_button("Download journal (CSV)", j.to_csv(index=False),
                       f"paper_trades_{now_ist():%Y%m%d}.csv", "text/csv")


def page_rules():
    st.title("Rules")
    st.markdown("""
Every rule here comes from your **Zigma Traders Class** notes. Where the notes were silent, the app adds
the minimum needed to test it — those are marked *(added)*.

#### Intraday — Camarilla breakout (06/12, 07/12, 24/01)
- Levels from the **previous day's** high, low and close: R3/S3 = C ± (H−L)×1.1/4, R4/S4 = C ± (H−L)×1.1/2.
- **No trade in the first 30 minutes.** First usable 15-minute candle closes at **9:45**.
- **BUY**: green candle closes above **R3**. **SELL**: red candle closes below **S3**.
- Price between R3 and S3 = no-trade zone. No new entries after **12:00**.
- **Don't go against the trend**: 1-hour chart must show higher highs + higher lows for buys
  (lower highs + lower lows for sells). Sideways → no trade.
- Exit at **+1%** or **R4/S4**, whichever comes first.
- *(added)* Stop loss: candle closes back inside R3/S3, plus a hard stop at 1%.
- *(added)* Square off at 15:15 if still open.

#### Swing — Fibonacci pullback (13/12, 24/01)
- Daily trend must be **up**. Fibonacci is buy-side only, never in sideways markets.
- Take the last up-move (swing low → swing high). Buy when price pulls back into **0.382–0.618** and the day closes green.
- Stop: close below **0.786** (maximum retracement). Target: extension **1.0** (character level).
- *(added)* Exit after 20 trading days (~1 month swing holding period).

#### Long-term — fundamentals (23/11)
- Market cap: small < ₹5,000 cr, mid ₹5,000–20,000 cr, large > ₹20,000 cr.
- Debt < 25% of market cap · P/E below industry · sales, profit, reserves, assets rising · borrowings falling.
- Promoters > 50%, FII > 10%, DII > 10%.

#### Optional filters from the books (off by default)
- **ADX ≥ 20–25** on the 1-hour chart — Wilder's test for a real trend.
- **Breakout volume ≥ 1.5×** the previous 20 candles — Murphy: volume confirms a breakout.
- **Previous day NR7** — Crabel: the quietest day in 7 often comes before a big move.
- **Narrow CPR** — Ochoa: a narrow central pivot range hints at a trending day.

Every trade records these readings, so the Backtest and Forward test pages can show whether each one helps
*before* you switch it on.

#### What the app can't do
- It doesn't place orders and never will — you decide every trade.
- Free data has ~60 days of 15-minute candles, so intraday backtests are short.
- Patterns from the 27–28/12 classes (double top/bottom, head & shoulders, triangles) aren't automated yet.
""")
    st.warning("SEBI found 7 in 10 individual intraday traders lost money in FY23, and 9 in 10 F&O traders lost "
               "money over FY22–FY24. Paper-trade first, start small, and never trade with borrowed money. "
               "This app is a learning tool, not investment advice.")


REPO_RAW = "https://raw.githubusercontent.com/arunkumarvel2705-sudo/zigma-trader/main/data/"


@st.cache_data(ttl=1800, show_spinner=False)
def read_forward(name: str) -> pd.DataFrame:
    from pathlib import Path
    base = Path(__file__).parent if "__file__" in globals() else Path.cwd()
    local = base / "data" / name
    try:
        return pd.read_csv(REPO_RAW + name)          # freshest copy (bot pushes daily)
    except Exception:
        return pd.read_csv(local) if local.exists() else pd.DataFrame()


def page_forward():
    st.title("Forward test")
    st.caption("Every trading day at 4:15 PM a robot runs the frozen class rules on the Nifty 100 and records every "
               "setup and its result — no hindsight, no tweaking. This is the honest accuracy test.")
    log, days = read_forward("forward_intraday.csv"), read_forward("forward_days.csv")
    if log.empty:
        st.info("No results yet. The first results appear after the next trading day's 4:15 PM run. "
                "To run it now: GitHub → zigma-trader → **Actions** → **Daily forward test** → **Run workflow**.")
        return
    n_days = days.date.nunique() if not days.empty else log.date.nunique()
    st.progress(min(n_days / 60, 1.0), text=f"{n_days} trading days recorded — target 60 (≈ 3 months)")
    variant = st.segmented_control("Rules", ["class", "raw"], default="class",
                                   format_func=lambda v: "Class rules (with 1h trend)" if v == "class" else "All breakouts (no filter)") or "class"
    v = log[log.variant == variant].copy()
    if v.empty:
        st.warning("No trades for this version yet.")
        return
    v["net"] = pd.to_numeric(v.net, errors="coerce")
    wins = int((v.net > 0).sum())
    lo, hi = stats.wilson(wins, len(v))
    c1, c2 = st.columns(2)
    c1.metric("Trades", len(v), f"{len(v) / max(n_days, 1):.1f} per day", delta_color="off")
    c2.metric("Win rate", f"{wins / len(v):.0%}", f"likely {lo:.0%}–{hi:.0%}", delta_color="off")
    c3, c4 = st.columns(2)
    c3.metric("Net after charges", inr(v.net.sum()), f"₹{int(intraday.IntradayRules().capital_per_trade):,} per trade",
              delta_color="off")
    rs = stats.r_stats(v)
    c4.metric("Expectancy", f"{rs['expectancy_R']:+.2f}R" if rs else "—", help="Average result per trade in units of risk")
    st.markdown(f"Class notes claim **80%** accuracy. Forward result so far: **{wins / len(v):.0%}** "
                f"(likely range {lo:.0%}–{hi:.0%}).")

    v = v.sort_values(["date", "entry_time"])
    fig = go.Figure(go.Scatter(x=pd.to_datetime(v.date), y=v.net.cumsum(), mode="lines", line=dict(color=PINK, width=2)))
    fig.add_hline(y=0, line_color=GREY)
    fig.update_layout(height=240, margin=dict(l=0, r=0, t=10, b=0), yaxis_tickprefix="₹")
    st.plotly_chart(fig, width="stretch", config=CFG)

    imp = stats.filter_impact(log[log.variant == "raw"].assign(net=lambda d: pd.to_numeric(d.net, errors="coerce")))
    if not imp.empty:
        st.markdown("#### Which filter helps? (all breakouts)")
        st.dataframe(imp, hide_index=True, width="stretch", column_config={
            "win_rate": st.column_config.ProgressColumn("win rate", format="percent", min_value=0, max_value=1),
            "avg_R": st.column_config.NumberColumn("avg R", format="%.2f"),
            "net": st.column_config.NumberColumn(format="₹%d")})
    by = v.groupby("symbol").agg(trades=("net", "size"), win_rate=("net", lambda x: (x > 0).mean()),
                                 net=("net", "sum")).sort_values("net", ascending=False).reset_index()
    with st.expander("By stock"):
        st.dataframe(by, hide_index=True, width="stretch", column_config={
            "win_rate": st.column_config.ProgressColumn("win rate", format="percent", min_value=0, max_value=1),
            "net": st.column_config.NumberColumn(format="₹%d")})
    with st.expander("By exit reason"):
        st.dataframe(v.groupby("reason").agg(trades=("net", "size"), net=("net", "sum")).reset_index(),
                     hide_index=True, width="stretch")
    with st.expander(f"All {len(v)} trades"):
        st.dataframe(v.sort_values(["date", "entry_time"], ascending=False), hide_index=True, width="stretch")
    sw = read_forward("forward_swing.csv")
    if not sw.empty:
        st.markdown("#### Swing trades since the start")
        sw["net"] = pd.to_numeric(sw.net, errors="coerce")
        a, b = st.columns(2)
        a.metric("Closed swing trades", len(sw))
        b.metric("Win rate", f"{(sw.net > 0).mean():.0%}")
        st.dataframe(sw[["symbol", "entry_date", "entry", "exit_date", "exit", "reason", "net_pct"]], hide_index=True,
                     width="stretch")


BOOKS = [
    ("Secrets of a Pivot Boss", "Franklin O. Ochoa (2010)",
     "Camarilla equation, two-day pivot relationships, Central Pivot Range (CPR) and pivot width forecasting.",
     "CPR width is recorded on every trade and can be used as a filter."),
    ("Day Trading with Short Term Price Patterns and Opening Range Breakout", "Toby Crabel (1990)",
     "Opening-range breakouts and narrow-range days (NR4/NR7) — quiet days before big moves.",
     "Previous-day NR7 is recorded and can be used as a filter. Your 9:45 rule is an opening-range idea."),
    ("New Concepts in Technical Trading Systems", "J. Welles Wilder Jr. (1978)",
     "ADX for trend strength (below 20 = no trend) and ATR for volatility.",
     "ADX(14) on the 1-hour chart is recorded and can be used to skip sideways markets."),
    ("Technical Analysis of the Financial Markets", "John J. Murphy (1999)",
     "Trends, support/resistance, patterns, and volume as confirmation of breakouts.",
     "Breakout-candle volume vs average is recorded and can be used as a filter."),
    ("Japanese Candlestick Charting Techniques", "Steve Nison",
     "Hammer, shooting star, engulfing patterns and how to use them for confirmation.",
     "The four patterns from your notes are detected; swing entries can require one."),
    ("Encyclopedia of Chart Patterns", "Thomas N. Bulkowski (3rd ed., 2021)",
     "Measured success and failure rates for double tops/bottoms, head & shoulders, triangles.",
     "Next step: automate the 27–28/12 patterns and compare with his published numbers."),
    ("Trade Your Way to Financial Freedom", "Van K. Tharp",
     "R-multiples, expectancy and position sizing — judge a system by average R, not win rate.",
     "Every trade shows its R-multiple; Backtest and Forward test show expectancy in R."),
    ("Evidence-Based Technical Analysis", "David Aronson (2006)",
     "Testing trading rules scientifically: benchmarks, confidence intervals, data-mining bias.",
     "Win-rate confidence range, t-statistic and the 'buy at 9:45' benchmark."),
    ("The Evaluation and Optimization of Trading Strategies", "Robert Pardo (2nd ed., 2008)",
     "Out-of-sample and walk-forward testing to avoid fitting rules to the past.",
     "First-half vs second-half check, and the daily forward test with frozen rules."),
    ("Trading in the Zone", "Mark Douglas (2000)",
     "Thinking in probabilities, following rules consistently, handling losses.",
     "Journal tracks whether you followed the rules and how you felt."),
    ("The Daily Trading Coach", "Brett N. Steenbarger (2009)",
     "Using a journal to review mistakes and build better habits.",
     "Journal 'mistake' field and weekly review of rule-breaking trades."),
    ("The Intelligent Investor", "Benjamin Graham",
     "Margin of safety, buying good businesses below their value — the basis of the 23/11 checklist.",
     "Fundamentals page: P/E vs industry, debt, growth checks."),
    ("Value Investing: The Use of Historical Financial Statement Information", "Joseph Piotroski (2000, paper)",
     "The F-Score: 9 simple accounting checks on profitability, leverage and efficiency.",
     "Next step: add the F-Score to the Fundamentals page."),
]


def page_reading():
    st.title("Further reading")
    st.caption("The books behind each part of the app — what to read, and where it shows up here.")
    for title, author, idea, in_app in BOOKS:
        with st.container(border=True):
            st.markdown(f"**{title}**  \n{author}")
            st.markdown(f"{idea}  \n*In the app:* {in_app}")


PAGES = {
    "today": st.Page(page_today, title="Today", icon="📡", default=True),
    "backtest": st.Page(page_backtest, title="Backtest", icon="🧪"),
    "fund": st.Page(page_fundamentals, title="Fundamentals", icon="🏢"),
    "forward": st.Page(page_forward, title="Forward test", icon="⏱️"),
    "journal": st.Page(page_journal, title="Paper trades", icon="📝"),
    "rules": st.Page(page_rules, title="Rules", icon="📘"),
    "reading": st.Page(page_reading, title="Reading", icon="📚"),
}
st.navigation(list(PAGES.values()), position="top").run()
