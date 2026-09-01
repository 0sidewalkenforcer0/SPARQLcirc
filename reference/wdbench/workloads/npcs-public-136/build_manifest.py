#!/usr/bin/env python3
"""Validate and describe the public NPCS Basic Wikidata workload."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any


SCHEMA = "npcs-public-136-workload-v1"
EXPECTED_COUNTS = {"single": 49, "multiple": 37, "optional": 50}
SHARD_OFFSETS = {"single": 0, "multiple": 1, "optional": 2}
SOURCE_REPOSITORY = "https://github.com/ZubariaForthAcc/NPCS.git"


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


def numeric_name(path: Path) -> int:
    if not path.stem.isdigit():
        raise ValueError("query filename is not numeric: %s" % path)
    return int(path.stem)


def projected_variables(query: str, path: Path) -> list[str]:
    match = re.search(r"(?is)\bSELECT\s+(.*?)\bWHERE\b", query)
    if match is None:
        raise ValueError("query has no SELECT ... WHERE header: %s" % path)
    header = match.group(1)
    if "*" in header or re.search(r"[()]", header):
        raise ValueError("query is not a plain variable projection: %s" % path)
    variables = re.findall(r"\?[A-Za-z_][A-Za-z0-9_]*", header)
    if len(variables) != 1:
        raise ValueError("query does not project exactly one variable: %s" % path)
    return variables


def build(root: Path) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    shard_counts = [0, 0, 0]
    for category in ("single", "multiple", "optional"):
        paths = sorted((root / category).glob("*.sparql"), key=numeric_name)
        counts[category] = len(paths)
        if len(paths) != EXPECTED_COUNTS[category]:
            raise ValueError(
                "%s contains %d queries, expected %d"
                % (category, len(paths), EXPECTED_COUNTS[category])
            )
        for position, path in enumerate(paths):
            query = path.read_text(encoding="utf-8")
            if not query.strip():
                raise ValueError("empty query: %s" % path)
            projection = projected_variables(query, path)
            shard = (position + SHARD_OFFSETS[category]) % 3
            shard_counts[shard] += 1
            entries.append(
                {
                    "query_id": "%s-%02d" % (category, numeric_name(path)),
                    "category": category,
                    "source_number": numeric_name(path),
                    "query": str(path.relative_to(root.parent)).replace("\\", "/"),
                    "projected_variables": projection,
                    "shard": shard,
                    "bytes": len(query.encode("utf-8")),
                }
            )
    if counts != EXPECTED_COUNTS or len(entries) != 136:
        raise ValueError("public workload count contract failed")
    if shard_counts != [46, 45, 45]:
        raise ValueError("unexpected shard sizes: %r" % shard_counts)
    return {
        "schema": SCHEMA,
        "workload_id": "NPCS-public-136",
        "source": {
            "repository": SOURCE_REPOSITORY,
            "path": "queries/Basic/wikidata",
        },
        "counts": {**counts, "total": len(entries)},
        "sharding": {
            "algorithm": "within-category round-robin with offsets single=0, multiple=1, optional=2",
            "shards": 3,
            "counts": shard_counts,
        },
        "query_contract": {
            "projection": "exactly one public SELECT variable",
            "input": "unaltered public Basic query text",
            "limit": None,
        },
        "entries": entries,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queries", type=Path, default=Path(__file__).parent / "queries")
    parser.add_argument("--out", type=Path, default=Path(__file__).parent / "manifest.json")
    args = parser.parse_args()
    manifest = build(args.queries.resolve())
    atomic_json(args.out.resolve(), manifest)
    print(json.dumps({
        "status": "ok",
        "queries": manifest["counts"]["total"],
        "shards": manifest["sharding"]["counts"],
        "out": str(args.out.resolve()),
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
