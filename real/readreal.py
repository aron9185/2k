import csv
import json
import pandas as pd
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
game_val = float(input())

# Read JSON data from file
with open(BASE_DIR / 'stat.txt', 'r', encoding='utf-8') as json_file:
    data = json.load(json_file)
    
# Read CSV file into DataFrame
df_csv = pd.read_csv(BASE_DIR / 'real.csv', encoding='utf-8')

score = {}
for player_stat in data['playerBoxScores']:
    team = player_stat['team']['key']
    pts = player_stat['topStats'][0]['value']
    if (team in score):
        score[team] += pts
    else:
        score[team] = pts
score_list = list(score.keys())
print(score[score_list[0]], score_list[0], " - ", score_list[1], score[score_list[1]])

if score[score_list[0]] > score[score_list[1]]:
    team_won = score_list[0]
    team_lost = score_list[1]
else:
    team_won = score_list[1]
    team_lost = score_list[0]

print(f'{team_won} won, game_val = {game_val}')
print('---------------------------------------')

for player_stat in data['playerBoxScores']:
    rpr = round(float(player_stat['value']), 2)
    name = player_stat['player']['firstName'] + ' ' + player_stat['player']['lastName']
    team = player_stat['team']['key']
    
    if team not in [team_won, team_lost]:
        raise(AssertionError)
    won = (team == team_won)
    injured = player_stat['injuryStatus'] == 'Out'
    
    if injured:
        print(name, 'Out')
        continue 
    if name in df_csv['Player'].values:
        # Find index of player_name in DataFrame
        index = df_csv.index[df_csv['Player'] == name].tolist()[0]
        # Add column value to the respective player's column in DataFrame
        df_csv.loc[index, 'total_real'] += rpr - float(df_csv.loc[index, 'avg_real'])
        df_csv.loc[index, 'Team(s)'] = team
        if won:
            df_csv.loc[index, 'morale'] += game_val
        else:
            df_csv.loc[index, 'morale'] -= game_val
        print(name, rpr)
    else: 
        print("Player Not Found")
        print(name, rpr)
    
# Write updated DataFrame to CSV
df_csv.to_csv(BASE_DIR / 'real.csv', index=False, encoding='utf-8')
    
