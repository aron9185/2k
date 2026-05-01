from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests


BASE_DIR = Path(__file__).resolve().parent
TMP_DIR = BASE_DIR / "tmp"
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


@dataclass(frozen=True)
class BestHitterPick:
    selection: str
    team: str
    positions: tuple[str, ...]
    hr_projection: float
    rbi_projection: float
    runs_projection: float
    page_detail: str
    candidate_count: int
    matched_projection_count: int
    real_player_details: str


def _first_text(nodes: list[dict[str, Any]] | None) -> str:
    if not isinstance(nodes, list):
        return ""
    parts: list[str] = []
    for node in nodes:
        children = node.get("children") or []
        if not isinstance(children, list):
            continue
        for child in children:
            text = str(child.get("text") or "").strip()
            if text:
                parts.append(text)
    return " ".join(parts).strip()


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


def _load_projection_dump(path: str | Path) -> list[OptimalProjection]:
    raw_rows = json.loads(Path(path).read_text(encoding="utf8"))
    projections: list[OptimalProjection] = []
    for row in raw_rows:
        if not isinstance(row, dict):
            continue
        projections.append(
            OptimalProjection(
                player_id=str(row.get("player_id") or "").strip(),
                name=str(row.get("name") or "").strip(),
                team=_normalize_team(str(row.get("team") or "")),
                opponent=_normalize_team(str(row.get("opponent") or "")),
                positions=tuple(str(pos).upper() for pos in row.get("positions") or []),
                hr_projection=_mid(row.get("hr_projection")),
                rbi_projection=_mid(row.get("rbi_projection")),
                runs_projection=_mid(row.get("runs_projection")),
                page_detail=str(row.get("page_detail") or "").strip(),
            )
        )
    return projections


def _projection_cache_path(day: str) -> Path:
    return TMP_DIR / f"optimal_mlb_projections_{day}.json"


def _latest_projection_cache() -> Path | None:
    paths = sorted(TMP_DIR.glob("optimal_mlb_projections_*.json"), reverse=True)
    return paths[0] if paths else None


def load_optimal_projections(*, day: str = "") -> list[OptimalProjection]:
    normalized_day = str(day or "").strip()
    if normalized_day:
        cache_path = _projection_cache_path(normalized_day)
        if cache_path.is_file():
            return _load_projection_dump(cache_path)

    try:
        return fetch_optimal_projections()
    except Exception:
        fallback = _latest_projection_cache()
        if fallback and fallback.is_file():
            return _load_projection_dump(fallback)
        raise


def _projection_lookup(
    projections: list[OptimalProjection],
) -> tuple[dict[tuple[str, str], OptimalProjection], dict[str, list[OptimalProjection]]]:
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
        part
        for part in [
            str(player.get("firstName") or "").strip(),
            str(player.get("lastName") or "").strip(),
        ]
        if part
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


def entry_content_text(entry: dict[str, Any]) -> str:
    post = entry.get("post") or {}
    return _first_text(((post.get("content") or {}).get("nodes")) or [])


def is_anytime_rbi_entry(entry: dict[str, Any]) -> bool:
    poll = entry.get("poll") or {}
    post = entry.get("post") or {}
    additional = poll.get("additionalInfo") or {}
    poll_type = str(additional.get("type") or "").strip().lower()
    header = str(post.get("header") or "").strip().lower()
    content = entry_content_text(entry).lower()
    return (
        poll_type == "player"
        and additional.get("isAnytimePlay")
        and "rbi" in f"{header} {content}"
    )


def is_most_stat_entry(
    entry: dict[str, Any],
    *,
    stat: str = "",
    threshold: float | None = None,
) -> bool:
    poll = entry.get("poll") or {}
    additional = poll.get("additionalInfo") or {}
    if str(additional.get("type") or "").strip().lower() != "player":
        return False
    if not additional.get("isMostStat"):
        return False
    if stat:
        if str(additional.get("stat") or "").strip().lower() != str(stat).strip().lower():
            return False
    if threshold is not None:
        try:
            if float(additional.get("threshold")) != float(threshold):
                return False
        except Exception:
            return False
    return True


def best_hitter_pick(
    entry: dict[str, Any],
    projections: list[OptimalProjection],
    *,
    include_bench: bool = False,
) -> BestHitterPick | None:
    by_team_name, by_name = _projection_lookup(projections)
    candidates = _game_players(entry.get("game_payload") or {}, include_bench=include_bench)
    matched: list[tuple[dict[str, Any], OptimalProjection]] = []
    for player in candidates:
        projection = _match_projection(player, by_team_name, by_name)
        if projection:
            matched.append((player, projection))
    if not matched:
        return None

    best_player, best_projection = max(
        matched,
        key=lambda item: (
            item[1].hr_projection,
            item[1].rbi_projection,
            item[1].runs_projection,
            item[1].name,
        ),
    )
    return BestHitterPick(
        selection=best_projection.name,
        team=((best_player.get("team") or {}).get("key") or best_projection.team),
        positions=best_projection.positions,
        hr_projection=best_projection.hr_projection,
        rbi_projection=best_projection.rbi_projection,
        runs_projection=best_projection.runs_projection,
        page_detail=best_projection.page_detail,
        candidate_count=len(candidates),
        matched_projection_count=len(matched),
        real_player_details=_player_details_text(best_player),
    )


def projected_hitter_candidates(
    entry: dict[str, Any],
    projections: list[OptimalProjection],
    *,
    stat: str,
    include_bench: bool = False,
) -> list[dict[str, Any]]:
    stat_key = str(stat or "").strip().lower()
    projection_field = {
        "runs": "runs_projection",
        "rbis": "rbi_projection",
        "homeruns": "hr_projection",
    }.get(stat_key)
    if not projection_field:
        return []

    by_team_name, by_name = _projection_lookup(projections)
    candidates = _game_players(entry.get("game_payload") or {}, include_bench=include_bench)
    matched: list[tuple[dict[str, Any], OptimalProjection]] = []
    for player in candidates:
        projection = _match_projection(player, by_team_name, by_name)
        if projection:
            matched.append((player, projection))

    results: list[dict[str, Any]] = []
    for player, projection in matched:
        projection_value = float(getattr(projection, projection_field, 0.0) or 0.0)
        if projection_value <= 0:
            continue
        fair_prob = 1.0 - math.exp(-projection_value)
        details = _player_details_text(player)
        note = (
            f"Optimal {stat_key} projection {projection_value:.2f} "
            f"({fair_prob * 100.0:.1f}% for 1+)"
        )
        if details:
            note = f"{note}; {details}"
        results.append(
            {
                "player_key": _normalize_player(projection.name),
                "selection": projection.name,
                "fair_prob": fair_prob,
                "projection_value": projection_value,
                "source_note": note,
            }
        )
    return results
