import csv
import json
import pandas as pd

# Read JSON data from file
with open('avg.txt', 'r', encoding='utf-8') as json_file:
    data = json.load(json_file)
    
# Read CSV file into DataFrame
df_csv = pd.read_csv('real.csv', encoding='utf-8')

for list in data:
    print("==========================")
    for player in list['players']:
        name = player['firstName'] + ' ' + player['lastName']
        avg_rpr = round(float(player['value']), 2)
        teamId = player['teamId']
        if name in df_csv['Player'].values:
            # Find index of player_name in DataFrame
            index = df_csv.index[df_csv['Player'] == name].tolist()[0]
            df_csv.loc[index, 'avg_real'] = avg_rpr
            df_csv.loc[index, 'teamId'] = teamId
        else: 
            print("Player Not Found-------------------------")
            print(name, avg_rpr, teamId)
            # Create a new row for the player and fill in the average
            new_row = {'Player': name, 'teamId': teamId, 'avg_real': avg_rpr, 'total_real': 0, 'morale':0}  # Assuming 'Player' is the column name for player names
            df_csv = df_csv._append(new_row, ignore_index=True)
            
# Write updated DataFrame to CSV
df_csv.to_csv('updated_data.csv', index=False, encoding='utf-8')

# Print updated DataFrame
print(df_csv)