import pandas as pd

# Step 1: Open and read the input text file
input_path = "dunks.txt"  # Replace with your actual file path
output_path = "dunks.csv"  # Path to save the resulting CSV

# Read the txt file into a pandas DataFrame
df = pd.read_csv(input_path)

# Step 2: Filter only the 'playerName' and 'dunkScore' columns
filtered_df = df[["playerName", "dunkScore"]]

# Step 3: Save the filtered data to a CSV file with a comma separator
filtered_df.to_csv(output_path, sep=",", index=False)

print(f"CSV file created at: {output_path}")
