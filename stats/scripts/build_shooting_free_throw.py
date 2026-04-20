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


FREE_THROW_METRICS: Sequence[MetricSpec] = (
    MetricSpec("FT_PCT", "box", "FT_PCT"),
    MetricSpec("Stable FT%", "bball", "Stable FT%"),
    MetricSpec("FTM", "box", "FTM"),
)

FREE_THROW_COMPONENTS: Sequence[ComponentSlot] = (
    ComponentSlot("FT_PCT", "FT_PCT"),
    ComponentSlot("Stable FT%", "Stable FT%"),
    ComponentSlot("FTM", "FTM"),
)


def free_throw_rating(value: float, population: Sequence[float]) -> float:
    minimum = min(population)
    p30 = percentile_inc(population, 0.30)
    p50 = percentile_inc(population, 0.50)
    p65 = percentile_inc(population, 0.65)
    p75 = percentile_inc(population, 0.75)
    p83 = percentile_inc(population, 0.83)
    p90 = percentile_inc(population, 0.90)
    p97 = percentile_inc(population, 0.97)
    maximum = percentile_inc(population, 1.0)

    if value <= p30:
        return interpolate_rating(value, minimum, p30, 25.0, 71.0)
    if value <= p50:
        return interpolate_rating(value, p30, p50, 71.0, 77.0)
    if value <= p65:
        return interpolate_rating(value, p50, p65, 77.0, 80.0)
    if value <= p75:
        return interpolate_rating(value, p65, p75, 80.0, 83.0)
    if value <= p83:
        return interpolate_rating(value, p75, p83, 83.0, 85.0)
    if value <= p90:
        return interpolate_rating(value, p83, p90, 85.0, 87.0)
    if value <= p97:
        return interpolate_rating(value, p90, p97, 87.0, 89.0)
    return interpolate_rating(value, p97, maximum, 89.0, 100.0)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build the Shooting -> Free Throw rating export."
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
        default=str(HISTORY_DIR / "bball_index_free_throw.csv"),
        help="CSV file containing the free-throw bball-index metric pack.",
    )
    parser.add_argument(
        "--box-score-source",
        default=str(HISTORY_DIR / "general_traditional.csv"),
        help="History CSV used for FT_PCT and FTM.",
    )
    parser.add_argument(
        "--output-prefix",
        default="shooting_free_throw",
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

    metric_results: Dict[str, MetricResult] = {}
    for metric in FREE_THROW_METRICS:
        metric_results[metric.alias] = compute_metric_result(
            contexts=contexts,
            rows=source_rows_by_key[metric.source_key],
            metric=metric,
            current_season=current_season,
            current_season_min_threshold=args.current_season_min_threshold,
            standard_min_threshold=args.standard_min_threshold,
            allow_id_fallback=args.allow_id_fallback,
        )

    free_throw_scores: List[float] = []
    component_rows: List[List[Optional[float]]] = []
    for row_index in range(len(contexts)):
        ft_pct_value = metric_results["FT_PCT"].normalized_values[row_index]
        stable_ft_value = metric_results["Stable FT%"].normalized_values[row_index]
        ftm_value = metric_results["FTM"].normalized_values[row_index]

        component_values = [ft_pct_value, stable_ft_value, ftm_value]
        component_rows.append(component_values)

        free_throw_score = (
            -1.0
            if ft_pct_value is None
            else ft_pct_value * 0.5 + (stable_ft_value or 0.0) * 0.45 + (ftm_value or 0.0) * 0.05
        )
        free_throw_scores.append(free_throw_score)

    if len(free_throw_scores) < 2:
        raise SystemExit("Not enough free-throw scores to compute aggregate z-scores.")

    free_throw_median = statistics.median(free_throw_scores)
    free_throw_stdev = statistics.stdev(free_throw_scores)
    if free_throw_stdev == 0:
        raise SystemExit("Free-throw scores have zero variance; cannot compute aggregate z-scores.")

    aggregate_z_scores = [
        (value - free_throw_median) / free_throw_stdev for value in free_throw_scores
    ]
    free_throw_ratings = [
        free_throw_rating(value, aggregate_z_scores)
        for value in aggregate_z_scores
    ]

    sheet_headers = [
        "NBA ID",
        "Season",
        "Player",
        "Free Throw Rating",
        "",
        "Free Throw",
        *[component.header for component in FREE_THROW_COMPONENTS],
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
            for metric in FREE_THROW_METRICS
            if metric_results[metric.alias].raw_values[index] is None
        ]

        sheet_rows.append(
            [
                context.universe_row.nba_id,
                season,
                player,
                free_throw_ratings[index],
                aggregate_z_scores[index],
                free_throw_scores[index],
                *component_values,
            ]
        )
        rating_only_rows.append(
            {
                "NBA_ID": context.universe_row.nba_id,
                "Season": season,
                "Player": player,
                "Free Throw Rating": free_throw_ratings[index],
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
            "Free Throw": free_throw_scores[index],
            "FreeThrowAggregateZ": aggregate_z_scores[index],
            "FreeThrowRating": free_throw_ratings[index],
            "MissingMetricCount": len(missing_metrics),
            "MissingMetrics": " | ".join(missing_metrics),
        }
        for metric in FREE_THROW_METRICS:
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

    output_prefix = args.output_prefix.strip() or "shooting_free_throw"
    sheet_path = EXPORT_DIR / f"{output_prefix}_sheet.csv"
    rating_only_path = EXPORT_DIR / f"{output_prefix}_rating_only.csv"
    audit_path = EXPORT_DIR / f"{output_prefix}_audit.csv"
    unmatched_path = EXPORT_DIR / f"{output_prefix}_unmatched.csv"

    write_matrix_csv(sheet_path, sheet_headers, sheet_rows)
    write_csv(
        rating_only_path,
        ["NBA_ID", "Season", "Player", "Free Throw Rating"],
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
        f"[OK] Built Free Throw export for {len(sheet_rows)} player-season rows "
        f"(aggregate median={free_throw_median:.6f}, stdev={free_throw_stdev:.6f})"
    )
    print("[INFO] Workbook weights kept: FT_PCT 0.50, Stable FT% 0.45, FTM 0.05.")
    print(f"[OUT] Sheet -> {sheet_path}")
    print(f"[OUT] Rating only -> {rating_only_path}")
    print(f"[OUT] Audit -> {audit_path}")
    print(f"[OUT] Unmatched -> {unmatched_path}")


if __name__ == "__main__":
    main()
