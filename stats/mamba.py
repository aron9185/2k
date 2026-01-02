import json
import pandas as pd

# Paste your full JSON response here (or load it from the API as before)
with open("mamba.json", "r") as f:
    data = json.load(f)

# Convert to DataFrame
df = pd.DataFrame(data)

# Save to CSV
df.to_csv("mamba.csv", index=False)
print("✅ Saved to mamba_data.csv")
