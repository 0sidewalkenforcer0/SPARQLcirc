#!/usr/bin/env python3
"""Run CUDD or per-answer d4 PQE over one immutable C construction cell or NPCS cell.

A C source cell supplies one warm-up circuit and five measured circuits.  Each
PQE execution receives the part of the complete-method deadline that remains
after that source run's endpoint construction time.

An NPCS source cell (``run_cell.py run-n-pair``) supplies, per execution, the
query-global DAG that the ``N-shared`` post-processing branch merged from the
endpoint's provenance strings (``<run>/N-shared/pp/npcs-hc-dag.json``).  Its
PQE receives what remains of the same complete-method deadline after the
endpoint request and that post-processing, so N and C share one budget rule.
The stages are reported as endpoint, parse+merge (N only), artifact load,
compilation and WMC.

Results are written to a new output root; neither the source cell nor a prior
PQE attempt is modified.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import os
from pathlib import Path
import statistics
import subprocess
import sys
import tempfile
import time
from typing import Any, Mapping, Optional, Sequence


REFERENCE = Path(__file__).resolve().parents[1]
if str(REFERENCE) not in sys.path:
    sys.path.insert(0, str(REFERENCE))

import event_probabilities


SCHEMA = "wikidata-circuit-pqe-cell-v1"
RUN_SCHEMA = "wikidata-circuit-pqe-run-v1"
SOURCE_SCHEMA = "wikidata-method-cell-v1"
RUN_IDS = ("warmup-01",) + tuple("measured-%02d" % index for index in range(1, 6))
C_METHODS = ("C-flat", "C-factorised", "C-path")
NPCS_METHOD = "N-shared"


class PqeRunError(RuntimeError):
    """The source cell or requested PQE protocol is invalid."""


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def atomic_json(path: Path, value: Any) -> None:
    if path.exists():
        raise PqeRunError("refusing to overwrite %s" % path)
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


def load_runner(source_root: Path):
    path = source_root / "reference" / "paper" / "watdiv10m_runner.py"
    spec = importlib.util.spec_from_file_location("wikidata_pqe_core", path)
    if spec is None or spec.loader is None:
        raise PqeRunError("cannot load %s" % path)
    module = importlib.util.module_from_spec(spec)
    sys.path[:0] = [str(source_root / "reference"), str(path.parent)]
    spec.loader.exec_module(module)
    return module


def positive_seconds(value: str) -> float:
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise argparse.ArgumentTypeError("value must be positive and finite")
    return result


def source_method(cell: Mapping[str, Any]) -> str:
    """The logical method whose artifacts this PQE cell evaluates."""
    physical = str(cell.get("physical_method"))
    return NPCS_METHOD if physical == "N-paired" else physical


def source_run(cell: Mapping[str, Any], run_id: str) -> Optional[Mapping[str, Any]]:
    if cell.get("physical_method") == "N-paired":
        methods = cell.get("methods")
        branch = methods.get(NPCS_METHOD) if isinstance(methods, Mapping) else None
        runs = branch.get("runs", []) if isinstance(branch, Mapping) else []
    else:
        runs = cell.get("runs", [])
    for run in runs:
        if isinstance(run, Mapping) and run.get("run_id") == run_id:
            return run
    return None


def endpoint_construction_ms(run: Mapping[str, Any]) -> Optional[float]:
    value = run.get("endpoint_observed_wall_ms")
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return float(value)
    endpoint = run.get("endpoint")
    metrics = endpoint.get("endpoint") if isinstance(endpoint, Mapping) else None
    value = metrics.get("endpoint_e2e_ms") if isinstance(metrics, Mapping) else None
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return float(value)
    return None


def stage_split(backend: str, metrics: Mapping[str, Any]) -> tuple[float, float]:
    """Compilation and WMC separately (the d4 compilation includes CNF export)."""
    if backend in ("cudd", "oracle"):
        return (float(metrics.get("compile_wall_ms") or 0.0),
                float(metrics.get("wmc_wall_ms") or 0.0))
    return (
        float(metrics.get("cnf_encode_ms") or 0.0) + float(metrics.get("d4_compile_ms") or 0.0),
        float(metrics.get("ddnnf_wmc_ms") or 0.0),
    )


def stage_values(backend: str, metrics: Mapping[str, Any]) -> tuple[float, float]:
    if backend in ("cudd", "oracle"):
        parsing = sum(
            float(metrics.get(field) or 0.0)
            for field in ("artifact_load_ms", "probability_load_ms", "artifact_persist_ms")
        )
        compilation = sum(
            float(metrics.get(field) or 0.0)
            for field in ("compile_wall_ms", "wmc_wall_ms")
        )
        return parsing, compilation
    parsing = sum(
        float(metrics.get(field) or 0.0)
        for field in ("artifact_load_ms", "probability_prepare_ms", "artifact_persist_ms")
    )
    compilation = sum(
        float(metrics.get(field) or 0.0)
        for field in ("cnf_encode_ms", "d4_compile_ms", "ddnnf_wmc_ms")
    )
    return parsing, compilation


def run_one(
    args: argparse.Namespace,
    core: Any,
    source_cell: Mapping[str, Any],
    run_id: str,
) -> dict[str, Any]:
    phase = "warmup" if run_id.startswith("warmup-") else "measured"
    method = source_method(source_cell)
    npcs = method == NPCS_METHOD
    source = source_run(source_cell, run_id)
    source_dir = args.cell.parent / run_id
    base: dict[str, Any] = {
        "schema": RUN_SCHEMA,
        "run_id": run_id,
        "phase": phase,
        "backend": args.backend,
        "source_run": str(source_dir),
        "complete_method_timeout_s": args.complete_method_timeout,
    }
    if source is None:
        return {**base, "status": "source-missing", "failure_class": "SOURCE_MISSING"}
    if npcs:
        if source.get("status") != "ok":
            return {**base, "status": "source-incomplete", "failure_class": "SOURCE_INCOMPLETE"}
        artifact = args.cell.parent / str(source.get("artifact") or "") / "pp" / "npcs-hc-dag.json"
        endpoint_ms = source.get("endpoint_observed_wall_ms")
        prior_ms = source.get("component_e2e_ms")
        if not isinstance(endpoint_ms, (int, float)) or not isinstance(prior_ms, (int, float)) \
                or not artifact.is_file():
            return {**base, "status": "source-incomplete", "failure_class": "SOURCE_INCOMPLETE"}
        offline = source.get("offline") if isinstance(source.get("offline"), Mapping) else {}
        pp_metrics = offline.get("metrics") if isinstance(offline.get("metrics"), Mapping) else {}
        base.update(
            endpoint_request_ms=endpoint_request_ms(source_cell, run_id),
            endpoint_ms=round(float(endpoint_ms), 6),
            parse_merge_ms=round(float(prior_ms) - float(endpoint_ms), 6),
            parse_merge_inner_ms=pp_metrics.get("pp_hc_build_wall_ms"),
            source_artifact=str(artifact),
            source_artifact_bytes=artifact.stat().st_size,
        )
        kind = "npcs-shared"
    else:
        endpoint = source.get("endpoint")
        if not isinstance(endpoint, Mapping) or endpoint.get("status") != "ok":
            return {**base, "status": "source-incomplete", "failure_class": "SOURCE_INCOMPLETE"}
        construction_ms = endpoint_construction_ms(source)
        artifact = source_dir / "circuit.nt"
        if construction_ms is None or not artifact.is_file():
            return {**base, "status": "source-incomplete", "failure_class": "SOURCE_INCOMPLETE"}
        prior_ms = construction_ms
        base.update(
            circuit_construction_ms=round(construction_ms, 6),
            source_circuit=str(artifact),
            source_circuit_bytes=artifact.stat().st_size,
        )
        kind = "circuit"
    remaining = args.complete_method_timeout - float(prior_ms) / 1000.0
    base["remaining_pqe_budget_s"] = round(max(0.0, remaining), 6)
    if remaining <= 0:
        return {**base, "status": "timeout", "failure_class": "TO"}

    target = args.out / run_id
    target.mkdir(parents=True)
    pqe_output = target / "pqe"
    context = (
        "--query-id", str(source_cell["query_id"]),
        "--run-id", run_id,
        "--method", method,
    )
    if args.backend in ("cudd", "oracle"):
        command = [
            str(args.python),
            str(args.source_root / "reference" / "pqe_from_artifact.py"),
            "--kind", kind,
            "--input", str(artifact),
            "--out", str(pqe_output),
            "--backend", args.backend,
            "--probability-seed", str(args.probability_seed),
            "--memory-sample-interval", str(args.memory_sample_interval),
            *context,
        ]
    else:
        command = [
            str(args.python),
            str(args.source_root / "reference" / "d4_from_artifact.py"),
            "--kind", kind,
            "--input", str(artifact),
            "--out", str(pqe_output),
            "--d4", str(args.d4),
            "--probability-seed", str(args.probability_seed),
            "--method-timeout", str(remaining),
            "--memory-sample-interval", str(args.memory_sample_interval),
            *context,
        ]
    started = time.perf_counter()
    process = core._run_child(
        command,
        target / "stdout.log",
        target / "stderr.log",
        remaining,
        args.memory_sample_interval,
    )
    observed_ms = (time.perf_counter() - started) * 1000.0
    base["process"] = process
    base["pqe_process_observed_wall_ms"] = round(observed_ms, 6)
    if process.get("timed_out") or process.get("returncode") == 124:
        return {**base, "status": "timeout", "failure_class": "TO"}
    if process.get("returncode") != 0:
        return {**base, "status": "error", "failure_class": "PQE_ERROR"}
    metrics_path = pqe_output / "metrics.json"
    if not metrics_path.is_file():
        return {**base, "status": "error", "failure_class": "PQE_PROTOCOL_ERROR"}
    metrics = read_json(metrics_path)
    parsing_ms, compilation_ms = stage_values(args.backend, metrics)
    compile_ms, wmc_ms = stage_split(args.backend, metrics)
    total_ms = float(prior_ms) + parsing_ms + compilation_ms
    return {
        **base,
        "status": "ok",
        "failure_class": None,
        "metrics": metrics,
        "circuit_parsing_ms": round(parsing_ms, 6),
        "compilation_and_wmc_ms": round(compilation_ms, 6),
        "compile_ms": round(compile_ms, 6),
        "wmc_ms": round(wmc_ms, 6),
        "full_pipeline_ms": round(total_ms, 6),
    }


def endpoint_request_ms(cell: Mapping[str, Any], run_id: str) -> Optional[float]:
    """The worker's own endpoint figure: endpoint_only_ms, else the legacy window."""
    for run in cell.get("common_runs", []):
        if isinstance(run, Mapping) and run.get("run_id") == run_id:
            endpoint = run.get("endpoint")
            metrics = endpoint.get("endpoint") if isinstance(endpoint, Mapping) else None
            if isinstance(metrics, Mapping):
                for key in ("endpoint_only_ms", "endpoint_e2e_ms"):
                    if isinstance(metrics.get(key), (int, float)):
                        return round(float(metrics[key]), 6)
    return None


def median(records: Sequence[Mapping[str, Any]], field: str) -> Optional[float]:
    values = [float(record[field]) for record in records if record.get(field) is not None]
    return round(float(statistics.median(values)), 6) if len(values) == 5 else None


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--cell", required=True, type=Path, help="C construction cell.json")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument(
        "--backend",
        required=True,
        choices=("cudd", "d4", "oracle"),
        help="cudd and d4 are the formal backends; oracle is the pure-Python test backend",
    )
    parser.add_argument("--d4", type=Path)
    parser.add_argument("--python", type=Path, default=Path(sys.executable))
    parser.add_argument(
        "--probability-seed",
        type=int,
        default=event_probabilities.DEFAULT_PROBABILITY_SEED,
    )
    parser.add_argument("--complete-method-timeout", type=positive_seconds, default=600.0)
    parser.add_argument("--memory-sample-interval", type=positive_seconds, default=0.05)
    args = parser.parse_args(argv)
    args.source_root = args.source_root.resolve()
    args.cell = args.cell.resolve()
    args.out = args.out.resolve()
    args.python = args.python.resolve()
    if args.d4 is not None:
        args.d4 = args.d4.resolve()
    if args.backend == "d4" and args.d4 is None:
        parser.error("--backend d4 requires --d4")
    if args.probability_seed < 0:
        parser.error("--probability-seed must be non-negative")
    if args.out.exists():
        raise PqeRunError("refusing to reuse output: %s" % args.out)
    cell = read_json(args.cell)
    if not isinstance(cell, Mapping) or cell.get("schema") != SOURCE_SCHEMA:
        raise PqeRunError("unsupported construction cell")
    if cell.get("physical_method") not in C_METHODS + ("N-paired",):
        raise PqeRunError("PQE source must be a C construction cell or an N-paired cell")
    protocol = cell.get("protocol")
    if not isinstance(protocol, Mapping) or (
        protocol.get("warmups"), protocol.get("measured_runs"), protocol.get("primary_statistic")
    ) != (1, 5, "median"):
        raise PqeRunError("source cell does not use the formal 1+5 median protocol")
    args.out.mkdir(parents=True)
    core = load_runner(args.source_root)
    records = []
    for run_id in RUN_IDS:
        record = run_one(args, core, cell, run_id)
        records.append(record)
        atomic_json(args.out / run_id / "run.json", record)
        if run_id == "warmup-01" and record["status"] != "ok":
            break
    measured = [record for record in records if record["phase"] == "measured"]
    successful = [record for record in measured if record["status"] == "ok"]
    complete = len(measured) == 5 and len(successful) == 5
    stage_fields = (
        ("endpoint_request_ms", "endpoint_ms", "parse_merge_ms", "circuit_parsing_ms",
         "compile_ms", "wmc_ms", "compilation_and_wmc_ms", "full_pipeline_ms")
        if source_method(cell) == NPCS_METHOD
        else ("circuit_construction_ms", "circuit_parsing_ms", "compile_ms", "wmc_ms",
              "compilation_and_wmc_ms", "full_pipeline_ms")
    )
    result = {
        "schema": SCHEMA,
        "query_id": cell["query_id"],
        "method": source_method(cell),
        "backend": args.backend,
        "source_cell": str(args.cell),
        "protocol": {
            "warmups": 1,
            "measured_runs": 5,
            "primary_statistic": "median",
            "complete_method_timeout_s_per_execution": args.complete_method_timeout,
            "probability_seed": args.probability_seed,
            "probability_scheme": event_probabilities.PROBABILITY_SCHEME,
            "d4_granularity": "one d4 invocation per answer" if args.backend == "d4" else None,
        },
        "status": "ok" if complete else "recorded-failure",
        "measured_successes": len(successful),
        "stage_medians_ms": {field: median(successful, field) for field in stage_fields},
        "stage_scope": (
            "N-shared: endpoint_ms is the endpoint worker process as observed by run_cell.py "
            "(budget basis, includes interpreter start and the rewrite); endpoint_request_ms "
            "is the worker's own endpoint_only_ms (endpoint-only protocol) or endpoint_e2e_ms; "
            "parse_merge_ms is the N-shared post-processing process "
            "(parse, Boolean normalization, query-global hash-consing, DAG persistence); "
            "circuit_parsing_ms is the PQE process's DAG load, probability preparation and "
            "result persistence; compile_ms/wmc_ms split compilation_and_wmc_ms"
            if source_method(cell) == NPCS_METHOD else
            "C: circuit_construction_ms is the endpoint construction; circuit_parsing_ms is "
            "the PQE process's circuit load, probability preparation and result persistence"
        ),
        "runs": records,
    }
    atomic_json(args.out / "cell.json", result)
    print(json.dumps({"status": result["status"], "out": str(args.out)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
