from __future__ import annotations

import argparse
import statistics
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from build_finishing_driving_dunk import interpolate_rating
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


def post_hook_rating(value: float, population: Sequence[float]) -> float:
    minimum = min(population)
    p05 = percentile_inc(population, 0.05)
    p15 = percentile_inc(population, 0.15)
    p25 = percentile_inc(population, 0.25)
    p40 = percentile_inc(population, 0.40)
    p60 = percentile_inc(population, 0.60)
    p80 = percentile_inc(population, 0.80)
    p95 = percentile_inc(population, 0.95)
    maximum = percentile_inc(population, 1.0)

    if value <= p05:
        return interpolate_rating(value, minimum, p05, 25.0, 26.0)
    if value <= p15:
        return interpolate_rating(value, p05, p15, 26.0, 28.0)
    if value <= p25:
        return interpolate_rating(value, p15, p25, 28.0, 35.0)
    if value <= p40:
        return interpolate_rating(value, p25, p40, 35.0, 44.0)
    if value <= p60:
        return interpolate_rating(value, p40, p60, 44.0, 56.0)
    if value <= p80:
        return interpolate_rating(value, p60, p80, 56.0, 69.0)
    if value <= p95:
        return interpolate_rating(value, p80, p95, 69.0, 86.0)
    return interpolate_rating(value, p95, maximum, 86.0, 100.0)


def build_hook_sources(
    shot_rows: Sequence[Dict[str, str]],
    gp_rows: Sequence[Dict[str, str]],
) -> Tuple[
    List[Dict[str, str]],
    List[Dict[str, str]],
    List[Dict[str, str]],
    List[str],
    List[str],
]:
    if not shot_rows:
        return [], [], [], [], []

    gp_lookup = {
        (str(row.get("Season", "")).strip(), str(row.get("PLAYER_NAME", "")).strip()): row
        for row in gp_rows
    }

    fieldnames = list(shot_rows[0].keys())
    hook_fgm_columns = [
        column for column in fieldnames if "Hook" in column and column.endswith("_FGM")
    ]
    hook_fga_columns = [
        column for column in fieldnames if "Hook" in column and column.endswith("_FGA")
    ]

    hook_fgm_rows: List[Dict[str, str]] = []
    hook_pg_rows: List[Dict[str, str]] = []
    hook_fg_pct_rows: List[Dict[str, str]] = []

    for row in shot_rows:
        season = str(row.get("Season", "")).strip()
        player = str(row.get("PLAYER_NAME", "")).strip()
        if not season or not player:
            continue

        total_fgm = sum(parse_metric_value(row.get(column, "")) or 0.0 for column in hook_fgm_columns)
        total_fga = sum(parse_metric_value(row.get(column, "")) or 0.0 for column in hook_fga_columns)

        gp_row = gp_lookup.get((season, player))
        gp = parse_metric_value(gp_row.get("GP", "")) if gp_row is not None else None

        hook_fgm_rows.append(
            {
                "Season": season,
                "PLAYER_NAME": player,
                "Hook_Shot_FGM": total_fgm,
            }
        )
        hook_pg_rows.append(
            {
                "Season": season,
                "PLAYER_NAME": player,
                "Hook_Shot_PG": (total_fgm / gp) if gp not in (None, 0) else "",
            }
        )
        hook_fg_pct_rows.append(
            {
                "Season": season,
                "PLAYER_NAME": player,
                "Hook_Shot_FG%": (total_fgm / total_fga) if total_fga else "",
            }
        )

    return hook_pg_rows, hook_fgm_rows, hook_fg_pct_rows, hook_fgm_columns, hook_fga_columns


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build the Finishing -> Post Hook rating export."
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
        help="History CSV used to derive hook shot counts.",
    )
    parser.add_argument(
        "--bball-index-source",
        default=str(HISTORY_DIR / "bball_index_postup_full.csv"),
        help="CSV file containing the post-up support metrics.",
    )
    parser.add_argument(
        "--postup-tracking-source",
        default=str(HISTORY_DIR / "tracking_postup.csv"),
        help="History CSV used for POST_TOUCHES.",
    )
    parser.add_argument(
        "--support-metric-column",
        default="Post Up Shot Making Efficiency",
        help=(
            "Extra post-up support metric used for the workbook's BM column. "
            "Defaults to Post Up Shot Making Efficiency because the reduced sheet header is ambiguous."
        ),
    )
    parser.add_argument(
        "--output-prefix",
        default="finishing_post_hook",
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
    bball_index_source_path = Path(args.bball_index_source)
    postup_tracking_source_path = Path(args.postup_tracking_source)

    required_paths = [
        minutes_source_path,
        shot_source_path,
        bball_index_source_path,
        postup_tracking_source_path,
    ]
    for path in required_paths:
        if not path.exists():
            raise SystemExit(f"Source CSV not found: {path}")
    if details_path and not details_path.exists():
        raise SystemExit(f"Details CSV not found: {details_path}")

    support_metric_column = args.support_metric_column.strip() or "Post Up Shot Making Efficiency"

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
    hook_pg_rows, hook_fgm_rows, hook_fg_pct_rows, hook_fgm_columns, hook_fga_columns = (
        build_hook_sources(shot_rows, gp_rows)
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
            "Post Up Shot Making",
            "Post Up Impact Per 75 Possessions",
            "Stable Post Up PPP",
            support_metric_column,
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

    metric_results: Dict[str, MetricResult] = {
        "Hook_Shot_FGM": compute_metric_result(
            contexts,
            hook_fgm_rows,
            "Hook_Shot_FGM",
            current_season,
            args.current_season_min_threshold,
            args.standard_min_threshold,
            args.allow_id_fallback,
            apply_penalty=True,
            default_zero_when_missing=True,
            default_zero_nonzero_seasons=build_nonzero_season_coverage(
                hook_fgm_rows,
                "Hook_Shot_FGM",
            ),
        ),
        "Hook_Shot_PG": compute_metric_result(
            contexts,
            hook_pg_rows,
            "Hook_Shot_PG",
            current_season,
            args.current_season_min_threshold,
            args.standard_min_threshold,
            args.allow_id_fallback,
            apply_penalty=True,
        ),
        "Hook_Shot_FG%": compute_metric_result(
            contexts,
            hook_fg_pct_rows,
            "Hook_Shot_FG%",
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
        support_metric_column: compute_metric_result(
            contexts,
            bball_index_rows,
            support_metric_column,
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
    }

    component_names = [
        "Hook_Shot_FGM",
        "Hook_Shot_PG",
        "Hook_Shot_FG%",
        "Post Up Shot Making",
        "Post Up Impact Per 75 Possessions",
        "POST_TOUCHES",
        support_metric_column,
        "Stable Post Up PPP",
    ]

    hook_scores: List[float] = []
    aggregate_rows: List[List[Optional[float]]] = []
    for index in range(len(contexts)):
        primary_group = [
            metric_results["Hook_Shot_FGM"].output_values[index],
            metric_results["Hook_Shot_PG"].output_values[index],
            metric_results["Hook_Shot_FG%"].output_values[index],
            metric_results["Post Up Shot Making"].output_values[index],
        ]
        support_group = [
            metric_results["Post Up Impact Per 75 Possessions"].output_values[index],
            metric_results["POST_TOUCHES"].output_values[index],
            metric_results[support_metric_column].output_values[index],
            metric_results["Stable Post Up PPP"].output_values[index],
        ]
        primary_average = average_numeric(primary_group)
        support_average = average_numeric(support_group)
        hook_score = (primary_average if primary_average is not None else 0.0) * 0.9 + (
            support_average if support_average is not None else 0.0
        ) * 0.1

        hook_scores.append(hook_score)
        aggregate_rows.append([*primary_group, *support_group])

    hook_mean = statistics.mean(hook_scores)
    hook_stdev = statistics.stdev(hook_scores)
    aggregate_z_scores = [
        (value - hook_mean) / hook_stdev for value in hook_scores
    ]
    hook_ratings = [
        post_hook_rating(value, aggregate_z_scores)
        for value in aggregate_z_scores
    ]

    workbook_columns = [
        "NBA ID",
        "Season",
        "Player",
        "Post Hook Rating",
        "",
        "Post Hook",
        "Hook_Shot_FGM",
        "Hook_Shot_PG",
        "Hook_Shot_FG%",
        "Post Up Shot Making",
        "Post Up Impact Per 75 Possessions",
        "POST_TOUCHES",
        support_metric_column,
        "Stable Post Up PPP",
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
                hook_ratings[index],
                aggregate_z_scores[index],
                hook_scores[index],
                *component_values,
            ]
        )
        rating_only_rows.append(
            {
                "NBA_ID": context.universe_row.nba_id,
                "Season": context.universe_row.season,
                "Player": context.universe_row.player,
                "Post Hook Rating": hook_ratings[index],
            }
        )

        audit_row: Dict[str, object] = {
            "NBA_ID": context.universe_row.nba_id,
            "Season": context.universe_row.season,
            "Player": context.universe_row.player,
            "RotationRole": context.universe_row.rotation_role,
            "MIN": context.effective_minutes,
            "Post Hook": hook_scores[index],
            "Post Hook Aggregate Z": aggregate_z_scores[index],
            "Post Hook Rating": hook_ratings[index],
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

    output_prefix = args.output_prefix.strip() or "finishing_post_hook"
    sheet_path = EXPORT_DIR / f"{output_prefix}_sheet.csv"
    rating_only_path = EXPORT_DIR / f"{output_prefix}_rating_only.csv"
    audit_path = EXPORT_DIR / f"{output_prefix}_audit.csv"
    unmatched_path = EXPORT_DIR / f"{output_prefix}_unmatched.csv"

    write_matrix_csv(sheet_path, workbook_columns, sheet_rows)
    write_csv(
        rating_only_path,
        ["NBA_ID", "Season", "Player", "Post Hook Rating"],
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

    print(f"[OK] Built Post Hook export for {len(sheet_rows)} player-season rows")
    print(f"[INFO] Hook FGM columns used: {', '.join(hook_fgm_columns)}")
    print(f"[INFO] Hook FGA columns used: {', '.join(hook_fga_columns)}")
    print(f"[INFO] Workbook BM column mapped to: {support_metric_column}")
    print(f"[OUT] Sheet -> {sheet_path}")
    print(f"[OUT] Rating only -> {rating_only_path}")
    print(f"[OUT] Audit -> {audit_path}")
    print(f"[OUT] Unmatched -> {unmatched_path}")


if __name__ == "__main__":
    main()
