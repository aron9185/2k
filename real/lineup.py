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
from realsports_api import (
    RealSportsAuthError,
    RealSportsError,
    RealSportsRateLimitError,
    build_realsports_client,
)


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_SPORT = "nba"  # 'mlb', 'nhl', 'nba', 'ncaam', 'soccer', 'wnba', 'ncaaf', 'nfl', 'golf'
DEFAULT_DATE = "2026-04-20"
DEFAULT_SEASON = "2025"
DEFAULT_FANTASY_POINTS_FILE = str(BASE_DIR / "fantasy_points.json")
DEFAULT_LINEUP_FILE = str(BASE_DIR / "lineup.csv")
LINEUP_SNAPSHOTS_DIR = BASE_DIR / "lineups"
DEFAULT_ROTOWIRE_SITE = "auto"
DEFAULT_REAL_ID_FILE = str(BASE_DIR / "real_id.csv")
DEFAULT_MULTIPLIER_CACHE_DIR = str(BASE_DIR / ".cache" / "realsports_multiplier")
REAL_AVAILABILITY_CACHE_TTL_SECONDS = 20 * 60

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
REAL_ONLY_LINEUP_SPORTS = {"ncaabb"}
SPORT_DEFAULT_SEASONS = {
    "ncaabb": "2026",
}
REAL_LINEUP_SEARCH_QUERIES = [""] + list("abcdefghijklmnopqrstuvwxyz")
REAL_RATING_FALLBACK_SITE = "Real ratings"
REAL_RATING_FIELD_KEYS = (
    "rating",
    "realRating",
    "averageRating",
    "avgRating",
    "tertiaryRating",
    "playerRating",
    "ratingValue",
)

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
    supported_sports = sorted(set(ROTOWIRE_SPORT_SLUGS) | REAL_ONLY_LINEUP_SPORTS)
    parser = argparse.ArgumentParser(
        description=(
            "Pull live lineup projections, choose slate coverage for the requested date, "
            "then apply Real Sports multipliers. Sports without Rotowire support can "
            "fall back to Real player ratings."
        )
    )
    parser.add_argument("--sport", default=DEFAULT_SPORT, choices=supported_sports)
    parser.add_argument("--date", default=DEFAULT_DATE, help="Target slate date in YYYY-MM-DD format.")
    parser.add_argument(
        "--season",
        default=None,
        help="Real Sports season key. Defaults to the active season for each sport.",
    )
    parser.add_argument(
        "--site",
        default=DEFAULT_ROTOWIRE_SITE,
        choices=["auto", *ROTOWIRE_SITE_PRIORITY],
        help="Lock to one sportsbook scoring system, or auto-pick the widest same-book coverage.",
    )
    parser.add_argument(
        "--fantasy-points-file",
        default=DEFAULT_FANTASY_POINTS_FILE,
        help="Where to write the normalized projection cache.",
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
        "--game-context-csv",
        default="",
        help=(
            "Optional Real poll recommendation CSV used by Real-only lineup fallbacks "
            "to identify active games."
        ),
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
    args = parser.parse_args()
    if args.season is None:
        args.season = SPORT_DEFAULT_SEASONS.get(args.sport, DEFAULT_SEASON)
    return args


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


AVAILABILITY_STATUS_KEYS = (
    "availability_status",
    "availabilityStatus",
    "availability",
    "injuryStatus",
    "injury_status",
    "playerStatus",
    "gameStatus",
    "lineupStatus",
    "playingStatus",
    "healthStatus",
    "designation",
    "status",
)
AVAILABILITY_NESTED_KEYS = ("availability", "injury", "lineup", "player", "health")
AVAILABILITY_BLOCKING_BOOL_KEYS = (
    "availability_blocked",
    "isOut",
    "isInactive",
    "isQuestionable",
    "isDoubtful",
    "isInjured",
    "isSuspended",
    "isUnavailable",
)
AVAILABILITY_FALSE_MEANS_BLOCKED_KEYS = (
    "isPlaying",
    "isAvailable",
    "available",
    "active",
    "isActive",
)
AVAILABILITY_HEALTHY_STATUSES = {
    "no",
    "none",
    "healthy",
    "active",
    "available",
    "prob",
    "probable",
    "p",
}
AVAILABILITY_BLOCKED_EXACT_STATUSES = {
    "q",
    "o",
    "d",
    "na",
    "n/a",
    "out",
    "ques",
    "questionable",
    "gtd",
    "dtd",
    "day to day",
    "day-to-day",
    "doubt",
    "doubtful",
    "dnp",
    "inactive",
    "injured reserve",
    "ir",
    "il",
    "injured list",
    "injury list",
    "susp",
    "suspended",
}
AVAILABILITY_BLOCKED_STATUS_FRAGMENTS = (
    "ques",
    "questionable",
    "gtd",
    "game time decision",
    "out",
    "doubt",
    "dnp",
    "not playing",
    "inactive",
    "injured reserve",
    "injured list",
    "injury list",
    "susp",
    "suspended",
)


def _compact_status(value):
    return " ".join(str(value or "").strip().lower().replace("_", " ").split())


def _extract_status_text(payload, seen=None):
    if not isinstance(payload, dict):
        return ""
    seen = seen or set()
    payload_id = id(payload)
    if payload_id in seen:
        return ""
    seen.add(payload_id)

    for key in AVAILABILITY_STATUS_KEYS:
        if key not in payload:
            continue
        value = payload.get(key)
        if isinstance(value, dict):
            nested = _extract_status_text(value, seen)
            if nested:
                return nested
            continue
        if isinstance(value, str) and value.strip():
            return value.strip()
        if value is not None and value != "" and key.lower().endswith("status"):
            return str(value).strip()

    for key in AVAILABILITY_NESTED_KEYS:
        value = payload.get(key)
        if isinstance(value, dict):
            nested = _extract_status_text(value, seen)
            if nested:
                return nested
    return ""


def availability_status_label(record):
    return _extract_status_text(record)


def injury_status_key(record):
    return _compact_status(availability_status_label(record))


def is_unavailable_or_questionable_status(status):
    status = _compact_status(status)
    if not status or status in AVAILABILITY_HEALTHY_STATUSES:
        return False
    if status in AVAILABILITY_BLOCKED_EXACT_STATUSES:
        return True
    if status.startswith(("il", "ir")):
        return True
    return any(token in status for token in AVAILABILITY_BLOCKED_STATUS_FRAGMENTS)


def _availability_booleans_are_blocked(record):
    if not isinstance(record, dict):
        return False
    for key in AVAILABILITY_BLOCKING_BOOL_KEYS:
        if record.get(key) is True:
            return True
    for key in AVAILABILITY_FALSE_MEANS_BLOCKED_KEYS:
        if key in record and record.get(key) is False:
            return True
    for key in AVAILABILITY_NESTED_KEYS:
        value = record.get(key)
        if isinstance(value, dict) and _availability_booleans_are_blocked(value):
            return True
    return False


def is_unavailable_or_questionable_record(record):
    if _availability_booleans_are_blocked(record):
        return True
    return is_unavailable_or_questionable_status(injury_status_key(record))


def availability_note(record):
    status = availability_status_label(record)
    if status:
        return status
    return "unavailable/questionable"


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


def real_player_query_names(record, real_entries):
    full_name = player_full_name(record)
    query_names = []
    for entry in real_entries:
        exact_name = entry.get("Name", "").strip()
        if exact_name and exact_name not in query_names:
            query_names.append(exact_name)
    if full_name and full_name not in query_names:
        query_names.append(full_name)
    return query_names


def _availability_cache_keys(cache_keys):
    return [f"availability:{cache_key}" for cache_key in cache_keys]


def _cached_availability_entry_is_fresh(entry):
    if not isinstance(entry, dict):
        return False
    cached_at = safe_int(entry.get("cached_at"), default=0)
    if cached_at <= 0:
        return False
    return int(time.time()) - cached_at <= REAL_AVAILABILITY_CACHE_TTL_SECONDS


def lookup_real_availability_entry(client, sport, date, record, real_player_index, cache):
    full_name = player_full_name(record)
    normalized = normalize_name(full_name)
    real_entries = real_player_index.get(normalized, [])
    cache_keys = _availability_cache_keys(real_id_cache_keys(record, real_entries))

    for cache_key in cache_keys:
        entry = cache.get(cache_key)
        if _cached_availability_entry_is_fresh(entry):
            return entry

    query_names = real_player_query_names(record, real_entries)
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
        except RealSportsRateLimitError:
            raise
        except Exception as exc:
            print(f"Failed availability lookup for {full_name} via {query_name}: {exc}")
            continue

        for candidate in response.get("players", []):
            score = score_multiplier_candidate(candidate, record, real_entries, query_name)
            if score > best_score:
                best_score = score
                best_candidate = candidate
                best_query = query_name

    entry = {
        "availability_status": "",
        "availability_blocked": False,
        "availability_source": "",
        "availability_query": query_names[0] if query_names else full_name,
        "matched_id": "",
        "matched_name": "",
        "matched_team": "",
        "record_name": full_name,
        "record_team": normalize_team_abbr((record.get("team") or {}).get("abbr", "")),
        "cached_at": int(time.time()),
    }

    if best_candidate is not None and best_score > 0:
        entry.update(
            {
                "availability_status": availability_status_label(best_candidate),
                "availability_blocked": is_unavailable_or_questionable_record(best_candidate),
                "availability_source": "real",
                "availability_query": best_query or entry["availability_query"],
                "matched_id": str(best_candidate.get("id", "")).strip(),
                "matched_name": candidate_full_name(best_candidate),
                "matched_team": extract_candidate_team_abbr(best_candidate),
            }
        )

    for cache_key in cache_keys:
        cache[cache_key] = dict(entry)
    return entry


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
        event = record.get("event") or {}
        event_time = str(event.get("eventDate") or game_time or "").strip()
        course_name = str(event.get("courseName") or "").strip()
        if event_time:
            return f"{event_time}|{course_name or 'event'}"
        return None
    if record.get("isHome"):
        away_team, home_team = opponent, team
    else:
        away_team, home_team = team, opponent
    return f"{game_time}|{away_team}@{home_team}"


def slate_target_rank(sport, slate, target_date):
    start_date = slate.get("startDate")
    if not start_date:
        return None
    start_dt = parse_iso_datetime(start_date)
    end_raw = slate.get("endDate") or start_date
    try:
        end_dt = parse_iso_datetime(end_raw)
    except Exception:
        end_dt = start_dt
    start_day = start_dt.date()
    end_day = end_dt.date()

    if start_day == target_date:
        return (0, 0, start_dt.timestamp())
    if start_day <= target_date <= end_day:
        return (1, 0, start_dt.timestamp())
    if sport == "golf" and target_date < start_day:
        days_ahead = (start_day - target_date).days
        if days_ahead <= 7:
            return (2, days_ahead, start_dt.timestamp())
    return None


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


def select_same_day_slates(candidates):
    ordered = sorted(
        candidates,
        key=lambda candidate: (
            candidate.get("target_rank", (999, 999, float("inf"))),
            parse_iso_datetime(candidate["start_date"]).timestamp(),
            -contest_preference_score(candidate["contest_type"]),
            -len(candidate["games"]),
            candidate["slate_id"],
        ),
    )
    selected = [
        {
            "slate": slate,
            "included_games": set(slate["games"]),
        }
        for slate in ordered
    ]
    covered_games = set()
    for entry in selected:
        covered_games |= entry["included_games"]
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
        target_rank = slate_target_rank(sport, slate, target_date)
        if target_rank is None:
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
                "target_rank": target_rank,
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

    selected_entries, covered_games = select_same_day_slates(eligible_slates)
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


def build_ranking_id_lookup(ranking_rows):
    ranking_dict = {}
    for row in ranking_rows:
        player_id = str(row.get("id") or "").strip()
        if player_id:
            ranking_dict[player_id] = safe_float(row.get("rating", 0))
    return ranking_dict


def _game_team_value(game, side):
    for key in (f"{side}TeamKey", f"{side}TeamAbbr"):
        value = str(game.get(key) or "").strip()
        if value:
            return value
    team = game.get(f"{side}Team")
    if isinstance(team, dict):
        for key in ("key", "abbreviation", "abbr", "displayName", "name"):
            value = str(team.get(key) or "").strip()
            if value:
                return value
    return ""


def _game_team_id(game, side):
    for key in (f"{side}TeamId", f"{side}TeamID"):
        value = str(game.get(key) or "").strip()
        if value:
            return value
    team = game.get(f"{side}Team")
    if isinstance(team, dict):
        for key in ("id", "teamId", "teamID"):
            value = str(team.get(key) or "").strip()
            if value:
                return value
    return ""


def _game_id_value(game):
    for key in ("gameId", "gameID", "id", "entityId"):
        value = str(game.get(key) or "").strip()
        if value:
            return value
    return ""


def _game_time_value(game):
    for key in ("dateTime", "gameTime", "startTime", "startDate", "eventDate"):
        value = str(game.get(key) or "").strip()
        if value:
            return value
    return ""


def _game_day_value(game):
    direct = str(game.get("day") or "").strip()
    if direct:
        return direct
    time_text = _game_time_value(game)
    return time_text[:10] if len(time_text) >= 10 else ""


def _normal_game_context(game):
    game_id = _game_id_value(game)
    game_time = _game_time_value(game)
    home_team = normalize_team_abbr(_game_team_value(game, "home"))
    away_team = normalize_team_abbr(_game_team_value(game, "away"))
    if not home_team or not away_team:
        label = str(game.get("game_label") or game.get("gameLabel") or "").strip()
        if "@" in label:
            away_label, home_label = label.split("@", 1)
            away_team = away_team or normalize_team_abbr(away_label)
            home_team = home_team or normalize_team_abbr(home_label)
    if not game_id or not home_team or not away_team:
        return None
    return {
        "game_id": game_id,
        "game_time": game_time,
        "home_team": home_team,
        "away_team": away_team,
        "home_team_id": _game_team_id(game, "home"),
        "away_team_id": _game_team_id(game, "away"),
        "day": _game_day_value(game),
        "game_key": f"{game_time}|{away_team}@{home_team}" if game_time else f"|{away_team}@{home_team}",
    }


def _load_game_context_csv(path, *, sport, date):
    csv_path = Path(path) if path else None
    if not csv_path or not csv_path.exists():
        return []
    if csv.field_size_limit() < 10_000_000:
        csv.field_size_limit(10_000_000)
    games_by_id = {}
    with csv_path.open("r", encoding="utf8", newline="") as handle:
        for row in csv.DictReader(handle):
            if str(row.get("sport") or "").strip().lower() != str(sport or "").strip().lower():
                continue
            if date and str(row.get("day") or "").strip() not in {"", str(date)}:
                continue
            game_id = str(row.get("game_id") or "").strip()
            if not game_id or game_id in games_by_id:
                continue
            home_team = normalize_team_abbr(str(row.get("home_team") or ""))
            away_team = normalize_team_abbr(str(row.get("away_team") or ""))
            if not home_team or not away_team:
                label = str(row.get("game_label") or "").strip()
                if "@" in label:
                    away_label, home_label = label.split("@", 1)
                    away_team = away_team or normalize_team_abbr(away_label)
                    home_team = home_team or normalize_team_abbr(home_label)
            if not home_team or not away_team:
                continue
            game_time = str(row.get("game_time") or "").strip()
            games_by_id[game_id] = {
                "game_id": game_id,
                "game_time": game_time,
                "home_team": home_team,
                "away_team": away_team,
                "home_team_id": "",
                "away_team_id": "",
                "day": str(row.get("day") or "").strip(),
                "game_key": f"{game_time}|{away_team}@{home_team}" if game_time else f"|{away_team}@{home_team}",
            }
    return list(games_by_id.values())


def _load_real_active_games(client, sport, date, game_context_csv=""):
    context_games = _load_game_context_csv(game_context_csv, sport=sport, date=date)
    if context_games:
        return context_games
    try:
        payload = client.get_home_tab(sport=sport)
    except Exception as exc:
        print(f"Failed to load Real {sport.upper()} active games for rating fallback: {exc}")
        return []
    latest = payload.get("latestDayContent") or {}
    games = []
    for game in latest.get("games") or []:
        if not isinstance(game, dict):
            continue
        game_day = _game_day_value(game)
        if date and game_day and game_day != str(date):
            continue
        normalized = _normal_game_context(game)
        if normalized is not None:
            games.append(normalized)
    return games


def _candidate_display_name(candidate):
    name = candidate_full_name(candidate)
    if name:
        return name
    for key in ("name", "fullName", "displayName", "label"):
        value = str(candidate.get(key) or "").strip()
        if value:
            return value
    return ""


def _candidate_team_id(candidate):
    team = candidate.get("team")
    if isinstance(team, dict):
        for key in ("id", "teamId", "teamID"):
            value = str(team.get(key) or "").strip()
            if value:
                return value
    for key in ("teamId", "teamID", "team_id"):
        value = str(candidate.get(key) or "").strip()
        if value:
            return value
    return ""


def _candidate_game_id(candidate):
    for key in ("_lineup_game_id", "gameId", "gameID", "game_id"):
        value = str(candidate.get(key) or "").strip()
        if value:
            return value
    game = candidate.get("game")
    if isinstance(game, dict):
        return _game_id_value(game)
    return ""


def _candidate_rating(candidate, ranking_by_name, ranking_by_id):
    player_id = str(candidate.get("id") or "").strip()
    if player_id and player_id in ranking_by_id:
        value = ranking_by_id[player_id]
        if value > 0:
            return value
    name_key = normalize_name(_candidate_display_name(candidate))
    if name_key and ranking_by_name.get(name_key, 0) > 0:
        return ranking_by_name[name_key]
    for key in REAL_RATING_FIELD_KEYS:
        value = safe_float(candidate.get(key), default=0.0)
        if value > 0:
            return value
    return 0.0


def _match_candidate_game(candidate, games):
    candidate_game_id = _candidate_game_id(candidate)
    team_id = _candidate_team_id(candidate)
    team_abbr = extract_candidate_team_abbr(candidate)
    for game in games:
        if candidate_game_id and candidate_game_id == game["game_id"]:
            return game
        if team_id and team_id in {game.get("home_team_id"), game.get("away_team_id")}:
            return game
        if team_abbr and team_abbr in {game.get("home_team"), game.get("away_team")}:
            return game
    return None


def _search_real_lineup_players(client, sport, date, season, games):
    game_ids = [game["game_id"] for game in games if game.get("game_id")]
    players_by_key = {}
    scoped_game_ids = game_ids or [""]
    for game_id in scoped_game_ids:
        for query in REAL_LINEUP_SEARCH_QUERIES:
            try:
                payload = client.search_players(
                    sport,
                    query=query,
                    day=date,
                    search_type="ratingLineup",
                    include_no_one_option=False,
                    game_id=game_id or None,
                    season=season,
                )
            except Exception as exc:
                if query == "":
                    scope = f" game {game_id}" if game_id else ""
                    print(f"Failed Real ratingLineup search for {sport.upper()}{scope}: {exc}")
                continue
            for player in payload.get("players") or []:
                if not isinstance(player, dict):
                    continue
                player = dict(player)
                if game_id and not _candidate_game_id(player):
                    player["_lineup_game_id"] = game_id
                name = _candidate_display_name(player)
                team = extract_candidate_team_abbr(player)
                player_id = str(player.get("id") or "").strip()
                candidate_game_id = _candidate_game_id(player)
                key = player_id or f"{normalize_name(name)}|{team}|{candidate_game_id}"
                if key and key not in players_by_key:
                    players_by_key[key] = player
    return list(players_by_key.values())


def build_real_rating_lineup(
    *,
    client,
    sport,
    date,
    season,
    ranking_data,
    ranking_rows,
    game_context_csv="",
):
    games = _load_real_active_games(client, sport, date, game_context_csv=game_context_csv)
    if not games:
        raise RuntimeError(f"No active Real games were available for {sport} on {date}.")
    ranking_by_id = build_ranking_id_lookup(ranking_rows)
    candidates = _search_real_lineup_players(client, sport, date, season, games)
    results_by_key = {}
    for candidate in candidates:
        if is_unavailable_or_questionable_record(candidate):
            continue
        name = _candidate_display_name(candidate)
        if not name:
            continue
        game = _match_candidate_game(candidate, games)
        if game is None:
            continue
        rating = _candidate_rating(candidate, ranking_data, ranking_by_id)
        if rating <= 0:
            continue
        team = extract_candidate_team_abbr(candidate)
        if not team:
            team_id = _candidate_team_id(candidate)
            if team_id == game.get("home_team_id"):
                team = game["home_team"]
            elif team_id == game.get("away_team_id"):
                team = game["away_team"]
        opponent = ""
        is_home = False
        if team == game["home_team"]:
            opponent = game["away_team"]
            is_home = True
        elif team == game["away_team"]:
            opponent = game["home_team"]
            is_home = False
        multiplier = safe_float(candidate.get("multiplierBonus"), default=0.0)
        multiplier_factor = 1.0 + multiplier
        adjusted_rating = rating * multiplier_factor
        player_id = str(candidate.get("id") or "").strip()
        dedupe_key = f"{normalize_name(name)}|{game['game_id']}"
        result = {
            "name": name,
            "team": team,
            "opponent": opponent,
            "is_home": is_home,
            "game_key": game["game_key"],
            "position": "/".join(sorted(extract_candidate_positions(candidate))),
            "salary": 0,
            "base_fp": rating,
            "multiplier_bonus": multiplier,
            "adjusted_fp": adjusted_rating,
            "multiplier": multiplier_factor,
            "real_rating": rating,
            "adjusted_rating": adjusted_rating,
            "source_site": REAL_RATING_FALLBACK_SITE,
            "source_slate_id": "real-rating",
            "source_contest_type": "Real rating fallback",
            "source_slate_start_date": game["game_time"],
            "source_coverage_games": len(games),
            "matched_real_name": name,
            "matched_real_team": team,
            "matched_real_id": player_id,
            "multiplier_status": "real-rating",
        }
        previous = results_by_key.get(dedupe_key)
        if previous is None or result["adjusted_rating"] > previous["adjusted_rating"]:
            results_by_key[dedupe_key] = result
    results = sorted(
        results_by_key.values(),
        key=lambda item: (item["adjusted_rating"], item["real_rating"], item["name"]),
        reverse=True,
    )
    if not results:
        raise RuntimeError(f"No Real rating lineup players were available for {sport} on {date}.")
    projection_summary = {
        "site": REAL_RATING_FALLBACK_SITE,
        "site_id": "",
        "coverage_count": len(games),
        "covered_games": sorted(game["game_key"] for game in games),
        "primary_coverage_count": len(games),
        "selected_slates": [
            {
                "slateID": "real-rating",
                "contestType": "Real rating fallback",
                "startDate": date,
                "gamesCovered": len(games),
                "totalSlateGames": len(games),
            }
        ],
        "records": candidates,
    }
    print(
        f"Using Real ratings fallback with {len(results)} player(s) "
        f"across {len(games)} active {sport.upper()} game(s)."
    )
    return projection_summary, results


def lookup_multiplier_entry(client, sport, date, record, real_player_index, cache):
    full_name = player_full_name(record)
    normalized = normalize_name(full_name)
    real_entries = real_player_index.get(normalized, [])
    cache_keys = real_id_cache_keys(record, real_entries)

    for cache_key in cache_keys:
        entry = cache.get(cache_key)
        if entry is not None:
            return entry

    query_names = real_player_query_names(record, real_entries)

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
        except RealSportsRateLimitError:
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
        "availability_status": "",
        "availability_blocked": False,
        "availability_source": "",
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
                "availability_status": availability_status_label(best_candidate),
                "availability_blocked": is_unavailable_or_questionable_record(best_candidate),
                "availability_source": "real",
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
        if is_unavailable_or_questionable_record(record):
            continue
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
            except (RealSportsAuthError, RealSportsRateLimitError) as exc:
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
        availability_entry = multiplier_entry
        if (
            multiplier_lookup_enabled
            and not skip_multiplier
            and (
                not availability_entry.get("availability_source")
                or not _cached_availability_entry_is_fresh(availability_entry)
            )
        ):
            try:
                availability_entry = lookup_real_availability_entry(
                    client,
                    sport,
                    date,
                    record,
                    real_player_index,
                    multiplier_cache,
                )
            except (RealSportsAuthError, RealSportsRateLimitError):
                availability_entry = multiplier_entry
        if availability_entry.get("availability_blocked") is True:
            status_text = availability_entry.get("availability_status") or "questionable/unavailable"
            print(f"Skipping {full_name}: Real availability is {status_text}.")
            continue
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


def _lineup_output_fieldnames():
    return [
        "Sport",
        "Slate_Date",
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


def _write_lineup_csv(path, *, sport, date, projection_summary, results):
    ensure_parent_dir(path)
    selected_slates_text = " | ".join(
        (
            f"{slate['slateID']}:{slate['contestType']}:"
            f"{slate['gamesCovered']}/{slate['totalSlateGames']}"
        )
        for slate in projection_summary["selected_slates"]
    )
    with Path(path).open("w", newline="", encoding="utf8") as handle:
        writer = csv.DictWriter(handle, fieldnames=_lineup_output_fieldnames())
        writer.writeheader()
        for index, result in enumerate(results, start=1):
            writer.writerow(
                {
                    "Sport": sport,
                    "Slate_Date": date,
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


def write_lineup_output(path, *, sport, date, projection_summary, results):
    output_path = Path(path)
    _write_lineup_csv(
        output_path,
        sport=sport,
        date=date,
        projection_summary=projection_summary,
        results=results,
    )
    snapshot_path = LINEUP_SNAPSHOTS_DIR / f"{sport}.csv"
    if snapshot_path.resolve() != output_path.resolve():
        _write_lineup_csv(
            snapshot_path,
            sport=sport,
            date=date,
            projection_summary=projection_summary,
            results=results,
        )

    print(f"Saved lineup sheet to {path}")
    print(
        f"Rows: {len(results)} | Source: {projection_summary['site']} | "
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
            if args.sport not in REAL_ONLY_LINEUP_SPORTS:
                raise RuntimeError(
                    f"{args.real_id_csv} is empty and live Real Sports rankings were unavailable."
                )
            print(
                f"{args.real_id_csv} is empty, but {args.sport.upper()} uses "
                "the Real ratings fallback and can continue without it."
            )

    if args.sport in REAL_ONLY_LINEUP_SPORTS:
        projection_summary, results = build_real_rating_lineup(
            client=client,
            sport=args.sport,
            date=args.date,
            season=args.season,
            ranking_data=ranking_data,
            ranking_rows=ranking_rows,
            game_context_csv=args.game_context_csv,
        )
        write_fantasy_points_cache(args.fantasy_points_file, projection_summary["records"])
        write_lineup_output(
            args.output,
            sport=args.sport,
            date=args.date,
            projection_summary=projection_summary,
            results=results,
        )
        return

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
    write_lineup_output(
        args.output,
        sport=args.sport,
        date=args.date,
        projection_summary=projection_summary,
        results=results,
    )


if __name__ == "__main__":
    main()
