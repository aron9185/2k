# Sportradar Synergy Playmaking Notes

This note tracks the first Synergy endpoints we want to pull before we decide how to use them.

## First endpoints to pull

1. `seasons`
   - Path: `/synergy/basketball/{league}/seasons`
   - Why: resolve the live `seasonId` before every season-level pull.

2. `playerplaytypestats`
   - Path: `/synergy/basketball/{league}/seasons/{seasonId}/events/reports/playerplaytypestats`
   - Why: best first season-level source for player offensive role, usage style, and playmaking profile.

3. `player events`
   - Path: `/synergy/basketball/{league}/seasons/{seasonId}/players/{playerId}/events`
   - Why: possession-level fallback when we need to derive custom logic ourselves.

## Candidate play types to validate first

These are the first values to test because they overlap with the old NBA.com Synergy pulls already used in this repo:

- `PRBallHandler`
- `Handoff`
- `Isolation`
- `Transition`
- `Cut`
- `SpotUp`
- `OffScreen`
- `PRRollman`
- `PostUp`

If Sportradar uses different enum names than NBA.com, we should keep a small mapping table here after the first successful live pull.

## Live enum notes from the first pull session

Validated against the live API on April 16, 2026:

- `PRBallHandler` should be queried as `PandRBallHandler`
- `PRRollman` should be queried as `PandRRollMan`
- `Isolation` should be queried as `ISO`
- `SpotUp`, `PostUp`, `Handoff`, `Cut`, `Transition`, and `OffScreen` all work as written

The pull script now auto-translates the validated old NBA.com aliases for:

- `PRBallHandler`
- `PRRollman`
- `Isolation`
- `Spotup`
- `Postup`
- `Offscreen`

## Planned repo usage

- `stats/`
  - use `playerplaytypestats` as the first Synergy pull target
  - save raw API responses to `stats/tmp/`
  - save normalized tabular outputs to `stats/history/`

- `Upgrade/`
  - write a normalized long-form CSV with `PLAYER_NAME`, `PLAY_TYPE`, and `POSS_PCT`
  - feed that into the existing top-4 playtype summary logic in `process.py` and `process_to_presets.py`

## First live command to try

```powershell
$env:SPORTRADAR_SYNERGY_API_KEY="YOUR_KEY"
python3 stats\scripts\pull_sportradar_synergy.py seasons --league nba
```

Then:

```powershell
python3 stats\scripts\pull_sportradar_synergy.py player-playtypes --league nba --season-label 2025-26 --play-type PRBallHandler
```
