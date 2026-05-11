# Dashboard + Odds Refresh Session Changelog (2026-05-12)

## Scope of this commit

This session focused on dashboard presentation reliability and odds-refresh correctness/performance, with emphasis on:

- Live poll refresh speed and targeting
- Better sportsbook market coverage for NBA/WNBA/MLB/Soccer
- Correct poll-kind routing (winner vs spread vs first-basket)
- Dog-of-the-day moneyline handling
- Dashboard startup/logging and refresh resilience

Only dashboard/odds-refresh related code and documentation are included here (no probe/temp CSVs, no cached runtime artifacts).

---

## Key behavioral changes

## 1) Live poll refresh now runs targeted recrawls instead of broad crawls

### Files
- `real/recommend_live_polls.py`
- `real/provider_draftkings.py`
- `real/provider_fanduel.py`

### What changed
- Live refresh now:
  1. Reads open (non-closed) live polls first.
  2. Builds market requirements from those polls (sport, game, poll kind, player/stat/line/period).
  3. Recrawls only needed sports and game pairs where possible.
- Added stale-window control:
  - `--min-market-refresh-interval-seconds` (default 900).
  - If the CSV is fresh and already covers open-poll requirements, recrawl is skipped.
- Added requirement coverage checks/fallback checks (including soccer shots -> goals fallback compatibility check for coverage).
- Added poll-prefetch pass so poll fetch is done once per loop and reused by recommendation rendering.
- Added provider market-scope auto-selection:
  - `game-lines` scope for team/game polls.
  - `all` scope when player props are needed.

### Result
- Faster live refresh loops, fewer unnecessary sportsbook calls, better stability when many sports/tabs are active.

---

## 2) DraftKings provider coverage expanded (WNBA + first-basket + targeted game pulls)

### File
- `real/provider_draftkings.py`

### What changed
- Added WNBA eventgroup support and WNBA league/event subcategory coverage.
- Added first-basket market parsing support (including first field goal phrasing).
- Added targeted event selection by normalized team pair for event-subcategory pulls.
- Improved soccer targeted league/event fetch mode for matched games.
- Relaxed selection parsing gate so single-selection milestone markets (notably certain soccer assists/player milestone markets) are not dropped.

### Result
- Better WNBA market coverage and lower no-market rate for targeted refreshes.
- Better first-basket handling for live/special polls.
- More efficient per-game refresh behavior.

---

## 3) FanDuel provider coverage expanded and quarter-winner routing fixed

### File
- `real/provider_fanduel.py`

### What changed
- Added targeted team-pair filtering support to fetch paths.
- Expanded tab keyword coverage:
  - MLB live batter/pitcher props coverage hooks
  - WNBA tab families (points/threes/rebounds/assists/combos/defense/quarter)
  - NBA SGP/tab variants where relevant
- Added WNBA first-basket payload completeness check.
- Added first-basket parsing guards (avoid false positives like first team basket).
- Expanded player single-side stat parsing (including hits+runs+RBIs variants).
- **Fixed moneyline market classification for quarter-winner markets**, including:
  - market names like `1st Quarter Winner`
  - type keys like `1ST_QUARTER_WINNER`
  - quarter match-betting type variants

### Result
- NBA 1Q winner polls can use direct sportsbook quarter-winner markets rather than spread proxy when available (especially from FanDuel data).

---

## 4) Poll recommendation logic improvements (kind detection, spread routing, dog logic)

### File
- `real/recommend_game_feed_polls.py`

### What changed

#### Poll kind detection
- `_poll_kind` now considers `poll` + `post` context, not only `additionalInfo`.
- Detects first-basket/first-field-goal text variants.
- Distinguishes `game_spread` from `game_winner` when spread indicators exist (point spread fields or spread language in content).

#### Player parsing robustness
- Better player name inference from poll text using separators and numeric-line prefix handling.

#### Game total odds behavior
- For game totals, switched to prefer direct exact-line consensus where available (`prefer_fitted_ladder=False`) to reduce misleading fitted-line output when exact lines exist.

#### Live spread handling
- If spread side/team id is missing, spread side is inferred from closest sportsbook lines instead of immediate failure.
- Notes now explicitly mark when spread side was inferred.

#### Period winner support
- Added WNBA quarter period code mapping (`1Q..4Q`) for period winner flow.

#### First-basket zero-cost polls
- Added explicit no-market note for first-basket when no same-game first-basket sportsbook props exist.

#### Daily pool/dog behavior
- Added generalized option-label helpers for fair prob / sportsbook odds / real odds resolution.
- Daily pool rows now tolerate partial missing child polls with neutral placeholders, instead of hard failing full pool synthesis.
- Daily dog rows now evaluate underdog candidates via moneyline consensus flow and emit clearer notes:
  - “Daily dog moneyline ...”
- Added use of sportsbook moneyline consensus from matched market rows for underdog selection.

### Result
- Better routing accuracy (winner vs spread vs first-basket).
- Better EV logic consistency for dog/pool rows.
- Fewer false “unsupported/no market” outcomes when poll metadata is incomplete but content text is informative.

---

## 5) Team alias normalization expanded for Soccer + WNBA naming variance

### File
- `real/poll_market_matcher.py`

### What changed
- Added extensive alias coverage for:
  - WNBA team keys/names
  - MLS / EPL / La Liga / Serie A / Bundesliga shorthand and naming variants
  - Additional soccer team canonicalization variants used in sportsbook vs Real app labels

### Result
- Higher same-game match hit rate between Real poll teams and sportsbook teams.
- Reduced “No sportsbook match” due solely to naming mismatch.

---

## 6) Live poll ingestion filter correctness fix

### File
- `real/live_polls.py`

### What changed
- Fixed filtering condition to respect `wagerable_only` directly (instead of binding to `include_locked` behavior).

### Result
- More consistent inclusion/exclusion of live polls when polling feed snapshots.

---

## 7) Dashboard refresh/runtime resilience and logging improvements

### Files
- `real/refresh_dashboard_data.py`
- `real/start_dashboard.ps1`
- `real/render_vote_sheet.py`
- `real/render_prediction_sheet.py`

### What changed

#### `refresh_dashboard_data.py`
- Increased CSV field size limit before reads to avoid large-field parsing failures.
- Open-live-poll check no longer hard-rejects non-wagerable flag before lock-time evaluation.
- Lineup context refresh now uses non-fatal step execution with warning fallback (dashboard render continues).
- Live-poll refresh now uses combined market CSV input and targeted refresh mode with min refresh interval.

#### `start_dashboard.ps1`
- Split stdout/stderr into separate files:
  - `.cache/dashboard/dashboard_server.out.log`
  - `.cache/dashboard/dashboard_server.err.log`
- Simplified argument forwarding (removes unnecessary flags).
- Improved startup output with explicit error-log path.

#### `render_vote_sheet.py` + `render_prediction_sheet.py`
- Increased CSV field size limit before reads to handle large embedded fields safely.

### Result
- More robust refresh loop under large CSV payloads.
- Better operational visibility when dashboard startup/refresh fails.

---

## 8) Fair-line display correction for exact-line consensus

### File
- `real/fair_odds.py`

### What changed
- For exact-line consensus, fair line is anchored to the evaluated target line instead of surfacing ladder-fit drift from unrelated lines.

### Result
- Cleaner, less confusing `consensus_fair_line` display when exact market line is already known.

---

## Validation performed in-session

- Re-ran NBA targeted market + recommendation flow using local cached sportsbook payloads.
- Verified DET @ CLE 1Q poll row moved from:
  - `... 1Q spread proxy for period winner`
  to:
  - direct quarter-winner moneyline note from sportsbook source.

---

## Notes

- This commit intentionally excludes temporary probe files and generated runtime artifacts.
- Existing historical CSV/json datasets in the repo working tree were left untouched unless they are part of code-path behavior updates.
