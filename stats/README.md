# Ratings Stats Workspace

This folder now separates scripts from data so the ratings workflow is easier to maintain.

## Layout

- `scripts/`
  - Python helpers and automation entry points.
  - See `scripts/README.md` for the current entrypoint map.
- `tmp/`
  - Fresh NBA.com pull outputs such as `*_tmp.csv`.
  - Safe to refresh often.
  - `tmp/synergy/` keeps raw Sportradar Synergy JSON pulls out of the shared tmp root.
- `history/`
  - Long-term merged CSV history used by the ratings workflow.
  - `history/synergy/` keeps Synergy CSV outputs separate from the NBA.com and bball-index history files.
- `manual/`
  - Manual inputs, legacy pasted responses, and reference files.
  - Examples: `nbacom.txt`, `playerlist.csv`, `.xlsx` reference files, raw JSON dumps.
- `exports/`
  - Paste-ready CSV slices for Google Sheets.
  - `exports/archive/` stores old experiments and test snapshots we no longer treat as the current source of truth.

## Main Commands

Refresh tmp pulls only:

```powershell
python3 stats\scripts\pull_nba_stats.py --season 2025-26 --jobs all --retries 1
```

Refresh and merge into long-term history:

```powershell
python3 stats\scripts\pull_nba_stats.py --season 2025-26 --jobs all --retries 1 --merge
```

Merge existing tmp files without refetching:

```powershell
python3 stats\scripts\pull_nba_stats.py --season 2025-26 --jobs all --merge --merge-only
```

List exportable sources and value modes:

```powershell
python3 stats\scripts\export_sheet_slice.py --list-sources
```

Export a paste-ready sheet slice:

```powershell
python3 stats\scripts\export_sheet_slice.py --source general_traditional --season 2025-26 --columns FGM FGA FG_PCT
```

Build one `DataPull -> Cal` lane from the database player list + history stats:

```powershell
python3 stats\scripts\build_cal_lane.py --source general_traditional.csv --metric GP --lane-name gp
```

Build the first `FT%` lane using live minutes from `general_traditional.csv`:

```powershell
python3 stats\scripts\build_cal_lane.py --metric FT_PCT --lane-name ft_pct
```

By default, `build_cal_lane.py` uses `stats/manual/playerlist.csv` as the identity universe.
If you want `RotationRole` / `MIN` enrichment from `stats/manual/player_universe.csv`, pass it explicitly with `--details-csv`.

Pull the live bball-index Layup metrics used by the `Finishing` tab:

```powershell
python3 stats\scripts\pull_bball_index.py --metrics "Finishing Talent" "Rim Shot Creation" "Drives Per 75 Possessions" "Rim Shot Making" "Rim Shot Making Efficiency" "Paint Shooting Talent" "Rim Makes Consistency" "Stable Rim FG%" "Contact Finish Rate" "Rim Attempt Consistency" "Rim FG%" "Drive Foul Drawn Rate" --output stats\history\bball_index_layup.csv
```

Build the first `Finishing -> Layup` export:

```powershell
python3 stats\scripts\build_finishing_layup.py
```

Build the first `Finishing -> Standing Dunk` export:

```powershell
python3 stats\scripts\pull_nba_dunk_sources.py --season 2025-26
python3 stats\scripts\pull_bball_index.py --metrics "Putback Scoring Impact Per 75 Possessions" "Stable Putback Points Per 75" "Offensive Rebounding Crashing Skill" --output stats\history\bball_index_standing_dunk.csv
python3 stats\scripts\build_finishing_standing_dunk.py
```

Build the first `Finishing -> Driving Dunk` export:

```powershell
python3 stats\scripts\build_finishing_driving_dunk.py
```

Pull the live bball-index Post Hook metrics:

```powershell
python3 stats\scripts\pull_bball_index.py --metrics "Post Up Shot Making" "Post Up Impact Per 75 Possessions" "Stable Post Up PPP" "Post Up Shot Making Efficiency" "Post Up Shooting Talent" "Post Up Shot Quality" --output stats\history\bball_index_postup_full.csv
```

Build the first `Finishing -> Post Hook` export:

```powershell
python3 stats\scripts\build_finishing_post_hook.py
```

Build the first `Finishing -> Post Fade` export:

```powershell
python3 stats\scripts\build_finishing_post_fade.py
```

Build the first `Finishing -> Post Control` export:

```powershell
python3 stats\scripts\build_finishing_post_control.py
```

Build the first `Finishing -> Draw Foul` export:

```powershell
python3 stats\scripts\build_finishing_draw_foul.py
```

Build the first `Finishing -> Hands` export:

```powershell
python3 stats\scripts\build_finishing_hands.py
```

Rebuild every current `Finishing` lane and merge them into one CSV:

```powershell
python3 stats\scripts\build_finishing_all.py
```

Merge the existing lane outputs without rerunning every builder:

```powershell
python3 stats\scripts\build_finishing_all.py --skip-build
```

Rebuild every current `Shooting` lane and merge them into one CSV:

```powershell
python3 stats\scripts\build_shooting_all.py
```

Merge the existing `Shooting` lane outputs without rerunning every builder:

```powershell
python3 stats\scripts\build_shooting_all.py --skip-build
```

Pull the first combined `Playmaking` bball-index metric pack:

```powershell
python3 stats\scripts\pull_bball_index.py --metrics "On-Ball Action Share" "P&R Creation Rate" "Overall Shot Creation" "Guarded On-Ball %" "Average Dribbles Per Touch" "Dribbles Per Second on Offense" "Pull-Up Shot Creation" "Isolation Shooting Talent" "PnR Ball Handler Shot Making Efficiency" "One on One Shooting Talent" "Self-Created Shot Making" "Midrange Pull Up Shot Creation" "On-Ball Gravity" "Stable Isolation PPP" "Self-Created Shot Making Efficiency" "Guarded by Perimeter Isolation Defense" "Self-Created Openness Rating" "Overall Shot Making Efficiency" "Transition Shot Creation" "Transition Frequency Impact" "Offensive Transition Frequency Impact" "Movement Speed Rating" "Passing Efficiency" "Passing Creation Quality" "Potential Assists Per 100 Passes" "Role-Adjusted Potential Assists Per 100 Passes" "Turnovers Per 100 Touches" "Offensive eFG% Impact on Teammates" "Offense Impact on Teammate Shot Quality" "Stable Bad Pass Turnovers Per 75" "High Value Assists Per 75 Possessions" "Stable At Rim Assists Per 75" "Passing Versatility" "Passing Creation Volume" "Box Creation" "Assists Per 75 Possessions" "Stable Assists Per 75" --years-from 2013 --years-to 2026 --output stats\history\bball_index_playmaking.csv
```

Build the first combined `Playmaking` export:

```powershell
python3 stats\scripts\build_playmaking_all.py
```

Pull the `Impact` bball-index LEBRON-family pack from the live main `Metric` dropdown:

```powershell
python3 stats\scripts\pull_bball_index.py --metrics "LEBRON" "O-LEBRON" "D-LEBRON" "LEBRON WAR" "LEBRON Box Impact" "LEBRON Vs Role Average" "Multi-Year LEBRON" "O-LEBRON Box Impact" "LEBRON Offensive Points Added" "O-LEBRON Vs Role Average" "Multi-Year O-LEBRON" "Predictive O-LEBRON" "D-LEBRON Box Impact" "LEBRON Defensive Points Saved" "D-LEBRON Vs Role Average" "Multi-Year D-LEBRON" "Predictive D-LEBRON" --years-from 2015 --years-to 2026 --output stats\history\bball_index_impact.csv
```

Pull the public `nbarapm.com` impact datasets:

```powershell
python3 stats\scripts\pull_nbarapm_data.py
```

Pull one public `nbarapm.com` dataset only when needed:

```powershell
python3 stats\scripts\lebron.py
python3 stats\scripts\mamba.py
python3 stats\scripts\nbarapm.py
```

Pull the public current-season Dunks & Threes `EPM` table:

```powershell
python3 stats\scripts\pull_dunksandthrees_epm.py --season-end-year 2026
```

Build the first combined `Impact` export:

```powershell
python3 stats\scripts\build_impact_all.py
```

Pull the combined `Defense` bball-index metric pack from the live main `Metric` dropdown:

```powershell
python3 stats\scripts\pull_bball_index.py --metrics "Screener Rim Defense" "Rim Protection" "Rim Contests Per 75 Possessions" "Rim Points Saved Per 75 Possessions" "Stable Rim DFGA Per 75 Possessions" "Help Defensive Activity" "Help Defense Talent" "Help Effectiveness Rating" "Percentage of Shots at Rim Contested" "Post Defense" "Stable Rim dFG% vs. Expected" "Perimeter Isolation Defense" "Matchup Difficulty" "Guarded Shooting Talent" "Passing Lane Defense" "Deflections Per 75 Possessions" "% of Time Guarding Primary Ball Handlers" "Guarded 3PT Shooting Talent" "Guarded Midrange Talent" "% of Time Guarding Usage Tier 1 Players" "Off-Ball Chaser Defense" "Ball Screen Navigation" "Guarded 3PT Shot Creation" "3PT Contests Per 75 Possessions" "Steals Per 75 Possessions" "Pickpocket Rating" "Defensive Miles Per 75 Possessions" "Loose Ball Recovery Rate" "Stable Steals Per 75" "Stable Bad Pass Steals Per 75" "Stable Lost Ball Steals Per 75" "Blocks Per 75 Possessions" "Stable Blocks Per 75" "Stable Recovered Blocks Per 75" "Stable Recovered Blocks%" "Block Rate on Contests" "Defensive Playmaking" "Screener Mobile Defense" "Rim Deterrence" "Rim Deterrence Per 100" "DHO Coverage Versatility" "Defensive Role Versatility" "Overall Coverage Versatility" "P&R Coverage Versatility" --years-from 2015 --years-to 2026 --output stats\history\bball_index_defense.csv
```

Build the first combined `Defense` export:

```powershell
python3 stats\scripts\build_defense_all.py
```

Pull the combined `Rebounding` bball-index metric pack from the live main `Metric` dropdown:

```powershell
python3 stats\scripts\pull_bball_index.py --metrics "Offensive Rebounding Talent" "Offensive Rebounding Chances Per 75 Possessions" "Offensive Rebounding Crashing Skill" "Offensive Rebounds Per Game" "Stable Offensive Rebounds Per 75" "Offensive Rebounding Conversion Skill" "Adjusted Offensive Rebounding Success Rate" "Percentage of Offensive Rebounds Contested" "Defensive Rebounding Talent" "Defensive Reb Per 75 Possessions" "Defensive Rebounding Conversion Skill" "Defensive Rebounding Crashing Skill" "Defensive Rebounds Per Game" "Stable Defensive Rebounds Per 75" "Percentage of Defensive Rebounds Contested" "Adjusted Defensive Rebounding Success Rate" --years-from 2015 --years-to 2026 --output stats\history\bball_index_rebounding.csv
```

Build the first combined `Rebounding` export:

```powershell
python3 stats\scripts\build_rebounding_all.py
```

Pull the combined `Physical` bball-index metric pack from the live main `Metric` dropdown:

```powershell
python3 stats\scripts\pull_bball_index.py --metrics "Transition Shot Creation" "Avg Speed Offense" "Avg Speed Defense" "Movement Speed Rating" "Offensive Transition Frequency Impact" "Transition Frequency Impact" "Perimeter Isolation Defense" "Stable And 1s Per 75" "Post Defense" "Screen Assists Per 75 Possessions" "Finishing Talent" "Screening Talent" "Usage Rate" "True Usage" "Stable True Usage%" "Defensive Playmaking" --years-from 2015 --years-to 2026 --output stats\history\bball_index_physical.csv
```

Build the first combined `Physical` export:

```powershell
python3 stats\scripts\build_physical_all.py
```

Build the first workbook-shaped `Badges` export:

```powershell
python3 stats\scripts\build_badges_all.py
```

Build the first workbook-shaped `Tendency` export:

```powershell
python3 stats\scripts\build_tendency_all.py --allow-id-fallback
```

Fetch available Sportradar Synergy seasons for one league:

```powershell
$env:SPORTRADAR_SYNERGY_API_KEY="YOUR_KEY"
python3 stats\scripts\pull_sportradar_synergy.py --league nba seasons
```

By default, Synergy raw JSON now lands in `stats\tmp\synergy\` and normalized CSV output lands in `stats\history\synergy\`.

Fetch Synergy player play-type stats for one season:

```powershell
python3 stats\scripts\pull_sportradar_synergy.py --league nba player-playtypes --season-label 2025-26 --play-type PRBallHandler
```

Fetch possession-level Synergy events for one player:

```powershell
python3 stats\scripts\pull_sportradar_synergy.py --league nba player-events --season-label 2025-26 --player-id YOUR_PLAYER_ID
```

Pull the first `Shooting -> Close Shot` bball-index metric pack:

```powershell
python3 stats\scripts\pull_bball_index.py --metrics "Rim Shot Making" "Rim Shot Making Efficiency" "Rim Shot Attempts Per 75 Possessions" "Stable Rim FG%" "Stable Short Midrange FG%" "Floater Talent" "Paint Shooting Talent" "Paint Shot Making" "Paint Shot Making Efficiency" "Short Mid Range FG%" "Rim FG%" --years-from 2014 --years-to 2026 --output stats\history\bball_index_close_shot.csv
```

Build the first `Shooting -> Close Shot` export:

```powershell
python3 stats\scripts\build_shooting_close_shot.py
```

Pull the live wide shot-location export that keeps every NBA.com zone in one row:

```powershell
python3 stats\scripts\pull_nba_stats.py --season 2025-26 --jobs shot_locations_by_zone --retries 1 --merge
```

Pull the live very-tight defender shot dashboard export used for `FG3M_verytight` and `FG3_PCT_verytight` badge inputs:

```powershell
python3 stats\scripts\pull_nba_stats.py --season 2025-26 --jobs very_tight --retries 1 --merge
```

Pull the live wide 8ft-range shot-location export used to inspect distance buckets like `24+ ft`:

```powershell
python3 stats\scripts\pull_nba_stats.py --season 2025-26 --jobs shot_locations_by_distance_8ft --retries 1 --merge
```

Pull the first `Shooting -> Mid-Range Shot` bball-index metric pack:

```powershell
python3 stats\scripts\pull_bball_index.py --metrics "Midrange Talent" "Midrange Shot Making" "Midrange Shot Creation" "Midrange Pull Up Talent" "Midrange Pull Up Shot Making" "Midrange Pull Up Shot Making Efficiency" "Stable Long Midrange FG%" "Stable Short Midrange FG%" "Midrange FGM Per 75" "Midrange Pull Up FGM Per 75" "Midrange Shot Making Efficiency" "Midrange Pull Up FG%" --years-from 2014 --years-to 2026 --output stats\history\bball_index_mid_range.csv
```

Build the first `Shooting -> Mid-Range Shot` export:

```powershell
python3 stats\scripts\build_shooting_mid_range.py
```

Pull the first `Shooting -> 3-Point Shot` bball-index metric pack:

```powershell
python3 stats\scripts\pull_bball_index.py --metrics "3PT Shooting Talent" "3PT Shot Making" "C&S 3PT Shot Making" "3PT Pull Up Talent" "3PT Pull Up Shot Making" "3PT Pull Up Shot Creation" "3PT Shot Creation" "3PT Shot Making Efficiency" "C&S 3PT Shot Making Efficiency" "Stable FG3%" "Stable C&S 3PT%" "Stable ATB 3PT%" "Stable Pull Up 3PT%" "3PT Functional Versatility" "Stable 3PTA Per 75" "Off-Ball Gravity" "Stable Corner 3PT%" "3PT Attempt Rate" --years-from 2014 --years-to 2026 --output stats\history\bball_index_three_point.csv
```

Build the first `Shooting -> 3-Point Shot` export:

```powershell
python3 stats\scripts\build_shooting_three_point.py
```

Pull the first `Shooting -> Free Throw` bball-index metric pack:

```powershell
python3 stats\scripts\pull_bball_index.py --metrics "Stable FT%" --years-from 2014 --years-to 2026 --output stats\history\bball_index_free_throw.csv
```

Build the first `Shooting -> Free Throw` export:

```powershell
python3 stats\scripts\build_shooting_free_throw.py
```

## Current Output Notes

- `stats\exports\finishing_*` root files are the current finishing outputs used by the combined finishing pipeline.
- The latest validated shooting outputs now use the plain root `shooting_*` filenames.
- Those root shooting outputs keep per-stat clipping while leaving the aggregate category z-score unclipped, so the old `*_uncappedagg_test_*` suffix is no longer needed.
- `stats\exports\playmaking_all_ratings.csv` is the first automated combined playmaking pass from `stats/manual/2k26_playmaking.xlsx`.
- `stats\exports\impact_all_ratings.csv` is the first automated combined impact pass from `stats/manual/2k26_impact.xlsx`.
- `stats\exports\rebounding_all_ratings.csv` is the first automated combined rebounding pass from `stats/manual/2k26_rebound.xlsx`.
- `stats\exports\badges_all_sheet.csv` and `stats\exports\badges_all_badges.csv` are the first automated badge outputs from `stats/manual/2k26_badges.xlsx`.
- The automated badge build now treats the workbook's `FG3M_verytight` / `FG3_PCT_verytight` lanes as `tight + very tight` combined totals from NBA.com.
- `stats\history\bball_index_impact.csv`, `stats\history\lebron.csv`, and `stats\history\dunksandthrees_epm.csv` are now part of the active impact source stack.
- `stats\history\bball_index_rebounding.csv` is now part of the active rebounding source stack.
- `stats\history\dunksandthrees_epm.csv` only overrides the current season; older `EPM` history still falls back to `C:\2k\epm.xlsx`.
- Older experiment files such as `*_pipeline_test_*`, `*_cap_test_*`, `*_6919_*`, and `*_gate_02_test_*` can live in `stats\exports\archive\`.

Pull the first `Shooting -> Shot IQ` bball-index metric pack:

```powershell
python3 stats\scripts\pull_bball_index.py --metrics "Points Over Expectation / 75" "Stable eFG%" "Overall Shot Making Efficiency" "Overall Shot Making" "Self-Created Shot Making Efficiency" "Overall Shot Creation" "Role Adjusted Stable eFG%" --years-from 2014 --years-to 2026 --output stats\history\bball_index_shot_iq.csv
```

Build the first `Shooting -> Shot IQ` export:

```powershell
python3 stats\scripts\build_shooting_shot_iq.py
```

List the live bball-index metric catalog:

```powershell
python3 stats\scripts\pull_bball_index.py --list-metrics
```

Pull one live bball-index leaderboard metric:

```powershell
python3 stats\scripts\pull_bball_index.py --metrics "Games Played"
```

Pull the full bball-index metric catalog in batches of 10:

```powershell
python3 stats\scripts\pull_bball_index.py --metrics all --batch-size 10
```

`build_cal_lane.py` now refreshes `Cal` minutes from `general_traditional.csv`.
By default it treats nba.com `MIN` as per-game minutes and converts it to total minutes with `GP * MIN`,
then applies the same penalty rule across seasons while using a lower minute threshold for the latest season only.
It also matches by normalized `Season + Player Name` first, with accent stripping and nickname fixes,
and writes a Google-Sheets-ready `*_datapull_paste.csv` in `Season / Player / Stat` order for `DataPull` pasting.
The default player universe is `stats/manual/playerlist.csv`.
If you want role/minutes enrichment from `stats/manual/player_universe.csv`, pass it explicitly with `--details-csv`.
If you have a separate role/minutes source such as a future bball-index export, pass it in with:

```powershell
python3 stats\scripts\build_cal_lane.py --metric FT_PCT --lane-name ft_pct --details-csv stats\manual\player_details.csv
```

The `--details-csv` file can carry `Season`, `Player`, and optional `NBA_ID`, `RotationRole`, `MIN` columns.
If no details CSV is supplied, the script still falls back to the workbook for `RotationRole` and `MIN`.

`pull_bball_index.py` automates the live Shiny workflow:

- activates the main `Metric` dropdown so the full live catalog materializes
- sets the requested metrics
- clicks `Run Query`
- switches the DataTable from `25` rows to `All`
- writes the full leaderboard result to CSV

It also saves the discovered live metric catalog to `stats/manual/bball_index_metric_catalog.csv`.
That catalog now includes both a clean `Metric` name and the raw dropdown value used by the live app.
On the first browser run it may take a little longer while Selenium prepares its browser runtime.

All current `build_*` rating scripts clamp per-metric z-scores to `[-3, 3]` before they are written to
the audit or sheet-style exports. Final aggregate category z-scores are left unclamped so the percentile
rating curves can still separate the top end naturally.

`build_finishing_layup.py` writes:

- `stats/exports/finishing_layup_sheet.csv` for direct sheet-style pasting
- `stats/exports/finishing_layup_rating_only.csv` for final ratings only
- `stats/exports/finishing_layup_audit.csv` for debugging raw values, z-scores, and match paths
- `stats/exports/finishing_layup_unmatched.csv` for player-seasons missing one or more source metrics

`build_finishing_standing_dunk.py` writes:

- `stats/exports/finishing_standing_dunk_sheet.csv` for direct sheet-style pasting
- `stats/exports/finishing_standing_dunk_rating_only.csv` for final ratings only
- `stats/exports/finishing_standing_dunk_audit.csv` for debugging raw values, z-scores, and match paths
- `stats/exports/finishing_standing_dunk_unmatched.csv` for player-seasons missing one or more source metrics

`build_finishing_driving_dunk.py` writes:

- `stats/exports/finishing_driving_dunk_sheet.csv` for direct sheet-style pasting
- `stats/exports/finishing_driving_dunk_rating_only.csv` for final ratings only
- `stats/exports/finishing_driving_dunk_audit.csv` for debugging raw values, z-scores, and match paths
- `stats/exports/finishing_driving_dunk_unmatched.csv` for player-seasons missing one or more source metrics

`build_finishing_post_hook.py` writes:

- `stats/exports/finishing_post_hook_sheet.csv` for direct sheet-style pasting
- `stats/exports/finishing_post_hook_rating_only.csv` for final ratings only
- `stats/exports/finishing_post_hook_audit.csv` for debugging raw values, z-scores, and match paths
- `stats/exports/finishing_post_hook_unmatched.csv` for player-seasons missing one or more source metrics
- It derives the hook shot inputs by summing every shot-type column in `shooting_splits.csv` whose name contains `Hook`
- The reduced workbook's `BM` header is ambiguous, so the script keeps that support slot configurable with `--support-metric-column`

`build_finishing_post_fade.py` writes:

- `stats/exports/finishing_post_fade_sheet.csv` for direct sheet-style pasting
- `stats/exports/finishing_post_fade_rating_only.csv` for final ratings only
- `stats/exports/finishing_post_fade_audit.csv` for debugging raw values, z-scores, and match paths
- `stats/exports/finishing_post_fade_unmatched.csv` for player-seasons missing one or more source metrics
- It derives the fade shot inputs by summing every shot-type column in `shooting_splits.csv` whose name contains `Fade`
- Because the old workbook references `Post Up Frequency%` but that exact live bball-index metric is no longer exposed, the first build uses a tracking proxy: `(POST_TOUCHES / TOUCHES) * 100`

`build_finishing_post_control.py` writes:

- `stats/exports/finishing_post_control_sheet.csv` for direct sheet-style pasting
- `stats/exports/finishing_post_control_rating_only.csv` for final ratings only
- `stats/exports/finishing_post_control_audit.csv` for debugging raw values, z-scores, and match paths
- `stats/exports/finishing_post_control_unmatched.csv` for player-seasons missing one or more source metrics
- It uses `POST_TOUCHES` from `tracking_postup.csv`
- It uses `PTS_PER_POST_TOUCH` from `tracking_touches.csv`
- It uses the same `Post Up Frequency%` tracking proxy as the Post Fade build: `(POST_TOUCHES / TOUCHES) * 100`
- Its first `Post Up Draw Foul Rate` build uses a tracking proxy: `(POST_TOUCH_FOULS / POST_TOUCHES) * 100`

`build_finishing_draw_foul.py` writes:

- `stats/exports/finishing_draw_foul_sheet.csv` for direct sheet-style pasting
- `stats/exports/finishing_draw_foul_rating_only.csv` for final ratings only
- `stats/exports/finishing_draw_foul_audit.csv` for debugging raw values, z-scores, and match paths
- `stats/exports/finishing_draw_foul_unmatched.csv` for player-seasons missing one or more source metrics
- It uses `general_traditional.csv -> FTA` for the per-game free-throw lane
- It uses `general_traditional_per100.csv -> FTA` for the `FTA_per100` lane
- It uses `stats/history/bball_index_draw_foul.csv` for the foul-drawing support metrics
- Because the live export currently returns `Stable Shooting Fouls Drawn Per 75` as a flat zero column, the first build derives that lane as `Stable Fouls Drawn Per 75 - Stable Non Shooting Fouls Drawn Per 75`
- It uses the same `Post Up Draw Foul Rate` tracking proxy as the Post Control build: `(POST_TOUCH_FOULS / POST_TOUCHES) * 100`

`build_finishing_hands.py` writes:

- `stats/exports/finishing_hands_sheet.csv` for direct sheet-style pasting
- `stats/exports/finishing_hands_rating_only.csv` for final ratings only
- `stats/exports/finishing_hands_audit.csv` for debugging raw values, z-scores, and match paths
- `stats/exports/finishing_hands_unmatched.csv` for player-seasons missing one or more source metrics

`shot_locations_by_zone.csv` is the new wide NBA.com zone export.
It keeps columns such as `Restricted Area_FGM`, `In The Paint (Non-RA)_FGM`, `Mid-Range_FGM`, and `Corner 3_FGM`
instead of collapsing the response down to only the first zone.

`very_tight.csv` is the NBA.com `leaguedashplayerptshot` export for `0-2 Feet - Very Tight`.
It is the intended badge source for `FG3M_verytight` and `FG3_PCT_verytight`.

`shot_locations_by_distance_8ft.csv` is the wide NBA.com `8ft Range` shot-location export.
It is the intended badge-inspection source for long-distance buckets such as `24+ ft`.

`build_shooting_mid_range.py` writes:

- `stats/exports/shooting_mid_range_sheet.csv` for direct sheet-style pasting
- `stats/exports/shooting_mid_range_rating_only.csv` for final ratings only
- `stats/exports/shooting_mid_range_audit.csv` for debugging raw values, z-scores, and match paths
- `stats/exports/shooting_mid_range_unmatched.csv` for player-seasons missing one or more source metrics
- It uses the legacy `stats/history/shot_locations.csv` for historical seasons and overrides the newest season with `stats/history/shot_locations_by_zone.csv`
- It keeps the workbook's duplicated `Midrange Pull Up Shot Making Efficiency` slot so the first pass mirrors the sheet weighting exactly

`build_shooting_three_point.py` writes:

- `stats/exports/shooting_three_point_sheet.csv` for direct sheet-style pasting
- `stats/exports/shooting_three_point_rating_only.csv` for final ratings only
- `stats/exports/shooting_three_point_audit.csv` for debugging raw values, z-scores, and match paths
- `stats/exports/shooting_three_point_unmatched.csv` for player-seasons missing one or more source metrics
- It uses `general_traditional.csv` for `FG3A`, `FG3M`, and `FG3_PCT`
- It applies the `FG3A` low-volume gate: `0 attempts -> -3`, `0.2 or less -> z - 2.5`, then floors the adjusted z-score at `-3`

`build_shooting_free_throw.py` writes:

- `stats/exports/shooting_free_throw_sheet.csv` for direct sheet-style pasting
- `stats/exports/shooting_free_throw_rating_only.csv` for final ratings only
- `stats/exports/shooting_free_throw_audit.csv` for debugging raw values, z-scores, and match paths
- `stats/exports/shooting_free_throw_unmatched.csv` for player-seasons missing one or more source metrics
- It uses `general_traditional.csv` for `FT_PCT` and `FTM`
- It keeps the workbook weights exactly: `FT_PCT * 0.50 + Stable FT% * 0.45 + FTM * 0.05`

`build_shooting_shot_iq.py` writes:

- `stats/exports/shooting_shot_iq_sheet.csv` for direct sheet-style pasting
- `stats/exports/shooting_shot_iq_rating_only.csv` for final ratings only
- `stats/exports/shooting_shot_iq_audit.csv` for debugging raw values, z-scores, and match paths
- `stats/exports/shooting_shot_iq_unmatched.csv` for player-seasons missing one or more source metrics
- It uses `general_advanced.csv` for `EFG_PCT` and `TS_PCT`
- It uses `general_per1poss.csv -> PTS` as the `Points Per Possession` support slot
- It keeps the workbook weights exactly: `avg(BU:BY) * 0.85 + BZ * 0.10 + avg(CA:CD) * 0.50`
- It blanks `Points Over Expectation / 75` in any season where the live bball-index export is flat zero for every player, which currently affects `2025-26`
- It uses `stats/history/bball_index_hands.csv` for `Playmaking Talent` and `Touches Per 75`
- It uses `tracking_touches.csv` for `TOUCHES`

`build_finishing_all.py` orchestrates the current Finishing lanes:

- reruns Layup, Standing Dunk, Driving Dunk, Post Hook, Post Fade, Post Control, Draw Foul, and Hands with stable output prefixes
- merges their `*_rating_only.csv` outputs by `NBA_ID`, `Season`, and `Player`
- writes one combined file: `stats/exports/finishing_all_ratings.csv`

`build_shooting_all.py` orchestrates the current Shooting lanes:

- reruns Close Shot, Mid-Range Shot, 3-Point Shot, Free Throw, and Shot IQ with stable output prefixes
- merges their `*_rating_only.csv` outputs by `NBA_ID`, `Season`, and `Player`
- writes one combined file: `stats/exports/shooting_all_ratings.csv`

`build_playmaking_all.py` builds the current combined Playmaking tab:

- writes one workbook-style sheet export: `stats/exports/playmaking_all_sheet.csv`
- writes one ratings-only export: `stats/exports/playmaking_all_ratings.csv`
- writes one audit export: `stats/exports/playmaking_all_audit.csv`
- writes one unmatched export: `stats/exports/playmaking_all_unmatched.csv`
- keeps `stats/manual/playerlist.csv` as the identity universe
- enriches `RotationRole` from `stats/history/bball_index_playmaking.csv` when available
- keeps live `MIN` on the NBA.com side via `general_traditional.csv`
- `Speed with Ball` now consumes two rating-level z-scores:
- `Ball Handle` from the Playmaking `Ball Handle` aggregate z-score
- `Speed` from `stats/exports/physical_all_audit.csv -> Speed AggregateZ` when that file is available
- currently uses these first-pass proxies:
- `playtype_pnr_handler.csv -> POSS_PCT / PPP / POSS` for several PnR ball-handler slots
- `playtype_iso.csv -> POSS_PCT` plus `PPP * POSS_PCT` for the first isolation frequency / impact proxies
- `tracking_speed.csv -> AVG_SPEED` as the fallback `SPEED` proxy only when the Physical audit export is not present
- `tracking_drives.csv -> DRIVE_AST` for the first `Drive Assist Points Per 75` proxy

`build_impact_all.py` builds the current combined Impact tab:

- writes one workbook-style sheet export: `stats/exports/impact_all_sheet.csv`
- writes one ratings-only export: `stats/exports/impact_all_ratings.csv`
- writes one audit export: `stats/exports/impact_all_audit.csv`
- writes one unmatched export: `stats/exports/impact_all_unmatched.csv`
- keeps `stats/manual/playerlist.csv` as the identity universe
- uses this current source stack:
- `stats/manual/bballref_advanced.xlsx` for `WS`, `WS/48`, `BPM`, `VORP`, `OWS`, `DWS`, `OBPM`, and `DBPM`
- `C:/2k/epm.xlsx` as the baseline history for `EPM`, `O-EPM`, and `D-EPM`
- `stats/history/dunksandthrees_epm.csv` as the current-season overlay for `EPM`, `O-EPM`, and `D-EPM`
- `stats/history/mamba.csv` for `MAMBA`, `O-MAMBA`, and `D-MAMBA`
- `stats/history/bball_index_impact.csv` as the primary live source for the LEBRON-family metrics
- `stats/history/lebron.csv` as the fallback for core `LEBRON`, `O-LEBRON`, and `D-LEBRON` rows the live bball-index export does not cover
- `stats/scripts/pull_nbarapm_data.py` refreshes `nbarapm.csv`, `mamba.csv`, and `lebron.csv`
- `stats/scripts/pull_bball_index.py` now resolves the active main `Metric` dropdown and strips the live trailing `*` markers from metric names in the exported CSV
- `stats/scripts/pull_dunksandthrees_epm.py` refreshes the public current-season Dunks & Threes actual `EPM` table
- uses these first-pass proxies where the workbook wants on-court impact slots:
- `general_advanced.csv -> NET_RATING` for `Stable On-Court Net Rating`
- `general_advanced.csv -> OFF_RATING` for `Stable On-Court ORtg`
- inverse `general_advanced.csv -> DEF_RATING` for `INV_Stable On-Court DRtg`
- inverse `tracking_defensive_impact.csv -> DEF_RIM_FG_PCT` for `Defensive eFG% Impact`
- the core and extended workbook `LEBRON` family slots are now filled from `bball_index_impact.csv` where that export has coverage
- the remaining RAPTOR / RPM / RAPM / DPM / DRIP / SPI-family workbook slots are still explicitly unmatched for now
- reweights the section buckets across the groups that actually have data so a missing `Impact Luck` or RAPM family bucket does not collapse every row to `-1`

`build_defense_all.py` builds the current combined Defense tab:

- writes one workbook-style sheet export: `stats/exports/defense_all_sheet.csv`
- writes one ratings-only export: `stats/exports/defense_all_ratings.csv`
- writes one audit export: `stats/exports/defense_all_audit.csv`
- writes one unmatched export: `stats/exports/defense_all_unmatched.csv`
- keeps `stats/manual/playerlist.csv` as the identity universe
- enriches `RotationRole` from `stats/history/bball_index_defense.csv` when available
- keeps live `MIN` on the NBA.com side via `general_traditional.csv`
- uses this current source stack:
- `stats/history/bball_index_defense.csv` for the live defense metric catalog from the main bball-index `Metric` dropdown
- `stats/exports/impact_all_audit.csv -> Defensive Impact AggregateZ` as a direct pass-through z-score for the workbook's `DEFENSIVE IMPACT` slot
- `stats/history/nbarapm.csv` for `rim_points_saved`, `rim_points_saved_100`, `rimdfga/100`, `rim_dif%`, `rim_acc_onoff`, `rim_acc_on`, `Steals_100`, `Blocks_100`, `RecoveredBlocks_100`, `STOPS_100`, `FTOV_100`, and `rFTOV_100`
- `stats/history/general_defense.csv` for `STL`, `PCT_STL`, `BLK`, and `PCT_BLK`
- `stats/history/hustle.csv` for `DEFLECTIONS` and `CONTESTED_SHOTS_3PT`
- `stats/history/defense_dashboard_3.csv -> FG3A - FG3M` for `FG3_MISS`
- `stats/history/defense_dashboard_l6ft.csv -> FGA_LT_06 - FGM_LT_06` for `FG_MISS_LT_06`
- `stats/history/defense_dashboard_l6ft.csv -> NS_LT_06_PCT - LT_06_PCT` for `<6ft_FG_Diff%`
- derives inverse rim-suppression aliases for `INV_Stable Rim dFG% vs. Expected`, `INV_rim_dif%`, `INV_rim_acc_onoff`, and `INV_rim_acc_on`
- mirrors the workbook's special `Perimeter Defense` formula, with renormalization across the pieces that actually have data
- first-pass workbook fit against `stats/manual/2k26_defense.xlsx` currently lands at:
- `Interior Defense Score` MAE: `0.2601`
- `Perimeter Defense Score` MAE: `0.2328`
- `Steal Score` MAE: `0.1710`
- `Block Score` MAE: `0.1795`
- `Help Defense IQ Score` MAE: `0.2047`
- `Pass Perception Score` MAE: `0.2853`
- and rating MAE at:
- `Interior Defense Rating`: `5.7430`
- `Perimeter Defense Rating`: `4.0874`
- `Steal Rating`: `2.9898`
- `Block Rating`: `3.0486`
- `Help Defense IQ Rating`: `4.2269`
- `Pass Perception Rating`: `4.6694`

`build_rebounding_all.py` builds the current combined Rebounding tab:

- writes one workbook-style sheet export: `stats/exports/rebounding_all_sheet.csv`
- writes one ratings-only export: `stats/exports/rebounding_all_ratings.csv`
- writes one audit export: `stats/exports/rebounding_all_audit.csv`
- writes one unmatched export: `stats/exports/rebounding_all_unmatched.csv`
- keeps `stats/manual/playerlist.csv` as the identity universe
- enriches `RotationRole` from `stats/history/bball_index_rebounding.csv` when available
- keeps live `MIN` on the NBA.com side via `general_traditional.csv`
- uses this current source stack:
- `stats/history/bball_index_rebounding.csv` for the live rebounding talent, stable, conversion, and adjusted-success metrics
- `stats/history/tracking_rebound.csv` for `OREB`, `DREB`, `OREB_CONTEST`, `DREB_CONTEST`, `OREB_CHANCE_PCT_ADJ`, and `DREB_CHANCE_PCT_ADJ`
- `stats/history/general_traditional.csv` for the per-game rebound slots
- `stats/history/general_advanced.csv` for `OREB_PCT` and `DREB_PCT`
- `stats/history/hustle.csv` for `OFF_BOXOUTS`, `DEF_BOXOUTS`, `PCT_BOX_OUTS_OFF`, and `PCT_BOX_OUTS_DEF`
- `stats/history/nbarapm.csv` for `SelfORebPct`
- mirrors the workbook exactly where the defensive section repeats `Offensive Rebounding Chances Per 75 Possessions`
- reweights the section buckets across the groups that actually have data so older seasons without full bball-index, hustle, or nbarapm coverage do not collapse to `-1`
- first-pass workbook fit against `stats/manual/2k26_rebound.xlsx` currently lands at:
- `Offensive Rebound Score` MAE: `0.1446`
- `Defensive Rebound Score` MAE: `0.1441`
- and rating MAE at:
- `Offensive Rebound Rating`: `3.1470`
- `Defensive Rebound Rating`: `3.0420`

`build_physical_all.py` builds the current combined Physical tab:

- writes one workbook-style sheet export: `stats/exports/physical_all_sheet.csv`
- writes one ratings-only export: `stats/exports/physical_all_ratings.csv`
- writes one audit export: `stats/exports/physical_all_audit.csv`
- writes one unmatched export: `stats/exports/physical_all_unmatched.csv`
- keeps `stats/manual/playerlist.csv` as the identity universe
- enriches `RotationRole` from `stats/history/bball_index_physical.csv` when available
- keeps live `MIN` on the NBA.com side via `general_traditional.csv`
- uses this current source stack:
- `stats/history/bball_index_physical.csv` for transition creation, speed context, usage, screening, finishing, perimeter isolation, post defense, and defensive playmaking metrics
- `stats/history/general_traditional.csv` for `MIN` and `GP`
- `stats/history/general_scoring.csv` for `PCT_PTS_FB`
- `stats/history/general_advanced.csv -> USG_PCT` as the current `Usage Rate` fallback proxy when a row is missing from the live bball-index file
- `stats/history/bios.csv` for `PLAYER_HEIGHT_INCHES`, `PLAYER_WEIGHT`, and `AGE`
- `stats/history/tracking_speed.csv` for `AVG_SPEED`, `AVG_SPEED_OFF`, and the fallback `Avg Speed Offense` / `Avg Speed Defense` proxies
- `stats/history/playtype_transition.csv`, `stats/history/playtype_offscreen.csv`, and `stats/history/playtype_cut.csv` for possession counts and rates
- `stats/history/hustle.csv` for `BOX_OUTS`, `SCREEN_ASSISTS`, `LOOSE_BALLS_RECOVERED`, `CONTESTED_SHOTS`, and `DEFLECTIONS`
- `stats/history/draft.csv` for `THREE_QUARTER_SPRINT`, `LANE_AGILITY_TIME`, `MODIFIED_LANE_AGILITY_TIME`, and `VERTICAL_LEAP`
- `stats/history/overall_dunk_stats.csv` for `jumpSubscore_average`, `jumpSubscore_max`, and `jumpSubscore_total`
- `Upgrade/players.csv` for the current database `attributes_Speed`, `attributes_Agility`, `attributes_Strength`, `attributes_Vertical`, `attributes_Stamina`, and `attributes_Hustle`
- `stats/exports/finishing_post_control_audit.csv`, `stats/exports/finishing_driving_dunk_audit.csv`, and `stats/exports/finishing_standing_dunk_audit.csv` as the current direct score feeds for `Post Control`, `Driving Dunk`, and `Standing Dunk`
- `stats/manual/2k26_rebound.xlsx -> Offensive Rebound` and `stats/manual/2k26_defense.xlsx -> Block` as the current direct score feeds for those cross-category slots
- mirrors the workbook behavior where `Agility -> SPEED` reads the Physical `Speed` section score directly instead of the Speed aggregate z-score
- first-pass workbook fit against `stats/manual/2k26_physical.xlsx` currently lands at:
- `Speed Score` MAE: `0.1697`
- `Agility Score` MAE: `0.1921`
- `Strength Score` MAE: `0.2785`
- `Vertical Score` MAE: `0.3295`
- `Stamina Score` MAE: `0.3023`
- `Hustle Score` MAE: `0.2901`
- and rating MAE at:
- `Speed Rating`: `4.1650`
- `Agility Rating`: `4.7432`
- `Strength Rating`: `8.0745`
- `Vertical Rating`: `6.3611`
- `Stamina Rating`: `2.0210`
- `Hustle Rating`: `5.0847`

`build_tendency_all.py` builds the current combined Tendency tab:

- writes one workbook-style sheet export: `stats/exports/tendency_all_sheet.csv`
- writes one final-results export: `stats/exports/tendency_all_ratings.csv`
- writes one audit export: `stats/exports/tendency_all_audit.csv`
- writes one unmatched export: `stats/exports/tendency_all_unmatched.csv`
- keeps `stats/manual/playerlist.csv` as the identity universe
- currently uses this source stack:
- `stats/history/general_traditional_per100.csv` for `FGA/100`, `FG3A`, and `PF`
- `stats/history/general_advanced.csv` for `USG_PCT` and the possession totals used to turn shot-area totals into per-75 helpers
- `stats/history/tracking_drives.csv`, `stats/history/tracking_postup.csv`, and `stats/history/tracking_rebound.csv` for the live drive, post-touch, and offensive-rebound lanes
- `stats/history/hustle.csv` for `CHARGES_DRAWN` and `CONTESTED_SHOTS`
- `stats/history/general_defense.csv` for `PCT_BLK` and the `PCT_STL` fallback proxy behind older `Pickpocket Rating` gaps
- `stats/history/bball_index_close_shot.csv` for `Rim Shot Attempts Per 75 Possessions`
- `stats/history/shooting_splits.csv` for the standing-dunk, driving-dunk, and putback shot-type totals
- `shot_detail2.csv`, `stats/history/shot_locations.csv`, and `stats/history/shot_locations_by_zone.csv` for the close / restricted-area / mid-range shot-area helpers
- `stats/exports/defense_all_sheet.csv` for `Pass Perception` and the direct `Pickpocket Rating` lane
- `stats/exports/shooting_three_point_sheet.csv -> inverse Three-Point Shot` as the current `ROLL_VS._POP_TENDENCY` helper
- every tendency percentile lane now uses the same two-slope curve as `SHOT_TENDENCY`: the upper half stays as the raw percentile while the lower half is compressed into the `0.15` to `0.50` band
- `TAKE_CHARGE_TENDENCY` and `CONTEST_SHOT_TENDENCY` still keep the workbook-style `0.3333` fallback when the helper stat is blank
- older `CHARGES_DRAWN` and `CONTESTED_SHOTS` gaps before `2015-16` are still real because public hustle coverage starts there

`pull_sportradar_synergy.py` is the first Synergy API entry point:

- reads the API key from `SPORTRADAR_SYNERGY_API_KEY` or `SPORTRADAR_API_KEY`
- writes raw responses to `stats/tmp/`
- writes normalized CSVs to `stats/history/`
- currently supports:
  - `seasons`
  - `player-playtypes`
  - `player-events`

See `stats/manual/sportradar_synergy_playmaking_notes.md` for the initial playmaking-focused pull plan.

`pull_nba_dunk_sources.py` replaces the old repo-root `nba.py`, `concat.py`, and `dunk_leaderboard.py` flow for the Standing Dunk pipeline.
It now:

- refreshes current-season player shooting splits into `stats/tmp/shooting_splits_tmp.csv`
- merges them into `stats/history/shooting_splits.csv`
- refreshes the NBA Dunk Score leaderboard into `stats/tmp/dunks_leaderboard_tmp.csv`
- rebuilds both `stats/history/standing_dunk_stats.csv` and `stats/history/overall_dunk_stats.csv`

The builder defaults now read the structured history files in `stats/history/` instead of the repo-root CSVs.

`pull_bballref_positions.py` pulls the Basketball Reference play-by-play position-estimate table for every season in `stats/manual/playerlist.csv`:

- writes `stats/history/bballref_position_estimate.csv`
- reads `https://www.basketball-reference.com/leagues/NBA_<endyear>_play-by-play.html`
- keeps the raw position mix columns `PG`, `SG`, `SF`, `PF`, and `C`
- derives `Primary_Position` from the highest position share
- derives `Second_Position` from the next-highest non-zero share

`build_rating_all.py` builds the flattened final ratings + badges sheet that matches `stats/manual/2k26_rating.xlsx`:

- writes `stats/exports/rating_all_sheet.csv`
- writes `stats/exports/rating_all_sheet.xlsx`
- writes `stats/exports/rating_all_audit.csv`
- writes `stats/exports/rating_all_unmatched.csv`
- keeps `stats/manual/playerlist.csv` as the identity universe
- fills `Team(s)` from this fallback chain:
- workbook sample in `stats/manual/2k26_rating.xlsx`
- live/history `bball-index` `Team(s)` values when available
- `general_traditional.csv -> TEAM_ABBREVIATION`
- Basketball Reference position table `Team`
- `impact_all_ratings.csv -> Team(s)` as the last fallback
- fills `Primary_Position` and `Second_Position` from `stats/history/bballref_position_estimate.csv`, with the uploaded workbook as the final fallback for the few rows that already had a manual position

Current known source limits:

- the live bball-index Shiny leaderboard currently exposes `2015-16` through `2025-26`, so older seasons need a backfill source for those metrics
- the live main `Metric` dropdown only materializes after the control is activated, so `pull_bball_index.py` now explicitly opens that control before it resolves the catalog
- the `less_than_5ft_*` inputs are still using the current placeholder NBA.com mapping and should be rechecked against the older sheet logic before treating Layup as final
- `Putback Frequency%` in the badge build now uses `nbarapm.csv -> SelfORebPct * TeammateMissORebPerc / 100`, but the Standing Dunk sheet still leaves `Putback Frequency%` and `Putback per Offensive Rebound` blank until that category gets its own final source logic
- the first automated Playmaking pass is already close to the uploaded workbook, but several slots still rely on first-pass proxies rather than exact source recreation
- `Speed with Ball -> SPEED` now reads from `physical_all_audit.csv -> Speed AggregateZ` when that export exists, but it still falls back to `tracking_speed.csv -> AVG_SPEED` if the Physical audit has not been built yet
- the current automated Impact pass now fills the workbook's core and extended `LEBRON` family slots, plus current-season `EPM` from Dunks & Threes, but the RAPTOR / RPM / RAPM / DPM / DRIP / SPI families are still missing and show up in `impact_all_unmatched.csv`
- the live bball-index defense export currently covers `2015-16` through `2025-26`, so older `Defense` seasons still depend on the public local sources and show larger unmatched gaps in `defense_all_unmatched.csv`
- the live bball-index rebounding export currently covers `2015-16` through `2025-26`, while `hustle.csv` boxout coverage also starts at `2015-16` and `nbarapm.csv -> SelfORebPct` currently starts at `2021-22`, so older `Rebounding` seasons still show real source gaps in `rebounding_all_unmatched.csv`
- the live bball-index physical export currently covers `2015-16` through `2025-26`, so older `Physical` seasons still lean on current-database attributes, NBA.com history, combine data, and the cross-category score feeds and therefore show real gaps in `physical_all_unmatched.csv`
- `rating_all_unmatched.csv` is now down to the rows where Basketball Reference does not expose a position-estimate row for that player-season and there is no trusted team fallback in the uploaded workbook or history sources

## Naming Note

`stats/` is still the current top-level name so we do not break the rest of the repo all at once.
If this structure feels right after one update cycle, the next safe rename would be:

`stats/` -> `ratings/`

That rename should wait until the Google Sheets mapping is documented and stable.
