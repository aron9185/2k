from bs4 import BeautifulSoup
import requests
import json
import csv
import pandas as pd
import numpy as np
import os

# Create an empty DataFrame to store the concatenated data
concatenated_df = pd.DataFrame()

# Define the URL and headers
url_headers = {
    "Accept-Encoding": "gzip, deflate, br",
    "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "Host": "stats.nba.com",
    "Origin": "https://www.nba.com",
    "Pragma": "no-cache",
    "Referer": "https://www.nba.com/",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-site",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116.0.0.0 Safari/537.36",
    "sec-ch-ua": "\"Chromium\";v=\"116\", \"Not)A;Brand\";v=\"24\", \"Google Chrome\";v=\"116\"",
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": "Windows"
}

output_path = 'shot_detail2.csv'
playerlist = []
head_written = False

with open('playerlist.csv', 'r') as file:
    reader = csv.DictReader(file, delimiter=',')
    for row in reader:
        playerlist.append(row)

# Run a for loop with the data
for player in playerlist:
    print(player)
    print("NBA ID:", player['NBA ID'])
    print("Season:", player['Season'])
    print("Player:", player['Player'])
    
    response = None
    year = player['Season']
    full_name = player['Player']
    player_id = player['NBA ID']
    print(f'Full Name: {full_name}, ID: {player_id}')

    url = f"https://stats.nba.com/stats/playerdashboardbyshootingsplits?DateFrom=&DateTo=&GameSegment=&LastNGames=0&LeagueID=00&Location=&MeasureType=Base&Month=0&OpponentTeamID=0&Outcome=&PORound=0&PaceAdjust=N&PerMode=PerGame&Period=0&PlayerID={player_id}&PlusMinus=N&Rank=N&Season={year}&SeasonSegment=&SeasonType=Regular%20Season&ShotClockRange=&Split=general&VsConference=&VsDivision="

    # Send the GET request with headers
    response = requests.get(url, headers=url_headers)

    # Check if the request was successful (status code 200)
    if response.status_code == 200:
        # Parse the JSON content
        data = json.loads(response.text)
        
        # Access the 'resultSets' from the JSON data
        result_sets = data.get('resultSets')

        if result_sets:
            aggregated_data_list = []
            
            for resultSet in result_sets:
                headers = resultSet['headers']
                rows = resultSet['rowSet']
                
                for row in rows:
                    combined_row = {headers[0]: row[0], headers[1]: row[1]}
                    combined_row.update({header: value for header, value in zip(headers[2:], row[2:])})
                    aggregated_data_list.append(combined_row)
            
            # Create DataFrame from aggregated data
            temp_df = pd.DataFrame(aggregated_data_list)
            
            # Add player info to the DataFrame
            temp_df['season'] = year
            temp_df['name'] = full_name

            # Concatenate the new DataFrame to the existing one
            concatenated_df = pd.concat([concatenated_df, temp_df], ignore_index=True)
            
            # Save the DataFrame to a CSV file
            concatenated_df.to_csv(output_path, index=False)

    else:
        print(f"Failed to retrieve data for player {full_name}")

# Save the DataFrame to a CSV file
concatenated_df.to_csv(output_path, index=False)

# Print the complete DataFrame
with pd.option_context('display.max_rows', None, 'display.max_columns', None):
    print(concatenated_df)
