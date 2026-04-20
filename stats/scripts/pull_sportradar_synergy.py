from __future__ import annotations

import argparse
import csv
import gzip
import json
import os
import re
import ssl
import time
import zlib
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

try:
    from urllib.parse import urlencode
    from urllib.error import HTTPError
    from urllib.error import URLError
    from urllib.request import Request, urlopen
except ImportError:
    from urllib import urlencode
    from urllib2 import HTTPError
    from urllib2 import URLError
    from urllib2 import Request, urlopen

from pull_nba_stats import HISTORY_DIR, TMP_DIR


API_ROOT = "https://api.sportradar.com"
API_KEY_ENV_VARS = ("SPORTRADAR_SYNERGY_API_KEY", "SPORTRADAR_API_KEY")
SSL_CONTEXT = ssl.create_default_context()
INSECURE_SSL_CONTEXT = ssl._create_unverified_context()
SYNERGY_HISTORY_DIR = HISTORY_DIR / "synergy"
SYNERGY_TMP_DIR = TMP_DIR / "synergy"
PLAY_TYPE_ALIASES = {
    "prballhandler": "PandRBallHandler",
    "prrollman": "PandRRollMan",
    "isolation": "ISO",
    "iso": "ISO",
    "spotup": "SpotUp",
    "postup": "PostUp",
    "offscreen": "OffScreen",
}


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", str(text or "").lower()).strip("_")
    return slug or "synergy"


def normalize_label(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(text or "").lower())


def canonical_play_type(play_type: str) -> str:
    cleaned = str(play_type or "").strip()
    if not cleaned:
        return cleaned
    return PLAY_TYPE_ALIASES.get(normalize_label(cleaned), cleaned)


def decode_payload(payload: bytes, content_encoding: str) -> str:
    encoding = (content_encoding or "").lower()
    if "gzip" in encoding or payload[:2] == b"\x1f\x8b":
        payload = gzip.decompress(payload)
    elif "deflate" in encoding:
        try:
            payload = zlib.decompress(payload)
        except zlib.error:
            payload = zlib.decompress(payload, -zlib.MAX_WBITS)
    return payload.decode("utf-8")


def load_api_key(explicit_key: str = "") -> str:
    if explicit_key.strip():
        return explicit_key.strip()
    for env_name in API_KEY_ENV_VARS:
        value = os.environ.get(env_name, "").strip()
        if value:
            return value
    raise SystemExit(
        "Sportradar API key not found. Set SPORTRADAR_SYNERGY_API_KEY or SPORTRADAR_API_KEY."
    )


def request_json(
    path: str,
    api_key: str,
    query_params: Optional[Dict[str, Any]] = None,
    timeout: int = 60,
    insecure: bool = False,
    retries: int = 0,
    retry_wait_seconds: float = 2.0,
) -> Dict[str, Any]:
    url = f"{API_ROOT}{path}"
    if query_params:
        filtered = {
            key: value
            for key, value in query_params.items()
            if value is not None and str(value).strip() != ""
        }
        if filtered:
            url = f"{url}?{urlencode(filtered, doseq=True)}"

    request = Request(url)
    request.add_header("accept", "application/json")
    request.add_header("accept-encoding", "gzip, deflate")
    request.add_header("x-api-key", api_key)

    context = INSECURE_SSL_CONTEXT if insecure else SSL_CONTEXT
    attempt = 0
    while True:
        try:
            response = urlopen(request, timeout=timeout, context=context)
            payload = decode_payload(response.read(), response.headers.get("Content-Encoding", ""))
            return json.loads(payload)
        except HTTPError as exc:
            error_payload = exc.read()
            error_text = decode_payload(error_payload, exc.headers.get("Content-Encoding", ""))
            if exc.code == 429 and attempt < retries:
                retry_after_header = str(exc.headers.get("Retry-After", "")).strip()
                wait_seconds = retry_wait_seconds * (attempt + 1)
                try:
                    if retry_after_header:
                        wait_seconds = max(wait_seconds, float(retry_after_header))
                except Exception:
                    pass
                print(
                    f"[WARN] Sportradar rate limit hit for {url}. "
                    f"Retrying in {wait_seconds:.1f}s ({attempt + 1}/{retries})."
                )
                time.sleep(wait_seconds)
                attempt += 1
                continue
            raise SystemExit(
                f"Sportradar request failed ({exc.code}) for {url}\n{error_text}"
            )
        except URLError as exc:
            if attempt < retries:
                wait_seconds = retry_wait_seconds * (attempt + 1)
                print(
                    f"[WARN] Sportradar request error for {url}: {exc}. "
                    f"Retrying in {wait_seconds:.1f}s ({attempt + 1}/{retries})."
                )
                time.sleep(wait_seconds)
                attempt += 1
                continue
            raise


def parse_key_value_pairs(raw_pairs: Sequence[str]) -> Dict[str, str]:
    parsed: Dict[str, str] = {}
    for raw_pair in raw_pairs:
        if "=" not in raw_pair:
            raise SystemExit(f"Expected KEY=VALUE for --extra-param, got: {raw_pair}")
        key, value = raw_pair.split("=", 1)
        key = key.strip()
        if not key:
            raise SystemExit(f"Expected KEY=VALUE for --extra-param, got: {raw_pair}")
        parsed[key] = value.strip()
    return parsed


def season_label_variants(label: str) -> List[str]:
    variants = {normalize_label(label)}
    match = re.fullmatch(r"(\d{4})[-/](\d{2})", str(label or "").strip())
    if match:
        start_year = match.group(1)
        end_short = match.group(2)
        century_prefix = start_year[:2]
        variants.add(normalize_label(f"{start_year}-{century_prefix}{end_short}"))

    match = re.fullmatch(r"(\d{4})[-/](\d{4})", str(label or "").strip())
    if match:
        variants.add(normalize_label(f"{match.group(1)}-{match.group(2)[2:]}"))

    return [variant for variant in variants if variant]


def flatten_value(
    value: Any,
    prefix: str = "",
    output: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    current = output if output is not None else {}

    if isinstance(value, dict):
        for key, child in value.items():
            child_key = f"{prefix}_{key}" if prefix else str(key)
            flatten_value(child, child_key, current)
        return current

    if isinstance(value, list):
        if value and all(not isinstance(item, (dict, list)) for item in value):
            current[prefix] = " | ".join("" if item is None else str(item) for item in value)
        else:
            current[prefix] = json.dumps(value, ensure_ascii=False, sort_keys=True)
        return current

    current[prefix] = value
    return current


def normalize_data_rows(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    raw_rows = payload.get("data", [])
    if isinstance(raw_rows, dict):
        raw_rows = [raw_rows]
    if not isinstance(raw_rows, list):
        return []

    normalized_rows: List[Dict[str, Any]] = []
    for item in raw_rows:
        if not isinstance(item, dict):
            normalized_rows.append({"value": item})
            continue

        row: Dict[str, Any] = {}
        if isinstance(item.get("data"), dict):
            flatten_value(item["data"], output=row)
            for key, value in item.items():
                if key == "data":
                    continue
                extra = flatten_value(value, key)
                for extra_key, extra_value in extra.items():
                    if extra_key in row:
                        row[f"wrapper_{extra_key}"] = extra_value
                    else:
                        row[extra_key] = extra_value
        else:
            flatten_value(item, output=row)
        normalized_rows.append(row)

    return normalized_rows


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2, sort_keys=True)


def write_csv(path: Path, rows: Sequence[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    headers: List[str] = []
    seen_headers = set()
    for row in rows:
        for key in row.keys():
            if key not in seen_headers:
                seen_headers.add(key)
                headers.append(key)

    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=headers)
        if headers:
            writer.writeheader()
            for row in rows:
                writer.writerow({header: row.get(header, "") for header in headers})


def fetch_seasons_payload(
    league: str,
    api_key: str,
    timeout: int,
    insecure: bool,
    retries: int,
    retry_wait_seconds: float,
) -> Dict[str, Any]:
    return request_json(
        path=f"/synergy/basketball/{league}/seasons",
        api_key=api_key,
        timeout=timeout,
        insecure=insecure,
        retries=retries,
        retry_wait_seconds=retry_wait_seconds,
    )


def resolve_season(
    league: str,
    api_key: str,
    timeout: int,
    insecure: bool,
    retries: int,
    retry_wait_seconds: float,
    season_id: str,
    season_label: str,
) -> Tuple[str, str, Dict[str, Any]]:
    if season_id.strip():
        return season_id.strip(), season_label.strip(), {}

    if not season_label.strip():
        raise SystemExit("Provide either --season-id or --season-label.")

    seasons_payload = fetch_seasons_payload(
        league=league,
        api_key=api_key,
        timeout=timeout,
        insecure=insecure,
        retries=retries,
        retry_wait_seconds=retry_wait_seconds,
    )
    season_rows = normalize_data_rows(seasons_payload)
    desired_variants = set(season_label_variants(season_label))

    for row in season_rows:
        row_id = str(row.get("id", "")).strip()
        if not row_id:
            continue

        row_values = [str(value).strip() for value in row.values() if str(value).strip()]
        normalized_values = {normalize_label(value) for value in row_values}
        if desired_variants & normalized_values:
            return row_id, str(row.get("name", season_label)).strip(), seasons_payload

    examples = []
    for row in season_rows[:10]:
        label = str(row.get("name") or row.get("description") or row.get("id") or "").strip()
        if label:
            examples.append(label)
    example_text = ""
    if examples:
        example_text = " Example seasons: " + ", ".join(examples)
    raise SystemExit(f"Could not resolve season label: {season_label}.{example_text}")


def default_output_paths(stem: str) -> Tuple[Path, Path]:
    return SYNERGY_TMP_DIR / f"{stem}_raw.json", SYNERGY_HISTORY_DIR / f"{stem}.csv"


def cmd_seasons(args: argparse.Namespace) -> None:
    api_key = load_api_key(args.api_key)
    payload = fetch_seasons_payload(
        league=args.league,
        api_key=api_key,
        timeout=args.timeout,
        insecure=args.insecure,
        retries=args.retries,
        retry_wait_seconds=args.retry_wait_seconds,
    )
    rows = normalize_data_rows(payload)

    output_stem = args.output_stem.strip() or f"sportradar_synergy_{args.league}_seasons"
    json_path, csv_path = default_output_paths(output_stem)
    if args.output_json:
        json_path = Path(args.output_json)
    if args.output_csv:
        csv_path = Path(args.output_csv)

    write_json(json_path, payload)
    write_csv(csv_path, rows)

    print(f"[OK] Fetched {len(rows)} Synergy season rows for league={args.league}")
    print(f"[OUT] Raw JSON -> {json_path}")
    print(f"[OUT] CSV -> {csv_path}")


def cmd_player_playtypes(args: argparse.Namespace) -> None:
    api_key = load_api_key(args.api_key)
    season_id, resolved_label, seasons_payload = resolve_season(
        league=args.league,
        api_key=api_key,
        timeout=args.timeout,
        insecure=args.insecure,
        retries=args.retries,
        retry_wait_seconds=args.retry_wait_seconds,
        season_id=args.season_id,
        season_label=args.season_label,
    )
    if not args.play_type:
        raise SystemExit("Provide at least one --play-type value.")

    extra_params = parse_key_value_pairs(args.extra_param)
    output_stem = args.output_stem.strip()
    if not output_stem:
        if len(args.play_type) == 1:
            descriptor = slugify(args.play_type[0])
        else:
            descriptor = "combined"
        season_slug = slugify(resolved_label or season_id)
        output_stem = f"sportradar_synergy_{args.league}_{season_slug}_{descriptor}_playerplaytypes"

    json_path, csv_path = default_output_paths(output_stem)
    if args.output_json:
        json_path = Path(args.output_json)
    if args.output_csv:
        csv_path = Path(args.output_csv)

    request_bundle: List[Dict[str, Any]] = []
    combined_rows: List[Dict[str, Any]] = []

    if seasons_payload:
        request_bundle.append(
            {
                "resource": "seasons",
                "seasonId": season_id,
                "seasonLabel": resolved_label,
                "payload": seasons_payload,
            }
        )

    canonical_play_types = [canonical_play_type(play_type) for play_type in args.play_type]

    for index, (requested_play_type, play_type) in enumerate(
        zip(args.play_type, canonical_play_types)
    ):
        if requested_play_type != play_type:
            print(f"[INFO] PlayType alias: {requested_play_type} -> {play_type}")

        query_params = {"playType": play_type}
        query_params.update(extra_params)
        payload = request_json(
            path=f"/synergy/basketball/{args.league}/seasons/{season_id}/events/reports/playerplaytypestats",
            api_key=api_key,
            query_params=query_params,
            timeout=args.timeout,
            insecure=args.insecure,
            retries=args.retries,
            retry_wait_seconds=args.retry_wait_seconds,
        )
        request_bundle.append(
            {
                "resource": "playerplaytypestats",
                "seasonId": season_id,
                "seasonLabel": resolved_label,
                "requestedPlayType": requested_play_type,
                "playType": play_type,
                "query": query_params,
                "payload": payload,
            }
        )

        rows = normalize_data_rows(payload)
        for row in rows:
            row["SeasonID"] = season_id
            row["SeasonLabel"] = resolved_label
            row["RequestedPlayType"] = requested_play_type
            row["QueryPlayType"] = play_type
            row["PLAYER_NAME"] = row.pop("player_name", "")
            row["PLAYER_ID"] = row.pop("player_id", "")
            row["TEAM_NAME"] = row.pop("team_name", "")
            row["TEAM_ABBREVIATION"] = row.pop("team_abbr", "")
            row["PLAY_TYPE"] = play_type
            row["PLAY_TYPE_REQUESTED"] = requested_play_type
            row["GP"] = row.get("stats_gp", "")
            row["POSS"] = row.get("stats_possessions", "")
            row["TIME_PERCENT"] = row.get("stats_timePercent", "")
            row["POINTS"] = row.get("stats_points", "")
            row["PPP"] = row.get("stats_ppp", "")
            row["PPP_RANK"] = row.get("stats_pppRank", "")
        combined_rows.extend(rows)

        if args.sleep_seconds > 0 and index < len(canonical_play_types) - 1:
            time.sleep(args.sleep_seconds)

    write_json(json_path, request_bundle)
    write_csv(csv_path, combined_rows)

    print(
        f"[OK] Fetched {len(combined_rows)} Synergy player play-type rows "
        f"for league={args.league}, season={resolved_label or season_id}"
    )
    print(f"[OUT] Raw JSON -> {json_path}")
    print(f"[OUT] CSV -> {csv_path}")


def cmd_player_events(args: argparse.Namespace) -> None:
    api_key = load_api_key(args.api_key)
    season_id, resolved_label, _ = resolve_season(
        league=args.league,
        api_key=api_key,
        timeout=args.timeout,
        insecure=args.insecure,
        retries=args.retries,
        retry_wait_seconds=args.retry_wait_seconds,
        season_id=args.season_id,
        season_label=args.season_label,
    )
    if not args.player_id.strip():
        raise SystemExit("Provide --player-id for player-events.")

    extra_params = parse_key_value_pairs(args.extra_param)
    output_stem = args.output_stem.strip()
    if not output_stem:
        season_slug = slugify(resolved_label or season_id)
        output_stem = (
            f"sportradar_synergy_{args.league}_{season_slug}_"
            f"{slugify(args.player_id)}_events"
        )

    json_path, csv_path = default_output_paths(output_stem)
    if args.output_json:
        json_path = Path(args.output_json)
    if args.output_csv:
        csv_path = Path(args.output_csv)

    payload = request_json(
        path=f"/synergy/basketball/{args.league}/seasons/{season_id}/players/{args.player_id}/events",
        api_key=api_key,
        query_params=extra_params,
        timeout=args.timeout,
        insecure=args.insecure,
        retries=args.retries,
        retry_wait_seconds=args.retry_wait_seconds,
    )
    rows = normalize_data_rows(payload)
    for row in rows:
        row["SeasonID"] = season_id
        row["SeasonLabel"] = resolved_label
        row["QueryPlayerID"] = args.player_id.strip()

    write_json(json_path, payload)
    write_csv(csv_path, rows)

    print(
        f"[OK] Fetched {len(rows)} Synergy player event rows "
        f"for league={args.league}, season={resolved_label or season_id}, player={args.player_id}"
    )
    print(f"[OUT] Raw JSON -> {json_path}")
    print(f"[OUT] CSV -> {csv_path}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Pull Sportradar Synergy basketball data into the stats workspace."
    )
    parser.add_argument(
        "--api-key",
        default="",
        help="Optional API key override. Prefer SPORTRADAR_SYNERGY_API_KEY env var.",
    )
    parser.add_argument(
        "--league",
        default="nba",
        help="Synergy league slug, for example nba, wnba, ncaamb, ncaawb, gleague.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=60,
        help="Request timeout in seconds.",
    )
    parser.add_argument(
        "--insecure",
        action="store_true",
        help="Disable SSL certificate verification for environments missing local CA certificates.",
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=2,
        help="Retry count for transient request failures or HTTP 429 responses.",
    )
    parser.add_argument(
        "--retry-wait-seconds",
        type=float,
        default=2.0,
        help="Base backoff delay used when retrying requests.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    seasons_parser = subparsers.add_parser(
        "seasons",
        help="Fetch available Synergy seasons for a league.",
    )
    seasons_parser.add_argument(
        "--output-stem",
        default="",
        help="Optional file stem used for default tmp/history outputs.",
    )
    seasons_parser.add_argument(
        "--output-json",
        default="",
        help="Optional raw JSON output path.",
    )
    seasons_parser.add_argument(
        "--output-csv",
        default="",
        help="Optional normalized CSV output path.",
    )
    seasons_parser.set_defaults(func=cmd_seasons)

    playtypes_parser = subparsers.add_parser(
        "player-playtypes",
        help="Fetch player-level Synergy play-type stats for one season.",
    )
    playtypes_parser.add_argument(
        "--season-id",
        default="",
        help="Exact Sportradar season id. Use this or --season-label.",
    )
    playtypes_parser.add_argument(
        "--season-label",
        default="",
        help="Friendly season label such as 2025-26 or 2025-2026.",
    )
    playtypes_parser.add_argument(
        "--play-type",
        action="append",
        default=[],
        help="Synergy play type to request. Repeat for multiple play types.",
    )
    playtypes_parser.add_argument(
        "--extra-param",
        action="append",
        default=[],
        help="Optional extra query parameter in KEY=VALUE form. Repeat as needed.",
    )
    playtypes_parser.add_argument(
        "--sleep-seconds",
        type=float,
        default=0.25,
        help="Pause between repeated play-type requests.",
    )
    playtypes_parser.add_argument(
        "--output-stem",
        default="",
        help="Optional file stem used for default tmp/history outputs.",
    )
    playtypes_parser.add_argument(
        "--output-json",
        default="",
        help="Optional raw JSON output path.",
    )
    playtypes_parser.add_argument(
        "--output-csv",
        default="",
        help="Optional normalized CSV output path.",
    )
    playtypes_parser.set_defaults(func=cmd_player_playtypes)

    events_parser = subparsers.add_parser(
        "player-events",
        help="Fetch possession-level Synergy event rows for one player and season.",
    )
    events_parser.add_argument(
        "--season-id",
        default="",
        help="Exact Sportradar season id. Use this or --season-label.",
    )
    events_parser.add_argument(
        "--season-label",
        default="",
        help="Friendly season label such as 2025-26 or 2025-2026.",
    )
    events_parser.add_argument(
        "--player-id",
        default="",
        help="Exact Sportradar player id.",
    )
    events_parser.add_argument(
        "--extra-param",
        action="append",
        default=[],
        help="Optional extra query parameter in KEY=VALUE form. Repeat as needed.",
    )
    events_parser.add_argument(
        "--output-stem",
        default="",
        help="Optional file stem used for default tmp/history outputs.",
    )
    events_parser.add_argument(
        "--output-json",
        default="",
        help="Optional raw JSON output path.",
    )
    events_parser.add_argument(
        "--output-csv",
        default="",
        help="Optional normalized CSV output path.",
    )
    events_parser.set_defaults(func=cmd_player_events)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
