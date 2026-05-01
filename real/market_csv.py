from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Iterable

import requests

from sportsbook_catalog import get_source


MARKET_FIELDNAMES = [
    "provider",
    "provider_event_id",
    "provider_market_id",
    "provider_league",
    "provider_market_name",
    "book",
    "book_display_name",
    "book_category",
    "sport",
    "market_type",
    "stat",
    "player_name",
    "line",
    "home_spread",
    "away_spread",
    "home_team",
    "away_team",
    "over_odds",
    "under_odds",
    "draw_odds",
    "extra_outcomes",
    "updated_at",
    "period",
    "event_date",
    "question",
]


def build_public_session(user_agent: str = "c2k-public-markets/1.0") -> requests.Session:
    session = requests.Session()
    session.trust_env = False
    session.headers.update({"accept": "application/json", "user-agent": user_agent})
    return session


def normalize_market_row(row: dict[str, Any]) -> dict[str, Any]:
    normalized = {field: "" for field in MARKET_FIELDNAMES}
    for field in MARKET_FIELDNAMES:
        if field in row:
            normalized[field] = row[field]

    book = str(normalized.get("book") or "").strip()
    if book:
        source = get_source(book)
        if source is not None:
            normalized["book"] = source.canonical
            if not normalized["book_display_name"]:
                normalized["book_display_name"] = source.display_name
            if not normalized["book_category"]:
                normalized["book_category"] = source.category
    return normalized


def dedupe_market_rows(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for row in rows:
        normalized = normalize_market_row(row)
        key = (
            normalized["provider"],
            normalized["provider_market_id"] or normalized["provider_market_name"],
            normalized["book"],
            normalized["sport"],
            normalized["market_type"],
            normalized["stat"],
            normalized["player_name"],
            str(normalized["line"]),
            normalized["home_team"],
            normalized["away_team"],
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(normalized)
    return deduped


def write_market_rows(
    path: str | Path,
    rows: Iterable[dict[str, Any]],
    *,
    append: bool = False,
) -> int:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    prepared_rows = [normalize_market_row(row) for row in rows]
    mode = "a" if append and output_path.exists() else "w"
    write_header = mode == "w"
    with output_path.open(mode, newline="", encoding="utf8") as handle:
        writer = csv.DictWriter(handle, fieldnames=MARKET_FIELDNAMES)
        if write_header:
            writer.writeheader()
        writer.writerows(prepared_rows)
    return len(prepared_rows)
