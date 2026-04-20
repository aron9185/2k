from __future__ import annotations

import argparse
import csv
import difflib
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Tuple

from pull_nba_stats import HISTORY_DIR, MANUAL_DIR
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait


APP_URL = "https://bball-index.shinyapps.io/SoulYOLO3Lose/"
CATALOG_SOURCE_IDS = ("MetricYOY", "Metric_rise")
MAIN_METRIC_INPUT_ID = "Metric"
MAIN_METRIC_CATALOG_PROBE = "LEBRON"
BASE_COLUMN_COUNT = 9


@dataclass(frozen=True)
class MetricCatalogEntry:
    name: str
    raw_value: str


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return slug or "metrics"


def clean_metric_name(value: str) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    return re.sub(r"\s*\*$", "", text).strip()


def normalize_metric_name(value: str) -> str:
    return clean_metric_name(value).casefold()


def launch_driver() -> webdriver.Chrome:
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1800,1400")
    return webdriver.Chrome(options=options)


def wait_for_app(driver: webdriver.Chrome, timeout: int) -> None:
    wait = WebDriverWait(driver, timeout)
    wait.until(lambda d: d.execute_script("return document.readyState") == "complete")
    wait.until(lambda d: d.find_element(By.ID, "make_table"))
    wait.until(
        lambda d: d.execute_script(
            "return !!(window.Shiny && Shiny.shinyapp && Shiny.shinyapp.isConnected && Shiny.shinyapp.isConnected())"
        )
    )
    wait.until(lambda d: d.execute_script("return !!(document.querySelector('#Metric') && document.querySelector('#Metric').selectize)"))


def fallback_metric_catalog(driver: webdriver.Chrome) -> List[MetricCatalogEntry]:
    raw_catalog: List[str] = driver.execute_script(
        """
        const ids = arguments[0];
        for (const id of ids) {
            const el = document.querySelector('#' + id);
            if (!el || !el.selectize) continue;
            const values = Object.values(el.selectize.options)
              .map(opt => (opt.value || opt.text || '').trim())
              .filter(Boolean);
            if (values.length) {
              return Array.from(new Set(values));
            }
        }
        return [];
        """,
        list(CATALOG_SOURCE_IDS),
    )
    entries: List[MetricCatalogEntry] = []
    seen = set()
    for raw_value in raw_catalog:
        name = clean_metric_name(raw_value)
        key = normalize_metric_name(name)
        if not name or key in seen:
            continue
        seen.add(key)
        entries.append(MetricCatalogEntry(name=name, raw_value=str(raw_value).strip()))
    return entries


def ensure_main_metric_catalog_loaded(driver: webdriver.Chrome, timeout: int) -> None:
    already_loaded = driver.execute_script(
        """
        const el = document.querySelector('#' + arguments[0]);
        return !!(el && el.selectize && Object.keys(el.selectize.options || {}).length > 0);
        """,
        MAIN_METRIC_INPUT_ID,
    )
    if already_loaded:
        return

    wait = WebDriverWait(driver, timeout)
    metric_input = wait.until(
        lambda d: d.find_element(By.CSS_SELECTOR, f"#{MAIN_METRIC_INPUT_ID} + .selectize-control input")
    )
    metric_input.click()
    metric_input.send_keys(MAIN_METRIC_CATALOG_PROBE)
    wait.until(
        lambda d: d.execute_script(
            """
            const el = document.querySelector('#' + arguments[0]);
            return !!(el && el.selectize && Object.keys(el.selectize.options || {}).length > 0);
            """,
            MAIN_METRIC_INPUT_ID,
        )
    )
    driver.execute_script(
        """
        const el = document.querySelector('#' + arguments[0]);
        if (!el || !el.selectize) return;
        const selectize = el.selectize;
        selectize.setTextboxValue('');
        selectize.refreshOptions(false);
        selectize.close();
        """,
        MAIN_METRIC_INPUT_ID,
    )


def get_metric_catalog(driver: webdriver.Chrome, timeout: int) -> List[MetricCatalogEntry]:
    ensure_main_metric_catalog_loaded(driver, timeout=timeout)

    raw_entries = driver.execute_script(
        """
        const el = document.querySelector('#' + arguments[0]);
        if (!el || !el.selectize) return [];
        return Object.values(el.selectize.options || {}).map(option => ({
          raw_value: (option.value || option.text || '').trim(),
          label: (option.label || option.text || option.value || '').trim(),
        }));
        """,
        MAIN_METRIC_INPUT_ID,
    )

    entries: List[MetricCatalogEntry] = []
    seen = set()
    for entry in raw_entries:
        raw_value = str(entry.get("raw_value", "")).strip()
        name = clean_metric_name(entry.get("label", "") or raw_value)
        key = normalize_metric_name(name)
        if not raw_value or not name or key in seen:
            continue
        seen.add(key)
        entries.append(MetricCatalogEntry(name=name, raw_value=raw_value))

    if entries:
        return entries

    fallback = fallback_metric_catalog(driver)
    if fallback:
        return fallback

    raise SystemExit("Could not load the bball-index metric catalog from the live page.")


def write_metric_catalog(path: Path, catalog: Sequence[MetricCatalogEntry]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["Metric", "RawValue"])
        writer.writeheader()
        for metric in catalog:
            writer.writerow({"Metric": metric.name, "RawValue": metric.raw_value})


def resolve_metrics(
    requested: Sequence[str],
    catalog: Sequence[MetricCatalogEntry],
) -> List[MetricCatalogEntry]:
    by_name = {normalize_metric_name(metric.name): metric for metric in catalog}
    catalog_names = [metric.name for metric in catalog]
    resolved: List[MetricCatalogEntry] = []

    for raw_metric in requested:
        lookup = normalize_metric_name(raw_metric)
        if lookup in by_name:
            resolved.append(by_name[lookup])
            continue

        suggestions = difflib.get_close_matches(
            clean_metric_name(raw_metric),
            catalog_names,
            n=5,
            cutoff=0.5,
        )
        suggestion_text = ""
        if suggestions:
            suggestion_text = " Close matches: " + ", ".join(suggestions)
        raise SystemExit(f"Metric not found in bball-index catalog: {raw_metric}.{suggestion_text}")

    return resolved


def set_select_input(driver: webdriver.Chrome, input_id: str, value: str) -> None:
    driver.execute_script(
        """
        const inputId = arguments[0];
        const value = arguments[1];
        const el = document.querySelector('#' + inputId);
        if (!el) return;
        if (el.selectize) {
          el.selectize.setValue(value, true);
        } else {
          el.value = value;
          el.dispatchEvent(new Event('change', {bubbles: true}));
        }
        if (window.Shiny && Shiny.setInputValue) {
          Shiny.setInputValue(inputId, value, {priority: 'event'});
        }
        """,
        input_id,
        value,
    )


def set_range_input(driver: webdriver.Chrome, input_id: str, value_from: int, value_to: int) -> None:
    driver.execute_script(
        """
        const inputId = arguments[0];
        const valueFrom = arguments[1];
        const valueTo = arguments[2];
        if (window.jQuery) {
          const slider = jQuery('#' + inputId).data('ionRangeSlider');
          if (slider) {
            slider.update({from: valueFrom, to: valueTo});
          }
        }
        if (window.Shiny && Shiny.setInputValue) {
          Shiny.setInputValue(inputId, [valueFrom, valueTo], {priority: 'event'});
        }
        """,
        input_id,
        value_from,
        value_to,
    )


def set_metrics(driver: webdriver.Chrome, metrics: Sequence[str], timeout: int) -> None:
    ensure_main_metric_catalog_loaded(driver, timeout=timeout)
    driver.execute_script(
        """
        const metrics = arguments[0];
        const el = document.querySelector('#Metric');
        if (el && el.selectize) {
          el.selectize.clear(true);
          el.selectize.setValue(metrics, true);
          el.selectize.setTextboxValue('');
          el.selectize.refreshOptions(false);
          el.selectize.close();
        }
        if (window.Shiny && Shiny.setInputValue) {
          Shiny.setInputValue('Metric', metrics, {priority: 'event'});
        }
        """,
        list(metrics),
    )


def current_table_draw(driver: webdriver.Chrome) -> int:
    draw_value = driver.execute_script(
        """
        const table = document.querySelector('#DataTables_Table_0');
        if (!table || !window.jQuery || !jQuery.fn || !jQuery.fn.dataTable) return -1;
        try {
          const dt = jQuery(table).DataTable();
          const settings = dt.settings()[0];
          return settings ? settings.iDraw : -1;
        } catch (err) {
          return -1;
        }
        """
    )
    return int(draw_value)


def wait_for_query_ready(
    driver: webdriver.Chrome,
    previous_draw: int,
    timeout: int,
) -> None:
    wait = WebDriverWait(driver, timeout)
    wait.until(
        lambda d: d.execute_script(
            """
            const table = document.querySelector('#DataTables_Table_0');
            if (!table || !window.jQuery || !jQuery.fn || !jQuery.fn.dataTable) return false;
            try {
              const dt = jQuery(table).DataTable();
              const settings = dt.settings()[0];
              const info = dt.page.info();
              const processing = document.querySelector('#DataTables_Table_0_processing');
              const processingVisible = !!(
                processing &&
                processing.offsetParent !== null &&
                getComputedStyle(processing).display !== 'none'
              );
              return settings && settings.iDraw > arguments[0] && !processingVisible && info.recordsDisplay > 0;
            } catch (err) {
              return false;
            }
            """,
            previous_draw,
        )
    )


def run_query(
    driver: webdriver.Chrome,
    metrics: Sequence[str],
    display: str,
    years: Tuple[int, int] | None,
    minutes: Tuple[int, int] | None,
    timeout: int,
) -> None:
    if display:
        set_select_input(driver, "Display", display)
    if years is not None:
        set_range_input(driver, "Years", years[0], years[1])
    if minutes is not None:
        set_range_input(driver, "Minutes", minutes[0], minutes[1])

    set_metrics(driver, metrics, timeout=timeout)
    previous_draw = current_table_draw(driver)
    driver.find_element(By.ID, "make_table").click()
    wait_for_query_ready(driver, previous_draw=previous_draw, timeout=timeout)


def expand_table_to_all(driver: webdriver.Chrome, timeout: int) -> None:
    set_table_page_length(driver, length=-1, timeout=timeout)


def set_table_page_length(driver: webdriver.Chrome, length: int, timeout: int) -> None:
    previous_draw = current_table_draw(driver)
    driver.execute_script(
        """
        const dt = jQuery('#DataTables_Table_0').DataTable();
        dt.page.len(arguments[0]).draw('page');
        """,
        length,
    )
    wait = WebDriverWait(driver, timeout)
    wait.until(
        lambda d: d.execute_script(
            """
            const dt = jQuery('#DataTables_Table_0').DataTable();
            const settings = dt.settings()[0];
            const info = dt.page.info();
            return settings && settings.iDraw > arguments[1] && info.length === arguments[0] && dt.rows().count() === info.recordsDisplay;
            """,
            length,
            previous_draw,
        )
    )


def extract_table(driver: webdriver.Chrome) -> Tuple[List[str], List[List[object]]]:
    raw = driver.execute_script(
        """
        const dt = jQuery('#DataTables_Table_0').DataTable();
        return {
          headers: dt.columns().header().toArray().map(h => (h.textContent || '').trim()),
          rows: dt.rows().data().toArray()
        };
        """
    )
    headers = [clean_metric_name(str(header or "").strip()) for header in raw["headers"]]
    if headers and headers[0] == "":
        headers[0] = "RecordID"

    rows: List[List[object]] = []
    for row in raw["rows"]:
        rows.append(list(row))
    return headers, rows


def chunked(values: Sequence[str], size: int) -> Iterable[List[str]]:
    for index in range(0, len(values), size):
        yield list(values[index : index + size])


def merge_batches(
    batch_results: Sequence[Tuple[List[str], List[List[object]]]],
) -> Tuple[List[str], List[Dict[str, object]]]:
    if not batch_results:
        return [], []

    base_headers = batch_results[0][0][:BASE_COLUMN_COUNT]
    metric_headers: List[str] = []
    merged_rows: Dict[str, Dict[str, object]] = {}

    for headers, rows in batch_results:
        current_metric_headers = headers[BASE_COLUMN_COUNT:]
        for header in current_metric_headers:
            if header not in metric_headers:
                metric_headers.append(header)

        for row in rows:
            record = {
                headers[index]: row[index] if index < len(row) else ""
                for index in range(len(headers))
            }
            record_id = str(record.get("RecordID", "")).strip()
            if not record_id:
                record_id = "|".join(
                    str(record.get(column, "")).strip()
                    for column in ("Season", "Player", "Team(s)")
                )

            merged = merged_rows.setdefault(record_id, {})
            for header in base_headers:
                merged.setdefault(header, record.get(header, ""))
            for header in current_metric_headers:
                merged[header] = record.get(header, "")

    ordered_headers = base_headers + metric_headers
    ordered_rows = [merged_rows[key] for key in sorted(merged_rows.keys(), key=lambda value: (len(value), value))]
    return ordered_headers, ordered_rows


def write_csv(path: Path, headers: Sequence[str], rows: Sequence[Dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(headers), extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def build_output_path(
    output_arg: str,
    metrics: Sequence[str],
    all_metrics: bool,
) -> Path:
    if output_arg:
        return Path(output_arg)

    if all_metrics:
        filename = "bball_index_all_metrics.csv"
    elif len(metrics) == 1:
        filename = f"bball_index_{slugify(metrics[0])}.csv"
    else:
        filename = f"bball_index_{len(metrics)}_metrics.csv"
    return HISTORY_DIR / filename


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Pull leaderboard data from the live bball-index Shiny app."
    )
    parser.add_argument(
        "--metrics",
        nargs="+",
        default=["Games Played"],
        help="One or more exact metric names from the bball-index leaderboard app. Use 'all' to pull the full catalog in batches.",
    )
    parser.add_argument(
        "--display",
        default="Values",
        help="Leaderboard display mode. Defaults to Values.",
    )
    parser.add_argument(
        "--years-from",
        type=int,
        default=None,
        help="Optional lower season bound for the leaderboard query, e.g. 2014.",
    )
    parser.add_argument(
        "--years-to",
        type=int,
        default=None,
        help="Optional upper season bound for the leaderboard query, e.g. 2026.",
    )
    parser.add_argument(
        "--minutes-min",
        type=int,
        default=None,
        help="Optional lower minutes bound for the leaderboard query.",
    )
    parser.add_argument(
        "--minutes-max",
        type=int,
        default=None,
        help="Optional upper minutes bound for the leaderboard query.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=10,
        help="How many metrics to query at once. The site UI max is 10.",
    )
    parser.add_argument(
        "--output",
        default="",
        help="Optional output CSV path. Defaults to stats/history.",
    )
    parser.add_argument(
        "--catalog-out",
        default=str(MANUAL_DIR / "bball_index_metric_catalog.csv"),
        help="Where to save the discovered live metric catalog.",
    )
    parser.add_argument(
        "--list-metrics",
        action="store_true",
        help="Print the live metric catalog and exit.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=240,
        help="Per-step timeout in seconds for page loads and queries.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    years = None
    if args.years_from is not None or args.years_to is not None:
        if args.years_from is None or args.years_to is None:
            raise SystemExit("Use both --years-from and --years-to together.")
        years = (args.years_from, args.years_to)

    minutes = None
    if args.minutes_min is not None or args.minutes_max is not None:
        if args.minutes_min is None or args.minutes_max is None:
            raise SystemExit("Use both --minutes-min and --minutes-max together.")
        minutes = (args.minutes_min, args.minutes_max)

    if args.batch_size < 1 or args.batch_size > 10:
        raise SystemExit("--batch-size must be between 1 and 10.")

    driver = launch_driver()
    try:
        driver.get(APP_URL)
        wait_for_app(driver, timeout=args.timeout)

        catalog = get_metric_catalog(driver, timeout=args.timeout)
        write_metric_catalog(Path(args.catalog_out), catalog)

        if args.list_metrics:
            for metric in catalog:
                print(metric.name)
            return

        all_metrics = len(args.metrics) == 1 and normalize_metric_name(args.metrics[0]) == "all"
        requested_metrics = catalog if all_metrics else resolve_metrics(args.metrics, catalog)
        requested_metric_names = [metric.name for metric in requested_metrics]
        output_path = build_output_path(
            args.output,
            requested_metric_names,
            all_metrics=all_metrics,
        )

        batch_results: List[Tuple[List[str], List[List[object]]]] = []
        batches = list(chunked(requested_metrics, args.batch_size))

        for index, metric_batch in enumerate(batches, start=1):
            if index > 1:
                driver.get(APP_URL)
                wait_for_app(driver, timeout=args.timeout)
            print(f"[{index}/{len(batches)}] Pulling {', '.join(metric.name for metric in metric_batch)}")
            run_query(
                driver,
                metrics=[metric.raw_value for metric in metric_batch],
                display=args.display,
                years=years,
                minutes=minutes,
                timeout=args.timeout,
            )
            expand_table_to_all(driver, timeout=args.timeout)
            headers, rows = extract_table(driver)
            print(f"[OK] {len(rows)} rows returned for batch {index}")
            batch_results.append((headers, rows))
            time.sleep(1.0)

        headers, merged_rows = merge_batches(batch_results)
        write_csv(output_path, headers, merged_rows)
        print(f"[OUT] {output_path}")
        print(f"[OK] merged {len(merged_rows)} player-season rows")
    finally:
        driver.quit()


if __name__ == "__main__":
    main()
