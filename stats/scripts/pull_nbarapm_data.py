from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Dict, Iterable, List, Sequence

from urllib.request import Request, urlopen

from pull_nba_stats import HISTORY_DIR, MANUAL_DIR


BASE_URL = "https://www.nbarapm.com/load"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/136.0.0.0 Safari/537.36"
)
DATASET_OUTPUTS = {
    "player_stats_export": HISTORY_DIR / "nbarapm.csv",
    "mamba": HISTORY_DIR / "mamba.csv",
    "lebron": HISTORY_DIR / "lebron.csv",
}


def canonical_id(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        return str(int(float(text)))
    except Exception:
        return text


def season_from_end_year(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        year = int(float(text))
    except Exception:
        return text
    return f"{year - 1}-{year % 100:02d}"


def read_player_index(path: Path) -> Dict[tuple[str, str], str]:
    with path.open("r", newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        headers = list(reader.fieldnames or [])
        id_column = "NBA_ID" if "NBA_ID" in headers else "nba_id"

        index: Dict[tuple[str, str], str] = {}
        for row in reader:
            season = str(row.get("Season", "")).strip()
            nba_id = canonical_id(row.get(id_column, ""))
            player = str(row.get("Player", "")).strip()
            if season and nba_id and player:
                index[(nba_id, season)] = player
        return index


def fetch_dataset(dataset: str) -> List[Dict[str, object]]:
    request = Request(
        f"{BASE_URL}/{dataset}",
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    with urlopen(request, timeout=60) as response:
        payload = response.read().decode("utf-8")
    data = json.loads(payload)
    if not isinstance(data, list):
        raise SystemExit(f"Unexpected payload type for {dataset}: {type(data).__name__}")
    return data


def ordered_headers(rows: Sequence[Dict[str, object]], preferred: Sequence[str]) -> List[str]:
    seen = set()
    headers: List[str] = []

    for key in preferred:
        if key not in seen:
            seen.add(key)
            headers.append(key)

    for row in rows:
        for key in row.keys():
            if key in seen:
                continue
            seen.add(key)
            headers.append(key)
    return headers


def normalize_player_name(
    player_index: Dict[tuple[str, str], str],
    nba_id: str,
    season: str,
    fallback: object,
) -> str:
    direct = player_index.get((nba_id, season), "").strip()
    if direct:
        return direct
    return str(fallback or "").strip()


def transform_player_stats_export(
    rows: Sequence[Dict[str, object]],
    player_index: Dict[tuple[str, str], str],
) -> List[Dict[str, object]]:
    transformed: List[Dict[str, object]] = []
    for row in rows:
        season = season_from_end_year(row.get("year", ""))
        nba_id = canonical_id(row.get("NBA_ID", row.get("nba_id", "")))
        player = normalize_player_name(player_index, nba_id, season, row.get("ShortName", ""))

        merged = dict(row)
        merged.pop("nba_id", None)
        merged["NBA_ID"] = nba_id
        merged["Season"] = season
        merged["Player"] = player
        transformed.append(merged)
    return transformed


def transform_mamba(
    rows: Sequence[Dict[str, object]],
    player_index: Dict[tuple[str, str], str],
) -> List[Dict[str, object]]:
    transformed: List[Dict[str, object]] = []
    for row in rows:
        season = str(row.get("Season", "")).strip() or season_from_end_year(row.get("year", ""))
        nba_id = canonical_id(row.get("NBA_ID", row.get("nba_id", "")))
        player = normalize_player_name(player_index, nba_id, season, row.get("player_name", ""))

        merged = dict(row)
        merged.pop("nba_id", None)
        merged["NBA_ID"] = nba_id
        merged["Season"] = season
        merged["Player"] = player
        transformed.append(merged)
    return transformed


def transform_lebron(
    rows: Sequence[Dict[str, object]],
    player_index: Dict[tuple[str, str], str],
) -> List[Dict[str, object]]:
    transformed: List[Dict[str, object]] = []
    for row in rows:
        season = str(row.get("Season", "")).strip() or season_from_end_year(row.get("year", ""))
        nba_id = canonical_id(row.get("NBA_ID", row.get("nba_id", "")))
        player = normalize_player_name(player_index, nba_id, season, row.get("player_name", ""))

        merged = dict(row)
        merged.pop("nba_id", None)
        merged["NBA_ID"] = nba_id
        merged["Season"] = season
        merged["Player"] = player
        transformed.append(merged)
    return transformed


TRANSFORMS = {
    "player_stats_export": transform_player_stats_export,
    "mamba": transform_mamba,
    "lebron": transform_lebron,
}


def write_rows(path: Path, rows: Sequence[Dict[str, object]], preferred_headers: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    headers = ordered_headers(rows, preferred=preferred_headers)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=headers, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({header: row.get(header, "") for header in headers})


def pull_datasets(
    datasets: Sequence[str],
    playerlist_path: Path,
) -> None:
    player_index = read_player_index(playerlist_path)

    for dataset in datasets:
        rows = fetch_dataset(dataset)
        transformed = TRANSFORMS[dataset](rows, player_index)
        output_path = DATASET_OUTPUTS[dataset]
        write_rows(output_path, transformed, preferred_headers=("Season", "Player", "NBA_ID"))
        print(f"[OK] {dataset} -> {output_path} ({len(transformed)} rows)")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Pull public nbarapm.com JSON datasets into stats/history."
    )
    parser.add_argument(
        "--datasets",
        nargs="+",
        default=["player_stats_export", "mamba", "lebron"],
        choices=sorted(DATASET_OUTPUTS.keys()),
        help="One or more nbarapm.com datasets to pull.",
    )
    parser.add_argument(
        "--playerlist",
        default=str(MANUAL_DIR / "playerlist.csv"),
        help="Player list used to recover canonical player names by NBA ID and season.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    pull_datasets(args.datasets, Path(args.playerlist))


if __name__ == "__main__":
    main()
