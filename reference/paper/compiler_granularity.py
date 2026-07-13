#!/usr/bin/env python3
"""Controlled production-CUDD shared-vs-per-root experiment.

The experiment deliberately separates three physical execution strategies:

``shared``
    one CUDD manager, one source-gate memo, and a vector of output roots;
``per-root-retained``
    one independent manager per output, all retained simultaneously (the
    public ``compiler.compile_many(..., mode="per-root")`` contract);
``per-root-sequential``
    compile and WMC one output at a time, destroy that manager, then continue.
    Its reported memory/peak-live-node values are maxima over one live manager,
    never sums masquerading as a sequential peak.

Defaults implement the paper protocol: one warm-up plus five measured attempts,
each in a fresh killable worker with a 120 second wall-clock deadline.  The CSV
is append-only at attempt granularity, so raw samples, timeouts, and failures
survive interruption.  Production runs always use CUDD; the bundled Python BDD
is accepted only by internal unit-test helpers.
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
import gc
import hashlib
import json
import math
import multiprocessing
import os
from pathlib import Path
import resource
import statistics
import subprocess
import sys
import time
import traceback
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


HERE = Path(__file__).resolve().parent
REFERENCE = HERE.parent
if str(REFERENCE) not in sys.path:
    sys.path.insert(0, str(REFERENCE))

import circuit_io
import compiler
from experiment_timeouts import COMPILE_TIMEOUT_S

try:
    import circuit_cache
except ImportError:  # pragma: no cover - package-style imports
    from . import circuit_cache


SCHEMA = "compiler-granularity-v1"
DEFAULT_SEED = 20260713
DEFAULT_WARMUPS = 1
DEFAULT_RUNS = 5
MODES = ("shared", "per-root-retained", "per-root-sequential")
REORDERINGS = ("fixed", "dynamic")
TERMINAL = {"ok", "timeout", "oom", "error", "worker-exit", "not-run"}

FIELDS = [
    "schema", "git_commit", "git_dirty", "backend", "backend_version",
    "instance_id", "source", "source_path", "source_commit", "query_sha256",
    "circuit_sha256",
    "family", "size", "source_gate_count", "source_edge_count",
    "root_count", "variable_count", "mode", "reordering", "phase", "rep",
    "seed", "weights_sha256", "input_order_sha256", "warmups", "runs",
    "timeout_s", "status", "attempt_wall_ms", "compile_ms", "compile_wall_ms",
    "wmc_ms", "wmc_wall_ms", "teardown_ms", "compiled_nodes_unique",
    "compiled_nodes_sum_roots", "sharing_savings_nodes", "sharing_ratio",
    "manager_count", "concurrent_manager_count", "manager_memory_bytes",
    "manager_memory_semantics", "manager_peak_live_nodes_upper_bound",
    "manager_peak_live_nodes_max", "manager_current_nodes",
    "manager_reorderings", "manager_reordering_seconds", "process_max_rss_bytes",
    "probability_sum", "probability_checksum", "probability_checksum_12dp",
    "parity", "notes",
]


@dataclass(frozen=True)
class CircuitInstance:
    instance_id: str
    source: str
    source_path: str
    source_commit: str
    query_sha256: str
    circuit_sha256: str
    family: str
    size: int
    circ: Mapping[str, Tuple[str, Any]]
    roots: Mapping[str, str]
    order: Tuple[str, ...]


def _stable_text(value: Any) -> str:
    if value is None or isinstance(value, (bool, int, float, str, bytes)):
        return type(value).__name__ + ":" + repr(value)
    if isinstance(value, (tuple, list)):
        return type(value).__name__ + "[" + ",".join(_stable_text(v) for v in value) + "]"
    if isinstance(value, (set, frozenset)):
        return type(value).__name__ + "{" + ",".join(
            sorted(_stable_text(v) for v in value)) + "}"
    if isinstance(value, dict):
        items = sorted((_stable_text(k), _stable_text(v)) for k, v in value.items())
        return "dict{" + ",".join(k + "=" + v for k, v in items) + "}"
    return type(value).__module__ + "." + type(value).__qualname__ + ":" + repr(value)


def _children(node: Tuple[str, Any]) -> Tuple[str, ...]:
    op, payload = node
    if op in ("leaf", "const"):
        return ()
    if op in ("plus", "times", "minus"):
        return tuple(payload)
    raise ValueError("unknown circuit operation %r" % (op,))


def _source_stats(circ: Mapping[str, Tuple[str, Any]], roots: Iterable[str]) -> Tuple[int, int]:
    seen = set()
    edges = 0
    stack = list(roots)
    while stack:
        gate = stack.pop()
        if gate in seen:
            continue
        seen.add(gate)
        if gate not in circ:
            raise ValueError("missing gate %r" % gate)
        children = _children(circ[gate])
        edges += len(children)
        stack.extend(children)
    return len(seen), edges


def _hash_order(order: Sequence[str]) -> str:
    digest = hashlib.sha256()
    for variable in order:
        encoded = variable.encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    return digest.hexdigest()


def _circuit_digest(circ: Mapping[str, Tuple[str, Any]], roots: Mapping[str, str],
                    order: Sequence[str]) -> str:
    payload = {
        "gates": [[_stable_text(gate), op, _stable_text(value)]
                  for gate, (op, value) in sorted(circ.items(), key=lambda item: _stable_text(item[0]))],
        "roots": [[_stable_text(key), _stable_text(value)]
                  for key, value in sorted(roots.items(), key=lambda item: _stable_text(item[0]))],
        "order": list(order),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def synthetic_instance(family: str, size: int, root_count: int) -> CircuitInstance:
    """Build a fixed sharing or disjoint-support control family."""
    if family not in ("sharing", "no-sharing"):
        raise ValueError("synthetic family must be sharing or no-sharing")
    if size < 1 or root_count < 1:
        raise ValueError("synthetic size and root count must be positive")

    circ: Dict[str, Tuple[str, Any]] = {"const:one": ("const", 1)}
    roots: Dict[str, str] = {}
    order: List[str] = []
    if family == "sharing":
        # Private variables precede the common suffix.  This is intentional:
        # root_i = private_i AND common then shares the complete `common` BDD
        # across outputs under this fixed order.
        private_gates = []
        for root_index in range(root_count):
            gate = "private:l:%05d" % root_index
            token = "urn:compiler:sharing:private:%05d" % root_index
            circ[gate] = ("leaf", token)
            private_gates.append(gate)
            order.append(token)
        previous = "const:one"
        for index in range(size):
            leaf = "common:l:%05d" % index
            gate = "common:g:%05d" % index
            token = "urn:compiler:sharing:common:%05d" % index
            circ[leaf] = ("leaf", token)
            circ[gate] = ("times", (previous, leaf))
            previous = gate
            order.append(token)
        for root_index, private in enumerate(private_gates):
            gate = "answer:%05d" % root_index
            circ[gate] = ("times", (private, previous))
            roots["answer:%05d" % root_index] = gate
    else:
        for root_index in range(root_count):
            previous = "const:one"
            for index in range(size):
                leaf = "disjoint:l:%05d:%05d" % (root_index, index)
                gate = "disjoint:g:%05d:%05d" % (root_index, index)
                token = "urn:compiler:no-sharing:%05d:%05d" % (root_index, index)
                circ[leaf] = ("leaf", token)
                circ[gate] = ("times", (previous, leaf))
                previous = gate
                order.append(token)
            roots["answer:%05d" % root_index] = previous

    order_tuple = tuple(order)
    digest = _circuit_digest(circ, roots, order_tuple)
    return CircuitInstance(
        instance_id="synthetic:%s:s%d:r%d:%s" % (family, size, root_count, digest[:12]),
        source="synthetic",
        source_path="",
        source_commit="",
        query_sha256="",
        circuit_sha256=digest,
        family=family,
        size=size,
        circ=circ,
        roots=roots,
        order=order_tuple,
    )


def cache_instance(path: Path, root_limit: Optional[int] = None,
                   expected_commit: Optional[str] = None) -> CircuitInstance:
    metadata_path = Path(path).with_suffix(".json")
    if not metadata_path.is_file():
        raise ValueError("canonical cache sidecar is missing: %s" % metadata_path)
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    observations = [metadata] + list(metadata.get("producer_observations", []))
    selected = metadata
    if expected_commit is not None:
        selected = next(
            (item for item in observations
             if str(item.get("commit", "")) == expected_commit),
            None,
        )
        if selected is None:
            commits = sorted({str(item.get("commit", "")) for item in observations})
            raise ValueError(
                "cache has producer commits %r, not experiment commit %s for %s"
                % (commits, expected_commit, path))
    circuit_sha = str(selected.get("circuit_sha256", ""))
    query_sha = str(selected.get("query_sha256", ""))
    source_commit = str(selected.get("commit", ""))
    if len(query_sha) != 64 or any(ch not in "0123456789abcdef" for ch in query_sha):
        raise ValueError("canonical cache sidecar has no valid query_sha256: %s" % metadata_path)
    if not source_commit or source_commit == "?":
        raise ValueError("canonical cache sidecar has no frozen commit: %s" % metadata_path)
    expected_stem = "%s-%s" % (query_sha, circuit_sha)
    if Path(path).stem != expected_stem:
        raise ValueError(
            "canonical cache filename does not match sidecar hashes: %s != %s"
            % (Path(path).stem, expected_stem))
    descriptor = circuit_cache.verify(path, circuit_sha)
    text = Path(path).read_text(encoding="utf-8")
    circ, answers, bindings = circuit_io.parse(text)
    roots: Dict[str, str] = {}
    for gate in answers:
        key = circuit_io.answer_key(bindings[gate])
        if key in roots and roots[key] != gate:
            raise ValueError("cache has duplicate gates for term-aware answer %r" % key)
        roots[key] = gate
    if not roots:
        raise ValueError("cache contains no answer roots: %s" % path)
    if root_limit is not None:
        roots = dict(sorted(roots.items())[:root_limit])
    order = compiler.deterministic_order(circ, roots)
    return CircuitInstance(
        instance_id="cache:%s:%s" % (Path(path).stem, descriptor["circuit_sha256"][:12]),
        source="canonical-cache",
        source_path=str(Path(path).resolve()),
        source_commit=source_commit,
        query_sha256=query_sha,
        circuit_sha256=descriptor["circuit_sha256"],
        family="cache",
        size=descriptor["circuit_triples"],
        circ=circ,
        roots=roots,
        order=order,
    )


def discover_cache_paths(values: Sequence[str]) -> List[Path]:
    paths = []
    for value in values:
        path = Path(value).expanduser().resolve()
        if path.is_dir():
            paths.extend(sorted(path.glob("*.nt")))
        else:
            paths.append(path)
    unique = []
    seen = set()
    for path in paths:
        resolved = str(path.resolve())
        if resolved not in seen:
            if not path.is_file():
                raise FileNotFoundError(path)
            seen.add(resolved)
            unique.append(path)
    return unique


def fixed_weights(order: Sequence[str], seed: int) -> Tuple[Dict[str, float], str]:
    """Stable non-uniform probabilities in [0.05, 0.95], independent of hash randomization."""
    weights = {}
    fingerprint = hashlib.sha256()
    for variable in sorted(set(order)):
        digest = hashlib.sha256((str(seed) + "\0" + variable).encode("utf-8")).digest()
        unit = int.from_bytes(digest[:8], "big") / float(1 << 64)
        probability = 0.05 + 0.90 * unit
        weights[variable] = probability
        encoded = variable.encode("utf-8")
        fingerprint.update(len(encoded).to_bytes(8, "big"))
        fingerprint.update(encoded)
        fingerprint.update(probability.hex().encode("ascii"))
    return weights, fingerprint.hexdigest()


def probability_checksums(values: Mapping[str, float]) -> Tuple[float, str, str]:
    exact = hashlib.sha256()
    tolerant = hashlib.sha256()
    ordered_values = []
    for key in sorted(values, key=_stable_text):
        value = float(values[key])
        if not math.isfinite(value) or not 0.0 <= value <= 1.0:
            raise ValueError("invalid WMC result for %r: %r" % (key, value))
        key_bytes = _stable_text(key).encode("utf-8")
        exact.update(len(key_bytes).to_bytes(8, "big"))
        exact.update(key_bytes)
        exact.update(value.hex().encode("ascii"))
        tolerant.update(len(key_bytes).to_bytes(8, "big"))
        tolerant.update(key_bytes)
        tolerant.update(("%.12e" % value).encode("ascii"))
        ordered_values.append(value)
    return math.fsum(ordered_values), exact.hexdigest(), tolerant.hexdigest()


def _rss_bytes() -> int:
    value = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    # Linux reports KiB; macOS/BSD reports bytes.
    return value * 1024 if sys.platform.startswith("linux") else value


def _backend_identity(backend: str) -> str:
    if backend == "oracle":
        return "bundled-python-robdd"
    import dd
    import dd.cudd
    return "dd=%s;cudd=%s" % (
        getattr(dd, "__version__", "unknown"),
        getattr(dd.cudd, "__version__", "unknown"),
    )


def _git_identity() -> Tuple[str, str]:
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=HERE, check=True,
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain"], cwd=HERE, check=True,
            capture_output=True, text=True, timeout=10,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return "unknown", "unknown"
    return commit, "true" if dirty else "false"


def _retained_attempt(instance: CircuitInstance, weights: Mapping[str, float],
                      mode: str, dynamic: bool, backend: str) -> Dict[str, Any]:
    compiler_mode = "shared" if mode == "shared" else "per-root"
    compile_started = time.perf_counter()
    batch = compiler.compile_many(
        instance.circ, instance.roots, mode=compiler_mode, backend=backend,
        order=instance.order, dynamic_reordering=dynamic,
    )
    compile_wall_ms = (time.perf_counter() - compile_started) * 1000.0
    wmc_started = time.perf_counter()
    probabilities = batch.wmc_many(weights)
    wmc_wall_ms = (time.perf_counter() - wmc_started) * 1000.0
    metrics = dict(batch.metrics)
    probability_sum, checksum, checksum_12dp = probability_checksums(probabilities)
    return {
        "backend": backend,
        "backend_version": _backend_identity(backend),
        "compile_ms": metrics["compile_ms"],
        "compile_wall_ms": compile_wall_ms,
        "wmc_ms": metrics["wmc_ms"],
        "wmc_wall_ms": wmc_wall_ms,
        "teardown_ms": 0.0,
        "compiled_nodes_unique": metrics["compiled_nodes_unique"],
        "compiled_nodes_sum_roots": metrics["compiled_nodes_sum_roots"],
        "sharing_savings_nodes": metrics["sharing_savings_nodes"],
        "sharing_ratio": metrics["sharing_ratio"],
        "manager_count": metrics["manager_count"],
        "concurrent_manager_count": metrics["manager_count"],
        "manager_memory_bytes": metrics["manager_memory_bytes"],
        "manager_memory_semantics": (
            "one-shared-manager" if mode == "shared"
            else "sum-of-simultaneously-retained-independent-managers"
        ),
        "manager_peak_live_nodes_upper_bound": metrics["manager_peak_live_nodes_upper_bound"],
        "manager_peak_live_nodes_max": metrics["manager_peak_live_nodes_max"],
        "manager_current_nodes": metrics["manager_current_nodes"],
        "manager_reorderings": metrics["manager_reorderings"],
        "manager_reordering_seconds": metrics["manager_reordering_seconds"],
        "probability_sum": probability_sum,
        "probability_checksum": checksum,
        "probability_checksum_12dp": checksum_12dp,
    }


def _sequential_attempt(instance: CircuitInstance, weights: Mapping[str, float],
                        dynamic: bool, backend: str) -> Dict[str, Any]:
    compile_ms = compile_wall_ms = wmc_ms = wmc_wall_ms = 0.0
    teardown_ms = 0.0
    node_sum = root_node_sum = 0
    manager_memory_max: Optional[int] = None
    peak_live_max: Optional[int] = None
    current_nodes_max: Optional[int] = None
    reorderings = 0
    reordering_seconds = 0.0
    probabilities = {}
    ordered_keys = sorted(instance.roots, key=_stable_text)

    for key in ordered_keys:
        root = instance.roots[key]
        support = set(compiler.deterministic_order(instance.circ, {key: root}))
        local_order = tuple(variable for variable in instance.order if variable in support)
        started = time.perf_counter()
        batch = compiler.compile_many(
            instance.circ, {key: root}, mode="shared", backend=backend,
            order=local_order, dynamic_reordering=dynamic,
        )
        compile_wall_ms += (time.perf_counter() - started) * 1000.0
        metrics = batch.metrics
        compile_ms += float(metrics["compile_ms"])
        started = time.perf_counter()
        result = batch.wmc_many(weights)
        wmc_wall_ms += (time.perf_counter() - started) * 1000.0
        wmc_ms += float(batch.metrics["wmc_ms"])
        probabilities[key] = result[key]
        node_sum += int(metrics["compiled_nodes_unique"])
        root_node_sum += int(metrics["compiled_nodes_sum_roots"])
        if metrics["manager_memory_bytes"] is not None:
            manager_memory_max = max(manager_memory_max or 0, int(metrics["manager_memory_bytes"]))
        if metrics["manager_peak_live_nodes_upper_bound"] is not None:
            peak_live_max = max(
                peak_live_max or 0, int(metrics["manager_peak_live_nodes_upper_bound"]))
        if metrics["manager_current_nodes"] is not None:
            current_nodes_max = max(current_nodes_max or 0, int(metrics["manager_current_nodes"]))
        reorderings += int(metrics["manager_reorderings"])
        reordering_seconds += float(metrics["manager_reordering_seconds"])

        # Destruction is part of the real sequential path and is timed
        # separately. At no point does this function retain a list of batches.
        started = time.perf_counter()
        del result, batch
        gc.collect()
        teardown_ms += (time.perf_counter() - started) * 1000.0

    probability_sum, checksum, checksum_12dp = probability_checksums(probabilities)
    return {
        "backend": backend,
        "backend_version": _backend_identity(backend),
        "compile_ms": compile_ms,
        "compile_wall_ms": compile_wall_ms,
        "wmc_ms": wmc_ms,
        "wmc_wall_ms": wmc_wall_ms,
        "teardown_ms": teardown_ms,
        "compiled_nodes_unique": node_sum,
        "compiled_nodes_sum_roots": root_node_sum,
        "sharing_savings_nodes": 0,
        "sharing_ratio": 1.0,
        "manager_count": len(instance.roots),
        "concurrent_manager_count": 1 if instance.roots else 0,
        "manager_memory_bytes": manager_memory_max,
        "manager_memory_semantics": "max-of-one-live-sequential-manager",
        "manager_peak_live_nodes_upper_bound": peak_live_max,
        "manager_peak_live_nodes_max": peak_live_max,
        "manager_current_nodes": current_nodes_max,
        "manager_reorderings": reorderings,
        "manager_reordering_seconds": reordering_seconds,
        "probability_sum": probability_sum,
        "probability_checksum": checksum,
        "probability_checksum_12dp": checksum_12dp,
    }


def execute_attempt(instance: CircuitInstance, weights: Mapping[str, float],
                    mode: str, reordering: str, backend: str = "cudd") -> Dict[str, Any]:
    """Execute one attempt in the current process (workers call this)."""
    if mode not in MODES or reordering not in REORDERINGS:
        raise ValueError("invalid mode or reordering")
    if backend not in ("cudd", "oracle"):
        raise ValueError("invalid backend")
    dynamic = reordering == "dynamic"
    if backend == "oracle" and dynamic:
        raise ValueError("the test-only oracle does not support dynamic reordering")
    started = time.perf_counter()
    if mode == "per-root-sequential":
        result = _sequential_attempt(instance, weights, dynamic, backend)
    else:
        result = _retained_attempt(instance, weights, mode, dynamic, backend)
    result["attempt_worker_ms"] = (time.perf_counter() - started) * 1000.0
    result["process_max_rss_bytes"] = _rss_bytes()
    return result


def _attempt_worker(send, instance: CircuitInstance, weights: Mapping[str, float],
                    mode: str, reordering: str, backend: str) -> None:
    try:
        payload = {"status": "ok"}
        payload.update(execute_attempt(instance, weights, mode, reordering, backend))
    except MemoryError as exc:
        payload = {"status": "oom", "notes": " ".join(str(exc).splitlines())[:500]}
    except BaseException as exc:  # worker must report native-wrapper and validation failures
        payload = {
            "status": "error",
            "notes": (
                type(exc).__name__ + ": " + " ".join(str(exc).splitlines())
            )[:500],
            "traceback": traceback.format_exc(limit=8)[-3000:],
        }
    try:
        send.send(payload)
    finally:
        send.close()


def _mp_context():
    methods = multiprocessing.get_all_start_methods()
    return multiprocessing.get_context("fork" if "fork" in methods else "spawn")


def run_killable(instance: CircuitInstance, weights: Mapping[str, float], mode: str,
                 reordering: str, timeout: float, backend: str = "cudd",
                 context=None) -> Dict[str, Any]:
    """Run one attempt behind a parent-enforced, process-killable deadline."""
    if timeout <= 0:
        raise ValueError("attempt timeout must be positive")
    context = context or _mp_context()
    receive, send = context.Pipe(duplex=False)
    process = context.Process(
        target=_attempt_worker,
        args=(send, instance, dict(weights), mode, reordering, backend),
    )
    started = time.monotonic()
    process.start()
    send.close()
    process.join(timeout)
    elapsed_ms = (time.monotonic() - started) * 1000.0
    if process.is_alive():
        process.terminate()
        process.join(2.0)
        if process.is_alive() and hasattr(process, "kill"):
            process.kill()
            process.join(2.0)
        receive.close()
        return {
            "status": "timeout",
            "attempt_wall_ms": min(elapsed_ms, timeout * 1000.0),
            "notes": "attempt exceeded %.6gs killable deadline" % timeout,
        }
    payload = receive.recv() if receive.poll(1.0) else {
        "status": "worker-exit",
        "notes": "worker exited %r without a result" % process.exitcode,
    }
    receive.close()
    payload["attempt_wall_ms"] = elapsed_ms
    return payload


def _blank_row(instance: CircuitInstance, mode: str, reordering: str,
               phase: str, rep: int, args: argparse.Namespace,
               weights_sha: str) -> Dict[str, Any]:
    gates, edges = _source_stats(instance.circ, instance.roots.values())
    return {
        field: "" for field in FIELDS
    } | {
        "schema": SCHEMA,
        "git_commit": getattr(args, "git_commit", "unknown"),
        "git_dirty": getattr(args, "git_dirty", "unknown"),
        "backend": "cudd",
        "backend_version": getattr(args, "backend_version", "unknown"),
        "instance_id": instance.instance_id,
        "source": instance.source,
        "source_path": instance.source_path,
        "source_commit": instance.source_commit,
        "query_sha256": instance.query_sha256,
        "circuit_sha256": instance.circuit_sha256,
        "family": instance.family,
        "size": instance.size,
        "source_gate_count": gates,
        "source_edge_count": edges,
        "root_count": len(instance.roots),
        "variable_count": len(instance.order),
        "mode": mode,
        "reordering": reordering,
        "phase": phase,
        "rep": rep,
        "seed": args.seed,
        "weights_sha256": weights_sha,
        "input_order_sha256": _hash_order(instance.order),
        "warmups": args.warmups,
        "runs": args.runs,
        "timeout_s": args.timeout,
    }


def _row_key(row: Mapping[str, Any]) -> Tuple[str, ...]:
    return tuple(str(row.get(field, "")) for field in (
        "git_commit", "git_dirty", "backend", "backend_version", "instance_id",
        "source_commit", "query_sha256",
        "mode", "reordering", "phase", "rep", "seed", "weights_sha256",
        "warmups", "runs", "timeout_s",
    ))


def _baseline_key(row: Mapping[str, Any]) -> Tuple[str, ...]:
    return tuple(str(row.get(field, "")) for field in (
        "git_commit", "git_dirty", "backend", "backend_version", "instance_id",
        "circuit_sha256", "weights_sha256", "input_order_sha256",
    ))


def _repair_torn_tail(path: Path) -> None:
    """Drop an incomplete final physical record left by process interruption."""
    if not path.exists() or path.stat().st_size == 0:
        return
    with path.open("r+b") as handle:
        handle.seek(-1, os.SEEK_END)
        if handle.read(1) == b"\n":
            return
        position = handle.tell() - 1
        while position > 0:
            start = max(0, position - 65536)
            handle.seek(start)
            block = handle.read(position - start)
            newline = block.rfind(b"\n")
            if newline >= 0:
                handle.truncate(start + newline + 1)
                return
            position = start
        handle.truncate(0)


def load_checkpoint(path: Path) -> List[Dict[str, str]]:
    if not path.exists():
        return []
    _repair_torn_tail(path)
    if path.stat().st_size == 0:
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != FIELDS:
            raise ValueError("checkpoint schema mismatch: %s" % path)
        rows = list(reader)
    return [row for row in rows if row.get("schema") == SCHEMA]


def append_checkpoint(path: Path, row: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _repair_torn_tail(path)
    exists = path.exists() and path.stat().st_size > 0
    mode = "a" if exists else "w"
    with path.open(mode, newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, extrasaction="ignore")
        if not exists:
            writer.writeheader()
        writer.writerow({field: row.get(field, "") for field in FIELDS})
        handle.flush()
        os.fsync(handle.fileno())


def _attempt_schedule(warmups: int, runs: int) -> List[Tuple[str, int]]:
    return ([('warmup', index) for index in range(warmups)]
            + [('measured', index) for index in range(runs)])


def _parity(result: Mapping[str, Any], baseline: Optional[Mapping[str, str]]) -> str:
    if result.get("status") != "ok":
        return "unverified"
    if baseline is None:
        return "baseline"
    checksum = str(result.get("probability_checksum_12dp", ""))
    if checksum != baseline.get("probability_checksum_12dp", ""):
        return "mismatch"
    try:
        observed = float(result["probability_sum"])
        expected = float(baseline["probability_sum"])
    except (KeyError, TypeError, ValueError):
        return "mismatch"
    tolerance = 1e-10 * max(1.0, abs(expected))
    return "ok" if abs(observed - expected) <= tolerance else "mismatch"


def _preflight_cudd() -> str:
    probe = subprocess.run(
        [sys.executable, "-c", (
            "import dd, dd.cudd; "
            "print('dd=%s;cudd=%s' % "
            "(getattr(dd, '__version__', 'unknown'), "
            "getattr(dd.cudd, '__version__', 'unknown')))"
        )],
        capture_output=True, text=True, timeout=30,
    )
    if probe.returncode:
        raise RuntimeError(
            "production CUDD unavailable; install reference/requirements-production.txt: "
            + (probe.stderr or probe.stdout)[-1000:])
    return probe.stdout.strip()


def _parse_ints(value: str) -> List[int]:
    values = [int(item.strip()) for item in value.split(",") if item.strip()]
    if not values or any(item < 1 for item in values):
        raise argparse.ArgumentTypeError("expected comma-separated positive integers")
    return values


def build_instances(args: argparse.Namespace) -> List[CircuitInstance]:
    instances = []
    if not args.no_synthetic:
        for family in args.families.split(","):
            family = family.strip()
            if family:
                for size in args.sizes:
                    instances.append(synthetic_instance(family, size, args.roots))
    expected_commit = None if args.allow_cache_commit_mismatch else args.git_commit
    for path in discover_cache_paths(args.cache):
        instances.append(cache_instance(path, args.root_limit, expected_commit))
    if not instances:
        raise ValueError("no circuit instances selected")
    return instances


def run_experiment(args: argparse.Namespace) -> Dict[str, Any]:
    args.backend_version = _preflight_cudd()
    args.git_commit, args.git_dirty = _git_identity()
    instances = build_instances(args)
    rows = load_checkpoint(args.output)
    done = {_row_key(row) for row in rows if row.get("status") in TERMINAL}
    baselines: Dict[Tuple[str, ...], Dict[str, str]] = {}
    for row in rows:
        if row.get("status") == "ok" and row.get("parity") in ("baseline", "ok"):
            baselines.setdefault(_baseline_key(row), row)

    reorderings = ["fixed"] + (["dynamic"] if args.include_dynamic else [])
    attempted = skipped = failed = 0
    for instance in instances:
        weights, weights_sha = fixed_weights(instance.order, args.seed)
        for reordering in reorderings:
            for mode in MODES:
                stop_cell = False
                for phase, rep in _attempt_schedule(args.warmups, args.runs):
                    row = _blank_row(
                        instance, mode, reordering, phase, rep, args, weights_sha)
                    key = _row_key(row)
                    if key in done:
                        skipped += 1
                        continue
                    if stop_cell:
                        row.update({
                            "status": "not-run",
                            "parity": "unverified",
                            "notes": "earlier attempt in this cell failed or timed out",
                        })
                        append_checkpoint(args.output, row)
                        done.add(key)
                        continue
                    print(
                        "# %s %s %s %s[%d]" % (
                            instance.instance_id, mode, reordering, phase, rep),
                        file=sys.stderr, flush=True,
                    )
                    result = run_killable(
                        instance, weights, mode, reordering, args.timeout, backend="cudd")
                    attempted += 1
                    baseline_id = _baseline_key(row)
                    baseline = baselines.get(baseline_id)
                    row.update(result)
                    row["parity"] = _parity(result, baseline)
                    append_checkpoint(args.output, row)
                    done.add(key)
                    if result.get("status") == "ok" and baseline is None:
                        baselines[baseline_id] = {
                            key: str(row.get(key, ""))
                            for key in ("probability_checksum_12dp", "probability_sum")
                        }
                    if result.get("status") != "ok":
                        failed += 1
                        stop_cell = not args.continue_after_failure

    final_rows = load_checkpoint(args.output)
    measured_ok = [row for row in final_rows
                   if row.get("phase") == "measured" and row.get("status") == "ok"]
    compile_samples = [float(row["compile_wall_ms"]) for row in measured_ok]
    wmc_samples = [float(row["wmc_wall_ms"]) for row in measured_ok]
    failed_total = sum(
        row.get("status") not in ("ok", "not-run") for row in final_rows)
    parity_mismatches = sum(row.get("parity") == "mismatch" for row in final_rows)
    return {
        "schema": SCHEMA,
        "status": (
            "completed-with-failures"
            if failed_total or parity_mismatches else "ok"
        ),
        "output": str(args.output.resolve()),
        "instance_count": len(instances),
        "attempted": attempted,
        "resumed_or_skipped": skipped,
        "failed_attempts_this_invocation": failed,
        "failed_attempts_total": failed_total,
        "parity_mismatches": parity_mismatches,
        "checkpoint_rows": len(final_rows),
        "measured_ok": len(measured_ok),
        "compile_wall_ms_median": statistics.median(compile_samples) if compile_samples else None,
        "wmc_wall_ms_median": statistics.median(wmc_samples) if wmc_samples else None,
    }


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--output", type=Path,
                        default=HERE / "compiler_granularity.csv")
    result.add_argument("--cache", action="append", default=[],
                        help="canonical circuit .nt file or directory (repeatable)")
    result.add_argument("--no-synthetic", action="store_true")
    result.add_argument("--families", default="sharing,no-sharing")
    result.add_argument("--sizes", type=_parse_ints, default=[8, 32, 128])
    result.add_argument("--roots", type=int, default=8)
    result.add_argument("--root-limit", type=int)
    result.add_argument(
        "--allow-cache-commit-mismatch", action="store_true",
        help="exploratory only: accept a canonical cache produced by another commit",
    )
    result.add_argument("--seed", type=int, default=DEFAULT_SEED)
    result.add_argument("--warmups", type=int, default=DEFAULT_WARMUPS)
    result.add_argument("--runs", type=int, default=DEFAULT_RUNS)
    result.add_argument("--timeout", type=float, default=float(COMPILE_TIMEOUT_S))
    result.add_argument("--include-dynamic", action="store_true")
    result.add_argument("--continue-after-failure", action="store_true")
    return result


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parser().parse_args(argv)
    if args.roots < 1 or args.warmups < 0 or args.runs < 1 or args.timeout <= 0:
        raise SystemExit("roots/runs/timeout must be positive and warmups non-negative")
    if args.root_limit is not None and args.root_limit < 1:
        raise SystemExit("root-limit must be positive")
    summary = run_experiment(args)
    print(json.dumps(summary, sort_keys=True))
    # Timeouts/OOM are experiment outcomes, but semantic disagreement between
    # modes is a correctness failure and must not pass unnoticed in automation.
    return 1 if summary["parity_mismatches"] else 0


if __name__ == "__main__":
    sys.exit(main())
