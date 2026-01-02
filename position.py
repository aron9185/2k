import pandas as pd
import numpy as np
from unidecode import unidecode  # <-- Make sure to install this with: pip install unidecode

# Load the data
file_path = "./player_positions.csv"
df = pd.read_csv(file_path, sep=',')

# Clean up column names
df.columns = df.columns.str.strip()

# Normalize special characters in player names
df['Player'] = df['Player'].apply(unidecode)

# Define position columns
position_columns = ['PG%', 'SG%', 'SF%', 'PF%', 'C%']

# Convert columns to numeric explicitly
for col in position_columns:
    df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)

# Function to determine primary and second position
def get_positions(row):
    primary_pos = row['Pos']

    # Copy and clean percentage columns
    percents = row[position_columns].copy()

    # Remove primary position
    primary_col = primary_pos + '%'
    if primary_col in percents:
        percents[primary_col] = -1

    # Ensure percents are numeric
    percents = percents.astype(float)

    # Get second highest
    second_pct = percents.max()
    second_col = percents.idxmax()

    # Clean up
    second_pos = second_col.replace('%', '') if second_pct > 0 else ""

    return pd.Series({
        'Primary_Position': primary_pos,
        'Second_Position': second_pos
    })

# Apply function
position_info = df.apply(get_positions, axis=1)

# Combine results
result_df = pd.concat([df[['Player']], position_info], axis=1)

# Output results
print(result_df.head())
result_df.to_csv("position_summary.csv", index=False, encoding='utf-8')
