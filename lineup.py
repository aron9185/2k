import os
import csv
import json
import time
import requests

# Example configuration
sport = "nfl"  # Change sport here: 'mlb', 'nhl', 'nba', 'ncaam', 'soccer', 'wnba', 'ncaaf', 'nfl'
date = "2025-12-22"
season = "2025"
ranking_csv_file = f"ranking_data_{sport}.csv"
fantasy_points_file = "fantasy_points.json"
token = "3yD2Pr6vqgWYMQK8"

headers = {
    "accept": "application/json",
    "accept-encoding": "gzip, deflate, br, zstd",
    "accept-language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
    "content-type": "application/json",
    "origin": "https://www.realsports.io",
    "real-auth-info": "jvbzP76v!e3OdkxzE!5022ac70-75d7-44ef-b24b-dc47af7bc8d2",
    "real-device-name": "5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36",
    "real-device-type": "desktop_web",
    "real-device-uuid": "4311295d-c470-419d-a2b3-bfe30c3565ca",
    "real-request-token": token,
    "real-version": "23",
    "referer": "https://www.realsports.io/",
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36",
}

sport_urls = {
    "nhl": "https://web.realsports.io/rankings/sport/nhl/entity/player/ranking/tertiary",
    "mlb": "https://web.realsports.io/rankings/sport/mlb/entity/player/ranking/tertiary",
    "ncaam": "https://web.realsports.io/rankings/sport/ncaam/entity/player/ranking/tertiary",
    "nba": "https://web.realsports.io/rankings/sport/nba/entity/player/ranking/tertiary",
    "soccer": "https://web.realsports.io/rankings/sport/soccer/entity/player/ranking/tertiary",
    "wnba": "https://web.realsports.io/rankings/sport/wnba/entity/player/ranking/tertiary",
    "ncaaf": "https://web.realsports.io/rankings/sport/ncaaf/entity/player/ranking/tertiary",
    "nfl": "https://web.realsports.io/rankings/sport/nfl/entity/player/ranking/tertiary",
}

def fetch_rankings_to_csv(sport, output_csv, season="2025", start_before=0, step=50, delay=1):
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
                "firstName": item.get("firstName", "").strip(),
                "lastName": item.get("lastName", "").strip(),
                "rating": item.get("rating", "0")
            })

        before_value += step
        time.sleep(delay)

    with open(output_csv, 'w', newline='', encoding='utf8') as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=["id", "firstName", "lastName", "rating"])
        writer.writeheader()
        writer.writerows(rows)

def load_ranking_csv(csv_file):
    ranking_dict = {}
    with open(csv_file, 'r', encoding='utf8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            full_name = f"{row['firstName'].strip()} {row['lastName'].strip()}"
            ranking_dict[full_name.lower()] = float(row.get("rating", 0))
    return ranking_dict

def load_multiple_json(filename):
    records = []
    with open(filename, 'r', encoding='utf8') as f:
        content = f.read()
    decoder = json.JSONDecoder()
    pos = 0
    while pos < len(content):
        obj, pos = decoder.raw_decode(content, pos)
        records.extend(obj if isinstance(obj, list) else [obj])
        while pos < len(content) and content[pos].isspace():
            pos += 1
    return records

def process_fantasy_points(sport, date, fantasy_points_file, ranking_csv_file):
    ranking_data = load_ranking_csv(ranking_csv_file)
    fantasy_records = load_multiple_json(fantasy_points_file)
    results = []

    for record in fantasy_records:
        full_name = f"{record.get('firstName', '').strip()} {record.get('lastName', '').strip()}"
        fantasy_pts = float(record.get("pts", "0"))

        search_url = f"https://web.realsports.io/players/sport/{sport}/search?day={date}&includeNoOneOption=false&query={full_name}&searchType=ratingLineup"
        multiplier = 0.0

        try:
            response = requests.get(search_url, headers=headers).json()
            for player in response.get("players", []):
                if player.get("firstName", "").strip().lower() + " " + player.get("lastName", "").strip().lower() == full_name.lower():
                    multiplier = float(player.get("multiplierBonus", 0))
                    break
        except Exception as e:
            print(f"Failed for {full_name}: {e}")

        real_rating = ranking_data.get(full_name.lower(), 0.0)
        adjusted_fp = fantasy_pts * (1 + multiplier)
        adjusted_rating = real_rating * (1 + multiplier)
        results.append((full_name, adjusted_rating, adjusted_fp, 1 + multiplier, real_rating))

    results.sort(key=lambda x: x[1], reverse=True)
    return results

fetch_rankings_to_csv(sport, ranking_csv_file, season=season)
results = process_fantasy_points(sport, date, fantasy_points_file, ranking_csv_file)

with open('lineup.txt', 'w', encoding='utf8') as txtfile:
    for name, adj_rtg, adj_fp, mult, rating in results:
        line = f"{name}: Adjusted RTG = {adj_rtg:.2f}, Adjusted FP = {adj_fp:.2f}, Multiplier = {mult}, Rating = {rating}\n"
        print(line.strip())
        txtfile.write(line)