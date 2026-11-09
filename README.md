# Collector

Collects headlines from six crypto news feeds every 5 minutes and shows them on a phone-friendly dashboard. Nothing is filtered yet: the point of this stage is to measure volume, duplicates, speed and feed health so the filter can be designed from real data.

## What's in here

| File | Job |
|---|---|
| `feeds.json` | The feed list. Add or remove a source by editing one line. |
| `collector.py` | Checks every feed, saves new headlines to `data/items/`, logs every check to `data/health/`. |
| `report.py` | Builds the dashboard numbers: feed health, multi-outlet stories and who was first, hourly flow, coin and topic mentions. |
| `docs/index.html` | The Collector dashboard. Monitor is one full-width Timeline with Recent and Hot (24h) tabs. Analyze has Outlets and the Filter lab. Manage has Sources, Filters, Watchlist and Alerts. The page checks for new data every minute. |
| `run_local.py` | Runs everything on your own computer with one command. |
| `backfill.py` | Pages back through feeds to collect up to a week of older headlines. |
| `matcher.py` | Sends new headlines to Claude, which groups them into events and storylines and tags type, market, topic, tickers and dollar size. Each headline is sent once. |
| `history.py` | Builds `history.json` for the dashboard: stories, storylines, outlet scorecard and headlines. |
| `.github/workflows/collect.yml` | Runs the collector every 5 minutes and saves the data to this repo. |
| `.github/workflows/dashboard.yml` | Rebuilds and publishes the dashboard every 15 minutes. |

## Run it locally first (laptop)

1. Install Python 3.12 from python.org. On Windows, tick **Add python.exe to PATH** in the installer.
2. Unzip this folder, open a terminal inside it (Windows: right-click the folder → *Open in Terminal*; Mac: right-click → *New Terminal at Folder*).
3. Run `pip install -r requirements.txt` once.
4. Run `python run_local.py`. It checks the feeds right away, opens the dashboard at http://localhost:8000, and repeats every 5 minutes. Refresh the page to see new data. Ctrl+C stops it; the data stays in `data/`.

When you're happy, set up GitHub below and upload the `data/` folder too, so the cloud version continues the same collection.

## Setup on GitHub (about 10 minutes, easiest on the laptop)

1. Create a free GitHub account if you don't have one, then make a **new public repository** called `crypto-wire`. Public keeps Actions and Pages free; the data is only public headlines.
2. Unzip this folder and upload everything with **Add file → Upload files**. Check the `.github/workflows` folder made it in (some file browsers hide folders starting with a dot). If it didn't, use **Add file → Create new file**, type `.github/workflows/collect.yml` as the name, and paste the file's contents. Do the same for `dashboard.yml`.
3. **Settings → Actions → General → Workflow permissions:** choose *Read and write permissions* and save.
4. **Settings → Pages → Build and deployment → Source:** choose *GitHub Actions*.
5. Open the **Actions** tab (enable workflows if asked). Run **Collect headlines** by hand once, wait for it to finish, then run **Update dashboard**.
6. Your dashboard is at `https://YOUR-USERNAME.github.io/crypto-wire/`. Watchlist, Filters and Alert rules are saved in the browser you set them in. On the S24, open it in Chrome and use *Add to Home screen*.

After that it runs on its own. Let it collect for 3 to 5 days before designing the filter.

## Claude API key (optional at first)

Without a key, `basic_matcher.py` groups headlines into stories and tags them with simple rules, so the timeline works from day one (no storylines, less accurate). Add the key below and new headlines switch to Claude automatically; older ones keep their rule-based labels.

### Adding the key

1. Create a key at console.anthropic.com (API keys) and add some credit.
2. On GitHub: **Settings → Secrets and variables → Actions → New repository secret**, name `ANTHROPIC_API_KEY`, paste the key.
3. Locally: set the same environment variable before running `python matcher.py`.

Matching uses `claude-sonnet-5-5` by default. Set a `MATCH_MODEL` environment variable to change it. Headlines go 25 at a time, and each one is only ever sent once, which keeps the cost low.

## Getting a week of history

Run **Actions → Backfill a week of history → Run workflow** once (or `python backfill.py` locally, then `python matcher.py`). Feeds with a `pages` entry in `feeds.json` are paged back; the log says which ones allow it. Feeds that don't allow paging fill in from the day you start collecting.


## News topic searches (GDELT)

`feeds.json` includes 25 entries with `"kind": "gdelt"`. Each is a search on the GDELT DOC 2.0 API (free, no key, built for programs; English sources, last 24 hours, up to 75 articles), covering AI, big tech, stocks, the Fed and macro, policy and sanctions, geopolitics, hedge funds, ETF flows and crypto.

- `trusted.json` lists the outlets whose articles are kept (FT, Bloomberg, Reuters, CNBC, WSJ, AP, BBC, the crypto outlets and more), mapped from web address to display name. Set `"trusted_only": false` to accept every outlet GDELT finds, or add domains to widen it.
- Each article is credited to its outlet, and a story already collected from another search or a direct feed is skipped.
- Collector pauses briefly between GDELT searches and always asks for fresh copies.
- GDELT's timestamp is when GDELT first saw an article, usually within about 15 minutes of publication.
- To add a topic, copy an entry and change `name`, `query` and the `query=` part of `url`. Group alternatives with OR inside parentheses, quote phrases, and prefix `-` to exclude a word.

Google News was considered and dropped: its site rules disallow automated readers.

## Good to know

- DL News is switched off in `feeds.json` (its feed was frozen when checked on 8 Nov). Unchained and crypto.news are new and not yet confirmed. Check the Feeds panel after the first run and remove any that show red.

- GitHub's scheduler can start runs a few minutes late when it's busy. That's fine for this stage; the dashboard shows each outlet's typical delay so you can see it.
- The very first run saves each feed's existing backlog. Those items are marked so they don't distort the delay and hourly-flow numbers.
- A feed that keeps failing shows red on the dashboard with the error. Remove or replace it in `feeds.json`.
- To run it on your own computer instead, see *Run it locally first* above.
