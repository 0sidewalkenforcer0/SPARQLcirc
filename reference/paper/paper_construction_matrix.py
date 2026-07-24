"""R9.2: B / R / N_clean / C construction-timing decomposition.

Each CSV row is one *cell*: one rewrite, ``PCM_WARMUPS`` warm-ups, and
``PCM_RUNS`` measured executions.  ``timeout_s`` is a hard wall-clock budget
for that whole cell.  It starts before rewrite and is never reset between
requests or between the steps of a C plan.  A killable worker owns the complete
cell, so a response which keeps producing bytes forever cannot evade the
deadline through socket activity.

Timing boundaries:

* ``rewrite_ms`` is query/plan generation and is diagnostic (not engine time).
* an engine/network sample starts immediately before POST and ends after the
  final response byte has been drained, before body assembly/decoding;
* a factored C sample is the sum of every CONSTRUCT, private-message INSERT,
  and private-workspace cleanup DELETE POST interval in that execution;
* ``c_parse_median_ms`` includes byte assembly, UTF-8 decode, ``splitlines``,
  triple deduplication, circuit parsing, and binding recovery.  Its interval is
  disjoint from network time;
* ``c_protocol_median_ms`` covers local row-atomic INSERT/DELETE body creation;
* ``construct_total_ms`` is an outer wall interval around one complete
  ``_execute_c_once`` call. ``construct_unattributed_ms`` retains the small,
  non-overlapping remainder beyond network + parse + explicit protocol work
  (request encoding/header setup, loop/control, and return assembly).

The timing response format remains CSV for B/R/N and N-Triples for C.  B/R rows
also carry a normalized CSV-multiset fingerprint.  The formal term-aware
correctness gate is ``verify_brnc_parity.py``: it uses SPARQL Results JSON for
B/R/N and structured ``c:binding`` values for C.
"""

import argparse
import collections
import contextlib
import csv
import fcntl
import hashlib
import io
import json
import math
import multiprocessing
import os
import re
import signal
import shutil
import socket
import stat as statmod
import statistics
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request as U
from collections.abc import Sequence
from pathlib import Path

sys.setrecursionlimit(1_000_000)

HERE = os.path.dirname(os.path.abspath(__file__))
REF = os.path.dirname(HERE)
ROOT = os.path.dirname(REF)
sys.path.insert(0, REF)
import circuit_io
import circuit_cache
import freeze_inputs

JAR = os.path.join(REF, "..", "engine", "target", "npcs-rewrite.jar")
JAVA = os.environ.get("PCM_JAVA_BIN") or shutil.which("java") or "java"

# Publication parameters are protocol constants, never ambient environment
# configuration.  The command-line parser exposes separate exploratory-only
# overrides below.
FORMAL_WARMUPS = 1
FORMAL_RUNS = 5
FORMAL_TIMEOUT = 500.0
FORMAL_UPDATE_CHUNK_TRIPLES = 1000
FORMAL_ORPHAN_CLEANUP_TIMEOUT = float(os.environ.get("PCM_ORPHAN_CLEANUP_TIMEOUT", "15"))
FORMAL_CLASSES = ("L", "S", "F", "C", "O", "M")
FORMAL_METHODS = ("B", "R", "N", "C")
TIMEOUT = FORMAL_TIMEOUT
WARMUPS = FORMAL_WARMUPS
RUNS = FORMAL_RUNS
RS = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
COMMIT = subprocess.run(
    ["git", "-C", ROOT, "rev-parse", "HEAD"],
    capture_output=True,
    text=True,
).stdout.strip() or "?"

PROTOCOL = "r9.2-frozen-identity-v9"
NOTE_PREFIX = "pcm-meta-v2:"
PROVENANCE_VAR = "finalprovennacevariable"  # legacy name; capture-safe builds may rename it
GENERATED_PROVENANCE_RE = re.compile(
    r"^__npcs\d+_finalprovennacevariable$"
)
BATCH_ID_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
FROZEN_TOOL_NAME = "npcs-rewrite.jar"
FROZEN_JAVA_RUNTIME_NAME = "java-runtime"
DEFAULT_ARTIFACT_ROOT = os.path.join(ROOT, "artifacts", "r9")
# Formal endpoint serialization must have one process-independent namespace.
# In particular, an ambient variable must not let two invocations select
# disjoint lock directories for the same writable store.
ENDPOINT_LOCK_DIRECTORY = "/tmp/sparqlcirc-r9-endpoint-locks-v7"

IDENTITY_FIELDS = (
    "commit",
    "batch_id",
    "protocol",
    "query_sha256",
    "engine",
    "engine_version",
    "scale",
    "base_endpoint_sha256",
    "reified_endpoint_sha256",
    "update_endpoint_sha256",
    "base_data_identity_sha256",
    "reified_data_identity_sha256",
    "update_for",
    "access_mode",
    "base_data_name",
    "reified_data_name",
    "update_canary_sha256",
    "store_instance_sha256",
    "store_discriminator_sha256",
    "tool_sha256",
    "java_runtime_sha256",
    "run_identity_sha256",
)

_CANONICAL_UINT_RE = re.compile(r"^(?:0|[1-9][0-9]*)$")

_FORMAL_TOOL_SNAPSHOTS = None
_NO_PROXY_HANDLER = U.ProxyHandler({})
_NO_PROXY_OPENER = U.build_opener(_NO_PROXY_HANDLER)

# Every engine uses two independent stores per scale: one base and one reified.
# Defaults follow reference/engines/engines.json's localhost profile and assign
# adjacent ports to the paired/100M instances.  Real deployments can override
# any cell with PCM_<ENGINE>_<SCALE>_{BASE,REIFIED,UPDATE}_ENDPOINT.
GDB = "http://localhost:7200/repositories"
_DEFAULT_ENDPOINTS = {
    "graphdb": {
        "10M": {
            "base": f"{GDB}/watdivbase",
            "reified": f"{GDB}/watdiv",
            "update": f"{GDB}/watdiv/statements",
        },
        "100M": {
            "base": f"{GDB}/watdiv100mbase",
            "reified": f"{GDB}/watdiv100m",
            "update": f"{GDB}/watdiv100m/statements",
        },
    },
    "oxigraph": {
        "10M": {
            "base": "http://localhost:7879/query",
            "reified": "http://localhost:7878/query",
            "update": "http://localhost:7878/update",
        },
        "100M": {
            "base": "http://localhost:7881/query",
            "reified": "http://localhost:7880/query",
            "update": "http://localhost:7880/update",
        },
    },
    "qlever": {
        "10M": {"base": "http://localhost:7002", "reified": "http://localhost:7001", "update": None},
        "100M": {"base": "http://localhost:7004", "reified": "http://localhost:7003", "update": None},
    },
    "millenniumdb": {
        "10M": {"base": "http://localhost:1235/sparql", "reified": "http://localhost:1234/sparql", "update": None},
        "100M": {"base": "http://localhost:1237/sparql", "reified": "http://localhost:1236/sparql", "update": None},
    },
}


def build_engine_registry(environ=None):
    environ = os.environ if environ is None else environ
    profile_path = os.path.join(REF, "engines", "engines.json")
    try:
        with open(profile_path) as fh:
            profiles = json.load(fh)
    except (OSError, ValueError):
        profiles = {}
    display = {
        "graphdb": "GraphDB (RDF4J)",
        "oxigraph": "Oxigraph",
        "qlever": "QLever",
        "millenniumdb": "MillenniumDB",
    }
    registry = {}
    for engine, scales in _DEFAULT_ENDPOINTS.items():
        profile = profiles.get(engine, {})
        config = {
            "version": environ.get(
                f"PCM_{engine.upper()}_ENGINE_VERSION",
                display[engine] + " [engines.json profile]",
            ),
            "profile": profile,
            "read_only": bool(profile.get("readonly", engine in ("qlever", "millenniumdb"))),
        }
        for scale, roles in scales.items():
            config[scale] = {}
            for role, default in roles.items():
                env_name = f"PCM_{engine.upper()}_{scale}_{role.upper()}_ENDPOINT"
                value = environ.get(env_name, default)
                config[scale][role] = value or None
            prefix = f"PCM_{engine.upper()}_{scale}"
            config[scale]["base_data_identity"] = environ.get(
                prefix + "_BASE_DATA_IDENTITY", f"watdiv:{scale}:base:v1"
            )
            config[scale]["reified_data_identity"] = environ.get(
                prefix + "_REIFIED_DATA_IDENTITY",
                f"watdiv:{scale}:standard-reified:v1",
            )
            config[scale]["update_for"] = environ.get(
                prefix + "_UPDATE_FOR",
                "reified" if config[scale].get("update") else "none",
            )
        registry[engine] = config
    return registry


ENGINES = build_engine_registry()


def _git(args):
    return subprocess.run(
        ["git", "-C", ROOT, *args],
        capture_output=True,
        text=True,
        timeout=30,
    )


def validate_no_hidden_index_bits():
    """Reject tracked paths hidden from porcelain by index flags."""
    try:
        result = subprocess.run(
            ["git", "-C", ROOT, "ls-files", "-v", "-z"],
            capture_output=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as ex:
        raise RuntimeError("could not audit tracked Git index flags") from ex
    if result.returncode != 0:
        raise RuntimeError("Git hidden-index-bit probe failed")
    hidden = []
    for record in result.stdout.split(b"\0"):
        if not record:
            continue
        if len(record) < 3 or record[1:2] != b" ":
            raise RuntimeError("git ls-files -v emitted an unparseable record")
        marker = record[:1]
        # -v lower-cases the normal tag for assume-unchanged; S is the
        # skip-worktree tag.  Keep paths opaque so arbitrary filenames are safe.
        if marker == b"S" or marker.islower():
            hidden.append(record[2:])
    if hidden:
        raise RuntimeError(
            "formal R9 rejects %d tracked file(s) hidden by "
            "assume-unchanged/skip-worktree index flags" % len(hidden)
        )


def clean_git_identity():
    """Return one full, clean HEAD observed as HEAD -> status -> HEAD."""
    before_result = _git(["rev-parse", "HEAD"])
    if before_result.returncode != 0:
        raise RuntimeError("Git HEAD probe failed")
    before = before_result.stdout.strip()
    if not COMMIT_RE.fullmatch(before):
        raise RuntimeError("Git HEAD is not a full lowercase 40-hex commit")
    status = _git(
        [
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
            "--ignore-submodules=none",
        ]
    )
    if status.returncode != 0:
        raise RuntimeError("Git clean-state probe failed")
    dirty = [line for line in status.stdout.splitlines() if line]
    if dirty:
        raise RuntimeError(f"formal R9 requires a clean Git worktree ({len(dirty)} entries)")
    validate_no_hidden_index_bits()
    after_result = _git(["rev-parse", "HEAD"])
    after = after_result.stdout.strip() if after_result.returncode == 0 else ""
    if after != before:
        raise RuntimeError("Git HEAD changed during clean-state validation")
    return before


def verify_git_end(expected_commit):
    """Repeat the clean HEAD sandwich and require invocation-wide stability."""
    observed = clean_git_identity()
    if observed != expected_commit:
        raise RuntimeError("Git HEAD changed during the R9 invocation")
    return observed


def _stat_signature(value):
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_nlink,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _validate_opened_single_link(path, descriptor, label):
    opened = os.fstat(descriptor)
    try:
        current = os.lstat(os.fspath(path))
    except OSError as ex:
        raise ValueError(f"{label} path is unstable") from ex
    if (
        not statmod.S_ISREG(opened.st_mode)
        or not statmod.S_ISREG(current.st_mode)
        or opened.st_nlink != 1
        or current.st_nlink != 1
        or (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino)
    ):
        raise ValueError(f"{label} must be one stable single-link regular file")
    return opened


def _open_single_link(path, flags, label, mode=0o600):
    descriptor = None
    try:
        descriptor = os.open(
            os.fspath(path),
            flags
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
            mode,
        )
        _validate_opened_single_link(path, descriptor, label)
        return descriptor
    except (OSError, ValueError) as ex:
        if descriptor is not None:
            os.close(descriptor)
        if isinstance(ex, ValueError):
            raise
        raise ValueError(f"{label} is missing, aliased, or unsafe") from ex


def _read_open_descriptor(
    path, descriptor, label, *, allow_empty=True, limit=None
):
    """Read and revalidate ``path`` through an already verified descriptor.

    Mutating callers use this helper so validation and repair happen on the
    same inode and file descriptor.  This closes the reopen race where a path
    could be replaced after a snapshot was checked but before it was truncated.
    """
    before = _validate_opened_single_link(path, descriptor, label)
    if not allow_empty and before.st_size == 0:
        raise ValueError(f"{label} is empty")
    if limit is not None and before.st_size > limit:
        raise ValueError(f"{label} exceeds its safety cap")
    os.lseek(descriptor, 0, os.SEEK_SET)
    chunks = []
    remaining = None if limit is None else limit + 1
    while True:
        amount = 1024 * 1024 if remaining is None else min(1024 * 1024, remaining)
        if amount <= 0:
            break
        block = os.read(descriptor, amount)
        if not block:
            break
        chunks.append(block)
        if remaining is not None:
            remaining -= len(block)
    payload = b"".join(chunks)
    if limit is not None and len(payload) > limit:
        raise ValueError(f"{label} exceeds its safety cap")
    after = _validate_opened_single_link(path, descriptor, label)
    if _stat_signature(before) != _stat_signature(after):
        raise ValueError(f"{label} changed while being read")
    return payload


def _read_stable_bytes(path, label, *, allow_empty=True, limit=None):
    descriptor = _open_single_link(path, os.O_RDONLY, label)
    try:
        return _read_open_descriptor(
            path,
            descriptor,
            label,
            allow_empty=allow_empty,
            limit=limit,
        )
    finally:
        os.close(descriptor)


def _snapshot_tool(path, label):
    try:
        resolved = Path(path).expanduser().resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as ex:
        raise freeze_inputs.FreezeError(f"{label} is missing") from ex
    descriptor = None
    try:
        descriptor = _open_single_link(resolved, os.O_RDONLY, label)
        payload = _read_open_descriptor(
            resolved, descriptor, label, allow_empty=False
        )
        observed = _validate_opened_single_link(
            resolved, descriptor, label
        )
    except ValueError as ex:
        raise freeze_inputs.FreezeError(str(ex)) from ex
    finally:
        if descriptor is not None:
            os.close(descriptor)
    return {
        "path": str(resolved),
        "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "signature": _stat_signature(observed),
        "label": label,
    }


def _verify_tool_snapshot(snapshot):
    try:
        descriptor = _open_single_link(
            snapshot["path"], os.O_RDONLY, snapshot["label"]
        )
        try:
            current = _validate_opened_single_link(
                snapshot["path"], descriptor, snapshot["label"]
            )
        finally:
            os.close(descriptor)
    except ValueError as ex:
        raise RuntimeError(str(ex)) from ex
    if tuple(snapshot["signature"]) != _stat_signature(current):
        raise RuntimeError(f"{snapshot['label']} changed during the R9 invocation")


def _fsync_directory(path):
    descriptor = os.open(os.fspath(path), os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _prepare_artifact_path(path, *, exploratory=False, directory=False):
    """Create the parent and reject formal outputs not covered by .gitignore."""
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = Path(ROOT) / candidate
    parent = candidate if directory else candidate.parent
    parent.mkdir(parents=True, exist_ok=True)
    resolved_parent = parent.resolve(strict=True)
    resolved = resolved_parent if directory else resolved_parent / candidate.name
    if not directory and os.path.lexists(resolved):
        info = os.lstat(resolved)
        if (
            os.path.islink(resolved)
            or not statmod.S_ISREG(info.st_mode)
            or info.st_nlink != 1
        ):
            raise ValueError("R9 output must be one non-symlink regular file")
    if not exploratory:
        try:
            resolved.relative_to(Path(ROOT).resolve(strict=True))
        except ValueError:
            pass  # external scratch cannot dirty the repository
        else:
            probe = resolved / ".pcm-ignore-probe" if directory else resolved
            ignored = _git(["check-ignore", "-q", "--no-index", "--", str(probe)])
            if ignored.returncode != 0:
                raise ValueError(
                    f"formal R9 refuses an unignored repository output path: {resolved}"
                )
    return str(resolved)


@contextlib.contextmanager
def invocation_file_lock(output_path, timeout=None):
    """Serialize one complete load/run/append-or-merge invocation per output."""
    timeout = float(
        timeout
        if timeout is not None
        else os.environ.get("PCM_INVOCATION_LOCK_TIMEOUT_S", "300")
    )
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("PCM_INVOCATION_LOCK_TIMEOUT_S must be positive")
    output_path = os.path.abspath(output_path)
    lock_path = output_path + ".invocation.lock"
    existed = os.path.lexists(lock_path)
    descriptor = _open_single_link(
        lock_path,
        os.O_RDWR | os.O_CREAT,
        "R9 invocation lock",
    )
    handle = os.fdopen(descriptor, "r+", encoding="utf-8", closefd=True)
    if not existed:
        _fsync_directory(os.path.dirname(lock_path) or ".")
    started = time.monotonic()
    try:
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() - started >= timeout:
                    raise EndpointLockTimeout(
                        f"invocation lock waited {timeout:g}s for {os.path.basename(output_path)}"
                    )
                time.sleep(0.05)
        _validate_opened_single_link(lock_path, handle.fileno(), "R9 invocation lock")
        handle.seek(0)
        handle.truncate()
        handle.write(
            json.dumps(
                {
                    "pid": os.getpid(),
                    "acquired_unix": time.time(),
                    "output_sha256": identity_sha256(output_path),
                },
                sort_keys=True,
            )
            + "\n"
        )
        handle.flush()
        os.fsync(handle.fileno())
        _fsync_directory(os.path.dirname(lock_path) or ".")
        yield {
            "wait_ms": round((time.monotonic() - started) * 1000.0, 3),
            "lock_path": lock_path,
        }
    finally:
        try:
            _validate_opened_single_link(
                lock_path, handle.fileno(), "R9 invocation lock"
            )
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


def identity_sha256(value):
    """Hash endpoint/data identities so checkpoints never expose credentials."""
    if value in (None, ""):
        return ""
    return hashlib.sha256(str(value).strip().encode("utf-8")).hexdigest()


def validate_endpoint_registration(config, endpoints, require_update=False):
    """Fail closed on aliases and unregistered update/data relationships."""
    base, reified = endpoints.get("base"), endpoints.get("reified")
    if not base or not reified:
        raise ValueError("both base and reified query endpoints must be registered")
    try:
        base_safe = freeze_inputs.endpoint_identity(base)[0]
        reified_safe = freeze_inputs.endpoint_identity(reified)[0]
        # Treat an optional trailing slash as an alias for store-separation
        # purposes while retaining the exact frozen endpoint identity elsewhere.
        base_identity = (base_safe["origin_sha256"], base_safe["path_sha256"])
        reified_identity = (
            reified_safe["origin_sha256"],
            reified_safe["path_sha256"],
        )
    except freeze_inputs.FreezeError:
        base_identity = identity_sha256(base.rstrip("/"))
        reified_identity = identity_sha256(reified.rstrip("/"))
    if base_identity == reified_identity:
        raise ValueError("base and reified endpoints must be distinct stores")
    for name in ("base_data_identity", "reified_data_identity"):
        frozen_digest = endpoints.get(name + "_sha256")
        if not endpoints.get(name) and not BATCH_ID_RE.fullmatch(
            str(frozen_digest or "")
        ):
            raise ValueError(f"missing registered {name}")
    access_mode = endpoints.get("access_mode")
    if access_mode not in (None, "writable", "read-only"):
        raise ValueError("store access_mode must be writable or read-only")
    if access_mode == "read-only" and endpoints.get("update"):
        raise ValueError("read-only store must not register an update endpoint")
    if require_update:
        if access_mode == "read-only":
            raise ValueError("read-only store cannot run writable factored C")
        if not endpoints.get("update"):
            raise ValueError("writable factored C requires an update endpoint")
        if endpoints.get("update_for") not in (
            "reified",
            "canonical-same-as-reified",
            "strict-reified-statements-child",
            "strict-query-update-sibling",
        ):
            raise ValueError(
                "factored C update endpoint must be explicitly registered for reified"
            )


def require_batch_id(value=None):
    value = value if value is not None else os.environ.get("PCM_BATCH_ID", "")
    if not BATCH_ID_RE.fullmatch(str(value)):
        raise ValueError("formal R9 requires PCM_BATCH_ID as lowercase 64-hex")
    return str(value)


def cell_identity(
    engine, scale, query_sha256, config, endpoints, batch_id=None
):
    """Frozen, credential-safe identity bound into every timing/parity row."""
    payload = {
        "commit": COMMIT,
        "batch_id": require_batch_id(batch_id),
        "protocol": PROTOCOL,
        "query_sha256": query_sha256,
        "engine": engine,
        "engine_version": endpoints.get("engine_version")
        or config.get("version", ""),
        "scale": scale,
        "base_endpoint_sha256": endpoints.get("base_endpoint_sha256")
        or identity_sha256(endpoints.get("base")),
        "reified_endpoint_sha256": endpoints.get("reified_endpoint_sha256")
        or identity_sha256(endpoints.get("reified")),
        "update_endpoint_sha256": endpoints.get("update_endpoint_sha256")
        or identity_sha256(endpoints.get("update") or "registered:no-update"),
        "base_data_identity_sha256": endpoints.get("base_data_identity_sha256")
        or identity_sha256(endpoints.get("base_data_identity")),
        "reified_data_identity_sha256": endpoints.get(
            "reified_data_identity_sha256"
        )
        or identity_sha256(endpoints.get("reified_data_identity")),
        "update_for": endpoints.get("update_for", ""),
        "access_mode": endpoints.get("access_mode", ""),
        "base_data_name": endpoints.get("base_data_name", ""),
        "reified_data_name": endpoints.get("reified_data_name", ""),
        "update_canary_sha256": endpoints.get("update_canary_sha256", ""),
        "store_instance_sha256": endpoints.get("store_instance_sha256", ""),
        "store_discriminator_sha256": endpoints.get(
            "store_discriminator_sha256", ""
        ),
        "tool_sha256": config.get("tool_sha256", ""),
        "java_runtime_sha256": config.get("java_runtime_sha256", ""),
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    payload["run_identity_sha256"] = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return payload


def _canonical_digest(value):
    return hashlib.sha256(_canonical_json_bytes(value, newline=False)).hexdigest()


def _canonical_json_bytes(value, *, newline=True):
    """Return the one accepted JSON encoding for sealed protocol documents."""
    rendered = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return (rendered + ("\n" if newline else "")).encode("utf-8")


def _canonical_uint(value):
    """Parse a CSV unsigned integer only when its spelling is canonical."""
    if type(value) is not str or not _CANONICAL_UINT_RE.fullmatch(value):
        return None
    parsed = int(value)
    return parsed if str(parsed) == value else None


def _canonical_float(value):
    """Parse a finite CSV float only when it matches Python's emitted spelling."""
    if type(value) is not str:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed) or str(parsed) != value:
        return None
    return parsed


def _identity_from_row(row):
    return {name: row.get(name, "") for name in IDENTITY_FIELDS}


def _ordered_identity_records(expected_identities):
    """Canonical, credential-safe representation of a deterministic slot plan."""
    return [
        {"key": list(key), "identity": dict(identity)}
        for key, identity in expected_identities.items()
    ]


def _sentinel_identity(document, engine, scale, role):
    records = sorted(
        (
            item
            for item in document["identity"]["store_sentinels"]
            if (item["engine"], item["scale"], item["role"])
            == (engine, scale, role)
        ),
        key=lambda item: (item["kind"], item["query_sha256"]),
    )
    if not records:
        raise freeze_inputs.FreezeError(
            f"frozen store {engine}/{scale} has no {role} sentinel evidence"
        )
    return _canonical_digest(
        [
            {
                key: item[key]
                for key in (
                    "kind",
                    "query_sha256",
                    "expected_fingerprint",
                    "observed_fingerprint",
                )
            }
            for item in records
        ]
    )


def bind_frozen_registry(
    document, engines, scales, methods, registry=None, tool_snapshots=None
):
    """Match raw runtime URLs to frozen canonical identities and reject defaults."""
    registry = ENGINES if registry is None else registry
    bound = {}
    tool = freeze_inputs.frozen_tool(document, FROZEN_TOOL_NAME)
    observed_tool = (
        {
            "bytes": tool_snapshots[FROZEN_TOOL_NAME]["bytes"],
            "sha256": tool_snapshots[FROZEN_TOOL_NAME]["sha256"],
        }
        if tool_snapshots is not None
        else freeze_inputs.hash_file(JAR, label=FROZEN_TOOL_NAME)
    )
    if observed_tool != {"bytes": tool["bytes"], "sha256": tool["sha256"]}:
        raise freeze_inputs.FreezeError(
            "current CircuitRun jar differs from the frozen tool binary"
        )
    for engine in engines:
        source = registry.get(engine)
        if source is None:
            raise freeze_inputs.FreezeError(f"unregistered runtime engine: {engine}")
        config = {
            key: value
            for key, value in source.items()
            if key not in _DEFAULT_ENDPOINTS.get(engine, {}) and key != "version"
        }
        config["tool_sha256"] = tool["sha256"]
        if tool_snapshots is not None:
            config["java_runtime_sha256"] = tool_snapshots[
                FROZEN_JAVA_RUNTIME_NAME
            ]["sha256"]
        for scale in scales:
            runtime = source.get(scale)
            if runtime is None:
                raise freeze_inputs.FreezeError(
                    f"runtime endpoints are not registered for {engine}/{scale}"
                )
            store = freeze_inputs.frozen_store(document, engine, scale)
            endpoints = dict(runtime)
            endpoints.pop("base_data_identity", None)
            endpoints.pop("reified_data_identity", None)
            endpoints["engine_version"] = store["engine_version"]
            endpoints["access_mode"] = store["access_mode"]
            endpoints["read_only"] = store["access_mode"] == "read-only"
            endpoints["base_data_name"] = store["base_data_name"]
            endpoints["reified_data_name"] = store["reified_data_name"]
            for role in ("base", "reified", "update"):
                raw, expected = endpoints.get(role), store["endpoints"][role]
                if (raw is None) != (expected is None):
                    raise freeze_inputs.FreezeError(
                        f"runtime {engine}/{scale}/{role} presence differs from frozen store"
                    )
                if raw is not None:
                    observed, _canonical = freeze_inputs.endpoint_identity(raw)
                    if observed != expected:
                        raise freeze_inputs.FreezeError(
                            f"runtime {engine}/{scale}/{role} endpoint differs from frozen identity"
                        )
                    endpoints[role + "_endpoint_sha256"] = expected[
                        "endpoint_sha256"
                    ]
                else:
                    endpoints[role + "_endpoint_sha256"] = identity_sha256(
                        "registered:no-update"
                    )
            endpoints["update_for"] = store["update_binding"]
            base_data = freeze_inputs.frozen_data(
                document, store["base_data_name"]
            )
            reified_data = freeze_inputs.frozen_data(
                document, store["reified_data_name"]
            )
            base_sentinel = _sentinel_identity(document, engine, scale, "base")
            reified_sentinel = _sentinel_identity(
                document, engine, scale, "reified"
            )
            endpoints["base_data_identity_sha256"] = _canonical_digest(
                {"data": base_data, "sentinel_sha256": base_sentinel}
            )
            endpoints["reified_data_identity_sha256"] = _canonical_digest(
                {"data": reified_data, "sentinel_sha256": reified_sentinel}
            )
            if (
                endpoints["base_data_identity_sha256"]
                == endpoints["reified_data_identity_sha256"]
            ):
                raise freeze_inputs.FreezeError(
                    f"frozen store {engine}/{scale} lacks base/reified discrimination"
                )
            discriminators = sorted(
                (
                    item
                    for item in document["identity"]["store_sentinels"]
                    if (item["engine"], item["scale"]) == (engine, scale)
                ),
                key=lambda item: (item["role"], item["query_sha256"]),
            )
            endpoints["store_discriminator_sha256"] = _canonical_digest(
                discriminators
            )
            endpoints["update_canary_sha256"] = _canonical_digest(
                store["update_canary"]
                if store["update_canary"] is not None
                else {"access_mode": "read-only", "update_canary": None}
            )
            endpoints["store_instance_sha256"] = _canonical_digest(
                {
                    "reified_endpoint_sha256": endpoints[
                        "reified_endpoint_sha256"
                    ],
                    "update_endpoint_sha256": endpoints[
                        "update_endpoint_sha256"
                    ],
                }
            )
            config[scale] = endpoints
            if config.get("version") not in (None, store["engine_version"]):
                raise freeze_inputs.FreezeError(
                    f"frozen engine versions disagree across scales for {engine}"
                )
            config["version"] = store["engine_version"]
            validate_endpoint_registration(
                config,
                endpoints,
                require_update=("C" in methods and not endpoints["read_only"]),
            )
        bound[engine] = config
    return bound


def load_formal_context(engines, scales, methods, environ=None):
    environ = os.environ if environ is None else environ
    frozen_path = environ.get("PCM_FROZEN_INPUTS", "")
    if not frozen_path:
        raise freeze_inputs.FreezeError("formal R9 requires PCM_FROZEN_INPUTS")
    if not COMMIT_RE.fullmatch(COMMIT):
        raise freeze_inputs.FreezeError("formal R9 requires a full 40-hex Git HEAD")
    expected_batch = require_batch_id(environ.get("PCM_BATCH_ID"))
    java_candidate = environ.get("PCM_JAVA_BIN", "")
    if not java_candidate or not os.path.isabs(java_candidate):
        raise freeze_inputs.FreezeError(
            "formal R9 requires PCM_JAVA_BIN as an explicit absolute runtime path"
        )
    tool_snapshots = {
        FROZEN_TOOL_NAME: _snapshot_tool(JAR, FROZEN_TOOL_NAME),
        FROZEN_JAVA_RUNTIME_NAME: _snapshot_tool(
            java_candidate, FROZEN_JAVA_RUNTIME_NAME
        ),
    }
    required_stores = [(engine, scale) for engine in engines for scale in scales]
    document = freeze_inputs.load_frozen_batch(
        frozen_path,
        expected_commit=COMMIT,
        expected_protocol=PROTOCOL,
        required_tools=(FROZEN_TOOL_NAME, FROZEN_JAVA_RUNTIME_NAME),
        required_stores=required_stores,
        require_formal=True,
    )
    if document["batch_id"] != expected_batch:
        raise freeze_inputs.FreezeError(
            "PCM_BATCH_ID differs from the canonical frozen batch_id"
        )
    observed_path = freeze_inputs.validate_manifest(
        os.path.join(HERE, "path_manifest.csv"), "path", REF
    )
    if observed_path != _frozen_manifest(document, "path"):
        raise freeze_inputs.FreezeError(
            "current path manifest/query bytes differ from the frozen batch"
        )
    for name, snapshot in tool_snapshots.items():
        frozen = freeze_inputs.frozen_tool(document, name)
        if {"bytes": snapshot["bytes"], "sha256": snapshot["sha256"]} != {
            "bytes": frozen["bytes"],
            "sha256": frozen["sha256"],
        }:
            raise freeze_inputs.FreezeError(
                f"current {name} differs from the frozen tool binary"
            )
    registry = bind_frozen_registry(
        document,
        engines,
        scales,
        methods,
        tool_snapshots=tool_snapshots,
    )
    return {
        "document": document,
        "batch_id": expected_batch,
        "registry": registry,
        "frozen_path": os.path.abspath(frozen_path),
        "tool_snapshots": tool_snapshots,
    }


def bind_exploratory_registry(engines, scales, batch_id, registry=None):
    """Make explicit non-formal identities for smoke tests only."""
    registry = ENGINES if registry is None else registry
    tool = freeze_inputs.hash_file(JAR, label=FROZEN_TOOL_NAME)
    try:
        java = _snapshot_tool(JAVA, FROZEN_JAVA_RUNTIME_NAME)
    except freeze_inputs.FreezeError:
        java = {"sha256": identity_sha256(JAVA)}
    bound = {}
    for engine in engines:
        source = registry.get(engine)
        if source is None:
            continue
        config = {
            key: value
            for key, value in source.items()
            if key not in _DEFAULT_ENDPOINTS.get(engine, {})
        }
        config["tool_sha256"] = tool["sha256"]
        config["java_runtime_sha256"] = java["sha256"]
        for scale in scales:
            runtime = source.get(scale)
            if runtime is None:
                continue
            endpoints = dict(runtime)
            endpoints["access_mode"] = (
                "read-only" if config.get("read_only", False) else "writable"
            )
            endpoints["read_only"] = endpoints["access_mode"] == "read-only"
            endpoints["base_data_name"] = "exploratory-base"
            endpoints["reified_data_name"] = "exploratory-reified"
            for role in ("base", "reified", "update"):
                raw = endpoints.get(role)
                if raw:
                    try:
                        digest = freeze_inputs.endpoint_identity(raw)[0][
                            "endpoint_sha256"
                        ]
                    except freeze_inputs.FreezeError:
                        digest = identity_sha256(raw)
                else:
                    digest = identity_sha256("registered:no-update")
                endpoints[role + "_endpoint_sha256"] = digest
            endpoints["base_data_identity_sha256"] = identity_sha256(
                endpoints.get("base_data_identity")
            )
            endpoints["reified_data_identity_sha256"] = identity_sha256(
                endpoints.get("reified_data_identity")
            )
            endpoints["store_discriminator_sha256"] = _canonical_digest(
                {
                    "base": endpoints["base_data_identity_sha256"],
                    "reified": endpoints["reified_data_identity_sha256"],
                }
            )
            endpoints["update_canary_sha256"] = _canonical_digest(
                {
                    "exploratory": True,
                    "access_mode": endpoints["access_mode"],
                }
            )
            endpoints["store_instance_sha256"] = _canonical_digest(
                {
                    "reified_endpoint_sha256": endpoints[
                        "reified_endpoint_sha256"
                    ],
                    "update_endpoint_sha256": endpoints[
                        "update_endpoint_sha256"
                    ],
                }
            )
            config[scale] = endpoints
        bound[engine] = config
    return bound


@contextlib.contextmanager
def endpoint_lock(endpoint, timeout=None):
    """Cross-process lock covering a writable endpoint's complete C protocol."""
    timeout = float(
        timeout
        if timeout is not None
        else os.environ.get("PCM_ENDPOINT_LOCK_TIMEOUT_S", "300")
    )
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("PCM_ENDPOINT_LOCK_TIMEOUT_S must be positive")
    # This path is deliberately not configurable through the environment: a
    # per-process override would let two formal invocations lock distinct
    # files while mutating the same store.  Tests may patch the module constant
    # before forking, but production identity is fixed at import time.
    directory = Path(ENDPOINT_LOCK_DIRECTORY)
    if not directory.is_absolute():
        raise ValueError("R9 endpoint lock directory must be absolute")
    directory.mkdir(parents=True, exist_ok=True, mode=0o777)
    info = os.lstat(directory)
    if not statmod.S_ISDIR(info.st_mode) or statmod.S_ISLNK(info.st_mode):
        raise ValueError("R9 endpoint lock directory must be a real directory")
    if BATCH_ID_RE.fullmatch(str(endpoint or "")):
        endpoint_hash = str(endpoint)
    else:
        try:
            endpoint_hash = freeze_inputs.endpoint_identity(str(endpoint))[0][
                "endpoint_sha256"
            ]
        except freeze_inputs.FreezeError:
            endpoint_hash = identity_sha256(endpoint)
    path = directory / (endpoint_hash + ".lock")
    existed = os.path.lexists(path)
    descriptor = _open_single_link(
        path, os.O_RDWR | os.O_CREAT, "R9 endpoint lock"
    )
    handle = os.fdopen(descriptor, "r+", encoding="utf-8", closefd=True)
    if not existed:
        _fsync_directory(directory)
    started = time.monotonic()
    try:
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() - started >= timeout:
                    raise EndpointLockTimeout(
                        f"endpoint lock {endpoint_hash[:16]} waited {timeout:g}s"
                    )
                time.sleep(0.05)
        wait_ms = (time.monotonic() - started) * 1000.0
        handle.seek(0)
        handle.truncate()
        handle.write(
            json.dumps(
                {
                    "pid": os.getpid(),
                    "acquired_unix": time.time(),
                    "endpoint_sha256": endpoint_hash,
                    "batch_id": os.environ.get("PCM_BATCH_ID", ""),
                },
                sort_keys=True,
            )
            + "\n"
        )
        handle.flush()
        os.fsync(handle.fileno())
        _validate_opened_single_link(path, handle.fileno(), "R9 endpoint lock")
        _fsync_directory(directory)
        yield {"wait_ms": round(wait_ms, 3), "lock_path": str(path)}
    finally:
        try:
            _validate_opened_single_link(path, handle.fileno(), "R9 endpoint lock")
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


class HardDeadline(TimeoutError):
    """A wall-clock deadline, as distinct from a per-socket inactivity timeout."""


class PostFailure(RuntimeError):
    """Serializable HTTP failure raised on the controlling process."""

    def __init__(self, kind, detail):
        super().__init__(detail)
        self.kind = kind
        self.detail = detail


class UnsupportedConstruction(RuntimeError):
    """The selected endpoint cannot execute the requested C protocol."""


class NondeterministicCircuit(RuntimeError):
    """Measured repetitions returned different canonical circuits/signatures."""


class ConstructionProtocolError(RuntimeError):
    """CircuitRun or an endpoint violated the declared construction protocol."""


class EndpointLockTimeout(TimeoutError):
    """Another harness owns the writable endpoint beyond the lock budget."""


def _mp_context():
    # The benchmark runs on Linux.  fork also lets the offline tests inject
    # deterministic rewrite functions without importing this script as __main__.
    methods = multiprocessing.get_all_start_methods()
    return multiprocessing.get_context("fork" if "fork" in methods else methods[0])


def _new_process_group():
    """Put a worker and any Java subprocess it starts in one killable group."""
    if os.name == "posix":
        try:
            os.setsid()
        except OSError:
            pass


def _process_group_alive(pgid):
    if os.name != "posix" or not pgid:
        return False
    proc_root = "/proc"
    if os.path.isdir(proc_root):
        observed_group = False
        try:
            names = os.listdir(proc_root)
        except OSError:
            names = ()
        for name in names:
            if not name.isdigit():
                continue
            try:
                raw = Path(proc_root, name, "stat").read_text(encoding="ascii")
                tail = raw[raw.rfind(")") + 2 :].split()
                state, process_group = tail[0], int(tail[2])
            except (OSError, ValueError, IndexError):
                continue
            if process_group == pgid:
                observed_group = True
                if state != "Z":
                    return True
        if observed_group:
            return False
    try:
        os.killpg(pgid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _kill_worker(proc):
    """Kill and confirm the worker's dedicated process group, even after leader exit."""
    if proc is None:
        return True
    pgid = proc.pid if os.name == "posix" else None
    leader_alive = proc.is_alive()
    group_alive = _process_group_alive(pgid)
    if os.name == "posix" and group_alive:
        try:
            os.killpg(pgid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    elif leader_alive:
        proc.terminate()
    proc.join(0.10)
    leader_alive = proc.is_alive()
    group_alive = _process_group_alive(pgid)
    if leader_alive or group_alive:
        if os.name == "posix" and group_alive:
            try:
                os.killpg(pgid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        elif leader_alive:
            proc.kill()
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            proc.join(0.05)
            if not proc.is_alive() and not _process_group_alive(pgid):
                break
    return not proc.is_alive() and not _process_group_alive(pgid)


def _remaining(deadline):
    remain = deadline - time.monotonic()
    if remain <= 0:
        raise HardDeadline("cell wall-clock budget exhausted")
    return remain


# ---------------------------------------------------------------------------
# HTTP: direct implementation (inside a killable worker) and public wrapper.

def _post_timed_direct(
    endpoint,
    body,
    accept,
    keep=False,
    timeout=TIMEOUT,
    return_chunks=False,
    content_type="application/sparql-query",
):
    """POST and fully drain one response.

    ``timeout`` is also supplied to urllib as an inactivity guard.  The caller
    must enforce the wall-clock limit (the cell worker is killed by its parent).
    Timing begins immediately before ``urlopen`` and ends only after EOF.
    """
    if timeout is None or timeout <= 0:
        raise HardDeadline("HTTP wall-clock budget exhausted before send")
    req = U.Request(endpoint, data=body.encode("utf-8"), method="POST")
    req.add_header("Content-Type", content_type)
    req.add_header("Accept", accept)
    chunks = [] if keep else None
    nbytes = n_newlines = 0
    last = b""
    started = time.monotonic()
    try:
        # Publication traffic must go to the frozen endpoint directly; ambient
        # HTTP(S)_PROXY variables are intentionally ignored.
        with _NO_PROXY_OPENER.open(
            req, timeout=max(0.001, timeout)
        ) as response:
            while True:
                raw = response.read(64 * 1024)
                if not raw:
                    break
                nbytes += len(raw)
                n_newlines += raw.count(b"\n")
                last = raw[-1:]
                if keep:
                    chunks.append(raw)
    except urllib.error.HTTPError as ex:
        # Error-body drain is deliberately inside the killable worker too.
        try:
            detail = ex.read(4096).decode("utf-8", "replace")
        except Exception:
            detail = str(ex)
        raise PostFailure("http", f"HTTP {ex.code}: {detail or ex.reason}") from ex
    except urllib.error.URLError as ex:
        reason = getattr(ex, "reason", ex)
        if isinstance(reason, (socket.timeout, TimeoutError)):
            raise HardDeadline(str(reason) or "HTTP inactivity timeout") from ex
        raise PostFailure("network", str(reason)) from ex
    except (socket.timeout, TimeoutError) as ex:
        raise HardDeadline(str(ex) or "HTTP inactivity timeout") from ex

    elapsed_ms = (time.monotonic() - started) * 1000.0
    # A final partial line is still a logical response line.
    nlines = n_newlines + (1 if nbytes and last != b"\n" else 0)
    if not keep:
        kept = None
    elif return_chunks:
        kept = chunks
    else:
        kept = b"".join(chunks).decode("utf-8", "strict")
    return elapsed_ms, nlines, nbytes, kept


def _post_worker(conn, path, endpoint, body, accept, keep, timeout, content_type):
    _new_process_group()
    try:
        ms, lines, nbytes, response = _post_timed_direct(
            endpoint,
            body,
            accept,
            keep=keep,
            timeout=timeout,
            content_type=content_type,
        )
        if keep:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(response)
        conn.send(("ok", ms, lines, nbytes))
    except HardDeadline as ex:
        conn.send(("timeout", str(ex)))
    except PostFailure as ex:
        conn.send(("post-error", ex.kind, ex.detail))
    except BaseException as ex:  # worker must never disappear without a diagnostic
        conn.send(("error", type(ex).__name__, str(ex)))
    finally:
        conn.close()


def post_timed(
    endpoint,
    body,
    accept,
    keep=False,
    timeout=TIMEOUT,
    content_type="application/sparql-query",
):
    """Hard-deadline HTTP POST used by the standalone parity pass.

    The worker writes a kept response to a temporary file before sending its
    small completion message, avoiding pipe deadlock on large result sets.
    """
    if timeout is None or timeout <= 0:
        raise HardDeadline("HTTP wall-clock budget exhausted before send")
    ctx = _mp_context()
    recv, send = ctx.Pipe(duplex=False)
    tmp = tempfile.NamedTemporaryFile(prefix="pcm-response-", suffix=".tmp", delete=False)
    path = tmp.name
    tmp.close()
    proc = ctx.Process(
        target=_post_worker,
        args=(send, path, endpoint, body, accept, keep, timeout, content_type),
    )
    started = time.monotonic()
    proc.start()
    send.close()
    proc.join(max(0.0, timeout - (time.monotonic() - started)))
    elapsed = time.monotonic() - started
    try:
        if proc.is_alive() or elapsed > timeout:
            if not _kill_worker(proc):
                raise PostFailure(
                    "worker", "HTTP worker process group survived SIGKILL confirmation"
                )
            raise HardDeadline(f"HTTP response exceeded {timeout:g}s hard deadline")
        if not recv.poll():
            if not _kill_worker(proc):
                raise PostFailure(
                    "worker", "HTTP worker exited without a result and descendants survived"
                )
            raise PostFailure("worker", f"HTTP worker exited {proc.exitcode} without a result")
        msg = recv.recv()
        if msg[0] == "timeout":
            raise HardDeadline(msg[1])
        if msg[0] == "post-error":
            raise PostFailure(msg[1], msg[2])
        if msg[0] == "error":
            raise PostFailure("worker", f"{msg[1]}: {msg[2]}")
        _, ms, lines, nbytes = msg
        # Do not record an over-budget request as successful even if completion
        # raced with the parent's timer at the boundary.
        if ms > timeout * 1000.0 or elapsed > timeout:
            raise HardDeadline(f"HTTP response exceeded {timeout:g}s hard deadline")
        if keep:
            with open(path, encoding="utf-8") as response_fh:
                response = response_fh.read()
        else:
            response = None
        return ms, lines, nbytes, response
    finally:
        recv.close()
        if proc.is_alive() or _process_group_alive(proc.pid if os.name == "posix" else None):
            _kill_worker(proc)
        try:
            proc.close()
        except (ValueError, AttributeError):
            pass
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass


# ---------------------------------------------------------------------------
# Stable answer identities/evidence.

def normalized_csv_multiset(text, drop_vars=()):
    """Parse a SPARQL CSV result into a column-order-independent row Counter.

    This is exact for CSV serialization (including bag multiplicity), not a
    term-aware RDF comparison.  The latter is performed from Results JSON by
    ``verify_brnc_parity.py``.
    """
    reader = csv.reader(io.StringIO(text, newline=""))
    try:
        header = next(reader)
    except StopIteration:
        raise ValueError("empty SPARQL CSV response")
    header = [v.lstrip("\ufeff").lstrip("?") for v in header]
    if len(set(header)) != len(header):
        raise ValueError(f"duplicate SPARQL CSV variables: {header!r}")
    drop = {v.lstrip("?") for v in drop_vars}
    keep = sorted((name, i) for i, name in enumerate(header) if name not in drop)
    rows = collections.Counter()
    for row_no, row in enumerate(reader, 2):
        if len(row) != len(header):
            raise ValueError(
                f"SPARQL CSV row {row_no} has {len(row)} fields; expected {len(header)}"
            )
        rows[tuple((name, row[i]) for name, i in keep)] += 1
    return rows


def csv_variables(text):
    reader = csv.reader(io.StringIO(text, newline=""))
    try:
        return [name.lstrip("\ufeff").lstrip("?") for name in next(reader)]
    except StopIteration:
        raise ValueError("empty SPARQL CSV response")


def provenance_output_variable(variables, rewritten_query=None):
    """Find the one NPCS-generated provenance column without deleting user data.

    A capture-safe NpcsRewriter uses ``?__npcsN_finalprovennacevariable`` when
    the user owns the legacy variable name.  The rewritten ``AS`` alias is
    authoritative.  Header-only fallback prefers exactly one generated alias;
    only when none exists does it use the legacy name.  It never drops both.
    """
    variables = [str(name).lstrip("?") for name in variables]
    if rewritten_query:
        aliases = re.findall(
            r"\bAS\s+\?([A-Za-z_][A-Za-z0-9_]*finalprovennacevariable)\s*\)",
            rewritten_query,
            flags=re.IGNORECASE,
        )
        aliases = [name for name in aliases if name in variables]
        if aliases:
            return aliases[-1]
    generated = [name for name in variables if GENERATED_PROVENANCE_RE.fullmatch(name)]
    if len(generated) == 1:
        return generated[0]
    if len(generated) > 1:
        raise ValueError(
            "ambiguous NPCS provenance columns; pass the rewritten query to identify its AS alias: "
            + repr(generated)
        )
    if PROVENANCE_VAR in variables:
        return PROVENANCE_VAR
    raise ValueError(f"NPCS provenance column not found in {variables!r}")


def npcs_csv_candidate_multiset(text, rewritten_query=None):
    provenance = provenance_output_variable(
        csv_variables(text), rewritten_query=rewritten_query
    )
    return normalized_csv_multiset(text, drop_vars=(provenance,))


def _npcs_node_counts(text, rewritten_query=None):
    """Node counts for an NPCS provenance CSV (RQ2 compactness): (otimes, oplus, ominus, leaves),
    one node per otimes/oplus/ominus operator and per leaf.  The operator symbols occur only inside
    provenance strings, so they are counted over the whole response; leaves (the reified-statement
    atoms that are the direct children of a product) are counted only inside the provenance column,
    so answer-binding IRIs in other columns are not miscounted.  Grammar:
    engine/examples/npcs_optional.expected.txt  (a sum wraps a product wraps leaf atoms; OPTIONAL/
    MINUS add a difference operator).
    NOTE: leaf tokenisation assumes leaf atoms contain none of the four delimiters used by the
    provenance grammar.  Validate against a real WatDiv NPCS response and widen the split class if
    leaf IRIs embed any of them."""
    otimes, oplus, ominus = text.count("⊗"), text.count("⊕"), text.count("⊖")
    leaves = None
    try:
        prov = provenance_output_variable(csv_variables(text), rewritten_query=rewritten_query)
        leaves = 0
        for row in csv.DictReader(text.splitlines()):
            cell = row.get(prov) or ""
            if "⊗" in cell:
                leaves += sum(1 for atom in re.split(r"[⊕⊗⊖(),]+", cell) if atom)
    except Exception:
        leaves = None
    return otimes, oplus, ominus, leaves


def _digest_counted(items):
    """Stable SHA-256 over ``(key, multiplicity)`` pairs."""
    payload = [
        [key, count]
        for key, count in sorted(items.items(), key=lambda item: repr(item[0]))
    ]
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _digest_set(items):
    raw = json.dumps(sorted(items), ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def multiset_evidence(items, kind="csv-binding-multiset-v1"):
    return {
        "answer_kind": kind,
        "answer_key_count": sum(items.values()),
        "answer_fingerprint": _digest_counted(items),
    }


def set_evidence(items, kind="term-aware-candidate-set-v1"):
    values = set(items)
    return {
        "answer_kind": kind,
        "answer_key_count": len(values),
        "answer_fingerprint": _digest_set(values),
    }


def canonical_json_term(value):
    """Canonicalize one SPARQL Results JSON binding like circuit_io.canon_term."""
    if value is None:
        return "u"
    kind = value.get("type")
    lexical = value.get("value", "")
    us = circuit_io.US
    if kind == "uri":
        return "i" + us + lexical
    if kind == "bnode":
        return "b" + us + lexical
    if kind in ("literal", "typed-literal"):
        lang = (value.get("xml:lang") or value.get("lang") or "").lower()
        datatype = value.get("datatype") or (
            circuit_io.RDF_LANGSTRING if lang else circuit_io.XSD_STRING
        )
        return "l" + us + lexical + us + datatype + us + lang
    raise ValueError(f"unsupported SPARQL Results JSON term type: {kind!r}")


def json_binding_key(binding, variables):
    canonical = {name: canonical_json_term(binding.get(name)) for name in variables}
    return circuit_io.answer_key(canonical)


def json_binding_multiset(payload, drop_vars=()):
    """Term-aware canonical binding multiset from a Results JSON document."""
    drop = {v.lstrip("?") for v in drop_vars}
    variables = sorted(v for v in payload.get("head", {}).get("vars", []) if v not in drop)
    rows = payload.get("results", {}).get("bindings", [])
    return collections.Counter(json_binding_key(row, variables) for row in rows)


def csv_row_count(text):
    return sum(normalized_csv_multiset(text).values())


# ---------------------------------------------------------------------------
# Query generators.

def q_base(qtext):
    return qtext


def q_reify(qtext):
    # Keep rdflib (used by reify_query) optional for importing/testing the
    # otherwise-stdlib harness on endpoint-free machines.
    import reify_query
    return reify_query.reify(qtext)


@contextlib.contextmanager
def _formal_java_descriptors():
    """Expose the frozen Java/JAR inodes to one child without reopening paths."""
    snapshots = _FORMAL_TOOL_SNAPSHOTS
    if not snapshots:
        yield None
        return
    java_snapshot = snapshots[FROZEN_JAVA_RUNTIME_NAME]
    jar_snapshot = snapshots[FROZEN_TOOL_NAME]
    _verify_tool_snapshot(java_snapshot)
    _verify_tool_snapshot(jar_snapshot)
    java_fd = _open_single_link(
        java_snapshot["path"], os.O_RDONLY, FROZEN_JAVA_RUNTIME_NAME
    )
    jar_fd = _open_single_link(jar_snapshot["path"], os.O_RDONLY, FROZEN_TOOL_NAME)
    try:
        yield {
            "java_fd": java_fd,
            "jar_fd": jar_fd,
            "java_path": f"/proc/self/fd/{java_fd}",
            "jar_path": f"/proc/self/fd/{jar_fd}",
            "java_display": java_snapshot["path"],
        }
        for snapshot, descriptor in (
            (java_snapshot, java_fd),
            (jar_snapshot, jar_fd),
        ):
            current = _validate_opened_single_link(
                snapshot["path"], descriptor, snapshot["label"]
            )
            if tuple(snapshot["signature"]) != _stat_signature(current):
                raise RuntimeError(f"{snapshot['label']} changed during Java execution")
    finally:
        os.close(jar_fd)
        os.close(java_fd)
        _verify_tool_snapshot(java_snapshot)
        _verify_tool_snapshot(jar_snapshot)


def _run_java(arguments, *, timeout=None, extra_pass_fds=()):
    """Run the resolved Java runtime; formal runs execute the verified FDs."""
    extra_pass_fds = tuple(int(value) for value in extra_pass_fds)
    with _formal_java_descriptors() as frozen:
        if frozen is None:
            command = [JAVA, *arguments]
            return subprocess.run(
                command,
                pass_fds=extra_pass_fds,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        replaced = [
            frozen["jar_path"] if value == "{frozen-jar}" else value
            for value in arguments
        ]
        command = [frozen["java_display"], *replaced]
        return subprocess.run(
            command,
            executable=frozen["java_path"],
            pass_fds=(frozen["java_fd"], frozen["jar_fd"], *extra_pass_fds),
            capture_output=True,
            text=True,
            timeout=timeout,
        )


def q_npcs(qtext, timeout=None):
    """Clean-room NpcsRewriter provenance SELECT."""
    result = _run_java(
        [
            "-jar",
            "{frozen-jar}" if _FORMAL_TOOL_SNAPSHOTS else JAR,
            "Standard",
            "query",
            qtext,
        ],
        timeout=timeout,
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise RuntimeError(
            f"NpcsRewriter failed rc={result.returncode}: {result.stderr[-300:]}"
        )
    return result.stdout


class ConstructionStep:
    """One parsed CircuitRun step plus its client feedback obligation."""

    __slots__ = ("query", "feedback", "label")

    def __init__(self, query, feedback=False, label=""):
        self.query = str(query)
        self.feedback = bool(feedback)
        self.label = str(label or "")


class ConstructionPlan(Sequence):
    """Metadata-bearing plan which remains iterable as legacy query strings."""

    __slots__ = ("steps", "requested_mode", "effective_mode", "fallback_reason")

    def __init__(
        self,
        steps,
        requested_mode="unknown",
        effective_mode="unknown",
        fallback_reason=None,
    ):
        self.steps = tuple(
            step if isinstance(step, ConstructionStep) else ConstructionStep(step)
            for step in steps
        )
        self.requested_mode = requested_mode or "unknown"
        self.effective_mode = effective_mode or "unknown"
        self.fallback_reason = fallback_reason or None

    def __len__(self):
        return len(self.steps)

    def __getitem__(self, index):
        if isinstance(index, slice):
            return [step.query for step in self.steps[index]]
        return self.steps[index].query

    def __iter__(self):
        return (step.query for step in self.steps)

    @property
    def requires_feedback(self):
        return any(step.feedback for step in self.steps)


def _construct_emits_private_messages(query):
    """Infer CircuitRun's feedback bit from the CONSTRUCT template only.

    Later factored steps also mention ``urn:sc:message`` in their WHERE clause,
    so searching the complete query would incorrectly feed the final answer
    response back to the endpoint.
    """
    match = re.search(r"\bCONSTRUCT\b(.*?)\bWHERE\b", query, re.IGNORECASE | re.DOTALL)
    return bool(match and re.search(r"<urn:sc:[^>]*>", match.group(1)))


def _normalize_c_plan(plan):
    """Accept v4 metadata plans and the historical ``list[str]`` test API."""
    if isinstance(plan, ConstructionPlan):
        return plan
    if isinstance(plan, dict):
        steps = [
            ConstructionStep(
                step.get("query", ""),
                step.get("feedback", _construct_emits_private_messages(step.get("query", ""))),
                step.get("label", ""),
            )
            if isinstance(step, dict)
            else ConstructionStep(step, _construct_emits_private_messages(str(step)))
            for step in plan.get("steps", ())
        ]
        return ConstructionPlan(
            steps,
            plan.get("requested_mode", "unknown"),
            plan.get("effective_mode", "unknown"),
            plan.get("fallback_reason"),
        )
    steps = [
        ConstructionStep(body, _construct_emits_private_messages(str(body)))
        for body in plan
    ]
    mode = "factored" if any(step.feedback for step in steps) else "flat"
    return ConstructionPlan(steps, mode, mode)


def c_construct_plan(qtext, timeout=None, construction="factored"):
    """Extract an executable, metadata-bearing plan from CircuitRun stderr."""
    query_file = tempfile.NamedTemporaryFile(
        "w+", suffix=".rq", delete=False, encoding="utf-8", newline=""
    )
    query_file.write(qtext)
    query_file.flush()
    os.fsync(query_file.fileno())
    if os.fstat(query_file.fileno()).st_nlink != 1:
        query_name = query_file.name
        query_file.close()
        try:
            os.unlink(query_name)
        except FileNotFoundError:
            pass
        raise ValueError("CircuitRun query temporary gained a hardlink")
    query_signature = _stat_signature(os.fstat(query_file.fileno()))
    query_argument = (
        f"/proc/self/fd/{query_file.fileno()}"
        if os.path.isdir("/proc/self/fd")
        else query_file.name
    )
    passed = (query_file.fileno(),) if query_argument.startswith("/proc/") else ()
    try:
        result = _run_java(
            [
                "-cp",
                "{frozen-jar}" if _FORMAL_TOOL_SNAPSHOTS else JAR,
                "npcs.circuit.CircuitRun",
                f"--construction={construction}",
                "Standard",
                os.path.join(REF, "bench_engine", "tiny.ttl"),
                query_argument,
            ],
            timeout=timeout,
            extra_pass_fds=passed,
        )
    finally:
        try:
            query_current = _validate_opened_single_link(
                query_file.name,
                query_file.fileno(),
                "CircuitRun query temporary",
            )
            query_changed = _stat_signature(query_current) != query_signature
        except ValueError:
            query_changed = True
        query_name = query_file.name
        query_file.close()
        if not query_changed:
            os.unlink(query_name)
        if query_changed:
            raise ValueError("CircuitRun query temporary changed during execution")
    if result.returncode != 0:
        raise RuntimeError(
            f"CircuitRun rewrite failed rc={result.returncode}: {result.stderr[-300:]}"
        )
    mode_matches = re.findall(
        r"construction mode:\s*requested=([a-z-]+),\s*effective=([a-z-]+)",
        result.stderr,
        re.IGNORECASE,
    )
    if len(mode_matches) != 1:
        raise ConstructionProtocolError(
            "CircuitRun must emit exactly one construction-mode marker"
        )
    requested_mode, effective_mode = (
        mode_matches[0][0].lower(),
        mode_matches[0][1].lower(),
    )
    if requested_mode != construction or effective_mode != construction:
        raise ConstructionProtocolError(
            "CircuitRun construction marker disagrees with the requested strict mode: "
            f"requested={requested_mode}, effective={effective_mode}, CLI={construction}"
        )
    fallback_match = re.search(r"explicit fallback:\s*(.*?)\s*----", result.stderr)
    fallback_reason = fallback_match.group(1) if fallback_match else None
    if fallback_reason:
        raise ConstructionProtocolError(
            "formal CircuitRun forbids construction-mode fallback: "
            + fallback_reason[:160]
        )

    steps = []
    headers = list(re.finditer(r"(?m)^# --- step \d+ ---\s*$", result.stderr))
    for index, header in enumerate(headers):
        end = headers[index + 1].start() if index + 1 < len(headers) else len(result.stderr)
        chunk = result.stderr[header.end():end]
        chunk = chunk.split("# circuit triples", 1)[0]
        label_match = re.search(r"(?m)^# step label:\s*(.*?)\s*$", chunk)
        label = label_match.group(1) if label_match else ""
        if label_match:
            chunk = chunk[:label_match.start()]
        chunk = chunk.strip()
        if chunk.startswith(("PREFIX", "CONSTRUCT")):
            steps.append(
                ConstructionStep(
                    chunk,
                    feedback=_construct_emits_private_messages(chunk),
                    label=label,
                )
            )
    if not steps:
        grab, current = False, []
        for line in result.stderr.splitlines():
            if line.startswith("PREFIX c:"):
                grab = True
            if line.startswith("# circuit triples"):
                grab = False
            if grab:
                current.append(line)
        if current:
            body = "\n".join(current)
            steps = [ConstructionStep(body, _construct_emits_private_messages(body))]
    if not steps:
        raise RuntimeError("empty CONSTRUCT plan")
    return ConstructionPlan(
        steps,
        requested_mode=requested_mode,
        effective_mode=effective_mode,
        fallback_reason=fallback_reason,
    )


def parse_circuit(nt_lines, include_keys=False):
    """Count a deduplicated RDF circuit and recover term-aware answer keys."""
    lines = list(nt_lines)
    typ, feeds, tin = {}, {}, {}
    for line in lines:
        line = line.strip()
        if not line.endswith(" ."):
            continue
        try:
            subject, predicate, obj = line[:-2].split(None, 2)
        except ValueError:
            continue
        subject = subject.strip("<>")
        predicate = predicate.strip("<>")
        if predicate == RS + "type":
            typ[subject] = obj.strip("<>")
        elif predicate == "urn:circuit:feeds":
            feeds.setdefault(obj.strip("<>"), set()).add(subject)
        elif predicate == "urn:circuit:in":
            tin.setdefault(subject, set()).add(obj.strip("<>"))
    circ, answer_gates, bindings = circuit_io.parse(lines)
    keys = {circuit_io.answer_key(bindings.get(gate, {})) for gate in answer_gates}
    gates = sum(1 for value in typ.values() if value.endswith(("Times", "Plus", "Minus")))
    times = sum(1 for value in typ.values() if value.endswith("Times"))
    plus = sum(1 for value in typ.values() if value.endswith("Plus"))
    minus = sum(1 for value in typ.values() if value.endswith("Minus"))
    leaves = sum(1 for op, _payload in circ.values() if op == "leaf")
    edges = sum(map(len, tin.values())) + sum(map(len, feeds.values()))
    # RQ2 compactness node count: each leaf, product, sum and difference is one node.  `gates`
    # stays operator-only for back-compat; `nodes` = leaves + operators is the full count that is
    # compared against the NPCS node count.
    nodestats = {"leaves": leaves, "plus": plus, "minus": minus, "nodes": leaves + gates}
    result = (gates, edges, len(answer_gates), times, nodestats)
    return result + (keys,) if include_keys else result


# ---------------------------------------------------------------------------
# One whole-cell worker.  There is exactly one deadline for rewrite + all runs.

def stat(values):
    return {
        "median": statistics.median(values),
        "min": min(values),
        "max": max(values),
        "mean": statistics.mean(values),
        "sd": statistics.stdev(values) if len(values) > 1 else 0.0,
    }


def _empty_result(status, note, rewrite_ms=None, status_evidence_kind=None):
    return {
        "status": status,
        "answers": None,
        "samples": [],
        "response_bytes": None,
        "c_parse": [],
        "c_protocol": [],
        "construct_total": [],
        "construct_unattributed": [],
        "gates": None,
        "edges": None,
        "derivations": None,
        "ntok": None,
        "rewrite_ms": rewrite_ms,
        "evidence": {},
        "protocol_metrics": {},
        "cache": {},
        "circuit_sha256": None,
        "status_evidence": {
            "kind": status_evidence_kind or "unspecified",
            "message": str(note)[:240],
        },
        "note": note,
    }


def _failure_result(ex, rewrite_ms=None):
    if isinstance(ex, (HardDeadline, socket.timeout, subprocess.TimeoutExpired)):
        return _empty_result(
            "timeout",
            str(ex)[:160] or "cell deadline exhausted",
            rewrite_ms,
            "hard-wall-deadline",
        )
    if isinstance(ex, PostFailure):
        if ex.kind in ("network", "worker"):
            return _empty_result(
                f"err:{ex.kind}", ex.detail[:160], rewrite_ms, "retryable-transport"
            )
        lowered = ex.detail.lower()
        status_match = re.match(r"HTTP\s+(\d{3}):", ex.detail)
        http_status = int(status_match.group(1)) if status_match else None
        explicit_feature_error = bool(
            re.search(
                r"\b(?:sparql\s+)?feature\b.{0,120}\b(?:unsupported|not supported|not implemented)\b"
                r"|\b(?:unsupported|not supported|not implemented)\b.{0,120}\b(?:sparql\s+)?feature\b",
                lowered,
            )
        )
        if 400 <= (http_status or 0) < 500 and http_status in (
            400,
            405,
            406,
            415,
            422,
        ) and explicit_feature_error:
            status = "unsupported"
            evidence_kind = "explicit-4xx-feature-unsupported"
        else:
            status = "err:http"
            evidence_kind = "retryable-http"
        return _empty_result(
            status, ex.detail[:160], rewrite_ms, evidence_kind
        )
    if isinstance(ex, UnsupportedConstruction):
        return _empty_result(
            "unsupported",
            str(ex)[:160],
            rewrite_ms,
            "construction-feature-unsupported",
        )
    if isinstance(ex, NondeterministicCircuit):
        return _empty_result(
            "answer-mismatch",
            str(ex)[:160],
            rewrite_ms,
            "measured-circuit-disagreement",
        )
    if isinstance(ex, MemoryError):
        return _empty_result(
            "oom", str(ex)[:160] or "local MemoryError", rewrite_ms, "local-memory-error"
        )
    return _empty_result(f"err:{type(ex).__name__}", str(ex)[:160], rewrite_ms)


_NT_IRI = r'<(?:[^<>"{}|^`\\\x00-\x20]|\\u[0-9A-Fa-f]{4}|\\U[0-9A-Fa-f]{8})*>'
_NT_BNODE = r'_:[A-Za-z0-9_](?:[A-Za-z0-9._-]*[A-Za-z0-9_-])?'
_NT_STRING = (
    r'"(?:[^"\\\x00-\x1f]|\\[tbnrf"\'\\]|'
    r'\\u[0-9A-Fa-f]{4}|\\U[0-9A-Fa-f]{8})*"'
)
_NT_LITERAL = rf'(?:{_NT_STRING})(?:@[A-Za-z]+(?:-[A-Za-z0-9]+)*|\^\^{_NT_IRI})?'
_NT_LINE = re.compile(
    rf'^(?P<subject>{_NT_IRI}|{_NT_BNODE})[ \t]+'
    rf'(?P<predicate>{_NT_IRI})[ \t]+'
    rf'(?P<object>{_NT_IRI}|{_NT_BNODE}|{_NT_LITERAL})[ \t]+\.$'
)


def _merge_circuit_chunks(chunks, unique):
    """Strictly decode N-Triples, separating private protocol rows."""
    text = b"".join(chunks).decode("utf-8", "strict")
    private = set()
    for line_no, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = _NT_LINE.fullmatch(line)
        if match is None:
            raise ValueError(f"invalid N-Triples response line {line_no}")
        predicate = match.group("predicate")
        if predicate.startswith("<urn:sc:"):
            private.add(line)
        else:
            unique.add(line)
    return private


def _update_bodies(
    operation, triples, chunk_size=FORMAL_UPDATE_CHUNK_TRIPLES
):
    """Yield deterministic row-atomic standard-SPARQL Update requests.

    A private relation row is never split across HTTP requests. In particular,
    every INSERT request that can create a row also carries its indexed
    ``urn:sc:message`` marker, so a post-kill orphan sweep can always find it.
    """
    if chunk_size <= 0:
        raise ValueError("update chunk size must be positive")
    by_subject = collections.defaultdict(set)
    for line in set(triples):
        try:
            subject, _rest = line.split(None, 1)
        except ValueError as ex:
            raise ValueError(f"invalid N-Triples feedback row: {line!r}") from ex
        by_subject[subject].add(line)
    groups = []
    for subject in sorted(by_subject):
        group = sorted(by_subject[subject])
        if operation.upper() == "INSERT" and not any(
            " <urn:sc:message> " in line for line in group
        ):
            raise ValueError(
                f"feedback row {subject} lacks its urn:sc:message marker"
            )
        groups.append(group)
    chunks, current = [], []
    for group in groups:
        if current and len(current) + len(group) > chunk_size:
            chunks.append(current)
            current = []
        current.extend(group)
    if current:
        chunks.append(current)
    for lines in chunks:
        block = "\n".join(lines)
        yield f"{operation} DATA {{\n{block}\n}}"


def _post_update(endpoint, body, deadline):
    return _post_timed_direct(
        endpoint,
        body,
        "*/*",
        keep=False,
        timeout=_remaining(deadline),
        content_type="application/sparql-update",
    )[0]


def _post_update_hard(endpoint, body, deadline):
    """Per-request killable update for the unmeasured parity executor."""
    return post_timed(
        endpoint,
        body,
        "*/*",
        keep=False,
        timeout=_remaining(deadline),
        content_type="application/sparql-update",
    )[0]


ORPHAN_CLEANUP_UPDATE = """DELETE { ?row ?p ?o }
WHERE {
  ?row <urn:sc:message> ?message .
  ?row ?p ?o .
}"""


def _orphan_cleanup(endpoint, timeout=FORMAL_ORPHAN_CLEANUP_TIMEOUT):
    """Hard-bounded cleanup used outside a measured C cell.

    R9 runs C cells serially. Every private relation row has the indexed
    ``urn:sc:message`` predicate, so this avoids a full-store predicate-prefix
    scan while removing all properties of every orphan row. The sweep makes a
    worker SIGKILL fail-safe: the next factored cell never observes private
    rows left by a process whose Python ``finally`` could not run.
    """
    timeout = float(timeout)
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("orphan cleanup timeout must be positive")
    ms, _lines, _nbytes, _response = post_timed(
        endpoint,
        ORPHAN_CLEANUP_UPDATE,
        "*/*",
        keep=False,
        timeout=timeout,
        content_type="application/sparql-update",
    )
    return ms


def _raise_with_cleanup(primary, cleanup_error, cleanup_attempted=False):
    """Preserve the primary status while making cleanup failure visible."""
    if cleanup_error is None:
        if not cleanup_attempted:
            raise primary
        detail = f"{primary}; private-workspace cleanup=ok"
    else:
        detail = f"{primary}; private-workspace cleanup also failed: {cleanup_error}"
    if isinstance(primary, PostFailure):
        raise PostFailure(primary.kind, detail) from primary
    if isinstance(primary, (HardDeadline, socket.timeout, subprocess.TimeoutExpired)):
        raise HardDeadline(detail) from primary
    raise RuntimeError(detail) from primary


def _execute_c_once(
    endpoint,
    update_endpoint,
    plan,
    deadline,
    include_circuit=False,
    hard_http=False,
    update_chunk_triples=FORMAL_UPDATE_CHUNK_TRIPLES,
):
    """Execute query/feedback/cleanup under one still-shrinking cell budget."""
    plan = _normalize_c_plan(plan)
    if plan.requires_feedback and not update_endpoint:
        raise UnsupportedConstruction(
            "factored C plan requires a registered writable SPARQL Update endpoint; "
            "select construction=flat on a read-only engine"
        )
    network_ms = 0.0
    construct_query_ms = 0.0
    feedback_update_ms = 0.0
    cleanup_ms = 0.0
    client_parse_ms = 0.0
    client_protocol_ms = 0.0
    unique = set()
    inserted_private = set()
    feedback_responses = 0
    primary_error = cleanup_error = None
    try:
        for step in plan.steps:
            if hard_http:
                ms, _, _, text = post_timed(
                    endpoint,
                    step.query,
                    "application/n-triples",
                    keep=True,
                    timeout=_remaining(deadline),
                )
                chunks = [text.encode("utf-8")]
            else:
                ms, _, _, chunks = _post_timed_direct(
                    endpoint,
                    step.query,
                    "application/n-triples",
                    keep=True,
                    timeout=_remaining(deadline),
                    return_chunks=True,
                )
            network_ms += ms
            construct_query_ms += ms
            parse_started = time.monotonic()
            private = _merge_circuit_chunks(chunks, unique)
            client_parse_ms += (time.monotonic() - parse_started) * 1000.0
            _remaining(deadline)
            if step.feedback:
                if not private:
                    raise ConstructionProtocolError(
                        f"feedback step {step.label or feedback_responses + 1!r} "
                        "returned zero private rows"
                    )
                feedback_responses += 1
                # Mark all rows as possibly inserted before sending. If a server
                # applies an update and the response connection then fails, the
                # finally cleanup must still attempt to delete those rows.
                inserted_private.update(private)
                protocol_started = time.monotonic()
                update_bodies = list(
                    _update_bodies(
                        "INSERT", private, chunk_size=update_chunk_triples
                    )
                )
                client_protocol_ms += (time.monotonic() - protocol_started) * 1000.0
                for update_body in update_bodies:
                    post_update = _post_update_hard if hard_http else _post_update
                    update_local_started = time.monotonic()
                    ms = post_update(update_endpoint, update_body, deadline)
                    client_protocol_ms += max(
                        0.0,
                        (time.monotonic() - update_local_started) * 1000.0 - ms,
                    )
                    network_ms += ms
                    feedback_update_ms += ms
                    _remaining(deadline)
    except BaseException as ex:
        primary_error = ex
    finally:
        if inserted_private:
            try:
                protocol_started = time.monotonic()
                cleanup_bodies = list(
                    _update_bodies(
                        "DELETE",
                        inserted_private,
                        chunk_size=update_chunk_triples,
                    )
                )
                client_protocol_ms += (
                    time.monotonic() - protocol_started
                ) * 1000.0
                for cleanup_body in cleanup_bodies:
                    post_update = _post_update_hard if hard_http else _post_update
                    update_local_started = time.monotonic()
                    ms = post_update(update_endpoint, cleanup_body, deadline)
                    client_protocol_ms += max(
                        0.0,
                        (time.monotonic() - update_local_started) * 1000.0 - ms,
                    )
                    network_ms += ms
                    cleanup_ms += ms
                    _remaining(deadline)
            except BaseException as ex:
                cleanup_error = ex

    if primary_error is not None:
        _raise_with_cleanup(
            primary_error,
            cleanup_error,
            cleanup_attempted=bool(inserted_private),
        )
    if cleanup_error is not None:
        raise cleanup_error

    # Wire drain ended before each merge above.  Final circuit decode and
    # binding recovery remain wholly in the disjoint client-parse interval.
    parse_started = time.monotonic()
    gates, edges, answers, derivations, nodestats, keys = parse_circuit(unique, include_keys=True)
    canonical = circuit_cache.canonical_bytes(unique)
    circuit_sha = hashlib.sha256(canonical).hexdigest()
    answer_fingerprint = set_evidence(keys)["answer_fingerprint"]
    signature_payload = {
        "circuit_sha256": circuit_sha,
        "answer_fingerprint": answer_fingerprint,
        "gates": gates,
        "edges": edges,
        "answers": answers,
        "derivations": derivations,
    }
    semantic_signature = hashlib.sha256(
        json.dumps(signature_payload, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()
    client_parse_ms += (time.monotonic() - parse_started) * 1000.0
    _remaining(deadline)
    dedup_bytes = len(canonical)
    protocol_metrics = {
        "requested_mode": plan.requested_mode,
        "effective_mode": plan.effective_mode,
        "fallback_reason": plan.fallback_reason,
        "plan_steps": len(plan.steps),
        "feedback_steps": feedback_responses,
        "feedback_triples": len(inserted_private),
        "construct_query_ms": round(construct_query_ms, 3),
        "feedback_update_ms": round(feedback_update_ms, 3),
        "cleanup_ms": round(cleanup_ms, 3),
        "client_protocol_ms": round(client_protocol_ms, 3),
        "network_scope": "construct+feedback+cleanup",
        "circuit_sha256": circuit_sha,
        "answer_fingerprint": answer_fingerprint,
        "semantic_signature_sha256": semantic_signature,
        "structure_signature": {
            "gates": gates,
            "edges": edges,
            "answers": answers,
            "derivations": derivations,
            "leaves": nodestats["leaves"],
            "plus": nodestats["plus"],
            "minus": nodestats["minus"],
            "nodes": nodestats["nodes"],
            "canonical_bytes": dedup_bytes,
        },
    }
    if include_circuit:
        # Internal-only handoff to _time_method_impl. It is removed before
        # protocol_metrics crosses the worker pipe or enters CSV notes.
        protocol_metrics["_captured_circuit"] = tuple(sorted(unique))
    return (
        network_ms,
        dedup_bytes,
        client_parse_ms,
        gates,
        edges,
        answers,
        derivations,
        keys,
        protocol_metrics,
    )


def _time_method_impl(
    method,
    qtext,
    base_ep,
    reified_ep,
    deadline,
    warmups,
    runs,
    rewrite_clock=None,
    update_ep=None,
    read_only=False,
    cache_capture_path=None,
    update_chunk_triples=FORMAL_UPDATE_CHUNK_TRIPLES,
):
    endpoint = base_ep if method == "B" else reified_ep
    rewrite_started = time.monotonic()
    try:
        if method == "B":
            bodies = [q_base(qtext)]
        elif method == "R":
            bodies = [q_reify(qtext)]
        elif method == "N":
            bodies = [q_npcs(qtext, timeout=_remaining(deadline))]
        elif method == "C":
            # PCM_FORCE_FLAT opt-in: measure the read-only single-CONSTRUCT (flat)
            # construction even on a writable engine — the NPCS-comparable construction
            # time for the R9.2 timing matrix (no feedback / workspace / orphan sweep).
            construction = "flat" if (read_only or os.environ.get("PCM_FORCE_FLAT")) else "factored"
            try:
                raw_plan = c_construct_plan(
                    qtext,
                    timeout=_remaining(deadline),
                    construction=construction,
                )
            except TypeError as ex:
                # Offline/third-party harnesses predating v4 commonly monkey
                # patch the two-argument plan function and return ``list[str]``.
                if "construction" not in str(ex):
                    raise
                raw_plan = c_construct_plan(qtext, timeout=_remaining(deadline))
            bodies = _normalize_c_plan(raw_plan)
            if read_only and bodies.requires_feedback:
                raise UnsupportedConstruction(
                    "read-only engine was explicitly assigned construction=flat, "
                    "but the returned plan still requires feedback"
                )
        else:
            raise ValueError(f"unknown method {method!r}")
        rewrite_ms = (time.monotonic() - rewrite_started) * 1000.0
        if rewrite_clock is not None:
            rewrite_clock.value = rewrite_ms
        _remaining(deadline)
    except BaseException as ex:
        rewrite_ms = (time.monotonic() - rewrite_started) * 1000.0
        if rewrite_clock is not None:
            rewrite_clock.value = rewrite_ms
        return _failure_result(ex, rewrite_ms)

    samples, parse_samples, protocol_cost_samples, construct_total_samples = [], [], [], []
    construct_unattributed_samples = []
    answers = response_bytes = gates = edges = derivations = ntok = None
    npcs_oplus = npcs_ominus = npcs_leaves = None
    evidence = {}
    protocol_samples = []
    warmup_signatures = []
    measured_signature = None
    circuit_sha256 = None
    captured_circuit = None
    cache_snapshot_ms = None
    try:
        for index in range(warmups + runs):
            capture = index == warmups  # first measured execution
            if method == "C":
                execution_started = time.monotonic()
                (
                    engine_ms,
                    nbytes,
                    parse_ms,
                    run_gates,
                    run_edges,
                    run_answers,
                    run_derivations,
                    candidate_keys,
                    run_protocol,
                ) = _execute_c_once(
                    endpoint,
                    update_ep,
                    bodies,
                    deadline,
                    include_circuit=bool(capture and cache_capture_path),
                    update_chunk_triples=update_chunk_triples,
                )
                execution_wall_ms = (time.monotonic() - execution_started) * 1000.0
                internal_circuit = run_protocol.pop("_captured_circuit", None)
                run_signature = run_protocol["semantic_signature_sha256"]
                if index < warmups:
                    warmup_signatures.append(run_signature)
                elif capture:
                    measured_signature = run_signature
                    circuit_sha256 = run_protocol["circuit_sha256"]
                    if cache_capture_path:
                        captured_circuit = internal_circuit
                    evidence = set_evidence(candidate_keys)
                    answers = run_answers
                    response_bytes = nbytes
                    gates, edges, derivations = (
                        run_gates,
                        run_edges,
                        run_derivations,
                    )
                elif run_signature != measured_signature:
                    raise NondeterministicCircuit(
                        "measured C repetitions disagree: first="
                        f"{measured_signature}, run{index - warmups + 1}={run_signature}"
                    )
            else:
                engine_ms, _, nbytes, text = _post_timed_direct(
                    endpoint,
                    bodies[0],
                    "text/csv",
                    keep=capture,
                    timeout=_remaining(deadline),
                )
                parse_ms = 0.0
                run_gates = run_edges = run_derivations = None
                if capture:
                    rows = normalized_csv_multiset(text)
                    run_answers = sum(rows.values())
                    if method in ("B", "R"):
                        evidence = multiset_evidence(rows)
                    else:
                        candidates = npcs_csv_candidate_multiset(
                            text, rewritten_query=bodies[0]
                        )
                        evidence = multiset_evidence(
                            candidates, kind="csv-candidate-multiset-v1"
                        )
                        ntok = text.count("⊗")
                        _, npcs_oplus, npcs_ominus, npcs_leaves = _npcs_node_counts(
                            text, rewritten_query=bodies[0]
                        )
                else:
                    run_answers = answers
            _remaining(deadline)
            if index >= warmups:
                samples.append(round(engine_ms, 3))
                parse_samples.append(round(parse_ms, 3))
                if method == "C":
                    protocol_ms = run_protocol["client_protocol_ms"]
                    protocol_cost_samples.append(round(protocol_ms, 3))
                    component_ms = engine_ms + parse_ms + protocol_ms
                    if execution_wall_ms + 0.001 < component_ms:
                        raise RuntimeError(
                            "C outer wall time is smaller than its measured components"
                        )
                    total_ms = round(execution_wall_ms, 3)
                    unattributed_ms = round(max(0.0, execution_wall_ms - component_ms), 3)
                    construct_total_samples.append(total_ms)
                    construct_unattributed_samples.append(unattributed_ms)
                    run_protocol["construct_total_ms"] = total_ms
                    run_protocol["client_unattributed_ms"] = unattributed_ms
                    protocol_samples.append(run_protocol)
            if method != "C":
                if capture or answers is None:
                    answers = run_answers
                response_bytes, gates, edges, derivations = (
                    nbytes,
                    run_gates,
                    run_edges,
                    run_derivations,
                )
        if method == "C" and cache_capture_path:
            if captured_circuit is None:
                raise RuntimeError("first measured C response was not captured for cache")
            snapshot_started = time.monotonic()
            payload = circuit_cache.canonical_bytes(captured_circuit)
            with open(cache_capture_path, "wb") as cache_snapshot:
                cache_snapshot.write(payload)
                cache_snapshot.flush()
                os.fsync(cache_snapshot.fileno())
            cache_snapshot_ms = (time.monotonic() - snapshot_started) * 1000.0
        _remaining(deadline)
    except BaseException as ex:
        # A cell is atomic: partial samples are intentionally not checkpointed as
        # successful.  The next invocation can retry a transient err:* result.
        return _failure_result(ex, rewrite_ms)

    protocol_metrics = {}
    note = ""
    if method == "C":
        protocol_metrics = {
            "c_protocol_samples": protocol_samples,
            "c_warmup_signatures": warmup_signatures,
            "measured_semantic_signature_sha256": measured_signature,
            "measured_repetitions_consistent": True,
        }
        if cache_snapshot_ms is not None:
            protocol_metrics["cache_snapshot_ms"] = round(cache_snapshot_ms, 3)
            protocol_metrics["cache_snapshot_scope"] = (
                "first-measured-response; deferred until all runs succeeded"
            )
        if protocol_samples:
            first = protocol_samples[0]
            note = (
                f"C requested={first['requested_mode']} effective={first['effective_mode']}; "
                f"steps={first['plan_steps']} feedback={first['feedback_steps']}; "
                "network includes CONSTRUCT, feedback INSERT, and cleanup DELETE"
            )
    return {
        "status": "ok",
        "answers": answers,
        "samples": samples,
        "response_bytes": response_bytes,
        "c_parse": parse_samples,
        "c_protocol": protocol_cost_samples,
        "construct_total": construct_total_samples,
        "construct_unattributed": construct_unattributed_samples,
        "gates": gates,
        "edges": edges,
        "derivations": derivations,
        "ntok": ntok,
        "npcs_oplus": npcs_oplus,
        "npcs_ominus": npcs_ominus,
        "npcs_leaves": npcs_leaves,
        "rewrite_ms": round(rewrite_ms, 6),
        "evidence": evidence,
        "protocol_metrics": protocol_metrics,
        "cache": {},
        "circuit_sha256": circuit_sha256,
        "note": note,
    }


def _cell_worker(
    conn,
    method,
    qtext,
    base_ep,
    reified_ep,
    deadline,
    warmups,
    runs,
    rewrite_clock,
    update_ep,
    read_only,
    cache_capture_path,
    update_chunk_triples,
):
    _new_process_group()
    try:
        result = _time_method_impl(
            method,
            qtext,
            base_ep,
            reified_ep,
            deadline,
            warmups,
            runs,
            rewrite_clock,
            update_ep,
            read_only,
            cache_capture_path,
            update_chunk_triples,
        )
        conn.send(("ok", result))
    except BaseException as ex:
        conn.send(("error", type(ex).__name__, str(ex)))
    finally:
        conn.close()


def _time_method_locked(
    method,
    qtext,
    base_ep,
    reified_ep,
    timeout=TIMEOUT,
    warmups=WARMUPS,
    runs=RUNS,
    update_ep=None,
    read_only=False,
    cache_dir=None,
    cache_metadata=None,
    update_chunk_triples=FORMAL_UPDATE_CHUNK_TRIPLES,
    orphan_cleanup_timeout=FORMAL_ORPHAN_CLEANUP_TIMEOUT,
):
    """Run one atomic method cell under one hard wall-clock deadline."""
    if not math.isfinite(float(timeout)) or timeout <= 0 or warmups < 0 or runs <= 0:
        raise ValueError("timeout and runs must be positive; warmups must be non-negative")
    if (cache_dir is None) != (cache_metadata is None):
        raise ValueError("cache_dir and cache_metadata must be provided together")
    cache_capture_path = None
    outside_cleanup = {}
    hygiene_enabled = (method == "C" and bool(update_ep) and not read_only
                       and not os.environ.get("PCM_FORCE_FLAT"))
    if hygiene_enabled:
        # This is deliberately outside ``started`` and therefore outside both
        # cell_wall_ms and the measured C samples. It is endpoint hygiene, not
        # construction work. A failure is fail-stop: no C query is submitted.
        try:
            outside_cleanup["orphan_preflight_ms"] = round(
                _orphan_cleanup(update_ep, timeout=orphan_cleanup_timeout), 3
            )
            outside_cleanup["orphan_preflight_status"] = "ok"
        except BaseException as ex:
            result = _empty_result(
                "err:cleanup",
                "outside-cell orphan preflight failed; C cell not started: "
                + str(ex)[:120],
            )
            result["cell_wall_ms"] = 0.0
            result["protocol_metrics"] = {
                "outside_cell_cleanup": {
                    "orphan_preflight_status": "failed",
                    "error": str(ex)[:160],
                }
            }
            return result

    if method == "C" and cache_dir is not None:
        capture = tempfile.NamedTemporaryFile(
            prefix="pcm-circuit-capture-", suffix=".nt", delete=False
        )
        cache_capture_path = capture.name
        capture.close()

    ctx = _mp_context()
    recv, send = ctx.Pipe(duplex=False)
    rewrite_clock = ctx.Value("d", -1.0)
    started = time.monotonic()
    deadline = started + timeout
    proc = ctx.Process(
        target=_cell_worker,
        args=(
            send,
            method,
            qtext,
            base_ep,
            reified_ep,
            deadline,
            warmups,
            runs,
            rewrite_clock,
            update_ep,
            read_only,
            cache_capture_path,
            update_chunk_triples,
        ),
    )
    proc.start()
    send.close()
    proc.join(max(0.0, deadline - time.monotonic()))
    elapsed = time.monotonic() - started
    worker_was_killed = False
    worker_reaped = True
    try:
        if proc.is_alive() or elapsed > timeout:
            worker_was_killed = True
            worker_reaped = _kill_worker(proc)
            if worker_reaped:
                result = _empty_result(
                    "timeout",
                    f"whole cell exceeded {timeout:g}s hard deadline; worker reaped",
                    round(rewrite_clock.value, 6) if rewrite_clock.value >= 0 else None,
                    "hard-wall-deadline",
                )
            else:
                result = _empty_result(
                    "err:worker-reap",
                    "worker survived SIGKILL confirmation; orphan cleanup skipped",
                    round(rewrite_clock.value, 6) if rewrite_clock.value >= 0 else None,
                )
        elif not recv.poll():
            worker_reaped = _kill_worker(proc)
            result = _empty_result(
                "err:worker" if worker_reaped else "err:worker-reap",
                (
                    f"cell worker exited {proc.exitcode} without a result"
                    if worker_reaped
                    else "cell worker exited but its process-group descendants survived"
                ),
                status_evidence_kind="retryable-worker-failure",
            )
        else:
            message = recv.recv()
            if message[0] == "ok":
                result = message[1]
            else:
                result = _empty_result(
                    "err:worker", f"{message[1]}: {message[2]}"[:160]
                )
        if proc.is_alive() or _process_group_alive(
            proc.pid if os.name == "posix" else None
        ):
            worker_reaped = _kill_worker(proc)
            if not worker_reaped:
                result = _empty_result(
                    "err:worker-reap",
                    "cell worker returned but process-group descendants survived",
                    result.get("rewrite_ms"),
                    "retryable-worker-failure",
                )
        result["cell_wall_ms"] = round(min(elapsed, timeout) * 1000.0, 3)
        # Last line of defence against the old per-socket loophole.
        if result.get("status") == "ok" and elapsed > timeout:
            result = _empty_result(
                "timeout",
                f"whole cell exceeded {timeout:g}s hard deadline",
                status_evidence_kind="hard-wall-deadline",
            )
        if hygiene_enabled and result.get("status") != "ok" and worker_reaped:
            # SIGTERM/SIGKILL bypasses the worker's finally; an ordinary HTTP
            # failure can also leave a server-applied update whose response was
            # lost. Sweep after every failed cell, with a separate hard deadline.
            phase = "postkill" if worker_was_killed else "postfailure"
            phase_note = "post-kill" if worker_was_killed else "post-failure"
            try:
                outside_cleanup[f"orphan_{phase}_ms"] = round(
                    _orphan_cleanup(
                        update_ep, timeout=orphan_cleanup_timeout
                    ), 3
                )
                outside_cleanup[f"orphan_{phase}_status"] = "ok"
                result["note"] = (
                    (result.get("note") or "")
                    + f"; outside-cell {phase_note} orphan cleanup=ok"
                ).lstrip("; ")
            except BaseException as ex:
                outside_cleanup[f"orphan_{phase}_status"] = "failed"
                outside_cleanup[f"orphan_{phase}_error"] = str(ex)[:160]
                result["note"] = (
                    (result.get("note") or "")
                    + f"; outside-cell {phase_note} orphan cleanup FAILED; "
                    "subsequent C must pass preflight: "
                    + str(ex)[:100]
                ).lstrip("; ")
        elif hygiene_enabled and not worker_reaped:
            outside_cleanup["orphan_postkill_status"] = "skipped-worker-alive"
        if outside_cleanup:
            result.setdefault("protocol_metrics", {})[
                "outside_cell_cleanup"
            ] = outside_cleanup
        if cache_capture_path and result.get("status") == "ok":
            cache_started = time.monotonic()
            try:
                producer_metadata = dict(cache_metadata)
                protocol_samples = (
                    (result.get("protocol_metrics") or {}).get(
                        "c_protocol_samples", []
                    )
                )
                if protocol_samples:
                    producer_metadata["construction_requested"] = protocol_samples[
                        0
                    ].get("requested_mode")
                    producer_metadata["construction_effective"] = protocol_samples[
                        0
                    ].get("effective_mode")
                with open(cache_capture_path, "rb") as captured:
                    descriptor = circuit_cache.store(
                        cache_dir,
                        captured.read().splitlines(),
                        producer_metadata,
                    )
                if descriptor["circuit_sha256"] != result.get("circuit_sha256"):
                    raise RuntimeError(
                        "cache payload hash differs from first measured circuit"
                    )
                result["cache"] = descriptor
                result.setdefault("protocol_metrics", {})[
                    "outside_cell_cache_write_ms"
                ] = round((time.monotonic() - cache_started) * 1000.0, 3)
            except BaseException as ex:
                failed = _empty_result(
                    "err:cache",
                    "measured C cell succeeded but external circuit cache failed: "
                    + str(ex)[:120],
                    result.get("rewrite_ms"),
                )
                failed["cell_wall_ms"] = result.get("cell_wall_ms")
                failed["protocol_metrics"] = {
                    **(result.get("protocol_metrics") or {}),
                    "outside_cell_cache_status": "failed",
                    "outside_cell_cache_error": str(ex)[:160],
                }
                result = failed
        return result
    finally:
        recv.close()
        if proc.is_alive() or _process_group_alive(
            proc.pid if os.name == "posix" else None
        ):
            _kill_worker(proc)
        try:
            proc.close()
        except (ValueError, AttributeError):
            pass
        if cache_capture_path:
            try:
                os.unlink(cache_capture_path)
            except FileNotFoundError:
                pass


def time_method(
    method,
    qtext,
    base_ep,
    reified_ep,
    timeout=TIMEOUT,
    warmups=WARMUPS,
    runs=RUNS,
    update_ep=None,
    read_only=False,
    cache_dir=None,
    cache_metadata=None,
    lock_identity=None,
    update_chunk_triples=FORMAL_UPDATE_CHUNK_TRIPLES,
    orphan_cleanup_timeout=FORMAL_ORPHAN_CLEANUP_TIMEOUT,
):
    """Run one cell, serializing every writable C protocol by endpoint."""
    if method == "C" and update_ep and not read_only:
        try:
            # Lock the reified query-store identity, not a transport-specific
            # update URL alias (e.g. GraphDB's /statements suffix).
            with endpoint_lock(lock_identity or reified_ep) as lock:
                result = _time_method_locked(
                    method,
                    qtext,
                    base_ep,
                    reified_ep,
                    timeout=timeout,
                    warmups=warmups,
                    runs=runs,
                    update_ep=update_ep,
                    read_only=read_only,
                    cache_dir=cache_dir,
                    cache_metadata=cache_metadata,
                    update_chunk_triples=update_chunk_triples,
                    orphan_cleanup_timeout=orphan_cleanup_timeout,
                )
                result.setdefault("protocol_metrics", {})["endpoint_lock"] = lock
                return result
        except EndpointLockTimeout as ex:
            result = _empty_result("err:lock", str(ex))
            result["cell_wall_ms"] = 0.0
            result["protocol_metrics"] = {
                "endpoint_lock": {"status": "timeout", "error": str(ex)[:160]}
            }
            return result
    return _time_method_locked(
        method,
        qtext,
        base_ep,
        reified_ep,
        timeout=timeout,
        warmups=warmups,
        runs=runs,
        update_ep=update_ep,
        read_only=read_only,
        cache_dir=cache_dir,
        cache_metadata=cache_metadata,
        update_chunk_triples=update_chunk_triples,
        orphan_cleanup_timeout=orphan_cleanup_timeout,
    )


# ---------------------------------------------------------------------------
# Append-only checkpoint handling.

def _frozen_manifest(document, kind):
    for record in document["identity"]["manifests"]:
        if record["kind"] == kind:
            return record
    raise freeze_inputs.FreezeError(f"frozen batch lacks the {kind} manifest")


def load_manifest(verify_files=True, frozen_document=None):
    """Return rows from the validator's one-FD snapshot; never reopen it."""
    path = os.path.join(HERE, "workload_manifest.csv")
    if verify_files:
        observed_record = freeze_inputs.validate_manifest(path, "workload", REF)
        if frozen_document is not None and observed_record != _frozen_manifest(
            frozen_document, "workload"
        ):
            raise freeze_inputs.FreezeError(
                "current workload manifest/query bytes differ from the frozen batch"
            )
        return [
            {
                **query["key"],
                "query_file": query["query_file"],
                "query_sha256": query["query_sha256"],
                # These descriptive columns are deliberately not consumed by
                # formal execution; the frozen record retains only semantic keys.
                "bound_policy": "",
                "notes": "",
            }
            for query in observed_record["queries"]
        ]
    try:
        payload = _read_stable_bytes(
            path, "workload manifest", allow_empty=False, limit=16 * 1024 * 1024
        )
        text = payload.decode("utf-8-sig", "strict")
        reader = csv.DictReader(io.StringIO(text, newline=""), strict=True)
        if tuple(reader.fieldnames or ()) != freeze_inputs.MANIFESTS["workload"][
            "columns"
        ]:
            raise RuntimeError("workload manifest schema/header mismatch")
        rows = list(reader)
        if any(None in row or any(value is None for value in row.values()) for row in rows):
            raise RuntimeError("workload manifest has extra or missing fields")
    except (OSError, csv.Error, UnicodeError) as ex:
        raise RuntimeError("workload manifest is not valid UTF-8 CSV") from ex
    return rows


def read_query_verified(row):
    """Read the exact query bytes used by a cell and re-check their manifest hash."""
    relative = row.get("query_file", "")
    try:
        root = Path(REF).resolve(strict=True)
        path = (root / relative).resolve(strict=True)
        path.relative_to(root)
        payload = _read_stable_bytes(
            path, "manifest query", allow_empty=False, limit=16 * 1024 * 1024
        )
    except (OSError, ValueError) as ex:
        raise RuntimeError("manifest query is missing, unstable, or escapes reference/") from ex
    observed = hashlib.sha256(payload).hexdigest()
    if observed != row.get("query_sha256"):
        raise RuntimeError("actual cell query bytes differ from manifest SHA-256")
    try:
        return payload.decode("utf-8", "strict")
    except UnicodeDecodeError as ex:
        raise RuntimeError("manifest query is not UTF-8") from ex


CELL_KEY = (
    "engine",
    "scale",
    "class",
    "template",
    "instance",
    "query_sha256",
    "method",
)
TERMINAL_STATUSES = {"ok", "unsupported", "timeout", "oom", "answer-mismatch", "not-run"}
# Timeouts, resource exhaustion, and a genuinely unsupported engine feature are
# publishable censored outcomes.  Correctness disagreement and missing work are
# terminal for resume purposes but must make formal completion fail non-zero.
FORMAL_PUBLICATION_STATUSES = {"ok", "unsupported", "timeout", "oom"}

LEGACY_COLS = [
    "commit", "engine", "engine_version", "scale", "class", "template", "instance",
    "query_sha256", "method", "implementation", "status", "answers", "median_ms",
    "min_ms", "max_ms", "mean_ms", "sd_ms", "warmups", "runs", "timeout_s",
    "response_bytes", "c_parse_median_ms", "gates", "edges", "derivations",
    "npcs_token_occurrences", "rewrite_ms", "samples_json", "notes",
]
COLS = LEGACY_COLS[:-1] + [
    "c_parse_samples_json", "c_protocol_median_ms", "c_protocol_samples_json",
    "construct_total_ms", "construct_total_samples_json",
    "construct_unattributed_median_ms", "construct_unattributed_samples_json",
    "protocol", "batch_id", "answer_kind", "answer_key_count", "answer_fingerprint",
    "construction_requested", "construction_effective", "circuit_sha256",
    "circuit_cache_path", "circuit_cache_metadata_path",
    "circuit_cache_observation_sha256", "circuit_cache_sidecar_sha256",
    "base_endpoint_sha256", "reified_endpoint_sha256", "update_endpoint_sha256",
    "base_data_identity_sha256", "reified_data_identity_sha256", "update_for",
    "access_mode", "base_data_name", "reified_data_name", "update_canary_sha256",
    "store_instance_sha256", "store_discriminator_sha256", "tool_sha256",
    "java_runtime_sha256",
    "npcs_oplus", "npcs_ominus", "npcs_leaves",
    "run_identity_sha256", "notes"
]

IMPL = {
    "B": "base-select",
    "R": "reification-only",
    "N": "N_clean (NPCS reimplementation)",
    "C": "SPARQLcirc CircuitRewriter",
}


def pack_note(message="", evidence=None, cell_wall_ms=None):
    evidence = evidence or {}
    payload = {
        "protocol": PROTOCOL,
        "message": str(message).replace("\r", " ").replace("\n", " ")[:240],
        "cell_wall_ms": cell_wall_ms,
        **evidence,
    }
    return NOTE_PREFIX + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def unpack_note(row):
    note = row.get("notes") or ""
    metadata = {}
    if note.startswith(NOTE_PREFIX):
        try:
            metadata = json.loads(note[len(NOTE_PREFIX):])
        except (ValueError, TypeError):
            metadata = {}
    for name in (
        "protocol",
        "batch_id",
        "answer_kind",
        "answer_key_count",
        "answer_fingerprint",
        "construction_requested",
        "construction_effective",
        "circuit_sha256",
        "circuit_cache_path",
        "circuit_cache_metadata_path",
        "circuit_cache_observation_sha256",
        "circuit_cache_sidecar_sha256",
        "base_endpoint_sha256",
        "reified_endpoint_sha256",
        "update_endpoint_sha256",
        "base_data_identity_sha256",
        "reified_data_identity_sha256",
        "update_for",
        "access_mode",
        "base_data_name",
        "reified_data_name",
        "update_canary_sha256",
        "store_instance_sha256",
        "store_discriminator_sha256",
        "tool_sha256",
        "java_runtime_sha256",
        "run_identity_sha256",
    ):
        if row.get(name) not in (None, ""):
            metadata[name] = row[name]
    return metadata


def _parse_checkpoint_payload(complete, *, torn=False):
    """Parse already-snapshotted complete physical CSV records strictly."""
    try:
        text = complete.decode("utf-8", "strict")
    except UnicodeDecodeError as ex:
        raise ValueError("R9 timing checkpoint is not valid UTF-8") from ex
    physical = text.splitlines(keepends=True)
    if not physical:
        return [], []

    def parse_line(raw, line_no):
        try:
            parsed = list(csv.reader([raw], strict=True))
        except csv.Error as ex:
            raise ValueError(
                f"R9 timing checkpoint has malformed CSV at line {line_no}"
            ) from ex
        if len(parsed) != 1:
            raise ValueError(
                f"R9 timing checkpoint has a multiline record at line {line_no}"
            )
        return parsed[0]

    header = parse_line(physical[0], 1)
    if not header or len(set(header)) != len(header) or any(not name for name in header):
        raise ValueError("R9 timing checkpoint header is invalid")
    rows = []
    for line_no, raw in enumerate(physical[1:], 2):
        values = parse_line(raw, line_no)
        if len(values) != len(header):
            raise ValueError(
                f"R9 timing checkpoint row {line_no} has {len(values)} fields; "
                f"expected {len(header)}"
            )
        rows.append(dict(zip(header, values)))
    return header, rows


def _parse_checkpoint_snapshot(path, *, allow_torn_tail=True):
    """Strictly parse a stable one-line-per-row CSV snapshot."""
    if not os.path.lexists(path):
        return [], [], False
    payload = _read_stable_bytes(path, "R9 timing checkpoint")
    if not payload:
        return [], [], False
    torn = not payload.endswith((b"\n", b"\r"))
    complete = payload
    if torn:
        if not allow_torn_tail:
            raise ValueError("R9 timing checkpoint has a torn final record")
        boundary = payload.rfind(b"\n")
        complete = payload[: boundary + 1] if boundary >= 0 else b""
    header, rows = _parse_checkpoint_payload(complete, torn=torn)
    return header, rows, torn


def _checkpoint_rows(path):
    """Return strict complete records, ignoring only a torn final physical row."""
    return _parse_checkpoint_snapshot(path, allow_torn_tail=True)[1]


def _repair_checkpoint_tail(path):
    """Durably discard only an incomplete final physical CSV record."""
    if not os.path.lexists(path):
        return
    descriptor = _open_single_link(
        path, os.O_RDWR, "R9 timing checkpoint"
    )
    try:
        before = _validate_opened_single_link(
            path, descriptor, "R9 timing checkpoint"
        )
        payload = _read_open_descriptor(
            path, descriptor, "R9 timing checkpoint"
        )
        if payload.endswith((b"\n", b"\r")):
            return
        # Validate every complete physical record from these exact bytes.  Only
        # the incomplete final suffix may be discarded; malformed middle rows
        # remain fatal and are never "repaired" into apparently valid evidence.
        boundary = payload.rfind(b"\n")
        complete = payload[: boundary + 1] if boundary >= 0 else b""
        _parse_checkpoint_payload(complete, torn=True)
        os.ftruncate(descriptor, boundary + 1 if boundary >= 0 else 0)
        os.fsync(descriptor)
        current = _validate_opened_single_link(
            path, descriptor, "R9 timing checkpoint"
        )
        if (before.st_dev, before.st_ino) != (current.st_dev, current.st_ino):
            raise ValueError("R9 timing checkpoint inode changed during tail repair")
    finally:
        os.close(descriptor)
    _fsync_directory(os.path.dirname(os.path.abspath(path)) or ".")


def _row_key(row):
    try:
        key = tuple(row[name] for name in CELL_KEY)
    except KeyError:
        return None
    return key if all(key) else None


def _cache_matches_row(row, metadata):
    """Validate the external payload and one matching immutable producer record."""
    circuit_path = metadata.get("circuit_cache_path") or metadata.get("circuit_path")
    metadata_path = metadata.get("circuit_cache_metadata_path") or metadata.get(
        "metadata_path"
    )
    circuit_sha = metadata.get("circuit_sha256")
    observation_sha = metadata.get("circuit_cache_observation_sha256")
    if not circuit_path or not metadata_path or not circuit_cache.SHA256.match(
        str(circuit_sha or "")
    ) or not circuit_cache.SHA256.fullmatch(str(observation_sha or "")):
        return False
    try:
        loaded = circuit_cache.load_sidecar(
            metadata_path, circuit_path, circuit_sha
        )
    except (OSError, ValueError, TypeError):
        return False
    candidates = [
        candidate
        for candidate in loaded["observations"]
        if candidate.get("producer_observation_sha256") == observation_sha
    ]
    identity = (
        "commit",
        "batch_id",
        "protocol",
        "query_sha256",
        "engine",
        "engine_version",
        "scale",
        "class",
        "template",
        "instance",
        "base_endpoint_sha256",
        "reified_endpoint_sha256",
        "update_endpoint_sha256",
        "base_data_identity_sha256",
        "reified_data_identity_sha256",
        "update_for",
        "access_mode",
        "base_data_name",
        "reified_data_name",
        "update_canary_sha256",
        "store_instance_sha256",
        "store_discriminator_sha256",
        "tool_sha256",
        "java_runtime_sha256",
        "run_identity_sha256",
        "construction_requested",
        "construction_effective",
    )
    return any(
        candidate.get("circuit_sha256") == circuit_sha
        and all(str(candidate.get(name, "")) == str(row.get(name, "")) for name in identity)
        for candidate in candidates
    )


def _summary_matches(row, samples):
    if not samples:
        return False
    try:
        numeric = [float(value) for value in samples]
    except (TypeError, ValueError):
        return False
    if not all(math.isfinite(value) for value in numeric):
        return False
    expected = {
        "median_ms": round(statistics.median(numeric), 1),
        "min_ms": round(min(numeric), 1),
        "max_ms": round(max(numeric), 1),
        "mean_ms": round(statistics.mean(numeric), 1),
        "sd_ms": round(statistics.stdev(numeric) if len(numeric) > 1 else 0.0, 1),
    }
    try:
        return all(
            abs(float(row.get(name)) - value) <= 1e-9
            for name, value in expected.items()
        )
    except (TypeError, ValueError):
        return False


def _median_matches(value, samples):
    try:
        return abs(float(value) - round(statistics.median(samples), 1)) <= 1e-9
    except (TypeError, ValueError, statistics.StatisticsError):
        return False


def checkpoint_complete(
    row,
    warmups=WARMUPS,
    runs=RUNS,
    timeout=TIMEOUT,
    require_circuit_cache=False,
    expected_identity=None,
    current_commit=COMMIT,
    current_batch_id=None,
):
    status = row.get("status", "")
    if status not in TERMINAL_STATUSES:
        return False
    # Protocol-sensitive completed results are reusable only under the exact
    # repetition/deadline configuration.  Failure rows remain retryable.
    if type(warmups) is not int or type(runs) is not int:
        return False
    if (
        _canonical_uint(row.get("warmups")) != warmups
        or _canonical_uint(row.get("runs")) != runs
    ):
        return False
    if type(timeout) not in (int, float):
        return False
    try:
        expected_timeout = float(timeout)
    except (TypeError, ValueError):
        return False
    if (
        not math.isfinite(expected_timeout)
        or _canonical_float(row.get("timeout_s")) != expected_timeout
    ):
        return False
    metadata = unpack_note(row)
    # The timeout semantics changed from per-socket/per-execution to one hard
    # whole-cell deadline.  Old terminal rows (including timeout) therefore
    # cannot be silently reused under the new protocol.
    if metadata.get("protocol") != PROTOCOL:
        return False
    if not COMMIT_RE.fullmatch(str(current_commit or "")):
        return False
    if row.get("commit") != current_commit or not COMMIT_RE.fullmatch(
        str(row.get("commit") or "")
    ):
        return False
    if not BATCH_ID_RE.fullmatch(str(row.get("query_sha256") or "")):
        return False
    if not BATCH_ID_RE.fullmatch(str(metadata.get("batch_id") or "")):
        return False
    if not BATCH_ID_RE.fullmatch(str(current_batch_id or "")):
        return False
    if metadata.get("batch_id") != current_batch_id:
        return False
    cleanup = metadata.get("outside_cell_cleanup")
    if isinstance(cleanup, dict) and any(
        value == "failed" or str(value).startswith("skipped-")
        for name, value in cleanup.items()
        if name.endswith("_status")
    ):
        # A primary timeout remains visible in the raw checkpoint, but it is
        # not a reusable/publishable terminal observation until endpoint
        # hygiene itself has succeeded under its independent hard deadline.
        return False
    identity_fields = (
        "base_endpoint_sha256",
        "reified_endpoint_sha256",
        "update_endpoint_sha256",
        "base_data_identity_sha256",
        "reified_data_identity_sha256",
        "update_for",
        "access_mode",
        "base_data_name",
        "reified_data_name",
        "update_canary_sha256",
        "store_instance_sha256",
        "store_discriminator_sha256",
        "tool_sha256",
        "java_runtime_sha256",
        "run_identity_sha256",
    )
    if not all(metadata.get(name) not in (None, "") for name in identity_fields):
        return False
    if expected_identity is not None:
        for name in (
            "commit",
            "batch_id",
            "protocol",
            "query_sha256",
            "engine",
            "engine_version",
            "scale",
            *identity_fields,
        ):
            observed = row.get(name) if name in row and row.get(name) != "" else metadata.get(name)
            if str(observed or "") != str(expected_identity.get(name, "")):
                return False
    try:
        cell_wall_ms = float(metadata.get("cell_wall_ms"))
        if (
            not math.isfinite(cell_wall_ms)
            or cell_wall_ms < 0
            or cell_wall_ms > timeout * 1000.0
        ):
            return False
    except (TypeError, ValueError):
        return False
    if status != "ok":
        evidence = metadata.get("status_evidence")
        if (
            metadata.get("status") != status
            or not isinstance(evidence, dict)
            or not evidence.get("kind")
            or evidence.get("kind") == "unspecified"
            or not evidence.get("message")
        ):
            return False
        try:
            return not json.loads(row.get("samples_json") or "[]")
        except (TypeError, ValueError, json.JSONDecodeError):
            return False
    try:
        samples = json.loads(row.get("samples_json") or "")
        if len(samples) != runs or any(
            not math.isfinite(float(value)) or float(value) < 0
            for value in samples
        ):
            return False
        # Invalidates legacy rows that slipped past a 300s per-socket timeout.
        if any(float(value) > timeout * 1000.0 for value in samples):
            return False
        if sum(float(value) for value in samples) > timeout * 1000.0:
            return False
    except (TypeError, ValueError, json.JSONDecodeError):
        return False
    if not _summary_matches(row, samples):
        return False
    if row.get("rewrite_ms") in (None, ""):
        return False
    try:
        rewrite_ms = float(row["rewrite_ms"])
        if (
            not math.isfinite(rewrite_ms)
            or rewrite_ms < 0
            or rewrite_ms > timeout * 1000.0
        ):
            return False
    except (TypeError, ValueError):
        return False
    if row.get("method") == "C":
        if not metadata.get("construction_requested") or not metadata.get(
            "construction_effective"
        ):
            return False
        try:
            parse_samples = metadata["c_parse_samples"]
            protocol_cost_samples = metadata["c_protocol_samples"]
            total_samples = metadata["construct_total_samples"]
            unattributed_samples = metadata["construct_unattributed_samples"]
            if (
                len(parse_samples) != runs
                or len(protocol_cost_samples) != runs
                or len(total_samples) != runs
                or len(unattributed_samples) != runs
            ):
                return False
            if any(
                not math.isfinite(float(value)) or float(value) < 0
                for group in (
                    parse_samples,
                    protocol_cost_samples,
                    total_samples,
                    unattributed_samples,
                )
                for value in group
            ):
                return False
            if any(
                float(total) + 0.001
                < (float(network) + float(client) + float(protocol_cost))
                or abs(
                    float(total)
                    - (
                        float(network)
                        + float(client)
                        + float(protocol_cost)
                        + float(unattributed)
                    )
                )
                > 0.01
                for network, client, protocol_cost, total, unattributed in zip(
                    samples,
                    parse_samples,
                    protocol_cost_samples,
                    total_samples,
                    unattributed_samples,
                )
            ):
                return False
        except (KeyError, TypeError, ValueError):
            return False
        if not (
            _median_matches(row.get("c_parse_median_ms"), parse_samples)
            and _median_matches(
                row.get("c_protocol_median_ms"), protocol_cost_samples
            )
            and _median_matches(row.get("construct_total_ms"), total_samples)
            and _median_matches(
                row.get("construct_unattributed_median_ms"),
                unattributed_samples,
            )
        ):
            return False
        if not circuit_cache.SHA256.fullmatch(
            str(metadata.get("circuit_sha256") or "")
        ):
            return False
        if require_circuit_cache and not _cache_matches_row(row, metadata):
            return False
    # Every successful new-protocol row must carry full-answer evidence.  This
    # also makes legacy count-only cells rerunnable without rewriting the CSV.
    return bool(metadata.get("answer_kind") and metadata.get("answer_fingerprint"))


def build_expected_identities(
    manifest, engines, scales, methods, registry, *, batch_id
):
    """Build the single deterministic timing-slot schedule used by all gates."""
    expected = {}
    for engine in engines:
        config = registry.get(engine)
        if config is None:
            continue
        for scale in scales:
            endpoints = config.get(scale)
            if endpoints is None:
                continue
            for manifest_row in manifest:
                if manifest_row["scale"] != scale:
                    continue
                identity = cell_identity(
                    engine,
                    scale,
                    manifest_row["query_sha256"],
                    config,
                    endpoints,
                    batch_id=batch_id,
                )
                for method in methods:
                    key = (
                        engine,
                        scale,
                        manifest_row["class"],
                        manifest_row["template"],
                        manifest_row["instance"],
                        manifest_row["query_sha256"],
                        method,
                    )
                    if key in expected:
                        raise ValueError("formal timing slot schedule contains a duplicate")
                    expected[key] = identity
    return expected


def validate_checkpoint_prefix(rows, expected_identities, *, require_full=False):
    """Require every physical timing row to be the exact planned slot prefix."""
    planned = list(expected_identities)
    if len(rows) > len(planned):
        raise ValueError("R9 timing checkpoint has more rows than planned slots")
    for index, row in enumerate(rows):
        observed = _row_key(row)
        if observed != planned[index]:
            raise ValueError(
                "R9 timing checkpoint is sparse, reordered, or duplicated at row %d"
                % (index + 1)
            )
        expected = expected_identities[observed]
        for name in IDENTITY_FIELDS:
            value = row.get(name) if row.get(name) != "" else unpack_note(row).get(name)
            if str(value or "") != str(expected.get(name, "")):
                raise ValueError(
                    "R9 timing checkpoint slot %d has a noncanonical %s identity"
                    % (index + 1, name)
                )
    if require_full and len(rows) != len(planned):
        raise RuntimeError(
            "R9 timing checkpoint is only a prefix: %d/%d slots"
            % (len(rows), len(planned))
        )
    return tuple(planned[: len(rows)])


def load_done(
    out,
    warmups=WARMUPS,
    runs=RUNS,
    timeout=TIMEOUT,
    require_circuit_cache=False,
    expected_identities=None,
    current_commit=COMMIT,
    current_batch_id=None,
):
    rows = list(_checkpoint_rows(out) or ())
    if expected_identities is not None:
        validate_checkpoint_prefix(rows, expected_identities)
        done = set()
        for row in rows:
            key = _row_key(row)
            if not checkpoint_complete(
                row,
                warmups=warmups,
                runs=runs,
                timeout=timeout,
                require_circuit_cache=require_circuit_cache,
                expected_identity=expected_identities[key],
                current_commit=current_commit,
                current_batch_id=current_batch_id,
            ):
                raise ValueError(
                    "formal R9 checkpoint prefix contains a non-reusable slot; "
                    "start a new output instead of appending a duplicate"
                )
            done.add(key)
        return done
    latest = {}
    for row in rows:
        key = _row_key(row)
        if key is not None:
            latest[key] = row
    return {
        key
        for key, row in latest.items()
        if (expected_identities is None or key in expected_identities)
        and checkpoint_complete(
            row,
            warmups=warmups,
            runs=runs,
            timeout=timeout,
            require_circuit_cache=require_circuit_cache,
            expected_identity=(expected_identities or {}).get(key),
            current_commit=current_commit,
            current_batch_id=current_batch_id,
        )
    }


def _open_writer(path):
    if os.path.lexists(path):
        _repair_checkpoint_tail(path)
    new = not os.path.lexists(path) or os.lstat(path).st_size == 0
    if new:
        fieldnames = COLS
    else:
        fieldnames, rows, torn = _parse_checkpoint_snapshot(
            path, allow_torn_tail=False
        )
        if torn:  # defensive; allow_torn_tail=False already rejects it
            raise ValueError("R9 timing checkpoint tail repair failed")
        missing = set(LEGACY_COLS) - set(fieldnames)
        if missing:
            raise ValueError(f"checkpoint is missing required columns: {sorted(missing)}")
        additions = [name for name in COLS if name not in fieldnames]
        if additions:
            # Upgrade legacy checkpoints atomically. Existing rows retain
            # their values and receive empty cells for v5/cache columns; torn
            # trailing records remain ignored by _checkpoint_rows.
            upgraded_fields = fieldnames + additions
            directory = os.path.dirname(os.path.abspath(path)) or "."
            tmp = tempfile.NamedTemporaryFile(
                "w",
                newline="",
                prefix=".pcm-schema-upgrade-",
                suffix=".tmp",
                dir=directory,
                delete=False,
            )
            try:
                writer = csv.DictWriter(
                    tmp, fieldnames=upgraded_fields, extrasaction="ignore"
                )
                writer.writeheader()
                writer.writerows(rows)
                tmp.flush()
                os.fsync(tmp.fileno())
                tmp.close()
                os.replace(tmp.name, path)
                _fsync_directory(directory)
                descriptor = _open_single_link(
                    path, os.O_RDONLY, "upgraded R9 timing checkpoint"
                )
                os.close(descriptor)
            finally:
                if not tmp.closed:
                    tmp.close()
                try:
                    os.unlink(tmp.name)
                except FileNotFoundError:
                    pass
            fieldnames = upgraded_fields
    existed = os.path.lexists(path)
    descriptor = _open_single_link(
        path,
        os.O_WRONLY | os.O_APPEND | os.O_CREAT,
        "R9 timing checkpoint",
    )
    fh = os.fdopen(descriptor, "a", newline="", encoding="utf-8", closefd=True)
    writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
    if new:
        writer.writeheader()
        fh.flush()
        os.fsync(fh.fileno())
        _fsync_directory(os.path.dirname(os.path.abspath(path)) or ".")
    elif not existed:
        _fsync_directory(os.path.dirname(os.path.abspath(path)) or ".")
    return fh, writer


def _completion_path(path):
    return os.path.abspath(path) + ".complete.json"


def _strict_timing_snapshot(path):
    """Return CSV bytes and strict rows derived from that exact byte snapshot."""
    payload = _read_stable_bytes(path, "R9 timing checkpoint", allow_empty=False)
    if not payload.endswith((b"\n", b"\r")):
        raise ValueError("completed R9 timing checkpoint has a torn final record")
    try:
        text = payload.decode("utf-8", "strict")
    except UnicodeDecodeError as ex:
        raise ValueError("R9 timing checkpoint is not valid UTF-8") from ex
    physical = text.splitlines(keepends=True)
    parsed = []
    for line_no, raw in enumerate(physical, 1):
        try:
            records = list(csv.reader([raw], strict=True))
        except csv.Error as ex:
            raise ValueError(
                f"R9 timing checkpoint has malformed CSV at line {line_no}"
            ) from ex
        if len(records) != 1:
            raise ValueError("R9 timing checkpoint contains a multiline record")
        parsed.append(records[0])
    if not parsed or parsed[0] != COLS:
        raise ValueError("completed R9 timing checkpoint schema is not protocol v7")
    rows = []
    for line_no, values in enumerate(parsed[1:], 2):
        if len(values) != len(COLS):
            raise ValueError(
                f"R9 timing checkpoint row {line_no} has the wrong field count"
            )
        rows.append(dict(zip(COLS, values)))
    return payload, rows


def canonical_circuit_gate(rows, *, commit, batch_id):
    """Require cross-engine C agreement for the same scale/query dataset cell."""
    by_query = collections.defaultdict(lambda: {"hashes": set(), "engines": set()})
    latest = {}
    for row in rows:
        key = _row_key(row)
        if key is not None:
            latest[key] = row
    for row in latest.values():
        if (
            row.get("commit") != commit
            or row.get("batch_id") != batch_id
            or row.get("protocol") != PROTOCOL
            or row.get("method") != "C"
            or row.get("status") != "ok"
        ):
            continue
        digest = row.get("circuit_sha256", "")
        if not circuit_cache.SHA256.fullmatch(str(digest)):
            raise ValueError("successful C row lacks a canonical circuit SHA-256")
        query_sha = row.get("query_sha256", "")
        scale = row.get("scale", "")
        if not scale or not BATCH_ID_RE.fullmatch(str(query_sha)):
            raise ValueError("successful C row lacks a scale/query identity")
        by_query[(scale, query_sha)]["hashes"].add(digest)
        by_query[(scale, query_sha)]["engines"].add(row.get("engine", ""))
    canonical = {}
    for (scale, query_sha), observed in sorted(by_query.items()):
        if len(observed["hashes"]) != 1:
            raise ValueError(
                "cross-engine canonical circuit mismatch for "
                + scale
                + "/"
                + query_sha
            )
        canonical[scale + "/" + query_sha] = {
            "scale": scale,
            "query_sha256": query_sha,
            "circuit_sha256": next(iter(observed["hashes"])),
            "engines": sorted(observed["engines"]),
        }
    return canonical


def _sealed_json(document):
    body = dict(document)
    body.pop("document_sha256", None)
    digest = _canonical_digest(body)
    body["document_sha256"] = digest
    return body


_COMMON_COMPLETION_FIELDS = {
    "schema",
    "protocol",
    "commit",
    "batch_id",
    "profile",
    "profile_sha256",
    "expected_cells",
    "expected_keys",
    "expected_keys_sha256",
    "expected_identities",
    "expected_identities_sha256",
    "completed_cells",
    "csv_sha256",
    "csv_bytes",
    "csv_rows",
    "document_sha256",
}
_TIMING_PROFILE_FIELDS = {
    "engines",
    "scales",
    "methods",
    "classes",
    "require_circuit_cache",
    "warmups",
    "runs",
    "timeout_s",
    "update_chunk_triples",
    "orphan_cleanup_timeout_s",
}
_PARITY_PROFILE_FIELDS = {
    "engines",
    "scales",
    "classes",
    "cap",
    "timeout_s",
    "update_chunk_triples",
    "orphan_cleanup_timeout_s",
}


def _exact_keys(value, expected, label):
    if type(value) is not dict or set(value) != set(expected):
        raise ValueError(f"{label} field schema mismatch")


def _strict_string_list(value, label):
    if (
        type(value) is not list
        or not value
        or any(type(item) is not str or not item for item in value)
        or len(set(value)) != len(value)
    ):
        raise ValueError(f"{label} must be a nonempty unique string list")


def _validate_profile(profile, schema, label):
    fields = (
        _TIMING_PROFILE_FIELDS
        if schema == "r9-timing-completion-v1"
        else _PARITY_PROFILE_FIELDS
    )
    _exact_keys(profile, fields, f"{label} profile")
    for name in ("engines", "scales", "classes"):
        _strict_string_list(profile[name], f"{label} profile {name}")
    if schema == "r9-timing-completion-v1":
        _strict_string_list(profile["methods"], f"{label} profile methods")
        if type(profile["require_circuit_cache"]) is not bool:
            raise ValueError(f"{label} profile cache flag has the wrong type")
        integer_fields = ("warmups", "runs", "update_chunk_triples")
        positive_fields = ("runs", "update_chunk_triples")
    else:
        integer_fields = ("cap", "update_chunk_triples")
        positive_fields = integer_fields
    for name in integer_fields:
        if type(profile[name]) is not int or profile[name] < 0:
            raise ValueError(f"{label} profile {name} is not an exact integer")
    if any(profile[name] <= 0 for name in positive_fields):
        raise ValueError(f"{label} profile counts must be positive")
    for name in ("timeout_s", "orphan_cleanup_timeout_s"):
        if (
            type(profile[name]) is not float
            or not math.isfinite(profile[name])
            or profile[name] <= 0
        ):
            raise ValueError(f"{label} profile {name} is not a positive exact float")


def _validate_expected_identity_records(document, *, key_length, label):
    keys = document["expected_keys"]
    records = document["expected_identities"]
    if (
        type(keys) is not list
        or type(records) is not list
        or len(keys) != document["expected_cells"]
        or len(records) != document["expected_cells"]
    ):
        raise ValueError(f"{label} expected-slot coverage mismatch")
    observed_keys = []
    for index, record in enumerate(records):
        _exact_keys(record, {"key", "identity"}, f"{label} expected identity")
        key = record["key"]
        identity = record["identity"]
        if (
            type(key) is not list
            or len(key) != key_length
            or any(type(item) is not str or not item for item in key)
        ):
            raise ValueError(f"{label} expected key {index} is invalid")
        _exact_keys(identity, IDENTITY_FIELDS, f"{label} expected identity {index}")
        if any(type(identity[name]) is not str or not identity[name] for name in IDENTITY_FIELDS):
            raise ValueError(f"{label} expected identity {index} is incomplete")
        if (
            identity["engine"] != key[0]
            or identity["scale"] != key[1]
            or identity["query_sha256"] != key[5]
            or key[0] not in document["profile"]["engines"]
            or key[1] not in document["profile"]["scales"]
            or key[2] not in document["profile"]["classes"]
            or (
                key_length == 7
                and key[6] not in document["profile"]["methods"]
            )
            or identity["protocol"] != PROTOCOL
            or identity["commit"] != document["commit"]
            or identity["batch_id"] != document["batch_id"]
        ):
            raise ValueError(f"{label} expected identity {index} disagrees with its key")
        for name in (
            "query_sha256",
            "base_endpoint_sha256",
            "reified_endpoint_sha256",
            "update_endpoint_sha256",
            "base_data_identity_sha256",
            "reified_data_identity_sha256",
            "update_canary_sha256",
            "store_instance_sha256",
            "store_discriminator_sha256",
            "tool_sha256",
            "java_runtime_sha256",
            "run_identity_sha256",
        ):
            if not BATCH_ID_RE.fullmatch(identity[name]):
                raise ValueError(f"{label} expected identity {index} has invalid {name}")
        observed_keys.append(key)
    if keys != observed_keys or len({tuple(key) for key in keys}) != len(keys):
        raise ValueError(f"{label} expected keys are duplicated or reordered")
    if document["expected_keys_sha256"] != _canonical_digest(keys):
        raise ValueError(f"{label} expected-key digest mismatch")
    if document["expected_identities_sha256"] != _canonical_digest(records):
        raise ValueError(f"{label} expected-identity digest mismatch")


def _validate_completion_document(document, *, expected_schema, label):
    if expected_schema not in (
        "r9-timing-completion-v1",
        "r9-parity-completion-v1",
    ):
        raise ValueError(f"{label} requested an unknown completion schema")
    extras = (
        {"terminal_status_counts", "canonical_circuits"}
        if expected_schema == "r9-timing-completion-v1"
        else set()
    )
    _exact_keys(document, _COMMON_COMPLETION_FIELDS | extras, label)
    if type(document["schema"]) is not str or document["schema"] != expected_schema:
        raise ValueError(f"{label} schema mismatch")
    if (
        type(document["protocol"]) is not str
        or document["protocol"] != PROTOCOL
        or type(document["commit"]) is not str
        or not COMMIT_RE.fullmatch(document["commit"])
        or type(document["batch_id"]) is not str
        or not BATCH_ID_RE.fullmatch(document["batch_id"])
    ):
        raise ValueError(f"{label} has invalid protocol/commit/batch identity")
    for name in (
        "profile_sha256",
        "expected_keys_sha256",
        "expected_identities_sha256",
        "csv_sha256",
        "document_sha256",
    ):
        if type(document[name]) is not str or not BATCH_ID_RE.fullmatch(document[name]):
            raise ValueError(f"{label} has an invalid {name}")
    for name in ("expected_cells", "completed_cells", "csv_bytes", "csv_rows"):
        if type(document[name]) is not int or document[name] < 0:
            raise ValueError(f"{label} has an invalid exact {name}")
    if (
        document["expected_cells"] <= 0
        or document["completed_cells"] != document["expected_cells"]
        or document["csv_rows"] != document["expected_cells"]
        or document["csv_bytes"] <= 0
    ):
        raise ValueError(f"{label} does not prove complete nonempty coverage")
    _validate_profile(document["profile"], expected_schema, label)
    if document["profile_sha256"] != _canonical_digest(document["profile"]):
        raise ValueError(f"{label} run-profile integrity mismatch")
    _validate_expected_identity_records(
        document,
        key_length=7 if expected_schema == "r9-timing-completion-v1" else 6,
        label=label,
    )
    if expected_schema == "r9-timing-completion-v1":
        counts = document["terminal_status_counts"]
        if (
            type(counts) is not dict
            or not counts
            or any(
                type(name) is not str
                or name not in FORMAL_PUBLICATION_STATUSES
                or type(count) is not int
                or count <= 0
                for name, count in counts.items()
            )
            or sum(counts.values()) != document["expected_cells"]
        ):
            raise ValueError(f"{label} terminal status counts are invalid")
        circuits = document["canonical_circuits"]
        if type(circuits) is not dict:
            raise ValueError(f"{label} canonical circuits are invalid")
        for key, value in circuits.items():
            _exact_keys(
                value,
                {"scale", "query_sha256", "circuit_sha256", "engines"},
                f"{label} canonical circuit",
            )
            _strict_string_list(value["engines"], f"{label} circuit engines")
            if (
                type(key) is not str
                or key != value["scale"] + "/" + value["query_sha256"]
                or type(value["scale"]) is not str
                or not BATCH_ID_RE.fullmatch(str(value["query_sha256"]))
                or not BATCH_ID_RE.fullmatch(str(value["circuit_sha256"]))
            ):
                raise ValueError(f"{label} canonical circuit identity is invalid")


def completion_expected_identities(document):
    """Rehydrate the ordered expected-slot mapping from a verified proof."""
    return {
        tuple(record["key"]): dict(record["identity"])
        for record in document["expected_identities"]
    }


def verify_completion_sidecar(
    csv_path,
    *,
    expected_schema,
    label,
    csv_payload=None,
    csv_rows=None,
):
    """Validate a completion proof and its exact CSV byte snapshot.

    Consumers (summaries/plots) use this gate rather than trusting a checkpoint
    merely because its rows look complete.  The proof seals the profile,
    expected-cell coverage, and the exact bytes that were audited.
    """
    completion = _completion_path(csv_path)
    try:
        encoded = _read_stable_bytes(
            completion,
            label,
            allow_empty=False,
            limit=16 * 1024 * 1024,
        )
        document = json.loads(encoded.decode("utf-8", "strict"))
    except (UnicodeError, json.JSONDecodeError) as ex:
        raise ValueError(f"{label} is invalid JSON") from ex
    if type(document) is not dict:
        raise ValueError(f"{label} schema mismatch")
    try:
        canonical = _canonical_json_bytes(document)
    except (TypeError, ValueError) as ex:
        raise ValueError(f"{label} contains a noncanonical JSON value") from ex
    if encoded != canonical:
        raise ValueError(f"{label} bytes are not canonical JSON")
    _validate_completion_document(
        document, expected_schema=expected_schema, label=label
    )
    claimed = document.get("document_sha256")
    body = dict(document)
    body.pop("document_sha256", None)
    if _canonical_digest(body) != claimed:
        raise ValueError(f"{label} integrity mismatch")
    if csv_payload is None:
        csv_payload = _read_stable_bytes(
            csv_path, label.replace("completion sidecar", "CSV"), allow_empty=False
        )
    if (
        hashlib.sha256(csv_payload).hexdigest() != document.get("csv_sha256")
        or len(csv_payload) != document.get("csv_bytes")
    ):
        raise ValueError(f"{label} does not bind the current CSV bytes")
    if (
        csv_rows is not None
        and (type(csv_rows) is not int or document["csv_rows"] != csv_rows)
    ):
        raise ValueError(f"{label} CSV-row coverage mismatch")
    return document


def _atomic_publish_json(path, document, label):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if os.path.lexists(path):
        _read_stable_bytes(path, label, allow_empty=False, limit=16 * 1024 * 1024)
    encoded = _canonical_json_bytes(document)
    temporary = tempfile.NamedTemporaryFile(
        prefix="." + path.name + ".",
        suffix=".tmp",
        dir=str(path.parent),
        delete=False,
    )
    temporary_path = Path(temporary.name)
    try:
        with temporary:
            temporary.write(encoded)
            temporary.flush()
            os.fsync(temporary.fileno())
            if os.fstat(temporary.fileno()).st_nlink != 1:
                raise ValueError(f"{label} temporary gained a hardlink")
        if os.path.lexists(path):
            descriptor = _open_single_link(path, os.O_RDONLY, label)
            os.close(descriptor)
        os.replace(temporary_path, path)
        descriptor = _open_single_link(path, os.O_RDONLY, label)
        try:
            current = os.fstat(descriptor)
            if current.st_size != len(encoded):
                raise ValueError(f"{label} publication size mismatch")
        finally:
            os.close(descriptor)
        _fsync_directory(path.parent)
    finally:
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass


def _atomic_publish_bytes(path, payload, label):
    """Durably replace one single-link artifact with caller-rendered bytes."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if os.path.lexists(path):
        _read_stable_bytes(path, label)
    temporary = tempfile.NamedTemporaryFile(
        prefix="." + path.name + ".",
        suffix=".tmp",
        dir=str(path.parent),
        delete=False,
    )
    temporary_path = Path(temporary.name)
    try:
        with temporary:
            temporary.write(payload)
            temporary.flush()
            os.fsync(temporary.fileno())
            if os.fstat(temporary.fileno()).st_nlink != 1:
                raise ValueError(f"{label} temporary gained a hardlink")
        if os.path.lexists(path):
            descriptor = _open_single_link(path, os.O_RDONLY, label)
            os.close(descriptor)
        os.replace(temporary_path, path)
        descriptor = _open_single_link(path, os.O_RDONLY, label)
        try:
            if os.fstat(descriptor).st_size != len(payload):
                raise ValueError(f"{label} publication size mismatch")
        finally:
            os.close(descriptor)
        _fsync_directory(path.parent)
    finally:
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass


def finalize_timing_completion(
    path,
    *,
    expected_identities,
    commit,
    batch_id,
    profile,
    engines,
    scales,
    methods,
    classes,
    require_circuit_cache,
):
    """Fail closed on partial cells, then publish an exact CSV-bound proof."""
    payload, rows = _strict_timing_snapshot(path)
    if not expected_identities:
        raise RuntimeError("formal R9 timing shard has zero expected cells")
    validate_checkpoint_prefix(rows, expected_identities, require_full=True)
    latest = {_row_key(row): row for row in rows}
    for key, row in latest.items():
        metadata = unpack_note(row)
        if (
            row.get("commit") != commit
            or row.get("protocol") != PROTOCOL
            or row.get("batch_id") != batch_id
            or metadata.get("protocol") != PROTOCOL
            or metadata.get("batch_id") != batch_id
        ):
            raise ValueError(
                "formal R9 timing checkpoint mixes commit/protocol/batch identities"
            )
    missing = []
    for key, identity in expected_identities.items():
        row = latest.get(key)
        if row is None or not checkpoint_complete(
            row,
            warmups=profile["warmups"],
            runs=profile["runs"],
            timeout=profile["timeout_s"],
            require_circuit_cache=require_circuit_cache,
            expected_identity=identity,
            current_commit=commit,
            current_batch_id=batch_id,
        ):
            missing.append(key)
    if missing:
        raise RuntimeError(
            "formal R9 timing shard is incomplete: %d/%d expected cells missing "
            "or nonterminal" % (len(missing), len(expected_identities))
        )
    fatal = {
        key: latest[key].get("status", "")
        for key in expected_identities
        if latest[key].get("status") not in FORMAL_PUBLICATION_STATUSES
    }
    if fatal:
        raise RuntimeError(
            "formal R9 timing shard contains %d correctness/missing terminal outcome(s)"
            % len(fatal)
        )
    canonical = canonical_circuit_gate(rows, commit=commit, batch_id=batch_id)
    run_profile = {
        "engines": list(engines),
        "scales": list(scales),
        "methods": list(methods),
        "classes": list(classes),
        "require_circuit_cache": bool(require_circuit_cache),
        **profile,
    }
    identity_records = _ordered_identity_records(expected_identities)
    expected_keys = [record["key"] for record in identity_records]
    document = _sealed_json(
        {
            "schema": "r9-timing-completion-v1",
            "protocol": PROTOCOL,
            "commit": commit,
            "batch_id": batch_id,
            "profile": run_profile,
            "profile_sha256": _canonical_digest(run_profile),
            "expected_cells": len(expected_keys),
            "expected_keys": expected_keys,
            "expected_keys_sha256": _canonical_digest(expected_keys),
            "expected_identities": identity_records,
            "expected_identities_sha256": _canonical_digest(identity_records),
            "completed_cells": len(expected_keys),
            "terminal_status_counts": dict(
                sorted(collections.Counter(latest[key]["status"] for key in latest).items())
            ),
            "csv_sha256": hashlib.sha256(payload).hexdigest(),
            "csv_bytes": len(payload),
            "csv_rows": len(rows),
            "canonical_circuits": canonical,
        }
    )
    _validate_completion_document(
        document,
        expected_schema="r9-timing-completion-v1",
        label="R9 timing completion sidecar",
    )
    completion = _completion_path(path)
    if os.path.lexists(completion):
        existing = verify_completion_sidecar(
            path,
            expected_schema="r9-timing-completion-v1",
            label="R9 timing completion sidecar",
            csv_payload=payload,
            csv_rows=len(rows),
        )
        if _canonical_json_bytes(existing) != _canonical_json_bytes(document):
            raise ValueError(
                "R9 timing completion sidecar does not bind the current CSV/profile"
            )
    else:
        _atomic_publish_json(
            completion, document, "R9 timing completion sidecar"
        )
        verify_completion_sidecar(
            path,
            expected_schema="r9-timing-completion-v1",
            label="R9 timing completion sidecar",
            csv_payload=payload,
            csv_rows=len(rows),
        )
    return document


def _run_matrix(
    args,
    *,
    engines,
    scales,
    methods,
    registry,
    batch_id,
    cache_dir,
    frozen_document,
):
    classes = set(
        getattr(args, "selected_classes", ())
        or filter(None, args.classes.split(","))
    )
    profile = getattr(
        args,
        "run_profile",
        {
            "warmups": FORMAL_WARMUPS,
            "runs": FORMAL_RUNS,
            "timeout_s": FORMAL_TIMEOUT,
            "update_chunk_triples": FORMAL_UPDATE_CHUNK_TRIPLES,
            "orphan_cleanup_timeout_s": FORMAL_ORPHAN_CLEANUP_TIMEOUT,
        },
    )
    manifest = [
        row
        for row in load_manifest(frozen_document=frozen_document)
        if row["class"] in classes
    ]
    if not args.exploratory:
        missing_dimensions = [
            (scale, cls)
            for scale in scales
            for cls in getattr(args, "selected_classes", FORMAL_CLASSES)
            if not any(
                row["scale"] == scale and row["class"] == cls
                for row in manifest
            )
        ]
        if missing_dimensions:
            raise RuntimeError(
                "formal workload manifest lacks %d selected scale/class dimension(s)"
                % len(missing_dimensions)
            )
    for engine in engines:
        cfg = registry.get(engine)
        if not cfg:
            continue
        for scale in scales:
            endpoints = cfg.get(scale)
            if endpoints is None:
                continue
            validate_endpoint_registration(
                cfg,
                endpoints,
                require_update=(
                    "C" in methods
                    and not endpoints.get(
                        "read_only", cfg.get("read_only", False)
                    )
                ),
            )
    expected_identities = build_expected_identities(
        manifest,
        engines,
        scales,
        methods,
        registry,
        batch_id=batch_id,
    )
    done = load_done(
        args.out,
        warmups=profile["warmups"],
        runs=profile["runs"],
        timeout=profile["timeout_s"],
        require_circuit_cache=bool(cache_dir),
        expected_identities=expected_identities,
        current_commit=COMMIT,
        current_batch_id=batch_id,
    )
    if not args.exploratory and os.path.lexists(_completion_path(args.out)):
        completion = finalize_timing_completion(
            args.out,
            expected_identities=expected_identities,
            commit=COMMIT,
            batch_id=batch_id,
            profile=profile,
            engines=engines,
            scales=scales,
            methods=methods,
            classes=getattr(args, "selected_classes", FORMAL_CLASSES),
            require_circuit_cache=bool(cache_dir),
        )
        print(f"\nverified complete {args.out}")
        return completion
    fh, writer = _open_writer(args.out)
    try:
        for engine in engines:
            cfg = registry.get(engine)
            if not cfg:
                cfg = {"version": f"{engine} (not registered)"}
                print(f"[{engine}] not registered; writing explicit not-run cells")
            for scale in scales:
                endpoints = cfg.get(scale)
                for manifest_row in (row for row in manifest if row["scale"] == scale):
                    cls = manifest_row["class"]
                    template = manifest_row["template"]
                    instance = manifest_row["instance"]
                    query_sha = manifest_row["query_sha256"]
                    qtext = read_query_verified(manifest_row)
                    for method in methods:
                        key = (
                            engine, scale, cls, template, instance, query_sha, method
                        )
                        if key in done:
                            continue
                        if endpoints is None:
                            result = _empty_result(
                                "not-run",
                                f"{scale} endpoints not registered",
                                status_evidence_kind="unregistered-store",
                            )
                        else:
                            identity = expected_identities[key]
                            cache_metadata = None
                            if method == "C" and cache_dir:
                                cache_metadata = {
                                    "commit": COMMIT,
                                    "batch_id": batch_id,
                                    "query_sha256": query_sha,
                                    "engine": engine,
                                    "scale": scale,
                                    "class": cls,
                                    "template": template,
                                    "instance": instance,
                                    "protocol": PROTOCOL,
                                    **identity,
                                }
                            result = time_method(
                                method,
                                qtext,
                                endpoints["base"],
                                endpoints["reified"],
                                update_ep=endpoints.get("update"),
                                read_only=endpoints.get(
                                    "read_only", cfg.get("read_only", False)
                                ),
                                cache_dir=cache_dir if method == "C" else None,
                                cache_metadata=cache_metadata,
                                lock_identity=identity.get("store_instance_sha256"),
                                timeout=profile["timeout_s"],
                                warmups=profile["warmups"],
                                runs=profile["runs"],
                                update_chunk_triples=profile[
                                    "update_chunk_triples"
                                ],
                                orphan_cleanup_timeout=profile[
                                    "orphan_cleanup_timeout_s"
                                ],
                            )
                        summary = stat(result["samples"]) if result["samples"] else None
                        parse_median = (
                            statistics.median(result["c_parse"])
                            if result["c_parse"]
                            else None
                        )
                        protocol_median = (
                            statistics.median(result["c_protocol"])
                            if result["c_protocol"]
                            else None
                        )
                        total_median = (
                            statistics.median(result["construct_total"])
                            if result["construct_total"]
                            else None
                        )
                        unattributed_median = (
                            statistics.median(result["construct_unattributed"])
                            if result["construct_unattributed"]
                            else None
                        )
                        evidence = result.get("evidence") or {}
                        identity = expected_identities.get(key, {})
                        protocol_samples = (
                            (result.get("protocol_metrics") or {}).get(
                                "c_protocol_samples", []
                            )
                        )
                        construction = protocol_samples[0] if protocol_samples else {}
                        cache = result.get("cache") or {}
                        note_metadata = {
                            **evidence,
                            "c_parse_samples": result["c_parse"],
                            "c_protocol_samples": result["c_protocol"],
                            "construct_total_samples": result["construct_total"],
                            "construct_unattributed_samples": result[
                                "construct_unattributed"
                            ],
                            "status": result["status"],
                            "status_evidence": result.get("status_evidence"),
                            **(result.get("protocol_metrics") or {}),
                            "construction_requested": construction.get(
                                "requested_mode"
                            ),
                            "construction_effective": construction.get(
                                "effective_mode"
                            ),
                            **cache,
                            **identity,
                        }
                        record = {
                            "commit": COMMIT,
                            "engine": engine,
                            "engine_version": (
                                endpoints.get("engine_version")
                                if endpoints is not None
                                else cfg["version"]
                            ),
                            "scale": scale,
                            "class": cls,
                            "template": template,
                            "instance": instance,
                            "query_sha256": query_sha,
                            "method": method,
                            "implementation": IMPL[method],
                            "status": result["status"],
                            "answers": result["answers"],
                            "median_ms": round(summary["median"], 1) if summary else None,
                            "min_ms": round(summary["min"], 1) if summary else None,
                            "max_ms": round(summary["max"], 1) if summary else None,
                            "mean_ms": round(summary["mean"], 1) if summary else None,
                            "sd_ms": round(summary["sd"], 1) if summary else None,
                            "warmups": profile["warmups"],
                            "runs": profile["runs"],
                            "timeout_s": profile["timeout_s"],
                            "response_bytes": result["response_bytes"],
                            "c_parse_median_ms": (
                                round(parse_median, 1) if parse_median is not None else None
                            ),
                            "c_parse_samples_json": json.dumps(result["c_parse"]),
                            "c_protocol_median_ms": (
                                round(protocol_median, 1)
                                if protocol_median is not None
                                else None
                            ),
                            "c_protocol_samples_json": json.dumps(
                                result["c_protocol"]
                            ),
                            "construct_total_ms": (
                                round(total_median, 1) if total_median is not None else None
                            ),
                            "construct_total_samples_json": json.dumps(
                                result["construct_total"]
                            ),
                            "construct_unattributed_median_ms": (
                                round(unattributed_median, 1)
                                if unattributed_median is not None
                                else None
                            ),
                            "construct_unattributed_samples_json": json.dumps(
                                result["construct_unattributed"]
                            ),
                            "gates": result["gates"],
                            "edges": result["edges"],
                            "derivations": result.get("derivations"),
                            "npcs_token_occurrences": result.get("ntok"),
                            "npcs_oplus": result.get("npcs_oplus"),
                            "npcs_ominus": result.get("npcs_ominus"),
                            "npcs_leaves": result.get("npcs_leaves"),
                            "rewrite_ms": result.get("rewrite_ms"),
                            "samples_json": json.dumps(result["samples"]),
                            "protocol": PROTOCOL,
                            "batch_id": batch_id,
                            "construction_requested": construction.get(
                                "requested_mode"
                            ),
                            "construction_effective": construction.get(
                                "effective_mode"
                            ),
                            "circuit_sha256": result.get("circuit_sha256"),
                            "circuit_cache_path": cache.get("circuit_path"),
                            "circuit_cache_metadata_path": cache.get("metadata_path"),
                            "circuit_cache_observation_sha256": cache.get(
                                "producer_observation_sha256"
                            ),
                            "circuit_cache_sidecar_sha256": cache.get(
                                "sidecar_sha256"
                            ),
                            **identity,
                            **evidence,
                            "notes": pack_note(
                                result.get("note", ""),
                                evidence=note_metadata,
                                cell_wall_ms=result.get("cell_wall_ms"),
                            ),
                        }
                        writer.writerow(record)
                        fh.flush()
                        os.fsync(fh.fileno())
                        _validate_opened_single_link(
                            args.out, fh.fileno(), "R9 timing checkpoint"
                        )
                        _fsync_directory(
                            os.path.dirname(os.path.abspath(args.out)) or "."
                        )
                        done.add(key)
                        display = (
                            f"{summary['median']:.0f}ms" if summary else result["status"]
                        )
                        print(
                            f"  [{engine} {scale} {cls}/{template} {method}] "
                            f"{result['status']:14} ans={result['answers']} {display}"
                            + (f" gates={result['gates']}" if result["gates"] else "")
                        )
    finally:
        _validate_opened_single_link(
            args.out, fh.fileno(), "R9 timing checkpoint"
        )
        fh.close()
    completion = None
    if not args.exploratory:
        completion = finalize_timing_completion(
            args.out,
            expected_identities=expected_identities,
            commit=COMMIT,
            batch_id=batch_id,
            profile=profile,
            engines=engines,
            scales=scales,
            methods=methods,
            classes=getattr(args, "selected_classes", FORMAL_CLASSES),
            require_circuit_cache=bool(cache_dir),
        )
    print(f"\nwrote/appended {args.out}")
    return completion


def _assert_output_identity(path, commit, batch_id):
    for row in _checkpoint_rows(path) or ():
        metadata = unpack_note(row)
        if (
            row.get("commit") != commit
            or metadata.get("protocol") != PROTOCOL
            or metadata.get("batch_id") != batch_id
        ):
            raise ValueError(
                "formal output already contains a different commit/protocol/batch"
            )


def _profile_number(args, attribute, env_name, cast, formal_value):
    explicit = getattr(args, attribute, None)
    ambient = os.environ.get(env_name)
    try:
        selected = (
            cast(explicit)
            if explicit is not None
            else cast(ambient)
            if ambient is not None
            else formal_value
        )
    except (TypeError, ValueError) as ex:
        raise ValueError(f"{env_name}/{attribute} is not a valid number") from ex
    if not args.exploratory and selected != formal_value:
        raise ValueError(
            f"formal {PROTOCOL} fixes {attribute}={formal_value}; "
            "overrides require --exploratory"
        )
    return selected


def resolve_run_profile(args):
    """Resolve the immutable formal protocol or explicit exploratory controls."""
    profile = {
        "warmups": _profile_number(
            args, "warmups", "PCM_WARMUPS", int, FORMAL_WARMUPS
        ),
        "runs": _profile_number(args, "runs", "PCM_RUNS", int, FORMAL_RUNS),
        "timeout_s": _profile_number(
            args, "timeout", "PCM_TIMEOUT_S", float, FORMAL_TIMEOUT
        ),
        "update_chunk_triples": _profile_number(
            args,
            "update_chunk_triples",
            "PCM_UPDATE_CHUNK_TRIPLES",
            int,
            FORMAL_UPDATE_CHUNK_TRIPLES,
        ),
        "orphan_cleanup_timeout_s": _profile_number(
            args,
            "orphan_cleanup_timeout",
            "PCM_ORPHAN_CLEANUP_TIMEOUT_S",
            float,
            FORMAL_ORPHAN_CLEANUP_TIMEOUT,
        ),
    }
    if (
        profile["warmups"] < 0
        or profile["runs"] <= 0
        or not math.isfinite(float(profile["timeout_s"]))
        or profile["timeout_s"] <= 0
        or profile["update_chunk_triples"] <= 0
        or not math.isfinite(float(profile["orphan_cleanup_timeout_s"]))
        or profile["orphan_cleanup_timeout_s"] <= 0
    ):
        raise ValueError("run-profile counts and deadlines must be positive")
    return profile


def _dimension(raw, label, allowed=None):
    values = [value.strip() for value in raw.split(",") if value.strip()]
    if not values:
        raise ValueError(f"--{label} must be non-empty")
    if len(set(values)) != len(values):
        raise ValueError(f"--{label} contains duplicate shard values")
    if allowed is not None:
        unknown = set(values) - set(allowed)
        if unknown:
            raise ValueError(f"unknown {label}: {sorted(unknown)}")
    return values


def main(argv=None):
    parser = argparse.ArgumentParser(
        epilog=(
            "Formal runs require PCM_FROZEN_INPUTS + PCM_BATCH_ID, a full clean Git HEAD, "
            "and frozen canonical base/reified/update identities. Use --exploratory only "
            "for explicit smoke tests."
        )
    )
    parser.add_argument(
        "--engines",
        default="graphdb",
        help="comma list: graphdb,oxigraph,qlever,millenniumdb",
    )
    parser.add_argument("--scales", default="10M")
    parser.add_argument("--classes", default="L,S,F,C,O,M")
    parser.add_argument("--methods", default="B,R,N,C")
    parser.add_argument("--warmups", type=int)
    parser.add_argument("--runs", type=int)
    parser.add_argument("--timeout", type=float)
    parser.add_argument("--update-chunk-triples", type=int)
    parser.add_argument("--orphan-cleanup-timeout", type=float)
    parser.add_argument("--out")
    parser.add_argument(
        "--exploratory",
        action="store_true",
        help="explicitly bypass frozen-input and clean-worktree formal gates",
    )
    args = parser.parse_args(argv)
    try:
        engines = _dimension(args.engines, "engines", ENGINES)
        scales = _dimension(args.scales, "scales")
        methods = _dimension(args.methods, "methods", IMPL)
        classes = _dimension(args.classes, "classes", FORMAL_CLASSES)
        if not args.exploratory and tuple(classes) != FORMAL_CLASSES:
            raise ValueError(
                "formal R9 shards may select only engine/scale/method; "
                "every shard must cover classes L,S,F,C,O,M"
            )
        args.selected_classes = tuple(classes)
        args.run_profile = resolve_run_profile(args)
    except ValueError as ex:
        parser.error(str(ex))

    formal_commit = None
    try:
        try:
            if args.exploratory:
                batch_id = require_batch_id()
                registry = bind_exploratory_registry(engines, scales, batch_id)
                frozen_document = None
            else:
                formal_commit = clean_git_identity()
                if formal_commit != COMMIT:
                    raise RuntimeError(
                        "module Git commit differs from invocation-start HEAD"
                    )
                context = load_formal_context(engines, scales, methods)
                batch_id = context["batch_id"]
                registry = context["registry"]
                frozen_document = context["document"]
                global _FORMAL_TOOL_SNAPSHOTS
                _FORMAL_TOOL_SNAPSHOTS = context["tool_snapshots"]
        except (ValueError, RuntimeError, freeze_inputs.FreezeError) as ex:
            parser.error(str(ex))

        run_root = os.path.join(
            DEFAULT_ARTIFACT_ROOT,
            ("exploratory-" if args.exploratory else "") + batch_id,
        )
        out = args.out or os.path.join(run_root, "construction_brnc.csv")
        try:
            args.out = _prepare_artifact_path(out, exploratory=args.exploratory)
            configured_cache = os.environ.get("PCM_CIRCUIT_CACHE_DIR")
            if configured_cache:
                cache_dir = _prepare_artifact_path(
                    configured_cache, exploratory=args.exploratory, directory=True
                )
            elif args.exploratory:
                cache_dir = None
            else:
                cache_dir = _prepare_artifact_path(
                    os.path.join(run_root, "circuit-cache"), directory=True
                )
        except (OSError, ValueError) as ex:
            parser.error(str(ex))

        with invocation_file_lock(args.out):
            _repair_checkpoint_tail(args.out)
            if not args.exploratory:
                _assert_output_identity(args.out, formal_commit, batch_id)
            _run_matrix(
                args,
                engines=engines,
                scales=scales,
                methods=methods,
                registry=registry,
                batch_id=batch_id,
                cache_dir=cache_dir,
                frozen_document=frozen_document,
            )
        return 0
    finally:
        if formal_commit is not None:
            try:
                for snapshot in (_FORMAL_TOOL_SNAPSHOTS or {}).values():
                    _verify_tool_snapshot(snapshot)
                verify_git_end(formal_commit)
            finally:
                _FORMAL_TOOL_SNAPSHOTS = None


if __name__ == "__main__":
    raise SystemExit(main())
