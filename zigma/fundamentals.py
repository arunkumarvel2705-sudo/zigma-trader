"""Long-term investment checklist from the 23/11 class notes (fundamental analysis).

Market cap slab:  small < ₹5,000 cr · mid ₹5,000–20,000 cr · large > ₹20,000 cr
Debt %            = debt / market cap × 100, should be < 25%
P/E               below the industry P/E (undervalued)
P&L               sales increasing, net profit increasing
Balance sheet     reserves increasing, borrowings decreasing, assets increasing
Shareholding      promoters > 50%, FII > 10%, DII > 10%

Free data (Yahoo Finance) has no FII/DII split: we use total institutional holding > 20%
as a proxy and link every stock to Screener.in so you can check the exact split.
"""

import math

CR = 1e7  # ₹1 crore


def cap_slab(mcap_rs: float | None) -> str:
    if not mcap_rs or math.isnan(mcap_rs):
        return "?"
    cr = mcap_rs / CR
    return "Small cap" if cr < 5_000 else "Mid cap" if cr < 20_000 else "Large cap"


def _rising(series) -> bool | None:
    """True if each year is higher than the one before (oldest → newest)."""
    vals = [v for v in series if v is not None and not (isinstance(v, float) and math.isnan(v))]
    if len(vals) < 2:
        return None
    return all(b > a for a, b in zip(vals, vals[1:]))


def _falling(series) -> bool | None:
    vals = [v for v in series if v is not None and not (isinstance(v, float) and math.isnan(v))]
    if len(vals) < 2:
        return None
    return all(b <= a for a, b in zip(vals, vals[1:]))


CHECKS = ["Debt < 25% of mcap", "P/E below industry", "Sales rising", "Profit rising",
          "Reserves rising", "Borrowings falling", "Assets rising", "Promoters > 50%",
          "Institutions > 20%"]


def evaluate(m: dict, industry_pe: float | None) -> dict:
    """m: metrics for one company (see data.fundamentals). Returns pass/fail per check + score."""
    res = {}
    mcap, debt, pe = m.get("market_cap"), m.get("total_debt"), m.get("pe")
    res["Debt < 25% of mcap"] = (debt / mcap * 100 < 25) if mcap and debt is not None else None
    res["P/E below industry"] = (pe < industry_pe) if pe and industry_pe and pe > 0 else (False if pe and pe < 0 else None)
    res["Sales rising"] = _rising(m.get("sales", []))
    res["Profit rising"] = _rising(m.get("profit", []))
    res["Reserves rising"] = _rising(m.get("reserves", []))
    res["Borrowings falling"] = _falling(m.get("borrowings", []))
    res["Assets rising"] = _rising(m.get("assets", []))
    ins, inst = m.get("insiders"), m.get("institutions")
    res["Promoters > 50%"] = (ins > 0.5) if ins is not None else None
    res["Institutions > 20%"] = (inst > 0.2) if inst is not None else None
    known = [v for v in res.values() if v is not None]
    res["score"] = sum(known)
    res["checked"] = len(known)
    res["debt_pct"] = (debt / mcap * 100) if mcap and debt is not None else None
    res["slab"] = cap_slab(mcap)
    return res
