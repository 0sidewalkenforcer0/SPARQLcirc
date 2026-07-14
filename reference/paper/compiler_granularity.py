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

Publication protocol ``compiler-granularity-v3`` fixes one warm-up plus five
measured attempts, seed 20260713, a 120 second killable deadline, both fixed and
dynamic reordering, and one of two explicit profiles: the code-defined
synthetic sharing controls or frozen canonical real-cache circuits using every
answer root and an exact query-SHA allowlist.  Publication runs freeze both the
Python runtime and native CUDD extension and reject every exploratory override.

The CSV is append-only at attempt granularity, but resume accepts only a strict
canonical schedule prefix whose metrics and parity can be recomputed.  A
durable completion sidecar binds the exact checkpoint bytes and complete run
configuration.  Production runs always use CUDD; the bundled Python BDD is
accepted only by internal unit-test helpers.
"""
from __future__ import annotations

import argparse
import contextlib
import csv
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import fcntl
import gc
import hashlib
import io
import json
import math
import multiprocessing
import os
from pathlib import Path
import re
import resource
import signal
import stat
import statistics
import subprocess
import sys
import tempfile
import time
import traceback
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


HERE = Path(__file__).resolve().parent
REFERENCE = HERE.parent
if str(REFERENCE) not in sys.path:
    sys.path.insert(0, str(REFERENCE))

import circuit_io
import compiler
import freeze_inputs

try:
    import circuit_cache
except ImportError:  # pragma: no cover - package-style imports
    from . import circuit_cache


SCHEMA = "compiler-granularity-v3"
FORMAL_PROTOCOL = "compiler-granularity-v3"
FORMAL_SEED = 20260713
FORMAL_WARMUPS = 1
FORMAL_RUNS = 5
FORMAL_TIMEOUT_S = 120.0
FORMAL_SYNTHETIC_FAMILIES = ("sharing", "no-sharing")
FORMAL_SYNTHETIC_SIZES = (8, 32, 128)
FORMAL_SYNTHETIC_ROOTS = 8
FORMAL_REORDERINGS = ("fixed", "dynamic")
SYNTHETIC_PROFILE = "synthetic-sharing-controls-v1"
REAL_CACHE_PROFILE = "real-cache-all-roots-v1"
PROFILES = (SYNTHETIC_PROFILE, REAL_CACHE_PROFILE)
DEFAULT_SEED = FORMAL_SEED
DEFAULT_WARMUPS = FORMAL_WARMUPS
DEFAULT_RUNS = FORMAL_RUNS
CUDD_TOOL_NAME = "dd-cudd-extension"
PYTHON_TOOL_NAME = "python-runtime"
COMPLETION_SCHEMA = "compiler-granularity-completion-v1"
FAILURE_POLICY = "terminal-or-parity-failure-blocks-cell-no-retry"
MODES = ("shared", "per-root-retained", "per-root-sequential")
REORDERINGS = ("fixed", "dynamic")
TERMINAL = {
    "ok", "timeout", "oom", "error", "worker-exit", "killed-signal",
    "cleanup-error", "not-run",
}
FATAL_STATUSES = {"error", "worker-exit", "killed-signal", "cleanup-error"}
RESOURCE_STATUSES = {"timeout", "oom"}
NOT_RUN_NOTE = (
    "earlier terminal failure or parity mismatch blocks this cell; "
    "the original run config never retries"
)
MAX_INTEGER_METRIC = 2 ** 63 - 1
REPO_ROOT = HERE.parents[1]

FIELDS = [
    "schema", "batch_id", "frozen_inputs_sha256", "protocol",
    "run_config_sha256", "formal_run", "profile", "failure_policy",
    "git_commit", "git_dirty", "backend", "backend_version",
    "cudd_extension_sha256", "cudd_extension_bytes",
    "python_runtime_sha256", "python_runtime_bytes",
    "instance_id", "source", "source_path", "source_commit", "source_batch_id",
    "query_sha256", "source_sidecar_sha256", "source_sidecar_bytes",
    "source_observation_sha256", "circuit_sha256", "circuit_bytes",
    "family", "size", "source_gate_count", "source_edge_count",
    "root_count", "variable_count", "mode", "reordering", "phase", "rep",
    "seed", "weights_sha256", "input_order_sha256", "warmups", "runs",
    "timeout_s", "status", "attempt_wall_ms", "attempt_worker_ms",
    "prepare_ms", "backend_compile_ms", "inspect_ms", "source_to_result_ms",
    "timing_unattributed_ms", "timing_scope", "compile_ms", "compile_wall_ms",
    "wmc_ms", "wmc_wall_ms", "teardown_ms", "compiled_nodes_unique",
    "compiled_nodes_sum_roots", "sharing_savings_nodes", "sharing_ratio",
    "manager_count", "concurrent_manager_count", "manager_memory_bytes",
    "manager_memory_semantics", "memory_aggregation",
    "manager_peak_live_nodes_upper_bound",
    "manager_peak_live_nodes_max", "manager_current_nodes",
    "manager_reorderings", "manager_reordering_seconds", "process_max_rss_bytes",
    "probability_sum", "probability_checksum", "probability_checksum_12dp",
    "parity", "cleanup_ms", "cleanup_action", "process_group_reaped", "notes",
]


@dataclass(frozen=True)
class CircuitInstance:
    instance_id: str
    source: str
    source_path: str
    source_commit: str
    source_batch_id: str
    query_sha256: str
    circuit_sha256: str
    family: str
    size: int
    circ: Mapping[str, Tuple[str, Any]]
    roots: Mapping[str, str]
    order: Tuple[str, ...]
    source_sidecar_sha256: str = ""
    source_sidecar_bytes: int = 0
    source_observation_sha256: str = ""
    circuit_bytes: int = 0
    artifact_snapshots: Tuple[Mapping[str, Any], ...] = ()


def _stat_signature(value: os.stat_result) -> Tuple[int, ...]:
    return (
        value.st_dev, value.st_ino, value.st_mode, value.st_nlink,
        value.st_size, value.st_mtime_ns, value.st_ctime_ns,
    )


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(
        str(path), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    )
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _validate_opened_single_link(path: Path, descriptor: int,
                                 label: str) -> os.stat_result:
    opened = os.fstat(descriptor)
    try:
        current = os.lstat(path)
    except OSError as exc:
        raise ValueError("%s disappeared while open" % label) from exc
    if (
        not stat.S_ISREG(opened.st_mode)
        or opened.st_nlink != 1
        or _stat_signature(opened) != _stat_signature(current)
    ):
        raise ValueError("%s must be one stable single-link regular file" % label)
    return opened


def _open_single_link(path: Path, flags: int, label: str,
                      mode: int = 0o600) -> int:
    descriptor = None
    try:
        descriptor = os.open(
            str(path),
            flags | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
            mode,
        )
        _validate_opened_single_link(path, descriptor, label)
        return descriptor
    except (OSError, ValueError) as exc:
        if descriptor is not None:
            os.close(descriptor)
        if isinstance(exc, ValueError):
            raise
        raise ValueError("%s is missing, aliased, or unsafe" % label) from exc


def _validate_opened_directory(path: Path, descriptor: int,
                               label: str) -> os.stat_result:
    opened = os.fstat(descriptor)
    try:
        current = os.lstat(path)
    except OSError as exc:
        raise ValueError("%s disappeared while open" % label) from exc
    if (
        not stat.S_ISDIR(opened.st_mode)
        or not stat.S_ISDIR(current.st_mode)
        or (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino)
    ):
        raise ValueError("%s must remain one stable directory" % label)
    return opened


def _open_stable_directory(path: Path, label: str) -> int:
    descriptor = None
    try:
        descriptor = os.open(
            str(path),
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
        )
        _validate_opened_directory(path, descriptor, label)
        return descriptor
    except (OSError, ValueError) as exc:
        if descriptor is not None:
            os.close(descriptor)
        if isinstance(exc, ValueError):
            raise
        raise ValueError("%s is missing, aliased, or unsafe" % label) from exc


def _acquire_flock(descriptor: int, started: float, timeout: float,
                   label: str) -> None:
    while True:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except BlockingIOError:
            if time.monotonic() - started >= timeout:
                raise TimeoutError("%s timed out" % label)
            time.sleep(0.05)


def _snapshot_file(path: Path, label: str,
                   *, allow_empty: bool = False) -> Dict[str, Any]:
    """Hash one stable single-link artifact and retain its stat identity."""
    try:
        resolved = Path(path).expanduser().resolve(strict=True)
    except OSError as exc:
        raise RuntimeError("%s is missing" % label) from exc
    descriptor = _open_single_link(resolved, os.O_RDONLY, label)
    try:
        before = os.fstat(descriptor)
        if before.st_size == 0 and not allow_empty:
            raise RuntimeError("%s is empty" % label)
        digest = hashlib.sha256()
        while True:
            block = os.read(descriptor, 1024 * 1024)
            if not block:
                break
            digest.update(block)
        after = _validate_opened_single_link(resolved, descriptor, label)
        if _stat_signature(before) != _stat_signature(after):
            raise RuntimeError("%s changed while hashing" % label)
    finally:
        os.close(descriptor)
    return {
        "path": str(resolved),
        "bytes": before.st_size,
        "sha256": digest.hexdigest(),
        "signature": _stat_signature(before),
        "label": label,
    }


def _verify_snapshot(snapshot: Mapping[str, Any]) -> None:
    path = Path(str(snapshot["path"]))
    descriptor = _open_single_link(path, os.O_RDONLY, str(snapshot["label"]))
    try:
        current = _validate_opened_single_link(
            path, descriptor, str(snapshot["label"])
        )
    finally:
        os.close(descriptor)
    if tuple(snapshot["signature"]) != _stat_signature(current):
        raise RuntimeError("%s changed during the experiment" % snapshot["label"])


def _reject_output_input_aliases(output: Path,
                                 snapshots: Sequence[Mapping[str, Any]]) -> None:
    candidates = (
        output,
        output.with_name(output.name + ".lock"),
        output.with_name(output.name + ".complete.json"),
    )
    for candidate in candidates:
        if not candidate.exists() and not candidate.is_symlink():
            continue
        for snapshot in snapshots:
            try:
                aliases = os.path.samefile(candidate, snapshot["path"])
            except OSError as exc:
                raise ValueError("could not verify output/input aliasing") from exc
            if aliases:
                raise ValueError(
                    "compiler output/lock/completion must not alias %s"
                    % snapshot["label"]
                )


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
        source_batch_id="",
        query_sha256="",
        circuit_sha256=digest,
        family=family,
        size=size,
        circ=circ,
        roots=roots,
        order=order_tuple,
    )


def cache_instance(path: Path, root_limit: Optional[int] = None,
                   expected_commit: Optional[str] = None,
                   expected_batch_id: Optional[str] = None,
                   allowed_query_sha256: Optional[Sequence[str]] = None,
                   ) -> CircuitInstance:
    path = Path(os.path.abspath(os.fspath(Path(path).expanduser())))
    metadata_path = Path(path).with_suffix(".json")
    if not os.path.lexists(metadata_path):
        raise ValueError("canonical cache sidecar is missing: %s" % metadata_path)
    # One FD-pair generation supplies the bytes, identities, and snapshots.
    # Opening the paths independently before this call can splice sidecar B to
    # payload/snapshot A when a producer atomically publishes a new sidecar.
    loaded = circuit_cache.load_sidecar(metadata_path, path)
    payload_snapshot = loaded["payload_snapshot"]
    sidecar_snapshot = loaded["sidecar_snapshot"]
    _verify_snapshot(payload_snapshot)
    _verify_snapshot(sidecar_snapshot)
    observations = loaded["observations"]
    matching = [
        item for item in observations
        if (expected_commit is None or str(item.get("commit", "")) == expected_commit)
        and (
            expected_batch_id is None
            or str(item.get("batch_id", "")) == expected_batch_id
        )
    ]
    if not matching:
        identities = sorted({
            (str(item.get("commit", "")), str(item.get("batch_id", "")))
            for item in observations
        })
        raise ValueError(
            "cache has producer identities %r, not requested commit/batch for %s"
            % (identities, path))
    selected = sorted(
        matching, key=lambda item: item["producer_observation_sha256"]
    )[0]
    circuit_sha = str(selected.get("circuit_sha256", ""))
    query_sha = str(selected.get("query_sha256", ""))
    source_commit = str(selected.get("commit", ""))
    batch_id = str(selected.get("batch_id", ""))
    if not circuit_cache.SHA256.fullmatch(query_sha):
        raise ValueError("canonical cache sidecar has no valid query_sha256: %s" % metadata_path)
    if not circuit_cache.COMMIT.fullmatch(source_commit):
        raise ValueError("canonical cache sidecar has no full frozen commit: %s" % metadata_path)
    if not circuit_cache.SHA256.fullmatch(batch_id):
        raise ValueError("canonical cache sidecar has no frozen batch_id: %s" % metadata_path)
    allowed = set(allowed_query_sha256 or ())
    if allowed and query_sha not in allowed:
        raise ValueError(
            "canonical cache query is outside the explicit allowlist: %s" % query_sha
        )
    expected_stem = "%s-%s" % (query_sha, circuit_sha)
    if Path(path).stem != expected_stem:
        raise ValueError(
            "canonical cache filename does not match sidecar hashes: %s != %s"
            % (Path(path).stem, expected_stem))
    descriptor = loaded
    if descriptor["circuit_sha256"] != circuit_sha:
        raise ValueError("selected producer observation disagrees with payload")
    if payload_snapshot["sha256"] != circuit_sha:
        raise ValueError("cache payload snapshot disagrees with its sealed descriptor")
    try:
        text = descriptor["payload"].decode("utf-8", "strict")
    except UnicodeDecodeError as exc:
        raise ValueError("canonical cache payload is not UTF-8") from exc
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
        source_batch_id=batch_id,
        query_sha256=query_sha,
        circuit_sha256=descriptor["circuit_sha256"],
        family="cache",
        size=descriptor["circuit_triples"],
        circ=circ,
        roots=roots,
        order=order,
        source_sidecar_sha256=sidecar_snapshot["sha256"],
        source_sidecar_bytes=sidecar_snapshot["bytes"],
        source_observation_sha256=selected["producer_observation_sha256"],
        circuit_bytes=descriptor["circuit_bytes"],
        artifact_snapshots=(payload_snapshot, sidecar_snapshot),
    )


def discover_cache_paths(values: Sequence[str]) -> List[Path]:
    paths = []
    for value in values:
        path = Path(os.path.abspath(os.fspath(Path(value).expanduser())))
        if path.is_symlink():
            raise ValueError("canonical cache input must not be a symlink: %s" % path)
        if path.is_dir():
            paths.extend(sorted(path.glob("*.nt")))
        else:
            paths.append(path)
    unique = []
    seen = set()
    for path in paths:
        resolved = str(path.absolute())
        if resolved not in seen:
            if path.is_symlink() or not path.is_file() or path.stat().st_nlink != 1:
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
        def head() -> str:
            return subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=HERE, check=True,
                capture_output=True, text=True, timeout=10,
            ).stdout.strip()

        first = head()
        dirty = subprocess.run(
            ["git", "status", "--porcelain=v1", "--untracked-files=all",
             "--ignore-submodules=none"], cwd=HERE, check=True,
            capture_output=True, text=True, timeout=10,
        ).stdout
        second = head()
    except (OSError, subprocess.SubprocessError):
        return "unknown", "unknown"
    if first != second:
        return "unknown", "unknown"
    return first, "true" if dirty else "false"


def _validate_git_identity(commit: str, dirty: str, allow_dirty: bool) -> None:
    if re.fullmatch(r"[0-9a-f]{40}", commit) is None:
        raise RuntimeError("formal compiler experiment requires a full Git commit")
    if dirty not in ("true", "false"):
        raise RuntimeError("could not establish a stable Git identity")
    if dirty != "false" and not allow_dirty:
        raise RuntimeError("formal compiler experiment requires a clean worktree")


def _validate_no_hidden_index_bits(repo: Path = REPO_ROOT) -> None:
    """Reject tracked paths hidden by assume-unchanged/skip-worktree flags."""
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), "ls-files", "-v", "-z"],
            check=True, capture_output=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError("could not audit tracked Git index flags") from exc
    hidden = []
    for record in result.stdout.split(b"\0"):
        if not record:
            continue
        if len(record) < 3 or record[1:2] != b" ":
            raise RuntimeError("git ls-files -v emitted an unparseable record")
        marker = record[:1]
        if marker == b"S" or marker.islower():
            hidden.append(record[2:])
    if hidden:
        raise RuntimeError(
            "formal compiler runs reject %d tracked file(s) hidden by "
            "assume-unchanged/skip-worktree flags" % len(hidden)
        )


def _discover_cudd_runtime() -> Tuple[str, Dict[str, Dict[str, Any]]]:
    try:
        import dd
        import dd.cudd
    except ImportError as exc:
        raise RuntimeError(
            "production CUDD unavailable; install reference/requirements-production.txt"
        ) from exc
    cudd = _snapshot_file(Path(dd.cudd.__file__), CUDD_TOOL_NAME)
    python_runtime = _snapshot_file(Path(sys.executable), PYTHON_TOOL_NAME)
    version = "dd=%s;cudd=%s" % (
        getattr(dd, "__version__", "unknown"),
        getattr(dd.cudd, "__version__", "unknown"),
    )
    return version, {
        CUDD_TOOL_NAME: cudd,
        PYTHON_TOOL_NAME: python_runtime,
    }


def _resolve_frozen_identity(args: argparse.Namespace) -> None:
    if args.frozen_inputs is None:
        if not args.allow_unfrozen:
            raise RuntimeError(
                "formal compiler runs require --frozen-inputs; "
                "use --allow-unfrozen only for exploration")
        if re.fullmatch(r"[0-9a-f]{64}", str(args.batch_id or "")) is None:
            raise RuntimeError("unfrozen exploration requires a 64-hex --batch-id")
        args.frozen_inputs_sha256 = ""
        args.protocol = args.expected_protocol or "unfrozen-exploration"
        args.formal_run = False
        args._frozen_document = None
        args._frozen_snapshot = None
        return
    if args.allow_unfrozen:
        raise RuntimeError("--allow-unfrozen cannot be combined with --frozen-inputs")
    if not args.expected_protocol:
        raise RuntimeError("--expected-protocol is required with --frozen-inputs")
    snapshot = _snapshot_file(args.frozen_inputs, "compiler frozen inputs")
    try:
        document = freeze_inputs.load_frozen_batch(
            args.frozen_inputs,
            expected_commit=args.git_commit,
            expected_protocol=args.expected_protocol,
            required_tools=tuple(sorted(args._actual_tools)),
            require_formal=args.formal_run,
        )
    except freeze_inputs.FreezeError as exc:
        raise RuntimeError("invalid frozen inputs: %s" % exc) from exc
    _verify_snapshot(snapshot)
    for name, observed in args._actual_tools.items():
        try:
            frozen = freeze_inputs.frozen_tool(document, name)
        except freeze_inputs.FreezeError as exc:
            raise RuntimeError("frozen runtime is incomplete: %s" % exc) from exc
        if (
            frozen["sha256"] != observed["sha256"]
            or frozen["bytes"] != observed["bytes"]
        ):
            raise RuntimeError(
                "frozen %s differs from the active runtime" % name
            )
    if args.formal_run and args.profile == REAL_CACHE_PROFILE:
        frozen_queries = {
            query["query_sha256"]
            for manifest in document["identity"]["manifests"]
            for query in manifest["queries"]
        }
        if not set(args.cache_query_sha256).issubset(frozen_queries):
            raise RuntimeError(
                "real-cache query allowlist is not present in frozen manifests"
            )
    observed = document["batch_id"]
    if args.batch_id and args.batch_id != observed:
        raise RuntimeError("--batch-id disagrees with frozen inputs")
    args.batch_id = observed
    args.frozen_inputs_sha256 = snapshot["sha256"]
    args.protocol = document["identity"]["protocol"]
    args._frozen_document = document
    args._frozen_snapshot = snapshot


def _validate_formal_configuration(args: argparse.Namespace) -> None:
    """Publication profiles are code-defined; arbitrary knobs are exploration."""
    if not args.formal_run:
        return
    unsafe = {
        "allow_unsafe_output": args.allow_unsafe_output,
        "allow_cache_commit_mismatch": args.allow_cache_commit_mismatch,
        "continue_after_failure": args.continue_after_failure,
        "allow_unfrozen": args.allow_unfrozen,
        "allow_dirty": args.allow_dirty,
    }
    enabled = sorted(name for name, value in unsafe.items() if value)
    if enabled:
        raise ValueError(
            "formal compiler runs reject exploratory switches: %s"
            % ", ".join(enabled)
        )
    expected = {
        "expected_protocol": FORMAL_PROTOCOL,
        "seed": FORMAL_SEED,
        "warmups": FORMAL_WARMUPS,
        "runs": FORMAL_RUNS,
        "timeout": FORMAL_TIMEOUT_S,
        "include_dynamic": True,
    }
    for field, value in expected.items():
        if getattr(args, field) != value:
            raise ValueError(
                "formal compiler configuration fixes %s=%r" % (field, value)
            )
    families = tuple(
        item.strip() for item in args.families.split(",") if item.strip()
    )
    if args.profile == SYNTHETIC_PROFILE:
        profile_expected = {
            "no_synthetic": False,
            "cache": [],
            "cache_query_sha256": (),
            "families": FORMAL_SYNTHETIC_FAMILIES,
            "sizes": list(FORMAL_SYNTHETIC_SIZES),
            "roots": FORMAL_SYNTHETIC_ROOTS,
            "root_limit": None,
        }
    elif args.profile == REAL_CACHE_PROFILE:
        if not args.cache:
            raise ValueError("formal real-cache profile requires --cache")
        if not args.cache_query_sha256:
            raise ValueError(
                "formal real-cache profile requires an explicit query SHA-256 allowlist"
            )
        profile_expected = {
            "no_synthetic": True,
            "families": FORMAL_SYNTHETIC_FAMILIES,
            "sizes": list(FORMAL_SYNTHETIC_SIZES),
            "roots": FORMAL_SYNTHETIC_ROOTS,
            "root_limit": None,
        }
    else:  # parser normally prevents this; keep direct API callers safe.
        raise ValueError("unknown compiler publication profile")
    for field, value in profile_expected.items():
        if field == "families":
            observed = families
        elif field == "cache_query_sha256":
            observed = tuple(getattr(args, field))
        else:
            observed = getattr(args, field)
        if observed != value:
            raise ValueError(
                "formal %s profile fixes %s=%r"
                % (args.profile, field, value)
            )


def _canonical_digest(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _validate_output_destination(path: Path, allow_unsafe: bool) -> Path:
    candidate = Path(path).expanduser()
    if not candidate.name or candidate.name in (".", ".."):
        raise ValueError("output path must name one file")
    candidate.parent.mkdir(parents=True, exist_ok=True)
    resolved = candidate.parent.resolve(strict=True) / candidate.name
    if resolved.exists() or resolved.is_symlink():
        try:
            current = os.lstat(resolved)
        except OSError as exc:
            raise ValueError("output path is unstable") from exc
        if not stat.S_ISREG(current.st_mode) or current.st_nlink != 1:
            raise ValueError("output must not be a symlink, hardlink, or special file")
    if allow_unsafe:
        return resolved
    repo = REPO_ROOT.resolve()
    try:
        relative = resolved.relative_to(repo)
    except ValueError:
        return resolved
    tracked = subprocess.run(
        ["git", "-C", str(repo), "ls-files", "--error-unmatch", "--", str(relative)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    ignored = subprocess.run(
        ["git", "-C", str(repo), "check-ignore", "-q", "--no-index", "--",
         str(relative)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    if tracked.returncode == 0 or ignored.returncode != 0:
        raise RuntimeError(
            "formal output inside the repository must be untracked and Git-ignored")
    return resolved


@contextlib.contextmanager
def _invocation_lock(output: Path, timeout: float = 300.0):
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    lock_path = output.with_name(output.name + ".lock")
    directory_descriptor = _open_stable_directory(
        output.parent, "compiler output directory lock"
    )
    descriptor = None
    started = time.monotonic()
    try:
        _acquire_flock(
            directory_descriptor, started, timeout,
            "compiler output directory lock",
        )
        _validate_opened_directory(
            output.parent, directory_descriptor, "compiler output directory lock"
        )
        descriptor = _open_single_link(
            lock_path, os.O_RDWR | os.O_CREAT, "compiler invocation lock"
        )
        _acquire_flock(
            descriptor, started, timeout, "compiler checkpoint invocation lock"
        )
        _validate_opened_single_link(
            lock_path, descriptor, "compiler invocation lock"
        )
        if output.exists() or output.is_symlink():
            check = _open_single_link(
                output, os.O_RDONLY, "compiler checkpoint"
            )
            os.close(check)
        try:
            yield
        finally:
            _validate_opened_single_link(
                lock_path, descriptor, "compiler invocation lock"
            )
            _validate_opened_directory(
                output.parent, directory_descriptor,
                "compiler output directory lock",
            )
    finally:
        if descriptor is not None:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)
        try:
            fcntl.flock(directory_descriptor, fcntl.LOCK_UN)
        finally:
            os.close(directory_descriptor)


def _prepare_attempt(instance: CircuitInstance) -> Tuple[Dict[str, Any], float]:
    """Perform the identical source/order preparation for every physical mode."""
    started = time.perf_counter()
    ordered_keys = tuple(sorted(instance.roots, key=_stable_text))
    global_support = set(compiler.deterministic_order(instance.circ, instance.roots))
    if len(instance.order) != len(set(instance.order)):
        raise ValueError("frozen input order contains duplicates")
    missing = global_support - set(instance.order)
    if missing:
        raise ValueError("frozen input order omits source support")
    local_orders: Dict[str, Tuple[str, ...]] = {}
    for key in ordered_keys:
        root = instance.roots[key]
        support = set(compiler.deterministic_order(instance.circ, {key: root}))
        local_orders[key] = tuple(
            variable for variable in instance.order if variable in support
        )
        if set(local_orders[key]) != support:
            raise ValueError("local order does not cover one root support")
    elapsed = (time.perf_counter() - started) * 1000.0
    return {"ordered_keys": ordered_keys, "local_orders": local_orders}, elapsed


def _retained_attempt(instance: CircuitInstance, weights: Mapping[str, float],
                      mode: str, dynamic: bool, backend: str,
                      common_prepare_ms: float) -> Dict[str, Any]:
    compiler_mode = "shared" if mode == "shared" else "per-root"
    batch = compiler.compile_many(
        instance.circ, instance.roots, mode=compiler_mode, backend=backend,
        order=instance.order, dynamic_reordering=dynamic,
    )
    wmc_started = time.perf_counter()
    probabilities = batch.wmc_many(weights)
    wmc_wall_ms = (time.perf_counter() - wmc_started) * 1000.0
    metrics = dict(batch.metrics)
    teardown_started = time.perf_counter()
    del batch
    gc.collect()
    teardown_ms = (time.perf_counter() - teardown_started) * 1000.0
    probability_sum, checksum, checksum_12dp = probability_checksums(probabilities)
    del probabilities
    prepare_ms = common_prepare_ms + float(metrics["prepare_ms"])
    backend_compile_ms = float(metrics["backend_compile_ms"])
    inspect_ms = float(metrics["inspect_ms"])
    source_to_result_ms = common_prepare_ms + float(metrics["source_to_result_ms"])
    memory_aggregation = (
        "single-live-manager; manager/current/peak are that manager"
        if mode == "shared"
        else (
            "simultaneously-retained managers; memory/current/peak-upper-bound "
            "are sums and peak-max is the largest manager"
        )
    )
    return {
        "backend": backend,
        "backend_version": _backend_identity(backend),
        "prepare_ms": prepare_ms,
        "backend_compile_ms": backend_compile_ms,
        "inspect_ms": inspect_ms,
        "source_to_result_ms": source_to_result_ms,
        "compile_ms": source_to_result_ms,
        "compile_wall_ms": source_to_result_ms,
        "wmc_ms": metrics["wmc_ms"],
        "wmc_wall_ms": wmc_wall_ms,
        "teardown_ms": teardown_ms,
        "timing_scope": (
            "common root/support/order preparation | compiler source preparation | "
            "native manager build | compiled-structure inspection | WMC separate | "
            "explicit manager teardown"
        ),
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
        "memory_aggregation": memory_aggregation,
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
                        dynamic: bool, backend: str,
                        prepared: Mapping[str, Any],
                        common_prepare_ms: float) -> Dict[str, Any]:
    prepare_ms = common_prepare_ms
    backend_compile_ms = inspect_ms = source_to_result_ms = 0.0
    wmc_ms = wmc_wall_ms = 0.0
    teardown_ms = 0.0
    node_sum = root_node_sum = 0
    manager_memory_max: Optional[int] = None
    peak_live_max: Optional[int] = None
    current_nodes_max: Optional[int] = None
    reorderings = 0
    reordering_seconds = 0.0
    probabilities = {}
    ordered_keys = prepared["ordered_keys"]

    for key in ordered_keys:
        root = instance.roots[key]
        # This plan was derived in the common preparation boundary above.  In
        # particular, sequential mode cannot move support/order work outside
        # the source-to-result evidence while the other modes retain it.
        local_order = prepared["local_orders"][key]
        batch = compiler.compile_many(
            instance.circ, {key: root}, mode="shared", backend=backend,
            order=local_order, dynamic_reordering=dynamic,
        )
        metrics = dict(batch.metrics)
        prepare_ms += float(metrics["prepare_ms"])
        backend_compile_ms += float(metrics["backend_compile_ms"])
        inspect_ms += float(metrics["inspect_ms"])
        source_to_result_ms += float(metrics["source_to_result_ms"])
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
    source_to_result_ms += common_prepare_ms
    return {
        "backend": backend,
        "backend_version": _backend_identity(backend),
        "prepare_ms": prepare_ms,
        "backend_compile_ms": backend_compile_ms,
        "inspect_ms": inspect_ms,
        "source_to_result_ms": source_to_result_ms,
        "compile_ms": source_to_result_ms,
        "compile_wall_ms": source_to_result_ms,
        "wmc_ms": wmc_ms,
        "wmc_wall_ms": wmc_wall_ms,
        "teardown_ms": teardown_ms,
        "timing_scope": (
            "common root/support/order preparation | summed per-root compiler "
            "source preparation | summed native manager builds | summed inspection | "
            "summed WMC | one-manager-at-a-time teardown"
        ),
        "compiled_nodes_unique": node_sum,
        "compiled_nodes_sum_roots": root_node_sum,
        "sharing_savings_nodes": 0,
        "sharing_ratio": 1.0,
        "manager_count": len(instance.roots),
        "concurrent_manager_count": 1 if instance.roots else 0,
        "manager_memory_bytes": manager_memory_max,
        "manager_memory_semantics": "max-of-one-live-sequential-manager",
        "memory_aggregation": (
            "one live manager at a time; memory/current/peak fields are maxima "
            "across roots while compiled-node and reordering counts are sums"
        ),
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
    if backend == "cudd":
        # Native-extension process start-up is not knowledge compilation.
        # Every fresh attempt worker imports it before either the internal or
        # wall-clock compile boundary, keeping all physical modes comparable.
        import dd.cudd  # noqa: F401
    started = time.perf_counter()
    prepared, common_prepare_ms = _prepare_attempt(instance)
    if mode == "per-root-sequential":
        result = _sequential_attempt(
            instance, weights, dynamic, backend, prepared, common_prepare_ms
        )
    else:
        result = _retained_attempt(
            instance, weights, mode, dynamic, backend, common_prepare_ms
        )
    attempt_worker_ms = (time.perf_counter() - started) * 1000.0
    attributed = math.fsum(float(result[field]) for field in (
        "source_to_result_ms", "wmc_wall_ms", "teardown_ms"
    ))
    result["attempt_worker_ms"] = attempt_worker_ms
    result["timing_unattributed_ms"] = max(0.0, attempt_worker_ms - attributed)
    result["process_max_rss_bytes"] = _rss_bytes()
    return result


def _attempt_worker(send, ready_send, instance: CircuitInstance,
                    weights: Mapping[str, float], mode: str,
                    reordering: str, backend: str) -> None:
    try:
        os.setsid()
        ready_send.send(("ready", os.getpid()))
        ready_send.close()
        payload = {"status": "ok"}
        payload.update(execute_attempt(instance, weights, mode, reordering, backend))
    except MemoryError as exc:
        detail = " ".join(str(exc).splitlines())[:500]
        payload = {
            "status": "oom",
            "notes": detail or "worker raised MemoryError",
        }
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


def _process_group_exists(pgid: int) -> bool:
    proc_root = Path("/proc")
    if proc_root.is_dir():
        observed = False
        try:
            entries = list(proc_root.iterdir())
        except OSError:
            entries = []
        for entry in entries:
            if not entry.name.isdigit():
                continue
            try:
                raw = (entry / "stat").read_text(encoding="ascii")
                fields = raw[raw.rfind(")") + 2:].split()
                state, process_group = fields[0], int(fields[2])
            except (OSError, ValueError, IndexError):
                continue
            if process_group == pgid:
                observed = True
                if state != "Z":
                    return True
        if observed:
            return False
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _cleanup_attempt(process: multiprocessing.Process, group_ready: bool,
                     timed_out: bool) -> Dict[str, Any]:
    started = time.monotonic()
    actions = []
    pgid = process.pid
    if timed_out:
        if group_ready:
            try:
                os.killpg(pgid, signal.SIGTERM)
                actions.append("sigterm-process-group")
            except ProcessLookupError:
                pass
        elif process.is_alive():
            process.terminate()
            actions.append("sigterm-worker-before-setsid")
        process.join(0.75)
    group_alive = group_ready and _process_group_exists(pgid)
    if process.is_alive() or group_alive:
        if group_ready:
            try:
                os.killpg(pgid, signal.SIGKILL)
                actions.append("sigkill-process-group")
            except ProcessLookupError:
                pass
        elif process.is_alive() and hasattr(process, "kill"):
            process.kill()
            actions.append("sigkill-worker-before-setsid")
        process.join(0.75)
    deadline = time.monotonic() + 0.5
    while group_ready and _process_group_exists(pgid) and time.monotonic() < deadline:
        time.sleep(0.01)
    process.join(0)
    reaped = not process.is_alive() and (
        not group_ready or not _process_group_exists(pgid)
    )
    return {
        "cleanup_ms": (time.monotonic() - started) * 1000.0,
        "cleanup_action": "+".join(actions) if actions else "none",
        "process_group_reaped": reaped,
    }


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
    ready_receive, ready_send = context.Pipe(duplex=False)
    process = context.Process(
        target=_attempt_worker,
        args=(send, ready_send, instance, dict(weights), mode, reordering, backend),
    )
    started = time.monotonic()
    process.start()
    send.close()
    ready_send.close()
    remaining = max(0.0, timeout - (time.monotonic() - started))
    group_ready = False
    if ready_receive.poll(remaining):
        try:
            state, _detail = ready_receive.recv()
        except EOFError:
            state = "worker-exit-before-setsid"
        group_ready = state == "ready"
    ready_receive.close()
    if not group_ready and process.is_alive():
        try:
            group_ready = os.getpgid(process.pid) == process.pid
        except ProcessLookupError:
            pass
    remaining = max(0.0, timeout - (time.monotonic() - started))
    process.join(remaining)
    timed_out = process.is_alive() or (time.monotonic() - started) >= timeout
    cleanup = _cleanup_attempt(process, group_ready, timed_out)
    if timed_out:
        elapsed_ms = (time.monotonic() - started) * 1000.0
        receive.close()
        payload = {
            "status": "timeout",
            "attempt_wall_ms": elapsed_ms,
            "notes": "attempt exceeded %.6gs killable deadline" % timeout,
            **cleanup,
        }
        if not cleanup["process_group_reaped"]:
            payload["status"] = "cleanup-error"
            payload["notes"] += "; process group survived termination"
        return payload
    if receive.poll(0.2):
        try:
            payload = receive.recv()
        except EOFError:
            payload = {
                "status": "worker-exit",
                "notes": "worker exited %r without a result" % process.exitcode,
            }
    elif process.exitcode is not None and (
        process.exitcode < 0 or process.exitcode in (9, 137, 143)
    ):
        payload = {
            "status": "killed-signal",
            "notes": "worker was killed without evidence of memory exhaustion",
        }
    else:
        payload = {
            "status": "worker-exit",
            "notes": "worker exited %r without a result" % process.exitcode,
        }
    receive.close()
    payload["attempt_wall_ms"] = (time.monotonic() - started) * 1000.0
    payload.update(cleanup)
    if not cleanup["process_group_reaped"]:
        prior = payload.get("status", "unknown")
        payload["status"] = "cleanup-error"
        payload["notes"] = (
            (payload.get("notes") or "")
            + "; process-group cleanup failed after status=%s" % prior
        ).lstrip("; ")
    return payload


def _blank_row(instance: CircuitInstance, mode: str, reordering: str,
               phase: str, rep: int, args: argparse.Namespace,
               weights_sha: str) -> Dict[str, Any]:
    gates, edges = _source_stats(instance.circ, instance.roots.values())
    return {
        field: "" for field in FIELDS
    } | {
        "schema": SCHEMA,
        "batch_id": getattr(args, "batch_id", ""),
        "frozen_inputs_sha256": getattr(args, "frozen_inputs_sha256", ""),
        "protocol": getattr(args, "protocol", ""),
        "run_config_sha256": getattr(args, "run_config_sha256", ""),
        "formal_run": getattr(args, "formal_run", False),
        "profile": getattr(args, "profile", ""),
        "failure_policy": getattr(args, "failure_policy", FAILURE_POLICY),
        "git_commit": getattr(args, "git_commit", "unknown"),
        "git_dirty": getattr(args, "git_dirty", "unknown"),
        "backend": "cudd",
        "backend_version": getattr(args, "backend_version", "unknown"),
        "cudd_extension_sha256": getattr(args, "cudd_extension_sha256", ""),
        "cudd_extension_bytes": getattr(args, "cudd_extension_bytes", ""),
        "python_runtime_sha256": getattr(args, "python_runtime_sha256", ""),
        "python_runtime_bytes": getattr(args, "python_runtime_bytes", ""),
        "instance_id": instance.instance_id,
        "source": instance.source,
        "source_path": instance.source_path,
        "source_commit": instance.source_commit,
        "source_batch_id": instance.source_batch_id,
        "query_sha256": instance.query_sha256,
        "source_sidecar_sha256": instance.source_sidecar_sha256,
        "source_sidecar_bytes": instance.source_sidecar_bytes,
        "source_observation_sha256": instance.source_observation_sha256,
        "circuit_sha256": instance.circuit_sha256,
        "circuit_bytes": instance.circuit_bytes,
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
        "batch_id", "run_config_sha256", "formal_run", "profile",
        "failure_policy", "git_commit", "git_dirty",
        "backend", "backend_version", "instance_id",
        "source_commit", "source_batch_id", "query_sha256",
        "source_sidecar_sha256", "source_observation_sha256",
        "mode", "reordering", "phase", "rep", "seed", "weights_sha256",
        "warmups", "runs", "timeout_s",
    ))


def _baseline_key(row: Mapping[str, Any]) -> Tuple[str, ...]:
    return tuple(str(row.get(field, "")) for field in (
        "batch_id", "run_config_sha256", "git_commit", "git_dirty",
        "backend", "backend_version", "instance_id",
        "circuit_sha256", "weights_sha256", "input_order_sha256",
    ))


def _read_checkpoint_payload(path: Path, *, repair: bool,
                             with_signature: bool = False) -> Any:
    path = Path(path)
    if not path.exists() and not path.is_symlink():
        return (b"", None) if with_signature else b""
    descriptor = _open_single_link(
        path, os.O_RDWR if repair else os.O_RDONLY, "compiler checkpoint"
    )
    try:
        payload = bytearray()
        while True:
            block = os.read(descriptor, 1024 * 1024)
            if not block:
                break
            payload.extend(block)
        if repair and payload and not payload.endswith((b"\n", b"\r")):
            newline = payload.rfind(b"\n")
            length = newline + 1 if newline >= 0 else 0
            os.ftruncate(descriptor, length)
            os.fsync(descriptor)
            del payload[length:]
            _fsync_directory(path.parent)
        current = _validate_opened_single_link(
            path, descriptor, "compiler checkpoint"
        )
        result = bytes(payload)
        return (result, _stat_signature(current)) if with_signature else result
    finally:
        os.close(descriptor)


def _serialize_checkpoint(rows: Sequence[Mapping[str, Any]]) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(
        buffer,
        fieldnames=FIELDS,
        extrasaction="raise",
        dialect="excel",
        lineterminator="\r\n",
    )
    writer.writeheader()
    for row in rows:
        writer.writerow({field: row[field] for field in FIELDS})
    return buffer.getvalue().encode("utf-8")


def load_checkpoint(path: Path, *, repair: bool = True) -> List[Dict[str, str]]:
    payload = _read_checkpoint_payload(path, repair=repair)
    if not payload:
        return []
    try:
        text = payload.decode("utf-8", "strict")
        reader = csv.DictReader(io.StringIO(text, newline=""), strict=True)
        if reader.fieldnames != FIELDS:
            raise ValueError("checkpoint schema mismatch: %s" % path)
        rows = list(reader)
    except (UnicodeError, csv.Error) as exc:
        raise ValueError("checkpoint is not strict UTF-8 CSV") from exc
    if any(
        None in row
        or set(row) != set(FIELDS)
        or any(row[field] is None for field in FIELDS)
        for row in rows
    ):
        raise ValueError("checkpoint rows do not have the exact declared width")
    try:
        canonical = _serialize_checkpoint(rows)
    except (KeyError, TypeError, ValueError, csv.Error) as exc:
        raise ValueError("checkpoint cannot be canonically serialized") from exc
    if payload != canonical:
        raise ValueError("checkpoint is not canonical protocol CSV")
    seen = set()
    for row in rows:
        if row.get("schema") != SCHEMA:
            raise ValueError("checkpoint row schema mismatch: %s" % path)
        if row.get("status") not in TERMINAL:
            raise ValueError("checkpoint contains a non-terminal/unknown status")
        key = _row_key(row)
        if key in seen:
            raise ValueError("checkpoint contains a duplicate terminal row")
        seen.add(key)
    return rows


def append_checkpoint(path: Path, row: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing, signature = _read_checkpoint_payload(
        path, repair=True, with_signature=True
    )
    encoded = _serialize_checkpoint([
        {field: row.get(field, "") for field in FIELDS}
    ])
    if existing:
        header_end = encoded.find(b"\n") + 1
        encoded = encoded[header_end:]
    flags = os.O_WRONLY | os.O_APPEND
    if signature is None:
        flags |= os.O_CREAT | os.O_EXCL
    descriptor = _open_single_link(path, flags, "compiler checkpoint")
    try:
        if (
            signature is not None
            and tuple(signature) != _stat_signature(os.fstat(descriptor))
        ):
            raise ValueError("compiler checkpoint changed before append")
        view = memoryview(encoded)
        while view:
            written = os.write(descriptor, view)
            view = view[written:]
        os.fsync(descriptor)
        _validate_opened_single_link(path, descriptor, "compiler checkpoint")
    finally:
        os.close(descriptor)
    _fsync_directory(path.parent)


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


def _number(row: Mapping[str, Any], field: str, *, integer: bool = False,
            positive: bool = False) -> Any:
    raw = row.get(field, "")
    if type(raw) is not str or not raw or raw.strip() != raw:
        raise ValueError("checkpoint metric %s is not canonical" % field)
    if integer:
        if re.fullmatch(r"0|[1-9][0-9]*", raw) is None:
            raise ValueError(
                "checkpoint integer metric %s is not canonical" % field
            )
        value = int(raw)
        if value > MAX_INTEGER_METRIC:
            raise ValueError("checkpoint integer metric %s is too large" % field)
    else:
        if re.fullmatch(
            r"(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:e[+-]?[0-9]+)?", raw
        ) is None:
            raise ValueError("checkpoint metric %s is not canonical" % field)
        try:
            value = float(raw)
            exact = Decimal(raw)
        except (ValueError, InvalidOperation) as exc:
            raise ValueError("checkpoint requires finite metric %s" % field) from exc
        if not math.isfinite(value) or exact != Decimal(str(value)):
            raise ValueError("checkpoint metric %s is not canonical" % field)
    if not math.isfinite(value) or value < 0 or (positive and value <= 0):
        raise ValueError("checkpoint metric %s is outside its valid range" % field)
    return value


def _checksum(row: Mapping[str, Any], field: str) -> None:
    if re.fullmatch(r"[0-9a-f]{64}", str(row.get(field, ""))) is None:
        raise ValueError("checkpoint requires lowercase SHA-256 field %s" % field)


def _validate_attempt_metrics(row: Mapping[str, Any]) -> None:
    status = str(row.get("status", ""))
    if status == "not-run":
        if row.get("parity") != "unverified" or row.get("notes") != NOT_RUN_NOTE:
            raise ValueError("not-run checkpoint row has forged evidence")
        return
    attempt_wall = _number(row, "attempt_wall_ms")
    _number(row, "cleanup_ms")
    if not str(row.get("cleanup_action", "")):
        raise ValueError("attempt row lacks cleanup_action")
    expected_reaped = "False" if status == "cleanup-error" else "True"
    if str(row.get("process_group_reaped", "")) != expected_reaped:
        raise ValueError("checkpoint process-group cleanup evidence is inconsistent")
    if status != "ok":
        if not str(row.get("notes", "")):
            raise ValueError("non-ok attempt row lacks diagnostics")
        if row.get("parity") != "unverified":
            raise ValueError("non-ok attempt row claims semantic parity")
        return

    worker = _number(row, "attempt_worker_ms")
    if attempt_wall + 1e-6 < worker:
        raise ValueError("parent attempt wall is smaller than worker wall")
    prepare = _number(row, "prepare_ms")
    backend = _number(row, "backend_compile_ms")
    inspect = _number(row, "inspect_ms")
    source = _number(row, "source_to_result_ms")
    unattributed = _number(row, "timing_unattributed_ms")
    compile_ms = _number(row, "compile_ms")
    compile_wall = _number(row, "compile_wall_ms")
    wmc_ms = _number(row, "wmc_ms")
    wmc_wall = _number(row, "wmc_wall_ms")
    teardown = _number(row, "teardown_ms")
    phase_sum = prepare + backend + inspect
    phase_tolerance = max(1.0, source * 0.05)
    if source + 1e-6 < phase_sum or source - phase_sum > phase_tolerance:
        raise ValueError("source-to-result timing does not match its phase boundaries")
    for name, value in (("compile_ms", compile_ms), ("compile_wall_ms", compile_wall)):
        if abs(value - source) > max(1e-6, source * 1e-9):
            raise ValueError("%s is not the source-to-result compatibility alias" % name)
    if wmc_ms > wmc_wall + max(1.0, wmc_wall * 0.05):
        raise ValueError("internal WMC time exceeds its wall boundary")
    attributed = source + wmc_wall + teardown + unattributed
    if abs(worker - attributed) > max(1e-6, worker * 1e-9):
        raise ValueError("worker timing attribution identity is inconsistent")
    if not str(row.get("timing_scope", "")):
        raise ValueError("successful row lacks an explicit timing scope")

    unique = _number(row, "compiled_nodes_unique", integer=True)
    root_sum = _number(row, "compiled_nodes_sum_roots", integer=True)
    savings = _number(row, "sharing_savings_nodes", integer=True)
    ratio = _number(row, "sharing_ratio")
    if savings != root_sum - unique:
        raise ValueError("sharing_savings_nodes does not match node evidence")
    expected_ratio = root_sum / unique if unique else 1.0
    if abs(ratio - expected_ratio) > 1e-12 * max(1.0, abs(expected_ratio)):
        raise ValueError("sharing_ratio does not match node evidence")
    roots = int(_number(row, "root_count", integer=True, positive=True))
    managers = int(_number(row, "manager_count", integer=True, positive=True))
    concurrent = int(_number(
        row, "concurrent_manager_count", integer=True, positive=True
    ))
    mode = row.get("mode")
    if mode == "shared":
        expected_managers, expected_concurrent = 1, 1
        expected_semantics = "one-shared-manager"
    elif mode == "per-root-retained":
        expected_managers = expected_concurrent = roots
        expected_semantics = "sum-of-simultaneously-retained-independent-managers"
    elif mode == "per-root-sequential":
        expected_managers, expected_concurrent = roots, 1
        expected_semantics = "max-of-one-live-sequential-manager"
    else:
        raise ValueError("checkpoint contains an unknown physical mode")
    if (managers, concurrent) != (expected_managers, expected_concurrent):
        raise ValueError("manager cardinality disagrees with physical mode")
    if row.get("manager_memory_semantics") != expected_semantics:
        raise ValueError("manager memory semantics disagree with physical mode")
    if not str(row.get("memory_aggregation", "")):
        raise ValueError("successful row lacks explicit memory aggregation")
    for field in (
        "manager_memory_bytes", "manager_peak_live_nodes_upper_bound",
        "manager_peak_live_nodes_max", "manager_current_nodes",
        "manager_reorderings", "process_max_rss_bytes",
    ):
        _number(row, field, integer=True)
    _number(row, "manager_reordering_seconds")
    _number(row, "probability_sum")
    _checksum(row, "probability_checksum")
    _checksum(row, "probability_checksum_12dp")


def _canonical_slots(instances: Sequence[CircuitInstance],
                     args: argparse.Namespace) -> List[Dict[str, Any]]:
    slots = []
    reorderings = ["fixed"] + (["dynamic"] if args.include_dynamic else [])
    for instance in instances:
        _weights, weights_sha = fixed_weights(instance.order, args.seed)
        for reordering in reorderings:
            for mode in MODES:
                for phase, rep in _attempt_schedule(args.warmups, args.runs):
                    slots.append(
                        _blank_row(
                            instance, mode, reordering, phase, rep, args, weights_sha
                        )
                    )
    return slots


def _validate_checkpoint_scope(rows: Sequence[Mapping[str, Any]],
                               args: argparse.Namespace) -> None:
    expected = {
        "schema": SCHEMA,
        "batch_id": args.batch_id,
        "frozen_inputs_sha256": args.frozen_inputs_sha256,
        "protocol": args.protocol,
        "run_config_sha256": args.run_config_sha256,
        "formal_run": str(args.formal_run),
        "profile": args.profile,
        "failure_policy": args.failure_policy,
        "git_commit": args.git_commit,
        "git_dirty": args.git_dirty,
        "backend": "cudd",
        "backend_version": args.backend_version,
        "cudd_extension_sha256": args.cudd_extension_sha256,
        "cudd_extension_bytes": str(args.cudd_extension_bytes),
        "python_runtime_sha256": args.python_runtime_sha256,
        "python_runtime_bytes": str(args.python_runtime_bytes),
    }
    for row in rows:
        for field, value in expected.items():
            if str(row.get(field, "")) != str(value):
                raise ValueError(
                    "checkpoint mixes a different batch/config/artifact identity (%s)"
                    % field
                )


_SLOT_IDENTITY_FIELDS = tuple(
    field for field in FIELDS[:FIELDS.index("status")]
)


def validate_checkpoint(rows: Sequence[Mapping[str, Any]],
                        instances: Sequence[CircuitInstance],
                        args: argparse.Namespace, *,
                        require_complete: bool = False) -> None:
    """Validate one immutable canonical schedule prefix and recompute parity."""
    _validate_checkpoint_scope(rows, args)
    slots = _canonical_slots(instances, args)
    if len(rows) > len(slots) or (require_complete and len(rows) != len(slots)):
        raise ValueError("checkpoint has the wrong canonical schedule cardinality")
    baselines: Dict[Tuple[str, ...], Mapping[str, Any]] = {}
    blocked_cells = set()
    block_on_failure = not args.continue_after_failure
    for index, row in enumerate(rows):
        expected = slots[index]
        for field in _SLOT_IDENTITY_FIELDS:
            if str(row.get(field, "")) != str(expected.get(field, "")):
                raise ValueError(
                    "checkpoint is not a canonical schedule prefix (%s)" % field
                )
        _validate_attempt_metrics(row)
        cell = (
            row.get("instance_id"), row.get("reordering"), row.get("mode")
        )
        if cell in blocked_cells:
            if row.get("status") != "not-run":
                raise ValueError("checkpoint retries a blocked physical cell")
            continue
        if row.get("status") == "not-run":
            raise ValueError("checkpoint contains an isolated not-run row")
        baseline_id = _baseline_key(row)
        expected_parity = _parity(row, baselines.get(baseline_id))
        if row.get("parity") != expected_parity:
            raise ValueError("checkpoint parity does not match recomputed evidence")
        if row.get("status") == "ok" and baseline_id not in baselines:
            baselines[baseline_id] = row
        if block_on_failure and (
            row.get("status") != "ok" or expected_parity == "mismatch"
        ):
            blocked_cells.add(cell)


def _completion_path(output: Path) -> Path:
    return output.with_name(output.name + ".complete.json")


def _checkpoint_identity(output: Path) -> Dict[str, Any]:
    payload = _read_checkpoint_payload(output, repair=False)
    if not payload or not payload.endswith((b"\n", b"\r")):
        raise ValueError("completed checkpoint is empty or physically torn")
    return {
        "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def _completion_payload(output: Path, rows: Sequence[Mapping[str, Any]],
                        args: argparse.Namespace) -> Dict[str, Any]:
    return {
        "schema": COMPLETION_SCHEMA,
        "checkpoint": _checkpoint_identity(output),
        "rows": len(rows),
        "run_config_sha256": args.run_config_sha256,
        "run_config": args.run_config,
    }


def _canonical_json_bytes(document: Mapping[str, Any]) -> bytes:
    try:
        return (json.dumps(
            document,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ) + "\n").encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("completion sidecar contains non-JSON values") from exc


def _validate_completion_document(document: Any) -> None:
    if type(document) is not dict or set(document) != {
        "schema", "checkpoint", "rows", "run_config_sha256", "run_config",
    }:
        raise ValueError("completion sidecar schema mismatch")
    if document["schema"] != COMPLETION_SCHEMA:
        raise ValueError("completion sidecar schema mismatch")
    checkpoint = document["checkpoint"]
    if type(checkpoint) is not dict or set(checkpoint) != {"bytes", "sha256"}:
        raise ValueError("completion checkpoint identity schema mismatch")
    if (
        type(checkpoint["bytes"]) is not int
        or checkpoint["bytes"] <= 0
        or checkpoint["bytes"] > MAX_INTEGER_METRIC
    ):
        raise ValueError("completion checkpoint byte count is invalid")
    if (
        type(checkpoint["sha256"]) is not str
        or re.fullmatch(r"[0-9a-f]{64}", checkpoint["sha256"]) is None
    ):
        raise ValueError("completion checkpoint SHA-256 is invalid")
    if (
        type(document["rows"]) is not int
        or document["rows"] <= 0
        or document["rows"] > MAX_INTEGER_METRIC
    ):
        raise ValueError("completion row count is invalid")
    if (
        type(document["run_config_sha256"]) is not str
        or re.fullmatch(
            r"[0-9a-f]{64}", document["run_config_sha256"]
        ) is None
    ):
        raise ValueError("completion run_config SHA-256 is invalid")
    if type(document["run_config"]) is not dict:
        raise ValueError("completion run_config is not an object")


def _read_small_single_link(path: Path, label: str,
                            limit: int = 8 * 1024 * 1024) -> bytes:
    descriptor = _open_single_link(path, os.O_RDONLY, label)
    try:
        before = os.fstat(descriptor)
        if before.st_size > limit:
            raise ValueError("%s exceeds its safety cap" % label)
        payload = bytearray()
        while True:
            block = os.read(descriptor, 1024 * 1024)
            if not block:
                break
            payload.extend(block)
            if len(payload) > limit:
                raise ValueError("%s exceeds its safety cap" % label)
        after = _validate_opened_single_link(path, descriptor, label)
        if _stat_signature(before) != _stat_signature(after):
            raise ValueError("%s changed while being read" % label)
        return bytes(payload)
    finally:
        os.close(descriptor)


def _load_completion(output: Path) -> Optional[Dict[str, Any]]:
    path = _completion_path(output)
    if not path.exists() and not path.is_symlink():
        return None
    payload = _read_small_single_link(path, "compiler completion sidecar")
    try:
        document = json.loads(payload.decode("utf-8", "strict"))
    except (UnicodeError, ValueError) as exc:
        raise ValueError("completion sidecar is not strict JSON") from exc
    _validate_completion_document(document)
    canonical = _canonical_json_bytes(document)
    if payload != canonical:
        raise ValueError("completion sidecar is not canonical JSON")
    return document


def validate_completion(output: Path, rows: Sequence[Mapping[str, Any]],
                        args: argparse.Namespace, *, required: bool) -> None:
    observed = _load_completion(output)
    if observed is None:
        if required:
            raise ValueError("completed checkpoint lacks its completion sidecar")
        return
    expected = _completion_payload(output, rows, args)
    _validate_completion_document(expected)
    if _canonical_json_bytes(observed) != _canonical_json_bytes(expected):
        raise ValueError("completion sidecar does not bind this CSV/run_config")


def write_completion(output: Path, rows: Sequence[Mapping[str, Any]],
                     args: argparse.Namespace) -> None:
    path = _completion_path(output)
    expected = _completion_payload(output, rows, args)
    _validate_completion_document(expected)
    existing = _load_completion(output)
    if existing is not None:
        if _canonical_json_bytes(existing) != _canonical_json_bytes(expected):
            raise ValueError("existing completion sidecar is stale or forged")
        return
    payload = _canonical_json_bytes(expected)
    descriptor, temporary = tempfile.mkstemp(
        prefix=".%s." % path.name, suffix=".tmp", dir=str(path.parent)
    )
    temporary_path = Path(temporary)
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
            raise ValueError("completion temporary file is unsafe")
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            view = view[written:]
        os.fsync(descriptor)
        _validate_opened_single_link(
            temporary_path, descriptor, "completion temporary file"
        )
        if path.exists() or path.is_symlink():
            raise ValueError("completion sidecar appeared during atomic publication")
        try:
            os.link(temporary_path, path, follow_symlinks=False)
        except FileExistsError as exc:
            raise ValueError(
                "completion sidecar appeared during no-clobber publication"
            ) from exc
        published = os.lstat(path)
        opened = os.fstat(descriptor)
        if (published.st_dev, published.st_ino) != (opened.st_dev, opened.st_ino):
            raise ValueError("completion no-clobber publication was path-replaced")
        temporary_path.unlink()
        _fsync_directory(path.parent)
        _validate_opened_single_link(path, descriptor, "compiler completion sidecar")
        os.close(descriptor)
        descriptor = -1
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass


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
    allowed_queries = tuple(args.cache_query_sha256)
    for path in discover_cache_paths(args.cache):
        instances.append(
            cache_instance(
                path,
                args.root_limit,
                expected_commit,
                None if args.allow_cache_commit_mismatch else args.batch_id,
                allowed_queries,
            )
        )
    args._cache_snapshots = [
        snapshot
        for instance in instances
        for snapshot in instance.artifact_snapshots
    ]
    canonical = {}
    selected = []
    for instance in instances:
        if instance.source != "canonical-cache":
            selected.append(instance)
            continue
        key = (
            instance.source_commit,
            instance.source_batch_id,
            instance.query_sha256,
        )
        previous = canonical.get(key)
        if previous is not None and previous.circuit_sha256 != instance.circuit_sha256:
            raise ValueError(
                "cross-engine canonical circuit mismatch for commit/batch/query %r"
                % (key,)
            )
        if previous is None:
            canonical[key] = instance
            selected.append(instance)
    instances = selected
    observed_queries = {
        instance.query_sha256
        for instance in instances if instance.source == "canonical-cache"
    }
    if allowed_queries and observed_queries != set(allowed_queries):
        raise ValueError(
            "cache query allowlist and loaded canonical cache queries differ"
        )
    if not instances:
        raise ValueError("no circuit instances selected")
    return sorted(instances, key=lambda item: item.instance_id)


def _run_config(args: argparse.Namespace,
                instances: Sequence[CircuitInstance]) -> Dict[str, Any]:
    return {
        "schema": SCHEMA,
        "batch_id": args.batch_id,
        "frozen_inputs_sha256": args.frozen_inputs_sha256,
        "protocol": args.protocol,
        "formal_run": args.formal_run,
        "profile": args.profile,
        "failure_policy": args.failure_policy,
        "git_commit": args.git_commit,
        "git_dirty": args.git_dirty,
        "backend": "cudd",
        "backend_version": args.backend_version,
        "runtime_artifacts": {
            CUDD_TOOL_NAME: {
                "bytes": args.cudd_extension_bytes,
                "sha256": args.cudd_extension_sha256,
            },
            PYTHON_TOOL_NAME: {
                "bytes": args.python_runtime_bytes,
                "sha256": args.python_runtime_sha256,
            },
        },
        "protocol_parameters": {
            "seed": args.seed,
            "warmups": args.warmups,
            "runs": args.runs,
            "timeout_s": args.timeout,
            "failure_policy": args.failure_policy,
        },
        "selection": {
            "synthetic_enabled": not args.no_synthetic,
            "families": [
                item.strip() for item in args.families.split(",") if item.strip()
            ],
            "sizes": list(args.sizes),
            "roots": args.roots,
            "root_limit": args.root_limit,
            "cache_query_allowlist": list(args.cache_query_sha256),
            "reorderings": list(FORMAL_REORDERINGS if args.include_dynamic else ("fixed",)),
        },
        "modes": list(MODES),
        "instances": [
            {
                "instance_id": instance.instance_id,
                "source": instance.source,
                "source_commit": instance.source_commit,
                "source_batch_id": instance.source_batch_id,
                "query_sha256": instance.query_sha256,
                "circuit_sha256": instance.circuit_sha256,
                "circuit_bytes": instance.circuit_bytes,
                "source_sidecar_sha256": instance.source_sidecar_sha256,
                "source_sidecar_bytes": instance.source_sidecar_bytes,
                "source_observation_sha256": instance.source_observation_sha256,
                "root_count": len(instance.roots),
                "input_order_sha256": _hash_order(instance.order),
            }
            for instance in sorted(instances, key=lambda item: item.instance_id)
        ],
    }


def _verify_frozen_end(args: argparse.Namespace) -> None:
    if args._frozen_snapshot is None:
        return
    _verify_snapshot(args._frozen_snapshot)
    try:
        document = freeze_inputs.load_frozen_batch(
            args.frozen_inputs,
            expected_commit=args.git_commit,
            expected_protocol=args.expected_protocol,
            required_tools=tuple(sorted(args._actual_tools)),
            require_formal=args.formal_run,
        )
    except freeze_inputs.FreezeError as exc:
        raise RuntimeError("frozen inputs changed during compiler run") from exc
    if document["batch_id"] != args.batch_id:
        raise RuntimeError("frozen batch changed during compiler run")
    for name, observed in args._actual_tools.items():
        try:
            frozen = freeze_inputs.frozen_tool(document, name)
        except freeze_inputs.FreezeError as exc:
            raise RuntimeError("frozen runtime identity disappeared") from exc
        if (
            frozen["sha256"] != observed["sha256"]
            or frozen["bytes"] != observed["bytes"]
        ):
            raise RuntimeError("frozen runtime identity changed during compiler run")


def _run_experiment_locked(args: argparse.Namespace) -> Dict[str, Any]:
    args.formal_run = not args.allow_unfrozen and not args.allow_dirty
    args.failure_policy = (
        "continue-after-terminal-failure-no-retry"
        if args.continue_after_failure else FAILURE_POLICY
    )
    _validate_formal_configuration(args)
    args.git_commit, args.git_dirty = _git_identity()
    _validate_git_identity(args.git_commit, args.git_dirty, args.allow_dirty)
    if args.formal_run:
        _validate_no_hidden_index_bits()

    args.backend_version, args._actual_tools = _discover_cudd_runtime()
    args._artifact_snapshots = list(args._actual_tools.values())
    args.cudd_extension_sha256 = args._actual_tools[CUDD_TOOL_NAME]["sha256"]
    args.cudd_extension_bytes = args._actual_tools[CUDD_TOOL_NAME]["bytes"]
    args.python_runtime_sha256 = args._actual_tools[PYTHON_TOOL_NAME]["sha256"]
    args.python_runtime_bytes = args._actual_tools[PYTHON_TOOL_NAME]["bytes"]
    _resolve_frozen_identity(args)
    instances = build_instances(args)
    args._artifact_snapshots.extend(args._cache_snapshots)
    if args._frozen_snapshot is not None:
        args._artifact_snapshots.append(args._frozen_snapshot)
    _reject_output_input_aliases(args.output, args._artifact_snapshots)
    args.run_config = _run_config(args, instances)
    args.run_config_sha256 = _canonical_digest(args.run_config)
    completion = _load_completion(args.output)
    rows = load_checkpoint(args.output, repair=completion is None)
    validate_checkpoint(rows, instances, args)
    if completion is not None:
        validate_checkpoint(rows, instances, args, require_complete=True)
        validate_completion(args.output, rows, args, required=True)

    existing = {_row_key(row): row for row in rows}
    baselines: Dict[Tuple[str, ...], Mapping[str, Any]] = {}
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
                    prior = existing.get(key)
                    if prior is not None:
                        skipped += 1
                        if (
                            not args.continue_after_failure
                            and (
                                prior.get("status") != "ok"
                                or prior.get("parity") == "mismatch"
                            )
                        ):
                            stop_cell = True
                        continue
                    if stop_cell:
                        row.update({
                            "status": "not-run",
                            "parity": "unverified",
                            "notes": NOT_RUN_NOTE,
                        })
                        serialized = {
                            field: str(row.get(field, "")) for field in FIELDS
                        }
                        validate_checkpoint([*rows, serialized], instances, args)
                        append_checkpoint(args.output, row)
                        existing[key] = serialized
                        rows.append(serialized)
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
                    row["parity"] = _parity(row, baseline)
                    serialized = {
                        field: str(row.get(field, "")) for field in FIELDS
                    }
                    validate_checkpoint([*rows, serialized], instances, args)
                    append_checkpoint(args.output, row)
                    existing[key] = serialized
                    rows.append(serialized)
                    if result.get("status") == "ok" and baseline is None:
                        baselines[baseline_id] = serialized
                    if (
                        result.get("status") != "ok"
                        or row.get("parity") == "mismatch"
                    ):
                        failed += 1
                        stop_cell = not args.continue_after_failure

    final_rows = load_checkpoint(args.output, repair=False)
    validate_checkpoint(final_rows, instances, args, require_complete=True)
    if _canonical_digest(_run_config(args, instances)) != args.run_config_sha256:
        raise RuntimeError("run_config changed during compiler experiment")
    for snapshot in args._artifact_snapshots:
        _verify_snapshot(snapshot)
    _verify_frozen_end(args)
    ending_git = _git_identity()
    if ending_git != (args.git_commit, args.git_dirty):
        raise RuntimeError("Git identity changed during compiler experiment")
    if args.formal_run:
        _validate_no_hidden_index_bits()
    write_completion(args.output, final_rows, args)
    validate_completion(args.output, final_rows, args, required=True)
    # Completion is evidence too: recheck every frozen artifact and Git after
    # its durable publication rather than trusting a pre-publication sandwich.
    for snapshot in args._artifact_snapshots:
        _verify_snapshot(snapshot)
    _verify_frozen_end(args)
    if _git_identity() != (args.git_commit, args.git_dirty):
        raise RuntimeError("Git identity changed while publishing completion evidence")
    if args.formal_run:
        _validate_no_hidden_index_bits()

    measured_ok = [row for row in final_rows
                   if row.get("phase") == "measured" and row.get("status") == "ok"]
    compile_samples = [float(row["compile_wall_ms"]) for row in measured_ok]
    wmc_samples = [float(row["wmc_wall_ms"]) for row in measured_ok]
    fatal_total = sum(row.get("status") in FATAL_STATUSES for row in final_rows)
    resource_total = sum(row.get("status") in RESOURCE_STATUSES for row in final_rows)
    failed_total = fatal_total + resource_total
    parity_mismatches = sum(row.get("parity") == "mismatch" for row in final_rows)
    exit_code = 1 if (
        fatal_total or parity_mismatches or (args.formal_run and resource_total)
    ) else 0
    return {
        "schema": SCHEMA,
        "batch_id": args.batch_id,
        "frozen_inputs_sha256": args.frozen_inputs_sha256,
        "protocol": args.protocol,
        "run_config_sha256": args.run_config_sha256,
        "formal_run": args.formal_run,
        "profile": args.profile,
        "failure_policy": args.failure_policy,
        "git_commit": args.git_commit,
        "git_dirty": args.git_dirty,
        "git_identity_verified_end": True,
        "status": "failed" if exit_code else (
            "resource-boundary" if resource_total else "ok"
        ),
        "exit_code": exit_code,
        "output": str(args.output.resolve()),
        "instance_count": len(instances),
        "attempted": attempted,
        "resumed_or_skipped": skipped,
        "failed_attempts_this_invocation": failed,
        "failed_attempts_total": failed_total,
        "fatal_failures_total": fatal_total,
        "resource_boundaries_total": resource_total,
        "parity_mismatches": parity_mismatches,
        "checkpoint_rows": len(final_rows),
        "completion_sidecar": str(_completion_path(args.output)),
        "measured_ok": len(measured_ok),
        "compile_wall_ms_median": statistics.median(compile_samples) if compile_samples else None,
        "wmc_wall_ms_median": statistics.median(wmc_samples) if wmc_samples else None,
    }


def run_experiment(args: argparse.Namespace) -> Dict[str, Any]:
    args.formal_run = not args.allow_unfrozen and not args.allow_dirty
    args.failure_policy = (
        "continue-after-terminal-failure-no-retry"
        if args.continue_after_failure else FAILURE_POLICY
    )
    _validate_formal_configuration(args)
    args.output = _validate_output_destination(
        args.output, args.allow_unsafe_output
    )
    with _invocation_lock(args.output, timeout=args.lock_timeout):
        return _run_experiment_locked(args)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--output", type=Path,
                        default=HERE.parents[1] / "artifacts" / "compiler_granularity.csv")
    result.add_argument("--cache", action="append", default=[],
                        help="canonical circuit .nt file or directory (repeatable)")
    result.add_argument(
        "--cache-query-sha256", action="append", default=[], metavar="SHA256",
        help=("exact query allowlist for the real-cache profile; repeat for "
              "multiple canonical queries"),
    )
    result.add_argument("--profile", choices=PROFILES, default=SYNTHETIC_PROFILE)
    result.add_argument("--no-synthetic", action="store_true")
    result.add_argument("--families", default=",".join(FORMAL_SYNTHETIC_FAMILIES))
    result.add_argument("--sizes", type=_parse_ints,
                        default=list(FORMAL_SYNTHETIC_SIZES))
    result.add_argument("--roots", type=int, default=FORMAL_SYNTHETIC_ROOTS)
    result.add_argument("--root-limit", type=int)
    result.add_argument(
        "--allow-cache-commit-mismatch", action="store_true",
        help="exploratory only: accept a canonical cache produced by another commit",
    )
    result.add_argument("--seed", type=int, default=DEFAULT_SEED)
    result.add_argument("--frozen-inputs", type=Path)
    result.add_argument("--batch-id")
    result.add_argument(
        "--expected-protocol",
        default=FORMAL_PROTOCOL,
        help="exact protocol string embedded in the frozen batch",
    )
    result.add_argument(
        "--allow-unfrozen", action="store_true",
        help="exploratory only: accept an explicit batch ID without a frozen manifest",
    )
    result.add_argument(
        "--allow-dirty", action="store_true",
        help="exploratory only: run from a dirty but committed worktree",
    )
    result.add_argument(
        "--allow-unsafe-output", action="store_true",
        help="exploratory only: allow a tracked or non-ignored repository output",
    )
    result.add_argument("--lock-timeout", type=float, default=300.0)
    result.add_argument("--warmups", type=int, default=DEFAULT_WARMUPS)
    result.add_argument("--runs", type=int, default=DEFAULT_RUNS)
    result.add_argument("--timeout", type=float, default=FORMAL_TIMEOUT_S)
    result.add_argument(
        "--include-dynamic", action=argparse.BooleanOptionalAction, default=True,
        help="include the fixed formal dynamic-reordering cells",
    )
    result.add_argument("--continue-after-failure", action="store_true")
    return result


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parser().parse_args(argv)
    if (args.roots < 1 or args.warmups < 0 or args.runs < 1
            or args.timeout <= 0 or args.lock_timeout <= 0):
        raise SystemExit("roots/runs/timeout must be positive and warmups non-negative")
    if args.root_limit is not None and args.root_limit < 1:
        raise SystemExit("root-limit must be positive")
    families = [item.strip() for item in args.families.split(",") if item.strip()]
    if (
        not families
        or len(families) != len(set(families))
        or set(families) - set(FORMAL_SYNTHETIC_FAMILIES)
    ):
        raise SystemExit("families must be unique sharing/no-sharing names")
    if len(args.sizes) != len(set(args.sizes)):
        raise SystemExit("sizes contains duplicate values")
    if any(
        re.fullmatch(r"[0-9a-f]{64}", value or "") is None
        for value in args.cache_query_sha256
    ):
        raise SystemExit("cache-query-sha256 must be lowercase 64-hex")
    if len(args.cache_query_sha256) != len(set(args.cache_query_sha256)):
        raise SystemExit("cache-query-sha256 contains duplicates")
    args.cache_query_sha256 = tuple(sorted(args.cache_query_sha256))
    if (args.expected_protocol is not None
            and (not args.expected_protocol or len(args.expected_protocol) > 128
                 or any(ord(character) < 32 for character in args.expected_protocol))):
        raise SystemExit("expected protocol is blank, too long, or contains controls")
    summary = run_experiment(args)
    print(json.dumps(summary, sort_keys=True))
    return int(summary["exit_code"])


if __name__ == "__main__":
    sys.exit(main())
