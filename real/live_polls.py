from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

from realsports_api import build_realsports_client


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT = BASE_DIR / "live_polls.csv"
EVEN_MONEY_ODDS = 100


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Fetch Real Sports poll posts and expand them into a "
            "flat CSV that is easier to inspect and match against sportsbook markets."
        )
    )
    parser.add_argument(
        "--source",
        choices=("livefeed", "home", "sport-polls"),
        default="livefeed",
        help="Poll source: the legacy livefeed, a sport home-tab feed, or the dedicated sport polls tab.",
    )
    parser.add_argument("--feed", default="all", help="Livefeed segment, e.g. all.")
    parser.add_argument(
        "--sport",
        default="",
        help="Home-tab sport key such as mlb when --source home is used.",
    )
    parser.add_argument(
        "--cohort",
        type=int,
        default=0,
        help="Home-tab cohort index when --source home is used.",
    )
    parser.add_argument(
        "--day",
        default="",
        help="Poll-tab day key such as 2026-04-20 when --source sport-polls is used.",
    )
    parser.add_argument(
        "--poll-type",
        default="all",
        help="Poll-tab type id when --source sport-polls is used, e.g. all, player, gamewinner, totaloverunder.",
    )
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="CSV output path.")
    parser.add_argument(
        "--include-locked",
        action="store_true",
        help="Keep polls that are already locked.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Optional max number of posts to inspect before expanding polls.",
    )
    parser.add_argument(
        "--dump-json",
        default="",
        help="Optional JSON output path for the raw expanded poll objects.",
    )
    return parser.parse_args()


def _first_text(nodes: list[dict[str, Any]] | None) -> str:
    if not isinstance(nodes, list):
        return ""
    parts: list[str] = []
    for node in nodes:
        children = node.get("children") or []
        if not isinstance(children, list):
            continue
        for child in children:
            text = str(child.get("text") or "").strip()
            if text:
                parts.append(text)
    return " ".join(parts).strip()


def _extract_poll_id(post: dict[str, Any]) -> int | None:
    additional_info = post.get("additionalInfo") or {}
    poll_id = additional_info.get("pollId")
    if poll_id:
        try:
            return int(poll_id)
        except Exception:
            pass

    nodes = ((post.get("content") or {}).get("nodes")) or []
    for node in nodes:
        poll_id = node.get("pollId")
        if poll_id:
            try:
                return int(poll_id)
            except Exception:
                return None
    return None


def _extract_option_flags(option: dict[str, Any]) -> tuple[bool | None, bool | None]:
    additional = option.get("additionalInfo") or {}
    over_flag = additional.get("over")
    if over_flag is True:
        return True, False
    if over_flag is False:
        return False, True

    label = str(option.get("label") or "").strip().lower()
    if label == "over":
        return True, False
    if label == "under":
        return False, True
    return None, None


def _choose_over_under_options(options: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
    over_option: dict[str, Any] | None = None
    under_option: dict[str, Any] | None = None
    for option in options:
        is_over, is_under = _extract_option_flags(option)
        if is_over:
            over_option = option
        if is_under:
            under_option = option
    return over_option or {}, under_option or {}


def _normalize_option_odds(option: dict[str, Any]) -> int:
    additional = option.get("additionalInfo") or {}
    odds = additional.get("odds", option.get("odds"))
    if odds in (None, "", "None"):
        return EVEN_MONEY_ODDS
    return int(odds)


def _normalize_poll_row(post: dict[str, Any], poll_payload: dict[str, Any]) -> dict[str, Any]:
    poll = poll_payload.get("poll") or {}
    additional = poll.get("additionalInfo") or {}
    options = poll.get("options") or []
    over_option, under_option = _choose_over_under_options(options)
    explicit_option_odds = any(
        ((option.get("additionalInfo") or {}).get("odds") not in (None, "", "None"))
        for option in options
    )

    return {
        "post_id": post.get("id"),
        "poll_id": poll.get("id"),
        "sport": poll.get("sport") or post.get("sport"),
        "game_id": poll.get("gameId") or post.get("gameId"),
        "day": poll.get("day"),
        "header": post.get("header") or "",
        "content_text": _first_text(((post.get("content") or {}).get("nodes")) or []),
        "market_type": additional.get("type") or (post.get("additionalInfo") or {}).get("type") or "",
        "entity_type": additional.get("type") or "",
        "stat": additional.get("stat") or "",
        "player_id": additional.get("playerId") or "",
        "period": additional.get("period"),
        "is_over_under": bool(additional.get("isOverUnder")),
        "line": additional.get("overUnderAmount"),
        "point_spread": poll.get("pointSpread"),
        "home_team": poll.get("homeTeamKey"),
        "away_team": poll.get("awayTeamKey"),
        "home_moneyline": poll.get("homeMoneyline"),
        "away_moneyline": poll.get("awayMoneyline"),
        "locks_at": poll.get("locksAt"),
        "can_wager": poll.get("canWager"),
        "min_wager": poll.get("minWager"),
        "max_wager": poll.get("maxWager"),
        "is_locked": all(bool(option.get("isLocked")) for option in options) if options else False,
        "has_explicit_odds": explicit_option_odds,
        "option_1_label": (options[0].get("label") if len(options) > 0 else ""),
        "option_1_odds": (_normalize_option_odds(options[0]) if len(options) > 0 else ""),
        "option_1_count": (options[0].get("count") if len(options) > 0 else ""),
        "option_2_label": (options[1].get("label") if len(options) > 1 else ""),
        "option_2_odds": (_normalize_option_odds(options[1]) if len(options) > 1 else ""),
        "option_2_count": (options[1].get("count") if len(options) > 1 else ""),
        "over_odds": (_normalize_option_odds(over_option) if over_option else ""),
        "under_odds": (_normalize_option_odds(under_option) if under_option else ""),
        "over_count": over_option.get("count", ""),
        "under_count": under_option.get("count", ""),
    }


def _collect_home_tab_post_refs(payload: dict[str, Any]) -> list[dict[str, Any]]:
    latest_day = payload.get("latestDayContent") or {}
    items = latest_day.get("items") or []
    refs: list[dict[str, Any]] = []
    for item in items:
        if str(item.get("entityType") or "").lower() != "post":
            continue
        for post_ref in item.get("posts") or []:
            if isinstance(post_ref, dict) and post_ref.get("id"):
                refs.append(post_ref)
    return refs


def _filter_requested_sport(post: dict[str, Any], poll_payload: dict[str, Any], sport: str) -> bool:
    requested = str(sport or "").strip().lower()
    if not requested:
        return True
    poll = poll_payload.get("poll") or {}
    poll_sport = str(poll.get("sport") or "").strip().lower()
    post_sport = str(post.get("sport") or "").strip().lower()
    return requested in {poll_sport, post_sport}


def fetch_live_polls(feed: str = "all", *, include_locked: bool = False, limit: int = 0):
    client = build_realsports_client()
    payload = client.get_livefeed_posts(feed=feed)
    posts = payload.get("posts") or []
    if limit > 0:
        posts = posts[:limit]

    rows = []
    raw = []
    seen_poll_ids: set[int] = set()

    for post in posts:
        poll_id = _extract_poll_id(post)
        if not poll_id or poll_id in seen_poll_ids:
            continue
        seen_poll_ids.add(poll_id)

        poll_payload = client.get_poll(poll_id)
        poll = poll_payload.get("poll") or {}
        if not include_locked and not poll.get("canWager", False):
            continue

        row = _normalize_poll_row(post, poll_payload)
        if not include_locked and row["is_locked"]:
            continue

        rows.append(row)
        raw.append({"post": post, "poll_payload": poll_payload})

    return rows, raw


def fetch_home_tab_polls(
    sport: str,
    *,
    cohort: int = 0,
    include_locked: bool = False,
    limit: int = 0,
):
    client = build_realsports_client()
    payload = client.get_home_tab(sport=sport, cohort=cohort)
    post_refs = _collect_home_tab_post_refs(payload)
    if limit > 0:
        post_refs = post_refs[:limit]

    rows = []
    raw = []
    seen_poll_ids: set[int] = set()

    for post_ref in post_refs:
        post_payload = client.get_post(post_ref["id"])
        post = post_payload.get("post") or {}
        poll_id = _extract_poll_id(post)
        if not poll_id or poll_id in seen_poll_ids:
            continue

        poll_payload = client.get_poll(poll_id)
        if not _filter_requested_sport(post, poll_payload, sport):
            continue

        seen_poll_ids.add(poll_id)
        poll = poll_payload.get("poll") or {}
        if not include_locked and not poll.get("canWager", False):
            continue

        row = _normalize_poll_row(post, poll_payload)
        if not include_locked and row["is_locked"]:
            continue

        rows.append(row)
        raw.append(
            {
                "home_payload": payload,
                "post_ref": post_ref,
                "post_payload": post_payload,
                "poll_payload": poll_payload,
            }
        )

    return rows, raw


def _choose_default_poll_day(info_payload: dict[str, Any]) -> str:
    day_options = info_payload.get("dayOptions") or []
    for option in day_options:
        if str(option.get("label") or "").strip().lower() == "active":
            return str(option.get("id") or "").strip()
    if day_options:
        return str(day_options[0].get("id") or "").strip()
    return ""


def fetch_sport_tab_polls(
    sport: str,
    *,
    day: str = "",
    poll_type: str = "all",
    include_locked: bool = False,
    limit: int = 0,
):
    client = build_realsports_client()
    info_payload = client.get_polls_info_for_sport(sport)
    resolved_day = day or _choose_default_poll_day(info_payload)
    if not resolved_day:
        raise RuntimeError(f"No day options were returned for sport '{sport}'.")

    payload = client.get_polls_for_sport_day(sport, day=resolved_day, poll_type=poll_type)
    posts = payload.get("posts") or []
    if limit > 0:
        posts = posts[:limit]

    rows = []
    raw = []
    seen_poll_ids: set[int] = set()

    for post in posts:
        poll_id = _extract_poll_id(post)
        if not poll_id or poll_id in seen_poll_ids:
            continue

        poll_payload = client.get_poll(poll_id)
        if not _filter_requested_sport(post, poll_payload, sport):
            continue

        seen_poll_ids.add(poll_id)
        poll = poll_payload.get("poll") or {}
        if not include_locked and not poll.get("canWager", False):
            continue

        row = _normalize_poll_row(post, poll_payload)
        if not include_locked and row["is_locked"]:
            continue

        rows.append(row)
        raw.append(
            {
                "poll_info": info_payload,
                "resolved_day": resolved_day,
                "poll_type": poll_type,
                "post": post,
                "poll_payload": poll_payload,
            }
        )

    return rows, raw


def write_csv(path: str | Path, rows: list[dict[str, Any]]) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "post_id",
        "poll_id",
        "sport",
        "game_id",
        "day",
        "header",
        "content_text",
        "market_type",
        "entity_type",
        "stat",
        "player_id",
        "period",
        "is_over_under",
        "line",
        "point_spread",
        "home_team",
        "away_team",
        "home_moneyline",
        "away_moneyline",
        "locks_at",
        "can_wager",
        "min_wager",
        "max_wager",
        "is_locked",
        "has_explicit_odds",
        "option_1_label",
        "option_1_odds",
        "option_1_count",
        "option_2_label",
        "option_2_odds",
        "option_2_count",
        "over_odds",
        "under_odds",
        "over_count",
        "under_count",
    ]
    with output_path.open("w", newline="", encoding="utf8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main():
    args = parse_args()
    if args.source == "home":
        if not args.sport:
            raise SystemExit("--sport is required when --source home is used.")
        rows, raw = fetch_home_tab_polls(
            sport=args.sport,
            cohort=args.cohort,
            include_locked=args.include_locked,
            limit=args.limit,
        )
    elif args.source == "sport-polls":
        if not args.sport:
            raise SystemExit("--sport is required when --source sport-polls is used.")
        rows, raw = fetch_sport_tab_polls(
            sport=args.sport,
            day=args.day,
            poll_type=args.poll_type,
            include_locked=args.include_locked,
            limit=args.limit,
        )
    else:
        rows, raw = fetch_live_polls(
            feed=args.feed,
            include_locked=args.include_locked,
            limit=args.limit,
        )
    write_csv(args.output, rows)
    print(f"Saved {len(rows)} live poll rows to {args.output}")

    if args.dump_json:
        dump_path = Path(args.dump_json)
        dump_path.parent.mkdir(parents=True, exist_ok=True)
        dump_path.write_text(json.dumps(raw, indent=2, ensure_ascii=False), encoding="utf8")
        print(f"Saved expanded live poll JSON to {dump_path}")


if __name__ == "__main__":
    main()
