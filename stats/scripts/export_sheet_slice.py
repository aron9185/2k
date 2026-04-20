from __future__ import annotations

import argparse
import csv
from pathlib import Path
from typing import Dict, List, Tuple

from pull_nba_stats import EXPORT_DIR, HISTORY_DIR, build_jobs


def build_catalog() -> Dict[str, Dict[str, str]]:
    catalog: Dict[str, Dict[str, str]] = {}
    for job in build_jobs("2025-26"):
        if job.merge_strategy == "skip":
            continue
        history_name = job.history_name or job.output_name.replace("_tmp.csv", ".csv")
        catalog[job.name] = {
            "path": str(HISTORY_DIR / history_name),
            "value_mode": job.value_mode,
        }
    return catalog


def resolve_source(source: str, catalog: Dict[str, Dict[str, str]]) -> Tuple[str, Path, str]:
    if source in catalog:
        item = catalog[source]
        return source, Path(item["path"]), item["value_mode"]

    candidate = HISTORY_DIR / source
    if candidate.exists():
        return candidate.stem, candidate, "unknown"

    if not source.endswith(".csv"):
        candidate = HISTORY_DIR / f"{source}.csv"
        if candidate.exists():
            return candidate.stem, candidate, "unknown"

    raise SystemExit(f"Unknown source: {source}")


def read_rows(path: Path) -> Tuple[List[str], List[Dict[str, str]]]:
    with path.open("r", newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        headers = list(reader.fieldnames or [])
        rows = [dict(row) for row in reader]
    return headers, rows


def default_keys(headers: List[str]) -> List[str]:
    return [column for column in ["Season", "PLAYER_NAME"] if column in headers]


def build_default_output(source_name: str, season: str | None, columns: List[str]) -> Path:
    safe_columns = "_".join(columns[:4])
    season_part = season or "all"
    return EXPORT_DIR / f"{source_name}_{season_part}_{safe_columns}.csv"


def write_rows(path: Path, headers: List[str], rows: List[Dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=headers, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({header: row.get(header, "") for header in headers})


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export paste-ready slices from stats history CSVs for the Google Sheet workflow."
    )
    parser.add_argument(
        "--list-sources",
        action="store_true",
        help="List the known history CSV sources and their value modes.",
    )
    parser.add_argument(
        "--source",
        help="Job name from pull_nba_stats.py or a CSV filename inside stats/history/.",
    )
    parser.add_argument(
        "--columns",
        nargs="*",
        default=[],
        help="Columns to export alongside the default keys.",
    )
    parser.add_argument(
        "--season",
        help="Optional season filter, e.g. 2025-26.",
    )
    parser.add_argument(
        "--keys",
        nargs="*",
        help="Override the default key columns. Defaults to the columns present from Season and PLAYER_NAME.",
    )
    parser.add_argument(
        "--out",
        help="Optional output CSV path. Defaults to stats/exports/.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    catalog = build_catalog()

    if args.list_sources:
        for name in sorted(catalog):
            item = catalog[name]
            print(f"{name}\t{Path(item['path']).name}\t{item['value_mode']}")
        return

    if not args.source:
        raise SystemExit("--source is required unless --list-sources is used")
    if not args.columns:
        raise SystemExit("--columns is required when exporting a slice")

    source_name, csv_path, value_mode = resolve_source(args.source, catalog)
    headers, rows = read_rows(csv_path)
    if not headers:
        raise SystemExit(f"No headers found in {csv_path.name}")

    keys = list(args.keys) if args.keys else default_keys(headers)
    missing = [column for column in [*keys, *args.columns] if column not in headers]
    if missing:
        raise SystemExit(
            f"Missing columns in {csv_path.name}: {', '.join(missing)}"
        )

    selected_rows = rows
    if args.season:
        if "Season" not in headers:
            raise SystemExit(f"{csv_path.name} has no Season column to filter on")
        selected_rows = [row for row in rows if row.get("Season") == args.season]

    output_headers = [*keys, *args.columns]
    output_rows = [
        {header: row.get(header, "") for header in output_headers}
        for row in selected_rows
    ]

    out_path = Path(args.out) if args.out else build_default_output(source_name, args.season, args.columns)
    write_rows(out_path, output_headers, output_rows)

    print(
        f"[OK] {source_name} -> {out_path} "
        f"({len(output_rows)} rows, mode={value_mode})"
    )


if __name__ == "__main__":
    main()
