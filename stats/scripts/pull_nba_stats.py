from __future__ import annotations

import argparse
import csv
import gzip
import json
import ssl
import time
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

try:
    from urllib.parse import urlencode
    from urllib.request import Request, urlopen
except ImportError:
    from urllib import urlencode
    from urllib2 import Request, urlopen


STATS_BASE_URL = "https://stats.nba.com/stats"
SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
TMP_DIR = ROOT / "tmp"
HISTORY_DIR = ROOT / "history"
MANUAL_DIR = ROOT / "manual"
EXPORT_DIR = ROOT / "exports"
SSL_CONTEXT = ssl.create_default_context()
INSECURE_SSL_CONTEXT = ssl._create_unverified_context()

# Mirror the working browser-style headers already used elsewhere in the repo.
HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Encoding": "gzip, deflate",
    "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "Host": "stats.nba.com",
    "Origin": "https://www.nba.com",
    "Pragma": "no-cache",
    "Referer": "https://www.nba.com/",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-site",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/136.0.0.0 Safari/537.36"
    ),
    "x-nba-stats-origin": "stats",
    "x-nba-stats-token": "true",
    "sec-ch-ua": '"Chromium";v="136", "Google Chrome";v="136", "Not.A/Brand";v="99"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
}


def _decode_payload(payload: Any, content_encoding: str) -> str:
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


def _season_value(data: Dict[str, Any]) -> Optional[str]:
    params = data.get("parameters") or {}
    return params.get("Season") or params.get("SeasonYear")


def _coerce_headers(raw_headers: Any, row_width: Optional[int]) -> Optional[List[str]]:
    candidates: List[List[str]] = []

    if isinstance(raw_headers, list):
        if raw_headers and isinstance(raw_headers[0], str):
            candidates.append([str(x) for x in raw_headers])
        else:
            for candidate in raw_headers:
                if not isinstance(candidate, dict):
                    continue
                cols = candidate.get("columnNames") or candidate.get("columns")
                if isinstance(cols, list) and cols:
                    candidates.append([str(x) for x in cols])

    if not candidates:
        return None

    if row_width is not None:
        matching = [candidate for candidate in candidates if len(candidate) == row_width]
        if matching:
            return matching[-1]

    return candidates[-1]


def _extract_rows(
    data: Dict[str, Any],
    include_season: bool = True,
) -> Tuple[List[str], List[List[Any]]]:
    """
    Handles the common NBA Stats response shapes seen in this repo:
    - {"resultSets": [{"headers": [...], "rowSet": [...]}]}
    - {"resultSets": {"headers": [...], "rowSet": [...]}}
    - {"resultSets": {"headers": [{"columnNames": [...]}, ...], "rowSet": [...]}}
    """
    result_sets = data.get("resultSets") or data.get("resultSet")
    headers: Optional[List[str]] = None
    rows: Optional[List[List[Any]]] = None

    if isinstance(result_sets, list) and result_sets:
        first = result_sets[0]
        raw_rows = first.get("rowSet")
        if isinstance(raw_rows, list):
            rows = [list(row) for row in raw_rows]
        headers = _coerce_headers(first.get("headers"), len(rows[0]) if rows else None)

    elif isinstance(result_sets, dict):
        raw_rows = result_sets.get("rowSet")
        if isinstance(raw_rows, list):
            rows = [list(row) for row in raw_rows]
        headers = _coerce_headers(result_sets.get("headers"), len(rows[0]) if rows else None)

    if not headers or rows is None:
        raise ValueError("Could not extract headers/rows from NBA stats response.")

    season = _season_value(data)
    if include_season and season and "Season" not in headers:
        headers = ["Season", *headers]
        rows = [[season, *row] for row in rows]

    return headers, rows


def _result_set_label(result_set: Dict[str, Any], index: int) -> str:
    for key in ("name", "label", "groupName", "title"):
        value = result_set.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return f"Group {index + 1}"


def _extract_shot_location_wide_rows(
    data: Dict[str, Any],
    include_season: bool = True,
) -> Tuple[List[str], List[List[Any]]]:
    result_sets = data.get("resultSets") or data.get("resultSet")
    if isinstance(result_sets, dict):
        headers = result_sets.get("headers")
        row_set = result_sets.get("rowSet")
        if not isinstance(headers, list) or not isinstance(row_set, list):
            raise ValueError("Could not extract shot-location rows from NBA stats response.")

        shot_category_header = next(
            (
                item
                for item in headers
                if isinstance(item, dict) and item.get("name") == "SHOT_CATEGORY"
            ),
            None,
        )
        column_header = next(
            (
                item
                for item in headers
                if isinstance(item, dict) and item.get("name") == "columns"
            ),
            None,
        )
        if shot_category_header is None or column_header is None:
            raise ValueError("Shot-location response is missing category/column metadata.")

        category_names = shot_category_header.get("columnNames") or []
        raw_columns = column_header.get("columnNames") or []
        identity_count = int(shot_category_header.get("columnsToSkip") or 0)
        if not category_names or not raw_columns or identity_count <= 0:
            raise ValueError("Shot-location metadata is incomplete.")

        identity_columns = [str(column) for column in raw_columns[:identity_count]]
        metric_columns = [str(column) for column in raw_columns[identity_count:]]
        if len(metric_columns) % len(category_names) != 0:
            raise ValueError("Shot-location metrics do not align with the declared categories.")
        metrics_per_category = len(metric_columns) // len(category_names)

        output_headers = list(identity_columns)
        if include_season and "Season" not in output_headers:
            output_headers.insert(0, "Season")

        for category_index, category_name in enumerate(category_names):
            start = category_index * metrics_per_category
            end = start + metrics_per_category
            for metric_name in metric_columns[start:end]:
                output_headers.append(f"{category_name}_{metric_name}")

        season = _season_value(data) if include_season else None
        output_rows: List[List[Any]] = []
        for row in row_set:
            if not isinstance(row, list):
                continue
            output_row: List[Any] = []
            if include_season and "Season" not in identity_columns:
                output_row.append(season or "")
            output_row.extend(row[:identity_count])
            output_row.extend(row[identity_count:])
            output_rows.append(output_row)

        if not output_rows:
            raise ValueError("Shot-location response did not contain any usable rows.")
        return output_headers, output_rows

    if not isinstance(result_sets, list) or not result_sets:
        raise ValueError("Could not extract shot-location result sets from NBA stats response.")

    identity_candidates = [
        "Season",
        "PLAYER_ID",
        "PLAYER_NAME",
        "TEAM_ID",
        "TEAM_ABBREVIATION",
        "AGE",
        "NICKNAME",
    ]
    seen_labels: Dict[str, int] = {}
    output_metric_headers: List[str] = []
    merged_rows: Dict[Tuple[str, ...], Dict[str, Any]] = {}
    identity_columns: List[str] = []

    for index, result_set in enumerate(result_sets):
        raw_rows = result_set.get("rowSet")
        if not isinstance(raw_rows, list) or not raw_rows:
            continue

        rows = [list(row) for row in raw_rows]
        headers = _coerce_headers(result_set.get("headers"), len(rows[0]) if rows else None)
        if not headers:
            continue

        season = _season_value(data)
        if include_season and season and "Season" not in headers:
            headers = ["Season", *headers]
            rows = [[season, *row] for row in rows]

        if not identity_columns:
            identity_columns = [column for column in identity_candidates if column in headers]
        metric_columns = [column for column in headers if column not in identity_columns]
        if not metric_columns:
            continue

        label = _result_set_label(result_set, index)
        label_count = seen_labels.get(label, 0)
        seen_labels[label] = label_count + 1
        if label_count:
            label = f"{label} {label_count + 1}"

        prefixed_columns = [f"{label}_{column}" for column in metric_columns]
        output_metric_headers.extend(prefixed_columns)

        for row in rows:
            row_dict = dict(zip(headers, row))
            key = tuple(str(row_dict.get(column, "")).strip() for column in identity_columns)
            merged = merged_rows.setdefault(
                key,
                {column: row_dict.get(column, "") for column in identity_columns},
            )
            for metric_column, prefixed_column in zip(metric_columns, prefixed_columns):
                merged[prefixed_column] = row_dict.get(metric_column, "")

    if not identity_columns or not merged_rows:
        raise ValueError("Shot-location response did not contain any usable rows.")

    output_headers = [*identity_columns, *output_metric_headers]
    output_rows = [
        [row.get(header, "") for header in output_headers]
        for row in merged_rows.values()
    ]
    return output_headers, output_rows


def _write_csv(path: Path, headers: List[str], rows: List[List[Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(headers)
        writer.writerows(rows)


def _read_csv_dicts(path: Path) -> Tuple[List[str], List[Dict[str, str]]]:
    if not path.exists():
        return [], []
    with path.open("r", newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        headers = list(reader.fieldnames or [])
        rows = [dict(row) for row in reader]
    return headers, rows


def _write_csv_dicts(path: Path, headers: List[str], rows: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=headers, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({header: row.get(header, "") for header in headers})


def _merge_headers(preferred: List[str], existing: List[str]) -> List[str]:
    merged: List[str] = []
    seen = set()
    for header in [*preferred, *existing]:
        if header and header not in seen:
            merged.append(header)
            seen.add(header)
    return merged


def _row_key(row: Dict[str, str], keys: Tuple[str, ...]) -> Tuple[str, ...]:
    return tuple((row.get(key) or "").strip() for key in keys)


def _replace_seasons_in_history(
    tmp_path: Path,
    history_path: Path,
) -> Tuple[Path, int, int]:
    tmp_headers, tmp_rows = _read_csv_dicts(tmp_path)
    if not tmp_rows:
        raise ValueError(f"No rows available in {tmp_path.name}")
    if "Season" not in tmp_headers:
        raise ValueError(f"{tmp_path.name} has no Season column for seasonal merge")

    old_headers, old_rows = _read_csv_dicts(history_path)
    seasons = [row.get("Season", "") for row in tmp_rows if row.get("Season", "")]
    season_set = set(seasons)
    kept_old = [row for row in old_rows if row.get("Season", "") not in season_set]
    headers = _merge_headers(tmp_headers, old_headers)
    merged_rows = [*tmp_rows, *kept_old]
    _write_csv_dicts(history_path, headers, merged_rows)
    return history_path, len(tmp_rows), len(kept_old)


def _replace_keys_in_history(
    tmp_path: Path,
    history_path: Path,
    merge_keys: Tuple[str, ...],
) -> Tuple[Path, int, int]:
    tmp_headers, tmp_rows = _read_csv_dicts(tmp_path)
    if not tmp_rows:
        raise ValueError(f"No rows available in {tmp_path.name}")

    old_headers, old_rows = _read_csv_dicts(history_path)
    tmp_keys = {_row_key(row, merge_keys) for row in tmp_rows}
    kept_old = [row for row in old_rows if _row_key(row, merge_keys) not in tmp_keys]
    headers = _merge_headers(tmp_headers, old_headers)
    merged_rows = [*tmp_rows, *kept_old]
    _write_csv_dicts(history_path, headers, merged_rows)
    return history_path, len(tmp_rows), len(kept_old)


def _filter_min_gp(headers: List[str], rows: List[List[Any]], min_gp: int) -> Tuple[List[str], List[List[Any]]]:
    if "GP" not in headers:
        return headers, rows
    gp_idx = headers.index("GP")
    kept: List[List[Any]] = []
    for row in rows:
        try:
            gp = int(float(row[gp_idx]))
        except Exception:
            continue
        if gp >= min_gp:
            kept.append(row)
    return headers, kept


def _copy_with_new_name(
    headers: List[str],
    rows: List[List[Any]],
    output_name: str,
) -> Tuple[List[str], List[List[Any]], str]:
    return headers, rows, output_name


Postprocess = Callable[
    [List[str], List[List[Any]]],
    Optional[Tuple[List[str], List[List[Any]], str]],
]


@dataclass(frozen=True)
class Job:
    name: str
    endpoint: str
    output_name: str
    params: Dict[str, Any]
    postprocess: Optional[Postprocess] = None
    include_season: bool = True
    history_name: Optional[str] = None
    merge_strategy: str = "season_replace"
    merge_keys: Tuple[str, ...] = ()
    value_mode: str = "per_game"
    wide_multi_result: bool = False


def general_params(season: str, measure_type: str, per_mode: str) -> Dict[str, Any]:
    return {
        "College": "",
        "Conference": "",
        "Country": "",
        "DateFrom": "",
        "DateTo": "",
        "Division": "",
        "DraftPick": "",
        "DraftYear": "",
        "GameScope": "",
        "GameSegment": "",
        "Height": "",
        "LastNGames": 0,
        "LeagueID": "00",
        "Location": "",
        "MeasureType": measure_type,
        "Month": 0,
        "OpponentTeamID": 0,
        "Outcome": "",
        "PORound": 0,
        "PaceAdjust": "N",
        "PerMode": per_mode,
        "Period": 0,
        "PlayerExperience": "",
        "PlayerPosition": "",
        "PlusMinus": "N",
        "Rank": "N",
        "Season": season,
        "SeasonSegment": "",
        "SeasonType": "Regular Season",
        "ShotClockRange": "",
        "StarterBench": "",
        "TeamID": 0,
        "TwoWay": 0,
        "VsConference": "",
        "VsDivision": "",
        "Weight": "",
    }


def tracking_params(season: str, pt_measure_type: str, per_mode: str = "PerGame") -> Dict[str, Any]:
    return {
        "College": "",
        "Conference": "",
        "Country": "",
        "DateFrom": "",
        "DateTo": "",
        "Division": "",
        "DraftPick": "",
        "DraftYear": "",
        "GameScope": "",
        "GameSegment": "",
        "Height": "",
        "LastNGames": 0,
        "LeagueID": "00",
        "Location": "",
        "Month": 0,
        "OpponentTeamID": 0,
        "Outcome": "",
        "PORound": 0,
        "PerMode": per_mode,
        "PlayerExperience": "",
        "PlayerOrTeam": "Player",
        "PlayerPosition": "",
        "PtMeasureType": pt_measure_type,
        "Season": season,
        "SeasonSegment": "",
        "SeasonType": "Regular Season",
        "StarterBench": "",
        "TeamID": 0,
        "VsConference": "",
        "VsDivision": "",
        "Weight": "",
    }


def shot_dashboard_params(
    season: str,
    close_def_dist_range: str,
    per_mode: str = "Totals",
) -> Dict[str, Any]:
    return {
        "CloseDefDistRange": close_def_dist_range,
        "College": "",
        "Conference": "",
        "Country": "",
        "DateFrom": "",
        "DateTo": "",
        "Division": "",
        "DribbleRange": "",
        "DraftPick": "",
        "DraftYear": "",
        "GameScope": "",
        "GameSegment": "",
        "GeneralRange": "",
        "Height": "",
        "LastNGames": 0,
        "LeagueID": "00",
        "Location": "",
        "Month": 0,
        "OpponentTeamID": 0,
        "Outcome": "",
        "PORound": 0,
        "PaceAdjust": "N",
        "PerMode": per_mode,
        "Period": 0,
        "PlayerExperience": "",
        "PlayerPosition": "",
        "PlusMinus": "N",
        "Rank": "N",
        "Season": season,
        "SeasonSegment": "",
        "SeasonType": "Regular Season",
        "ShotClockRange": "",
        "TeamID": 0,
        "TouchTimeRange": "",
        "VsConference": "",
        "VsDivision": "",
        "Weight": "",
    }


def shot_locations_params(season: str, distance_range: str) -> Dict[str, Any]:
    return {
        "College": "",
        "Conference": "",
        "Country": "",
        "DateFrom": "",
        "DateTo": "",
        "DistanceRange": distance_range,
        "Division": "",
        "DraftPick": "",
        "DraftYear": "",
        "GameScope": "",
        "GameSegment": "",
        "Height": "",
        "LastNGames": 0,
        "LeagueID": "00",
        "Location": "",
        "Month": 0,
        "OpponentTeamID": 0,
        "Outcome": "",
        "PORound": 0,
        "PerMode": "PerGame",
        "Period": 0,
        "PlayerExperience": "",
        "PlayerPosition": "",
        "Season": season,
        "SeasonSegment": "",
        "SeasonType": "Regular Season",
        "TeamID": 0,
        "VsConference": "",
        "VsDivision": "",
        "Weight": "",
    }


def synergy_params(season: str, play_type: str) -> Dict[str, Any]:
    return {
        "LeagueID": "00",
        "SeasonYear": season,
        "SeasonType": "Regular Season",
        "PerMode": "PerGame",
        "PlayerOrTeam": "P",
        "PlayType": play_type,
        "TypeGrouping": "offensive",
    }


def draft_combine_params(season: str) -> Dict[str, Any]:
    return {
        "LeagueID": "00",
        "SeasonYear": season,
    }


def defense_dashboard_params(season: str, defense_category: str) -> Dict[str, Any]:
    return {
        "College": "",
        "Conference": "",
        "Country": "",
        "DateFrom": "",
        "DateTo": "",
        "DefenseCategory": defense_category,
        "Division": "",
        "DraftPick": "",
        "DraftYear": "",
        "Height": "",
        "LastNGames": 0,
        "LeagueID": "00",
        "Month": 0,
        "OpponentTeamID": 0,
        "PerMode": "PerGame",
        "PlayerExperience": "",
        "PlayerPosition": "",
        "Season": season,
        "SeasonSegment": "",
        "SeasonType": "Regular Season",
        "StarterBench": "",
        "TeamID": 0,
        "VsConference": "",
        "VsDivision": "",
        "Weight": "",
    }


def hustle_params(season: str) -> Dict[str, Any]:
    return {
        "College": "",
        "Conference": "",
        "Country": "",
        "DateFrom": "",
        "DateTo": "",
        "Division": "",
        "DraftPick": "",
        "DraftYear": "",
        "GameScope": "",
        "Height": "",
        "LastNGames": 0,
        "LeagueID": "00",
        "Location": "",
        "Month": 0,
        "OpponentTeamID": 0,
        "Outcome": "",
        "PORound": 0,
        "PerMode": "PerGame",
        "PlayerExperience": "",
        "PlayerPosition": "",
        "Season": season,
        "SeasonSegment": "",
        "SeasonType": "Regular Season",
        "StarterBench": "",
        "TeamID": 0,
        "VsConference": "",
        "VsDivision": "",
        "Weight": "",
    }


def bios_params(season: str) -> Dict[str, Any]:
    return {
        "College": "",
        "Conference": "",
        "Country": "",
        "DateFrom": "",
        "DateTo": "",
        "Division": "",
        "DraftPick": "",
        "DraftYear": "",
        "GameScope": "",
        "Height": "",
        "LastNGames": 0,
        "LeagueID": "00",
        "Location": "",
        "Month": 0,
        "OpponentTeamID": 0,
        "Outcome": "",
        "PORound": 0,
        "PerMode": "PerGame",
        "PlayerExperience": "",
        "PlayerPosition": "",
        "Season": season,
        "SeasonSegment": "",
        "SeasonType": "Regular Season",
        "StarterBench": "",
        "TeamID": 0,
        "VsConference": "",
        "VsDivision": "",
        "Weight": "",
    }


def build_jobs(season: str) -> List[Job]:
    jobs: List[Job] = [
        Job("bios", "leaguedashplayerbiostats", "bios_tmp.csv", bios_params(season)),
        Job(
            "general_traditional",
            "leaguedashplayerstats",
            "general_traditional_tmp.csv",
            general_params(season, "Base", "PerGame"),
        ),
        Job(
            "general_advanced",
            "leaguedashplayerstats",
            "general_advanced_tmp.csv",
            general_params(season, "Advanced", "PerGame"),
        ),
        Job(
            "general_scoring",
            "leaguedashplayerstats",
            "general_scoring_tmp.csv",
            general_params(season, "Scoring", "PerGame"),
        ),
        Job(
            "general_defense",
            "leaguedashplayerstats",
            "general_defense_tmp.csv",
            general_params(season, "Defense", "PerGame"),
        ),
        Job(
            "general_traditional_per100",
            "leaguedashplayerstats",
            "general_traditional_per100_tmp.csv",
            general_params(season, "Base", "Per100Possessions"),
            value_mode="per_100_possessions",
        ),
        Job(
            "general_advanced_per100",
            "leaguedashplayerstats",
            "general_advanced_per100_tmp.csv",
            general_params(season, "Advanced", "Per100Possessions"),
            value_mode="per_100_possessions",
        ),
        Job(
            "general_per1poss",
            "leaguedashplayerstats",
            "general_per1poss_tmp.csv",
            general_params(season, "Base", "PerPossession"),
            value_mode="per_possession",
        ),
        Job("hustle", "leaguehustlestatsplayer", "hustle_tmp.csv", hustle_params(season)),
        Job(
            "boxout",
            "leaguehustlestatsplayer",
            "boxout_tmp.csv",
            hustle_params(season),
            merge_strategy="skip",
        ),
        Job(
            "tracking_speed",
            "leaguedashptstats",
            "tracking_speed_tmp.csv",
            tracking_params(season, "SpeedDistance"),
        ),
        Job(
            "tracking_touches",
            "leaguedashptstats",
            "tracking_touches_tmp.csv",
            tracking_params(season, "Possessions"),
        ),
        Job(
            "tracking_passing",
            "leaguedashptstats",
            "tracking_passing_tmp.csv",
            tracking_params(season, "Passing"),
        ),
        Job(
            "tracking_rebound",
            "leaguedashptstats",
            "tracking_rebound_tmp.csv",
            tracking_params(season, "Rebounding"),
        ),
        Job(
            "tracking_rebounding_alias",
            "leaguedashptstats",
            "tracking_rebounding_tmp.csv",
            tracking_params(season, "Rebounding"),
            merge_strategy="skip",
        ),
        Job(
            "tracking_drives",
            "leaguedashptstats",
            "tracking_drives_tmp.csv",
            tracking_params(season, "Drives"),
        ),
        Job(
            "tracking_postup",
            "leaguedashptstats",
            "tracking_postup_tmp.csv",
            tracking_params(season, "PostTouch"),
        ),
        Job(
            "tracking_defensive_impact",
            "leaguedashptstats",
            "tracking_defensive_impact_tmp.csv",
            tracking_params(season, "Defense"),
        ),
        Job(
            "tracking_catch_shoot",
            "leaguedashptstats",
            "tracking_c&s_tmp.csv",
            tracking_params(season, "CatchShoot", per_mode="Totals"),
            value_mode="totals",
        ),
        Job(
            "pullup",
            "leaguedashptstats",
            "pullup_tmp.csv",
            tracking_params(season, "PullUpShot"),
        ),
        Job(
            "playtype_transition",
            "synergyplaytypes",
            "playtype_transition_tmp.csv",
            synergy_params(season, "Transition"),
        ),
        Job(
            "playtype_spotup",
            "synergyplaytypes",
            "playtype_spotup_tmp.csv",
            synergy_params(season, "Spotup"),
        ),
        Job(
            "spotup_alias",
            "synergyplaytypes",
            "spotup_tmp.csv",
            synergy_params(season, "Spotup"),
        ),
        Job(
            "playtype_postup",
            "synergyplaytypes",
            "playtype_postup_tmp.csv",
            synergy_params(season, "Postup"),
        ),
        Job(
            "playtype_offscreen",
            "synergyplaytypes",
            "playtype_offscreen_tmp.csv",
            synergy_params(season, "OffScreen"),
        ),
        Job(
            "off_screen_alias",
            "synergyplaytypes",
            "off_screen_tmp.csv",
            synergy_params(season, "OffScreen"),
        ),
        Job(
            "playtype_pnr_handler",
            "synergyplaytypes",
            "playtype_pnr_handler_tmp.csv",
            synergy_params(season, "PRBallHandler"),
        ),
        Job(
            "playtype_pnr_rollman",
            "synergyplaytypes",
            "playtype_pnr_rollman_tmp.csv",
            synergy_params(season, "PRRollman"),
        ),
        Job(
            "playtype_handoff",
            "synergyplaytypes",
            "playtype_handoff_tmp.csv",
            synergy_params(season, "Handoff"),
        ),
        Job(
            "playtype_cut",
            "synergyplaytypes",
            "playtype_cut_tmp.csv",
            synergy_params(season, "Cut"),
        ),
        Job(
            "playtype_iso",
            "synergyplaytypes",
            "playtype_iso_tmp.csv",
            synergy_params(season, "Isolation"),
        ),
        Job(
            "wide_open",
            "leaguedashplayerptshot",
            "wide_open_tmp.csv",
            shot_dashboard_params(season, "6+ Feet - Wide Open"),
            value_mode="totals",
        ),
        Job(
            "closest_defender",
            "leaguedashplayerptshot",
            "closest_defender_tmp.csv",
            shot_dashboard_params(season, "2-4 Feet - Tight"),
            value_mode="totals",
        ),
        Job(
            "very_tight",
            "leaguedashplayerptshot",
            "very_tight_tmp.csv",
            shot_dashboard_params(season, "0-2 Feet - Very Tight"),
            value_mode="totals",
        ),
        Job(
            "shooting_zone",
            "leaguedashplayershotlocations",
            "shooting_zone_tmp.csv",
            shot_locations_params(season, "By Zone"),
        ),
        Job(
            "shot_locations_by_zone",
            "leaguedashplayershotlocations",
            "shot_locations_by_zone_tmp.csv",
            shot_locations_params(season, "By Zone"),
            history_name="shot_locations_by_zone.csv",
            wide_multi_result=True,
        ),
        Job(
            "shooting_5ft",
            "leaguedashplayershotlocations",
            "shooting_5ft_tmp.csv",
            shot_locations_params(season, "5ft Range"),
        ),
        Job(
            "shooting_8ft",
            "leaguedashplayershotlocations",
            "shooting_8ft_tmp.csv",
            shot_locations_params(season, "8ft Range"),
        ),
        Job(
            "shot_locations_by_distance_8ft",
            "leaguedashplayershotlocations",
            "shot_locations_by_distance_8ft_tmp.csv",
            shot_locations_params(season, "8ft Range"),
            history_name="shot_locations_by_distance_8ft.csv",
            wide_multi_result=True,
        ),
        Job(
            "draft",
            "draftcombinedrillresults",
            "draft_tmp.csv",
            draft_combine_params(season),
            include_season=False,
            merge_strategy="key_replace",
            merge_keys=("PLAYER_ID",),
            value_mode="draft_combine",
        ),
        Job(
            "defense_dashboard_3",
            "leaguedashptdefend",
            "defense_dashboard_3_tmp.csv",
            defense_dashboard_params(season, "3 Pointers"),
        ),
        Job(
            "defense_dashboard_l6ft",
            "leaguedashptdefend",
            "defense_dashboard_l6ft_tmp.csv",
            defense_dashboard_params(season, "Less Than 6Ft"),
        ),
        Job(
            "defense_dashboard_l6ft_m30gp",
            "leaguedashptdefend",
            "defense_dashboard_l6ft_m30gp_tmp.csv",
            defense_dashboard_params(season, "Less Than 6Ft"),
            postprocess=lambda headers, rows: (
                *_filter_min_gp(headers, rows, min_gp=30),
                "defense_dashboard_l6ft_m30gp_tmp.csv",
            ),
        ),
    ]
    return jobs


def fetch_json(endpoint: str, params: Dict[str, Any], retries: int, pause: float) -> Dict[str, Any]:
    query = urlencode(params)
    url = f"{STATS_BASE_URL}/{endpoint}?{query}"
    last_error: Optional[Exception] = None

    for attempt in range(1, retries + 1):
        try:
            request = Request(url, headers=HEADERS)
            try:
                with urlopen(request, timeout=45, context=SSL_CONTEXT) as response:
                    payload = response.read()
                    content_encoding = response.info().get("Content-Encoding", "")
            except Exception as exc:
                msg = str(exc)
                if "CERTIFICATE_VERIFY_FAILED" not in msg:
                    raise
                print("[WARN] SSL verification failed, retrying without certificate validation.")
                with urlopen(request, timeout=45, context=INSECURE_SSL_CONTEXT) as response:
                    payload = response.read()
                    content_encoding = response.info().get("Content-Encoding", "")
            return json.loads(_decode_payload(payload, content_encoding))
        except Exception as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(pause * attempt)

    raise RuntimeError(f"Failed to fetch {endpoint}") from last_error


def run_job(job: Job, retries: int, pause: float) -> Path:
    data = fetch_json(job.endpoint, job.params, retries=retries, pause=pause)
    if job.wide_multi_result:
        headers, rows = _extract_shot_location_wide_rows(
            data,
            include_season=job.include_season,
        )
    else:
        headers, rows = _extract_rows(data, include_season=job.include_season)

    output_name = job.output_name
    if job.postprocess is not None:
        result = job.postprocess(headers, rows)
        if result is not None:
            headers, rows, output_name = result

    output_path = TMP_DIR / output_name
    _write_csv(output_path, headers, rows)
    return output_path


def _history_path_for_job(job: Job, output_path: Path) -> Optional[Path]:
    if job.merge_strategy == "skip":
        return None
    history_name = job.history_name or output_path.name.replace("_tmp.csv", ".csv")
    return HISTORY_DIR / history_name


def merge_job_output(job: Job, output_path: Path) -> Optional[Tuple[Path, int, int]]:
    history_path = _history_path_for_job(job, output_path)
    if history_path is None:
        return None

    if job.merge_strategy == "season_replace":
        return _replace_seasons_in_history(output_path, history_path)

    if job.merge_strategy == "key_replace":
        if not job.merge_keys:
            raise ValueError(f"{job.name} uses key_replace but has no merge_keys configured")
        return _replace_keys_in_history(output_path, history_path, job.merge_keys)

    raise ValueError(f"Unknown merge strategy for {job.name}: {job.merge_strategy}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Pull NBA.com stats directly into stats/tmp/*_tmp.csv files."
    )
    parser.add_argument(
        "--season",
        default="2025-26",
        help="Season string used by NBA stats, e.g. 2025-26",
    )
    parser.add_argument(
        "--jobs",
        nargs="*",
        default=["all"],
        help="One or more job names. Use 'all' to run every configured job.",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List configured jobs and exit.",
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
        help="Base pause between retries.",
    )
    parser.add_argument(
        "--merge",
        action="store_true",
        help="Merge refreshed stats/tmp/*_tmp.csv files back into stats/history/*.csv.",
    )
    parser.add_argument(
        "--merge-only",
        action="store_true",
        help="Skip fetching and only merge the existing *_tmp.csv files.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.merge_only and not args.merge:
        raise SystemExit("--merge-only requires --merge")

    jobs = build_jobs(args.season)
    by_name = {job.name: job for job in jobs}

    if args.list:
        for name in by_name:
            print(name)
        return

    selected_names = list(by_name.keys()) if "all" in args.jobs else args.jobs

    missing = [name for name in selected_names if name not in by_name]
    if missing:
        raise SystemExit(f"Unknown jobs: {', '.join(missing)}")

    for name in selected_names:
        job = by_name[name]
        if args.merge_only:
            output_path = TMP_DIR / job.output_name
            if not output_path.exists():
                raise SystemExit(f"Missing tmp file for merge-only run: {output_path.name}")
        else:
            output_path = run_job(job, retries=args.retries, pause=args.pause)
            print(f"[OK] {name} -> {output_path.name}")

        if args.merge:
            merge_result = merge_job_output(job, output_path)
            if merge_result is not None:
                history_path, inserted_rows, retained_rows = merge_result
                print(
                    f"[MERGED] {name} -> {history_path.name} "
                    f"({inserted_rows} refreshed rows, {retained_rows} retained history rows)"
                )

    print(
        "\nConfigured jobs now cover the current player-level NBA.com pulls used in "
        "the stats sheet flow. Tmp files now live in stats/tmp, merged history lives "
        "in stats/history, and legacy files like clutch, come_back, shot_locations, "
        "and team_tmp are intentionally left out."
    )


if __name__ == "__main__":
    main()
