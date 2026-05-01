from __future__ import annotations

import argparse
import csv
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

from recommend_game_feed_polls import _fetch_active_day_game_entries, _first_text, _poll_kind


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT = BASE_DIR / "anytime_rbi_recommendations.csv"
DEFAULT_OPTIMAL_PROJECT = "bet-plus-ev"
DEFAULT_OPTIMAL_API_KEY = "AIzaSyCrK-7aLOp8ULj-HLdaTGnAfvL5dDHCLOs"
DEFAULT_OPTIMAL_COLLECTION = "mlbDailyFantasyProjections"
PITCHER_POSITIONS = {"P", "SP", "RP"}


@dataclass(frozen=True)
class OptimalProjection:
    player_id: str
    name: str
    team: str
    opponent: str
    positions: tuple[str, ...]
    hr_projection: float
    rbi_projection: float
    runs_projection: float
    page_detail: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Legacy MLB Anytime RBI helper that ranks Real hitters by the "
            "highest Optimal Bet HR projection in each game."
        )
    )
    parser.add_argument("--day", default="", help="Optional Real active day, e.g. 2026-04-29.")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument(
        "--sheet-output",
        default="",
        help="Optional markdown sheet output. No markdown sheet is written unless this is set.",
    )
    parser.add_argument(
        "--include-bench",
        action="store_true",
        help="Include players whose Real game-feed details say Bench.",
    )
    parser.add_argument(
        "--dump-projections",
        default="",
        help="Optional JSON dump of parsed Optimal Bet projections.",
    )
    return parser.parse_args()


def _firestore_url() -> str:
    return (
        "https://firestore.googleapis.com/v1/projects/"
        f"{DEFAULT_OPTIMAL_PROJECT}/databases/(default)/documents/"
        f"{DEFAULT_OPTIMAL_COLLECTION}?key={DEFAULT_OPTIMAL_API_KEY}"
    )


def _decode_firestore_value(value: dict[str, Any]) -> Any:
    if not isinstance(value, dict):
        return value
    if "stringValue" in value:
        return value["stringValue"]
    if "integerValue" in value:
        return int(value["integerValue"])
    if "doubleValue" in value:
        return float(value["doubleValue"])
    if "booleanValue" in value:
        return bool(value["booleanValue"])
    if "nullValue" in value:
        return None
    if "timestampValue" in value:
        return value["timestampValue"]
    if "arrayValue" in value:
        return [
            _decode_firestore_value(item)
            for item in (value.get("arrayValue") or {}).get("values") or []
        ]
    if "mapValue" in value:
        return {
            key: _decode_firestore_value(item)
            for key, item in ((value.get("mapValue") or {}).get("fields") or {}).items()
        }
    return value


def _decode_firestore_document(document: dict[str, Any]) -> dict[str, Any]:
    return {
        key: _decode_firestore_value(value)
        for key, value in (document.get("fields") or {}).items()
    }


def _mid(value: Any) -> float:
    if isinstance(value, dict):
        value = value.get("mid")
    try:
        return float(value)
    except Exception:
        return 0.0


def _normalize_team(value: str) -> str:
    return "".join(ch for ch in str(value or "").upper() if ch.isalnum())


def _normalize_player(value: str) -> str:
    text = str(value or "").lower()
    text = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b\.?", " ", text)
    return "".join(ch for ch in text if ch.isalnum())


def _is_pitcher(positions: list[str] | tuple[str, ...]) -> bool:
    return bool(PITCHER_POSITIONS.intersection({str(pos).upper() for pos in positions}))


def fetch_optimal_projections() -> list[OptimalProjection]:
    session = requests.Session()
    session.trust_env = False
    response = session.get(_firestore_url(), timeout=30)
    response.raise_for_status()
    payload = response.json()

    projections: list[OptimalProjection] = []
    for document in payload.get("documents") or []:
        data = _decode_firestore_document(document)
        team = _normalize_team(data.get("team") or "")
        opponent = _normalize_team(data.get("opp") or "")
        page_detail = str(data.get("pageDetail") or "").strip()
        for row in data.get("projections") or []:
            if not isinstance(row, dict):
                continue
            positions = tuple(str(pos).upper() for pos in row.get("positions") or [])
            if _is_pitcher(positions):
                continue
            name = str(row.get("name") or "").strip()
            if not name:
                continue
            projections.append(
                OptimalProjection(
                    player_id=str(row.get("id") or "").strip(),
                    name=name,
                    team=team,
                    opponent=opponent,
                    positions=positions,
                    hr_projection=_mid(row.get("battingHomeRuns")),
                    rbi_projection=_mid(row.get("battingRBI")),
                    runs_projection=_mid(row.get("battingRuns")),
                    page_detail=page_detail,
                )
            )
    return projections


def _projection_lookup(projections: list[OptimalProjection]) -> tuple[dict[tuple[str, str], OptimalProjection], dict[str, list[OptimalProjection]]]:
    by_team_name = {
        (_normalize_team(projection.team), _normalize_player(projection.name)): projection
        for projection in projections
    }
    by_name: dict[str, list[OptimalProjection]] = {}
    for projection in projections:
        by_name.setdefault(_normalize_player(projection.name), []).append(projection)
    return by_team_name, by_name


def _player_name(player: dict[str, Any]) -> str:
    return " ".join(
        part for part in [str(player.get("firstName") or "").strip(), str(player.get("lastName") or "").strip()] if part
    ).strip()


def _player_details_text(player: dict[str, Any]) -> str:
    details = []
    for item in player.get("details") or []:
        if isinstance(item, dict) and item.get("text"):
            details.append(str(item.get("text")))
    return " | ".join(details)


def _is_bench_player(player: dict[str, Any]) -> bool:
    return any(
        str(item.get("text") or "").strip().lower() == "bench"
        for item in player.get("details") or []
        if isinstance(item, dict)
    )


def _game_players(game_payload: dict[str, Any], *, include_bench: bool) -> list[dict[str, Any]]:
    players = []
    for player in game_payload.get("players") or []:
        if not isinstance(player, dict):
            continue
        positions = [str(player.get("position") or "").upper()]
        if _is_pitcher(positions):
            continue
        if not include_bench and _is_bench_player(player):
            continue
        name = _player_name(player)
        if not name:
            continue
        players.append(player)
    return players


def _match_projection(
    player: dict[str, Any],
    by_team_name: dict[tuple[str, str], OptimalProjection],
    by_name: dict[str, list[OptimalProjection]],
) -> OptimalProjection | None:
    name_key = _normalize_player(_player_name(player))
    team_key = _normalize_team(((player.get("team") or {}).get("key")) or "")
    projection = by_team_name.get((team_key, name_key))
    if projection:
        return projection
    if team_key:
        return None
    matches = by_name.get(name_key) or []
    if len(matches) == 1:
        return matches[0]
    return None


def _is_anytime_rbi_entry(entry: dict[str, Any]) -> bool:
    poll = entry.get("poll") or {}
    post = entry.get("post") or {}
    additional = poll.get("additionalInfo") or {}
    header = str(post.get("header") or "").strip().lower()
    content = _first_text(((post.get("content") or {}).get("nodes")) or []).lower()
    return (
        _poll_kind(additional) == "anytime_play"
        and "rbi" in f"{header} {content}"
    )


def _best_player_for_entry(
    entry: dict[str, Any],
    by_team_name: dict[tuple[str, str], OptimalProjection],
    by_name: dict[str, list[OptimalProjection]],
    *,
    include_bench: bool,
) -> tuple[dict[str, Any] | None, OptimalProjection | None, int, int]:
    candidates = _game_players(entry.get("game_payload") or {}, include_bench=include_bench)
    matched: list[tuple[dict[str, Any], OptimalProjection]] = []
    for player in candidates:
        projection = _match_projection(player, by_team_name, by_name)
        if projection:
            matched.append((player, projection))
    if not matched:
        return None, None, len(candidates), 0
    best_player, best_projection = max(
        matched,
        key=lambda item: (
            item[1].hr_projection,
            item[1].rbi_projection,
            item[1].runs_projection,
            item[1].name,
        ),
    )
    return best_player, best_projection, len(candidates), len(matched)


def build_recommendations(
    *,
    requested_day: str = "",
    include_bench: bool = False,
) -> tuple[str, list[dict[str, Any]], list[OptimalProjection]]:
    projections = fetch_optimal_projections()
    by_team_name, by_name = _projection_lookup(projections)
    resolved_day, entries = _fetch_active_day_game_entries(
        "mlb",
        requested_day=requested_day,
        include_nonwagerable=True,
    )

    rows: list[dict[str, Any]] = []
    for entry in entries:
        if not _is_anytime_rbi_entry(entry):
            continue
        game = entry.get("game") or {}
        post = entry.get("post") or {}
        poll = entry.get("poll") or {}
        player, projection, candidate_count, matched_count = _best_player_for_entry(
            entry,
            by_team_name,
            by_name,
            include_bench=include_bench,
        )
        content_text = _first_text(((post.get("content") or {}).get("nodes")) or [])
        if player and projection:
            team = (player.get("team") or {}).get("key") or projection.team
            selection = projection.name
            details = _player_details_text(player)
            notes = "highest Optimal Bet HR projection among matched Real game-feed hitters"
        else:
            team = ""
            selection = ""
            details = ""
            notes = "No Optimal Bet projection matched Real game-feed hitters."
        rows.append(
            {
                "day": resolved_day,
                "game_id": game.get("id") or poll.get("gameId") or post.get("gameId") or "",
                "game_time": game.get("dateTime") or poll.get("locksAt") or post.get("pollsLockAt") or "",
                "away_team": game.get("awayTeamKey") or poll.get("awayTeamKey") or "",
                "home_team": game.get("homeTeamKey") or poll.get("homeTeamKey") or "",
                "post_id": post.get("id") or "",
                "poll_id": poll.get("id") or "",
                "poll": post.get("header") or "Anytime RBI",
                "content_text": content_text,
                "selection": selection,
                "selection_put": f"{selection}0" if selection else "",
                "team": team,
                "position": "/".join(projection.positions) if projection else "",
                "optimal_hr_projection": round(projection.hr_projection, 4) if projection else "",
                "optimal_rbi_projection": round(projection.rbi_projection, 4) if projection else "",
                "optimal_runs_projection": round(projection.runs_projection, 4) if projection else "",
                "optimal_page_detail": projection.page_detail if projection else "",
                "candidate_count": candidate_count,
                "matched_projection_count": matched_count,
                "real_player_details": details,
                "notes": notes,
            }
        )
    rows.sort(key=lambda row: (str(row["game_time"]), str(row["away_team"]), str(row["home_team"])))
    return resolved_day, rows, projections


def write_csv(path: str | Path, rows: list[dict[str, Any]]) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "day",
        "game_id",
        "game_time",
        "away_team",
        "home_team",
        "post_id",
        "poll_id",
        "poll",
        "content_text",
        "selection",
        "selection_put",
        "team",
        "position",
        "optimal_hr_projection",
        "optimal_rbi_projection",
        "optimal_runs_projection",
        "optimal_page_detail",
        "candidate_count",
        "matched_projection_count",
        "real_player_details",
        "notes",
    ]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _format_game_time(value: str) -> str:
    return str(value or "").replace("T", " ").replace(".000Z", " UTC")


def render_sheet(rows: list[dict[str, Any]], *, day: str) -> str:
    sections = [
        f"# MLB Anytime RBI Picks - {day}",
        "",
        "Method: pick the Real game-feed hitter with the highest Optimal Bet `HR` projection in that game.",
        "",
        "| Game | Pick | Put | HR Proj | RBI Proj | Team | Notes |",
        "| --- | --- | ---: | ---: | ---: | --- | --- |",
    ]
    for row in rows:
        game = f"{row.get('away_team')} @ {row.get('home_team')} ({_format_game_time(str(row.get('game_time') or ''))})"
        sections.append(
            "| "
            + " | ".join(
                str(value or "").replace("|", "\\|")
                for value in [
                    game,
                    row.get("selection") or "No match",
                    "0",
                    row.get("optimal_hr_projection"),
                    row.get("optimal_rbi_projection"),
                    row.get("team"),
                    row.get("notes"),
                ]
            )
            + " |"
        )
    return "\n".join(sections).rstrip() + "\n"


def main() -> None:
    args = parse_args()
    resolved_day, rows, projections = build_recommendations(
        requested_day=args.day,
        include_bench=args.include_bench,
    )
    output_path = Path(args.output)
    write_csv(output_path, rows)

    sheet_path: Path | None = None
    if args.sheet_output:
        sheet_path = Path(args.sheet_output)
        sheet_path.parent.mkdir(parents=True, exist_ok=True)
        sheet_path.write_text(render_sheet(rows, day=resolved_day), encoding="utf8")

    if args.dump_projections:
        dump_path = Path(args.dump_projections)
        dump_path.parent.mkdir(parents=True, exist_ok=True)
        dump_path.write_text(
            json.dumps([projection.__dict__ for projection in projections], indent=2, ensure_ascii=True),
            encoding="utf8",
        )
    print(f"Saved {len(rows)} Anytime RBI recommendations for MLB {resolved_day} to {output_path}")
    if sheet_path is not None:
        print(f"Saved Anytime RBI sheet to {sheet_path}")


if __name__ == "__main__":
    main()
