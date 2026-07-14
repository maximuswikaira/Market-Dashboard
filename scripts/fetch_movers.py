#!/usr/bin/env python3
"""
Fetches US market indices, top movers, niche watchlists, and screens
S&P 500 / Nasdaq-100 / Dow 30 constituents for recent analyst upgrades and
net insider buying. Renders a static HTML dashboard.

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

NICHES = {
    "Tech": ["AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSM", "AVGO", "ORCL", "CRM", "AMD", "PLTR"],
    "Rare & critical metals": ["MP", "ALB", "SQM", "LAC", "FCX", "VALE", "SCCO", "TMC", "UUUU", "NEM", "PLL", "LYSCF"],
    "Space & aerospace": ["RKLB", "BA", "LMT", "NOC", "RTX", "ASTS", "SPCE", "RDW", "IRDM", "KTOS", "AVAV", "LDOS"],
    "Energy & uranium": ["CCJ", "UEC", "DNN", "NXE", "SMR", "OKLO", "LEU", "BWXT", "XOM", "CVX", "NEE", "UUUU"],
}

UPGRADE_LOOKBACK_DAYS = 7
INSIDER_LOOKBACK_DAYS = 90
MAX_SCREEN_CANDIDATES = 600
MAX_CONSECUTIVE_FAILURES = 15


def fetch_sp500_tickers():
    url = "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/master/data/constituents.csv"
    r = requests.get(url, headers=HEADERS, timeout=20)
    r.raise_for_status()
    lines = r.text.strip().splitlines()[1:]
    return [line.split(",")[0].strip() for line in lines if line.strip()]


def fetch_nasdaq100_tickers():
    url = "https://en.wikipedia.org/wiki/Nasdaq-100"
    r = requests.get(url, headers=HEADERS, timeout=20)
    r.raise_for_status()
    import re

    tickers = re.findall(r'title="[^"]*"\s*>\s*([A-Z]{1,5})\s*</a>', r.text)
    if len(tickers) < 50:
        tickers = re.findall(r">([A-Z]{1,5})</td>", r.text)
    return sorted(set(tickers))


def fetch_dow30_tickers():
    return [
        "AAPL", "AMGN", "AMZN", "AXP", "BA", "CAT", "CRM", "CSCO", "CVX",
        "DIS", "GS", "HD", "HON", "IBM", "JNJ", "JPM", "KO", "MCD", "MMM",
        "MRK", "MSFT", "NKE", "NVDA", "PG", "SHW", "TRV", "UNH", "V", "VZ", "WMT",
    ]


def build_universe():
    tickers = set()
    for fetch_fn, label in [
        (fetch_sp500_tickers, "S&P 500"),
        (fetch_nasdaq100_tickers, "Nasdaq-100"),
        (fetch_dow30_tickers, "Dow 30"),
    ]:
        try:
            result = fetch_fn()
            tickers.update(result)
            print(f"Loaded {len(result)} tickers from {label}")
        except Exception as e:
            print(f"Failed to load {label} list: {e}")
    return sorted(tickers)


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


def fetch_quotes_batch(symbols):
    url = "https://query1.finance.yahoo.com/v7/finance/quote"
    params = {"symbols": ",".join(symbols)}
    r = requests.get(url, headers=HEADERS, params=params, timeout=15)
    r.raise_for_status()
    results = r.json().get("quoteResponse", {}).get("result", [])
    rows = []
    for q in results:
        rows.append(
            {
                "symbol": q.get("symbol"),
                "name": q.get("shortName", "") or q.get("symbol", ""),
                "price": q.get("regularMarketPrice"),
                "change_pct": q.get("regularMarketChangePercent"),
                "volume": q.get("regularMarketVolume"),
            }
        )
    return rows


def fetch_niche_sections():
    sections = {}
    for niche, tickers in NICHES.items():
        try:
            rows = fetch_quotes_batch(tickers)
            rows.sort(key=lambda r: abs(r["change_pct"] or 0), reverse=True)
            sections[niche] = rows
        except Exception as e:
            print(f"Failed to fetch niche {niche}: {e}")
            sections[niche] = []
    return sections


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


def screen_for_signals(candidates):
    hits = []
    checked = 0
    consecutive_failures = 0
    for sym in candidates:
        if checked >= MAX_SCREEN_CANDIDATES:
            print(f"Hit MAX_SCREEN_CANDIDATES ({MAX_SCREEN_CANDIDATES}), stopping.")
            break
        try:
            info = fetch_analyst_and_insider(sym)
            consecutive_failures = 0
        except Exception as e:
            consecutive_failures += 1
            print(f"Skipping {sym}: {e}")
            if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                print(
                    f"Hit {MAX_CONSECUTIVE_FAILURES} consecutive failures "
                    "(likely rate-limited) — stopping scan early."
                )
                break
            time.sleep(0.3)
            continue

        checked += 1
        has_upgrade = len(info["recent_upgrades"]) > 0
        net_insider_buying = info["insider_buys"] > info["insider_sells"]
        if has_upgrade or net_insider_buying:
            hits.append(info)
        time.sleep(0.3)
    print(f"Screened {checked} tickers, found {len(hits)} signal hits.")
    return hits, checked


def render_html(indices, sections, niche_sections, signal_hits, screened_count, universe_count):
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
            return "<p class='empty'>No signals found in this run.</p>"
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
    niches_html = "".join(
        f"<section><h2>{niche}</h2>"
        f"<p class='note'>Sorted by biggest move today, up or down.</p>"
        f"{table(rows)}</section>"
        for niche, rows in niche_sections.items()
    )
    indices_html = "".join(index_card(n, d) for n, d in indices.items())
    signals_html = (
        f"<section><h2>Analyst upgrades &amp; insider buying</h2>"
        f"<p class='note'>Screened {screened_count} of {universe_count} S&amp;P 500 / "
        f"Nasdaq-100 / Dow 30 tickers this run. Signals, not recommendations.</p>"
        f"{signal_table(signal_hits)}</section>"
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
  {niches_html}
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

    niche_sections = fetch_niche_sections()

    universe = build_universe()
    print(f"Universe size: {len(universe)} unique tickers")
    signal_hits, screened_count = screen_for_signals(universe)

    html = render_html(indices, sections, niche_sections, signal_hits, screened_count, len(universe))
    os.makedirs("docs", exist_ok=True)
    with open("docs/index.html", "w") as f:
        f.write(html)
    print(f"Wrote docs/index.html ({len(signal_hits)} signal hits)")


if __name__ == "__main__":
    main()
