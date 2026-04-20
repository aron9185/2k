import argparse
import json
import math
import os
import re
import pandas as pd

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_DIR = os.path.dirname(SCRIPT_DIR)
DEFAULT_TENDENCY_CSV = os.path.join(REPO_DIR, "stats", "exports", "tendency_all_ratings.csv")

# -----------------------------
# Column → (group, offsetName)
# -----------------------------
COLUMN_MAPPINGS = {
    "NBA ID": None, "Season": None, "Team(s)": None, "MIN": None, "Overall": None, "Player": None,

    # Vitals (skipped by default because strings)
    "Primary_Position": ("vitals", "Position"),
    "Second_Position":  ("vitals", "Secondary Position"),

    # Attributes
    "Layup":    ("attributes", "Driving Layup"),
    "ST Dunk":  ("attributes", "Standing Dunk"),
    "Dunk":     ("attributes", "Driving Dunk"),
    "Close":    ("attributes", "Close Shot"),
    "Mid":      ("attributes", "Midrange Shot"),
    "3PT":      ("attributes", "3pt Shot"),   # alias below will rename to "Three Point"
    "FT":       ("attributes", "Free Throw"),
    "PHook":    ("attributes", "Post Hook"),
    "PFade":    ("attributes", "Post Fade"),
    "PostC":    ("attributes", "Post Moves"),
    "Foul":     ("attributes", "Draw Foul"),
    "SHOTIQ":   ("attributes", "Shot IQ"),
    "Ball":     ("attributes", "Ball Control"),
    "SPD/BALL": ("attributes", "Speed with Ball"),
    "Hands":    ("attributes", "Hands"),
    "Pass":     ("attributes", "Pass Accuracy"),
    "Pass IQ":  ("attributes", "Passing IQ"),
    "Vision":   ("attributes", "Passing Vision"),
    "OCNST":    ("attributes", "Offensive Consistency"),
    "ID":       ("attributes", "Interior Defense"),
    "PD":       ("attributes", "Perimeter Defense"),
    "STL":      ("attributes", "Steal"),
    "BLK":      ("attributes", "Block"),
    "OREB":     ("attributes", "Offensive Rebound"),
    "DREB":     ("attributes", "Defensive Rebound"),
    "HelpDIQ":  ("attributes", "Help Defense IQ"),
    "PSPER":    ("attributes", "Pass Perception"),
    "DCNST":    ("attributes", "Defensive Consistency"),
    "SPEED":    ("attributes", "Speed"),
    "Agility":  ("attributes", "Agility"),
    "STR":      ("attributes", "Strength"),
    "VERT":     ("attributes", "Vertical"),
    "STAM":     ("attributes", "Stamina"),
    "INTNGBL":  ("attributes", "Intangibles"),
    "HSTL":     ("attributes", "Hustle"),

    # Badges (we’ll map tiers to numbers)
    "SHOT_TENDENCY":               ("tendencies", "Shot Tendency"),
    "TOUCHES_TENDENCY":            ("tendencies", "Touches"),
    "SHOT_CLOSE_TENDENCY":         ("tendencies", "Shot Close"),
    "SHOT_UNDER_BASKET_TENDENCY":  ("tendencies", "Shot Under Basket"),
    "SHOT_MID-RANGE_TENDENCY":     ("tendencies", "Shot Mid"),
    "SHOT_THREE_TENDENCY":         ("tendencies", "Shot Three"),
    "DRIVING_LAYUP_TENDENCY":      ("tendencies", "Driving Layup Tendency"),
    "STANDING_DUNK_TENDENCY":      ("tendencies", "Standing Dunk Tendency"),
    "DRIVING_DUNK_TENDENCY":       ("tendencies", "Driving Dunk Tendency"),
    "PUTBACK_TENDENCY":            ("tendencies", "Putback Dunk"),
    "CRASH_TENDENCY":              ("tendencies", "Crash"),
    "DRIVE_TENDENCY":              ("tendencies", "Drive"),
    "POST_UP_TENDENCY":            ("tendencies", "Post Up"),
    "SHOOT_FROM_POST_TENDENCY":    ("tendencies", "Post Shoot"),
    "ROLL_VS._POP_TENDENCY":       ("tendencies", "Roll Vs Pop"),
    "PASS_INTERCEPTION_TENDENCY":  ("tendencies", "Pass Interception"),
    "TAKE_CHARGE_TENDENCY":        ("tendencies", "Take Charge"),
    "ON-BALL_STEAL_TENDENCY":      ("tendencies", "Steal Tendency"),
    "CONTEST_SHOT_TENDENCY":       ("tendencies", "Contest Shot"),
    "BLOCK_SHOT_TENDENCY":         ("tendencies", "Block Tendency"),
    "FOUL_TENDENCY":               ("tendencies", "Foul"),

    "Aerial Wizard":       ("badges", "Aerial Wizard"),
    "Ankle Assassin":      ("badges", "Ankle Assassin"),
    "Bail Out":            ("badges", "Bail Out"),
    "Boxout Beast":        ("badges", "Boxout Beast"),
    "Break Starter":       ("badges", "Break Starter"),
    "Brick Wall":          ("badges", "Brick Wall"),
    "Challenger":          ("badges", "Challenger"),
    "Deadeye":             ("badges", "Deadeye"),
    "Dimer":               ("badges", "Dimer"),
    "Float Game":          ("badges", "Float Game"),
    "Glove":               ("badges", "Glove"),
    "Handles for Days":    ("badges", "Handles for Days"),
    "High Flying Denier":  ("badges", "High-Flying Denier"),
    "Hook Specialist":     ("badges", "Hook Specialist"),
    "Immovable Enforcer":  ("badges", "Immovable Enforcer"),
    "Interceptor":         ("badges", "Interceptor"),
    "Layup Mixmaster":     ("badges", "Layup Mixmaster"),
    "Lightning Launch":    ("badges", "Lightning Launch"),
    "Limitless Range":     ("badges", "Limitless Range"),
    "Mini Marksman":       ("badges", "Mini Marksman"),
    "Off Ball Pest":       ("badges", "Off-Ball Pest"),
    "On-Ball Menace":      ("badges", "On-Ball Menace"),
    "Paint Patroller":     ("badges", "Paint Patroller"),
    "Paint Prodigy":       ("badges", "Paint Prodigy"),
    "Physical Finisher":   ("badges", "Physical Finisher"),
    "Pick Dodger":         ("badges", "Pick Dodger"),
    "Pogo Stick":          ("badges", "Pogo Stick"),
    "Posterizer":          ("badges", "Posterizer"),
    "Post Fade Phenom":    ("badges", "Post Fade Phenom"),
    "Post Lockdown":       ("badges", "Post Lockdown"),
    "Post Powerhouse":     ("badges", "Post Powerhouse"),
    "Post Up Poet":        ("badges", "Post-Up Poet"),
    "Rebound Chaser":      ("badges", "Rebound Chaser"),
    "Rise Up":             ("badges", "Rise Up"),
    "Set Shot Specialist": ("badges", "Set Shot Specialist"),
    "Shifty Shooter":      ("badges", "Shifty Shooter"),
    "Slippery Off Ball":   ("badges", "Slippery Off-Ball"),
    "Strong Handle":       ("badges", "Strong Handle"),
    "Unpluckable":         ("badges", "Unpluckable"),
    "Versatile Visionary": ("badges", "Versatile Visionary"),
}

# Optional renames to match your helper’s expected keys
OFFSET_ALIAS = {
    "3pt Shot": "Three Point",
    "Speed with Ball": "Speed With Ball",
    "Midrange Shot": "Mid Range",
    "Post Moves": "Post Control",
    "Pass Accuracy": "Passing Accuracy",
    "Pass Perception": "Passing Perception",
    "Secondary Position": "Position 2",
    "Ankle Assassin": "Ankle Breaker",
    "Handles for Days": "Handles For Days",
    "High-Flying Denier": "High Flying Denier",
    "Off-Ball Pest": "Off Ball Pest",
    "On-Ball Menace": "On Ball Menace",
    "Post-Up Poet": "Post Up Poet",
    "Slippery Off-Ball": "Slippery Off Ball",
    "Strong Handle": "Strong Handles",
}

PLAYER_NAME_OUTPUT_OVERRIDES = {
    "AJ Lawson": ("A.J.", "Lawson"),
    "Andre Jackson Jr": ("Andre", "Jackson Jr."),
    "Craig Porter Jr": ("Craig", "Porter Jr."),
    "DAngelo Russell": ("D'Angelo", "Russell"),
    "DayRon Sharpe": ("Day'Ron", "Sharpe"),
    "DaRon Holmes": ("DaRon", "Holmes II"),
    "DeAaron Fox": ("De'Aaron", "Fox"),
    "DeAndre Hunter": ("De'Andre", "Hunter"),
    "DeAnthony Melton": ("De'Anthony", "Melton"),
    "Derrick Jones Jr": ("Derrick", "Jones Jr."),
    "Doug Mcdermott": ("Doug", "McDermott"),
    "EJ Liddell": ("E.J.", "Liddell"),
    "Eli NDiaye": ("Eli", "N'Diaye"),
    "Gary Trent Jr": ("Gary", "Trent Jr."),
    "Jabari Smith Jr": ("Jabari", "Smith Jr."),
    "Jaren Jackson Jr": ("Jaren", "Jackson Jr."),
    "Jaime Jaquez Jr": ("Jaime", "Jaquez Jr."),
    "JaKobe Walter": ("Ja'Kobe", "Walter"),
    "JaeSean Tate": ("Jae'Sean", "Tate"),
    "Kelly Oubre Jr": ("Kelly", "Oubre Jr."),
    "Kelel Ware": ("Kel'el", "Ware"),
    "Kevin Mccullar Jr": ("Kevin", "McCullar Jr."),
    "Kevin Porter Jr": ("Kevin", "Porter Jr."),
    "Larry Nance Jr": ("Larry", "Nance Jr."),
    "Lindy Waters": ("Lindy", "Waters III"),
    "LJ Cryer": ("L.J.", "Cryer"),
    "Lebron James": ("LeBron", "James"),
    "Marvin Bagley": ("Marvin", "Bagley III"),
    "Michael Porter Jr": ("Michael", "Porter Jr."),
    "Moe Wagner": ("Moritz", "Wagner"),
    "NFaly Dante": ("N'Faly", "Dante"),
    "NaeQwan Tomlin": ("Nae'Qwan", "Tomlin"),
    "Nick Smith Jr": ("Nick", "Smith Jr."),
    "Noah Essengue": ("Noa", "Essengue"),
    "Patrick Baldwin": ("Patrick", "Baldwin Jr."),
    "PJ Washington": ("P.J.", "Washington"),
    "Robert Williams": ("Robert", "Williams III"),
    "Ron Harper Jr": ("Ron", "Harper Jr."),
    "Royce ONeale": ("Royce", "O'Neale"),
    "Scotty Pippen Jr": ("Scotty", "Pippen Jr."),
    "Terrence Shannon Jr": ("Terrence", "Shannon Jr."),
    "Terry Rozier": ("Terry", "Rozier III"),
    "Tim Hardaway Jr": ("Tim", "Hardaway Jr."),
    "TJ Mcconnell": ("T.J.", "McConnell"),
    "Trey Jemison": ("Trey", "Jemison III"),
    "Trey Murphy": ("Trey", "Murphy III"),
    "TyTy Washington": ("TyTy", "Washington Jr."),
    "Vince Williams Jr": ("Vince", "Williams Jr."),
    "VJ Edgecombe": ("V.J.", "Edgecombe"),
    "Walter Clayton Jr": ("Walter", "Clayton Jr."),
    "Wendell Carter Jr": ("Wendell", "Carter Jr."),
    "Wendell Moore Jr": ("Wendell", "Moore Jr."),
    "Yanic Konan Niederhauser": ("Yanic", "Konan Niederhauser"),
}

# Treat these as text-only; we won’t export them unless forced
STRING_FIELD_OFFSETS = {"Position", "Secondary Position"}

# Badge tier normalization
BADGE_TIER_MAP = {
    "none": 0, "": 0, "0": 0, "off": 0, "no": 0,
    "bronze": 1, "1": 1,
    "silver": 2, "2": 2,
    "gold": 3, "3": 3,
    "hall of fame": 4, "hof": 4, "h.o.f": 4, "legend": 4, "4": 4,
}

# --------------------------------------------------
# TEMP: Hard-coded Play Type → numeric code mapping
# Adjust these codes as you trial in-game.
# --------------------------------------------------
_LEGACY_PLAYTYPE_CODE_MAPS_UNUSED = {
    1: {  # Playtype 1 mapping
        "PRBallHandler": 4,
        "Isolation": 2,
        "Cut": 9,
        "OffScreen": 8,
        "Handoff": 5,
        "PRRollMan": 3,
        "Postup": 7,
        "Other": 0,
        "P&RWing": 2,
    },
    2: {  # Playtype 2 mapping (STARTER GUESS — tweak by testing)
        "PRBallHandler": 4,
        "Isolation": 2,
        "Cut": 9,
        "OffScreen": 8,
        "Handoff": 5,
        "PRRollMan": 3,
        "Postup": 7,
        "Putbacks": 8,
        "Other": 0,
        "IsolationPoint": 4,
        "IsolationWing": 6,
        "P&RWing": 5,
        "P&RPoint": 5,
    },
    3: {  # Playtype 3 mapping (STARTER GUESS)
        "PRBallHandler": 4,
        "Isolation": 2,
        "Cut": 9,
        "OffScreen": 8,
        "Handoff": 5,
        "PRRollMan": 3,
        "Postup": 7,
        "Putbacks": 8,
        "Other": 0,
        "P&RPoint": 5,
        "IsolationPoint": 4,
    },
    4: {  # Playtype 4 mapping (STARTER GUESS)
        "PRBallHandler": 4,
        "Isolation": 2,
        "Cut": 9,
        "OffScreen": 8,
        "Handoff": 5,
        "PRRollMan": 3,
        "Postup": 7,
        "Putbacks": 8,
        "Other": 0,
        "P&RPoint": 4,
        "P&RWing": 7,
    },
}

PLAYTYPE_CODE_BY_LABEL = {
    "Isolation": 1,
    "Isolation Point": 2,
    "Isolation Wing": 3,
    "P&R Ball Handler": 4,
    "P&R Point": 5,
    "P&R Wing": 6,
    "P&R Roll Man": 7,
    "Post Up Low": 8,
    "Post Up High": 9,
    "Guard Post Up": 10,
    "Cutter": 11,
    "Handoff Receiver": 12,
    "Mid Range": 14,
    "3 PT": 15,
}

PLAYTYPE_NBA_FILES = [
    "playtype_transition_tmp.csv",
    "playtype_spotup_tmp.csv",
    "playtype_postup_tmp.csv",
    "playtype_offscreen_tmp.csv",
    "playtype_pnr_handler_tmp.csv",
    "playtype_pnr_rollman_tmp.csv",
    "playtype_handoff_tmp.csv",
    "playtype_cut_tmp.csv",
    "playtype_iso_tmp.csv",
]


def normalize_column_name(name: str) -> str:
    return re.sub(r"\s+", " ", str(name).strip())


CANONICAL_COLUMN_BY_NORMALIZED = {
    normalize_column_name(column): column for column in COLUMN_MAPPINGS.keys()
}
TENDENCY_SOURCE_COLUMNS = [
    column for column, mapping in COLUMN_MAPPINGS.items()
    if mapping and mapping[0] == "tendencies"
]


def _norm_badge_value(x):
    if pd.isna(x):
        return None
    s = str(x).strip()
    low = s.lower()
    if low in BADGE_TIER_MAP:
        return BADGE_TIER_MAP[low]
    # Also accept plain numerics like 0..5
    try:
        f = float(s)
        if math.isfinite(f):
            return int(round(f))
    except:
        pass
    return None

def _parse_value_and_op(raw, default_op="Set"):
    """
    Accepts: 85 -> (Set, 85)
             '+5' -> (Increment, 5)
             '-3' -> (Decrement, 3)
    Returns (op, val) or (None, None) if not numeric.
    """
    if pd.isna(raw):
        return None, None
    s = str(raw).strip()
    if s == "":
        return None, None

    if s.startswith("+"):
        try:
            v = int(round(float(s[1:])))
            return "Increment", v
        except:
            return None, None
    if s.startswith("-"):
        try:
            v = int(round(abs(float(s[1:]))))
            return "Decrement", v
        except:
            return None, None

    # Otherwise parse numeric and use default_op
    try:
        v = int(round(float(s)))
        return default_op, v
    except:
        return None, None


def _norm_tendency_value(raw):
    if pd.isna(raw):
        return None
    s = str(raw).strip()
    if s == "":
        return None
    try:
        value = float(s)
    except Exception:
        return None
    if not math.isfinite(value):
        return None
    if 0 <= value <= 1.5:
        value *= 100.0
    return max(0, min(100, int(round(value))))

def _alias(name):
    return OFFSET_ALIAS.get(name, name)


def _normalize_merge_text(series: pd.Series) -> pd.Series:
    return series.fillna("").astype(str).str.strip()


def merge_tendency_data(df: pd.DataFrame, tendency_source: str, *, season: str | None = None) -> pd.DataFrame:
    missing_columns = [column for column in TENDENCY_SOURCE_COLUMNS if column not in df.columns]
    if not missing_columns or not tendency_source:
        return df

    tendency_path = tendency_source
    if not os.path.isabs(tendency_path):
        local_candidate = os.path.normpath(os.path.join(SCRIPT_DIR, tendency_source))
        repo_candidate = os.path.normpath(os.path.join(REPO_DIR, tendency_source))
        if os.path.exists(local_candidate):
            tendency_path = local_candidate
        elif os.path.exists(repo_candidate):
            tendency_path = repo_candidate

    if not os.path.exists(tendency_path):
        print(f"[WARN] Tendency source not found, skipping tendency merge: {tendency_source}")
        return df

    suffix = os.path.splitext(tendency_path)[1].lower()
    if suffix == ".csv":
        tendency_df = pd.read_csv(tendency_path, dtype=str)
    else:
        tendency_df = pd.read_excel(tendency_path, dtype=str)

    if "NBA_ID" in tendency_df.columns and "NBA ID" not in tendency_df.columns:
        tendency_df = tendency_df.rename(columns={"NBA_ID": "NBA ID"})

    available_columns = [column for column in missing_columns if column in tendency_df.columns]
    if not available_columns:
        print(f"[WARN] No recognized tendency columns found in {tendency_path}")
        return df

    merged = df.copy()
    if "Season" in merged.columns:
        merged["Season"] = _normalize_merge_text(merged["Season"])
    if "Season" in tendency_df.columns:
        tendency_df["Season"] = _normalize_merge_text(tendency_df["Season"])
    if season is not None and "Season" in tendency_df.columns:
        tendency_df = tendency_df[tendency_df["Season"] == str(season).strip()].copy()

    if "NBA ID" in merged.columns and "NBA ID" in tendency_df.columns and "Season" in merged.columns and "Season" in tendency_df.columns:
        merged["NBA ID"] = _normalize_merge_text(merged["NBA ID"])
        tendency_df["NBA ID"] = _normalize_merge_text(tendency_df["NBA ID"])
        tendency_id = tendency_df[["NBA ID", "Season", *available_columns]].drop_duplicates(["NBA ID", "Season"])
        merged = merged.merge(tendency_id, on=["NBA ID", "Season"], how="left")

    for column in available_columns:
        if column not in merged.columns:
            merged[column] = pd.NA

    unresolved_mask = merged[available_columns].isna().all(axis=1)
    if unresolved_mask.any() and "Player" in merged.columns and "Player" in tendency_df.columns and "Season" in merged.columns and "Season" in tendency_df.columns:
        merged["_player_key"] = merged["Player"].map(normalize_player_name)
        tendency_player = tendency_df[["Season", "Player", *available_columns]].copy()
        tendency_player["_player_key"] = tendency_player["Player"].map(normalize_player_name)
        tendency_player = tendency_player.drop_duplicates(["Season", "_player_key"])
        fallback = merged.loc[unresolved_mask, ["Season", "_player_key"]].merge(
            tendency_player[["Season", "_player_key", *available_columns]],
            on=["Season", "_player_key"],
            how="left",
        )
        for column in available_columns:
            merged.loc[unresolved_mask, column] = fallback[column].values
        merged = merged.drop(columns=["_player_key"])

    return merged

def _resolve_playtype_sources(playtype_source: str) -> list[str]:
    script_dir = os.path.dirname(__file__)
    repo_dir = os.path.dirname(script_dir)

    candidates = []
    if playtype_source:
        candidates.append(playtype_source)
        if not os.path.isabs(playtype_source):
            candidates.append(os.path.join(script_dir, playtype_source))
            candidates.append(os.path.join(repo_dir, playtype_source))

    candidates.extend([
        os.path.join(repo_dir, "stats", "tmp"),
        os.path.join(repo_dir, "stats", "history"),
    ])

    seen = set()
    for candidate in candidates:
        if not candidate:
            continue
        candidate = os.path.normpath(candidate)
        if candidate in seen or not os.path.exists(candidate):
            continue
        seen.add(candidate)

        if os.path.isfile(candidate):
            return [candidate]

        if os.path.isdir(candidate):
            names = PLAYTYPE_NBA_FILES
            if os.path.basename(candidate).lower() == "history":
                names = [name.replace("_tmp", "") for name in PLAYTYPE_NBA_FILES]
            paths = [os.path.join(candidate, name) for name in names if os.path.exists(os.path.join(candidate, name))]
            if paths:
                return paths

    return []


def load_playtype_summary(playtype_source: str, season: str | None = None) -> dict:
    """Return {player_key: [{"play_type": ..., "poss_pct": ...}, ...]} sorted by poss%."""
    sources = _resolve_playtype_sources(playtype_source)
    if not sources:
        print(f"[WARN] no playtype source found for: {playtype_source}")
        return {}

    frames = []
    for source in sources:
        try:
            frame = pd.read_csv(source)
        except Exception as exc:
            print(f"[WARN] failed to load playtype source {source}: {exc}")
            continue
        frame.columns = [col.strip() for col in frame.columns]
        frame = frame.rename(columns={
            "PLAYER_NAME": "Player",
            "PLAY_TYPE": "Play Type",
            "POSS_PCT": "Possession %",
        })
        if "Player" not in frame.columns or "Play Type" not in frame.columns or "Possession %" not in frame.columns:
            print(f"[WARN] playtype source columns unexpected: {source}")
            continue
        if season is not None and "Season" in frame.columns:
            frame = frame[frame["Season"].astype(str).str.strip() == str(season).strip()].copy()
        frame["Player"] = frame["Player"].astype(str).str.strip()
        frame["Play Type"] = frame["Play Type"].astype(str).str.strip()
        frame["Possession %"] = pd.to_numeric(frame["Possession %"], errors="coerce")
        frame = frame.dropna(subset=["Possession %"])
        frame = frame[frame["Play Type"].ne("")]
        frame["PlayerKey"] = frame["Player"].map(normalize_player_name)
        frames.append(frame[["PlayerKey", "Play Type", "Possession %"]])

    if not frames:
        return {}

    playtype_df = pd.concat(frames, ignore_index=True)
    playtype_df = (
        playtype_df
        .sort_values(["PlayerKey", "Play Type", "Possession %"], ascending=[True, True, False])
        .drop_duplicates(subset=["PlayerKey", "Play Type"], keep="first")
        .sort_values(["PlayerKey", "Possession %", "Play Type"], ascending=[True, False, True])
    )

    summary = {}
    for player_key, group in playtype_df.groupby("PlayerKey"):
        summary[player_key] = [
            {"play_type": row["Play Type"], "poss_pct": float(row["Possession %"])}
            for _, row in group.iterrows()
        ]
    return summary


def load_input_table(input_path: str) -> pd.DataFrame:
    suffix = os.path.splitext(input_path)[1].lower()
    if suffix == ".csv":
        df = pd.read_csv(input_path, dtype=str)
    else:
        df = pd.read_excel(input_path, dtype=str)

    rename_map = {}
    for col in df.columns:
        normalized = normalize_column_name(col)
        canonical = CANONICAL_COLUMN_BY_NORMALIZED.get(normalized)
        if canonical and canonical != col:
            rename_map[col] = canonical
    if rename_map:
        df = df.rename(columns=rename_map)
    return df

def normalize_player_name(name: str) -> str:
    """Normalize player full name for matching across datasets.
    - Remove common suffixes: Jr., Sr., II, III, IV, V
    - Ignore punctuation/casing differences
    """
    if name is None:
        return ""
    s = str(name).strip().lower()
    s = re.sub(r"\s+(jr\.?|sr\.?|ii|iii|iv|v)\s*$", "", s, flags=re.IGNORECASE)
    s = re.sub(r"[^a-z0-9]+", "", s)
    return s


def apply_output_name_override(first: str, last: str) -> tuple[str, str]:
    full_name = f"{first} {last}".strip()
    override = PLAYER_NAME_OUTPUT_OVERRIDES.get(full_name)
    if override:
        return override
    return first, last

def _row_int(row, column_name: str) -> int:
    raw = row.get(column_name)
    if pd.isna(raw) or raw is None:
        return 0
    try:
        return int(round(float(str(raw).strip())))
    except Exception:
        return 0


def _row_text(row, column_name: str) -> str:
    raw = row.get(column_name)
    if pd.isna(raw) or raw is None:
        return ""
    return str(raw).strip()


def _playtype_candidates(play_type: str, row) -> list[str]:
    primary = _row_text(row, "Primary_Position").upper()
    secondary = _row_text(row, "Second_Position").upper()

    layup = _row_int(row, "Layup")
    st_dunk = _row_int(row, "ST Dunk")
    dunk = _row_int(row, "Dunk")
    mid = _row_int(row, "Mid")
    three = _row_int(row, "3PT")
    post_control = _row_int(row, "PostC")
    post_hook = _row_int(row, "PHook")
    post_fade = _row_int(row, "PFade")
    ball = _row_int(row, "Ball")
    passing = _row_int(row, "Pass")
    vision = _row_int(row, "Vision")

    is_point = primary == "PG"
    is_guard = primary in {"PG", "SG"} or secondary in {"PG", "SG"}
    is_wing = primary in {"SG", "SF"} or secondary in {"SG", "SF"}
    is_big = primary in {"PF", "C"} or secondary in {"PF", "C"}
    is_shooter = three >= max(mid + 3, 78)
    is_midrange = mid >= max(three + 3, 78)
    is_slasher = max(dunk, st_dunk) >= 75 or layup >= 82
    is_post_fade = post_fade >= max(post_hook + 3, post_control + 5)
    is_primary_creator = ball >= 80 and max(passing, vision) >= 75
    is_pass_first_point = is_point and passing >= 88 and vision >= 85 and passing >= (ball - 3)

    if play_type == "PRBallHandler":
        if is_pass_first_point:
            return ["P&R Point", "P&R Ball Handler"]
        if primary in {"SG", "SF"} and ball >= 78 and passing < 82 and vision < 80:
            return ["P&R Wing", "P&R Ball Handler"]
        return ["P&R Ball Handler", "P&R Point", "P&R Wing"]

    if play_type == "PRRollMan":
        if is_big:
            return ["P&R Roll Man", "Cutter"]
        if is_wing:
            return ["P&R Wing", "Cutter", "P&R Roll Man"]
        return ["P&R Roll Man", "P&R Wing", "Cutter"]

    if play_type == "Isolation":
        if is_pass_first_point:
            return ["Isolation Point", "Isolation"]
        if primary == "SG" or (primary == "SF" and secondary not in {"PF", "C"} and post_control < 75):
            return ["Isolation Wing", "Isolation"]
        return ["Isolation", "Isolation Wing", "Isolation Point"]

    if play_type == "Postup":
        if is_guard and post_control >= 65:
            return ["Guard Post Up", "Post Up High", "Post Up Low"]
        if is_big and (post_control >= 75 or post_hook >= 70):
            return ["Post Up Low", "Post Up High"]
        if is_big and not (is_post_fade and post_fade >= post_control + 12 and post_fade >= post_hook + 8):
            return ["Post Up Low", "Post Up High"]
        if is_post_fade or (mid >= 80 and post_fade >= 75):
            return ["Post Up High", "Post Up Low"]
        return ["Post Up Low", "Post Up High"]

    if play_type == "Spotup":
        if three >= 75 and (is_guard or is_wing or three >= mid - 5):
            return ["3 PT", "Handoff Receiver", "Mid Range"]
        if is_midrange and three < 72:
            return ["Mid Range", "Handoff Receiver", "3 PT"]
        return ["3 PT" if three >= mid else "Mid Range", "Handoff Receiver"]

    if play_type == "OffScreen":
        if three >= 72 and (is_guard or is_wing or three >= mid):
            return ["3 PT", "Handoff Receiver", "Mid Range"]
        return ["Mid Range", "Handoff Receiver", "3 PT"]

    if play_type == "Handoff":
        if is_shooter:
            return ["Handoff Receiver", "3 PT", "Mid Range"]
        return ["Handoff Receiver", "Mid Range" if mid >= three else "3 PT"]

    if play_type == "Cut":
        if is_big and max(dunk, st_dunk) >= 70:
            return ["P&R Roll Man", "Cutter"]
        return ["Cutter", "P&R Wing" if is_wing else "P&R Roll Man"]

    if play_type == "Transition":
        if is_primary_creator and (is_point or ball >= 85):
            if is_pass_first_point:
                return ["P&R Point", "P&R Ball Handler", "3 PT"]
            return ["P&R Ball Handler", "3 PT", "P&R Wing"]
        if ball >= 75 and max(passing, vision) >= 70:
            return ["P&R Ball Handler", "Cutter", "3 PT"]
        if is_shooter and not is_slasher:
            return ["3 PT", "Handoff Receiver", "P&R Wing"]
        if is_big:
            return ["P&R Roll Man", "Cutter", "Post Up Low"]
        if is_slasher:
            return ["Cutter", "Isolation", "P&R Wing"]
        if is_wing:
            return ["P&R Wing", "Cutter", "3 PT"]
        return ["Cutter", "3 PT", "P&R Ball Handler"]

    return []


def build_playtype_vitals(playtype_rows: list[dict], row) -> dict:
    labels = []
    for item in playtype_rows:
        candidates = _playtype_candidates(item["play_type"], row)
        if not candidates:
            continue
        candidate = candidates[0]
        if candidate in PLAYTYPE_CODE_BY_LABEL and candidate not in labels:
            labels.append(candidate)
        if len(labels) >= 4:
            break

    return {
        f"Playtype {index + 1}": PLAYTYPE_CODE_BY_LABEL[label]
        for index, label in enumerate(labels[:4])
    }

def excel_to_actions(
    input_xlsx: str,
    output_json: str = "players_actions.json",
    include_vitals: bool = False,
    playtype_csv: str = "stats/tmp",
    tendency_csv: str = DEFAULT_TENDENCY_CSV,
    default_attr_op: str = "Set",
    default_badge_op: str = "Set",
    season: str | None = None,
):
    if default_attr_op not in {"Set", "Increment", "Decrement"}:
        raise ValueError("default_attr_op must be Set|Increment|Decrement")
    if default_badge_op not in {"Set", "Increment", "Decrement"}:
        raise ValueError("default_badge_op must be Set|Increment|Decrement")

    df = load_input_table(input_xlsx)
    if season is not None:
        if "Season" not in df.columns:
            raise ValueError(f"--season was provided but no Season column exists in {input_xlsx}")
        df = df[df["Season"].astype(str).str.strip() == str(season).strip()].copy()
    df = merge_tendency_data(df, tendency_csv, season=season)

    # Optional: load playtypes and store numeric codes under Set→Vitals.
    playtype_summary = load_playtype_summary(playtype_csv, season=season)

    actions = []

    for _, row in df.iterrows():
        first = (row.get("PLAYER_FIRST_NAME") or "").strip()
        last  = (row.get("PLAYER_LAST_NAME") or "").strip()
        if not first or not last:
            continue
        output_first, output_last = apply_output_name_override(first, last)

        # Accumulate buckets
        set_vitals = {}
        set_attrs, set_badges, set_tendencies = {}, {}, {}
        inc_attrs, inc_badges = {}, {}
        dec_attrs, dec_badges = {}, {}

        for col in df.columns:
            if col in ("PLAYER_FIRST_NAME", "PLAYER_LAST_NAME"):
                continue

            mapping = COLUMN_MAPPINGS.get(col)
            if mapping is None:
                continue

            group, offset = mapping
            raw_val = row[col]

            if group == "vitals" and not include_vitals:
                # skip strings by default
                continue

            if group == "vitals":
                # Keep text values (only when include_vitals=True)
                val = "" if pd.isna(raw_val) else str(raw_val).strip()
                if val == "":
                    continue
                key = _alias(offset)
                set_vitals[key] = val
                continue

            if group == "tendencies":
                val = _norm_tendency_value(raw_val)
                if val is None:
                    continue
                key = _alias(offset)
                set_tendencies[key] = val
                continue

            if group == "badges":
                val = _norm_badge_value(raw_val)
                if val is None:
                    continue
                op = default_badge_op
            else:
                # attributes
                op, val = _parse_value_and_op(raw_val, default_op=default_attr_op)
                if op is None or val is None:
                    continue

            key = _alias(offset)

            if group == "attributes":
                if op == "Set":
                    set_attrs[key] = val
                elif op == "Increment":
                    inc_attrs[key] = val
                elif op == "Decrement":
                    dec_attrs[key] = val
            elif group == "badges":
                if op == "Set":
                    set_badges[key] = val
                elif op == "Increment":
                    inc_badges[key] = val
                elif op == "Decrement":
                    dec_badges[key] = val

        if include_vitals and playtype_summary:
            full_name = normalize_player_name(f"{first} {last}".strip())
            if full_name in playtype_summary:
                set_vitals.update(build_playtype_vitals(playtype_summary[full_name], row))

        # Build the player action if anything to write
        entry = {"First Name": output_first, "Last Name": output_last}

        if set_vitals or set_attrs or set_badges or set_tendencies:
            entry["Set"] = {}
            if set_vitals:
                entry["Set"]["Vitals"] = set_vitals
            if set_attrs:
                entry["Set"]["Attributes"] = set_attrs
            if set_tendencies:
                entry["Set"]["Tendencies"] = set_tendencies
            if set_badges:
                entry["Set"]["Badges"] = set_badges

        if inc_attrs or inc_badges:
            entry["Increment"] = {}
            if inc_attrs:
                entry["Increment"]["Attributes"] = inc_attrs
            if inc_badges:
                entry["Increment"]["Badges"] = inc_badges

        if dec_attrs or dec_badges:
            entry["Decrement"] = {}
            if dec_attrs:
                entry["Decrement"]["Attributes"] = dec_attrs
            if dec_badges:
                entry["Decrement"]["Badges"] = dec_badges

        # Skip fully empty rows
        if len(entry.keys()) > 2:
            actions.append(entry)

    with open(output_json, "w", encoding="utf-8") as f:
        json.dump(actions, f, indent=4, ensure_ascii=False)

    print(f"[OK] Wrote {len(actions)} player actions → {output_json}")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input-xlsx", default="players.xlsx", help="Input workbook or csv.")
    ap.add_argument("--output-json", default="players_actions.json")
    ap.add_argument(
        "--include-vitals",
        action="store_true",
        help="Include text vitals (Position etc.) in Set→Vitals. Also adds numeric Playtype 1..4 if playtype csv is available."
    )
    ap.add_argument(
        "--season",
        default=None,
        help="Optional season filter such as 2025-26."
    )
    ap.add_argument(
        "--playtype-csv",
        default="stats/tmp",
        help="CSV containing play types (PLAYER_NAME/PLAY_TYPE/POSS_PCT). Added to Set→Vitals as numeric Playtype 1..4 using PLAYTYPE_CODE_MAP."
    )
    ap.add_argument(
        "--tendency-csv",
        default=DEFAULT_TENDENCY_CSV,
        help="CSV/xlsx containing final tendency ratings. Defaults to stats/exports/tendency_all_ratings.csv."
    )
    ap.add_argument(
        "--default-attr-op",
        choices=["Set", "Increment", "Decrement"],
        default="Set",
        help="Default operation for numeric attributes. +n/-n cells override."
    )
    ap.add_argument(
        "--default-badge-op",
        choices=["Set", "Increment", "Decrement"],
        default="Set",
        help="Default operation for badges after tier mapping."
    )
    args = ap.parse_args()

    excel_to_actions(
        input_xlsx=args.input_xlsx,
        output_json=args.output_json,
        include_vitals=args.include_vitals,
        season=args.season,
        playtype_csv=args.playtype_csv,
        tendency_csv=args.tendency_csv,
        default_attr_op=args.default_attr_op,
        default_badge_op=args.default_badge_op,
    )

if __name__ == "__main__":
    main()
