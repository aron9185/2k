from __future__ import annotations

import math
import statistics
from functools import lru_cache
from typing import Any, Callable

import requests

from fair_odds import probability_to_american
from poll_market_matcher import normalize_team


NHL_STATS_BASE_URL = "https://api.nhle.com/stats/rest/en/team"
NHL_PLAYOFF_GAME_TYPE = 3

TEAM_STAT_PHRASES = {
    "higher face-off win %": ("summary", "faceoff_win_pct", "higher", 0.03),
    "higher faceoff win %": ("summary", "faceoff_win_pct", "higher", 0.03),
    "more penalty minutes": ("penalties", "penalty_minutes_per_game", "higher", 2.0),
    "more power play opportunities": ("powerplay", "pp_opportunities_per_game", "higher", 0.35),
    "more takeaways": ("realtime", "takeaways_per_game", "higher", 0.4),
}

TEAM_STAT_ID_METRICS = {
    6: ("summary", "faceoff_win_pct", "higher", 0.03),
    5: ("penalties", "penalty_minutes_per_game", "higher", 2.0),
    48: ("powerplay", "pp_opportunities_per_game", "higher", 0.35),
    63: ("realtime", "takeaways_per_game", "higher", 0.4),
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


def recommend_nhl_team_stat(
    *,
    day: str,
    home_team: str,
    away_team: str,
    content_text: str,
    stat_ids: list[Any] | None = None,
) -> dict[str, Any] | None:
    metric_context = _metric_context_from_ids(stat_ids) or _metric_context(content_text)
    if metric_context is None:
        return None

    report, metric_key, direction, minimum_scale = metric_context
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
