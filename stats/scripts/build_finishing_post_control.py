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


def post_control_rating(value: float, population: Sequence[float]) -> float:
    minimum = min(population)
    p10 = percentile_inc(population, 0.10)
    p20 = percentile_inc(population, 0.20)
    p30 = percentile_inc(population, 0.30)
    p40 = percentile_inc(population, 0.40)
    p50 = percentile_inc(population, 0.50)
    p60 = percentile_inc(population, 0.60)
    p70 = percentile_inc(population, 0.70)
    p80 = percentile_inc(population, 0.80)
    p90 = percentile_inc(population, 0.90)
    p95 = percentile_inc(population, 0.95)
    maximum = percentile_inc(population, 1.0)

    if value <= p10:
        return interpolate_rating(value, minimum, p10, 25.0, 29.0)
    if value <= p20:
        return interpolate_rating(value, p10, p20, 29.0, 36.0)
    if value <= p30:
        return interpolate_rating(value, p20, p30, 36.0, 43.0)
    if value <= p40:
        return interpolate_rating(value, p30, p40, 43.0, 49.0)
    if value <= p50:
        return interpolate_rating(value, p40, p50, 49.0, 55.0)
    if value <= p60:
        return interpolate_rating(value, p50, p60, 55.0, 60.0)
    if value <= p70:
        return interpolate_rating(value, p60, p70, 60.0, 66.0)
    if value <= p80:
        return interpolate_rating(value, p70, p80, 66.0, 71.0)
    if value <= p90:
        return interpolate_rating(value, p80, p90, 71.0, 81.0)
    if value <= p95:
        return interpolate_rating(value, p90, p95, 81.0, 88.0)
    return interpolate_rating(value, p95, maximum, 88.0, 100.0)


def build_postup_frequency_rows(
    tracking_rows: Sequence[Dict[str, str]],
) -> List[Dict[str, str]]:
    derived_rows: List[Dict[str, str]] = []
    for row in tracking_rows:
        season = str(row.get("Season", "")).strip()
        player = str(row.get("PLAYER_NAME", "")).strip()
        if not season or not player:
            continue

        post_touches = parse_metric_value(row.get("POST_TOUCHES", ""))
        touches = parse_metric_value(row.get("TOUCHES", ""))
        if post_touches is None or touches in (None, 0):
            value: object = ""
        else:
            value = (post_touches / touches) * 100.0

        derived_rows.append(
            {
                "Season": season,
                "PLAYER_NAME": player,
                "Post Up Frequency%": value,
            }
        )
    return derived_rows


def build_postup_draw_foul_rows(
    tracking_rows: Sequence[Dict[str, str]],
) -> List[Dict[str, str]]:
    derived_rows: List[Dict[str, str]] = []
    for row in tracking_rows:
        season = str(row.get("Season", "")).strip()
        player = str(row.get("PLAYER_NAME", "")).strip()
        if not season or not player:
            continue

        post_touches = parse_metric_value(row.get("POST_TOUCHES", ""))
        post_fouls = parse_metric_value(row.get("POST_TOUCH_FOULS", ""))
        if post_touches is None:
            value: object = ""
        elif post_touches == 0:
            value = 0.0
        elif post_fouls is None:
            value = ""
        else:
            value = (post_fouls / post_touches) * 100.0

        derived_rows.append(
            {
                "Season": season,
                "PLAYER_NAME": player,
                "Post Up Draw Foul Rate": value,
            }
        )
    return derived_rows


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build the Finishing -> Post Control rating export."
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
        default=str(HISTORY_DIR / "bball_index_postup_full.csv"),
        help="CSV file containing the post-up support metrics.",
    )
    parser.add_argument(
        "--postup-tracking-source",
        default=str(HISTORY_DIR / "tracking_postup.csv"),
        help="History CSV used for post touch metrics and derived rate proxies.",
    )
    parser.add_argument(
        "--touches-source",
        default=str(HISTORY_DIR / "tracking_touches.csv"),
        help="History CSV used for PTS_PER_POST_TOUCH.",
    )
    parser.add_argument(
        "--output-prefix",
        default="finishing_post_control",
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
    postup_tracking_source_path = Path(args.postup_tracking_source)
    touches_source_path = Path(args.touches_source)

    required_paths = [
        minutes_source_path,
        bball_index_source_path,
        postup_tracking_source_path,
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
    bball_index_columns = set(bball_index_rows[0].keys()) if bball_index_rows else set()
    missing_bball_columns = [
        column
        for column in [
            "Post Up Impact Per 75 Possessions",
            "Post Up Shot Making",
            "Stable Post Up PPP",
            "Post Up Shot Quality",
        ]
        if column not in bball_index_columns
    ]
    if missing_bball_columns:
        raise SystemExit(
            "Missing columns in the bball-index source: "
            + ", ".join(missing_bball_columns)
        )

    postup_tracking_rows = standardize_rows(
        read_history_csv(postup_tracking_source_path),
        season_column="Season",
        player_column="PLAYER_NAME",
    )
    frequency_rows = build_postup_frequency_rows(postup_tracking_rows)
    draw_foul_rows = build_postup_draw_foul_rows(postup_tracking_rows)

    touches_rows = standardize_rows(
        read_history_csv(touches_source_path),
        season_column="Season",
        player_column="PLAYER_NAME",
    )

    metric_results: Dict[str, MetricResult] = {
        "POST_TOUCHES": compute_metric_result(
            contexts,
            postup_tracking_rows,
            "POST_TOUCHES",
            current_season,
            args.current_season_min_threshold,
            args.standard_min_threshold,
            args.allow_id_fallback,
            apply_penalty=True,
        ),
        "Post Up Frequency%": compute_metric_result(
            contexts,
            frequency_rows,
            "Post Up Frequency%",
            current_season,
            args.current_season_min_threshold,
            args.standard_min_threshold,
            args.allow_id_fallback,
            apply_penalty=True,
        ),
        "Post Up Impact Per 75 Possessions": compute_metric_result(
            contexts,
            bball_index_rows,
            "Post Up Impact Per 75 Possessions",
            current_season,
            args.current_season_min_threshold,
            args.standard_min_threshold,
            args.allow_id_fallback,
            apply_penalty=True,
        ),
        "Post Up Shot Making": compute_metric_result(
            contexts,
            bball_index_rows,
            "Post Up Shot Making",
            current_season,
            args.current_season_min_threshold,
            args.standard_min_threshold,
            args.allow_id_fallback,
            apply_penalty=True,
        ),
        "Stable Post Up PPP": compute_metric_result(
            contexts,
            bball_index_rows,
            "Stable Post Up PPP",
            current_season,
            args.current_season_min_threshold,
            args.standard_min_threshold,
            args.allow_id_fallback,
            apply_penalty=True,
        ),
        "PTS_PER_POST_TOUCH": compute_metric_result(
            contexts,
            touches_rows,
            "PTS_PER_POST_TOUCH",
            current_season,
            args.current_season_min_threshold,
            args.standard_min_threshold,
            args.allow_id_fallback,
            apply_penalty=True,
        ),
        "Post Up Shot Quality": compute_metric_result(
            contexts,
            bball_index_rows,
            "Post Up Shot Quality",
            current_season,
            args.current_season_min_threshold,
            args.standard_min_threshold,
            args.allow_id_fallback,
            apply_penalty=True,
        ),
        "Post Up Draw Foul Rate": compute_metric_result(
            contexts,
            draw_foul_rows,
            "Post Up Draw Foul Rate",
            current_season,
            args.current_season_min_threshold,
            args.standard_min_threshold,
            args.allow_id_fallback,
            apply_penalty=True,
        ),
    }

    component_names = [
        "POST_TOUCHES",
        "Post Up Frequency%",
        "Post Up Impact Per 75 Possessions",
        "Post Up Shot Making",
        "Stable Post Up PPP",
        "PTS_PER_POST_TOUCH",
        "Post Up Shot Quality",
        "Post Up Draw Foul Rate",
    ]

    control_scores: List[float] = []
    aggregate_rows: List[List[Optional[float]]] = []
    for index in range(len(contexts)):
        primary_group = [
            metric_results["POST_TOUCHES"].output_values[index],
            metric_results["Post Up Frequency%"].output_values[index],
            metric_results["Post Up Impact Per 75 Possessions"].output_values[index],
            metric_results["Post Up Shot Making"].output_values[index],
            metric_results["Stable Post Up PPP"].output_values[index],
        ]
        support_group = [
            metric_results["PTS_PER_POST_TOUCH"].output_values[index],
            metric_results["Post Up Shot Quality"].output_values[index],
            metric_results["Post Up Draw Foul Rate"].output_values[index],
        ]

        primary_average = average_numeric(primary_group)
        support_average = average_numeric(support_group)
        if primary_average is None or support_average is None:
            control_score = -1.0
        else:
            control_score = primary_average * 0.9 + support_average * 0.1

        control_scores.append(control_score)
        aggregate_rows.append([*primary_group, *support_group])

    control_median = statistics.median(control_scores)
    control_stdev = statistics.stdev(control_scores)
    aggregate_z_scores = [
        (value - control_median) / control_stdev for value in control_scores
    ]
    control_ratings = [
        post_control_rating(value, aggregate_z_scores)
        for value in aggregate_z_scores
    ]

    workbook_columns = [
        "NBA ID",
        "Season",
        "Player",
        "Post Control Rating",
        "",
        "Post Control",
        "POST_TOUCHES",
        "Post Up Frequency%",
        "Post Up Impact Per 75 Possessions",
        "Post Up Shot Making",
        "Stable Post Up PPP",
        "PTS_PER_POST_TOUCH",
        "Post Up Shot Quality",
        "Post Up Draw Foul Rate",
    ]

    sheet_rows: List[List[object]] = []
    rating_only_rows: List[Dict[str, object]] = []
    audit_rows: List[Dict[str, object]] = []
    unmatched_rows: List[Dict[str, object]] = []

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
                control_ratings[index],
                aggregate_z_scores[index],
                control_scores[index],
                *component_values,
            ]
        )
        rating_only_rows.append(
            {
                "NBA_ID": context.universe_row.nba_id,
                "Season": context.universe_row.season,
                "Player": context.universe_row.player,
                "Post Control Rating": control_ratings[index],
            }
        )

        audit_row: Dict[str, object] = {
            "NBA_ID": context.universe_row.nba_id,
            "Season": context.universe_row.season,
            "Player": context.universe_row.player,
            "RotationRole": context.universe_row.rotation_role,
            "MIN": context.effective_minutes,
            "Post Control": control_scores[index],
            "Post Control Aggregate Z": aggregate_z_scores[index],
            "Post Control Rating": control_ratings[index],
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

    output_prefix = args.output_prefix.strip() or "finishing_post_control"
    sheet_path = EXPORT_DIR / f"{output_prefix}_sheet.csv"
    rating_only_path = EXPORT_DIR / f"{output_prefix}_rating_only.csv"
    audit_path = EXPORT_DIR / f"{output_prefix}_audit.csv"
    unmatched_path = EXPORT_DIR / f"{output_prefix}_unmatched.csv"

    write_matrix_csv(sheet_path, workbook_columns, sheet_rows)
    write_csv(
        rating_only_path,
        ["NBA_ID", "Season", "Player", "Post Control Rating"],
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

    print(f"[OK] Built Post Control export for {len(sheet_rows)} player-season rows")
    print("[INFO] Post Up Frequency% uses tracking_postup.csv as (POST_TOUCHES / TOUCHES) * 100")
    print("[INFO] Post Up Draw Foul Rate uses tracking_postup.csv as (POST_TOUCH_FOULS / POST_TOUCHES) * 100")
    print(f"[OUT] Sheet -> {sheet_path}")
    print(f"[OUT] Rating only -> {rating_only_path}")
    print(f"[OUT] Audit -> {audit_path}")
    print(f"[OUT] Unmatched -> {unmatched_path}")


if __name__ == "__main__":
    main()
