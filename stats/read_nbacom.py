import csv
import json

# Read JSON data from file
with open('nbacom.txt', 'r', encoding="utf-8") as json_file:
    data = json.load(json_file)
    
# Extract headers and rowSet
try:
    headers = data["resultSets"][0]["headers"]
except KeyError:
    headers = data["resultSets"]["headers"][1]['columnNames'] # for shot area

try:
    Season = data['parameters']['Season']
except KeyError:
    Season = data['parameters']['SeasonYear'] # Synergy
print(headers)
print(Season)

# Insert 'Season' as the first element in headers
headers.insert(0, 'Season')

# Iterate through each row in rowSet and add the 'Season' data
try:
    row_data = data["resultSets"][0]["rowSet"]
except KeyError:
    row_data = data["resultSets"]["rowSet"] # for shot area
    
for row in row_data:
    row.insert(0, Season)

row_set = row_data

# Write data to CSV
csv_file_path = './playtype_transition_tmp.csv'
with open(csv_file_path, 'w', newline='', encoding="utf-8") as csvfile:
    csv_writer = csv.writer(csvfile)
    
    # Write header
    csv_writer.writerow(headers)
    
    # Write rows
    csv_writer.writerows(row_set)

print(f'Data written to {csv_file_path}')
