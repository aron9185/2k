from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lineup import (
    DEFAULT_MULTIPLIER_CACHE_DIR,
    DEFAULT_REAL_ID_FILE,
    ROTOWIRE_SITES,
    availability_note,
    build_realsports_client,
    build_rotowire_session,
    choose_rotowire_projection_set,
    is_unavailable_or_questionable_record,
    is_standard_projection_slate,
    load_multiplier_cache,
    load_real_player_index,
    lookup_real_availability_entry,
    parse_iso_date,
    player_full_name,
    rotowire_get_json,
    safe_float,
    save_multiplier_cache,
    slate_target_rank,
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


def _record_dedupe_key(record: dict[str, Any]) -> str:
    player_name = player_full_name(record) or str(record.get("fullName") or "").strip()
    team_abbr = str(((record.get("team") or {}).get("abbr")) or "").strip()
    player_id = str(record.get("rwID") or "").strip()
    return player_id or f"{player_name.lower()}|{_normalize_lineup_team(team_abbr)}"


def _top_projected_records(records: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    records_by_player: dict[str, dict[str, Any]] = {}
    for record in records:
        points = _projected_points(record)
        if points <= 0:
            continue
        dedupe_key = _record_dedupe_key(record)
        existing = records_by_player.get(dedupe_key)
        if existing is None or points > _projected_points(existing):
            records_by_player[dedupe_key] = record
    top_records = list(records_by_player.values())
    top_records.sort(
        key=lambda record: (
            -_projected_points(record),
            player_full_name(record) or str(record.get("fullName") or ""),
        )
    )
    return top_records[:limit]


def _availability_risk_notes(
    records: list[dict[str, Any]],
    *,
    lineup_size: int,
    real_availability_lookup,
) -> list[str]:
    notes: list[str] = []
    for record in _top_projected_records(records, lineup_size):
        player_name = player_full_name(record) or str(record.get("fullName") or "").strip()
        if not player_name:
            continue
        if is_unavailable_or_questionable_record(record):
            notes.append(f"{player_name} ({availability_note(record)})")
            continue
        if real_availability_lookup is None:
            continue
        availability_entry = real_availability_lookup(record)
        if availability_entry.get("availability_blocked") is True:
            status = availability_entry.get("availability_status") or "questionable/unavailable"
            notes.append(f"{player_name} ({status})")
    return notes


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
    rows_by_player: dict[str, dict[str, Any]] = {}
    for record in records:
        if is_unavailable_or_questionable_record(record):
            continue
        points = _projected_points(record)
        if points <= 0:
            continue
        player_name = player_full_name(record) or str(record.get("fullName") or "").strip()
        team_abbr = str(((record.get("team") or {}).get("abbr")) or "").strip()
        player_id = str(record.get("rwID") or "").strip()
        dedupe_key = player_id or f"{player_name.lower()}|{_normalize_lineup_team(team_abbr)}"

        row = {
            "name": player_name,
            "points": points,
            "team": team_abbr,
            "position": "/".join(str(pos).strip() for pos in (record.get("pos") or []) if str(pos).strip()),
        }
        existing = rows_by_player.get(dedupe_key)
        if existing is None or float(row["points"]) > float(existing["points"]):
            rows_by_player[dedupe_key] = row
    rows = list(rows_by_player.values())
    rows.sort(
        key=lambda row: (
            -float(row["points"]),
            str(row["name"]),
        )
    )
    return rows


def _nonstandard_records_by_pair(
    *,
    sport: str,
    day: str,
    site: str,
) -> dict[tuple[str, str], list[dict[str, Any]]]:
    site_id = ROTOWIRE_SITES.get(site)
    if site_id is None:
        return {}
    target_date = parse_iso_date(day)
    session = build_rotowire_session()
    payload = rotowire_get_json(session, sport, "slate-list.php", params={"siteID": site_id})
    slates = payload.get("slates") or []
    records_by_pair: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for slate in slates:
        if slate_target_rank(sport, slate, target_date) is None:
            continue
        players = rotowire_get_json(session, sport, "players.php", params={"slateID": slate["slateID"]})
        if not isinstance(players, list) or not players:
            continue
        if is_standard_projection_slate(slate.get("contestType", ""), players):
            continue
        for record in players:
            if not isinstance(record, dict):
                continue
            pair = _record_team_pair(record)
            if pair is None:
                continue
            records_by_pair.setdefault(pair, []).append(record)
    return records_by_pair


def _build_lineup_candidate_row(
    *,
    post_id: str,
    matchup_key: str,
    ranked_players: list[dict[str, Any]],
    lineup_size: int,
    projection_site: str,
    candidate_count: int,
    note: str,
    entry_order: int,
) -> dict[str, Any]:
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
    return {
        "post_id": post_id,
        "lineup_matchup_key": matchup_key,
        "recommended_option": lineup_players,
        "lineup_players": lineup_players,
        "lineup_cutoff_gap": round(cutoff_gap, 4),
        "lineup_min_rank_gap": round(min_rank_gap, 4),
        "lineup_avg_rank_gap": round(avg_rank_gap, 4),
        "lineup_top5_total": round(top5_total, 4),
        "lineup_projection_site": projection_site,
        "lineup_candidate_count": candidate_count,
        "notes": note,
        "entry_order": entry_order,
    }


def build_lineup_contest_rankings(
    entries: list[dict[str, Any]],
    *,
    sport: str,
    day: str,
    lineup_size: int = 5,
    max_games_to_play: int = 3,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    projection_summary = choose_rotowire_projection_set(sport, day, site="auto")
    real_client = None
    real_player_index: dict[str, list[dict[str, Any]]] | None = None
    availability_cache_path = None
    availability_cache: dict[str, Any] = {}
    availability_lookup_disabled = False

    def real_availability_lookup(record: dict[str, Any]) -> dict[str, Any]:
        nonlocal real_client
        nonlocal real_player_index
        nonlocal availability_cache_path
        nonlocal availability_cache
        nonlocal availability_lookup_disabled

        if availability_lookup_disabled:
            return {}
        if real_client is None:
            real_client = build_realsports_client()
            real_player_index = load_real_player_index(DEFAULT_REAL_ID_FILE)
            availability_cache_path, availability_cache = load_multiplier_cache(
                DEFAULT_MULTIPLIER_CACHE_DIR,
                sport,
                day,
            )
        try:
            return lookup_real_availability_entry(
                real_client,
                sport,
                day,
                record,
                real_player_index or {},
                availability_cache,
            )
        except Exception as exc:
            availability_lookup_disabled = True
            print(f"Real availability lookup disabled for lineup contests: {exc}")
            return {}

    records_by_pair: dict[tuple[str, str], list[dict[str, Any]]] = {}
    all_ranked_players = _ranked_player_rows(projection_summary.get("records") or [])
    nonstandard_by_pair = _nonstandard_records_by_pair(
        sport=sport,
        day=day,
        site=str(projection_summary.get("site") or ""),
    )
    for record in projection_summary.get("records") or []:
        pair = _record_team_pair(record)
        if pair is None:
            continue
        records_by_pair.setdefault(pair, []).append(record)

    candidate_rows: list[dict[str, Any]] = []
    by_post_id: dict[str, dict[str, Any]] = {}
    for entry_order, entry in enumerate(entries):
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
        note_text = (
            f"Rotowire {projection_summary.get('site') or ''} lineup contest score uses "
            "5v6 gap first, then rank separation, then top-five total."
        ).strip()
        source_records: list[dict[str, Any]] = []
        if pair is None:
            ranked_players = all_ranked_players if len(entries) == 1 else []
            if len(entries) == 1 and ranked_players:
                source_records = projection_summary.get("records") or []
                note_text = (
                    f"Fallback lineup from Rotowire {projection_summary.get('site') or ''} "
                    "global slate pool because only one lineup contest was available."
                ).strip()
        else:
            primary_records = records_by_pair.get(pair, [])
            showdown_records = nonstandard_by_pair.get(pair, [])
            primary_ranked = _ranked_player_rows(primary_records)
            showdown_ranked = _ranked_player_rows(showdown_records)
            ranked_players = primary_ranked
            source_records = primary_records
            if len(showdown_ranked) > len(ranked_players):
                ranked_players = showdown_ranked
                source_records = showdown_records
            if ranked_players is showdown_ranked and ranked_players:
                note_text = (
                    f"Fallback lineup from Rotowire {projection_summary.get('site') or ''} "
                    "single-game slate for this matchup."
                ).strip()
            if not ranked_players and len(entries) == 1:
                ranked_players = all_ranked_players
                if ranked_players:
                    source_records = projection_summary.get("records") or []
                    note_text = (
                        f"Fallback lineup from Rotowire {projection_summary.get('site') or ''} "
                        "global slate pool because only one lineup contest was available."
                    ).strip()
        risk_notes = _availability_risk_notes(
            source_records,
            lineup_size=lineup_size,
            real_availability_lookup=real_availability_lookup,
        )
        if risk_notes:
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
                "lineup_candidate_count": len(ranked_players),
                "notes": (
                    "Skipped this lineup contest because a top projected player has "
                    f"injury/availability risk: {', '.join(risk_notes)}."
                ),
            }
            continue
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
        candidate_rows.append(
            _build_lineup_candidate_row(
                post_id=post_id,
                matchup_key="|".join(pair) if pair is not None else post_id,
                ranked_players=ranked_players,
                lineup_size=lineup_size,
                projection_site=projection_summary.get("site") or "",
                candidate_count=len(ranked_players),
                note=note_text,
                entry_order=entry_order,
            )
        )

    candidate_rows.sort(
        key=lambda row: (
            -float(row["lineup_cutoff_gap"]),
            -float(row["lineup_min_rank_gap"]),
            -float(row["lineup_top5_total"]),
            -float(row["lineup_avg_rank_gap"]),
            int(row.get("entry_order") or 0),
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
        row.pop("entry_order", None)
        by_post_id[str(row["post_id"])] = row

    if availability_cache_path is not None:
        save_multiplier_cache(availability_cache_path, availability_cache)

    return by_post_id, projection_summary
