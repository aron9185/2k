import json
import pandas as pd

# Load player stats from JSON
with open("nbarapm.json", "r", encoding="utf-8") as f:
    stats_data = json.load(f)

df_stats = pd.DataFrame(stats_data)

# Convert 'year' to 'Season' format (e.g. 2025 → "2024-25")
df_stats["Season"] = df_stats["year"].apply(lambda y: f"{y-1}-{str(y)[-2:]}")

# Load player list (tab-separated)
df_names = pd.read_csv("playerlist.csv", delimiter=',')  # Columns: nba_id, Season, Player

# Merge on nba_id + Season
df_merged = df_stats.merge(df_names, on=["nba_id", "Season"], how="left")

# Reorder columns to put Player and Season first
cols = ['Season', 'Player'] + [col for col in df_merged.columns if col not in ['Season', 'Player']]
df_merged = df_merged[cols]

# Save
df_merged.to_csv("nbarapm.csv", index=False)
print("✅ Merged and saved to nbarapm.csv")
