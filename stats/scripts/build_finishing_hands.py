from __future__ import annotations

import argparse
import statistics
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from build_finishing_driving_dunk import interpolate_rating
from build_finishing_standing_dunk import (
    MetricResult,
    PlayerContext,
    average_numeric,
    build_player_contexts,
    compute_metric_result,
    parse_metric_value,
    read_dict_rows,
    standardize_rows,
    write_matrix_csv,
)
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
from pull_nba_stats import EXPORT_DIR, HISTORY_DIR, MANUAL_DIR


TRACKING_TOUCHES_PER_GAME_COLUMNS = (
    "MIN",
    "POINTS",
    "TOUCHES",
    "FRONT_CT_TOUCHES",
    "TIME_OF_POSS",
    "ELBOW_TOUCHES",
    "POST_TOUCHES",
    "PAINT_TOUCHES",
)


def hands_rating(value: float, population: Sequence[float]) -> float:
    minimum = min(population)
    p10 = percentile_inc(population, 0.10)
    p20 = percentile_inc(population, 0.20)
    p30 = percentile_inc(population, 0.30)
    p40 = percentile_inc(population, 0.40)
    p50 = percentile_inc(population, 0.50)
    p60 = percentile_inc(population, 0.60)
    p70 = percentile_inc(population, 0.70)
    p80 = percentile_inc(population, 0.80)
    p88 = percentile_inc(population, 0.88)
    p94 = percentile_inc(population, 0.94)
    p98 = percentile_inc(population, 0.98)
    maximum = max(population)

    if value <= p10:
        return interpolate_rating(value, minimum, p10, 25.0, 62.0)
    if value <= p20:
        return interpolate_rating(value, p10, p20, 62.0, 70.0)
    if value <= p30:
        return interpolate_rating(value, p20, p30, 70.0, 75.0)
    if value <= p40:
        return interpolate_rating(value, p30, p40, 75.0, 78.0)
    if value <= p50:
        return interpolate_rating(value, p40, p50, 78.0, 80.0)
    if value <= p60:
        return interpolate_rating(value, p50, p60, 80.0, 83.0)
    if value <= p70:
        return interpolate_rating(value, p60, p70, 83.0, 85.0)
    if value <= p80:
        return interpolate_rating(value, p70, p80, 85.0, 90.0)
    if value <= p88:
        return interpolate_rating(value, p80, p88, 90.0, 95.0)
    if value <= p94:
        return interpolate_rating(value, p88, p94, 95.0, 98.0)
    if value <= p98:
        return 98.0
    return interpolate_rating(value, p98, maximum, 98.0, 100.0)


def percentile_inc(values: Sequence[float], percentile: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("Cannot compute percentile on an empty sequence.")
    if len(ordered) == 1:
        return ordered[0]
    if percentile <= 0:
        return ordered[0]
    if percentile >= 1:
        return ordered[-1]

    rank = 1 + (len(ordered) - 1) * percentile
    lower_rank = int(rank // 1)
    upper_rank = lower_rank if rank.is_integer() else lower_rank + 1
    lower_value = ordered[lower_rank - 1]
    upper_value = ordered[upper_rank - 1]
    if lower_rank == upper_rank:
        return lower_value
    fraction = rank - lower_rank
    return lower_value + fraction * (upper_value - lower_value)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build the Finishing -> Hands rating export."
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
        default=str(HISTORY_DIR / "bball_index_hands.csv"),
        help="CSV file containing Playmaking Talent and Touches Per 75.",
    )
    parser.add_argument(
        "--touches-source",
        default=str(HISTORY_DIR / "tracking_touches.csv"),
        help="History CSV used for TOUCHES.",
    )
    parser.add_argument(
        "--output-prefix",
        default="finishing_hands",
        help="Prefix used for CSV outputs inside stats/exports.",
    )
    return parser.parse_args()


def detect_total_tracking_touches_seasons(
    rows: Sequence[Dict[str, str]],
) -> List[str]:
    by_season: Dict[str, List[Dict[str, str]]] = {}
    for row in rows:
        season = str(row.get("Season", "")).strip()
        if not season:
            continue
        by_season.setdefault(season, []).append(row)

    total_seasons: List[str] = []
    for season, season_rows in by_season.items():
        min_values = [
            value
            for value in (
                parse_metric_value(row.get("MIN", ""))
                for row in season_rows
            )
            if value is not None
        ]
        touches_values = [
            value
            for value in (
                parse_metric_value(row.get("TOUCHES", ""))
                for row in season_rows
            )
            if value is not None
        ]
        median_min = statistics.median(min_values) if min_values else 0.0
        median_touches = statistics.median(touches_values) if touches_values else 0.0
        if median_min > 100.0 or median_touches > 150.0:
            total_seasons.append(season)

    return sorted(total_seasons)


def normalize_tracking_touches_rows(
    rows: Sequence[Dict[str, str]],
) -> tuple[List[Dict[str, str]], List[str]]:
    total_seasons = set(detect_total_tracking_touches_seasons(rows))
    if not total_seasons:
        return [dict(row) for row in rows], []

    normalized_rows: List[Dict[str, str]] = []
    for row in rows:
        season = str(row.get("Season", "")).strip()
        converted = dict(row)
        if season in total_seasons:
            gp = parse_metric_value(row.get("GP", ""))
            if gp not in (None, 0):
                for column in TRACKING_TOUCHES_PER_GAME_COLUMNS:
                    value = parse_metric_value(row.get(column, ""))
                    if value is None:
                        continue
                    converted[column] = f"{value / gp:.6f}".rstrip("0").rstrip(".")
        normalized_rows.append(converted)

    return normalized_rows, sorted(total_seasons)


def main() -> None:
    args = parse_args()

    workbook_path = Path(args.workbook)
    universe_path = Path(args.universe_csv)
    details_path = resolve_details_csv_path(args.details_csv, universe_path)
    minutes_source_path = Path(args.minutes_source)
    bball_index_source_path = Path(args.bball_index_source)
    touches_source_path = Path(args.touches_source)

    required_paths = [
        minutes_source_path,
        bball_index_source_path,
        touches_source_path,
    ]
    for path in required_paths:
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
    current_season = detect_current_season(args.current_season, universe, minutes_rows)
    contexts = build_player_contexts(
        universe=universe,
        minutes_rows=minutes_rows,
        minutes_column=args.minutes_column,
        minutes_games_column=args.minutes_games_column,
        allow_id_fallback=args.allow_id_fallback,
    )

    bball_index_rows = standardize_rows(
        read_dict_rows(bball_index_source_path),
        season_column="Season",
        player_column="Player",
    )
    normalized_touches_rows, normalized_touch_seasons = normalize_tracking_touches_rows(
        read_history_csv(touches_source_path)
    )
    touches_rows = standardize_rows(
        normalized_touches_rows,
        season_column="Season",
        player_column="PLAYER_NAME",
    )

    bball_index_columns = set(bball_index_rows[0].keys()) if bball_index_rows else set()
    missing_bball_columns = [
        column for column in ["Playmaking Talent", "Touches Per 75"] if column not in bball_index_columns
    ]
    if missing_bball_columns:
        raise SystemExit(
            "Missing columns in the bball-index source: " + ", ".join(missing_bball_columns)
        )

    metric_results: Dict[str, MetricResult] = {
        "Playmaking Talent": compute_metric_result(
            contexts,
            bball_index_rows,
            "Playmaking Talent",
            current_season,
            args.current_season_min_threshold,
            args.standard_min_threshold,
            args.allow_id_fallback,
            apply_penalty=True,
        ),
        "Touches Per 75": compute_metric_result(
            contexts,
            bball_index_rows,
            "Touches Per 75",
            current_season,
            args.current_season_min_threshold,
            args.standard_min_threshold,
            args.allow_id_fallback,
            apply_penalty=True,
        ),
        "TOUCHES": compute_metric_result(
            contexts,
            touches_rows,
            "TOUCHES",
            current_season,
            args.current_season_min_threshold,
            args.standard_min_threshold,
            args.allow_id_fallback,
            apply_penalty=True,
        ),
    }

    component_names = ["Playmaking Talent", "Touches Per 75", "TOUCHES"]
    hands_scores: List[float] = []
    aggregate_rows: List[List[Optional[float]]] = []
    for index in range(len(contexts)):
        component_values = [
            metric_results["Playmaking Talent"].output_values[index],
            metric_results["Touches Per 75"].output_values[index],
            metric_results["TOUCHES"].output_values[index],
        ]
        hands_score = average_numeric(component_values)
        hands_scores.append(-2.0 if hands_score is None else hands_score)
        aggregate_rows.append(component_values)

    hands_median = statistics.median(hands_scores)
    hands_stdev = statistics.stdev(hands_scores)
    aggregate_z_scores = [
        (value - hands_median) / hands_stdev for value in hands_scores
    ]
    hands_ratings = [
        hands_rating(value, aggregate_z_scores)
        for value in aggregate_z_scores
    ]

    workbook_columns = [
        "NBA ID",
        "Season",
        "Player",
        "Hands Rating",
        "",
        "Hands",
        "Playmaking Talent",
        "Touches Per 75",
        "TOUCHES",
    ]

    sheet_rows: List[List[object]] = []
    rating_only_rows: List[Dict[str, object]] = []
    audit_rows: List[Dict[str, object]] = []
    unmatched_rows: List[Dict[str, object]] = []

    for index, context in enumerate(contexts):
        component_values = aggregate_rows[index]
        missing_metrics = [
            name for name, value in zip(component_names, component_values) if value is None
        ]

        sheet_rows.append(
            [
                context.universe_row.nba_id,
                context.universe_row.season,
                context.universe_row.player,
                hands_ratings[index],
                aggregate_z_scores[index],
                hands_scores[index],
                *component_values,
            ]
        )
        rating_only_rows.append(
            {
                "NBA_ID": context.universe_row.nba_id,
                "Season": context.universe_row.season,
                "Player": context.universe_row.player,
                "Hands Rating": hands_ratings[index],
            }
        )

        audit_row: Dict[str, object] = {
            "NBA_ID": context.universe_row.nba_id,
            "Season": context.universe_row.season,
            "Player": context.universe_row.player,
            "RotationRole": context.universe_row.rotation_role,
            "MIN": context.effective_minutes,
            "Hands": hands_scores[index],
            "Hands Aggregate Z": aggregate_z_scores[index],
            "Hands Rating": hands_ratings[index],
            "MissingMetricCount": len(missing_metrics),
            "MissingMetrics": " | ".join(missing_metrics),
        }
        for metric_name, result in metric_results.items():
            audit_row[f"{metric_name} Raw"] = result.raw_values[index]
            audit_row[f"{metric_name} Z"] = result.output_values[index]
            audit_row[f"{metric_name} MatchedBy"] = result.matched_by[index]
        audit_rows.append(audit_row)

        if missing_metrics:
            unmatched_rows.append(
                {
                    "NBA_ID": context.universe_row.nba_id,
                    "Season": context.universe_row.season,
                    "Player": context.universe_row.player,
                    "RotationRole": context.universe_row.rotation_role,
                    "MIN": context.effective_minutes,
                    "MissingCount": len(missing_metrics),
                    "MissingMetrics": " | ".join(missing_metrics),
                }
            )

    output_prefix = args.output_prefix.strip() or "finishing_hands"
    sheet_path = EXPORT_DIR / f"{output_prefix}_sheet.csv"
    rating_only_path = EXPORT_DIR / f"{output_prefix}_rating_only.csv"
    audit_path = EXPORT_DIR / f"{output_prefix}_audit.csv"
    unmatched_path = EXPORT_DIR / f"{output_prefix}_unmatched.csv"

    write_matrix_csv(sheet_path, workbook_columns, sheet_rows)
    write_csv(
        rating_only_path,
        ["NBA_ID", "Season", "Player", "Hands Rating"],
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

    print(f"[OK] Built Hands export for {len(sheet_rows)} player-season rows")
    if normalized_touch_seasons:
        print(
            "[INFO] tracking_touches.csv seasons auto-normalized from totals to per-game: "
            + ", ".join(normalized_touch_seasons)
        )
    print(f"[OUT] Sheet -> {sheet_path}")
    print(f"[OUT] Rating only -> {rating_only_path}")
    print(f"[OUT] Audit -> {audit_path}")
    print(f"[OUT] Unmatched -> {unmatched_path}")


if __name__ == "__main__":
    main()
