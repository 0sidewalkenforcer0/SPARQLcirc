#!/usr/bin/env python3
"""Summarize relational TPC-H cells without conflating RDF timing fields."""

import argparse
import csv
import json
import math
import os
from pathlib import Path
import statistics
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


SCHEMA = "tpch-provsql-summary-v1"
WORKLOAD_SCHEMA = "tpch-provsql-workload-v1"
CELL_SCHEMA = "tpch-provsql-cell-v1"
METHODS = ("PG-B", "ProvSQL")
FORMAL_METHODS = ("ProvSQL",)
COUNTERFACTUAL_PHASES = frozenset((
    "query_serialization_transfer",
    "relational_data_path_warmup",
    "provenance_construction",
    "root_serialization_transfer",
    "pqe_compute",
    "probability_serialization_transfer",
))


class ProvsqlSummaryError(RuntimeError):
    """Relational TPC-H artifacts do not form an unambiguous summary."""


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ProvsqlSummaryError("cannot read JSON %s: %s" % (path, error)) from error


def _write_text(path: Path, value: str) -> None:
    if path.exists():
        raise ProvsqlSummaryError("refusing to overwrite %s" % path)
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".partial")
    with partial.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(value)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(partial, path)


def _median(values: Sequence[float]) -> Optional[float]:
    return round(statistics.median(values), 6) if values else None


def _number(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _flatten_numbers(value: Any, prefix: str = "") -> Iterable[Tuple[str, float]]:
    if isinstance(value, Mapping):
        for key, child in value.items():
            child_prefix = str(key) if not prefix else prefix + "." + str(key)
            yield from _flatten_numbers(child, child_prefix)
    elif not isinstance(value, list):
        number = _number(value)
        if number is not None:
            yield prefix, number


def _measured_runs(cell: Mapping[str, Any]) -> List[Mapping[str, Any]]:
    return [run for run in cell.get("runs", []) if isinstance(run, Mapping)]


def _expected_measured_runs(cell: Mapping[str, Any]) -> int:
    protocol = cell.get("protocol")
    if isinstance(protocol, Mapping) and protocol.get("measured_runs") is not None:
        try:
            expected = int(protocol["measured_runs"])
        except (TypeError, ValueError) as error:
            raise ProvsqlSummaryError("cell has an invalid measured-run contract") from error
        if expected < 1:
            raise ProvsqlSummaryError("cell measured-run contract must be positive")
        return expected
    return len(_measured_runs(cell))


def _validate_formal_protocol(cell: Mapping[str, Any]) -> None:
    protocol = cell.get("protocol")
    if not isinstance(protocol, Mapping):
        raise ProvsqlSummaryError("cell has no formal protocol record")
    if (
        protocol.get("warmups") != 1
        or protocol.get("measured_runs") != 5
        or protocol.get("primary_statistic") != "median"
    ):
        raise ProvsqlSummaryError(
            "TPC-H cells must use one warm-up, five measured executions, and median"
        )


def _status(cell: Optional[Mapping[str, Any]]) -> str:
    if cell is None:
        return "missing"
    runs = _measured_runs(cell)
    expected = _expected_measured_runs(cell)
    if (
        cell.get("status") == "ok"
        and len(runs) == expected
        and all(run.get("status", "ok") == "ok" for run in runs)
    ):
        return "ok"
    values = [str(cell.get("status", ""))]
    values.extend(
        str(run_value.get("status", ""))
        for run_value in cell.get("runs", [])
        if isinstance(run_value, Mapping)
    )
    return "timeout" if any("timeout" in value for value in values) else "failed"


def _phase(run: Mapping[str, Any], name: str) -> Optional[Mapping[str, Any]]:
    matches = [
        value for value in run.get("phases", [])
        if isinstance(value, Mapping) and value.get("name") == name
    ]
    if len(matches) > 1:
        raise ProvsqlSummaryError("measured run repeats phase %s" % name)
    return matches[0] if matches else None


def _phase_ms(run: Mapping[str, Any], name: str) -> Optional[float]:
    value = _phase(run, name)
    return _number(value.get("client_wall_ms")) if value is not None else None


def _run_metrics(run: Mapping[str, Any]) -> Iterable[Tuple[str, float]]:
    for key, value in run.items():
        if key not in ("phases", "run"):
            yield from _flatten_numbers(value, str(key))
    for phase in run.get("phases", []):
        if not isinstance(phase, Mapping) or not isinstance(phase.get("name"), str):
            continue
        name = str(phase["name"])
        for key, value in phase.items():
            if key != "name":
                yield from _flatten_numbers(value, "phase.%s.%s" % (name, key))


def _primary_ms(method: str, run: Mapping[str, Any]) -> Optional[float]:
    field = "full_end_to_end_ms" if method == "PG-B" else "artifact_complete_total_ms"
    return _number(run.get(field))


def _cell_phases(cell: Mapping[str, Any]) -> Iterable[Mapping[str, Any]]:
    for warmup in cell.get("warmups", []):
        if not isinstance(warmup, Mapping):
            continue
        nested = warmup.get("phases")
        if isinstance(nested, list):
            yield from (phase for phase in nested if isinstance(phase, Mapping))
        elif isinstance(warmup.get("name"), str):
            yield warmup
    for run in cell.get("runs", []):
        if not isinstance(run, Mapping):
            continue
        yield from (
            phase for phase in run.get("phases", [])
            if isinstance(phase, Mapping)
        )


def _counterfactual_phase_timeout(
    cell: Optional[Mapping[str, Any]],
    timeout_s: float,
) -> Dict[str, Any]:
    """Classify a cell under an independent timeout for each core phase."""
    if cell is None:
        return {"status": "missing", "phase": None, "reason": "cell is absent"}
    observed = False
    threshold_ms = timeout_s * 1000.0
    for phase in _cell_phases(cell):
        name = str(phase.get("name", ""))
        if name not in COUNTERFACTUAL_PHASES:
            continue
        observed = True
        status = str(phase.get("status", ""))
        wall_ms = _number(phase.get("client_wall_ms"))
        observed_timeout_s = _number(phase.get("timeout_s"))
        evidence = {
            "phase": name,
            "observed_wall_ms": wall_ms,
            "observed_phase_timeout_s": observed_timeout_s,
        }
        if status == "ok":
            if wall_ms is not None and wall_ms > threshold_ms:
                return {
                    **evidence,
                    "status": "would-timeout",
                    "reason": "completed phase exceeded the counterfactual limit",
                }
            continue
        if "timeout" in status:
            if (
                (observed_timeout_s is not None and observed_timeout_s >= timeout_s)
                or (wall_ms is not None and wall_ms >= threshold_ms)
            ):
                return {
                    **evidence,
                    "status": "would-timeout",
                    "reason": "observed timeout establishes the lower limit",
                }
            return {
                **evidence,
                "status": "unknown",
                "reason": "observed phase was stopped before the counterfactual limit",
            }
        return {
            **evidence,
            "status": "unknown",
            "reason": "phase failed for a reason other than timeout",
        }
    if not observed:
        return {
            "status": "unknown",
            "phase": None,
            "reason": "no core phase timing is available",
        }
    return {
        "status": "would-complete",
        "phase": None,
        "reason": "every observed core phase completed within the limit",
    }


def summarize(
    manifest_path: Path,
    cell_roots: Sequence[Path],
    expected_methods: Optional[Sequence[str]] = None,
    expected_instances: Optional[Sequence[str]] = None,
    expected_scales: Optional[Sequence[str]] = None,
    counterfactual_phase_timeout_s: Optional[float] = None,
) -> Dict[str, Any]:
    manifest = _read_json(manifest_path)
    if not isinstance(manifest, Mapping) or manifest.get("schema") != WORKLOAD_SCHEMA:
        raise ProvsqlSummaryError("unsupported ProvSQL workload manifest")
    all_entries = manifest.get("entries")
    if not isinstance(all_entries, list) or not all_entries:
        raise ProvsqlSummaryError("ProvSQL workload manifest has no entries")
    selected_instances = set(str(value) for value in (expected_instances or ()))
    available_instances = {str(entry.get("instance")) for entry in all_entries}
    unknown_instances = selected_instances.difference(available_instances)
    if unknown_instances:
        raise ProvsqlSummaryError(
            "workload does not contain instances: %s"
            % ", ".join(sorted(unknown_instances))
        )
    selected_scales = set(str(value) for value in (expected_scales or ()))
    available_scales = {str(value) for value in manifest.get("scale_factors", [])}
    unknown_scales = selected_scales.difference(available_scales)
    if unknown_scales:
        raise ProvsqlSummaryError(
            "workload does not contain scales: %s" % ", ".join(sorted(unknown_scales))
        )
    entries = [
        entry for entry in all_entries
        if not selected_instances or str(entry.get("instance")) in selected_instances
        if not selected_scales or str(entry.get("scale_factor")) in selected_scales
    ]
    expected = {str(entry["query_id"]): entry for entry in entries}
    if len(expected) != len(entries):
        raise ProvsqlSummaryError("workload manifest contains duplicate query identifiers")

    methods = tuple(expected_methods or FORMAL_METHODS)
    if len(set(methods)) != len(methods) or set(methods).difference(METHODS):
        raise ProvsqlSummaryError("expected methods must be unique PG-B/ProvSQL values")

    cells: Dict[Tuple[str, str], Mapping[str, Any]] = {}
    ignored = 0
    for root in cell_roots:
        for path in root.rglob("cell.json"):
            value = _read_json(path)
            if not isinstance(value, Mapping) or value.get("schema") != CELL_SCHEMA:
                ignored += 1
                continue
            query_id = str(value.get("query_id"))
            if query_id not in expected:
                ignored += 1
                continue
            entry = expected[query_id]
            if any(
                str(value.get(field)) != str(entry.get(field))
                for field in ("scale_factor", "template", "instance")
            ):
                raise ProvsqlSummaryError("cell identity differs from workload: %s" % path)
            _validate_formal_protocol(value)
            method = str(value.get("method"))
            if method not in METHODS:
                raise ProvsqlSummaryError("unsupported relational method in %s" % path)
            key = (query_id, method)
            if key in cells:
                raise ProvsqlSummaryError("duplicate cell for %s/%s" % key)
            cells[key] = value

    groups: List[Dict[str, Any]] = []
    scales = [
        str(scale) for scale in manifest["scale_factors"]
        if not selected_scales or str(scale) in selected_scales
    ]
    for scale in scales:
        for template in manifest["templates"]:
            query_ids = sorted(
                (
                    query_id for query_id, entry in expected.items()
                    if str(entry["scale_factor"]) == str(scale)
                    and str(entry["template"]) == str(template)
                ),
                key=lambda query_id: str(expected[query_id]["instance"]),
            )
            for method in methods:
                statuses: Dict[str, str] = {}
                primary_values: List[float] = []
                native_values: List[float] = []
                artifact_values: List[float] = []
                construction_values: List[float] = []
                pqe_values: List[float] = []
                metric_values: Dict[str, List[float]] = {}
                counterfactual: Dict[str, Dict[str, Any]] = {}
                for query_id in query_ids:
                    cell = cells.get((query_id, method))
                    if counterfactual_phase_timeout_s is not None:
                        counterfactual[query_id] = _counterfactual_phase_timeout(
                            cell, counterfactual_phase_timeout_s
                        )
                    status = _status(cell)
                    statuses[query_id] = status
                    if status != "ok" or cell is None:
                        continue
                    runs = _measured_runs(cell)
                    if not runs:
                        raise ProvsqlSummaryError(
                            "successful cell has no measured runs: %s" % query_id
                        )
                    per_run: Dict[str, List[float]] = {
                        "primary": [],
                        "native": [],
                        "artifact": [],
                        "construction": [],
                        "pqe": [],
                    }
                    per_run_metrics: Dict[str, List[float]] = {}
                    for run in runs:
                        values = {
                            "primary": _primary_ms(method, run),
                            "native": _number(run.get("native_database_total_ms")),
                            "artifact": _number(run.get("artifact_complete_total_ms")),
                            "construction": _phase_ms(run, "provenance_construction"),
                            "pqe": _phase_ms(run, "pqe_compute"),
                        }
                        for name, value in values.items():
                            if value is not None:
                                per_run[name].append(value)
                        for metric, number in _run_metrics(run):
                            per_run_metrics.setdefault(metric, []).append(number)
                    targets = {
                        "primary": primary_values,
                        "native": native_values,
                        "artifact": artifact_values,
                        "construction": construction_values,
                        "pqe": pqe_values,
                    }
                    for name, values in per_run.items():
                        if len(values) == len(runs):
                            targets[name].append(float(statistics.median(values)))
                    for metric, values in per_run_metrics.items():
                        if len(values) == len(runs):
                            metric_values.setdefault(metric, []).append(
                                float(statistics.median(values))
                            )

                expected_count = len(query_ids)
                success_count = sum(value == "ok" for value in statuses.values())
                complete = success_count == expected_count

                def publication_median(values: Sequence[float]) -> Optional[float]:
                    return _median(values) if complete and len(values) == expected_count else None

                groups.append({
                    "scale_factor": str(scale),
                    "template": str(template),
                    "method": method,
                    "expected_instances": expected_count,
                    "successful_instances": success_count,
                    "timeout_instances": sum(value == "timeout" for value in statuses.values()),
                    "failed_instances": sum(value == "failed" for value in statuses.values()),
                    "missing_instances": sum(value == "missing" for value in statuses.values()),
                    "complete": complete,
                    "median_primary_total_ms": publication_median(primary_values),
                    "median_native_database_total_ms": publication_median(native_values),
                    "median_artifact_complete_total_ms": publication_median(artifact_values),
                    "median_provenance_construction_ms": publication_median(construction_values),
                    "median_pqe_compute_ms": publication_median(pqe_values),
                    "available_median_primary_total_ms": _median(primary_values),
                    "counterfactual_timeout_instances": (
                        sum(
                            value["status"] == "would-timeout"
                            for value in counterfactual.values()
                        )
                        if counterfactual_phase_timeout_s is not None else None
                    ),
                    "counterfactual_complete_instances": (
                        sum(
                            value["status"] == "would-complete"
                            for value in counterfactual.values()
                        )
                        if counterfactual_phase_timeout_s is not None else None
                    ),
                    "counterfactual_unknown_instances": (
                        sum(
                            value["status"] in ("unknown", "missing")
                            for value in counterfactual.values()
                        )
                        if counterfactual_phase_timeout_s is not None else None
                    ),
                    "counterfactual_instance_status": counterfactual,
                    "complete_metric_medians": {
                        metric: _median(values)
                        for metric, values in sorted(metric_values.items())
                        if complete and len(values) == expected_count
                    },
                    "instance_status": statuses,
                })

    instance_names = sorted({str(entry["instance"]) for entry in entries})
    return {
        "schema": SCHEMA,
        "aggregation": (
            "median of five measured executions for %s" % instance_names[0]
            if len(instance_names) == 1
            else "median per cell, then median across selected query instances"
        ),
        "selected_instances": instance_names,
        "selected_scales": scales,
        "primary_total_definition": {
            "PG-B": "SQL execution, CSV serialization, and local transfer",
            "ProvSQL": "construction, root export, in-database PQE, and probability export",
        },
        "incomplete_group_policy": (
            "publication median is null unless every selected instance succeeds"
        ),
        "counterfactual_phase_timeout_s": counterfactual_phase_timeout_s,
        "counterfactual_timeout_scope": (
            "warm-up and measured core phases, applying the limit independently "
            "to each phase"
            if counterfactual_phase_timeout_s is not None else None
        ),
        "workload_manifest": str(manifest_path.resolve()),
        "cell_roots": [str(path.resolve()) for path in cell_roots],
        "methods": list(methods),
        "ignored_cell_json_files": ignored,
        "groups": groups,
    }


def write_outputs(result: Mapping[str, Any], json_path: Path, csv_path: Path) -> None:
    if json_path.exists() or csv_path.exists():
        raise ProvsqlSummaryError("refusing to overwrite summary output")
    _write_text(
        json_path,
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    columns = (
        "scale_factor", "template", "method", "expected_instances",
        "successful_instances", "timeout_instances", "failed_instances",
        "missing_instances", "complete", "median_primary_total_ms",
        "median_native_database_total_ms", "median_artifact_complete_total_ms",
        "median_provenance_construction_ms", "median_pqe_compute_ms",
        "available_median_primary_total_ms", "counterfactual_timeout_instances",
        "counterfactual_complete_instances", "counterfactual_unknown_instances",
    )
    rows = []
    for group in result["groups"]:
        rows.append({column: group[column] for column in columns})
    if csv_path.exists():
        raise ProvsqlSummaryError("refusing to overwrite %s" % csv_path)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    partial = csv_path.with_name(csv_path.name + ".partial")
    with partial.open("x", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(partial, csv_path)


def _positive(value: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("value must be positive and finite")
    return number


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--cells", required=True, action="append", type=Path)
    parser.add_argument("--method", dest="methods", action="append", choices=METHODS)
    parser.add_argument("--instance", dest="instances", action="append")
    parser.add_argument("--scale", dest="scales", action="append")
    parser.add_argument("--counterfactual-phase-timeout", type=_positive)
    parser.add_argument("--json", required=True, type=Path)
    parser.add_argument("--csv", required=True, type=Path)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        result = summarize(
            args.manifest.resolve(),
            [path.resolve() for path in args.cells],
            args.methods,
            args.instances,
            args.scales,
            args.counterfactual_phase_timeout,
        )
        write_outputs(result, args.json.resolve(), args.csv.resolve())
    except (OSError, ValueError, ProvsqlSummaryError) as error:
        parser.exit(2, "ProvSQL summary: error: %s\n" % error)
    print(json.dumps({"status": "ok", "groups": len(result["groups"])}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
