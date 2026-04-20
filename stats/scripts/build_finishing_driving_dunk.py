from __future__ import annotations

import argparse
import math
import statistics
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from build_finishing_standing_dunk import (
    MetricResult,
    PlayerContext,
    average_numeric,
    build_nonzero_season_coverage,
    build_player_contexts,
    compute_metric_result,
    parse_metric_value,
    percentile_inc,
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


WORKBOOK_COLUMNS = [
    "NBA ID",
    "Season",
    "Player",
    "Driving Dunk Rating",
    "",
    "Driving Dunk",
    "Drving Dunk_PG",
    "Drving Dunk_FGM",
    "Rim Shot Creation",
    "dunkScore_max",
    "dunkScore_total",
    "dunkScore_average",
    "jumpSubscore_average",
    "powerSubscore_average",
    "styleSubscore_average",
    "hangTime_average",
    "takeoffDistance_average",
    "DRIVES",
    "DRIVE_FGM",
]

DRIVING_DUNK_SHOT_COLUMNS = [
    "Driving Dunk Shot_FGM",
    "Driving Slam Dunk Shot_FGM",
    "Running Dunk Shot_FGM",
    "Running Slam Dunk Shot_FGM",
    "Cutting Dunk Shot_FGM",
    "Driving Reverse Dunk Shot_FGM",
    "Running Alley Oop Dunk Shot_FGM",
    "Running Reverse Dunk Shot_FGM",
]


def interpolate_rating(
    value: float,
    low_x: float,
    high_x: float,
    low_y: float,
    high_y: float,
) -> float:
    if high_x <= low_x:
        return high_y
    return low_y + (value - low_x) * (high_y - low_y) / (high_x - low_x)


def driving_dunk_rating(value: float, population: Sequence[float]) -> float:
    minimum = min(population)
    p15 = percentile_inc(population, 0.15)
    p30 = percentile_inc(population, 0.30)
    p50 = percentile_inc(population, 0.50)
    p70 = percentile_inc(population, 0.70)
    p85 = percentile_inc(population, 0.85)
    p95 = percentile_inc(population, 0.95)
    maximum = percentile_inc(population, 1.0)

    if value <= p15:
        return interpolate_rating(value, minimum, p15, 25.0, 41.0)
    if value <= p30:
        return interpolate_rating(value, p15, p30, 41.0, 55.0)
    if value <= p50:
        return interpolate_rating(value, p30, p50, 55.0, 65.0)
    if value <= p70:
        return interpolate_rating(value, p50, p70, 65.0, 75.0)
    if value <= p85:
        return interpolate_rating(value, p70, p85, 75.0, 80.0)
    if value <= p95:
        return interpolate_rating(value, p85, p95, 80.0, 90.0)
    return interpolate_rating(value, p95, maximum, 90.0, 100.0)


def build_driving_dunk_sources(
    shot_rows: Sequence[Dict[str, str]],
    gp_rows: Sequence[Dict[str, str]],
) -> Tuple[List[Dict[str, str]], List[Dict[str, str]], List[str]]:
    if not shot_rows:
        return [], [], []

    gp_lookup = {
        (str(row.get("Season", "")).strip(), str(row.get("PLAYER_NAME", "")).strip()): row
        for row in gp_rows
    }

    driving_fgm_rows: List[Dict[str, str]] = []
    driving_pg_rows: List[Dict[str, str]] = []

    for row in shot_rows:
        season = str(row.get("Season", "")).strip()
        player = str(row.get("PLAYER_NAME", "")).strip()
        if not season or not player:
            continue

        total_fgm = 0.0
        for column in DRIVING_DUNK_SHOT_COLUMNS:
            total_fgm += parse_metric_value(row.get(column, "")) or 0.0

        gp_row = gp_lookup.get((season, player))
        gp = parse_metric_value(gp_row.get("GP", "")) if gp_row is not None else None

        driving_fgm_rows.append(
            {
                "Season": season,
                "PLAYER_NAME": player,
                "Drving Dunk_FGM": total_fgm,
            }
        )
        driving_pg_rows.append(
            {
                "Season": season,
                "PLAYER_NAME": player,
                "Drving Dunk_PG": (total_fgm / gp) if gp not in (None, 0) else "",
            }
        )

    return driving_pg_rows, driving_fgm_rows, DRIVING_DUNK_SHOT_COLUMNS[:]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build the Finishing -> Driving Dunk rating export."
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
        "--shot-source",
        default=str(HISTORY_DIR / "shooting_splits.csv"),
        help="History CSV used to derive driving dunk counts.",
    )
    parser.add_argument(
        "--overall-dunk-stats-source",
        default=str(HISTORY_DIR / "overall_dunk_stats.csv"),
        help="Combined overall dunk score stats CSV.",
    )
    parser.add_argument(
        "--bball-index-source",
        default=str(HISTORY_DIR / "bball_index_layup.csv"),
        help="CSV file containing Rim Shot Creation.",
    )
    parser.add_argument(
        "--drives-source",
        default=str(HISTORY_DIR / "tracking_drives.csv"),
        help="History CSV used for DRIVES and DRIVE_FGM.",
    )
    parser.add_argument(
        "--output-prefix",
        default="finishing_driving_dunk",
        help="Prefix used for CSV outputs inside stats/exports.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    workbook_path = Path(args.workbook)
    universe_path = Path(args.universe_csv)
    details_path = resolve_details_csv_path(args.details_csv, universe_path)
    minutes_source_path = Path(args.minutes_source)
    shot_source_path = Path(args.shot_source)
    overall_dunk_stats_source_path = Path(args.overall_dunk_stats_source)
    bball_index_source_path = Path(args.bball_index_source)
    drives_source_path = Path(args.drives_source)

    required_paths = [
        minutes_source_path,
        shot_source_path,
        overall_dunk_stats_source_path,
        drives_source_path,
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

    gp_rows = standardize_rows(minutes_rows, season_column="Season", player_column="PLAYER_NAME")
    shot_rows = standardize_rows(
        read_dict_rows(shot_source_path),
        season_column="Season",
        player_column="Name",
    )
    driving_pg_rows, driving_fgm_rows, driving_columns = build_driving_dunk_sources(
        shot_rows,
        gp_rows,
    )

    overall_dunk_stats_rows = standardize_rows(
        read_dict_rows(overall_dunk_stats_source_path),
        season_column="Season",
        player_column="playerName",
    )

    bball_index_rows: List[Dict[str, str]] = []
    if bball_index_source_path.exists():
        bball_index_rows = standardize_rows(
            read_dict_rows(bball_index_source_path),
            season_column="Season",
            player_column="Player",
        )

    drives_rows = standardize_rows(
        read_history_csv(drives_source_path),
        season_column="Season",
        player_column="PLAYER_NAME",
    )

    metric_results = {
        "Drving Dunk_PG": compute_metric_result(
            contexts,
            driving_pg_rows,
            "Drving Dunk_PG",
            current_season,
            args.current_season_min_threshold,
            args.standard_min_threshold,
            args.allow_id_fallback,
            apply_penalty=True,
        ),
        "Drving Dunk_FGM": compute_metric_result(
            contexts,
            driving_fgm_rows,
            "Drving Dunk_FGM",
            current_season,
            args.current_season_min_threshold,
            args.standard_min_threshold,
            args.allow_id_fallback,
            apply_penalty=True,
            default_zero_when_missing=True,
            default_zero_nonzero_seasons=build_nonzero_season_coverage(
                driving_fgm_rows,
                "Drving Dunk_FGM",
            ),
        ),
        "Rim Shot Creation": compute_metric_result(
            contexts,
            bball_index_rows,
            "Rim Shot Creation",
            current_season,
            args.current_season_min_threshold,
            args.standard_min_threshold,
            args.allow_id_fallback,
            apply_penalty=True,
        ),
        "dunkScore_max": compute_metric_result(
            contexts,
            overall_dunk_stats_rows,
            "dunkScore_max",
            current_season,
            args.current_season_min_threshold,
            args.standard_min_threshold,
            args.allow_id_fallback,
            apply_penalty=True,
        ),
        "dunkScore_total": compute_metric_result(
            contexts,
            overall_dunk_stats_rows,
            "dunkScore_total",
            current_season,
            args.current_season_min_threshold,
            args.standard_min_threshold,
            args.allow_id_fallback,
            apply_penalty=True,
        ),
        "dunkScore_average": compute_metric_result(
            contexts,
            overall_dunk_stats_rows,
            "dunkScore_average",
            current_season,
            args.current_season_min_threshold,
            args.standard_min_threshold,
            args.allow_id_fallback,
            apply_penalty=True,
        ),
        "jumpSubscore_average": compute_metric_result(
            contexts,
            overall_dunk_stats_rows,
            "jumpSubscore_average",
            current_season,
            args.current_season_min_threshold,
            args.standard_min_threshold,
            args.allow_id_fallback,
            apply_penalty=True,
        ),
        "powerSubscore_average": compute_metric_result(
            contexts,
            overall_dunk_stats_rows,
            "powerSubscore_average",
            current_season,
            args.current_season_min_threshold,
            args.standard_min_threshold,
            args.allow_id_fallback,
            apply_penalty=True,
        ),
        "styleSubscore_average": compute_metric_result(
            contexts,
            overall_dunk_stats_rows,
            "styleSubscore_average",
            current_season,
            args.current_season_min_threshold,
            args.standard_min_threshold,
            args.allow_id_fallback,
            apply_penalty=True,
        ),
        "hangTime_average": compute_metric_result(
            contexts,
            overall_dunk_stats_rows,
            "hangTime_average",
            current_season,
            args.current_season_min_threshold,
            args.standard_min_threshold,
            args.allow_id_fallback,
            apply_penalty=True,
        ),
        "takeoffDistance_average": compute_metric_result(
            contexts,
            overall_dunk_stats_rows,
            "takeoffDistance_average",
            current_season,
            args.current_season_min_threshold,
            args.standard_min_threshold,
            args.allow_id_fallback,
            apply_penalty=True,
        ),
        "DRIVES": compute_metric_result(
            contexts,
            drives_rows,
            "DRIVES",
            current_season,
            args.current_season_min_threshold,
            args.standard_min_threshold,
            args.allow_id_fallback,
            apply_penalty=True,
        ),
        "DRIVE_FGM": compute_metric_result(
            contexts,
            drives_rows,
            "DRIVE_FGM",
            current_season,
            args.current_season_min_threshold,
            args.standard_min_threshold,
            args.allow_id_fallback,
            apply_penalty=True,
        ),
    }

    driving_scores: List[float] = []
    aggregate_rows: List[List[Optional[float]]] = []
    for index in range(len(contexts)):
        aq_value = metric_results["Drving Dunk_PG"].output_values[index]
        group_two = [
            metric_results["Drving Dunk_FGM"].output_values[index],
            metric_results["Rim Shot Creation"].output_values[index],
            metric_results["dunkScore_max"].output_values[index],
            metric_results["dunkScore_total"].output_values[index],
        ]
        group_three = [
            metric_results["dunkScore_average"].output_values[index],
            metric_results["jumpSubscore_average"].output_values[index],
            metric_results["powerSubscore_average"].output_values[index],
            metric_results["styleSubscore_average"].output_values[index],
            metric_results["hangTime_average"].output_values[index],
            metric_results["takeoffDistance_average"].output_values[index],
            metric_results["DRIVES"].output_values[index],
            metric_results["DRIVE_FGM"].output_values[index],
        ]

        group_two_average = average_numeric(group_two)
        group_three_average = average_numeric(group_three)
        if group_two_average is None or group_three_average is None:
            driving_score = -1.0
        else:
            driving_score = (
                (aq_value or 0.0) * 0.4
                + group_two_average * 0.45
                + group_three_average * 0.15
            )
        driving_scores.append(driving_score)
        aggregate_rows.append(
            [
                aq_value,
                *group_two,
                *group_three,
            ]
        )

    driving_median = statistics.median(driving_scores)
    driving_stdev = statistics.stdev(driving_scores)
    aggregate_z_scores = [
        (value - driving_median) / driving_stdev for value in driving_scores
    ]
    driving_ratings = [
        driving_dunk_rating(value, aggregate_z_scores)
        for value in aggregate_z_scores
    ]

    sheet_rows: List[List[object]] = []
    rating_only_rows: List[Dict[str, object]] = []
    audit_rows: List[Dict[str, object]] = []
    unmatched_rows: List[Dict[str, object]] = []

    component_names = [
        "Drving Dunk_PG",
        "Drving Dunk_FGM",
        "Rim Shot Creation",
        "dunkScore_max",
        "dunkScore_total",
        "dunkScore_average",
        "jumpSubscore_average",
        "powerSubscore_average",
        "styleSubscore_average",
        "hangTime_average",
        "takeoffDistance_average",
        "DRIVES",
        "DRIVE_FGM",
    ]

    for index, context in enumerate(contexts):
        component_values = aggregate_rows[index]
        missing_metrics = [
            name
            for name, value in zip(component_names, component_values)
            if value is None
        ]

        sheet_rows.append(
            [
                context.universe_row.nba_id,
                context.universe_row.season,
                context.universe_row.player,
                driving_ratings[index],
                aggregate_z_scores[index],
                driving_scores[index],
                *component_values,
            ]
        )
        rating_only_rows.append(
            {
                "NBA_ID": context.universe_row.nba_id,
                "Season": context.universe_row.season,
                "Player": context.universe_row.player,
                "Driving Dunk Rating": driving_ratings[index],
            }
        )

        audit_row: Dict[str, object] = {
            "NBA_ID": context.universe_row.nba_id,
            "Season": context.universe_row.season,
            "Player": context.universe_row.player,
            "RotationRole": context.universe_row.rotation_role,
            "MIN": context.effective_minutes,
            "Driving Dunk": driving_scores[index],
            "Driving Dunk Aggregate Z": aggregate_z_scores[index],
            "Driving Dunk Rating": driving_ratings[index],
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

    output_prefix = args.output_prefix.strip() or "finishing_driving_dunk"
    sheet_path = EXPORT_DIR / f"{output_prefix}_sheet.csv"
    rating_only_path = EXPORT_DIR / f"{output_prefix}_rating_only.csv"
    audit_path = EXPORT_DIR / f"{output_prefix}_audit.csv"
    unmatched_path = EXPORT_DIR / f"{output_prefix}_unmatched.csv"

    write_matrix_csv(sheet_path, WORKBOOK_COLUMNS, sheet_rows)
    write_csv(
        rating_only_path,
        ["NBA_ID", "Season", "Player", "Driving Dunk Rating"],
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

    print(f"[OK] Built Driving Dunk export for {len(sheet_rows)} player-season rows")
    print(f"[INFO] Driving dunk shot columns used: {', '.join(driving_columns)}")
    print(f"[OUT] Sheet -> {sheet_path}")
    print(f"[OUT] Rating only -> {rating_only_path}")
    print(f"[OUT] Audit -> {audit_path}")
    print(f"[OUT] Unmatched -> {unmatched_path}")


if __name__ == "__main__":
    main()
