# Real Sports Utilities

`real/` contains the active Real Sports workflow that used to live in the repo root.

## Main scripts

- `realsports_api.py`
  - shared Real Sports API/auth helper
- `bootstrap_realsports_session.py`
  - captures a reusable browser-backed Real Sports session
- `extract_realsports_session_from_chrome.py`
  - extracts the current session from Chrome/Edge local storage
- `read_real_player.py`
  - refreshes `real_id.csv` from the Real Sports player leaderboard
- `fetch_ranking.py`
  - saves raw ranking pages for deeper inspection
- `lineup.py`
  - pulls Rotowire optimizer projections, applies Real Sports multipliers, and writes `lineup.csv`
- `fair_odds.py`
  - core fair-line / devig / EV / Kelly math for sportsbook consensus markets
- `sportsbook_catalog.py`
  - canonical target-book list, alias normalization, categories, and default source weights
- `live_polls.py`
  - pulls current Real Sports polls from the live feed, sport home tabs, or the dedicated sport-polls tab and writes `live_polls.csv`
- `poll_market_matcher.py`
  - matches Real Sports polls to sportsbook markets and evaluates fair line / EV
- `ingest_public_markets.py`
  - the main market ingester for `Kalshi`, `Polymarket`, `DraftKings`, and `FanDuel`
- `provider_kalshi.py`
  - normalizes public Kalshi sports markets into the shared market schema
- `provider_polymarket.py`
  - normalizes public Polymarket sports markets into the shared market schema
- `provider_draftkings.py`
  - parses DraftKings sportsbook payloads and can replay saved browser-backed requests
- `provider_fanduel.py`
  - parses FanDuel sportsbook payloads and can replay saved browser-backed requests
- `ingest_odds_api_io.py`
  - optional paid fallback adapter for odds-api.io
- `market_csv.py`
  - shared market-row writer / dedupe / requests-session helper
- `sportsbook_http.py`
  - browser-like HTTP helper plus saved-request/saved-payload support for blocked books
- `rank.py`
  - updates the smoothed ranking workbooks in `rank/`
- `picks.py`
  - reads `rank/*.xlsx` plus `picks.txt` and writes `picks_ev.txt`
- `readrealavg.py`
  - updates average player values in `real.csv`
- `readreal.py`
  - updates game-result / morale style tracking in `real.csv`
- `ev.py`
  - simple expected-value helper

## Data and outputs

Active Real Sports files now live here too:

- `rank/`
- `real.csv`
- `real_id.csv`
- `live_polls.csv`
- `lineup.csv`
- `fantasy_points.json`
- `fantasy_points.txt`
- `picks.txt`
- `picks_ev.txt`
- `rating.txt`
- `rank.zip`
- `tmp/`
- `.cache/realsports_multiplier/`

## Auth files

The shared helper uses local files inside `real/` by default:

- `.realsports_auth_cache.json`
- `.realsports_browser_session.json`
- `.realsports_env.ps1`

These are meant to stay next to the Real Sports scripts instead of cluttering the repo root.

The helper also needs `hashids` installed:

```powershell
python -m pip install hashids
```

## Typical commands

```powershell
python real\bootstrap_realsports_session.py
python real\read_real_player.py --sport nba --season 2026
python real\lineup.py --sport nba --date 2026-04-20 --season 2025
python real\ingest_public_markets.py --providers kalshi --sports nba,mlb,nhl --output real\sportsbook_markets.csv
python real\live_polls.py --source sport-polls --sport mlb --output real\live_polls.csv
python real\poll_market_matcher.py --polls-csv real\live_polls.csv --markets-csv real\sportsbook_markets.csv
```

## Rank workbooks

`real/rank/` stores the smoothed ranking workbooks used by `real/rank.py` and `real/picks.py`.

See [`real/rank/README.md`](/c:/2k/real/rank/README.md) for the workbook details and workflow.

## Sportsbook Market Schema

The poll matcher expects a sportsbook market CSV shaped like:

- `book`
- `sport`
- `market_type`
- `stat`
- `player_name`
- `line`
- `home_team`
- `away_team`
- `over_odds`
- `under_odds`
- `updated_at`
- `period`

Example starter file:

- [`real/sportsbook_markets.example.csv`](/c:/2k/real/sportsbook_markets.example.csv)

## Market Ingestion

The default market-ingestion path is now free/public-source first, with partial browser-backed support for blocked sportsbook sites.

Current public providers:

- `Kalshi`
  - public sports market data from the unauthenticated market-data endpoints
- `Polymarket`
  - public sports/event discovery from the Gamma API
- `DraftKings`
  - parser and provider wiring are in place, including live `sportsbook-nash.draftkings.com` pulls for NBA game lines, points O/U, and player milestone ladders
  - current live NBA coverage now also includes rebounds O/U, assists O/U, threes made O/U, PRA O/U, PR O/U, PA O/U, RA O/U, steals O/U, blocks O/U, steals+blocks O/U, plus main-line 1st quarter spread
  - player milestone ladders like `25+ Points` are currently normalized as synthetic `24.5` over lines so they can participate in fair-line fitting, even when DraftKings does not expose the explicit under side in the captured payload
  - the default live DraftKings path now uses the newer `sportscontent/controldata/...` endpoints instead of the older blocked `eventgroups` API
- `FanDuel`
  - parser and provider wiring are in place, but this host currently gets CloudFront-blocked on direct scripted fetches

Typical flow:

```powershell
python real\ingest_public_markets.py --providers kalshi --sports nba,mlb,nhl --output real\sportsbook_markets.csv
python real\live_polls.py --source sport-polls --sport mlb --output real\live_polls.csv
python real\poll_market_matcher.py --polls-csv real\live_polls.csv --markets-csv real\sportsbook_markets.csv
```

Notes:

- `live_polls.py` can now source polls three ways:
  - `--source livefeed` for the older mixed feed
  - `--source home --sport mlb` for the sport home cards
  - `--source sport-polls --sport mlb` for the dedicated MLB Polls tab, which is the cleaner source for current MLB poll posts
- `Kalshi` is currently the stronger fit for the existing Real Sports over/under workflow because it exposes line-based sports markets directly.
- `Polymarket` is available with `--providers kalshi,polymarket`, but only numeric over/under style sports questions are normalized today. Futures and generic winner markets stay available in the raw dumps, but they are not yet part of the default poll matcher.
- `DraftKings` and `FanDuel` are now wired into the ingester as providers, but on this machine their official hosts currently reject scripted requests even with browser impersonation. The provider code supports saved official payloads and saved request configs under `real/.cache/sportsbook_payloads/` and `real/.cache/sportsbook_requests/`, so one browser-captured request can unblock them without changing the parser code.
- DraftKings/FanDuel request configs can now also carry a real provider proxy route with `proxy_url`, plus a custom browser fingerprint via `impersonate`, if you have a working U.S. VPN/proxy path outside the dead repo-wide `127.0.0.1:9` env proxy.

Example request config:

```json
{
  "proxy_url": "socks5://127.0.0.1:1080",
  "impersonate": "chrome136",
  "headers": {
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
  },
  "sports": {
    "mlb": {
      "urls": [
        "https://sportsbook.draftkings.com/sites/US-SB/api/v5/eventgroups/84240?format=json"
      ]
    }
  }
}
```

## Optional Paid Fallback

`ingest_odds_api_io.py` is still available as a convenience fallback if you later decide to use a paid aggregator.

It expects:

- `ODDS_API_IO_KEY`
  - or `ODDS_API_KEY`

Typical flow:

```powershell
$env:ODDS_API_IO_KEY='your-key'
python real\ingest_odds_api_io.py --sport baseball --league mlb --output real\sportsbook_markets.csv
python real\live_polls.py --output real\live_polls.csv
python real\poll_market_matcher.py --polls-csv real\live_polls.csv --markets-csv real\sportsbook_markets.csv
```
