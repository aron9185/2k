from __future__ import annotations

import argparse
import json
import re
import time
import unicodedata
from io import StringIO
from pathlib import Path
from typing import Any, Iterable

import pandas as pd
import requests

from rotation_plot import canonical_team_abbr, load_position_estimate, norm_name

BASE_DIR = Path(__file__).resolve().parent
CACHE_DIR = BASE_DIR / "cache_json"
DEFAULT_OUTPUT = BASE_DIR / "sportsref_download.xls"
DEFAULT_MISMATCH_OUTPUT = BASE_DIR / "sportsref_position_mismatches.csv"
HISTORY_SOURCE = BASE_DIR.parent / "stats" / "history" / "bballref_position_estimate.csv"

POSITION_ORDER = ("PG", "SG", "SF", "PF", "C")
CURRENT_SEASON_URL = "https://www.basketball-reference.com/leagues/NBA_{end_year}_play-by-play.html"
WEB_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/136.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def season_to_end_year(season: str) -> int:
    text = str(season).strip()
    if not text:
        raise ValueError("Season is blank.")
    end_part = text.split("-")[-1]
    if len(end_part) == 2:
        return 2000 + int(end_part)
    return int(end_part)


def end_year_to_season(end_year: int) -> str:
    return f"{end_year - 1}-{str(end_year)[-2:]}"


def default_season() -> str:
    now = time.localtime()
    end_year = now.tm_year + 1 if now.tm_mon >= 7 else now.tm_year
    return end_year_to_season(end_year)


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


def fetch_current_position_frame(season: str) -> pd.DataFrame:
    end_year = season_to_end_year(season)
    url = CURRENT_SEASON_URL.format(end_year=end_year)
    session = requests.Session()
    session.trust_env = False
    response = session.get(url, timeout=(4, 12), headers=WEB_HEADERS)
    response.raise_for_status()
    tables = pd.read_html(StringIO(response.text))
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

    optional_columns = {
        "Rk": next((column for column in columns if column == "Rk" or column.endswith("Rk")), None),
        "Age": next((column for column in columns if column == "Age" or column.endswith("Age")), None),
        "G": next((column for column in columns if column == "G" or column.endswith("G")), None),
        "GS": next((column for column in columns if column == "GS" or column.endswith("GS")), None),
        "MP": next((column for column in columns if column == "MP" or column.endswith("MP")), None),
    }

    frame = pd.DataFrame(
        {
            "Rk": table[optional_columns["Rk"]] if optional_columns["Rk"] else range(1, len(table) + 1),
            "Player": table[player_column].astype(str).str.strip(),
            "Age": table[optional_columns["Age"]] if optional_columns["Age"] else "",
            "Team": table[team_column].astype(str).str.strip().map(canonical_team_abbr),
            "Pos": table[pos_column].astype(str).str.strip(),
            "G": table[optional_columns["G"]] if optional_columns["G"] else "",
            "GS": table[optional_columns["GS"]] if optional_columns["GS"] else "",
            "MP": table[optional_columns["MP"]] if optional_columns["MP"] else "",
            "PG%": to_numeric(table[pg_column]),
            "SG%": to_numeric(table[sg_column]),
            "SF%": to_numeric(table[sf_column]),
            "PF%": to_numeric(table[pf_column]),
            "C%": to_numeric(table[c_column]),
            "SourceURL": url,
        }
    )

    frame = frame[
        frame["Player"].notna()
        & frame["Player"].ne("")
        & frame["Player"].ne("Player")
        & frame["Team"].notna()
        & frame["Team"].ne("")
        & ~frame["Team"].astype(str).str.endswith("TM")
    ].reset_index(drop=True)

    return frame


def load_position_frame_from_history(season: str, source_path: Path = HISTORY_SOURCE) -> pd.DataFrame:
    if not source_path.exists():
        raise FileNotFoundError(f"History source not found: {source_path}")

    history = pd.read_csv(source_path)
    subset = history[history["Season"].astype(str).str.strip() == str(season).strip()].copy()
    if subset.empty:
        raise RuntimeError(f"No {season} rows found in {source_path}")

    frame = pd.DataFrame(
        {
            "Rk": range(1, len(subset) + 1),
            "Player": subset["Player"].astype(str).str.strip(),
            "Age": "",
            "Team": subset["Team"].astype(str).str.strip().map(canonical_team_abbr),
            "Pos": subset["Pos"].astype(str).str.strip(),
            "G": "",
            "GS": "",
            "MP": "",
            "PG%": pd.to_numeric(subset["PG"], errors="coerce"),
            "SG%": pd.to_numeric(subset["SG"], errors="coerce"),
            "SF%": pd.to_numeric(subset["SF"], errors="coerce"),
            "PF%": pd.to_numeric(subset["PF"], errors="coerce"),
            "C%": pd.to_numeric(subset["C"], errors="coerce"),
            "SourceURL": subset["SourceURL"].astype(str).str.strip(),
        }
    )
    frame = frame[
        frame["Player"].notna()
        & frame["Player"].ne("")
        & frame["Team"].notna()
        & frame["Team"].ne("")
        & ~frame["Team"].astype(str).str.endswith("TM")
    ].reset_index(drop=True)
    return frame


def write_html_xls(frame: pd.DataFrame, output_path: Path, season: str) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    display_frame = frame.copy()
    display_frame.insert(0, "Season", season)

    html_table = display_frame.to_html(index=False, border=0)
    document = (
        "<html><head>"
        "<meta http-equiv='Content-Type' content='text/html; charset=utf-8'>"
        "<meta name='ProgId' content='Excel.Sheet'>"
        "<title>Sports Reference Position Estimate</title>"
        "</head><body>"
        f"{html_table}"
        "</body></html>"
    )
    output_path.write_text(document, encoding="utf-8")


def build_mismatch_report(position_path: Path, output_path: Path) -> pd.DataFrame:
    lookup = load_position_estimate(str(position_path))
    lookup_names: dict[str, set[str]] = {}
    for team, player_name in lookup:
        lookup_names.setdefault(player_name, set()).add(team)

    rows: list[dict[str, Any]] = []
    for path in sorted(CACHE_DIR.glob("box_*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        game = (payload.get("game") or {}) if isinstance(payload, dict) else {}
        game_id = str(game.get("gameId") or path.stem.removeprefix("box_"))
        for side in ("homeTeam", "awayTeam"):
            team = game.get(side) or {}
            team_abbr = canonical_team_abbr(team.get("teamTricode") or "")
            for player in team.get("players") or []:
                raw_name = str(player.get("name") or "").strip()
                if not raw_name:
                    continue
                normalized_name = norm_name(raw_name)
                if (team_abbr, normalized_name) in lookup:
                    continue

                candidate_teams = sorted(lookup_names.get(normalized_name, set()))
                mismatch_type = "missing_from_source"
                reason = "No matching player name in Sports Reference position file."
                if candidate_teams:
                    mismatch_type = "team_mismatch"
                    reason = f"Name exists under different team code(s): {', '.join(candidate_teams)}"

                rows.append(
                    {
                        "GameID": game_id,
                        "CacheFile": path.name,
                        "Team": team_abbr,
                        "Player": raw_name,
                        "NormalizedName": normalized_name,
                        "MismatchType": mismatch_type,
                        "Reason": reason,
                        "CandidateTeams": "|".join(candidate_teams),
                    }
                )

    report = pd.DataFrame(rows).drop_duplicates(subset=["Team", "Player", "MismatchType", "CandidateTeams"]).sort_values(
        ["MismatchType", "Team", "Player"],
        kind="stable",
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    report.to_csv(output_path, index=False, encoding="utf-8-sig")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Refresh rotation/sportsref_download.xls from Basketball Reference.")
    parser.add_argument("--season", default=default_season(), help="Season string like 2025-26.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--mismatch-output", type=Path, default=DEFAULT_MISMATCH_OUTPUT)
    args = parser.parse_args()

    source_mode = "live"
    try:
        frame = fetch_current_position_frame(args.season)
    except Exception as exc:
        print(f"[WARN] live Basketball Reference pull failed, falling back to history csv: {exc}")
        frame = load_position_frame_from_history(args.season)
        source_mode = "history"
    write_html_xls(frame, args.output, args.season)
    report = build_mismatch_report(args.output, args.mismatch_output)

    print(f"[WROTE] {args.output} ({len(frame)} rows, source={source_mode})")
    print(f"[WROTE] {args.mismatch_output} ({len(report)} mismatch rows)")
    if not report.empty:
        summary = report["MismatchType"].value_counts().to_dict()
        print(f"[SUMMARY] {summary}")


if __name__ == "__main__":
    main()
