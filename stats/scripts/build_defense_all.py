from __future__ import annotations

import re
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


INTERIOR_DEFENSE_CURVE: Sequence[Tuple[float, float]] = (
    (0.08, 31.0),
    (0.16, 36.0),
    (0.24, 42.0),
    (0.32, 48.0),
    (0.40, 52.0),
    (0.50, 58.0),
    (0.60, 64.0),
    (0.70, 70.0),
    (0.80, 75.0),
    (0.88, 79.0),
    (0.94, 85.0),
    (0.98, 94.06),
    (1.00, 100.0),
)

PERIMETER_DEFENSE_CURVE: Sequence[Tuple[float, float]] = (
    (0.08, 39.0),
    (0.16, 47.0),
    (0.24, 53.28),
    (0.34, 59.0),
    (0.44, 63.0),
    (0.54, 67.0),
    (0.64, 71.0),
    (0.74, 75.0),
    (0.82, 78.0),
    (0.90, 83.0),
    (0.96, 88.0),
    (1.00, 100.0),
)

STEAL_CURVE: Sequence[Tuple[float, float]] = (
    (0.06, 36.0),
    (0.13, 41.0),
    (0.22, 44.0),
    (0.32, 51.0),
    (0.42, 56.0),
    (0.52, 59.44),
    (0.62, 63.0),
    (0.72, 66.0),
    (0.80, 71.0),
    (0.88, 77.0),
    (0.95, 85.0),
    (0.98, 90.0),
    (1.00, 100.0),
)

BLOCK_CURVE: Sequence[Tuple[float, float]] = (
    (0.06, 29.0),
    (0.12, 32.0),
    (0.18, 36.0),
    (0.26, 40.0),
    (0.34, 43.0),
    (0.44, 49.0),
    (0.54, 52.0),
    (0.64, 57.0),
    (0.74, 63.0),
    (0.84, 72.0),
    (0.92, 82.0),
    (0.98, 93.0),
    (1.00, 100.0),
)

HELP_DEFENSE_IQ_CURVE: Sequence[Tuple[float, float]] = (
    (0.04, 44.0),
    (0.08, 50.0),
    (0.14, 55.0),
    (0.22, 60.0),
    (0.30, 63.0),
    (0.40, 67.0),
    (0.50, 70.0),
    (0.60, 72.0),
    (0.70, 76.0),
    (0.78, 80.0),
    (0.86, 84.0),
    (0.92, 88.0),
    (0.97, 95.0),
    (1.00, 100.0),
)

PASS_PERCEPTION_CURVE: Sequence[Tuple[float, float]] = (
    (0.04, 40.0),
    (0.08, 43.0),
    (0.14, 48.0),
    (0.22, 52.0),
    (0.30, 56.0),
    (0.40, 60.0),
    (0.50, 65.0),
    (0.60, 68.0),
    (0.70, 72.0),
    (0.78, 76.0),
    (0.86, 81.0),
    (0.92, 87.0),
    (0.97, 93.0),
    (1.00, 100.0),
)


INTERIOR_COMPONENTS: Sequence[ComponentSlot] = (
    ComponentSlot("DEFENSIVE IMPACT", "DEFENSIVE IMPACT"),
    ComponentSlot("Screener Rim Defense", "Screener Rim Defense"),
    ComponentSlot("Rim Protection", "Rim Protection"),
    ComponentSlot("Rim Contests Per 75 Possessions", "Rim Contests Per 75 Possessions"),
    ComponentSlot("Rim Points Saved Per 75 Possessions", "Rim Points Saved Per 75 Possessions"),
    ComponentSlot("Stable Rim DFGA Per 75 Possessions", "Stable Rim DFGA Per 75 Possessions"),
    ComponentSlot("Help Defensive Activity", "Help Defensive Activity"),
    ComponentSlot("Help Defense Talent", "Help Defense Talent"),
    ComponentSlot("Help Effectiveness Rating", "Help Effectiveness Rating"),
    ComponentSlot("FG_MISS_LT_06", "FG_MISS_LT_06"),
    ComponentSlot("rim_points_saved", "rim_points_saved"),
    ComponentSlot("rim_points_saved_100", "rim_points_saved_100"),
    ComponentSlot("Percentage of Shots at Rim Contested", "Percentage of Shots at Rim Contested"),
    ComponentSlot("Post Defense", "Post Defense"),
    ComponentSlot("INV_Stable Rim dFG% vs. Expected", "INV_Stable Rim dFG% vs. Expected"),
    ComponentSlot("rimdfga/100", "rimdfga/100"),
    ComponentSlot("INV_rim_dif%", "INV_rim_dif%"),
    ComponentSlot("INV_rim_acc_onoff", "INV_rim_acc_onoff"),
    ComponentSlot("INV_rim_acc_on", "INV_rim_acc_on"),
    ComponentSlot("<6ft_FG_Diff%", "<6ft_FG_Diff%"),
)

PERIMETER_COMPONENTS: Sequence[ComponentSlot] = (
    ComponentSlot("Perimeter Isolation Defense", "Perimeter Isolation Defense"),
    ComponentSlot("Matchup Difficulty", "Matchup Difficulty"),
    ComponentSlot("Guarded Shooting Talent", "Guarded Shooting Talent"),
    ComponentSlot("Passing Lane Defense", "Passing Lane Defense"),
    ComponentSlot("Deflections Per 75 Possessions", "Deflections Per 75 Possessions"),
    ComponentSlot("% of Time Guarding Primary Ball Handlers", "% of Time Guarding Primary Ball Handlers"),
    ComponentSlot("Guarded 3PT Shooting Talent", "Guarded 3PT Shooting Talent"),
    ComponentSlot("Guarded Midrange Talent", "Guarded Midrange Talent"),
    ComponentSlot("% of Time Guarding Usage Tier 1 Players", "% of Time Guarding Usage Tier 1 Players"),
    ComponentSlot("DEFLECTIONS", "DEFLECTIONS"),
    ComponentSlot("Off-Ball Chaser Defense", "Off-Ball Chaser Defense"),
    ComponentSlot("Ball Screen Navigation", "Ball Screen Navigation"),
    ComponentSlot("Guarded 3PT Shot Creation", "Guarded 3PT Shot Creation"),
    ComponentSlot("3PT Contests Per 75 Possessions", "3PT Contests Per 75 Possessions"),
    ComponentSlot("Steals Per 75 Possessions", "Steals Per 75 Possessions"),
    ComponentSlot("Pickpocket Rating", "Pickpocket Rating"),
    ComponentSlot("CONTESTED_SHOTS_3PT", "CONTESTED_SHOTS_3PT"),
    ComponentSlot("FG3_MISS", "FG3_MISS"),
    ComponentSlot("Defensive Miles Per 75 Possessions", "Defensive Miles Per 75 Possessions"),
    ComponentSlot("Loose Ball Recovery Rate", "Loose Ball Recovery Rate"),
)

STEAL_COMPONENTS: Sequence[ComponentSlot] = (
    ComponentSlot("Steals Per 75 Possessions", "Steals Per 75 Possessions"),
    ComponentSlot("Stable Steals Per 75", "Stable Steals Per 75"),
    ComponentSlot("STL", "STL"),
    ComponentSlot("PCT_STL", "PCT_STL"),
    ComponentSlot("Steals_100", "Steals_100"),
    ComponentSlot("Stable Bad Pass Steals Per 75", "Stable Bad Pass Steals Per 75"),
    ComponentSlot("Stable Lost Ball Steals Per 75", "Stable Lost Ball Steals Per 75"),
    ComponentSlot("Pickpocket Rating", "Pickpocket Rating"),
    ComponentSlot("rFTOV_100", "rFTOV_100"),
)

BLOCK_COMPONENTS: Sequence[ComponentSlot] = (
    ComponentSlot("BLK", "BLK"),
    ComponentSlot("PCT_BLK", "PCT_BLK"),
    ComponentSlot("Blocks Per 75 Possessions", "Blocks Per 75 Possessions"),
    ComponentSlot("Stable Blocks Per 75", "Stable Blocks Per 75"),
    ComponentSlot("Stable Recovered Blocks Per 75", "Stable Recovered Blocks Per 75"),
    ComponentSlot("Blocks_100", "Blocks_100"),
    ComponentSlot("RecoveredBlocks_100", "RecoveredBlocks_100"),
    ComponentSlot("Stable Recovered Blocks%", "Stable Recovered Blocks%"),
    ComponentSlot("Block Rate on Contests", "Block Rate on Contests"),
)

HELP_DEFENSE_IQ_COMPONENTS: Sequence[ComponentSlot] = (
    ComponentSlot("Help Defensive Activity", "Help Defensive Activity"),
    ComponentSlot("Help Defense Talent", "Help Defense Talent"),
    ComponentSlot("Help Effectiveness Rating", "Help Effectiveness Rating"),
    ComponentSlot("DEFENSIVE IMPACT", "DEFENSIVE IMPACT"),
    ComponentSlot("STOPS_100", "STOPS_100"),
    ComponentSlot("Defensive Playmaking", "Defensive Playmaking"),
    ComponentSlot("Screener Mobile Defense", "Screener Mobile Defense"),
    ComponentSlot("Screener Rim Defense", "Screener Rim Defense"),
    ComponentSlot("Passing Lane Defense", "Passing Lane Defense"),
    ComponentSlot("Rim Deterrence", "Rim Deterrence"),
    ComponentSlot("Rim Deterrence Per 100", "Rim Deterrence Per 100"),
    ComponentSlot("DHO Coverage Versatility", "DHO Coverage Versatility"),
    ComponentSlot("Defensive Role Versatility", "Defensive Role Versatility"),
    ComponentSlot("Overall Coverage Versatility", "Overall Coverage Versatility"),
    ComponentSlot("P&R Coverage Versatility", "P&R Coverage Versatility"),
)

PASS_PERCEPTION_COMPONENTS: Sequence[ComponentSlot] = (
    ComponentSlot("Passing Lane Defense", "Passing Lane Defense"),
    ComponentSlot("DEFLECTIONS", "DEFLECTIONS"),
    ComponentSlot("FTOV_100", "FTOV_100"),
    ComponentSlot("Deflections Per 75 Possessions", "Deflections Per 75 Possessions"),
    ComponentSlot("Stable Bad Pass Steals Per 75", "Stable Bad Pass Steals Per 75"),
)


WORKBOOK_COLUMNS = [
    "NBA ID",
    "Season",
    "Player",
    "Interior Defense Rating",
    "",
    "Interior Defense",
    *[slot.header for slot in INTERIOR_COMPONENTS],
    "Perimeter Defense Rating",
    "",
    "Perimeter Defense",
    *[slot.header for slot in PERIMETER_COMPONENTS],
    "Steal Rating",
    "",
    "Steal",
    *[slot.header for slot in STEAL_COMPONENTS],
    "Block Rating",
    "",
    "Block",
    *[slot.header for slot in BLOCK_COMPONENTS],
    "Help Defense IQ Rating",
    "",
    "Help Defense IQ",
    *[slot.header for slot in HELP_DEFENSE_IQ_COMPONENTS],
    "Pass Perception",
    "",
    "Pass Perception",
    *[slot.header for slot in PASS_PERCEPTION_COMPONENTS],
]


@dataclass(frozen=True)
class SectionSpec:
    name: str
    rating_header: str
    rating_only_header: str
    component_slots: Sequence[ComponentSlot]
    weighted_groups: Sequence[Tuple[Sequence[str], float]]
    fallback_aliases: Sequence[str]
    curve: Sequence[Tuple[float, float]]


@dataclass
class MetricResult:
    raw_values: List[Optional[float]]
    normalized_values: List[Optional[float]]
    matched_by: List[str]
    mean_value: Optional[float]
    stdev_value: Optional[float]
    source_note: str


INTERIOR_SECTION = SectionSpec(
    name="Interior Defense",
    rating_header="Interior Defense Rating",
    rating_only_header="Interior Defense Rating",
    component_slots=INTERIOR_COMPONENTS,
    weighted_groups=(
        (tuple(slot.metric_alias for slot in INTERIOR_COMPONENTS[:12]), 0.60),
        (tuple(slot.metric_alias for slot in INTERIOR_COMPONENTS[12:16]), 0.30),
        (tuple(slot.metric_alias for slot in INTERIOR_COMPONENTS[16:]), 0.10),
    ),
    fallback_aliases=(),
    curve=INTERIOR_DEFENSE_CURVE,
)

PERIMETER_SECTION = SectionSpec(
    name="Perimeter Defense",
    rating_header="Perimeter Defense Rating",
    rating_only_header="Perimeter Defense Rating",
    component_slots=PERIMETER_COMPONENTS,
    weighted_groups=(
        (tuple(slot.metric_alias for slot in PERIMETER_COMPONENTS[:10]), 0.85),
        (tuple(slot.metric_alias for slot in PERIMETER_COMPONENTS[10:]), 0.15),
    ),
    fallback_aliases=(),
    curve=PERIMETER_DEFENSE_CURVE,
)

STEAL_SECTION = SectionSpec(
    name="Steal",
    rating_header="Steal Rating",
    rating_only_header="Steal Rating",
    component_slots=STEAL_COMPONENTS,
    weighted_groups=(
        (tuple(slot.metric_alias for slot in STEAL_COMPONENTS[:5]), 0.90),
        (tuple(slot.metric_alias for slot in STEAL_COMPONENTS[5:]), 0.10),
    ),
    fallback_aliases=(),
    curve=STEAL_CURVE,
)

BLOCK_SECTION = SectionSpec(
    name="Block",
    rating_header="Block Rating",
    rating_only_header="Block Rating",
    component_slots=BLOCK_COMPONENTS,
    weighted_groups=(
        (tuple(slot.metric_alias for slot in BLOCK_COMPONENTS[:7]), 0.90),
        (tuple(slot.metric_alias for slot in BLOCK_COMPONENTS[7:]), 0.10),
    ),
    fallback_aliases=(),
    curve=BLOCK_CURVE,
)

HELP_DEFENSE_IQ_SECTION = SectionSpec(
    name="Help Defense IQ",
    rating_header="Help Defense IQ Rating",
    rating_only_header="Help Defense IQ Rating",
    component_slots=HELP_DEFENSE_IQ_COMPONENTS,
    weighted_groups=(
        (tuple(slot.metric_alias for slot in HELP_DEFENSE_IQ_COMPONENTS[:5]), 0.60),
        (tuple(slot.metric_alias for slot in HELP_DEFENSE_IQ_COMPONENTS[5:11]), 0.30),
        (tuple(slot.metric_alias for slot in HELP_DEFENSE_IQ_COMPONENTS[11:]), 0.10),
    ),
    fallback_aliases=(),
    curve=HELP_DEFENSE_IQ_CURVE,
)

PASS_PERCEPTION_SECTION = SectionSpec(
    name="Pass Perception",
    rating_header="Pass Perception",
    rating_only_header="Pass Perception Rating",
    component_slots=PASS_PERCEPTION_COMPONENTS,
    weighted_groups=((tuple(slot.metric_alias for slot in PASS_PERCEPTION_COMPONENTS), 1.0),),
    fallback_aliases=(),
    curve=PASS_PERCEPTION_CURVE,
)

SECTIONS: Sequence[SectionSpec] = (
    INTERIOR_SECTION,
    PERIMETER_SECTION,
    STEAL_SECTION,
    BLOCK_SECTION,
    HELP_DEFENSE_IQ_SECTION,
    PASS_PERCEPTION_SECTION,
)

RATING_ONLY_HEADERS = [
    "NBA_ID",
    "Season",
    "Player",
    "Interior Defense Rating",
    "Perimeter Defense Rating",
    "Steal Rating",
    "Block Rating",
    "Help Defense IQ Rating",
    "Pass Perception Rating",
]

B_BALL_SOURCE_NOTE = "bball_index_defense.csv"

METRIC_SOURCE_NOTES: Dict[str, str] = {
    "DEFENSIVE IMPACT": "impact_all_audit.csv -> Defensive Impact AggregateZ direct",
    "Screener Rim Defense": B_BALL_SOURCE_NOTE,
    "Rim Protection": B_BALL_SOURCE_NOTE,
    "Rim Contests Per 75 Possessions": B_BALL_SOURCE_NOTE,
    "Rim Points Saved Per 75 Possessions": B_BALL_SOURCE_NOTE,
    "Stable Rim DFGA Per 75 Possessions": B_BALL_SOURCE_NOTE,
    "Help Defensive Activity": B_BALL_SOURCE_NOTE,
    "Help Defense Talent": B_BALL_SOURCE_NOTE,
    "Help Effectiveness Rating": B_BALL_SOURCE_NOTE,
    "Percentage of Shots at Rim Contested": B_BALL_SOURCE_NOTE,
    "Post Defense": B_BALL_SOURCE_NOTE,
    "Perimeter Isolation Defense": B_BALL_SOURCE_NOTE,
    "Matchup Difficulty": B_BALL_SOURCE_NOTE,
    "Guarded Shooting Talent": B_BALL_SOURCE_NOTE,
    "Passing Lane Defense": B_BALL_SOURCE_NOTE,
    "Deflections Per 75 Possessions": B_BALL_SOURCE_NOTE,
    "% of Time Guarding Primary Ball Handlers": B_BALL_SOURCE_NOTE,
    "Guarded 3PT Shooting Talent": B_BALL_SOURCE_NOTE,
    "Guarded Midrange Talent": B_BALL_SOURCE_NOTE,
    "% of Time Guarding Usage Tier 1 Players": B_BALL_SOURCE_NOTE,
    "Off-Ball Chaser Defense": B_BALL_SOURCE_NOTE,
    "Ball Screen Navigation": B_BALL_SOURCE_NOTE,
    "Guarded 3PT Shot Creation": B_BALL_SOURCE_NOTE,
    "3PT Contests Per 75 Possessions": B_BALL_SOURCE_NOTE,
    "Steals Per 75 Possessions": B_BALL_SOURCE_NOTE,
    "Pickpocket Rating": B_BALL_SOURCE_NOTE,
    "Defensive Miles Per 75 Possessions": B_BALL_SOURCE_NOTE,
    "Loose Ball Recovery Rate": B_BALL_SOURCE_NOTE,
    "Stable Steals Per 75": B_BALL_SOURCE_NOTE,
    "Stable Bad Pass Steals Per 75": B_BALL_SOURCE_NOTE,
    "Stable Lost Ball Steals Per 75": B_BALL_SOURCE_NOTE,
    "Blocks Per 75 Possessions": B_BALL_SOURCE_NOTE,
    "Stable Blocks Per 75": B_BALL_SOURCE_NOTE,
    "Stable Recovered Blocks Per 75": B_BALL_SOURCE_NOTE,
    "Stable Recovered Blocks%": B_BALL_SOURCE_NOTE,
    "Block Rate on Contests": B_BALL_SOURCE_NOTE,
    "Defensive Playmaking": B_BALL_SOURCE_NOTE,
    "Screener Mobile Defense": B_BALL_SOURCE_NOTE,
    "Rim Deterrence": B_BALL_SOURCE_NOTE,
    "Rim Deterrence Per 100": B_BALL_SOURCE_NOTE,
    "DHO Coverage Versatility": B_BALL_SOURCE_NOTE,
    "Defensive Role Versatility": B_BALL_SOURCE_NOTE,
    "Overall Coverage Versatility": B_BALL_SOURCE_NOTE,
    "P&R Coverage Versatility": B_BALL_SOURCE_NOTE,
    "FG_MISS_LT_06": "defense_dashboard_l6ft.csv -> FGA_LT_06 - FGM_LT_06",
    "<6ft_FG_Diff%": "defense_dashboard_l6ft.csv -> NS_LT_06_PCT - LT_06_PCT",
    "rim_points_saved": "nbarapm.csv -> rim_points_saved",
    "rim_points_saved_100": "nbarapm.csv -> rim_points_saved_100",
    "INV_Stable Rim dFG% vs. Expected": "bball_index_defense.csv -> inverse Stable Rim dFG% vs. Expected",
    "rimdfga/100": "nbarapm.csv -> rimdfga/100",
    "INV_rim_dif%": "nbarapm.csv -> inverse rim_dif%",
    "INV_rim_acc_onoff": "nbarapm.csv -> inverse rim_acc_onoff",
    "INV_rim_acc_on": "nbarapm.csv -> inverse rim_acc_on",
    "DEFLECTIONS": "hustle.csv -> DEFLECTIONS",
    "CONTESTED_SHOTS_3PT": "hustle.csv -> CONTESTED_SHOTS_3PT",
    "FG3_MISS": "defense_dashboard_3.csv -> FG3A - FG3M",
    "STL": "general_defense.csv -> STL",
    "PCT_STL": "general_defense.csv -> PCT_STL",
    "Steals_100": "nbarapm.csv -> Steals_100",
    "BLK": "general_defense.csv -> BLK",
    "PCT_BLK": "general_defense.csv -> PCT_BLK",
    "Blocks_100": "nbarapm.csv -> Blocks_100",
    "RecoveredBlocks_100": "nbarapm.csv -> RecoveredBlocks_100",
    "STOPS_100": "nbarapm.csv -> STOPS_100",
    "FTOV_100": "nbarapm.csv -> FTOV_100",
    "rFTOV_100": "nbarapm.csv -> rFTOV_100",
}


def parse_args():
    import argparse

    parser = argparse.ArgumentParser(
        description="Build the combined Defense ratings export."
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
        "--bball-defense-source",
        default=str(HISTORY_DIR / "bball_index_defense.csv"),
    )
    parser.add_argument(
        "--impact-audit-source",
        default=str(EXPORT_DIR / "impact_all_audit.csv"),
    )
    parser.add_argument("--nbarapm-source", default=str(HISTORY_DIR / "nbarapm.csv"))
    parser.add_argument(
        "--general-defense-source",
        default=str(HISTORY_DIR / "general_defense.csv"),
    )
    parser.add_argument("--hustle-source", default=str(HISTORY_DIR / "hustle.csv"))
    parser.add_argument(
        "--defense-dashboard-3-source",
        default=str(HISTORY_DIR / "defense_dashboard_3.csv"),
    )
    parser.add_argument(
        "--defense-dashboard-l6ft-source",
        default=str(HISTORY_DIR / "defense_dashboard_l6ft.csv"),
    )
    parser.add_argument("--output-prefix", default="defense_all")
    return parser.parse_args()


def clean_text(value: object) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if text.lower() == "nan":
        return ""
    return text


def canonical_season(value: object) -> str:
    text = clean_text(value)
    if not text:
        return ""

    match = re.match(r"^(\d{4})-(\d{2})$", text)
    if match:
        return f"{match.group(1)}-{match.group(2)}"

    match = re.match(r"^(\d{4})-(\d{4})$", text)
    if match:
        return f"{match.group(1)}-{match.group(2)[-2:]}"

    numeric = parse_float(text)
    if numeric is None:
        return text

    year = int(numeric)
    return f"{year - 1}-{year % 100:02d}"


def parse_metric_float(value: object) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return float(value)
        except Exception:
            return None

    text = clean_text(value)
    if not text:
        return None

    text = (
        text.replace("\u2212", "-")
        .replace("%", "")
        .replace(",", "")
        .replace("\u00a0", " ")
    )
    if text.startswith("+"):
        text = text[1:]
    try:
        return float(text)
    except Exception:
        return None


def load_bball_detail_rows(rows: Sequence[Dict[str, str]]) -> List[CalUniverseRow]:
    detail_rows: List[CalUniverseRow] = []
    for row in rows:
        season = canonical_season(row.get("Season", ""))
        player = clean_text(row.get("Player", ""))
        if not season or not player:
            continue

        detail_rows.append(
            CalUniverseRow(
                nba_id=canonical_id(row.get("NBA_ID", row.get("NBA ID", ""))),
                season=season,
                player=player,
                rotation_role=clean_text(row.get("Rotation Role", "")),
                minutes=parse_metric_float(row.get("Minutes", "")),
            )
        )
    return detail_rows


def load_bball_index_rows(path: Path) -> List[Dict[str, object]]:
    raw_rows = read_history_csv(path)
    standardized: List[Dict[str, object]] = []
    metric_columns = {
        "Screener Rim Defense",
        "Rim Protection",
        "Rim Contests Per 75 Possessions",
        "Rim Points Saved Per 75 Possessions",
        "Stable Rim DFGA Per 75 Possessions",
        "Help Defensive Activity",
        "Help Defense Talent",
        "Help Effectiveness Rating",
        "Percentage of Shots at Rim Contested",
        "Post Defense",
        "Stable Rim dFG% vs. Expected",
        "Perimeter Isolation Defense",
        "Matchup Difficulty",
        "Guarded Shooting Talent",
        "Passing Lane Defense",
        "Deflections Per 75 Possessions",
        "% of Time Guarding Primary Ball Handlers",
        "Guarded 3PT Shooting Talent",
        "Guarded Midrange Talent",
        "% of Time Guarding Usage Tier 1 Players",
        "Off-Ball Chaser Defense",
        "Ball Screen Navigation",
        "Guarded 3PT Shot Creation",
        "3PT Contests Per 75 Possessions",
        "Steals Per 75 Possessions",
        "Pickpocket Rating",
        "Defensive Miles Per 75 Possessions",
        "Loose Ball Recovery Rate",
        "Stable Steals Per 75",
        "Stable Bad Pass Steals Per 75",
        "Stable Lost Ball Steals Per 75",
        "Blocks Per 75 Possessions",
        "Stable Blocks Per 75",
        "Stable Recovered Blocks Per 75",
        "Stable Recovered Blocks%",
        "Block Rate on Contests",
        "Defensive Playmaking",
        "Screener Mobile Defense",
        "Rim Deterrence",
        "Rim Deterrence Per 100",
        "DHO Coverage Versatility",
        "Defensive Role Versatility",
        "Overall Coverage Versatility",
        "P&R Coverage Versatility",
    }

    for row in raw_rows:
        season = canonical_season(row.get("Season", ""))
        player = clean_text(row.get("Player", ""))
        if not season or not player:
            continue

        merged: Dict[str, object] = {
            "Season": season,
            "PLAYER_ID": canonical_id(row.get("NBA_ID", row.get("NBA ID", ""))),
            "PLAYER_NAME": player,
            "TEAM_ABBREVIATION": clean_text(row.get("Team(s)", row.get("Team", ""))),
        }
        for metric in metric_columns:
            merged[metric] = parse_metric_float(row.get(metric, ""))
        standardized.append(merged)

    return standardized


def load_impact_audit_rows(path: Path) -> List[Dict[str, object]]:
    raw_rows = read_history_csv(path)
    standardized: List[Dict[str, object]] = []

    for row in raw_rows:
        season = canonical_season(row.get("Season", ""))
        player = clean_text(row.get("Player", ""))
        if not season or not player:
            continue

        standardized.append(
            {
                "Season": season,
                "PLAYER_ID": canonical_id(row.get("NBA_ID", "")),
                "PLAYER_NAME": player,
                "TEAM_ABBREVIATION": clean_text(row.get("Team(s)", "")),
                "Defensive Impact AggregateZ": parse_metric_float(
                    row.get("Defensive Impact AggregateZ", "")
                ),
            }
        )

    return standardized


def load_nbarapm_rows(path: Path) -> List[Dict[str, object]]:
    raw_rows = read_history_csv(path)
    standardized: List[Dict[str, object]] = []

    for row in raw_rows:
        season = canonical_season(row.get("Season", ""))
        player = clean_text(row.get("Player", ""))
        if not season or not player:
            continue

        standardized.append(
            {
                "Season": season,
                "PLAYER_ID": canonical_id(row.get("NBA_ID", "")),
                "PLAYER_NAME": player,
                "TEAM_ABBREVIATION": "",
                "rim_points_saved": parse_metric_float(row.get("rim_points_saved", "")),
                "rim_points_saved_100": parse_metric_float(row.get("rim_points_saved_100", "")),
                "rimdfga/100": parse_metric_float(row.get("rimdfga/100", "")),
                "rim_dif%": parse_metric_float(row.get("rim_dif%", "")),
                "rim_acc_onoff": parse_metric_float(row.get("rim_acc_onoff", "")),
                "rim_acc_on": parse_metric_float(row.get("rim_acc_on", "")),
                "Steals_100": parse_metric_float(row.get("Steals_100", "")),
                "Blocks_100": parse_metric_float(row.get("Blocks_100", "")),
                "RecoveredBlocks_100": parse_metric_float(row.get("RecoveredBlocks_100", "")),
                "STOPS_100": parse_metric_float(row.get("STOPS_100", "")),
                "FTOV_100": parse_metric_float(row.get("FTOV_100", "")),
                "rFTOV_100": parse_metric_float(row.get("rFTOV_100", "")),
            }
        )

    return standardized


def load_hustle_rows(path: Path) -> List[Dict[str, object]]:
    raw_rows = standardize_rows(
        read_history_csv(path),
        season_column="Season",
        player_column="PLAYER_NAME",
    )
    standardized: List[Dict[str, object]] = []

    for row in raw_rows:
        season = canonical_season(row.get("Season", ""))
        player = clean_text(row.get("PLAYER_NAME", ""))
        if not season or not player:
            continue

        standardized.append(
            {
                "Season": season,
                "PLAYER_ID": canonical_id(row.get("PLAYER_ID", "")),
                "PLAYER_NAME": player,
                "TEAM_ABBREVIATION": clean_text(row.get("TEAM_ABBREVIATION", "")),
                "CONTESTED_SHOTS_3PT": parse_metric_float(row.get("CONTESTED_SHOTS_3PT", "")),
                "DEFLECTIONS": parse_metric_float(row.get("DEFLECTIONS", "")),
            }
        )

    return standardized


def load_defense_dashboard_3_rows(path: Path) -> List[Dict[str, object]]:
    raw_rows = standardize_rows(
        read_history_csv(path),
        season_column="Season",
        player_column="PLAYER_NAME",
    )
    standardized: List[Dict[str, object]] = []

    for row in raw_rows:
        season = canonical_season(row.get("Season", ""))
        player = clean_text(row.get("PLAYER_NAME", ""))
        if not season or not player:
            continue

        fg3m = parse_metric_float(row.get("FG3M", ""))
        fg3a = parse_metric_float(row.get("FG3A", ""))
        fg3_miss = None
        if fg3m is not None and fg3a is not None:
            fg3_miss = fg3a - fg3m

        standardized.append(
            {
                "Season": season,
                "PLAYER_ID": canonical_id(row.get("CLOSE_DEF_PERSON_ID", "")),
                "PLAYER_NAME": player,
                "TEAM_ABBREVIATION": clean_text(row.get("PLAYER_LAST_TEAM_ABBREVIATION", "")),
                "FG3_MISS": fg3_miss,
            }
        )

    return standardized


def load_defense_dashboard_l6ft_rows(path: Path) -> List[Dict[str, object]]:
    raw_rows = standardize_rows(
        read_history_csv(path),
        season_column="Season",
        player_column="PLAYER_NAME",
    )
    standardized: List[Dict[str, object]] = []

    for row in raw_rows:
        season = canonical_season(row.get("Season", ""))
        player = clean_text(row.get("PLAYER_NAME", ""))
        if not season or not player:
            continue

        fgm_lt_06 = parse_metric_float(row.get("FGM_LT_06", ""))
        fga_lt_06 = parse_metric_float(row.get("FGA_LT_06", ""))
        lt_06_pct = parse_metric_float(row.get("LT_06_PCT", ""))
        ns_lt_06_pct = parse_metric_float(row.get("NS_LT_06_PCT", ""))

        fg_miss_lt_06 = None
        if fgm_lt_06 is not None and fga_lt_06 is not None:
            fg_miss_lt_06 = fga_lt_06 - fgm_lt_06

        fg_diff_lt_06 = None
        if lt_06_pct is not None and ns_lt_06_pct is not None:
            fg_diff_lt_06 = ns_lt_06_pct - lt_06_pct

        standardized.append(
            {
                "Season": season,
                "PLAYER_ID": canonical_id(row.get("CLOSE_DEF_PERSON_ID", "")),
                "PLAYER_NAME": player,
                "TEAM_ABBREVIATION": clean_text(row.get("PLAYER_LAST_TEAM_ABBREVIATION", "")),
                "FG_MISS_LT_06": fg_miss_lt_06,
                "<6ft_FG_Diff%": fg_diff_lt_06,
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


def derive_defense_metrics(base_rows: Iterable[Dict[str, object]]) -> None:
    for base_row in base_rows:
        stable_rim_diff = parse_float(base_row.get("Stable Rim dFG% vs. Expected", ""))
        if stable_rim_diff is not None:
            base_row["INV_Stable Rim dFG% vs. Expected"] = -stable_rim_diff
            base_row["__matched__INV_Stable Rim dFG% vs. Expected"] = base_row.get(
                "__matched__Stable Rim dFG% vs. Expected",
                "",
            )

        rim_dif = parse_float(base_row.get("rim_dif%", ""))
        if rim_dif is not None:
            base_row["INV_rim_dif%"] = -rim_dif
            base_row["__matched__INV_rim_dif%"] = base_row.get("__matched__rim_dif%", "")

        rim_acc_onoff = parse_float(base_row.get("rim_acc_onoff", ""))
        if rim_acc_onoff is not None:
            base_row["INV_rim_acc_onoff"] = -rim_acc_onoff
            base_row["__matched__INV_rim_acc_onoff"] = base_row.get(
                "__matched__rim_acc_onoff",
                "",
            )

        rim_acc_on = parse_float(base_row.get("rim_acc_on", ""))
        if rim_acc_on is not None:
            base_row["INV_rim_acc_on"] = -rim_acc_on
            base_row["__matched__INV_rim_acc_on"] = base_row.get("__matched__rim_acc_on", "")


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

    if metric_alias == "DEFENSIVE IMPACT":
        normalized_values = list(raw_values)
        return MetricResult(
            raw_values=raw_values,
            normalized_values=normalized_values,
            matched_by=matched_by_values,
            mean_value=mean_value,
            stdev_value=stdev_value,
            source_note=METRIC_SOURCE_NOTES.get(metric_alias, "merged defense sources"),
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
        source_note=METRIC_SOURCE_NOTES.get(metric_alias, "merged defense sources"),
    )


def average_numeric(values: Sequence[Optional[float]]) -> Optional[float]:
    numeric = [value for value in values if value is not None]
    if not numeric:
        return None
    return statistics.mean(numeric)


def compute_weighted_group_score(
    weighted_groups: Sequence[Tuple[Sequence[str], float]],
    component_values_by_alias: Dict[str, Optional[float]],
) -> float:
    weighted_values: List[float] = []
    total_weight = 0.0

    for aliases, weight in weighted_groups:
        group_average = average_numeric([component_values_by_alias.get(alias) for alias in aliases])
        if group_average is None:
            continue
        weighted_values.append(group_average * weight)
        total_weight += weight

    if not weighted_values or total_weight <= 0:
        return -1.0
    return sum(weighted_values) / total_weight


def compute_perimeter_score(component_values_by_alias: Dict[str, Optional[float]]) -> float:
    isolation = component_values_by_alias.get("Perimeter Isolation Defense")
    matchup = component_values_by_alias.get("Matchup Difficulty")

    front_half_aliases = [slot.metric_alias for slot in PERIMETER_COMPONENTS[:10]]
    back_half_aliases = [slot.metric_alias for slot in PERIMETER_COMPONENTS[10:]]
    front_rest_aliases = [slot.metric_alias for slot in PERIMETER_COMPONENTS[2:10]]

    if isolation is None:
        return compute_weighted_group_score(
            (
                (tuple(front_half_aliases), 0.85),
                (tuple(back_half_aliases), 0.15),
            ),
            component_values_by_alias,
        )

    weighted_values: List[float] = [isolation * 0.50]
    total_weight = 0.50

    if matchup is not None:
        weighted_values.append(matchup * 0.20)
        total_weight += 0.20

    front_rest_average = average_numeric([component_values_by_alias.get(alias) for alias in front_rest_aliases])
    if front_rest_average is not None:
        weighted_values.append(front_rest_average * 0.15)
        total_weight += 0.15

    back_half_average = average_numeric([component_values_by_alias.get(alias) for alias in back_half_aliases])
    if back_half_average is not None:
        weighted_values.append(back_half_average * 0.15)
        total_weight += 0.15

    if total_weight <= 0:
        return -1.0
    return sum(weighted_values) / total_weight


def compute_section_score(
    section: SectionSpec,
    component_values_by_alias: Dict[str, Optional[float]],
) -> float:
    if section.name == "Perimeter Defense":
        return compute_perimeter_score(component_values_by_alias)

    score = compute_weighted_group_score(section.weighted_groups, component_values_by_alias)
    if score != -1.0:
        return score

    if section.fallback_aliases:
        fallback_value = average_numeric(
            [component_values_by_alias.get(alias) for alias in section.fallback_aliases]
        )
        return fallback_value if fallback_value is not None else -1.0

    return -1.0


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
    bball_defense_source_path = Path(args.bball_defense_source)
    impact_audit_source_path = Path(args.impact_audit_source)
    nbarapm_source_path = Path(args.nbarapm_source)
    general_defense_source_path = Path(args.general_defense_source)
    hustle_source_path = Path(args.hustle_source)
    defense_dashboard_3_source_path = Path(args.defense_dashboard_3_source)
    defense_dashboard_l6ft_source_path = Path(args.defense_dashboard_l6ft_source)

    required_paths = (
        minutes_source_path,
        bball_defense_source_path,
        impact_audit_source_path,
        nbarapm_source_path,
        general_defense_source_path,
        hustle_source_path,
        defense_dashboard_3_source_path,
        defense_dashboard_l6ft_source_path,
    )
    missing_paths = [str(path) for path in required_paths if not path.exists()]
    if missing_paths:
        raise SystemExit("Missing required source files:\n- " + "\n- ".join(missing_paths))

    minutes_rows = read_history_csv(minutes_source_path)
    bball_defense_raw_rows = read_history_csv(bball_defense_source_path)
    bball_defense_rows = load_bball_index_rows(bball_defense_source_path)
    impact_audit_rows = load_impact_audit_rows(impact_audit_source_path)
    nbarapm_rows = load_nbarapm_rows(nbarapm_source_path)
    general_defense_rows = standardize_rows(
        read_history_csv(general_defense_source_path),
        season_column="Season",
        player_column="PLAYER_NAME",
    )
    hustle_rows = load_hustle_rows(hustle_source_path)
    defense_dashboard_3_rows = load_defense_dashboard_3_rows(defense_dashboard_3_source_path)
    defense_dashboard_l6ft_rows = load_defense_dashboard_l6ft_rows(defense_dashboard_l6ft_source_path)

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
    if bball_defense_raw_rows:
        universe = enrich_universe_rows(universe, load_bball_detail_rows(bball_defense_raw_rows))
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
        bball_defense_rows,
        {
            "Screener Rim Defense": "Screener Rim Defense",
            "Rim Protection": "Rim Protection",
            "Rim Contests Per 75 Possessions": "Rim Contests Per 75 Possessions",
            "Rim Points Saved Per 75 Possessions": "Rim Points Saved Per 75 Possessions",
            "Stable Rim DFGA Per 75 Possessions": "Stable Rim DFGA Per 75 Possessions",
            "Help Defensive Activity": "Help Defensive Activity",
            "Help Defense Talent": "Help Defense Talent",
            "Help Effectiveness Rating": "Help Effectiveness Rating",
            "Percentage of Shots at Rim Contested": "Percentage of Shots at Rim Contested",
            "Post Defense": "Post Defense",
            "Stable Rim dFG% vs. Expected": "Stable Rim dFG% vs. Expected",
            "Perimeter Isolation Defense": "Perimeter Isolation Defense",
            "Matchup Difficulty": "Matchup Difficulty",
            "Guarded Shooting Talent": "Guarded Shooting Talent",
            "Passing Lane Defense": "Passing Lane Defense",
            "Deflections Per 75 Possessions": "Deflections Per 75 Possessions",
            "% of Time Guarding Primary Ball Handlers": "% of Time Guarding Primary Ball Handlers",
            "Guarded 3PT Shooting Talent": "Guarded 3PT Shooting Talent",
            "Guarded Midrange Talent": "Guarded Midrange Talent",
            "% of Time Guarding Usage Tier 1 Players": "% of Time Guarding Usage Tier 1 Players",
            "Off-Ball Chaser Defense": "Off-Ball Chaser Defense",
            "Ball Screen Navigation": "Ball Screen Navigation",
            "Guarded 3PT Shot Creation": "Guarded 3PT Shot Creation",
            "3PT Contests Per 75 Possessions": "3PT Contests Per 75 Possessions",
            "Steals Per 75 Possessions": "Steals Per 75 Possessions",
            "Pickpocket Rating": "Pickpocket Rating",
            "Defensive Miles Per 75 Possessions": "Defensive Miles Per 75 Possessions",
            "Loose Ball Recovery Rate": "Loose Ball Recovery Rate",
            "Stable Steals Per 75": "Stable Steals Per 75",
            "Stable Bad Pass Steals Per 75": "Stable Bad Pass Steals Per 75",
            "Stable Lost Ball Steals Per 75": "Stable Lost Ball Steals Per 75",
            "Blocks Per 75 Possessions": "Blocks Per 75 Possessions",
            "Stable Blocks Per 75": "Stable Blocks Per 75",
            "Stable Recovered Blocks Per 75": "Stable Recovered Blocks Per 75",
            "Stable Recovered Blocks%": "Stable Recovered Blocks%",
            "Block Rate on Contests": "Block Rate on Contests",
            "Defensive Playmaking": "Defensive Playmaking",
            "Screener Mobile Defense": "Screener Mobile Defense",
            "Rim Deterrence": "Rim Deterrence",
            "Rim Deterrence Per 100": "Rim Deterrence Per 100",
            "DHO Coverage Versatility": "DHO Coverage Versatility",
            "Defensive Role Versatility": "Defensive Role Versatility",
            "Overall Coverage Versatility": "Overall Coverage Versatility",
            "P&R Coverage Versatility": "P&R Coverage Versatility",
        },
        allow_id_fallback=args.allow_id_fallback,
    )
    merge_metric_columns(
        universe,
        base_rows_by_key,
        impact_audit_rows,
        {"DEFENSIVE IMPACT": "Defensive Impact AggregateZ"},
        allow_id_fallback=args.allow_id_fallback,
    )
    merge_metric_columns(
        universe,
        base_rows_by_key,
        nbarapm_rows,
        {
            "rim_points_saved": "rim_points_saved",
            "rim_points_saved_100": "rim_points_saved_100",
            "rimdfga/100": "rimdfga/100",
            "rim_dif%": "rim_dif%",
            "rim_acc_onoff": "rim_acc_onoff",
            "rim_acc_on": "rim_acc_on",
            "Steals_100": "Steals_100",
            "Blocks_100": "Blocks_100",
            "RecoveredBlocks_100": "RecoveredBlocks_100",
            "STOPS_100": "STOPS_100",
            "FTOV_100": "FTOV_100",
            "rFTOV_100": "rFTOV_100",
        },
        allow_id_fallback=args.allow_id_fallback,
    )
    merge_metric_columns(
        universe,
        base_rows_by_key,
        general_defense_rows,
        {
            "STL": "STL",
            "PCT_STL": "PCT_STL",
            "BLK": "BLK",
            "PCT_BLK": "PCT_BLK",
        },
        allow_id_fallback=args.allow_id_fallback,
    )
    merge_metric_columns(
        universe,
        base_rows_by_key,
        hustle_rows,
        {
            "DEFLECTIONS": "DEFLECTIONS",
            "CONTESTED_SHOTS_3PT": "CONTESTED_SHOTS_3PT",
        },
        allow_id_fallback=args.allow_id_fallback,
    )
    merge_metric_columns(
        universe,
        base_rows_by_key,
        defense_dashboard_3_rows,
        {"FG3_MISS": "FG3_MISS"},
        allow_id_fallback=args.allow_id_fallback,
    )
    merge_metric_columns(
        universe,
        base_rows_by_key,
        defense_dashboard_l6ft_rows,
        {
            "FG_MISS_LT_06": "FG_MISS_LT_06",
            "<6ft_FG_Diff%": "<6ft_FG_Diff%",
        },
        allow_id_fallback=args.allow_id_fallback,
    )

    derive_defense_metrics(base_rows_by_key.values())

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

    interior_scores, interior_z, interior_ratings, interior_rows = build_section_outputs(
        section=INTERIOR_SECTION,
        contexts=contexts,
        metric_results=metric_results,
    )
    perimeter_scores, perimeter_z, perimeter_ratings, perimeter_rows = build_section_outputs(
        section=PERIMETER_SECTION,
        contexts=contexts,
        metric_results=metric_results,
    )
    steal_scores, steal_z, steal_ratings, steal_rows = build_section_outputs(
        section=STEAL_SECTION,
        contexts=contexts,
        metric_results=metric_results,
    )
    block_scores, block_z, block_ratings, block_rows = build_section_outputs(
        section=BLOCK_SECTION,
        contexts=contexts,
        metric_results=metric_results,
    )
    help_iq_scores, help_iq_z, help_iq_ratings, help_iq_rows = build_section_outputs(
        section=HELP_DEFENSE_IQ_SECTION,
        contexts=contexts,
        metric_results=metric_results,
    )
    pass_perception_scores, pass_perception_z, pass_perception_ratings, pass_perception_rows = build_section_outputs(
        section=PASS_PERCEPTION_SECTION,
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
                interior_ratings[index],
                interior_z[index],
                interior_scores[index],
                *interior_rows[index],
                perimeter_ratings[index],
                perimeter_z[index],
                perimeter_scores[index],
                *perimeter_rows[index],
                steal_ratings[index],
                steal_z[index],
                steal_scores[index],
                *steal_rows[index],
                block_ratings[index],
                block_z[index],
                block_scores[index],
                *block_rows[index],
                help_iq_ratings[index],
                help_iq_z[index],
                help_iq_scores[index],
                *help_iq_rows[index],
                pass_perception_ratings[index],
                pass_perception_z[index],
                pass_perception_scores[index],
                *pass_perception_rows[index],
            ]
        )

        rating_only_rows.append(
            {
                "NBA_ID": context.universe_row.nba_id,
                "Season": context.universe_row.season,
                "Player": context.universe_row.player,
                "Interior Defense Rating": interior_ratings[index],
                "Perimeter Defense Rating": perimeter_ratings[index],
                "Steal Rating": steal_ratings[index],
                "Block Rating": block_ratings[index],
                "Help Defense IQ Rating": help_iq_ratings[index],
                "Pass Perception Rating": pass_perception_ratings[index],
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
            "Interior Defense Score": interior_scores[index],
            "Interior Defense AggregateZ": interior_z[index],
            "Interior Defense Rating": interior_ratings[index],
            "Perimeter Defense Score": perimeter_scores[index],
            "Perimeter Defense AggregateZ": perimeter_z[index],
            "Perimeter Defense Rating": perimeter_ratings[index],
            "Steal Score": steal_scores[index],
            "Steal AggregateZ": steal_z[index],
            "Steal Rating": steal_ratings[index],
            "Block Score": block_scores[index],
            "Block AggregateZ": block_z[index],
            "Block Rating": block_ratings[index],
            "Help Defense IQ Score": help_iq_scores[index],
            "Help Defense IQ AggregateZ": help_iq_z[index],
            "Help Defense IQ Rating": help_iq_ratings[index],
            "Pass Perception Score": pass_perception_scores[index],
            "Pass Perception AggregateZ": pass_perception_z[index],
            "Pass Perception Rating": pass_perception_ratings[index],
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

    output_prefix = args.output_prefix.strip() or "defense_all"
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

    print(f"[OK] Built Defense export for {len(sheet_rows)} player-season rows")
    print(
        "[INFO] Exact local sources: bball_index_defense.csv for the defense metric catalog, "
        "impact_all_audit.csv for direct Defensive Impact aggregate z, general_defense.csv for STL/BLK, "
        "hustle.csv for DEFLECTIONS/CONTESTED_SHOTS_3PT, and nbarapm.csv for rim, stop, steal, "
        "block, and forced-turnover rates."
    )
    print(
        "[INFO] Proxies and derivations in use: FG3_MISS from defense_dashboard_3.csv, "
        "FG_MISS_LT_06 and <6ft_FG_Diff% from defense_dashboard_l6ft.csv, plus inverse rim "
        "suppression aliases derived from bball-index and nbarapm."
    )
    print(
        "[INFO] RotationRole and workbook minutes default to playerlist.csv + optional details, "
        "then get enriched from the live bball-index defense file and Cal workbook fallback when available."
    )
    print(f"[OUT] Sheet -> {sheet_path}")
    print(f"[OUT] Ratings -> {rating_only_path}")
    print(f"[OUT] Audit -> {audit_path}")
    print(f"[OUT] Unmatched -> {unmatched_path}")


if __name__ == "__main__":
    main()
