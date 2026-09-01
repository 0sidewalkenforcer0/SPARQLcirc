#!/usr/bin/env python3
"""Generate and audit frozen TPC-H non-aggregate SPARQL workloads."""

import argparse
from datetime import date, timedelta
from decimal import Decimal
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

HERE = Path(__file__).resolve().parent
REFERENCE = HERE.parent
if str(REFERENCE) not in sys.path:
    sys.path.insert(0, str(REFERENCE))

from tpch import inline_rows, sparqlprov_rows


DEFAULT_TEMPLATES = HERE / "templates" / "non_aggregate"
DEFAULT_PARAMETER_TEMPLATES = HERE / "qgen_params"
TEMPLATE_IDS = (1, 3, 4, 5, 6, 7, 8, 10, 12, 14, 15, 19)
RELEASED_IDS = frozenset((1, 3, 5, 6, 7, 8, 10, 12, 14, 19))
PARAMETER_COUNTS = {
    1: 1, 3: 2, 4: 1, 5: 2, 6: 3, 7: 2, 8: 3,
    10: 1, 12: 3, 14: 1, 15: 1, 19: 6,
}
# The formal scaling sweep includes the lower endpoint and the eight
# logarithmically spaced points above it:
# SF_i = 10 ** (i / 4 - 2), i = 0, ..., 8.
# Decimal strings keep dbgen/qgen invocations and manifest identities stable.
PAPER_SCALE_FACTORS = (
    "0.01",
    "0.0177827941003892",
    "0.0316227766016838",
    "0.0562341325190349",
    "0.1",
    "0.177827941003892",
    "0.316227766016838",
    "0.562341325190349",
    "1",
)
DEFAULT_SCALES = PAPER_SCALE_FACTORS
DEFAULT_SEEDS = (1,)
DEFAULT_INSTANCES = ("q001",)
DEFAULT_ENGINES = ("graphdb", "oxigraph")
DEFAULT_METHODS = ("B", "R", "P", "N", "C-flat", "C-factorised")
SCHEMA = "tpch-nonaggregate-workload-v1"
SHARD_SCHEMA = "tpch-brnc-shards-v1"


class WorkloadError(RuntimeError):
    """The workload cannot be generated or audited safely."""


def _atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise WorkloadError("refusing to overwrite %s" % path)
    partial = path.with_name(path.name + ".partial")
    with partial.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(value)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(partial, path)


def _atomic_json(path: Path, value: Any) -> None:
    _atomic_text(path, json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def _scale_slug(scale: str) -> str:
    return "sf" + scale.replace(".", "p")


def _add_months(value: date, months: int) -> date:
    month_index = value.year * 12 + value.month - 1 + months
    year, month_zero = divmod(month_index, 12)
    month = month_zero + 1
    month_lengths = (31, 29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28,
                     31, 30, 31, 30, 31, 31, 30, 31, 30, 31)
    return date(year, month, min(value.day, month_lengths[month - 1]))


def _replace(text: str, old: str, new: str, query_id: int) -> str:
    if old not in text:
        raise WorkloadError("Q%02d template is missing instantiation marker %r" % (query_id, old))
    return text.replace(old, new)


def instantiate(template: str, query_id: int, parameters: Sequence[str]) -> Tuple[str, List[str]]:
    """Apply qgen parameters and return the query plus recorded artifact corrections."""
    expected = PARAMETER_COUNTS[query_id]
    if len(parameters) != expected:
        raise WorkloadError(
            "Q%02d expected %d qgen parameters, got %d" %
            (query_id, expected, len(parameters))
        )
    p = list(parameters)
    corrections: List[str] = []
    query = template

    if query_id == 1:
        cutoff = date(1998, 12, 1) - timedelta(days=int(p[0]))
        query = _replace(query, "1998-09-24", cutoff.isoformat(), query_id)
    elif query_id == 3:
        query = _replace(query, "FURNITURE", p[0], query_id)
        query = _replace(query, "1995-03-17", p[1], query_id)
    elif query_id == 4:
        start = date.fromisoformat(p[0])
        query = _replace(query, "1993-07-01", start.isoformat(), query_id)
        query = _replace(query, "1993-10-01", _add_months(start, 3).isoformat(), query_id)
    elif query_id == 5:
        start = date.fromisoformat(p[1])
        query = _replace(query, "AMERICA", p[0], query_id)
        query = _replace(query, "1993-01-01", start.isoformat(), query_id)
        query = _replace(query, "1994-01-01", _add_months(start, 12).isoformat(), query_id)
    elif query_id == 6:
        start = date.fromisoformat(p[0])
        center = Decimal(p[1])
        query = _replace(query, "1993-01-01", start.isoformat(), query_id)
        query = _replace(query, "1994-01-01", _add_months(start, 12).isoformat(), query_id)
        query = _replace(query, "0.06", format(center - Decimal("0.01"), ".2f"), query_id)
        query = _replace(query, "0.08", format(center + Decimal("0.01"), ".2f"), query_id)
        query = _replace(query, "25", str(int(p[2])), query_id)
    elif query_id == 7:
        query = _replace(query, "MOZAMBIQUE", p[0], query_id)
        query = _replace(query, "UNITED KINGDOM", p[1], query_id)
    elif query_id == 8:
        query = _replace(query, "AFRICA", p[1], query_id)
        query = _replace(query, "PROMO POLISHED TIN", p[2], query_id)
        corrections.append(
            "Q08 nation parameter is used only by the removed aggregate expression"
        )
    elif query_id == 10:
        start = date.fromisoformat(p[0])
        query = _replace(query, "1993-11-01", start.isoformat(), query_id)
        query = _replace(query, "1994-02-01", _add_months(start, 3).isoformat(), query_id)
    elif query_id == 12:
        start = date.fromisoformat(p[2])
        query = _replace(query, "FOB", p[0], query_id)
        query = _replace(query, "REG AIR", p[1], query_id)
        query = _replace(query, "1993-01-01", start.isoformat(), query_id)
        query = _replace(query, "1994-01-01", _add_months(start, 12).isoformat(), query_id)
    elif query_id == 14:
        start = date.fromisoformat(p[0])
        query = _replace(query, "1993-04-01", start.isoformat(), query_id)
        query = _replace(query, "1993-05-01", _add_months(start, 1).isoformat(), query_id)
    elif query_id == 15:
        start = date.fromisoformat(p[0])
        query = _replace(query, "1996-01-01", start.isoformat(), query_id)
        query = _replace(query, "1996-04-01", _add_months(start, 3).isoformat(), query_id)
    elif query_id == 19:
        query = _replace(query, "Brand13", p[0], query_id)
        query = _replace(query, "Brand#43", p[1], query_id)
        query = _replace(query, "Brand#55", p[2], query_id)
        query = _replace(query, "(6)", "(%d)" % int(p[3]), query_id)
        query = _replace(query, "(11)", "(%d)" % int(p[4]), query_id)
        query = _replace(query, "(27)", "(%d)" % int(p[5]), query_id)
        corrections.append("Q19 release template omitted # in the first brand marker")
    else:
        raise WorkloadError("unsupported template Q%02d" % query_id)

    if not query.endswith("\n"):
        query += "\n"
    return query, corrections


def _qgen_parameters(
    qgen: Path,
    dbgen_directory: Path,
    parameter_templates: Path,
    query_id: int,
    scale: str,
    seed: int,
) -> Tuple[List[str], str]:
    environment = dict(os.environ)
    environment["DSS_CONFIG"] = str(dbgen_directory.resolve())
    environment["DSS_QUERY"] = str(parameter_templates.resolve())
    command = [str(qgen.resolve()), "-r", str(seed), "-s", scale, str(query_id)]
    completed = subprocess.run(
        command,
        cwd=str(dbgen_directory.resolve()),
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if completed.returncode != 0:
        raise WorkloadError(
            "qgen failed for Q%02d SF=%s seed=%d: %s" %
            (query_id, scale, seed, completed.stderr[-1000:].strip())
        )
    candidates = [
        line.strip() for line in completed.stdout.splitlines()
        if line.strip() and not line.lstrip().startswith("--")
    ]
    if len(candidates) != 1:
        raise WorkloadError(
            "qgen parameter template for Q%02d produced %d data lines" %
            (query_id, len(candidates))
        )
    parameters = [item.strip() for item in candidates[0].split("|")]
    if len(parameters) != PARAMETER_COUNTS[query_id]:
        raise WorkloadError(
            "qgen output for Q%02d has %d parameters: %r" %
            (query_id, len(parameters), candidates[0])
        )
    return parameters, completed.stdout


def generate(args: argparse.Namespace) -> Dict[str, Any]:
    output = args.out.resolve()
    if output.exists():
        raise WorkloadError("refusing to reuse workload directory: %s" % output)
    if not args.qgen.is_file():
        raise WorkloadError("qgen does not exist: %s" % args.qgen)
    if not args.dbgen_dir.is_dir():
        raise WorkloadError("dbgen directory does not exist: %s" % args.dbgen_dir)
    if not args.sparqlprov_templates.is_dir():
        raise WorkloadError(
            "SPARQLprov template directory does not exist: %s"
            % args.sparqlprov_templates
        )
    for query_id in RELEASED_IDS:
        released = args.sparqlprov_templates / (
            "%02d_prov_naryrel_non_aggregate.sparql" % query_id
        )
        if not released.is_file():
            raise WorkloadError("missing released SPARQLprov template: %s" % released)
    output.mkdir(parents=True)

    entries: List[Dict[str, Any]] = []
    for scale in args.scales:
        scale_slug = _scale_slug(scale)
        for query_id in TEMPLATE_IDS:
            template_path = args.templates / ("Q%02d.rq" % query_id)
            if not template_path.is_file():
                raise WorkloadError("missing query template: %s" % template_path)
            template = template_path.read_text(encoding="utf-8")
            for instance_index, seed in enumerate(args.seeds, 1):
                instance = "q%03d" % instance_index
                query_key = "%s-Q%02d-%s" % (scale_slug, query_id, instance)
                query_dir = output / scale_slug / ("Q%02d" % query_id) / instance
                parameters, qgen_stdout = _qgen_parameters(
                    args.qgen, args.dbgen_dir, args.parameter_templates,
                    query_id, scale, seed,
                )
                base_query, corrections = instantiate(template, query_id, parameters)
                row_query = inline_rows.inline_rows(base_query)
                if query_id in RELEASED_IDS:
                    sparqlprov_template = args.sparqlprov_templates / (
                        "%02d_prov_naryrel_non_aggregate.sparql" % query_id
                    )
                    sparqlprov_query, sparqlprov_corrections = instantiate(
                        sparqlprov_template.read_text(encoding="utf-8"),
                        query_id,
                        parameters,
                    )
                    if sparqlprov_corrections != corrections:
                        raise WorkloadError(
                            "SPARQLprov/base correction metadata differs for Q%02d"
                            % query_id
                        )
                    sparqlprov_source = "SPARQLprov release prov_naryrel_non_aggregate"
                else:
                    sparqlprov_query = sparqlprov_rows.rewrite(base_query, row_query)
                    sparqlprov_source = "project adapted SPARQLprov-style nary-row query"
                _atomic_text(query_dir / "qgen.stdout", qgen_stdout)
                _atomic_json(query_dir / "parameters.json", {
                    "query_id": query_key,
                    "template": "Q%02d" % query_id,
                    "scale_factor": scale,
                    "seed": seed,
                    "parameters": parameters,
                })
                _atomic_text(query_dir / "base.rq", base_query)
                _atomic_text(query_dir / "row-inline.rq", row_query)
                _atomic_text(query_dir / "sparqlprov.rq", sparqlprov_query)
                entries.append({
                    "query_id": query_key,
                    "scale_factor": scale,
                    "template": "Q%02d" % query_id,
                    "template_source": (
                        "SPARQLprov release base_non_aggregate"
                        if query_id in RELEASED_IDS
                        else "project adapted non-aggregate variant"
                    ),
                    "instance": instance,
                    "seed": seed,
                    "parameters": parameters,
                    "artifact_corrections": corrections,
                    "base_query": str((query_dir / "base.rq").relative_to(output)),
                    "row_inline_query": str((query_dir / "row-inline.rq").relative_to(output)),
                    "sparqlprov_query": str(
                        (query_dir / "sparqlprov.rq").relative_to(output)
                    ),
                    "sparqlprov_query_source": sparqlprov_source,
                    "qgen_stdout": str((query_dir / "qgen.stdout").relative_to(output)),
                    "parameter_record": str((query_dir / "parameters.json").relative_to(output)),
                })

    manifest = {
        "schema": SCHEMA,
        "tpch_version": args.tpch_version,
        "scale_factors": list(args.scales),
        "fractional_scale_factors": [
            scale for scale in args.scales if Decimal(scale) < Decimal("1")
        ],
        "templates": ["Q%02d" % query_id for query_id in TEMPLATE_IDS],
        "released_templates": ["Q%02d" % query_id for query_id in TEMPLATE_IDS
                               if query_id in RELEASED_IDS],
        "adapted_templates": ["Q04", "Q15"],
        "seeds": list(args.seeds),
        "instances_per_template_scale": len(args.seeds),
        "rdf_star_profile": "RDF-star 1.1 quoted triple plus occurrenceOf",
        "rdf_star_12_permitted": False,
        "provenance_granularity": "one token per TPC-H row rdf:type marker",
        "query_layout": "hybrid-inline, one occurrence lookup per distinct row subject",
        "sparqlprov_layout": "released or explicitly adapted nary-row provenance query",
        "entries": entries,
    }
    _atomic_json(output / "manifest.json", manifest)
    audit_manifest(output / "manifest.json")
    return manifest


def audit_manifest(path: Path) -> Dict[str, Any]:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("schema") != SCHEMA:
        raise WorkloadError("unsupported workload schema: %r" % manifest.get("schema"))
    if manifest.get("rdf_star_profile") != "RDF-star 1.1 quoted triple plus occurrenceOf":
        raise WorkloadError("workload does not declare the RDF-star 1.1 profile")
    if manifest.get("rdf_star_12_permitted") is not False:
        raise WorkloadError("workload does not prohibit RDF 1.2 reification")
    if manifest.get("provenance_granularity") != "one token per TPC-H row rdf:type marker":
        raise WorkloadError("workload does not use per-row provenance")
    if manifest.get("query_layout") != (
        "hybrid-inline, one occurrence lookup per distinct row subject"
    ):
        raise WorkloadError("workload does not use the hybrid-inline row layout")
    root = path.parent
    entries = manifest.get("entries")
    if not isinstance(entries, list):
        raise WorkloadError("manifest entries must be a list")
    expected = (len(manifest["scale_factors"]) * len(TEMPLATE_IDS)
                * int(manifest["instances_per_template_scale"]))
    if len(entries) != expected:
        raise WorkloadError("manifest has %d entries; expected %d" % (len(entries), expected))
    query_ids = set()
    for entry in entries:
        query_id = entry["query_id"]
        if query_id in query_ids:
            raise WorkloadError("duplicate query id: %s" % query_id)
        query_ids.add(query_id)
        base = root / entry["base_query"]
        row = root / entry["row_inline_query"]
        sparqlprov = root / entry["sparqlprov_query"]
        for artifact in (
            base, row, sparqlprov,
            root / entry["qgen_stdout"], root / entry["parameter_record"],
        ):
            if not artifact.is_file() or artifact.stat().st_size == 0:
                raise WorkloadError("missing workload artifact: %s" % artifact)
        base_text = base.read_text(encoding="utf-8")
        row_text = row.read_text(encoding="utf-8")
        if "occurrenceOf" in base_text:
            raise WorkloadError("base query contains row occurrence lookup: %s" % query_id)
        if "rdf:reifies" in base_text or "rdf-syntax-ns#reifies" in base_text or "<<(" in base_text:
            raise WorkloadError("RDF 1.2 syntax found in base query: %s" % query_id)
        if "occurrenceOf" not in row_text:
            raise WorkloadError("row-inline query has no occurrence lookup: %s" % query_id)
        if "rdf:reifies" in row_text or "rdf-syntax-ns#reifies" in row_text or "<<(" in row_text:
            raise WorkloadError("RDF 1.2 syntax found in row-inline query: %s" % query_id)
        occurrence_lines = [
            line for line in row_text.splitlines()
            if "<http://example.org/occurrenceOf>" in line
        ]
        if not occurrence_lines or any(
            not line.lstrip().startswith("<< ") or " >> " not in line
            for line in occurrence_lines
        ):
            raise WorkloadError("non-legacy RDF-star row lookup in query: %s" % query_id)
        try:
            regenerated = inline_rows.inline_rows(base_text)
        except (RuntimeError, ValueError) as error:
            raise WorkloadError(
                "cannot verify the frozen row-inline query for %s: %s" % (query_id, error)
            ) from error
        if regenerated != row_text:
            raise WorkloadError("frozen row-inline query differs from its generator: %s" % query_id)
        sparqlprov_text = sparqlprov.read_text(encoding="utf-8")
        if "?prov_sum" not in sparqlprov_text:
            raise WorkloadError("SPARQLprov query has no provenance sum: %s" % query_id)
        if "occurrenceOf" in sparqlprov_text or "rdf:reifies" in sparqlprov_text or "<<(" in sparqlprov_text:
            raise WorkloadError("SPARQLprov n-ary-row query contains reification syntax: %s" % query_id)
    return {
        "status": "ok",
        "entry_count": len(entries),
        "query_ids": len(query_ids),
        "scale_factors": manifest["scale_factors"],
    }


def shard_manifest(args: argparse.Namespace) -> Dict[str, Any]:
    workload_path = args.manifest.resolve()
    workload = json.loads(workload_path.read_text(encoding="utf-8"))
    audit_manifest(workload_path)
    workers = list(args.workers)
    if len(workers) < 1:
        raise WorkloadError("at least one worker is required")
    scales = list(workload["scale_factors"])
    engines = list(args.engines)
    instances = set(args.instances)
    query_ids_by_scale = {
        scale: [entry["query_id"] for entry in workload["entries"]
                if entry["scale_factor"] == scale
                and entry["instance"] in instances]
        for scale in scales
    }
    if any(not query_ids_by_scale[scale] for scale in scales):
        raise WorkloadError(
            "selected instances have no queries for every scale: %s"
            % ",".join(sorted(instances))
        )
    shards = {worker: [] for worker in workers}
    for scale_index, scale in enumerate(reversed(scales)):
        for engine_index, engine in enumerate(engines):
            worker = workers[(scale_index + engine_index) % len(workers)]
            shards[worker].append({
                "batch_id": "%s-%s" % (_scale_slug(scale), engine),
                "scale_factor": scale,
                "engine": engine,
                "methods": list(args.methods),
                "query_ids": query_ids_by_scale[scale],
                "cell_count": len(query_ids_by_scale[scale]) * len(args.methods),
            })
    result = {
        "schema": SHARD_SCHEMA,
        "workload_manifest": str(workload_path),
        "instances": sorted(instances),
        "workers": [
            {
                "worker": worker,
                "batches": shards[worker],
                "cell_count": sum(batch["cell_count"] for batch in shards[worker]),
            }
            for worker in workers
        ],
    }
    _atomic_json(args.out.resolve(), result)
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    subparsers = parser.add_subparsers(dest="command", required=True)

    generate_parser = subparsers.add_parser("generate", help="freeze qgen parameters and queries")
    generate_parser.add_argument("--qgen", required=True, type=Path)
    generate_parser.add_argument("--dbgen-dir", required=True, type=Path)
    generate_parser.add_argument(
        "--sparqlprov-templates",
        required=True,
        type=Path,
        help="SPARQLprov release tpch/sparql_examples directory",
    )
    generate_parser.add_argument("--out", required=True, type=Path)
    generate_parser.add_argument("--templates", type=Path, default=DEFAULT_TEMPLATES)
    generate_parser.add_argument(
        "--parameter-templates", type=Path, default=DEFAULT_PARAMETER_TEMPLATES
    )
    generate_parser.add_argument(
        "--scale",
        dest="scales",
        action="append",
        help=(
            "scale factor to freeze; repeat to override the nine formal points "
            "10^(i/4-2), i=0..8"
        ),
    )
    generate_parser.add_argument("--seed", dest="seeds", action="append", type=int)
    generate_parser.add_argument("--tpch-version", default="3.0.1")

    audit_parser = subparsers.add_parser("audit", help="audit a frozen workload")
    audit_parser.add_argument("manifest", type=Path)

    shard_parser = subparsers.add_parser("shard", help="split SF x engine batches across workers")
    shard_parser.add_argument("--manifest", required=True, type=Path)
    shard_parser.add_argument("--worker", dest="workers", action="append", required=True)
    shard_parser.add_argument(
        "--instance",
        dest="instances",
        action="append",
        default=None,
        help="frozen instance to include; default q001",
    )
    shard_parser.add_argument(
        "--engine", dest="engines", action="append",
        default=None, help="engine name; default graphdb and oxigraph",
    )
    shard_parser.add_argument("--method", dest="methods", action="append", default=None)
    shard_parser.add_argument("--out", required=True, type=Path)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "generate":
            args.scales = tuple(args.scales or DEFAULT_SCALES)
            args.seeds = tuple(args.seeds or DEFAULT_SEEDS)
            result = generate(args)
            summary = {"status": "ok", "entries": len(result["entries"])}
        elif args.command == "audit":
            summary = audit_manifest(args.manifest.resolve())
        else:
            args.instances = tuple(args.instances or DEFAULT_INSTANCES)
            args.engines = tuple(args.engines or DEFAULT_ENGINES)
            args.methods = tuple(args.methods or DEFAULT_METHODS)
            result = shard_manifest(args)
            summary = {
                "status": "ok",
                "workers": len(result["workers"]),
                "cells": sum(worker["cell_count"] for worker in result["workers"]),
            }
    except (OSError, ValueError, WorkloadError) as error:
        parser.exit(1, "tpch workload: error: %s\n" % error)
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
