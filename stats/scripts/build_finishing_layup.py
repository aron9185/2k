from __future__ import annotations

import argparse
import csv
import math
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence

from build_cal_lane import (
    CalUniverseRow,
    apply_cal_normalization,
    build_source_index,
    compute_capped_z_score,
    detect_current_season,
    enrich_universe_rows,
    load_cal_universe,
    load_universe_csv,
    match_metric_row,
    parse_float,
    read_history_csv,
    resolve_details_csv_path,
    resolve_live_minutes,
    write_csv,
)
from pull_nba_stats import EXPORT_DIR, HISTORY_DIR, MANUAL_DIR


@dataclass(frozen=True)
class MetricSpec:
    alias: str
    source_key: str
    source_column: str


@dataclass(frozen=True)
class ComponentSlot:
    header: str
    metric_alias: str


@dataclass
class PlayerContext:
    universe_row: CalUniverseRow
    effective_minutes: Optional[float]
    workbook_minutes: Optional[float]
    live_minutes: Optional[float]
    live_minutes_per_game: Optional[float]
    live_gp: Optional[float]
    minutes_matched_by: str


@dataclass
class MetricResult:
    raw_values: List[Optional[float]]
    normalized_values: List[Optional[float]]
    matched_by: List[str]
    mean_value: float
    stdev_value: float
    source_file: str


LAYUP_METRICS: Sequence[MetricSpec] = (
    MetricSpec("Finishing Talent", "bball", "Finishing Talent"),
    MetricSpec("Rim Shot Creation", "bball", "Rim Shot Creation"),
    MetricSpec("Drives Per 75 Possessions", "bball", "Drives Per 75 Possessions"),
    MetricSpec("Rim Shot Making", "bball", "Rim Shot Making"),
    MetricSpec("Rim Shot Making Efficiency", "bball", "Rim Shot Making Efficiency"),
    MetricSpec("Paint Shooting Talent", "bball", "Paint Shooting Talent"),
    MetricSpec("Rim Makes Consistency", "bball", "Rim Makes Consistency"),
    MetricSpec("Stable Rim FG%", "bball", "Stable Rim FG%"),
    MetricSpec("Contact Finish Rate", "bball", "Contact Finish Rate"),
    MetricSpec("Rim Attempt Consistency", "bball", "Rim Attempt Consistency"),
    MetricSpec("Rim FG%", "bball", "Rim FG%"),
    MetricSpec("Drive Foul Drawn Rate", "bball", "Drive Foul Drawn Rate"),
    MetricSpec("DRIVES", "drives", "DRIVES"),
    MetricSpec("DRIVE_FGM", "drives", "DRIVE_FGM"),
    MetricSpec("less_than_5ft_FGM", "under_5ft", "less_than_5ft_FGM"),
    MetricSpec("less_than_5ft_FG_PCT", "under_5ft", "less_than_5ft_FG_PCT"),
)

LAYUP_COMPONENTS: Sequence[ComponentSlot] = (
    ComponentSlot("Finishing Talent", "Finishing Talent"),
    ComponentSlot("Rim Shot Creation", "Rim Shot Creation"),
    ComponentSlot("Drives Per 75 Possessions", "Drives Per 75 Possessions"),
    ComponentSlot("Rim Shot Making", "Rim Shot Making"),
    ComponentSlot("Rim Shot Making Efficiency", "Rim Shot Making Efficiency"),
    ComponentSlot("Paint Shooting Talent", "Paint Shooting Talent"),
    ComponentSlot("Rim Makes Consistency", "Rim Makes Consistency"),
    ComponentSlot("DRIVES", "DRIVES"),
    ComponentSlot("DRIVE_FGM", "DRIVE_FGM"),
    ComponentSlot("Stable Rim FG%", "Stable Rim FG%"),
    ComponentSlot("Contact Finish Rate", "Contact Finish Rate"),
    ComponentSlot("Rim Attempt Consistency", "Rim Attempt Consistency"),
    ComponentSlot("less_than_5ft_FGM", "less_than_5ft_FGM"),
    ComponentSlot("Rim FG%", "Rim FG%"),
    ComponentSlot("Drive Foul Drawn Rate", "Drive Foul Drawn Rate"),
    ComponentSlot("less_than_5ft_FG_PCT", "less_than_5ft_FG_PCT"),
)

GROUP_ONE_COUNT = 9
GROUP_TWO_COUNT = 4
GROUP_THREE_COUNT = 3


def first_present_column(
    available_columns: Iterable[str],
    candidates: Iterable[str],
) -> str:
    column_set = {str(column).strip() for column in available_columns}
    for candidate in candidates:
        if candidate in column_set:
            return candidate
    return ""


def standardize_bball_rows(rows: Sequence[Dict[str, str]]) -> List[Dict[str, str]]:
    standardized: List[Dict[str, str]] = []
    for row in rows:
        merged = dict(row)
        merged["PLAYER_ID"] = ""
        merged["PLAYER_NAME"] = str(row.get("Player", "")).strip()
        merged["TEAM_ABBREVIATION"] = str(
            row.get("Team(s)", "") or row.get("TEAM_ABBREVIATION", "")
        ).strip()
        standardized.append(merged)
    return standardized


def standardize_under_5ft_rows(rows: Sequence[Dict[str, str]]) -> List[Dict[str, str]]:
    standardized: List[Dict[str, str]] = []
    for row in rows:
        merged = dict(row)
        merged["less_than_5ft_FGM"] = row.get("FGM", "")
        merged["less_than_5ft_FG_PCT"] = row.get("FG_PCT", "")
        standardized.append(merged)
    return standardized


def write_matrix_csv(path: Path, headers: Sequence[str], rows: Sequence[Sequence[object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(list(headers))
        writer.writerows(rows)


def mean_or_none(values: Sequence[float]) -> Optional[float]:
    if not values:
        return None
    return statistics.mean(values)


def layup_bucket_average(values: Sequence[Optional[float]]) -> float:
    numeric = [value for value in values if value is not None]
    if not numeric:
        return -0.5
    return statistics.mean(numeric)


def average_plus_zero(values: Sequence[Optional[float]]) -> float:
    numeric = [value for value in values if value is not None]
    if not numeric:
        return 0.0
    return statistics.mean(numeric + [0.0])


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
    lower_rank = math.floor(rank)
    upper_rank = math.ceil(rank)
    lower_value = ordered[lower_rank - 1]
    upper_value = ordered[upper_rank - 1]
    if lower_rank == upper_rank:
        return lower_value
    fraction = rank - lower_rank
    return lower_value + fraction * (upper_value - lower_value)


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


def compute_piecewise_rating(value: float, population: Sequence[float]) -> float:
    minimum = min(population)
    p25 = percentile_inc(population, 0.25)
    p50 = percentile_inc(population, 0.50)
    p75 = percentile_inc(population, 0.75)
    p85 = percentile_inc(population, 0.85)
    p92 = percentile_inc(population, 0.92)
    p96 = percentile_inc(population, 0.96)
    p99 = percentile_inc(population, 0.99)
    maximum = percentile_inc(population, 1.0)

    if value <= p25:
        return interpolate_rating(value, minimum, p25, 25.0, 66.0)
    if value <= p50:
        return interpolate_rating(value, p25, p50, 66.0, 73.0)
    if value <= p75:
        return interpolate_rating(value, p50, p75, 73.0, 80.0)
    if value <= p85:
        return interpolate_rating(value, p75, p85, 80.0, 84.45)
    if value <= p92:
        return interpolate_rating(value, p85, p92, 84.45, 89.0)
    if value <= p96:
        return interpolate_rating(value, p92, p96, 89.0, 94.0)
    if value <= p99:
        return interpolate_rating(value, p96, p99, 94.0, 98.0)
    return interpolate_rating(value, p99, maximum, 98.0, 100.0)


def build_player_contexts(
    universe: Sequence[CalUniverseRow],
    minutes_rows: Sequence[Dict[str, str]],
    minutes_column: str,
    minutes_games_column: str,
    allow_id_fallback: bool,
) -> List[PlayerContext]:
    minutes_by_id, minutes_by_name = build_source_index(minutes_rows)
    contexts: List[PlayerContext] = []

    for universe_row in universe:
        minutes_row, minutes_matched_by = match_metric_row(
            universe_row,
            minutes_by_id,
            minutes_by_name,
            allow_id_fallback=allow_id_fallback,
        )
        live_minutes, live_minutes_per_game, live_gp = resolve_live_minutes(
            minutes_row,
            minutes_column,
            minutes_games_column,
        )
        contexts.append(
            PlayerContext(
                universe_row=universe_row,
                effective_minutes=live_minutes if live_minutes is not None else universe_row.minutes,
                workbook_minutes=universe_row.minutes,
                live_minutes=live_minutes,
                live_minutes_per_game=live_minutes_per_game,
                live_gp=live_gp,
                minutes_matched_by=minutes_matched_by,
            )
        )

    return contexts


def compute_metric_result(
    contexts: Sequence[PlayerContext],
    rows: Sequence[Dict[str, str]],
    metric: MetricSpec,
    current_season: str,
    current_season_min_threshold: float,
    standard_min_threshold: float,
    allow_id_fallback: bool,
    default_zero_when_missing: bool = False,
    default_zero_nonzero_seasons: Optional[set[str]] = None,
) -> MetricResult:
    if not rows:
        raise SystemExit(f"No rows found for source {metric.source_key}.")
    if metric.source_column not in rows[0]:
        raise SystemExit(
            f"Metric column not found in source {metric.source_key}: {metric.source_column}"
        )

    by_id, by_name = build_source_index(rows)
    raw_values: List[Optional[float]] = []
    matched_by_values: List[str] = []
    matched_numeric_values: List[float] = []

    for context in contexts:
        source_row, matched_by = match_metric_row(
            context.universe_row,
            by_id,
            by_name,
            allow_id_fallback=allow_id_fallback,
        )
        raw_value = None if source_row is None else parse_float(source_row.get(metric.source_column, ""))
        if (
            raw_value is None
            and default_zero_when_missing
            and (
                default_zero_nonzero_seasons is None
                or context.universe_row.season in default_zero_nonzero_seasons
            )
        ):
            raw_value = 0.0
            matched_by = "default-zero" if not matched_by else f"{matched_by}|default-zero"
        raw_values.append(raw_value)
        matched_by_values.append(matched_by)
        if raw_value is not None:
            matched_numeric_values.append(raw_value)

    if len(matched_numeric_values) < 2:
        raise SystemExit(
            f"Not enough matched values to compute z-scores for {metric.alias}."
        )

    mean_value = statistics.mean(matched_numeric_values)
    stdev_value = statistics.stdev(matched_numeric_values)
    if stdev_value == 0:
        raise SystemExit(f"{metric.alias} has zero variance; cannot compute z-scores.")

    normalized_values: List[Optional[float]] = []
    for context, raw_value in zip(contexts, raw_values):
        raw_z = compute_capped_z_score(raw_value, mean_value, stdev_value)
        normalized_values.append(
            apply_cal_normalization(
                season=context.universe_row.season,
                rotation_role=context.universe_row.rotation_role,
                minutes=context.effective_minutes,
                raw_z=raw_z,
                current_season=current_season,
                current_season_min_threshold=current_season_min_threshold,
                standard_min_threshold=standard_min_threshold,
            )
        )

    return MetricResult(
        raw_values=raw_values,
        normalized_values=normalized_values,
        matched_by=matched_by_values,
        mean_value=mean_value,
        stdev_value=stdev_value,
        source_file=metric.source_key,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build the Finishing -> Layup rating export from bball-index + NBA.com history."
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
        "--bball-index-source",
        default=str(HISTORY_DIR / "bball_index_layup.csv"),
        help="CSV file containing the live bball-index layup metrics.",
    )
    parser.add_argument(
        "--drives-source",
        default=str(HISTORY_DIR / "tracking_drives.csv"),
        help="CSV file containing DRIVES / DRIVE_FGM.",
    )
    parser.add_argument(
        "--under-5ft-source",
        default=str(HISTORY_DIR / "shooting_5ft.csv"),
        help="CSV file used for the current placeholder less_than_5ft mapping.",
    )
    parser.add_argument(
        "--minutes-source",
        default=str(HISTORY_DIR / "general_traditional.csv"),
        help="History CSV used to refresh MIN in real time.",
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
        "--output-prefix",
        default="finishing_layup",
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
    drives_source_path = Path(args.drives_source)
    under_5ft_source_path = Path(args.under_5ft_source)

    for path in (
        minutes_source_path,
        bball_index_source_path,
        drives_source_path,
        under_5ft_source_path,
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
        "drives": read_history_csv(drives_source_path),
        "under_5ft": standardize_under_5ft_rows(read_history_csv(under_5ft_source_path)),
    }
    current_season = detect_current_season(
        args.current_season,
        universe,
        minutes_rows,
    )
    contexts = build_player_contexts(
        universe=universe,
        minutes_rows=minutes_rows,
        minutes_column=args.minutes_column,
        minutes_games_column=args.minutes_games_column,
        allow_id_fallback=args.allow_id_fallback,
    )

    metric_results: Dict[str, MetricResult] = {}
    for metric in LAYUP_METRICS:
        metric_results[metric.alias] = compute_metric_result(
            contexts=contexts,
            rows=source_rows_by_key[metric.source_key],
            metric=metric,
            current_season=current_season,
            current_season_min_threshold=args.current_season_min_threshold,
            standard_min_threshold=args.standard_min_threshold,
            allow_id_fallback=args.allow_id_fallback,
        )

    layup_scores: List[float] = []
    component_rows: List[List[Optional[float]]] = []
    for row_index in range(len(contexts)):
        component_values = [
            metric_results[component.metric_alias].normalized_values[row_index]
            for component in LAYUP_COMPONENTS
        ]
        component_rows.append(component_values)

        group_one = component_values[:GROUP_ONE_COUNT]
        group_two = component_values[
            GROUP_ONE_COUNT : GROUP_ONE_COUNT + GROUP_TWO_COUNT
        ]
        group_three = component_values[-GROUP_THREE_COUNT:]

        layup_score = (
            layup_bucket_average(group_one) * 0.8
            + average_plus_zero(group_two) * 0.15
            + average_plus_zero(group_three) * 0.5
        )
        layup_scores.append(layup_score)

    if len(layup_scores) < 2:
        raise SystemExit("Not enough layup scores to compute aggregate z-scores.")

    layup_median = statistics.median(layup_scores)
    layup_stdev = statistics.stdev(layup_scores)
    if layup_stdev == 0:
        raise SystemExit("Layup scores have zero variance; cannot compute aggregate z-scores.")

    aggregate_z_scores = [
        (value - layup_median) / layup_stdev for value in layup_scores
    ]
    layup_ratings = [
        compute_piecewise_rating(value, aggregate_z_scores)
        for value in aggregate_z_scores
    ]

    sheet_headers = [
        "NBA ID",
        "Season",
        "Player",
        "Layup Rating",
        "",
        "Layup",
        *[component.header for component in LAYUP_COMPONENTS],
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
            for metric in LAYUP_METRICS
            if metric_results[metric.alias].raw_values[index] is None
        ]

        sheet_rows.append(
            [
                context.universe_row.nba_id,
                season,
                player,
                layup_ratings[index],
                aggregate_z_scores[index],
                layup_scores[index],
                *component_values,
            ]
        )
        rating_only_rows.append(
            {
                "NBA_ID": context.universe_row.nba_id,
                "Season": season,
                "Player": player,
                "Layup Rating": layup_ratings[index],
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
            "Layup": layup_scores[index],
            "LayupAggregateZ": aggregate_z_scores[index],
            "LayupRating": layup_ratings[index],
            "MissingMetricCount": len(missing_metrics),
            "MissingMetrics": " | ".join(missing_metrics),
        }
        for metric in LAYUP_METRICS:
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

    output_prefix = args.output_prefix.strip() or "finishing_layup"
    sheet_path = EXPORT_DIR / f"{output_prefix}_sheet.csv"
    rating_only_path = EXPORT_DIR / f"{output_prefix}_rating_only.csv"
    audit_path = EXPORT_DIR / f"{output_prefix}_audit.csv"
    unmatched_path = EXPORT_DIR / f"{output_prefix}_unmatched.csv"

    write_matrix_csv(sheet_path, sheet_headers, sheet_rows)
    write_csv(
        rating_only_path,
        ["NBA_ID", "Season", "Player", "Layup Rating"],
        rating_only_rows,
    )
    write_csv(
        audit_path,
        list(audit_rows[0].keys()) if audit_rows else [],
        audit_rows,
    )
    write_csv(
        unmatched_path,
        [
            "NBA_ID",
            "Season",
            "Player",
            "RotationRole",
            "MIN",
            "MissingCount",
            "MissingMetrics",
        ],
        unmatched_rows,
    )

    print(
        f"[OK] Built Layup export for {len(sheet_rows)} player-season rows "
        f"(aggregate median={layup_median:.6f}, stdev={layup_stdev:.6f})"
    )
    print(f"[OUT] Sheet -> {sheet_path}")
    print(f"[OUT] Rating only -> {rating_only_path}")
    print(f"[OUT] Audit -> {audit_path}")
    print(f"[OUT] Unmatched -> {unmatched_path}")


if __name__ == "__main__":
    main()
