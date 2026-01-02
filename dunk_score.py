import requests
import pandas as pd
import time
import os

# URL to fetch the dunk score data
URL = "https://cdn.nba.com/static/json/staticData/leaderboards/00_nightlydunkscore.json"
CSV_FILE = "dunk_scores.csv"

# Function to load existing records from CSV
def load_existing_records():
    if os.path.exists(CSV_FILE):
        return pd.read_csv(CSV_FILE)
    else:
        # Create an empty DataFrame with necessary columns if the file doesn't exist
        columns = [
            "gameId", "playerName", "dunkScore", 
            "dunkTimeUTC"
        ]
        return pd.DataFrame(columns=columns)

# Function to save new records to CSV
def save_new_records(df):
    if not os.path.exists(CSV_FILE):
        df.to_csv(CSV_FILE, index=False)
    else:
        df.to_csv(CSV_FILE, mode='a', header=False, index=False)

# Function to fetch and process data
def fetch_and_record_data():
    existing_records = load_existing_records()

    try:
        # Fetch data from URL
        response = requests.get(URL)
        data = response.json()
        dunk_scores = data["dunkScores"]

        new_records = []

        for dunk in dunk_scores:
            game_id = int(dunk["gameId"].lstrip("0"))
            dunk_time = dunk["dunkTimeUTC"]

            # Check if the dunk is already recorded
            is_existing_record = False
            matching_indices = existing_records.index[existing_records["dunkTimeUTC"] == dunk_time].tolist()
            for indices in matching_indices:
                if existing_records["gameId"][indices] == game_id:
                    
                    is_existing_record = True

            if not is_existing_record:
                # Prepare new record data
                new_record = {
                    "gameId": game_id,
                    "playerName": dunk["playerName"],
                    "dunkScore": dunk["dunkScore"],
                    "dunkTimeUTC": dunk_time
                }
                new_records.append(new_record)

        # If there are new records, append them to CSV
        if new_records:
            new_df = pd.DataFrame(new_records)
            save_new_records(new_df)
            print(f"{len(new_records)} new dunks recorded.")
        else:
            print("No new dunks found.")

    except Exception as e:
        print(f"Error fetching or processing data: {e}")

# Run the function in a loop with a specified delay
while True:
    fetch_and_record_data()
    time.sleep(300)  # Wait 1 minute before the next check
