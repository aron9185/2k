# Real Sports Rank Files

`rank/` stores the smoothed ranking workbooks used by the Real Sports scripts in this folder's parent directory.

## Inputs and outputs

- `rank.txt`
  - raw ranking JSON from Real Sports
  - read by `..\rank.py`
- `*.xlsx`
  - rolling ranking workbooks keyed by prop type
  - read and updated by `..\rank.py`
  - read by `..\picks.py`

## Naming pattern

- `14pt.xlsx`, `5reb.xlsx`, `7ast.xlsx`, `1_3.xlsx`
  - baseline ranking history by prop
- `game_14pt.xlsx`, `game_5reb.xlsx`, `game_3ast.xlsx`, `game_ffg.xlsx`
  - game-specific ranking history by prop

Typical suffix meaning:

- `pt` = points
- `reb` = rebounds
- `ast` = assists
- `_3` = three-pointers

## Workbook columns

Each workbook stores:

- `displayName`
- `rank`
- `votes`
- `entries`
- `rankMomentum`
- `votesMomentum`

`rank.py` updates these using a smoothing blend:

- new rank/value weight: `0.3`
- previous workbook weight: `0.7`

## Practical workflow

1. Save the latest Real Sports ranking payload into `rank.txt`.
2. Run `rank.py` with the desired workbook target configured in the script.
3. Run `picks.py` against the matching workbook and `picks.txt`.
4. Review `picks_ev.txt`.

The files in this folder are active utility data, not archive leftovers.
