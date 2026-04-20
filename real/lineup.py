import argparse
import csv
import json
import time
import unicodedata
from datetime import date as date_cls, datetime
from pathlib import Path

import requests

from read_real_player import (
    build_real_id_rows_from_ranking_items,
    fetch_ranking_items,
    ranking_urls_for_sport,
    write_real_id_csv,
)
from realsports_api import RealSportsAuthError, RealSportsError, build_realsports_client


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_SPORT = "nba"  # 'mlb', 'nhl', 'nba', 'ncaam', 'soccer', 'wnba', 'ncaaf', 'nfl', 'golf'
DEFAULT_DATE = "2026-04-20"
DEFAULT_SEASON = "2025"
DEFAULT_FANTASY_POINTS_FILE = str(BASE_DIR / "fantasy_points.json")
DEFAULT_LINEUP_FILE = str(BASE_DIR / "lineup.csv")
DEFAULT_ROTOWIRE_SITE = "auto"
DEFAULT_REAL_ID_FILE = str(BASE_DIR / "real_id.csv")
DEFAULT_MULTIPLIER_CACHE_DIR = str(BASE_DIR / ".cache" / "realsports_multiplier")

ROTOWIRE_BASE_URL = "https://www.rotowire.com"
ROTOWIRE_TIMEOUT = 20
ROTOWIRE_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/135.0.0.0 Safari/537.36"
)

ROTOWIRE_SPORT_SLUGS = {
    "mlb": "mlb",
    "nhl": "nhl",
    "nba": "nba",
    "ncaam": "cbb",
    "soccer": "soccer",
    "wnba": "wnba",
    "ncaaf": "cfb",
    "nfl": "nfl",
    "golf": "golf",
}

ROTOWIRE_SITES = {
    "DraftKings": 1,
    "FanDuel": 2,
}
ROTOWIRE_SITE_PRIORITY = ["DraftKings", "FanDuel"]
SHOWDOWN_CONTEST_KEYWORDS = ("showdown", "single", "tiers")
SHOWDOWN_POSITION_TOKENS = {
    "CPTN",
    "CHP",
    "MVP",
    "STAR",
    "PRO",
    "MEGASTAR",
    "SUPERSTAR",
}

def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Pull live Rotowire optimizer projections, choose the best same-book "
            "slate coverage for the requested date, then apply Real Sports multipliers."
        )
    )
    parser.add_argument("--sport", default=DEFAULT_SPORT, choices=sorted(ROTOWIRE_SPORT_SLUGS))
    parser.add_argument("--date", default=DEFAULT_DATE, help="Target slate date in YYYY-MM-DD format.")
    parser.add_argument("--season", default=DEFAULT_SEASON, help="Real Sports season key, e.g. 2025.")
    parser.add_argument(
        "--site",
        default=DEFAULT_ROTOWIRE_SITE,
        choices=["auto", *ROTOWIRE_SITE_PRIORITY],
        help="Lock to one sportsbook scoring system, or auto-pick the widest same-book coverage.",
    )
    parser.add_argument(
        "--fantasy-points-file",
        default=DEFAULT_FANTASY_POINTS_FILE,
        help="Where to write the normalized Rotowire projection cache.",
    )
    parser.add_argument(
        "--real-id-csv",
        default=DEFAULT_REAL_ID_FILE,
        help="Lookup file for Real Sports player ids and exact names.",
    )
    parser.add_argument(
        "--multiplier-cache-dir",
        default=DEFAULT_MULTIPLIER_CACHE_DIR,
        help="Directory for persistent per-day multiplier cache files.",
    )
    parser.add_argument(
        "--output",
        default=DEFAULT_LINEUP_FILE,
        help="Where to write the adjusted lineup summary.",
    )
    parser.add_argument(
        "--skip-real-id-refresh",
        action="store_true",
        help="Reuse the existing real_id.csv instead of refreshing it from Real Sports.",
    )
    parser.add_argument(
        "--skip-multiplier",
        action="store_true",
        help="Skip Real Sports multiplier lookups and use Rotowire projections only.",
    )
    return parser.parse_args()


def build_rotowire_session():
    session = requests.Session()
    session.trust_env = False
    session.headers.update(
        {
            "accept": "application/json",
            "user-agent": ROTOWIRE_USER_AGENT,
            "referer": ROTOWIRE_BASE_URL,
        }
    )
    return session


def rotowire_api_base(sport):
    slug = ROTOWIRE_SPORT_SLUGS.get(sport)
    if not slug:
        raise ValueError(f"Unsupported RotoWire sport: {sport}")
    return f"{ROTOWIRE_BASE_URL}/daily/{slug}/api"


def rotowire_get_json(session, sport, endpoint, params=None):
    response = session.get(
        f"{rotowire_api_base(sport)}/{endpoint}",
        params=params,
        timeout=ROTOWIRE_TIMEOUT,
    )
    response.raise_for_status()
    return response.json()


def normalize_name(value):
    text = unicodedata.normalize("NFKD", value or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    cleaned = []
    for char in text.lower():
        if char.isalnum() or char.isspace():
            cleaned.append(char)
        else:
            cleaned.append(" ")
    return " ".join("".join(cleaned).split())


def normalize_team_abbr(value):
    return (value or "").strip().upper()


def ensure_parent_dir(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)


def load_real_player_index(path):
    index = {}
    csv_path = Path(path)
    if not csv_path.exists():
        return index

    with csv_path.open("r", encoding="utf8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            exact_name = (row.get("Name") or "").strip()
            normalized = (row.get("normalized_name") or "").strip() or normalize_name(exact_name)
            if not exact_name or not normalized:
                continue

            entry = {
                "id": str(row.get("id", "")).strip(),
                "Name": exact_name,
                "firstName": (row.get("firstName") or "").strip(),
                "lastName": (row.get("lastName") or "").strip(),
                "normalized_name": normalized,
            }
            index.setdefault(normalized, []).append(entry)

    return index


def real_player_index_has_rows(index):
    return any(index.values())


def ensure_real_id_csv_from_ranking_rows(real_id_csv, ranking_rows):
    fallback_rows = build_real_id_rows_from_ranking_items(ranking_rows)
    if not fallback_rows:
        return 0
    write_real_id_csv(real_id_csv, fallback_rows)
    return len(fallback_rows)


def extract_candidate_team_abbr(candidate):
    team = candidate.get("team")
    if isinstance(team, dict):
        for key in ("abbr", "abbreviation", "teamAbbr", "shortName", "code"):
            value = team.get(key)
            if value:
                return normalize_team_abbr(value)
    if isinstance(team, str):
        return normalize_team_abbr(team)

    for key in ("teamAbbr", "team_abbr", "abbr", "team"):
        value = candidate.get(key)
        if isinstance(value, str) and value.strip():
            return normalize_team_abbr(value)
    return ""


def split_position_text(value):
    if isinstance(value, list):
        items = value
    else:
        items = str(value or "").replace("-", "/").replace(",", "/").split("/")
    return {item.strip().upper() for item in items if str(item).strip()}


def extract_candidate_positions(candidate):
    for key in ("positions", "pos", "position", "displayPos", "rotoPos"):
        value = candidate.get(key)
        if value:
            positions = split_position_text(value)
            if positions:
                return positions
    return set()


def record_position_tokens(record):
    return {pos.strip().upper() for pos in (record.get("pos") or []) if str(pos).strip()}


def real_id_cache_keys(record, real_entries):
    keys = []
    for entry in real_entries:
        player_id = entry.get("id", "").strip()
        if player_id:
            keys.append(f"id:{player_id}")

    fallback = f"name:{normalize_name(player_full_name(record))}|team:{normalize_team_abbr((record.get('team') or {}).get('abbr', ''))}"
    keys.append(fallback)
    return keys


def load_multiplier_cache(cache_dir, sport, date):
    cache_path = Path(cache_dir) / f"{sport}_{date}.json"
    if not cache_path.exists():
        return cache_path, {}

    try:
        return cache_path, json.loads(cache_path.read_text(encoding="utf8"))
    except (OSError, json.JSONDecodeError):
        return cache_path, {}


def save_multiplier_cache(cache_path, cache):
    ensure_parent_dir(cache_path)
    cache_path.write_text(
        json.dumps(cache, indent=2, ensure_ascii=False),
        encoding="utf8",
    )


def candidate_full_name(candidate):
    first = (candidate.get("firstName") or "").strip()
    last = (candidate.get("lastName") or "").strip()
    return f"{first} {last}".strip()


def score_multiplier_candidate(candidate, record, real_entries, query_name):
    score = 0
    candidate_id = str(candidate.get("id", "")).strip()
    candidate_name = normalize_name(candidate_full_name(candidate))
    record_name = normalize_name(player_full_name(record))
    query_normalized = normalize_name(query_name)
    real_entry_ids = {entry.get("id", "").strip() for entry in real_entries if entry.get("id")}
    real_entry_names = {entry.get("normalized_name", "") for entry in real_entries if entry.get("normalized_name")}

    if candidate_id and candidate_id in real_entry_ids:
        score += 1000
    if candidate_name == record_name:
        score += 200
    if candidate_name == query_normalized:
        score += 120
    if candidate_name in real_entry_names:
        score += 120
    if candidate_name and (candidate_name.startswith(record_name) or record_name.startswith(candidate_name)):
        score += 25

    record_team = normalize_team_abbr((record.get("team") or {}).get("abbr", ""))
    candidate_team = extract_candidate_team_abbr(candidate)
    if record_team and candidate_team and record_team == candidate_team:
        score += 80

    overlap = record_position_tokens(record) & extract_candidate_positions(candidate)
    score += 20 * len(overlap)
    return score


def parse_iso_date(value):
    return date_cls.fromisoformat(str(value))


def parse_iso_datetime(value):
    return datetime.fromisoformat(str(value))


def player_full_name(record):
    return f"{record.get('firstName', '').strip()} {record.get('lastName', '').strip()}".strip()


def safe_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def safe_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def build_game_key(record):
    team = (record.get("team") or {}).get("abbr", "")
    opponent = (record.get("opponent") or {}).get("team", "")
    game_time = (record.get("game") or {}).get("dateTime", "")
    if not team or not opponent or not game_time:
        return None
    if record.get("isHome"):
        away_team, home_team = opponent, team
    else:
        away_team, home_team = team, opponent
    return f"{game_time}|{away_team}@{home_team}"


def contest_preference_score(contest_type):
    label = (contest_type or "").strip().lower()
    if "classic" in label or "main" in label or "full roster" in label:
        return 3
    if "full" in label:
        return 2
    return 1


def is_standard_projection_slate(contest_type, players):
    label = (contest_type or "").strip().lower()
    if any(keyword in label for keyword in SHOWDOWN_CONTEST_KEYWORDS):
        return False
    positions = {
        pos.upper()
        for player in players
        for pos in (player.get("pos") or [])
        if isinstance(pos, str)
    }
    return not bool(positions & SHOWDOWN_POSITION_TOKENS)


def pick_covering_slates(candidates):
    selected = []
    covered_games = set()
    remaining = list(candidates)

    while remaining:
        best = min(
            remaining,
            key=lambda candidate: (
                -len(candidate["games"] - covered_games),
                -len(candidate["games"]),
                -contest_preference_score(candidate["contest_type"]),
                parse_iso_datetime(candidate["start_date"]).timestamp(),
                candidate["slate_id"],
            ),
        )
        incremental_games = best["games"] - covered_games
        if not incremental_games and selected:
            break

        selected.append({"slate": best, "included_games": incremental_games or set(best["games"])})
        covered_games |= best["games"]
        remaining.remove(best)

    return selected, covered_games


def merge_selected_slate_records(site_name, site_id, selected_entries):
    records_by_key = {}

    for entry in selected_entries:
        slate = entry["slate"]
        included_games = entry["included_games"]
        for player in slate["players"]:
            game_key = build_game_key(player)
            if game_key not in included_games:
                continue

            record_key = player.get("rwID") or normalize_name(player_full_name(player))
            merged = dict(player)
            merged["sourceSite"] = site_name
            merged["sourceSiteID"] = site_id
            merged["sourceSlateID"] = slate["slate_id"]
            merged["sourceContestType"] = slate["contest_type"]
            merged["sourceSlateStartDate"] = slate["start_date"]
            merged["sourceCoverageGames"] = len(slate["games"])
            records_by_key.setdefault(record_key, merged)

    return sorted(records_by_key.values(), key=lambda item: safe_float(item.get("pts")), reverse=True)


def fetch_site_projection_summary(session, sport, target_date, site_name):
    site_id = ROTOWIRE_SITES[site_name]
    slate_payload = rotowire_get_json(session, sport, "slate-list.php", params={"siteID": site_id})
    slates = slate_payload.get("slates") or []
    candidate_slates = []

    for slate in slates:
        start_date = slate.get("startDate")
        if not start_date or parse_iso_datetime(start_date).date() != target_date:
            continue

        players = rotowire_get_json(session, sport, "players.php", params={"slateID": slate["slateID"]})
        if not isinstance(players, list) or not players:
            continue

        games = {
            game_key
            for game_key in (build_game_key(player) for player in players)
            if game_key is not None
        }
        if not games:
            continue

        candidate_slates.append(
            {
                "site": site_name,
                "site_id": site_id,
                "slate_id": slate["slateID"],
                "contest_type": slate.get("contestType", ""),
                "start_date": start_date,
                "end_date": slate.get("endDate", ""),
                "default_slate": bool(slate.get("defaultSlate")),
                "games": games,
                "players": players,
                "is_standard": is_standard_projection_slate(slate.get("contestType", ""), players),
            }
        )

    eligible_slates = [slate for slate in candidate_slates if slate["is_standard"]] or candidate_slates
    if not eligible_slates:
        return {
            "site": site_name,
            "site_id": site_id,
            "coverage_count": 0,
            "selected_slates": [],
            "records": [],
        }

    selected_entries, covered_games = pick_covering_slates(eligible_slates)
    records = merge_selected_slate_records(site_name, site_id, selected_entries)
    primary_coverage = len(selected_entries[0]["slate"]["games"]) if selected_entries else 0

    return {
        "site": site_name,
        "site_id": site_id,
        "coverage_count": len(covered_games),
        "covered_games": sorted(covered_games),
        "primary_coverage_count": primary_coverage,
        "selected_slates": [
            {
                "slateID": entry["slate"]["slate_id"],
                "contestType": entry["slate"]["contest_type"],
                "startDate": entry["slate"]["start_date"],
                "gamesCovered": len(entry["included_games"]),
                "totalSlateGames": len(entry["slate"]["games"]),
            }
            for entry in selected_entries
        ],
        "records": records,
    }


def choose_rotowire_projection_set(sport, date, site="auto"):
    target_date = parse_iso_date(date)
    session = build_rotowire_session()
    site_names = [site] if site != "auto" else ROTOWIRE_SITE_PRIORITY
    summaries = []

    for site_name in site_names:
        print(f"Loading Rotowire {site_name} projections for {sport.upper()} on {date}...")
        try:
            summary = fetch_site_projection_summary(session, sport, target_date, site_name)
            if summary["coverage_count"] > 0 and summary["records"]:
                print(
                    f"Rotowire {site_name}: "
                    f"{summary['coverage_count']} game(s), "
                    f"{len(summary['records'])} player records, "
                    f"{len(summary['selected_slates'])} slate(s)."
                )
            else:
                print(f"Rotowire {site_name}: no usable slate coverage for {date}.")
            summaries.append(summary)
        except requests.RequestException as exc:
            print(f"Failed to fetch Rotowire {site_name} projections: {exc}")

    summaries = [summary for summary in summaries if summary["coverage_count"] > 0 and summary["records"]]
    if not summaries:
        raise RuntimeError(f"No Rotowire optimizer projections were available for {sport} on {date}.")

    if site != "auto":
        selected = summaries[0]
        print(
            f"Using Rotowire {selected['site']} with "
            f"{selected['coverage_count']} game(s) across "
            f"{len(selected['selected_slates'])} slate(s)."
        )
        return summaries[0]

    selected = max(
        summaries,
        key=lambda summary: (
            summary["coverage_count"],
            -len(summary["selected_slates"]),
            summary["primary_coverage_count"],
            -ROTOWIRE_SITE_PRIORITY.index(summary["site"]),
        ),
    )
    print(
        f"Using Rotowire {selected['site']} with "
        f"{selected['coverage_count']} game(s) across "
        f"{len(selected['selected_slates'])} slate(s)."
    )
    return selected


def fetch_rankings_rows(sport, season="2025", start_before=0, step=50, delay=1, client=None):
    client = client or build_realsports_client()
    ranking_rows = []
    last_error = None
    for ranking_url in ranking_urls_for_sport(sport):
        try:
            ranking_rows = fetch_ranking_items(
                client,
                ranking_url,
                season=season,
                start_before=start_before,
                step=step,
                delay=delay,
            )
        except RealSportsError as exc:
            last_error = exc
            continue
        if ranking_rows:
            break

    if not ranking_rows and last_error is not None:
        raise last_error

    return [
        {
            "id": row.get("id"),
            "firstName": (row.get("firstName") or "").strip(),
            "lastName": (row.get("lastName") or "").strip(),
            "rating": row.get("rating", "0"),
        }
        for row in ranking_rows
    ]


def build_ranking_lookup(ranking_rows):
    ranking_dict = {}
    for row in ranking_rows:
        full_name = f"{(row.get('firstName') or '').strip()} {(row.get('lastName') or '').strip()}".strip()
        if not full_name:
            continue
        ranking_dict[normalize_name(full_name)] = safe_float(row.get("rating", 0))
    return ranking_dict


def lookup_multiplier_entry(client, sport, date, record, real_player_index, cache):
    full_name = player_full_name(record)
    normalized = normalize_name(full_name)
    real_entries = real_player_index.get(normalized, [])
    cache_keys = real_id_cache_keys(record, real_entries)

    for cache_key in cache_keys:
        entry = cache.get(cache_key)
        if entry is not None:
            return entry

    query_names = []
    for entry in real_entries:
        exact_name = entry.get("Name", "").strip()
        if exact_name and exact_name not in query_names:
            query_names.append(exact_name)
    if full_name and full_name not in query_names:
        query_names.append(full_name)

    best_candidate = None
    best_score = float("-inf")
    best_query = ""

    for query_name in query_names:
        try:
            response = client.search_players(
                sport,
                query=query_name,
                day=date,
                search_type="ratingLineup",
                include_no_one_option=False,
            )
        except RealSportsAuthError:
            raise
        except Exception as exc:
            print(f"Failed multiplier lookup for {full_name} via {query_name}: {exc}")
            continue

        for candidate in response.get("players", []):
            score = score_multiplier_candidate(candidate, record, real_entries, query_name)
            if score > best_score:
                best_score = score
                best_candidate = candidate
                best_query = query_name

    entry = {
        "multiplier_bonus": 0.0,
        "matched_id": "",
        "matched_name": "",
        "matched_team": "",
        "matched_positions": [],
        "source_query": query_names[0] if query_names else full_name,
        "record_name": full_name,
        "record_team": normalize_team_abbr((record.get("team") or {}).get("abbr", "")),
        "cached_at": int(time.time()),
    }

    if best_candidate is not None and best_score > 0:
        entry.update(
            {
                "multiplier_bonus": safe_float(best_candidate.get("multiplierBonus", 0)),
                "matched_id": str(best_candidate.get("id", "")).strip(),
                "matched_name": candidate_full_name(best_candidate),
                "matched_team": extract_candidate_team_abbr(best_candidate),
                "matched_positions": sorted(extract_candidate_positions(best_candidate)),
                "source_query": best_query or entry["source_query"],
            }
        )

    for cache_key in cache_keys:
        cache[cache_key] = dict(entry)
    return entry


def process_fantasy_points(
    sport,
    date,
    fantasy_records,
    ranking_data,
    real_id_csv,
    multiplier_cache_dir,
    skip_multiplier=False,
    client=None,
):
    client = client or build_realsports_client()
    real_player_index = load_real_player_index(real_id_csv)
    multiplier_cache_path, multiplier_cache = load_multiplier_cache(multiplier_cache_dir, sport, date)
    results = []
    multiplier_lookup_enabled = not skip_multiplier
    multiplier_failure_message = ""

    for record in fantasy_records:
        full_name = player_full_name(record)
        if not full_name:
            continue

        fantasy_pts = safe_float(record.get("pts"))
        if multiplier_lookup_enabled:
            try:
                multiplier_entry = lookup_multiplier_entry(
                    client,
                    sport,
                    date,
                    record,
                    real_player_index,
                    multiplier_cache,
                )
            except RealSportsAuthError as exc:
                multiplier_lookup_enabled = False
                multiplier_failure_message = str(exc)
                print(
                    "Real Sports multiplier lookup disabled for the rest of this run: "
                    f"{exc}"
                )
                multiplier_entry = {
                    "multiplier_bonus": 0.0,
                    "matched_id": "",
                    "matched_name": "",
                    "matched_team": "",
                    "matched_positions": [],
                }
        else:
            multiplier_entry = {
                "multiplier_bonus": 0.0,
                "matched_id": "",
                "matched_name": "",
                "matched_team": "",
                "matched_positions": [],
            }
        multiplier = safe_float(multiplier_entry.get("multiplier_bonus", 0))
        multiplier_factor = 1.0 + multiplier
        real_rating = ranking_data.get(normalize_name(full_name), 0.0)
        adjusted_fp = fantasy_pts * multiplier_factor
        adjusted_rating = real_rating * multiplier_factor

        results.append(
            {
                "name": full_name,
                "team": (record.get("team") or {}).get("abbr", ""),
                "opponent": (record.get("opponent") or {}).get("team", ""),
                "is_home": bool(record.get("isHome")),
                "game_key": build_game_key(record) or "",
                "position": "/".join(record.get("pos") or []),
                "salary": safe_int(record.get("salary")),
                "base_fp": fantasy_pts,
                "multiplier_bonus": multiplier,
                "adjusted_fp": adjusted_fp,
                "multiplier": multiplier_factor,
                "real_rating": real_rating,
                "adjusted_rating": adjusted_rating,
                "source_site": record.get("sourceSite", ""),
                "source_slate_id": record.get("sourceSlateID", ""),
                "source_contest_type": record.get("sourceContestType", ""),
                "source_slate_start_date": record.get("sourceSlateStartDate", ""),
                "source_coverage_games": safe_int(record.get("sourceCoverageGames")),
                "matched_real_name": multiplier_entry.get("matched_name", ""),
                "matched_real_team": multiplier_entry.get("matched_team", ""),
                "matched_real_id": multiplier_entry.get("matched_id", ""),
                "multiplier_status": (
                    "skipped"
                    if skip_multiplier
                    else "disabled"
                    if not multiplier_lookup_enabled and multiplier_failure_message
                    else "ok"
                ),
            }
        )

    save_multiplier_cache(multiplier_cache_path, multiplier_cache)
    results.sort(key=lambda item: item["adjusted_fp"], reverse=True)
    return results


def write_fantasy_points_cache(path, fantasy_records):
    ensure_parent_dir(path)
    Path(path).write_text(
        json.dumps(fantasy_records, indent=2, ensure_ascii=False),
        encoding="utf8",
    )


def write_lineup_output(path, projection_summary, results):
    ensure_parent_dir(path)
    selected_slates_text = " | ".join(
        (
            f"{slate['slateID']}:{slate['contestType']}:"
            f"{slate['gamesCovered']}/{slate['totalSlateGames']}"
        )
        for slate in projection_summary["selected_slates"]
    )
    fieldnames = [
        "Lineup_Rank",
        "Name",
        "Adjusted_FP",
        "Adjusted_Rating",
        "Multiplier_Factor",
        "Team",
        "Opponent",
        "Is_Home",
        "Game_Key",
        "Position",
        "Salary",
        "Base_FP",
        "Multiplier_Bonus",
        "Real_Rating",
        "Multiplier_Status",
        "Matched_Real_Name",
        "Matched_Real_Team",
        "Matched_Real_ID",
        "Rotowire_Site",
        "Rotowire_Coverage_Games",
        "Rotowire_Selected_Slates_Count",
        "Rotowire_Selected_Slates",
        "Source_Slate_ID",
        "Source_Contest_Type",
        "Source_Slate_Start_Date",
        "Source_Coverage_Games",
    ]
    with Path(path).open("w", newline="", encoding="utf8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for index, result in enumerate(results, start=1):
            writer.writerow(
                {
                    "Lineup_Rank": index,
                    "Name": result["name"],
                    "Adjusted_FP": f"{result['adjusted_fp']:.2f}",
                    "Adjusted_Rating": f"{result['adjusted_rating']:.2f}",
                    "Multiplier_Factor": f"{result['multiplier']:.4f}",
                    "Team": result["team"],
                    "Opponent": result["opponent"],
                    "Is_Home": "Y" if result["is_home"] else "N",
                    "Game_Key": result["game_key"],
                    "Position": result["position"],
                    "Salary": result["salary"],
                    "Base_FP": f"{result['base_fp']:.2f}",
                    "Multiplier_Bonus": f"{result['multiplier_bonus']:.4f}",
                    "Real_Rating": f"{result['real_rating']:.2f}",
                    "Multiplier_Status": result["multiplier_status"],
                    "Matched_Real_Name": result["matched_real_name"],
                    "Matched_Real_Team": result["matched_real_team"],
                    "Matched_Real_ID": result["matched_real_id"],
                    "Rotowire_Site": projection_summary["site"],
                    "Rotowire_Coverage_Games": projection_summary["coverage_count"],
                    "Rotowire_Selected_Slates_Count": len(projection_summary["selected_slates"]),
                    "Rotowire_Selected_Slates": selected_slates_text,
                    "Source_Slate_ID": result["source_slate_id"],
                    "Source_Contest_Type": result["source_contest_type"],
                    "Source_Slate_Start_Date": result["source_slate_start_date"],
                    "Source_Coverage_Games": result["source_coverage_games"],
                }
            )

    print(f"Saved lineup sheet to {path}")
    print(
        f"Rows: {len(results)} | Rotowire site: {projection_summary['site']} | "
        f"Coverage: {projection_summary['coverage_count']} game(s)"
    )


def main():
    args = parse_args()
    client = build_realsports_client()
    ranking_rows = []
    ranking_data = {}
    try:
        ranking_rows = fetch_rankings_rows(args.sport, season=args.season, client=client)
        ranking_data = build_ranking_lookup(ranking_rows)
        print(
            f"Fetched {len(ranking_rows)} live Real Sports ranking rows for "
            f"{args.sport.upper()} season {args.season}."
        )
    except Exception as exc:
        print(f"Failed to fetch live Real Sports rankings: {exc}")
        print("Continuing without saved ranking CSV output.")

    if not args.skip_real_id_refresh:
        if ranking_rows:
            refreshed_count = ensure_real_id_csv_from_ranking_rows(args.real_id_csv, ranking_rows)
            print(
                f"Refreshed {args.real_id_csv} from live rankings with {refreshed_count} rows."
            )
        else:
            print(f"Skipped refreshing {args.real_id_csv} because live ranking rows were unavailable.")

    real_index = load_real_player_index(args.real_id_csv)
    if not real_player_index_has_rows(real_index):
        fallback_count = ensure_real_id_csv_from_ranking_rows(args.real_id_csv, ranking_rows)
        if fallback_count > 0:
            print(
                f"{args.real_id_csv} was empty. "
                f"Rebuilt it from live rankings with {fallback_count} rows."
            )
            real_index = load_real_player_index(args.real_id_csv)
        else:
            raise RuntimeError(
                f"{args.real_id_csv} is empty and live Real Sports rankings were unavailable."
            )

    projection_summary = choose_rotowire_projection_set(args.sport, args.date, site=args.site)
    write_fantasy_points_cache(args.fantasy_points_file, projection_summary["records"])

    results = process_fantasy_points(
        args.sport,
        args.date,
        projection_summary["records"],
        ranking_data,
        args.real_id_csv,
        args.multiplier_cache_dir,
        skip_multiplier=args.skip_multiplier,
        client=client,
    )
    write_lineup_output(args.output, projection_summary, results)


if __name__ == "__main__":
    main()
