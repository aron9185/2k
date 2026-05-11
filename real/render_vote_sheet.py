from __future__ import annotations

import argparse
import base64
import csv
import json
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from fair_odds import american_to_implied_prob, net_payout_per_unit, probability_to_american
from poll_market_matcher import normalize_team
from render_prediction_sheet import prediction_summary_line, render_prediction_sections


BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "output"
DEFAULT_INPUT = BASE_DIR / "poll_vote_recommendations.csv"
DEFAULT_LINEUP_INPUT = BASE_DIR / "lineup.csv"
DEFAULT_PREDICTION_INPUT = BASE_DIR / "prediction_market_recommendations.csv"
DEFAULT_PREDICTION_POSITIONS_INPUT = BASE_DIR / "prediction_position_recommendations.csv"
DEFAULT_PREDICTION_MARKETS_CSV = BASE_DIR / "sportsbook_markets_consensus_live.csv"
LINEUPS_DIR = BASE_DIR / "lineups"
DAILY_LINEUP_DISPLAY_LIMIT = 10
CHINA_TZ = timezone(timedelta(hours=8))
try:
    US_EASTERN_TZ = ZoneInfo("America/New_York")
except Exception:
    US_EASTERN_TZ = timezone(timedelta(hours=-4))

POLL_ORDER = {
    "Hit streak": 0,
    "Over/under": 1,
    "Winner": 2,
    "Any runs": 3,
    "Anytime RBI": 4,
    "Stat": 5,
}

MLB_DISPLAY_POLL_KINDS = {
    "daily_pool",
    "game_total",
    "game_winner",
    "period_total_yes_no",
    "player_over_under",
    "anytime_play",
    "player_most_stat",
}
MLB_POLL_KIND_ORDER = {
    "daily_pool": -1,
    "player_over_under": 0,
    "game_total": 1,
    "game_winner": 2,
    "period_total_yes_no": 3,
}
ZERO_PUT_WIN_WAGER = 10.0
ZERO_COST_ACTION_POLL_KINDS = {"anytime_play", "player_most_stat", "first_basket"}
ZERO_COST_PICK_DISPLAY_KINDS = {"anytime_play", "player_most_stat", "first_basket", "team_stat"}

STATUS_ORDER = {
    "bet": 0,
    "pick": 1,
    "pass": 2,
    "no_edge": 3,
    "no_market": 4,
    "missing_poll_data": 5,
    "unsupported": 6,
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Render a game-by-game Real Sports vote sheet from "
            "real/poll_vote_recommendations.csv."
        )
    )
    parser.add_argument("--input", default=str(DEFAULT_INPUT))
    parser.add_argument("--output", default="")
    parser.add_argument(
        "--lineup-input",
        default="",
        help=(
            "Optional lineup.csv override. If omitted, the renderer first looks for "
            "real/lineups/<sport>.csv and then falls back to real/lineup.csv."
        ),
    )
    parser.add_argument(
        "--predictions-input",
        default="",
        help=(
            "Optional prediction market CSV override. If omitted, the renderer first looks for "
            "real/prediction_market_recommendations_<sport>.csv and then falls back to the generic path."
        ),
    )
    parser.add_argument(
        "--prediction-positions-input",
        default="",
        help=(
            "Optional prediction position CSV override. If omitted, the renderer first looks for "
            "real/prediction_position_recommendations_<sport>.csv and then falls back to the generic path."
        ),
    )
    parser.add_argument(
        "--refresh-predictions",
        action="store_true",
        help="Refresh Real prediction market and open-position EV CSVs for this sport before rendering.",
    )
    parser.add_argument(
        "--prediction-markets-csv",
        default=str(DEFAULT_PREDICTION_MARKETS_CSV),
        help="Sportsbook consensus CSV to use when refreshing predictions.",
    )
    parser.add_argument(
        "--not-started-only",
        action="store_true",
        help="Only render games whose listed start time is still in the future.",
    )
    return parser.parse_args()


def _parse_game_time(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _load_rows(path: str | Path) -> list[dict[str, str]]:
    if csv.field_size_limit() < 10_000_000:
        csv.field_size_limit(10_000_000)
    csv_path = Path(path)
    with csv_path.open("r", encoding="utf8", newline="") as handle:
        return list(csv.DictReader(handle))


def _maybe_load_rows(path: Path | None) -> list[dict[str, str]]:
    if path is None or not path.exists():
        return []
    return _load_rows(path)


def _safe_float(value: str) -> float | None:
    if value in (None, "", "None"):
        return None
    try:
        return float(value)
    except Exception:
        return None


def _safe_int(value: str) -> int | None:
    if value in (None, "", "None"):
        return None
    try:
        return int(float(value))
    except Exception:
        return None


def _format_game_time(value: str) -> str:
    parsed_utc = _parse_game_time(value)
    if parsed_utc is None:
        return str(value or "").strip()
    return parsed_utc.astimezone(CHINA_TZ).strftime("%Y-%m-%d %H:%M UTC+8")


def _is_future_game_row(row: dict[str, str], *, now_utc: datetime | None = None) -> bool:
    cutoff = now_utc or datetime.now(timezone.utc)
    game_time = _parse_game_time(str(row.get("game_time") or ""))
    return game_time is None or game_time > cutoff


def _filter_future_game_rows(rows: list[dict[str, str]], *, now_utc: datetime | None = None) -> list[dict[str, str]]:
    return [row for row in rows if _is_future_game_row(row, now_utc=now_utc)]


def _normalize(value: str) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _format_american(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        odds = int(float(text))
    except Exception:
        return text
    return f"+{odds}" if odds > 0 else str(odds)


def _format_number(value: str, *, digits: int = 2, suffix: str = "") -> str:
    number = _safe_float(value)
    if number is None:
        return ""
    text = f"{number:.{digits}f}".rstrip("0").rstrip(".")
    return f"{text}{suffix}"


def _format_signed_percent(value: str) -> str:
    number = _safe_float(value)
    if number is None:
        return ""
    return f"{number:+.2f}%"


def _format_probability(value: str) -> str:
    number = _safe_float(value)
    if number is None:
        return ""
    return f"{number * 100.0:.1f}%"


def _format_probability_value(value: float | None) -> str:
    if value is None:
        return ""
    return f"{value * 100.0:.1f}%"


def _table_escape(value: object) -> str:
    text = str(value or "").replace("\n", " ").strip()
    return text.replace("|", "\\|")


def _canonical_label(value: str) -> str:
    normalized = _normalize(value).replace(" ", "")
    aliases = {
        "o": "over",
        "u": "under",
        "yes": "yes",
        "no": "no",
        "tie": "draw",
    }
    return aliases.get(normalized, normalized)


def _outcome_tokens(value: str) -> set[str]:
    text = f" {_normalize(value)} "
    text = text.replace(" and ", " or ").replace(" & ", " or ")
    parts = [part.strip() for part in text.split(" or ") if part.strip()]
    if len(parts) <= 1:
        return set()
    tokens: set[str] = set()
    for part in parts:
        canonical = _canonical_label(part)
        if canonical in {"draw", "tie"}:
            tokens.add("draw")
        else:
            tokens.add(normalize_team(part) or canonical)
    return tokens


def _labels_match(candidate: str, selection: str) -> bool:
    candidate_key = _canonical_label(candidate)
    selection_key = _canonical_label(selection)
    if candidate_key == selection_key:
        return True

    candidate_team = normalize_team(candidate)
    selection_team = normalize_team(selection)
    if candidate_team and candidate_team == selection_team:
        return True

    candidate_tokens = _outcome_tokens(candidate)
    selection_tokens = _outcome_tokens(selection)
    return bool(candidate_tokens and candidate_tokens == selection_tokens)


def _matching_labeled_odds(
    row: dict[str, str],
    *,
    label_prefix: str,
    odds_prefix: str,
    selection: str,
) -> tuple[str, str] | None:
    if not _canonical_label(selection):
        return None
    for slot in ("a", "b", "c"):
        label = str(row.get(f"{label_prefix}_{slot}_label") or "").strip()
        odds = str(row.get(f"{odds_prefix}_{slot}_odds") or "").strip()
        if not label:
            continue
        if _labels_match(label, selection):
            return label, odds
    return None


def _option_slots(row: dict[str, str], *, prefix: str = "option") -> list[dict[str, str]]:
    slots = []
    for slot in ("a", "b", "c"):
        label = str(row.get(f"{prefix}_{slot}_label") or "").strip()
        odds = str(row.get(f"{prefix}_{slot}_odds") or "").strip()
        if label:
            slots.append({"slot": slot, "label": label, "odds": odds})
    return slots


def _matching_slot(options: list[dict[str, str]], selection: str) -> str:
    for option in options:
        if _labels_match(option["label"], selection):
            return option["slot"]
    return ""


def _binary_fair_probabilities(row: dict[str, str]) -> dict[str, float]:
    options = _option_slots(row)
    if len(options) != 2:
        return {}
    fair_prob = _safe_float(str(row.get("fair_prob") or ""))
    if fair_prob is None:
        return {}
    recommended_slot = _matching_slot(options, str(row.get("recommended_option") or ""))
    if not recommended_slot:
        return {}

    other_slot = options[0]["slot"] if options[1]["slot"] == recommended_slot else options[1]["slot"]
    return {
        recommended_slot: fair_prob,
        other_slot: 1.0 - fair_prob,
    }


def _sportsbook_fair_probabilities(row: dict[str, str], options: list[dict[str, str]]) -> dict[str, float]:
    raw_probs: dict[str, float] = {}
    for option in options:
        sportsbook_match = _matching_labeled_odds(
            row,
            label_prefix="sportsbook",
            odds_prefix="sportsbook",
            selection=option["label"],
        )
        if not sportsbook_match:
            continue
        _, sportsbook_odds = sportsbook_match
        odds_value = _safe_int(str(sportsbook_odds or ""))
        if odds_value is None:
            continue
        try:
            raw_probs[option["slot"]] = american_to_implied_prob(odds_value)
        except Exception:
            continue
    total = sum(raw_probs.values())
    if total <= 0:
        return {}
    return {slot: value / total for slot, value in raw_probs.items()}


def _action_fair_probabilities(row: dict[str, str], options: list[dict[str, str]]) -> dict[str, float]:
    if len(options) == 2:
        fair_probs = _binary_fair_probabilities(row)
        if fair_probs:
            return fair_probs
    return _sportsbook_fair_probabilities(row, options)


def _action_label(label: str) -> str:
    compact = _compact_label(label)
    return "".join(str(compact or "").split())


def _format_signed_number(value: float, *, suffix: str = "") -> str:
    sign = "+" if value >= 0 else ""
    text = f"{sign}{value:.2f}"
    return f"{text}{suffix}" if suffix else text


def _expected_profit(fair_prob: float, real_odds: int, amount: int) -> float:
    payout = net_payout_per_unit(real_odds)
    if amount <= 0:
        return fair_prob * ZERO_PUT_WIN_WAGER
    return amount * ((fair_prob * payout) - (1.0 - fair_prob))


def _action_ev_text(fair_prob: float, real_odds: int, amount: int) -> str:
    expected_profit = _expected_profit(fair_prob, real_odds, amount)
    if amount <= 0:
        return _format_signed_number(expected_profit, suffix=" Rax")
    ev_percent = (expected_profit / max(float(amount), 1.0)) * 100.0
    return _format_signed_percent(str(round(ev_percent, 4)))


def _action_payload_token(payload: dict[str, object]) -> str:
    encoded = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode("utf8")
    ).decode("ascii").rstrip("=")
    return f"<!--REAL_ACTIONS:{encoded}-->"


def _source_lines_token(text: str) -> str:
    encoded = base64.urlsafe_b64encode(str(text or "").encode("utf8")).decode("ascii").rstrip("=")
    return f"<!--REAL_SOURCE_LINES:{encoded}-->"


def _source_lines_text(row: dict[str, str]) -> str:
    return str(row.get("source_lines") or "").strip()


def _player_choice_probability(choice: dict[str, object]) -> float | None:
    probability = _safe_float(str(choice.get("fair_prob") or ""))
    if probability is not None:
        return probability
    odds = _safe_int(str(choice.get("sportsbook_odds") or ""))
    if odds is None:
        return None
    try:
        return american_to_implied_prob(odds)
    except Exception:
        return None


def _player_choices_from_json(row: dict[str, str]) -> list[dict[str, object]]:
    raw = str(row.get("player_choices_json") or "").strip()
    if raw:
        try:
            payload = json.loads(raw)
        except Exception:
            payload = []
        if isinstance(payload, list):
            choices = [choice for choice in payload if isinstance(choice, dict)]
            if choices:
                return choices

    # Backward-compatible fallback for older CSVs that only exported 3 slots.
    choices: list[dict[str, object]] = []
    for rank, option in enumerate(_option_slots(row, prefix="sportsbook"), start=1):
        name = str(option.get("label") or "").strip()
        odds = _safe_int(str(option.get("odds") or ""))
        if not name or odds is None:
            continue
        probability = None
        try:
            probability = american_to_implied_prob(odds)
        except Exception:
            pass
        choices.append(
            {
                "selection": name,
                "probability_rank": rank,
                "fair_prob": probability,
                "fair_odds": probability_to_american(probability) if probability is not None else "",
                "ranked_payout": "",
                "expected_value": "",
                "sportsbook_odds": odds,
                "books": str(row.get("books") or ""),
            }
        )
    return choices


def _ranked_player_choices(row: dict[str, str]) -> list[dict[str, object]]:
    return sorted(
        _player_choices_from_json(row),
        key=lambda choice: (
            -float(_player_choice_probability(choice) or 0.0),
            _safe_int(str(choice.get("probability_rank") or "")) or 9999,
            str(choice.get("selection") or ""),
        ),
    )


def _selected_player_choice(row: dict[str, str]) -> dict[str, object] | None:
    choices = _ranked_player_choices(row)
    if not choices:
        return None
    recommended = str(row.get("recommended_option") or "").strip()
    for choice in choices:
        if _labels_match(str(choice.get("selection") or ""), recommended):
            return choice
    return max(
        choices,
        key=lambda choice: (
            float(_safe_float(str(choice.get("expected_value") or "")) or 0.0),
            float(_player_choice_probability(choice) or 0.0),
            -int(_safe_int(str(choice.get("probability_rank") or "")) or 9999),
            str(choice.get("selection") or ""),
        ),
    )


def _format_karma_ev(value: object) -> str:
    number = _safe_float(str(value or ""))
    if number is None:
        return "n/a"
    return _format_signed_number(number, suffix=" karma")


def _choice_books_label(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return (
        text.replace("draftkings", "DK")
        .replace("fanduel", "FD")
        .replace(" | ", "+")
    )


def _player_choice_sportsbook_text(choice: dict[str, object], fallback: str) -> str:
    name = _compact_label(str(choice.get("selection") or ""))
    odds = _format_american(str(choice.get("sportsbook_odds") or ""))
    if not name and not odds:
        return fallback
    text = " ".join(part for part in (name, odds) if part)
    books = _choice_books_label(choice.get("books"))
    return f"{books}: {text}" if books and text else text or fallback


def _action_choice_payload(row: dict[str, str]) -> dict[str, object] | None:
    status = str(row.get("status") or "").strip().lower()
    if status in {"no_market", "missing_poll_data", "unsupported", "pass"}:
        return None
    max_wager = _safe_int(str(row.get("max_wager") or ""))
    if max_wager is None or max_wager <= 0:
        return None

    options = _option_slots(row)
    if len(options) < 2:
        return None
    fair_probs = _action_fair_probabilities(row, options)
    if not fair_probs:
        return None

    actions: list[dict[str, object]] = []
    source_lines = _source_lines_text(row)
    for option in options:
        fair_prob = fair_probs.get(option["slot"])
        real_odds = _safe_int(str(option.get("odds") or ""))
        if fair_prob is None or real_odds is None:
            continue
        sportsbook_text = _compact_sportsbook_pair(row)
        for amount in (0, max_wager):
            selection_put = f"{_action_label(option['label'])}{amount}"
            actions.append(
                {
                    "selection_put": selection_put,
                    "selection": option["label"],
                    "amount": amount,
                    "consensus": (
                        f"{_format_probability_value(fair_prob)} "
                        f"({_format_american(str(probability_to_american(fair_prob)))})"
                    ),
                    "ev": _action_ev_text(fair_prob, real_odds, amount),
                    "sportsbook": sportsbook_text,
                    "source": _source_text(row),
                    "source_lines": source_lines,
                }
            )

    if len(actions) < 2:
        return None
    default_text = _selection_put_text(row, include_action_token=False)
    return {"default": default_text, "actions": actions}


def _player_selection_choice_payload(row: dict[str, str]) -> dict[str, object] | None:
    status = str(row.get("status") or "").strip().lower()
    poll_kind = str(row.get("poll_kind") or "").strip().lower()
    if status != "pick" or poll_kind not in ZERO_COST_ACTION_POLL_KINDS:
        return None

    choices = _ranked_player_choices(row)
    if len(choices) < 2:
        return None

    sportsbook_text = _compact_sportsbook_pair(row)
    source_lines = _source_lines_text(row)
    actions: list[dict[str, object]] = []
    for fallback_rank, choice in enumerate(choices, start=1):
        fair_prob = _player_choice_probability(choice)
        if fair_prob is None:
            continue
        rank = _safe_int(str(choice.get("probability_rank") or "")) or fallback_rank
        probability = _format_probability_value(fair_prob)
        fair_odds = _format_american(
            str(choice.get("fair_odds") or probability_to_american(fair_prob))
        )
        name = str(choice.get("selection") or "").strip()
        choice_source_lines = str(choice.get("source_lines") or source_lines or "").strip()
        choice_source_note = str(choice.get("source_note") or "").strip()
        choice_source = (
            _source_text({**row, "notes": choice_source_note})
            if choice_source_note
            else _source_text(row)
        )
        actions.append(
            {
                "selection_put": name,
                "display_label": f"{_compact_label(name)} | #{rank} | {probability}",
                "selection": name,
                "amount": 0,
                "consensus": f"{probability} ({fair_odds})",
                "ev": _format_karma_ev(choice.get("expected_value")),
                "sportsbook": _player_choice_sportsbook_text(choice, sportsbook_text),
                "source": choice_source,
                "source_lines": choice_source_lines,
                "expected_value": _safe_float(str(choice.get("expected_value") or "")) or 0.0,
            }
        )

    if len(actions) < 2:
        return None

    recommended = str(row.get("recommended_option") or "").strip()
    default = next(
        (
            str(action.get("selection_put") or "")
            for action in actions
            if _labels_match(str(action.get("selection") or ""), recommended)
        ),
        str(
            max(
                actions,
                key=lambda action: (
                    float(action.get("expected_value") or 0.0),
                    str(action.get("selection") or ""),
                ),
            ).get("selection_put")
            or ""
        ),
    )
    return {"default": default, "actions": actions}


def _best_action(row: dict[str, str]) -> dict[str, object]:
    poll_kind = str(row.get("poll_kind") or "").strip().lower()
    options = _option_slots(row)
    fair_probs = _binary_fair_probabilities(row)
    max_wager = _safe_int(str(row.get("max_wager") or "")) or 0
    candidates: list[dict[str, object]] = []

    if poll_kind in ZERO_COST_ACTION_POLL_KINDS:
        for option in options:
            fair_prob = fair_probs.get(option["slot"])
            real_odds = _safe_int(option["odds"])
            if fair_prob is None or real_odds is None:
                continue
            for amount in (0, max_wager):
                candidates.append(
                    {
                        "label": option["label"],
                        "amount": amount,
                        "fair_prob": fair_prob,
                        "fair_odds": probability_to_american(fair_prob),
                        "expected_profit": _expected_profit(fair_prob, real_odds, amount),
                    }
                )

    if candidates:
        return max(
            candidates,
            key=lambda item: (
                float(item["expected_profit"]),
                int(item["amount"]),
                str(item["label"]),
            ),
        )

    amount = _safe_int(str(row.get("recommended_amount") or "")) or 0
    fair_prob = _safe_float(str(row.get("fair_prob") or ""))
    fair_odds = _safe_int(str(row.get("fair_odds") or ""))
    return {
        "label": str(row.get("recommended_option") or "").strip(),
        "amount": amount,
        "fair_prob": fair_prob,
        "fair_odds": fair_odds,
        "expected_profit": None,
    }


def _selection_put_text(row: dict[str, str], *, include_action_token: bool = True) -> str:
    action = _best_action(row)
    label = str(action.get("label") or "").strip()
    status = str(row.get("status") or "").strip().lower()
    poll_kind = str(row.get("poll_kind") or "").strip().lower()
    if not label:
        return "NoMarket" if status == "no_market" else "Skip"
    if poll_kind == "daily_pool":
        amount = int(action.get("amount") or 0)
        return f"{label} | {amount}" if amount > 0 else label
    if status == "pick" and poll_kind in ZERO_COST_PICK_DISPLAY_KINDS:
        text = _compact_label(label)
        if include_action_token:
            payload = _player_selection_choice_payload(row)
            if payload:
                return f"{text} {_action_payload_token(payload)}"
        return text
    text = f"{_action_label(label)}{int(action.get('amount') or 0)}"
    if include_action_token:
        payload = _action_choice_payload(row)
        if payload:
            return f"{text} {_action_payload_token(payload)}"
    return text


def _compact_consensus_text(row: dict[str, str]) -> str:
    action = _best_action(row)
    fair_prob = action.get("fair_prob")
    fair_odds = action.get("fair_odds")
    if fair_prob is None or fair_odds in (None, ""):
        return ""
    return f"{_format_probability_value(float(fair_prob))} ({_format_american(str(fair_odds))})"


def _selected_real_odds(row: dict[str, str]) -> str:
    option = str(row.get("recommended_option") or "").strip()
    match = _matching_labeled_odds(
        row,
        label_prefix="option",
        odds_prefix="option",
        selection=option,
    )
    if match:
        label, odds = match
        return f"{_compact_label(label)} {_format_american(odds)}".strip()
    return _real_pair(row)


def _selected_sportsbook_odds(row: dict[str, str]) -> str:
    option = str(row.get("recommended_option") or "").strip()
    match = _matching_labeled_odds(
        row,
        label_prefix="sportsbook",
        odds_prefix="sportsbook",
        selection=option,
    )
    if match:
        label, odds = match
        return f"{_compact_label(label)} {_format_american(odds)}".strip()
    return _sportsbook_pair(row)


def _compact_label(label: str) -> str:
    normalized = _normalize(label)
    if normalized == "over":
        return "Over"
    if normalized == "under":
        return "Under"
    if normalized == "yes":
        return "Yes"
    if normalized == "no":
        return "No"
    return str(label or "").strip()


def _labeled_pair(
    left_label: str,
    left_odds: str,
    right_label: str,
    right_odds: str,
) -> str:
    left = _format_american(left_odds) or "+100"
    right = _format_american(right_odds) or "+100"
    left_name = _compact_label(left_label)
    right_name = _compact_label(right_label)
    if not left_name and not right_name:
        return ""
    return f"{left_name}:{left} {right_name}:{right}".strip()


def _labeled_options(*items: tuple[str, str]) -> str:
    parts: list[str] = []
    for label, odds in items:
        name = _compact_label(label)
        if not name:
            continue
        formatted = _format_american(odds) or "+100"
        parts.append(f"{name}:{formatted}")
    return " ".join(parts)


def _real_pair(row: dict[str, str]) -> str:
    return _labeled_options(
        (
            str(row.get("option_a_label") or ""),
            str(row.get("option_a_odds") or ""),
        ),
        (
            str(row.get("option_b_label") or ""),
            str(row.get("option_b_odds") or ""),
        ),
        (
            str(row.get("option_c_label") or ""),
            str(row.get("option_c_odds") or ""),
        ),
    ) or _labeled_pair(
        str(row.get("option_a_label") or ""),
        str(row.get("option_a_odds") or ""),
        str(row.get("option_b_label") or ""),
        str(row.get("option_b_odds") or ""),
    )


def _sportsbook_pair(row: dict[str, str]) -> str:
    return _labeled_options(
        (
            str(row.get("sportsbook_a_label") or ""),
            str(row.get("sportsbook_a_odds") or ""),
        ),
        (
            str(row.get("sportsbook_b_label") or ""),
            str(row.get("sportsbook_b_odds") or ""),
        ),
        (
            str(row.get("sportsbook_c_label") or ""),
            str(row.get("sportsbook_c_odds") or ""),
        ),
    ) or _labeled_pair(
        str(row.get("sportsbook_a_label") or ""),
        str(row.get("sportsbook_a_odds") or ""),
        str(row.get("sportsbook_b_label") or ""),
        str(row.get("sportsbook_b_odds") or ""),
    )


def _compact_sportsbook_pair(row: dict[str, str]) -> str:
    if str(row.get("poll_kind") or "").strip().lower() == "daily_pool":
        return str(row.get("sportsbook_a_label") or "").strip()
    parts = []
    for option in _option_slots(row, prefix="sportsbook"):
        odds = _format_american(option["odds"])
        if not odds:
            continue
        parts.append(f"{_compact_label(option['label'])} {odds}")
    if parts:
        return " / ".join(parts)
    notes = str(row.get("notes") or "").strip()
    lowered_notes = notes.lower()
    status = str(row.get("status") or "").strip().lower()
    if "real split fallback" in lowered_notes:
        return "Fallback: Real split"
    if "sportsbook" in lowered_notes and "proxy" in lowered_notes:
        return "Proxy: sportsbook"
    if "official" in lowered_notes and "proxy" in lowered_notes:
        return "Proxy: official stats"
    if "proxy lookup failed" in lowered_notes:
        return "Proxy unavailable"
    if status == "no_market":
        return "No sportsbook match"
    return ""


def _source_text(row: dict[str, str]) -> str:
    notes = str(row.get("notes") or "").strip()
    lowered = notes.lower()
    if "daily pool payout model" in lowered:
        return "Pool payout model"
    if "real split fallback" in lowered:
        return "Real split fallback"
    if "normalized first-basket implied probabilities" in lowered:
        return "First-basket book probs"
    if "1q spread proxy for period winner" in lowered:
        return "1Q spread proxy"
    if "sportsbook moneyline proxy" in lowered:
        return "Sportsbook moneyline proxy"
    if "official playoff" in lowered or "official season-to-date" in lowered:
        return "Official stats proxy"
    if "integer-goal equivalent exact line" in lowered:
        return "Integer-equivalent exact line"
    if "single-book exact line" in lowered:
        return "Single-book exact line"
    if "exact-line consensus" in lowered:
        return "Exact-line consensus"
    if "nearest-line fallback" in lowered:
        return "Nearest-line fallback"
    if "fitted line curve" in lowered:
        return "Fitted line curve"
    if "single-book moneyline" in lowered:
        return "Single-book moneyline"
    if "weighted no-vig moneyline consensus" in lowered:
        return "Moneyline consensus"
    if "weighted daily-stats payout ladder" in lowered:
        return "Daily-stats ranking"
    if "weighted zero-cost payout ladder" in lowered:
        return "Zero-cost ranking"
    if "proxy unavailable" in lowered:
        return "Proxy unavailable"
    if "no sportsbook match" in lowered or "no matching sportsbook" in lowered:
        return "No sportsbook match"
    if not notes:
        return ""
    return notes.split(";", 1)[0].strip()


def _source_text_with_lines(row: dict[str, str]) -> str:
    source = _source_text(row)
    source_lines = _source_lines_text(row)
    if source and source_lines:
        return f"{source} {_source_lines_token(source_lines)}"
    return source


def _market_odds_text(row: dict[str, str]) -> str:
    sportsbook_odds = _sportsbook_pair(row)
    if not sportsbook_odds:
        return ""
    books = str(row.get("books") or "").strip().replace(" | ", "+")
    books_text = f" Books={books}" if books else ""
    return f"{books_text} Market={sportsbook_odds}"


def _fair_line_text(row: dict[str, str]) -> str:
    fair_line = _safe_float(str(row.get("consensus_fair_line") or ""))
    if fair_line is None:
        return ""
    return f" FairLine={fair_line:g}"


def _fair_value_text(row: dict[str, str]) -> str:
    probability = _format_probability(str(row.get("fair_prob") or ""))
    odds = _format_american(str(row.get("fair_odds") or ""))
    fair_line = _format_number(str(row.get("consensus_fair_line") or ""), digits=3)
    parts = []
    if probability or odds:
        parts.append(" / ".join(part for part in (probability, odds) if part))
    if fair_line:
        parts.append(f"fair line {fair_line}")
    return "; ".join(parts)


def _books_text(row: dict[str, str]) -> str:
    books = str(row.get("books") or "").strip()
    books = books.replace("draftkings", "DK").replace("fanduel", "FD")
    books = books.replace(" | ", "+")
    matched = str(row.get("matched_books") or "").strip()
    if books and matched not in {"", "0"}:
        return f"{books} ({matched})"
    return books


def _poll_title(row: dict[str, str]) -> str:
    header = str(row.get("header") or "").strip()
    option = str(row.get("recommended_option") or "").strip()
    line = str(row.get("line") or "").strip()
    poll_kind = str(row.get("poll_kind") or "").strip().lower()

    if poll_kind == "daily_pool":
        return "Pool of the day"
    if header == "Winner":
        return option or "Winner"
    if poll_kind == "first_basket":
        return option or header or "First FG"
    if poll_kind == "team_stat":
        return option or header or "Team stat"
    if header == "Over/under":
        if _normalize(option) == "over":
            return f"O {line}".strip()
        if _normalize(option) == "under":
            return f"U {line}".strip()
        return "O/U"
    if header == "Any runs":
        if option:
            return f"{option.upper()} 1st runs"
        return "1st runs"
    if header in {"Double chance", "Half time", "Scoring"}:
        return option or header
    if header == "Hit streak":
        if option:
            return f"Hit Streak {option.upper()}"
        return "Hit Streak"
    if header == "Anytime RBI":
        return option or "Anytime RBI"
    if header == "Daily stats":
        return str(row.get("content_text") or "").strip() or header
    if header == "Stat":
        return option or header
    return header or "Poll"


def _poll_table_label(row: dict[str, str]) -> str:
    header = str(row.get("header") or "").strip()
    content = str(row.get("content_text") or "").strip()
    player = str(row.get("player_name") or "").strip()
    poll_kind = str(row.get("poll_kind") or "").strip().lower()
    if poll_kind == "daily_pool":
        return "Pool of the day"
    if str(row.get("poll_kind") or "").strip() == "period_total_yes_no":
        return "Score in 1st inning"
    if header == "Winner":
        return "Winner"
    if poll_kind == "first_basket":
        return content or header or "First FG"
    if poll_kind == "team_stat":
        return content or header or "Team stat"
    if header == "Anytime RBI":
        return "Anytime RBI"
    if header == "Daily stats":
        return content or "Daily stats"
    if header == "Hit streak":
        return f"Hit streak: {player}" if player else "Hit streak"
    if header == "Stat":
        return content or "Stat"
    if poll_kind == "player_most_stat" and content:
        return content
    if player and content:
        return f"{header}: {player} - {content}" if header else f"{player} - {content}"
    if content:
        return f"{header}: {content}" if header else content
    return _skip_title(row)


def _skip_title(row: dict[str, str]) -> str:
    header = str(row.get("header") or "").strip()
    if str(row.get("poll_kind") or "").strip().lower() == "daily_pool":
        return "Pool of the day"
    if header == "Any runs":
        return "1st runs"
    if header == "Over/under":
        line = str(row.get("line") or "").strip()
        return f"O/U {line}".strip()
    return header or "Poll"


def _selection_text(row: dict[str, str]) -> str:
    status = str(row.get("status") or "").strip().lower()
    option = str(row.get("recommended_option") or "").strip()
    if status == "bet":
        return option or "Bet"
    if status == "pick":
        return option or "Pick"
    if status == "pass":
        return f"Pass (best: {option})" if option else "Pass"
    if status == "no_edge":
        return f"Skip (best: {option})" if option else "Skip"
    return "Skip"


def _put_text(row: dict[str, str]) -> str:
    status = str(row.get("status") or "").strip().lower()
    amount = _safe_int(str(row.get("recommended_amount") or ""))
    max_wager = _safe_int(str(row.get("max_wager") or ""))
    if status == "bet" and amount is not None and max_wager is not None:
        return f"{amount} / {max_wager}"
    if status == "pick":
        return "0"
    if max_wager is not None:
        return f"0 / {max_wager}"
    return "0"


def _reason_text(row: dict[str, str]) -> str:
    status = str(row.get("status") or "").strip().lower()
    notes = str(row.get("notes") or "").strip()
    if status == "bet":
        return notes
    if status == "pick":
        return notes or "recommended player pick"
    if status == "pass":
        return notes or "not a top-three lineup game"
    if status == "no_edge":
        return notes or "best side is not +EV"
    if status == "no_market":
        return notes or "no matching market"
    if status == "missing_poll_data":
        return notes or "missing Real poll odds"
    if status == "unsupported":
        return notes or "unsupported poll type"
    return notes


def _table_row(row: dict[str, str]) -> str:
    status = str(row.get("status") or "").strip().lower()
    status_label = {
        "bet": "Bet",
        "pick": "Pick",
        "pass": "Pass",
        "no_edge": "Skip",
        "no_market": "No market",
        "missing_poll_data": "Missing data",
        "unsupported": "Unsupported",
    }.get(status, status or "Skip")
    cells = [
        status_label,
        _poll_table_label(row),
        _selection_text(row),
        _put_text(row),
        _selected_real_odds(row),
        _fair_value_text(row),
        _selected_sportsbook_odds(row),
        _format_signed_percent(str(row.get("recommended_ev_percent") or "")),
        _books_text(row),
        _reason_text(row),
    ]
    return "| " + " | ".join(_table_escape(cell) for cell in cells) + " |"


def _compact_ev_text(row: dict[str, str]) -> str:
    value = _format_signed_percent(str(row.get("recommended_ev_percent") or ""))
    if value:
        return value
    status = str(row.get("status") or "").strip().lower()
    poll_kind = str(row.get("poll_kind") or "").strip().lower()
    if status == "pick" and poll_kind in ZERO_COST_ACTION_POLL_KINDS:
        choice = _selected_player_choice(row)
        if choice is not None:
            return _format_karma_ev(choice.get("expected_value"))
    if status == "pick":
        return "n/a"
    return ""


def _lineup_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    return [
        row
        for row in rows
        if str(row.get("poll_kind") or "").strip().lower() == "contest"
    ]


def _non_lineup_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    return [
        row
        for row in rows
        if str(row.get("poll_kind") or "").strip().lower() != "contest"
    ]


def _lineup_action(row: dict[str, str]) -> str:
    status = str(row.get("status") or "").strip().lower()
    if status == "pick":
        rank = _safe_int(str(row.get("lineup_rank") or ""))
        return f"Play #{rank}" if rank is not None else "Play"
    if status == "pass":
        rank = _safe_int(str(row.get("lineup_rank") or ""))
        return f"Pass #{rank}" if rank is not None else "Pass"
    if status == "no_market":
        return "No data"
    if status == "missing_poll_data":
        return "Error"
    return "Skip"


def _lineup_rank_sort(row: dict[str, str]) -> tuple[int, int]:
    rank = _safe_int(str(row.get("lineup_rank") or ""))
    status = str(row.get("status") or "").strip().lower()
    priority = {
        "pick": 0,
        "pass": 1,
        "no_market": 2,
        "missing_poll_data": 3,
    }.get(status, 4)
    return priority, rank if rank is not None else 999


def _lineup_file_candidates(sport: str, lineup_input: str = "") -> list[Path]:
    if lineup_input:
        return [Path(lineup_input)]
    sport_key = str(sport or "").strip().lower()
    candidates = []
    if sport_key:
        candidates.append(LINEUPS_DIR / f"{sport_key}.csv")
    candidates.append(DEFAULT_LINEUP_INPUT)
    return candidates


def _prediction_market_file_candidates(sport: str, predictions_input: str = "") -> list[Path]:
    if predictions_input:
        return [Path(predictions_input)]
    sport_key = str(sport or "").strip().lower()
    candidates = []
    if sport_key:
        candidates.append(BASE_DIR / f"prediction_market_recommendations_{sport_key}.csv")
    candidates.append(DEFAULT_PREDICTION_INPUT)
    return candidates


def _prediction_position_file_candidates(sport: str, prediction_positions_input: str = "") -> list[Path]:
    if prediction_positions_input:
        return [Path(prediction_positions_input)]
    sport_key = str(sport or "").strip().lower()
    candidates = []
    if sport_key:
        candidates.append(BASE_DIR / f"prediction_position_recommendations_{sport_key}.csv")
    candidates.append(DEFAULT_PREDICTION_POSITIONS_INPUT)
    return candidates


def _first_existing_path(candidates: list[Path]) -> Path | None:
    seen: set[Path] = set()
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except Exception:
            resolved = candidate
        if resolved in seen:
            continue
        seen.add(resolved)
        if candidate.exists():
            return candidate
    return None


def _prediction_rows_for_sport(rows: list[dict[str, str]], sport: str) -> list[dict[str, str]]:
    sport_key = str(sport or "").strip().lower()
    return [
        row
        for row in rows
        if str(row.get("sport") or "").strip().lower() == sport_key
    ]


def _load_prediction_rows(
    *,
    sport: str,
    predictions_input: str = "",
    prediction_positions_input: str = "",
    not_started_only: bool = False,
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    market_path = _first_existing_path(_prediction_market_file_candidates(sport, predictions_input))
    position_path = _first_existing_path(_prediction_position_file_candidates(sport, prediction_positions_input))
    market_rows = _prediction_rows_for_sport(_maybe_load_rows(market_path), sport)
    position_rows = _prediction_rows_for_sport(_maybe_load_rows(position_path), sport)
    if not_started_only:
        market_rows = _filter_future_game_rows(market_rows)
        position_rows = _filter_future_game_rows(position_rows)
    return market_rows, position_rows


def _refresh_prediction_recommendations(
    *,
    sport: str,
    markets_csv: str,
    predictions_input: str = "",
    prediction_positions_input: str = "",
) -> None:
    sport_key = str(sport or "").strip().lower()
    if not sport_key:
        return
    market_output = (
        Path(predictions_input)
        if predictions_input
        else BASE_DIR / f"prediction_market_recommendations_{sport_key}.csv"
    )
    position_output = (
        Path(prediction_positions_input)
        if prediction_positions_input
        else BASE_DIR / f"prediction_position_recommendations_{sport_key}.csv"
    )
    print(f"Refreshing {sport_key.upper()} prediction markets...", flush=True)
    subprocess.run(
        [
            sys.executable,
            "-B",
            str(BASE_DIR / "recommend_prediction_markets.py"),
            "--sport",
            sport_key,
            "--markets-csv",
            str(markets_csv),
            "--output",
            str(market_output),
        ],
        check=True,
    )
    print(f"Refreshing {sport_key.upper()} prediction open positions...", flush=True)
    subprocess.run(
        [
            sys.executable,
            "-B",
            str(BASE_DIR / "recommend_prediction_positions.py"),
            "--sport",
            sport_key,
            "--markets-csv",
            str(markets_csv),
            "--output",
            str(position_output),
        ],
        check=True,
    )
    print(f"Finished {sport_key.upper()} prediction refresh.", flush=True)


def _load_lineup_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    if csv.field_size_limit() < 10_000_000:
        csv.field_size_limit(10_000_000)
    with path.open("r", encoding="utf8", newline="") as handle:
        return list(csv.DictReader(handle))


def _lineup_slate_day(row: dict[str, str]) -> str:
    direct = str(row.get("Slate_Date") or "").strip()
    if direct:
        return direct
    start_time = str(row.get("Source_Slate_Start_Date") or "").strip()
    if len(start_time) >= 10:
        return start_time[:10]
    return ""


def _load_daily_lineup_rows(
    *,
    sport: str,
    day: str,
    lineup_input: str = "",
) -> list[dict[str, str]]:
    sport_key = str(sport or "").strip().lower()
    target_day = str(day or "").strip()
    for path in _lineup_file_candidates(sport_key, lineup_input):
        rows = _load_lineup_csv_rows(path)
        if not rows:
            continue
        allow_untyped_rows = bool(lineup_input) or path == (LINEUPS_DIR / f"{sport_key}.csv")
        matching = [
            row
            for row in rows
            if (
                (
                    str(row.get("Sport") or "").strip().lower() == sport_key
                    or (
                        allow_untyped_rows
                        and not str(row.get("Sport") or "").strip()
                    )
                )
                and (not target_day or _lineup_slate_day(row) == target_day)
            )
        ]
        if matching:
            return matching
    return []


def _daily_lineup_rank_sort(row: dict[str, str]) -> tuple[int, str]:
    rank = _safe_int(str(row.get("Lineup_Rank") or ""))
    return rank if rank is not None else 999999, str(row.get("Name") or "")


def _daily_lineup_game_text(row: dict[str, str]) -> str:
    game_key = str(row.get("Game_Key") or "").strip()
    matchup = ""
    if "|" in game_key:
        _game_time, matchup = game_key.split("|", 1)
        matchup = matchup.strip()
    if matchup and "@" in matchup and " @ " not in matchup:
        matchup = matchup.replace("@", " @ ")
    if not matchup:
        team = str(row.get("Team") or "").strip()
        opponent = str(row.get("Opponent") or "").strip()
        is_home = str(row.get("Is_Home") or "").strip().upper() == "Y"
        if team and opponent:
            matchup = f"{opponent} @ {team}" if is_home else f"{team} @ {opponent}"
    return matchup or game_key


def _lineup_game_pair(value: str) -> tuple[str, str] | None:
    text = str(value or "").strip()
    if not text:
        return None
    if "|" in text:
        _time_text, text = text.split("|", 1)
    matchup = text.strip()
    if not matchup or "@" not in matchup:
        return None
    away_team, home_team = matchup.split("@", 1)
    away_key = normalize_team(away_team)
    home_key = normalize_team(home_team)
    if not away_key or not home_key:
        return None
    ordered = sorted((away_key, home_key))
    return ordered[0], ordered[1]


def _row_game_pair(row: dict[str, str]) -> tuple[str, str] | None:
    home_key = normalize_team(str(row.get("home_team") or ""))
    away_key = normalize_team(str(row.get("away_team") or ""))
    if not home_key or not away_key:
        return None
    ordered = sorted((home_key, away_key))
    return ordered[0], ordered[1]


def _parse_lineup_game_time(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if "|" in text:
        text, _matchup = text.split("|", 1)
        text = text.strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        parsed = None
    if parsed is None:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
            try:
                parsed = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=US_EASTERN_TZ)
    return parsed.astimezone(timezone.utc)


def _future_game_pairs(
    rows: list[dict[str, str]],
    *,
    now_utc: datetime | None = None,
) -> set[tuple[str, str]]:
    cutoff = now_utc or datetime.now(timezone.utc)
    pairs: set[tuple[str, str]] = set()
    for row in rows:
        game_time = _parse_game_time(str(row.get("game_time") or ""))
        if game_time is not None and game_time <= cutoff:
            continue
        game_pair = _row_game_pair(row)
        if game_pair is not None:
            pairs.add(game_pair)
    return pairs


def _render_daily_lineup_section(
    *,
    sport: str,
    day: str,
    lineup_input: str = "",
    game_rows: list[dict[str, str]] | None = None,
    now_utc: datetime | None = None,
) -> list[str]:
    lineup_rows = _load_daily_lineup_rows(sport=sport, day=day, lineup_input=lineup_input)
    if not lineup_rows:
        return []
    cutoff = now_utc or datetime.now(timezone.utc)
    future_pairs = _future_game_pairs(game_rows or [], now_utc=cutoff)
    filtered_rows: list[dict[str, str]] = []
    for row in lineup_rows:
        lineup_pair = _lineup_game_pair(str(row.get("Game_Key") or ""))
        lineup_time = _parse_lineup_game_time(str(row.get("Game_Key") or ""))
        if lineup_pair is not None and lineup_pair in future_pairs:
            filtered_rows.append(row)
            continue
        if lineup_time is not None and lineup_time > cutoff:
            filtered_rows.append(row)
    if not filtered_rows:
        return []
    ordered_rows = sorted(filtered_rows, key=_daily_lineup_rank_sort)[:DAILY_LINEUP_DISPLAY_LIMIT]
    sections = [
        "## Daily Lineup",
        "",
        (
            f"Top {len(ordered_rows)} `lineup.py` player ranks from games that have not started yet "
            f"for this {sport.upper()} slate, after the Real multiplier adjustment."
        ),
        "",
        "| Rank | Player | Team | Pos | Game | Adj FP | Adj Real Rating | Mult | Salary |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in ordered_rows:
        cells = [
            str(row.get("Lineup_Rank") or ""),
            str(row.get("Name") or "").strip(),
            str(row.get("Team") or "").strip(),
            str(row.get("Position") or "").strip(),
            _daily_lineup_game_text(row),
            _format_number(str(row.get("Adjusted_FP") or ""), digits=2),
            _format_number(str(row.get("Adjusted_Rating") or ""), digits=2),
            _format_number(str(row.get("Multiplier_Factor") or ""), digits=2),
            str(row.get("Salary") or "").strip(),
        ]
        sections.append("| " + " | ".join(_table_escape(cell) for cell in cells) + " |")
    sections.append("")
    return sections


def _lineup_game_label(row: dict[str, str]) -> str:
    custom_label = str(row.get("game_label") or "").strip()
    if custom_label:
        matchup = custom_label
    else:
        away = str(row.get("away_team") or "").strip()
        home = str(row.get("home_team") or "").strip()
        matchup = f"{away} @ {home}".strip()
        if matchup == "@":
            matchup = ""
        if not matchup:
            matchup = str(row.get("content_text") or row.get("header") or row.get("game_id") or "").strip()
    game_time = _format_game_time(str(row.get("game_time") or ""))
    return f"{matchup} ({game_time})" if game_time else matchup


def _lineup_top_five_text(row: dict[str, str]) -> str:
    return str(row.get("lineup_players") or row.get("recommended_option") or "").strip()


def _lineup_metric_text(row: dict[str, str], field: str) -> str:
    return _format_number(str(row.get(field) or ""), digits=2)


def _render_lineup_section(rows: list[dict[str, str]]) -> list[str]:
    lineup_rows = [
        row
        for row in _lineup_rows(rows)
        if str(row.get("status") or "").strip().lower() == "pick"
    ]
    if not lineup_rows:
        return []
    ordered_rows = sorted(lineup_rows, key=_lineup_rank_sort)
    sections = [
        "## Lineup Contest Picks",
        "",
        "Only the top 3 lineup plays are shown below. The `Top 5` order is the recommended player rank order from Rotowire fantasy projections.",
        "",
        "| Rank | Game | Action | Top 5 | Gap 5v6 | Min Rank Gap | Top-5 Total |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in ordered_rows:
        cells = [
            str(row.get("lineup_rank") or ""),
            _lineup_game_label(row),
            _lineup_action(row),
            _lineup_top_five_text(row),
            _lineup_metric_text(row, "lineup_cutoff_gap"),
            _lineup_metric_text(row, "lineup_min_rank_gap"),
            _lineup_metric_text(row, "lineup_top5_total"),
        ]
        sections.append("| " + " | ".join(_table_escape(cell) for cell in cells) + " |")
    sections.append("")
    return sections


def _render_predictions_section(
    *,
    sport: str,
    predictions_input: str = "",
    prediction_positions_input: str = "",
    not_started_only: bool = False,
) -> list[str]:
    market_rows, position_rows = _load_prediction_rows(
        sport=sport,
        predictions_input=predictions_input,
        prediction_positions_input=prediction_positions_input,
        not_started_only=not_started_only,
    )
    if not market_rows and not position_rows:
        return []
    sections = [
        "## Predictions",
        "",
        prediction_summary_line(market_rows, position_rows),
        "",
        "`Selection+Rax` is the side plus the recommended Real prediction buy size. Open positions show the current hold-vs-cashout view.",
        "",
    ]
    sections.extend(render_prediction_sections(market_rows, position_rows, heading_level=3))
    return sections


def _poll_sort_key(row: dict[str, str]) -> tuple[int, int, int, int, str]:
    game_order = _safe_int(str(row.get("game_order") or ""))
    post_order = _safe_int(str(row.get("post_order") or ""))
    header = str(row.get("header") or "").strip()
    poll_kind = str(row.get("poll_kind") or "").strip()
    return (
        game_order if game_order is not None else 999999,
        post_order if post_order is not None else 999999,
        MLB_POLL_KIND_ORDER.get(poll_kind, 99),
        POLL_ORDER.get(header, 99),
        header,
    )


def _section_game_label(row: dict[str, str]) -> str:
    custom_label = str(row.get("game_label") or "").strip()
    if custom_label:
        return custom_label
    away = str(row.get("away_team") or "").strip()
    home = str(row.get("home_team") or "").strip()
    matchup = f"{away} @ {home}".strip()
    if matchup and matchup != "@":
        return matchup
    return str(row.get("content_text") or row.get("header") or row.get("game_id") or "").strip()


def _compact_table_row(row: dict[str, str]) -> str:
    cells = [
        _poll_table_label(row),
        _selection_put_text(row),
        _compact_consensus_text(row),
        _compact_ev_text(row),
        _compact_sportsbook_pair(row),
        _source_text_with_lines(row),
    ]
    return "| " + " | ".join(_table_escape(cell) for cell in cells) + " |"


def _display_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    if not rows:
        return rows
    rows = _non_lineup_rows(rows)
    if not rows:
        return rows
    sport = str(rows[0].get("sport") or "").strip().lower()
    if sport == "mlb":
        return [
            row
            for row in rows
            if (
                str(row.get("poll_kind") or "").strip() in MLB_DISPLAY_POLL_KINDS
                and (
                    str(row.get("poll_kind") or "").strip() != "player_over_under"
                    or str(row.get("header") or "").strip() == "Hit streak"
                )
            )
        ]
    return rows


def _summary_line(rows: list[dict[str, str]]) -> str:
    actions = [_best_action(row) for row in rows]
    total_put = sum(int(action.get("amount") or 0) for action in actions)
    return f"**Summary:** {len(rows)} displayed polls, total put {total_put}."


def _consensus_explainer() -> list[str]:
    return [
        "## How Consensus Is Calculated",
        "",
        "- Sportsbook odds are first converted from American odds to implied probabilities.",
        "- For two-way markets, the vig is removed per book by normalizing both sides so they sum to 100%.",
        "- DraftKings and FanDuel both default to weight `1.00`; if timestamps are available, older quotes decay with a 20-minute half-life.",
        "- If a usable alternate-line ladder exists for game totals, the model fits a weighted logit curve across nearby lines.",
        "- Otherwise, if the exact Real line exists at multiple books, the sheet uses weighted no-vig consensus at that line; if that cannot fit, it falls back to the nearest line with a conservative line adjustment.",
        "- Regular wager polls now choose between `0` and max put in the backend. A max bet is only used when its EV beats the fixed `0`-put -> win `10` alternative.",
        "- Zero-cost player-pick dropdowns list every exported player from highest fair win probability to lowest; the default selection is still the highest expected-karma option.",
        "- Player-pick EV is `fair win probability * Real ranked karma payout`; Daily stats pay `10, 20, ... 200` through rank 20, then add `+1` per rank after that.",
        "- For max put wager polls, Real EV is `fair win probability * net payout - loss probability`, multiplied by max put.",
        "- For `0`-put wager polls, the comparison baseline is `fair win probability * 10`.",
        "",
    ]


def _status_text(row: dict[str, str]) -> str:
    status = str(row.get("status") or "").strip().lower()
    option = str(row.get("recommended_option") or "").strip()
    amount = _safe_int(str(row.get("recommended_amount") or ""))
    max_wager = _safe_int(str(row.get("max_wager") or ""))
    ev_percent = _safe_float(str(row.get("recommended_ev_percent") or ""))
    notes = str(row.get("notes") or "").strip()
    market_odds_text = _market_odds_text(row)
    fair_line_text = _fair_line_text(row)

    if status == "bet" and amount is not None and max_wager is not None:
        ev_text = f" EV={ev_percent:.2f}%" if ev_percent is not None else ""
        return f"{_poll_title(row)}{ev_text}{fair_line_text}{market_odds_text} Vote={amount}/{max_wager}"
    if status == "pick":
        ev_text = f" EV={ev_percent:.2f}%" if ev_percent is not None else ""
        title = _poll_title(row) if option else _skip_title(row)
        return f"{title}{ev_text}{fair_line_text}{market_odds_text} Vote=0"
    if status == "no_edge":
        ev_text = f" EV={ev_percent:.2f}%" if ev_percent is not None else ""
        title = _poll_title(row) if option else _skip_title(row)
        return f"{title}{ev_text}{fair_line_text}{market_odds_text} Vote=0"
    if status == "no_market":
        reason = notes or "no market"
        return f"{_skip_title(row)} Vote=0 ({reason})"
    if status == "unsupported":
        reason = notes or "unsupported"
        return f"{_skip_title(row)} Vote=0 ({reason})"
    if status == "missing_poll_data":
        reason = notes or "missing poll data"
        return f"{_skip_title(row)} Vote=0 ({reason})"
    return f"{_skip_title(row)} Vote=0 ({notes or 'no recommendation'})"


def _default_output(rows: list[dict[str, str]]) -> Path:
    sport = str((rows[0].get("sport") or "sport")).strip().lower() if rows else "sport"
    prefix = f"{sport}_v"
    version_numbers: list[int] = []
    for candidate in OUTPUT_DIR.glob(f"{sport}_v*.md"):
        suffix = candidate.stem[len(prefix) :]
        if suffix.isdigit():
            version_numbers.append(int(suffix))
    next_version = (max(version_numbers) + 1) if version_numbers else 1
    return OUTPUT_DIR / f"{sport}_v{next_version}.md"


def _filter_not_started_rows(
    rows: list[dict[str, str]],
    *,
    now_utc: datetime | None = None,
) -> list[dict[str, str]]:
    if not rows:
        return rows
    cutoff = now_utc or datetime.now(timezone.utc)
    filtered: list[dict[str, str]] = []
    for row in rows:
        game_time = _parse_game_time(str(row.get("game_time") or ""))
        if game_time is not None and game_time <= cutoff:
            continue
        filtered.append(row)
    return filtered


def render_vote_sheet(
    rows: list[dict[str, str]],
    *,
    lineup_input: str = "",
    predictions_input: str = "",
    prediction_positions_input: str = "",
    not_started_only: bool = False,
) -> str:
    if not rows:
        return "# Vote Sheet\n\nNo rows found.\n"

    day = str(rows[0].get("day") or "").strip()
    sport_key = str(rows[0].get("sport") or "").strip().lower()
    sport = sport_key.upper()
    daily_lineup_section = _render_daily_lineup_section(
        sport=sport_key,
        day=day,
        lineup_input=lineup_input,
        game_rows=rows,
    )
    lineup_section = _render_lineup_section(rows)
    predictions_section = _render_predictions_section(
        sport=sport_key,
        predictions_input=predictions_input,
        prediction_positions_input=prediction_positions_input,
        not_started_only=not_started_only,
    )
    rows = _display_rows(rows)
    rows = sorted(rows, key=_poll_sort_key)

    grouped: dict[tuple[str, str, str, str, str, str], list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        key = (
            str(row.get("game_order") or "").strip(),
            str(row.get("game_time") or "").strip(),
            str(row.get("game_label") or "").strip(),
            str(row.get("away_team") or "").strip(),
            str(row.get("home_team") or "").strip(),
            str(row.get("game_id") or "").strip(),
        )
        grouped[key].append(row)

    sections: list[str] = [
        f"# {sport} Vote Sheet - {day}",
        "",
        _summary_line(rows),
        "",
        "`Selection+Put` is the side plus the amount to enter, for example `No0`, `No50`, `Yes0`, or `Yes50`. Zero-cost pick rows just show the selection.",
        "",
    ]
    if daily_lineup_section:
        sections.extend(daily_lineup_section)
    if lineup_section:
        sections.extend(lineup_section)
    for key, game_rows in grouped.items():
        _game_order, game_time, _game_label, away_team, home_team, _game_id = key
        sections.append(f"## {_section_game_label(game_rows[0])}")
        sections.append(f"`{_format_game_time(game_time)}`")
        rows_for_game = sorted(game_rows, key=_poll_sort_key)
        sections.append("")
        sections.append("| Poll | Selection+Put | Consensus Prob (Odds) | EV | Sportsbook Odds | Source |")
        sections.append("| --- | --- | --- | --- | --- | --- |")
        sections.extend(_compact_table_row(row) for row in rows_for_game)
        sections.append("")

    if predictions_section:
        sections.extend(predictions_section)
    sections.extend(_consensus_explainer())

    return "\n".join(sections).rstrip() + "\n"


def main() -> None:
    args = parse_args()
    all_rows = _load_rows(args.input)
    rows = _filter_not_started_rows(all_rows) if args.not_started_only else all_rows
    source_rows = rows or all_rows
    sport_key = str((source_rows[0].get("sport") or "")).strip().lower() if source_rows else ""
    if args.refresh_predictions and sport_key:
        _refresh_prediction_recommendations(
            sport=sport_key,
            markets_csv=args.prediction_markets_csv,
            predictions_input=args.predictions_input,
            prediction_positions_input=args.prediction_positions_input,
        )
    output_path = Path(args.output) if args.output else _default_output(rows or all_rows)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        render_vote_sheet(
            rows,
            lineup_input=args.lineup_input,
            predictions_input=args.predictions_input,
            prediction_positions_input=args.prediction_positions_input,
            not_started_only=args.not_started_only,
        ),
        encoding="utf8",
    )
    print(f"Saved vote sheet to {output_path}")


if __name__ == "__main__":
    main()
