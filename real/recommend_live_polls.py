from __future__ import annotations

import argparse
import csv
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from live_polls import fetch_live_polls
from market_csv import dedupe_market_rows, write_market_rows
from poll_market_matcher import MarketRow, build_market_row, load_csv_rows
from provider_draftkings import fetch_rows as fetch_draftkings_rows
from provider_fanduel import fetch_rows as fetch_fanduel_rows
from realsports_api import build_realsports_client
from recommend_game_feed_polls import (
    _player_lookup,
    _recommend_game_spread,
    _recommend_game_total,
    _recommend_game_winner,
    _recommend_period_winner,
    _recommend_player_over_under,
    _recommend_special_or_unpriced,
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
    "teamtowinperiod",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Poll Real Sports livefeed posts, match supported live polls against "
            "sportsbook consensus, and write EV-based vote recommendations."
        )
    )
    parser.add_argument("--feed", default="all", help="Livefeed segment, e.g. all.")
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
        default="nba,mlb,nhl",
        help="Comma-separated sports to refresh when --refresh-markets is enabled.",
    )
    parser.add_argument(
        "--refresh-markets",
        action="store_true",
        help="Refresh the live sportsbook market CSV before scoring polls.",
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
    return parser.parse_args()


def _parse_csv_arg(value: str) -> list[str]:
    return [part.strip() for part in str(value or "").split(",") if part.strip()]


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


def _to_float(value: Any) -> float | None:
    if value in (None, "", "None"):
        return None
    try:
        return float(value)
    except Exception:
        return None


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _refresh_markets_csv(
    *,
    providers: list[str],
    sports: list[str],
    output_path: str | Path,
) -> tuple[int, dict[str, int]]:
    rows: list[dict[str, Any]] = []
    provider_counts: dict[str, int] = {}
    for provider in providers:
        key = provider.lower()
        if key == "draftkings":
            provider_rows, _raw = fetch_draftkings_rows(sports, use_saved_payloads=False)
        elif key == "fanduel":
            provider_rows, _raw = fetch_fanduel_rows(sports, use_saved_payloads=False)
        else:
            raise RuntimeError(f"Unsupported live-market provider: {provider}")
        provider_counts[key] = len(provider_rows)
        rows.extend(provider_rows)

    deduped_rows = dedupe_market_rows(rows)
    if not deduped_rows:
        raise RuntimeError("No normalized live sportsbook rows were fetched.")
    written = write_market_rows(output_path, deduped_rows, append=False)
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
        payload = client.get_game_feed(game_key, sport=sport_key)
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
) -> list[dict[str, Any]]:
    rows, raw_entries = fetch_live_polls(feed=feed, include_locked=include_locked, limit=limit)
    client = build_realsports_client()
    game_feed_cache: dict[tuple[str, str], dict[str, Any]] = {}
    observed_at = _iso_now()
    recommendations: list[dict[str, Any]] = []

    for row, raw_entry in zip(rows, raw_entries):
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
        elif poll_kind == "game_spread":
            recommendation = _recommend_game_spread(entry, markets, sport)
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
        elif poll_kind == "game_spread":
            recommendation = _unsupported_recommendation(
                row,
                observed_at=observed_at,
                feed=feed,
                note="Live spread consensus is not wired yet.",
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
            str(item.get("sport") or ""),
            str(item.get("locks_at") or item.get("game_time") or ""),
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
        if args.refresh_markets:
            try:
                written, provider_counts = _refresh_markets_csv(
                    providers=providers,
                    sports=sports,
                    output_path=args.markets_csv,
                )
                counts_text = ", ".join(f"{provider}={count}" for provider, count in sorted(provider_counts.items()))
                print(f"[iteration {iteration}] refreshed {written} live market rows ({counts_text})")
            except Exception as exc:
                if not Path(args.markets_csv).exists():
                    raise
                print(f"[iteration {iteration}] market refresh failed, using existing CSV: {exc}")

        markets = _load_markets(args.markets_csv)
        recommendations = recommend_live_rows(
            feed=args.feed,
            markets=markets,
            include_locked=include_locked,
            limit=args.limit,
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
