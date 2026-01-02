import requests
import pandas as pd
import json
from tqdm import tqdm

# Constants
HEADERS = {
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

PLAYER_LIST_PATH = "playerlist.csv"
OUTPUT_PATH = "splitsshooting26.csv"

# Load player list
player_list = pd.read_csv(PLAYER_LIST_PATH)

# Function to normalize player data
def flatten_player_data(player_id, season, name, response_json):
    """Flatten JSON response into a single row."""
    player_data = {"PlayerID": player_id, "Season": season, "Name": name}
    
    # Process each resultSet
    for result_set in response_json.get("resultSets", []):
        set_name = result_set["name"]  # Prefix based on the set name
        headers = result_set["headers"]
        rows = result_set["rowSet"]
        
        for row in rows:
            if 'Shot Type' not in row[0] or 'Summary' in row[0]:
                continue
            for i, value in enumerate(row):
                if i > 1 and i <= 4:
                    column_name = f"{row[1]}_{headers[i]}"
                    # Ensure the last value overwrites others for single-row logic
                    player_data[column_name] = value
    
    return player_data

# Store all players' data
all_player_data = []

# Crawl data with progress bar
for _, player in tqdm(player_list.iterrows(), total=player_list.shape[0], desc="Processing players"):
    player_id = player["NBA ID"]
    season = player["Season"]
    name = player['Player']
    
    # Construct URL
    url = (
        f"https://stats.nba.com/stats/playerdashboardbyshootingsplits"
        f"?DateFrom=&DateTo=&GameSegment=&LastNGames=0&LeagueID=00&Location=&MeasureType=Base"
        f"&Month=0&OpponentTeamID=0&Outcome=&PORound=0&PaceAdjust=N&PerMode=PerGame&Period=0"
        f"&PlayerID={player_id}&PlusMinus=N&Rank=N&Season={season}&SeasonSegment="
        f"&SeasonType=Regular%20Season&ShotClockRange=&Split=general&VsConference=&VsDivision="
    )
    
    # Fetch player data
    response = requests.get(url, headers=HEADERS)
    
    # Explicitly set the encoding to UTF-8
    response.encoding = 'utf-8'

    if response.status_code == 200:
        response_json = response.json()
        flattened_data = flatten_player_data(player_id, season, name, response_json)
        all_player_data.append(flattened_data)
    else:
        print(f"Failed to fetch data for PlayerID {player_id}, Season {season}")

# Convert all data to a DataFrame
result_df = pd.DataFrame(all_player_data)

# Save to CSV
result_df.to_csv(OUTPUT_PATH, index=False, encoding="utf-8")
print(f"Data saved to {OUTPUT_PATH}")
