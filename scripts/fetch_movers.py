#!/usr/bin/env python3
"""
Fetches US market indices + top movers, screens them for recent analyst
upgrades and net insider buying, and renders a static HTML dashboard.

Data source: Yahoo Finance's public (undocumented) endpoints. No API key
needed, but these endpoints can occasionally change shape or rate-limit —
see README for a fallback plan if that happens.

Run manually with: python scripts/fetch_movers.py
Normally triggered on a schedule by .github/workflows/market-report.yml
"""
import datetime
import os
import time

import requests

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}

SCREENERS = {
    "Top gainers": "day_gainers",
    "Top losers": "day_losers",
    "Most active": "most_actives",
}

INDICES = {
    "S&P 500": "%5EGSPC",
    "Nasdaq": "%5EIXIC",
    "Dow Jones": "%5EDJI",
}

UPGRADE_LOOKBACK_DAYS = 7
INSIDER_LOOKBACK_DAYS = 90
MAX_SCREEN_CANDIDATES = 25


def fetch_screener(scr_id, count=10):
    url = (
        "https://query1.finance.yahoo.com/v1/finance/screener/predefined/saved"
        f"?count={count}&scrIds={scr_id}"
    )
    r = requests.get(url, headers=HEADERS, timeout=15)
    r.raise_for_status()
    data = r.json()
    quotes = data["finance"]["result"][0]["quotes"]
    rows = []
    for q in quotes:
        rows.append(
            {
                "symbol": q.get("symbol"),
                "name": q.get("shortName", "") or "",
                "price": q.get("regularMarketPrice"),
                "change_pct": q.get("regularMarketChangePercent"),
                "volume": q.get("regularMarketVolume"),
            }
        )
    return rows


def fetch_index(symbol):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    r = requests.get(url, headers=HEADERS, timeout=15)
    r.raise_for_status()
    data = r.json()
    meta = data["chart"]["result"][0]["meta"]
    price = meta.get("regularMarketPrice")
    prev = meta.get("chartPreviousClose") or meta.get("previousClose")
    change_pct = ((price - prev) / prev * 100) if price and prev else None
    return {"price": price, "change_pct": change_pct}


def fetch_analyst_and_insider(symbol):
    url = f"https://query1.finance.yahoo.com/v10/finance/quoteSummary/{symbol}"
    params = {"modules": "upgradeDowngradeHistory,insiderTransactions,price"}
    r = requests.get(url, headers=HEADERS, params=params, timeout=15)
    r.raise_for_status()
    result = r.json()["quoteSummary"]["result"][0]
    now = datetime.datetime.utcnow().timestamp()

    recent_upgrades = []
    history = result.get("upgradeDowngradeHistory", {}).get("history", [])
    for h in history:
        epoch = h.get("epochGradeDate")
        if epoch and (now - epoch) <= UPGRADE_LOOKBACK_DAYS * 86400:
            if h.get("action") in ("up", "init"):
                recent_upgrades.append(
                    {"firm": h.get("firm"), "to_grade": h.get("toGrade"), "action": h.get("action")}
                )

    insider_buys = 0
    insider_sells = 0
    txns = result.get("insiderTransactions", {}).get("transactions", [])
    for t in txns:
        start = t.get("startDate", {})
        epoch = start.get("raw") if isinstance(start, dict) else None
        if epoch and (now - epoch) <= INSIDER_LOOKBACK_DAYS * 86400:
            text = (t.get("transactionText") or "").lower()
            if "purchase" in text or "buy" in text:
                insider_buys += 1
            elif "sale" in text or "sell" in text:
                insider_sells += 1

    name = result.get("price", {}).get("shortName", symbol)

    return {
        "symbol": symbol,
        "name": name,
        "recent_upgrades": recent_upgrades,
        "insider_buys": insider_buys,
        "insider_sells": insider_sells,
    }


def build_signal_candidates(mover_sections):
    seen = []
    for rows in mover_sections.values():
        for r in rows:
            sym = r.get("symbol")
            if sym and sym not in seen:
                seen.append(sym)
    return seen[:MAX_SCREEN_CANDIDATES]


def screen_for_signals(candidates):
    hits = []
    for sym in candidates:
        try:
            info = fetch_analyst_and_insider(sym)
        except Exception as e:
            print(f"Skipping {sym}: {e}")
            continue
        has_upgrade = len(info["recent_upgrades"]) > 0
        net_insider_buying = info["insider_buys"] > info["insider_sells"]
        if has_upgrade or net_insider_buying:
            hits.append(info)
        time.sleep(0.3)
    return hits


def render_html(indices, sections, signal_hits):
    now = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    def index_card(name, d):
        if d["price"] is None:
            return f"<div class='card'><h3>{name}</h3><p>n/a</p></div>"
        cls = "up" if (d["change_pct"] or 0) >= 0 else "down"
        return (
            f"<div class='card'><h3>{name}</h3>"
            f"<p class='price'>{d['price']:.2f}</p>"
            f"<p class='{cls}'>{d['change_pct']:+.2f}%</p></div>"
        )

    def table(rows):
        if not rows:
            return "<p class='empty'>No data.</p>"
        trs = ""
        for r in rows:
            cls = "up" if (r["change_pct"] or 0) >= 0 else "down"
            price = f"{r['price']:.2f}" if r["price"] is not None else "n/a"
            chg = f"{r['change_pct']:+.2f}%" if r["change_pct"] is not None else "n/a"
            vol = f"{r['volume']:,}" if r["volume"] else "n/a"
            trs += (
                f"<tr><td>{r['symbol']}</td><td>{r['name']}</td>"
                f"<td>{price}</td><td class='{cls}'>{chg}</td><td>{vol}</td></tr>"
            )
        return (
            "<table><thead><tr><th>Symbol</th><th>Name</th><th>Price</th>"
            f"<th>Change</th><th>Volume</th></tr></thead><tbody>{trs}</tbody></table>"
        )

    def signal_table(hits):
        if not hits:
            return "<p class='empty'>No signals among today's movers.</p>"
        trs = ""
        for h in hits:
            upgrade_text = (
                "; ".join(f"{u['firm']} → {u['to_grade']}" for u in h["recent_upgrades"])
                if h["recent_upgrades"] else "—"
            )
            insider_text = f"{h['insider_buys']} buys / {h['insider_sells']} sells"
            trs += (
                f"<tr><td>{h['symbol']}</td><td>{h['name']}</td>"
                f"<td>{upgrade_text}</td><td>{insider_text}</td></tr>"
            )
        return (
            "<table><thead><tr><th>Symbol</th><th>Name</th>"
            f"<th>Recent upgrades (7d)</th><th>Insider activity (90d)</th></tr></thead>"
            f"<tbody>{trs}</tbody></table>"
        )

    sections_html = "".join(
        f"<section><h2>{title}</h2>{table(rows)}</section>" for title, rows in sections.items()
    )
    indices_html = "".join(index_card(n, d) for n, d in indices.items())
    signals_html = (
        f"<section><h2>Analyst upgrades &amp; insider buying</h2>"
        f"<p class='note'>Screened from today's movers only — not the whole "
        f"market. Signals, not recommendations.</p>{signal_table(signal_hits)}</section>"
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Overnight market report</title>
<style>
  body {{ font-family: -apple-system, Helvetica, Arial, sans-serif; background: #0b0b0b;
         color: #eee; margin: 0; padding: 24px; max-width: 900px; }}
  h1 {{ font-size: 20px; font-weight: 500; margin: 0 0 4px; }}
  .timestamp {{ color: #999; font-size: 13px; margin-bottom: 24px; }}
  .cards {{ display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 32px; }}
  .card {{ background: #1a1a1a; border-radius: 8px; padding: 16px; min-width: 140px; }}
  .card h3 {{ margin: 0 0 8px; font-size: 13px; color: #aaa; font-weight: 400; }}
  .price {{ font-size: 22px; margin: 0; }}
  .up {{ color: #4caf50; }}
  .down {{ color: #f44336; }}
  .empty {{ color: #777; }}
  .note {{ color: #888; font-size: 12px; margin: -4px 0 12px; }}
  section {{ margin-bottom: 32px; }}
  h2 {{ font-size: 16px; font-weight: 500; border-bottom: 1px solid #333; padding-bottom: 8px; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
  th, td {{ text-align: left; padding: 8px; border-bottom: 1px solid #222; }}
  th {{ color: #888; font-weight: 400; }}
</style>
</head>
<body>
  <h1>Overnight market report</h1>
  <p class="timestamp">Generated {now} &middot; covers the US trading session</p>
  <div class="cards">{indices_html}</div>
  {sections_html}
  {signals_html}
</body>
</html>"""


def main():
    indices = {}
    for name, sym in INDICES.items():
        try:
            indices[name] = fetch_index(sym)
        except Exception as e:
            print(f"Failed to fetch index {name}: {e}")
            indices[name] = {"price": None, "change_pct": None}

    sections = {}
    for title, scr in SCREENERS.items():
        try:
            sections[title] = fetch_screener(scr)
        except Exception as e:
            print(f"Failed to fetch screener {title}: {e}")
            sections[title] = []

    candidates = build_signal_candidates(sections)
    signal_hits = screen_for_signals(candidates)

    html = render_html(indices, sections, signal_hits)
    os.makedirs("docs", exist_ok=True)
    with open("docs/index.html", "w") as f:
        f.write(html)
    print(f"Wrote docs/index.html ({len(signal_hits)} signal hits from {len(candidates)} candidates)")


if __name__ == "__main__":
    main()
