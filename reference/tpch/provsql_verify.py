#!/usr/bin/env python3
"""Verify answer-binding parity across PG-B, ProvSQL roots, and PQE output."""

import argparse
import csv
from decimal import Decimal, InvalidOperation
import json
import math
import os
from pathlib import Path
import sqlite3
import sys
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple
import uuid


HERE = Path(__file__).resolve().parent
REFERENCE = HERE.parent
if str(REFERENCE) not in sys.path:
    sys.path.insert(0, str(REFERENCE))

from tpch import provsql_workload


SCHEMA = "tpch-provsql-parity-v1"
CELL_SCHEMA = "tpch-provsql-cell-v1"


class ProvsqlVerificationError(RuntimeError):
    """ProvSQL result artifacts cannot be compared safely."""


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ProvsqlVerificationError("cannot read JSON %s: %s" % (path, error)) from error


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    if path.exists():
        raise ProvsqlVerificationError("refusing to overwrite %s" % path)
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".partial")
    with partial.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(partial, path)


def _entry(manifest: Mapping[str, Any], query_id: str) -> Mapping[str, Any]:
    matches = [
        entry for entry in manifest["entries"]
        if str(entry.get("query_id")) == query_id
    ]
    if len(matches) != 1:
        raise ProvsqlVerificationError(
            "expected one workload entry for %s, found %d" % (query_id, len(matches))
        )
    return matches[0]


def _cell(cells: Path, entry: Mapping[str, Any], method: str) -> Tuple[Path, Mapping[str, Any]]:
    path = cells / str(entry["template"]) / str(entry["instance"]) / method
    value = _read_json(path / "cell.json")
    if (
        not isinstance(value, Mapping)
        or value.get("schema") != CELL_SCHEMA
        or value.get("query_id") != entry["query_id"]
        or value.get("method") != method
    ):
        raise ProvsqlVerificationError("invalid %s cell identity: %s" % (method, path))
    if value.get("status") != "ok":
        raise ProvsqlVerificationError(
            "%s cell is not successful: %s" % (method, value.get("status"))
        )
    runs = value.get("runs")
    protocol = value.get("protocol")
    expected = protocol.get("measured_runs") if isinstance(protocol, Mapping) else None
    if (
        not isinstance(runs, list)
        or expected is None
        or len(runs) != int(expected)
        or any(run.get("status", "ok") != "ok" for run in runs)
    ):
        raise ProvsqlVerificationError(
            "%s cell does not satisfy its measured-run contract" % method
        )
    # Correctness is deterministic; run001 is the representative artifact after
    # all measured executions have been checked for successful completion.
    return path / "runs" / "run001", value


def _normalize_sql_value(value: str, signature: Optional[Tuple[str, str, str]]) -> str:
    if signature is None or signature[0] != "literal":
        return value
    datatype = signature[1]
    try:
        if datatype == "http://www.w3.org/2001/XMLSchema#decimal":
            normalized = format(Decimal(value).normalize(), "f")
            return "0" if Decimal(normalized) == 0 else normalized
        if datatype == "http://www.w3.org/2001/XMLSchema#integer":
            return str(int(value))
    except (InvalidOperation, ValueError) as error:
        raise ProvsqlVerificationError(
            "SQL value %r is not valid for RDF datatype %s" % (value, datatype)
        ) from error
    return value


def _key(
    row: Mapping[str, str],
    columns: Sequence[str],
    signatures: Optional[Mapping[str, Tuple[str, str, str]]] = None,
) -> str:
    return json.dumps(
        [
            _normalize_sql_value(
                row[column], signatures.get(column) if signatures is not None else None
            )
            for column in columns
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _require_columns(reader: csv.DictReader, columns: Sequence[str], path: Path) -> None:
    missing = set(columns).difference(reader.fieldnames or ())
    if missing:
        raise ProvsqlVerificationError(
            "%s is missing columns: %s" % (path, ", ".join(sorted(missing)))
        )


def _load_base(
    connection: sqlite3.Connection,
    path: Path,
    columns: Sequence[str],
    signatures: Optional[Mapping[str, Tuple[str, str, str]]] = None,
) -> Dict[str, int]:
    total = 0
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        _require_columns(reader, columns, path)
        for row in reader:
            total += 1
            connection.execute(
                "INSERT INTO base(binding, multiplicity) VALUES (?, 1) "
                "ON CONFLICT(binding) DO UPDATE SET multiplicity = multiplicity + 1",
                (_key(row, columns, signatures),),
            )
    distinct = connection.execute("SELECT count(*) FROM base").fetchone()[0]
    return {"rows": total, "distinct_bindings": int(distinct), "serialized_bytes": path.stat().st_size}


def _load_sparql_records(
    connection: sqlite3.Connection,
    path: Path,
    columns: Sequence[str],
) -> Tuple[Dict[str, Any], Dict[str, Tuple[str, str, str]]]:
    rows = 0
    records = 0
    duplicate_bindings = 0
    signatures: Dict[str, set] = {column: set() for column in columns}
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as error:
                raise ProvsqlVerificationError(
                    "invalid SPARQL answer record at %s:%d" % (path, line_number)
                ) from error
            multiplicity = record.get("multiplicity") if isinstance(record, Mapping) else None
            raw_binding = record.get("binding") if isinstance(record, Mapping) else None
            if (
                isinstance(multiplicity, bool)
                or not isinstance(multiplicity, int)
                or multiplicity < 1
                or not isinstance(raw_binding, list)
            ):
                raise ProvsqlVerificationError(
                    "invalid SPARQL answer record at %s:%d" % (path, line_number)
                )
            values: Dict[str, str] = {}
            row_signatures: Dict[str, Tuple[str, str, str]] = {}
            for item in raw_binding:
                if not isinstance(item, list) or len(item) != 2:
                    raise ProvsqlVerificationError(
                        "invalid SPARQL binding at %s:%d" % (path, line_number)
                    )
                name, term = item
                if (
                    not isinstance(name, str)
                    or not isinstance(term, list)
                    or len(term) < 2
                    or name in values
                ):
                    raise ProvsqlVerificationError(
                        "invalid SPARQL term at %s:%d" % (path, line_number)
                    )
                kind = term[0]
                if (
                    kind in ("iri", "bnode")
                    and len(term) == 2
                    and all(isinstance(value, str) for value in term)
                ):
                    lexical, datatype, language = term[1], "", ""
                elif (
                    kind == "literal"
                    and len(term) == 4
                    and all(isinstance(value, str) for value in term)
                ):
                    lexical, datatype, language = term[1], term[2], term[3]
                else:
                    raise ProvsqlVerificationError(
                        "invalid SPARQL term at %s:%d" % (path, line_number)
                    )
                values[name] = lexical
                row_signatures[name] = (kind, datatype, language)
                if name in signatures:
                    signatures[name].add((kind, datatype, language))
            if set(values) != set(columns):
                raise ProvsqlVerificationError(
                    "SPARQL columns differ at %s:%d" % (path, line_number)
                )
            key = _key(values, columns, row_signatures)
            before = connection.total_changes
            connection.execute(
                "INSERT OR IGNORE INTO sparql(binding, multiplicity) VALUES (?, ?)",
                (key, multiplicity),
            )
            if connection.total_changes == before:
                duplicate_bindings += 1
                connection.execute(
                    "UPDATE sparql SET multiplicity = multiplicity + ? WHERE binding = ?",
                    (multiplicity, key),
                )
            records += 1
            rows += multiplicity
    ambiguous = [column for column in columns if len(signatures[column]) > 1]
    if ambiguous:
        raise ProvsqlVerificationError(
            "SPARQL answer columns do not have one stable term type: %s"
            % ", ".join(ambiguous)
        )
    column_signatures = {
        column: (
            next(iter(signatures[column]))
            if signatures[column] else ("unknown", "", "")
        )
        for column in columns
    }
    return {
        "rows": rows,
        "distinct_bindings": int(
            connection.execute("SELECT count(*) FROM sparql").fetchone()[0]
        ),
        "answer_records": records,
        "duplicate_answer_records": duplicate_bindings,
        "serialized_bytes": path.stat().st_size,
        "term_signatures": {
            column: [list(value) for value in sorted(signatures[column])]
            for column in columns
        },
    }, column_signatures


def _load_roots(
    connection: sqlite3.Connection,
    path: Path,
    columns: Sequence[str],
    signatures: Optional[Mapping[str, Tuple[str, str, str]]] = None,
) -> Dict[str, int]:
    total = 0
    duplicate_bindings = 0
    malformed_tokens = 0
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        _require_columns(reader, (*columns, "provenance_root"), path)
        for row in reader:
            total += 1
            try:
                uuid.UUID(row["provenance_root"])
            except (ValueError, AttributeError):
                malformed_tokens += 1
            before = connection.total_changes
            connection.execute(
                "INSERT OR IGNORE INTO roots(binding, root) VALUES (?, ?)",
                (_key(row, columns, signatures), row["provenance_root"]),
            )
            duplicate_bindings += connection.total_changes == before
    distinct_roots = connection.execute("SELECT count(DISTINCT root) FROM roots").fetchone()[0]
    return {
        "rows": total,
        "duplicate_bindings": duplicate_bindings,
        "distinct_roots": int(distinct_roots),
        "malformed_root_tokens": malformed_tokens,
        "serialized_bytes": path.stat().st_size,
    }


def _load_probabilities(
    connection: sqlite3.Connection,
    path: Path,
    columns: Sequence[str],
    signatures: Optional[Mapping[str, Tuple[str, str, str]]] = None,
) -> Dict[str, int]:
    total = 0
    duplicate_bindings = 0
    invalid_probabilities = 0
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        _require_columns(reader, (*columns, "probability"), path)
        for row in reader:
            total += 1
            try:
                probability = float(row["probability"])
                if not math.isfinite(probability) or not 0.0 <= probability <= 1.0:
                    invalid_probabilities += 1
            except (TypeError, ValueError):
                probability = None
                invalid_probabilities += 1
            before = connection.total_changes
            connection.execute(
                "INSERT OR IGNORE INTO probabilities(binding, probability) VALUES (?, ?)",
                (_key(row, columns, signatures), probability),
            )
            duplicate_bindings += connection.total_changes == before
    return {
        "rows": total,
        "duplicate_bindings": duplicate_bindings,
        "invalid_probabilities": invalid_probabilities,
        "serialized_bytes": path.stat().st_size,
    }


def _difference(connection: sqlite3.Connection, left: str, right: str) -> int:
    value = connection.execute(
        "SELECT count(*) FROM (SELECT binding FROM %s EXCEPT SELECT binding FROM %s)"
        % (left, right)
    ).fetchone()[0]
    return int(value)


def _multiplicity_mismatches(connection: sqlite3.Connection) -> int:
    value = connection.execute(
        "SELECT count(*) FROM base JOIN sparql USING (binding) "
        "WHERE base.multiplicity <> sparql.multiplicity"
    ).fetchone()[0]
    return int(value)


def verify(
    manifest_path: Path,
    query_id: str,
    cells: Path,
    output: Path,
    scratch: Optional[Path] = None,
    sparql_records: Optional[Path] = None,
) -> Dict[str, Any]:
    manifest_path = manifest_path.resolve()
    cells = cells.resolve()
    output = output.resolve()
    provsql_workload.audit(manifest_path)
    manifest = _read_json(manifest_path)
    entry = _entry(manifest, query_id)
    columns = tuple(str(column) for column in entry["answer_columns"])
    pg_directory, _pg_cell = _cell(cells, entry, "PG-B")
    provsql_directory, _provsql_cell = _cell(cells, entry, "ProvSQL")
    base_path = pg_directory / "answers.csv"
    roots_path = provsql_directory / "roots.csv"
    probabilities_path = provsql_directory / "probabilities.csv"
    for path in (base_path, roots_path, probabilities_path):
        if not path.is_file():
            raise ProvsqlVerificationError("missing measured CSV: %s" % path)
    if sparql_records is not None:
        sparql_records = sparql_records.resolve()
        if not sparql_records.is_file():
            raise ProvsqlVerificationError(
                "missing SPARQL answer records: %s" % sparql_records
            )

    scratch_root = scratch.resolve() if scratch is not None else output.parent
    scratch_root.mkdir(parents=True, exist_ok=True)
    database = scratch_root / (output.name + ".sqlite.partial")
    if database.exists():
        raise ProvsqlVerificationError("refusing to reuse parity scratch database: %s" % database)
    connection = sqlite3.connect(str(database))
    try:
        connection.executescript("""
            PRAGMA journal_mode = OFF;
            PRAGMA synchronous = OFF;
            PRAGMA temp_store = FILE;
            CREATE TABLE base(binding TEXT PRIMARY KEY, multiplicity INTEGER NOT NULL);
            CREATE TABLE sparql(binding TEXT PRIMARY KEY, multiplicity INTEGER NOT NULL);
            CREATE TABLE roots(binding TEXT PRIMARY KEY, root TEXT NOT NULL);
            CREATE TABLE probabilities(binding TEXT PRIMARY KEY, probability REAL);
        """)
        sparql: Optional[Dict[str, Any]] = None
        signatures: Optional[Dict[str, Tuple[str, str, str]]] = None
        if sparql_records is not None:
            sparql, signatures = _load_sparql_records(
                connection, sparql_records, columns
            )
        base = _load_base(connection, base_path, columns, signatures)
        roots = _load_roots(connection, roots_path, columns, signatures)
        probabilities = _load_probabilities(
            connection, probabilities_path, columns, signatures
        )
        differences = {
            "base_minus_roots": _difference(connection, "base", "roots"),
            "roots_minus_base": _difference(connection, "roots", "base"),
            "roots_minus_probabilities": _difference(
                connection, "roots", "probabilities"
            ),
            "probabilities_minus_roots": _difference(
                connection, "probabilities", "roots"
            ),
        }
        sparql_differences = (
            {
                "pg_b_minus_sparql_b": _difference(connection, "base", "sparql"),
                "sparql_b_minus_pg_b": _difference(connection, "sparql", "base"),
                "multiplicity_mismatches": _multiplicity_mismatches(connection),
            }
            if sparql is not None else None
        )
    finally:
        connection.close()
        try:
            database.unlink()
        except FileNotFoundError:
            pass

    ok = (
        all(value == 0 for value in differences.values())
        and roots["duplicate_bindings"] == 0
        and roots["malformed_root_tokens"] == 0
        and probabilities["duplicate_bindings"] == 0
        and probabilities["invalid_probabilities"] == 0
        and (
            sparql is None
            or (
                sparql["duplicate_answer_records"] == 0
                and all(value == 0 for value in sparql_differences.values())
            )
        )
    )
    result = {
        "schema": SCHEMA,
        "status": "ok" if ok else "failed",
        "query_id": query_id,
        "template": entry["template"],
        "instance": entry["instance"],
        "scale_factor": entry["scale_factor"],
        "answer_columns": list(columns),
        "pg_b": base,
        "provsql_roots": roots,
        "provsql_probabilities": probabilities,
        "binding_set_differences": differences,
        "comparison": (
            "SPARQL-B and PG-B bags agree; distinct PG-B bindings equal ProvSQL "
            "roots and probability rows"
            if sparql is not None
            else "distinct PG-B answer bindings equal ProvSQL roots and probability rows"
        ),
    }
    if sparql is not None:
        result["sparql_b"] = sparql
        result["sparql_bag_differences"] = sparql_differences
    _write_json(output, result)
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--query-id", required=True)
    parser.add_argument("--cells", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--scratch", type=Path)
    parser.add_argument(
        "--sparql-answer-records",
        type=Path,
        help="optional B-stage answer-records.jsonl for exact bag parity",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        result = verify(
            args.manifest, args.query_id, args.cells, args.out, args.scratch,
            args.sparql_answer_records,
        )
    except (OSError, ValueError, sqlite3.Error, ProvsqlVerificationError,
            provsql_workload.ProvsqlWorkloadError) as error:
        parser.exit(2, "ProvSQL parity: error: %s\n" % error)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
