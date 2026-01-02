import re
import csv

# Define the input and output file paths
input_file = 'epm.txt'
output_file = 'epm_output.csv'

# Define a pattern to extract each player's data block
player_pattern = re.compile(
    r'(\d{4}),\s+(\d+),\s+"([^"]+)",\s+(\d+),\s+"(\w+)",\s+(\d+),\s+"(\d+)",\s+(\d+),\s+(\d+),\s+"(\w+)",'
    r'\s+([\d.-]+),\s+([\d.-]+),\s+([\d.-]+),\s+([\d.-]+),\s+([\d.-]+),\s+([\d.-]+),\s+([\d.-]+),'
    r'\s+([\d.-]+),\s+([\d.-]+),\s+([\d.-]+),\s+([\d.-]+),\s+([\d.-]+),\s+([\d.-]+),\s+([\d.-]+),'
    r'\s+([\d.-]+),\s+([\d.-]+),\s+([\d.-]+),\s+([\d.-]+),\s+([\d.-]+),\s+([\d.-]+),\s+([\d.-]+),'
    r'\s+([\d.-]+),\s+([\d.-]+),\s+([\d.-]+),\s+([\d.-]+),\s+(\d+),\s+(\d+),\s+(\d+),\s+(\d+),'
    r'\s+(\d+),\s+(\d+),\s+(\d+),\s+(\d+),\s+(\d+),\s+(\d+),\s+(\d+),\s+([\d.-]+),\s+([\d.-]+),'
    r'\s+([\d.-]+),\s+([\d.-]+),\s+([\d.-]+),\s+([\d.-]+),\s+([\d.-]+),\s+([\d.-]+),\s+([\d.-]+)'
)

# Prepare the CSV file with appropriate column headers
with open(output_file, mode='w', newline='') as csv_file:
    writer = csv.writer(csv_file)
    # Write headers
    headers = [
        "Year", "Player ID", "Player Name", "Team ID", "Team Alias", "Age", "Games Played", "Minutes Played",
        "Rookie Year", "Position", "Offensive Rating", "Defensive Rating", "Total Rating", "Usage Percentage",
        "Points per 100", "True Shooting Percentage", "Effective Field Goal Percentage", "Free Throws Attempted per 100",
        "Field Goal Percentage at Rim", "Field Goal Percentage at Mid-Range", "Two-Point Percentage",
        "Three-Point Percentage", "Free Throw Percentage", "Assists per 100", "Turnovers per 100",
        "Offensive Rebounds per 100", "Defensive Rebounds per 100", "Steals per 100", "Blocks per 100"
    ]
    writer.writerow(headers)

    # Read and parse the input file
    with open(input_file, 'r', encoding='utf-8') as file:
        content = file.read()

        # Find all player data blocks using regex
        matches = player_pattern.findall(content)
        for match in matches:
            # Write each player's data as a row in the CSV
            writer.writerow(match[:len(headers)])

print(f"Data extracted and saved to {output_file}")
