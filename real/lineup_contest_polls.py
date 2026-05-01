from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lineup import (
    choose_rotowire_projection_set,
    is_unavailable_or_questionable_record,
    player_full_name,
    safe_float,
)
from poll_market_matcher import normalize_team, team_pair


BASE_DIR = Path(__file__).resolve().parent


def _normalize_lineup_team(value: str) -> str:
    team = normalize_team(value)
    aliases = {
        "ath": "oak",
        "kcr": "kc",
    }
    return aliases.get(team, team)


def is_lineup_contest_post(post: dict[str, Any]) -> bool:
    additional = post.get("additionalInfo") or {}
    if str(additional.get("type") or "").strip().lower() == "playerratingcontest":
        return True
    for node in ((post.get("content") or {}).get("nodes")) or []:
        if str(node.get("type") or "").strip().lower() == "playerratingcontest":
            return True
    return False


def lineup_contest_additional(post: dict[str, Any]) -> dict[str, Any]:
    additional = dict(post.get("additionalInfo") or {})
    for node in ((post.get("content") or {}).get("nodes")) or []:
        if str(node.get("type") or "").strip().lower() != "playerratingcontest":
            continue
        payload = dict(node.get("additionalInfo") or {})
        payload.setdefault("contestId", node.get("contestId"))
        payload.setdefault("contestType", node.get("contestType"))
        payload.setdefault("type", "PlayerRatingContest")
        additional = {
            **payload,
            **additional,
        }
        break
    return additional


def _record_team_pair(record: dict[str, Any]) -> tuple[str, str] | None:
    team = _normalize_lineup_team(str(((record.get("team") or {}).get("abbr")) or ""))
    opponent = _normalize_lineup_team(str(((record.get("opponent") or {}).get("team")) or ""))
    if not team or not opponent:
        return None
    return team_pair(team, opponent)


def _entry_team_pair(entry: dict[str, Any]) -> tuple[str, str] | None:
    game = entry.get("game") or {}
    home_team = _normalize_lineup_team(str(game.get("homeTeamKey") or ""))
    away_team = _normalize_lineup_team(str(game.get("awayTeamKey") or ""))
    if not home_team or not away_team or home_team == away_team:
        return None
    return team_pair(home_team, away_team)


def _projected_points(record: dict[str, Any]) -> float:
    return safe_float(record.get("pts"))


def _game_has_started(entry: dict[str, Any]) -> bool:
    game = entry.get("game") or {}
    game_time = str(game.get("dateTime") or "").strip()
    if not game_time:
        return False
    try:
        parsed = datetime.fromisoformat(game_time.replace("Z", "+00:00"))
    except ValueError:
        return False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed <= datetime.now(timezone.utc)


def _ranked_player_rows(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for record in records:
        if is_unavailable_or_questionable_record(record):
            continue
        points = _projected_points(record)
        if points <= 0:
            continue
        rows.append(
            {
                "name": player_full_name(record) or str(record.get("fullName") or "").strip(),
                "points": points,
                "team": str(((record.get("team") or {}).get("abbr")) or "").strip(),
                "position": "/".join(str(pos).strip() for pos in (record.get("pos") or []) if str(pos).strip()),
            }
        )
    rows.sort(
        key=lambda row: (
            -float(row["points"]),
            str(row["name"]),
        )
    )
    return rows


def build_lineup_contest_rankings(
    entries: list[dict[str, Any]],
    *,
    sport: str,
    day: str,
    lineup_size: int = 5,
    max_games_to_play: int = 3,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    projection_summary = choose_rotowire_projection_set(sport, day, site="auto")
    records_by_pair: dict[tuple[str, str], list[dict[str, Any]]] = {}
    all_ranked_players = _ranked_player_rows(projection_summary.get("records") or [])
    for record in projection_summary.get("records") or []:
        pair = _record_team_pair(record)
        if pair is None:
            continue
        records_by_pair.setdefault(pair, []).append(record)

    candidate_rows: list[dict[str, Any]] = []
    by_post_id: dict[str, dict[str, Any]] = {}
    for entry in entries:
        post = entry.get("post") or {}
        post_id = str(post.get("id") or "").strip()
        pair = _entry_team_pair(entry)
        if not post_id:
            continue
        if _game_has_started(entry):
            by_post_id[post_id] = {
                "status": "pass",
                "recommended_option": "",
                "lineup_players": "",
                "lineup_cutoff_gap": "",
                "lineup_min_rank_gap": "",
                "lineup_avg_rank_gap": "",
                "lineup_top5_total": "",
                "lineup_rank": "",
                "lineup_projection_site": projection_summary.get("site") or "",
                "lineup_candidate_count": "",
                "notes": "Game already started; lineup contest skipped.",
            }
            continue
        if pair is None:
            ranked_players = all_ranked_players if len(entries) == 1 else []
        else:
            ranked_players = _ranked_player_rows(records_by_pair.get(pair, []))
            if not ranked_players and len(entries) == 1:
                ranked_players = all_ranked_players
        if len(ranked_players) < lineup_size:
            by_post_id[post_id] = {
                "status": "no_market",
                "recommended_option": "",
                "lineup_players": "",
                "lineup_cutoff_gap": "",
                "lineup_min_rank_gap": "",
                "lineup_avg_rank_gap": "",
                "lineup_top5_total": "",
                "lineup_rank": "",
                "lineup_projection_site": projection_summary.get("site") or "",
                "lineup_candidate_count": len(ranked_players),
                "notes": (
                    "Not enough positive available Rotowire projections in this contest to build a top five."
                ),
            }
            continue

        top_five = ranked_players[:lineup_size]
        sixth_points = ranked_players[lineup_size]["points"] if len(ranked_players) > lineup_size else 0.0
        cutoff_gap = float(top_five[-1]["points"]) - float(sixth_points)
        adjacent_gaps = [
            float(top_five[index]["points"]) - float(top_five[index + 1]["points"])
            for index in range(len(top_five) - 1)
        ]
        min_rank_gap = min(adjacent_gaps) if adjacent_gaps else 0.0
        avg_rank_gap = sum(adjacent_gaps) / len(adjacent_gaps) if adjacent_gaps else 0.0
        top5_total = sum(float(player["points"]) for player in top_five)
        lineup_players = " > ".join(
            f"{index + 1}. {player['name']}"
            for index, player in enumerate(top_five)
        )
        note = (
            f"Rotowire {projection_summary.get('site') or ''} lineup contest score uses "
            f"5v6 gap first, then rank separation, then top-five total."
        ).strip()
        candidate_rows.append(
            {
                "post_id": post_id,
                "lineup_matchup_key": "|".join(pair) if pair is not None else post_id,
                "recommended_option": lineup_players,
                "lineup_players": lineup_players,
                "lineup_cutoff_gap": round(cutoff_gap, 4),
                "lineup_min_rank_gap": round(min_rank_gap, 4),
                "lineup_avg_rank_gap": round(avg_rank_gap, 4),
                "lineup_top5_total": round(top5_total, 4),
                "lineup_projection_site": projection_summary.get("site") or "",
                "lineup_candidate_count": len(ranked_players),
                "notes": note,
            }
        )

    candidate_rows.sort(
        key=lambda row: (
            -float(row["lineup_cutoff_gap"]),
            -float(row["lineup_min_rank_gap"]),
            -float(row["lineup_top5_total"]),
            -float(row["lineup_avg_rank_gap"]),
            str(row["post_id"]),
        )
    )

    selected_matchups: set[str] = set()
    pick_count = 0
    for row in candidate_rows:
        matchup_key = str(row.get("lineup_matchup_key") or row["post_id"])
        if matchup_key in selected_matchups:
            row["lineup_rank"] = ""
            row["status"] = "pass"
            row["notes"] = f"{row['notes']} Duplicate matchup already selected."
        elif pick_count < max_games_to_play:
            pick_count += 1
            selected_matchups.add(matchup_key)
            row["lineup_rank"] = pick_count
            row["status"] = "pick"
        else:
            row["lineup_rank"] = ""
            row["status"] = "pass"
        by_post_id[str(row["post_id"])] = row

    return by_post_id, projection_summary
