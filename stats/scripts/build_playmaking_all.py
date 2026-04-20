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
    standardize_bball_rows,
)
from build_finishing_standing_dunk import standardize_rows, write_matrix_csv
from pull_nba_stats import EXPORT_DIR, HISTORY_DIR, MANUAL_DIR


BALL_HANDLE_CURVE: Sequence[Tuple[float, float]] = (
    (0.20, 50.0),
    (0.40, 67.0),
    (0.55, 72.0),
    (0.70, 77.0),
    (0.80, 80.0),
    (0.88, 86.0),
    (0.94, 87.0),
    (0.98, 93.0),
    (1.00, 100.0),
)

SPEED_WITH_BALL_CURVE: Sequence[Tuple[float, float]] = (
    (0.10, 31.0),
    (0.20, 44.0),
    (0.30, 54.0),
    (0.40, 62.0),
    (0.50, 67.0),
    (0.60, 70.0),
    (0.70, 75.0),
    (0.78, 78.0),
    (0.86, 82.0),
    (0.93, 86.0),
    (0.97, 90.0),
    (1.00, 100.0),
)

PASS_ACCURACY_CURVE: Sequence[Tuple[float, float]] = (
    (0.10, 40.0),
    (0.20, 50.0),
    (0.30, 55.0),
    (0.40, 60.0),
    (0.50, 65.0),
    (0.60, 69.0),
    (0.70, 74.0),
    (0.78, 76.0),
    (0.86, 80.0),
    (0.93, 85.0),
    (0.97, 90.0),
    (1.00, 100.0),
)

PASS_IQ_CURVE: Sequence[Tuple[float, float]] = (
    (0.10, 46.0),
    (0.20, 54.0),
    (0.30, 60.0),
    (0.40, 64.0),
    (0.50, 68.0),
    (0.60, 70.0),
    (0.70, 75.0),
    (0.78, 79.0),
    (0.86, 82.0),
    (0.93, 88.0),
    (0.97, 94.0),
    (1.00, 100.0),
)

PASS_VISION_CURVE: Sequence[Tuple[float, float]] = (
    (0.06, 40.0),
    (0.12, 48.0),
    (0.18, 52.0),
    (0.24, 56.0),
    (0.30, 60.0),
    (0.38, 63.0),
    (0.46, 66.0),
    (0.56, 70.0),
    (0.66, 74.0),
    (0.76, 78.0),
    (0.86, 82.0),
    (0.94, 89.0),
    (1.00, 100.0),
)


BALL_HANDLE_COMPONENTS: Sequence[ComponentSlot] = (
    ComponentSlot("On-Ball Action Share", "On-Ball Action Share"),
    ComponentSlot("Pick & Roll Ball Handler Frequency%", "Pick & Roll Ball Handler Frequency%"),
    ComponentSlot("P&R Creation Rate", "P&R Creation Rate"),
    ComponentSlot("Overall Shot Creation", "Overall Shot Creation"),
    ComponentSlot("Perimeter Isolation Frequency%", "Perimeter Isolation Frequency%"),
    ComponentSlot(
        "Stable Pick & Roll Ball Handler Points Per Possession",
        "Stable Pick & Roll Ball Handler Points Per Possession",
    ),
    ComponentSlot("On-Ball%", "On-Ball%"),
    ComponentSlot("Pick & Roll Ball Handler POSS", "Pick & Roll Ball Handler POSS"),
    ComponentSlot("AVG_DRIB_PER_TOUCH", "AVG_DRIB_PER_TOUCH"),
    ComponentSlot("INV_PLAYER_HEIGHT_INCHES", "INV_PLAYER_HEIGHT_INCHES"),
    ComponentSlot("Average Dribbles Per Touch", "Average Dribbles Per Touch"),
    ComponentSlot("Dribbles Per Second on Offense", "Dribbles Per Second on Offense"),
    ComponentSlot("Pull-Up Shot Creation", "Pull-Up Shot Creation"),
    ComponentSlot("Isolation Shooting Talent", "Isolation Shooting Talent"),
    ComponentSlot(
        "PnR Ball Handler Shot Making Efficiency",
        "PnR Ball Handler Shot Making Efficiency",
    ),
    ComponentSlot("One on One Shooting Talent", "One on One Shooting Talent"),
    ComponentSlot("Self-Created Shot Making", "Self-Created Shot Making"),
    ComponentSlot("Midrange Pull Up Shot Creation", "Midrange Pull Up Shot Creation"),
    ComponentSlot("On-Ball Gravity", "On-Ball Gravity"),
    ComponentSlot("Stable Isolation PPP", "Stable Isolation PPP"),
    ComponentSlot(
        "Self-Created Shot Making Efficiency",
        "Self-Created Shot Making Efficiency",
    ),
    ComponentSlot("One on One Shooting Talent", "One on One Shooting Talent"),
    ComponentSlot(
        "Total Isolation Impact Per 75 Possessions",
        "Total Isolation Impact Per 75 Possessions",
    ),
    ComponentSlot(
        "Guarded by Perimeter Isolation Defense",
        "Guarded by Perimeter Isolation Defense",
    ),
    ComponentSlot("Self-Created Openness Rating", "Self-Created Openness Rating"),
    ComponentSlot("Overall Shot Making Efficiency", "Overall Shot Making Efficiency"),
)

# TODO: The workbook's Speed with Ball section should eventually use:
# - Ball Handle -> the playmaking Ball Handle aggregate z-score
# - SPEED -> the Physical category's Speed z-score
# The current build already uses Ball Handle aggregate z, but SPEED is still a
# temporary tracking proxy until the Physical category is automated.
SPEED_WITH_BALL_COMPONENTS: Sequence[ComponentSlot] = (
    ComponentSlot("Ball Handle", "Ball Handle Aggregate Z"),
    ComponentSlot("SPEED", "SPEED"),
    ComponentSlot("Transition Shot Creation", "Transition Shot Creation"),
    ComponentSlot("Transition Frequency Impact", "Transition Frequency Impact"),
    ComponentSlot(
        "Offensive Transition Frequency Impact",
        "Offensive Transition Frequency Impact",
    ),
    ComponentSlot("Transition_POSS", "Transition_POSS"),
    ComponentSlot("INV_PLAYER_HEIGHT_INCHES", "INV_PLAYER_HEIGHT_INCHES"),
    ComponentSlot("DRIVES", "DRIVES"),
    ComponentSlot("Movement Speed Rating", "Movement Speed Rating"),
    ComponentSlot("Avg Speed Offense", "AVG_SPEED_OFF"),
    ComponentSlot("Overall Shot Creation", "Overall Shot Creation"),
)

PASS_ACCURACY_COMPONENTS: Sequence[ComponentSlot] = (
    ComponentSlot("Playmaking Talent", "Playmaking Talent"),
    ComponentSlot("Passing Efficiency", "Passing Efficiency"),
    ComponentSlot("Passing Creation Quality", "Passing Creation Quality"),
    ComponentSlot(
        "Potential Assists Per 100 Passes",
        "Potential Assists Per 100 Passes",
    ),
    ComponentSlot(
        "Role-Adjusted Potential Assists Per 100 Passes",
        "Role-Adjusted Potential Assists Per 100 Passes",
    ),
    ComponentSlot("P&R Creation Rate", "P&R Creation Rate"),
    ComponentSlot("INV_Turnovers Per 100 Touches", "INV_Turnovers Per 100 Touches"),
    ComponentSlot(
        "Offensive eFG% Impact on Teammates",
        "Offensive eFG% Impact on Teammates",
    ),
    ComponentSlot(
        "Offense Impact on Teammate Shot Quality",
        "Offense Impact on Teammate Shot Quality",
    ),
    ComponentSlot(
        "Potential Assists Per 100 Passes - Stable Bad Pass Turnovers Per 75",
        "Potential Assists Per 100 Passes - Stable Bad Pass Turnovers Per 75",
    ),
    ComponentSlot("Drive Assist Points Per 75", "Drive Assist Points Per 75"),
    ComponentSlot("AST", "AST"),
    ComponentSlot("AST_ADJ", "AST_ADJ"),
    ComponentSlot("AST_TO_PASS_PCT_ADJ", "AST_TO_PASS_PCT_ADJ"),
    ComponentSlot("AST_PCT", "AST_PCT"),
    ComponentSlot("AST_RATIO", "AST_RATIO"),
    ComponentSlot("AST_TO", "AST_TO"),
    ComponentSlot(
        "High Value Assists Per 75 Possessions",
        "High Value Assists Per 75 Possessions",
    ),
    ComponentSlot("Stable At Rim Assists Per 75", "Stable At Rim Assists Per 75"),
    ComponentSlot("INV_Turnovers Per 100 Touches", "INV_Turnovers Per 100 Touches"),
)

PASS_IQ_COMPONENTS: Sequence[ComponentSlot] = (
    ComponentSlot("Playmaking Talent", "Playmaking Talent"),
    ComponentSlot("Passing Creation Quality", "Passing Creation Quality"),
    ComponentSlot(
        "High Value Assists Per 75 Possessions",
        "High Value Assists Per 75 Possessions",
    ),
    ComponentSlot("Stable At Rim Assists Per 75", "Stable At Rim Assists Per 75"),
    ComponentSlot("P&R Creation Rate", "P&R Creation Rate"),
    ComponentSlot("Drive Assist Points Per 75", "Drive Assist Points Per 75"),
    ComponentSlot("AST_TO_PASS_PCT_ADJ", "AST_TO_PASS_PCT_ADJ"),
    ComponentSlot("AST_PCT", "AST_PCT"),
    ComponentSlot("POTENTIAL_AST", "POTENTIAL_AST"),
    ComponentSlot("SECONDARY_AST", "SECONDARY_AST"),
)

PASS_VISION_COMPONENTS: Sequence[ComponentSlot] = (
    ComponentSlot("Potential Assists Per 100 Passes", "Potential Assists Per 100 Passes"),
    ComponentSlot("Passing Versatility", "Passing Versatility"),
    ComponentSlot("Passing Creation Volume", "Passing Creation Volume"),
    ComponentSlot("Box Creation", "Box Creation"),
    ComponentSlot("Assists Per 75 Possessions", "Assists Per 75 Possessions"),
    ComponentSlot("Stable Assists Per 75", "Stable Assists Per 75"),
    ComponentSlot("Drive Assist Points Per 75", "Drive Assist Points Per 75"),
    ComponentSlot("AST", "AST"),
    ComponentSlot("POTENTIAL_AST", "POTENTIAL_AST"),
    ComponentSlot("SECONDARY_AST", "SECONDARY_AST"),
    ComponentSlot("AST_PCT", "AST_PCT"),
    ComponentSlot("AST_POINTS_CREATED", "AST_POINTS_CREATED"),
    ComponentSlot("PASSES_MADE", "PASSES_MADE"),
)

WORKBOOK_COLUMNS = [
    "NBA ID",
    "Season",
    "Player",
    "Ball Handle Rating",
    "",
    "Ball Handle",
    *[slot.header for slot in BALL_HANDLE_COMPONENTS],
    "Speed with Ball",
    "",
    "Speed with Ball",
    *[slot.header for slot in SPEED_WITH_BALL_COMPONENTS],
    "Pass Accuracy Rating",
    "",
    "Pass Accuracy",
    *[slot.header for slot in PASS_ACCURACY_COMPONENTS],
    "Pass IQ",
    "",
    "Pass IQ",
    *[slot.header for slot in PASS_IQ_COMPONENTS],
    "Pass Vision",
    "",
    "Pass Vision",
    *[slot.header for slot in PASS_VISION_COMPONENTS],
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


BALL_HANDLE_SECTION = SectionSpec(
    name="Ball Handle",
    rating_header="Ball Handle Rating",
    rating_only_header="Ball Handle Rating",
    component_slots=BALL_HANDLE_COMPONENTS,
    weighted_groups=(
        (
            [
                "On-Ball Action Share",
                "Pick & Roll Ball Handler Frequency%",
                "P&R Creation Rate",
                "Overall Shot Creation",
                "Perimeter Isolation Frequency%",
                "Stable Pick & Roll Ball Handler Points Per Possession",
                "On-Ball%",
                "Pick & Roll Ball Handler POSS",
                "AVG_DRIB_PER_TOUCH",
                "INV_PLAYER_HEIGHT_INCHES",
                "Average Dribbles Per Touch",
                "Dribbles Per Second on Offense",
                "Pull-Up Shot Creation",
                "Isolation Shooting Talent",
                "PnR Ball Handler Shot Making Efficiency",
            ],
            0.85,
        ),
        (
            [
                "One on One Shooting Talent",
                "Self-Created Shot Making",
                "Midrange Pull Up Shot Creation",
                "On-Ball Gravity",
                "Stable Isolation PPP",
                "Self-Created Shot Making Efficiency",
                "One on One Shooting Talent",
                "Total Isolation Impact Per 75 Possessions",
                "Guarded by Perimeter Isolation Defense",
            ],
            0.50,
        ),
        (
            [
                "Self-Created Openness Rating",
                "Overall Shot Making Efficiency",
            ],
            0.05,
        ),
    ),
    fallback_aliases=(
        "On-Ball Action Share",
        "Pick & Roll Ball Handler Frequency%",
        "P&R Creation Rate",
        "Overall Shot Creation",
        "Perimeter Isolation Frequency%",
        "Stable Pick & Roll Ball Handler Points Per Possession",
        "On-Ball%",
        "Pick & Roll Ball Handler POSS",
        "AVG_DRIB_PER_TOUCH",
        "INV_PLAYER_HEIGHT_INCHES",
    ),
    curve=BALL_HANDLE_CURVE,
)

SPEED_WITH_BALL_SECTION = SectionSpec(
    name="Speed with Ball",
    rating_header="Speed with Ball",
    rating_only_header="Speed with Ball Rating",
    component_slots=SPEED_WITH_BALL_COMPONENTS,
    weighted_groups=(
        (
            [
                "Ball Handle Aggregate Z",
                "SPEED",
                "Transition Shot Creation",
                "Transition Frequency Impact",
                "Offensive Transition Frequency Impact",
                "Transition_POSS",
                "INV_PLAYER_HEIGHT_INCHES",
                "DRIVES",
            ],
            0.75,
        ),
        (
            [
                "Movement Speed Rating",
                "AVG_SPEED_OFF",
                "Overall Shot Creation",
            ],
            0.25,
        ),
    ),
    fallback_aliases=(
        "Ball Handle Aggregate Z",
        "SPEED",
        "Transition Shot Creation",
        "Transition Frequency Impact",
        "Offensive Transition Frequency Impact",
        "Transition_POSS",
        "INV_PLAYER_HEIGHT_INCHES",
        "DRIVES",
    ),
    curve=SPEED_WITH_BALL_CURVE,
)

PASS_ACCURACY_SECTION = SectionSpec(
    name="Pass Accuracy",
    rating_header="Pass Accuracy Rating",
    rating_only_header="Pass Accuracy Rating",
    component_slots=PASS_ACCURACY_COMPONENTS,
    weighted_groups=(
        (
            [
                "Passing Efficiency",
                "Passing Creation Quality",
                "Potential Assists Per 100 Passes",
                "Role-Adjusted Potential Assists Per 100 Passes",
                "P&R Creation Rate",
                "INV_Turnovers Per 100 Touches",
                "Offensive eFG% Impact on Teammates",
                "Offense Impact on Teammate Shot Quality",
                "Potential Assists Per 100 Passes - Stable Bad Pass Turnovers Per 75",
                "Drive Assist Points Per 75",
                "AST",
                "AST_ADJ",
                "AST_TO_PASS_PCT_ADJ",
                "AST_PCT",
                "AST_RATIO",
                "AST_TO",
            ],
            0.80,
        ),
        (
            [
                "High Value Assists Per 75 Possessions",
                "Stable At Rim Assists Per 75",
                "INV_Turnovers Per 100 Touches",
            ],
            0.20,
        ),
    ),
    fallback_aliases=(
        "Passing Efficiency",
        "Passing Creation Quality",
        "Potential Assists Per 100 Passes",
        "Role-Adjusted Potential Assists Per 100 Passes",
        "P&R Creation Rate",
        "INV_Turnovers Per 100 Touches",
        "Offensive eFG% Impact on Teammates",
        "Offense Impact on Teammate Shot Quality",
        "Potential Assists Per 100 Passes - Stable Bad Pass Turnovers Per 75",
        "Drive Assist Points Per 75",
        "AST",
        "AST_ADJ",
        "AST_TO_PASS_PCT_ADJ",
        "AST_PCT",
        "AST_RATIO",
        "AST_TO",
    ),
    curve=PASS_ACCURACY_CURVE,
)

PASS_IQ_SECTION = SectionSpec(
    name="Pass IQ",
    rating_header="Pass IQ",
    rating_only_header="Pass IQ Rating",
    component_slots=PASS_IQ_COMPONENTS,
    weighted_groups=((tuple(slot.metric_alias for slot in PASS_IQ_COMPONENTS), 1.0),),
    fallback_aliases=(),
    curve=PASS_IQ_CURVE,
)

PASS_VISION_SECTION = SectionSpec(
    name="Pass Vision",
    rating_header="Pass Vision",
    rating_only_header="Pass Vision Rating",
    component_slots=PASS_VISION_COMPONENTS,
    weighted_groups=((tuple(slot.metric_alias for slot in PASS_VISION_COMPONENTS[1:]), 1.0),),
    fallback_aliases=(),
    curve=PASS_VISION_CURVE,
)

SECTIONS: Sequence[SectionSpec] = (
    BALL_HANDLE_SECTION,
    SPEED_WITH_BALL_SECTION,
    PASS_ACCURACY_SECTION,
    PASS_IQ_SECTION,
    PASS_VISION_SECTION,
)

RATING_ONLY_HEADERS = [
    "NBA_ID",
    "Season",
    "Player",
    "Ball Handle Rating",
    "Speed with Ball Rating",
    "Pass Accuracy Rating",
    "Pass IQ Rating",
    "Pass Vision Rating",
]

B_BALL_SOURCE_NOTE = "bball_index_playmaking.csv"

METRIC_SOURCE_NOTES: Dict[str, str] = {
    "On-Ball Action Share": B_BALL_SOURCE_NOTE,
    "P&R Creation Rate": B_BALL_SOURCE_NOTE,
    "Overall Shot Creation": B_BALL_SOURCE_NOTE,
    "On-Ball%": "bball_index_playmaking.csv -> Guarded On-Ball % proxy",
    "Average Dribbles Per Touch": B_BALL_SOURCE_NOTE,
    "Dribbles Per Second on Offense": B_BALL_SOURCE_NOTE,
    "Pull-Up Shot Creation": B_BALL_SOURCE_NOTE,
    "Isolation Shooting Talent": B_BALL_SOURCE_NOTE,
    "PnR Ball Handler Shot Making Efficiency": B_BALL_SOURCE_NOTE,
    "One on One Shooting Talent": B_BALL_SOURCE_NOTE,
    "Self-Created Shot Making": B_BALL_SOURCE_NOTE,
    "Midrange Pull Up Shot Creation": B_BALL_SOURCE_NOTE,
    "On-Ball Gravity": B_BALL_SOURCE_NOTE,
    "Stable Isolation PPP": B_BALL_SOURCE_NOTE,
    "Self-Created Shot Making Efficiency": B_BALL_SOURCE_NOTE,
    "Guarded by Perimeter Isolation Defense": B_BALL_SOURCE_NOTE,
    "Self-Created Openness Rating": B_BALL_SOURCE_NOTE,
    "Overall Shot Making Efficiency": B_BALL_SOURCE_NOTE,
    "Transition Shot Creation": B_BALL_SOURCE_NOTE,
    "Transition Frequency Impact": B_BALL_SOURCE_NOTE,
    "Offensive Transition Frequency Impact": B_BALL_SOURCE_NOTE,
    "Movement Speed Rating": B_BALL_SOURCE_NOTE,
    "Passing Efficiency": B_BALL_SOURCE_NOTE,
    "Passing Creation Quality": B_BALL_SOURCE_NOTE,
    "Potential Assists Per 100 Passes": B_BALL_SOURCE_NOTE,
    "Role-Adjusted Potential Assists Per 100 Passes": B_BALL_SOURCE_NOTE,
    "Turnovers Per 100 Touches": B_BALL_SOURCE_NOTE,
    "Offensive eFG% Impact on Teammates": B_BALL_SOURCE_NOTE,
    "Offense Impact on Teammate Shot Quality": B_BALL_SOURCE_NOTE,
    "Stable Bad Pass Turnovers Per 75": B_BALL_SOURCE_NOTE,
    "High Value Assists Per 75 Possessions": B_BALL_SOURCE_NOTE,
    "Stable At Rim Assists Per 75": B_BALL_SOURCE_NOTE,
    "Passing Versatility": B_BALL_SOURCE_NOTE,
    "Passing Creation Volume": B_BALL_SOURCE_NOTE,
    "Box Creation": B_BALL_SOURCE_NOTE,
    "Assists Per 75 Possessions": B_BALL_SOURCE_NOTE,
    "Stable Assists Per 75": B_BALL_SOURCE_NOTE,
    "Playmaking Talent": "bball_index_hands.csv",
    "Pick & Roll Ball Handler Frequency%": "playtype_pnr_handler.csv -> POSS_PCT proxy",
    "Stable Pick & Roll Ball Handler Points Per Possession": "playtype_pnr_handler.csv -> PPP proxy",
    "Pick & Roll Ball Handler POSS": "playtype_pnr_handler.csv -> POSS proxy",
    "Perimeter Isolation Frequency%": "playtype_iso.csv -> POSS_PCT proxy",
    "Transition_POSS": "playtype_transition.csv -> POSS proxy",
    "AVG_DRIB_PER_TOUCH": "tracking_touches.csv -> AVG_DRIB_PER_TOUCH",
    "SPEED": (
        "physical_all_audit.csv -> Speed AggregateZ direct when available; "
        "otherwise tracking_speed.csv -> AVG_SPEED proxy"
    ),
    "AVG_SPEED_OFF": "tracking_speed.csv -> AVG_SPEED_OFF",
    "DRIVES": "tracking_drives.csv -> DRIVES",
    "Drive Assist Points Per 75": "tracking_drives.csv -> DRIVE_AST proxy",
    "AST": "tracking_passing.csv -> AST",
    "AST_ADJ": "tracking_passing.csv -> AST_ADJ",
    "AST_TO_PASS_PCT_ADJ": "tracking_passing.csv -> AST_TO_PASS_PCT_ADJ",
    "AST_PCT": "general_advanced.csv -> AST_PCT",
    "AST_RATIO": "general_advanced.csv -> AST_RATIO",
    "AST_TO": "general_advanced.csv -> AST_TO",
    "POTENTIAL_AST": "tracking_passing.csv -> POTENTIAL_AST",
    "SECONDARY_AST": "tracking_passing.csv -> SECONDARY_AST",
    "AST_POINTS_CREATED": "tracking_passing.csv -> AST_POINTS_CREATED",
    "PASSES_MADE": "tracking_passing.csv -> PASSES_MADE",
    "PLAYER_HEIGHT_INCHES": "bios.csv -> PLAYER_HEIGHT_INCHES",
    "INV_PLAYER_HEIGHT_INCHES": "bios.csv -> inverse PLAYER_HEIGHT_INCHES",
    "INV_Turnovers Per 100 Touches": "bball_index_playmaking.csv -> inverse Turnovers Per 100 Touches",
    "Potential Assists Per 100 Passes - Stable Bad Pass Turnovers Per 75": (
        "bball_index_playmaking.csv -> Potential Assists Per 100 Passes minus Stable Bad Pass Turnovers Per 75"
    ),
    "Total Isolation Impact Per 75 Possessions": "playtype_iso.csv -> PPP * POSS_PCT proxy",
    "Ball Handle Aggregate Z": "derived from Ball Handle section aggregate z-score",
}

RAW_Z_ONLY_ALIASES = {
    "PLAYER_HEIGHT_INCHES",
    "INV_PLAYER_HEIGHT_INCHES",
}


def parse_args():
    import argparse

    parser = argparse.ArgumentParser(
        description="Build the combined Playmaking ratings export."
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
        "--bball-playmaking-source",
        default=str(HISTORY_DIR / "bball_index_playmaking.csv"),
    )
    parser.add_argument("--hands-source", default=str(HISTORY_DIR / "bball_index_hands.csv"))
    parser.add_argument("--bios-source", default=str(HISTORY_DIR / "bios.csv"))
    parser.add_argument("--advanced-source", default=str(HISTORY_DIR / "general_advanced.csv"))
    parser.add_argument(
        "--tracking-passing-source",
        default=str(HISTORY_DIR / "tracking_passing.csv"),
    )
    parser.add_argument(
        "--tracking-touches-source",
        default=str(HISTORY_DIR / "tracking_touches.csv"),
    )
    parser.add_argument(
        "--tracking-drives-source",
        default=str(HISTORY_DIR / "tracking_drives.csv"),
    )
    parser.add_argument(
        "--tracking-speed-source",
        default=str(HISTORY_DIR / "tracking_speed.csv"),
    )
    parser.add_argument(
        "--playtype-pnr-source",
        default=str(HISTORY_DIR / "playtype_pnr_handler.csv"),
    )
    parser.add_argument(
        "--playtype-iso-source",
        default=str(HISTORY_DIR / "playtype_iso.csv"),
    )
    parser.add_argument(
        "--playtype-transition-source",
        default=str(HISTORY_DIR / "playtype_transition.csv"),
    )
    parser.add_argument(
        "--physical-audit-source",
        default=str(EXPORT_DIR / "physical_all_audit.csv"),
        help=(
            "Optional Physical audit export used to replace the Speed with Ball SPEED proxy "
            "with Physical -> Speed AggregateZ when available."
        ),
    )
    parser.add_argument("--output-prefix", default="playmaking_all")
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
                nba_id="",
                season=season,
                player=player,
                rotation_role=str(row.get("Rotation Role", "")).strip(),
                minutes=parse_float(row.get("Minutes", "")),
            )
        )
    return detail_rows


def load_physical_audit_rows(path: Path) -> List[Dict[str, object]]:
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
                "TEAM_ABBREVIATION": str(row.get("Team(s)", "")).strip(),
                "Speed AggregateZ": parse_float(row.get("Speed AggregateZ", "")),
            }
        )
    return standardized


def get_row_key(row: CalUniverseRow) -> Tuple[str, str]:
    return row.season, normalize_name(row.player)


def set_base_value(base_row: Dict[str, object], alias: str, value: object, matched_by: str) -> None:
    text = "" if value is None else str(value).strip()
    if not text:
        return
    base_row[alias] = value
    base_row[f"__matched__{alias}"] = matched_by


def merge_metric_columns(
    universe: Sequence[CalUniverseRow],
    base_rows_by_key: Dict[Tuple[str, str], Dict[str, object]],
    source_rows: Sequence[Dict[str, str]],
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
            base_row["TEAM_ABBREVIATION"] = str(source_row.get("TEAM_ABBREVIATION", "")).strip()
        for alias, source_column in column_map.items():
            set_base_value(base_row, alias, source_row.get(source_column, ""), matched_by)


def derive_playmaking_metrics(base_rows: Iterable[Dict[str, object]]) -> None:
    for base_row in base_rows:
        height = parse_float(base_row.get("PLAYER_HEIGHT_INCHES", ""))
        if height is not None:
            base_row["INV_PLAYER_HEIGHT_INCHES"] = -height
            base_row["__matched__INV_PLAYER_HEIGHT_INCHES"] = base_row.get(
                "__matched__PLAYER_HEIGHT_INCHES",
                "",
            )

        turnovers_per_100_touches = parse_float(base_row.get("Turnovers Per 100 Touches", ""))
        if turnovers_per_100_touches is not None:
            base_row["INV_Turnovers Per 100 Touches"] = -turnovers_per_100_touches
            base_row["__matched__INV_Turnovers Per 100 Touches"] = base_row.get(
                "__matched__Turnovers Per 100 Touches",
                "",
            )

        potential_assists = parse_float(base_row.get("Potential Assists Per 100 Passes", ""))
        stable_bad_pass = parse_float(base_row.get("Stable Bad Pass Turnovers Per 75", ""))
        if potential_assists is not None and stable_bad_pass is not None:
            base_row[
                "Potential Assists Per 100 Passes - Stable Bad Pass Turnovers Per 75"
            ] = potential_assists - stable_bad_pass
            base_row[
                "__matched__Potential Assists Per 100 Passes - Stable Bad Pass Turnovers Per 75"
            ] = "derived"

        avg_speed = parse_float(base_row.get("AVG_SPEED", ""))
        if avg_speed is not None:
            base_row["SPEED"] = avg_speed
            base_row["__matched__SPEED"] = base_row.get("__matched__AVG_SPEED", "")

        drive_ast = parse_float(base_row.get("DRIVE_AST", ""))
        if drive_ast is not None:
            base_row["Drive Assist Points Per 75"] = drive_ast
            base_row["__matched__Drive Assist Points Per 75"] = base_row.get(
                "__matched__DRIVE_AST",
                "",
            )

        iso_ppp = parse_float(base_row.get("ISO_PPP", ""))
        iso_poss_pct = parse_float(base_row.get("ISO_POSS_PCT", ""))
        if iso_ppp is not None and iso_poss_pct is not None:
            base_row["Total Isolation Impact Per 75 Possessions"] = iso_ppp * iso_poss_pct
            base_row["__matched__Total Isolation Impact Per 75 Possessions"] = "derived-proxy"


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
        if metric_alias in RAW_Z_ONLY_ALIASES:
            normalized_values.append(raw_z)
        else:
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
        source_note=METRIC_SOURCE_NOTES.get(metric_alias, "merged playmaking sources"),
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
    for aliases, weight in section.weighted_groups:
        group_average = average_numeric([component_values_by_alias.get(alias) for alias in aliases])
        if group_average is None:
            if section.fallback_aliases:
                fallback_value = average_numeric(
                    [component_values_by_alias.get(alias) for alias in section.fallback_aliases]
                )
                return fallback_value if fallback_value is not None else -1.0
            return -1.0
        weighted_values.append(group_average * weight)
    return sum(weighted_values)


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
    extra_normalized_by_alias: Optional[Dict[str, List[Optional[float]]]] = None,
) -> Tuple[List[float], List[float], List[float], List[List[Optional[float]]]]:
    extra_normalized_by_alias = extra_normalized_by_alias or {}

    scores: List[float] = []
    component_rows: List[List[Optional[float]]] = []

    for index in range(len(contexts)):
        component_values_by_alias: Dict[str, Optional[float]] = {}
        component_row: List[Optional[float]] = []
        for slot in section.component_slots:
            if slot.metric_alias in extra_normalized_by_alias:
                value = extra_normalized_by_alias[slot.metric_alias][index]
            else:
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
    bball_playmaking_source_path = Path(args.bball_playmaking_source)
    hands_source_path = Path(args.hands_source)
    bios_source_path = Path(args.bios_source)
    advanced_source_path = Path(args.advanced_source)
    tracking_passing_source_path = Path(args.tracking_passing_source)
    tracking_touches_source_path = Path(args.tracking_touches_source)
    tracking_drives_source_path = Path(args.tracking_drives_source)
    tracking_speed_source_path = Path(args.tracking_speed_source)
    playtype_pnr_source_path = Path(args.playtype_pnr_source)
    playtype_iso_source_path = Path(args.playtype_iso_source)
    playtype_transition_source_path = Path(args.playtype_transition_source)
    physical_audit_source_path = Path(args.physical_audit_source)

    required_paths = (
        minutes_source_path,
        bball_playmaking_source_path,
        hands_source_path,
        bios_source_path,
        advanced_source_path,
        tracking_passing_source_path,
        tracking_touches_source_path,
        tracking_drives_source_path,
        tracking_speed_source_path,
        playtype_pnr_source_path,
        playtype_iso_source_path,
        playtype_transition_source_path,
    )
    for path in required_paths:
        if not path.exists():
            raise SystemExit(f"Source CSV not found: {path}")
    if details_path and not details_path.exists():
        raise SystemExit(f"Details CSV not found: {details_path}")

    bball_playmaking_rows = standardize_bball_rows(read_history_csv(bball_playmaking_source_path))
    hands_rows = standardize_bball_rows(read_history_csv(hands_source_path))
    bios_rows = read_history_csv(bios_source_path)
    advanced_rows = read_history_csv(advanced_source_path)
    tracking_passing_rows = standardize_rows(
        read_history_csv(tracking_passing_source_path),
        season_column="Season",
        player_column="PLAYER_NAME",
    )
    tracking_touches_rows = standardize_rows(
        read_history_csv(tracking_touches_source_path),
        season_column="Season",
        player_column="PLAYER_NAME",
    )
    tracking_drives_rows = standardize_rows(
        read_history_csv(tracking_drives_source_path),
        season_column="Season",
        player_column="PLAYER_NAME",
    )
    tracking_speed_rows = standardize_rows(
        read_history_csv(tracking_speed_source_path),
        season_column="Season",
        player_column="PLAYER_NAME",
    )
    playtype_pnr_rows = standardize_rows(
        read_history_csv(playtype_pnr_source_path),
        season_column="Season",
        player_column="PLAYER_NAME",
    )
    playtype_iso_rows = standardize_rows(
        read_history_csv(playtype_iso_source_path),
        season_column="Season",
        player_column="PLAYER_NAME",
    )
    playtype_transition_rows = standardize_rows(
        read_history_csv(playtype_transition_source_path),
        season_column="Season",
        player_column="PLAYER_NAME",
    )
    physical_audit_rows: List[Dict[str, object]] = []
    if physical_audit_source_path.exists():
        physical_audit_rows = load_physical_audit_rows(physical_audit_source_path)
    minutes_rows = read_history_csv(minutes_source_path)

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
    universe = enrich_universe_rows(universe, load_bball_detail_rows(bball_playmaking_rows))
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
        bball_playmaking_rows,
        {
            "On-Ball Action Share": "On-Ball Action Share",
            "P&R Creation Rate": "P&R Creation Rate",
            "Overall Shot Creation": "Overall Shot Creation",
            "On-Ball%": "Guarded On-Ball %",
            "Average Dribbles Per Touch": "Average Dribbles Per Touch",
            "Dribbles Per Second on Offense": "Dribbles Per Second on Offense",
            "Pull-Up Shot Creation": "Pull-Up Shot Creation",
            "Isolation Shooting Talent": "Isolation Shooting Talent",
            "PnR Ball Handler Shot Making Efficiency": "PnR Ball Handler Shot Making Efficiency",
            "One on One Shooting Talent": "One on One Shooting Talent",
            "Self-Created Shot Making": "Self-Created Shot Making",
            "Midrange Pull Up Shot Creation": "Midrange Pull Up Shot Creation",
            "On-Ball Gravity": "On-Ball Gravity",
            "Stable Isolation PPP": "Stable Isolation PPP",
            "Self-Created Shot Making Efficiency": "Self-Created Shot Making Efficiency",
            "Guarded by Perimeter Isolation Defense": "Guarded by Perimeter Isolation Defense",
            "Self-Created Openness Rating": "Self-Created Openness Rating",
            "Overall Shot Making Efficiency": "Overall Shot Making Efficiency",
            "Transition Shot Creation": "Transition Shot Creation",
            "Transition Frequency Impact": "Transition Frequency Impact",
            "Offensive Transition Frequency Impact": "Offensive Transition Frequency Impact",
            "Movement Speed Rating": "Movement Speed Rating",
            "Passing Efficiency": "Passing Efficiency",
            "Passing Creation Quality": "Passing Creation Quality",
            "Potential Assists Per 100 Passes": "Potential Assists Per 100 Passes",
            "Role-Adjusted Potential Assists Per 100 Passes": "Role-Adjusted Potential Assists Per 100 Passes",
            "Turnovers Per 100 Touches": "Turnovers Per 100 Touches",
            "Offensive eFG% Impact on Teammates": "Offensive eFG% Impact on Teammates",
            "Offense Impact on Teammate Shot Quality": "Offense Impact on Teammate Shot Quality",
            "Stable Bad Pass Turnovers Per 75": "Stable Bad Pass Turnovers Per 75",
            "High Value Assists Per 75 Possessions": "High Value Assists Per 75 Possessions",
            "Stable At Rim Assists Per 75": "Stable At Rim Assists Per 75",
            "Passing Versatility": "Passing Versatility",
            "Passing Creation Volume": "Passing Creation Volume",
            "Box Creation": "Box Creation",
            "Assists Per 75 Possessions": "Assists Per 75 Possessions",
            "Stable Assists Per 75": "Stable Assists Per 75",
        },
        allow_id_fallback=args.allow_id_fallback,
    )
    merge_metric_columns(
        universe,
        base_rows_by_key,
        hands_rows,
        {"Playmaking Talent": "Playmaking Talent"},
        allow_id_fallback=args.allow_id_fallback,
    )
    merge_metric_columns(
        universe,
        base_rows_by_key,
        bios_rows,
        {"PLAYER_HEIGHT_INCHES": "PLAYER_HEIGHT_INCHES"},
        allow_id_fallback=args.allow_id_fallback,
    )
    merge_metric_columns(
        universe,
        base_rows_by_key,
        tracking_touches_rows,
        {"AVG_DRIB_PER_TOUCH": "AVG_DRIB_PER_TOUCH"},
        allow_id_fallback=args.allow_id_fallback,
    )
    merge_metric_columns(
        universe,
        base_rows_by_key,
        tracking_drives_rows,
        {"DRIVES": "DRIVES", "DRIVE_AST": "DRIVE_AST"},
        allow_id_fallback=args.allow_id_fallback,
    )
    merge_metric_columns(
        universe,
        base_rows_by_key,
        tracking_speed_rows,
        {"AVG_SPEED": "AVG_SPEED", "AVG_SPEED_OFF": "AVG_SPEED_OFF"},
        allow_id_fallback=args.allow_id_fallback,
    )
    merge_metric_columns(
        universe,
        base_rows_by_key,
        tracking_passing_rows,
        {
            "AST": "AST",
            "AST_ADJ": "AST_ADJ",
            "AST_TO_PASS_PCT_ADJ": "AST_TO_PASS_PCT_ADJ",
            "POTENTIAL_AST": "POTENTIAL_AST",
            "SECONDARY_AST": "SECONDARY_AST",
            "AST_POINTS_CREATED": "AST_POINTS_CREATED",
            "PASSES_MADE": "PASSES_MADE",
        },
        allow_id_fallback=args.allow_id_fallback,
    )
    merge_metric_columns(
        universe,
        base_rows_by_key,
        advanced_rows,
        {"AST_PCT": "AST_PCT", "AST_RATIO": "AST_RATIO", "AST_TO": "AST_TO"},
        allow_id_fallback=args.allow_id_fallback,
    )
    merge_metric_columns(
        universe,
        base_rows_by_key,
        playtype_pnr_rows,
        {
            "Pick & Roll Ball Handler Frequency%": "POSS_PCT",
            "Stable Pick & Roll Ball Handler Points Per Possession": "PPP",
            "Pick & Roll Ball Handler POSS": "POSS",
        },
        allow_id_fallback=args.allow_id_fallback,
    )
    merge_metric_columns(
        universe,
        base_rows_by_key,
        playtype_iso_rows,
        {
            "Perimeter Isolation Frequency%": "POSS_PCT",
            "ISO_PPP": "PPP",
            "ISO_POSS_PCT": "POSS_PCT",
        },
        allow_id_fallback=args.allow_id_fallback,
    )
    merge_metric_columns(
        universe,
        base_rows_by_key,
        playtype_transition_rows,
        {"Transition_POSS": "POSS"},
        allow_id_fallback=args.allow_id_fallback,
    )
    merge_metric_columns(
        universe,
        base_rows_by_key,
        physical_audit_rows,
        {"Physical Speed Aggregate Z": "Speed AggregateZ"},
        allow_id_fallback=args.allow_id_fallback,
    )

    derive_playmaking_metrics(base_rows_by_key.values())

    all_metric_aliases = {
        alias
        for section in SECTIONS
        for alias in [slot.metric_alias for slot in section.component_slots]
        if alias != "Ball Handle Aggregate Z"
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

    ball_handle_scores, ball_handle_z, ball_handle_ratings, ball_handle_rows = build_section_outputs(
        section=BALL_HANDLE_SECTION,
        contexts=contexts,
        metric_results=metric_results,
    )

    metric_results["Ball Handle Aggregate Z"] = MetricResult(
        raw_values=ball_handle_z,
        normalized_values=ball_handle_z,
        matched_by=["derived"] * len(ball_handle_z),
        mean_value=statistics.mean(ball_handle_z),
        stdev_value=statistics.stdev(ball_handle_z),
        source_note=METRIC_SOURCE_NOTES["Ball Handle Aggregate Z"],
    )

    physical_speed_values: List[Optional[float]] = []
    physical_speed_matched_by: List[str] = []
    for context in contexts:
        base_row = base_rows_by_key[get_row_key(context.universe_row)]
        physical_speed_values.append(parse_float(base_row.get("Physical Speed Aggregate Z", "")))
        physical_speed_matched_by.append(
            str(base_row.get("__matched__Physical Speed Aggregate Z", "")).strip()
        )
    using_physical_speed = any(value is not None for value in physical_speed_values)
    if using_physical_speed:
        matched_values = [value for value in physical_speed_values if value is not None]
        metric_results["SPEED"] = MetricResult(
            raw_values=physical_speed_values,
            normalized_values=physical_speed_values,
            matched_by=physical_speed_matched_by,
            mean_value=statistics.mean(matched_values) if matched_values else None,
            stdev_value=statistics.stdev(matched_values) if len(matched_values) >= 2 else None,
            source_note="physical_all_audit.csv -> Speed AggregateZ direct",
        )

    speed_scores, speed_z, speed_ratings, speed_rows = build_section_outputs(
        section=SPEED_WITH_BALL_SECTION,
        contexts=contexts,
        metric_results=metric_results,
        extra_normalized_by_alias={"Ball Handle Aggregate Z": ball_handle_z},
    )
    pass_accuracy_scores, pass_accuracy_z, pass_accuracy_ratings, pass_accuracy_rows = build_section_outputs(
        section=PASS_ACCURACY_SECTION,
        contexts=contexts,
        metric_results=metric_results,
    )
    pass_iq_scores, pass_iq_z, pass_iq_ratings, pass_iq_rows = build_section_outputs(
        section=PASS_IQ_SECTION,
        contexts=contexts,
        metric_results=metric_results,
    )
    pass_vision_scores, pass_vision_z, pass_vision_ratings, pass_vision_rows = build_section_outputs(
        section=PASS_VISION_SECTION,
        contexts=contexts,
        metric_results=metric_results,
    )

    sheet_rows: List[List[object]] = []
    rating_only_rows: List[Dict[str, object]] = []
    audit_rows: List[Dict[str, object]] = []
    unmatched_rows: List[Dict[str, object]] = []

    for index, context in enumerate(contexts):
        sheet_rows.append(
            [
                context.universe_row.nba_id,
                context.universe_row.season,
                context.universe_row.player,
                ball_handle_ratings[index],
                ball_handle_z[index],
                ball_handle_scores[index],
                *ball_handle_rows[index],
                speed_ratings[index],
                speed_z[index],
                speed_scores[index],
                *speed_rows[index],
                pass_accuracy_ratings[index],
                pass_accuracy_z[index],
                pass_accuracy_scores[index],
                *pass_accuracy_rows[index],
                pass_iq_ratings[index],
                pass_iq_z[index],
                pass_iq_scores[index],
                *pass_iq_rows[index],
                pass_vision_ratings[index],
                pass_vision_z[index],
                pass_vision_scores[index],
                *pass_vision_rows[index],
            ]
        )

        rating_only_rows.append(
            {
                "NBA_ID": context.universe_row.nba_id,
                "Season": context.universe_row.season,
                "Player": context.universe_row.player,
                "Ball Handle Rating": ball_handle_ratings[index],
                "Speed with Ball Rating": speed_ratings[index],
                "Pass Accuracy Rating": pass_accuracy_ratings[index],
                "Pass IQ Rating": pass_iq_ratings[index],
                "Pass Vision Rating": pass_vision_ratings[index],
            }
        )

        audit_row: Dict[str, object] = {
            "NBA_ID": context.universe_row.nba_id,
            "Season": context.universe_row.season,
            "Player": context.universe_row.player,
            "RotationRole": context.universe_row.rotation_role,
            "MIN": context.effective_minutes,
            "WorkbookMIN": context.workbook_minutes,
            "LiveMIN": context.live_minutes,
            "LiveMINPerGame": context.live_minutes_per_game,
            "LiveGP": context.live_gp,
            "MinutesMatchedBy": context.minutes_matched_by,
            "CurrentSeason": current_season,
            "Ball Handle Score": ball_handle_scores[index],
            "Ball Handle AggregateZ": ball_handle_z[index],
            "Ball Handle Rating": ball_handle_ratings[index],
            "Speed with Ball Score": speed_scores[index],
            "Speed with Ball AggregateZ": speed_z[index],
            "Speed with Ball Rating": speed_ratings[index],
            "Pass Accuracy Score": pass_accuracy_scores[index],
            "Pass Accuracy AggregateZ": pass_accuracy_z[index],
            "Pass Accuracy Rating": pass_accuracy_ratings[index],
            "Pass IQ Score": pass_iq_scores[index],
            "Pass IQ AggregateZ": pass_iq_z[index],
            "Pass IQ Rating": pass_iq_ratings[index],
            "Pass Vision Score": pass_vision_scores[index],
            "Pass Vision AggregateZ": pass_vision_z[index],
            "Pass Vision Rating": pass_vision_ratings[index],
        }

        missing_labels: List[str] = []
        for section in SECTIONS:
            missing_aliases = sorted(
                {
                    slot.metric_alias
                    for slot in section.component_slots
                    if (
                        metric_results.get(slot.metric_alias) is not None
                        and metric_results[slot.metric_alias].normalized_values[index] is None
                    )
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
                    "RotationRole": context.universe_row.rotation_role,
                    "MIN": context.effective_minutes,
                    "MissingCount": len(missing_labels),
                    "MissingMetrics": " | ".join(missing_labels),
                }
            )

    output_prefix = args.output_prefix.strip() or "playmaking_all"
    sheet_path = EXPORT_DIR / f"{output_prefix}_sheet.csv"
    rating_only_path = EXPORT_DIR / f"{output_prefix}_ratings.csv"
    audit_path = EXPORT_DIR / f"{output_prefix}_audit.csv"
    unmatched_path = EXPORT_DIR / f"{output_prefix}_unmatched.csv"

    write_matrix_csv(sheet_path, WORKBOOK_COLUMNS, sheet_rows)
    write_csv(rating_only_path, RATING_ONLY_HEADERS, rating_only_rows)
    write_csv(audit_path, list(audit_rows[0].keys()) if audit_rows else [], audit_rows)
    write_csv(
        unmatched_path,
        ["NBA_ID", "Season", "Player", "RotationRole", "MIN", "MissingCount", "MissingMetrics"],
        unmatched_rows,
    )

    print(f"[OK] Built Playmaking export for {len(sheet_rows)} player-season rows")
    print("[INFO] RotationRole is enriched from the playmaking bball-index file when available.")
    print("[INFO] Live MIN still comes from NBA.com history via the minutes source.")
    print(
        "[INFO] Proxies in use: playtype POSS/POSS_PCT/PPP for PnR and isolation frequency slots, "
        f"{'Physical Speed AggregateZ for SPEED plus tracking DRIVE_AST' if using_physical_speed else 'tracking SPEED/DRIVE_AST for SPEED and Drive Assist slots'}."
    )
    print(
        "[INFO] Speed with Ball uses the playmaking Ball Handle z-score, and "
        f"{'now reads SPEED from physical_all_audit.csv -> Speed AggregateZ when present.' if using_physical_speed else 'still falls back to tracking_speed.csv -> AVG_SPEED until the Physical audit export is available.'}"
    )
    print(f"[OUT] Sheet -> {sheet_path}")
    print(f"[OUT] Ratings -> {rating_only_path}")
    print(f"[OUT] Audit -> {audit_path}")
    print(f"[OUT] Unmatched -> {unmatched_path}")


if __name__ == "__main__":
    main()
