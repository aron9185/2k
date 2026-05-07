from __future__ import annotations

import json
from typing import Any, Iterable, Sequence

from fair_odds import american_to_implied_prob, probability_to_american
from sportsbook_http import (
    SportsbookFetchBlocked,
    get_browser_like_json,
    load_request_config,
    load_saved_payload,
    save_payload,
)


SPORT_TO_EVENTGROUP = {
    "mlb": 84240,
    "nba": 42648,
    "wnba": 94682,
    "nhl": 42133,
    "nfl": 88808,
}

SOCCER_LEAGUE_IDS = {
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
    "nhl": 4525,
}

NBA_LEAGUE_SUBCATEGORY_IDS = {
    "points_milestones": 16477,
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

MLB_LEAGUE_SUBCATEGORY_IDS = {
    "first_inning_runs": 11024,
    "home_runs": 17319,
    "hits": 17320,
    "total_bases": 17321,
    "hits_runs_rbis": 17843,
    "rbis": 17322,
    "stolen_bases": 18726,
    "live_home_runs": 17482,
    "live_hits": 17483,
    "live_total_bases": 17480,
    "live_hits_runs_rbis": 18773,
    "live_rbis": 17479,
    "live_runs": 17488,
    "live_hits_ou": 9502,
    "live_total_bases_ou": 9506,
    "live_hits_runs_rbis_ou": 12152,
    "live_rbis_ou": 9505,
    "pitcher_strikeouts": 17323,
    "pitcher_hits_allowed": 19457,
    "pitcher_walks_allowed": 19456,
    "pitcher_earned_runs_allowed": 19458,
    "pitcher_hits_walks_earned_runs_allowed": 19460,
    "live_pitcher_strikeouts": 17481,
    "live_pitcher_strikeouts_ou": 12960,
    "live_pitcher_hits_allowed_ou": 12962,
    "live_pitcher_walks_allowed_ou": 12963,
    "live_pitcher_outs_recorded_ou": 17476,
    "pitcher_strikeouts_ou": 15221,
    "pitcher_outs_recorded_ou": 17413,
    "pitcher_hits_allowed_ou": 9886,
    "pitcher_walks_allowed_ou": 15219,
    "pitcher_earned_runs_allowed_ou": 17412,
    "pitcher_hits_walks_earned_runs_allowed_ou": 19459,
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
    "asian_handicap": 17968,
    "first_half_asian_handicap": 17910,
    "total_goals": 13171,
    "asian_total_goals": 19542,
    "first_half_total_goals": 17903,
    "first_half_asian_total_goals": 19543,
    "both_teams_score": 5645,
    "double_chance": 8068,
    "halftime_result": 11273,
    "goalscorer": 16604,
    "player_assists": 16863,
    "player_shots_on_target": 16861,
    "goalkeeper_saves": 18346,
}

WNBA_LEAGUE_IDS = {
    "wnba": 94682,
    "wnba_preseason": 94531,
}

WNBA_LEAGUE_SUBCATEGORY_IDS = {
    "primary_markets": 4511,
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

SOCCER_EVENT_ASSIST_SUBCATEGORY_IDS = {
    "player_assists": 16863,
    "player_total_assists": 12384,
}

SOCCER_EVENT_GOAL_SUBCATEGORY_IDS = {
    "anytime_goalscorer": 10710,
}

NBA_PLAYER_SUBCATEGORY_IDS = {
    "first_quarter_spread": 16822,
}

SPORTS_WITH_LEAGUE_SUBCATEGORY_FEEDS = {"nba", "wnba", "mlb", "nhl", "soccer"}

STAT_ALIASES = {
    "hits+runs+rbis": "hitsrunsrbis",
    "hitsrunsrbis": "hitsrunsrbis",
    "hitsrunsrbisou": "hitsrunsrbis",
    "hitsrunsrbismilestones": "hitsrunsrbis",
    "strikeouts": "strikeouts",
    "strikeoutsou": "strikeouts",
    "total bases": "totalbases",
    "totalbasesou": "totalbases",
    "home runs": "homeruns",
    "hits": "hits",
    "hitsou": "hits",
    "runs": "runs",
    "runsou": "runs",
    "rbis": "rbis",
    "rbisou": "rbis",
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
    "threepointersmadeou": "madethrees",
    "threesou": "madethrees",
    "3pm": "madethrees",
    "stealsou": "steals",
    "blocksou": "blocks",
    "stealsblocksou": "stealsblocks",
    "saves": "saves",
    "savesou": "saves",
    "goalkeepersaves": "saves",
    "goalkeepersavesou": "saves",
    "shots on goal": "shots",
    "shots on target": "shots",
    "shotsongoal": "shots",
    "shotsontarget": "shots",
    "shots": "shots",
    "playershotsongoalou": "shots",
    "playerpointsou": "points",
    "playerassistsou": "assists",
    "playerblocksou": "blocks",
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
    "nextpoints": "nextpoints",
    "nextrun": "nextpoints",
    "nextgoal": "nextpoints",
    "total": "total",
    "spread": "spread",
}


def _normalize_sports(values: Sequence[str]) -> list[str]:
    return [str(value or "").strip().lower() for value in values if str(value or "").strip()]


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


def _compact_token(value: Any) -> str:
    return "".join(ch for ch in str(value or "").strip().lower() if ch.isalnum())


def _is_no_more_label(value: str) -> bool:
    token = _compact_token(value)
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


def _looks_like_next_score_market(market_name: str, market_type_name: str) -> bool:
    text = f"{market_name} {market_type_name}".lower()
    return "next" in text and any(token in text for token in ("run", "score", "goal", "point"))


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


def _selection_points(selection: dict[str, Any]) -> float | None:
    return _parse_line(selection.get("points"))


def _selection_player_name(selection: dict[str, Any]) -> str:
    for participant in selection.get("participants") or []:
        participant_type = str(participant.get("type") or "").strip().lower()
        if participant_type and participant_type != "team":
            return str(participant.get("name") or "").strip()
    return ""


def _selection_team_role(selection: dict[str, Any], home_team: str, away_team: str) -> str:
    outcome_type = str(selection.get("outcomeType") or "").strip().lower()
    if outcome_type in {"home", "away"}:
        return outcome_type

    label = _selection_label(selection)
    if _is_no_more_label(label):
        return "no_more"

    for participant in selection.get("participants") or []:
        if str(participant.get("type") or "").strip().lower() != "team":
            continue
        role = str(participant.get("venueRole") or "").strip().lower()
        if role in {"home", "away"}:
            return role

    label_token = _compact_token(label)
    home_token = _compact_token(home_team)
    away_token = _compact_token(away_team)
    if label_token and len(label_token) >= 2:
        if home_token and (label_token == home_token or label_token in home_token):
            return "home"
        if away_token and (label_token == away_token or label_token in away_token):
            return "away"
    return "other"


def _derive_player_stat(market_name: str, player_name: str) -> str:
    market_text = str(market_name or "").strip()
    player_text = str(player_name or "").strip()
    if player_text and market_text.lower().startswith(player_text.lower()):
        stat_text = market_text[len(player_text):].strip(" -")
        return _normalize_stat(stat_text)
    return _normalize_stat(market_text)


def _is_anytime_goalscorer_selection(selection: dict[str, Any], market_name: str) -> bool:
    outcome_type = str(selection.get("outcomeType") or "").strip().lower()
    if outcome_type == "toscoreanytime":
        return True
    return "anytime goalscorer" in str(market_name or "").strip().lower()


def _infer_period(market_name: str) -> str:
    lowered = str(market_name or "").lower()
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
    period_tokens = lowered.replace("-", " ").replace("/", " ").split()
    if "overtime" in period_tokens or "ot" in period_tokens:
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

    for market in payload.get("markets") or []:
        market_id = str(market.get("id") or "").strip()
        event_id = str(market.get("eventId") or "").strip()
        selections = selections_by_market.get(market_id, [])
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
            grouped_pairs: dict[tuple[str, float], dict[str, dict[str, Any]]] = {}
            for selection in selections:
                side = _selection_label(selection).lower()
                if side not in {"over", "under"}:
                    continue
                points = _selection_points(selection)
                if points is None:
                    continue
                player_name_key = _selection_player_name(selection)
                grouped_pairs.setdefault((player_name_key, float(points)), {})[side] = selection

            for (player_name, line), pair in sorted(grouped_pairs.items(), key=lambda item: (item[0][0], item[0][1])):
                over = pair.get("over")
                under = pair.get("under")
                if not over or not under:
                    continue
                over_odds = _selection_display_odds(over)
                under_odds = _selection_display_odds(under)
                if over_odds is None or under_odds is None:
                    continue
                market_type = "player_over_under" if player_name else "game_total"
                stat_key = (
                    _derive_player_stat(market_type_name or market_name, player_name)
                    if player_name
                    else _normalize_stat(market_type_name or market_name)
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

        anytime_rows_added = False
        for selection in selections:
            if not _is_anytime_goalscorer_selection(selection, market_name):
                continue
            player_name = _selection_player_name(selection)
            if not player_name:
                continue
            over_odds = _selection_display_odds(selection)
            if over_odds is None:
                continue
            anytime_rows_added = True
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
                    "line": 0.5,
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
        if anytime_rows_added:
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
            rows.append(
                {
                    "provider": "draftkings",
                    "provider_event_id": event_id,
                    "provider_market_id": market_id,
                    "provider_league": sport,
                    "provider_market_name": market_name,
                    "book": "draftkings",
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
                    "period": period,
                    "event_date": event.get("startEventDate") or "",
                    "question": market_name,
                }
            )
            continue

        if _looks_like_next_score_market(market_name, market_type_name):
            priced: list[tuple[dict[str, Any], str, int]] = []
            for selection in selections:
                odds = _selection_display_odds(selection)
                if odds is None:
                    continue
                role = _selection_team_role(selection, home_team, away_team)
                priced.append((selection, role, odds))
            has_team = any(role in {"home", "away"} for _selection, role, _odds in priced)
            has_no_more = any(role == "no_more" for _selection, role, _odds in priced)
            if has_team and has_no_more:
                role_order = {"home": 0, "away": 1, "no_more": 2}
                ordered = sorted(
                    priced,
                    key=lambda item: (
                        role_order.get(item[1], 9),
                        _selection_label(item[0]).lower(),
                    ),
                )
                team_item = next((item for item in ordered if item[1] in {"home", "away"}), None)
                no_more_item = next((item for item in ordered if item[1] == "no_more"), None)
                if team_item and no_more_item:
                    team_selection, _team_role, team_odds = team_item
                    no_more_selection, _no_more_role, no_more_odds = no_more_item
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
                            "over_odds": team_odds,
                            "under_odds": no_more_odds,
                            "extra_outcomes": _selection_outcomes_json(
                                [selection for selection, _role, _odds in ordered]
                            ),
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

        if "spread" in market_name.lower() or "spread" in market_type_name.lower():
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
        expected_keys = {
            f"{league_key}:{subcategory_key}"
            for league_key in WNBA_LEAGUE_IDS
            for subcategory_key in WNBA_LEAGUE_SUBCATEGORY_IDS
        }
    elif sport == "mlb":
        expected_keys = set(MLB_LEAGUE_SUBCATEGORY_IDS)
    elif sport == "nhl":
        expected_keys = set(NHL_LEAGUE_SUBCATEGORY_IDS)
    elif sport == "soccer":
        expected_keys = {
            f"{league_key}:{subcategory_key}"
            for league_key in SOCCER_LEAGUE_IDS
            for subcategory_key in SOCCER_LEAGUE_SUBCATEGORY_IDS
        }
    else:
        expected_keys = set()
    return bool(expected_keys - set(league_subcategories))


def _fetch_live_nash_sport_payloads(
    sport: str,
    *,
    proxy_url: str | None,
    impersonate: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if sport == "soccer":
        all_rows: list[dict[str, Any]] = []
        soccer_leagues: dict[str, Any] = {}
        flattened_subcategories: dict[str, Any] = {}
        flattened_event_subcategories: dict[str, Any] = {}
        for league_key, league_id in SOCCER_LEAGUE_IDS.items():
            merged_event_map: dict[str, dict[str, Any]] = {}
            league_subcategory_payloads: dict[str, Any] = {}
            league_rows: list[dict[str, Any]] = []
            for subcategory_key, subcategory_id in SOCCER_LEAGUE_SUBCATEGORY_IDS.items():
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
                parsed_rows = parse_payload(payload, sport, event_lookup=merged_event_map)
                all_rows.extend(parsed_rows)
                league_rows.extend(parsed_rows)

            events_with_assists = {
                str(row.get("provider_event_id") or "")
                for row in league_rows
                if row.get("market_type") == "player_over_under"
                and str(row.get("stat") or "").strip() == "assists"
            }
            events_with_goals = {
                str(row.get("provider_event_id") or "")
                for row in league_rows
                if row.get("market_type") == "player_over_under"
                and str(row.get("stat") or "").strip() == "goals"
            }
            missing_assist_events = [
                event_id
                for event_id in merged_event_map
                if event_id and event_id not in events_with_assists
            ]
            missing_goal_events = [
                event_id
                for event_id in merged_event_map
                if event_id and event_id not in events_with_goals
            ]
            event_subcategory_payloads: dict[str, Any] = {}
            for event_id in merged_event_map:
                if not event_id:
                    continue
                fallback_targets: list[tuple[str, int, str]] = []
                if event_id in missing_assist_events:
                    fallback_targets.extend(
                        (subcategory_key, subcategory_id, "assists")
                        for subcategory_key, subcategory_id in SOCCER_EVENT_ASSIST_SUBCATEGORY_IDS.items()
                    )
                if event_id in missing_goal_events:
                    fallback_targets.extend(
                        (subcategory_key, subcategory_id, "goals")
                        for subcategory_key, subcategory_id in SOCCER_EVENT_GOAL_SUBCATEGORY_IDS.items()
                    )
                for subcategory_key, subcategory_id, target_stat in fallback_targets:
                    try:
                        payload = get_browser_like_json(
                            _event_subcategory_url(event_id, subcategory_id),
                            headers=_nash_headers(feature="eventSubcategory", page="event"),
                            proxy_url=proxy_url,
                            impersonate=impersonate,
                        )
                    except Exception:
                        continue
                    payload_key = f"{league_key}:{event_id}:{subcategory_key}"
                    event_subcategory_payloads[payload_key] = payload
                    flattened_event_subcategories[payload_key] = payload
                    parsed_rows = parse_payload(payload, sport, event_lookup=merged_event_map)
                    all_rows.extend(parsed_rows)
                    has_target_stat = any(
                        str(row.get("provider_event_id") or "") == event_id
                        and row.get("market_type") == "player_over_under"
                        and str(row.get("stat") or "").strip() == target_stat
                        for row in parsed_rows
                    )
                    if has_target_stat:
                        break
            soccer_leagues[league_key] = {
                "league_id": league_id,
                "league_subcategories": league_subcategory_payloads,
                "event_subcategories": event_subcategory_payloads,
            }
        return all_rows, {
            "primary_markets": {},
            "league_subcategories": flattened_subcategories,
            "event_subcategories": flattened_event_subcategories,
            "soccer_leagues": soccer_leagues,
        }

    if sport == "wnba":
        all_rows: list[dict[str, Any]] = []
        flattened_subcategories: dict[str, Any] = {}
        wnba_leagues: dict[str, Any] = {}
        for league_key, league_id in WNBA_LEAGUE_IDS.items():
            league_payloads: dict[str, Any] = {}
            merged_event_map: dict[str, dict[str, Any]] = {}
            league_rows: list[dict[str, Any]] = []
            for subcategory_key, subcategory_id in WNBA_LEAGUE_SUBCATEGORY_IDS.items():
                payload_key = f"{league_key}:{subcategory_key}"
                try:
                    payload = get_browser_like_json(
                        _league_subcategory_url(league_id, subcategory_id),
                        headers=_nash_headers(feature="leagueSubcategory", page="league"),
                        proxy_url=proxy_url,
                        impersonate=impersonate,
                    )
                except Exception as exc:
                    payload = {"error": str(exc)}
                league_payloads[payload_key] = payload
                flattened_subcategories[payload_key] = payload
                if isinstance(payload, dict) and payload.get("error"):
                    continue
                merged_event_map = _merge_event_maps(merged_event_map, _event_map(payload))
                parsed_rows = parse_payload(payload, sport, event_lookup=merged_event_map)
                all_rows.extend(parsed_rows)
                league_rows.extend(parsed_rows)

            wnba_leagues[league_key] = {
                "league_id": league_id,
                "league_subcategories": league_payloads,
                "row_count": len(league_rows),
            }

        return all_rows, {
            "primary_markets": {},
            "league_subcategories": flattened_subcategories,
            "wnba_leagues": wnba_leagues,
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
        for key, subcategory_id in NBA_LEAGUE_SUBCATEGORY_IDS.items():
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
        for event_id in merged_event_map:
            payload = get_browser_like_json(
                _event_subcategory_url(event_id, NBA_PLAYER_SUBCATEGORY_IDS["first_quarter_spread"]),
                headers=_nash_headers(feature="eventSubcategory", page="event"),
                proxy_url=proxy_url,
                impersonate=impersonate,
            )
            quarter_spread_payloads[event_id] = payload
            all_rows.extend(parse_payload(payload, sport, event_lookup=merged_event_map))
        raw_payloads["first_quarter_spread_by_event"] = quarter_spread_payloads
    elif sport == "mlb":
        merged_event_map = dict(primary_event_map)
        league_subcategory_payloads: dict[str, Any] = {}
        for key, subcategory_id in MLB_LEAGUE_SUBCATEGORY_IDS.items():
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
    elif sport == "nhl":
        merged_event_map = dict(primary_event_map)
        league_subcategory_payloads: dict[str, Any] = {}
        for key, subcategory_id in NHL_LEAGUE_SUBCATEGORY_IDS.items():
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
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    request_config = load_request_config("draftkings")
    all_rows: list[dict[str, Any]] = []
    raw_payloads: dict[str, Any] = {}

    for sport in _normalize_sports(sports):
        payload = load_saved_payload("draftkings", sport) if use_saved_payloads else None
        if _missing_league_subcategory_payloads(sport, payload) or _is_empty_primary_payload(payload):
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
                    )
                    raw_payloads[sport] = raw
                    all_rows.extend(rows)
                    if save_payloads:
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
                    if save_payloads:
                        save_payload("draftkings", sport, payload)
                    break
                except Exception as exc:
                    last_error = exc
            if payload is None and last_error is not None:
                raw_payloads[sport] = {"error": str(last_error)}
                continue
            raw_payloads[sport] = payload
            all_rows.extend(parse_payload(payload, sport))
            continue

        raw_payloads[sport] = payload
        if isinstance(payload, dict) and "primary_markets" in payload:
            merged_event_map = _merge_event_maps(
                _event_map(payload.get("primary_markets") or {}),
            )
            all_rows.extend(parse_payload(payload["primary_markets"], sport, event_lookup=merged_event_map))
            for league_payload in (payload.get("league_subcategories") or {}).values():
                merged_event_map = _merge_event_maps(merged_event_map, _event_map(league_payload or {}))
                all_rows.extend(parse_payload(league_payload, sport, event_lookup=merged_event_map))
            for event_payload in (payload.get("event_subcategories") or {}).values():
                all_rows.extend(parse_payload(event_payload, sport, event_lookup=merged_event_map))
            for event_payload in (payload.get("first_quarter_spread_by_event") or {}).values():
                all_rows.extend(parse_payload(event_payload, sport, event_lookup=merged_event_map))
        else:
            all_rows.extend(parse_payload(payload, sport))

    return all_rows, raw_payloads
