# rotation_plot.py
from __future__ import annotations

import itertools
import os
import re
import sys
import unicodedata
from dataclasses import dataclass
from typing import Dict, List, Tuple, Optional, Any

import numpy as np
import pandas as pd
import plotly.graph_objects as go

REG_SECONDS = 48 * 60
POS_LIST = ["PG", "SG", "SF", "PF", "C"]
POS_TO_CODE = {"PG": 1, "SG": 2, "SF": 3, "PF": 4, "C": 5}
TEAM_ALIASES = {
    "ATL": {"ATL"},
    "BKN": {"BKN", "BRK", "NJN"},
    "BOS": {"BOS"},
    "CHA": {"CHA", "CHO", "CHH"},
    "CHI": {"CHI"},
    "CLE": {"CLE"},
    "DAL": {"DAL"},
    "DEN": {"DEN"},
    "DET": {"DET"},
    "GSW": {"GSW", "GS"},
    "HOU": {"HOU"},
    "IND": {"IND"},
    "LAC": {"LAC", "LACL", "SDC"},
    "LAL": {"LAL"},
    "MEM": {"MEM", "VAN"},
    "MIA": {"MIA"},
    "MIL": {"MIL"},
    "MIN": {"MIN"},
    "NOP": {"NOP", "NOH", "NOK"},
    "NYK": {"NYK"},
    "OKC": {"OKC", "SEA"},
    "ORL": {"ORL"},
    "PHI": {"PHI"},
    "PHX": {"PHX", "PHO"},
    "POR": {"POR"},
    "SAC": {"SAC", "KCK"},
    "SAS": {"SAS", "SA"},
    "TOR": {"TOR"},
    "UTA": {"UTA", "UTH"},
    "WAS": {"WAS", "WSB"},
}
TEAM_ALIAS_TO_CANONICAL = {
    alias: canonical
    for canonical, aliases in TEAM_ALIASES.items()
    for alias in aliases
}

# PG (red), SG(green), SF(yellow), PF(blue), C(orange)
DISCRETE_COLORSCALE = [
    [0.0, "white"], [0.1666, "white"],
    [0.1667, "#8B0000"], [0.3333, "#8B0000"],   # PG dark red
    [0.3334, "#006400"], [0.5, "#006400"],      # SG dark green
    [0.5001, "#B8860B"], [0.6666, "#B8860B"],   # SF dark goldenrod
    [0.6667, "#00008B"], [0.8333, "#00008B"],   # PF dark blue
    [0.8334, "#CC5500"], [1.0, "#CC5500"],      # C dark orange
]


def _safe_print(*parts: Any) -> None:
    text = " ".join(str(part) for part in parts)
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    safe = text.encode(encoding, errors="backslashreplace").decode(encoding, errors="replace")
    print(safe)


def norm_name(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s))
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.lower().strip()
    s = re.sub(r"[^\w\s]", " ", s)  # remove punctuation
    s = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b", " ", s)  # remove suffix
    s = re.sub(r"\s+", " ", s).strip()
    return s


def canonical_team_abbr(team: str) -> str:
    return TEAM_ALIAS_TO_CANONICAL.get(str(team or "").strip().upper(), str(team or "").strip().upper())


def _to_float(x: Any) -> float:
    """
    SportsRef exports often have ints/strings for percents.
    This makes parsing robust and never raises.
    """
    try:
        if x is None:
            return 0.0
        if isinstance(x, (int, float, np.integer, np.floating)):
            return float(x)
        s = str(x).strip()
        if not s or s.lower() == "nan":
            return 0.0
        # strip '%' if present
        s = s.replace("%", "").strip()
        return float(s)
    except Exception:
        return 0.0


def _normalize_columns(cols: List[Any]) -> List[str]:
    out = []
    for c in cols:
        s = str(c).strip()
        s = re.sub(r"\s+", " ", s)
        out.append(s)
    return out


def load_position_estimate(path_xls: str) -> Dict[Tuple[str, str], Dict[str, float]]:
    """
    Loads SportsRef Position Estimate table from HTML .xls.
    Handles the common 2-row header layout:
      row0: banner like "Position Estimate"
      row1: real header with Player/Team/PG%..C%

    Returns:
      (TEAM_ABBR, norm_player_name) -> {PG,SG,SF,PF,C} in perc (float).
    """
    if not os.path.exists(path_xls):
        raise FileNotFoundError(f"Position estimate file not found: {path_xls}")

    tables = pd.read_html(path_xls)
    if not tables:
        raise RuntimeError("No tables found in position estimate file.")

    df = tables[0].copy()

    # Case A: pandas already parsed proper headers
    df.columns = _normalize_columns(list(df.columns))

    def has_required_columns(d: pd.DataFrame) -> bool:
        cols = set(_normalize_columns(list(d.columns)))
        # SportsRef sometimes uses Team or Tm
        has_team = ("Team" in cols) or ("Tm" in cols)
        needed = {"Player", "PG%", "SG%", "SF%", "PF%", "C%"}
        return has_team and needed.issubset(cols)

    # Case B: common SportsRef export: integer columns 0..N and real header in row 1
    # Detect: columns look like 0..24 AND row 1 contains "Player" and "Team"/"Tm" and "PG%"
    if not has_required_columns(df):
        try:
            if len(df) >= 3:
                row1 = _normalize_columns(list(df.iloc[1].tolist()))
                if ("Player" in row1) and (("Team" in row1) or ("Tm" in row1)) and ("PG%" in row1):
                    df2 = df.copy()
                    df2.columns = _normalize_columns(list(df2.iloc[1].tolist()))
                    df2 = df2.iloc[2:].reset_index(drop=True)
                    df = df2
        except Exception:
            pass

    if not has_required_columns(df):
        # Provide a helpful diagnostic of what we *did* see
        cols_preview = _normalize_columns(list(df.columns))[:30]
        raise RuntimeError(f"Position estimate missing required columns. Saw columns: {cols_preview}")

    cols = _normalize_columns(list(df.columns))
    # Support "Team" or "Tm"
    team_col = "Team" if "Team" in cols else "Tm"
    player_col = "Player"

    out: Dict[Tuple[str, str], Dict[str, float]] = {}

    for _, r in df.iterrows():
        team = canonical_team_abbr(str(r.get(team_col, "") or "").strip())
        player = str(r.get(player_col, "") or "").strip()
        if not team or not player:
            continue

        payload = {
            "PG": _to_float(r.get("PG%", 0.0)),
            "SG": _to_float(r.get("SG%", 0.0)),
            "SF": _to_float(r.get("SF%", 0.0)),
            "PF": _to_float(r.get("PF%", 0.0)),
            "C":  _to_float(r.get("C%", 0.0)),
        }
        for alias in TEAM_ALIASES.get(team, {team}):
            key = (alias, norm_name(player))
            out[key] = payload.copy()

    return out


@dataclass(frozen=True)
class Stint:
    player_id: int
    player_name: str
    team_id: int
    start_s: float
    end_s: float


def df_to_stints_real(df: pd.DataFrame) -> List[Stint]:
    required = {"TEAM_ID", "PERSON_ID", "PLAYER_FIRST", "PLAYER_LAST", "IN_TIME_REAL", "OUT_TIME_REAL"}
    if df is None or not isinstance(df, pd.DataFrame):
        return []
    if not required.issubset(set(df.columns)):
        return []

    stints: List[Stint] = []
    for _, r in df.iterrows():
        try:
            team_id = int(r["TEAM_ID"])
            pid = int(r["PERSON_ID"])
        except Exception:
            continue

        first = str(r.get("PLAYER_FIRST", "") or "")
        last = str(r.get("PLAYER_LAST", "") or "")
        name = f"{first} {last}".strip()

        try:
            start = float(r["IN_TIME_REAL"])
            end = float(r["OUT_TIME_REAL"])
        except Exception:
            continue

        start = max(0.0, min(float(REG_SECONDS), start))
        end = max(0.0, min(float(REG_SECONDS), end))
        if end <= start:
            continue
        stints.append(Stint(pid, name, team_id, start, end))

    return stints


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


def overlap_seconds(a0: float, a1: float, b0: float, b1: float) -> float:
    return max(0.0, min(a1, b1) - max(a0, b0))


def seconds_played_in_minute(intervals: List[Tuple[float, float]], minute_idx: int) -> float:
    m0 = minute_idx * 60.0
    m1 = (minute_idx + 1) * 60.0
    return sum(overlap_seconds(s, e, m0, m1) for s, e in intervals)


def best_position_assignment(players5: List[int], perc_by_pid: Dict[int, Dict[str, float]]) -> Dict[int, str]:
    best_score = -1e18
    best_perm = None
    ZERO_PENALTY = 1000.0  # avoid 0% if possible

    for perm in itertools.permutations(POS_LIST, 5):
        score = 0.0
        zeros = 0
        for pid, pos in zip(players5, perm):
            p = perc_by_pid.get(pid) or {}
            v = float(p.get(pos, 0.0))
            if v <= 0.0:
                zeros += 1
            score += v
        score = score - ZERO_PENALTY * zeros
        if score > best_score:
            best_score = score
            best_perm = perm

    if best_perm is None:
        best_perm = POS_LIST
    return {pid: pos for pid, pos in zip(players5, best_perm)}


def build_team_matrix(
    stints: List[Stint],
    team_abbr: str,
    pos_lookup: Dict[Tuple[str, str], Dict[str, float]],
    debug: bool = False,
) -> pd.DataFrame:
    """
    Enforces:
    - For each minute 0..47, ALWAYS choose exactly 5 players (repair if needed)
    - Color EXACTLY those 5 players for that minute
    """
    by_pid: Dict[int, Dict[str, Any]] = {}
    for s in stints:
        by_pid.setdefault(s.player_id, {"name": s.player_name, "team_id": s.team_id, "intervals": []})
        by_pid[s.player_id]["intervals"].append((s.start_s, s.end_s))

    for pid in list(by_pid.keys()):
        by_pid[pid]["intervals"] = merge_intervals(by_pid[pid]["intervals"])

    if not by_pid:
        return pd.DataFrame()

    perc_by_pid: Dict[int, Dict[str, float]] = {}
    for pid, info in by_pid.items():
        key = (team_abbr, norm_name(info["name"]))
        perc_by_pid[pid] = pos_lookup.get(key, {"PG": 0, "SG": 0, "SF": 0, "PF": 0, "C": 0})

    total_sec_by_pid = {pid: sum(e - s for s, e in info["intervals"]) for pid, info in by_pid.items()}
    codes = {pid: np.zeros(48, dtype=int) for pid in by_pid.keys()}

    pid_order = sorted(by_pid.keys(), key=lambda pid: (-total_sec_by_pid.get(pid, 0.0), str(by_pid[pid]["name"]), pid))
    last_lineup: Optional[List[int]] = None

    for m in range(48):
        secs = []
        for pid, info in by_pid.items():
            sec = seconds_played_in_minute(info["intervals"], m)
            if sec > 0:
                secs.append((pid, sec))

        secs.sort(key=lambda x: (-x[1], x[0]))
        lineup = [pid for pid, _ in secs[:5]]

        if len(lineup) < 5:
            if last_lineup:
                for pid in last_lineup:
                    if pid not in lineup:
                        lineup.append(pid)
                    if len(lineup) == 5:
                        break
            for pid in pid_order:
                if pid not in lineup:
                    lineup.append(pid)
                if len(lineup) == 5:
                    break

        lineup = lineup[:5]
        last_lineup = lineup

        assign = best_position_assignment(lineup, perc_by_pid)

        for pid in by_pid.keys():
            codes[pid][m] = 0
        for pid in lineup:
            codes[pid][m] = POS_TO_CODE.get(assign.get(pid, ""), 0)

        if debug and m in (0, 12, 24, 36):
            _safe_print(
                f"[matrix] minute={m} lineup={[by_pid[p]['name'] for p in lineup]} "
                f"assign={{ {', '.join(by_pid[p]['name'] + ':' + assign[p] for p in lineup)} }}"
            )

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
    df = df.sort_values(["total_minutes", "player_name"], ascending=[False, True]).reset_index(drop=True)

    if debug:
        missing = []
        missing_keys = []

        for pid, perc in perc_by_pid.items():
            if max(perc.values()) == 0:
                name = str(by_pid.get(pid, {}).get("name", f"pid={pid}"))
                missing.append(name)
                missing_keys.append((team_abbr, norm_name(name)))

        hit = len(perc_by_pid) - len(missing)
        total = len(perc_by_pid)

        _safe_print(f"[pos] team={team_abbr} matched={hit}/{total}")

        if missing:
            _safe_print(f"[pos] team={team_abbr} MISSING {len(missing)} players:")
            for name, key in sorted(zip(missing, missing_keys), key=lambda x: x[0]):
                _safe_print(f"       - {name}  key={key}")

    return df


def make_heatmap(df: pd.DataFrame, title: str) -> go.Figure:
    if df is None or df.empty:
        fig = go.Figure()
        fig.update_layout(
            title=f"{title} (unavailable)",
            xaxis=dict(range=[0, 48], tickvals=[0, 12, 24, 36, 48], ticktext=["0", "12", "24", "36", "48"]),
            yaxis=dict(visible=False),
            height=420,
            margin=dict(l=30, r=30, t=60, b=60),
        )
        return fig

    minute_cols = [f"m{i:02d}" for i in range(48)]
    z = df[minute_cols].to_numpy()
    y = df["player_name"].tolist()
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
                tickvals=[0, 1, 2, 3, 4, 5],
                ticktext=["Off", "PG", "SG", "SF", "PF", "C"],
            ),
            hovertemplate="Player=%{y}<br>Minute=%{x}<extra></extra>",
        )
    )

    fig.update_layout(
        title=title,
        height=max(450, 20 * len(y)),
        margin=dict(l=180, r=30, t=60, b=80),
        paper_bgcolor="white",
        plot_bgcolor="white",
    )

    fig.update_xaxes(
        range=[0, 48],
        tickmode="array",
        tickvals=[0, 12, 24, 36, 48],
        ticktext=["0", "12", "24", "36", "48"],
        showgrid=False,
        showline=True,
        linecolor="black",
        ticks="outside",
        title_text="Minute (0–48)",
    )
    fig.update_yaxes(autorange="reversed", showgrid=False, title_text="Player (most minutes at top)")

    for q in [12, 24, 36]:
        fig.add_vline(x=q, line_width=4, line_color="black", opacity=1.0)

    return fig


def figs_to_html(figs: List[go.Figure]) -> str:
    if not figs:
        return "<div>Rotation unavailable.</div>"
    parts = []
    for i, fig in enumerate(figs):
        parts.append(fig.to_html(full_html=False, include_plotlyjs=("cdn" if i == 0 else False)))
    return "\n<hr/>\n".join(parts)


def build_game_figs(
    game_id: str,
    pos_lookup: Dict[Tuple[str, str], Dict[str, float]],
    meta: Optional[Dict[str, Any]] = None,
    home_df: Optional[pd.DataFrame] = None,
    away_df: Optional[pd.DataFrame] = None,
    debug: bool = False,
) -> List[go.Figure]:
    meta = meta or {}
    home_abbr = str(meta.get("home_abbr") or "HOME").strip() or "HOME"
    away_abbr = str(meta.get("away_abbr") or "AWAY").strip() or "AWAY"

    figs: List[go.Figure] = []

    if home_df is not None and isinstance(home_df, pd.DataFrame) and not home_df.empty:
        stints = df_to_stints_real(home_df)
        mat = build_team_matrix(stints, team_abbr=home_abbr, pos_lookup=pos_lookup, debug=debug)
        figs.append(make_heatmap(mat, f"{game_id} — {home_abbr} (minute-wise positions)"))
    else:
        figs.append(make_heatmap(pd.DataFrame(), f"{game_id} — {home_abbr} (minute-wise positions)"))

    if away_df is not None and isinstance(away_df, pd.DataFrame) and not away_df.empty:
        stints = df_to_stints_real(away_df)
        if debug:
            debug_minute_lineups(stints, away_abbr)
        mat = build_team_matrix(stints, team_abbr=away_abbr, pos_lookup=pos_lookup, debug=debug)
        figs.append(make_heatmap(mat, f"{game_id} — {away_abbr} (minute-wise positions)"))
    else:
        figs.append(make_heatmap(pd.DataFrame(), f"{game_id} — {away_abbr} (minute-wise positions)"))

    return figs

def debug_minute_lineups(stints: List[Stint], team_abbr: str, minutes: List[int] | None = None) -> None:
    if minutes is None:
        minutes = [0, 1, 2, 10, 11, 12, 23, 24, 35, 36, 47]

    by_pid: Dict[int, Dict[str, Any]] = {}
    for s in stints:
        by_pid.setdefault(s.player_id, {"name": s.player_name, "intervals": []})
        by_pid[s.player_id]["intervals"].append((s.start_s, s.end_s))

    for pid in list(by_pid.keys()):
        by_pid[pid]["intervals"] = merge_intervals(by_pid[pid]["intervals"])

    _safe_print(f"[diag] {team_abbr}: roster_players={len(by_pid)} stints={len(stints)}")

    for m in minutes:
        secs = []
        for pid, info in by_pid.items():
            sec = seconds_played_in_minute(info["intervals"], m)
            if sec > 0:
                secs.append((pid, sec, info["name"]))
        secs.sort(key=lambda x: (-x[1], x[0]))

        lineup = [name for _, _, name in secs[:5]]
        top10 = [(name, float(sec)) for _, sec, name in secs[:10]]
        _safe_print(f"[diag] {team_abbr} minute {m:02d}: top5={lineup}")
        _safe_print(f"       top10_seconds={top10}")
