# Scripts Map

This folder keeps the active ratings automation entrypoints together.

## Main Pullers

- `pull_nba_stats.py`
  - Pulls NBA.com stats into `stats/tmp/` and can merge them into `stats/history/`.
- `pull_bball_index.py`
  - Pulls bball-index metric tables into `stats/history/`.
- `pull_nbarapm_data.py`
  - Pulls the public `nbarapm.com` JSON datasets into `stats/history/` as `nbarapm.csv`, `mamba.csv`, and `lebron.csv`.
- `pull_dunksandthrees_epm.py`
  - Pulls the public current-season Dunks & Threes actual `EPM` table into `stats/history/dunksandthrees_epm.csv`.
- `pull_nba_dunk_sources.py`
  - Builds the dunk-related historical sources used by the finishing pipelines.
- `pull_sportradar_synergy.py`
  - Pulls Sportradar Synergy data into `stats/tmp/synergy/` and `stats/history/synergy/`.

## Main Builders

- `build_cal_lane.py`
  - Recreates one `DataPull -> Cal` lane outside Google Sheets.
- `build_finishing_*.py`
  - Builds individual finishing sub-ratings.
- `build_finishing_all.py`
  - Rebuilds and merges all current finishing sub-ratings.
- `build_defense_all.py`
  - Rebuilds the current combined defense tab and writes sheet / ratings / audit outputs.
- `build_impact_all.py`
  - Rebuilds the current combined impact tab and writes sheet / ratings / audit outputs.
- `build_playmaking_all.py`
  - Rebuilds the current combined playmaking tab and writes sheet / ratings / audit outputs.
- `build_physical_all.py`
  - Rebuilds the current combined physical tab and writes sheet / ratings / audit outputs.
- `build_rebounding_all.py`
  - Rebuilds the current combined rebounding tab and writes sheet / ratings / audit outputs.
- `build_shooting_*.py`
  - Builds individual shooting sub-ratings.
- `build_shooting_all.py`
  - Rebuilds and merges all current shooting sub-ratings.

## Export Helper

- `export_sheet_slice.py`
  - Creates paste-ready CSV slices from historical sources.

## Source-Specific Wrappers And Older Helpers

- `read_nbacom.py`
  - Older pasted-response helper kept for reference.
- `lebron.py`
  - Thin wrapper around `pull_nbarapm_data.py` for the `lebron` dataset only.
- `mamba.py`
  - Thin wrapper around `pull_nbarapm_data.py` for the `mamba` dataset only.
- `nbarapm.py`
  - Thin wrapper around `pull_nbarapm_data.py` for the `player_stats_export` dataset only.

Treat the pullers and builders above as the current active pipeline surface.
