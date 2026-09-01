#!/usr/bin/env python3
"""Evaluate every answer of a persisted SPARQLcirc circuit with pinned d4v2.

d4v2 accepts a single Boolean root.  This runner therefore exports and
compiles one Tseitin CNF per answer root, exactly matching the per-answer d4
protocol used by the repository's Level-1 comparison.  The input circuit is
loaded once; encoding, d4 compilation, d-DNNF WMC, persistence, and RSS are
reported separately.
"""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import sys
import time
from typing import Any, Dict, Mapping, Optional, Sequence


HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import compile_portfolio
import compiler
import event_probabilities
import pqe_from_artifact as artifact
from stage_memory import StageRssSampler


SCHEMA = "sparqlcirc-artifact-d4v2-per-answer-v2"


class D4ArtifactError(RuntimeError):
    """The saved circuit cannot be evaluated under the d4 protocol."""


class D4MethodTimeout(RuntimeError):
    """The complete d4 PQE method exhausted its remaining deadline."""


def _ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000.0


def _positive_seconds(value: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0.0:
        raise argparse.ArgumentTypeError("value must be positive and finite")
    return number


def evaluate(
    source: Path,
    output: Path,
    d4_binary: Path,
    uniform_probability: Optional[float],
    method_timeout_s: float,
    context: Mapping[str, str],
    memory_sample_interval_s: float,
    probabilities: Optional[Path] = None,
    probability_seed: Optional[int] = None,
) -> Dict[str, Any]:
    if output.exists():
        raise D4ArtifactError("refusing to reuse output directory: %s" % output)
    if not source.is_file():
        raise D4ArtifactError("source artifact does not exist: %s" % source)
    if not d4_binary.is_file() or not os.access(d4_binary, os.X_OK):
        raise D4ArtifactError("d4v2 binary is missing or not executable: %s" % d4_binary)
    output.mkdir(parents=True)
    wall_started = time.perf_counter()
    method_deadline = wall_started + method_timeout_s
    sampler = StageRssSampler(interval_s=memory_sample_interval_s).start()

    sampler.set_stage("artifact_load")
    load_started = time.perf_counter()
    circuit, roots, bindings, source_metrics = artifact._load_circuit(source)
    artifact_load_ms = _ms(load_started)

    sampler.set_stage("probability_prepare")
    probability_started = time.perf_counter()
    order = tuple(compiler.deterministic_order(circuit, roots))
    try:
        weights = artifact._load_weights(
            probabilities, uniform_probability, order, probability_seed
        )
    except artifact.ArtifactPqeError as exc:
        raise D4ArtifactError(str(exc)) from exc
    probability_prepare_ms = _ms(probability_started)

    probability_partial = output / "probabilities.jsonl.partial"
    progress_path = output / "progress.jsonl"
    encode_ms = 0.0
    compile_ms = 0.0
    wmc_ms = 0.0
    ddnnf_nodes = 0
    ddnnf_edges = 0
    cnf_variables = 0
    cnf_clauses = 0
    completed = 0
    persist_started = time.perf_counter()
    with probability_partial.open("wb") as probability_handle, progress_path.open(
        "wb"
    ) as progress_handle:
        sampler.set_stage("d4_per_answer")
        for index, key in enumerate(sorted(roots), 1):
            remaining_s = method_deadline - time.perf_counter()
            if remaining_s <= 0.0:
                raise D4MethodTimeout(
                    "complete d4 method timed out after %.6gs" % method_timeout_s
                )
            try:
                result = compile_portfolio.d4_compile_once(
                    circuit,
                    roots[key],
                    weights,
                    d4bin=str(d4_binary),
                    timeout=remaining_s,
                )
            except RuntimeError as exc:
                if "d4 compilation timed out after" in str(exc):
                    raise D4MethodTimeout(
                        "complete d4 method timed out after %.6gs" % method_timeout_s
                    ) from exc
                raise
            encode_ms += float(result["encode_ms"])
            compile_ms += float(result["compile_ms"])
            wmc_ms += float(result["wmc_ms"])
            ddnnf_nodes += int(result["ddnnf_nodes"])
            ddnnf_edges += int(result["ddnnf_edges"])
            cnf_variables += int(result["cnf_vars"])
            cnf_clauses += int(result["cnf_clauses"])
            completed = index
            row = {
                **dict(context),
                "answer_key": key,
                "binding": bindings[key],
                "probability": float(result["probability"]),
            }
            probability_handle.write(
                (json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode(
                    "utf-8"
                )
            )
            if index == 1 or index % 50 == 0 or index == len(roots):
                progress = {
                    "completed_roots": index,
                    "root_count": len(roots),
                    "elapsed_ms": round(_ms(wall_started), 6),
                }
                progress_handle.write(
                    (json.dumps(progress, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
                )
                probability_handle.flush()
                progress_handle.flush()
        probability_handle.flush()
        os.fsync(probability_handle.fileno())
        progress_handle.flush()
        os.fsync(progress_handle.fileno())
    probabilities_path = output / "probabilities.jsonl"
    os.replace(probability_partial, probabilities_path)
    persist_ms = _ms(persist_started) - encode_ms - compile_ms - wmc_ms
    persist_ms = max(0.0, persist_ms)

    stage_memory = sampler.finish()
    metrics: Dict[str, Any] = {
        "schema": SCHEMA,
        "status": "ok",
        "backend": "d4v2-per-answer",
        "probability_source": (
            {
                "kind": "seeded-event",
                "seed": probability_seed,
                "scheme": event_probabilities.PROBABILITY_SCHEME,
            }
            if probability_seed is not None
            else {"kind": "file" if probabilities is not None else "uniform"}
        ),
        "source_kind": "circuit",
        "source_artifact": source.name,
        "source_artifact_bytes": source.stat().st_size,
        "context": dict(context),
        **source_metrics,
        "variable_count": len(order),
        "root_count_completed": completed,
        "method_timeout_s": float(method_timeout_s),
        "root_timeout_rule": "remaining complete-method budget",
        "artifact_load_ms": round(artifact_load_ms, 6),
        "probability_prepare_ms": round(probability_prepare_ms, 6),
        "cnf_encode_ms": round(encode_ms, 6),
        "d4_compile_ms": round(compile_ms, 6),
        "ddnnf_wmc_ms": round(wmc_ms, 6),
        "artifact_persist_ms": round(persist_ms, 6),
        "pqe_backend_ms": round(encode_ms + compile_ms + wmc_ms, 6),
        "pqe_wall_ms": round(_ms(wall_started), 6),
        "ddnnf_nodes_sum": ddnnf_nodes,
        "ddnnf_edges_sum": ddnnf_edges,
        "cnf_variables_sum": cnf_variables,
        "cnf_clauses_sum": cnf_clauses,
        "probability_jsonl_bytes": probabilities_path.stat().st_size,
        "stage_peak_memory": stage_memory,
        "process_peak_rss_bytes": artifact._peak_rss_bytes(),
    }
    artifact._atomic_json(output / "metrics.json", metrics)
    return metrics


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--d4", required=True, type=Path)
    probability = parser.add_mutually_exclusive_group(required=True)
    probability.add_argument("--probabilities", type=Path)
    probability.add_argument("--uniform-probability", type=float)
    probability.add_argument("--probability-seed", type=int)
    parser.add_argument("--method-timeout", type=_positive_seconds, required=True)
    parser.add_argument("--memory-sample-interval", type=_positive_seconds, default=0.05)
    parser.add_argument("--query-id")
    parser.add_argument("--run-id")
    parser.add_argument("--method")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    context = {
        key: value
        for key, value in (
            ("query_id", args.query_id),
            ("run_id", args.run_id),
            ("method", args.method),
        )
        if value is not None
    }
    os.environ["D4V2"] = "1"
    metrics = evaluate(
        args.input.resolve(),
        args.out.resolve(),
        args.d4.resolve(),
        args.uniform_probability,
        args.method_timeout,
        context,
        args.memory_sample_interval,
        probabilities=(
            args.probabilities.resolve() if args.probabilities is not None else None
        ),
        probability_seed=args.probability_seed,
    )
    print(json.dumps(metrics, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except D4MethodTimeout as exc:
        print("d4_from_artifact: timeout: %s" % exc, file=sys.stderr)
        raise SystemExit(124)
    except (D4ArtifactError, OSError, RuntimeError, ValueError) as exc:
        print("d4_from_artifact: error: %s" % exc, file=sys.stderr)
        raise SystemExit(1)
