import json
import csv
import glob

"""# Define a function to read JSON files and write data to CSV
def save_players_to_csv(json_folder_path, output_csv):
    player_data = []

    # Read each JSON file in the specified folder
    for json_file in glob.glob(f"{json_folder_path}/*.json"):
        print(json_file)
        with open(json_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
            # Extract player info
            for item in data.get('items', []):
                player_id = item.get("id")
                first_name = item.get("firstName")
                last_name = item.get("lastName")
                player_data.append([player_id, first_name + ' ' + last_name])

    # Write the extracted data to a CSV file
    with open(output_csv, 'w', newline='', encoding='utf-8') as csvfile:
        csvwriter = csv.writer(csvfile)
        csvwriter.writerow(["ID", "Name"])
        csvwriter.writerows(player_data)

# Usage
json_folder_path = "./real"  # Folder containing JSON files
output_csv = "realid.csv"                # Output CSV file path
save_players_to_csv(json_folder_path, output_csv)"""

import os
import csv
import json
import time
import requests

token = "od9XLRKaYpDplbZ6"

# Define headers for the request (remove pseudo headers)
headers = {
    "accept": "application/json",
    "accept-encoding": "gzip, deflate, br, zstd",
    "accept-language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
    "cache-control": "no-cache",
    "content-type": "application/json",
    "origin": "https://www.realsports.io",
    "pragma": "no-cache",
    "real-auth-info": "jvbzP76v!e3OdkxzE!5022ac70-75d7-44ef-b24b-dc47af7bc8d2",
    "real-device-name": "5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "real-device-type": "desktop_web",
    "real-device-uuid": "4311295d-c470-419d-a2b3-bfe30c3565ca",
    "real-request-token": token,
    "real-version": "20",
    "referer": "https://www.realsports.io/",
    "sec-ch-ua": "\"Chromium\";v=\"130\", \"Google Chrome\";v=\"130\", \"Not?A_Brand\";v=\"99\"",
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": "\"Windows\"",
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-site",
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
}


sport_urls = {
    "nhl": "https://web.realsports.io/rankings/sport/nhl/entity/player/ranking/tertiary",
    "mlb": "https://web.realsports.io/rankings/sport/mlb/entity/player/ranking/tertiary",
    "ncaam": "https://web.realsports.io/rankings/sport/ncaam/entity/player/ranking/tertiary",
    "nba": "https://web.realsports.io/rankings/sport/nba/entity/player/ranking/tertiary",
}

def fetch_rankings_to_csv(sport, output_csv, season="2026", start_before=0, step=50, delay=1):
    base_url = sport_urls.get(sport)
    rows = []
    before_value = start_before

    while True:
        params = {"season": season}
        if before_value != 0:
            params["before"] = before_value

        response = requests.get(base_url, headers=headers, params=params)
        if response.status_code != 200:
            print(f"Request failed with status code {response.status_code}")
            break

        data = response.json()
        if not data.get("items"):
            break

        for item in data["items"]:
            rows.append({
                "id": item.get("id"),
                "Name": item.get("firstName", "").strip() + " " + item.get("lastName", "").strip(),
            })
            print(item.get("firstName", "").strip() + " " + item.get("lastName", "").strip())

        before_value += step
        time.sleep(delay)

    with open(output_csv, 'w', newline='', encoding='utf8') as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=["id", "Name"])
        writer.writeheader()
        writer.writerows(rows)

fetch_rankings_to_csv('nba', f"real_id.csv", season=2026)