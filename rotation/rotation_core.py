# rotation_core.py
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import requests

ROTATION_COLUMNS = [
    "TEAM_ID",
    "PERSON_ID",
    "PLAYER_FIRST",
    "PLAYER_LAST",
    "IN_TIME_REAL",
    "OUT_TIME_REAL",
]

REG_SECONDS = 48 * 60

CDN_PBP = "https://cdn.nba.com/static/json/liveData/playbyplay/playbyplay_{GAME_ID}.json"
CDN_BOXSCORE = "https://cdn.nba.com/static/json/liveData/boxscore/boxscore_{GAME_ID}.json"

CACHE_DIR = os.path.join(os.path.dirname(__file__), "cache_json")
os.makedirs(CACHE_DIR, exist_ok=True)

DEFAULT_TTL_SEC = 20
DEFAULT_STALE_SEC = 365 * 24 * 3600
CONNECT_TIMEOUT = 0.5
READ_TIMEOUT = 1.5
RETRY_TIMEOUTS = (
    (CONNECT_TIMEOUT, READ_TIMEOUT),
    (1.0, 3.0),
)

DEBUG_ROTATION = os.environ.get("ROT_DEBUG", "1") == "1"

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/136.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://www.nba.com",
    "Referer": "https://www.nba.com/",
}

# Supports "11:32"
_MMSS_RE = re.compile(r"^\s*(\d{1,2}):(\d{2})\s*$")
# Supports ISO duration "PT11M32.00S"
_PT_RE = re.compile(r"^\s*PT(\d+)M(\d+(?:\.\d+)?)S\s*$", re.IGNORECASE)


def empty_rotation_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "TEAM_ID": pd.Series(dtype="int64"),
            "PERSON_ID": pd.Series(dtype="int64"),
            "PLAYER_FIRST": pd.Series(dtype="object"),
            "PLAYER_LAST": pd.Series(dtype="object"),
            "IN_TIME_REAL": pd.Series(dtype="int64"),
            "OUT_TIME_REAL": pd.Series(dtype="int64"),
        }
    )


def ensure_rotation_schema(df: object) -> pd.DataFrame:
    if isinstance(df, pd.DataFrame):
        out = df.copy()
    else:
        try:
            out = pd.DataFrame(df)
        except Exception:
            return empty_rotation_df()

    base = empty_rotation_df()
    for c in ROTATION_COLUMNS:
        if c not in out.columns:
            out[c] = pd.Series(dtype=base[c].dtype)
    return out[ROTATION_COLUMNS]


def _cache_path(key: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", key)
    return os.path.join(CACHE_DIR, safe + ".json")


def _read_cache(path: str) -> Optional[Dict[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            obj = json.load(f)
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


def _write_cache_atomic(path: str, obj: Dict[str, Any]) -> None:
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f)
        os.replace(tmp, path)
    except Exception:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass


def fetch_json(url: str, cache_key: str, ttl_sec: int = DEFAULT_TTL_SEC, stale_sec: int = DEFAULT_STALE_SEC) -> Dict[str, Any]:
    path = _cache_path(cache_key)
    now = time.time()

    if os.path.exists(path):
        age = now - os.path.getmtime(path)
        if age < ttl_sec:
            cached = _read_cache(path)
            if cached is not None:
                return cached

    session = requests.Session()
    session.trust_env = False
    for timeout in RETRY_TIMEOUTS:
        try:
            r = session.get(url, timeout=timeout, headers=DEFAULT_HEADERS)
            if r.status_code == 200:
                obj = r.json()
                if isinstance(obj, dict):
                    _write_cache_atomic(path, obj)
                    return obj
        except Exception:
            continue

    if os.path.exists(path):
        age = now - os.path.getmtime(path)
        if age < stale_sec:
            cached = _read_cache(path)
            if cached is not None:
                return cached

    return {}


def _safe_int(x: Any) -> Optional[int]:
    try:
        if x is None:
            return None
        return int(x)
    except Exception:
        return None


def _safe_str(x: Any) -> str:
    try:
        return str(x or "").strip()
    except Exception:
        return ""


def _walk_items(obj: Any, prefix: str = "") -> List[Tuple[str, Any]]:
    """
    Flatten nested dict/list into (path, value).
    Paths use dot notation, lists use [i].
    """
    out: List[Tuple[str, Any]] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{prefix}.{k}" if prefix else str(k)
            out.extend(_walk_items(v, p))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            p = f"{prefix}[{i}]"
            out.extend(_walk_items(v, p))
    else:
        out.append((prefix, obj))
    return out


def _find_int_candidates(action: Dict[str, Any]) -> List[Tuple[str, int]]:
    """
    Find all int-like values inside the action with their flattened paths.
    """
    items = _walk_items(action)
    cand: List[Tuple[str, int]] = []
    for path, val in items:
        iv = _safe_int(val)
        if iv is not None:
            cand.append((path, iv))
    return cand


def _extract_sub_in_out_generic(a: Dict[str, Any]) -> Tuple[Optional[int], Optional[int], Dict[str, Any]]:
    """
    Robust extraction using:
      - known top-level keys
      - recursive scan for keys containing 'in'/'out' and 'id'/'person'
    Returns (pin, pout, debug_info)
    """
    # 1) known keys
    pin = _safe_int(a.get("playerIn")) or _safe_int(a.get("playerInId")) or _safe_int(a.get("playerInPersonId"))
    pout = _safe_int(a.get("playerOut")) or _safe_int(a.get("playerOutId")) or _safe_int(a.get("playerOutPersonId"))

    dbg: Dict[str, Any] = {"pin_from_known": pin, "pout_from_known": pout}

    if pin is not None or pout is not None:
        return pin, pout, dbg

    # 2) recursive scan
    cands = _find_int_candidates(a)

    # heuristic: pick best matching paths
    in_keys = []
    out_keys = []
    for path, iv in cands:
        p = path.lower()
        # prefer ids, personIds
        if ("in" in p) and (("person" in p) or ("id" in p)) and ("player" in p or "sub" in p or "in" in p):
            in_keys.append((path, iv))
        if ("out" in p) and (("person" in p) or ("id" in p)) and ("player" in p or "sub" in p or "out" in p):
            out_keys.append((path, iv))

    # fallback: some feeds use entering/leaving
    if not in_keys:
        for path, iv in cands:
            p = path.lower()
            if ("enter" in p or "incoming" in p) and (("person" in p) or ("id" in p)):
                in_keys.append((path, iv))
    if not out_keys:
        for path, iv in cands:
            p = path.lower()
            if ("leave" in p or "outgoing" in p) and (("person" in p) or ("id" in p)):
                out_keys.append((path, iv))

    # Choose first candidate deterministically (paths are stable)
    if in_keys:
        in_keys.sort(key=lambda x: x[0])
        pin = in_keys[0][1]
        dbg["pin_path"] = in_keys[0][0]
    if out_keys:
        out_keys.sort(key=lambda x: x[0])
        pout = out_keys[0][1]
        dbg["pout_path"] = out_keys[0][0]

    # keep some context for debugging
    dbg["top_in_candidates"] = in_keys[:5]
    dbg["top_out_candidates"] = out_keys[:5]

    return pin, pout, dbg

def iter_pbp_actions(pbp_json: Dict[str, Any]) -> List[Dict[str, Any]]:
    g = (pbp_json or {}).get("game") or {}
    actions = g.get("actions")
    return actions if isinstance(actions, list) else []


def parse_boxscore_meta(box_json: Dict[str, Any]) -> Dict[str, Any]:
    g = (box_json or {}).get("game") or {}
    ht = g.get("homeTeam") or {}
    at = g.get("awayTeam") or {}
    return {
        "home_team_id": _safe_int(ht.get("teamId")) or 0,
        "away_team_id": _safe_int(at.get("teamId")) or 0,
        "home_abbr": _safe_str(ht.get("teamTricode")),
        "away_abbr": _safe_str(at.get("teamTricode")),
    }


@dataclass(frozen=True)
class PlayerInfo:
    person_id: int
    team_id: int
    first: str
    last: str
    starter: bool


def parse_boxscore_players(box_json: Dict[str, Any]) -> Tuple[Dict[int, PlayerInfo], Dict[int, List[int]]]:
    pid_map: Dict[int, PlayerInfo] = {}
    starters_by_team: Dict[int, List[int]] = {}

    g = (box_json or {}).get("game") or {}
    for side in ("homeTeam", "awayTeam"):
        t = g.get(side) or {}
        tid = _safe_int(t.get("teamId"))
        if tid is None:
            continue

        starters: List[int] = []
        players = t.get("players") or []
        if not isinstance(players, list):
            players = []

        for p in players:
            if not isinstance(p, dict):
                continue
            pid = _safe_int(p.get("personId"))
            if pid is None:
                continue

            first = _safe_str(p.get("firstName"))
            last = _safe_str(p.get("familyName"))
            starter_flag = bool(p.get("starter") or p.get("isStarter") or False)

            pid_map[pid] = PlayerInfo(pid, tid, first, last, starter_flag)
            if starter_flag:
                starters.append(pid)

        starters_by_team[tid] = starters

    return pid_map, starters_by_team


def _is_substitution_action(a: Dict[str, Any]) -> bool:
    atype = _safe_str(a.get("actionType")).lower()
    return "sub" in atype


def _get_sub_in_out(a: dict) -> tuple[int | None, int | None]:
    """
    CDN schema:
      actionType == "substitution"
      subType == "in" or "out"
      personId == the player entering/leaving
    """
    st = _safe_str(a.get("subType")).lower()
    pid = _safe_int(a.get("personId"))

    if pid is None:
        return None, None

    if st == "in":
        return pid, None
    if st == "out":
        return None, pid

    # Some rare cases may have empty/unknown subtype; ignore safely.
    return None, None

def _apply_sub_batch(
    team_on: dict[int, set[int]],
    t: int,
    batch: list[dict],
    debug: bool = False,
) -> None:
    """
    Apply all subs at the same (t, teamId).
    Rule: OUT first, then IN.
    """
    if not batch:
        return

    team_id = _safe_int(batch[0].get("teamId")) or 0
    if team_id == 0:
        return

    outs: list[int] = []
    ins: list[int] = []

    for a in batch:
        pin, pout = _get_sub_in_out(a)
        if pout is not None:
            outs.append(pout)
        if pin is not None:
            ins.append(pin)

    if team_id not in team_on:
        team_on[team_id] = set()

    on = team_on[team_id]

    # OUT first
    for pid in outs:
        if pid in on:
            on.remove(pid)

    # IN after OUT
    for pid in ins:
        on.add(pid)

    if debug:
        def _nm(a_):
            return _safe_str(a_.get("playerNameI") or a_.get("playerName") or "")

        # show readable summary from descriptions
        out_names = [a.get("description") or _nm(a) for a in batch if _safe_str(a.get("subType")).lower() == "out"]
        in_names  = [a.get("description") or _nm(a) for a in batch if _safe_str(a.get("subType")).lower() == "in"]

        print(f"[subs] t={t} teamId={team_id} outs={len(outs)} ins={len(ins)} on_court={len(on)}")
        if out_names or in_names:
            print("       OUT:", out_names[:10])
            print("       IN :", in_names[:10])

def build_person_team_map_from_pbp(actions: List[Dict[str, Any]]) -> Dict[int, int]:
    out: Dict[int, int] = {}
    for a in actions:
        tid = _safe_int(a.get("teamId"))
        pid = _safe_int(a.get("personId"))
        if tid is not None and pid is not None:
            out[pid] = tid

        if _is_substitution_action(a) and tid is not None:
            pin, pout = _get_sub_in_out(a)
            if pin is not None:
                out.setdefault(pin, tid)
            if pout is not None:
                out.setdefault(pout, tid)
    return out


def build_person_name_map_from_pbp(actions: List[Dict[str, Any]]) -> Dict[int, Tuple[str, str]]:
    name_map: Dict[int, Tuple[str, str]] = {}

    def split_name(full: str) -> Tuple[str, str]:
        full = _safe_str(full)
        if not full:
            return ("", "")
        parts = full.split()
        if len(parts) == 1:
            return (parts[0], "")
        return (" ".join(parts[:-1]), parts[-1])

    for a in actions:
        pid = _safe_int(a.get("personId"))
        if pid is not None and pid not in name_map:
            nm = _safe_str(a.get("playerName")) or _safe_str(a.get("personName")) or _safe_str(a.get("playerNameI"))
            if nm:
                name_map[pid] = split_name(nm)

        if _is_substitution_action(a):
            pin, pout = _get_sub_in_out(a)
            pin_name = _safe_str(a.get("playerInName"))
            pout_name = _safe_str(a.get("playerOutName"))
            if pin is not None and pin_name and pin not in name_map:
                name_map[pin] = split_name(pin_name)
            if pout is not None and pout_name and pout not in name_map:
                name_map[pout] = split_name(pout_name)

    return name_map


def clock_to_remaining_seconds(clock: str) -> Optional[float]:
    """
    Supports:
      - '11:32'
      - 'PT11M32.00S' (NBA CDN common)
    Returns remaining seconds in the period.
    """
    if not isinstance(clock, str):
        return None
    s = clock.strip()
    if not s:
        return None

    m1 = _MMSS_RE.match(s)
    if m1:
        mm = int(m1.group(1))
        ss = int(m1.group(2))
        if ss < 0 or ss >= 60 or mm < 0:
            return None
        return float(mm * 60 + ss)

    m2 = _PT_RE.match(s)
    if m2:
        mm = int(m2.group(1))
        ss = float(m2.group(2))
        if ss < 0 or ss >= 60.0 or mm < 0:
            return None
        return float(mm * 60) + ss

    return None


def abs_time_seconds(period: int, clock: str) -> Optional[int]:
    rem = clock_to_remaining_seconds(clock)
    if rem is None:
        return None
    if not isinstance(period, int) or period < 1:
        return None
    elapsed = 720.0 - rem
    t = (period - 1) * 720.0 + elapsed
    # clamp to regulation range (we only render 48 minutes)
    if t < 0:
        t = 0.0
    if t > REG_SECONDS:
        t = float(REG_SECONDS)
    return int(round(t))


def infer_starters_from_pbp(
    actions: List[Dict[str, Any]],
    team_ids: List[int],
    pid_to_team: Dict[int, int],
) -> Dict[int, List[int]]:
    """
    Robust starter inference using substitution timing.

    A player likely STARTED if:
      - they sub OUT before they ever sub IN, OR
      - they never sub IN at all (but exists on team roster)

    Then we pick 5 starters per team using:
      1) started_flag (True first)
      2) earlier first_out_time (starters tend to sub out earlier than bench)
      3) earlier first_action_time (tie-break)
      4) pid
    """
    team_set = set(team_ids)
    roster_by_team: Dict[int, List[int]] = {tid: [] for tid in team_ids}
    for pid, tid in pid_to_team.items():
        if tid in roster_by_team:
            roster_by_team[tid].append(pid)
    for tid in team_ids:
        roster_by_team[tid] = sorted(set(roster_by_team[tid]))

    # First time we ever see ANY action for pid (helps tie-break)
    first_action_t: Dict[int, int] = {}
    # First substitution in/out times
    first_in_t: Dict[int, int] = {}
    first_out_t: Dict[int, int] = {}

    for a in actions:
        pid = _safe_int(a.get("personId"))
        tid = _safe_int(a.get("teamId"))
        if pid is not None and (pid not in first_action_t):
            # Use abs time if possible, else just mark as seen
            period = _safe_int(a.get("period")) or 1
            t = abs_time_seconds(period, _safe_str(a.get("clock")))
            if t is None:
                t = 10**9
            first_action_t[pid] = int(t)

        if not _is_substitution_action(a):
            continue

        # Determine team for this sub action as reliably as possible
        if tid is None and pid is not None:
            tid = pid_to_team.get(pid)
        if tid is None or tid not in team_set:
            continue

        period = _safe_int(a.get("period")) or 1
        t = abs_time_seconds(period, _safe_str(a.get("clock")))
        if t is None:
            continue
        t = int(t)

        pin, pout = _get_sub_in_out(a)
        if pin is not None:
            if pin not in first_in_t:
                first_in_t[pin] = t
        if pout is not None:
            if pout not in first_out_t:
                first_out_t[pout] = t

    starters_by_team: Dict[int, List[int]] = {tid: [] for tid in team_ids}

    for tid in team_ids:
        roster = roster_by_team.get(tid, [])
        if not roster:
            starters_by_team[tid] = []
            continue

        def started_flag(pid: int) -> bool:
            tin = first_in_t.get(pid)
            tout = first_out_t.get(pid)
            # subbed out before ever subbed in -> started
            if tout is not None and (tin is None or tout <= tin):
                return True
            # never subbed in -> very likely started (could also be DNP, but we tie-break with action time)
            if tin is None:
                return True
            return False

        def sort_key(pid: int):
            sf = 1 if started_flag(pid) else 0
            tout = first_out_t.get(pid, 10**9)      # starters who sub out earlier get priority
            tact = first_action_t.get(pid, 10**9)   # tie-break with presence
            return (-sf, tout, tact, pid)

        ranked = sorted(roster, key=sort_key)

        # Pick top 5. This will include Brunson because he won't have a "sub in" before his first "sub out",
        # and his first_action_time will generally be early even if he has few events.
        starters_by_team[tid] = ranked[:5]

    return starters_by_team


def build_rotation_from_pbp(
    actions: List[Dict[str, Any]],
    pid_info: Dict[int, PlayerInfo],
    pid_to_team: Dict[int, int],
    pid_name_map: Dict[int, Tuple[str, str]],
    starters_by_team: Dict[int, List[int]],
    team_ids: List[int],
    notes: List[str],
) -> pd.DataFrame:
    if len(team_ids) < 2:
        notes.append("Could not determine 2 team ids.")
        return empty_rotation_df()

    roster_by_team: Dict[int, List[int]] = {tid: [] for tid in team_ids}
    for pid, tid in pid_to_team.items():
        if tid in roster_by_team:
            roster_by_team[tid].append(pid)
    for tid in team_ids:
        roster_by_team[tid] = sorted(set(roster_by_team[tid]))

    on_court: Dict[int, set[int]] = {tid: set() for tid in team_ids}
    in_time: Dict[Tuple[int, int], int] = {}
    rows: List[Dict[str, Any]] = []

    def name_for(pid: int) -> Tuple[str, str]:
        info = pid_info.get(pid)
        if info is not None and (info.first or info.last):
            return (info.first, info.last)
        if pid in pid_name_map:
            return pid_name_map[pid]
        return ("", "")

    # init at t=0
    for tid in team_ids:
        starters = [pid for pid in (starters_by_team.get(tid) or []) if isinstance(pid, int)]
        if len(starters) < 5:
            for pid in roster_by_team.get(tid, []):
                if pid not in starters:
                    starters.append(pid)
                if len(starters) == 5:
                    break
        starters = starters[:5]
        on_court[tid] = set(starters)
        for pid in starters:
            in_time[(tid, pid)] = 0

    # Build sortable substitution list
    sortable: List[Tuple[int, int, Dict[str, Any]]] = []
    raw_subs: List[Dict[str, Any]] = []
    for i, a in enumerate(actions):
        if not _is_substitution_action(a):
            continue
        raw_subs.append(a)
        period = _safe_int(a.get("period")) or 1
        t = abs_time_seconds(period, _safe_str(a.get("clock")))
        if t is None:
            continue
        sortable.append((t, i, a))
    sortable.sort(key=lambda x: (x[0], x[1]))

    if DEBUG_ROTATION:
        ex = []
        for t, _, a in sortable[:8]:
            pin, pout = _get_sub_in_out(a)
            ex.append({
                "t": t,
                "period": a.get("period"),
                "clock": a.get("clock"),
                "teamId": a.get("teamId"),
                "actionType": a.get("actionType"),
                "subType": a.get("subType"),
                "personId": a.get("personId"),
                "pin": pin,
                "pout": pout,
            })
        print("[diag] first_sub_examples:", ex)

        ok = 0
        for _, _, a in sortable:
            pin, pout = _get_sub_in_out(a)
            if pin is not None or pout is not None:
                ok += 1
        print(f"[diag] subs_with_pin_or_pout={ok} / {len(sortable)}")

    # Apply subs
    def _enforce_exactly_five(tid: int, t: int) -> None:
        """
        STRICT mode:
        Do NOT fabricate players.
        Only trim if somehow >5.
        Never pad.
        """
        on = on_court[tid]

        # If somehow >5, trim deterministically
        if len(on) > 5:
            def start_time(pid: int) -> int:
                return int(in_time.get((tid, pid), t))

            keep = sorted(list(on), key=lambda pid: (start_time(pid), pid))[:5]
            drop = [pid for pid in on if pid not in set(keep)]

            for pid in drop:
                tin = in_time.pop((tid, pid), None)
                if tin is not None and tin < t:
                    f, l = name_for(pid)
                    rows.append(dict(
                        TEAM_ID=tid,
                        PERSON_ID=pid,
                        PLAYER_FIRST=f,
                        PLAYER_LAST=l,
                        IN_TIME_REAL=int(tin),
                        OUT_TIME_REAL=int(t)
                    ))
                on.remove(pid)

    def _flush_batch(batch_tid: int, batch_t: int, batch_actions: List[Dict[str, Any]]) -> None:
        if batch_tid not in on_court:
            return

        outs: List[int] = []
        ins: List[int] = []
        for a in batch_actions:
            pin, pout = _get_sub_in_out(a)
            if pout is not None:
                outs.append(pout)
            if pin is not None:
                ins.append(pin)

        # 1) OUT first: remove + close stints
        for pid in outs:
            if pid in on_court[batch_tid]:
                on_court[batch_tid].remove(pid)
                tin = in_time.pop((batch_tid, pid), None)
                if tin is not None and tin < batch_t:
                    f, l = name_for(pid)
                    rows.append(dict(
                        TEAM_ID=batch_tid, PERSON_ID=pid, PLAYER_FIRST=f, PLAYER_LAST=l,
                        IN_TIME_REAL=int(tin), OUT_TIME_REAL=int(batch_t)
                    ))

        # 2) IN after OUT: add + open stints
        for pid in ins:
            if pid not in on_court[batch_tid]:
                on_court[batch_tid].add(pid)
                in_time[(batch_tid, pid)] = batch_t

        # 3) Defensive enforce 5
        _enforce_exactly_five(batch_tid, batch_t)

        if DEBUG_ROTATION and (outs or ins):
            print(f"[subs] t={batch_t} teamId={batch_tid} outs={len(outs)} ins={len(ins)} on_court={len(on_court[batch_tid])}")

    # Apply subs in true (t, teamId) batches (robust to interleaving)
    batches: Dict[Tuple[int, int], List[Dict[str, Any]]] = {}
    for t, _, a in sortable:
        tid = _safe_int(a.get("teamId"))
        if tid is None or tid not in on_court:
            continue
        batches.setdefault((t, tid), []).append(a)

    for (t, tid) in sorted(batches.keys(), key=lambda k: (k[0], k[1])):
        _flush_batch(tid, t, batches[(t, tid)])

    # close end of regulation
    for (tid, pid), tin in list(in_time.items()):
        f, l = name_for(pid)
        rows.append(dict(TEAM_ID=tid, PERSON_ID=pid, PLAYER_FIRST=f, PLAYER_LAST=l, IN_TIME_REAL=int(tin), OUT_TIME_REAL=int(REG_SECONDS)))

    df = ensure_rotation_schema(pd.DataFrame(rows))
    if not df.empty:
        df = df[df["OUT_TIME_REAL"] > df["IN_TIME_REAL"]].copy()
    return ensure_rotation_schema(df)


def split_home_away(df: pd.DataFrame, meta: Dict[str, Any], notes: List[str]) -> Tuple[pd.DataFrame, pd.DataFrame]:
    df = ensure_rotation_schema(df)
    home_id = int(meta.get("home_team_id") or 0)
    away_id = int(meta.get("away_team_id") or 0)
    if home_id and away_id:
        return df[df["TEAM_ID"] == home_id].copy(), df[df["TEAM_ID"] == away_id].copy()
    tids = df["TEAM_ID"].dropna().unique().tolist()
    if len(tids) >= 2:
        notes.append("Home/away inferred from rotation TEAM_IDs.")
        return df[df["TEAM_ID"] == int(tids[0])].copy(), df[df["TEAM_ID"] == int(tids[1])].copy()
    notes.append("Could not split home/away.")
    return empty_rotation_df(), empty_rotation_df()


def fetch_game_rotation(game_id: str) -> Tuple[pd.DataFrame, pd.DataFrame, Dict[str, Any]]:
    notes: List[str] = []

    pbp = fetch_json(CDN_PBP.format(GAME_ID=game_id), cache_key=f"pbp_{game_id}", ttl_sec=10, stale_sec=DEFAULT_STALE_SEC)
    actions = iter_pbp_actions(pbp)

    if DEBUG_ROTATION:
        sub_cnt = sum(1 for a in actions if "sub" in str(a.get("actionType", "")).lower())
        print(f"[diag] actions={len(actions)} subs_by_actionType={sub_cnt}")

    if not actions:
        notes.append("No play-by-play actions available.")
        meta = {"home_team_id": 0, "away_team_id": 0, "home_abbr": "", "away_abbr": "", "notes": notes}
        return empty_rotation_df(), empty_rotation_df(), meta

    box = fetch_json(CDN_BOXSCORE.format(GAME_ID=game_id), cache_key=f"box_{game_id}", ttl_sec=20, stale_sec=DEFAULT_STALE_SEC)
    meta = parse_boxscore_meta(box)

    pid_info, starters_by_team = parse_boxscore_players(box)
    pid_to_team = build_person_team_map_from_pbp(actions)
    pid_name_map = build_person_name_map_from_pbp(actions)

    team_ids: List[int] = []
    if meta.get("home_team_id"):
        team_ids.append(int(meta["home_team_id"]))
    if meta.get("away_team_id") and int(meta["away_team_id"]) not in team_ids:
        team_ids.append(int(meta["away_team_id"]))
    if len(team_ids) < 2:
        tids = []
        for a in actions:
            tid = _safe_int(a.get("teamId"))
            if tid is not None and tid not in tids:
                tids.append(tid)
        team_ids = tids[:2]

    need_infer = any(len(starters_by_team.get(tid) or []) != 5 for tid in team_ids)
    if need_infer:
        inferred = infer_starters_from_pbp(actions, team_ids, pid_to_team)
        for tid in team_ids:
            if len(starters_by_team.get(tid) or []) != 5:
                starters_by_team[tid] = inferred.get(tid, [])[:5]
        notes.append("Used PBP starter inference (boxscore starters incomplete).")

    df = build_rotation_from_pbp(
        actions=actions,
        pid_info=pid_info,
        pid_to_team=pid_to_team,
        pid_name_map=pid_name_map,
        starters_by_team=starters_by_team,
        team_ids=team_ids,
        notes=notes,
    )

    if DEBUG_ROTATION and df is not None and isinstance(df, pd.DataFrame) and not df.empty:
        print(
            f"[diag] rotation_df rows={len(df)} players={df['PERSON_ID'].nunique()} "
            f"distinct_IN={df['IN_TIME_REAL'].nunique()} distinct_OUT={df['OUT_TIME_REAL'].nunique()}"
        )

    home_df, away_df = split_home_away(df, meta, notes)
    meta = dict(meta)
    meta["notes"] = notes
    return ensure_rotation_schema(home_df), ensure_rotation_schema(away_df), meta
