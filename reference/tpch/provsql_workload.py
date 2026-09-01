#!/usr/bin/env python3
"""Derive an auditable relational ProvSQL workload from a frozen TPC-H manifest."""

import argparse
import json
import os
from pathlib import Path
import re
import sys
import textwrap
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple


HERE = Path(__file__).resolve().parent
REFERENCE = HERE.parent
if str(REFERENCE) not in sys.path:
    sys.path.insert(0, str(REFERENCE))

from tpch import workload


SCHEMA = "tpch-provsql-workload-v1"
DEFAULT_TEMPLATES = HERE / "templates" / "provsql_non_aggregate"
OUTPUT_COLUMNS = {
    1: ("l_returnflag", "l_linestatus"),
    3: ("order", "o_orderdate", "o_shippriority"),
    4: ("order", "o_orderpriority"),
    5: ("n_name",),
    6: ("x",),
    7: ("supp_nation", "cust_nation", "l_year"),
    8: ("o_year",),
    10: (
        "customer", "c_name", "c_acctbal", "n_name", "c_address",
        "c_phone", "c_comment",
    ),
    12: ("l_shipmode",),
    14: ("x",),
    15: ("supplier", "lineitem"),
    19: ("x",),
}


class ProvsqlWorkloadError(RuntimeError):
    """The relational workload cannot be generated or audited safely."""


def _write_text(path: Path, value: str) -> None:
    if path.exists():
        raise ProvsqlWorkloadError("refusing to overwrite %s" % path)
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".partial")
    with partial.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(value)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(partial, path)


def _write_json(path: Path, value: Any) -> None:
    _write_text(
        path,
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )


def _quoted_identifier(value: str) -> str:
    if not re.fullmatch(r"[a-z][a-z0-9_]*", value):
        raise ProvsqlWorkloadError("unsafe SQL output identifier: %r" % value)
    return '"%s"' % value


def answer_query(base_sql: str, output_columns: Sequence[str]) -> str:
    """Group derivations into one provenance-bearing row per answer binding."""
    body = base_sql.strip()
    if body.endswith(";"):
        body = body[:-1].rstrip()
    if not body:
        raise ProvsqlWorkloadError("empty ProvSQL base query")
    columns = tuple(output_columns)
    if not columns:
        raise ProvsqlWorkloadError("ProvSQL query must project at least one answer column")
    selected = ",\n  ".join(
        "d.%s AS %s" % (_quoted_identifier(column), _quoted_identifier(column))
        for column in columns
    )
    grouped = ", ".join("d.%s" % _quoted_identifier(column) for column in columns)
    return (
        "SELECT\n  %s\nFROM (\n%s\n) AS d\nGROUP BY %s;\n"
        % (selected, textwrap.indent(body, "  "), grouped)
    )


def _template_id(entry: Mapping[str, Any]) -> int:
    match = re.fullmatch(r"Q(\d{2})", str(entry.get("template", "")))
    if not match:
        raise ProvsqlWorkloadError(
            "invalid source template identifier: %r" % entry.get("template")
        )
    query_id = int(match.group(1))
    if query_id not in OUTPUT_COLUMNS:
        raise ProvsqlWorkloadError("unsupported ProvSQL template Q%02d" % query_id)
    return query_id


def _instantiate(
    templates: Path,
    entry: Mapping[str, Any],
) -> Tuple[str, str, Tuple[str, ...]]:
    query_id = _template_id(entry)
    template_path = templates / ("Q%02d.sql" % query_id)
    if not template_path.is_file():
        raise ProvsqlWorkloadError("missing ProvSQL SQL template: %s" % template_path)
    parameters = entry.get("parameters")
    if not isinstance(parameters, list) or not all(
        isinstance(parameter, str) for parameter in parameters
    ):
        raise ProvsqlWorkloadError(
            "%s has invalid qgen parameters" % entry.get("query_id")
        )
    try:
        base_sql, corrections = workload.instantiate(
            template_path.read_text(encoding="utf-8"), query_id, parameters
        )
    except workload.WorkloadError as error:
        raise ProvsqlWorkloadError(str(error)) from error
    expected_corrections = entry.get("artifact_corrections", [])
    if corrections != expected_corrections:
        raise ProvsqlWorkloadError(
            "%s correction metadata differs between SPARQL and SQL"
            % entry.get("query_id")
        )
    columns = OUTPUT_COLUMNS[query_id]
    return base_sql, answer_query(base_sql, columns), columns


def generate(
    source_manifest: Path,
    output: Path,
    templates: Path = DEFAULT_TEMPLATES,
    instances: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    source_manifest = source_manifest.resolve()
    output = output.resolve()
    templates = templates.resolve()
    if output.exists():
        raise ProvsqlWorkloadError("refusing to reuse workload directory: %s" % output)
    try:
        workload.audit_manifest(source_manifest)
    except (OSError, ValueError, workload.WorkloadError) as error:
        raise ProvsqlWorkloadError("invalid source workload: %s" % error) from error
    source = json.loads(source_manifest.read_text(encoding="utf-8"))
    selected_instances = set(instances or ())
    entries = [
        entry for entry in source["entries"]
        if not selected_instances or entry.get("instance") in selected_instances
    ]
    if not entries:
        raise ProvsqlWorkloadError("no source queries match the requested instances")
    unknown_instances = selected_instances.difference(
        str(entry.get("instance")) for entry in source["entries"]
    )
    if unknown_instances:
        raise ProvsqlWorkloadError(
            "unknown source instances: %s" % ", ".join(sorted(unknown_instances))
        )

    output.mkdir(parents=True)
    generated: List[Dict[str, Any]] = []
    for entry in entries:
        base_sql, grouped_sql, columns = _instantiate(templates, entry)
        query_directory = (
            output
            / ("sf" + str(entry["scale_factor"]).replace(".", "p"))
            / str(entry["template"])
            / str(entry["instance"])
        )
        base_path = query_directory / "base.sql"
        answer_path = query_directory / "answers.sql"
        _write_text(base_path, base_sql)
        _write_text(answer_path, grouped_sql)
        generated.append({
            "query_id": entry["query_id"],
            "scale_factor": entry["scale_factor"],
            "template": entry["template"],
            "instance": entry["instance"],
            "seed": entry["seed"],
            "parameters": entry["parameters"],
            "artifact_corrections": entry.get("artifact_corrections", []),
            "answer_columns": list(columns),
            "base_sql": str(base_path.relative_to(output)),
            "answer_sql": str(answer_path.relative_to(output)),
            "source_base_query": entry["base_query"],
        })

    result = {
        "schema": SCHEMA,
        "source_workload_manifest": str(source_manifest),
        "tpch_version": source["tpch_version"],
        "scale_factors": sorted(
            {str(entry["scale_factor"]) for entry in generated},
            key=lambda value: float(value),
        ),
        "templates": ["Q%02d" % query_id for query_id in workload.TEMPLATE_IDS],
        "instances": sorted({str(entry["instance"]) for entry in generated}),
        "provenance_granularity": "one ProvSQL input token per TPC-H row",
        "answer_semantics": (
            "one GROUP BY row and one provenance root per distinct projected binding"
        ),
        "query_language": "PostgreSQL SQL accepted by the ProvSQL planner hook",
        "entries": generated,
    }
    _write_json(output / "manifest.json", result)
    audit(output / "manifest.json", templates=templates)
    return result


def audit(path: Path, templates: Path = DEFAULT_TEMPLATES) -> Dict[str, Any]:
    path = path.resolve()
    templates = templates.resolve()
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("schema") != SCHEMA:
        raise ProvsqlWorkloadError(
            "unsupported ProvSQL workload schema: %r" % value.get("schema")
        )
    entries = value.get("entries")
    if not isinstance(entries, list) or not entries:
        raise ProvsqlWorkloadError("ProvSQL workload entries must be a non-empty list")
    root = path.parent
    query_ids = set()
    for entry in entries:
        query_id = str(entry.get("query_id"))
        if query_id in query_ids:
            raise ProvsqlWorkloadError("duplicate ProvSQL query id: %s" % query_id)
        query_ids.add(query_id)
        base_path = root / str(entry.get("base_sql"))
        answer_path = root / str(entry.get("answer_sql"))
        if not base_path.is_file() or not answer_path.is_file():
            raise ProvsqlWorkloadError("missing ProvSQL SQL artifact for %s" % query_id)
        expected_base, expected_answer, columns = _instantiate(templates, entry)
        if base_path.read_text(encoding="utf-8") != expected_base:
            raise ProvsqlWorkloadError("frozen ProvSQL base SQL differs for %s" % query_id)
        if answer_path.read_text(encoding="utf-8") != expected_answer:
            raise ProvsqlWorkloadError("frozen ProvSQL answer SQL differs for %s" % query_id)
        if entry.get("answer_columns") != list(columns):
            raise ProvsqlWorkloadError("answer columns differ for %s" % query_id)
    return {
        "status": "ok",
        "entry_count": len(entries),
        "query_ids": len(query_ids),
        "instances": value.get("instances"),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    subparsers = parser.add_subparsers(dest="command", required=True)
    generate_parser = subparsers.add_parser(
        "generate", help="derive frozen ProvSQL SQL from a SPARQL workload"
    )
    generate_parser.add_argument("--manifest", required=True, type=Path)
    generate_parser.add_argument("--out", required=True, type=Path)
    generate_parser.add_argument("--templates", type=Path, default=DEFAULT_TEMPLATES)
    generate_parser.add_argument(
        "--instance", dest="instances", action="append",
        help="include only this frozen instance; repeat to select several",
    )
    audit_parser = subparsers.add_parser("audit", help="audit a derived ProvSQL workload")
    audit_parser.add_argument("manifest", type=Path)
    audit_parser.add_argument("--templates", type=Path, default=DEFAULT_TEMPLATES)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "generate":
            result = generate(
                args.manifest, args.out, templates=args.templates,
                instances=args.instances,
            )
            summary = {"status": "ok", "entries": len(result["entries"])}
        else:
            summary = audit(args.manifest, templates=args.templates)
    except (OSError, ValueError, ProvsqlWorkloadError) as error:
        parser.exit(1, "ProvSQL workload: error: %s\n" % error)
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
