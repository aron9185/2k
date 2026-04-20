# Google Sheet Mapping Template

Fill this in for the tabs you want automated first. `Datapull` is the best place to start.

## Workbook Input

- Latest sheet export file name:
- Export date:
- Season being updated:

## Tab Mapping

Use one block per target tab.

### Target Tab

- Tab name:
- Purpose:
- Priority:

### Source

- Source file or job name:
- Folder:
  - `stats/history`
  - `stats/tmp`
  - `stats/manual`
- Value mode:
  - `per_game`
  - `totals`
  - `per_100_possessions`
  - `per_possession`
  - `draft_combine`
- Season filter needed:

### Matching Keys

- Primary player key:
  - Example: `PLAYER_NAME`
  - Example: `PLAYER_ID`
- Extra keys if needed:
  - Example: `Season`
  - Example: `TEAM_ID`
- Known naming cleanup rules:

### Column Mapping

Write rows like this:

`source column -> destination column -> notes`

Examples:

- `Season -> A -> keep as text`
- `PLAYER_NAME -> B -> exact match needed`
- `FGM -> H -> per-game value`

### Output Rules

- Replace whole tab or append:
- Sort order:
- Any formulas already in the sheet that must stay untouched:
- Any columns that are manual only:

## Questions To Answer First

- Which tab should we automate first:
- Is that tab filled by paste only, or should we eventually write directly into Google Sheets:
- Do you want the first automation target to be:
  - `Datapull`
  - `Cal`
  - one rating tab such as `Finishing` or `Shooting`
