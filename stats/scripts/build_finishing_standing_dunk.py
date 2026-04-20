from __future__ import annotations

import argparse
import csv
import math
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

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
    normalize_name,
    parse_float,
    read_history_csv,
    resolve_details_csv_path,
    resolve_live_minutes,
    write_csv,
)
from pull_nba_stats import EXPORT_DIR, HISTORY_DIR, MANUAL_DIR


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
    output_values: List[Optional[float]]
    matched_by: List[str]
    mean_value: Optional[float]
    stdev_value: Optional[float]


WORKBOOK_COLUMNS = [
    "NBA ID",
    "Season",
    "Player",
    "Standing Dunk Rating",
    "",
    "Standing Dunk",
    "Standing_dunk_PG",
    "Standing_dunk_FGM",
    "dunkScore_total",
    "dunkScore_average",
    "dunkScore_max",
    "Putback Frequency%",
    "Putback Scoring Impact Per 75 Possessions",
    "Stable Putback Points Per 75",
    "Offensive Rebound",
    "Offensive Rebounding Crashing Skill",
    "PLAYER_HEIGHT_INCHES",
    "Putback per Offensive Rebound",
]


def parse_metric_value(value: object) -> Optional[float]:
    text = "" if value is None else str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except Exception:
        return None


def read_dict_rows(
    path: Path,
    delimiter: str = ",",
    encoding: str = "utf-8",
) -> List[Dict[str, str]]:
    with path.open("r", newline="", encoding=encoding) as fh:
        return [dict(row) for row in csv.DictReader(fh, delimiter=delimiter)]


def write_matrix_csv(path: Path, headers: Sequence[str], rows: Sequence[Sequence[object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(list(headers))
        writer.writerows(rows)


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


def standing_dunk_rating(value: float, population: Sequence[float]) -> float:
    minimum = min(population)
    p25 = percentile_inc(population, 0.25)
    p40 = percentile_inc(population, 0.40)
    p55 = percentile_inc(population, 0.55)
    p70 = percentile_inc(population, 0.70)
    p85 = percentile_inc(population, 0.85)
    p95 = percentile_inc(population, 0.95)
    p99 = percentile_inc(population, 0.99)
    maximum = percentile_inc(population, 1.0)

    if value <= p25:
        return interpolate_rating(value, minimum, p25, 25.0, 30.0)
    if value <= p40:
        return interpolate_rating(value, p25, p40, 30.0, 40.0)
    if value <= p55:
        return interpolate_rating(value, p40, p55, 40.0, 50.0)
    if value <= p70:
        return interpolate_rating(value, p55, p70, 50.0, 65.0)
    if value <= p85:
        return interpolate_rating(value, p70, p85, 65.0, 80.0)
    if value <= p95:
        return interpolate_rating(value, p85, p95, 80.0, 90.0)
    if value <= p99:
        return interpolate_rating(value, p95, p99, 90.0, 96.0)
    return interpolate_rating(value, p99, maximum, 96.0, 100.0)


def average_numeric(values: Sequence[Optional[float]]) -> Optional[float]:
    numeric = [value for value in values if value is not None]
    if not numeric:
        return None
    return statistics.mean(numeric)


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


def standardize_rows(
    rows: Sequence[Dict[str, str]],
    season_column: str,
    player_column: str,
) -> List[Dict[str, str]]:
    standardized: List[Dict[str, str]] = []
    for row in rows:
        merged = dict(row)
        merged["Season"] = str(row.get(season_column, "")).strip()
        merged["PLAYER_NAME"] = str(row.get(player_column, "")).strip()
        merged.setdefault("PLAYER_ID", "")
        standardized.append(merged)
    return standardized


def build_nonzero_season_coverage(
    rows: Sequence[Dict[str, str]],
    metric_column: str,
) -> set[str]:
    seasons: set[str] = set()
    for row in rows:
        season = str(row.get("Season", "")).strip()
        value = parse_metric_value(row.get(metric_column, ""))
        if season and value is not None and value > 0:
            seasons.add(season)
    return seasons


def build_standing_dunk_sources(
    shot_rows: Sequence[Dict[str, str]],
    gp_rows: Sequence[Dict[str, str]],
) -> Tuple[List[Dict[str, str]], List[Dict[str, str]], List[str]]:
    if not shot_rows:
        return [], [], []

    gp_by_id, gp_by_name = build_source_index(gp_rows)
    fieldnames = list(shot_rows[0].keys())
    standing_columns = [
        column
        for column in fieldnames
        if column.endswith("_FGM")
        and "Dunk" in column
        and "Driving" not in column
        and "Running" not in column
    ]

    standing_fgm_rows: List[Dict[str, str]] = []
    standing_pg_rows: List[Dict[str, str]] = []

    for row in shot_rows:
        season = str(row.get("Season", "")).strip()
        player = str(row.get("PLAYER_NAME", "")).strip()
        if not season or not player:
            continue

        total_fgm = 0.0
        for column in standing_columns:
            total_fgm += parse_metric_value(row.get(column, "")) or 0.0

        lookup_row = row
        gp = None
        if lookup_row is not None:
            candidate_row = gp_by_name.get((season, normalize_name(player)))
            if candidate_row is not None:
                gp = parse_metric_value(candidate_row.get("GP", ""))
        if gp is None:
            # Fall back to exact scan via standardized names when the row did not exist in the index keys above.
            for candidate in gp_rows:
                if str(candidate.get("Season", "")).strip() == season and str(
                    candidate.get("PLAYER_NAME", "")
                ).strip() == player:
                    gp = parse_metric_value(candidate.get("GP", ""))
                    break

        standing_fgm_rows.append(
            {
                "Season": season,
                "PLAYER_NAME": player,
                "Standing_dunk_FGM": total_fgm,
            }
        )
        standing_pg_rows.append(
            {
                "Season": season,
                "PLAYER_NAME": player,
                "Standing_dunk_PG": (total_fgm / gp) if gp not in (None, 0) else "",
            }
        )

    return standing_pg_rows, standing_fgm_rows, standing_columns


def compute_metric_result(
    contexts: Sequence[PlayerContext],
    rows: Sequence[Dict[str, str]],
    metric_column: str,
    current_season: str,
    current_season_min_threshold: float,
    standard_min_threshold: float,
    allow_id_fallback: bool,
    apply_penalty: bool = True,
    pass_through: bool = False,
    default_zero_when_missing: bool = False,
    default_zero_nonzero_seasons: Optional[set[str]] = None,
) -> MetricResult:
    if not rows:
        return MetricResult(
            raw_values=[None] * len(contexts),
            output_values=[None] * len(contexts),
            matched_by=[""] * len(contexts),
            mean_value=None,
            stdev_value=None,
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
        raw_value = (
            None
            if source_row is None
            else parse_metric_value(source_row.get(metric_column, ""))
        )
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

    if pass_through:
        return MetricResult(
            raw_values=raw_values,
            output_values=raw_values[:],
        matched_by=matched_by_values,
        mean_value=None,
        stdev_value=None,
    )

    if default_zero_when_missing:
        source_numeric_values = matched_numeric_values[:]
    else:
        source_numeric_values = [
            value
            for value in (parse_metric_value(row.get(metric_column, "")) for row in rows)
            if value is not None
        ]
    if len(source_numeric_values) < 2:
        return MetricResult(
            raw_values=raw_values,
            output_values=[None] * len(contexts),
            matched_by=matched_by_values,
            mean_value=None,
            stdev_value=None,
        )

    mean_value = statistics.mean(source_numeric_values)
    stdev_value = statistics.stdev(source_numeric_values)
    if stdev_value == 0:
        return MetricResult(
            raw_values=raw_values,
            output_values=[None] * len(contexts),
            matched_by=matched_by_values,
            mean_value=mean_value,
            stdev_value=stdev_value,
        )

    output_values: List[Optional[float]] = []
    for context, raw_value in zip(contexts, raw_values):
        raw_z = compute_capped_z_score(raw_value, mean_value, stdev_value)
        if raw_z is None:
            output_values.append(None)
        elif apply_penalty:
            output_values.append(
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
        else:
            output_values.append(raw_z)

    return MetricResult(
        raw_values=raw_values,
        output_values=output_values,
        matched_by=matched_by_values,
        mean_value=mean_value,
        stdev_value=stdev_value,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build the Finishing -> Standing Dunk rating export."
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
        help="Concatenated shot type CSV used to derive standing dunk counts.",
    )
    parser.add_argument(
        "--standing-dunk-stats-source",
        default=str(HISTORY_DIR / "standing_dunk_stats.csv"),
        help="Combined standing dunk score stats CSV.",
    )
    parser.add_argument(
        "--bball-index-source",
        default=str(HISTORY_DIR / "bball_index_standing_dunk.csv"),
        help="CSV file containing the standing dunk bball-index metrics.",
    )
    parser.add_argument(
        "--rebounding-source",
        default=str(MANUAL_DIR / "rebounding.csv"),
        help="Tab-delimited manual Offensive Rebound export.",
    )
    parser.add_argument(
        "--bios-source",
        default=str(HISTORY_DIR / "bios.csv"),
        help="History CSV used for PLAYER_HEIGHT_INCHES.",
    )
    parser.add_argument(
        "--output-prefix",
        default="finishing_standing_dunk",
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
    standing_dunk_stats_source_path = Path(args.standing_dunk_stats_source)
    bball_index_source_path = Path(args.bball_index_source)
    rebounding_source_path = Path(args.rebounding_source)
    bios_source_path = Path(args.bios_source)

    required_paths = [
        minutes_source_path,
        shot_source_path,
        standing_dunk_stats_source_path,
        rebounding_source_path,
        bios_source_path,
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
    standing_pg_rows, standing_fgm_rows, standing_columns = build_standing_dunk_sources(
        shot_rows,
        gp_rows,
    )

    standing_dunk_stats_rows = standardize_rows(
        read_dict_rows(standing_dunk_stats_source_path, encoding="utf-8-sig"),
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
    rebounding_rows = standardize_rows(
        read_dict_rows(rebounding_source_path, delimiter="\t"),
        season_column="Season",
        player_column="Player",
    )
    bios_rows = standardize_rows(
        read_history_csv(bios_source_path),
        season_column="Season",
        player_column="PLAYER_NAME",
    )

    metric_results = {
        "Standing_dunk_PG": compute_metric_result(
            contexts,
            standing_pg_rows,
            "Standing_dunk_PG",
            current_season,
            args.current_season_min_threshold,
            args.standard_min_threshold,
            args.allow_id_fallback,
            apply_penalty=True,
        ),
        "Standing_dunk_FGM": compute_metric_result(
            contexts,
            standing_fgm_rows,
            "Standing_dunk_FGM",
            current_season,
            args.current_season_min_threshold,
            args.standard_min_threshold,
            args.allow_id_fallback,
            apply_penalty=True,
            default_zero_when_missing=True,
            default_zero_nonzero_seasons=build_nonzero_season_coverage(
                standing_fgm_rows,
                "Standing_dunk_FGM",
            ),
        ),
        "dunkScore_total": compute_metric_result(
            contexts,
            standing_dunk_stats_rows,
            "dunkScore_total",
            current_season,
            args.current_season_min_threshold,
            args.standard_min_threshold,
            args.allow_id_fallback,
            apply_penalty=True,
        ),
        "dunkScore_average": compute_metric_result(
            contexts,
            standing_dunk_stats_rows,
            "dunkScore_average",
            current_season,
            args.current_season_min_threshold,
            args.standard_min_threshold,
            args.allow_id_fallback,
            apply_penalty=True,
        ),
        "dunkScore_max": compute_metric_result(
            contexts,
            standing_dunk_stats_rows,
            "dunkScore_max",
            current_season,
            args.current_season_min_threshold,
            args.standard_min_threshold,
            args.allow_id_fallback,
            apply_penalty=True,
        ),
        "Putback Scoring Impact Per 75 Possessions": compute_metric_result(
            contexts,
            bball_index_rows,
            "Putback Scoring Impact Per 75 Possessions",
            current_season,
            args.current_season_min_threshold,
            args.standard_min_threshold,
            args.allow_id_fallback,
            apply_penalty=True,
        ),
        "Stable Putback Points Per 75": compute_metric_result(
            contexts,
            bball_index_rows,
            "Stable Putback Points Per 75",
            current_season,
            args.current_season_min_threshold,
            args.standard_min_threshold,
            args.allow_id_fallback,
            apply_penalty=True,
        ),
        "Offensive Rebound": compute_metric_result(
            contexts,
            rebounding_rows,
            "Offensive Rebound",
            current_season,
            args.current_season_min_threshold,
            args.standard_min_threshold,
            args.allow_id_fallback,
            pass_through=True,
        ),
        "Offensive Rebounding Crashing Skill": compute_metric_result(
            contexts,
            bball_index_rows,
            "Offensive Rebounding Crashing Skill",
            current_season,
            args.current_season_min_threshold,
            args.standard_min_threshold,
            args.allow_id_fallback,
            apply_penalty=True,
        ),
        "PLAYER_HEIGHT_INCHES": compute_metric_result(
            contexts,
            bios_rows,
            "PLAYER_HEIGHT_INCHES",
            current_season,
            args.current_season_min_threshold,
            args.standard_min_threshold,
            args.allow_id_fallback,
            apply_penalty=False,
        ),
    }

    standing_scores: List[float] = []
    aggregate_rows: List[List[Optional[float]]] = []
    for index in range(len(contexts)):
        group_one = [
            metric_results["Standing_dunk_PG"].output_values[index],
            metric_results["Standing_dunk_FGM"].output_values[index],
            metric_results["dunkScore_total"].output_values[index],
        ]
        group_two = [
            metric_results["dunkScore_average"].output_values[index],
            metric_results["dunkScore_max"].output_values[index],
            None,
            metric_results["Putback Scoring Impact Per 75 Possessions"].output_values[index],
            metric_results["Stable Putback Points Per 75"].output_values[index],
            metric_results["Offensive Rebound"].output_values[index],
            metric_results["Offensive Rebounding Crashing Skill"].output_values[index],
            metric_results["PLAYER_HEIGHT_INCHES"].output_values[index],
        ]
        putback_per_off_reb = None

        group_one_average = average_numeric(group_one)
        group_two_average = average_numeric(group_two)
        if group_one_average is None or group_two_average is None:
            standing_score = -1.0
        else:
            standing_score = (
                group_one_average * 0.7
                + group_two_average * 0.25
                + (putback_per_off_reb or 0.0) * 0.05
            )
        standing_scores.append(standing_score)
        aggregate_rows.append(
            [
                metric_results["Standing_dunk_PG"].output_values[index],
                metric_results["Standing_dunk_FGM"].output_values[index],
                metric_results["dunkScore_total"].output_values[index],
                metric_results["dunkScore_average"].output_values[index],
                metric_results["dunkScore_max"].output_values[index],
                None,
                metric_results["Putback Scoring Impact Per 75 Possessions"].output_values[index],
                metric_results["Stable Putback Points Per 75"].output_values[index],
                metric_results["Offensive Rebound"].output_values[index],
                metric_results["Offensive Rebounding Crashing Skill"].output_values[index],
                metric_results["PLAYER_HEIGHT_INCHES"].output_values[index],
                putback_per_off_reb,
            ]
        )

    standing_median = statistics.median(standing_scores)
    standing_stdev = statistics.stdev(standing_scores)
    aggregate_z_scores = [
        (value - standing_median) / standing_stdev for value in standing_scores
    ]
    standing_ratings = [
        standing_dunk_rating(value, aggregate_z_scores)
        for value in aggregate_z_scores
    ]

    sheet_rows: List[List[object]] = []
    rating_only_rows: List[Dict[str, object]] = []
    audit_rows: List[Dict[str, object]] = []
    unmatched_rows: List[Dict[str, object]] = []

    component_names = [
        "Standing_dunk_PG",
        "Standing_dunk_FGM",
        "dunkScore_total",
        "dunkScore_average",
        "dunkScore_max",
        "Putback Frequency%",
        "Putback Scoring Impact Per 75 Possessions",
        "Stable Putback Points Per 75",
        "Offensive Rebound",
        "Offensive Rebounding Crashing Skill",
        "PLAYER_HEIGHT_INCHES",
        "Putback per Offensive Rebound",
    ]

    for index, context in enumerate(contexts):
        component_values = aggregate_rows[index]
        missing_metrics = [
            name
            for name, value in zip(component_names, component_values)
            if value is None and name not in {"Putback Frequency%", "Putback per Offensive Rebound"}
        ]

        sheet_rows.append(
            [
                context.universe_row.nba_id,
                context.universe_row.season,
                context.universe_row.player,
                standing_ratings[index],
                aggregate_z_scores[index],
                standing_scores[index],
                *component_values,
            ]
        )
        rating_only_rows.append(
            {
                "NBA_ID": context.universe_row.nba_id,
                "Season": context.universe_row.season,
                "Player": context.universe_row.player,
                "Standing Dunk Rating": standing_ratings[index],
            }
        )

        audit_row: Dict[str, object] = {
            "NBA_ID": context.universe_row.nba_id,
            "Season": context.universe_row.season,
            "Player": context.universe_row.player,
            "RotationRole": context.universe_row.rotation_role,
            "MIN": context.effective_minutes,
            "Standing Dunk": standing_scores[index],
            "Standing Dunk Aggregate Z": aggregate_z_scores[index],
            "Standing Dunk Rating": standing_ratings[index],
            "MissingMetricCount": len(missing_metrics),
            "MissingMetrics": " | ".join(missing_metrics),
        }
        for metric_name, result in metric_results.items():
            audit_row[f"{metric_name} Raw"] = result.raw_values[index]
            audit_row[f"{metric_name} Z"] = result.output_values[index]
            audit_row[f"{metric_name} MatchedBy"] = result.matched_by[index]
        audit_row["Putback Frequency% Z"] = None
        audit_row["Putback per Offensive Rebound Z"] = None
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

    output_prefix = args.output_prefix.strip() or "finishing_standing_dunk"
    sheet_path = EXPORT_DIR / f"{output_prefix}_sheet.csv"
    rating_only_path = EXPORT_DIR / f"{output_prefix}_rating_only.csv"
    audit_path = EXPORT_DIR / f"{output_prefix}_audit.csv"
    unmatched_path = EXPORT_DIR / f"{output_prefix}_unmatched.csv"

    write_matrix_csv(sheet_path, WORKBOOK_COLUMNS, sheet_rows)
    write_csv(
        rating_only_path,
        ["NBA_ID", "Season", "Player", "Standing Dunk Rating"],
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

    print(f"[OK] Built Standing Dunk export for {len(sheet_rows)} player-season rows")
    print(f"[INFO] Standing dunk shot columns used: {', '.join(standing_columns)}")
    print(f"[OUT] Sheet -> {sheet_path}")
    print(f"[OUT] Rating only -> {rating_only_path}")
    print(f"[OUT] Audit -> {audit_path}")
    print(f"[OUT] Unmatched -> {unmatched_path}")


if __name__ == "__main__":
    main()
