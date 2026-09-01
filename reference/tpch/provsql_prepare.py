#!/usr/bin/env python3
"""Load a prepared TPC-H scale into PostgreSQL and enable ProvSQL per row."""

import argparse
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple


HERE = Path(__file__).resolve().parent
REFERENCE = HERE.parent
if str(REFERENCE) not in sys.path:
    sys.path.insert(0, str(REFERENCE))

import event_probabilities
from tpch import prepare_data, tbl_to_rdf


SCHEMA = "tpch-provsql-data-v2"
POSTGRESQL_VERSION = "18.4"
DDL = HERE / "provsql_schema.sql"
TABLES = tuple(tbl_to_rdf.SCHEMA)
PRIMARY_KEYS = {
    "part": ("p_partkey",),
    "region": ("r_regionkey",),
    "nation": ("n_nationkey",),
    "supplier": ("s_suppkey",),
    "partsupp": ("ps_partkey", "ps_suppkey"),
    "customer": ("c_custkey",),
    "orders": ("o_orderkey",),
    "lineitem": ("l_orderkey", "l_linenumber"),
}


class ProvsqlPreparationError(RuntimeError):
    """The relational ProvSQL dataset cannot be prepared safely."""


def _identifier(value: str) -> str:
    if not re.fullmatch(r"[a-z][a-z0-9_]*", value):
        raise ProvsqlPreparationError("unsafe PostgreSQL identifier: %r" % value)
    return value


def _psql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def row_event_expression(table: str, alias: str = "t") -> str:
    """Return SQL that reconstructs the RDF row IRI for one tuple."""
    if table not in PRIMARY_KEYS:
        raise ProvsqlPreparationError("unknown TPC-H table: %s" % table)
    alias = _identifier(alias)
    prefix = tbl_to_rdf.BASE + tbl_to_rdf.ENTITY[table] + "/"
    key_parts = ["%s.%s::text" % (alias, column) for column in PRIMARY_KEYS[table]]
    key_expression = " || '/' || ".join(key_parts)
    return "%s || %s" % (_psql_literal(prefix), key_expression)


def probability_expression(event_expression: str, seed: int) -> str:
    """Return PostgreSQL SQL matching ``event_probability`` exactly."""
    event_probabilities.validate_seed(seed)
    prefix = "%s|%d|" % (event_probabilities.PROBABILITY_DOMAIN, seed)
    hexadecimal = "substr(md5(%s || %s), 1, 13)" % (
        _psql_literal(prefix), event_expression
    )
    return (
        "((('x' || %s)::bit(52)::bigint::double precision + 0.5) "
        "/ 4503599627370496.0)" % hexadecimal
    )


def probability_assignment_sql(table: str, relation: str, seed: int) -> str:
    """Assign deterministic row-event probabilities to one ProvSQL table."""
    expression = probability_expression(row_event_expression(table), seed)
    return "PERFORM provsql.set_prob(t.provsql, %s) FROM %s AS t;" % (
        expression, relation
    )


def _write_text(path: Path, value: str) -> None:
    if path.exists():
        raise ProvsqlPreparationError("refusing to overwrite %s" % path)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(value)
        handle.flush()
        os.fsync(handle.fileno())


def _write_json(path: Path, value: Any) -> None:
    _write_text(
        path,
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )


def load_sql(
    dataset_path: Path,
    database_schema: str,
    probability_seed: int = event_probabilities.DEFAULT_PROBABILITY_SEED,
) -> str:
    """Return a psql script that streams the existing ``.tbl`` files once."""
    database_schema = _identifier(database_schema)
    event_probabilities.validate_seed(probability_seed)
    metadata = json.loads(dataset_path.read_text(encoding="utf-8"))
    root = dataset_path.parent
    lines = [
        r"\set ON_ERROR_STOP on",
        "SET client_min_messages TO warning;",
        "CREATE EXTENSION IF NOT EXISTS provsql CASCADE;",
        "DO $$",
        "BEGIN",
        "  IF EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = %s) THEN"
        % _psql_literal(database_schema),
        "    RAISE EXCEPTION 'schema %% already exists', %s;"
        % _psql_literal(database_schema),
        "  END IF;",
        "END",
        "$$;",
        'CREATE SCHEMA "%s";' % database_schema,
        'SET search_path TO "%s", public, provsql;' % database_schema,
        DDL.read_text(encoding="utf-8").rstrip(),
    ]
    for table in TABLES:
        columns = list(tbl_to_rdf.SCHEMA[table][0]) + ["_trailer"]
        table_path = (root / metadata["tables"][table]["path"]).resolve()
        lines.append(
            r"\copy %s (%s) FROM %s WITH (FORMAT csv, DELIMITER '|', NULL '')"
            % (table, ", ".join(columns), _psql_literal(str(table_path)))
        )
    lines.extend((
        "DO $$",
        "DECLARE bad_rows bigint;",
        "BEGIN",
    ))
    for table in TABLES:
        lines.extend((
            "  SELECT count(*) INTO bad_rows FROM %s WHERE _trailer IS NOT NULL;"
            % table,
            "  IF bad_rows <> 0 THEN",
            "    RAISE EXCEPTION '%% has non-empty trailing TPC-H fields', %s;"
            % _psql_literal(table),
            "  END IF;",
        ))
    lines.extend(("END", "$$;"))
    for table in TABLES:
        lines.append("ALTER TABLE %s DROP COLUMN _trailer;" % table)
    for table in TABLES:
        lines.append(
            "ALTER TABLE %s ADD PRIMARY KEY (%s);"
            % (table, ", ".join(PRIMARY_KEYS[table]))
        )
    lines.append("ANALYZE;")
    for table in TABLES:
        lines.append(
            "SELECT provsql.add_provenance(%s);"
            % _psql_literal("%s.%s" % (database_schema, table))
        )
    lines.extend(("DO $$", "BEGIN"))
    for table in TABLES:
        lines.append(
            "  " + probability_assignment_sql(table, table, probability_seed)
        )
    lines.extend(("END", "$$;", "ANALYZE;", ""))
    return "\n".join(lines)


def _psql_command(
    psql: Path,
    psql_args: Sequence[str],
    dsn: str,
    *arguments: str,
) -> List[str]:
    return [str(psql), *psql_args, "-X", "--dbname", dsn, *arguments]


def _run_psql(
    psql: Path,
    psql_args: Sequence[str],
    dsn: str,
    arguments: Sequence[str],
    timeout: float,
    stdout_path: Path,
    stderr_path: Path,
) -> Tuple[int, float]:
    started = time.perf_counter()
    with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
        try:
            completed = subprocess.run(
                _psql_command(psql, psql_args, dsn, *arguments),
                stdout=stdout,
                stderr=stderr,
                check=False,
                timeout=timeout,
            )
            return completed.returncode, (time.perf_counter() - started) * 1000.0
        except subprocess.TimeoutExpired:
            return 124, (time.perf_counter() - started) * 1000.0


def _inventory_sql(database_schema: str) -> str:
    database_schema = _identifier(database_schema)
    selects = [
        "SELECT %s AS table_name, count(*)::text AS value FROM \"%s\".%s"
        % (_psql_literal(table), database_schema, table)
        for table in TABLES
    ]
    return (
        "SET provsql.active = off;\n"
        "SELECT 'postgresql_version', current_setting('server_version')\n"
        "UNION ALL SELECT 'provsql_version', extversion FROM pg_extension "
        "WHERE extname = 'provsql'\n"
        "UNION ALL SELECT 'materialized_gates', provsql.get_nb_gates()::text\n"
        "UNION ALL " + "\nUNION ALL ".join(selects) + ";\n"
    )


def _parse_inventory(path: Path) -> Dict[str, str]:
    values: Dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line:
            continue
        fields = line.split("\t", 1)
        if len(fields) != 2:
            raise ProvsqlPreparationError("invalid psql inventory row: %r" % line)
        values[fields[0]] = fields[1]
    required = {
        "postgresql_version", "provsql_version", "materialized_gates", *TABLES
    }
    missing = required.difference(values)
    if missing:
        raise ProvsqlPreparationError(
            "psql inventory is missing: %s" % ", ".join(sorted(missing))
        )
    return values


def load(
    dataset_path: Path,
    output: Path,
    database_schema: str,
    psql: Path,
    psql_args: Sequence[str],
    dsn: str,
    timeout: float,
    probability_seed: int = event_probabilities.DEFAULT_PROBABILITY_SEED,
) -> Dict[str, Any]:
    dataset_path = dataset_path.resolve()
    output = output.resolve()
    database_schema = _identifier(database_schema)
    event_probabilities.validate_seed(probability_seed)
    if output.exists() or output.with_name(output.name + ".partial").exists():
        raise ProvsqlPreparationError("refusing to reuse output directory: %s" % output)
    if not psql.is_file():
        raise ProvsqlPreparationError("psql executable does not exist: %s" % psql)
    try:
        prepare_data.audit(dataset_path, scan_mixed=False)
    except (OSError, ValueError, prepare_data.PreparationError) as error:
        raise ProvsqlPreparationError("invalid source dataset: %s" % error) from error
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    root = dataset_path.parent
    for table in TABLES:
        table_path = root / dataset["tables"][table]["path"]
        if not table_path.is_file() or table_path.stat().st_size == 0:
            raise ProvsqlPreparationError("missing TPC-H table: %s" % table_path)

    partial = output.with_name(output.name + ".partial")
    partial.mkdir(parents=True)
    script_path = partial / "load.sql"
    _write_text(
        script_path, load_sql(dataset_path, database_schema, probability_seed)
    )
    return_code, wall_ms = _run_psql(
        psql, psql_args, dsn,
        ("-v", "ON_ERROR_STOP=1", "-f", str(script_path)), timeout,
        partial / "load.stdout", partial / "load.stderr",
    )
    if return_code != 0:
        raise ProvsqlPreparationError(
            "psql load failed with exit code %d; diagnostics remain in %s"
            % (return_code, partial)
        )

    inventory_sql = _inventory_sql(database_schema)
    inventory_path = partial / "inventory.tsv"
    inventory_error = partial / "inventory.stderr"
    return_code, inventory_wall_ms = _run_psql(
        psql, psql_args, dsn,
        (
            "-q", "-v", "ON_ERROR_STOP=1", "-A", "-t", "-F", "\t", "-c",
            inventory_sql,
        ),
        min(timeout, 600.0), inventory_path, inventory_error,
    )
    if return_code != 0:
        raise ProvsqlPreparationError(
            "psql inventory failed with exit code %d; diagnostics remain in %s"
            % (return_code, partial)
        )
    inventory = _parse_inventory(inventory_path)
    postgresql_version = inventory.pop("postgresql_version")
    if postgresql_version.split()[0] != POSTGRESQL_VERSION:
        raise ProvsqlPreparationError(
            "formal ProvSQL runs require PostgreSQL %s, found %s"
            % (POSTGRESQL_VERSION, postgresql_version)
        )
    result = {
        "schema": SCHEMA,
        "status": "ok",
        "source_dataset": str(dataset_path),
        "tpch_version": dataset["tpch_version"],
        "scale_factor": dataset["scale_factor"],
        "database_schema": database_schema,
        "postgresql_version": postgresql_version,
        "provsql_version": inventory.pop("provsql_version"),
        "materialized_gates_after_load": int(inventory.pop("materialized_gates")),
        "provenance_granularity": "one ProvSQL input token per TPC-H row",
        "probability_seed": probability_seed,
        "probability_scheme": event_probabilities.PROBABILITY_SCHEME,
        "indexes": "TPC-H primary keys only",
        "load_wall_ms": wall_ms,
        "inventory_wall_ms": inventory_wall_ms,
        "row_counts": {table: int(inventory[table]) for table in TABLES},
        "connection": "provided at runtime and deliberately not recorded",
    }
    _write_json(partial / "provsql-dataset.json", result)
    os.replace(partial, output)
    return result


def _positive(value: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("value must be positive and finite")
    return number


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--schema", required=True, dest="database_schema")
    parser.add_argument("--psql", required=True, type=Path)
    parser.add_argument(
        "--psql-arg", dest="psql_args", action="append", default=[],
        help="argument inserted between the psql launcher and normal psql options",
    )
    parser.add_argument("--dsn", required=True)
    parser.add_argument("--timeout", type=_positive, default=7200.0)
    parser.add_argument(
        "--probability-seed",
        type=int,
        default=event_probabilities.DEFAULT_PROBABILITY_SEED,
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        result = load(
            args.dataset, args.out, args.database_schema, args.psql.resolve(),
            tuple(args.psql_args), args.dsn, args.timeout, args.probability_seed,
        )
    except (OSError, ValueError, ProvsqlPreparationError) as error:
        parser.exit(1, "ProvSQL data: error: %s\n" % error)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
