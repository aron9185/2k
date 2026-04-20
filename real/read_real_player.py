import argparse
import csv
import time
import unicodedata
from pathlib import Path

from realsports_api import RealSportsError, build_realsports_client


BASE_DIR = Path(__file__).resolve().parent
SPORT_URLS = {
    "nhl": [
        "https://web.realapp.com/rankings/sport/nhl/entity/player/ranking/tertiary",
        "https://web.realsports.io/rankings/sport/nhl/entity/player/ranking/tertiary",
    ],
    "mlb": [
        "https://web.realapp.com/rankings/sport/mlb/entity/player/ranking/tertiary",
        "https://web.realsports.io/rankings/sport/mlb/entity/player/ranking/tertiary",
    ],
    "ncaam": [
        "https://web.realapp.com/rankings/sport/ncaam/entity/player/ranking/tertiary",
        "https://web.realsports.io/rankings/sport/ncaam/entity/player/ranking/tertiary",
    ],
    "nba": [
        "https://web.realapp.com/rankings/sport/nba/entity/player/ranking/tertiary",
        "https://web.realsports.io/rankings/sport/nba/entity/player/ranking/tertiary",
    ],
    "soccer": [
        "https://web.realapp.com/rankings/sport/soccer/entity/player/ranking/tertiary",
        "https://web.realsports.io/rankings/sport/soccer/entity/player/ranking/tertiary",
    ],
    "wnba": [
        "https://web.realapp.com/rankings/sport/wnba/entity/player/ranking/tertiary",
        "https://web.realsports.io/rankings/sport/wnba/entity/player/ranking/tertiary",
    ],
    "ncaaf": [
        "https://web.realapp.com/rankings/sport/ncaaf/entity/player/ranking/tertiary",
        "https://web.realsports.io/rankings/sport/ncaaf/entity/player/ranking/tertiary",
    ],
    "nfl": [
        "https://web.realapp.com/rankings/sport/nfl/entity/player/ranking/tertiary",
        "https://web.realsports.io/rankings/sport/nfl/entity/player/ranking/tertiary",
    ],
    "golf": [
        "https://web.realapp.com/rankings/sport/golf/entity/player/ranking/tertiary",
        "https://web.realsports.io/rankings/sport/golf/entity/player/ranking/tertiary",
    ],
}


def normalize_name(value):
    text = unicodedata.normalize("NFKD", value or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    cleaned = []
    for char in text.lower():
        if char.isalnum() or char.isspace():
            cleaned.append(char)
        else:
            cleaned.append(" ")
    return " ".join("".join(cleaned).split())


def build_real_id_rows_from_ranking_items(items):
    rows = []
    seen_ids = set()
    for row in items:
        player_id = str(row.get("id", "")).strip()
        first_name = (row.get("firstName") or "").strip()
        last_name = (row.get("lastName") or "").strip()
        if not player_id or not first_name and not last_name or player_id in seen_ids:
            continue
        seen_ids.add(player_id)
        name = f"{first_name} {last_name}".strip()
        rows.append(
            {
                "id": player_id,
                "Name": name,
                "firstName": first_name,
                "lastName": last_name,
                "normalized_name": normalize_name(name),
            }
        )
    return rows


def write_real_id_csv(output_csv, rows):
    output_path = Path(output_csv)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf8") as csvfile:
        writer = csv.DictWriter(
            csvfile,
            fieldnames=["id", "Name", "firstName", "lastName", "normalized_name"],
        )
        writer.writeheader()
        writer.writerows(rows)


def ranking_urls_for_sport(sport):
    urls = SPORT_URLS.get(sport)
    if not urls:
        raise ValueError(f"Unsupported sport: {sport}")
    if isinstance(urls, str):
        return [urls]
    return list(urls)


def fetch_ranking_items(client, ranking_url, season="2025", start_before=0, step=50, delay=1):
    rows = []
    before_value = start_before
    seen_ids = set()

    while True:
        params = {"season": str(season)}
        if before_value != 0:
            params["before"] = before_value

        try:
            data = client.get_json(ranking_url, params=params)
        except RealSportsError:
            if rows:
                break
            raise

        items = data.get("items") or []
        if not items:
            break

        page_added = 0
        for item in items:
            player_id = str(item.get("id", "")).strip()
            if not player_id or player_id in seen_ids:
                continue
            seen_ids.add(player_id)
            rows.append(item)
            page_added += 1

        if page_added <= 0:
            break

        before_value += step
        time.sleep(delay)

    return rows


def refresh_real_id_csv(
    sport,
    output_csv,
    season="2025",
    start_before=0,
    step=50,
    delay=1,
    client=None,
):
    client = client or build_realsports_client()
    raw_items = []
    last_error = None
    for ranking_url in ranking_urls_for_sport(sport):
        try:
            raw_items = fetch_ranking_items(
                client,
                ranking_url,
                season=season,
                start_before=start_before,
                step=step,
                delay=delay,
            )
        except RealSportsError as exc:
            last_error = exc
            continue
        if raw_items:
            break

    if not raw_items and last_error is not None:
        raise last_error

    rows = build_real_id_rows_from_ranking_items(raw_items)

    if not rows:
        raise RealSportsError(
            f"Real Sports id refresh returned 0 rows for sport={sport}, season={season}."
        )

    write_real_id_csv(output_csv, rows)
    return len(rows)


def parse_args():
    parser = argparse.ArgumentParser(description="Refresh real_id.csv from the Real Sports ranking feed.")
    parser.add_argument("--sport", default="nba", choices=sorted(SPORT_URLS))
    parser.add_argument("--output", default=str(BASE_DIR / "real_id.csv"))
    parser.add_argument("--season", default="2025")
    parser.add_argument("--delay", type=float, default=1.0)
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    refresh_real_id_csv(
        args.sport,
        args.output,
        season=args.season,
        delay=args.delay,
    )
