from __future__ import annotations

import argparse
import csv
import re
import statistics
import unicodedata
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from pull_nba_stats import EXPORT_DIR, HISTORY_DIR, MANUAL_DIR


WORKBOOK_NS = {
    "main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "rel": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "pkgrel": "http://schemas.openxmlformats.org/package/2006/relationships",
}

PLAYER_NAME_FIXES = {
    "jakob poltl": "jakob poeltl",
    "kenyon martin jr": "kenyon martin",
    "reggie bullock jr": "reggie bullock",
    "xavier tillman sr": "xavier tillman",
    "kj martin": "kenyon martin",
    "brandon boston jr": "brandon boston",
    "carlton carrington": "bub carrington",
    "vince edwards": "vincent edwards",
    "rj nembhard jr": "ruben nembhard jr",
    "jeenathan williams": "nate williams",
}

PENALTY_ROLES = {"Too Few Games", "Garbage Time"}
DEFAULT_Z_SCORE_CAP = 3.0


@dataclass
class CalUniverseRow:
    nba_id: str
    season: str
    player: str
    rotation_role: str
    minutes: Optional[float]


def first_present_column(
    available_columns: Iterable[str],
    candidates: Iterable[str],
) -> str:
    column_set = {str(column).strip() for column in available_columns}
    for candidate in candidates:
        if candidate in column_set:
            return candidate
    return ""


def canonical_id(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        return str(int(float(text)))
    except Exception:
        return text


def normalize_name(name: object) -> str:
    text = str(name or "").strip()
    if not text:
        return ""

    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.replace("'", "").replace(".", "")
    text = re.sub(r"\bIII\b", "", text)
    text = re.sub(r"\s+", " ", text).strip().lower()
    return PLAYER_NAME_FIXES.get(text, text)


def parse_float(value: object) -> Optional[float]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return float(text)
    except Exception:
        return None


def clamp_float(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def cap_z_score(
    value: Optional[float],
    z_score_cap: float = DEFAULT_Z_SCORE_CAP,
) -> Optional[float]:
    if value is None or z_score_cap <= 0:
        return value
    return clamp_float(value, -z_score_cap, z_score_cap)


def compute_capped_z_score(
    raw_value: Optional[float],
    mean_value: float,
    stdev_value: float,
    z_score_cap: float = DEFAULT_Z_SCORE_CAP,
) -> Optional[float]:
    if raw_value is None:
        return None
    return cap_z_score((raw_value - mean_value) / stdev_value, z_score_cap=z_score_cap)


def read_history_csv(path: Path) -> List[Dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8") as fh:
        return [dict(row) for row in csv.DictReader(fh)]


def resolve_live_minutes(
    row: Optional[Dict[str, str]],
    minutes_column: str,
    minutes_games_column: str,
) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    if row is None:
        return None, None, None

    minutes_per_game = parse_float(row.get(minutes_column, ""))
    games_played = parse_float(row.get(minutes_games_column, "")) if minutes_games_column else None
    if minutes_per_game is None:
        return None, None, games_played
    if games_played is None:
        return minutes_per_game, minutes_per_game, None
    return minutes_per_game * games_played, minutes_per_game, games_played


def season_sort_key(season: str) -> Tuple[int, str]:
    match = re.match(r"(\d{4})", str(season or "").strip())
    if match:
        return int(match.group(1)), str(season)
    return -1, str(season)


def load_shared_strings(zf: zipfile.ZipFile) -> List[str]:
    if "xl/sharedStrings.xml" not in zf.namelist():
        return []

    root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
    shared: List[str] = []
    for si in root.findall("main:si", WORKBOOK_NS):
        parts = []
        for text_node in si.iterfind(".//main:t", WORKBOOK_NS):
            parts.append(text_node.text or "")
        shared.append("".join(parts))
    return shared


def workbook_sheet_targets(zf: zipfile.ZipFile) -> Dict[str, str]:
    workbook = ET.fromstring(zf.read("xl/workbook.xml"))
    rels = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
    rel_map = {
        rel.attrib["Id"]: rel.attrib["Target"]
        for rel in rels.findall("pkgrel:Relationship", WORKBOOK_NS)
    }

    targets: Dict[str, str] = {}
    for sheet in workbook.find("main:sheets", WORKBOOK_NS):
        rel_id = sheet.attrib.get("{%s}id" % WORKBOOK_NS["rel"])
        target = rel_map.get(rel_id, "")
        if target:
            targets[sheet.attrib["name"]] = f"xl/{target}"
    return targets


def read_sheet_cells(
    workbook_path: Path,
    sheet_name: str,
    wanted_columns: Iterable[str],
) -> Dict[int, Dict[str, str]]:
    wanted = set(wanted_columns)

    with zipfile.ZipFile(workbook_path) as zf:
        shared = load_shared_strings(zf)
        targets = workbook_sheet_targets(zf)
        if sheet_name not in targets:
            raise SystemExit(f"Sheet not found in workbook: {sheet_name}")

        root = ET.fromstring(zf.read(targets[sheet_name]))
        rows = root.find("main:sheetData", WORKBOOK_NS)
        data: Dict[int, Dict[str, str]] = {}

        for row in rows.findall("main:row", WORKBOOK_NS):
            row_number = int(row.attrib["r"])
            current: Dict[str, str] = {}

            for cell in row.findall("main:c", WORKBOOK_NS):
                ref = cell.attrib.get("r", "")
                match = re.match(r"([A-Z]+)(\d+)", ref)
                if not match:
                    continue
                column = match.group(1)
                if column not in wanted:
                    continue

                value = ""
                cell_type = cell.attrib.get("t", "")
                formula = cell.find("main:f", WORKBOOK_NS)
                scalar = cell.find("main:v", WORKBOOK_NS)

                if scalar is not None:
                    raw = scalar.text or ""
                    if cell_type == "s":
                        try:
                            value = shared[int(raw)]
                        except Exception:
                            value = raw
                    else:
                        value = raw
                elif formula is not None and formula.text:
                    value = "=" + formula.text

                if value != "":
                    current[column] = value

            if current:
                data[row_number] = current

    return data


def load_cal_universe(workbook_path: Path, sheet_name: str = "Cal") -> List[CalUniverseRow]:
    cells = read_sheet_cells(workbook_path, sheet_name, ["A", "B", "C", "D", "E"])
    rows: List[CalUniverseRow] = []

    for row_number in sorted(cells):
        if row_number == 1:
            continue
        row = cells[row_number]
        season = str(row.get("B", "")).strip()
        player = str(row.get("C", "")).strip()
        if not season or not player:
            continue

        rows.append(
            CalUniverseRow(
                nba_id=canonical_id(row.get("A", "")),
                season=season,
                player=player,
                rotation_role=str(row.get("D", "")).strip(),
                minutes=parse_float(row.get("E", "")),
            )
        )

    return rows


def load_universe_csv(path: Path) -> List[CalUniverseRow]:
    rows = read_history_csv(path)
    if not rows:
        raise SystemExit(f"No rows found in universe CSV: {path.name}")

    columns = list(rows[0].keys())
    id_column = first_present_column(
        columns,
        ["NBA_ID", "nba_id", "PLAYER_ID", "Player_ID", "Unnamed: 0", ""],
    )
    season_column = first_present_column(columns, ["Season", "season"])
    player_column = first_present_column(columns, ["Player", "PLAYER_NAME", "player"])
    role_column = first_present_column(
        columns,
        ["RotationRole", "rotation_role", "Role", "role"],
    )
    minutes_column = first_present_column(columns, ["MIN", "Minutes", "minutes"])

    if not season_column:
        raise SystemExit(f"Season column not found in universe CSV: {path.name}")
    if not player_column:
        raise SystemExit(f"Player column not found in universe CSV: {path.name}")

    universe_rows: List[CalUniverseRow] = []
    for row in rows:
        season = str(row.get(season_column, "")).strip()
        player = str(row.get(player_column, "")).strip()
        if not season or not player:
            continue

        universe_rows.append(
            CalUniverseRow(
                nba_id=canonical_id(row.get(id_column, "")) if id_column else "",
                season=season,
                player=player,
                rotation_role=str(row.get(role_column, "")).strip() if role_column else "",
                minutes=parse_float(row.get(minutes_column, "")) if minutes_column else None,
            )
        )

    return universe_rows


def resolve_details_csv_path(details_csv: str, universe_path: Path) -> Optional[Path]:
    if details_csv:
        return Path(details_csv)
    return None


def build_universe_index(
    rows: List[CalUniverseRow],
) -> Tuple[Dict[Tuple[str, str], CalUniverseRow], Dict[Tuple[str, str], CalUniverseRow]]:
    by_id: Dict[Tuple[str, str], CalUniverseRow] = {}
    by_name: Dict[Tuple[str, str], CalUniverseRow] = {}

    for row in rows:
        if row.nba_id:
            by_id[(row.season, row.nba_id)] = row
        by_name[(row.season, normalize_name(row.player))] = row

    return by_id, by_name


def enrich_universe_rows(
    universe_rows: List[CalUniverseRow],
    detail_rows: List[CalUniverseRow],
) -> List[CalUniverseRow]:
    detail_by_id, detail_by_name = build_universe_index(detail_rows)
    enriched_rows: List[CalUniverseRow] = []

    for universe_row in universe_rows:
        detail_row = None

        key = (universe_row.season, normalize_name(universe_row.player))
        if key in detail_by_name:
            detail_row = detail_by_name[key]
        elif universe_row.nba_id:
            key = (universe_row.season, universe_row.nba_id)
            detail_row = detail_by_id.get(key)

        enriched_rows.append(
            CalUniverseRow(
                nba_id=universe_row.nba_id or (detail_row.nba_id if detail_row else ""),
                season=universe_row.season,
                player=universe_row.player,
                rotation_role=universe_row.rotation_role
                or (detail_row.rotation_role if detail_row else ""),
                minutes=universe_row.minutes
                if universe_row.minutes is not None
                else (detail_row.minutes if detail_row else None),
            )
        )

    return enriched_rows


def export_universe_csv(path: Path, universe_rows: List[CalUniverseRow]) -> None:
    write_csv(
        path,
        ["NBA_ID", "Season", "Player", "RotationRole", "MIN"],
        [
            {
                "NBA_ID": row.nba_id,
                "Season": row.season,
                "Player": row.player,
                "RotationRole": row.rotation_role,
                "MIN": row.minutes,
            }
            for row in universe_rows
        ],
    )


def build_source_index(
    rows: List[Dict[str, str]],
) -> Tuple[Dict[Tuple[str, str], Dict[str, str]], Dict[Tuple[str, str], Dict[str, str]]]:
    by_id: Dict[Tuple[str, str], Dict[str, str]] = {}
    by_name: Dict[Tuple[str, str], Dict[str, str]] = {}

    for row in rows:
        season = str(row.get("Season", "")).strip()
        if not season:
            continue

        player_id = canonical_id(row.get("PLAYER_ID", ""))
        player_name = normalize_name(row.get("PLAYER_NAME", ""))

        if player_id:
            by_id[(season, player_id)] = row
        if player_name:
            by_name[(season, player_name)] = row

    return by_id, by_name


def detect_current_season(
    explicit_current_season: str,
    universe_rows: List[CalUniverseRow],
    source_rows: List[Dict[str, str]],
) -> str:
    if explicit_current_season:
        return explicit_current_season.strip()

    seasons = {row.season for row in universe_rows if row.season}
    seasons.update(str(row.get("Season", "")).strip() for row in source_rows if row.get("Season"))
    seasons.discard("")
    if not seasons:
        return ""
    return max(seasons, key=season_sort_key)


def build_sheet_input_rows(
    source_rows: List[Dict[str, str]],
    metric_column: str,
) -> List[Dict[str, object]]:
    sheet_rows: List[Dict[str, object]] = []
    for row in source_rows:
        season = str(row.get("Season", "")).strip()
        player = str(row.get("PLAYER_NAME", "")).strip()
        raw_value = parse_float(row.get(metric_column, ""))
        if not season or not player or raw_value is None:
            continue
        sheet_rows.append(
            {
                "Season": season,
                "Player": player,
                metric_column: raw_value,
            }
        )
    return sheet_rows


def match_metric_row(
    universe_row: CalUniverseRow,
    by_id: Dict[Tuple[str, str], Dict[str, str]],
    by_name: Dict[Tuple[str, str], Dict[str, str]],
    allow_id_fallback: bool,
) -> Tuple[Optional[Dict[str, str]], str]:
    key = (universe_row.season, normalize_name(universe_row.player))
    if key in by_name:
        return by_name[key], "season+player"

    if allow_id_fallback and universe_row.nba_id:
        key = (universe_row.season, universe_row.nba_id)
        if key in by_id:
            return by_id[key], "season+id-fallback"

    return None, ""


def apply_cal_normalization(
    season: str,
    rotation_role: str,
    minutes: Optional[float],
    raw_z: Optional[float],
    current_season: str,
    current_season_min_threshold: float,
    standard_min_threshold: float,
) -> Optional[float]:
    capped_raw_z = cap_z_score(raw_z)
    if capped_raw_z is None:
        return None

    mins = minutes or 0.0
    threshold = (
        current_season_min_threshold
        if current_season and season == current_season
        else standard_min_threshold
    )

    if rotation_role not in PENALTY_ROLES or mins >= threshold:
        return capped_raw_z

    if capped_raw_z > 0:
        return cap_z_score(capped_raw_z * mins / 1000.0)
    return cap_z_score(capped_raw_z - 1.0)


def inverse_or_blank(value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    return -value


def write_csv(path: Path, headers: List[str], rows: List[Dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=headers, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build one DataPull -> Cal lane from stats history using a player-universe CSV."
    )
    parser.add_argument(
        "--universe-csv",
        default=str(MANUAL_DIR / "playerlist.csv"),
        help="CSV that defines the player universe. Expected core columns: Season, Player, and optional nba_id/NBA_ID/PLAYER_ID.",
    )
    parser.add_argument(
        "--details-csv",
        default="",
        help=(
            "Optional CSV used to backfill RotationRole and MIN. "
            "Pass stats/manual/player_universe.csv explicitly if you want that enrichment."
        ),
    )
    parser.add_argument(
        "--workbook",
        default=str(MANUAL_DIR / "2k26_Temp_for_codex.xlsx"),
        help=(
            "Optional workbook used only to backfill RotationRole and MIN "
            "when the universe/details CSVs do not contain them."
        ),
    )
    parser.add_argument(
        "--sheet",
        default="Cal",
        help="Workbook sheet used for optional RotationRole and MIN backfill. Defaults to Cal.",
    )
    parser.add_argument(
        "--source",
        default="general_traditional.csv",
        help="History CSV inside stats/history that contains the metric column.",
    )
    parser.add_argument(
        "--metric",
        default="GP",
        help="Metric column to read from the source CSV.",
    )
    parser.add_argument(
        "--lane-name",
        default="gp",
        help="Short lane name used in output filenames.",
    )
    parser.add_argument(
        "--minutes-source",
        default="general_traditional.csv",
        help="History CSV inside stats/history used to refresh MIN in real time.",
    )
    parser.add_argument(
        "--minutes-column",
        default="MIN",
        help="Column from the minutes source used for live minutes.",
    )
    parser.add_argument(
        "--minutes-games-column",
        default="GP",
        help="Optional column used to convert a per-game MIN column into total minutes. Leave blank to use MIN as-is.",
    )
    parser.add_argument(
        "--current-season",
        default="",
        help="Season string that should use the lower in-season minute threshold. Defaults to the latest detected season.",
    )
    parser.add_argument(
        "--current-season-min-threshold",
        type=float,
        default=200.0,
        help="Minute threshold for penalty rows in the current season.",
    )
    parser.add_argument(
        "--standard-min-threshold",
        type=float,
        default=1000.0,
        help="Minute threshold for penalty rows in completed seasons.",
    )
    parser.add_argument(
        "--allow-id-fallback",
        action="store_true",
        help="Allow season+NBA_ID fallback when normalized season+player matching fails.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    universe_path = Path(args.universe_csv)
    details_path = resolve_details_csv_path(args.details_csv, universe_path)
    workbook_path = Path(args.workbook)
    source_path = HISTORY_DIR / args.source
    minutes_source_path = HISTORY_DIR / args.minutes_source
    if not source_path.exists():
        raise SystemExit(f"Source CSV not found: {source_path}")
    if not minutes_source_path.exists():
        raise SystemExit(f"Minutes source CSV not found: {minutes_source_path}")
    if details_path and not details_path.exists():
        raise SystemExit(f"Details CSV not found: {details_path}")

    source_rows = read_history_csv(source_path)
    if not source_rows:
        raise SystemExit(f"No rows found in source CSV: {source_path.name}")
    if args.metric not in source_rows[0]:
        raise SystemExit(f"Metric column not found in {source_path.name}: {args.metric}")
    if "Season" not in source_rows[0]:
        raise SystemExit(f"Season column not found in {source_path.name}")
    if "PLAYER_NAME" not in source_rows[0]:
        raise SystemExit(f"PLAYER_NAME column not found in {source_path.name}")
    if minutes_source_path == source_path:
        minutes_rows = source_rows
    else:
        minutes_rows = read_history_csv(minutes_source_path)
    if not minutes_rows:
        raise SystemExit(f"No rows found in minutes source CSV: {minutes_source_path.name}")
    if args.minutes_column not in minutes_rows[0]:
        raise SystemExit(
            f"Minutes column not found in {minutes_source_path.name}: {args.minutes_column}"
        )
    if args.minutes_games_column and args.minutes_games_column not in minutes_rows[0]:
        raise SystemExit(
            f"Minutes games column not found in {minutes_source_path.name}: {args.minutes_games_column}"
        )

    workbook_universe: List[CalUniverseRow] = []
    if workbook_path.exists():
        workbook_universe = load_cal_universe(workbook_path, sheet_name=args.sheet)

    if universe_path.exists():
        universe = load_universe_csv(universe_path)
    elif workbook_universe:
        universe = workbook_universe
        export_universe_csv(universe_path, universe)
    else:
        raise SystemExit(
            f"Universe CSV not found: {universe_path} and workbook not found: {workbook_path}"
        )

    if details_path:
        universe = enrich_universe_rows(universe, load_universe_csv(details_path))
    if workbook_universe:
        universe = enrich_universe_rows(universe, workbook_universe)
    source_by_id, source_by_name = build_source_index(source_rows)
    minutes_by_id, minutes_by_name = build_source_index(minutes_rows)
    current_season = detect_current_season(args.current_season, universe, minutes_rows)
    sheet_input_rows = build_sheet_input_rows(source_rows, args.metric)

    matched_values: List[float] = []
    matched_rows: List[
        Tuple[
            CalUniverseRow,
            Optional[Dict[str, str]],
            str,
            Optional[Dict[str, str]],
            str,
        ]
    ] = []

    for universe_row in universe:
        source_row, matched_by = match_metric_row(
            universe_row,
            source_by_id,
            source_by_name,
            allow_id_fallback=args.allow_id_fallback,
        )
        minutes_row, minutes_matched_by = match_metric_row(
            universe_row,
            minutes_by_id,
            minutes_by_name,
            allow_id_fallback=args.allow_id_fallback,
        )
        matched_rows.append(
            (universe_row, source_row, matched_by, minutes_row, minutes_matched_by)
        )
        if source_row is not None:
            value = parse_float(source_row.get(args.metric, ""))
            if value is not None:
                matched_values.append(value)

    if len(matched_values) < 2:
        raise SystemExit("Not enough matched values to compute a sample standard deviation.")

    mean_value = statistics.mean(matched_values)
    stdev_value = statistics.stdev(matched_values)
    if stdev_value == 0:
        raise SystemExit("Matched values have zero variance; cannot compute z-scores.")

    datapull_rows: List[Dict[str, object]] = []
    cal_rows: List[Dict[str, object]] = []
    unmatched_rows: List[Dict[str, object]] = []

    for universe_row, source_row, matched_by, minutes_row, minutes_matched_by in matched_rows:
        raw_value = None if source_row is None else parse_float(source_row.get(args.metric, ""))
        source_player_id = "" if source_row is None else canonical_id(source_row.get("PLAYER_ID", ""))
        source_player_name = "" if source_row is None else str(source_row.get("PLAYER_NAME", "")).strip()
        source_team = "" if source_row is None else str(
            source_row.get("TEAM_ABBREVIATION")
            or source_row.get("PLAYER_LAST_TEAM_ABBREVIATION")
            or ""
        ).strip()
        workbook_minutes = universe_row.minutes
        live_minutes, live_minutes_per_game, live_gp = resolve_live_minutes(
            minutes_row,
            args.minutes_column,
            args.minutes_games_column,
        )
        effective_minutes = live_minutes if live_minutes is not None else workbook_minutes

        raw_z = compute_capped_z_score(raw_value, mean_value, stdev_value)
        normalized = apply_cal_normalization(
            season=universe_row.season,
            rotation_role=universe_row.rotation_role,
            minutes=effective_minutes,
            raw_z=raw_z,
            current_season=current_season,
            current_season_min_threshold=args.current_season_min_threshold,
            standard_min_threshold=args.standard_min_threshold,
        )

        datapull_rows.append(
            {
                "NBA_ID": universe_row.nba_id,
                "Season": universe_row.season,
                "Player": universe_row.player,
                args.metric: raw_value,
                "MatchedBy": matched_by,
                "SourcePlayerID": source_player_id,
                "SourcePlayer": source_player_name,
                "SourceTeam": source_team,
                "SourceFile": source_path.name,
            }
        )

        cal_rows.append(
            {
                "NBA_ID": universe_row.nba_id,
                "Season": universe_row.season,
                "Player": universe_row.player,
                "RotationRole": universe_row.rotation_role,
                "MIN": effective_minutes,
                "WorkbookMIN": workbook_minutes,
                "LiveMIN": live_minutes,
                "LiveMINPerGame": live_minutes_per_game,
                "LiveGP": live_gp,
                "MinutesMatchedBy": minutes_matched_by,
                "MinutesSourceFile": minutes_source_path.name,
                "CurrentSeason": current_season,
                "RawMatchedValue": raw_value,
                "NonNormalized": raw_z,
                "Normalized": normalized,
                "InverseNonNormalized": inverse_or_blank(raw_z),
                "InverseNormalized": inverse_or_blank(normalized),
                "MatchedBy": matched_by,
                "SourcePlayerID": source_player_id,
                "SourcePlayer": source_player_name,
                "SourceTeam": source_team,
                "SourceFile": source_path.name,
                "SourceMetric": args.metric,
            }
        )

        if raw_value is None:
            unmatched_rows.append(
                {
                    "NBA_ID": universe_row.nba_id,
                    "Season": universe_row.season,
                    "Player": universe_row.player,
                    "RotationRole": universe_row.rotation_role,
                    "MIN": effective_minutes,
                    "ExpectedMetric": args.metric,
                    "SourceFile": source_path.name,
                }
            )

    datapull_path = EXPORT_DIR / f"{args.lane_name}_datapull.csv"
    datapull_paste_path = EXPORT_DIR / f"{args.lane_name}_datapull_paste.csv"
    cal_path = EXPORT_DIR / f"{args.lane_name}_cal.csv"
    unmatched_path = EXPORT_DIR / f"{args.lane_name}_unmatched.csv"

    write_csv(
        datapull_path,
        [
            "NBA_ID",
            "Season",
            "Player",
            args.metric,
            "MatchedBy",
            "SourcePlayerID",
            "SourcePlayer",
            "SourceTeam",
            "SourceFile",
        ],
        datapull_rows,
    )
    write_csv(
        datapull_paste_path,
        [
            "Season",
            "Player",
            args.metric,
        ],
        sheet_input_rows,
    )
    write_csv(
        cal_path,
        [
            "NBA_ID",
            "Season",
            "Player",
            "RotationRole",
            "MIN",
            "WorkbookMIN",
            "LiveMIN",
            "LiveMINPerGame",
            "LiveGP",
            "MinutesMatchedBy",
            "MinutesSourceFile",
            "CurrentSeason",
            "RawMatchedValue",
            "NonNormalized",
            "Normalized",
            "InverseNonNormalized",
            "InverseNormalized",
            "MatchedBy",
            "SourcePlayerID",
            "SourcePlayer",
            "SourceTeam",
            "SourceFile",
            "SourceMetric",
        ],
        cal_rows,
    )
    write_csv(
        unmatched_path,
        [
            "NBA_ID",
            "Season",
            "Player",
            "RotationRole",
            "MIN",
            "ExpectedMetric",
            "SourceFile",
        ],
        unmatched_rows,
    )

    matched_count = sum(1 for row in cal_rows if row["RawMatchedValue"] is not None)
    print(
        f"[OK] {args.lane_name} lane built "
        f"({matched_count}/{len(cal_rows)} matched, mean={mean_value:.6f}, stdev={stdev_value:.6f})"
    )
    print(f"[OUT] DataPull -> {datapull_path}")
    print(f"[OUT] DataPull paste -> {datapull_paste_path}")
    print(f"[OUT] Cal -> {cal_path}")
    print(f"[OUT] Unmatched -> {unmatched_path}")


if __name__ == "__main__":
    main()
