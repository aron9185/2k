#!/usr/bin/env python3
"""
rotation_pos_by_lineup.py

For each regulation minute (0..47) and team:
- Determine the 5 players "on court" for that minute (by seconds played in that minute; take top 5)
- Assign PG/SG/SF/PF/C uniquely among those 5 by maximizing Position Estimate percentages
- Color each played minute block by the assigned position that minute
- Not played => white
- Sort players top->bottom by total minutes played (union seconds / 60)
- Save HTML to ./html/

Dependencies:
  pip install nba_api pandas numpy plotly

SportsRef Position Estimate (HTML .xls) must contain columns:
  Team, Player, PG%, SG%, SF%, PF%, C%

Run:
  python rotation_pos_by_lineup.py --game_id 0022500200 --side both --pos_xls /mnt/data/sportsref_download.xls
"""

from __future__ import annotations

import argparse
import itertools
import os
import re
from dataclasses import dataclass
from typing import Dict, List, Tuple, Optional

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import unicodedata

from nba_api.stats.endpoints import gamerotation
from nba_api.stats.static import teams as static_teams

REG_SECONDS = 48 * 60
POS_LIST = ["PG", "SG", "SF", "PF", "C"]
POS_TO_CODE = {"PG": 1, "SG": 2, "SF": 3, "PF": 4, "C": 5}

# PG (red), SG(green), SF(yellow), PF(blue), C(orange)
DISCRETE_COLORSCALE = [
    [0.0, "white"], [0.1666, "white"],
    [0.1667, "#8B0000"], [0.3333, "#8B0000"],   # PG dark red
    [0.3334, "#006400"], [0.5, "#006400"],     # SG dark green
    [0.5001, "#B8860B"], [0.6666, "#B8860B"],   # SF dark yellow (goldenrod)
    [0.6667, "#00008B"], [0.8333, "#00008B"],   # PF dark blue
    [0.8334, "#CC5500"], [1.0, "#CC5500"],      # C dark orange
]

_TEAM_ABBR_CANON = {
    "BRK": "BKN",
    "BKN": "BKN",
    "PHO": "PHX",
    "PHX": "PHX",
    "CHO": "CHA",
    "CHA": "CHA",
    "NOH": "NOP",
    "NOK": "NOP",
    "NOP": "NOP",
}

def team_abbr_aliases(abbr: str) -> List[str]:
    a = str(abbr or "").strip().upper()
    if not a:
        return []
    canon = _TEAM_ABBR_CANON.get(a, a)
    out = [a]
    if canon not in out:
        out.append(canon)
    for k, v in _TEAM_ABBR_CANON.items():
        if v == canon and k not in out:
            out.append(k)
    return out

_MMSS_RE = re.compile(r"^\s*(\d{1,2}):(\d{2})\s*$")

def _deaccent(s: str) -> str:
    try:
        return "".join(ch for ch in unicodedata.normalize("NFKD", s) if not unicodedata.combining(ch))
    except Exception:
        return s

def norm_name(s: str) -> str:
    s = str(s or "")
    s = _deaccent(s)
    s = s.lower().strip()
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    s = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

def team_abbr_from_team_id(team_id: int) -> Optional[str]:
    info = static_teams.find_team_name_by_id(team_id)
    return info.get("abbreviation") if info else None


# -----------------------------
# Load SportsRef position-estimate lookup
# key: (TEAM_ABBR, norm_player_name) -> dict(pos -> percent)
# -----------------------------
_POS_COLS = ["PG%", "SG%", "SF%", "PF%", "C%"]

# -----------------------------
# Rotation stints from nba_api GameRotation
# Your schema: IN_TIME_REAL/OUT_TIME_REAL in tenths of seconds (0..28800)
# We auto-scale to seconds.
# -----------------------------
@dataclass(frozen=True)
class Stint:
    player_id: int
    player_name: str
    team_id: int
    start_s: float
    end_s: float

def fetch_game_rotation(game_id: str, league_id: str = "00") -> Tuple[pd.DataFrame, pd.DataFrame]:
    gr = gamerotation.GameRotation(game_id=game_id, league_id=league_id)
    away = gr.get_data_frames()[0]
    home = gr.get_data_frames()[1]
    return home, away

def df_to_stints_real(df: pd.DataFrame) -> List[Stint]:
    required = {"TEAM_ID", "PERSON_ID", "PLAYER_FIRST", "PLAYER_LAST", "IN_TIME_REAL", "OUT_TIME_REAL"}
    missing = required - set(df.columns)
    if missing:
        raise RuntimeError(f"Missing required columns: {sorted(missing)}")

    max_out = float(pd.to_numeric(df["OUT_TIME_REAL"], errors="coerce").max())
    scale = 10.0 if max_out > 10000 else 1.0  # 28800 => tenths

    stints: List[Stint] = []
    for _, r in df.iterrows():
        team_id = int(r["TEAM_ID"])
        pid = int(r["PERSON_ID"])
        name = f"{r['PLAYER_FIRST']} {r['PLAYER_LAST']}".strip()
        try:
            start = float(r["IN_TIME_REAL"]) / scale
            end = float(r["OUT_TIME_REAL"]) / scale
        except Exception:
            continue

        start = max(0.0, min(float(REG_SECONDS), start))
        end = max(0.0, min(float(REG_SECONDS), end))
        if end <= start:
            continue

        stints.append(Stint(pid, name, team_id, start, end))
    return stints


# -----------------------------
# Merge intervals (union) per player
# -----------------------------
def merge_intervals(intervals: List[Tuple[float, float]], eps: float = 1e-6) -> List[Tuple[float, float]]:
    if not intervals:
        return []
    intervals = sorted(intervals, key=lambda x: (x[0], x[1]))
    merged = [intervals[0]]
    for s, e in intervals[1:]:
        ps, pe = merged[-1]
        if s <= pe + eps:
            merged[-1] = (ps, max(pe, e))
        else:
            merged.append((s, e))
    return merged


# -----------------------------
# Seconds-in-minute
# -----------------------------
def overlap_seconds(a0: float, a1: float, b0: float, b1: float) -> float:
    return max(0.0, min(a1, b1) - max(a0, b0))

def seconds_played_in_minute(intervals: List[Tuple[float, float]], minute_idx: int) -> float:
    m0 = minute_idx * 60.0
    m1 = (minute_idx + 1) * 60.0
    return sum(overlap_seconds(s, e, m0, m1) for s, e in intervals)


# -----------------------------
# Assignment: choose unique PG/SG/SF/PF/C for 5 players each minute
# Maximize sum of position percentages.
# No SciPy: brute-force 120 permutations.
# -----------------------------
def best_position_assignment(
    players: List[int],
    perc_by_pid: Dict[int, Dict[str, float]],
) -> Dict[int, str]:
    """
    players: list of 5 player_ids
    returns: mapping pid -> assigned position
    """
    best_score = -1.0
    best_perm = None

    for perm in itertools.permutations(POS_LIST, 5):
        score = 0.0
        ok = True
        for pid, pos in zip(players, perm):
            perc = perc_by_pid.get(pid)
            if perc is None:
                # if missing, treat as 0
                score += 0.0
            else:
                score += float(perc.get(pos, 0.0))
        if score > best_score:
            best_score = score
            best_perm = perm

    assert best_perm is not None
    return {pid: pos for pid, pos in zip(players, best_perm)}


# -----------------------------
# Build final 48xN code matrix
# code per cell:
# 0 = white (not played or not in top-5 that minute)
# 1..5 = position color (PG..C)
# -----------------------------
def build_team_matrix(
    stints: List[Stint],
    pos_lookup: Dict[Tuple[str, str], Dict[str, float]],
    threshold_sec: float = 30.0,
    debug: bool = False,
) -> pd.DataFrame:
    # collect merged intervals + identity
    by_pid: Dict[int, Dict[str, object]] = {}
    for s in stints:
        if s.player_id not in by_pid:
            by_pid[s.player_id] = {
                "name": s.player_name,
                "team_id": s.team_id,
                "intervals": [],
            }
        by_pid[s.player_id]["intervals"].append((s.start_s, s.end_s))

    for pid in list(by_pid.keys()):
        by_pid[pid]["intervals"] = merge_intervals(by_pid[pid]["intervals"])

    # lookup perc by pid
    team_id = int(next(iter(by_pid.values()))["team_id"]) if by_pid else 0
    team_abbr = team_abbr_from_team_id(team_id) or ""
    perc_by_pid: Dict[int, Dict[str, float]] = {}
    for pid, info in by_pid.items():
        nkey = norm_name(info["name"])
        perc = None
        for ta in team_abbr_aliases(team_abbr):
            perc = pos_lookup.get((ta, nkey))
            if perc:
                break
        perc_by_pid[pid] = perc or {"PG":0,"SG":0,"SF":0,"PF":0,"C":0}

    # total minutes played for sorting
    total_sec_by_pid: Dict[int, float] = {}
    for pid, info in by_pid.items():
        # exact union seconds
        total_sec_by_pid[pid] = sum(e - s for s, e in info["intervals"])

    # initialize per-player per-minute codes
    codes = {pid: np.zeros(48, dtype=int) for pid in by_pid.keys()}

    # per minute: choose top 5 by seconds, assign positions uniquely, paint tiles
    for m in range(48):
        secs_this_min: List[Tuple[int, float]] = []
        for pid, info in by_pid.items():
            sec = seconds_played_in_minute(info["intervals"], m)
            if sec > 0:
                secs_this_min.append((pid, sec))

        if not secs_this_min:
            continue

        # "on court" set: prefer players with > threshold, but enforce exactly 5
        # 1) candidates with sec > threshold
        on = [(pid, sec) for pid, sec in secs_this_min if sec > threshold_sec]
        if len(on) < 5:
            # fill with next-best seconds until 5
            rest = sorted([x for x in secs_this_min if x[0] not in {p for p, _ in on}], key=lambda x: x[1], reverse=True)
            on = sorted(on, key=lambda x: x[1], reverse=True) + rest
        on = sorted(on, key=lambda x: x[1], reverse=True)[:5]

        pids5 = [pid for pid, _ in on]
        assign = best_position_assignment(pids5, perc_by_pid)

        # paint only those who played > threshold this minute
        for pid, sec in secs_this_min:
            if sec > threshold_sec:
                pos = assign.get(pid)
                if pos:
                    codes[pid][m] = POS_TO_CODE[pos]
                else:
                    codes[pid][m] = 0
            else:
                codes[pid][m] = 0

        if debug and m in (0, 12, 24, 36):
            names5 = [by_pid[pid]["name"] for pid in pids5]
            print(f"Minute {m:02d}: top5={names5} assign={ {by_pid[pid]['name']:assign[pid] for pid in pids5} }")

    # build df
    rows = []
    for pid, info in by_pid.items():
        row = {
            "player_id": pid,
            "player_name": info["name"],
            "team_abbr": team_abbr,
            "total_minutes": total_sec_by_pid[pid] / 60.0,
        }
        for m in range(48):
            row[f"m{m:02d}"] = int(codes[pid][m])
        rows.append(row)

    df = pd.DataFrame(rows)
    if df.empty:
        return df

    # sort most minutes at top
    df = df.sort_values(["total_minutes", "player_name"], ascending=[False, True]).reset_index(drop=True)
    return df


def make_heatmap(df: pd.DataFrame, title: str) -> go.Figure:
    minute_cols = [f"m{i:02d}" for i in range(48)]
    z = df[minute_cols].to_numpy()

    y = df["player_name"].tolist()

    # x 軸用數值型態
    # 格子中心在 0.5,1.5,...,47.5
    x_centers = np.arange(48) + 0.5

    fig = go.Figure(
        data=go.Heatmap(
            z=z,
            x=x_centers,
            y=y,
            zmin=0,
            zmax=5,
            colorscale=DISCRETE_COLORSCALE,
            xgap=1,
            ygap=1,
            showscale=True,
            colorbar=dict(
                title="Position",
                tickmode="array",
                tickvals=[0,1,2,3,4,5],
                ticktext=["Off","PG","SG","SF","PF","C"]
            ),
            hovertemplate="Player=%{y}<br>Minute=%{x}<extra></extra>",
        )
    )

    fig.update_layout(
        title=title,
        height=max(450, 20 * len(y)),
        margin=dict(l=180, r=30, t=60, b=80),
        paper_bgcolor="white",
        plot_bgcolor="white"
    )

    # 設定 X 軸為數值型態
    fig.update_xaxes(
        range=[0, 48],
        tickmode="array",
        tickvals=[12, 24, 36],
        ticktext=["12", "24", "36"],
        showgrid=False,
        showline=True,
        linecolor="black",
        ticks="outside",
        title_text="Minute (1–48)"
    )

    # Y 軸
    fig.update_yaxes(
        autorange="reversed",
        showgrid=False,
        title_text="Player (most minutes at top)"
    )

    # 粗線畫在真正的格子邊界
    for q in [12, 24, 36]:
        fig.add_vline(
            x=q,
            line_width=4,
            line_color="black",
            opacity=1.0
        )

    return fig


def ensure_dir(p: str) -> None:
    os.makedirs(p, exist_ok=True)

def write_figs_to_html(figs: List[go.Figure], out_path: str) -> None:
    if len(figs) == 1:
        figs[0].write_html(out_path, include_plotlyjs="cdn")
        return
    parts = []
    for i, fig in enumerate(figs):
        parts.append(fig.to_html(full_html=False, include_plotlyjs=("cdn" if i == 0 else False)))
    full = "<html><head><meta charset='utf-8'></head><body>" + "\n<hr/>\n".join(parts) + "</body></html>"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(full)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--game_id", required=True)
    ap.add_argument("--league_id", default="00")
    ap.add_argument("--side", choices=["home", "away", "both"], default="both")
    ap.add_argument("--threshold", type=float, default=30.0, help="played if seconds in minute > threshold")
    ap.add_argument("--out_dir", default="./html")
    ap.add_argument("--pos_xls", default="./sportsref_download.xls",
                    help="SportsRef download (HTML .xls) with Position Estimate PG%..C%")
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args()

    pos_lookup = load_position_estimate(args.pos_xls)
    ensure_dir(args.out_dir)

    home_df, away_df = fetch_game_rotation(args.game_id, args.league_id)
    figs: List[go.Figure] = []

    if args.side in ("home", "both"):
        stints = df_to_stints_real(home_df)
        mat = build_team_matrix(stints, pos_lookup, threshold_sec=args.threshold, debug=args.debug)
        team_abbr = mat["team_abbr"].iloc[0] if len(mat) else "HOME"
        figs.append(make_heatmap(mat, f"{args.game_id} — {team_abbr} (minute-wise positions)"))

    if args.side in ("away", "both"):
        stints = df_to_stints_real(away_df)
        mat = build_team_matrix(stints, pos_lookup, threshold_sec=args.threshold, debug=args.debug)
        team_abbr = mat["team_abbr"].iloc[0] if len(mat) else "AWAY"
        figs.append(make_heatmap(mat, f"{args.game_id} — {team_abbr} (minute-wise positions)"))

    out_path = os.path.join(args.out_dir, f"rotation_{args.game_id}_lineup_pos48.html")
    write_figs_to_html(figs, out_path)
    print(f"Wrote: {out_path}")


if __name__ == "__main__":
    main()
