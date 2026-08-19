#!/usr/bin/env python3
"""Run one measured WatDiv B/R/N/C cell and preserve its raw artifacts.

A cell is one ``query x engine x method`` combination.  The default protocol is
one warm-up followed by five measured executions.  Every endpoint execution is
placed in its own process group and receives an independent hard deadline.
Offline response processing and PQE have a separate deadline.

The runner is deliberately cluster-neutral.  It records process-local resource
figures where the operating system exposes them; Slurm/cgroup sampling and job
placement belong to the deployment wrapper used on the evaluation cluster.
No experiment-level checksum or content digest is computed.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
import math
import os
from pathlib import Path
import re
import signal
import socket
import statistics
import subprocess
import sys
import time
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple
import urllib.error
import urllib.request


HERE = Path(__file__).resolve().parent
REFERENCE = HERE.parent
if str(REFERENCE) not in sys.path:
    sys.path.insert(0, str(REFERENCE))

import circuit_io


SCHEMA = "watdiv-brnc-cell-v1"
RUN_SCHEMA = "watdiv-brnc-run-v1"
ENDPOINT_SCHEMA = "watdiv-brnc-endpoint-v1"
OFFLINE_SCHEMA = "watdiv-brnc-offline-v1"
METHODS = ("B", "R", "N", "C-flat", "C-factored", "C-path")
JSON_RESULTS = "application/sparql-results+json"
NT_RESULTS = "application/n-triples"
DEFAULT_ENDPOINT_TIMEOUT_S = 1200.0
DEFAULT_OFFLINE_TIMEOUT_S = 1200.0
DEFAULT_TOKEN_REGEX = r"^urn:t:[0-9]+$"
CHUNK_BYTES = 64 * 1024
_NO_PROXY_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class RunnerError(RuntimeError):
    """A cell cannot be run under the requested protocol."""


class StageError(RuntimeError):
    """A measured stage failed with a stable status classification."""

    def __init__(self, status: str, detail: str, fields: Optional[Mapping[str, Any]] = None):
        super().__init__(detail)
        self.status = status
        self.detail = detail
        self.fields = dict(fields or {})


def _positive_seconds(value: str) -> float:
    seconds = float(value)
    if not math.isfinite(seconds) or seconds <= 0:
        raise argparse.ArgumentTypeError("timeout must be a positive finite number")
    return seconds


def _nonnegative_integer(value: str) -> int:
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError("value must be non-negative")
    return number


def _positive_integer(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("value must be positive")
    return number


def _milliseconds(started: float) -> float:
    return (time.perf_counter() - started) * 1000.0


def _round_ms(value: float) -> float:
    return round(float(value), 6)


def _atomic_bytes(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError("refusing to overwrite artifact: %s" % path)
    temporary = path.with_name(path.name + ".partial")
    if temporary.exists():
        raise FileExistsError("partial artifact already exists: %s" % temporary)
    with temporary.open("wb") as handle:
        handle.write(value)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _atomic_text(path: Path, value: str) -> None:
    _atomic_bytes(path, value.encode("utf-8"))


def _atomic_json(path: Path, value: Any) -> None:
    _assert_no_digest_fields(value)
    _atomic_bytes(
        path,
        (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
            "utf-8"
        ),
    )


def _atomic_json_lines(path: Path, values: Iterable[Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError("refusing to overwrite artifact: %s" % path)
    temporary = path.with_name(path.name + ".partial")
    if temporary.exists():
        raise FileExistsError("partial artifact already exists: %s" % temporary)
    with temporary.open("wb") as handle:
        for value in values:
            _assert_no_digest_fields(value)
            handle.write(
                (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                 + "\n").encode("utf-8")
            )
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _atomic_utf8_lines(path: Path, values: Iterable[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError("refusing to overwrite artifact: %s" % path)
    temporary = path.with_name(path.name + ".partial")
    if temporary.exists():
        raise FileExistsError("partial artifact already exists: %s" % temporary)
    with temporary.open("wb") as handle:
        for value in values:
            handle.write(str(value).encode("utf-8"))
            handle.write(b"\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _assert_no_digest_fields(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            lowered = str(key).lower()
            if "checksum" in lowered or "digest" in lowered or lowered.endswith(
                ("_sha", "_sha1", "_sha256", "_sha512")
            ):
                raise RunnerError("digest-bearing result field is forbidden: %s" % key)
            _assert_no_digest_fields(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _assert_no_digest_fields(child)


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _remaining(deadline: float) -> float:
    seconds = deadline - time.monotonic()
    if seconds <= 0:
        raise StageError("timeout", "hard endpoint deadline exhausted")
    return seconds


def _peak_rss_bytes(kind: int) -> Optional[int]:
    try:
        import resource
    except ImportError:
        return None
    value = int(resource.getrusage(kind).ru_maxrss)
    return value if sys.platform == "darwin" else value * 1024


def _resource_metrics() -> Dict[str, Any]:
    try:
        import resource
    except ImportError:
        return {
            "scope": "process-local; engine resources require an external sampler",
            "client_cpu_ms": None,
            "client_peak_rss_bytes": None,
            "child_cpu_ms": None,
            "child_peak_rss_bytes": None,
        }
    own = resource.getrusage(resource.RUSAGE_SELF)
    children = resource.getrusage(resource.RUSAGE_CHILDREN)
    return {
        "scope": "process-local; remote endpoint resources require an external sampler",
        "client_cpu_ms": _round_ms((own.ru_utime + own.ru_stime) * 1000.0),
        "client_peak_rss_bytes": _peak_rss_bytes(resource.RUSAGE_SELF),
        "child_cpu_ms": _round_ms((children.ru_utime + children.ru_stime) * 1000.0),
        "child_peak_rss_bytes": _peak_rss_bytes(resource.RUSAGE_CHILDREN),
    }


def _directory_bytes(path: Path) -> int:
    total = 0
    if path.exists():
        for candidate in path.rglob("*"):
            if candidate.is_file():
                total += candidate.stat().st_size
    return total


def _terminate_process_group(process: subprocess.Popen, grace_s: float = 2.0) -> None:
    if process.poll() is not None:
        return
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
    else:
        process.terminate()
    try:
        process.wait(timeout=grace_s)
        return
    except subprocess.TimeoutExpired:
        pass
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            return
    else:
        process.kill()
    process.wait()


def _wait_process(process: subprocess.Popen, timeout_s: float) -> Tuple[int, float, bool]:
    started = time.perf_counter()
    try:
        return process.wait(timeout=timeout_s), _milliseconds(started), False
    except subprocess.TimeoutExpired:
        _terminate_process_group(process)
        return process.returncode if process.returncode is not None else -1, _milliseconds(started), True


def _finalize_open_file(handle: Any, partial: Path, target: Path) -> None:
    handle.flush()
    os.fsync(handle.fileno())
    handle.close()
    os.replace(partial, target)


def _run_java_rewrite(config: Mapping[str, Any], run_dir: Path, deadline: float) -> Tuple[str, Dict[str, Any]]:
    output = run_dir / "rewritten.rq"
    partial = output.with_name(output.name + ".partial")
    stderr_target = run_dir / "rewrite.stderr"
    stderr_partial = stderr_target.with_name(stderr_target.name + ".partial")
    command = [
        str(config["java"]),
        "-jar",
        str(config["jar"]),
        "rewrite",
        str(config["scheme"]),
        "path",
        str(config["query"]),
    ]
    started = time.perf_counter()
    stdout_handle = partial.open("wb")
    stderr_handle = stderr_partial.open("wb")
    process = subprocess.Popen(command, stdout=stdout_handle, stderr=stderr_handle)
    timed_out = False
    try:
        process.wait(timeout=_remaining(deadline))
    except subprocess.TimeoutExpired:
        timed_out = True
        process.terminate()
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
    _finalize_open_file(stderr_handle, stderr_partial, stderr_target)
    stdout_handle.flush()
    os.fsync(stdout_handle.fileno())
    stdout_handle.close()
    rewrite_ms = _milliseconds(started)
    if timed_out:
        raise StageError("timeout", "NPCS rewrite exceeded the endpoint deadline")
    if process.returncode != 0:
        failed = run_dir / "rewrite.failed.stdout"
        os.replace(partial, failed)
        detail = stderr_target.read_text(encoding="utf-8", errors="replace")[-1000:]
        raise StageError("rewrite-error", "NPCS rewrite failed: %s" % detail.strip())
    if partial.stat().st_size == 0:
        raise StageError("rewrite-error", "NPCS rewrite returned an empty query")
    os.replace(partial, output)
    return output.read_text(encoding="utf-8"), {
        "kind": "npcs-java-rewrite",
        "rewrite_ms": _round_ms(rewrite_ms),
        "rewritten_query_bytes": output.stat().st_size,
        "stderr_bytes": stderr_target.stat().st_size,
    }


def _run_reification_rewrite(
    query: str, run_dir: Path, scheme: str
) -> Tuple[str, Dict[str, Any]]:
    started = time.perf_counter()
    try:
        import reify_query
        rewritten = reify_query.reify(query, scheme=scheme)
    except Exception as exc:
        raise StageError("rewrite-error", "R rewrite failed: %s" % exc) from exc
    rewrite_ms = _milliseconds(started)
    target = run_dir / "rewritten.rq"
    _atomic_text(target, rewritten)
    return rewritten, {
        "kind": "algebra-preserving-reification",
        "scheme": scheme,
        "rewrite_ms": _round_ms(rewrite_ms),
        "rewritten_query_bytes": target.stat().st_size,
    }


def _stream_query_response(
    endpoint: str,
    query: str,
    target: Path,
    deadline: float,
) -> Dict[str, Any]:
    request = urllib.request.Request(
        endpoint,
        data=query.encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/sparql-query", "Accept": JSON_RESULTS},
    )
    partial = target.with_name(target.name + ".partial")
    request_started = time.perf_counter()
    first_byte_at: Optional[float] = None
    last_byte_at: Optional[float] = None
    byte_count = 0
    newline_count = 0
    last_byte = b""
    try:
        with partial.open("xb") as handle:
            with _NO_PROXY_OPENER.open(request, timeout=max(0.001, _remaining(deadline))) as response:
                chunk = response.read(1)
                if chunk:
                    first_byte_at = time.perf_counter()
                    last_byte_at = first_byte_at
                    handle.write(chunk)
                    byte_count = 1
                    newline_count = chunk.count(b"\n")
                    last_byte = chunk
                while True:
                    chunk = response.read(CHUNK_BYTES)
                    if not chunk:
                        break
                    now = time.perf_counter()
                    if first_byte_at is None:
                        first_byte_at = now
                    last_byte_at = now
                    handle.write(chunk)
                    byte_count += len(chunk)
                    newline_count += chunk.count(b"\n")
                    last_byte = chunk[-1:]
                    _remaining(deadline)
            response_finished = time.perf_counter()
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(partial, target)
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read(4096).decode("utf-8", errors="replace")
        except Exception:
            detail = str(exc)
        raise StageError(
            "http-error",
            "HTTP %d: %s" % (exc.code, detail.strip() or exc.reason),
            {"http_status": exc.code},
        ) from exc
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        if isinstance(reason, (socket.timeout, TimeoutError)):
            raise StageError("timeout", "HTTP request timed out: %s" % reason) from exc
        raise StageError("network-error", str(reason)) from exc
    except (socket.timeout, TimeoutError) as exc:
        raise StageError("timeout", "HTTP request timed out: %s" % exc) from exc
    except FileExistsError as exc:
        raise StageError("artifact-error", str(exc)) from exc
    finally:
        if partial.exists() and not target.exists():
            # Keep incomplete bytes for diagnosis.  They are never accepted as
            # a complete response by the offline stage.
            pass

    persisted_at = time.perf_counter()
    request_ms = (response_finished - request_started) * 1000.0
    ttfb_ms = (
        (first_byte_at - request_started) * 1000.0 if first_byte_at is not None else None
    )
    drain_ms = (
        (last_byte_at - first_byte_at) * 1000.0
        if first_byte_at is not None and last_byte_at is not None
        else 0.0
    )
    line_count = newline_count + (1 if byte_count and last_byte != b"\n" else 0)
    return {
        "request_ms": _round_ms(request_ms),
        "ttfb_ms": _round_ms(ttfb_ms) if ttfb_ms is not None else None,
        "response_drain_ms": _round_ms(drain_ms),
        "response_finalize_ms": _round_ms((persisted_at - response_finished) * 1000.0),
        "response_bytes": byte_count,
        "response_lines": line_count,
        "timing_scope": (
            "client observed; TTFB includes connection, endpoint evaluation, headers, and first payload; "
            "drain includes remaining transfer and streaming writes"
        ),
    }


def _parse_c_stderr(text: str, method: str) -> Dict[str, Any]:
    construction = re.findall(r"(?m)^# construction_ms:\s*([0-9]+(?:\.[0-9]+)?)\s*$", text)
    if len(construction) != 1:
        raise StageError("c-protocol-error", "CircuitRun emitted no unique construction_ms marker")
    encoding = re.findall(
        r"final_triples=([0-9]+)\s*->\s*([0-9]+),\s*"
        r"collapsed_unary_plus=([0-9]+),\s*omitted_types=([0-9]+)",
        text,
    )
    mode_matches = re.findall(
        r"construction mode:\s*requested=([a-z-]+),\s*effective=([a-z-]+)",
        text,
        flags=re.IGNORECASE,
    )
    fallback = re.findall(r"explicit fallback:\s*(.*?)\s*----", text)
    if method == "C-path":
        requested = effective = "property-path-dedicated"
        if mode_matches:
            raise StageError("c-protocol-error", "property-path run emitted a BGP construction mode")
    else:
        expected = "flat" if method == "C-flat" else "factored"
        if len(mode_matches) != 1:
            raise StageError("c-protocol-error", "CircuitRun emitted no unique construction-mode marker")
        requested, effective = (item.lower() for item in mode_matches[0])
        if requested != expected or effective != expected or fallback:
            raise StageError(
                "c-mode-error",
                "strict C mode was not honored: requested=%s effective=%s fallback=%s"
                % (requested, effective, fallback[0] if fallback else None),
            )
    result: Dict[str, Any] = {
        "requested_mode": requested,
        "effective_mode": effective,
        "fallback_reason": fallback[0] if fallback else None,
        "construction_ms": _round_ms(float(construction[0])),
        "plan_steps": len(re.findall(r"(?m)^# --- step [0-9]+ ---\s*$", text)),
        "path_construct_requests": len(
            re.findall(r"(?m)^# --- path CONSTRUCT ---\s*$", text)
        ),
    }
    if encoding:
        before, after, collapsed, omitted = encoding[-1]
        result["encoding"] = {
            "pre_normalization_triples": int(before),
            "final_triples": int(after),
            "collapsed_unary_plus": int(collapsed),
            "omitted_types": int(omitted),
        }
    path_plan = re.findall(
        r"property-path plan:\s*reachable-nodes=([0-9]+),\s*rounds=([0-9]+)\s*\(cap=([0-9]+)\)",
        text,
    )
    if path_plan:
        reachable, rounds, cap = path_plan[-1]
        result["property_path"] = {
            "reachable_nodes": int(reachable),
            "rounds": int(rounds),
            "round_cap": int(cap),
        }
    return result


def _run_c(config: Mapping[str, Any], run_dir: Path, deadline: float) -> Dict[str, Any]:
    method = str(config["method"])
    construction = "flat" if method == "C-flat" else "factored"
    circuit = run_dir / "circuit.nt"
    circuit_partial = circuit.with_name(circuit.name + ".partial")
    stderr_target = run_dir / "circuit.stderr"
    stderr_partial = stderr_target.with_name(stderr_target.name + ".partial")
    command = [
        str(config["java"]),
        "-jar",
        str(config["jar"]),
        "circuit",
        "--construction=%s" % construction,
        str(config["scheme"]),
        str(config["reified_data"]),
        str(config["query"]),
        str(config["reified_endpoint"]),
    ]
    environment = dict(os.environ)
    environment["CIRCUIT_SKIP_LOAD"] = "1"
    environment["CIRCUIT_CLEANUP"] = "1"
    environment["CIRCUIT_PARALLELISM"] = str(config["c_parallelism"])
    if config.get("update_endpoint"):
        environment["CIRCUIT_UPDATE_ENDPOINT"] = str(config["update_endpoint"])
    if config.get("c_read_only"):
        environment["CIRCUIT_READONLY"] = "1"
    if config.get("skip_bnode_check"):
        environment["CIRCUIT_SKIP_BNODE_CHECK"] = "1"

    started = time.perf_counter()
    stdout_handle = circuit_partial.open("xb")
    stderr_handle = stderr_partial.open("xb")
    process = subprocess.Popen(
        command,
        stdout=stdout_handle,
        stderr=stderr_handle,
        env=environment,
    )
    timed_out = False
    try:
        process.wait(timeout=_remaining(deadline))
    except subprocess.TimeoutExpired:
        timed_out = True
        process.terminate()
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
    _finalize_open_file(stderr_handle, stderr_partial, stderr_target)
    stdout_handle.flush()
    os.fsync(stdout_handle.fileno())
    stdout_handle.close()
    process_ms = _milliseconds(started)
    if timed_out:
        raise StageError("timeout", "CircuitRun exceeded the endpoint deadline")
    if process.returncode != 0:
        failed = run_dir / "circuit.failed.nt"
        os.replace(circuit_partial, failed)
        detail = stderr_target.read_text(encoding="utf-8", errors="replace")[-2000:]
        raise StageError("c-execution-error", "CircuitRun failed: %s" % detail.strip())
    if circuit_partial.stat().st_size == 0:
        raise StageError("c-protocol-error", "CircuitRun emitted an empty circuit")
    os.replace(circuit_partial, circuit)
    persisted_at = time.perf_counter()
    stderr_text = stderr_target.read_text(encoding="utf-8", errors="replace")
    protocol = _parse_c_stderr(stderr_text, method)
    endpoint_e2e_ms = (persisted_at - started) * 1000.0
    protocol["process_ms"] = _round_ms(process_ms)
    protocol["endpoint_e2e_ms"] = _round_ms(endpoint_e2e_ms)
    protocol["outside_reported_construction_ms"] = _round_ms(
        max(0.0, endpoint_e2e_ms - float(protocol["construction_ms"]))
    )
    protocol["circuit_bytes"] = circuit.stat().st_size
    protocol["stderr_bytes"] = stderr_target.stat().st_size
    protocol["timing_scope"] = (
        "CircuitRun process start through atomic circuit persistence; construction_ms is the "
        "engine client's plan execution plus final normalization marker emitted by CircuitRun"
    )
    return protocol


def _endpoint_run(config: Mapping[str, Any], run_dir: Path) -> Dict[str, Any]:
    started = time.perf_counter()
    deadline = time.monotonic() + float(config["endpoint_timeout_s"])
    method = str(config["method"])
    try:
        if method.startswith("C-"):
            metrics = _run_c(config, run_dir, deadline)
            endpoint_e2e_ms = float(metrics["endpoint_e2e_ms"])
            rewrite = None
        else:
            query_started = time.perf_counter()
            query = Path(str(config["query"])).read_text(encoding="utf-8")
            query_read_ms = _milliseconds(query_started)
            if method == "B":
                rewritten = query
                rewrite = {
                    "kind": "none",
                    "rewrite_ms": 0.0,
                    "rewritten_query_bytes": len(query.encode("utf-8")),
                }
                endpoint = str(config["base_endpoint"])
            elif method == "R":
                rewritten, rewrite = _run_reification_rewrite(
                    query, run_dir, str(config["scheme"])
                )
                endpoint = str(config["reified_endpoint"])
            elif method == "N":
                rewritten, rewrite = _run_java_rewrite(config, run_dir, deadline)
                endpoint = str(config["reified_endpoint"])
            else:
                raise StageError("configuration-error", "unknown method: %s" % method)
            response = run_dir / "raw-response.json"
            request_metrics = _stream_query_response(endpoint, rewritten, response, deadline)
            endpoint_e2e_ms = _milliseconds(started)
            metrics = dict(request_metrics)
            metrics["query_read_ms"] = _round_ms(query_read_ms)
            metrics["endpoint_e2e_ms"] = _round_ms(endpoint_e2e_ms)
            metrics["endpoint_e2e_scope"] = (
                "worker start, query read and rewrite through atomic persistence of the complete response"
            )
        _remaining(deadline)
        result: Dict[str, Any] = {
            "schema": ENDPOINT_SCHEMA,
            "status": "ok",
            "method": method,
            "scheme": config["scheme"],
            "endpoint_timeout_s": float(config["endpoint_timeout_s"]),
            "endpoint": metrics,
            "rewrite": rewrite,
            "resources": _resource_metrics(),
        }
    except StageError as exc:
        result = {
            "schema": ENDPOINT_SCHEMA,
            "status": exc.status,
            "method": method,
            "scheme": config["scheme"],
            "endpoint_timeout_s": float(config["endpoint_timeout_s"]),
            "detail": exc.detail[:4000],
            "failure": exc.fields,
            "worker_wall_ms": _round_ms(_milliseconds(started)),
            "resources": _resource_metrics(),
        }
    except BaseException as exc:
        result = {
            "schema": ENDPOINT_SCHEMA,
            "status": "runner-error",
            "method": method,
            "scheme": config["scheme"],
            "endpoint_timeout_s": float(config["endpoint_timeout_s"]),
            "detail": "%s: %s" % (type(exc).__name__, exc),
            "worker_wall_ms": _round_ms(_milliseconds(started)),
            "resources": _resource_metrics(),
        }
    result["artifact_bytes_at_worker_exit"] = _directory_bytes(run_dir)
    return result


def _endpoint_worker_main(config_path: Path, run_dir: Path) -> int:
    config = _read_json(config_path)
    result_path = run_dir / "endpoint-result.json"
    result = _endpoint_run(config, run_dir)
    _atomic_json(result_path, result)
    return 0 if result["status"] == "ok" else 1


def _term_key(binding: Optional[Mapping[str, Any]]) -> List[str]:
    if binding is None:
        return ["unbound"]
    kind = binding.get("type")
    value = binding.get("value")
    if not isinstance(value, str):
        raise RunnerError("SPARQL JSON term has no string value")
    if kind == "uri":
        return ["iri", value]
    if kind == "bnode":
        return ["bnode", value]
    if kind in ("literal", "typed-literal"):
        language = str(binding.get("xml:lang") or binding.get("lang") or "").lower()
        datatype = binding.get("datatype") or (
            circuit_io.RDF_LANGSTRING if language else circuit_io.XSD_STRING
        )
        return ["literal", value, str(datatype), language]
    raise RunnerError("unsupported SPARQL JSON term type: %r" % kind)


def _binding_value(binding: Mapping[str, Any], variables: Sequence[str]) -> List[Any]:
    return [[variable, _term_key(binding.get(variable))] for variable in sorted(variables)]


def _binding_text(binding: Sequence[Any]) -> str:
    return json.dumps(binding, ensure_ascii=False, sort_keys=False, separators=(",", ":"))


def _canonical_response_records(raw: bytes) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    decode_started = time.perf_counter()
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RunnerError("response is not valid UTF-8 SPARQL Results JSON") from exc
    decode_ms = _milliseconds(decode_started)
    if not isinstance(payload, dict):
        raise RunnerError("SPARQL Results JSON top level is not an object")
    head = payload.get("head")
    results = payload.get("results")
    if not isinstance(head, dict) or not isinstance(results, dict):
        raise RunnerError("response lacks SPARQL head or results objects")
    variables = head.get("vars")
    rows = results.get("bindings")
    if not isinstance(variables, list) or not all(isinstance(item, str) for item in variables):
        raise RunnerError("response head.vars is invalid")
    if not isinstance(rows, list) or not all(isinstance(item, dict) for item in rows):
        raise RunnerError("response results.bindings is invalid")
    canonical_started = time.perf_counter()
    counts = Counter(_binding_text(_binding_value(row, variables)) for row in rows)
    records = [
        {"binding": json.loads(binding), "multiplicity": counts[binding]}
        for binding in sorted(counts)
    ]
    return records, {
        "json_decode_ms": _round_ms(decode_ms),
        "canonicalize_ms": _round_ms(_milliseconds(canonical_started)),
        "solution_rows": len(rows),
        "distinct_binding_count": len(records),
    }


def _canonicalize_response(response: Path, output: Path) -> Dict[str, Any]:
    if output.exists():
        raise RunnerError("refusing to reuse offline output directory: %s" % output)
    output.mkdir(parents=True)
    started = time.perf_counter()
    read_started = time.perf_counter()
    raw = response.read_bytes()
    read_ms = _milliseconds(read_started)
    records, metrics = _canonical_response_records(raw)
    persist_started = time.perf_counter()
    _atomic_json_lines(output / "answer-records.jsonl", records)
    persist_ms = _milliseconds(persist_started)
    metrics.update({
        "schema": OFFLINE_SCHEMA,
        "kind": "sparql-results-canonicalization",
        "response_read_ms": _round_ms(read_ms),
        "answer_record_persist_ms": _round_ms(persist_ms),
        "answer_record_bytes": (output / "answer-records.jsonl").stat().st_size,
        "offline_wall_ms": _round_ms(_milliseconds(started)),
        "process_peak_rss_bytes": _resource_metrics()["client_peak_rss_bytes"],
    })
    _atomic_json(output / "metrics.json", metrics)
    return metrics


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
    raise RunnerError("invalid canonical circuit term: %r" % value)


def _circuit_binding(binding: Mapping[str, str]) -> List[Any]:
    return [
        [variable, _canonical_term_key(value)]
        for variable, value in sorted(binding.items())
    ]


def _reachable_circuit_stats(
    circuit: Mapping[str, Tuple[str, Any]], roots: Iterable[str]
) -> Dict[str, Any]:
    reachable = set()
    stack = list(roots)
    edges = 0
    by_operation: Counter = Counter()
    while stack:
        node = stack.pop()
        if node in reachable:
            continue
        if node not in circuit:
            raise RunnerError("answer circuit references a missing node: %s" % node)
        reachable.add(node)
        operation, payload = circuit[node]
        by_operation[operation] += 1
        if operation in ("plus", "times"):
            children = tuple(payload)
        elif operation == "minus":
            children = tuple(payload)
        elif operation == "not":
            children = (payload,)
        else:
            children = ()
        edges += len(children)
        stack.extend(children)
    return {
        "nodes": len(reachable),
        "edges": edges,
        "total": len(reachable) + edges,
        "nodes_by_operation": dict(sorted(by_operation.items())),
    }


def _load_weights(
    probability_path: Optional[Path], uniform: Optional[float], tokens: Sequence[str]
) -> Dict[str, float]:
    if (probability_path is None) == (uniform is None):
        raise RunnerError("choose exactly one probability source for PQE")
    if probability_path is not None:
        value = _read_json(probability_path)
        if not isinstance(value, dict):
            raise RunnerError("probability file must be a JSON object")
    else:
        value = {token: uniform for token in tokens}
    weights: Dict[str, float] = {}
    for token in tokens:
        if token not in value:
            raise RunnerError("probability source is missing token: %s" % token)
        try:
            probability = float(value[token])
        except (TypeError, ValueError) as exc:
            raise RunnerError("probability is not numeric for token: %s" % token) from exc
        if not math.isfinite(probability) or not 0 <= probability <= 1:
            raise RunnerError("probability is outside [0, 1] for token: %s" % token)
        weights[token] = probability
    return weights


def _process_circuit(
    circuit_path: Path,
    output: Path,
    backend: str,
    probabilities: Optional[Path],
    uniform_probability: Optional[float],
) -> Dict[str, Any]:
    if output.exists():
        raise RunnerError("refusing to reuse offline output directory: %s" % output)
    output.mkdir(parents=True)
    started = time.perf_counter()
    decode_started = time.perf_counter()
    circuit_lines = 0

    def counted_lines() -> Iterable[str]:
        nonlocal circuit_lines
        with circuit_path.open(encoding="utf-8") as handle:
            for line in handle:
                if line.strip().endswith(" ."):
                    circuit_lines += 1
                yield line

    try:
        circuit, answers, bindings = circuit_io.parse(counted_lines())
    except (UnicodeDecodeError, ValueError) as exc:
        raise RunnerError("invalid circuit artifact: %s" % exc) from exc
    decode_ms = _milliseconds(decode_started)
    records_by_text: Dict[str, Dict[str, Any]] = {}
    roots: Dict[str, str] = {}
    for root in sorted(answers):
        binding = _circuit_binding(bindings.get(root, {}))
        key = _binding_text(binding)
        if key in roots:
            raise RunnerError("multiple circuit roots carry the same answer binding")
        roots[key] = root
        records_by_text[key] = {"binding": binding, "multiplicity": 1}
    persist_started = time.perf_counter()
    answer_target = output / "answer-records.jsonl"
    _atomic_json_lines(answer_target, (records_by_text[key] for key in sorted(records_by_text)))
    persist_ms = _milliseconds(persist_started)
    structure = _reachable_circuit_stats(circuit, roots.values())
    metrics: Dict[str, Any] = {
        "schema": OFFLINE_SCHEMA,
        "kind": "circuit-decode-and-pqe",
        "circuit_stream_parse_ms": _round_ms(decode_ms),
        "circuit_bytes": circuit_path.stat().st_size,
        "circuit_triple_lines": circuit_lines,
        "answer_count": len(answers),
        "answer_record_persist_ms": _round_ms(persist_ms),
        "answer_record_bytes": answer_target.stat().st_size,
        "answer_reachable_circuit": structure,
        "pqe_backend": backend,
    }
    if backend != "none":
        import compiler

        pqe_started = time.perf_counter()
        probability_started = time.perf_counter()
        order = tuple(compiler.deterministic_order(circuit, roots))
        weights = _load_weights(probabilities, uniform_probability, order)
        probability_load_ms = _milliseconds(probability_started)
        compile_started = time.perf_counter()
        batch = compiler.compile_many(
            circuit,
            roots,
            mode="shared",
            backend=backend,
            order=order,
            record_order_fingerprint=False,
        )
        compile_wall_ms = _milliseconds(compile_started)
        wmc_started = time.perf_counter()
        values = batch.wmc_many(weights)
        wmc_wall_ms = _milliseconds(wmc_started)
        order_started = time.perf_counter()
        _atomic_utf8_lines(output / "variable-order.txt", order)
        order_persist_ms = _milliseconds(order_started)
        probability_persist_started = time.perf_counter()
        _atomic_json_lines(
            output / "probabilities.jsonl",
            (
                {
                    "binding": records_by_text[key]["binding"],
                    "probability": values[key],
                }
                for key in sorted(values)
            ),
        )
        probability_persist_ms = _milliseconds(probability_persist_started)
        compiler_metrics = dict(batch.metrics)
        _assert_no_digest_fields(compiler_metrics)
        pqe_total_ms = (
            probability_load_ms
            + compile_wall_ms
            + wmc_wall_ms
            + order_persist_ms
            + probability_persist_ms
        )
        compiler_metrics.update({
            "probability_load_ms": _round_ms(probability_load_ms),
            "compile_wall_ms": _round_ms(compile_wall_ms),
            "wmc_wall_ms": _round_ms(wmc_wall_ms),
            "variable_order_persist_ms": _round_ms(order_persist_ms),
            "probability_persist_ms": _round_ms(probability_persist_ms),
            "pqe_total_ms": _round_ms(pqe_total_ms),
            "pqe_wall_ms": _round_ms(_milliseconds(pqe_started)),
        })
        metrics["compiler"] = compiler_metrics
    metrics["offline_wall_ms"] = _round_ms(_milliseconds(started))
    metrics["process_peak_rss_bytes"] = _resource_metrics()["client_peak_rss_bytes"]
    _atomic_json(output / "metrics.json", metrics)
    return metrics


def _offline_worker_main(args: argparse.Namespace) -> int:
    try:
        if args.offline_kind == "response":
            metrics = _canonicalize_response(args.input, args.out)
        elif args.offline_kind == "circuit":
            metrics = _process_circuit(
                args.input,
                args.out,
                args.backend,
                args.probabilities,
                args.uniform_probability,
            )
        else:
            raise RunnerError("unknown offline worker kind")
    except BaseException as exc:
        sys.stderr.write("%s: %s\n" % (type(exc).__name__, exc))
        return 1
    sys.stdout.write(json.dumps(metrics, ensure_ascii=False, sort_keys=True) + "\n")
    return 0


def _run_child(
    command: Sequence[str], stdout_path: Path, stderr_path: Path, timeout_s: float
) -> Dict[str, Any]:
    stdout_partial = stdout_path.with_name(stdout_path.name + ".partial")
    stderr_partial = stderr_path.with_name(stderr_path.name + ".partial")
    stdout_handle = stdout_partial.open("xb")
    stderr_handle = stderr_partial.open("xb")
    started = time.perf_counter()
    process = subprocess.Popen(
        list(command),
        stdout=stdout_handle,
        stderr=stderr_handle,
        start_new_session=(os.name == "posix"),
    )
    returncode, wall_ms, timed_out = _wait_process(process, timeout_s)
    _finalize_open_file(stdout_handle, stdout_partial, stdout_path)
    _finalize_open_file(stderr_handle, stderr_partial, stderr_path)
    return {
        "returncode": returncode,
        "wall_ms": _round_ms(wall_ms),
        "timed_out": timed_out,
        "stdout_bytes": stdout_path.stat().st_size,
        "stderr_bytes": stderr_path.stat().st_size,
        "parent_observed_wall_ms": _round_ms(_milliseconds(started)),
    }


def _persist_npcs_answer_records(pp: Path) -> Dict[str, Any]:
    source = pp / "npcs-provenance.jsonl"
    target = pp / "answer-records.jsonl"
    started = time.perf_counter()
    records = []
    seen = set()
    with source.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            item = json.loads(line)
            key = item.get("answer_key")
            if not isinstance(key, str):
                raise RunnerError("NPCS answer record %d has no answer_key" % line_number)
            binding = json.loads(key)
            canonical = _binding_text(binding)
            if canonical in seen:
                raise RunnerError("NPCS post-processing produced duplicate answer bindings")
            seen.add(canonical)
            records.append((canonical, {"binding": binding, "multiplicity": 1}))
    _atomic_json_lines(target, (item for _key, item in sorted(records)))
    return {
        "answer_record_persist_ms": _round_ms(_milliseconds(started)),
        "answer_record_bytes": target.stat().st_size,
        "answer_count": len(records),
    }


def _run_offline(config: Mapping[str, Any], run_dir: Path, run_id: str) -> Dict[str, Any]:
    method = str(config["method"])
    timeout_s = float(config["offline_timeout_s"])
    stdout_path = run_dir / "offline.stdout"
    stderr_path = run_dir / "offline.stderr"
    if method in ("B", "R"):
        output = run_dir / "offline"
        command = [
            sys.executable,
            str(Path(__file__).resolve()),
            "_offline",
            "response",
            "--input",
            str(run_dir / "raw-response.json"),
            "--out",
            str(output),
        ]
    elif method == "N":
        output = run_dir / "pp"
        command = [
            sys.executable,
            str(REFERENCE / "npcs_postprocess.py"),
            str(run_dir / "raw-response.json"),
            "--out",
            str(output),
            "--query-id",
            str(config["query_id"]),
            "--run-id",
            run_id,
            "--engine",
            str(config["engine"]),
            "--token-regex",
            str(config["token_regex"]),
            "--backend",
            str(config["pqe_backend"]),
        ]
        if config.get("probabilities"):
            command.extend(("--probabilities", str(config["probabilities"])))
        elif config.get("uniform_probability") is not None:
            command.extend(("--uniform-probability", str(config["uniform_probability"])))
    else:
        output = run_dir / "offline"
        command = [
            sys.executable,
            str(Path(__file__).resolve()),
            "_offline",
            "circuit",
            "--input",
            str(run_dir / "circuit.nt"),
            "--out",
            str(output),
            "--backend",
            str(config["pqe_backend"]),
        ]
        if config.get("probabilities"):
            command.extend(("--probabilities", str(config["probabilities"])))
        elif config.get("uniform_probability") is not None:
            command.extend(("--uniform-probability", str(config["uniform_probability"])))
    child = _run_child(command, stdout_path, stderr_path, timeout_s)
    result: Dict[str, Any] = {
        "schema": OFFLINE_SCHEMA,
        "status": "ok",
        "offline_timeout_s": timeout_s,
        "process": child,
    }
    if child["timed_out"]:
        result["status"] = "offline-timeout"
        result["detail"] = "offline processing exceeded %.6gs" % timeout_s
        return result
    if child["returncode"] != 0:
        result["status"] = "offline-error"
        result["detail"] = stderr_path.read_text(encoding="utf-8", errors="replace")[-4000:]
        return result
    metrics_path = output / "metrics.json"
    if not metrics_path.is_file():
        result["status"] = "offline-error"
        result["detail"] = "offline process returned success without metrics.json"
        return result
    result["metrics"] = _read_json(metrics_path)
    if method == "N":
        try:
            result["answer_records"] = _persist_npcs_answer_records(output)
        except (OSError, ValueError, RunnerError) as exc:
            result["status"] = "offline-error"
            result["detail"] = "cannot persist NPCS candidate answers: %s" % exc
    _assert_no_digest_fields(result)
    return result


def _worker_command(config_path: Path, run_dir: Path) -> List[str]:
    return [
        sys.executable,
        str(Path(__file__).resolve()),
        "_endpoint",
        "--config",
        str(config_path),
        "--run-dir",
        str(run_dir),
    ]


def _run_endpoint_worker(
    config: Mapping[str, Any], config_path: Path, run_dir: Path
) -> Dict[str, Any]:
    stdout_path = run_dir / "worker.stdout"
    stderr_path = run_dir / "worker.stderr"
    child = _run_child(
        _worker_command(config_path, run_dir),
        stdout_path,
        stderr_path,
        float(config["endpoint_timeout_s"]),
    )
    result_path = run_dir / "endpoint-result.json"
    if child["timed_out"]:
        return {
            "schema": ENDPOINT_SCHEMA,
            "status": "timeout",
            "method": config["method"],
            "scheme": config["scheme"],
            "endpoint_timeout_s": config["endpoint_timeout_s"],
            "detail": "endpoint worker exceeded its independent hard deadline",
            "outer_process": child,
            "recovery_required": str(config["method"]).startswith("C-"),
        }
    if not result_path.is_file():
        return {
            "schema": ENDPOINT_SCHEMA,
            "status": "runner-error",
            "method": config["method"],
            "scheme": config["scheme"],
            "endpoint_timeout_s": config["endpoint_timeout_s"],
            "detail": "endpoint worker exited without endpoint-result.json",
            "outer_process": child,
            "recovery_required": str(config["method"]).startswith("C-"),
        }
    result = _read_json(result_path)
    result["outer_process"] = child
    if result.get("status") != "ok" and str(config["method"]).startswith("C-"):
        result["recovery_required"] = True
    _assert_no_digest_fields(result)
    return result


def _answer_records_path(method: str, run_dir: Path) -> Path:
    return (
        run_dir / "pp" / "answer-records.jsonl"
        if method == "N"
        else run_dir / "offline" / "answer-records.jsonl"
    )


def _same_file_content(left: Path, right: Path) -> bool:
    if left.stat().st_size != right.stat().st_size:
        return False
    with left.open("rb") as first, right.open("rb") as second:
        while True:
            a = first.read(CHUNK_BYTES)
            b = second.read(CHUNK_BYTES)
            if a != b:
                return False
            if not a:
                return True


def _nearest_rank(values: Sequence[float], probability: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(probability * len(ordered)) - 1)
    return ordered[index]


def _summary(values: Sequence[float]) -> Dict[str, Any]:
    return {
        "count": len(values),
        "values": list(values),
        "median": statistics.median(values) if values else None,
        "mean": statistics.mean(values) if values else None,
        "standard_deviation": statistics.stdev(values) if len(values) > 1 else 0.0 if values else None,
        "minimum": min(values) if values else None,
        "maximum": max(values) if values else None,
        "q1_nearest_rank": _nearest_rank(values, 0.25),
        "q3_nearest_rank": _nearest_rank(values, 0.75),
        "iqr_nearest_rank": (
            _nearest_rank(values, 0.75) - _nearest_rank(values, 0.25)
            if values else None
        ),
    }


def _component_method_e2e(endpoint: Mapping[str, Any], offline: Mapping[str, Any]) -> Optional[float]:
    endpoint_metrics = endpoint.get("endpoint")
    offline_metrics = offline.get("metrics")
    if not isinstance(endpoint_metrics, Mapping) or not isinstance(offline_metrics, Mapping):
        return None
    endpoint_ms = endpoint_metrics.get("endpoint_e2e_ms")
    if endpoint_ms is None:
        return None
    if offline_metrics.get("timing_scope") == "offline_from_complete_response_file":
        offline_ms = offline_metrics.get("pp_hc_build_wall_ms")
        compiler_metrics = offline_metrics.get("compiler", {})
        pqe_ms = compiler_metrics.get("pqe_wall_ms") if isinstance(compiler_metrics, Mapping) else None
        return float(endpoint_ms) + float(offline_ms or 0) + float(pqe_ms or 0)
    return float(endpoint_ms) + float(offline_metrics.get("offline_wall_ms", 0))


def _validate_config(config: Mapping[str, Any]) -> None:
    method = str(config["method"])
    if method not in METHODS:
        raise RunnerError("unknown method: %s" % method)
    if config.get("scheme") not in ("Standard", "SPARQL_Star"):
        raise RunnerError("scheme must be Standard or SPARQL_Star")
    query = Path(str(config["query"]))
    if not query.is_file() or query.stat().st_size == 0:
        raise RunnerError("query is missing or empty: %s" % query)
    if method == "B" and not config.get("base_endpoint"):
        raise RunnerError("B requires --base-endpoint")
    if method != "B" and not config.get("reified_endpoint"):
        raise RunnerError("%s requires --reified-endpoint" % method)
    if method == "N" or method.startswith("C-"):
        jar = Path(str(config.get("jar") or ""))
        if not jar.is_file():
            raise RunnerError("%s requires an existing --jar" % method)
    if method.startswith("C-"):
        data = Path(str(config.get("reified_data") or ""))
        if not data.is_file():
            raise RunnerError("C requires an existing --reified-data path")
    if method in ("C-factored", "C-path") and not config.get("update_endpoint"):
        raise RunnerError("%s requires --update-endpoint" % method)
    if method == "C-path" and config.get("c_read_only"):
        raise RunnerError("C-path cannot use a read-only endpoint")
    backend = str(config["pqe_backend"])
    probability_path = config.get("probabilities")
    uniform = config.get("uniform_probability")
    if backend == "none" and (probability_path is not None or uniform is not None):
        raise RunnerError("probabilities require --pqe-backend oracle or cudd")
    if backend != "none" and ((probability_path is None) == (uniform is None)):
        raise RunnerError("PQE requires exactly one probability source")
    if probability_path is not None and not Path(str(probability_path)).is_file():
        raise RunnerError("probability file does not exist: %s" % probability_path)


def _run_cell(config: Dict[str, Any], output: Path) -> Dict[str, Any]:
    _validate_config(config)
    if output.exists():
        raise RunnerError("refusing to reuse cell output directory: %s" % output)
    output.mkdir(parents=True)
    query_source = Path(str(config["query"])).resolve()
    query_target = output / "query.rq"
    _atomic_bytes(query_target, query_source.read_bytes())
    config["query"] = str(query_target.resolve())
    config_path = output / "cell-config.json"
    _atomic_json(config_path, config)

    runs: List[Dict[str, Any]] = []
    stop = False
    first_measured_answers: Optional[Path] = None
    first_measured_circuit: Optional[Path] = None
    for phase, count in (("warmup", int(config["warmups"])), ("measured", int(config["runs"]))):
        for index in range(1, count + 1):
            if stop:
                break
            run_id = "%s-%02d" % (phase, index)
            run_dir = output / run_id
            run_dir.mkdir()
            run_started = time.perf_counter()
            endpoint = _run_endpoint_worker(config, config_path, run_dir)
            record: Dict[str, Any] = {
                "schema": RUN_SCHEMA,
                "run_id": run_id,
                "phase": phase,
                "index": index,
                "endpoint": endpoint,
                "offline": None,
                "status": endpoint.get("status"),
            }
            if endpoint.get("status") == "ok":
                offline = _run_offline(config, run_dir, run_id)
                record["offline"] = offline
                record["status"] = offline.get("status")
                if offline.get("status") == "ok":
                    answer_path = _answer_records_path(str(config["method"]), run_dir)
                    if phase == "measured":
                        if first_measured_answers is None:
                            first_measured_answers = answer_path
                            record["answer_records_equal_first_measured"] = True
                        else:
                            same = _same_file_content(first_measured_answers, answer_path)
                            record["answer_records_equal_first_measured"] = same
                            if not same:
                                record["status"] = "answer-mismatch"
                        if str(config["method"]).startswith("C-"):
                            circuit_path = run_dir / "circuit.nt"
                            if first_measured_circuit is None:
                                first_measured_circuit = circuit_path
                                record["circuit_equal_first_measured"] = True
                            else:
                                same_circuit = _same_file_content(first_measured_circuit, circuit_path)
                                record["circuit_equal_first_measured"] = same_circuit
                                if not same_circuit:
                                    record["status"] = "circuit-mismatch"
                    record["component_method_e2e_ms"] = _component_method_e2e(endpoint, offline)
            record["run_wall_ms"] = _round_ms(_milliseconds(run_started))
            record["artifact_bytes_before_run_record"] = _directory_bytes(run_dir)
            _assert_no_digest_fields(record)
            _atomic_json(run_dir / "run.json", record)
            runs.append(record)
            if record["status"] != "ok":
                stop = True
        if stop:
            break

    measured = [item for item in runs if item["phase"] == "measured" and item["status"] == "ok"]
    endpoint_values = [
        float(item["endpoint"]["endpoint"]["endpoint_e2e_ms"])
        for item in measured
    ]
    method_values = [
        float(item["component_method_e2e_ms"])
        for item in measured
        if item.get("component_method_e2e_ms") is not None
    ]
    expected_runs = int(config["warmups"]) + int(config["runs"])
    cell_status = "ok" if len(runs) == expected_runs and all(item["status"] == "ok" for item in runs) else "incomplete"
    result = {
        "schema": SCHEMA,
        "status": cell_status,
        "query_id": config["query_id"],
        "engine": config["engine"],
        "method": config["method"],
        "scheme": config["scheme"],
        "protocol": {
            "warmups": config["warmups"],
            "measured_runs": config["runs"],
            "endpoint_timeout_s_per_execution": config["endpoint_timeout_s"],
            "offline_timeout_s_per_execution": config["offline_timeout_s"],
            "failure_policy": "stop the cell after the first failed execution",
            "artifact_policy": "new immutable directory per execution",
        },
        "runs": runs,
        "summary": {
            "measured_successes": len(measured),
            "endpoint_e2e_ms": _summary(endpoint_values),
            "component_method_e2e_ms": _summary(method_values),
            "primary_statistic": "median of the configured measured executions",
            "warmups_excluded": True,
        },
        "artifact_bytes_before_cell_record": _directory_bytes(output),
    }
    _assert_no_digest_fields(result)
    _atomic_json(output / "cell.json", result)
    return result


def _run_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run one WatDiv query x engine x B/R/N/C method cell."
    )
    parser.add_argument("--query", required=True, type=Path)
    parser.add_argument("--query-id", required=True)
    parser.add_argument("--engine", required=True)
    parser.add_argument("--method", required=True, choices=METHODS)
    parser.add_argument(
        "--scheme",
        choices=("Standard", "SPARQL_Star"),
        default="SPARQL_Star",
        help="reification scheme used by R, N, and C (default: SPARQL_Star)",
    )
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--base-endpoint")
    parser.add_argument("--reified-endpoint")
    parser.add_argument("--update-endpoint")
    parser.add_argument("--jar", type=Path)
    parser.add_argument("--java", default="java")
    parser.add_argument("--reified-data", type=Path)
    parser.add_argument("--warmups", type=_nonnegative_integer, default=1)
    parser.add_argument("--runs", type=_positive_integer, default=5)
    parser.add_argument(
        "--endpoint-timeout",
        type=_positive_seconds,
        default=DEFAULT_ENDPOINT_TIMEOUT_S,
        help="independent hard deadline for each endpoint execution",
    )
    parser.add_argument(
        "--offline-timeout",
        type=_positive_seconds,
        default=DEFAULT_OFFLINE_TIMEOUT_S,
        help="independent deadline for each response-processing/PQE pipeline",
    )
    parser.add_argument("--pqe-backend", choices=("none", "oracle", "cudd"), default="none")
    probability = parser.add_mutually_exclusive_group()
    probability.add_argument("--probabilities", type=Path)
    probability.add_argument("--uniform-probability", type=float)
    parser.add_argument("--token-regex", default=DEFAULT_TOKEN_REGEX)
    parser.add_argument("--c-parallelism", type=_positive_integer, default=1)
    parser.add_argument("--c-read-only", action="store_true")
    parser.add_argument(
        "--skip-bnode-check",
        action="store_true",
        help="skip CircuitRun's store probe only after the loaded data is independently known to be ground",
    )
    return parser


def _configuration(args: argparse.Namespace) -> Dict[str, Any]:
    return {
        "schema": SCHEMA,
        "query": str(args.query.resolve()),
        "query_id": args.query_id,
        "engine": args.engine,
        "method": args.method,
        "scheme": args.scheme,
        "base_endpoint": args.base_endpoint,
        "reified_endpoint": args.reified_endpoint,
        "update_endpoint": args.update_endpoint,
        "jar": str(args.jar.resolve()) if args.jar is not None else None,
        "java": args.java,
        "reified_data": str(args.reified_data.resolve()) if args.reified_data is not None else None,
        "warmups": args.warmups,
        "runs": args.runs,
        "endpoint_timeout_s": args.endpoint_timeout,
        "offline_timeout_s": args.offline_timeout,
        "pqe_backend": args.pqe_backend,
        "probabilities": (
            str(args.probabilities.resolve()) if args.probabilities is not None else None
        ),
        "uniform_probability": args.uniform_probability,
        "token_regex": args.token_regex,
        "c_parallelism": args.c_parallelism,
        "c_read_only": bool(args.c_read_only),
        "skip_bnode_check": bool(args.skip_bnode_check),
    }


def _internal_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    subparsers = parser.add_subparsers(dest="internal_command", required=True)
    endpoint = subparsers.add_parser("_endpoint")
    endpoint.add_argument("--config", required=True, type=Path)
    endpoint.add_argument("--run-dir", required=True, type=Path)
    offline = subparsers.add_parser("_offline")
    offline.add_argument("offline_kind", choices=("response", "circuit"))
    offline.add_argument("--input", required=True, type=Path)
    offline.add_argument("--out", required=True, type=Path)
    offline.add_argument("--backend", choices=("none", "oracle", "cudd"), default="none")
    probability = offline.add_mutually_exclusive_group()
    probability.add_argument("--probabilities", type=Path)
    probability.add_argument("--uniform-probability", type=float)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0].startswith("_"):
        args = _internal_parser().parse_args(arguments)
        if args.internal_command == "_endpoint":
            return _endpoint_worker_main(args.config, args.run_dir)
        return _offline_worker_main(args)
    parser = _run_parser()
    args = parser.parse_args(arguments)
    try:
        result = _run_cell(_configuration(args), args.out.resolve())
    except (OSError, RunnerError, ValueError) as exc:
        parser.exit(1, "watdiv10m_runner: error: %s\n" % exc)
    print(json.dumps({
        "status": result["status"],
        "query_id": result["query_id"],
        "engine": result["engine"],
        "method": result["method"],
        "cell": str((args.out.resolve() / "cell.json")),
    }, ensure_ascii=False, sort_keys=True))
    return 0 if result["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
