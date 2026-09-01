#!/usr/bin/env python3
"""Compile and evaluate a persisted provenance artifact in an isolated stage.

The construction runners deliberately stop before PQE when this command is
used.  It accepts either an NPCS shared hash-consed DAG or a SPARQLcirc circuit
file, loads the immutable artifact, compiles every answer root in one manager,
and writes the probabilities and stage metrics into a new directory.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
import math
import os
from pathlib import Path
import sys
import time
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import circuit_io
import compiler
import event_probabilities
from stage_memory import StageRssSampler


SCHEMA = "sparqlcirc-artifact-pqe-v2"
NPCS_DAG_SCHEMA = "npcs-pp-hc-dag-v2"
DEFAULT_MEMORY_SAMPLE_INTERVAL_S = 0.05


class ArtifactPqeError(RuntimeError):
    """A saved artifact cannot be evaluated under the requested protocol."""


def _milliseconds(started: float) -> float:
    return (time.perf_counter() - started) * 1000.0


def _round_ms(value: float) -> float:
    return round(float(value), 6)


def _positive_seconds(value: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0.0:
        raise argparse.ArgumentTypeError("value must be positive and finite")
    return number


def _atomic_bytes(path: Path, value: bytes) -> None:
    if path.exists():
        raise FileExistsError("refusing to overwrite artifact: %s" % path)
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".partial")
    if partial.exists():
        raise FileExistsError("partial artifact already exists: %s" % partial)
    with partial.open("wb") as handle:
        handle.write(value)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(partial, path)


def _atomic_json(path: Path, value: Any) -> None:
    _reject_digest_fields(value)
    _atomic_bytes(
        path,
        (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
            "utf-8"
        ),
    )


def _atomic_json_lines(path: Path, values: Iterable[Mapping[str, Any]]) -> None:
    if path.exists():
        raise FileExistsError("refusing to overwrite artifact: %s" % path)
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".partial")
    if partial.exists():
        raise FileExistsError("partial artifact already exists: %s" % partial)
    with partial.open("wb") as handle:
        for value in values:
            _reject_digest_fields(value)
            handle.write(
                (
                    json.dumps(
                        value,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    + "\n"
                ).encode("utf-8")
            )
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(partial, path)


def _atomic_lines(path: Path, values: Iterable[str]) -> None:
    if path.exists():
        raise FileExistsError("refusing to overwrite artifact: %s" % path)
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".partial")
    if partial.exists():
        raise FileExistsError("partial artifact already exists: %s" % partial)
    with partial.open("wb") as handle:
        for value in values:
            if "\n" in value or "\r" in value:
                raise ArtifactPqeError("line-oriented artifact value contains a newline")
            handle.write(value.encode("utf-8") + b"\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(partial, path)


def _reject_digest_fields(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            lowered = str(key).lower()
            if "checksum" in lowered or "digest" in lowered or lowered.endswith(
                ("_sha", "_sha1", "_sha256", "_sha512")
            ):
                raise ArtifactPqeError(
                    "digest-bearing result field is forbidden: %s" % key
                )
            _reject_digest_fields(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _reject_digest_fields(child)


def _peak_rss_bytes() -> Optional[int]:
    try:
        import resource
    except ImportError:
        return None
    value = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    return value if sys.platform == "darwin" else value * 1024


def _canonical_term_key(value: str) -> List[str]:
    if value == "u":
        return ["unbound"]
    parts = value.split(circuit_io.US)
    if len(parts) == 2 and parts[0] == "i":
        return ["iri", parts[1]]
    if len(parts) == 2 and parts[0] == "b":
        return ["bnode", parts[1]]
    if len(parts) == 4 and parts[0] == "l":
        return ["literal", parts[1], parts[2], parts[3]]
    raise ArtifactPqeError("invalid canonical circuit term: %r" % value)


def _circuit_binding(binding: Mapping[str, str]) -> List[Any]:
    return [
        [variable, _canonical_term_key(value)]
        for variable, value in sorted(binding.items())
    ]


def _binding_text(binding: Sequence[Any]) -> str:
    return json.dumps(binding, ensure_ascii=False, separators=(",", ":"))


def _answer_binding(answer_key: str) -> List[Any]:
    try:
        binding = json.loads(answer_key)
    except json.JSONDecodeError as exc:
        raise ArtifactPqeError("answer key is not canonical binding JSON") from exc
    if not isinstance(binding, list):
        raise ArtifactPqeError("answer key is not a binding list")
    return binding


def _load_npcs_dag(
    path: Path,
) -> Tuple[Dict[int, Tuple[str, Any]], Dict[str, int], Dict[str, List[Any]], Dict[str, Any]]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ArtifactPqeError("cannot read NPCS DAG: %s" % exc) from exc
    if not isinstance(document, Mapping) or document.get("schema") != NPCS_DAG_SCHEMA:
        raise ArtifactPqeError("unexpected NPCS DAG schema")
    nodes = document.get("nodes")
    roots_document = document.get("roots")
    if not isinstance(nodes, list) or not isinstance(roots_document, list):
        raise ArtifactPqeError("NPCS DAG requires node and root arrays")

    operations = {"and": "times", "or": "plus"}
    circuit: Dict[int, Tuple[str, Any]] = {}
    edges = 0
    by_operation: Counter = Counter()
    for expected, item in enumerate(nodes):
        if not isinstance(item, Mapping) or item.get("id") != expected:
            raise ArtifactPqeError("NPCS DAG node ids must be dense and ordered")
        operation = str(item.get("op"))
        if operation == "leaf":
            payload: Any = item.get("token")
            if not isinstance(payload, str) or not payload:
                raise ArtifactPqeError("leaf %d has no token" % expected)
        elif operation == "const":
            payload = item.get("value")
            if not isinstance(payload, bool):
                raise ArtifactPqeError("constant %d is not Boolean" % expected)
        elif operation == "not":
            payload = item.get("child")
            if not isinstance(payload, int) or not 0 <= payload < expected:
                raise ArtifactPqeError("not node %d is not topological" % expected)
            edges += 1
        elif operation in ("and", "or"):
            children = item.get("children")
            if not isinstance(children, list) or not all(
                isinstance(child, int) and 0 <= child < expected for child in children
            ):
                raise ArtifactPqeError("node %d has invalid children" % expected)
            payload = tuple(children)
            edges += len(payload)
        else:
            raise ArtifactPqeError("unknown NPCS DAG operation: %s" % operation)
        compiler_operation = operations.get(operation, operation)
        circuit[expected] = (compiler_operation, payload)
        by_operation[compiler_operation] += 1

    roots: Dict[str, int] = {}
    bindings: Dict[str, List[Any]] = {}
    for item in roots_document:
        if not isinstance(item, Mapping):
            raise ArtifactPqeError("NPCS root entry is not an object")
        key = item.get("answer_key")
        root = item.get("root")
        if not isinstance(key, str) or key in roots:
            raise ArtifactPqeError("NPCS root has a missing or duplicate answer key")
        if not isinstance(root, int) or root not in circuit:
            raise ArtifactPqeError("NPCS root references a missing node")
        roots[key] = root
        bindings[key] = _answer_binding(key)
    return circuit, roots, bindings, {
        "source_nodes": len(circuit),
        "source_edges": edges,
        "source_total": len(circuit) + edges,
        "source_nodes_by_operation": dict(sorted(by_operation.items())),
        "answer_count": len(roots),
    }


def _children(operation: str, payload: Any) -> Tuple[Any, ...]:
    if operation in ("leaf", "const"):
        return ()
    if operation == "not":
        return (payload,)
    if operation in ("plus", "times"):
        if not isinstance(payload, (list, tuple)):
            raise ArtifactPqeError("%s gate payload is not a child list" % operation)
        return tuple(payload)
    if operation == "minus":
        if not isinstance(payload, (list, tuple)) or len(payload) != 2:
            raise ArtifactPqeError("minus gate requires exactly two children")
        return tuple(payload)
    raise ArtifactPqeError("unknown circuit operation: %s" % operation)


def _reachable_stats(
    circuit: Mapping[Any, Tuple[str, Any]], roots: Iterable[Any]
) -> Dict[str, Any]:
    seen = set()
    edges = 0
    by_operation: Counter = Counter()
    stack = list(roots)
    while stack:
        node = stack.pop()
        if node in seen:
            continue
        if node not in circuit:
            raise ArtifactPqeError("circuit references a missing node: %s" % node)
        seen.add(node)
        operation, payload = circuit[node]
        by_operation[operation] += 1
        children = _children(operation, payload)
        edges += len(children)
        stack.extend(children)
    return {
        "source_nodes": len(seen),
        "source_edges": edges,
        "source_total": len(seen) + edges,
        "source_nodes_by_operation": dict(sorted(by_operation.items())),
    }


def _load_circuit(
    path: Path,
) -> Tuple[
    Dict[str, Tuple[str, Any]],
    Dict[str, str],
    Dict[str, List[Any]],
    Dict[str, Any],
]:
    try:
        with path.open(encoding="utf-8") as handle:
            circuit, answers, encoded_bindings = circuit_io.parse(handle)
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise ArtifactPqeError("cannot read SPARQLcirc circuit: %s" % exc) from exc
    binding_by_root: Dict[str, List[Any]] = {}
    bindings: Dict[str, List[Any]] = {}
    for root in sorted(answers):
        binding = _circuit_binding(encoded_bindings.get(root, {}))
        key = _binding_text(binding)
        binding_by_root[root] = binding
        bindings[key] = binding
    roots, merge_metrics = circuit_io.merge_answer_roots(
        circuit,
        answers,
        lambda root: _binding_text(binding_by_root[root]),
    )
    metrics = _reachable_stats(circuit, roots.values())
    metrics.update(merge_metrics)
    metrics["answer_count"] = len(roots)
    return circuit, roots, bindings, metrics


def _load_weights(
    path: Optional[Path],
    uniform: Optional[float],
    tokens: Sequence[str],
    probability_seed: Optional[int] = None,
) -> Dict[str, float]:
    if sum(source is not None for source in (path, uniform, probability_seed)) != 1:
        raise ArtifactPqeError("choose exactly one probability source")
    if probability_seed is not None:
        try:
            return event_probabilities.event_weights(tokens, probability_seed)
        except ValueError as exc:
            raise ArtifactPqeError(str(exc)) from exc
    if path is None:
        if uniform is None or not math.isfinite(uniform) or not 0.0 <= uniform <= 1.0:
            raise ArtifactPqeError("uniform probability must be finite and in [0, 1]")
        return {token: float(uniform) for token in tokens}
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ArtifactPqeError("cannot read probability file: %s" % exc) from exc
    if not isinstance(document, Mapping):
        raise ArtifactPqeError("probability file must contain one JSON object")
    weights: Dict[str, float] = {}
    for token in tokens:
        if token not in document:
            raise ArtifactPqeError("probability file is missing token: %s" % token)
        try:
            probability = float(document[token])
        except (TypeError, ValueError) as exc:
            raise ArtifactPqeError("probability is not numeric for token: %s" % token) from exc
        if not math.isfinite(probability) or not 0.0 <= probability <= 1.0:
            raise ArtifactPqeError("probability is outside [0, 1] for token: %s" % token)
        weights[token] = probability
    return weights


def evaluate(
    kind: str,
    source: Path,
    output: Path,
    backend: str,
    probabilities: Optional[Path],
    uniform_probability: Optional[float],
    context: Mapping[str, str],
    memory_sample_interval_s: float = DEFAULT_MEMORY_SAMPLE_INTERVAL_S,
    probability_seed: Optional[int] = None,
) -> Dict[str, Any]:
    if output.exists():
        raise ArtifactPqeError("refusing to reuse output directory: %s" % output)
    if not source.is_file():
        raise ArtifactPqeError("source artifact does not exist: %s" % source)
    output.mkdir(parents=True)
    wall_started = time.perf_counter()
    sampler = StageRssSampler(interval_s=memory_sample_interval_s).start()

    sampler.set_stage("artifact_load")
    load_started = time.perf_counter()
    if kind == "npcs-shared":
        circuit, roots, bindings, source_metrics = _load_npcs_dag(source)
    elif kind == "circuit":
        circuit, roots, bindings, source_metrics = _load_circuit(source)
    else:
        raise ArtifactPqeError("unknown source kind: %s" % kind)
    input_load_ms = _milliseconds(load_started)

    sampler.set_stage("probability_load")
    probability_started = time.perf_counter()
    order = tuple(compiler.deterministic_order(circuit, roots))
    weights = _load_weights(
        probabilities, uniform_probability, order, probability_seed
    )
    probability_load_ms = _milliseconds(probability_started)

    sampler.set_stage("pqe_compile")
    compile_started = time.perf_counter()
    batch = compiler.compile_many(
        circuit,
        roots,
        mode="shared",
        backend=backend,
        order=order,
        record_order_fingerprint=False,
    )
    compile_ms = _milliseconds(compile_started)

    sampler.set_stage("pqe_wmc")
    wmc_started = time.perf_counter()
    values = batch.wmc_many(weights)
    wmc_ms = _milliseconds(wmc_started)

    sampler.set_stage("artifact_persist")
    persist_started = time.perf_counter()
    order_path = output / "variable-order.txt"
    probability_path = output / "probabilities.jsonl"
    _atomic_lines(order_path, order)
    _atomic_json_lines(
        probability_path,
        (
            dict(
                context,
                answer_key=key,
                binding=bindings[key],
                probability=values[key],
            )
            for key in sorted(values)
        ),
    )
    persist_ms = _milliseconds(persist_started)

    compiler_metrics = dict(batch.metrics)
    _reject_digest_fields(compiler_metrics)
    stage_memory = sampler.finish()
    metrics: Dict[str, Any] = {
        "schema": SCHEMA,
        "status": "ok",
        "source_kind": kind,
        "source_artifact": source.name,
        "source_artifact_bytes": source.stat().st_size,
        "backend": backend,
        "probability_source": (
            {
                "kind": "seeded-event",
                "seed": probability_seed,
                "scheme": event_probabilities.PROBABILITY_SCHEME,
            }
            if probability_seed is not None
            else {"kind": "file" if probabilities is not None else "uniform"}
        ),
        "context": dict(context),
        **source_metrics,
        "variable_count": len(order),
        "artifact_load_ms": _round_ms(input_load_ms),
        "probability_load_ms": _round_ms(probability_load_ms),
        "compile_wall_ms": _round_ms(compile_ms),
        "wmc_wall_ms": _round_ms(wmc_ms),
        "artifact_persist_ms": _round_ms(persist_ms),
        "pqe_total_ms": _round_ms(
            input_load_ms + probability_load_ms + compile_ms + wmc_ms + persist_ms
        ),
        "pqe_wall_ms": _round_ms(_milliseconds(wall_started)),
        "variable_order_bytes": order_path.stat().st_size,
        "probability_jsonl_bytes": probability_path.stat().st_size,
        "compiler": compiler_metrics,
        "stage_peak_memory": stage_memory,
        "process_peak_rss_bytes": _peak_rss_bytes(),
    }
    _atomic_json(output / "metrics.json", metrics)
    return metrics


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kind", required=True, choices=("npcs-shared", "circuit"))
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--backend", choices=("oracle", "cudd"), default="cudd")
    probability = parser.add_mutually_exclusive_group(required=True)
    probability.add_argument("--probabilities", type=Path)
    probability.add_argument("--uniform-probability", type=float)
    probability.add_argument("--probability-seed", type=int)
    parser.add_argument("--query-id")
    parser.add_argument("--run-id")
    parser.add_argument("--method")
    parser.add_argument(
        "--memory-sample-interval",
        type=_positive_seconds,
        default=DEFAULT_MEMORY_SAMPLE_INTERVAL_S,
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    context = {
        key: value
        for key, value in (
            ("query_id", args.query_id),
            ("run_id", args.run_id),
            ("method", args.method),
        )
        if value is not None
    }
    try:
        metrics = evaluate(
            args.kind,
            args.input.resolve(),
            args.out.resolve(),
            args.backend,
            args.probabilities.resolve() if args.probabilities is not None else None,
            args.uniform_probability,
            context,
            args.memory_sample_interval,
            probability_seed=args.probability_seed,
        )
    except (ArtifactPqeError, OSError, RuntimeError, ValueError) as exc:
        parser.exit(1, "pqe_from_artifact: error: %s\n" % exc)
    print(json.dumps(metrics, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
