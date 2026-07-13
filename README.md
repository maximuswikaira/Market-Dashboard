# Overnight market dashboard

Fetches S&P 500 / Nasdaq / Dow levels plus top gainers, losers, and most-active
stocks after the US market closes, and publishes a dashboard you can check
each morning (NZ time).

## How it works

1. `scripts/fetch_movers.py` pulls data from Yahoo Finance's public feed and
   writes a static `docs/index.html`.
2. `.github/workflows/market-report.yml` runs that script automatically every
   US trading day and publishes `docs/` to GitHub Pages — no server of your
   own needed.

## Setup (about 5 minutes)

1. **Create a new GitHub repo.** Go to github.com → New repository → give it
   any name (e.g. `market-dashboard`) → Public → Create.
2. **Push these files to it.** In a terminal:
   ```
   cd market-dashboard
   git init
   git add .
   git commit -m "Initial dashboard setup"
   git branch -M main
   git remote add origin https://github.com/YOUR_USERNAME/market-dashboard.git
   git push -u origin main
   ```
3. **Enable GitHub Pages.** In your repo: Settings → Pages → under "Build and
   deployment", set Source to "Deploy from a branch", Branch to `gh-pages`
   (this branch will appear automatically after the workflow runs once — see
   step 4). Save.
4. **Trigger the first run manually.** In your repo: Actions tab → "Overnight
   market report" → Run workflow. Wait ~30 seconds, then refresh the Pages
   settings — `gh-pages` should now be selectable.
5. **Bookmark your dashboard.** It'll be at:
   `https://YOUR_USERNAME.github.io/market-dashboard/`

After that, it updates itself automatically every weeknight — just open the
bookmark each morning.

## Adjusting the schedule

The cron line `0 20 * * 1-5` runs at 20:00 UTC, Monday–Friday (in UTC terms;
GitHub Actions cron is always UTC). That's timed to land close to the US
market close. Two things to know:

- **Daylight saving drifts it by an hour** twice a year (US and NZ don't
  change clocks on the same dates), so the dashboard might land an hour
  earlier or later in your morning for a few weeks each year. If that
  matters, just nudge the number in the cron line.
- You can always click **Run workflow** manually any time in the Actions tab
  if you want a fresh read without waiting for the schedule.

## If Yahoo's endpoint breaks

Yahoo's screener/chart endpoints are undocumented and free, which is great
for cost but means they can occasionally change shape or start rate-limiting
GitHub's IP ranges. If the dashboard starts showing blank sections:
- Check the Actions tab → latest run → logs for the actual error.
- A reliable paid fallback is a proper market-data API (e.g. Financial
  Modeling Prep, Polygon.io, Alpha Vantage) — swap the `fetch_screener`/
  `fetch_index` functions for calls to one of those with a free-tier API key
  stored as a GitHub Actions secret.

## Extending it

- Add your own watchlist by editing `SCREENERS`/`INDICES` in
  `fetch_movers.py`, or adding a hardcoded list of tickers you fetch
  individually via the same `chart` endpoint used for indices.
- Want it emailed too, not just a dashboard? Add a step to the workflow using
  an action like `dawidd6/action-send-mail`, with your email creds stored as
  repo secrets (never commit credentials directly).
