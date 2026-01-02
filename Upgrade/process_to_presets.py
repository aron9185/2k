import argparse
import json
import math
import os
import re
import pandas as pd

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
    "Speed with Ball": "Speed With Ball",  # if your helper uses this casing
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
PLAYTYPE_CODE_MAPS = {
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

def _alias(name):
    return OFFSET_ALIAS.get(name, name)

def load_playtype_summary(playtype_csv: str) -> dict:
    """Return { 'First Last': {'Play Type 1': ..., ...} } (top 4 by Possession %).
    If file missing/unreadable, return {}.
    """
    if not playtype_csv:
        return {}
    if not os.path.exists(playtype_csv):
        print(f"[WARN] playtype csv not found: {playtype_csv} (skip playtypes)")
        return {}
    try:
        playtype_df = pd.read_csv(playtype_csv, delimiter=",")
        playtype_df.columns = [col.strip() for col in playtype_df.columns]
        # Normalize expected column names from your process.py
        playtype_df = playtype_df.rename(columns={
            "PLAYER_NAME": "Player",
            "PLAY_TYPE": "Play Type",
            "POSS_PCT": "Possession %",
        })
        if "Player" not in playtype_df.columns or "Play Type" not in playtype_df.columns:
            print(f"[WARN] playtype csv columns unexpected (need PLAYER_NAME/PLAY_TYPE/POSS_PCT): {playtype_csv}")
            return {}

        playtype_df["Player"] = playtype_df["Player"].astype(str).str.strip()
        playtype_df["Play Type"] = playtype_df["Play Type"].astype(str).str.strip()
        if "Possession %" in playtype_df.columns:
            playtype_df["Possession %"] = pd.to_numeric(playtype_df["Possession %"], errors="coerce")
        else:
            playtype_df["Possession %"] = pd.to_numeric(playtype_df.get("Possession %"), errors="coerce")

        top_playtypes = (
            playtype_df
            .sort_values(by=["Player", "Possession %"], ascending=[True, False])
            .groupby("Player")
            .head(4)
        )

        summary = {}
        for player, group in top_playtypes.groupby("Player"):
            sorted_types = group.sort_values(by="Possession %", ascending=False)["Play Type"].tolist()
            summary[player] = {f"Play Type {i+1}": play for i, play in enumerate(sorted_types)}
        return summary
    except Exception as e:
        print(f"[WARN] failed to load playtype csv {playtype_csv}: {e}")
        return {}

def normalize_player_name(name: str) -> str:
    """Normalize player full name for matching across datasets.
    - Remove common suffixes: Jr., Sr., II, III, IV, V
    - Collapse whitespace
    """
    if name is None:
        return ""
    s = str(name).strip()
    # Remove suffixes (case-insensitive): "Kenyon Martin Jr.", "Gary Payton II", "Tim Hardaway Sr."
    s = re.sub(r"\s+(Jr\.|Sr\.|II|III|IV|V)\s*$", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\s+", " ", s).strip()
    return s

def playtype_dict_to_numeric_vitals(pt_dict: dict) -> dict:
    out = {}
    if not pt_dict:
        return out

    for i in range(1, 5):
        s = pt_dict.get(f"Play Type {i}")
        if not s:
            continue
        slot_map = PLAYTYPE_CODE_MAPS.get(i, {})
        if s not in slot_map:
            print(f"[WARN] unmapped playtype slot={i}: {s!r} -> using 0")
        out[f"Playtype {i}"] = int(slot_map.get(s, 0))
    return out

def excel_to_actions(
    input_xlsx: str,
    output_json: str = "players_actions.json",
    include_vitals: bool = False,
    playtype_csv: str = "playtype.csv",
    default_attr_op: str = "Set",
    default_badge_op: str = "Set",
):
    if default_attr_op not in {"Set", "Increment", "Decrement"}:
        raise ValueError("default_attr_op must be Set|Increment|Decrement")
    if default_badge_op not in {"Set", "Increment", "Decrement"}:
        raise ValueError("default_badge_op must be Set|Increment|Decrement")

    df = pd.read_excel(input_xlsx, dtype=str)

    # Optional: load playtypes and store numeric codes under Set→Vitals.
    #playtype_summary = load_playtype_summary(playtype_csv)

    actions = []

    for _, row in df.iterrows():
        first = (row.get("PLAYER_FIRST_NAME") or "").strip()
        last  = (row.get("PLAYER_LAST_NAME") or "").strip()
        if not first or not last:
            continue

        # Accumulate buckets
        set_vitals = {}
        set_attrs, set_badges = {}, {}
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

        # Add playtypes (numeric) into vitals if available
        # if include_vitals and playtype_summary:
        #     full_name = normalize_player_name(f"{first} {last}".strip())
        #     if full_name in playtype_summary:
        #         set_vitals.update(playtype_dict_to_numeric_vitals(playtype_summary[full_name]))

        # Build the player action if anything to write
        entry = {"First Name": first, "Last Name": last}

        if set_vitals or set_attrs or set_badges:
            entry["Set"] = {}
            if set_vitals:
                entry["Set"]["Vitals"] = set_vitals
            if set_attrs:
                entry["Set"]["Attributes"] = set_attrs
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
    ap.add_argument("--input-xlsx", default="players.xlsx")
    ap.add_argument("--output-json", default="players_actions.json")
    ap.add_argument(
        "--include-vitals",
        default=False,
        help="Include text vitals (Position etc.) in Set→Vitals. Also adds numeric Playtype 1..4 if playtype csv is available."
    )
    ap.add_argument(
        "--playtype-csv",
        default="playtype.csv",
        help="CSV containing play types (PLAYER_NAME/PLAY_TYPE/POSS_PCT). Added to Set→Vitals as numeric Playtype 1..4 using PLAYTYPE_CODE_MAP."
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
        playtype_csv=args.playtype_csv,
        default_attr_op=args.default_attr_op,
        default_badge_op=args.default_badge_op,
    )

if __name__ == "__main__":
    main()
