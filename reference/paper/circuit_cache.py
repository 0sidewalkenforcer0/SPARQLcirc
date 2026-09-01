"""Single-link, content-addressed canonical circuit cache for formal R9."""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile


PRIVATE_PREDICATE = re.compile(r"^\s*\S+\s+<urn:sc:[^>]*>\s+")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
COMMIT = re.compile(r"^[0-9a-f]{40}$")
SCHEMA = "canonical-circuit-cache-v3"
FORMAT = "canonical-sorted-ntriples-v1"
MAX_SIDECAR_BYTES = 16 * 1024 * 1024
MAX_INTEGER = (1 << 63) - 1
SIDECAR_KEYS = {
    "schema",
    "format",
    "circuit_sha256",
    "circuit_triples",
    "circuit_bytes",
    "circuit_file",
    "producer_observations",
    "sidecar_sha256",
}


def _canonical_json(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _digest(value):
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _strict_object(pairs):
    document = {}
    for key, value in pairs:
        if key in document:
            raise ValueError("circuit cache sidecar has duplicate object keys")
        document[key] = value
    return document


def _reject_json_constant(value):
    raise ValueError("circuit cache sidecar contains non-finite JSON: %s" % value)


def _exact_string(value, pattern=None):
    return type(value) is str and (pattern is None or pattern.fullmatch(value))


def _exact_count(value):
    return type(value) is int and 0 <= value <= MAX_INTEGER


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


def _validate_open(path, descriptor, label):
    opened = os.fstat(descriptor)
    try:
        current = os.lstat(path)
    except OSError as exc:
        raise ValueError(f"{label} path is unstable") from exc
    if (
        not stat.S_ISREG(opened.st_mode)
        or not stat.S_ISREG(current.st_mode)
        or opened.st_nlink != 1
        or current.st_nlink != 1
        or (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino)
    ):
        raise ValueError(f"{label} must be one stable single-link regular file")
    return opened


def _open_single(path, flags, label, mode=0o600):
    descriptor = None
    try:
        descriptor = os.open(
            os.fspath(path),
            flags
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
            mode,
        )
        _validate_open(path, descriptor, label)
        return descriptor
    except (OSError, ValueError) as exc:
        if descriptor is not None:
            os.close(descriptor)
        if isinstance(exc, ValueError):
            raise
        raise ValueError(f"{label} is missing, aliased, or unsafe") from exc


def _validate_directory(path, descriptor, label):
    opened = os.fstat(descriptor)
    try:
        current = os.lstat(path)
    except OSError as exc:
        raise ValueError(f"{label} path is unstable") from exc
    if (
        not stat.S_ISDIR(opened.st_mode)
        or not stat.S_ISDIR(current.st_mode)
        or (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino)
    ):
        raise ValueError(f"{label} must remain one stable directory")
    return opened


def _open_directory(path, label):
    descriptor = None
    try:
        descriptor = os.open(
            os.fspath(path),
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
        )
        _validate_directory(path, descriptor, label)
        return descriptor
    except (OSError, ValueError) as exc:
        if descriptor is not None:
            os.close(descriptor)
        if isinstance(exc, ValueError):
            raise
        raise ValueError(f"{label} is missing, aliased, or unsafe") from exc


def _read_descriptor(path, descriptor, label, limit=None):
    before = _validate_open(path, descriptor, label)
    if limit is not None and before.st_size > limit:
        raise ValueError(f"{label} exceeds its safety cap")
    os.lseek(descriptor, 0, os.SEEK_SET)
    blocks = []
    total = 0
    while True:
        block = os.read(descriptor, 1024 * 1024)
        if not block:
            break
        blocks.append(block)
        total += len(block)
        if limit is not None and total > limit:
            raise ValueError(f"{label} exceeds its safety cap")
    after = _validate_open(path, descriptor, label)
    if _stat_signature(before) != _stat_signature(after):
        raise ValueError(f"{label} changed while being read")
    return b"".join(blocks)


def _read_stable(path, label, limit=None):
    descriptor = _open_single(path, os.O_RDONLY, label)
    try:
        return _read_descriptor(path, descriptor, label, limit)
    finally:
        os.close(descriptor)


def _fsync_directory(path):
    descriptor = os.open(os.fspath(path), os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _normalize_escaped_apostrophe(line):
    """Normalize MillenniumDB's non-standard \' only inside an N-Triples literal."""
    out = []
    in_literal = False
    index = 0
    while index < len(line):
        char = line[index]
        if char == '"':
            in_literal = not in_literal
            out.append(char)
            index += 1
            continue
        if in_literal and char == "\\":
            end = index
            while end < len(line) and line[end] == "\\":
                end += 1
            count = end - index
            if end < len(line) and line[end] == '"':
                out.append("\\" * count)
                out.append('"')
                if count % 2 == 0:
                    in_literal = False
                index = end + 1
                continue
            if end < len(line) and line[end] == "'" and count % 2:
                out.append("\\" * (count - 1))
                out.append("'")
                index = end + 1
                continue
            out.append("\\" * count)
            index = end
            continue
        out.append(char)
        index += 1
    return "".join(out)


def canonical_bytes(lines):
    """Return sorted, deduplicated N-Triples bytes without private messages."""
    unique = set()
    for raw in lines:
        try:
            line = raw.decode("utf-8", "strict") if isinstance(raw, bytes) else str(raw)
        except UnicodeDecodeError:
            raise
        line = line.strip()
        # Serialization-agnostic content-addressing (RQ3 byte-identity): MillenniumDB emits
        # the non-standard escape \' for an apostrophe. Normalize only an unescaped \' inside
        # a literal; preserve \\' because it denotes a lexical backslash followed by apostrophe.
        line = _normalize_escaped_apostrophe(line)
        if not line or PRIVATE_PREDICATE.match(line):
            continue
        if not line.endswith(" ."):
            raise ValueError(
                "cache input is not one complete N-Triples statement: %r" % line[:160]
            )
        unique.add(line)
    return (("\n".join(sorted(unique)) + "\n").encode("utf-8") if unique else b"")


def _atomic_write(path, data, label):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if os.path.lexists(path):
        _read_stable(path, label)
    handle = tempfile.NamedTemporaryFile(
        prefix="." + path.name + ".",
        suffix=".tmp",
        dir=str(path.parent),
        delete=False,
    )
    temporary = Path(handle.name)
    try:
        with handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
            if os.fstat(handle.fileno()).st_nlink != 1:
                raise ValueError(f"{label} temporary gained a hardlink")
        if os.path.lexists(path):
            descriptor = _open_single(path, os.O_RDONLY, label)
            os.close(descriptor)
        os.replace(temporary, path)
        descriptor = _open_single(path, os.O_RDONLY, label)
        try:
            if os.fstat(descriptor).st_size != len(data):
                raise ValueError(f"{label} publication size mismatch")
        finally:
            os.close(descriptor)
        _fsync_directory(path.parent)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


@contextlib.contextmanager
def _stem_lock(directory, stem):
    """Serialize payload and sidecar publication for one canonical stem."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / ("." + stem + ".lock")
    directory_descriptor = _open_directory(
        directory, "circuit cache directory lock"
    )
    descriptor = None
    try:
        # The stable directory lock preserves cooperative exclusion even if an
        # attacker replaces the named per-stem lock while it is held.
        fcntl.flock(directory_descriptor, fcntl.LOCK_EX)
        _validate_directory(
            directory, directory_descriptor, "circuit cache directory lock"
        )
        existed = os.path.lexists(path)
        descriptor = _open_single(
            path, os.O_RDWR | os.O_CREAT, "circuit cache stem lock"
        )
        if not existed:
            _fsync_directory(directory)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        _validate_open(path, descriptor, "circuit cache stem lock")
        yield
        _validate_open(path, descriptor, "circuit cache stem lock")
        _validate_directory(
            directory, directory_descriptor, "circuit cache directory lock"
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


def _seal_observation(metadata, circuit_descriptor):
    observation = dict(metadata)
    observation.update(circuit_descriptor)
    observation.pop("producer_observation_sha256", None)
    observation["producer_observation_sha256"] = _digest(observation)
    return observation


def _verify_observation(observation, circuit_descriptor):
    if type(observation) is not dict:
        raise ValueError("cache producer observation is not an object")
    required = {
        "producer_observation_sha256",
        "query_sha256",
        "commit",
        "batch_id",
        *circuit_descriptor,
    }
    if not required.issubset(observation):
        raise ValueError("cache producer observation schema mismatch")
    claimed = observation.get("producer_observation_sha256")
    body = dict(observation)
    body.pop("producer_observation_sha256", None)
    if not _exact_string(claimed, SHA256) or _digest(body) != claimed:
        raise ValueError("cache producer observation integrity mismatch")
    for name, value in circuit_descriptor.items():
        if type(observation.get(name)) is not type(value) or observation.get(name) != value:
            raise ValueError(f"cache producer observation has wrong {name}")
    if not _exact_string(observation.get("query_sha256"), SHA256):
        raise ValueError("cache producer observation has no query_sha256")
    if not _exact_string(observation.get("commit"), COMMIT):
        raise ValueError("cache producer observation has no full commit")
    if not _exact_string(observation.get("batch_id"), SHA256):
        raise ValueError("cache producer observation has no batch_id")
    return observation


def _seal_sidecar(base, observations):
    document = {
        **base,
        "producer_observations": sorted(
            observations, key=lambda item: item["producer_observation_sha256"]
        ),
    }
    document["sidecar_sha256"] = _digest(document)
    return document


def _validate_sidecar_payload(
    metadata_path, circuit_path, payload, sidecar_payload, expected_sha256
):
    if canonical_bytes(payload.splitlines()) != payload:
        raise ValueError("circuit cache entry is not canonical: %s" % circuit_path)
    circuit_sha = hashlib.sha256(payload).hexdigest()
    if expected_sha256 is not None and circuit_sha != expected_sha256:
        raise ValueError(
            "circuit cache SHA-256 mismatch: %s != %s"
            % (circuit_sha, expected_sha256)
        )
    try:
        document = json.loads(
            sidecar_payload.decode("utf-8", "strict"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError("circuit cache sidecar is invalid JSON") from exc
    if (
        type(document) is not dict
        or set(document) != SIDECAR_KEYS
        or type(document.get("schema")) is not str
        or document.get("schema") != SCHEMA
    ):
        raise ValueError("circuit cache sidecar schema mismatch")
    canonical_sidecar = _canonical_json(document) + b"\n"
    if sidecar_payload != canonical_sidecar:
        raise ValueError("circuit cache sidecar is not canonical JSON")
    claimed = document.get("sidecar_sha256")
    body = dict(document)
    body.pop("sidecar_sha256", None)
    if not _exact_string(claimed, SHA256) or _digest(body) != claimed:
        raise ValueError("circuit cache sidecar integrity mismatch")
    descriptor = {
        "format": FORMAT,
        "circuit_sha256": circuit_sha,
        "circuit_triples": payload.count(b"\n"),
        "circuit_bytes": len(payload),
        "circuit_file": circuit_path.name,
    }
    for name, value in {"schema": SCHEMA, **descriptor}.items():
        if type(document.get(name)) is not type(value) or document.get(name) != value:
            raise ValueError(f"circuit cache sidecar has wrong {name}")
    if not _exact_count(document["circuit_triples"]):
        raise ValueError("circuit cache sidecar has invalid circuit_triples")
    if not _exact_count(document["circuit_bytes"]):
        raise ValueError("circuit cache sidecar has invalid circuit_bytes")
    if metadata_path.name != circuit_path.with_suffix(".json").name:
        raise ValueError("circuit cache payload/sidecar stems differ")
    observations = document.get("producer_observations")
    if not isinstance(observations, list) or not observations:
        raise ValueError("circuit cache has no producer observations")
    verified = [_verify_observation(item, descriptor) for item in observations]
    hashes = [item["producer_observation_sha256"] for item in verified]
    if hashes != sorted(set(hashes)):
        raise ValueError("cache producer observations are duplicate or unsorted")
    query_hashes = {item["query_sha256"] for item in verified}
    if len(query_hashes) != 1:
        raise ValueError("one cache stem mixes multiple query identities")
    expected_stem = "%s-%s" % (next(iter(query_hashes)), circuit_sha)
    if circuit_path.stem != expected_stem:
        raise ValueError("canonical cache filename does not match content identities")
    return {
        "document": document,
        "observations": verified,
        "payload": payload,
        "circuit_sha256": circuit_sha,
        "circuit_triples": descriptor["circuit_triples"],
        "circuit_bytes": descriptor["circuit_bytes"],
        "circuit_path": str(circuit_path),
        "metadata_path": str(metadata_path),
        "sidecar_sha256": claimed,
        "sidecar_payload_sha256": hashlib.sha256(sidecar_payload).hexdigest(),
        "sidecar_bytes": len(sidecar_payload),
    }


def load_sidecar(metadata_path, circuit_path, expected_sha256=None):
    """Validate payload and sidecar from one simultaneous stable FD pair."""
    circuit_path = Path(os.path.abspath(os.fspath(Path(circuit_path).expanduser())))
    metadata_path = Path(os.path.abspath(os.fspath(Path(metadata_path).expanduser())))
    circuit_descriptor_fd = _open_single(
        circuit_path, os.O_RDONLY, "circuit cache payload"
    )
    metadata_descriptor_fd = None
    try:
        metadata_descriptor_fd = _open_single(
            metadata_path, os.O_RDONLY, "circuit cache sidecar"
        )
        payload = _read_descriptor(
            circuit_path,
            circuit_descriptor_fd,
            "circuit cache payload",
        )
        sidecar_payload = _read_descriptor(
            metadata_path,
            metadata_descriptor_fd,
            "circuit cache sidecar",
            MAX_SIDECAR_BYTES,
        )
        circuit_signature = _stat_signature(os.fstat(circuit_descriptor_fd))
        metadata_signature = _stat_signature(os.fstat(metadata_descriptor_fd))
        result = _validate_sidecar_payload(
            metadata_path,
            circuit_path,
            payload,
            sidecar_payload,
            expected_sha256,
        )
        if (
            _stat_signature(
                _validate_open(
                    circuit_path, circuit_descriptor_fd, "circuit cache payload"
                )
            )
            != circuit_signature
            or _stat_signature(
                _validate_open(
                    metadata_path,
                    metadata_descriptor_fd,
                    "circuit cache sidecar",
                )
            )
            != metadata_signature
        ):
            raise ValueError("circuit cache payload/sidecar changed during validation")
        result["payload_snapshot"] = {
            "path": str(circuit_path),
            "bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
            "signature": circuit_signature,
            "label": "canonical circuit cache payload",
        }
        result["sidecar_snapshot"] = {
            "path": str(metadata_path),
            "bytes": len(sidecar_payload),
            "sha256": hashlib.sha256(sidecar_payload).hexdigest(),
            "signature": metadata_signature,
            "label": "canonical circuit cache sidecar",
        }
        return result
    finally:
        if metadata_descriptor_fd is not None:
            os.close(metadata_descriptor_fd)
        os.close(circuit_descriptor_fd)


def store(cache_dir, lines, metadata):
    """Store one circuit and one sealed producer observation."""
    metadata = dict(metadata)
    query_sha = metadata.get("query_sha256")
    commit = metadata.get("commit")
    batch_id = metadata.get("batch_id")
    if not _exact_string(query_sha, SHA256):
        raise ValueError("metadata requires a lowercase 64-hex query_sha256")
    if not _exact_string(commit, COMMIT):
        raise ValueError("metadata requires a full lowercase 40-hex Git commit")
    if not _exact_string(batch_id, SHA256):
        raise ValueError("metadata requires a lowercase 64-hex batch_id")
    payload = canonical_bytes(lines)
    circuit_sha = hashlib.sha256(payload).hexdigest()
    stem = "%s-%s" % (query_sha, circuit_sha)
    directory = Path(cache_dir).expanduser().resolve()
    circuit_path = directory / (stem + ".nt")
    metadata_path = directory / (stem + ".json")
    base = {
        "schema": SCHEMA,
        "format": FORMAT,
        "circuit_sha256": circuit_sha,
        "circuit_triples": payload.count(b"\n"),
        "circuit_bytes": len(payload),
        "circuit_file": circuit_path.name,
    }
    observation_descriptor = {
        key: base[key]
        for key in (
            "format",
            "circuit_sha256",
            "circuit_triples",
            "circuit_bytes",
            "circuit_file",
        )
    }
    observation = _seal_observation(metadata, observation_descriptor)
    with _stem_lock(directory, stem):
        if os.path.lexists(circuit_path):
            existing = _read_stable(circuit_path, "circuit cache payload")
            if existing != payload:
                raise RuntimeError("content-addressed cache collision at %s" % circuit_path)
        else:
            _atomic_write(circuit_path, payload, "circuit cache payload")
        observations = []
        if os.path.lexists(metadata_path):
            loaded = load_sidecar(metadata_path, circuit_path, circuit_sha)
            observations.extend(loaded["observations"])
        by_hash = {
            item["producer_observation_sha256"]: item for item in observations
        }
        by_hash[observation["producer_observation_sha256"]] = observation
        document = _seal_sidecar(base, list(by_hash.values()))
        if not os.path.lexists(metadata_path) or load_sidecar(
            metadata_path, circuit_path, circuit_sha
        )["document"] != document:
            _atomic_write(
                metadata_path,
                _canonical_json(document) + b"\n",
                "circuit cache sidecar",
            )
        # Validate the exact bytes just published before returning an identity
        # that a checkpoint may trust on resume.
        loaded = load_sidecar(metadata_path, circuit_path, circuit_sha)
        if observation["producer_observation_sha256"] not in {
            item["producer_observation_sha256"] for item in loaded["observations"]
        }:
            raise RuntimeError("published cache sidecar lost producer observation")
    return {
        "circuit_sha256": circuit_sha,
        "circuit_triples": len(payload.splitlines()),
        "circuit_bytes": len(payload),
        "circuit_path": str(circuit_path),
        "metadata_path": str(metadata_path),
        "producer_observation_sha256": observation[
            "producer_observation_sha256"
        ],
        "sidecar_sha256": loaded["sidecar_sha256"],
    }


def verify(path, expected_sha256=None):
    """Verify canonical payload bytes from a single stable descriptor."""
    path = Path(os.path.abspath(os.fspath(Path(path).expanduser())))
    payload = _read_stable(path, "circuit cache payload")
    if canonical_bytes(payload.splitlines()) != payload:
        raise ValueError("circuit cache entry is not canonical: %s" % path)
    digest = hashlib.sha256(payload).hexdigest()
    if expected_sha256 is not None and digest != expected_sha256:
        raise ValueError(
            "circuit cache SHA-256 mismatch: %s != %s"
            % (digest, expected_sha256)
        )
    return {
        "circuit_sha256": digest,
        "circuit_triples": payload.count(b"\n"),
        "circuit_bytes": len(payload),
        "circuit_path": str(path),
        "payload": payload,
    }
