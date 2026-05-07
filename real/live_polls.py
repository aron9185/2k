from __future__ import annotations

import argparse
import csv
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from realsports_api import build_realsports_client


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT = BASE_DIR / "live_polls.csv"
EVEN_MONEY_ODDS = 100
ZERO_COST_LIVE_POLL_KINDS = {
    "anytime_play",
    "pick_a_player",
    "player_most_stat",
    "first_basket",
}
PLAYER_NAME_WITH_LINE_RE = re.compile(
    r"^(?P<name>.+?)\s*(?:·|‧|•|-|–|—)\s*\d+(?:\.\d+)?\s+\S",
    re.IGNORECASE,
)
PLAYER_NAME_FALLBACK_RE = re.compile(
    r"^(?P<name>.+?)\s+\d+(?:\.\d+)?\s+\S",
    re.IGNORECASE,
)
PLAYER_NAME_SPLIT_RE = re.compile(r"\s*[·•\-–—]\s*")


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Fetch Real Sports poll posts and expand them into a "
            "flat CSV that is easier to inspect and match against sportsbook markets."
        )
    )
    parser.add_argument(
        "--source",
        choices=("livefeed", "home", "sport-polls", "game-feed"),
        default="livefeed",
        help=(
            "Poll source: the legacy livefeed, a sport home-tab feed, "
            "the dedicated sport polls tab, or a single game feed."
        ),
    )
    parser.add_argument("--feed", default="all", help="Livefeed segment, e.g. all.")
    parser.add_argument(
        "--sport",
        default="",
        help="Sport key such as mlb when --source home, --source sport-polls, or --source game-feed is used.",
    )
    parser.add_argument(
        "--cohort",
        type=int,
        default=0,
        help="Home-tab cohort index when --source home is used.",
    )
    parser.add_argument(
        "--game-id",
        default="",
        help="Game id when --source game-feed is used.",
    )
    parser.add_argument(
        "--view",
        default="recent",
        help="Game-feed view name such as recent, top, or all when --source game-feed is used.",
    )
    parser.add_argument(
        "--view-frame",
        default="default",
        help="Game-feed frame such as default when --source game-feed is used.",
    )
    parser.add_argument(
        "--version",
        default="2",
        help="Game-feed API version when --source game-feed is used.",
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
        help="Legacy alias; locked polls are included by default.",
    )
    parser.add_argument(
        "--unlocked-only",
        action="store_true",
        help="Keep only polls whose options are not all locked.",
    )
    parser.add_argument(
        "--wagerable-only",
        action="store_true",
        help="Keep only polls where Real currently reports canWager=true.",
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
    poll_ids = _extract_poll_ids(post)
    return poll_ids[0] if poll_ids else None


def _extract_poll_ids(post: dict[str, Any]) -> list[int]:
    additional_info = post.get("additionalInfo") or {}
    values: list[Any] = []
    poll_ids_value = additional_info.get("pollIds")
    if isinstance(poll_ids_value, list):
        values.extend(poll_ids_value)
    values.append(additional_info.get("pollId"))

    nodes = ((post.get("content") or {}).get("nodes")) or []
    for node in nodes:
        values.append(node.get("pollId"))

    poll_ids: list[int] = []
    seen: set[int] = set()
    for poll_id in values:
        if poll_id in (None, "", "None"):
            continue
        try:
            parsed = int(poll_id)
        except Exception:
            continue
        if parsed in seen:
            continue
        seen.add(parsed)
        poll_ids.append(parsed)
    return poll_ids


def _parse_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    text = text.replace("Z", "+00:00")
    if re.search(r"[+-]\d{2}$", text):
        text = f"{text}:00"
    try:
        parsed = datetime.fromisoformat(text)
    except Exception:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _lock_time_elapsed(value: Any, *, now_utc: datetime | None = None) -> bool:
    parsed = _parse_datetime(value)
    if parsed is None:
        return False
    return parsed <= (now_utc or datetime.now(timezone.utc))


def _is_closed_poll_row(row: dict[str, Any], poll: dict[str, Any]) -> bool:
    if bool(row.get("is_locked")):
        return True
    if _lock_time_elapsed(row.get("locks_at")):
        return True
    if poll.get("canWager", False):
        return False
    return str(row.get("poll_kind") or "").strip().lower() not in ZERO_COST_LIVE_POLL_KINDS


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
    odds = additional.get("odds")
    if odds in (None, "", "None"):
        odds = option.get("odds")
    if odds in (None, "", "None"):
        return EVEN_MONEY_ODDS
    return int(odds)


def _option_additional(option: dict[str, Any]) -> dict[str, Any]:
    return option.get("additionalInfo") or {}


def _option_team_id(option: dict[str, Any]) -> Any:
    return _option_additional(option).get("teamId") or ""


def _option_player_id(option: dict[str, Any]) -> Any:
    return _option_additional(option).get("playerId") or ""


def _normalize_poll_kind(post: dict[str, Any], poll: dict[str, Any]) -> str:
    additional = poll.get("additionalInfo") or {}
    post_additional = post.get("additionalInfo") or {}
    poll_type = str(additional.get("type") or post_additional.get("type") or "").strip().lower()
    header = str(post.get("header") or "").strip().lower()
    is_anytime = bool(additional.get("isAnytimePlay") or post_additional.get("isAnytimePlay"))
    if is_anytime:
        return "pick_a_player" if header == "pick a player" else "anytime_play"
    if additional.get("isHeadToHead") or post_additional.get("isHeadToHead"):
        return "player_head_to_head"
    if additional.get("isOverUnder") or post_additional.get("isOverUnder"):
        return "player_over_under" if poll_type == "player" else "game_total"
    if poll_type == "gamewinner" or additional.get("isPickWinner"):
        return "game_winner"
    if poll_type == "bothteamsscore":
        return "both_teams_score"
    if poll_type == "doublechance":
        return "double_chance"
    if poll_type == "halftimeresult":
        return "halftime_result"
    if poll_type == "midgame":
        point_spread = additional.get("pointSpread")
        try:
            spread_value = float(point_spread)
        except Exception:
            spread_value = None
        if spread_value is not None:
            return "period_winner" if spread_value == 0 else "game_spread"
        return "midgame"
    return poll_type


def _extract_player_name(content_text: str, *, poll_kind: str, entity_type: str) -> str:
    if poll_kind != "player_over_under":
        return ""
    if entity_type and entity_type != "player":
        return ""
    text = str(content_text or "").strip()
    if not text:
        return ""
    match = PLAYER_NAME_WITH_LINE_RE.match(text) or PLAYER_NAME_FALLBACK_RE.match(text)
    if match:
        player_name = str(match.group("name") or "").strip(" .")
    elif "·" in text:
        player_name = str(text.split("·", 1)[0] or "").strip(" .")
    else:
        return ""
    if not player_name or player_name.lower().startswith("to "):
        return ""
    return player_name


def _format_play_types(value: Any) -> str:
    if isinstance(value, list):
        parts = [str(item).strip() for item in value if str(item).strip()]
        return " | ".join(parts)
    return str(value or "").strip()


def _normalize_poll_row(post: dict[str, Any], poll_payload: dict[str, Any]) -> dict[str, Any]:
    poll = poll_payload.get("poll") or {}
    additional = poll.get("additionalInfo") or {}
    post_additional = post.get("additionalInfo") or {}
    options = poll.get("options") or []
    over_option, under_option = _choose_over_under_options(options)
    explicit_option_odds = any(
        ((option.get("additionalInfo") or {}).get("odds") not in (None, "", "None"))
        for option in options
    )
    content_text = _first_text(((post.get("content") or {}).get("nodes")) or [])
    poll_kind = _normalize_poll_kind(post, poll)
    entity_type = str(additional.get("entityType") or additional.get("type") or "").strip()

    return {
        "source": "livefeed",
        "post_id": post.get("id"),
        "poll_id": poll.get("id"),
        "sport": poll.get("sport") or post.get("sport"),
        "game_id": poll.get("gameId") or post.get("gameId"),
        "day": poll.get("day"),
        "created_at": post.get("createdAt") or "",
        "poll_created_at": poll.get("createdAt") or "",
        "header": post.get("header") or "",
        "content_text": content_text,
        "poll_kind": poll_kind,
        "market_type": additional.get("type") or (post.get("additionalInfo") or {}).get("type") or "",
        "entity_type": entity_type,
        "stat": additional.get("stat") or "",
        "player_id": additional.get("playerId") or "",
        "player_name": _extract_player_name(content_text, poll_kind=poll_kind, entity_type=entity_type),
        "period": additional.get("period"),
        "is_over_under": bool(additional.get("isOverUnder")),
        "line": additional.get("overUnderAmount"),
        "point_spread": poll.get("pointSpread"),
        "spread_team_id": additional.get("spreadTeamId") or "",
        "home_team": poll.get("homeTeamKey"),
        "away_team": poll.get("awayTeamKey"),
        "home_moneyline": poll.get("homeMoneyline"),
        "away_moneyline": poll.get("awayMoneyline"),
        "locks_at": poll.get("locksAt"),
        "lock_time_elapsed": additional.get("lockTimeElapsed") or "",
        "after_time_elapsed": additional.get("afterTimeElapsed") or "",
        "play_type": additional.get("playType") or "",
        "play_types": _format_play_types(additional.get("playTypes")),
        "params_json": json.dumps(additional.get("params") or {}, separators=(",", ":"), ensure_ascii=True),
        "is_anytime_play": bool(additional.get("isAnytimePlay") or post_additional.get("isAnytimePlay")),
        "is_head_to_head": bool(additional.get("isHeadToHead") or post_additional.get("isHeadToHead")),
        "is_pick_winner": bool(additional.get("isPickWinner")),
        "is_moneyline": bool(additional.get("isMoneyline")),
        "is_midgame": bool(additional.get("isMidgame") or str(poll_kind).startswith("game_") or poll_kind == "period_winner"),
        "can_wager": poll.get("canWager"),
        "min_wager": poll.get("minWager"),
        "max_wager": poll.get("maxWager"),
        "is_locked": all(bool(option.get("isLocked")) for option in options) if options else False,
        "has_explicit_odds": explicit_option_odds,
        "option_1_label": (options[0].get("label") if len(options) > 0 else ""),
        "option_1_odds": (_normalize_option_odds(options[0]) if len(options) > 0 else ""),
        "option_1_count": (options[0].get("count") if len(options) > 0 else ""),
        "option_1_team_id": (_option_team_id(options[0]) if len(options) > 0 else ""),
        "option_1_player_id": (_option_player_id(options[0]) if len(options) > 0 else ""),
        "option_2_label": (options[1].get("label") if len(options) > 1 else ""),
        "option_2_odds": (_normalize_option_odds(options[1]) if len(options) > 1 else ""),
        "option_2_count": (options[1].get("count") if len(options) > 1 else ""),
        "option_2_team_id": (_option_team_id(options[1]) if len(options) > 1 else ""),
        "option_2_player_id": (_option_player_id(options[1]) if len(options) > 1 else ""),
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


def fetch_live_polls(
    feed: str = "all",
    *,
    include_locked: bool = True,
    wagerable_only: bool = False,
    limit: int = 0,
):
    client = build_realsports_client()
    payload = client.get_livefeed_posts(feed=feed)
    posts = payload.get("posts") or []
    if limit > 0:
        posts = posts[:limit]

    rows = []
    raw = []
    seen_poll_ids: set[int] = set()

    for post in posts:
        for poll_id in _extract_poll_ids(post):
            if poll_id in seen_poll_ids:
                continue
            seen_poll_ids.add(poll_id)

            poll_payload = client.get_poll(poll_id)
            poll = poll_payload.get("poll") or {}
            if wagerable_only and not poll.get("canWager", False):
                continue

            row = _normalize_poll_row(post, poll_payload)
            if not include_locked and _is_closed_poll_row(row, poll):
                continue

            rows.append(row)
            raw.append({"post": post, "poll_payload": poll_payload})

    return rows, raw


def fetch_home_tab_polls(
    sport: str,
    *,
    cohort: int = 0,
    include_locked: bool = True,
    wagerable_only: bool = False,
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
        for poll_id in _extract_poll_ids(post):
            if poll_id in seen_poll_ids:
                continue

            poll_payload = client.get_poll(poll_id)
            if not _filter_requested_sport(post, poll_payload, sport):
                continue

            seen_poll_ids.add(poll_id)
            poll = poll_payload.get("poll") or {}
            if wagerable_only and not poll.get("canWager", False):
                continue

            row = _normalize_poll_row(post, poll_payload)
            if not include_locked and _is_closed_poll_row(row, poll):
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


def fetch_game_feed_polls(
    sport: str,
    *,
    game_id: int | str,
    view: str = "recent",
    view_frame: str = "default",
    version: int | str = 2,
    include_locked: bool = True,
    wagerable_only: bool = False,
    limit: int = 0,
):
    client = build_realsports_client()
    payload = client.get_game_feed(
        game_id,
        sport=sport,
        version=version,
        view=view,
        view_frame=view_frame,
    )
    posts = payload.get("posts") or []
    if limit > 0:
        posts = posts[:limit]

    rows = []
    entries = []
    seen_poll_ids: set[int] = set()

    for post in posts:
        for poll_id in _extract_poll_ids(post):
            if poll_id in seen_poll_ids:
                continue

            poll_payload = client.get_poll(poll_id)
            if not _filter_requested_sport(post, poll_payload, sport):
                continue

            seen_poll_ids.add(poll_id)
            poll = poll_payload.get("poll") or {}
            if wagerable_only and not poll.get("canWager", False):
                continue

            row = _normalize_poll_row(post, poll_payload)
            if not include_locked and _is_closed_poll_row(row, poll):
                continue

            rows.append(row)
            entries.append(
                {
                    "post": post,
                    "poll_payload": poll_payload,
                }
            )

    raw = {
        "game_id": str(game_id),
        "sport": sport,
        "view": view,
        "view_frame": view_frame,
        "version": str(version),
        "game_payload": payload,
        "entries": entries,
    }
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
    include_locked: bool = True,
    wagerable_only: bool = False,
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
        for poll_id in _extract_poll_ids(post):
            if poll_id in seen_poll_ids:
                continue

            poll_payload = client.get_poll(poll_id)
            if not _filter_requested_sport(post, poll_payload, sport):
                continue

            seen_poll_ids.add(poll_id)
            poll = poll_payload.get("poll") or {}
            if wagerable_only and not poll.get("canWager", False):
                continue

            row = _normalize_poll_row(post, poll_payload)
            if not include_locked and _is_closed_poll_row(row, poll):
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
        "source",
        "post_id",
        "poll_id",
        "sport",
        "game_id",
        "day",
        "created_at",
        "poll_created_at",
        "header",
        "content_text",
        "poll_kind",
        "market_type",
        "entity_type",
        "stat",
        "player_id",
        "player_name",
        "period",
        "is_over_under",
        "line",
        "point_spread",
        "spread_team_id",
        "home_team",
        "away_team",
        "home_moneyline",
        "away_moneyline",
        "locks_at",
        "lock_time_elapsed",
        "after_time_elapsed",
        "play_type",
        "play_types",
        "params_json",
        "is_anytime_play",
        "is_head_to_head",
        "is_pick_winner",
        "is_moneyline",
        "is_midgame",
        "can_wager",
        "min_wager",
        "max_wager",
        "is_locked",
        "has_explicit_odds",
        "option_1_label",
        "option_1_odds",
        "option_1_count",
        "option_1_team_id",
        "option_1_player_id",
        "option_2_label",
        "option_2_odds",
        "option_2_count",
        "option_2_team_id",
        "option_2_player_id",
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
    include_locked = args.include_locked or not args.unlocked_only
    if args.source == "home":
        if not args.sport:
            raise SystemExit("--sport is required when --source home is used.")
        rows, raw = fetch_home_tab_polls(
            sport=args.sport,
            cohort=args.cohort,
            include_locked=include_locked,
            wagerable_only=args.wagerable_only,
            limit=args.limit,
        )
    elif args.source == "sport-polls":
        if not args.sport:
            raise SystemExit("--sport is required when --source sport-polls is used.")
        rows, raw = fetch_sport_tab_polls(
            sport=args.sport,
            day=args.day,
            poll_type=args.poll_type,
            include_locked=include_locked,
            wagerable_only=args.wagerable_only,
            limit=args.limit,
        )
    elif args.source == "game-feed":
        if not args.sport:
            raise SystemExit("--sport is required when --source game-feed is used.")
        if not args.game_id:
            raise SystemExit("--game-id is required when --source game-feed is used.")
        rows, raw = fetch_game_feed_polls(
            sport=args.sport,
            game_id=args.game_id,
            view=args.view,
            view_frame=args.view_frame,
            version=args.version,
            include_locked=include_locked,
            wagerable_only=args.wagerable_only,
            limit=args.limit,
        )
    else:
        rows, raw = fetch_live_polls(
            feed=args.feed,
            include_locked=include_locked,
            wagerable_only=args.wagerable_only,
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
