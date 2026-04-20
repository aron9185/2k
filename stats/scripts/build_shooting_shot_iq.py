from __future__ import annotations

import argparse
import statistics
from pathlib import Path
from typing import Dict, List, Optional, Sequence

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
from pull_nba_stats import EXPORT_DIR, HISTORY_DIR, MANUAL_DIR


SHOT_IQ_METRICS: Sequence[MetricSpec] = (
    MetricSpec("Points Over Expectation / 75", "bball", "Points Over Expectation / 75"),
    MetricSpec("Stable eFG%", "bball", "Stable eFG%"),
    MetricSpec("Overall Shot Making Efficiency", "bball", "Overall Shot Making Efficiency"),
    MetricSpec("Overall Shot Making", "bball", "Overall Shot Making"),
    MetricSpec("Self-Created Shot Making Efficiency", "bball", "Self-Created Shot Making Efficiency"),
    MetricSpec("Overall Shot Creation", "bball", "Overall Shot Creation"),
    MetricSpec("EFG_PCT", "advanced", "EFG_PCT"),
    MetricSpec("TS_PCT", "advanced", "TS_PCT"),
    MetricSpec("Role Adjusted Stable eFG%", "bball", "Role Adjusted Stable eFG%"),
    MetricSpec("Points Per Possession", "per1poss", "PTS"),
)

SHOT_IQ_COMPONENTS: Sequence[ComponentSlot] = (
    ComponentSlot("Points Over Expectation / 75", "Points Over Expectation / 75"),
    ComponentSlot("Stable eFG%", "Stable eFG%"),
    ComponentSlot("Overall Shot Making Efficiency", "Overall Shot Making Efficiency"),
    ComponentSlot("Overall Shot Making", "Overall Shot Making"),
    ComponentSlot("Self-Created Shot Making Efficiency", "Self-Created Shot Making Efficiency"),
    ComponentSlot("Overall Shot Creation", "Overall Shot Creation"),
    ComponentSlot("EFG_PCT", "EFG_PCT"),
    ComponentSlot("TS_PCT", "TS_PCT"),
    ComponentSlot("Role Adjusted Stable eFG%", "Role Adjusted Stable eFG%"),
    ComponentSlot("Points Per Possession", "Points Per Possession"),
)

PRIMARY_COMPONENT_COUNT = 5


def shot_iq_rating(value: float, population: Sequence[float]) -> float:
    minimum = min(population)
    p30 = percentile_inc(population, 0.30)
    p50 = percentile_inc(population, 0.50)
    p60 = percentile_inc(population, 0.60)
    p70 = percentile_inc(population, 0.70)
    p78 = percentile_inc(population, 0.78)
    p86 = percentile_inc(population, 0.86)
    p93 = percentile_inc(population, 0.93)
    p97 = percentile_inc(population, 0.97)
    maximum = percentile_inc(population, 1.0)

    if value <= p30:
        return interpolate_rating(value, minimum, p30, 25.0, 65.0)
    if value <= p50:
        return interpolate_rating(value, p30, p50, 65.0, 75.0)
    if value <= p60:
        return interpolate_rating(value, p50, p60, 75.0, 80.0)
    if value <= p70:
        return interpolate_rating(value, p60, p70, 80.0, 85.0)
    if value <= p78:
        return interpolate_rating(value, p70, p78, 85.0, 90.0)
    if value <= p86:
        return interpolate_rating(value, p78, p86, 90.0, 96.0)
    if value <= p93:
        return interpolate_rating(value, p86, p93, 96.0, 98.0)
    if value <= p97:
        return interpolate_rating(value, p93, p97, 98.0, 99.0)
    return interpolate_rating(value, p97, maximum, 99.0, 100.0)


def average_numeric(values: Sequence[Optional[float]]) -> Optional[float]:
    numeric = [value for value in values if value is not None]
    if not numeric:
        return None
    return statistics.mean(numeric)


def blank_flat_zero_seasons(
    rows: Sequence[Dict[str, str]],
    metric_column: str,
) -> List[Dict[str, str]]:
    season_values: Dict[str, List[float]] = {}
    for row in rows:
        season = str(row.get("Season", "")).strip()
        if not season:
            continue
        raw_value = str(row.get(metric_column, "")).strip()
        if not raw_value:
            continue
        try:
            value = float(raw_value)
        except Exception:
            continue
        season_values.setdefault(season, []).append(value)

    bad_seasons = {
        season
        for season, values in season_values.items()
        if values and len(set(values)) == 1 and values[0] == 0.0
    }
    if not bad_seasons:
        return [dict(row) for row in rows]

    sanitized_rows: List[Dict[str, str]] = []
    for row in rows:
        updated = dict(row)
        season = str(updated.get("Season", "")).strip()
        if season in bad_seasons:
            updated[metric_column] = ""
        sanitized_rows.append(updated)
    return sanitized_rows


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build the Shooting -> Shot IQ rating export."
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
        default=str(HISTORY_DIR / "bball_index_shot_iq.csv"),
        help="CSV file containing the Shot IQ bball-index metric pack.",
    )
    parser.add_argument(
        "--advanced-source",
        default=str(HISTORY_DIR / "general_advanced.csv"),
        help="History CSV used for EFG_PCT and TS_PCT.",
    )
    parser.add_argument(
        "--per1poss-source",
        default=str(HISTORY_DIR / "general_per1poss.csv"),
        help="History CSV used for the Points Per Possession support metric.",
    )
    parser.add_argument(
        "--output-prefix",
        default="shooting_shot_iq",
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
    advanced_source_path = Path(args.advanced_source)
    per1poss_source_path = Path(args.per1poss_source)

    for path in (
        minutes_source_path,
        bball_index_source_path,
        advanced_source_path,
        per1poss_source_path,
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
        "bball": blank_flat_zero_seasons(
            standardize_bball_rows(read_history_csv(bball_index_source_path)),
            metric_column="Points Over Expectation / 75",
        ),
        "advanced": read_history_csv(advanced_source_path),
        "per1poss": read_history_csv(per1poss_source_path),
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
    for metric in SHOT_IQ_METRICS:
        metric_results[metric.alias] = compute_metric_result(
            contexts=contexts,
            rows=source_rows_by_key[metric.source_key],
            metric=metric,
            current_season=current_season,
            current_season_min_threshold=args.current_season_min_threshold,
            standard_min_threshold=args.standard_min_threshold,
            allow_id_fallback=args.allow_id_fallback,
        )

    shot_iq_scores: List[float] = []
    component_rows: List[List[Optional[float]]] = []
    for row_index in range(len(contexts)):
        component_values = [
            metric_results[component.metric_alias].normalized_values[row_index]
            for component in SHOT_IQ_COMPONENTS
        ]
        component_rows.append(component_values)

        primary_group = component_values[:PRIMARY_COMPONENT_COUNT]
        overall_shot_creation = component_values[PRIMARY_COMPONENT_COUNT]
        support_group = component_values[PRIMARY_COMPONENT_COUNT + 1 :]

        primary_average = average_numeric(primary_group)
        support_average = average_numeric(support_group) or 0.0
        shot_iq_score = (
            -1.0
            if primary_average is None
            else primary_average * 0.85 + (overall_shot_creation or 0.0) * 0.1 + support_average * 0.5
        )
        shot_iq_scores.append(shot_iq_score)

    if len(shot_iq_scores) < 2:
        raise SystemExit("Not enough Shot IQ scores to compute aggregate z-scores.")

    shot_iq_median = statistics.median(shot_iq_scores)
    shot_iq_stdev = statistics.stdev(shot_iq_scores)
    if shot_iq_stdev == 0:
        raise SystemExit("Shot IQ scores have zero variance; cannot compute aggregate z-scores.")

    aggregate_z_scores = [
        (value - shot_iq_median) / shot_iq_stdev for value in shot_iq_scores
    ]
    shot_iq_ratings = [
        shot_iq_rating(value, aggregate_z_scores)
        for value in aggregate_z_scores
    ]

    sheet_headers = [
        "NBA ID",
        "Season",
        "Player",
        "Shot IQ Rating",
        "",
        "Shot IQ",
        *[component.header for component in SHOT_IQ_COMPONENTS],
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
            for metric in SHOT_IQ_METRICS
            if metric_results[metric.alias].raw_values[index] is None
        ]

        sheet_rows.append(
            [
                context.universe_row.nba_id,
                season,
                player,
                shot_iq_ratings[index],
                aggregate_z_scores[index],
                shot_iq_scores[index],
                *component_values,
            ]
        )
        rating_only_rows.append(
            {
                "NBA_ID": context.universe_row.nba_id,
                "Season": season,
                "Player": player,
                "Shot IQ Rating": shot_iq_ratings[index],
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
            "Shot IQ": shot_iq_scores[index],
            "ShotIQAggregateZ": aggregate_z_scores[index],
            "ShotIQRating": shot_iq_ratings[index],
            "MissingMetricCount": len(missing_metrics),
            "MissingMetrics": " | ".join(missing_metrics),
        }
        for metric in SHOT_IQ_METRICS:
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

    output_prefix = args.output_prefix.strip() or "shooting_shot_iq"
    sheet_path = EXPORT_DIR / f"{output_prefix}_sheet.csv"
    rating_only_path = EXPORT_DIR / f"{output_prefix}_rating_only.csv"
    audit_path = EXPORT_DIR / f"{output_prefix}_audit.csv"
    unmatched_path = EXPORT_DIR / f"{output_prefix}_unmatched.csv"

    write_matrix_csv(sheet_path, sheet_headers, sheet_rows)
    write_csv(
        rating_only_path,
        ["NBA_ID", "Season", "Player", "Shot IQ Rating"],
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
        f"[OK] Built Shot IQ export for {len(sheet_rows)} player-season rows "
        f"(aggregate median={shot_iq_median:.6f}, stdev={shot_iq_stdev:.6f})"
    )
    print(
        "[INFO] Workbook weights kept: avg(BU:BY)*0.85 + BZ*0.10 + avg(CA:CD)*0.50."
    )
    print(f"[OUT] Sheet -> {sheet_path}")
    print(f"[OUT] Rating only -> {rating_only_path}")
    print(f"[OUT] Audit -> {audit_path}")
    print(f"[OUT] Unmatched -> {unmatched_path}")


if __name__ == "__main__":
    main()
