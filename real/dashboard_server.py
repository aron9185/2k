from __future__ import annotations

import argparse
import html
import json
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


BASE_DIR = Path(__file__).resolve().parent
ROOT_DIR = BASE_DIR.parent
DASHBOARD_DIR = BASE_DIR / "output" / "dashboard"
CORE_MARKETS_CSV = BASE_DIR / "sportsbook_markets_consensus_live.csv"
SOCCER_MARKETS_CSV = BASE_DIR / "sportsbook_markets_soccer_live.csv"
LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
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
        "id": "soccer-vote",
        "category": "Vote Sheets",
        "label": "Soccer Vote Sheet",
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
        default="mlb,nba,nhl",
        help="Comma-separated sports passed through to refresh_dashboard_data.py.",
    )
    parser.add_argument(
        "--refresh-soccer",
        action="store_true",
        help="Include soccer in the background dashboard-data refresh cycle.",
    )
    parser.add_argument(
        "--season",
        default="2025",
        help="Season value passed through to refresh_dashboard_data.py for lineups.",
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
    last_started_at: str = ""
    last_finished_at: str = ""
    last_succeeded_at: str = ""
    last_duration_seconds: float = 0.0
    last_error: str = ""
    last_exit_code: int = 0
    runs: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)


class DashboardContext:
    def __init__(self, *, sports: str, refresh_soccer: bool, season: str, refresh_seconds: int) -> None:
        self.sports = sports
        self.refresh_soccer = refresh_soccer
        self.season = season
        self.refresh_seconds = max(0, int(refresh_seconds))
        self.state = RefreshState()
        self._stop_event = threading.Event()
        self._loop_thread: threading.Thread | None = None

    def documents(self) -> list[dict[str, object]]:
        return _existing_document_paths()

    def _refresh_command(self) -> list[str]:
        command = [
            sys.executable,
            "-B",
            str(BASE_DIR / "refresh_dashboard_data.py"),
            "--sports",
            self.sports,
            "--season",
            self.season,
            "--dashboard-dir",
            str(DASHBOARD_DIR),
        ]
        if self.refresh_soccer:
            command.append("--refresh-soccer")
        return command

    def trigger_refresh(self) -> bool:
        with self.state.lock:
            if self.state.running:
                return False
            self.state.running = True
            self.state.last_started_at = datetime.now(timezone.utc).isoformat()
            self.state.last_error = ""
        thread = threading.Thread(target=self._run_refresh, daemon=True)
        thread.start()
        return True

    def _run_refresh(self) -> None:
        started = time.time()
        exit_code = 0
        error_text = ""
        try:
            subprocess.run(self._refresh_command(), check=True, cwd=str(ROOT_DIR))
        except subprocess.CalledProcessError as exc:
            exit_code = int(exc.returncode or 1)
            error_text = str(exc)
        except Exception as exc:
            exit_code = 1
            error_text = str(exc)
        finished_at = datetime.now(timezone.utc).isoformat()
        duration = time.time() - started
        with self.state.lock:
            self.state.running = False
            self.state.last_finished_at = finished_at
            self.state.last_duration_seconds = duration
            self.state.last_exit_code = exit_code
            self.state.last_error = error_text
            self.state.runs += 1
            if exit_code == 0:
                self.state.last_succeeded_at = finished_at

    def start_refresh_loop(self, *, refresh_on_start: bool) -> None:
        if refresh_on_start:
            self.trigger_refresh()
        if self.refresh_seconds <= 0:
            return

        def _loop() -> None:
            while not self._stop_event.wait(self.refresh_seconds):
                self.trigger_refresh()

        self._loop_thread = threading.Thread(target=_loop, daemon=True)
        self._loop_thread.start()

    def stop(self) -> None:
        self._stop_event.set()

    def status_payload(self) -> dict[str, object]:
        with self.state.lock:
            return {
                "running": self.state.running,
                "last_started_at": self.state.last_started_at,
                "last_finished_at": self.state.last_finished_at,
                "last_succeeded_at": self.state.last_succeeded_at,
                "last_duration_seconds": round(self.state.last_duration_seconds, 2),
                "last_error": self.state.last_error,
                "last_exit_code": self.state.last_exit_code,
                "runs": self.state.runs,
                "refresh_seconds": self.refresh_seconds,
                "sports": self.sports,
                "refresh_soccer": self.refresh_soccer,
                "core_odds_updated_at": _path_updated_at(CORE_MARKETS_CSV),
                "soccer_odds_updated_at": _path_updated_at(SOCCER_MARKETS_CSV),
            }


def _json_response(handler: BaseHTTPRequestHandler, payload: dict[str, object], *, status: int = 200) -> None:
    body = json.dumps(payload).encode("utf8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
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
      --radius: 18px;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background:
        radial-gradient(circle at top left, rgba(189, 107, 45, 0.14), transparent 28rem),
        radial-gradient(circle at top right, rgba(15, 92, 77, 0.12), transparent 32rem),
        var(--bg);
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
      letter-spacing: 0.12em;
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
      letter-spacing: 0.1em;
      text-transform: uppercase;
    }
    .doc-list {
      display: grid;
      gap: 0.45rem;
    }
    .doc-item {
      width: 100%;
      text-align: left;
      border: 1px solid var(--line);
      border-radius: 14px;
      background: rgba(255, 255, 255, 0.82);
      padding: 0.8rem 0.9rem;
      color: var(--ink);
      cursor: pointer;
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
    .doc-html h3 { font-size: 1.15rem; letter-spacing: 0.02em; }
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
      border-radius: 14px;
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
      letter-spacing: 0.04em;
      text-transform: uppercase;
    }
    tr:last-child td { border-bottom: none; }
    .empty {
      padding: 2rem 1rem;
      text-align: center;
      color: var(--muted);
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
          <button id="refresh-now">Refresh Now</button>
        </div>
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
    };

    async function fetchJson(url, options) {
      const response = await fetch(url, options);
      if (!response.ok) {
        throw new Error(`Request failed: ${response.status}`);
      }
      return response.json();
    }

    function formatTimestamp(value) {
      if (!value) return "Not refreshed yet";
      try {
        return new Date(value).toLocaleString();
      } catch (error) {
        return value;
      }
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
      const payload = await fetchJson(`/api/document?id=${encodeURIComponent(docId)}`);
      const doc = payload.document;
      if (!doc) return;
      if (!force && state.activeId === doc.id && state.activeUpdatedAt === doc.updated_at) {
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
      renderDocGroups();
    }

    async function loadStatus() {
      const payload = await fetchJson("/api/status");
      const running = !!payload.running;
      const line = document.getElementById("status-line");
      const detail = document.getElementById("status-detail");
      const pill = document.getElementById("refresh-pill");
      const button = document.getElementById("refresh-now");
      line.textContent = running ? "Refreshing sportsbook and Real data…" : "Dashboard ready";
      const parts = [];
      line.textContent = running ? "Refreshing sportsbook and Real data..." : "Dashboard ready";
      if (payload.core_odds_updated_at) {
        parts.push(`Odds updated: ${formatTimestamp(payload.core_odds_updated_at)}`);
      }
      if (payload.refresh_soccer && payload.soccer_odds_updated_at) {
        parts.push(`Soccer odds: ${formatTimestamp(payload.soccer_odds_updated_at)}`);
      }
      parts.push(`Auto odds refresh: ${payload.refresh_seconds > 0 ? `${payload.refresh_seconds}s` : "off"}`);
      parts.push(`Sports: ${payload.sports}${payload.refresh_soccer ? ", soccer" : ""}`);
      if (payload.last_succeeded_at) {
        parts.push(`Last success: ${formatTimestamp(payload.last_succeeded_at)}`);
      }
      if (payload.last_error) {
        parts.push(`Last error: ${payload.last_error}`);
      }
      detail.textContent = parts.join(" • ");
      detail.textContent = parts.join(" • ");
      pill.textContent = running ? "Refreshing" : "Idle";
      button.disabled = running;
    }

    async function triggerRefresh() {
      await fetchJson("/api/refresh", { method: "POST" });
      await loadStatus();
    }

    async function poll() {
      try {
        await loadStatus();
        await loadDocuments();
        if (state.activeId) {
          await loadDocument(state.activeId, false);
        }
      } catch (error) {
        document.getElementById("status-line").textContent = "Dashboard fetch failed";
        document.getElementById("status-detail").textContent = String(error);
      }
    }

    document.getElementById("refresh-now").addEventListener("click", triggerRefresh);
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
        return {
            "id": doc["id"],
            "category": doc["category"],
            "label": doc["label"],
            "sport": doc["sport"],
            "filename": doc["filename"],
            "updated_at": doc["updated_at"],
            "odds_updated_at": doc["odds_updated_at"],
            "html": markdown_to_html(markdown_text),
        }
    return None


def build_handler(context: DashboardContext) -> type[BaseHTTPRequestHandler]:
    class DashboardHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path in {"/", "/index.html"}:
                body = _html_shell().encode("utf8")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/html; charset=utf-8")
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
            _json_response(self, {"error": "not found"}, status=404)

        def do_POST(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path != "/api/refresh":
                _json_response(self, {"error": "not found"}, status=404)
                return
            started = context.trigger_refresh()
            _json_response(self, {"started": started, "status": context.status_payload()})

        def log_message(self, format: str, *args: object) -> None:  # noqa: A003
            return

    return DashboardHandler


def main() -> int:
    args = parse_args()
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
