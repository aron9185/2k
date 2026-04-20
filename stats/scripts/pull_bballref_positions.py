from __future__ import annotations

import argparse
import time
import unicodedata
from io import StringIO
from pathlib import Path
from typing import Iterable

import pandas as pd

STATS_DIR = Path(__file__).resolve().parents[1]
PLAYERLIST_PATH = STATS_DIR / "manual" / "playerlist.csv"
OUTPUT_PATH = STATS_DIR / "history" / "bballref_position_estimate.csv"

POSITION_ORDER = ("PG", "SG", "SF", "PF", "C")


def season_to_end_year(season: str) -> int:
    text = str(season).strip()
    if not text:
        raise ValueError("Season is blank.")
    end_part = text.split("-")[-1]
    if len(end_part) == 2:
        return 2000 + int(end_part)
    return int(end_part)


def load_seasons_from_playerlist(path: Path) -> list[str]:
    frame = pd.read_csv(path, usecols=["Season"])
    seasons = sorted({str(value).strip() for value in frame["Season"].dropna()}, key=season_to_end_year)
    if not seasons:
        raise ValueError(f"No seasons found in {path}")
    return seasons


def flatten_columns(columns: Iterable[object]) -> list[str]:
    flattened: list[str] = []
    for column in columns:
        if isinstance(column, tuple):
            parts = [str(part).strip() for part in column if str(part).strip() and str(part).strip().lower() != "nan"]
            flattened.append(" ".join(parts))
        else:
            flattened.append(str(column).strip())
    return flattened


def normalize_header(text: object) -> str:
    normalized = unicodedata.normalize("NFKD", str(text))
    normalized = "".join(char for char in normalized if not unicodedata.combining(char))
    normalized = " ".join(normalized.replace("\xa0", " ").split())
    return normalized


def select_position_table(tables: list[pd.DataFrame]) -> pd.DataFrame:
    for table in tables:
        headers = [normalize_header(value) for value in flatten_columns(table.columns)]
        has_player = any(header.endswith("Player") or header == "Player" for header in headers)
        has_team = any(header.endswith("Team") or header == "Team" or header.endswith("Tm") for header in headers)
        has_percentages = all(any(header.endswith(position + "%") for header in headers) for position in POSITION_ORDER)
        if has_player and has_team and has_percentages:
            selected = table.copy()
            selected.columns = headers
            return selected
    raise RuntimeError("Could not find the Basketball Reference position estimate table.")


def find_column(columns: list[str], *suffixes: str) -> str:
    for suffix in suffixes:
        for column in columns:
            if column == suffix or column.endswith(suffix):
                return column
    raise KeyError(f"Could not find header matching any of: {suffixes}")


def to_numeric(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series.astype(str).str.replace("%", "", regex=False).str.strip(), errors="coerce")


def choose_primary_position(row: pd.Series) -> str:
    values = {position: float(row.get(position, 0.0) or 0.0) for position in POSITION_ORDER}
    max_value = max(values.values(), default=0.0)
    if max_value > 0:
        candidates = [position for position in POSITION_ORDER if values[position] == max_value]
        pos_value = str(row.get("Pos", "") or "").strip()
        if pos_value in candidates:
            return pos_value
        return candidates[0]
    pos_value = str(row.get("Pos", "") or "").strip()
    return pos_value if pos_value in POSITION_ORDER else ""


def choose_second_position(row: pd.Series) -> str:
    primary = str(row.get("Primary_Position", "") or "").strip()
    values = {position: float(row.get(position, 0.0) or 0.0) for position in POSITION_ORDER if position != primary}
    if not values:
        return ""
    max_value = max(values.values(), default=0.0)
    if max_value <= 0:
        return ""
    for position in POSITION_ORDER:
        if position != primary and values.get(position, 0.0) == max_value:
            return position
    return ""


def fetch_position_estimate(season: str) -> pd.DataFrame:
    end_year = season_to_end_year(season)
    url = f"https://www.basketball-reference.com/leagues/NBA_{end_year}_play-by-play.html"
    tables = pd.read_html(url)
    table = select_position_table(tables)
    columns = list(table.columns)

    player_column = find_column(columns, "Player")
    team_column = find_column(columns, "Team", "Tm")
    pos_column = find_column(columns, "Pos")
    pg_column = find_column(columns, "PG%")
    sg_column = find_column(columns, "SG%")
    sf_column = find_column(columns, "SF%")
    pf_column = find_column(columns, "PF%")
    c_column = find_column(columns, "C%")

    frame = pd.DataFrame(
        {
            "Season": season,
            "Player": table[player_column].astype(str).str.strip(),
            "Team": table[team_column].astype(str).str.strip(),
            "Pos": table[pos_column].astype(str).str.strip(),
            "PG": to_numeric(table[pg_column]),
            "SG": to_numeric(table[sg_column]),
            "SF": to_numeric(table[sf_column]),
            "PF": to_numeric(table[pf_column]),
            "C": to_numeric(table[c_column]),
            "SourceURL": url,
        }
    )

    frame = frame[
        frame["Player"].notna()
        & frame["Player"].ne("")
        & frame["Player"].ne("Player")
        & frame["Team"].notna()
        & frame["Team"].ne("")
    ].reset_index(drop=True)

    frame["Primary_Position"] = frame.apply(choose_primary_position, axis=1)
    frame["Second_Position"] = frame.apply(choose_second_position, axis=1)
    return frame[
        [
            "Season",
            "Player",
            "Team",
            "Pos",
            "PG",
            "SG",
            "SF",
            "PF",
            "C",
            "Primary_Position",
            "Second_Position",
            "SourceURL",
        ]
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description="Pull Basketball Reference position-estimate tables by season.")
    parser.add_argument("--playerlist", type=Path, default=PLAYERLIST_PATH)
    parser.add_argument("--output", type=Path, default=OUTPUT_PATH)
    parser.add_argument("--seasons", nargs="*", default=None, help="Season strings like 2013-14 2014-15.")
    parser.add_argument("--sleep-seconds", type=float, default=0.75)
    parser.add_argument("--retries", type=int, default=2)
    args = parser.parse_args()

    seasons = args.seasons or load_seasons_from_playerlist(args.playerlist)

    collected: list[pd.DataFrame] = []
    for season in seasons:
        last_error: Exception | None = None
        for attempt in range(1, args.retries + 2):
            try:
                print(f"[PULL] Basketball Reference positions {season} (attempt {attempt})")
                collected.append(fetch_position_estimate(season))
                last_error = None
                break
            except Exception as exc:  # pragma: no cover - network retries are environment-dependent
                last_error = exc
                if attempt > args.retries:
                    raise
                time.sleep(args.sleep_seconds)
        if last_error is None and args.sleep_seconds > 0:
            time.sleep(args.sleep_seconds)

    output = pd.concat(collected, ignore_index=True)
    output = output.sort_values(["Season", "Player", "Team"], kind="stable").reset_index(drop=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    output.to_csv(args.output, index=False, encoding="utf-8-sig")
    print(f"[WROTE] {args.output} ({len(output)} rows)")


if __name__ == "__main__":
    main()
