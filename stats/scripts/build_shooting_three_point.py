from __future__ import annotations

import argparse
import statistics
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from build_cal_lane import (
    CalUniverseRow,
    build_source_index,
    detect_current_season,
    enrich_universe_rows,
    load_cal_universe,
    load_universe_csv,
    match_metric_row,
    parse_float,
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
from pull_nba_stats import EXPORT_DIR, HISTORY_DIR, MANUAL_DIR


THREE_POINT_METRICS: Sequence[MetricSpec] = (
    MetricSpec("3PT Shooting Talent", "bball", "3PT Shooting Talent"),
    MetricSpec("3PT Shot Making", "bball", "3PT Shot Making"),
    MetricSpec("C&S 3PT Shot Making", "bball", "C&S 3PT Shot Making"),
    MetricSpec("3PT Pull Up Talent", "bball", "3PT Pull Up Talent"),
    MetricSpec("3PT Pull Up Shot Making", "bball", "3PT Pull Up Shot Making"),
    MetricSpec("3PT Pull Up Shot Creation", "bball", "3PT Pull Up Shot Creation"),
    MetricSpec("3PT Shot Creation", "bball", "3PT Shot Creation"),
    MetricSpec("3PT Shot Making Efficiency", "bball", "3PT Shot Making Efficiency"),
    MetricSpec("C&S 3PT Shot Making Efficiency", "bball", "C&S 3PT Shot Making Efficiency"),
    MetricSpec("Stable FG3%", "bball", "Stable FG3%"),
    MetricSpec("Stable C&S 3PT%", "bball", "Stable C&S 3PT%"),
    MetricSpec("Stable ATB 3PT%", "bball", "Stable ATB 3PT%"),
    MetricSpec("Stable Pull Up 3PT%", "bball", "Stable Pull Up 3PT%"),
    MetricSpec("FG3M", "box", "FG3M"),
    MetricSpec("3PT Functional Versatility", "bball", "3PT Functional Versatility"),
    MetricSpec("Stable 3PTA Per 75", "bball", "Stable 3PTA Per 75"),
    MetricSpec("Off-Ball Gravity", "bball", "Off-Ball Gravity"),
    MetricSpec("Stable Corner 3PT%", "bball", "Stable Corner 3PT%"),
    MetricSpec("3PT Attempt Rate", "bball", "3PT Attempt Rate"),
    MetricSpec("FG3_PCT", "box", "FG3_PCT"),
)

THREE_POINT_COMPONENTS: Sequence[ComponentSlot] = (
    ComponentSlot("3PT Shooting Talent", "3PT Shooting Talent"),
    ComponentSlot("3PT Shot Making", "3PT Shot Making"),
    ComponentSlot("C&S 3PT Shot Making", "C&S 3PT Shot Making"),
    ComponentSlot("3PT Pull Up Talent", "3PT Pull Up Talent"),
    ComponentSlot("3PT Pull Up Shot Making", "3PT Pull Up Shot Making"),
    ComponentSlot("3PT Pull Up Shot Creation", "3PT Pull Up Shot Creation"),
    ComponentSlot("3PT Shot Creation", "3PT Shot Creation"),
    ComponentSlot("3PT Shot Making Efficiency", "3PT Shot Making Efficiency"),
    ComponentSlot("C&S 3PT Shot Making Efficiency", "C&S 3PT Shot Making Efficiency"),
    ComponentSlot("Stable FG3%", "Stable FG3%"),
    ComponentSlot("Stable C&S 3PT%", "Stable C&S 3PT%"),
    ComponentSlot("Stable ATB 3PT%", "Stable ATB 3PT%"),
    ComponentSlot("Stable Pull Up 3PT%", "Stable Pull Up 3PT%"),
    ComponentSlot("FG3M", "FG3M"),
    ComponentSlot("3PT Functional Versatility", "3PT Functional Versatility"),
    ComponentSlot("Stable 3PTA Per 75", "Stable 3PTA Per 75"),
    ComponentSlot("Off-Ball Gravity", "Off-Ball Gravity"),
    ComponentSlot("Stable Corner 3PT%", "Stable Corner 3PT%"),
    ComponentSlot("3PT Attempt Rate", "3PT Attempt Rate"),
    ComponentSlot("FG3_PCT", "FG3_PCT"),
)

PRIMARY_COMPONENT_COUNT = 14
LOW_VOLUME_FG3A_THRESHOLD = 0.2


def three_point_rating(value: float, population: Sequence[float]) -> float:
    minimum = min(population)
    p15 = percentile_inc(population, 0.15)
    p30 = percentile_inc(population, 0.30)
    p50 = percentile_inc(population, 0.50)
    p70 = percentile_inc(population, 0.70)
    p83 = percentile_inc(population, 0.83)
    p90 = percentile_inc(population, 0.90)
    p97 = percentile_inc(population, 0.97)
    maximum = percentile_inc(population, 1.0)

    if value <= p15:
        return interpolate_rating(value, minimum, p15, 25.0, 45.0)
    if value <= p30:
        return interpolate_rating(value, p15, p30, 45.0, 65.0)
    if value <= p50:
        return interpolate_rating(value, p30, p50, 65.0, 74.0)
    if value <= p70:
        return interpolate_rating(value, p50, p70, 74.0, 79.0)
    if value <= p83:
        return interpolate_rating(value, p70, p83, 79.0, 83.0)
    if value <= p90:
        return interpolate_rating(value, p83, p90, 83.0, 85.0)
    if value <= p97:
        return interpolate_rating(value, p90, p97, 85.0, 91.0)
    return interpolate_rating(value, p97, maximum, 91.0, 100.0)


def average_numeric(values: Sequence[Optional[float]]) -> Optional[float]:
    numeric = [value for value in values if value is not None]
    if not numeric:
        return None
    return statistics.mean(numeric)


def load_raw_metric_values(
    contexts,
    rows: Sequence[Dict[str, str]],
    metric_column: str,
    allow_id_fallback: bool,
) -> Tuple[List[Optional[float]], List[str]]:
    if not rows:
        raise SystemExit(f"No rows found for raw metric column: {metric_column}")
    if metric_column not in rows[0]:
        raise SystemExit(f"Metric column not found: {metric_column}")

    by_id, by_name = build_source_index(list(rows))
    raw_values: List[Optional[float]] = []
    matched_by_values: List[str] = []

    for context in contexts:
        source_row, matched_by = match_metric_row(
            context.universe_row,
            by_id,
            by_name,
            allow_id_fallback=allow_id_fallback,
        )
        raw_values.append(None if source_row is None else parse_float(source_row.get(metric_column, "")))
        matched_by_values.append(matched_by)

    return raw_values, matched_by_values


def adjusted_three_point_z(aggregate_z: float, fg3a_raw: Optional[float]) -> float:
    attempts = fg3a_raw or 0.0
    if attempts == 0:
        return -3.0
    if attempts <= LOW_VOLUME_FG3A_THRESHOLD:
        return max(aggregate_z - 2.5, -3.0)
    return max(aggregate_z, -3.0)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build the Shooting -> 3-Point Shot rating export."
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
        default=str(HISTORY_DIR / "bball_index_three_point.csv"),
        help="CSV file containing the 3-point bball-index metrics.",
    )
    parser.add_argument(
        "--box-score-source",
        default=str(HISTORY_DIR / "general_traditional.csv"),
        help="History CSV used for FG3A, FG3M, and FG3_PCT.",
    )
    parser.add_argument(
        "--output-prefix",
        default="shooting_three_point",
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
    box_score_source_path = Path(args.box_score_source)

    for path in (
        minutes_source_path,
        bball_index_source_path,
        box_score_source_path,
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

    source_rows_by_key: Dict[str, List[Dict[str, str]]] = {
        "bball": standardize_bball_rows(read_history_csv(bball_index_source_path)),
        "box": read_history_csv(box_score_source_path),
    }
    current_season = detect_current_season(args.current_season, universe, minutes_rows)
    contexts = build_player_contexts(
        universe=universe,
        minutes_rows=minutes_rows,
        minutes_column=args.minutes_column,
        minutes_games_column=args.minutes_games_column,
        allow_id_fallback=args.allow_id_fallback,
    )

    fg3a_raw_values, fg3a_matched_by = load_raw_metric_values(
        contexts=contexts,
        rows=source_rows_by_key["box"],
        metric_column="FG3A",
        allow_id_fallback=args.allow_id_fallback,
    )

    metric_results: Dict[str, MetricResult] = {}
    for metric in THREE_POINT_METRICS:
        metric_results[metric.alias] = compute_metric_result(
            contexts=contexts,
            rows=source_rows_by_key[metric.source_key],
            metric=metric,
            current_season=current_season,
            current_season_min_threshold=args.current_season_min_threshold,
            standard_min_threshold=args.standard_min_threshold,
            allow_id_fallback=args.allow_id_fallback,
        )

    three_point_scores: List[float] = []
    component_rows: List[List[Optional[float]]] = []
    for row_index in range(len(contexts)):
        component_values = [
            metric_results[component.metric_alias].normalized_values[row_index]
            for component in THREE_POINT_COMPONENTS
        ]
        component_rows.append(component_values)

        primary_group = component_values[:PRIMARY_COMPONENT_COUNT]
        support_group = component_values[PRIMARY_COMPONENT_COUNT:]
        primary_average = average_numeric(primary_group)
        support_average = average_numeric(support_group)
        three_point_score = (
            -1.0
            if primary_average is None or support_average is None
            else primary_average * 0.65 + support_average * 0.35
        )
        three_point_scores.append(three_point_score)

    if len(three_point_scores) < 2:
        raise SystemExit("Not enough three-point scores to compute aggregate z-scores.")

    three_point_median = statistics.median(three_point_scores)
    three_point_stdev = statistics.stdev(three_point_scores)
    if three_point_stdev == 0:
        raise SystemExit("Three-point scores have zero variance; cannot compute aggregate z-scores.")

    aggregate_z_scores = [
        (value - three_point_median) / three_point_stdev for value in three_point_scores
    ]
    adjusted_z_scores = [
        adjusted_three_point_z(aggregate_z_scores[index], fg3a_raw_values[index])
        for index in range(len(contexts))
    ]
    three_point_ratings = [
        three_point_rating(value, adjusted_z_scores)
        for value in adjusted_z_scores
    ]

    sheet_headers = [
        "NBA ID",
        "Season",
        "Player",
        "3-Point Shot Rating",
        "",
        "Three-Point Shot",
        "FG3A",
        *[component.header for component in THREE_POINT_COMPONENTS],
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
            for metric in THREE_POINT_METRICS
            if metric_results[metric.alias].raw_values[index] is None
        ]
        if fg3a_raw_values[index] is None:
            missing_metrics.append("FG3A")

        sheet_rows.append(
            [
                context.universe_row.nba_id,
                season,
                player,
                three_point_ratings[index],
                adjusted_z_scores[index],
                three_point_scores[index],
                fg3a_raw_values[index],
                *component_values,
            ]
        )
        rating_only_rows.append(
            {
                "NBA_ID": context.universe_row.nba_id,
                "Season": season,
                "Player": player,
                "3-Point Shot Rating": three_point_ratings[index],
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
            "Three-Point Shot": three_point_scores[index],
            "ThreePointAggregateZ": aggregate_z_scores[index],
            "ThreePointAdjustedZ": adjusted_z_scores[index],
            "ThreePointRating": three_point_ratings[index],
            "FG3A Raw": fg3a_raw_values[index],
            "FG3A MatchedBy": fg3a_matched_by[index],
            "MissingMetricCount": len(missing_metrics),
            "MissingMetrics": " | ".join(missing_metrics),
        }
        for metric in THREE_POINT_METRICS:
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

    output_prefix = args.output_prefix.strip() or "shooting_three_point"
    sheet_path = EXPORT_DIR / f"{output_prefix}_sheet.csv"
    rating_only_path = EXPORT_DIR / f"{output_prefix}_rating_only.csv"
    audit_path = EXPORT_DIR / f"{output_prefix}_audit.csv"
    unmatched_path = EXPORT_DIR / f"{output_prefix}_unmatched.csv"

    write_matrix_csv(sheet_path, sheet_headers, sheet_rows)
    write_csv(
        rating_only_path,
        ["NBA_ID", "Season", "Player", "3-Point Shot Rating"],
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
        f"[OK] Built 3-Point Shot export for {len(sheet_rows)} player-season rows "
        f"(aggregate median={three_point_median:.6f}, stdev={three_point_stdev:.6f})"
    )
    print(
        "[INFO] FG3A low-volume gate: "
        f"0 attempts -> -3, {LOW_VOLUME_FG3A_THRESHOLD:.1f} or less -> z-2.5."
    )
    print(f"[OUT] Sheet -> {sheet_path}")
    print(f"[OUT] Rating only -> {rating_only_path}")
    print(f"[OUT] Audit -> {audit_path}")
    print(f"[OUT] Unmatched -> {unmatched_path}")


if __name__ == "__main__":
    main()
