# bball-index / Shiny Notes

Source app:

- `https://bball-index.shinyapps.io/SoulYOLO3Lose/`

## What We Confirmed

- The app is reachable from this environment.
- `Rotation Role`, `Advanced Position`, `Offensive Role`, and `Defensive Role` are present as Shiny inputs in the page HTML.
- The actual player/profile data is not embedded in the initial HTML response.
- That means the real values are rendered dynamically after the Shiny session starts and inputs are applied.
- The main `Metric` selectize control does expose the core and extended `LEBRON` family after the control is activated.
- That same live main `Metric` control also exposes the current Defense catalog used by `build_defense_all.py`,
  including rim, perimeter, steal, block, and coverage-versatility metrics.
- That same live main `Metric` control also exposes the current Rebounding catalog used by `build_rebounding_all.py`,
  including offensive and defensive rebounding talent, stable rebound rates, conversion skill, and adjusted-success metrics.
- The live dropdown values currently include a trailing `*`, so `pull_bball_index.py` strips that UI suffix when it writes
  the catalog or exported CSV headers.
- The live bball-index LEBRON family is now the primary source for those Impact slots, with `nbarapm.com/load/lebron`
  only acting as a fallback for older or uncovered rows.

## Useful Shiny IDs Found

Player profile / role outputs:

- `rotationRoleUI`
- `archetypeUI`
- `defRoleUI`
- `adPosUI`
- `seasonProfUI`
- `prof_table`

Comparison group inputs and output:

- `RotationRoles_mult`
- `advanced_position_mult`
- `offensive_archetype_mult`
- `defensive_role_mult`
- `minutes_min`
- `make_player_card_profile2`
- `gt_pcp`

## Recommended Near-Term Workflow

Until we build a true Shiny-session client, the most robust path is:

1. Export the needed bball-index data from the site.
2. Save that export as a CSV in `stats/manual/`.
3. Feed it into `build_cal_lane.py` with `--details-csv`.

Suggested columns for a first export:

- `Season`
- `Player`
- `NBA_ID` if available
- `RotationRole`
- `MIN` if available
- any future role or archetype columns we want to keep

## Why This Matters

`playerlist.csv` is now the source of truth for which database players exist.
The bball-index export should act as a details/backfill source for:

- `RotationRole`
- `MIN` if needed
- future role-based columns

That keeps identity matching stable while allowing us to swap in cleaner role data later.
