from __future__ import annotations

import json
import re
from typing import Any, Sequence

from fair_odds import probability_to_american
from market_csv import build_public_session


BASE_URL = "https://gamma-api.polymarket.com"
GENERAL_TAGS = {"1", "100639", "100350"}


def _parse_float(value: Any) -> float | None:
    if value in (None, "", "None"):
        return None
    try:
        return float(value)
    except Exception:
        return None


def _parse_json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return parsed
        except Exception:
            return []
    return []


def _normalize_sports(values: Sequence[str]) -> set[str]:
    return {str(value or "").strip().lower() for value in values if str(value or "").strip()}


def _sport_tag_ids(session, sports: set[str]) -> dict[str, list[str]]:
    response = session.get(f"{BASE_URL}/sports", timeout=30)
    response.raise_for_status()
    payload = response.json()
    tag_map: dict[str, list[str]] = {}
    for item in payload:
        sport = str(item.get("sport") or "").strip().lower()
        if sports and sport not in sports:
            continue
        tags = [tag.strip() for tag in str(item.get("tags") or "").split(",") if tag.strip()]
        specific_tags = [tag for tag in tags if tag not in GENERAL_TAGS]
        if specific_tags:
            tag_map[sport] = specific_tags
    return tag_map


def _display_yes_probability(market: dict[str, Any]) -> float | None:
    best_bid = _parse_float(market.get("bestBid"))
    best_ask = _parse_float(market.get("bestAsk"))
    spread = _parse_float(market.get("spread"))
    if best_bid is not None and best_ask is not None and spread is not None:
        if spread <= 0.10:
            return (best_bid + best_ask) / 2.0

    last_trade = _parse_float(market.get("lastTradePrice"))
    if last_trade is not None and 0.0 < last_trade < 1.0:
        return last_trade

    outcomes = _parse_json_list(market.get("outcomes"))
    prices = _parse_json_list(market.get("outcomePrices"))
    if outcomes and prices and len(outcomes) == len(prices):
        for outcome, price in zip(outcomes, prices):
            if str(outcome).strip().lower() == "yes":
                parsed = _parse_float(price)
                if parsed is not None:
                    return parsed
    return None


def _parse_numeric_over_under(question: str) -> tuple[str, str, float] | None:
    text = str(question or "").strip()
    player_match = re.match(
        r"^(?:Will\s+)?(.+?)\s+(?:have|record|score|make|get)\s+over\s+([0-9]+(?:\.[0-9]+)?)\s+(.+?)\?$",
        text,
        flags=re.IGNORECASE,
    )
    if player_match:
        return player_match.group(1).strip(), player_match.group(3).strip(), float(player_match.group(2))

    total_match = re.match(
        r"^(?:Will\s+)?(?:there be|the total(?: combined)?(?: score)? be)\s+over\s+([0-9]+(?:\.[0-9]+)?)\s+(.+?)\?$",
        text,
        flags=re.IGNORECASE,
    )
    if total_match:
        return "", total_match.group(2).strip(), float(total_match.group(1))
    return None


def _infer_market_type(question: str, player_name: str) -> str:
    if player_name:
        return "player_over_under"
    return "game_total"


def _normalize_stat_key(raw_stat: str) -> str:
    key = re.sub(r"[^a-z0-9]+", "", str(raw_stat or "").lower())
    mapping = {
        "points": "points",
        "point": "points",
        "rebounds": "rebounds",
        "rebound": "rebounds",
        "assists": "assists",
        "assist": "assists",
        "threes": "threes",
        "threepointers": "threes",
        "threepointersmade": "threes",
        "3pointers": "threes",
        "3pointersmade": "threes",
        "shotsongoal": "shots",
        "shots": "shots",
        "goals": "goals",
        "runs": "total",
        "pointscored": "total",
        "totalpoints": "total",
        "totalruns": "total",
        "totalgoals": "total",
        "strikeouts": "strikeouts",
    }
    return mapping.get(key, key)


def _parse_event_teams(event: dict[str, Any]) -> tuple[str, str]:
    title = str(event.get("title") or "")
    matchup = re.search(r"(.+?)\s+(?:vs|at)\.?\s+(.+)", title, flags=re.IGNORECASE)
    if not matchup:
        return "", ""
    away = matchup.group(1).strip()
    home = matchup.group(2).strip()
    return home, away


def _normalize_market(market: dict[str, Any], event: dict[str, Any], sport: str) -> dict[str, Any] | None:
    question = str(market.get("question") or "").strip()
    parsed = _parse_numeric_over_under(question)
    if not parsed:
        return None

    player_name, raw_stat, line_value = parsed
    yes_prob = _display_yes_probability(market)
    if yes_prob is None:
        return None

    over_odds = probability_to_american(yes_prob)
    under_odds = probability_to_american(1.0 - yes_prob)
    home_team, away_team = _parse_event_teams(event)

    return {
        "provider": "polymarket",
        "provider_event_id": event.get("id") or "",
        "provider_market_id": market.get("id") or "",
        "provider_league": sport,
        "provider_market_name": market.get("question") or "",
        "book": "polymarket",
        "sport": sport,
        "market_type": _infer_market_type(question, player_name),
        "stat": _normalize_stat_key(raw_stat),
        "player_name": player_name,
        "line": line_value,
        "home_team": home_team,
        "away_team": away_team,
        "over_odds": over_odds,
        "under_odds": under_odds,
        "updated_at": market.get("updatedAt") or event.get("updatedAt") or "",
        "period": "",
        "event_date": market.get("endDate") or event.get("endDate") or "",
        "question": question,
    }


def fetch_rows(
    sports: Sequence[str],
    *,
    page_limit: int = 3,
    page_size: int = 100,
    max_rows: int = 0,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    wanted_sports = _normalize_sports(sports)
    session = build_public_session("c2k-polymarket-ingest/1.0")
    tag_map = _sport_tag_ids(session, wanted_sports)
    rows: list[dict[str, Any]] = []
    raw_events: dict[str, list[dict[str, Any]]] = {}

    for sport, tag_ids in tag_map.items():
        sport_events: list[dict[str, Any]] = []
        for tag_id in tag_ids:
            for page_index in range(max(page_limit, 1)):
                offset = page_index * page_size
                response = session.get(
                    f"{BASE_URL}/events",
                    params={
                        "tag_id": tag_id,
                        "active": "true",
                        "closed": "false",
                        "limit": page_size,
                        "offset": offset,
                    },
                    timeout=30,
                )
                response.raise_for_status()
                events = response.json()
                if not events:
                    break
                sport_events.extend(events)
                for event in events:
                    for market in event.get("markets") or []:
                        if not market.get("active", False) or market.get("closed", False):
                            continue
                        normalized = _normalize_market(market, event, sport)
                        if normalized is None:
                            continue
                        rows.append(normalized)
                        if max_rows > 0 and len(rows) >= max_rows:
                            raw_events[sport] = sport_events
                            return rows, {"events": raw_events}
                if len(events) < page_size:
                    break
        raw_events[sport] = sport_events

    return rows, {"events": raw_events}
