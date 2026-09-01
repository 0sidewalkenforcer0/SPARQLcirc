#!/usr/bin/env python3
"""Build the normalized Figure 3 table from formal WatDiv 10M cells."""
from __future__ import annotations

import argparse
import csv
import json
import os
from pathlib import Path
import statistics
import sys
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple


PAPER = Path(__file__).resolve().parent
if str(PAPER) not in sys.path:
    sys.path.insert(0, str(PAPER))

import watdiv10m_batch as batch
import watdiv10m_workload as workload


SCHEMA = "watdiv10m-figure3-summary-v1"
CELL_SCHEMA = "watdiv-brnc-cell-v1"
DEFAULT_ENGINES = ("graphdb-10.7.6", "fuseki-5.4.0", "oxigraph-0.5.9")
DISPLAY_METHOD = {
    "B": "B",
    "R": "R",
    "N": "N",
    "C-flat": "C-flat",
    "C-factorised": "C-factored",
    "C-path": "C-path",
}
CSV_COLUMNS = (
    "engine",
    "template",
    "display_method",
    "raw_method",
    "expected_instances",
    "measured_endpoint_ok",
    "endpoint_timeout",
    "successful_median_ms",
    "plotted_successful_median_ms",
    "plot_kind",
)


class SummaryError(RuntimeError):
    """The selected WatDiv cell roots do not form a publishable matrix."""


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SummaryError("cannot read JSON %s: %s" % (path, error)) from error


def _write_json(path: Path, value: Any) -> None:
    if path.exists():
        raise SummaryError("refusing to overwrite %s" % path)
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".partial")
    with partial.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(partial, path)


def _workload_rows(root: Path) -> Dict[str, Mapping[str, str]]:
    workload.audit_workload(root)
    with (root / "query-list.tsv").open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    selected = {
        row["query_id"]: row
        for row in rows
        if row["template"] in batch.FORMAL_TEMPLATES
        and row["instance"] in batch.FORMAL_INSTANCES
    }
    expected = len(batch.FORMAL_TEMPLATES) * len(batch.FORMAL_INSTANCES)
    if len(selected) != expected:
        raise SummaryError(
            "formal workload selection has %d rows, expected %d" % (len(selected), expected)
        )
    return selected


def _validate_protocol(cell: Mapping[str, Any], path: Path) -> None:
    protocol = cell.get("protocol")
    if not isinstance(protocol, Mapping) or (
        protocol.get("warmups"),
        protocol.get("measured_runs"),
        protocol.get("primary_statistic"),
    ) != (1, 5, "median"):
        raise SummaryError("cell does not use the formal 1+5 median protocol: %s" % path)


def _cell_status(cell: Mapping[str, Any]) -> str:
    runs = [
        run for run in cell.get("runs", [])
        if isinstance(run, Mapping) and run.get("phase") == "measured"
    ]
    if cell.get("status") == "ok" and len(runs) == 5 and all(
        run.get("status") == "ok" for run in runs
    ):
        return "ok"
    statuses = [str(cell.get("status", ""))]
    statuses.extend(str(run.get("status", "")) for run in runs)
    failures = cell.get("failures")
    if isinstance(failures, list):
        statuses.extend(str(item.get("status", "")) for item in failures if isinstance(item, Mapping))
    return "timeout" if any("timeout" in value for value in statuses) else "failed"


def _cell_endpoint_median(cell: Mapping[str, Any]) -> float:
    values: List[float] = []
    for run in cell.get("runs", []):
        if not isinstance(run, Mapping) or run.get("phase") != "measured":
            continue
        try:
            values.append(float(run["endpoint"]["endpoint"]["endpoint_e2e_ms"]))
        except (KeyError, TypeError, ValueError) as error:
            raise SummaryError("successful cell has no endpoint E2E timing") from error
    if len(values) != 5:
        raise SummaryError("successful cell does not have five endpoint timings")
    return float(statistics.median(values))


def summarize(
    workload_root: Path,
    cell_roots: Sequence[Path],
    engines: Sequence[str],
) -> Dict[str, Any]:
    rows = _workload_rows(workload_root)
    if len(set(engines)) != len(engines):
        raise SummaryError("engine identifiers must be unique")
    cells: Dict[Tuple[str, str, str], Mapping[str, Any]] = {}
    ignored = 0
    for root in cell_roots:
        for path in root.rglob("cell.json"):
            cell = _read_json(path)
            if not isinstance(cell, Mapping) or cell.get("schema") != CELL_SCHEMA:
                ignored += 1
                continue
            query_id = str(cell.get("query_id"))
            if query_id not in rows:
                ignored += 1
                continue
            if cell.get("workload") != "watdiv" or cell.get("scheme") != "SPARQL_Star":
                raise SummaryError("WatDiv cell has an incompatible workload or scheme: %s" % path)
            _validate_protocol(cell, path)
            key = (query_id, str(cell.get("engine")), str(cell.get("method")))
            if key in cells:
                raise SummaryError("duplicate immutable cell for %s/%s/%s" % key)
            cells[key] = cell

    unexpected_engines = {key[1] for key in cells}.difference(engines)
    if unexpected_engines:
        raise SummaryError("unexpected engine identifiers: %s" % sorted(unexpected_engines))

    output_rows: List[Dict[str, Any]] = []
    diagnostics: List[Dict[str, Any]] = []
    for engine in engines:
        for template in batch.FORMAL_TEMPLATES:
            methods = batch.PATH_METHODS if template in batch.FORMAL_PATH_TEMPLATES else batch.NON_PATH_METHODS
            query_ids = ["%s-%s" % (template, instance) for instance in batch.FORMAL_INSTANCES]
            for method in methods:
                statuses: Dict[str, str] = {}
                medians: List[float] = []
                for query_id in query_ids:
                    cell = cells.get((query_id, engine, method))
                    if cell is None:
                        statuses[query_id] = "missing"
                        continue
                    status = _cell_status(cell)
                    statuses[query_id] = status
                    if status == "ok":
                        medians.append(_cell_endpoint_median(cell))
                invalid = {
                    query_id: status for query_id, status in statuses.items()
                    if status not in ("ok", "timeout")
                }
                if invalid:
                    raise SummaryError(
                        "Figure 3 matrix contains missing/infrastructure-invalid cells for "
                        "%s/%s/%s: %s" % (engine, template, method, invalid)
                    )
                successful = len(medians)
                timed_out = sum(status == "timeout" for status in statuses.values())
                value = float(statistics.median(medians)) if medians else None
                output_rows.append({
                    "engine": engine,
                    "template": template,
                    "display_method": DISPLAY_METHOD[method],
                    "raw_method": method,
                    "expected_instances": len(query_ids),
                    "measured_endpoint_ok": successful,
                    "endpoint_timeout": timed_out,
                    "successful_median_ms": "" if value is None else round(value, 6),
                    "plotted_successful_median_ms": "" if value is None else round(value, 6),
                    "plot_kind": "successful-median-bar" if value is not None else "timeout-bar",
                })
                diagnostics.append({
                    "engine": engine,
                    "template": template,
                    "method": method,
                    "instance_status": statuses,
                    "per_successful_cell_median_ms": medians,
                })

    expected_rows = len(engines) * (
        len(workload.NON_PATH_TEMPLATES) * len(batch.NON_PATH_METHODS)
        + len(batch.FORMAL_PATH_TEMPLATES) * len(batch.PATH_METHODS)
    )
    if len(output_rows) != expected_rows:
        raise SummaryError("normalized matrix row count is not %d" % expected_rows)
    return {
        "schema": SCHEMA,
        "aggregation": "median within each 1+5 cell, then median across instances 00-02",
        "protocol": {"warmups": 1, "measured_runs": 5, "primary_statistic": "median"},
        "workload": str(workload_root.resolve()),
        "cell_roots": [str(path.resolve()) for path in cell_roots],
        "engines": list(engines),
        "ignored_cell_json_files": ignored,
        "normalized_rows": output_rows,
        "diagnostics": diagnostics,
    }


def write_outputs(result: Mapping[str, Any], json_path: Path, csv_path: Path) -> None:
    if csv_path.exists():
        raise SummaryError("refusing to overwrite %s" % csv_path)
    _write_json(json_path, result)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("x", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for row in result["normalized_rows"]:
            writer.writerow({column: row[column] for column in CSV_COLUMNS})


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--workload", required=True, type=Path)
    parser.add_argument("--cells", required=True, action="append", type=Path)
    parser.add_argument("--engine", dest="engines", action="append")
    parser.add_argument("--json", required=True, type=Path)
    parser.add_argument("--csv", required=True, type=Path)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    engines = tuple(args.engines or DEFAULT_ENGINES)
    try:
        result = summarize(args.workload.resolve(), args.cells, engines)
        write_outputs(result, args.json, args.csv)
    except (OSError, ValueError, SummaryError, workload.WorkloadError) as error:
        parser.exit(2, "WatDiv 10M summary: error: %s\n" % error)
    print(json.dumps({
        "schema": result["schema"],
        "rows": len(result["normalized_rows"]),
        "csv": str(args.csv),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
