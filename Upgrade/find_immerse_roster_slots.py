import argparse
import csv
import json
import struct
from pathlib import Path

import check_immerse_offsets as cio


DEFAULT_OFFSETS_PATH = Path(r"C:\2k\Upgrade\2k26_offsets_2026-04-24_relative_compact.json")
DEFAULT_PLAYERS_CSV = Path(r"C:\2k\Upgrade\players.csv")
PAGE_GUARD = 0x100
PAGE_NOACCESS = 0x01
MEM_COMMIT = 0x1000
READABLE_PROTECTIONS = {
    0x02,  # PAGE_READONLY
    0x04,  # PAGE_READWRITE
    0x08,  # PAGE_WRITECOPY
    0x20,  # PAGE_EXECUTE_READ
    0x40,  # PAGE_EXECUTE_READWRITE
    0x80,  # PAGE_EXECUTE_WRITECOPY
}


class MEMORY_BASIC_INFORMATION(cio.ctypes.Structure):
    _fields_ = [
        ("BaseAddress", cio.ctypes.c_void_p),
        ("AllocationBase", cio.ctypes.c_void_p),
        ("AllocationProtect", cio.wintypes.DWORD),
        ("PartitionId", cio.wintypes.WORD),
        ("RegionSize", cio.ctypes.c_size_t),
        ("State", cio.wintypes.DWORD),
        ("Protect", cio.wintypes.DWORD),
        ("Type", cio.wintypes.DWORD),
    ]


cio.kernel32.VirtualQueryEx.argtypes = [
    cio.wintypes.HANDLE,
    cio.wintypes.LPCVOID,
    cio.ctypes.POINTER(MEMORY_BASIC_INFORMATION),
    cio.ctypes.c_size_t,
]
cio.kernel32.VirtualQueryEx.restype = cio.ctypes.c_size_t


def read_bytes(handle, address: int, size: int) -> bytes | None:
    buffer = (cio.ctypes.c_ubyte * size)()
    bytes_read = cio.ctypes.c_size_t()
    ok = cio.kernel32.ReadProcessMemory(
        handle,
        cio.ctypes.c_void_p(address),
        buffer,
        size,
        cio.ctypes.byref(bytes_read),
    )
    if not ok or bytes_read.value <= 0:
        return None
    return bytes(buffer[: bytes_read.value])


def read_wstring(handle, address: int, length: int) -> str:
    raw = read_bytes(handle, address, length * 2)
    if not raw:
        return ""
    try:
        return raw.decode("utf-16-le", errors="ignore").split("\x00", 1)[0].strip()
    except Exception:
        return ""


def load_name_sequence(players_csv: Path, limit: int = 8) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    with players_csv.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            first = (row.get("firstName") or "").strip()
            last = (row.get("lastName") or "").strip()
            if first and last:
                rows.append((first, last))
            if len(rows) >= limit:
                break
    if len(rows) < 2:
        raise RuntimeError(f"Could not load enough player names from {players_csv}")
    return rows


def load_offsets(path: Path) -> dict:
    with path.open("r", encoding="utf-8-sig") as handle:
        return json.load(handle)


def iter_readable_regions(handle):
    address = 0
    while True:
        mbi = MEMORY_BASIC_INFORMATION()
        result = cio.kernel32.VirtualQueryEx(
            handle,
            cio.ctypes.c_void_p(address),
            cio.ctypes.byref(mbi),
            cio.ctypes.sizeof(mbi),
        )
        if not result:
            break
        base_address = int(mbi.BaseAddress or 0)
        region_size = int(mbi.RegionSize or 0)
        if region_size <= 0:
            break
        protect = int(mbi.Protect)
        is_readable = (
            int(mbi.State) == MEM_COMMIT
            and not (protect & PAGE_GUARD)
            and protect != PAGE_NOACCESS
            and (protect & 0xFF) in READABLE_PROTECTIONS
        )
        if is_readable:
            yield base_address, region_size
        address = base_address + region_size


def score_player_table(handle, table_base: int, player_size: int, expected_names: list[tuple[str, str]]) -> int:
    score = 0
    for index, (first_expected, last_expected) in enumerate(expected_names):
        record_base = table_base + index * player_size
        last_name = read_wstring(handle, record_base + 0, 20)
        first_name = read_wstring(handle, record_base + 40, 20)
        if first_name == first_expected and last_name == last_expected:
            score += 1
        else:
            break
    return score


def find_player_slot(
    handle,
    old_slot: int,
    player_size: int,
    expected_names: list[tuple[str, str]],
    scan_bytes: int,
) -> dict | None:
    best: dict | None = None
    start = max(0, old_slot - scan_bytes)
    end = old_slot + scan_bytes
    first_expected, last_expected = expected_names[0]
    for slot in range(start, end + 1, 8):
        probe = cio.read_qword(handle, slot)
        if not probe["ok"]:
            continue
        table_base = probe["value"]
        if not table_base:
            continue
        last_name = read_wstring(handle, table_base + 0, 20)
        first_name = read_wstring(handle, table_base + 40, 20)
        if first_name != first_expected or last_name != last_expected:
            continue
        score = score_player_table(handle, table_base, player_size, expected_names)
        team_ptr = cio.read_qword(handle, table_base + 96)
        candidate = {
            "slot": slot,
            "table_base": table_base,
            "score": score,
            "distance": abs(slot - old_slot),
            "team_ptr": team_ptr["value"] if team_ptr["ok"] else 0,
        }
        if (
            best is None
            or candidate["score"] > best["score"]
            or (candidate["score"] == best["score"] and candidate["distance"] < best["distance"])
        ):
            best = candidate
    return best


def find_player_table_from_regions(handle, player_size: int, expected_names: list[tuple[str, str]]) -> dict | None:
    first_expected, last_expected = expected_names[0]
    pattern = (last_expected + "\x00").encode("utf-16-le")
    best: dict | None = None
    for region_base, region_size in iter_readable_regions(handle):
        raw = read_bytes(handle, region_base, region_size)
        if not raw:
            continue
        start = 0
        while True:
            idx = raw.find(pattern, start)
            if idx < 0:
                break
            table_base = region_base + idx
            first_name = read_wstring(handle, table_base + 40, 20)
            if first_name == first_expected:
                score = score_player_table(handle, table_base, player_size, expected_names)
                if score >= 2:
                    team_ptr = cio.read_qword(handle, table_base + 96)
                    candidate = {
                        "table_base": table_base,
                        "score": score,
                        "team_ptr": team_ptr["value"] if team_ptr["ok"] else 0,
                        "region_base": region_base,
                    }
                    if best is None or candidate["score"] > best["score"]:
                        best = candidate
            start = idx + 2
    return best


def find_pointer_slot_by_value(handle, pointer_value: int, old_slot: int | None = None, max_slot_address: int | None = None) -> dict | None:
    pattern = struct.pack("<Q", pointer_value)
    best: dict | None = None
    for region_base, region_size in iter_readable_regions(handle):
        if max_slot_address is not None and region_base > max_slot_address:
            continue
        raw = read_bytes(handle, region_base, region_size)
        if not raw:
            continue
        start = 0
        while True:
            idx = raw.find(pattern, start)
            if idx < 0:
                break
            slot = region_base + idx
            probe = cio.read_qword(handle, slot)
            if not probe["ok"] or probe["value"] != pointer_value:
                start = idx + 1
                continue
            candidate = {
                "slot": slot,
                "distance": abs(slot - old_slot) if old_slot is not None else slot,
            }
            if best is None or candidate["distance"] < best["distance"]:
                best = candidate
            start = idx + 1
    return best


def find_team_slot(handle, old_slot: int, expected_team_ptr: int, scan_bytes: int) -> dict | None:
    if not expected_team_ptr:
        return None
    start = max(0, old_slot - scan_bytes)
    end = old_slot + scan_bytes
    best: dict | None = None
    for slot in range(start, end + 1, 8):
        probe = cio.read_qword(handle, slot)
        if not probe["ok"]:
            continue
        if probe["value"] != expected_team_ptr:
            continue
        candidate = {
            "slot": slot,
            "team_base": probe["value"],
            "distance": abs(slot - old_slot),
        }
        if best is None or candidate["distance"] < best["distance"]:
            best = candidate
    return best


def main() -> int:
    parser = argparse.ArgumentParser(description="Find updated Immerse Player/Team slot addresses for NBA2K26.")
    parser.add_argument("--offsets", type=Path, default=DEFAULT_OFFSETS_PATH)
    parser.add_argument("--players-csv", type=Path, default=DEFAULT_PLAYERS_CSV)
    parser.add_argument("--exe-name", default="NBA2K26.exe")
    parser.add_argument("--scan-bytes", type=int, default=2_000_000)
    parser.add_argument("--name-count", type=int, default=8)
    args = parser.parse_args()

    offsets = load_offsets(args.offsets)
    player_size = int(offsets["game_info"]["playerSize"])
    old_player_slot = int(offsets["base_pointers"]["Player"]["address"])
    old_team_slot = int(offsets["base_pointers"]["Team"]["address"])
    expected_names = load_name_sequence(args.players_csv, limit=args.name_count)

    pid = cio.find_process_id(args.exe_name)
    if pid is None:
        print(f"{args.exe_name} is not running.")
        return 1

    handle = cio.kernel32.OpenProcess(cio.PROCESS_QUERY_INFORMATION | cio.PROCESS_VM_READ, False, pid)
    if not handle:
        print(f"OpenProcess failed for pid {pid}.")
        return 1

    try:
        print(f"Scanning pid={pid} around Player slot 0x{old_player_slot:X} (+/- {args.scan_bytes:,} bytes)")
        print(f"Expected leading players: {', '.join(f'{first} {last}' for first, last in expected_names[:4])}")
        player_hit = find_player_slot(handle, old_player_slot, player_size, expected_names, args.scan_bytes)
        if not player_hit:
            print("No nearby Player slot candidate found. Falling back to full readable-memory scan.")
            table_hit = find_player_table_from_regions(handle, player_size, expected_names)
            if not table_hit:
                print("No Player table candidate found in readable memory.")
                return 2
            slot_hit = find_pointer_slot_by_value(handle, table_hit["table_base"], old_player_slot)
            if not slot_hit:
                print("Player table found, but no pointer slot referencing it was found.")
                return 2
            player_hit = {
                "slot": slot_hit["slot"],
                "table_base": table_hit["table_base"],
                "score": table_hit["score"],
                "distance": abs(slot_hit["slot"] - old_player_slot),
                "team_ptr": table_hit["team_ptr"],
            }
        print(
            "Player slot candidate\n"
            f"- slot=0x{player_hit['slot']:X}\n"
            f"- table=0x{player_hit['table_base']:X}\n"
            f"- score={player_hit['score']} consecutive player-name matches\n"
            f"- team_ptr=0x{player_hit['team_ptr']:X}\n"
            f"- delta={player_hit['slot'] - old_player_slot:+,}"
        )

        team_hit = find_team_slot(handle, old_team_slot, player_hit["team_ptr"], args.scan_bytes)
        if not team_hit:
            print("No nearby Team slot candidate found. Falling back to full readable-memory scan.")
            slot_hit = find_pointer_slot_by_value(handle, player_hit["team_ptr"], old_team_slot)
            if not slot_hit:
                print("No Team slot candidate found.")
                return 3
            team_hit = {
                "slot": slot_hit["slot"],
                "team_base": player_hit["team_ptr"],
                "distance": abs(slot_hit["slot"] - old_team_slot),
            }
        print(
            "Team slot candidate\n"
            f"- slot=0x{team_hit['slot']:X}\n"
            f"- table=0x{team_hit['team_base']:X}\n"
            f"- delta={team_hit['slot'] - old_team_slot:+,}"
        )
        return 0
    finally:
        cio.kernel32.CloseHandle(handle)


if __name__ == "__main__":
    raise SystemExit(main())
