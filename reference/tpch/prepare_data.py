#!/usr/bin/env python3
"""Generate and direct-map one TPC-H scale into the experiment data layouts.

The base layout is the SPARQLprov-compatible RDF graph emitted by
``tbl_to_rdf.py``.  The mixed layout contains that graph unchanged and adds
one legacy RDF-star quoted-triple occurrence statement per relational row.
RDF 1.2 triple terms and ``rdf:reifies`` are deliberately outside this
experiment profile.
"""

import argparse
from decimal import Decimal, InvalidOperation
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any, Dict, Optional, Sequence


HERE = Path(__file__).resolve().parent
REFERENCE = HERE.parent
if str(REFERENCE) not in sys.path:
    sys.path.insert(0, str(REFERENCE))

from tpch import reify_rows, tbl_to_rdf


SCHEMA = "tpch-rdf-star11-data-v1"
TABLES = tuple(tbl_to_rdf.SCHEMA)


class PreparationError(RuntimeError):
    """A TPC-H dataset could not be prepared without ambiguity."""


def _write_text(path: Path, value: str) -> None:
    if path.exists():
        raise PreparationError("refusing to overwrite %s" % path)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(value)
        handle.flush()
        os.fsync(handle.fileno())


def _write_json(path: Path, value: Any) -> None:
    _write_text(path, json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def _require_tables(directory: Path) -> None:
    missing = [name for name in TABLES if not (directory / (name + ".tbl")).is_file()]
    empty = [
        name for name in TABLES
        if (directory / (name + ".tbl")).is_file()
        and (directory / (name + ".tbl")).stat().st_size == 0
    ]
    if missing or empty:
        detail = []
        if missing:
            detail.append("missing=" + ",".join(missing))
        if empty:
            detail.append("empty=" + ",".join(empty))
        raise PreparationError("incomplete TPC-H table set (%s)" % "; ".join(detail))


def _run_dbgen(dbgen: Path, dbgen_directory: Path, scale: str, table_directory: Path) -> Dict[str, Any]:
    environment = dict(os.environ)
    environment["DSS_CONFIG"] = str(dbgen_directory.resolve())
    environment["DSS_PATH"] = str(table_directory.resolve())
    command = [str(dbgen.resolve()), "-f", "-s", scale]
    completed = subprocess.run(
        command,
        cwd=str(table_directory.resolve()),
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    _write_text(table_directory.parent / "dbgen.stdout", completed.stdout)
    _write_text(table_directory.parent / "dbgen.stderr", completed.stderr)
    if completed.returncode != 0:
        raise PreparationError(
            "dbgen failed with exit code %d: %s"
            % (completed.returncode, completed.stderr[-1000:].strip())
        )
    return {
        "argv": [dbgen.name, "-f", "-s", scale],
        "exit_code": completed.returncode,
        "stdout": "dbgen.stdout",
        "stderr": "dbgen.stderr",
    }


def prepare(
    output: Path,
    scale: str,
    tpch_version: str,
    dbgen: Path,
    dbgen_directory: Path,
) -> Dict[str, Any]:
    """Create one immutable scale directory and return its metadata."""
    try:
        numeric_scale = Decimal(scale)
    except InvalidOperation as error:
        raise PreparationError("invalid TPC-H scale factor: %s" % scale) from error
    if not numeric_scale.is_finite() or numeric_scale <= 0:
        raise PreparationError("TPC-H scale factor must be positive and finite: %s" % scale)

    final = output.resolve()
    partial = final.with_name(final.name + ".partial")
    if final.exists() or partial.exists():
        raise PreparationError("refusing to reuse output or partial directory: %s" % final)
    if not dbgen.is_file():
        raise PreparationError("dbgen executable does not exist: %s" % dbgen)
    if not dbgen_directory.is_dir():
        raise PreparationError("dbgen directory does not exist: %s" % dbgen_directory)

    partial.mkdir(parents=True)
    table_directory = partial / "tbl"
    table_directory.mkdir()
    dbgen_record = _run_dbgen(dbgen, dbgen_directory, scale, table_directory)
    _require_tables(table_directory)

    base_path = partial / "base.nt"
    mixed_path = partial / "mixed-rdfstar11.ttls"
    base_count = tbl_to_rdf.convert(str(table_directory), str(base_path))
    copied_count, row_count, mixed_count = reify_rows.reify_rows(base_path, mixed_path)
    if copied_count != base_count:
        raise PreparationError(
            "base statement count changed during reification: %d != %d"
            % (copied_count, base_count)
        )

    metadata = {
        "schema": SCHEMA,
        "tpch_version": tpch_version,
        "scale_factor": scale,
        "fractional_scale_factor": numeric_scale < Decimal("1"),
        "mapping": "SPARQLprov-compatible direct RDF mapping",
        "provenance_granularity": "one token per relational row",
        "row_marker": "row rdf:type Table",
        "rdf_star_profile": "RDF-star 1.1 quoted triple plus occurrenceOf",
        "rdf_star_12_permitted": False,
        "layouts": {
            "base": {
                "path": "base.nt",
                "statement_count": base_count,
                "bytes": base_path.stat().st_size,
            },
            "mixed": {
                "path": "mixed-rdfstar11.ttls",
                "asserted_statement_count": copied_count,
                "row_occurrence_statement_count": row_count,
                "physical_statement_count": mixed_count,
                "bytes": mixed_path.stat().st_size,
            },
        },
        "tables": {
            name: {
                "path": "tbl/%s.tbl" % name,
                "bytes": (table_directory / (name + ".tbl")).stat().st_size,
            }
            for name in TABLES
        },
        "dbgen": dbgen_record,
    }
    _write_json(partial / "dataset.json", metadata)
    audit(partial / "dataset.json", scan_mixed=True)
    os.replace(partial, final)
    return metadata


def audit(metadata_path: Path, scan_mixed: bool = True) -> Dict[str, Any]:
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    if metadata.get("schema") != SCHEMA:
        raise PreparationError("unsupported dataset metadata schema")
    if metadata.get("rdf_star_12_permitted") is not False:
        raise PreparationError("dataset does not prohibit RDF 1.2 reification")
    if metadata.get("rdf_star_profile") != "RDF-star 1.1 quoted triple plus occurrenceOf":
        raise PreparationError("dataset does not declare the RDF-star 1.1 profile")
    if metadata.get("provenance_granularity") != "one token per relational row":
        raise PreparationError("dataset does not use per-row provenance")
    root = metadata_path.parent
    base_path = root / metadata["layouts"]["base"]["path"]
    mixed_path = root / metadata["layouts"]["mixed"]["path"]
    for path in (base_path, mixed_path):
        if not path.is_file() or path.stat().st_size == 0:
            raise PreparationError("dataset artifact is missing or empty: %s" % path)

    expected = int(metadata["layouts"]["mixed"]["row_occurrence_statement_count"])
    occurrence_count = None
    if scan_mixed:
        occurrence_count = 0
        with mixed_path.open(encoding="utf-8") as source:
            for line_number, line in enumerate(source, 1):
                if "rdf:reifies" in line or "rdf-syntax-ns#reifies" in line or "<<(" in line:
                    raise PreparationError("RDF 1.2 reification at mixed line %d" % line_number)
                if "<%s>" % reify_rows.OCCURRENCE_OF in line:
                    if not line.startswith("<< ") or " >> " not in line:
                        raise PreparationError("non-legacy occurrence syntax at line %d" % line_number)
                    occurrence_count += 1
        if occurrence_count != expected:
            raise PreparationError(
                "mixed layout has %d row occurrences; expected %d" % (occurrence_count, expected)
            )
    if base_path.stat().st_size != int(metadata["layouts"]["base"]["bytes"]):
        raise PreparationError("base layout size differs from dataset metadata")
    if mixed_path.stat().st_size != int(metadata["layouts"]["mixed"]["bytes"]):
        raise PreparationError("mixed layout size differs from dataset metadata")
    return {
        "status": "ok",
        "scale_factor": metadata["scale_factor"],
        "row_occurrence_statement_count": expected,
        "mixed_content_scanned": scan_mixed,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    subparsers = parser.add_subparsers(dest="command", required=True)
    generate = subparsers.add_parser("generate", help="run dbgen and create base and mixed layouts")
    generate.add_argument("--dbgen", required=True, type=Path)
    generate.add_argument("--dbgen-dir", required=True, type=Path)
    generate.add_argument("--scale", required=True)
    generate.add_argument("--out", required=True, type=Path)
    generate.add_argument("--tpch-version", default="3.0.1")
    check = subparsers.add_parser("audit", help="audit an existing prepared scale")
    check.add_argument("metadata", type=Path)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "generate":
            result = prepare(
                args.out, args.scale, args.tpch_version, args.dbgen, args.dbgen_dir
            )
            summary = {
                "status": "ok",
                "scale_factor": result["scale_factor"],
                "base_statements": result["layouts"]["base"]["statement_count"],
                "row_occurrences": result["layouts"]["mixed"]["row_occurrence_statement_count"],
            }
        else:
            summary = audit(args.metadata.resolve())
    except (OSError, ValueError, PreparationError) as error:
        parser.exit(1, "tpch data: error: %s\n" % error)
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
