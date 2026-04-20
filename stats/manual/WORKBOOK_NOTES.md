# Workbook Notes

Workbook inspected:

- `2k26_Temp_for_codex.xlsx`

## Visible Sheets

- `「Rating」的副本`
- `「Finishing」的副本`
- `Cal`
- `DataPull`

## Current Understanding

This reduced workbook is useful as a specification for the calculation flow, but it is not a perfect execution copy.
Some formulas still contain `#REF!` because omitted helper tabs are missing.

That means we should treat this workbook as the design reference for automation, not as the long-term calculation engine.

## DataPull Role

`DataPull` is acting like a stat import and matching lane.

- Identity columns:
  - `A` NBA ID
  - `B` Season
  - `C` Player
- `D` is the matched stat value that `Cal` reads.
- The sheet also contains helper lookup and name-normalization logic to resolve player-name mismatches.
- In the current sample slice, the imported stat header shown is `GP`.

## Cal Outputs

Per user clarification, `Cal` keeps five useful outputs for a stat lane:

- `F`
  - raw matched datapulled value
- `G`
  - non-normalized setting
  - currently the raw z-score style output
- `H`
  - normalized setting
  - this is the minutes and role adjusted output
- `I`
  - inverse of `G`
- `J`
  - inverse of `H`

So the automation target is not just "the z-score".
It is the full set:

- raw matched value
- non-normalized value
- normalized value
- inverse non-normalized value
- inverse normalized value

## Finishing Tab Pattern

`Finishing` appears to consume multiple component outputs, combine them into a weighted aggregate, then:

- convert the aggregate into a z-score-like value
- map that value into a final rating using percentile bands

This suggests the best automation order is:

1. automate one `DataPull -> Cal` lane correctly
2. validate all five outputs
3. feed those outputs into one downstream rating tab

## Recommended First Build Target

Use one stat lane first as a pipeline test.

Good options:

- `GP` if we want to mirror the workbook sample exactly
- `FT%` if we want to start with a real rating metric that is simpler than finishing
