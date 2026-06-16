from __future__ import annotations

import json
import re
from typing import Any, Iterable, Sequence

from fair_odds import american_to_implied_prob, probability_to_american
from poll_market_matcher import normalize_team, normalize_text
from sportsbook_http import (
    SportsbookFetchBlocked,
    get_browser_like_json,
    load_request_config,
    load_saved_payload,
    save_payload,
)


SPORT_TO_EVENTGROUP = {
    "mlb": 84240,
    "ncaabb": 41151,
    "nba": 42648,
    "wnba": 94682,
    "nhl": 42133,
    "nfl": 88808,
    "ufc": 9034,
}

SPORT_ALIASES = {
    "cws": "ncaabb",
}

GOLF_LEAGUE_IDS = {
    "uspga_championship": 79720,
}

GOLF_LEAGUE_SUBCATEGORY_IDS = {
    "tournament_winner": 4508,
    "top_finish": 15786,
    "round_1_leader": 19071,
    "round_2_leader": 19075,
    "round_3_leader": 19076,
    "round_2_3_balls": 19605,
    "round_score": 11015,
}

SOCCER_LEAGUE_IDS = {
    "world_cup_2026": 209533,
    "premier_league": 40253,
    "la_liga": 40031,
    "serie_a": 40030,
    "champions_league": 40685,
    "england_championship": 40817,
    "la_liga_2": 44411,
    "ligue_1": 40032,
    "bundesliga": 40481,
    "mls": 89345,
}

NASH_HOST = "https://sportsbook-nash.draftkings.com"

PRIMARY_MARKETS_SUBCATEGORY_IDS = {
    "nba": 4511,
    "wnba": 4511,
    "mlb": 4519,
    "ncaabb": 4519,
    "nhl": 4525,
}

NBA_LEAGUE_SUBCATEGORY_IDS = {
    "points_milestones": 16477,
    "assists_milestones": 16478,
    "rebounds_milestones": 16479,
    "threes_milestones": 16480,
    "points_ou": 12488,
    "rebounds_ou": 12492,
    "assists_ou": 12495,
    "threes_ou": 12497,
    "pra_ou": 5001,
    "pr_ou": 9976,
    "pa_ou": 9973,
    "ra_ou": 9974,
    "steals_ou": 13508,
    "blocks_ou": 13780,
    "stocks_ou": 13781,
}

WNBA_LEAGUE_SUBCATEGORY_IDS = {
    "points_milestones": 16477,
    "assists_milestones": 16478,
    "rebounds_milestones": 16479,
    "threes_milestones": 16480,
    "points_ou": 12488,
    "rebounds_ou": 12492,
    "assists_ou": 12495,
    "threes_ou": 12497,
    "pra_ou": 5001,
}

WNBA_EVENT_SUBCATEGORY_IDS = {
    "alternate_total": 13201,
}

MLB_LEAGUE_SUBCATEGORY_IDS = {
    "first_inning_runs": 11024,
    "home_runs": 17319,
    "hits": 17320,
    "total_bases": 17321,
    "hits_runs_rbis": 17843,
    "rbis": 17322,
    "stolen_bases": 18726,
    "pitcher_strikeouts": 17323,
    "pitcher_hits_allowed": 19457,
    "pitcher_walks_allowed": 19456,
    "pitcher_earned_runs_allowed": 19458,
    "pitcher_hits_walks_earned_runs_allowed": 19460,
    "pitcher_strikeouts_ou": 15221,
    "pitcher_outs_recorded_ou": 17413,
    "pitcher_hits_allowed_ou": 9886,
    "pitcher_walks_allowed_ou": 15219,
    "pitcher_earned_runs_allowed_ou": 17412,
    "pitcher_hits_walks_earned_runs_allowed_ou": 19459,
}

MLB_EVENT_SUBCATEGORY_IDS = {
    "alternate_total_runs": 13169,
    "inning_team_runs": 12978,
}

NCAABB_LEAGUE_SUBCATEGORY_IDS = {
    "first_inning_runs": MLB_LEAGUE_SUBCATEGORY_IDS["first_inning_runs"],
    "hits": MLB_LEAGUE_SUBCATEGORY_IDS["hits"],
    "rbis": MLB_LEAGUE_SUBCATEGORY_IDS["rbis"],
    "alternate_total_runs": MLB_EVENT_SUBCATEGORY_IDS["alternate_total_runs"],
}

NCAABB_EVENT_SUBCATEGORY_IDS = {
    "inning_team_runs": MLB_EVENT_SUBCATEGORY_IDS["inning_team_runs"],
}

NHL_LEAGUE_SUBCATEGORY_IDS = {
    "goal_milestones": 16547,
    "shots": 16544,
    "shots_ou": 12040,
    "points": 16545,
    "points_ou": 16213,
    "assists": 16546,
    "assists_ou": 16215,
    "blocks": 16548,
    "blocks_ou": 16257,
    "saves_ou": 16550,
    "power_play_points": 18750,
}

SOCCER_LEAGUE_SUBCATEGORY_IDS = {
    "moneyline": 4514,
    "spread": 13170,
    "total_goals": 13171,
    "first_half_total_goals": 17903,
    "both_teams_score": 5645,
    "double_chance": 8068,
    "halftime_result": 11273,
    "asian_total_goals": 19542,
    "goalscorer": 16604,
    "next_goalscorer": 10711,
    "player_assists": 16863,
    "goalkeeper_saves": 18346,
}

UFC_LEAGUE_SUBCATEGORY_IDS = {
    "fight_lines": 13025,
    "method_of_victory": 18911,
    "round_of_finish": 13308,
    "winning_round": 5798,
    "significant_strikes": 19391,
    "significant_strikes_ou": 19390,
    "total_significant_strikes": 19389,
    "takedowns": 19393,
    "takedowns_ou": 19392,
}

NBA_PLAYER_SUBCATEGORY_IDS = {
    "first_quarter_spread": 16822,
    "alternate_total": 13201,
}

GAME_LINE_MARKET_TYPES = {
    "both_teams_score",
    "double_chance",
    "game_spread",
    "game_total",
    "game_winner",
    "halftime_result",
    "teamnextpoints",
    "team_period_total",
}
LEAGUE_GAME_LINE_SUBCATEGORY_KEYS_BY_SPORT = {
    "mlb": {"first_inning_runs"},
    "ncaabb": {"first_inning_runs", "alternate_total_runs"},
    "nba": set(),
    "nhl": set(),
    "soccer": {
        "moneyline",
        "spread",
        "total_goals",
        "first_half_total_goals",
        "both_teams_score",
        "double_chance",
        "halftime_result",
        "asian_total_goals",
    },
    "ufc": {"fight_lines"},
}
EVENT_GAME_LINE_SUBCATEGORY_KEYS_BY_SPORT = {
    "mlb": {"alternate_total_runs", "inning_team_runs"},
    "ncaabb": {"inning_team_runs"},
    "nba": {"first_quarter_spread", "alternate_total"},
    "wnba": {"alternate_total"},
}

SPORTS_WITH_LEAGUE_SUBCATEGORY_FEEDS = {"nba", "wnba", "mlb", "ncaabb", "nhl", "soccer", "golf", "ufc"}

STAT_ALIASES = {
    "hits+runs+rbis": "hitsrunsrbis",
    "hitsrunsrbis": "hitsrunsrbis",
    "strikeouts": "strikeouts",
    "total bases": "totalbases",
    "home runs": "homeruns",
    "hits": "hits",
    "runs": "runs",
    "rbis": "rbis",
    "points": "points",
    "pointsou": "points",
    "rebounds": "rebounds",
    "reboundsou": "rebounds",
    "assists": "assists",
    "assistsou": "assists",
    "pts": "points",
    "ptsrebast": "pointsreboundsassists",
    "ptsrebasts": "pointsreboundsassists",
    "pointsreboundsassists": "pointsreboundsassists",
    "pointsreboundsassistsou": "pointsreboundsassists",
    "ptsrebou": "pointsrebounds",
    "pointsreboundsou": "pointsrebounds",
    "ptsastou": "pointsassists",
    "pointsassistsou": "pointsassists",
    "rebastou": "reboundsassists",
    "reboundsassistsou": "reboundsassists",
    "madedthreesou": "madethrees",
    "madethree": "madethrees",
    "madethrees": "madethrees",
    "threepointersmade": "madethrees",
    "threepointsmade": "madethrees",
    "3pointersmade": "madethrees",
    "threepointersmadeou": "madethrees",
    "threesou": "madethrees",
    "3pm": "madethrees",
    "stealsou": "steals",
    "blocksou": "blocks",
    "stealsblocksou": "stealsblocks",
    "saves": "saves",
    "shots on goal": "shots",
    "shotsongoal": "shots",
    "shots": "shots",
    "playershotsongoalou": "shots",
    "playerpointsou": "points",
    "playerassistsou": "assists",
    "playerassists": "assists",
    "anytimegoalscorer": "goals",
    "goalscorer": "goals",
    "toscore2ormoregoals": "goals",
    "toscore2ormoregoalscorer": "goals",
    "chancescreated": "chancescreated",
    "chancecreated": "chancescreated",
    "keypasses": "chancescreated",
    "shotsassisted": "chancescreated",
    "playerblocksou": "blocks",
    "goalkeepersaves": "saves",
    "goaltendersavesou": "saves",
    "playerpowerplaypointsou": "powerplaypoints",
    "stolenbases": "stolenbases",
    "strikeoutsthrown": "strikeouts",
    "strikeoutsthrownou": "strikeouts",
    "hitsallowedxorfewer": "hitsallowed",
    "hitsallowedou": "hitsallowed",
    "walksallowedxorfewer": "walksallowed",
    "walksallowedou": "walksallowed",
    "earnedrunsallowedxorfewer": "earnedrunsallowed",
    "earnedrunsallowedou": "earnedrunsallowed",
    "outsou": "outsrecorded",
    "hitsallowedwalksallowedearnedrunsallowedxorfewer": "hitswalksearnedrunsallowed",
    "hitsallowedwalksallowedearnedrunsallowedou": "hitswalksearnedrunsallowed",
    "runs1stinning": "total",
    "runsfirstinning": "total",
    "totalalternate": "total",
    "alternatetotal": "total",
    "alternatetotalruns": "total",
    "asiantotal": "total",
    "asiantotalgoals": "total",
    "asianhandicaptotal": "total",
    "totalruns": "total",
    "total": "total",
    "totalgoals": "total",
    "spread": "spread",
    "asianhandicap": "spread",
    "topfinish": "topfinish",
    "top5": "topfinish",
    "top10": "topfinish",
    "top20": "topfinish",
    "outrightwinner": "winner",
    "tournamentwinner": "winner",
    "endofround1leader": "leader",
    "round1leader": "leader",
    "endofround2leader": "leader",
    "round2leader": "leader",
    "endofround3leader": "leader",
    "round3leader": "leader",
    "3ballround2": "roundmatchup",
    "3ballsround2": "roundmatchup",
    "roundmatchup": "roundmatchup",
    "roundscore": "roundscore",
    "significantstrikes": "significantstrikes",
    "significantstrikeslanded": "significantstrikes",
    "totalsignificantstrikes": "significantstrikes",
    "totalsignificantstrikeslanded": "significantstrikes",
    "takedowns": "takedowns",
    "takedownslanded": "takedowns",
    "totaltakedowns": "takedowns",
    "totaltakedownslanded": "takedowns",
    "knockdowns": "knockdowns",
}


def _normalize_sports(values: Sequence[str]) -> list[str]:
    sports: list[str] = []
    for value in values:
        sport = str(value or "").strip().lower()
        if sport:
            sports.append(SPORT_ALIASES.get(sport, sport))
    return sports


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


def _league_subcategory_items_for_scope(
    sport: str,
    subcategory_ids: dict[str, int],
    market_scope: str,
) -> list[tuple[str, int]]:
    items = list(subcategory_ids.items())
    if not _is_game_lines_scope(market_scope):
        return items
    allowed = LEAGUE_GAME_LINE_SUBCATEGORY_KEYS_BY_SPORT.get(sport, set())
    return [(key, value) for key, value in items if key in allowed]


def _event_subcategory_items_for_scope(
    sport: str,
    subcategory_ids: dict[str, int],
    market_scope: str,
) -> list[tuple[str, int]]:
    items = list(subcategory_ids.items())
    if not _is_game_lines_scope(market_scope):
        return items
    allowed = EVENT_GAME_LINE_SUBCATEGORY_KEYS_BY_SPORT.get(sport, set())
    return [(key, value) for key, value in items if key in allowed]


def _parse_american(value: Any) -> int | None:
    if value in (None, "", "None"):
        return None
    text = (
        str(value)
        .strip()
        .upper()
        .replace("−", "-")
        .replace("–", "-")
        .replace("—", "-")
    )
    if text == "EVEN":
        return 100
    try:
        return int(text.replace("+", ""))
    except Exception:
        return None


def _parse_line(value: Any) -> float | None:
    if value in (None, "", "None"):
        return None
    try:
        return float(value)
    except Exception:
        return None


def _iter_offer_bundles(node: Any) -> Iterable[tuple[str, dict[str, Any]]]:
    if isinstance(node, dict):
        if "offerSubcategory" in node and isinstance(node["offerSubcategory"], dict):
            subcategory = node["offerSubcategory"]
            name = str(node.get("name") or subcategory.get("name") or "").strip()
            offers = subcategory.get("offers")
            if isinstance(offers, list):
                yield name, {"offers": offers}
        for value in node.values():
            yield from _iter_offer_bundles(value)
    elif isinstance(node, list):
        for item in node:
            yield from _iter_offer_bundles(item)


def _event_map(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    if isinstance(payload.get("events"), list):
        events = payload.get("events") or []
    else:
        event_group = payload.get("eventGroup") or payload
        events = event_group.get("events") or []
    result: dict[str, dict[str, Any]] = {}
    for event in events:
        event_id = str(event.get("eventId") or event.get("id") or "").strip()
        if event_id:
            result[event_id] = event
    return result


def _normalize_stat(value: str) -> str:
    raw = str(value or "").strip().lower()
    key = "".join(ch for ch in raw if ch.isalnum())
    return STAT_ALIASES.get(key, key)


def _market_family(subcategory_name: str, outcomes: list[dict[str, Any]], offer_label: str) -> str | None:
    lowered = str(subcategory_name or "").lower()
    labels = {str(outcome.get("label") or "").strip().lower() for outcome in outcomes}
    if labels == {"over", "under"} and (
        "total" in lowered or (("inning" in lowered or "innings" in lowered) and "run" in lowered)
    ):
        return "game_total"
    if labels == {"over", "under"}:
        return "player_over_under"
    if len(outcomes) == 2 and offer_label == "":
        return "game_winner"
    return None


def _is_yes_no_first_inning_runs_market(
    market_name: str,
    market_type_name: str,
    labels: set[str],
) -> bool:
    if labels != {"yes", "no"}:
        return False
    text = f"{market_name} {market_type_name}".lower()
    return ("inning" in text or "innings" in text) and "run" in text


def _team_name_in_market_name(
    market_name: str,
    *,
    home_team: str,
    away_team: str,
) -> str:
    market_key = normalize_text(market_name)
    for team_name in (home_team, away_team):
        team_text = str(team_name or "").strip()
        if not team_text:
            continue
        team_key = normalize_text(team_text)
        if team_key and team_key in market_key:
            return team_text
        abbreviation = normalize_team(team_text)
        if abbreviation and re.search(rf"\b{re.escape(abbreviation)}\b", market_name, flags=re.IGNORECASE):
            return team_text
    return ""


def _event_teams(event: dict[str, Any]) -> tuple[str, str]:
    home = str(
        event.get("teamName2")
        or event.get("homeTeamName")
        or event.get("homeTeam")
        or ""
    ).strip()
    away = str(
        event.get("teamName1")
        or event.get("awayTeamName")
        or event.get("awayTeam")
        or ""
    ).strip()
    return home, away


def _event_teams_from_participants(event: dict[str, Any]) -> tuple[str, str]:
    home = ""
    away = ""
    for participant in event.get("participants") or []:
        if str(participant.get("type") or "").strip().lower() != "team":
            continue
        role = str(participant.get("venueRole") or "").strip().lower()
        name = str(
            participant.get("name")
            or (participant.get("metadata") or {}).get("shortName")
            or ""
        ).strip()
        if role == "home":
            home = name
        elif role == "away":
            away = name
    return home, away


def _normalized_team_pair(home_team: str, away_team: str) -> tuple[str, str]:
    normalized_home = normalize_team(home_team)
    normalized_away = normalize_team(away_team)
    if not normalized_home or not normalized_away:
        return "", ""
    teams = sorted((normalized_home, normalized_away))
    return teams[0], teams[1]


def _event_team_pair(event: dict[str, Any]) -> tuple[str, str]:
    home_team, away_team = _event_teams_from_participants(event)
    if not home_team or not away_team:
        fallback_home, fallback_away = _event_teams(event)
        home_team = home_team or fallback_home
        away_team = away_team or fallback_away
    return _normalized_team_pair(home_team, away_team)


def _normalized_target_team_pairs_by_sport(
    target_team_pairs_by_sport: dict[str, set[tuple[str, str]] | list[tuple[str, str]]] | None,
) -> dict[str, set[tuple[str, str]]]:
    normalized: dict[str, set[tuple[str, str]]] = {}
    if not isinstance(target_team_pairs_by_sport, dict):
        return normalized
    for sport_key, pairs in target_team_pairs_by_sport.items():
        sport = str(sport_key or "").strip().lower()
        sport = SPORT_ALIASES.get(sport, sport)
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


def _event_ids_for_target_games(
    events_by_id: dict[str, dict[str, Any]],
    target_team_pairs: set[tuple[str, str]] | None,
) -> list[str]:
    event_ids = list(events_by_id)
    if not target_team_pairs:
        return event_ids
    matched_event_ids = [
        event_id
        for event_id, event in events_by_id.items()
        if _event_team_pair(event) in target_team_pairs
    ]
    # Fallback to all events when no team-pair match is found.
    return matched_event_ids or event_ids


def _selection_label(selection: dict[str, Any]) -> str:
    return str(selection.get("label") or "").strip()


def _selection_display_odds(selection: dict[str, Any]) -> int | None:
    return _parse_american(((selection.get("displayOdds") or {}).get("american")))


def _selection_outcome_key(selection: dict[str, Any]) -> str:
    label = _selection_label(selection)
    outcome_type = str(selection.get("outcomeType") or "").strip()
    return outcome_type or label


def _selection_outcomes_json(selections: list[dict[str, Any]]) -> str:
    outcomes = []
    for selection in selections:
        odds = _selection_display_odds(selection)
        if odds is None:
            continue
        outcomes.append(
            {
                "key": _selection_outcome_key(selection),
                "label": _selection_label(selection),
                "odds": odds,
            }
        )
    return json.dumps(outcomes, separators=(",", ":"), ensure_ascii=True) if outcomes else ""


def _ufc_method_key(value: str) -> str:
    key = normalize_text(value).replace(" ", "")
    if "ko" in key or "tko" in key or "dq" in key:
        return "ko"
    if "sub" in key:
        return "sub"
    if "decision" in key or "points" in key:
        return "decision"
    return ""


def _ufc_round_key(value: str) -> str:
    text = normalize_text(value)
    key = text.replace(" ", "")
    round_match = re.search(r"\bround\s*([1-5])\b", text)
    if round_match:
        return f"{int(round_match.group(1))}{'st' if round_match.group(1) == '1' else 'nd' if round_match.group(1) == '2' else 'rd' if round_match.group(1) == '3' else 'th'}"
    if key in {"1st", "first"}:
        return "1st"
    if key in {"2nd", "second"}:
        return "2nd"
    if key in {"3rd", "third"}:
        return "3rd"
    if key in {"4th", "fourth"}:
        return "4th"
    if key in {"5th", "fifth"}:
        return "5th"
    if "decision" in key or "points" in key:
        return "decision"
    return ""


def _ufc_round_label(key: str) -> str:
    return {
        "1st": "1st",
        "2nd": "2nd",
        "3rd": "3rd",
        "4th": "4th",
        "5th": "5th",
        "decision": "Decision",
    }.get(key, key)


def _ufc_stat_key(value: str) -> str:
    text = normalize_text(value)
    compact = text.replace(" ", "")
    if "control" in text and "time" in text:
        return "controltime"
    if "head" in text and "strike" in text:
        return "significantheadstrikes"
    if "leg" in text and "strike" in text:
        return "significantlegstrikes"
    if "significantstrike" in compact or "sigstrike" in compact:
        return "significantstrikes"
    if "takedown" in text:
        return "takedowns"
    if "knockdown" in text:
        return "knockdowns"
    if "strike" in text:
        return "strikes"
    return ""


def _ufc_player_stat_context(market_name: str, market_type_name: str) -> tuple[str, str] | None:
    market_text = " ".join(str(market_name or "").strip().split())
    type_text = " ".join(str(market_type_name or "").strip().split())
    stat_text = f"{market_text} {type_text}"
    stat_key = _ufc_stat_key(stat_text)
    if stat_key not in {"significantstrikes", "takedowns", "strikes"}:
        return None

    patterns = (
        r"^(.+?)\s+total\s+significant\s+strikes(?:\s+landed)?(?:\s+o/u)?$",
        r"^(.+?)\s+total\s+takedowns(?:\s+landed)?(?:\s+o/u)?$",
        r"^(.+?)\s+total\s+strikes(?:\s+landed)?(?:\s+o/u)?$",
    )
    for pattern in patterns:
        match = re.match(pattern, market_text, flags=re.IGNORECASE)
        if match:
            player_name = match.group(1).strip()
            if player_name and normalize_text(player_name) != "total":
                return player_name, stat_key
    return None


def _selection_over_under_side(selection: dict[str, Any]) -> str:
    outcome_type = normalize_text(str(selection.get("outcomeType") or "")).replace(" ", "")
    if outcome_type in {"over", "under"}:
        return outcome_type
    label = normalize_text(_selection_label(selection))
    if label.startswith("over "):
        return "over"
    if label.startswith("under "):
        return "under"
    return ""


def _selection_total_line(selection: dict[str, Any]) -> float | None:
    points = _parse_line(selection.get("points"))
    if points is not None:
        return points
    match = re.search(r"\b(?:over|under)\s+([0-9]+(?:\.[0-9]+)?)\b", _selection_label(selection), flags=re.IGNORECASE)
    if match:
        return _parse_line(match.group(1))
    return None


def _plus_threshold_line(value: Any) -> float | None:
    match = re.search(r"\b([0-9]+)\s*\+", str(value or ""))
    if not match:
        return None
    try:
        return float(match.group(1)) - 0.5
    except Exception:
        return None


def _ufc_selection_side(selection: dict[str, Any]) -> str:
    outcome_type = str(selection.get("outcomeType") or "").strip().lower()
    if outcome_type in {"home", "away", "tie", "draw"}:
        return {"tie": "draw"}.get(outcome_type, outcome_type)
    for participant in selection.get("participants") or []:
        role = str(participant.get("venueRole") or "").strip().lower()
        if role in {"home", "away"}:
            return role
    label = _selection_label(selection)
    if normalize_text(label).replace(" ", "") in {"tie", "draw"}:
        return "draw"
    return ""


def _ufc_aggregate_probability_odds(selections: list[dict[str, Any]]) -> int | None:
    probabilities = [
        american_to_implied_prob(odds)
        for odds in (_selection_display_odds(selection) for selection in selections)
        if odds is not None
    ]
    if not probabilities:
        return None
    return probability_to_american(min(sum(probabilities), 0.999999))


def _ufc_extra_outcomes_json(outcomes: list[dict[str, Any]]) -> str:
    cleaned = [outcome for outcome in outcomes if outcome.get("key") and outcome.get("odds") is not None]
    return json.dumps(cleaned, separators=(",", ":"), ensure_ascii=True) if cleaned else ""


def _parse_ufc_controldata_grouped_markets(
    payload: dict[str, Any],
    *,
    events: dict[str, dict[str, Any]],
    selections_by_market: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    methods_by_event: dict[str, dict[str, list[dict[str, Any]]]] = {}

    for market in payload.get("markets") or []:
        market_id = str(market.get("id") or "").strip()
        event_id = str(market.get("eventId") or "").strip()
        if not market_id or not event_id:
            continue
        selections = selections_by_market.get(market_id, [])
        if not selections:
            continue
        event = events.get(event_id, {})
        home_team, away_team = _event_teams_from_participants(event)
        updated_at = market.get("lastUpdated") or event.get("startEventDate") or ""
        market_name = str(
            market.get("name")
            or (market.get("marketType") or {}).get("name")
            or ""
        ).strip()
        market_type_name = str(((market.get("marketType") or {}).get("name") or "")).strip()
        market_text = f"{market_name} {market_type_name}"
        market_name_key = normalize_text(market_name).replace(" ", "")
        method_key = _ufc_method_key(market_name) if market_name_key in {"kotkodq", "submission", "decision"} else ""
        if method_key and len(selections) >= 2:
            methods_by_event.setdefault(event_id, {}).setdefault(method_key, []).extend(selections)
            continue

        player_stat = _ufc_player_stat_context(market_name, market_type_name)
        if player_stat is not None:
            player_name, player_stat_key = player_stat
            grouped_by_line: dict[float, dict[str, dict[str, Any]]] = {}
            for selection in selections:
                side = _selection_over_under_side(selection)
                line = _selection_total_line(selection)
                if side not in {"over", "under"} or line is None:
                    continue
                grouped_by_line.setdefault(line, {})[side] = selection

            added_rows = False
            for line, grouped in sorted(grouped_by_line.items()):
                over = grouped.get("over")
                under = grouped.get("under")
                if not over or not under:
                    continue
                over_odds = _selection_display_odds(over)
                under_odds = _selection_display_odds(under)
                if over_odds is None or under_odds is None:
                    continue
                added_rows = True
                rows.append(
                    {
                        "provider": "draftkings",
                        "provider_event_id": event_id,
                        "provider_market_id": f"{market_id}:{line:g}",
                        "provider_league": "ufc",
                        "provider_market_name": market_name,
                        "book": "draftkings",
                        "sport": "ufc",
                        "market_type": "player_over_under",
                        "stat": player_stat_key,
                        "player_name": player_name,
                        "line": line,
                        "home_team": home_team,
                        "away_team": away_team,
                        "over_odds": over_odds,
                        "under_odds": under_odds,
                        "updated_at": updated_at,
                        "period": "",
                        "event_date": event.get("startEventDate") or "",
                        "question": market_name,
                    }
                )

            if not added_rows:
                for selection in selections:
                    over_odds = _selection_display_odds(selection)
                    line = _plus_threshold_line(_selection_label(selection))
                    if over_odds is None or line is None:
                        continue
                    added_rows = True
                    rows.append(
                        {
                            "provider": "draftkings",
                            "provider_event_id": event_id,
                            "provider_market_id": f"{market_id}:{selection.get('id') or _selection_label(selection)}",
                            "provider_league": "ufc",
                            "provider_market_name": market_name,
                            "book": "draftkings",
                            "sport": "ufc",
                            "market_type": "player_over_under",
                            "stat": player_stat_key,
                            "player_name": player_name,
                            "line": line,
                            "home_team": home_team,
                            "away_team": away_team,
                            "over_odds": over_odds,
                            "under_odds": _synthetic_under_odds(over_odds),
                            "updated_at": updated_at,
                            "period": "",
                            "event_date": event.get("startEventDate") or "",
                            "question": f"{player_name} {_selection_label(selection)}",
                        }
                    )
            if added_rows:
                continue

        if normalize_text(market_name).replace(" ", "") in {"winninground", "whatroundwillfightend"}:
            outcomes: list[dict[str, Any]] = []
            for selection in selections:
                odds = _selection_display_odds(selection)
                round_key = _ufc_round_key(_selection_label(selection))
                if odds is None or not round_key:
                    continue
                outcomes.append(
                    {
                        "key": round_key,
                        "label": _ufc_round_label(round_key),
                        "odds": odds,
                    }
                )
            extra_outcomes = _ufc_extra_outcomes_json(outcomes)
            if extra_outcomes:
                rows.append(
                    {
                        "provider": "draftkings",
                        "provider_event_id": event_id,
                        "provider_market_id": market_id,
                        "provider_league": "ufc",
                        "provider_market_name": market_name,
                        "book": "draftkings",
                        "sport": "ufc",
                        "market_type": "fight_round",
                        "stat": "endinground",
                        "player_name": "",
                        "line": "",
                        "home_team": home_team,
                        "away_team": away_team,
                        "over_odds": outcomes[0].get("odds") if outcomes else "",
                        "under_odds": outcomes[1].get("odds") if len(outcomes) > 1 else "",
                        "extra_outcomes": extra_outcomes,
                        "updated_at": updated_at,
                        "period": "",
                        "event_date": event.get("startEventDate") or "",
                        "question": market_name,
                    }
                )
            continue

        stat_key = _ufc_stat_key(market_text)
        if stat_key and "most" in normalize_text(market_text):
            outcomes = []
            for selection in selections:
                odds = _selection_display_odds(selection)
                side = _ufc_selection_side(selection)
                if odds is None or not side:
                    continue
                label = _selection_label(selection)
                outcomes.append({"key": side, "label": label, "odds": odds})
            extra_outcomes = _ufc_extra_outcomes_json(outcomes)
            if extra_outcomes:
                rows.append(
                    {
                        "provider": "draftkings",
                        "provider_event_id": event_id,
                        "provider_market_id": market_id,
                        "provider_league": "ufc",
                        "provider_market_name": market_name,
                        "book": "draftkings",
                        "sport": "ufc",
                        "market_type": "fighter_stat_winner",
                        "stat": stat_key,
                        "player_name": "",
                        "line": "",
                        "home_team": home_team,
                        "away_team": away_team,
                        "over_odds": outcomes[0].get("odds") if outcomes else "",
                        "under_odds": outcomes[1].get("odds") if len(outcomes) > 1 else "",
                        "extra_outcomes": extra_outcomes,
                        "updated_at": updated_at,
                        "period": "",
                        "event_date": event.get("startEventDate") or "",
                        "question": market_name,
                    }
                )

    for event_id, method_selections in methods_by_event.items():
        event = events.get(event_id, {})
        home_team, away_team = _event_teams_from_participants(event)
        outcomes: list[dict[str, Any]] = []
        for key, label in (("ko", "KO/TKO/DQ"), ("sub", "Submission"), ("decision", "Decision")):
            odds = _ufc_aggregate_probability_odds(method_selections.get(key, []))
            if odds is None:
                continue
            outcomes.append({"key": key, "label": label, "odds": odds})
        extra_outcomes = _ufc_extra_outcomes_json(outcomes)
        if not extra_outcomes:
            continue
        rows.append(
            {
                "provider": "draftkings",
                "provider_event_id": event_id,
                "provider_market_id": f"{event_id}:fight_method",
                "provider_league": "ufc",
                "provider_market_name": "Method of Victory",
                "book": "draftkings",
                "sport": "ufc",
                "market_type": "fight_method",
                "stat": "method",
                "player_name": "",
                "line": "",
                "home_team": home_team,
                "away_team": away_team,
                "over_odds": outcomes[0].get("odds") if outcomes else "",
                "under_odds": outcomes[1].get("odds") if len(outcomes) > 1 else "",
                "extra_outcomes": extra_outcomes,
                "updated_at": event.get("startEventDate") or "",
                "period": "",
                "event_date": event.get("startEventDate") or "",
                "question": "Method of Victory",
            }
        )
    return rows


def _selection_points(selection: dict[str, Any]) -> float | None:
    return _parse_line(selection.get("points"))


def _selection_player_name(
    selection: dict[str, Any],
    *,
    allow_team_participant: bool = False,
) -> str:
    for participant in selection.get("participants") or []:
        participant_type = str(participant.get("type") or "").strip().lower()
        if participant_type and (participant_type != "team" or allow_team_participant):
            return str(participant.get("name") or "").strip()
    return ""


def _golf_market_context(market_name: str, market_type_name: str) -> tuple[str, float, str] | None:
    text = f"{market_name} {market_type_name}".lower()
    period = ""
    round_match = re.search(r"\b(?:round|end of round)\s+([1-9][0-9]*)\b", text)
    if not round_match:
        round_match = re.search(r"\b([1-9][0-9]*)(?:st|nd|rd|th)\s+round\b", text)
    if round_match:
        period = f"R{int(round_match.group(1))}"
    if "3 ball" in text or "3-ball" in text or "3 balls" in text:
        return "roundmatchup", 1.0, period
    top_match = re.search(r"\btop\s+([0-9]+)\b", text)
    if top_match:
        return "topfinish", float(top_match.group(1)), period
    if "leader" in text and period:
        return "leader", 1.0, period
    if "outright winner" in text or "tournament winner" in text:
        return "winner", 1.0, ""
    return None


def _derive_player_stat(market_name: str, player_name: str) -> str:
    market_text = str(market_name or "").strip()
    player_text = str(player_name or "").strip()
    if player_text and market_text.lower().startswith(player_text.lower()):
        stat_text = market_text[len(player_text):].strip(" -")
        return _normalize_stat(stat_text)
    return _normalize_stat(market_text)


def _goalscorer_market_line(market_name: str, market_type_name: str) -> float | None:
    text = f"{market_name} {market_type_name}".lower()
    if "first goalscorer" in text or "1st goalscorer" in text:
        return None
    if "next goalscorer" in text or "next goal scorer" in text:
        return 0.5
    if "anytime goalscorer" in text:
        return 0.5
    match = re.search(r"(?:to\s+)?score\s+([0-9]+)\s*(?:\+|or\s+more)", text)
    if match:
        return float(match.group(1)) - 0.5
    return None


def _is_first_basket_market(market_name: str, market_type_name: str) -> bool:
    text = f"{market_name} {market_type_name}".lower()
    if "first team basket" in text:
        return False
    return "first basket" in text or "first field goal" in text or "next basket" in text


def _is_next_score_market(market_name: str, market_type_name: str) -> bool:
    text = f"{market_name} {market_type_name}".lower()
    return (
        "next run" in text
        or "next score" in text
        or "next team to score" in text
        or "team to score next" in text
        or "next goal" in text
    )


def _infer_period(market_name: str) -> str:
    lowered = str(market_name or "").lower()
    inning_match = re.search(r"\b([1-9][0-9]*)(?:st|nd|rd|th)?\s+inning\b", lowered)
    if inning_match:
        return f"{int(inning_match.group(1))}I"
    inning_words = {
        "first": 1,
        "second": 2,
        "third": 3,
        "fourth": 4,
        "fifth": 5,
        "sixth": 6,
        "seventh": 7,
        "eighth": 8,
        "ninth": 9,
    }
    for word, number in inning_words.items():
        if f"{word} inning" in lowered:
            return f"{number}I"
    if "1st inning" in lowered or "first inning" in lowered:
        return "1I"
    if "2nd inning" in lowered or "second inning" in lowered:
        return "2I"
    if "3rd inning" in lowered or "third inning" in lowered:
        return "3I"
    if "4th inning" in lowered or "fourth inning" in lowered:
        return "4I"
    if "1st quarter" in lowered or "q1" in lowered:
        return "1Q"
    if "2nd quarter" in lowered or "q2" in lowered:
        return "2Q"
    if "3rd quarter" in lowered or "q3" in lowered:
        return "3Q"
    if "4th quarter" in lowered or "q4" in lowered:
        return "4Q"
    if "1st period" in lowered or "p1" in lowered:
        return "1P"
    if "2nd period" in lowered or "p2" in lowered:
        return "2P"
    if "3rd period" in lowered or "p3" in lowered:
        return "3P"
    if "1st half" in lowered:
        return "1H"
    if "2nd half" in lowered:
        return "2H"
    if "overtime" in lowered or re.search(r"\bot\b", lowered):
        return "OT"
    return ""


def _synthetic_under_odds(over_odds: int) -> int:
    over_prob = american_to_implied_prob(over_odds)
    under_prob = max(1e-9, 1.0 - over_prob)
    return probability_to_american(under_prob)


def _group_selections(payload: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for selection in payload.get("selections") or []:
        market_id = str(selection.get("marketId") or "").strip()
        if not market_id:
            continue
        grouped.setdefault(market_id, []).append(selection)
    return grouped


def _merge_event_maps(*mappings: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for mapping in mappings:
        merged.update(mapping or {})
    return merged


def _league_subcategory_url(league_id: int | str, subcategory_id: int | str) -> str:
    return (
        f"{NASH_HOST}/sites/US-SB/api/sportscontent/controldata/league/"
        "leagueSubcategory/v1/markets"
        f"?isBatchable=false&templateVars={league_id}"
        f"&eventsQuery=%24filter%3DleagueId%20eq%20%27{league_id}%27%20AND%20clientMetadata%2FSubcategories%2Fany%28s%3A%20s%2FId%20eq%20%27{subcategory_id}%27%29"
        f"&marketsQuery=%24filter%3DclientMetadata%2FsubCategoryId%20eq%20%27{subcategory_id}%27%20AND%20tags%2Fall%28t%3A%20t%20ne%20%27SportcastBetBuilder%27%29"
        "&include=Events&entity=events"
    )


def _event_subcategory_url(event_id: int | str, subcategory_id: int | str) -> str:
    return (
        f"{NASH_HOST}/sites/US-SB/api/sportscontent/controldata/event/"
        "eventSubcategory/v1/markets"
        f"?isBatchable=false&templateVars={event_id}%2C{subcategory_id}"
        f"&marketsQuery=%24filter%3DeventId%20eq%20%27{event_id}%27%20AND%20clientMetadata%2FsubCategoryId%20eq%20%27{subcategory_id}%27%20AND%20tags%2Fall%28t%3A%20t%20ne%20%27SportcastBetBuilder%27%29"
        "&entity=markets"
    )


def _nash_headers(*, feature: str, page: str) -> dict[str, str]:
    return {
        "accept": "*/*",
        "content-type": "application/json charset=utf-8",
        "origin": "https://sportsbook.draftkings.com",
        "referer": "https://sportsbook.draftkings.com/",
        "sec-ch-ua": '"Google Chrome";v="147", "Not.A/Brand";v="8", "Chromium";v="147"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-site",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36",
        "x-client-feature": feature,
        "x-client-name": "web",
        "x-client-page": page,
        "x-client-version": "2616.4.1.4",
        "x-client-widget-name": "cms",
        "x-client-widget-version": "2.10.9",
    }


def _parse_controldata_payload(
    payload: dict[str, Any],
    sport: str,
    *,
    event_lookup: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    events = _merge_event_maps(_event_map(payload), event_lookup or {})
    selections_by_market = _group_selections(payload)
    if sport == "ufc":
        rows.extend(
            _parse_ufc_controldata_grouped_markets(
                payload,
                events=events,
                selections_by_market=selections_by_market,
            )
        )

    for market in payload.get("markets") or []:
        market_id = str(market.get("id") or "").strip()
        event_id = str(market.get("eventId") or "").strip()
        selections = selections_by_market.get(market_id, [])
        # Some DK player milestone markets (notably soccer assists 1+) are single-selection.
        if not event_id or not selections:
            continue

        event = events.get(event_id, {})
        home_team, away_team = _event_teams_from_participants(event)
        updated_at = market.get("lastUpdated") or event.get("startEventDate") or ""
        market_name = str(
            market.get("name")
            or (market.get("marketType") or {}).get("name")
            or ""
        ).strip()
        market_type_name = str(((market.get("marketType") or {}).get("name") or "")).strip()
        period = _infer_period(market_name or market_type_name)
        labels = {(_selection_label(selection).lower()) for selection in selections}

        if sport == "golf":
            market_text = f"{market_name} {market_type_name}".lower()
            if "round score" in market_text and {"over", "under"}.issubset(labels):
                over = next(
                    (
                        item
                        for item in selections
                        if str(item.get("outcomeType") or item.get("label") or "").strip().lower() == "over"
                    ),
                    None,
                )
                under = next(
                    (
                        item
                        for item in selections
                        if str(item.get("outcomeType") or item.get("label") or "").strip().lower() == "under"
                    ),
                    None,
                )
                if over and under:
                    line = _selection_points(over) or _selection_points(under)
                    over_odds = _selection_display_odds(over)
                    under_odds = _selection_display_odds(under)
                    player_name = (
                        _selection_player_name(over, allow_team_participant=True)
                        or _selection_player_name(under, allow_team_participant=True)
                        or re.sub(r"\s+round\s+score.*$", "", market_name, flags=re.IGNORECASE).strip()
                    )
                    round_match = re.search(r"\bround\s+([1-9][0-9]*)\b", market_text)
                    golf_period = f"R{int(round_match.group(1))}" if round_match else ""
                    if line is not None and over_odds is not None and under_odds is not None and player_name:
                        rows.append(
                            {
                                "provider": "draftkings",
                                "provider_event_id": event_id,
                                "provider_market_id": market_id,
                                "provider_league": sport,
                                "provider_market_name": market_name,
                                "book": "draftkings",
                                "sport": sport,
                                "market_type": "player_over_under",
                                "stat": "roundscore",
                                "player_name": player_name,
                                "line": line,
                                "home_team": "",
                                "away_team": "",
                                "over_odds": over_odds,
                                "under_odds": under_odds,
                                "updated_at": updated_at,
                                "period": golf_period,
                                "event_date": event.get("startEventDate") or "",
                                "question": market_name,
                            }
                        )
                continue

            golf_context = _golf_market_context(market_name, market_type_name)
            if golf_context is None:
                continue
            stat_key, line, golf_period = golf_context
            for selection in selections:
                odds = _selection_display_odds(selection)
                player_name = (
                    _selection_player_name(selection, allow_team_participant=True)
                    or _selection_label(selection)
                )
                if odds is None or not player_name:
                    continue
                rows.append(
                    {
                        "provider": "draftkings",
                        "provider_event_id": event_id,
                        "provider_market_id": f"{market_id}:{selection.get('id') or player_name}",
                        "provider_league": sport,
                        "provider_market_name": market_name,
                        "book": "draftkings",
                        "sport": sport,
                        "market_type": "player_finish",
                        "stat": stat_key,
                        "player_name": player_name,
                        "line": line,
                        "home_team": "",
                        "away_team": "",
                        "over_odds": odds,
                        "under_odds": "",
                        "updated_at": updated_at,
                        "period": golf_period,
                        "event_date": event.get("startEventDate") or "",
                        "question": _selection_label(selection) or market_name,
                    }
                )
            continue

        goalscorer_line = _goalscorer_market_line(market_name, market_type_name)
        if goalscorer_line is not None:
            for selection in selections:
                over_odds = _selection_display_odds(selection)
                player_name = _selection_player_name(selection)
                if over_odds is None or not player_name:
                    continue
                rows.append(
                    {
                        "provider": "draftkings",
                        "provider_event_id": event_id,
                        "provider_market_id": str(selection.get("id") or market_id),
                        "provider_league": sport,
                        "provider_market_name": market_name,
                        "book": "draftkings",
                        "sport": sport,
                        "market_type": "player_over_under",
                        "stat": "goals",
                        "player_name": player_name,
                        "line": goalscorer_line,
                        "home_team": home_team,
                        "away_team": away_team,
                        "over_odds": over_odds,
                        "under_odds": _synthetic_under_odds(over_odds),
                        "updated_at": updated_at,
                        "period": period,
                        "event_date": event.get("startEventDate") or "",
                        "question": _selection_label(selection) or market_name,
                    }
                )
            continue

        if _is_first_basket_market(market_name, market_type_name):
            for selection in selections:
                over_odds = _selection_display_odds(selection)
                player_name = _selection_player_name(selection)
                if over_odds is None or not player_name:
                    continue
                rows.append(
                    {
                        "provider": "draftkings",
                        "provider_event_id": event_id,
                        "provider_market_id": str(selection.get("id") or market_id),
                        "provider_league": sport,
                        "provider_market_name": market_name,
                        "book": "draftkings",
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
                        "period": period,
                        "event_date": event.get("startEventDate") or "",
                        "question": _selection_label(selection) or market_name,
                    }
                )
            continue

        if any(selection.get("milestoneValue") not in (None, "", "None") for selection in selections):
            for selection in selections:
                over_odds = _selection_display_odds(selection)
                milestone_value = _parse_line(selection.get("milestoneValue"))
                player_name = _selection_player_name(selection)
                if over_odds is None or milestone_value is None or not player_name:
                    continue
                rows.append(
                    {
                        "provider": "draftkings",
                        "provider_event_id": event_id,
                        "provider_market_id": str(selection.get("id") or market_id),
                        "provider_league": sport,
                        "provider_market_name": market_name,
                        "book": "draftkings",
                        "sport": sport,
                        "market_type": "player_over_under",
                        "stat": _derive_player_stat(market_name, player_name),
                        "player_name": player_name,
                        "line": milestone_value - 0.5,
                        "home_team": home_team,
                        "away_team": away_team,
                        "over_odds": over_odds,
                        "under_odds": _synthetic_under_odds(over_odds),
                        "updated_at": updated_at,
                        "period": period,
                        "event_date": event.get("startEventDate") or "",
                        "question": _selection_label(selection) or market_name,
                    }
                )
            continue

        if labels == {"over", "under"}:
            grouped_by_line: dict[tuple[str, float], dict[str, dict[str, Any]]] = {}
            for selection in selections:
                label = _selection_label(selection).lower()
                line = _selection_points(selection)
                if label not in {"over", "under"} or line is None:
                    continue
                player_name = _selection_player_name(selection)
                grouped_by_line.setdefault((player_name, line), {})[label] = selection

            for (player_name_hint, line), grouped in sorted(grouped_by_line.items(), key=lambda item: (item[0][0], item[0][1])):
                over = grouped.get("over")
                under = grouped.get("under")
                if not over or not under:
                    continue
                over_odds = _selection_display_odds(over)
                under_odds = _selection_display_odds(under)
                if over_odds is None or under_odds is None:
                    continue

                player_name = player_name_hint or _selection_player_name(over) or _selection_player_name(under)
                market_type = "player_over_under" if player_name else "game_total"
                stat_key = _derive_player_stat(market_type_name or market_name, player_name) if player_name else _normalize_stat(market_type_name or market_name)
                rows.append(
                    {
                        "provider": "draftkings",
                        "provider_event_id": event_id,
                        "provider_market_id": f"{market_id}:{line:g}",
                        "provider_league": sport,
                        "provider_market_name": market_name,
                        "book": "draftkings",
                        "sport": sport,
                        "market_type": market_type,
                        "stat": stat_key,
                        "player_name": player_name,
                        "line": line,
                        "home_team": home_team,
                        "away_team": away_team,
                        "over_odds": over_odds,
                        "under_odds": under_odds,
                        "updated_at": updated_at,
                        "period": period,
                        "event_date": event.get("startEventDate") or "",
                        "question": market_name,
                    }
                )
            continue

        if market_name.lower() == "both teams to score" and labels == {"yes", "no"}:
            yes_selection = next((selection for selection in selections if _selection_label(selection).lower() == "yes"), None)
            no_selection = next((selection for selection in selections if _selection_label(selection).lower() == "no"), None)
            if not yes_selection or not no_selection:
                continue
            yes_odds = _selection_display_odds(yes_selection)
            no_odds = _selection_display_odds(no_selection)
            if yes_odds is None or no_odds is None:
                continue
            rows.append(
                {
                    "provider": "draftkings",
                    "provider_event_id": event_id,
                    "provider_market_id": market_id,
                    "provider_league": sport,
                    "provider_market_name": market_name,
                    "book": "draftkings",
                    "sport": sport,
                    "market_type": "both_teams_score",
                    "stat": "bothteamsscore",
                    "player_name": "",
                    "line": "",
                    "home_team": home_team,
                    "away_team": away_team,
                    "over_odds": yes_odds,
                    "under_odds": no_odds,
                    "extra_outcomes": _selection_outcomes_json([yes_selection, no_selection]),
                    "updated_at": updated_at,
                    "period": period,
                    "event_date": event.get("startEventDate") or "",
                    "question": market_name,
                }
            )
            continue

        if _is_yes_no_first_inning_runs_market(market_name, market_type_name, labels):
            yes_selection = next((selection for selection in selections if _selection_label(selection).lower() == "yes"), None)
            no_selection = next((selection for selection in selections if _selection_label(selection).lower() == "no"), None)
            if not yes_selection or not no_selection:
                continue
            yes_odds = _selection_display_odds(yes_selection)
            no_odds = _selection_display_odds(no_selection)
            if yes_odds is None or no_odds is None:
                continue
            team_name = _team_name_in_market_name(
                market_name,
                home_team=home_team,
                away_team=away_team,
            )
            rows.append(
                {
                    "provider": "draftkings",
                    "provider_event_id": event_id,
                    "provider_market_id": market_id,
                    "provider_league": sport,
                    "provider_market_name": market_name,
                    "book": "draftkings",
                    "sport": sport,
                    "market_type": "team_period_total" if team_name else "game_total",
                    "stat": "total",
                    "player_name": team_name,
                    "line": 0.5,
                    "home_team": home_team,
                    "away_team": away_team,
                    "over_odds": yes_odds,
                    "under_odds": no_odds,
                    "updated_at": updated_at,
                    "period": period,
                    "event_date": event.get("startEventDate") or "",
                    "question": market_name,
                }
            )
            continue

        if _is_next_score_market(market_name, market_type_name):
            priced = [selection for selection in selections if _selection_display_odds(selection) is not None]
            if len(priced) < 2:
                continue
            rows.append(
                {
                    "provider": "draftkings",
                    "provider_event_id": event_id,
                    "provider_market_id": market_id,
                    "provider_league": sport,
                    "provider_market_name": market_name,
                    "book": "draftkings",
                    "sport": sport,
                    "market_type": "teamnextpoints",
                    "stat": "nextpoints",
                    "player_name": "",
                    "line": "",
                    "home_team": home_team,
                    "away_team": away_team,
                    "over_odds": _selection_display_odds(priced[0]),
                    "under_odds": _selection_display_odds(priced[1]),
                    "extra_outcomes": _selection_outcomes_json(priced),
                    "updated_at": updated_at,
                    "period": period,
                    "event_date": event.get("startEventDate") or "",
                    "question": market_name,
                }
            )
            continue

        if market_name.lower() == "double chance":
            priced = [selection for selection in selections if _selection_display_odds(selection) is not None]
            if len(priced) < 2:
                continue
            rows.append(
                {
                    "provider": "draftkings",
                    "provider_event_id": event_id,
                    "provider_market_id": market_id,
                    "provider_league": sport,
                    "provider_market_name": market_name,
                    "book": "draftkings",
                    "sport": sport,
                    "market_type": "double_chance",
                    "stat": "doublechance",
                    "player_name": "",
                    "line": "",
                    "home_team": home_team,
                    "away_team": away_team,
                    "over_odds": _selection_display_odds(priced[0]),
                    "under_odds": _selection_display_odds(priced[1]),
                    "extra_outcomes": _selection_outcomes_json(priced),
                    "updated_at": updated_at,
                    "period": period,
                    "event_date": event.get("startEventDate") or "",
                    "question": market_name,
                }
            )
            continue

        if (
            "spread" in market_name.lower()
            or "spread" in market_type_name.lower()
            or "handicap" in market_name.lower()
            or "handicap" in market_type_name.lower()
        ):
            selection_groups: list[list[dict[str, Any]]] = []
            seen_spread_pairs: set[tuple[float, float]] = set()

            def _append_spread_group(grouped: list[dict[str, Any]]) -> None:
                home_selection = next(
                    (
                        selection
                        for selection in grouped
                        if any(
                            str(participant.get("venueRole") or "").strip().lower() == "home"
                            for participant in selection.get("participants") or []
                        )
                    ),
                    None,
                )
                away_selection = next(
                    (
                        selection
                        for selection in grouped
                        if any(
                            str(participant.get("venueRole") or "").strip().lower() == "away"
                            for participant in selection.get("participants") or []
                        )
                    ),
                    None,
                )
                if not home_selection or not away_selection:
                    return
                home_points = _selection_points(home_selection)
                away_points = _selection_points(away_selection)
                if home_points is None or away_points is None:
                    return
                pair_key = (float(home_points), float(away_points))
                if pair_key in seen_spread_pairs:
                    return
                seen_spread_pairs.add(pair_key)
                selection_groups.append([home_selection, away_selection])

            mainpoint_group = [
                selection
                for selection in selections
                if _selection_points(selection) is not None
                and "MainPointLine" in (selection.get("tags") or [])
            ]
            if len(mainpoint_group) == 2:
                _append_spread_group(mainpoint_group)
            selections_by_line: dict[float, list[dict[str, Any]]] = {}
            for selection in selections:
                points = _selection_points(selection)
                if points is None:
                    continue
                selections_by_line.setdefault(abs(points), []).append(selection)
            for grouped in selections_by_line.values():
                _append_spread_group(grouped)

            for grouped in selection_groups:
                home_selection, away_selection = grouped
                spread_line = abs(float(_selection_points(home_selection) or 0.0))

                home_odds = _selection_display_odds(home_selection)
                away_odds = _selection_display_odds(away_selection)
                home_points = _selection_points(home_selection)
                away_points = _selection_points(away_selection)
                if (
                    home_odds is None
                    or away_odds is None
                    or home_points is None
                    or away_points is None
                ):
                    continue

                rows.append(
                    {
                        "provider": "draftkings",
                        "provider_event_id": event_id,
                        "provider_market_id": f"{market_id}:{home_points}",
                        "provider_league": sport,
                        "provider_market_name": market_name,
                        "book": "draftkings",
                        "sport": sport,
                        "market_type": "game_spread",
                        "stat": "spread",
                        "player_name": "",
                        "line": spread_line,
                        "home_spread": home_points,
                        "away_spread": away_points,
                        "home_team": home_team,
                        "away_team": away_team,
                        "over_odds": home_odds,
                        "under_odds": away_odds,
                        "updated_at": updated_at,
                        "period": period,
                        "event_date": event.get("startEventDate") or "",
                        "question": market_name,
                    }
            )
            continue

        if "moneyline" in market_name.lower():
            home_selection = next(
                (
                    selection
                    for selection in selections
                    if str(selection.get("outcomeType") or "").strip().lower() == "home"
                ),
                None,
            )
            away_selection = next(
                (
                    selection
                    for selection in selections
                    if str(selection.get("outcomeType") or "").strip().lower() == "away"
                ),
                None,
            )
            draw_selection = next(
                (
                    selection
                    for selection in selections
                    if str(selection.get("outcomeType") or "").strip().lower() in {"tie", "draw"}
                    or _selection_label(selection).lower() == "draw"
                ),
                None,
            )
            if not home_selection or not away_selection:
                continue
            home_odds = _selection_display_odds(home_selection)
            away_odds = _selection_display_odds(away_selection)
            if home_odds is None or away_odds is None:
                continue
            draw_odds = _selection_display_odds(draw_selection) if draw_selection else None
            moneyline_selections = [home_selection]
            if draw_selection:
                moneyline_selections.append(draw_selection)
            moneyline_selections.append(away_selection)
            rows.append(
                {
                    "provider": "draftkings",
                    "provider_event_id": event_id,
                    "provider_market_id": market_id,
                    "provider_league": sport,
                    "provider_market_name": market_name,
                    "book": "draftkings",
                    "sport": sport,
                    "market_type": "halftime_result" if period == "1H" else "game_winner",
                    "stat": "winner",
                    "player_name": "",
                    "line": "",
                    "home_team": home_team,
                    "away_team": away_team,
                    "over_odds": home_odds,
                    "under_odds": away_odds,
                    "draw_odds": draw_odds if draw_odds is not None else "",
                    "extra_outcomes": _selection_outcomes_json(moneyline_selections),
                    "updated_at": updated_at,
                    "period": period,
                    "event_date": event.get("startEventDate") or "",
                    "question": event.get("name") or market_name,
                }
            )

    return rows


def parse_payload(
    payload: dict[str, Any],
    sport: str,
    *,
    event_lookup: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    if isinstance(payload.get("markets"), list) and isinstance(payload.get("selections"), list):
        return _parse_controldata_payload(payload, sport, event_lookup=event_lookup)

    rows: list[dict[str, Any]] = []
    events = _event_map(payload)
    provider_market_name = str(((payload.get("eventGroup") or payload).get("name") or "")).strip()

    for subcategory_name, bundle in _iter_offer_bundles(payload.get("eventGroup") or payload):
        offer_groups = bundle.get("offers") or []
        for offer_group in offer_groups:
            offers = offer_group if isinstance(offer_group, list) else [offer_group]
            for offer in offers:
                outcomes = offer.get("outcomes") or []
                offer_label = str(offer.get("label") or offer.get("criterionName") or "").strip()
                market_type = _market_family(subcategory_name, outcomes, offer_label)
                if market_type is None or len(outcomes) < 2:
                    continue

                event_id = str(offer.get("eventId") or offer.get("eventGroupId") or "").strip()
                event = events.get(event_id, {})
                home_team, away_team = _event_teams(event)
                updated_at = (
                    offer.get("lastUpdated")
                    or event.get("startDate")
                    or ""
                )

                if market_type in {"player_over_under", "game_total"}:
                    over = next((item for item in outcomes if str(item.get("label") or "").lower() == "over"), None)
                    under = next((item for item in outcomes if str(item.get("label") or "").lower() == "under"), None)
                    if not over or not under:
                        continue
                    line = _parse_line(over.get("line") or under.get("line"))
                    over_odds = _parse_american(over.get("oddsAmerican"))
                    under_odds = _parse_american(under.get("oddsAmerican"))
                    if over_odds is None or under_odds is None:
                        continue
                    rows.append(
                        {
                            "provider": "draftkings",
                            "provider_event_id": event_id,
                            "provider_market_id": offer.get("offerId") or offer.get("id") or "",
                            "provider_league": sport,
                            "provider_market_name": subcategory_name or provider_market_name,
                            "book": "draftkings",
                            "sport": sport,
                            "market_type": market_type,
                            "stat": _normalize_stat(subcategory_name),
                            "player_name": offer_label if market_type == "player_over_under" else "",
                            "line": line if line is not None else "",
                            "home_team": home_team,
                            "away_team": away_team,
                            "over_odds": over_odds,
                            "under_odds": under_odds,
                            "updated_at": updated_at,
                            "period": "",
                            "event_date": event.get("startDate") or "",
                            "question": offer.get("label") or subcategory_name or "",
                        }
                    )
                elif market_type == "game_winner":
                    over = outcomes[0]
                    under = outcomes[1]
                    over_odds = _parse_american(over.get("oddsAmerican"))
                    under_odds = _parse_american(under.get("oddsAmerican"))
                    if over_odds is None or under_odds is None:
                        continue
                    rows.append(
                        {
                            "provider": "draftkings",
                            "provider_event_id": event_id,
                            "provider_market_id": offer.get("offerId") or offer.get("id") or "",
                            "provider_league": sport,
                            "provider_market_name": subcategory_name or provider_market_name,
                            "book": "draftkings",
                            "sport": sport,
                            "market_type": market_type,
                            "stat": "winner",
                            "player_name": "",
                            "line": "",
                            "home_team": home_team,
                            "away_team": away_team,
                            "over_odds": over_odds,
                            "under_odds": under_odds,
                            "updated_at": updated_at,
                            "period": "",
                            "event_date": event.get("startDate") or "",
                            "question": event.get("name") or subcategory_name or "",
                        }
                    )
    return rows


def _default_urls(sport: str) -> list[str]:
    eventgroup = SPORT_TO_EVENTGROUP.get(sport)
    if not eventgroup:
        return []
    return [
        f"https://sportsbook.draftkings.com/sites/US-NJ-SB/api/v5/eventgroups/{eventgroup}?format=json",
        f"https://sportsbook.draftkings.com/sites/US-SB/api/v5/eventgroups/{eventgroup}?format=json",
    ]


def _is_empty_primary_payload(payload: Any) -> bool:
    if not isinstance(payload, dict) or "primary_markets" not in payload:
        return False
    if payload.get("league_subcategories"):
        return False
    primary_payload = payload.get("primary_markets") or {}
    if not isinstance(primary_payload, dict):
        return False
    return not (
        primary_payload.get("markets")
        or primary_payload.get("selections")
        or primary_payload.get("events")
    )


def _missing_league_subcategory_payloads(sport: str, payload: Any) -> bool:
    if not (
        sport in SPORTS_WITH_LEAGUE_SUBCATEGORY_FEEDS
        and isinstance(payload, dict)
        and "primary_markets" in payload
    ):
        return False
    league_subcategories = payload.get("league_subcategories")
    if not isinstance(league_subcategories, dict) or not league_subcategories:
        return True
    if sport == "nba":
        expected_keys = set(NBA_LEAGUE_SUBCATEGORY_IDS)
    elif sport == "wnba":
        expected_keys = set(WNBA_LEAGUE_SUBCATEGORY_IDS)
    elif sport == "mlb":
        expected_keys = set(MLB_LEAGUE_SUBCATEGORY_IDS)
    elif sport == "ncaabb":
        expected_keys = set(NCAABB_LEAGUE_SUBCATEGORY_IDS)
    elif sport == "nhl":
        expected_keys = set(NHL_LEAGUE_SUBCATEGORY_IDS)
    elif sport == "soccer":
        expected_keys = {
            f"{league_key}:{subcategory_key}"
            for league_key in SOCCER_LEAGUE_IDS
            for subcategory_key in SOCCER_LEAGUE_SUBCATEGORY_IDS
        }
    elif sport == "golf":
        expected_keys = {
            f"{league_key}:{subcategory_key}"
            for league_key in GOLF_LEAGUE_IDS
            for subcategory_key in GOLF_LEAGUE_SUBCATEGORY_IDS
        }
    elif sport == "ufc":
        expected_keys = set(UFC_LEAGUE_SUBCATEGORY_IDS)
    else:
        expected_keys = set()
    return bool(expected_keys - set(league_subcategories))


def _missing_event_subcategory_payloads(sport: str, payload: Any) -> bool:
    if not (isinstance(payload, dict) and "primary_markets" in payload):
        return False
    if sport == "nba":
        event_subcategories = payload.get("first_quarter_spread_by_event")
        if not isinstance(event_subcategories, dict) or not event_subcategories:
            return True
        found_keys = {
            str(key).split(":", 1)[1]
            for key in event_subcategories
            if ":" in str(key)
        }
        return bool(set(NBA_PLAYER_SUBCATEGORY_IDS) - found_keys)
    if sport == "wnba":
        event_subcategories = payload.get("event_subcategories")
        return not isinstance(event_subcategories, dict) or not event_subcategories
    if sport not in {"mlb", "ncaabb"}:
        return False
    event_subcategories = payload.get("event_subcategories")
    return not isinstance(event_subcategories, dict) or not event_subcategories


def _fetch_live_nash_sport_payloads(
    sport: str,
    *,
    proxy_url: str | None,
    impersonate: str,
    market_scope: str = "all",
    target_team_pairs: set[tuple[str, str]] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    market_scope = _normalize_market_scope(market_scope)
    if sport == "golf":
        all_rows: list[dict[str, Any]] = []
        golf_leagues: dict[str, Any] = {}
        flattened_subcategories: dict[str, Any] = {}
        for league_key, league_id in GOLF_LEAGUE_IDS.items():
            merged_event_map: dict[str, dict[str, Any]] = {}
            league_subcategory_payloads: dict[str, Any] = {}
            for subcategory_key, subcategory_id in _league_subcategory_items_for_scope(
                sport,
                GOLF_LEAGUE_SUBCATEGORY_IDS,
                market_scope,
            ):
                payload = get_browser_like_json(
                    _league_subcategory_url(league_id, subcategory_id),
                    headers=_nash_headers(feature="leagueSubcategory", page="league"),
                    proxy_url=proxy_url,
                    impersonate=impersonate,
                )
                payload_key = f"{league_key}:{subcategory_key}"
                league_subcategory_payloads[payload_key] = payload
                flattened_subcategories[payload_key] = payload
                merged_event_map = _merge_event_maps(merged_event_map, _event_map(payload))
                all_rows.extend(parse_payload(payload, sport, event_lookup=merged_event_map))
            golf_leagues[league_key] = {
                "league_id": league_id,
                "league_subcategories": league_subcategory_payloads,
            }
        return all_rows, {
            "primary_markets": {},
            "league_subcategories": flattened_subcategories,
            "golf_leagues": golf_leagues,
        }

    if sport == "soccer":
        subcategory_items = _league_subcategory_items_for_scope(
            sport,
            SOCCER_LEAGUE_SUBCATEGORY_IDS,
            market_scope,
        )
        if target_team_pairs:
            all_rows: list[dict[str, Any]] = []
            soccer_leagues: dict[str, Any] = {}
            flattened_subcategories: dict[str, Any] = {}
            matched_event_total = 0
            discovery_subcategory_id = SOCCER_LEAGUE_SUBCATEGORY_IDS.get("moneyline")
            if discovery_subcategory_id is None and subcategory_items:
                discovery_subcategory_id = int(subcategory_items[0][1])

            for league_key, league_id in SOCCER_LEAGUE_IDS.items():
                merged_event_map: dict[str, dict[str, Any]] = {}
                league_subcategory_payloads: dict[str, Any] = {}
                event_subcategory_payloads: dict[str, Any] = {}
                target_event_ids: list[str] = []

                if discovery_subcategory_id is not None:
                    discovery_payload = get_browser_like_json(
                        _league_subcategory_url(league_id, discovery_subcategory_id),
                        headers=_nash_headers(feature="leagueSubcategory", page="league"),
                        proxy_url=proxy_url,
                        impersonate=impersonate,
                    )
                    discovery_key = f"{league_key}:discovery"
                    league_subcategory_payloads[discovery_key] = discovery_payload
                    flattened_subcategories[discovery_key] = discovery_payload
                    merged_event_map = _merge_event_maps(merged_event_map, _event_map(discovery_payload))
                    target_event_ids = [
                        event_id
                        for event_id, event in merged_event_map.items()
                        if _event_team_pair(event) in target_team_pairs
                    ]

                if target_event_ids:
                    matched_event_total += len(target_event_ids)
                    for event_id in target_event_ids:
                        for subcategory_key, subcategory_id in subcategory_items:
                            payload = get_browser_like_json(
                                _event_subcategory_url(event_id, subcategory_id),
                                headers=_nash_headers(feature="eventSubcategory", page="event"),
                                proxy_url=proxy_url,
                                impersonate=impersonate,
                            )
                            payload_key = f"{event_id}:{subcategory_key}"
                            event_subcategory_payloads[payload_key] = payload
                            all_rows.extend(parse_payload(payload, sport, event_lookup=merged_event_map))

                soccer_leagues[league_key] = {
                    "league_id": league_id,
                    "league_subcategories": league_subcategory_payloads,
                    "event_subcategories": event_subcategory_payloads,
                    "target_event_ids": target_event_ids,
                }

            if matched_event_total:
                return all_rows, {
                    "primary_markets": {},
                    "league_subcategories": flattened_subcategories,
                    "soccer_leagues": soccer_leagues,
                    "mode": "targeted",
                }

        all_rows: list[dict[str, Any]] = []
        soccer_leagues: dict[str, Any] = {}
        flattened_subcategories: dict[str, Any] = {}
        for league_key, league_id in SOCCER_LEAGUE_IDS.items():
            merged_event_map: dict[str, dict[str, Any]] = {}
            league_subcategory_payloads: dict[str, Any] = {}
            for subcategory_key, subcategory_id in subcategory_items:
                payload = get_browser_like_json(
                    _league_subcategory_url(league_id, subcategory_id),
                    headers=_nash_headers(feature="leagueSubcategory", page="league"),
                    proxy_url=proxy_url,
                    impersonate=impersonate,
                )
                payload_key = f"{league_key}:{subcategory_key}"
                league_subcategory_payloads[payload_key] = payload
                flattened_subcategories[payload_key] = payload
                merged_event_map = _merge_event_maps(merged_event_map, _event_map(payload))
                all_rows.extend(parse_payload(payload, sport, event_lookup=merged_event_map))
            soccer_leagues[league_key] = {
                "league_id": league_id,
                "league_subcategories": league_subcategory_payloads,
            }
        return all_rows, {
            "primary_markets": {},
            "league_subcategories": flattened_subcategories,
            "soccer_leagues": soccer_leagues,
        }

    if sport == "ufc":
        league_id = SPORT_TO_EVENTGROUP.get("ufc")
        all_rows: list[dict[str, Any]] = []
        merged_event_map: dict[str, dict[str, Any]] = {}
        league_subcategory_payloads: dict[str, Any] = {}
        for key, subcategory_id in _league_subcategory_items_for_scope(
            sport,
            UFC_LEAGUE_SUBCATEGORY_IDS,
            market_scope,
        ):
            payload = get_browser_like_json(
                _league_subcategory_url(league_id, subcategory_id),
                headers=_nash_headers(feature="leagueSubcategory", page="league"),
                proxy_url=proxy_url,
                impersonate=impersonate,
            )
            league_subcategory_payloads[key] = payload
            merged_event_map = _merge_event_maps(merged_event_map, _event_map(payload))
            all_rows.extend(parse_payload(payload, sport, event_lookup=merged_event_map))
        return all_rows, {
            "primary_markets": {},
            "league_subcategories": league_subcategory_payloads,
        }

    league_id = SPORT_TO_EVENTGROUP.get(sport)
    if not league_id:
        return [], {"error": f"unsupported live DraftKings sport: {sport}"}
    primary_subcategory_id = PRIMARY_MARKETS_SUBCATEGORY_IDS.get(sport, 4511)

    raw_payloads: dict[str, Any] = {}
    all_rows: list[dict[str, Any]] = []

    primary_payload = get_browser_like_json(
        _league_subcategory_url(league_id, primary_subcategory_id),
        headers=_nash_headers(feature="leagueSubcategory", page="league"),
        proxy_url=proxy_url,
        impersonate=impersonate,
    )
    raw_payloads["primary_markets"] = primary_payload
    primary_event_map = _event_map(primary_payload)
    all_rows.extend(parse_payload(primary_payload, sport))

    if sport == "nba":
        merged_event_map = dict(primary_event_map)
        league_subcategory_payloads: dict[str, Any] = {}
        for key, subcategory_id in _league_subcategory_items_for_scope(
            sport,
            NBA_LEAGUE_SUBCATEGORY_IDS,
            market_scope,
        ):
            payload = get_browser_like_json(
                _league_subcategory_url(league_id, subcategory_id),
                headers=_nash_headers(feature="leagueSubcategory", page="league"),
                proxy_url=proxy_url,
                impersonate=impersonate,
            )
            league_subcategory_payloads[key] = payload
            merged_event_map = _merge_event_maps(merged_event_map, _event_map(payload))
            all_rows.extend(parse_payload(payload, sport, event_lookup=merged_event_map))
        raw_payloads["league_subcategories"] = league_subcategory_payloads

        quarter_spread_payloads: dict[str, Any] = {}
        event_ids = _event_ids_for_target_games(merged_event_map, target_team_pairs)
        for key, subcategory_id in _event_subcategory_items_for_scope(
            sport,
            NBA_PLAYER_SUBCATEGORY_IDS,
            market_scope,
        ):
            for event_id in event_ids:
                payload = get_browser_like_json(
                    _event_subcategory_url(event_id, subcategory_id),
                    headers=_nash_headers(feature="eventSubcategory", page="event"),
                    proxy_url=proxy_url,
                    impersonate=impersonate,
                )
                quarter_spread_payloads[f"{event_id}:{key}"] = payload
                all_rows.extend(parse_payload(payload, sport, event_lookup=merged_event_map))
        raw_payloads["first_quarter_spread_by_event"] = quarter_spread_payloads
    elif sport == "wnba":
        merged_event_map = dict(primary_event_map)
        league_subcategory_payloads: dict[str, Any] = {}
        for key, subcategory_id in _league_subcategory_items_for_scope(
            sport,
            WNBA_LEAGUE_SUBCATEGORY_IDS,
            market_scope,
        ):
            payload = get_browser_like_json(
                _league_subcategory_url(league_id, subcategory_id),
                headers=_nash_headers(feature="leagueSubcategory", page="league"),
                proxy_url=proxy_url,
                impersonate=impersonate,
            )
            league_subcategory_payloads[key] = payload
            merged_event_map = _merge_event_maps(merged_event_map, _event_map(payload))
            all_rows.extend(parse_payload(payload, sport, event_lookup=merged_event_map))
        raw_payloads["league_subcategories"] = league_subcategory_payloads

        event_subcategory_payloads: dict[str, Any] = {}
        event_ids = _event_ids_for_target_games(merged_event_map, target_team_pairs)
        for event_id in event_ids:
            for key, subcategory_id in _event_subcategory_items_for_scope(
                sport,
                WNBA_EVENT_SUBCATEGORY_IDS,
                market_scope,
            ):
                payload_key = f"{event_id}:{key}"
                try:
                    payload = get_browser_like_json(
                        _event_subcategory_url(event_id, subcategory_id),
                        headers=_nash_headers(feature="eventSubcategory", page="event"),
                        proxy_url=proxy_url,
                        impersonate=impersonate,
                    )
                except Exception as exc:
                    event_subcategory_payloads[payload_key] = {"error": str(exc)}
                    continue
                event_subcategory_payloads[payload_key] = payload
                all_rows.extend(parse_payload(payload, sport, event_lookup=merged_event_map))
        raw_payloads["event_subcategories"] = event_subcategory_payloads
    elif sport == "mlb":
        merged_event_map = dict(primary_event_map)
        league_subcategory_payloads: dict[str, Any] = {}
        for key, subcategory_id in _league_subcategory_items_for_scope(
            sport,
            MLB_LEAGUE_SUBCATEGORY_IDS,
            market_scope,
        ):
            payload = get_browser_like_json(
                _league_subcategory_url(league_id, subcategory_id),
                headers=_nash_headers(feature="leagueSubcategory", page="league"),
                proxy_url=proxy_url,
                impersonate=impersonate,
            )
            league_subcategory_payloads[key] = payload
            merged_event_map = _merge_event_maps(merged_event_map, _event_map(payload))
            all_rows.extend(parse_payload(payload, sport, event_lookup=merged_event_map))
        raw_payloads["league_subcategories"] = league_subcategory_payloads

        event_subcategory_payloads: dict[str, Any] = {}
        event_ids = _event_ids_for_target_games(merged_event_map, target_team_pairs)
        for event_id in event_ids:
            for key, subcategory_id in _event_subcategory_items_for_scope(
                sport,
                MLB_EVENT_SUBCATEGORY_IDS,
                market_scope,
            ):
                payload_key = f"{event_id}:{key}"
                try:
                    payload = get_browser_like_json(
                        _event_subcategory_url(event_id, subcategory_id),
                        headers=_nash_headers(feature="eventSubcategory", page="event"),
                        proxy_url=proxy_url,
                        impersonate=impersonate,
                    )
                except Exception as exc:
                    event_subcategory_payloads[payload_key] = {"error": str(exc)}
                    continue
                event_subcategory_payloads[payload_key] = payload
                all_rows.extend(parse_payload(payload, sport, event_lookup=merged_event_map))
        raw_payloads["event_subcategories"] = event_subcategory_payloads
    elif sport == "ncaabb":
        merged_event_map = dict(primary_event_map)
        league_subcategory_payloads: dict[str, Any] = {}
        for key, subcategory_id in _league_subcategory_items_for_scope(
            sport,
            NCAABB_LEAGUE_SUBCATEGORY_IDS,
            market_scope,
        ):
            payload = get_browser_like_json(
                _league_subcategory_url(league_id, subcategory_id),
                headers=_nash_headers(feature="leagueSubcategory", page="league"),
                proxy_url=proxy_url,
                impersonate=impersonate,
            )
            league_subcategory_payloads[key] = payload
            merged_event_map = _merge_event_maps(merged_event_map, _event_map(payload))
            all_rows.extend(parse_payload(payload, sport, event_lookup=merged_event_map))
        raw_payloads["league_subcategories"] = league_subcategory_payloads

        event_subcategory_payloads: dict[str, Any] = {}
        event_ids = _event_ids_for_target_games(merged_event_map, target_team_pairs)
        for event_id in event_ids:
            for key, subcategory_id in _event_subcategory_items_for_scope(
                sport,
                NCAABB_EVENT_SUBCATEGORY_IDS,
                market_scope,
            ):
                payload_key = f"{event_id}:{key}"
                try:
                    payload = get_browser_like_json(
                        _event_subcategory_url(event_id, subcategory_id),
                        headers=_nash_headers(feature="eventSubcategory", page="event"),
                        proxy_url=proxy_url,
                        impersonate=impersonate,
                    )
                except Exception as exc:
                    event_subcategory_payloads[payload_key] = {"error": str(exc)}
                    continue
                event_subcategory_payloads[payload_key] = payload
                all_rows.extend(parse_payload(payload, sport, event_lookup=merged_event_map))
        raw_payloads["event_subcategories"] = event_subcategory_payloads
    elif sport == "nhl":
        merged_event_map = dict(primary_event_map)
        league_subcategory_payloads: dict[str, Any] = {}
        for key, subcategory_id in _league_subcategory_items_for_scope(
            sport,
            NHL_LEAGUE_SUBCATEGORY_IDS,
            market_scope,
        ):
            payload = get_browser_like_json(
                _league_subcategory_url(league_id, subcategory_id),
                headers=_nash_headers(feature="leagueSubcategory", page="league"),
                proxy_url=proxy_url,
                impersonate=impersonate,
            )
            league_subcategory_payloads[key] = payload
            merged_event_map = _merge_event_maps(merged_event_map, _event_map(payload))
            all_rows.extend(parse_payload(payload, sport, event_lookup=merged_event_map))
        raw_payloads["league_subcategories"] = league_subcategory_payloads

    return all_rows, raw_payloads


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
    request_config = load_request_config("draftkings")
    all_rows: list[dict[str, Any]] = []
    raw_payloads: dict[str, Any] = {}

    for sport in _normalize_sports(sports):
        sport_target_pairs = normalized_target_pairs.get(sport)
        payload = load_saved_payload("draftkings", sport) if use_saved_payloads else None
        if (
            _missing_league_subcategory_payloads(sport, payload)
            or _missing_event_subcategory_payloads(sport, payload)
            or _is_empty_primary_payload(payload)
        ):
            payload = None
        if payload is None:
            headers = dict(request_config.get("headers") or {})
            sport_config = (request_config.get("sports") or {}).get(sport) or {}
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
            use_live_nash = not (sport_config.get("urls") or headers)
            if use_live_nash:
                try:
                    rows, raw = _fetch_live_nash_sport_payloads(
                        sport,
                        proxy_url=proxy_url,
                        impersonate=impersonate,
                        market_scope=market_scope,
                        target_team_pairs=sport_target_pairs,
                    )
                    raw_payloads[sport] = raw
                    all_rows.extend(_filter_rows_by_market_scope(rows, market_scope))
                    if save_payloads and market_scope == "all":
                        save_payload("draftkings", sport, raw)
                    continue
                except Exception as exc:
                    raw_payloads[sport] = {"error": str(exc)}
                    continue

            urls = list(sport_config.get("urls") or [])
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
                    if save_payloads and market_scope == "all":
                        save_payload("draftkings", sport, payload)
                    break
                except Exception as exc:
                    last_error = exc
            if payload is None and last_error is not None:
                raw_payloads[sport] = {"error": str(last_error)}
                continue
            raw_payloads[sport] = payload
            all_rows.extend(_filter_rows_by_market_scope(parse_payload(payload, sport), market_scope))
            continue

        raw_payloads[sport] = payload
        if isinstance(payload, dict) and "primary_markets" in payload:
            merged_event_map = _merge_event_maps(
                _event_map(payload.get("primary_markets") or {}),
            )
            all_rows.extend(
                _filter_rows_by_market_scope(
                    parse_payload(payload["primary_markets"], sport, event_lookup=merged_event_map),
                    market_scope,
                )
            )
            for league_payload in (payload.get("league_subcategories") or {}).values():
                merged_event_map = _merge_event_maps(merged_event_map, _event_map(league_payload or {}))
                all_rows.extend(
                    _filter_rows_by_market_scope(
                        parse_payload(league_payload, sport, event_lookup=merged_event_map),
                        market_scope,
                    )
                )
            for event_payload in (payload.get("first_quarter_spread_by_event") or {}).values():
                all_rows.extend(
                    _filter_rows_by_market_scope(
                        parse_payload(event_payload, sport, event_lookup=merged_event_map),
                        market_scope,
                    )
                )
            for event_payload in (payload.get("event_subcategories") or {}).values():
                all_rows.extend(
                    _filter_rows_by_market_scope(
                        parse_payload(event_payload, sport, event_lookup=merged_event_map),
                        market_scope,
                    )
                )
        else:
            all_rows.extend(_filter_rows_by_market_scope(parse_payload(payload, sport), market_scope))

    return all_rows, raw_payloads
