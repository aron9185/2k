import json
import pandas as pd

# Step 1: Load JSON file
input_path = "dunk_leaderboard.json"  # Path to the input JSON file
output_path = "dunks_leaderboard.csv"       # Path to save the output CSV

# Load the JSON data
with open(input_path, "r", encoding='utf8') as file:
    data = json.load(file)

# Step 2: Extract dunk entries
dunks = data.get("dunks", [])

# Step 3: Normalize the data into a pandas DataFrame
df = pd.json_normalize(dunks)

# Step 4: Save the DataFrame to a CSV file
df.to_csv(output_path, index=False)

print(f"Dunk data saved to {output_path}")

##########################################

# File paths
input_path = "dunks_leaderboard.csv"  # Replace with the actual path to your input file
output_overall = "overall_dunk_stats_26.csv"  # Output file path
output_standing = "standing_dunk_stats_26.csv"  # Path to save the standing dunk stats CSV

# Step 1: Load the CSV with correct delimiter
df = pd.read_csv(input_path, delimiter=",")

# Debugging: Ensure column names are correct and stripped
df.columns = df.columns.str.strip()
print("Columns in the DataFrame:", df.columns)

# Step 2: Specify columns to skip
skip_columns = [
    "gameId", "gameDate", "matchup", "period", "gameClockTime", "eventNum",
    "playerId", "teamId", "passerId", "passerName", "shooterId",
    "shotReleasePoint", "shotLength", "possibleAttemptedCharge", "videoAvailable"
]

# Step 3: Identify numerical columns excluding skipped ones
numerical_cols = df.select_dtypes(include="number").columns
numerical_cols = [col for col in numerical_cols if col not in skip_columns]

# Step 4: Generate overall player stats
overall_stats = df.groupby("playerName").agg(
    **{f"{col}_average": (col, "mean") for col in numerical_cols},
    **{f"{col}_max": (col, "max") for col in numerical_cols},
    **{f"{col}_total": (col, "sum") for col in numerical_cols},
).reset_index()

# Step 5: Generate standing dunk stats (filtered by takeoffDistance < 4.0)
standing_dunks = df[df["takeoffDistance"] < 4.0]
standing_stats = standing_dunks.groupby("playerName").agg(
    **{f"{col}_average": (col, "mean") for col in numerical_cols},
    **{f"{col}_max": (col, "max") for col in numerical_cols},
    **{f"{col}_total": (col, "sum") for col in numerical_cols},
).reset_index()

# Step 6: Save the overall and standing dunk stats to separate CSVs
overall_stats.to_csv(output_overall, index=False)
standing_stats.to_csv(output_standing, index=False)

print(f"Overall player stats saved to {output_overall}")
print(f"Standing dunk stats saved to {output_standing}")
