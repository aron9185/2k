import argparse
import ctypes
import json
import sys
from ctypes import wintypes
from pathlib import Path


DEFAULT_OFFSETS_DIR = Path(r"C:\Program Files (x86)\Immerse\config\offsets")
PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010
TH32CS_SNAPPROCESS = 0x00000002
TH32CS_SNAPMODULE = 0x00000008
TH32CS_SNAPMODULE32 = 0x00000010
MAX_PATH = 260


kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * MAX_PATH),
    ]


class MODULEENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("th32ModuleID", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("GlblcntUsage", wintypes.DWORD),
        ("ProccntUsage", wintypes.DWORD),
        ("modBaseAddr", ctypes.POINTER(ctypes.c_ubyte)),
        ("modBaseSize", wintypes.DWORD),
        ("hModule", wintypes.HMODULE),
        ("szModule", wintypes.WCHAR * 256),
        ("szExePath", wintypes.WCHAR * MAX_PATH),
    ]


kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
kernel32.Process32FirstW.restype = wintypes.BOOL
kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
kernel32.Process32NextW.restype = wintypes.BOOL
kernel32.Module32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MODULEENTRY32W)]
kernel32.Module32FirstW.restype = wintypes.BOOL
kernel32.Module32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MODULEENTRY32W)]
kernel32.Module32NextW.restype = wintypes.BOOL
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.ReadProcessMemory.argtypes = [
    wintypes.HANDLE,
    wintypes.LPCVOID,
    wintypes.LPVOID,
    ctypes.c_size_t,
    ctypes.POINTER(ctypes.c_size_t),
]
kernel32.ReadProcessMemory.restype = wintypes.BOOL
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL


def format_address(value):
    if value is None:
        return "-"
    return f"0x{value:X}"


def load_offsets(path):
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def list_offset_files(offsets_dir):
    return sorted(offsets_dir.glob("2k26_offsets*.json"))


def find_process_id(exe_name):
    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snapshot == wintypes.HANDLE(-1).value:
        raise OSError("CreateToolhelp32Snapshot failed for processes")
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        found = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while found:
            if entry.szExeFile.lower() == exe_name.lower():
                return entry.th32ProcessID
            found = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    return None


def find_main_module(pid, exe_name):
    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32, pid)
    if snapshot == wintypes.HANDLE(-1).value:
        return None
    try:
        entry = MODULEENTRY32W()
        entry.dwSize = ctypes.sizeof(MODULEENTRY32W)
        found = kernel32.Module32FirstW(snapshot, ctypes.byref(entry))
        while found:
            if entry.szModule.lower() == exe_name.lower():
                base = ctypes.cast(entry.modBaseAddr, ctypes.c_void_p).value
                return {
                    "base_address": base,
                    "size": int(entry.modBaseSize),
                    "path": entry.szExePath,
                }
            found = kernel32.Module32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    return None


def read_qword(handle, address):
    buffer = ctypes.c_uint64()
    bytes_read = ctypes.c_size_t()
    ok = kernel32.ReadProcessMemory(
        handle,
        ctypes.c_void_p(address),
        ctypes.byref(buffer),
        ctypes.sizeof(buffer),
        ctypes.byref(bytes_read),
    )
    return {
        "ok": bool(ok),
        "bytes_read": int(bytes_read.value),
        "value": int(buffer.value) if ok else None,
    }


def summarize_file(path, data):
    game_info = data.get("game_info", {})
    base_pointers = data.get("base_pointers", {})
    return {
        "name": path.name,
        "version": game_info.get("version"),
        "executable": game_info.get("executable"),
        "player_size": game_info.get("playerSize"),
        "base_pointers": {
            key: value.get("address")
            for key, value in base_pointers.items()
        },
    }


def diff_base_pointers(left, right):
    left_bases = left.get("base_pointers", {})
    right_bases = right.get("base_pointers", {})
    names = sorted(set(left_bases.keys()) | set(right_bases.keys()))
    rows = []
    for name in names:
        left_addr = left_bases.get(name, {}).get("address")
        right_addr = right_bases.get(name, {}).get("address")
        if left_addr != right_addr:
            rows.append((name, left_addr, right_addr))
    return rows


def diff_offsets(left, right):
    left_offsets = left.get("offsets", [])
    right_offsets = right.get("offsets", [])
    if len(left_offsets) != len(right_offsets):
        return {"count": None, "address_diffs": None, "non_address_diffs": None}
    total = 0
    address_diffs = 0
    non_address_diffs = 0
    for left_item, right_item in zip(left_offsets, right_offsets):
        if left_item != right_item:
            total += 1
            left_addr = left_item.get("address")
            right_addr = right_item.get("address")
            if left_addr != right_addr:
                address_diffs += 1
            else:
                non_address_diffs += 1
    return {
        "count": total,
        "address_diffs": address_diffs,
        "non_address_diffs": non_address_diffs,
    }


def print_file_summaries(files):
    print("Offset files")
    for path, data in files:
        summary = summarize_file(path, data)
        print(
            f"- {summary['name']}: {summary['version']} "
            f"(exe={summary['executable']}, playerSize={summary['player_size']})"
        )
        for key in ("Player", "Team", "Staff", "Stadium", "TeamHistory", "NBAHistory", "HallOfFame"):
            address = summary["base_pointers"].get(key)
            print(f"  {key:<12} {format_address(address)}")
    print()


def print_comparisons(files):
    if len(files) < 2:
        return
    print("File comparisons")
    first_path, first_data = files[0]
    for other_path, other_data in files[1:]:
        print(f"- {first_path.name} vs {other_path.name}")
        base_rows = diff_base_pointers(first_data, other_data)
        if base_rows:
            for name, left_addr, right_addr in base_rows:
                delta = None
                if left_addr is not None and right_addr is not None:
                    delta = right_addr - left_addr
                delta_text = "-" if delta is None else f"{delta:+,}"
                print(
                    f"  {name:<12} {format_address(left_addr)} -> "
                    f"{format_address(right_addr)} ({delta_text})"
                )
        else:
            print("  base pointers: identical")
        offset_stats = diff_offsets(first_data, other_data)
        if offset_stats["count"] is None:
            print("  offsets array: different lengths")
        else:
            print(
                "  offsets array: "
                f"{offset_stats['count']} differences "
                f"({offset_stats['address_diffs']} address, "
                f"{offset_stats['non_address_diffs']} non-address)"
            )
    print()


def print_live_probe(files, exe_name):
    pid = find_process_id(exe_name)
    if not pid:
        print(f"Live probe\n- {exe_name} is not running.\n")
        return

    module = find_main_module(pid, exe_name)

    handle = kernel32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
    if not handle:
        print(f"Live probe\n- OpenProcess failed for PID {pid}.\n")
        return

    try:
        print("Live probe")
        if module:
            print(
                f"- pid={pid}, module_base={format_address(module['base_address'])}, "
                f"module_size={module['size']:,}"
            )
        else:
            print(
                f"- pid={pid}, module base unavailable from this Python runtime; "
                "absolute probes only"
            )
        for path, data in files:
            print(f"- {path.name}")
            for name in ("Player", "Team", "Staff", "Stadium", "TeamHistory", "NBAHistory", "HallOfFame"):
                stored = data.get("base_pointers", {}).get(name, {}).get("address")
                if stored is None:
                    continue
                absolute_probe = read_qword(handle, stored)
                rel_value = None
                if module and stored < module["size"]:
                    rel_probe = read_qword(handle, module["base_address"] + stored)
                    rel_value = rel_probe["value"] if rel_probe["ok"] else None
                print(
                    f"  {name:<12} stored={format_address(stored)} "
                    f"abs={format_address(absolute_probe['value'])} "
                    f"module+stored={format_address(rel_value)}"
                )
        print()
        print(
            "Note: zero values during the live probe usually mean the current game "
            "state has not initialized that table yet, or the pointer is stale."
        )
        print()
    finally:
        kernel32.CloseHandle(handle)


def main():
    parser = argparse.ArgumentParser(
        description="Compare Immerse 2K26 offset files and probe the live NBA2K26 process."
    )
    parser.add_argument(
        "--offsets-dir",
        type=Path,
        default=DEFAULT_OFFSETS_DIR,
        help=f"Directory containing 2k26_offsets*.json files (default: {DEFAULT_OFFSETS_DIR})",
    )
    parser.add_argument(
        "--exe-name",
        default="NBA2K26.exe",
        help="Process executable name to probe (default: NBA2K26.exe)",
    )
    args = parser.parse_args()

    files = [(path, load_offsets(path)) for path in list_offset_files(args.offsets_dir)]
    if not files:
        print(f"No 2k26_offsets*.json files found in {args.offsets_dir}", file=sys.stderr)
        return 1

    print_file_summaries(files)
    print_comparisons(files)
    print_live_probe(files, args.exe_name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
