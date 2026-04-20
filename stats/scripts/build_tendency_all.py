from __future__ import annotations

import argparse
import csv
import statistics
from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from build_cal_lane import (
    CalUniverseRow,
    build_source_index,
    canonical_id,
    enrich_universe_rows,
    load_cal_universe,
    load_universe_csv,
    match_metric_row,
    normalize_name,
    parse_float,
    read_history_csv,
    resolve_details_csv_path,
    write_csv,
)
from build_finishing_standing_dunk import write_matrix_csv
from build_shooting_mid_range import read_legacy_shot_location_rows
from pull_nba_stats import EXPORT_DIR, HISTORY_DIR, MANUAL_DIR, ROOT


PROJECT_ROOT = ROOT.parent
BLANK_TENDENCY_FALLBACK = 0.3333

SHEET_LAYOUT: Sequence[Tuple[str, str]] = (
    ("A", "NBA ID"),
    ("B", "Season"),
    ("C", "Player"),
    ("D", "PLAYER_LAST_NAME"),
    ("E", "PLAYER_FIRST_NAME"),
    ("F", "SHOT_TENDENCY"),
    ("G", "FGA/100"),
    ("H", "TOUCHES_TENDENCY"),
    ("I", "USG_PCT"),
    ("J", "SHOT_CLOSE_TENDENCY"),
    ("K", ""),
    ("L", "SHOT_UNDER_BASKET_TENDENCY"),
    ("M", ""),
    ("N", "Rim Shot Attempts Per 75 Possessions"),
    ("O", "Shot Area_Restricted Area_FGA"),
    ("P", "SHOT_MID-RANGE_TENDENCY"),
    ("Q", "Shot Area_Mid-Range_FGA"),
    ("R", "SHOT_THREE_TENDENCY"),
    ("S", "FG3A"),
    ("T", "DRIVING_LAYUP_TENDENCY"),
    ("U", "DRIVE_FGA"),
    ("V", "STANDING_DUNK_TENDENCY"),
    ("W", "STANDING_DUNK_FGA"),
    ("X", "DRIVING_DUNK_TENDENCY"),
    ("Y", "Driving Dunk Shot_FGM"),
    ("Z", "PUTBACK_TENDENCY"),
    ("AA", "Putback per OReb%"),
    ("AB", "CRASH_TENDENCY"),
    ("AC", "OREB"),
    ("AD", "DRIVE_TENDENCY"),
    ("AE", "DRIVES"),
    ("AF", "POST_UP_TENDENCY"),
    ("AG", "POST TOUCHES"),
    ("AH", "SHOOT_FROM_POST_TENDENCY"),
    ("AI", "POST_TOUCH_FGA"),
    ("AJ", "ROLL_VS._POP_TENDENCY"),
    ("AK", "3PT"),
    ("AL", "PASS_INTERCEPTION_TENDENCY"),
    ("AM", "Pass Perception"),
    ("AN", "TAKE_CHARGE_TENDENCY"),
    ("AO", "CHARGES_DRAWN"),
    ("AP", "ON-BALL_STEAL_TENDENCY"),
    ("AQ", "Pickpocket Rating"),
    ("AR", "CONTEST_SHOT_TENDENCY"),
    ("AS", "CONTESTED_SHOTS"),
    ("AT", "BLOCK_SHOT_TENDENCY"),
    ("AU", "PCT_BLK"),
    ("AV", "FOUL_TENDENCY"),
    ("AW", "PF"),
)

TENDENCY_OUTPUTS: Sequence[Tuple[str, str, Optional[float]]] = (
    ("F", "G", None),
    ("H", "I", None),
    ("J", "K", None),
    ("L", "M", None),
    ("P", "Q", None),
    ("R", "S", None),
    ("T", "U", None),
    ("V", "W", None),
    ("X", "Y", None),
    ("Z", "AA", None),
    ("AB", "AC", None),
    ("AD", "AE", None),
    ("AF", "AG", None),
    ("AH", "AI", None),
    ("AJ", "AK", None),
    ("AL", "AM", BLANK_TENDENCY_FALLBACK),
    ("AN", "AO", BLANK_TENDENCY_FALLBACK),
    ("AP", "AQ", None),
    ("AR", "AS", BLANK_TENDENCY_FALLBACK),
    ("AT", "AU", None),
    ("AV", "AW", None),
)
TENDENCY_RESULT_KEYS = [output_key for output_key, _, _ in TENDENCY_OUTPUTS]

RAW_METRIC_KEYS: Sequence[str] = (
    "G",
    "I",
    "K",
    "N",
    "O",
    "Q",
    "S",
    "U",
    "W",
    "Y",
    "AA",
    "AC",
    "AE",
    "AG",
    "AI",
    "AK",
    "AM",
    "AO",
    "AQ",
    "AS",
    "AU",
    "AW",
)

SEASON_Z_METRICS = {"K", "N", "O", "Q"}
DIRECT_METRICS = {"AK", "AM", "AQ"}
AUDIT_LABELS = {
    "K": "SHOT_CLOSE_HELPER",
    "M": "SHOT_UNDER_BASKET_HELPER",
}


@dataclass
class SourceTable:
    rows: List[Dict[str, str]]
    by_id: Dict[Tuple[str, str], Dict[str, str]]
    by_name: Dict[Tuple[str, str], Dict[str, str]]


@dataclass
class MetricBundle:
    key: str
    header: str
    raw_values: List[Optional[float]]
    helper_values: List[Optional[float]]
    matched_by: List[str]
    source_note: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build the workbook-shaped tendency export from the current history and ratings sources."
    )
    parser.add_argument(
        "--universe-csv",
        default=str(MANUAL_DIR / "playerlist.csv"),
        help="Season/player universe CSV. Defaults to stats/manual/playerlist.csv.",
    )
    parser.add_argument(
        "--workbook",
        default=str(MANUAL_DIR / "2k26_tendency.xlsx"),
        help="Workbook used only to mirror the tendency sheet layout when helpful.",
    )
    parser.add_argument(
        "--sheet",
        default="",
        help="Optional workbook sheet name. Defaults to the first sheet in the workbook.",
    )
    parser.add_argument(
        "--details-csv",
        default="",
        help="Optional detail CSV used only to backfill IDs when the universe file lacks them.",
    )
    parser.add_argument(
        "--allow-id-fallback",
        action="store_true",
        help="Allow season+NBA_ID fallback when normalized season+player matching fails.",
    )
    parser.add_argument(
        "--output-prefix",
        default="tendency_all",
        help="Prefix used for CSV outputs inside stats/exports.",
    )
    return parser.parse_args()


def build_source_table(rows: Sequence[Dict[str, str]]) -> SourceTable:
    standardized_rows = list(rows)
    by_id, by_name = build_source_index(standardized_rows)
    return SourceTable(rows=standardized_rows, by_id=by_id, by_name=by_name)


def standardize_rows_generic(
    rows: Sequence[Dict[str, str]],
    season_column: str,
    player_column: str,
    id_column: str = "",
) -> List[Dict[str, str]]:
    standardized: List[Dict[str, str]] = []
    for row in rows:
        season = str(row.get(season_column, "")).strip()
        player = str(row.get(player_column, "")).strip()
        if not season or not player:
            continue
        merged = dict(row)
        merged["Season"] = season
        merged["PLAYER_NAME"] = player
        merged["PLAYER_ID"] = canonical_id(row.get(id_column, "")) if id_column else ""
        standardized.append(merged)
    return standardized


def standardize_shot_detail_rows(rows: Sequence[Dict[str, str]]) -> List[Dict[str, str]]:
    standardized: List[Dict[str, str]] = []
    for row in rows:
        season = str(row.get("season", "")).strip()
        player = str(row.get("name", "")).strip()
        if not season or not player:
            continue
        merged = dict(row)
        merged["Season"] = season
        merged["PLAYER_NAME"] = player
        merged["PLAYER_ID"] = ""
        standardized.append(merged)
    return standardized


def split_first_last_name(name: str) -> Tuple[str, str]:
    tokens = [token for token in str(name or "").strip().split() if token]
    if not tokens:
        return "", ""
    if len(tokens) == 1:
        return "", tokens[0]

    suffixes = {"jr", "jr.", "sr", "sr.", "ii", "iii", "iv", "v"}
    if tokens[-1].lower() in suffixes and len(tokens) >= 3:
        last_name = f"{tokens[-2]} {tokens[-1]}"
        first_name = " ".join(tokens[:-2])
        return last_name, first_name
    return tokens[-1], " ".join(tokens[:-1])


def average_numeric(values: Iterable[Optional[float]]) -> Optional[float]:
    numeric = [value for value in values if value is not None]
    if not numeric:
        return None
    return statistics.mean(numeric)


def combine_match_notes(*parts: Tuple[str, str]) -> str:
    notes = [f"{label}:{value}" for label, value in parts if value]
    return ";".join(notes)


def tendency_curve(percentile_value: Optional[float]) -> Optional[float]:
    if percentile_value is None:
        return None
    if percentile_value <= 0.5:
        return 0.15 + (percentile_value / 0.5) * 0.35
    return 0.5 + ((percentile_value - 0.5) / 0.5) * 0.5


def z_score(values: Sequence[Optional[float]]) -> List[Optional[float]]:
    numeric = [value for value in values if value is not None]
    if len(numeric) < 2:
        return [0.0 if value is not None else None for value in values]

    mean_value = statistics.mean(numeric)
    stdev_value = statistics.stdev(numeric)
    if stdev_value == 0:
        return [0.0 if value is not None else None for value in values]
    return [None if value is None else (value - mean_value) / stdev_value for value in values]


def season_z_scores(
    universe: Sequence[CalUniverseRow],
    values: Sequence[Optional[float]],
) -> List[Optional[float]]:
    grouped: Dict[str, List[float]] = {}
    for universe_row, value in zip(universe, values):
        if value is None:
            continue
        grouped.setdefault(universe_row.season, []).append(value)

    stats_by_season: Dict[str, Tuple[float, float]] = {}
    for season, season_values in grouped.items():
        if len(season_values) < 2:
            stats_by_season[season] = (season_values[0], 0.0)
            continue
        stats_by_season[season] = (
            statistics.mean(season_values),
            statistics.stdev(season_values),
        )

    z_values: List[Optional[float]] = []
    for universe_row, value in zip(universe, values):
        if value is None:
            z_values.append(None)
            continue
        mean_value, stdev_value = stats_by_season.get(universe_row.season, (value, 0.0))
        z_values.append(0.0 if stdev_value == 0 else (value - mean_value) / stdev_value)
    return z_values


def match_table_row(
    universe_row: CalUniverseRow,
    table: SourceTable,
    allow_id_fallback: bool,
) -> Tuple[Optional[Dict[str, str]], str]:
    return match_metric_row(
        universe_row,
        table.by_id,
        table.by_name,
        allow_id_fallback=allow_id_fallback,
    )


def nonzero_season_coverage(
    rows: Sequence[Dict[str, str]],
    column: str,
) -> set[str]:
    seasons: set[str] = set()
    for row in rows:
        season = str(row.get("Season", "")).strip()
        value = parse_float(row.get(column, ""))
        if season and value is not None and value > 0:
            seasons.add(season)
    return seasons


def standardize_shot_area_rows(rows: Sequence[Dict[str, str]]) -> List[Dict[str, str]]:
    standardized = standardize_rows_generic(rows, "Season", "PLAYER_NAME", "PLAYER_ID")
    for row in standardized:
        row["Shot Area_Restricted Area_FGA"] = row.get("Restricted Area_FGA", "")
        row["Shot Area_In The Paint (Non-RA)_FGA"] = row.get("In The Paint (Non-RA)_FGA", "")
        row["Shot Area_Mid-Range_FGA"] = row.get("Mid-Range_FGA", "")
    return standardized


def merge_shot_area_rows(
    primary_rows: Sequence[Dict[str, str]],
    fallback_rows: Sequence[Dict[str, str]],
) -> List[Dict[str, str]]:
    merged: Dict[Tuple[str, str], Dict[str, str]] = {}

    for row in fallback_rows:
        season = str(row.get("Season", "")).strip()
        player = normalize_name(row.get("PLAYER_NAME", ""))
        if season and player:
            merged[(season, player)] = dict(row)

    for row in primary_rows:
        season = str(row.get("Season", "")).strip()
        player = normalize_name(row.get("PLAYER_NAME", ""))
        if season and player:
            merged[(season, player)] = dict(row)

    return list(merged.values())


def build_shot_area_rate_rows(
    shot_area_rows: Sequence[Dict[str, str]],
    advanced_rows: Sequence[Dict[str, str]],
) -> List[Dict[str, str]]:
    advanced_table = build_source_table(advanced_rows)
    output_rows: List[Dict[str, str]] = []

    for row in shot_area_rows:
        season = str(row.get("Season", "")).strip()
        player = str(row.get("PLAYER_NAME", "")).strip()
        if not season or not player:
            continue

        advanced_row, _ = match_metric_row(
            CalUniverseRow(
                nba_id=canonical_id(row.get("PLAYER_ID", "")),
                season=season,
                player=player,
                rotation_role="",
                minutes=None,
            ),
            advanced_table.by_id,
            advanced_table.by_name,
            allow_id_fallback=True,
        )
        poss = None if advanced_row is None else parse_float(advanced_row.get("POSS", ""))
        if poss in (None, 0):
            continue

        restricted_fga = parse_float(row.get("Shot Area_Restricted Area_FGA", ""))
        paint_non_ra_fga = parse_float(row.get("Shot Area_In The Paint (Non-RA)_FGA", ""))
        mid_range_fga = parse_float(row.get("Shot Area_Mid-Range_FGA", ""))

        output_rows.append(
            {
                "Season": season,
                "PLAYER_ID": canonical_id(row.get("PLAYER_ID", "")),
                "PLAYER_NAME": player,
                "RA_FGA_PER75": (0.0 if restricted_fga is None else restricted_fga) / poss * 75.0,
                "PAINT_NON_RA_FGA_PER75": (0.0 if paint_non_ra_fga is None else paint_non_ra_fga) / poss * 75.0,
                "MID_RANGE_FGA_PER75": (0.0 if mid_range_fga is None else mid_range_fga) / poss * 75.0,
            }
        )

    return output_rows


def build_shot_type_rows(rows: Sequence[Dict[str, str]]) -> List[Dict[str, str]]:
    if not rows:
        return []

    fieldnames = list(rows[0].keys())
    standing_dunk_fga_columns = [
        column
        for column in fieldnames
        if column.endswith("_FGA")
        and "Dunk" in column
        and "Driving" not in column
        and "Running" not in column
    ]
    putback_fga_columns = [
        column for column in fieldnames if "Putback" in column and column.endswith("_FGA")
    ]

    output_rows: List[Dict[str, str]] = []
    for row in rows:
        season = str(row.get("Season", "")).strip()
        player = str(row.get("Name", "")).strip()
        if not season or not player:
            continue

        standing_dunk_fga = sum(
            parse_float(row.get(column, "")) or 0.0
            for column in standing_dunk_fga_columns
        )
        putback_fga = sum(
            parse_float(row.get(column, "")) or 0.0
            for column in putback_fga_columns
        )
        driving_dunk_fgm = parse_float(row.get("Driving Dunk Shot_FGM", "")) or 0.0

        output_rows.append(
            {
                "Season": season,
                "PLAYER_ID": canonical_id(row.get("PlayerID", "")),
                "PLAYER_NAME": player,
                "Standing_Dunk_FGA": standing_dunk_fga,
                "Driving Dunk Shot_FGM": driving_dunk_fgm,
                "Putback_FGA": putback_fga,
            }
        )

    return output_rows


def resolve_numeric_column(
    universe: Sequence[CalUniverseRow],
    table: SourceTable,
    column: str,
    allow_id_fallback: bool,
) -> Tuple[List[Optional[float]], List[str]]:
    values: List[Optional[float]] = []
    matched_by_values: List[str] = []

    for universe_row in universe:
        source_row, matched_by = match_table_row(universe_row, table, allow_id_fallback)
        values.append(None if source_row is None else parse_float(source_row.get(column, "")))
        matched_by_values.append(matched_by)

    return values, matched_by_values


def resolve_direct_metric_bundle(
    universe: Sequence[CalUniverseRow],
    key: str,
    header: str,
    values: Sequence[Optional[float]],
    matched_by_values: Sequence[str],
    source_note: str,
) -> MetricBundle:
    return MetricBundle(
        key=key,
        header=header,
        raw_values=list(values),
        helper_values=list(values),
        matched_by=list(matched_by_values),
        source_note=source_note,
    )


def resolve_z_metric_bundle(
    universe: Sequence[CalUniverseRow],
    key: str,
    header: str,
    raw_values: Sequence[Optional[float]],
    matched_by_values: Sequence[str],
    source_note: str,
) -> MetricBundle:
    if key in SEASON_Z_METRICS:
        helper_values = season_z_scores(universe, raw_values)
    else:
        helper_values = z_score(raw_values)

    return MetricBundle(
        key=key,
        header=header,
        raw_values=list(raw_values),
        helper_values=helper_values,
        matched_by=list(matched_by_values),
        source_note=source_note,
    )


def build_sheet_headers() -> List[str]:
    return [header for _, header in SHEET_LAYOUT]


def build_tendency_values(
    helper_values: Sequence[Optional[float]],
    blank_fallback: Optional[float],
) -> List[Optional[float]]:
    ordered = sorted(value for value in helper_values if value is not None)
    if not ordered:
        return [blank_fallback for _ in helper_values]

    if len(ordered) == 1:
        percentile_values = [None if value is None else 1.0 for value in helper_values]
    else:
        rank_by_value: Dict[float, float] = {}
        for value in ordered:
            if value in rank_by_value:
                continue
            lower_index = bisect_left(ordered, value)
            upper_index = bisect_right(ordered, value) - 1
            rank_by_value[value] = statistics.mean(
                index / (len(ordered) - 1)
                for index in range(lower_index, upper_index + 1)
            )
        percentile_values = [None if value is None else rank_by_value[value] for value in helper_values]

    output: List[Optional[float]] = []
    for percentile_value in percentile_values:
        if percentile_value is None:
            output.append(blank_fallback)
            continue
        output.append(tendency_curve(percentile_value))
    return output


def main() -> None:
    args = parse_args()

    universe_path = Path(args.universe_csv)
    workbook_path = Path(args.workbook)
    details_path = resolve_details_csv_path(args.details_csv, universe_path)

    required_paths = [
        HISTORY_DIR / "general_traditional_per100.csv",
        HISTORY_DIR / "general_advanced.csv",
        HISTORY_DIR / "tracking_drives.csv",
        HISTORY_DIR / "tracking_rebound.csv",
        HISTORY_DIR / "tracking_postup.csv",
        HISTORY_DIR / "hustle.csv",
        HISTORY_DIR / "general_defense.csv",
        HISTORY_DIR / "bball_index_close_shot.csv",
        HISTORY_DIR / "shooting_splits.csv",
        HISTORY_DIR / "shot_locations.csv",
        HISTORY_DIR / "shot_locations_by_zone.csv",
        EXPORT_DIR / "defense_all_sheet.csv",
        EXPORT_DIR / "shooting_three_point_sheet.csv",
        PROJECT_ROOT / "shot_detail2.csv",
    ]
    missing_paths = [str(path) for path in required_paths if not path.exists()]
    if missing_paths:
        raise SystemExit("Missing required source files:\n- " + "\n- ".join(missing_paths))

    workbook_universe: List[CalUniverseRow] = []
    if workbook_path.exists() and args.sheet:
        workbook_universe = load_cal_universe(workbook_path, sheet_name=args.sheet)

    if universe_path.exists():
        universe = load_universe_csv(universe_path)
    elif workbook_universe:
        universe = workbook_universe
    else:
        raise SystemExit(f"Universe CSV not found: {universe_path}")

    if details_path:
        universe = enrich_universe_rows(universe, load_universe_csv(details_path))
    if workbook_universe:
        universe = enrich_universe_rows(universe, workbook_universe)

    per100_rows = standardize_rows_generic(
        read_history_csv(HISTORY_DIR / "general_traditional_per100.csv"),
        "Season",
        "PLAYER_NAME",
        "PLAYER_ID",
    )
    advanced_rows = standardize_rows_generic(
        read_history_csv(HISTORY_DIR / "general_advanced.csv"),
        "Season",
        "PLAYER_NAME",
        "PLAYER_ID",
    )
    drives_rows = standardize_rows_generic(
        read_history_csv(HISTORY_DIR / "tracking_drives.csv"),
        "Season",
        "PLAYER_NAME",
        "PLAYER_ID",
    )
    rebound_rows = standardize_rows_generic(
        read_history_csv(HISTORY_DIR / "tracking_rebound.csv"),
        "Season",
        "PLAYER_NAME",
        "PLAYER_ID",
    )
    postop_rows = standardize_rows_generic(
        read_history_csv(HISTORY_DIR / "tracking_postup.csv"),
        "Season",
        "PLAYER_NAME",
        "PLAYER_ID",
    )
    hustle_rows = standardize_rows_generic(
        read_history_csv(HISTORY_DIR / "hustle.csv"),
        "Season",
        "PLAYER_NAME",
        "PLAYER_ID",
    )
    general_defense_rows = standardize_rows_generic(
        read_history_csv(HISTORY_DIR / "general_defense.csv"),
        "Season",
        "PLAYER_NAME",
        "PLAYER_ID",
    )
    bball_close_rows = standardize_rows_generic(
        read_history_csv(HISTORY_DIR / "bball_index_close_shot.csv"),
        "Season",
        "Player",
    )
    defense_all_rows = standardize_rows_generic(
        read_history_csv(EXPORT_DIR / "defense_all_sheet.csv"),
        "Season",
        "Player",
        "NBA ID",
    )
    shooting_three_rows = standardize_rows_generic(
        read_history_csv(EXPORT_DIR / "shooting_three_point_sheet.csv"),
        "Season",
        "Player",
        "NBA ID",
    )
    shooting_splits_rows = read_history_csv(HISTORY_DIR / "shooting_splits.csv")

    with (PROJECT_ROOT / "shot_detail2.csv").open("r", newline="", encoding="utf-8-sig") as fh:
        shot_detail_rows = standardize_shot_detail_rows(list(csv.DictReader(fh)))
    legacy_shot_rows = standardize_shot_area_rows(
        read_legacy_shot_location_rows(HISTORY_DIR / "shot_locations.csv")
    )
    current_shot_rows = standardize_shot_area_rows(
        read_history_csv(HISTORY_DIR / "shot_locations_by_zone.csv")
    )

    historical_shot_rows = merge_shot_area_rows(shot_detail_rows, legacy_shot_rows)
    shot_area_rows = merge_shot_area_rows(current_shot_rows, historical_shot_rows)
    shot_area_rate_rows = build_shot_area_rate_rows(shot_area_rows, advanced_rows)
    shot_type_rows = build_shot_type_rows(shooting_splits_rows)

    per100_table = build_source_table(per100_rows)
    advanced_table = build_source_table(advanced_rows)
    drives_table = build_source_table(drives_rows)
    rebound_table = build_source_table(rebound_rows)
    postop_table = build_source_table(postop_rows)
    hustle_table = build_source_table(hustle_rows)
    general_defense_table = build_source_table(general_defense_rows)
    bball_close_table = build_source_table(bball_close_rows)
    defense_all_table = build_source_table(defense_all_rows)
    shooting_three_table = build_source_table(shooting_three_rows)
    shot_area_rate_table = build_source_table(shot_area_rate_rows)
    shot_type_table = build_source_table(shot_type_rows)

    allow_id_fallback = args.allow_id_fallback
    header_by_key = {key: header for key, header in SHEET_LAYOUT}

    shot_close_coverage = nonzero_season_coverage(shot_area_rate_rows, "PAINT_NON_RA_FGA_PER75")
    restricted_area_coverage = nonzero_season_coverage(shot_area_rate_rows, "RA_FGA_PER75")
    mid_range_coverage = nonzero_season_coverage(shot_area_rate_rows, "MID_RANGE_FGA_PER75")
    charges_coverage = nonzero_season_coverage(hustle_rows, "CHARGES_DRAWN")
    contested_coverage = nonzero_season_coverage(hustle_rows, "CONTESTED_SHOTS")

    pct_stl_raw_values, pct_stl_matched = resolve_numeric_column(
        universe,
        general_defense_table,
        "PCT_STL",
        allow_id_fallback,
    )
    pct_stl_helper_values = z_score(pct_stl_raw_values)

    bundles: Dict[str, MetricBundle] = {}

    for key, column, source_note in (
        ("G", "FGA", "general_traditional_per100.csv -> FGA"),
        ("S", "FG3A", "general_traditional_per100.csv -> FG3A"),
        ("AW", "PF", "general_traditional_per100.csv -> PF"),
    ):
        raw_values, matched_by_values = resolve_numeric_column(
            universe,
            per100_table,
            column,
            allow_id_fallback,
        )
        bundles[key] = resolve_z_metric_bundle(
            universe,
            key,
            header_by_key[key],
            raw_values,
            matched_by_values,
            source_note,
        )

    raw_values, matched_by_values = resolve_numeric_column(
        universe,
        advanced_table,
        "USG_PCT",
        allow_id_fallback,
    )
    bundles["I"] = resolve_z_metric_bundle(
        universe,
        "I",
        header_by_key["I"],
        raw_values,
        matched_by_values,
        "general_advanced.csv -> USG_PCT",
    )

    for key, column, source_note in (
        ("U", "DRIVE_FGA", "tracking_drives.csv -> DRIVE_FGA"),
        ("AE", "DRIVES", "tracking_drives.csv -> DRIVES"),
    ):
        raw_values, matched_by_values = resolve_numeric_column(
            universe,
            drives_table,
            column,
            allow_id_fallback,
        )
        bundles[key] = resolve_z_metric_bundle(
            universe,
            key,
            header_by_key[key],
            raw_values,
            matched_by_values,
            source_note,
        )

    for key, column, source_note in (
        ("AC", "OREB", "tracking_rebound.csv -> OREB"),
        ("AG", "POST_TOUCHES", "tracking_postup.csv -> POST_TOUCHES"),
        ("AI", "POST_TOUCH_FGA", "tracking_postup.csv -> POST_TOUCH_FGA"),
        ("AU", "PCT_BLK", "general_defense.csv -> PCT_BLK"),
    ):
        table = {
            "AC": rebound_table,
            "AG": postop_table,
            "AI": postop_table,
            "AU": general_defense_table,
        }[key]
        raw_values, matched_by_values = resolve_numeric_column(
            universe,
            table,
            column,
            allow_id_fallback,
        )
        bundles[key] = resolve_z_metric_bundle(
            universe,
            key,
            header_by_key[key],
            raw_values,
            matched_by_values,
            source_note,
        )

    for key, column, coverage, source_note in (
        ("AO", "CHARGES_DRAWN", charges_coverage, "hustle.csv -> CHARGES_DRAWN with season-aware zero fill"),
        ("AS", "CONTESTED_SHOTS", contested_coverage, "hustle.csv -> CONTESTED_SHOTS with season-aware zero fill"),
    ):
        raw_values: List[Optional[float]] = []
        matched_by_values: List[str] = []
        for universe_row in universe:
            source_row, matched_by = match_table_row(universe_row, hustle_table, allow_id_fallback)
            raw_value = None if source_row is None else parse_float(source_row.get(column, ""))
            if raw_value is None and universe_row.season in coverage:
                raw_value = 0.0
                matched_by = "default-zero" if not matched_by else f"{matched_by}|default-zero"
            raw_values.append(raw_value)
            matched_by_values.append(matched_by)
        bundles[key] = resolve_z_metric_bundle(
            universe,
            key,
            header_by_key[key],
            raw_values,
            matched_by_values,
            source_note,
        )

    for key, column, source_note in (
        ("K", "PAINT_NON_RA_FGA_PER75", "shot_area totals + general_advanced.csv -> paint non-RA FGA per 75"),
        ("O", "RA_FGA_PER75", "shot_area totals + general_advanced.csv -> restricted area FGA per 75"),
        ("Q", "MID_RANGE_FGA_PER75", "shot_area totals + general_advanced.csv -> mid-range FGA per 75"),
    ):
        coverage = {
            "K": shot_close_coverage,
            "O": restricted_area_coverage,
            "Q": mid_range_coverage,
        }[key]
        raw_values = []
        matched_by_values = []
        for universe_row in universe:
            source_row, matched_by = match_table_row(universe_row, shot_area_rate_table, allow_id_fallback)
            raw_value = None if source_row is None else parse_float(source_row.get(column, ""))
            if raw_value is None and universe_row.season in coverage:
                raw_value = 0.0
                matched_by = "default-zero" if not matched_by else f"{matched_by}|default-zero"
            raw_values.append(raw_value)
            matched_by_values.append(matched_by)
        bundles[key] = resolve_z_metric_bundle(
            universe,
            key,
            AUDIT_LABELS.get(key, header_by_key[key]),
            raw_values,
            matched_by_values,
            source_note,
        )

    rim_raw_values: List[Optional[float]] = []
    rim_matched_by: List[str] = []
    for universe_row in universe:
        bball_row, bball_match = match_table_row(universe_row, bball_close_table, allow_id_fallback)
        raw_value = None if bball_row is None else parse_float(bball_row.get("Rim Shot Attempts Per 75 Possessions", ""))
        matched_by = bball_match
        if raw_value is None:
            fallback_row, fallback_match = match_table_row(universe_row, shot_area_rate_table, allow_id_fallback)
            raw_value = None if fallback_row is None else parse_float(fallback_row.get("RA_FGA_PER75", ""))
            matched_by = (
                ""
                if raw_value is None
                else ("shot-area-fallback" if not fallback_match else f"{fallback_match}|shot-area-fallback")
            )
        if raw_value is None and universe_row.season in restricted_area_coverage:
            raw_value = 0.0
            matched_by = "default-zero" if not matched_by else f"{matched_by}|default-zero"
        rim_raw_values.append(raw_value)
        rim_matched_by.append(matched_by)
    bundles["N"] = resolve_z_metric_bundle(
        universe,
        "N",
        header_by_key["N"],
        rim_raw_values,
        rim_matched_by,
        "bball_index_close_shot.csv -> Rim Shot Attempts Per 75 Possessions, fallback to shot-area restricted area FGA per 75",
    )

    standing_coverage = nonzero_season_coverage(shot_type_rows, "Standing_Dunk_FGA")
    driving_coverage = nonzero_season_coverage(shot_type_rows, "Driving Dunk Shot_FGM")
    putback_coverage = nonzero_season_coverage(shot_type_rows, "Putback_FGA")

    standing_raw_values: List[Optional[float]] = []
    standing_matched_by: List[str] = []
    driving_raw_values: List[Optional[float]] = []
    driving_matched_by: List[str] = []
    putback_raw_values: List[Optional[float]] = []
    putback_matched_by: List[str] = []

    for universe_row in universe:
        shot_row, shot_match = match_table_row(universe_row, shot_type_table, allow_id_fallback)
        rebound_row, rebound_match = match_table_row(universe_row, rebound_table, allow_id_fallback)
        season = universe_row.season
        standing_shot_match = shot_match
        driving_shot_match = shot_match
        putback_shot_match = shot_match

        standing_dunk_fga = None if shot_row is None else parse_float(shot_row.get("Standing_Dunk_FGA", ""))
        if standing_dunk_fga is None and season in standing_coverage:
            standing_dunk_fga = 0.0
            standing_shot_match = (
                "default-zero" if not standing_shot_match else f"{standing_shot_match}|default-zero"
            )

        driving_dunk_fgm = None if shot_row is None else parse_float(shot_row.get("Driving Dunk Shot_FGM", ""))
        if driving_dunk_fgm is None and season in driving_coverage:
            driving_dunk_fgm = 0.0
            driving_shot_match = (
                "default-zero" if not driving_shot_match else f"{driving_shot_match}|default-zero"
            )

        putback_fga = None if shot_row is None else parse_float(shot_row.get("Putback_FGA", ""))
        if putback_fga is None and season in putback_coverage:
            putback_fga = 0.0
            putback_shot_match = (
                "default-zero" if not putback_shot_match else f"{putback_shot_match}|default-zero"
            )

        oreb = None if rebound_row is None else parse_float(rebound_row.get("OREB", ""))

        standing_raw_values.append(
            None if standing_dunk_fga is None or oreb in (None, 0) else standing_dunk_fga / oreb
        )
        standing_matched_by.append(
            combine_match_notes(("shot", standing_shot_match), ("oreb", rebound_match))
        )

        driving_raw_values.append(driving_dunk_fgm)
        driving_matched_by.append(driving_shot_match)

        putback_raw_values.append(
            None if putback_fga is None or oreb in (None, 0) else putback_fga / oreb
        )
        putback_matched_by.append(
            combine_match_notes(("shot", putback_shot_match), ("oreb", rebound_match))
        )

    bundles["W"] = resolve_z_metric_bundle(
        universe,
        "W",
        header_by_key["W"],
        standing_raw_values,
        standing_matched_by,
        "shooting_splits.csv -> standing dunk FGA / tracking_rebound.csv -> OREB",
    )
    bundles["Y"] = resolve_z_metric_bundle(
        universe,
        "Y",
        header_by_key["Y"],
        driving_raw_values,
        driving_matched_by,
        "shooting_splits.csv -> Driving Dunk Shot_FGM with season-aware zero fill",
    )
    bundles["AA"] = resolve_z_metric_bundle(
        universe,
        "AA",
        header_by_key["AA"],
        putback_raw_values,
        putback_matched_by,
        "shooting_splits.csv -> Putback FGA / tracking_rebound.csv -> OREB",
    )

    three_pt_direct_values: List[Optional[float]] = []
    three_pt_direct_matched: List[str] = []
    for universe_row in universe:
        source_row, matched_by = match_table_row(universe_row, shooting_three_table, allow_id_fallback)
        three_point_value = None if source_row is None else parse_float(source_row.get("Three-Point Shot", ""))
        three_pt_direct_values.append(None if three_point_value is None else -three_point_value)
        three_pt_direct_matched.append(matched_by)
    bundles["AK"] = resolve_direct_metric_bundle(
        universe,
        "AK",
        header_by_key["AK"],
        three_pt_direct_values,
        three_pt_direct_matched,
        "shooting_three_point_sheet.csv -> inverse Three-Point Shot aggregate",
    )

    for key, column, source_note in (
        ("AM", "Pass Perception", "defense_all_sheet.csv -> Pass Perception"),
    ):
        direct_values, matched_by_values = resolve_numeric_column(
            universe,
            defense_all_table,
            column,
            allow_id_fallback,
        )
        bundles[key] = resolve_direct_metric_bundle(
            universe,
            key,
            header_by_key[key],
            direct_values,
            matched_by_values,
            source_note,
        )

    pickpocket_values: List[Optional[float]] = []
    pickpocket_matched: List[str] = []
    for index, universe_row in enumerate(universe):
        defense_row, defense_match = match_table_row(universe_row, defense_all_table, allow_id_fallback)
        pickpocket_value = None if defense_row is None else parse_float(defense_row.get("Pickpocket Rating", ""))
        matched_by = defense_match
        if pickpocket_value is None:
            pickpocket_value = pct_stl_helper_values[index]
            if pickpocket_value is not None:
                matched_by = (
                    "pct-stl-fallback"
                    if not pct_stl_matched[index]
                    else f"{pct_stl_matched[index]}|pct-stl-fallback"
                )
        pickpocket_values.append(pickpocket_value)
        pickpocket_matched.append(matched_by)
    bundles["AQ"] = resolve_direct_metric_bundle(
        universe,
        "AQ",
        header_by_key["AQ"],
        pickpocket_values,
        pickpocket_matched,
        "defense_all_sheet.csv -> Pickpocket Rating, fallback to general_defense.csv -> PCT_STL z-score",
    )

    under_basket_helper_values: List[Optional[float]] = []
    for rim_value, restricted_value in zip(bundles["N"].helper_values, bundles["O"].helper_values):
        average_value = average_numeric([rim_value, restricted_value])
        under_basket_helper_values.append(0.0 if average_value is None else average_value)
    bundles["M"] = MetricBundle(
        key="M",
        header=AUDIT_LABELS["M"],
        raw_values=list(under_basket_helper_values),
        helper_values=list(under_basket_helper_values),
        matched_by=["computed-average"] * len(universe),
        source_note="AVERAGE(N, O), defaulting to 0 when both helper inputs are blank",
    )

    sheet_values: Dict[str, List[object]] = {key: [] for key, _ in SHEET_LAYOUT}
    for universe_row in universe:
        last_name, first_name = split_first_last_name(universe_row.player)
        sheet_values["A"].append(universe_row.nba_id)
        sheet_values["B"].append(universe_row.season)
        sheet_values["C"].append(universe_row.player)
        sheet_values["D"].append(last_name)
        sheet_values["E"].append(first_name)

    for key in RAW_METRIC_KEYS:
        sheet_values[key] = [
            "" if value is None else value
            for value in bundles[key].helper_values
        ]
    sheet_values["M"] = ["" if value is None else value for value in bundles["M"].helper_values]

    for output_key, helper_key, blank_fallback in TENDENCY_OUTPUTS:
        sheet_values[output_key] = [
            "" if value is None else value
            for value in build_tendency_values(
                [None if item == "" else parse_float(item) for item in sheet_values[helper_key]],
                blank_fallback,
            )
        ]

    sheet_headers = build_sheet_headers()
    sheet_rows = [
        [sheet_values[key][index] for key, _ in SHEET_LAYOUT]
        for index in range(len(universe))
    ]
    rating_only_headers = ["NBA_ID", "Season", "Player"] + [
        header_by_key[key] for key in TENDENCY_RESULT_KEYS
    ]

    metric_order = list(RAW_METRIC_KEYS) + ["M"]
    audit_headers = ["NBA_ID", "Season", "Player"]
    for key in metric_order:
        label = AUDIT_LABELS.get(key, header_by_key.get(key) or key)
        audit_headers.extend(
            [
                f"{label} Raw",
                f"{label} Helper",
                f"{label} MatchedBy",
                f"{label} Source",
            ]
        )
    for output_key, _, _ in TENDENCY_OUTPUTS:
        audit_headers.append(header_by_key[output_key])

    audit_rows: List[Dict[str, object]] = []
    rating_only_rows: List[Dict[str, object]] = []
    unmatched_rows: List[Dict[str, object]] = []
    for index, universe_row in enumerate(universe):
        rating_row: Dict[str, object] = {
            "NBA_ID": universe_row.nba_id,
            "Season": universe_row.season,
            "Player": universe_row.player,
        }
        for output_key in TENDENCY_RESULT_KEYS:
            rating_row[header_by_key[output_key]] = sheet_values[output_key][index]
        rating_only_rows.append(rating_row)

        audit_row: Dict[str, object] = {
            "NBA_ID": universe_row.nba_id,
            "Season": universe_row.season,
            "Player": universe_row.player,
        }
        for key in metric_order:
            bundle = bundles[key]
            label = AUDIT_LABELS.get(key, header_by_key.get(key) or key)
            raw_value = bundle.raw_values[index]
            helper_value = bundle.helper_values[index]
            audit_row[f"{label} Raw"] = raw_value
            audit_row[f"{label} Helper"] = helper_value
            audit_row[f"{label} MatchedBy"] = bundle.matched_by[index]
            audit_row[f"{label} Source"] = bundle.source_note

            if key != "M" and helper_value is None:
                unmatched_rows.append(
                    {
                        "NBA_ID": universe_row.nba_id,
                        "Season": universe_row.season,
                        "Player": universe_row.player,
                        "MetricKey": key,
                        "Metric": label,
                        "Source": bundle.source_note,
                        "MatchedBy": bundle.matched_by[index],
                    }
                )

        for output_key, _, _ in TENDENCY_OUTPUTS:
            audit_row[header_by_key[output_key]] = sheet_values[output_key][index]
        audit_rows.append(audit_row)

    output_prefix = args.output_prefix
    sheet_path = EXPORT_DIR / f"{output_prefix}_sheet.csv"
    rating_only_path = EXPORT_DIR / f"{output_prefix}_ratings.csv"
    audit_path = EXPORT_DIR / f"{output_prefix}_audit.csv"
    unmatched_path = EXPORT_DIR / f"{output_prefix}_unmatched.csv"

    write_matrix_csv(sheet_path, sheet_headers, sheet_rows)
    write_csv(rating_only_path, rating_only_headers, rating_only_rows)
    write_csv(audit_path, audit_headers, audit_rows)
    write_csv(
        unmatched_path,
        ["NBA_ID", "Season", "Player", "MetricKey", "Metric", "Source", "MatchedBy"],
        unmatched_rows,
    )

    print(f"[OK] Built tendency export for {len(universe)} player-season rows")


if __name__ == "__main__":
    main()
