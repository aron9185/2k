from __future__ import annotations

import math
from functools import lru_cache
from typing import Any

import requests

from fair_odds import probability_to_american
from poll_market_matcher import normalize_team
from realsports_api import build_realsports_client


NBA_STATS_URL = "https://stats.nba.com/stats/leaguedashteamstats"
NBA_STATS_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Encoding": "gzip, deflate",
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "Host": "stats.nba.com",
    "Origin": "https://www.nba.com",
    "Pragma": "no-cache",
    "Referer": "https://www.nba.com/",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-site",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/136.0.0.0 Safari/537.36"
    ),
    "x-nba-stats-origin": "stats",
    "x-nba-stats-token": "true",
}

TEAM_STAT_METRICS = {
    "higher fg %": ("FG_PCT", "higher", 0.03),
    "higher 3fg %": ("FG3_PCT", "higher", 0.035),
    "higher 3fg%": ("FG3_PCT", "higher", 0.035),
    "higher 3pt %": ("FG3_PCT", "higher", 0.035),
    "higher 3-point %": ("FG3_PCT", "higher", 0.035),
    "higher ft %": ("FT_PCT", "higher", 0.05),
    "more turnovers": ("TOV", "higher", 1.75),
    "higher turnovers": ("TOV", "higher", 1.75),
    "fewer turnovers": ("TOV", "lower", 1.75),
    "more rebounds": ("REB", "higher", 4.0),
    "more assists": ("AST", "higher", 4.0),
    "more steals": ("STL", "higher", 1.5),
    "more blocks": ("BLK", "higher", 1.5),
    "more points": ("PTS", "higher", 6.0),
}

TEAM_STAT_ID_METRICS = {
    6: ("TOV", "higher", 1.75),
    9: ("FG_PCT", "higher", 0.03),
    11: ("FT_PCT", "higher", 0.05),
}

REAL_COMPARE_STAT_IDS = {
    "AST": 2,
    "STL": 4,
    "REB": 3,
    "TOV": 6,
    "FG_PCT": 9,
    "FG3_PCT": 10,
    "FT_PCT": 11,
    "PTS": 17,
    "BLK": 5,
}

PERCENT_METRICS = {"FG_PCT", "FG3_PCT", "FT_PCT"}


def _safe_float(value: Any) -> float:
    try:
        return float(value)
    except Exception:
        return 0.0


def _season_for_day(day: str) -> str:
    year = int(str(day).split("-", 1)[0])
    month = int(str(day).split("-", 2)[1])
    if month >= 10:
        return f"{year}-{str(year + 1)[-2:]}"
    return f"{year - 1}-{str(year)[-2:]}"


def _real_season_for_day(day: str) -> int:
    year = int(str(day).split("-", 1)[0])
    month = int(str(day).split("-", 2)[1])
    return year + 1 if month >= 10 else year


def _team_stats_params(season: str) -> dict[str, str]:
    return {
        "College": "",
        "Conference": "",
        "DateFrom": "",
        "DateTo": "",
        "Division": "",
        "GameScope": "",
        "GameSegment": "",
        "LastNGames": "0",
        "LeagueID": "00",
        "Location": "",
        "MeasureType": "Base",
        "Month": "0",
        "OpponentTeamID": "0",
        "Outcome": "",
        "PORound": "0",
        "PaceAdjust": "N",
        "PerMode": "PerGame",
        "Period": "0",
        "PlayerExperience": "",
        "PlayerPosition": "",
        "PlusMinus": "N",
        "Rank": "N",
        "Season": season,
        "SeasonSegment": "",
        "SeasonType": "Regular Season",
        "ShotClockRange": "",
        "StarterBench": "",
        "TeamID": "0",
        "TwoWay": "0",
        "VsConference": "",
        "VsDivision": "",
    }


@lru_cache(maxsize=8)
def fetch_nba_team_base_stats(season: str) -> dict[str, dict[str, float]]:
    session = requests.Session()
    session.trust_env = False
    response = session.get(
        NBA_STATS_URL,
        params=_team_stats_params(season),
        headers=NBA_STATS_HEADERS,
        timeout=45,
    )
    response.raise_for_status()
    payload = response.json()
    result_sets = payload.get("resultSets") or []
    if not result_sets:
        return {}
    result_set = result_sets[0]
    headers = result_set.get("headers") or []
    rows = result_set.get("rowSet") or []
    if not headers or not rows:
        return {}

    keyed_rows: dict[str, dict[str, float]] = {}
    for row in rows:
        row_dict = {
            str(header): row[index]
            for index, header in enumerate(headers)
            if index < len(row)
        }
        team_label = str(row_dict.get("TEAM_ABBREVIATION") or row_dict.get("TEAM_NAME") or "")
        team_key = normalize_team(team_label)
        if not team_key:
            continue
        keyed_rows[team_key] = {
            "PTS": _safe_float(row_dict.get("PTS")),
            "FGM": _safe_float(row_dict.get("FGM")),
            "FGA": _safe_float(row_dict.get("FGA")),
            "FG_PCT": _safe_float(row_dict.get("FG_PCT")),
            "FG3M": _safe_float(row_dict.get("FG3M")),
            "FG3A": _safe_float(row_dict.get("FG3A")),
            "FG3_PCT": _safe_float(row_dict.get("FG3_PCT")),
            "FTM": _safe_float(row_dict.get("FTM")),
            "FTA": _safe_float(row_dict.get("FTA")),
            "FT_PCT": _safe_float(row_dict.get("FT_PCT")),
            "REB": _safe_float(row_dict.get("REB")),
            "AST": _safe_float(row_dict.get("AST")),
            "STL": _safe_float(row_dict.get("STL")),
            "BLK": _safe_float(row_dict.get("BLK")),
            "TOV": _safe_float(row_dict.get("TOV")),
        }
    return keyed_rows


def _metric_context(content_text: str) -> tuple[str, str, float] | None:
    lowered = " ".join(str(content_text or "").strip().lower().split())
    for phrase, context in TEAM_STAT_METRICS.items():
        if phrase in lowered:
            return context
    return None


def _metric_context_from_ids(stat_ids: list[Any] | None) -> tuple[str, str, float] | None:
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


def _bounded_logit_prob(diff: float, scale: float) -> float:
    if scale <= 0:
        return 0.5
    z_score = diff / scale
    return 1.0 / (1.0 + math.exp(-z_score))


def _normalize_real_metric_value(metric_key: str, value: Any) -> float:
    number = _safe_float(value)
    if metric_key in PERCENT_METRICS and number > 1.0:
        return number / 100.0
    return number


def _format_metric_value(metric_key: str, value: float) -> str:
    if metric_key in PERCENT_METRICS:
        return f"{value * 100.0:.1f}%"
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
        return _normalize_real_metric_value(metric_key, item.get("value"))
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
        "nba",
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

    leader_display = _format_metric_value(metric_key, leader_value)
    trailing_display = _format_metric_value(metric_key, trailing_value)
    home_display = _format_metric_value(metric_key, home_value)
    away_display = _format_metric_value(metric_key, away_value)
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
            f"NBA official {sample_label} team-stat proxy from Real compare for {metric_key}: "
            f"{selection} {leader_display} vs {trailing_team} {trailing_display} "
            f"({sample_text})"
        ),
    }


def recommend_nba_team_stat(
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

    metric_key, direction, scale = metric_context
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
        scale=scale,
    )
    if real_recommendation is not None:
        return real_recommendation

    season = _season_for_day(day)
    team_stats = fetch_nba_team_base_stats(season)
    home_key = normalize_team(home_team)
    away_key = normalize_team(away_team)
    home_stats = team_stats.get(home_key)
    away_stats = team_stats.get(away_key)
    if not home_stats or not away_stats:
        return None

    home_value = _safe_float(home_stats.get(metric_key))
    away_value = _safe_float(away_stats.get(metric_key))
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
    else:
        selection = away_team
        fair_prob = away_prob
        leader_value = away_value
        trailing_value = home_value

    return {
        "selection": selection,
        "fair_prob": fair_prob,
        "fair_odds": probability_to_american(fair_prob),
        "metric_key": metric_key,
        "metric_direction": direction,
        "leader_value": leader_value,
        "trailing_value": trailing_value,
        "season": season,
        "notes": (
            f"NBA official season-to-date {metric_key} proxy: "
            f"{selection} {leader_value:.3f} vs {trailing_value:.3f}"
        ),
    }
