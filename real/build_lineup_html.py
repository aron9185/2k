from __future__ import annotations

import argparse
import csv
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT = BASE_DIR / "lineup.html"
DEFAULT_INPUT_DIR = BASE_DIR / "lineups"
DEFAULT_SPORTS = "mlb,nba,nhl,wnba,fc"
DEFAULT_SEASON = "2025"

SPORT_ALIASES = {
    "mlb": ("mlb", "MLB", "mlb"),
    "nba": ("nba", "NBA", "nba"),
    "nhl": ("nhl", "NHL", "nhl"),
    "wnba": ("wnba", "WNBA", "wnba"),
    "fc": ("soccer", "FC", "fc"),
    "soccer": ("soccer", "FC", "fc"),
}

TABLE_COLUMNS = [
    ("Lineup_Rank", "Rank"),
    ("Name", "Name"),
    ("Adjusted_FP", "Adj FP"),
    ("Multiplier_Factor", "Mult"),
    ("Base_FP", "Base FP"),
    ("Real_Rating", "Real"),
    ("Team", "Team"),
    ("Opponent", "Opp"),
    ("Position", "Pos"),
    ("Salary", "Salary"),
    ("Multiplier_Status", "Status"),
    ("Rotowire_Site", "Site"),
    ("Source_Contest_Type", "Contest"),
    ("Source_Slate_Start_Date", "Slate Start"),
]


@dataclass(frozen=True)
class SportConfig:
    requested: str
    lineup_sport: str
    label: str
    file_key: str


@dataclass
class SportSheet:
    config: SportConfig
    csv_path: Path
    rows: list[dict[str, str]]
    error: str = ""

    @property
    def updated_at(self) -> str:
        if not self.csv_path.exists():
            return ""
        return datetime.fromtimestamp(self.csv_path.stat().st_mtime).strftime("%Y-%m-%d %H:%M")

    @property
    def top_row(self) -> dict[str, str]:
        return self.rows[0] if self.rows else {}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a single HTML lineup sheet from MLB, NBA, NHL, WNBA, and FC Real Sports lineup CSVs."
    )
    parser.add_argument(
        "--sports",
        default=DEFAULT_SPORTS,
        help="Comma-separated sports. Use fc or soccer for Real Sports soccer.",
    )
    parser.add_argument("--date", default="", help="Optional refresh date in YYYY-MM-DD format.")
    parser.add_argument(
        "--sport-dates",
        default="",
        help="Optional comma-separated per-sport refresh dates, e.g. mlb=2026-05-01,nba=2026-05-01,nhl=2026-05-02,wnba=2026-05-02,fc=2026-05-02.",
    )
    parser.add_argument("--season", default=DEFAULT_SEASON, help="Real Sports season key for refresh mode.")
    parser.add_argument("--input-dir", default=str(DEFAULT_INPUT_DIR), help="Directory for sport lineup CSVs.")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="HTML output path.")
    parser.add_argument(
        "--max-rows-per-sport",
        type=int,
        default=80,
        help="Maximum rows to render per sport. Use 0 for all rows.",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Run lineup.py for each requested sport before building the HTML.",
    )
    parser.add_argument(
        "--skip-real-id-refresh",
        action="store_true",
        help="Pass through to lineup.py when --refresh is used.",
    )
    parser.add_argument(
        "--skip-multiplier",
        action="store_true",
        help="Pass through to lineup.py when --refresh is used.",
    )
    parser.add_argument(
        "--stop-on-refresh-error",
        action="store_true",
        help="Stop immediately if one sport refresh fails.",
    )
    return parser.parse_args()


def parse_sports(value: str) -> list[SportConfig]:
    configs: list[SportConfig] = []
    seen: set[str] = set()
    for raw_part in value.replace(" ", ",").split(","):
        key = raw_part.strip().lower()
        if not key:
            continue
        if key not in SPORT_ALIASES:
            raise SystemExit(f"Unsupported sport for HTML sheet: {raw_part}")
        lineup_sport, label, file_key = SPORT_ALIASES[key]
        if file_key in seen:
            continue
        seen.add(file_key)
        configs.append(SportConfig(key, lineup_sport, label, file_key))
    if not configs:
        raise SystemExit("No sports were requested.")
    return configs


def parse_sport_dates(value: str) -> dict[str, str]:
    dates: dict[str, str] = {}
    for raw_part in str(value or "").split(","):
        part = raw_part.strip()
        if not part:
            continue
        if "=" not in part:
            raise SystemExit(f"Invalid --sport-dates entry: {part}")
        raw_key, raw_date = part.split("=", 1)
        key = raw_key.strip().lower()
        date_value = raw_date.strip()
        if key not in SPORT_ALIASES:
            raise SystemExit(f"Unsupported sport in --sport-dates: {raw_key}")
        _, _, file_key = SPORT_ALIASES[key]
        dates[file_key] = date_value
    return dates


def numeric_value(row: dict[str, str], column: str, default: float = 0.0) -> float:
    try:
        return float(str(row.get(column, "")).replace(",", ""))
    except Exception:
        return default


def read_lineup_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    return sorted(
        rows,
        key=lambda row: (
            numeric_value(row, "Lineup_Rank", 999999.0),
            -numeric_value(row, "Adjusted_FP"),
            row.get("Name", ""),
        ),
    )


def refresh_lineup(config: SportConfig, csv_path: Path, date: str, season: str, args: argparse.Namespace) -> str:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    fantasy_path = csv_path.with_name(f"{config.file_key}_fantasy_points.json")
    command = [
        sys.executable,
        str(BASE_DIR / "lineup.py"),
        "--sport",
        config.lineup_sport,
        "--season",
        season,
        "--output",
        str(csv_path),
        "--fantasy-points-file",
        str(fantasy_path),
    ]
    if date:
        command.extend(["--date", date])
    if args.skip_real_id_refresh:
        command.append("--skip-real-id-refresh")
    if args.skip_multiplier:
        command.append("--skip-multiplier")

    result = subprocess.run(
        command,
        cwd=str(BASE_DIR.parent),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if result.returncode != 0:
        return result.stdout.strip() or f"lineup.py exited with code {result.returncode}"
    return ""


def load_sheets(
    configs: list[SportConfig],
    input_dir: Path,
    args: argparse.Namespace,
    sport_dates: dict[str, str],
) -> list[SportSheet]:
    sheets: list[SportSheet] = []
    for config in configs:
        csv_path = input_dir / f"{config.file_key}_lineup.csv"
        error = ""
        if args.refresh:
            refresh_date = sport_dates.get(config.file_key, args.date)
            error = refresh_lineup(config, csv_path, refresh_date, args.season, args)
            if error and args.stop_on_refresh_error:
                raise SystemExit(f"{config.label} refresh failed:\n{error}")
        rows = read_lineup_csv(csv_path)
        sheets.append(SportSheet(config=config, csv_path=csv_path, rows=rows, error=error))
    return sheets


def fmt_number(value: str, decimals: int = 2) -> str:
    if value in (None, ""):
        return ""
    try:
        number = float(str(value).replace(",", ""))
    except Exception:
        return str(value)
    if decimals <= 0:
        return f"{number:,.0f}"
    return f"{number:,.{decimals}f}"


def cell_value(row: dict[str, str], column: str) -> str:
    if column in {"Adjusted_FP", "Base_FP", "Real_Rating"}:
        return fmt_number(row.get(column, ""), 2)
    if column == "Multiplier_Factor":
        return fmt_number(row.get(column, ""), 3)
    if column == "Salary":
        return fmt_number(row.get(column, ""), 0)
    return row.get(column, "")


def render_status(sheet: SportSheet) -> str:
    if sheet.error:
        return f"<span class=\"status bad\">Refresh failed</span>"
    if sheet.rows:
        return f"<span class=\"status good\">{len(sheet.rows)} rows</span>"
    return "<span class=\"status muted\">No data</span>"


def render_summary_card(sheet: SportSheet) -> str:
    top = sheet.top_row
    top_name = top.get("Name", "No rows")
    top_fp = fmt_number(top.get("Adjusted_FP", ""), 2) if top else ""
    site = top.get("Rotowire_Site", "") if top else ""
    coverage = top.get("Rotowire_Coverage_Games", "") if top else ""
    return f"""
      <article class="summary-card" data-sport="{escape(sheet.config.label)}">
        <div class="summary-topline">
          <h2>{escape(sheet.config.label)}</h2>
          {render_status(sheet)}
        </div>
        <p class="hero-name">{escape(top_name)}</p>
        <dl>
          <div><dt>Adjusted FP</dt><dd>{escape(top_fp or "-")}</dd></div>
          <div><dt>Site</dt><dd>{escape(site or "-")}</dd></div>
          <div><dt>Games</dt><dd>{escape(str(coverage or "-"))}</dd></div>
          <div><dt>Updated</dt><dd>{escape(sheet.updated_at or "-")}</dd></div>
        </dl>
      </article>
    """


def link_href(path: Path, output_path: Path) -> str:
    try:
        return path.resolve().relative_to(output_path.resolve().parent).as_posix()
    except Exception:
        return path.as_posix()


def render_table(sheet: SportSheet, max_rows: int, output_path: Path) -> str:
    rows = sheet.rows if max_rows <= 0 else sheet.rows[:max_rows]
    header = "".join(f"<th>{escape(label)}</th>" for _, label in TABLE_COLUMNS)
    body_rows: list[str] = []
    for row in rows:
        cells = []
        for column, _ in TABLE_COLUMNS:
            value = cell_value(row, column)
            css = "num" if column in {"Lineup_Rank", "Adjusted_FP", "Multiplier_Factor", "Base_FP", "Real_Rating", "Salary"} else ""
            cells.append(f"<td class=\"{css}\">{escape(value)}</td>")
        body_rows.append(f"<tr>{''.join(cells)}</tr>")

    if not body_rows:
        message = sheet.error or f"No CSV found at {sheet.csv_path}"
        body_rows.append(
            f"<tr><td colspan=\"{len(TABLE_COLUMNS)}\" class=\"empty\">{escape(message)}</td></tr>"
        )

    error_html = ""
    if sheet.error:
        error_html = f"<pre class=\"refresh-error\">{escape(sheet.error)}</pre>"

    return f"""
      <section class="sport-section" id="{escape(sheet.config.file_key)}">
        <div class="section-heading">
          <div>
            <p class="eyebrow">Sport sheet</p>
            <h2>{escape(sheet.config.label)}</h2>
          </div>
          <a href="{escape(link_href(sheet.csv_path, output_path))}">CSV</a>
        </div>
        {error_html}
        <div class="table-shell">
          <table>
            <thead><tr>{header}</tr></thead>
            <tbody>{''.join(body_rows)}</tbody>
          </table>
        </div>
      </section>
    """


def render_html(sheets: list[SportSheet], output_path: Path, input_dir: Path, max_rows: int) -> str:
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    nav = "".join(
        f"<a href=\"#{escape(sheet.config.file_key)}\">{escape(sheet.config.label)}</a>"
        for sheet in sheets
    )
    cards = "".join(render_summary_card(sheet) for sheet in sheets)
    tables = "".join(render_table(sheet, max_rows, output_path) for sheet in sheets)
    total_rows = sum(len(sheet.rows) for sheet in sheets)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Real Sports Lineup Sheet</title>
  <style>
    :root {{
      --ink: #14213d;
      --muted: #64748b;
      --paper: #fffdf7;
      --card: rgba(255, 255, 255, 0.82);
      --line: rgba(20, 33, 61, 0.14);
      --accent: #e76f51;
      --accent-2: #2a9d8f;
      --shadow: 0 24px 70px rgba(20, 33, 61, 0.14);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      color: var(--ink);
      font-family: "Aptos", "Segoe UI", sans-serif;
      background: linear-gradient(145deg, #fff7ed 0%, #f8fafc 52%, #eefcf8 100%);
      min-height: 100vh;
    }}
    header {{
      padding: 42px min(5vw, 72px) 22px;
    }}
    .kicker {{
      color: var(--accent);
      font-weight: 800;
      letter-spacing: 0;
      text-transform: uppercase;
      font-size: 0.78rem;
    }}
    h1 {{
      margin: 10px 0 10px;
      font-size: clamp(2.4rem, 6vw, 5.6rem);
      line-height: 0.92;
      letter-spacing: 0;
    }}
    .lede {{
      max-width: 760px;
      color: var(--muted);
      font-size: 1.04rem;
      line-height: 1.6;
    }}
    nav {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      margin-top: 24px;
    }}
    nav a, .section-heading a {{
      color: var(--ink);
      text-decoration: none;
      border: 1px solid var(--line);
      background: rgba(255, 255, 255, 0.68);
      padding: 9px 13px;
      border-radius: 999px;
      font-weight: 700;
    }}
    main {{
      padding: 0 min(5vw, 72px) 54px;
    }}
    .summary-grid {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 16px;
      margin: 16px 0 28px;
    }}
    .summary-card, .sport-section {{
      background: var(--card);
      border: 1px solid var(--line);
      box-shadow: var(--shadow);
      backdrop-filter: blur(16px);
    }}
    .summary-card {{
      border-radius: 8px;
      padding: 20px;
    }}
    .summary-topline, .section-heading {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 14px;
    }}
    .summary-card h2, .section-heading h2 {{
      margin: 0;
      letter-spacing: 0;
    }}
    .hero-name {{
      margin: 18px 0 18px;
      min-height: 2.6em;
      font-size: 1.26rem;
      font-weight: 850;
      line-height: 1.15;
    }}
    dl {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 12px;
      margin: 0;
    }}
    dt {{
      color: var(--muted);
      font-size: 0.72rem;
      text-transform: uppercase;
      letter-spacing: 0;
    }}
    dd {{
      margin: 3px 0 0;
      font-weight: 800;
    }}
    .status {{
      border-radius: 999px;
      padding: 6px 10px;
      font-size: 0.75rem;
      font-weight: 800;
    }}
    .status.good {{ background: rgba(42, 157, 143, 0.16); color: #0f766e; }}
    .status.bad {{ background: rgba(220, 38, 38, 0.13); color: #b91c1c; }}
    .status.muted {{ background: rgba(100, 116, 139, 0.12); color: var(--muted); }}
    .sport-section {{
      border-radius: 8px;
      margin: 18px 0;
      overflow: hidden;
    }}
    .section-heading {{
      padding: 20px 22px;
      border-bottom: 1px solid var(--line);
    }}
    .eyebrow {{
      margin: 0 0 4px;
      color: var(--accent-2);
      font-size: 0.72rem;
      font-weight: 900;
      letter-spacing: 0;
      text-transform: uppercase;
    }}
    .table-shell {{
      overflow-x: auto;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      min-width: 1160px;
    }}
    th, td {{
      padding: 11px 12px;
      border-bottom: 1px solid rgba(20, 33, 61, 0.09);
      text-align: left;
      white-space: nowrap;
    }}
    th {{
      position: sticky;
      top: 0;
      background: #fffaf0;
      color: var(--muted);
      font-size: 0.72rem;
      letter-spacing: 0;
      text-transform: uppercase;
      z-index: 1;
    }}
    tbody tr:nth-child(even) {{
      background: rgba(255, 255, 255, 0.42);
    }}
    .num {{
      text-align: right;
      font-variant-numeric: tabular-nums;
    }}
    .empty {{
      color: var(--muted);
      text-align: center;
      padding: 32px;
    }}
    .refresh-error {{
      margin: 0;
      padding: 16px 22px;
      overflow-x: auto;
      background: rgba(220, 38, 38, 0.08);
      color: #991b1b;
      border-bottom: 1px solid rgba(220, 38, 38, 0.18);
      white-space: pre-wrap;
    }}
    footer {{
      color: var(--muted);
      padding: 4px min(5vw, 72px) 32px;
      font-size: 0.9rem;
    }}
    @media (max-width: 980px) {{
      .summary-grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
    }}
    @media (max-width: 620px) {{
      header {{ padding-top: 28px; }}
      .summary-grid {{ grid-template-columns: 1fr; }}
      .summary-topline {{ align-items: flex-start; }}
      dl {{ grid-template-columns: 1fr 1fr; }}
    }}
  </style>
</head>
<body>
  <header>
    <div class="kicker">Real Sports Lineup Sheet</div>
    <h1>Multi-sport edge board</h1>
    <p class="lede">Generated {escape(generated_at)} from sport-specific lineup CSVs. Includes MLB, NBA, NHL, WNBA, and FC/soccer when their CSVs are present or refreshed.</p>
    <nav>{nav}</nav>
  </header>
  <main>
    <section class="summary-grid" aria-label="Sport summaries">{cards}</section>
    {tables}
  </main>
  <footer>
    Rows rendered: {total_rows}. Source directory: {escape(str(input_dir))}.
  </footer>
</body>
</html>
"""


def write_html(path: Path, html_text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html_text, encoding="utf8")


def main() -> None:
    args = parse_args()
    configs = parse_sports(args.sports)
    sport_dates = parse_sport_dates(args.sport_dates)
    input_dir = Path(args.input_dir)
    output_path = Path(args.output)
    sheets = load_sheets(configs, input_dir, args, sport_dates)
    html_text = render_html(sheets, output_path, input_dir, args.max_rows_per_sport)
    write_html(output_path, html_text)
    for sheet in sheets:
        status = f"{len(sheet.rows)} rows" if sheet.rows else "no rows"
        if sheet.error:
            status += " (refresh failed)"
        print(f"{sheet.config.label}: {status} from {sheet.csv_path}")
    print(f"Saved HTML lineup sheet to {output_path}")


if __name__ == "__main__":
    main()
