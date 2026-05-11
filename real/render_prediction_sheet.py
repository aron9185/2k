from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "output"
DEFAULT_INPUT = BASE_DIR / "prediction_market_recommendations.csv"
CHINA_TZ = timezone(timedelta(hours=8))

MARKET_ORDER = {
    "gamewinner": 0,
    "pointspread": 1,
    "totalpoints": 2,
    "rfi": 3,
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Render a game-by-game Real prediction sheet from "
            "real/prediction_market_recommendations.csv, optionally including "
            "open hold/cashout positions in the same markdown output."
        )
    )
    parser.add_argument("--input", default=str(DEFAULT_INPUT))
    parser.add_argument("--positions-input", default="")
    parser.add_argument("--output", default="")
    return parser.parse_args()


def _load_rows(path: str | Path) -> list[dict[str, str]]:
    if csv.field_size_limit() < 10_000_000:
        csv.field_size_limit(10_000_000)
    csv_path = Path(path)
    with csv_path.open("r", encoding="utf8", newline="") as handle:
        return list(csv.DictReader(handle))


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


def _table_escape(value: object) -> str:
    text = str(value or "").replace("\n", " ").strip()
    return text.replace("|", "\\|")


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


def _format_game_time(value: str) -> str:
    parsed = _parse_game_time(value)
    if parsed is None:
        return str(value or "").strip()
    return parsed.astimezone(CHINA_TZ).strftime("%Y-%m-%d %H:%M UTC+8")


def _format_probability(value: str) -> str:
    number = _safe_float(value)
    if number is None:
        return ""
    return f"{number * 100.0:.1f}%"


def _format_signed_percent(value: str) -> str:
    number = _safe_float(value)
    if number is None:
        return ""
    return f"{number:+.2f}%"


def _format_signed_probability_delta(value: str) -> str:
    number = _safe_float(value)
    if number is None:
        return ""
    return f"{number * 100.0:+.2f}%"


def _format_signed_rax(value: str) -> str:
    number = _safe_float(value)
    if number is None:
        return ""
    return f"{number:+.2f}"


def _format_rax(value: str) -> str:
    number = _safe_float(value)
    if number is None:
        return ""
    return f"{number:.2f}".rstrip("0").rstrip(".")


def _summary_line(market_rows: list[dict[str, str]], position_rows: list[dict[str, str]]) -> str:
    total_rax = sum(_safe_int(str(row.get("recommended_amount") or "")) or 0 for row in market_rows)
    active_buys = sum(1 for row in market_rows if (_safe_int(str(row.get("recommended_amount") or "")) or 0) > 0)
    open_positions = len(position_rows)
    current_cashout = sum(_safe_float(str(row.get("cashout_now") or "")) or 0.0 for row in position_rows)
    return (
        f"**Summary:** {len(market_rows)} markets, {active_buys} max-buy recommendations, "
        f"{open_positions} open positions, current cashout {current_cashout:.2f} rax."
    )


def prediction_summary_line(
    market_rows: list[dict[str, str]],
    position_rows: list[dict[str, str]],
) -> str:
    return _summary_line(market_rows, position_rows)


def _selection_rax_text(row: dict[str, str]) -> str:
    outcome = str(row.get("best_outcome") or "").strip()
    amount = _safe_int(str(row.get("recommended_amount") or "")) or 0
    if not outcome:
        return f"Skip{amount}"
    separator = " " if outcome[-1:].isdigit() else ""
    return f"{outcome}{separator}{amount}"


def _real_price_text(row: dict[str, str]) -> str:
    probability = _format_probability(str(row.get("best_real_prob") or ""))
    payout = _format_rax(str(row.get("best_payout_per_1") or ""))
    if probability and payout:
        return f"{probability} ({payout}x)"
    return probability or payout


def _consensus_text(row: dict[str, str]) -> str:
    fair_prob = _format_probability(str(row.get("best_fair_prob") or ""))
    edge = _format_signed_probability_delta(str(row.get("best_edge_prob") or ""))
    if fair_prob and edge:
        return f"{fair_prob} ({edge})"
    return fair_prob or edge


def _best_outcome_slot(row: dict[str, str]) -> str:
    best_outcome = str(row.get("best_outcome") or "").strip()
    if best_outcome and best_outcome == str(row.get("outcome_a_label") or "").strip():
        return "a"
    if best_outcome and best_outcome == str(row.get("outcome_b_label") or "").strip():
        return "b"
    return "a"


def _ev_text(row: dict[str, str]) -> str:
    ev_10 = _format_signed_rax(str(row.get(f"outcome_{_best_outcome_slot(row)}_ev_for_10") or ""))
    ev_percent = _format_signed_percent(str(row.get("best_ev_percent") or ""))
    if ev_10 and ev_percent:
        return f"{ev_10} / 10 ({ev_percent})"
    return ev_10 or ev_percent


def _market_label(row: dict[str, str]) -> str:
    label = str(row.get("market_label") or "").strip() or str(row.get("market_type") or "").strip()
    url = str(row.get("buy_url") or "").strip()
    if url:
        return f"[{label}]({url})"
    return label


def _books_text(row: dict[str, str]) -> str:
    books = str(row.get("books") or "").strip()
    matched_books = _safe_int(str(row.get("matched_books") or ""))
    if books and matched_books is not None:
        return f"{books} ({matched_books})"
    if books:
        return books
    notes = str(row.get("notes") or "").strip()
    status = str(row.get("status") or "").strip()
    if notes and status and status != "ok":
        return notes
    return str(matched_books or "")


def _row_game_order(row: dict[str, str]) -> int:
    return _safe_int(str(row.get("game_order") or "")) or 999999


def _row_game_time(row: dict[str, str]) -> datetime:
    return _parse_game_time(str(row.get("game_time") or "")) or datetime.max.replace(tzinfo=timezone.utc)


def _market_row_sort_key(row: dict[str, str]) -> tuple[int, datetime, str, str, int, float, str]:
    parsed = _parse_game_time(str(row.get("game_time") or ""))
    return (
        _row_game_order(row),
        parsed or datetime.max.replace(tzinfo=timezone.utc),
        str(row.get("game_display") or "").strip(),
        str(row.get("game_id") or "").strip(),
        MARKET_ORDER.get(str(row.get("market_type") or "").strip().lower(), 99),
        -(_safe_float(str(row.get("best_ev_per_1") or "")) or -9999.0),
        str(row.get("market_id") or "").strip(),
    )


def _position_row_sort_key(row: dict[str, str]) -> tuple[int, datetime, str, str, int, str]:
    return (
        _row_game_order(row),
        _row_game_time(row),
        str(row.get("game_display") or "").strip(),
        str(row.get("game_id") or "").strip(),
        MARKET_ORDER.get(str(row.get("market_type") or "").strip().lower(), 99),
        str(row.get("position_id") or "").strip(),
    )


def _default_output(rows: list[dict[str, str]]) -> Path:
    sport = str((rows[0].get("sport") or "sport")).strip().lower() if rows else "sport"
    prefix = f"{sport}_predictions_v"
    version_numbers: list[int] = []
    for candidate in OUTPUT_DIR.glob(f"{sport}_predictions_v*.md"):
        suffix = candidate.stem[len(prefix) :]
        if suffix.isdigit():
            version_numbers.append(int(suffix))
    next_version = (max(version_numbers) + 1) if version_numbers else 1
    return OUTPUT_DIR / f"{sport}_predictions_v{next_version}.md"


def _market_section_key(row: dict[str, str]) -> tuple[str, str, str]:
    parsed = _parse_game_time(str(row.get("game_time") or ""))
    if parsed is not None:
        parsed = parsed.replace(second=0, microsecond=0)
    return (
        parsed.isoformat() if parsed is not None else str(row.get("game_time") or "").strip(),
        str(row.get("game_display") or "").strip(),
        str(row.get("game_id") or "").strip(),
    )


def _position_section_key(row: dict[str, str]) -> tuple[str, str, str]:
    parsed = _parse_game_time(str(row.get("game_time") or ""))
    if parsed is not None:
        parsed = parsed.replace(second=0, microsecond=0)
    return (
        parsed.isoformat() if parsed is not None else str(row.get("game_time") or "").strip(),
        str(row.get("game_display") or "").strip(),
        str(row.get("game_id") or "").strip(),
    )


def _section_title(
    key: tuple[str, str, str],
    duplicate_counts: dict[tuple[str, str], int],
) -> str:
    game_time, game_display, game_id = key
    if duplicate_counts.get((game_time, game_display), 0) > 1 and game_id:
        return f"{game_display} [{game_id}]"
    return game_display


def _position_market_cell(row: dict[str, str]) -> str:
    market = str(row.get("market_label") or "").strip()
    held = str(row.get("held_label") or "").strip()
    label = f"{market}: {held}" if held else market
    url = str(row.get("position_url") or "").strip()
    if url:
        return f"[{label}]({url})"
    return label


def _position_action_cell(row: dict[str, str]) -> str:
    action = str(row.get("recommended_action") or "").strip()
    sell_url = str(row.get("sell_url") or "").strip()
    if action == "CASHOUT" and sell_url:
        return f"[CASHOUT]({sell_url})"
    return action


def _auto_positions_input(
    input_path: Path,
    market_rows: list[dict[str, str]],
) -> Path | None:
    sport = str((market_rows[0].get("sport") or "")).strip().lower() if market_rows else ""
    candidates = []
    if sport:
        candidates.append(input_path.with_name(f"prediction_position_recommendations_{sport}.csv"))
        candidates.append(BASE_DIR / f"prediction_position_recommendations_{sport}.csv")
    candidates.append(BASE_DIR / "prediction_position_recommendations.csv")
    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        if candidate.exists():
            return candidate
    return None


def _load_position_rows(
    input_path: Path,
    positions_input: str,
    market_rows: list[dict[str, str]],
) -> list[dict[str, str]]:
    if positions_input:
        candidate = Path(positions_input)
        if candidate.exists():
            return _load_rows(candidate)
        return []
    auto_path = _auto_positions_input(input_path, market_rows)
    if auto_path is None:
        return []
    return _load_rows(auto_path)


def _explainer() -> list[str]:
    return [
        "## How Prediction EV Is Calculated",
        "",
        "- Real prediction buy prices are taken from the current market probability shown in the buy ticket.",
        "- The sheet treats the terminal payout multiple as `1 / Real probability`, so a 40% price pays about `2.5x` if it wins.",
        "- Consensus fair probability comes from the same sportsbook no-vig logic used in the normal poll sheets.",
        "- `Game Winner` uses weighted no-vig moneyline consensus from DraftKings and FanDuel when both are available.",
        "- `Spread` uses the sportsbook spread consensus at the matching home-line target.",
        "- `Total` uses the sportsbook total consensus at the matching over/under line.",
        "- `Run in 1st inning?` maps sportsbook `Over 0.5 runs in 1st inning` to `YRFI` and `Under 0.5` to `NRFI`.",
        "- `Consensus Prob (Edge)` shows sportsbook fair win probability and the edge in percentage points, so `+14.00%` means a `0.14` probability gap.",
        "- EV per 1 rax is `fair probability / Real probability - 1`.",
        "- `EV / 10` shows the expected rax gain or loss for a `10` rax buy at the current Real price.",
        "- Open positions compare `Cashout Now` against `Hold Fair Value = fair win probability * payout if the position wins`.",
        "- `Hold-Cashout EV` is the incremental value of keeping the position from here instead of selling now.",
        "- Recommendations currently follow the same rule you wanted on the poll side: max buy if EV is positive, otherwise `0`.",
        "- Current sizing and cashout values still come from Real's displayed values, so any hidden sell slippage is not modeled yet.",
        "",
    ]


def render_prediction_sections(
    market_rows: list[dict[str, str]],
    position_rows: list[dict[str, str]] | None = None,
    *,
    heading_level: int = 2,
) -> list[str]:
    position_rows = position_rows or []
    heading_prefix = "#" * max(1, int(heading_level))

    grouped_markets: dict[tuple[str, str, str], list[dict[str, str]]] = defaultdict(list)
    grouped_positions: dict[tuple[str, str, str], list[dict[str, str]]] = defaultdict(list)
    section_game_order: dict[tuple[str, str, str], int] = {}
    for row in sorted(market_rows, key=_market_row_sort_key):
        key = _market_section_key(row)
        grouped_markets[key].append(row)
        section_game_order[key] = min(section_game_order.get(key, 999999), _row_game_order(row))
    for row in sorted(position_rows, key=_position_row_sort_key):
        key = _position_section_key(row)
        grouped_positions[key].append(row)
        section_game_order[key] = min(section_game_order.get(key, 999999), _row_game_order(row))

    section_keys = sorted(
        set(grouped_markets) | set(grouped_positions),
        key=lambda key: (
            section_game_order.get(key, 999999),
            _parse_game_time(key[0]) or datetime.max.replace(tzinfo=timezone.utc),
            key[1],
            key[2],
        ),
    )
    duplicate_counts: dict[tuple[str, str], int] = defaultdict(int)
    for key in section_keys:
        duplicate_counts[(key[0], key[1])] += 1

    sections: list[str] = []
    for key in section_keys:
        game_time, _game_display, _game_id = key
        title = _section_title(key, duplicate_counts)
        sections.append(f"{heading_prefix} {title}")
        if game_time:
            sections.append(f"`{_format_game_time(game_time)}`")
        sections.append("")

        game_market_rows = grouped_markets.get(key) or []
        if game_market_rows:
            sections.append("| Market | Selection+Rax | Real Price | Consensus Prob (Edge) | EV / 10 | Books |")
            sections.append("| --- | --- | --- | --- | --- | --- |")
            for row in game_market_rows:
                cells = [
                    _market_label(row),
                    _selection_rax_text(row),
                    _real_price_text(row),
                    _consensus_text(row),
                    _ev_text(row),
                    _books_text(row),
                ]
                sections.append("| " + " | ".join(_table_escape(cell) for cell in cells) + " |")
            sections.append("")

        game_position_rows = grouped_positions.get(key) or []
        if game_position_rows:
            sections.append("| Position | Cashout Now | Hold Fair Value | Hold-Cashout EV | Hold Total EV | Cashout P/L | Action |")
            sections.append("| --- | --- | --- | --- | --- | --- | --- |")
            for row in game_position_rows:
                cells = [
                    _position_market_cell(row),
                    _format_rax(str(row.get("cashout_now") or "")),
                    _format_rax(str(row.get("hold_fair_value") or "")),
                    _format_signed_rax(str(row.get("hold_vs_cashout_ev") or "")),
                    _format_signed_rax(str(row.get("hold_total_ev") or "")),
                    _format_signed_rax(str(row.get("cashout_total_pl") or "")),
                    _position_action_cell(row),
                ]
                sections.append("| " + " | ".join(_table_escape(cell) for cell in cells) + " |")
            sections.append("")
    return sections


def render_prediction_sheet(
    market_rows: list[dict[str, str]],
    position_rows: list[dict[str, str]] | None = None,
) -> str:
    if not market_rows and not position_rows:
        return "# Prediction Sheet\n\nNo rows found.\n"

    position_rows = position_rows or []
    all_rows = market_rows or position_rows
    sport = str(all_rows[0].get("sport") or "").strip().upper()

    sections: list[str] = [
        f"# {sport} Prediction Sheet",
        "",
        _summary_line(market_rows, position_rows),
        "",
        "`Selection+Rax` is the side plus the recommended buy size, for example `DET10000` or `YRFI10000`. A `0` means pass at the current Real price.",
        "`Action` in the open positions table is the current hold-vs-cashout recommendation.",
        "",
    ]
    sections.extend(render_prediction_sections(market_rows, position_rows, heading_level=2))
    sections.extend(_explainer())
    return "\n".join(sections).rstrip() + "\n"


def main() -> None:
    args = parse_args()
    input_path = Path(args.input)
    market_rows = _load_rows(input_path)
    position_rows = _load_position_rows(input_path, args.positions_input, market_rows)
    output_path = Path(args.output) if args.output else _default_output(market_rows or position_rows)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_prediction_sheet(market_rows, position_rows), encoding="utf8")
    print(f"Saved prediction sheet to {output_path}")


if __name__ == "__main__":
    main()
