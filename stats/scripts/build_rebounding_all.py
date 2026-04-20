from __future__ import annotations

import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from build_cal_lane import (
    CalUniverseRow,
    apply_cal_normalization,
    build_source_index,
    canonical_id,
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
    write_csv,
)
from build_finishing_layup import (
    ComponentSlot,
    build_player_contexts,
    interpolate_rating,
    percentile_inc,
)
from build_finishing_standing_dunk import standardize_rows, write_matrix_csv
from pull_nba_stats import EXPORT_DIR, HISTORY_DIR, MANUAL_DIR


OFFENSIVE_REBOUND_CURVE: Sequence[Tuple[float, float]] = (
    (0.05, 29.0),
    (0.10, 32.0),
    (0.15, 34.0),
    (0.22, 36.0),
    (0.30, 40.0),
    (0.38, 43.0),
    (0.48, 48.0),
    (0.58, 55.0),
    (0.68, 63.0),
    (0.78, 70.0),
    (0.88, 79.0),
    (0.96, 89.0),
    (1.00, 100.0),
)

DEFENSIVE_REBOUND_CURVE: Sequence[Tuple[float, float]] = (
    (0.06, 37.0),
    (0.12, 41.0),
    (0.20, 45.0),
    (0.30, 49.0),
    (0.40, 53.0),
    (0.50, 59.0),
    (0.60, 64.0),
    (0.70, 69.0),
    (0.78, 74.0),
    (0.86, 80.0),
    (0.92, 85.0),
    (0.97, 92.0),
    (1.00, 100.0),
)


OFFENSIVE_COMPONENTS: Sequence[ComponentSlot] = (
    ComponentSlot("Offensive Rebounding Talent", "Offensive Rebounding Talent"),
    ComponentSlot(
        "Offensive Rebounding Chances Per 75 Possessions",
        "Offensive Rebounding Chances Per 75 Possessions",
    ),
    ComponentSlot("Offensive Rebounding Crashing Skill", "Offensive Rebounding Crashing Skill"),
    ComponentSlot("Offensive Rebounds Per Game", "Offensive Rebounds Per Game"),
    ComponentSlot("Stable Offensive Rebounds Per 75", "Stable Offensive Rebounds Per 75"),
    ComponentSlot("OREB", "OREB"),
    ComponentSlot("OREB_PCT", "OREB_PCT"),
    ComponentSlot("OFF_BOXOUTS", "OFF_BOXOUTS"),
    ComponentSlot("OREB_CONTEST", "OREB_CONTEST"),
    ComponentSlot("Offensive Rebounding Conversion Skill", "Offensive Rebounding Conversion Skill"),
    ComponentSlot("SelfORebPct", "SelfORebPct"),
    ComponentSlot("PCT_BOX_OUTS_OFF", "PCT_BOX_OUTS_OFF"),
    ComponentSlot(
        "Adjusted Offensive Rebounding Success Rate",
        "Adjusted Offensive Rebounding Success Rate",
    ),
    ComponentSlot(
        "Percentage of Offensive Rebounds Contested",
        "Percentage of Offensive Rebounds Contested",
    ),
    ComponentSlot("OREB_CHANCE_PCT_ADJ", "OREB_CHANCE_PCT_ADJ"),
)

DEFENSIVE_COMPONENTS: Sequence[ComponentSlot] = (
    ComponentSlot("Defensive Rebounding Talent", "Defensive Rebounding Talent"),
    ComponentSlot("Defensive Reb Per 75 Possessions", "Defensive Reb Per 75 Possessions"),
    ComponentSlot(
        "Defensive Rebounding Conversion Skill",
        "Defensive Rebounding Conversion Skill",
    ),
    ComponentSlot("Defensive Rebounding Crashing Skill", "Defensive Rebounding Crashing Skill"),
    ComponentSlot("Defensive Rebounds Per Game", "Defensive Rebounds Per Game"),
    ComponentSlot("Stable Defensive Rebounds Per 75", "Stable Defensive Rebounds Per 75"),
    ComponentSlot("DREB", "DREB"),
    ComponentSlot("DREB_PCT", "DREB_PCT"),
    ComponentSlot("DEF_BOXOUTS", "DEF_BOXOUTS"),
    ComponentSlot("DREB_CONTEST", "DREB_CONTEST"),
    ComponentSlot(
        "Percentage of Defensive Rebounds Contested",
        "Percentage of Defensive Rebounds Contested",
    ),
    # The workbook repeats this offensive metric in the defensive section, so we mirror it.
    ComponentSlot(
        "Offensive Rebounding Chances Per 75 Possessions",
        "Offensive Rebounding Chances Per 75 Possessions",
    ),
    ComponentSlot("PCT_BOX_OUTS_DEF", "PCT_BOX_OUTS_DEF"),
    ComponentSlot(
        "Adjusted Defensive Rebounding Success Rate",
        "Adjusted Defensive Rebounding Success Rate",
    ),
    ComponentSlot("DREB_CHANCE_PCT_ADJ", "DREB_CHANCE_PCT_ADJ"),
)


WORKBOOK_COLUMNS = [
    "NBA ID",
    "Season",
    "Player",
    "Offensive Rebound Rating",
    "",
    "Offensive Rebound",
    *[slot.header for slot in OFFENSIVE_COMPONENTS],
    "Defensive Rebound Rating",
    "",
    "Defensive Rebound",
    *[slot.header for slot in DEFENSIVE_COMPONENTS],
]


@dataclass(frozen=True)
class SectionSpec:
    name: str
    rating_header: str
    rating_only_header: str
    component_slots: Sequence[ComponentSlot]
    weighted_groups: Sequence[Tuple[Sequence[str], float]]
    curve: Sequence[Tuple[float, float]]


@dataclass
class MetricResult:
    raw_values: List[Optional[float]]
    normalized_values: List[Optional[float]]
    matched_by: List[str]
    mean_value: Optional[float]
    stdev_value: Optional[float]
    source_note: str


OFFENSIVE_SECTION = SectionSpec(
    name="Offensive Rebound",
    rating_header="Offensive Rebound Rating",
    rating_only_header="Offensive Rebound Rating",
    component_slots=OFFENSIVE_COMPONENTS,
    weighted_groups=(
        (tuple(slot.metric_alias for slot in OFFENSIVE_COMPONENTS[:9]), 0.85),
        (tuple(slot.metric_alias for slot in OFFENSIVE_COMPONENTS[9:12]), 0.10),
        (tuple(slot.metric_alias for slot in OFFENSIVE_COMPONENTS[12:]), 0.05),
    ),
    curve=OFFENSIVE_REBOUND_CURVE,
)

DEFENSIVE_SECTION = SectionSpec(
    name="Defensive Rebound",
    rating_header="Defensive Rebound Rating",
    rating_only_header="Defensive Rebound Rating",
    component_slots=DEFENSIVE_COMPONENTS,
    weighted_groups=(
        (tuple(slot.metric_alias for slot in DEFENSIVE_COMPONENTS[:10]), 0.85),
        (tuple(slot.metric_alias for slot in DEFENSIVE_COMPONENTS[10:13]), 0.10),
        (tuple(slot.metric_alias for slot in DEFENSIVE_COMPONENTS[13:]), 0.05),
    ),
    curve=DEFENSIVE_REBOUND_CURVE,
)

SECTIONS: Sequence[SectionSpec] = (
    OFFENSIVE_SECTION,
    DEFENSIVE_SECTION,
)

RATING_ONLY_HEADERS = [
    "NBA_ID",
    "Season",
    "Player",
    "Offensive Rebound Rating",
    "Defensive Rebound Rating",
]

B_BALL_SOURCE_NOTE = "bball_index_rebounding.csv"

METRIC_SOURCE_NOTES: Dict[str, str] = {
    "Offensive Rebounding Talent": B_BALL_SOURCE_NOTE,
    "Offensive Rebounding Chances Per 75 Possessions": B_BALL_SOURCE_NOTE,
    "Offensive Rebounding Crashing Skill": B_BALL_SOURCE_NOTE,
    "Offensive Rebounds Per Game": "general_traditional.csv -> OREB",
    "Stable Offensive Rebounds Per 75": B_BALL_SOURCE_NOTE,
    "OREB": "tracking_rebound.csv -> OREB",
    "OREB_PCT": "general_advanced.csv -> OREB_PCT",
    "OFF_BOXOUTS": "hustle.csv -> OFF_BOXOUTS",
    "OREB_CONTEST": "tracking_rebound.csv -> OREB_CONTEST",
    "Offensive Rebounding Conversion Skill": B_BALL_SOURCE_NOTE,
    "SelfORebPct": "nbarapm.csv -> SelfORebPct",
    "PCT_BOX_OUTS_OFF": "hustle.csv -> PCT_BOX_OUTS_OFF",
    "Adjusted Offensive Rebounding Success Rate": B_BALL_SOURCE_NOTE,
    "Percentage of Offensive Rebounds Contested": B_BALL_SOURCE_NOTE,
    "OREB_CHANCE_PCT_ADJ": "tracking_rebound.csv -> OREB_CHANCE_PCT_ADJ",
    "Defensive Rebounding Talent": B_BALL_SOURCE_NOTE,
    "Defensive Reb Per 75 Possessions": B_BALL_SOURCE_NOTE,
    "Defensive Rebounding Conversion Skill": B_BALL_SOURCE_NOTE,
    "Defensive Rebounding Crashing Skill": B_BALL_SOURCE_NOTE,
    "Defensive Rebounds Per Game": "general_traditional.csv -> DREB",
    "Stable Defensive Rebounds Per 75": B_BALL_SOURCE_NOTE,
    "DREB": "tracking_rebound.csv -> DREB",
    "DREB_PCT": "general_advanced.csv -> DREB_PCT",
    "DEF_BOXOUTS": "hustle.csv -> DEF_BOXOUTS",
    "DREB_CONTEST": "tracking_rebound.csv -> DREB_CONTEST",
    "Percentage of Defensive Rebounds Contested": B_BALL_SOURCE_NOTE,
    "PCT_BOX_OUTS_DEF": "hustle.csv -> PCT_BOX_OUTS_DEF",
    "Adjusted Defensive Rebounding Success Rate": B_BALL_SOURCE_NOTE,
    "DREB_CHANCE_PCT_ADJ": "tracking_rebound.csv -> DREB_CHANCE_PCT_ADJ",
}


def parse_args():
    import argparse

    parser = argparse.ArgumentParser(
        description="Build the combined Rebounding ratings export."
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
    parser.add_argument("--sheet", default="Cal", help="Workbook sheet used for role/minute fallback.")
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
    parser.add_argument("--minutes-column", default="MIN")
    parser.add_argument("--minutes-games-column", default="GP")
    parser.add_argument("--current-season", default="")
    parser.add_argument("--current-season-min-threshold", type=float, default=200.0)
    parser.add_argument("--standard-min-threshold", type=float, default=1000.0)
    parser.add_argument("--allow-id-fallback", action="store_true")
    parser.add_argument(
        "--bball-rebounding-source",
        default=str(HISTORY_DIR / "bball_index_rebounding.csv"),
    )
    parser.add_argument(
        "--tracking-rebound-source",
        default=str(HISTORY_DIR / "tracking_rebound.csv"),
    )
    parser.add_argument(
        "--general-traditional-source",
        default=str(HISTORY_DIR / "general_traditional.csv"),
    )
    parser.add_argument(
        "--general-advanced-source",
        default=str(HISTORY_DIR / "general_advanced.csv"),
    )
    parser.add_argument("--hustle-source", default=str(HISTORY_DIR / "hustle.csv"))
    parser.add_argument("--nbarapm-source", default=str(HISTORY_DIR / "nbarapm.csv"))
    parser.add_argument("--output-prefix", default="rebounding_all")
    return parser.parse_args()


def load_bball_detail_rows(rows: Sequence[Dict[str, str]]) -> List[CalUniverseRow]:
    detail_rows: List[CalUniverseRow] = []
    for row in rows:
        season = str(row.get("Season", "")).strip()
        player = str(row.get("Player", "")).strip()
        if not season or not player:
            continue
        detail_rows.append(
            CalUniverseRow(
                nba_id=canonical_id(row.get("NBA_ID", row.get("NBA ID", ""))),
                season=season,
                player=player,
                rotation_role=str(row.get("Rotation Role", "")).strip(),
                minutes=parse_float(row.get("Minutes", "")),
            )
        )
    return detail_rows


def load_bball_index_rows(path: Path) -> List[Dict[str, object]]:
    raw_rows = read_history_csv(path)
    metric_columns = {
        "Offensive Rebounding Talent",
        "Offensive Rebounding Chances Per 75 Possessions",
        "Offensive Rebounding Crashing Skill",
        "Stable Offensive Rebounds Per 75",
        "Offensive Rebounding Conversion Skill",
        "Adjusted Offensive Rebounding Success Rate",
        "Percentage of Offensive Rebounds Contested",
        "Defensive Rebounding Talent",
        "Defensive Reb Per 75 Possessions",
        "Defensive Rebounding Conversion Skill",
        "Defensive Rebounding Crashing Skill",
        "Stable Defensive Rebounds Per 75",
        "Percentage of Defensive Rebounds Contested",
        "Adjusted Defensive Rebounding Success Rate",
    }

    standardized: List[Dict[str, object]] = []
    for row in raw_rows:
        season = str(row.get("Season", "")).strip()
        player = str(row.get("Player", "")).strip()
        if not season or not player:
            continue

        merged: Dict[str, object] = {
            "Season": season,
            "PLAYER_ID": canonical_id(row.get("NBA_ID", row.get("NBA ID", ""))),
            "PLAYER_NAME": player,
            "TEAM_ABBREVIATION": str(row.get("Team(s)", row.get("Team", ""))).strip(),
        }
        for metric in metric_columns:
            merged[metric] = parse_float(row.get(metric, ""))
        standardized.append(merged)

    return standardized


def load_nbarapm_rows(path: Path) -> List[Dict[str, object]]:
    raw_rows = read_history_csv(path)
    standardized: List[Dict[str, object]] = []

    for row in raw_rows:
        season = str(row.get("Season", "")).strip()
        player = str(row.get("Player", "")).strip()
        if not season or not player:
            continue

        standardized.append(
            {
                "Season": season,
                "PLAYER_ID": canonical_id(row.get("NBA_ID", "")),
                "PLAYER_NAME": player,
                "TEAM_ABBREVIATION": "",
                "SelfORebPct": parse_float(row.get("SelfORebPct", "")),
            }
        )

    return standardized


def get_row_key(row: CalUniverseRow) -> Tuple[str, str]:
    return row.season, normalize_name(row.player)


def set_base_value(base_row: Dict[str, object], alias: str, value: object, matched_by: str) -> None:
    if value is None:
        return
    text = str(value).strip()
    if not text:
        return
    base_row[alias] = value
    base_row[f"__matched__{alias}"] = matched_by


def merge_metric_columns(
    universe: Sequence[CalUniverseRow],
    base_rows_by_key: Dict[Tuple[str, str], Dict[str, object]],
    source_rows: Sequence[Dict[str, object]],
    column_map: Dict[str, str],
    allow_id_fallback: bool,
) -> None:
    if not source_rows:
        return

    by_id, by_name = build_source_index(list(source_rows))
    for universe_row in universe:
        source_row, matched_by = match_metric_row(
            universe_row,
            by_id,
            by_name,
            allow_id_fallback=allow_id_fallback,
        )
        if source_row is None:
            continue

        base_row = base_rows_by_key[get_row_key(universe_row)]
        if not str(base_row.get("TEAM_ABBREVIATION", "")).strip():
            team_value = str(source_row.get("TEAM_ABBREVIATION", "")).strip()
            if team_value:
                base_row["TEAM_ABBREVIATION"] = team_value

        for alias, source_column in column_map.items():
            set_base_value(base_row, alias, source_row.get(source_column, None), matched_by)


def build_metric_result(
    contexts,
    base_rows_by_key: Dict[Tuple[str, str], Dict[str, object]],
    metric_alias: str,
    current_season: str,
    current_season_min_threshold: float,
    standard_min_threshold: float,
) -> MetricResult:
    raw_values: List[Optional[float]] = []
    normalized_values: List[Optional[float]] = []
    matched_by_values: List[str] = []
    matched_numeric_values: List[float] = []

    for context in contexts:
        base_row = base_rows_by_key[get_row_key(context.universe_row)]
        raw_value = parse_float(base_row.get(metric_alias, ""))
        matched_by = str(base_row.get(f"__matched__{metric_alias}", "")).strip()
        raw_values.append(raw_value)
        matched_by_values.append(matched_by)
        if raw_value is not None:
            matched_numeric_values.append(raw_value)

    mean_value = None if not matched_numeric_values else statistics.mean(matched_numeric_values)
    stdev_value = (
        statistics.stdev(matched_numeric_values)
        if len(matched_numeric_values) >= 2
        else None
    )

    for index, context in enumerate(contexts):
        raw_value = raw_values[index]
        if raw_value is None or mean_value is None or stdev_value in (None, 0):
            normalized_values.append(None)
            continue

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
        source_note=METRIC_SOURCE_NOTES.get(metric_alias, "merged rebounding sources"),
    )


def average_numeric(values: Sequence[Optional[float]]) -> Optional[float]:
    numeric = [value for value in values if value is not None]
    if not numeric:
        return None
    return statistics.mean(numeric)


def compute_section_score(
    section: SectionSpec,
    component_values_by_alias: Dict[str, Optional[float]],
) -> float:
    weighted_values: List[float] = []
    total_weight = 0.0

    for aliases, weight in section.weighted_groups:
        group_average = average_numeric([component_values_by_alias.get(alias) for alias in aliases])
        if group_average is None:
            continue
        weighted_values.append(group_average * weight)
        total_weight += weight

    if not weighted_values or total_weight <= 0:
        return -1.0
    return sum(weighted_values) / total_weight


def compute_piecewise_rating(
    value: float,
    population: Sequence[float],
    curve: Sequence[Tuple[float, float]],
) -> float:
    minimum = min(population)
    low_x = minimum
    low_y = 25.0

    for percentile, high_y in curve:
        high_x = percentile_inc(population, percentile)
        if value <= high_x or percentile >= 1.0:
            return interpolate_rating(value, low_x, high_x, low_y, high_y)
        low_x = high_x
        low_y = high_y

    return curve[-1][1]


def build_section_outputs(
    section: SectionSpec,
    contexts,
    metric_results: Dict[str, MetricResult],
) -> Tuple[List[float], List[float], List[float], List[List[Optional[float]]]]:
    scores: List[float] = []
    component_rows: List[List[Optional[float]]] = []

    for index in range(len(contexts)):
        component_values_by_alias: Dict[str, Optional[float]] = {}
        component_row: List[Optional[float]] = []
        for slot in section.component_slots:
            value = metric_results[slot.metric_alias].normalized_values[index]
            component_values_by_alias[slot.metric_alias] = value
            component_row.append(value)

        scores.append(compute_section_score(section, component_values_by_alias))
        component_rows.append(component_row)

    if len(scores) < 2:
        raise SystemExit(f"Not enough {section.name} scores to compute aggregate z-scores.")

    median_value = statistics.median(scores)
    stdev_value = statistics.stdev(scores)
    if stdev_value == 0:
        raise SystemExit(f"{section.name} scores have zero variance; cannot compute ratings.")

    aggregate_z_scores = [(value - median_value) / stdev_value for value in scores]
    ratings = [
        compute_piecewise_rating(value, aggregate_z_scores, section.curve)
        for value in aggregate_z_scores
    ]
    return scores, aggregate_z_scores, ratings, component_rows


def main() -> None:
    args = parse_args()

    workbook_path = Path(args.workbook)
    universe_path = Path(args.universe_csv)
    details_path = resolve_details_csv_path(args.details_csv, universe_path)
    minutes_source_path = Path(args.minutes_source)
    bball_rebounding_source_path = Path(args.bball_rebounding_source)
    tracking_rebound_source_path = Path(args.tracking_rebound_source)
    general_traditional_source_path = Path(args.general_traditional_source)
    general_advanced_source_path = Path(args.general_advanced_source)
    hustle_source_path = Path(args.hustle_source)
    nbarapm_source_path = Path(args.nbarapm_source)

    required_paths = (
        minutes_source_path,
        bball_rebounding_source_path,
        tracking_rebound_source_path,
        general_traditional_source_path,
        general_advanced_source_path,
        hustle_source_path,
        nbarapm_source_path,
    )
    missing_paths = [str(path) for path in required_paths if not path.exists()]
    if missing_paths:
        raise SystemExit("Missing required source files:\n- " + "\n- ".join(missing_paths))

    minutes_rows = read_history_csv(minutes_source_path)
    bball_rebounding_raw_rows = read_history_csv(bball_rebounding_source_path)
    bball_rebounding_rows = load_bball_index_rows(bball_rebounding_source_path)
    tracking_rebound_rows = standardize_rows(
        read_history_csv(tracking_rebound_source_path),
        season_column="Season",
        player_column="PLAYER_NAME",
    )
    general_traditional_rows = standardize_rows(
        read_history_csv(general_traditional_source_path),
        season_column="Season",
        player_column="PLAYER_NAME",
    )
    general_advanced_rows = standardize_rows(
        read_history_csv(general_advanced_source_path),
        season_column="Season",
        player_column="PLAYER_NAME",
    )
    hustle_rows = standardize_rows(
        read_history_csv(hustle_source_path),
        season_column="Season",
        player_column="PLAYER_NAME",
    )
    nbarapm_rows = load_nbarapm_rows(nbarapm_source_path)

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
    if bball_rebounding_raw_rows:
        universe = enrich_universe_rows(universe, load_bball_detail_rows(bball_rebounding_raw_rows))
    if workbook_universe:
        universe = enrich_universe_rows(universe, workbook_universe)

    current_season = detect_current_season(args.current_season, universe, minutes_rows)
    contexts = build_player_contexts(
        universe=universe,
        minutes_rows=minutes_rows,
        minutes_column=args.minutes_column,
        minutes_games_column=args.minutes_games_column,
        allow_id_fallback=args.allow_id_fallback,
    )

    base_rows_by_key: Dict[Tuple[str, str], Dict[str, object]] = {}
    for universe_row in universe:
        base_rows_by_key[get_row_key(universe_row)] = {
            "Season": universe_row.season,
            "PLAYER_NAME": universe_row.player,
            "PLAYER_ID": universe_row.nba_id,
            "NBA_ID": universe_row.nba_id,
            "TEAM_ABBREVIATION": "",
        }

    merge_metric_columns(
        universe,
        base_rows_by_key,
        bball_rebounding_rows,
        {
            "Offensive Rebounding Talent": "Offensive Rebounding Talent",
            "Offensive Rebounding Chances Per 75 Possessions": "Offensive Rebounding Chances Per 75 Possessions",
            "Offensive Rebounding Crashing Skill": "Offensive Rebounding Crashing Skill",
            "Stable Offensive Rebounds Per 75": "Stable Offensive Rebounds Per 75",
            "Offensive Rebounding Conversion Skill": "Offensive Rebounding Conversion Skill",
            "Adjusted Offensive Rebounding Success Rate": "Adjusted Offensive Rebounding Success Rate",
            "Percentage of Offensive Rebounds Contested": "Percentage of Offensive Rebounds Contested",
            "Defensive Rebounding Talent": "Defensive Rebounding Talent",
            "Defensive Reb Per 75 Possessions": "Defensive Reb Per 75 Possessions",
            "Defensive Rebounding Conversion Skill": "Defensive Rebounding Conversion Skill",
            "Defensive Rebounding Crashing Skill": "Defensive Rebounding Crashing Skill",
            "Stable Defensive Rebounds Per 75": "Stable Defensive Rebounds Per 75",
            "Percentage of Defensive Rebounds Contested": "Percentage of Defensive Rebounds Contested",
            "Adjusted Defensive Rebounding Success Rate": "Adjusted Defensive Rebounding Success Rate",
        },
        allow_id_fallback=args.allow_id_fallback,
    )
    merge_metric_columns(
        universe,
        base_rows_by_key,
        tracking_rebound_rows,
        {
            "OREB": "OREB",
            "OREB_CONTEST": "OREB_CONTEST",
            "OREB_CHANCE_PCT_ADJ": "OREB_CHANCE_PCT_ADJ",
            "DREB": "DREB",
            "DREB_CONTEST": "DREB_CONTEST",
            "DREB_CHANCE_PCT_ADJ": "DREB_CHANCE_PCT_ADJ",
        },
        allow_id_fallback=args.allow_id_fallback,
    )
    merge_metric_columns(
        universe,
        base_rows_by_key,
        general_traditional_rows,
        {
            "Offensive Rebounds Per Game": "OREB",
            "Defensive Rebounds Per Game": "DREB",
        },
        allow_id_fallback=args.allow_id_fallback,
    )
    merge_metric_columns(
        universe,
        base_rows_by_key,
        general_advanced_rows,
        {
            "OREB_PCT": "OREB_PCT",
            "DREB_PCT": "DREB_PCT",
        },
        allow_id_fallback=args.allow_id_fallback,
    )
    merge_metric_columns(
        universe,
        base_rows_by_key,
        hustle_rows,
        {
            "OFF_BOXOUTS": "OFF_BOXOUTS",
            "DEF_BOXOUTS": "DEF_BOXOUTS",
            "PCT_BOX_OUTS_OFF": "PCT_BOX_OUTS_OFF",
            "PCT_BOX_OUTS_DEF": "PCT_BOX_OUTS_DEF",
        },
        allow_id_fallback=args.allow_id_fallback,
    )
    merge_metric_columns(
        universe,
        base_rows_by_key,
        nbarapm_rows,
        {"SelfORebPct": "SelfORebPct"},
        allow_id_fallback=args.allow_id_fallback,
    )

    all_metric_aliases = {
        slot.metric_alias
        for section in SECTIONS
        for slot in section.component_slots
    }
    metric_results: Dict[str, MetricResult] = {}
    for metric_alias in sorted(all_metric_aliases):
        metric_results[metric_alias] = build_metric_result(
            contexts=contexts,
            base_rows_by_key=base_rows_by_key,
            metric_alias=metric_alias,
            current_season=current_season,
            current_season_min_threshold=args.current_season_min_threshold,
            standard_min_threshold=args.standard_min_threshold,
        )

    offensive_scores, offensive_z, offensive_ratings, offensive_rows = build_section_outputs(
        section=OFFENSIVE_SECTION,
        contexts=contexts,
        metric_results=metric_results,
    )
    defensive_scores, defensive_z, defensive_ratings, defensive_rows = build_section_outputs(
        section=DEFENSIVE_SECTION,
        contexts=contexts,
        metric_results=metric_results,
    )

    sheet_rows: List[List[object]] = []
    rating_only_rows: List[Dict[str, object]] = []
    audit_rows: List[Dict[str, object]] = []
    unmatched_rows: List[Dict[str, object]] = []

    for index, context in enumerate(contexts):
        base_row = base_rows_by_key[get_row_key(context.universe_row)]
        team_value = str(base_row.get("TEAM_ABBREVIATION", "")).strip()

        sheet_rows.append(
            [
                context.universe_row.nba_id,
                context.universe_row.season,
                context.universe_row.player,
                offensive_ratings[index],
                offensive_z[index],
                offensive_scores[index],
                *offensive_rows[index],
                defensive_ratings[index],
                defensive_z[index],
                defensive_scores[index],
                *defensive_rows[index],
            ]
        )

        rating_only_rows.append(
            {
                "NBA_ID": context.universe_row.nba_id,
                "Season": context.universe_row.season,
                "Player": context.universe_row.player,
                "Offensive Rebound Rating": offensive_ratings[index],
                "Defensive Rebound Rating": defensive_ratings[index],
            }
        )

        audit_row: Dict[str, object] = {
            "NBA_ID": context.universe_row.nba_id,
            "Season": context.universe_row.season,
            "Player": context.universe_row.player,
            "Team(s)": team_value,
            "RotationRole": context.universe_row.rotation_role,
            "MIN": context.effective_minutes,
            "WorkbookMIN": context.workbook_minutes,
            "LiveMIN": context.live_minutes,
            "LiveMINPerGame": context.live_minutes_per_game,
            "LiveGP": context.live_gp,
            "MinutesMatchedBy": context.minutes_matched_by,
            "CurrentSeason": current_season,
            "Offensive Rebound Score": offensive_scores[index],
            "Offensive Rebound AggregateZ": offensive_z[index],
            "Offensive Rebound Rating": offensive_ratings[index],
            "Defensive Rebound Score": defensive_scores[index],
            "Defensive Rebound AggregateZ": defensive_z[index],
            "Defensive Rebound Rating": defensive_ratings[index],
        }

        missing_labels: List[str] = []
        for section in SECTIONS:
            missing_aliases = sorted(
                {
                    slot.metric_alias
                    for slot in section.component_slots
                    if metric_results[slot.metric_alias].normalized_values[index] is None
                }
            )
            if missing_aliases:
                missing_labels.extend(f"{section.name}::{alias}" for alias in missing_aliases)
            audit_row[f"{section.name} MissingCount"] = len(missing_aliases)
            audit_row[f"{section.name} MissingMetrics"] = " | ".join(missing_aliases)

        for metric_alias in sorted(metric_results):
            result = metric_results[metric_alias]
            audit_row[f"{metric_alias} Raw"] = result.raw_values[index]
            audit_row[f"{metric_alias} Z"] = result.normalized_values[index]
            audit_row[f"{metric_alias} MatchedBy"] = result.matched_by[index]
            audit_row[f"{metric_alias} Mean"] = result.mean_value
            audit_row[f"{metric_alias} Stdev"] = result.stdev_value
            audit_row[f"{metric_alias} Source"] = result.source_note

        audit_rows.append(audit_row)

        if missing_labels:
            unmatched_rows.append(
                {
                    "NBA_ID": context.universe_row.nba_id,
                    "Season": context.universe_row.season,
                    "Player": context.universe_row.player,
                    "Team(s)": team_value,
                    "RotationRole": context.universe_row.rotation_role,
                    "MIN": context.effective_minutes,
                    "MissingCount": len(missing_labels),
                    "MissingMetrics": " | ".join(missing_labels),
                }
            )

    output_prefix = args.output_prefix.strip() or "rebounding_all"
    sheet_path = EXPORT_DIR / f"{output_prefix}_sheet.csv"
    rating_only_path = EXPORT_DIR / f"{output_prefix}_ratings.csv"
    audit_path = EXPORT_DIR / f"{output_prefix}_audit.csv"
    unmatched_path = EXPORT_DIR / f"{output_prefix}_unmatched.csv"

    write_matrix_csv(sheet_path, WORKBOOK_COLUMNS, sheet_rows)
    write_csv(rating_only_path, RATING_ONLY_HEADERS, rating_only_rows)
    write_csv(audit_path, list(audit_rows[0].keys()) if audit_rows else [], audit_rows)
    write_csv(
        unmatched_path,
        ["NBA_ID", "Season", "Player", "Team(s)", "RotationRole", "MIN", "MissingCount", "MissingMetrics"],
        unmatched_rows,
    )

    print(f"[OK] Built Rebounding export for {len(sheet_rows)} player-season rows")
    print(
        "[INFO] Exact local sources: bball_index_rebounding.csv for talent/stable/conversion metrics, "
        "tracking_rebound.csv for OREB/DREB contest and chance-adjusted slots, general_traditional.csv "
        "for per-game rebound slots, general_advanced.csv for rebound percentages, hustle.csv for boxouts, "
        "and nbarapm.csv for SelfORebPct."
    )
    print(
        "[INFO] The defensive workbook section repeats Offensive Rebounding Chances Per 75 Possessions, "
        "and this build mirrors that workbook column exactly."
    )
    print(
        "[INFO] Section group weights are renormalized across the groups that actually have data so older "
        "seasons without full bball-index / hustle / nbarapm coverage do not collapse to -1."
    )
    print(f"[OUT] Sheet -> {sheet_path}")
    print(f"[OUT] Ratings -> {rating_only_path}")
    print(f"[OUT] Audit -> {audit_path}")
    print(f"[OUT] Unmatched -> {unmatched_path}")


if __name__ == "__main__":
    main()
