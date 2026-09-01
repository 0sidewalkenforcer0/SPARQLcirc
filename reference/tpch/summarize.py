#!/usr/bin/env python3
"""Summarize TPC-H cell JSON with medians over measured executions."""

import argparse
import copy
import csv
import json
import math
from pathlib import Path
import statistics
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


SCHEMA = "tpch-brnc-summary-v1"
CELL_SCHEMA = "watdiv-brnc-cell-v1"
OFFLINE_RESUME_SCHEMA = "watdiv-brnc-offline-resume-v1"
DEFAULT_ENGINES = ("graphdb", "fuseki", "oxigraph")
DEFAULT_METHODS = ("B", "R", "N", "C-flat", "C-factorised")


class SummaryError(RuntimeError):
    """Raw cell artifacts do not form an unambiguous summary."""


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SummaryError("cannot read JSON %s: %s" % (path, error)) from error


def _median(values: Sequence[float]) -> Optional[float]:
    if not values:
        return None
    return round(statistics.median(values), 6)


def _flatten_numbers(value: Any, prefix: str = "") -> Iterable[Tuple[str, float]]:
    if isinstance(value, Mapping):
        for key, child in value.items():
            child_prefix = str(key) if not prefix else prefix + "." + str(key)
            yield from _flatten_numbers(child, child_prefix)
    elif isinstance(value, list):
        return
    elif isinstance(value, bool) or value is None:
        return
    elif isinstance(value, (int, float)) and math.isfinite(float(value)):
        yield prefix, float(value)


def _measured_runs(cell: Mapping[str, Any]) -> List[Mapping[str, Any]]:
    return [
        run for run in cell.get("runs", [])
        if isinstance(run, Mapping) and run.get("phase") == "measured"
    ]


def _expected_measured_runs(cell: Mapping[str, Any]) -> int:
    protocol = cell.get("protocol")
    if isinstance(protocol, Mapping) and protocol.get("measured_runs") is not None:
        try:
            expected = int(protocol["measured_runs"])
        except (TypeError, ValueError) as error:
            raise SummaryError("cell has an invalid measured-run contract") from error
        if expected < 1:
            raise SummaryError("cell measured-run contract must be positive")
        return expected
    return len(_measured_runs(cell))


def _validate_formal_protocol(cell: Mapping[str, Any]) -> None:
    protocol = cell.get("protocol")
    if not isinstance(protocol, Mapping):
        raise SummaryError("cell has no formal protocol record")
    if (
        protocol.get("warmups") != 1
        or protocol.get("measured_runs") != 5
        or protocol.get("primary_statistic") != "median"
    ):
        raise SummaryError(
            "TPC-H cells must use one warm-up, five measured executions, and median"
        )


def _effective_cell(cell_path: Path, source: Mapping[str, Any]) -> Mapping[str, Any]:
    """Overlay a successful immutable offline-resume manifest on its source cell."""
    cell_directory = cell_path.parent
    for manifest_path in reversed(sorted(cell_directory.glob("offline-resume-*/resume.json"))):
        resume = _read_json(manifest_path)
        if (
            not isinstance(resume, Mapping)
            or resume.get("schema") != OFFLINE_RESUME_SCHEMA
            or resume.get("status") != "ok"
        ):
            continue
        effective = copy.deepcopy(source)
        runs = {
            str(run.get("run_id")): run
            for run in effective.get("runs", [])
            if isinstance(run, Mapping) and run.get("run_id") is not None
        }
        usable = True
        for resumed_run in resume.get("runs", []):
            run_id = str(resumed_run.get("run_id"))
            target = runs.get(run_id)
            artifact = resumed_run.get("offline_artifact_run")
            if target is None or not artifact or resumed_run.get("offline_status") != "ok":
                usable = False
                break
            offline_path = cell_directory / str(artifact) / "offline-result.json"
            if not offline_path.is_file():
                usable = False
                break
            offline = _read_json(offline_path)
            if not isinstance(offline, Mapping) or offline.get("status") != "ok":
                usable = False
                break
            target["offline"] = offline
            target["status"] = "ok"
            target["component_method_e2e_ms"] = resumed_run.get(
                "component_method_e2e_ms"
            )
        if usable:
            effective["status"] = "ok"
            effective["effective_offline_resume"] = manifest_path.parent.name
            return effective
    return source


def _status(cell: Optional[Mapping[str, Any]]) -> str:
    if cell is None:
        return "missing"
    measured = _measured_runs(cell)
    expected = _expected_measured_runs(cell)
    if (
        cell.get("status") == "ok"
        and len(measured) == expected
        and all(run.get("status") == "ok" for run in measured)
    ):
        return "ok"
    statuses = [str(cell.get("status", ""))]
    statuses.extend(
        str(run.get("status", ""))
        for run in cell.get("runs", [])
        if isinstance(run, Mapping)
    )
    return "timeout" if any("timeout" in value for value in statuses) else "failed"


def _primary_times(run: Mapping[str, Any]) -> Tuple[Optional[float], Optional[float]]:
    endpoint = run.get("endpoint")
    endpoint_ms = None
    if isinstance(endpoint, Mapping) and isinstance(endpoint.get("endpoint"), Mapping):
        value = endpoint["endpoint"].get("endpoint_e2e_ms")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            endpoint_ms = float(value)
    value = run.get("component_method_e2e_ms")
    method_ms = (
        float(value)
        if isinstance(value, (int, float)) and not isinstance(value, bool)
        else None
    )
    return endpoint_ms, method_ms


def summarize(
    manifest_path: Path,
    cell_roots: Sequence[Path],
    expected_engines: Optional[Sequence[str]] = None,
    expected_methods: Optional[Sequence[str]] = None,
    expected_instances: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    manifest = _read_json(manifest_path)
    if manifest.get("rdf_star_profile") != "RDF-star 1.1 quoted triple plus occurrenceOf":
        raise SummaryError("workload does not declare the RDF-star 1.1 profile")
    if manifest.get("rdf_star_12_permitted") is not False:
        raise SummaryError("workload does not prohibit RDF 1.2 reification")
    if manifest.get("provenance_granularity") != (
        "one token per TPC-H row rdf:type marker"
    ):
        raise SummaryError("workload does not use per-row provenance")
    all_entries = manifest.get("entries")
    if not isinstance(all_entries, list) or not all_entries:
        raise SummaryError("workload manifest has no entries")
    selected_instances = set(expected_instances or ())
    available_instances = {str(entry["instance"]) for entry in all_entries}
    unknown_instances = selected_instances.difference(available_instances)
    if unknown_instances:
        raise SummaryError(
            "workload does not contain instances: %s"
            % ", ".join(sorted(unknown_instances))
        )
    entries = [
        entry for entry in all_entries
        if not selected_instances or str(entry["instance"]) in selected_instances
    ]
    expected = {
        str(entry["query_id"]): {
            "scale_factor": str(entry["scale_factor"]),
            "template": str(entry["template"]),
            "instance": str(entry["instance"]),
        }
        for entry in entries
    }
    if len(expected) != len(entries):
        raise SummaryError("workload manifest contains duplicate query identifiers")

    cells: Dict[Tuple[str, str, str], Mapping[str, Any]] = {}
    ignored = 0
    for root in cell_roots:
        for path in root.rglob("cell.json"):
            cell = _read_json(path)
            if not isinstance(cell, Mapping) or cell.get("schema") != CELL_SCHEMA:
                ignored += 1
                continue
            cell = _effective_cell(path, cell)
            query_id = str(cell.get("query_id"))
            if query_id not in expected:
                ignored += 1
                continue
            if cell.get("workload") != "tpch" or cell.get("scheme") != "SPARQL_Star_Row":
                raise SummaryError("TPC-H cell does not use SPARQL_Star_Row: %s" % path)
            _validate_formal_protocol(cell)
            key = (query_id, str(cell.get("engine")), str(cell.get("method")))
            if key in cells:
                raise SummaryError("duplicate cell for %s/%s/%s" % key)
            cells[key] = cell

    observed_engines = {key[1] for key in cells}
    observed_methods = {key[2] for key in cells}
    engines = list(expected_engines) if expected_engines is not None else sorted(observed_engines)
    methods = list(expected_methods) if expected_methods is not None else sorted(
        observed_methods,
        key=lambda item: (
            DEFAULT_METHODS.index(item) if item in DEFAULT_METHODS else len(DEFAULT_METHODS),
            item,
        ),
    )
    if len(set(engines)) != len(engines) or len(set(methods)) != len(methods):
        raise SummaryError("expected engines and methods must be unique")
    unexpected_engines = observed_engines - set(engines)
    unexpected_methods = observed_methods - set(methods)
    if unexpected_engines or unexpected_methods:
        raise SummaryError(
            "cells contain unexpected engines or methods: engines=%s methods=%s"
            % (sorted(unexpected_engines), sorted(unexpected_methods))
        )
    groups: List[Dict[str, Any]] = []
    for scale in manifest["scale_factors"]:
        for template in manifest["templates"]:
            query_ids = [
                query_id for query_id, identity in expected.items()
                if identity["scale_factor"] == str(scale) and identity["template"] == template
            ]
            query_ids.sort(key=lambda query_id: expected[query_id]["instance"])
            for engine in engines:
                for method in methods:
                    statuses: Dict[str, str] = {}
                    endpoint_values: List[float] = []
                    method_values: List[float] = []
                    metric_values: Dict[str, List[float]] = {}
                    for query_id in query_ids:
                        cell = cells.get((query_id, engine, method))
                        status = _status(cell)
                        statuses[query_id] = status
                        if status != "ok" or cell is None:
                            continue
                        measured = _measured_runs(cell)
                        if not measured:
                            raise SummaryError("successful cell has no measured runs: %s" % query_id)
                        run_endpoint_values: List[float] = []
                        run_method_values: List[float] = []
                        run_metric_values: Dict[str, List[float]] = {}
                        for run in measured:
                            endpoint_ms, method_ms = _primary_times(run)
                            if endpoint_ms is not None:
                                run_endpoint_values.append(endpoint_ms)
                            if method_ms is not None:
                                run_method_values.append(method_ms)
                            for metric, value in _flatten_numbers(run):
                                run_metric_values.setdefault(metric, []).append(value)
                        if len(run_endpoint_values) == len(measured):
                            endpoint_values.append(float(statistics.median(run_endpoint_values)))
                        if len(run_method_values) == len(measured):
                            method_values.append(float(statistics.median(run_method_values)))
                        for metric, values in run_metric_values.items():
                            if len(values) == len(measured):
                                metric_values.setdefault(metric, []).append(
                                    float(statistics.median(values))
                                )

                    success_count = sum(value == "ok" for value in statuses.values())
                    complete = success_count == len(query_ids)
                    complete_metrics = {
                        metric: _median(values)
                        for metric, values in sorted(metric_values.items())
                        if complete and len(values) == len(query_ids)
                    }
                    groups.append({
                        "scale_factor": str(scale),
                        "template": template,
                        "engine": engine,
                        "method": method,
                        "expected_instances": len(query_ids),
                        "successful_instances": success_count,
                        "timeout_instances": sum(value == "timeout" for value in statuses.values()),
                        "failed_instances": sum(value == "failed" for value in statuses.values()),
                        "missing_instances": sum(value == "missing" for value in statuses.values()),
                        "complete": complete,
                        "median_endpoint_e2e_ms": (
                            _median(endpoint_values)
                            if complete and len(endpoint_values) == len(query_ids)
                            else None
                        ),
                        "median_component_method_e2e_ms": (
                            _median(method_values)
                            if complete and len(method_values) == len(query_ids)
                            else None
                        ),
                        "available_median_endpoint_e2e_ms": _median(endpoint_values),
                        "available_median_component_method_e2e_ms": _median(method_values),
                        "complete_metric_medians": complete_metrics,
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
        "incomplete_group_policy": (
            "publication median is null unless all expected instances succeed; available medians are diagnostic"
        ),
        "workload_manifest": str(manifest_path.resolve()),
        "cell_roots": [str(path.resolve()) for path in cell_roots],
        "engines": engines,
        "methods": methods,
        "ignored_cell_json_files": ignored,
        "groups": groups,
    }


def write_outputs(result: Mapping[str, Any], json_path: Path, csv_path: Path) -> None:
    if json_path.exists() or csv_path.exists():
        raise SummaryError("refusing to overwrite summary output")
    json_path.parent.mkdir(parents=True, exist_ok=True)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    columns = (
        "scale_factor", "template", "engine", "method", "expected_instances",
        "successful_instances", "timeout_instances", "failed_instances",
        "missing_instances", "complete", "median_endpoint_e2e_ms",
        "median_component_method_e2e_ms", "available_median_endpoint_e2e_ms",
        "available_median_component_method_e2e_ms",
    )
    with csv_path.open("x", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for group in result["groups"]:
            writer.writerow({column: group[column] for column in columns})


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--cells", required=True, action="append", type=Path)
    parser.add_argument("--engine", dest="engines", action="append")
    parser.add_argument("--method", dest="methods", action="append", choices=DEFAULT_METHODS)
    parser.add_argument(
        "--instance", dest="instances", action="append",
        help="summarize only this frozen instance; repeat to select several",
    )
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
            args.engines or DEFAULT_ENGINES,
            args.methods or DEFAULT_METHODS,
            args.instances,
        )
        write_outputs(result, args.json.resolve(), args.csv.resolve())
    except (OSError, ValueError, SummaryError) as error:
        parser.exit(1, "tpch summary: error: %s\n" % error)
    print(json.dumps({"status": "ok", "groups": len(result["groups"])}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
