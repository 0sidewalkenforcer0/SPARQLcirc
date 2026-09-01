#!/usr/bin/env python3
"""Validate and describe the frozen five-query WDBench path supplement."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Optional


SCHEMA = "wdbench-property-paths-5-workload-v1"
SOURCE_REPOSITORY = "https://github.com/MillenniumDB/WDBench.git"
SOURCE_FILE = "Queries/paths.txt"

# local identifier, source row, identifier in the screened 540-query
# population, effective plan, exact closure count, exact final-answer count
SELECTION = (
    ("02", 13, "path-106", "dedicated-path-fixpoint", 6, 6),
    ("05", 111, "path-432", "dedicated-path-fixpoint", 6, 6),
    ("08", 113, "path-223", "dedicated-path-fixpoint", 5, 5),
    ("11", 165, "path-536", "dedicated-path-fixpoint", 23, 23),
    ("14", 252, "path-519", "dedicated-path-fixpoint", 255, 255),
)


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False, newline="\n"
    ) as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
        temporary = Path(handle.name)
    os.replace(temporary, path)


def read_source_rows(wdbench: Path) -> dict[int, str]:
    path = wdbench / SOURCE_FILE
    if not path.is_file():
        raise ValueError("missing WDBench source file: %s" % path)
    rows: dict[int, str] = {}
    with path.open(encoding="utf-8-sig") as handle:
        for line_number, raw in enumerate(handle, 1):
            line = raw.rstrip("\r\n")
            if not line:
                continue
            identifier, separator, fragment = line.partition(",")
            if not separator or not identifier.isdigit() or not fragment.strip():
                raise ValueError("invalid WDBench path row %d" % line_number)
            number = int(identifier)
            if number in rows:
                raise ValueError("duplicate WDBench path id: %d" % number)
            rows[number] = fragment.strip()
    return rows


def query_text(path: Path) -> str:
    if not path.is_file():
        raise ValueError("missing frozen query file: %s" % path)
    value = path.read_text(encoding="utf-8")
    if not value.strip():
        raise ValueError("empty frozen query file: %s" % path)
    return value


def validate_query(path: Path) -> str:
    value = query_text(path)
    match = re.search(r"(?is)\bSELECT\s+(.*?)\bWHERE\b", value)
    if match is None or match.group(1).strip() != "?var1":
        raise ValueError("query must project exactly ?var1: %s" % path)
    if re.search(r"(?i)\b(?:DISTINCT|LIMIT)\b", value):
        raise ValueError("DISTINCT or LIMIT is not part of the path workload: %s" % path)
    if not re.search(r"[+*]", value):
        raise ValueError("query has no recursive path operator: %s" % path)
    return value


def build(root: Path, wdbench: Optional[Path] = None) -> dict[str, Any]:
    published_rows = read_source_rows(wdbench) if wdbench is not None else None
    entries: list[dict[str, Any]] = []
    shard_counts = [0, 0, 0]
    for shard_position, selection in enumerate(SELECTION):
        number, source_number, population_id, plan_class, closure_count, final_count = selection
        draw_index = int(number)
        query_root = root / "queries" / number
        source_fragment = query_text(query_root / "source-fragment.sparql").strip()
        base_query = validate_query(query_root / "base.rq")
        circuit_query = validate_query(query_root / "sparqlcirc.rq")
        if published_rows is not None:
            observed = published_rows.get(source_number)
            if observed != source_fragment:
                raise ValueError(
                    "frozen source fragment does not match WDBench row %d" % source_number
                )
        shard = shard_position % 3
        shard_counts[shard] += 1
        entries.append(
            {
                "query_id": "path-%s" % number,
                "category": "property_path",
                "selection_draw_index": draw_index,
                "source_query_number": source_number,
                "screening_population_id": population_id,
                "source_fragment": "queries/%s/source-fragment.sparql" % number,
                "base_query": "queries/%s/base.rq" % number,
                "sparqlcirc_query": "queries/%s/sparqlcirc.rq" % number,
                "projected_variables": ["?var1"],
                "recursive": True,
                "plan_class": plan_class,
                "source_bounded_adaptation": base_query != circuit_query,
                "screening_counts": {
                    "closure": closure_count,
                    "final_answers": final_count,
                },
                "shard": shard,
            }
        )
    if len(entries) != 5 or shard_counts != [2, 2, 1]:
        raise ValueError("property-path workload count contract failed")
    return {
        "schema": SCHEMA,
        "workload_id": "WDBench-property-paths-5",
        "role": "C-only supplement to NPCS-public-136",
        "source": {
            "repository": SOURCE_REPOSITORY,
            "file": SOURCE_FILE,
        },
        "selection": {
            "algorithm": (
                "five fixed draws from a seed-20260901 sample, drawn without "
                "replacement from conservatively screened candidates"
            ),
            "candidate_rule": (
                "closure and final count probes both completed exactly with counts <= 1000"
            ),
            "screening_is_formal_timing": False,
            "seed": 20260901,
            "count": 5,
        },
        "query_contract": {
            "projection": "exactly ?var1",
            "distinct": False,
            "limit": None,
            "base_query": "published path with deterministic alpha-renaming",
            "sparqlcirc_query": (
                "semantically equivalent source-bounded orientation; a top-level "
                "sequence is exposed as named BGP joins when required"
            ),
        },
        "method_scope": {
            "included": ["C-path"],
            "excluded": {
                "NPCS": "the released rewriter has no recursive property-path rule",
                "SPARQLprov": "the released rewriter has no property-path rule",
            },
            "interpretation": (
                "recursive paths use the dedicated fixpoint plan and are recorded "
                "once rather than duplicated under flat and factorised labels"
            ),
        },
        "counts": {
            "property_path": 5,
            "total": 5,
            "dedicated_path_fixpoint": sum(
                entry["plan_class"] == "dedicated-path-fixpoint" for entry in entries
            ),
            "composite_path_factored": sum(
                entry["plan_class"] == "composite-path-factored" for entry in entries
            ),
        },
        "sharding": {
            "algorithm": "fixed-query order round robin across three shards",
            "shards": 3,
            "counts": shard_counts,
        },
        "entries": entries,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).parent)
    parser.add_argument("--wdbench", type=Path)
    parser.add_argument("--out", type=Path, default=Path(__file__).parent / "manifest.json")
    args = parser.parse_args()
    manifest = build(
        args.root.resolve(),
        args.wdbench.resolve() if args.wdbench is not None else None,
    )
    atomic_json(args.out.resolve(), manifest)
    print(
        json.dumps(
            {
                "status": "ok",
                "queries": manifest["counts"]["total"],
                "shards": manifest["sharding"]["counts"],
                "out": str(args.out.resolve()),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
