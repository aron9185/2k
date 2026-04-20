import json
import os
import time

from realsports_api import RealSportsError, build_realsports_client


BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def fetch_rankings(base_url, output_folder, season="2025", start_before=50, step=50, delay=1, client=None):
    """Fetch and save Real Sports ranking pages until the API returns no rows."""
    client = client or build_realsports_client()
    before_value = start_before

    while True:
        params = {"before": before_value, "season": str(season)}
        print(f"Requesting data with before={before_value}...")

        try:
            data = client.get_json(base_url, params=params)
        except RealSportsError as exc:
            print(f"{exc}. Stopping.")
            break

        if data.get("items") == [] and data.get("positionOptions") is None and data.get("info") is None:
            print("No more data found. Stopping.")
            break

        num_records = len(data.get("items", []))
        print(f"Received {num_records} records.")

        filename = os.path.join(output_folder, f"tertiary_{before_value}.json")
        os.makedirs(os.path.dirname(filename), exist_ok=True)
        with open(filename, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=4)
        print(f"Saved data to {filename}")

        before_value += step
        time.sleep(delay)


if __name__ == "__main__":
    fetch_rankings(
        base_url="https://web.realsports.io/rankings/sport/ncaam/entity/player/ranking/tertiary",
        output_folder=os.path.join(BASE_DIR, "cbb"),
        season="2025",
    )
