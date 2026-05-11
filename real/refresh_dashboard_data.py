from __future__ import annotations

import argparse
import csv
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from market_csv import dedupe_market_rows, write_market_rows
from render_prediction_sheet import prediction_summary_line, render_prediction_sections
from render_vote_sheet import (
    _compact_table_row,
    _format_game_time,
    _poll_sort_key,
    _section_game_label,
    _summary_line,
)


BASE_DIR = Path(__file__).resolve().parent
ROOT_DIR = BASE_DIR.parent
OUTPUT_DIR = BASE_DIR / "output" / "dashboard"
DEFAULT_MARKETS_CSV = BASE_DIR / "sportsbook_markets_consensus_live.csv"
DEFAULT_SOCCER_MARKETS_CSV = BASE_DIR / "sportsbook_markets_soccer_live.csv"
LIVE_POLL_MARKETS_CSV = BASE_DIR / "sportsbook_markets_live_polls.csv"
LIVE_POLL_RECOMMENDATIONS_CSV = BASE_DIR / "live_poll_vote_recommendations.csv"
PREDICTION_SPORTS = {"mlb", "nba", "nhl", "soccer"}
PREDICTION_SPORT_ORDER = ("mlb", "nba", "nhl", "soccer")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Refresh stable markdown files for the local HTML dashboard without "
            "creating a new versioned output file on every cycle."
        )
    )
    parser.add_argument(
        "--sports",
        default="mlb,nba,nhl,wnba",
        help="Comma-separated sports to refresh, for example mlb,nba,nhl,wnba.",
    )
    parser.add_argument(
        "--refresh-soccer",
        action="store_true",
        help="Also refresh soccer using the DraftKings-only soccer market pull.",
    )
    parser.add_argument(
        "--markets-csv",
        default=str(DEFAULT_MARKETS_CSV),
        help="Consensus sportsbook CSV for mlb/nba/nhl refreshes.",
    )
    parser.add_argument(
        "--soccer-markets-csv",
        default=str(DEFAULT_SOCCER_MARKETS_CSV),
        help="Soccer sportsbook CSV path when soccer is refreshed.",
    )
    parser.add_argument(
        "--season",
        default="2025",
        help="Season value passed through to lineup.py.",
    )
    parser.add_argument(
        "--dashboard-dir",
        default=str(OUTPUT_DIR),
        help="Stable dashboard markdown output directory.",
    )
    parser.add_argument(
        "--only-live-polls",
        action="store_true",
        help="Refresh only the live poll recommendation dashboard sheet.",
    )
    parser.add_argument(
        "--only-predictions",
        action="store_true",
        help=(
            "Refresh only prediction recommendation sheets using game-line "
            "sportsbook markets."
        ),
    )
    parser.add_argument(
        "--skip-live-polls",
        action="store_true",
        help="Do not refresh the live-poll dashboard sheet after pre-game sport sheets.",
    )
    return parser.parse_args()


def _normalize_sports_arg(value: str) -> list[str]:
    seen: set[str] = set()
    sports: list[str] = []
    for item in str(value or "").split(","):
        sport = item.strip().lower()
        if not sport or sport in seen:
            continue
        seen.add(sport)
        sports.append(sport)
    return sports


def _run_step(command: list[str]) -> None:
    print(">>", " ".join(command), flush=True)
    subprocess.run(command, check=True, cwd=str(ROOT_DIR))


def _try_run_step(command: list[str]) -> bool:
    try:
        _run_step(command)
    except subprocess.CalledProcessError as exc:
        print(f"Warning: command failed with exit code {exc.returncode}: {' '.join(command)}", flush=True)
        return False
    return True


def _load_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    if csv.field_size_limit() < 10_000_000:
        csv.field_size_limit(10_000_000)
    with path.open("r", encoding="utf8", newline="") as handle:
        return list(csv.DictReader(handle))


def _first_csv_row(path: Path) -> dict[str, str] | None:
    if not path.exists():
        return None
    if csv.field_size_limit() < 10_000_000:
        csv.field_size_limit(10_000_000)
    with path.open("r", encoding="utf8", newline="") as handle:
        reader = csv.DictReader(handle)
        return next(reader, None)


def _table_escape(value: object) -> str:
    return str(value if value is not None else "").replace("|", "\\|").replace("\n", " ").strip()


def _dashboard_timestamp() -> str:
    return datetime.now(timezone.utc).astimezone(timezone.utc).isoformat()


def _parse_utc_datetime(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except Exception:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _csv_bool(value: object) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y"}


def _is_open_live_poll_row(row: dict[str, str], now: datetime) -> bool:
    if _csv_bool(row.get("is_locked")):
        return False
    locks_at = _parse_utc_datetime(row.get("locks_at"))
    if locks_at is not None and locks_at <= now:
        return False
    return True


def _compact_text(value: object, *, max_len: int = 120) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= max_len:
        return text
    return text[: max(0, max_len - 3)].rstrip() + "..."


def _game_label(row: dict[str, str]) -> str:
    display = str(row.get("game_display") or "").strip()
    if display:
        return display
    away = str(row.get("away_team") or "").strip()
    home = str(row.get("home_team") or "").strip()
    if away and home:
        return f"{away} @ {home}"
    return str(row.get("game_id") or "").strip()


def _selection_with_amount(row: dict[str, str], *, amount_field: str) -> str:
    selection = str(row.get("recommended_option") or row.get("best_outcome") or "").strip()
    amount = str(row.get(amount_field) or "").strip()
    if not selection:
        return f"Skip {amount}".strip()
    return f"{selection} {amount}".strip()


def _live_prediction_section_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    labeled_rows: list[dict[str, str]] = []
    for row in rows:
        labeled = dict(row)
        sport = str(row.get("sport") or "").strip().upper()
        game = _game_label(row)
        if sport and game:
            labeled["game_display"] = f"{sport} - {game}"
        elif game:
            labeled["game_display"] = game
        elif sport:
            labeled["game_display"] = sport
        labeled_rows.append(labeled)
    return labeled_rows


def _render_live_poll_sheet(rows: list[dict[str, str]]) -> str:
    def live_sort_key(row: dict[str, str]) -> tuple[str, int, str, str, tuple[int, int, int, int, str], str]:
        return (
            str(row.get("locks_at") or row.get("poll_created_at") or row.get("created_at") or "").strip(),
            _safe_sort_int(row.get("feed_order")),
            str(row.get("sport") or "").strip().lower(),
            _section_game_label(row),
            _poll_sort_key(row),
            str(row.get("poll_id") or "").strip(),
        )

    now = datetime.now(timezone.utc)
    rows = [row for row in rows if _is_open_live_poll_row(row, now)]
    rows = sorted(rows, key=live_sort_key)
    sections = [
        "# Live Poll Recommendations",
        "",
        f"**Updated:** {_dashboard_timestamp()}",
        "",
        _summary_line(rows),
        "",
        "`Selection+Put` is the side plus the amount to enter, for example `No0`, `No50`, `Yes0`, or `Yes50`. Zero-cost pick rows just show the selection.",
        "",
    ]
    if not rows:
        sections.append("No live poll rows found.")
        return "\n".join(sections).rstrip() + "\n"

    grouped: dict[tuple[str, str, str, str, str, str], list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        key = (
            str(row.get("sport") or "").strip().lower(),
            str(row.get("game_time") or "").strip(),
            _section_game_label(row),
            str(row.get("away_team") or "").strip(),
            str(row.get("home_team") or "").strip(),
            str(row.get("game_id") or "").strip(),
        )
        grouped[key].append(row)

    for key, game_rows in grouped.items():
        sport, game_time, _label, _away_team, _home_team, _game_id = key
        game_label = _section_game_label(game_rows[0])
        heading = f"{sport.upper()} - {game_label}" if sport else game_label
        sections.append(f"## {heading}")
        sections.append(f"`{_format_game_time(game_time)}`")
        sections.append("")
        sections.append("| Poll | Selection+Put | Consensus Prob (Odds) | EV | Sportsbook Odds | Source |")
        sections.append("| --- | --- | --- | --- | --- | --- |")
        sections.extend(_compact_table_row(row) for row in sorted(game_rows, key=live_sort_key))
        sections.append("")
    return "\n".join(sections).rstrip() + "\n"


def _render_live_prediction_sheet(
    market_rows: list[dict[str, str]],
    position_rows: list[dict[str, str]],
) -> str:
    sections = [
        "# Live Prediction Markets",
        "",
        f"**Updated:** {_dashboard_timestamp()}",
        "",
        prediction_summary_line(market_rows, position_rows),
        "",
        "`Selection+Rax` is the side plus the recommended buy size, for example `DET10000` or `YRFI10000`. A `0` means pass at the current Real price.",
        "`Action` in the open positions table is the current hold-vs-cashout recommendation.",
        "",
    ]

    if not market_rows and not position_rows:
        sections.append("No prediction-market rows found.")
        return "\n".join(sections).rstrip() + "\n"

    sections.extend(
        render_prediction_sections(
            _live_prediction_section_rows(market_rows),
            _live_prediction_section_rows(position_rows),
            heading_level=2,
        )
    )
    return "\n".join(sections).rstrip() + "\n"


def _refresh_core_markets(
    sports: list[str],
    markets_csv: Path,
    *,
    market_scope: str = "all",
) -> None:
    if not sports:
        return
    scope_key = str(market_scope or "all").strip().lower()
    print(
        f"Refreshing sportsbook markets for {', '.join(sport.upper() for sport in sports)} "
        f"({scope_key}).",
        flush=True,
    )
    dump_name = "dashboard_consensus_live_check"
    if scope_key != "all":
        dump_name = f"dashboard_consensus_{scope_key.replace('-', '_')}_live_check"
    command = [
        sys.executable,
        "-B",
        str(BASE_DIR / "ingest_public_markets.py"),
        "--providers",
        "draftkings,fanduel",
        "--sports",
        ",".join(sports),
        "--force-live",
        "--output",
        str(markets_csv),
        "--dump-json-dir",
        str(BASE_DIR / "tmp" / dump_name),
    ]
    if scope_key != "all":
        command.extend(["--market-scope", scope_key])
    _run_step(command)


def _refresh_soccer_markets(
    soccer_markets_csv: Path,
    *,
    market_scope: str = "all",
) -> None:
    scope_key = str(market_scope or "all").strip().lower()
    print(f"Refreshing soccer sportsbook markets ({scope_key}).", flush=True)
    dump_name = "dashboard_soccer_live_check"
    if scope_key != "all":
        dump_name = f"dashboard_soccer_{scope_key.replace('-', '_')}_live_check"
    command = [
        sys.executable,
        "-B",
        str(BASE_DIR / "ingest_public_markets.py"),
        "--providers",
        "draftkings,fanduel",
        "--sports",
        "soccer",
        "--output",
        str(soccer_markets_csv),
        "--dump-json-dir",
        str(BASE_DIR / "tmp" / dump_name),
    ]
    if scope_key != "all":
        command.extend(["--market-scope", scope_key, "--force-live"])
    if _try_run_step(command):
        return
    if soccer_markets_csv.exists():
        print(
            "Warning: soccer market refresh failed; continuing with existing "
            f"{soccer_markets_csv}.",
            flush=True,
        )
        return
    raise RuntimeError("Soccer market refresh failed and no existing soccer market CSV is available.")


def _refresh_sport(
    sport: str,
    *,
    season: str,
    dashboard_dir: Path,
    markets_csv: Path,
) -> None:
    recommendation_csv = BASE_DIR / f"poll_vote_recommendations_consensus_{sport}.csv"
    print(f"Refreshing {sport.upper()} vote recommendations.", flush=True)
    _run_step(
        [
            sys.executable,
            "-B",
            str(BASE_DIR / "recommend_game_feed_polls.py"),
            "--sport",
            sport,
            "--markets-csv",
            str(markets_csv),
            "--output",
            str(recommendation_csv),
        ]
    )

    first_row = _first_csv_row(recommendation_csv)
    day_value = str((first_row or {}).get("day") or "").strip()
    if day_value:
        print(f"Refreshing {sport.upper()} lineup context for {day_value}.", flush=True)
        lineup_ok = _try_run_step(
            [
                sys.executable,
                "-B",
                str(BASE_DIR / "lineup.py"),
                "--sport",
                sport,
                "--date",
                day_value,
                "--season",
                str(season),
            ]
        )
        if not lineup_ok:
            print(
                f"Warning: lineup context refresh failed for {sport.upper()} {day_value}; "
                "continuing with vote sheet render.",
                flush=True,
            )

    render_command = [
        sys.executable,
        "-B",
        str(BASE_DIR / "render_vote_sheet.py"),
        "--input",
        str(recommendation_csv),
        "--output",
        str(dashboard_dir / f"{sport}.md"),
        "--not-started-only",
    ]
    if sport in PREDICTION_SPORTS:
        render_command.extend(
            [
                "--refresh-predictions",
                "--prediction-markets-csv",
                str(markets_csv),
            ]
        )
    print(f"Rendering {sport.upper()} vote sheet.", flush=True)
    _run_step(render_command)

    if sport not in PREDICTION_SPORTS:
        return

    prediction_market_csv = BASE_DIR / f"prediction_market_recommendations_{sport}.csv"
    prediction_position_csv = BASE_DIR / f"prediction_position_recommendations_{sport}.csv"
    if prediction_market_csv.exists():
        print(f"Rendering {sport.upper()} prediction sheet.", flush=True)
        _run_step(
            [
                sys.executable,
                "-B",
                str(BASE_DIR / "render_prediction_sheet.py"),
                "--input",
                str(prediction_market_csv),
                "--positions-input",
                str(prediction_position_csv),
                "--output",
                str(dashboard_dir / f"{sport}_predictions.md"),
            ]
        )


def _refresh_prediction_sport(
    sport: str,
    *,
    dashboard_dir: Path,
    markets_csv: Path,
) -> None:
    prediction_market_csv = BASE_DIR / f"prediction_market_recommendations_{sport}.csv"
    prediction_position_csv = BASE_DIR / f"prediction_position_recommendations_{sport}.csv"
    print(f"Refreshing {sport.upper()} prediction markets.", flush=True)
    _run_step(
        [
            sys.executable,
            "-B",
            str(BASE_DIR / "recommend_prediction_markets.py"),
            "--sport",
            sport,
            "--markets-csv",
            str(markets_csv),
            "--output",
            str(prediction_market_csv),
        ]
    )
    print(f"Refreshing {sport.upper()} prediction open positions.", flush=True)
    _run_step(
        [
            sys.executable,
            "-B",
            str(BASE_DIR / "recommend_prediction_positions.py"),
            "--sport",
            sport,
            "--markets-csv",
            str(markets_csv),
            "--output",
            str(prediction_position_csv),
        ]
    )
    print(f"Rendering {sport.upper()} prediction sheet.", flush=True)
    _run_step(
        [
            sys.executable,
            "-B",
            str(BASE_DIR / "render_prediction_sheet.py"),
            "--input",
            str(prediction_market_csv),
            "--positions-input",
            str(prediction_position_csv),
            "--output",
            str(dashboard_dir / f"{sport}_predictions.md"),
        ]
    )


def _safe_positive_int(value: object) -> bool:
    try:
        return int(float(str(value or "0"))) > 0
    except Exception:
        return False


def _safe_sort_int(value: object, default: int = 999999) -> int:
    try:
        return int(float(str(value or "").strip()))
    except Exception:
        return default


def _combined_live_markets_csv(markets_csv: Path, soccer_markets_csv: Path | None = None) -> Path:
    source_paths = [
        path
        for path in (markets_csv, soccer_markets_csv)
        if path is not None and path.exists()
    ]
    if not source_paths:
        if LIVE_POLL_MARKETS_CSV.exists():
            return LIVE_POLL_MARKETS_CSV
        return markets_csv

    if LIVE_POLL_MARKETS_CSV.exists():
        live_mtime = LIVE_POLL_MARKETS_CSV.stat().st_mtime
        if all(path.stat().st_mtime <= live_mtime for path in source_paths):
            return LIVE_POLL_MARKETS_CSV

    rows: list[dict[str, object]] = []
    for path in source_paths:
        rows.extend(_load_csv_rows(path))
    if rows:
        write_market_rows(LIVE_POLL_MARKETS_CSV, dedupe_market_rows(rows), append=False)
        return LIVE_POLL_MARKETS_CSV
    return markets_csv


def _refresh_live_poll_dashboard(
    markets_csv: Path,
    dashboard_dir: Path,
    *,
    soccer_markets_csv: Path | None = None,
) -> None:
    live_markets_csv = _combined_live_markets_csv(markets_csv, soccer_markets_csv)
    print("Refreshing live poll recommendations (targeted sportsbook refresh).", flush=True)
    refreshed = _try_run_step(
        [
            sys.executable,
            "-B",
            str(BASE_DIR / "recommend_live_polls.py"),
            "--markets-csv",
            str(live_markets_csv),
            "--refresh-markets",
            "--providers",
            "draftkings,fanduel",
            "--sports",
            "mlb,nba,nhl,wnba,soccer",
            "--min-market-refresh-interval-seconds",
            "900",
            "--output",
            str(LIVE_POLL_RECOMMENDATIONS_CSV),
            "--history-jsonl",
            "",
            "--iterations",
            "1",
            "--unlocked-only",
        ]
    )
    if not refreshed:
        live_markets_csv = (
            LIVE_POLL_MARKETS_CSV
            if LIVE_POLL_MARKETS_CSV.exists()
            else _combined_live_markets_csv(markets_csv, soccer_markets_csv)
        )
        _run_step(
            [
                sys.executable,
                "-B",
                str(BASE_DIR / "recommend_live_polls.py"),
                "--markets-csv",
                str(live_markets_csv),
                "--output",
                str(LIVE_POLL_RECOMMENDATIONS_CSV),
                "--history-jsonl",
                "",
                "--iterations",
                "1",
                "--unlocked-only",
            ]
        )
    rows = _load_csv_rows(LIVE_POLL_RECOMMENDATIONS_CSV)
    output_path = dashboard_dir / "live_polls.md"
    output_path.write_text(_render_live_poll_sheet(rows), encoding="utf8")
    print(f"Saved live poll dashboard sheet to {output_path}", flush=True)


def _write_live_prediction_dashboard(dashboard_dir: Path) -> None:
    market_rows: list[dict[str, str]] = []
    position_rows: list[dict[str, str]] = []
    for sport in PREDICTION_SPORT_ORDER:
        market_rows.extend(_load_csv_rows(BASE_DIR / f"prediction_market_recommendations_{sport}.csv"))
        position_rows.extend(_load_csv_rows(BASE_DIR / f"prediction_position_recommendations_{sport}.csv"))
    market_rows.sort(
        key=lambda row: (
            str(row.get("sport") or ""),
            _safe_sort_int(row.get("game_order")),
            str(row.get("game_time") or ""),
            str(row.get("game_display") or ""),
            str(row.get("market_type") or ""),
        )
    )
    position_rows.sort(
        key=lambda row: (
            str(row.get("sport") or ""),
            _safe_sort_int(row.get("game_order")),
            str(row.get("game_time") or ""),
            str(row.get("game_display") or ""),
            str(row.get("market_type") or ""),
        )
    )
    output_path = dashboard_dir / "live_predictions.md"
    output_path.write_text(_render_live_prediction_sheet(market_rows, position_rows), encoding="utf8")
    print(f"Saved live prediction dashboard sheet to {output_path}", flush=True)


def main() -> int:
    args = parse_args()
    dashboard_dir = Path(args.dashboard_dir)
    dashboard_dir.mkdir(parents=True, exist_ok=True)

    requested_sports = _normalize_sports_arg(args.sports)
    core_sports = [sport for sport in requested_sports if sport != "soccer"]
    refresh_soccer = args.refresh_soccer or ("soccer" in requested_sports)

    markets_csv = Path(args.markets_csv)
    soccer_markets_csv = Path(args.soccer_markets_csv)

    if args.only_live_polls:
        _refresh_live_poll_dashboard(
            markets_csv,
            dashboard_dir,
            soccer_markets_csv=soccer_markets_csv,
        )
        print("", flush=True)
        print("Stable dashboard files:", flush=True)
        for path in sorted(dashboard_dir.glob("live_polls.md")):
            print(path, flush=True)
        return 0

    if args.only_predictions:
        prediction_core_sports = [
            sport for sport in core_sports if sport in PREDICTION_SPORTS
        ]
        _refresh_core_markets(
            prediction_core_sports,
            markets_csv,
            market_scope="game-lines",
        )
        if refresh_soccer:
            _refresh_soccer_markets(soccer_markets_csv, market_scope="game-lines")

        for sport in prediction_core_sports:
            _refresh_prediction_sport(
                sport,
                dashboard_dir=dashboard_dir,
                markets_csv=markets_csv,
            )

        if refresh_soccer:
            _refresh_prediction_sport(
                "soccer",
                dashboard_dir=dashboard_dir,
                markets_csv=soccer_markets_csv,
            )

        _write_live_prediction_dashboard(dashboard_dir)
        print("", flush=True)
        print("Stable prediction dashboard files:", flush=True)
        for path in sorted(dashboard_dir.glob("*predictions.md")):
            print(path, flush=True)
        return 0

    _refresh_core_markets(core_sports, markets_csv)
    if refresh_soccer:
        _refresh_soccer_markets(soccer_markets_csv)

    for sport in core_sports:
        _refresh_sport(
            sport,
            season=str(args.season),
            dashboard_dir=dashboard_dir,
            markets_csv=markets_csv,
        )

    if refresh_soccer:
        _refresh_sport(
            "soccer",
            season=str(args.season),
            dashboard_dir=dashboard_dir,
            markets_csv=soccer_markets_csv,
        )

    if not args.skip_live_polls:
        _refresh_live_poll_dashboard(
            markets_csv,
            dashboard_dir,
            soccer_markets_csv=soccer_markets_csv,
        )
    _write_live_prediction_dashboard(dashboard_dir)

    print("", flush=True)
    print("Stable dashboard files:", flush=True)
    for path in sorted(dashboard_dir.glob("*.md")):
        print(path, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
