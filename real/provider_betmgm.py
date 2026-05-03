import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Sequence

import requests

from fair_odds import american_to_implied_prob, probability_to_american
from sportsbook_catalog import canonical_book_name, get_source
from sportsbook_http import load_saved_payload, save_payload


BASE_URL = "https://api.odds-api.io/v3"
BOOKMAKER_NAME = "BetMGM"
BOOK_CANONICAL = "betmgm"

# internal sport -> (provider sport slug, optional provider league slug)
SPORT_REQUESTS: dict[str, tuple[str, str]] = {
    "mlb": ("mlb", "mlb"),
    "nba": ("nba", "nba"),
    "wnba": ("wnba", "wnba"),
    "nhl": ("nhl", "nhl"),
    "nfl": ("nfl", "nfl"),
    "soccer": ("soccer", ""),
}

STAT_ALIASES = {
    "points": "points",
    "rebounds": "rebounds",
    "assists": "assists",
    "three pointers made": "madethrees",
    "3 pointers made": "madethrees",
    "three-pointers made": "madethrees",
    "3-pointers made": "madethrees",
    "shots on goal": "shots",
    "shots": "shots",
    "goals": "goals",
    "goal scorer": "goals",
    "goalscorer": "goals",
    "saves": "saves",
    "strikeouts": "strikeouts",
    "hits + runs + rbis": "hitsrunsrbis",
    "hits + runs + rbi": "hitsrunsrbis",
    "hits + runs + runs batted in": "hitsrunsrbis",
    "total bases": "totalbases",
    "hits": "hits",
    "home runs": "homeruns",
    "rbis": "rbis",
    "rbi": "rbis",
    "runs scored": "runs",
    "total": "total",
}


def _normalize_sports(values: Sequence[str]) -> list[str]:
    return [str(value or "").strip().lower() for value in values if str(value or "").strip()]


def _build_session() -> requests.Session:
    session = requests.Session()
    session.trust_env = False
    session.headers.update({"accept": "application/json", "user-agent": "c2k-betmgm-oddsapi/1.0"})
    return session


def _api_get(session: requests.Session, path: str, params: dict[str, Any]) -> Any:
    response = session.get(f"{BASE_URL}{path}", params=params, timeout=30)
    response.raise_for_status()
    return response.json()


def _chunked(values: list[str], size: int) -> list[list[str]]:
    chunks: list[list[str]] = []
    for index in range(0, len(values), size):
        chunks.append(values[index:index + size])
    return chunks


def _parse_float(value: Any) -> float | None:
    if value in (None, "", "None"):
        return None
    try:
        return float(value)
    except Exception:
        return None


def _decimal_to_american(value: Any) -> int | None:
    decimal = _parse_float(value)
    if decimal is None or decimal <= 1.0:
        return None
    if decimal >= 2.0:
        return int(round((decimal - 1.0) * 100.0))
    return int(round(-100.0 / (decimal - 1.0)))


def _synthetic_under_odds(over_odds: int) -> int:
    over_prob = american_to_implied_prob(over_odds)
    under_prob = max(1e-9, 1.0 - over_prob)
    return probability_to_american(under_prob)


def _normalize_stat(value: str) -> str:
    text = " ".join(str(value or "").strip().lower().split())
    if text in STAT_ALIASES:
        return STAT_ALIASES[text]
    key = "".join(ch for ch in text if ch.isalnum() or ch == " ")
    key = " ".join(key.split())
    if key in STAT_ALIASES:
        return STAT_ALIASES[key]
    return key.replace(" ", "")


def _infer_period_code(market_name: str) -> str:
    text = str(market_name or "").lower()
    if "first 5" in text or "1st 5" in text:
        return "F5"
    if "1st inning" in text or "first inning" in text:
        return "1I"
    if "2nd inning" in text or "second inning" in text:
        return "2I"
    if "3rd inning" in text or "third inning" in text:
        return "3I"
    if "4th inning" in text or "fourth inning" in text:
        return "4I"
    if "1st quarter" in text or "q1" in text:
        return "1Q"
    if "2nd quarter" in text or "q2" in text:
        return "2Q"
    if "3rd quarter" in text or "q3" in text:
        return "3Q"
    if "4th quarter" in text or "q4" in text:
        return "4Q"
    if "1st period" in text or "p1" in text:
        return "1P"
    if "2nd period" in text or "p2" in text:
        return "2P"
    if "3rd period" in text or "p3" in text:
        return "3P"
    if "1st half" in text or "first half" in text:
        return "1H"
    if "2nd half" in text or "second half" in text:
        return "2H"
    tokens = text.replace("-", " ").replace("/", " ").split()
    if "overtime" in tokens or "ot" in tokens:
        return "OT"
    return ""


def _is_moneyline_market(market_name: str) -> bool:
    key = " ".join(str(market_name or "").strip().lower().split())
    return key in {"ml", "moneyline", "money line", "match winner", "winner"}


def _is_spread_market(market_name: str) -> bool:
    key = str(market_name or "").strip().lower()
    return "spread" in key or "handicap" in key or "run line" in key or "puck line" in key


def _is_total_market(market_name: str) -> bool:
    key = str(market_name or "").strip().lower()
    return key in {"totals", "over/under", "total"} or ("total" in key and "player" not in key)


def _is_player_props_market(market_name: str) -> bool:
    key = str(market_name or "").strip().lower()
    return key.startswith("player props")


def _player_prop_stat(market_name: str) -> str:
    name = " ".join(str(market_name or "").strip().split())
    if " - " in name:
        return _normalize_stat(name.split(" - ", 1)[1])
    return _normalize_stat(name)


def _single_side_player_market(market_name: str) -> tuple[str, float] | None:
    text = " ".join(str(market_name or "").strip().lower().split())
    if "anytime goal scorer" in text or "any time goal scorer" in text:
        return "goals", 0.5

    patterns = (
        (r"player to score ([0-9]+)\+ goals?", "goals"),
        (r"player to record ([0-9]+)\+ goals?", "goals"),
        (r"player to record ([0-9]+)\+ points?", "points"),
        (r"player to record ([0-9]+)\+ assists?", "assists"),
        (r"player to record ([0-9]+)\+ shots? on goal", "shots"),
    )
    for pattern, stat_key in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if not match:
            continue
        try:
            line = float(match.group(1)) - 0.5
        except Exception:
            continue
        return stat_key, line
    return None


def _row_base(
    *,
    internal_sport: str,
    event_payload: dict[str, Any],
    market_name: str,
    market_id: str,
    period: str,
) -> dict[str, Any]:
    event_id = event_payload.get("id")
    home_team = str(event_payload.get("home") or "").strip()
    away_team = str(event_payload.get("away") or "").strip()
    event_date = event_payload.get("date") or ""
    league_slug = ((event_payload.get("league") or {}).get("slug")) or ""
    source = get_source(BOOK_CANONICAL)
    return {
        "provider": "betmgm",
        "provider_event_id": event_id,
        "provider_market_id": market_id,
        "provider_league": league_slug,
        "provider_market_name": market_name,
        "book": BOOK_CANONICAL,
        "book_display_name": source.display_name if source else BOOKMAKER_NAME,
        "book_category": source.category if source else "sportsbook",
        "sport": internal_sport,
        "home_team": home_team,
        "away_team": away_team,
        "updated_at": event_date,
        "period": period,
        "event_date": event_date,
        "question": market_name,
    }


def _parse_event_odds(event_payload: dict[str, Any], *, internal_sport: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    bookmakers = event_payload.get("bookmakers") or {}
    for bookmaker_name, markets in bookmakers.items():
        if canonical_book_name(bookmaker_name) != BOOK_CANONICAL:
            continue
        for market in markets or []:
            market_name = str(market.get("name") or "").strip()
            period = _infer_period_code(market_name)
            market_id = str(market.get("id") or market_name).strip()
            odds_rows = market.get("odds") or []
            if not isinstance(odds_rows, list):
                continue

            if _is_moneyline_market(market_name):
                for odds_row in odds_rows:
                    home_odds = _decimal_to_american(odds_row.get("home"))
                    away_odds = _decimal_to_american(odds_row.get("away"))
                    if home_odds is None or away_odds is None:
                        continue
                    rows.append(
                        {
                            **_row_base(
                                internal_sport=internal_sport,
                                event_payload=event_payload,
                                market_name=market_name,
                                market_id=market_id,
                                period=period,
                            ),
                            "market_type": "game_winner",
                            "stat": "winner",
                            "player_name": "",
                            "line": "",
                            "over_odds": home_odds,
                            "under_odds": away_odds,
                        }
                    )
                continue

            if _is_spread_market(market_name):
                for odds_row in odds_rows:
                    home_odds = _decimal_to_american(odds_row.get("home"))
                    away_odds = _decimal_to_american(odds_row.get("away"))
                    home_spread = _parse_float(odds_row.get("hdp"))
                    if home_odds is None or away_odds is None or home_spread is None:
                        continue
                    rows.append(
                        {
                            **_row_base(
                                internal_sport=internal_sport,
                                event_payload=event_payload,
                                market_name=market_name,
                                market_id=market_id,
                                period=period,
                            ),
                            "market_type": "game_spread",
                            "stat": "spread",
                            "player_name": "",
                            "line": abs(home_spread),
                            "home_spread": home_spread,
                            "away_spread": -home_spread,
                            "over_odds": home_odds,
                            "under_odds": away_odds,
                        }
                    )
                continue

            if _is_total_market(market_name):
                for odds_row in odds_rows:
                    over_odds = _decimal_to_american(odds_row.get("over"))
                    under_odds = _decimal_to_american(odds_row.get("under"))
                    line = _parse_float(odds_row.get("hdp"))
                    if over_odds is None or under_odds is None or line is None:
                        continue
                    rows.append(
                        {
                            **_row_base(
                                internal_sport=internal_sport,
                                event_payload=event_payload,
                                market_name=market_name,
                                market_id=market_id,
                                period=period,
                            ),
                            "market_type": "game_total",
                            "stat": "total",
                            "player_name": "",
                            "line": line,
                            "over_odds": over_odds,
                            "under_odds": under_odds,
                        }
                    )
                continue

            single_side_market = _single_side_player_market(market_name)
            if single_side_market is not None:
                stat_key, line = single_side_market
                for odds_row in odds_rows:
                    player_name = str(odds_row.get("label") or "").strip()
                    over_odds = _decimal_to_american(
                        odds_row.get("odds")
                        or odds_row.get("over")
                        or odds_row.get("price")
                    )
                    if not player_name or over_odds is None:
                        continue
                    rows.append(
                        {
                            **_row_base(
                                internal_sport=internal_sport,
                                event_payload=event_payload,
                                market_name=market_name,
                                market_id=f"{market_id}:{player_name}",
                                period=period,
                            ),
                            "market_type": "player_over_under",
                            "stat": stat_key,
                            "player_name": player_name,
                            "line": line,
                            "over_odds": over_odds,
                            "under_odds": _synthetic_under_odds(over_odds),
                            "question": f"{player_name} {market_name}",
                        }
                    )
                continue

            if not _is_player_props_market(market_name):
                continue
            stat_key = _player_prop_stat(market_name)
            for odds_row in odds_rows:
                player_name = str(odds_row.get("label") or "").strip()
                line = _parse_float(odds_row.get("hdp"))
                over_odds = _decimal_to_american(odds_row.get("over"))
                under_odds = _decimal_to_american(odds_row.get("under"))
                if not player_name or line is None or over_odds is None or under_odds is None:
                    continue
                rows.append(
                    {
                        **_row_base(
                            internal_sport=internal_sport,
                            event_payload=event_payload,
                            market_name=market_name,
                            market_id=f"{market_id}:{player_name}:{line}",
                            period=period,
                        ),
                        "market_type": "player_over_under",
                        "stat": stat_key,
                        "player_name": player_name,
                        "line": line,
                        "over_odds": over_odds,
                        "under_odds": under_odds,
                        "question": f"{player_name} {market_name}",
                    }
                )
    return rows


def _fetch_live_payload(*, internal_sport: str, api_key: str) -> list[dict[str, Any]]:
    sport_slug, league_slug = SPORT_REQUESTS.get(internal_sport, (internal_sport, ""))
    now = datetime.now(timezone.utc)
    base_params: dict[str, Any] = {
        "apiKey": api_key,
        "sport": sport_slug,
        "status": "pending,live",
        "from": now.isoformat().replace("+00:00", "Z"),
        "to": (now + timedelta(hours=24)).isoformat().replace("+00:00", "Z"),
        "bookmaker": BOOKMAKER_NAME,
    }
    if league_slug:
        base_params["league"] = league_slug

    session = _build_session()
    events = _api_get(session, "/events", base_params)
    if not isinstance(events, list) or not events:
        return []
    event_ids = [str(event.get("id")) for event in events if event.get("id") is not None]
    if not event_ids:
        return []

    payloads: list[dict[str, Any]] = []
    for batch in _chunked(event_ids, 10):
        params = {
            "apiKey": api_key,
            "eventIds": ",".join(batch),
            "bookmakers": BOOKMAKER_NAME,
        }
        result = _api_get(session, "/odds/multi", params)
        if isinstance(result, list):
            payloads.extend(item for item in result if isinstance(item, dict))
    return payloads


def fetch_rows(
    sports: Sequence[str],
    *,
    save_payloads: bool = True,
    use_saved_payloads: bool = True,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    api_key = os.environ.get("ODDS_API_IO_KEY", "").strip() or os.environ.get("ODDS_API_KEY", "").strip()
    all_rows: list[dict[str, Any]] = []
    raw_payloads: dict[str, Any] = {}

    for sport in _normalize_sports(sports):
        payload = load_saved_payload("betmgm", sport) if use_saved_payloads else None
        if payload is None:
            if not api_key:
                raw_payloads[sport] = {
                    "error": (
                        "missing ODDS_API_IO_KEY/ODDS_API_KEY for BetMGM provider "
                        "(direct website scraping is typically blocked by captcha/affiliate access controls)"
                    )
                }
                continue
            try:
                payload = _fetch_live_payload(internal_sport=sport, api_key=api_key)
                if save_payloads:
                    save_payload("betmgm", sport, payload)
            except Exception as exc:
                raw_payloads[sport] = {"error": str(exc)}
                continue

        raw_payloads[sport] = payload
        event_payloads = payload if isinstance(payload, list) else []
        for event_payload in event_payloads:
            if not isinstance(event_payload, dict):
                continue
            all_rows.extend(_parse_event_odds(event_payload, internal_sport=sport))

    return all_rows, raw_payloads
