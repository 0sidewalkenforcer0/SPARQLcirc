#!/usr/bin/env python3
"""Run one TPC-H scale-by-engine batch as resumable immutable cells."""

import argparse
import json
import math
import os
from pathlib import Path
import subprocess
import sys
from typing import Any, Dict, List, Mapping, Optional, Sequence


HERE = Path(__file__).resolve().parent
REFERENCE = HERE.parent
if str(REFERENCE) not in sys.path:
    sys.path.insert(0, str(REFERENCE))

import event_probabilities
from tpch import prepare_data, workload


SCHEMA = "tpch-brnc-batch-v2"
RUNNER = REFERENCE / "paper" / "watdiv10m_runner.py"
FORMAL_METHODS = ("C-flat", "C-factorised")


class BatchError(RuntimeError):
    """A scale-by-engine batch cannot continue safely."""


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise BatchError("cannot read JSON %s: %s" % (path, error)) from error


def _write_json(path: Path, value: Any) -> None:
    if path.exists():
        raise BatchError("refusing to overwrite %s" % path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".partial")
    with temporary.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _configuration(args: argparse.Namespace, dataset: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "schema": SCHEMA,
        "workload_manifest": str(args.manifest.resolve()),
        "dataset_metadata": str(args.dataset.resolve()),
        "scale_factor": args.scale,
        "engine": args.engine,
        "methods": list(args.methods),
        "query_ids": list(args.query_ids) if args.query_ids else None,
        "rdf_star_profile": dataset["rdf_star_profile"],
        "rdf_star_12_permitted": dataset["rdf_star_12_permitted"],
        "provenance_granularity": dataset["provenance_granularity"],
        "base_endpoint": args.base_endpoint,
        "mixed_endpoint": args.mixed_endpoint,
        "update_endpoint": args.update_endpoint,
        "jar": str(args.jar.resolve()) if args.jar is not None else None,
        "java": args.java,
        "mixed_data": str(
            (args.dataset.resolve().parent / dataset["layouts"]["mixed"]["path"]).resolve()
        ),
        "warmups": args.warmups,
        "measured_runs": args.runs,
        "primary_statistic": "median",
        "endpoint_timeout_s": args.endpoint_timeout,
        "offline_timeout_s": args.offline_timeout,
        "complete_method_timeout_s_per_execution": args.complete_method_timeout,
        "pqe_backend": args.pqe_backend,
        "probability_seed": args.probability_seed,
        "probability_scheme": event_probabilities.PROBABILITY_SCHEME,
        "c_parallelism": 1,
        "cell_order": "workload manifest order, then requested method order",
        "failure_policy": (
            "completed cell directories are immutable; a newly failed cell stops the batch unless "
            "--continue-after-failure is set"
        ),
        "timeout_policy": (
            "each warmup or measured execution has one complete-method deadline shared by "
            "rewrite, endpoint response, artifact preparation, and PQE"
        ),
    }


def _validate_args(args: argparse.Namespace, dataset: Mapping[str, Any]) -> None:
    if args.warmups != 1 or args.runs != 5:
        raise BatchError("the formal protocol is exactly one warm-up and five measured runs")
    if str(dataset.get("scale_factor")) != args.scale:
        raise BatchError(
            "dataset scale %s does not match requested scale %s"
            % (dataset.get("scale_factor"), args.scale)
        )
    unknown = [method for method in args.methods if method not in workload.DEFAULT_METHODS]
    if unknown:
        raise BatchError("unsupported TPC-H methods: %s" % ", ".join(unknown))
    if len(set(args.methods)) != len(args.methods):
        raise BatchError("methods must be unique")
    if args.query_ids and len(set(args.query_ids)) != len(args.query_ids):
        raise BatchError("query ids must be unique")
    if not args.base_endpoint:
        raise BatchError("the batch requires --base-endpoint")
    if any(method not in ("B", "P") for method in args.methods) and not args.mixed_endpoint:
        raise BatchError("R, N, and C require --mixed-endpoint")
    if any(method in ("N", "C-flat", "C-factorised") for method in args.methods):
        if args.jar is None or not args.jar.is_file():
            raise BatchError("N and C require an existing --jar")
    if "C-factorised" in args.methods and not args.update_endpoint:
        raise BatchError("C-factorised requires --update-endpoint")
    if args.pqe_backend == "none" and args.probability_seed is not None:
        raise BatchError("a probability seed requires a PQE backend")
    if args.pqe_backend != "none" and args.probability_seed is None:
        raise BatchError("PQE requires --probability-seed")


def _runner_command(
    args: argparse.Namespace,
    entry: Mapping[str, Any],
    method: str,
    cell_directory: Path,
    workload_root: Path,
    mixed_data: Path,
) -> List[str]:
    command = [
        sys.executable,
        str(RUNNER),
        "--query", str(
            workload_root
            / entry["sparqlprov_query" if method == "P" else "base_query"]
        ),
        "--query-id", str(entry["query_id"]),
        "--engine", args.engine,
        "--method", method,
        "--workload", "tpch",
        "--scheme", "SPARQL_Star_Row",
        "--out", str(cell_directory),
        "--base-endpoint", args.base_endpoint,
        "--warmups", str(args.warmups),
        "--runs", str(args.runs),
        "--endpoint-timeout", str(args.endpoint_timeout),
        "--offline-timeout", str(args.offline_timeout),
        "--complete-method-timeout", str(args.complete_method_timeout),
        "--c-parallelism", "1",
    ]
    if method == "R":
        command.extend((
            "--expected-r-query", str(workload_root / entry["row_inline_query"]),
        ))
    if method not in ("B", "P"):
        command.extend(("--reified-endpoint", args.mixed_endpoint))
    if method in ("N", "C-flat", "C-factorised"):
        command.extend(("--jar", str(args.jar.resolve()), "--java", args.java))
    if method in ("C-flat", "C-factorised"):
        command.extend(("--reified-data", str(mixed_data), "--skip-bnode-check"))
    if method == "C-factorised":
        command.extend(("--update-endpoint", args.update_endpoint))
    if method in ("N", "C-flat", "C-factorised"):
        command.extend(("--pqe-backend", args.pqe_backend))
        if args.pqe_backend != "none":
            command.extend(("--probability-seed", str(args.probability_seed)))
    return command


def _cell_path(output: Path, entry: Mapping[str, Any], method: str) -> Path:
    return output / str(entry["template"]) / str(entry["instance"]) / method


def _cell_record(path: Path) -> Optional[Mapping[str, Any]]:
    manifest = path / "cell.json"
    if manifest.is_file():
        value = _read_json(manifest)
        if not isinstance(value, Mapping):
            raise BatchError("cell manifest is not an object: %s" % manifest)
        return value
    if path.exists():
        raise BatchError(
            "partial cell has no cell.json and must be preserved or moved before resume: %s" % path
        )
    return None


def _select_entries(
    manifest: Mapping[str, Any],
    scale: str,
    query_ids: Optional[Sequence[str]],
) -> List[Mapping[str, Any]]:
    scale_entries = [
        entry for entry in manifest["entries"]
        if str(entry["scale_factor"]) == scale
    ]
    if not scale_entries:
        raise BatchError("workload has no entries for scale %s" % scale)
    if not query_ids:
        return scale_entries
    available = {str(entry["query_id"]) for entry in scale_entries}
    unknown = [query_id for query_id in query_ids if query_id not in available]
    if unknown:
        raise BatchError(
            "query ids are not present at scale %s: %s"
            % (scale, ", ".join(unknown))
        )
    selected = set(query_ids)
    return [
        entry for entry in scale_entries if str(entry["query_id"]) in selected
    ]


def run(args: argparse.Namespace) -> Dict[str, Any]:
    workload_manifest = workload.audit_manifest(args.manifest.resolve())
    del workload_manifest  # The full manifest is loaded below after the strict audit.
    manifest = _read_json(args.manifest.resolve())
    dataset = _read_json(args.dataset.resolve())
    prepare_data.audit(args.dataset.resolve(), scan_mixed=False)
    _validate_args(args, dataset)

    entries = _select_entries(manifest, args.scale, args.query_ids)
    output = args.out.resolve()
    output.mkdir(parents=True, exist_ok=True)
    config_path = output / "batch-config.json"
    requested_config = _configuration(args, dataset)
    if config_path.exists():
        if _read_json(config_path) != requested_config:
            raise BatchError("resume configuration differs from batch-config.json")
    else:
        _write_json(config_path, requested_config)

    if (output / "batch.json").is_file():
        return _read_json(output / "batch.json")

    workload_root = args.manifest.resolve().parent
    mixed_data = Path(requested_config["mixed_data"])
    invoked = 0
    skipped = 0
    for entry in entries:
        for method in args.methods:
            cell_directory = _cell_path(output, entry, method)
            existing = _cell_record(cell_directory)
            if existing is not None:
                if (
                    existing.get("query_id") != entry["query_id"]
                    or existing.get("engine") != args.engine
                    or existing.get("method") != method
                    or existing.get("workload") != "tpch"
                    or existing.get("scheme") != "SPARQL_Star_Row"
                ):
                    raise BatchError("existing cell identity mismatch: %s" % cell_directory)
                skipped += 1
                continue

            command = _runner_command(
                args, entry, method, cell_directory, workload_root, mixed_data
            )
            completed = subprocess.run(command, check=False)
            invoked += 1
            created = _cell_record(cell_directory)
            if created is None:
                raise BatchError("runner returned without a cell artifact: %s" % cell_directory)
            if completed.returncode != 0 and not args.continue_after_failure:
                raise BatchError(
                    "cell ended with status %s; recover the endpoint if needed and rerun the batch: %s"
                    % (created.get("status"), cell_directory)
                )

    records = [
        _cell_record(_cell_path(output, entry, method))
        for entry in entries
        for method in args.methods
    ]
    if any(record is None for record in records):
        raise BatchError("batch ended before every cell became terminal")
    complete = sum(record.get("status") == "ok" for record in records if record is not None)
    result = {
        "schema": SCHEMA,
        "status": "terminal",
        "scale_factor": args.scale,
        "engine": args.engine,
        "expected_cells": len(records),
        "successful_cells": complete,
        "non_successful_cells": len(records) - complete,
        "invoked_in_final_pass": invoked,
        "reused_in_final_pass": skipped,
        "batch_config": str(config_path),
    }
    _write_json(output / "batch.json", result)
    return result


def _positive(value: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("value must be positive and finite")
    return number


def _nonnegative_integer(value: str) -> int:
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError("value must be non-negative")
    return number


def _positive_integer(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("value must be positive")
    return number


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--scale", required=True)
    parser.add_argument("--engine", required=True)
    parser.add_argument("--method", dest="methods", action="append")
    parser.add_argument(
        "--query-id",
        dest="query_ids",
        action="append",
        help="run only the named frozen query instance; repeat to select several",
    )
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--base-endpoint", required=True)
    parser.add_argument("--mixed-endpoint")
    parser.add_argument("--update-endpoint")
    parser.add_argument("--jar", type=Path)
    parser.add_argument("--java", default="java")
    parser.add_argument("--warmups", type=_nonnegative_integer, default=1)
    parser.add_argument("--runs", type=_positive_integer, default=5)
    parser.add_argument("--endpoint-timeout", type=_positive, default=3000.0)
    parser.add_argument("--offline-timeout", type=_positive, default=3000.0)
    parser.add_argument(
        "--complete-method-timeout",
        type=_positive,
        default=3000.0,
        help=(
            "deadline in seconds shared by all stages of each warmup or measured "
            "execution (default: 3000)"
        ),
    )
    parser.add_argument("--pqe-backend", choices=("none", "oracle", "cudd"), default="cudd")
    parser.add_argument("--probability-seed", type=_nonnegative_integer)
    parser.add_argument("--continue-after-failure", action="store_true")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    args.methods = tuple(args.methods or FORMAL_METHODS)
    if args.pqe_backend != "none" and args.probability_seed is None:
        args.probability_seed = event_probabilities.DEFAULT_PROBABILITY_SEED
    try:
        result = run(args)
    except (OSError, ValueError, BatchError, workload.WorkloadError,
            prepare_data.PreparationError) as error:
        parser.exit(2, "tpch batch: error: %s\n" % error)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
