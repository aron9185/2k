from __future__ import annotations

import argparse
import csv
import subprocess
import sys
from collections import OrderedDict
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

from pull_nba_stats import EXPORT_DIR, MANUAL_DIR


SHOOTING_BUILDS: Sequence[Dict[str, str]] = [
    {
        "script": "build_shooting_close_shot.py",
        "output_prefix": "shooting_close_shot",
        "rating_column": "Close Shot Rating",
    },
    {
        "script": "build_shooting_mid_range.py",
        "output_prefix": "shooting_mid_range",
        "rating_column": "Mid-Range Shot Rating",
    },
    {
        "script": "build_shooting_three_point.py",
        "output_prefix": "shooting_three_point",
        "rating_column": "3-Point Shot Rating",
    },
    {
        "script": "build_shooting_free_throw.py",
        "output_prefix": "shooting_free_throw",
        "rating_column": "Free Throw Rating",
    },
    {
        "script": "build_shooting_shot_iq.py",
        "output_prefix": "shooting_shot_iq",
        "rating_column": "Shot IQ Rating",
    },
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Rebuild all Shooting sub-ratings and merge them into one CSV."
    )
    parser.add_argument(
        "--skip-build",
        action="store_true",
        help="Skip rerunning the individual shooting builders and only merge existing rating-only CSVs.",
    )
    parser.add_argument(
        "--universe-csv",
        default=str(MANUAL_DIR / "playerlist.csv"),
        help="Season/player universe CSV forwarded to each builder.",
    )
    parser.add_argument(
        "--workbook",
        default=str(MANUAL_DIR / "2k26_Temp_for_codex.xlsx"),
        help="Workbook fallback forwarded to each builder.",
    )
    parser.add_argument(
        "--sheet",
        default="Cal",
        help="Workbook sheet name forwarded to each builder.",
    )
    parser.add_argument(
        "--details-csv",
        default="",
        help=(
            "Optional details CSV forwarded to each builder. "
            "Pass stats/manual/player_universe.csv explicitly if you want that enrichment."
        ),
    )
    parser.add_argument(
        "--current-season",
        default="",
        help="Current season override forwarded to each builder.",
    )
    parser.add_argument(
        "--current-season-min-threshold",
        type=float,
        default=200.0,
        help="Current-season minute threshold forwarded to each builder.",
    )
    parser.add_argument(
        "--standard-min-threshold",
        type=float,
        default=1000.0,
        help="Completed-season minute threshold forwarded to each builder.",
    )
    parser.add_argument(
        "--allow-id-fallback",
        action="store_true",
        help="Allow season+NBA_ID fallback when normalized season+player matching fails.",
    )
    parser.add_argument(
        "--combined-prefix",
        default="shooting_all",
        help="Prefix used for the merged output CSVs inside stats/exports.",
    )
    parser.add_argument(
        "--lane-prefix-suffix",
        default="",
        help="Optional suffix appended to each individual lane output prefix before building and merging.",
    )
    return parser.parse_args()


def build_forwarded_args(args: argparse.Namespace) -> List[str]:
    forwarded = [
        "--universe-csv",
        args.universe_csv,
        "--workbook",
        args.workbook,
        "--sheet",
        args.sheet,
        "--current-season-min-threshold",
        str(args.current_season_min_threshold),
        "--standard-min-threshold",
        str(args.standard_min_threshold),
    ]
    if args.details_csv:
        forwarded.extend(["--details-csv", args.details_csv])
    if args.current_season:
        forwarded.extend(["--current-season", args.current_season])
    if args.allow_id_fallback:
        forwarded.append("--allow-id-fallback")
    return forwarded


def run_shooting_builds(args: argparse.Namespace) -> None:
    script_dir = Path(__file__).resolve().parent
    forwarded_args = build_forwarded_args(args)
    lane_prefix_suffix = args.lane_prefix_suffix.strip()

    for build in SHOOTING_BUILDS:
        script_path = script_dir / build["script"]
        output_prefix = f"{build['output_prefix']}{lane_prefix_suffix}"
        command = [
            sys.executable,
            str(script_path),
            "--output-prefix",
            output_prefix,
            *forwarded_args,
        ]
        print(f"[RUN] {script_path.name} -> {output_prefix}")
        subprocess.run(command, check=True)


def read_csv_rows(path: Path) -> List[Dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8") as fh:
        return [dict(row) for row in csv.DictReader(fh)]


def merge_rating_rows(lane_prefix_suffix: str = "") -> Tuple[List[str], List[Dict[str, object]]]:
    merged_rows: "OrderedDict[Tuple[str, str, str], Dict[str, object]]" = OrderedDict()

    for build in SHOOTING_BUILDS:
        rating_path = EXPORT_DIR / f"{build['output_prefix']}{lane_prefix_suffix}_rating_only.csv"
        if not rating_path.exists():
            raise SystemExit(f"Missing rating-only CSV: {rating_path}")

        for row in read_csv_rows(rating_path):
            key = (
                str(row.get("NBA_ID", "")).strip(),
                str(row.get("Season", "")).strip(),
                str(row.get("Player", "")).strip(),
            )
            current = merged_rows.setdefault(
                key,
                {
                    "NBA_ID": key[0],
                    "Season": key[1],
                    "Player": key[2],
                },
            )
            current[build["rating_column"]] = row.get(build["rating_column"], "")

    rating_columns = [build["rating_column"] for build in SHOOTING_BUILDS]
    headers = [
        "NBA_ID",
        "Season",
        "Player",
        *rating_columns,
        "AvailableRatings",
        "MissingRatings",
    ]

    final_rows: List[Dict[str, object]] = []
    for row in merged_rows.values():
        missing = [
            column for column in rating_columns if str(row.get(column, "")).strip() == ""
        ]
        row["AvailableRatings"] = len(rating_columns) - len(missing)
        row["MissingRatings"] = " | ".join(missing)
        final_rows.append(row)

    return headers, final_rows


def write_csv(path: Path, headers: Sequence[str], rows: Sequence[Dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(headers), extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({header: row.get(header, "") for header in headers})


def main() -> None:
    args = parse_args()
    lane_prefix_suffix = args.lane_prefix_suffix.strip()

    if not args.skip_build:
        run_shooting_builds(args)

    headers, merged_rows = merge_rating_rows(lane_prefix_suffix=lane_prefix_suffix)
    combined_prefix = args.combined_prefix.strip() or "shooting_all"
    combined_path = EXPORT_DIR / f"{combined_prefix}_ratings.csv"
    write_csv(combined_path, headers, merged_rows)

    print(f"[OK] Built merged Shooting ratings for {len(merged_rows)} player-season rows")
    print(f"[OUT] Combined ratings -> {combined_path}")


if __name__ == "__main__":
    main()
