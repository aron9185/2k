from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from realsports_api import build_realsports_client


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT_DIR = BASE_DIR / "tmp"
FIELDNAMES = [
    "sport",
    "game_id",
    "game_display",
    "game_path",
    "market_id",
    "market_label",
    "volume_display",
    "is_locked",
    "is_settled",
    "settled_label",
    "outcome_count",
    "outcome_labels",
    "history_points",
    "history_first_timestamp",
    "history_last_timestamp",
    "history_tracked_outcome_slot",
    "history_tracked_outcome_key",
    "history_tracked_outcome_label",
    "history_probability_open",
    "history_probability_latest",
    "history_probability_change",
    "outcome_1_id",
    "outcome_1_key",
    "outcome_1_label",
    "outcome_1_probability",
    "outcome_1_price_label",
    "outcome_1_is_winner",
    "outcome_2_id",
    "outcome_2_key",
    "outcome_2_label",
    "outcome_2_probability",
    "outcome_2_price_label",
    "outcome_2_is_winner",
]
ORDER_FIELDNAMES = [
    "mode",
    "market_id",
    "market_type",
    "market_label",
    "is_locked",
    "is_settled",
    "volume_display",
    "outcome_1_id",
    "outcome_1_key",
    "outcome_1_label",
    "outcome_1_probability",
    "outcome_1_price_label",
    "outcome_2_id",
    "outcome_2_key",
    "outcome_2_label",
    "outcome_2_probability",
    "outcome_2_price_label",
    "initial_value",
    "max_value",
    "preset_values",
    "preset_labels",
    "allow_accept_price_changes",
    "disclaimer",
]
POSITION_FIELDNAMES = [
    "position_id",
    "market_id",
    "outcome_id",
    "game_id",
    "sport",
    "market_type",
    "market_label",
    "header_label",
    "outcome_label",
    "game_display",
    "game_path",
    "current_price_display",
    "can_cash_out",
    "is_settled",
    "latest_ledger_timestamp",
    "shares_value",
    "shares_value_display",
    "avg_display",
    "cost_display",
    "pays_display",
    "trade_count",
    "first_trade_created_at",
    "first_trade_amount",
    "first_trade_shares",
    "first_trade_display",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Inspect Real prediction data, including game markets, buy/sell order "
            "tickets, and individual position views."
        )
    )
    parser.add_argument(
        "--kind",
        choices=("markets", "order", "position"),
        default="markets",
        help="Prediction payload to fetch: sport game markets, a buy/sell order ticket, or a position.",
    )
    parser.add_argument(
        "--sport",
        default="mlb",
        help="Sport key such as mlb, nba, nhl, or soccer.",
    )
    parser.add_argument(
        "--market-id",
        default="",
        help="Prediction market id when --kind order is used.",
    )
    parser.add_argument(
        "--mode",
        choices=("buy", "sell"),
        default="buy",
        help="Order ticket mode when --kind order is used.",
    )
    parser.add_argument(
        "--position-id",
        default="",
        help="Prediction position id when --kind position is used.",
    )
    parser.add_argument(
        "--output",
        default="",
        help=(
            "CSV output path. Defaults to a file in real/tmp/ based on the requested kind."
        ),
    )
    parser.add_argument(
        "--dump-json",
        default="",
        help="Optional raw JSON dump path for the full prediction payload.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Optional max number of prediction markets to keep.",
    )
    return parser.parse_args()


def _resolve_output_path(output: str, sport: str) -> Path:
    if output:
        return Path(output)
    sport_key = str(sport or "").strip().lower() or "unknown"
    return DEFAULT_OUTPUT_DIR / f"{sport_key}_prediction_markets.csv"


def _resolve_kind_output_path(
    *,
    output: str,
    kind: str,
    sport: str,
    market_id: str,
    mode: str,
    position_id: str,
) -> Path:
    if output:
        return Path(output)
    sport_key = str(sport or "").strip().lower() or "unknown"
    if kind == "order":
        market_key = str(market_id or "").strip() or "unknown"
        return DEFAULT_OUTPUT_DIR / f"prediction_marketorder_{market_key}_{mode}.csv"
    if kind == "position":
        position_key = str(position_id or "").strip() or "unknown"
        return DEFAULT_OUTPUT_DIR / f"prediction_position_{position_key}.csv"
    return _resolve_output_path("", sport_key)


def _to_iso8601(timestamp_ms: Any) -> str:
    try:
        seconds = float(timestamp_ms) / 1000.0
    except Exception:
        return ""
    return (
        datetime.fromtimestamp(seconds, tz=timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _normalize_probability(value: Any) -> str:
    try:
        return f"{float(value):.6f}"
    except Exception:
        return ""


def _normalize_outcome(outcomes: list[dict[str, Any]], index: int) -> dict[str, Any]:
    if 0 <= index < len(outcomes) and isinstance(outcomes[index], dict):
        return outcomes[index]
    return {}


def _identify_history_outcome(
    outcomes: list[dict[str, Any]],
    history_latest_probability: Any,
) -> tuple[str, str, str]:
    try:
        latest_value = float(history_latest_probability)
    except Exception:
        return "", "", ""
    best_slot = ""
    best_key = ""
    best_label = ""
    best_diff = float("inf")
    for index, outcome in enumerate(outcomes, start=1):
        if not isinstance(outcome, dict):
            continue
        try:
            outcome_probability = float(outcome.get("probability"))
        except Exception:
            continue
        diff = abs(outcome_probability - latest_value)
        if diff < best_diff:
            best_diff = diff
            best_slot = str(index)
            best_key = str(outcome.get("key") or "").strip()
            best_label = str(outcome.get("label") or "").strip()
    return best_slot, best_key, best_label


def _flatten_market(market: dict[str, Any]) -> dict[str, Any]:
    outcomes = market.get("outcomes") or []
    history = market.get("probabilityHistory") or []
    game_display = market.get("gameDisplay") or {}
    outcome_1 = _normalize_outcome(outcomes, 0)
    outcome_2 = _normalize_outcome(outcomes, 1)
    history_open = history[0] if history else {}
    history_latest = history[-1] if history else {}
    history_open_probability = history_open.get("pA")
    history_latest_probability = history_latest.get("pA")
    history_change = ""
    try:
        history_change = f"{float(history_latest_probability) - float(history_open_probability):.6f}"
    except Exception:
        history_change = ""
    tracked_slot, tracked_key, tracked_label = _identify_history_outcome(
        outcomes,
        history_latest_probability,
    )
    return {
        "sport": str(market.get("sport") or "").strip().lower(),
        "game_id": market.get("gameId") or "",
        "game_display": str(game_display.get("display") or "").strip(),
        "game_path": str(game_display.get("path") or "").strip(),
        "market_id": market.get("id") or "",
        "market_label": str(market.get("label") or "").strip(),
        "volume_display": str(market.get("volumeDisplay") or "").strip(),
        "is_locked": bool(market.get("isLocked")),
        "is_settled": bool(market.get("isSettled")),
        "settled_label": str(market.get("settledLabel") or "").strip(),
        "outcome_count": len(outcomes),
        "outcome_labels": " | ".join(
            str((outcome or {}).get("label") or "").strip()
            for outcome in outcomes
            if str((outcome or {}).get("label") or "").strip()
        ),
        "history_points": len(history),
        "history_first_timestamp": _to_iso8601(history_open.get("t")),
        "history_last_timestamp": _to_iso8601(history_latest.get("t")),
        "history_tracked_outcome_slot": tracked_slot,
        "history_tracked_outcome_key": tracked_key,
        "history_tracked_outcome_label": tracked_label,
        "history_probability_open": _normalize_probability(history_open_probability),
        "history_probability_latest": _normalize_probability(history_latest_probability),
        "history_probability_change": history_change,
        "outcome_1_id": outcome_1.get("id") or "",
        "outcome_1_key": str(outcome_1.get("key") or "").strip(),
        "outcome_1_label": str(outcome_1.get("label") or "").strip(),
        "outcome_1_probability": _normalize_probability(outcome_1.get("probability")),
        "outcome_1_price_label": str(outcome_1.get("priceLabel") or "").strip(),
        "outcome_1_is_winner": bool(outcome_1.get("isWinner")),
        "outcome_2_id": outcome_2.get("id") or "",
        "outcome_2_key": str(outcome_2.get("key") or "").strip(),
        "outcome_2_label": str(outcome_2.get("label") or "").strip(),
        "outcome_2_probability": _normalize_probability(outcome_2.get("probability")),
        "outcome_2_price_label": str(outcome_2.get("priceLabel") or "").strip(),
        "outcome_2_is_winner": bool(outcome_2.get("isWinner")),
    }


def _flatten_order(payload: dict[str, Any], *, mode: str) -> dict[str, Any]:
    market = payload.get("market") or {}
    settings = payload.get("settings") or {}
    outcomes = market.get("outcomes") or []
    outcome_1 = _normalize_outcome(outcomes, 0)
    outcome_2 = _normalize_outcome(outcomes, 1)
    presets = settings.get("presets") or []
    return {
        "mode": mode,
        "market_id": market.get("marketId") or "",
        "market_type": str(market.get("marketType") or "").strip(),
        "market_label": str(market.get("label") or "").strip(),
        "is_locked": bool(market.get("isLocked")),
        "is_settled": bool(market.get("isSettled")),
        "volume_display": str(market.get("volumeDisplay") or "").strip(),
        "outcome_1_id": outcome_1.get("id") or "",
        "outcome_1_key": str(outcome_1.get("key") or "").strip(),
        "outcome_1_label": str(outcome_1.get("label") or "").strip(),
        "outcome_1_probability": _normalize_probability(outcome_1.get("probability")),
        "outcome_1_price_label": str(outcome_1.get("priceLabel") or "").strip(),
        "outcome_2_id": outcome_2.get("id") or "",
        "outcome_2_key": str(outcome_2.get("key") or "").strip(),
        "outcome_2_label": str(outcome_2.get("label") or "").strip(),
        "outcome_2_probability": _normalize_probability(outcome_2.get("probability")),
        "outcome_2_price_label": str(outcome_2.get("priceLabel") or "").strip(),
        "initial_value": settings.get("initialValue") or 0,
        "max_value": settings.get("maxValue") or 0,
        "preset_values": " | ".join(str((preset or {}).get("value") or "") for preset in presets),
        "preset_labels": " | ".join(
            str((preset or {}).get("label") or "").strip()
            for preset in presets
            if str((preset or {}).get("label") or "").strip()
        ),
        "allow_accept_price_changes": bool(settings.get("allowAcceptPriceChanges")),
        "disclaimer": str(settings.get("disclaimer") or "").strip().replace("\r\n", "\n"),
    }


def _detail_lookup(details: list[dict[str, Any]], label: str) -> str:
    label_key = str(label or "").strip().lower()
    for detail in details:
        if str((detail or {}).get("label") or "").strip().lower() == label_key:
            return str((detail or {}).get("display") or "").strip()
    return ""


def _flatten_position(payload: dict[str, Any]) -> dict[str, Any]:
    position = payload.get("position") or {}
    items = payload.get("items") or []
    details = position.get("details") or []
    first_item = items[0] if items else {}
    return {
        "position_id": str(position.get("sharedPositionId") or "").strip(),
        "market_id": position.get("marketId") or "",
        "outcome_id": position.get("outcomeId") or "",
        "game_id": position.get("gameId") or "",
        "sport": str(position.get("sport") or "").strip().lower(),
        "market_type": str(position.get("marketType") or "").strip(),
        "market_label": str(position.get("marketLabel") or "").strip(),
        "header_label": str(position.get("headerLabel") or "").strip(),
        "outcome_label": str(position.get("outcomeLabel") or "").strip(),
        "game_display": str(((position.get("marketDisplay") or {}).get("display")) or "").strip(),
        "game_path": str(((position.get("marketDisplay") or {}).get("path")) or "").strip(),
        "current_price_display": str(position.get("currentPriceDisplay") or "").strip(),
        "can_cash_out": bool(position.get("canCashOut")),
        "is_settled": bool(position.get("isSettled")),
        "latest_ledger_timestamp": str(position.get("latestLedgerTimestamp") or "").strip(),
        "shares_value": position.get("sharesValue") or "",
        "shares_value_display": str(position.get("sharesValueDisplay") or "").strip(),
        "avg_display": _detail_lookup(details, "Avg"),
        "cost_display": _detail_lookup(details, "Cost"),
        "pays_display": _detail_lookup(details, "Pays"),
        "trade_count": len(items),
        "first_trade_created_at": str(first_item.get("createdAt") or "").strip(),
        "first_trade_amount": first_item.get("amount") or "",
        "first_trade_shares": str(first_item.get("shares") or "").strip(),
        "first_trade_display": str(first_item.get("display") or "").strip(),
    }


def write_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf8") as handle:
        if not rows:
            fieldnames = FIELDNAMES
        else:
            sample_keys = set(rows[0].keys())
            if sample_keys == set(ORDER_FIELDNAMES):
                fieldnames = ORDER_FIELDNAMES
            elif sample_keys == set(POSITION_FIELDNAMES):
                fieldnames = POSITION_FIELDNAMES
            else:
                fieldnames = FIELDNAMES
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _print_summary(rows: list[dict[str, Any]]) -> None:
    labels: dict[str, int] = {}
    games: set[str] = set()
    locked = 0
    settled = 0
    for row in rows:
        label = str(row.get("market_label") or "").strip()
        labels[label] = labels.get(label, 0) + 1
        if row.get("game_display"):
            games.add(str(row["game_display"]))
        if row.get("is_locked"):
            locked += 1
        if row.get("is_settled"):
            settled += 1
    print(f"markets={len(rows)} games={len(games)} locked={locked} settled={settled}")
    for label, count in sorted(labels.items()):
        print(f"{count} {label}")


def main() -> int:
    args = parse_args()
    client = build_realsports_client()
    if args.kind == "order":
        payload = client.get_prediction_market_order(args.market_id, mode=args.mode)
        rows = [_flatten_order(payload, mode=args.mode)]
    elif args.kind == "position":
        payload = client.get_prediction_position(args.position_id)
        rows = [_flatten_position(payload)]
    else:
        payload = client.get_prediction_game_markets(args.sport)
        markets = payload.get("gameMarkets") or []
        if args.limit > 0:
            markets = markets[: args.limit]
        rows = [_flatten_market(market) for market in markets if isinstance(market, dict)]
    output_path = _resolve_kind_output_path(
        output=args.output,
        kind=args.kind,
        sport=args.sport,
        market_id=args.market_id,
        mode=args.mode,
        position_id=args.position_id,
    )
    write_rows(output_path, rows)
    if args.dump_json:
        dump_path = Path(args.dump_json)
        dump_path.parent.mkdir(parents=True, exist_ok=True)
        dump_path.write_text(json.dumps(payload, indent=2), encoding="utf8")
    print(output_path)
    if args.kind == "markets":
        _print_summary(rows)
    elif rows:
        print(json.dumps(rows[0], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
