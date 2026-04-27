import argparse
import json
from pathlib import Path

import check_immerse_offsets as cio
import find_immerse_roster_slots as firs


DEFAULT_TEMPLATE = Path(r"C:\2k\Upgrade\2k26_offsets_2026-04-24_relative_compact.json")
DEFAULT_OUTPUT_DIR = Path(r"C:\2k\Upgrade")


def find_player_tables_from_regions(handle, player_size: int, expected_names: list[tuple[str, str]]) -> list[dict]:
    first_expected, last_expected = expected_names[0]
    pattern = (last_expected + "\x00").encode("utf-16-le")
    hits: list[dict] = []
    seen: set[int] = set()
    for region_base, region_size in firs.iter_readable_regions(handle):
        raw = firs.read_bytes(handle, region_base, region_size)
        if not raw:
            continue
        start = 0
        while True:
            idx = raw.find(pattern, start)
            if idx < 0:
                break
            table_base = region_base + idx
            if table_base in seen:
                start = idx + 2
                continue
            first_name = firs.read_wstring(handle, table_base + 40, 20)
            if first_name == first_expected:
                score = firs.score_player_table(handle, table_base, player_size, expected_names)
                if score >= min(4, len(expected_names)):
                    team_ptr = cio.read_qword(handle, table_base + 96)
                    hits.append(
                        {
                            "table_base": table_base,
                            "score": score,
                            "team_ptr": team_ptr["value"] if team_ptr["ok"] else 0,
                        }
                    )
                    seen.add(table_base)
            start = idx + 2
    hits.sort(key=lambda item: (item["score"], item["table_base"]))
    return hits


def clone_offsets(template_path: Path) -> dict:
    with template_path.open("r", encoding="utf-8-sig") as handle:
        return json.load(handle)


def write_candidate_file(
    template_path: Path,
    output_path: Path,
    version_label: str,
    player_address: int,
    team_address: int,
) -> None:
    data = clone_offsets(template_path)
    data["game_info"]["version"] = version_label
    data["base_pointers"]["Player"]["address"] = player_address
    data["base_pointers"]["Player"]["chain"] = []
    data["base_pointers"]["Team"]["address"] = team_address
    data["base_pointers"]["Team"]["chain"] = []
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        json.dump(data, handle, indent=4)
        handle.write("\n")


def sample_players(handle, table_base: int, player_size: int, limit: int = 6) -> list[str]:
    rows: list[str] = []
    for index in range(limit):
        record_base = table_base + index * player_size
        last_name = firs.read_wstring(handle, record_base + 0, 20)
        first_name = firs.read_wstring(handle, record_base + 40, 20)
        if not (first_name or last_name):
            break
        rows.append(f"{first_name} {last_name}".strip())
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Write Immerse direct-roster offset candidates from the live NBA2K26 memory layout."
    )
    parser.add_argument("--template", type=Path, default=DEFAULT_TEMPLATE)
    parser.add_argument("--players-csv", type=Path, default=firs.DEFAULT_PLAYERS_CSV)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--exe-name", default="NBA2K26.exe")
    parser.add_argument("--name-count", type=int, default=8)
    args = parser.parse_args()

    expected_names = firs.load_name_sequence(args.players_csv, limit=args.name_count)
    template = clone_offsets(args.template)
    player_size = int(template["game_info"]["playerSize"])

    pid = cio.find_process_id(args.exe_name)
    if pid is None:
        print(f"{args.exe_name} is not running.")
        return 1

    handle = cio.kernel32.OpenProcess(cio.PROCESS_QUERY_INFORMATION | cio.PROCESS_VM_READ, False, pid)
    if not handle:
        print(f"OpenProcess failed for pid {pid}.")
        return 1

    try:
        print(
            "Scanning for live roster tables starting with: "
            + ", ".join(f"{first} {last}" for first, last in expected_names[:4])
        )
        hits = find_player_tables_from_regions(handle, player_size, expected_names)
        if not hits:
            print("No live roster tables found.")
            return 2

        args.output_dir.mkdir(parents=True, exist_ok=True)
        for index, hit in enumerate(hits, start=1):
            output_path = args.output_dir / f"2k26_offsets_direct_roster_candidate_{index}.json"
            version_label = f"April 24 2026 Patch (direct roster candidate {index})"
            write_candidate_file(
                args.template,
                output_path,
                version_label,
                hit["table_base"],
                hit["team_ptr"],
            )
            print(
                f"Candidate {index}\n"
                f"- file={output_path}\n"
                f"- player_address=0x{hit['table_base']:X}\n"
                f"- team_address=0x{hit['team_ptr']:X}\n"
                f"- sample={', '.join(sample_players(handle, hit['table_base'], player_size))}"
            )
        return 0
    finally:
        cio.kernel32.CloseHandle(handle)


if __name__ == "__main__":
    raise SystemExit(main())
