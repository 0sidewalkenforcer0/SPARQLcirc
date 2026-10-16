#!/usr/bin/env python3
"""Summarize WWW'27 E1 cells: medians, per-template ratios, median of ratios.

Statistic (the paper's protocol): each cell contributes the MEDIAN of its five
measured executions; a template's value is the median of its instances'
cell medians (instances 00-02); ratios are formed per template; the headline
number is the median of the per-template ratios.  The arithmetic mean is not
computed anywhere.

The per-execution time is the protocol's primary field: ``endpoint_only_ms``
for endpoint-only cells (for C the CircuitRun construction window, or
``--c-field c_request_ms_sum`` for the summed CONSTRUCT request times) and
``endpoint_e2e_ms`` for legacy cells.  Cells of different timing protocols or
cache modes are kept in separate groups.

A cell counts as ``ok`` only with five successful measured executions,
``unsupported`` cells (N-orig outside its fragment) are reported as such, and
any other unsuccessful cell is ``timeout`` when a timeout caused it and
``failed`` otherwise.  Ratios use only templates where both methods have a
value.  Answer parity between methods that must return the same answers on
the same query (B, B-mixed and R; N and N-orig) is checked on the first
measured execution and reported as a diagnostic.
"""
from __future__ import annotations

import argparse
import csv
from collections import defaultdict
import json
import os
from pathlib import Path
import statistics
import sys
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


HERE = Path(__file__).resolve().parent
REFERENCE = HERE.parent
PAPER = REFERENCE / "paper"
for _path in (REFERENCE, PAPER):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import watdiv10m_batch as published


SCHEMA = "www27-watdiv-e1-summary-v1"
CELL_SCHEMA = "watdiv-brnc-cell-v1"
DISPLAY = {
    "B": "B",
    "B-mixed": "B-mixed",
    "R": "R",
    "N": "N",
    "N-orig": "N-orig",
    "C-flat": "C-flat",
    "C-factorised": "C-factored",
    "C-path": "C-path",
}
METHOD_ORDER = tuple(DISPLAY)
TEMPLATE_COLUMNS = (
    "engine", "timing_protocol", "cache_mode", "template", "display_method", "raw_method",
    "layout", "time_field", "expected_instances", "measured_endpoint_ok", "endpoint_timeout",
    "unsupported", "failed", "successful_median_ms", "plotted_successful_median_ms",
    "plot_kind", "ratio_vs_B", "ratio_vs_B_mixed", "ratio_vs_R",
)
RATIO_COLUMNS = (
    "engine", "timing_protocol", "cache_mode", "display_method", "raw_method", "layout",
    "reference", "templates_with_ratio", "median_of_template_ratios", "min_template_ratio",
    "max_template_ratio",
)


class SummaryError(RuntimeError):
    """The cell roots do not form an unambiguous summary."""


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SummaryError("cannot read JSON %s: %s" % (path, error)) from error


def _layout(cell: Mapping[str, Any]) -> str:
    if cell.get("method") in ("B", "B-mixed"):
        return "plain"
    protocol = cell.get("protocol") if isinstance(cell.get("protocol"), Mapping) else {}
    if protocol.get("layout"):
        return str(protocol["layout"])
    return "lookup-only" if str(cell.get("scheme", "")).endswith("_Pure") else "asserted-plus-lookup"


def _group(cell: Mapping[str, Any]) -> Tuple[str, str, str]:
    protocol = cell.get("protocol") if isinstance(cell.get("protocol"), Mapping) else {}
    return (
        str(cell.get("engine")),
        str(protocol.get("timing_protocol") or "legacy-e2e"),
        str(protocol.get("cache_mode") or "warm"),
    )


def _measured(cell: Mapping[str, Any]) -> List[Mapping[str, Any]]:
    return [
        run for run in cell.get("runs", [])
        if isinstance(run, Mapping) and run.get("phase") == "measured"
    ]


def cell_status(cell: Mapping[str, Any], runs_expected: int = 5) -> str:
    if cell.get("status") == "unsupported":
        return "unsupported"
    runs = _measured(cell)
    if cell.get("status") == "ok" and len(runs) == runs_expected and all(
        run.get("status") == "ok" for run in runs
    ):
        return "ok"
    texts = [str(cell.get("status", ""))]
    texts.extend(str(run.get("status", "")) for run in runs)
    for item in cell.get("failures") or []:
        if isinstance(item, Mapping):
            texts.append(str(item.get("status", "")))
    return "timeout" if any("timeout" in text for text in texts) else "failed"


def time_field(cell: Mapping[str, Any], c_field: str) -> str:
    timing = _group(cell)[1]
    if timing != "endpoint-only":
        return "endpoint_e2e_ms"
    if str(cell.get("method", "")).startswith("C-"):
        return c_field
    return "endpoint_only_ms"


def cell_median(cell: Mapping[str, Any], field: str, runs_expected: int = 5) -> float:
    values = []
    for run in _measured(cell):
        try:
            values.append(float(run["endpoint"]["endpoint"][field]))
        except (KeyError, TypeError, ValueError) as error:
            raise SummaryError(
                "successful cell %s/%s has no %s timing"
                % (cell.get("query_id"), cell.get("method"), field)
            ) from error
    if len(values) != runs_expected:
        raise SummaryError("successful cell does not have %d timings" % runs_expected)
    return float(statistics.median(values))


def _first_measured_answers(cell_dir: Path, method: str) -> Optional[Path]:
    run = cell_dir / "measured-01"
    if method in ("N", "N-orig"):
        path = run / "pp" / "answer-records.jsonl"
    else:
        path = run / "offline" / "answer-records.jsonl"
    return path if path.is_file() else None


def _answer_set(path: Path) -> List[str]:
    rows = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.dumps(json.loads(line)["binding"], sort_keys=True))
    return sorted(set(rows))


def load_cells(roots: Sequence[Path], query_ids: Iterable[str]) -> Dict[Tuple[Any, ...], Dict[str, Any]]:
    wanted = set(query_ids)
    cells: Dict[Tuple[Any, ...], Dict[str, Any]] = {}
    for root in roots:
        for path in sorted(root.rglob("cell.json")):
            if "offline-resume-" in str(path.parent):
                continue
            cell = _read_json(path)
            if not isinstance(cell, Mapping) or cell.get("schema") != CELL_SCHEMA:
                continue
            if cell.get("workload") != "watdiv" or str(cell.get("query_id")) not in wanted:
                continue
            key = _group(cell) + (str(cell["query_id"]), str(cell["method"]), _layout(cell))
            if key in cells:
                raise SummaryError("duplicate cell for %s (%s and %s)"
                                   % (key, cells[key]["_path"], path.parent))
            entry = dict(cell)
            entry["_path"] = str(path.parent)
            cells[key] = entry
    return cells


def _ratio(numerator: Optional[float], denominator: Optional[float]) -> Optional[float]:
    if numerator is None or denominator is None or denominator <= 0:
        return None
    return numerator / denominator


def summarize(workload_root: Path, roots: Sequence[Path], c_field: str = "endpoint_only_ms",
              templates: Sequence[str] = published.FORMAL_TEMPLATES,
              instances: Sequence[str] = published.FORMAL_INSTANCES) -> Dict[str, Any]:
    rows = published._read_workload_rows(workload_root)
    selected = published._selected_rows(rows, templates, instances)
    by_query = {row["query_id"]: row for row in selected}
    cells = load_cells(roots, by_query)
    if not cells:
        raise SummaryError("no E1 cells found under the given roots")

    per_cell: List[Dict[str, Any]] = []
    grouped: Dict[Tuple[Any, ...], Dict[str, Any]] = {}
    for key, cell in sorted(cells.items()):
        engine, timing, cache, query_id, method, layout = key
        row = by_query[query_id]
        status = cell_status(cell)
        field = time_field(cell, c_field)
        value = cell_median(cell, field) if status == "ok" else None
        per_cell.append({
            "engine": engine, "timing_protocol": timing, "cache_mode": cache,
            "query_id": query_id, "template": row["template"], "instance": row["instance"],
            "method": method, "layout": layout, "status": status, "time_field": field,
            "median_ms": value,
            "unsupported_reason": cell.get("unsupported_reason"),
            "cell": cell["_path"],
        })
        slot = grouped.setdefault(
            (engine, timing, cache, row["template"], method, layout),
            {"statuses": {}, "medians": [], "field": field},
        )
        slot["statuses"][query_id] = status
        if value is not None:
            slot["medians"].append(value)

    template_rows: List[Dict[str, Any]] = []
    values: Dict[Tuple[Any, ...], Optional[float]] = {}
    for (engine, timing, cache, template, method, layout), slot in sorted(grouped.items()):
        medians = slot["medians"]
        value = float(statistics.median(medians)) if medians else None
        values[(engine, timing, cache, template, method, layout)] = value
        counts = defaultdict(int)
        for status in slot["statuses"].values():
            counts[status] += 1
        template_rows.append({
            "engine": engine, "timing_protocol": timing, "cache_mode": cache,
            "template": template, "display_method": DISPLAY[method], "raw_method": method,
            "layout": layout, "time_field": slot["field"],
            "expected_instances": len(instances),
            "measured_endpoint_ok": counts["ok"], "endpoint_timeout": counts["timeout"],
            "unsupported": counts["unsupported"], "failed": counts["failed"],
            "successful_median_ms": "" if value is None else round(value, 6),
            "plotted_successful_median_ms": "" if value is None else round(value, 6),
            "plot_kind": (
                "successful-median-bar" if value is not None
                else "unsupported" if counts["unsupported"] else "timeout-bar"
                if counts["timeout"] else "failed"
            ),
        })

    def reference(row: Mapping[str, Any], method: str, layout: str) -> Optional[float]:
        return values.get((row["engine"], row["timing_protocol"], row["cache_mode"],
                           row["template"], method, layout))

    for row in template_rows:
        own = values[(row["engine"], row["timing_protocol"], row["cache_mode"],
                      row["template"], row["raw_method"], row["layout"])]
        method = row["raw_method"]
        row["ratio_vs_B"] = (
            "" if method == "B" else _round(_ratio(own, reference(row, "B", "plain")))
        )
        row["ratio_vs_B_mixed"] = (
            "" if method == "B-mixed"
            else _round(_ratio(own, reference(row, "B-mixed", "plain")))
        )
        # R of the same physical layout; B and B-mixed have none.
        row["ratio_vs_R"] = (
            "" if method in ("R", "B", "B-mixed")
            else _round(_ratio(own, reference(row, "R", row["layout"])))
        )

    ratio_rows: List[Dict[str, Any]] = []
    series = defaultdict(lambda: defaultdict(list))
    for row in template_rows:
        identity = (row["engine"], row["timing_protocol"], row["cache_mode"],
                    row["display_method"], row["raw_method"], row["layout"])
        for reference_name, column in (("B", "ratio_vs_B"), ("B-mixed", "ratio_vs_B_mixed"),
                                       ("R", "ratio_vs_R")):
            if row[column] != "" and row[column] is not None:
                series[identity][reference_name].append(float(row[column]))
    for identity, by_reference in sorted(series.items()):
        for reference_name, ratios in sorted(by_reference.items()):
            ratio_rows.append({
                "engine": identity[0], "timing_protocol": identity[1], "cache_mode": identity[2],
                "display_method": identity[3], "raw_method": identity[4], "layout": identity[5],
                "reference": reference_name,
                "templates_with_ratio": len(ratios),
                "median_of_template_ratios": round(float(statistics.median(ratios)), 6),
                "min_template_ratio": round(min(ratios), 6),
                "max_template_ratio": round(max(ratios), 6),
            })

    parity = answer_parity(cells)
    return {
        "schema": SCHEMA,
        "statistic": (
            "median of the five measured executions per cell; median of instance medians per "
            "template; ratios per template; median of per-template ratios"
        ),
        "c_time_field": c_field,
        "workload": str(workload_root),
        "cell_roots": [str(root.resolve()) for root in roots],
        "templates": list(templates),
        "instances": list(instances),
        "template_rows": template_rows,
        "ratio_rows": ratio_rows,
        "cells": per_cell,
        "answer_parity": parity,
    }


def _round(value: Optional[float]) -> Any:
    return "" if value is None else round(value, 6)


def answer_parity(cells: Mapping[Tuple[Any, ...], Mapping[str, Any]]) -> Dict[str, Any]:
    """Compare the answer sets of methods that evaluate the same query semantics."""
    by_query: Dict[Tuple[Any, ...], Dict[Tuple[str, str], Path]] = defaultdict(dict)
    for (engine, timing, cache, query_id, method, layout), cell in cells.items():
        if cell_status(cell) != "ok":
            continue
        answers = _first_measured_answers(Path(cell["_path"]), method)
        if answers is not None:
            by_query[(engine, timing, cache, query_id)][(method, layout)] = answers
    checks = []
    for query, available in sorted(by_query.items()):
        pairs = []
        plain = [key for key in available if key[0] in ("B", "B-mixed", "R")]
        npcs = [key for key in available if key[0] in ("N", "N-orig")]
        for group, multiset in ((plain, True), (npcs, False)):
            ordered = sorted(group)
            for left, right in zip(ordered, ordered[1:]):
                pairs.append((left, right, multiset))
        for left, right, multiset in pairs:
            left_path, right_path = available[left], available[right]
            if multiset:
                equal = left_path.read_bytes() == right_path.read_bytes()
            else:
                equal = _answer_set(left_path) == _answer_set(right_path)
            checks.append({
                "engine": query[0], "timing_protocol": query[1], "cache_mode": query[2],
                "query_id": query[3],
                "left": "%s/%s" % left, "right": "%s/%s" % right,
                "comparison": "answer multiset" if multiset else "answer set",
                "equal": equal,
            })
    return {
        "checked_pairs": len(checks),
        "unequal_pairs": [check for check in checks if not check["equal"]],
        "rule": (
            "first measured execution; B, B-mixed and R compare exact answer multisets "
            "(streamed TSV evidence below the exact-row limit), N and N-orig compare "
            "answer sets"
        ),
    }


def _write_csv(path: Path, columns: Sequence[str], rows: Iterable[Mapping[str, Any]]) -> None:
    if path.exists():
        raise SummaryError("refusing to overwrite %s" % path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(columns))
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in columns})


def write_outputs(result: Mapping[str, Any], json_path: Path, csv_path: Path,
                  ratios_path: Path) -> None:
    for path in (json_path, csv_path, ratios_path):
        if path.exists():
            raise SummaryError("refusing to overwrite %s" % path)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    partial = json_path.with_name(json_path.name + ".partial")
    with partial.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(partial, json_path)
    _write_csv(csv_path, TEMPLATE_COLUMNS, result["template_rows"])
    _write_csv(ratios_path, RATIO_COLUMNS, result["ratio_rows"])


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--workload", required=True, type=Path)
    parser.add_argument("--cells", required=True, action="append", type=Path,
                        help="an E1 batch output root; repeat for engines and layouts")
    parser.add_argument("--c-field", choices=("endpoint_only_ms", "c_request_ms_sum",
                                              "legacy_endpoint_e2e_ms"),
                        default="endpoint_only_ms")
    parser.add_argument("--template", dest="templates", action="append",
                        choices=published.FORMAL_TEMPLATES)
    parser.add_argument("--instance", dest="instances", action="append")
    parser.add_argument("--json", required=True, type=Path)
    parser.add_argument("--csv", required=True, type=Path, help="per-template plot data")
    parser.add_argument("--ratios-csv", required=True, type=Path)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        result = summarize(
            args.workload.resolve(), [path.resolve() for path in args.cells], args.c_field,
            tuple(args.templates or published.FORMAL_TEMPLATES),
            tuple(args.instances or published.FORMAL_INSTANCES),
        )
        write_outputs(result, args.json, args.csv, args.ratios_csv)
    except (OSError, ValueError, SummaryError, published.BatchError) as error:
        parser.exit(2, "WatDiv E1 summary: error: %s\n" % error)
    print(json.dumps({
        "schema": result["schema"],
        "template_rows": len(result["template_rows"]),
        "ratio_rows": len(result["ratio_rows"]),
        "unequal_answer_pairs": len(result["answer_parity"]["unequal_pairs"]),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
