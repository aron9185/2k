from __future__ import annotations

import datetime as dt
import json
import os
import re
import socket
import time
from html import unescape
from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus

import requests
from flask import Flask, jsonify, redirect, render_template, request, url_for

from matchups import build_guarded_top3_tables
from rotation_core import CDN_BOXSCORE, fetch_game_rotation, fetch_json
from rotation_plot import build_game_figs, figs_to_html, load_position_estimate

app = Flask(__name__)

BASE_DIR = os.path.dirname(__file__)
CACHE_JSON_DIR = os.path.join(BASE_DIR, "cache_json")
SCHEDULE_PAGE = "https://www.nba.com/games?date={DATE}"
POS_XLS_DEFAULT = os.environ.get("POS_XLS", os.path.join(BASE_DIR, "sportsref_download.xls"))
OUT_DIR = os.environ.get("OUT_DIR", os.path.join(BASE_DIR, "html"))
os.makedirs(OUT_DIR, exist_ok=True)

WEB_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/136.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

ROTO_BASE_URL = "https://www.rotowire.com"
ROTO_SEARCH_URL = ROTO_BASE_URL + "/search.php?term={QUERY}"
ROTO_CACHE_DIR = os.path.join(CACHE_JSON_DIR, "rotowire")
ROTO_SEARCH_TTL_SEC = 12 * 3600
ROTO_PAGE_TTL_SEC = 12 * 3600
ROTO_JSON_TTL_SEC = 6 * 3600
ROTO_STALE_SEC = 45 * 24 * 3600
ROTO_TIMEOUT = (1.5, 4.0)

ROTO_PLAYER_URL_RE = re.compile(r"^https?://www\.rotowire\.com/basketball/player/[^/?#]+-\d+$", re.IGNORECASE)
ROTO_SEARCH_RESULT_RE = re.compile(
    r'<a href="(/basketball/player/[^"]+)">([^<]+)</a><span>([^<]*)</span>',
    re.IGNORECASE,
)
ROTO_PLAYER_AJAX_RE = re.compile(
    r"url\s*:\s*'(?P<path>/basketball/ajax/player-page-data\.php)'.*?"
    r"id:\s*\"(?P<id>\d+)\".*?"
    r"team:\s*\"(?P<team>[A-Z]+)\".*?"
    r"nba:\s*true",
    re.IGNORECASE | re.DOTALL,
)
ROTO_MONTH_LOOKUP = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}
ROTO_WEEKDAY_LOOKUP = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}
ROTO_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}
ROTO_TEAM_ALIASES = {
    "PHX": {"PHX", "PHO"},
    "NOP": {"NOP", "NO"},
    "GSW": {"GSW", "GS"},
    "SAS": {"SAS", "SA"},
}

os.makedirs(ROTO_CACHE_DIR, exist_ok=True)


def _safe_str(value: Any) -> str:
    try:
        return str(value or "").strip()
    except Exception:
        return ""


def _safe_int(value: Any) -> Optional[int]:
    try:
        if value in (None, ""):
            return None
        return int(value)
    except Exception:
        return None


def _safe_float(value: Any) -> Optional[float]:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except Exception:
        return None


def _cache_safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", _safe_str(value))


def _read_cached_obj(path: str) -> Optional[Dict[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
        return payload if isinstance(payload, dict) else None
    except Exception:
        return None


def _write_cached_obj(path: str, payload: Dict[str, Any]) -> None:
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        os.replace(tmp, path)
    except Exception:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass


def _load_cached_obj(path: str, max_age_sec: int) -> Optional[Dict[str, Any]]:
    if not os.path.exists(path):
        return None
    age = time.time() - os.path.getmtime(path)
    if age > max_age_sec:
        return None
    return _read_cached_obj(path)


def _fetch_cached_text(url: str, cache_key: str, *, ttl_sec: int, stale_sec: int, headers: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    path = os.path.join(ROTO_CACHE_DIR, _cache_safe_name(cache_key) + ".json")
    cached = _load_cached_obj(path, ttl_sec)
    if cached is not None:
        return cached

    try:
        session = requests.Session()
        session.trust_env = False
        merged_headers = dict(WEB_HEADERS)
        if headers:
            merged_headers.update(headers)
        response = session.get(url, timeout=ROTO_TIMEOUT, headers=merged_headers, allow_redirects=True)
        if response.status_code == 200:
            payload = {
                "url": response.url,
                "status": response.status_code,
                "text": response.text,
            }
            _write_cached_obj(path, payload)
            return payload
    except Exception:
        pass

    stale = _load_cached_obj(path, stale_sec)
    return stale or {}


def _fetch_cached_json_response(
    url: str,
    cache_key: str,
    *,
    ttl_sec: int,
    stale_sec: int,
    headers: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    path = os.path.join(ROTO_CACHE_DIR, _cache_safe_name(cache_key) + ".json")
    cached = _load_cached_obj(path, ttl_sec)
    if cached is not None:
        return cached

    try:
        session = requests.Session()
        session.trust_env = False
        merged_headers = {
            "User-Agent": WEB_HEADERS["User-Agent"],
            "Accept": "application/json, text/plain, */*",
        }
        if headers:
            merged_headers.update(headers)
        response = session.get(url, timeout=ROTO_TIMEOUT, headers=merged_headers, allow_redirects=True)
        if response.status_code == 200:
            payload = response.json()
            if isinstance(payload, dict):
                wrapped = {
                    "url": response.url,
                    "status": response.status_code,
                    "json": payload,
                }
                _write_cached_obj(path, wrapped)
                return wrapped
    except Exception:
        pass

    stale = _load_cached_obj(path, stale_sec)
    return stale or {}


def _strip_html(value: Any) -> str:
    text = _safe_str(value)
    if not text:
        return ""
    text = re.sub(r"<br\s*/?>", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"</(?:div|p|li|span|a|b|strong)>", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\s+", " ", unescape(text)).strip()
    return re.sub(r"^ANALYSIS\s*", "", text, flags=re.IGNORECASE)


def _person_name_keys(name: Any) -> set[str]:
    text = unescape(_safe_str(name)).lower().replace("\u2019", "'").replace("`", "'")
    tokens = re.findall(r"[a-z0-9]+", text)
    if not tokens:
        return set()

    keys = {"".join(tokens)}
    trimmed = [token for token in tokens if token not in ROTO_SUFFIXES]
    if trimmed:
        keys.add("".join(trimmed))
    return {key for key in keys if key}


def _rotowire_team_codes(team_abbr: Any) -> set[str]:
    abbr = _safe_str(team_abbr).upper()
    if not abbr:
        return set()
    return {abbr} | set(ROTO_TEAM_ALIASES.get(abbr, set()))


def _resolve_rotowire_player(name: str, team_abbr: str) -> Dict[str, Any]:
    query = _safe_str(name)
    if not query:
        return {}

    search_payload = _fetch_cached_text(
        ROTO_SEARCH_URL.format(QUERY=quote_plus(query)),
        cache_key=f"rotowire_search_{query}_{team_abbr}",
        ttl_sec=ROTO_SEARCH_TTL_SEC,
        stale_sec=ROTO_STALE_SEC,
    )
    final_url = _safe_str(search_payload.get("url"))
    html = _safe_str(search_payload.get("text"))

    if final_url and ROTO_PLAYER_URL_RE.match(final_url):
        return {"url": final_url, "html": html}
    if not html:
        return {}

    wanted_keys = _person_name_keys(query)
    team_codes = _rotowire_team_codes(team_abbr)
    best_url = ""
    best_score = -1

    for href, candidate_name, meta in ROTO_SEARCH_RESULT_RE.findall(html):
        candidate_keys = _person_name_keys(candidate_name)
        if not (wanted_keys & candidate_keys):
            continue
        score = 10
        meta_team = _safe_str(meta).split(" ", 1)[0].upper()
        if meta_team and meta_team in team_codes:
            score += 3
        if score > best_score:
            best_url = ROTO_BASE_URL + href
            best_score = score

    if not best_url:
        return {}

    slug = best_url.rstrip("/").rsplit("/", 1)[-1]
    page_payload = _fetch_cached_text(
        best_url,
        cache_key=f"rotowire_page_{slug}",
        ttl_sec=ROTO_PAGE_TTL_SEC,
        stale_sec=ROTO_STALE_SEC,
    )
    return {
        "url": _safe_str(page_payload.get("url")) or best_url,
        "html": _safe_str(page_payload.get("text")),
    }


def _extract_first_class_html(document: str, class_name: str) -> str:
    match = re.search(
        rf'<div class="[^"]*\b{re.escape(class_name)}\b[^"]*">(.*?)</div>',
        document,
        re.IGNORECASE | re.DOTALL,
    )
    return match.group(1) if match else ""


def _parse_date_text(value: Any) -> Optional[dt.date]:
    text = _safe_str(value)
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%B %d, %Y", "%m/%d/%Y"):
        try:
            return dt.datetime.strptime(text, fmt).date()
        except Exception:
            continue
    try:
        return dt.datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except Exception:
        return None


def _rotowire_season_key(game_date: dt.date) -> int:
    return game_date.year if game_date.month >= 7 else game_date.year - 1


def _select_rotowire_game_log(payload: Dict[str, Any], game_date: dt.date) -> List[Dict[str, Any]]:
    if not isinstance(payload, dict):
        return []

    target_key = _rotowire_season_key(game_date)
    available = sorted(
        int(key[2:])
        for key in payload
        if isinstance(key, str) and key.startswith("gl") and key[2:].isdigit() and isinstance(payload.get(key), dict)
    )
    if not available:
        return []

    year = target_key if target_key in available else min(available, key=lambda item: abs(item - target_key))
    rows = (payload.get(f"gl{year}") or {}).get("body") or []
    return rows if isinstance(rows, list) else []


def _rotowire_row_is_active(row: Dict[str, Any]) -> bool:
    if _played_flag(row.get("playedgame")):
        return True
    return _parse_minutes_value(row.get("min")) is not None


def _find_next_active_date(rows: List[Dict[str, Any]], reference_date: dt.date) -> Optional[dt.date]:
    next_date: Optional[dt.date] = None
    for row in rows:
        if not isinstance(row, dict):
            continue
        row_date = _parse_date_text(row.get("gamedate") or row.get("fulldate"))
        if row_date is None or row_date <= reference_date or not _rotowire_row_is_active(row):
            continue
        if next_date is None or row_date < next_date:
            next_date = row_date
    return next_date


def _split_sentences(text: str) -> List[str]:
    return [segment.strip() for segment in re.split(r"(?<=[.!?])\s+", _safe_str(text)) if segment.strip()]


def _pick_rotowire_return_note(news_text: str, analysis_text: str) -> str:
    priority_patterns = [
        r"\bnext opportunity\b",
        r"\breturn(?:ed|ing|s)?\b.*\b(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
        r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|"
        r"sep(?:t|tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\b",
        r"\bexpected to play\b",
        r"\bexpected back\b",
        r"\bcould play\b",
        r"\bback in\b",
        r"\bback on\b",
        r"\btimetable\b",
        r"\bmiss(?:es|ing|ed)?\b",
        r"\bsurgery\b",
        r"\bsidelined\b",
        r"\bavailable\b",
        r"\bquestionable\b",
        r"\bdoubtful\b",
        r"\bprobable\b",
        r"\bday-to-day\b",
    ]

    for block in (analysis_text, news_text):
        for sentence in _split_sentences(block):
            lowered = sentence.lower()
            if any(re.search(pattern, lowered) for pattern in priority_patterns):
                return sentence
    return ""


def _next_weekday_on_or_after(reference_date: dt.date, weekday: int) -> dt.date:
    delta = (weekday - reference_date.weekday()) % 7
    return reference_date + dt.timedelta(days=delta)


def _infer_date_from_text(text: str, reference_date: Optional[dt.date]) -> Optional[dt.date]:
    if not text or reference_date is None:
        return None

    explicit_match = re.search(
        r"\b("
        r"Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
        r"Jul(?:y)?|Aug(?:ust)?|Sep(?:t|tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?"
        r")\.?\s+(\d{1,2})(?:,\s*(\d{4}))?",
        text,
        re.IGNORECASE,
    )
    if explicit_match:
        month_name = explicit_match.group(1).lower().rstrip(".")
        month = ROTO_MONTH_LOOKUP.get(month_name)
        day = _safe_int(explicit_match.group(2))
        year = _safe_int(explicit_match.group(3)) or reference_date.year
        if month and day and year:
            try:
                candidate = dt.date(year, month, day)
                if explicit_match.group(3) is None and candidate < reference_date - dt.timedelta(days=7):
                    candidate = dt.date(year + 1, month, day)
                if candidate >= reference_date:
                    return candidate
            except Exception:
                pass

    for weekday_name, weekday_number in ROTO_WEEKDAY_LOOKUP.items():
        if re.search(rf"\b{weekday_name}(?:'s)?\b", text, re.IGNORECASE):
            return _next_weekday_on_or_after(reference_date, weekday_number)
    return None


def _format_days_after(days: int) -> str:
    if days <= 0:
        return "0 days"
    if days == 1:
        return "1 day"
    return f"{days} days"


def _format_timing_text(label: str, event_date: dt.date, reference_date: dt.date) -> str:
    days = max(0, (event_date - reference_date).days)
    return f"Out for {_format_days_after(days)} | {label}: {event_date.strftime('%b %d, %Y')}"


def _parse_rotowire_page_info(page_html: str) -> Dict[str, Any]:
    ajax_match = ROTO_PLAYER_AJAX_RE.search(page_html)
    timestamp_text = _strip_html(_extract_first_class_html(page_html, "news-update__timestamp"))
    news_text = _strip_html(_extract_first_class_html(page_html, "news-update__news"))
    analysis_text = _strip_html(_extract_first_class_html(page_html, "news-update__analysis"))
    article_date = _parse_date_text(timestamp_text)
    return_note = _pick_rotowire_return_note(news_text, analysis_text)
    estimated_return_date = _infer_date_from_text(return_note, article_date)

    return {
        "ajax_path": ajax_match.group("path") if ajax_match else "",
        "player_id": ajax_match.group("id") if ajax_match else "",
        "team_code": ajax_match.group("team") if ajax_match else "",
        "headline": _strip_html(_extract_first_class_html(page_html, "news-update__headline")),
        "injury": _strip_html(_extract_first_class_html(page_html, "news-update__inj")),
        "timestamp": timestamp_text,
        "article_date": article_date,
        "news": news_text,
        "analysis": analysis_text,
        "return_note": return_note,
        "estimated_return_date": estimated_return_date,
    }


def _enrich_absence_row(row: Dict[str, Any], team_abbr: str, game_date: str) -> Dict[str, Any]:
    enriched = dict(row)
    enriched.setdefault("rotowire_url", "")
    enriched.setdefault("rotowire_timing_text", "")
    enriched.setdefault("rotowire_note", "")

    reference_date = _parse_date_text(game_date)
    if reference_date is None:
        return enriched

    resolved = _resolve_rotowire_player(_safe_str(row.get("name")), team_abbr)
    page_url = _safe_str(resolved.get("url"))
    page_html = _safe_str(resolved.get("html"))
    if not page_url:
        return enriched

    enriched["rotowire_url"] = page_url
    if not page_html:
        return enriched

    page_info = _parse_rotowire_page_info(page_html)
    if page_info.get("return_note"):
        enriched["rotowire_note"] = _safe_str(page_info.get("return_note"))

    player_id = _safe_str(page_info.get("player_id"))
    team_code = _safe_str(page_info.get("team_code"))
    ajax_path = _safe_str(page_info.get("ajax_path"))
    if player_id and team_code and ajax_path:
        ajax_url = f"{ROTO_BASE_URL}{ajax_path}?id={player_id}&team={team_code}&nba=true"
        ajax_payload = _fetch_cached_json_response(
            ajax_url,
            cache_key=f"rotowire_ajax_{player_id}_{team_code}",
            ttl_sec=ROTO_JSON_TTL_SEC,
            stale_sec=ROTO_STALE_SEC,
            headers={
                "Referer": page_url,
                "X-Requested-With": "XMLHttpRequest",
            },
        )
        game_log_rows = _select_rotowire_game_log(ajax_payload.get("json") or {}, reference_date)
        next_active = _find_next_active_date(game_log_rows, reference_date)
        if next_active is not None:
            enriched["rotowire_timing_text"] = _format_timing_text("Next active", next_active, reference_date)
            return enriched

    estimated_return_date = page_info.get("estimated_return_date")
    if isinstance(estimated_return_date, dt.date) and estimated_return_date >= reference_date:
        enriched["rotowire_timing_text"] = _format_timing_text("Estimated return", estimated_return_date, reference_date)

    return enriched


def _enrich_absences(rows: List[Dict[str, Any]], team_abbr: str, game_date: str) -> List[Dict[str, Any]]:
    enriched_rows: List[Dict[str, Any]] = []
    for row in rows:
        try:
            enriched_rows.append(_enrich_absence_row(row, team_abbr=team_abbr, game_date=game_date))
        except Exception:
            enriched_rows.append(dict(row))
    return enriched_rows


def _parse_requested_date(raw: Optional[str]) -> dt.date:
    text = _safe_str(raw)
    if text:
        try:
            return dt.date.fromisoformat(text)
        except Exception:
            pass
    return dt.date.today()


def _format_iso_datetime(raw: Any) -> str:
    text = _safe_str(raw)
    if not text:
        return ""
    try:
        value = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
        return value.strftime("%a, %b %d, %Y %I:%M %p")
    except Exception:
        return text


def _format_number(value: Any) -> str:
    integer = _safe_int(value)
    if integer is None:
        return ""
    return f"{integer:,}"


def _parse_minutes_value(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)

    text = _safe_str(value)
    if not text:
        return None

    iso_match = re.match(r"^PT(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?$", text, re.IGNORECASE)
    if iso_match:
        minutes = float(iso_match.group(1) or 0.0)
        seconds = float(iso_match.group(2) or 0.0)
        return minutes + seconds / 60.0

    mmss_match = re.match(r"^(\d+):(\d+(?:\.\d+)?)$", text)
    if mmss_match:
        minutes = float(mmss_match.group(1))
        seconds = float(mmss_match.group(2))
        return minutes + seconds / 60.0

    return _safe_float(text)


def _format_minutes(value: Any) -> str:
    minutes_value = _parse_minutes_value(value)
    if minutes_value is None:
        return ""
    whole_minutes = int(minutes_value)
    seconds = int(round((minutes_value - whole_minutes) * 60))
    if seconds == 60:
        whole_minutes += 1
        seconds = 0
    return f"{whole_minutes}:{seconds:02d}"


def _played_flag(value: Any) -> bool:
    return _safe_str(value) in {"1", "true", "True"} or value is True


def _format_plus_minus(value: Any) -> str:
    number = _safe_float(value)
    if number is None:
        return ""
    if abs(number - round(number)) < 1e-9:
        integer = int(round(number))
        return f"{integer:+d}"
    return f"{number:+.1f}"


def _format_pair(made: Any, attempted: Any) -> str:
    made_value = _safe_int(made)
    attempted_value = _safe_int(attempted)
    if made_value is None and attempted_value is None:
        return ""
    return f"{made_value or 0}-{attempted_value or 0}"


def _game_date_from_iso(*values: Any) -> str:
    for raw in values:
        text = _safe_str(raw)
        if not text:
            continue
        try:
            return dt.datetime.fromisoformat(text.replace("Z", "+00:00")).date().isoformat()
        except Exception:
            continue
    return ""


def _arena_summary(arena: Any) -> str:
    if not isinstance(arena, dict):
        return ""
    name = _safe_str(arena.get("arenaName"))
    city = _safe_str(arena.get("arenaCity"))
    state = _safe_str(arena.get("arenaState"))
    parts = [part for part in (name, ", ".join(part for part in (city, state) if part)) if part]
    return " | ".join(parts)


def _team_label(team: Dict[str, Any]) -> str:
    city = _safe_str(team.get("teamCity"))
    name = _safe_str(team.get("teamName"))
    return " ".join(part for part in (city, name) if part).strip() or _safe_str(team.get("teamTricode"))


def _status_text(game_status: Any, fallback: Any) -> str:
    text = _safe_str(fallback)
    if text:
        return text
    status = _safe_int(game_status) or 0
    if status == 1:
        return "Scheduled"
    if status == 2:
        return "Live"
    if status == 3:
        return "Final"
    return "Unknown"


def _best_lan_ip() -> str:
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # No packets need to complete; this is just a common way to discover
        # the primary local interface address.
        probe.connect(("8.8.8.8", 80))
        return probe.getsockname()[0]
    except Exception:
        try:
            return socket.gethostbyname(socket.gethostname())
        except Exception:
            return "127.0.0.1"
    finally:
        probe.close()


def _fetch_schedule_page(date_str: str) -> Dict[str, Any]:
    try:
        session = requests.Session()
        session.trust_env = False
        response = session.get(SCHEDULE_PAGE.format(DATE=date_str), timeout=(4, 10), headers=WEB_HEADERS)
        if response.status_code != 200:
            return {}
        match = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', response.text)
        if not match:
            return {}
        payload = json.loads(match.group(1))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _game_card_from_parts(
    *,
    game_id: str,
    date_str: str,
    home_abbr: str,
    away_abbr: str,
    home_score: str = "",
    away_score: str = "",
    status: str = "",
    tipoff: str = "",
    arena: str = "",
    source: str = "",
) -> Dict[str, Any]:
    if home_score or away_score:
        scoreline = f"{away_abbr} {away_score or '0'} @ {home_abbr} {home_score or '0'}"
    else:
        scoreline = f"{away_abbr} @ {home_abbr}"

    return {
        "game_id": game_id,
        "date": date_str,
        "matchup": f"{away_abbr} @ {home_abbr}",
        "summary": scoreline,
        "status": status or "Unknown",
        "tipoff": tipoff,
        "arena": arena,
        "home_abbr": home_abbr,
        "away_abbr": away_abbr,
        "home_score": home_score,
        "away_score": away_score,
        "source": source,
    }


def _cached_games_for_date(game_date: dt.date) -> List[Dict[str, Any]]:
    date_str = game_date.isoformat()
    games: List[Dict[str, Any]] = []
    if not os.path.isdir(CACHE_JSON_DIR):
        return games

    for name in sorted(os.listdir(CACHE_JSON_DIR)):
        if not (name.startswith("box_") and name.endswith(".json")):
            continue
        path = os.path.join(CACHE_JSON_DIR, name)
        try:
            with open(path, "r", encoding="utf-8") as f:
                payload = json.load(f)
        except Exception:
            continue

        game = (payload.get("game") or {}) if isinstance(payload, dict) else {}
        if not isinstance(game, dict) or not game:
            continue

        game_date_str = _game_date_from_iso(game.get("gameEt"), game.get("gameTimeUTC"), game.get("gameTimeLocal"))
        if game_date_str != date_str:
            continue

        home = game.get("homeTeam") or {}
        away = game.get("awayTeam") or {}
        game_id = _safe_str(game.get("gameId")) or name.removeprefix("box_").removesuffix(".json")
        games.append(
            _game_card_from_parts(
                game_id=game_id,
                date_str=date_str,
                home_abbr=_safe_str(home.get("teamTricode")),
                away_abbr=_safe_str(away.get("teamTricode")),
                home_score=_safe_str(home.get("score")),
                away_score=_safe_str(away.get("score")),
                status=_status_text(game.get("gameStatus"), game.get("gameStatusText")),
                tipoff=_format_iso_datetime(game.get("gameEt") or game.get("gameTimeUTC") or game.get("gameTimeLocal")),
                arena=_arena_summary(game.get("arena") or {}),
                source="cache",
            )
        )

    games.sort(key=lambda item: (item.get("tipoff") or "", item.get("game_id") or ""))
    return games


def available_cached_dates(limit: int = 18) -> List[str]:
    dates = set()
    if not os.path.isdir(CACHE_JSON_DIR):
        return []
    for name in os.listdir(CACHE_JSON_DIR):
        if not (name.startswith("box_") and name.endswith(".json")):
            continue
        path = os.path.join(CACHE_JSON_DIR, name)
        try:
            with open(path, "r", encoding="utf-8") as f:
                payload = json.load(f)
        except Exception:
            continue
        game = (payload.get("game") or {}) if isinstance(payload, dict) else {}
        game_date_str = _game_date_from_iso(game.get("gameEt"), game.get("gameTimeUTC"), game.get("gameTimeLocal"))
        if game_date_str:
            dates.add(game_date_str)
    return sorted(dates, reverse=True)[:limit]


def _live_games_for_date(game_date: dt.date) -> List[Dict[str, Any]]:
    date_str = game_date.isoformat()
    payload = _fetch_schedule_page(date_str)
    page_props = (payload.get("props") or {}).get("pageProps") or {}
    game_card_feed = page_props.get("gameCardFeed") or {}
    modules = game_card_feed.get("modules") or []
    if not isinstance(modules, list):
        modules = []

    cards: List[Dict[str, Any]] = []
    for module in modules:
        if not isinstance(module, dict):
            continue
        module_cards = module.get("cards") or []
        if isinstance(module_cards, list):
            cards.extend(card for card in module_cards if isinstance(card, dict))

    games: List[Dict[str, Any]] = []
    for card in cards:
        card_data = card.get("cardData") or {}
        if not isinstance(card_data, dict):
            continue

        game_id = _safe_str(card_data.get("gameId"))
        if not game_id:
            continue

        home = card_data.get("homeTeam") or {}
        away = card_data.get("awayTeam") or {}
        home_abbr = _safe_str(home.get("teamTricode"))
        away_abbr = _safe_str(away.get("teamTricode"))
        if not home_abbr or not away_abbr:
            continue

        game_time = card_data.get("actualStartTimeUTC") or card_data.get("gameTimeUtc") or card_data.get("gameTimeEastern")
        games.append(
            _game_card_from_parts(
                game_id=game_id,
                date_str=date_str,
                home_abbr=home_abbr,
                away_abbr=away_abbr,
                home_score=_safe_str(home.get("score")),
                away_score=_safe_str(away.get("score")),
                status=_status_text(card_data.get("gameStatus"), card_data.get("gameStatusText")),
                tipoff=_format_iso_datetime(game_time),
                arena="",
                source="nba.com",
            )
        )

    return games


def fetch_games_for_date(game_date: dt.date) -> List[Dict[str, Any]]:
    games = _live_games_for_date(game_date)
    if games:
        return games
    return _cached_games_for_date(game_date)


def fetch_recent_games(days: int = 3, anchor_date: Optional[dt.date] = None) -> List[Dict[str, Any]]:
    anchor = anchor_date or dt.date.today()
    games: List[Dict[str, Any]] = []
    for offset in range(max(1, days)):
        games.extend(fetch_games_for_date(anchor - dt.timedelta(days=offset)))
    return games


def fetch_team_games(abbr: str, days: int = 30, anchor_date: Optional[dt.date] = None) -> List[Dict[str, Any]]:
    abbr = _safe_str(abbr).upper()
    games = fetch_recent_games(days=days, anchor_date=anchor_date)
    return [g for g in games if g.get("home_abbr") == abbr or g.get("away_abbr") == abbr]


def _build_player_row(player: Dict[str, Any]) -> Dict[str, Any]:
    stats = player.get("statistics") or {}
    played = _played_flag(player.get("played"))
    minutes_value = _parse_minutes_value(stats.get("minutes") or stats.get("minutesCalculated"))
    minutes = _format_minutes(stats.get("minutes") or stats.get("minutesCalculated"))
    status = _safe_str(player.get("status"))

    return {
        "name": _safe_str(player.get("name")) or " ".join(
            part for part in (_safe_str(player.get("firstName")), _safe_str(player.get("familyName"))) if part
        ),
        "starter": bool(player.get("starter")),
        "position": _safe_str(player.get("position")),
        "minutes": minutes if played else (status or ""),
        "points": _safe_int(stats.get("points")) or (0 if played else ""),
        "rebounds": _safe_int(stats.get("reboundsTotal")) or (0 if played else ""),
        "assists": _safe_int(stats.get("assists")) or (0 if played else ""),
        "steals": _safe_int(stats.get("steals")) or (0 if played else ""),
        "blocks": _safe_int(stats.get("blocks")) or (0 if played else ""),
        "turnovers": _safe_int(stats.get("turnovers")) or (0 if played else ""),
        "fg": _format_pair(stats.get("fieldGoalsMade"), stats.get("fieldGoalsAttempted")) if played else "",
        "three": _format_pair(stats.get("threePointersMade"), stats.get("threePointersAttempted")) if played else "",
        "ft": _format_pair(stats.get("freeThrowsMade"), stats.get("freeThrowsAttempted")) if played else "",
        "plus_minus": _format_plus_minus(stats.get("plusMinusPoints")) if played else "",
        "played": played,
        "minutes_value": minutes_value or 0.0,
    }


def _humanize_not_playing_reason(reason: Any) -> str:
    text = _safe_str(reason)
    if not text:
        return ""
    text = text.replace("INACTIVE_", "").replace("_", " ").title()
    replacements = {
        "Gleague": "G League",
        "Twoway": "Two-Way",
    }
    for src, dst in replacements.items():
        text = text.replace(src, dst)
    return text


def _build_absence_row(player: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if _played_flag(player.get("played")):
        return None

    name = _safe_str(player.get("name")) or " ".join(
        part for part in (_safe_str(player.get("firstName")), _safe_str(player.get("familyName"))) if part
    )
    if not name:
        return None

    status = _safe_str(player.get("status"))
    reason = _humanize_not_playing_reason(player.get("notPlayingReason"))
    detail = _safe_str(player.get("notPlayingDescription"))

    if status == "INACTIVE":
        label = "Inactive"
    elif status == "ACTIVE":
        label = "DNP"
        if not reason:
            reason = "Coach's Decision or Did Not Play"
    else:
        label = status or "Unavailable"

    return {
        "name": name,
        "label": label,
        "reason": reason,
        "detail": detail,
    }


def _build_gamebook_info(game_date: str, away_abbr: str, home_abbr: str) -> Dict[str, Any]:
    date_stamp = _safe_str(game_date).replace("-", "")
    away = _safe_str(away_abbr).upper()
    home = _safe_str(home_abbr).upper()
    if not date_stamp or not away or not home:
        return {"primary_url": "", "alternate_urls": []}

    candidates = [
        f"https://statsdmz.nba.com/pdfs/{date_stamp}/{date_stamp}_{away}{home}_book.pdf",
        f"https://statsdmz.nba.com/pdfs/{date_stamp}/{date_stamp}_{away}{home}.pdf",
    ]
    working = ""
    try:
        session = requests.Session()
        session.trust_env = False
        for candidate in candidates:
            response = session.get(candidate, timeout=(4, 12), headers={"User-Agent": WEB_HEADERS["User-Agent"]}, stream=True)
            response.close()
            if response.status_code == 200:
                working = candidate
                break
    except Exception:
        pass

    return {
        "primary_url": working or candidates[0],
        "alternate_urls": candidates if not working else [url for url in candidates if url != working],
    }


def _build_team_context(team: Dict[str, Any]) -> Dict[str, Any]:
    players = team.get("players") or []
    if not isinstance(players, list):
        players = []

    rows = [_build_player_row(player) for player in players if isinstance(player, dict)]
    rows.sort(
        key=lambda row: (
            0 if row.get("starter") else 1,
            0 if row.get("played") else 1,
            -(float(row.get("minutes_value") or 0.0)),
            row.get("name") or "",
        )
    )

    stats = team.get("statistics") or {}
    return {
        "team_id": _safe_int(team.get("teamId")) or 0,
        "abbr": _safe_str(team.get("teamTricode")),
        "name": _team_label(team),
        "score": _safe_str(team.get("score")),
        "players": rows,
        "totals": {
            "minutes": _format_minutes(stats.get("minutes") or stats.get("minutesCalculated")),
            "points": _safe_int(stats.get("points")) or 0,
            "rebounds": _safe_int(stats.get("reboundsTotal")) or 0,
            "assists": _safe_int(stats.get("assists")) or 0,
            "steals": _safe_int(stats.get("steals")) or 0,
            "blocks": _safe_int(stats.get("blocks")) or 0,
            "turnovers": _safe_int(stats.get("turnovers")) or 0,
            "fg": _format_pair(stats.get("fieldGoalsMade"), stats.get("fieldGoalsAttempted")),
            "three": _format_pair(stats.get("threePointersMade"), stats.get("threePointersAttempted")),
            "ft": _format_pair(stats.get("freeThrowsMade"), stats.get("freeThrowsAttempted")),
        },
        "periods": team.get("periods") or [],
    }


def _build_line_score(away_team: Dict[str, Any], home_team: Dict[str, Any], regulation_periods: int) -> Dict[str, Any]:
    away_periods = away_team.get("periods") or []
    home_periods = home_team.get("periods") or []
    period_count = max(len(away_periods), len(home_periods), regulation_periods or 4)
    headers: List[str] = []
    for period in range(1, period_count + 1):
        if period <= max(regulation_periods, 4):
            headers.append(f"Q{period}")
        else:
            headers.append(f"OT{period - max(regulation_periods, 4)}")

    def _row(team: Dict[str, Any]) -> Dict[str, Any]:
        scores_by_period: Dict[int, Any] = {}
        for item in team.get("periods") or []:
            if isinstance(item, dict):
                number = _safe_int(item.get("period"))
                if number is not None:
                    scores_by_period[number] = item.get("score")
        scores = [scores_by_period.get(period, "") for period in range(1, period_count + 1)]
        return {
            "label": team.get("abbr") or team.get("name") or "TEAM",
            "scores": scores,
            "total": team.get("score") or "",
        }

    return {"headers": headers, "rows": [_row(away_team), _row(home_team)]}


def build_boxscore_context(game_id: str) -> Dict[str, Any]:
    game_id = _safe_str(game_id)
    payload = fetch_json(
        CDN_BOXSCORE.format(GAME_ID=game_id),
        cache_key=f"box_{game_id}",
        ttl_sec=20,
        stale_sec=365 * 24 * 3600,
    )
    game = (payload.get("game") or {}) if isinstance(payload, dict) else {}
    if not isinstance(game, dict) or not game:
        return {
            "available": False,
            "game_id": game_id,
            "headline": f"Game {game_id}",
            "game_date": "",
            "home_team": {},
            "away_team": {},
            "line_score_headers": [],
            "line_score_rows": [],
        }

    home_team = _build_team_context(game.get("homeTeam") or {})
    away_team = _build_team_context(game.get("awayTeam") or {})
    line_score = _build_line_score(away_team, home_team, _safe_int(game.get("regulationPeriods")) or 4)
    status_text = _status_text(game.get("gameStatus"), game.get("gameStatusText"))
    headline = f"{away_team.get('abbr', 'AWAY')} {away_team.get('score', '')} @ {home_team.get('abbr', 'HOME')} {home_team.get('score', '')}".strip()
    game_date = _game_date_from_iso(game.get("gameEt"), game.get("gameTimeUTC"), game.get("gameTimeLocal"))
    home_absences = [row for row in (_build_absence_row(player) for player in (game.get("homeTeam") or {}).get("players") or []) if row]
    away_absences = [row for row in (_build_absence_row(player) for player in (game.get("awayTeam") or {}).get("players") or []) if row]
    home_absences = _enrich_absences(home_absences, team_abbr=home_team.get("abbr", ""), game_date=game_date)
    away_absences = _enrich_absences(away_absences, team_abbr=away_team.get("abbr", ""), game_date=game_date)

    return {
        "available": True,
        "game_id": game_id,
        "headline": headline,
        "matchup": f"{away_team.get('abbr', 'AWAY')} @ {home_team.get('abbr', 'HOME')}",
        "status_text": status_text,
        "tipoff": _format_iso_datetime(game.get("gameEt") or game.get("gameTimeUTC") or game.get("gameTimeLocal")),
        "arena": _arena_summary(game.get("arena") or {}),
        "attendance": _format_number(game.get("attendance")),
        "duration": _format_number(game.get("duration")),
        "game_date": game_date,
        "home_team": home_team,
        "away_team": away_team,
        "home_absences": home_absences,
        "away_absences": away_absences,
        "gamebook": _build_gamebook_info(game_date, away_team.get("abbr"), home_team.get("abbr")),
        "line_score_headers": line_score["headers"],
        "line_score_rows": line_score["rows"],
    }


def all_teams() -> List[Dict[str, str]]:
    teams = [
        {"abbr": "ATL", "name": "Atlanta Hawks"},
        {"abbr": "BOS", "name": "Boston Celtics"},
        {"abbr": "BKN", "name": "Brooklyn Nets"},
        {"abbr": "CHA", "name": "Charlotte Hornets"},
        {"abbr": "CHI", "name": "Chicago Bulls"},
        {"abbr": "CLE", "name": "Cleveland Cavaliers"},
        {"abbr": "DAL", "name": "Dallas Mavericks"},
        {"abbr": "DEN", "name": "Denver Nuggets"},
        {"abbr": "DET", "name": "Detroit Pistons"},
        {"abbr": "GSW", "name": "Golden State Warriors"},
        {"abbr": "HOU", "name": "Houston Rockets"},
        {"abbr": "IND", "name": "Indiana Pacers"},
        {"abbr": "LAC", "name": "LA Clippers"},
        {"abbr": "LAL", "name": "Los Angeles Lakers"},
        {"abbr": "MEM", "name": "Memphis Grizzlies"},
        {"abbr": "MIA", "name": "Miami Heat"},
        {"abbr": "MIL", "name": "Milwaukee Bucks"},
        {"abbr": "MIN", "name": "Minnesota Timberwolves"},
        {"abbr": "NOP", "name": "New Orleans Pelicans"},
        {"abbr": "NYK", "name": "New York Knicks"},
        {"abbr": "OKC", "name": "Oklahoma City Thunder"},
        {"abbr": "ORL", "name": "Orlando Magic"},
        {"abbr": "PHI", "name": "Philadelphia 76ers"},
        {"abbr": "PHX", "name": "Phoenix Suns"},
        {"abbr": "POR", "name": "Portland Trail Blazers"},
        {"abbr": "SAC", "name": "Sacramento Kings"},
        {"abbr": "SAS", "name": "San Antonio Spurs"},
        {"abbr": "TOR", "name": "Toronto Raptors"},
        {"abbr": "UTA", "name": "Utah Jazz"},
        {"abbr": "WAS", "name": "Washington Wizards"},
    ]
    teams.sort(key=lambda item: item["abbr"])
    return teams


try:
    POS_LOOKUP = load_position_estimate(POS_XLS_DEFAULT)
except Exception as exc:
    print(f"[startup] load_position_estimate failed (swallowed): {exc}")
    POS_LOOKUP = {}


@app.route("/teams")
def teams_page():
    return render_template("teams.html", teams=all_teams(), title="Teams")


@app.route("/team/<abbr>")
def team_page(abbr: str):
    abbr = _safe_str(abbr).upper()
    selected_date = _parse_requested_date(request.args.get("date"))
    try:
        days = int(request.args.get("days", "30"))
    except Exception:
        days = 30
    days = max(1, min(days, 365))

    games = fetch_team_games(abbr=abbr, days=days, anchor_date=selected_date)
    return render_template(
        "team.html",
        title=f"{abbr} Games",
        abbr=abbr,
        days=days,
        games=games,
        selected_date=selected_date.isoformat(),
    )


@app.route("/")
def index():
    selected_date = _parse_requested_date(request.args.get("date"))
    games = fetch_games_for_date(selected_date)
    return render_template(
        "index.html",
        title="NBA Rotations",
        games=games,
        selected_date=selected_date.isoformat(),
        prev_date=(selected_date - dt.timedelta(days=1)).isoformat(),
        next_date=(selected_date + dt.timedelta(days=1)).isoformat(),
        today_date=dt.date.today().isoformat(),
        cached_dates=available_cached_dates(),
    )


@app.route("/go", methods=["POST"])
def go():
    game_id = _safe_str(request.form.get("game_id"))
    if not game_id:
        return redirect(url_for("index"))
    return redirect(url_for("game", game_id=game_id))


@app.route("/game/<game_id>")
def game(game_id: str):
    game_id = _safe_str(game_id)
    box = build_boxscore_context(game_id)

    meta: Dict[str, Any] = {}
    try:
        home_df, away_df, meta = fetch_game_rotation(game_id)
    except Exception as exc:
        print(f"[route /game] fetch_game_rotation failed (swallowed): {exc}")
        home_df, away_df = None, None
        meta = {"notes": ["rotation fetch failed"]}

    home_team_id = int(meta.get("home_team_id") or box.get("home_team", {}).get("team_id") or 0)
    away_team_id = int(meta.get("away_team_id") or box.get("away_team", {}).get("team_id") or 0)
    home_abbr = _safe_str(meta.get("home_abbr")) or _safe_str(box.get("home_team", {}).get("abbr"))
    away_abbr = _safe_str(meta.get("away_abbr")) or _safe_str(box.get("away_team", {}).get("abbr"))

    html_fragment = ""
    try:
        figs = build_game_figs(game_id, POS_LOOKUP, meta=meta, home_df=home_df, away_df=away_df, debug=True)
        html_fragment = figs_to_html(figs)
        if not isinstance(html_fragment, str):
            html_fragment = ""
    except Exception as exc:
        print(f"[route /game] build_game_figs failed (swallowed): {exc}")
        html_fragment = (
            "<div class='empty-card'>"
            "<strong>Rotation unavailable.</strong><br/>"
            "<span>CDN data is missing or the play-by-play parse failed.</span>"
            "</div>"
        )

    out_path = ""
    try:
        out_path = os.path.join(OUT_DIR, f"rotation_{game_id}_lineup_pos48.html")
        with open(out_path, "w", encoding="utf-8") as f:
            f.write("<html><head><meta charset='utf-8'></head><body>")
            f.write(html_fragment)
            f.write("</body></html>")
    except Exception as exc:
        print(f"[route /game] saving html failed (swallowed): {exc}")
        out_path = ""

    return render_template(
        "game.html",
        title=box.get("headline") or f"Game {game_id}",
        game_id=game_id,
        box=box,
        html_fragment=html_fragment,
        out_path=out_path,
        home_team_id=home_team_id,
        away_team_id=away_team_id,
        home_abbr=home_abbr,
        away_abbr=away_abbr,
        rotation_notes=meta.get("notes") or [],
    )


@app.route("/api/matchups/<game_id>")
def api_matchups(game_id: str):
    game_id = _safe_str(game_id)
    try:
        home_team_id = int(request.args.get("home_team_id", "0"))
    except Exception:
        home_team_id = 0
    try:
        away_team_id = int(request.args.get("away_team_id", "0"))
    except Exception:
        away_team_id = 0

    try:
        guarded = build_guarded_top3_tables(game_id, home_team_id=home_team_id, away_team_id=away_team_id)
        if not isinstance(guarded, dict):
            guarded = {}
    except Exception as exc:
        print(f"[api_matchups] failed (swallowed): {exc}")
        guarded = {}

    guarded.setdefault("home", {"team_abbr": "", "rows": []})
    guarded.setdefault("away", {"team_abbr": "", "rows": []})
    return jsonify(guarded)


if __name__ == "__main__":
    host = os.environ.get("ROTATION_HOST", "0.0.0.0")
    port = int(os.environ.get("ROTATION_PORT", "5000"))
    lan_ip = _best_lan_ip()
    print(f"[rotation] local: http://127.0.0.1:{port}")
    print(f"[rotation] mobile/LAN: http://{lan_ip}:{port}")
    print("[rotation] phone/tablet access requires the device to be on the same network.")
    app.run(debug=True, host=host, port=port)
