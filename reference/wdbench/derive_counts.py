#!/usr/bin/env python3
"""Extract one stable derivation count per Wikidata query from formal cells."""

from __future__ import annotations

import argparse
import csv
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Any, Mapping, Optional, Sequence


SOURCE_SCHEMA = "wikidata-method-cell-v1"
COLUMNS = ("query_id", "category", "derivations_total", "count_source")


class CountError(RuntimeError):
    """Construction artifacts cannot provide one stable derivation count."""


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def collect(roots: Sequence[Path]) -> dict[tuple[str, str], tuple[Path, Mapping[str, Any]]]:
    cells: dict[tuple[str, str], tuple[Path, Mapping[str, Any]]] = {}
    for root in roots:
        for path in root.rglob("cell.json"):
            value = read_json(path)
            if not isinstance(value, Mapping) or value.get("schema") != SOURCE_SCHEMA:
                continue
            key = (str(value.get("query_id")), str(value.get("physical_method")))
            if key in cells:
                raise CountError("duplicate construction cell %s" % (key,))
            cells[key] = (path, value)
    return cells


def polynomial_count(path: Path, parser: Any) -> int:
    total = 0
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            row = json.loads(line)
            provenance = row.get("provenance") if isinstance(row, Mapping) else None
            if not isinstance(provenance, str):
                raise CountError("invalid NPCS provenance row %d in %s" % (line_number, path))
            arena, root = parser.parse_expression(provenance)
            node = arena.nodes[root]
            total += len(node.payload) if node.op == "plus" else 1
    return total


def circuit_count(path: Path, circuit_io: Any) -> int:
    with path.open(encoding="utf-8") as handle:
        circuit, answers, _ = circuit_io.parse(handle)
    total = 0
    for root in answers:
        operation, payload = circuit[root]
        total += len(payload) if operation == "plus" else 1
    return total


def stable_counts(paths: Sequence[Path], counter: Any) -> Optional[int]:
    present = [counter(path) for path in paths if path.is_file()]
    if not present:
        return None
    if len(set(present)) != 1:
        raise CountError("derivation count differs across measured runs: %s" % present)
    return present[0]


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--cells", required=True, action="append", type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    source_root = args.source_root.resolve()
    sys.path.insert(0, str(source_root / "reference"))
    import circuit_io
    import npcs_postprocess

    manifest = read_json(args.manifest.resolve())
    entries = {str(entry["query_id"]): entry for entry in manifest["entries"]}
    cells = collect([path.resolve() for path in args.cells])
    rows = []
    for query_id, entry in entries.items():
        count = None
        source = None
        n = cells.get((query_id, "N-paired"))
        if n is not None:
            path = n[0].parent
            provenance_paths = [
                path / ("measured-%02d" % index) / "N-shared" / "pp" / "npcs-provenance.jsonl"
                for index in range(1, 6)
            ]
            count = stable_counts(
                provenance_paths,
                lambda item: polynomial_count(item, npcs_postprocess),
            )
            if count is not None:
                source = "NPCS provenance stream"
        for method, label in (
            ("C-flat", "C-flat answer-root feeds"),
            ("C-path", "C-path answer-root feeds"),
        ):
            if count is not None:
                break
            cell = cells.get((query_id, method))
            if cell is None:
                continue
            path = cell[0].parent
            circuit_paths = [
                path / ("measured-%02d" % index) / "circuit.nt"
                for index in range(1, 6)
            ]
            count = stable_counts(
                circuit_paths,
                lambda item: circuit_count(item, circuit_io),
            )
            if count is not None:
                source = label
        if count is not None:
            rows.append({
                "query_id": query_id,
                "category": entry["category"],
                "derivations_total": count,
                "count_source": source,
            })

    out = args.out.resolve()
    if out.exists():
        raise CountError("refusing to overwrite %s" % out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=out.parent, delete=False, newline=""
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
        handle.flush()
        os.fsync(handle.fileno())
        temporary = Path(handle.name)
    os.replace(temporary, out)
    print(json.dumps({"status": "ok", "rows": len(rows)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
