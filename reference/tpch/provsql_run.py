#!/usr/bin/env python3
"""Run the relational TPC-H baseline and ProvSQL as resumable cells."""

import argparse
import csv
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


HERE = Path(__file__).resolve().parent
REFERENCE = HERE.parent
if str(REFERENCE) not in sys.path:
    sys.path.insert(0, str(REFERENCE))

from tpch import provsql_prepare, provsql_workload


BATCH_SCHEMA = "tpch-provsql-batch-v1"
CELL_SCHEMA = "tpch-provsql-cell-v1"
METHODS = ("PG-B", "ProvSQL")
FORMAL_METHODS = ("ProvSQL",)
DEFAULT_PHASE_TIMEOUT = 3000.0
DEFAULT_COMPLETE_METHOD_TIMEOUT = 3000.0


class ProvsqlRunError(RuntimeError):
    """A relational TPC-H batch cannot continue safely."""


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ProvsqlRunError("cannot read JSON %s: %s" % (path, error)) from error


def _write_text(path: Path, value: str) -> None:
    if path.exists():
        raise ProvsqlRunError("refusing to overwrite %s" % path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".partial")
    with temporary.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(value)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _write_json(path: Path, value: Any) -> None:
    _write_text(
        path,
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )


def _identifier(value: str) -> str:
    if not re.fullmatch(r"[a-z][a-z0-9_]*", value):
        raise ProvsqlRunError("unsafe PostgreSQL identifier: %r" % value)
    return value


def _quoted(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        raise ProvsqlRunError("unsafe SQL identifier: %r" % value)
    return '"%s"' % value


def _literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _query_body(value: str) -> str:
    body = value.strip()
    if body.endswith(";"):
        body = body[:-1].rstrip()
    if not body:
        raise ProvsqlRunError("empty SQL query")
    return body


def _phase_timeout_ms(timeout_s: float) -> int:
    return max(1, int(math.ceil(timeout_s * 1000.0)))


def _session_header(schema: str, timeout_s: float, active: bool) -> str:
    return "\n".join((
        r"\set ON_ERROR_STOP on",
        "SET client_min_messages TO warning;",
        "SET application_name TO 'sparqlcirc-tpch-provsql';",
        'SET search_path TO "%s", public, provsql;' % _identifier(schema),
        "SET max_parallel_workers_per_gather TO 0;",
        "SET statement_timeout TO %s;" % _literal(
            "%dms" % _phase_timeout_ms(timeout_s)
        ),
        "SET provsql.active TO %s;" % ("on" if active else "off"),
    ))


def _qualified(schema: str, table: str) -> str:
    return '"%s"."%s"' % (_identifier(schema), _identifier(table))


def _table_names(entry: Mapping[str, Any], run_label: str) -> Tuple[str, str]:
    raw = "%s_%s_%s" % (entry["template"], entry["instance"], run_label)
    suffix = re.sub(r"[^a-z0-9]+", "_", raw.lower()).strip("_")
    roots = ("spcirc_roots_" + suffix)[:63]
    probabilities = ("spcirc_probabilities_" + suffix)[:63]
    return _identifier(roots), _identifier(probabilities)


def pg_baseline_sql(base_sql: str, schema: str, timeout_s: float) -> str:
    return "%s\nCOPY (\n%s\n) TO STDOUT WITH (FORMAT csv, HEADER true);\n" % (
        _session_header(schema, timeout_s, active=False),
        _query_body(base_sql),
    )


def deterministic_warmup_sql(
    answer_sql: str,
    schema: str,
    timeout_s: float,
) -> str:
    """Warm the relational plan without materializing reusable circuit gates."""
    return pg_baseline_sql(answer_sql, schema, timeout_s)


def cleanup_sql(
    schema: str,
    roots_table: str,
    probability_table: str,
    timeout_s: float,
) -> str:
    return "%s\nDROP TABLE IF EXISTS %s;\nDROP TABLE IF EXISTS %s;\n" % (
        _session_header(schema, timeout_s, active=False),
        _qualified(schema, probability_table),
        _qualified(schema, roots_table),
    )


def reset_tokens_sql(
    schema: str,
    timeout_s: float,
    probability_seed: int = provsql_prepare.event_probabilities.DEFAULT_PROBABILITY_SEED,
) -> str:
    """Mint fresh tuple tokens so a warm-up circuit cannot be reused."""
    provsql_prepare.event_probabilities.validate_seed(probability_seed)
    lines = [_session_header(schema, timeout_s, active=False)]
    for table in provsql_prepare.TABLES:
        relation = "%s.%s" % (schema, table)
        lines.append(
            "SELECT provsql.remove_provenance(%s::regclass);" % _literal(relation)
        )
        lines.append(
            "SELECT provsql.add_provenance(%s::regclass);" % _literal(relation)
        )
    lines.extend(("DO $$", "BEGIN"))
    for table in provsql_prepare.TABLES:
        lines.append(
            "  " + provsql_prepare.probability_assignment_sql(
                table, _qualified(schema, table), probability_seed
            )
        )
    lines.extend(("END", "$$;", "ANALYZE;", ""))
    return "\n".join(lines)


def construction_sql(
    answer_sql: str,
    schema: str,
    roots_table: str,
    probability_table: str,
    timeout_s: float,
) -> str:
    roots = _qualified(schema, roots_table)
    probabilities = _qualified(schema, probability_table)
    return "\n".join((
        _session_header(schema, timeout_s, active=False),
        "DROP TABLE IF EXISTS %s;" % probabilities,
        "DROP TABLE IF EXISTS %s;" % roots,
        "SET provsql.active TO on;",
        "SELECT 'gates_before', provsql.get_nb_gates();",
        "CREATE TABLE %s AS\n%s;" % (roots, _query_body(answer_sql)),
        "SELECT 'gates_after', provsql.get_nb_gates();",
        "",
    ))


def roots_export_sql(
    schema: str,
    roots_table: str,
    answer_columns: Sequence[str],
    timeout_s: float,
) -> str:
    selected = ", ".join(_quoted(column) for column in answer_columns)
    return "%s\nCOPY (\n  SELECT %s, provsql::text AS provenance_root\n  FROM %s\n) TO STDOUT WITH (FORMAT csv, HEADER true);\n" % (
        _session_header(schema, timeout_s, active=False),
        selected,
        _qualified(schema, roots_table),
    )


def pqe_sql(
    schema: str,
    roots_table: str,
    probability_table: str,
    answer_columns: Sequence[str],
    timeout_s: float,
) -> str:
    selected = ", ".join(_quoted(column) for column in answer_columns)
    return "\n".join((
        _session_header(schema, timeout_s, active=False),
        "SET provsql.last_eval_method TO '';",
        "DROP TABLE IF EXISTS %s;" % _qualified(schema, probability_table),
        "CREATE TABLE %s AS" % _qualified(schema, probability_table),
        "SELECT %s, probability_evaluate(provsql) AS probability" % selected,
        "FROM %s;" % _qualified(schema, roots_table),
        "SELECT current_setting('provsql.last_eval_method');",
        "",
    ))


def probability_export_sql(
    schema: str,
    probability_table: str,
    answer_columns: Sequence[str],
    timeout_s: float,
) -> str:
    selected = ", ".join(_quoted(column) for column in answer_columns)
    return "%s\nCOPY (\n  SELECT %s, probability\n  FROM %s\n) TO STDOUT WITH (FORMAT csv, HEADER true);\n" % (
        _session_header(schema, timeout_s, active=False),
        selected,
        _qualified(schema, probability_table),
    )


def circuit_metrics_sql(
    schema: str,
    roots_table: str,
    timeout_s: float,
) -> str:
    roots = _qualified(schema, roots_table)
    return "\n".join((
        _session_header(schema, timeout_s, active=False),
        "SELECT 'roots', count(*), count(DISTINCT provsql) FROM %s;" % roots,
        "WITH RECURSIVE reachable(node) AS (",
        "  SELECT provsql::uuid FROM %s" % roots,
        "  UNION",
        "  SELECT child.token",
        "  FROM reachable AS parent",
        "  CROSS JOIN LATERAL unnest(provsql.get_children(parent.node)) AS child(token)",
        ")",
        "SELECT 'circuit', count(*),",
        "       coalesce(sum(cardinality(provsql.get_children(node))), 0)",
        "FROM reachable;",
        "",
    ))


def _process_tree(root_pid: int) -> List[int]:
    found = {root_pid}
    pending = [root_pid]
    while pending:
        parent = pending.pop()
        try:
            children = (
                Path("/proc") / str(parent) / "task" / str(parent) / "children"
            ).read_text(encoding="utf-8").split()
        except OSError:
            continue
        for value in children:
            try:
                child = int(value)
            except ValueError:
                continue
            if child not in found:
                found.add(child)
                pending.append(child)
    return sorted(found)


def _process_pss_kib(pid: int) -> Optional[int]:
    """Read proportional memory, falling back to resident memory."""
    try:
        lines = (Path("/proc") / str(pid) / "smaps_rollup").read_text(
            encoding="utf-8"
        ).splitlines()
        for line in lines:
            if line.startswith("Pss:"):
                return int(line.split()[1])
    except (OSError, ValueError, IndexError):
        pass
    try:
        for line in (Path("/proc") / str(pid) / "status").read_text(
            encoding="utf-8"
        ).splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1])
    except (OSError, ValueError, IndexError):
        pass
    return None


def _tree_rss_kib(root_pid: Optional[int]) -> Optional[int]:
    if root_pid is None:
        return None
    if not (Path("/proc") / str(root_pid)).is_dir():
        return None
    values = [
        value for value in (_process_pss_kib(pid) for pid in _process_tree(root_pid))
        if value is not None
    ]
    return sum(values) if values else None


def _postgres_pid(path: Optional[Path]) -> Optional[int]:
    if path is None:
        return None
    try:
        return int(path.read_text(encoding="utf-8").splitlines()[0])
    except (OSError, ValueError, IndexError):
        return None


def _psql_command(
    psql: Path,
    psql_args: Sequence[str],
    dsn: str,
    script: Path,
) -> List[str]:
    return [
        str(psql), *psql_args, "-X", "--dbname", dsn, "-q", "-A", "-t",
        "-F", "\t", "-v", "ON_ERROR_STOP=1", "-f", str(script),
    ]


def _run_phase(
    psql: Path,
    psql_args: Sequence[str],
    dsn: str,
    script: Path,
    stdout_path: Optional[Path],
    stderr_path: Path,
    timeout_s: float,
    postgres_pid_file: Optional[Path],
) -> Dict[str, Any]:
    postgres_pid = _postgres_pid(postgres_pid_file)
    server_baseline = _tree_rss_kib(postgres_pid)
    server_peak = server_baseline
    client_peak: Optional[int] = None
    timed_out = False
    stdout_handle = (
        stdout_path.open("wb") if stdout_path is not None else open(os.devnull, "wb")
    )
    with stdout_handle as stdout, stderr_path.open("wb") as stderr:
        started = time.perf_counter()
        process = subprocess.Popen(
            _psql_command(psql, psql_args, dsn, script),
            stdout=stdout,
            stderr=stderr,
        )
        deadline = started + timeout_s
        next_client_sample = started
        next_server_sample = started
        while process.poll() is None:
            now = time.perf_counter()
            if now >= next_client_sample:
                client_rss = _tree_rss_kib(process.pid)
                if client_rss is not None:
                    client_peak = max(client_peak or 0, client_rss)
                next_client_sample = now + 0.01
            if now >= next_server_sample:
                server_rss = _tree_rss_kib(postgres_pid)
                if server_rss is not None:
                    server_peak = max(server_peak or 0, server_rss)
                next_server_sample = now + 0.05
            if now >= deadline:
                timed_out = True
                process.terminate()
                try:
                    process.wait(timeout=5.0)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                break
            time.sleep(0.002)
        return_code = process.returncode if not timed_out else 124
    wall_ms = (time.perf_counter() - started) * 1000.0
    try:
        error_text = stderr_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        error_text = ""
    statement_timeout = "statement timeout" in error_text.lower()
    if timed_out or statement_timeout:
        status = "timeout"
    elif return_code == 0:
        status = "ok"
    else:
        status = "error"
    return {
        "status": status,
        "return_code": return_code,
        "client_wall_ms": wall_ms,
        "timeout_s": timeout_s,
        "timeout_source": (
            "client" if timed_out else "postgresql" if statement_timeout else None
        ),
        "client_peak_memory_kib": client_peak,
        "server_process_tree_memory_kib": {
            "measurement": "PSS from smaps_rollup, with VmRSS as a fallback",
            "baseline": server_baseline,
            "peak": server_peak,
            "peak_delta": (
                server_peak - server_baseline
                if server_peak is not None and server_baseline is not None else None
            ),
        },
        "script": script.name,
        "stdout": stdout_path.name if stdout_path is not None else None,
        "stderr": stderr_path.name,
    }


def _execute(
    directory: Path,
    name: str,
    sql: str,
    args: argparse.Namespace,
    timeout_s: float,
    capture_stdout: bool = True,
) -> Dict[str, Any]:
    script = directory / (name + ".sql")
    stdout = directory / (name + ".stdout") if capture_stdout else None
    stderr = directory / (name + ".stderr")
    _write_text(script, sql)
    return _run_phase(
        args.psql,
        args.psql_args,
        args.dsn,
        script,
        stdout,
        stderr,
        timeout_s,
        args.postgres_pid_file,
    )


def _parse_gate_counts(path: Path) -> Tuple[int, int]:
    values: Dict[str, int] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        fields = line.split("\t")
        if len(fields) == 2 and fields[0] in ("gates_before", "gates_after"):
            values[fields[0]] = int(fields[1])
    if set(values) != {"gates_before", "gates_after"}:
        raise ProvsqlRunError("construction output does not contain both gate counts")
    return values["gates_before"], values["gates_after"]


def _csv_summary(path: Path, required_column: Optional[str] = None) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if required_column is not None and required_column not in (reader.fieldnames or ()):
            raise ProvsqlRunError(
                "CSV is missing %s: %s" % (required_column, path)
            )
        rows = 0
        for _row in reader:
            rows += 1
    return {"rows": rows, "serialized_bytes": path.stat().st_size}


def _parse_metrics(path: Path) -> Dict[str, int]:
    values: Dict[str, int] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        fields = line.split("\t")
        if len(fields) != 3:
            continue
        if fields[0] == "roots":
            values["answer_rows"] = int(fields[1])
            values["distinct_roots"] = int(fields[2])
        elif fields[0] == "circuit":
            values["reachable_nodes"] = int(fields[1])
            values["reachable_edges"] = int(fields[2])
    required = {"answer_rows", "distinct_roots", "reachable_nodes", "reachable_edges"}
    if set(values) != required:
        raise ProvsqlRunError("circuit metric output is incomplete: %s" % path)
    values["reachable_nodes_plus_edges"] = (
        values["reachable_nodes"] + values["reachable_edges"]
    )
    return values


def _terminal_status(phases: Iterable[Mapping[str, Any]]) -> Tuple[str, Optional[str]]:
    for phase in phases:
        if phase.get("status") != "ok":
            return str(phase.get("status")), str(phase.get("name"))
    return "ok", None


def _named(name: str, value: Dict[str, Any]) -> Dict[str, Any]:
    value["name"] = name
    return value


def _budgeted_timeout(
    configured_timeout_s: float,
    total_timeout_s: Optional[float],
    started: Optional[float],
) -> Tuple[float, Optional[float]]:
    """Return the phase timeout and remaining shared measured-run budget."""
    if total_timeout_s is None or started is None:
        return configured_timeout_s, None
    remaining = total_timeout_s - (time.perf_counter() - started)
    return min(configured_timeout_s, max(0.001, remaining)), remaining


def _annotate_budget(
    phase: Dict[str, Any],
    configured_timeout_s: float,
    total_timeout_s: Optional[float],
    remaining_before_s: Optional[float],
) -> None:
    if total_timeout_s is None or remaining_before_s is None:
        return
    phase.update({
        "configured_phase_timeout_s": configured_timeout_s,
        "measured_total_timeout_s": total_timeout_s,
        "measured_total_remaining_before_phase_s": max(0.0, remaining_before_s),
        "budget_exhausted_before_phase": remaining_before_s <= 0.0,
    })
    if (
        phase.get("status") == "timeout"
        and float(phase["timeout_s"]) < configured_timeout_s
    ):
        phase["timeout_source"] = "measured-total-budget"


def _run_pg_cell(
    entry: Mapping[str, Any],
    workload_root: Path,
    dataset: Mapping[str, Any],
    cell: Path,
    args: argparse.Namespace,
) -> Dict[str, Any]:
    base_sql = (workload_root / str(entry["base_sql"])).read_text(encoding="utf-8")
    schema = str(dataset["database_schema"])
    warmups: List[Dict[str, Any]] = []
    measured: List[Dict[str, Any]] = []
    failed_stage: Optional[str] = None
    status = "ok"
    for index in range(1, args.warmups + 1):
        directory = cell / "warmups" / ("warmup%03d" % index)
        directory.mkdir(parents=True)
        phase = _named(
            "query_serialization_transfer",
            _execute(
                directory,
                "query",
                pg_baseline_sql(base_sql, schema, args.query_timeout),
                args,
                args.query_timeout,
                capture_stdout=False,
            ),
        )
        warmups.append(phase)
        if phase["status"] != "ok":
            status, failed_stage = phase["status"], "warmup.query_serialization_transfer"
            break
    if status == "ok":
        for index in range(1, args.runs + 1):
            directory = cell / "runs" / ("run%03d" % index)
            directory.mkdir(parents=True)
            output = directory / "answers.csv.partial"
            script = directory / "query.sql"
            stderr = directory / "query.stderr"
            _write_text(script, pg_baseline_sql(base_sql, schema, args.query_timeout))
            phase = _named(
                "query_serialization_transfer",
                _run_phase(
                    args.psql, args.psql_args, args.dsn, script, output, stderr,
                    args.query_timeout, args.postgres_pid_file,
                ),
            )
            record: Dict[str, Any] = {"run": index, "phases": [phase]}
            if phase["status"] == "ok":
                final_output = directory / "answers.csv"
                os.replace(output, final_output)
                phase["stdout"] = final_output.name
                record.update(_csv_summary(final_output))
                record["full_end_to_end_ms"] = phase["client_wall_ms"]
            else:
                status, failed_stage = phase["status"], phase["name"]
            _write_json(directory / "run.json", record)
            measured.append(record)
            if status != "ok":
                break
    return {
        "status": status,
        "failed_stage": failed_stage,
        "warmups": warmups,
        "runs": measured,
    }


def _cleanup(
    directory: Path,
    name: str,
    schema: str,
    roots_table: str,
    probability_table: str,
    args: argparse.Namespace,
) -> Dict[str, Any]:
    return _named(
        name,
        _execute(
            directory,
            name,
            cleanup_sql(
                schema, roots_table, probability_table, args.offline_timeout
            ),
            args,
            args.offline_timeout,
        ),
    )


def _reset(
    directory: Path,
    name: str,
    schema: str,
    args: argparse.Namespace,
) -> Dict[str, Any]:
    return _named(
        name,
        _execute(
            directory,
            name,
            reset_tokens_sql(
                schema, args.offline_timeout, args.probability_seed
            ),
            args,
            args.offline_timeout,
        ),
    )


def _provsql_execution(
    entry: Mapping[str, Any],
    answer_sql: str,
    schema: str,
    directory: Path,
    label: str,
    args: argparse.Namespace,
    measured: bool,
    fresh_tokens: bool,
) -> Dict[str, Any]:
    roots_table, probability_table = _table_names(entry, label)
    columns = tuple(str(column) for column in entry["answer_columns"])
    phases: List[Dict[str, Any]] = []
    total_timeout = (
        getattr(args, "measured_total_timeout", None) if measured else None
    )
    budget_started: Optional[float] = None
    budget_stopped: Optional[float] = None

    def budget_metadata() -> Dict[str, Any]:
        if total_timeout is None or budget_started is None:
            return {}
        stopped = budget_stopped if budget_stopped is not None else time.perf_counter()
        return {
            "measured_total_timeout_s": total_timeout,
            "measured_budget_elapsed_ms": (
                stopped - budget_started
            ) * 1000.0,
        }

    def failed(phase: Mapping[str, Any]) -> Dict[str, Any]:
        metadata = budget_metadata()
        cleanup = _cleanup(
            directory, "cleanup_offline", schema, roots_table,
            probability_table, args,
        )
        phases.append(cleanup)
        result = {
            "phases": phases,
            "status": phase["status"],
            "failed_stage": phase["name"],
        }
        result.update(metadata)
        return result

    reset: Optional[Dict[str, Any]] = None
    if fresh_tokens:
        reset = _reset(directory, "fresh_input_tokens", schema, args)
        phases.append(reset)
        if reset["status"] != "ok":
            return failed(reset)

    if total_timeout is not None:
        budget_started = time.perf_counter()
    construction_timeout, construction_remaining = _budgeted_timeout(
        args.query_timeout, total_timeout, budget_started
    )
    construction = _named(
        "provenance_construction",
        _execute(
            directory,
            "construction",
            construction_sql(
                answer_sql, schema, roots_table, probability_table,
                construction_timeout,
            ),
            args,
            construction_timeout,
        ),
    )
    _annotate_budget(
        construction, args.query_timeout, total_timeout, construction_remaining
    )
    phases.append(construction)
    if construction["status"] != "ok":
        return failed(construction)
    before, after = _parse_gate_counts(directory / "construction.stdout")
    construction["global_gates_before"] = before
    construction["global_gates_after"] = after
    construction["new_materialized_gates"] = after - before

    root_summary: Optional[Dict[str, Any]] = None
    metrics_summary: Optional[Dict[str, int]] = None
    if measured:
        root_timeout, root_remaining = _budgeted_timeout(
            args.query_timeout, total_timeout, budget_started
        )
        roots_partial = directory / "roots.csv.partial"
        roots_script = directory / "roots-export.sql"
        roots_stderr = directory / "roots-export.stderr"
        _write_text(
            roots_script,
            roots_export_sql(schema, roots_table, columns, root_timeout),
        )
        root_export = _named(
            "root_serialization_transfer",
            _run_phase(
                args.psql, args.psql_args, args.dsn, roots_script, roots_partial,
                roots_stderr, root_timeout, args.postgres_pid_file,
            ),
        )
        _annotate_budget(
            root_export, args.query_timeout, total_timeout, root_remaining
        )
        phases.append(root_export)
        if root_export["status"] != "ok":
            return failed(root_export)
        roots_final = directory / "roots.csv"
        os.replace(roots_partial, roots_final)
        root_export["stdout"] = roots_final.name
        root_summary = _csv_summary(roots_final, required_column="provenance_root")
        root_export.update(root_summary)

    pqe_timeout, pqe_remaining = _budgeted_timeout(
        args.pqe_timeout, total_timeout, budget_started
    )
    pqe = _named(
        "pqe_compute",
        _execute(
            directory,
            "pqe",
            pqe_sql(
                schema, roots_table, probability_table, columns, pqe_timeout
            ),
            args,
            pqe_timeout,
        ),
    )
    _annotate_budget(pqe, args.pqe_timeout, total_timeout, pqe_remaining)
    phases.append(pqe)
    if pqe["status"] != "ok":
        return failed(pqe)
    methods = [
        line for line in (directory / "pqe.stdout").read_text(
            encoding="utf-8"
        ).splitlines() if line
    ]
    pqe["reported_evaluation_methods"] = methods[-1] if methods else ""

    probability_summary: Optional[Dict[str, Any]] = None
    if measured:
        probability_timeout, probability_remaining = _budgeted_timeout(
            args.query_timeout, total_timeout, budget_started
        )
        output_partial = directory / "probabilities.csv.partial"
        export_script = directory / "probabilities-export.sql"
        export_stderr = directory / "probabilities-export.stderr"
        _write_text(
            export_script,
            probability_export_sql(
                schema, probability_table, columns, probability_timeout
            ),
        )
        probability_export = _named(
            "probability_serialization_transfer",
            _run_phase(
                args.psql, args.psql_args, args.dsn, export_script,
                output_partial, export_stderr, probability_timeout,
                args.postgres_pid_file,
            ),
        )
        _annotate_budget(
            probability_export,
            args.query_timeout,
            total_timeout,
            probability_remaining,
        )
        phases.append(probability_export)
        if probability_export["status"] == "ok":
            output_final = directory / "probabilities.csv"
            os.replace(output_partial, output_final)
            probability_export["stdout"] = output_final.name
            probability_summary = _csv_summary(output_final)
            probability_export.update(probability_summary)
        else:
            return failed(probability_export)

        budget_stopped = time.perf_counter()

        metrics = _named(
            "circuit_metrics_offline",
            _execute(
                directory,
                "circuit-metrics",
                circuit_metrics_sql(
                    schema, roots_table, args.circuit_metrics_timeout
                ),
                args,
                args.circuit_metrics_timeout,
            ),
        )
        phases.append(metrics)
        if metrics["status"] == "ok":
            metrics_summary = _parse_metrics(directory / "circuit-metrics.stdout")
            metrics.update(metrics_summary)

    cleanup = _cleanup(
        directory, "cleanup_offline", schema, roots_table, probability_table, args
    )
    phases.append(cleanup)
    status, failed_stage = _terminal_status(
        phase for phase in phases
        if phase["name"] != "circuit_metrics_offline"
    )
    result: Dict[str, Any] = {
        "phases": phases,
        "status": status,
        "failed_stage": failed_stage,
    }
    result.update(budget_metadata())
    if measured and status == "ok":
        native = construction["client_wall_ms"] + pqe["client_wall_ms"]
        transferred = sum(
            phase["client_wall_ms"] for phase in phases
            if phase["name"] in (
                "provenance_construction",
                "root_serialization_transfer",
                "pqe_compute",
                "probability_serialization_transfer",
            )
        )
        result.update({
            "native_database_total_ms": native,
            "artifact_complete_total_ms": transferred,
            "pre_run_reset_wall_ms": (
                reset["client_wall_ms"] if reset is not None else None
            ),
            "answer_rows": root_summary["rows"] if root_summary else None,
            "distinct_provenance_roots": (
                metrics_summary["distinct_roots"] if metrics_summary else None
            ),
            "probability_rows": (
                probability_summary["rows"] if probability_summary else None
            ),
        })
    return result


def _run_provsql_cell(
    entry: Mapping[str, Any],
    workload_root: Path,
    dataset: Mapping[str, Any],
    cell: Path,
    args: argparse.Namespace,
) -> Dict[str, Any]:
    answer_sql = (
        workload_root / str(entry["answer_sql"])
    ).read_text(encoding="utf-8")
    schema = str(dataset["database_schema"])
    warmups: List[Dict[str, Any]] = []
    measured_runs: List[Dict[str, Any]] = []
    status = "ok"
    failed_stage: Optional[str] = None

    for index in range(1, args.warmups + 1):
        directory = cell / "warmups" / ("warmup%03d" % index)
        directory.mkdir(parents=True)
        if args.warmup_policy == "deterministic-query":
            phase = _named(
                "relational_data_path_warmup",
                _execute(
                    directory,
                    "query",
                    deterministic_warmup_sql(
                        answer_sql, schema, args.query_timeout
                    ),
                    args,
                    args.query_timeout,
                    capture_stdout=False,
                ),
            )
            record = {
                "phases": [phase],
                "status": phase["status"],
                "failed_stage": (
                    None if phase["status"] == "ok" else phase["name"]
                ),
            }
        else:
            record = _provsql_execution(
                entry, answer_sql, schema, directory, "w%03d" % index,
                args, measured=False, fresh_tokens=True,
            )
        _write_json(directory / "run.json", record)
        warmups.append(record)
        if record["status"] != "ok":
            status, failed_stage = record["status"], "warmup.%s" % record["failed_stage"]
            break

    if status == "ok":
        for index in range(1, args.runs + 1):
            directory = cell / "runs" / ("run%03d" % index)
            directory.mkdir(parents=True)
            record = _provsql_execution(
                entry, answer_sql, schema, directory, "r%03d" % index,
                args, measured=True,
                fresh_tokens=args.warmup_policy == "full-fresh-tokens",
            )
            record["run"] = index
            _write_json(directory / "run.json", record)
            measured_runs.append(record)
            if record["status"] != "ok":
                status, failed_stage = record["status"], record["failed_stage"]
                break
    return {
        "status": status,
        "failed_stage": failed_stage,
        "warmups": warmups,
        "runs": measured_runs,
        "timing_policy": {
            "native_database_total": "provenance construction plus in-database PQE",
            "artifact_complete_total": (
                "construction, provenance-root export, in-database PQE, and probability export"
            ),
            "excluded_offline": (
                "fresh input tokens when requested, cleanup, and post-PQE circuit traversal"
            ),
        },
        "warmup_policy": args.warmup_policy,
        "warmup_interpretation": (
            "the grouped relational query runs with provenance disabled so the measured "
            "content-addressed circuit is not reused"
            if args.warmup_policy == "deterministic-query"
            else "a full ProvSQL warm-up and measured run use separate tuple UUID namespaces"
        ),
    }


def _cell_path(output: Path, entry: Mapping[str, Any], method: str) -> Path:
    return output / str(entry["template"]) / str(entry["instance"]) / method


def _cell_record(path: Path) -> Optional[Mapping[str, Any]]:
    manifest = path / "cell.json"
    if manifest.is_file():
        value = _read_json(manifest)
        if not isinstance(value, Mapping):
            raise ProvsqlRunError("cell manifest is not an object: %s" % manifest)
        return value
    if path.exists():
        raise ProvsqlRunError(
            "partial cell has no cell.json and must be preserved or moved before resume: %s"
            % path
        )
    return None


def _select_entries(
    manifest: Mapping[str, Any],
    scale: str,
    query_ids: Optional[Sequence[str]],
) -> List[Mapping[str, Any]]:
    entries = [
        entry for entry in manifest["entries"]
        if str(entry["scale_factor"]) == scale
    ]
    if not entries:
        raise ProvsqlRunError("workload has no entries for scale %s" % scale)
    if not query_ids:
        return entries
    available = {str(entry["query_id"]) for entry in entries}
    unknown = set(query_ids).difference(available)
    if unknown:
        raise ProvsqlRunError(
            "query ids are not present at scale %s: %s"
            % (scale, ", ".join(sorted(unknown)))
        )
    selected = set(query_ids)
    return [entry for entry in entries if str(entry["query_id"]) in selected]


def _configuration(
    args: argparse.Namespace,
    dataset: Mapping[str, Any],
) -> Dict[str, Any]:
    return {
        "schema": BATCH_SCHEMA,
        "workload_manifest": str(args.manifest.resolve()),
        "dataset_metadata": str(args.dataset.resolve()),
        "scale_factor": args.scale,
        "database_schema": dataset["database_schema"],
        "postgresql_version": dataset["postgresql_version"],
        "provsql_version": dataset["provsql_version"],
        "methods": list(args.methods),
        "query_ids": list(args.query_ids) if args.query_ids else None,
        "warmups": args.warmups,
        "measured_runs": args.runs,
        "primary_statistic": "median",
        "provsql_warmup_policy": args.warmup_policy,
        "query_timeout_s": args.query_timeout,
        "pqe_timeout_s": args.pqe_timeout,
        "measured_total_timeout_s": args.measured_total_timeout,
        "offline_timeout_s": args.offline_timeout,
        "circuit_metrics_timeout_s": args.circuit_metrics_timeout,
        "probability_seed": dataset["probability_seed"],
        "probability_scheme": dataset["probability_scheme"],
        "materialized_gates_after_load": dataset["materialized_gates_after_load"],
        "psql_command": [str(args.psql.resolve()), *args.psql_args, "<connection omitted>"],
        "postgres_pid_file": (
            str(args.postgres_pid_file.resolve())
            if args.postgres_pid_file is not None else None
        ),
        "connection": "provided at runtime and deliberately not recorded",
        "cell_order": "derived workload manifest order, then requested method order",
        "resume_policy": "terminal cell.json files are immutable and reused",
    }


def _validate(
    args: argparse.Namespace,
    workload: Mapping[str, Any],
    dataset: Mapping[str, Any],
) -> None:
    if args.warmups != 1 or args.runs != 5:
        raise ProvsqlRunError(
            "the formal protocol is exactly one warm-up and five measured runs"
        )
    if workload.get("schema") != provsql_workload.SCHEMA:
        raise ProvsqlRunError("unsupported relational workload metadata")
    if dataset.get("schema") != provsql_prepare.SCHEMA:
        raise ProvsqlRunError("unsupported ProvSQL dataset metadata")
    if str(dataset.get("postgresql_version", "")).split()[0] != (
        provsql_prepare.POSTGRESQL_VERSION
    ):
        raise ProvsqlRunError(
            "formal ProvSQL runs require PostgreSQL %s"
            % provsql_prepare.POSTGRESQL_VERSION
        )
    if str(dataset.get("scale_factor")) != args.scale:
        raise ProvsqlRunError("dataset scale does not match --scale")
    if args.scale not in [str(value) for value in workload.get("scale_factors", [])]:
        raise ProvsqlRunError("workload scale does not match --scale")
    if str(dataset.get("tpch_version")) != str(workload.get("tpch_version")):
        raise ProvsqlRunError("TPC-H versions differ between dataset and workload")
    try:
        probability_seed = int(dataset.get("probability_seed"))
        provsql_prepare.event_probabilities.validate_seed(probability_seed)
    except (TypeError, ValueError) as error:
        raise ProvsqlRunError("ProvSQL dataset has no valid probability seed") from error
    if dataset.get("probability_scheme") != (
        provsql_prepare.event_probabilities.PROBABILITY_SCHEME
    ):
        raise ProvsqlRunError("ProvSQL dataset uses an unsupported probability scheme")
    row_counts = dataset.get("row_counts")
    if not isinstance(row_counts, Mapping) or set(row_counts) != set(
        provsql_prepare.TABLES
    ):
        raise ProvsqlRunError("ProvSQL dataset metadata has incomplete row counts")
    try:
        input_rows = sum(int(row_counts[table]) for table in provsql_prepare.TABLES)
        loaded_gates = int(dataset["materialized_gates_after_load"])
    except (KeyError, TypeError, ValueError) as error:
        raise ProvsqlRunError(
            "ProvSQL dataset metadata has no valid post-load gate baseline"
        ) from error
    if input_rows < 0 or loaded_gates < input_rows:
        raise ProvsqlRunError("post-load gate baseline is smaller than the input row count")
    if not args.psql.is_file():
        raise ProvsqlRunError("psql launcher does not exist: %s" % args.psql)
    unknown = set(args.methods).difference(METHODS)
    if unknown:
        raise ProvsqlRunError("unsupported methods: %s" % ", ".join(sorted(unknown)))
    if len(set(args.methods)) != len(args.methods):
        raise ProvsqlRunError("methods must be unique")
    if args.query_ids and len(set(args.query_ids)) != len(args.query_ids):
        raise ProvsqlRunError("query ids must be unique")


def run(args: argparse.Namespace) -> Dict[str, Any]:
    provsql_workload.audit(args.manifest.resolve())
    workload = _read_json(args.manifest.resolve())
    dataset = _read_json(args.dataset.resolve())
    _validate(args, workload, dataset)
    args.probability_seed = int(dataset["probability_seed"])
    entries = _select_entries(workload, args.scale, args.query_ids)

    output = args.out.resolve()
    output.mkdir(parents=True, exist_ok=True)
    config_path = output / "batch-config.json"
    configuration = _configuration(args, dataset)
    if config_path.exists():
        if _read_json(config_path) != configuration:
            raise ProvsqlRunError("resume configuration differs from batch-config.json")
    else:
        _write_json(config_path, configuration)
    if (output / "batch.json").is_file():
        return _read_json(output / "batch.json")

    workload_root = args.manifest.resolve().parent
    invoked = 0
    reused = 0
    stop = False
    for entry in entries:
        for method in args.methods:
            cell = _cell_path(output, entry, method)
            existing = _cell_record(cell)
            if existing is not None:
                if (
                    existing.get("query_id") != entry["query_id"]
                    or existing.get("method") != method
                ):
                    raise ProvsqlRunError("existing cell identity mismatch: %s" % cell)
                reused += 1
                continue
            cell.mkdir(parents=True)
            if method == "PG-B":
                result = _run_pg_cell(entry, workload_root, dataset, cell, args)
            else:
                result = _run_provsql_cell(entry, workload_root, dataset, cell, args)
            record = {
                "schema": CELL_SCHEMA,
                "status": result["status"],
                "failed_stage": result["failed_stage"],
                "query_id": entry["query_id"],
                "template": entry["template"],
                "instance": entry["instance"],
                "scale_factor": entry["scale_factor"],
                "method": method,
                "answer_columns": entry["answer_columns"],
                "protocol": {
                    "warmups": args.warmups,
                    "measured_runs": args.runs,
                    "primary_statistic": "median",
                    "probability_seed": dataset["probability_seed"],
                    "probability_scheme": dataset["probability_scheme"],
                },
                "warmups": result["warmups"],
                "runs": result["runs"],
            }
            if "timing_policy" in result:
                record["timing_policy"] = result["timing_policy"]
                record["warmup_policy"] = result["warmup_policy"]
                record["warmup_interpretation"] = result["warmup_interpretation"]
            _write_json(cell / "cell.json", record)
            invoked += 1
            if result["status"] != "ok" and not args.continue_after_failure:
                stop = True
                break
        if stop:
            break
    if stop:
        raise ProvsqlRunError(
            "a cell failed; recover the database if needed and rerun to resume"
        )

    records = [
        _cell_record(_cell_path(output, entry, method))
        for entry in entries for method in args.methods
    ]
    if any(record is None for record in records):
        raise ProvsqlRunError("batch ended before every cell became terminal")
    successful = sum(record.get("status") == "ok" for record in records if record)
    result = {
        "schema": BATCH_SCHEMA,
        "status": "terminal",
        "scale_factor": args.scale,
        "expected_cells": len(records),
        "successful_cells": successful,
        "non_successful_cells": len(records) - successful,
        "invoked_in_final_pass": invoked,
        "reused_in_final_pass": reused,
        "batch_config": str(config_path),
    }
    _write_json(output / "batch.json", result)
    return result


def _positive(value: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("value must be positive and finite")
    return number


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


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--scale", required=True)
    parser.add_argument("--method", dest="methods", action="append", choices=METHODS)
    parser.add_argument("--query-id", dest="query_ids", action="append")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--psql", required=True, type=Path)
    parser.add_argument(
        "--psql-arg", dest="psql_args", action="append", default=[],
        help="argument inserted between the psql launcher and normal psql options",
    )
    parser.add_argument("--dsn", required=True)
    parser.add_argument("--postgres-pid-file", type=Path)
    parser.add_argument("--warmups", type=_nonnegative_integer, default=1)
    parser.add_argument("--runs", type=_positive_integer, default=5)
    parser.add_argument(
        "--warmup-policy",
        choices=("deterministic-query", "full-fresh-tokens"),
        default="deterministic-query",
        help=(
            "ProvSQL warm-up policy; deterministic-query avoids gate reuse and is the "
            "formal scalable policy"
        ),
    )
    parser.add_argument("--query-timeout", type=_positive, default=DEFAULT_PHASE_TIMEOUT)
    parser.add_argument("--pqe-timeout", type=_positive, default=DEFAULT_PHASE_TIMEOUT)
    parser.add_argument(
        "--measured-total-timeout",
        type=_positive,
        default=DEFAULT_COMPLETE_METHOD_TIMEOUT,
        help=(
            "shared wall-clock cap for measured ProvSQL construction, root export, "
            "PQE, and probability export; phase-specific limits remain additional caps "
            "(default: 3000)"
        ),
    )
    parser.add_argument("--offline-timeout", type=_positive, default=DEFAULT_PHASE_TIMEOUT)
    parser.add_argument(
        "--circuit-metrics-timeout", type=_positive,
        default=DEFAULT_PHASE_TIMEOUT,
    )
    parser.add_argument("--continue-after-failure", action="store_true")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    args.methods = tuple(args.methods or FORMAL_METHODS)
    args.psql_args = tuple(args.psql_args)
    try:
        result = run(args)
    except (OSError, ValueError, ProvsqlRunError,
            provsql_workload.ProvsqlWorkloadError) as error:
        parser.exit(2, "ProvSQL TPC-H batch: error: %s\n" % error)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
