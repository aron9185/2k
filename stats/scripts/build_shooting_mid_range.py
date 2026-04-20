from __future__ import annotations

import argparse
import csv
import statistics
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from build_cal_lane import (
    CalUniverseRow,
    detect_current_season,
    enrich_universe_rows,
    load_cal_universe,
    load_universe_csv,
    read_history_csv,
    resolve_details_csv_path,
    write_csv,
)
from build_finishing_layup import (
    ComponentSlot,
    MetricResult,
    MetricSpec,
    build_player_contexts,
    compute_metric_result,
    interpolate_rating,
    percentile_inc,
    standardize_bball_rows,
    write_matrix_csv,
)
from build_finishing_standing_dunk import standardize_rows
from pull_nba_stats import EXPORT_DIR, HISTORY_DIR, MANUAL_DIR


MID_RANGE_METRICS: Sequence[MetricSpec] = (
    MetricSpec("Midrange Talent", "bball", "Midrange Talent"),
    MetricSpec("Midrange Shot Making", "bball", "Midrange Shot Making"),
    MetricSpec("Midrange Shot Creation", "bball", "Midrange Shot Creation"),
    MetricSpec("Midrange Pull Up Talent", "bball", "Midrange Pull Up Talent"),
    MetricSpec("Midrange Pull Up Shot Making", "bball", "Midrange Pull Up Shot Making"),
    MetricSpec(
        "Midrange Pull Up Shot Making Efficiency",
        "bball",
        "Midrange Pull Up Shot Making Efficiency",
    ),
    MetricSpec("Stable Long Midrange FG%", "bball", "Stable Long Midrange FG%"),
    MetricSpec("Stable Short Midrange FG%", "bball", "Stable Short Midrange FG%"),
    MetricSpec("Midrange FGM Per 75", "bball", "Midrange FGM Per 75"),
    MetricSpec("Midrange Pull Up FGM Per 75", "bball", "Midrange Pull Up FGM Per 75"),
    MetricSpec(
        "Midrange Shot Making Efficiency",
        "bball",
        "Midrange Shot Making Efficiency",
    ),
    MetricSpec("MID-RANGE FGM", "mid_range_zone", "MID-RANGE FGM"),
    MetricSpec("Midrange Pull Up FG%", "bball", "Midrange Pull Up FG%"),
    MetricSpec("MID-RANGE FG_PCT", "mid_range_zone", "MID-RANGE FG_PCT"),
)

MID_RANGE_COMPONENTS: Sequence[ComponentSlot] = (
    ComponentSlot("Midrange Talent", "Midrange Talent"),
    ComponentSlot("Midrange Shot Making", "Midrange Shot Making"),
    ComponentSlot("Midrange Shot Creation", "Midrange Shot Creation"),
    ComponentSlot("Midrange Pull Up Talent", "Midrange Pull Up Talent"),
    ComponentSlot("Midrange Pull Up Shot Making", "Midrange Pull Up Shot Making"),
    ComponentSlot(
        "Midrange Pull Up Shot Making Efficiency",
        "Midrange Pull Up Shot Making Efficiency",
    ),
    ComponentSlot("Stable Long Midrange FG%", "Stable Long Midrange FG%"),
    ComponentSlot("Stable Short Midrange FG%", "Stable Short Midrange FG%"),
    ComponentSlot("Midrange FGM Per 75", "Midrange FGM Per 75"),
    ComponentSlot("Midrange Pull Up FGM Per 75", "Midrange Pull Up FGM Per 75"),
    ComponentSlot("Midrange Shot Making Efficiency", "Midrange Shot Making Efficiency"),
    ComponentSlot(
        "Midrange Pull Up Shot Making Efficiency",
        "Midrange Pull Up Shot Making Efficiency",
    ),
    ComponentSlot("MID-RANGE FGM", "MID-RANGE FGM"),
    ComponentSlot("Midrange Pull Up FG%", "Midrange Pull Up FG%"),
    ComponentSlot("MID-RANGE FG_PCT", "MID-RANGE FG_PCT"),
)

PRIMARY_COMPONENT_COUNT = 13
LEGACY_SHOT_LOCATION_ZONE_ORDER = (
    "Restricted Area",
    "In The Paint (Non-RA)",
    "Mid-Range",
    "Left Corner 3",
    "Right Corner 3",
    "Above the Break 3",
    "Backcourt",
    "Corner 3",
)
SHOT_LOCATION_METRICS = ("FGM", "FGA", "FG_PCT")


def mid_range_rating(value: float, population: Sequence[float]) -> float:
    minimum = min(population)
    p50 = percentile_inc(population, 0.50)
    p60 = percentile_inc(population, 0.60)
    p70 = percentile_inc(population, 0.70)
    p80 = percentile_inc(population, 0.80)
    p88 = percentile_inc(population, 0.88)
    p94 = percentile_inc(population, 0.94)
    p98 = percentile_inc(population, 0.98)
    maximum = percentile_inc(population, 1.0)

    if value <= p50:
        return interpolate_rating(value, minimum, p50, 25.0, 74.0)
    if value <= p60:
        return interpolate_rating(value, p50, p60, 74.0, 77.0)
    if value <= p70:
        return interpolate_rating(value, p60, p70, 77.0, 80.0)
    if value <= p80:
        return interpolate_rating(value, p70, p80, 80.0, 84.0)
    if value <= p88:
        return interpolate_rating(value, p80, p88, 84.0, 88.0)
    if value <= p94:
        return interpolate_rating(value, p88, p94, 88.0, 92.0)
    if value <= p98:
        return interpolate_rating(value, p94, p98, 92.0, 95.0)
    return interpolate_rating(value, p98, maximum, 95.0, 100.0)


def average_numeric(values: Sequence[Optional[float]]) -> Optional[float]:
    numeric = [value for value in values if value is not None]
    if not numeric:
        return None
    return statistics.mean(numeric)


def read_legacy_shot_location_rows(path: Path) -> List[Dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8") as fh:
        reader = csv.reader(fh)
        header = next(reader, [])
        if len(header) < 7:
            raise SystemExit(f"Legacy shot-location CSV is missing base columns: {path.name}")

        base_headers = header[:7]
        rows: List[Dict[str, str]] = []
        for raw_row in reader:
            if len(raw_row) < 7 + len(LEGACY_SHOT_LOCATION_ZONE_ORDER) * len(SHOT_LOCATION_METRICS):
                continue

            row = {base_headers[index]: raw_row[index] for index in range(7)}
            cursor = 7
            for zone in LEGACY_SHOT_LOCATION_ZONE_ORDER:
                for metric in SHOT_LOCATION_METRICS:
                    row[f"{zone}_{metric}"] = raw_row[cursor]
                    cursor += 1
            rows.append(row)
    return rows


def standardize_mid_range_zone_rows(rows: Sequence[Dict[str, str]]) -> List[Dict[str, str]]:
    standardized = standardize_rows(
        rows,
        season_column="Season",
        player_column="PLAYER_NAME",
    )
    for row in standardized:
        row["MID-RANGE FGM"] = row.get("Mid-Range_FGM", "")
        row["MID-RANGE FG_PCT"] = row.get("Mid-Range_FG_PCT", "")
    return standardized


def merge_mid_range_zone_rows(
    current_rows: Sequence[Dict[str, str]],
    legacy_rows: Sequence[Dict[str, str]],
) -> List[Dict[str, str]]:
    merged_by_key: Dict[Tuple[str, str], Dict[str, str]] = {}

    for row in legacy_rows:
        season = str(row.get("Season", "")).strip()
        player_id = str(row.get("PLAYER_ID", "")).strip()
        if not season:
            continue
        merged_by_key[(season, player_id)] = dict(row)

    for row in current_rows:
        season = str(row.get("Season", "")).strip()
        player_id = str(row.get("PLAYER_ID", "")).strip()
        if not season:
            continue
        merged_by_key[(season, player_id)] = dict(row)

    return list(merged_by_key.values())


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build the Shooting -> Mid-Range Shot rating export."
    )
    parser.add_argument(
        "--universe-csv",
        default=str(MANUAL_DIR / "playerlist.csv"),
        help="Season/player universe CSV. Defaults to stats/manual/playerlist.csv.",
    )
    parser.add_argument(
        "--workbook",
        default=str(MANUAL_DIR / "2k26_Temp_for_codex.xlsx"),
        help="Workbook fallback used for Cal role/minutes.",
    )
    parser.add_argument(
        "--sheet",
        default="Cal",
        help="Workbook sheet used for role/minute fallback.",
    )
    parser.add_argument(
        "--details-csv",
        default="",
        help=(
            "Optional role/minutes detail CSV. "
            "Pass stats/manual/player_universe.csv explicitly if you want that enrichment."
        ),
    )
    parser.add_argument(
        "--minutes-source",
        default=str(HISTORY_DIR / "general_traditional.csv"),
        help="History CSV used to refresh MIN and GP in real time.",
    )
    parser.add_argument(
        "--minutes-column",
        default="MIN",
        help="Column from the minutes source used for live minutes.",
    )
    parser.add_argument(
        "--minutes-games-column",
        default="GP",
        help="Optional column used to convert a per-game MIN column into total minutes.",
    )
    parser.add_argument(
        "--current-season",
        default="",
        help="Season string that should use the lower in-season minute threshold.",
    )
    parser.add_argument(
        "--current-season-min-threshold",
        type=float,
        default=200.0,
        help="Minute threshold for penalty rows in the current season.",
    )
    parser.add_argument(
        "--standard-min-threshold",
        type=float,
        default=1000.0,
        help="Minute threshold for penalty rows in completed seasons.",
    )
    parser.add_argument(
        "--allow-id-fallback",
        action="store_true",
        help="Allow season+NBA_ID fallback when normalized season+player matching fails.",
    )
    parser.add_argument(
        "--bball-index-source",
        default=str(HISTORY_DIR / "bball_index_mid_range.csv"),
        help="CSV file containing the Mid-Range bball-index metrics.",
    )
    parser.add_argument(
        "--legacy-shot-locations-source",
        default=str(HISTORY_DIR / "shot_locations.csv"),
        help="Legacy shot-location CSV used for pre-2025-26 Mid-Range history.",
    )
    parser.add_argument(
        "--current-shot-locations-source",
        default=str(HISTORY_DIR / "shot_locations_by_zone.csv"),
        help="Wide shot-location CSV used to override the newest season with live zone data.",
    )
    parser.add_argument(
        "--output-prefix",
        default="shooting_mid_range",
        help="Prefix used for CSV outputs inside stats/exports.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    workbook_path = Path(args.workbook)
    universe_path = Path(args.universe_csv)
    details_path = resolve_details_csv_path(args.details_csv, universe_path)
    minutes_source_path = Path(args.minutes_source)
    bball_index_source_path = Path(args.bball_index_source)
    legacy_shot_locations_path = Path(args.legacy_shot_locations_source)
    current_shot_locations_path = Path(args.current_shot_locations_source)

    for path in (
        minutes_source_path,
        bball_index_source_path,
        legacy_shot_locations_path,
        current_shot_locations_path,
    ):
        if not path.exists():
            raise SystemExit(f"Source CSV not found: {path}")
    if details_path and not details_path.exists():
        raise SystemExit(f"Details CSV not found: {details_path}")

    workbook_universe: List[CalUniverseRow] = []
    if workbook_path.exists():
        workbook_universe = load_cal_universe(workbook_path, sheet_name=args.sheet)

    if universe_path.exists():
        universe = load_universe_csv(universe_path)
    elif workbook_universe:
        universe = workbook_universe
    else:
        raise SystemExit(
            f"Universe CSV not found: {universe_path} and workbook not found: {workbook_path}"
        )

    if details_path:
        universe = enrich_universe_rows(universe, load_universe_csv(details_path))
    if workbook_universe:
        universe = enrich_universe_rows(universe, workbook_universe)

    minutes_rows = read_history_csv(minutes_source_path)
    if not minutes_rows:
        raise SystemExit(f"No rows found in minutes source CSV: {minutes_source_path.name}")
    if args.minutes_column not in minutes_rows[0]:
        raise SystemExit(
            f"Minutes column not found in {minutes_source_path.name}: {args.minutes_column}"
        )
    if args.minutes_games_column and args.minutes_games_column not in minutes_rows[0]:
        raise SystemExit(
            f"Minutes games column not found in {minutes_source_path.name}: {args.minutes_games_column}"
        )

    merged_zone_rows = merge_mid_range_zone_rows(
        current_rows=read_history_csv(current_shot_locations_path),
        legacy_rows=read_legacy_shot_location_rows(legacy_shot_locations_path),
    )

    source_rows_by_key: Dict[str, List[Dict[str, str]]] = {
        "bball": standardize_bball_rows(read_history_csv(bball_index_source_path)),
        "mid_range_zone": standardize_mid_range_zone_rows(merged_zone_rows),
    }

    current_season = detect_current_season(args.current_season, universe, minutes_rows)
    contexts = build_player_contexts(
        universe=universe,
        minutes_rows=minutes_rows,
        minutes_column=args.minutes_column,
        minutes_games_column=args.minutes_games_column,
        allow_id_fallback=args.allow_id_fallback,
    )

    metric_results: Dict[str, MetricResult] = {}
    for metric in MID_RANGE_METRICS:
        metric_results[metric.alias] = compute_metric_result(
            contexts=contexts,
            rows=source_rows_by_key[metric.source_key],
            metric=metric,
            current_season=current_season,
            current_season_min_threshold=args.current_season_min_threshold,
            standard_min_threshold=args.standard_min_threshold,
            allow_id_fallback=args.allow_id_fallback,
        )

    mid_range_scores: List[float] = []
    component_rows: List[List[Optional[float]]] = []
    for row_index in range(len(contexts)):
        component_values = [
            metric_results[component.metric_alias].normalized_values[row_index]
            for component in MID_RANGE_COMPONENTS
        ]
        component_rows.append(component_values)

        primary_group = component_values[:PRIMARY_COMPONENT_COUNT]
        support_group = component_values[PRIMARY_COMPONENT_COUNT:]
        primary_average = average_numeric(primary_group)
        support_average = average_numeric(support_group) or 0.0
        mid_range_score = -1.0 if primary_average is None else primary_average * 0.95 + support_average * 0.05
        mid_range_scores.append(mid_range_score)

    if len(mid_range_scores) < 2:
        raise SystemExit("Not enough Mid-Range scores to compute aggregate z-scores.")

    mid_range_median = statistics.median(mid_range_scores)
    mid_range_stdev = statistics.stdev(mid_range_scores)
    if mid_range_stdev == 0:
        raise SystemExit("Mid-Range scores have zero variance; cannot compute aggregate z-scores.")

    aggregate_z_scores = [
        (value - mid_range_median) / mid_range_stdev for value in mid_range_scores
    ]
    mid_range_ratings = [
        mid_range_rating(value, aggregate_z_scores)
        for value in aggregate_z_scores
    ]

    sheet_headers = [
        "NBA ID",
        "Season",
        "Player",
        "Mid-Range Shot Rating",
        "",
        "Mid-Range Shot",
        *[component.header for component in MID_RANGE_COMPONENTS],
    ]
    sheet_rows: List[List[object]] = []
    rating_only_rows: List[Dict[str, object]] = []
    audit_rows: List[Dict[str, object]] = []
    unmatched_rows: List[Dict[str, object]] = []

    for index, context in enumerate(contexts):
        season = context.universe_row.season
        player = context.universe_row.player
        component_values = component_rows[index]
        missing_metrics = [
            metric.alias
            for metric in MID_RANGE_METRICS
            if metric_results[metric.alias].raw_values[index] is None
        ]

        sheet_rows.append(
            [
                context.universe_row.nba_id,
                season,
                player,
                mid_range_ratings[index],
                aggregate_z_scores[index],
                mid_range_scores[index],
                *component_values,
            ]
        )
        rating_only_rows.append(
            {
                "NBA_ID": context.universe_row.nba_id,
                "Season": season,
                "Player": player,
                "Mid-Range Shot Rating": mid_range_ratings[index],
            }
        )

        audit_row: Dict[str, object] = {
            "NBA_ID": context.universe_row.nba_id,
            "Season": season,
            "Player": player,
            "RotationRole": context.universe_row.rotation_role,
            "MIN": context.effective_minutes,
            "WorkbookMIN": context.workbook_minutes,
            "LiveMIN": context.live_minutes,
            "LiveMINPerGame": context.live_minutes_per_game,
            "LiveGP": context.live_gp,
            "MinutesMatchedBy": context.minutes_matched_by,
            "CurrentSeason": current_season,
            "Mid-Range Shot": mid_range_scores[index],
            "MidRangeAggregateZ": aggregate_z_scores[index],
            "MidRangeRating": mid_range_ratings[index],
            "MissingMetricCount": len(missing_metrics),
            "MissingMetrics": " | ".join(missing_metrics),
        }
        for metric in MID_RANGE_METRICS:
            result = metric_results[metric.alias]
            audit_row[f"{metric.alias} Raw"] = result.raw_values[index]
            audit_row[f"{metric.alias} Z"] = result.normalized_values[index]
            audit_row[f"{metric.alias} MatchedBy"] = result.matched_by[index]
            audit_row[f"{metric.alias} Mean"] = result.mean_value
            audit_row[f"{metric.alias} Stdev"] = result.stdev_value
        audit_rows.append(audit_row)

        if missing_metrics:
            unmatched_rows.append(
                {
                    "NBA_ID": context.universe_row.nba_id,
                    "Season": season,
                    "Player": player,
                    "RotationRole": context.universe_row.rotation_role,
                    "MIN": context.effective_minutes,
                    "MissingCount": len(missing_metrics),
                    "MissingMetrics": " | ".join(missing_metrics),
                }
            )

    output_prefix = args.output_prefix.strip() or "shooting_mid_range"
    sheet_path = EXPORT_DIR / f"{output_prefix}_sheet.csv"
    rating_only_path = EXPORT_DIR / f"{output_prefix}_rating_only.csv"
    audit_path = EXPORT_DIR / f"{output_prefix}_audit.csv"
    unmatched_path = EXPORT_DIR / f"{output_prefix}_unmatched.csv"

    write_matrix_csv(sheet_path, sheet_headers, sheet_rows)
    write_csv(
        rating_only_path,
        ["NBA_ID", "Season", "Player", "Mid-Range Shot Rating"],
        rating_only_rows,
    )
    write_csv(
        audit_path,
        list(audit_rows[0].keys()) if audit_rows else [],
        audit_rows,
    )
    write_csv(
        unmatched_path,
        ["NBA_ID", "Season", "Player", "RotationRole", "MIN", "MissingCount", "MissingMetrics"],
        unmatched_rows,
    )

    print(
        f"[OK] Built Mid-Range Shot export for {len(sheet_rows)} player-season rows "
        f"(aggregate median={mid_range_median:.6f}, stdev={mid_range_stdev:.6f})"
    )
    print(
        "[INFO] Workbook duplicate kept: Midrange Pull Up Shot Making Efficiency "
        "is counted twice to mirror the sheet."
    )
    print(f"[OUT] Sheet -> {sheet_path}")
    print(f"[OUT] Rating only -> {rating_only_path}")
    print(f"[OUT] Audit -> {audit_path}")
    print(f"[OUT] Unmatched -> {unmatched_path}")


if __name__ == "__main__":
    main()
