from __future__ import annotations

import json
import re
from urllib.parse import urlencode
from typing import Any, Sequence

from fair_odds import american_to_implied_prob, probability_to_american
from poll_market_matcher import normalize_team
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
    "wnba": "BASKETBALL",
    "nhl": "ICE_HOCKEY",
    "nfl": "AMERICAN_FOOTBALL",
    "soccer": "FOOTBALL",
}

SPORT_TO_HOST = {
    "mlb": "https://sbapi.nj.sportsbook.fanduel.com",
    "nba": "https://sbapi.nj.sportsbook.fanduel.com",
    "wnba": "https://sbapi.nj.sportsbook.fanduel.com",
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
FANDUEL_SOCCER_COMPETITION_IDS = [
    10932509,  # English Premier League
    228,  # UEFA Champions League
    117,  # Spanish La Liga
    81,  # Italian Serie A
    55,  # French Ligue 1
    59,  # German Bundesliga
    61,  # German Bundesliga 2
    141,  # US MLS
]
FANDUEL_SOCCER_TAB_KEYWORDS = ("popular", "goal scorer", "goals", "half", "assist", "save")
FANDUEL_SOCCER_GAME_LINE_TAB_KEYWORDS = ("popular", "goals", "half")
FANDUEL_PRICE_BATCH_SIZE = 70
FANDUEL_EVENT_TAB_KEYWORDS_BY_SPORT = {
    "mlb": (
        "innings",
        "quick bets",
        "live",
        "player props",
        "batter props",
        "pitcher props",
        "live player props",
        "live batter props",
        "live pitcher props",
    ),
    "nba": (
        "sgp",
        "quick bets",
        "live",
        "1st quarter",
        "quarter",
        "player points",
        "player threes",
        "player rebounds",
        "player assists",
        "player combos",
        "player defense",
        "player props",
    ),
    "wnba": (
        "sgp",
        "popular",
        "quick bets",
        "live",
        "player props",
        "player points",
        "player threes",
        "player rebounds",
        "player assists",
        "player combos",
        "player defense",
        "points",
        "threes",
        "rebounds",
        "assists",
    ),
    "nhl": (
        "quick bets",
        "live",
        "live player props",
        "player props",
        "period",
        "3rd period",
    ),
    "nfl": ("quick bets", "live", "player props"),
}
FANDUEL_GAME_LINE_EVENT_TAB_KEYWORDS_BY_SPORT = {
    "mlb": ("innings", "quick bets", "live"),
    "nba": ("quick bets", "live", "1st quarter", "quarter"),
    "wnba": ("quick bets", "live", "1st quarter", "quarter"),
    "nhl": ("quick bets", "live", "period", "3rd period"),
    "nfl": ("quick bets", "live"),
}

GAME_LINE_MARKET_TYPES = {
    "both_teams_score",
    "double_chance",
    "game_spread",
    "game_total",
    "game_winner",
    "halftime_result",
}

STAT_ALIASES = {
    "total points": "points",
    "points": "points",
    "alt points": "points",
    "rebounds": "rebounds",
    "alt rebounds": "rebounds",
    "assists": "assists",
    "alt assists": "assists",
    "made threes": "madethrees",
    "alt threes": "madethrees",
    "threes": "madethrees",
    "three pointers made": "madethrees",
    "total made 3 point field goals": "madethrees",
    "made 3 point field goals": "madethrees",
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


def _normalize_market_scope(value: str) -> str:
    return "game-lines" if str(value or "").strip().lower() == "game-lines" else "all"


def _is_game_lines_scope(market_scope: str) -> bool:
    return _normalize_market_scope(market_scope) == "game-lines"


def _filter_rows_by_market_scope(rows: list[dict[str, Any]], market_scope: str) -> list[dict[str, Any]]:
    if not _is_game_lines_scope(market_scope):
        return rows
    return [
        row
        for row in rows
        if str(row.get("market_type") or "").strip().lower() in GAME_LINE_MARKET_TYPES
    ]


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


def _normalized_team_pair(home_team: str, away_team: str) -> tuple[str, str]:
    normalized_home = normalize_team(home_team)
    normalized_away = normalize_team(away_team)
    if not normalized_home or not normalized_away:
        return "", ""
    teams = sorted((normalized_home, normalized_away))
    return teams[0], teams[1]


def _event_team_pair(event: dict[str, Any]) -> tuple[str, str]:
    home_team, away_team = _event_teams(event)
    return _normalized_team_pair(home_team, away_team)


def _normalized_target_team_pairs_by_sport(
    target_team_pairs_by_sport: dict[str, set[tuple[str, str]] | list[tuple[str, str]]] | None,
) -> dict[str, set[tuple[str, str]]]:
    normalized: dict[str, set[tuple[str, str]]] = {}
    if not isinstance(target_team_pairs_by_sport, dict):
        return normalized
    for sport_key, pairs in target_team_pairs_by_sport.items():
        sport = str(sport_key or "").strip().lower()
        if not sport or not isinstance(pairs, (set, list, tuple)):
            continue
        normalized_pairs: set[tuple[str, str]] = set()
        for pair in pairs:
            if not isinstance(pair, (list, tuple)) or len(pair) != 2:
                continue
            normalized_pair = _normalized_team_pair(str(pair[0] or ""), str(pair[1] or ""))
            if normalized_pair != ("", ""):
                normalized_pairs.add(normalized_pair)
        if normalized_pairs:
            normalized[sport] = normalized_pairs
    return normalized


def _match_events_for_target_games(
    events: list[dict[str, Any]],
    target_team_pairs: set[tuple[str, str]] | None,
    *,
    fallback_to_all: bool = True,
) -> list[dict[str, Any]]:
    if not target_team_pairs:
        return events
    matched_events = [event for event in events if _event_team_pair(event) in target_team_pairs]
    if matched_events:
        return matched_events
    return events if fallback_to_all else []


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
    return bool(re.search(r"(?:^|\b)(?:1st\s+half\s+)?over\b", str(value or "").strip(), flags=re.IGNORECASE))


def _is_under_label(value: str) -> bool:
    return bool(re.search(r"(?:^|\b)(?:1st\s+half\s+)?under\b", str(value or "").strip(), flags=re.IGNORECASE))


def _player_name_from_market_or_runners(
    market_name: str,
    over_runner: dict[str, Any],
    under_runner: dict[str, Any],
) -> str:
    for runner in (over_runner, under_runner):
        label = str(runner.get("runnerName") or "").strip()
        lowered = label.lower()
        for suffix in (" over", " under"):
            if lowered.endswith(suffix):
                return label[: -len(suffix)].strip()
    if " - " in market_name:
        return market_name.split(" - ", 1)[0].strip()
    return ""


def _player_stat_from_market_name(market_name: str, player_name: str = "") -> str:
    text = str(market_name or "").strip()
    if " - " in text:
        text = text.split(" - ", 1)[1].strip()
    if player_name:
        lowered = text.lower()
        player_lower = player_name.strip().lower()
        if player_lower and lowered.startswith(player_lower):
            text = text[len(player_name):].strip(" -:")
    return _normalize_stat(text)


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
        match = re.search(
            r"\b(?:over|under)\s+\(?([0-9]+(?:\.[0-9]+)?)\)?",
            label,
            flags=re.IGNORECASE,
        )
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


def _runner_total_side_line(runner: dict[str, Any]) -> tuple[str, float] | None:
    label = _runner_label(runner)
    match = re.search(
        r"^\s*(over|under)\s+\(?([0-9]+(?:\.[0-9]+)?)\)?\s*$",
        label,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    return match.group(1).lower(), float(match.group(2))


def _alternate_total_runner_pairs(
    runners: list[dict[str, Any]],
) -> list[tuple[float, dict[str, Any], dict[str, Any]]]:
    grouped: dict[float, dict[str, dict[str, Any]]] = {}
    for runner in runners:
        if str(runner.get("runnerStatus") or "").upper() in {"REMOVED", "SUSPENDED"}:
            continue
        side_line = _runner_total_side_line(runner)
        if side_line is None:
            continue
        side, line = side_line
        grouped.setdefault(line, {})[side] = runner

    pairs: list[tuple[float, dict[str, Any], dict[str, Any]]] = []
    for line in sorted(grouped):
        over_runner = grouped[line].get("over")
        under_runner = grouped[line].get("under")
        if over_runner is not None and under_runner is not None:
            pairs.append((line, over_runner, under_runner))
    return pairs


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


def _line_from_plus_threshold(value: str) -> float | None:
    try:
        return float(value) - 0.5
    except Exception:
        return None


def _single_side_player_market(market_name: str, market_type_name: str) -> tuple[str, float] | None:
    market_text = " ".join(str(market_name or "").strip().lower().split())
    market_type_key = str(market_type_name or "").strip().upper()

    if not market_text and not market_type_key:
        return None
    if (
        "series" in market_text
        or "all-in" in market_text
        or "including extra time" in market_text
        or "INCLUDING_EXTRA_TIME" in market_type_key
        or "1st quarter" in market_text
        or "1st qtr" in market_text
        or "first 3 minutes" in market_text
        or "first three minutes" in market_text
        or "1ST_QUARTER" in market_type_key
    ):
        return None

    if (
        market_type_key == "ANY_TIME_GOAL_SCORER"
        or "any time goal scorer" in market_text
        or "anytime goalscorer" in market_text
    ):
        return "goals", 0.5
    if (
        market_type_key in {"ANYTIME_ASSIST", "PLAYER_TO_RECORD_AN_ASSIST"}
        or "anytime assist" in market_text
        or "any time assist" in market_text
        or "to record an assist" in market_text
    ):
        return "assists", 0.5

    type_patterns = (
        (r"PLAYER_TO_SCORE_([0-9]+)\+_GOALS", "goals"),
        (r"PLAYER_TO_RECORD_([0-9]+)\+_GOALS", "goals"),
        (r"PLAYER_TO_RECORD_([0-9]+)\+_POINTS", "points"),
        (r"PLAYER_TO_RECORD_([0-9]+)\+_ASSISTS", "assists"),
        (r"PLAYER_TO_RECORD_([0-9]+)\+_REBOUNDS", "rebounds"),
        (r"PLAYER_TO_RECORD_([0-9]+)\+_HITS\+RUNS\+RBIS", "hitsrunsrbis"),
        (r"PLAYER_TO_RECORD_([0-9]+)\+_HITS_RUNS_RBIS", "hitsrunsrbis"),
        (r"PLAYER_TO_RECORD_([0-9]+)\+_SHOTS_ON_GOAL", "shots"),
        (r"TO_SCORE_([0-9]+)\+_POINTS", "points"),
        (r"TO_RECORD_([0-9]+)\+_ASSISTS", "assists"),
        (r"TO_RECORD_([0-9]+)\+_REBOUNDS", "rebounds"),
        (r"([0-9]+)\+_MADE_THREES", "madethrees"),
    )
    for pattern, stat_key in type_patterns:
        match = re.search(pattern, market_type_key)
        if not match:
            continue
        line = _line_from_plus_threshold(match.group(1))
        if line is not None:
            return stat_key, line

    market_patterns = (
        (r"(?:^|\b)(?:60\s*min\s+)?player\s+to\s+score\s+([0-9]+)\+\s+goals?\b", "goals"),
        (r"(?:^|\b)(?:60\s*min\s+)?player\s+to\s+record\s+([0-9]+)\+\s+goals?\b", "goals"),
        (r"(?:^|\b)(?:60\s*min\s+)?to\s+score\s+([0-9]+)\+\s+points?\b", "points"),
        (r"(?:^|\b)(?:60\s*min\s+)?player\s+to\s+record\s+([0-9]+)\+\s+points?\b", "points"),
        (r"(?:^|\b)(?:60\s*min\s+)?to\s+record\s+([0-9]+)\+\s+assists?\b", "assists"),
        (r"(?:^|\b)(?:60\s*min\s+)?player\s+to\s+record\s+([0-9]+)\+\s+assists?\b", "assists"),
        (r"(?:^|\b)(?:60\s*min\s+)?to\s+record\s+([0-9]+)\+\s+rebounds?\b", "rebounds"),
        (r"(?:^|\b)(?:60\s*min\s+)?player\s+to\s+record\s+([0-9]+)\+\s+rebounds?\b", "rebounds"),
        (
            r"(?:^|\b)(?:60\s*min\s+)?player\s+to\s+record\s+([0-9]+)\+\s+hits\s*\+\s*runs\s*\+\s*rbis?\b",
            "hitsrunsrbis",
        ),
        (
            r"(?:^|\b)(?:60\s*min\s+)?to\s+record\s+([0-9]+)\+\s+hits\s*\+\s*runs\s*\+\s*rbis?\b",
            "hitsrunsrbis",
        ),
        (r"(?:^|\b)(?:60\s*min\s+)?player\s+to\s+record\s+([0-9]+)\+\s+shots?\s+on\s+goal\b", "shots"),
        (r"(?:^|\b)player\s+([0-9]+)\+\s+points?\b", "points"),
        (r"(?:^|\b)player\s+([0-9]+)\+\s+assists?\b", "assists"),
        (r"(?:^|\b)player\s+([0-9]+)\+\s+rebounds?\b", "rebounds"),
        (r"(?:^|\b)player\s+([0-9]+)\+\s+hits\s*\+\s*runs\s*\+\s*rbis?\b", "hitsrunsrbis"),
        (r"(?:^|\b)player\s+([0-9]+)\+\s+goals?\b", "goals"),
        (r"(?:^|\b)player\s+([0-9]+)\+\s+shots?\s+on\s+goal\b", "shots"),
        (r"(?:^|\b)([0-9]+)\+\s+made\s+threes?\b", "madethrees"),
        (r"(?:^|\b)([0-9]+)\+\s+threes?\b", "madethrees"),
    )
    for pattern, stat_key in market_patterns:
        match = re.search(pattern, market_text, flags=re.IGNORECASE)
        if not match:
            continue
        line = _line_from_plus_threshold(match.group(1))
        if line is not None:
            return stat_key, line

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
            "1st quarter moneyline",
            "2nd quarter moneyline",
            "3rd quarter moneyline",
            "4th quarter moneyline",
            "1st quarter winner",
            "2nd quarter winner",
            "3rd quarter winner",
            "4th quarter winner",
            "half time result",
            "half-time result",
            "halftime result",
        }
        or re.search(r"\b[123](?:st|nd|rd)\s+period\s+money\s+line\b", market_key) is not None
        or re.search(r"\b[1234](?:st|nd|rd|th)\s+quarter\s+money\s*line\b", market_key) is not None
        or re.search(r"\b[1234](?:st|nd|rd|th)\s+quarter\s+winner\b", market_key) is not None
        or type_key
        in {
            "MONEY_LINE",
            "WIN-DRAW-WIN",
            "MATCH_RESULT",
            "MATCH_BETTING",
            "1ST_HALF_MONEY_LINE",
            "1ST_QUARTER_MONEY_LINE",
            "2ND_QUARTER_MONEY_LINE",
            "3RD_QUARTER_MONEY_LINE",
            "4TH_QUARTER_MONEY_LINE",
            "1ST_QUARTER_WINNER",
            "2ND_QUARTER_WINNER",
            "3RD_QUARTER_WINNER",
            "4TH_QUARTER_WINNER",
            "HALF_TIME_RESULT",
            "HALF-TIME_RESULT",
            "HALFTIME_RESULT",
        }
        or re.fullmatch(r"[1234](?:ST|ND|RD|TH)_QUARTER_MONEY_LINE", type_key) is not None
        or re.fullmatch(r"[1234](?:ST|ND|RD|TH)_QUARTER_WINNER", type_key) is not None
        or re.fullmatch(r"[1234](?:ST|ND|RD|TH)_QUARTER_MATCH_BETTING(?:_.*)?", type_key) is not None
        or re.fullmatch(r"[123](?:ST|ND|RD)_PERIOD_MONEY_LINE", type_key) is not None
    )


def _is_spread_market(market_name: str, market_type_name: str) -> bool:
    text = f"{market_name} {market_type_name}".lower()
    return "run line" in text or "spread" in text or "handicap" in text


def _is_halftime_market(market_name: str, market_type_name: str) -> bool:
    text = f"{market_name} {market_type_name}".lower()
    return "half" in text and ("1st" in text or "first" in text or "time" in text)


def _is_first_five_market(market_name: str, market_type_name: str) -> bool:
    text = f"{market_name} {market_type_name}".lower()
    return "first 5" in text or "1st 5" in text or ("inning" in text and "1st_half" in text)


def _infer_period_code(market_name: str, market_type_name: str) -> str:
    text = f"{market_name} {market_type_name}".lower()
    if "first 3" in text or "1st 3" in text:
        return "F3"
    if "first 5" in text or "1st 5" in text:
        return "F5"
    if "first 7" in text or "1st 7" in text:
        return "F7"
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
    period_tokens = text.replace("-", " ").replace("/", " ").split()
    if "overtime" in period_tokens or "ot" in period_tokens:
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


def _player_name_from_alt_saves_market(market_name: str, market_type_name: str) -> str:
    market_text = " ".join(str(market_name or "").strip().split())
    type_key = str(market_type_name or "").strip().upper()
    if "ALT_SAVES" not in type_key and "alt saves" not in market_text.lower():
        return ""
    text = re.sub(
        r"^(?:1st|2nd|3rd)\s+period\s+",
        "",
        market_text,
        flags=re.IGNORECASE,
    )
    match = re.match(r"(.+?)\s+alt\s+saves$", text, flags=re.IGNORECASE)
    return match.group(1).strip() if match else ""


def _plus_threshold_line(value: Any) -> float | None:
    match = re.search(r"\b([0-9]+)\s*\+", str(value or ""))
    if not match:
        return None
    return _line_from_plus_threshold(match.group(1))


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

        if _is_first_basket_market(market) and " @ " in str(event.get("name") or ""):
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

        alt_saves_player = _player_name_from_alt_saves_market(market_name, market_type_name)
        if alt_saves_player and home_team and away_team:
            period = _infer_period_code(market_name, market_type_name)
            added_rows = False
            for runner in runner_rows:
                if str(runner.get("runnerStatus") or "").upper() in {"REMOVED", "SUSPENDED"}:
                    continue
                over_odds = _runner_odds(runner)
                line = _plus_threshold_line(runner.get("runnerName"))
                if over_odds is None or line is None:
                    continue
                added_rows = True
                rows.append(
                    {
                        "provider": "fanduel",
                        "provider_event_id": event_id,
                        "provider_market_id": f"{market_id}:{runner.get('selectionId') or runner.get('runnerName')}",
                        "provider_league": sport,
                        "provider_market_name": market_name,
                        "book": "fanduel",
                        "sport": sport,
                        "market_type": "player_over_under",
                        "stat": "saves",
                        "player_name": alt_saves_player,
                        "line": line,
                        "home_team": home_team,
                        "away_team": away_team,
                        "over_odds": over_odds,
                        "under_odds": _synthetic_under_odds(over_odds),
                        "updated_at": updated_at,
                        "period": period,
                        "event_date": event_date,
                        "question": f"{alt_saves_player} {market_name} {runner.get('runnerName') or ''}".strip(),
                    }
                )
            if added_rows:
                continue

        single_side_player_market = _single_side_player_market(market_name, market_type_name)
        if single_side_player_market is not None and home_team and away_team:
            stat_key, line = single_side_player_market
            added_rows = False
            for runner in runner_rows:
                over_odds = _runner_odds(runner)
                player_name = str(runner.get("runnerName") or "").strip()
                if over_odds is None or not player_name:
                    continue
                added_rows = True
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
            if added_rows:
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

        alternate_total_pairs = _alternate_total_runner_pairs(runner_rows)
        if (
            len(alternate_total_pairs) >= 2
            and _is_game_total_market(market_name, market_type_name, sport)
            and home_team
            and away_team
        ):
            for line, over_runner, under_runner in alternate_total_pairs:
                over_odds = _runner_odds(over_runner)
                under_odds = _runner_odds(under_runner)
                if over_odds is None or under_odds is None:
                    continue
                rows.append(
                    {
                        "provider": "fanduel",
                        "provider_event_id": event_id,
                        "provider_market_id": f"{market_id}:{line:g}",
                        "provider_league": sport,
                        "provider_market_name": market_name,
                        "book": "fanduel",
                        "sport": sport,
                        "market_type": "game_total",
                        "stat": "total",
                        "player_name": "",
                        "line": line,
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
            if market_type == "player_over_under" and not player_name:
                player_name = _player_name_from_market_or_runners(market_name, over, under)

            line = _parse_line(over.get("handicap") or under.get("handicap") or market.get("handicap"))
            if line is None or line == 0.0:
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
                    "stat": "total"
                    if market_type == "game_total"
                    else _player_stat_from_market_name(market_name, player_name),
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
    keyword_values = [str(keyword or "").strip().lower() for keyword in keywords]
    include_all = any(keyword in {"*", "all", "__all__"} for keyword in keyword_values)
    tab_ids: list[str] = []
    for tab in tabs_payload.get("tabs") or []:
        if not isinstance(tab, dict):
            continue
        title = " ".join(
            str(tab.get(field) or "").strip().lower()
            for field in ("title", "label", "name", "tabName", "slug", "seoTabSlug", "tabSlug")
            if str(tab.get(field) or "").strip()
        )
        tab_id = str(tab.get("id") or "").strip()
        if not tab_id:
            continue
        if include_all:
            if tab_id not in tab_ids:
                tab_ids.append(tab_id)
            continue
        if not title:
            continue
        if any(keyword and keyword in title for keyword in keyword_values):
            if tab_id not in tab_ids:
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


def _is_first_basket_market(market: dict[str, Any]) -> bool:
    market_name = str(market.get("marketName") or market.get("name") or "").strip().lower()
    market_type = str(market.get("marketType") or market.get("marketTypeName") or "").strip().upper()
    if "FIRST_TEAM_BASKET" in market_type or "first team basket" in market_name:
        return False
    return (
        market_name in {"first basket", "next basket", "first field goal", "player to score first field goal"}
        or market_type.startswith("FIRST_BASKET")
    )


def _inline_runner_rows(market: dict[str, Any]) -> list[dict[str, Any]]:
    runners = market.get("runners") or []
    if not isinstance(runners, list):
        return []
    return [runner for runner in runners if isinstance(runner, dict)]


def _market_needs_runner_prices(market: dict[str, Any], sport: str = "") -> bool:
    if str(market.get("marketStatus") or "").upper() not in {"", "OPEN"}:
        return False
    market_name = str(market.get("marketName") or market.get("name") or "").strip()
    market_type = str(market.get("marketType") or market.get("marketTypeName") or "").strip()
    needs_player_prices = (
        _ladder_market(market_name) is not None
        or _single_side_player_market(market_name, market_type) is not None
    )
    runner_rows = [
        runner
        for runner in _inline_runner_rows(market)
        if str(runner.get("runnerStatus") or "").upper() not in {"REMOVED", "SUSPENDED"}
    ]
    if not runner_rows or all(_runner_odds(runner) is not None for runner in runner_rows):
        return False
    if _is_first_basket_market(market) or needs_player_prices:
        return True
    return (
        _is_moneyline_market(market_name, market_type)
        or _is_spread_market(market_name, market_type)
        or _is_game_total_market(market_name, market_type, str(sport or "").strip().lower())
        or _is_double_chance_market(market_name, market_type)
        or _is_both_teams_score_market(market_name, market_type)
    )


def _market_ids_missing_runner_prices(
    markets: dict[str, dict[str, Any]],
    sport: str = "",
) -> list[str]:
    missing_ids: list[str] = []
    for market_id, market in markets.items():
        if isinstance(market, dict) and _market_needs_runner_prices(market, sport):
            missing_ids.append(str(market.get("marketId") or market_id))
    return missing_ids


def _saved_payload_needs_price_refresh(payload: Any, sport: str) -> bool:
    if not isinstance(payload, dict):
        return False
    attachments = _payload_attachments(payload)
    markets = attachments.get("markets") or attachments.get("sportsBookMarkets") or {}
    return isinstance(markets, dict) and bool(_market_ids_missing_runner_prices(markets, sport))


def _saved_payload_missing_core_nba_player_props(payload: Any, sport: str) -> bool:
    if sport not in {"nba", "wnba"} or not isinstance(payload, dict):
        return False
    attachments = _payload_attachments(payload)
    events = attachments.get("events") or {}
    markets = attachments.get("markets") or attachments.get("sportsBookMarkets") or {}
    if not isinstance(events, dict) or not any(
        isinstance(event, dict) and _is_match_event(event) for event in events.values()
    ):
        return False
    if not isinstance(markets, dict):
        return True
    found_stats: set[str] = set()
    for market in markets.values():
        if not isinstance(market, dict):
            continue
        market_name = str(market.get("marketName") or market.get("name") or "").strip()
        market_type = str(market.get("marketType") or market.get("marketTypeName") or "").strip()
        if "1st quarter" in market_name.lower() or "1st qtr" in market_name.lower():
            continue
        stat_key = _single_side_player_market(market_name, market_type)
        if stat_key:
            found_stats.add(stat_key[0])
            continue
        if len(_inline_runner_rows(market)) == 2:
            found_stats.add(_player_stat_from_market_name(market_name))
    return not {"points", "rebounds", "assists", "madethrees"}.issubset(found_stats)


def _saved_payload_missing_nba_quarter_game_lines(payload: Any, sport: str) -> bool:
    if sport not in {"nba", "wnba"} or not isinstance(payload, dict):
        return False
    attachments = _payload_attachments(payload)
    events = attachments.get("events") or {}
    markets = attachments.get("markets") or attachments.get("sportsBookMarkets") or {}
    if not isinstance(events, dict) or not any(
        isinstance(event, dict) and _is_match_event(event) for event in events.values()
    ):
        return False
    if not isinstance(markets, dict):
        return True
    for market in markets.values():
        if not isinstance(market, dict):
            continue
        market_name = str(market.get("marketName") or market.get("name") or "").strip()
        market_type = str(market.get("marketType") or market.get("marketTypeName") or "").strip()
        period = _infer_period_code(market_name, market_type)
        if period in {"1Q", "2Q", "3Q", "4Q"} and (
            _is_moneyline_market(market_name, market_type)
            or _is_spread_market(market_name, market_type)
            or _is_game_total_market(market_name, market_type, sport)
        ):
            return False
    return True


def _saved_payload_missing_wnba_first_basket(payload: Any, sport: str) -> bool:
    if sport != "wnba" or not isinstance(payload, dict):
        return False
    attachments = _payload_attachments(payload)
    events = attachments.get("events") or {}
    markets = attachments.get("markets") or attachments.get("sportsBookMarkets") or {}
    if not isinstance(events, dict) or not any(
        isinstance(event, dict) and _is_match_event(event) for event in events.values()
    ):
        return False
    if not isinstance(markets, dict):
        return True
    for market in markets.values():
        if not isinstance(market, dict):
            continue
        if _is_first_basket_market(market):
            return False
    return True


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
    market_scope: str = "all",
    target_team_pairs: set[tuple[str, str]] | None = None,
) -> None:
    market_scope = _normalize_market_scope(market_scope)
    tab_keywords = (
        FANDUEL_GAME_LINE_EVENT_TAB_KEYWORDS_BY_SPORT.get(sport)
        if _is_game_lines_scope(market_scope)
        else FANDUEL_EVENT_TAB_KEYWORDS_BY_SPORT.get(sport)
    )
    if not tab_keywords:
        return
    attachments = _payload_attachments(payload)
    events = attachments.get("events") or {}
    if not isinstance(events, dict) or not events:
        return
    markets = _ensure_canonical_market_bucket(payload)
    existing_market_ids = set(markets.keys())
    match_events = [
        event
        for event in events.values()
        if isinstance(event, dict) and _is_match_event(event)
    ]
    for event in _match_events_for_target_games(
        match_events,
        target_team_pairs,
        fallback_to_all=not bool(target_team_pairs),
    ):
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
        tab_ids = _extract_tab_ids(tabs_payload, tab_keywords)
        if not tab_ids and event.get("inPlay") is True:
            tab_ids = _extract_tab_ids(tabs_payload, ("__all__",))
        for tab_id in tab_ids:
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
    missing_price_market_ids = set(_market_ids_missing_runner_prices(markets, sport))
    market_ids_requiring_prices = sorted(set(new_market_ids) | missing_price_market_ids)
    if not market_ids_requiring_prices:
        return
    price_rows = _fetch_market_prices(
        market_ids_requiring_prices,
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
    market_scope: str = "all",
    target_team_pairs: set[tuple[str, str]] | None = None,
) -> dict[str, Any]:
    market_scope = _normalize_market_scope(market_scope)
    competition_ids = _configured_ints(
        sport_config.get("competition_ids"),
        FANDUEL_SOCCER_COMPETITION_IDS,
    )
    tab_keywords = _configured_strings(
        sport_config.get("tab_title_keywords") or sport_config.get("tab_keywords"),
        FANDUEL_SOCCER_GAME_LINE_TAB_KEYWORDS
        if _is_game_lines_scope(market_scope)
        else FANDUEL_SOCCER_TAB_KEYWORDS,
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

        competition_events = _competition_match_events(attachments, competition_id)
        for event in _match_events_for_target_games(
            competition_events,
            target_team_pairs,
            fallback_to_all=not bool(target_team_pairs),
        )[:event_limit]:
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


def _missing_live_soccer_competitions(payload: Any, sport_config: dict[str, Any]) -> bool:
    if not isinstance(payload, dict) or payload.get("source") != "fanduel_live_soccer":
        return False
    expected_ids = set(
        _configured_ints(
            sport_config.get("competition_ids"),
            FANDUEL_SOCCER_COMPETITION_IDS,
        )
    )
    payload_ids = set(_configured_ints(payload.get("competition_ids"), ()))
    return bool(expected_ids - payload_ids)


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
    market_scope: str = "all",
    target_team_pairs_by_sport: dict[str, set[tuple[str, str]] | list[tuple[str, str]]] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    market_scope = _normalize_market_scope(market_scope)
    normalized_target_pairs = _normalized_target_team_pairs_by_sport(target_team_pairs_by_sport)
    request_config = load_request_config("fanduel")
    all_rows: list[dict[str, Any]] = []
    raw_payloads: dict[str, Any] = {}

    for sport in _normalize_sports(sports):
        sport_target_pairs = normalized_target_pairs.get(sport)
        sport_config = (request_config.get("sports") or {}).get(sport) or {}
        payload = load_saved_payload("fanduel", sport) if use_saved_payloads else None
        if market_scope == "all" and _saved_payload_needs_price_refresh(payload, sport):
            payload = None
        if market_scope == "all" and _saved_payload_missing_core_nba_player_props(payload, sport):
            payload = None
        if _saved_payload_missing_nba_quarter_game_lines(payload, sport):
            payload = None
        if market_scope == "all" and _saved_payload_missing_wnba_first_basket(payload, sport):
            payload = None
        if sport == "soccer" and _missing_live_soccer_competitions(payload, sport_config):
            payload = None
        if payload is None:
            headers = dict(request_config.get("headers") or {})
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
                            market_scope=market_scope,
                            target_team_pairs=sport_target_pairs,
                        )
                        if save_payloads and market_scope == "all":
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
                            market_scope=market_scope,
                            target_team_pairs=sport_target_pairs,
                        )
                    if save_payloads and market_scope == "all":
                        save_payload("fanduel", sport, payload)
                    break
                except Exception as exc:
                    last_error = exc
            if payload is None and last_error is not None:
                raw_payloads[sport] = {"error": str(last_error)}
                continue
        raw_payloads[sport] = payload
        all_rows.extend(_filter_rows_by_market_scope(parse_payload(payload, sport), market_scope))

    return all_rows, raw_payloads
