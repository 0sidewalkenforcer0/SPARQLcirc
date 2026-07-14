#!/usr/bin/env python3
"""Recoverable Stage-3 controlled-mechanism experiments.

The harness has three independently selectable experiment groups:

``construction``
    flat/factored Python reference construction over star, layered, chain, and
    cyclic-join families;
``numerical``
    CUDD shared/per-root WMC under uniform, deterministic non-uniform, and
    extreme probabilities, checked against a 100-digit Decimal oracle;
``treewidth``
    factored layered-family circuits compiled by production CUDD and the pinned
    d4v2 binary, with independently replayable lower/upper certificates for
    the exact Tseitin-CNF primal graph supplied to d4.

The treewidth group distinguishes the generator width parameter from certified
CNF-primal treewidth. Every row binds the CNF/graph and deterministic
minor-min-width lower/min-fill upper proof hashes. The formal bounded family
has the exact interval [3,3]; the growing family has certified intervals
[3,3], [4,7], and [5,7].

Every CSV row is one raw warm-up/measured attempt. Formal protocol
``controlled-mechanisms-v5`` fixes 1+5 attempts, seed 20260713, a 120-second
killable worker, and each selected group's complete predefined grid. Rows are
append-only, fsynced, resume-aware, and retain terminal/not-run outcomes. A
formal run requires a canonical ``freeze_inputs.py`` document, the fixed
protocol, a matching clean Git commit with no hidden index bits, a frozen
Python runtime for every group, the frozen CUDD extension when CUDD is
selected, and the frozen/pinned d4 hash for treewidth.
The complete invocation holds an exclusive output lock; output must live
outside the repository or below a Git-ignored directory.
"""
from __future__ import annotations

import argparse
import contextlib
import csv
from decimal import Decimal, InvalidOperation, localcontext
import io
import fcntl
import hashlib
import itertools
import json
import math
import multiprocessing
import os
from pathlib import Path
import re
import resource
import signal
import stat
import subprocess
import sys
import tempfile
import time
import traceback
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple


HERE = Path(__file__).resolve().parent
REFERENCE = HERE.parent
if str(REFERENCE) not in sys.path:
    sys.path.insert(0, str(REFERENCE))

import compiler
import ddnnf_wmc
import export_cnf
import factor
import gamma
import gates
import gen_families
import treewidth_evidence

try:
    import freeze_inputs
except ImportError:  # pragma: no cover - package-style imports
    from . import freeze_inputs

import compiler_granularity as granularity


SCHEMA = "controlled-mechanisms-v5"
FORMAL_PROTOCOL = "controlled-mechanisms-v5"
FORMAL_SEED = 20260713
FORMAL_WARMUPS = 1
FORMAL_RUNS = 5
FORMAL_TIMEOUT_S = 120.0
FORMAL_CONSTRUCTION_SHAPES = ("star", "layered", "chain", "cycle")
FORMAL_CONSTRUCTION_SIZES = (2, 3, 4)
FORMAL_NUMERICAL_DEPTHS = (8, 64, 512)
FORMAL_BOUNDED_DEPTHS = (4, 8, 16)
FORMAL_GROWING_WIDTHS = (2, 3, 4)
FORMAL_TREEWIDTH_INTERVALS = {
    ("bounded", 4): (3, 3),
    ("bounded", 8): (3, 3),
    ("bounded", 16): (3, 3),
    ("growing", 2): (3, 3),
    ("growing", 3): (4, 7),
    ("growing", 4): (5, 7),
}
DEFAULT_SEED = FORMAL_SEED
CUDD_TOOL_NAME = "dd-cudd-extension"
PYTHON_TOOL_NAME = "python-runtime"
D4_TOOL_NAME = "d4v2"
COMPLETION_SCHEMA = "controlled-mechanisms-completion-v2"
DEFAULT_D4V2 = HERE.parents[1] / (
    "artifacts/toolchains/d4v2-15eff319-patched/d4_compiler_static"
)
PINNED_D4V2_SHA256 = "521df25c6b9438cd6687256ee958ba9b08ab3d932308167163192bbc0685b06d"
D4_SOURCE_COMMIT = "15eff31962466804a48374826b9e5a746fc2766e"
D4_SOURCE_ARCHIVE_SHA256 = (
    "8177f28ae4b98f9aedbb76b8a475a30bfc933f3ab795cbc81a7abd5a923af156"
)
D4_PATCHED_SOURCE_SHA256 = (
    "0a18203c8059bd6fb5cc88bddea993dfab620a34ad1a2affa09177e79e1163ea"
)
D4_WEIGHT_CONTRACT = "normalized-primary-functional-tseitin-v1"
EXPERIMENTS = ("construction", "numerical", "treewidth")
CONSTRUCTION_SHAPES = ("star", "layered", "chain", "cycle")
TERMINAL = {
    "ok", "timeout", "oom", "error", "numerical-mismatch", "unsupported",
    "not-run", "worker-exit", "killed-signal", "cleanup-error",
}
FATAL_STATUSES = {
    "error", "numerical-mismatch", "unsupported", "worker-exit",
    "killed-signal", "cleanup-error",
}
RESOURCE_STATUSES = {"timeout", "oom"}
D4_ARGV_PROTOCOL = (
    "d4v2-15eff319-problem-width-static-v1:"
    "{d4} -i {cnf} --dump-file {out}"
)
D4_ARGV_SHA256 = hashlib.sha256(D4_ARGV_PROTOCOL.encode("utf-8")).hexdigest()
REPO_ROOT = HERE.parents[1]
NUMERICAL_ABS_TOL = Decimal("1e-12")
NUMERICAL_REL_TOL = Decimal("1e-10")
MAX_INTEGER_METRIC = 2 ** 63 - 1
NOT_RUN_NOTE = (
    "earlier terminal failure/resource boundary blocks this cell; "
    "the original batch never retries"
)

FIELDS = [
    "schema", "batch_id", "frozen_inputs_sha256", "protocol",
    "run_config_sha256", "formal_run", "strict_mode", "git_commit", "git_dirty",
    "experiment", "instance_id",
    "family", "shape", "size", "depth_parameter", "width_parameter",
    "tw_evidence", "treewidth_graph", "treewidth_nodes", "treewidth_edges",
    "treewidth_clauses", "treewidth_lower_bound", "treewidth_upper_bound",
    "treewidth_cnf_sha256", "treewidth_graph_sha256",
    "treewidth_lower_certificate_sha256",
    "treewidth_upper_certificate_sha256",
    "method", "construction_mode", "compiler",
    "compile_mode", "probability_profile", "phase", "rep", "warmups", "runs",
    "timeout_s", "seed", "backend_version",
    "cudd_extension_sha256", "cudd_extension_bytes",
    "python_runtime_sha256", "python_runtime_bytes",
    "d4v2_path", "d4v2_sha256", "d4v2_bytes", "d4_argv_sha256",
    "d4_weight_contract", "d4_primary_vars", "d4_aux_vars",
    "status", "attempt_wall_ms", "tokens", "answers", "build_ms", "gates",
    "edges", "circuit_bytes", "process_self_peak_rss_bytes",
    "process_self_rss_delta_bytes", "compiler_child_peak_rss_bytes",
    "parity_kind", "parity_worlds", "semantic_checksum", "parity",
    "prepare_ms", "backend_compile_ms", "inspect_ms", "source_to_result_ms",
    "wmc_ms", "wmc_wall_ms", "timing_scope",
    "compiled_nodes_unique", "compiled_nodes_sum_roots", "manager_memory_bytes",
    "manager_peak_live_nodes_upper_bound",
    "cnf_vars", "cnf_clauses", "ddnnf_nodes", "ddnnf_edges",
    "ddnnf_max_literal",
    "probability_sum", "probability_checksum", "exact_probability_checksum",
    "max_abs_error", "max_rel_error", "underflow_count", "numerical_classification",
    "cleanup_ms", "cleanup_action", "process_group_reaped", "notes",
]
TREEWIDTH_IDENTITY_FIELDS = (
    "tw_evidence", "treewidth_graph", "treewidth_nodes", "treewidth_edges",
    "treewidth_clauses", "treewidth_lower_bound", "treewidth_upper_bound",
    "treewidth_cnf_sha256", "treewidth_graph_sha256",
    "treewidth_lower_certificate_sha256",
    "treewidth_upper_certificate_sha256",
)


def _parse_ints(value: str) -> List[int]:
    values = [int(item.strip()) for item in value.split(",") if item.strip()]
    if not values or any(item < 1 for item in values):
        raise argparse.ArgumentTypeError("expected comma-separated positive integers")
    return values


def _stat_signature(value: os.stat_result) -> Tuple[int, ...]:
    return (
        value.st_dev, value.st_ino, value.st_mode, value.st_nlink,
        value.st_size, value.st_mtime_ns, value.st_ctime_ns,
    )


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(str(path), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _validate_opened_single_link(path: Path, descriptor: int, label: str) -> os.stat_result:
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


def _open_single_link(path: Path, flags: int, label: str, mode: int = 0o600) -> int:
    descriptor = None
    try:
        descriptor = os.open(
            str(path), flags | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
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


def _snapshot_file(path: Path, label: str, *, allow_empty: bool = False) -> Dict[str, Any]:
    """Hash one resolved single-link artifact once and retain its end-run stat identity."""
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
    """Prevent checkpoint repair/open from ever targeting a frozen input/tool."""
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
                    "controlled output/lock/sidecar must not alias %s"
                    % snapshot["label"]
                )


def validate_git_identity(commit: str, dirty: str, allow_dirty: bool) -> None:
    """Enforce publication-run provenance, with an explicit exploratory escape hatch."""
    if re.fullmatch(r"[0-9a-f]{40}", commit) is None:
        raise RuntimeError("experiment requires a full lowercase 40-hex Git commit")
    if dirty not in ("true", "false"):
        raise RuntimeError("could not determine whether the Git worktree is clean")
    if dirty != "false" and not allow_dirty:
        raise RuntimeError(
            "formal runs require a clean worktree; use --allow-dirty only for exploration")


def validate_no_hidden_index_bits(repo: Path = REPO_ROOT) -> None:
    """Reject tracked files porcelain can hide via assume-unchanged/skip-worktree."""
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
        # `-v` lowercases the normal tag for assume-unchanged entries.  `S`
        # is the explicit skip-worktree tag. Paths remain opaque bytes.
        if marker == b"S" or marker.islower():
            hidden.append(record[2:])
    if hidden:
        raise RuntimeError(
            "formal runs reject %d tracked file(s) hidden by "
            "assume-unchanged/skip-worktree index flags" % len(hidden)
        )


def validate_frozen_inputs(path: Path, *, batch_id: Optional[str],
                           expected_protocol: Optional[str], current_commit: str,
                           require_d4: bool, d4_sha256: str,
                           d4_tool_name: str,
                           required_data: Sequence[str] = (),
                           require_formal: bool = True,
                           actual_tools: Optional[Mapping[str, Mapping[str, Any]]] = None,
                           frozen_snapshot: Optional[Mapping[str, Any]] = None,
                           ) -> Dict[str, Any]:
    """Bind the shared validator to the fixed controlled protocol and artifacts."""
    if not expected_protocol:
        raise ValueError("--expected-protocol is required with --frozen-inputs")
    if require_formal and expected_protocol != FORMAL_PROTOCOL:
        raise ValueError(
            "formal controlled runs require protocol %s" % FORMAL_PROTOCOL
        )
    tools = dict(actual_tools or {})
    if require_formal and require_d4 and d4_tool_name != D4_TOOL_NAME:
        raise ValueError("formal d4 logical tool name is fixed as %s" % D4_TOOL_NAME)
    d4_name = D4_TOOL_NAME if require_formal else d4_tool_name
    required_tools = set(tools)
    if require_d4:
        required_tools.add(d4_name)
    snapshot = dict(
        frozen_snapshot
        or _snapshot_file(Path(path), "frozen inputs document")
    )
    try:
        document = freeze_inputs.load_frozen_batch(
            path, expected_commit=current_commit,
            expected_protocol=expected_protocol,
            required_data=tuple(required_data),
            required_tools=tuple(sorted(required_tools)),
            require_formal=require_formal,
        )
    except freeze_inputs.FreezeError as exc:
        raise ValueError(str(exc)) from exc
    manifest_batch = document["batch_id"]
    if batch_id and batch_id != manifest_batch:
        raise ValueError("--batch-id disagrees with canonical frozen batch_id")
    if (require_d4
            and freeze_inputs.frozen_tool(document, d4_name)["sha256"] != d4_sha256):
        raise ValueError("frozen d4 tool hash does not match the selected binary")
    for name, observed in tools.items():
        frozen = freeze_inputs.frozen_tool(document, name)
        if (
            frozen["sha256"] != observed.get("sha256")
            or frozen["bytes"] != observed.get("bytes")
        ):
            raise ValueError(
                "frozen %s artifact differs from the active runtime" % name
            )
    return {
        "batch_id": manifest_batch,
        "frozen_inputs_sha256": snapshot["sha256"],
        "protocol": expected_protocol,
        "frozen_snapshot": snapshot,
        "document": document,
    }


def resolve_provenance(args: argparse.Namespace, commit: str,
                       require_d4: bool,
                       actual_tools: Mapping[str, Mapping[str, Any]]) -> Dict[str, Any]:
    if args.frozen_inputs is None:
        if not args.allow_unfrozen:
            raise ValueError(
                "formal runs require --frozen-inputs; use --allow-unfrozen only for exploration")
        if re.fullmatch(r"[0-9a-f]{64}", str(args.batch_id or "")) is None:
            raise ValueError("unfrozen exploration requires a lowercase 64-hex --batch-id")
        protocol = args.expected_protocol or "unfrozen-exploration"
        return {
            "batch_id": args.batch_id,
            "frozen_inputs_sha256": "",
            "protocol": protocol,
        }
    return validate_frozen_inputs(
        args.frozen_inputs, batch_id=args.batch_id,
        expected_protocol=args.expected_protocol, current_commit=commit,
        require_d4=require_d4, d4_sha256=args.d4v2_sha256,
        d4_tool_name=args.d4_tool_name, required_data=args.required_data,
        require_formal=args.formal_run,
        actual_tools=actual_tools,
    )


def validate_output_destination(path: Path) -> Path:
    """Require results outside the repository or under a Git-ignored path."""
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
    try:
        relative = resolved.relative_to(REPO_ROOT.resolve())
    except ValueError:
        return resolved
    tracked = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "ls-files", "--error-unmatch", "--", str(relative)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10,
    )
    ignored = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "check-ignore", "-q", "--no-index", "--",
         str(relative)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        timeout=10,
    )
    if tracked.returncode == 0 or ignored.returncode != 0:
        raise ValueError("output inside the repository must be untracked and Git-ignored")
    return resolved


@contextlib.contextmanager
def invocation_lock(output: Path):
    """Cross-process exclusive lock covering the complete checkpoint invocation."""
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    lock_path = output.with_name(output.name + ".lock")
    descriptor = _open_single_link(
        lock_path, os.O_RDWR | os.O_CREAT, "controlled invocation lock"
    )
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        _validate_opened_single_link(
            lock_path, descriptor, "controlled invocation lock"
        )
        if output.exists() or output.is_symlink():
            check = _open_single_link(output, os.O_RDONLY, "controlled checkpoint")
            os.close(check)
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _parse_generated_family(ttl: str, query: str) -> Tuple[Dict[str, Tuple[str, str, str]],
                                                           List[Tuple[str, str, str]],
                                                           List[str]]:
    """Translate the deliberately simple ``gen_families`` output into the reference DSL."""
    data = {}
    pattern = re.compile(
        r":(\w+)\s+rdf:subject\s+:(\S+)\s*;\s*"
        r"rdf:predicate\s+:(\S+)\s*;\s*rdf:object\s+:(\S+)\s*\."
    )
    for match in pattern.finditer(ttl):
        token, subject, predicate, obj = match.groups()
        data[token] = (subject, predicate, obj)
    selected = re.search(r"SELECT\s+(.+?)\s+WHERE", query, re.S)
    if selected is None:
        raise ValueError("generated family query has no SELECT/WHERE")
    out_vars = selected.group(1).split()
    body = query[query.find("{") + 1:query.rfind("}")]
    patterns = []
    for statement in body.split("."):
        terms = statement.split()
        if len(terms) == 3:
            patterns.append(tuple(term[1:] if term.startswith(":") else term
                                  for term in terms))
    if not data or not patterns:
        raise ValueError("generated family did not yield data and patterns")
    return data, patterns, out_vars


def _cyclic_join(size: int) -> Tuple[Dict[str, Tuple[str, str, str]],
                                      List[Tuple[str, str, str]], List[str], Dict[str, Any]]:
    """Directed-clique data with a triangle BGP; ``size`` is the node count."""
    if size < 2:
        raise ValueError("cyclic join needs at least two nodes")
    data = {}
    token = 0
    for left in range(size):
        for right in range(size):
            data["cycle%05d" % token] = ("n%d" % left, "e", "n%d" % right)
            token += 1
    patterns = [("?a", "e", "?b"), ("?b", "e", "?c"), ("?c", "e", "?a")]
    return data, patterns, ["?a"], {
        "depth_parameter": 3,
        "width_parameter": size,
        "note": "triangle BGP over directed-clique data; no treewidth claim",
    }


def construction_family(shape: str, size: int) -> Tuple[
        Dict[str, Tuple[str, str, str]], List[Tuple[str, str, str]], List[str], Dict[str, Any]]:
    if shape == "chain":
        generated = gen_families.chain(size)
        depth, width = size, 1
        note = "generated read-once path; formula treewidth was not independently measured"
    elif shape == "star":
        generated = gen_families.star(3, size)
        depth, width = 3, size
        note = generated[2].get("note", "")
    elif shape == "layered":
        generated = gen_families.layered(4, size)
        depth, width = 4, size
        note = (
            "generated layered s-t DAG with declared width=%d; formula treewidth "
            "was not independently measured" % width)
    elif shape == "cycle":
        return _cyclic_join(size)
    else:
        raise ValueError("unknown construction shape %r" % shape)
    data, patterns, out_vars = _parse_generated_family(generated[0], generated[1])
    return data, patterns, out_vars, {
        "depth_parameter": depth,
        "width_parameter": width,
        "note": note,
    }


def _children(circ: Mapping[str, Tuple[str, Any]], gate: str) -> Tuple[str, ...]:
    op, payload = circ[gate]
    if op in ("leaf", "const"):
        return ()
    if op in ("plus", "times", "minus"):
        return tuple(payload)
    raise ValueError("unknown gate operation %r" % op)


def _reachable(circ: Mapping[str, Tuple[str, Any]], roots: Iterable[str]) -> Tuple[Set[str], int]:
    seen: Set[str] = set()
    edges = 0
    stack = list(roots)
    while stack:
        gate = stack.pop()
        if gate in seen:
            continue
        seen.add(gate)
        children = _children(circ, gate)
        edges += len(children)
        stack.extend(children)
    return seen, edges


def _postorder(circ: Mapping[str, Tuple[str, Any]], roots: Iterable[str]) -> List[str]:
    order = []
    state: Dict[str, int] = {}
    for root in roots:
        stack = [(root, False)]
        while stack:
            gate, expanded = stack.pop()
            mark = state.get(gate, 0)
            if expanded:
                if mark != 2:
                    state[gate] = 2
                    order.append(gate)
                continue
            if mark == 2:
                continue
            if mark == 1:
                raise ValueError("cycle in circuit at %s" % gate)
            state[gate] = 1
            stack.append((gate, True))
            for child in reversed(_children(circ, gate)):
                if state.get(child) == 1:
                    raise ValueError("cycle in circuit at %s" % child)
                if state.get(child) != 2:
                    stack.append((child, False))
    return order


def _evaluate(circ: Mapping[str, Tuple[str, Any]], order: Sequence[str],
              assignment: Mapping[str, bool]) -> Dict[str, bool]:
    values = {}
    for gate in order:
        op, payload = circ[gate]
        if op == "leaf":
            values[gate] = assignment[payload]
        elif op == "const":
            values[gate] = bool(payload)
        elif op == "plus":
            values[gate] = any(values[child] for child in payload)
        elif op == "times":
            values[gate] = all(values[child] for child in payload)
        elif op == "minus":
            values[gate] = values[payload[0]] and not values[payload[1]]
    return values


def _semantic_checksum(circ: Mapping[str, Tuple[str, Any]], roots: Mapping[Any, str],
                       seed: int, exhaustive_limit: int = 10,
                       sampled_worlds: int = 64) -> Tuple[str, str, int]:
    tokens = sorted({payload for op, payload in circ.values() if op == "leaf"})
    order = _postorder(circ, roots.values())
    if len(tokens) <= exhaustive_limit:
        worlds = list(itertools.product((False, True), repeat=len(tokens)))
        kind = "exhaustive"
    else:
        worlds = []
        for world in range(sampled_worlds):
            bits = []
            for token in tokens:
                digest = hashlib.sha256(
                    ("%d\0%d\0%s" % (seed, world, token)).encode("utf-8")).digest()
                bits.append(bool(digest[0] & 1))
            worlds.append(tuple(bits))
        # This is a deterministic semantic smoke sample, not an exhaustive
        # equivalence proof for circuits with more than ten input tokens.
        kind = "seeded-worlds-smoke"
    digest = hashlib.sha256()
    sorted_roots = sorted(roots, key=granularity._stable_text)
    for world_index, bits in enumerate(worlds):
        values = _evaluate(circ, order, dict(zip(tokens, bits)))
        digest.update(world_index.to_bytes(8, "big"))
        for key in sorted_roots:
            encoded = granularity._stable_text(key).encode("utf-8")
            digest.update(len(encoded).to_bytes(8, "big"))
            digest.update(encoded)
            digest.update(b"1" if values[roots[key]] else b"0")
    return digest.hexdigest(), kind, len(worlds)


def _circuit_bytes(circ: Mapping[str, Tuple[str, Any]], roots: Mapping[Any, str]) -> int:
    reachable, _ = _reachable(circ, roots.values())
    payload = {
        "gates": [[gate, circ[gate][0], granularity._stable_text(circ[gate][1])]
                  for gate in sorted(reachable)],
        "roots": [[granularity._stable_text(key), root]
                  for key, root in sorted(roots.items(), key=lambda item: granularity._stable_text(item[0]))],
    }
    return len(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def construction_attempt(task: Mapping[str, Any]) -> Dict[str, Any]:
    data, patterns, out_vars, metadata = construction_family(task["shape"], task["size"])
    before_rss = granularity._rss_bytes()
    circuit = gates.Circuit()
    started = time.perf_counter()
    if task["construction_mode"] == "flat":
        roots = gamma.project(circuit, gamma.eval_bgp(circuit, patterns, data), out_vars)
    elif task["construction_mode"] == "factored":
        roots = factor.factored_bgp(circuit, patterns, data, set(out_vars))
    else:
        raise ValueError("unknown construction mode")
    build_ms = (time.perf_counter() - started) * 1000.0
    build_peak_rss = granularity._rss_bytes()
    reachable, edges = _reachable(circuit.gates, roots.values())
    checksum, parity_kind, parity_worlds = _semantic_checksum(
        circuit.gates, roots, task["seed"])
    return {
        "status": "ok",
        "tokens": len(data),
        "answers": len(roots),
        "depth_parameter": metadata["depth_parameter"],
        "width_parameter": metadata["width_parameter"],
        "build_ms": build_ms,
        "gates": len(reachable),
        "edges": edges,
        "circuit_bytes": _circuit_bytes(circuit.gates, roots),
        "process_self_peak_rss_bytes": build_peak_rss,
        "process_self_rss_delta_bytes": max(0, build_peak_rss - before_rss),
        "parity_kind": parity_kind,
        "parity_worlds": parity_worlds,
        "semantic_checksum": checksum,
        "notes": metadata["note"],
    }


def numerical_instance(depth: int, profile: str, seed: int) -> Tuple[
        Dict[str, Tuple[str, Any]], Dict[str, Tuple[str, Decimal]],
        Dict[str, float], Dict[str, Decimal]]:
    if depth < 1:
        raise ValueError("numerical depth must be positive")
    circ: Dict[str, Tuple[str, Any]] = {"const:one": ("const", 1)}
    roots = {}
    float_weights = {}
    decimal_weights = {}
    previous = "const:one"
    exact = Decimal(1)
    exact_by_gate = {}
    with localcontext() as context:
        context.prec = 100
        for index in range(depth):
            tokens = ["urn:numeric:%s:%05d:%s" % (profile, index, suffix)
                      for suffix in ("x", "y", "z")]
            if profile == "uniform":
                decimals = [Decimal("0.5")] * 3
            elif profile == "nonuniform":
                decimals = []
                for token in tokens:
                    digest = hashlib.sha256(
                        (str(seed) + "\0" + token).encode("utf-8")).digest()
                    value = 50000 + int.from_bytes(digest[:4], "big") % 900001
                    decimals.append(Decimal(value) / Decimal(1000000))
            elif profile == "extreme":
                # Both endpoints survive the Decimal -> binary64 conversion.
                # Every layer remains close to one, so the default depth=512
                # is a numerical stress test without silently collapsing the
                # oracle or binary64 result to zero.
                near_zero = Decimal.from_float(math.ldexp(1.0, -40))
                near_one = Decimal.from_float(math.nextafter(1.0, 0.0))
                variants = (
                    (near_zero, near_one, near_zero),
                    (near_one, near_zero, near_zero),
                    (near_one, near_one, Decimal(0)),
                    (Decimal(0), near_one, near_zero),
                )
                decimals = list(variants[index % len(variants)])
            else:
                raise ValueError("unknown probability profile")
            leaves = []
            for suffix, token, probability in zip(("x", "y", "z"), tokens, decimals):
                gate = "numeric:l:%05d:%s" % (index, suffix)
                circ[gate] = ("leaf", token)
                leaves.append(gate)
                decimal_weights[token] = probability
                float_weights[token] = float(probability)
            plus = "numeric:p:%05d" % index
            times = "numeric:t:%05d" % index
            minus = "numeric:m:%05d" % index
            circ[plus] = ("plus", (leaves[0], leaves[1]))
            circ[times] = ("times", (previous, plus))
            circ[minus] = ("minus", (times, leaves[2]))
            p_x, p_y, p_z = decimals
            exact *= (Decimal(1) - (Decimal(1) - p_x) * (Decimal(1) - p_y))
            exact *= Decimal(1) - p_z
            exact_by_gate[minus] = +exact
            previous = minus
            if index + 1 in {max(1, depth // 2), depth}:
                roots["depth:%d" % (index + 1)] = minus
    # Carry the exact values as synthetic const-like metadata outside `circ`.
    roots_with_exact = {key: (gate, exact_by_gate[gate]) for key, gate in roots.items()}
    return circ, roots_with_exact, float_weights, decimal_weights


def _decimal_checksum(values: Mapping[str, Decimal]) -> str:
    digest = hashlib.sha256()
    for key in sorted(values):
        digest.update(key.encode("utf-8"))
        digest.update(str(values[key]).encode("ascii"))
    return digest.hexdigest()


def numerical_error_report(actual: Mapping[str, float],
                           exact: Mapping[str, Decimal]) -> Dict[str, Any]:
    """Classify binary64 error; relative and absolute tolerances must both pass."""
    max_abs = Decimal(0)
    max_rel = Decimal(0)
    underflow = []
    nonfinite = []
    for key in exact:
        value = float(actual[key])
        if not math.isfinite(value):
            nonfinite.append(key)
            continue
        observed = Decimal.from_float(value)
        absolute = abs(observed - exact[key])
        relative = absolute / abs(exact[key]) if exact[key] else absolute
        max_abs = max(max_abs, absolute)
        max_rel = max(max_rel, relative)
        if exact[key] != 0 and value == 0.0:
            underflow.append(key)
    if nonfinite:
        classification = "non-finite-binary64-result"
    elif underflow:
        classification = "underflow-nonzero-exact-to-zero"
    elif max_abs > NUMERICAL_ABS_TOL or max_rel > NUMERICAL_REL_TOL:
        classification = "tolerance-exceeded"
    else:
        classification = "within-tolerance"
    return {
        "ok": classification == "within-tolerance",
        "max_abs_error": str(max_abs),
        "max_rel_error": str(max_rel),
        "underflow_count": len(underflow),
        "numerical_classification": classification,
    }


def _preload_cudd(backend: str) -> None:
    """Import the native backend before the compile wall-clock boundary."""
    if backend == "cudd":
        import dd.cudd  # noqa: F401  # deliberately outside the timed region


def numerical_attempt(task: Mapping[str, Any]) -> Dict[str, Any]:
    circ, roots_with_exact, weights, _ = numerical_instance(
        task["depth"], task["profile"], task["seed"])
    roots = {key: value[0] for key, value in roots_with_exact.items()}
    exact = {key: value[1] for key, value in roots_with_exact.items()}
    backend = task.get("backend", "cudd")
    _preload_cudd(backend)
    source_started = time.perf_counter()
    order_started = source_started
    order = compiler.deterministic_order(circ, roots)
    order_ms = (time.perf_counter() - order_started) * 1000.0
    batch = compiler.compile_many(
        circ, roots, mode=task["compile_mode"], backend=backend,
        order=order,
    )
    source_to_result_ms = (time.perf_counter() - source_started) * 1000.0
    started = time.perf_counter()
    actual = batch.wmc_many(weights)
    wmc_wall_ms = (time.perf_counter() - started) * 1000.0
    comparison = numerical_error_report(actual, exact)
    probability_sum, checksum, _ = granularity.probability_checksums(actual)
    return {
        "status": "ok" if comparison["ok"] else "numerical-mismatch",
        "tokens": len(weights),
        "answers": len(roots),
        "prepare_ms": order_ms + batch.metrics.get("prepare_ms", 0.0),
        "backend_compile_ms": batch.metrics.get(
            "backend_compile_ms", batch.metrics.get("compile_ms", 0.0)
        ),
        "inspect_ms": batch.metrics.get("inspect_ms", 0.0),
        "source_to_result_ms": source_to_result_ms,
        "wmc_ms": batch.metrics["wmc_ms"],
        "wmc_wall_ms": wmc_wall_ms,
        "compiled_nodes_unique": batch.metrics["compiled_nodes_unique"],
        "compiled_nodes_sum_roots": batch.metrics["compiled_nodes_sum_roots"],
        "manager_memory_bytes": batch.metrics["manager_memory_bytes"],
        "manager_peak_live_nodes_upper_bound": batch.metrics[
            "manager_peak_live_nodes_upper_bound"],
        "process_self_peak_rss_bytes": granularity._rss_bytes(),
        "probability_sum": probability_sum,
        "probability_checksum": checksum,
        "exact_probability_checksum": _decimal_checksum(exact),
        **{key: comparison[key] for key in (
            "max_abs_error", "max_rel_error", "underflow_count",
            "numerical_classification")},
        "timing_scope": (
            "python-order+source-preparation | native-manager-build | "
            "compiled-structure-inspection | source-to-compiled-result; WMC separate"
        ),
        "notes": "100-digit Decimal independent-layer recurrence oracle; abs AND rel tolerance",
    }


def treewidth_instance(kind: str, size: int, seed: int) -> Dict[str, Any]:
    if kind == "bounded":
        depth, width = size, 2
    elif kind == "growing":
        depth, width = 4, size
    else:
        raise ValueError("treewidth family must be bounded or growing")
    ttl, query, _metadata = gen_families.layered(depth, width)
    data, patterns, out_vars = _parse_generated_family(ttl, query)
    circuit = gates.Circuit()
    table = factor.factored_bgp(circuit, patterns, data, set(out_vars))
    if not table:
        raise ValueError("treewidth-control instance has no answers")
    answer_key = sorted(table, key=granularity._stable_text)[0]
    roots = {"answer": table[answer_key]}
    order = compiler.deterministic_order(circuit.gates, roots)
    weights, weights_sha = granularity.fixed_weights(order, seed)
    encoded = export_cnf.export(
        circuit.gates, roots["answer"], weights
    )
    evidence = treewidth_evidence.analyze_export(encoded)
    treewidth_evidence.verify_evidence(encoded, evidence)
    return {
        "circ": circuit.gates,
        "roots": roots,
        "order": order,
        "weights": weights,
        "weights_sha256": weights_sha,
        "encoded": encoded,
        "treewidth_document": evidence,
        "tokens": len(data),
        # d4v2 is a single-root compiler, so both backends compile the same
        # one canonical answer.  Do not report every uncompiled table row as
        # an answer in the compiler cell.
        "answers": len(roots),
        "depth_parameter": depth,
        "width_parameter": width,
        "tw_evidence": evidence["schema"],
        "treewidth_graph": treewidth_evidence.GRAPH_SCHEMA,
        "treewidth_nodes": evidence["nodes"],
        "treewidth_edges": evidence["edges"],
        "treewidth_clauses": evidence["clauses"],
        "treewidth_lower_bound": evidence["lower"],
        "treewidth_upper_bound": evidence["upper"],
        "treewidth_cnf_sha256": evidence["cnf_sha256"],
        "treewidth_graph_sha256": evidence["graph_sha256"],
        "treewidth_lower_certificate_sha256": evidence[
            "lower_certificate_sha256"
        ],
        "treewidth_upper_certificate_sha256": evidence[
            "upper_certificate_sha256"
        ],
        "control_note": (
            "generator depth=%d width=%d; selected one canonical answer from %d "
            "candidates; exact d4 Tseitin-CNF primal graph has certified "
            "treewidth interval [%d,%d]"
            % (depth, width, len(table), evidence["lower"], evidence["upper"])),
    }


def _treewidth_metrics(instance: Mapping[str, Any]) -> Dict[str, Any]:
    evidence = instance["treewidth_document"]
    lower, upper = treewidth_evidence.verify_evidence(
        instance["encoded"], evidence
    )
    expected = {
        "tw_evidence": evidence["schema"],
        "treewidth_graph": treewidth_evidence.GRAPH_SCHEMA,
        "treewidth_nodes": evidence["nodes"],
        "treewidth_edges": evidence["edges"],
        "treewidth_clauses": evidence["clauses"],
        "treewidth_lower_bound": lower,
        "treewidth_upper_bound": upper,
        "treewidth_cnf_sha256": evidence["cnf_sha256"],
        "treewidth_graph_sha256": evidence["graph_sha256"],
        "treewidth_lower_certificate_sha256": evidence[
            "lower_certificate_sha256"
        ],
        "treewidth_upper_certificate_sha256": evidence[
            "upper_certificate_sha256"
        ],
    }
    for field, value in expected.items():
        if instance.get(field) != value:
            raise ValueError("treewidth instance evidence field changed: %s" % field)
    return expected


def treewidth_cudd_attempt(task: Mapping[str, Any]) -> Dict[str, Any]:
    instance = task["instance"]
    treewidth_metrics = _treewidth_metrics(instance)
    _preload_cudd("cudd")
    source_started = time.perf_counter()
    batch = compiler.compile_many(
        instance["circ"], instance["roots"], mode="shared", backend="cudd",
        order=instance["order"],
    )
    source_to_result_ms = (time.perf_counter() - source_started) * 1000.0
    started = time.perf_counter()
    probabilities = batch.wmc_many(instance["weights"])
    wmc_wall_ms = (time.perf_counter() - started) * 1000.0
    probability_sum, checksum, _ = granularity.probability_checksums(probabilities)
    return {
        "status": "ok",
        "tokens": instance["tokens"],
        "answers": instance["answers"],
        "depth_parameter": instance["depth_parameter"],
        "width_parameter": instance["width_parameter"],
        **treewidth_metrics,
        "prepare_ms": batch.metrics["prepare_ms"],
        "backend_compile_ms": batch.metrics["backend_compile_ms"],
        "inspect_ms": batch.metrics["inspect_ms"],
        "source_to_result_ms": source_to_result_ms,
        "wmc_ms": batch.metrics["wmc_ms"],
        "wmc_wall_ms": wmc_wall_ms,
        "compiled_nodes_unique": batch.metrics["compiled_nodes_unique"],
        "compiled_nodes_sum_roots": batch.metrics["compiled_nodes_sum_roots"],
        "manager_memory_bytes": batch.metrics["manager_memory_bytes"],
        "manager_peak_live_nodes_upper_bound": batch.metrics[
            "manager_peak_live_nodes_upper_bound"],
        "process_self_peak_rss_bytes": granularity._rss_bytes(),
        "probability_sum": probability_sum,
        "probability_checksum": checksum,
        "timing_scope": (
            "source-support-preparation | native-CUDD-manager-build | "
            "CUDD-node/manager-inspection | source-to-compiled-result; WMC separate"
        ),
        "notes": instance["control_note"],
    }


_ACTIVE_D4_PROCESS = None


def _stop_active_d4() -> None:
    global _ACTIVE_D4_PROCESS
    process = _ACTIVE_D4_PROCESS
    if process is not None and process.poll() is None:
        try:
            process.kill()
        except ProcessLookupError:
            pass
        process.wait()
    _ACTIVE_D4_PROCESS = None


def _write_private_file(path: Path, payload: bytes, label: str) -> Tuple[int, ...]:
    descriptor = _open_single_link(
        path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, label
    )
    try:
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            view = view[written:]
        os.fsync(descriptor)
        current = _validate_opened_single_link(path, descriptor, label)
        return _stat_signature(current)
    finally:
        os.close(descriptor)


def _read_stable_private_file(path: Path, label: str,
                              limit: int = 2 * 1024 * 1024 * 1024) -> bytes:
    descriptor = _open_single_link(path, os.O_RDONLY, label)
    try:
        before = os.fstat(descriptor)
        if before.st_size <= 0 or before.st_size > limit:
            raise ValueError("%s is empty or exceeds the safety cap" % label)
        chunks = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(1024 * 1024, limit + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > limit:
                raise ValueError("%s exceeds the safety cap" % label)
        after = _validate_opened_single_link(path, descriptor, label)
        if _stat_signature(before) != _stat_signature(after):
            raise ValueError("%s changed while it was read" % label)
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _subprocess_failure_status(returncode: int, diagnostic: str) -> str:
    lowered = diagnostic.lower()
    oom_evidence = any(marker in lowered for marker in (
        "out of memory", "std::bad_alloc", "cannot allocate memory",
        "memory allocation failed",
    ))
    if oom_evidence:
        return "oom"
    if returncode < 0 or returncode in (9, 137, 143):
        return "killed-signal"
    return "error"


def treewidth_d4_attempt(task: Mapping[str, Any]) -> Dict[str, Any]:
    global _ACTIVE_D4_PROCESS
    instance = task["instance"]
    treewidth_metrics = _treewidth_metrics(instance)
    source_started = time.perf_counter()
    prepare_started = source_started
    encoded = instance["encoded"]
    input_weights = {}
    for node, variable in encoded["var_of"].items():
        op, payload = instance["circ"][node]
        if op == "leaf":
            probability = instance["weights"][payload]
            input_weights[variable] = (probability, 1.0 - probability)
    with tempfile.TemporaryDirectory(prefix="controlled-d4v2-") as directory:
        os.chmod(directory, 0o700)
        cnf = Path(directory) / "input.cnf"
        nnf = Path(directory) / "output.nnf"
        cnf_signature = _write_private_file(
            cnf, encoded["dimacs"].encode("utf-8"), "staged d4 CNF"
        )
        _fsync_directory(Path(directory))
        prepare_ms = (time.perf_counter() - prepare_started) * 1000.0
        # Publication runs use this exact argv.  In particular, an ambient
        # D4V2_DDNNF_CMD cannot redirect the compiler or output format.
        command = [
            task["d4v2_bin"], "-i", str(cnf), "--dump-file", str(nnf)
        ]
        backend_started = time.perf_counter()
        binary_descriptor = _open_single_link(
            Path(task["d4v2_bin"]), os.O_RDONLY, "d4v2 executable"
        )
        try:
            binary_stat = os.fstat(binary_descriptor)
            expected_signature = task.get("d4v2_signature")
            if (
                expected_signature is not None
                and tuple(expected_signature) != _stat_signature(binary_stat)
            ):
                raise RuntimeError("d4v2 executable changed before process creation")
            descriptor_executable = "/proc/self/fd/%d" % binary_descriptor
            if not Path(descriptor_executable).exists():
                if task.get("formal_run"):
                    raise RuntimeError(
                        "formal d4 requires descriptor-bound /proc/self/fd execution"
                    )
                descriptor_executable = task["d4v2_bin"]
                pass_descriptors: Tuple[int, ...] = ()
            else:
                pass_descriptors = (binary_descriptor,)
            _ACTIVE_D4_PROCESS = subprocess.Popen(
                command, executable=descriptor_executable,
                pass_fds=pass_descriptors,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
        finally:
            os.close(binary_descriptor)
        try:
            stdout, stderr = _ACTIVE_D4_PROCESS.communicate(
                timeout=max(0.1, float(task["timeout"]) - 1.0))
        except subprocess.TimeoutExpired:
            _stop_active_d4()
            return {
                "status": "timeout",
                "prepare_ms": prepare_ms,
                "backend_compile_ms": (
                    time.perf_counter() - backend_started
                ) * 1000.0,
                "d4_argv_sha256": D4_ARGV_SHA256,
                "notes": "d4v2 exceeded internal compiler deadline",
            }
        returncode = _ACTIVE_D4_PROCESS.returncode
        _ACTIVE_D4_PROCESS = None
        backend_compile_ms = (time.perf_counter() - backend_started) * 1000.0
        child_rss = int(resource.getrusage(
            resource.RUSAGE_CHILDREN
        ).ru_maxrss) * 1024
        if returncode:
            diagnostic = (stderr or stdout)[-500:].replace("\n", " ")
            status = _subprocess_failure_status(returncode, diagnostic)
            return {
                "status": status,
                "prepare_ms": prepare_ms,
                "backend_compile_ms": backend_compile_ms,
                "d4_argv_sha256": D4_ARGV_SHA256,
                "compiler_child_peak_rss_bytes": child_rss,
                "notes": diagnostic or "d4v2 exited by status %d" % returncode,
            }
        try:
            current_cnf = os.lstat(cnf)
        except OSError as exc:
            raise RuntimeError("staged d4 CNF disappeared") from exc
        if (
            not stat.S_ISREG(current_cnf.st_mode)
            or current_cnf.st_nlink != 1
            or _stat_signature(current_cnf) != cnf_signature
        ):
            raise RuntimeError("staged d4 CNF changed during compilation")
        inspect_started = time.perf_counter()
        try:
            nnf_payload = _read_stable_private_file(nnf, "staged d4 d-DNNF")
            nnf_text = nnf_payload.decode("utf-8", "strict")
        except (OSError, ValueError, UnicodeError) as exc:
            return {
                "status": "error", "prepare_ms": prepare_ms,
                "backend_compile_ms": backend_compile_ms,
                "inspect_ms": (time.perf_counter() - inspect_started) * 1000.0,
                "d4_argv_sha256": D4_ARGV_SHA256,
                "compiler_child_peak_rss_bytes": child_rss,
                "notes": "d4v2 emitted unsafe/invalid d-DNNF: %s" % exc,
            }
        inspect_ms = (time.perf_counter() - inspect_started) * 1000.0
        source_to_result_ms = (time.perf_counter() - source_started) * 1000.0
        wmc_started = time.perf_counter()
        evaluated = ddnnf_wmc.evaluate_text(nnf_text, input_weights)
        wmc_ms = (time.perf_counter() - wmc_started) * 1000.0
    values = {"answer": evaluated.probability}
    probability_sum, checksum, _ = granularity.probability_checksums(values)
    return {
        "status": "ok",
        "tokens": instance["tokens"],
        "answers": instance["answers"],
        "depth_parameter": instance["depth_parameter"],
        "width_parameter": instance["width_parameter"],
        **treewidth_metrics,
        "prepare_ms": prepare_ms,
        "backend_compile_ms": backend_compile_ms,
        "inspect_ms": inspect_ms,
        "source_to_result_ms": source_to_result_ms,
        "d4_argv_sha256": D4_ARGV_SHA256,
        "wmc_ms": wmc_ms,
        "wmc_wall_ms": wmc_ms,
        "cnf_vars": encoded["nvars"],
        "cnf_clauses": encoded["nclauses"],
        "ddnnf_nodes": evaluated.nodes,
        "ddnnf_edges": evaluated.edges,
        "process_self_peak_rss_bytes": granularity._rss_bytes(),
        "compiler_child_peak_rss_bytes": child_rss,
        "probability_sum": probability_sum,
        "probability_checksum": checksum,
        "timing_scope": (
            "CNF-export+secure-stage | d4-subprocess-only | stable-output-read | "
            "source-to-d-DNNF-bytes; parse+WMC separate"
        ),
        "notes": instance["control_note"],
    }


def _dispatch(task: Mapping[str, Any]) -> Dict[str, Any]:
    if task["kind"] == "construction":
        return construction_attempt(task)
    if task["kind"] == "numerical":
        return numerical_attempt(task)
    if task["kind"] == "treewidth-cudd":
        return treewidth_cudd_attempt(task)
    if task["kind"] == "treewidth-d4v2":
        return treewidth_d4_attempt(task)
    raise ValueError("unknown controlled task kind")


def _worker(send, ready, task: Mapping[str, Any]) -> None:
    try:
        os.setsid()
    except OSError as exc:
        ready.send(("error", str(exc)))
        ready.close()
        send.send({"status": "error", "notes": "worker setsid failed"})
        send.close()
        return
    ready.send(("ready", os.getpid()))
    ready.close()

    def terminated(_signum, _frame):
        _stop_active_d4()
        os._exit(143)

    signal.signal(signal.SIGTERM, terminated)
    try:
        payload = _dispatch(task)
    except MemoryError as exc:
        payload = {"status": "oom", "notes": str(exc)[:500]}
    except BaseException as exc:
        payload = {
            "status": "error",
            "notes": (type(exc).__name__ + ": " + " ".join(str(exc).splitlines()))[:500],
            "traceback": traceback.format_exc(limit=8)[-2000:],
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
            names = list(proc_root.iterdir())
        except OSError:
            names = []
        for entry in names:
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
    """Terminate/reap the worker and every subprocess in its private session."""
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
    reaped = not process.is_alive() and (not group_ready or not _process_group_exists(pgid))
    return {
        "cleanup_ms": (time.monotonic() - started) * 1000.0,
        "cleanup_action": "+".join(actions) if actions else "none",
        "process_group_reaped": reaped,
    }


def run_killable(task: Mapping[str, Any], timeout: float, context=None) -> Dict[str, Any]:
    if timeout <= 0:
        raise ValueError("timeout must be positive")
    context = context or granularity._mp_context()
    receive, send = context.Pipe(duplex=False)
    ready_receive, ready_send = context.Pipe(duplex=False)
    process = context.Process(target=_worker, args=(send, ready_send, dict(task)))
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


def _schedule(warmups: int, runs: int) -> List[Tuple[str, int]]:
    return ([('warmup', index) for index in range(warmups)]
            + [('measured', index) for index in range(runs)])


def canonical_run_config(args: argparse.Namespace) -> Dict[str, Any]:
    """The complete, path-independent identity of one controlled invocation."""
    return {
        "schema": SCHEMA,
        "batch_id": args.batch_id,
        "frozen_inputs_sha256": args.frozen_inputs_sha256,
        "protocol": args.protocol,
        "git": {"commit": args.git_commit, "dirty": args.git_dirty},
        "selection": {
            "experiments": list(args.experiments),
            "construction_shapes": list(args.construction_shapes),
            "construction_sizes": list(args.construction_sizes),
            "numerical_depths": list(args.numerical_depths),
            "bounded_depths": list(args.bounded_depths),
            "growing_widths": list(args.growing_widths),
        },
        "protocol_parameters": {
            "seed": args.seed,
            "warmups": args.warmups,
            "runs": args.runs,
            "timeout_s": args.timeout,
            "strict_mode": args.strict_mode,
            "failure_policy": "terminal-failure-blocks-cell-no-retry",
        },
        "treewidth_evidence": {
            "schema": treewidth_evidence.SCHEMA,
            "graph_schema": treewidth_evidence.GRAPH_SCHEMA,
            "formal_intervals": [
                [family, size, lower, upper]
                for (family, size), (lower, upper)
                in sorted(FORMAL_TREEWIDTH_INTERVALS.items())
            ],
        },
        "backend": {
            "cudd": args.backend_version,
            "python": "%d.%d.%d" % sys.version_info[:3],
            "cudd_extension": {
                "bytes": args.cudd_extension_bytes,
                "sha256": args.cudd_extension_sha256,
            },
            "python_runtime": {
                "bytes": args.python_runtime_bytes,
                "sha256": args.python_runtime_sha256,
            },
        },
        "tool": {
            "d4_selected": "treewidth" in args.experiments,
            "d4_tool_name": args.d4_tool_name,
            "d4v2_bytes": args.d4v2_bytes,
            "d4v2_sha256": args.d4v2_sha256,
            "d4_argv_sha256": args.d4_argv_sha256,
            "expected_d4_sha256": args.expected_d4_sha256,
        },
        "required_data": list(args.required_data),
        "freeze": {
            "formal_run": args.formal_run,
            "allow_unfrozen": args.allow_unfrozen,
            "allow_dirty": args.allow_dirty,
            "expected_protocol": args.expected_protocol or "",
        },
    }


def _blank_row(task: Mapping[str, Any], phase: str, rep: int,
               args: argparse.Namespace) -> Dict[str, Any]:
    row = {field: "" for field in FIELDS}
    row.update({
        "schema": SCHEMA,
        "batch_id": args.batch_id,
        "frozen_inputs_sha256": args.frozen_inputs_sha256,
        "protocol": args.protocol,
        "run_config_sha256": args.run_config_sha256,
        "formal_run": args.formal_run,
        "strict_mode": args.strict_mode,
        "git_commit": args.git_commit,
        "git_dirty": args.git_dirty,
        "experiment": task["experiment"],
        "instance_id": task["instance_id"],
        "family": task.get("family", ""),
        "shape": task.get("shape", ""),
        "size": task.get("size", ""),
        "depth_parameter": task.get("depth", task.get("depth_parameter", "")),
        "width_parameter": task.get("width_parameter", ""),
        "tw_evidence": task.get("tw_evidence", ""),
        **{
            field: task.get(field, "")
            for field in TREEWIDTH_IDENTITY_FIELDS
            if field != "tw_evidence"
        },
        "method": task["method"],
        "construction_mode": task.get("construction_mode", ""),
        "compiler": task.get("compiler", ""),
        "compile_mode": task.get("compile_mode", ""),
        "probability_profile": task.get("profile", ""),
        "phase": phase,
        "rep": rep,
        "warmups": args.warmups,
        "runs": args.runs,
        "timeout_s": args.timeout,
        "seed": args.seed,
        "backend_version": args.backend_version,
        "cudd_extension_sha256": args.cudd_extension_sha256,
        "cudd_extension_bytes": args.cudd_extension_bytes,
        "python_runtime_sha256": args.python_runtime_sha256,
        "python_runtime_bytes": args.python_runtime_bytes,
        "d4v2_path": args.d4v2_path,
        "d4v2_sha256": args.d4v2_sha256,
        "d4v2_bytes": args.d4v2_bytes,
        "d4_argv_sha256": args.d4_argv_sha256,
    })
    return row


def _row_key(row: Mapping[str, Any]) -> Tuple[str, ...]:
    return tuple(str(row.get(field, "")) for field in (
        "batch_id", "run_config_sha256", "experiment", "instance_id", "method",
        "phase", "rep",
    ))


def _read_checkpoint_payload(path: Path, *, repair: bool) -> bytes:
    path = Path(path)
    if not path.exists() and not path.is_symlink():
        return b""
    descriptor = _open_single_link(
        path, os.O_RDWR if repair else os.O_RDONLY, "controlled checkpoint"
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
        _validate_opened_single_link(path, descriptor, "controlled checkpoint")
        return bytes(payload)
    finally:
        os.close(descriptor)


def _serialize_checkpoint(rows: Sequence[Mapping[str, Any]]) -> bytes:
    """Return the one physical CSV representation accepted by this protocol."""
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
    existing = _read_checkpoint_payload(path, repair=True)
    encoded = _serialize_checkpoint([{field: row.get(field, "") for field in FIELDS}])
    if existing:
        header_end = encoded.find(b"\n") + 1
        encoded = encoded[header_end:]
    flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
    descriptor = _open_single_link(path, flags, "controlled checkpoint")
    try:
        view = memoryview(encoded)
        while view:
            written = os.write(descriptor, view)
            view = view[written:]
        os.fsync(descriptor)
        _validate_opened_single_link(path, descriptor, "controlled checkpoint")
    finally:
        os.close(descriptor)
    _fsync_directory(path.parent)


def _baseline_key(row: Mapping[str, Any]) -> Tuple[str, ...]:
    return tuple(str(row.get(field, "")) for field in (
        "batch_id", "run_config_sha256", "experiment", "instance_id",
        "probability_profile", "seed",
    ))


def validate_checkpoint_scope(rows: Sequence[Mapping[str, Any]],
                              args: argparse.Namespace) -> None:
    """One output file is one immutable batch/config/backend identity."""
    expected = {
        "batch_id": args.batch_id,
        "frozen_inputs_sha256": args.frozen_inputs_sha256,
        "protocol": args.protocol,
        "run_config_sha256": args.run_config_sha256,
        "formal_run": str(args.formal_run),
        "strict_mode": str(args.strict_mode),
        "git_commit": args.git_commit,
        "git_dirty": args.git_dirty,
        "backend_version": args.backend_version,
        "cudd_extension_sha256": args.cudd_extension_sha256,
        "cudd_extension_bytes": args.cudd_extension_bytes,
        "python_runtime_sha256": args.python_runtime_sha256,
        "python_runtime_bytes": args.python_runtime_bytes,
        "d4v2_sha256": args.d4v2_sha256,
        "d4v2_bytes": args.d4v2_bytes,
        "d4_argv_sha256": args.d4_argv_sha256,
    }
    for row in rows:
        for field, value in expected.items():
            if str(row.get(field, "")) != str(value):
                raise ValueError(
                    "checkpoint mixes a different batch/config/backend identity (%s)" % field)


def _set_parity(row: Dict[str, Any], baseline: Optional[Mapping[str, Any]]) -> None:
    if row.get("status") != "ok":
        row["parity"] = "unverified"
        return
    if row["experiment"] == "numerical":
        if baseline is None:
            row["parity"] = "baseline"
        else:
            row["parity"] = (
                "ok" if row.get("probability_checksum") == baseline.get("probability_checksum")
                else "mismatch")
        return
    checksum_field = "semantic_checksum" if row["experiment"] == "construction" else None
    if baseline is None:
        row["parity"] = "baseline"
    elif checksum_field:
        row["parity"] = (
            "ok" if row.get(checksum_field) == baseline.get(checksum_field) else "mismatch")
    else:
        try:
            current = float(row["probability_sum"])
            expected = float(baseline["probability_sum"])
            row["parity"] = (
                "ok" if abs(current - expected) <= 1e-8 * max(1.0, abs(expected))
                else "mismatch")
        except (KeyError, TypeError, ValueError):
            row["parity"] = "mismatch"


def build_tasks(args: argparse.Namespace) -> List[Dict[str, Any]]:
    tasks = []
    selected = set(args.experiments)
    if "construction" in selected:
        for shape in args.construction_shapes:
            for size in args.construction_sizes:
                depth_parameter = size if shape == "chain" else (4 if shape == "layered" else 3)
                width_parameter = 1 if shape == "chain" else size
                for mode in ("flat", "factored"):
                    tasks.append({
                        "kind": "construction", "experiment": "construction",
                        "instance_id": "construction:%s:s%d" % (shape, size),
                        "family": shape, "shape": shape, "size": size,
                        "depth_parameter": depth_parameter,
                        "width_parameter": width_parameter,
                        "method": mode, "construction_mode": mode, "seed": args.seed,
                    })
    if "numerical" in selected:
        for profile in ("uniform", "nonuniform", "extreme"):
            for depth in args.numerical_depths:
                for mode in ("shared", "per-root"):
                    tasks.append({
                        "kind": "numerical", "experiment": "numerical",
                        "instance_id": "numerical:%s:d%d" % (profile, depth),
                        "family": "independent-layer-recurrence", "shape": "deep-dag",
                        "size": depth, "depth": depth, "profile": profile,
                        "method": "cudd-" + mode, "compiler": "cudd",
                        "compile_mode": mode, "seed": args.seed,
                    })
    if "treewidth" in selected:
        controls = []
        controls.extend(("bounded", size) for size in args.bounded_depths)
        controls.extend(("growing", size) for size in args.growing_widths)
        for family, size in controls:
            instance = treewidth_instance(family, size, args.seed)
            if args.formal_run:
                expected_interval = FORMAL_TREEWIDTH_INTERVALS.get((family, size))
                observed_interval = (
                    instance["treewidth_lower_bound"],
                    instance["treewidth_upper_bound"],
                )
                if expected_interval is None or observed_interval != expected_interval:
                    raise RuntimeError(
                        "formal treewidth certificate interval changed for %s:%s: "
                        "%r != %r"
                        % (family, size, observed_interval, expected_interval)
                    )
            for backend in ("cudd", "d4v2"):
                tasks.append({
                    "kind": "treewidth-" + backend, "experiment": "treewidth",
                    "instance_id": "treewidth:%s:s%d" % (family, size),
                    "family": family, "shape": "layered", "size": size,
                    "depth_parameter": instance["depth_parameter"],
                    "width_parameter": instance["width_parameter"],
                    **{
                        field: instance[field]
                        for field in TREEWIDTH_IDENTITY_FIELDS
                    },
                    "method": backend, "compiler": backend,
                    "compile_mode": "shared" if backend == "cudd" else "single-root",
                    "seed": args.seed, "instance": instance,
                    "d4v2_bin": args.d4v2_path, "timeout": args.timeout,
                    "d4v2_signature": (
                        getattr(args, "_d4_snapshot", None)["signature"]
                        if getattr(args, "_d4_snapshot", None) is not None else None
                    ),
                    "formal_run": args.formal_run,
                })
    return tasks


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
        try:
            value = float(raw)
        except ValueError as exc:
            raise ValueError("checkpoint requires finite metric %s" % field) from exc
        if raw != repr(value) or (value == 0.0 and raw.startswith("-")):
            raise ValueError("checkpoint metric %s is not canonical" % field)
    if not math.isfinite(value) or value < 0 or (positive and value <= 0):
        raise ValueError("checkpoint metric %s is outside its valid range" % field)
    return value


def _decimal_number(row: Mapping[str, Any], field: str) -> Decimal:
    raw = row.get(field, "")
    if type(raw) is not str or not raw or raw.strip() != raw:
        raise ValueError("checkpoint decimal metric %s is not canonical" % field)
    try:
        value = Decimal(raw)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("checkpoint decimal metric %s is invalid" % field) from exc
    if (
        not value.is_finite()
        or value < 0
        or value.is_signed()
        or str(value) != raw
    ):
        raise ValueError("checkpoint decimal metric %s is not canonical" % field)
    return value


def _checksum(row: Mapping[str, Any], field: str) -> None:
    if re.fullmatch(r"[0-9a-f]{64}", str(row.get(field, ""))) is None:
        raise ValueError("checkpoint requires lowercase SHA-256 field %s" % field)


def _validate_attempt_metrics(row: Mapping[str, Any]) -> None:
    status = str(row.get("status", ""))
    if status == "not-run":
        return
    _number(row, "attempt_wall_ms")
    _number(row, "cleanup_ms")
    if not str(row.get("cleanup_action", "")):
        raise ValueError("attempt row lacks cleanup_action")
    reaped = str(row.get("process_group_reaped", ""))
    expected_reaped = "False" if status == "cleanup-error" else "True"
    if reaped != expected_reaped:
        raise ValueError(
            "process_group_reaped=false is permitted only as fatal cleanup-error"
        )
    if not str(row.get("notes", "")) and status != "ok":
        raise ValueError("non-ok attempt row lacks diagnostic notes")
    if status not in ("ok", "numerical-mismatch"):
        return

    experiment = row.get("experiment")
    for field in ("tokens", "answers"):
        _number(row, field, integer=True)
    if experiment == "construction":
        _number(row, "build_ms")
        for field in ("process_self_peak_rss_bytes",
                      "process_self_rss_delta_bytes"):
            _number(row, field, integer=True)
        for field in ("gates", "edges", "circuit_bytes", "parity_worlds"):
            _number(row, field, integer=True, positive=field in ("gates", "circuit_bytes"))
        _checksum(row, "semantic_checksum")
        parity_kind = row.get("parity_kind")
        if parity_kind not in ("exhaustive", "seeded-worlds-smoke"):
            raise ValueError("construction parity_kind is not an honest evidence label")
        if parity_kind == "seeded-worlds-smoke" and int(float(row["parity_worlds"])) != 64:
            raise ValueError("sampled construction evidence must retain exactly 64 worlds")
        return

    for field in (
        "prepare_ms", "backend_compile_ms", "inspect_ms",
        "source_to_result_ms", "wmc_ms", "wmc_wall_ms",
    ):
        _number(row, field)
    _number(row, "process_self_peak_rss_bytes", integer=True)
    if not str(row.get("timing_scope", "")):
        raise ValueError("compiler row lacks an explicit timing_scope")
    if _number(row, "source_to_result_ms") + 1e-6 < _number(
        row, "backend_compile_ms"
    ):
        raise ValueError("source_to_result_ms is smaller than backend_compile_ms")
    for field in ("probability_sum",):
        _number(row, field)
    _checksum(row, "probability_checksum")

    if experiment == "numerical":
        for field in (
            "compiled_nodes_unique", "compiled_nodes_sum_roots",
            "manager_memory_bytes", "manager_peak_live_nodes_upper_bound",
            "underflow_count",
        ):
            _number(row, field, integer=True)
        for field in ("max_abs_error", "max_rel_error"):
            _decimal_number(row, field)
        _checksum(row, "exact_probability_checksum")
        if row.get("numerical_classification") not in (
            "within-tolerance", "underflow-nonzero-exact-to-zero",
            "tolerance-exceeded", "non-finite-binary64-result",
        ):
            raise ValueError("numerical row lacks a valid classification")
        if (status == "ok") != (
            row.get("numerical_classification") == "within-tolerance"
        ):
            raise ValueError("numerical status/classification disagree")
        return

    if experiment != "treewidth":
        raise ValueError("compiler row has an unknown experiment")
    if row.get("tw_evidence") != treewidth_evidence.SCHEMA:
        raise ValueError("treewidth row lost its certified evidence schema")
    if row.get("treewidth_graph") != treewidth_evidence.GRAPH_SCHEMA:
        raise ValueError("treewidth row has an unknown graph definition")
    treewidth_nodes = _number(row, "treewidth_nodes", integer=True, positive=True)
    _number(row, "treewidth_edges", integer=True)
    _number(row, "treewidth_clauses", integer=True, positive=True)
    lower = _number(row, "treewidth_lower_bound", integer=True)
    upper = _number(row, "treewidth_upper_bound", integer=True)
    if lower > upper or upper >= treewidth_nodes:
        raise ValueError("treewidth certified interval is invalid")
    for field in (
        "treewidth_cnf_sha256", "treewidth_graph_sha256",
        "treewidth_lower_certificate_sha256",
        "treewidth_upper_certificate_sha256",
    ):
        _checksum(row, field)
    if row.get("method") == "cudd":
        for field in (
            "compiled_nodes_unique", "compiled_nodes_sum_roots",
            "manager_memory_bytes", "manager_peak_live_nodes_upper_bound",
        ):
            _number(row, field, integer=True)
    elif row.get("method") == "d4v2":
        for field in (
            "cnf_vars", "cnf_clauses", "ddnnf_nodes", "ddnnf_edges",
            "compiler_child_peak_rss_bytes",
        ):
            _number(row, field, integer=True)
        if int(row["cnf_vars"]) != treewidth_nodes:
            raise ValueError("d4 CNF variable count differs from certified graph")
        if int(row["cnf_clauses"]) != int(row["treewidth_clauses"]):
            raise ValueError("d4 CNF clause count differs from certified graph")
        if row.get("d4_argv_sha256") != D4_ARGV_SHA256:
            raise ValueError("d4 row argv protocol differs from the fixed command")
    else:
        raise ValueError("unknown treewidth method in checkpoint")


def _not_run_row(task: Mapping[str, Any], phase: str, rep: int,
                 args: argparse.Namespace) -> Dict[str, Any]:
    row = _blank_row(task, phase, rep, args)
    row.update({
        "status": "not-run",
        "parity": "unverified",
        "notes": NOT_RUN_NOTE,
    })
    return row


def _canonical_slots(tasks: Sequence[Mapping[str, Any]],
                     args: argparse.Namespace) -> List[Tuple[Mapping[str, Any], str, int]]:
    return [
        (task, phase, rep)
        for task in tasks
        for phase, rep in _schedule(args.warmups, args.runs)
    ]


def validate_checkpoint(rows: Sequence[Mapping[str, Any]],
                        tasks: Sequence[Mapping[str, Any]],
                        args: argparse.Namespace, *, require_complete: bool = False) -> None:
    """Validate one immutable tasks×schedule canonical prefix and recompute parity."""
    validate_checkpoint_scope(rows, args)
    slots = _canonical_slots(tasks, args)
    if len(rows) > len(slots) or (require_complete and len(rows) != len(slots)):
        raise ValueError("checkpoint is not the required tasks x schedule cardinality")
    baselines: Dict[Tuple[str, ...], Mapping[str, Any]] = {}
    blocked_task: Optional[Tuple[str, str]] = None
    for index, row in enumerate(rows):
        task, phase, rep = slots[index]
        task_key = (task["instance_id"], task["method"])
        expected = _blank_row(task, phase, rep, args)
        for field in FIELDS[:FIELDS.index("status")]:
            if str(row.get(field, "")) != str(expected.get(field, "")):
                raise ValueError(
                    "checkpoint is not a canonical tasks x schedule prefix (%s)" % field
                )
        if blocked_task == task_key:
            required = _not_run_row(task, phase, rep, args)
            if any(str(row.get(field, "")) != str(required.get(field, ""))
                   for field in FIELDS):
                raise ValueError("rows after a cell failure must be exact not-run rows")
            continue
        if blocked_task is not None and blocked_task != task_key:
            blocked_task = None
        if row.get("status") == "not-run":
            raise ValueError("checkpoint contains an isolated not-run row")
        _validate_attempt_metrics(row)
        recomputed = dict(row)
        baseline_id = _baseline_key(row)
        baseline = baselines.get(baseline_id)
        _set_parity(recomputed, baseline)
        if row.get("parity") != recomputed.get("parity"):
            raise ValueError("checkpoint parity does not match recomputed evidence")
        if row.get("status") == "ok" and baseline is None:
            baselines[baseline_id] = row
        if row.get("status") != "ok" or row.get("parity") == "mismatch":
            blocked_task = task_key


def _completion_path(output: Path) -> Path:
    return output.with_name(output.name + ".complete.json")


def _checkpoint_identity(output: Path) -> Dict[str, Any]:
    payload = _read_checkpoint_payload(output, repair=False)
    if not payload or not payload.endswith(b"\n"):
        raise ValueError("completed checkpoint is empty or physically torn")
    return {
        "csv_sha256": hashlib.sha256(payload).hexdigest(),
        "csv_bytes": len(payload),
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
    if type(checkpoint) is not dict or set(checkpoint) != {
        "csv_sha256", "csv_bytes",
    }:
        raise ValueError("completion checkpoint identity schema mismatch")
    if (
        type(checkpoint["csv_sha256"]) is not str
        or re.fullmatch(r"[0-9a-f]{64}", checkpoint["csv_sha256"]) is None
    ):
        raise ValueError("completion checkpoint SHA-256 is invalid")
    if (
        type(checkpoint["csv_bytes"]) is not int
        or checkpoint["csv_bytes"] <= 0
        or checkpoint["csv_bytes"] > (2 ** 63 - 1)
    ):
        raise ValueError("completion checkpoint byte count is invalid")
    if (
        type(document["rows"]) is not int
        or document["rows"] <= 0
        or document["rows"] > (2 ** 63 - 1)
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


def _load_completion(output: Path) -> Optional[Dict[str, Any]]:
    path = _completion_path(output)
    if not path.exists() and not path.is_symlink():
        return None
    payload = _read_stable_private_file(path, "controlled completion sidecar", 8 * 1024 * 1024)
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
    existing = _load_completion(output)
    expected = _completion_payload(output, rows, args)
    _validate_completion_document(expected)
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
        os.close(descriptor)
        descriptor = -1
        if path.exists() or path.is_symlink():
            raise ValueError("completion sidecar appeared during atomic publication")
        os.replace(temporary_path, path)
        _fsync_directory(path.parent)
        check = _open_single_link(path, os.O_RDONLY, "controlled completion sidecar")
        os.close(check)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass


def validate_formal_configuration(args: argparse.Namespace) -> None:
    """Formal publication runs may shard only by complete predefined groups."""
    if not args.formal_run:
        return
    expected = {
        "expected_protocol": FORMAL_PROTOCOL,
        "seed": FORMAL_SEED,
        "warmups": FORMAL_WARMUPS,
        "runs": FORMAL_RUNS,
        "timeout": FORMAL_TIMEOUT_S,
        "construction_shapes": FORMAL_CONSTRUCTION_SHAPES,
        "construction_sizes": FORMAL_CONSTRUCTION_SIZES,
        "numerical_depths": FORMAL_NUMERICAL_DEPTHS,
        "bounded_depths": FORMAL_BOUNDED_DEPTHS,
        "growing_widths": FORMAL_GROWING_WIDTHS,
        "d4_tool_name": D4_TOOL_NAME,
        "expected_d4_sha256": PINNED_D4V2_SHA256,
        "required_data": (),
    }
    for field, value in expected.items():
        if getattr(args, field) != value:
            raise ValueError(
                "formal controlled configuration fixes %s=%r" % (field, value)
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
    python = _snapshot_file(Path(sys.executable), PYTHON_TOOL_NAME)
    version = "dd=%s;cudd=%s" % (
        getattr(dd, "__version__", "unknown"),
        getattr(dd.cudd, "__version__", "unknown"),
    )
    return version, {CUDD_TOOL_NAME: cudd, PYTHON_TOOL_NAME: python}


def _run_experiment_locked(args: argparse.Namespace) -> Dict[str, Any]:
    require_d4 = "treewidth" in args.experiments
    require_cudd = bool({"numerical", "treewidth"} & set(args.experiments))
    args.formal_run = not args.allow_unfrozen and not args.allow_dirty
    args.strict_mode = bool(args.formal_strict or args.formal_run)
    validate_formal_configuration(args)
    args.git_commit, args.git_dirty = granularity._git_identity()
    validate_git_identity(args.git_commit, args.git_dirty, args.allow_dirty)
    if args.formal_run:
        validate_no_hidden_index_bits()

    args._artifact_snapshots = []
    args._actual_tools = {}
    args.cudd_extension_sha256 = ""
    args.cudd_extension_bytes = ""
    args.python_runtime_sha256 = ""
    args.python_runtime_bytes = ""
    args.backend_version = "not-used"
    if require_cudd:
        args.backend_version, cudd_tools = _discover_cudd_runtime()
        args._actual_tools.update(cudd_tools)
        args._artifact_snapshots.extend(cudd_tools.values())
        args.cudd_extension_sha256 = cudd_tools[CUDD_TOOL_NAME]["sha256"]
        args.cudd_extension_bytes = cudd_tools[CUDD_TOOL_NAME]["bytes"]
        args.python_runtime_sha256 = cudd_tools[PYTHON_TOOL_NAME]["sha256"]
        args.python_runtime_bytes = cudd_tools[PYTHON_TOOL_NAME]["bytes"]
    else:
        python_runtime = _snapshot_file(Path(sys.executable), PYTHON_TOOL_NAME)
        args._actual_tools[PYTHON_TOOL_NAME] = python_runtime
        args._artifact_snapshots.append(python_runtime)
        args.python_runtime_sha256 = python_runtime["sha256"]
        args.python_runtime_bytes = python_runtime["bytes"]

    args.d4v2_path = ""
    args.d4v2_sha256 = ""
    args.d4v2_bytes = ""
    args.d4_argv_sha256 = ""
    args._d4_snapshot = None
    if require_d4:
        if args.formal_run and args.expected_d4_sha256 != PINNED_D4V2_SHA256:
            raise RuntimeError("formal runs require the repository-pinned d4v2 SHA-256")
        d4_snapshot = _snapshot_file(Path(args.d4v2_bin), "d4v2 executable")
        args._d4_snapshot = d4_snapshot
        if not os.access(d4_snapshot["path"], os.X_OK):
            raise RuntimeError("pinned d4v2 executable is not executable")
        args.d4v2_path = d4_snapshot["path"]
        args.d4v2_sha256 = d4_snapshot["sha256"]
        args.d4v2_bytes = d4_snapshot["bytes"]
        args.d4_argv_sha256 = D4_ARGV_SHA256
        if args.d4v2_sha256 != args.expected_d4_sha256:
            raise RuntimeError(
                "d4v2 SHA-256 mismatch: %s != %s"
                % (args.d4v2_sha256, args.expected_d4_sha256))
        d4_name = D4_TOOL_NAME if args.formal_run else args.d4_tool_name
        args._actual_tools[d4_name] = d4_snapshot
        args._artifact_snapshots.append(d4_snapshot)

    provenance = resolve_provenance(
        args, args.git_commit, require_d4, args._actual_tools
    )
    args.batch_id = provenance["batch_id"]
    args.frozen_inputs_sha256 = provenance["frozen_inputs_sha256"]
    args.protocol = provenance["protocol"]
    args._frozen_snapshot = provenance.get("frozen_snapshot")
    _reject_output_input_aliases(
        args.output,
        [
            *args._artifact_snapshots,
            *([args._frozen_snapshot] if args._frozen_snapshot is not None else []),
        ],
    )
    args.run_config = canonical_run_config(args)
    args.run_config_sha256 = freeze_inputs.canonical_batch_id(args.run_config)

    tasks = build_tasks(args)
    # A completion sidecar makes the CSV immutable evidence.  Probe it before
    # reading the checkpoint so a forged/torn append cannot be silently
    # truncated back to the sidecar's recorded digest.  Only an unfinished
    # checkpoint is eligible for torn-tail recovery.
    completion = _load_completion(args.output)
    rows = load_checkpoint(args.output, repair=completion is None)
    validate_checkpoint(rows, tasks, args)
    if completion is not None:
        validate_checkpoint(rows, tasks, args, require_complete=True)
        validate_completion(args.output, rows, args, required=True)
    existing = {_row_key(row): row for row in rows}
    baselines = {}
    for row in rows:
        if row.get("status") == "ok" and row.get("parity") in ("baseline", "ok"):
            baselines.setdefault(_baseline_key(row), row)
    attempted = skipped = failures = 0
    for task in tasks:
        stop_cell = False
        for phase, rep in _schedule(args.warmups, args.runs):
            row = _blank_row(task, phase, rep, args)
            key = _row_key(row)
            prior = existing.get(key)
            if prior is not None:
                skipped += 1
                if prior.get("status") != "ok" or prior.get("parity") == "mismatch":
                    stop_cell = True
                continue
            if stop_cell:
                row = _not_run_row(task, phase, rep, args)
                serialized = {
                    field: str(row.get(field, "")) for field in FIELDS
                }
                validate_checkpoint([*rows, serialized], tasks, args)
                append_checkpoint(args.output, row)
                existing[key] = row
                rows.append(serialized)
                continue
            print("# %s %s %s[%d]" % (
                task["instance_id"], task["method"], phase, rep),
                file=sys.stderr, flush=True)
            result = run_killable(task, args.timeout)
            attempted += 1
            row.update(result)
            baseline_id = _baseline_key(row)
            baseline = baselines.get(baseline_id)
            _set_parity(row, baseline)
            serialized = {field: str(row.get(field, "")) for field in FIELDS}
            validate_checkpoint([*rows, serialized], tasks, args)
            append_checkpoint(args.output, row)
            existing[key] = row
            rows.append(serialized)
            if row["status"] == "ok" and baseline is None:
                baselines[baseline_id] = row
            if row["status"] != "ok" or row.get("parity") == "mismatch":
                failures += 1
                stop_cell = True

    all_final_rows = load_checkpoint(args.output, repair=False)
    validate_checkpoint(all_final_rows, tasks, args, require_complete=True)
    final_rows = all_final_rows

    if freeze_inputs.canonical_batch_id(canonical_run_config(args)) != (
        args.run_config_sha256
    ):
        raise RuntimeError("run_config changed during the experiment")
    for snapshot in args._artifact_snapshots:
        _verify_snapshot(snapshot)
    if args._frozen_snapshot is not None:
        _verify_snapshot(args._frozen_snapshot)
        end_provenance = validate_frozen_inputs(
            args.frozen_inputs,
            batch_id=args.batch_id,
            expected_protocol=args.expected_protocol,
            current_commit=args.git_commit,
            require_d4=require_d4,
            d4_sha256=args.d4v2_sha256,
            d4_tool_name=args.d4_tool_name,
            required_data=args.required_data,
            require_formal=args.formal_run,
            actual_tools=args._actual_tools,
            frozen_snapshot=args._frozen_snapshot,
        )
        if end_provenance["batch_id"] != args.batch_id:
            raise RuntimeError("frozen batch changed during the experiment")

    end_commit, end_dirty = granularity._git_identity()
    if (end_commit, end_dirty) != (args.git_commit, args.git_dirty):
        raise RuntimeError("Git HEAD/clean identity changed during the experiment")
    if args.formal_run:
        validate_no_hidden_index_bits()
    write_completion(args.output, final_rows, args)
    validate_completion(args.output, final_rows, args, required=True)
    # The sidecar is part of completion.  Recheck Git after its durable publish.
    if granularity._git_identity() != (args.git_commit, args.git_dirty):
        raise RuntimeError("Git identity changed while publishing completion evidence")
    if args.formal_run:
        validate_no_hidden_index_bits()

    mismatches = sum(row.get("parity") == "mismatch" for row in final_rows)
    fatal_total = sum(row.get("status") in FATAL_STATUSES for row in final_rows)
    error_total = sum(row.get("status") == "error" for row in final_rows)
    worker_exit_total = sum(row.get("status") == "worker-exit" for row in final_rows)
    killed_signal_total = sum(row.get("status") == "killed-signal" for row in final_rows)
    cleanup_error_total = sum(row.get("status") == "cleanup-error" for row in final_rows)
    numerical_mismatch_total = sum(
        row.get("status") == "numerical-mismatch" for row in final_rows)
    resource_total = sum(row.get("status") in RESOURCE_STATUSES for row in final_rows)
    timeout_total = sum(row.get("status") == "timeout" for row in final_rows)
    oom_total = sum(row.get("status") == "oom" for row in final_rows)
    not_run_total = sum(row.get("status") == "not-run" for row in final_rows)
    exit_code = 1 if (fatal_total or mismatches
                      or (args.strict_mode and resource_total)) else 0
    if fatal_total or mismatches or (args.strict_mode and resource_total):
        status = "failed"
    elif resource_total:
        status = "resource-boundary"
    else:
        status = "ok"
    return {
        "schema": SCHEMA,
        "batch_id": args.batch_id,
        "frozen_inputs_sha256": args.frozen_inputs_sha256,
        "git_commit": args.git_commit,
        "git_dirty": args.git_dirty,
        "git_identity_verified_end": True,
        "backend_version": args.backend_version,
        "d4v2_sha256": args.d4v2_sha256,
        "d4_argv_sha256": args.d4_argv_sha256,
        "protocol": args.protocol,
        "run_config_sha256": args.run_config_sha256,
        "formal_run": args.formal_run,
        "strict_mode": args.strict_mode,
        "status": status,
        "exit_code": exit_code,
        "output": str(args.output.resolve()),
        "task_count": len(tasks),
        "attempted": attempted,
        "resumed_or_skipped": skipped,
        "failures_this_invocation": failures,
        "fatal_failures_total": fatal_total,
        "errors_total": error_total,
        "worker_exits_total": worker_exit_total,
        "killed_signals_total": killed_signal_total,
        "cleanup_errors_total": cleanup_error_total,
        "numerical_mismatches_total": numerical_mismatch_total,
        "resource_boundaries_total": resource_total,
        "timeouts_total": timeout_total,
        "oom_total": oom_total,
        "not_run_total": not_run_total,
        "parity_mismatches": mismatches,
        "checkpoint_rows": len(final_rows),
        "checkpoint_rows_all_batches": len(all_final_rows),
        "completion_sidecar": str(_completion_path(args.output)),
        "measured_ok": sum(row.get("phase") == "measured" and row.get("status") == "ok"
                           for row in final_rows),
    }


def run_experiment(args: argparse.Namespace) -> Dict[str, Any]:
    args.output = validate_output_destination(args.output)
    with invocation_lock(args.output):
        return _run_experiment_locked(args)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument(
        "--output", type=Path, default=HERE / "artifacts" / "controlled_mechanisms.csv",
        help="checkpoint outside the repository or under a Git-ignored directory")
    result.add_argument("--experiments", default="construction,numerical,treewidth",
                        help=("formal shard may select complete predefined groups: "
                              "construction,numerical,treewidth"))
    result.add_argument("--construction-shapes", default=",".join(FORMAL_CONSTRUCTION_SHAPES))
    result.add_argument("--construction-sizes", type=_parse_ints,
                        default=list(FORMAL_CONSTRUCTION_SIZES))
    result.add_argument("--numerical-depths", type=_parse_ints,
                        default=list(FORMAL_NUMERICAL_DEPTHS))
    result.add_argument("--bounded-depths", type=_parse_ints,
                        default=list(FORMAL_BOUNDED_DEPTHS))
    result.add_argument("--growing-widths", type=_parse_ints,
                        default=list(FORMAL_GROWING_WIDTHS))
    result.add_argument("--seed", type=int, default=DEFAULT_SEED)
    result.add_argument("--batch-id",
                        help="optional cross-check for frozen batch; required when unfrozen")
    result.add_argument("--frozen-inputs", type=Path,
                        help="required canonical freeze_inputs.py JSON for formal runs")
    result.add_argument("--expected-protocol", default=FORMAL_PROTOCOL,
                        help=("frozen protocol cross-check; formal value is fixed as "
                              + FORMAL_PROTOCOL))
    result.add_argument("--allow-unfrozen", action="store_true",
                        help="exploratory override permitting no frozen-input manifest")
    result.add_argument("--allow-dirty", action="store_true",
                        help="exploratory override; formal runs require a clean worktree")
    result.add_argument("--warmups", type=int, default=FORMAL_WARMUPS)
    result.add_argument("--runs", type=int, default=FORMAL_RUNS)
    result.add_argument("--timeout", type=float, default=FORMAL_TIMEOUT_S)
    result.add_argument("--formal-strict", action="store_true",
                        help="make timeout/OOM resource boundaries fail exploratory runs too")
    result.add_argument("--d4v2-bin", default=os.environ.get("D4V2_BIN", str(DEFAULT_D4V2)))
    result.add_argument("--expected-d4-sha256", default=PINNED_D4V2_SHA256)
    result.add_argument("--d4-tool-name", default=D4_TOOL_NAME,
                        help=("exploratory logical tool label; formal value is fixed as "
                              + D4_TOOL_NAME))
    result.add_argument(
        "--required-data", action="append", default=[], metavar="NAME",
        help="logical frozen data name consumed by this invocation (repeatable)")
    return result


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parser().parse_args(argv)
    args.experiments = tuple(item.strip() for item in args.experiments.split(",") if item.strip())
    args.construction_shapes = tuple(
        item.strip() for item in args.construction_shapes.split(",") if item.strip())
    unknown = set(args.experiments) - set(EXPERIMENTS)
    if unknown:
        raise SystemExit("unknown experiments: %s" % sorted(unknown))
    if not args.experiments:
        raise SystemExit("at least one experiment group is required")
    unknown_shapes = set(args.construction_shapes) - set(CONSTRUCTION_SHAPES)
    if unknown_shapes:
        raise SystemExit("unknown construction shapes: %s" % sorted(unknown_shapes))
    args.experiments = tuple(item for item in EXPERIMENTS if item in args.experiments)
    args.construction_shapes = tuple(
        item for item in CONSTRUCTION_SHAPES if item in args.construction_shapes)
    if "construction" in args.experiments and not args.construction_shapes:
        raise SystemExit("construction requires at least one shape")
    for field in ("construction_sizes", "numerical_depths", "bounded_depths",
                  "growing_widths"):
        values = getattr(args, field)
        if len(values) != len(set(values)):
            raise SystemExit("%s contains duplicate values" % field.replace("_", "-"))
        setattr(args, field, tuple(sorted(values)))
    if args.warmups < 0 or args.runs < 1 or args.timeout <= 0:
        raise SystemExit("warmups must be non-negative; runs and timeout must be positive")
    if ("construction" in args.experiments and "cycle" in args.construction_shapes
            and any(size < 2 for size in args.construction_sizes)):
        raise SystemExit("cycle construction sizes must be at least two")
    if re.fullmatch(r"[0-9a-f]{64}", args.expected_d4_sha256) is None:
        raise SystemExit("--expected-d4-sha256 must be lowercase 64-hex")
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:+-]{0,95}", args.d4_tool_name) is None:
        raise SystemExit("--d4-tool-name is invalid")
    if (len(args.required_data) != len(set(args.required_data))
            or any(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:+-]{0,95}", name) is None
                   for name in args.required_data)):
        raise SystemExit("--required-data names must be unique valid logical names")
    args.required_data = tuple(sorted(args.required_data))
    if (args.expected_protocol is not None
            and (not args.expected_protocol
                 or args.expected_protocol.strip() != args.expected_protocol
                 or len(args.expected_protocol) > 128
                 or any(ord(char) < 32 for char in args.expected_protocol))):
        raise SystemExit("--expected-protocol is invalid")
    try:
        summary = run_experiment(args)
    except (ValueError, RuntimeError) as exc:
        print("controlled_mechanisms: ERROR: %s" % exc, file=sys.stderr)
        return 2
    print(json.dumps(summary, sort_keys=True))
    return int(summary["exit_code"])


if __name__ == "__main__":
    sys.exit(main())
