import json

# Read JSON data from file
with open('playerlist.txt', 'r') as json_file:
    json_data = json.load(json_file)

# Read names to filter from a text file
with open('names.txt', 'r') as names_file:
    names_to_filter = [line.strip() for line in names_file]

# Filter the JSON data based on the full names
filtered_data = [entry for entry in json_data if entry["full_name"] in names_to_filter]

# Save the filtered data to a new JSON file
output_file_path = 'filtered_entries.txt'
with open(output_file_path, 'w') as output_file:
    json.dump(filtered_data, output_file, indent=2)

print(f"Filtered data saved to {output_file_path}")
