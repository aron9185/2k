from __future__ import annotations

import argparse
import base64
import html
import json
import os
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
GOLF_MARKETS_CSV = BASE_DIR / "sportsbook_markets_golf_live.csv"
UFC_MARKETS_CSV = BASE_DIR / "sportsbook_markets_ufc_live.csv"
LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
ACTION_TOKEN_RE = re.compile(r"\s*<!--REAL_ACTIONS:([A-Za-z0-9_\-=]+)-->\s*$")
SOURCE_LINES_TOKEN_RE = re.compile(r"\s*<!--REAL_SOURCE_LINES:([A-Za-z0-9_\-=]+)-->\s*$")
ROW_META_TOKEN_RE = re.compile(r"\s*<!--REAL_ROW:([A-Za-z0-9_\-=]+)-->\s*$")
DEFAULT_REAL_COMMENT_GROUP_ID = 33162
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
        "label": "Soccer Vote Sheet",
        "sport": "soccer",
        "stable_path": DASHBOARD_DIR / "soccer.md",
        "fallback_glob": "soccer_v*.md",
    },
    {
        "id": "golf-vote",
        "category": "Vote Sheets",
        "label": "Golf Vote Sheet",
        "sport": "golf",
        "stable_path": DASHBOARD_DIR / "golf.md",
        "fallback_glob": "golf_v*.md",
    },
    {
        "id": "ufc-vote",
        "category": "Vote Sheets",
        "label": "UFC Vote Sheet",
        "sport": "ufc",
        "stable_path": DASHBOARD_DIR / "ufc.md",
        "fallback_glob": "ufc_v*.md",
    },
    {
        "id": "live-polls",
        "category": "Live",
        "label": "Live Poll Recommendations",
        "sport": "live",
        "refresh_target": "live-polls",
        "stable_path": DASHBOARD_DIR / "live_polls.md",
        "fallback_glob": "live_polls_v*.md",
    },
    {
        "id": "live-predictions",
        "category": "Live",
        "label": "Live Prediction Markets",
        "sport": "predictions",
        "stable_path": DASHBOARD_DIR / "live_predictions.md",
        "fallback_glob": "live_predictions_v*.md",
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
    {
        "id": "soccer-predictions",
        "category": "Predictions",
        "label": "Soccer Predictions",
        "sport": "soccer",
        "stable_path": DASHBOARD_DIR / "soccer_predictions.md",
        "fallback_glob": "soccer_predictions_v*.md",
    },
]
CATEGORY_ORDER = {
    "Vote Sheets": 0,
    "Live": 1,
    "Predictions": 2,
}
REFRESHABLE_VOTE_SPORTS = {"mlb", "nba", "nhl", "wnba", "soccer", "golf", "ufc"}
REFRESHABLE_PREDICTION_SPORTS = {"mlb", "nba", "nhl", "soccer"}
REFRESHABLE_TARGETS = {"live-polls"}
REFRESH_TARGET_ALIASES = {
    "live": "live-polls",
    "live_poll": "live-polls",
    "live-poll": "live-polls",
    "live_polls": "live-polls",
    "live-polls": "live-polls",
}
SPORT_LABELS = {
    "mlb": "MLB",
    "nba": "NBA",
    "nhl": "NHL",
    "wnba": "WNBA",
    "soccer": "Soccer",
    "golf": "Golf",
    "ufc": "UFC",
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
        default="mlb,nba,nhl,wnba,golf,ufc",
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
    sport_key = str(sport or "").strip().lower()
    if sport_key == "soccer":
        return SOCCER_MARKETS_CSV
    if sport_key == "golf":
        return GOLF_MARKETS_CSV
    if sport_key == "ufc":
        return UFC_MARKETS_CSV
    return CORE_MARKETS_CSV


def _odds_updated_at_for_sport(sport: str) -> str:
    return _path_updated_at(_odds_source_path_for_sport(sport))


def _refresh_label_for_doc(spec: dict[str, object]) -> str:
    target = str(spec.get("refresh_target") or "").strip().lower()
    if target == "live-polls":
        return "Refresh live polls"
    sport = str(spec.get("sport") or "").strip().lower()
    label = SPORT_LABELS.get(sport, sport.upper())
    if str(spec.get("category") or "") == "Predictions":
        return f"Refresh {label} predictions"
    return f"Refresh {label} pre-game data"


def _can_refresh_doc(spec: dict[str, object]) -> bool:
    target = str(spec.get("refresh_target") or "").strip().lower()
    if target in REFRESHABLE_TARGETS:
        return True
    sport = str(spec.get("sport") or "").strip().lower()
    category = str(spec.get("category") or "")
    if category == "Vote Sheets":
        return sport in REFRESHABLE_VOTE_SPORTS
    if category == "Predictions":
        return sport in REFRESHABLE_PREDICTION_SPORTS
    return False


def _existing_document_paths() -> list[dict[str, object]]:
    documents: list[dict[str, object]] = []
    for order, spec in enumerate(DOC_SPECS):
        stable_path = Path(spec["stable_path"])
        source_path = stable_path if stable_path.exists() else _latest_matching_file(str(spec["fallback_glob"]))
        can_refresh = _can_refresh_doc(spec)
        if (source_path is None or not source_path.exists()) and not can_refresh:
            continue
        stat = source_path.stat() if source_path is not None and source_path.exists() else None
        documents.append(
            {
                "id": spec["id"],
                "category": spec["category"],
                "label": spec["label"],
                "sport": spec["sport"],
                "path": source_path,
                "mtime": stat.st_mtime if stat is not None else 0.0,
                "updated_at": (
                    datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()
                    if stat is not None
                    else ""
                ),
                "odds_updated_at": _odds_updated_at_for_sport(str(spec["sport"])),
                "filename": source_path.name if source_path is not None else stable_path.name,
                "order": order,
                "can_refresh": can_refresh,
                "refresh_sport": str(spec["sport"]) if can_refresh else "",
                "refresh_target": str(spec.get("refresh_target") or "") if can_refresh else "",
                "refresh_label": _refresh_label_for_doc(spec) if can_refresh else "",
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


def _decode_action_payload(cell: str) -> tuple[str, dict[str, object] | None]:
    text = str(cell or "")
    match = ACTION_TOKEN_RE.search(text)
    if not match:
        return text, None
    visible_text = text[: match.start()].strip()
    encoded = match.group(1)
    try:
        padded = encoded + ("=" * (-len(encoded) % 4))
        decoded = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf8")
        payload = json.loads(decoded)
    except Exception:
        return visible_text, None
    return visible_text, payload if isinstance(payload, dict) else None


def _decode_source_lines(cell: str) -> tuple[str, str]:
    text = str(cell or "")
    match = SOURCE_LINES_TOKEN_RE.search(text)
    if not match:
        return text, ""
    visible_text = text[: match.start()].strip()
    encoded = match.group(1)
    try:
        padded = encoded + ("=" * (-len(encoded) % 4))
        decoded = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf8")
    except Exception:
        return visible_text, ""
    return visible_text, decoded.strip()


def _decode_row_meta(cell: str) -> tuple[str, dict[str, object]]:
    text = str(cell or "")
    match = ROW_META_TOKEN_RE.search(text)
    if not match:
        return text, {}
    visible_text = text[: match.start()].strip()
    encoded = match.group(1)
    try:
        padded = encoded + ("=" * (-len(encoded) % 4))
        decoded = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf8")
        payload = json.loads(decoded)
    except Exception:
        return visible_text, {}
    return visible_text, payload if isinstance(payload, dict) else {}


def _row_meta_attrs(payload: dict[str, object]) -> str:
    if not payload:
        return ""
    allowed_keys = {
        "post_id",
        "poll_id",
        "sport",
        "poll_kind",
        "group_id",
        "section_group_id",
        "header",
        "content_text",
        "game_label",
        "game_id",
    }
    attrs: list[str] = []
    for key in sorted(allowed_keys):
        value = str(payload.get(key) or "").strip()
        if not value:
            continue
        data_key = key.replace("_", "-")
        attrs.append(f' data-{data_key}="{html.escape(value, quote=True)}"')
    return "".join(attrs)


def _header_index(header_cells: list[str], target: str) -> int | None:
    normalized_target = target.strip().lower()
    for index, cell in enumerate(header_cells):
        if str(cell or "").strip().lower() == normalized_target:
            return index
    return None


def _compact_decimal_label(number_text: str) -> str:
    return number_text.rstrip("0").rstrip(".") if "." in number_text else number_text


def _compact_action_ev_label(ev_text: str) -> str:
    text = str(ev_text or "").strip()
    percent_match = re.fullmatch(r"([+-]?)(\d+(?:\.\d+)?)%", text)
    if percent_match:
        sign, number_text = percent_match.groups()
        return f"{sign}{_compact_decimal_label(number_text)}%"

    rax_match = re.fullmatch(r"([+-]?)(\d+(?:\.\d+)?)(\s+Rax)", text)
    if rax_match:
        sign, number_text, suffix = rax_match.groups()
        return f"{sign}{_compact_decimal_label(number_text)}{suffix}"

    return text


def _source_tooltip_text(row: list[str], sportsbook_index: int | None) -> str:
    if sportsbook_index is None or sportsbook_index >= len(row):
        return ""
    text = str(row[sportsbook_index] or "").strip()
    if not text or text.lower() in {"no sportsbook match", "proxy unavailable"}:
        return ""
    return text


def _source_tooltip_attr(source_lines: str) -> str:
    text = str(source_lines or "").strip()
    if not text:
        return ""
    tooltip = "Sportsbook lines:\n" + text
    return f' title="{html.escape(tooltip, quote=True)}"'


def _render_action_select(visible_text: str, payload: dict[str, object]) -> str:
    actions = payload.get("actions")
    if not isinstance(actions, list) or not actions:
        return _render_inline(visible_text)
    default_text = str(payload.get("default") or visible_text or "").strip()
    html_parts = ['<select class="selection-put-select" onchange="updateSelectionPut(this)">']
    for action in actions:
        if not isinstance(action, dict):
            continue
        selection_put = str(action.get("selection_put") or "").strip()
        if not selection_put:
            continue
        selected = " selected" if selection_put == default_text else ""
        attrs = {
            "value": selection_put,
            "data-selection": str(action.get("selection") or ""),
            "data-amount": str(action.get("amount") or ""),
            "data-consensus": str(action.get("consensus") or ""),
            "data-ev": str(action.get("ev") or ""),
            "data-sportsbook": str(action.get("sportsbook") or ""),
            "data-source": str(action.get("source") or ""),
            "data-source-lines": str(action.get("source_lines") or ""),
        }
        attr_text = "".join(
            f' {name}="{html.escape(value, quote=True)}"'
            for name, value in attrs.items()
        )
        label = str(action.get("display_label") or "").strip() or selection_put
        ev_text = str(action.get("ev") or "").strip()
        if ev_text and not action.get("display_label"):
            label = f"{label} | {_compact_action_ev_label(ev_text)}"
        html_parts.append(f"<option{attr_text}{selected}>{html.escape(label)}</option>")
    html_parts.append("</select>")
    return "".join(html_parts)


def _render_table(
    lines: list[str],
    start_index: int,
    row_metadata: list[dict[str, object]] | None = None,
    row_metadata_index: int = 0,
) -> tuple[str, int, int]:
    header_cells = _split_markdown_row(lines[start_index])
    poll_index = _header_index(header_cells, "Poll")
    selection_index = _header_index(header_cells, "Selection+Put")
    consensus_index = _header_index(header_cells, "Consensus Prob (Odds)")
    ev_index = _header_index(header_cells, "EV")
    sportsbook_index = _header_index(header_cells, "Sportsbook Odds")
    source_index = _header_index(header_cells, "Source")
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
    is_poll_pick_table = poll_index is not None and selection_index is not None
    for row in body_rows:
        row = list(row)
        row_meta: dict[str, object] = {}
        if poll_index is not None and poll_index < len(row):
            visible_poll, row_meta = _decode_row_meta(row[poll_index])
            row[poll_index] = visible_poll
        if (
            not row_meta
            and is_poll_pick_table
            and row_metadata is not None
            and row_metadata_index < len(row_metadata)
        ):
            candidate = row_metadata[row_metadata_index]
            if isinstance(candidate, dict):
                row_meta = candidate
            row_metadata_index += 1
        row_source_lines = ""
        if source_index is not None and source_index < len(row):
            visible_source, row_source_lines = _decode_source_lines(row[source_index])
            if row_source_lines:
                row[source_index] = visible_source
        html_parts.append(f"<tr{_row_meta_attrs(row_meta)}>")
        for cell_index, cell in enumerate(row):
            data_field = ""
            if cell_index == consensus_index:
                data_field = ' data-action-field="consensus"'
            elif cell_index == ev_index:
                data_field = ' data-action-field="ev"'
            elif cell_index == sportsbook_index:
                data_field = ' data-action-field="sportsbook"'
                if row_source_lines:
                    data_field += _source_tooltip_attr(row_source_lines)
            elif cell_index == source_index:
                data_field = ' data-action-field="source"'
                if row_source_lines:
                    data_field += _source_tooltip_attr(row_source_lines)
            if cell_index == selection_index:
                visible_cell, action_payload = _decode_action_payload(cell)
                if action_payload is not None:
                    html_parts.append(f"<td>{_render_action_select(visible_cell, action_payload)}</td>")
                    continue
                cell = visible_cell
            html_parts.append(f"<td{data_field}>{_render_inline(cell)}</td>")
        html_parts.append("</tr>")
    html_parts.append("</tbody></table></div>")
    return "".join(html_parts), index, row_metadata_index


def markdown_to_html(
    markdown_text: str,
    row_metadata: list[dict[str, object]] | None = None,
) -> str:
    lines = markdown_text.splitlines()
    html_parts: list[str] = []
    index = 0
    row_metadata_index = 0
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
            table_html, next_index, row_metadata_index = _render_table(
                lines,
                index,
                row_metadata,
                row_metadata_index,
            )
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
    progress_line: str = ""
    progress_log: list[str] = field(default_factory=list)
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

    def _refresh_command(
        self,
        *,
        target: str | None = None,
        sports: str | None = None,
        refresh_soccer: bool | None = None,
        only_predictions: bool = False,
    ) -> list[str]:
        if str(target or "").strip().lower() == "live-polls":
            return [
                sys.executable,
                "-B",
                str(BASE_DIR / "refresh_dashboard_data.py"),
                "--only-live-polls",
                "--markets-csv",
                str(CORE_MARKETS_CSV),
                "--dashboard-dir",
                str(DASHBOARD_DIR),
            ]
        selected_sports = _normalize_sports_arg(self.sports if sports is None else sports)
        selected_refresh_soccer = self.refresh_soccer if refresh_soccer is None else bool(refresh_soccer)
        command = [
            sys.executable,
            "-B",
            str(BASE_DIR / "refresh_dashboard_data.py"),
            "--sports",
            selected_sports,
            "--season",
            self.season,
            "--dashboard-dir",
            str(DASHBOARD_DIR),
        ]
        if selected_refresh_soccer:
            command.append("--refresh-soccer")
        if only_predictions:
            command.append("--only-predictions")
        if sports is not None and not only_predictions:
            command.append("--skip-live-polls")
        return command

    def trigger_refresh(
        self,
        *,
        target: str | None = None,
        sports: str | None = None,
        refresh_soccer: bool | None = None,
        only_predictions: bool = False,
        label: str = "",
    ) -> bool:
        command = self._refresh_command(
            target=target,
            sports=sports,
            refresh_soccer=refresh_soccer,
            only_predictions=only_predictions,
        )
        with self.state.lock:
            if self.state.running:
                return False
            self.state.running = True
            self.state.active_label = label or "configured sports"
            self.state.progress_line = "Starting refresh..."
            self.state.progress_log = [self.state.progress_line]
            self.state.last_started_at = datetime.now(timezone.utc).isoformat()
            self.state.last_error = ""
        thread = threading.Thread(target=self._run_refresh, args=(command,), daemon=True)
        thread.start()
        return True

    def _run_refresh(self, command: list[str]) -> None:
        started = time.time()
        exit_code = 0
        error_text = ""
        try:
            env = dict(os.environ)
            env["PYTHONUNBUFFERED"] = "1"
            process = subprocess.Popen(
                command,
                cwd=str(ROOT_DIR),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                env=env,
            )
            assert process.stdout is not None
            for raw_line in process.stdout:
                line = raw_line.rstrip()
                if not line:
                    continue
                print(line, flush=True)
                with self.state.lock:
                    self.state.progress_line = line
                    self.state.progress_log.append(line)
                    self.state.progress_log = self.state.progress_log[-12:]
            exit_code = int(process.wait() or 0)
            if exit_code:
                error_text = f"Refresh command failed with exit code {exit_code}."
        except Exception as exc:
            exit_code = 1
            error_text = str(exc)
        finished_at = datetime.now(timezone.utc).isoformat()
        duration = time.time() - started
        with self.state.lock:
            self.state.running = False
            self.state.active_label = ""
            final_progress = (
                f"Refresh finished in {duration:.1f}s."
                if exit_code == 0
                else error_text
            )
            self.state.progress_line = final_progress
            if final_progress:
                self.state.progress_log.append(final_progress)
                self.state.progress_log = self.state.progress_log[-12:]
            self.state.last_finished_at = finished_at
            self.state.last_duration_seconds = duration
            self.state.last_exit_code = exit_code
            self.state.last_error = error_text
            self.state.runs += 1
            if exit_code == 0:
                self.state.last_succeeded_at = finished_at

    def start_refresh_loop(self, *, refresh_on_start: bool) -> None:
        if refresh_on_start:
            self.trigger_refresh(label="configured sports")
        if self.refresh_seconds <= 0:
            return

        def _loop() -> None:
            while not self._stop_event.wait(self.refresh_seconds):
                self.trigger_refresh(label="configured sports")

        self._loop_thread = threading.Thread(target=_loop, daemon=True)
        self._loop_thread.start()

    def stop(self) -> None:
        self._stop_event.set()

    def status_payload(self) -> dict[str, object]:
        with self.state.lock:
            return {
                "running": self.state.running,
                "active_refresh": self.state.active_label,
                "progress_line": self.state.progress_line,
                "progress_log": list(self.state.progress_log),
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
                "golf_odds_updated_at": _path_updated_at(GOLF_MARKETS_CSV),
                "ufc_odds_updated_at": _path_updated_at(UFC_MARKETS_CSV),
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
      align-items: flex-start;
      justify-content: space-between;
      gap: 1rem;
      margin-bottom: 1.2rem;
      flex-wrap: wrap;
    }
    .doc-header-main {
      min-width: 16rem;
    }
    .doc-header-actions {
      display: flex;
      align-items: center;
      gap: 0.75rem;
      flex-wrap: wrap;
    }
    .doc-title {
      margin: 0;
      font-family: "Iowan Old Style", "Palatino Linotype", "Book Antiqua", Georgia, serif;
      font-size: 2rem;
    }
    .doc-updated {
      margin-top: 0.25rem;
      color: var(--muted);
      font-size: 0.92rem;
    }
    .doc-refresh[hidden] {
      display: none;
    }
    .doc-html h1, .doc-html h2, .doc-html h3 {
      font-family: "Iowan Old Style", "Palatino Linotype", "Book Antiqua", Georgia, serif;
      margin: 1.2rem 0 0.55rem;
      line-height: 1.1;
    }
    .doc-html h1 { font-size: 2rem; margin-top: 0; }
    .doc-html h2 { font-size: 1.55rem; }
    .doc-html h3 { font-size: 1.15rem; letter-spacing: 0.02em; }
    .game-copy-row {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 0.75rem;
      margin: 1.2rem 0 0.55rem;
      flex-wrap: wrap;
    }
    .game-copy-row h2 {
      margin: 0;
    }
    .copy-game-picks,
    .post-game-picks {
      padding: 0.48rem 0.78rem;
      background: rgba(15, 92, 77, 0.09);
      color: var(--accent);
      font-size: 0.82rem;
      box-shadow: inset 0 0 0 1px rgba(15, 92, 77, 0.16);
    }
    .copy-game-picks:hover,
    .post-game-picks:hover {
      background: rgba(15, 92, 77, 0.14);
    }
    .copy-game-picks.copied,
    .post-game-picks.posted {
      background: var(--accent);
      color: white;
      box-shadow: none;
    }
    .post-game-picks.failed {
      background: rgba(189, 45, 45, 0.12);
      color: #9a2d2d;
      box-shadow: inset 0 0 0 1px rgba(154, 45, 45, 0.2);
    }
    .daily-lineup-copy-row {
      margin-top: 0;
    }
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
    td[data-action-field="sportsbook"][title],
    td[data-action-field="source"][title] {
      cursor: help;
      text-decoration: underline dotted rgba(15, 92, 77, 0.45);
      text-underline-offset: 0.18rem;
    }
    .odds-stack {
      display: flex;
      align-items: flex-start;
      gap: 0.35rem;
      flex-wrap: wrap;
    }
    .odds-book {
      display: inline-flex;
      align-items: center;
      min-height: 1.65rem;
      padding: 0 0.45rem;
      border-radius: 0.35rem;
      background: rgba(29, 36, 51, 0.08);
      color: #4a5668;
      font-size: 0.76rem;
      font-weight: 800;
      letter-spacing: 0.04em;
    }
    .odds-pill {
      display: inline-flex;
      align-items: baseline;
      gap: 0.35rem;
      min-height: 1.65rem;
      padding: 0.16rem 0.5rem;
      border-radius: 0.35rem;
      background: rgba(15, 92, 77, 0.08);
      box-shadow: inset 0 0 0 1px rgba(15, 92, 77, 0.12);
      white-space: nowrap;
    }
    .odds-label {
      color: #355148;
      font-size: 0.78rem;
      font-weight: 700;
    }
    .odds-price {
      color: #11251f;
      font-size: 0.88rem;
      font-weight: 850;
      font-variant-numeric: tabular-nums;
    }
    .odds-price.positive {
      color: #0d684f;
    }
    .odds-price.negative {
      color: #8a3e1f;
    }
    .selection-put-select {
      width: 100%;
      min-width: 12rem;
      border: 1px solid rgba(15, 92, 77, 0.24);
      border-radius: 0.72rem;
      background: linear-gradient(180deg, #fffaf1, #f6ead7);
      color: var(--ink);
      padding: 0.48rem 2rem 0.48rem 0.62rem;
      font: inherit;
      font-weight: 700;
      box-shadow: 0 8px 20px rgba(87, 62, 32, 0.08);
      cursor: pointer;
    }
    .selection-put-select:focus {
      outline: 3px solid rgba(15, 92, 77, 0.18);
      border-color: var(--accent);
    }
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
        <p class="subcopy">Pregame sheets, live recommendations, and prediction sheets in one place.</p>
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
          <div class="doc-header-main">
            <h2 class="doc-title" id="doc-title">Loading…</h2>
            <div class="doc-updated" id="doc-updated"></div>
          </div>
          <div class="doc-header-actions">
            <button id="refresh-doc" class="doc-refresh" type="button" hidden>Refresh sport</button>
          </div>
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
      activeDoc: null,
      activeUpdatedAt: "",
      refreshRunning: false,
      pollMs: 15000,
      livePollAutoRefreshMs: 60000,
      lastLivePollAutoRefreshAt: 0,
    };

    async function fetchJson(url, options) {
      const response = await fetch(url, options);
      if (!response.ok) {
        let message = `Request failed: ${response.status}`;
        try {
          const payload = await response.json();
          if (payload && payload.error) message = payload.error;
        } catch (error) {
          // Keep the generic status message.
        }
        throw new Error(message);
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

    function updateSelectionPut(select) {
      const option = select.selectedOptions && select.selectedOptions[0];
      const row = select.closest("tr");
      if (!option || !row) return;
      const fieldMap = {
        consensus: option.dataset.consensus || "",
        ev: option.dataset.ev || "",
        sportsbook: option.dataset.sportsbook || "",
        source: option.dataset.source || "",
      };
      for (const [field, value] of Object.entries(fieldMap)) {
        const cell = row.querySelector(`[data-action-field="${field}"]`);
        if (cell) {
          cell.textContent = value;
          if (field === "source" || field === "sportsbook") {
            const sourceLines = option.dataset.sourceLines || "";
            if (sourceLines) {
              cell.title = `Sportsbook lines:\n${sourceLines}`;
            } else {
              cell.removeAttribute("title");
            }
          }
          if (field === "sportsbook") {
            if (value) {
              cell.dataset.oddsText = value;
            } else {
              delete cell.dataset.oddsText;
            }
            renderOddsCell(cell);
          }
        }
      }
    }

    function hydrateSelectionPutControls() {
      for (const select of document.querySelectorAll(".selection-put-select")) {
        updateSelectionPut(select);
      }
    }

    function splitBookPrefix(text) {
      const match = String(text || "").trim().match(/^([A-Z][A-Z+ ]{1,12}):\\s+(.+)$/);
      if (!match) return { book: "", body: String(text || "").trim() };
      return { book: match[1].trim(), body: match[2].trim() };
    }

    function parseOddsPart(part) {
      const text = String(part || "").replace(/\\s+/g, " ").trim();
      if (!text) return null;
      const oddsMatch = text.match(/^(.*?)([+-]\\d+)$/);
      if (oddsMatch) {
        return {
          label: oddsMatch[1].trim(),
          price: oddsMatch[2],
          className: oddsMatch[2].startsWith("+") ? "positive" : "negative",
        };
      }
      const percentMatch = text.match(/^(.*?)(\\d+(?:\\.\\d+)?%)$/);
      if (percentMatch) {
        return { label: percentMatch[1].trim(), price: percentMatch[2], className: "" };
      }
      return { label: text, price: "", className: "" };
    }

    function renderOddsCell(cell) {
      const rawText = String(cell.dataset.oddsText || cell.textContent || "").replace(/\\s+/g, " ").trim();
      if (rawText) {
        cell.dataset.oddsText = rawText;
      } else {
        delete cell.dataset.oddsText;
      }
      cell.classList.remove("odds-cell");
      if (!rawText || rawText.length > 140 || /^(?:no sportsbook match|proxy unavailable)$/i.test(rawText)) {
        cell.textContent = rawText;
        return;
      }
      const { book, body } = splitBookPrefix(rawText);
      const parts = body.split(" / ").map(parseOddsPart).filter(Boolean);
      if (!parts.length || (parts.length === 1 && !parts[0].price && !book)) {
        cell.textContent = rawText;
        return;
      }
      cell.textContent = "";
      cell.classList.add("odds-cell");
      const stack = document.createElement("span");
      stack.className = "odds-stack";
      if (book) {
        const bookNode = document.createElement("span");
        bookNode.className = "odds-book";
        bookNode.textContent = book;
        stack.appendChild(bookNode);
      }
      for (const part of parts) {
        const pill = document.createElement("span");
        pill.className = "odds-pill";
        const label = document.createElement("span");
        label.className = "odds-label";
        label.textContent = part.label;
        pill.appendChild(label);
        if (part.price) {
          const price = document.createElement("span");
          price.className = `odds-price ${part.className}`.trim();
          price.textContent = part.price;
          pill.appendChild(price);
        }
        stack.appendChild(pill);
      }
      cell.appendChild(stack);
    }

    function hydrateOddsCells() {
      for (const cell of document.querySelectorAll('td[data-action-field="sportsbook"]')) {
        renderOddsCell(cell);
      }
    }

    function isGamePollHeading(heading) {
      const text = (heading.textContent || "").trim();
      return /^(?:[A-Z]+\\s*[-:]\\s*)?[A-Z0-9 .'-]+\\s+@\\s+[A-Z0-9 .'-]+$/.test(text);
    }

    function getTableColumnIndex(table, label) {
      const wanted = label.toLowerCase();
      const headers = Array.from(table.querySelectorAll("thead th"));
      return headers.findIndex((header) => (header.textContent || "").trim().toLowerCase() === wanted);
    }

    function findSectionTable(heading, predicate) {
      let node = heading.nextElementSibling;
      while (node && node.tagName !== "H2") {
        const table = node.matches(".table-wrap")
          ? node.querySelector("table")
          : node.querySelector && node.querySelector(".table-wrap table");
        if (table && predicate(table)) {
          return table;
        }
        node = node.nextElementSibling;
      }
      return null;
    }

    function findPollPickTable(heading) {
      return findSectionTable(
        heading,
        (table) => getTableColumnIndex(table, "Poll") >= 0 && getTableColumnIndex(table, "Selection+Put") >= 0
      );
    }

    function findDailyLineupTable(heading) {
      return findSectionTable(heading, (table) => getTableColumnIndex(table, "Player") >= 0);
    }

    function findLineupContestTable(heading) {
      return findSectionTable(
        heading,
        (table) => getTableColumnIndex(table, "Action") >= 0 && getTableColumnIndex(table, "Top 5") >= 0
      );
    }

    function cleanSelectionPutValue(value) {
      let text = String(value || "").replace(/\\s+/g, " ").trim();
      const dividerIndex = text.indexOf("|");
      if (dividerIndex >= 0) {
        text = text.slice(0, dividerIndex).trim();
      }
      if (!text || text.toLowerCase() === "nomarket") return "";
      const overUnderMatch = text.match(/^(Over|Under)([0-9]+)$/i);
      if (overUnderMatch) {
        const side = overUnderMatch[1].charAt(0).toUpperCase() + overUnderMatch[1].slice(1).toLowerCase();
        return `${side} ${overUnderMatch[2]}`;
      }
      return text;
    }

    function collectGamePickText(table) {
      const selectionIndex = getTableColumnIndex(table, "Selection+Put");
      if (selectionIndex < 0) return "";
      const picks = [];
      for (const row of table.querySelectorAll("tbody tr")) {
        const cell = row.cells[selectionIndex];
        if (!cell) continue;
        const select = cell.querySelector(".selection-put-select");
        const value = cleanSelectionPutValue(select ? select.value : cell.textContent);
        if (value) picks.push(value);
      }
      return picks.join("\\n");
    }

    function cellText(row, index) {
      if (index < 0 || index >= row.cells.length) return "";
      const cell = row.cells[index];
      if (!cell) return "";
      return String(cell.dataset.oddsText || cell.textContent || "").replace(/\\s+/g, " ").trim();
    }

    function selectedAction(row, selectionIndex) {
      const cell = row.cells[selectionIndex];
      const select = cell ? cell.querySelector(".selection-put-select") : null;
      const option = select && select.selectedOptions ? select.selectedOptions[0] : null;
      const value = cleanSelectionPutValue(option ? option.value : cellText(row, selectionIndex));
      return {
        value,
        selection: option ? (option.dataset.selection || value) : value,
        amount: option ? (option.dataset.amount || "") : "",
        consensus: option ? (option.dataset.consensus || "") : "",
        ev: option ? (option.dataset.ev || "") : "",
        sportsbook: option ? (option.dataset.sportsbook || "") : "",
        source: option ? (option.dataset.source || "") : "",
      };
    }

    function collectGameRecommendationRows(table) {
      const pollIndex = getTableColumnIndex(table, "Poll");
      const selectionIndex = getTableColumnIndex(table, "Selection+Put");
      const consensusIndex = getTableColumnIndex(table, "Consensus Prob (Odds)");
      const evIndex = getTableColumnIndex(table, "EV");
      const sportsbookIndex = getTableColumnIndex(table, "Sportsbook Odds");
      const sourceIndex = getTableColumnIndex(table, "Source");
      if (pollIndex < 0 || selectionIndex < 0) return [];
      const rows = [];
      for (const row of table.querySelectorAll("tbody tr")) {
        const action = selectedAction(row, selectionIndex);
        if (!action.value) continue;
        rows.push({
          element: row,
          postId: row.dataset.postId || "",
          pollId: row.dataset.pollId || "",
          pollKind: row.dataset.pollKind || "",
          groupId: row.dataset.groupId || row.dataset.sectionGroupId || "",
          sectionGroupId: row.dataset.sectionGroupId || row.dataset.groupId || "",
          poll: cellText(row, pollIndex),
          selectionPut: action.value,
          selection: action.selection,
          amount: action.amount,
          consensus: action.consensus || cellText(row, consensusIndex),
          ev: action.ev || cellText(row, evIndex),
          sportsbook: action.sportsbook || cellText(row, sportsbookIndex),
          source: action.source || cellText(row, sourceIndex),
        });
      }
      return rows;
    }

    function postTargetPriority(pollKind) {
      const priority = {
        game_winner: 0,
        game_spread: 1,
        game_total: 2,
        period_total_yes_no: 3,
        both_teams_score: 4,
        halftime_result: 5,
        double_chance: 6,
      };
      return Object.prototype.hasOwnProperty.call(priority, pollKind) ? priority[pollKind] : 50;
    }

    function choosePostTarget(rows) {
      return rows
        .filter((row) => row.postId)
        .slice()
        .sort((a, b) => postTargetPriority(a.pollKind) - postTargetPriority(b.pollKind))[0] || null;
    }

    function buildRecommendationComment(heading, table) {
      const rows = collectGameRecommendationRows(table);
      return { text: collectGamePickText(table), target: choosePostTarget(rows) };
    }

    function collectDailyLineupNames(table) {
      const playerIndex = getTableColumnIndex(table, "Player");
      if (playerIndex < 0) return "";
      const names = [];
      for (const row of table.querySelectorAll("tbody tr")) {
        const cell = row.cells[playerIndex];
        const name = String(cell ? cell.textContent : "").replace(/\\s+/g, " ").trim();
        if (name) names.push(name);
      }
      return names.join("\\n");
    }

    function parseLineupContestNames(value) {
      return String(value || "")
        .split(">")
        .map((part) => part.replace(/^\\s*\\d+\\.\\s*/, "").replace(/\\s+/g, " ").trim())
        .filter(Boolean);
    }

    function collectLineupContestText(table) {
      const actionIndex = getTableColumnIndex(table, "Action");
      const topFiveIndex = getTableColumnIndex(table, "Top 5");
      if (topFiveIndex < 0) return "";
      const lineups = [];
      for (const row of table.querySelectorAll("tbody tr")) {
        const actionCell = actionIndex >= 0 ? row.cells[actionIndex] : null;
        const topFiveCell = row.cells[topFiveIndex];
        const action = String(actionCell ? actionCell.textContent : "").replace(/\\s+/g, " ").trim();
        const names = parseLineupContestNames(topFiveCell ? topFiveCell.textContent : "");
        if (!names.length) continue;
        lineups.push((action ? [action, ...names] : names).join("\\n"));
      }
      return lineups.join("\\n\\n");
    }

    async function copyText(text) {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(text);
        return;
      }
      const textarea = document.createElement("textarea");
      textarea.value = text;
      textarea.setAttribute("readonly", "");
      textarea.style.position = "fixed";
      textarea.style.left = "-9999px";
      document.body.appendChild(textarea);
      textarea.select();
      document.execCommand("copy");
      textarea.remove();
    }

    function setCopyButtonState(button, label, copied) {
      window.clearTimeout(button.copyResetTimer);
      button.textContent = label;
      button.classList.toggle("copied", !!copied);
      button.copyResetTimer = window.setTimeout(() => {
        button.textContent = button.dataset.defaultLabel || "Copy Picks";
        button.classList.remove("copied");
      }, copied ? 1500 : 1800);
    }

    function setPostButtonState(button, label, stateName, disabled) {
      window.clearTimeout(button.postResetTimer);
      button.textContent = label;
      button.disabled = !!disabled;
      button.classList.toggle("posted", stateName === "posted");
      button.classList.toggle("failed", stateName === "failed");
      if (stateName === "posted" || stateName === "failed") {
        button.postResetTimer = window.setTimeout(() => {
          button.textContent = button.dataset.defaultLabel || "Post Picks";
          button.classList.remove("posted", "failed");
          button.disabled = false;
        }, stateName === "posted" ? 2200 : 3000);
      }
    }

    function attachHeadingPostButton(row, heading, table, scope) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "post-game-picks";
      button.textContent = "Post Picks";
      button.dataset.defaultLabel = "Post Picks";
      button.title = `Post current recommendations as a Real comment on this ${scope}'s game-lines poll`;
      button.addEventListener("click", async () => {
        const built = buildRecommendationComment(heading, table);
        if (!built.text) {
          setPostButtonState(button, "Nothing to post", "failed", false);
          return;
        }
        if (!built.target || !built.target.postId) {
          setPostButtonState(button, "No post id", "failed", false);
          return;
        }
        const groupId = built.target.groupId || built.target.sectionGroupId || "";
        const groupText = groupId ? ` in group ${groupId}` : "";
        if (!window.confirm(`Post these picks to Real post ${built.target.postId}${groupText}?`)) {
          return;
        }
        setPostButtonState(button, "Posting...", "", true);
        try {
          await fetchJson("/api/post-recommendation", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ post_id: built.target.postId, group_id: groupId, text: built.text }),
          });
          setPostButtonState(button, "Posted", "posted", false);
        } catch (error) {
          setPostButtonState(button, "Post failed", "failed", false);
          button.title = String(error && error.message ? error.message : error);
        }
      });
      row.appendChild(button);
    }

    function attachHeadingCopyButton(heading, rowClassName, label, title, collectText) {
      const row = document.createElement("div");
      row.className = rowClassName;
      heading.parentNode.insertBefore(row, heading);
      row.appendChild(heading);

      const button = document.createElement("button");
      button.type = "button";
      button.className = "copy-game-picks";
      button.textContent = label;
      button.dataset.defaultLabel = label;
      button.title = title;
      button.addEventListener("click", async () => {
        const text = collectText();
        if (!text) {
          setCopyButtonState(button, "Nothing to copy", false);
          return;
        }
        try {
          await copyText(text);
          setCopyButtonState(button, "Copied", true);
        } catch (error) {
          setCopyButtonState(button, "Copy failed", false);
        }
      });
      row.appendChild(button);
      return row;
    }

    function hydratePollCopyButtons() {
      const container = document.getElementById("doc-html");
      for (const heading of Array.from(container.querySelectorAll("h2"))) {
        if (heading.closest(".game-copy-row")) continue;
        const table = findPollPickTable(heading);
        if (!table) continue;
        const scope = isGamePollHeading(heading) ? "game" : "section";
        const row = attachHeadingCopyButton(
          heading,
          "game-copy-row",
          "Copy Picks",
          `Copy current Selection+Put values for this ${scope}`,
          () => collectGamePickText(table)
        );
        attachHeadingPostButton(row, heading, table, scope);
      }
    }

    function hydrateLineupCopyButtons() {
      const container = document.getElementById("doc-html");
      for (const heading of Array.from(container.querySelectorAll("h2"))) {
        const headingText = (heading.textContent || "").trim().toLowerCase();
        if (heading.closest(".game-copy-row")) continue;
        if (headingText === "daily lineup") {
          const table = findDailyLineupTable(heading);
          if (!table) continue;
          attachHeadingCopyButton(
            heading,
            "game-copy-row daily-lineup-copy-row",
            "Copy Names",
            "Copy all Daily Lineup player names",
            () => collectDailyLineupNames(table)
          );
        } else if (headingText === "lineup contest picks") {
          const table = findLineupContestTable(heading);
          if (!table) continue;
          attachHeadingCopyButton(
            heading,
            "game-copy-row daily-lineup-copy-row",
            "Copy Lineups",
            "Copy lineup contest player names",
            () => collectLineupContestText(table)
          );
        }
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
      state.activeDoc = doc;
      state.activeUpdatedAt = doc.updated_at || "";
      document.getElementById("doc-title").textContent = doc.label;
      const updatedBits = [`Sheet updated ${formatTimestamp(doc.updated_at)}`];
      if (doc.odds_updated_at) {
        updatedBits.push(`Odds updated ${formatTimestamp(doc.odds_updated_at)}`);
      }
      document.getElementById("doc-updated").textContent = updatedBits.join(" • ");
      document.getElementById("doc-html").innerHTML = doc.html || '<div class="empty">No content found.</div>';
      hydrateSelectionPutControls();
      hydrateOddsCells();
      hydrateLineupCopyButtons();
      hydratePollCopyButtons();
      updateDocumentRefreshButton(doc);
      renderDocGroups();
    }

    function updateDocumentRefreshButton(doc) {
      const button = document.getElementById("refresh-doc");
      if (!doc || !doc.can_refresh) {
        button.hidden = true;
        return;
      }
      button.hidden = false;
      button.textContent = doc.refresh_label || `Refresh ${doc.sport.toUpperCase()} pre-game data`;
      button.disabled = state.refreshRunning;
    }

    async function loadStatus() {
      const payload = await fetchJson("/api/status");
      const running = !!payload.running;
      const line = document.getElementById("status-line");
      const detail = document.getElementById("status-detail");
      const pill = document.getElementById("refresh-pill");
      const button = document.getElementById("refresh-now");
      state.refreshRunning = running;
      const parts = [];
      line.textContent = running
        ? `Refreshing ${payload.active_refresh || "sportsbook and Real data"}...`
        : "Dashboard ready";
      if (payload.core_odds_updated_at) {
        parts.push(`Odds updated: ${formatTimestamp(payload.core_odds_updated_at)}`);
      }
      if (running && payload.progress_line) {
        parts.push(`Progress: ${payload.progress_line}`);
      }
      if (payload.refresh_soccer && payload.soccer_odds_updated_at) {
        parts.push(`Soccer odds: ${formatTimestamp(payload.soccer_odds_updated_at)}`);
      }
      if (payload.golf_odds_updated_at) {
        parts.push(`Golf odds: ${formatTimestamp(payload.golf_odds_updated_at)}`);
      }
      if (payload.ufc_odds_updated_at) {
        parts.push(`UFC odds: ${formatTimestamp(payload.ufc_odds_updated_at)}`);
      }
      parts.push(`Auto odds refresh: ${payload.refresh_seconds > 0 ? `${payload.refresh_seconds}s` : "off"}`);
      parts.push(`Sports: ${payload.sports}${payload.refresh_soccer ? ", soccer" : ""}`);
      if (payload.last_succeeded_at) {
        parts.push(`Last success: ${formatTimestamp(payload.last_succeeded_at)}`);
      }
      if (payload.last_error) {
        parts.push(`Last error: ${payload.last_error}`);
      }
      detail.textContent = parts.join(" | ");
      pill.textContent = running ? "Refreshing" : "Idle";
      button.disabled = running;
      updateDocumentRefreshButton(state.activeDoc);
    }

    function isLivePollDocument(doc) {
      return !!doc && (doc.id === "live-polls" || doc.refresh_target === "live-polls");
    }

    async function triggerRefresh() {
      await fetchJson("/api/refresh", { method: "POST" });
      await loadStatus();
    }

    async function triggerDocumentRefresh(options = {}) {
      const doc = state.activeDoc;
      if (!doc || !doc.can_refresh) return;
      if (options.auto && (state.refreshRunning || !isLivePollDocument(doc))) return;
      await fetchJson("/api/refresh", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(
          doc.refresh_target
            ? { target: doc.refresh_target }
            : {
                sport: doc.refresh_sport || doc.sport,
                category: doc.category || "",
                only_predictions: doc.category === "Predictions",
              }
        ),
      });
      if (isLivePollDocument(doc)) {
        state.lastLivePollAutoRefreshAt = Date.now();
      }
      await loadStatus();
    }

    async function maybeAutoRefreshLivePolls() {
      if (document.hidden || state.refreshRunning || !isLivePollDocument(state.activeDoc)) return;
      const now = Date.now();
      if (now - state.lastLivePollAutoRefreshAt < state.livePollAutoRefreshMs) return;
      state.lastLivePollAutoRefreshAt = now;
      try {
        await triggerDocumentRefresh({ auto: true });
      } catch (error) {
        document.getElementById("status-detail").textContent = String(error);
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
        document.getElementById("status-line").textContent = "Dashboard fetch failed";
        document.getElementById("status-detail").textContent = String(error);
      }
    }

    document.getElementById("refresh-now").addEventListener("click", triggerRefresh);
    document.getElementById("refresh-doc").addEventListener("click", triggerDocumentRefresh);
    poll();
    setInterval(poll, state.pollMs);
    setInterval(maybeAutoRefreshLivePolls, state.livePollAutoRefreshMs);
  </script>
</body>
</html>"""


def _document_payload(doc_id: str) -> dict[str, object] | None:
    for doc in _existing_document_paths():
        if str(doc["id"]) != doc_id:
            continue
        path_value = doc.get("path")
        path = Path(path_value) if path_value else None
        if path is not None and path.exists():
            markdown_text = path.read_text(encoding="utf8")
        else:
            markdown_text = f"# {doc['label']}\n\nNo sheet has been generated yet.\n"
        return {
            "id": doc["id"],
            "category": doc["category"],
            "label": doc["label"],
            "sport": doc["sport"],
            "filename": doc["filename"],
            "updated_at": doc["updated_at"],
            "odds_updated_at": doc["odds_updated_at"],
            "can_refresh": doc["can_refresh"],
            "refresh_sport": doc["refresh_sport"],
            "refresh_target": doc["refresh_target"],
            "refresh_label": doc["refresh_label"],
            "html": markdown_to_html(markdown_text, _document_row_metadata(path)),
        }
    return None


def _request_json(handler: BaseHTTPRequestHandler) -> dict[str, object]:
    length_text = handler.headers.get("Content-Length") or "0"
    try:
        length = int(length_text)
    except ValueError:
        length = 0
    if length <= 0:
        return {}
    raw = handler.rfile.read(length)
    if not raw:
        return {}
    try:
        payload = json.loads(raw.decode("utf8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _truthy_payload_flag(value: object) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off", ""}:
        return False
    return bool(value)


def _refresh_request_scope(
    payload: dict[str, object],
) -> tuple[str | None, str | None, bool | None, bool, str, str] | None:
    raw_target = str(payload.get("target") or "").strip().lower()
    if raw_target:
        target = REFRESH_TARGET_ALIASES.get(raw_target, raw_target)
        if target not in REFRESHABLE_TARGETS:
            return ("", "", False, False, "", "Unsupported refresh target.")
        return (target, None, None, False, "live poll recommendations", "")

    raw_sport = str(payload.get("sport") or payload.get("sports") or "").strip().lower()
    if not raw_sport:
        return None
    if raw_sport in REFRESH_TARGET_ALIASES:
        return (REFRESH_TARGET_ALIASES[raw_sport], None, None, False, "live poll recommendations", "")
    sports = _normalize_sports_arg(raw_sport)
    requested = [sport for sport in sports.split(",") if sport]
    only_predictions = (
        str(payload.get("category") or "").strip().lower() == "predictions"
        or str(payload.get("mode") or "").strip().lower() in {"prediction", "predictions"}
        or str(payload.get("refresh_mode") or "").strip().lower() in {"prediction", "predictions"}
        or _truthy_payload_flag(payload.get("only_predictions"))
        or _truthy_payload_flag(payload.get("prediction_only"))
    )
    allowed_sports = REFRESHABLE_PREDICTION_SPORTS if only_predictions else REFRESHABLE_VOTE_SPORTS
    if not requested or any(sport not in allowed_sports for sport in requested):
        return ("", "", False, only_predictions, "", "Unsupported refresh sport.")
    label = ", ".join(SPORT_LABELS.get(sport, sport.upper()) for sport in requested)
    label_suffix = "predictions" if only_predictions else "pre-game data"
    return (None, sports, ("soccer" in requested), only_predictions, f"{label} {label_suffix}", "")


def _post_recommendation(payload: dict[str, object]) -> tuple[dict[str, object], int]:
    post_id = str(payload.get("post_id") or payload.get("postId") or "").strip()
    text = str(payload.get("text") or "").strip()
    group_id_raw = str(
        payload.get("group_id")
        or payload.get("groupId")
        or payload.get("section_group_id")
        or payload.get("sectionGroupId")
        or ""
    ).strip()
    group_id: int | str = DEFAULT_REAL_COMMENT_GROUP_ID
    if group_id_raw:
        group_id = int(group_id_raw) if group_id_raw.isdigit() else group_id_raw

    if not post_id or not post_id.isdigit():
        return ({"error": "A numeric Real post_id is required."}, 400)
    if not text:
        return ({"error": "Recommendation text is required."}, 400)
    if len(text) > 1800:
        text = text[:1797].rstrip() + "..."

    request_payload = {
        "post_id": post_id,
        "group_id": group_id,
        "text": text,
    }
    if _truthy_payload_flag(payload.get("dry_run")):
        return ({"posted": False, "dry_run": True, "request": request_payload}, 200)

    try:
        from realsports_api import RealSportsError, build_realsports_client

        response = build_realsports_client().add_post_comment(
            post_id,
            text=text,
            group_id=group_id,
        )
    except RealSportsError as exc:
        return ({"error": str(exc)}, 502)
    except Exception as exc:
        return ({"error": f"Posting failed: {exc}"}, 500)

    comment = response.get("comment") if isinstance(response, dict) else None
    return (
        {
            "posted": True,
            "post_id": post_id,
            "comment": comment if isinstance(comment, dict) else response,
        },
        200,
    )


def _document_row_metadata(path: Path | None) -> list[dict[str, object]]:
    if path is None:
        return []
    metadata_path = path.with_name(f"{path.name}.meta.json")
    if not metadata_path.exists():
        return []
    try:
        payload = json.loads(metadata_path.read_text(encoding="utf8"))
    except Exception:
        return []
    rows = payload.get("rows") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


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
                        "can_refresh": doc["can_refresh"],
                        "refresh_sport": doc["refresh_sport"],
                        "refresh_target": doc["refresh_target"],
                        "refresh_label": doc["refresh_label"],
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
            if parsed.path == "/api/post-recommendation":
                response_payload, status = _post_recommendation(_request_json(self))
                _json_response(self, response_payload, status=status)
                return
            if parsed.path != "/api/refresh":
                _json_response(self, {"error": "not found"}, status=404)
                return
            payload = _request_json(self)
            scope = _refresh_request_scope(payload)
            if scope is None:
                started = context.trigger_refresh(label="configured sports")
            else:
                target, sports, refresh_soccer, only_predictions, label, error = scope
                if error:
                    _json_response(self, {"error": error}, status=400)
                    return
                started = context.trigger_refresh(
                    target=target,
                    sports=sports,
                    refresh_soccer=refresh_soccer,
                    only_predictions=only_predictions,
                    label=label,
                )
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
