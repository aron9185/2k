import pandas as pd
import json
import math

# -----------------------------------------------------------------------------
# 1) Define a mapping from each Excel column to:
#    - Which sub-dict it belongs to ("attributes", "badges", or None if not used)
#    - The JSON field name to use
#
#    Columns with "NaN" in your mapping are excluded (set to None).
# -----------------------------------------------------------------------------
COLUMN_MAPPINGS = {
    "NBA ID": None,
    "Season": None,
    "Team(s)": None,
    "MIN": None,
    "Overall": None,

    # We no longer use "Player" for the JSON key
    "Player": None,

    # "PLAYER_FIRST_NAME" and "PLAYER_LAST_NAME" are used to build the JSON key
    # so we do not map them to "attributes" or "badges" here.
    
    # Vitals
    "Primary_Position":("vitals", "Position"),
    "Second_Position": ("vitals", "Secondary Position"),
            
    # Attributes
    "Layup":           ("attributes", "Driving Layup"),
    "ST Dunk":         ("attributes", "Standing Dunk"),
    "Dunk":            ("attributes", "Driving Dunk"),
    "Close":           ("attributes", "Close Shot"),
    "Mid":             ("attributes", "Midrange Shot"),
    "3PT":             ("attributes", "3pt Shot"),
    "FT":              ("attributes", "Free Throw"),
    "PHook":           ("attributes", "Post Hook"),
    "PFade":           ("attributes", "Post Fade"),
    "PostC":           ("attributes", "Post Moves"),
    "Foul":            ("attributes", "Draw Foul"),
    "SHOTIQ":          ("attributes", "Shot IQ"),
    "Ball":            ("attributes", "Ball Control"),
    "SPD/BALL":        ("attributes", "Speed with Ball"),
    "Hands":           ("attributes", "Hands"),
    "Pass":            ("attributes", "Pass Accuracy"),
    "Pass IQ":         ("attributes", "Passing IQ"),
    "Vision":          ("attributes", "Passing Vision"),
    "OCNST":           ("attributes", "Offensive Consistency"),
    "ID":              ("attributes", "Interior Defense"),
    "PD":              ("attributes", "Perimeter Defense"),
    "STL":             ("attributes", "Steal"),
    "BLK":             ("attributes", "Block"),
    "OREB":            ("attributes", "Offensive Rebound"),
    "DREB":            ("attributes", "Defensive Rebound"),
    "HelpDIQ":         ("attributes", "Help Defense IQ"),
    "PSPER":           ("attributes", "Pass Perception"),
    "DCNST":           ("attributes", "Defensive Consistency"),
    "SPEED":           ("attributes", "Speed"),
    "Agility":         ("attributes", "Agility"),
    "STR":             ("attributes", "Strength"),
    "VERT":            ("attributes", "Vertical"),
    "STAM":            ("attributes", "Stamina"),
    "INTNGBL":         ("attributes", "Intangibles"),
    "HSTL":            ("attributes", "Hustle"),

    # Badges
    "Aerial Wizard":         ("badges", "Aerial Wizard"),
    "Ankle Assassin":        ("badges", "Ankle Assassin"),
    "Bail Out":              ("badges", "Bail Out"),
    "Boxout Beast":          ("badges", "Boxout Beast"),
    "Break Starter":         ("badges", "Break Starter"),
    "Brick Wall":            ("badges", "Brick Wall"),
    "Challenger":            ("badges", "Challenger"),
    "Deadeye":               ("badges", "Deadeye"),
    "Dimer":                 ("badges", "Dimer"),
    "Float Game":            ("badges", "Float Game"),
    "Glove":                 ("badges", "Glove"),
    "Handles for Days":      ("badges", "Handles for Days"),
    "High Flying Denier":    ("badges", "High-Flying Denier"),
    "Hook Specialist":       ("badges", "Hook Specialist"),
    "Immovable Enforcer":    ("badges", "Immovable Enforcer"),
    "Interceptor":           ("badges", "Interceptor"),
    "Layup Mixmaster":       ("badges", "Layup Mixmaster"),
    "Lightning Launch":      ("badges", "Lightning Launch"),
    "Limitless Range":       ("badges", "Limitless Range"),
    "Mini Marksman":         ("badges", "Mini Marksman"),
    "Off Ball Pest":         ("badges", "Off-Ball Pest"),
    "On-Ball Menace":        ("badges", "On-Ball Menace"),
    "Paint Patroller":       ("badges", "Paint Patroller"),
    "Paint Prodigy":         ("badges", "Paint Prodigy"),
    "Physical Finisher":     ("badges", "Physical Finisher"),
    "Pick Dodger":           ("badges", "Pick Dodger"),
    "Pogo Stick":            ("badges", "Pogo Stick"),
    "Posterizer":            ("badges", "Posterizer"),
    "Post Fade Phenom":      ("badges", "Post Fade Phenom"),
    "Post Lockdown":         ("badges", "Post Lockdown"),
    "Post Powerhouse":       ("badges", "Post Powerhouse"),
    "Post Up Poet":          ("badges", "Post-Up Poet"),
    "Rebound Chaser":        ("badges", "Rebound Chaser"),
    "Rise Up":               ("badges", "Rise Up"),
    "Set Shot Specialist":   ("badges", "Set Shot Specialist"),
    "Shifty Shooter":        ("badges", "Shifty Shooter"),
    "Slippery Off Ball":     ("badges", "Slippery Off-Ball"),
    "Strong Handle":         ("badges", "Strong Handle"),
    "Unpluckable":           ("badges", "Unpluckable"),
    "Versatile Visionary":   ("badges", "Versatile Visionary"),
}

TEAM_NAME_MAP = {
    "Atlanta Hawks": "Current Hawks",
    "Boston Celtics": "Current Celtics",
    "Brooklyn Nets": "Current Nets",
    "Charlotte Hornets": "Current Hornets",
    "Chicago Bulls": "Current Bulls",
    "Cleveland Cavaliers": "Current Cavaliers",
    "Dallas Mavericks": "Current Mavericks",
    "Denver Nuggets": "Current Nuggets",
    "Detroit Pistons": "Current Pistons",
    "Golden State Warriors": "Current Warriors",
    "Houston Rockets": "Current Rockets",
    "Indiana Pacers": "Current Pacers",
    "Los Angeles Clippers": "Current Clippers",
    "Los Angeles Lakers": "Current Lakers",
    "Memphis Grizzlies": "Current Grizzlies",
    "Miami Heat": "Current Heat",
    "Milwaukee Bucks": "Current Bucks",
    "Minnesota Timberwolves": "Current Timberwolves",
    "New Orleans Pelicans": "Current Pelicans",
    "New York Knicks": "Current Knicks",
    "Oklahoma City Thunder": "Current Thunder",
    "Orlando Magic": "Current Magic",
    "Philadelphia Sixers": "Current 76ers",
    "Phoenix Suns": "Current Suns",
    "Portland Trail Blazers": "Current Trail Blazers",
    "Sacramento Kings": "Current Kings",
    "San Antonio Spurs": "Current Spurs",
    "Toronto Raptors": "Current Raptors",
    "Utah Jazz": "Current Jazz",
    "Washington Wizards": "Current Wizards"
}

def excel_to_json(input_xlsx: str, output_json: str):
    import pandas as pd
    import json

    # Load playtype data
    playtype_path = "playtype.csv"  # Adjust path if needed
    playtype_df = pd.read_csv(playtype_path, delimiter=",")
    playtype_df.columns = [col.strip() for col in playtype_df.columns]
    playtype_df = playtype_df.rename(columns={
        "Season": "Season",
        "PLAYER_NAME": "Player",
        "TEAM_ABBREVIATION": "Team",
        "PLAY_TYPE": "Play Type",
        "POSS_PCT": "Possession %"
    })
    playtype_df["Player"] = playtype_df["Player"].str.strip()
    playtype_df["Play Type"] = playtype_df["Play Type"].str.strip()
    playtype_df["Possession %"] = pd.to_numeric(playtype_df["Possession %"], errors="coerce")

    # Get top 4 play types per player
    top_playtypes = (
        playtype_df
        .sort_values(by=["Player", "Possession %"], ascending=[True, False])
        .groupby("Player")
        .head(4)
    )
    playtype_summary = {}
    for player, group in top_playtypes.groupby("Player"):
        sorted_types = group.sort_values(by="Possession %", ascending=False)["Play Type"].tolist()
        playtype_dict = {f"Play Type {i+1}": play for i, play in enumerate(sorted_types)}
        playtype_summary[player] = playtype_dict

    # -------------------------------------------------------------------------
    # Main conversion process
    # -------------------------------------------------------------------------
    df = pd.read_excel(input_xlsx, dtype=str)
    output_dict = {}

    for _, row in df.iterrows():
        first_name = row.get("PLAYER_FIRST_NAME", "")
        last_name = row.get("PLAYER_LAST_NAME", "")
        raw_team = row.get("Current Team", "Current ")
        mapped_team = TEAM_NAME_MAP.get(raw_team, raw_team)

        if pd.isna(first_name) or pd.isna(last_name):
            continue

        entry_name = f"{first_name} {last_name}".strip()
        if not entry_name:
            continue

        player_data = {
            "firstName": str(first_name),
            "lastName": str(last_name),
            "teamName": str(mapped_team),
            "vitals": {},
            "attributes": {},
            "badges": {}
        }

        # Assign mapped Excel fields
        for col_name in df.columns:
            if col_name in ["PLAYER_FIRST_NAME", "PLAYER_LAST_NAME"]:
                continue

            mapping = COLUMN_MAPPINGS.get(col_name)
            if mapping is None:
                continue

            val = row[col_name]
            if pd.isna(val):
                continue

            if isinstance(mapping, tuple):
                sub_obj, json_field_name = mapping
                try:
                    val = int(val)
                except ValueError:
                    val = str(val).strip()
                player_data[sub_obj][json_field_name] = val
            elif isinstance(mapping, str):
                player_data[mapping] = str(val).strip()

        # Add play types if available
        if entry_name in playtype_summary:
            player_data["vitals"].update(playtype_summary[entry_name])

        output_dict[entry_name] = player_data

    with open(output_json, "w", encoding="utf-8") as f:
        json.dump(output_dict, f, indent=4, ensure_ascii=False)



if __name__ == "__main__":
    # Example usage:
    excel_to_json(
        input_xlsx="players.xlsx", 
        output_json="options.json"
    )
