#!/usr/bin/env python3
"""Capture a privacy-safe, reproducible SPARQLcirc experiment environment.

The collector uses only the Python standard library.  It deliberately never
serializes host names, user names, home directories, raw paths, URL user-info,
or arbitrary environment variables.  Paths are inputs and appear only through
stable logical labels in the output.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import platform
import re
import resource
import shutil
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlsplit


SCHEMA_VERSION = 1
SAFE_VALUE = re.compile(r"^[A-Za-z0-9_.:+-]{1,64}$")
PINNED_D4V2_SHA256 = "9b2ca0a3969ea61d159e1cc5ace20f675346a83cc75fbd9dc7c902d8597bbad5"

SERVICES = {
    "graphdb": {"port": 7200, "role": "primary RDF engine"},
    "fuseki": {"port": 3030, "role": "SPARQLprov-compatible baseline"},
    "oxigraph": {"port": 7878, "role": "cross-engine validation"},
    "qlever": {"port": 7001, "role": "read-only scale validation"},
    "millenniumdb": {"port": 1234, "role": "read-only comparison"},
    "virtuoso": {"port": 8890, "role": "SPARQLprov native baseline"},
    "stardog": {"port": 5820, "role": "NPCS native baseline"},
    "postgresql_provsql": {"port": 54320, "role": "ProvSQL baseline"},
}

DATA_FILES = {
    "watdiv_10m_base": "watdiv-data/watdiv.10M.nt",
    "watdiv_10m_reified": "watdiv-data/watdiv.10M.reified.nt",
    "watdiv_100m_base": "watdiv-data/watdiv.100M.nt",
    "watdiv_100m_reified": "watdiv-data/watdiv.100M.reified.nt",
    "watdiv_200m_base": "watdiv-data/watdiv.200M.nt",
    "watdiv_200m_reified": "watdiv-data/watdiv.200M.reified.nt",
    "tpch_sf0_01_rdf": "tpch-data/tpch.sf001.nt",
    "tpch_sf0_1_rdf": "tpch-data/tpch.sf01.nt",
    "tpch_sf1_rdf": "tpch-data/tpch.sf1.nt",
}

GRAPHDB_REPOSITORIES = {
    "gallery": "small correctness fixture",
    "test": "scratch repository",
    "tpch001": "TPC-H SF 0.01",
    "tpch01": "TPC-H SF 0.1",
    "watdivbase": "WatDiv 10M base",
    "watdiv": "WatDiv 10M reified",
    "watdiv100m": "WatDiv 100M repository (pair role must be validated)",
    "wdpaths": "Wikidata path workload",
    "wikidata": "full filtered/reified Wikidata",
}


def _run(argv: list[str], timeout: float = 5.0,
         extra_env: dict[str, str] | None = None) -> tuple[int | None, str]:
    """Run without a shell and return a bounded, normalized output string."""
    try:
        environment = {"PATH": os.environ.get("PATH", ""), "LC_ALL": "C"}
        if extra_env:
            environment.update(extra_env)
        proc = subprocess.run(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout,
            check=False,
            env=environment,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None, ""
    return proc.returncode, proc.stdout.replace("\r", "").strip()[:8192]


def _git(path: Path) -> dict[str, Any]:
    record: dict[str, Any] = {"configured": True, "present": path.is_dir()}
    if not path.is_dir():
        return record
    rc, head = _run(["git", "-C", str(path), "rev-parse", "HEAD"])
    if rc != 0 or not re.fullmatch(r"[0-9a-f]{40}", head):
        record["git_checkout"] = False
        return record
    record["git_checkout"] = True
    record["revision"] = head
    rc, branch = _run(["git", "-C", str(path), "branch", "--show-current"])
    record["branch"] = branch if rc == 0 and SAFE_VALUE.fullmatch(branch) else "detached-or-redacted"
    rc, status = _run(["git", "-C", str(path), "status", "--porcelain"])
    if rc == 0:
        lines = [line for line in status.splitlines() if line]
        record.update({"dirty": bool(lines), "dirty_entry_count": len(lines)})
    return record


def _sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def _tree_size(path: Path) -> int | None:
    total = 0
    try:
        for root, _dirs, files in os.walk(path):
            for name in files:
                try:
                    total += (Path(root) / name).stat().st_size
                except OSError:
                    pass
    except OSError:
        return None
    return total


def _path_record(path: Path | None, *, hash_file: bool = False,
                 scan_size: bool = False) -> dict[str, Any]:
    record: dict[str, Any] = {"configured": path is not None}
    if path is None:
        return record
    exists = path.exists()
    record["present"] = exists
    if not exists:
        return record
    record["kind"] = "directory" if path.is_dir() else "file" if path.is_file() else "other"
    if path.is_file():
        try:
            record["bytes"] = path.stat().st_size
        except OSError:
            pass
        if hash_file:
            record["sha256"] = _sha256(path)
        record["executable"] = os.access(path, os.X_OK)
    elif scan_size:
        record["apparent_bytes"] = _tree_size(path)
    return record


def _file_dataset(path: Path) -> dict[str, Any]:
    record = _path_record(path)
    size = record.get("bytes", 0)
    record["usable"] = bool(record.get("present") and record.get("kind") == "file" and size > 0)
    if record.get("present") and size == 0:
        record["reason"] = "zero-byte placeholder/incomplete"
    return record


def _read_key_values(path: Path, delimiter: str = ":") -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        for line in path.read_text(errors="replace").splitlines():
            if delimiter in line:
                key, value = line.split(delimiter, 1)
                values[key.strip()] = value.strip()
    except OSError:
        pass
    return values


def _cpu_info() -> dict[str, Any]:
    processors: list[dict[str, str]] = []
    current: dict[str, str] = {}
    try:
        for line in Path("/proc/cpuinfo").read_text(errors="replace").splitlines():
            if not line.strip():
                if current:
                    processors.append(current)
                    current = {}
            elif ":" in line:
                key, value = line.split(":", 1)
                current[key.strip()] = value.strip()
        if current:
            processors.append(current)
    except OSError:
        pass

    try:
        affinity = sorted(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        affinity = list(range(os.cpu_count() or 0))
    physical_pairs = {
        (p.get("physical id"), p.get("core id"))
        for p in processors
        if p.get("physical id") is not None and p.get("core id") is not None
    }
    sockets = {p.get("physical id") for p in processors if p.get("physical id") is not None}
    model = next((p.get("model name") for p in processors if p.get("model name")), None)
    cache_groups: set[tuple[str, str]] = set()
    for cache in Path("/sys/devices/system/cpu").glob("cpu[0-9]*/cache/index3"):
        try:
            cache_groups.add(((cache / "shared_cpu_list").read_text().strip(), (cache / "size").read_text().strip()))
        except OSError:
            pass
    l3_total = sum(_size_text(size) or 0 for _cpus, size in cache_groups) or None
    try:
        numa_online = (Path("/sys/devices/system/node") / "online").read_text().strip()
        numa_nodes = len(_expand_ranges(numa_online))
    except OSError:
        numa_nodes = None
    return {
        "model": model,
        "logical_cpus_visible": os.cpu_count(),
        "logical_cpus_allowed": len(affinity),
        "allowed_cpu_list": _range_list(affinity),
        "physical_cores_visible": len(physical_pairs) or None,
        "sockets_visible": len(sockets) or None,
        "threads_per_core_visible": round(len(processors) / len(physical_pairs), 3) if physical_pairs else None,
        "l3_cache_bytes_visible": l3_total,
        "numa_nodes_visible": numa_nodes,
    }


def _range_list(numbers: Iterable[int]) -> str:
    values = sorted(set(numbers))
    if not values:
        return ""
    groups: list[str] = []
    start = previous = values[0]
    for value in values[1:]:
        if value == previous + 1:
            previous = value
            continue
        groups.append(str(start) if start == previous else f"{start}-{previous}")
        start = previous = value
    groups.append(str(start) if start == previous else f"{start}-{previous}")
    return ",".join(groups)


def _expand_ranges(text: str) -> set[int]:
    values: set[int] = set()
    for part in text.split(","):
        if not part:
            continue
        if "-" in part:
            start, end = (int(value) for value in part.split("-", 1))
            values.update(range(start, end + 1))
        else:
            values.add(int(part))
    return values


def _size_text(text: str) -> int | None:
    match = re.fullmatch(r"(\d+)([KMG]?)", text.strip(), re.I)
    if not match:
        return None
    multiplier = {"": 1, "K": 1024, "M": 1024 ** 2, "G": 1024 ** 3}[match.group(2).upper()]
    return int(match.group(1)) * multiplier


def _memory_info(include_volatile: bool) -> dict[str, Any]:
    raw = _read_key_values(Path("/proc/meminfo"))

    def kib(name: str) -> int | None:
        match = re.match(r"(\d+)\s+kB", raw.get(name, ""))
        return int(match.group(1)) * 1024 if match else None

    result = {"total_bytes": kib("MemTotal"), "swap_total_bytes": kib("SwapTotal")}
    if include_volatile:
        result["available_bytes_at_capture"] = kib("MemAvailable")
    return result


def _os_info() -> dict[str, Any]:
    release: dict[str, str] = {}
    try:
        for line in Path("/etc/os-release").read_text(errors="replace").splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                release[key] = value.strip().strip('"')
    except OSError:
        pass
    return {
        "distribution": release.get("PRETTY_NAME") or release.get("NAME"),
        "kernel": platform.release(),
        "architecture": platform.machine(),
    }


def _limit(value: int) -> int | str:
    return "unlimited" if value == resource.RLIM_INFINITY else value


def _limits() -> dict[str, Any]:
    names = {
        "open_files": resource.RLIMIT_NOFILE,
        "stack_bytes": resource.RLIMIT_STACK,
        "address_space_bytes": resource.RLIMIT_AS,
        "cpu_seconds": resource.RLIMIT_CPU,
    }
    return {
        name: {"soft": _limit(resource.getrlimit(kind)[0]), "hard": _limit(resource.getrlimit(kind)[1])}
        for name, kind in names.items()
    }


def _cgroup() -> dict[str, Any]:
    root = Path("/sys/fs/cgroup")
    relative = Path(".")
    try:
        for line in Path("/proc/self/cgroup").read_text().splitlines():
            parts = line.split(":", 2)
            if len(parts) == 3 and parts[0] == "0":
                relative = Path(parts[2].lstrip("/"))
                break
    except OSError:
        pass
    base = root / relative

    def read(name: str) -> str | None:
        candidates = [base]
        candidates.extend(parent for parent in base.parents if parent == root or root in parent.parents)
        for candidate in candidates:
            try:
                return (candidate / name).read_text().strip()
            except OSError:
                pass
        return None

    cpu_max = read("cpu.max")
    quota: dict[str, Any] | None = None
    if cpu_max:
        parts = cpu_max.split()
        if len(parts) == 2:
            quota = {
                "quota_us": "unlimited" if parts[0] == "max" else int(parts[0]),
                "period_us": int(parts[1]),
            }
    memory_max = read("memory.max")
    return {
        "version": 2 if (root / "cgroup.controllers").exists() else None,
        "effective_cpuset": read("cpuset.cpus.effective") or read("cpuset.cpus"),
        "cpu": quota,
        "memory_max_bytes": "unlimited" if memory_max == "max" else int(memory_max) if memory_max and memory_max.isdigit() else None,
    }


def _python_packages(binary: Path | None) -> dict[str, Any]:
    """Probe the selected interpreter, not the interpreter running this collector."""
    python = {"configured": binary is not None, "available": bool(binary and binary.is_file())}
    empty = {"python": python, "dd": {"installed": False},
             "dd_cudd": {"discoverable": False, "importable": False}}
    if not python["available"]:
        return empty
    probe = r'''import importlib, importlib.metadata, json, platform, sys, warnings
warnings.simplefilter("ignore")
result = {
    "python": {"version": platform.python_version(), "implementation": platform.python_implementation()},
    "dd": {"installed": False, "importable": False, "version": None},
    "dd_cudd": {"discoverable": False, "importable": False, "version": None},
}
try:
    result["dd"]["version"] = importlib.metadata.version("dd")
    result["dd"]["installed"] = True
    importlib.import_module("dd")
    result["dd"]["importable"] = True
except Exception:
    pass
try:
    cudd = importlib.import_module("dd.cudd")
    result["dd_cudd"].update({
        "discoverable": True,
        "importable": True,
        "version": getattr(cudd, "__version__", None),
    })
except Exception:
    pass
print(json.dumps(result, sort_keys=True))'''
    rc, output = _run([str(binary), "-c", probe], timeout=15.0)
    try:
        parsed = json.loads(output.splitlines()[-1])
    except (IndexError, json.JSONDecodeError):
        python["works"] = False
        return empty
    parsed["python"].update({"configured": True, "available": True, "works": rc == 0})
    return parsed


def _java(binary: Path | None) -> dict[str, Any]:
    result = {"configured": binary is not None, "available": bool(binary and binary.is_file())}
    if not result["available"]:
        return result
    rc, output = _run([str(binary), "-version"])
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    match = re.search(r'version\s+"([^"]+)"', lines[0] if lines else "")
    result.update({"works": rc == 0, "version": match.group(1) if match else None})
    if len(lines) > 1:
        result["runtime"] = lines[1][:240]
    return result


def _maven(binary: Path | None, java_binary: Path | None) -> dict[str, Any]:
    result = {"configured": binary is not None, "available": bool(binary and binary.is_file())}
    if not result["available"]:
        return result
    java_home = str(java_binary.parent.parent) if java_binary and java_binary.is_file() else None
    rc, output = _run(
        [str(binary), "-version"],
        extra_env={"JAVA_HOME": java_home} if java_home else None,
    )
    first = output.splitlines()[0].strip() if output else ""
    match = re.search(r"Apache Maven\s+([^\s]+)", first)
    result.update({"works": rc == 0, "version": match.group(1) if match else None})
    return result


def _endpoint_env(value: str) -> dict[str, Any]:
    result: dict[str, Any] = {"set": True}
    try:
        parsed = urlsplit(value)
        result.update({
            "scheme": parsed.scheme if parsed.scheme in {"http", "https"} else "other",
            "port": parsed.port,
            "host_class": "loopback" if parsed.hostname in {"localhost", "127.0.0.1", "::1"} else "non-loopback",
            "credentials_present": parsed.username is not None or parsed.password is not None,
            "query_present": bool(parsed.query),
        })
    except (ValueError, TypeError):
        result["valid_url"] = False
    return result


def _environment() -> dict[str, Any]:
    result: dict[str, Any] = {}
    endpoint_names = {"CIRCUIT_UPDATE_ENDPOINT", "SPARQLCIRC_ENDPOINT"}
    path_names = {
        "JAVA_HOME", "MAVEN_HOME", "WATDIV_NT", "D4", "D4V2", "PGHOST",
        "GRAPHDB_HOME", "NPCS_ORIG_JAR",
    }
    scalar_names = {
        "PYTHONHASHSEED", "CIRCUIT_SKIP_LOAD", "CIRCUIT_READONLY", "CIRCUIT_CLEANUP",
        "WATDIV_REPO", "PGPORT", "GDB_HEAP", "GDB_HEAP_SIZE", "OMP_NUM_THREADS",
        "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
    }
    for name in sorted(endpoint_names | path_names | scalar_names | {"LD_LIBRARY_PATH"}):
        value = os.environ.get(name)
        if value is None:
            result[name] = {"set": False}
        elif name in endpoint_names:
            result[name] = _endpoint_env(value)
        elif name in path_names:
            candidate = Path(value).expanduser()
            result[name] = {"set": True, "exists": candidate.exists()}
            if name == "WATDIV_NT" and candidate.is_file():
                result[name]["bytes"] = candidate.stat().st_size
            if name in {"D4", "D4V2", "NPCS_ORIG_JAR"} and candidate.is_file():
                result[name]["sha256"] = _sha256(candidate)
        elif name == "LD_LIBRARY_PATH":
            result[name] = {"set": True}
        else:
            result[name] = {"set": True, "value": value if SAFE_VALUE.fullmatch(value) else "redacted"}
    result["sandbox_flags"] = {
        "network_disabled_declared": "CODEX_SANDBOX_NETWORK_DISABLED" in os.environ,
        "codex_sandbox_declared": any(key.startswith("CODEX_SANDBOX") for key in os.environ),
    }
    return result


def _probe(port: int) -> str:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.25):
            return "tcp-open"
    except OSError:
        return "closed-or-network-isolated"


def _service_records(probe_local: bool) -> dict[str, Any]:
    return {
        name: {**description, "probe": _probe(description["port"]) if probe_local else "not-probed"}
        for name, description in SERVICES.items()
    }


def _disk(path: Path) -> dict[str, int] | None:
    try:
        usage = shutil.disk_usage(path)
    except OSError:
        return None
    return {"total_bytes": usage.total, "free_bytes_at_capture": usage.free}


def _resolve_binary(explicit: str | None, command: str) -> Path | None:
    if explicit:
        return Path(explicit).expanduser()
    found = shutil.which(command)
    return Path(found) if found else None


def capture(args: argparse.Namespace) -> dict[str, Any]:
    repo = Path(args.repo_root).expanduser().resolve()
    data_root = Path(args.data_root).expanduser().resolve()
    tools_root = Path(args.tools_root).expanduser().resolve()
    graphdb_home = Path(args.graphdb_home).expanduser().resolve()
    graphdb_install = Path(args.graphdb_install).expanduser().resolve()
    python_binary = Path(args.python_bin).expanduser().resolve() if args.python_bin else None
    d4v2_source = Path(args.d4v2_source).expanduser().resolve()
    d4v2_bin = Path(args.d4v2_bin).expanduser().resolve()
    d4_bin = Path(args.d4_bin).expanduser().resolve()
    provsql = Path(args.provsql_source).expanduser().resolve()
    sparqlprov = Path(args.sparqlprov_root).expanduser().resolve()

    datasets = {name: _file_dataset(data_root / relative) for name, relative in DATA_FILES.items()}
    repositories_root = graphdb_home / "data" / "repositories"
    graphdb_repositories = {
        name: {**_path_record(repositories_root / name, scan_size=args.scan_store_sizes), "declared_role": role}
        for name, role in GRAPHDB_REPOSITORIES.items()
    }
    external_stores = {
        "oxigraph_watdiv": _path_record(tools_root / "oxi-watdiv", scan_size=args.scan_store_sizes),
        "qlever_watdiv": _path_record(tools_root / "qlever-watdiv", scan_size=args.scan_store_sizes),
        "millenniumdb_watdiv": _path_record(tools_root / "mdb-watdiv", scan_size=args.scan_store_sizes),
    }

    rewrite = sparqlprov / "SPARQLprov" / "build" / "rewrite"
    tests = sparqlprov / "SPARQLprov" / "build" / "SPMPolynomialTest"
    npcs_arg = args.npcs_orig_jar or os.environ.get("NPCS_ORIG_JAR")
    npcs_path = Path(npcs_arg).expanduser().resolve() if npcs_arg else None
    graphdb_version = None
    match = re.search(r"graphdb[-_]?([0-9]+(?:\.[0-9]+)+)", graphdb_install.name, re.I)
    if match:
        graphdb_version = match.group(1)

    git = _git(repo)
    packages = _python_packages(python_binary)
    d4v2_binary = _path_record(d4v2_bin, hash_file=True)
    d4v2_binary.update({
        "pinned_sha256": PINNED_D4V2_SHA256,
        "sha256_matches_pin": d4v2_binary.get("sha256") == PINNED_D4V2_SHA256,
        "invocation": ["-i", "{cnf}", "-m", "ddnnf-compiler", "--dump-ddnnf", "{out}"],
    })
    official_npcs = _path_record(npcs_path, hash_file=True)
    services = _service_records(args.probe_local)
    environment = _environment()
    probe_authoritative = bool(
        args.probe_local
        and not environment["sandbox_flags"]["network_disabled_declared"]
    )
    graphdb_endpoint_validated = services["graphdb"]["probe"] == "tcp-open"
    main_blockers = []
    if git.get("dirty"):
        main_blockers.append("repository worktree is dirty")
    if not packages["dd_cudd"]["importable"]:
        main_blockers.append("dd.cudd is not importable in the selected Python interpreter")
    if not graphdb_endpoint_validated:
        main_blockers.append("GraphDB endpoint was not validated as TCP-open in this capture")

    java_binary = _resolve_binary(args.java_bin, "java")
    maven_binary = _resolve_binary(args.maven_bin, "mvn")
    result: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "captured_at_utc": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        "privacy": {
            "raw_paths_emitted": False,
            "hostname_emitted": False,
            "username_emitted": False,
            "arbitrary_environment_emitted": False,
        },
        "repository": git,
        "host": {
            "os": _os_info(),
            "cpu": _cpu_info(),
            "memory": _memory_info(args.include_volatile),
            "cgroup": _cgroup(),
            "limits": _limits(),
        },
        "toolchain": {
            **packages,
            "java": _java(java_binary),
            "maven": _maven(maven_binary, java_binary),
            "graphdb": {
                **_path_record(graphdb_install),
                "version": graphdb_version,
                "edition_declared": args.graphdb_edition,
                "worker_cores_declared": args.graphdb_worker_cores,
            },
            "d4v2_source": _git(d4v2_source),
            "d4v2_binary": d4v2_binary,
            "d4_v1_binary": _path_record(d4_bin, hash_file=True),
            "provsql_source": _git(provsql),
            "sparqlprov_release": {
                "tree": _path_record(sparqlprov),
                "rewrite_binary": _path_record(rewrite, hash_file=True),
                "test_binary": _path_record(tests, hash_file=True),
                "git_revision": None,
                "note": "release artifact tree; pin the source archive SHA-256 separately",
            },
            "official_npcs_jar": official_npcs,
        },
        "data": {
            "files": datasets,
            "graphdb_repositories": graphdb_repositories,
            "external_stores": external_stores,
            "wikidata_raw_directory": _path_record(data_root / "wikidata-data"),
        },
        "services": services,
        "environment": environment,
        "freeze_readiness": {
            "main_batch_ready": not main_blockers,
            "main_batch_blockers": main_blockers,
            "level1_d4v2_ready": bool(
                d4v2_binary.get("present") and d4v2_binary.get("executable")
                and d4v2_binary.get("sha256_matches_pin")
            ),
            "official_npcs_ready": bool(official_npcs.get("present")),
            "endpoint_probe_requested": bool(args.probe_local),
            "endpoint_probe_is_authoritative": probe_authoritative,
        },
    }
    if args.include_volatile:
        try:
            load = os.getloadavg()
            load_record = {"one_minute": load[0], "five_minutes": load[1], "fifteen_minutes": load[2]}
        except OSError:
            load_record = None
        result["volatile"] = {
            "load_average": load_record,
            "repository_filesystem": _disk(repo),
            "temporary_filesystem": _disk(Path(args.tmp_root)),
        }
    return result


def _cell(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value).replace("|", "\\|").replace("\n", " ")


def _table(headers: list[str], rows: Iterable[Iterable[Any]]) -> list[str]:
    result = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    result.extend("| " + " | ".join(_cell(value) for value in row) + " |" for row in rows)
    return result


def markdown(snapshot: dict[str, Any]) -> str:
    host = snapshot["host"]
    tool = snapshot["toolchain"]
    lines = [
        "# Environment capture",
        "",
        f"Captured at `{snapshot['captured_at_utc']}`. Raw paths, host/user names, credentials, and arbitrary environment variables are intentionally omitted.",
        "",
        "## Repository and host",
        "",
    ]
    lines += _table(
        ["field", "value"],
        [
            ("commit", snapshot["repository"].get("revision")),
            ("branch", snapshot["repository"].get("branch")),
            ("dirty", snapshot["repository"].get("dirty")),
            ("OS", host["os"].get("distribution")),
            ("kernel", host["os"].get("kernel")),
            ("CPU", host["cpu"].get("model")),
            ("physical/logical/allowed CPUs", f"{host['cpu'].get('physical_cores_visible')}/{host['cpu'].get('logical_cpus_visible')}/{host['cpu'].get('logical_cpus_allowed')}"),
            ("RAM bytes", host["memory"].get("total_bytes")),
            ("swap bytes", host["memory"].get("swap_total_bytes")),
        ],
    )
    lines += ["", "## Toolchain", ""]
    lines += _table(
        ["component", "version/revision", "available", "dirty"],
        [
            ("Python", tool["python"].get("version"), tool["python"].get("works"), None),
            ("dd", tool["dd"].get("version"), tool["dd"].get("installed"), None),
            ("dd.cudd", tool["dd_cudd"].get("version"), tool["dd_cudd"].get("importable"), None),
            ("Java", tool["java"].get("version"), tool["java"].get("works"), None),
            ("Maven", tool["maven"].get("version"), tool["maven"].get("works"), None),
            ("GraphDB", tool["graphdb"].get("version"), tool["graphdb"].get("present"), None),
            ("d4v2 source", tool["d4v2_source"].get("revision"), tool["d4v2_source"].get("present"), tool["d4v2_source"].get("dirty")),
            ("d4v2 binary", tool["d4v2_binary"].get("sha256"), tool["d4v2_binary"].get("present"), None),
            ("d4 v1 binary", tool["d4_v1_binary"].get("sha256"), tool["d4_v1_binary"].get("present"), None),
            ("ProvSQL source", tool["provsql_source"].get("revision"), tool["provsql_source"].get("present"), tool["provsql_source"].get("dirty")),
            ("SPARQLprov rewrite", tool["sparqlprov_release"]["rewrite_binary"].get("sha256"), tool["sparqlprov_release"]["rewrite_binary"].get("present"), None),
            ("official NPCS jar", tool["official_npcs_jar"].get("sha256"), tool["official_npcs_jar"].get("present"), None),
        ],
    )
    lines += ["", "## Data and stores", ""]
    rows = []
    for name, record in snapshot["data"]["files"].items():
        rows.append((name, "file", record.get("present"), record.get("usable"), record.get("bytes")))
    for name, record in snapshot["data"]["graphdb_repositories"].items():
        rows.append((name, "GraphDB repository", record.get("present"), "endpoint validation required", record.get("apparent_bytes")))
    for name, record in snapshot["data"]["external_stores"].items():
        rows.append((name, "external store", record.get("present"), "endpoint validation required", record.get("apparent_bytes")))
    lines += _table(["logical name", "kind", "present", "usable/readiness", "bytes"], rows)
    lines += ["", "## Configured service ports", ""]
    lines += _table(
        ["service", "port", "probe"],
        [(name, record["port"], record["probe"]) for name, record in snapshot["services"].items()],
    )
    lines += ["", "## Freeze readiness", ""]
    readiness = snapshot["freeze_readiness"]
    lines.append(f"Main batch ready: **{_cell(readiness['main_batch_ready'])}**.")
    if readiness["main_batch_blockers"]:
        lines.append("")
        lines.extend(f"- {item}" for item in readiness["main_batch_blockers"])
    lines.append("")
    return "\n".join(lines)


def parser() -> argparse.ArgumentParser:
    inferred_repo = Path(__file__).resolve().parents[2]
    inferred_data = inferred_repo.parent
    inferred_tools = inferred_data / "tools"
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repo-root", default=str(inferred_repo))
    p.add_argument("--data-root", default=str(inferred_data))
    p.add_argument("--tools-root", default=str(inferred_tools))
    p.add_argument("--graphdb-home", default=str(inferred_data / "graphdb-home"))
    p.add_argument("--graphdb-install", default=str(inferred_tools / "graphdb-10.7.6"))
    p.add_argument("--graphdb-edition", default="unknown", choices=("unknown", "free-lite", "commercial"))
    p.add_argument("--graphdb-worker-cores", type=int)
    p.add_argument("--d4v2-source", default=str(inferred_tools / "d4v2"))
    p.add_argument("--d4v2-bin", default=str(inferred_tools / "d4v2" / "scripts" / "d4_static"))
    p.add_argument("--d4-bin", default=str(inferred_tools / "d4" / "d4"))
    p.add_argument("--provsql-source", default=str(inferred_tools / "provsql"))
    p.add_argument("--sparqlprov-root", default=str(inferred_data / "sparqlprov" / "SPARQLprov-experiments"))
    p.add_argument("--npcs-orig-jar", help="official NPCS jar; never print the raw path")
    p.add_argument("--python-bin", default=sys.executable,
                   help="production Python interpreter; never print the raw path")
    p.add_argument("--java-bin", help="java executable; never print the raw path")
    p.add_argument("--maven-bin", help="mvn executable; never print the raw path")
    p.add_argument("--tmp-root", default="/tmp")
    p.add_argument("--probe-local", action="store_true", help="probe only 127.0.0.1 configured ports")
    p.add_argument("--scan-store-sizes", action="store_true", help="walk store trees for apparent byte sizes")
    p.add_argument("--include-volatile", action="store_true", help="include load, free memory, and free disk")
    p.add_argument("--format", choices=("json", "markdown"), default="json")
    p.add_argument("--output", help="output file (default: stdout)")
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.graphdb_worker_cores is not None and args.graphdb_worker_cores < 1:
        raise SystemExit("--graphdb-worker-cores must be positive")
    snapshot = capture(args)
    rendered = markdown(snapshot) if args.format == "markdown" else json.dumps(
        snapshot, indent=2, sort_keys=True, ensure_ascii=False
    ) + "\n"
    if args.output:
        Path(args.output).write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
