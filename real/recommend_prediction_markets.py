from __future__ import annotations

import argparse
import csv
import re
from pathlib import Path
from typing import Any

from fair_odds import MarketQuote, devig_quote, estimate_fair_line
from poll_market_matcher import build_market_row, load_csv_rows, normalize_team, team_pair
from realsports_api import build_realsports_client


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_MARKETS_CSV = BASE_DIR / "sportsbook_markets_consensus_live.csv"
DEFAULT_OUTPUT = BASE_DIR / "prediction_market_recommendations.csv"
FIELDNAMES = [
    "market_id",
    "sport",
    "game_id",
    "game_time",
    "game_display",
    "game_path",
    "market_type",
    "market_label",
    "status",
    "buy_url",
    "matched_books",
    "books",
    "max_value",
    "preset_values",
    "allow_accept_price_changes",
    "notes",
    "outcome_a_label",
    "outcome_a_real_prob",
    "outcome_a_fair_prob",
    "outcome_a_edge_prob",
    "outcome_a_payout_per_1",
    "outcome_a_ev_per_1",
    "outcome_a_ev_percent",
    "outcome_a_payout_for_10",
    "outcome_a_ev_for_10",
    "outcome_b_label",
    "outcome_b_real_prob",
    "outcome_b_fair_prob",
    "outcome_b_edge_prob",
    "outcome_b_payout_per_1",
    "outcome_b_ev_per_1",
    "outcome_b_ev_percent",
    "outcome_b_payout_for_10",
    "outcome_b_ev_for_10",
    "best_outcome",
    "best_real_prob",
    "best_fair_prob",
    "best_edge_prob",
    "best_payout_per_1",
    "best_ev_per_1",
    "best_ev_percent",
    "recommended_amount",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Fetch current Real prediction markets for one sport, match them to the "
            "sportsbook consensus, and calculate buy EV in rax."
        )
    )
    parser.add_argument("--sport", default="mlb", help="Sport key such as mlb.")
    parser.add_argument("--markets-csv", default=str(DEFAULT_MARKETS_CSV))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Optional max number of prediction markets to evaluate.",
    )
    return parser.parse_args()


def _parse_game_display(display: str) -> tuple[str, str]:
    text = str(display or "").strip()
    if " @ " not in text:
        return "", ""
    away_team, home_team = [part.strip() for part in text.split(" @ ", 1)]
    return away_team, home_team


def _same_game(home_team: str, away_team: str, market_home: str, market_away: str) -> bool:
    return team_pair(home_team, away_team) == team_pair(market_home, market_away)


def _matching_game_quotes(
    markets: list[Any],
    *,
    sport: str,
    home_team: str,
    away_team: str,
    market_family: str,
    period: str = "",
) -> list[Any]:
    matches: list[Any] = []
    for market in markets:
        if market.sport != sport or market.market_family != market_family:
            continue
        if period and str(market.period or "").strip() != period:
            continue
        if _same_game(home_team, away_team, market.home_team, market.away_team):
            matches.append(market)
    return matches


def _payout_per_1(real_probability: float) -> float:
    return 1.0 / max(float(real_probability), 1e-9)


def _ev_per_1(fair_probability: float, real_probability: float) -> float:
    payout_per_1 = _payout_per_1(real_probability)
    return (float(fair_probability) * payout_per_1) - 1.0


def _format_float(value: float | None, digits: int = 6) -> str:
    if value is None:
        return ""
    return f"{float(value):.{digits}f}"


def _preset_values(settings: dict[str, Any]) -> str:
    presets = settings.get("presets") or []
    values = [str((preset or {}).get("value") or "").strip() for preset in presets]
    return " | ".join(value for value in values if value)


def _empty_row(
    market_id: Any,
    *,
    sport: str,
    game_id: Any,
    game_time: str,
    game_display: str,
    game_path: str,
    market_type: str,
    market_label: str,
    buy_url: str,
    settings: dict[str, Any],
    status: str,
    notes: str,
) -> dict[str, Any]:
    return {
        "market_id": market_id or "",
        "sport": sport,
        "game_id": game_id or "",
        "game_time": game_time,
        "game_display": game_display,
        "game_path": game_path,
        "market_type": market_type,
        "market_label": market_label,
        "status": status,
        "buy_url": buy_url,
        "matched_books": 0,
        "books": "",
        "max_value": settings.get("maxValue") or "",
        "preset_values": _preset_values(settings),
        "allow_accept_price_changes": bool(settings.get("allowAcceptPriceChanges")),
        "notes": notes,
        "outcome_a_label": "",
        "outcome_a_real_prob": "",
        "outcome_a_fair_prob": "",
        "outcome_a_edge_prob": "",
        "outcome_a_payout_per_1": "",
        "outcome_a_ev_per_1": "",
        "outcome_a_ev_percent": "",
        "outcome_a_payout_for_10": "",
        "outcome_a_ev_for_10": "",
        "outcome_b_label": "",
        "outcome_b_real_prob": "",
        "outcome_b_fair_prob": "",
        "outcome_b_edge_prob": "",
        "outcome_b_payout_per_1": "",
        "outcome_b_ev_per_1": "",
        "outcome_b_ev_percent": "",
        "outcome_b_payout_for_10": "",
        "outcome_b_ev_for_10": "",
        "best_outcome": "",
        "best_real_prob": "",
        "best_fair_prob": "",
        "best_edge_prob": "",
        "best_payout_per_1": "",
        "best_ev_per_1": "",
        "best_ev_percent": "",
        "recommended_amount": 0,
    }


def _build_outcome_eval(label: str, real_probability: float, fair_probability: float) -> dict[str, Any]:
    payout_per_1 = _payout_per_1(real_probability)
    ev_per_1 = _ev_per_1(fair_probability, real_probability)
    return {
        "label": label,
        "real_prob": float(real_probability),
        "fair_prob": float(fair_probability),
        "edge_prob": float(fair_probability) - float(real_probability),
        "payout_per_1": payout_per_1,
        "ev_per_1": ev_per_1,
        "ev_percent": ev_per_1 * 100.0,
        "payout_for_10": payout_per_1 * 10.0,
        "ev_for_10": ev_per_1 * 10.0,
    }


def _matched_game_time(matched: list[Any]) -> str:
    timestamps = [
        str((market.raw or {}).get("event_date") or "").strip()
        for market in matched
        if str((market.raw or {}).get("event_date") or "").strip()
    ]
    if not timestamps:
        return ""
    return min(timestamps)


def _winner_fair_probs(
    sportsbook_markets: list[Any],
    *,
    sport: str,
    home_team: str,
    away_team: str,
) -> tuple[float, float, list[Any]]:
    matched = _matching_game_quotes(
        sportsbook_markets,
        sport=sport,
        home_team=home_team,
        away_team=away_team,
        market_family="game_winner",
    )
    devigged = [
        devig_quote(
            MarketQuote(
                book=market.book,
                line=0.0,
                over_odds=market.over_odds,
                under_odds=market.under_odds,
                updated_at=market.updated_at,
            )
        )
        for market in matched
        if market.over_odds is not None and market.under_odds is not None
    ]
    if not devigged:
        raise ValueError("No matching sportsbook moneyline quotes found.")
    total_weight = sum(quote.weight for quote in devigged)
    fair_home_prob = sum(quote.over_prob * quote.weight for quote in devigged) / total_weight
    return fair_home_prob, 1.0 - fair_home_prob, matched


def _rfi_fair_probs(
    sportsbook_markets: list[Any],
    *,
    sport: str,
    home_team: str,
    away_team: str,
) -> tuple[float, float, list[Any], str]:
    matched = _matching_game_quotes(
        sportsbook_markets,
        sport=sport,
        home_team=home_team,
        away_team=away_team,
        market_family="game_total",
        period="1I",
    )
    quotes = [
        MarketQuote(
            book=market.book,
            line=market.line,
            over_odds=market.over_odds,
            under_odds=market.under_odds,
            updated_at=market.updated_at,
        )
        for market in matched
        if market.line is not None
        and abs(float(market.line) - 0.5) <= 1e-9
        and market.over_odds is not None
        and market.under_odds is not None
    ]
    if not quotes:
        raise ValueError("No matching sportsbook 1st-inning total quotes found.")
    estimate = estimate_fair_line(quotes, target_line=0.5)
    return estimate.fair_over_prob, estimate.fair_under_prob, matched, estimate.source


def _spread_fair_probs(
    sportsbook_markets: list[Any],
    *,
    sport: str,
    home_team: str,
    away_team: str,
    target_home_line: float,
) -> tuple[float, float, list[Any], str]:
    matched = _matching_game_quotes(
        sportsbook_markets,
        sport=sport,
        home_team=home_team,
        away_team=away_team,
        market_family="game_spread",
    )
    quotes: list[MarketQuote] = []
    for market in matched:
        raw_home_spread = (market.raw or {}).get("home_spread")
        if raw_home_spread in (None, "", "None") or market.over_odds is None or market.under_odds is None:
            continue
        try:
            home_spread = float(raw_home_spread)
        except Exception:
            continue
        quotes.append(
            MarketQuote(
                book=market.book,
                line=-home_spread,
                over_odds=market.over_odds,
                under_odds=market.under_odds,
                updated_at=market.updated_at,
            )
        )
    if not quotes:
        raise ValueError("No matching sportsbook spread quotes found.")
    estimate = estimate_fair_line(quotes, target_line=float(target_home_line), default_scale=6.0)
    return estimate.fair_over_prob, estimate.fair_under_prob, matched, estimate.source


def _total_fair_probs(
    sportsbook_markets: list[Any],
    *,
    sport: str,
    home_team: str,
    away_team: str,
    target_total: float,
) -> tuple[float, float, list[Any], str]:
    matched = _matching_game_quotes(
        sportsbook_markets,
        sport=sport,
        home_team=home_team,
        away_team=away_team,
        market_family="game_total",
    )
    quotes = [
        MarketQuote(
            book=market.book,
            line=market.line,
            over_odds=market.over_odds,
            under_odds=market.under_odds,
            updated_at=market.updated_at,
        )
        for market in matched
        if market.line is not None and market.over_odds is not None and market.under_odds is not None
    ]
    if not quotes:
        raise ValueError("No matching sportsbook total quotes found.")
    estimate = estimate_fair_line(quotes, target_line=float(target_total), default_scale=8.0)
    return estimate.fair_over_prob, estimate.fair_under_prob, matched, estimate.source


def _parse_last_float(text: str) -> float | None:
    matches = re.findall(r"[-+]?\d+(?:\.\d+)?", str(text or ""))
    if not matches:
        return None
    try:
        return float(matches[-1])
    except Exception:
        return None


def _outcome_team_key(label: str) -> str:
    text = re.sub(r"\s*[-+]?\d+(?:\.\d+)?\s*$", "", str(label or "").strip())
    return normalize_team(text)


def _spread_target_home_line(
    outcomes: list[dict[str, Any]],
    *,
    home_team: str,
    away_team: str,
) -> float:
    normalized_home = normalize_team(home_team)
    normalized_away = normalize_team(away_team)
    for outcome in outcomes[:2]:
        label = str(outcome.get("label") or "").strip()
        line_value = _parse_last_float(label)
        if line_value is None:
            continue
        normalized_label = _outcome_team_key(label)
        if normalized_label == normalized_home:
            return -line_value
        if normalized_label == normalized_away:
            return line_value
    raise ValueError("Could not parse the prediction spread line from the Real outcomes.")


def _total_target_line(outcomes: list[dict[str, Any]]) -> float:
    for outcome in outcomes[:2]:
        label = str(outcome.get("label") or "").strip()
        line_value = _parse_last_float(label)
        if line_value is not None:
            return line_value
    raise ValueError("Could not parse the prediction total line from the Real outcomes.")


def _winner_outcomes(
    outcomes: list[dict[str, Any]],
    *,
    home_team: str,
    away_team: str,
    fair_home_prob: float,
    fair_away_prob: float,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    normalized_home = normalize_team(home_team)
    normalized_away = normalize_team(away_team)
    for outcome in outcomes[:2]:
        label = str(outcome.get("label") or "").strip()
        normalized_label = _outcome_team_key(label)
        if normalized_label == normalized_home:
            result.append(_build_outcome_eval(label, float(outcome.get("probability") or 0.0), fair_home_prob))
        elif normalized_label == normalized_away:
            result.append(_build_outcome_eval(label, float(outcome.get("probability") or 0.0), fair_away_prob))
    return result


def _spread_outcomes(
    outcomes: list[dict[str, Any]],
    *,
    home_team: str,
    away_team: str,
    fair_home_prob: float,
    fair_away_prob: float,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    normalized_home = normalize_team(home_team)
    normalized_away = normalize_team(away_team)
    for outcome in outcomes[:2]:
        label = str(outcome.get("label") or "").strip()
        normalized_label = _outcome_team_key(label)
        if normalized_label == normalized_home:
            result.append(_build_outcome_eval(label, float(outcome.get("probability") or 0.0), fair_home_prob))
        elif normalized_label == normalized_away:
            result.append(_build_outcome_eval(label, float(outcome.get("probability") or 0.0), fair_away_prob))
    return result


def _total_outcomes(
    outcomes: list[dict[str, Any]],
    *,
    fair_over_prob: float,
    fair_under_prob: float,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for outcome in outcomes[:2]:
        label = str(outcome.get("label") or "").strip()
        key = str(outcome.get("key") or "").strip().lower()
        label_key = label.strip().lower()
        if key == "over" or label_key.startswith("over "):
            result.append(_build_outcome_eval(label, float(outcome.get("probability") or 0.0), fair_over_prob))
        elif key == "under" or label_key.startswith("under "):
            result.append(_build_outcome_eval(label, float(outcome.get("probability") or 0.0), fair_under_prob))
    return result


def _rfi_outcomes(
    outcomes: list[dict[str, Any]],
    *,
    fair_yes_prob: float,
    fair_no_prob: float,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for outcome in outcomes[:2]:
        label = str(outcome.get("label") or "").strip()
        key = str(outcome.get("key") or "").strip().lower()
        label_key = label.strip().lower()
        if key == "yes" or label_key == "yrfi":
            result.append(_build_outcome_eval(label, float(outcome.get("probability") or 0.0), fair_yes_prob))
        elif key == "no" or label_key == "nrfi":
            result.append(_build_outcome_eval(label, float(outcome.get("probability") or 0.0), fair_no_prob))
    return result


def _row_from_evals(
    base: dict[str, Any],
    evals: list[dict[str, Any]],
    *,
    matched_books: int,
    books: str,
    notes: str,
    max_value: int,
) -> dict[str, Any]:
    if len(evals) < 2:
        return {
            **base,
            "status": "unsupported",
            "matched_books": matched_books,
            "books": books,
            "notes": "Could not align the Real outcomes with sportsbook fair probabilities.",
        }
    first, second = evals[0], evals[1]
    best = max(evals, key=lambda item: (float(item.get("ev_per_1") or 0.0), str(item.get("label") or "")))
    recommended_amount = max_value if float(best.get("ev_per_1") or 0.0) > 0 else 0
    return {
        **base,
        "status": "ok",
        "matched_books": matched_books,
        "books": books,
        "notes": notes,
        "outcome_a_label": first["label"],
        "outcome_a_real_prob": _format_float(first["real_prob"]),
        "outcome_a_fair_prob": _format_float(first["fair_prob"]),
        "outcome_a_edge_prob": _format_float(first["edge_prob"]),
        "outcome_a_payout_per_1": _format_float(first["payout_per_1"]),
        "outcome_a_ev_per_1": _format_float(first["ev_per_1"]),
        "outcome_a_ev_percent": _format_float(first["ev_percent"], 4),
        "outcome_a_payout_for_10": _format_float(first["payout_for_10"], 4),
        "outcome_a_ev_for_10": _format_float(first["ev_for_10"], 4),
        "outcome_b_label": second["label"],
        "outcome_b_real_prob": _format_float(second["real_prob"]),
        "outcome_b_fair_prob": _format_float(second["fair_prob"]),
        "outcome_b_edge_prob": _format_float(second["edge_prob"]),
        "outcome_b_payout_per_1": _format_float(second["payout_per_1"]),
        "outcome_b_ev_per_1": _format_float(second["ev_per_1"]),
        "outcome_b_ev_percent": _format_float(second["ev_percent"], 4),
        "outcome_b_payout_for_10": _format_float(second["payout_for_10"], 4),
        "outcome_b_ev_for_10": _format_float(second["ev_for_10"], 4),
        "best_outcome": best["label"],
        "best_real_prob": _format_float(best["real_prob"]),
        "best_fair_prob": _format_float(best["fair_prob"]),
        "best_edge_prob": _format_float(best["edge_prob"]),
        "best_payout_per_1": _format_float(best["payout_per_1"]),
        "best_ev_per_1": _format_float(best["ev_per_1"]),
        "best_ev_percent": _format_float(best["ev_percent"], 4),
        "recommended_amount": recommended_amount,
    }


def _evaluate_market(
    order_payload: dict[str, Any],
    game_id: Any,
    game_display: str,
    game_path: str,
    sport: str,
    sportsbook_markets: list[Any],
) -> dict[str, Any]:
    market = order_payload.get("market") or {}
    settings = order_payload.get("settings") or {}
    market_id = market.get("marketId") or ""
    market_type = str(market.get("marketType") or "").strip().lower()
    market_label = str(market.get("label") or "").strip()
    buy_url = f"https://web.realapp.com/predictions/marketorder/{market_id}/mode/buy"
    base = _empty_row(
        market_id,
        sport=sport,
        game_id=game_id,
        game_time="",
        game_display=game_display,
        game_path=game_path,
        market_type=market_type,
        market_label=market_label,
        buy_url=buy_url,
        settings=settings,
        status="unsupported",
        notes="",
    )
    away_team, home_team = _parse_game_display(game_display)
    if not home_team or not away_team:
        return {
            **base,
            "status": "parse_error",
            "notes": f"Could not parse away/home teams from '{game_display}'.",
        }
    outcomes = market.get("outcomes") or []
    if len(outcomes) < 2:
        return {
            **base,
            "status": "missing_data",
            "notes": "Prediction market had fewer than two outcomes.",
        }

    try:
        if market_type == "gamewinner":
            fair_home_prob, fair_away_prob, matched = _winner_fair_probs(
                sportsbook_markets,
                sport=sport,
                home_team=home_team,
                away_team=away_team,
            )
            evals = _winner_outcomes(
                outcomes,
                home_team=home_team,
                away_team=away_team,
                fair_home_prob=fair_home_prob,
                fair_away_prob=fair_away_prob,
            )
            notes = "weighted no-vig moneyline consensus"
        elif market_type == "rfi":
            fair_yes_prob, fair_no_prob, matched, source = _rfi_fair_probs(
                sportsbook_markets,
                sport=sport,
                home_team=home_team,
                away_team=away_team,
            )
            evals = _rfi_outcomes(
                outcomes,
                fair_yes_prob=fair_yes_prob,
                fair_no_prob=fair_no_prob,
            )
            notes = f"{source}; over=YRFI, under=NRFI"
        elif market_type == "pointspread":
            target_home_line = _spread_target_home_line(
                outcomes,
                home_team=home_team,
                away_team=away_team,
            )
            fair_home_prob, fair_away_prob, matched, source = _spread_fair_probs(
                sportsbook_markets,
                sport=sport,
                home_team=home_team,
                away_team=away_team,
                target_home_line=target_home_line,
            )
            evals = _spread_outcomes(
                outcomes,
                home_team=home_team,
                away_team=away_team,
                fair_home_prob=fair_home_prob,
                fair_away_prob=fair_away_prob,
            )
            notes = f"{source}; home-line target {target_home_line:g}"
        elif market_type == "totalpoints":
            target_total = _total_target_line(outcomes)
            fair_over_prob, fair_under_prob, matched, source = _total_fair_probs(
                sportsbook_markets,
                sport=sport,
                home_team=home_team,
                away_team=away_team,
                target_total=target_total,
            )
            evals = _total_outcomes(
                outcomes,
                fair_over_prob=fair_over_prob,
                fair_under_prob=fair_under_prob,
            )
            notes = f"{source}; total target {target_total:g}"
        else:
            return {
                **base,
                "status": "unsupported",
                "notes": f"Prediction market type '{market_type}' is not mapped yet.",
            }
    except ValueError as exc:
        return {
            **base,
            "status": "no_market",
            "notes": str(exc),
        }

    base["game_time"] = _matched_game_time(matched)
    return _row_from_evals(
        base,
        evals,
        matched_books=len({market.book for market in matched}),
        books=" | ".join(sorted({market.book for market in matched})),
        notes=notes,
        max_value=int(settings.get("maxValue") or 0),
    )


def _write_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    args = parse_args()
    sportsbook_rows = load_csv_rows(args.markets_csv)
    sportsbook_markets = [build_market_row(row) for row in sportsbook_rows]
    client = build_realsports_client()
    payload = client.get_prediction_game_markets(args.sport)
    game_markets = payload.get("gameMarkets") or []
    if args.limit > 0:
        game_markets = game_markets[: args.limit]

    rows: list[dict[str, Any]] = []
    for game_market in game_markets:
        if not isinstance(game_market, dict):
            continue
        market_id = game_market.get("id")
        order_payload = client.get_prediction_market_order(market_id, mode="buy")
        row = _evaluate_market(
            order_payload,
            game_id=game_market.get("gameId"),
            game_display=str((game_market.get("gameDisplay") or {}).get("display") or "").strip(),
            game_path=str((game_market.get("gameDisplay") or {}).get("path") or "").strip(),
            sport=str(game_market.get("sport") or args.sport).strip().lower(),
            sportsbook_markets=sportsbook_markets,
        )
        rows.append(row)

    output_path = Path(args.output)
    _write_rows(output_path, rows)
    print(output_path)
    print(f"saved {len(rows)} prediction market evaluations")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
