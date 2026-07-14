#!/usr/bin/env python3
"""Freeze the immutable identity of one SPARQLcirc experiment batch.

Formal mode deliberately requires explicit data files, tool binaries, stores,
and role-discriminating sentinels; it has no expensive or deployment-specific
defaults. Manifests and the captured query root must live in the producer's
actual source repository; explicitly named data/tool and ``@sentinel`` files
may be external because their bytes are independently hashed and snapshotted.
``--allow-empty-inputs`` is reserved for tests/exploration.  Output
contains repository-relative manifest query paths and logical labels, but never
raw data/tool paths, endpoint hostnames or paths, URL credentials/query/fragment
components, or sentinel query text.  Existing output is immutable: only a
byte-identical retry is accepted.  Harnesses should reuse ``load_frozen_batch``
instead of independently implementing the canonical batch digest.
"""

from __future__ import annotations

import argparse
import csv
import contextlib
from dataclasses import dataclass
import errno
import fcntl
import hashlib
import io
import ipaddress
import json
import math
import multiprocessing
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import signal
import socket
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.request as U
from urllib.parse import urlsplit


SCHEMA = "sparqlcirc-frozen-inputs"
SCHEMA_VERSION = 2
SHA256 = re.compile(r"^[0-9a-f]{64}$")
SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:+-]{0,95}$")
SAFE_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.+ ():/-]{0,127}$")
MAX_SENTINEL_BYTES = 1024 * 1024
MAX_FROZEN_BATCH_BYTES = 16 * 1024 * 1024
ACCESS_MODES = {"read-only", "writable"}
CANARY_PROTOCOL = "insert-ask-delete-ask-v1"

MANIFESTS = {
    "workload": {
        "columns": (
            "suite", "class", "template", "instance", "query_file",
            "query_sha256", "scale", "bound_policy", "notes",
        ),
        "key": ("suite", "scale", "class", "template", "instance"),
    },
    "path": {
        "columns": (
            "suite", "class", "template", "form", "bound", "query_file",
            "query_sha256", "dataset", "notes",
        ),
        "key": ("suite", "dataset", "class", "template", "form", "bound"),
    },
}


class FreezeError(RuntimeError):
    """A public, path/credential-safe validation failure."""


@dataclass(frozen=True)
class _FileSnapshot:
    """Private filesystem state; paths and ctime never enter frozen JSON."""

    path: Path
    signature: tuple[int, int, int, int, int, int]
    label: str


@dataclass(frozen=True)
class _DirectorySnapshot:
    """One resolved directory plus the lexical path that selected it."""

    path: Path
    source_path: Path
    identity: tuple[int, int, int, int]
    label: str


@dataclass
class _OutputTarget:
    """An output name bound to one already-open directory inode."""

    parent: Path
    source_parent: Path
    name: str
    identity: tuple[int, int, int, int]
    descriptor: int
    committed: bool = False

    @property
    def path(self):
        return self.parent / self.name


@dataclass(frozen=True)
class _CanonicalEndpoint:
    """Private endpoint material used only for equality/binding checks."""

    scheme: str
    host: str
    port: int
    path: str

    @property
    def origin(self):
        return self.scheme, self.host, self.port

    @property
    def url(self):
        rendered_host = f"[{self.host}]" if ":" in self.host else self.host
        return f"{self.scheme}://{rendered_host}:{self.port}{self.path}"


def _json_bytes(value):
    try:
        rendered = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as ex:
        raise FreezeError("value is not canonical JSON") from ex
    return rendered.encode("utf-8")


def _digest(value):
    return hashlib.sha256(_json_bytes(value)).hexdigest()


def _stat_signature(value):
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _directory_identity(value):
    return value.st_dev, value.st_ino, value.st_mode, value.st_uid


def _capture_directory(path, label):
    source = Path(os.path.abspath(os.fspath(path)))
    try:
        resolved = source.resolve(strict=True)
        observed = os.stat(resolved)
    except OSError as ex:
        raise FreezeError(f"{label}: directory is missing or unreadable") from ex
    if not stat.S_ISDIR(observed.st_mode):
        raise FreezeError(f"{label}: path is not a directory")
    return _DirectorySnapshot(
        resolved, source, _directory_identity(observed), label
    )


def _verify_directory(snapshot):
    try:
        resolved = snapshot.source_path.resolve(strict=True)
        observed = os.stat(snapshot.path)
    except OSError as ex:
        raise FreezeError(f"{snapshot.label}: directory disappeared") from ex
    if (
        resolved != snapshot.path
        or not stat.S_ISDIR(observed.st_mode)
        or _directory_identity(observed) != snapshot.identity
    ):
        raise FreezeError(f"{snapshot.label}: directory identity changed")


def _canonical_query_path(relative, label):
    if (
        not isinstance(relative, str)
        or not relative
        or relative.strip() != relative
        or "\\" in relative
        or any(ord(character) < 32 or ord(character) == 127 for character in relative)
    ):
        raise FreezeError(f"{label}: invalid repository-relative query_file")
    parts = relative.split("/")
    candidate = PurePosixPath(relative)
    if (
        candidate.is_absolute()
        or any(part in ("", ".", "..") for part in parts)
        or str(candidate) != relative
    ):
        raise FreezeError(f"{label}: query_file is not canonical POSIX relative path")
    return candidate


def _snapshot(path, value, label):
    return _FileSnapshot(
        Path(os.path.abspath(os.fspath(path))), _stat_signature(value), label
    )


def verify_snapshots(snapshots):
    """Cheaply re-stat all private input snapshots immediately before output."""
    for snapshot in snapshots:
        try:
            current = os.stat(snapshot.path)
        except OSError as ex:
            raise FreezeError(f"{snapshot.label}: input disappeared before output") from ex
        if not stat.S_ISREG(current.st_mode) or _stat_signature(current) != snapshot.signature:
            raise FreezeError(f"{snapshot.label}: input changed before output")


def hash_file(path, *, label, allow_empty=False, snapshots=None):
    """Stream a regular file and detect replacement/modification while hashing."""
    try:
        with open(path, "rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode):
                raise FreezeError(f"{label}: input is not a regular file")
            if before.st_size == 0 and not allow_empty:
                raise FreezeError(f"{label}: zero-byte input is not freezeable")
            digest = hashlib.sha256()
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
            after = os.fstat(stream.fileno())
        current = os.stat(path)
    except FreezeError:
        raise
    except OSError as ex:
        raise FreezeError(f"{label}: input is missing or unreadable") from ex
    if not (_stat_signature(before) == _stat_signature(after) == _stat_signature(current)):
        raise FreezeError(f"{label}: input changed while it was being hashed")
    if snapshots is not None:
        snapshots.append(_snapshot(path, current, label))
    return {"bytes": before.st_size, "sha256": digest.hexdigest()}


def _read_small_file(path, *, label, limit, snapshots=None):
    """Stat first, then read at most limit bytes from one stable regular file."""
    try:
        with open(path, "rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode):
                raise FreezeError(f"{label}: input is not a regular file")
            if before.st_size == 0:
                raise FreezeError(f"{label}: input is empty")
            if before.st_size > limit:
                raise FreezeError(f"{label}: input exceeds the safety cap")
            payload = stream.read(limit + 1)
            after = os.fstat(stream.fileno())
        current = os.stat(path)
    except FreezeError:
        raise
    except OSError as ex:
        raise FreezeError(f"{label}: input is missing or unreadable") from ex
    if len(payload) > limit:
        raise FreezeError(f"{label}: input exceeds the safety cap")
    if not (_stat_signature(before) == _stat_signature(after) == _stat_signature(current)):
        raise FreezeError(f"{label}: input changed while it was being read")
    if snapshots is not None:
        snapshots.append(_snapshot(path, current, label))
    return payload


def _read_single_link_file(path, *, label, limit):
    """Read one immutable regular path without following aliases."""
    descriptor = None
    try:
        before = os.lstat(path)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_size == 0
            or before.st_size > limit
        ):
            raise FreezeError(f"{label}: input is not a single-link bounded regular file")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            payload = stream.read(limit + 1)
        after = os.fstat(descriptor)
        current = os.lstat(path)
    except FreezeError:
        raise
    except OSError as ex:
        raise FreezeError(f"{label}: input is missing, aliased, or unreadable") from ex
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
    if len(payload) > limit:
        raise FreezeError(f"{label}: input exceeds the safety cap")
    if (
        before.st_nlink != 1
        or opened.st_nlink != 1
        or after.st_nlink != 1
        or current.st_nlink != 1
        or not (
            _stat_signature(before)
            == _stat_signature(opened)
            == _stat_signature(after)
            == _stat_signature(current)
        )
    ):
        raise FreezeError(f"{label}: input changed or gained an alias while being read")
    return payload


def _inside(root, relative, label):
    candidate = _canonical_query_path(relative, label)
    root_snapshot = (
        root if isinstance(root, _DirectorySnapshot)
        else _capture_directory(root, "query root")
    )
    _verify_directory(root_snapshot)
    try:
        resolved = (root_snapshot.path / Path(*candidate.parts)).resolve(strict=True)
        resolved.relative_to(root_snapshot.path)
    except (OSError, ValueError) as ex:
        raise FreezeError(f"{label}: query_file is missing or escapes query root") from ex
    if not resolved.is_file():
        raise FreezeError(f"{label}: query_file is not a regular file")
    return resolved


def validate_manifest(path, kind, query_root, snapshots=None):
    """Validate one fixed-schema CSV and every query hash, failing closed."""
    if kind not in MANIFESTS:
        raise FreezeError(f"unknown manifest kind: {kind}")
    spec = MANIFESTS[kind]
    root_snapshot = (
        query_root if isinstance(query_root, _DirectorySnapshot)
        else _capture_directory(query_root, "query root")
    )
    local_snapshots = []
    try:
        raw = open(path, "rb")
    except OSError as ex:
        raise FreezeError(f"{kind} manifest is missing or unreadable") from ex
    entries, keys = [], set()
    try:
        with raw:
            before = os.fstat(raw.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size == 0:
                raise FreezeError(f"{kind} manifest is not a nonempty regular file")
            digest = hashlib.sha256()
            for block in iter(lambda: raw.read(1024 * 1024), b""):
                digest.update(block)
            hashed = os.fstat(raw.fileno())
            raw.seek(0)
            text_stream = io.TextIOWrapper(raw, newline="", encoding="utf-8-sig")
            try:
                reader = csv.DictReader(text_stream, strict=True)
                if tuple(reader.fieldnames or ()) != spec["columns"]:
                    raise FreezeError(f"{kind} manifest schema/header mismatch")
                semantic = tuple(name for name in spec["columns"] if name != "notes")
                for line_no, row in enumerate(reader, 2):
                    if None in row:
                        raise FreezeError(f"{kind} manifest row {line_no} has extra fields")
                    if any(value is None for value in row.values()):
                        raise FreezeError(f"{kind} manifest row {line_no} has missing fields")
                    if any(not row[name] or row[name].strip() != row[name] for name in semantic):
                        raise FreezeError(
                            f"{kind} manifest row {line_no} has blank/padded semantic fields"
                        )
                    if any(
                        any(
                            ord(character) < 32 or ord(character) == 127
                            for character in row[name]
                        )
                        for name in semantic
                    ):
                        raise FreezeError(
                            f"{kind} manifest row {line_no} has control characters "
                            "in semantic fields"
                        )
                    expected = row["query_sha256"]
                    if not SHA256.fullmatch(expected):
                        raise FreezeError(
                            f"{kind} manifest row {line_no} has invalid query_sha256"
                        )
                    key = tuple(row[name] for name in spec["key"])
                    if key in keys:
                        raise FreezeError(
                            f"{kind} manifest has duplicate logical key at row {line_no}"
                        )
                    keys.add(key)
                    query = _inside(
                        root_snapshot, row["query_file"], f"{kind} row {line_no}"
                    )
                    observed = hash_file(
                        query,
                        label=f"{kind} row {line_no} query",
                        snapshots=local_snapshots,
                    )["sha256"]
                    if observed != expected:
                        raise FreezeError(
                            f"{kind} manifest query hash mismatch at row {line_no}"
                        )
                    entries.append({
                        "key": {name: row[name] for name in spec["key"]},
                        "query_file": row["query_file"],
                        "query_sha256": expected,
                    })
            finally:
                text_stream.detach()
            after = os.fstat(raw.fileno())
        current = os.stat(path)
    except FreezeError:
        raise
    except (csv.Error, UnicodeError) as ex:
        raise FreezeError(f"{kind} manifest is not valid UTF-8 CSV") from ex
    except OSError as ex:
        raise FreezeError(f"{kind} manifest changed or became unreadable") from ex
    if not (
        _stat_signature(before)
        == _stat_signature(hashed)
        == _stat_signature(after)
        == _stat_signature(current)
    ):
        raise FreezeError(f"{kind} manifest changed while it was being validated")
    local_snapshots.append(_snapshot(path, current, f"{kind} manifest"))
    if not entries:
        raise FreezeError(f"{kind} manifest is empty")
    _verify_directory(root_snapshot)
    verify_snapshots(local_snapshots)
    if snapshots is not None:
        snapshots.extend(local_snapshots)
    return {
        "kind": kind,
        "schema": list(spec["columns"]),
        "bytes": before.st_size,
        "sha256": digest.hexdigest(),
        "rows": len(entries),
        "queries": sorted(entries, key=lambda item: _json_bytes(item["key"])),
    }


def _named_path(value, category):
    if "=" not in value:
        raise FreezeError(f"{category}: expected NAME=PATH")
    name, path = value.split("=", 1)
    if not SAFE_NAME.fullmatch(name) or not path:
        raise FreezeError(f"{category}: invalid logical name or missing path")
    return name, path


def hash_named_paths(values, category, snapshots=None):
    """Hash only explicitly named files; raw paths never enter the result."""
    parsed, names = [], set()
    for value in values:
        name, path = _named_path(value, category)
        if name in names:
            raise FreezeError(f"{category}: duplicate logical name {name}")
        names.add(name)
        parsed.append((name, path))
    records = []
    for name, path in parsed:
        record = hash_file(
            path, label=f"{category} {name}", snapshots=snapshots
        )
        records.append({"name": name, **record})
    return sorted(records, key=lambda item: item["name"])


def endpoint_identity(endpoint):
    """Return safe metadata plus a private canonical endpoint for internal checks."""
    if (
        not isinstance(endpoint, str)
        or not endpoint
        or endpoint.strip() != endpoint
        or any(ord(char) <= 32 or ord(char) == 127 for char in endpoint)
    ):
        raise FreezeError("endpoint is blank or contains control/outer whitespace")
    try:
        parsed = urlsplit(endpoint)
        port = parsed.port
    except ValueError as ex:
        raise FreezeError("endpoint has invalid URL/port syntax") from ex
    if (
        parsed.username is not None
        or parsed.password is not None
        or "@" in parsed.netloc
    ):
        raise FreezeError("formal endpoint must not contain userinfo")
    if "?" in endpoint:
        raise FreezeError("formal endpoint must not contain a query component")
    if "#" in endpoint:
        raise FreezeError("formal endpoint must not contain a fragment")
    if parsed.netloc.endswith(":") or (port is not None and not 1 <= port <= 65535):
        raise FreezeError("endpoint has an invalid explicit port")
    scheme = parsed.scheme.lower()
    if scheme not in ("http", "https") or not parsed.hostname:
        raise FreezeError("endpoint must be an absolute HTTP(S) URL")
    host = parsed.hostname.rstrip(".").lower()
    if not host:
        raise FreezeError("endpoint hostname is invalid")
    try:
        host_ascii = host.encode("idna").decode("ascii")
    except UnicodeError as ex:
        raise FreezeError("endpoint hostname is invalid") from ex
    try:
        address = ipaddress.ip_address(host_ascii)
    except ValueError:
        labels = host_ascii.split(".")
        if len(host_ascii) > 253 or any(
            not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
            for label in labels
        ):
            raise FreezeError("endpoint hostname is invalid")
        loopback = host_ascii == "localhost"
    else:
        loopback = address.is_loopback
    effective_port = port if port is not None else (443 if scheme == "https" else 80)
    path = parsed.path or "/"
    if not path.startswith("/"):
        raise FreezeError("endpoint path is invalid")
    canonical = _CanonicalEndpoint(scheme, host_ascii, effective_port, path)
    origin_text = canonical.url[: -len(path)] if path != "/" else canonical.url[:-1]
    normalized_path = path.rstrip("/") or "/"
    parent, separator, leaf = normalized_path.rpartition("/")
    if not separator:
        parent, leaf = "/", path
    else:
        parent = parent or "/"
    leaf_kind = leaf if leaf in ("query", "update", "statements") else "other"
    safe = {
        "endpoint_sha256": hashlib.sha256(canonical.url.encode("utf-8")).hexdigest(),
        "origin_sha256": hashlib.sha256(origin_text.encode("utf-8")).hexdigest(),
        "path_sha256": hashlib.sha256(normalized_path.encode("utf-8")).hexdigest(),
        "parent_path_sha256": hashlib.sha256(parent.encode("utf-8")).hexdigest(),
        "path_leaf_kind": leaf_kind,
        "scheme": scheme,
        "port": effective_port,
        "host_class": "loopback" if loopback else "network",
    }
    return safe, canonical


def _bind_update_endpoint(reified, update):
    if update.url == reified.url:
        return "canonical-same-as-reified"
    parent = reified.path.rstrip("/")
    statements_alias = f"{parent}/statements" if parent else "/statements"
    segments = parent[1:].split("/") if parent else []
    unambiguous_parent = (
        reified.path in (parent, parent + "/")
        and "%" not in reified.path
        and "%" not in update.path
        and all(segment not in ("", ".", "..") for segment in segments)
    )
    if (
        unambiguous_parent
        and update.origin == reified.origin
        and update.path == statements_alias
    ):
        return "strict-reified-statements-child"
    reified_parent, separator, reified_leaf = reified.path.rstrip("/").rpartition("/")
    update_parent, update_separator, update_leaf = update.path.rstrip("/").rpartition("/")
    if (
        separator
        and update_separator
        and reified.origin == update.origin
        and reified_parent == update_parent
        and reified_leaf == "query"
        and update_leaf == "update"
        and "%" not in reified.path
        and "%" not in update.path
        and all(
            segment not in ("", ".", "..")
            for segment in (reified_parent[1:].split("/") if reified_parent else [])
        )
    ):
        return "strict-query-update-sibling"
    raise FreezeError("update endpoint is not provably bound to the reified endpoint")


def validate_store_specs(specs):
    """Validate repeatable store/data/access/endpoint registration groups."""
    records, urls, seen = [], {}, set()
    for values in specs:
        if not isinstance(values, (list, tuple)) or len(values) != 9:
            raise FreezeError("store specification must contain exactly nine fields")
        if any(not isinstance(value, str) for value in values):
            raise FreezeError("store specification fields must be strings")
        (
            engine,
            scale,
            version,
            access_mode,
            base_data_name,
            reified_data_name,
            base,
            reified,
            update,
        ) = values
        if not SAFE_NAME.fullmatch(engine) or not SAFE_NAME.fullmatch(scale):
            raise FreezeError("store has invalid engine or scale identifier")
        if not SAFE_VERSION.fullmatch(version):
            raise FreezeError(f"store {engine}/{scale} has invalid engine version")
        if access_mode not in ACCESS_MODES:
            raise FreezeError(f"store {engine}/{scale} has invalid access mode")
        if (
            not SAFE_NAME.fullmatch(base_data_name)
            or not SAFE_NAME.fullmatch(reified_data_name)
            or base_data_name == reified_data_name
        ):
            raise FreezeError(
                f"store {engine}/{scale} requires distinct valid base/reified data names"
            )
        if access_mode == "read-only" and update != "-":
            raise FreezeError(f"read-only store {engine}/{scale} must not register update")
        if access_mode == "writable" and update == "-":
            raise FreezeError(f"writable store {engine}/{scale} requires update")
        key = (engine, scale)
        if key in seen:
            raise FreezeError(f"duplicate store identity {engine}/{scale}")
        seen.add(key)
        base_record, base_canonical = endpoint_identity(base)
        reified_record, reified_canonical = endpoint_identity(reified)
        if base_canonical.url == reified_canonical.url:
            raise FreezeError(f"store {engine}/{scale} base and reified endpoints are identical")
        endpoint_records = {"base": base_record, "reified": reified_record, "update": None}
        update_binding = "absent"
        urls[(engine, scale, "base")] = base
        urls[(engine, scale, "reified")] = reified
        if update != "-":
            update_record, update_canonical = endpoint_identity(update)
            update_binding = _bind_update_endpoint(reified_canonical, update_canonical)
            endpoint_records["update"] = update_record
            urls[(engine, scale, "update")] = update
        records.append({
            "engine": engine,
            "scale": scale,
            "engine_version": version,
            "access_mode": access_mode,
            "base_data_name": base_data_name,
            "reified_data_name": reified_data_name,
            "endpoints": endpoint_records,
            "update_binding": update_binding,
            "update_canary": None,
        })
    return sorted(records, key=lambda item: (item["engine"], item["scale"])), urls


def validate_store_data_references(stores, data_files, *, formal):
    """Bind each store role to one named, content-hashed frozen data input."""
    available = {record["name"]: record["sha256"] for record in data_files}
    for store in stores:
        names = (store["base_data_name"], store["reified_data_name"])
        if names[0] == names[1]:
            raise FreezeError("base and reified store roles must reference distinct data")
        if formal and any(name not in available for name in names):
            raise FreezeError(
                f"store {store['engine']}/{store['scale']} references an unfrozen data name"
            )
        if formal and available[names[0]] == available[names[1]]:
            raise FreezeError(
                f"store {store['engine']}/{store['scale']} base/reified data are byte-identical"
            )


def sentinel_fingerprint(kind, value):
    if kind == "ask":
        if type(value) is not bool:
            raise FreezeError("ASK sentinel value must be boolean")
        normalized = {"kind": "ask", "boolean": value}
    elif kind == "count":
        if type(value) is not int or value < 0:
            raise FreezeError("COUNT sentinel value must be a non-negative integer")
        normalized = {"kind": "count", "value": value}
    else:
        raise FreezeError("sentinel must be ASK or SELECT COUNT")
    return _digest(normalized)


def _validate_sentinel_form(query, kind):
    if kind not in ("ask", "count"):
        raise FreezeError("sentinel kind must be explicit ask or count")
    if kind == "ask":
        if not re.match(r"^ASK\b", query, flags=re.IGNORECASE):
            raise FreezeError("ASK sentinel query must begin with ASK")
        return
    match = re.match(
        r"^SELECT\b(?P<head>.*?)(?:\bWHERE\b|\{)",
        query,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not match:
        raise FreezeError("COUNT sentinel query must be a SELECT projection")
    projection = re.sub(r"#[^\r\n]*", " ", match.group("head"))
    if any(character in projection for character in ("'", '"', "<", ">")):
        raise FreezeError("COUNT sentinel projection contains unsupported literals/IRIs")
    if not re.search(r"\bCOUNT\s*\(", projection, flags=re.IGNORECASE):
        raise FreezeError("COUNT sentinel SELECT projection must contain COUNT(...)")


def _sentinel_query(value, kind, snapshots=None):
    if not isinstance(value, str) or not isinstance(kind, str):
        raise FreezeError("sentinel kind and query must be strings")
    if value.startswith("@"):
        raw = _read_small_file(
            value[1:],
            label="sentinel query file",
            limit=MAX_SENTINEL_BYTES,
            snapshots=snapshots,
        )
        try:
            query = raw.decode("utf-8")
        except UnicodeDecodeError as ex:
            raise FreezeError("sentinel query is not UTF-8") from ex
    else:
        query = value
    query = query.strip()
    if not query or len(query.encode("utf-8")) > MAX_SENTINEL_BYTES:
        raise FreezeError("sentinel query is blank or too large")
    _validate_sentinel_form(query, kind)
    return query, kind, hashlib.sha256(query.encode("utf-8")).hexdigest()


def _response_fingerprint(kind, payload):
    try:
        document = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as ex:
        raise FreezeError("sentinel response is not valid SPARQL Results JSON") from ex
    if kind == "ask":
        value = document.get("boolean")
        if type(value) is not bool:
            raise FreezeError("ASK sentinel response has no boolean")
        return sentinel_fingerprint("ask", value)
    rows = document.get("results", {}).get("bindings")
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        raise FreezeError("COUNT sentinel must return exactly one binding row")
    terms = list(rows[0].values())
    if (
        len(terms) != 1
        or not isinstance(terms[0], dict)
        or terms[0].get("type") != "literal"
        or not re.fullmatch(r"\+?\d+", str(terms[0].get("value", "")))
    ):
        raise FreezeError("COUNT sentinel response is not one non-negative integer")
    return sentinel_fingerprint("count", int(terms[0]["value"]))


def _read_bounded(response):
    parts, size = [], 0
    while True:
        block = response.read(min(64 * 1024, MAX_SENTINEL_BYTES + 1 - size))
        if not block:
            break
        parts.append(block)
        size += len(block)
        if size > MAX_SENTINEL_BYTES:
            raise FreezeError("sentinel response exceeds the 1 MiB safety cap")
    return b"".join(parts)


def _network_status(ex):
    reason = getattr(ex, "reason", ex)
    if isinstance(reason, (socket.timeout, TimeoutError)):
        return {"status": "timeout"}
    closed_errnos = {
        errno.ECONNREFUSED, errno.ECONNRESET, errno.EHOSTUNREACH,
        errno.ENETUNREACH,
    }
    if (
        isinstance(reason, ConnectionRefusedError)
        or getattr(reason, "errno", None) in closed_errnos
    ):
        return {"status": "closed"}
    return {"status": "error", "error_type": type(reason).__name__}


class _NoRedirect(U.HTTPRedirectHandler):
    def redirect_request(self, _request, _fp, _code, _msg, _headers, _newurl):
        return None


_NO_REDIRECT_OPENER = U.build_opener(U.ProxyHandler({}), _NoRedirect())


def _open_no_redirect(request, timeout):
    return _NO_REDIRECT_OPENER.open(request, timeout=timeout)


def _probe_once(endpoint, query, kind, timeout, opener=None):
    """One socket-bounded probe; the public wrapper supplies the hard wall."""
    request = U.Request(endpoint, data=query.encode("utf-8"), method="POST")
    request.add_header("Content-Type", "application/sparql-query")
    request.add_header("Accept", "application/sparql-results+json")
    opener = opener or _open_no_redirect
    try:
        with opener(request, timeout=max(0.001, timeout)) as response:
            payload = _read_bounded(response)
        return {
            "status": "ok",
            "observed_fingerprint": _response_fingerprint(kind, payload),
        }
    except urllib.error.HTTPError as ex:
        return {"status": "http", "http_status": int(ex.code)}
    except urllib.error.URLError as ex:
        return _network_status(ex)
    except (socket.timeout, TimeoutError) as ex:
        return _network_status(ex)
    except FreezeError as ex:
        return {"status": "error", "error_type": "InvalidSentinelResponse"}
    except Exception as ex:
        return {"status": "error", "error_type": type(ex).__name__}


def _sentinel_worker(conn, endpoint, query, kind, timeout):
    if os.name == "posix":
        try:
            os.setsid()
        except OSError:
            pass
    try:
        conn.send(_probe_once(endpoint, query, kind, timeout))
    except BaseException as ex:
        conn.send({"status": "error", "error_type": type(ex).__name__})
    finally:
        conn.close()


def _kill(proc):
    if not proc.is_alive():
        return
    if os.name == "posix":
        try:
            if os.getpgid(proc.pid) == proc.pid:
                os.killpg(proc.pid, signal.SIGKILL)
            else:
                proc.kill()
        except OSError:
            try:
                proc.kill()
            except OSError:
                pass
    else:
        try:
            proc.kill()
        except OSError:
            pass
    proc.join(0.05)


def probe_sentinel(endpoint, query, kind, timeout):
    """Probe in a killable process under a true wall-clock deadline."""
    if not math.isfinite(timeout) or timeout <= 0:
        raise FreezeError("sentinel timeout must be positive and finite")
    methods = multiprocessing.get_all_start_methods()
    ctx = multiprocessing.get_context("fork" if "fork" in methods else methods[0])
    recv, send = ctx.Pipe(duplex=False)
    proc = ctx.Process(target=_sentinel_worker, args=(send, endpoint, query, kind, timeout))
    proc.start()
    send.close()
    proc.join(timeout)
    try:
        if proc.is_alive():
            _kill(proc)
            return {"status": "timeout"}
        if not recv.poll():
            return {"status": "error", "error_type": "SentinelWorkerExit"}
        try:
            return recv.recv()
        except EOFError:
            return {"status": "error", "error_type": "SentinelWorkerExit"}
    finally:
        recv.close()
        if proc.is_alive():
            _kill(proc)
        try:
            proc.close()
        except (ValueError, AttributeError):
            pass


def _update_once(endpoint, update, timeout):
    """Execute and fully drain one no-redirect SPARQL Update response."""
    if not math.isfinite(timeout) or timeout <= 0:
        return {"status": "timeout"}
    request = U.Request(endpoint, data=update.encode("utf-8"), method="POST")
    request.add_header("Content-Type", "application/sparql-update")
    request.add_header("Accept", "*/*")
    try:
        with _open_no_redirect(request, timeout=max(0.001, timeout)) as response:
            _read_bounded(response)
        return {"status": "ok"}
    except urllib.error.HTTPError as ex:
        return {"status": "http", "http_status": int(ex.code)}
    except urllib.error.URLError as ex:
        return _network_status(ex)
    except (socket.timeout, TimeoutError) as ex:
        return _network_status(ex)
    except FreezeError:
        return {"status": "error", "error_type": "InvalidUpdateResponse"}
    except Exception as ex:
        return {"status": "error", "error_type": type(ex).__name__}


def _canary_remaining(deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise FreezeError("update canary hard deadline exhausted")
    return remaining


def _canary_worker(conn, reified_endpoint, update_endpoint, subject, deadline):
    if os.name == "posix":
        try:
            os.setsid()
        except OSError:
            pass
    triple = f"<{subject}> <urn:sc:freeze-canary> \"1\" ."
    insert = f"INSERT DATA {{ {triple} }}"
    delete = f"DELETE DATA {{ {triple} }}"
    ask = f"ASK {{ {triple} }}"
    cleanup_needed = True
    try:
        inserted = _update_once(update_endpoint, insert, _canary_remaining(deadline))
        if inserted.get("status") != "ok":
            raise FreezeError("update canary INSERT failed")
        visible = _probe_once(
            reified_endpoint, ask, "ask", _canary_remaining(deadline)
        )
        if visible.get("status") != "ok" or visible.get(
            "observed_fingerprint"
        ) != sentinel_fingerprint("ask", True):
            raise FreezeError("update canary INSERT is not visible in reified store")
        deleted = _update_once(update_endpoint, delete, _canary_remaining(deadline))
        if deleted.get("status") != "ok":
            raise FreezeError("update canary DELETE failed")
        absent = _probe_once(
            reified_endpoint, ask, "ask", _canary_remaining(deadline)
        )
        if absent.get("status") != "ok" or absent.get(
            "observed_fingerprint"
        ) != sentinel_fingerprint("ask", False):
            raise FreezeError("update canary DELETE is still visible in reified store")
        cleanup_needed = False
        conn.send(("ok",))
    except BaseException as ex:
        cleanup_status = "not-attempted"
        if cleanup_needed:
            try:
                cleanup = _update_once(
                    update_endpoint, delete, _canary_remaining(deadline)
                )
                cleanup_status = cleanup.get("status", "error")
            except BaseException:
                cleanup_status = "error"
        conn.send(("error", type(ex).__name__, cleanup_status))
    finally:
        conn.close()


def _update_worker(conn, endpoint, body, timeout):
    if os.name == "posix":
        try:
            os.setsid()
        except OSError:
            pass
    try:
        conn.send(_update_once(endpoint, body, timeout))
    except BaseException as ex:
        conn.send({"status": "error", "error_type": type(ex).__name__})
    finally:
        conn.close()


def _hard_update(endpoint, body, timeout):
    """Best-effort cleanup request that itself cannot exceed a hard wall."""
    ctx = multiprocessing.get_context(
        "fork" if "fork" in multiprocessing.get_all_start_methods()
        else multiprocessing.get_all_start_methods()[0]
    )
    recv, send = ctx.Pipe(duplex=False)
    proc = ctx.Process(target=_update_worker, args=(send, endpoint, body, timeout))
    proc.start()
    send.close()
    proc.join(timeout)
    try:
        if proc.is_alive():
            _kill(proc)
            return {"status": "timeout"}
        if not recv.poll():
            return {"status": "error", "error_type": "UpdateWorkerExit"}
        try:
            return recv.recv()
        except EOFError:
            return {"status": "error", "error_type": "UpdateWorkerExit"}
    finally:
        recv.close()
        if proc.is_alive():
            _kill(proc)
        try:
            proc.close()
        except (ValueError, AttributeError):
            pass


@contextlib.contextmanager
def _store_lock(store_identity, timeout):
    """Serialize canary mutations for one privacy-safe frozen store identity."""
    if not math.isfinite(timeout) or timeout <= 0:
        raise FreezeError("store lock timeout must be positive and finite")
    if not isinstance(store_identity, str) or not SHA256.fullmatch(store_identity):
        raise FreezeError("store lock identity must be a SHA-256 digest")
    directory = Path(
        os.environ.get(
            "SPARQLCIRC_FREEZE_LOCK_DIR",
            f"/tmp/sparqlcirc-freeze-store-locks-{os.getuid()}",
        )
    )
    directory_descriptor = None
    descriptor = None
    try:
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        directory_stat = os.lstat(directory)
        if (
            not stat.S_ISDIR(directory_stat.st_mode)
            or directory_stat.st_uid != os.getuid()
            or directory_stat.st_mode & 0o022
        ):
            raise OSError("unsafe lock directory")
        directory_descriptor = os.open(
            directory,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        opened_directory = os.fstat(directory_descriptor)
        if _directory_identity(opened_directory) != _directory_identity(directory_stat):
            raise OSError("unstable lock directory")
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(
            f"{store_identity}.lock",
            flags,
            0o600,
            dir_fd=directory_descriptor,
        )
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_uid != os.getuid()
            or opened.st_nlink != 1
            or opened.st_mode & 0o077
        ):
            raise OSError("unsafe lock file")
    except OSError as ex:
        if descriptor is not None:
            os.close(descriptor)
        if directory_descriptor is not None:
            os.close(directory_descriptor)
        raise FreezeError("store lock setup failed") from ex
    handle = os.fdopen(descriptor, "a+")
    started = time.monotonic()

    def acquire(lock_descriptor):
        while True:
            try:
                fcntl.flock(lock_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return
            except BlockingIOError:
                if time.monotonic() - started >= timeout:
                    raise FreezeError("store lock hard deadline exhausted")
                time.sleep(min(0.05, timeout / 10.0))

    try:
        # The directory lock prevents an unlinked/recreated per-store lock file
        # from creating two cooperative critical sections in the same directory.
        acquire(directory_descriptor)
        acquire(handle.fileno())
        yield
    finally:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            try:
                fcntl.flock(directory_descriptor, fcntl.LOCK_UN)
            finally:
                handle.close()
                os.close(directory_descriptor)


def run_update_canary(reified_endpoint, update_endpoint, store_identity, timeout):
    """Prove a writable update endpoint targets the registered reified store."""
    if not math.isfinite(timeout) or timeout <= 0:
        raise FreezeError("update canary timeout must be positive and finite")
    started = time.monotonic()
    overall_deadline = started + timeout
    cleanup_reserve = min(5.0, max(0.01, timeout * 0.2))
    operation_deadline = overall_deadline - cleanup_reserve
    subject = "urn:sc:freeze-canary:" + secrets.token_hex(24)
    triple = f"<{subject}> <urn:sc:freeze-canary> \"1\" ."
    delete = f"DELETE DATA {{ {triple} }}"
    lock_budget = operation_deadline - time.monotonic()
    if lock_budget <= 0:
        raise FreezeError("update canary hard deadline exhausted before lock")
    with _store_lock(store_identity, lock_budget):
        if time.monotonic() >= operation_deadline:
            raise FreezeError("update canary hard deadline exhausted after lock")
        ctx = multiprocessing.get_context(
            "fork" if "fork" in multiprocessing.get_all_start_methods()
            else multiprocessing.get_all_start_methods()[0]
        )
        recv, send = ctx.Pipe(duplex=False)
        proc = ctx.Process(
            target=_canary_worker,
            args=(
                send,
                reified_endpoint,
                update_endpoint,
                subject,
                operation_deadline,
            ),
        )
        proc.start()
        send.close()
        proc.join(max(0.0, operation_deadline - time.monotonic()))
        failure = None
        try:
            if proc.is_alive() or time.monotonic() > operation_deadline:
                _kill(proc)
                failure = "timeout"
            elif not recv.poll():
                failure = "worker-exit"
            else:
                try:
                    message = recv.recv()
                except EOFError:
                    message = ("error", "CanaryWorkerExit", "unknown")
                if not message or message[0] != "ok":
                    failure = "protocol"
        finally:
            recv.close()
            if proc.is_alive():
                _kill(proc)
            try:
                proc.close()
            except (ValueError, AttributeError):
                pass
        if failure is not None:
            # The worker may have been killed after the server committed INSERT.
            # Make one separately hard-bounded exact cleanup attempt while the
            # store lock is still held, then fail closed regardless of outcome.
            cleanup_budget = overall_deadline - time.monotonic() - 0.05
            cleanup = (
                _hard_update(update_endpoint, delete, cleanup_budget)
                if cleanup_budget > 0
                else {"status": "deadline"}
            )
            raise FreezeError(
                "writable store update canary failed; cleanup="
                + str(cleanup.get("status", "error"))
            )
    return {
        "protocol": CANARY_PROTOCOL,
        "insert_visible": True,
        "delete_invisible": True,
    }


def run_sentinels(specs, stores, endpoint_urls, timeout, snapshots=None):
    store_keys = {(item["engine"], item["scale"]) for item in stores}
    records, seen = [], set()
    for values in specs:
        if not isinstance(values, (list, tuple)) or len(values) != 6:
            raise FreezeError("sentinel specification must contain exactly six fields")
        if any(not isinstance(value, str) for value in values):
            raise FreezeError("sentinel specification fields must be strings")
        engine, scale, role, kind, query_arg, expected = values
        key = (engine, scale)
        if key not in store_keys or role not in ("base", "reified"):
            raise FreezeError("sentinel references an unknown store or non-query role")
        if not SHA256.fullmatch(expected):
            raise FreezeError(f"sentinel {engine}/{scale}/{role} has invalid expected fingerprint")
        query, kind, query_sha = _sentinel_query(query_arg, kind, snapshots=snapshots)
        identity = (engine, scale, role, kind, query_sha)
        if identity in seen:
            raise FreezeError(f"duplicate sentinel for {engine}/{scale}/{role}")
        seen.add(identity)
        observed = probe_sentinel(endpoint_urls[(engine, scale, role)], query, kind, timeout)
        status = observed.get("status")
        if status != "ok":
            suffix = f" HTTP {observed.get('http_status')}" if status == "http" else ""
            raise FreezeError(f"sentinel {engine}/{scale}/{role} failed: {status}{suffix}")
        if observed.get("observed_fingerprint") != expected:
            raise FreezeError(f"sentinel {engine}/{scale}/{role} fingerprint mismatch")
        records.append({
            "engine": engine,
            "scale": scale,
            "role": role,
            "kind": kind,
            "query_sha256": query_sha,
            "expected_fingerprint": expected,
            "observed_fingerprint": observed["observed_fingerprint"],
        })
    covered = {(item["engine"], item["scale"], item["role"]) for item in records}
    for engine, scale in store_keys:
        for role in ("base", "reified"):
            if (engine, scale, role) not in covered:
                raise FreezeError(f"store {engine}/{scale} lacks a successful {role} sentinel")
        paired = {}
        for item in records:
            if (item["engine"], item["scale"]) == (engine, scale):
                paired.setdefault((item["kind"], item["query_sha256"]), {})[
                    item["role"]
                ] = item
        distinguished = False
        for roles in paired.values():
            if "base" not in roles or "reified" not in roles:
                continue
            base = roles["base"]
            reified = roles["reified"]
            if (
                base["expected_fingerprint"] != reified["expected_fingerprint"]
                and base["observed_fingerprint"] != reified["observed_fingerprint"]
            ):
                distinguished = True
                break
        if not distinguished:
            raise FreezeError(
                f"store {engine}/{scale} lacks a role-discriminating sentinel pair"
            )
    return sorted(records, key=lambda item: (
        item["engine"], item["scale"], item["role"], item["query_sha256"]
    ))


def _git_top_level(path):
    try:
        result = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
            env={"PATH": os.environ.get("PATH", ""), "LC_ALL": "C"},
        )
    except (OSError, subprocess.TimeoutExpired) as ex:
        raise FreezeError("Git top-level probe failed") from ex
    try:
        top_level = Path(result.stdout.strip()).resolve(strict=True)
    except OSError as ex:
        raise FreezeError("Git top-level probe returned an invalid path") from ex
    if result.returncode != 0 or not top_level.is_dir():
        raise FreezeError("path is not inside a Git worktree")
    return top_level


def validate_repository_binding(repo_root):
    """Bind --repo-root to the checkout containing this producer's source."""
    source_repository = _git_top_level(Path(__file__).resolve().parent)
    requested_repository = _git_top_level(repo_root)
    try:
        same = os.path.samefile(source_repository, requested_repository)
    except OSError as ex:
        raise FreezeError("repository identity comparison failed") from ex
    if not same:
        raise FreezeError("--repo-root is not the producer source repository")
    try:
        producer = Path(__file__).resolve(strict=True).relative_to(source_repository)
        tracked = subprocess.run(
            [
                "git", "-C", str(source_repository), "ls-files",
                "--error-unmatch", "--", producer.as_posix(),
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
            env={"PATH": os.environ.get("PATH", ""), "LC_ALL": "C"},
        )
    except (OSError, ValueError, subprocess.TimeoutExpired) as ex:
        raise FreezeError("producer source tracking probe failed") from ex
    if tracked.returncode != 0:
        raise FreezeError("frozen-input producer source is not Git-tracked")
    return source_repository


def _require_in_repository(path, repository, label, *, directory=False):
    try:
        resolved = Path(path).resolve(strict=True)
        resolved.relative_to(repository)
    except (OSError, ValueError) as ex:
        raise FreezeError(f"{label}: path must be inside the producer repository") from ex
    valid = resolved.is_dir() if directory else resolved.is_file()
    if not valid:
        raise FreezeError(f"{label}: path has the wrong filesystem type")
    return resolved


def git_identity(repo_root, *, allowed_untracked=()):
    def run(*args):
        try:
            result = subprocess.run(
                ["git", "-C", str(repo_root), *args], capture_output=True,
                text=True, timeout=10, check=False,
                env={"PATH": os.environ.get("PATH", ""), "LC_ALL": "C"},
            )
        except (OSError, subprocess.TimeoutExpired) as ex:
            raise FreezeError("Git identity probe failed") from ex
        return result

    def head_commit():
        head = run("rev-parse", "HEAD")
        commit = head.stdout.strip()
        if head.returncode != 0 or not re.fullmatch(r"[0-9a-f]{40}", commit):
            raise FreezeError("repository has no full Git commit")
        return commit

    before = head_commit()
    status_result = run(
        "status", "--porcelain=v1", "-z", "--untracked-files=all",
        "--ignore-submodules=none"
    )
    if status_result.returncode != 0:
        raise FreezeError("Git clean-state probe failed")
    allowed_records = set()
    if allowed_untracked:
        repository = _git_top_level(repo_root)
        for path in allowed_untracked:
            try:
                relative = Path(path).resolve(strict=True).relative_to(repository)
            except ValueError:
                continue
            except OSError as ex:
                raise FreezeError(
                    "allowed staging path disappeared during Git probe"
                ) from ex
            allowed_records.add("?? " + relative.as_posix())
    dirty_count = sum(
        record not in allowed_records
        for record in status_result.stdout.split("\0")
        if record
    )
    if dirty_count:
        raise FreezeError(f"Git worktree is not clean ({dirty_count} entries)")
    index_flags = run("ls-files", "-v", "-z")
    if index_flags.returncode != 0:
        raise FreezeError("Git index flag probe failed")
    if any(
        not record.startswith("H ")
        for record in index_flags.stdout.split("\0")
        if record
    ):
        raise FreezeError("Git index contains hidden-worktree flags")
    after = head_commit()
    if after != before:
        raise FreezeError("Git HEAD changed during clean-state validation")
    return {"commit": before, "clean": True}


def validate_formal_output_location(output, repo_root):
    """Require a formal artifact to live outside Git or at an ignored path."""
    output_path = Path(output)
    if output_path.name in ("", ".", "..") or any(
        ord(character) < 32 or ord(character) == 127
        for character in output_path.name
    ):
        raise FreezeError("formal output filename is invalid")
    try:
        repository = _git_top_level(repo_root)
        target = output_path.parent.resolve(strict=True) / output_path.name
        relative = target.relative_to(repository)
    except ValueError:
        return
    except (OSError, subprocess.TimeoutExpired) as ex:
        raise FreezeError("formal output path is invalid") from ex
    if relative.parts and relative.parts[0] == ".git":
        raise FreezeError("formal output must not be placed in Git metadata")

    def run(*arguments):
        try:
            return subprocess.run(
                ["git", "-C", str(repository), *arguments],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
                env={"PATH": os.environ.get("PATH", ""), "LC_ALL": "C"},
            )
        except (OSError, subprocess.TimeoutExpired) as ex:
            raise FreezeError("formal output Git policy probe failed") from ex

    rendered = relative.as_posix()
    tracked = run("ls-files", "--error-unmatch", "--", rendered)
    if tracked.returncode == 0:
        raise FreezeError("formal output must not overwrite a tracked path")
    if tracked.returncode not in (0, 1):
        raise FreezeError("formal output tracked-state probe failed")
    ignored = run("check-ignore", "-q", "--no-index", "--", rendered)
    if ignored.returncode != 0:
        if ignored.returncode == 1:
            raise FreezeError("formal output inside the repository must be Git-ignored")
        raise FreezeError("formal output ignore-state probe failed")


def _validate_protocol(protocol):
    if (
        not isinstance(protocol, str)
        or not protocol
        or protocol.strip() != protocol
        or len(protocol) > 128
        or any(ord(char) < 32 for char in protocol)
    ):
        raise FreezeError("protocol is blank, too long, or contains control characters")


def canonical_batch_id(identity):
    """Public canonical digest shared by producers and harness validators."""
    if not isinstance(identity, dict):
        raise FreezeError("frozen batch identity must be a JSON object")
    return _digest(identity)


def build_batch(
    protocol,
    git,
    manifests,
    data_files,
    stores,
    sentinels,
    tools,
    *,
    batch_profile="formal",
):
    _validate_protocol(protocol)
    if batch_profile not in ("formal", "exploratory"):
        raise FreezeError("batch profile must be formal or exploratory")
    identity = {
        "identity_schema_version": SCHEMA_VERSION,
        "batch_profile": batch_profile,
        "protocol": protocol,
        "git": git,
        "manifests": sorted(manifests, key=lambda item: item["kind"]),
        "data_files": sorted(data_files, key=lambda item: item["name"]),
        "stores": sorted(stores, key=lambda item: (item["engine"], item["scale"])),
        "store_sentinels": sorted(sentinels, key=lambda item: (
            item["engine"], item["scale"], item["role"], item["query_sha256"]
        )),
        "tool_binaries": sorted(tools, key=lambda item: item["name"]),
    }
    return {
        "schema": SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "batch_id": canonical_batch_id(identity),
        "identity": identity,
    }


def _reject_json_constant(_value):
    raise FreezeError("frozen batch contains a non-finite JSON value")


def _unique_json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise FreezeError("frozen batch JSON contains duplicate object keys")
        result[key] = value
    return result


def _validate_named_hash_records(records, category):
    if not isinstance(records, list):
        raise FreezeError(f"frozen batch {category} must be a list")
    seen, order = set(), []
    for record in records:
        if not isinstance(record, dict) or set(record) != {"name", "bytes", "sha256"}:
            raise FreezeError(f"frozen batch {category} record schema mismatch")
        name = record["name"]
        size = record["bytes"]
        digest = record["sha256"]
        if not isinstance(name, str) or not SAFE_NAME.fullmatch(name) or name in seen:
            raise FreezeError(f"frozen batch {category} has an invalid/duplicate name")
        if (
            type(size) is not int
            or size <= 0
            or not isinstance(digest, str)
            or not SHA256.fullmatch(digest)
        ):
            raise FreezeError(f"frozen batch {category} has an invalid hash record")
        seen.add(name)
        order.append(name)
    if order != sorted(order):
        raise FreezeError(f"frozen batch {category} records are not canonically ordered")
    return {record["name"]: record["sha256"] for record in records}


def _validate_safe_endpoint_record(record, *, optional=False):
    if optional and record is None:
        return
    expected = {
        "endpoint_sha256",
        "origin_sha256",
        "path_sha256",
        "parent_path_sha256",
        "path_leaf_kind",
        "scheme",
        "port",
        "host_class",
    }
    if not isinstance(record, dict) or set(record) != expected:
        raise FreezeError("frozen batch endpoint record schema mismatch")
    for name in (
        "endpoint_sha256", "origin_sha256", "path_sha256", "parent_path_sha256"
    ):
        if not isinstance(record[name], str) or not SHA256.fullmatch(record[name]):
            raise FreezeError("frozen batch endpoint hash is invalid")
    if record["path_leaf_kind"] not in ("query", "update", "statements", "other"):
        raise FreezeError("frozen batch endpoint path witness is invalid")
    if record["scheme"] not in ("http", "https"):
        raise FreezeError("frozen batch endpoint scheme is invalid")
    if type(record["port"]) is not int or not 1 <= record["port"] <= 65535:
        raise FreezeError("frozen batch endpoint port is invalid")
    if record["host_class"] not in ("loopback", "network"):
        raise FreezeError("frozen batch endpoint host class is invalid")


def _validate_frozen_stores(stores, data_records, *, formal):
    if not isinstance(stores, list):
        raise FreezeError("frozen batch stores must be a list")
    seen, order = set(), []
    for store in stores:
        expected = {
            "engine",
            "scale",
            "engine_version",
            "access_mode",
            "base_data_name",
            "reified_data_name",
            "endpoints",
            "update_binding",
            "update_canary",
        }
        if not isinstance(store, dict) or set(store) != expected:
            raise FreezeError("frozen batch store record schema mismatch")
        engine, scale = store["engine"], store["scale"]
        key = (engine, scale)
        if (
            not isinstance(engine, str)
            or not SAFE_NAME.fullmatch(engine)
            or not isinstance(scale, str)
            or not SAFE_NAME.fullmatch(scale)
            or key in seen
        ):
            raise FreezeError("frozen batch has an invalid/duplicate store")
        if not isinstance(store["engine_version"], str) or not SAFE_VERSION.fullmatch(
            store["engine_version"]
        ):
            raise FreezeError("frozen batch engine version is invalid")
        access_mode = store["access_mode"]
        base_data_name = store["base_data_name"]
        reified_data_name = store["reified_data_name"]
        if access_mode not in ACCESS_MODES:
            raise FreezeError("frozen batch store access mode is invalid")
        if (
            not isinstance(base_data_name, str)
            or not SAFE_NAME.fullmatch(base_data_name)
            or not isinstance(reified_data_name, str)
            or not SAFE_NAME.fullmatch(reified_data_name)
            or base_data_name == reified_data_name
        ):
            raise FreezeError("frozen batch store data mapping is invalid")
        if formal and (
            base_data_name not in data_records or reified_data_name not in data_records
        ):
            raise FreezeError("frozen batch store references missing formal data")
        if formal and data_records[base_data_name] == data_records[reified_data_name]:
            raise FreezeError("frozen batch base/reified data are byte-identical")
        endpoints = store["endpoints"]
        if not isinstance(endpoints, dict) or set(endpoints) != {"base", "reified", "update"}:
            raise FreezeError("frozen batch store endpoints schema mismatch")
        _validate_safe_endpoint_record(endpoints["base"])
        _validate_safe_endpoint_record(endpoints["reified"])
        _validate_safe_endpoint_record(endpoints["update"], optional=True)
        if endpoints["base"]["endpoint_sha256"] == endpoints["reified"]["endpoint_sha256"]:
            raise FreezeError("frozen batch base/reified endpoint identity collision")
        binding = store["update_binding"]
        allowed = {
            "absent",
            "canonical-same-as-reified",
            "strict-reified-statements-child",
            "strict-query-update-sibling",
        }
        if not isinstance(binding, str) or binding not in allowed:
            raise FreezeError("frozen batch update binding is invalid")
        if (endpoints["update"] is None) != (binding == "absent"):
            raise FreezeError("frozen batch update endpoint/binding mismatch")
        if access_mode == "read-only" and binding != "absent":
            raise FreezeError("frozen batch read-only store has an update endpoint")
        if access_mode == "writable" and binding == "absent":
            raise FreezeError("frozen batch writable store lacks an update endpoint")
        if binding == "canonical-same-as-reified" and endpoints["update"] != endpoints["reified"]:
            raise FreezeError("frozen batch canonical update binding is inconsistent")
        if binding == "strict-reified-statements-child":
            update = endpoints["update"]
            reified = endpoints["reified"]
            if (
                update["endpoint_sha256"] == reified["endpoint_sha256"]
                or update["origin_sha256"] != reified["origin_sha256"]
                or any(
                    update[name] != reified[name]
                    for name in ("scheme", "port", "host_class")
                )
                or update["parent_path_sha256"] != reified["path_sha256"]
                or update["path_sha256"] == reified["path_sha256"]
                or update["path_leaf_kind"] != "statements"
            ):
                raise FreezeError("frozen batch statements update binding is inconsistent")
        if binding == "strict-query-update-sibling":
            update = endpoints["update"]
            reified = endpoints["reified"]
            if (
                update["endpoint_sha256"] == reified["endpoint_sha256"]
                or update["origin_sha256"] != reified["origin_sha256"]
                or any(
                    update[name] != reified[name]
                    for name in ("scheme", "port", "host_class")
                )
                or update["parent_path_sha256"] != reified["parent_path_sha256"]
                or update["path_sha256"] == reified["path_sha256"]
                or reified["path_leaf_kind"] != "query"
                or update["path_leaf_kind"] != "update"
            ):
                raise FreezeError("frozen batch query/update sibling binding is inconsistent")
        canary = store["update_canary"]
        if access_mode == "read-only":
            if canary is not None:
                raise FreezeError("frozen batch read-only store has update canary evidence")
        elif formal:
            if not isinstance(canary, dict) or set(canary) != {
                "protocol", "insert_visible", "delete_invisible"
            }:
                raise FreezeError("frozen batch writable store lacks update canary evidence")
            if (
                canary["protocol"] != CANARY_PROTOCOL
                or canary["insert_visible"] is not True
                or canary["delete_invisible"] is not True
            ):
                raise FreezeError("frozen batch update canary evidence is invalid")
        elif canary is not None:
            if not isinstance(canary, dict) or set(canary) != {
                "protocol", "insert_visible", "delete_invisible"
            }:
                raise FreezeError("frozen batch update canary evidence is invalid")
            if (
                canary["protocol"] != CANARY_PROTOCOL
                or canary["insert_visible"] is not True
                or canary["delete_invisible"] is not True
            ):
                raise FreezeError("frozen batch update canary evidence is invalid")
        seen.add(key)
        order.append(key)
    if order != sorted(order):
        raise FreezeError("frozen batch stores are not canonically ordered")
    return seen


def _validate_frozen_manifests(manifests):
    if not isinstance(manifests, list):
        raise FreezeError("frozen batch manifests must be a list")
    kinds, order = set(), []
    for manifest in manifests:
        required = {"kind", "schema", "bytes", "sha256", "rows", "queries"}
        if not isinstance(manifest, dict) or set(manifest) != required:
            raise FreezeError("frozen batch manifest record schema mismatch")
        kind = manifest["kind"]
        if not isinstance(kind, str) or kind not in MANIFESTS or kind in kinds:
            raise FreezeError("frozen batch has an invalid/duplicate manifest kind")
        if manifest["schema"] != list(MANIFESTS[kind]["columns"]):
            raise FreezeError("frozen batch manifest column schema mismatch")
        if (
            type(manifest["bytes"]) is not int
            or manifest["bytes"] <= 0
            or not isinstance(manifest["sha256"], str)
            or not SHA256.fullmatch(manifest["sha256"])
            or type(manifest["rows"]) is not int
            or manifest["rows"] <= 0
            or not isinstance(manifest["queries"], list)
            or len(manifest["queries"]) != manifest["rows"]
        ):
            raise FreezeError("frozen batch manifest identity is invalid")
        query_keys = set()
        for query in manifest["queries"]:
            if not isinstance(query, dict) or set(query) != {
                "key", "query_file", "query_sha256"
            }:
                raise FreezeError("frozen batch manifest query schema mismatch")
            key = query["key"]
            if not isinstance(key, dict) or set(key) != set(MANIFESTS[kind]["key"]):
                raise FreezeError("frozen batch manifest query key schema mismatch")
            logical_key = tuple(key[name] for name in MANIFESTS[kind]["key"])
            if (
                any(
                    not isinstance(value, str)
                    or not value
                    or value.strip() != value
                    or any(
                        ord(character) < 32 or ord(character) == 127
                        for character in value
                    )
                    for value in logical_key
                )
                or logical_key in query_keys
            ):
                raise FreezeError("frozen batch manifest query key is invalid/duplicate")
            query_file = query["query_file"]
            query_sha = query["query_sha256"]
            try:
                _canonical_query_path(query_file, "frozen batch manifest")
            except FreezeError as ex:
                raise FreezeError(
                    "frozen batch manifest query identity is invalid"
                ) from ex
            if not isinstance(query_sha, str) or not SHA256.fullmatch(query_sha):
                raise FreezeError("frozen batch manifest query identity is invalid")
            query_keys.add(logical_key)
        if manifest["queries"] != sorted(
            manifest["queries"], key=lambda item: _json_bytes(item["key"])
        ):
            raise FreezeError("frozen batch manifest queries are not canonically ordered")
        kinds.add(kind)
        order.append(kind)
    if kinds != set(MANIFESTS):
        raise FreezeError("frozen batch must contain workload and path manifests")
    if order != sorted(order):
        raise FreezeError("frozen batch manifests are not canonically ordered")


def _validate_frozen_sentinels(sentinels, store_keys):
    if not isinstance(sentinels, list):
        raise FreezeError("frozen batch store_sentinels must be a list")
    seen = set()
    grouped = {}
    expected_keys = {
        "engine", "scale", "role", "kind", "query_sha256",
        "expected_fingerprint", "observed_fingerprint",
    }
    for item in sentinels:
        if not isinstance(item, dict) or set(item) != expected_keys:
            raise FreezeError("frozen batch sentinel record schema mismatch")
        engine, scale = item["engine"], item["scale"]
        if (
            not isinstance(engine, str)
            or not SAFE_NAME.fullmatch(engine)
            or not isinstance(scale, str)
            or not SAFE_NAME.fullmatch(scale)
        ):
            raise FreezeError("frozen batch sentinel store identity is invalid")
        store_key = (engine, scale)
        key = store_key + (item["role"], item["kind"], item["query_sha256"])
        if (
            store_key not in store_keys
            or item["role"] not in ("base", "reified")
            or item["kind"] not in ("ask", "count")
            or not isinstance(item["query_sha256"], str)
            or not SHA256.fullmatch(item["query_sha256"])
            or key in seen
        ):
            raise FreezeError("frozen batch sentinel identity is invalid/duplicate")
        expected = item["expected_fingerprint"]
        observed = item["observed_fingerprint"]
        if (
            not isinstance(expected, str)
            or not SHA256.fullmatch(expected)
            or not isinstance(observed, str)
            or not SHA256.fullmatch(observed)
            or observed != expected
        ):
            raise FreezeError("frozen batch sentinel fingerprint is invalid")
        if item["kind"] == "ask" and expected not in {
            sentinel_fingerprint("ask", False),
            sentinel_fingerprint("ask", True),
        }:
            raise FreezeError("frozen batch ASK sentinel fingerprint is impossible")
        grouped.setdefault(store_key + (item["kind"], item["query_sha256"]), {})[
            item["role"]
        ] = item
        seen.add(key)
    for store_key in store_keys:
        distinguished = False
        for group_key, roles in grouped.items():
            if group_key[:2] != store_key or set(roles) != {"base", "reified"}:
                continue
            if (
                roles["base"]["expected_fingerprint"]
                != roles["reified"]["expected_fingerprint"]
                and roles["base"]["observed_fingerprint"]
                != roles["reified"]["observed_fingerprint"]
            ):
                distinguished = True
                break
        if not distinguished:
            raise FreezeError("frozen batch store lacks a role-discriminating sentinel pair")
    if sentinels != sorted(sentinels, key=lambda item: (
        item["engine"], item["scale"], item["role"], item["query_sha256"]
    )):
        raise FreezeError("frozen batch sentinels are not canonically ordered")


def frozen_tool(document, name):
    """Return one validated named tool hash without reimplementing lookup rules."""
    for record in document["identity"]["tool_binaries"]:
        if record["name"] == name:
            return record
    raise FreezeError("frozen batch does not contain the requested tool")


def frozen_data(document, name):
    """Return one validated named data hash."""
    for record in document["identity"]["data_files"]:
        if record["name"] == name:
            return record
    raise FreezeError("frozen batch does not contain the requested data")


def frozen_store(document, engine, scale):
    """Return one validated engine/scale store identity."""
    for record in document["identity"]["stores"]:
        if (record["engine"], record["scale"]) == (engine, scale):
            return record
    raise FreezeError("frozen batch does not contain the requested store")


def load_frozen_batch(
    path,
    *,
    expected_commit=None,
    expected_protocol=None,
    required_data=(),
    required_tools=(),
    required_stores=(),
    require_formal=True,
    _document=None,
):
    """Load and strictly validate a frozen batch for reuse by experiment harnesses."""
    if _document is None:
        payload = _read_single_link_file(
            path, label="frozen batch", limit=MAX_FROZEN_BATCH_BYTES
        )
        try:
            document = json.loads(
                payload.decode("utf-8"),
                object_pairs_hook=_unique_json_object,
                parse_constant=_reject_json_constant,
            )
        except FreezeError:
            raise
        except (UnicodeDecodeError, json.JSONDecodeError) as ex:
            raise FreezeError("frozen batch is not valid UTF-8 JSON") from ex
    else:
        document = _document
    if not isinstance(document, dict) or set(document) != {
        "schema", "schema_version", "batch_id", "identity"
    }:
        raise FreezeError("frozen batch top-level schema mismatch")
    if (
        document["schema"] != SCHEMA
        or type(document["schema_version"]) is not int
        or document["schema_version"] != SCHEMA_VERSION
    ):
        raise FreezeError("frozen batch schema/version mismatch")
    identity = document["identity"]
    identity_keys = {
        "identity_schema_version", "batch_profile", "protocol", "git", "manifests",
        "data_files", "stores", "store_sentinels", "tool_binaries",
    }
    if not isinstance(identity, dict) or set(identity) != identity_keys:
        raise FreezeError("frozen batch identity schema mismatch")
    if (
        type(identity["identity_schema_version"]) is not int
        or identity["identity_schema_version"] != SCHEMA_VERSION
    ):
        raise FreezeError("frozen batch identity version mismatch")
    profile = identity["batch_profile"]
    if profile not in ("formal", "exploratory"):
        raise FreezeError("frozen batch profile is invalid")
    if require_formal and profile != "formal":
        raise FreezeError("formal harness refuses an exploratory frozen batch")
    batch_id = document["batch_id"]
    if (
        not isinstance(batch_id, str)
        or not SHA256.fullmatch(batch_id)
        or batch_id != canonical_batch_id(identity)
    ):
        raise FreezeError("frozen batch canonical batch_id mismatch")
    _validate_protocol(identity["protocol"])
    git = identity["git"]
    if (
        not isinstance(git, dict)
        or set(git) != {"commit", "clean"}
        or not isinstance(git["commit"], str)
        or not re.fullmatch(r"[0-9a-f]{40}", git["commit"])
        or git["clean"] is not True
    ):
        raise FreezeError("frozen batch Git identity is invalid")
    if expected_commit is not None and git["commit"] != expected_commit:
        raise FreezeError("frozen batch Git commit does not match the expected commit")
    if expected_protocol is not None and identity["protocol"] != expected_protocol:
        raise FreezeError("frozen batch protocol does not match the expected protocol")
    _validate_frozen_manifests(identity["manifests"])
    data_records = _validate_named_hash_records(identity["data_files"], "data_files")
    _validate_named_hash_records(identity["tool_binaries"], "tool_binaries")
    formal = profile == "formal"
    if formal and (
        not identity["data_files"]
        or not identity["tool_binaries"]
        or not identity["stores"]
    ):
        raise FreezeError("formal frozen batch has empty required input categories")
    store_keys = _validate_frozen_stores(
        identity["stores"], data_records, formal=formal
    )
    _validate_frozen_sentinels(identity["store_sentinels"], store_keys)
    for name in required_data:
        frozen_data(document, name)
    for name in required_tools:
        frozen_tool(document, name)
    for engine, scale in required_stores:
        frozen_store(document, engine, scale)
    return document


def _reject_output_alias(output, input_paths):
    try:
        output_path = Path(output)
        output_name = output_path.name
        if not output_name:
            raise OSError("missing output filename")
        target = output_path.parent.resolve(strict=True) / output_name
        target_resolved = target.resolve(strict=False)
    except OSError as ex:
        raise FreezeError("output path or directory is invalid") from ex
    for source in input_paths:
        try:
            source_resolved = Path(source).resolve(strict=True)
        except OSError:
            continue
        if source_resolved == target_resolved:
            raise FreezeError("output aliases a frozen input file")
        try:
            if target.exists() and os.path.samefile(target, source_resolved):
                raise FreezeError("output aliases a frozen input file")
        except OSError as ex:
            raise FreezeError("output alias validation failed") from ex


@contextlib.contextmanager
def _open_output_target(output):
    """Bind an output basename to one open, stable directory inode."""
    source = Path(os.path.abspath(os.fspath(output)))
    if source.name in ("", ".", "..") or any(
        ord(character) < 32 or ord(character) == 127 for character in source.name
    ):
        raise FreezeError("output filename is invalid")
    source_parent = source.parent
    descriptor = None
    try:
        parent = source_parent.resolve(strict=True)
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(
            os, "O_NOFOLLOW", 0
        )
        descriptor = os.open(parent, flags)
        opened = os.fstat(descriptor)
        current = os.stat(parent)
    except OSError as ex:
        if descriptor is not None:
            os.close(descriptor)
        raise FreezeError("output directory is missing or unreadable") from ex
    if (
        not stat.S_ISDIR(opened.st_mode)
        or _directory_identity(opened) != _directory_identity(current)
    ):
        os.close(descriptor)
        raise FreezeError("output directory identity is unstable")
    target = _OutputTarget(
        parent,
        source_parent,
        source.name,
        _directory_identity(opened),
        descriptor,
    )
    try:
        yield target
    finally:
        try:
            os.close(descriptor)
        except OSError as ex:
            if not target.committed:
                raise FreezeError("uncommitted output directory close failed") from ex


def _verify_output_target(target):
    try:
        resolved = target.source_parent.resolve(strict=True)
        opened = os.fstat(target.descriptor)
        current = os.stat(target.parent)
    except OSError as ex:
        raise FreezeError("output directory identity changed") from ex
    if (
        resolved != target.parent
        or _directory_identity(opened) != target.identity
        or _directory_identity(current) != target.identity
    ):
        raise FreezeError("output directory identity changed")


@contextlib.contextmanager
def _output_publication_lock(target):
    """Serialize cooperative publications in one bound output directory."""
    try:
        fcntl.flock(target.descriptor, fcntl.LOCK_EX)
    except OSError as ex:
        raise FreezeError("output publication lock failed") from ex
    try:
        yield
    finally:
        try:
            fcntl.flock(target.descriptor, fcntl.LOCK_UN)
        except OSError as ex:
            if not target.committed:
                raise FreezeError("uncommitted output unlock failed") from ex


def _read_descriptor_matches(descriptor, expected, expected_links):
    before = os.fstat(descriptor)
    if (
        not stat.S_ISREG(before.st_mode)
        or before.st_nlink != expected_links
        or before.st_size != len(expected)
    ):
        return False, before
    os.lseek(descriptor, 0, os.SEEK_SET)
    parts, remaining = [], len(expected) + 1
    while remaining:
        block = os.read(descriptor, min(1024 * 1024, remaining))
        if not block:
            break
        parts.append(block)
        remaining -= len(block)
    after = os.fstat(descriptor)
    return (
        b"".join(parts) == expected
        and _stat_signature(before) == _stat_signature(after),
        after,
    )


def _same_inode(first, second):
    return (first.st_dev, first.st_ino) == (second.st_dev, second.st_ino)


def _existing_output_matches_at(target, expected):
    descriptor = None
    try:
        before = os.stat(
            target.name, dir_fd=target.descriptor, follow_symlinks=False
        )
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_size != len(expected)
        ):
            return False
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(target.name, flags, dir_fd=target.descriptor)
        matches, after = _read_descriptor_matches(descriptor, expected, 1)
        current = os.stat(
            target.name, dir_fd=target.descriptor, follow_symlinks=False
        )
    except OSError as ex:
        raise FreezeError("existing frozen output is unreadable or unstable") from ex
    finally:
        if descriptor is not None:
            os.close(descriptor)
    return (
        matches
        and before.st_nlink == after.st_nlink == current.st_nlink == 1
        and _same_inode(before, after)
        and _stat_signature(after) == _stat_signature(current)
    )


def _existing_output_matches(path, expected):
    with _open_output_target(path) as target:
        return _existing_output_matches_at(target, expected)


def _rollback_created_output(target):
    """Remove the output name created by this transaction, even if replaced."""
    try:
        os.stat(
            target.name, dir_fd=target.descriptor, follow_symlinks=False
        )
        os.unlink(target.name, dir_fd=target.descriptor)
        _fsync_directory_fd(target.descriptor)
    except FileNotFoundError:
        return
    except OSError as ex:
        raise FreezeError("failed to roll back staged frozen output") from ex


def _fsync_directory_fd(descriptor):
    try:
        os.fsync(descriptor)
    except OSError as ex:
        raise FreezeError("frozen output directory sync failed") from ex


def atomic_write_json(path, document, *, staged_validation=None):
    """Publish once, keeping a new target unloadable until validation succeeds."""
    load_frozen_batch(None, require_formal=False, _document=document)
    try:
        data = json.dumps(
            document, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False
        ).encode("utf-8") + b"\n"
    except (TypeError, ValueError) as ex:
        raise FreezeError("frozen output document is not canonical JSON") from ex
    if len(data) > MAX_FROZEN_BATCH_BYTES:
        raise FreezeError("frozen output exceeds the loader safety cap")
    manager = (
        contextlib.nullcontext(path)
        if isinstance(path, _OutputTarget)
        else _open_output_target(path)
    )
    with manager as target, _output_publication_lock(target):
        _verify_output_target(target)
        temporary_name = f".{target.name}.{secrets.token_hex(24)}.tmp"
        temporary_fd = None
        published_fd = None
        created = False
        temporary_exists = False
        published_stat = None
        operation_failed = False
        try:
            flags = (
                os.O_RDWR
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_NOFOLLOW", 0)
            )
            temporary_fd = os.open(
                temporary_name, flags, 0o600, dir_fd=target.descriptor
            )
            temporary_exists = True
            view = memoryview(data)
            while view:
                written = os.write(temporary_fd, view)
                if written <= 0:
                    raise OSError("short frozen output write")
                view = view[written:]
            os.fsync(temporary_fd)
            matches, temporary_stat = _read_descriptor_matches(
                temporary_fd, data, 1
            )
            if not matches:
                raise FreezeError("staged frozen output failed descriptor verification")
            _verify_output_target(target)
            try:
                os.link(
                    temporary_name,
                    target.name,
                    src_dir_fd=target.descriptor,
                    dst_dir_fd=target.descriptor,
                    follow_symlinks=False,
                )
                created = True
            except FileExistsError:
                if not _existing_output_matches_at(target, data):
                    raise FreezeError(
                        "frozen output already exists with conflicting bytes"
                    )
                if staged_validation is not None:
                    staged_validation(target.parent / temporary_name)
                if not _existing_output_matches_at(target, data):
                    raise FreezeError("existing frozen output changed during validation")
                _verify_output_target(target)
                target.committed = True
                return

            published_stat = os.stat(
                target.name, dir_fd=target.descriptor, follow_symlinks=False
            )
            linked_stat = os.fstat(temporary_fd)
            if (
                not _same_inode(temporary_stat, published_stat)
                or not _same_inode(linked_stat, published_stat)
                or linked_stat.st_nlink != 2
            ):
                raise FreezeError("staged frozen output link identity mismatch")
            published_fd = os.open(
                target.name,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=target.descriptor,
            )
            matches, opened_stat = _read_descriptor_matches(
                published_fd, data, 2
            )
            if not matches or not _same_inode(opened_stat, published_stat):
                raise FreezeError("published frozen output failed descriptor verification")
            if staged_validation is not None:
                staged_validation(target.parent / temporary_name)
            _verify_output_target(target)
            current = os.stat(
                target.name, dir_fd=target.descriptor, follow_symlinks=False
            )
            if not _same_inode(current, published_stat):
                raise FreezeError("published frozen output was replaced during validation")

            os.unlink(temporary_name, dir_fd=target.descriptor)
            temporary_exists = False
            target_matches, target_stat = _read_descriptor_matches(
                published_fd, data, 1
            )
            staged_matches, staged_stat = _read_descriptor_matches(
                temporary_fd, data, 1
            )
            current = os.stat(
                target.name, dir_fd=target.descriptor, follow_symlinks=False
            )
            if (
                not target_matches
                or not staged_matches
                or not _same_inode(target_stat, staged_stat)
                or not _same_inode(target_stat, current)
                or _stat_signature(target_stat) != _stat_signature(current)
            ):
                raise FreezeError("final frozen output identity/content verification failed")
            _verify_output_target(target)
            _fsync_directory_fd(target.descriptor)
            target.committed = True
        except FreezeError:
            operation_failed = True
            if created and published_stat is not None:
                _rollback_created_output(target)
            raise
        except OSError as ex:
            operation_failed = True
            if created and published_stat is not None:
                _rollback_created_output(target)
            raise FreezeError("write-once atomic output creation failed") from ex
        except BaseException:
            operation_failed = True
            if created and published_stat is not None:
                _rollback_created_output(target)
            raise
        finally:
            cleanup_error = None
            if temporary_exists:
                remove_temporary = True
                if created and temporary_fd is not None:
                    try:
                        os.stat(
                            temporary_name,
                            dir_fd=target.descriptor,
                            follow_symlinks=False,
                        )
                        current = os.stat(
                            target.name,
                            dir_fd=target.descriptor,
                            follow_symlinks=False,
                        )
                    except FileNotFoundError:
                        pass
                    except OSError as ex:
                        cleanup_error = ex
                        remove_temporary = False
                    else:
                        try:
                            os.unlink(target.name, dir_fd=target.descriptor)
                        except OSError as ex:
                            if cleanup_error is None:
                                cleanup_error = ex
                            expected = published_stat or os.fstat(temporary_fd)
                            if _same_inode(current, expected):
                                # Keep the second link: loader then fails closed
                                # instead of accepting a validation-failed target.
                                remove_temporary = False
                if remove_temporary:
                    try:
                        os.unlink(temporary_name, dir_fd=target.descriptor)
                    except FileNotFoundError:
                        pass
                    except OSError as ex:
                        if cleanup_error is None:
                            cleanup_error = ex
            for descriptor in (published_fd, temporary_fd):
                if descriptor is None:
                    continue
                try:
                    os.close(descriptor)
                except OSError as ex:
                    if cleanup_error is None:
                        cleanup_error = ex
            if (
                cleanup_error is not None
                and not target.committed
                and not operation_failed
            ):
                raise FreezeError(
                    "uncommitted publication cleanup failed"
                ) from cleanup_error


def freeze(args):
    formal = not getattr(args, "allow_empty_inputs", False)
    batch_profile = "formal" if formal else "exploratory"
    if formal:
        missing = [
            option
            for option, values in (
                ("--data", args.data), ("--tool", args.tool), ("--store", args.store)
            )
            if not values
        ]
        if missing:
            raise FreezeError(
                "formal mode requires at least one " + ", ".join(missing)
            )
    repository = validate_repository_binding(args.repo_root)
    query_root = _capture_directory(args.query_root, "query root")
    _require_in_repository(
        query_root.path, repository, "query root", directory=True
    )
    _require_in_repository(args.workload_manifest, repository, "workload manifest")
    _require_in_repository(args.path_manifest, repository, "path manifest")

    with _open_output_target(args.output) as output_target:
        if formal:
            validate_formal_output_location(output_target.path, repository)
        preliminary_inputs = [args.workload_manifest, args.path_manifest]
        for category, values in (
            ("data file", args.data), ("tool binary", args.tool)
        ):
            preliminary_inputs.extend(
                _named_path(value, category)[1] for value in values
            )
        _reject_output_alias(output_target.path, preliminary_inputs)

        initial_git = git_identity(repository)
        snapshots = []
        manifests = [
            validate_manifest(
                args.workload_manifest,
                "workload",
                query_root,
                snapshots=snapshots,
            ),
            validate_manifest(
                args.path_manifest, "path", query_root, snapshots=snapshots
            ),
        ]
        data_files = hash_named_paths(
            args.data, "data file", snapshots=snapshots
        )
        tools = hash_named_paths(
            args.tool, "tool binary", snapshots=snapshots
        )
        stores, endpoint_urls = validate_store_specs(args.store)
        validate_store_data_references(stores, data_files, formal=formal)
        sentinels = run_sentinels(
            args.sentinel,
            stores,
            endpoint_urls,
            args.sentinel_timeout,
            snapshots=snapshots,
        )
        if formal:
            for store in stores:
                if store["access_mode"] != "writable":
                    continue
                key = (store["engine"], store["scale"])
                store["update_canary"] = run_update_canary(
                    endpoint_urls[key + ("reified",)],
                    endpoint_urls[key + ("update",)],
                    store["endpoints"]["reified"]["endpoint_sha256"],
                    args.sentinel_timeout,
                )
        final_git = git_identity(repository)
        if final_git != initial_git:
            raise FreezeError("Git identity changed during frozen-input validation")
        verify_snapshots(snapshots)
        _verify_directory(query_root)
        _verify_output_target(output_target)
        _reject_output_alias(
            output_target.path, [snapshot.path for snapshot in snapshots]
        )
        document = build_batch(
            args.protocol,
            initial_git,
            manifests,
            data_files,
            stores,
            sentinels,
            tools,
            batch_profile=batch_profile,
        )

        def staged_validation(staging_path):
            if formal:
                repeated = run_sentinels(
                    args.sentinel,
                    stores,
                    endpoint_urls,
                    args.sentinel_timeout,
                )
                if repeated != sentinels:
                    raise FreezeError("store sentinel state changed before publication")
                for store in stores:
                    if store["access_mode"] != "writable":
                        continue
                    key = (store["engine"], store["scale"])
                    repeated_canary = run_update_canary(
                        endpoint_urls[key + ("reified",)],
                        endpoint_urls[key + ("update",)],
                        store["endpoints"]["reified"]["endpoint_sha256"],
                        args.sentinel_timeout,
                    )
                    if repeated_canary != store["update_canary"]:
                        raise FreezeError("writable store canary evidence changed")
                validate_formal_output_location(output_target.path, repository)
            post_write_git = git_identity(
                repository, allowed_untracked=(staging_path,)
            )
            if post_write_git != initial_git:
                raise FreezeError("Git identity changed while publishing frozen output")
            verify_snapshots(snapshots)
            _verify_directory(query_root)
            _verify_output_target(output_target)

        atomic_write_json(
            output_target, document, staged_validation=staged_validation
        )
        return document


def parser():
    repo = Path(__file__).resolve().parents[2]
    reference = repo / "reference"
    p = argparse.ArgumentParser(
        description=__doc__,
        epilog=(
            "Repeat --store ENGINE SCALE VERSION ACCESS_MODE BASE_DATA REIFIED_DATA "
            "BASE REIFIED UPDATE (read-only requires '-', writable requires a bound update); "
            "each store requires successful base and reified --sentinel ENGINE SCALE ROLE "
            "KIND QUERY_OR_@FILE EXPECTED_SHA256, including a same-query pair whose role "
            "fingerprints differ. Export the resulting batch_id as PCM_BATCH_ID."
        ),
    )
    p.add_argument("--repo-root", default=str(repo))
    p.add_argument("--query-root", default=str(reference))
    p.add_argument(
        "--workload-manifest",
        default=str(reference / "paper" / "workload_manifest.csv"),
    )
    p.add_argument("--path-manifest", default=str(reference / "paper" / "path_manifest.csv"))
    p.add_argument("--protocol", required=True)
    p.add_argument("--data", action="append", default=[], metavar="NAME=PATH")
    p.add_argument("--tool", action="append", default=[], metavar="NAME=PATH")
    p.add_argument(
        "--store", action="append", nargs=9, default=[],
        metavar=(
            "ENGINE", "SCALE", "VERSION", "ACCESS_MODE", "BASE_DATA",
            "REIFIED_DATA", "BASE", "REIFIED", "UPDATE",
        ),
    )
    p.add_argument(
        "--sentinel", action="append", nargs=6, default=[],
        metavar=(
            "ENGINE", "SCALE", "ROLE", "KIND", "QUERY_OR_@FILE", "EXPECTED_SHA256"
        ),
    )
    p.add_argument("--sentinel-timeout", type=float, default=10.0)
    p.add_argument(
        "--allow-empty-inputs",
        action="store_true",
        help="allow missing data/tool/store only for unit tests or exploratory use",
    )
    p.add_argument("--output", required=True)
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    if not math.isfinite(args.sentinel_timeout) or args.sentinel_timeout <= 0:
        parser().error("--sentinel-timeout must be positive and finite")
    try:
        document = freeze(args)
    except FreezeError as ex:
        print(f"freeze_inputs: ERROR: {ex}", file=sys.stderr)
        return 2
    except Exception as ex:
        print(f"freeze_inputs: INTERNAL ERROR: {type(ex).__name__}", file=sys.stderr)
        return 3
    print(document["batch_id"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
