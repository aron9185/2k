from __future__ import annotations

import argparse
import csv
import gzip
import json
import ssl
import time
import zlib
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

try:
    from urllib.parse import urlencode
    from urllib.request import Request, urlopen
except ImportError:
    from urllib import urlencode
    from urllib2 import Request, urlopen

from build_cal_lane import canonical_id
from pull_nba_stats import HEADERS as NBA_STATS_HEADERS
from pull_nba_stats import HISTORY_DIR, MANUAL_DIR, TMP_DIR


SHOOTING_SPLITS_URL = "https://stats.nba.com/stats/playerdashboardbyshootingsplits"
DUNK_SCORE_URL = "https://stats.nba.com/stats/dunkscoreleaders"
SSL_CONTEXT = ssl.create_default_context()
INSECURE_SSL_CONTEXT = ssl._create_unverified_context()

SHOOTING_SPLITS_TMP_PATH = TMP_DIR / "shooting_splits_tmp.csv"
SHOOTING_SPLITS_HISTORY_PATH = HISTORY_DIR / "shooting_splits.csv"
DUNK_LEADERBOARD_TMP_PATH = TMP_DIR / "dunks_leaderboard_tmp.csv"
STANDING_DUNK_STATS_TMP_PATH = TMP_DIR / "standing_dunk_stats_tmp.csv"
OVERALL_DUNK_STATS_TMP_PATH = TMP_DIR / "overall_dunk_stats_tmp.csv"
STANDING_DUNK_STATS_HISTORY_PATH = HISTORY_DIR / "standing_dunk_stats.csv"
OVERALL_DUNK_STATS_HISTORY_PATH = HISTORY_DIR / "overall_dunk_stats.csv"
SHOOTING_SPLITS_FAILURES_PATH = TMP_DIR / "shooting_splits_failures_tmp.csv"

LEGACY_SHOOTING_HISTORY_PATH = Path("splitsshooting_old.csv")
LEGACY_STANDING_DUNK_HISTORY_PATH = Path("standing_dunk_stats.csv")
LEGACY_OVERALL_DUNK_HISTORY_PATH = Path("overall_dunk_stats.csv")

SHOOTING_SPLITS_OUTPUT_COLUMNS = (
    "FGM",
    "FGA",
    "FG_PCT",
)

OVERALL_DUNK_EXCLUDED_HEADERS = (
    "jerseyNum_average",
    "jerseyNum_max",
    "jerseyNum_total",
    "urlDate_average",
    "urlDate_max",
    "urlDate_total",
    "flairSubscore_average",
    "flairSubscore_max",
    "flairSubscore_total",
    "ballCockBack_average",
    "ballCockBack_max",
    "ballCockBack_total",
)

SHOOTING_SPLITS_PARAM_ORDER = (
    ("DateFrom", ""),
    ("DateTo", ""),
    ("GameSegment", ""),
    ("LastNGames", 0),
    ("LeagueID", "00"),
    ("Location", ""),
    ("MeasureType", "Base"),
    ("Month", 0),
    ("OpponentTeamID", 0),
    ("Outcome", ""),
    ("PORound", 0),
    ("PaceAdjust", "N"),
    ("PerMode", "PerGame"),
    ("Period", 0),
    ("PlayerID", ""),
    ("PlusMinus", "N"),
    ("Rank", "N"),
    ("Season", ""),
    ("SeasonSegment", ""),
    ("SeasonType", "Regular Season"),
    ("ShotClockRange", ""),
    ("Split", "general"),
    ("VsConference", ""),
    ("VsDivision", ""),
)

DUNK_SKIP_COLUMNS = {
    "Season",
    "gameId",
    "gameDate",
    "matchup",
    "urlDate",
    "urlTeams",
    "dunkTimeUTC",
    "period",
    "gameClockTime",
    "eventNum",
    "playerId",
    "jerseyNum",
    "teamId",
    "passerId",
    "passerName",
    "shooterId",
    "shotReleasePoint",
    "shotLength",
    "possibleAttemptedCharge",
    "videoAvailable",
    "videoData",
}


def decode_payload(payload: Any, content_encoding: str) -> str:
    if isinstance(payload, str):
        return payload

    encoding = (content_encoding or "").lower()
    if "gzip" in encoding or payload[:2] == b"\x1f\x8b":
        payload = gzip.decompress(payload)
    elif "deflate" in encoding:
        try:
            payload = zlib.decompress(payload)
        except zlib.error:
            payload = zlib.decompress(payload, -zlib.MAX_WBITS)

    return payload.decode("utf-8")


def fetch_json_url(
    url: str,
    headers: Dict[str, str],
    retries: int,
    pause: float,
) -> Dict[str, Any]:
    last_error: Optional[Exception] = None

    for attempt in range(1, retries + 1):
        try:
            request = Request(url, headers=headers)
            try:
                with urlopen(request, timeout=45, context=SSL_CONTEXT) as response:
                    payload = response.read()
                    content_encoding = response.info().get("Content-Encoding", "")
            except Exception as exc:
                if "CERTIFICATE_VERIFY_FAILED" not in str(exc):
                    raise
                print("[WARN] SSL verification failed, retrying without certificate validation.")
                with urlopen(request, timeout=45, context=INSECURE_SSL_CONTEXT) as response:
                    payload = response.read()
                    content_encoding = response.info().get("Content-Encoding", "")
            return json.loads(decode_payload(payload, content_encoding))
        except Exception as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(pause * attempt)

    raise RuntimeError(f"Failed to fetch JSON from {url}") from last_error


def write_matrix_csv(path: Path, headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(list(headers))
        writer.writerows(rows)


def read_csv_dicts(path: Path, encoding: str = "utf-8") -> Tuple[List[str], List[Dict[str, str]]]:
    if not path.exists():
        return [], []
    with path.open("r", newline="", encoding=encoding) as fh:
        reader = csv.DictReader(fh)
        return list(reader.fieldnames or []), [dict(row) for row in reader]


def write_csv_dicts(
    path: Path,
    headers: Sequence[str],
    rows: Sequence[Dict[str, Any]],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(headers), extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({header: row.get(header, "") for header in headers})


def merge_headers(*header_groups: Iterable[str]) -> List[str]:
    merged: List[str] = []
    seen = set()
    for group in header_groups:
        for header in group:
            if header and header not in seen:
                merged.append(header)
                seen.add(header)
    return merged


def load_seed_rows(
    history_path: Path,
    legacy_path: Optional[Path],
    legacy_encoding: str = "utf-8",
    inject_season: str = "",
) -> Tuple[List[str], List[Dict[str, str]]]:
    if history_path.exists():
        return read_csv_dicts(history_path, encoding="utf-8")

    if legacy_path and legacy_path.exists():
        headers, rows = read_csv_dicts(legacy_path, encoding=legacy_encoding)
        if inject_season and "Season" not in headers:
            headers = ["Season", *headers]
            rows = [{**row, "Season": inject_season} for row in rows]
        return headers, rows

    return [], []


def filter_header_set(
    headers: Sequence[str],
    rows: Sequence[Dict[str, Any]],
    excluded_headers: Sequence[str],
) -> Tuple[List[str], List[Dict[str, Any]]]:
    excluded = set(excluded_headers)
    kept_headers = [header for header in headers if header not in excluded]
    if not excluded:
        return kept_headers, list(rows)

    filtered_rows: List[Dict[str, Any]] = []
    for row in rows:
        filtered_rows.append(
            {header: row.get(header, "") for header in kept_headers}
        )
    return kept_headers, filtered_rows


def merge_season_rows(
    current_headers: Sequence[str],
    current_rows: Sequence[Dict[str, Any]],
    history_path: Path,
    legacy_path: Optional[Path] = None,
    legacy_encoding: str = "utf-8",
    inject_legacy_season: str = "",
    excluded_headers: Sequence[str] = (),
) -> Tuple[Path, int, int]:
    previous_headers, previous_rows = load_seed_rows(
        history_path,
        legacy_path=legacy_path,
        legacy_encoding=legacy_encoding,
        inject_season=inject_legacy_season,
    )
    previous_headers, previous_rows = filter_header_set(
        previous_headers,
        previous_rows,
        excluded_headers,
    )
    current_headers, current_rows = filter_header_set(
        current_headers,
        current_rows,
        excluded_headers,
    )
    season_set = {
        str(row.get("Season", "")).strip()
        for row in current_rows
        if str(row.get("Season", "")).strip()
    }
    retained_rows = [
        row
        for row in previous_rows
        if str(row.get("Season", "")).strip() not in season_set
    ]
    headers = merge_headers(current_headers, previous_headers)
    merged_rows = [*current_rows, *retained_rows]
    write_csv_dicts(history_path, headers, merged_rows)
    return history_path, len(current_rows), len(retained_rows)


def parse_numeric(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except Exception:
        return None


def first_present_column(
    available_columns: Iterable[str],
    candidates: Iterable[str],
) -> str:
    column_set = {str(column).strip() for column in available_columns}
    for candidate in candidates:
        if candidate in column_set:
            return candidate
    return ""


def season_players(
    universe_csv: Path,
    season: str,
) -> List[Dict[str, str]]:
    headers, rows = read_csv_dicts(universe_csv, encoding="utf-8-sig")
    if not rows:
        raise SystemExit(f"No rows found in universe CSV: {universe_csv}")

    id_column = first_present_column(headers, ["NBA_ID", "nba_id", "PLAYER_ID", "PlayerID"])
    season_column = first_present_column(headers, ["Season", "season"])
    player_column = first_present_column(headers, ["Player", "PLAYER_NAME", "Name", "player"])

    if not season_column or not player_column:
        raise SystemExit(f"Universe CSV is missing Season/Player columns: {universe_csv}")

    filtered: List[Dict[str, str]] = []
    seen = set()
    for row in rows:
        row_season = str(row.get(season_column, "")).strip()
        player = str(row.get(player_column, "")).strip()
        player_id = canonical_id(row.get(id_column, "")) if id_column else ""
        if row_season != season or not player or not player_id:
            continue
        key = (season, player_id)
        if key in seen:
            continue
        seen.add(key)
        filtered.append(
            {
                "PlayerID": player_id,
                "Season": season,
                "Name": player,
            }
        )

    if not filtered:
        raise SystemExit(f"No {season} player rows found in universe CSV: {universe_csv}")
    return filtered


def shooting_splits_url(player_id: str, season: str) -> str:
    params = []
    for key, default_value in SHOOTING_SPLITS_PARAM_ORDER:
        if key == "PlayerID":
            value = player_id
        elif key == "Season":
            value = season
        else:
            value = default_value
        params.append((key, value))
    return f"{SHOOTING_SPLITS_URL}?{urlencode(params)}"


def dunk_score_url(
    season: str,
    season_type: str = "Regular Season",
    league_id: str = "00",
) -> str:
    params = [
        ("LeagueID", league_id),
        ("Season", season),
        ("SeasonType", season_type),
    ]
    return f"{DUNK_SCORE_URL}?{urlencode(params)}"


def flatten_shooting_response(
    player_id: str,
    season: str,
    player_name: str,
    response_json: Dict[str, Any],
) -> Dict[str, Any]:
    row: Dict[str, Any] = {
        "PlayerID": player_id,
        "Season": season,
        "Name": player_name,
    }

    for result_set in response_json.get("resultSets", []) or []:
        headers = result_set.get("headers") or []
        row_set = result_set.get("rowSet") or []
        if not isinstance(headers, list) or not isinstance(row_set, list):
            continue

        for shot_row in row_set:
            if len(shot_row) < 5:
                continue
            shot_group = str(shot_row[0] or "")
            shot_type = str(shot_row[1] or "")
            if "Shot Type" not in shot_group or "Summary" in shot_group or not shot_type:
                continue
            for column_index, header in enumerate(headers):
                if column_index < 2 or column_index > 4 or column_index >= len(shot_row):
                    continue
                row[f"{shot_type}_{header}"] = shot_row[column_index]

    return row


def pull_shooting_splits(
    season: str,
    universe_csv: Path,
    retries: int,
    pause: float,
    player_pause: float,
) -> Tuple[List[str], List[Dict[str, Any]], List[Dict[str, str]]]:
    players = season_players(universe_csv, season)
    pulled_rows: List[Dict[str, Any]] = []
    failures: List[Dict[str, str]] = []
    all_headers = {"PlayerID", "Season", "Name"}

    for index, player in enumerate(players, start=1):
        player_id = player["PlayerID"]
        player_name = player["Name"]
        url = shooting_splits_url(player_id, season)
        try:
            payload = fetch_json_url(url, NBA_STATS_HEADERS, retries=retries, pause=pause)
            flat_row = flatten_shooting_response(player_id, season, player_name, payload)
            pulled_rows.append(flat_row)
            all_headers.update(flat_row.keys())
        except Exception as exc:
            failures.append(
                {
                    "PlayerID": player_id,
                    "Season": season,
                    "Name": player_name,
                    "Error": str(exc),
                }
            )
        if player_pause > 0 and index < len(players):
            time.sleep(player_pause)
        if index % 50 == 0 or index == len(players):
            print(f"[INFO] Shooting splits: {index}/{len(players)} player rows processed")

    headers = merge_headers(["PlayerID", "Season", "Name"], sorted(all_headers))
    return headers, pulled_rows, failures


def load_dunk_rows(
    url: str,
    season: str,
    retries: int,
    pause: float,
) -> Tuple[List[str], List[Dict[str, Any]]]:
    payload = fetch_json_url(url, NBA_STATS_HEADERS, retries=retries, pause=pause)
    params = payload.get("params") or {}
    payload_season = str(
        params.get("seasonYear") or params.get("Season") or season
    ).strip() or season
    dunk_rows = payload.get("dunks") or payload.get("dunkScores") or []
    if not isinstance(dunk_rows, list):
        raise RuntimeError("Dunk score payload did not contain a list of dunk rows.")

    normalized_rows: List[Dict[str, Any]] = []
    ordered_headers: List[str] = ["Season"]
    seen_headers = {"Season"}

    for dunk in dunk_rows:
        if not isinstance(dunk, dict):
            continue
        normalized = {"Season": payload_season, **dunk}
        if "styleSubscore" not in normalized and "flairSubscore" in normalized:
            normalized["styleSubscore"] = normalized.get("flairSubscore")
        if "ballReachBack" not in normalized and "ballCockBack" in normalized:
            normalized["ballReachBack"] = normalized.get("ballCockBack")
        if "dunk360" not in normalized and "360Dunk" in normalized:
            normalized["dunk360"] = normalized.get("360Dunk")
        normalized_rows.append(normalized)
        for key in normalized.keys():
            if key not in seen_headers:
                ordered_headers.append(key)
                seen_headers.add(key)

    return ordered_headers, normalized_rows


def numeric_dunk_columns(rows: Sequence[Dict[str, Any]]) -> List[str]:
    ordered_columns: List[str] = []
    seen = set()

    for row in rows:
        for key in row.keys():
            if key in seen or key in DUNK_SKIP_COLUMNS:
                continue
            if parse_numeric(row.get(key)) is None:
                continue
            ordered_columns.append(key)
            seen.add(key)

    return ordered_columns


def aggregate_dunk_rows(
    rows: Sequence[Dict[str, Any]],
    standing_only: bool,
) -> Tuple[List[str], List[Dict[str, Any]]]:
    filtered_rows: List[Dict[str, Any]] = []
    for row in rows:
        if standing_only:
            takeoff_distance = parse_numeric(row.get("takeoffDistance"))
            if takeoff_distance is None or takeoff_distance >= 4.0:
                continue
        filtered_rows.append(row)

    columns = numeric_dunk_columns(filtered_rows)
    grouped: Dict[Tuple[str, str], Dict[str, List[float]]] = {}

    for row in filtered_rows:
        season = str(row.get("Season", "")).strip()
        player_name = str(row.get("playerName", "")).strip()
        if not season or not player_name:
            continue
        bucket = grouped.setdefault(
            (season, player_name),
            {column: [] for column in columns},
        )
        for column in columns:
            numeric_value = parse_numeric(row.get(column))
            if numeric_value is not None:
                bucket[column].append(numeric_value)

    output_headers = ["Season", "playerName"]
    output_headers.extend(f"{column}_average" for column in columns)
    output_headers.extend(f"{column}_max" for column in columns)
    output_headers.extend(f"{column}_total" for column in columns)

    output_rows: List[Dict[str, Any]] = []
    for (season, player_name) in sorted(grouped.keys()):
        row: Dict[str, Any] = {
            "Season": season,
            "playerName": player_name,
        }
        bucket = grouped[(season, player_name)]
        for column in columns:
            values = bucket[column]
            if not values:
                continue
            row[f"{column}_average"] = sum(values) / len(values)
            row[f"{column}_max"] = max(values)
            row[f"{column}_total"] = sum(values)
        output_rows.append(row)

    return output_headers, output_rows


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Refresh the NBA.com Standing/Overall dunk sources into stats/tmp and stats/history."
        )
    )
    parser.add_argument(
        "--season",
        default="2025-26",
        help="Season string used for the refresh, e.g. 2025-26.",
    )
    parser.add_argument(
        "--universe-csv",
        default=str(MANUAL_DIR / "playerlist.csv"),
        help="Season/player universe CSV used for shooting split pulls.",
    )
    parser.add_argument(
        "--dunk-url",
        default="",
        help="Optional override URL used for dunk score pulls.",
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=3,
        help="Retry count per request.",
    )
    parser.add_argument(
        "--pause",
        type=float,
        default=1.0,
        help="Base pause between request retries.",
    )
    parser.add_argument(
        "--player-pause",
        type=float,
        default=0.0,
        help="Optional pause between each shooting-splits player request.",
    )
    parser.add_argument(
        "--skip-shooting-splits",
        action="store_true",
        help="Skip the shooting-splits refresh and merge step.",
    )
    parser.add_argument(
        "--skip-dunk-scores",
        action="store_true",
        help="Skip the dunk-score refresh and merge step.",
    )
    parser.add_argument(
        "--legacy-overall-season",
        default="2024-25",
        help=(
            "Season injected into the repo-root overall_dunk_stats.csv seed file when it has "
            "no Season column."
        ),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    universe_csv = Path(args.universe_csv)

    if not args.skip_shooting_splits:
        shooting_headers, shooting_rows, shooting_failures = pull_shooting_splits(
            season=args.season,
            universe_csv=universe_csv,
            retries=args.retries,
            pause=args.pause,
            player_pause=args.player_pause,
        )
        write_csv_dicts(SHOOTING_SPLITS_TMP_PATH, shooting_headers, shooting_rows)
        if shooting_failures:
            write_csv_dicts(
                SHOOTING_SPLITS_FAILURES_PATH,
                ["PlayerID", "Season", "Name", "Error"],
                shooting_failures,
            )
        history_path, inserted_rows, retained_rows = merge_season_rows(
            shooting_headers,
            shooting_rows,
            SHOOTING_SPLITS_HISTORY_PATH,
            legacy_path=LEGACY_SHOOTING_HISTORY_PATH,
            legacy_encoding="utf-8",
        )
        print(
            f"[OK] Shooting splits -> {SHOOTING_SPLITS_TMP_PATH.name} "
            f"({len(shooting_rows)} rows, {len(shooting_failures)} failures)"
        )
        print(
            f"[MERGED] Shooting splits -> {history_path.name} "
            f"({inserted_rows} refreshed rows, {retained_rows} retained history rows)"
        )
        if shooting_failures:
            print(f"[WARN] Shooting split failures -> {SHOOTING_SPLITS_FAILURES_PATH.name}")

    if not args.skip_dunk_scores:
        dunk_headers, dunk_rows = load_dunk_rows(
            url=args.dunk_url.strip() or dunk_score_url(args.season),
            season=args.season,
            retries=args.retries,
            pause=args.pause,
        )
        standing_headers, standing_rows = aggregate_dunk_rows(dunk_rows, standing_only=True)
        overall_headers, overall_rows = aggregate_dunk_rows(dunk_rows, standing_only=False)

        write_csv_dicts(DUNK_LEADERBOARD_TMP_PATH, dunk_headers, dunk_rows)
        write_csv_dicts(STANDING_DUNK_STATS_TMP_PATH, standing_headers, standing_rows)
        write_csv_dicts(OVERALL_DUNK_STATS_TMP_PATH, overall_headers, overall_rows)

        standing_history_path, standing_inserted, standing_retained = merge_season_rows(
            standing_headers,
            standing_rows,
            STANDING_DUNK_STATS_HISTORY_PATH,
            legacy_path=LEGACY_STANDING_DUNK_HISTORY_PATH,
            legacy_encoding="utf-8-sig",
        )
        overall_history_path, overall_inserted, overall_retained = merge_season_rows(
            overall_headers,
            overall_rows,
            OVERALL_DUNK_STATS_HISTORY_PATH,
            legacy_path=LEGACY_OVERALL_DUNK_HISTORY_PATH,
            legacy_encoding="utf-8",
            inject_legacy_season=args.legacy_overall_season,
            excluded_headers=OVERALL_DUNK_EXCLUDED_HEADERS,
        )

        print(f"[OK] Dunk leaderboard -> {DUNK_LEADERBOARD_TMP_PATH.name} ({len(dunk_rows)} raw dunks)")
        print(
            f"[MERGED] Standing dunk stats -> {standing_history_path.name} "
            f"({standing_inserted} refreshed rows, {standing_retained} retained history rows)"
        )
        print(
            f"[MERGED] Overall dunk stats -> {overall_history_path.name} "
            f"({overall_inserted} refreshed rows, {overall_retained} retained history rows)"
        )


if __name__ == "__main__":
    main()
