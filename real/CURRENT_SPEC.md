# Real Sports Current Spec

Last updated: 2026-05-04 (UTC+8)

## 1) Scope

This spec covers the current `real/` pipeline used by the local dashboard and sheets:

- Pre-game vote recommendations
- Live-poll recommendations
- Prediction market EV and position tracking
- Sportsbook consensus ingestion (DraftKings/FanDuel, optional BetMGM via odds-api key)
- Lineup contest projections and sheet rendering

## 2) Supported sports

- `mlb`
- `nba`
- `nhl`
- `soccer`
- `wnba`

## 3) Core outputs

- `poll_vote_recommendations_consensus_<sport>.csv`
- `prediction_market_recommendations_<sport>.csv`
- `prediction_position_recommendations_<sport>.csv`
- `lineup.csv` (+ per-sport snapshots in `lineups/`)
- Stable dashboard markdown under `output/dashboard/`:
  - `<sport>.md`
  - `<sport>_predictions.md`

## 4) Market ingestion behavior

Primary consensus feed:

- `ingest_public_markets.py --providers draftkings,fanduel --force-live`

Optional third source:

- `betmgm` is enabled only when `ODDS_API_IO_KEY` (or `ODDS_API_KEY`) is set.
- Direct BetMGM website scraping is not default/reliable due to anti-bot and affiliate access controls.

Soccer:

- Uses soccer-specific market CSV path when refreshed independently.

## 5) Refresh behavior

Per-sport manual refresh:

- Refresh actions are sport-scoped (do not refresh all sports unless explicitly requested).

Live/Predictions auto-refresh:

- Auto refresh should run only while the related page/tab is open.
- Pre-game refresh should not be blocked by always-on background polling.

Queued manual refresh:

- Multiple refresh clicks may be queued in order.
- Execution should remain serialized (not parallel) unless explicitly changed.

## 6) Poll recommendation rules

General:

- Match polls to same-game sportsbook markets first.
- Fall back only when direct market family match is unavailable.
- Prefer consensus across multiple books when present.

Live polls:

- Prefer live sportsbook markets for live poll matching.

No-market fallback:

- If shot-on-goal player props are unavailable, fallback may use closest supported scorer market logic (sport-specific).

## 7) Dog of the day rules

Only one synthetic Dog row should be rendered per applicable sport/day view.

Selection constraints:

- Prefer actual Real daily dog post (`isDailyDog`) when present.
- Candidate must be a true underdog side from sportsbook-matched winner odds (plus-money side only).
- Do not treat favorites (negative moneyline) as underdogs.

Selection objective:

- Choose highest EV among valid underdog choices.

## 8) Ordering rules

Game ordering on sheets should follow Real app ordering for that sport/day.

- Do not re-rank by local tie-breakers (for example game time) when Real already provides order intent.
- Live poll display should follow Real order and can suppress redundant time text when configured.

## 9) Timestamp rules

Dashboard and prediction rows should carry update timestamps.

- User-facing time normalization target: `UTC+8` for this workflow.

## 10) Naming/UX conventions

Tab/button labels must be sport-specific and unambiguous, especially where both pre-game and vote-sheet views exist.

Examples:

- Avoid generic "Refresh Now" when context is sport-specific.
- Use explicit labels like "Refresh MLB pre-game data".

## 11) Known operational dependencies

- Valid Real auth/session cache files
- Reachable sportsbook endpoints (VPN/proxy path may be required for some providers)
- Rotowire fetch availability for lineup contest projections

