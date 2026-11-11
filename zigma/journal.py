"""Paper-trading journal. Keeps trades as a CSV you download/upload, or in a Notion database
if NOTION_TOKEN and NOTION_DATABASE_ID are set in the app's secrets."""

import pandas as pd
import requests

COLUMNS = ["date", "symbol", "strategy", "side", "entry", "stop", "target", "exit", "qty",
           "status", "net", "followed_rules", "notes"]


def empty() -> pd.DataFrame:
    return pd.DataFrame(columns=COLUMNS)


def load_csv(file) -> pd.DataFrame:
    df = pd.read_csv(file)
    for c in COLUMNS:
        if c not in df:
            df[c] = None
    return df[COLUMNS]


def compute_net(row, charge_fn) -> float | None:
    try:
        e, x, q = float(row["entry"]), float(row["exit"]), int(row["qty"])
    except (TypeError, ValueError):
        return None
    sign = 1 if str(row["side"]).upper().startswith("B") else -1
    buy_v, sell_v = (e * q, x * q) if sign == 1 else (x * q, e * q)
    kind = "intraday" if str(row["strategy"]).lower().startswith("intra") else "delivery"
    return sign * (x - e) * q - charge_fn(buy_v, sell_v, kind)


class Notion:
    """Minimal Notion API client for one database. Property names must match COLUMNS
    (Name = title holding the symbol)."""
    API = "https://api.notion.com/v1"

    def __init__(self, token: str, database_id: str):
        self.h = {"Authorization": f"Bearer {token}", "Notion-Version": "2022-06-28",
                  "Content-Type": "application/json"}
        self.db = database_id

    def add(self, r: dict) -> bool:
        def num(v):
            try:
                return {"number": float(v)}
            except (TypeError, ValueError):
                return {"number": None}
        props = {
            "Name": {"title": [{"text": {"content": str(r["symbol"])}}]},
            "Date": {"date": {"start": str(r["date"])}},
            "Strategy": {"select": {"name": str(r["strategy"])}},
            "Side": {"select": {"name": str(r["side"])}},
            "Status": {"select": {"name": str(r["status"])}},
            "Entry": num(r["entry"]), "Stop": num(r["stop"]), "Target": num(r["target"]),
            "Exit": num(r["exit"]), "Qty": num(r["qty"]), "Net": num(r["net"]),
            "Followed rules": {"checkbox": bool(r.get("followed_rules"))},
            "Notes": {"rich_text": [{"text": {"content": str(r.get("notes") or "")[:1900]}}]},
        }
        resp = requests.post(f"{self.API}/pages", headers=self.h,
                             json={"parent": {"database_id": self.db}, "properties": props}, timeout=15)
        return resp.ok

    def all(self) -> pd.DataFrame:
        rows, cursor = [], None
        while True:
            body = {"page_size": 100, **({"start_cursor": cursor} if cursor else {})}
            resp = requests.post(f"{self.API}/databases/{self.db}/query", headers=self.h, json=body, timeout=15)
            if not resp.ok:
                break
            data = resp.json()
            for p in data.get("results", []):
                pr = p["properties"]

                def g(k, kind):
                    v = pr.get(k, {})
                    if kind == "title":
                        return "".join(t["plain_text"] for t in v.get("title", []))
                    if kind == "text":
                        return "".join(t["plain_text"] for t in v.get("rich_text", []))
                    if kind == "select":
                        return (v.get("select") or {}).get("name")
                    if kind == "date":
                        return (v.get("date") or {}).get("start")
                    if kind == "check":
                        return v.get("checkbox")
                    return v.get("number")
                rows.append({"date": g("Date", "date"), "symbol": g("Name", "title"),
                             "strategy": g("Strategy", "select"), "side": g("Side", "select"),
                             "entry": g("Entry", "n"), "stop": g("Stop", "n"), "target": g("Target", "n"),
                             "exit": g("Exit", "n"), "qty": g("Qty", "n"), "status": g("Status", "select"),
                             "net": g("Net", "n"), "followed_rules": g("Followed rules", "check"),
                             "notes": g("Notes", "text")})
            if not data.get("has_more"):
                break
            cursor = data.get("next_cursor")
        return pd.DataFrame(rows, columns=COLUMNS)
