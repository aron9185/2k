from __future__ import annotations

from typing import Any, Sequence

from sportsbook_http import (
    get_browser_like_json,
    load_request_config,
    load_saved_payload,
    save_payload,
)


SPORT_TO_PAGE = {
    "mlb": "BASEBALL",
    "nba": "BASKETBALL",
    "nhl": "ICE_HOCKEY",
    "nfl": "AMERICAN_FOOTBALL",
}

SPORT_TO_HOST = {
    "mlb": "https://sbapi.nj.sportsbook.fanduel.com",
    "nba": "https://sbapi.nj.sportsbook.fanduel.com",
    "nhl": "https://sbapi.nj.sportsbook.fanduel.com",
    "nfl": "https://sbapi.nj.sportsbook.fanduel.com",
}

STAT_ALIASES = {
    "total points": "points",
    "points": "points",
    "rebounds": "rebounds",
    "assists": "assists",
    "total bases": "totalbases",
    "hits": "hits",
    "hits + runs + rbis": "hitsrunsrbis",
    "hits+runs+rbis": "hitsrunsrbis",
    "strikeouts": "strikeouts",
    "saves": "saves",
    "shots on goal": "shots",
    "shots": "shots",
    "total": "total",
}


def _normalize_sports(values: Sequence[str]) -> list[str]:
    return [str(value or "").strip().lower() for value in values if str(value or "").strip()]


def _parse_american(value: Any) -> int | None:
    if value in (None, "", "None"):
        return None
    try:
        return int(str(value).replace("+", "").strip())
    except Exception:
        return None


def _parse_line(value: Any) -> float | None:
    if value in (None, "", "None"):
        return None
    try:
        return float(value)
    except Exception:
        return None


def _normalize_stat(value: str) -> str:
    key = str(value or "").strip().lower()
    return STAT_ALIASES.get(key, key.replace(" ", "").replace("-", ""))


def parse_payload(payload: dict[str, Any], sport: str) -> list[dict[str, Any]]:
    attachments = payload.get("attachments") or {}
    events = attachments.get("events") or {}
    markets = attachments.get("markets") or {}
    runners = attachments.get("runners") or {}

    rows: list[dict[str, Any]] = []
    for market_id, market in markets.items():
        event_id = str(market.get("eventId") or "")
        event = events.get(event_id, {})
        runner_ids = market.get("runnerIds") or market.get("runners") or []
        runner_rows = [runners.get(str(runner_id)) for runner_id in runner_ids]
        runner_rows = [runner for runner in runner_rows if isinstance(runner, dict)]
        if len(runner_rows) != 2:
            continue

        market_name = str(market.get("marketName") or market.get("name") or "").strip()
        labels = {str(runner.get("runnerName") or "").strip().lower() for runner in runner_rows}
        market_type = None
        player_name = ""
        if labels == {"over", "under"}:
            if "total" in market_name.lower() and "player" not in market_name.lower():
                market_type = "game_total"
            else:
                market_type = "player_over_under"
                player_name = str(market.get("playerName") or market.get("marketTitle") or "").strip()
        elif len(runner_rows) == 2:
            market_type = "game_winner"

        if market_type is None:
            continue

        over = runner_rows[0]
        under = runner_rows[1]
        over_label = str(over.get("runnerName") or "").strip().lower()
        if over_label == "under":
            over, under = under, over

        over_odds = _parse_american(
            (((over.get("winRunnerOdds") or {}).get("americanDisplayOdds") or {}).get("americanOdds"))
            or over.get("odds")
        )
        under_odds = _parse_american(
            (((under.get("winRunnerOdds") or {}).get("americanDisplayOdds") or {}).get("americanOdds"))
            or under.get("odds")
        )
        if over_odds is None or under_odds is None:
            continue

        home_team = str(event.get("homeTeamName") or event.get("home") or "").strip()
        away_team = str(event.get("awayTeamName") or event.get("away") or "").strip()
        line = _parse_line(over.get("handicap") or under.get("handicap") or market.get("handicap"))

        rows.append(
            {
                "provider": "fanduel",
                "provider_event_id": event_id,
                "provider_market_id": market_id,
                "provider_league": sport,
                "provider_market_name": market_name,
                "book": "fanduel",
                "sport": sport,
                "market_type": market_type,
                "stat": _normalize_stat(market_name),
                "player_name": player_name,
                "line": line if line is not None else "",
                "home_team": home_team,
                "away_team": away_team,
                "over_odds": over_odds,
                "under_odds": under_odds,
                "updated_at": market.get("lastModified") or event.get("openDate") or "",
                "period": "",
                "event_date": event.get("openDate") or "",
                "question": market_name,
            }
        )
    return rows


def _default_urls(sport: str) -> list[str]:
    page_sport = SPORT_TO_PAGE.get(sport)
    host = SPORT_TO_HOST.get(sport)
    if not page_sport or not host:
        return []
    return [
        f"{host}/api/content-managed-page?page=SPORT&sport={page_sport}",
        f"{host}/api/content-managed-page?page=CUSTOM&customPageId={sport}",
    ]


def fetch_rows(
    sports: Sequence[str],
    *,
    save_payloads: bool = True,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    request_config = load_request_config("fanduel")
    all_rows: list[dict[str, Any]] = []
    raw_payloads: dict[str, Any] = {}

    for sport in _normalize_sports(sports):
        payload = load_saved_payload("fanduel", sport)
        if payload is None:
            headers = dict(request_config.get("headers") or {})
            urls = []
            sport_config = (request_config.get("sports") or {}).get(sport) or {}
            urls.extend(sport_config.get("urls") or [])
            proxy_url = str(
                sport_config.get("proxy_url")
                or request_config.get("proxy_url")
                or ""
            ).strip() or None
            impersonate = str(
                sport_config.get("impersonate")
                or request_config.get("impersonate")
                or "chrome136"
            ).strip()
            if not urls:
                urls.extend(_default_urls(sport))
            last_error = None
            for url in urls:
                try:
                    payload = get_browser_like_json(
                        url,
                        headers=headers,
                        proxy_url=proxy_url,
                        impersonate=impersonate,
                    )
                    if save_payloads:
                        save_payload("fanduel", sport, payload)
                    break
                except Exception as exc:
                    last_error = exc
            if payload is None and last_error is not None:
                raw_payloads[sport] = {"error": str(last_error)}
                continue
        raw_payloads[sport] = payload
        all_rows.extend(parse_payload(payload, sport))

    return all_rows, raw_payloads
