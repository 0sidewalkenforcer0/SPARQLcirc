#!/usr/bin/env python3
"""Run one resumable Wikidata-141 shard against a verified GraphDB process."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from typing import Any, Mapping


PLAN_SCHEMA = "wikidata-141-shard-plan-v1"
STATUS_SCHEMA = "wikidata-141-shard-status-v1"
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


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def cpu_count(value: str) -> int:
    result: set[int] = set()
    for item in value.split(","):
        bounds = item.strip().split("-", 1)
        if len(bounds) == 1:
            result.add(int(bounds[0]))
        else:
            result.update(range(int(bounds[0]), int(bounds[1]) + 1))
    return len(result)


def graphdb_contract(
    pid: int,
    expected_heap_initial: str,
    expected_heap_max: str,
) -> dict[str, Any]:
    root = Path("/proc") / str(pid)
    status = {}
    for line in (root / "status").read_text(encoding="utf-8").splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            status[key] = value.strip()
    stat = (root / "stat").read_text(encoding="utf-8")
    fields = stat[stat.rfind(")") + 2:].split()
    cmdline = (root / "cmdline").read_bytes().replace(b"\0", b" ").decode(
        "utf-8", errors="replace"
    ).strip()
    allowed = status.get("Cpus_allowed_list", "")
    if cpu_count(allowed) != 1:
        raise RuntimeError("GraphDB is not restricted to one CPU")
    if "-XX:ActiveProcessorCount=1" not in cmdline or "graphdb" not in cmdline.lower():
        raise RuntimeError("GraphDB command line violates the single-core contract")
    initial = re.search(r"(?:^|\s)-Xms([^\s]+)", cmdline)
    maximum = re.search(r"(?:^|\s)-Xmx([^\s]+)", cmdline)
    if initial is None or initial.group(1).lower() != expected_heap_initial.lower():
        raise RuntimeError("GraphDB -Xms differs from --graphdb-heap")
    if maximum is None or maximum.group(1).lower() != expected_heap_max.lower():
        raise RuntimeError("GraphDB -Xmx differs from --graphdb-heap")
    return {
        "pid": pid,
        "start_ticks": int(fields[19]),
        "cpus_allowed_list": allowed,
        "allowed_cpu_count": 1,
        "active_processor_count": 1,
        "heap_initial": initial.group(1),
        "heap_max": maximum.group(1),
        "cmdline": cmdline,
    }


def build_plan(args: argparse.Namespace, workload: Mapping[str, Any]) -> dict[str, Any]:
    entries = [
        entry for entry in workload["entries"]
        if int(entry["shard"]) == args.shard_index
    ]
    cells = []
    for entry in entries:
        logical_methods = tuple(entry["applicable_methods"])
        physical_methods = []
        if any(method.startswith("N-") for method in logical_methods):
            physical_methods.append("N-paired")
        physical_methods.extend(
            method for method in ("C-flat", "C-factorised", "C-path")
            if method in logical_methods
        )
        if not physical_methods:
            raise RuntimeError("query has no runnable methods: %s" % entry["query_id"])
        for method in physical_methods:
            safe_method = method.replace("-", "_")
            cells.append({
                "cell_id": "%s--%s" % (entry["query_id"], method),
                "query_id": entry["query_id"],
                "category": entry["category"],
                "query": entry["query"],
                "physical_method": method,
                "output": "cells/%s/%s" % (entry["query_id"], safe_method),
            })
    return {
        "schema": PLAN_SCHEMA,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "workload_id": workload["workload_id"],
        "workload_components": workload["components"],
        "shard_index": args.shard_index,
        "query_count": len(entries),
        "physical_cell_count": len(cells),
        "logical_method_cell_count": sum(
            len(entry["applicable_methods"]) for entry in entries
        ),
        "protocol": {
            "warmups": args.warmups,
            "measured_runs": args.runs,
            "primary_statistic": "median",
            "complete_method_deadline_s_per_execution": args.timeout,
            "n_endpoint_reuse": True,
            "pqe_in_timed_scope": False,
            "response_mode": "stream-tsv",
            "graphdb_active_processor_count": 1,
            "graphdb_heap": args.graphdb_heap,
            "c_java_max_heap": args.java_max_heap,
            "jvm_heap_peak_measurement": (
                "CircuitRun MemoryMXBean sampled every 100 ms without requesting GC; "
                "GraphDB internal heap peak is not collected"
            ),
            "slurm_cpus_per_task": os.environ.get("SLURM_CPUS_PER_TASK"),
            "slurm_mem_per_node_mb": os.environ.get("SLURM_MEM_PER_NODE"),
            "slurm_node": os.environ.get("SLURMD_NODENAME"),
            "c_parallelism": 1,
            "c_result_handling": "streaming",
            "c_method_labels": {
                "C-flat": "C-flat (streaming)",
                "C-factorised": "C-factorised (streaming)",
                "C-path": "C-path (streaming)",
            },
        },
        "entries": entries,
        "cells": cells,
    }


def ensure_plan(args: argparse.Namespace) -> dict[str, Any]:
    workload = read_json(args.manifest)
    if workload.get("schema") != "wikidata-141-workload-v1":
        raise RuntimeError("unexpected workload manifest schema")
    expected = build_plan(args, workload)
    path = args.result_root / "plan.json"
    if path.is_file():
        observed = read_json(path)
        for key in (
            "schema", "workload_id", "workload_components", "shard_index",
            "query_count", "physical_cell_count", "logical_method_cell_count",
            "protocol", "entries", "cells",
        ):
            if observed.get(key) != expected.get(key):
                raise RuntimeError("saved plan differs at %s" % key)
        return observed
    atomic_json(path, expected)
    return expected


def next_attempt(root: Path, cell_id: str) -> Path:
    parent = root / "driver-attempts" / cell_id
    parent.mkdir(parents=True, exist_ok=True)
    numbers = []
    for path in parent.iterdir():
        match = re.fullmatch(r"attempt-([0-9]+)", path.name)
        if path.is_dir() and match:
            numbers.append(int(match.group(1)))
    attempt = parent / ("attempt-%03d" % ((max(numbers) if numbers else 0) + 1))
    attempt.mkdir()
    return attempt


def archive_partial(output: Path, result_root: Path, cell_id: str) -> None:
    if not output.exists() or (output / "cell.json").is_file():
        return
    parent = result_root / "interrupted-cells" / cell_id
    parent.parent.mkdir(parents=True, exist_ok=True)
    index = 1
    target = parent
    while target.exists():
        index += 1
        target = parent.with_name(parent.name + "-%03d" % index)
    os.replace(output, target)


def command(args: argparse.Namespace, cell: Mapping[str, Any], output: Path) -> list[str]:
    query = args.manifest.parent / str(cell["query"])
    base = [
        sys.executable,
        str(args.source_root / "reference" / "wdbench" / "run_cell.py"),
        "run-n-pair" if cell["physical_method"] == "N-paired" else "run-c",
        "--source-root", str(args.source_root),
        "--query", str(query),
        "--query-id", str(cell["query_id"]),
        "--out", str(output),
        "--engine-pid", str(args.graphdb_pid),
        "--mixed-endpoint", args.mixed_endpoint,
        "--update-endpoint", args.update_endpoint,
        "--jar", str(args.jar),
        "--java", args.java,
        "--java-max-heap", args.java_max_heap,
        "--graphdb-heap-initial", args.graphdb_heap,
        "--graphdb-heap-max", args.graphdb_heap,
        "--reified-data", str(args.reified_data),
        "--timeout", str(args.timeout),
        "--warmups", str(args.warmups),
        "--runs", str(args.runs),
        "--memory-sample-interval", str(args.memory_sample_interval),
        "--exact-response-row-limit", str(args.exact_response_row_limit),
        "--token-regex", args.token_regex,
    ]
    if cell["physical_method"] != "N-paired":
        base.extend(("--method", str(cell["physical_method"])))
    return base


def answer_path(result_root: Path, cell: Mapping[str, Any], logical: str) -> Path:
    base = result_root / str(cell["output"])
    if logical == "N-per-answer":
        return base / "measured-01/N-per-answer/pp/answer-records.jsonl"
    if logical == "N-shared":
        return base / "measured-01/N-shared/pp/answer-records.jsonl"
    return base / "measured-01/offline/answer-records.jsonl"


def same_file(left: Path, right: Path) -> bool:
    if not left.is_file() or not right.is_file() or left.stat().st_size != right.stat().st_size:
        return False
    with left.open("rb") as first, right.open("rb") as second:
        while True:
            a = first.read(1024 * 1024)
            b = second.read(1024 * 1024)
            if a != b:
                return False
            if not a:
                return True


def write_query_validations(plan: Mapping[str, Any], result_root: Path) -> None:
    by_query: dict[str, dict[str, Mapping[str, Any]]] = {}
    for cell in plan["cells"]:
        by_query.setdefault(str(cell["query_id"]), {})[str(cell["physical_method"])] = cell
    target_root = result_root / "query-validation"
    target_root.mkdir(parents=True, exist_ok=True)
    for query_id, cells in by_query.items():
        target = target_root / (query_id + ".json")
        if target.is_file():
            continue
        cell_results = {
            method: result_root / str(spec["output"]) / "cell.json"
            for method, spec in cells.items()
        }
        if not all(path.is_file() for path in cell_results.values()):
            continue
        observed = {method: read_json(path) for method, path in cell_results.items()}
        logical_status = {}
        paths = {}
        if "N-paired" in observed:
            for method in ("N-per-answer", "N-shared"):
                logical_status[method] = observed["N-paired"]["methods"][method]["status"]
                paths[method] = answer_path(result_root, cells["N-paired"], method)
        for method in ("C-flat", "C-factorised", "C-path"):
            if method in observed:
                logical_status[method] = observed[method]["status"]
                paths[method] = answer_path(result_root, cells[method], method)
        all_success = all(value == "ok" for value in logical_status.values())
        comparisons = None
        if all_success and all(path.is_file() for path in paths.values()):
            pairs = (
                ("n_per_answer_equals_n_shared", "N-per-answer", "N-shared"),
                ("c_flat_equals_c_factorised", "C-flat", "C-factorised"),
                ("n_shared_equals_c_factorised", "N-shared", "C-factorised"),
            )
            comparisons = {
                label: same_file(paths[left], paths[right])
                for label, left, right in pairs
                if left in paths and right in paths
            } or None
        atomic_json(target, {
            "schema": "wikidata-141-query-validation-v1",
            "query_id": query_id,
            "logical_method_status": logical_status,
            "answer_comparisons": comparisons,
            "answer_parity_ok": (
                all(comparisons.values()) if isinstance(comparisons, Mapping) else None
            ),
        })


def write_status(plan: Mapping[str, Any], result_root: Path) -> dict[str, Any]:
    rows = []
    for cell in plan["cells"]:
        result_path = result_root / str(cell["output"]) / "cell.json"
        if result_path.is_file():
            result = read_json(result_path)
            status = str(result.get("status") or "invalid")
        else:
            status = "missing"
        rows.append({
            "cell_id": cell["cell_id"],
            "query_id": cell["query_id"],
            "physical_method": cell["physical_method"],
            "status": status,
        })
    completed = sum(row["status"] != "missing" for row in rows)
    status = {
        "schema": STATUS_SCHEMA,
        "updated_utc": datetime.now(timezone.utc).isoformat(),
        "shard_index": plan["shard_index"],
        "physical_cells_total": len(rows),
        "physical_cells_terminal": completed,
        "complete": completed == len(rows),
        "status_counts": {
            name: sum(row["status"] == name for row in rows)
            for name in sorted({row["status"] for row in rows})
        },
        "cells": rows,
    }
    atomic_json(result_root / "status.json", status)
    return status


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--result-root", required=True, type=Path)
    parser.add_argument("--shard-index", required=True, type=int, choices=(0, 1, 2))
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--graphdb-pid", required=True, type=int)
    parser.add_argument("--mixed-endpoint", required=True)
    parser.add_argument("--update-endpoint", required=True)
    parser.add_argument("--jar", required=True, type=Path)
    parser.add_argument("--java", required=True)
    parser.add_argument("--java-max-heap", default="96g")
    parser.add_argument("--graphdb-heap", default="128g")
    parser.add_argument("--reified-data", required=True, type=Path)
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument("--warmups", type=int, default=1)
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--memory-sample-interval", type=float, default=0.05)
    parser.add_argument("--exact-response-row-limit", type=int, default=1_000_000)
    parser.add_argument("--token-regex", default=r"^urn:wdbench:statement:[1-9][0-9]*$")
    args = parser.parse_args()
    args.manifest = args.manifest.resolve()
    args.source_root = args.source_root.resolve()
    args.result_root = args.result_root.resolve()
    args.jar = args.jar.resolve()
    args.reified_data = args.reified_data.resolve()
    args.result_root.mkdir(parents=True, exist_ok=True)
    if args.warmups != 1 or args.runs != 5:
        raise SystemExit("the formal protocol is exactly one warm-up and five measured runs")
    if not args.source_root.is_dir():
        raise SystemExit("source root does not exist: %s" % args.source_root)
    contract = graphdb_contract(
        args.graphdb_pid, args.graphdb_heap, args.graphdb_heap
    )
    plan = ensure_plan(args)
    session = args.result_root / "sessions" / args.session_id
    if session.exists():
        raise SystemExit("session already exists: %s" % session)
    session.mkdir(parents=True)
    atomic_json(session / "graphdb.json", contract)

    for cell in plan["cells"]:
        output = args.result_root / str(cell["output"])
        archive_partial(output, args.result_root, str(cell["cell_id"]))
        if (output / "cell.json").is_file():
            continue
        attempt = next_attempt(args.result_root, str(cell["cell_id"]))
        completed = subprocess.run(
            command(args, cell, output), text=True, capture_output=True
        )
        (attempt / "stdout.log").write_text(completed.stdout, encoding="utf-8")
        (attempt / "stderr.log").write_text(completed.stderr, encoding="utf-8")
        atomic_json(attempt / "result.json", {
            "returncode": completed.returncode,
            "cell_id": cell["cell_id"],
            "output": str(output),
        })
        if not (output / "cell.json").is_file():
            write_status(plan, args.result_root)
            return 2
        write_query_validations(plan, args.result_root)
        write_status(plan, args.result_root)
        if completed.returncode == 20:
            return 20
        if completed.returncode != 0:
            return 2

    write_query_validations(plan, args.result_root)
    status = write_status(plan, args.result_root)
    atomic_json(args.result_root / "complete.json", {
        "schema": "wikidata-141-shard-complete-v1",
        "completed_utc": datetime.now(timezone.utc).isoformat(),
        "shard_index": args.shard_index,
        "status": status,
    })
    print(json.dumps(status, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
