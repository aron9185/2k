from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests

from rotation_core import CDN_BOXSCORE, fetch_json, parse_boxscore_meta

MATCHUPS_URL = "https://stats.nba.com/stats/boxscorematchupsv3"
CACHE_DIR = os.path.join(os.path.dirname(__file__), "cache")
os.makedirs(CACHE_DIR, exist_ok=True)

HEADERS = {
    "Accept": "*/*",
    "Accept-Encoding": "gzip, deflate, br, zstd",
    "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "Origin": "https://www.nba.com",
    "Pragma": "no-cache",
    "Referer": "https://www.nba.com/",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-site",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/147.0.0.0 Safari/537.36"
    ),
    "x-nba-stats-origin": "stats",
    "x-nba-stats-token": "true",
    'sec-ch-ua': '"Google Chrome";v="147", "Not.A/Brand";v="8", "Chromium";v="147"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
}


def _cache_path(game_id: str) -> str:
    return os.path.join(CACHE_DIR, f"matchups_v3_{game_id}.json")


def _debug_path(game_id: str) -> str:
    return os.path.join(CACHE_DIR, f"matchups_v3_{game_id}_debug.txt")


def _read_json(path: str) -> Optional[Dict[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            obj = json.load(f)
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


def _write_json(path: str, obj: Dict[str, Any]) -> None:
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f)
        os.replace(tmp, path)
    except Exception:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass


def _write_text(path: str, text: str) -> None:
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
    except Exception:
        pass


def _safe_str(value: Any) -> str:
    try:
        return str(value or "").strip()
    except Exception:
        return ""


def _safe_int(value: Any) -> Optional[int]:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except Exception:
        return None


def _safe_float(value: Any) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except Exception:
        return None


def _parse_minutes(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)

    text = _safe_str(value)
    if not text:
        return None

    iso_match = re.match(r"^PT(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?$", text, re.IGNORECASE)
    if iso_match:
        minutes = float(iso_match.group(1) or 0.0)
        seconds = float(iso_match.group(2) or 0.0)
        return minutes + seconds / 60.0

    mmss_match = re.match(r"^(\d+):(\d+(?:\.\d+)?)$", text)
    if mmss_match:
        minutes = float(mmss_match.group(1))
        seconds = float(mmss_match.group(2))
        return minutes + seconds / 60.0

    return _safe_float(text)


def _flatten(obj: Any, prefix: str = "") -> List[Tuple[str, Any]]:
    items: List[Tuple[str, Any]] = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            child_prefix = f"{prefix}.{key}" if prefix else str(key)
            items.extend(_flatten(value, child_prefix))
    elif isinstance(obj, list):
        for idx, value in enumerate(obj):
            child_prefix = f"{prefix}[{idx}]"
            items.extend(_flatten(value, child_prefix))
    else:
        items.append((prefix.lower(), obj))
    return items


def _lookup_text(obj: Any, preferred_keys: Iterable[str]) -> str:
    preferred = [key.lower() for key in preferred_keys]
    flat = _flatten(obj)
    best_fallback = ""
    for path, value in flat:
        text = _safe_str(value)
        if not text:
            continue
        if any(path.endswith(key) or key in path for key in preferred):
            return text
        if not best_fallback and (path.endswith("name") or path.endswith("personname")):
            best_fallback = text
    return best_fallback


def _lookup_minutes(obj: Any) -> Optional[float]:
    preferred = [
        "matchupminutes",
        "minutes",
        "timeguarding",
        "matchuptime",
        "min",
    ]
    flat = _flatten(obj)
    fallback: Optional[float] = None
    for path, value in flat:
        parsed = _parse_minutes(value)
        if parsed is None:
            continue
        if any(key in path for key in preferred):
            return parsed
        if fallback is None and ("minute" in path or path.endswith("min")):
            fallback = parsed
    return fallback


def _player_name(row: Dict[str, Any]) -> str:
    direct = [
        row.get("personName"),
        row.get("playerName"),
        row.get("name"),
        row.get("fullName"),
        row.get("nameI"),
    ]
    for value in direct:
        text = _safe_str(value)
        if text:
            return text
    first = _safe_str(row.get("firstName"))
    last = _safe_str(row.get("familyName") or row.get("lastName"))
    return f"{first} {last}".strip()


def _matchup_target_name(matchup: Dict[str, Any]) -> str:
    for value in (
        matchup.get("personName"),
        matchup.get("playerName"),
        matchup.get("name"),
        matchup.get("fullName"),
    ):
        text = _safe_str(value)
        if text:
            return text

    first = _safe_str(matchup.get("firstName"))
    last = _safe_str(matchup.get("familyName") or matchup.get("lastName"))
    combined = f"{first} {last}".strip()
    if combined:
        return combined

    name_i = _safe_str(matchup.get("nameI"))
    if name_i:
        return name_i

    return _lookup_text(
        matchup,
        [
            "offensiveplayer.personname",
            "offensiveplayer.name",
            "offensiveplayername",
            "offplayername",
            "opponentplayername",
            "matchupplayername",
            "guardedplayername",
            "playername",
            "personname",
        ],
    )


def _load_from_cache(game_id: str) -> Optional[Dict[str, Any]]:
    cache = _cache_path(game_id)
    if os.path.exists(cache):
        return _read_json(cache)
    return None


def _fetch_matchup_payload(game_id: str) -> Dict[str, Any]:
    params = {"GameID": str(game_id), "LeagueID": "00"}
    cache = _cache_path(game_id)
    debug = _debug_path(game_id)
    session = requests.Session()
    session.trust_env = False
    last_error = ""

    for attempt in range(3):
        try:
            response = session.get(
                MATCHUPS_URL,
                params=params,
                headers=HEADERS,
                timeout=(1.5, 6.0),
            )
            body_preview = response.text[:400]
            if response.status_code == 200:
                payload = response.json()
                if isinstance(payload, dict):
                    _write_json(cache, payload)
                    _write_text(debug, f"OK status=200 len={len(response.text)}")
                    return payload
                last_error = "Received non-dict JSON payload."
            else:
                last_error = f"HTTP {response.status_code}\n\nfirst400:\n{body_preview}"
        except Exception as exc:
            last_error = f"ERROR: {exc!r}\n"

        time.sleep(0.35 * (attempt + 1))

    cached = _load_from_cache(game_id)
    if cached is not None:
        _write_text(debug, f"{last_error}\nUsing cached payload.")
        return cached

    _write_text(debug, last_error or "Unknown matchup fetch failure.")
    return {}


def _extract_matchup_root(payload: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        return {}

    for key in ("boxScoreMatchups", "boxScoreMatchupsV3", "boxscoreMatchups"):
        root = payload.get(key)
        if isinstance(root, dict):
            return root

    if "homeTeam" in payload and "awayTeam" in payload:
        return payload

    return {}


def _format_guarded(target_name: str, minutes: float) -> str:
    return f"{target_name} ({minutes:.1f}m)"


def _summarize_edges(edges: List[Tuple[str, str, float]]) -> List[Dict[str, Any]]:
    grouped: Dict[str, Dict[str, float]] = {}
    for subject, counterpart, minutes in edges:
        if not subject or not counterpart or minutes <= 0:
            continue
        subject_bucket = grouped.setdefault(subject, {})
        subject_bucket[counterpart] = subject_bucket.get(counterpart, 0.0) + float(minutes)

    rows: List[Dict[str, Any]] = []
    for subject, counterparts in grouped.items():
        ordered = sorted(counterparts.items(), key=lambda item: (-item[1], item[0]))
        total_min = sum(minutes for _, minutes in ordered)
        display = [_format_guarded(name, minutes) for name, minutes in ordered[:3]]
        while len(display) < 3:
            display.append("")
        rows.append(
            {
                "subject": subject,
                "top1": display[0],
                "top2": display[1],
                "top3": display[2],
                "total_min": f"{total_min:.1f}" if total_min > 0 else "",
                "total_min_value": round(total_min, 3),
            }
        )

    rows.sort(key=lambda row: (-float(row.get("total_min_value") or 0.0), row.get("subject") or ""))
    return rows


def _team_edges(team_obj: Dict[str, Any]) -> List[Tuple[str, str, float]]:
    edges: List[Tuple[str, str, float]] = []
    players = team_obj.get("players") or []
    if not isinstance(players, list):
        players = []

    for player in players:
        if not isinstance(player, dict):
            continue
        defender = _player_name(player)
        if not defender:
            continue

        matchups = player.get("matchups") or player.get("playerMatchups") or []
        if not isinstance(matchups, list):
            matchups = []

        for matchup in matchups:
            if not isinstance(matchup, dict):
                continue
            target_name = _matchup_target_name(matchup)
            minutes = _lookup_minutes(matchup)
            if not target_name or minutes is None or minutes <= 0:
                continue
            edges.append((defender, target_name, minutes))

    return edges


def _summarize_team(team_obj: Dict[str, Any], fallback_abbr: str) -> Dict[str, Any]:
    team_abbr = _safe_str(team_obj.get("teamTricode")) or fallback_abbr or "TEAM"
    return {"team_abbr": team_abbr, "rows": _summarize_edges(_team_edges(team_obj))}


def build_guarded_top3_tables(game_id: str, home_team_id: int = 0, away_team_id: int = 0) -> Dict[str, Any]:
    game_id = _safe_str(game_id)
    box = fetch_json(CDN_BOXSCORE.format(GAME_ID=game_id), cache_key=f"box_{game_id}")
    box_meta = parse_boxscore_meta(box)

    payload = _fetch_matchup_payload(game_id)
    root = _extract_matchup_root(payload)

    home_fallback = _safe_str(box_meta.get("home_abbr")) or "HOME"
    away_fallback = _safe_str(box_meta.get("away_abbr")) or "AWAY"

    result = {
        "home": {"team_abbr": home_fallback, "defends_rows": [], "guarded_by_rows": []},
        "away": {"team_abbr": away_fallback, "defends_rows": [], "guarded_by_rows": []},
    }

    if not root:
        return result

    home_team = root.get("homeTeam") or {}
    away_team = root.get("awayTeam") or {}

    home_edges: List[Tuple[str, str, float]] = []
    away_edges: List[Tuple[str, str, float]] = []
    if isinstance(home_team, dict):
        home_summary = _summarize_team(home_team, home_fallback)
        home_edges = _team_edges(home_team)
        result["home"]["team_abbr"] = home_summary["team_abbr"]
        result["home"]["defends_rows"] = home_summary["rows"]
    if isinstance(away_team, dict):
        away_summary = _summarize_team(away_team, away_fallback)
        away_edges = _team_edges(away_team)
        result["away"]["team_abbr"] = away_summary["team_abbr"]
        result["away"]["defends_rows"] = away_summary["rows"]

    # "guarded_by" is the inverse perspective:
    # home offensive players are guarded by away defenders, and vice versa.
    result["home"]["guarded_by_rows"] = _summarize_edges([(offense, defender, minutes) for defender, offense, minutes in away_edges])
    result["away"]["guarded_by_rows"] = _summarize_edges([(offense, defender, minutes) for defender, offense, minutes in home_edges])

    if not result["home"]["team_abbr"] and home_team_id:
        result["home"]["team_abbr"] = f"HOME-{home_team_id}"
    if not result["away"]["team_abbr"] and away_team_id:
        result["away"]["team_abbr"] = f"AWAY-{away_team_id}"

    return result
