from __future__ import annotations

import argparse
import csv
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from fair_odds import (
    MarketQuote,
    american_to_implied_prob,
    consensus_snapshot,
    devig_quote,
    net_payout_per_unit,
    probability_to_american,
)
from lineup_contest_polls import (
    build_lineup_contest_rankings,
    is_lineup_contest_post,
    lineup_contest_additional,
)
from mlb_special_polls import load_optimal_projections, projected_hitter_candidates
from nba_team_stat_polls import (
    TEAM_STAT_ID_METRICS,
    TEAM_STAT_METRICS,
    recommend_nba_team_stat,
)
from nhl_team_stat_polls import recommend_nhl_team_stat
from poll_market_matcher import (
    MarketRow,
    build_market_row,
    clean_player_name,
    load_csv_rows,
    normalize_player_name,
    normalize_stat,
    normalize_team,
    normalize_text,
    team_pair,
)
from realsports_api import build_realsports_client


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_MARKETS_CSV = BASE_DIR / "sportsbook_markets.csv"
DEFAULT_OUTPUT = BASE_DIR / "poll_vote_recommendations.csv"
NBA_SPLIT_STAT_TYPES = {
    "points": 1,
    "rebounds": 2,
    "assists": 3,
    "steals": 4,
    "blocks": 5,
    "madethrees": 21,
}
WNBA_SPLIT_STAT_TYPES = dict(NBA_SPLIT_STAT_TYPES)
NHL_SPLIT_STAT_TYPES = {
    "points": 1,
    "goals": 2,
    "assists": 3,
    "powerplaypoints": 8,
    "shots": 11,
    "hits": 60,
}
MLB_HRR_COMPONENT_STATS = ("hits", "runs", "rbis")
ZERO_PUT_WIN_WAGER = 10.0
ZERO_COST_PLAYER_POLL_KINDS = {"anytime_play", "player_most_stat", "first_basket"}
UNPRICED_POLL_KINDS = ZERO_COST_PLAYER_POLL_KINDS | {"team_stat", "golf_leaderboard"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Fetch the active-day single-game Real Sports feeds for one sport, "
            "match supported wager polls against sportsbook markets, and "
            "recommend which option to vote on with a 0-max size."
        )
    )
    parser.add_argument("--sport", default="mlb", help="Sport key such as mlb.")
    parser.add_argument(
        "--day",
        default="",
        help="Optional day filter such as 2026-04-22. Defaults to the active day from the sport home tab.",
    )
    parser.add_argument("--markets-csv", default=str(DEFAULT_MARKETS_CSV))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument(
        "--include-nonwagerable",
        action="store_true",
        help="Keep non-wagerable or unresolved polls like anytime cards with no options yet.",
    )
    return parser.parse_args()


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


def _extract_poll_id(post: dict[str, Any]) -> int | None:
    additional_info = post.get("additionalInfo") or {}
    poll_id = additional_info.get("pollId")
    if poll_id:
        try:
            return int(poll_id)
        except Exception:
            pass

    nodes = ((post.get("content") or {}).get("nodes")) or []
    for node in nodes:
        poll_id = node.get("pollId")
        if poll_id:
            try:
                return int(poll_id)
            except Exception:
                return None
    return None


def _extract_poll_ids(post: dict[str, Any]) -> list[int]:
    additional_info = post.get("additionalInfo") or {}
    values = additional_info.get("pollIds")
    poll_ids: list[int] = []
    if isinstance(values, list):
        for value in values:
            try:
                poll_ids.append(int(value))
            except Exception:
                continue
    if poll_ids:
        return poll_ids

    nodes = ((post.get("content") or {}).get("nodes")) or []
    for node in nodes:
        poll_id = node.get("pollId")
        if poll_id in (None, "", "None"):
            continue
        try:
            poll_ids.append(int(poll_id))
        except Exception:
            continue
    return poll_ids


def _contest_payload_from_post(post: dict[str, Any]) -> dict[str, Any]:
    additional = lineup_contest_additional(post)
    contest_id = additional.get("contestId") or post.get("contestId") or ""
    return {
        "id": contest_id,
        "canWager": False,
        "maxWager": 0,
        "additionalInfo": additional,
        "options": [],
    }


def _entry_game_label(entry: dict[str, Any]) -> str:
    game = entry.get("game") or {}
    away_team = str(game.get("awayTeamKey") or "").strip()
    home_team = str(game.get("homeTeamKey") or "").strip()
    if away_team and home_team and away_team != home_team:
        return f"{away_team} @ {home_team}"

    metadata = game.get("metadata") or {}
    title = str(metadata.get("title") or "").strip()
    subtitle_fragments = [
        str(fragment).strip()
        for fragment in (metadata.get("subtitleFragments") or [])
        if str(fragment).strip()
    ]
    if title and subtitle_fragments:
        return f"{title} - {' - '.join(subtitle_fragments)}"
    if title:
        return title

    display = game.get("display") or {}
    primary = str(display.get("primary") or "").strip()
    secondary = str(display.get("secondary") or "").strip()
    if primary and secondary:
        return f"{primary} {secondary}".strip()
    if primary:
        return primary
    return ""


def _infer_player_name_from_text(content_text: str) -> str:
    text = str(content_text or "").strip()
    if not text:
        return ""
    marker = " to "
    if marker in text:
        return text.split(marker, 1)[0].strip()
    if " · " in text:
        return text.split(" · ", 1)[0].strip()
    if " - " in text:
        left = text.split(" - ", 1)[0].strip()
        if left:
            return left
    line_match = re.search(r"\b\d+(?:\.\d+)?\b", text)
    if line_match:
        prefix = text[: line_match.start()].strip()
        prefix = re.sub(r"[?·•|:;/\\\-]+\s*$", "", prefix).strip()
        if prefix:
            return prefix
    return ""


def _extract_option_flags(option: dict[str, Any]) -> tuple[bool | None, bool | None]:
    additional = option.get("additionalInfo") or {}
    over_flag = additional.get("over")
    if over_flag is True:
        return True, False
    if over_flag is False:
        return False, True

    label = str(option.get("label") or "").strip().lower()
    if label == "over":
        return True, False
    if label == "under":
        return False, True
    if label == "yes":
        return True, False
    if label == "no":
        return False, True
    return None, None


def _choose_over_under_options(options: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
    over_option: dict[str, Any] | None = None
    under_option: dict[str, Any] | None = None
    for option in options:
        is_over, is_under = _extract_option_flags(option)
        if is_over:
            over_option = option
        if is_under:
            under_option = option
    return over_option or {}, under_option or {}


def _normalize_option_odds(option: dict[str, Any]) -> int | None:
    if not option:
        return None
    additional = option.get("additionalInfo") or {}
    odds = additional.get("odds")
    if odds in (None, "", "None"):
        odds = option.get("odds")
    if odds in (None, "", "None"):
        return 100
    try:
        return int(odds)
    except Exception:
        try:
            return int(float(odds))
        except Exception:
            return None


def _is_no_more_outcome_label(value: Any) -> bool:
    token = normalize_text(str(value or "")).replace(" ", "")
    if token in {
        "nomore",
        "none",
        "norun",
        "noruns",
        "noscore",
        "nogoal",
        "nogoals",
        "nopoint",
        "nopoints",
    }:
        return True
    return token.startswith("nomore")


def _player_lookup(players: list[dict[str, Any]]) -> dict[str, str]:
    lookup: dict[str, str] = {}
    for player in players or []:
        player_id = str(player.get("id") or "").strip()
        if not player_id:
            continue
        first = str(player.get("firstName") or "").strip()
        last = str(player.get("lastName") or "").strip()
        full_name = " ".join(part for part in (first, last) if part).strip()
        if full_name:
            lookup[player_id] = full_name
    return lookup


def _poll_kind(
    additional: dict[str, Any],
    *,
    poll: dict[str, Any] | None = None,
    post: dict[str, Any] | None = None,
) -> str:
    poll = poll or {}
    post = post or {}
    poll_type = str(additional.get("type") or "").strip().lower()
    play_types = additional.get("playTypes") or []
    text_bits = [
        str(additional.get("playType") or "").strip().lower(),
        " ".join(str(item).strip().lower() for item in play_types if str(item).strip()),
        normalize_text(str(post.get("header") or "")),
        normalize_text(_first_text(((post.get("content") or {}).get("nodes")) or [])),
    ]
    combined_text = " ".join(part for part in text_bits if part)
    if "first field goal" in combined_text or "first basket" in combined_text:
        return "first_basket"
    if poll_type == "daily" and additional.get("isMostStat"):
        return "player_most_stat"
    if poll_type == "player" and additional.get("isOverUnder"):
        return "player_over_under"
    if poll_type == "player" and additional.get("isNextPoints"):
        return "first_basket"
    if poll_type == "player" and additional.get("isAnytimePlay"):
        return "anytime_play"
    if poll_type == "player" and additional.get("isMostStat"):
        return "player_most_stat"
    if poll_type == "player" and additional.get("isLeaderboardPoll"):
        sport_key = str(additional.get("sport") or poll.get("sport") or "").strip().lower()
        if sport_key == "golf":
            return "golf_leaderboard"
    if poll_type == "teamstat":
        return "team_stat"
    if poll_type == "totaloverunder":
        return "game_total"
    if poll_type == "gamewinner":
        spread_bits = [
            additional.get("pointSpread"),
            additional.get("spreadTeamId"),
            poll.get("pointSpread"),
            poll.get("spreadTeamId"),
        ]
        if any(value not in (None, "", "None") for value in spread_bits):
            return "game_spread"
        if "cover the spread" in combined_text or " point spread" in combined_text:
            return "game_spread"
        if combined_text.startswith("spread ") or " spread" in combined_text:
            return "game_spread"
        return "game_winner"
    if poll_type == "teamnextpoints":
        return "teamnextpoints"
    if poll_type == "teamtowinperiod":
        return "period_winner"
    if poll_type == "minimumperiodtotalpoints":
        return "period_total_yes_no"
    if poll_type == "bothteamsscore":
        return "both_teams_score"
    if poll_type == "doublechance":
        return "double_chance"
    if poll_type == "halftimeresult":
        return "halftime_result"
    if poll_type == "playerratingcontest":
        return "contest"
    return poll_type or "unknown"


def _evaluate_binary_offer(fair_prob: float, offered_odds: int) -> dict[str, float | int]:
    payout = net_payout_per_unit(offered_odds)
    lose_prob = 1.0 - fair_prob
    ev_per_unit = (fair_prob * payout) - lose_prob
    kelly_full = max(0.0, ((payout * fair_prob) - lose_prob) / payout)
    return {
        "offered_implied_prob": american_to_implied_prob(offered_odds),
        "fair_prob": fair_prob,
        "fair_odds": probability_to_american(fair_prob),
        "ev_per_unit": ev_per_unit,
        "ev_percent": ev_per_unit * 100.0,
        "kelly_fraction_quarter": kelly_full * 0.25,
    }


def _max_wager_amount(max_wager: int | float | None) -> int:
    if max_wager in (None, "", "None"):
        return 0
    try:
        max_value = max(0.0, float(max_wager))
    except Exception:
        return 0
    return int(max_value)


def _zero_put_expected_value(fair_prob: float) -> float:
    return max(0.0, float(fair_prob)) * ZERO_PUT_WIN_WAGER


def _evaluation_value(evaluation: Any, field: str) -> Any:
    if isinstance(evaluation, dict):
        return evaluation.get(field)
    return getattr(evaluation, field, None)


def _choose_zero_or_max_action(
    max_wager: int | float | None,
    evaluations: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if not evaluations:
        return None

    max_amount = _max_wager_amount(max_wager)
    candidates: list[dict[str, Any]] = []
    for item in evaluations:
        evaluation = item.get("evaluation") or {}
        fair_prob = float(_evaluation_value(evaluation, "fair_prob") or 0.0)
        candidates.append(
            {
                **item,
                "amount": 0,
                "stake_fraction": 0.0,
                "status": "pick",
                "expected_value": _zero_put_expected_value(fair_prob),
            }
        )
        if max_amount > 0:
            candidates.append(
                {
                    **item,
                    "amount": max_amount,
                    "stake_fraction": 1.0,
                    "status": "bet",
                    "expected_value": max_amount * float(_evaluation_value(evaluation, "ev_per_unit") or 0.0),
                }
            )

    return max(
        candidates,
        key=lambda item: (
            float(item.get("expected_value") or 0.0),
            int(item.get("amount") or 0),
            float(_evaluation_value(item.get("evaluation") or {}, "fair_prob") or 0.0),
            str(item.get("label") or ""),
        ),
    )


def _status_from_wager(ev_per_unit: float, stake_amount: int) -> str:
    return "bet" if ev_per_unit > 0 and stake_amount > 0 else "no_edge"


def _build_game_winner_options(
    poll: dict[str, Any],
    game: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    home_team = normalize_team(poll.get("homeTeamKey") or game.get("homeTeamKey") or "")
    away_team = normalize_team(poll.get("awayTeamKey") or game.get("awayTeamKey") or "")
    options = poll.get("options") or []
    result: dict[str, dict[str, Any]] = {}
    for option in options:
        label = str(option.get("label") or "").strip()
        normalized_label = normalize_team(label)
        payload = {
            "label": label,
            "odds": _normalize_option_odds(option),
        }
        if normalized_label == home_team:
            result["home"] = payload
        elif normalized_label == away_team:
            result["away"] = payload
    return result


def _recommend_multi_outcome(
    entry: dict[str, Any],
    markets: list[MarketRow],
    sport: str,
    *,
    market_family: str,
    poll_kind: str,
    stat: str,
    period: str = "",
    notes_prefix: str,
) -> dict[str, Any]:
    game = entry["game"]
    post = entry["post"]
    poll = entry["poll"]
    options = poll.get("options") or []
    option_fields = _option_fields(options)
    base = {
        "poll_id": poll.get("id"),
        "post_id": post.get("id"),
        "sport": sport,
        "game_id": game.get("id"),
        "game_time": game.get("dateTime") or "",
        "home_team": game.get("homeTeamKey") or "",
        "away_team": game.get("awayTeamKey") or "",
        "header": post.get("header") or "",
        "content_text": _first_text(((post.get("content") or {}).get("nodes")) or []),
        "poll_kind": poll_kind,
        "player_name": "",
        "stat": stat,
        "line": "",
        "can_wager": poll.get("canWager"),
        "max_wager": poll.get("maxWager", ""),
        **option_fields,
        **_book_outcome_fields([]),
    }

    game_quotes = _matching_game_quotes(
        markets,
        sport=sport,
        game=game,
        market_family=market_family,
        period=period,
    )
    representative_market = _representative_game_market(game_quotes)
    if not representative_market:
        return {
            **base,
            "status": "no_market",
            "recommended_option": "",
            "recommended_amount": 0,
            "stake_fraction_of_max": 0.0,
            "recommended_ev_percent": "",
            "fair_prob": "",
            "fair_odds": "",
            "matched_books": 0,
            "books": "",
            "notes": f"No matching sportsbook {notes_prefix} quotes found.",
        }

    book_outcomes = _market_extra_outcomes(representative_market)
    keyed_book_outcomes: dict[str, dict[str, Any]] = {}
    for outcome in book_outcomes:
        key = _canonical_book_outcome_key(outcome, representative_market)
        odds = _normalize_option_odds({"odds": outcome.get("odds")})
        if key and odds is not None:
            keyed_book_outcomes[key] = {**outcome, "odds": odds}
    if not keyed_book_outcomes:
        return {
            **base,
            "status": "no_market",
            "recommended_option": "",
            "recommended_amount": 0,
            "stake_fraction_of_max": 0.0,
            "recommended_ev_percent": "",
            "fair_prob": "",
            "fair_odds": "",
            "matched_books": len(game_quotes),
            "books": " | ".join(sorted({market.book for market in game_quotes})),
            "notes": f"Matching {notes_prefix} rows were missing outcome odds.",
        }

    implied_total = sum(american_to_implied_prob(outcome["odds"]) for outcome in keyed_book_outcomes.values())
    if implied_total <= 0:
        implied_total = 1.0
    evaluations = []
    for option in options:
        option_key = _canonical_poll_option_key(option, game)
        book_outcome = keyed_book_outcomes.get(option_key)
        offered_odds = _normalize_option_odds(option)
        if book_outcome is None or offered_odds is None:
            continue
        fair_prob = american_to_implied_prob(book_outcome["odds"]) / implied_total
        evaluations.append(
            {
                "option": option,
                "book_outcome": book_outcome,
                "evaluation": _evaluate_binary_offer(fair_prob, offered_odds),
            }
        )
    if not evaluations:
        return {
            **base,
            **_book_outcome_fields(book_outcomes),
            "status": "missing_poll_data",
            "recommended_option": "",
            "recommended_amount": 0,
            "stake_fraction_of_max": 0.0,
            "recommended_ev_percent": "",
            "fair_prob": "",
            "fair_odds": "",
            "matched_books": len(game_quotes),
            "books": " | ".join(sorted({market.book for market in game_quotes})),
            "notes": f"Could not align Real options with sportsbook {notes_prefix} outcomes.",
        }

    selected_action = _choose_zero_or_max_action(
        poll.get("maxWager"),
        [
            {
                "label": str(item["option"].get("label") or ""),
                **item,
            }
            for item in evaluations
        ],
    )
    if selected_action is None:
        return {
            **base,
            **_book_outcome_fields(book_outcomes),
            "status": "missing_poll_data",
            "recommended_option": "",
            "recommended_amount": 0,
            "stake_fraction_of_max": 0.0,
            "recommended_ev_percent": "",
            "fair_prob": "",
            "fair_odds": "",
            "matched_books": len(game_quotes),
            "books": " | ".join(sorted({market.book for market in game_quotes})),
            "notes": f"Could not evaluate {notes_prefix} action choices.",
        }
    best = selected_action
    best_eval = best["evaluation"]
    return {
        **base,
        **_book_outcome_fields(book_outcomes),
        "status": str(best.get("status") or "pick"),
        "recommended_option": str(best.get("label") or best["option"].get("label") or ""),
        "recommended_amount": int(best.get("amount") or 0),
        "stake_fraction_of_max": round(float(best.get("stake_fraction") or 0.0), 6),
        "recommended_ev_percent": round(float(_evaluation_value(best_eval, "ev_percent") or 0.0), 4),
        "fair_prob": round(float(_evaluation_value(best_eval, "fair_prob") or 0.0), 6),
        "fair_odds": int(_evaluation_value(best_eval, "fair_odds") or probability_to_american(0.5)),
        "matched_books": len(game_quotes),
        "books": " | ".join(sorted({market.book for market in game_quotes})),
        "notes": f"no-vig {notes_prefix} from sportsbook odds",
    }


def _same_game(game: dict[str, Any], market: MarketRow) -> bool:
    market_pair = team_pair(market.home_team, market.away_team)

    key_pair = team_pair(
        game.get("homeTeamKey", ""),
        game.get("awayTeamKey", ""),
    )
    if key_pair == market_pair:
        return True

    # Soccer feeds can use short keys (for example "PAR", "BRE") while
    # sportsbook rows use full names (for example "Paris FC", "Stade Brest").
    # Fall back to the game-card team names when key-based matching misses.
    home_team_info = game.get("homeTeam")
    away_team_info = game.get("awayTeam")
    if isinstance(home_team_info, dict) and isinstance(away_team_info, dict):
        home_name = (
            home_team_info.get("displayName")
            or home_team_info.get("name")
            or home_team_info.get("key")
            or ""
        )
        away_name = (
            away_team_info.get("displayName")
            or away_team_info.get("name")
            or away_team_info.get("key")
            or ""
        )
        name_pair = team_pair(home_name, away_name)
        if name_pair == market_pair:
            return True

    return False


def _matching_player_quotes(
    markets: list[MarketRow],
    *,
    sport: str,
    game: dict[str, Any],
    player_name: str,
    stat_key: str,
    period: str = "",
) -> list[MarketQuote]:
    normalized_player = normalize_player_name(player_name)
    requested_period = str(period or "").strip()
    quotes: list[MarketQuote] = []
    for market in markets:
        if market.sport != sport or market.market_family != "player_over_under":
            continue
        market_period = str(market.period or "").strip()
        if requested_period:
            if market_period != requested_period:
                continue
        elif market_period:
            continue
        if not _same_game(game, market):
            continue
        if market.stat_key != stat_key:
            continue
        if market.player_name != normalized_player:
            continue
        if market.line is None or market.over_odds is None or market.under_odds is None:
            continue
        quotes.append(
            MarketQuote(
                book=market.book,
                line=market.line,
                over_odds=market.over_odds,
                under_odds=market.under_odds,
                updated_at=market.updated_at,
            )
        )
    return quotes


def _participant_names_from_play(play: dict[str, Any]) -> dict[str, str]:
    names: dict[str, str] = {}
    for key in (
        "primaryPlayer",
        "secondaryPlayer",
        "tertiaryPlayer",
        "quaternaryPlayer",
        "quinaryPlayer",
    ):
        player = play.get(key) or {}
        player_id = str(player.get("id") or "").strip()
        if not player_id:
            continue
        full_name = " ".join(
            part
            for part in (
                str(player.get("firstName") or "").strip(),
                str(player.get("lastName") or "").strip(),
            )
            if part
        ).strip()
        display_name = str(player.get("displayName") or "").strip()
        names[player_id] = full_name or display_name
    return names


def _nhl_goalie_save_records(game_payload: dict[str, Any], player_name: str) -> list[dict[str, Any]]:
    target_player = normalize_player_name(player_name)
    if not target_player:
        return []
    player_names: dict[str, str] = {}
    records: list[dict[str, Any]] = []
    for play in game_payload.get("plays") or []:
        if not isinstance(play, dict):
            continue
        player_names.update(_participant_names_from_play(play))
        default_display = ((play.get("display") or {}).get("default") or {})
        if not isinstance(default_display, dict):
            continue
        for player_id, text in default_display.items():
            match = re.search(r"\b([0-9]+)\s+save\b", str(text or ""), flags=re.IGNORECASE)
            if not match:
                continue
            name = player_names.get(str(player_id), "")
            if normalize_player_name(name) != target_player:
                continue
            records.append(
                {
                    "sequence": int(_to_float_or_none(play.get("sequence")) or 0),
                    "period": int(_to_float_or_none(play.get("period")) or 0),
                    "saves": int(match.group(1)),
                }
            )
    return records


def _period_number_from_code(value: str) -> int | None:
    match = re.match(r"^([0-9]+)P$", str(value or "").strip().upper())
    if not match:
        return None
    try:
        return int(match.group(1))
    except Exception:
        return None


def _nhl_live_saves_converted_quotes(
    entry: dict[str, Any],
    markets: list[MarketRow],
    *,
    sport: str,
    game: dict[str, Any],
    player_name: str,
    stat_key: str,
) -> dict[str, Any] | None:
    if sport != "nhl" or stat_key != "saves":
        return None
    records = _nhl_goalie_save_records(entry.get("game_payload") or {}, player_name)
    if not records:
        return None
    current_saves = max((int(record["saves"]) for record in records), default=0)
    normalized_player = normalize_player_name(player_name)
    quotes: list[MarketQuote] = []
    source_parts: list[str] = []
    for market in markets:
        if market.sport != sport or market.market_family != "player_over_under":
            continue
        if not market.period:
            continue
        period_number = _period_number_from_code(str(market.period or ""))
        if period_number is None:
            continue
        if not _same_game(game, market):
            continue
        if market.stat_key != stat_key or market.player_name != normalized_player:
            continue
        if market.line is None or market.over_odds is None or market.under_odds is None:
            continue
        saves_before_period = 0 if period_number == 1 else max(
            (
                int(record["saves"])
                for record in records
                if int(record.get("period") or 0) < period_number
            ),
            default=-1,
        )
        if saves_before_period < 0:
            continue
        converted_line = float(saves_before_period) + float(market.line)
        quotes.append(
            MarketQuote(
                book=market.book,
                line=converted_line,
                over_odds=market.over_odds,
                under_odds=market.under_odds,
                updated_at=market.updated_at,
            )
        )
        source_parts.append(
            (
                f"{_book_abbreviation(market.book)} {market.period} "
                f"{float(market.line):g}+{saves_before_period}={converted_line:g}: "
                f"{_format_american(market.over_odds)} / {_format_american(market.under_odds)}"
            )
        )
    if not quotes:
        return None
    return {
        "quotes": quotes,
        "source_lines": "; ".join(source_parts),
        "note": (
            f"converted live period saves with Real current saves "
            f"({player_name} {current_saves} saves)"
        ),
    }


def _compact_market_name(value: str) -> str:
    return "".join(ch for ch in str(value or "").lower() if ch.isalnum())


def _is_full_game_total_market(market: MarketRow) -> bool:
    raw = market.raw or {}
    names = [
        raw.get("provider_market_name"),
        raw.get("market_name"),
        raw.get("question"),
    ]
    compact_names = {_compact_market_name(str(name or "")) for name in names if str(name or "").strip()}
    if any("parlay" in name for name in compact_names):
        return False
    if str(market.sport or "").strip().lower() == "nhl":
        return bool(compact_names & {"total", "totalgoals"})
    for name in compact_names:
        if (
            "overunder" in name
            and (name.endswith("goals") or name.endswith("runs") or name.endswith("points"))
            and "half" not in name
            and "quarter" not in name
            and "period" not in name
            and "team" not in name
        ):
            return True
    allowed_names = {
        "total",
        "totalpoints",
        "totalruns",
        "totalgoals",
        "totalalternate",
        "alternatetotal",
        "alternatetotalpoints",
        "alternatetotalruns",
        "alternatetotalgoals",
        "asiantotal",
        "asiantotalpoints",
        "asiantotalgoals",
        "asianhandicaptotal",
    }
    return bool(compact_names & allowed_names)


def _matching_game_quotes(
    markets: list[MarketRow],
    *,
    sport: str,
    game: dict[str, Any],
    market_family: str,
    period: str = "",
) -> list[MarketRow]:
    matches: list[MarketRow] = []
    for market in markets:
        if market.sport != sport or market.market_family != market_family:
            continue
        market_period = str(market.period or "").strip()
        if period:
            if market_period != period:
                continue
        elif market_period:
            continue
        if market_family == "game_total" and not period and not _is_full_game_total_market(market):
            continue
        if _same_game(game, market):
            matches.append(market)
    return matches


def _integer_goal_total_equivalent_line(line_value: float) -> float | None:
    """Map quarter soccer totals to the half-goal line with the same integer result."""
    if line_value < 0:
        return None
    whole = int(line_value)
    fraction = line_value - whole
    if abs(fraction) <= 1e-9 or abs(fraction - 0.5) <= 1e-9:
        return None
    return whole + 0.5


def _market_extra_outcomes(market: MarketRow) -> list[dict[str, Any]]:
    raw_value = (market.raw or {}).get("extra_outcomes")
    if raw_value:
        try:
            parsed = json.loads(raw_value)
            if isinstance(parsed, list):
                return [item for item in parsed if isinstance(item, dict)]
        except Exception:
            pass

    outcomes: list[dict[str, Any]] = []
    if market.over_odds is not None:
        outcomes.append({"key": "home", "label": market.raw.get("home_team") or "Home", "odds": market.over_odds})
    draw_odds = market.raw.get("draw_odds")
    if draw_odds not in (None, "", "None"):
        try:
            outcomes.append({"key": "draw", "label": "Draw", "odds": int(float(draw_odds))})
        except Exception:
            pass
    if market.under_odds is not None:
        outcomes.append({"key": "away", "label": market.raw.get("away_team") or "Away", "odds": market.under_odds})
    return outcomes


def _canonical_book_outcome_key(outcome: dict[str, Any], market: MarketRow) -> str:
    key = normalize_text(str(outcome.get("key") or "")).replace(" ", "")
    label = str(outcome.get("label") or "").strip()
    label_key = normalize_text(label).replace(" ", "")
    if key in {"home", "1"}:
        return "home"
    if key in {"away", "2"}:
        return "away"
    if key in {"tie", "draw", "x"} or label_key == "draw":
        return "draw"
    if key in {"1x", "x2", "12"}:
        return key
    if key in {"yes", "no"}:
        return key
    if key in {"nomore", "norun", "noruns", "noscore", "nogoal", "nogoals", "nopoint", "nopoints"}:
        return "no_more"
    if _is_no_more_outcome_label(label):
        return "no_more"
    normalized_label_team = normalize_team(label)
    if normalized_label_team and normalized_label_team == market.home_team:
        return "home"
    if normalized_label_team and normalized_label_team == market.away_team:
        return "away"
    normalized_label = normalize_text(label).replace(" ", "")
    if "or" in normalized_label:
        home_text = normalize_text(market.raw.get("home_team") or "").replace(" ", "")
        away_text = normalize_text(market.raw.get("away_team") or "").replace(" ", "")
        has_home = bool(
            market.home_team
            and (
                market.home_team in normalized_label
                or (home_text and home_text in normalized_label)
            )
        )
        has_away = bool(
            market.away_team
            and (
                market.away_team in normalized_label
                or (away_text and away_text in normalized_label)
            )
        )
        has_draw = "tie" in normalized_label or "draw" in normalized_label
        if has_home and has_draw:
            return "1x"
        if has_away and has_draw:
            return "x2"
        if has_home and has_away:
            return "12"
    return label_key


def _canonical_poll_option_key(option: dict[str, Any], game: dict[str, Any]) -> str:
    additional = option.get("additionalInfo") or {}
    outcomes = {str(value or "").strip().lower() for value in additional.get("outcomes") or []}
    if outcomes == {"home", "draw"}:
        return "1x"
    if outcomes == {"away", "draw"}:
        return "x2"
    if outcomes == {"home", "away"}:
        return "12"
    if additional.get("isDraw") is True:
        return "draw"
    if additional.get("yes") is True:
        return "yes"
    if additional.get("yes") is False:
        return "no"

    label = str(option.get("label") or "").strip()
    if _is_no_more_outcome_label(label):
        return "no_more"
    normalized_label = normalize_team(label)
    if normalized_label == normalize_team(game.get("homeTeamKey") or ""):
        return "home"
    if normalized_label == normalize_team(game.get("awayTeamKey") or ""):
        return "away"
    label_key = normalize_text(label).replace(" ", "")
    if label_key in {"draw", "tie", "yes", "no"}:
        return {"tie": "draw"}.get(label_key, label_key)
    return label_key


def _option_fields(options: list[dict[str, Any]]) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    slots = ("a", "b", "c")
    for slot, option in zip(slots, options):
        odds = _normalize_option_odds(option)
        fields[f"option_{slot}_label"] = str(option.get("label") or "")
        fields[f"option_{slot}_odds"] = odds if odds is not None else ""
    for slot in slots:
        fields.setdefault(f"option_{slot}_label", "")
        fields.setdefault(f"option_{slot}_odds", "")
    return fields


def _book_outcome_fields(outcomes: list[dict[str, Any]]) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    slots = ("a", "b", "c")
    for slot, outcome in zip(slots, outcomes):
        fields[f"sportsbook_{slot}_label"] = str(outcome.get("label") or outcome.get("key") or "")
        fields[f"sportsbook_{slot}_odds"] = outcome.get("odds") if outcome.get("odds") is not None else ""
    for slot in slots:
        fields.setdefault(f"sportsbook_{slot}_label", "")
        fields.setdefault(f"sportsbook_{slot}_odds", "")
    return fields


def _format_american(value: Any) -> str:
    try:
        odds = int(value)
    except Exception:
        return ""
    return f"+{odds}" if odds > 0 else str(odds)


def _book_abbreviation(book: str) -> str:
    normalized = str(book or "").strip().lower()
    aliases = {
        "draftkings": "DK",
        "fanduel": "FD",
    }
    return aliases.get(normalized, normalized.upper() if normalized else "Book")


def _representative_market_quote(quotes: list[MarketQuote], target_line: float) -> MarketQuote | None:
    if not quotes:
        return None
    return min(
        quotes,
        key=lambda quote: (
            abs(float(quote.line) - float(target_line)),
            str(quote.book),
        ),
    )


def _book_line_summary(
    quotes: list[MarketQuote],
    target_line: float,
    *,
    prefer_exact: bool | None = None,
) -> str:
    if not quotes:
        return ""

    target = float(target_line)
    exact_quotes = [quote for quote in quotes if abs(float(quote.line) - target) <= 1e-9]
    use_exact = bool(exact_quotes) if prefer_exact is None else bool(prefer_exact and exact_quotes)
    quotes_by_book: dict[str, list[MarketQuote]] = {}
    for quote in (exact_quotes if use_exact else quotes):
        quotes_by_book.setdefault(quote.book, []).append(quote)

    parts: list[str] = []
    for book in sorted(quotes_by_book):
        ranked = sorted(
            quotes_by_book[book],
            key=lambda item: (abs(float(item.line) - target), float(item.line)),
        )
        if use_exact:
            ranked = ranked[:1]
        else:
            ranked = sorted(ranked[:5], key=lambda item: float(item.line))

        quote_parts: list[str] = []
        for quote in ranked:
            line_text = "" if use_exact and abs(float(quote.line) - target) <= 1e-9 else f"{float(quote.line):g}"
            over_odds = _format_american(quote.over_odds)
            under_odds = _format_american(quote.under_odds)
            if over_odds and under_odds:
                quote_parts.append(f"{line_text}: {over_odds} / {under_odds}" if line_text else f"{over_odds} / {under_odds}")
        if quote_parts:
            parts.append(f"{_book_abbreviation(book)}: {'; '.join(quote_parts)}")
    return "\n".join(parts)


def _quote_book_count(quotes: list[MarketQuote]) -> int:
    return len({str(quote.book or "").strip().lower() for quote in quotes if str(quote.book or "").strip()})


def _quote_books_text(quotes: list[MarketQuote]) -> str:
    return " | ".join(
        sorted({str(quote.book or "").strip() for quote in quotes if str(quote.book or "").strip()})
    )


def _mlb_hrr_component_model(
    markets: list[MarketRow],
    *,
    game: dict[str, Any],
    player_name: str,
    target_line: float,
    period: str = "",
) -> dict[str, Any] | None:
    if abs(float(target_line) - 0.5) > 1e-9:
        return None

    normalized_player = normalize_player_name(player_name)
    requested_period = str(period or "").strip()
    component_rows: dict[str, list[tuple[MarketRow, float, float]]] = {
        stat: [] for stat in MLB_HRR_COMPONENT_STATS
    }
    for market in markets:
        if market.sport != "mlb" or market.market_family != "player_over_under":
            continue
        if market.stat_key not in component_rows:
            continue
        market_period = str(market.period or "").strip()
        if requested_period:
            if market_period != requested_period:
                continue
        elif market_period:
            continue
        if not _same_game(game, market):
            continue
        if market.player_name != normalized_player:
            continue
        if market.line is None or abs(float(market.line) - 0.5) > 1e-9:
            continue
        if market.over_odds is None or market.under_odds is None:
            continue
        try:
            devigged = devig_quote(
                MarketQuote(
                    book=market.book,
                    line=float(market.line),
                    over_odds=market.over_odds,
                    under_odds=market.under_odds,
                    updated_at=market.updated_at,
                )
            )
        except Exception:
            continue
        component_rows[market.stat_key].append((market, devigged.over_prob, devigged.weight))

    available_stats = [stat for stat in MLB_HRR_COMPONENT_STATS if component_rows[stat]]
    if not available_stats:
        return None

    component_probs: dict[str, float] = {}
    selected_rows: dict[str, list[MarketRow]] = {}
    for stat in available_stats:
        rows = component_rows[stat]
        total_weight = sum(max(0.0, float(weight)) for _row, _prob, weight in rows)
        if total_weight <= 0:
            component_prob = sum(float(prob) for _row, prob, _weight in rows) / len(rows)
        else:
            component_prob = sum(float(prob) * max(0.0, float(weight)) for _row, prob, weight in rows) / total_weight
        component_probs[stat] = max(0.0, min(1.0, component_prob))
        selected_rows[stat] = [row for row, _prob, _weight in rows]

    lower_bound_only = len(available_stats) == 1
    if lower_bound_only and max(component_probs.values()) <= 0.5:
        return None

    miss_prob = 1.0
    for probability in component_probs.values():
        miss_prob *= 1.0 - probability
    over_fair_prob = max(component_probs.values())
    over_fair_prob = max(over_fair_prob, min(0.999999, 1.0 - miss_prob))
    over_fair_prob = max(0.000001, min(0.999999, over_fair_prob))

    books = sorted(
        {
            str(row.book or "").strip()
            for rows in selected_rows.values()
            for row in rows
            if str(row.book or "").strip()
        }
    )
    books_by_name: dict[str, list[str]] = {}
    for stat in MLB_HRR_COMPONENT_STATS:
        for row in selected_rows.get(stat, []):
            over_text = _format_american(row.over_odds)
            under_text = _format_american(row.under_odds)
            if not over_text or not under_text:
                continue
            book = _book_abbreviation(row.book)
            books_by_name.setdefault(book, []).append(f"{stat} {over_text} / {under_text}")

    source_lines = "\n".join(
        f"{book}: {'; '.join(parts)}" for book, parts in sorted(books_by_name.items())
    )
    stat_text = ", ".join(available_stats)
    if lower_bound_only:
        source_note = (
            "MLB HRR lower-bound from same-game "
            f"{stat_text} prop; no exact hits+runs+RBIs market was available."
        )
    else:
        source_note = (
            "MLB HRR component model from same-game "
            f"{stat_text} props; no exact hits+runs+RBIs market was available."
        )
    return {
        "fair_prob": over_fair_prob,
        "fair_odds": probability_to_american(over_fair_prob),
        "matched_books": len(books),
        "books": " | ".join(books),
        "source_lines": source_lines,
        "source_note": source_note,
        "lower_bound_only": lower_bound_only,
    }


def _book_moneyline_summary(markets: list[MarketRow], home_label: str, away_label: str) -> str:
    if not markets:
        return ""

    markets_by_book: dict[str, list[MarketRow]] = {}
    for market in markets:
        markets_by_book.setdefault(market.book, []).append(market)

    parts: list[str] = []
    for book in sorted(markets_by_book):
        market = sorted(markets_by_book[book], key=lambda item: str(item.updated_at or ""), reverse=True)[0]
        home_odds = _format_american(market.over_odds)
        away_odds = _format_american(market.under_odds)
        if home_odds and away_odds:
            parts.append(f"{_book_abbreviation(book)}: {home_label} {home_odds} / {away_label} {away_odds}")
    return "\n".join(parts)


def _moneyline_source_note(success_note: str, unique_book_count: int) -> str:
    if unique_book_count > 1:
        return success_note
    return str(success_note or "").replace("moneyline consensus", "single-book moneyline")


def _representative_game_market(markets: list[MarketRow]) -> MarketRow | None:
    if not markets:
        return None
    return sorted(
        markets,
        key=lambda market: (
            str(market.book),
            str(market.updated_at or ""),
        ),
    )[0]


def _fetch_active_day_game_entries(
    sport: str,
    *,
    requested_day: str = "",
    include_nonwagerable: bool = False,
    client: Any | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    client = client or build_realsports_client()
    home_payload = client.get_home_tab(sport=sport)
    latest = home_payload.get("latestDayContent") or {}
    active_day = str(latest.get("day") or home_payload.get("latestDay") or "").strip()
    resolved_day = requested_day or active_day
    games = [
        game
        for game in (latest.get("games") or [])
        if str(game.get("day") or "").strip() == resolved_day
    ]
    entries: list[dict[str, Any]] = []
    game_sort_meta: dict[str, tuple[str, str, int]] = {}
    for game_order, game in enumerate(games):
        game_payload = client.get_game_feed(game.get("id"), sport=sport)
        players_by_id = _player_lookup(game_payload.get("players") or [])
        game_payload_game = game_payload.get("game") or game
        game_id = str(game_payload_game.get("id") or game.get("id") or "").strip()
        game_date_time = str(game_payload_game.get("dateTime") or game.get("dateTime") or "").strip()
        posts = game_payload.get("posts") or []
        first_post_created_at = ""
        for post in posts:
            created_at = str(post.get("createdAt") or "").strip()
            if not created_at:
                continue
            if not first_post_created_at or created_at < first_post_created_at:
                first_post_created_at = created_at
        if game_id and game_id not in game_sort_meta:
            game_sort_meta[game_id] = (game_date_time, first_post_created_at, game_order)
        for post_order, post in enumerate(posts):
            poll_id = _extract_poll_id(post)
            if poll_id:
                poll_payload = client.get_poll(poll_id)
                poll = poll_payload.get("poll") or {}
                poll_kind = _poll_kind(
                    poll.get("additionalInfo") or {},
                    poll=poll,
                    post=post,
                )
                allow_unpriced_poll = poll_kind in UNPRICED_POLL_KINDS
                if not include_nonwagerable and not poll.get("canWager", False) and not allow_unpriced_poll:
                    continue
            elif is_lineup_contest_post(post):
                poll = _contest_payload_from_post(post)
            else:
                continue
            entries.append(
                {
                    "game": game_payload_game,
                    "game_payload": game_payload,
                    "post": post,
                    "poll": poll,
                    "player_lookup": players_by_id,
                    "game_order": game_order,
                    "post_order": post_order,
                }
            )
    if game_sort_meta and entries:
        ordered_game_ids = sorted(
            game_sort_meta,
            key=lambda gid: (
                game_sort_meta[gid][0] if game_sort_meta[gid][0] else "9999-99-99T99:99:99.999Z",
                game_sort_meta[gid][1] if game_sort_meta[gid][1] else "9999-99-99T99:99:99.999Z",
                game_sort_meta[gid][2],
                gid,
            ),
        )
        game_rank = {gid: rank for rank, gid in enumerate(ordered_game_ids)}
        for entry in entries:
            gid = str((entry.get("game") or {}).get("id") or "").strip()
            if gid in game_rank:
                entry["game_order"] = game_rank[gid]
    return resolved_day, entries


def _fetch_requested_day_game_entries(
    client: Any,
    sport: str,
    *,
    day: str,
    include_nonwagerable: bool = False,
) -> list[dict[str, Any]]:
    if not day:
        return []

    posts_by_id: dict[str, dict[str, Any]] = {}
    games_by_id: dict[str, dict[str, Any]] = {}
    before: str | None = None
    for _ in range(8):
        try:
            payload = client.get_polls_for_sport_day(sport, day=day, before=before)
        except Exception:
            break
        for game in payload.get("games") or []:
            game_id = str(game.get("id") or "").strip()
            if game_id:
                games_by_id[game_id] = game
        posts = [
            post
            for post in (payload.get("posts") or [])
            if isinstance(post, dict) and str(post.get("id") or "").strip()
        ]
        if not posts:
            break
        for post in posts:
            posts_by_id[str(post.get("id"))] = post
        oldest_created_at = str(posts[-1].get("createdAt") or "").strip()
        if not oldest_created_at or oldest_created_at == before:
            break
        before = oldest_created_at

    if not posts_by_id:
        return []

    game_payloads: dict[str, dict[str, Any]] = {}
    player_lookups: dict[str, dict[str, dict[str, Any]]] = {}
    for game_id in games_by_id:
        try:
            game_payload = client.get_game_feed(game_id, sport=sport)
        except Exception:
            game_payload = {"game": games_by_id[game_id], "players": [], "posts": []}
        game_payloads[game_id] = game_payload
        player_lookups[game_id] = _player_lookup(game_payload.get("players") or [])

    ordered_games = sorted(
        games_by_id,
        key=lambda gid: (
            str(games_by_id[gid].get("dateTime") or "9999-99-99T99:99:99.999Z"),
            gid,
        ),
    )
    game_rank = {gid: index for index, gid in enumerate(ordered_games)}
    ordered_posts = sorted(
        posts_by_id.values(),
        key=lambda post: (
            str((games_by_id.get(str(post.get("gameId") or "").strip()) or {}).get("dateTime") or "9999-99-99T99:99:99.999Z"),
            str(post.get("createdAt") or ""),
            int(post.get("id") or 0),
        ),
    )

    entries: list[dict[str, Any]] = []
    for post_order, post in enumerate(ordered_posts):
        game_id = str(post.get("gameId") or "").strip()
        game = games_by_id.get(game_id) or {}
        if not game:
            continue
        poll_id = _extract_poll_id(post)
        if poll_id:
            try:
                poll_payload = client.get_poll(poll_id)
            except Exception:
                continue
            poll = poll_payload.get("poll") or {}
            poll_kind = _poll_kind(
                poll.get("additionalInfo") or {},
                poll=poll,
                post=post,
            )
            allow_unpriced_poll = poll_kind in UNPRICED_POLL_KINDS
            if not include_nonwagerable and not poll.get("canWager", False) and not allow_unpriced_poll:
                continue
        elif is_lineup_contest_post(post):
            poll = _contest_payload_from_post(post)
        else:
            continue
        game_payload = game_payloads.get(game_id) or {"game": game, "players": [], "posts": []}
        entries.append(
            {
                "game": game_payload.get("game") or game,
                "game_payload": game_payload,
                "post": post,
                "poll": poll,
                "player_lookup": player_lookups.get(game_id, {}),
                "game_order": game_rank.get(game_id, 999999),
                "post_order": post_order,
            }
        )
    return entries


def _fetch_daily_poll_entries(
    client: Any,
    sport: str,
    *,
    day: str,
    active_games: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    try:
        home_payload = client.get_home_tab(sport=sport)
    except Exception:
        return []

    latest = home_payload.get("latestDayContent") or {}
    items = latest.get("items") or []
    entries: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if str(item.get("entityType") or "").strip().lower() != "post":
            continue
        for summary in item.get("posts") or []:
            post_id = summary.get("postId") or summary.get("id")
            if not post_id:
                continue
            try:
                post_payload = client.get_post(post_id)
            except Exception:
                continue
            post = post_payload.get("post") or post_payload
            additional = post.get("additionalInfo") or {}
            if str(additional.get("type") or "").strip().lower() != "daily":
                continue
            if str(additional.get("subType") or "").strip().lower() != "stats":
                continue

            poll_ids = _extract_poll_ids(post)
            if not poll_ids:
                continue

            lock_time = str(post.get("pollsLockAt") or "").strip()
            synthetic_game = {
                "id": f"daily:{post.get('id')}",
                "sport": sport,
                "day": day,
                "dateTime": lock_time,
                "homeTeamKey": "",
                "awayTeamKey": "",
                "metadata": {
                    "title": str(post.get("header") or "Daily stats").strip() or "Daily stats",
                },
            }
            for post_order, poll_id in enumerate(poll_ids):
                try:
                    poll_payload = client.get_poll(poll_id)
                except Exception:
                    continue
                poll = poll_payload.get("poll") or {}
                entries.append(
                    {
                        "game": synthetic_game,
                        "game_payload": {
                            "game": synthetic_game,
                            "players": [],
                            "posts": [],
                        },
                        "post": post,
                        "poll": poll,
                        "player_lookup": {},
                        "game_order": -1,
                        "post_order": post_order,
                        "active_day_games": active_games,
                    }
                )
    return entries


def _parse_pool_payout_multiples(value: Any) -> list[float]:
    payouts: list[float] = []
    if isinstance(value, list):
        for item in value:
            raw_value: Any = item
            if isinstance(item, dict):
                raw_value = (
                    item.get("prizeAmount")
                    or item.get("payout")
                    or item.get("amount")
                    or item.get("value")
                )
            text = str(raw_value or "").strip().lower()
            if text.endswith("x"):
                text = text[:-1].strip()
            try:
                payout = float(text)
            except Exception:
                continue
            if payout > 0:
                payouts.append(payout)
    return payouts


def _fetch_daily_pool_posts(
    client: Any,
    sport: str,
    *,
    day: str,
) -> list[dict[str, Any]]:
    try:
        home_payload = client.get_home_tab(sport=sport)
    except Exception:
        return []

    latest = home_payload.get("latestDayContent") or {}
    items = latest.get("items") or []
    pool_posts: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if str(item.get("entityType") or "").strip().lower() != "post":
            continue
        for summary in item.get("posts") or []:
            post_id = summary.get("postId") or summary.get("id")
            if not post_id:
                continue
            try:
                post_payload = client.get_post(post_id)
            except Exception:
                continue
            post = post_payload.get("post") or post_payload
            additional = post.get("additionalInfo") or {}
            info = post_payload.get("info") or post.get("info") or {}
            is_daily_dog = bool(additional.get("isDailyDog"))
            if not is_daily_dog:
                if str(additional.get("type") or "").strip().lower() != "daily":
                    continue
                if str(additional.get("subType") or "").strip().lower() == "stats":
                    continue
            post_day = str(additional.get("day") or "").strip()
            if post_day and day and post_day != day:
                continue
            poll_ids = _extract_poll_ids(post)
            if not poll_ids:
                continue
            if not is_daily_dog:
                if len(poll_ids) < 2:
                    continue
                valid_poll_types = [str(value or "").strip().lower() for value in (additional.get("validPollTypes") or [])]
                if "gamewinner" not in valid_poll_types:
                    continue
            pool_options: list[dict[str, Any]] = []
            if is_daily_dog:
                for poll_id in poll_ids:
                    try:
                        poll_payload = client.get_poll(poll_id)
                    except Exception:
                        continue
                    poll = poll_payload.get("poll") or {}
                    poll_game_id = str(poll.get("gameId") or "").strip()
                    for option in poll.get("options") or []:
                        label = str(option.get("label") or "").strip()
                        if not label:
                            continue
                        option_additional = option.get("additionalInfo") or {}
                        option_game_id = str(option_additional.get("gameId") or poll_game_id or "").strip()
                        odds = _normalize_option_odds(option)
                        if odds is None:
                            odds = _extract_trailing_american_odds(label)
                        pool_options.append(
                            {
                                "poll_id": str(poll_id).strip(),
                                "game_id": option_game_id,
                                "label": label,
                                "team_label": _option_label_team(label),
                                "odds": odds,
                            }
                        )
            pool_posts.append(
                {
                    "post_id": str(post.get("id") or post_id).strip(),
                    "header": str(post.get("header") or "").strip(),
                    "content_text": _first_text(((post.get("content") or {}).get("nodes")) or []),
                    "poll_ids": [str(poll_id).strip() for poll_id in poll_ids if str(poll_id).strip()],
                    "max_karma": additional.get("maxKarma"),
                    "payouts": (
                        _parse_pool_payout_multiples(additional.get("payouts"))
                        or _parse_pool_payout_multiples(info.get("payoutInfoItems"))
                    ),
                    "is_daily_dog": is_daily_dog,
                    "options": pool_options,
                    "day": post_day or day,
                }
            )
    return pool_posts


def _option_odds_pairs(row: dict[str, Any]) -> list[tuple[str, int]]:
    pairs: list[tuple[str, int]] = []
    for label_key, odds_key in (
        ("option_a_label", "option_a_odds"),
        ("option_b_label", "option_b_odds"),
        ("option_c_label", "option_c_odds"),
    ):
        label = str(row.get(label_key) or "").strip()
        odds_value = row.get(odds_key)
        if not label or odds_value in (None, "", "None"):
            continue
        try:
            odds = int(float(odds_value))
        except Exception:
            continue
        pairs.append((label, odds))
    return pairs


def _sportsbook_odds_pairs(row: dict[str, Any]) -> list[tuple[str, int]]:
    pairs: list[tuple[str, int]] = []
    for label_key, odds_key in (
        ("sportsbook_a_label", "sportsbook_a_odds"),
        ("sportsbook_b_label", "sportsbook_b_odds"),
        ("sportsbook_c_label", "sportsbook_c_odds"),
    ):
        label = str(row.get(label_key) or "").strip()
        odds_value = row.get(odds_key)
        if not label or odds_value in (None, "", "None"):
            continue
        try:
            odds = int(float(odds_value))
        except Exception:
            continue
        pairs.append((label, odds))
    return pairs


def _underdog_label_from_pairs(
    pairs: list[tuple[str, int]],
    *,
    require_plus_money: bool,
) -> str:
    if len(pairs) < 2:
        return ""
    ranked = sorted(
        pairs,
        key=lambda item: (
            american_to_implied_prob(item[1]),
            -item[1],
            normalize_team(item[0]),
        ),
    )
    if require_plus_money and int(ranked[0][1]) <= 0:
        return ""
    return str(ranked[0][0] or "").strip()


def _underdog_label(row: dict[str, Any]) -> str:
    sportsbook_label = _underdog_label_from_pairs(
        _sportsbook_odds_pairs(row),
        require_plus_money=True,
    )
    if sportsbook_label:
        return sportsbook_label
    return _underdog_label_from_pairs(
        _option_odds_pairs(row),
        require_plus_money=True,
    )


def _is_label_match(a: str, b: str) -> bool:
    left = normalize_team(str(a or ""))
    right = normalize_team(str(b or ""))
    if left and right:
        return left == right
    return str(a or "").strip().lower() == str(b or "").strip().lower()


def _to_float_or_none(value: Any) -> float | None:
    if value in (None, "", "None"):
        return None
    try:
        return float(value)
    except Exception:
        return None


def _extract_trailing_american_odds(value: str) -> int | None:
    text = str(value or "").strip()
    if not text:
        return None
    match = re.search(r"([+-]\d{2,5})\s*$", text)
    if not match:
        return None
    try:
        return int(match.group(1))
    except Exception:
        return None


def _option_label_team(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    trimmed = re.sub(r"\s*[+-]\d{2,5}\s*$", "", text).strip()
    # Daily dog labels can include spread suffixes (for example "SEA +5.5").
    trimmed = re.sub(r"\s+[+-]?\d+(?:\.\d+)?\s*$", "", trimmed).strip()
    return trimmed or text


def _row_option_labels(row: dict[str, Any]) -> list[str]:
    labels: list[str] = []
    for key in ("option_a_label", "option_b_label", "option_c_label"):
        label = str(row.get(key) or "").strip()
        if label:
            labels.append(label)
    return labels


def _fair_prob_for_option_label(row: dict[str, Any], target_label: str) -> float | None:
    target = str(target_label or "").strip()
    if not target:
        return None

    poll_kind = str(row.get("poll_kind") or "").strip().lower()
    if poll_kind == "game_winner":
        winner_prob = _fair_prob_for_game_winner_label(row, target)
        if winner_prob is not None:
            return winner_prob

    fair = _to_float_or_none(row.get("fair_prob"))
    recommended = str(row.get("recommended_option") or "").strip()
    if fair is None or not recommended:
        return None
    fair = max(0.0, min(1.0, fair))
    if _is_label_match(target, recommended):
        return fair

    labels = _row_option_labels(row)
    if len(labels) == 2 and any(_is_label_match(target, label) for label in labels):
        return max(0.0, min(1.0, 1.0 - fair))
    return None


def _sportsbook_odds_for_option_label(row: dict[str, Any], target_label: str) -> int | str:
    target = str(target_label or "").strip()
    if not target:
        return ""
    for label_key, odds_key in (
        ("sportsbook_a_label", "sportsbook_a_odds"),
        ("sportsbook_b_label", "sportsbook_b_odds"),
        ("sportsbook_c_label", "sportsbook_c_odds"),
    ):
        label = str(row.get(label_key) or "").strip()
        if label and _is_label_match(target, label):
            return row.get(odds_key) or ""
    for slot in ("a", "b", "c"):
        option_label = str(row.get(f"option_{slot}_label") or "").strip()
        if option_label and _is_label_match(target, option_label):
            return row.get(f"sportsbook_{slot}_odds") or ""
    return ""


def _real_odds_for_option_label(row: dict[str, Any], target_label: str) -> int | str:
    target = str(target_label or "").strip()
    if not target:
        return ""
    for slot in ("a", "b", "c"):
        option_label = str(row.get(f"option_{slot}_label") or "").strip()
        if option_label and _is_label_match(target, option_label):
            return row.get(f"option_{slot}_odds") or ""
    return ""


def _fair_prob_for_game_winner_label(row: dict[str, Any], target_label: str) -> float | None:
    fair = _to_float_or_none(row.get("fair_prob"))
    if fair is None:
        return None
    fair = max(0.0, min(1.0, fair))
    option_a_label = str(row.get("option_a_label") or "").strip()
    option_b_label = str(row.get("option_b_label") or "").strip()
    recommended = str(row.get("recommended_option") or "").strip()
    if not option_a_label or not option_b_label or not recommended:
        return None
    if _is_label_match(recommended, option_a_label):
        prob_a = fair
        prob_b = 1.0 - fair
    elif _is_label_match(recommended, option_b_label):
        prob_a = 1.0 - fair
        prob_b = fair
    else:
        return None
    if _is_label_match(target_label, option_a_label):
        return max(0.0, min(1.0, prob_a))
    if _is_label_match(target_label, option_b_label):
        return max(0.0, min(1.0, prob_b))
    return None


def _sportsbook_odds_for_game_winner_label(row: dict[str, Any], target_label: str) -> int | str:
    sportsbook_a_label = str(row.get("sportsbook_a_label") or "").strip()
    sportsbook_b_label = str(row.get("sportsbook_b_label") or "").strip()
    if _is_label_match(target_label, sportsbook_a_label):
        return row.get("sportsbook_a_odds") or ""
    if _is_label_match(target_label, sportsbook_b_label):
        return row.get("sportsbook_b_odds") or ""
    option_a_label = str(row.get("option_a_label") or "").strip()
    option_b_label = str(row.get("option_b_label") or "").strip()
    if _is_label_match(target_label, option_a_label):
        return row.get("sportsbook_a_odds") or ""
    if _is_label_match(target_label, option_b_label):
        return row.get("sportsbook_b_odds") or ""
    return ""


def _real_odds_for_game_winner_label(row: dict[str, Any], target_label: str) -> int | str:
    option_a_label = str(row.get("option_a_label") or "").strip()
    option_b_label = str(row.get("option_b_label") or "").strip()
    if _is_label_match(target_label, option_a_label):
        return row.get("option_a_odds") or ""
    if _is_label_match(target_label, option_b_label):
        return row.get("option_b_odds") or ""
    return ""


def _pool_miss_distribution(probabilities: list[float]) -> list[float]:
    distribution = [1.0]
    for raw_probability in probabilities:
        probability = max(0.0, min(1.0, float(raw_probability)))
        next_distribution = [0.0] * (len(distribution) + 1)
        for misses, value in enumerate(distribution):
            next_distribution[misses] += value * probability
            next_distribution[misses + 1] += value * (1.0 - probability)
        distribution = next_distribution
    return distribution


def _format_payout_multiple(value: float) -> str:
    if abs(float(value) - int(float(value))) <= 1e-9:
        return f"{int(float(value))}x"
    return f"{float(value):g}x"


def _ordinal_label(value: int) -> str:
    if 10 <= int(value) % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(int(value) % 10, "th")
    return f"{int(value)}{suffix}"


def _format_probability_note(value: float) -> str:
    return f"{float(value) * 100.0:.1f}%"


def _build_daily_pool_slate_rows(
    recommendations: list[dict[str, Any]],
    *,
    sport: str,
    day: str,
    pool_posts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    pool_posts = [post for post in pool_posts if not bool(post.get("is_daily_dog"))]
    if not pool_posts:
        return []

    by_poll_id: dict[str, dict[str, Any]] = {}
    for row in recommendations:
        poll_id = str(row.get("poll_id") or "").strip()
        if poll_id and poll_id not in by_poll_id:
            by_poll_id[poll_id] = row

    synthetic_rows: list[dict[str, Any]] = []
    for pool_index, pool in enumerate(pool_posts):
        poll_ids = [str(value or "").strip() for value in (pool.get("poll_ids") or []) if str(value or "").strip()]
        payouts = [float(value) for value in (pool.get("payouts") or []) if _to_float_or_none(value) is not None]
        pool_text = str(pool.get("content_text") or "").strip() or "Get polls correct to win the pool"
        max_wager_value = pool.get("max_karma") if pool.get("max_karma") not in (None, "", "None") else ""
        try:
            max_wager = int(float(max_wager_value))
        except Exception:
            max_wager = 0
        base_row = {
            "day": day,
            "sport": sport,
            "game_id": f"daily-pool:{pool.get('post_id') or pool_index}",
            "game_time": "",
            "home_team": "",
            "away_team": "",
            "game_label": "Pool of the day",
            "game_order": -3,
            "post_order": int(pool_index),
            "post_id": pool.get("post_id") or "",
            "poll_id": f"daily-pool:{pool.get('post_id') or pool_index}",
            "header": str(pool.get("header") or "").strip() or "Pool of the day",
            "poll_kind": "daily_pool",
            "player_name": "",
            "stat": "pool",
            "line": "",
            "can_wager": "",
            "max_wager": max_wager_value,
            "option_b_label": "",
            "option_b_odds": "",
            "option_c_label": "",
            "option_c_odds": "",
            "sportsbook_b_label": "",
            "sportsbook_b_odds": "",
            "sportsbook_c_label": "",
            "sportsbook_c_odds": "",
            "recommended_amount": 0,
            "stake_fraction_of_max": 0.0,
        }
        if not poll_ids:
            synthetic_rows.append(
                {
                    **base_row,
                    "content_text": pool_text,
                    "option_a_label": "",
                    "option_a_odds": "",
                    "sportsbook_a_label": "",
                    "sportsbook_a_odds": "",
                    "status": "missing_poll_data",
                    "recommended_option": "",
                    "recommended_ev_percent": "",
                    "fair_prob": "",
                    "fair_odds": "",
                    "consensus_fair_line": "",
                    "matched_books": 0,
                    "books": "",
                    "source_lines": "",
                    "notes": "Pool of the day detected, but no child polls were listed.",
                }
            )
            continue
        if not payouts:
            synthetic_rows.append(
                {
                    **base_row,
                    "content_text": pool_text,
                    "option_a_label": "",
                    "option_a_odds": "",
                    "sportsbook_a_label": "",
                    "sportsbook_a_odds": "",
                    "status": "missing_poll_data",
                    "recommended_option": "",
                    "recommended_ev_percent": "",
                    "fair_prob": "",
                    "fair_odds": "",
                    "consensus_fair_line": "",
                    "matched_books": 0,
                    "books": "",
                    "source_lines": "",
                    "notes": "Pool of the day detected, but payout tiers were missing.",
                }
            )
            continue

        legs: list[dict[str, Any]] = []
        missing_poll_ids: list[str] = []
        for poll_id in poll_ids:
            row = by_poll_id.get(poll_id)
            if not row:
                missing_poll_ids.append(poll_id)
                continue
            option_labels = _row_option_labels(row)
            candidate_labels = [label for label in option_labels if label]
            candidates: list[dict[str, Any]] = []
            for label in candidate_labels:
                fair_prob = _fair_prob_for_option_label(row, label)
                if fair_prob is None:
                    continue
                real_odds = _real_odds_for_option_label(row, label)
                sportsbook_odds = _sportsbook_odds_for_option_label(row, label)
                candidates.append(
                    {
                        "label": label,
                        "fair_prob": max(0.0, min(1.0, float(fair_prob))),
                        "fair_odds": probability_to_american(max(0.0, min(1.0, float(fair_prob)))),
                        "real_odds": real_odds,
                        "sportsbook_odds": sportsbook_odds,
                        "row": row,
                    }
                )
            if not candidates:
                option_pairs = _option_odds_pairs(row)
                if len(option_pairs) >= 2:
                    implied_total = sum(american_to_implied_prob(int(odds)) for _label, odds in option_pairs)
                    if implied_total <= 0:
                        implied_total = 1.0
                    for label, odds in option_pairs:
                        fair_prob = american_to_implied_prob(int(odds)) / implied_total
                        candidates.append(
                            {
                                "label": label,
                                "fair_prob": max(0.0, min(1.0, float(fair_prob))),
                                "fair_odds": probability_to_american(max(0.0, min(1.0, float(fair_prob)))),
                                "real_odds": int(odds),
                                "sportsbook_odds": _sportsbook_odds_for_option_label(row, label),
                                "row": row,
                            }
                        )
            if not candidates:
                missing_poll_ids.append(poll_id)
                continue
            selected = max(
                candidates,
                key=lambda item: (
                    float(item.get("fair_prob") or 0.0),
                    str(item.get("label") or ""),
                ),
            )
            row = selected["row"]
            game_label = str(
                row.get("game_label")
                or _entry_game_label(
                    {
                        "game": {
                            "awayTeamKey": row.get("away_team"),
                            "homeTeamKey": row.get("home_team"),
                        }
                    }
                )
                or ""
            ).strip()
            legs.append(
                {
                    **selected,
                    "poll_id": poll_id,
                    "game_label": game_label,
                    "game_time": str(row.get("game_time") or "").strip(),
                    "matched_books": row.get("matched_books") or "",
                    "books": str(row.get("books") or "").strip(),
                    "source_lines": str(row.get("source_lines") or "").strip(),
                }
            )

        if not legs:
            synthetic_rows.append(
                {
                    **base_row,
                    "content_text": pool_text,
                    "option_a_label": "",
                    "option_a_odds": "",
                    "sportsbook_a_label": "",
                    "sportsbook_a_odds": "",
                    "status": "no_market",
                    "recommended_option": "",
                    "recommended_ev_percent": "",
                    "fair_prob": "",
                    "fair_odds": "",
                    "consensus_fair_line": "",
                    "matched_books": 0,
                    "books": "",
                    "source_lines": "",
                    "notes": (
                        "Pool of the day detected, but none of the child polls "
                        "had sportsbook consensus rows yet."
                    ),
                }
            )
            continue

        missing_count = max(0, len(poll_ids) - len(legs))
        probabilities = [float(leg["fair_prob"]) for leg in legs] + ([0.5] * missing_count)
        miss_distribution = _pool_miss_distribution(probabilities)
        tier_probabilities = [
            miss_distribution[index] if index < len(miss_distribution) else 0.0
            for index, _payout in enumerate(payouts)
        ]
        cash_probability = sum(tier_probabilities)
        lose_probability = max(0.0, 1.0 - cash_probability)
        expected_return_multiple = sum(
            float(payout) * float(probability)
            for payout, probability in zip(payouts, tier_probabilities)
        )
        expected_ev_percent = (expected_return_multiple - 1.0) * 100.0
        recommended_amount = max_wager if expected_ev_percent > 0 and max_wager > 0 else 0
        status = "bet" if recommended_amount > 0 else "no_edge"
        selection_text = " / ".join(str(leg["label"]) for leg in legs)
        if missing_count > 0:
            selection_text = f"{selection_text} (+{missing_count} pending leg{'s' if missing_count != 1 else ''})"
        payout_text = " / ".join(
            f"{_ordinal_label(index + 1)} {_format_payout_multiple(payout)}"
            for index, payout in enumerate(payouts)
        )
        earliest_game_time = sorted(
            [str(leg.get("game_time") or "").strip() for leg in legs if str(leg.get("game_time") or "").strip()]
        )
        tier_notes = []
        for index, probability in enumerate(tier_probabilities):
            if index == 0:
                label = "all correct"
            else:
                label = f"exactly {len(legs) - index} correct"
            tier_notes.append(f"tier {index + 1} ({label}) {_format_probability_note(probability)}")
        chance_note = (
            "cash more likely than lose"
            if cash_probability > lose_probability
            else "lose more likely than cash"
        )
        leg_source_lines: list[str] = []
        for leg in legs:
            real_odds = _format_american(leg.get("real_odds"))
            sportsbook_odds = _format_american(leg.get("sportsbook_odds"))
            leg_source_lines.append(
                (
                    f"{leg.get('game_label')}: {leg.get('label')} "
                    f"{_format_probability_note(float(leg.get('fair_prob') or 0.0))} "
                    f"({_format_american(leg.get('fair_odds'))}); "
                    f"Real {real_odds or 'n/a'}; book {sportsbook_odds or 'n/a'}"
                )
            )
        if missing_poll_ids:
            leg_source_lines.append(
                "Pending child poll ids (neutral 50.0% placeholder): "
                + ", ".join(missing_poll_ids)
            )
        books = " | ".join(
            sorted(
                {
                    book.strip()
                    for leg in legs
                    for book in str(leg.get("books") or "").split("|")
                    if book.strip()
                }
            )
        )
        matched_books_values = [
            int(float(leg.get("matched_books") or 0))
            for leg in legs
            if _to_float_or_none(leg.get("matched_books")) is not None
        ]
        synthetic_rows.append(
            {
                **base_row,
                "game_time": earliest_game_time[0] if earliest_game_time else "",
                "content_text": f"{pool_text}. Picks -> {selection_text}.",
                "option_a_label": selection_text,
                "option_a_odds": "",
                "sportsbook_a_label": payout_text,
                "sportsbook_a_odds": "",
                "status": status,
                "recommended_option": selection_text,
                "recommended_amount": recommended_amount,
                "stake_fraction_of_max": 1.0 if recommended_amount else 0.0,
                "recommended_ev_percent": round(expected_ev_percent, 4),
                "fair_prob": round(cash_probability, 6),
                "fair_odds": int(probability_to_american(cash_probability)),
                "consensus_fair_line": round(expected_return_multiple, 4),
                "matched_books": min(matched_books_values) if matched_books_values else 0,
                "books": books,
                "source_lines": "\n".join(leg_source_lines),
                "notes": (
                    "daily pool payout model; "
                    f"{'; '.join(tier_notes)}; "
                    f"cash {_format_probability_note(cash_probability)}; "
                    f"lose {_format_probability_note(lose_probability)}; "
                    f"expected return {expected_return_multiple:.2f}x; {chance_note}"
                    + (
                        f"; missing child polls assumed at 50/50 ({', '.join(missing_poll_ids)})"
                        if missing_poll_ids
                        else ""
                    )
                ),
            }
        )
    return synthetic_rows


def _build_daily_pool_underdog_rows(
    recommendations: list[dict[str, Any]],
    *,
    sport: str,
    day: str,
    pool_posts: list[dict[str, Any]],
    markets: list[MarketRow],
) -> list[dict[str, Any]]:
    if not pool_posts:
        return []
    daily_dog_posts = [post for post in pool_posts if bool(post.get("is_daily_dog"))]
    processed_posts = daily_dog_posts
    if not processed_posts:
        return []

    by_poll_id: dict[str, dict[str, Any]] = {}
    by_game_id: dict[str, list[dict[str, Any]]] = {}
    game_context_by_id: dict[str, dict[str, str]] = {}
    for row in recommendations:
        poll_id = str(row.get("poll_id") or "").strip()
        if poll_id and poll_id not in by_poll_id:
            by_poll_id[poll_id] = row
        game_id = str(row.get("game_id") or "").strip()
        if not game_id or game_id.startswith("daily:") or game_id.startswith("daily-pool:"):
            continue
        by_game_id.setdefault(game_id, []).append(row)
        context = game_context_by_id.setdefault(
            game_id,
            {
                "home_team": "",
                "away_team": "",
                "game_label": "",
                "game_time": "",
            },
        )
        if not context["home_team"]:
            context["home_team"] = str(row.get("home_team") or "").strip()
        if not context["away_team"]:
            context["away_team"] = str(row.get("away_team") or "").strip()
        if not context["game_label"]:
            context["game_label"] = str(row.get("game_label") or "").strip()
        if not context["game_time"]:
            context["game_time"] = str(row.get("game_time") or "").strip()

    winner_markets_by_pair: dict[tuple[str, str], list[MarketRow]] = {}
    for market in markets:
        if market.sport != sport or market.market_family != "game_winner":
            continue
        if str(market.period or "").strip():
            continue
        if market.over_odds is None or market.under_odds is None:
            continue
        pair = team_pair(market.home_team, market.away_team)
        winner_markets_by_pair.setdefault(pair, []).append(market)

    synthetic_rows: list[dict[str, Any]] = []
    for pool_index, pool in enumerate(processed_posts):
        poll_ids = [str(value or "").strip() for value in (pool.get("poll_ids") or []) if str(value or "").strip()]
        pool_header = "Dog of the day"
        pool_text = str(pool.get("content_text") or "").strip() or "Underdog with best EV"
        normalized_pool_text = pool_text.lower()
        for prefix in ("sport of the day:", "sports of the day:", "dog of the day:"):
            if normalized_pool_text.startswith(prefix):
                pool_text = pool_text[len(prefix):].strip()
                break
        base_row = {
            "day": day,
            "sport": sport,
            "game_id": f"daily-pool:{pool.get('post_id') or pool_index}",
            "game_time": "",
            "home_team": "",
            "away_team": "",
            "game_label": "Dog of the day",
            "game_order": -2,
            "post_order": int(pool_index),
            "post_id": pool.get("post_id") or "",
            "poll_id": f"daily-pool:{pool.get('post_id') or pool_index}",
            "header": "Dog of the day",
            "poll_kind": "game_winner",
            "player_name": "",
            "stat": "winner",
            "line": "",
            "can_wager": "",
            "max_wager": pool.get("max_karma") if pool.get("max_karma") not in (None, "", "None") else "",
            "option_b_label": "",
            "option_b_odds": "",
            "option_c_label": "",
            "option_c_odds": "",
            "sportsbook_b_label": "",
            "sportsbook_b_odds": "",
            "sportsbook_c_label": "",
            "sportsbook_c_odds": "",
            "recommended_amount": 0,
            "stake_fraction_of_max": 0.0,
        }
        daily_dog_options = list(pool.get("options") or [])
        if daily_dog_options:
            option_candidates: list[dict[str, Any]] = []
            for option in daily_dog_options:
                target_label = str(option.get("team_label") or option.get("label") or "").strip()
                offered_odds_value = option.get("odds")
                if not target_label or offered_odds_value in (None, "", "None"):
                    continue
                try:
                    offered_odds = int(float(offered_odds_value))
                except Exception:
                    continue
                option_game_id = str(option.get("game_id") or "").strip()
                game_context = game_context_by_id.get(option_game_id) or {}
                home_team = str(game_context.get("home_team") or "").strip()
                away_team = str(game_context.get("away_team") or "").strip()
                if not home_team or not away_team:
                    continue
                target_norm = normalize_team(target_label)
                home_norm = normalize_team(home_team)
                away_norm = normalize_team(away_team)
                if target_norm == home_norm:
                    target_side = "home"
                elif target_norm == away_norm:
                    target_side = "away"
                else:
                    continue
                game_pair = team_pair(home_team, away_team)
                game_winner_markets = winner_markets_by_pair.get(game_pair) or []
                if not game_winner_markets:
                    continue

                target_probabilities: list[float] = []
                target_sportsbook_odds: list[int] = []
                underdog_books = 0
                for market in game_winner_markets:
                    home_odds = market.over_odds
                    away_odds = market.under_odds
                    if home_odds is None or away_odds is None:
                        continue
                    implied_home = american_to_implied_prob(home_odds)
                    implied_away = american_to_implied_prob(away_odds)
                    implied_total = implied_home + implied_away
                    if implied_total <= 0:
                        continue
                    fair_home = implied_home / implied_total
                    fair_away = implied_away / implied_total
                    if target_side == "home":
                        target_prob = fair_home
                        target_odds = int(home_odds)
                        opponent_prob = fair_away
                    else:
                        target_prob = fair_away
                        target_odds = int(away_odds)
                        opponent_prob = fair_home
                    target_probabilities.append(target_prob)
                    target_sportsbook_odds.append(target_odds)
                    if target_prob < opponent_prob and target_odds > 0:
                        underdog_books += 1
                if not target_probabilities or underdog_books <= 0:
                    continue
                fair_prob = sum(target_probabilities) / len(target_probabilities)
                sportsbook_odds = int(round(sum(target_sportsbook_odds) / len(target_sportsbook_odds)))
                evaluation = _evaluate_binary_offer(fair_prob, offered_odds)
                game_label = str(game_context.get("game_label") or "").strip()
                game_time = str(game_context.get("game_time") or "").strip()
                books = " | ".join(sorted({market.book for market in game_winner_markets if str(market.book or "").strip()}))
                option_candidates.append(
                    {
                        "option_label": target_label,
                        "target_label": target_label,
                        "offered_odds": offered_odds,
                        "sportsbook_odds": sportsbook_odds,
                        "matched_books": len(target_probabilities),
                        "books": books,
                        "game_label": game_label,
                        "game_time": game_time,
                        "evaluation": evaluation,
                    }
                )
            if not option_candidates:
                synthetic_rows.append(
                    {
                        **base_row,
                        "content_text": f"{pool_text}.",
                        "option_a_label": "",
                        "option_a_odds": "",
                        "sportsbook_a_label": "",
                        "sportsbook_a_odds": "",
                        "status": "no_market",
                        "recommended_option": "",
                        "recommended_ev_percent": "",
                        "fair_prob": "",
                        "fair_odds": "",
                        "consensus_fair_line": "",
                        "matched_books": 0,
                        "books": "",
                        "notes": (
                            "Daily dog poll detected, but no option could be matched to "
                            "same-game moneyline underdog consensus rows."
                        ),
                    }
                )
                continue
            selected_option = max(
                option_candidates,
                key=lambda item: (
                    float(_evaluation_value(item.get("evaluation") or {}, "ev_percent") or -9999.0),
                    float(_evaluation_value(item.get("evaluation") or {}, "fair_prob") or 0.0),
                    str(item.get("option_label") or ""),
                ),
            )
            selected_eval = selected_option.get("evaluation") or {}
            selected_label = str(selected_option.get("option_label") or "").strip()
            selected_team_label = str(selected_option.get("target_label") or selected_label).strip()
            selected_game_label = str(selected_option.get("game_label") or "").strip()
            summary_text = (
                f"{pool_text}. Best underdog EV -> {selected_label} in {selected_game_label}."
                if selected_game_label
                else f"{pool_text}. Best underdog EV -> {selected_label}."
            )
            synthetic_rows.append(
                {
                    **base_row,
                    "content_text": summary_text,
                    "option_a_label": selected_label,
                    "option_a_odds": selected_option.get("offered_odds"),
                    "sportsbook_a_label": selected_team_label,
                    "sportsbook_a_odds": selected_option.get("sportsbook_odds") or "",
                    "status": "pick",
                    "recommended_option": selected_label,
                    "recommended_ev_percent": round(float(_evaluation_value(selected_eval, "ev_percent") or 0.0), 4),
                    "fair_prob": round(float(_evaluation_value(selected_eval, "fair_prob") or 0.0), 6),
                    "fair_odds": int(_evaluation_value(selected_eval, "fair_odds") or probability_to_american(0.5)),
                    "consensus_fair_line": "",
                    "game_time": str(selected_option.get("game_time") or "").strip(),
                    "matched_books": selected_option.get("matched_books") or "",
                    "books": selected_option.get("books") or "",
                    "notes": (
                        f"Daily dog moneyline option selected from {len(option_candidates)} "
                        "voteable underdog choice(s); "
                        f"selected {selected_label} in {selected_game_label}."
                    ),
                }
            )
            continue
        candidates: list[dict[str, Any]] = []
        for poll_id in poll_ids:
            row = by_poll_id.get(poll_id)
            if not row:
                continue
            if str(row.get("poll_kind") or "").strip().lower() != "game_winner":
                continue
            underdog = _underdog_label(row)
            if not underdog:
                continue
            fair_prob = _fair_prob_for_game_winner_label(row, underdog)
            if fair_prob is None:
                continue
            offered_odds_raw = _real_odds_for_game_winner_label(row, underdog)
            offered_odds_value = _to_float_or_none(offered_odds_raw)
            if offered_odds_value is None:
                continue
            offered_odds = int(offered_odds_value)
            sportsbook_odds_raw = _sportsbook_odds_for_game_winner_label(row, underdog)
            sportsbook_odds_value = _to_float_or_none(sportsbook_odds_raw)
            sportsbook_odds = int(sportsbook_odds_value) if sportsbook_odds_value is not None else ""
            evaluation = _evaluate_binary_offer(fair_prob, offered_odds)
            candidates.append(
                {
                    "row": row,
                    "label": underdog,
                    "offered_odds": offered_odds,
                    "sportsbook_odds": sportsbook_odds,
                    "evaluation": evaluation,
                }
            )

        if not candidates:
            synthetic_rows.append(
                {
                    **base_row,
                    "content_text": f"{pool_text}.",
                    "option_a_label": "",
                    "option_a_odds": "",
                    "sportsbook_a_label": "",
                    "sportsbook_a_odds": "",
                    "status": "no_market",
                    "recommended_option": "",
                    "recommended_ev_percent": "",
                    "fair_prob": "",
                    "fair_odds": "",
                    "consensus_fair_line": "",
                    "matched_books": 0,
                    "books": "",
                    "notes": "Daily dog detected, but no moneyline underdog option had a computed EV recommendation yet.",
                }
            )
            continue

        selected = max(
            candidates,
            key=lambda candidate: (
                float(_evaluation_value(candidate.get("evaluation") or {}, "ev_percent") or -9999.0),
                float(_evaluation_value(candidate.get("evaluation") or {}, "fair_prob") or 0.0),
                str(candidate.get("label") or ""),
            ),
        )
        selected_row = selected.get("row") or {}
        selected_eval = selected.get("evaluation") or {}
        selected_label = str(selected.get("label") or "").strip()
        selected_game_label = str(
            selected_row.get("game_label")
            or _entry_game_label(
                {
                    "game": {
                        "awayTeamKey": selected_row.get("away_team"),
                        "homeTeamKey": selected_row.get("home_team"),
                    }
                }
            )
            or ""
        ).strip()
        summary_text = (
            f"{pool_text}. Best underdog EV -> {selected_label} in {selected_game_label}."
            if selected_game_label
            else f"{pool_text}. Best underdog EV -> {selected_label}."
        )
        synthetic_rows.append(
            {
                **base_row,
                "content_text": summary_text,
                "option_a_label": selected_label,
                "option_a_odds": selected.get("offered_odds") or "",
                "sportsbook_a_label": selected_label,
                "sportsbook_a_odds": selected.get("sportsbook_odds") or "",
                "status": "pick",
                "recommended_option": selected_label,
                "recommended_ev_percent": round(float(_evaluation_value(selected_eval, "ev_percent") or 0.0), 4),
                "fair_prob": round(float(_evaluation_value(selected_eval, "fair_prob") or 0.0), 6),
                "fair_odds": int(_evaluation_value(selected_eval, "fair_odds") or probability_to_american(0.5)),
                "consensus_fair_line": "",
                "matched_books": selected_row.get("matched_books") or "",
                "books": selected_row.get("books") or "",
                "notes": (
                    f"Daily dog moneyline pick from {len(candidates)} EV-qualified underdog option(s); "
                    f"selected {selected_label} in {selected_game_label}."
                ),
            }
        )
    return synthetic_rows


def _recommend_player_over_under(
    entry: dict[str, Any],
    markets: list[MarketRow],
    sport: str,
) -> dict[str, Any]:
    game = entry["game"]
    post = entry["post"]
    poll = entry["poll"]
    additional = poll.get("additionalInfo") or {}
    content_text = _first_text(((post.get("content") or {}).get("nodes")) or [])
    over_option, under_option = _choose_over_under_options(poll.get("options") or [])
    over_odds = _normalize_option_odds(over_option)
    under_odds = _normalize_option_odds(under_option)
    player_id = str(additional.get("playerId") or "").strip()
    player_name = entry["player_lookup"].get(player_id, "") or _infer_player_name_from_text(content_text)
    line = additional.get("overUnderAmount")
    try:
        line_value = float(line)
    except Exception:
        line_value = None
    period = "1H" if sport == "soccer" and str(additional.get("period") or "").strip() == "1" else ""

    base = {
        "poll_id": poll.get("id"),
        "post_id": post.get("id"),
        "sport": sport,
        "game_id": game.get("id"),
        "game_time": game.get("dateTime") or "",
        "home_team": game.get("homeTeamKey") or "",
        "away_team": game.get("awayTeamKey") or "",
        "header": post.get("header") or "",
        "content_text": content_text,
        "poll_kind": "player_over_under",
        "player_name": player_name,
        "stat": normalize_stat(additional.get("stat") or ""),
        "line": line_value if line_value is not None else "",
        "can_wager": poll.get("canWager"),
        "max_wager": poll.get("maxWager", ""),
        "option_a_label": str(over_option.get("label") or ""),
        "option_a_odds": over_odds if over_odds is not None else "",
        "option_b_label": str(under_option.get("label") or ""),
        "option_b_odds": under_odds if under_odds is not None else "",
        "sportsbook_a_label": str(over_option.get("label") or ""),
        "sportsbook_a_odds": "",
        "sportsbook_b_label": str(under_option.get("label") or ""),
        "sportsbook_b_odds": "",
    }
    if not player_name or line_value is None or over_odds is None or under_odds is None:
        return {
            **base,
            "status": "missing_poll_data",
            "recommended_option": "",
            "recommended_amount": 0,
            "stake_fraction_of_max": 0.0,
            "recommended_ev_percent": "",
            "fair_prob": "",
            "fair_odds": "",
            "matched_books": 0,
            "books": "",
            "notes": "Missing player id/name, line, or option odds.",
        }

    stat_key = normalize_stat(additional.get("stat") or "")
    quotes = _matching_player_quotes(
        markets,
        sport=sport,
        game=game,
        player_name=player_name,
        stat_key=stat_key,
        period=period,
    )
    converted_saves_source_lines = ""
    converted_saves_note = ""
    if not quotes:
        converted_saves = _nhl_live_saves_converted_quotes(
            entry,
            markets,
            sport=sport,
            game=game,
            player_name=player_name,
            stat_key=stat_key,
        )
        if converted_saves is not None:
            quotes = list(converted_saves.get("quotes") or [])
            converted_saves_source_lines = str(converted_saves.get("source_lines") or "")
            converted_saves_note = str(converted_saves.get("note") or "")

    if not quotes:
        component_fallback = None
        if sport == "mlb" and stat_key == "hitsrunsrbis":
            component_fallback = _mlb_hrr_component_model(
                markets,
                game=game,
                player_name=player_name,
                target_line=float(line_value),
                period=period,
            )
        if component_fallback is not None:
            over_fair_prob = float(component_fallback.get("fair_prob") or 0.0)
            over_fair_prob = max(0.0, min(1.0, over_fair_prob))
            under_fair_prob = max(0.0, min(1.0, 1.0 - over_fair_prob))
            over_eval = _evaluate_binary_offer(over_fair_prob, over_odds)
            under_eval = _evaluate_binary_offer(
                0.0 if component_fallback.get("lower_bound_only") else under_fair_prob,
                under_odds,
            )
            selected_action = _choose_zero_or_max_action(
                poll.get("maxWager"),
                [
                    {"label": str(over_option.get("label") or ""), "evaluation": over_eval},
                    {"label": str(under_option.get("label") or ""), "evaluation": under_eval},
                ],
            )
            if selected_action is not None:
                selected_eval = selected_action["evaluation"]
                return {
                    **base,
                    "status": str(selected_action.get("status") or "pick"),
                    "recommended_option": str(selected_action.get("label") or ""),
                    "recommended_amount": int(selected_action.get("amount") or 0),
                    "stake_fraction_of_max": round(float(selected_action.get("stake_fraction") or 0.0), 6),
                    "recommended_ev_percent": round(float(_evaluation_value(selected_eval, "ev_percent") or 0.0), 4),
                    "fair_prob": round(float(_evaluation_value(selected_eval, "fair_prob") or 0.0), 6),
                    "fair_odds": int(_evaluation_value(selected_eval, "fair_odds") or probability_to_american(0.5)),
                    "consensus_fair_line": line_value,
                    "matched_books": int(component_fallback.get("matched_books") or 0),
                    "books": str(component_fallback.get("books") or ""),
                    "notes": str(component_fallback.get("source_note") or "MLB HRR component model"),
                    "sportsbook_a_odds": "",
                    "sportsbook_b_odds": "",
                    "source_lines": str(component_fallback.get("source_lines") or ""),
                }
        split_fallback = None
        split_fallback_attempted = False
        if sport == "wnba":
            split_fallback_attempted = True
            split_fallback = _player_split_over_fallback(
                entry,
                sport=sport,
                player_id=player_id,
                player_name=player_name,
                stat_key=stat_key,
                target_line=float(line_value),
            )
        if split_fallback is not None:
            over_fair_prob = float(split_fallback.get("fair_prob") or 0.0)
            over_fair_prob = max(0.0, min(1.0, over_fair_prob))
            under_fair_prob = max(0.0, min(1.0, 1.0 - over_fair_prob))
            over_eval = _evaluate_binary_offer(over_fair_prob, over_odds)
            under_eval = _evaluate_binary_offer(under_fair_prob, under_odds)
            selected_action = _choose_zero_or_max_action(
                poll.get("maxWager"),
                [
                    {"label": str(over_option.get("label") or ""), "evaluation": over_eval},
                    {"label": str(under_option.get("label") or ""), "evaluation": under_eval},
                ],
            )
            if selected_action is not None:
                selected_eval = selected_action["evaluation"]
                return {
                    **base,
                    "status": str(selected_action.get("status") or "pick"),
                    "recommended_option": str(selected_action.get("label") or ""),
                    "recommended_amount": int(selected_action.get("amount") or 0),
                    "stake_fraction_of_max": round(float(selected_action.get("stake_fraction") or 0.0), 6),
                    "recommended_ev_percent": round(float(_evaluation_value(selected_eval, "ev_percent") or 0.0), 4),
                    "fair_prob": round(float(_evaluation_value(selected_eval, "fair_prob") or 0.0), 6),
                    "fair_odds": int(_evaluation_value(selected_eval, "fair_odds") or probability_to_american(0.5)),
                    "consensus_fair_line": "",
                    "matched_books": 0,
                    "books": "",
                    "notes": str(split_fallback.get("source_note") or "Real split fallback"),
                    "sportsbook_a_odds": "",
                    "sportsbook_b_odds": "",
                }
        return {
            **base,
            "status": "no_market",
            "recommended_option": "",
            "recommended_amount": 0,
            "stake_fraction_of_max": 0.0,
            "recommended_ev_percent": "",
            "fair_prob": "",
            "fair_odds": "",
            "matched_books": 0,
            "books": "",
            "notes": (
                "No matching sportsbook player prop quotes found; "
                "Real split fallback returned no usable rows for this player/line."
                if split_fallback_attempted
                else "No matching sportsbook player prop quotes found."
            ),
        }

    snapshot = consensus_snapshot(
        quotes,
        target_line=line_value,
        over_odds=over_odds,
        under_odds=under_odds,
    )
    representative = _representative_market_quote(quotes, line_value)
    source_lines = converted_saves_source_lines or _book_line_summary(quotes, line_value)
    over_eval = snapshot["over"]
    under_eval = snapshot["under"]
    selected_action = _choose_zero_or_max_action(
        poll.get("maxWager"),
        [
            {
                "label": str(over_option.get("label") or ""),
                "evaluation": over_eval,
            },
            {
                "label": str(under_option.get("label") or ""),
                "evaluation": under_eval,
            },
        ],
    )
    if selected_action is None:
        return {
            **base,
            "status": "missing_poll_data",
            "recommended_option": "",
            "recommended_amount": 0,
            "stake_fraction_of_max": 0.0,
            "recommended_ev_percent": "",
            "fair_prob": "",
            "fair_odds": "",
            "matched_books": len(quotes),
            "books": " | ".join(sorted({quote.book for quote in quotes})),
            "notes": "Could not evaluate over/under action choices.",
            "sportsbook_a_odds": representative.over_odds if representative else "",
            "sportsbook_b_odds": representative.under_odds if representative else "",
            "source_lines": source_lines,
        }
    selected_eval = selected_action["evaluation"]
    return {
        **base,
        "status": str(selected_action.get("status") or "pick"),
        "recommended_option": str(selected_action.get("label") or ""),
        "recommended_amount": int(selected_action.get("amount") or 0),
        "stake_fraction_of_max": round(float(selected_action.get("stake_fraction") or 0.0), 6),
        "recommended_ev_percent": round(float(_evaluation_value(selected_eval, "ev_percent") or 0.0), 4),
        "fair_prob": round(float(_evaluation_value(selected_eval, "fair_prob") or 0.0), 6),
        "fair_odds": int(_evaluation_value(selected_eval, "fair_odds") or probability_to_american(0.5)),
        "consensus_fair_line": round(snapshot["estimate"].fair_line, 4),
        "matched_books": len(quotes),
        "books": " | ".join(sorted({quote.book for quote in quotes})),
        "notes": (
            f"{snapshot['estimate'].source}; {converted_saves_note}"
            if converted_saves_note
            else snapshot["estimate"].source
        ),
        "sportsbook_a_odds": representative.over_odds if representative else "",
        "sportsbook_b_odds": representative.under_odds if representative else "",
        "source_lines": source_lines,
    }


def _recommend_game_total(
    entry: dict[str, Any],
    markets: list[MarketRow],
    sport: str,
) -> dict[str, Any]:
    game = entry["game"]
    post = entry["post"]
    poll = entry["poll"]
    additional = poll.get("additionalInfo") or {}
    over_option, under_option = _choose_over_under_options(poll.get("options") or [])
    over_odds = _normalize_option_odds(over_option)
    under_odds = _normalize_option_odds(under_option)
    line = additional.get("overUnderAmount")
    try:
        line_value = float(line)
    except Exception:
        line_value = None
    period = "1H" if sport == "soccer" and str(additional.get("period") or "").strip() == "1" else ""

    base = {
        "poll_id": poll.get("id"),
        "post_id": post.get("id"),
        "sport": sport,
        "game_id": game.get("id"),
        "game_time": game.get("dateTime") or "",
        "home_team": game.get("homeTeamKey") or "",
        "away_team": game.get("awayTeamKey") or "",
        "header": post.get("header") or "",
        "content_text": _first_text(((post.get("content") or {}).get("nodes")) or []),
        "poll_kind": "game_total",
        "player_name": "",
        "stat": "total",
        "line": line_value if line_value is not None else "",
        "can_wager": poll.get("canWager"),
        "max_wager": poll.get("maxWager", ""),
        "option_a_label": str(over_option.get("label") or ""),
        "option_a_odds": over_odds if over_odds is not None else "",
        "option_b_label": str(under_option.get("label") or ""),
        "option_b_odds": under_odds if under_odds is not None else "",
        "sportsbook_a_label": str(over_option.get("label") or ""),
        "sportsbook_a_odds": "",
        "sportsbook_b_label": str(under_option.get("label") or ""),
        "sportsbook_b_odds": "",
    }
    if line_value is None or over_odds is None or under_odds is None:
        return {
            **base,
            "status": "missing_poll_data",
            "recommended_option": "",
            "recommended_amount": 0,
            "stake_fraction_of_max": 0.0,
            "recommended_ev_percent": "",
            "fair_prob": "",
            "fair_odds": "",
            "matched_books": 0,
            "books": "",
            "notes": "Missing total line or option odds.",
        }

    game_quotes = _matching_game_quotes(
        markets,
        sport=sport,
        game=game,
        market_family="game_total",
        period=period,
    )
    quotes = [
        MarketQuote(
            book=market.book,
            line=market.line,
            over_odds=market.over_odds,
            under_odds=market.under_odds,
            updated_at=market.updated_at,
        )
        for market in game_quotes
        if market.line is not None and market.over_odds is not None and market.under_odds is not None
    ]
    if not quotes:
        return {
            **base,
            "status": "no_market",
            "recommended_option": "",
            "recommended_amount": 0,
            "stake_fraction_of_max": 0.0,
            "recommended_ev_percent": "",
            "fair_prob": "",
            "fair_odds": "",
            "matched_books": 0,
            "books": "",
            "notes": "No matching sportsbook total quotes found.",
        }

    consensus_target_line = line_value
    equivalent_line_note = ""
    if sport == "soccer":
        equivalent_line = _integer_goal_total_equivalent_line(line_value)
        has_exact_poll_line = any(abs(float(quote.line) - line_value) <= 1e-9 for quote in quotes)
        has_equivalent_line = (
            equivalent_line is not None
            and any(abs(float(quote.line) - equivalent_line) <= 1e-9 for quote in quotes)
        )
        if not has_exact_poll_line and has_equivalent_line:
            consensus_target_line = float(equivalent_line)
            equivalent_line_note = (
                f"integer-goal equivalent exact line ({consensus_target_line:g})"
            )

    snapshot = consensus_snapshot(
        quotes,
        target_line=consensus_target_line,
        over_odds=over_odds,
        under_odds=under_odds,
        prefer_fitted_ladder=False,
    )
    representative = _representative_market_quote(quotes, consensus_target_line)
    source_lines = _book_line_summary(
        quotes,
        consensus_target_line,
        prefer_exact="fitted line curve" not in str(snapshot["estimate"].source).lower(),
    )
    over_eval = snapshot["over"]
    under_eval = snapshot["under"]
    selected_action = _choose_zero_or_max_action(
        poll.get("maxWager"),
        [
            {
                "label": str(over_option.get("label") or ""),
                "evaluation": over_eval,
            },
            {
                "label": str(under_option.get("label") or ""),
                "evaluation": under_eval,
            },
        ],
    )
    if selected_action is None:
        return {
            **base,
            "status": "missing_poll_data",
            "recommended_option": "",
            "recommended_amount": 0,
            "stake_fraction_of_max": 0.0,
            "recommended_ev_percent": "",
            "fair_prob": "",
            "fair_odds": "",
            "matched_books": _quote_book_count(quotes),
            "books": _quote_books_text(quotes),
            "notes": "Could not evaluate total action choices.",
            "sportsbook_a_odds": representative.over_odds if representative else "",
            "sportsbook_b_odds": representative.under_odds if representative else "",
            "source_lines": source_lines,
        }
    selected_eval = selected_action["evaluation"]
    return {
        **base,
        "status": str(selected_action.get("status") or "pick"),
        "recommended_option": str(selected_action.get("label") or ""),
        "recommended_amount": int(selected_action.get("amount") or 0),
        "stake_fraction_of_max": round(float(selected_action.get("stake_fraction") or 0.0), 6),
        "recommended_ev_percent": round(float(_evaluation_value(selected_eval, "ev_percent") or 0.0), 4),
        "fair_prob": round(float(_evaluation_value(selected_eval, "fair_prob") or 0.0), 6),
        "fair_odds": int(_evaluation_value(selected_eval, "fair_odds") or probability_to_american(0.5)),
        "consensus_fair_line": round(snapshot["estimate"].fair_line, 4),
        "matched_books": _quote_book_count(quotes),
        "books": _quote_books_text(quotes),
        "notes": (
            f"{equivalent_line_note}; {snapshot['estimate'].source}"
            if equivalent_line_note
            else snapshot["estimate"].source
        ),
        "sportsbook_a_odds": representative.over_odds if representative else "",
        "sportsbook_b_odds": representative.under_odds if representative else "",
        "source_lines": source_lines,
    }


def _recommend_two_way_winner(
    entry: dict[str, Any],
    markets: list[MarketRow],
    sport: str,
    *,
    poll_kind: str,
    period: str = "",
    no_market_note: str,
    success_note: str,
) -> dict[str, Any]:
    game = entry["game"]
    post = entry["post"]
    poll = entry["poll"]
    option_map = _build_game_winner_options(poll, game)
    home_option = option_map.get("home") or {}
    away_option = option_map.get("away") or {}
    home_odds = home_option.get("odds")
    away_odds = away_option.get("odds")
    game_quotes = _matching_game_quotes(
        markets,
        sport=sport,
        game=game,
        market_family="game_winner",
        period=period,
    )

    base = {
        "poll_id": poll.get("id"),
        "post_id": post.get("id"),
        "sport": sport,
        "game_id": game.get("id"),
        "game_time": game.get("dateTime") or "",
        "home_team": game.get("homeTeamKey") or "",
        "away_team": game.get("awayTeamKey") or "",
        "header": post.get("header") or "",
        "content_text": _first_text(((post.get("content") or {}).get("nodes")) or []),
        "poll_kind": poll_kind,
        "player_name": "",
        "stat": "winner",
        "line": "",
        "can_wager": poll.get("canWager"),
        "max_wager": poll.get("maxWager", ""),
        "option_a_label": str(home_option.get("label") or ""),
        "option_a_odds": home_odds if home_odds is not None else "",
        "option_b_label": str(away_option.get("label") or ""),
        "option_b_odds": away_odds if away_odds is not None else "",
        "sportsbook_a_label": str(home_option.get("label") or ""),
        "sportsbook_a_odds": "",
        "sportsbook_b_label": str(away_option.get("label") or ""),
        "sportsbook_b_odds": "",
    }
    if home_odds is None or away_odds is None:
        return {
            **base,
            "status": "missing_poll_data",
            "recommended_option": "",
            "recommended_amount": 0,
            "stake_fraction_of_max": 0.0,
            "recommended_ev_percent": "",
            "fair_prob": "",
            "fair_odds": "",
            "matched_books": 0,
            "books": "",
            "notes": "Missing home/away winner option odds.",
        }
    if not game_quotes:
        return {
            **base,
            "status": "no_market",
            "recommended_option": "",
            "recommended_amount": 0,
            "stake_fraction_of_max": 0.0,
            "recommended_ev_percent": "",
            "fair_prob": "",
            "fair_odds": "",
            "matched_books": 0,
            "books": "",
            "notes": no_market_note,
        }

    representative_market = _representative_game_market(game_quotes)
    devigged = [
        devig_quote(
            MarketQuote(
                book=market.book,
                line=0.0,
                over_odds=market.over_odds,
                under_odds=market.under_odds,
                updated_at=market.updated_at,
            )
        )
        for market in game_quotes
        if market.over_odds is not None and market.under_odds is not None
    ]
    if not devigged:
        return {
            **base,
            "status": "no_market",
            "recommended_option": "",
            "recommended_amount": 0,
            "stake_fraction_of_max": 0.0,
            "recommended_ev_percent": "",
            "fair_prob": "",
            "fair_odds": "",
            "matched_books": 0,
            "books": "",
            "notes": "Matching moneyline rows were missing two-way odds.",
        }

    total_weight = sum(quote.weight for quote in devigged)
    fair_home_prob = sum(quote.over_prob * quote.weight for quote in devigged) / total_weight
    fair_away_prob = 1.0 - fair_home_prob
    home_eval = _evaluate_binary_offer(fair_home_prob, int(home_odds))
    away_eval = _evaluate_binary_offer(fair_away_prob, int(away_odds))
    selected_action = _choose_zero_or_max_action(
        poll.get("maxWager"),
        [
            {
                "label": str(home_option.get("label") or game.get("homeTeamKey") or ""),
                "evaluation": home_eval,
            },
            {
                "label": str(away_option.get("label") or game.get("awayTeamKey") or ""),
                "evaluation": away_eval,
            },
        ],
    )
    if selected_action is None:
        return {
            **base,
            "status": "missing_poll_data",
            "recommended_option": "",
            "recommended_amount": 0,
            "stake_fraction_of_max": 0.0,
            "recommended_ev_percent": "",
            "fair_prob": "",
            "fair_odds": "",
            "matched_books": len(devigged),
            "books": " | ".join(book_names),
            "notes": "Could not evaluate winner action choices.",
            "sportsbook_a_odds": representative_market.over_odds if representative_market else "",
            "sportsbook_b_odds": representative_market.under_odds if representative_market else "",
        }
    recommended_eval = selected_action["evaluation"]
    book_names = sorted({market.book for market in game_quotes})
    source_lines = _book_moneyline_summary(
        game_quotes,
        str(home_option.get("label") or game.get("homeTeamKey") or ""),
        str(away_option.get("label") or game.get("awayTeamKey") or ""),
    )
    return {
        **base,
        "status": str(selected_action.get("status") or "pick"),
        "recommended_option": str(selected_action.get("label") or ""),
        "recommended_amount": int(selected_action.get("amount") or 0),
        "stake_fraction_of_max": round(float(selected_action.get("stake_fraction") or 0.0), 6),
        "recommended_ev_percent": round(float(_evaluation_value(recommended_eval, "ev_percent") or 0.0), 4),
        "fair_prob": round(float(_evaluation_value(recommended_eval, "fair_prob") or 0.0), 6),
        "fair_odds": int(_evaluation_value(recommended_eval, "fair_odds") or probability_to_american(0.5)),
        "matched_books": len(devigged),
        "books": " | ".join(book_names),
        "source_lines": source_lines,
        "notes": _moneyline_source_note(success_note, len(book_names)),
        "sportsbook_a_odds": representative_market.over_odds if representative_market else "",
        "sportsbook_b_odds": representative_market.under_odds if representative_market else "",
    }


def _recommend_game_winner(
    entry: dict[str, Any],
    markets: list[MarketRow],
    sport: str,
) -> dict[str, Any]:
    if sport == "soccer":
        return _recommend_multi_outcome(
            entry,
            markets,
            sport,
            market_family="game_winner",
            poll_kind="game_winner",
            stat="winner",
            notes_prefix="3-way moneyline",
        )
    return _recommend_two_way_winner(
        entry,
        markets,
        sport,
        poll_kind="game_winner",
        no_market_note="No matching sportsbook moneyline quotes found.",
        success_note="weighted no-vig moneyline consensus",
    )


def _recommend_team_next_points(
    entry: dict[str, Any],
    markets: list[MarketRow],
    sport: str,
) -> dict[str, Any]:
    direct_recommendation = _recommend_multi_outcome(
        entry,
        markets,
        sport,
        market_family="teamnextpoints",
        poll_kind="teamnextpoints",
        stat="nextpoints",
        notes_prefix="next-score",
    )
    direct_status = str(direct_recommendation.get("status") or "")
    if direct_status not in {"no_market", "missing_poll_data"}:
        return direct_recommendation

    game = entry["game"]
    post = entry["post"]
    poll = entry["poll"]
    options = poll.get("options") or []
    option_fields = _option_fields(options)
    base = {
        "poll_id": poll.get("id"),
        "post_id": post.get("id"),
        "sport": sport,
        "game_id": game.get("id"),
        "game_time": game.get("dateTime") or "",
        "home_team": game.get("homeTeamKey") or "",
        "away_team": game.get("awayTeamKey") or "",
        "header": post.get("header") or "",
        "content_text": _first_text(((post.get("content") or {}).get("nodes")) or []),
        "poll_kind": "teamnextpoints",
        "player_name": "",
        "stat": "nextpoints",
        "line": "",
        "can_wager": poll.get("canWager"),
        "max_wager": poll.get("maxWager", ""),
        **option_fields,
        **_book_outcome_fields([]),
    }

    team_option: dict[str, Any] | None = None
    team_side = ""
    no_more_option: dict[str, Any] | None = None
    for option in options:
        option_key = _canonical_poll_option_key(option, game)
        if option_key in {"home", "away"} and team_option is None:
            team_option = option
            team_side = option_key
            continue
        if option_key == "no_more" and no_more_option is None:
            no_more_option = option

    if team_option is None or no_more_option is None:
        return {
            **base,
            "status": "no_market",
            "recommended_option": "",
            "recommended_amount": 0,
            "stake_fraction_of_max": 0.0,
            "recommended_ev_percent": "",
            "fair_prob": "",
            "fair_odds": "",
            "matched_books": 0,
            "books": "",
            "notes": "Could not align next-score poll options to a team side and 'No more'.",
        }

    team_odds = _normalize_option_odds(team_option)
    no_more_odds = _normalize_option_odds(no_more_option)
    if team_odds is None or no_more_odds is None:
        return {
            **base,
            "status": "missing_poll_data",
            "recommended_option": "",
            "recommended_amount": 0,
            "stake_fraction_of_max": 0.0,
            "recommended_ev_percent": "",
            "fair_prob": "",
            "fair_odds": "",
            "matched_books": 0,
            "books": "",
            "notes": "Missing option odds for next-score poll.",
        }

    moneyline_rows = _matching_game_quotes(
        markets,
        sport=sport,
        game=game,
        market_family="game_winner",
    )
    if not moneyline_rows:
        return {
            **base,
            "status": "no_market",
            "recommended_option": "",
            "recommended_amount": 0,
            "stake_fraction_of_max": 0.0,
            "recommended_ev_percent": "",
            "fair_prob": "",
            "fair_odds": "",
            "matched_books": 0,
            "books": "",
            "notes": "No direct next-score market matched, and no moneyline proxy quotes were found.",
        }

    devigged = [
        devig_quote(
            MarketQuote(
                book=market.book,
                line=0.0,
                over_odds=market.over_odds,
                under_odds=market.under_odds,
                updated_at=market.updated_at,
            )
        )
        for market in moneyline_rows
        if market.over_odds is not None and market.under_odds is not None
    ]
    if not devigged:
        return {
            **base,
            "status": "no_market",
            "recommended_option": "",
            "recommended_amount": 0,
            "stake_fraction_of_max": 0.0,
            "recommended_ev_percent": "",
            "fair_prob": "",
            "fair_odds": "",
            "matched_books": 0,
            "books": "",
            "notes": "Moneyline proxy rows were missing two-way odds.",
        }

    total_weight = sum(quote.weight for quote in devigged)
    fair_home_prob = sum(quote.over_prob * quote.weight for quote in devigged) / total_weight
    fair_away_prob = 1.0 - fair_home_prob
    fair_team_prob = fair_home_prob if team_side == "home" else fair_away_prob
    fair_no_more_prob = 1.0 - fair_team_prob
    team_eval = _evaluate_binary_offer(fair_team_prob, int(team_odds))
    no_more_eval = _evaluate_binary_offer(fair_no_more_prob, int(no_more_odds))
    selected_action = _choose_zero_or_max_action(
        poll.get("maxWager"),
        [
            {
                "label": str(team_option.get("label") or ""),
                "evaluation": team_eval,
            },
            {
                "label": str(no_more_option.get("label") or ""),
                "evaluation": no_more_eval,
            },
        ],
    )
    if selected_action is None:
        return {
            **base,
            "status": "missing_poll_data",
            "recommended_option": "",
            "recommended_amount": 0,
            "stake_fraction_of_max": 0.0,
            "recommended_ev_percent": "",
            "fair_prob": "",
            "fair_odds": "",
            "matched_books": len(devigged),
            "books": " | ".join(sorted({market.book for market in moneyline_rows})),
            "notes": "Could not evaluate next-score proxy action choices.",
        }

    representative_market = _representative_game_market(moneyline_rows)
    team_book_odds = ""
    if representative_market is not None:
        team_book_odds = (
            representative_market.over_odds
            if team_side == "home"
            else representative_market.under_odds
        )
    selected_eval = selected_action["evaluation"]
    return {
        **base,
        "sportsbook_a_label": str(team_option.get("label") or ""),
        "sportsbook_a_odds": team_book_odds if team_book_odds is not None else "",
        "sportsbook_b_label": str(no_more_option.get("label") or ""),
        "sportsbook_b_odds": "",
        "status": str(selected_action.get("status") or "pick"),
        "recommended_option": str(selected_action.get("label") or ""),
        "recommended_amount": int(selected_action.get("amount") or 0),
        "stake_fraction_of_max": round(float(selected_action.get("stake_fraction") or 0.0), 6),
        "recommended_ev_percent": round(float(_evaluation_value(selected_eval, "ev_percent") or 0.0), 4),
        "fair_prob": round(float(_evaluation_value(selected_eval, "fair_prob") or 0.0), 6),
        "fair_odds": int(_evaluation_value(selected_eval, "fair_odds") or probability_to_american(0.5)),
        "matched_books": len(devigged),
        "books": " | ".join(sorted({market.book for market in moneyline_rows})),
        "notes": "No direct next-score market matched; used weighted no-vig moneyline proxy.",
    }


def _period_market_code(sport: str, period_value: str) -> str:
    normalized_period = str(period_value or "").strip()
    if not normalized_period:
        return ""
    period_key = normalized_period.upper()
    if period_key.endswith(("I", "Q", "P", "H")):
        return period_key
    if sport == "mlb" and normalized_period.isdigit():
        return f"{normalized_period}I"
    if sport in {"nba", "wnba"} and normalized_period in {"1", "2", "3", "4"}:
        return f"{normalized_period}Q"
    if sport == "nhl" and normalized_period in {"1", "2", "3"}:
        return f"{normalized_period}P"
    if sport == "nhl" and normalized_period == "4":
        return "OT"
    if sport == "soccer" and normalized_period in {"1", "2"}:
        return f"{normalized_period}H"
    return ""


def _recommend_period_winner(
    entry: dict[str, Any],
    markets: list[MarketRow],
    sport: str,
) -> dict[str, Any]:
    game = entry["game"]
    post = entry["post"]
    poll = entry["poll"]
    additional = poll.get("additionalInfo") or {}
    option_map = _build_game_winner_options(poll, game)
    home_option = option_map.get("home") or {}
    away_option = option_map.get("away") or {}
    home_odds = home_option.get("odds")
    away_odds = away_option.get("odds")
    period_value = str(additional.get("period") or "").strip()
    market_period = _period_market_code(sport, period_value)

    base = {
        "poll_id": poll.get("id"),
        "post_id": post.get("id"),
        "sport": sport,
        "game_id": game.get("id"),
        "game_time": game.get("dateTime") or "",
        "home_team": game.get("homeTeamKey") or "",
        "away_team": game.get("awayTeamKey") or "",
        "header": post.get("header") or "",
        "content_text": _first_text(((post.get("content") or {}).get("nodes")) or []),
        "poll_kind": "period_winner",
        "player_name": "",
        "stat": "winner",
        "line": "",
        "can_wager": poll.get("canWager"),
        "max_wager": poll.get("maxWager", ""),
        "option_a_label": str(home_option.get("label") or ""),
        "option_a_odds": home_odds if home_odds is not None else "",
        "option_b_label": str(away_option.get("label") or ""),
        "option_b_odds": away_odds if away_odds is not None else "",
        "sportsbook_a_label": str(home_option.get("label") or ""),
        "sportsbook_a_odds": "",
        "sportsbook_b_label": str(away_option.get("label") or ""),
        "sportsbook_b_odds": "",
    }
    if home_odds is None or away_odds is None or not market_period:
        return {
            **base,
            "status": "missing_poll_data",
            "recommended_option": "",
            "recommended_amount": 0,
            "stake_fraction_of_max": 0.0,
            "recommended_ev_percent": "",
            "fair_prob": "",
            "fair_odds": "",
            "matched_books": 0,
            "books": "",
            "notes": "Missing period code or home/away winner option odds.",
        }

    moneyline_recommendation = _recommend_two_way_winner(
        entry,
        markets,
        sport,
        poll_kind="period_winner",
        period=market_period,
        no_market_note=f"No matching sportsbook {market_period} moneyline quotes found.",
        success_note=f"weighted no-vig {market_period} moneyline consensus",
    )
    if str(moneyline_recommendation.get("status") or "") != "no_market":
        return moneyline_recommendation

    spread_rows = _matching_game_quotes(
        markets,
        sport=sport,
        game=game,
        market_family="game_spread",
        period=market_period,
    )
    quotes: list[MarketQuote] = []
    for market in spread_rows:
        try:
            home_spread = float((market.raw or {}).get("home_spread"))
        except Exception:
            home_spread = None
        if home_spread is None or market.over_odds is None or market.under_odds is None:
            continue
        quotes.append(
            MarketQuote(
                book=market.book,
                line=-home_spread,
                over_odds=market.over_odds,
                under_odds=market.under_odds,
                updated_at=market.updated_at,
            )
        )
    if not quotes:
        return {
            **base,
            "status": "no_market",
            "recommended_option": "",
            "recommended_amount": 0,
            "stake_fraction_of_max": 0.0,
            "recommended_ev_percent": "",
            "fair_prob": "",
            "fair_odds": "",
            "matched_books": 0,
            "books": "",
            "notes": f"No matching sportsbook {market_period} spread quotes found for period winner.",
        }

    target_line = 0.5
    representative = _representative_market_quote(quotes, target_line)
    snapshot = consensus_snapshot(
        quotes,
        target_line=target_line,
        over_odds=int(home_odds),
        under_odds=int(away_odds),
        default_scale=4.0,
    )
    home_eval = snapshot["over"]
    away_eval = snapshot["under"]
    selected_action = _choose_zero_or_max_action(
        poll.get("maxWager"),
        [
            {
                "label": str(home_option.get("label") or game.get("homeTeamKey") or ""),
                "evaluation": home_eval,
            },
            {
                "label": str(away_option.get("label") or game.get("awayTeamKey") or ""),
                "evaluation": away_eval,
            },
        ],
    )
    if selected_action is None:
        return {
            **base,
            "status": "missing_poll_data",
            "recommended_option": "",
            "recommended_amount": 0,
            "stake_fraction_of_max": 0.0,
            "recommended_ev_percent": "",
            "fair_prob": "",
            "fair_odds": "",
            "matched_books": len(quotes),
            "books": " | ".join(sorted({quote.book for quote in quotes})),
            "notes": f"Could not evaluate {market_period} winner action choices.",
            "sportsbook_a_odds": representative.over_odds if representative else "",
            "sportsbook_b_odds": representative.under_odds if representative else "",
        }
    selected_eval = selected_action["evaluation"]
    return {
        **base,
        "status": str(selected_action.get("status") or "pick"),
        "recommended_option": str(selected_action.get("label") or ""),
        "recommended_amount": int(selected_action.get("amount") or 0),
        "stake_fraction_of_max": round(float(selected_action.get("stake_fraction") or 0.0), 6),
        "recommended_ev_percent": round(float(_evaluation_value(selected_eval, "ev_percent") or 0.0), 4),
        "fair_prob": round(float(_evaluation_value(selected_eval, "fair_prob") or 0.0), 6),
        "fair_odds": int(_evaluation_value(selected_eval, "fair_odds") or probability_to_american(0.5)),
        "consensus_fair_line": round(snapshot["estimate"].fair_line, 4),
        "matched_books": len(quotes),
        "books": " | ".join(sorted({quote.book for quote in quotes})),
        "notes": f"{snapshot['estimate'].source}; {market_period} spread proxy for period winner",
        "sportsbook_a_odds": representative.over_odds if representative else "",
        "sportsbook_b_odds": representative.under_odds if representative else "",
    }


def _recommend_game_spread(
    entry: dict[str, Any],
    markets: list[MarketRow],
    sport: str,
) -> dict[str, Any]:
    game = entry["game"]
    post = entry["post"]
    poll = entry["poll"]
    additional = poll.get("additionalInfo") or {}
    option_map = _build_game_winner_options(poll, game)
    home_option = option_map.get("home") or {}
    away_option = option_map.get("away") or {}
    home_odds = home_option.get("odds")
    away_odds = away_option.get("odds")
    point_spread_value = additional.get("pointSpread")
    if point_spread_value in (None, "", "None"):
        point_spread_value = poll.get("pointSpread")
    try:
        point_spread = abs(float(point_spread_value))
    except Exception:
        point_spread = None
    spread_team_id = str(additional.get("spreadTeamId") or poll.get("spreadTeamId") or "").strip()
    home_team_id = str(poll.get("homeTeamId") or additional.get("homeTeamId") or "").strip()
    away_team_id = str(poll.get("awayTeamId") or additional.get("awayTeamId") or "").strip()

    base = {
        "poll_id": poll.get("id"),
        "post_id": post.get("id"),
        "sport": sport,
        "game_id": game.get("id"),
        "game_time": game.get("dateTime") or "",
        "home_team": game.get("homeTeamKey") or "",
        "away_team": game.get("awayTeamKey") or "",
        "header": post.get("header") or "",
        "content_text": _first_text(((post.get("content") or {}).get("nodes")) or []),
        "poll_kind": "game_spread",
        "player_name": "",
        "stat": "spread",
        "line": point_spread if point_spread is not None else "",
        "can_wager": poll.get("canWager"),
        "max_wager": poll.get("maxWager", ""),
        "option_a_label": str(home_option.get("label") or ""),
        "option_a_odds": home_odds if home_odds is not None else "",
        "option_b_label": str(away_option.get("label") or ""),
        "option_b_odds": away_odds if away_odds is not None else "",
        "sportsbook_a_label": str(home_option.get("label") or ""),
        "sportsbook_a_odds": "",
        "sportsbook_b_label": str(away_option.get("label") or ""),
        "sportsbook_b_odds": "",
    }
    if home_odds is None or away_odds is None or point_spread is None:
        return {
            **base,
            "status": "missing_poll_data",
            "recommended_option": "",
            "recommended_amount": 0,
            "stake_fraction_of_max": 0.0,
            "recommended_ev_percent": "",
            "fair_prob": "",
            "fair_odds": "",
            "matched_books": 0,
            "books": "",
            "notes": "Missing live spread line or option odds.",
        }

    target_home_line: float | None = None
    spread_team_missing = spread_team_id not in {home_team_id, away_team_id}
    if not spread_team_missing:
        target_home_line = point_spread if spread_team_id == home_team_id else -point_spread
    spread_rows = _matching_game_quotes(
        markets,
        sport=sport,
        game=game,
        market_family="game_spread",
    )
    quotes: list[MarketQuote] = []
    for market in spread_rows:
        try:
            home_spread = float((market.raw or {}).get("home_spread"))
        except Exception:
            home_spread = None
        if home_spread is None or market.over_odds is None or market.under_odds is None:
            continue
        quotes.append(
            MarketQuote(
                book=market.book,
                line=-home_spread,
                over_odds=market.over_odds,
                under_odds=market.under_odds,
                updated_at=market.updated_at,
            )
        )
    if not quotes:
        return {
            **base,
            "status": "no_market",
            "recommended_option": "",
            "recommended_amount": 0,
            "stake_fraction_of_max": 0.0,
            "recommended_ev_percent": "",
            "fair_prob": "",
            "fair_odds": "",
            "matched_books": 0,
            "books": "",
            "notes": "No matching sportsbook live spread quotes found.",
        }
    if target_home_line is None:
        inferred_home_line_candidates = [point_spread, -point_spread]

        def _candidate_key(candidate: float) -> tuple[float, int]:
            nearest = min(abs(quote.line - candidate) for quote in quotes)
            exact_matches = sum(1 for quote in quotes if abs(quote.line - candidate) <= 1e-9)
            return nearest, -exact_matches

        target_home_line = min(inferred_home_line_candidates, key=_candidate_key)

    representative = _representative_market_quote(quotes, target_home_line)
    snapshot = consensus_snapshot(
        quotes,
        target_line=target_home_line,
        over_odds=int(home_odds),
        under_odds=int(away_odds),
        default_scale=6.0,
    )
    home_eval = snapshot["over"]
    away_eval = snapshot["under"]
    selected_action = _choose_zero_or_max_action(
        poll.get("maxWager"),
        [
            {
                "label": str(home_option.get("label") or game.get("homeTeamKey") or ""),
                "evaluation": home_eval,
            },
            {
                "label": str(away_option.get("label") or game.get("awayTeamKey") or ""),
                "evaluation": away_eval,
            },
        ],
    )
    if selected_action is None:
        return {
            **base,
            "status": "missing_poll_data",
            "recommended_option": "",
            "recommended_amount": 0,
            "stake_fraction_of_max": 0.0,
            "recommended_ev_percent": "",
            "fair_prob": "",
            "fair_odds": "",
            "matched_books": len(quotes),
            "books": " | ".join(sorted({quote.book for quote in quotes})),
            "notes": "Could not evaluate live spread action choices.",
            "sportsbook_a_odds": representative.over_odds if representative else "",
            "sportsbook_b_odds": representative.under_odds if representative else "",
        }
    selected_eval = selected_action["evaluation"]
    return {
        **base,
        "status": str(selected_action.get("status") or "pick"),
        "recommended_option": str(selected_action.get("label") or ""),
        "recommended_amount": int(selected_action.get("amount") or 0),
        "stake_fraction_of_max": round(float(selected_action.get("stake_fraction") or 0.0), 6),
        "recommended_ev_percent": round(float(_evaluation_value(selected_eval, "ev_percent") or 0.0), 4),
        "fair_prob": round(float(_evaluation_value(selected_eval, "fair_prob") or 0.0), 6),
        "fair_odds": int(_evaluation_value(selected_eval, "fair_odds") or probability_to_american(0.5)),
        "consensus_fair_line": round(snapshot["estimate"].fair_line, 4),
        "matched_books": len(quotes),
        "books": " | ".join(sorted({quote.book for quote in quotes})),
        "notes": (
            f"{snapshot['estimate'].source}; live spread consensus"
            + ("; inferred spread side from sportsbook lines" if spread_team_missing else "")
        ),
        "sportsbook_a_odds": representative.over_odds if representative else "",
        "sportsbook_b_odds": representative.under_odds if representative else "",
    }


def _recommend_period_total_yes_no(
    entry: dict[str, Any],
    markets: list[MarketRow],
    sport: str,
) -> dict[str, Any]:
    game = entry["game"]
    post = entry["post"]
    poll = entry["poll"]
    additional = poll.get("additionalInfo") or {}
    over_option, under_option = _choose_over_under_options(poll.get("options") or [])
    over_odds = _normalize_option_odds(over_option)
    under_odds = _normalize_option_odds(under_option)
    period = str(additional.get("period") or "").strip()
    market_period = _period_market_code(sport, period)

    line_value = None
    points = additional.get("points")
    if points not in (None, "", "None"):
        try:
            line_value = float(points) - 0.5
        except Exception:
            line_value = None
    if line_value is None:
        over_under_amount = additional.get("overUnderAmount")
        if over_under_amount not in (None, "", "None"):
            try:
                line_value = float(over_under_amount)
            except Exception:
                line_value = None

    base = {
        "poll_id": poll.get("id"),
        "post_id": post.get("id"),
        "sport": sport,
        "game_id": game.get("id"),
        "game_time": game.get("dateTime") or "",
        "home_team": game.get("homeTeamKey") or "",
        "away_team": game.get("awayTeamKey") or "",
        "header": post.get("header") or "",
        "content_text": _first_text(((post.get("content") or {}).get("nodes")) or []),
        "poll_kind": "period_total_yes_no",
        "player_name": "",
        "stat": "total",
        "line": line_value if line_value is not None else "",
        "can_wager": poll.get("canWager"),
        "max_wager": poll.get("maxWager", ""),
        "option_a_label": str(over_option.get("label") or ""),
        "option_a_odds": over_odds if over_odds is not None else "",
        "option_b_label": str(under_option.get("label") or ""),
        "option_b_odds": under_odds if under_odds is not None else "",
        "sportsbook_a_label": str(over_option.get("label") or ""),
        "sportsbook_a_odds": "",
        "sportsbook_b_label": str(under_option.get("label") or ""),
        "sportsbook_b_odds": "",
    }
    if line_value is None or over_odds is None or under_odds is None or not market_period:
        return {
            **base,
            "status": "missing_poll_data",
            "recommended_option": "",
            "recommended_amount": 0,
            "stake_fraction_of_max": 0.0,
            "recommended_ev_percent": "",
            "fair_prob": "",
            "fair_odds": "",
            "matched_books": 0,
            "books": "",
            "notes": "Missing period total line, period, or option odds.",
        }

    game_quotes = _matching_game_quotes(
        markets,
        sport=sport,
        game=game,
        market_family="game_total",
        period=market_period,
    )
    quotes = [
        MarketQuote(
            book=market.book,
            line=market.line,
            over_odds=market.over_odds,
            under_odds=market.under_odds,
            updated_at=market.updated_at,
        )
        for market in game_quotes
        if market.line is not None and market.over_odds is not None and market.under_odds is not None
    ]
    if not quotes:
        return {
            **base,
            "status": "no_market",
            "recommended_option": "",
            "recommended_amount": 0,
            "stake_fraction_of_max": 0.0,
            "recommended_ev_percent": "",
            "fair_prob": "",
            "fair_odds": "",
            "matched_books": 0,
            "books": "",
            "notes": "No matching period total runs quotes found.",
        }

    snapshot = consensus_snapshot(
        quotes,
        target_line=line_value,
        over_odds=over_odds,
        under_odds=under_odds,
    )
    representative = _representative_market_quote(quotes, line_value)
    source_lines = _book_line_summary(quotes, line_value, prefer_exact=True)
    over_eval = snapshot["over"]
    under_eval = snapshot["under"]
    selected_action = _choose_zero_or_max_action(
        poll.get("maxWager"),
        [
            {
                "label": str(over_option.get("label") or ""),
                "evaluation": over_eval,
            },
            {
                "label": str(under_option.get("label") or ""),
                "evaluation": under_eval,
            },
        ],
    )
    if selected_action is None:
        return {
            **base,
            "status": "missing_poll_data",
            "recommended_option": "",
            "recommended_amount": 0,
            "stake_fraction_of_max": 0.0,
            "recommended_ev_percent": "",
            "fair_prob": "",
            "fair_odds": "",
            "matched_books": _quote_book_count(quotes),
            "books": _quote_books_text(quotes),
            "notes": "Could not evaluate period total action choices.",
            "sportsbook_a_odds": representative.over_odds if representative else "",
            "sportsbook_b_odds": representative.under_odds if representative else "",
            "source_lines": source_lines,
        }
    selected_eval = selected_action["evaluation"]
    return {
        **base,
        "status": str(selected_action.get("status") or "pick"),
        "recommended_option": str(selected_action.get("label") or ""),
        "recommended_amount": int(selected_action.get("amount") or 0),
        "stake_fraction_of_max": round(float(selected_action.get("stake_fraction") or 0.0), 6),
        "recommended_ev_percent": round(float(_evaluation_value(selected_eval, "ev_percent") or 0.0), 4),
        "fair_prob": round(float(_evaluation_value(selected_eval, "fair_prob") or 0.0), 6),
        "fair_odds": int(_evaluation_value(selected_eval, "fair_odds") or probability_to_american(0.5)),
        "consensus_fair_line": round(snapshot["estimate"].fair_line, 4),
        "matched_books": _quote_book_count(quotes),
        "books": _quote_books_text(quotes),
        "notes": f"{snapshot['estimate'].source}; period total runs",
        "sportsbook_a_odds": representative.over_odds if representative else "",
        "sportsbook_b_odds": representative.under_odds if representative else "",
        "source_lines": source_lines,
    }


def _market_proxy_fields(
    markets: list[MarketRow],
    *,
    sport: str,
    game: dict[str, Any],
    player_name: str,
    stat_key: str,
    target_line: float | None,
) -> dict[str, Any]:
    empty = {
        "recommended_ev_percent": "",
        "fair_prob": "",
        "fair_odds": "",
        "consensus_fair_line": "",
        "matched_books": 0,
        "books": "",
        "sportsbook_a_label": "",
        "sportsbook_a_odds": "",
        "sportsbook_b_label": "",
        "sportsbook_b_odds": "",
        "proxy_note": "",
    }
    if target_line is None:
        empty["proxy_note"] = f"No target line available for {stat_key}."
        return empty

    quotes = _matching_player_quotes(
        markets,
        sport=sport,
        game=game,
        player_name=player_name,
        stat_key=stat_key,
    )
    if not quotes:
        empty["proxy_note"] = f"No matching sportsbook {stat_key} market found."
        return empty

    representative = _representative_market_quote(quotes, target_line)
    if representative is None:
        empty["proxy_note"] = f"No representative sportsbook {stat_key} quote found."
        return empty

    snapshot = consensus_snapshot(
        quotes,
        target_line=target_line,
        over_odds=representative.over_odds,
        under_odds=representative.under_odds,
    )
    over_eval = snapshot["over"]
    return {
        "recommended_ev_percent": round(over_eval.ev_percent, 4),
        "fair_prob": round(over_eval.fair_prob, 6),
        "fair_odds": over_eval.fair_odds,
        "consensus_fair_line": round(snapshot["estimate"].fair_line, 4),
        "matched_books": len(quotes),
        "books": " | ".join(sorted({quote.book for quote in quotes})),
        "sportsbook_a_label": player_name,
        "sportsbook_a_odds": representative.over_odds if representative else "",
        "sportsbook_b_label": "",
        "sportsbook_b_odds": "",
        "proxy_note": snapshot["estimate"].source,
    }


def _threshold_to_market_line(value: Any) -> float | None:
    try:
        return float(value) - 0.5
    except Exception:
        return None


def _normalize_pick_stat(value: Any) -> str:
    key = normalize_text(str(value or "")).replace(" ", "").replace("+", "")
    aliases = {
        "point": "points",
        "points": "points",
        "rebound": "rebounds",
        "rebounds": "rebounds",
        "assist": "assists",
        "assists": "assists",
        "steal": "steals",
        "steals": "steals",
        "block": "blocks",
        "blocks": "blocks",
        "goal": "goals",
        "goals": "goals",
        "chance": "chancescreated",
        "chances": "chancescreated",
        "chancecreated": "chancescreated",
        "chancescreated": "chancescreated",
        "keypass": "chancescreated",
        "keypasses": "chancescreated",
        "shotsassisted": "chancescreated",
        "shot": "shots",
        "shots": "shots",
        "shotongoal": "shots",
        "shotsongoal": "shots",
        "rbi": "rbis",
        "rbis": "rbis",
        "runsbattedin": "rbis",
        "totalbases": "totalbases",
        "3pt": "madethrees",
        "3pts": "madethrees",
        "3pointer": "madethrees",
        "3pointers": "madethrees",
        "3pointersmade": "madethrees",
        "3pm": "madethrees",
        "madethrees": "madethrees",
        "threes": "madethrees",
    }
    normalized = aliases.get(key)
    if normalized:
        return normalized
    return normalize_stat(str(value or ""))


def _resolve_zero_cost_stat_key(entry: dict[str, Any], poll_kind: str) -> str:
    poll = entry.get("poll") or {}
    game = entry.get("game") or {}
    post = entry.get("post") or {}
    additional = poll.get("additionalInfo") or {}
    if poll_kind == "first_basket":
        return "firstbasket"
    sport = str(additional.get("sport") or game.get("sport") or "").strip().lower()
    stat_key = _normalize_pick_stat(additional.get("stat") or "")
    if stat_key:
        return stat_key

    params = additional.get("params") or {}
    try:
        target_points = int(float(params.get("points")))
    except Exception:
        target_points = None
    if target_points == 3:
        return "madethrees"
    if target_points == 1:
        if not (sport == "mlb" and poll_kind == "anytime_play"):
            return "points"

    play_types = additional.get("playTypes") or []
    text_parts = [
        str(post.get("header") or "").strip(),
        _first_text(((post.get("content") or {}).get("nodes")) or []),
        str(additional.get("playType") or "").strip(),
        " ".join(str(item).strip() for item in play_types if str(item).strip()),
    ]
    combined_text = " ".join(part for part in text_parts if part).lower()
    if "shot on goal" in combined_text or "shots on goal" in combined_text or " shot " in f" {combined_text} ":
        return "shots"
    if "assist" in combined_text:
        return "assists"
    if "chance" in combined_text or "key pass" in combined_text or "key passes" in combined_text:
        return "chancescreated"
    if "goal" in combined_text:
        return "goals"
    if "rbi" in combined_text:
        return "rbis"
    if " run " in f" {combined_text} " or "runs" in combined_text:
        return "runs"
    if "rebound" in combined_text:
        return "rebounds"
    if "point" in combined_text:
        return "points"
    if "block" in combined_text:
        return "blocks"
    if "steal" in combined_text:
        return "steals"
    if "3-point" in combined_text or "3pt" in combined_text or "three" in combined_text:
        return "madethrees"
    return ""


def _player_name_from_payload(player: dict[str, Any]) -> str:
    display_name = str(
        player.get("displayName")
        or player.get("fullName")
        or ""
    ).strip()
    if display_name:
        return display_name
    first_name = str(player.get("firstName") or "").strip()
    last_name = str(player.get("lastName") or "").strip()
    return " ".join(part for part in (first_name, last_name) if part).strip()


def _allowed_game_players(game_payload: dict[str, Any]) -> set[str]:
    players: set[str] = set()
    for player in game_payload.get("players") or []:
        if not isinstance(player, dict):
            continue
        names = [
            _player_name_from_payload(player),
            player.get("displayName"),
            player.get("fullName"),
            " ".join(
                part
                for part in (
                    str(player.get("firstName") or "").strip(),
                    str(player.get("lastName") or "").strip(),
                )
                if part
            ),
        ]
        for player_name in names:
            if player_name:
                players.add(normalize_player_name(str(player_name)))
    return players


def _group_same_game_player_markets(
    markets: list[MarketRow],
    *,
    sport: str,
    game: dict[str, Any],
    market_family: str,
    stat_key: str = "",
    allowed_players: set[str] | None = None,
) -> tuple[dict[str, list[MarketRow]], dict[str, str]]:
    grouped: dict[str, list[MarketRow]] = {}
    display_names: dict[str, str] = {}
    for market in markets:
        if market.sport != sport or market.market_family != market_family:
            continue
        if stat_key and market.stat_key != stat_key:
            continue
        if not _same_game(game, market) or not market.player_name:
            continue
        if allowed_players and market.player_name not in allowed_players:
            continue
        grouped.setdefault(market.player_name, []).append(market)
        display_names.setdefault(
            market.player_name,
            clean_player_name(str(market.raw.get("player_name") or "").strip()) or market.player_name,
        )
    return grouped, display_names


def _group_active_day_player_markets(
    markets: list[MarketRow],
    *,
    sport: str,
    active_games: list[dict[str, Any]],
    market_family: str,
    stat_key: str = "",
) -> tuple[dict[str, list[MarketRow]], dict[str, str]]:
    allowed_pairs: set[tuple[str, str]] = set()
    for game in active_games:
        if not isinstance(game, dict):
            continue
        key_pair = team_pair(game.get("homeTeamKey") or "", game.get("awayTeamKey") or "")
        if key_pair != team_pair("", ""):
            allowed_pairs.add(key_pair)
        home_team_info = game.get("homeTeam")
        away_team_info = game.get("awayTeam")
        if isinstance(home_team_info, dict) and isinstance(away_team_info, dict):
            name_pair = team_pair(
                home_team_info.get("displayName") or home_team_info.get("name") or "",
                away_team_info.get("displayName") or away_team_info.get("name") or "",
            )
            if name_pair != team_pair("", ""):
                allowed_pairs.add(name_pair)
    grouped: dict[str, list[MarketRow]] = {}
    display_names: dict[str, str] = {}
    for market in markets:
        if market.sport != sport or market.market_family != market_family:
            continue
        if stat_key and market.stat_key != stat_key:
            continue
        if not market.player_name:
            continue
        if team_pair(market.home_team, market.away_team) not in allowed_pairs:
            continue
        grouped.setdefault(market.player_name, []).append(market)
        display_names.setdefault(
            market.player_name,
            clean_player_name(str(market.raw.get("player_name") or "").strip()) or market.player_name,
        )
    return grouped, display_names


def _player_market_candidate(
    player_key: str,
    display_name: str,
    markets: list[MarketRow],
    *,
    target_line: float,
) -> dict[str, Any] | None:
    quotes = [
        MarketQuote(
            book=market.book,
            line=market.line,
            over_odds=market.over_odds,
            under_odds=market.under_odds,
            updated_at=market.updated_at,
        )
        for market in markets
        if market.line is not None and market.over_odds is not None and market.under_odds is not None
    ]
    if not quotes:
        return None

    representative = _representative_market_quote(quotes, target_line)
    if representative is None:
        return None

    source_lines = _book_line_summary(quotes, target_line, prefer_exact=True)
    snapshot = consensus_snapshot(
        quotes,
        target_line=target_line,
        over_odds=representative.over_odds,
        under_odds=representative.under_odds,
    )
    over_eval = snapshot["over"]
    return {
        "player_key": player_key,
        "selection": display_name,
        "fair_prob": float(over_eval.fair_prob),
        "fair_odds": int(over_eval.fair_odds),
        "consensus_fair_line": round(snapshot["estimate"].fair_line, 4),
        "matched_books": _quote_book_count(quotes),
        "books": _quote_books_text(quotes),
        "sportsbook_odds": representative.over_odds,
        "source_lines": source_lines,
        "source_note": str(snapshot["estimate"].source),
    }


def _normalize_probability_value(value: Any) -> float | None:
    try:
        probability = float(value)
    except Exception:
        return None
    if probability > 1.0:
        probability /= 100.0
    if probability < 0.0 or probability > 1.0:
        return None
    return probability


def _split_over_probability(split: dict[str, Any]) -> float | None:
    try:
        times_over = float(split.get("timesOver"))
        times_under = float(split.get("timesUnder"))
        total_trials = times_over + times_under
        if total_trials > 0:
            return (times_over + 1.0) / (total_trials + 2.0)
    except Exception:
        pass
    return _normalize_probability_value(split.get("pctTimesOver"))


def _split_priority(split: dict[str, Any]) -> tuple[int, int, str]:
    label = normalize_text(
        str(split.get("label") or split.get("name") or split.get("type") or "")
    ).replace(" ", "")
    try:
        num_games = int(float(split.get("numGames") or 0))
    except Exception:
        num_games = 0
    if label in {"l20", "last20"} or num_games == 20:
        return 0, -num_games, label
    if label in {"l10", "last10"} or num_games == 10:
        return 1, -num_games, label
    if label in {"l5", "last5"} or num_games == 5:
        return 2, -num_games, label
    if label == "overall":
        return 3, -num_games, label
    return 4, -num_games, label


def _best_split_row(splits: list[dict[str, Any]]) -> dict[str, Any] | None:
    candidates = [split for split in splits if isinstance(split, dict)]
    if not candidates:
        return None
    return sorted(candidates, key=_split_priority)[0]


def _best_split_row_with_type_preference(
    splits: list[dict[str, Any]],
    preferred_types: tuple[str, ...] = (),
) -> dict[str, Any] | None:
    candidates = [split for split in splits if isinstance(split, dict)]
    if not candidates:
        return None
    normalized_preferences = tuple(normalize_text(value) for value in preferred_types if value)
    for preferred_type in normalized_preferences:
        typed_candidates = [
            split
            for split in candidates
            if normalize_text(str(split.get("type") or "")) == preferred_type
        ]
        if typed_candidates:
            return sorted(typed_candidates, key=_split_priority)[0]
    return sorted(candidates, key=_split_priority)[0]


@lru_cache(maxsize=1024)
def _cached_player_boxscore_splits(
    entity_id: str,
    sport: str,
    stat_type: int,
    target_line: str,
) -> dict[str, Any]:
    client = build_realsports_client()
    return client.get_player_boxscore_splits(
        entity_id=entity_id,
        entity_type="player",
        sport=sport,
        stat_type=stat_type,
        value=target_line,
    )


def _nba_split_fallback_candidates(
    entry: dict[str, Any],
    *,
    stat_key: str,
    target_line: float,
) -> list[dict[str, Any]]:
    stat_type = NBA_SPLIT_STAT_TYPES.get(stat_key)
    if stat_type is None:
        return []

    game_payload = entry.get("game_payload") or {}
    candidates: list[dict[str, Any]] = []
    for player in game_payload.get("players") or []:
        if not isinstance(player, dict):
            continue
        entity_id = str(player.get("id") or "").strip()
        player_name = _player_name_from_payload(player)
        if not entity_id or not player_name:
            continue
        try:
            payload = _cached_player_boxscore_splits(
                entity_id,
                "nba",
                stat_type,
                f"{float(target_line):g}",
            )
        except Exception:
            continue
        split = _best_split_row(payload.get("splits") or [])
        if split is None:
            continue
        fair_prob = _split_over_probability(split)
        if fair_prob is None:
            continue
        split_label = str(split.get("label") or split.get("type") or "recent").strip()
        avg_value = split.get("avg")
        avg_text = ""
        try:
            avg_text = f", avg {float(avg_value):.2f}"
        except Exception:
            avg_text = ""
        candidates.append(
            {
                "player_key": normalize_text(player_name),
                "selection": player_name,
                "fair_prob": fair_prob,
                "fair_odds": int(probability_to_american(fair_prob)),
                "consensus_fair_line": "",
                "matched_books": 0,
                "books": "",
                "sportsbook_odds": "",
                "source_note": (
                    f"Real split fallback {split_label} "
                    f"({float(fair_prob) * 100.0:.1f}% over{avg_text})"
                ),
            }
        )
    return candidates


def _sport_split_stat_types(sport: str) -> dict[str, int]:
    if sport == "nba":
        return NBA_SPLIT_STAT_TYPES
    if sport == "wnba":
        return WNBA_SPLIT_STAT_TYPES
    if sport == "nhl":
        return NHL_SPLIT_STAT_TYPES
    return {}


def _sport_split_preferred_types(sport: str) -> tuple[str, ...]:
    if sport == "nhl":
        return ("playoff",)
    return ()


@lru_cache(maxsize=128)
def _cached_game_feed_players_for_split(
    sport: str,
    game_id: str,
) -> tuple[dict[str, Any], ...]:
    client = build_realsports_client()
    payload = client.get_game_feed(game_id, sport=sport)
    players = payload.get("players") or []
    return tuple(player for player in players if isinstance(player, dict))


def _split_fallback_players(
    entry: dict[str, Any],
    *,
    sport: str,
) -> list[dict[str, Any]]:
    game_payload = entry.get("game_payload") or {}
    players = [player for player in (game_payload.get("players") or []) if isinstance(player, dict)]
    if players:
        return players

    seen_ids: set[str] = set()
    gathered: list[dict[str, Any]] = []
    for game in entry.get("active_day_games") or []:
        game_id = str((game or {}).get("id") or "").strip()
        if not game_id:
            continue
        try:
            game_players = _cached_game_feed_players_for_split(sport, game_id)
        except Exception:
            continue
        for player in game_players:
            entity_id = str((player or {}).get("id") or "").strip()
            if entity_id and entity_id in seen_ids:
                continue
            if entity_id:
                seen_ids.add(entity_id)
            gathered.append(player)
    return gathered


def _sport_split_fallback_candidates(
    entry: dict[str, Any],
    *,
    sport: str,
    stat_key: str,
    target_line: float,
) -> list[dict[str, Any]]:
    stat_type = _sport_split_stat_types(sport).get(stat_key)
    if stat_type is None:
        return []

    preferred_types = _sport_split_preferred_types(sport)
    candidates: list[dict[str, Any]] = []
    for player in _split_fallback_players(entry, sport=sport):
        entity_id = str(player.get("id") or "").strip()
        player_name = _player_name_from_payload(player)
        if not entity_id or not player_name:
            continue
        try:
            payload = _cached_player_boxscore_splits(
                entity_id,
                sport,
                stat_type,
                f"{float(target_line):g}",
            )
        except Exception:
            continue
        split = _best_split_row_with_type_preference(
            payload.get("splits") or [],
            preferred_types=preferred_types,
        )
        if split is None:
            continue
        fair_prob = _split_over_probability(split)
        if fair_prob is None:
            continue
        split_type = str(split.get("type") or "recent").strip()
        split_label = str(split.get("label") or split_type or "recent").strip()
        avg_value = split.get("avg")
        avg_text = ""
        try:
            avg_text = f", avg {float(avg_value):.2f}"
        except Exception:
            avg_text = ""
        candidates.append(
            {
                "player_key": normalize_text(player_name),
                "selection": player_name,
                "fair_prob": fair_prob,
                "fair_odds": int(probability_to_american(fair_prob)),
                "consensus_fair_line": "",
                "matched_books": 0,
                "books": "",
                "sportsbook_odds": "",
                "source_note": (
                    f"Real split fallback {split_type} {split_label} "
                    f"({float(fair_prob) * 100.0:.1f}% over{avg_text})"
                ).strip(),
            }
        )
    return candidates


def _entry_player_entity_id(
    entry: dict[str, Any],
    *,
    sport: str,
    player_id: str,
    player_name: str,
) -> str:
    entity_id = str(player_id or "").strip()
    if entity_id:
        return entity_id
    normalized_name = normalize_text(player_name)
    if not normalized_name:
        return ""
    for player in _split_fallback_players(entry, sport=sport):
        candidate_id = str(player.get("id") or "").strip()
        if not candidate_id:
            continue
        candidate_name = normalize_text(_player_name_from_payload(player))
        if candidate_name and candidate_name == normalized_name:
            return candidate_id
    return ""


def _player_split_over_fallback(
    entry: dict[str, Any],
    *,
    sport: str,
    player_id: str,
    player_name: str,
    stat_key: str,
    target_line: float,
) -> dict[str, Any] | None:
    stat_type = _sport_split_stat_types(sport).get(stat_key)
    if stat_type is None:
        return None
    entity_id = _entry_player_entity_id(
        entry,
        sport=sport,
        player_id=player_id,
        player_name=player_name,
    )
    if not entity_id:
        return None
    try:
        payload = _cached_player_boxscore_splits(
            entity_id,
            sport,
            stat_type,
            f"{float(target_line):g}",
        )
    except Exception:
        return None
    split = _best_split_row_with_type_preference(
        payload.get("splits") or [],
        preferred_types=_sport_split_preferred_types(sport),
    )
    if split is None:
        return None
    fair_prob = _split_over_probability(split)
    if fair_prob is None:
        return None
    split_type = str(split.get("type") or "recent").strip()
    split_label = str(split.get("label") or split_type or "recent").strip()
    avg_value = split.get("avg")
    avg_text = ""
    try:
        avg_text = f", avg {float(avg_value):.2f}"
    except Exception:
        avg_text = ""
    return {
        "fair_prob": float(fair_prob),
        "fair_odds": int(probability_to_american(float(fair_prob))),
        "source_note": (
            f"Real split fallback {split_type} {split_label} "
            f"({float(fair_prob) * 100.0:.1f}% over{avg_text})"
        ).strip(),
    }


@lru_cache(maxsize=32)
def _cached_mlb_optimal_projections(day: str) -> tuple[Any, ...]:
    return tuple(load_optimal_projections(day=day))


def _mlb_projection_fallback_candidates(
    entry: dict[str, Any],
    *,
    stat_key: str,
) -> list[dict[str, Any]]:
    if stat_key != "runs":
        return []
    game = entry.get("game") or {}
    day = str(game.get("day") or "").strip()
    if not day:
        return []
    try:
        projections = list(_cached_mlb_optimal_projections(day))
    except Exception:
        return []

    candidates = projected_hitter_candidates(
        entry,
        projections,
        stat=stat_key,
    )
    results: list[dict[str, Any]] = []
    for candidate in candidates:
        fair_prob = float(candidate.get("fair_prob") or 0.0)
        selection = str(candidate.get("selection") or "").strip()
        if fair_prob <= 0 or not selection:
            continue
        results.append(
            {
                "player_key": str(candidate.get("player_key") or normalize_text(selection)),
                "selection": selection,
                "fair_prob": fair_prob,
                "fair_odds": int(probability_to_american(fair_prob)),
                "consensus_fair_line": "",
                "matched_books": 0,
                "books": "",
                "sportsbook_odds": "",
                "source_note": str(candidate.get("source_note") or "").strip(),
            }
        )
    return results


def _rank_zero_cost_candidates(
    candidates: list[dict[str, Any]],
    payout_base: int,
    *,
    daily_ladder: bool = False,
) -> list[dict[str, Any]]:
    ranked: list[dict[str, Any]] = []
    ordered = sorted(
        candidates,
        key=lambda item: (
            -float(item.get("fair_prob") or 0.0),
            str(item.get("selection") or ""),
        ),
    )
    for probability_rank, candidate in enumerate(ordered, start=1):
        if daily_ladder:
            if probability_rank <= 20:
                payout = int(payout_base) * probability_rank
            else:
                payout = (int(payout_base) * 20) + (probability_rank - 20)
        else:
            payout = int(payout_base) * min(probability_rank, 10)
        expected_value = float(candidate.get("fair_prob") or 0.0) * float(payout)
        ranked.append(
            {
                **candidate,
                "probability_rank": probability_rank,
                "ranked_payout": payout,
                "expected_value": expected_value,
            }
        )
    return ranked


def _zero_cost_player_choices_json(candidates: list[dict[str, Any]]) -> str:
    choices: list[dict[str, Any]] = []
    for candidate in sorted(
        candidates,
        key=lambda item: (
            int(item.get("probability_rank") or 9999),
            -float(item.get("fair_prob") or 0.0),
            str(item.get("selection") or ""),
        ),
    ):
        selection = str(candidate.get("selection") or "").strip()
        if not selection:
            continue
        choices.append(
            {
                "selection": selection,
                "player_key": str(candidate.get("player_key") or "").strip(),
                "probability_rank": int(candidate.get("probability_rank") or len(choices) + 1),
                "fair_prob": round(float(candidate.get("fair_prob") or 0.0), 6),
                "fair_odds": int(candidate.get("fair_odds") or probability_to_american(0.5)),
                "ranked_payout": int(candidate.get("ranked_payout") or 0),
                "expected_value": round(float(candidate.get("expected_value") or 0.0), 4),
                "sportsbook_odds": candidate.get("sportsbook_odds")
                if candidate.get("sportsbook_odds") not in ("", None)
                else "",
                "matched_books": int(candidate.get("matched_books") or 0),
                "books": str(candidate.get("books") or "").strip(),
                "source_lines": str(candidate.get("source_lines") or "").strip(),
                "source_note": str(candidate.get("source_note") or "").strip(),
            }
        )
    return json.dumps(choices, separators=(",", ":"), ensure_ascii=True) if choices else ""


def _ranked_sportsbook_fields(candidates: list[dict[str, Any]], selection: str) -> dict[str, Any]:
    chosen = next(
        (
            candidate
            for candidate in candidates
            if str(candidate.get("selection") or "").strip() == str(selection).strip()
        ),
        None,
    )
    display_candidates: list[dict[str, Any]] = []
    if chosen is not None:
        display_candidates.append(chosen)
    for candidate in candidates:
        if chosen is not None and candidate is chosen:
            continue
        display_candidates.append(candidate)
        if len(display_candidates) >= 3:
            break
    return _book_outcome_fields(
        [
            {
                "label": candidate.get("selection") or "",
                "odds": candidate.get("sportsbook_odds") if candidate.get("sportsbook_odds") not in ("", None) else "",
            }
            for candidate in display_candidates[:3]
        ]
    )


def _nba_team_stat_context(
    content_text: str,
    stat_ids: list[Any] | None = None,
) -> tuple[str, str, float] | None:
    if isinstance(stat_ids, list):
        for raw_value in stat_ids:
            try:
                stat_id = int(raw_value)
            except Exception:
                continue
            context = TEAM_STAT_ID_METRICS.get(stat_id)
            if context is not None:
                return context
    lowered = " ".join(str(content_text or "").strip().lower().split())
    for phrase, context in TEAM_STAT_METRICS.items():
        if phrase in lowered:
            return context
    return None


def _nba_team_stat_market_fallback(
    entry: dict[str, Any],
    markets: list[MarketRow],
    *,
    content_text: str,
    stat_ids: list[Any] | None = None,
) -> dict[str, Any] | None:
    context = _nba_team_stat_context(content_text, stat_ids)
    if context is None:
        return None

    metric_key, direction, _scale = context
    game = entry.get("game") or {}
    game_quotes = _matching_game_quotes(
        markets,
        sport="nba",
        game=game,
        market_family="game_winner",
    )
    if not game_quotes:
        return None

    home_probabilities: list[float] = []
    representative_market = _representative_game_market(game_quotes)
    representative_outcomes: dict[str, dict[str, Any]] = {}
    if representative_market is not None:
        for outcome in _market_extra_outcomes(representative_market):
            key = _canonical_book_outcome_key(outcome, representative_market)
            odds = _normalize_option_odds({"odds": outcome.get("odds")})
            if key and odds is not None:
                representative_outcomes[key] = {**outcome, "odds": odds}

    for market in game_quotes:
        keyed_outcomes: dict[str, int] = {}
        for outcome in _market_extra_outcomes(market):
            key = _canonical_book_outcome_key(outcome, market)
            odds = _normalize_option_odds({"odds": outcome.get("odds")})
            if key in {"home", "away"} and odds is not None:
                keyed_outcomes[key] = odds
        home_odds = keyed_outcomes.get("home")
        away_odds = keyed_outcomes.get("away")
        if home_odds is None or away_odds is None:
            continue
        implied_total = american_to_implied_prob(home_odds) + american_to_implied_prob(away_odds)
        if implied_total <= 0:
            continue
        home_probabilities.append(american_to_implied_prob(home_odds) / implied_total)
    if not home_probabilities:
        return None

    home_win_prob = sum(home_probabilities) / len(home_probabilities)
    away_win_prob = 1.0 - home_win_prob
    favorite_key = "home" if home_win_prob >= away_win_prob else "away"
    underdog_key = "away" if favorite_key == "home" else "home"
    if metric_key == "TOV" and direction == "higher":
        selection_key = underdog_key
    else:
        selection_key = favorite_key
    favorite_prob = max(home_win_prob, away_win_prob)
    fair_prob = min(0.5 + ((favorite_prob - 0.5) * 0.6), 0.8)
    if selection_key == favorite_key:
        selection = str(game.get("homeTeamKey") or "") if favorite_key == "home" else str(game.get("awayTeamKey") or "")
    else:
        selection = str(game.get("homeTeamKey") or "") if underdog_key == "home" else str(game.get("awayTeamKey") or "")

    representative_home = representative_outcomes.get("home") or {}
    representative_away = representative_outcomes.get("away") or {}
    proxy_name = "moneyline"
    if metric_key == "TOV" and direction == "higher":
        proxy_reason = "underdog tendency proxy"
    else:
        proxy_reason = "favorite strength proxy"
    return {
        "selection": selection,
        "fair_prob": fair_prob,
        "fair_odds": probability_to_american(fair_prob),
        "matched_books": len(home_probabilities),
        "books": " | ".join(sorted({market.book for market in game_quotes})),
        "sportsbook_a_label": str(game.get("homeTeamKey") or ""),
        "sportsbook_a_odds": representative_home.get("odds", ""),
        "sportsbook_b_label": str(game.get("awayTeamKey") or ""),
        "sportsbook_b_odds": representative_away.get("odds", ""),
        "notes": (
            f"NBA sportsbook {proxy_name} proxy for {metric_key}: "
            f"{selection} selected from {proxy_reason} "
            f"({favorite_prob * 100.0:.1f}% win consensus)"
        ),
    }


def _zero_cost_pick_base(
    entry: dict[str, Any],
    sport: str,
    *,
    poll_kind: str,
    stat_key: str,
    line_value: float | None,
) -> dict[str, Any]:
    game = entry["game"]
    post = entry["post"]
    poll = entry["poll"]
    additional = poll.get("additionalInfo") or {}
    content_text = _first_text(((post.get("content") or {}).get("nodes")) or [])
    if str(additional.get("type") or "").strip().lower() == "daily":
        content_text = str(additional.get("detail") or content_text).strip()
    return {
        "poll_id": poll.get("id"),
        "post_id": post.get("id"),
        "sport": sport,
        "game_id": game.get("id"),
        "game_time": game.get("dateTime") or "",
        "home_team": game.get("homeTeamKey") or "",
        "away_team": game.get("awayTeamKey") or "",
        "header": post.get("header") or "",
        "content_text": content_text,
        "poll_kind": poll_kind,
        "player_name": "",
        "stat": stat_key,
        "line": line_value if line_value is not None else "",
        "can_wager": poll.get("canWager"),
        "max_wager": poll.get("maxWager", ""),
        "option_a_label": "",
        "option_a_odds": "",
        "option_b_label": "",
        "option_b_odds": "",
        "option_c_label": "",
        "option_c_odds": "",
        "sportsbook_a_label": "",
        "sportsbook_a_odds": "",
        "sportsbook_b_label": "",
        "sportsbook_b_odds": "",
        "sportsbook_c_label": "",
        "sportsbook_c_odds": "",
        "status": "no_market",
        "recommended_option": "",
        "recommended_amount": 0,
        "stake_fraction_of_max": 0.0,
        "recommended_ev_percent": "",
        "fair_prob": "",
        "fair_odds": "",
        "consensus_fair_line": "",
        "matched_books": 0,
        "books": "",
        "player_choices_json": "",
        "source_lines": "",
        "notes": "",
    }


def _recommend_zero_cost_player_poll(
    entry: dict[str, Any],
    sport: str,
    *,
    markets: list[MarketRow],
    poll_kind: str,
) -> dict[str, Any]:
    poll = entry["poll"]
    additional = poll.get("additionalInfo") or {}
    stat_key = _resolve_zero_cost_stat_key(entry, poll_kind)
    target_line = 0.5 if poll_kind == "anytime_play" else None
    if poll_kind == "player_most_stat":
        target_line = _threshold_to_market_line(additional.get("threshold"))
    base = _zero_cost_pick_base(
        entry,
        sport,
        poll_kind=poll_kind,
        stat_key=stat_key,
        line_value=target_line,
    )
    if poll_kind != "first_basket" and not stat_key:
        return {
            **base,
            "status": "missing_poll_data",
            "notes": "Could not determine the poll stat from the Real payload.",
        }
    if poll_kind == "player_most_stat" and target_line is None:
        return {
            **base,
            "status": "missing_poll_data",
            "notes": "Missing Real stat threshold for zero-cost player poll.",
        }

    allowed_players = _allowed_game_players(entry.get("game_payload") or {})
    candidates: list[dict[str, Any]] = []
    split_fallback_attempted = False
    is_daily_poll = str(additional.get("type") or "").strip().lower() == "daily"
    if poll_kind == "first_basket":
        grouped_rows, display_names = _group_same_game_player_markets(
            markets,
            sport=sport,
            game=entry["game"],
            market_family="first_basket",
            allowed_players=allowed_players or None,
        )
        by_book: dict[str, list[MarketRow]] = {}
        for market in markets:
            if market.sport != sport or market.market_family != "first_basket":
                continue
            if not _same_game(entry["game"], market) or not market.player_name or market.over_odds is None:
                continue
            by_book.setdefault(market.book, []).append(market)
        probability_map: dict[str, list[float]] = {}
        price_map: dict[str, list[int]] = {}
        books_map: dict[str, set[str]] = {}
        for book, book_rows in by_book.items():
            total_implied = sum(
                american_to_implied_prob(market.over_odds)
                for market in book_rows
                if market.over_odds is not None
            )
            if total_implied <= 0:
                continue
            for market in book_rows:
                if market.player_name not in grouped_rows or market.over_odds is None:
                    continue
                probability_map.setdefault(market.player_name, []).append(
                    american_to_implied_prob(market.over_odds) / total_implied
                )
                price_map.setdefault(market.player_name, []).append(market.over_odds)
                books_map.setdefault(market.player_name, set()).add(book)
        for player_key, probabilities in probability_map.items():
            if not probabilities:
                continue
            fair_prob = sum(probabilities) / len(probabilities)
            price_candidates = price_map.get(player_key) or []
            sportsbook_odds = ""
            if price_candidates:
                sportsbook_odds = min(
                    price_candidates,
                    key=lambda price: abs(american_to_implied_prob(price) - fair_prob),
                )
            candidates.append(
                {
                    "player_key": player_key,
                    "selection": display_names.get(player_key, player_key),
                    "fair_prob": fair_prob,
                    "fair_odds": int(probability_to_american(fair_prob)),
                    "consensus_fair_line": "",
                    "matched_books": len(probabilities),
                    "books": " | ".join(sorted(books_map.get(player_key) or [])),
                    "sportsbook_odds": sportsbook_odds,
                    "source_note": "normalized first-basket implied probabilities",
                }
            )
    else:
        if is_daily_poll:
            grouped_rows, display_names = _group_active_day_player_markets(
                markets,
                sport=sport,
                active_games=entry.get("active_day_games") or [],
                market_family="player_over_under",
                stat_key=stat_key,
            )
        else:
            grouped_rows, display_names = _group_same_game_player_markets(
                markets,
                sport=sport,
                game=entry["game"],
                market_family="player_over_under",
                stat_key=stat_key,
                allowed_players=allowed_players or None,
            )
        for player_key, player_rows in grouped_rows.items():
            candidate = _player_market_candidate(
                player_key,
                display_names.get(player_key, player_key),
                player_rows,
                target_line=float(target_line),
            )
            if candidate is not None:
                candidates.append(candidate)
        if not candidates and sport in {"nba", "wnba", "nhl"} and poll_kind in {"player_most_stat", "anytime_play"}:
            split_fallback_attempted = True
            candidates = _sport_split_fallback_candidates(
                entry,
                sport=sport,
                stat_key=stat_key,
                target_line=float(target_line),
            )
        if not candidates and sport == "mlb" and poll_kind == "anytime_play":
            candidates = _mlb_projection_fallback_candidates(
                entry,
                stat_key=stat_key,
            )

        if (
            not candidates
            and sport == "soccer"
            and stat_key == "shots"
            and poll_kind in {"anytime_play", "pick_a_player", "player_most_stat"}
        ):
            fallback_stat_key = "goals"
            if is_daily_poll:
                grouped_rows, display_names = _group_active_day_player_markets(
                    markets,
                    sport=sport,
                    active_games=entry.get("active_day_games") or [],
                    market_family="player_over_under",
                    stat_key=fallback_stat_key,
                )
            else:
                grouped_rows, display_names = _group_same_game_player_markets(
                    markets,
                    sport=sport,
                    game=entry["game"],
                    market_family="player_over_under",
                    stat_key=fallback_stat_key,
                    allowed_players=allowed_players or None,
                )
            for player_key, player_rows in grouped_rows.items():
                candidate = _player_market_candidate(
                    player_key,
                    display_names.get(player_key, player_key),
                    player_rows,
                    target_line=float(target_line),
                )
                if candidate is not None:
                    existing_note = str(candidate.get("source_note") or "").strip()
                    fallback_note = "fallback via goal-scorer (goals) props for shot-on-goal poll"
                    candidate["source_note"] = (
                        f"{existing_note}; {fallback_note}" if existing_note else fallback_note
                    )
                    candidates.append(candidate)

    if not candidates:
        if poll_kind == "first_basket":
            return {
                **base,
                "status": "no_market",
                "notes": "No same-game sportsbook first-basket props were available for this matchup.",
            }
        same_game_stat_rows = [
            market
            for market in markets
            if market.sport == sport
            and market.market_family == "player_over_under"
            and market.stat_key == stat_key
            and _same_game(entry["game"], market)
        ]
        same_game_goal_rows = [
            market
            for market in markets
            if market.sport == sport
            and market.market_family == "player_over_under"
            and market.stat_key == "goals"
            and _same_game(entry["game"], market)
        ]
        if sport == "soccer" and stat_key == "shots":
            if not same_game_stat_rows and not same_game_goal_rows:
                note = (
                    "No same-game sportsbook shot-on-goal props were available, "
                    "and no goal-scorer fallback props were available either."
                )
            elif not same_game_stat_rows and same_game_goal_rows:
                note = (
                    "No same-game sportsbook shot-on-goal props were available; "
                    "goal-scorer fallback props existed but did not produce a match."
                )
            else:
                note = (
                    "Same-game shot-on-goal props existed but no candidate matched, "
                    "and goal-scorer fallback did not produce a match."
                )
        elif not same_game_stat_rows:
            note = f"No same-game sportsbook {stat_key} props were available for this matchup."
        else:
            note = "No same-game sportsbook or split candidates matched this zero-cost poll."
        if split_fallback_attempted and sport == "wnba":
            note = (
                f"{note} Real split fallback returned no usable player split rows for this threshold."
            )
        return {
            **base,
            "status": "no_market",
            "notes": note,
        }

    payout_base = 10
    try:
        payout_base = max(1, int(float(additional.get("karmaIncrement") or 10)))
    except Exception:
        payout_base = 10
    is_daily_poll = str(additional.get("type") or "").strip().lower() == "daily"
    ranked_candidates = _rank_zero_cost_candidates(
        candidates,
        payout_base,
        daily_ladder=is_daily_poll,
    )
    selected = max(
        ranked_candidates,
        key=lambda candidate: (
            float(candidate.get("expected_value") or 0.0),
            float(candidate.get("fair_prob") or 0.0),
            -int(candidate.get("probability_rank") or 9999),
            str(candidate.get("selection") or ""),
        ),
    )
    note_parts = [
        (
            f"weighted daily-stats payout ladder from karmaIncrement={payout_base}"
            if is_daily_poll
            else f"weighted zero-cost payout ladder from karmaIncrement={payout_base}"
        ),
        f"rank {int(selected['probability_rank'])} payout {int(selected['ranked_payout'])}",
        f"fair win {float(selected['fair_prob']) * 100.0:.1f}%",
        f"EV {float(selected['expected_value']):.2f}",
    ]
    source_note = str(selected.get("source_note") or "").strip()
    if source_note:
        note_parts.append(source_note)
    return {
        **base,
        **_ranked_sportsbook_fields(
            sorted(
                ranked_candidates,
                key=lambda candidate: (
                    -float(candidate.get("expected_value") or 0.0),
                    -float(candidate.get("fair_prob") or 0.0),
                    str(candidate.get("selection") or ""),
                ),
            ),
            str(selected.get("selection") or ""),
        ),
        "status": "pick",
        "player_name": str(selected.get("selection") or ""),
        "recommended_option": str(selected.get("selection") or ""),
        "recommended_amount": 0,
        "fair_prob": round(float(selected.get("fair_prob") or 0.0), 6),
        "fair_odds": int(selected.get("fair_odds") or probability_to_american(0.5)),
        "consensus_fair_line": selected.get("consensus_fair_line") or "",
        "matched_books": int(selected.get("matched_books") or 0),
        "books": str(selected.get("books") or ""),
        "player_choices_json": _zero_cost_player_choices_json(ranked_candidates),
        "source_lines": str(selected.get("source_lines") or ""),
        "notes": "; ".join(note_parts),
    }


def _golf_leaderboard_context(entry: dict[str, Any]) -> tuple[str, float, str, str] | None:
    poll = entry.get("poll") or {}
    post = entry.get("post") or {}
    additional = poll.get("additionalInfo") or {}
    content_text = _first_text(((post.get("content") or {}).get("nodes")) or [])
    text = f"{content_text} {additional.get('karmaDisplay') or ''}".lower()
    round_value = str(additional.get("round") or "").strip()
    period = f"R{round_value}" if round_value.isdigit() else ""

    min_rank: int | None = None
    try:
        min_rank = int(float(additional.get("minRank")))
    except Exception:
        min_rank = None
    top_match = re.search(r"\btop\s+([0-9]+)\b", text)
    if top_match:
        min_rank = int(top_match.group(1))

    if period == "R1" and ("leader" in text or min_rank == 1):
        return "leader", 1.0, period, "round 1 leader"
    if min_rank is not None and min_rank > 1:
        label = f"round {round_value} top {min_rank}" if period else f"top {min_rank}"
        return "topfinish", float(min_rank), period, label
    if min_rank == 1 or "win the tournament" in text or "tournament winner" in text:
        return "winner", 1.0, "", "tournament winner"
    return None


def _golf_leaderboard_candidates(
    markets: list[MarketRow],
    *,
    stat_key: str,
    line_value: float,
    period: str,
    allowed_players: set[str],
) -> list[dict[str, Any]]:
    market_groups: dict[tuple[str, str], list[MarketRow]] = {}
    for market in markets:
        if market.sport != "golf" or market.market_family != "player_finish":
            continue
        if market.stat_key != stat_key:
            continue
        if market.line is None or abs(float(market.line) - float(line_value)) > 1e-9:
            continue
        if period and str(market.period or "").strip().upper() != period:
            continue
        if not period and str(market.period or "").strip():
            continue
        if not market.player_name or market.over_odds is None:
            continue
        if allowed_players and market.player_name not in allowed_players:
            continue
        market_id = str(market.raw.get("provider_market_id") or market.raw.get("provider_market_name") or "").split(":", 1)[0]
        market_groups.setdefault((market.book, market_id), []).append(market)

    probability_map: dict[str, list[float]] = {}
    price_map: dict[str, list[int]] = {}
    books_map: dict[str, set[str]] = {}
    display_names: dict[str, str] = {}
    source_map: dict[str, list[str]] = {}
    for (book, _market_id), rows in market_groups.items():
        total_implied = sum(
            american_to_implied_prob(row.over_odds)
            for row in rows
            if row.over_odds is not None
        )
        if total_implied <= 0:
            continue
        market_name = str((rows[0].raw if rows else {}).get("provider_market_name") or "").strip()
        for row in rows:
            if row.over_odds is None:
                continue
            fair_prob = american_to_implied_prob(row.over_odds) / total_implied
            probability_map.setdefault(row.player_name, []).append(fair_prob)
            price_map.setdefault(row.player_name, []).append(row.over_odds)
            books_map.setdefault(row.player_name, set()).add(book)
            display_names.setdefault(
                row.player_name,
                clean_player_name(str(row.raw.get("player_name") or "").strip()) or row.player_name,
            )
            source_map.setdefault(row.player_name, []).append(
                f"{_book_abbreviation(book)} {market_name} {_format_american(row.over_odds)} ({fair_prob * 100.0:.1f}% no-vig)"
            )

    candidates: list[dict[str, Any]] = []
    for player_key, probabilities in probability_map.items():
        if not probabilities:
            continue
        fair_prob = sum(probabilities) / len(probabilities)
        price_candidates = price_map.get(player_key) or []
        sportsbook_odds: int | str = ""
        if price_candidates:
            sportsbook_odds = min(
                price_candidates,
                key=lambda price: abs(american_to_implied_prob(price) - fair_prob),
            )
        candidates.append(
            {
                "player_key": player_key,
                "selection": display_names.get(player_key, player_key),
                "fair_prob": fair_prob,
                "fair_odds": int(probability_to_american(fair_prob)),
                "consensus_fair_line": "",
                "matched_books": len(books_map.get(player_key) or []),
                "books": " | ".join(sorted(books_map.get(player_key) or [])),
                "sportsbook_odds": sportsbook_odds,
                "source_lines": "\n".join(source_map.get(player_key, [])[:6]),
                "source_note": "normalized golf leaderboard implied probabilities",
            }
        )
    return candidates


def _recommend_golf_leaderboard_poll(
    entry: dict[str, Any],
    markets: list[MarketRow],
    sport: str,
) -> dict[str, Any]:
    context = _golf_leaderboard_context(entry)
    stat_key = context[0] if context else ""
    line_value = context[1] if context else None
    base = _zero_cost_pick_base(
        entry,
        sport,
        poll_kind="golf_leaderboard",
        stat_key=stat_key,
        line_value=line_value,
    )
    if context is None:
        return {
            **base,
            "status": "missing_poll_data",
            "notes": "Could not determine the golf leaderboard market from the Real payload.",
        }

    stat_key, line_value, period, market_label = context
    candidates = _golf_leaderboard_candidates(
        markets,
        stat_key=stat_key,
        line_value=line_value,
        period=period,
        allowed_players=_allowed_game_players(entry.get("game_payload") or {}),
    )
    if not candidates:
        return {
            **base,
            "status": "no_market",
            "notes": f"No DraftKings golf {market_label} odds matched this leaderboard poll.",
        }

    additional = (entry.get("poll") or {}).get("additionalInfo") or {}
    try:
        payout_base = max(1, int(float(additional.get("karmaIncrement") or 10)))
    except Exception:
        payout_base = 10
    ranked_candidates = _rank_zero_cost_candidates(candidates, payout_base)
    selected = max(
        ranked_candidates,
        key=lambda candidate: (
            float(candidate.get("expected_value") or 0.0),
            float(candidate.get("fair_prob") or 0.0),
            -int(candidate.get("probability_rank") or 9999),
            str(candidate.get("selection") or ""),
        ),
    )
    note_parts = [
        f"weighted golf leaderboard payout ladder from karmaIncrement={payout_base}",
        f"{market_label} market",
        f"rank {int(selected['probability_rank'])} payout {int(selected['ranked_payout'])}",
        f"fair win {float(selected['fair_prob']) * 100.0:.1f}%",
        f"EV {float(selected['expected_value']):.2f}",
        str(selected.get("source_note") or "").strip(),
    ]
    return {
        **base,
        **_ranked_sportsbook_fields(
            sorted(
                ranked_candidates,
                key=lambda candidate: (
                    -float(candidate.get("expected_value") or 0.0),
                    -float(candidate.get("fair_prob") or 0.0),
                    str(candidate.get("selection") or ""),
                ),
            ),
            str(selected.get("selection") or ""),
        ),
        "status": "pick",
        "player_name": str(selected.get("selection") or ""),
        "recommended_option": str(selected.get("selection") or ""),
        "recommended_amount": 0,
        "fair_prob": round(float(selected.get("fair_prob") or 0.0), 6),
        "fair_odds": int(selected.get("fair_odds") or probability_to_american(0.5)),
        "matched_books": int(selected.get("matched_books") or 0),
        "books": str(selected.get("books") or ""),
        "player_choices_json": _zero_cost_player_choices_json(ranked_candidates),
        "source_lines": str(selected.get("source_lines") or ""),
        "notes": "; ".join(part for part in note_parts if part),
    }


def _recommend_team_stat_poll(
    entry: dict[str, Any],
    sport: str,
    *,
    day: str,
    markets: list[MarketRow] | None = None,
) -> dict[str, Any]:
    game = entry["game"]
    post = entry["post"]
    poll = entry["poll"]
    additional = poll.get("additionalInfo") or {}
    base = {
        "poll_id": poll.get("id"),
        "post_id": post.get("id"),
        "sport": sport,
        "game_id": game.get("id"),
        "game_time": game.get("dateTime") or "",
        "home_team": game.get("homeTeamKey") or "",
        "away_team": game.get("awayTeamKey") or "",
        "header": post.get("header") or "",
        "content_text": _first_text(((post.get("content") or {}).get("nodes")) or []),
        "poll_kind": "team_stat",
        "player_name": "",
        "stat": "teamstat",
        "line": "",
        "can_wager": poll.get("canWager"),
        "max_wager": poll.get("maxWager", ""),
        "option_a_label": "",
        "option_a_odds": "",
        "option_b_label": "",
        "option_b_odds": "",
        "option_c_label": "",
        "option_c_odds": "",
        "sportsbook_a_label": "",
        "sportsbook_a_odds": "",
        "sportsbook_b_label": "",
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
        "notes": "Unsupported team-stat poll.",
    }
    fallback_recommendation = None
    if sport == "nba":
        fallback_recommendation = _nba_team_stat_market_fallback(
            entry,
            markets or [],
            content_text=base["content_text"],
            stat_ids=additional.get("stats"),
        )
    try:
        if sport == "nba":
            recommendation = recommend_nba_team_stat(
                day=day,
                home_team=str(game.get("homeTeamKey") or ""),
                away_team=str(game.get("awayTeamKey") or ""),
                content_text=base["content_text"],
                stat_ids=additional.get("stats"),
                home_team_id=game.get("homeTeamId") or poll.get("homeTeamId") or "",
                away_team_id=game.get("awayTeamId") or poll.get("awayTeamId") or "",
                season=game.get("season") or "",
                season_type=str(game.get("seasonType") or "").strip(),
            )
        elif sport == "nhl":
            recommendation = recommend_nhl_team_stat(
                day=day,
                home_team=str(game.get("homeTeamKey") or ""),
                away_team=str(game.get("awayTeamKey") or ""),
                content_text=base["content_text"],
                stat_ids=additional.get("stats"),
                home_team_id=game.get("homeTeamId") or poll.get("homeTeamId") or "",
                away_team_id=game.get("awayTeamId") or poll.get("awayTeamId") or "",
                season=game.get("season") or "",
                season_type=str(game.get("seasonType") or "").strip(),
            )
        else:
            recommendation = None
    except Exception as exc:
        league_name = sport.upper()
        if fallback_recommendation is not None:
            return {
                **base,
                "status": "pick",
                "recommended_option": str(fallback_recommendation.get("selection") or ""),
                "recommended_amount": 0,
                "fair_prob": round(float(fallback_recommendation.get("fair_prob") or 0.0), 6),
                "fair_odds": int(fallback_recommendation.get("fair_odds") or probability_to_american(0.5)),
                "matched_books": int(fallback_recommendation.get("matched_books") or 0),
                "books": str(fallback_recommendation.get("books") or ""),
                "sportsbook_a_label": str(fallback_recommendation.get("sportsbook_a_label") or ""),
                "sportsbook_a_odds": fallback_recommendation.get("sportsbook_a_odds") or "",
                "sportsbook_b_label": str(fallback_recommendation.get("sportsbook_b_label") or ""),
                "sportsbook_b_odds": fallback_recommendation.get("sportsbook_b_odds") or "",
                "notes": str(fallback_recommendation.get("notes") or ""),
            }
        return {
            **base,
            "status": "missing_poll_data",
            "notes": f"{league_name} team-stat proxy lookup failed: {exc}",
        }
    if sport not in {"nba", "nhl"}:
        return {
            **base,
            "notes": "Team-stat proxy is only implemented for NBA and NHL right now.",
        }
    if recommendation is None:
        if fallback_recommendation is not None:
            return {
                **base,
                "status": "pick",
                "recommended_option": str(fallback_recommendation.get("selection") or ""),
                "recommended_amount": 0,
                "fair_prob": round(float(fallback_recommendation.get("fair_prob") or 0.0), 6),
                "fair_odds": int(fallback_recommendation.get("fair_odds") or probability_to_american(0.5)),
                "matched_books": int(fallback_recommendation.get("matched_books") or 0),
                "books": str(fallback_recommendation.get("books") or ""),
                "sportsbook_a_label": str(fallback_recommendation.get("sportsbook_a_label") or ""),
                "sportsbook_a_odds": fallback_recommendation.get("sportsbook_a_odds") or "",
                "sportsbook_b_label": str(fallback_recommendation.get("sportsbook_b_label") or ""),
                "sportsbook_b_odds": fallback_recommendation.get("sportsbook_b_odds") or "",
                "notes": str(fallback_recommendation.get("notes") or ""),
            }
        return {
            **base,
            "notes": f"No {sport.upper()} team-stat proxy matched this poll yet.",
        }
    return {
        **base,
        "status": "pick",
        "recommended_option": str(recommendation.get("selection") or ""),
        "recommended_amount": 0,
        "fair_prob": round(float(recommendation.get("fair_prob") or 0.0), 6),
        "fair_odds": int(recommendation.get("fair_odds") or probability_to_american(0.5)),
        "matched_books": int(recommendation.get("matched_books") or 0),
        "books": str(recommendation.get("books") or ""),
        "sportsbook_a_label": str(recommendation.get("sportsbook_a_label") or ""),
        "sportsbook_a_odds": recommendation.get("sportsbook_a_odds") or "",
        "sportsbook_b_label": str(recommendation.get("sportsbook_b_label") or ""),
        "sportsbook_b_odds": recommendation.get("sportsbook_b_odds") or "",
        "source_lines": str(recommendation.get("source_lines") or ""),
        "notes": str(recommendation.get("notes") or ""),
    }


def _recommend_special_or_unpriced(
    entry: dict[str, Any],
    sport: str,
    *,
    markets: list[MarketRow] | None = None,
) -> dict[str, Any]:
    post = entry.get("post") or {}
    poll = entry.get("poll") or {}
    additional = (entry.get("poll") or {}).get("additionalInfo") or {}
    poll_kind = _poll_kind(
        additional,
        poll=poll,
        post=post,
    )
    if poll_kind in ZERO_COST_PLAYER_POLL_KINDS:
        return _recommend_zero_cost_player_poll(
            entry,
            sport,
            markets=markets or [],
            poll_kind=poll_kind,
        )

    game = entry["game"]
    content_text = _first_text(((post.get("content") or {}).get("nodes")) or [])
    note = "Unsupported game-feed card type."
    if poll_kind == "anytime_play":
        note = "Anytime-play cards need a separate picker against anytime prop markets."
    return {
        "poll_id": poll.get("id"),
        "post_id": post.get("id"),
        "sport": sport,
        "game_id": game.get("id"),
        "game_time": game.get("dateTime") or "",
        "home_team": game.get("homeTeamKey") or "",
        "away_team": game.get("awayTeamKey") or "",
        "header": post.get("header") or "",
        "content_text": content_text,
        "poll_kind": poll_kind,
        "player_name": "",
        "stat": normalize_stat(additional.get("stat") or ""),
        "line": additional.get("overUnderAmount") or "",
        "can_wager": poll.get("canWager"),
        "max_wager": poll.get("maxWager", ""),
        "option_a_label": "",
        "option_a_odds": "",
        "option_b_label": "",
        "option_b_odds": "",
        "sportsbook_a_label": "",
        "sportsbook_a_odds": "",
        "sportsbook_b_label": "",
        "sportsbook_b_odds": "",
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


def _recommend_lineup_contest(
    entry: dict[str, Any],
    sport: str,
    *,
    contest_recommendation: dict[str, Any] | None = None,
    projection_summary: dict[str, Any] | None = None,
    contest_error: str = "",
) -> dict[str, Any]:
    game = entry["game"]
    post = entry["post"]
    poll = entry["poll"]
    additional = poll.get("additionalInfo") or {}
    content_text = _first_text(((post.get("content") or {}).get("nodes")) or [])
    lineup_size = (additional.get("lineupSize") or 5)
    base = {
        "poll_id": poll.get("id") or additional.get("contestId") or "",
        "post_id": post.get("id"),
        "sport": sport,
        "game_id": game.get("id"),
        "game_time": game.get("dateTime") or "",
        "home_team": game.get("homeTeamKey") or "",
        "away_team": game.get("awayTeamKey") or "",
        "header": post.get("header") or "Lineup",
        "content_text": content_text,
        "poll_kind": "contest",
        "player_name": "",
        "stat": "fantasy_points",
        "line": lineup_size,
        "can_wager": False,
        "max_wager": 0,
        "option_a_label": "",
        "option_a_odds": "",
        "option_b_label": "",
        "option_b_odds": "",
        "sportsbook_a_label": "",
        "sportsbook_a_odds": "",
        "sportsbook_b_label": "",
        "sportsbook_b_odds": "",
        "status": "missing_poll_data" if contest_error else "no_market",
        "recommended_option": "",
        "recommended_amount": 0,
        "stake_fraction_of_max": 0.0,
        "recommended_ev_percent": "",
        "fair_prob": "",
        "fair_odds": "",
        "consensus_fair_line": "",
        "matched_books": 0,
        "books": "",
        "notes": contest_error or "No lineup contest projection data matched this game.",
        "lineup_rank": "",
        "lineup_players": "",
        "lineup_cutoff_gap": "",
        "lineup_min_rank_gap": "",
        "lineup_avg_rank_gap": "",
        "lineup_top5_total": "",
        "lineup_projection_site": (projection_summary or {}).get("site") or "",
        "lineup_candidate_count": "",
    }
    if not contest_recommendation:
        return base
    return {
        **base,
        "status": contest_recommendation.get("status") or base["status"],
        "recommended_option": contest_recommendation.get("recommended_option") or "",
        "notes": contest_recommendation.get("notes") or base["notes"],
        "lineup_rank": contest_recommendation.get("lineup_rank") or "",
        "lineup_players": contest_recommendation.get("lineup_players") or "",
        "lineup_cutoff_gap": contest_recommendation.get("lineup_cutoff_gap") or "",
        "lineup_min_rank_gap": contest_recommendation.get("lineup_min_rank_gap") or "",
        "lineup_avg_rank_gap": contest_recommendation.get("lineup_avg_rank_gap") or "",
        "lineup_top5_total": contest_recommendation.get("lineup_top5_total") or "",
        "lineup_projection_site": contest_recommendation.get("lineup_projection_site")
        or base["lineup_projection_site"],
        "lineup_candidate_count": contest_recommendation.get("lineup_candidate_count") or "",
    }


def _recommend_both_teams_score(
    entry: dict[str, Any],
    markets: list[MarketRow],
    sport: str,
) -> dict[str, Any]:
    return _recommend_multi_outcome(
        entry,
        markets,
        sport,
        market_family="both_teams_score",
        poll_kind="both_teams_score",
        stat="bothteamsscore",
        notes_prefix="both-teams-to-score",
    )


def _recommend_double_chance(
    entry: dict[str, Any],
    markets: list[MarketRow],
    sport: str,
) -> dict[str, Any]:
    return _recommend_multi_outcome(
        entry,
        markets,
        sport,
        market_family="double_chance",
        poll_kind="double_chance",
        stat="doublechance",
        notes_prefix="double-chance",
    )


def _recommend_halftime_result(
    entry: dict[str, Any],
    markets: list[MarketRow],
    sport: str,
) -> dict[str, Any]:
    return _recommend_multi_outcome(
        entry,
        markets,
        sport,
        market_family="halftime_result",
        poll_kind="halftime_result",
        stat="winner",
        period="1H",
        notes_prefix="first-half 3-way moneyline",
    )


def build_recommendations(
    sport: str,
    markets_csv: str | Path,
    *,
    requested_day: str = "",
    include_nonwagerable: bool = False,
) -> tuple[str, list[dict[str, Any]]]:
    markets = [build_market_row(row) for row in load_csv_rows(markets_csv)]
    client = build_realsports_client()
    resolved_day, entries = _fetch_active_day_game_entries(
        sport,
        requested_day=requested_day,
        include_nonwagerable=include_nonwagerable,
        client=client,
    )
    if requested_day and not entries:
        entries = _fetch_requested_day_game_entries(
            client,
            sport,
            day=resolved_day,
            include_nonwagerable=include_nonwagerable,
        )
    active_games = [entry.get("game") or {} for entry in entries]
    entries.extend(
        _fetch_daily_poll_entries(
            client,
            sport,
            day=resolved_day,
            active_games=active_games,
        )
    )
    daily_pool_posts = _fetch_daily_pool_posts(
        client,
        sport,
        day=resolved_day,
    )
    contest_recommendations: dict[str, dict[str, Any]] = {}
    contest_projection_summary: dict[str, Any] | None = None
    contest_error = ""
    contest_entries = [
        entry
        for entry in entries
        if _poll_kind(
            (entry.get("poll") or {}).get("additionalInfo") or {},
            poll=entry.get("poll") or {},
            post=entry.get("post") or {},
        )
        == "contest"
    ]
    if contest_entries:
        try:
            contest_recommendations, contest_projection_summary = build_lineup_contest_rankings(
                contest_entries,
                sport=sport,
                day=resolved_day,
            )
        except Exception as exc:
            contest_error = f"Rotowire lineup projection load failed: {exc}"
    recommendations: list[dict[str, Any]] = []
    for entry in entries:
        poll = entry["poll"]
        additional = poll.get("additionalInfo") or {}
        poll_kind = _poll_kind(
            additional,
            poll=poll,
            post=entry.get("post") or {},
        )
        if poll_kind == "player_over_under":
            recommendation = _recommend_player_over_under(entry, markets, sport)
        elif poll_kind == "game_total":
            recommendation = _recommend_game_total(entry, markets, sport)
        elif poll_kind == "game_spread":
            recommendation = _recommend_game_spread(entry, markets, sport)
        elif poll_kind == "game_winner":
            recommendation = _recommend_game_winner(entry, markets, sport)
        elif poll_kind == "teamnextpoints":
            recommendation = _recommend_team_next_points(entry, markets, sport)
        elif poll_kind == "period_winner":
            recommendation = _recommend_period_winner(entry, markets, sport)
        elif poll_kind == "period_total_yes_no":
            recommendation = _recommend_period_total_yes_no(entry, markets, sport)
        elif poll_kind == "both_teams_score":
            recommendation = _recommend_both_teams_score(entry, markets, sport)
        elif poll_kind == "double_chance":
            recommendation = _recommend_double_chance(entry, markets, sport)
        elif poll_kind == "halftime_result":
            recommendation = _recommend_halftime_result(entry, markets, sport)
        elif poll_kind == "golf_leaderboard":
            recommendation = _recommend_golf_leaderboard_poll(entry, markets, sport)
        elif poll_kind == "team_stat":
            recommendation = _recommend_team_stat_poll(
                entry,
                sport,
                day=resolved_day,
                markets=markets,
            )
        elif poll_kind == "contest":
            recommendation = _recommend_lineup_contest(
                entry,
                sport,
                contest_recommendation=contest_recommendations.get(str((entry.get("post") or {}).get("id") or "").strip()),
                projection_summary=contest_projection_summary,
                contest_error=contest_error,
            )
        else:
            recommendation = _recommend_special_or_unpriced(
                entry,
                sport,
                markets=markets,
            )
        recommendation["day"] = resolved_day
        recommendation["game_order"] = entry.get("game_order", "")
        recommendation["post_order"] = entry.get("post_order", "")
        recommendation["game_label"] = _entry_game_label(entry)
        recommendations.append(recommendation)
    recommendations.extend(
        _build_daily_pool_slate_rows(
            recommendations,
            sport=sport,
            day=resolved_day,
            pool_posts=daily_pool_posts,
        )
    )
    recommendations.extend(
        _build_daily_pool_underdog_rows(
            recommendations,
            sport=sport,
            day=resolved_day,
            pool_posts=daily_pool_posts,
            markets=markets,
        )
    )
    recommendations.sort(
        key=lambda row: (
            int(row.get("game_order") if row.get("game_order") not in ("", None) else 999999),
            int(row.get("post_order") if row.get("post_order") not in ("", None) else 999999),
            str(row.get("game_time") or ""),
            str(row.get("header") or ""),
        )
    )
    return resolved_day, recommendations


def write_csv(path: str | Path, rows: list[dict[str, Any]]) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "day",
        "sport",
        "game_id",
        "game_time",
        "home_team",
        "away_team",
        "game_label",
        "game_order",
        "post_order",
        "post_id",
        "poll_id",
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
        "option_b_label",
        "option_b_odds",
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
        "player_choices_json",
        "source_lines",
        "lineup_rank",
        "lineup_players",
        "lineup_cutoff_gap",
        "lineup_min_rank_gap",
        "lineup_avg_rank_gap",
        "lineup_top5_total",
        "lineup_projection_site",
        "lineup_candidate_count",
        "notes",
    ]
    with output_path.open("w", newline="", encoding="utf8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    args = parse_args()
    resolved_day, rows = build_recommendations(
        args.sport,
        args.markets_csv,
        requested_day=args.day,
        include_nonwagerable=args.include_nonwagerable,
    )
    write_csv(args.output, rows)
    bet_count = sum(1 for row in rows if row.get("status") == "bet")
    print(f"Saved {len(rows)} poll recommendations for {args.sport} {resolved_day} to {args.output}")
    print(f"Bet recommendations: {bet_count}")


if __name__ == "__main__":
    main()
