from __future__ import annotations

import argparse
import csv
import html
import json
import os
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from render_vote_sheet import (
    _compact_table_row,
    _format_game_time,
    _poll_sort_key,
    _section_game_label,
    _summary_line,
    _table_escape,
)


BASE_DIR = Path(__file__).resolve().parent
ROOT_DIR = BASE_DIR.parent
DASHBOARD_DIR = BASE_DIR / "output" / "dashboard"
CORE_MARKETS_CSV = BASE_DIR / "sportsbook_markets_consensus_live.csv"
SOCCER_MARKETS_CSV = BASE_DIR / "sportsbook_markets_soccer_live.csv"
LIVE_RECOMMENDATIONS_CSV = BASE_DIR / "live_poll_vote_recommendations.csv"
PREDICTION_SPORT_ORDER = ("mlb", "nba", "nhl")
PREDICTION_SPORTS = set(PREDICTION_SPORT_ORDER)
UTC_PLUS_8 = timezone(timedelta(hours=8))
LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
LOG_HANDLE = None
DOC_SPECS = [
    {
        "id": "mlb-vote",
        "category": "Vote Sheets",
        "label": "MLB Vote Sheet",
        "sport": "mlb",
        "stable_path": DASHBOARD_DIR / "mlb.md",
        "fallback_glob": "mlb_v*.md",
    },
    {
        "id": "nba-vote",
        "category": "Vote Sheets",
        "label": "NBA Vote Sheet",
        "sport": "nba",
        "stable_path": DASHBOARD_DIR / "nba.md",
        "fallback_glob": "nba_v*.md",
    },
    {
        "id": "nhl-vote",
        "category": "Vote Sheets",
        "label": "NHL Vote Sheet",
        "sport": "nhl",
        "stable_path": DASHBOARD_DIR / "nhl.md",
        "fallback_glob": "nhl_v*.md",
    },
    {
        "id": "wnba-vote",
        "category": "Vote Sheets",
        "label": "WNBA Vote Sheet",
        "sport": "wnba",
        "stable_path": DASHBOARD_DIR / "wnba.md",
        "fallback_glob": "wnba_v*.md",
    },
    {
        "id": "soccer-vote",
        "category": "Vote Sheets",
        "label": "Soccer / FC Vote Sheet",
        "sport": "soccer",
        "stable_path": DASHBOARD_DIR / "soccer.md",
        "fallback_glob": "soccer_v*.md",
    },
    {
        "id": "mlb-predictions",
        "category": "Predictions",
        "label": "MLB Predictions",
        "sport": "mlb",
        "stable_path": DASHBOARD_DIR / "mlb_predictions.md",
        "fallback_glob": "mlb_predictions_v*.md",
    },
    {
        "id": "nba-predictions",
        "category": "Predictions",
        "label": "NBA Predictions",
        "sport": "nba",
        "stable_path": DASHBOARD_DIR / "nba_predictions.md",
        "fallback_glob": "nba_predictions_v*.md",
    },
    {
        "id": "nhl-predictions",
        "category": "Predictions",
        "label": "NHL Predictions",
        "sport": "nhl",
        "stable_path": DASHBOARD_DIR / "nhl_predictions.md",
        "fallback_glob": "nhl_predictions_v*.md",
    },
]
CATEGORY_ORDER = {
    "Vote Sheets": 0,
    "Predictions": 1,
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Serve a local HTML dashboard for the Real markdown outputs, with optional "
            "background odds refresh cycles."
        )
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--refresh-seconds",
        type=int,
        default=0,
        help="Background dashboard-data refresh interval in seconds. Set 0 to disable.",
    )
    parser.add_argument(
        "--refresh-on-start",
        action="store_true",
        help="Kick off one background data refresh as soon as the server starts.",
    )
    parser.add_argument(
        "--sports",
        default="mlb,nba,nhl,wnba",
        help="Comma-separated sports passed through to refresh_dashboard_data.py.",
    )
    parser.add_argument(
        "--refresh-soccer",
        action="store_true",
        default=True,
        help="Include soccer in the background dashboard-data refresh cycle.",
    )
    parser.add_argument(
        "--no-refresh-soccer",
        action="store_false",
        dest="refresh_soccer",
        help="Disable soccer in dashboard-data refresh cycles.",
    )
    parser.add_argument(
        "--season",
        default="2025",
        help="Season value passed through to refresh_dashboard_data.py for lineups.",
    )
    parser.add_argument(
        "--pid-file",
        default="",
        help="Optional PID file path for local start/stop wrappers.",
    )
    parser.add_argument(
        "--log-file",
        default="",
        help="Optional file to receive stdout/stderr from the server process.",
    )
    return parser.parse_args()


def _normalize_sports_arg(value: str) -> str:
    seen: set[str] = set()
    sports: list[str] = []
    for item in str(value or "").split(","):
        sport = item.strip().lower()
        if not sport or sport in seen:
            continue
        seen.add(sport)
        sports.append(sport)
    return ",".join(sports)


def _sports_list(value: str) -> list[str]:
    normalized = _normalize_sports_arg(value)
    return [sport for sport in normalized.split(",") if sport]


def _normalize_sport(value: str) -> str:
    sport = str(value or "").strip().lower()
    return "soccer" if sport == "fc" else sport


def _sport_label(sport: str) -> str:
    labels = {
        "mlb": "MLB",
        "nba": "NBA",
        "nhl": "NHL",
        "wnba": "WNBA",
        "soccer": "Soccer / FC",
    }
    return labels.get(_normalize_sport(sport), str(sport or "sport").upper())


def _core_market_providers_arg() -> str:
    providers = ["draftkings", "fanduel"]
    if os.environ.get("ODDS_API_IO_KEY", "").strip() or os.environ.get("ODDS_API_KEY", "").strip():
        providers.append("betmgm")
    return ",".join(providers)


def _latest_matching_file(pattern: str) -> Path | None:
    candidates = list((BASE_DIR / "output").glob(pattern))
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)


def _path_updated_at(path: Path | None) -> str:
    if path is None or not path.exists():
        return ""
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()


def _odds_source_path_for_sport(sport: str) -> Path:
    return SOCCER_MARKETS_CSV if str(sport or "").strip().lower() == "soccer" else CORE_MARKETS_CSV


def _odds_updated_at_for_sport(sport: str) -> str:
    return _path_updated_at(_odds_source_path_for_sport(sport))


def _existing_document_paths() -> list[dict[str, object]]:
    documents: list[dict[str, object]] = []
    for order, spec in enumerate(DOC_SPECS):
        stable_path = Path(spec["stable_path"])
        source_path = stable_path if stable_path.exists() else _latest_matching_file(str(spec["fallback_glob"]))
        if source_path is None or not source_path.exists():
            continue
        stat = source_path.stat()
        documents.append(
            {
                "id": spec["id"],
                "category": spec["category"],
                "label": spec["label"],
                "sport": spec["sport"],
                "path": source_path,
                "mtime": stat.st_mtime,
                "updated_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
                "odds_updated_at": _odds_updated_at_for_sport(str(spec["sport"])),
                "filename": source_path.name,
                "order": order,
            }
        )
    documents.sort(
        key=lambda item: (
            CATEGORY_ORDER.get(str(item["category"]), 99),
            int(item["order"]),
        )
    )
    return documents


def _split_markdown_row(line: str) -> list[str]:
    text = line.strip()
    if text.startswith("|"):
        text = text[1:]
    if text.endswith("|"):
        text = text[:-1]
    cells: list[str] = []
    current: list[str] = []
    index = 0
    while index < len(text):
        char = text[index]
        if char == "\\" and index + 1 < len(text):
            current.append(text[index + 1])
            index += 2
            continue
        if char == "|":
            cells.append("".join(current).strip())
            current = []
            index += 1
            continue
        current.append(char)
        index += 1
    cells.append("".join(current).strip())
    return cells


def _is_table_separator(line: str) -> bool:
    cells = _split_markdown_row(line)
    if not cells:
        return False
    for cell in cells:
        normalized = cell.replace(":", "").replace("-", "").replace(" ", "")
        if normalized:
            return False
    return True


def _render_inline_markup(text: str) -> str:
    result: list[str] = []
    index = 0
    strong_open = False
    code_open = False
    while index < len(text):
        if text.startswith("**", index):
            result.append("</strong>" if strong_open else "<strong>")
            strong_open = not strong_open
            index += 2
            continue
        char = text[index]
        if char == "`":
            result.append("</code>" if code_open else "<code>")
            code_open = not code_open
            index += 1
            continue
        if char == "\\" and index + 1 < len(text):
            result.append(html.escape(text[index + 1]))
            index += 2
            continue
        result.append(html.escape(char))
        index += 1
    if code_open:
        result.append("</code>")
    if strong_open:
        result.append("</strong>")
    return "".join(result)


def _render_inline(text: str) -> str:
    parts: list[str] = []
    cursor = 0
    raw_text = str(text or "")
    for match in LINK_RE.finditer(raw_text):
        parts.append(_render_inline_markup(raw_text[cursor : match.start()]))
        label = _render_inline_markup(match.group(1))
        url = html.escape(match.group(2), quote=True)
        parts.append(f'<a href="{url}" target="_blank" rel="noreferrer">{label}</a>')
        cursor = match.end()
    parts.append(_render_inline_markup(raw_text[cursor:]))
    return "".join(parts)


def _render_table(lines: list[str], start_index: int) -> tuple[str, int]:
    header_cells = _split_markdown_row(lines[start_index])
    body_start = start_index + 2
    body_rows: list[list[str]] = []
    index = body_start
    while index < len(lines) and lines[index].strip().startswith("|"):
        body_rows.append(_split_markdown_row(lines[index]))
        index += 1
    html_parts = ['<div class="table-wrap"><table><thead><tr>']
    for cell in header_cells:
        html_parts.append(f"<th>{_render_inline(cell)}</th>")
    html_parts.append("</tr></thead><tbody>")
    for row in body_rows:
        html_parts.append("<tr>")
        for cell in row:
            html_parts.append(f"<td>{_render_inline(cell)}</td>")
        html_parts.append("</tr>")
    html_parts.append("</tbody></table></div>")
    return "".join(html_parts), index


def markdown_to_html(markdown_text: str) -> str:
    lines = markdown_text.splitlines()
    html_parts: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        if not stripped:
            index += 1
            continue
        if stripped.startswith("```"):
            code_lines: list[str] = []
            index += 1
            while index < len(lines) and not lines[index].strip().startswith("```"):
                code_lines.append(lines[index])
                index += 1
            html_parts.append(f"<pre><code>{html.escape(chr(10).join(code_lines))}</code></pre>")
            index += 1
            continue
        if stripped.startswith("|") and index + 1 < len(lines) and _is_table_separator(lines[index + 1]):
            table_html, next_index = _render_table(lines, index)
            html_parts.append(table_html)
            index = next_index
            continue
        if stripped.startswith("#"):
            level = len(stripped) - len(stripped.lstrip("#"))
            level = min(max(level, 1), 6)
            content = stripped[level:].strip()
            html_parts.append(f"<h{level}>{_render_inline(content)}</h{level}>")
            index += 1
            continue
        if stripped.startswith("- "):
            items: list[str] = []
            while index < len(lines) and lines[index].strip().startswith("- "):
                items.append(lines[index].strip()[2:].strip())
                index += 1
            html_parts.append("<ul>")
            for item in items:
                html_parts.append(f"<li>{_render_inline(item)}</li>")
            html_parts.append("</ul>")
            continue
        if stripped.startswith("`") and stripped.endswith("`") and stripped.count("`") == 2:
            html_parts.append(f'<div class="meta-line">{_render_inline(stripped[1:-1])}</div>')
            index += 1
            continue
        paragraph_lines = [stripped]
        index += 1
        while index < len(lines):
            next_line = lines[index].strip()
            if not next_line:
                break
            if next_line.startswith(("#", "- ", "|", "```")):
                break
            if next_line.startswith("`") and next_line.endswith("`") and next_line.count("`") == 2:
                break
            paragraph_lines.append(next_line)
            index += 1
        html_parts.append(f"<p>{_render_inline(' '.join(paragraph_lines))}</p>")
    return "\n".join(html_parts)


@dataclass
class RefreshState:
    running: bool = False
    active_label: str = ""
    last_started_at: str = ""
    last_finished_at: str = ""
    last_succeeded_at: str = ""
    last_label: str = ""
    last_duration_seconds: float = 0.0
    last_error: str = ""
    last_exit_code: int = 0
    runs: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)


def _log(message: str) -> None:
    timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print(f"[{timestamp}] {message}", flush=True)


class DashboardContext:
    def __init__(self, *, sports: str, refresh_soccer: bool, season: str, refresh_seconds: int) -> None:
        self.sports = sports
        self.refresh_soccer = refresh_soccer
        self.season = season
        self.refresh_seconds = max(0, int(refresh_seconds))
        self.state = RefreshState()
        self._queued_jobs: list[tuple[str, list[list[str]]]] = []
        self._stop_event = threading.Event()
        self._loop_thread: threading.Thread | None = None

    def documents(self) -> list[dict[str, object]]:
        return _existing_document_paths()

    def _sports_arg(self, *, include_soccer: bool = True) -> str:
        sports = _sports_list(self.sports)
        if include_soccer and self.refresh_soccer and "soccer" not in sports:
            sports.append("soccer")
        return ",".join(sports)

    def _dashboard_refresh_command(self, *, sport: str = "", skip_predictions: bool = False) -> list[str]:
        target_sport = _normalize_sport(sport)
        if target_sport:
            sports_arg = target_sport
        else:
            sports_arg = self._sports_arg(include_soccer=True)
        markets_csv = (
            BASE_DIR / "tmp" / f"dashboard_{target_sport}_markets.csv"
            if target_sport and target_sport in PREDICTION_SPORTS
            else CORE_MARKETS_CSV
        )
        command = [
            sys.executable,
            "-B",
            str(BASE_DIR / "refresh_dashboard_data.py"),
            "--sports",
            sports_arg,
            "--markets-csv",
            str(markets_csv),
            "--season",
            self.season,
            "--dashboard-dir",
            str(DASHBOARD_DIR),
        ]
        if skip_predictions:
            command.append("--skip-predictions")
        return command

    def _live_refresh_commands(self, *, refresh_markets: bool = False) -> list[list[str]]:
        command = [
            sys.executable,
            "-B",
            str(BASE_DIR / "recommend_live_polls.py"),
            "--unlocked-only",
            "--sports",
            self._sports_arg(include_soccer=True),
            "--markets-csv",
            str(CORE_MARKETS_CSV),
            "--output",
            str(LIVE_RECOMMENDATIONS_CSV),
            "--iterations",
            "1",
        ]
        if refresh_markets:
            command.insert(4, "--refresh-markets")
        return [command]

    def _prediction_refresh_commands(self, *, sport: str = "", refresh_markets: bool = False) -> list[list[str]]:
        target_sport = _normalize_sport(sport)
        requested = [target_sport] if target_sport else _sports_list(self.sports)
        sports = [item for item in requested if item in PREDICTION_SPORTS]
        if not target_sport and not sports:
            sports = [item for item in PREDICTION_SPORT_ORDER if item in PREDICTION_SPORTS]
        if not sports:
            raise ValueError("Prediction refresh currently supports mlb, nba, and nhl.")
        markets_csv = CORE_MARKETS_CSV

        commands: list[list[str]] = []
        if refresh_markets:
            markets_csv = BASE_DIR / "tmp" / f"dashboard_prediction_{target_sport or 'all'}_markets.csv"
            commands.append(
                [
                    sys.executable,
                    "-B",
                    str(BASE_DIR / "ingest_public_markets.py"),
                    "--providers",
                    _core_market_providers_arg(),
                    "--sports",
                    ",".join(sports),
                    "--force-live",
                    "--output",
                    str(markets_csv),
                    "--dump-json-dir",
                    str(BASE_DIR / "tmp" / "dashboard_prediction_live_check"),
                ]
            )
        for item in sports:
            market_csv = BASE_DIR / f"prediction_market_recommendations_{item}.csv"
            position_csv = BASE_DIR / f"prediction_position_recommendations_{item}.csv"
            commands.extend(
                [
                    [
                        sys.executable,
                        "-B",
                        str(BASE_DIR / "recommend_prediction_markets.py"),
                        "--sport",
                        item,
                        "--markets-csv",
                        str(markets_csv),
                        "--output",
                        str(market_csv),
                    ],
                    [
                        sys.executable,
                        "-B",
                        str(BASE_DIR / "recommend_prediction_positions.py"),
                        "--sport",
                        item,
                        "--markets-csv",
                        str(markets_csv),
                        "--output",
                        str(position_csv),
                    ],
                    [
                        sys.executable,
                        "-B",
                        str(BASE_DIR / "render_prediction_sheet.py"),
                        "--input",
                        str(market_csv),
                        "--positions-input",
                        str(position_csv),
                        "--output",
                        str(DASHBOARD_DIR / f"{item}_predictions.md"),
                    ],
                ]
            )
        return commands

    def _prediction_board_refresh_commands(self) -> list[list[str]]:
        requested = _sports_list(self.sports)
        prediction_sports = [item for item in requested if item in PREDICTION_SPORTS]
        if not prediction_sports:
            prediction_sports = [item for item in PREDICTION_SPORT_ORDER if item in PREDICTION_SPORTS]
        commands: list[list[str]] = [
            [
                sys.executable,
                "-B",
                str(BASE_DIR / "ingest_public_markets.py"),
                "--providers",
                _core_market_providers_arg(),
                "--sports",
                ",".join(prediction_sports),
                "--force-live",
                "--output",
                str(CORE_MARKETS_CSV),
                "--dump-json-dir",
                str(BASE_DIR / "tmp" / "dashboard_prediction_core_lines"),
            ]
        ]
        if self.refresh_soccer or "soccer" in requested:
            commands.append(
                [
                    sys.executable,
                    "-B",
                    str(BASE_DIR / "ingest_public_markets.py"),
                    "--providers",
                    "draftkings",
                    "--sports",
                    "soccer",
                    "--force-live",
                    "--output",
                    str(SOCCER_MARKETS_CSV),
                    "--dump-json-dir",
                    str(BASE_DIR / "tmp" / "dashboard_prediction_soccer_lines"),
                ]
            )
        commands.extend(self._prediction_refresh_commands(sport="", refresh_markets=False))
        return commands

    def _refresh_request(self, *, scope: str = "", sport: str = "") -> tuple[str, list[list[str]]]:
        normalized_scope = str(scope or "").strip().lower()
        target_sport = _normalize_sport(sport)
        if not normalized_scope:
            normalized_scope = "sport" if target_sport else "dashboard"
        if normalized_scope == "dashboard":
            return "all dashboard data", [self._dashboard_refresh_command()]
        if normalized_scope in {"dashboard-core", "dashboard-lite", "dashboard-nopred"}:
            return "all dashboard data (no prediction refresh)", [self._dashboard_refresh_command(skip_predictions=True)]
        if normalized_scope in {"sport", "pregame", "lineup", "lineups"}:
            if not target_sport:
                raise ValueError("Sport refresh requires a sport.")
            return (
                f"{_sport_label(target_sport)} pre-game data",
                [self._dashboard_refresh_command(sport=target_sport, skip_predictions=True)],
            )
        if normalized_scope == "live":
            return "open live poll recommendations", self._live_refresh_commands(refresh_markets=False)
        if normalized_scope in {"markets", "lines"}:
            return "live polls and sportsbook lines", self._live_refresh_commands(refresh_markets=True)
        if normalized_scope in {"prediction", "predictions"}:
            label = f"{_sport_label(target_sport)} predictions (cached lines)" if target_sport else "all prediction sheets (cached lines)"
            return label, self._prediction_refresh_commands(sport=target_sport, refresh_markets=False)
        if normalized_scope in {"prediction-lines", "predictionlines", "prediction-markets"}:
            label = (
                f"{_sport_label(target_sport)} predictions + sportsbook lines"
                if target_sport
                else "all prediction sheets + sportsbook lines"
            )
            return label, self._prediction_refresh_commands(sport=target_sport, refresh_markets=True)
        if normalized_scope in {"prediction-board", "prediction-ev", "prediction-tracker"}:
            return (
                "prediction EV tracker (Real + consensus lines)",
                self._prediction_board_refresh_commands(),
            )
        raise ValueError(f"Unsupported refresh scope: {normalized_scope}")

    @staticmethod
    def _commands_text(commands: list[list[str]]) -> str:
        return " ; ".join(" ".join(command) for command in commands)

    def _start_refresh_worker(self, *, commands: list[list[str]], label: str) -> None:
        thread = threading.Thread(target=self._run_refresh, args=(commands, label), daemon=True)
        thread.start()

    def trigger_refresh(self, *, scope: str = "", sport: str = "") -> tuple[bool, bool, int, str]:
        label, commands = self._refresh_request(scope=scope, sport=sport)
        start_now = False
        queued = False
        queue_position = 0
        with self.state.lock:
            if self.state.running:
                self._queued_jobs.append((label, commands))
                queued = True
                queue_position = len(self._queued_jobs)
            else:
                self.state.running = True
                self.state.active_label = label
                self.state.last_started_at = datetime.now(timezone.utc).isoformat()
                self.state.last_error = ""
                start_now = True
        if queued:
            _log(
                f"refresh queued for {label} at position {queue_position}: "
                f"{self._commands_text(commands)}"
            )
            return False, True, queue_position, label
        _log(f"refresh queued for {label}: {self._commands_text(commands)}")
        self._start_refresh_worker(commands=commands, label=label)
        return True, False, 0, label

    def _run_refresh(self, commands: list[list[str]], label: str) -> None:
        started = time.time()
        exit_code = 0
        error_text = ""
        _log(f"refresh started for {label}")
        for command in commands:
            _log(f"refresh step: {' '.join(command)}")
            try:
                subprocess.run(command, check=True, cwd=str(ROOT_DIR))
            except subprocess.CalledProcessError as exc:
                exit_code = int(exc.returncode or 1)
                error_text = str(exc)
                break
            except Exception as exc:
                exit_code = 1
                error_text = str(exc)
                break
        finished_at = datetime.now(timezone.utc).isoformat()
        duration = time.time() - started
        next_job: tuple[str, list[list[str]]] | None = None
        with self.state.lock:
            self.state.last_finished_at = finished_at
            self.state.last_label = label
            self.state.last_duration_seconds = duration
            self.state.last_exit_code = exit_code
            self.state.last_error = error_text
            self.state.runs += 1
            if exit_code == 0:
                self.state.last_succeeded_at = finished_at
            if self._queued_jobs:
                next_job = self._queued_jobs.pop(0)
                next_label, _next_commands = next_job
                self.state.running = True
                self.state.active_label = next_label
                self.state.last_started_at = datetime.now(timezone.utc).isoformat()
                self.state.last_error = ""
            else:
                self.state.running = False
                self.state.active_label = ""
        if exit_code == 0:
            _log(f"refresh finished successfully for {label} in {duration:.2f}s")
        else:
            _log(f"refresh failed for {label} in {duration:.2f}s with code {exit_code}: {error_text}")
        if next_job is not None:
            next_label, next_commands = next_job
            _log(f"refresh dequeued for {next_label}: {self._commands_text(next_commands)}")
            self._start_refresh_worker(commands=next_commands, label=next_label)

    def start_refresh_loop(self, *, refresh_on_start: bool) -> None:
        _log(
            "dashboard context started "
            f"sports={self.sports} refresh_soccer={self.refresh_soccer} "
            f"refresh_seconds={self.refresh_seconds} refresh_on_start={refresh_on_start}"
        )
        if refresh_on_start:
            self.trigger_refresh(scope="dashboard-core")
        if self.refresh_seconds <= 0:
            _log("background refresh loop disabled")
            return

        def _loop() -> None:
            while not self._stop_event.wait(self.refresh_seconds):
                self.trigger_refresh(scope="dashboard-core")

        self._loop_thread = threading.Thread(target=_loop, daemon=True)
        self._loop_thread.start()
        _log(f"background refresh loop enabled every {self.refresh_seconds}s")

    def stop(self) -> None:
        _log("dashboard context stopping")
        self._stop_event.set()

    def status_payload(self) -> dict[str, object]:
        with self.state.lock:
            queued_labels = [label for label, _commands in self._queued_jobs]
            return {
                "running": self.state.running,
                "active_refresh": self.state.active_label,
                "last_started_at": self.state.last_started_at,
                "last_finished_at": self.state.last_finished_at,
                "last_succeeded_at": self.state.last_succeeded_at,
                "last_refresh": self.state.last_label,
                "last_duration_seconds": round(self.state.last_duration_seconds, 2),
                "last_error": self.state.last_error,
                "last_exit_code": self.state.last_exit_code,
                "runs": self.state.runs,
                "refresh_seconds": self.refresh_seconds,
                "sports": self.sports,
                "effective_sports": self._sports_arg(include_soccer=True),
                "refresh_soccer": self.refresh_soccer,
                "queued_count": len(queued_labels),
                "queued_refreshes": queued_labels[:8],
                "core_odds_updated_at": _path_updated_at(CORE_MARKETS_CSV),
                "soccer_odds_updated_at": _path_updated_at(SOCCER_MARKETS_CSV),
            }


def _json_response(handler: BaseHTTPRequestHandler, payload: dict[str, object], *, status: int = 200) -> None:
    body = json.dumps(payload).encode("utf8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Cache-Control", "no-store, max-age=0")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _html_shell() -> str:
    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Real Dashboard</title>
  <style>
    :root {
      --bg: #f5f0e6;
      --panel: #fffaf2;
      --panel-strong: #fff;
      --ink: #1d2433;
      --muted: #6f7788;
      --accent: #0f5c4d;
      --accent-2: #bd6b2d;
      --line: #d9cfbf;
      --shadow: 0 18px 40px rgba(29, 36, 51, 0.08);
      --radius: 8px;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: linear-gradient(180deg, var(--bg), #fffaf2);
      color: var(--ink);
      font-family: "Aptos", "Trebuchet MS", "Segoe UI", sans-serif;
    }
    .shell {
      display: grid;
      grid-template-columns: 19rem minmax(0, 1fr);
      min-height: 100vh;
      gap: 1.5rem;
      padding: 1.5rem;
    }
    .sidebar, .content-card, .status-card {
      background: rgba(255, 250, 242, 0.88);
      backdrop-filter: blur(14px);
      border: 1px solid rgba(217, 207, 191, 0.9);
      border-radius: var(--radius);
      box-shadow: var(--shadow);
    }
    .sidebar {
      padding: 1.2rem;
      position: sticky;
      top: 1.5rem;
      height: calc(100vh - 3rem);
      overflow: auto;
    }
    .brand {
      margin-bottom: 1.2rem;
    }
    .eyebrow {
      color: var(--accent);
      font-size: 0.78rem;
      font-weight: 700;
      letter-spacing: 0;
      text-transform: uppercase;
    }
    h1 {
      margin: 0.2rem 0 0;
      font-family: "Iowan Old Style", "Palatino Linotype", "Book Antiqua", Georgia, serif;
      font-size: 2rem;
      line-height: 1.05;
    }
    .subcopy {
      margin: 0.5rem 0 0;
      color: var(--muted);
      font-size: 0.95rem;
      line-height: 1.4;
    }
    .group-title {
      margin: 1.3rem 0 0.5rem;
      color: var(--muted);
      font-size: 0.78rem;
      font-weight: 700;
      letter-spacing: 0;
      text-transform: uppercase;
    }
    .doc-list {
      display: grid;
      gap: 0.45rem;
    }
    .doc-item {
      display: block;
      width: 100%;
      text-align: left;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: rgba(255, 255, 255, 0.82);
      padding: 0.8rem 0.9rem;
      color: var(--ink);
      cursor: pointer;
      text-decoration: none;
      transition: transform 0.14s ease, border-color 0.14s ease, background 0.14s ease;
    }
    .doc-item:hover {
      transform: translateY(-1px);
      border-color: var(--accent-2);
    }
    .doc-item.active {
      border-color: var(--accent);
      background: rgba(15, 92, 77, 0.08);
    }
    .doc-item strong {
      display: block;
      font-size: 0.96rem;
    }
    .doc-meta {
      display: flex;
      justify-content: space-between;
      gap: 0.8rem;
      margin-top: 0.35rem;
      color: var(--muted);
      font-size: 0.8rem;
    }
    .main {
      display: grid;
      gap: 1rem;
      min-width: 0;
    }
    .status-card {
      padding: 1rem 1.2rem;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 1rem;
      flex-wrap: wrap;
    }
    .refresh-card {
      padding: 1.15rem 1.2rem;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 1rem;
      flex-wrap: wrap;
      border: 2px solid rgba(15, 92, 77, 0.22);
      background: var(--panel);
    }
    .refresh-card h2 {
      margin: 0 0 0.25rem;
      font-family: "Iowan Old Style", "Palatino Linotype", "Book Antiqua", Georgia, serif;
      font-size: 1.35rem;
    }
    .refresh-card p {
      margin: 0;
      color: var(--muted);
      font-size: 0.92rem;
      line-height: 1.45;
    }
    .status-copy {
      min-width: 16rem;
    }
    .status-line {
      font-weight: 700;
    }
    .status-detail {
      margin-top: 0.25rem;
      color: var(--muted);
      font-size: 0.92rem;
    }
    .status-actions {
      display: flex;
      align-items: center;
      gap: 0.75rem;
      flex-wrap: wrap;
    }
    button {
      border: none;
      border-radius: 999px;
      padding: 0.78rem 1.1rem;
      background: var(--accent);
      color: white;
      font-weight: 700;
      cursor: pointer;
    }
    button[disabled] {
      opacity: 0.55;
      cursor: wait;
    }
    .primary-refresh {
      min-width: 14rem;
      padding: 0.95rem 1.25rem;
      font-size: 1rem;
      box-shadow: 0 12px 28px rgba(15, 92, 77, 0.22);
    }
    .pill {
      display: inline-flex;
      align-items: center;
      gap: 0.35rem;
      border-radius: 999px;
      padding: 0.45rem 0.75rem;
      background: rgba(15, 92, 77, 0.08);
      color: var(--accent);
      font-size: 0.84rem;
      font-weight: 700;
    }
    .content-card {
      padding: 1.5rem;
      min-width: 0;
    }
    .doc-header {
      display: flex;
      align-items: baseline;
      justify-content: space-between;
      gap: 1rem;
      margin-bottom: 1.2rem;
      flex-wrap: wrap;
    }
    .doc-title {
      margin: 0;
      font-family: "Iowan Old Style", "Palatino Linotype", "Book Antiqua", Georgia, serif;
      font-size: 2rem;
    }
    .doc-updated {
      color: var(--muted);
      font-size: 0.92rem;
    }
    .doc-html h1, .doc-html h2, .doc-html h3 {
      font-family: "Iowan Old Style", "Palatino Linotype", "Book Antiqua", Georgia, serif;
      margin: 1.2rem 0 0.55rem;
      line-height: 1.1;
    }
    .doc-html h1 { font-size: 2rem; margin-top: 0; }
    .doc-html h2 { font-size: 1.55rem; }
    .doc-html h3 { font-size: 1.15rem; letter-spacing: 0; }
    .doc-html p, .doc-html li, .doc-html td, .doc-html th {
      line-height: 1.48;
      font-size: 0.95rem;
    }
    .doc-html p { margin: 0.55rem 0; }
    .doc-html a { color: var(--accent); }
    .doc-html code {
      background: rgba(29, 36, 51, 0.08);
      padding: 0.1rem 0.34rem;
      border-radius: 0.4rem;
      font-family: "Cascadia Code", "Consolas", monospace;
      font-size: 0.9em;
    }
    .doc-html ul {
      margin: 0.5rem 0 0.8rem 1.2rem;
      padding: 0;
    }
    .meta-line {
      display: inline-flex;
      margin: 0.2rem 0 0.7rem;
      padding: 0.38rem 0.65rem;
      border-radius: 999px;
      background: rgba(189, 107, 45, 0.1);
      color: #91511c;
      font-size: 0.88rem;
      font-weight: 700;
    }
    .table-wrap {
      width: 100%;
      overflow-x: auto;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel-strong);
      margin: 0.85rem 0 1.1rem;
    }
    table {
      width: 100%;
      border-collapse: collapse;
      min-width: 48rem;
    }
    th, td {
      padding: 0.72rem 0.78rem;
      border-bottom: 1px solid rgba(217, 207, 191, 0.75);
      vertical-align: top;
    }
    th {
      text-align: left;
      background: rgba(15, 92, 77, 0.08);
      color: #0e4036;
      font-size: 0.82rem;
      letter-spacing: 0;
      text-transform: uppercase;
    }
    tr:last-child td { border-bottom: none; }
    .empty {
      padding: 2rem 1rem;
      text-align: center;
      color: var(--muted);
    }
    .coverage-warning {
      border: 1px solid rgba(189, 107, 45, 0.35);
      border-left: 6px solid var(--accent-2);
      border-radius: 8px;
      background: rgba(255, 244, 225, 0.86);
      padding: 1rem;
      margin: 0 0 1rem;
    }
    .coverage-warning p {
      margin: 0.45rem 0 0;
      color: var(--ink);
    }
    @media (max-width: 1100px) {
      .shell {
        grid-template-columns: 1fr;
      }
      .sidebar {
        position: static;
        height: auto;
      }
    }
  </style>
</head>
<body>
  <div class="shell">
    <aside class="sidebar">
      <div class="brand">
        <div class="eyebrow">Real Sports</div>
        <h1>Odds Board</h1>
        <p class="subcopy">Pregame sheets and prediction sheets in one place, with live refresh support.</p>
      </div>
      <div class="group-title">Quick Links</div>
      <div class="doc-list">
        <a class="doc-item" href="/sheet/pregame/mlb"><strong>MLB Vote Sheet</strong><div class="doc-meta"><span>votes + lineups</span></div></a>
        <a class="doc-item" href="/sheet/pregame/nba"><strong>NBA Vote Sheet</strong><div class="doc-meta"><span>votes + lineups</span></div></a>
        <a class="doc-item" href="/sheet/pregame/nhl"><strong>NHL Vote Sheet</strong><div class="doc-meta"><span>votes + lineups</span></div></a>
        <a class="doc-item" href="/sheet/pregame/wnba"><strong>WNBA Vote Sheet</strong><div class="doc-meta"><span>votes + lineups</span></div></a>
        <a class="doc-item" href="/sheet/pregame/soccer"><strong>Soccer / FC Vote Sheet</strong><div class="doc-meta"><span>votes + lineups</span></div></a>
        <a class="doc-item" href="/sheet/live"><strong>Live Polls</strong><div class="doc-meta"><span>continuous snapshot</span></div></a>
        <a class="doc-item" href="/sheet/markets"><strong>Prediction EV Tracker</strong><div class="doc-meta"><span>high-EV buys + open positions</span></div></a>
      </div>
      <div id="doc-groups"></div>
    </aside>
    <main class="main">
      <section class="status-card">
        <div class="status-copy">
          <div class="status-line" id="status-line">Loading dashboard status...</div>
          <div class="status-detail" id="status-detail"></div>
        </div>
        <div class="status-actions">
          <span class="pill" id="refresh-pill">Idle</span>
          <button id="refresh-now">Refresh All Sports</button>
        </div>
      </section>
      <section class="refresh-card">
        <div>
          <h2>Manual Data Refresh</h2>
          <p>Pull the latest sportsbook markets, Real pre-game vote sheets, predictions, and lineup snapshots now. Soccer is included unless the server was started with <code>--no-refresh-soccer</code>; WNBA is included when it is in the <code>--sports</code> list.</p>
        </div>
        <button id="refresh-now-main" class="primary-refresh">Refresh All Dashboard Data</button>
      </section>
      <section class="content-card">
        <div class="doc-header">
          <h2 class="doc-title" id="doc-title">Loading…</h2>
          <div class="doc-updated" id="doc-updated"></div>
        </div>
        <div class="doc-html" id="doc-html">
          <div class="empty">Loading dashboard content…</div>
        </div>
      </section>
    </main>
  </div>
  <script>
    const state = {
      docs: [],
      activeId: null,
      activeUpdatedAt: "",
      pollMs: 15000,
      refresh: {
        prevRunning: null,
        lastFinishedAt: "",
        manualPendingLabels: [],
      },
    };

    async function fetchJson(url, options) {
      console.log("[RealDashboard] request", url, options || {});
      const response = await fetch(url, options);
      if (!response.ok) {
        console.error("[RealDashboard] request failed", url, response.status);
        throw new Error(`Request failed: ${response.status}`);
      }
      const payload = await response.json();
      console.log("[RealDashboard] response", url, payload);
      return payload;
    }

    function formatTimestamp(value) {
      if (!value) return "Not refreshed yet";
      try {
        return new Date(value).toLocaleString();
      } catch (error) {
        return value;
      }
    }

    async function ensureNotificationPermission() {
      if (!("Notification" in window)) return "unsupported";
      if (Notification.permission === "granted" || Notification.permission === "denied") {
        return Notification.permission;
      }
      try {
        return await Notification.requestPermission();
      } catch (error) {
        console.warn("[RealDashboard] notification permission request failed", error);
        return "error";
      }
    }

    function notifyRefreshFinished(payload) {
      const success = !payload.last_error && Number(payload.last_exit_code || 0) === 0;
      const label = payload.last_refresh || "dashboard data refresh";
      const title = success ? "Dashboard Update Finished" : "Dashboard Update Failed";
      const body = success
        ? `${label} completed successfully.`
        : `${label} failed: ${payload.last_error || "unknown error"}`;
      if ("Notification" in window && Notification.permission === "granted") {
        try {
          const note = new Notification(title, { body });
          setTimeout(() => note.close(), 8000);
        } catch (error) {
          console.warn("[RealDashboard] notification display failed", error);
        }
      }
      if (document.hidden) {
        document.title = success ? "Refresh Complete - Real Dashboard" : "Refresh Failed - Real Dashboard";
      }
      console.log("[RealDashboard] refresh completion notice", { success, label });
    }

    function renderDocGroups() {
      const groups = new Map();
      for (const doc of state.docs) {
        if (!groups.has(doc.category)) groups.set(doc.category, []);
        groups.get(doc.category).push(doc);
      }
      const container = document.getElementById("doc-groups");
      container.innerHTML = "";
      for (const [category, docs] of groups.entries()) {
        const title = document.createElement("div");
        title.className = "group-title";
        title.textContent = category;
        container.appendChild(title);
        const list = document.createElement("div");
        list.className = "doc-list";
        for (const doc of docs) {
          const button = document.createElement("button");
          button.className = "doc-item" + (doc.id === state.activeId ? " active" : "");
          button.type = "button";
          button.innerHTML = `
            <strong>${doc.label}</strong>
            <div class="doc-meta">
              <span>${doc.filename}</span>
              <span>${formatTimestamp(doc.updated_at)}</span>
            </div>
          `;
          button.addEventListener("click", () => loadDocument(doc.id, true));
          list.appendChild(button);
        }
        container.appendChild(list);
      }
    }

    async function loadDocuments() {
      const payload = await fetchJson("/api/documents");
      state.docs = payload.documents || [];
      console.log("[RealDashboard] documents loaded", state.docs.length);
      if (!state.activeId && state.docs.length) {
        state.activeId = state.docs[0].id;
      }
      if (state.activeId && !state.docs.find((doc) => doc.id === state.activeId)) {
        state.activeId = state.docs.length ? state.docs[0].id : null;
      }
      renderDocGroups();
    }

    async function loadDocument(docId, force) {
      if (!docId) return;
      console.log("[RealDashboard] load document", docId, { force });
      const payload = await fetchJson(`/api/document?id=${encodeURIComponent(docId)}`);
      const doc = payload.document;
      if (!doc) return;
      if (!force && state.activeId === doc.id && state.activeUpdatedAt === doc.updated_at) {
        console.log("[RealDashboard] document unchanged", doc.id, doc.updated_at);
        return;
      }
      state.activeId = doc.id;
      state.activeUpdatedAt = doc.updated_at || "";
      document.getElementById("doc-title").textContent = doc.label;
      const updatedBits = [`Sheet updated ${formatTimestamp(doc.updated_at)}`];
      if (doc.odds_updated_at) {
        updatedBits.push(`Odds updated ${formatTimestamp(doc.odds_updated_at)}`);
      }
      document.getElementById("doc-updated").textContent = updatedBits.join(" • ");
      document.getElementById("doc-html").innerHTML = doc.html || '<div class="empty">No content found.</div>';
      console.log("[RealDashboard] document rendered", doc.id, doc.updated_at);
      renderDocGroups();
    }

    async function loadStatus() {
      const payload = await fetchJson("/api/status");
      const running = !!payload.running;
      const finishedAt = String(payload.last_finished_at || "");
      const justFinished =
        state.refresh.prevRunning === true &&
        !running &&
        !!finishedAt &&
        finishedAt !== state.refresh.lastFinishedAt;
      const line = document.getElementById("status-line");
      const detail = document.getElementById("status-detail");
      const pill = document.getElementById("refresh-pill");
      const button = document.getElementById("refresh-now");
      const mainButton = document.getElementById("refresh-now-main");
      const parts = [];
      line.textContent = running
        ? `Refreshing ${payload.active_refresh || "all dashboard data"}...`
        : "Dashboard ready";
      if (payload.core_odds_updated_at) {
        parts.push(`Odds updated: ${formatTimestamp(payload.core_odds_updated_at)}`);
      }
      if (payload.refresh_soccer && payload.soccer_odds_updated_at) {
        parts.push(`Soccer odds: ${formatTimestamp(payload.soccer_odds_updated_at)}`);
      }
      parts.push(`Auto odds refresh: ${payload.refresh_seconds > 0 ? `${payload.refresh_seconds}s` : "off"}`);
      parts.push(`Sports: ${payload.sports}${payload.refresh_soccer ? ", soccer" : ""}`);
      if (Number(payload.queued_count || 0) > 0) {
        parts.push(`Queued refreshes: ${payload.queued_count}`);
      }
      if (payload.last_succeeded_at) {
        parts.push(`Last success: ${formatTimestamp(payload.last_succeeded_at)}`);
      }
      if (payload.last_refresh) {
        parts.push(`Last refresh: ${payload.last_refresh}`);
      }
      if (payload.last_error) {
        parts.push(`Last error: ${payload.last_error}`);
      }
      detail.textContent = parts.join(" • ");
      pill.textContent = running ? "Refreshing" : "Idle";
      button.disabled = running;
      if (mainButton) mainButton.disabled = running;
      if (justFinished) {
        const label = String(payload.last_refresh || "");
        const pendingIndex = state.refresh.manualPendingLabels.indexOf(label);
        if (pendingIndex >= 0) {
          notifyRefreshFinished(payload);
          state.refresh.manualPendingLabels.splice(pendingIndex, 1);
        }
      }
      state.refresh.prevRunning = running;
      if (finishedAt) state.refresh.lastFinishedAt = finishedAt;
      console.log("[RealDashboard] status", payload);
    }

    async function triggerRefresh() {
      console.log("[RealDashboard] manual refresh clicked");
      await ensureNotificationPermission();
      try {
        const payload = await fetchJson("/api/refresh", { method: "POST" });
        console.log("[RealDashboard] manual refresh response", payload);
        if (payload.started || payload.queued) {
          state.refresh.manualPendingLabels.push(String(payload.label || ""));
        }
        if (payload.queued) {
          console.log(
            "[RealDashboard] refresh queued",
            payload.label,
            "position",
            payload.queue_position
          );
        }
        await loadStatus();
      } catch (error) {
        throw error;
      }
    }

    async function poll() {
      try {
        await loadStatus();
        await loadDocuments();
        if (state.activeId) {
          await loadDocument(state.activeId, false);
        }
      } catch (error) {
        console.error("[RealDashboard] dashboard poll failed", error);
        document.getElementById("status-line").textContent = "Dashboard fetch failed";
        document.getElementById("status-detail").textContent = String(error);
      }
    }

    document.getElementById("refresh-now").addEventListener("click", triggerRefresh);
    document.getElementById("refresh-now-main").addEventListener("click", triggerRefresh);
    console.log("[RealDashboard] dashboard home loaded");
    poll();
    setInterval(poll, state.pollMs);
  </script>
</body>
</html>"""


def _document_payload(doc_id: str) -> dict[str, object] | None:
    for doc in _existing_document_paths():
        if str(doc["id"]) != doc_id:
            continue
        path = Path(doc["path"])
        markdown_text = path.read_text(encoding="utf8")
        coverage_html = _coverage_warning_html(str(doc["id"]))
        return {
            "id": doc["id"],
            "category": doc["category"],
            "label": doc["label"],
            "sport": doc["sport"],
            "filename": doc["filename"],
            "updated_at": doc["updated_at"],
            "odds_updated_at": doc["odds_updated_at"],
            "html": coverage_html + markdown_to_html(markdown_text),
        }
    return None


def _doc_id_for_sheet_path(path: str) -> str:
    parts = [part for part in path.strip("/").split("/") if part]
    if len(parts) < 2 or parts[0] != "sheet":
        return ""
    sheet_type = parts[1].lower()
    sport = parts[2].lower() if len(parts) > 2 else ""
    if sport == "fc":
        sport = "soccer"
    if sheet_type in {"pregame", "votes", "vote", "lineups", "lineup"}:
        return f"{sport}-vote" if sport else ""
    if sheet_type in {"predictions", "prediction"}:
        return f"{sport}-predictions" if sport else ""
    return ""


def _doc_refresh_scope(doc_id: str) -> tuple[str, str]:
    parts = str(doc_id or "").split("-", 1)
    sport = _normalize_sport(parts[0] if parts else "")
    suffix = parts[1] if len(parts) > 1 else ""
    if suffix == "predictions":
        return "prediction", sport
    if suffix == "vote":
        return "sport", sport
    return "", sport


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf8", newline="") as handle:
        return list(csv.DictReader(handle))


def _safe_float(value: object) -> float | None:
    if value in (None, "", "None"):
        return None
    try:
        return float(value)
    except Exception:
        return None


def _safe_int(value: object) -> int | None:
    if value in (None, "", "None"):
        return None
    try:
        return int(float(str(value)))
    except Exception:
        return None


def _format_timestamp(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return "Not refreshed yet"
    parsed = _parse_live_datetime(text)
    if parsed is None:
        return text
    return parsed.astimezone(UTC_PLUS_8).strftime("%Y-%m-%d %H:%M:%S UTC+8")


def _format_probability(value: object) -> str:
    number = _safe_float(value)
    if number is None:
        return ""
    return f"{number * 100.0:.1f}%"


def _format_signed_prob_delta(value: object) -> str:
    number = _safe_float(value)
    if number is None:
        return ""
    return f"{number * 100.0:+.2f}%"


def _format_signed_percent(value: object) -> str:
    number = _safe_float(value)
    if number is None:
        return ""
    return f"{number:+.2f}%"


def _format_signed_rax(value: object, *, digits: int = 2) -> str:
    number = _safe_float(value)
    if number is None:
        return ""
    return f"{number:+.{digits}f}"


def _format_rax(value: object, *, digits: int = 2) -> str:
    number = _safe_float(value)
    if number is None:
        return ""
    return f"{number:.{digits}f}".rstrip("0").rstrip(".")


def _prediction_market_csv_path(sport: str) -> Path:
    return BASE_DIR / f"prediction_market_recommendations_{sport}.csv"


def _prediction_position_csv_path(sport: str) -> Path:
    return BASE_DIR / f"prediction_position_recommendations_{sport}.csv"


def _load_prediction_rows(path: Path, *, fallback_sport: str) -> list[dict[str, str]]:
    rows = _read_csv_rows(path)
    for row in rows:
        if not str(row.get("sport") or "").strip():
            row["sport"] = fallback_sport
    return rows


def _market_link_cell(row: dict[str, str]) -> str:
    label = str(row.get("market_label") or row.get("market_type") or "").strip()
    url = str(row.get("buy_url") or "").strip()
    if label and url:
        return f"[{label}]({url})"
    return label


def _position_link_cell(row: dict[str, str]) -> str:
    label = str(row.get("market_label") or row.get("market_type") or "").strip()
    held = str(row.get("held_label") or "").strip()
    if held:
        label = f"{label}: {held}" if label else held
    url = str(row.get("position_url") or "").strip()
    if label and url:
        return f"[{label}]({url})"
    return label


def _selection_rax_cell(row: dict[str, str]) -> str:
    outcome = str(row.get("best_outcome") or "").strip()
    amount = _safe_int(row.get("recommended_amount")) or 0
    if not outcome:
        return f"Skip {amount}"
    separator = " " if outcome[-1:].isdigit() else ""
    return f"{outcome}{separator}{amount}"


def _real_price_cell(row: dict[str, str]) -> str:
    probability = _format_probability(row.get("best_real_prob"))
    payout = _format_rax(row.get("best_payout_per_1"))
    if probability and payout:
        return f"{probability} ({payout}x)"
    return probability or payout


def _consensus_cell(row: dict[str, str]) -> str:
    fair_prob = _format_probability(row.get("best_fair_prob"))
    edge = _format_signed_prob_delta(row.get("best_edge_prob"))
    if fair_prob and edge:
        return f"{fair_prob} ({edge})"
    return fair_prob or edge


def _books_cell(row: dict[str, str]) -> str:
    books = str(row.get("books") or "").strip()
    matched_books = _safe_int(row.get("matched_books"))
    if books and matched_books is not None:
        return f"{books} ({matched_books})"
    return books or str(matched_books or "")


def _prediction_market_sort_key(row: dict[str, str]) -> tuple[float, datetime, str, str, str]:
    return (
        -(_safe_float(row.get("best_ev_per_1")) or -9999.0),
        _parse_live_datetime(row.get("game_time") or "") or datetime.max.replace(tzinfo=timezone.utc),
        str(row.get("sport") or "").strip().lower(),
        str(row.get("game_display") or "").strip(),
        str(row.get("market_id") or "").strip(),
    )


def _prediction_position_sort_key(row: dict[str, str]) -> tuple[datetime, str, str, str]:
    return (
        _parse_live_datetime(row.get("game_time") or "") or datetime.max.replace(tzinfo=timezone.utc),
        str(row.get("sport") or "").strip().lower(),
        str(row.get("game_display") or "").strip(),
        str(row.get("position_id") or "").strip(),
    )


def _prediction_tracker_markdown() -> str:
    market_rows: list[dict[str, str]] = []
    position_rows: list[dict[str, str]] = []
    market_rows_by_sport: dict[str, list[dict[str, str]]] = {}
    position_rows_by_sport: dict[str, list[dict[str, str]]] = {}

    for sport in PREDICTION_SPORT_ORDER:
        market_path = _prediction_market_csv_path(sport)
        position_path = _prediction_position_csv_path(sport)
        sport_market_rows = _load_prediction_rows(market_path, fallback_sport=sport)
        sport_position_rows = _load_prediction_rows(position_path, fallback_sport=sport)
        market_rows_by_sport[sport] = sport_market_rows
        position_rows_by_sport[sport] = sport_position_rows
        market_rows.extend(sport_market_rows)
        position_rows.extend(sport_position_rows)

    ok_market_rows = [
        row for row in market_rows if str(row.get("status") or "").strip().lower() == "ok"
    ]
    high_ev_rows = [
        row
        for row in ok_market_rows
        if (_safe_float(row.get("best_ev_per_1")) or 0.0) > 0.0
    ]
    high_ev_rows.sort(key=_prediction_market_sort_key)
    sorted_positions = sorted(position_rows, key=_prediction_position_sort_key)

    total_recommended = sum(_safe_int(row.get("recommended_amount")) or 0 for row in high_ev_rows)
    open_position_count = len(sorted_positions)

    source_rows = _read_csv_rows(CORE_MARKETS_CSV)
    soccer_rows = _read_csv_rows(SOCCER_MARKETS_CSV)
    market_updated_by_sport = {
        sport: _format_timestamp(_path_updated_at(_prediction_market_csv_path(sport)))
        for sport in PREDICTION_SPORT_ORDER
    }
    position_updated_by_sport = {
        sport: _format_timestamp(_path_updated_at(_prediction_position_csv_path(sport)))
        for sport in PREDICTION_SPORT_ORDER
    }

    lines: list[str] = [
        "# Prediction EV Tracker",
        "",
        f"`Updated {datetime.now(UTC_PLUS_8).strftime('%Y-%m-%d %H:%M:%S UTC+8')}`",
        "",
        (
            f"**Summary:** {len(high_ev_rows)} high-EV buy opportunities "
            f"({len(ok_market_rows)} mapped prediction markets), "
            f"{open_position_count} tracked open positions, "
            f"{total_recommended} total recommended rax on positive EV markets."
        ),
        "",
        "## Data Sources",
        "",
        "| Source | Updated | Rows | Notes |",
        "| --- | --- | --- | --- |",
        "| Consensus odds (core) | "
        + _table_escape(_format_timestamp(_path_updated_at(CORE_MARKETS_CSV)))
        + " | "
        + _table_escape(str(len(source_rows)))
        + " | MLB/NBA/NHL consensus books. |",
        "| Consensus odds (FC / soccer) | "
        + _table_escape(_format_timestamp(_path_updated_at(SOCCER_MARKETS_CSV)))
        + " | "
        + _table_escape(str(len(soccer_rows)))
        + " | FC tracking source for this tab. |",
    ]

    for sport in PREDICTION_SPORT_ORDER:
        lines.append(
            "| "
            + _table_escape(f"Real prediction markets ({_sport_label(sport)})")
            + " | "
            + _table_escape(market_updated_by_sport[sport])
            + " | "
            + _table_escape(str(len(market_rows_by_sport.get(sport) or [])))
            + " | Real-side market prices from the latest refresh. |"
        )
        lines.append(
            "| "
            + _table_escape(f"Real open positions ({_sport_label(sport)})")
            + " | "
            + _table_escape(position_updated_by_sport[sport])
            + " | "
            + _table_escape(str(len(position_rows_by_sport.get(sport) or [])))
            + " | Current positions and hold vs cashout EV. |"
        )

    lines.extend(
        [
            "",
            "## High EV Buys",
            "",
            "| Sport | Game | Market | Selection+Rax | EV / 10 | EV % | Real Price | Consensus | Books | Updated (UTC+8) |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )

    if high_ev_rows:
        for row in high_ev_rows:
            ev_per_1 = _safe_float(row.get("best_ev_per_1")) or 0.0
            ev_for_10 = _format_signed_rax(ev_per_1 * 10.0)
            sport_key = _normalize_sport(str(row.get("sport") or "").strip().lower())
            cells = [
                _sport_label(sport_key),
                str(row.get("game_display") or "").strip(),
                _market_link_cell(row),
                _selection_rax_cell(row),
                ev_for_10,
                _format_signed_percent(row.get("best_ev_percent")),
                _real_price_cell(row),
                _consensus_cell(row),
                _books_cell(row),
                market_updated_by_sport.get(sport_key, "Not refreshed yet"),
            ]
            lines.append("| " + " | ".join(_table_escape(cell) for cell in cells) + " |")
    else:
        lines.append("| - | - | - | - | - | - | - | - | - | - |")

    lines.extend(
        [
            "",
            "## Current Open Positions",
            "",
            "| Sport | Game | Position | Cashout Now | Hold Fair Value | Hold-Cashout EV | Status | Action | Updated (UTC+8) |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )

    if sorted_positions:
        for row in sorted_positions:
            sport_key = _normalize_sport(str(row.get("sport") or "").strip().lower())
            cells = [
                _sport_label(sport_key),
                str(row.get("game_display") or "").strip(),
                _position_link_cell(row),
                _format_rax(row.get("cashout_now")),
                _format_rax(row.get("hold_fair_value")),
                _format_signed_rax(row.get("hold_vs_cashout_ev")),
                str(row.get("status") or "").strip(),
                str(row.get("recommended_action") or "").strip(),
                position_updated_by_sport.get(sport_key, "Not refreshed yet"),
            ]
            lines.append("| " + " | ".join(_table_escape(cell) for cell in cells) + " |")
    else:
        lines.append("| - | - | - | - | - | - | - | - | - |")

    lines.extend(
        [
            "",
            "`This tab auto-refreshes both consensus odds and Real prediction snapshots while it is open.`",
            "",
        ]
    )
    return "\n".join(lines)


def _prediction_tracker_html() -> str:
    controls = _refresh_controls_html(
        scope="prediction-board",
        button_label="Refresh Prediction EV + Lines",
        note=(
            "Auto-refreshes every 60s while this tab is open. "
            "Each run updates consensus sportsbook lines, FC/soccer lines, "
            "high-EV prediction opportunities, and current open positions."
        ),
        auto_seconds=60,
    )
    return _standalone_html(
        "Prediction EV Tracker",
        f'{controls}<div class="doc-html">{markdown_to_html(_prediction_tracker_markdown())}</div>',
    )


def _count_by_field(rows: list[dict[str, str]], field: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        key = str(row.get(field) or "").strip() or "blank"
        counts[key] = counts.get(key, 0) + 1
    return counts


def _game_list_html(rows: list[dict[str, str]], *, limit: int = 8) -> str:
    seen: set[str] = set()
    labels: list[str] = []
    for row in rows:
        label = str(row.get("game_label") or "").strip()
        away = str(row.get("away_team") or "").strip()
        home = str(row.get("home_team") or "").strip()
        if not label and (away or home):
            label = f"{away} @ {home}".strip(" @")
        if not label or label in seen:
            continue
        seen.add(label)
        labels.append(label)
    if not labels:
        return "<em>none found</em>"
    rendered = ", ".join(html.escape(label) for label in labels[:limit])
    if len(labels) > limit:
        rendered += f", +{len(labels) - limit} more"
    return rendered


def _coverage_warning_html(doc_id: str) -> str:
    scope, sport = _doc_refresh_scope(doc_id)
    if scope != "sport" or not sport:
        return ""

    recommendation_rows = _read_csv_rows(BASE_DIR / f"poll_vote_recommendations_consensus_{sport}.csv")
    if not recommendation_rows:
        return ""

    actionable_rows = [
        row
        for row in recommendation_rows
        if str(row.get("poll_kind") or "").strip().lower() not in {"contest", "lineup"}
    ]
    no_market_rows = [
        row
        for row in actionable_rows
        if str(row.get("status") or "").strip().lower() == "no_market"
    ]
    if len(no_market_rows) < max(3, int(len(actionable_rows) * 0.5)):
        return ""

    status_counts = _count_by_field(recommendation_rows, "status")
    status_summary = ", ".join(
        f"{html.escape(status)}={count}"
        for status, count in sorted(status_counts.items(), key=lambda item: (-item[1], item[0]))
    )
    market_rows = _read_csv_rows(_odds_source_path_for_sport(sport))
    market_count = len(market_rows)
    note = (
        "For soccer, the current sportsbook fetch is narrower than the Real app schedule "
        "unless we add the missing league IDs/request config."
        if sport == "soccer"
        else "The sportsbook cache refreshed, but it does not contain matching odds for many Real games."
    )
    return f"""
    <section class="coverage-warning">
      <strong>Odds coverage warning</strong>
      <p>The refresh completed, but {len(no_market_rows)} of {len(actionable_rows)} non-lineup poll rows are still <code>no_market</code>. Statuses: {status_summary}.</p>
      <p><strong>Real games:</strong> {_game_list_html(recommendation_rows)}</p>
      <p><strong>Sportsbook cache games ({market_count} rows):</strong> {_game_list_html(market_rows)}</p>
      <p>{html.escape(note)}</p>
    </section>
    """


def _refresh_controls_html(
    *,
    scope: str,
    sport: str = "",
    button_label: str = "Refresh",
    note: str = "",
    auto_seconds: int = 0,
) -> str:
    config = {
        "scope": scope,
        "sport": _normalize_sport(sport),
        "autoSeconds": max(0, int(auto_seconds)),
        "buttonLabel": button_label,
    }
    note_html = f'<p class="subcopy">{html.escape(note)}</p>' if note else ""
    config_json = json.dumps(config)
    return f"""
    <section class="refresh-panel">
      <div>
        <strong>{html.escape(button_label)}</strong>
        {note_html}
        <p class="subcopy" id="standalone-refresh-status">Ready.</p>
      </div>
      <button id="standalone-refresh" class="primary-refresh">{html.escape(button_label)}</button>
    </section>
    <script>
      (() => {{
        const config = {config_json};
        const button = document.getElementById("standalone-refresh");
        const status = document.getElementById("standalone-refresh-status");
        let refreshInFlight = false;
        let notifyOnComplete = false;
        let targetLabel = "";
        let baselineRuns = 0;
        let queuedPositionHint = 0;

        function sleep(ms) {{
          return new Promise((resolve) => setTimeout(resolve, ms));
        }}

        function normalizeLabel(value, fallback = "") {{
          const text = String(value || "").trim();
          return text || fallback;
        }}

        function queuedPositionForLabel(payload, label) {{
          const queue = Array.isArray(payload.queued_refreshes)
            ? payload.queued_refreshes.map((entry) => String(entry || ""))
            : [];
          const index = queue.indexOf(label);
          return index >= 0 ? index + 1 : 0;
        }}

        async function ensureNotificationPermission() {{
          if (!("Notification" in window)) return "unsupported";
          if (Notification.permission === "granted" || Notification.permission === "denied") {{
            return Notification.permission;
          }}
          try {{
            return await Notification.requestPermission();
          }} catch (error) {{
            console.warn("[RealDashboard] standalone notification permission failed", error);
            return "error";
          }}
        }}

        function notifyCompletion(success, message) {{
          if (!notifyOnComplete) return;
          if (!("Notification" in window) || Notification.permission !== "granted") return;
          try {{
            const note = new Notification(
              success ? "Refresh Finished" : "Refresh Failed",
              {{ body: message }}
            );
            setTimeout(() => note.close(), 8000);
          }} catch (error) {{
            console.warn("[RealDashboard] standalone notification failed", error);
          }}
        }}

        async function fetchJson(url, options) {{
          console.log("[RealDashboard] standalone request", url, options || {{}});
          const response = await fetch(url, options);
          const payload = await response.json();
          console.log("[RealDashboard] standalone response", url, payload);
          if (!response.ok) {{
            throw new Error(payload.error || `Request failed: ${{response.status}}`);
          }}
          return payload;
        }}

        async function waitForRefreshToFinish() {{
          const target = normalizeLabel(targetLabel, config.buttonLabel);
          for (let attempt = 0; attempt < 240; attempt += 1) {{
            const payload = await fetchJson("/api/status");
            const running = !!payload.running;
            const activeRefresh = normalizeLabel(payload.active_refresh);
            const lastRefresh = normalizeLabel(payload.last_refresh);
            const lastError = normalizeLabel(payload.last_error);
            const completedRuns = Number(payload.runs || 0);
            if (completedRuns > baselineRuns && lastRefresh === target) {{
              if (lastError) {{
                notifyCompletion(false, `${{target}} failed: ${{lastError}}`);
                status.textContent = `Last refresh failed: ${{lastError}}`;
                button.disabled = false;
                refreshInFlight = false;
                notifyOnComplete = false;
                targetLabel = "";
                queuedPositionHint = 0;
                return;
              }}
              notifyCompletion(true, `${{target}} completed successfully.`);
              status.textContent = `Refresh complete: ${{target}}. Reloading...`;
              notifyOnComplete = false;
              targetLabel = "";
              queuedPositionHint = 0;
              window.location.reload();
              return;
            }}
            if (!running) {{
              status.textContent = `Waiting for ${{target}} to start...`;
            }} else if (activeRefresh === target) {{
              status.textContent = `Refreshing ${{target}}...`;
            }} else {{
              const queuePosition = queuedPositionForLabel(payload, target) || queuedPositionHint;
              if (queuePosition > 0) {{
                status.textContent = `Queued: ${{target}} (position ${{queuePosition}}). Currently refreshing ${{activeRefresh || "another job"}}...`;
              }} else {{
                status.textContent = `Refreshing ${{activeRefresh || "another job"}}... Waiting for ${{target}}.`;
              }}
            }}
            await sleep(3000);
          }}
          status.textContent = `Still waiting for ${{normalizeLabel(targetLabel, config.buttonLabel)}}. Check the server log if this takes too long.`;
          button.disabled = false;
          refreshInFlight = false;
          notifyOnComplete = false;
          targetLabel = "";
          queuedPositionHint = 0;
        }}

        async function runRefresh(reason) {{
          if (refreshInFlight) return;
          refreshInFlight = true;
          button.disabled = true;
          notifyOnComplete = reason === "manual";
          targetLabel = normalizeLabel(config.buttonLabel, "Refresh");
          baselineRuns = 0;
          queuedPositionHint = 0;
          const params = new URLSearchParams();
          params.set("scope", config.scope);
          if (config.sport) params.set("sport", config.sport);
          status.textContent = reason === "auto" ? "Auto refresh starting..." : "Refresh starting...";
          if (reason === "manual") {{
            await ensureNotificationPermission();
          }}
          try {{
            const payload = await fetchJson(`/api/refresh?${{params.toString()}}`, {{ method: "POST" }});
            targetLabel = normalizeLabel(payload.label, targetLabel);
            baselineRuns = Number((payload.status && payload.status.runs) || 0);
            queuedPositionHint = payload.queued ? Number(payload.queue_position || 0) : 0;
            if (payload.started) {{
              status.textContent = `Refresh started: ${{targetLabel}}.`;
            }} else if (payload.queued) {{
              const position = Number(payload.queue_position || 0);
              status.textContent = `Queued: ${{targetLabel}}${{position > 0 ? ` (position ${{position}})` : ""}}.`;
            }} else {{
              status.textContent = "Refresh request not started.";
            }}
            await waitForRefreshToFinish();
          }} catch (error) {{
            console.error("[RealDashboard] standalone refresh failed", error);
            status.textContent = String(error);
            button.disabled = false;
            refreshInFlight = false;
            notifyOnComplete = false;
            targetLabel = "";
            queuedPositionHint = 0;
          }}
        }}

        button.addEventListener("click", () => runRefresh("manual"));
        if (config.autoSeconds > 0) {{
          status.textContent = `Auto refresh enabled every ${{config.autoSeconds}}s while this tab is open.`;
          const storageKey = `real-dashboard-auto-${{config.scope}}-${{config.sport || "all"}}`;
          const runThrottledAutoRefresh = () => {{
            if (!document.hidden) runRefresh("auto");
          }};
          const runInitialAutoRefresh = () => {{
            const lastStarted = Number(sessionStorage.getItem(storageKey) || "0");
            const minimumGapMs = config.autoSeconds * 1000;
            if (Date.now() - lastStarted < minimumGapMs) return;
            sessionStorage.setItem(storageKey, String(Date.now()));
            runThrottledAutoRefresh();
          }};
          setTimeout(runInitialAutoRefresh, 2000);
          setInterval(() => {{
            sessionStorage.setItem(storageKey, String(Date.now()));
            runThrottledAutoRefresh();
          }}, config.autoSeconds * 1000);
        }}
      }})();
    </script>
    """


def _sheet_index_html() -> str:
    links = [
        ("MLB Vote Sheet", "/sheet/pregame/mlb"),
        ("NBA Vote Sheet", "/sheet/pregame/nba"),
        ("NHL Vote Sheet", "/sheet/pregame/nhl"),
        ("WNBA Vote Sheet", "/sheet/pregame/wnba"),
        ("Soccer / FC Vote Sheet", "/sheet/pregame/soccer"),
        ("MLB Lineups", "/sheet/lineups/mlb"),
        ("NBA Lineups", "/sheet/lineups/nba"),
        ("NHL Lineups", "/sheet/lineups/nhl"),
        ("WNBA Lineups", "/sheet/lineups/wnba"),
        ("Soccer Lineups", "/sheet/lineups/soccer"),
        ("MLB Predictions", "/sheet/predictions/mlb"),
        ("NBA Predictions", "/sheet/predictions/nba"),
        ("NHL Predictions", "/sheet/predictions/nhl"),
        ("Live Poll Recommendations", "/sheet/live"),
        ("Prediction EV Tracker", "/sheet/markets"),
        ("Prediction Market Cache", "/sheet/market-cache"),
    ]
    cards = "\n".join(f'<a class="doc-item" href="{html.escape(url)}"><strong>{html.escape(label)}</strong></a>' for label, url in links)
    return _standalone_html(
        "Real Sports Sheets",
        f"""
        <p class="subcopy">Open a sport-specific vote sheet, lineup view, live-poll snapshot, or the prediction EV tracker.</p>
        <p><button class="primary-refresh" onclick="refreshDashboardData()">Refresh All Dashboard Data</button></p>
        <div class="doc-list sheet-grid">{cards}</div>
        <script>
          async function refreshDashboardData() {{
            console.log("[RealDashboard] manual refresh requested from /sheet");
            const response = await fetch("/api/refresh", {{ method: "POST" }});
            const payload = await response.json();
            console.log("[RealDashboard] refresh response", payload);
            if (payload.started) {{
              alert("Full dashboard refresh started.");
            }} else if (payload.queued) {{
              const position = Number(payload.queue_position || 0);
              alert(`Refresh queued${{position > 0 ? ` (position ${{position}})` : ""}}.`);
            }} else {{
              alert("Refresh request was not started.");
            }}
          }}
        </script>
        """,
    )


def _standalone_html(title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <style>
    :root {{
      --bg: #f7efe2;
      --panel: rgba(255, 252, 246, 0.92);
      --ink: #1d2433;
      --muted: #687282;
      --accent: #0f5c4d;
      --line: #ded4c7;
      --shadow: 0 28px 80px rgba(52, 44, 33, 0.14);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      min-height: 100vh;
      color: var(--ink);
      font-family: "Aptos", "Segoe UI", sans-serif;
      background: linear-gradient(180deg, var(--bg), #fffaf2);
    }}
    main {{
      width: min(1180px, calc(100% - 32px));
      margin: 32px auto;
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: var(--shadow);
      padding: 28px;
    }}
    h1, h2, h3 {{
      font-family: "Iowan Old Style", "Palatino Linotype", Georgia, serif;
      line-height: 1.08;
    }}
    h1 {{ margin: 0 0 10px; font-size: clamp(2.1rem, 5vw, 4rem); }}
    a {{ color: var(--accent); }}
    .topnav {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      margin-bottom: 22px;
    }}
    .topnav a, .doc-item {{
      border: 1px solid var(--line);
      border-radius: 8px;
      background: rgba(255,255,255,0.68);
      color: var(--ink);
      padding: 10px 12px;
      text-decoration: none;
      font-weight: 700;
    }}
    button {{
      border: none;
      border-radius: 999px;
      padding: 0.9rem 1.2rem;
      background: var(--accent);
      color: white;
      font: inherit;
      font-weight: 800;
      cursor: pointer;
    }}
    button:disabled {{
      opacity: 0.55;
      cursor: wait;
    }}
    .primary-refresh {{
      box-shadow: 0 12px 28px rgba(15, 92, 77, 0.22);
    }}
    .refresh-panel {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 16px;
      flex-wrap: wrap;
      border: 1px solid rgba(15, 92, 77, 0.22);
      border-radius: 8px;
      background: rgba(255,255,255,0.58);
      padding: 16px;
      margin: 16px 0 22px;
    }}
    .refresh-panel p {{
      margin: 6px 0 0;
    }}
    .coverage-warning {{
      border: 1px solid rgba(189, 107, 45, 0.35);
      border-left: 6px solid #bd6b2d;
      border-radius: 8px;
      background: rgba(255, 244, 225, 0.86);
      padding: 16px;
      margin: 16px 0 22px;
    }}
    .coverage-warning p {{
      margin: 8px 0 0;
    }}
    .subcopy {{ color: var(--muted); }}
    .sheet-grid {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 12px;
    }}
    .doc-html table, table {{
      width: 100%;
      border-collapse: collapse;
      min-width: 720px;
    }}
    .table-wrap {{
      overflow-x: auto;
      border: 1px solid var(--line);
      border-radius: 8px;
      margin: 14px 0;
    }}
    th, td {{
      padding: 10px 11px;
      border-bottom: 1px solid var(--line);
      text-align: left;
      vertical-align: top;
    }}
    th {{ background: rgba(15, 92, 77, 0.08); }}
    @media (max-width: 820px) {{
      main {{ padding: 20px; }}
      .sheet-grid {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <main>
    <nav class="topnav">
      <a href="/">Dashboard Home</a>
      <a href="/sheet">All Sheets</a>
      <a href="/sheet/live">Live Polls</a>
      <a href="/sheet/markets">Prediction EV</a>
    </nav>
    <h1>{html.escape(title)}</h1>
    {body}
  </main>
</body>
</html>"""


def _standalone_document_html(payload: dict[str, object]) -> str:
    title = str(payload.get("label") or "Sheet")
    doc_id = str(payload.get("id") or "")
    scope, sport = _doc_refresh_scope(doc_id)
    is_prediction = scope == "prediction"
    updated = str(payload.get("updated_at") or "")
    odds_updated = str(payload.get("odds_updated_at") or "")
    meta_bits = []
    if updated:
        meta_bits.append(f"Sheet updated {html.escape(updated)}")
    if odds_updated:
        meta_bits.append(f"Odds updated {html.escape(odds_updated)}")
    meta = f'<p class="subcopy">{" | ".join(meta_bits)}</p>' if meta_bits else ""
    controls = ""
    if scope:
        label = (
            f"Refresh {_sport_label(sport)} predictions"
            if is_prediction
            else f"Refresh {_sport_label(sport)} only"
        )
        note = (
            "Auto-refreshes prediction recommendations every 60s using cached sportsbook lines; use Prediction EV Tracker to refresh both line sources."
            if is_prediction
            else "Manual only: this updates the sport-specific pre-game polls and lineups without refreshing every other sport."
        )
        controls = _refresh_controls_html(
            scope=scope,
            sport=sport,
            button_label=label,
            note=note,
            auto_seconds=60 if is_prediction else 0,
        )
    return _standalone_html(
        title,
        f'{meta}{controls}<div class="doc-html">{payload.get("html") or "<p>No content found.</p>"}</div>',
    )


def _csv_sheet_html(
    path: Path,
    title: str,
    *,
    limit: int = 400,
    refresh_scope: str = "",
    refresh_sport: str = "",
    refresh_label: str = "",
    refresh_note: str = "",
    auto_seconds: int = 0,
) -> str:
    controls = ""
    if refresh_scope:
        controls = _refresh_controls_html(
            scope=refresh_scope,
            sport=refresh_sport,
            button_label=refresh_label or "Refresh",
            note=refresh_note,
            auto_seconds=auto_seconds,
        )
    if not path.exists():
        return _standalone_html(
            title,
            (
                f"{controls}"
                f'<p class="subcopy">No CSV is available yet at <code>{html.escape(str(path))}</code>.</p>'
            ),
        )
    with path.open("r", encoding="utf8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    display_rows = rows[:limit]
    headers = list(display_rows[0].keys()) if display_rows else []
    head = "".join(f"<th>{html.escape(header)}</th>" for header in headers)
    body_rows = []
    for row in display_rows:
        cells = "".join(f"<td>{html.escape(str(row.get(header, '')))}</td>" for header in headers)
        body_rows.append(f"<tr>{cells}</tr>")
    table = (
        '<div class="table-wrap"><table>'
        f"<thead><tr>{head}</tr></thead><tbody>{''.join(body_rows)}</tbody>"
        "</table></div>"
        if display_rows
        else "<p>No rows found.</p>"
    )
    return _standalone_html(
        title,
        (
            f"{controls}"
            f'<p class="subcopy">Showing {len(display_rows)} of {len(rows)} rows from <code>{html.escape(str(path))}</code>.</p>'
            f"{table}"
        ),
    )


def _live_sort_key(row: dict[str, str]) -> tuple[str, str, str, str, str, tuple[int, int, int, int, str], str]:
    return (
        str(row.get("locks_at") or row.get("game_time") or "").strip(),
        str(row.get("created_at") or row.get("poll_created_at") or "").strip(),
        str(row.get("sport") or "").strip().lower(),
        str(row.get("game_id") or "").strip(),
        str(row.get("post_id") or "").strip(),
        _poll_sort_key(row),
        str(row.get("poll_id") or "").strip(),
    )


def _parse_live_datetime(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    text = text.replace("Z", "+00:00")
    if re.search(r"[+-]\d{2}$", text):
        text = f"{text}:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _open_live_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    now_utc = datetime.now(timezone.utc)
    open_rows: list[dict[str, str]] = []
    for row in rows:
        lock_time = _parse_live_datetime(row.get("locks_at") or row.get("game_time") or "")
        if lock_time is not None and lock_time <= now_utc:
            continue
        open_rows.append(row)
    return open_rows


def _live_game_key(row: dict[str, str]) -> tuple[str, str, str, str]:
    return (
        str(row.get("sport") or "").strip().lower(),
        str(row.get("game_id") or "").strip(),
        str(row.get("away_team") or "").strip(),
        str(row.get("home_team") or "").strip(),
    )


def _live_row_label(row: dict[str, str]) -> str:
    sport = _sport_label(str(row.get("sport") or "").strip().lower())
    game = _section_game_label(row)
    return f"{sport} - {game}" if game else sport


def _live_meta_line(row: dict[str, str]) -> str:
    parts = []
    locks_at = _format_game_time(str(row.get("locks_at") or ""))
    created_at = _format_game_time(str(row.get("created_at") or row.get("poll_created_at") or ""))
    game_time = _format_game_time(str(row.get("game_time") or ""))
    if game_time:
        parts.append(f"Game {game_time}")
    if locks_at:
        parts.append(f"Locks {locks_at}")
    if created_at:
        parts.append(f"Posted {created_at}")
    return " | ".join(parts)


def _live_recommendations_markdown(rows: list[dict[str, str]]) -> str:
    if not rows:
        return "# Live Poll Recommendations\n\nNo rows found.\n"

    sections = [
        "# Live Poll Recommendations",
        "",
        _summary_line(rows),
        "",
        "`Selection+Put` is the side plus the amount to enter. `0` means vote that side with no put.",
        "",
    ]

    sections.append("| Game | Poll | Selection+Put | Consensus Prob (Odds) | EV | Sportsbook Odds | Source |")
    sections.append("| --- | --- | --- | --- | --- | --- | --- |")
    for row in rows:
        compact_row = _compact_table_row(row)
        compact_cells = _split_markdown_row(compact_row)
        cells = [
            _live_row_label(row),
            *compact_cells,
        ]
        sections.append("| " + " | ".join(_table_escape(cell) for cell in cells) + " |")
    sections.append("")

    return "\n".join(sections).rstrip() + "\n"


def _live_recommendations_html(
    path: Path,
    *,
    refresh_scope: str = "",
    refresh_label: str = "",
    refresh_note: str = "",
    auto_seconds: int = 0,
) -> str:
    controls = ""
    if refresh_scope:
        controls = _refresh_controls_html(
            scope=refresh_scope,
            button_label=refresh_label or "Refresh",
            note=refresh_note,
            auto_seconds=auto_seconds,
        )
    if not path.exists():
        return _standalone_html(
            "Live Poll Recommendations",
            (
                f"{controls}"
                f'<p class="subcopy">No CSV is available yet at <code>{html.escape(str(path))}</code>.</p>'
            ),
        )
    all_rows = _read_csv_rows(path)
    rows = _open_live_rows(all_rows)
    if not rows and all_rows:
        markdown = (
            "# Live Poll Recommendations\n\n"
            "No open live polls found. Closed polls are omitted from this view.\n"
        )
        return _standalone_html(
            "Live Poll Recommendations",
            f'{controls}<div class="doc-html">{markdown_to_html(markdown)}</div>',
        )
    return _standalone_html(
        "Live Poll Recommendations",
        f'{controls}<div class="doc-html">{markdown_to_html(_live_recommendations_markdown(rows))}</div>',
    )


def _write_pid_file(path_text: str) -> None:
    if not path_text:
        return
    path = Path(path_text)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(os.getpid()), encoding="ascii")


def _remove_pid_file(path_text: str) -> None:
    if not path_text:
        return
    path = Path(path_text)
    try:
        if path.exists() and path.read_text(encoding="ascii").strip() == str(os.getpid()):
            path.unlink()
    except OSError:
        pass


def _redirect_logs(path_text: str) -> None:
    global LOG_HANDLE
    if not path_text:
        return
    path = Path(path_text)
    path.parent.mkdir(parents=True, exist_ok=True)
    LOG_HANDLE = path.open("a", encoding="utf8", buffering=1)
    sys.stdout = LOG_HANDLE
    sys.stderr = LOG_HANDLE


def build_handler(context: DashboardContext) -> type[BaseHTTPRequestHandler]:
    class DashboardHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path in {"/", "/index.html"}:
                body = _html_shell().encode("utf8")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store, max-age=0")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if parsed.path == "/api/status":
                _json_response(self, context.status_payload())
                return
            if parsed.path == "/api/documents":
                documents = [
                    {
                        "id": doc["id"],
                        "category": doc["category"],
                        "label": doc["label"],
                        "sport": doc["sport"],
                        "filename": doc["filename"],
                        "updated_at": doc["updated_at"],
                        "odds_updated_at": doc["odds_updated_at"],
                    }
                    for doc in context.documents()
                ]
                _json_response(self, {"documents": documents})
                return
            if parsed.path == "/api/document":
                params = parse_qs(parsed.query or "")
                doc_id = str((params.get("id") or [""])[0]).strip()
                payload = _document_payload(doc_id)
                if payload is None:
                    _json_response(self, {"error": "document not found"}, status=404)
                    return
                _json_response(self, {"document": payload})
                return
            if parsed.path in {"/sheet", "/sheet/"}:
                body = _sheet_index_html().encode("utf8")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store, max-age=0")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if parsed.path == "/sheet/live":
                body = _live_recommendations_html(
                    LIVE_RECOMMENDATIONS_CSV,
                    refresh_scope="live",
                    refresh_label="Refresh open live polls",
                    refresh_note="Auto-refreshes open live poll recommendations every 60s while this tab is open, using the cached sportsbook lines.",
                    auto_seconds=60,
                ).encode("utf8")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store, max-age=0")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if parsed.path == "/sheet/markets":
                body = _prediction_tracker_html().encode("utf8")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store, max-age=0")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if parsed.path == "/sheet/market-cache":
                body = _csv_sheet_html(
                    CORE_MARKETS_CSV,
                    "Prediction Market Cache",
                    refresh_scope="lines",
                    refresh_label="Refresh sportsbook lines",
                    refresh_note="Auto-refreshes the live sportsbook line cache every 60s while this tab is open.",
                    auto_seconds=60,
                ).encode("utf8")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store, max-age=0")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if parsed.path.startswith("/sheet/"):
                doc_id = _doc_id_for_sheet_path(parsed.path)
                payload = _document_payload(doc_id) if doc_id else None
                if payload is None:
                    scope, sport = _doc_refresh_scope(doc_id)
                    controls = ""
                    if scope:
                        controls = _refresh_controls_html(
                            scope=scope,
                            sport=sport,
                            button_label=(
                                f"Refresh {_sport_label(sport)} predictions"
                                if scope == "prediction"
                                else f"Refresh {_sport_label(sport)} only"
                            ),
                            note="This will create the missing sheet if current Real and sportsbook data are available.",
                            auto_seconds=0,
                        )
                    body = _standalone_html(
                        "Sheet not refreshed yet",
                        (
                            "<p class=\"subcopy\">That sheet does not exist yet. "
                            "Run a dashboard refresh first, then reload this URL.</p>"
                            f"{controls}"
                            "<p><a href=\"/\">Back to dashboard home</a></p>"
                        ),
                    ).encode("utf8")
                    self.send_response(HTTPStatus.NOT_FOUND)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Cache-Control", "no-store, max-age=0")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                body = _standalone_document_html(payload).encode("utf8")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store, max-age=0")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            _json_response(self, {"error": "not found"}, status=404)

        def do_POST(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path == "/api/shutdown":
                if self.client_address[0] not in {"127.0.0.1", "::1"}:
                    _json_response(self, {"error": "shutdown is only allowed from localhost"}, status=403)
                    return
                _log("shutdown requested via /api/shutdown")
                _json_response(self, {"ok": True, "message": "dashboard shutdown requested"})
                context.stop()
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return
            if parsed.path != "/api/refresh":
                _json_response(self, {"error": "not found"}, status=404)
                return
            params = parse_qs(parsed.query or "")
            scope = str((params.get("scope") or [""])[0]).strip()
            sport = str((params.get("sport") or [""])[0]).strip()
            try:
                started, queued, queue_position, label = context.trigger_refresh(scope=scope, sport=sport)
            except ValueError as exc:
                _json_response(self, {"error": str(exc)}, status=400)
                return
            _log(
                "manual refresh endpoint returned "
                f"started={started} queued={queued} queue_position={queue_position} label={label}"
            )
            _json_response(
                self,
                {
                    "started": started,
                    "queued": queued,
                    "queue_position": queue_position,
                    "label": label,
                    "status": context.status_payload(),
                },
            )

        def log_message(self, format: str, *args: object) -> None:  # noqa: A003
            _log(f"{self.client_address[0]} {format % args}")

    return DashboardHandler


def main() -> int:
    args = parse_args()
    _redirect_logs(str(args.log_file or ""))
    _write_pid_file(str(args.pid_file or ""))
    DASHBOARD_DIR.mkdir(parents=True, exist_ok=True)
    sports = _normalize_sports_arg(args.sports)
    context = DashboardContext(
        sports=sports,
        refresh_soccer=bool(args.refresh_soccer),
        season=str(args.season),
        refresh_seconds=int(args.refresh_seconds),
    )
    context.start_refresh_loop(refresh_on_start=bool(args.refresh_on_start))

    server = ThreadingHTTPServer((args.host, int(args.port)), build_handler(context))
    print(f"Serving Real dashboard at http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        context.stop()
        server.server_close()
        _remove_pid_file(str(args.pid_file or ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
