#!/usr/bin/env python3
"""Run one Wikidata N-paired or streaming C cell under a fixed protocol."""

from __future__ import annotations

import argparse
import importlib
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import time
import statistics
from typing import Any, Mapping, Optional


SCHEMA = "wikidata-method-cell-v1"
RUN_SCHEMA = "wikidata-method-run-v1"
N_METHODS = ("N-per-answer", "N-shared")
DEFAULT_TOKEN_REGEX = r"^urn:wdbench:statement:[1-9][0-9]*$"


class CellError(RuntimeError):
    """The physical cell could not be run safely."""


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False, newline="\n"
    ) as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
        temporary = Path(handle.name)
    os.replace(temporary, path)


def load_core(source_root: Path):
    reference = source_root / "reference"
    paper = reference / "paper"
    runner = paper / "watdiv10m_runner.py"
    if not runner.is_file():
        raise CellError("core runner is missing: %s" % runner)
    sys.path.insert(0, str(reference))
    sys.path.insert(0, str(paper))
    module = importlib.import_module("watdiv10m_runner")
    if Path(module.__file__).resolve() != runner.resolve():
        raise CellError("loaded the wrong watdiv10m_runner module")
    return module


def endpoint_wall_ms(endpoint: Mapping[str, Any]) -> float:
    outer = endpoint.get("outer_process")
    if isinstance(outer, Mapping):
        for key in ("parent_observed_wall_ms", "wall_ms"):
            value = outer.get(key)
            if value is not None:
                return float(value)
    metrics = endpoint.get("endpoint")
    if isinstance(metrics, Mapping) and metrics.get("endpoint_e2e_ms") is not None:
        return float(metrics["endpoint_e2e_ms"])
    if endpoint.get("worker_wall_ms") is not None:
        return float(endpoint["worker_wall_ms"])
    return 0.0


def offline_wall_ms(offline: Mapping[str, Any]) -> float:
    process = offline.get("process")
    if isinstance(process, Mapping):
        for key in ("parent_observed_wall_ms", "wall_ms"):
            value = process.get(key)
            if value is not None:
                return float(value)
    metrics = offline.get("metrics")
    if isinstance(metrics, Mapping) and metrics.get("offline_wall_ms") is not None:
        return float(metrics["offline_wall_ms"])
    return 0.0


def same_file(left: Path, right: Path) -> bool:
    if not left.is_file() or not right.is_file() or left.stat().st_size != right.stat().st_size:
        return False
    with left.open("rb") as first, right.open("rb") as second:
        while True:
            a = first.read(1024 * 1024)
            b = second.read(1024 * 1024)
            if a != b:
                return False
            if not a:
                return True


def copy_query(query: Path, output: Path) -> Path:
    output.mkdir(parents=True)
    target = output / "query.sparql"
    with target.open("xb") as handle:
        handle.write(query.read_bytes())
        handle.flush()
        os.fsync(handle.fileno())
    return target.resolve()


def base_config(args: argparse.Namespace, method: str, query: Path) -> dict[str, Any]:
    config = {
        "schema": "watdiv-brnc-cell-v1",
        "query": str(query),
        "query_id": args.query_id,
        "engine": "graphdb-10.7.6",
        "engine_pid": args.engine_pid,
        "method": method,
        "scheme": "SPARQL_Star",
        "base_endpoint": args.mixed_endpoint,
        "reified_endpoint": args.mixed_endpoint,
        "update_endpoint": args.update_endpoint,
        "c_endpoint_protocol": "rdf4j",
        "jar": str(args.jar.resolve()),
        "java": str(args.java),
        "java_max_heap": args.java_max_heap,
        "c_max_union_branches": args.c_max_union_branches,
        "graphdb_heap_initial": args.graphdb_heap_initial,
        "graphdb_heap_max": args.graphdb_heap_max,
        "graphdb_concat_max_length": args.graphdb_concat_max_length,
        "reified_data": str(args.reified_data.resolve()),
        "warmups": args.warmups,
        "runs": args.runs,
        "primary_statistic": "median",
        "endpoint_timeout_s": float(args.timeout),
        "offline_timeout_s": float(args.timeout),
        "exact_response_row_limit": args.exact_response_row_limit,
        "pqe_backend": "none",
        "npcs_postprocess_mode": "shared",
        "probabilities": None,
        "uniform_probability": None,
        "probability_seed": None,
        "token_regex": args.token_regex,
        "c_parallelism": 1,
        "memory_sample_interval_s": args.memory_sample_interval,
        "jvm_heap_sample_interval_ms": 100,
        "c_read_only": False,
        "skip_bnode_check": True,
    }
    if method == "N":
        config["response_mode"] = "stream-tsv"
    return config


def run_endpoint(core, config: dict[str, Any], run_dir: Path) -> dict[str, Any]:
    config_path = run_dir / "endpoint-config.json"
    atomic_json(config_path, config)
    return core._run_endpoint_worker(config, config_path, run_dir)


def run_offline_with_budget(
    core,
    config: dict[str, Any],
    source_run_dir: Path,
    artifact_run_dir: Path,
    run_id: str,
    remaining_s: float,
) -> dict[str, Any]:
    if remaining_s <= 0:
        return {
            "schema": "sparqlcirc-offline-run-v3",
            "status": "offline-timeout",
            "offline_timeout_s": 0.0,
            "detail": "the endpoint exhausted the complete-method deadline",
        }
    branch_config = dict(config)
    branch_config["offline_timeout_s"] = remaining_s
    artifact_run_dir.mkdir()
    return core._run_offline(
        branch_config, source_run_dir, run_id, artifact_run_dir
    )


def logical_status(endpoint: Mapping[str, Any], offline: Optional[Mapping[str, Any]]) -> str:
    if endpoint.get("status") != "ok":
        return str(endpoint.get("status") or "endpoint-error")
    if offline is None:
        return "offline-missing"
    return str(offline.get("status") or "offline-error")


def measured_answer_path(method: str, physical: Path) -> Optional[Path]:
    if method in N_METHODS:
        candidate = physical / "measured-01" / method / "pp" / "answer-records.jsonl"
    else:
        candidate = physical / "measured-01" / "offline" / "answer-records.jsonl"
    return candidate if candidate.is_file() else None


def n_branch_order(query_id: str, run_id: str) -> tuple[str, str]:
    parity = sum(ord(character) for character in query_id + run_id)
    return N_METHODS if parity % 2 == 0 else tuple(reversed(N_METHODS))


def summarize_method(
    runs: list[dict[str, Any]], warmups: int, measured_runs: int
) -> dict[str, Any]:
    expected = {
        "%s-%02d" % (phase, index)
        for phase, count in (("warmup", warmups), ("measured", measured_runs))
        for index in range(1, count + 1)
    }
    successful = [item for item in runs if item.get("status") == "ok"]
    measured = [item for item in successful if item.get("phase") == "measured"]
    complete = {str(item.get("run_id")) for item in successful} == expected
    values = [float(item["component_e2e_ms"]) for item in measured]
    return {
        "status": "ok" if complete else "incomplete",
        "successful_executions": len(successful),
        "expected_executions": warmups + measured_runs,
        "measured_successes": len(measured),
        "component_e2e_ms": values,
        "median_component_e2e_ms": statistics.median(values) if values else None,
        "runs": runs,
    }


def run_n_pair(args: argparse.Namespace, core) -> dict[str, Any]:
    output = args.out.resolve()
    if output.exists():
        raise CellError("refusing to reuse output: %s" % output)
    query = copy_query(args.query.resolve(), output)
    config = base_config(args, "N", query)
    core._validate_config(config)
    method_runs: dict[str, list[dict[str, Any]]] = {method: [] for method in N_METHODS}
    active = set(N_METHODS)
    common_runs: list[dict[str, Any]] = []
    recovery_required = False

    executions = [
        (phase, index)
        for phase, count in (("warmup", args.warmups), ("measured", args.runs))
        for index in range(1, count + 1)
    ]
    for phase, index in executions:
        if not active:
            break
        run_id = "%s-%02d" % (phase, index)
        run_root = output / run_id
        endpoint_dir = run_root / "endpoint"
        endpoint_dir.mkdir(parents=True)
        started = time.monotonic()
        endpoint = run_endpoint(core, config, endpoint_dir)
        endpoint_elapsed_ms = (time.monotonic() - started) * 1000.0
        endpoint_observed_ms = max(endpoint_elapsed_ms, endpoint_wall_ms(endpoint))
        common_record: dict[str, Any] = {
            "schema": RUN_SCHEMA,
            "run_id": run_id,
            "phase": phase,
            "endpoint": endpoint,
            "endpoint_observed_wall_ms": round(endpoint_observed_ms, 3),
            "logical_deadline_s": args.timeout,
            "branch_order": list(n_branch_order(args.query_id, run_id)),
            "branches": {},
        }
        if endpoint.get("status") != "ok":
            recovery_required = (
                recovery_required or endpoint.get("recovery_required") is True
            )
            for method in tuple(active):
                record = {
                    "run_id": run_id,
                    "phase": phase,
                    "status": str(endpoint.get("status") or "endpoint-error"),
                    "endpoint_observed_wall_ms": round(endpoint_observed_ms, 3),
                    "offline": None,
                    "component_e2e_ms": None,
                    "shared_endpoint": True,
                }
                method_runs[method].append(record)
                common_record["branches"][method] = record
            active.clear()
            atomic_json(run_root / "run.json", common_record)
            common_runs.append(common_record)
            break

        logical_remaining_s = max(0.0, args.timeout - endpoint_observed_ms / 1000.0)
        failed_this_phase: set[str] = set()
        for method in n_branch_order(args.query_id, run_id):
            if method not in active:
                continue
            branch_dir = run_root / method
            branch_config = dict(config)
            branch_config["npcs_postprocess_mode"] = (
                "per-answer" if method == "N-per-answer" else "shared"
            )
            offline = run_offline_with_budget(
                core,
                branch_config,
                endpoint_dir,
                branch_dir,
                "%s-%s" % (run_id, method),
                logical_remaining_s,
            )
            e2e_ms = endpoint_observed_ms + offline_wall_ms(offline)
            status = logical_status(endpoint, offline)
            if status == "ok" and e2e_ms > args.timeout * 1000.0 + 100.0:
                status = "deadline-violation"
            record = {
                "run_id": run_id,
                "phase": phase,
                "status": status,
                "endpoint_observed_wall_ms": round(endpoint_observed_ms, 3),
                "offline_budget_s": round(logical_remaining_s, 6),
                "offline": offline,
                "component_e2e_ms": round(e2e_ms, 3),
                "shared_endpoint": True,
                "artifact": str(branch_dir.relative_to(output)),
            }
            method_runs[method].append(record)
            common_record["branches"][method] = record
            if status != "ok":
                failed_this_phase.add(method)
        if phase == "warmup":
            active.difference_update(failed_this_phase)
        atomic_json(run_root / "run.json", common_record)
        common_runs.append(common_record)

    methods = {
        method: summarize_method(method_runs[method], args.warmups, args.runs)
        for method in N_METHODS
    }
    answer_parity: Optional[bool] = None
    left = measured_answer_path("N-per-answer", output)
    right = measured_answer_path("N-shared", output)
    if left is not None and right is not None:
        answer_parity = same_file(left, right)
        if not answer_parity:
            methods["N-per-answer"]["status"] = "answer-mismatch"
            methods["N-shared"]["status"] = "answer-mismatch"
    result = {
        "schema": SCHEMA,
        "physical_method": "N-paired",
        "n_result_handling": "stream-tsv",
        "query_id": args.query_id,
        "query": str(query),
        "status": "ok" if all(item["status"] == "ok" for item in methods.values()) else "recorded-failure",
        "terminal": True,
        "recovery_required": recovery_required,
        "protocol": {
            "warmups": args.warmups,
            "measured_runs": args.runs,
            "primary_statistic": "median",
            "complete_method_deadline_s_per_execution": args.timeout,
            "endpoint_reuse": "one immutable NPCS response per query/phase for both PP branches",
            "pp_scheduling": "sequential with alternating branch order; waiting for the sibling branch is outside each logical method time",
            "pqe_in_timed_scope": False,
            "response_mode": "stream-tsv",
            "graphdb_heap_initial": args.graphdb_heap_initial,
            "graphdb_heap_max": args.graphdb_heap_max,
            "graphdb_concat_max_length": args.graphdb_concat_max_length,
        },
        "methods": methods,
        "n_answer_records_equal": answer_parity,
        "common_runs": common_runs,
        "artifact_bytes_before_cell_record": core._directory_bytes(output),
    }
    atomic_json(output / "cell.json", result)
    return result


def c_run_record(
    args: argparse.Namespace,
    core,
    config: dict[str, Any],
    output: Path,
    phase: str,
    index: int,
) -> dict[str, Any]:
    run_id = "%s-%02d" % (phase, index)
    run_dir = output / run_id
    run_dir.mkdir()
    started = time.monotonic()
    endpoint = run_endpoint(core, config, run_dir)
    endpoint_elapsed_ms = (time.monotonic() - started) * 1000.0
    endpoint_observed_ms = max(endpoint_elapsed_ms, endpoint_wall_ms(endpoint))
    offline: Optional[dict[str, Any]] = None
    if endpoint.get("status") == "ok":
        remaining_s = max(0.0, args.timeout - endpoint_observed_ms / 1000.0)
        offline_config = dict(config)
        offline_config["offline_timeout_s"] = remaining_s
        if remaining_s > 0:
            offline = core._run_offline(offline_config, run_dir, run_id)
        else:
            offline = {
                "schema": "sparqlcirc-offline-run-v3",
                "status": "offline-timeout",
                "offline_timeout_s": 0.0,
                "detail": "the endpoint exhausted the complete-method deadline",
            }
    status = logical_status(endpoint, offline)
    e2e_ms = endpoint_observed_ms + (offline_wall_ms(offline) if offline else 0.0)
    if status == "ok" and e2e_ms > args.timeout * 1000.0 + 100.0:
        status = "deadline-violation"
    endpoint_metrics = endpoint.get("endpoint")
    modes = {}
    if isinstance(endpoint_metrics, Mapping):
        modes = {
            "requested_mode": endpoint_metrics.get("requested_mode"),
            "effective_mode": endpoint_metrics.get("effective_mode"),
            "fallback_reason": endpoint_metrics.get("fallback_reason"),
        }
    record = {
        "schema": RUN_SCHEMA,
        "run_id": run_id,
        "phase": phase,
        "status": status,
        "endpoint": endpoint,
        "offline": offline,
        "endpoint_observed_wall_ms": round(endpoint_observed_ms, 3),
        "component_e2e_ms": round(e2e_ms, 3),
        **modes,
    }
    atomic_json(run_dir / "run.json", record)
    return record


def run_c(args: argparse.Namespace, core) -> dict[str, Any]:
    output = args.out.resolve()
    if output.exists():
        raise CellError("refusing to reuse output: %s" % output)
    query = copy_query(args.query.resolve(), output)
    config = base_config(args, args.method, query)
    core._validate_config(config)
    runs: list[dict[str, Any]] = []
    for index in range(1, args.warmups + 1):
        warmup = c_run_record(args, core, config, output, "warmup", index)
        runs.append(warmup)
        if warmup["status"] != "ok":
            break
    if len(runs) == args.warmups and all(item["status"] == "ok" for item in runs):
        for index in range(1, args.runs + 1):
            runs.append(c_run_record(args, core, config, output, "measured", index))
            if runs[-1]["endpoint"].get("status") != "ok":
                break
    complete = (
        len(runs) == args.warmups + args.runs
        and all(item["status"] == "ok" for item in runs)
    )
    measured = [item for item in runs if item["phase"] == "measured" and item["status"] == "ok"]
    recovery_required = any(
        isinstance(item.get("endpoint"), Mapping)
        and item["endpoint"].get("recovery_required") is True
        for item in runs
    )
    result = {
        "schema": SCHEMA,
        "physical_method": args.method,
        "display_method": "%s (streaming)" % args.method,
        "c_result_handling": "streaming",
        "query_id": args.query_id,
        "query": str(query),
        "status": "ok" if complete else "recorded-failure",
        "terminal": True,
        "recovery_required": recovery_required,
        "protocol": {
            "warmups": args.warmups,
            "measured_runs": args.runs,
            "primary_statistic": "median",
            "complete_method_deadline_s_per_execution": args.timeout,
            "pqe_in_timed_scope": False,
            "c_parallelism": 1,
            "java_max_heap": args.java_max_heap,
            "c_max_union_branches": args.c_max_union_branches,
            "graphdb_heap_initial": args.graphdb_heap_initial,
            "graphdb_heap_max": args.graphdb_heap_max,
            "graphdb_concat_max_length": args.graphdb_concat_max_length,
            "c_result_handling": "streaming",
            "c_result_protocol": "CONSTRUCT/RDF",
            "jvm_heap_peak_measurement": (
                "CircuitRun MemoryMXBean sampled every 100 ms without requesting GC; "
                "GraphDB internal heap peak is not collected"
            ),
            "endpoint_e2e_scope": (
                "CircuitRun startup, plan and CONSTRUCT execution, streamed RDF statement "
                "classification, normalization, and circuit serialization/persistence"
            ),
            "offline_scope": "circuit decode, validation, structure metrics, and answer-record persistence",
        },
        "runs": runs,
        "summary": {
            "measured_successes": len(measured),
            "component_e2e_ms": [item["component_e2e_ms"] for item in measured],
            "median_component_e2e_ms": (
                statistics.median(item["component_e2e_ms"] for item in measured)
                if measured else None
            ),
        },
        "artifact_bytes_before_cell_record": core._directory_bytes(output),
    }
    atomic_json(output / "cell.json", result)
    return result


def positive_float(value: str) -> float:
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise argparse.ArgumentTypeError("value must be positive and finite")
    return result


def nonnegative_integer(value: str) -> int:
    result = int(value)
    if result < 0:
        raise argparse.ArgumentTypeError("value must be non-negative")
    return result


def positive_integer(value: str) -> int:
    result = int(value)
    if result < 1:
        raise argparse.ArgumentTypeError("value must be positive")
    return result


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("command", choices=("run-n-pair", "run-c"))
    result.add_argument("--source-root", required=True, type=Path)
    result.add_argument("--query", required=True, type=Path)
    result.add_argument("--query-id", required=True)
    result.add_argument("--out", required=True, type=Path)
    result.add_argument("--engine-pid", required=True, type=int)
    result.add_argument("--mixed-endpoint", required=True)
    result.add_argument("--update-endpoint", required=True)
    result.add_argument("--jar", required=True, type=Path)
    result.add_argument("--java", required=True)
    result.add_argument("--java-max-heap", default="96g")
    result.add_argument("--c-max-union-branches", type=int, default=256)
    result.add_argument("--graphdb-heap-initial", default="128g")
    result.add_argument("--graphdb-heap-max", default="128g")
    result.add_argument("--graphdb-concat-max-length", type=int, default=10_485_760)
    result.add_argument("--reified-data", required=True, type=Path)
    result.add_argument("--timeout", type=positive_float, default=600.0)
    result.add_argument("--warmups", type=nonnegative_integer, default=1)
    result.add_argument("--runs", type=positive_integer, default=5)
    result.add_argument("--memory-sample-interval", type=positive_float, default=0.05)
    result.add_argument("--exact-response-row-limit", type=int, default=1_000_000)
    result.add_argument("--token-regex", default=DEFAULT_TOKEN_REGEX)
    result.add_argument("--method", choices=("C-flat", "C-factorised", "C-path"))
    return result


def main() -> int:
    args = parser().parse_args()
    if args.warmups != 1 or args.runs != 5:
        raise SystemExit("the formal protocol is exactly one warm-up and five measured runs")
    if args.c_max_union_branches < 1:
        raise SystemExit("--c-max-union-branches must be positive")
    if args.command == "run-c" and args.method is None:
        raise SystemExit("run-c requires --method")
    if args.command == "run-n-pair" and args.method is not None:
        raise SystemExit("run-n-pair does not accept --method")
    core = load_core(args.source_root.resolve())
    result = run_n_pair(args, core) if args.command == "run-n-pair" else run_c(args, core)
    print(json.dumps({
        "status": result["status"],
        "query_id": result["query_id"],
        "physical_method": result["physical_method"],
        "out": str(args.out.resolve()),
    }, ensure_ascii=False, sort_keys=True))
    return 20 if result.get("recovery_required") else 0


if __name__ == "__main__":
    raise SystemExit(main())
