"""Market data: which companies are listed (NSE files) and prices/fundamentals (Yahoo Finance).

Everything here is free and needs no login. Yahoo intraday history is limited:
15-minute candles go back about 60 days; 1-hour candles about 2 years.
"""

import io
import math

import pandas as pd
import requests

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "text/csv,application/octet-stream,*/*",
    "Referer": "https://www.nseindia.com/",
}

INDEX_FILES = {
    "Nifty 50": "ind_nifty50list.csv",
    "Nifty Next 50": "ind_niftynext50list.csv",
    "Nifty 100": "ind_nifty100list.csv",
    "Nifty 200": "ind_nifty200list.csv",
    "Nifty 500": "ind_nifty500list.csv",
}
INDEX_URLS = [
    "https://nsearchives.nseindia.com/content/indices/{f}",
    "https://www.niftyindices.com/IndexConstituent/{f}",
]
ALL_NSE_URLS = [
    "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv",
    "https://archives.nseindia.com/content/equities/EQUITY_L.csv",
]

# Used only if the NSE files can't be downloaded. May be out of date — index members change twice a year.
FALLBACK_NIFTY50 = [
    "ADANIENT", "ADANIPORTS", "APOLLOHOSP", "ASIANPAINT", "AXISBANK", "BAJAJ-AUTO", "BAJFINANCE",
    "BAJAJFINSV", "BEL", "BHARTIARTL", "BSE", "CIPLA", "COALINDIA", "DRREDDY", "EICHERMOT", "ETERNAL",
    "GRASIM", "HCLTECH", "HDFCBANK", "HDFCLIFE", "HINDALCO", "HINDUNILVR", "ICICIBANK", "INDIGO",
    "INFY", "ITC", "JIOFIN", "JSWSTEEL", "KOTAKBANK", "LT", "M&M", "MARUTI", "MAXHEALTH",
    "NESTLEIND", "NTPC", "ONGC", "POWERGRID", "RELIANCE", "SBILIFE", "SBIN", "SHRIRAMFIN",
    "SUNPHARMA", "TATACONSUM", "TATASTEEL", "TCS", "TECHM", "TITAN", "TRENT", "ULTRACEMCO",
]


def _get_csv(urls: list[str]) -> pd.DataFrame | None:
    for u in urls:
        try:
            r = requests.get(u, headers=HEADERS, timeout=15)
            if r.ok and b"," in r.content[:500]:
                df = pd.read_csv(io.BytesIO(r.content))
                df.columns = [c.strip() for c in df.columns]
                return df
        except Exception:
            continue
    return None


def universe(name: str) -> tuple[pd.DataFrame, str]:
    """Return (DataFrame[symbol, company, industry], source note)."""
    if name == "All NSE equities":
        df = _get_csv(ALL_NSE_URLS)
        if df is not None and "SYMBOL" in df:
            if "SERIES" in df:
                df = df[df["SERIES"].astype(str).str.strip() == "EQ"]
            out = pd.DataFrame({"symbol": df["SYMBOL"].str.strip(),
                                "company": df.get("NAME OF COMPANY", df["SYMBOL"]).astype(str).str.strip(),
                                "industry": ""})
            return out.reset_index(drop=True), f"NSE list of all listed equities ({len(out)} EQ stocks)"
        name = "Nifty 500"
    f = INDEX_FILES[name]
    df = _get_csv([u.format(f=f) for u in INDEX_URLS])
    if df is not None and "Symbol" in df:
        out = pd.DataFrame({"symbol": df["Symbol"].str.strip(),
                            "company": df.get("Company Name", df["Symbol"]).astype(str).str.strip(),
                            "industry": df.get("Industry", "").astype(str).str.strip()})
        return out, f"{name} constituents from NSE Indices ({len(out)} stocks)"
    out = pd.DataFrame({"symbol": FALLBACK_NIFTY50, "company": FALLBACK_NIFTY50, "industry": ""})
    return out, "⚠️ Couldn't reach NSE — using a built-in Nifty 50 list that may be slightly out of date"


def yahoo(symbol: str) -> str:
    return symbol if symbol.endswith((".NS", ".BO")) or symbol.startswith("^") else f"{symbol}.NS"


def _clean(sub: pd.DataFrame, intraday: bool) -> pd.DataFrame:
    cols = [c for c in ["Open", "High", "Low", "Close", "Volume"] if c in sub.columns]
    sub = sub[cols].dropna(subset=["Close"])
    sub = sub[sub["High"] > 0]
    if intraday and sub.index.tz is not None:
        sub.index = sub.index.tz_convert("Asia/Kolkata")
    elif intraday:
        sub.index = sub.index.tz_localize("UTC").tz_convert("Asia/Kolkata")
    if not intraday and sub.index.tz is not None:
        sub.index = sub.index.tz_localize(None)
    return sub


def prices(symbols: list[str], period: str, interval: str) -> dict[str, pd.DataFrame]:
    """Download OHLCV for many NSE symbols in batches. Missing symbols are left out."""
    import yfinance as yf  # imported lazily so tests don't need network

    intraday = interval not in ("1d", "1wk", "1mo")
    out: dict[str, pd.DataFrame] = {}
    for k in range(0, len(symbols), 40):
        chunk = symbols[k:k + 40]
        tickers = [yahoo(s) for s in chunk]
        try:
            raw = yf.download(tickers, period=period, interval=interval, group_by="ticker",
                              auto_adjust=False, progress=False, threads=True)
        except Exception:
            continue
        if raw is None or raw.empty:
            continue
        for s, t in zip(chunk, tickers):
            try:
                if isinstance(raw.columns, pd.MultiIndex):
                    lvl0 = raw.columns.get_level_values(0)
                    sub = raw[t] if t in lvl0 else raw.xs(t, axis=1, level=1) if t in raw.columns.get_level_values(1) else None
                else:
                    sub = raw
                if sub is None:
                    continue
                sub = _clean(sub.copy(), intraday)
                if not sub.empty:
                    out[s] = sub
            except Exception:
                continue
    return out


def _row(df, names, n=4):
    if df is None or getattr(df, "empty", True):
        return []
    for nm in names:
        if nm in df.index:
            vals = df.loc[nm].iloc[:n].tolist()[::-1]  # oldest → newest
            return [float(v) if v is not None and not (isinstance(v, float) and math.isnan(v)) else None
                    for v in vals]
    return []


def fundamentals(symbol: str) -> dict:
    """Metrics for the 23/11 checklist from Yahoo Finance (annual statements, last ~4 years)."""
    import yfinance as yf

    t = yf.Ticker(yahoo(symbol))
    info = {}
    try:
        info = t.info or {}
    except Exception:
        pass
    try:
        inc = t.income_stmt
    except Exception:
        inc = None
    try:
        bs = t.balance_sheet
    except Exception:
        bs = None
    sector, industry = info.get("sector", ""), info.get("industry", "")
    financial = sector == "Financial Services" or "Bank" in industry
    return {
        "symbol": symbol,
        "name": info.get("longName") or info.get("shortName") or symbol,
        "sector": sector, "yf_industry": industry,
        "price": info.get("currentPrice") or info.get("regularMarketPrice"),
        "market_cap": info.get("marketCap"),
        "pe": info.get("trailingPE"),
        "total_debt": None if financial else info.get("totalDebt"),  # debt rule doesn't suit banks/NBFCs
        "insiders": info.get("heldPercentInsiders"),
        "institutions": info.get("heldPercentInstitutions"),
        "dividend_yield": info.get("dividendYield"),
        "sales": _row(inc, ["Total Revenue", "Operating Revenue"]),
        "profit": _row(inc, ["Net Income", "Net Income Common Stockholders"]),
        "reserves": _row(bs, ["Retained Earnings", "Stockholders Equity", "Common Stock Equity"]),
        "borrowings": [] if financial else _row(bs, ["Total Debt", "Long Term Debt"]),
        "assets": _row(bs, ["Total Assets"]),
        "financial": financial,
    }


def screener_url(symbol: str) -> str:
    return f"https://www.screener.in/company/{symbol}/consolidated/"
