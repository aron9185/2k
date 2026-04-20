from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

import requests

from market_csv import write_market_rows
from sportsbook_catalog import TARGET_SOURCES, canonical_book_name, get_source


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT = BASE_DIR / "sportsbook_markets.csv"
DEFAULT_CACHE_DIR = BASE_DIR / ".cache" / "odds_api_io"
BASE_URL = "https://api.odds-api.io/v3"

# Provider-facing bookmaker names. These should match the external provider's
# expected names, not our internal canonical slugs.
ODDS_API_IO_BOOKMAKERS = {
    "fanduel": "FanDuel",
    "draftkings": "DraftKings",
    "prizepicks": "PrizePicks",
    "underdog": "Underdog",
    "novig": "Novig",
    "prophetx": "ProphetX",
    "hardrockbet": "Hard Rock Bet",
    "thescorebet": "theScore Bet",
    "fanatics": "Fanatics",
    "betmgm": "BetMGM",
    "caesars": "Caesars",
    "draftkingspick6": "DraftKings Pick6",
    "betr": "betr",
    "sleeper": "Sleeper",
    "dabble": "Dabble",
    "parlayplay": "ParlayPlay",
    "bet365": "Bet365",
    "fliff": "Fliff",
    "sportsbookrhodeisland": "Sportsbook Rhode Island",
    "onyxodds": "Onyx Odds",
    "circa": "Circa",
    "ballybet": "Bally Bet",
    "betrivers": "BetRivers",
    "polymarket": "Polymarket",
    "kalshi": "Kalshi",
    "coinbase": "Coinbase",
    "rebet": "Rebet",
    "betparx": "betPARX",
    "sugarhouse": "SugarHouse",
    "bovada": "Bovada",
    "bodog": "Bodog",
}

SPORT_ALIASES = {
    "mlb": "baseball",
    "baseball": "baseball",
    "nba": "basketball",
    "wnba": "basketball",
    "ncaam": "basketball",
    "ncaawb": "basketball",
    "nhl": "hockey",
    "nfl": "football",
    "ncaaf": "football",
    "soccer": "soccer",
    "mma": "mma",
    "ufc": "mma",
    "golf": "golf",
    "tennis": "tennis",
}

MARKET_NAME_TO_STAT = {
    "points": "points",
    "rebounds": "rebounds",
    "assists": "assists",
    "three-pointers made": "threes",
    "3-pointers made": "threes",
    "shots on goal": "shots",
    "shots": "shots",
    "saves": "saves",
    "strikeouts": "strikeouts",
    "hits + runs + rbis": "hitsrunsrbis",
    "hits + runs + rbi": "hitsrunsrbis",
    "hits + runs + runs batted in": "hitsrunsrbis",
    "total bases": "totalbases",
    "hits": "hits",
    "home runs": "homeruns",
    "rbis": "rbis",
    "runs scored": "runs",
}


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Ingest sportsbook markets from odds-api.io and normalize them into "
            "real/sportsbook_markets.csv for the Real Sports poll matcher."
        )
    )
    parser.add_argument("--sport", required=True, help="Provider sport slug or alias, e.g. baseball, basketball, hockey.")
    parser.add_argument("--league", default="", help="Optional provider league slug, e.g. mlb or nba.")
    parser.add_argument("--status", default="pending,live", help="Comma-separated event statuses.")
    parser.add_argument("--hours-ahead", type=int, default=24, help="How far ahead to pull pending events.")
    parser.add_argument(
        "--bookmakers",
        default="target",
        help="Comma-separated canonical books to request, or 'target' for the full target catalog intersection.",
    )
    parser.add_argument(
        "--event-bookmaker",
        default="DraftKings",
        help="Provider bookmaker name used to filter the event list efficiently.",
    )
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR))
    parser.add_argument("--dump-json", default="", help="Optional path to dump the raw provider payload.")
    parser.add_argument("--append", action="store_true", help="Append to the output CSV instead of replacing it.")
    parser.add_argument("--limit-events", type=int, default=0, help="Optional max number of events to fetch.")
    return parser.parse_args()


def normalize_sport(value: str) -> str:
    key = str(value or "").strip().lower()
    return SPORT_ALIASES.get(key, key)


def provider_bookmakers_from_arg(value: str) -> list[str]:
    if str(value).strip().lower() == "target":
        provider_names = []
        for source in TARGET_SOURCES:
            provider_name = ODDS_API_IO_BOOKMAKERS.get(source.canonical)
            if provider_name:
                provider_names.append(provider_name)
        return provider_names

    provider_names = []
    for raw in str(value or "").split(","):
        raw = raw.strip()
        if not raw:
            continue
        canonical = canonical_book_name(raw)
        provider_names.append(ODDS_API_IO_BOOKMAKERS.get(canonical, raw))
    return provider_names


def chunked(values: list[Any], size: int) -> Iterable[list[Any]]:
    for index in range(0, len(values), size):
        yield values[index:index + size]


def build_session() -> requests.Session:
    session = requests.Session()
    session.trust_env = False
    session.headers.update({"accept": "application/json", "user-agent": "c2k-odds-ingest/1.0"})
    return session


def api_get(session: requests.Session, path: str, params: dict[str, Any]) -> Any:
    url = f"{BASE_URL}{path}"
    response = session.get(url, params=params, timeout=30)
    response.raise_for_status()
    return response.json()


def decimal_to_american(value: str | float | int | None) -> int | None:
    if value in (None, "", "None"):
        return None
    decimal = float(value)
    if decimal <= 1.0:
        return None
    if decimal >= 2.0:
        return int(round((decimal - 1.0) * 100.0))
    return int(round(-100.0 / (decimal - 1.0)))


def infer_market_family(market_name: str) -> str | None:
    name = str(market_name or "").strip().lower()
    if name in {"over/under", "totals"}:
        return "game_total"
    if name.startswith("player props"):
        return "player_over_under"
    return None


def infer_stat_key(market_name: str) -> str:
    name = str(market_name or "").strip().lower()
    suffix = name
    if " - " in suffix:
        suffix = suffix.split(" - ", 1)[1].strip()
    return MARKET_NAME_TO_STAT.get(suffix, suffix.replace(" ", "").replace("-", ""))


def infer_period(market_name: str) -> str:
    name = str(market_name or "").lower()
    if "1st period" in name:
        return "1"
    if "2nd period" in name:
        return "2"
    if "3rd period" in name:
        return "3"
    if "1st half" in name:
        return "1h"
    if "2nd half" in name:
        return "2h"
    return ""


def normalize_book_display_to_canonical(value: str) -> str:
    return canonical_book_name(value)


def extract_market_rows(event_payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    home_team = event_payload.get("home") or ""
    away_team = event_payload.get("away") or ""
    event_id = event_payload.get("id")
    sport_slug = ((event_payload.get("sport") or {}).get("slug")) or ""
    league_slug = ((event_payload.get("league") or {}).get("slug")) or ""
    event_date = event_payload.get("date") or ""

    for bookmaker_name, markets in (event_payload.get("bookmakers") or {}).items():
        canonical_book = normalize_book_display_to_canonical(bookmaker_name)
        source = get_source(canonical_book)
        for market in markets or []:
            market_name = market.get("name") or ""
            market_family = infer_market_family(market_name)
            if market_family is None:
                continue

            stat_key = infer_stat_key(market_name)
            period = infer_period(market_name)
            updated_at = market.get("updatedAt") or ""

            for quote in market.get("odds") or []:
                if market_family == "player_over_under":
                    player_name = str(quote.get("label") or "").strip()
                    line = quote.get("hdp")
                    over_odds = decimal_to_american(quote.get("over"))
                    under_odds = decimal_to_american(quote.get("under"))
                else:
                    player_name = ""
                    line = quote.get("max", quote.get("hdp"))
                    over_odds = decimal_to_american(quote.get("over"))
                    under_odds = decimal_to_american(quote.get("under"))

                if line in (None, "") or over_odds is None or under_odds is None:
                    continue

                rows.append(
                    {
                        "provider": "odds-api.io",
                        "provider_event_id": event_id,
                        "provider_league": league_slug,
                        "provider_market_name": market_name,
                        "book": canonical_book,
                        "book_display_name": source.display_name if source else bookmaker_name,
                        "book_category": source.category if source else "",
                        "sport": sport_slug,
                        "market_type": market_family,
                        "stat": stat_key,
                        "player_name": player_name,
                        "line": line,
                        "home_team": home_team,
                        "away_team": away_team,
                        "over_odds": over_odds,
                        "under_odds": under_odds,
                        "updated_at": updated_at or event_date,
                        "period": period,
                        "event_date": event_date,
                    }
                )
    return rows


def fetch_events(
    session: requests.Session,
    *,
    api_key: str,
    sport: str,
    league: str,
    status: str,
    event_bookmaker: str,
    hours_ahead: int,
    limit_events: int,
) -> list[dict[str, Any]]:
    now = datetime.now(timezone.utc)
    params: dict[str, Any] = {
        "apiKey": api_key,
        "sport": sport,
        "status": status,
        "from": now.isoformat().replace("+00:00", "Z"),
        "to": (now + timedelta(hours=hours_ahead)).isoformat().replace("+00:00", "Z"),
        "bookmaker": event_bookmaker,
    }
    if league:
        params["league"] = league
    events = api_get(session, "/events", params=params)
    if limit_events > 0:
        events = events[:limit_events]
    return events


def fetch_multi_odds(
    session: requests.Session,
    *,
    api_key: str,
    event_ids: list[Any],
    bookmakers: list[str],
) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for batch in chunked([str(event_id) for event_id in event_ids if event_id], 10):
        params = {
            "apiKey": api_key,
            "eventIds": ",".join(batch),
            "bookmakers": ",".join(bookmakers),
        }
        result = api_get(session, "/odds/multi", params=params)
        if isinstance(result, list):
            payloads.extend(result)
    return payloads


def main():
    args = parse_args()
    api_key = os.environ.get("ODDS_API_IO_KEY", "").strip() or os.environ.get("ODDS_API_KEY", "").strip()
    if not api_key:
        raise SystemExit("Set ODDS_API_IO_KEY (or ODDS_API_KEY) before running this script.")

    sport = normalize_sport(args.sport)
    bookmakers = provider_bookmakers_from_arg(args.bookmakers)
    if not bookmakers:
        raise SystemExit("No provider bookmakers resolved from --bookmakers.")

    session = build_session()
    events = fetch_events(
        session,
        api_key=api_key,
        sport=sport,
        league=args.league,
        status=args.status,
        event_bookmaker=args.event_bookmaker,
        hours_ahead=args.hours_ahead,
        limit_events=args.limit_events,
    )
    event_ids = [event.get("id") for event in events]
    odds_payloads = fetch_multi_odds(
        session,
        api_key=api_key,
        event_ids=event_ids,
        bookmakers=bookmakers,
    )

    rows: list[dict[str, Any]] = []
    for payload in odds_payloads:
        rows.extend(extract_market_rows(payload))

    write_market_rows(args.output, rows, append=args.append)
    print(
        f"Saved {len(rows)} normalized market rows across {len(odds_payloads)} events "
        f"to {args.output}"
    )

    if args.dump_json:
        dump_path = Path(args.dump_json)
        dump_path.parent.mkdir(parents=True, exist_ok=True)
        dump_path.write_text(json.dumps(odds_payloads, indent=2, ensure_ascii=False), encoding="utf8")
        print(f"Saved raw odds payload to {dump_path}")


if __name__ == "__main__":
    main()
