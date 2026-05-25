from __future__ import annotations

import argparse
import csv
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from live_polls import DEFAULT_LIVEFEED_PAGES, fetch_live_polls
from market_csv import dedupe_market_rows, write_market_rows
from poll_market_matcher import (
    MarketRow,
    build_market_row,
    load_csv_rows,
    normalize_player_name,
    normalize_stat,
    normalize_team,
    team_pair,
)
from provider_draftkings import fetch_rows as fetch_draftkings_rows
from provider_fanduel import fetch_rows as fetch_fanduel_rows
from realsports_api import build_realsports_client
from recommend_game_feed_polls import (
    _player_lookup,
    _recommend_golf_leaderboard_poll,
    _recommend_game_spread,
    _recommend_game_total,
    _recommend_game_winner,
    _recommend_fight_method,
    _recommend_fight_round,
    _recommend_period_winner,
    _recommend_period_total_yes_no,
    _recommend_player_over_under,
    _recommend_special_or_unpriced,
    _recommend_ufc_fighter_stat_winner,
    _recommend_team_next_points,
    _period_market_code,
)


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_MARKETS_CSV = BASE_DIR / "sportsbook_markets_consensus_live.csv"
DEFAULT_OUTPUT = BASE_DIR / "live_poll_vote_recommendations.csv"
DEFAULT_HISTORY = BASE_DIR / "live_poll_vote_history.jsonl"

SUPPORTED_POLL_KINDS = {
    "anytime_play",
    "pick_a_player",
    "player_over_under",
    "game_total",
    "game_winner",
    "game_spread",
    "period_winner",
    "period_total_yes_no",
    "minimumperiodtotalpoints",
    "teamtowinperiod",
    "teamnextpoints",
    "golf_leaderboard",
    "fighter_stat_winner",
    "fight_method",
    "fight_round",
}
MLB_HRR_COMPONENT_STATS = {"hits", "runs", "rbis"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Poll Real Sports livefeed posts, match supported live polls against "
            "sportsbook consensus, and write EV-based vote recommendations."
        )
    )
    parser.add_argument("--feed", default="all", help="Livefeed segment(s), e.g. all or all,golf.")
    parser.add_argument(
        "--markets-csv",
        default=str(DEFAULT_MARKETS_CSV),
        help="Normalized sportsbook market CSV to use for consensus matching.",
    )
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT),
        help="Snapshot CSV output path for the latest live recommendations.",
    )
    parser.add_argument(
        "--history-jsonl",
        default=str(DEFAULT_HISTORY),
        help="Optional append-only JSONL history output. Pass empty string to disable.",
    )
    parser.add_argument(
        "--providers",
        default="draftkings,fanduel",
        help="Comma-separated provider list when refreshing live markets.",
    )
    parser.add_argument(
        "--sports",
        default="nba,mlb,nhl,wnba,soccer,golf,ufc",
        help="Comma-separated sports to refresh when --refresh-markets is enabled.",
    )
    parser.add_argument(
        "--refresh-markets",
        action="store_true",
        help="Refresh the live sportsbook market CSV before scoring polls.",
    )
    parser.add_argument(
        "--min-market-refresh-interval-seconds",
        type=int,
        default=900,
        help=(
            "Skip sportsbook recrawl when --refresh-markets is set but the markets CSV "
            "was updated within this many seconds."
        ),
    )
    parser.add_argument(
        "--poll-seconds",
        type=int,
        default=30,
        help="Seconds to sleep between polling iterations when --iterations is not 1.",
    )
    parser.add_argument(
        "--iterations",
        type=int,
        default=1,
        help="Number of polling iterations. Use 0 to run forever.",
    )
    parser.add_argument(
        "--include-locked",
        action="store_true",
        help="Legacy alias; locked livefeed polls are included by default.",
    )
    parser.add_argument(
        "--unlocked-only",
        action="store_true",
        help="Keep only livefeed polls whose options are not all locked.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Optional max number of livefeed posts to inspect per iteration.",
    )
    parser.add_argument(
        "--pages",
        type=int,
        default=DEFAULT_LIVEFEED_PAGES,
        help="Maximum livefeed pages to inspect per feed segment per iteration.",
    )
    return parser.parse_args()


def _parse_csv_arg(value: str) -> list[str]:
    return [part.strip() for part in str(value or "").split(",") if part.strip()]


def _expand_livefeed_segments(feed: str) -> list[str]:
    segments: list[str] = []
    for segment in _parse_csv_arg(feed) or ["all"]:
        key = segment.strip().lower()
        if key and key not in segments:
            segments.append(key)
    if "all" in segments and "golf" not in segments:
        # Real's all livefeed has not reliably carried golf poll posts.
        segments.append("golf")
    if "all" in segments and "ufc" not in segments:
        segments.append("ufc")
    return segments


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() == "true"


def _to_int(value: Any) -> int | None:
    if value in (None, "", "None"):
        return None
    try:
        return int(float(value))
    except Exception:
        return None


def _sort_int(value: Any, default: int = 999999) -> int:
    parsed = _to_int(value)
    return parsed if parsed is not None else default


def _to_float(value: Any) -> float | None:
    if value in (None, "", "None"):
        return None
    try:
        return float(value)
    except Exception:
        return None


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_utc_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except Exception:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _is_open_live_poll(row: dict[str, Any], observed_at: datetime) -> bool:
    if _to_bool(row.get("is_locked")):
        return False
    locks_at = _parse_utc_datetime(row.get("locks_at"))
    if locks_at is not None and locks_at <= observed_at:
        return False
    return True


def _market_csv_is_fresh(path: str | Path, min_refresh_interval_seconds: int) -> bool:
    if min_refresh_interval_seconds <= 0:
        return False
    csv_path = Path(path)
    if not csv_path.exists():
        return False
    try:
        age_seconds = max(0.0, time.time() - os.path.getmtime(csv_path))
    except Exception:
        return False
    return age_seconds < float(min_refresh_interval_seconds)


def _requirement_needs_market(requirement: dict[str, Any]) -> bool:
    poll_kind = str(requirement.get("poll_kind") or "").strip().lower()
    return poll_kind in SUPPORTED_POLL_KINDS


def _market_matches_requirement_or_fallback(market: MarketRow, requirement: dict[str, Any]) -> bool:
    if _market_matches_live_poll_requirement(market, requirement):
        return True

    poll_kind = str(requirement.get("poll_kind") or "").strip().lower()
    sport = str(requirement.get("sport") or "").strip().lower()
    stat = str(requirement.get("stat") or "").strip().lower()
    if sport == "mlb" and stat == "hitsrunsrbis" and poll_kind == "player_over_under":
        if market.stat_key not in MLB_HRR_COMPONENT_STATS:
            return False
        fallback_requirement = dict(requirement)
        fallback_requirement["stat"] = market.stat_key
        return _market_matches_live_poll_requirement(market, fallback_requirement)

    if not (
        sport == "soccer"
        and stat == "shots"
        and poll_kind in {"anytime_play", "pick_a_player", "player_over_under"}
    ):
        return False
    fallback_requirement = dict(requirement)
    fallback_requirement["stat"] = "goals"
    return _market_matches_live_poll_requirement(market, fallback_requirement)


def _mlb_hrr_component_stats_for_requirement(
    markets: list[MarketRow],
    requirement: dict[str, Any],
) -> set[str]:
    stats: set[str] = set()
    for market in markets:
        if market.stat_key not in MLB_HRR_COMPONENT_STATS:
            continue
        fallback_requirement = dict(requirement)
        fallback_requirement["stat"] = market.stat_key
        if _market_matches_live_poll_requirement(market, fallback_requirement):
            stats.add(market.stat_key)
    return stats


def _market_list_covers_requirement(markets: list[MarketRow], requirement: dict[str, Any]) -> bool:
    if any(_market_matches_live_poll_requirement(market, requirement) for market in markets):
        return True

    poll_kind = str(requirement.get("poll_kind") or "").strip().lower()
    sport = str(requirement.get("sport") or "").strip().lower()
    stat = str(requirement.get("stat") or "").strip().lower()
    if sport == "mlb" and stat == "hitsrunsrbis" and poll_kind == "player_over_under":
        return len(_mlb_hrr_component_stats_for_requirement(markets, requirement)) >= 2

    return any(_market_matches_requirement_or_fallback(market, requirement) for market in markets)


def _markets_cover_requirements(markets: list[MarketRow], requirements: list[dict[str, Any]]) -> bool:
    required = [req for req in requirements if _requirement_needs_market(req)]
    if not required:
        return True
    for requirement in required:
        if not _market_list_covers_requirement(markets, requirement):
            return False
    return True


def _market_csv_covers_requirements(path: str | Path, requirements: list[dict[str, Any]]) -> bool:
    csv_path = Path(path)
    if not csv_path.exists():
        return False
    try:
        markets = _load_markets(csv_path)
    except Exception:
        return False
    return _markets_cover_requirements(markets, requirements)


def _fetch_open_live_poll_entries(
    *,
    feed: str,
    include_locked: bool,
    limit: int,
    pages: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
    observed_at = _iso_now()
    observed_at_dt = _parse_utc_datetime(observed_at) or datetime.now(timezone.utc)
    rows: list[dict[str, Any]] = []
    raw_entries: list[dict[str, Any]] = []
    seen_keys: set[tuple[str, str]] = set()
    fetch_count = 0
    first_error: Exception | None = None
    for segment in _expand_livefeed_segments(feed):
        try:
            segment_rows, segment_raw_entries = fetch_live_polls(
                feed=segment,
                include_locked=include_locked,
                limit=limit,
                pages=pages,
            )
            fetch_count += 1
        except Exception as exc:
            first_error = first_error or exc
            print(f"Warning: livefeed segment '{segment}' failed: {exc}", flush=True)
            continue
        for row, raw_entry in zip(segment_rows, segment_raw_entries):
            poll_id = str(row.get("poll_id") or "").strip()
            post_id = str(row.get("post_id") or "").strip()
            key = ("poll", poll_id) if poll_id else ("post", post_id)
            if key[1] and key in seen_keys:
                continue
            if key[1]:
                seen_keys.add(key)
            row_copy = dict(row)
            row_copy["feed_segment"] = segment
            row_copy["source_feed_order"] = row.get("feed_order") or ""
            row_copy["feed_order"] = len(rows)
            rows.append(row_copy)
            raw_entries.append(raw_entry)
    if fetch_count == 0 and first_error is not None:
        raise first_error
    open_rows: list[dict[str, Any]] = []
    open_raw_entries: list[dict[str, Any]] = []
    for row, raw_entry in zip(rows, raw_entries):
        if not _is_open_live_poll(row, observed_at_dt):
            continue
        open_rows.append(row)
        open_raw_entries.append(raw_entry)
    return open_rows, open_raw_entries, observed_at


def _build_live_poll_market_requirements(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    requirements: list[dict[str, Any]] = []
    for row in rows:
        sport = str(row.get("sport") or "").strip().lower()
        if not sport:
            continue
        requirements.append(
            {
                "sport": sport,
                "poll_kind": str(row.get("poll_kind") or "").strip().lower(),
                "home_team": normalize_team(str(row.get("home_team") or "")),
                "away_team": normalize_team(str(row.get("away_team") or "")),
                "player_name": normalize_player_name(str(row.get("player_name") or "")),
                "stat": normalize_stat(str(row.get("stat") or "")),
                "line": _to_float(row.get("line")),
                "period": _period_market_code(
                    sport,
                    str(row.get("period") or "").strip(),
                ),
            }
        )
    return requirements


def _target_team_pairs_by_sport(
    requirements: list[dict[str, Any]],
) -> dict[str, set[tuple[str, str]]]:
    targets: dict[str, set[tuple[str, str]]] = {}
    for requirement in requirements:
        sport = str(requirement.get("sport") or "").strip().lower()
        home_team = normalize_team(str(requirement.get("home_team") or ""))
        away_team = normalize_team(str(requirement.get("away_team") or ""))
        if not sport or not home_team or not away_team:
            continue
        pair = tuple(sorted((home_team, away_team)))
        targets.setdefault(sport, set()).add(pair)
    return targets


def _market_matches_live_poll_requirement(market: MarketRow, requirement: dict[str, Any]) -> bool:
    if market.sport != requirement.get("sport"):
        return False

    req_home = str(requirement.get("home_team") or "").strip()
    req_away = str(requirement.get("away_team") or "").strip()
    if req_home and req_away:
        if team_pair(req_home, req_away) != team_pair(market.home_team, market.away_team):
            return False

    req_period = str(requirement.get("period") or "").strip()
    market_period = str(market.period or "").strip()
    if req_period and market_period and req_period != market_period:
        return False

    poll_kind = str(requirement.get("poll_kind") or "").strip().lower()
    market_family = str(market.market_family or "").strip().lower()

    if poll_kind == "game_total":
        return market_family == "game_total"
    if poll_kind == "game_spread":
        return market_family == "game_spread"
    if poll_kind == "game_winner":
        return market_family == "game_winner"
    if poll_kind in {"period_winner", "teamtowinperiod"}:
        return market_family in {"game_spread", "game_winner"}
    if poll_kind in {"period_total_yes_no", "minimumperiodtotalpoints"}:
        return market_family in {"game_total", "team_period_total"}
    if poll_kind == "teamnextpoints":
        return market_family in {"teamnextpoints", "game_winner"}
    if poll_kind == "golf_leaderboard":
        req_stat = str(requirement.get("stat") or "").strip()
        if req_stat in {"roundscore", "roundmatchup"}:
            return (
                (market_family == "player_finish" and market.stat_key == "roundmatchup")
                or (market_family == "player_over_under" and market.stat_key == "roundscore")
            )
        if req_stat:
            return market_family == "player_finish" and market.stat_key == req_stat
        return market_family == "player_finish"
    if poll_kind == "fighter_stat_winner":
        if market_family != "fighter_stat_winner":
            return False
        req_stat = str(requirement.get("stat") or "").strip()
        if req_stat and market.stat_key and market.stat_key != req_stat:
            return False
        return True
    if poll_kind == "fight_method":
        return market_family == "fight_method"
    if poll_kind == "fight_round":
        return market_family == "fight_round"
    if poll_kind == "player_over_under":
        if market_family != "player_over_under":
            return False
        req_stat = str(requirement.get("stat") or "").strip()
        if req_stat and market.stat_key and market.stat_key != req_stat:
            return False
        req_player = str(requirement.get("player_name") or "").strip()
        if req_player and market.player_name and market.player_name != req_player:
            return False
        return True
    if poll_kind in {"anytime_play", "pick_a_player"}:
        if market_family not in {"player_over_under", "first_basket"}:
            return False
        req_stat = str(requirement.get("stat") or "").strip()
        if req_stat and market.stat_key and market.stat_key != req_stat:
            return False
        return True

    return True


def _filter_market_rows_for_live_polls(
    rows: list[dict[str, Any]],
    requirements: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not requirements:
        return rows
    filtered: list[dict[str, Any]] = []
    for row in rows:
        market = build_market_row(row)
        if any(_market_matches_requirement_or_fallback(market, req) for req in requirements):
            filtered.append(row)
    return filtered


def _refresh_markets_csv(
    *,
    providers: list[str],
    sports: list[str],
    output_path: str | Path,
    requirements: list[dict[str, Any]] | None = None,
) -> tuple[int, dict[str, int]]:
    rows: list[dict[str, Any]] = []
    provider_counts: dict[str, int] = {}
    requirements = requirements or []
    target_pairs_by_sport = _target_team_pairs_by_sport(requirements)
    requires_full_scope = any(
        str(req.get("poll_kind") or "").strip().lower()
        in {
            "player_over_under",
            "anytime_play",
            "pick_a_player",
            "player_head_to_head",
            "teamnextpoints",
            "golf_leaderboard",
            "fighter_stat_winner",
            "fight_method",
            "fight_round",
        }
        for req in requirements
    )
    market_scope = "all" if requires_full_scope else "game-lines"
    for provider in providers:
        key = provider.lower()
        if key == "draftkings":
            provider_rows, _raw = fetch_draftkings_rows(
                sports,
                use_saved_payloads=False,
                save_payloads=False,
                market_scope=market_scope,
                target_team_pairs_by_sport=target_pairs_by_sport,
            )
        elif key == "fanduel":
            provider_rows, _raw = fetch_fanduel_rows(
                sports,
                use_saved_payloads=False,
                save_payloads=False,
                market_scope=market_scope,
                target_team_pairs_by_sport=target_pairs_by_sport,
            )
        else:
            raise RuntimeError(f"Unsupported live-market provider: {provider}")
        if requirements:
            filtered_rows = _filter_market_rows_for_live_polls(provider_rows, requirements)
            if filtered_rows:
                filtered_markets = [build_market_row(row) for row in filtered_rows]
                kept_rows = filtered_rows if _markets_cover_requirements(filtered_markets, requirements) else provider_rows
            else:
                kept_rows = provider_rows
        else:
            kept_rows = provider_rows
        provider_counts[key] = len(kept_rows)
        rows.extend(kept_rows)

    deduped_rows = dedupe_market_rows(rows)
    if not deduped_rows:
        raise RuntimeError("No normalized live sportsbook rows were fetched.")
    written = write_market_rows(output_path, deduped_rows, append=False)
    provider_counts["_market_scope"] = market_scope
    return written, provider_counts


def _load_markets(path: str | Path) -> list[MarketRow]:
    return [build_market_row(row) for row in load_csv_rows(path)]


def _load_game_feed_payload(
    cache: dict[tuple[str, str], dict[str, Any]],
    client: Any,
    *,
    sport: str,
    game_id: Any,
) -> dict[str, Any]:
    sport_key = str(sport or "").strip().lower()
    game_key = str(game_id or "").strip()
    if not sport_key or not game_key:
        return {}
    cache_key = (sport_key, game_key)
    if cache_key in cache:
        return cache[cache_key]
    try:
        payload = client.get_game_feed(
            game_key,
            sport=sport_key,
            view="all" if sport_key == "nhl" else "recent",
        )
        cache[cache_key] = payload if isinstance(payload, dict) else {}
    except Exception:
        cache[cache_key] = {}
    return cache[cache_key]


def _game_payload_from_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row.get("game_id") or "",
        "dateTime": row.get("locks_at") or row.get("poll_created_at") or row.get("created_at") or "",
        "homeTeamKey": row.get("home_team") or "",
        "awayTeamKey": row.get("away_team") or "",
    }


def _build_live_entry(
    row: dict[str, Any],
    raw_entry: dict[str, Any],
    game_feed_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    post = dict((raw_entry.get("post") or {}))
    poll = dict(((raw_entry.get("poll_payload") or {}).get("poll") or {}))
    game_payload = game_feed_payload or {}
    player_lookup: dict[str, str] = _player_lookup(game_payload.get("players") or [])
    player_id = str(row.get("player_id") or "").strip()
    player_name = str(row.get("player_name") or "").strip()
    if player_id and player_name:
        player_lookup[player_id] = player_name
    return {
        "game": (game_payload.get("game") or {}) or _game_payload_from_row(row),
        "game_payload": game_payload,
        "post": post,
        "poll": poll,
        "player_lookup": player_lookup,
        "live_row": row,
    }


def _unsupported_recommendation(
    row: dict[str, Any],
    *,
    observed_at: str,
    feed: str,
    note: str,
) -> dict[str, Any]:
    option_a_label = str(row.get("option_1_label") or "").strip()
    option_b_label = str(row.get("option_2_label") or "").strip()
    return {
        "observed_at": observed_at,
        "feed": feed,
        "source": "livefeed",
        "feed_order": row.get("feed_order") or "",
        "day": row.get("day") or "",
        "sport": row.get("sport") or "",
        "game_id": row.get("game_id") or "",
        "game_time": row.get("locks_at") or "",
        "locks_at": row.get("locks_at") or "",
        "home_team": row.get("home_team") or "",
        "away_team": row.get("away_team") or "",
        "post_id": row.get("post_id") or "",
        "poll_id": row.get("poll_id") or "",
        "created_at": row.get("created_at") or "",
        "poll_created_at": row.get("poll_created_at") or "",
        "header": row.get("header") or "",
        "content_text": row.get("content_text") or "",
        "poll_kind": row.get("poll_kind") or "",
        "player_name": row.get("player_name") or "",
        "stat": row.get("stat") or "",
        "line": row.get("line") or "",
        "can_wager": row.get("can_wager") or "",
        "max_wager": row.get("max_wager") or "",
        "option_a_label": option_a_label,
        "option_a_odds": row.get("option_1_odds") or "",
        "option_a_count": row.get("option_1_count") or "",
        "option_b_label": option_b_label,
        "option_b_odds": row.get("option_2_odds") or "",
        "option_b_count": row.get("option_2_count") or "",
        "sportsbook_a_label": option_a_label,
        "sportsbook_a_odds": "",
        "sportsbook_b_label": option_b_label,
        "sportsbook_b_odds": "",
        "sportsbook_c_label": "",
        "sportsbook_c_odds": "",
        "status": "unsupported",
        "recommended_option": "",
        "recommended_amount": 0,
        "stake_fraction_of_max": 0.0,
        "recommended_ev_percent": "",
        "fair_prob": "",
        "fair_odds": "",
        "consensus_fair_line": "",
        "matched_books": 0,
        "books": "",
        "notes": note,
    }


def _decorate_recommendation(
    recommendation: dict[str, Any],
    row: dict[str, Any],
    *,
    observed_at: str,
    feed: str,
) -> dict[str, Any]:
    decorated = dict(recommendation)
    decorated["observed_at"] = observed_at
    decorated["feed"] = feed
    decorated["source"] = "livefeed"
    decorated["feed_order"] = row.get("feed_order") or ""
    decorated["day"] = row.get("day") or decorated.get("day") or ""
    decorated["poll_kind"] = row.get("poll_kind") or decorated.get("poll_kind") or ""
    decorated["option_a_count"] = row.get("option_1_count") or ""
    decorated["option_b_count"] = row.get("option_2_count") or ""
    decorated["created_at"] = row.get("created_at") or ""
    decorated["poll_created_at"] = row.get("poll_created_at") or ""
    decorated["locks_at"] = row.get("locks_at") or decorated.get("game_time") or ""
    return decorated


def recommend_live_rows(
    *,
    feed: str,
    markets: list[MarketRow],
    include_locked: bool = True,
    limit: int = 0,
    pages: int = DEFAULT_LIVEFEED_PAGES,
    prefetched_rows: list[dict[str, Any]] | None = None,
    prefetched_raw_entries: list[dict[str, Any]] | None = None,
    observed_at: str = "",
) -> list[dict[str, Any]]:
    client = build_realsports_client()
    game_feed_cache: dict[tuple[str, str], dict[str, Any]] = {}
    observed_at = observed_at or _iso_now()
    observed_at_dt = _parse_utc_datetime(observed_at) or datetime.now(timezone.utc)
    if prefetched_rows is not None and prefetched_raw_entries is not None:
        rows = list(prefetched_rows)
        raw_entries = list(prefetched_raw_entries)
    else:
        rows, raw_entries = fetch_live_polls(
            feed=feed,
            include_locked=include_locked,
            limit=limit,
            pages=pages,
        )
    recommendations: list[dict[str, Any]] = []

    for row, raw_entry in zip(rows, raw_entries):
        if not _is_open_live_poll(row, observed_at_dt):
            continue
        poll_kind = str(row.get("poll_kind") or "").strip()
        sport = str(row.get("sport") or "").strip().lower()
        game_feed_payload = _load_game_feed_payload(
            game_feed_cache,
            client,
            sport=sport,
            game_id=row.get("game_id"),
        )
        entry = _build_live_entry(row, raw_entry, game_feed_payload)

        if poll_kind == "player_over_under":
            recommendation = _recommend_player_over_under(entry, markets, sport)
        elif poll_kind == "game_total":
            recommendation = _recommend_game_total(entry, markets, sport)
        elif poll_kind == "game_winner":
            recommendation = _recommend_game_winner(entry, markets, sport)
        elif poll_kind in {"period_winner", "teamtowinperiod"}:
            recommendation = _recommend_period_winner(entry, markets, sport)
        elif poll_kind in {"period_total_yes_no", "minimumperiodtotalpoints"}:
            recommendation = _recommend_period_total_yes_no(entry, markets, sport)
        elif poll_kind == "game_spread":
            recommendation = _recommend_game_spread(entry, markets, sport)
        elif poll_kind == "teamnextpoints":
            recommendation = _recommend_team_next_points(entry, markets, sport)
        elif poll_kind == "golf_leaderboard":
            recommendation = _recommend_golf_leaderboard_poll(entry, markets, sport)
        elif poll_kind == "fighter_stat_winner":
            recommendation = _recommend_ufc_fighter_stat_winner(entry, markets, sport)
        elif poll_kind == "fight_method":
            recommendation = _recommend_fight_method(entry, markets, sport)
        elif poll_kind == "fight_round":
            recommendation = _recommend_fight_round(entry, markets, sport)
        elif poll_kind in {"anytime_play", "pick_a_player"}:
            recommendation = _recommend_special_or_unpriced(entry, sport, markets=markets)
        elif poll_kind == "player_head_to_head":
            recommendation = _unsupported_recommendation(
                row,
                observed_at=observed_at,
                feed=feed,
                note="Live player head-to-head consensus is not wired yet.",
            )
            recommendations.append(recommendation)
            continue
        else:
            recommendation = _unsupported_recommendation(
                row,
                observed_at=observed_at,
                feed=feed,
                note=f"Unsupported live poll kind '{poll_kind or 'unknown'}'.",
            )
            recommendations.append(recommendation)
            continue

        recommendations.append(
            _decorate_recommendation(
                recommendation,
                row,
                observed_at=observed_at,
                feed=feed,
            )
        )

    recommendations.sort(
        key=lambda item: (
            str(item.get("locks_at") or item.get("game_time") or ""),
            _sort_int(item.get("feed_order")),
            str(item.get("sport") or ""),
            str(item.get("poll_created_at") or item.get("created_at") or ""),
            str(item.get("poll_id") or ""),
        )
    )
    return recommendations


def write_snapshot_csv(path: str | Path, rows: list[dict[str, Any]]) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "observed_at",
        "feed",
        "source",
        "feed_order",
        "day",
        "sport",
        "game_id",
        "game_time",
        "locks_at",
        "home_team",
        "away_team",
        "post_id",
        "poll_id",
        "created_at",
        "poll_created_at",
        "header",
        "content_text",
        "poll_kind",
        "player_name",
        "stat",
        "line",
        "can_wager",
        "max_wager",
        "option_a_label",
        "option_a_odds",
        "option_a_count",
        "option_b_label",
        "option_b_odds",
        "option_b_count",
        "option_c_label",
        "option_c_odds",
        "sportsbook_a_label",
        "sportsbook_a_odds",
        "sportsbook_b_label",
        "sportsbook_b_odds",
        "sportsbook_c_label",
        "sportsbook_c_odds",
        "status",
        "recommended_option",
        "recommended_amount",
        "stake_fraction_of_max",
        "recommended_ev_percent",
        "fair_prob",
        "fair_odds",
        "consensus_fair_line",
        "matched_books",
        "books",
        "odds_updated_at",
        "source_lines",
        "player_choices_json",
        "option_choices_json",
        "notes",
    ]
    with output_path.open("w", newline="", encoding="utf8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def append_history_jsonl(path: str | Path, rows: list[dict[str, Any]]) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("a", encoding="utf8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _print_iteration_summary(
    *,
    iteration: int,
    rows: list[dict[str, Any]],
    markets_path: str | Path,
) -> None:
    supported = sum(1 for row in rows if str(row.get("poll_kind") or "") in SUPPORTED_POLL_KINDS)
    bet_count = sum(1 for row in rows if str(row.get("status") or "") == "bet")
    pick_count = sum(1 for row in rows if str(row.get("status") or "") == "pick")
    no_market_count = sum(1 for row in rows if str(row.get("status") or "") == "no_market")
    unsupported_count = sum(1 for row in rows if str(row.get("status") or "") == "unsupported")
    print(
        f"[iteration {iteration}] saved {len(rows)} live recommendations "
        f"({supported} supported, {bet_count} bet, {pick_count} pick, "
        f"{no_market_count} no_market, {unsupported_count} unsupported) "
        f"using {markets_path}"
    )


def main() -> None:
    args = parse_args()
    providers = _parse_csv_arg(args.providers)
    sports = _parse_csv_arg(args.sports)
    include_locked = args.include_locked or not args.unlocked_only
    iteration = 0

    while True:
        iteration += 1
        open_rows, open_raw_entries, observed_at = _fetch_open_live_poll_entries(
            feed=args.feed,
            include_locked=include_locked,
            limit=args.limit,
            pages=args.pages,
        )
        requirements = _build_live_poll_market_requirements(open_rows)
        if args.refresh_markets:
            refresh_sports = sorted(
                {
                    str(req.get("sport") or "").strip().lower()
                    for req in requirements
                    if str(req.get("sport") or "").strip()
                }
            )
            if refresh_sports:
                should_refresh = True
                if _market_csv_is_fresh(args.markets_csv, int(args.min_market_refresh_interval_seconds)):
                    if _market_csv_covers_requirements(args.markets_csv, requirements):
                        print(
                            f"[iteration {iteration}] markets CSV is fresh and covers open polls; skipped sportsbook recrawl.",
                        )
                        should_refresh = False
                    else:
                        print(
                            f"[iteration {iteration}] markets CSV is fresh but missing some open-poll market coverage; "
                            "running a targeted sportsbook recrawl now.",
                        )
                if should_refresh:
                    try:
                        written, provider_counts = _refresh_markets_csv(
                            providers=providers,
                            sports=refresh_sports,
                            output_path=args.markets_csv,
                            requirements=requirements,
                        )
                        market_scope = str(provider_counts.pop("_market_scope", "all"))
                        counts_text = ", ".join(
                            f"{provider}={count}" for provider, count in sorted(provider_counts.items())
                        )
                        print(
                            f"[iteration {iteration}] refreshed {written} targeted live market rows "
                            f"for sports={','.join(refresh_sports)} scope={market_scope} ({counts_text})"
                        )
                    except Exception as exc:
                        if not Path(args.markets_csv).exists():
                            raise
                        print(f"[iteration {iteration}] targeted market refresh failed, using existing CSV: {exc}")
            else:
                print(
                    f"[iteration {iteration}] no open live polls found; skipped sportsbook market crawl.",
                )

        markets = _load_markets(args.markets_csv)
        recommendations = recommend_live_rows(
            feed=args.feed,
            markets=markets,
            include_locked=include_locked,
            limit=args.limit,
            pages=args.pages,
            prefetched_rows=open_rows,
            prefetched_raw_entries=open_raw_entries,
            observed_at=observed_at,
        )
        write_snapshot_csv(args.output, recommendations)
        if args.history_jsonl:
            append_history_jsonl(args.history_jsonl, recommendations)
        _print_iteration_summary(
            iteration=iteration,
            rows=recommendations,
            markets_path=args.markets_csv,
        )

        if args.iterations == 1:
            break
        if args.iterations > 1 and iteration >= args.iterations:
            break
        time.sleep(max(1, int(args.poll_seconds)))


if __name__ == "__main__":
    main()
