# 2k26 Badges Notes

Workbook inspected:

- `2k26_badges.xlsx`

Sheet seen:

- `「25Badges_cal」的副本`

## Workbook Format

- Identity columns are `NBA ID`, `Season`, and `Player`.
- Badge output columns return integer badge tiers on the workbook scale `0-5`.
- Helper columns are a mix of:
  - direct final 2K ratings on the normal `25-99` scale
  - raw or normalized stat lanes that the workbook percentile-ranks with `PERCENTRANK(...)`
  - weighted helper aggregates built inside the workbook before thresholding into tiers

## Direct Rating Aliases

These workbook headers should come from the finished ratings exports:

- `Close`, `Mid`, `3PT` -> `stats/exports/shooting_all_ratings.csv`
- `Layup`, `ST   Dunk`, `Dunk`, `PHook`, `PFade`, `PostC` -> `stats/exports/finishing_all_ratings.csv`
- `Ball`, `SPD/BALL`, `Pass` -> `stats/exports/playmaking_all_ratings.csv`
- `ID`, `PD`, `STL`, `BLK`, `Help Defense IQ`, `Pass Perception` -> `stats/exports/defense_all_ratings.csv`
- `OREB`, `DREB` -> `stats/exports/rebounding_all_ratings.csv`
- `SPEED`, `Agility`, `STR`, `VERT`, `STAM` -> `stats/exports/physical_all_ratings.csv`

## Confirmed Raw / Helper Sources

- `FG3M_tight`, `FG3_PCT_tight` -> `stats/history/closest_defender.csv`
- `FG3M_verytight`, `FG3_PCT_verytight`
  - automated build behavior now uses `tight + very tight` combined totals
  - source stack: `stats/history/closest_defender.csv` + `stats/history/very_tight.csv`
  - combined `FG3_PCT` is rebuilt from combined `FG3M / FG3A`
- `CATCH_SHOOT_FGM`, `CATCH_SHOOT_EFG_PCT` -> `stats/history/tracking_c&s.csv`
- `PULL_UP_FGM`, `PULL_UP_EFG_PCT` -> `stats/history/pullup.csv`
- `Spotup_FGM`, `Spotup_PPP` -> `stats/history/spotup.csv` using `FGM` and `PPP`
- `OFFSCREEN_POSS` -> `stats/history/off_screen.csv`

## Bball-Index Metrics Confirmed In The Catalog

These exist in `stats/manual/bball_index_metric_catalog.csv` and should be added to the
relevant bball-index pulls when the badge builder is wired:

- `Pull-Up Shooting Talent`
- `Lob Passing Creation Rate`
- `Off-Ball Shot Making`

## Special Badge Build Rules Confirmed By User

- `>24ft_FGM`
  - intended source: the NBA.com `8ft Range` shooting dashboard export
  - use the wide history export `stats/history/shot_locations_by_distance_8ft.csv`
  - the exact column name still needs to be confirmed from a live pull

- `Alley_Oop_FGM`
  - derive from `stats/history/shooting_splits.csv`
  - sum the shot-type `_FGM` columns the same way the standing-dunk flow sums shot types
  - include all shot types containing `Alley Oop`

- `Putback_FGM`
  - derive from `stats/history/shooting_splits.csv`
  - sum every shot-type `_FGM` column containing `Putback`
  - if a player row is missing, only default it to `0` when that season has confirmed shot-type coverage from other players

- `Putback Frequency%`
  - derive from `stats/history/nbarapm.csv`
  - current proxy: `SelfORebPct * TeammateMissORebPerc / 100`
  - this keeps the lane tied to offensive rebounding instead of putback shot volume
  - seasons before the local nbarapm history starts still stay blank for now

## Implementation Notes

- Many workbook helpers already exist in the generated `*_sheet.csv` exports, so the badge
  builder can often read from those sheet-style exports instead of recomputing raw metrics.
- The first automated badge entrypoint is now `stats/scripts/build_badges_all.py`.
