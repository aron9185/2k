from __future__ import annotations

import argparse
import csv
import subprocess
import sys
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
ROOT_DIR = BASE_DIR.parent
OUTPUT_DIR = BASE_DIR / "output" / "dashboard"
DEFAULT_MARKETS_CSV = BASE_DIR / "sportsbook_markets_consensus_live.csv"
DEFAULT_SOCCER_MARKETS_CSV = BASE_DIR / "sportsbook_markets_soccer_live.csv"
PREDICTION_SPORTS = {"mlb", "nba", "nhl"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Refresh stable markdown files for the local HTML dashboard without "
            "creating a new versioned output file on every cycle."
        )
    )
    parser.add_argument(
        "--sports",
        default="mlb,nba,nhl",
        help="Comma-separated sports to refresh, for example mlb,nba,nhl.",
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
    print(">>", " ".join(command))
    subprocess.run(command, check=True, cwd=str(ROOT_DIR))


def _first_csv_row(path: Path) -> dict[str, str] | None:
    if not path.exists():
        return None
    with path.open("r", encoding="utf8", newline="") as handle:
        reader = csv.DictReader(handle)
        return next(reader, None)


def _refresh_core_markets(sports: list[str], markets_csv: Path) -> None:
    if not sports:
        return
    _run_step(
        [
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
            str(BASE_DIR / "tmp" / "dashboard_consensus_live_check"),
        ]
    )


def _refresh_soccer_markets(soccer_markets_csv: Path) -> None:
    _run_step(
        [
            sys.executable,
            "-B",
            str(BASE_DIR / "ingest_public_markets.py"),
            "--providers",
            "draftkings",
            "--sports",
            "soccer",
            "--force-live",
            "--output",
            str(soccer_markets_csv),
            "--dump-json-dir",
            str(BASE_DIR / "tmp" / "dashboard_soccer_live_check"),
        ]
    )


def _refresh_sport(
    sport: str,
    *,
    season: str,
    dashboard_dir: Path,
    markets_csv: Path,
) -> None:
    recommendation_csv = BASE_DIR / f"poll_vote_recommendations_consensus_{sport}.csv"
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
        _run_step(
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
        render_command.append("--refresh-predictions")
    _run_step(render_command)

    if sport not in PREDICTION_SPORTS:
        return

    prediction_market_csv = BASE_DIR / f"prediction_market_recommendations_{sport}.csv"
    prediction_position_csv = BASE_DIR / f"prediction_position_recommendations_{sport}.csv"
    if prediction_market_csv.exists():
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


def main() -> int:
    args = parse_args()
    dashboard_dir = Path(args.dashboard_dir)
    dashboard_dir.mkdir(parents=True, exist_ok=True)

    requested_sports = _normalize_sports_arg(args.sports)
    core_sports = [sport for sport in requested_sports if sport != "soccer"]
    refresh_soccer = args.refresh_soccer or ("soccer" in requested_sports)

    markets_csv = Path(args.markets_csv)
    soccer_markets_csv = Path(args.soccer_markets_csv)

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

    print("")
    print("Stable dashboard files:")
    for path in sorted(dashboard_dir.glob("*.md")):
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
