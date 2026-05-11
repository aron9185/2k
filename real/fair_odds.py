from __future__ import annotations

"""Fair-line and EV helpers for sportsbook consensus markets.

This module is the math core for an "Optimal-like" workflow:

1. Take multiple sportsbook quotes for the same market.
2. Remove vig from each book's two-way price.
3. Weight sharper / fresher books more heavily.
4. Fit a consensus over-probability curve across lines when possible.
5. Evaluate a target offered line/odds for fair price, win probability, EV, and Kelly.

The first target use case is player props and other two-way markets where we
have a target line plus over/under odds from multiple books.
"""

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Sequence

from sportsbook_catalog import canonical_book_name, default_book_weights


EPSILON = 1e-9

# A small prior that leans toward sharper books without making soft books useless.
DEFAULT_BOOK_WEIGHTS = {
    "pinnacle": 1.35,
    "circa": 1.30,
    "bookmaker": 1.25,
    "betonline": 1.15,
    **default_book_weights(),
}


@dataclass(frozen=True)
class MarketQuote:
    book: str
    line: float
    over_odds: int
    under_odds: int
    updated_at: datetime | None = None
    weight: float | None = None


@dataclass(frozen=True)
class DeviggedQuote:
    book: str
    line: float
    over_prob: float
    under_prob: float
    weight: float
    updated_at: datetime | None = None


@dataclass(frozen=True)
class FairLineEstimate:
    target_line: float
    fair_over_prob: float
    fair_under_prob: float
    fair_over_odds: int
    fair_under_odds: int
    fair_line: float
    fitted_scale: float | None
    books_used: int
    source: str


@dataclass(frozen=True)
class BetEvaluation:
    side: str
    target_line: float
    offered_odds: int
    offered_implied_prob: float
    fair_prob: float
    fair_odds: int
    edge_prob: float
    ev_per_unit: float
    ev_percent: float
    kelly_fraction_full: float
    kelly_fraction_quarter: float


def normalize_book_name(book: str) -> str:
    return canonical_book_name(book)


def clamp_probability(probability: float) -> float:
    return min(max(float(probability), EPSILON), 1.0 - EPSILON)


def american_to_implied_prob(odds: int | float) -> float:
    odds = float(odds)
    if odds == 0:
        raise ValueError("American odds cannot be 0.")
    if odds > 0:
        return 100.0 / (odds + 100.0)
    return abs(odds) / (abs(odds) + 100.0)


def probability_to_american(probability: float) -> int:
    probability = clamp_probability(probability)
    if probability >= 0.5:
        return int(round(-100.0 * (probability / (1.0 - probability))))
    return int(round(100.0 * ((1.0 - probability) / probability)))


def net_payout_per_unit(odds: int | float) -> float:
    odds = float(odds)
    if odds == 0:
        raise ValueError("American odds cannot be 0.")
    if odds > 0:
        return odds / 100.0
    return 100.0 / abs(odds)


def devig_two_way_probs(over_odds: int | float, under_odds: int | float) -> tuple[float, float]:
    over_raw = american_to_implied_prob(over_odds)
    under_raw = american_to_implied_prob(under_odds)
    total = over_raw + under_raw
    if total <= 0:
        raise ValueError("Invalid two-way market; total implied probability must be positive.")
    return over_raw / total, under_raw / total


def logistic(value: float) -> float:
    if value >= 0:
        exp_neg = math.exp(-value)
        return 1.0 / (1.0 + exp_neg)
    exp_pos = math.exp(value)
    return exp_pos / (1.0 + exp_pos)


def logit(probability: float) -> float:
    probability = clamp_probability(probability)
    return math.log(probability / (1.0 - probability))


def recency_weight(
    updated_at: datetime | None,
    *,
    now: datetime | None = None,
    half_life_minutes: float = 20.0,
) -> float:
    if updated_at is None:
        return 1.0
    now = now or datetime.now(timezone.utc)
    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=timezone.utc)
    age_minutes = max(0.0, (now - updated_at).total_seconds() / 60.0)
    return 0.5 ** (age_minutes / max(half_life_minutes, 0.01))


def quote_weight(
    quote: MarketQuote,
    *,
    now: datetime | None = None,
    half_life_minutes: float = 20.0,
    book_weights: dict[str, float] | None = None,
) -> float:
    if quote.weight is not None:
        return float(quote.weight)
    book_weights = book_weights or DEFAULT_BOOK_WEIGHTS
    base = book_weights.get(normalize_book_name(quote.book), 1.0)
    return base * recency_weight(quote.updated_at, now=now, half_life_minutes=half_life_minutes)


def devig_quote(
    quote: MarketQuote,
    *,
    now: datetime | None = None,
    half_life_minutes: float = 20.0,
    book_weights: dict[str, float] | None = None,
) -> DeviggedQuote:
    over_prob, under_prob = devig_two_way_probs(quote.over_odds, quote.under_odds)
    return DeviggedQuote(
        book=quote.book,
        line=float(quote.line),
        over_prob=over_prob,
        under_prob=under_prob,
        weight=quote_weight(
            quote,
            now=now,
            half_life_minutes=half_life_minutes,
            book_weights=book_weights,
        ),
        updated_at=quote.updated_at,
    )


def _weighted_average(values: Sequence[float], weights: Sequence[float]) -> float:
    total_weight = sum(weights)
    if total_weight <= 0:
        raise ValueError("Weights must sum to a positive number.")
    return sum(value * weight for value, weight in zip(values, weights)) / total_weight


def _fit_logit_curve(quotes: Sequence[DeviggedQuote]) -> tuple[float, float] | None:
    if len(quotes) < 2:
        return None

    weights = [quote.weight for quote in quotes]
    xs = [quote.line for quote in quotes]
    ys = [logit(quote.over_prob) for quote in quotes]

    s = sum(weights)
    sx = sum(weight * x for weight, x in zip(weights, xs))
    sy = sum(weight * y for weight, y in zip(weights, ys))
    sxx = sum(weight * x * x for weight, x in zip(weights, xs))
    sxy = sum(weight * x * y for weight, x, y in zip(weights, xs, ys))
    denominator = (s * sxx) - (sx * sx)
    if abs(denominator) <= EPSILON:
        return None

    slope = ((s * sxy) - (sx * sy)) / denominator
    intercept = (sy - (slope * sx)) / s
    if slope >= -EPSILON:
        return None

    scale = -1.0 / slope
    fair_line = -intercept / slope
    if not math.isfinite(scale) or not math.isfinite(fair_line) or scale <= 0:
        return None
    return fair_line, scale


def estimate_fair_line(
    quotes: Iterable[MarketQuote],
    *,
    target_line: float,
    now: datetime | None = None,
    half_life_minutes: float = 20.0,
    default_scale: float = 1.5,
    book_weights: dict[str, float] | None = None,
    prefer_fitted_ladder: bool = False,
) -> FairLineEstimate:
    devigged_quotes = [
        devig_quote(
            quote,
            now=now,
            half_life_minutes=half_life_minutes,
            book_weights=book_weights,
        )
        for quote in quotes
    ]
    if not devigged_quotes:
        raise ValueError("At least one quote is required.")

    target_line = float(target_line)
    exact_quotes = [
        quote for quote in devigged_quotes if abs(quote.line - target_line) <= EPSILON
    ]
    fit = _fit_logit_curve(devigged_quotes)
    unique_exact_books = {normalize_book_name(quote.book) for quote in exact_quotes}
    unique_lines = {round(float(quote.line), 6) for quote in devigged_quotes}
    use_fitted_ladder = bool(fit is not None and len(unique_lines) >= 3) and (
        prefer_fitted_ladder or len(unique_exact_books) <= 1
    )
    use_exact_quotes = bool(exact_quotes) and not use_fitted_ladder
    if use_exact_quotes:
        fair_over_prob = _weighted_average(
            [quote.over_prob for quote in exact_quotes],
            [quote.weight for quote in exact_quotes],
        )
        source = (
            "exact-line consensus"
            if len(unique_exact_books) > 1
            else "single-book exact line"
        )
        # For exact-line consensus, anchor fair_line to the evaluated target.
        # This avoids surfacing outlier-fitted fair lines from unrelated ladder
        # quotes in UI fields that display consensus_fair_line.
        fair_line = target_line
        fitted_scale = fit[1] if fit else None
    else:
        if fit:
            fair_line, fitted_scale = fit
            fair_over_prob = logistic((fair_line - target_line) / fitted_scale)
            source = "fitted line curve"
        else:
            nearest = min(devigged_quotes, key=lambda quote: abs(quote.line - target_line))
            fitted_scale = default_scale
            nearest_logit = logit(nearest.over_prob)
            adjusted_logit = nearest_logit - ((target_line - nearest.line) / fitted_scale)
            fair_over_prob = logistic(adjusted_logit)
            fair_line = nearest.line + (nearest_logit * fitted_scale)
            source = "nearest-line fallback"

    fair_over_prob = clamp_probability(fair_over_prob)
    fair_under_prob = 1.0 - fair_over_prob
    return FairLineEstimate(
        target_line=target_line,
        fair_over_prob=fair_over_prob,
        fair_under_prob=fair_under_prob,
        fair_over_odds=probability_to_american(fair_over_prob),
        fair_under_odds=probability_to_american(fair_under_prob),
        fair_line=fair_line,
        fitted_scale=fitted_scale,
        books_used=len(devigged_quotes),
        source=source,
    )


def evaluate_offer(
    estimate: FairLineEstimate,
    *,
    side: str,
    offered_odds: int,
    kelly_fraction: float = 0.25,
) -> BetEvaluation:
    normalized_side = str(side or "").strip().lower()
    if normalized_side not in {"over", "under"}:
        raise ValueError("side must be 'over' or 'under'.")

    fair_prob = estimate.fair_over_prob if normalized_side == "over" else estimate.fair_under_prob
    fair_odds = estimate.fair_over_odds if normalized_side == "over" else estimate.fair_under_odds
    offered_implied_prob = american_to_implied_prob(offered_odds)
    payout = net_payout_per_unit(offered_odds)
    lose_prob = 1.0 - fair_prob
    ev_per_unit = (fair_prob * payout) - lose_prob
    kelly_full = max(0.0, ((payout * fair_prob) - lose_prob) / payout)

    return BetEvaluation(
        side=normalized_side,
        target_line=estimate.target_line,
        offered_odds=int(offered_odds),
        offered_implied_prob=offered_implied_prob,
        fair_prob=fair_prob,
        fair_odds=fair_odds,
        edge_prob=fair_prob - offered_implied_prob,
        ev_per_unit=ev_per_unit,
        ev_percent=ev_per_unit * 100.0,
        kelly_fraction_full=kelly_full,
        kelly_fraction_quarter=kelly_full * float(kelly_fraction),
    )


def consensus_snapshot(
    quotes: Iterable[MarketQuote],
    *,
    target_line: float,
    over_odds: int,
    under_odds: int,
    kelly_fraction: float = 0.25,
    now: datetime | None = None,
    half_life_minutes: float = 20.0,
    default_scale: float = 1.5,
    book_weights: dict[str, float] | None = None,
    prefer_fitted_ladder: bool = False,
) -> dict[str, object]:
    estimate = estimate_fair_line(
        quotes,
        target_line=target_line,
        now=now,
        half_life_minutes=half_life_minutes,
        default_scale=default_scale,
        book_weights=book_weights,
        prefer_fitted_ladder=prefer_fitted_ladder,
    )
    over_eval = evaluate_offer(
        estimate,
        side="over",
        offered_odds=over_odds,
        kelly_fraction=kelly_fraction,
    )
    under_eval = evaluate_offer(
        estimate,
        side="under",
        offered_odds=under_odds,
        kelly_fraction=kelly_fraction,
    )
    return {
        "estimate": estimate,
        "over": over_eval,
        "under": under_eval,
    }
