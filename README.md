# Zigma trader

The rules from the **Zigma Traders Class** notes turned into a testable app (Streamlit, works on Android in Chrome).

| Page | What it does |
|---|---|
| **Today** | Intraday Camarilla scanner (after 9:45) and swing Fibonacci scanner across Nifty 50/100/200/500 or all NSE stocks, with charts and one-tap logging as a paper trade |
| **Backtest** | Runs the rules on past data — win rate, average win/loss, net profit **after charges**, drawdown, results by stock and exit reason, and a verdict vs the class's "80% accuracy" claim |
| **Fundamentals** | Long-term checklist from the 23/11 class: market-cap slab, debt < 25% of mcap, P/E below industry, sales/profit/reserves/assets rising, borrowings falling, promoter and institutional holding — with a Screener.in link per stock |
| **Paper trades** | Journal (CSV or Notion) plus a "ready for real money?" check: 50+ trades, 8+ weeks, profitable after charges, rules followed 90%+ |
| **Rules** | Every rule in plain words, with anything not in the notes marked *(added)* |

## Data
- Company lists: NSE's official files (Nifty index constituents, all listed equities). Falls back to a built-in Nifty 50 list if NSE can't be reached.
- Prices and fundamentals: Yahoo Finance via `yfinance` (free, no login). 15-minute candles go back ~60 days, so intraday backtests are short.
- Screener.in has no API and its export is a paid feature, so the app links to it rather than scraping it.

## Honest limits
- The app never places orders.
- Yahoo prices can lag; confirm on your broker app.
- A backtest is not a promise — paper-trade for 2–3 months before using real money, and never trade with borrowed money.

## Optional: save paper trades to Notion
Add to the app's **Secrets** on Streamlit Cloud:
```
NOTION_TOKEN = "secret_..."
NOTION_DATABASE_ID = "..."
```
The database needs properties: Name (title), Date, Strategy (select), Side (select), Status (select), Entry, Stop, Target, Exit, Qty, Net (numbers), Followed rules (checkbox), Notes (text).

## Run locally
```
pip install -r requirements.txt
streamlit run app.py
pytest -q
```
