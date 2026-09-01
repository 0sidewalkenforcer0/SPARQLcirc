#!/usr/bin/env python3
"""Collect formal Wikidata CUDD/d4 cells into the Figure 6 stage table."""

from __future__ import annotations

import argparse
import csv
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Mapping, Optional, Sequence


CELL_SCHEMA = "wikidata-circuit-pqe-cell-v1"
COLUMNS = (
    "query_id",
    "category",
    "compiler",
    "backend",
    "method",
    "physical_method",
    "status",
    "measured_successes",
    "provenance_acquisition_ms",
    "artifact_preparation_ms",
    "pqe_overhead_ms",
    "total_e2e_ms",
)


class SummaryError(RuntimeError):
    """The selected PQE roots do not form an unambiguous formal result."""


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def atomic_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    if path.exists():
        raise SummaryError("refusing to overwrite %s" % path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False, newline=""
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column) for column in COLUMNS})
        handle.flush()
        os.fsync(handle.fileno())
        temporary = Path(handle.name)
    os.replace(temporary, path)


def collect(roots: Sequence[Path]) -> dict[tuple[str, str, str], Mapping[str, Any]]:
    result: dict[tuple[str, str, str], Mapping[str, Any]] = {}
    for root in roots:
        for path in root.rglob("cell.json"):
            cell = read_json(path)
            if not isinstance(cell, Mapping) or cell.get("schema") != CELL_SCHEMA:
                continue
            key = (str(cell.get("query_id")), str(cell.get("method")), str(cell.get("backend")))
            if key in result:
                raise SummaryError("duplicate PQE cell %s" % (key,))
            protocol = cell.get("protocol")
            if not isinstance(protocol, Mapping) or (
                protocol.get("warmups"),
                protocol.get("measured_runs"),
                protocol.get("primary_statistic"),
            ) != (1, 5, "median"):
                raise SummaryError("PQE cell does not use the formal 1+5 median protocol")
            result[key] = cell
    return result


def summarize(manifest: Mapping[str, Any], cells: Mapping[tuple[str, str, str], Mapping[str, Any]]) -> list[dict[str, Any]]:
    categories = {str(entry["query_id"]): str(entry["category"]) for entry in manifest["entries"]}
    rows = []
    for (query_id, physical, backend), cell in sorted(cells.items()):
        if query_id not in categories:
            raise SummaryError("PQE cell is outside the workload: %s" % query_id)
        if physical not in ("C-flat", "C-factorised", "C-path"):
            raise SummaryError("unsupported C method: %s" % physical)
        if backend not in ("cudd", "d4"):
            raise SummaryError("unsupported PQE backend: %s" % backend)
        stages = cell.get("stage_medians_ms")
        if not isinstance(stages, Mapping):
            stages = {}
        rows.append({
            "query_id": query_id,
            "category": categories[query_id],
            "compiler": "CUDD" if backend == "cudd" else "d4",
            "backend": "cudd-shared" if backend == "cudd" else "d4v2-per-answer",
            "method": "C-flat" if physical == "C-flat" else "C-factored",
            "physical_method": physical,
            "status": cell.get("status"),
            "measured_successes": cell.get("measured_successes"),
            "provenance_acquisition_ms": stages.get("circuit_construction_ms"),
            "artifact_preparation_ms": stages.get("circuit_parsing_ms"),
            "pqe_overhead_ms": stages.get("compilation_and_wmc_ms"),
            "total_e2e_ms": stages.get("full_pipeline_ms"),
        })
    return rows


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--cells", required=True, action="append", type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    manifest = read_json(args.manifest.resolve())
    if not isinstance(manifest, Mapping) or manifest.get("schema") != "wikidata-141-workload-v1":
        raise SummaryError("unsupported workload manifest")
    rows = summarize(manifest, collect([path.resolve() for path in args.cells]))
    atomic_csv(args.out.resolve(), rows)
    print(json.dumps({"status": "ok", "rows": len(rows)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
