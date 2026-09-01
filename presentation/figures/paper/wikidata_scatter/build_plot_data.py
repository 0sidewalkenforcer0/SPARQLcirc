#!/usr/bin/env python3
"""Build the Figure 4/5 plotting tables from formal construction summaries."""

from __future__ import annotations

import argparse
import csv
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Mapping, Optional, Sequence


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def atomic_csv(path: Path, columns: Sequence[str], rows: Sequence[Mapping[str, Any]]) -> None:
    if path.exists():
        raise RuntimeError("refusing to overwrite %s" % path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False, newline=""
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column) for column in columns})
        handle.flush()
        os.fsync(handle.fileno())
        temporary = Path(handle.name)
    os.replace(temporary, path)


def successful(rows: Sequence[Mapping[str, Any]]) -> dict[tuple[str, str], Mapping[str, Any]]:
    result = {}
    for row in rows:
        if row.get("status") != "ok":
            continue
        key = (str(row["query_id"]), str(row["method"]))
        if key in result:
            raise RuntimeError("duplicate summary row %s" % (key,))
        result[key] = row
    return result


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--construction-summary", required=True, type=Path)
    parser.add_argument("--derivations", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    summary = read_json(args.construction_summary.resolve())
    if summary.get("schema") != "wikidata-141-summary-v1":
        raise RuntimeError("unsupported construction summary")
    rows = successful(summary["rows"])
    derivations = {row["query_id"]: row for row in read_csv(args.derivations.resolve())}
    output = args.out.resolve()
    output.mkdir(parents=True, exist_ok=True)

    pair_columns = (
        "query_id", "category", "npcs_endpoint_e2e_ms",
        "sparqlcirc_endpoint_e2e_ms", "sparqlcirc_physical_method",
    )
    pair_counts = {}
    for method, filename in (
        ("C-flat", "npcs_vs_c_flat_endpoint.csv"),
        ("C-factorised", "npcs_vs_c_factored_endpoint.csv"),
    ):
        paired = []
        for (query_id, observed_method), c_row in rows.items():
            if observed_method != method:
                continue
            n_row = rows.get((query_id, "N-shared"))
            if n_row is None:
                continue
            paired.append({
                "query_id": query_id,
                "category": c_row["category"],
                "npcs_endpoint_e2e_ms": n_row["median_endpoint_e2e_ms"],
                "sparqlcirc_endpoint_e2e_ms": c_row["median_endpoint_e2e_ms"],
                "sparqlcirc_physical_method": method,
            })
        paired.sort(key=lambda row: (float(row["npcs_endpoint_e2e_ms"]), row["query_id"]))
        atomic_csv(output / filename, pair_columns, paired)
        pair_counts[method] = len(paired)

    derivation_columns = (
        "query_id", "category", "method", "raw_method", "derivations_total",
        "endpoint_e2e_ms", "count_source",
    )
    derivation_rows = []
    for (query_id, method), row in rows.items():
        if method not in ("C-flat", "C-factorised", "C-path") or query_id not in derivations:
            continue
        count = derivations[query_id]
        display = "SPARQLcirc (flat)" if method == "C-flat" else (
            "SPARQLcirc (path)" if method == "C-path" else "SPARQLcirc (factored)"
        )
        derivation_rows.append({
            "query_id": query_id,
            "category": row["category"],
            "method": display,
            "raw_method": method,
            "derivations_total": count["derivations_total"],
            "endpoint_e2e_ms": row["median_endpoint_e2e_ms"],
            "count_source": count["count_source"],
        })
    derivation_rows.sort(key=lambda row: (int(row["derivations_total"]), row["query_id"], row["raw_method"]))
    atomic_csv(output / "derivations_vs_c_endpoint_e2e.csv", derivation_columns, derivation_rows)
    manifest = {
        "schema": "wikidata-scatter-plot-data-v1",
        "construction_summary": str(args.construction_summary.resolve()),
        "derivation_counts": str(args.derivations.resolve()),
        "protocol": summary["protocol"],
        "paired_endpoint_points": pair_counts,
        "derivation_points": len(derivation_rows),
    }
    (output / "plot_data_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"status": "ok", **pair_counts, "derivation_rows": len(derivation_rows)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
