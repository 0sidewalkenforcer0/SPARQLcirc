#!/usr/bin/env python3
"""WWW'27 experiment E2 on WatDiv: end-to-end PQE for NPCS and SPARQLcirc.

The input is an E1 result root (``watdiv_e1_batch.py run``).  For every
successful N, N-orig, C-flat, C-factorised or C-path cell and every one of its
executions, this command compiles the artifact that execution produced and
computes all answer probabilities:

* N and N-orig: the query-global DAG that NPCS post-processing merged from
  the endpoint's provenance strings (``<run>/pp/npcs-hc-dag.json``);
* C: the circuit (``<run>/circuit.nt``).

``--backend cudd`` compiles all roots of a query into one shared CUDD manager
(reference/pqe_from_artifact.py); ``--backend d4`` compiles one Tseitin CNF per
answer root with d4v2 (reference/d4_from_artifact.py).  Both use the
variable-order heuristic ``compiler.deterministic_order`` for N and C alike.

Budget: one complete-method deadline (default 600 s) per execution covers the
endpoint, the NPCS parse+merge (N only) and the PQE process, the same rule as
the Wikidata PQE batch.  Stages per execution: endpoint_ms (the cell's primary
endpoint field: endpoint_only_ms under the endpoint-only protocol),
parse_merge_ms (N: pp_hc_build_wall_ms), circuit_parsing_ms (artifact load,
probability preparation and persistence inside the PQE process), compile_ms,
wmc_ms, and full_pipeline_ms (their sum).

``compare`` checks, per query, that the measured-01 probabilities of all
methods agree answer by answer.  Probabilities are not compared to a ground
truth here; reference/www27/test_npcs_pqe_toy.py does that on toy data.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
import math
import os
from pathlib import Path
import statistics
import sys
import time
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple


HERE = Path(__file__).resolve().parent
REFERENCE = HERE.parent
PAPER = REFERENCE / "paper"
for _path in (REFERENCE, PAPER, REFERENCE / "wdbench"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import event_probabilities
import run_pqe as wikidata_pqe
import watdiv10m_runner as runner


SCHEMA = "www27-watdiv-pqe-cell-v1"
RUN_SCHEMA = "www27-watdiv-pqe-run-v1"
BATCH_SCHEMA = "www27-watdiv-pqe-batch-v1"
PARITY_SCHEMA = "www27-watdiv-pqe-parity-v1"
CELL_SCHEMA = "watdiv-brnc-cell-v1"
METHODS = ("N", "N-orig", "C-flat", "C-factorised", "C-path")
NPCS = ("N", "N-orig")
STAGES = ("endpoint_ms", "parse_merge_ms", "circuit_parsing_ms", "compile_ms", "wmc_ms",
          "compilation_and_wmc_ms", "full_pipeline_ms")


class PosthocError(RuntimeError):
    """The source cells cannot be evaluated unambiguously."""


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise PosthocError("cannot read JSON %s: %s" % (path, error)) from error


def _write_json(path: Path, value: Any) -> None:
    if path.exists():
        raise PosthocError("refusing to overwrite %s" % path)
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".partial")
    with partial.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(partial, path)


def source_cells(root: Path, methods: Sequence[str]) -> List[Tuple[Path, Mapping[str, Any]]]:
    found = []
    for path in sorted(root.rglob("cell.json")):
        if "offline-resume-" in str(path.parent):
            continue
        cell = _read_json(path)
        if not isinstance(cell, Mapping) or cell.get("schema") != CELL_SCHEMA:
            continue
        if cell.get("method") in methods and cell.get("status") == "ok":
            found.append((path.parent, cell))
    return found


def _artifact(cell_dir: Path, method: str, run_id: str) -> Tuple[str, Path]:
    run = cell_dir / run_id
    if method in NPCS:
        return "npcs-shared", run / "pp" / "npcs-hc-dag.json"
    return "circuit", run / "circuit.nt"


def run_one(args: argparse.Namespace, cell_dir: Path, cell: Mapping[str, Any],
            source: Mapping[str, Any], target: Path) -> Dict[str, Any]:
    method = str(cell["method"])
    run_id = str(source["run_id"])
    base: Dict[str, Any] = {
        "schema": RUN_SCHEMA,
        "run_id": run_id,
        "phase": source.get("phase"),
        "backend": args.backend,
        "complete_method_timeout_s": args.complete_method_timeout,
    }
    if source.get("status") != "ok":
        return {**base, "status": "source-incomplete", "failure_class": "SOURCE_INCOMPLETE"}
    kind, artifact = _artifact(cell_dir, method, run_id)
    endpoint_ms = runner._primary_endpoint_ms(source.get("endpoint") or {})
    if endpoint_ms is None or not artifact.is_file():
        return {**base, "status": "source-incomplete", "failure_class": "SOURCE_INCOMPLETE"}
    parse_merge_ms = 0.0
    if method in NPCS:
        offline = source.get("offline") if isinstance(source.get("offline"), Mapping) else {}
        metrics = offline.get("metrics") if isinstance(offline.get("metrics"), Mapping) else {}
        if metrics.get("pp_hc_build_wall_ms") is None:
            return {**base, "status": "source-incomplete", "failure_class": "SOURCE_INCOMPLETE"}
        parse_merge_ms = float(metrics["pp_hc_build_wall_ms"])
    remaining = args.complete_method_timeout - (endpoint_ms + parse_merge_ms) / 1000.0
    base.update(
        endpoint_ms=round(endpoint_ms, 6),
        endpoint_field=(
            "endpoint_only_ms" if "endpoint_only_ms" in (source["endpoint"].get("endpoint") or {})
            else "endpoint_e2e_ms"
        ),
        parse_merge_ms=round(parse_merge_ms, 6),
        source_kind=kind,
        source_artifact=str(artifact),
        source_artifact_bytes=artifact.stat().st_size,
        remaining_pqe_budget_s=round(max(0.0, remaining), 6),
    )
    if remaining <= 0:
        return {**base, "status": "timeout", "failure_class": "TO"}
    target.mkdir(parents=True)
    output = target / "pqe"
    context = ("--query-id", str(cell["query_id"]), "--run-id", run_id, "--method", method)
    if args.backend in ("cudd", "oracle"):
        command = [
            sys.executable, str(REFERENCE / "pqe_from_artifact.py"),
            "--kind", kind, "--input", str(artifact), "--out", str(output),
            "--backend", args.backend,
            "--probability-seed", str(args.probability_seed),
            "--memory-sample-interval", str(args.memory_sample_interval),
            *context,
        ]
    else:
        command = [
            sys.executable, str(REFERENCE / "d4_from_artifact.py"),
            "--kind", kind, "--input", str(artifact), "--out", str(output),
            "--d4", str(args.d4),
            "--probability-seed", str(args.probability_seed),
            "--method-timeout", str(remaining),
            "--memory-sample-interval", str(args.memory_sample_interval),
            *context,
        ]
    started = time.perf_counter()
    process = runner._run_child(command, target / "stdout.log", target / "stderr.log",
                                remaining, args.memory_sample_interval)
    base["process"] = process
    base["pqe_process_observed_wall_ms"] = round((time.perf_counter() - started) * 1000.0, 6)
    if process.get("timed_out") or process.get("returncode") == 124:
        return {**base, "status": "timeout", "failure_class": "TO"}
    if process.get("returncode") != 0:
        return {**base, "status": "error", "failure_class": "PQE_ERROR"}
    metrics_path = output / "metrics.json"
    if not metrics_path.is_file():
        return {**base, "status": "error", "failure_class": "PQE_PROTOCOL_ERROR"}
    metrics = _read_json(metrics_path)
    parsing_ms, compilation_ms = wikidata_pqe.stage_values(args.backend, metrics)
    compile_ms, wmc_ms = wikidata_pqe.stage_split(args.backend, metrics)
    return {
        **base,
        "status": "ok",
        "failure_class": None,
        "metrics": metrics,
        "circuit_parsing_ms": round(parsing_ms, 6),
        "compile_ms": round(compile_ms, 6),
        "wmc_ms": round(wmc_ms, 6),
        "compilation_and_wmc_ms": round(compilation_ms, 6),
        "full_pipeline_ms": round(endpoint_ms + parse_merge_ms + parsing_ms + compilation_ms, 6),
    }


def evaluate_cell(args: argparse.Namespace, cell_dir: Path, cell: Mapping[str, Any],
                  output: Path) -> Dict[str, Any]:
    output.mkdir(parents=True)
    records = []
    for source in cell.get("runs", []):
        if not isinstance(source, Mapping):
            continue
        record = run_one(args, cell_dir, cell, source, output / str(source["run_id"]))
        records.append(record)
        _write_json(output / ("%s.run.json" % source["run_id"]), record)
        if source.get("phase") == "warmup" and record["status"] != "ok":
            break
    measured = [record for record in records if record.get("phase") == "measured"]
    successful = [record for record in measured if record["status"] == "ok"]
    runs_expected = int((cell.get("protocol") or {}).get("measured_runs", 5))
    complete = len(measured) == runs_expected and len(successful) == runs_expected
    result = {
        "schema": SCHEMA,
        "query_id": cell["query_id"],
        "engine": cell["engine"],
        "method": cell["method"],
        "scheme": cell["scheme"],
        "backend": args.backend,
        "source_cell": str(cell_dir),
        "protocol": {
            "measured_runs": runs_expected,
            "primary_statistic": "median",
            "complete_method_timeout_s_per_execution": args.complete_method_timeout,
            "probability_seed": args.probability_seed,
            "probability_scheme": event_probabilities.PROBABILITY_SCHEME,
            "variable_order": "compiler.deterministic_order (DFS first appearance)",
            "granularity": (
                "one shared manager per query" if args.backend in ("cudd", "oracle")
                else "one d4 invocation per answer root"
            ),
            "source_timing_protocol": (cell.get("protocol") or {}).get("timing_protocol"),
        },
        "status": "ok" if complete else "recorded-failure",
        "measured_successes": len(successful),
        "stage_medians_ms": {
            stage: (
                round(float(statistics.median(float(record[stage]) for record in successful)), 6)
                if complete else None
            )
            for stage in STAGES
        },
        "runs": records,
    }
    _write_json(output / "cell.json", result)
    return result


def run(args: argparse.Namespace) -> Dict[str, Any]:
    if args.backend == "d4" and (args.d4 is None or not args.d4.is_file()):
        raise PosthocError("--backend d4 requires an existing --d4 binary")
    output = args.out.resolve()
    output.mkdir(parents=True, exist_ok=True)
    config = {
        "schema": BATCH_SCHEMA,
        "source_root": str(args.cells.resolve()),
        "backend": args.backend,
        "d4": str(args.d4.resolve()) if args.d4 else None,
        "methods": list(args.methods),
        "complete_method_timeout_s": args.complete_method_timeout,
        "probability_seed": args.probability_seed,
    }
    config_path = output / "batch-config.json"
    if config_path.exists():
        if _read_json(config_path) != config:
            raise PosthocError("resume configuration differs from batch-config.json")
    else:
        _write_json(config_path, config)
    invoked = reused = failures = 0
    for cell_dir, cell in source_cells(args.cells.resolve(), args.methods):
        relative = cell_dir.relative_to(args.cells.resolve())
        target = output / relative / args.backend
        if (target / "cell.json").is_file():
            reused += 1
            continue
        if target.exists():
            raise PosthocError("partial PQE cell must be moved away first: %s" % target)
        result = evaluate_cell(args, cell_dir, cell, target)
        invoked += 1
        if result["status"] != "ok":
            failures += 1
            if not args.continue_after_failure:
                raise PosthocError("PQE cell did not complete: %s" % target)
    summary = {
        "schema": BATCH_SCHEMA,
        "status": "terminal",
        "backend": args.backend,
        "invoked": invoked,
        "reused": reused,
        "non_successful_cells": failures,
    }
    return summary


def _probabilities(cell_dir: Path, run_id: str = "measured-01") -> Optional[Dict[str, float]]:
    path = cell_dir / run_id / "pqe" / "probabilities.jsonl"
    if not path.is_file():
        return None
    values = {}
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                row = json.loads(line)
                values[str(row["answer_key"])] = float(row["probability"])
    return values


def compare(roots: Sequence[Path], rel_tol: float = 1e-9, abs_tol: float = 1e-12) -> Dict[str, Any]:
    """Answer-by-answer agreement of the probabilities of all methods and backends."""
    groups: Dict[Tuple[str, str], Dict[str, Path]] = defaultdict(dict)
    for root in roots:
        for path in sorted(root.rglob("cell.json")):
            cell = _read_json(path)
            if not isinstance(cell, Mapping) or cell.get("schema") != SCHEMA:
                continue
            if cell.get("status") != "ok":
                continue
            label = "%s/%s/%s" % (cell["method"], cell["scheme"], cell["backend"])
            groups[(str(cell["engine"]), str(cell["query_id"]))][label] = path.parent
    checks = []
    for (engine, query_id), labels in sorted(groups.items()):
        loaded = {label: _probabilities(path) for label, path in labels.items()}
        loaded = {label: value for label, value in loaded.items() if value is not None}
        names = sorted(loaded)
        for left, right in zip(names, names[1:]):
            a, b = loaded[left], loaded[right]
            same_answers = set(a) == set(b)
            worst = max(
                (abs(a[key] - b[key]) for key in set(a) & set(b)), default=0.0
            )
            agree = same_answers and all(
                math.isclose(a[key], b[key], rel_tol=rel_tol, abs_tol=abs_tol) for key in a
            )
            checks.append({
                "engine": engine, "query_id": query_id, "left": left, "right": right,
                "left_answers": len(a), "right_answers": len(b),
                "same_answer_keys": same_answers, "max_abs_difference": worst,
                "agree": agree,
            })
    return {
        "schema": PARITY_SCHEMA,
        "rule": "measured-01 probabilities, math.isclose(rel_tol=%g, abs_tol=%g)" % (rel_tol, abs_tol),
        "checked_pairs": len(checks),
        "disagreements": [check for check in checks if not check["agree"]],
        "checks": checks,
    }


def _positive(value: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("value must be positive and finite")
    return number


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    run_parser = sub.add_parser("run")
    run_parser.add_argument("--cells", required=True, type=Path, help="E1 result root")
    run_parser.add_argument("--out", required=True, type=Path)
    run_parser.add_argument("--backend", required=True, choices=("cudd", "d4", "oracle"),
                            help="cudd and d4 are formal; oracle is the pure-Python test backend")
    run_parser.add_argument("--d4", type=Path)
    run_parser.add_argument("--method", dest="methods", action="append", choices=METHODS)
    run_parser.add_argument("--complete-method-timeout", type=_positive, default=600.0)
    run_parser.add_argument("--memory-sample-interval", type=_positive, default=0.05)
    run_parser.add_argument("--probability-seed", type=int,
                            default=event_probabilities.DEFAULT_PROBABILITY_SEED)
    run_parser.add_argument("--continue-after-failure", action="store_true")
    compare_parser = sub.add_parser("compare")
    compare_parser.add_argument("--pqe", required=True, action="append", type=Path)
    compare_parser.add_argument("--json", required=True, type=Path)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "run":
            args.methods = tuple(args.methods or METHODS)
            result = run(args)
        else:
            result = compare([path.resolve() for path in args.pqe])
            _write_json(args.json, result)
            result = {key: value for key, value in result.items() if key != "checks"}
    except (OSError, ValueError, PosthocError) as error:
        parser.exit(2, "WatDiv PQE: error: %s\n" % error)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
