#!/usr/bin/env python3
"""Run the formal WatDiv 10M Figure 3 matrix as immutable cells.

The formal matrix uses query instances 00, 01, and 02.  Ordinary templates
run B, R, N, C-flat, and C-factorised; P-plus and P-star run B and C-path.
Each cell uses one warm-up, five measured executions, and the measured median.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
from pathlib import Path
import subprocess
import sys
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple


PAPER = Path(__file__).resolve().parent
REFERENCE = PAPER.parent
if str(REFERENCE) not in sys.path:
    sys.path.insert(0, str(REFERENCE))
if str(PAPER) not in sys.path:
    sys.path.insert(0, str(PAPER))

from watdiv import prepare_data
import watdiv10m_workload as workload


SCHEMA = "watdiv10m-formal-batch-v1"
RUNNER = PAPER / "watdiv10m_runner.py"
FORMAL_PATH_TEMPLATES = ("P-plus", "P-star")
FORMAL_TEMPLATES = workload.NON_PATH_TEMPLATES + FORMAL_PATH_TEMPLATES
FORMAL_INSTANCES = ("00", "01", "02")
NON_PATH_METHODS = ("B", "R", "N", "C-flat", "C-factorised")
PATH_METHODS = ("B", "C-path")
ALL_METHODS = NON_PATH_METHODS + ("C-path",)


class BatchError(RuntimeError):
    """The formal WatDiv batch cannot continue safely."""


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise BatchError("cannot read JSON %s: %s" % (path, error)) from error


def _write_json(path: Path, value: Any) -> None:
    if path.exists():
        raise BatchError("refusing to overwrite %s" % path)
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".partial")
    with partial.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(partial, path)


def _read_workload_rows(root: Path) -> List[Dict[str, str]]:
    workload.audit_workload(root)
    with (root / "query-list.tsv").open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    if not rows:
        raise BatchError("WatDiv workload contains no query rows")
    return rows


def _validate(args: argparse.Namespace, dataset: Mapping[str, Any]) -> None:
    if str(dataset.get("scale_factor")) != "1":
        raise BatchError("watdiv10m_batch requires a WatDiv scale-1 dataset")
    if args.warmups != 1 or args.runs != 5:
        raise BatchError("the formal protocol is exactly one warm-up and five measured runs")
    if len(set(args.methods)) != len(args.methods):
        raise BatchError("methods must be unique")
    if len(set(args.templates)) != len(args.templates):
        raise BatchError("templates must be unique")
    if len(set(args.instances)) != len(args.instances):
        raise BatchError("instances must be unique")
    unknown_templates = sorted(set(args.templates).difference(FORMAL_TEMPLATES))
    unknown_methods = sorted(set(args.methods).difference(ALL_METHODS))
    if unknown_templates or unknown_methods:
        raise BatchError(
            "unsupported formal selection: templates=%s methods=%s"
            % (unknown_templates, unknown_methods)
        )
    if any(method != "B" for method in args.methods) and not args.mixed_endpoint:
        raise BatchError("R, N, and C require --mixed-endpoint")
    if any(method in ("N", "C-flat", "C-factorised", "C-path") for method in args.methods):
        if args.jar is None or not args.jar.is_file():
            raise BatchError("N and C require an existing --jar")
    if any(method in ("C-factorised", "C-path") for method in args.methods):
        if args.c_endpoint_protocol == "sparql" and not args.update_endpoint:
            raise BatchError("writable C plans require --update-endpoint")


def _selected_rows(
    rows: Sequence[Mapping[str, str]],
    templates: Sequence[str],
    instances: Sequence[str],
) -> List[Mapping[str, str]]:
    selected = [
        row for row in rows
        if row["template"] in templates and row["instance"] in instances
    ]
    expected = {
        (template, instance)
        for template in templates
        for instance in instances
    }
    observed = {(row["template"], row["instance"]) for row in selected}
    missing = sorted(expected.difference(observed))
    if missing:
        raise BatchError("workload lacks selected template/instance pairs: %s" % missing)
    selected.sort(key=lambda row: (FORMAL_TEMPLATES.index(row["template"]), row["instance"]))
    return selected


def _methods_for(row: Mapping[str, str], requested: Sequence[str]) -> Tuple[str, ...]:
    allowed = PATH_METHODS if row["template"] in FORMAL_PATH_TEMPLATES else NON_PATH_METHODS
    return tuple(method for method in requested if method in allowed)


def _cell_path(output: Path, row: Mapping[str, str], method: str) -> Path:
    return output / "cells" / row["template"] / row["instance"] / method


def _cell_record(path: Path) -> Optional[Mapping[str, Any]]:
    manifest = path / "cell.json"
    if manifest.is_file():
        value = _read_json(manifest)
        if not isinstance(value, Mapping):
            raise BatchError("cell manifest is not an object: %s" % manifest)
        return value
    if path.exists():
        raise BatchError(
            "partial cell has no cell.json; preserve or move it before resume: %s" % path
        )
    return None


def _runner_command(
    args: argparse.Namespace,
    row: Mapping[str, str],
    method: str,
    cell: Path,
    workload_root: Path,
    mixed_data: Path,
) -> List[str]:
    command = [
        sys.executable,
        str(RUNNER),
        "--query", str(workload_root / row["query_file"]),
        "--query-id", row["query_id"],
        "--workload", "watdiv",
        "--engine", args.engine,
        "--method", method,
        "--scheme", "SPARQL_Star",
        "--out", str(cell),
        "--base-endpoint", args.base_endpoint,
        "--warmups", "1",
        "--runs", "5",
        "--primary-statistic", "median",
        "--endpoint-timeout", str(args.endpoint_timeout),
        "--offline-timeout", str(args.offline_timeout),
        "--complete-method-timeout", str(args.complete_method_timeout),
        "--memory-sample-interval", str(args.memory_sample_interval),
        "--stop-after-warmup-offline-failure",
        "--pqe-backend", "none",
        "--c-parallelism", "1",
    ]
    if method in ("B", "R", "N"):
        command.extend(("--response-mode", "stream-tsv"))
    if method != "B":
        command.extend(("--reified-endpoint", args.mixed_endpoint))
    if method in ("N", "C-flat", "C-factorised", "C-path"):
        command.extend(("--jar", str(args.jar.resolve()), "--java", args.java))
    if method in ("C-flat", "C-factorised", "C-path"):
        command.extend((
            "--reified-data", str(mixed_data),
            "--c-endpoint-protocol", args.c_endpoint_protocol,
            "--skip-bnode-check",
        ))
        if args.java_max_heap:
            command.extend(("--java-max-heap", args.java_max_heap))
    if method in ("C-factorised", "C-path") and args.update_endpoint:
        command.extend(("--update-endpoint", args.update_endpoint))
    if args.engine_pid is not None:
        command.extend(("--engine-pid", str(args.engine_pid)))
    return command


def _configuration(
    args: argparse.Namespace,
    dataset: Mapping[str, Any],
    rows: Sequence[Mapping[str, str]],
) -> Dict[str, Any]:
    cells = sum(len(_methods_for(row, args.methods)) for row in rows)
    return {
        "schema": SCHEMA,
        "workload": str(args.workload.resolve()),
        "dataset": str(args.dataset.resolve()),
        "dataset_scale_factor": dataset["scale_factor"],
        "rdf_star_profile": dataset["rdf_star_profile"],
        "engine": args.engine,
        "templates": list(args.templates),
        "instances": list(args.instances),
        "requested_methods": list(args.methods),
        "expected_physical_cells": cells,
        "protocol": {
            "warmups": 1,
            "measured_runs": 5,
            "primary_statistic": "median",
            "endpoint_timeout_s": args.endpoint_timeout,
            "offline_timeout_s": args.offline_timeout,
            "complete_method_timeout_s": args.complete_method_timeout,
        },
        "endpoints": {
            "base": args.base_endpoint,
            "mixed": args.mixed_endpoint,
            "update": args.update_endpoint,
        },
        "c_result_handling": "streaming",
        "n_result_handling": "stream-tsv",
        "c_parallelism": 1,
        "cell_order": "formal template order, instance order, requested method order",
        "immutability": "terminal cell.json artifacts are reused and never overwritten",
    }


def run(args: argparse.Namespace) -> Dict[str, Any]:
    workload_root = args.workload.resolve()
    rows = _read_workload_rows(workload_root)
    dataset = _read_json(args.dataset.resolve())
    prepare_data.audit(args.dataset.resolve(), scan_mixed=False)
    _validate(args, dataset)
    selected = _selected_rows(rows, args.templates, args.instances)
    selected = [row for row in selected if _methods_for(row, args.methods)]
    if not selected:
        raise BatchError("the requested methods select no formal cells")

    output = args.out.resolve()
    output.mkdir(parents=True, exist_ok=True)
    config_path = output / "batch-config.json"
    configuration = _configuration(args, dataset, selected)
    if config_path.exists():
        if _read_json(config_path) != configuration:
            raise BatchError("resume configuration differs from batch-config.json")
    else:
        _write_json(config_path, configuration)
    final_path = output / "batch.json"
    if final_path.is_file():
        return _read_json(final_path)

    mixed_data = (
        args.dataset.resolve().parent / dataset["layouts"]["mixed"]["path"]
    ).resolve()
    invoked = 0
    reused = 0
    identities: List[Tuple[Mapping[str, str], str]] = [
        (row, method)
        for row in selected
        for method in _methods_for(row, args.methods)
    ]
    for row, method in identities:
        cell = _cell_path(output, row, method)
        existing = _cell_record(cell)
        if existing is not None:
            if (
                existing.get("query_id") != row["query_id"]
                or existing.get("engine") != args.engine
                or existing.get("method") != method
                or existing.get("workload") != "watdiv"
                or existing.get("scheme") != "SPARQL_Star"
            ):
                raise BatchError("existing cell identity mismatch: %s" % cell)
            reused += 1
            continue
        completed = subprocess.run(
            _runner_command(args, row, method, cell, workload_root, mixed_data),
            check=False,
        )
        invoked += 1
        created = _cell_record(cell)
        if created is None:
            raise BatchError("runner returned without a terminal cell: %s" % cell)
        if created.get("recovery_required") is True:
            raise BatchError(
                "endpoint recovery is required before another cell can run: %s" % cell
            )
        if completed.returncode != 0 and not args.continue_after_failure:
            raise BatchError("cell failed with status %s: %s" % (created.get("status"), cell))

    records = [_cell_record(_cell_path(output, row, method)) for row, method in identities]
    if any(record is None for record in records):
        raise BatchError("batch ended before all selected cells became terminal")
    successes = sum(record.get("status") == "ok" for record in records if record)
    result = {
        "schema": SCHEMA,
        "status": "terminal",
        "engine": args.engine,
        "expected_cells": len(records),
        "successful_cells": successes,
        "non_successful_cells": len(records) - successes,
        "invoked_in_final_pass": invoked,
        "reused_in_final_pass": reused,
        "batch_config": str(config_path),
    }
    _write_json(final_path, result)
    return result


def _positive(value: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("value must be positive and finite")
    return number


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--workload", required=True, type=Path)
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--engine", required=True)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--base-endpoint", required=True)
    parser.add_argument("--mixed-endpoint")
    parser.add_argument("--update-endpoint")
    parser.add_argument("--c-endpoint-protocol", choices=("sparql", "rdf4j"), default="sparql")
    parser.add_argument("--engine-pid", type=int)
    parser.add_argument("--jar", type=Path)
    parser.add_argument("--java", default="java")
    parser.add_argument("--java-max-heap")
    parser.add_argument("--method", dest="methods", action="append", choices=ALL_METHODS)
    parser.add_argument("--template", dest="templates", action="append", choices=FORMAL_TEMPLATES)
    parser.add_argument("--instance", dest="instances", action="append")
    parser.add_argument("--warmups", type=int, default=1)
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--endpoint-timeout", type=_positive, default=600.0)
    parser.add_argument("--offline-timeout", type=_positive, default=600.0)
    parser.add_argument("--complete-method-timeout", type=_positive, default=600.0)
    parser.add_argument("--memory-sample-interval", type=_positive, default=0.05)
    parser.add_argument("--continue-after-failure", action="store_true")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    args.methods = tuple(args.methods or ALL_METHODS)
    args.templates = tuple(args.templates or FORMAL_TEMPLATES)
    args.instances = tuple(args.instances or FORMAL_INSTANCES)
    try:
        result = run(args)
    except (OSError, ValueError, BatchError, prepare_data.PreparationError,
            workload.WorkloadError) as error:
        parser.exit(2, "WatDiv 10M batch: error: %s\n" % error)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
