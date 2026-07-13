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
* a C sample is the sum of all CONSTRUCT POST intervals in that execution;
* ``c_parse_median_ms`` includes byte assembly, UTF-8 decode, ``splitlines``,
  triple deduplication, circuit parsing, and binding recovery.  Its interval is
  disjoint from network time; paired ``construct_total_ms`` samples add each
  exactly once.

The timing response format remains CSV for B/R/N and N-Triples for C.  B/R rows
also carry a normalized CSV-multiset fingerprint.  The formal term-aware
correctness gate is ``verify_brnc_parity.py``: it uses SPARQL Results JSON for
B/R/N and structured ``c:binding`` values for C.
"""

import argparse
import collections
import csv
import hashlib
import io
import json
import multiprocessing
import os
import re
import signal
import socket
import statistics
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request as U

sys.setrecursionlimit(1_000_000)

HERE = os.path.dirname(os.path.abspath(__file__))
REF = os.path.dirname(HERE)
sys.path.insert(0, REF)
import circuit_io
from experiment_timeouts import QUERY_TIMEOUT_S

JAR = os.path.join(REF, "..", "engine", "target", "npcs-rewrite.jar")
TIMEOUT = float(QUERY_TIMEOUT_S)
WARMUPS = int(os.environ.get("PCM_WARMUPS", "1"))
RUNS = int(os.environ.get("PCM_RUNS", "5"))
RS = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
COMMIT = subprocess.run(
    ["git", "-C", REF, "rev-parse", "--short", "HEAD"],
    capture_output=True,
    text=True,
).stdout.strip() or "?"

PROTOCOL = "r9.2-cell-deadline-v3"
NOTE_PREFIX = "pcm-meta-v2:"
PROVENANCE_VAR = "finalprovennacevariable"  # legacy name; capture-safe builds may rename it
GENERATED_PROVENANCE_RE = re.compile(
    r"^__npcs\d+_finalprovennacevariable$"
)

# Every engine uses two independent stores per scale: one base and one reified.
# Defaults follow reference/engines/engines.json's localhost profile and assign
# adjacent ports to the paired/100M instances.  Real deployments can override
# any cell with PCM_<ENGINE>_<SCALE>_{BASE,REIFIED}_ENDPOINT.
GDB = "http://localhost:7200/repositories"
_DEFAULT_ENDPOINTS = {
    "graphdb": {
        "10M": {"base": f"{GDB}/watdivbase", "reified": f"{GDB}/watdiv"},
        "100M": {"base": f"{GDB}/watdiv100mbase", "reified": f"{GDB}/watdiv100m"},
    },
    "oxigraph": {
        "10M": {"base": "http://localhost:7879/query", "reified": "http://localhost:7878/query"},
        "100M": {"base": "http://localhost:7881/query", "reified": "http://localhost:7880/query"},
    },
    "qlever": {
        "10M": {"base": "http://localhost:7002", "reified": "http://localhost:7001"},
        "100M": {"base": "http://localhost:7004", "reified": "http://localhost:7003"},
    },
    "millenniumdb": {
        "10M": {"base": "http://localhost:1235/sparql", "reified": "http://localhost:1234/sparql"},
        "100M": {"base": "http://localhost:1237/sparql", "reified": "http://localhost:1236/sparql"},
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
            "version": display[engine] + " [engines.json profile]",
            "profile": profile,
        }
        for scale, roles in scales.items():
            config[scale] = {}
            for role, default in roles.items():
                env_name = f"PCM_{engine.upper()}_{scale}_{role.upper()}_ENDPOINT"
                config[scale][role] = environ.get(env_name, default)
        registry[engine] = config
    return registry


ENGINES = build_engine_registry()


class HardDeadline(TimeoutError):
    """A wall-clock deadline, as distinct from a per-socket inactivity timeout."""


class PostFailure(RuntimeError):
    """Serializable HTTP failure raised on the controlling process."""

    def __init__(self, kind, detail):
        super().__init__(detail)
        self.kind = kind
        self.detail = detail


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


def _kill_worker(proc):
    """Best-effort kill of the worker *and* descendants, without a long wait."""
    if proc is None or not proc.is_alive():
        return
    if os.name == "posix":
        try:
            if os.getpgid(proc.pid) == proc.pid:
                os.killpg(proc.pid, signal.SIGTERM)
            else:
                proc.terminate()
        except (ProcessLookupError, PermissionError, OSError):
            proc.terminate()
    else:
        proc.terminate()
    proc.join(0.10)
    if proc.is_alive():
        if os.name == "posix":
            try:
                if os.getpgid(proc.pid) == proc.pid:
                    os.killpg(proc.pid, signal.SIGKILL)
                else:
                    proc.kill()
            except (ProcessLookupError, PermissionError, OSError):
                proc.kill()
        else:
            proc.kill()
        proc.join(0.10)


def _remaining(deadline):
    remain = deadline - time.monotonic()
    if remain <= 0:
        raise HardDeadline("cell wall-clock budget exhausted")
    return remain


# ---------------------------------------------------------------------------
# HTTP: direct implementation (inside a killable worker) and public wrapper.

def _post_timed_direct(
    endpoint, body, accept, keep=False, timeout=TIMEOUT, return_chunks=False
):
    """POST and fully drain one response.

    ``timeout`` is also supplied to urllib as an inactivity guard.  The caller
    must enforce the wall-clock limit (the cell worker is killed by its parent).
    Timing begins immediately before ``urlopen`` and ends only after EOF.
    """
    if timeout is None or timeout <= 0:
        raise HardDeadline("HTTP wall-clock budget exhausted before send")
    req = U.Request(endpoint, data=body.encode("utf-8"), method="POST")
    req.add_header("Content-Type", "application/sparql-query")
    req.add_header("Accept", accept)
    chunks = [] if keep else None
    nbytes = n_newlines = 0
    last = b""
    started = time.monotonic()
    try:
        with U.urlopen(req, timeout=max(0.001, timeout)) as response:
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
        kept = b"".join(chunks).decode("utf-8", "replace")
    return elapsed_ms, nlines, nbytes, kept


def _post_worker(conn, path, endpoint, body, accept, keep, timeout):
    _new_process_group()
    try:
        ms, lines, nbytes, response = _post_timed_direct(
            endpoint, body, accept, keep=keep, timeout=timeout
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


def post_timed(endpoint, body, accept, keep=False, timeout=TIMEOUT):
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
        args=(send, path, endpoint, body, accept, keep, timeout),
    )
    started = time.monotonic()
    proc.start()
    send.close()
    proc.join(max(0.0, timeout - (time.monotonic() - started)))
    elapsed = time.monotonic() - started
    try:
        if proc.is_alive() or elapsed > timeout:
            _kill_worker(proc)
            raise HardDeadline(f"HTTP response exceeded {timeout:g}s hard deadline")
        if not recv.poll():
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
        if proc.is_alive():
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


def q_npcs(qtext, timeout=None):
    """Clean-room NpcsRewriter provenance SELECT."""
    result = subprocess.run(
        ["java", "-jar", JAR, "Standard", "query", qtext],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise RuntimeError(
            f"NpcsRewriter failed rc={result.returncode}: {result.stderr[-300:]}"
        )
    return result.stdout


def c_construct_plan(qtext, timeout=None):
    """Extract CircuitRewriter's ordered CONSTRUCT plan from CircuitRun stderr."""
    query_file = tempfile.NamedTemporaryFile("w", suffix=".rq", delete=False)
    query_file.write(qtext)
    query_file.close()
    try:
        result = subprocess.run(
            [
                "java",
                "-cp",
                JAR,
                "npcs.circuit.CircuitRun",
                "Standard",
                os.path.join(REF, "bench_engine", "tiny.ttl"),
                query_file.name,
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    finally:
        os.unlink(query_file.name)
    if result.returncode != 0:
        raise RuntimeError(
            f"CircuitRun rewrite failed rc={result.returncode}: {result.stderr[-300:]}"
        )
    plan = []
    for chunk in re.split(r"# --- step \d+ ---", result.stderr)[1:]:
        chunk = chunk.split("# ---- ")[0].split("# circuit triples")[0].strip()
        if chunk.startswith(("PREFIX", "CONSTRUCT")):
            plan.append(chunk)
    if not plan:
        grab, current = False, []
        for line in result.stderr.splitlines():
            if line.startswith("PREFIX c:"):
                grab = True
            if line.startswith("# circuit triples"):
                grab = False
            if grab:
                current.append(line)
        if current:
            plan = ["\n".join(current)]
    if not plan:
        raise RuntimeError("empty CONSTRUCT plan")
    return plan


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
    _, answer_gates, bindings = circuit_io.parse(lines)
    keys = {circuit_io.answer_key(bindings.get(gate, {})) for gate in answer_gates}
    gates = sum(1 for value in typ.values() if value.endswith(("Times", "Plus", "Minus")))
    times = sum(1 for value in typ.values() if value.endswith("Times"))
    edges = sum(map(len, tin.values())) + sum(map(len, feeds.values()))
    result = (gates, edges, len(answer_gates), times)
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


def _empty_result(status, note, rewrite_ms=None):
    return {
        "status": status,
        "answers": None,
        "samples": [],
        "response_bytes": None,
        "c_parse": [],
        "construct_total": [],
        "gates": None,
        "edges": None,
        "derivations": None,
        "ntok": None,
        "rewrite_ms": rewrite_ms,
        "evidence": {},
        "note": note,
    }


def _failure_result(ex, rewrite_ms=None):
    if isinstance(ex, (HardDeadline, socket.timeout, subprocess.TimeoutExpired)):
        return _empty_result("timeout", str(ex)[:160] or "cell deadline exhausted", rewrite_ms)
    if isinstance(ex, PostFailure):
        if ex.kind in ("network", "worker"):
            return _empty_result(f"err:{ex.kind}", ex.detail[:160], rewrite_ms)
        lowered = ex.detail.lower()
        if "memory" in lowered or "heap" in lowered:
            status = "oom"
        elif "timeout" in lowered or "timed out" in lowered:
            status = "timeout"
        elif re.match(r"HTTP 4\d\d:", ex.detail):
            status = "unsupported"
        else:
            status = "err:http"
        return _empty_result(status, ex.detail[:160], rewrite_ms)
    return _empty_result(f"err:{type(ex).__name__}", str(ex)[:160], rewrite_ms)


def _merge_circuit_chunks(chunks, unique):
    """Client parse stage 1: assemble/decode/split/deduplicate one HTTP body."""
    text = b"".join(chunks).decode("utf-8", "replace")
    unique.update(line for line in text.splitlines() if line.strip().endswith(" ."))


def _execute_c_once(endpoint, bodies, deadline):
    """Execute every C step under the cell's still-shrinking shared budget."""
    network_ms = 0.0
    client_parse_ms = 0.0
    unique = set()
    for body in bodies:
        ms, _, _, chunks = _post_timed_direct(
            endpoint,
            body,
            "application/n-triples",
            keep=True,
            timeout=_remaining(deadline),
            return_chunks=True,
        )
        network_ms += ms
        parse_started = time.monotonic()
        _merge_circuit_chunks(chunks, unique)
        client_parse_ms += (time.monotonic() - parse_started) * 1000.0
        _remaining(deadline)
    # Wire drain ended before each merge above.  Final circuit decode and
    # binding recovery remain wholly in the disjoint client-parse interval.
    parse_started = time.monotonic()
    gates, edges, answers, derivations, keys = parse_circuit(unique, include_keys=True)
    client_parse_ms += (time.monotonic() - parse_started) * 1000.0
    _remaining(deadline)
    dedup_bytes = sum(len(line.encode("utf-8")) + 1 for line in unique)
    return (
        network_ms,
        dedup_bytes,
        client_parse_ms,
        gates,
        edges,
        answers,
        derivations,
        keys,
    )


def _time_method_impl(
    method, qtext, base_ep, reified_ep, deadline, warmups, runs, rewrite_clock=None
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
            bodies = [*c_construct_plan(qtext, timeout=_remaining(deadline))]
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

    samples, parse_samples, construct_total_samples = [], [], []
    answers = response_bytes = gates = edges = derivations = ntok = None
    evidence = {}
    try:
        for index in range(warmups + runs):
            capture = index == warmups  # first measured execution
            if method == "C":
                (
                    engine_ms,
                    nbytes,
                    parse_ms,
                    run_gates,
                    run_edges,
                    run_answers,
                    run_derivations,
                    candidate_keys,
                ) = _execute_c_once(endpoint, bodies, deadline)
                if capture:
                    evidence = set_evidence(candidate_keys)
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
                else:
                    run_answers = answers
            _remaining(deadline)
            if index >= warmups:
                samples.append(round(engine_ms, 3))
                parse_samples.append(round(parse_ms, 3))
                if method == "C":
                    construct_total_samples.append(round(engine_ms + parse_ms, 3))
            if capture or answers is None:
                answers = run_answers
            response_bytes, gates, edges, derivations = (
                nbytes,
                run_gates,
                run_edges,
                run_derivations,
            )
        _remaining(deadline)
    except BaseException as ex:
        # A cell is atomic: partial samples are intentionally not checkpointed as
        # successful.  The next invocation can retry a transient err:* result.
        return _failure_result(ex, rewrite_ms)

    return {
        "status": "ok",
        "answers": answers,
        "samples": samples,
        "response_bytes": response_bytes,
        "c_parse": parse_samples,
        "construct_total": construct_total_samples,
        "gates": gates,
        "edges": edges,
        "derivations": derivations,
        "ntok": ntok,
        "rewrite_ms": round(rewrite_ms, 6),
        "evidence": evidence,
        "note": "",
    }


def _cell_worker(
    conn, method, qtext, base_ep, reified_ep, deadline, warmups, runs, rewrite_clock
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
        )
        conn.send(("ok", result))
    except BaseException as ex:
        conn.send(("error", type(ex).__name__, str(ex)))
    finally:
        conn.close()


def time_method(
    method,
    qtext,
    base_ep,
    reified_ep,
    timeout=TIMEOUT,
    warmups=WARMUPS,
    runs=RUNS,
):
    """Run one atomic method cell under one hard wall-clock deadline."""
    if timeout <= 0 or warmups < 0 or runs <= 0:
        raise ValueError("timeout and runs must be positive; warmups must be non-negative")
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
        ),
    )
    proc.start()
    send.close()
    proc.join(max(0.0, deadline - time.monotonic()))
    elapsed = time.monotonic() - started
    try:
        if proc.is_alive() or elapsed > timeout:
            _kill_worker(proc)
            result = _empty_result(
                "timeout",
                f"whole cell exceeded {timeout:g}s hard deadline",
                round(rewrite_clock.value, 6) if rewrite_clock.value >= 0 else None,
            )
        elif not recv.poll():
            result = _empty_result(
                "err:worker", f"cell worker exited {proc.exitcode} without a result"
            )
        else:
            message = recv.recv()
            if message[0] == "ok":
                result = message[1]
            else:
                result = _empty_result(
                    "err:worker", f"{message[1]}: {message[2]}"[:160]
                )
        result["cell_wall_ms"] = round(min(elapsed, timeout) * 1000.0, 3)
        # Last line of defence against the old per-socket loophole.
        if result.get("status") == "ok" and elapsed > timeout:
            return _empty_result(
                "timeout", f"whole cell exceeded {timeout:g}s hard deadline"
            )
        return result
    finally:
        recv.close()
        if proc.is_alive():
            _kill_worker(proc)
        try:
            proc.close()
        except (ValueError, AttributeError):
            pass


# ---------------------------------------------------------------------------
# Append-only checkpoint handling.

def load_manifest():
    with open(os.path.join(HERE, "workload_manifest.csv"), newline="") as fh:
        return list(csv.DictReader(fh))


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

LEGACY_COLS = [
    "commit", "engine", "engine_version", "scale", "class", "template", "instance",
    "query_sha256", "method", "implementation", "status", "answers", "median_ms",
    "min_ms", "max_ms", "mean_ms", "sd_ms", "warmups", "runs", "timeout_s",
    "response_bytes", "c_parse_median_ms", "gates", "edges", "derivations",
    "npcs_token_occurrences", "rewrite_ms", "samples_json", "notes",
]
COLS = LEGACY_COLS[:-1] + [
    "c_parse_samples_json", "construct_total_ms", "construct_total_samples_json",
    "protocol", "answer_kind", "answer_key_count", "answer_fingerprint", "notes"
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
    for name in ("protocol", "answer_kind", "answer_key_count", "answer_fingerprint"):
        if row.get(name) not in (None, ""):
            metadata[name] = row[name]
    return metadata


def _checkpoint_rows(path):
    """Yield one-physical-line CSV records, skipping a torn final checkpoint."""
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return
    with open(path, newline="") as fh:
        header_line = fh.readline()
        try:
            header = next(csv.reader([header_line], strict=True))
        except (csv.Error, StopIteration):
            return
        for line in fh:
            try:
                values = next(csv.reader([line], strict=True))
            except (csv.Error, StopIteration):
                continue
            if len(values) != len(header):
                continue
            yield dict(zip(header, values))


def _row_key(row):
    try:
        key = tuple(row[name] for name in CELL_KEY)
    except KeyError:
        return None
    return key if all(key) else None


def checkpoint_complete(row, warmups=WARMUPS, runs=RUNS, timeout=TIMEOUT):
    status = row.get("status", "")
    if status not in TERMINAL_STATUSES:
        return False
    # Protocol-sensitive completed results are reusable only under the exact
    # repetition/deadline configuration.  Failure rows remain retryable.
    try:
        if int(row.get("warmups", warmups)) != warmups:
            return False
        if int(row.get("runs", runs)) != runs:
            return False
        if abs(float(row.get("timeout_s", timeout)) - timeout) > 1e-9:
            return False
    except (TypeError, ValueError):
        return False
    metadata = unpack_note(row)
    # The timeout semantics changed from per-socket/per-execution to one hard
    # whole-cell deadline.  Old terminal rows (including timeout) therefore
    # cannot be silently reused under the new protocol.
    if metadata.get("protocol") != PROTOCOL:
        return False
    if status != "ok":
        return True
    try:
        samples = json.loads(row.get("samples_json") or "")
        if len(samples) != runs or any(float(value) < 0 for value in samples):
            return False
        # Invalidates legacy rows that slipped past a 300s per-socket timeout.
        if any(float(value) > timeout * 1000.0 for value in samples):
            return False
        if sum(float(value) for value in samples) > timeout * 1000.0:
            return False
    except (TypeError, ValueError, json.JSONDecodeError):
        return False
    if row.get("rewrite_ms") in (None, ""):
        return False
    try:
        cell_wall_ms = float(metadata.get("cell_wall_ms"))
        if cell_wall_ms < 0 or cell_wall_ms > timeout * 1000.0:
            return False
    except (TypeError, ValueError):
        return False
    if row.get("method") == "C":
        try:
            parse_samples = metadata["c_parse_samples"]
            total_samples = metadata["construct_total_samples"]
            if len(parse_samples) != runs or len(total_samples) != runs:
                return False
            if any(
                abs(float(total) - (float(network) + float(client))) > 0.01
                for network, client, total in zip(samples, parse_samples, total_samples)
            ):
                return False
        except (KeyError, TypeError, ValueError):
            return False
    # Every successful new-protocol row must carry full-answer evidence.  This
    # also makes legacy count-only cells rerunnable without rewriting the CSV.
    return bool(metadata.get("answer_kind") and metadata.get("answer_fingerprint"))


def load_done(out, warmups=WARMUPS, runs=RUNS, timeout=TIMEOUT):
    latest = {}
    for row in _checkpoint_rows(out) or ():
        key = _row_key(row)
        if key is not None:
            latest[key] = row
    return {
        key
        for key, row in latest.items()
        if checkpoint_complete(row, warmups=warmups, runs=runs, timeout=timeout)
    }


def _open_writer(path):
    new = not os.path.exists(path) or os.path.getsize(path) == 0
    if new:
        fieldnames = COLS
    else:
        with open(path, newline="") as current:
            fieldnames = next(csv.reader([current.readline()]))
        missing = set(LEGACY_COLS) - set(fieldnames)
        if missing:
            raise ValueError(f"checkpoint is missing required columns: {sorted(missing)}")
        # Isolate a torn physical record before appending a retry.  _checkpoint_rows
        # will ignore that malformed record on the next resume.
        with open(path, "rb") as current:
            current.seek(-1, os.SEEK_END)
            needs_newline = current.read(1) not in (b"\n", b"\r")
        if needs_newline:
            with open(path, "a", newline="") as current:
                current.write("\n")
    fh = open(path, "a", newline="")
    writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
    if new:
        writer.writeheader()
        fh.flush()
        os.fsync(fh.fileno())
    return fh, writer


def main(argv=None):
    parser = argparse.ArgumentParser(
        epilog=(
            "Each engine/scale needs independent base+reified instances. Override defaults with "
            "PCM_<ENGINE>_<SCALE>_{BASE,REIFIED}_ENDPOINT, e.g. "
            "PCM_OXIGRAPH_10M_BASE_ENDPOINT."
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
    parser.add_argument("--out", default=os.path.join(HERE, "construction_brnc.csv"))
    args = parser.parse_args(argv)

    engines = [value for value in args.engines.split(",") if value]
    scales = [value for value in args.scales.split(",") if value]
    classes = set(filter(None, args.classes.split(",")))
    methods = [value for value in args.methods.split(",") if value]
    unknown = set(methods) - set(IMPL)
    if unknown:
        parser.error(f"unknown methods: {sorted(unknown)}")

    manifest = [row for row in load_manifest() if row["class"] in classes]
    done = load_done(args.out)
    fh, writer = _open_writer(args.out)
    try:
        for engine in engines:
            cfg = ENGINES.get(engine)
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
                    with open(os.path.join(REF, manifest_row["query_file"])) as query_fh:
                        qtext = query_fh.read()
                    for method in methods:
                        key = (
                            engine, scale, cls, template, instance, query_sha, method
                        )
                        if key in done:
                            continue
                        if endpoints is None:
                            result = _empty_result(
                                "not-run", f"{scale} endpoints not registered"
                            )
                        else:
                            result = time_method(
                                method,
                                qtext,
                                endpoints["base"],
                                endpoints["reified"],
                            )
                        summary = stat(result["samples"]) if result["samples"] else None
                        parse_median = (
                            statistics.median(result["c_parse"])
                            if result["c_parse"]
                            else None
                        )
                        total_median = (
                            statistics.median(result["construct_total"])
                            if result["construct_total"]
                            else None
                        )
                        evidence = result.get("evidence") or {}
                        note_metadata = {
                            **evidence,
                            "c_parse_samples": result["c_parse"],
                            "construct_total_samples": result["construct_total"],
                        }
                        record = {
                            "commit": COMMIT,
                            "engine": engine,
                            "engine_version": cfg["version"],
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
                            "warmups": WARMUPS,
                            "runs": RUNS,
                            "timeout_s": TIMEOUT,
                            "response_bytes": result["response_bytes"],
                            "c_parse_median_ms": (
                                round(parse_median, 1) if parse_median is not None else None
                            ),
                            "c_parse_samples_json": json.dumps(result["c_parse"]),
                            "construct_total_ms": (
                                round(total_median, 1) if total_median is not None else None
                            ),
                            "construct_total_samples_json": json.dumps(
                                result["construct_total"]
                            ),
                            "gates": result["gates"],
                            "edges": result["edges"],
                            "derivations": result.get("derivations"),
                            "npcs_token_occurrences": result.get("ntok"),
                            "rewrite_ms": result.get("rewrite_ms"),
                            "samples_json": json.dumps(result["samples"]),
                            "protocol": PROTOCOL,
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
        fh.close()
    print(f"\nwrote/appended {args.out}")


if __name__ == "__main__":
    main()
