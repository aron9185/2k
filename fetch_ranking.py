import os
import json
import requests
import time

def fetch_rankings(base_url, output_folder, season="2025", start_before=50, step=50, delay=1, headers=None):
    """
    Fetch rankings data starting at a given 'before' value and incrementing by step until no data is returned.
    
    Parameters:
      base_url (str): The API endpoint to request data from.
      output_folder (str): Folder path to save JSON files.
      season (str): Season to query (default "2025").
      start_before (int): Starting value for 'before' parameter (default 50).
      step (int): Increment for 'before' parameter on each iteration (default 50).
      delay (int): Seconds to pause between requests (default 1).
      headers (dict): Optional custom headers. If None, default headers are used.
    """
    # Default headers if none provided
    if headers is None:
        headers = {
            "accept": "application/json",
            "accept-encoding": "gzip, deflate, br, zstd",
            "accept-language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
            "cache-control": "no-cache",
            "content-type": "application/json",
            "origin": "https://www.realsports.io",
            "pragma": "no-cache",
            "real-auth-info": "jvbzP76v!e3OdkxzE!9f1cbe8e-860e-4d44-acb6-dd0b591d2dee",
            "real-device-name": "5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
            "real-device-type": "desktop_web",
            "real-device-uuid": "4311295d-c470-419d-a2b3-bfe30c3565ca",
            "real-request-token": "token",
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

    before_value = start_before
    while True:
        params = {
            "before": before_value,
            "season": season
        }
        print(f"Requesting data with before={before_value}...")
        response = requests.get(base_url, headers=headers, params=params)

        if response.status_code != 200:
            print(f"Request failed with status code {response.status_code}. Stopping.")
            break

        try:
            data = response.json()
        except ValueError:
            print("Response content is not valid JSON. Stopping.")
            break

        # Check if data is empty (structure: { "items": [], "positionOptions": null, "info": null })
        if data.get("items") == [] and data.get("positionOptions") is None and data.get("info") is None:
            print("No more data found. Stopping.")
            break

        num_records = len(data.get("items", []))
        print(f"Received {num_records} records.")

        # Define the filename and ensure the output directory exists.
        filename = os.path.join(output_folder, f"tertiary_{before_value}.json")
        os.makedirs(os.path.dirname(filename), exist_ok=True)

        with open(filename, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
        print(f"Saved data to {filename}")

        before_value += step
        time.sleep(delay)

if __name__ == "__main__":
    # Example usage for college basketball (ncaam)
    cbb_url = "https://web.realsports.io/rankings/sport/ncaam/entity/player/ranking/tertiary"
    fetch_rankings(base_url=cbb_url, output_folder="./real/cbb", season="2025")
    
    # To use for another sport, e.g., NBA, you can call:
    # nba_url = "https://web.realsports.io/rankings/sport/nba/entity/player/ranking/tertiary"
    # fetch_rankings(base_url=nba_url, output_folder="./real/nba", season="2025")
