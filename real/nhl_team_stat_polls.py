from __future__ import annotations

import math
import statistics
from functools import lru_cache
from typing import Any, Callable

import requests

from fair_odds import probability_to_american
from poll_market_matcher import normalize_team
from realsports_api import build_realsports_client


NHL_STATS_BASE_URL = "https://api.nhle.com/stats/rest/en/team"
NHL_PLAYOFF_GAME_TYPE = 3

TEAM_STAT_PHRASES = {
    "higher face-off win %": ("summary", "faceoff_win_pct", "higher", 0.03),
    "higher faceoff win %": ("summary", "faceoff_win_pct", "higher", 0.03),
    "more hits": ("realtime", "hits_per_game", "higher", 5.0),
    "more penalty minutes": ("penalties", "penalty_minutes_per_game", "higher", 2.0),
    "more power play opportunities": ("powerplay", "pp_opportunities_per_game", "higher", 0.35),
    "more takeaways": ("realtime", "takeaways_per_game", "higher", 0.4),
}

TEAM_STAT_ID_METRICS = {
    6: ("summary", "faceoff_win_pct", "higher", 0.03),
    5: ("penalties", "penalty_minutes_per_game", "higher", 2.0),
    48: ("powerplay", "pp_opportunities_per_game", "higher", 0.35),
    60: ("realtime", "hits_per_game", "higher", 5.0),
    63: ("realtime", "takeaways_per_game", "higher", 0.4),
}

REAL_COMPARE_STAT_IDS = {
    "hits_per_game": 60,
}


def _safe_float(value: Any) -> float:
    try:
        return float(value)
    except Exception:
        return 0.0


def _season_id_for_day(day: str) -> int:
    year = int(str(day).split("-", 1)[0])
    month = int(str(day).split("-", 2)[1])
    season_start_year = year if month >= 9 else year - 1
    return int(f"{season_start_year}{season_start_year + 1}")


def _stats_session() -> requests.Session:
    session = requests.Session()
    session.trust_env = False
    session.headers.update(
        {
            "Accept": "application/json, text/plain, */*",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/136.0.0.0 Safari/537.36"
            ),
        }
    )
    return session


@lru_cache(maxsize=32)
def _fetch_report_rows(report: str, season_id: int, game_type_id: int | None) -> list[dict[str, Any]]:
    session = _stats_session()
    cayenne_parts = [f"seasonId={int(season_id)}"]
    if game_type_id is not None:
        cayenne_parts.append(f"gameTypeId={int(game_type_id)}")
    response = session.get(
        f"{NHL_STATS_BASE_URL}/{report}",
        params={
            "start": 0,
            "limit": 200,
            "cayenneExp": " and ".join(cayenne_parts),
        },
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    return [row for row in (payload.get("data") or []) if isinstance(row, dict)]


def _metric_context(content_text: str) -> tuple[str, str, str, float] | None:
    lowered = " ".join(str(content_text or "").strip().lower().split())
    for phrase, context in TEAM_STAT_PHRASES.items():
        if phrase in lowered:
            return context
    return None


def _metric_context_from_ids(stat_ids: list[Any] | None) -> tuple[str, str, str, float] | None:
    if not isinstance(stat_ids, list):
        return None
    for raw_value in stat_ids:
        try:
            stat_id = int(raw_value)
        except Exception:
            continue
        context = TEAM_STAT_ID_METRICS.get(stat_id)
        if context is not None:
            return context
    return None


def _metric_value(row: dict[str, Any], metric_key: str) -> float:
    games_played = max(1.0, _safe_float(row.get("gamesPlayed")))
    if metric_key == "faceoff_win_pct":
        return _safe_float(row.get("faceoffWinPct"))
    if metric_key == "hits_per_game":
        value = _safe_float(row.get("hitsPerGame"))
        if value > 0:
            return value
        return _safe_float(row.get("hits")) / games_played
    if metric_key == "penalty_minutes_per_game":
        return _safe_float(row.get("penaltyMinutes")) / games_played
    if metric_key == "pp_opportunities_per_game":
        value = _safe_float(row.get("ppOpportunitiesPerGame"))
        if value > 0:
            return value
        return _safe_float(row.get("ppOpportunities")) / games_played
    if metric_key == "takeaways_per_game":
        return _safe_float(row.get("takeaways")) / games_played
    return 0.0


def _report_values(
    rows: list[dict[str, Any]],
    metric_key: str,
) -> tuple[dict[str, float], dict[str, int]]:
    values: dict[str, float] = {}
    game_counts: dict[str, int] = {}
    for row in rows:
        team_key = normalize_team(str(row.get("teamFullName") or row.get("teamName") or row.get("teamTriCode") or ""))
        if not team_key:
            continue
        values[team_key] = _metric_value(row, metric_key)
        try:
            game_counts[team_key] = int(float(row.get("gamesPlayed") or 0))
        except Exception:
            game_counts[team_key] = 0
    return values, game_counts


def _bounded_logit_prob(diff: float, scale: float) -> float:
    if scale <= 0:
        return 0.5
    return 1.0 / (1.0 + math.exp(-(diff / scale)))


def _metric_scale(values: list[float], minimum: float) -> float:
    cleaned = [float(value) for value in values]
    if len(cleaned) < 2:
        return minimum
    try:
        spread = statistics.pstdev(cleaned)
    except Exception:
        spread = 0.0
    return max(float(minimum), float(spread) or 0.0)


def _load_metric_values(
    report: str,
    metric_key: str,
    season_id: int,
) -> tuple[dict[str, float], dict[str, int], str]:
    playoff_rows = _fetch_report_rows(report, season_id, NHL_PLAYOFF_GAME_TYPE)
    playoff_values, playoff_games = _report_values(playoff_rows, metric_key)
    if playoff_values:
        return playoff_values, playoff_games, "playoff"
    season_rows = _fetch_report_rows(report, season_id, None)
    season_values, season_games = _report_values(season_rows, metric_key)
    return season_values, season_games, "season"


def _real_season_for_day(day: str) -> int:
    year = int(str(day).split("-", 1)[0])
    month = int(str(day).split("-", 2)[1])
    return year if month >= 9 else year - 1


def _format_metric_value(metric_key: str, value: float) -> str:
    if metric_key == "faceoff_win_pct":
        number = value if value > 1.0 else value * 100.0
        return f"{number:.1f}%"
    return f"{value:.1f}".rstrip("0").rstrip(".")


def _compare_stat_value(stats: list[dict[str, Any]], metric_key: str) -> float | None:
    stat_id = REAL_COMPARE_STAT_IDS.get(metric_key)
    if stat_id is None:
        return None
    for item in stats:
        try:
            item_stat = int(item.get("stat"))
        except Exception:
            continue
        if item_stat != stat_id:
            continue
        return _safe_float(item.get("value"))
    return None


@lru_cache(maxsize=64)
def _fetch_real_compare_metric(
    *,
    first_team_id: str,
    first_team_season: str,
    first_team_season_type: str,
    second_team_id: str,
    second_team_season: str,
    second_team_season_type: str,
    metric_key: str,
) -> dict[str, Any] | None:
    if not first_team_id or not second_team_id:
        return None
    client = build_realsports_client()
    payload = client.get_team_compare(
        "nhl",
        first_team_id=first_team_id,
        first_team_season=first_team_season,
        first_team_season_type=first_team_season_type,
        second_team_id=second_team_id,
        second_team_season=second_team_season,
        second_team_season_type=second_team_season_type,
    )
    first_team = payload.get("firstTeam") or {}
    second_team = payload.get("secondTeam") or {}
    first_key = normalize_team(str(first_team.get("key") or ""))
    second_key = normalize_team(str(second_team.get("key") or ""))
    first_value = _compare_stat_value(payload.get("firstTeamCompareStats") or [], metric_key)
    second_value = _compare_stat_value(payload.get("secondTeamCompareStats") or [], metric_key)
    if not first_key or not second_key or first_value is None or second_value is None:
        return None
    first_season_stats = payload.get("firstTeamSeasonStats") or {}
    second_season_stats = payload.get("secondTeamSeasonStats") or {}
    return {
        first_key: {
            "value": first_value,
            "team": str(first_team.get("key") or ""),
            "games": int(_safe_float(first_season_stats.get("games"))),
        },
        second_key: {
            "value": second_value,
            "team": str(second_team.get("key") or ""),
            "games": int(_safe_float(second_season_stats.get("games"))),
        },
    }


def _recommend_from_real_compare(
    *,
    day: str,
    home_team: str,
    away_team: str,
    home_team_id: Any,
    away_team_id: Any,
    season: Any,
    season_type: str,
    metric_key: str,
    direction: str,
    scale: float,
) -> dict[str, Any] | None:
    if metric_key not in REAL_COMPARE_STAT_IDS:
        return None
    home_id = str(home_team_id or "").strip()
    away_id = str(away_team_id or "").strip()
    if not home_id or not away_id:
        return None
    real_season = str(season or _real_season_for_day(day))
    real_season_type = str(season_type or "").strip() or "regular"
    compare = _fetch_real_compare_metric(
        first_team_id=home_id,
        first_team_season=real_season,
        first_team_season_type=real_season_type,
        second_team_id=away_id,
        second_team_season=real_season,
        second_team_season_type=real_season_type,
        metric_key=metric_key,
    )
    if not compare:
        return None

    home_key = normalize_team(home_team)
    away_key = normalize_team(away_team)
    home_record = compare.get(home_key)
    away_record = compare.get(away_key)
    if not home_record or not away_record:
        return None

    home_value = float(home_record["value"])
    away_value = float(away_record["value"])
    diff = home_value - away_value
    if direction == "lower":
        diff *= -1.0

    home_prob = _bounded_logit_prob(diff, scale)
    away_prob = 1.0 - home_prob
    if home_prob >= away_prob:
        selection = home_team
        fair_prob = home_prob
        leader_value = home_value
        trailing_value = away_value
        trailing_team = away_team
    else:
        selection = away_team
        fair_prob = away_prob
        leader_value = away_value
        trailing_value = home_value
        trailing_team = home_team

    metric_label = {
        "hits_per_game": "hits/game",
    }.get(metric_key, metric_key)
    home_display = _format_metric_value(metric_key, home_value)
    away_display = _format_metric_value(metric_key, away_value)
    leader_display = _format_metric_value(metric_key, leader_value)
    trailing_display = _format_metric_value(metric_key, trailing_value)
    sample_label = "playoff" if real_season_type == "postseason" else real_season_type
    sample_text = (
        f"{home_team} {int(home_record.get('games') or 0)} games, "
        f"{away_team} {int(away_record.get('games') or 0)} games"
    )
    return {
        "selection": selection,
        "fair_prob": fair_prob,
        "fair_odds": probability_to_american(fair_prob),
        "metric_key": metric_key,
        "metric_direction": direction,
        "leader_value": leader_value,
        "trailing_value": trailing_value,
        "season": real_season,
        "season_type": real_season_type,
        "matched_books": 0,
        "books": "realapp",
        "sportsbook_a_label": home_team,
        "sportsbook_a_odds": home_display,
        "sportsbook_b_label": away_team,
        "sportsbook_b_odds": away_display,
        "source_lines": f"Real compare {real_season_type}: {home_team} {home_display}; {away_team} {away_display}",
        "notes": (
            f"NHL official {sample_label} team-stat proxy from Real compare for {metric_label}: "
            f"{selection} {leader_display} vs {trailing_team} {trailing_display} "
            f"({sample_text})"
        ),
    }


def recommend_nhl_team_stat(
    *,
    day: str,
    home_team: str,
    away_team: str,
    content_text: str,
    stat_ids: list[Any] | None = None,
    home_team_id: Any = "",
    away_team_id: Any = "",
    season: Any = "",
    season_type: str = "",
) -> dict[str, Any] | None:
    metric_context = _metric_context_from_ids(stat_ids) or _metric_context(content_text)
    if metric_context is None:
        return None

    report, metric_key, direction, minimum_scale = metric_context
    try:
        real_recommendation = _recommend_from_real_compare(
            day=day,
            home_team=home_team,
            away_team=away_team,
            home_team_id=home_team_id,
            away_team_id=away_team_id,
            season=season,
            season_type=season_type,
            metric_key=metric_key,
            direction=direction,
            scale=minimum_scale,
        )
    except Exception:
        real_recommendation = None
    if real_recommendation is not None:
        return real_recommendation

    season_id = _season_id_for_day(day)
    team_values, team_games, sample_type = _load_metric_values(report, metric_key, season_id)
    home_key = normalize_team(home_team)
    away_key = normalize_team(away_team)
    if home_key not in team_values or away_key not in team_values:
        return None

    home_value = float(team_values[home_key])
    away_value = float(team_values[away_key])
    diff = home_value - away_value
    if direction == "lower":
        diff *= -1.0

    scale = _metric_scale(list(team_values.values()), minimum_scale)
    home_prob = _bounded_logit_prob(diff, scale)
    away_prob = 1.0 - home_prob
    if home_prob >= away_prob:
        selection = home_team
        fair_prob = home_prob
        leader_value = home_value
        trailing_value = away_value
        selected_games = team_games.get(home_key, 0)
        other_games = team_games.get(away_key, 0)
    else:
        selection = away_team
        fair_prob = away_prob
        leader_value = away_value
        trailing_value = home_value
        selected_games = team_games.get(away_key, 0)
        other_games = team_games.get(home_key, 0)

    metric_label = {
        "faceoff_win_pct": "face-off win%",
        "hits_per_game": "hits/game",
        "penalty_minutes_per_game": "penalty minutes/game",
        "pp_opportunities_per_game": "power-play opportunities/game",
        "takeaways_per_game": "takeaways/game",
    }.get(metric_key, metric_key)

    return {
        "selection": selection,
        "fair_prob": fair_prob,
        "fair_odds": probability_to_american(fair_prob),
        "notes": (
            f"NHL official {sample_type} {metric_label} proxy: "
            f"{selection} {leader_value:.2f} vs {trailing_value:.2f} "
            f"({selected_games} gp vs {other_games} gp)"
        ),
    }
