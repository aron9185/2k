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
  - pulls Rotowire optimizer projections, applies Real Sports multipliers, writes `lineup.csv`, and keeps per-sport snapshots in `lineups/`
- `fair_odds.py`
  - core fair-line / devig / EV / Kelly math for sportsbook consensus markets
- `sportsbook_catalog.py`
  - canonical target-book list, alias normalization, categories, and default source weights
- `live_polls.py`
  - pulls current Real Sports polls from the live feed, sport home tabs, or the dedicated sport-polls tab and writes `live_polls.csv`
- `predictions.py`
  - inspects Real prediction markets, buy/sell order tickets, and position pages
- `recommend_prediction_markets.py`
  - matches current Real prediction markets against sportsbook consensus and calculates buy EV in rax
- `recommend_prediction_positions.py`
  - compares current open Real prediction positions against sportsbook fair value and suggests hold vs cashout
- `render_prediction_sheet.py`
  - renders the prediction EV CSV into a markdown sheet under `real/output/`, and can include open positions in the same file
- `render_prediction_positions_sheet.py`
  - renders the open-position hold/cashout CSV into a markdown sheet under `real/output/`
- `refresh_dashboard_data.py`
  - refreshes stable dashboard markdown files under `real/output/dashboard/` so a local HTML view can update in place without creating a new versioned file every cycle
- `dashboard_server.py`
  - serves a local HTML dashboard for the vote-sheet and prediction markdown outputs, with optional background refresh
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
- `lineups/`
- `fantasy_points.json`
- `fantasy_points.txt`
- `picks.txt`
- `picks_ev.txt`
- `rating.txt`
- `rank.zip`
- `tmp/`
- `output/`
- `output/dashboard/`
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
python real\ingest_public_markets.py --providers draftkings --sports mlb --force-live --output real\sportsbook_markets.csv
python real\ingest_public_markets.py --providers draftkings,fanduel --sports mlb,nba,nhl --force-live --output real\sportsbook_markets_consensus_live.csv
python real\ingest_public_markets.py --providers draftkings,fanduel --sports soccer --force-live --output real\sportsbook_markets_soccer_live.csv
python real\live_polls.py --source sport-polls --sport mlb --output real\live_polls.csv
python real\live_polls.py --source game-feed --sport mlb --game-id 823878 --output real\live_polls_game.csv
python real\recommend_game_feed_polls.py --sport mlb --markets-csv real\sportsbook_markets_consensus_live.csv --output real\poll_vote_recommendations_consensus_mlb.csv
python real\render_vote_sheet.py --input real\poll_vote_recommendations_consensus_mlb.csv --not-started-only
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
- `draw_odds`
- `extra_outcomes`
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
  - soccer support currently pulls Champions League markets from the soccer league page, including moneyline, first-half total goals, both teams to score, double chance, and first-half moneyline
- `FanDuel`
  - live content-page pulls are working for MLB/NBA/NHL when a valid U.S. network path is available
  - MLB player ladder markets such as home runs, hits, total bases, RBI, and stolen bases are normalized as synthetic over/under rows so they can join the consensus fit
  - soccer live pulls now use FanDuel's soccer/event-tab/price endpoints for Champions League by default and normalize match result, game totals, first-half totals, both teams to score, double chance, and half-time result
  - FanDuel soccer can be expanded with `competition_ids`, `event_limit`, and `tab_title_keywords` request-config overrides when we want leagues beyond the default Champions League path

Typical flow:

```powershell
python real\ingest_public_markets.py --providers kalshi --sports nba,mlb,nhl --output real\sportsbook_markets.csv
python real\ingest_public_markets.py --providers draftkings --sports mlb --force-live --output real\sportsbook_markets.csv
python real\ingest_public_markets.py --providers draftkings,fanduel --sports mlb,nba,nhl --force-live --output real\sportsbook_markets_consensus_live.csv
python real\ingest_public_markets.py --providers draftkings,fanduel --sports soccer --force-live --output real\sportsbook_markets_soccer_live.csv
python real\live_polls.py --source sport-polls --sport mlb --output real\live_polls.csv
python real\predictions.py --kind markets --sport mlb --output real\tmp\mlb_prediction_markets.csv --dump-json real\tmp\mlb_prediction_markets.json
python real\predictions.py --kind order --market-id 4456 --mode buy --output real\tmp\prediction_marketorder_4456_buy.csv
python real\predictions.py --kind position --position-id 13926000 --output real\tmp\prediction_position_13926000.csv
python real\recommend_prediction_markets.py --sport mlb --markets-csv real\sportsbook_markets_consensus_live.csv --output real\prediction_market_recommendations_mlb.csv
python real\render_prediction_sheet.py --input real\prediction_market_recommendations_mlb.csv --positions-input real\prediction_position_recommendations_mlb.csv
python real\recommend_prediction_positions.py --sport mlb --markets-csv real\sportsbook_markets_consensus_live.csv --output real\prediction_position_recommendations_mlb.csv
python real\render_prediction_positions_sheet.py --input real\prediction_position_recommendations_mlb.csv
python real\recommend_game_feed_polls.py --sport mlb --markets-csv real\sportsbook_markets_consensus_live.csv --output real\poll_vote_recommendations_consensus_mlb.csv
python real\render_vote_sheet.py --input real\poll_vote_recommendations_consensus_mlb.csv --not-started-only
python real\render_vote_sheet.py --input real\poll_vote_recommendations_consensus_mlb.csv --not-started-only --refresh-predictions
python real\poll_market_matcher.py --polls-csv real\live_polls.csv --markets-csv real\sportsbook_markets.csv
```

Notes:

- `live_polls.py` can now source polls three ways:
  - `--source livefeed` for the older mixed feed
  - `--source home --sport mlb` for the sport home cards
  - `--source sport-polls --sport mlb` for the dedicated MLB Polls tab, which is the cleaner source for current MLB poll posts
- `live_polls.py --source game-feed --sport <sport> --game-id <id>` now reads a single game feed from `https://web.realapp.com/games/{gameId}/sport/{sport}/feed`
  - this is the best source when you want the exact pre-game cards shown inside one matchup, including gamewinner, totals, period specials, and player cards tied to that game
  - not every game-feed card is a wagerable poll; lineup contests and unresolved anytime-play cards may appear alongside normal polls
- `Kalshi` is currently the stronger fit for the existing Real Sports over/under workflow because it exposes line-based sports markets directly.
- `Polymarket` is available with `--providers kalshi,polymarket`, but only numeric over/under style sports questions are normalized today. Futures and generic winner markets stay available in the raw dumps, but they are not yet part of the default poll matcher.
- `DraftKings` and `FanDuel` are wired into the ingester as sportsbook providers. The provider code also supports saved official payloads and saved request configs under `real/.cache/sportsbook_payloads/` and `real/.cache/sportsbook_requests/`, so one browser-captured request can unblock or replay a provider without changing parser code.
- Use `--force-live` with DraftKings/FanDuel when you need current odds instead of replaying the saved payload cache. If the live request succeeds, the saved payload is refreshed.
- DraftKings/FanDuel request configs can now also carry a real provider proxy route with `proxy_url`, plus a custom browser fingerprint via `impersonate`, if you have a working U.S. VPN/proxy path outside the dead repo-wide `127.0.0.1:9` env proxy.
- FanDuel request configs can use simple GET `urls`, explicit `requests` entries with `method`, `url`, and optional JSON `payload`, or soccer-specific `competition_ids`/`tab_title_keywords` overrides.
- For consensus voting, point `recommend_game_feed_polls.py` at `real\sportsbook_markets_consensus_live.csv`. Rows that match both books show `books=draftkings | fanduel`; unsupported market families or markets available from only one book remain single-source.
- Markdown sheet outputs now live under `real\output\` so they are easy to find.
- If you omit `--output`, `render_vote_sheet.py` writes versioned markdown files like `real\output\mlb_v1.md`, `real\output\nba_v2.md`, or `real\output\nhl_v3.md` instead of date-stamped filenames.
- `render_vote_sheet.py` automatically includes the matching daily `lineup.py` snapshot from `real\lineups\<sport>.csv` when it exists for that sport and slate date.
- `render_vote_sheet.py` also auto-includes same-sport prediction buy and open-position sections when matching `prediction_market_recommendations_<sport>.csv` and `prediction_position_recommendations_<sport>.csv` files exist.
- Use `render_vote_sheet.py --refresh-predictions` when you want the combined vote sheet to pull fresh Real prediction prices and cashout values before rendering.
- `recommend_anytime_rbi_polls.py` is now CSV-first. It only writes a markdown sheet when you explicitly pass `--sheet-output`.
- The main MLB vote sheet handles `Anytime RBI` with the weighted zero-cost sportsbook ranking rule when same-game candidate markets exist.
- `refresh_dashboard_data.py` writes stable files like `real\output\dashboard\nba.md` and `real\output\dashboard\nba_predictions.md` so the local HTML dashboard can refresh in place instead of creating `v15`, `v16`, `v17`, and so on.
- `dashboard_server.py` serves those stable markdown files as a local HTML interface and can run timed refreshes in the background.

Dashboard commands:

```powershell
python real\refresh_dashboard_data.py --sports mlb,nba,nhl
python real\refresh_dashboard_data.py --sports mlb,nba,nhl --refresh-soccer
python real\dashboard_server.py --host 127.0.0.1 --port 8765
python real\dashboard_server.py --host 127.0.0.1 --port 8765 --refresh-on-start --refresh-seconds 180
```

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
        "https://sbapi.nj.sportsbook.fanduel.com/api/content-managed-page?currencyCode=USD&exchangeLocale=en_US&includePrices=true&language=en&regionCode=NAMERICA&timezone=America/New_York&_ak=FhMFpcPWXMeyZxOx&page=SPORT&sport=BASEBALL"
      ]
    },
    "soccer": {
      "competition_ids": [228],
      "event_limit": 12,
      "tab_title_keywords": ["popular", "goals", "half"]
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
