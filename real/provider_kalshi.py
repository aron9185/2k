from __future__ import annotations

import re
from typing import Any, Sequence

from fair_odds import probability_to_american
from market_csv import build_public_session


BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"
DEFAULT_LIMIT = 1000

EVENT_CODE_TO_SPORT = {
    "NBA": "nba",
    "WNBA": "wnba",
    "MLB": "mlb",
    "NHL": "nhl",
    "NFL": "nfl",
    "SOCCER": "soccer",
    "TENNIS": "tennis",
}

TEAM_CODES_BY_SPORT = {
    "mlb": {
        "ARI", "ATL", "BAL", "BOS", "CHC", "CHW", "CIN", "CLE", "COL", "DET",
        "HOU", "KC", "LAA", "LAD", "MIA", "MIL", "MIN", "NYM", "NYY", "ATH",
        "PHI", "PIT", "SD", "SEA", "SF", "SFG", "STL", "TB", "TEX", "TOR", "WSH",
    },
    "nba": {
        "ATL", "BOS", "BKN", "CHA", "CHI", "CLE", "DAL", "DEN", "DET", "GSW",
        "HOU", "IND", "LAC", "LAL", "MEM", "MIA", "MIL", "MIN", "NOP", "NYK",
        "OKC", "ORL", "PHI", "PHX", "POR", "SAC", "SAS", "TOR", "UTA", "WAS",
    },
    "wnba": {
        "ATL", "CHI", "CON", "DAL", "GSV", "IND", "LAS", "LVA", "MIN", "NYL",
        "PHX", "SEA", "WAS",
    },
    "nhl": {
        "ANA", "BOS", "BUF", "CAR", "CBJ", "CGY", "CHI", "COL", "DAL", "DET",
        "EDM", "FLA", "LAK", "MIN", "MTL", "NJD", "NSH", "NYI", "NYR", "OTT",
        "PHI", "PIT", "SEA", "SJS", "STL", "TB", "TOR", "UTA", "VAN", "VGK",
        "WPG", "WSH",
    },
    "nfl": {
        "ARI", "ATL", "BAL", "BUF", "CAR", "CHI", "CIN", "CLE", "DAL", "DEN",
        "DET", "GB", "HOU", "IND", "JAX", "KC", "LAC", "LAR", "LV", "MIA",
        "MIN", "NE", "NO", "NYG", "NYJ", "PHI", "PIT", "SEA", "SF", "TB",
        "TEN", "WAS",
    },
}


def _parse_float(value: Any) -> float | None:
    if value in (None, "", "None"):
        return None
    try:
        return float(value)
    except Exception:
        return None


def _midpoint(bid_value: Any, ask_value: Any, fallback_value: Any = None) -> float | None:
    bid = _parse_float(bid_value)
    ask = _parse_float(ask_value)
    if bid is not None and ask is not None:
        return (bid + ask) / 2.0
    if bid is not None:
        return bid
    if ask is not None:
        return ask
    return _parse_float(fallback_value)


def _normalize_sports(values: Sequence[str]) -> set[str]:
    return {str(value or "").strip().lower() for value in values if str(value or "").strip()}


def _infer_sport(event_ticker: str) -> str:
    upper = str(event_ticker or "").upper()
    for code, sport in sorted(EVENT_CODE_TO_SPORT.items(), key=lambda item: -len(item[0])):
        if upper.startswith(f"KX{code}"):
            return sport
    return ""


def _parse_event_teams(event_ticker: str, sport: str) -> tuple[str, str]:
    tail = str(event_ticker or "").split("-")[-1].upper()
    match = re.search(r"\d([A-Z]+)$", tail)
    code_tail = match.group(1) if match else ""
    valid_codes = TEAM_CODES_BY_SPORT.get(sport, set())
    if code_tail and valid_codes:
        for split_index in range(2, len(code_tail) - 1):
            away = code_tail[:split_index]
            home = code_tail[split_index:]
            if away in valid_codes and home in valid_codes:
                return home, away
    return "", ""


def _infer_market_type(market: dict[str, Any]) -> str | None:
    title = str(market.get("title") or "")
    rules_primary = str(market.get("rules_primary") or "").lower()
    yes_sub_title = str(market.get("yes_sub_title") or "")
    event_ticker = str(market.get("event_ticker") or "").upper()

    if "winner?" in title.lower() or " wins the " in rules_primary:
        return "game_winner"
    if "TOTAL" in event_ticker or "collectively score more than" in rules_primary:
        return "game_total"
    if ":" in title and ("+" in yes_sub_title or "records " in rules_primary or " over " in rules_primary):
        return "player_over_under"
    return None


def _infer_stat_key(market: dict[str, Any], market_type: str) -> str:
    event_ticker = str(market.get("event_ticker") or "").upper()
    title = str(market.get("title") or "").lower()
    rules_primary = str(market.get("rules_primary") or "").lower()

    if market_type == "game_total":
        return "total"
    if market_type == "game_winner":
        return "winner"

    ticker_stat_map = [
        ("KXMLBHRR", "hitsrunsrbis"),
        ("KXMLBHIT", "hits"),
        ("KXMLBHR-", "homeruns"),
        ("KXMLBRBI", "rbis"),
        ("KXMLBRUN", "runs"),
        ("KXMLBTB", "totalbases"),
        ("3PT", "threes"),
        ("AST", "assists"),
        ("REB", "rebounds"),
        ("PTS", "points"),
        ("SAVES", "saves"),
        ("SOG", "shots"),
        ("SHOT", "shots"),
        ("GOALS", "goals"),
        ("BLK", "blocks"),
        ("STL", "steals"),
    ]
    for needle, stat_key in ticker_stat_map:
        if needle in event_ticker:
            return stat_key

    text_stat_map = [
        ("strikeout", "strikeouts"),
        ("assist", "assists"),
        ("rebound", "rebounds"),
        ("point", "points"),
        ("save", "saves"),
        ("shots on goal", "shots"),
        ("shot", "shots"),
        ("goal", "goals"),
        ("hit + run + rbi", "hitsrunsrbis"),
        ("hits + runs + rbis", "hitsrunsrbis"),
        ("total base", "totalbases"),
        ("home run", "homeruns"),
        ("hits", "hits"),
        (" hit ", "hits"),
        (" rbi", "rbis"),
        (" run", "runs"),
        ("block", "blocks"),
        ("steal", "steals"),
        ("strikeout", "strikeouts"),
    ]
    haystack = f"{title} {rules_primary}"
    for needle, stat_key in text_stat_map:
        if needle in haystack:
            return stat_key
    return ""


def _infer_player_name(market: dict[str, Any], market_type: str) -> str:
    if market_type != "player_over_under":
        return ""
    title = str(market.get("title") or "").strip()
    if ":" in title:
        return title.split(":", 1)[0].strip()
    return ""


def _infer_line(market: dict[str, Any], market_type: str) -> float | None:
    floor_strike = _parse_float(market.get("floor_strike"))
    if floor_strike is not None:
        return floor_strike

    yes_sub_title = str(market.get("yes_sub_title") or "")
    if market_type == "player_over_under":
        plus_match = re.search(r":\s*([0-9]+(?:\.[0-9]+)?)\+", yes_sub_title)
        if plus_match:
            return float(plus_match.group(1)) - 0.5
    over_match = re.search(r"Over\s+([0-9]+(?:\.[0-9]+)?)", yes_sub_title, flags=re.IGNORECASE)
    if over_match:
        return float(over_match.group(1))
    return None


def _price_pair_to_american(market: dict[str, Any]) -> tuple[int | None, int | None]:
    over_prob = _midpoint(
        market.get("yes_bid_dollars"),
        market.get("yes_ask_dollars"),
        market.get("last_price_dollars"),
    )
    under_prob = _midpoint(
        market.get("no_bid_dollars"),
        market.get("no_ask_dollars"),
        None,
    )
    if over_prob is None and under_prob is None:
        return None, None
    if over_prob is None and under_prob is not None:
        over_prob = 1.0 - under_prob
    if under_prob is None and over_prob is not None:
        under_prob = 1.0 - over_prob
    if over_prob is None or under_prob is None:
        return None, None
    return probability_to_american(over_prob), probability_to_american(under_prob)


def _normalize_market(market: dict[str, Any], *, include_game_winner: bool) -> dict[str, Any] | None:
    event_ticker = str(market.get("event_ticker") or "")
    if event_ticker.startswith("KXMVE"):
        return None

    sport = _infer_sport(event_ticker)
    market_type = _infer_market_type(market)
    if market_type is None:
        return None
    if market_type == "game_winner" and not include_game_winner:
        return None

    line_value = _infer_line(market, market_type)
    over_odds, under_odds = _price_pair_to_american(market)
    if over_odds is None or under_odds is None:
        return None

    home_team, away_team = _parse_event_teams(event_ticker, sport)
    return {
        "provider": "kalshi",
        "provider_event_id": event_ticker,
        "provider_market_id": market.get("ticker") or "",
        "provider_league": sport,
        "provider_market_name": market.get("title") or "",
        "book": "kalshi",
        "sport": sport,
        "market_type": market_type,
        "stat": _infer_stat_key(market, market_type),
        "player_name": _infer_player_name(market, market_type),
        "line": line_value if line_value is not None else "",
        "home_team": home_team,
        "away_team": away_team,
        "over_odds": over_odds,
        "under_odds": under_odds,
        "updated_at": market.get("updated_time") or market.get("created_time") or "",
        "period": "",
        "event_date": market.get("occurrence_datetime") or market.get("close_time") or "",
        "question": market.get("rules_primary") or market.get("title") or "",
    }


def fetch_rows(
    sports: Sequence[str],
    *,
    page_limit: int = 10,
    max_rows: int = 0,
    include_game_winner: bool = True,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    wanted_sports = _normalize_sports(sports)
    session = build_public_session("c2k-kalshi-ingest/1.0")
    rows: list[dict[str, Any]] = []
    raw_pages: list[dict[str, Any]] = []
    cursor = ""

    for _ in range(max(page_limit, 1)):
        params = {"status": "open", "limit": DEFAULT_LIMIT, "mve_filter": "exclude"}
        if cursor:
            params["cursor"] = cursor
        response = session.get(f"{BASE_URL}/markets", params=params, timeout=30)
        response.raise_for_status()
        payload = response.json()
        raw_pages.append(payload)

        for market in payload.get("markets") or []:
            sport = _infer_sport(market.get("event_ticker") or "")
            if wanted_sports and sport not in wanted_sports:
                continue
            normalized = _normalize_market(market, include_game_winner=include_game_winner)
            if normalized is None:
                continue
            rows.append(normalized)
            if max_rows > 0 and len(rows) >= max_rows:
                return rows, {"pages": raw_pages}

        cursor = str(payload.get("cursor") or "").strip()
        if not cursor:
            break

    return rows, {"pages": raw_pages}
