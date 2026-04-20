# 2k Workspace

This repo now has four active work areas:

- `rotation/`: the live NBA rotation web app
- `stats/`: the 2K ratings and badges build pipeline
- `Upgrade/`: Immerse / preset export tooling
- `real/`: the Real Sports utilities, rank workbooks, caches, and outputs

## Real Sports

The Real Sports workflow no longer lives in the repo root. Use the files under [`real/`](/c:/2k/real/README.md).

Typical commands now look like:

```powershell
python real\lineup.py --sport nba --date 2026-04-20 --season 2025
python real\read_real_player.py --sport nba --season 2026
python real\bootstrap_realsports_session.py
```

See [`real/README.md`](/c:/2k/real/README.md) for the Real Sports auth flow, active files, and workbook details.

Latest full-project handoff:

- [`HANDOFF_2026-04-21.md`](/c:/2k/HANDOFF_2026-04-21.md)

These root files are intentionally still kept because newer `stats/` builders reference them:

- `HANDOFF_2026-04-16.md`
- `HANDOFF_2026-04-20.md`
- `HANDOFF_2026-04-21.md`

## Archived Legacy Files

Older root-level 2K prototype scripts and one-off generated files were moved to:

- `archive/root_legacy_2k/scripts/`
- `archive/root_legacy_2k/data/`

## Rotation Refresh

If you need to refresh the rotation position file, use:

```powershell
python rotation\update_sportsref_download.py --season 2025-26
```
