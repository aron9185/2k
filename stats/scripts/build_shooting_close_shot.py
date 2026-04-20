from __future__ import annotations

import argparse
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
from build_finishing_standing_dunk import (
    build_nonzero_season_coverage,
    parse_metric_value,
    read_dict_rows,
    standardize_rows,
)
from pull_nba_stats import EXPORT_DIR, HISTORY_DIR, MANUAL_DIR


CLOSE_SHOT_METRICS: Sequence[MetricSpec] = (
    MetricSpec("Rim Shot Making", "bball", "Rim Shot Making"),
    MetricSpec("Rim Shot Making Efficiency", "bball", "Rim Shot Making Efficiency"),
    MetricSpec("Rim Shot Attempts Per 75 Possessions", "bball", "Rim Shot Attempts Per 75 Possessions"),
    MetricSpec("Stable Rim FG%", "bball", "Stable Rim FG%"),
    MetricSpec("Stable Short Midrange FG%", "bball", "Stable Short Midrange FG%"),
    MetricSpec("Floater Talent", "bball", "Floater Talent"),
    MetricSpec("Paint Shooting Talent", "bball", "Paint Shooting Talent"),
    MetricSpec("Paint Shot Making", "bball", "Paint Shot Making"),
    MetricSpec("Paint Shot Making Efficiency", "bball", "Paint Shot Making Efficiency"),
    MetricSpec("Floating_shot_FGM", "floating", "Floating_shot_FGM"),
    MetricSpec("NON-RA FGM", "non_ra", "NON-RA FGM"),
    MetricSpec("Short Mid Range FG%", "bball", "Short Mid Range FG%"),
    MetricSpec("Rim FG%", "bball", "Rim FG%"),
    MetricSpec("NON-RA FG_PCT", "non_ra", "NON-RA FG_PCT"),
    MetricSpec("Floating_shot_FG%", "floating", "Floating_shot_FG%"),
)

CLOSE_SHOT_COMPONENTS: Sequence[ComponentSlot] = (
    ComponentSlot("Rim Shot Making", "Rim Shot Making"),
    ComponentSlot("Rim Shot Making Efficiency", "Rim Shot Making Efficiency"),
    ComponentSlot("Rim Shot Attempts Per 75 Possessions", "Rim Shot Attempts Per 75 Possessions"),
    ComponentSlot("Stable Rim FG%", "Stable Rim FG%"),
    ComponentSlot("Stable Short Midrange FG%", "Stable Short Midrange FG%"),
    ComponentSlot("Floater Talent", "Floater Talent"),
    ComponentSlot("Paint Shooting Talent", "Paint Shooting Talent"),
    ComponentSlot("Paint Shot Making", "Paint Shot Making"),
    ComponentSlot("Paint Shot Making Efficiency", "Paint Shot Making Efficiency"),
    ComponentSlot("Floating_shot_FGM", "Floating_shot_FGM"),
    ComponentSlot("NON-RA FGM", "NON-RA FGM"),
    ComponentSlot("Short Mid Range FG%", "Short Mid Range FG%"),
    ComponentSlot("Rim FG%", "Rim FG%"),
    ComponentSlot("NON-RA FG_PCT", "NON-RA FG_PCT"),
    ComponentSlot("Floating_shot_FG%", "Floating_shot_FG%"),
)

PRIMARY_COMPONENT_COUNT = 11


def close_shot_rating(value: float, population: Sequence[float]) -> float:
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
        return interpolate_rating(value, minimum, p50, 25.0, 77.0)
    if value <= p60:
        return interpolate_rating(value, p50, p60, 77.0, 80.0)
    if value <= p70:
        return interpolate_rating(value, p60, p70, 80.0, 84.0)
    if value <= p80:
        return interpolate_rating(value, p70, p80, 84.0, 88.0)
    if value <= p88:
        return interpolate_rating(value, p80, p88, 88.0, 92.0)
    if value <= p94:
        return interpolate_rating(value, p88, p94, 92.0, 95.0)
    if value <= p98:
        return interpolate_rating(value, p94, p98, 95.0, 97.0)
    return interpolate_rating(value, p98, maximum, 97.0, 100.0)


def average_numeric(values: Sequence[Optional[float]]) -> Optional[float]:
    numeric = [value for value in values if value is not None]
    if not numeric:
        return None
    return statistics.mean(numeric)


def build_floating_sources(
    shot_rows: Sequence[Dict[str, str]],
) -> Tuple[List[Dict[str, str]], List[Dict[str, str]], List[str], List[str]]:
    if not shot_rows:
        return [], [], [], []

    fieldnames = list(shot_rows[0].keys())
    floating_fgm_columns = [
        column for column in fieldnames if "Floating" in column and column.endswith("_FGM")
    ]
    floating_fga_columns = [
        column for column in fieldnames if "Floating" in column and column.endswith("_FGA")
    ]

    floating_fgm_rows: List[Dict[str, str]] = []
    floating_fg_pct_rows: List[Dict[str, str]] = []

    for row in shot_rows:
        season = str(row.get("Season", "")).strip()
        player = str(row.get("PLAYER_NAME", "")).strip()
        if not season or not player:
            continue

        total_fgm = sum(
            parse_metric_value(row.get(column, "")) or 0.0
            for column in floating_fgm_columns
        )
        total_fga = sum(
            parse_metric_value(row.get(column, "")) or 0.0
            for column in floating_fga_columns
        )

        floating_fgm_rows.append(
            {
                "Season": season,
                "PLAYER_NAME": player,
                "Floating_shot_FGM": total_fgm,
            }
        )
        floating_fg_pct_rows.append(
            {
                "Season": season,
                "PLAYER_NAME": player,
                "Floating_shot_FG%": (total_fgm / total_fga) if total_fga else "",
            }
        )

    return floating_fgm_rows, floating_fg_pct_rows, floating_fgm_columns, floating_fga_columns


def standardize_non_ra_rows(rows: Sequence[Dict[str, str]]) -> List[Dict[str, str]]:
    standardized: List[Dict[str, str]] = []
    for row in rows:
        merged = dict(row)
        merged["NON-RA FGM"] = row.get("FGM", "")
        merged["NON-RA FG_PCT"] = row.get("FG_PCT", "")
        standardized.append(merged)
    return standardized


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build the Shooting -> Close Shot rating export."
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
        default=str(HISTORY_DIR / "bball_index_close_shot.csv"),
        help="CSV file containing the close-shot bball-index metrics.",
    )
    parser.add_argument(
        "--shot-source",
        default=str(HISTORY_DIR / "shooting_splits.csv"),
        help="History CSV used to derive floating shot counts.",
    )
    parser.add_argument(
        "--non-ra-source",
        default=str(HISTORY_DIR / "shooting_zone.csv"),
        help=(
            "History CSV used for NON-RA FGM / FG_PCT. "
            "The current workflow assumes this file already represents the In The Paint (Non-RA) split."
        ),
    )
    parser.add_argument(
        "--output-prefix",
        default="shooting_close_shot",
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
    shot_source_path = Path(args.shot_source)
    non_ra_source_path = Path(args.non_ra_source)

    for path in (
        minutes_source_path,
        bball_index_source_path,
        shot_source_path,
        non_ra_source_path,
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

    shot_rows = standardize_rows(
        read_dict_rows(shot_source_path),
        season_column="Season",
        player_column="Name",
    )
    floating_fgm_rows, floating_fg_pct_rows, floating_fgm_columns, floating_fga_columns = (
        build_floating_sources(shot_rows)
    )

    source_rows_by_key: Dict[str, List[Dict[str, str]]] = {
        "bball": standardize_bball_rows(read_history_csv(bball_index_source_path)),
        "floating": standardize_rows(
            [*floating_fgm_rows, *floating_fg_pct_rows],
            season_column="Season",
            player_column="PLAYER_NAME",
        ),
        "non_ra": standardize_non_ra_rows(read_history_csv(non_ra_source_path)),
    }

    floating_by_key: Dict[Tuple[str, str], Dict[str, str]] = {}
    for row in floating_fgm_rows:
        floating_by_key[(row["Season"], row["PLAYER_NAME"])] = dict(row)
    for row in floating_fg_pct_rows:
        key = (row["Season"], row["PLAYER_NAME"])
        floating_by_key.setdefault(key, {}).update(row)
        floating_by_key[key]["Season"] = row["Season"]
        floating_by_key[key]["PLAYER_NAME"] = row["PLAYER_NAME"]
    source_rows_by_key["floating"] = list(floating_by_key.values())
    floating_fgm_coverage = build_nonzero_season_coverage(
        source_rows_by_key["floating"],
        "Floating_shot_FGM",
    )

    current_season = detect_current_season(args.current_season, universe, minutes_rows)
    contexts = build_player_contexts(
        universe=universe,
        minutes_rows=minutes_rows,
        minutes_column=args.minutes_column,
        minutes_games_column=args.minutes_games_column,
        allow_id_fallback=args.allow_id_fallback,
    )

    metric_results: Dict[str, MetricResult] = {}
    for metric in CLOSE_SHOT_METRICS:
        metric_results[metric.alias] = compute_metric_result(
            contexts=contexts,
            rows=source_rows_by_key[metric.source_key],
            metric=metric,
            current_season=current_season,
            current_season_min_threshold=args.current_season_min_threshold,
            standard_min_threshold=args.standard_min_threshold,
            allow_id_fallback=args.allow_id_fallback,
            default_zero_when_missing=metric.alias == "Floating_shot_FGM",
            default_zero_nonzero_seasons=(
                floating_fgm_coverage
                if metric.alias == "Floating_shot_FGM"
                else None
            ),
        )

    close_shot_scores: List[float] = []
    component_rows: List[List[Optional[float]]] = []
    for row_index in range(len(contexts)):
        component_values = [
            metric_results[component.metric_alias].normalized_values[row_index]
            for component in CLOSE_SHOT_COMPONENTS
        ]
        component_rows.append(component_values)

        primary_group = component_values[:PRIMARY_COMPONENT_COUNT]
        support_group = component_values[PRIMARY_COMPONENT_COUNT:]
        primary_average = average_numeric(primary_group)
        support_average = average_numeric(support_group) or 0.0
        close_shot_score = -1.0 if primary_average is None else primary_average * 0.9 + support_average * 0.1
        close_shot_scores.append(close_shot_score)

    if len(close_shot_scores) < 2:
        raise SystemExit("Not enough close-shot scores to compute aggregate z-scores.")

    close_shot_median = statistics.median(close_shot_scores)
    close_shot_stdev = statistics.stdev(close_shot_scores)
    if close_shot_stdev == 0:
        raise SystemExit("Close-shot scores have zero variance; cannot compute aggregate z-scores.")

    aggregate_z_scores = [
        (value - close_shot_median) / close_shot_stdev for value in close_shot_scores
    ]
    close_shot_ratings = [
        close_shot_rating(value, aggregate_z_scores)
        for value in aggregate_z_scores
    ]

    sheet_headers = [
        "NBA ID",
        "Season",
        "Player",
        "Close Shot Rating",
        "",
        "Close Shot",
        *[component.header for component in CLOSE_SHOT_COMPONENTS],
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
            for metric in CLOSE_SHOT_METRICS
            if metric_results[metric.alias].raw_values[index] is None
        ]

        sheet_rows.append(
            [
                context.universe_row.nba_id,
                season,
                player,
                close_shot_ratings[index],
                aggregate_z_scores[index],
                close_shot_scores[index],
                *component_values,
            ]
        )
        rating_only_rows.append(
            {
                "NBA_ID": context.universe_row.nba_id,
                "Season": season,
                "Player": player,
                "Close Shot Rating": close_shot_ratings[index],
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
            "Close Shot": close_shot_scores[index],
            "CloseShotAggregateZ": aggregate_z_scores[index],
            "CloseShotRating": close_shot_ratings[index],
            "MissingMetricCount": len(missing_metrics),
            "MissingMetrics": " | ".join(missing_metrics),
        }
        for metric in CLOSE_SHOT_METRICS:
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

    output_prefix = args.output_prefix.strip() or "shooting_close_shot"
    sheet_path = EXPORT_DIR / f"{output_prefix}_sheet.csv"
    rating_only_path = EXPORT_DIR / f"{output_prefix}_rating_only.csv"
    audit_path = EXPORT_DIR / f"{output_prefix}_audit.csv"
    unmatched_path = EXPORT_DIR / f"{output_prefix}_unmatched.csv"

    write_matrix_csv(sheet_path, sheet_headers, sheet_rows)
    write_csv(
        rating_only_path,
        ["NBA_ID", "Season", "Player", "Close Shot Rating"],
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
        f"[OK] Built Close Shot export for {len(sheet_rows)} player-season rows "
        f"(aggregate median={close_shot_median:.6f}, stdev={close_shot_stdev:.6f})"
    )
    print(f"[INFO] Floating FGM columns used: {', '.join(floating_fgm_columns)}")
    print(f"[INFO] Floating FGA columns used: {', '.join(floating_fga_columns)}")
    print("[INFO] NON-RA fields currently map from shooting_zone.csv FGM / FG_PCT.")
    print(f"[OUT] Sheet -> {sheet_path}")
    print(f"[OUT] Rating only -> {rating_only_path}")
    print(f"[OUT] Audit -> {audit_path}")
    print(f"[OUT] Unmatched -> {unmatched_path}")


if __name__ == "__main__":
    main()
