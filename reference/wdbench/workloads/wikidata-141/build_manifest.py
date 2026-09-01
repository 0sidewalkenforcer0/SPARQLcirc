#!/usr/bin/env python3
"""Build one 141-query manifest from the frozen Basic and path assets."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Optional


SCHEMA = "wikidata-141-workload-v1"


def load_builder(path: Path, module_name: str) -> Any:
    specification = importlib.util.spec_from_file_location(module_name, path)
    if specification is None or specification.loader is None:
        raise ValueError("cannot load workload builder: %s" % path)
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


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


def build(root: Path, wdbench: Optional[Path] = None) -> dict[str, Any]:
    basic_root = root.parent / "npcs-public-136"
    paths_root = root.parent / "wdbench-property-paths-5"
    basic_builder = load_builder(
        basic_root / "build_manifest.py", "npcs_public_136_workload"
    )
    paths_builder = load_builder(
        paths_root / "build_manifest.py", "wdbench_property_paths_5_workload"
    )
    basic = basic_builder.build(basic_root / "queries")
    paths = paths_builder.build(paths_root, wdbench)
    if basic.get("schema") != "npcs-public-136-workload-v1":
        raise ValueError("unexpected NPCS Basic workload schema")
    if paths.get("schema") != "wdbench-property-paths-5-workload-v1":
        raise ValueError("unexpected WDBench path workload schema")
    basic_counts = basic["counts"]
    path_counts = paths["counts"]
    counts = {
        "single_bgp": int(basic_counts["single"]),
        "multiple_bgp": int(basic_counts["multiple"]),
        "optional": int(basic_counts["optional"]),
        "property_path": int(path_counts["property_path"]),
    }
    counts["total"] = sum(counts.values())
    if counts != {
        "single_bgp": 49,
        "multiple_bgp": 37,
        "optional": 50,
        "property_path": 5,
        "total": 141,
    }:
        raise ValueError("combined Wikidata workload count contract failed: %r" % counts)
    entries: list[dict[str, Any]] = []
    for source in basic["entries"]:
        entry = dict(source)
        entry["workload_component"] = "npcs_public_basic"
        entry["query"] = "../npcs-public-136/%s" % source["query"]
        entry["applicable_methods"] = [
            "N-per-answer",
            "N-shared",
            "C-flat",
            "C-factorised",
        ]
        entries.append(entry)
    for source in paths["entries"]:
        entry = dict(source)
        entry["workload_component"] = "wdbench_property_path_supplement"
        for key in ("source_fragment", "base_query", "sparqlcirc_query"):
            entry[key] = "../wdbench-property-paths-5/%s" % source[key]
        entry["query"] = entry["sparqlcirc_query"]
        entry["semantic_reference_query"] = entry["base_query"]
        entry["applicable_methods"] = ["C-path"]
        entries.append(entry)
    if len(entries) != counts["total"] or len(
        {str(entry["query_id"]) for entry in entries}
    ) != counts["total"]:
        raise ValueError("combined workload entries are missing or duplicated")
    return {
        "schema": SCHEMA,
        "workload_id": "Wikidata-141 (NPCS-public-136 + WDBench-property-paths-5)",
        "counts": counts,
        "components": [
            {
                "workload_id": basic["workload_id"],
                "source_schema": basic["schema"],
                "queries": int(basic_counts["total"]),
                "methods": [
                    "N-per-answer",
                    "N-shared",
                    "C-flat",
                    "C-factorised",
                ],
            },
            {
                "workload_id": paths["workload_id"],
                "source_schema": paths["schema"],
                "queries": int(path_counts["total"]),
                "methods": ["C-path"],
            },
        ],
        "interpretation": (
            "The five WDBench queries are included as a property-path supplement "
            "to the public NPCS Wikidata workload; they are not presented as part "
            "of the NPCS release. Unsupported NPCS/SPARQLprov path cells are not "
            "counted as failures."
        ),
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
                "out": str(args.out.resolve()),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
