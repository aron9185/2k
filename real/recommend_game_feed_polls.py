from __future__ import annotations

import argparse
import csv
import json
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
    load_csv_rows,
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
NHL_SPLIT_STAT_TYPES = {
    "points": 1,
    "goals": 2,
    "assists": 3,
    "powerplaypoints": 8,
    "shots": 11,
    "hits": 60,
}
ZERO_PUT_WIN_WAGER = 10.0
ZERO_COST_PLAYER_POLL_KINDS = {"anytime_play", "player_most_stat", "first_basket"}


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


def _poll_kind(additional: dict[str, Any]) -> str:
    poll_type = str(additional.get("type") or "").strip().lower()
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
    if poll_type == "teamstat":
        return "team_stat"
    if poll_type == "totaloverunder":
        return "game_total"
    if poll_type == "gamewinner":
        return "game_winner"
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
    return team_pair(
        game.get("homeTeamKey", ""),
        game.get("awayTeamKey", ""),
    ) == team_pair(market.home_team, market.away_team)


def _matching_player_quotes(
    markets: list[MarketRow],
    *,
    sport: str,
    game: dict[str, Any],
    player_name: str,
    stat_key: str,
) -> list[MarketQuote]:
    normalized_player = normalize_text(player_name)
    quotes: list[MarketQuote] = []
    for market in markets:
        if market.sport != sport or market.market_family != "player_over_under":
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
        if period and str(market.period or "").strip() != period:
            continue
        if _same_game(game, market):
            matches.append(market)
    return matches


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
    for game_order, game in enumerate(games):
        game_payload = client.get_game_feed(game.get("id"), sport=sport)
        players_by_id = _player_lookup(game_payload.get("players") or [])
        for post_order, post in enumerate(game_payload.get("posts") or []):
            poll_id = _extract_poll_id(post)
            if poll_id:
                poll_payload = client.get_poll(poll_id)
                poll = poll_payload.get("poll") or {}
                poll_kind = _poll_kind(poll.get("additionalInfo") or {})
                allow_unpriced_poll = poll_kind in ZERO_COST_PLAYER_POLL_KINDS or poll_kind == "team_stat"
                if not include_nonwagerable and not poll.get("canWager", False) and not allow_unpriced_poll:
                    continue
            elif is_lineup_contest_post(post):
                poll = _contest_payload_from_post(post)
            else:
                continue
            entries.append(
                {
                    "game": game_payload.get("game") or game,
                    "game_payload": game_payload,
                    "post": post,
                    "poll": poll,
                    "player_lookup": players_by_id,
                    "game_order": game_order,
                    "post_order": post_order,
                }
            )
    return resolved_day, entries


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

    quotes = _matching_player_quotes(
        markets,
        sport=sport,
        game=game,
        player_name=player_name,
        stat_key=normalize_stat(additional.get("stat") or ""),
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
            "notes": "No matching sportsbook player prop quotes found.",
        }

    snapshot = consensus_snapshot(
        quotes,
        target_line=line_value,
        over_odds=over_odds,
        under_odds=under_odds,
    )
    representative = _representative_market_quote(quotes, line_value)
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
        "notes": snapshot["estimate"].source,
        "sportsbook_a_odds": representative.over_odds if representative else "",
        "sportsbook_b_odds": representative.under_odds if representative else "",
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

    snapshot = consensus_snapshot(
        quotes,
        target_line=line_value,
        over_odds=over_odds,
        under_odds=under_odds,
    )
    representative = _representative_market_quote(quotes, line_value)
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
            "notes": "Could not evaluate total action choices.",
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
        "notes": snapshot["estimate"].source,
        "sportsbook_a_odds": representative.over_odds if representative else "",
        "sportsbook_b_odds": representative.under_odds if representative else "",
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
            "books": " | ".join(sorted({market.book for market in game_quotes})),
            "notes": "Could not evaluate winner action choices.",
            "sportsbook_a_odds": representative_market.over_odds if representative_market else "",
            "sportsbook_b_odds": representative_market.under_odds if representative_market else "",
        }
    recommended_eval = selected_action["evaluation"]
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
        "books": " | ".join(sorted({market.book for market in game_quotes})),
        "notes": success_note,
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


def _period_market_code(sport: str, period_value: str) -> str:
    normalized_period = str(period_value or "").strip()
    if sport == "nba" and normalized_period in {"1", "2", "3", "4"}:
        return f"{normalized_period}Q"
    if sport == "nhl" and normalized_period in {"1", "2", "3"}:
        return f"{normalized_period}P"
    if sport == "nhl" and normalized_period == "4":
        return "OT"
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
    try:
        point_spread = abs(float(additional.get("pointSpread")))
    except Exception:
        point_spread = None
    spread_team_id = str(additional.get("spreadTeamId") or "").strip()
    home_team_id = str(poll.get("homeTeamId") or "").strip()
    away_team_id = str(poll.get("awayTeamId") or "").strip()

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
    if (
        home_odds is None
        or away_odds is None
        or point_spread is None
        or spread_team_id not in {home_team_id, away_team_id}
    ):
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
            "notes": "Missing live spread line, spread team, or option odds.",
        }

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
        "notes": f"{snapshot['estimate'].source}; live spread consensus",
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
    points = additional.get("points")
    try:
        target_points = float(points)
    except Exception:
        target_points = None
    line_value = (target_points - 0.5) if target_points is not None else None
    market_period = "1I" if period == "1" else ""

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
            "notes": "Missing first-inning line, period, or option odds.",
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
            "notes": "No matching first-inning runs quotes found.",
        }

    snapshot = consensus_snapshot(
        quotes,
        target_line=line_value,
        over_odds=over_odds,
        under_odds=under_odds,
    )
    representative = _representative_market_quote(quotes, line_value)
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
            "notes": "Could not evaluate first-period total action choices.",
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
        "notes": f"{snapshot['estimate'].source}; first-inning runs",
        "sportsbook_a_odds": representative.over_odds if representative else "",
        "sportsbook_b_odds": representative.under_odds if representative else "",
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
    if poll_kind == "first_basket":
        return "firstbasket"
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
        player_name = _player_name_from_payload(player)
        if player_name:
            players.add(normalize_text(player_name))
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
            str(market.raw.get("player_name") or "").strip() or market.player_name,
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
    allowed_pairs = {
        team_pair(game.get("homeTeamKey") or "", game.get("awayTeamKey") or "")
        for game in active_games
        if game.get("homeTeamKey") and game.get("awayTeamKey")
    }
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
            str(market.raw.get("player_name") or "").strip() or market.player_name,
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
        "matched_books": len(quotes),
        "books": " | ".join(sorted({quote.book for quote in quotes})),
        "sportsbook_odds": representative.over_odds,
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
        if not candidates and sport in {"nba", "nhl"} and poll_kind in {"player_most_stat", "anytime_play"}:
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

    if not candidates:
        return {
            **base,
            "status": "no_market",
            "notes": "No same-game sportsbook or split candidates matched this zero-cost poll.",
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
        "notes": "; ".join(note_parts),
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
            )
        elif sport == "nhl":
            recommendation = recommend_nhl_team_stat(
                day=day,
                home_team=str(game.get("homeTeamKey") or ""),
                away_team=str(game.get("awayTeamKey") or ""),
                content_text=base["content_text"],
                stat_ids=additional.get("stats"),
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
        "notes": str(recommendation.get("notes") or ""),
    }


def _recommend_special_or_unpriced(
    entry: dict[str, Any],
    sport: str,
    *,
    markets: list[MarketRow] | None = None,
) -> dict[str, Any]:
    additional = (entry.get("poll") or {}).get("additionalInfo") or {}
    poll_kind = _poll_kind(additional)
    if poll_kind in ZERO_COST_PLAYER_POLL_KINDS:
        return _recommend_zero_cost_player_poll(
            entry,
            sport,
            markets=markets or [],
            poll_kind=poll_kind,
        )

    game = entry["game"]
    post = entry["post"]
    poll = entry["poll"]
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
    active_games = [entry.get("game") or {} for entry in entries]
    entries.extend(
        _fetch_daily_poll_entries(
            client,
            sport,
            day=resolved_day,
            active_games=active_games,
        )
    )
    contest_recommendations: dict[str, dict[str, Any]] = {}
    contest_projection_summary: dict[str, Any] | None = None
    contest_error = ""
    contest_entries = [
        entry
        for entry in entries
        if _poll_kind((entry.get("poll") or {}).get("additionalInfo") or {}) == "contest"
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
        poll_kind = _poll_kind(additional)
        if poll_kind == "player_over_under":
            recommendation = _recommend_player_over_under(entry, markets, sport)
        elif poll_kind == "game_total":
            recommendation = _recommend_game_total(entry, markets, sport)
        elif poll_kind == "game_winner":
            recommendation = _recommend_game_winner(entry, markets, sport)
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
