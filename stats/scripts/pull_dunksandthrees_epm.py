from __future__ import annotations

import argparse
import csv
import datetime as dt
import re
from io import StringIO
from pathlib import Path
from typing import Dict, List, Sequence

import pandas as pd

from urllib.parse import urlencode
from urllib.request import Request, urlopen

from pull_nba_stats import HISTORY_DIR


BASE_URL = "https://dunksandthrees.com/epm/actual"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/136.0.0.0 Safari/537.36"
)


def default_season_end_year(today: dt.date | None = None) -> int:
    today = today or dt.date.today()
    return today.year if today.month <= 6 else today.year + 1


def season_label(end_year: int) -> str:
    return f"{end_year - 1}-{end_year % 100:02d}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Pull the public Dunks & Threes actual EPM table into stats/history."
    )
    parser.add_argument(
        "--season-end-year",
        type=int,
        default=default_season_end_year(),
        help="Season end year used in the query string and the output Season label.",
    )
    parser.add_argument(
        "--output",
        default=str(HISTORY_DIR / "dunksandthrees_epm.csv"),
        help="Output CSV path.",
    )
    return parser.parse_args()


def fetch_html(season_end_year: int) -> str:
    query = urlencode({"season": season_end_year})
    request = Request(
        f"{BASE_URL}?{query}",
        headers={"User-Agent": USER_AGENT},
    )
    with urlopen(request, timeout=60) as response:
        return response.read().decode("utf-8", errors="ignore")


def flatten_columns(columns: Sequence[object]) -> List[str]:
    flattened: List[str] = []
    for column in columns:
        if isinstance(column, tuple):
            top = str(column[0] or "").strip()
            bottom = str(column[1] or "").strip()
            if bottom and "Unnamed" not in bottom:
                flattened.append(bottom)
            elif top and "Unnamed" not in top:
                flattened.append(top)
            else:
                flattened.append(bottom or top or "")
            continue
        flattened.append(str(column or "").strip())
    return flattened


def extract_first_number(value: object) -> float | int | None:
    text = str(value or "").strip()
    if not text:
        return None
    match = re.search(r"[-+]?\d+(?:\.\d+)?", text.replace("−", "-"))
    if not match:
        return None
    number = float(match.group(0))
    if number.is_integer():
        return int(number)
    return number


def parse_player_blob(value: object) -> Dict[str, object]:
    text = str(value or "").strip()
    if not text:
        return {"PLAYER_NAME": "", "TEAM_ABBREVIATION": "", "POS": "", "AGE": None}

    match = re.match(
        r"^(?P<player>.+?)\s+(?P<team>[A-Z0-9]{2,4})\s+·\s+(?P<pos>[^·]+)\s+·\s+(?P<age>\d+)$",
        text,
    )
    if not match:
        return {"PLAYER_NAME": text, "TEAM_ABBREVIATION": "", "POS": "", "AGE": None}

    return {
        "PLAYER_NAME": match.group("player").strip(),
        "TEAM_ABBREVIATION": match.group("team").strip(),
        "POS": match.group("pos").strip(),
        "AGE": int(match.group("age")),
    }


def parse_table(html: str, season: str) -> List[Dict[str, object]]:
    tables = pd.read_html(StringIO(html))
    if not tables:
        raise SystemExit("No tables found on the Dunks & Threes EPM page.")

    df = tables[0].copy()
    df.columns = flatten_columns(df.columns)

    rows: List[Dict[str, object]] = []
    for _, row in df.iterrows():
        player_bits = parse_player_blob(row.get("Player", ""))
        player_name = str(player_bits.get("PLAYER_NAME", "")).strip()
        if not player_name:
            continue

        rows.append(
            {
                "Season": season,
                "PLAYER_ID": "",
                "PLAYER_NAME": player_name,
                "TEAM_ABBREVIATION": player_bits.get("TEAM_ABBREVIATION", ""),
                "POS": player_bits.get("POS", ""),
                "AGE": player_bits.get("AGE", ""),
                "GP": extract_first_number(row.get("GP", "")),
                "MPG": extract_first_number(row.get("MPG", "")),
                "USG": extract_first_number(row.get("USG", "")),
                "OFF": extract_first_number(row.get("OFF", "")),
                "DEF": extract_first_number(row.get("DEF", "")),
                "EPM": extract_first_number(row.get("EPM", "")),
                "EW": extract_first_number(row.get("EW", "")),
                "TS_PCT": extract_first_number(row.get("TS%", "")),
                "EFG_PCT": extract_first_number(row.get("eFG%", "")),
                "RIM_PCT": extract_first_number(row.get("Rim%", "")),
                "MID_PCT": extract_first_number(row.get("Mid%", "")),
                "FG3_PCT": extract_first_number(row.get("3PT%", "")),
                "FT_PCT": extract_first_number(row.get("FT%", "")),
                "OREB_PCT": extract_first_number(row.get("OR%", "")),
                "DREB_PCT": extract_first_number(row.get("DR%", "")),
                "AST_PCT": extract_first_number(row.get("AST%", "")),
                "TOV_PCT": extract_first_number(row.get("TO%", "")),
                "STL_PCT": extract_first_number(row.get("ST%", "")),
                "BLK_PCT": extract_first_number(row.get("BL%", "")),
                "RK": extract_first_number(row.get("RK", "")),
            }
        )
    return rows


def write_rows(path: Path, rows: Sequence[Dict[str, object]]) -> None:
    headers = [
        "Season",
        "PLAYER_ID",
        "PLAYER_NAME",
        "TEAM_ABBREVIATION",
        "POS",
        "AGE",
        "GP",
        "MPG",
        "USG",
        "OFF",
        "DEF",
        "EPM",
        "EW",
        "TS_PCT",
        "EFG_PCT",
        "RIM_PCT",
        "MID_PCT",
        "FG3_PCT",
        "FT_PCT",
        "OREB_PCT",
        "DREB_PCT",
        "AST_PCT",
        "TOV_PCT",
        "STL_PCT",
        "BLK_PCT",
        "RK",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=headers, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({header: row.get(header, "") for header in headers})


def main() -> None:
    args = parse_args()
    season = season_label(args.season_end_year)
    html = fetch_html(args.season_end_year)
    rows = parse_table(html, season=season)
    output_path = Path(args.output)
    write_rows(output_path, rows)
    print(f"[OK] Dunks & Threes EPM -> {output_path} ({len(rows)} rows for {season})")


if __name__ == "__main__":
    main()
