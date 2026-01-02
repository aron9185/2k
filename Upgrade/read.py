import json
import pandas as pd

# Load your JSON file (replace 'data.json' with the actual path)
with open('players.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

# Flatten the data
records = []
for player_id, player_info in data.items():
    flat = {
        "player_id": player_id,
        "teamName": player_info.get("teamName"),
        "firstName": player_info.get("firstName"),
        "lastName": player_info.get("lastName"),
    }

    # Flatten nested dicts (vitals, attributes, badges, tendencies)
    for section in ["vitals", "attributes", "badges", "tendencies"]:
        nested = player_info.get(section, {})
        for key, value in nested.items():
            flat[f"{section}_{key}"] = value

    records.append(flat)

# Create DataFrame and save as CSV
df = pd.DataFrame(records)
df.to_csv("players.csv", index=False)
print("Saved to players.csv")
