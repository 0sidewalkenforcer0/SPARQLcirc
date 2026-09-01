#!/usr/bin/env python3
"""Summarize immutable Wikidata-141 method cells with measured-run medians."""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
from pathlib import Path
import statistics
import tempfile
from typing import Any, Iterable, Mapping, Optional, Sequence


WORKLOAD_SCHEMA = "wikidata-141-workload-v1"
CELL_SCHEMA = "wikidata-method-cell-v1"
METHODS = ("N-per-answer", "N-shared", "C-flat", "C-factorised", "C-path")
CSV_COLUMNS = (
    "query_id",
    "category",
    "method",
    "status",
    "measured_successes",
    "median_endpoint_e2e_ms",
    "median_component_e2e_ms",
    "requested_mode",
    "effective_mode",
    "fallback_reason",
)


class SummaryError(RuntimeError):
    """The selected artifacts do not form one unambiguous formal summary."""


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SummaryError("cannot read %s: %s" % (path, error)) from error


def atomic_text(path: Path, value: str) -> None:
    if path.exists():
        raise SummaryError("refusing to overwrite %s" % path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False, newline="\n"
    ) as handle:
        handle.write(value)
        handle.flush()
        os.fsync(handle.fileno())
        temporary = Path(handle.name)
    os.replace(temporary, path)


def number(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    result = float(value)
    return result if math.isfinite(result) else None


def nested(value: Any, *keys: str) -> Any:
    current = value
    for key in keys:
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    return current


def flatten_numbers(value: Any, prefix: str = "") -> Iterable[tuple[str, float]]:
    if isinstance(value, Mapping):
        for key, child in value.items():
            name = str(key) if not prefix else prefix + "." + str(key)
            yield from flatten_numbers(child, name)
    elif not isinstance(value, list):
        parsed = number(value)
        if parsed is not None:
            yield prefix, parsed


def median(values: Sequence[float]) -> Optional[float]:
    return round(float(statistics.median(values)), 6) if values else None


def protocol(cell: Mapping[str, Any]) -> tuple[int, int]:
    value = cell.get("protocol")
    if not isinstance(value, Mapping):
        raise SummaryError("cell has no protocol")
    try:
        warmups = int(value["warmups"])
        measured = int(value["measured_runs"])
    except (KeyError, TypeError, ValueError) as error:
        raise SummaryError("cell has an invalid execution contract") from error
    if warmups != 1 or measured != 5 or value.get("primary_statistic") != "median":
        raise SummaryError("cell does not use the formal 1+5 median protocol")
    return warmups, measured


def method_view(cell: Mapping[str, Any], method: str) -> tuple[str, list[Mapping[str, Any]]]:
    protocol(cell)
    if cell.get("physical_method") == "N-paired":
        methods = cell.get("methods")
        if method not in ("N-per-answer", "N-shared") or not isinstance(methods, Mapping):
            raise SummaryError("invalid N-paired logical method")
        view = methods.get(method)
        if not isinstance(view, Mapping):
            raise SummaryError("N-paired cell is missing %s" % method)
        runs = [run for run in view.get("runs", []) if isinstance(run, Mapping)]
        return str(view.get("status") or "incomplete"), runs
    if cell.get("physical_method") != method:
        raise SummaryError("physical and logical method differ")
    runs = [run for run in cell.get("runs", []) if isinstance(run, Mapping)]
    status = "ok" if cell.get("status") == "ok" else "incomplete"
    return status, runs


def summarize_method(cell: Mapping[str, Any], method: str) -> dict[str, Any]:
    cell_status, runs = method_view(cell, method)
    measured = [run for run in runs if run.get("phase") == "measured"]
    successful = [run for run in measured if run.get("status") == "ok"]
    complete = cell_status == "ok" and len(measured) == 5 and len(successful) == 5
    endpoint_values = [
        value
        for value in (number(run.get("endpoint_observed_wall_ms")) for run in successful)
        if value is not None
    ]
    component_values = [
        value
        for value in (number(run.get("component_e2e_ms")) for run in successful)
        if value is not None
    ]
    run_metrics: dict[str, list[float]] = {}
    for run in successful:
        for key, value in flatten_numbers(run):
            run_metrics.setdefault(key, []).append(value)
    metrics = {
        key: median(values)
        for key, values in sorted(run_metrics.items())
        if len(values) == 5
    }
    requested = [run.get("requested_mode") for run in successful]
    effective = [run.get("effective_mode") for run in successful]
    fallbacks = [run.get("fallback_reason") for run in successful]
    return {
        "status": "ok" if complete else "incomplete",
        "measured_successes": len(successful),
        "median_endpoint_e2e_ms": (
            median(endpoint_values) if len(endpoint_values) == 5 else None
        ),
        "median_component_e2e_ms": (
            median(component_values) if len(component_values) == 5 else None
        ),
        "requested_mode": requested[0] if requested and len(set(requested)) == 1 else None,
        "effective_mode": effective[0] if effective and len(set(effective)) == 1 else None,
        "fallback_reason": fallbacks[0] if fallbacks and len(set(fallbacks)) == 1 else None,
        "metric_medians": metrics,
    }


def collect_cells(roots: Sequence[Path]) -> dict[tuple[str, str], Mapping[str, Any]]:
    cells: dict[tuple[str, str], Mapping[str, Any]] = {}
    for root in roots:
        for path in root.rglob("cell.json"):
            value = read_json(path)
            if not isinstance(value, Mapping) or value.get("schema") != CELL_SCHEMA:
                continue
            query_id = str(value.get("query_id"))
            physical = str(value.get("physical_method"))
            key = (query_id, physical)
            if key in cells:
                raise SummaryError("duplicate physical cell %s/%s" % key)
            cells[key] = value
    return cells


def summarize(manifest_path: Path, roots: Sequence[Path]) -> dict[str, Any]:
    manifest = read_json(manifest_path)
    if not isinstance(manifest, Mapping) or manifest.get("schema") != WORKLOAD_SCHEMA:
        raise SummaryError("unsupported workload manifest")
    cells = collect_cells(roots)
    rows = []
    for entry in manifest["entries"]:
        query_id = str(entry["query_id"])
        for method in entry["applicable_methods"]:
            physical = "N-paired" if str(method).startswith("N-") else str(method)
            cell = cells.get((query_id, physical))
            if cell is None:
                summary = {
                    "status": "missing",
                    "measured_successes": 0,
                    "median_endpoint_e2e_ms": None,
                    "median_component_e2e_ms": None,
                    "requested_mode": None,
                    "effective_mode": None,
                    "fallback_reason": None,
                    "metric_medians": {},
                }
            else:
                summary = summarize_method(cell, str(method))
            rows.append({
                "query_id": query_id,
                "category": entry["category"],
                "method": method,
                **summary,
            })
    expected = sum(len(entry["applicable_methods"]) for entry in manifest["entries"])
    if len(rows) != expected:
        raise SummaryError("summary row count differs from workload contract")
    return {
        "schema": "wikidata-141-summary-v1",
        "workload_manifest": str(manifest_path.resolve()),
        "cell_roots": [str(root.resolve()) for root in roots],
        "protocol": {"warmups": 1, "measured_runs": 5, "primary_statistic": "median"},
        "rows": rows,
    }


def write(result: Mapping[str, Any], json_path: Path, csv_path: Path) -> None:
    atomic_text(
        json_path,
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    if csv_path.exists():
        raise SummaryError("refusing to overwrite %s" % csv_path)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=csv_path.parent, delete=False, newline=""
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for row in result["rows"]:
            writer.writerow({column: row.get(column) for column in CSV_COLUMNS})
        handle.flush()
        os.fsync(handle.fileno())
        temporary = Path(handle.name)
    os.replace(temporary, csv_path)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--cells", required=True, action="append", type=Path)
    parser.add_argument("--json", required=True, type=Path)
    parser.add_argument("--csv", required=True, type=Path)
    args = parser.parse_args(argv)
    result = summarize(args.manifest.resolve(), [path.resolve() for path in args.cells])
    write(result, args.json.resolve(), args.csv.resolve())
    print(json.dumps({"status": "ok", "rows": len(result["rows"])}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
