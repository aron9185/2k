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
from poll_market_matcher import MarketRow, build_market_row, load_csv_rows, normalize_stat, team_pair
from provider_betmgm import fetch_rows as fetch_betmgm_rows
from provider_draftkings import fetch_rows as fetch_draftkings_rows
from provider_fanduel import fetch_rows as fetch_fanduel_rows
from realsports_api import build_realsports_client
from recommend_game_feed_polls import (
    _recommend_both_teams_score,
    _recommend_double_chance,
    _player_lookup,
    _recommend_game_spread,
    _recommend_game_total,
    _recommend_team_next_points,
    _recommend_game_winner,
    _recommend_halftime_result,
    _recommend_period_total_yes_no,
    _recommend_period_winner,
    _recommend_player_over_under,
    _recommend_special_or_unpriced,
)


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_MARKETS_CSV = BASE_DIR / "sportsbook_markets_consensus_live.csv"
DEFAULT_SOCCER_MARKETS_CSV = BASE_DIR / "sportsbook_markets_soccer_live.csv"
DEFAULT_OUTPUT = BASE_DIR / "live_poll_vote_recommendations.csv"
DEFAULT_HISTORY = BASE_DIR / "live_poll_vote_history.jsonl"

SUPPORTED_POLL_KINDS = {
    "anytime_play",
    "pick_a_player",
    "player_over_under",
    "game_total",
    "game_winner",
    "teamnextpoints",
    "game_spread",
    "period_winner",
    "teamtowinperiod",
    "minimumperiodtotalpoints",
    "period_total_yes_no",
    "both_teams_score",
    "double_chance",
    "halftime_result",
}

LIVE_POLL_KIND_ALIASES: dict[str, str] = {
    "minimumperiodtotalpoints": "period_total_yes_no",
}

LIVE_POLL_MARKET_FAMILIES: dict[str, set[str]] = {
    "player_over_under": {"player_over_under"},
    "game_total": {"game_total"},
    "game_winner": {"game_winner"},
    "teamnextpoints": {"teamnextpoints", "game_winner"},
    "period_winner": {"game_winner", "game_spread"},
    "teamtowinperiod": {"game_winner", "game_spread"},
    "period_total_yes_no": {"game_total"},
    "game_spread": {"game_spread"},
    "both_teams_score": {"both_teams_score"},
    "double_chance": {"double_chance"},
    "halftime_result": {"halftime_result"},
    "anytime_play": {"player_over_under", "first_basket"},
    "pick_a_player": {"player_over_under", "first_basket"},
    "first_basket": {"first_basket"},
    "player_most_stat": {"player_over_under"},
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
        "--soccer-markets-csv",
        default=str(DEFAULT_SOCCER_MARKETS_CSV),
        help="Optional supplemental soccer sportsbook market CSV used for soccer live poll matching.",
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
        elif key == "betmgm":
            provider_rows, _raw = fetch_betmgm_rows(sports, use_saved_payloads=False)
        else:
            raise RuntimeError(f"Unsupported live-market provider: {provider}")
        provider_counts[key] = len(provider_rows)
        rows.extend(provider_rows)

    deduped_rows = dedupe_market_rows(rows)
    if not deduped_rows:
        raise RuntimeError("No normalized live sportsbook rows were fetched.")
    written = write_market_rows(output_path, deduped_rows, append=False)
    return written, provider_counts


def _ordered_unique_sports(rows: list[dict[str, Any]], *, fallback: list[str]) -> list[str]:
    fallback_set = {str(sport or "").strip().lower() for sport in fallback if str(sport or "").strip()}
    seen: set[str] = set()
    ordered: list[str] = []
    for row in rows:
        sport = str(row.get("sport") or "").strip().lower()
        if not sport or sport in seen:
            continue
        if fallback_set and sport not in fallback_set:
            continue
        seen.add(sport)
        ordered.append(sport)
    return ordered or list(fallback)


def _load_markets(
    path: str | Path,
    *,
    soccer_path: str | Path = "",
) -> list[MarketRow]:
    markets = [build_market_row(row) for row in load_csv_rows(path)]

    soccer_csv = Path(str(soccer_path or "")).expanduser() if soccer_path else None
    if soccer_csv and soccer_csv.exists():
        soccer_rows = [
            build_market_row(row)
            for row in load_csv_rows(soccer_csv)
            if str(row.get("sport") or "").strip().lower() == "soccer"
        ]
        markets.extend(soccer_rows)

    deduped: list[MarketRow] = []
    seen: set[tuple[Any, ...]] = set()
    for market in markets:
        key = (
            market.book,
            market.sport,
            market.market_family,
            market.stat_key,
            market.player_name,
            market.line,
            market.home_team,
            market.away_team,
            market.over_odds,
            market.under_odds,
            market.period,
            market.updated_at.isoformat() if market.updated_at else "",
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(market)
    return deduped


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


def _build_market_indexes(
    markets: list[MarketRow],
) -> tuple[
    dict[str, list[MarketRow]],
    dict[tuple[str, tuple[str, str]], list[MarketRow]],
    dict[tuple[str, tuple[str, str], str], list[MarketRow]],
]:
    by_sport: dict[str, list[MarketRow]] = {}
    by_sport_pair: dict[tuple[str, tuple[str, str]], list[MarketRow]] = {}
    by_sport_pair_family: dict[tuple[str, tuple[str, str], str], list[MarketRow]] = {}
    for market in markets:
        sport = str(market.sport or "").strip().lower()
        if not sport:
            continue
        by_sport.setdefault(sport, []).append(market)
        if market.home_team and market.away_team:
            pair = team_pair(market.home_team, market.away_team)
            by_sport_pair.setdefault((sport, pair), []).append(market)
            by_sport_pair_family.setdefault((sport, pair, str(market.market_family or "")), []).append(market)
    return by_sport, by_sport_pair, by_sport_pair_family


def _entry_team_pair(entry: dict[str, Any], row: dict[str, Any]) -> tuple[str, str] | None:
    game = entry.get("game") or {}
    home_team = str(game.get("homeTeamKey") or row.get("home_team") or "").strip()
    away_team = str(game.get("awayTeamKey") or row.get("away_team") or "").strip()
    if not home_team or not away_team:
        return None
    return team_pair(home_team, away_team)


def _scoped_markets_for_live_entry(
    row: dict[str, Any],
    entry: dict[str, Any],
    *,
    sport: str,
    by_sport: dict[str, list[MarketRow]],
    by_sport_pair: dict[tuple[str, tuple[str, str]], list[MarketRow]],
    by_sport_pair_family: dict[tuple[str, tuple[str, str], str], list[MarketRow]],
) -> list[MarketRow]:
    sport_key = str(sport or "").strip().lower()
    sport_rows = by_sport.get(sport_key, [])
    if not sport_rows:
        return []

    raw_poll_kind = str(row.get("poll_kind") or "").strip().lower()
    poll_kind = LIVE_POLL_KIND_ALIASES.get(raw_poll_kind, raw_poll_kind)
    families = LIVE_POLL_MARKET_FAMILIES.get(poll_kind) or set()
    pair = _entry_team_pair(entry, row)

    scoped_rows: list[MarketRow] = []
    if pair and families:
        seen_ids: set[int] = set()
        for family in families:
            for market in by_sport_pair_family.get((sport_key, pair, family), []):
                marker = id(market)
                if marker in seen_ids:
                    continue
                seen_ids.add(marker)
                scoped_rows.append(market)
        if scoped_rows:
            stat_key = normalize_stat(str(row.get("stat") or ""))
            if stat_key and any(family == "player_over_under" for family in families):
                filtered = [
                    market
                    for market in scoped_rows
                    if market.market_family != "player_over_under" or market.stat_key == stat_key
                ]
                if filtered:
                    scoped_rows = filtered
            return scoped_rows

    if pair:
        pair_rows = by_sport_pair.get((sport_key, pair), [])
        if pair_rows:
            scoped_rows = pair_rows

    if not scoped_rows and families:
        scoped_rows = [market for market in sport_rows if market.market_family in families]

    return scoped_rows or sport_rows


def _live_recommendation_for_kind(
    *,
    poll_kind: str,
    row: dict[str, Any],
    entry: dict[str, Any],
    sport: str,
    markets: list[MarketRow],
    observed_at: str,
    feed: str,
) -> tuple[dict[str, Any], bool]:
    if poll_kind == "player_over_under":
        return _recommend_player_over_under(entry, markets, sport), True
    if poll_kind == "game_total":
        return _recommend_game_total(entry, markets, sport), True
    if poll_kind == "game_winner":
        return _recommend_game_winner(entry, markets, sport), True
    if poll_kind == "teamnextpoints":
        return _recommend_team_next_points(entry, markets, sport), True
    if poll_kind in {"period_winner", "teamtowinperiod"}:
        return _recommend_period_winner(entry, markets, sport), True
    if poll_kind == "period_total_yes_no":
        return _recommend_period_total_yes_no(entry, markets, sport), True
    if poll_kind == "game_spread":
        return _recommend_game_spread(entry, markets, sport), True
    if poll_kind == "both_teams_score":
        return _recommend_both_teams_score(entry, markets, sport), True
    if poll_kind == "double_chance":
        return _recommend_double_chance(entry, markets, sport), True
    if poll_kind == "halftime_result":
        return _recommend_halftime_result(entry, markets, sport), True
    if poll_kind in {"anytime_play", "pick_a_player"}:
        return _recommend_special_or_unpriced(entry, sport, markets=markets), True
    if poll_kind == "player_head_to_head":
        return (
            _unsupported_recommendation(
                row,
                observed_at=observed_at,
                feed=feed,
                note="Live player head-to-head consensus is not wired yet.",
            ),
            False,
        )
    return (
        _unsupported_recommendation(
            row,
            observed_at=observed_at,
            feed=feed,
            note=f"Unsupported live poll kind '{poll_kind or 'unknown'}'.",
        ),
        False,
    )


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
    source_order: int,
) -> dict[str, Any]:
    decorated = dict(recommendation)
    decorated["observed_at"] = observed_at
    decorated["feed"] = feed
    decorated["source"] = "livefeed"
    decorated["source_order"] = int(source_order)
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
    live_rows: list[dict[str, Any]] | None = None,
    raw_entries: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    if live_rows is None or raw_entries is None:
        rows, raw_entries = fetch_live_polls(feed=feed, include_locked=include_locked, limit=limit)
    else:
        rows = live_rows
    client = build_realsports_client()
    game_feed_cache: dict[tuple[str, str], dict[str, Any]] = {}
    observed_at = _iso_now()
    recommendations: list[dict[str, Any]] = []
    by_sport, by_sport_pair, by_sport_pair_family = _build_market_indexes(markets)

    for source_order, (row, raw_entry) in enumerate(zip(rows, raw_entries)):
        raw_poll_kind = str(row.get("poll_kind") or "").strip().lower()
        poll_kind = LIVE_POLL_KIND_ALIASES.get(raw_poll_kind, raw_poll_kind)
        sport = str(row.get("sport") or "").strip().lower()
        game_feed_payload = _load_game_feed_payload(
            game_feed_cache,
            client,
            sport=sport,
            game_id=row.get("game_id"),
        )
        entry = _build_live_entry(row, raw_entry, game_feed_payload)

        sport_rows = by_sport.get(sport, [])
        scoped_markets = _scoped_markets_for_live_entry(
            row,
            entry,
            sport=sport,
            by_sport=by_sport,
            by_sport_pair=by_sport_pair,
            by_sport_pair_family=by_sport_pair_family,
        )
        recommendation, decorate = _live_recommendation_for_kind(
            poll_kind=poll_kind,
            row=row,
            entry=entry,
            sport=sport,
            markets=scoped_markets,
            observed_at=observed_at,
            feed=feed,
        )
        if (
            decorate
            and str(recommendation.get("status") or "") == "no_market"
            and sport_rows
            and scoped_markets is not sport_rows
        ):
            fallback_recommendation, fallback_decorate = _live_recommendation_for_kind(
                poll_kind=poll_kind,
                row=row,
                entry=entry,
                sport=sport,
                markets=sport_rows,
                observed_at=observed_at,
                feed=feed,
            )
            if fallback_decorate and str(fallback_recommendation.get("status") or "") != "no_market":
                recommendation = fallback_recommendation

        if not decorate:
            recommendation["source_order"] = int(source_order)
            recommendations.append(recommendation)
            continue

        recommendations.append(
            _decorate_recommendation(
                recommendation,
                row,
                observed_at=observed_at,
                feed=feed,
                source_order=source_order,
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
        "source_order",
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
        live_rows, raw_entries = fetch_live_polls(
            feed=args.feed,
            include_locked=include_locked,
            limit=args.limit,
        )
        if args.refresh_markets:
            try:
                written, provider_counts = _refresh_markets_csv(
                    providers=providers,
                    sports=_ordered_unique_sports(live_rows, fallback=sports),
                    output_path=args.markets_csv,
                )
                counts_text = ", ".join(f"{provider}={count}" for provider, count in sorted(provider_counts.items()))
                print(f"[iteration {iteration}] refreshed {written} live market rows ({counts_text})")
            except Exception as exc:
                if not Path(args.markets_csv).exists():
                    raise
                print(f"[iteration {iteration}] market refresh failed, using existing CSV: {exc}")

        markets = _load_markets(
            args.markets_csv,
            soccer_path=args.soccer_markets_csv,
        )
        recommendations = recommend_live_rows(
            feed=args.feed,
            markets=markets,
            include_locked=include_locked,
            limit=args.limit,
            live_rows=live_rows,
            raw_entries=raw_entries,
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
