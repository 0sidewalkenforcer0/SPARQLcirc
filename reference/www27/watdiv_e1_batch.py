#!/usr/bin/env python3
"""WWW'27 experiment E1: NPCS under comparable conditions on WatDiv 10M.

Two subcommands share one matrix definition:

``prepare``
    Generates, without contacting any endpoint, the query text of every
    non-C cell (B, B-mixed, R, N, N-orig) with exactly the code the runner
    uses, and writes ``prepare-report.json``/``.tsv``: the N-orig refusals with
    their reasons, the RDF4J parse check of every original-jar output, and the
    comparison of the original jar with the re-implementation on every BGP
    instance.  Run it before the endpoint batch; it fails on any unexpected
    preparation error.

``run``
    Runs the matrix as immutable cells through ``reference/paper/watdiv10m_runner.py``.
    Unlike the published batch (``reference/paper/watdiv10m_batch.py``) the
    layout and the timing protocol must be chosen explicitly, B can also be
    sent to the mixed store (method ``B-mixed``), N-orig is part of the
    matrix, and a cold-cache hook can be supplied.  N-orig cells for queries
    outside the original jar's correct fragment end as ``unsupported`` cells
    without any endpoint request; they are terminal and are not failures.

Both subcommands are resumable: terminal ``cell.json`` files are reused, and
the batch refuses to continue with a configuration that differs from the
recorded ``batch-config.json``.
"""
from __future__ import annotations

import argparse
import csv
from collections import Counter
import json
import math
import os
from pathlib import Path
import subprocess
import sys
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple


HERE = Path(__file__).resolve().parent
REFERENCE = HERE.parent
PAPER = REFERENCE / "paper"
for _path in (REFERENCE, PAPER):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from watdiv import prepare_data
import watdiv10m_batch as published
import watdiv10m_runner as runner
import watdiv10m_workload as workload


SCHEMA = "www27-watdiv-e1-batch-v1"
PREPARE_REPORT_SCHEMA = "www27-watdiv-e1-prepare-report-v1"
RUNNER = PAPER / "watdiv10m_runner.py"
LAYOUT_SCHEMES = {
    "lookup-only": "SPARQL_Star_Pure",
    "asserted-plus-lookup": "SPARQL_Star",
}
NON_PATH_METHODS = ("B", "B-mixed", "R", "N-orig", "N", "C-flat", "C-factorised")
PATH_METHODS = ("B", "B-mixed", "C-path")
ALL_METHODS = NON_PATH_METHODS + ("C-path",)
PREPARED_METHODS = ("B", "B-mixed", "R", "N-orig", "N")


class E1Error(RuntimeError):
    """The E1 batch cannot continue safely."""


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise E1Error("cannot read JSON %s: %s" % (path, error)) from error


def _write_json(path: Path, value: Any) -> None:
    if path.exists():
        raise E1Error("refusing to overwrite %s" % path)
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".partial")
    with partial.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(partial, path)


def methods_for(template: str, requested: Sequence[str]) -> Tuple[str, ...]:
    allowed = PATH_METHODS if template in published.FORMAL_PATH_TEMPLATES else NON_PATH_METHODS
    return tuple(method for method in requested if method in allowed)


def scheme_for(method: str, layout: str) -> str:
    """The cell scheme: N-orig only has the lookup-only layout."""
    if method == "N-orig":
        return LAYOUT_SCHEMES["lookup-only"]
    return LAYOUT_SCHEMES[layout]


def _selected(args: argparse.Namespace) -> Tuple[Path, List[Mapping[str, str]]]:
    workload_root = args.workload.resolve()
    rows = published._read_workload_rows(workload_root)
    selected = published._selected_rows(rows, args.templates, args.instances)
    selected = [row for row in selected if methods_for(row["template"], args.methods)]
    if not selected:
        raise E1Error("the requested methods select no cells")
    return workload_root, selected


def _cell_path(output: Path, row: Mapping[str, str], method: str) -> Path:
    return output / "cells" / row["template"] / row["instance"] / method


def _check_tools(args: argparse.Namespace) -> None:
    needs_ours = any(method in ("N", "N-orig", "C-flat", "C-factorised", "C-path")
                     for method in args.methods)
    if needs_ours and (args.jar is None or not args.jar.is_file()):
        raise E1Error("N, N-orig and C need an existing --jar (engine/target/npcs-rewrite.jar)")
    if "N-orig" in args.methods and (args.orig_jar is None or not args.orig_jar.is_file()):
        raise E1Error("N-orig needs the original ReifySparqlByte.jar via --orig-jar or $NPCS_ORIG_JAR")


# --------------------------------------------------------------------- prepare

def prepare(args: argparse.Namespace) -> Dict[str, Any]:
    """Generate every non-C query text before any endpoint work."""
    _check_tools(args)
    workload_root, selected = _selected(args)
    output = args.out.resolve()
    output.mkdir(parents=True, exist_ok=True)
    report_path = output / "prepare-report.json"
    if report_path.exists():
        return _read_json(report_path)
    rows: List[Dict[str, Any]] = []
    for row in selected:
        for method in methods_for(row["template"], args.methods):
            if method not in PREPARED_METHODS:
                continue
            target = output / "prepared" / row["template"] / row["instance"] / method
            document_path = target / "prepared-query.json"
            if document_path.is_file():
                document = _read_json(document_path)
            else:
                if target.exists():
                    raise E1Error("partial preparation must be moved away first: %s" % target)
                target.mkdir(parents=True)
                config = {
                    "query": str(workload_root / row["query_file"]),
                    "query_id": row["query_id"],
                    "method": method,
                    "scheme": scheme_for(method, args.layout),
                    "jar": str(args.jar.resolve()) if args.jar else None,
                    "orig_jar": str(args.orig_jar.resolve()) if args.orig_jar else None,
                    "java": args.java,
                    "expected_r_query": None,
                    "prepare_timeout_s": args.prepare_timeout,
                }
                document = runner._prepare_query(config, target)
            rows.append({
                "query_id": row["query_id"],
                "template": row["template"],
                "instance": row["instance"],
                "method": method,
                "scheme": scheme_for(method, args.layout),
                "status": document["status"],
                "reason": document.get("unsupported_reason") or document.get("detail"),
                "reimplementation_equivalent": document.get("reimplementation_equivalent"),
                "rdf4j_parse": document.get("rdf4j_parse"),
                "prepared_query_bytes": document.get("prepared_query_bytes"),
                "prepare_ms": document.get("prepare_ms"),
            })
    errors = [row for row in rows if row["status"] == "prepare-error"]
    n_orig = [row for row in rows if row["method"] == "N-orig"]
    report = {
        "schema": PREPARE_REPORT_SCHEMA,
        "layout": args.layout,
        "workload": str(workload_root),
        "cells": len(rows),
        "status_counts": dict(sorted(Counter(
            "%s:%s" % (row["method"], row["status"]) for row in rows
        ).items())),
        "n_orig": {
            "supported": sum(row["status"] == "ok" for row in n_orig),
            "unsupported": sum(row["status"] == "unsupported" for row in n_orig),
            "equivalent_to_reimplementation": sum(
                row["reimplementation_equivalent"] is True for row in n_orig
                if row["status"] == "ok"
            ),
            "not_equivalent": [
                row["query_id"] for row in n_orig
                if row["status"] == "ok" and row["reimplementation_equivalent"] is not True
            ],
            "refusal_reasons": dict(sorted(Counter(
                str(row["reason"]).split("this query uses: ")[-1]
                for row in n_orig if row["status"] == "unsupported"
            ).items())),
        },
        "prepare_errors": [
            {key: row[key] for key in ("query_id", "method", "reason")} for row in errors
        ],
        "rows": rows,
    }
    _write_json(report_path, report)
    with (output / "prepare-report.tsv").open("x", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]) if rows else ["query_id"],
                                delimiter="\t", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    if errors:
        raise E1Error("%d query texts could not be prepared; see %s" % (len(errors), report_path))
    return report


# ------------------------------------------------------------------------- run

def _validate_run(args: argparse.Namespace, dataset: Mapping[str, Any]) -> None:
    if str(dataset.get("scale_factor")) != "1":
        raise E1Error("E1 runs on the WatDiv scale-1 (10M) dataset")
    if args.runs != 5:
        raise E1Error("the protocol has exactly five measured executions")
    if args.cache_mode == "warm" and args.warmups != 1:
        raise E1Error("warm-cache cells use exactly one warm-up")
    if args.cache_mode == "cold":
        if args.warmups not in (0, 1):
            raise E1Error("cold-cache cells use zero or one warm-up")
        if not args.cache_reset_command:
            raise E1Error("--cache-mode cold requires --cache-reset-command")
        if args.engine_pid is not None:
            raise E1Error("the store restarts in cold mode; use --engine-pid-file, not --engine-pid")
    if args.engine_pid is not None and args.engine_pid_file is not None:
        raise E1Error("use either --engine-pid or --engine-pid-file")
    if len(set(args.methods)) != len(args.methods):
        raise E1Error("methods must be unique")
    unknown = sorted(set(args.methods).difference(ALL_METHODS))
    if unknown:
        raise E1Error("unsupported methods: %s" % unknown)
    if any(method != "B" for method in args.methods) and not args.mixed_endpoint:
        raise E1Error("B-mixed, R, N, N-orig and C require --mixed-endpoint")
    if "B" in args.methods and not args.base_endpoint:
        raise E1Error("B requires --base-endpoint (the plain store)")
    _check_tools(args)
    if any(method in ("C-factorised", "C-path") for method in args.methods):
        if args.c_endpoint_protocol == "sparql" and not args.update_endpoint:
            raise E1Error("writable C plans require --update-endpoint")
    if args.pqe_backend != "none" and args.probability_seed is None:
        raise E1Error("PQE needs --probability-seed")


def runner_command(
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
        "--scheme", scheme_for(method, args.layout),
        "--out", str(cell),
        "--warmups", str(args.warmups),
        "--runs", str(args.runs),
        "--primary-statistic", "median",
        "--timing-protocol", args.timing_protocol,
        "--endpoint-timeout", str(args.endpoint_timeout),
        "--offline-timeout", str(args.offline_timeout),
        "--complete-method-timeout", str(args.complete_method_timeout),
        "--memory-sample-interval", str(args.memory_sample_interval),
        "--endpoint-buffer-bytes", str(args.endpoint_buffer_bytes),
        "--stop-after-warmup-offline-failure",
        "--c-parallelism", "1",
        "--cache-mode", args.cache_mode,
    ]
    if args.pqe_backend != "none" and method in ("N", "N-orig", "C-flat", "C-factorised", "C-path"):
        # In-line PQE inside the complete-method deadline; B, B-mixed and R
        # have no provenance to evaluate.
        command.extend((
            "--pqe-backend", args.pqe_backend,
            "--probability-seed", str(args.probability_seed),
        ))
        if method in ("N", "N-orig"):
            command.extend(("--npcs-variable-order", args.npcs_variable_order))
    if args.cache_mode == "cold":
        command.extend((
            "--cache-reset-command", args.cache_reset_command,
            "--cache-reset-timeout", str(args.cache_reset_timeout),
            "--cache-ready-probe", args.cache_ready_probe,
            "--cache-ready-timeout", str(args.cache_ready_timeout),
        ))
    if args.base_endpoint:
        command.extend(("--base-endpoint", args.base_endpoint))
    if method in ("B", "B-mixed", "R", "N", "N-orig"):
        command.extend(("--response-mode", "stream-tsv"))
    if method != "B":
        command.extend(("--reified-endpoint", args.mixed_endpoint))
    if method in ("N", "N-orig", "C-flat", "C-factorised", "C-path"):
        command.extend(("--jar", str(args.jar.resolve()), "--java", args.java))
    if method == "N-orig":
        command.extend(("--orig-jar", str(args.orig_jar.resolve())))
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
    if args.engine_pid_file is not None:
        command.extend(("--engine-pid-file", str(args.engine_pid_file.resolve())))
    return command


def _configuration(args: argparse.Namespace, dataset: Mapping[str, Any],
                   rows: Sequence[Mapping[str, str]]) -> Dict[str, Any]:
    return {
        "schema": SCHEMA,
        "workload": str(args.workload.resolve()),
        "dataset": str(args.dataset.resolve()),
        "dataset_scale_factor": dataset["scale_factor"],
        "rdf_star_profile": dataset["rdf_star_profile"],
        "engine": args.engine,
        "layout": args.layout,
        "schemes": {method: scheme_for(method, args.layout) for method in args.methods},
        "templates": list(args.templates),
        "instances": list(args.instances),
        "requested_methods": list(args.methods),
        "expected_physical_cells": sum(
            len(methods_for(row["template"], args.methods)) for row in rows
        ),
        "protocol": {
            "warmups": args.warmups,
            "measured_runs": args.runs,
            "primary_statistic": "median",
            "timing_protocol": args.timing_protocol,
            "cache_mode": args.cache_mode,
            "cache_reset_command": args.cache_reset_command,
            "cache_ready_probe": args.cache_ready_probe if args.cache_mode == "cold" else None,
            "endpoint_timeout_s": args.endpoint_timeout,
            "offline_timeout_s": args.offline_timeout,
            "complete_method_timeout_s": args.complete_method_timeout,
            "endpoint_buffer_bytes": args.endpoint_buffer_bytes,
            "pqe_backend": args.pqe_backend,
            "probability_seed": args.probability_seed if args.pqe_backend != "none" else None,
            "npcs_variable_order": (
                args.npcs_variable_order if args.pqe_backend != "none" else None
            ),
        },
        "stores": {
            "B": "plain store (--base-endpoint)",
            "B-mixed": "mixed store (--mixed-endpoint)",
            "R/N/N-orig/C": "mixed store (--mixed-endpoint)",
        },
        "endpoints": {
            "base": args.base_endpoint,
            "mixed": args.mixed_endpoint,
            "update": args.update_endpoint,
        },
        "jars": {
            "ours": str(args.jar.resolve()) if args.jar else None,
            "original": str(args.orig_jar.resolve()) if args.orig_jar else None,
        },
        "response_handling": "stream-tsv for B, B-mixed, R, N, N-orig; streamed CONSTRUCT for C",
        "c_parallelism": 1,
        "cell_order": "formal template order, instance order, requested method order",
        "immutability": "terminal cell.json artifacts are reused and never overwritten",
    }


def _identity_matches(existing: Mapping[str, Any], args: argparse.Namespace,
                      row: Mapping[str, str], method: str) -> bool:
    protocol = existing.get("protocol") if isinstance(existing.get("protocol"), Mapping) else {}
    return (
        existing.get("query_id") == row["query_id"]
        and existing.get("engine") == args.engine
        and existing.get("method") == method
        and existing.get("workload") == "watdiv"
        and existing.get("scheme") == scheme_for(method, args.layout)
        and protocol.get("timing_protocol") == args.timing_protocol
        and protocol.get("cache_mode") == args.cache_mode
    )


def run(args: argparse.Namespace) -> Dict[str, Any]:
    workload_root, selected = _selected(args)
    dataset = _read_json(args.dataset.resolve())
    prepare_data.audit(args.dataset.resolve(), scan_mixed=False)
    _validate_run(args, dataset)
    output = args.out.resolve()
    output.mkdir(parents=True, exist_ok=True)
    config_path = output / "batch-config.json"
    configuration = _configuration(args, dataset, selected)
    if config_path.exists():
        if _read_json(config_path) != configuration:
            raise E1Error("resume configuration differs from batch-config.json")
    else:
        _write_json(config_path, configuration)
    final_path = output / "batch.json"
    if final_path.is_file():
        return _read_json(final_path)
    mixed_data = (args.dataset.resolve().parent / dataset["layouts"]["mixed"]["path"]).resolve()
    identities = [
        (row, method) for row in selected for method in methods_for(row["template"], args.methods)
    ]
    invoked = reused = 0
    for row, method in identities:
        cell = _cell_path(output, row, method)
        existing = published._cell_record(cell)
        if existing is not None:
            if not _identity_matches(existing, args, row, method):
                raise E1Error("existing cell identity mismatch: %s" % cell)
            reused += 1
            continue
        completed = subprocess.run(
            runner_command(args, row, method, cell, workload_root, mixed_data), check=False
        )
        invoked += 1
        created = published._cell_record(cell)
        if created is None:
            raise E1Error("runner returned without a terminal cell: %s" % cell)
        if created.get("recovery_required") is True:
            raise E1Error("endpoint recovery is required before another cell can run: %s" % cell)
        if created.get("status") in ("ok", "unsupported"):
            continue
        if not args.continue_after_failure:
            raise E1Error("cell failed with status %s (exit %d): %s"
                          % (created.get("status"), completed.returncode, cell))
    records = [published._cell_record(_cell_path(output, row, method)) for row, method in identities]
    if any(record is None for record in records):
        raise E1Error("batch ended before all selected cells became terminal")
    statuses = Counter(str(record.get("status")) for record in records if record)
    result = {
        "schema": SCHEMA,
        "status": "terminal",
        "engine": args.engine,
        "layout": args.layout,
        "timing_protocol": args.timing_protocol,
        "cache_mode": args.cache_mode,
        "expected_cells": len(records),
        "status_counts": dict(sorted(statuses.items())),
        "invoked_in_final_pass": invoked,
        "reused_in_final_pass": reused,
        "batch_config": str(config_path),
    }
    _write_json(final_path, result)
    return result


# ----------------------------------------------------------------------- CLI

def _positive(value: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("value must be positive and finite")
    return number


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("prepare", "run"):
        command = sub.add_parser(name)
        command.add_argument("--workload", required=True, type=Path)
        command.add_argument("--out", required=True, type=Path)
        command.add_argument("--layout", required=True, choices=tuple(LAYOUT_SCHEMES))
        command.add_argument("--jar", type=Path)
        command.add_argument(
            "--orig-jar", type=Path,
            default=Path(os.environ["NPCS_ORIG_JAR"]) if os.environ.get("NPCS_ORIG_JAR") else None,
        )
        command.add_argument("--java", default="java")
        command.add_argument("--method", dest="methods", action="append", choices=ALL_METHODS)
        command.add_argument("--template", dest="templates", action="append",
                             choices=published.FORMAL_TEMPLATES)
        command.add_argument("--instance", dest="instances", action="append")
    prepare_parser = sub.choices["prepare"]
    prepare_parser.add_argument("--prepare-timeout", type=_positive, default=300.0)
    run_parser = sub.choices["run"]
    run_parser.add_argument("--dataset", required=True, type=Path)
    run_parser.add_argument("--engine", required=True)
    run_parser.add_argument("--base-endpoint")
    run_parser.add_argument("--mixed-endpoint")
    run_parser.add_argument("--update-endpoint")
    run_parser.add_argument("--c-endpoint-protocol", choices=("sparql", "rdf4j"), default="sparql")
    run_parser.add_argument("--engine-pid", type=int)
    run_parser.add_argument("--engine-pid-file", type=Path)
    run_parser.add_argument("--java-max-heap")
    run_parser.add_argument("--timing-protocol", required=True, choices=runner.TIMING_PROTOCOLS)
    run_parser.add_argument("--warmups", type=int, default=1)
    run_parser.add_argument("--runs", type=int, default=5)
    run_parser.add_argument("--endpoint-timeout", type=_positive, default=600.0)
    run_parser.add_argument("--offline-timeout", type=_positive, default=600.0)
    run_parser.add_argument("--complete-method-timeout", type=_positive, default=600.0)
    run_parser.add_argument("--memory-sample-interval", type=_positive, default=0.05)
    run_parser.add_argument("--endpoint-buffer-bytes", type=int,
                            default=runner.DEFAULT_ENDPOINT_BUFFER_BYTES)
    run_parser.add_argument("--cache-mode", choices=runner.CACHE_MODES, default="warm")
    run_parser.add_argument("--cache-reset-command")
    run_parser.add_argument("--cache-reset-timeout", type=_positive,
                            default=runner.DEFAULT_CACHE_RESET_TIMEOUT_S)
    run_parser.add_argument("--cache-ready-probe", choices=("ask", "none"), default="ask")
    run_parser.add_argument("--cache-ready-timeout", type=_positive,
                            default=runner.DEFAULT_CACHE_READY_TIMEOUT_S)
    run_parser.add_argument("--pqe-backend", choices=("none", "cudd"), default="none")
    run_parser.add_argument("--probability-seed", type=int)
    run_parser.add_argument("--npcs-variable-order",
                            choices=("sorted-tokens", "dfs-first-appearance"),
                            default="dfs-first-appearance",
                            help="N/N-orig BDD order; the default matches the C compilers")
    run_parser.add_argument("--continue-after-failure", action="store_true")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    args.methods = tuple(args.methods or ALL_METHODS)
    args.templates = tuple(args.templates or published.FORMAL_TEMPLATES)
    args.instances = tuple(args.instances or published.FORMAL_INSTANCES)
    try:
        result = prepare(args) if args.command == "prepare" else run(args)
    except (OSError, ValueError, E1Error, published.BatchError,
            prepare_data.PreparationError, workload.WorkloadError) as error:
        parser.exit(2, "WatDiv E1 batch: error: %s\n" % error)
    summary = {key: value for key, value in result.items() if key != "rows"}
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
