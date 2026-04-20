from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from market_csv import dedupe_market_rows, write_market_rows
from provider_draftkings import fetch_rows as fetch_draftkings_rows
from provider_fanduel import fetch_rows as fetch_fanduel_rows
from provider_kalshi import fetch_rows as fetch_kalshi_rows
from provider_polymarket import fetch_rows as fetch_polymarket_rows


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT = BASE_DIR / "sportsbook_markets.csv"


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Ingest normalized market rows from sportsbook and prediction-market "
            "providers into real/sportsbook_markets.csv."
        )
    )
    parser.add_argument(
        "--providers",
        default="kalshi",
        help="Comma-separated provider list. Supported: kalshi, polymarket, draftkings, fanduel.",
    )
    parser.add_argument(
        "--sports",
        default="nba,mlb,nhl,nfl,wnba",
        help="Comma-separated sports to pull, e.g. nba,mlb,nhl.",
    )
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--append", action="store_true")
    parser.add_argument("--limit-per-provider", type=int, default=0)
    parser.add_argument("--kalshi-page-limit", type=int, default=10)
    parser.add_argument("--polymarket-page-limit", type=int, default=3)
    parser.add_argument(
        "--exclude-game-winner",
        action="store_true",
        help="Skip normalized game-winner rows from Kalshi.",
    )
    parser.add_argument(
        "--dump-json-dir",
        default="",
        help="Optional directory to dump raw provider payloads for inspection.",
    )
    return parser.parse_args()


def _parse_csv_arg(value: str) -> list[str]:
    return [part.strip() for part in str(value or "").split(",") if part.strip()]


def _write_json_dump(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf8")


def main():
    args = parse_args()
    providers = _parse_csv_arg(args.providers)
    sports = _parse_csv_arg(args.sports)

    provider_rows: list[dict[str, Any]] = []
    raw_payloads: dict[str, Any] = {}
    counts_by_provider: Counter[str] = Counter()
    counts_by_market_type: Counter[str] = Counter()

    for provider in providers:
        provider_key = provider.lower()
        if provider_key == "kalshi":
            rows, raw = fetch_kalshi_rows(
                sports,
                page_limit=args.kalshi_page_limit,
                max_rows=args.limit_per_provider,
                include_game_winner=not args.exclude_game_winner,
            )
        elif provider_key == "polymarket":
            rows, raw = fetch_polymarket_rows(
                sports,
                page_limit=args.polymarket_page_limit,
                max_rows=args.limit_per_provider,
            )
        elif provider_key == "draftkings":
            rows, raw = fetch_draftkings_rows(sports)
        elif provider_key == "fanduel":
            rows, raw = fetch_fanduel_rows(sports)
        else:
            raise SystemExit(f"Unsupported provider: {provider}")

        raw_payloads[provider_key] = raw
        provider_rows.extend(rows)
        counts_by_provider[provider_key] += len(rows)
        counts_by_market_type.update(str(row.get("market_type") or "") for row in rows)

    deduped_rows = dedupe_market_rows(provider_rows)
    written = write_market_rows(args.output, deduped_rows, append=args.append)

    print(f"Saved {written} normalized public-market rows to {args.output}")
    for provider, count in sorted(counts_by_provider.items()):
        print(f"  {provider}: {count} row(s)")
    if counts_by_market_type:
        print("  market types:")
        for market_type, count in sorted(counts_by_market_type.items()):
            print(f"    {market_type}: {count}")

    if args.dump_json_dir:
        dump_dir = Path(args.dump_json_dir)
        for provider, payload in raw_payloads.items():
            _write_json_dump(dump_dir / f"{provider}.json", payload)
        print(f"Saved raw provider payloads to {dump_dir}")


if __name__ == "__main__":
    main()
