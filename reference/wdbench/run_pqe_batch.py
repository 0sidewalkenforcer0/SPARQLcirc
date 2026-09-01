#!/usr/bin/env python3
"""Run CUDD or per-answer d4 PQE over successful Wikidata C cells."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any, Mapping, Optional, Sequence


REFERENCE = Path(__file__).resolve().parents[1]
if str(REFERENCE) not in sys.path:
    sys.path.insert(0, str(REFERENCE))

import event_probabilities


SOURCE_SCHEMA = "wikidata-method-cell-v1"
RESULT_SCHEMA = "wikidata-circuit-pqe-cell-v1"
BATCH_SCHEMA = "wikidata-141-pqe-batch-v1"
METHODS = ("C-flat", "C-factorised", "C-path")


class BatchError(RuntimeError):
    """The selected construction cells cannot be evaluated unambiguously."""


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise BatchError("cannot read JSON %s: %s" % (path, error)) from error


def atomic_json(path: Path, value: Any) -> None:
    if path.exists():
        raise BatchError("refusing to overwrite %s" % path)
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


def construction_cells(
    roots: Sequence[Path],
    query_ids: set[str],
) -> dict[tuple[str, str], Path]:
    result: dict[tuple[str, str], Path] = {}
    for root in roots:
        for path in root.rglob("cell.json"):
            cell = read_json(path)
            if not isinstance(cell, Mapping) or cell.get("schema") != SOURCE_SCHEMA:
                continue
            query_id = str(cell.get("query_id"))
            method = str(cell.get("physical_method"))
            if query_id not in query_ids or method not in METHODS:
                continue
            if cell.get("status") != "ok":
                continue
            key = (query_id, method)
            if key in result:
                raise BatchError("duplicate successful construction cell %s" % (key,))
            result[key] = path
    return result


def output_path(root: Path, query_id: str, method: str, backend: str) -> Path:
    return root / "cells" / query_id / method.replace("-", "_") / backend


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--construction-cells", required=True, action="append", type=Path)
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--backend", required=True, choices=("cudd", "d4"))
    parser.add_argument("--d4", type=Path)
    parser.add_argument("--python", type=Path, default=Path(sys.executable))
    parser.add_argument("--shard-index", type=int, choices=(0, 1, 2))
    parser.add_argument("--complete-method-timeout", type=float, default=600.0)
    parser.add_argument("--memory-sample-interval", type=float, default=0.05)
    parser.add_argument(
        "--probability-seed",
        type=int,
        default=event_probabilities.DEFAULT_PROBABILITY_SEED,
    )
    parser.add_argument("--continue-after-failure", action="store_true")
    args = parser.parse_args(argv)
    if args.backend == "d4" and args.d4 is None:
        parser.error("--backend d4 requires --d4")
    if args.complete_method_timeout <= 0 or args.memory_sample_interval <= 0:
        parser.error("timeouts and sampling intervals must be positive")
    if args.probability_seed < 0:
        parser.error("--probability-seed must be non-negative")

    manifest_path = args.manifest.resolve()
    manifest = read_json(manifest_path)
    if not isinstance(manifest, Mapping) or manifest.get("schema") != "wikidata-141-workload-v1":
        raise BatchError("unsupported workload manifest")
    selected_entries = [
        entry for entry in manifest["entries"]
        if args.shard_index is None or int(entry["shard"]) == args.shard_index
    ]
    query_ids = {str(entry["query_id"]) for entry in selected_entries}
    sources = construction_cells(
        [path.resolve() for path in args.construction_cells], query_ids
    )
    expected_successful = {
        (str(entry["query_id"]), method)
        for entry in selected_entries
        for method in entry["applicable_methods"]
        if method in METHODS
    }
    absent_or_failed = sorted(expected_successful.difference(sources))

    output = args.out.resolve()
    output.mkdir(parents=True, exist_ok=True)
    config = {
        "schema": BATCH_SCHEMA,
        "manifest": str(manifest_path),
        "construction_roots": [str(path.resolve()) for path in args.construction_cells],
        "backend": args.backend,
        "d4": str(args.d4.resolve()) if args.d4 is not None else None,
        "python": str(args.python.resolve()),
        "shard_index": args.shard_index,
        "protocol": {
            "warmups": 1,
            "measured_runs": 5,
            "primary_statistic": "median",
            "complete_method_timeout_s_per_execution": args.complete_method_timeout,
            "probability_seed": args.probability_seed,
            "probability_scheme": event_probabilities.PROBABILITY_SCHEME,
        },
        "selection": "successful C construction cells only",
        "construction_cells_absent_or_failed": [list(key) for key in absent_or_failed],
    }
    config_path = output / "batch-config.json"
    if config_path.is_file():
        if read_json(config_path) != config:
            raise BatchError("resume configuration differs from batch-config.json")
    else:
        atomic_json(config_path, config)
    final = output / "batch.json"
    if final.is_file():
        print(json.dumps(read_json(final), sort_keys=True))
        return 0

    invoked = 0
    reused = 0
    failures = 0
    runner = args.source_root.resolve() / "reference" / "wdbench" / "run_pqe.py"
    for query_id, method in sorted(sources):
        target = output_path(output, query_id, method, args.backend)
        existing = target / "cell.json"
        if existing.is_file():
            value = read_json(existing)
            if (
                value.get("schema") != RESULT_SCHEMA
                or value.get("query_id") != query_id
                or value.get("method") != method
                or value.get("backend") != args.backend
            ):
                raise BatchError("existing PQE cell identity mismatch: %s" % target)
            reused += 1
            continue
        if target.exists():
            raise BatchError("partial PQE cell must be preserved or moved: %s" % target)
        command = [
            str(args.python.resolve()),
            str(runner),
            "--source-root", str(args.source_root.resolve()),
            "--cell", str(sources[(query_id, method)]),
            "--out", str(target),
            "--backend", args.backend,
            "--python", str(args.python.resolve()),
            "--complete-method-timeout", str(args.complete_method_timeout),
            "--memory-sample-interval", str(args.memory_sample_interval),
            "--probability-seed", str(args.probability_seed),
        ]
        if args.d4 is not None:
            command.extend(("--d4", str(args.d4.resolve())))
        completed = subprocess.run(command, check=False)
        invoked += 1
        if not (target / "cell.json").is_file():
            raise BatchError("PQE worker returned without a terminal cell: %s" % target)
        value = read_json(target / "cell.json")
        if completed.returncode != 0 or value.get("status") != "ok":
            failures += 1
            if not args.continue_after_failure:
                raise BatchError("PQE cell did not complete: %s" % target)

    result = {
        "schema": BATCH_SCHEMA,
        "status": "terminal",
        "backend": args.backend,
        "successful_construction_cells": len(sources),
        "construction_cells_absent_or_failed": len(absent_or_failed),
        "invoked_in_final_pass": invoked,
        "reused_in_final_pass": reused,
        "non_successful_pqe_cells_in_final_pass": failures,
    }
    atomic_json(final, result)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
