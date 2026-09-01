#!/usr/bin/env python3
"""Run one measured WatDiv B/R/N/C cell and preserve its protocol artifacts.

A cell is one ``query x engine x method`` combination.  The default protocol is
one warm-up followed by five measured executions.  Every endpoint execution is
placed in its own process group and receives an independent hard deadline.
Offline response processing and PQE have a separate deadline.

The runner is deliberately cluster-neutral.  It records process-local resource
figures where the operating system exposes them; Slurm/cgroup sampling and job
placement belong to the deployment wrapper used on the evaluation cluster.
The default complete-JSON protocol remains available.  The opt-in streamed TSV
protocol keeps bounded B/R answer evidence and the compact provenance rows that
N post-processing requires instead of persisting the complete endpoint response.
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
import event_probabilities
import npcs_postprocess
import sparql_results_tsv
from stage_memory import StageRssSampler, stage_peak_rss_bytes


SCHEMA = "watdiv-brnc-cell-v1"
RUN_SCHEMA = "watdiv-brnc-run-v1"
ENDPOINT_SCHEMA = "watdiv-brnc-endpoint-v1"
OFFLINE_SCHEMA = "watdiv-brnc-offline-v1"
OFFLINE_STAGE_SCHEMA = "watdiv-brnc-offline-stage-v1"
C_STAGE_SCHEMA = "sparqlcirc-c-stage-v1"
C_STAGE_PREFIX = "# sc-stage "
OFFLINE_RESUME_SCHEMA = "watdiv-brnc-offline-resume-v1"
METHODS = ("B", "R", "N", "C-flat", "C-factorised", "C-path")
JSON_RESULTS = "application/sparql-results+json"
TSV_RESULTS = "text/tab-separated-values"
NT_RESULTS = "application/n-triples"
DEFAULT_ENDPOINT_TIMEOUT_S = 600.0
DEFAULT_OFFLINE_TIMEOUT_S = 600.0
DEFAULT_TOKEN_REGEX = r"^urn:t:[0-9]+$"
CHUNK_BYTES = 64 * 1024
DEFAULT_MEMORY_SAMPLE_INTERVAL_S = 0.05
DEFAULT_RESPONSE_MODE = "full-json"
DEFAULT_EXACT_RESPONSE_ROW_LIMIT = 1_000_000
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


def _java_heap_size(value: str) -> str:
    if re.fullmatch(r"[1-9][0-9]*[kKmMgG]", value) is None:
        raise argparse.ArgumentTypeError("Java heap must look like 512m or 192g")
    return value


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


def _append_json_line(path: Path, value: Mapping[str, Any]) -> None:
    """Append one flushed event that survives a native child-process crash."""
    _assert_no_digest_fields(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")
    with path.open("ab") as handle:
        handle.write(encoded)
        handle.flush()


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
    process_memory = StageRssSampler(
        process.pid, float(config.get("memory_sample_interval_s", DEFAULT_MEMORY_SAMPLE_INTERVAL_S))
    ).start()
    process_memory.set_stage("npcs_rewrite")
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
    process_memory_result = process_memory.finish()
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
        "process_tree_peak_rss_bytes": stage_peak_rss_bytes(
            process_memory_result, "npcs_rewrite"
        ),
        "stage_peak_memory": process_memory_result,
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


def _persist_query_response_json(
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


# Kept for callers that explicitly exercise the legacy complete-JSON protocol.
_stream_query_response = _persist_query_response_json


class _StreamingTsvCollector:
    """Keep bounded answer evidence, or the provenance payload required by N+PP."""

    def __init__(self, method: str, output: Path, exact_row_limit: int,
                 context: Mapping[str, str]) -> None:
        self.method = method
        self.output = output
        self.exact_row_limit = exact_row_limit
        self.context = dict(context)
        self.lines = sparql_results_tsv.TsvLineStream()
        self.variables: Optional[List[str]] = None
        self.solution_rows = 0
        self.counts: Counter[str] = Counter()
        self.exact = method in ("B", "R")
        self.provenance_variable: Optional[str] = None
        self.answer_variables: List[str] = []
        self.provenance_target = output / "npcs-provenance.jsonl"
        self.provenance_partial = self.provenance_target.with_name(
            self.provenance_target.name + ".partial"
        )
        self.provenance_handle: Optional[Any] = None
        self.provenance_payload_bytes = 0
        self.provenance_field_encodings: Counter[str] = Counter()

    def _start(self, line: str) -> None:
        self.variables = sparql_results_tsv.parse_header(line)
        self.output.mkdir(parents=True, exist_ok=False)
        if self.method == "N":
            self.provenance_variable = npcs_postprocess._provenance_variable(
                self.variables, None
            )
            self.answer_variables = sorted(
                variable for variable in self.variables
                if variable != self.provenance_variable
            )
            self.provenance_handle = self.provenance_partial.open("xb")

    def _consume_line(self, line: str) -> None:
        if self.variables is None:
            self._start(line)
            return
        self.solution_rows += 1
        if self.method in ("B", "R"):
            if not self.exact:
                return
            if self.solution_rows > self.exact_row_limit:
                self.exact = False
                self.counts.clear()
                return
            fields = sparql_results_tsv.split_row(line, self.variables)
            binding = sparql_results_tsv.binding_value(
                fields, self.variables, allow_bare_text=True
            )
            self.counts[_binding_text(binding)] += 1
            return

        fields = sparql_results_tsv.split_row(line, self.variables)
        assert self.provenance_variable is not None
        assert self.provenance_handle is not None
        by_variable = dict(zip(self.variables, fields))
        provenance, provenance_encoding = sparql_results_tsv.literal_lexical(
            by_variable[self.provenance_variable], allow_bare_text=True
        )
        self.provenance_field_encodings[provenance_encoding] += 1
        answer_key = json.dumps(
            [
                [
                    variable,
                    sparql_results_tsv.term_key(
                        by_variable[variable], allow_bare_text=True
                    ),
                ]
                for variable in self.answer_variables
            ],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        provenance_bytes = len(provenance.encode("utf-8"))
        record = dict(self.context)
        record.update({
            "answer_key": answer_key,
            "provenance": provenance,
            "utf8_bytes": provenance_bytes,
        })
        self.provenance_handle.write(
            (json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
             + "\n").encode("utf-8")
        )
        self.provenance_payload_bytes += provenance_bytes

    def feed(self, chunk: bytes) -> None:
        for line in self.lines.feed(chunk):
            self._consume_line(line)

    def finish(self, response_bytes: int) -> Dict[str, Any]:
        for line in self.lines.finish():
            self._consume_line(line)
        if self.variables is None:
            raise sparql_results_tsv.TsvResultsError("endpoint returned an empty TSV response")
        if self.provenance_handle is not None:
            self.provenance_handle.flush()
            os.fsync(self.provenance_handle.fileno())
            self.provenance_handle.close()
            self.provenance_handle = None
            os.replace(self.provenance_partial, self.provenance_target)

        if self.method in ("B", "R") and self.exact:
            records = [
                {"binding": json.loads(binding), "multiplicity": self.counts[binding]}
                for binding in sorted(self.counts)
            ]
            _atomic_json_lines(self.output / "answer-records.jsonl", records)
            distinct_binding_count: Optional[int] = len(records)
            evidence_mode = "exact-multiset"
        elif self.method in ("B", "R"):
            distinct_binding_count = None
            evidence_mode = "cardinality-only"
        else:
            distinct_binding_count = self.solution_rows
            evidence_mode = "npcs-provenance"

        evidence = {
            "schema": "sparql-results-stream-v1",
            "response_format": "SPARQL Results TSV",
            "method": self.method,
            "variables": self.variables,
            "solution_rows": self.solution_rows,
            "distinct_binding_count": distinct_binding_count,
            "answer_evidence_mode": evidence_mode,
            "exact_row_limit": self.exact_row_limit,
            "response_bytes": response_bytes,
            "raw_response_persisted": False,
            "provenance_payload_bytes": (
                self.provenance_payload_bytes if self.method == "N" else None
            ),
            "provenance_field_encodings": (
                dict(sorted(self.provenance_field_encodings.items()))
                if self.method == "N" else None
            ),
            "retained_artifact": (
                "answer-records.jsonl" if evidence_mode == "exact-multiset"
                else "npcs-provenance.jsonl" if evidence_mode == "npcs-provenance"
                else None
            ),
        }
        _atomic_json(self.output / "evidence.json", evidence)
        return evidence

    def abort(self) -> None:
        if self.provenance_handle is not None:
            self.provenance_handle.close()
            self.provenance_handle = None
        if self.provenance_partial.exists():
            self.provenance_partial.unlink()


def _stream_query_response_tsv(
    endpoint: str,
    query: str,
    output: Path,
    deadline: float,
    method: str,
    exact_row_limit: int,
    context: Mapping[str, str],
) -> Dict[str, Any]:
    request = urllib.request.Request(
        endpoint,
        data=query.encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/sparql-query", "Accept": TSV_RESULTS},
    )
    collector = _StreamingTsvCollector(method, output, exact_row_limit, context)
    request_started = time.perf_counter()
    first_byte_at: Optional[float] = None
    last_byte_at: Optional[float] = None
    byte_count = 0
    try:
        with _NO_PROXY_OPENER.open(
            request, timeout=max(0.001, _remaining(deadline))
        ) as response:
            while True:
                chunk = response.read(CHUNK_BYTES)
                if not chunk:
                    break
                now = time.perf_counter()
                if first_byte_at is None:
                    first_byte_at = now
                last_byte_at = now
                byte_count += len(chunk)
                collector.feed(chunk)
                _remaining(deadline)
        response_finished = time.perf_counter()
        evidence = collector.finish(byte_count)
    except urllib.error.HTTPError as exc:
        collector.abort()
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
        collector.abort()
        reason = getattr(exc, "reason", exc)
        if isinstance(reason, (socket.timeout, TimeoutError)):
            raise StageError("timeout", "HTTP request timed out: %s" % reason) from exc
        raise StageError("network-error", str(reason)) from exc
    except (socket.timeout, TimeoutError) as exc:
        collector.abort()
        raise StageError("timeout", "HTTP request timed out: %s" % exc) from exc
    except sparql_results_tsv.TsvResultsError as exc:
        collector.abort()
        raise StageError("invalid-response", str(exc)) from exc
    except FileExistsError as exc:
        collector.abort()
        raise StageError("artifact-error", str(exc)) from exc

    finalized_at = time.perf_counter()
    request_ms = (response_finished - request_started) * 1000.0
    ttfb_ms = (
        (first_byte_at - request_started) * 1000.0 if first_byte_at is not None else None
    )
    drain_ms = (
        (last_byte_at - first_byte_at) * 1000.0
        if first_byte_at is not None and last_byte_at is not None
        else 0.0
    )
    return {
        "request_ms": _round_ms(request_ms),
        "ttfb_ms": _round_ms(ttfb_ms) if ttfb_ms is not None else None,
        "response_drain_ms": _round_ms(drain_ms),
        "response_finalize_ms": _round_ms((finalized_at - response_finished) * 1000.0),
        "response_bytes": byte_count,
        "response_lines": evidence["solution_rows"] + 1,
        "response_format": evidence["response_format"],
        "solution_rows": evidence["solution_rows"],
        "distinct_binding_count": evidence["distinct_binding_count"],
        "answer_evidence_mode": evidence["answer_evidence_mode"],
        "provenance_payload_bytes": evidence["provenance_payload_bytes"],
        "provenance_field_encodings": evidence["provenance_field_encodings"],
        "raw_response_persisted": False,
        "retained_response_bytes": _directory_bytes(output),
        "timing_scope": (
            "client observed; TTFB includes connection, endpoint evaluation, headers, and first payload; "
            "drain includes remaining transfer and incremental TSV row handling"
        ),
    }


def _parse_c_stage_records(text: str, method: str, plan_steps: int,
                           path_construct_requests: int) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    for line_number, line in enumerate(text.splitlines(), 1):
        if not line.startswith(C_STAGE_PREFIX):
            continue
        try:
            record = json.loads(line[len(C_STAGE_PREFIX):])
        except json.JSONDecodeError as exc:
            raise StageError(
                "c-protocol-error",
                "invalid structured C timing record on stderr line %d: %s"
                % (line_number, exc),
            ) from exc
        if not isinstance(record, dict):
            raise StageError(
                "c-protocol-error",
                "structured C timing record on stderr line %d is not an object"
                % line_number,
            )
        if record.get("schema") != C_STAGE_SCHEMA:
            raise StageError(
                "c-protocol-error",
                "unexpected structured C timing schema on stderr line %d" % line_number,
            )
        if not isinstance(record.get("event"), str) or not record["event"]:
            raise StageError(
                "c-protocol-error",
                "structured C timing record on stderr line %d has no event" % line_number,
            )
        for key, value in record.items():
            if key == "duration_ms" or key.endswith("_ms"):
                if (
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not math.isfinite(float(value))
                    or float(value) < 0
                ):
                    raise StageError(
                        "c-protocol-error",
                        "structured C timing field %s on stderr line %d is not a "
                        "non-negative finite number" % (key, line_number),
                    )
        _assert_no_digest_fields(record)
        records.append(record)

    counts = Counter(str(record["event"]) for record in records)
    common = (
        "query_read",
        "plan_generation",
        "repository_init",
        "data_ready",
        "normalization",
        "construction_complete",
        "serialization",
        "named_graph_persist",
        "endpoint_cleanup",
        "run_complete",
    )
    bad_common = [event for event in common if counts[event] != 1]
    if bad_common:
        raise StageError(
            "c-protocol-error",
            "structured C timing has non-unique required event(s): %s"
            % ", ".join(bad_common),
        )
    failed_cleanup = [
        str(record["event"])
        for record in records
        if record["event"] in ("workspace_cleanup", "endpoint_cleanup")
        and record.get("success") is False
    ]
    if failed_cleanup:
        raise StageError(
            "c-cleanup-error",
            "CircuitRun reported failed cleanup stage(s): %s"
            % ", ".join(failed_cleanup),
            {"cleanup_events": failed_cleanup},
        )
    if method == "C-path":
        if counts["path_construct"] != path_construct_requests:
            raise StageError(
                "c-protocol-error",
                "structured path step count %d does not match %d logged CONSTRUCT requests"
                % (counts["path_construct"], path_construct_requests),
            )
        if counts["path_source_complete"] < 1:
            raise StageError(
                "c-protocol-error", "structured path timing has no completed source"
            )
    else:
        if counts["workspace_cleanup"] != 1:
            raise StageError(
                "c-protocol-error",
                "structured non-path timing has no unique workspace cleanup event",
            )
        if counts["construct_step"] + counts["closure_step"] != plan_steps:
            raise StageError(
                "c-protocol-error",
                "structured top-level step count %d does not match the %d-step plan"
                % (counts["construct_step"] + counts["closure_step"], plan_steps),
            )
    return records


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
        expected = {
            "C-flat": "flat",
            "C-factorised": "factorised",
        }[method]
        if len(mode_matches) != 1:
            raise StageError("c-protocol-error", "CircuitRun emitted no unique construction-mode marker")
        requested, effective = (item.lower() for item in mode_matches[0])
        factorised_flat = method == "C-factorised" and effective == "flat" and bool(fallback)
        strict_mode = requested == expected and (
            (effective == expected and not fallback) or factorised_flat
        )
        if not strict_mode:
            raise StageError(
                "c-mode-error",
                "strict C mode was not honored: requested=%s effective=%s fallback=%s"
                % (requested, effective, fallback[0] if fallback else None),
            )
    plan_steps = len(re.findall(r"(?m)^# --- step [0-9]+ ---\s*$", text))
    path_construct_requests = len(
        re.findall(r"(?m)^# --- path CONSTRUCT ---\s*$", text)
    )
    result: Dict[str, Any] = {
        "requested_mode": requested,
        "effective_mode": effective,
        "fallback_reason": fallback[0] if fallback else None,
        "construction_ms": _round_ms(float(construction[0])),
        "plan_steps": plan_steps,
        "path_construct_requests": path_construct_requests,
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
    heap_peak = re.findall(
        r"(?m)^# heap_peak: used_bytes=([0-9]+), "
        r"committed_bytes_at_peak=([0-9]+), max_bytes=(-?[0-9]+), "
        r"samples=([0-9]+), interval_ms=([0-9]+)\s*$",
        text,
    )
    if heap_peak:
        used, committed, maximum, samples, interval = heap_peak[-1]
        result["jvm_heap_peak"] = {
            "peak_used_bytes": int(used),
            "committed_bytes_at_peak": int(committed),
            "max_bytes": int(maximum),
            "samples": int(samples),
            "sample_interval_ms": int(interval),
            "scope": "CircuitRun JVM heap only; excludes metaspace, native, and GraphDB",
            "source": "java.lang.management.MemoryMXBean",
            "gc_requested_by_sampler": False,
        }
    records = _parse_c_stage_records(
        text, method, plan_steps, path_construct_requests
    )
    plan_record = next(
        record for record in records if record["event"] == "plan_generation"
    )
    strategy_fragments = plan_record.get("strategy_fragments", [])
    if (
        not isinstance(strategy_fragments, list)
        or any(not isinstance(item, str) or not item for item in strategy_fragments)
    ):
        raise StageError(
            "c-protocol-error",
            "structured plan_generation strategy_fragments must be a list of non-empty strings",
        )
    result["strategy_fragments"] = strategy_fragments
    counts = Counter(str(record["event"]) for record in records)
    result["structured_timing"] = {
        "schema": C_STAGE_SCHEMA,
        "artifact": "c-stages.jsonl",
        "record_count": len(records),
        "event_counts": dict(sorted(counts.items())),
        "complete": True,
        "aggregation_note": (
            "parallel step durations may overlap and must not be summed as wall time"
        ),
    }
    result["_structured_timing_records"] = records
    return result


def _circuit_construction_mode(method: str) -> str:
    return {
        "C-flat": "flat",
        "C-factorised": "factorised",
        # CircuitRun selects its dedicated property-path protocol from the
        # parsed algebra; the construction option is not consulted for paths.
        "C-path": "factorised",
    }[method]


def _circuit_requires_cleanup(method: str) -> bool:
    """Return whether this protocol can leave private state on the endpoint."""
    return method in ("C-factorised", "C-path")


def _run_c(config: Mapping[str, Any], run_dir: Path, deadline: float) -> Dict[str, Any]:
    method = str(config["method"])
    construction = _circuit_construction_mode(method)
    circuit = run_dir / "circuit.nt"
    circuit_partial = circuit.with_name(circuit.name + ".partial")
    stderr_target = run_dir / "circuit.stderr"
    stderr_partial = stderr_target.with_name(stderr_target.name + ".partial")
    command = [str(config["java"])]
    if config.get("java_max_heap"):
        command.append("-Xmx%s" % str(config["java_max_heap"]))
    command.extend([
        "-jar",
        str(config["jar"]),
        "circuit",
        "--construction=%s" % construction,
        str(config["scheme"]),
        str(config["reified_data"]),
        str(config["query"]),
        str(config["reified_endpoint"]),
    ])
    environment = dict(os.environ)
    environment["CIRCUIT_SKIP_LOAD"] = "1"
    environment.pop("CIRCUIT_CLEANUP", None)
    environment.pop("CIRCUIT_UPDATE_ENDPOINT", None)
    environment.pop("CIRCUIT_ENDPOINT_PROTOCOL", None)
    if _circuit_requires_cleanup(method):
        environment["CIRCUIT_CLEANUP"] = "1"
    environment["CIRCUIT_PARALLELISM"] = str(config["c_parallelism"])
    environment["CIRCUIT_STRUCTURED_TIMING"] = "1"
    environment["CIRCUIT_HEAP_SAMPLING_MS"] = str(
        int(config.get("jvm_heap_sample_interval_ms", 100))
    )
    if config.get("update_endpoint"):
        environment["CIRCUIT_UPDATE_ENDPOINT"] = str(config["update_endpoint"])
    environment["CIRCUIT_ENDPOINT_PROTOCOL"] = str(
        config.get("c_endpoint_protocol", "sparql")
    )
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
    process_memory = StageRssSampler(
        process.pid, float(config.get("memory_sample_interval_s", DEFAULT_MEMORY_SAMPLE_INTERVAL_S))
    ).start()
    process_memory.set_stage("circuitrun")
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
    process_memory_result = process_memory.finish()
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
    os.replace(circuit_partial, circuit)
    persisted_at = time.perf_counter()
    stderr_text = stderr_target.read_text(encoding="utf-8", errors="replace")
    protocol = _parse_c_stderr(stderr_text, method)
    stage_records = protocol.pop("_structured_timing_records")
    _atomic_json_lines(run_dir / "c-stages.jsonl", stage_records)
    endpoint_e2e_ms = (persisted_at - started) * 1000.0
    protocol["process_ms"] = _round_ms(process_ms)
    protocol["endpoint_e2e_ms"] = _round_ms(endpoint_e2e_ms)
    protocol["outside_reported_construction_ms"] = _round_ms(
        max(0.0, endpoint_e2e_ms - float(protocol["construction_ms"]))
    )
    protocol["circuit_bytes"] = circuit.stat().st_size
    protocol["empty_circuit"] = protocol["circuit_bytes"] == 0
    protocol["stderr_bytes"] = stderr_target.stat().st_size
    protocol["process_tree_peak_rss_bytes"] = stage_peak_rss_bytes(
        process_memory_result, "circuitrun"
    )
    protocol["stage_peak_memory"] = process_memory_result
    protocol["timing_scope"] = (
        "CircuitRun process start through atomic circuit persistence; construction_ms is the "
        "engine client's plan execution plus final normalization marker emitted by CircuitRun"
    )
    return protocol


def _endpoint_run(config: Mapping[str, Any], run_dir: Path) -> Dict[str, Any]:
    started = time.perf_counter()
    deadline = time.monotonic() + float(config["endpoint_timeout_s"])
    method = str(config["method"])
    sample_interval = float(
        config.get("memory_sample_interval_s", DEFAULT_MEMORY_SAMPLE_INTERVAL_S)
    )
    client_memory = StageRssSampler(interval_s=sample_interval).start()
    engine_pid = config.get("engine_pid")
    engine_memory = (
        StageRssSampler(int(engine_pid), sample_interval).start()
        if engine_pid is not None
        else None
    )

    def memory_stage(name: str, engine_active: bool = False) -> None:
        client_memory.set_stage(name)
        if engine_memory is not None:
            engine_memory.set_stage(name if engine_active else None)

    try:
        if method.startswith("C-"):
            memory_stage("circuit_construct_and_persist", engine_active=True)
            metrics = _run_c(config, run_dir, deadline)
            endpoint_e2e_ms = float(metrics["endpoint_e2e_ms"])
            rewrite = None
        else:
            memory_stage("query_read")
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
                memory_stage("rewrite")
                expected = config.get("expected_r_query")
                if expected is not None:
                    rewrite_started = time.perf_counter()
                    rewritten = Path(str(expected)).read_text(encoding="utf-8")
                    target = run_dir / "rewritten.rq"
                    _atomic_text(target, rewritten)
                    rewrite = {
                        "kind": "frozen-row-reification",
                        "scheme": config["scheme"],
                        "rewrite_ms": _round_ms(_milliseconds(rewrite_started)),
                        "rewritten_query_bytes": target.stat().st_size,
                    }
                else:
                    rewritten, rewrite = _run_reification_rewrite(
                        query, run_dir, str(config["scheme"])
                    )
                endpoint = str(config["reified_endpoint"])
            elif method == "N":
                memory_stage("rewrite")
                rewritten, rewrite = _run_java_rewrite(config, run_dir, deadline)
                endpoint = str(config["reified_endpoint"])
            else:
                raise StageError("configuration-error", "unknown method: %s" % method)
            memory_stage("endpoint_query_serialize_transfer", engine_active=True)
            if config.get("response_mode", DEFAULT_RESPONSE_MODE) == "stream-tsv":
                request_metrics = _stream_query_response_tsv(
                    endpoint,
                    rewritten,
                    run_dir / "response",
                    deadline,
                    method,
                    int(config.get(
                        "exact_response_row_limit", DEFAULT_EXACT_RESPONSE_ROW_LIMIT
                    )),
                    {
                        "query_id": str(config["query_id"]),
                        "run_id": run_dir.name,
                        "engine": str(config["engine"]),
                        "method": method,
                    },
                )
                endpoint_scope = (
                    "worker start, query read and rewrite through complete TSV drain and "
                    "bounded response-evidence finalization"
                )
            else:
                request_metrics = _persist_query_response_json(
                    endpoint, rewritten, run_dir / "raw-response.json", deadline
                )
                endpoint_scope = (
                    "worker start, query read and rewrite through atomic persistence of the "
                    "complete JSON response"
                )
            endpoint_e2e_ms = _milliseconds(started)
            metrics = dict(request_metrics)
            metrics["query_read_ms"] = _round_ms(query_read_ms)
            metrics["endpoint_e2e_ms"] = _round_ms(endpoint_e2e_ms)
            metrics["endpoint_e2e_scope"] = endpoint_scope
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
    result["stage_peak_memory"] = {
        "client_process_tree": client_memory.finish(),
        "engine_process_tree": (
            engine_memory.finish()
            if engine_memory is not None
            else {
                "schema": "sparqlcirc-stage-rss-v1",
                "available": False,
                "reason": "no engine PID was supplied",
                "stages": {},
            }
        ),
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


def _canonical_response_records(
    raw: bytes, memory_sampler: Optional[StageRssSampler] = None
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    if memory_sampler is not None:
        memory_sampler.set_stage("json_decode")
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
    if memory_sampler is not None:
        memory_sampler.set_stage("canonicalize_answers")
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


def _canonicalize_response(
    response: Path, output: Path,
    memory_sample_interval_s: float = DEFAULT_MEMORY_SAMPLE_INTERVAL_S,
) -> Dict[str, Any]:
    if output.exists():
        raise RunnerError("refusing to reuse offline output directory: %s" % output)
    output.mkdir(parents=True)
    memory_sampler = StageRssSampler(interval_s=memory_sample_interval_s).start()
    started = time.perf_counter()
    memory_sampler.set_stage("response_read")
    read_started = time.perf_counter()
    raw = response.read_bytes()
    read_ms = _milliseconds(read_started)
    records, metrics = _canonical_response_records(raw, memory_sampler)
    memory_sampler.set_stage("answer_record_persist")
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
        "stage_peak_memory": memory_sampler.finish(),
        "process_peak_rss_bytes": _resource_metrics()["client_peak_rss_bytes"],
    })
    _atomic_json(output / "metrics.json", metrics)
    return metrics


def _finalize_streamed_response(response: Path, output: Path) -> Dict[str, Any]:
    """Expose bounded endpoint evidence without recreating the discarded response."""
    if output.exists():
        raise RunnerError("refusing to reuse offline output directory: %s" % output)
    evidence = _read_json(response / "evidence.json")
    if evidence.get("schema") != "sparql-results-stream-v1":
        raise RunnerError("unexpected streamed response evidence schema")
    mode = str(evidence.get("answer_evidence_mode"))
    if mode not in ("exact-multiset", "cardinality-only"):
        raise RunnerError("B/R streamed response has unsupported evidence mode: %s" % mode)
    output.mkdir(parents=True)
    started = time.perf_counter()
    if mode == "exact-multiset":
        source = response / "answer-records.jsonl"
        target = output / "answer-records.jsonl"
        if not source.is_file():
            raise RunnerError("streamed exact answer records are missing")
        os.link(source, target)
        answer_record_bytes: Optional[int] = target.stat().st_size
    else:
        summary = {
            "schema": "sparql-answer-cardinality-v1",
            "variables": evidence["variables"],
            "solution_rows": evidence["solution_rows"],
            "answer_evidence_mode": mode,
        }
        _atomic_json(output / "answer-summary.json", summary)
        answer_record_bytes = None
    metrics = {
        "schema": OFFLINE_SCHEMA,
        "kind": "streamed-sparql-results-evidence",
        "timing_scope": "offline_from_bounded_stream_evidence",
        "answer_evidence_mode": mode,
        "solution_rows": int(evidence["solution_rows"]),
        "distinct_binding_count": evidence.get("distinct_binding_count"),
        "raw_response_bytes": int(evidence["response_bytes"]),
        "raw_response_persisted": False,
        "answer_record_bytes": answer_record_bytes,
        "offline_wall_ms": _round_ms(_milliseconds(started)),
    }
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
    probability_path: Optional[Path],
    uniform: Optional[float],
    tokens: Sequence[str],
    probability_seed: Optional[int] = None,
) -> Dict[str, float]:
    if sum(
        source is not None for source in (probability_path, uniform, probability_seed)
    ) != 1:
        raise RunnerError("choose exactly one probability source for PQE")
    if probability_seed is not None:
        try:
            return event_probabilities.event_weights(tokens, probability_seed)
        except ValueError as exc:
            raise RunnerError(str(exc)) from exc
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
    memory_sample_interval_s: float = DEFAULT_MEMORY_SAMPLE_INTERVAL_S,
    probability_seed: Optional[int] = None,
) -> Dict[str, Any]:
    if output.exists():
        raise RunnerError("refusing to reuse offline output directory: %s" % output)
    output.mkdir(parents=True)
    memory_sampler = StageRssSampler(interval_s=memory_sample_interval_s).start()
    started = time.perf_counter()
    journal_path = output / "offline-stage-events.jsonl"
    stage_started: Dict[str, float] = {}
    stage_event_count = 0
    stage_journal_write_ms = 0.0

    def write_stage_event(event: Mapping[str, Any]) -> None:
        nonlocal stage_event_count, stage_journal_write_ms
        write_started = time.perf_counter()
        _append_json_line(journal_path, event)
        stage_journal_write_ms += _milliseconds(write_started)
        stage_event_count += 1

    def start_stage(stage: str, **fields: Any) -> None:
        memory_sampler.set_stage(stage)
        event = {
            "schema": OFFLINE_STAGE_SCHEMA,
            "event": "start",
            "stage": stage,
            "offline_elapsed_ms": _round_ms(_milliseconds(started)),
        }
        event.update(fields)
        write_stage_event(event)
        stage_started[stage] = time.perf_counter()

    def complete_stage(stage: str, **fields: Any) -> None:
        stage_start = stage_started.pop(stage)
        event = {
            "schema": OFFLINE_STAGE_SCHEMA,
            "event": "complete",
            "stage": stage,
            "duration_ms": _round_ms(_milliseconds(stage_start)),
            "offline_elapsed_ms": _round_ms(_milliseconds(started)),
        }
        event.update(fields)
        write_stage_event(event)

    start_stage("circuit_decode")
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
    complete_stage(
        "circuit_decode", circuit_triple_lines=circuit_lines, source_gate_count=len(circuit)
    )
    start_stage("answer_index")
    binding_by_text: Dict[str, List[Any]] = {}

    def answer_key_for_root(root: str) -> str:
        binding = _circuit_binding(bindings.get(root, {}))
        key = _binding_text(binding)
        binding_by_text.setdefault(key, binding)
        return key

    roots, answer_root_normalization = circuit_io.merge_answer_roots(
        circuit, answers, answer_key_for_root
    )
    records_by_text: Dict[str, Dict[str, Any]] = {
        key: {"binding": binding_by_text[key], "multiplicity": 1}
        for key in roots
    }
    complete_stage(
        "answer_index",
        answer_count=len(roots),
        raw_answer_root_count=len(answers),
    )
    start_stage("answer_record_persist")
    persist_started = time.perf_counter()
    answer_target = output / "answer-records.jsonl"
    _atomic_json_lines(answer_target, (records_by_text[key] for key in sorted(records_by_text)))
    persist_ms = _milliseconds(persist_started)
    complete_stage("answer_record_persist", artifact_bytes=answer_target.stat().st_size)
    start_stage("reachable_circuit_stats")
    structure = _reachable_circuit_stats(circuit, roots.values())
    complete_stage(
        "reachable_circuit_stats",
        reachable_nodes=structure["nodes"],
        reachable_edges=structure["edges"],
    )
    metrics: Dict[str, Any] = {
        "schema": OFFLINE_SCHEMA,
        "kind": "circuit-decode-and-pqe",
        "circuit_stream_parse_ms": _round_ms(decode_ms),
        "circuit_bytes": circuit_path.stat().st_size,
        "circuit_triple_lines": circuit_lines,
        "answer_count": len(roots),
        "raw_answer_root_count": len(answers),
        "answer_root_normalization": answer_root_normalization,
        "answer_record_persist_ms": _round_ms(persist_ms),
        "answer_record_bytes": answer_target.stat().st_size,
        "answer_reachable_circuit": structure,
        "pqe_backend": backend,
        "offline_stage_journal": journal_path.name,
    }
    if backend != "none":
        import compiler

        metrics["probability_source"] = (
            {
                "kind": "seeded-event",
                "seed": probability_seed,
                "scheme": event_probabilities.PROBABILITY_SCHEME,
            }
            if probability_seed is not None
            else {"kind": "file" if probabilities is not None else "uniform"}
        )
        pqe_started = time.perf_counter()
        start_stage("probability_load")
        probability_started = time.perf_counter()
        order = tuple(compiler.deterministic_order(circuit, roots))
        weights = _load_weights(
            probabilities, uniform_probability, order, probability_seed
        )
        probability_load_ms = _milliseconds(probability_started)
        complete_stage("probability_load", variable_count=len(order))
        start_stage("pqe_compile", variable_count=len(order), root_count=len(roots))
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
        complete_stage(
            "pqe_compile",
            compiled_nodes_unique=batch.metrics.get("compiled_nodes_unique"),
        )
        start_stage("pqe_wmc", root_count=len(roots))
        wmc_started = time.perf_counter()
        values = batch.wmc_many(weights)
        wmc_wall_ms = _milliseconds(wmc_started)
        complete_stage("pqe_wmc", result_count=len(values))
        start_stage("pqe_artifact_persist")
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
        complete_stage(
            "pqe_artifact_persist",
            probability_record_count=len(values),
        )
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
    metrics["offline_stage_event_count"] = stage_event_count
    metrics["offline_stage_journal_write_ms"] = _round_ms(stage_journal_write_ms)
    metrics["offline_wall_ms"] = _round_ms(_milliseconds(started))
    metrics["stage_peak_memory"] = memory_sampler.finish()
    metrics["process_peak_rss_bytes"] = _resource_metrics()["client_peak_rss_bytes"]
    _atomic_json(output / "metrics.json", metrics)
    return metrics


def _offline_worker_main(args: argparse.Namespace) -> int:
    try:
        if args.offline_kind == "response":
            metrics = _canonicalize_response(
                args.input, args.out, args.memory_sample_interval
            )
        elif args.offline_kind == "response-stream":
            metrics = _finalize_streamed_response(args.input, args.out)
        elif args.offline_kind == "circuit":
            metrics = _process_circuit(
                args.input,
                args.out,
                args.backend,
                args.probabilities,
                args.uniform_probability,
                args.memory_sample_interval,
                probability_seed=args.probability_seed,
            )
        else:
            raise RunnerError("unknown offline worker kind")
    except BaseException as exc:
        sys.stderr.write("%s: %s\n" % (type(exc).__name__, exc))
        return 1
    sys.stdout.write(json.dumps(metrics, ensure_ascii=False, sort_keys=True) + "\n")
    return 0


def _run_child(
    command: Sequence[str],
    stdout_path: Path,
    stderr_path: Path,
    timeout_s: float,
    memory_sample_interval_s: float = DEFAULT_MEMORY_SAMPLE_INTERVAL_S,
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
    process_memory = StageRssSampler(
        process.pid, memory_sample_interval_s
    ).start()
    process_memory.set_stage("child_process")
    returncode, wall_ms, timed_out = _wait_process(process, timeout_s)
    process_memory_result = process_memory.finish()
    _finalize_open_file(stdout_handle, stdout_partial, stdout_path)
    _finalize_open_file(stderr_handle, stderr_partial, stderr_path)
    result = {
        "returncode": returncode,
        "wall_ms": _round_ms(wall_ms),
        "timed_out": timed_out,
        "stdout_bytes": stdout_path.stat().st_size,
        "stderr_bytes": stderr_path.stat().st_size,
        "parent_observed_wall_ms": _round_ms(_milliseconds(started)),
        "process_tree_peak_rss_bytes": stage_peak_rss_bytes(
            process_memory_result, "child_process"
        ),
        "stage_peak_memory": process_memory_result,
    }
    if returncode < 0:
        signal_number = -int(returncode)
        try:
            signal_name = signal.Signals(signal_number).name
        except ValueError:
            signal_name = "SIGNAL_%d" % signal_number
        result["signal_number"] = signal_number
        result["signal_name"] = signal_name
    return result


def _offline_stage_state(output: Path) -> Optional[Dict[str, Any]]:
    """Recover the last durable circuit-offline stage after timeout or crash."""
    journal = output / "offline-stage-events.jsonl"
    if not journal.is_file():
        return None
    event_count = 0
    parse_errors = 0
    active_substage = None
    last_completed_substage = None
    last_event = None
    with journal.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except ValueError:
                parse_errors += 1
                continue
            if not isinstance(event, Mapping):
                parse_errors += 1
                continue
            event_count += 1
            last_event = dict(event)
            stage = event.get("stage")
            if event.get("event") == "start" and isinstance(stage, str):
                active_substage = stage
            elif event.get("event") == "complete" and isinstance(stage, str):
                last_completed_substage = stage
                if active_substage == stage:
                    active_substage = None
    return {
        "schema": OFFLINE_STAGE_SCHEMA,
        "journal": journal.name,
        "event_count": event_count,
        "parse_errors": parse_errors,
        "active_substage": active_substage,
        "last_completed_substage": last_completed_substage,
        "last_event": last_event,
    }


def _persist_npcs_answer_records(
    pp: Path,
    memory_sample_interval_s: float = DEFAULT_MEMORY_SAMPLE_INTERVAL_S,
) -> Dict[str, Any]:
    source = pp / "npcs-provenance.jsonl"
    target = pp / "answer-records.jsonl"
    memory_sampler = StageRssSampler(interval_s=memory_sample_interval_s).start()
    memory_sampler.set_stage("npcs_answer_record_persist")
    started = time.perf_counter()
    records_by_key: Dict[str, Dict[str, Any]] = {}
    source_rows = 0
    with source.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            source_rows += 1
            item = json.loads(line)
            key = item.get("answer_key")
            if not isinstance(key, str):
                raise RunnerError("NPCS answer record %d has no answer_key" % line_number)
            binding = json.loads(key)
            canonical = _binding_text(binding)
            records_by_key.setdefault(
                canonical, {"binding": binding, "multiplicity": 1}
            )
    _atomic_json_lines(target, (records_by_key[key] for key in sorted(records_by_key)))
    return {
        "answer_record_persist_ms": _round_ms(_milliseconds(started)),
        "answer_record_bytes": target.stat().st_size,
        "answer_count": len(records_by_key),
        "source_answer_record_count": source_rows,
        "duplicate_answer_records": source_rows - len(records_by_key),
        "stage_peak_memory": memory_sampler.finish(),
    }


def _run_offline(config: Mapping[str, Any], source_run_dir: Path, run_id: str,
                 artifact_run_dir: Optional[Path] = None) -> Dict[str, Any]:
    if artifact_run_dir is None:
        artifact_run_dir = source_run_dir
    method = str(config["method"])
    timeout_s = float(config["offline_timeout_s"])
    stdout_path = artifact_run_dir / "offline.stdout"
    stderr_path = artifact_run_dir / "offline.stderr"
    if method in ("B", "R"):
        output = artifact_run_dir / "offline"
        streamed = config.get("response_mode", DEFAULT_RESPONSE_MODE) == "stream-tsv"
        command = [
            sys.executable,
            str(Path(__file__).resolve()),
            "_offline",
            "response-stream" if streamed else "response",
            "--input",
            str(source_run_dir / "response" if streamed
                else source_run_dir / "raw-response.json"),
            "--out",
            str(output),
            "--memory-sample-interval",
            str(config.get("memory_sample_interval_s", DEFAULT_MEMORY_SAMPLE_INTERVAL_S)),
        ]
    elif method == "N":
        output = artifact_run_dir / "pp"
        streamed = config.get("response_mode", DEFAULT_RESPONSE_MODE) == "stream-tsv"
        command = [
            sys.executable,
            str(REFERENCE / "npcs_postprocess.py"),
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
            "--postprocess-mode",
            str(config.get("npcs_postprocess_mode", "shared")),
            "--backend",
            str(config["pqe_backend"]),
            "--memory-sample-interval",
            str(config.get("memory_sample_interval_s", DEFAULT_MEMORY_SAMPLE_INTERVAL_S)),
        ]
        if streamed:
            command.extend((
                "--provenance-jsonl",
                str(source_run_dir / "response" / "npcs-provenance.jsonl"),
                "--response-evidence",
                str(source_run_dir / "response" / "evidence.json"),
            ))
        else:
            command.insert(2, str(source_run_dir / "raw-response.json"))
        if config.get("probabilities"):
            command.extend(("--probabilities", str(config["probabilities"])))
        elif config.get("uniform_probability") is not None:
            command.extend(("--uniform-probability", str(config["uniform_probability"])))
        elif config.get("probability_seed") is not None:
            command.extend(("--probability-seed", str(config["probability_seed"])))
    else:
        output = artifact_run_dir / "offline"
        command = [
            sys.executable,
            str(Path(__file__).resolve()),
            "_offline",
            "circuit",
            "--input",
            str(source_run_dir / "circuit.nt"),
            "--out",
            str(output),
            "--backend",
            str(config["pqe_backend"]),
            "--memory-sample-interval",
            str(config.get("memory_sample_interval_s", DEFAULT_MEMORY_SAMPLE_INTERVAL_S)),
        ]
        if config.get("probabilities"):
            command.extend(("--probabilities", str(config["probabilities"])))
        elif config.get("uniform_probability") is not None:
            command.extend(("--uniform-probability", str(config["uniform_probability"])))
        elif config.get("probability_seed") is not None:
            command.extend(("--probability-seed", str(config["probability_seed"])))
    child = _run_child(
        command,
        stdout_path,
        stderr_path,
        timeout_s,
        float(config.get("memory_sample_interval_s", DEFAULT_MEMORY_SAMPLE_INTERVAL_S)),
    )
    result: Dict[str, Any] = {
        "schema": OFFLINE_SCHEMA,
        "status": "ok",
        "offline_timeout_s": timeout_s,
        "process": child,
    }
    stage_state = _offline_stage_state(output)
    if stage_state is not None:
        result["stage_state"] = stage_state
    active_substage = (
        stage_state.get("active_substage")
        if isinstance(stage_state, Mapping)
        else None
    )
    if child["timed_out"]:
        result["status"] = "offline-timeout"
        result["substage"] = active_substage
        result["detail"] = "offline processing exceeded %.6gs" % timeout_s
        if active_substage:
            result["detail"] += " during %s" % active_substage
        return result
    if child.get("signal_number") is not None:
        result["status"] = "offline-crash"
        result["signal"] = child["signal_number"]
        result["signal_name"] = child["signal_name"]
        result["substage"] = active_substage
        result["detail"] = "offline process terminated by %s (signal %d)" % (
            child["signal_name"], child["signal_number"]
        )
        if active_substage:
            result["detail"] += " during %s" % active_substage
        return result
    if child["returncode"] != 0:
        result["status"] = "offline-error"
        detail = stderr_path.read_text(encoding="utf-8", errors="replace")[-4000:].strip()
        if not detail:
            detail = "offline process exited with code %s" % child["returncode"]
        if active_substage:
            detail += " during %s" % active_substage
        result["detail"] = detail
        result["substage"] = active_substage
        return result
    metrics_path = output / "metrics.json"
    if not metrics_path.is_file():
        result["status"] = "offline-error"
        result["detail"] = "offline process returned success without metrics.json"
        return result
    result["metrics"] = _read_json(metrics_path)
    if method == "N":
        try:
            result["answer_records"] = _persist_npcs_answer_records(
                output,
                float(config.get(
                    "memory_sample_interval_s", DEFAULT_MEMORY_SAMPLE_INTERVAL_S
                )),
            )
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
        float(config.get("memory_sample_interval_s", DEFAULT_MEMORY_SAMPLE_INTERVAL_S)),
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
    if method == "N":
        return run_dir / "pp" / "answer-records.jsonl"
    records = run_dir / "offline" / "answer-records.jsonl"
    summary = run_dir / "offline" / "answer-summary.json"
    return summary if summary.is_file() and not records.is_file() else records


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
    if offline_metrics.get("postprocess_mode") == "per-answer":
        return float(endpoint_ms) + float(offline_metrics.get("offline_wall_ms", 0))
    if offline_metrics.get("timing_scope") in (
        "offline_from_complete_response_file",
        "offline_from_compact_streamed_provenance",
    ):
        offline_ms = offline_metrics.get("pp_hc_build_wall_ms")
        compiler_metrics = offline_metrics.get("compiler", {})
        pqe_ms = compiler_metrics.get("pqe_wall_ms") if isinstance(compiler_metrics, Mapping) else None
        return float(endpoint_ms) + float(offline_ms or 0) + float(pqe_ms or 0)
    return float(endpoint_ms) + float(offline_metrics.get("offline_wall_ms", 0))


def _normalized_failure(run: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    """Describe one failed execution without collapsing the original status or message."""
    status = str(run.get("status") or "unknown")
    if status == "ok":
        return None
    endpoint = run.get("endpoint")
    offline = run.get("offline")
    stage = "cell"
    raw_status = status
    detail = None
    substage = None
    signal_number = None
    signal_name = None
    if isinstance(endpoint, Mapping) and endpoint.get("status") != "ok":
        stage = "endpoint"
        raw_status = str(endpoint.get("status") or status)
        detail = endpoint.get("detail")
    elif isinstance(offline, Mapping) and offline.get("status") != "ok":
        stage = "offline"
        raw_status = str(offline.get("status") or status)
        detail = offline.get("detail")
        substage = offline.get("substage")
        signal_number = offline.get("signal")
        signal_name = offline.get("signal_name")
    elif status in ("answer-mismatch", "circuit-mismatch"):
        stage = "parity"

    if raw_status in ("timeout", "offline-timeout"):
        cause = "TO"
    elif raw_status == "offline-crash":
        cause = (
            "PQE_NATIVE_CRASH"
            if substage in ("pqe_compile", "pqe_wmc")
            else "CLIENT_CRASH"
        )
    elif raw_status == "runner-error":
        cause = "RUNNER_ERROR"
    elif raw_status in ("network-error", "transport-error"):
        cause = "TRANSPORT_ERROR"
    elif raw_status == "c-cleanup-error":
        cause = "STORE_CLEANUP_ERROR"
    elif raw_status in ("answer-mismatch", "circuit-mismatch"):
        cause = "PARITY_ERROR"
    else:
        cause = "ENGINE_ERROR" if stage == "endpoint" else "OFFLINE_ERROR"
    result = {
        "cause": cause,
        "stage": stage,
        "status": raw_status,
        "run_id": run.get("run_id"),
        "phase": run.get("phase"),
        "detail": str(detail)[:4000] if detail is not None else None,
    }
    if substage is not None:
        result["substage"] = str(substage)
    if signal_number is not None:
        result["signal"] = int(signal_number)
    if signal_name is not None:
        result["signal_name"] = str(signal_name)
    return result


def _validate_config(config: Mapping[str, Any]) -> None:
    method = str(config["method"])
    if method not in METHODS:
        raise RunnerError("unknown method: %s" % method)
    if config.get("scheme") not in ("Standard", "SPARQL_Star", "SPARQL_Star_Row"):
        raise RunnerError("scheme must be Standard, SPARQL_Star, or SPARQL_Star_Row")
    query = Path(str(config["query"]))
    if not query.is_file() or query.stat().st_size == 0:
        raise RunnerError("query is missing or empty: %s" % query)
    expected_r_query = config.get("expected_r_query")
    if expected_r_query is not None:
        expected_path = Path(str(expected_r_query))
        if method != "R":
            raise RunnerError("--expected-r-query is valid only for method R")
        if not expected_path.is_file() or expected_path.stat().st_size == 0:
            raise RunnerError("expected R query is missing or empty: %s" % expected_path)
    if method == "R" and config.get("scheme") == "SPARQL_Star_Row" \
            and expected_r_query is None:
        raise RunnerError("SPARQL_Star_Row method R requires --expected-r-query")
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
    if config.get("c_endpoint_protocol", "sparql") not in ("sparql", "rdf4j"):
        raise RunnerError("C endpoint protocol must be sparql or rdf4j")
    if method in ("C-factorised", "C-path") \
            and config.get("c_endpoint_protocol", "sparql") == "sparql" \
            and not config.get("update_endpoint"):
        raise RunnerError(
            "%s requires --update-endpoint with the generic SPARQL protocol" % method
        )
    if method == "C-path" and config.get("c_read_only"):
        raise RunnerError("C-path cannot use a read-only endpoint")
    backend = str(config["pqe_backend"])
    probability_path = config.get("probabilities")
    uniform = config.get("uniform_probability")
    probability_seed = config.get("probability_seed")
    if backend == "none" and any(
        source is not None for source in (probability_path, uniform, probability_seed)
    ):
        raise RunnerError("probabilities require --pqe-backend oracle or cudd")
    if backend != "none" and sum(
        source is not None for source in (probability_path, uniform, probability_seed)
    ) != 1:
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
    if config.get("expected_r_query") is not None:
        expected_source = Path(str(config["expected_r_query"])).resolve()
        expected_target = output / "expected-r-query.rq"
        _atomic_bytes(expected_target, expected_source.read_bytes())
        config["expected_r_query"] = str(expected_target.resolve())
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
            execution_config = config
            execution_config_path = config_path
            complete_timeout = config.get("complete_method_timeout_s")
            if complete_timeout is not None:
                execution_config = dict(config)
                execution_config["endpoint_timeout_s"] = min(
                    float(config["endpoint_timeout_s"]), float(complete_timeout)
                )
                execution_config_path = run_dir / "execution-config.json"
                _atomic_json(execution_config_path, execution_config)
            endpoint = _run_endpoint_worker(
                execution_config, execution_config_path, run_dir
            )
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
                offline_config = config
                if complete_timeout is not None:
                    remaining_s = max(
                        0.0,
                        float(complete_timeout)
                        - (time.perf_counter() - run_started),
                    )
                    if remaining_s <= 0:
                        offline = {
                            "schema": OFFLINE_SCHEMA,
                            "status": "offline-timeout",
                            "offline_timeout_s": 0.0,
                            "detail": (
                                "the endpoint exhausted the complete-method deadline"
                            ),
                        }
                    else:
                        offline_config = dict(config)
                        offline_config["offline_timeout_s"] = min(
                            float(config["offline_timeout_s"]), remaining_s
                        )
                        offline = _run_offline(
                            offline_config, run_dir, run_id
                        )
                else:
                    offline = _run_offline(config, run_dir, run_id)
                record["offline"] = offline
                record["status"] = offline.get("status")
                if offline.get("status") == "ok":
                    offline_metrics = offline.get("metrics", {})
                    evidence_mode = (
                        str(offline_metrics.get("answer_evidence_mode"))
                        if isinstance(offline_metrics, Mapping)
                        and offline_metrics.get("answer_evidence_mode") is not None
                        else "exact-multiset"
                    )
                    record["answer_evidence_mode"] = evidence_mode
                    answer_path = _answer_records_path(str(config["method"]), run_dir)
                    if phase == "measured":
                        if first_measured_answers is None:
                            first_measured_answers = answer_path
                            same = True
                        else:
                            same = _same_file_content(first_measured_answers, answer_path)
                            if not same:
                                record["status"] = "answer-mismatch"
                        if evidence_mode == "cardinality-only":
                            record["answer_cardinality_equal_first_measured"] = same
                            record["answer_content_verified"] = False
                        else:
                            record["answer_records_equal_first_measured"] = same
                            record["answer_content_verified"] = True
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
            # A failed endpoint execution can leave a writable C workspace in an
            # uncertain state, and a correctness mismatch invalidates the cell.
            # Offline work reads immutable response/circuit files, so its failure
            # must not discard the remaining independent endpoint measurements.
            if endpoint.get("status") != "ok" or record["status"] in (
                "answer-mismatch", "circuit-mismatch"
            ):
                stop = True
            elif (
                phase == "warmup"
                and record["status"] != "ok"
                and bool(config.get("stop_after_warmup_offline_failure", False))
            ):
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
    failures = [failure for failure in (_normalized_failure(item) for item in runs) if failure]
    recovery_required = any(
        isinstance(item.get("endpoint"), Mapping)
        and item["endpoint"].get("recovery_required") is True
        for item in runs
    )
    result = {
        "schema": SCHEMA,
        "status": cell_status,
        "failure": failures[0] if failures else None,
        "failures": failures,
        "recovery_required": recovery_required,
        "query_id": config["query_id"],
        "workload": config.get("workload", "watdiv"),
        "engine": config["engine"],
        "method": config["method"],
        "scheme": config["scheme"],
        "protocol": {
            "warmups": config["warmups"],
            "measured_runs": config["runs"],
            "primary_statistic": config.get("primary_statistic", "median"),
            "endpoint_timeout_s_per_execution": config["endpoint_timeout_s"],
            "offline_timeout_s_per_execution": config["offline_timeout_s"],
            "complete_method_timeout_s_per_execution": config.get(
                "complete_method_timeout_s"
            ),
            "response_mode": config.get("response_mode", DEFAULT_RESPONSE_MODE),
            "exact_response_row_limit": config.get(
                "exact_response_row_limit", DEFAULT_EXACT_RESPONSE_ROW_LIMIT
            ),
            "failure_policy": (
                "stop after an endpoint failure or correctness mismatch; "
                + (
                    "stop after a warmup offline failure"
                    if config.get("stop_after_warmup_offline_failure", False)
                    else "continue endpoint executions after an offline failure"
                )
            ),
            "stop_after_warmup_offline_failure": bool(
                config.get("stop_after_warmup_offline_failure", False)
            ),
            "artifact_policy": "new immutable directory per execution",
        },
        "runs": runs,
        "summary": {
            "measured_successes": len(measured),
            "endpoint_e2e_ms": _summary(endpoint_values),
            "component_method_e2e_ms": _summary(method_values),
            "primary_statistic": "%s of the configured measured executions"
            % str(config.get("primary_statistic", "median")),
            "warmups_excluded": True,
        },
        "artifact_bytes_before_cell_record": _directory_bytes(output),
    }
    _assert_no_digest_fields(result)
    _atomic_json(output / "cell.json", result)
    return result


def _expected_run_ids(config: Mapping[str, Any]) -> List[str]:
    return [
        "%s-%02d" % (phase, index)
        for phase, count in (
            ("warmup", int(config["warmups"])),
            ("measured", int(config["runs"])),
        )
        for index in range(1, count + 1)
    ]


def _offline_input_path(config: Mapping[str, Any], source_run_dir: Path) -> Path:
    method = str(config["method"])
    if method.startswith("C-"):
        return source_run_dir / "circuit.nt"
    if config.get("response_mode", DEFAULT_RESPONSE_MODE) == "stream-tsv":
        return source_run_dir / "response"
    return source_run_dir / "raw-response.json"


def _usable_offline_result(method: str, result: Any,
                           artifact_run_dir: Path) -> bool:
    output = artifact_run_dir / ("pp" if method == "N" else "offline")
    return (
        isinstance(result, Mapping)
        and result.get("schema") == OFFLINE_SCHEMA
        and result.get("status") == "ok"
        and (output / "metrics.json").is_file()
        and _answer_records_path(method, artifact_run_dir).is_file()
    )


def _effective_offline_result(cell: Path, method: str, run_id: str,
                              source_record: Mapping[str, Any]
                              ) -> Optional[Tuple[Dict[str, Any], Path, str]]:
    source_run_dir = cell / run_id
    original = source_record.get("offline")
    if _usable_offline_result(method, original, source_run_dir):
        return dict(original), source_run_dir, "original"
    for attempt in sorted(cell.glob("offline-resume-[0-9][0-9][0-9]")):
        artifact_run_dir = attempt / run_id
        result_path = artifact_run_dir / "offline-result.json"
        if not result_path.is_file():
            continue
        try:
            candidate = _read_json(result_path)
        except (OSError, ValueError):
            continue
        if _usable_offline_result(method, candidate, artifact_run_dir):
            return dict(candidate), artifact_run_dir, attempt.name
    return None


def _relative_artifact(cell: Path, path: Path) -> str:
    try:
        return str(path.relative_to(cell))
    except ValueError:
        return str(path)


def _evaluate_resumed_cell(cell: Path, config: Mapping[str, Any],
                           source_cell: Mapping[str, Any]) -> Dict[str, Any]:
    method = str(config["method"])
    source_runs = {
        str(item.get("run_id")): item
        for item in source_cell.get("runs", [])
        if isinstance(item, Mapping) and item.get("run_id") is not None
    }
    effective_runs: List[Dict[str, Any]] = []
    endpoints_complete = True
    offline_complete = True
    for run_id in _expected_run_ids(config):
        source_record = source_runs.get(run_id)
        endpoint = source_record.get("endpoint") if source_record else None
        endpoint_ok = isinstance(endpoint, Mapping) and endpoint.get("status") == "ok"
        endpoints_complete = endpoints_complete and endpoint_ok
        effective = (
            _effective_offline_result(cell, method, run_id, source_record)
            if endpoint_ok and source_record is not None
            else None
        )
        offline_complete = offline_complete and effective is not None
        item: Dict[str, Any] = {
            "run_id": run_id,
            "phase": "warmup" if run_id.startswith("warmup-") else "measured",
            "endpoint_status": endpoint.get("status") if isinstance(endpoint, Mapping) else "missing",
            "offline_status": "missing",
            "offline_source": None,
            "offline_artifact_run": None,
            "component_method_e2e_ms": None,
        }
        if effective is not None:
            offline, artifact_run_dir, origin = effective
            item.update({
                "offline_status": offline.get("status"),
                "offline_source": origin,
                "offline_artifact_run": _relative_artifact(cell, artifact_run_dir),
                "component_method_e2e_ms": _component_method_e2e(endpoint, offline),
            })
        effective_runs.append(item)

    answer_parity: Optional[bool] = None
    circuit_parity: Optional[bool] = None
    if endpoints_complete and offline_complete:
        measured = [item for item in effective_runs if item["phase"] == "measured"]
        answer_paths = [
            _answer_records_path(
                method, cell / str(item["offline_artifact_run"])
            )
            for item in measured
        ]
        answer_parity = all(
            _same_file_content(answer_paths[0], path) for path in answer_paths[1:]
        )
        if method.startswith("C-"):
            circuit_paths = [cell / str(item["run_id"]) / "circuit.nt" for item in measured]
            circuit_parity = all(
                path.is_file() and _same_file_content(circuit_paths[0], path)
                for path in circuit_paths[1:]
            ) and circuit_paths[0].is_file()

    if not endpoints_complete or not offline_complete:
        status = "incomplete"
    elif answer_parity is False:
        status = "answer-mismatch"
    elif circuit_parity is False:
        status = "circuit-mismatch"
    else:
        status = "ok"
    measured_complete = [
        item for item in effective_runs
        if item["phase"] == "measured" and item["offline_status"] == "ok"
    ]
    endpoint_values = []
    method_values = []
    for item in measured_complete:
        source_record = source_runs[item["run_id"]]
        endpoint_values.append(
            float(source_record["endpoint"]["endpoint"]["endpoint_e2e_ms"])
        )
        if item["component_method_e2e_ms"] is not None:
            method_values.append(float(item["component_method_e2e_ms"]))
    return {
        "status": status,
        "endpoint_runs_complete": endpoints_complete,
        "offline_runs_complete": offline_complete,
        "answer_records_equal_across_measured_runs": answer_parity,
        "circuits_equal_across_measured_runs": circuit_parity,
        "runs": effective_runs,
        "summary": {
            "measured_successes": len(measured_complete),
            "endpoint_e2e_ms": _summary(endpoint_values),
            "component_method_e2e_ms": _summary(method_values),
            "primary_statistic": "median of the configured measured executions",
            "warmups_excluded": True,
        },
    }


def _new_offline_resume_directory(cell: Path) -> Path:
    for index in range(1, 1000):
        candidate = cell / ("offline-resume-%03d" % index)
        try:
            candidate.mkdir()
            return candidate
        except FileExistsError:
            continue
    raise RunnerError("cell has exhausted offline-resume-001 through -999")


def _resume_offline_cell(cell: Path) -> Dict[str, Any]:
    cell = cell.resolve()
    config_path = cell / "cell-config.json"
    source_cell_path = cell / "cell.json"
    if not config_path.is_file() or not source_cell_path.is_file():
        raise RunnerError("resume requires cell-config.json and cell.json in %s" % cell)
    config = _read_json(config_path)
    source_cell = _read_json(source_cell_path)
    if config.get("schema") != SCHEMA or source_cell.get("schema") != SCHEMA:
        raise RunnerError("unsupported cell schema in %s" % cell)
    method = str(config.get("method"))
    if method not in METHODS:
        raise RunnerError("unknown method in saved cell: %s" % method)
    source_runs = {
        str(item.get("run_id")): item
        for item in source_cell.get("runs", [])
        if isinstance(item, Mapping) and item.get("run_id") is not None
    }
    pending: List[Tuple[str, Mapping[str, Any]]] = []
    for run_id in _expected_run_ids(config):
        source_record = source_runs.get(run_id)
        endpoint = source_record.get("endpoint") if source_record else None
        if not isinstance(endpoint, Mapping) or endpoint.get("status") != "ok":
            continue
        if _effective_offline_result(cell, method, run_id, source_record) is None:
            pending.append((run_id, source_record))

    attempt: Optional[Path] = None
    attempted_run_ids: List[str] = []
    if pending:
        attempt = _new_offline_resume_directory(cell)
        for run_id, _source_record in pending:
            attempted_run_ids.append(run_id)
            source_run_dir = cell / run_id
            artifact_run_dir = attempt / run_id
            artifact_run_dir.mkdir()
            input_path = _offline_input_path(config, source_run_dir)
            if not input_path.exists():
                offline: Dict[str, Any] = {
                    "schema": OFFLINE_SCHEMA,
                    "status": "offline-error",
                    "offline_timeout_s": config["offline_timeout_s"],
                    "detail": "saved endpoint artifact is missing: %s" % input_path,
                }
            else:
                try:
                    offline = _run_offline(
                        config, source_run_dir, run_id, artifact_run_dir
                    )
                except (OSError, RunnerError, ValueError) as exc:
                    offline = {
                        "schema": OFFLINE_SCHEMA,
                        "status": "offline-error",
                        "offline_timeout_s": config["offline_timeout_s"],
                        "detail": "%s: %s" % (type(exc).__name__, exc),
                    }
            _atomic_json(artifact_run_dir / "offline-result.json", offline)

    evaluated = _evaluate_resumed_cell(cell, config, source_cell)
    has_complete_manifest = False
    for prior in sorted(cell.glob("offline-resume-[0-9][0-9][0-9]")):
        manifest = prior / "resume.json"
        if not manifest.is_file():
            continue
        try:
            prior_result = _read_json(manifest)
        except (OSError, ValueError):
            continue
        if (
            prior_result.get("schema") == OFFLINE_RESUME_SCHEMA
            and prior_result.get("status") == "ok"
        ):
            has_complete_manifest = True
            break
    # A process may have finished the final per-run offline artifact and died
    # before writing resume.json.  Consolidate that already-complete state into
    # a new immutable manifest so a deployment wrapper can recognize it later.
    if (
        attempt is None
        and evaluated["status"] == "ok"
        and source_cell.get("status") != "ok"
        and not has_complete_manifest
    ):
        attempt = _new_offline_resume_directory(cell)
    result: Dict[str, Any] = {
        "schema": OFFLINE_RESUME_SCHEMA,
        "status": evaluated["status"],
        "source_cell": str(cell),
        "attempt": attempt.name if attempt is not None else None,
        "attempted_run_ids": attempted_run_ids,
        "scope": (
            "offline stages only; saved endpoint responses and circuits are reused, "
            "and no endpoint query or CONSTRUCT is resumed"
        ),
        **evaluated,
    }
    if attempt is not None:
        result["manifest"] = str(attempt / "resume.json")
    _assert_no_digest_fields(result)
    if attempt is not None:
        _atomic_json(attempt / "resume.json", result)
    return result


def _run_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run one WatDiv query x engine x B/R/N/C method cell."
    )
    parser.add_argument("--query", required=True, type=Path)
    parser.add_argument("--query-id", required=True)
    parser.add_argument(
        "--workload",
        choices=("watdiv", "tpch"),
        default="watdiv",
        help="workload identity recorded in the immutable cell",
    )
    parser.add_argument("--engine", required=True)
    parser.add_argument(
        "--engine-pid",
        type=_positive_integer,
        help="local endpoint process PID for stage-level process-tree RSS sampling",
    )
    parser.add_argument("--method", required=True, choices=METHODS)
    parser.add_argument(
        "--scheme",
        choices=("Standard", "SPARQL_Star", "SPARQL_Star_Row"),
        default="SPARQL_Star",
        help="reification scheme used by R, N, and C (default: SPARQL_Star)",
    )
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--base-endpoint")
    parser.add_argument("--reified-endpoint")
    parser.add_argument("--update-endpoint")
    parser.add_argument(
        "--c-endpoint-protocol",
        choices=("sparql", "rdf4j"),
        default="sparql",
        help=(
            "remote repository protocol used by CircuitRun; select rdf4j for "
            "GraphDB/RDF4J Server"
        ),
    )
    parser.add_argument("--jar", type=Path)
    parser.add_argument("--java", default="java")
    parser.add_argument(
        "--java-max-heap",
        type=_java_heap_size,
        help="explicit CircuitRun maximum heap (for example 192g)",
    )
    parser.add_argument("--reified-data", type=Path)
    parser.add_argument(
        "--expected-r-query",
        type=Path,
        help=(
            "frozen row-inline R query; required for SPARQL_Star_Row because "
            "ordinary per-triple reification would give the wrong event granularity"
        ),
    )
    parser.add_argument("--warmups", type=_nonnegative_integer, default=1)
    parser.add_argument("--runs", type=_positive_integer, default=5)
    parser.add_argument(
        "--primary-statistic",
        choices=("mean", "median"),
        default="median",
        help="statistic selected by the enclosing experiment for measured executions",
    )
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
    parser.add_argument(
        "--complete-method-timeout",
        type=_positive_seconds,
        help=(
            "optional shared deadline for endpoint plus offline/PQE in each "
            "warmup or measured execution"
        ),
    )
    parser.add_argument(
        "--stop-after-warmup-offline-failure",
        action="store_true",
        help=(
            "do not issue measured endpoint requests when warmup offline processing "
            "already failed; disabled by default for compatibility"
        ),
    )
    parser.add_argument(
        "--response-mode",
        choices=("full-json", "stream-tsv"),
        default=DEFAULT_RESPONSE_MODE,
        help=(
            "persist a complete JSON response, or drain TSV incrementally and retain only "
            "bounded answer evidence/required NPCS provenance"
        ),
    )
    parser.add_argument(
        "--exact-response-row-limit",
        type=_positive_integer,
        default=DEFAULT_EXACT_RESPONSE_ROW_LIMIT,
        help="maximum B/R solution rows retained as an exact answer multiset in stream-tsv mode",
    )
    parser.add_argument("--pqe-backend", choices=("none", "oracle", "cudd"), default="none")
    parser.add_argument(
        "--npcs-postprocess-mode",
        choices=("shared", "per-answer"),
        default="shared",
        help="query-global or sequential local per-answer NPCS hash-consing",
    )
    probability = parser.add_mutually_exclusive_group()
    probability.add_argument("--probabilities", type=Path)
    probability.add_argument("--uniform-probability", type=float)
    probability.add_argument("--probability-seed", type=int)
    parser.add_argument("--token-regex", default=DEFAULT_TOKEN_REGEX)
    parser.add_argument("--c-parallelism", type=_positive_integer, default=1)
    parser.add_argument(
        "--memory-sample-interval",
        type=_positive_seconds,
        default=DEFAULT_MEMORY_SAMPLE_INTERVAL_S,
        help="Linux process-tree RSS sampling interval in seconds",
    )
    parser.add_argument("--c-read-only", action="store_true")
    parser.add_argument(
        "--skip-bnode-check",
        action="store_true",
        help="skip CircuitRun's store probe only after the loaded data is independently known to be ground",
    )
    return parser


def _resume_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Resume failed or missing offline/PQE stages from immutable cell artifacts."
    )
    parser.add_argument("resume_offline", choices=("resume-offline",))
    parser.add_argument("--cell", required=True, type=Path)
    return parser


def _configuration(args: argparse.Namespace) -> Dict[str, Any]:
    if args.npcs_postprocess_mode == "per-answer":
        if args.method != "N":
            raise ValueError("per-answer NPCS post-processing is only valid for method N")
    return {
        "schema": SCHEMA,
        "query": str(args.query.resolve()),
        "query_id": args.query_id,
        "workload": args.workload,
        "engine": args.engine,
        "engine_pid": args.engine_pid,
        "method": args.method,
        "scheme": args.scheme,
        "base_endpoint": args.base_endpoint,
        "reified_endpoint": args.reified_endpoint,
        "update_endpoint": args.update_endpoint,
        "c_endpoint_protocol": args.c_endpoint_protocol,
        "jar": str(args.jar.resolve()) if args.jar is not None else None,
        "java": args.java,
        "java_max_heap": args.java_max_heap,
        "reified_data": str(args.reified_data.resolve()) if args.reified_data is not None else None,
        "expected_r_query": (
            str(args.expected_r_query.resolve())
            if args.expected_r_query is not None else None
        ),
        "warmups": args.warmups,
        "runs": args.runs,
        "primary_statistic": args.primary_statistic,
        "endpoint_timeout_s": args.endpoint_timeout,
        "offline_timeout_s": args.offline_timeout,
        "complete_method_timeout_s": args.complete_method_timeout,
        "stop_after_warmup_offline_failure": bool(
            args.stop_after_warmup_offline_failure
        ),
        "response_mode": args.response_mode,
        "exact_response_row_limit": args.exact_response_row_limit,
        "pqe_backend": args.pqe_backend,
        "npcs_postprocess_mode": args.npcs_postprocess_mode,
        "probabilities": (
            str(args.probabilities.resolve()) if args.probabilities is not None else None
        ),
        "uniform_probability": args.uniform_probability,
        "probability_seed": args.probability_seed,
        "token_regex": args.token_regex,
        "c_parallelism": args.c_parallelism,
        "memory_sample_interval_s": args.memory_sample_interval,
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
    offline.add_argument(
        "offline_kind", choices=("response", "response-stream", "circuit")
    )
    offline.add_argument("--input", required=True, type=Path)
    offline.add_argument("--out", required=True, type=Path)
    offline.add_argument("--backend", choices=("none", "oracle", "cudd"), default="none")
    offline.add_argument(
        "--memory-sample-interval",
        type=_positive_seconds,
        default=DEFAULT_MEMORY_SAMPLE_INTERVAL_S,
    )
    probability = offline.add_mutually_exclusive_group()
    probability.add_argument("--probabilities", type=Path)
    probability.add_argument("--uniform-probability", type=float)
    probability.add_argument("--probability-seed", type=int)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0].startswith("_"):
        args = _internal_parser().parse_args(arguments)
        if args.internal_command == "_endpoint":
            return _endpoint_worker_main(args.config, args.run_dir)
        return _offline_worker_main(args)
    if arguments and arguments[0] == "resume-offline":
        parser = _resume_parser()
        args = parser.parse_args(arguments)
        try:
            result = _resume_offline_cell(args.cell)
        except (OSError, RunnerError, ValueError) as exc:
            parser.exit(1, "watdiv10m_runner: error: %s\n" % exc)
        print(json.dumps({
            "status": result["status"],
            "cell": result["source_cell"],
            "attempt": result["attempt"],
            "manifest": result.get("manifest"),
        }, ensure_ascii=False, sort_keys=True))
        return 0 if result["status"] == "ok" else 1
    parser = _run_parser()
    args = parser.parse_args(arguments)
    if (
        args.pqe_backend != "none"
        and args.probabilities is None
        and args.uniform_probability is None
        and args.probability_seed is None
    ):
        args.probability_seed = event_probabilities.DEFAULT_PROBABILITY_SEED
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
