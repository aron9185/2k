from __future__ import annotations

import argparse
import json
import re
import sys
from typing import Any
from urllib.parse import urlparse

from realsports_api import RealSportsError, build_realsports_client


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Resolve a Real post/comments/poll URL and build the poll vote payload. "
            "Dry-run by default; add --submit to make the PUT request."
        )
    )
    parser.add_argument(
        "target",
        nargs="?",
        default="",
        help="Real comments URL, post URL, poll URL, post id, or poll id.",
    )
    parser.add_argument("--post-id", default="", help="Resolve the attached poll from this post id.")
    parser.add_argument("--poll-id", default="", help="Use this poll id directly.")
    parser.add_argument("--selection", default="", help="Poll option label to choose, e.g. ARS.")
    parser.add_argument("--option-id", default="", help="Use this Real poll option id directly.")
    parser.add_argument("--wager", type=int, default=None, help="Karma wager to submit. Defaults to 0 on wagerable polls.")
    parser.add_argument("--clear", action="store_true", help="Build a clear/unselect payload.")
    parser.add_argument("--submit", action="store_true", help="Actually PUT the response to Real.")
    return parser.parse_args()


def _find_first_int(pattern: str, text: str) -> str:
    match = re.search(pattern, text)
    return match.group(1) if match else ""


def infer_ids(target: str) -> tuple[str, str]:
    value = str(target or "").strip()
    if not value:
        return "", ""

    parsed = urlparse(value)
    path = parsed.path if parsed.scheme else value
    post_id = _find_first_int(r"/comments/type/post/entity/(\d+)", path)
    if not post_id:
        post_id = _find_first_int(r"/posts/(\d+)", path)
    poll_id = _find_first_int(r"/polls/(\d+)", path)

    if not post_id and not poll_id and re.fullmatch(r"\d+", value):
        post_id = value
    return post_id, poll_id


def extract_poll_id_from_post(post: dict[str, Any]) -> str:
    additional = post.get("additionalInfo") or {}
    poll_id = additional.get("pollId")
    if poll_id:
        return str(poll_id)

    for node in post.get("content") or []:
        if not isinstance(node, dict):
            continue
        poll_id = node.get("pollId")
        if poll_id:
            return str(poll_id)
    return ""


def normalize_label(value: Any) -> str:
    text = str(value or "").strip().lower()
    return re.sub(r"[^a-z0-9]+", "", text)


def coerce_numeric_id(value: Any) -> Any:
    text = str(value or "").strip()
    if re.fullmatch(r"\d+", text):
        return int(text)
    return value


def option_summary(option: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": option.get("id"),
        "label": option.get("label"),
        "odds": option.get("odds"),
        "isLocked": option.get("isLocked"),
        "avatarSource": option.get("avatarSource"),
    }


def load_options(client: Any, poll_id: str, poll: dict[str, Any], selection: str) -> list[dict[str, Any]]:
    options = [option for option in (poll.get("options") or []) if isinstance(option, dict)]
    if options or not selection:
        return options

    option_payload = client.get_poll_options(poll_id, query=selection)
    return [option for option in (option_payload.get("options") or []) if isinstance(option, dict)]


def find_option(options: list[dict[str, Any]], *, selection: str, option_id: str) -> dict[str, Any] | None:
    if option_id:
        for option in options:
            if str(option.get("id") or "") == option_id:
                return option
        return {"id": coerce_numeric_id(option_id), "label": selection or None}

    target = normalize_label(selection)
    if not target:
        return None
    for option in options:
        if normalize_label(option.get("label")) == target:
            return option
    for option in options:
        label = normalize_label(option.get("label"))
        if label and (target in label or label in target):
            return option
    return None


def build_vote_payload(
    poll: dict[str, Any],
    option: dict[str, Any],
    *,
    wager: int | None,
    is_clear: bool,
) -> dict[str, Any]:
    resolved_wager = wager
    if resolved_wager is None and poll.get("canWager"):
        resolved_wager = 0

    payload: dict[str, Any] = {
        "pollOptionId": option.get("id"),
        "userPassIds": [],
        "removeUserPassIds": [],
        "userPassSport": poll.get("sport"),
        "isClear": is_clear,
    }
    if resolved_wager is not None:
        payload["wager"] = resolved_wager
    if option.get("label") is not None:
        payload["label"] = option.get("label")
    if option.get("avatarSource") is not None:
        payload["avatarSource"] = option.get("avatarSource")
    return payload


def main() -> int:
    args = parse_args()
    post_id, poll_id = infer_ids(args.target)
    post_id = args.post_id or post_id
    poll_id = args.poll_id or poll_id

    client = build_realsports_client()
    post: dict[str, Any] | None = None
    if not poll_id and post_id:
        post_payload = client.get_post(post_id)
        post = post_payload.get("post") or {}
        poll_id = extract_poll_id_from_post(post)

    if not poll_id:
        raise RealSportsError("Could not resolve a poll id. Pass --poll-id or a post/comments URL with a poll.")

    poll_payload = client.get_poll(poll_id)
    poll = poll_payload.get("poll") or {}
    response = poll_payload.get("response")
    options = load_options(client, poll_id, poll, args.selection)
    selected_option = find_option(options, selection=args.selection, option_id=args.option_id)
    if not selected_option and args.clear and isinstance(response, dict) and response.get("pollOptionId"):
        selected_option = {
            "id": response.get("pollOptionId"),
            "label": response.get("label"),
            "avatarSource": response.get("avatarSource"),
        }

    result: dict[str, Any] = {
        "mode": "submit" if args.submit else "dry_run",
        "postId": post_id or (post or {}).get("id"),
        "pollId": poll_id,
        "poll": {
            "sport": poll.get("sport"),
            "type": poll.get("type"),
            "additionalInfo": poll.get("additionalInfo"),
            "canWager": poll.get("canWager"),
            "minWager": poll.get("minWager"),
            "maxWager": poll.get("maxWager"),
            "locksAt": poll.get("locksAt"),
            "currentResponse": response,
        },
        "options": [option_summary(option) for option in options],
    }

    if selected_option:
        payload = build_vote_payload(
            poll,
            selected_option,
            wager=args.wager,
            is_clear=args.clear,
        )
        result["selectedOption"] = option_summary(selected_option)
        result["put"] = {
            "method": "PUT",
            "url": f"https://web.realapp.com/polls/{poll_id}",
            "json": payload,
        }
        if args.submit:
            result["response"] = client.submit_poll_response(
                poll_id,
                poll_option_id=payload["pollOptionId"],
                user_pass_ids=payload.get("userPassIds"),
                remove_user_pass_ids=payload.get("removeUserPassIds"),
                user_pass_sport=payload.get("userPassSport"),
                wager=payload.get("wager"),
                label=payload.get("label"),
                avatar_source=payload.get("avatarSource"),
                is_clear=payload.get("isClear", False),
            )
    elif args.selection or args.option_id:
        result["error"] = "No matching option found for selection/option id."
    else:
        result["next"] = "Pass --selection or --option-id to build the vote payload."

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if "error" not in result else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RealSportsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
