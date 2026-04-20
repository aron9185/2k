from __future__ import annotations

import argparse
import statistics
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from build_finishing_post_control import (
    build_postup_draw_foul_rows,
    post_control_rating as draw_foul_rating,
)
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


def build_stable_shooting_fouls_drawn_rows(
    bball_index_rows: Sequence[Dict[str, str]],
) -> List[Dict[str, str]]:
    derived_rows: List[Dict[str, str]] = []
    for row in bball_index_rows:
        season = str(row.get("Season", "")).strip()
        player = str(row.get("PLAYER_NAME", "")).strip()
        if not season or not player:
            continue

        stable_total = parse_metric_value(row.get("Stable Fouls Drawn Per 75", ""))
        stable_non_shooting = parse_metric_value(row.get("Stable Non Shooting Fouls Drawn Per 75", ""))
        if stable_total is None or stable_non_shooting is None:
            value: object = ""
        else:
            value = stable_total - stable_non_shooting

        derived_rows.append(
            {
                "Season": season,
                "PLAYER_NAME": player,
                "Stable Shooting Fouls Drawn Per 75": value,
            }
        )
    return derived_rows


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build the Finishing -> Draw Foul rating export."
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
        "--traditional-source",
        default=str(HISTORY_DIR / "general_traditional.csv"),
        help="Traditional per-game source used for FTA.",
    )
    parser.add_argument(
        "--per100-source",
        default=str(HISTORY_DIR / "general_traditional_per100.csv"),
        help="Per-100 source used for FTA_per100.",
    )
    parser.add_argument(
        "--bball-index-source",
        default=str(HISTORY_DIR / "bball_index_draw_foul.csv"),
        help="CSV file containing the draw-foul support metrics.",
    )
    parser.add_argument(
        "--postup-tracking-source",
        default=str(HISTORY_DIR / "tracking_postup.csv"),
        help="History CSV used for the Post Up Draw Foul Rate proxy.",
    )
    parser.add_argument(
        "--output-prefix",
        default="finishing_draw_foul",
        help="Prefix used for CSV outputs inside stats/exports.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    workbook_path = Path(args.workbook)
    universe_path = Path(args.universe_csv)
    details_path = resolve_details_csv_path(args.details_csv, universe_path)
    minutes_source_path = Path(args.minutes_source)
    traditional_source_path = Path(args.traditional_source)
    per100_source_path = Path(args.per100_source)
    bball_index_source_path = Path(args.bball_index_source)
    postup_tracking_source_path = Path(args.postup_tracking_source)

    required_paths = [
        minutes_source_path,
        traditional_source_path,
        per100_source_path,
        bball_index_source_path,
        postup_tracking_source_path,
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

    traditional_rows = standardize_rows(
        read_history_csv(traditional_source_path),
        season_column="Season",
        player_column="PLAYER_NAME",
    )
    per100_rows = standardize_rows(
        read_history_csv(per100_source_path),
        season_column="Season",
        player_column="PLAYER_NAME",
    )
    bball_index_rows = standardize_rows(
        read_dict_rows(bball_index_source_path),
        season_column="Season",
        player_column="Player",
    )
    postup_tracking_rows = standardize_rows(
        read_history_csv(postup_tracking_source_path),
        season_column="Season",
        player_column="PLAYER_NAME",
    )
    postup_draw_foul_rows = build_postup_draw_foul_rows(postup_tracking_rows)
    stable_shooting_rows = build_stable_shooting_fouls_drawn_rows(bball_index_rows)

    bball_index_columns = set(bball_index_rows[0].keys()) if bball_index_rows else set()
    missing_bball_columns = [
        column
        for column in [
            "Fouls Drawn / 75",
            "Stable Fouls Drawn Per 75",
            "Stable Non Shooting Fouls Drawn Per 75",
            "Stable FTA Per 75",
            "Drive Foul Drawn Rate",
            "Isolation Foul Drawn Rate",
        ]
        if column not in bball_index_columns
    ]
    if missing_bball_columns:
        raise SystemExit(
            "Missing columns in the bball-index source: "
            + ", ".join(missing_bball_columns)
        )

    metric_results: Dict[str, MetricResult] = {
        "FTA_per100": compute_metric_result(
            contexts,
            per100_rows,
            "FTA",
            current_season,
            args.current_season_min_threshold,
            args.standard_min_threshold,
            args.allow_id_fallback,
            apply_penalty=True,
        ),
        "FTA": compute_metric_result(
            contexts,
            traditional_rows,
            "FTA",
            current_season,
            args.current_season_min_threshold,
            args.standard_min_threshold,
            args.allow_id_fallback,
            apply_penalty=True,
        ),
        "Fouls Drawn / 75": compute_metric_result(
            contexts,
            bball_index_rows,
            "Fouls Drawn / 75",
            current_season,
            args.current_season_min_threshold,
            args.standard_min_threshold,
            args.allow_id_fallback,
            apply_penalty=True,
        ),
        "Stable Fouls Drawn Per 75": compute_metric_result(
            contexts,
            bball_index_rows,
            "Stable Fouls Drawn Per 75",
            current_season,
            args.current_season_min_threshold,
            args.standard_min_threshold,
            args.allow_id_fallback,
            apply_penalty=True,
        ),
        "Stable Shooting Fouls Drawn Per 75": compute_metric_result(
            contexts,
            stable_shooting_rows,
            "Stable Shooting Fouls Drawn Per 75",
            current_season,
            args.current_season_min_threshold,
            args.standard_min_threshold,
            args.allow_id_fallback,
            apply_penalty=True,
        ),
        "Stable Non Shooting Fouls Drawn Per 75": compute_metric_result(
            contexts,
            bball_index_rows,
            "Stable Non Shooting Fouls Drawn Per 75",
            current_season,
            args.current_season_min_threshold,
            args.standard_min_threshold,
            args.allow_id_fallback,
            apply_penalty=True,
        ),
        "Stable FTA Per 75": compute_metric_result(
            contexts,
            bball_index_rows,
            "Stable FTA Per 75",
            current_season,
            args.current_season_min_threshold,
            args.standard_min_threshold,
            args.allow_id_fallback,
            apply_penalty=True,
        ),
        "Drive Foul Drawn Rate": compute_metric_result(
            contexts,
            bball_index_rows,
            "Drive Foul Drawn Rate",
            current_season,
            args.current_season_min_threshold,
            args.standard_min_threshold,
            args.allow_id_fallback,
            apply_penalty=True,
        ),
        "Isolation Foul Drawn Rate": compute_metric_result(
            contexts,
            bball_index_rows,
            "Isolation Foul Drawn Rate",
            current_season,
            args.current_season_min_threshold,
            args.standard_min_threshold,
            args.allow_id_fallback,
            apply_penalty=True,
        ),
        "Post Up Draw Foul Rate": compute_metric_result(
            contexts,
            postup_draw_foul_rows,
            "Post Up Draw Foul Rate",
            current_season,
            args.current_season_min_threshold,
            args.standard_min_threshold,
            args.allow_id_fallback,
            apply_penalty=True,
        ),
    }

    component_names = [
        "FTA_per100",
        "FTA",
        "Fouls Drawn / 75",
        "Stable Fouls Drawn Per 75",
        "Stable Shooting Fouls Drawn Per 75",
        "Stable Non Shooting Fouls Drawn Per 75",
        "Stable FTA Per 75",
        "Drive Foul Drawn Rate",
        "Isolation Foul Drawn Rate",
        "Post Up Draw Foul Rate",
    ]

    draw_foul_scores: List[float] = []
    aggregate_rows: List[List[Optional[float]]] = []
    for index in range(len(contexts)):
        group_one = [
            metric_results["FTA_per100"].output_values[index],
            metric_results["FTA"].output_values[index],
            metric_results["Fouls Drawn / 75"].output_values[index],
            metric_results["Stable Fouls Drawn Per 75"].output_values[index],
            metric_results["Stable Shooting Fouls Drawn Per 75"].output_values[index],
            metric_results["Stable Non Shooting Fouls Drawn Per 75"].output_values[index],
            metric_results["Stable FTA Per 75"].output_values[index],
        ]
        group_two = [
            metric_results["Drive Foul Drawn Rate"].output_values[index],
            metric_results["Isolation Foul Drawn Rate"].output_values[index],
        ]
        group_three = metric_results["Post Up Draw Foul Rate"].output_values[index]

        group_one_average = average_numeric(group_one)
        group_two_average = average_numeric(group_two)
        if group_one_average is None or group_three is None:
            draw_foul_score = -1.0
        else:
            draw_foul_score = (
                group_one_average * 0.85
                + (group_two_average or 0.0) * 0.1
                + group_three * 0.05
            )

        draw_foul_scores.append(draw_foul_score)
        aggregate_rows.append([*group_one, *group_two, group_three])

    draw_foul_median = statistics.median(draw_foul_scores)
    draw_foul_stdev = statistics.stdev(draw_foul_scores)
    aggregate_z_scores = [
        (value - draw_foul_median) / draw_foul_stdev for value in draw_foul_scores
    ]
    draw_foul_ratings = [
        draw_foul_rating(value, aggregate_z_scores)
        for value in aggregate_z_scores
    ]

    workbook_columns = [
        "NBA ID",
        "Season",
        "Player",
        "Draw Foul Rating",
        "",
        "Draw Foul",
        "FTA_per100",
        "FTA",
        "Fouls Drawn / 75",
        "Stable Fouls Drawn Per 75",
        "Stable Shooting Fouls Drawn Per 75",
        "Stable Non Shooting Fouls Drawn Per 75",
        "Stable FTA Per 75",
        "Drive Foul Drawn Rate",
        "Isolation Foul Drawn Rate",
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
                draw_foul_ratings[index],
                aggregate_z_scores[index],
                draw_foul_scores[index],
                *component_values,
            ]
        )
        rating_only_rows.append(
            {
                "NBA_ID": context.universe_row.nba_id,
                "Season": context.universe_row.season,
                "Player": context.universe_row.player,
                "Draw Foul Rating": draw_foul_ratings[index],
            }
        )

        audit_row: Dict[str, object] = {
            "NBA_ID": context.universe_row.nba_id,
            "Season": context.universe_row.season,
            "Player": context.universe_row.player,
            "RotationRole": context.universe_row.rotation_role,
            "MIN": context.effective_minutes,
            "Draw Foul": draw_foul_scores[index],
            "Draw Foul Aggregate Z": aggregate_z_scores[index],
            "Draw Foul Rating": draw_foul_ratings[index],
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

    output_prefix = args.output_prefix.strip() or "finishing_draw_foul"
    sheet_path = EXPORT_DIR / f"{output_prefix}_sheet.csv"
    rating_only_path = EXPORT_DIR / f"{output_prefix}_rating_only.csv"
    audit_path = EXPORT_DIR / f"{output_prefix}_audit.csv"
    unmatched_path = EXPORT_DIR / f"{output_prefix}_unmatched.csv"

    write_matrix_csv(sheet_path, workbook_columns, sheet_rows)
    write_csv(
        rating_only_path,
        ["NBA_ID", "Season", "Player", "Draw Foul Rating"],
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

    print(f"[OK] Built Draw Foul export for {len(sheet_rows)} player-season rows")
    print("[INFO] FTA_per100 uses general_traditional_per100.csv -> FTA")
    print("[INFO] Stable Shooting Fouls Drawn Per 75 uses a derived fallback: Stable Fouls Drawn Per 75 - Stable Non Shooting Fouls Drawn Per 75")
    print("[INFO] Post Up Draw Foul Rate uses tracking_postup.csv as (POST_TOUCH_FOULS / POST_TOUCHES) * 100")
    print(f"[OUT] Sheet -> {sheet_path}")
    print(f"[OUT] Rating only -> {rating_only_path}")
    print(f"[OUT] Audit -> {audit_path}")
    print(f"[OUT] Unmatched -> {unmatched_path}")


if __name__ == "__main__":
    main()
