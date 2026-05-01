from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "output"
DEFAULT_INPUT = BASE_DIR / "prediction_position_recommendations.csv"
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
            "Render a game-by-game Real prediction hold/cashout sheet from "
            "real/prediction_position_recommendations.csv."
        )
    )
    parser.add_argument("--input", default=str(DEFAULT_INPUT))
    parser.add_argument("--output", default="")
    return parser.parse_args()


def _load_rows(path: str | Path) -> list[dict[str, str]]:
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


def _table_escape(value: object) -> str:
    text = str(value or "").replace("\n", " ").strip()
    return text.replace("|", "\\|")


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


def _summary_line(rows: list[dict[str, str]]) -> str:
    cashout_total = sum(_safe_float(str(row.get("cashout_now") or "")) or 0.0 for row in rows)
    hold_total = sum(_safe_float(str(row.get("hold_fair_value") or "")) or 0.0 for row in rows)
    return f"**Summary:** {len(rows)} open positions, current cashout {cashout_total:.2f} rax, fair hold value {hold_total:.2f} rax."


def _row_sort_key(row: dict[str, str]) -> tuple[datetime, str, str, int, str]:
    return (
        _parse_game_time(str(row.get("game_time") or "")) or datetime.max.replace(tzinfo=timezone.utc),
        str(row.get("game_display") or "").strip(),
        str(row.get("game_id") or "").strip(),
        MARKET_ORDER.get(str(row.get("market_type") or "").strip().lower(), 99),
        str(row.get("position_id") or "").strip(),
    )


def _default_output(rows: list[dict[str, str]]) -> Path:
    sport = str((rows[0].get("sport") or "sport")).strip().lower() if rows else "sport"
    prefix = f"{sport}_prediction_positions_v"
    version_numbers: list[int] = []
    for candidate in OUTPUT_DIR.glob(f"{sport}_prediction_positions_v*.md"):
        suffix = candidate.stem[len(prefix) :]
        if suffix.isdigit():
            version_numbers.append(int(suffix))
    next_version = (max(version_numbers) + 1) if version_numbers else 1
    return OUTPUT_DIR / f"{sport}_prediction_positions_v{next_version}.md"


def _market_cell(row: dict[str, str]) -> str:
    market = str(row.get("market_label") or "").strip()
    held = str(row.get("held_label") or "").strip()
    label = f"{market}: {held}" if held else market
    url = str(row.get("position_url") or "").strip()
    if url:
        return f"[{label}]({url})"
    return label


def _action_cell(row: dict[str, str]) -> str:
    action = str(row.get("recommended_action") or "").strip()
    sell_url = str(row.get("sell_url") or "").strip()
    if action == "CASHOUT" and sell_url:
        return f"[CASHOUT]({sell_url})"
    return action


def _explainer() -> list[str]:
    return [
        "## How Hold Vs Cashout Is Calculated",
        "",
        "- `Cashout Now` is the current Real position value from the open-positions feed.",
        "- `Hold Fair Value` is `sportsbook fair win probability * payout if the position settles as a win`.",
        "- `Hold-Cashout EV` is the incremental decision value from now: `Hold Fair Value - Cashout Now`.",
        "- `Hold Total EV` is measured against your original cost basis: `Hold Fair Value - Cost`.",
        "- `Cashout P/L` is `Cashout Now - Cost`.",
        "- The recommendation is `HOLD` when `Hold-Cashout EV` is positive, otherwise `CASHOUT` when Real allows cashout.",
        "- Current sizing and cashout values still come from Real's displayed values, so any hidden sell slippage is not modeled yet.",
        "",
    ]


def render_prediction_positions_sheet(rows: list[dict[str, str]]) -> str:
    if not rows:
        return "# Prediction Positions Sheet\n\nNo rows found.\n"

    sport = str(rows[0].get("sport") or "").strip().upper()
    grouped: dict[tuple[str, str, str], list[dict[str, str]]] = defaultdict(list)
    for row in sorted(rows, key=_row_sort_key):
        key = (
            str(row.get("game_time") or "").strip(),
            str(row.get("game_display") or "").strip(),
            str(row.get("game_id") or "").strip(),
        )
        grouped[key].append(row)

    sections: list[str] = [
        f"# {sport} Prediction Positions Sheet",
        "",
        _summary_line(rows),
        "",
        "`Action` is the current recommendation on the open position. `CASHOUT` links to the sell ticket when Real currently allows it.",
        "",
    ]
    for (game_time, game_display, _game_id), game_rows in grouped.items():
        sections.append(f"## {game_display}")
        if game_time:
            sections.append(f"`{_format_game_time(game_time)}`")
        sections.append("")
        sections.append("| Position | Cashout Now | Hold Fair Value | Hold-Cashout EV | Hold Total EV | Cashout P/L | Action |")
        sections.append("| --- | --- | --- | --- | --- | --- | --- |")
        for row in game_rows:
            cells = [
                _market_cell(row),
                _format_rax(str(row.get("cashout_now") or "")),
                _format_rax(str(row.get("hold_fair_value") or "")),
                _format_signed_rax(str(row.get("hold_vs_cashout_ev") or "")),
                _format_signed_rax(str(row.get("hold_total_ev") or "")),
                _format_signed_rax(str(row.get("cashout_total_pl") or "")),
                _action_cell(row),
            ]
            sections.append("| " + " | ".join(_table_escape(cell) for cell in cells) + " |")
        sections.append("")

    sections.extend(_explainer())
    return "\n".join(sections).rstrip() + "\n"


def main() -> None:
    args = parse_args()
    rows = _load_rows(args.input)
    output_path = Path(args.output) if args.output else _default_output(rows)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_prediction_positions_sheet(rows), encoding="utf8")
    print(f"Saved prediction positions sheet to {output_path}")


if __name__ == "__main__":
    main()
