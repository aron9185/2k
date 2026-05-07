from __future__ import annotations

import argparse
import csv
from pathlib import Path
from typing import Any

from poll_market_matcher import build_market_row, load_csv_rows, normalize_team
from realsports_api import build_realsports_client
from recommend_prediction_markets import (
    _build_market_indexes,
    _matched_game_time,
    _scoped_sportsbook_markets,
    _outcome_team_key,
    _parse_last_float,
    _parse_game_display,
    _rfi_fair_probs,
    _spread_fair_probs,
    _total_fair_probs,
    _winner_fair_probs,
)


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_MARKETS_CSV = BASE_DIR / "sportsbook_markets_consensus_live.csv"
DEFAULT_OUTPUT = BASE_DIR / "prediction_position_recommendations.csv"
FIELDNAMES = [
    "position_id",
    "market_id",
    "sport",
    "game_id",
    "game_time",
    "game_display",
    "game_path",
    "market_type",
    "market_label",
    "status",
    "position_url",
    "sell_url",
    "can_cash_out",
    "matched_books",
    "books",
    "notes",
    "held_label",
    "held_real_prob",
    "held_fair_prob",
    "held_edge_prob",
    "avg_price_prob",
    "current_price_prob",
    "cost_basis",
    "payout_if_win",
    "cashout_now",
    "hold_fair_value",
    "hold_total_ev",
    "cashout_total_pl",
    "hold_vs_cashout_ev",
    "recommended_action",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Fetch current Real prediction open positions, match them to sportsbook "
            "consensus, and compare hold-to-settlement EV against cashing out now."
        )
    )
    parser.add_argument("--sport", default="mlb", help="Sport key such as mlb.")
    parser.add_argument("--markets-csv", default=str(DEFAULT_MARKETS_CSV))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    return parser.parse_args()


def _safe_float(value: Any) -> float | None:
    if value in (None, "", "None"):
        return None
    try:
        return float(value)
    except Exception:
        return None


def _format_float(value: float | None, digits: int = 6) -> str:
    if value is None:
        return ""
    return f"{float(value):.{digits}f}"


def _detail_lookup(details: list[dict[str, Any]], label: str) -> str:
    target = str(label or "").strip().lower()
    for detail in details or []:
        if str((detail or {}).get("label") or "").strip().lower() == target:
            return str((detail or {}).get("display") or "").strip()
    return ""


def _find_outcome(market_payload: dict[str, Any], outcome_id: Any) -> dict[str, Any]:
    target_id = str(outcome_id or "").strip()
    for outcome in (market_payload.get("market") or {}).get("outcomes") or []:
        if str((outcome or {}).get("id") or "").strip() == target_id:
            return outcome
    return {}


def _position_cost_and_payout(position_payload: dict[str, Any], summary_position: dict[str, Any]) -> tuple[float, float]:
    items = position_payload.get("items") or []
    total_cost = 0.0
    total_shares = 0.0
    saw_item = False
    for item in items:
        amount = _safe_float((item or {}).get("amount"))
        shares = _safe_float((item or {}).get("shares"))
        if amount is not None:
            total_cost += amount
            saw_item = True
        if shares is not None:
            total_shares += shares
    if saw_item and total_shares > 0:
        return total_cost, total_cost + total_shares

    details = summary_position.get("details") or []
    fallback_cost = _safe_float(_detail_lookup(details, "Cost")) or 0.0
    fallback_payout = _safe_float(_detail_lookup(details, "Pays")) or 0.0
    return fallback_cost, fallback_payout


def _held_fair_probability(
    *,
    sport: str,
    market_type: str,
    held_outcome: dict[str, Any],
    home_team: str,
    away_team: str,
    sportsbook_markets: list[Any],
) -> tuple[float, list[Any], str]:
    if market_type == "gamewinner":
        fair_home_prob, fair_away_prob, matched = _winner_fair_probs(
            sportsbook_markets,
            sport=sport,
            home_team=home_team,
            away_team=away_team,
        )
        label_team = normalize_team(str(held_outcome.get("label") or "").strip())
        if label_team == normalize_team(home_team):
            return fair_home_prob, matched, "weighted no-vig moneyline consensus"
        if label_team == normalize_team(away_team):
            return fair_away_prob, matched, "weighted no-vig moneyline consensus"
        raise ValueError("Could not align held game-winner outcome with home/away teams.")

    if market_type == "rfi":
        fair_yes_prob, fair_no_prob, matched, source = _rfi_fair_probs(
            sportsbook_markets,
            sport=sport,
            home_team=home_team,
            away_team=away_team,
        )
        key = str(held_outcome.get("key") or "").strip().lower()
        label = str(held_outcome.get("label") or "").strip().lower()
        if key == "yes" or label == "yrfi":
            return fair_yes_prob, matched, f"{source}; over=YRFI, under=NRFI"
        if key == "no" or label == "nrfi":
            return fair_no_prob, matched, f"{source}; over=YRFI, under=NRFI"
        raise ValueError("Could not align held RFI outcome with YRFI/NRFI.")

    if market_type == "pointspread":
        label = str(held_outcome.get("label") or "").strip()
        label_team = _outcome_team_key(label)
        line_value = _parse_last_float(label)
        if line_value is None:
            raise ValueError("Could not parse the held prediction spread line.")
        if label_team == normalize_team(home_team):
            target_home_line = -line_value
            fair_home_prob, _fair_away_prob, matched, source = _spread_fair_probs(
                sportsbook_markets,
                sport=sport,
                home_team=home_team,
                away_team=away_team,
                target_home_line=target_home_line,
            )
            return fair_home_prob, matched, f"{source}; home-line target {target_home_line:g}"
        if label_team == normalize_team(away_team):
            target_home_line = line_value
            _fair_home_prob, fair_away_prob, matched, source = _spread_fair_probs(
                sportsbook_markets,
                sport=sport,
                home_team=home_team,
                away_team=away_team,
                target_home_line=target_home_line,
            )
            return fair_away_prob, matched, f"{source}; home-line target {target_home_line:g}"
        raise ValueError("Could not align held spread outcome with the home/away teams.")

    if market_type == "totalpoints":
        label = str(held_outcome.get("label") or "").strip()
        line_value = _parse_last_float(label)
        if line_value is None:
            raise ValueError("Could not parse the held prediction total line.")
        fair_over_prob, fair_under_prob, matched, source = _total_fair_probs(
            sportsbook_markets,
            sport=sport,
            home_team=home_team,
            away_team=away_team,
            target_total=line_value,
        )
        lowered_label = label.lower()
        key = str(held_outcome.get("key") or "").strip().lower()
        if key == "over" or lowered_label.startswith("over "):
            return fair_over_prob, matched, f"{source}; total target {line_value:g}"
        if key == "under" or lowered_label.startswith("under "):
            return fair_under_prob, matched, f"{source}; total target {line_value:g}"
        raise ValueError("Could not align held total outcome with Over/Under.")

    raise ValueError(f"Prediction market type '{market_type}' is not mapped yet.")


def _row_for_position(
    summary_position: dict[str, Any],
    position_payload: dict[str, Any],
    market_payload: dict[str, Any],
    sportsbook_markets: list[Any],
    *,
    sport_filter: str,
) -> dict[str, Any]:
    sport = str(summary_position.get("sport") or "").strip().lower()
    market_id = summary_position.get("marketId") or ""
    position_id = str(summary_position.get("sharedPositionId") or "").strip()
    game_id = summary_position.get("gameId") or ""
    game_display = str(((summary_position.get("marketDisplay") or {}).get("display")) or "").strip()
    market_type = str(summary_position.get("marketType") or "").strip().lower()
    market_label = str(summary_position.get("marketLabel") or "").strip()
    position_url = f"https://web.realapp.com/predictions/position/{position_id}" if position_id else ""
    sell_url = f"https://web.realapp.com/predictions/marketorder/{market_id}/mode/sell" if market_id else ""

    base = {
        "position_id": position_id,
        "market_id": market_id,
        "sport": sport,
        "game_id": game_id,
        "game_time": "",
        "game_display": game_display,
        "game_path": str(((summary_position.get("marketDisplay") or {}).get("path")) or "").strip(),
        "market_type": market_type,
        "market_label": market_label,
        "status": "unsupported",
        "position_url": position_url,
        "sell_url": sell_url,
        "can_cash_out": bool(summary_position.get("canCashOut")),
        "matched_books": 0,
        "books": "",
        "notes": "",
        "held_label": "",
        "held_real_prob": "",
        "held_fair_prob": "",
        "held_edge_prob": "",
        "avg_price_prob": "",
        "current_price_prob": "",
        "cost_basis": "",
        "payout_if_win": "",
        "cashout_now": "",
        "hold_fair_value": "",
        "hold_total_ev": "",
        "cashout_total_pl": "",
        "hold_vs_cashout_ev": "",
        "recommended_action": "",
    }
    if sport != sport_filter:
        return base

    away_team, home_team = _parse_game_display(game_display)
    if not home_team or not away_team:
        return {
            **base,
            "status": "parse_error",
            "notes": f"Could not parse away/home teams from '{game_display}'.",
        }

    held_outcome = _find_outcome(market_payload, summary_position.get("outcomeId"))
    if not held_outcome:
        return {
            **base,
            "status": "missing_data",
            "notes": "Could not locate the held outcome in the current market payload.",
        }

    try:
        fair_prob, matched, notes = _held_fair_probability(
            sport=sport,
            market_type=market_type,
            held_outcome=held_outcome,
            home_team=home_team,
            away_team=away_team,
            sportsbook_markets=sportsbook_markets,
        )
    except ValueError as exc:
        return {
            **base,
            "status": "no_market",
            "notes": str(exc),
        }

    cost_basis, payout_if_win = _position_cost_and_payout(position_payload, summary_position)
    cashout_now = _safe_float(summary_position.get("sharesValue")) or 0.0
    hold_fair_value = fair_prob * payout_if_win
    hold_total_ev = hold_fair_value - cost_basis
    cashout_total_pl = cashout_now - cost_basis
    hold_vs_cashout_ev = hold_fair_value - cashout_now
    can_cash_out = bool(summary_position.get("canCashOut"))
    if not can_cash_out:
        recommended_action = "HOLD"
    elif hold_vs_cashout_ev > 0:
        recommended_action = "HOLD"
    else:
        recommended_action = "CASHOUT"

    avg_price_display = _detail_lookup(summary_position.get("details") or [], "Avg").replace("%", "").strip()
    current_price_display = str(summary_position.get("currentPriceDisplay") or "").replace("%", "").strip()
    base["game_time"] = _matched_game_time(matched)
    return {
        **base,
        "status": "ok",
        "matched_books": len({market.book for market in matched}),
        "books": " | ".join(sorted({market.book for market in matched})),
        "notes": notes,
        "held_label": str(held_outcome.get("label") or "").strip(),
        "held_real_prob": _format_float(_safe_float(held_outcome.get("probability"))),
        "held_fair_prob": _format_float(fair_prob),
        "held_edge_prob": _format_float(fair_prob - (_safe_float(held_outcome.get("probability")) or 0.0)),
        "avg_price_prob": _format_float((_safe_float(avg_price_display) or 0.0) / 100.0) if avg_price_display else "",
        "current_price_prob": _format_float((_safe_float(current_price_display) or 0.0) / 100.0) if current_price_display else "",
        "cost_basis": _format_float(cost_basis, 4),
        "payout_if_win": _format_float(payout_if_win, 4),
        "cashout_now": _format_float(cashout_now, 4),
        "hold_fair_value": _format_float(hold_fair_value, 4),
        "hold_total_ev": _format_float(hold_total_ev, 4),
        "cashout_total_pl": _format_float(cashout_total_pl, 4),
        "hold_vs_cashout_ev": _format_float(hold_vs_cashout_ev, 4),
        "recommended_action": recommended_action,
    }


def _write_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    args = parse_args()
    sport = str(args.sport or "").strip().lower()
    all_sportsbook_rows = load_csv_rows(args.markets_csv)
    sportsbook_rows = [
        row
        for row in all_sportsbook_rows
        if str(row.get("sport") or "").strip().lower() == sport
    ] or all_sportsbook_rows
    sportsbook_markets = [build_market_row(row) for row in sportsbook_rows]
    by_sport, by_sport_pair = _build_market_indexes(sportsbook_markets)
    client = build_realsports_client()
    open_positions = client.get_prediction_open_positions().get("positions") or []

    rows: list[dict[str, Any]] = []
    for summary_position in open_positions:
        if str(summary_position.get("sport") or "").strip().lower() != sport:
            continue
        if str(summary_position.get("marketType") or "").strip().lower() not in {
            "gamewinner",
            "rfi",
            "pointspread",
            "totalpoints",
        }:
            continue
        position_id = str(summary_position.get("sharedPositionId") or "").strip()
        market_id = summary_position.get("marketId")
        game_display = str(((summary_position.get("marketDisplay") or {}).get("display")) or "").strip()
        market_type = str(summary_position.get("marketType") or "").strip().lower()
        scoped_markets, sport_rows = _scoped_sportsbook_markets(
            sport=sport,
            game_display=game_display,
            market_type=market_type,
            by_sport=by_sport,
            by_sport_pair=by_sport_pair,
        )
        selected_markets = scoped_markets or sport_rows or sportsbook_markets
        position_payload = client.get_prediction_position(position_id) if position_id else {"position": summary_position, "items": []}
        market_payload = client.get_prediction_market_order(market_id, mode="buy")
        row = _row_for_position(
            summary_position,
            position_payload,
            market_payload,
            selected_markets,
            sport_filter=sport,
        )
        if (
            str(row.get("status") or "") == "no_market"
            and sport_rows
            and selected_markets is not sport_rows
        ):
            fallback_row = _row_for_position(
                summary_position,
                position_payload,
                market_payload,
                sport_rows,
                sport_filter=sport,
            )
            if str(fallback_row.get("status") or "") != "no_market":
                row = fallback_row
        rows.append(row)

    output_path = Path(args.output)
    _write_rows(output_path, rows)
    print(output_path)
    print(f"saved {len(rows)} open-position evaluations")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
