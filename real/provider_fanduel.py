from __future__ import annotations

import json
import re
from urllib.parse import urlencode
from typing import Any, Sequence

from fair_odds import american_to_implied_prob, probability_to_american
from sportsbook_http import (
    get_browser_like_json,
    load_request_config,
    load_saved_payload,
    post_browser_like_json,
    save_payload,
)


SPORT_TO_PAGE = {
    "mlb": "BASEBALL",
    "nba": "BASKETBALL",
    "nhl": "ICE_HOCKEY",
    "nfl": "AMERICAN_FOOTBALL",
    "soccer": "FOOTBALL",
}

SPORT_TO_HOST = {
    "mlb": "https://sbapi.nj.sportsbook.fanduel.com",
    "nba": "https://sbapi.nj.sportsbook.fanduel.com",
    "nhl": "https://sbapi.nj.sportsbook.fanduel.com",
    "nfl": "https://sbapi.nj.sportsbook.fanduel.com",
    "soccer": "https://sbapi.nj.sportsbook.fanduel.com",
}

DEFAULT_QUERY_PARAMS = {
    "currencyCode": "USD",
    "exchangeLocale": "en_US",
    "includePrices": "true",
    "language": "en",
    "regionCode": "NAMERICA",
    "timezone": "America/New_York",
    "_ak": "FhMFpcPWXMeyZxOx",
}

FANDUEL_API_KEY = DEFAULT_QUERY_PARAMS["_ak"]
FANDUEL_APP_VERSION = "2.142.2"
FANDUEL_REGION = "NJ"
FANDUEL_GATEWAY_HOST = "https://api.sportsbook.fanduel.com"
FANDUEL_MGA_HOST = "https://scan.nj.sportsbook.fanduel.com"
FANDUEL_SBAPI_HOST = "https://api.sportsbook.fanduel.com/sbapi"
FANDUEL_SMP_HOST = "https://smp.nj.sportsbook.fanduel.com"
FANDUEL_SOCCER_EVENT_TYPE_ID = 1
FANDUEL_SOCCER_COMPETITION_IDS = [228]
FANDUEL_SOCCER_TAB_KEYWORDS = ("popular", "goals", "half")
FANDUEL_PRICE_BATCH_SIZE = 70
FANDUEL_EVENT_TAB_KEYWORDS_BY_SPORT = {
    "mlb": ("innings", "quick bets"),
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
    "total runs": "total",
    "total points": "total",
    "total goals": "total",
    "total bases": "totalbases",
    "home runs": "homeruns",
    "rbi": "rbis",
    "rbis": "rbis",
    "stolen base": "stolenbases",
    "stolen bases": "stolenbases",
    "both teams to score": "bothteamsscore",
}

LADDER_MARKET_MAP = (
    (re.compile(r"^to hit a home run$", re.IGNORECASE), "homeruns", 0.5),
    (re.compile(r"^to record a hit$", re.IGNORECASE), "hits", 0.5),
    (re.compile(r"^to record ([0-9]+)\+ hits$", re.IGNORECASE), "hits", -0.5),
    (re.compile(r"^to record ([0-9]+)\+ total bases$", re.IGNORECASE), "totalbases", -0.5),
    (re.compile(r"^to record an rbi$", re.IGNORECASE), "rbis", 0.5),
    (re.compile(r"^to record ([0-9]+)\+ rbis$", re.IGNORECASE), "rbis", -0.5),
    (re.compile(r"^to record a stolen base$", re.IGNORECASE), "stolenbases", 0.5),
)


def _normalize_sports(values: Sequence[str]) -> list[str]:
    return [str(value or "").strip().lower() for value in values if str(value or "").strip()]


def _parse_american(value: Any) -> int | None:
    if value in (None, "", "None"):
        return None
    try:
        return int(float(str(value).replace("+", "").strip()))
    except Exception:
        return None


def _synthetic_under_odds(over_odds: int) -> int:
    over_prob = american_to_implied_prob(over_odds)
    under_prob = max(1e-9, 1.0 - over_prob)
    return probability_to_american(under_prob)


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


def _runner_odds(runner: dict[str, Any]) -> int | None:
    return _parse_american(
        (((runner.get("winRunnerOdds") or {}).get("americanDisplayOdds") or {}).get("americanOdds"))
        or (((runner.get("winRunnerOdds") or {}).get("americanDisplayOdds") or {}).get("americanOddsInt"))
        or ((runner.get("americanDisplayOdds") or {}).get("americanOdds"))
        or ((runner.get("americanDisplayOdds") or {}).get("americanOddsInt"))
        or runner.get("odds")
    )


def _runner_label(runner: dict[str, Any]) -> str:
    return str(runner.get("runnerName") or runner.get("nameAbbr") or "").strip()


def _runner_outcome_key(runner: dict[str, Any], home_team: str = "", away_team: str = "") -> str:
    result = runner.get("result") if isinstance(runner.get("result"), dict) else {}
    raw_key = str(
        runner.get("outcomeType")
        or runner.get("resultType")
        or result.get("type")
        or ""
    ).strip().lower()
    label = _runner_label(runner)
    label_key = "".join(ch for ch in label.lower() if ch.isalnum())
    if raw_key in {"home", "away", "draw", "tie", "yes", "no", "1x", "x2", "12"}:
        return {"tie": "draw"}.get(raw_key, raw_key)
    if label_key in {"draw", "tie"}:
        return "draw"
    if label_key in {"yes", "no"}:
        return label_key

    normalized_home = "".join(ch for ch in _clean_team_name(home_team).lower() if ch.isalnum())
    normalized_away = "".join(ch for ch in _clean_team_name(away_team).lower() if ch.isalnum())
    has_home = bool(normalized_home and normalized_home in label_key)
    has_away = bool(normalized_away and normalized_away in label_key)
    has_draw = "draw" in label_key or "tie" in label_key
    if has_home and has_draw:
        return "1x"
    if has_away and has_draw:
        return "x2"
    if has_home and has_away:
        return "12"

    if normalized_home and (label_key == normalized_home or label_key in normalized_home or normalized_home in label_key):
        return "home"
    if normalized_away and (label_key == normalized_away or label_key in normalized_away or normalized_away in label_key):
        return "away"

    return label_key


def _runner_outcomes_json(
    runners: list[dict[str, Any]],
    *,
    home_team: str = "",
    away_team: str = "",
) -> str:
    outcomes = []
    for runner in runners:
        odds = _runner_odds(runner)
        if odds is None:
            continue
        outcomes.append(
            {
                "key": _runner_outcome_key(runner, home_team, away_team),
                "label": _runner_label(runner),
                "odds": odds,
            }
        )
    return json.dumps(outcomes, separators=(",", ":"), ensure_ascii=True) if outcomes else ""


def _clean_team_name(value: str) -> str:
    text = str(value or "").strip()
    text = re.sub(r"\s*\([^)]*\)\s*", " ", text)
    return " ".join(text.split())


def _event_teams(event: dict[str, Any]) -> tuple[str, str]:
    home = str(event.get("homeTeamName") or event.get("home") or "").strip()
    away = str(event.get("awayTeamName") or event.get("away") or "").strip()
    if home or away:
        return _clean_team_name(home), _clean_team_name(away)

    competitors = event.get("competitors") or event.get("teams") or []
    if isinstance(competitors, list):
        home_candidate = ""
        away_candidate = ""
        for competitor in competitors:
            if not isinstance(competitor, dict):
                continue
            name = str(
                competitor.get("name")
                or competitor.get("teamName")
                or competitor.get("displayName")
                or ""
            ).strip()
            role = str(
                competitor.get("homeAway")
                or competitor.get("venueRole")
                or competitor.get("side")
                or ""
            ).strip().lower()
            if role == "home":
                home_candidate = name
            elif role == "away":
                away_candidate = name
        if home_candidate or away_candidate:
            return _clean_team_name(home_candidate), _clean_team_name(away_candidate)

    name = str(event.get("name") or "").strip()
    if " @ " in name:
        away_text, home_text = name.split(" @ ", 1)
        return _clean_team_name(home_text), _clean_team_name(away_text)
    match = re.match(r"(.+?)\s+(?:vs\.?|v)\s+(.+)", name, flags=re.IGNORECASE)
    if match:
        home_text, away_text = match.group(1), match.group(2)
        return _clean_team_name(home_text), _clean_team_name(away_text)
    return "", ""


def _runner_team_name(runner: dict[str, Any]) -> str:
    return _clean_team_name(str(runner.get("nameAbbr") or runner.get("runnerName") or ""))


def _find_team_runner(runners: list[dict[str, Any]], team_name: str) -> dict[str, Any] | None:
    wanted = "".join(ch for ch in _clean_team_name(team_name).lower() if ch.isalnum())
    if not wanted:
        return None
    for runner in runners:
        values = [
            str(runner.get("runnerName") or ""),
            str(runner.get("nameAbbr") or ""),
        ]
        for value in values:
            key = "".join(ch for ch in _clean_team_name(value).lower() if ch.isalnum())
            if key and (key == wanted or key in wanted or wanted in key):
                return runner
    return None


def _find_draw_runner(runners: list[dict[str, Any]]) -> dict[str, Any] | None:
    for runner in runners:
        key = _runner_outcome_key(runner)
        if key == "draw":
            return runner
    return None


def _find_yes_no_runners(runners: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    yes_runner = None
    no_runner = None
    for runner in runners:
        key = _runner_outcome_key(runner)
        if key == "yes":
            yes_runner = runner
        elif key == "no":
            no_runner = runner
    return yes_runner, no_runner


def _is_over_label(value: str) -> bool:
    return bool(re.match(r"^(?:1st\s+half\s+)?over\b", str(value or "").strip(), flags=re.IGNORECASE))


def _is_under_label(value: str) -> bool:
    return bool(re.match(r"^(?:1st\s+half\s+)?under\b", str(value or "").strip(), flags=re.IGNORECASE))


def _find_over_under_runners(runners: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    over_runner = None
    under_runner = None
    for runner in runners:
        label = _runner_label(runner)
        if _is_over_label(label):
            over_runner = runner
        elif _is_under_label(label):
            under_runner = runner
    return over_runner, under_runner


def _extract_total_line(market_name: str, market_type_name: str, runners: list[dict[str, Any]]) -> float | None:
    for runner in runners:
        label = _runner_label(runner)
        match = re.search(r"\b(?:over|under)\s+([0-9]+(?:\.[0-9]+)?)", label, flags=re.IGNORECASE)
        if match:
            return float(match.group(1))

    text = f"{market_name} {market_type_name}"
    match = re.search(r"over/under[_\s-]*([0-9]+(?:\.[0-9]+)?)", text, flags=re.IGNORECASE)
    if match:
        return float(match.group(1))

    compact_match = re.search(r"\bOVER_UNDER_(\d)(\d)\b", str(market_type_name or "").upper())
    if compact_match:
        return float(f"{compact_match.group(1)}.{compact_match.group(2)}")

    return None


def _ladder_market(market_name: str) -> tuple[str, float] | None:
    text = " ".join(str(market_name or "").strip().split())
    for pattern, stat, line_delta in LADDER_MARKET_MAP:
        match = pattern.match(text)
        if not match:
            continue
        if match.groups():
            return stat, float(match.group(1)) + line_delta
        return stat, line_delta
    return None


def _is_moneyline_market(market_name: str, market_type_name: str) -> bool:
    market_key = " ".join(str(market_name or "").strip().lower().split())
    type_key = str(market_type_name or "").strip().upper()
    return (
        market_key
        in {
            "moneyline",
            "moneyline (3-way)",
            "match result",
            "match betting",
            "90 min result",
            "90 minute result",
            "moneyline 1st half",
            "1st half moneyline",
            "half time result",
            "half-time result",
            "halftime result",
        }
        or type_key
        in {
            "MONEY_LINE",
            "WIN-DRAW-WIN",
            "MATCH_RESULT",
            "MATCH_BETTING",
            "1ST_HALF_MONEY_LINE",
            "HALF_TIME_RESULT",
            "HALF-TIME_RESULT",
            "HALFTIME_RESULT",
        }
    )


def _is_halftime_market(market_name: str, market_type_name: str) -> bool:
    text = f"{market_name} {market_type_name}".lower()
    return "half" in text and ("1st" in text or "first" in text or "time" in text)


def _is_first_five_market(market_name: str, market_type_name: str) -> bool:
    text = f"{market_name} {market_type_name}".lower()
    return "first 5" in text or "1st 5" in text or ("inning" in text and "1st_half" in text)


def _infer_period_code(market_name: str, market_type_name: str) -> str:
    text = f"{market_name} {market_type_name}".lower()
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
    if "1st half" in text or "first half" in text or "half time" in text or "halftime" in text:
        return "1H"
    if "2nd half" in text or "second half" in text:
        return "2H"
    if "overtime" in text or "ot" in text:
        return "OT"
    return ""


def _is_both_teams_score_market(market_name: str, market_type_name: str) -> bool:
    text = f"{market_name} {market_type_name}".lower()
    return "both teams" in text and ("score" in text or "scoring" in text)


def _is_yes_no_first_inning_runs_market(market_name: str, market_type_name: str) -> bool:
    text = f"{market_name} {market_type_name}".lower()
    return ("inning" in text or "innings" in text) and "run" in text


def _is_double_chance_market(market_name: str, market_type_name: str) -> bool:
    text = f"{market_name} {market_type_name}".lower()
    return "double chance" in text


def _is_game_total_market(market_name: str, market_type_name: str, sport: str) -> bool:
    market_key = " ".join(str(market_name or "").strip().lower().split())
    type_key = str(market_type_name or "").strip().upper()
    if sport == "soccer":
        return (
            type_key.startswith("OVER_UNDER_")
            or type_key.startswith("1ST_HALF_OVER/UNDER")
            or ("over/under" in market_key and "team" not in market_key)
        )
    if sport == "mlb":
        return (
            ("total" in market_key and "player" not in market_key)
            or (("inning" in market_key or "innings" in market_key) and "run" in market_key)
        )
    return "total" in market_key and "player" not in market_key


def _payload_attachments(payload: dict[str, Any]) -> dict[str, Any]:
    attachments = payload.get("attachments") or payload.get("marketAttachmentsData") or {}
    if not isinstance(attachments, dict):
        return {}
    return attachments


def parse_payload(payload: dict[str, Any], sport: str) -> list[dict[str, Any]]:
    attachments = _payload_attachments(payload)
    events = attachments.get("events") or {}
    markets = attachments.get("markets") or attachments.get("sportsBookMarkets") or {}
    runners = attachments.get("runners") or {}

    rows: list[dict[str, Any]] = []
    for market_id, market in markets.items():
        if str(market.get("marketStatus") or "").upper() not in {"", "OPEN"}:
            continue
        event_id = str(market.get("eventId") or "")
        event = events.get(event_id, {})
        runner_ids = market.get("runnerIds") or market.get("runners") or []
        if runner_ids and all(isinstance(runner, dict) for runner in runner_ids):
            runner_rows = list(runner_ids)
        else:
            runner_rows = [runners.get(str(runner_id)) for runner_id in runner_ids]
            runner_rows = [runner for runner in runner_rows if isinstance(runner, dict)]
        if len(runner_rows) < 2:
            continue

        market_name = str(market.get("marketName") or market.get("name") or "").strip()
        market_type_name = str(market.get("marketType") or "").strip()
        labels = {str(runner.get("runnerName") or "").strip().lower() for runner in runner_rows}
        home_team, away_team = _event_teams(event)
        updated_at = market.get("lastModified") or event.get("openDate") or market.get("marketTime") or ""
        event_date = event.get("openDate") or market.get("marketTime") or ""

        ladder = _ladder_market(market_name)
        if ladder is not None and " @ " in str(event.get("name") or ""):
            stat_key, line = ladder
            for runner in runner_rows:
                over_odds = _runner_odds(runner)
                player_name = str(runner.get("runnerName") or "").strip()
                if over_odds is None or not player_name:
                    continue
                rows.append(
                    {
                        "provider": "fanduel",
                        "provider_event_id": event_id,
                        "provider_market_id": f"{market_id}:{runner.get('selectionId') or player_name}",
                        "provider_league": sport,
                        "provider_market_name": market_name,
                        "book": "fanduel",
                        "sport": sport,
                        "market_type": "player_over_under",
                        "stat": stat_key,
                        "player_name": player_name,
                        "line": line,
                        "home_team": home_team,
                        "away_team": away_team,
                        "over_odds": over_odds,
                        "under_odds": _synthetic_under_odds(over_odds),
                        "updated_at": updated_at,
                        "period": _infer_period_code(market_name, market_type_name),
                        "event_date": event_date,
                        "question": f"{player_name} {market_name}",
                    }
                )
            continue

        if market_name.lower() in {"first basket", "next basket"} and " @ " in str(event.get("name") or ""):
            for runner in runner_rows:
                over_odds = _runner_odds(runner)
                player_name = str(runner.get("runnerName") or "").strip()
                if over_odds is None or not player_name:
                    continue
                rows.append(
                    {
                        "provider": "fanduel",
                        "provider_event_id": event_id,
                        "provider_market_id": f"{market_id}:{runner.get('selectionId') or player_name}",
                        "provider_league": sport,
                        "provider_market_name": market_name,
                        "book": "fanduel",
                        "sport": sport,
                        "market_type": "first_basket",
                        "stat": "firstbasket",
                        "player_name": player_name,
                        "line": "",
                        "home_team": home_team,
                        "away_team": away_team,
                        "over_odds": over_odds,
                        "under_odds": "",
                        "updated_at": updated_at,
                        "period": _infer_period_code(market_name, market_type_name),
                        "event_date": event_date,
                        "question": f"{player_name} {market_name}",
                    }
                )
            continue

        if _is_both_teams_score_market(market_name, market_type_name):
            if not home_team or not away_team:
                continue
            yes_runner, no_runner = _find_yes_no_runners(runner_rows)
            if not yes_runner or not no_runner:
                continue
            yes_odds = _runner_odds(yes_runner)
            no_odds = _runner_odds(no_runner)
            if yes_odds is None or no_odds is None:
                continue
            rows.append(
                {
                    "provider": "fanduel",
                    "provider_event_id": event_id,
                    "provider_market_id": market_id,
                    "provider_league": sport,
                    "provider_market_name": market_name,
                    "book": "fanduel",
                    "sport": sport,
                    "market_type": "both_teams_score",
                    "stat": "bothteamsscore",
                    "player_name": "",
                    "line": "",
                    "home_team": home_team,
                    "away_team": away_team,
                    "over_odds": yes_odds,
                    "under_odds": no_odds,
                    "extra_outcomes": _runner_outcomes_json(
                        [yes_runner, no_runner],
                        home_team=home_team,
                        away_team=away_team,
                    ),
                    "updated_at": updated_at,
                    "period": _infer_period_code(market_name, market_type_name),
                    "event_date": event_date,
                    "question": market_name,
                }
            )
            continue

        if labels == {"yes", "no"} and _is_yes_no_first_inning_runs_market(market_name, market_type_name):
            yes_runner, no_runner = _find_yes_no_runners(runner_rows)
            if not yes_runner or not no_runner:
                continue
            yes_odds = _runner_odds(yes_runner)
            no_odds = _runner_odds(no_runner)
            if yes_odds is None or no_odds is None:
                continue
            rows.append(
                {
                    "provider": "fanduel",
                    "provider_event_id": event_id,
                    "provider_market_id": market_id,
                    "provider_league": sport,
                    "provider_market_name": market_name,
                    "book": "fanduel",
                    "sport": sport,
                    "market_type": "game_total",
                    "stat": "total",
                    "player_name": "",
                    "line": 0.5,
                    "home_team": home_team,
                    "away_team": away_team,
                    "over_odds": yes_odds,
                    "under_odds": no_odds,
                    "updated_at": updated_at,
                    "period": _infer_period_code(market_name, market_type_name),
                    "event_date": event_date,
                    "question": market_name,
                }
            )
            continue

        if _is_double_chance_market(market_name, market_type_name):
            if not home_team or not away_team:
                continue
            priced = [runner for runner in runner_rows if _runner_odds(runner) is not None]
            if len(priced) < 2:
                continue
            rows.append(
                {
                    "provider": "fanduel",
                    "provider_event_id": event_id,
                    "provider_market_id": market_id,
                    "provider_league": sport,
                    "provider_market_name": market_name,
                    "book": "fanduel",
                    "sport": sport,
                    "market_type": "double_chance",
                    "stat": "doublechance",
                    "player_name": "",
                    "line": "",
                    "home_team": home_team,
                    "away_team": away_team,
                    "over_odds": _runner_odds(priced[0]),
                    "under_odds": _runner_odds(priced[1]),
                    "extra_outcomes": _runner_outcomes_json(
                        priced,
                        home_team=home_team,
                        away_team=away_team,
                    ),
                    "updated_at": updated_at,
                    "period": _infer_period_code(market_name, market_type_name),
                    "event_date": event_date,
                    "question": market_name,
                }
            )
            continue

        over_runner, under_runner = _find_over_under_runners(runner_rows)
        if labels == {"over", "under"} or (over_runner is not None and under_runner is not None):
            player_name = ""
            if _is_game_total_market(market_name, market_type_name, sport):
                market_type = "game_total"
            else:
                market_type = "player_over_under"
                player_name = str(market.get("playerName") or market.get("marketTitle") or "").strip()

            over = over_runner or runner_rows[0]
            under = under_runner or runner_rows[1]
            over_label = str(over.get("runnerName") or "").strip()
            if _is_under_label(over_label):
                over, under = under, over

            over_odds = _runner_odds(over)
            under_odds = _runner_odds(under)
            if over_odds is None or under_odds is None:
                continue

            line = _parse_line(over.get("handicap") or under.get("handicap") or market.get("handicap"))
            if (line is None or line == 0.0) and market_type == "game_total":
                line = _extract_total_line(market_name, market_type_name, runner_rows)
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
                    "stat": "total" if market_type == "game_total" else _normalize_stat(market_name),
                    "player_name": player_name,
                    "line": line if line is not None else "",
                    "home_team": home_team,
                    "away_team": away_team,
                    "over_odds": over_odds,
                    "under_odds": under_odds,
                    "updated_at": updated_at,
                    "period": _infer_period_code(market_name, market_type_name),
                    "event_date": event_date,
                    "question": market_name,
                }
            )
            continue

        if len(runner_rows) >= 2 and _is_moneyline_market(market_name, market_type_name):
            is_first_five = _is_first_five_market(market_name, market_type_name)
            is_halftime = _is_halftime_market(market_name, market_type_name) and not is_first_five
            home_runner = _find_team_runner(runner_rows, home_team)
            away_runner = _find_team_runner(runner_rows, away_team)
            draw_runner = _find_draw_runner(runner_rows)
            if not home_runner or not away_runner:
                continue
            home_odds = _runner_odds(home_runner)
            away_odds = _runner_odds(away_runner)
            if home_odds is None or away_odds is None:
                continue
            draw_odds = _runner_odds(draw_runner) if draw_runner else None
            moneyline_runners = [home_runner]
            if draw_runner:
                moneyline_runners.append(draw_runner)
            moneyline_runners.append(away_runner)

            rows.append(
                {
                    "provider": "fanduel",
                    "provider_event_id": event_id,
                    "provider_market_id": market_id,
                    "provider_league": sport,
                    "provider_market_name": market_name,
                    "book": "fanduel",
                    "sport": sport,
                    "market_type": "halftime_result"
                    if is_halftime
                    else "game_winner",
                    "stat": "winner",
                    "player_name": "",
                    "line": "",
                    "home_team": home_team,
                    "away_team": away_team,
                    "over_odds": home_odds,
                    "under_odds": away_odds,
                    "draw_odds": draw_odds if draw_odds is not None else "",
                    "extra_outcomes": _runner_outcomes_json(
                        moneyline_runners,
                        home_team=home_team,
                        away_team=away_team,
                    ),
                    "updated_at": updated_at,
                    "period": _infer_period_code(market_name, market_type_name),
                    "event_date": event_date,
                    "question": event.get("name") or market_name,
                }
            )
            continue

        if len(runner_rows) == 2 and (
            "run line" in market_name.lower()
            or "spread" in market_name.lower()
            or "handicap" in market_type_name.lower()
        ):
            home_runner = _find_team_runner(runner_rows, home_team)
            away_runner = _find_team_runner(runner_rows, away_team)
            if not home_runner or not away_runner:
                continue
            home_odds = _runner_odds(home_runner)
            away_odds = _runner_odds(away_runner)
            home_spread = _parse_line(home_runner.get("handicap"))
            away_spread = _parse_line(away_runner.get("handicap"))
            line = abs(home_spread or away_spread or 0.0)
            if home_odds is None or away_odds is None:
                continue
            rows.append(
                {
                    "provider": "fanduel",
                    "provider_event_id": event_id,
                    "provider_market_id": market_id,
                    "provider_league": sport,
                    "provider_market_name": market_name,
                    "book": "fanduel",
                    "sport": sport,
                    "market_type": "game_spread",
                    "stat": "spread",
                    "player_name": "",
                    "line": line,
                    "home_spread": home_spread if home_spread is not None else "",
                    "away_spread": away_spread if away_spread is not None else "",
                    "home_team": home_team,
                    "away_team": away_team,
                    "over_odds": home_odds,
                    "under_odds": away_odds,
                    "updated_at": updated_at,
                    "period": _infer_period_code(market_name, market_type_name),
                    "event_date": event_date,
                    "question": market_name,
                }
            )
    return rows


def _fanduel_headers(
    headers: dict[str, str] | None = None,
    *,
    include_app_version: bool = False,
) -> dict[str, str]:
    effective = {
        "accept": "application/json",
        "origin": "https://sportsbook.fanduel.com",
        "referer": "https://sportsbook.fanduel.com/soccer",
        "x-application": FANDUEL_API_KEY,
        "x-sportsbook-region": FANDUEL_REGION,
    }
    if include_app_version:
        effective["x-app-version"] = FANDUEL_APP_VERSION
    effective.update(headers or {})
    return effective


def _url_with_query(host: str, path: str, params: dict[str, Any]) -> str:
    return f"{host}{path}?{urlencode(params)}"


def _configured_ints(value: Any, default: Sequence[int]) -> list[int]:
    if value in (None, "", []):
        return list(default)
    if isinstance(value, str):
        parts = [part.strip() for part in value.split(",") if part.strip()]
    elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        parts = list(value)
    else:
        parts = [value]

    values: list[int] = []
    for part in parts:
        try:
            values.append(int(part))
        except Exception:
            continue
    return values or list(default)


def _configured_strings(value: Any, default: Sequence[str]) -> tuple[str, ...]:
    if value in (None, "", []):
        return tuple(default)
    if isinstance(value, str):
        parts = [part.strip().lower() for part in value.split(",") if part.strip()]
    elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        parts = [str(part or "").strip().lower() for part in value if str(part or "").strip()]
    else:
        parts = [str(value).strip().lower()]
    return tuple(parts) or tuple(default)


def _merge_payload_attachments(target: dict[str, dict[str, Any]], payload: dict[str, Any]) -> None:
    attachments = _payload_attachments(payload)
    for source_key, target_key in (
        ("eventTypes", "eventTypes"),
        ("competitions", "competitions"),
        ("events", "events"),
        ("markets", "markets"),
        ("sportsBookMarkets", "markets"),
    ):
        values = attachments.get(source_key) or {}
        if not isinstance(values, dict):
            continue
        bucket = target.setdefault(target_key, {})
        for key, value in values.items():
            if isinstance(value, dict):
                bucket[str(key)] = value


def _is_match_event(event: dict[str, Any]) -> bool:
    if event.get("homeTeamName") or event.get("awayTeamName") or event.get("competitors"):
        return True
    name = f" {event.get('name') or ''} ".lower()
    return " v " in name or " vs " in name or " @ " in name


def _competition_match_events(attachments: dict[str, dict[str, Any]], competition_id: int) -> list[dict[str, Any]]:
    events = attachments.get("events") or {}
    match_events = [
        event
        for event in events.values()
        if isinstance(event, dict)
        and str(event.get("competitionId") or "") == str(competition_id)
        and _is_match_event(event)
    ]
    return sorted(match_events, key=lambda event: str(event.get("openDate") or ""))


def _extract_tab_ids(tabs_payload: dict[str, Any], keywords: Sequence[str]) -> list[str]:
    tab_ids: list[str] = []
    for tab in tabs_payload.get("tabs") or []:
        if not isinstance(tab, dict):
            continue
        title = f"{tab.get('title') or ''} {tab.get('label') or ''}".strip().lower()
        if not title:
            continue
        if any(keyword in title for keyword in keywords):
            tab_id = str(tab.get("id") or "").strip()
            if tab_id and tab_id not in tab_ids:
                tab_ids.append(tab_id)
    return tab_ids


def _merge_market_prices(markets: dict[str, dict[str, Any]], price_rows: list[dict[str, Any]]) -> None:
    for price_row in price_rows:
        market_id = str(price_row.get("marketId") or "").strip()
        market = markets.get(market_id)
        if not market:
            continue
        if "marketStatus" in price_row:
            market["marketStatus"] = price_row["marketStatus"]
        if "inplay" in price_row:
            market["inPlay"] = price_row["inplay"]
        if "betDelay" in price_row:
            market["betDelay"] = price_row["betDelay"]
        if "bettingType" in price_row:
            market["bettingType"] = price_row["bettingType"]

        runner_details = {
            str(detail.get("selectionId")): detail
            for detail in price_row.get("runnerDetails") or []
            if isinstance(detail, dict) and detail.get("selectionId") is not None
        }
        for runner in market.get("runners") or []:
            if not isinstance(runner, dict):
                continue
            detail = runner_details.get(str(runner.get("selectionId")))
            if not detail:
                continue
            for key in ("winRunnerOdds", "previousWinRunnerOdds", "handicap", "runnerStatus"):
                if key in detail:
                    runner[key] = detail[key]


def _fetch_soccer_competition_page(
    competition_id: int,
    *,
    headers: dict[str, str],
    proxy_url: str | None,
    impersonate: str,
) -> dict[str, Any]:
    url = _url_with_query(
        FANDUEL_SBAPI_HOST,
        "/competition-page",
        {
            "competitionId": competition_id,
            "eventTypeId": FANDUEL_SOCCER_EVENT_TYPE_ID,
            "_ak": FANDUEL_API_KEY,
        },
    )
    return get_browser_like_json(
        url,
        headers=_fanduel_headers(headers),
        proxy_url=proxy_url,
        impersonate=impersonate,
    )


def _fetch_soccer_event_tabs(
    event: dict[str, Any],
    competition_id: int,
    *,
    headers: dict[str, str],
    proxy_url: str | None,
    impersonate: str,
) -> dict[str, Any]:
    event_id = int(event.get("eventId"))
    is_in_play = "true" if event.get("inPlay") is True else "false"
    url = _url_with_query(
        FANDUEL_GATEWAY_HOST,
        "/eventpage/tabs",
        {
            "eventId": event_id,
            "eventTypeId": int(event.get("eventTypeId") or FANDUEL_SOCCER_EVENT_TYPE_ID),
            "competitionId": competition_id,
            "isInPlay": is_in_play,
        },
    )
    return get_browser_like_json(
        url,
        headers=_fanduel_headers(headers, include_app_version=True),
        proxy_url=proxy_url,
        impersonate=impersonate,
    )


def _fetch_soccer_tab_details(
    event: dict[str, Any],
    competition_id: int,
    tab_id: str,
    *,
    headers: dict[str, str],
    proxy_url: str | None,
    impersonate: str,
) -> dict[str, Any]:
    event_id = int(event.get("eventId"))
    is_in_play = "true" if event.get("inPlay") is True else "false"
    url = _url_with_query(
        FANDUEL_GATEWAY_HOST,
        "/eventpage/tabDetails",
        {
            "eventId": event_id,
            "eventTypeId": int(event.get("eventTypeId") or FANDUEL_SOCCER_EVENT_TYPE_ID),
            "competitionId": competition_id,
            "isInPlay": is_in_play,
            "tabId": tab_id,
        },
    )
    return get_browser_like_json(
        url,
        headers=_fanduel_headers(headers, include_app_version=True),
        proxy_url=proxy_url,
        impersonate=impersonate,
    )


def _fetch_event_tabs(
    event: dict[str, Any],
    *,
    headers: dict[str, str],
    proxy_url: str | None,
    impersonate: str,
) -> dict[str, Any]:
    event_id = int(event.get("eventId"))
    competition_id = int(event.get("competitionId"))
    event_type_id = int(event.get("eventTypeId") or 0)
    is_in_play = "true" if event.get("inPlay") is True else "false"
    url = _url_with_query(
        FANDUEL_GATEWAY_HOST,
        "/eventpage/tabs",
        {
            "eventId": event_id,
            "eventTypeId": event_type_id,
            "competitionId": competition_id,
            "isInPlay": is_in_play,
        },
    )
    return get_browser_like_json(
        url,
        headers=_fanduel_headers(headers, include_app_version=True),
        proxy_url=proxy_url,
        impersonate=impersonate,
    )


def _fetch_event_tab_details(
    event: dict[str, Any],
    tab_id: str,
    *,
    headers: dict[str, str],
    proxy_url: str | None,
    impersonate: str,
) -> dict[str, Any]:
    event_id = int(event.get("eventId"))
    competition_id = int(event.get("competitionId"))
    event_type_id = int(event.get("eventTypeId") or 0)
    is_in_play = "true" if event.get("inPlay") is True else "false"
    url = _url_with_query(
        FANDUEL_GATEWAY_HOST,
        "/eventpage/tabDetails",
        {
            "eventId": event_id,
            "eventTypeId": event_type_id,
            "competitionId": competition_id,
            "isInPlay": is_in_play,
            "tabId": tab_id,
        },
    )
    return get_browser_like_json(
        url,
        headers=_fanduel_headers(headers, include_app_version=True),
        proxy_url=proxy_url,
        impersonate=impersonate,
    )


def _fetch_market_prices(
    market_ids: list[str],
    *,
    headers: dict[str, str],
    proxy_url: str | None,
    impersonate: str,
) -> list[dict[str, Any]]:
    price_rows: list[dict[str, Any]] = []
    url = f"{FANDUEL_SMP_HOST}/api/sports/fixedodds/readonly/v1/getMarketPrices?priceHistory=0"
    for offset in range(0, len(market_ids), FANDUEL_PRICE_BATCH_SIZE):
        batch = market_ids[offset : offset + FANDUEL_PRICE_BATCH_SIZE]
        payload = post_browser_like_json(
            url,
            {"marketIds": batch},
            headers=_fanduel_headers(headers),
            proxy_url=proxy_url,
            impersonate=impersonate,
        )
        if isinstance(payload, list):
            price_rows.extend(row for row in payload if isinstance(row, dict))
    return price_rows


def _ensure_canonical_market_bucket(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    attachments = _payload_attachments(payload)
    markets = attachments.get("markets")
    if isinstance(markets, dict):
        return markets
    sportsbook_markets = attachments.get("sportsBookMarkets") or {}
    canonical = dict(sportsbook_markets) if isinstance(sportsbook_markets, dict) else {}
    attachments["markets"] = canonical
    return canonical


def _enrich_payload_with_event_tabs(
    payload: dict[str, Any],
    *,
    sport: str,
    headers: dict[str, str],
    proxy_url: str | None,
    impersonate: str,
) -> None:
    tab_keywords = FANDUEL_EVENT_TAB_KEYWORDS_BY_SPORT.get(sport)
    if not tab_keywords:
        return
    attachments = _payload_attachments(payload)
    events = attachments.get("events") or {}
    if not isinstance(events, dict) or not events:
        return
    markets = _ensure_canonical_market_bucket(payload)
    existing_market_ids = set(markets.keys())
    for event in events.values():
        if not isinstance(event, dict) or not _is_match_event(event):
            continue
        competition_id = event.get("competitionId")
        event_type_id = event.get("eventTypeId")
        event_id = event.get("eventId")
        if not competition_id or not event_type_id or not event_id:
            continue
        try:
            tabs_payload = _fetch_event_tabs(
                event,
                headers=headers,
                proxy_url=proxy_url,
                impersonate=impersonate,
            )
        except Exception:
            continue
        for tab_id in _extract_tab_ids(tabs_payload, tab_keywords):
            try:
                tab_payload = _fetch_event_tab_details(
                    event,
                    tab_id,
                    headers=headers,
                    proxy_url=proxy_url,
                    impersonate=impersonate,
                )
            except Exception:
                continue
            _merge_payload_attachments(attachments, tab_payload)
    markets = _ensure_canonical_market_bucket(payload)
    new_market_ids = sorted(set(markets.keys()) - existing_market_ids)
    if not new_market_ids:
        return
    price_rows = _fetch_market_prices(
        new_market_ids,
        headers=headers,
        proxy_url=proxy_url,
        impersonate=impersonate,
    )
    _merge_market_prices(markets, price_rows)


def _fetch_live_soccer_payload(
    sport_config: dict[str, Any],
    *,
    headers: dict[str, str],
    proxy_url: str | None,
    impersonate: str,
) -> dict[str, Any]:
    competition_ids = _configured_ints(
        sport_config.get("competition_ids"),
        FANDUEL_SOCCER_COMPETITION_IDS,
    )
    tab_keywords = _configured_strings(
        sport_config.get("tab_title_keywords") or sport_config.get("tab_keywords"),
        FANDUEL_SOCCER_TAB_KEYWORDS,
    )
    try:
        event_limit = int(sport_config.get("event_limit") or 12)
    except Exception:
        event_limit = 12

    attachments: dict[str, dict[str, Any]] = {
        "eventTypes": {},
        "competitions": {},
        "events": {},
        "markets": {},
    }
    raw: dict[str, Any] = {
        "competition_pages": {},
        "event_tabs": {},
        "event_tab_details": {},
        "prices": [],
    }

    for competition_id in competition_ids:
        competition_payload = _fetch_soccer_competition_page(
            competition_id,
            headers=headers,
            proxy_url=proxy_url,
            impersonate=impersonate,
        )
        raw["competition_pages"][str(competition_id)] = competition_payload
        _merge_payload_attachments(attachments, competition_payload)

        for event in _competition_match_events(attachments, competition_id)[:event_limit]:
            event_id = str(event.get("eventId") or "")
            if not event_id:
                continue
            tabs_payload = _fetch_soccer_event_tabs(
                event,
                competition_id,
                headers=headers,
                proxy_url=proxy_url,
                impersonate=impersonate,
            )
            raw["event_tabs"][event_id] = tabs_payload

            for tab_id in _extract_tab_ids(tabs_payload, tab_keywords):
                try:
                    tab_payload = _fetch_soccer_tab_details(
                        event,
                        competition_id,
                        tab_id,
                        headers=headers,
                        proxy_url=proxy_url,
                        impersonate=impersonate,
                    )
                except Exception:
                    continue
                raw["event_tab_details"][f"{event_id}:{tab_id}"] = tab_payload
                _merge_payload_attachments(attachments, tab_payload)

    market_ids = sorted(attachments.get("markets") or {})
    if market_ids:
        price_rows = _fetch_market_prices(
            market_ids,
            headers=headers,
            proxy_url=proxy_url,
            impersonate=impersonate,
        )
        raw["prices"] = price_rows
        _merge_market_prices(attachments["markets"], price_rows)

    return {
        "source": "fanduel_live_soccer",
        "competition_ids": competition_ids,
        "tab_keywords": list(tab_keywords),
        "attachments": attachments,
        "raw": raw,
    }


def _default_urls(sport: str) -> list[str]:
    page_sport = SPORT_TO_PAGE.get(sport)
    host = SPORT_TO_HOST.get(sport)
    if not page_sport or not host:
        return []
    common_params = urlencode(DEFAULT_QUERY_PARAMS)
    return [
        f"{host}/api/content-managed-page?{common_params}&page=CUSTOM&customPageId={sport}",
        f"{host}/api/content-managed-page?{common_params}&page=SPORT&sport={page_sport}",
    ]


def _request_specs_from_config(sport_config: dict[str, Any]) -> list[dict[str, Any]]:
    configured = sport_config.get("requests")
    if isinstance(configured, list):
        specs = [item for item in configured if isinstance(item, dict)]
        if specs:
            return specs

    specs: list[dict[str, Any]] = []
    for url in sport_config.get("urls") or []:
        specs.append({"method": "GET", "url": url})

    url = str(sport_config.get("url") or "").strip()
    if url:
        specs.append(
            {
                "method": str(sport_config.get("method") or "GET").upper(),
                "url": url,
                "payload": sport_config.get("payload"),
            }
        )
    return specs


def fetch_rows(
    sports: Sequence[str],
    *,
    save_payloads: bool = True,
    use_saved_payloads: bool = True,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    request_config = load_request_config("fanduel")
    all_rows: list[dict[str, Any]] = []
    raw_payloads: dict[str, Any] = {}

    for sport in _normalize_sports(sports):
        payload = load_saved_payload("fanduel", sport) if use_saved_payloads else None
        if payload is None:
            headers = dict(request_config.get("headers") or {})
            sport_config = (request_config.get("sports") or {}).get(sport) or {}
            request_specs = _request_specs_from_config(sport_config)
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
            last_error = None
            if not request_specs:
                if sport == "soccer":
                    try:
                        payload = _fetch_live_soccer_payload(
                            sport_config,
                            headers=headers,
                            proxy_url=proxy_url,
                            impersonate=impersonate,
                        )
                        if save_payloads:
                            save_payload("fanduel", sport, payload)
                    except Exception as exc:
                        last_error = exc
                if payload is None:
                    request_specs.extend({"method": "GET", "url": url} for url in _default_urls(sport))
            for spec in request_specs:
                url = str(spec.get("url") or "").strip()
                if not url:
                    continue
                method = str(spec.get("method") or "GET").upper()
                try:
                    if method == "POST":
                        payload = post_browser_like_json(
                            url,
                            spec.get("payload") or {},
                            headers=headers,
                            proxy_url=proxy_url,
                            impersonate=impersonate,
                        )
                    else:
                        payload = get_browser_like_json(
                            url,
                            headers=headers,
                            proxy_url=proxy_url,
                            impersonate=impersonate,
                        )
                    if isinstance(payload, dict):
                        _enrich_payload_with_event_tabs(
                            payload,
                            sport=sport,
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
