"""Regression tests for median aggregation of relational TPC-H cells."""

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


MODULE_PATH = Path(__file__).resolve().parents[1] / "tpch" / "provsql_summarize.py"
SPEC = importlib.util.spec_from_file_location("tpch_provsql_summarize", MODULE_PATH)
provsql_summarize = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(provsql_summarize)


def manifest(root: Path) -> Path:
    entries = [
        {
            "query_id": "sf0p01-Q03-q%03d" % index,
            "scale_factor": "0.01",
            "template": "Q03",
            "instance": "q%03d" % index,
        }
        for index in (1, 2)
    ]
    path = root / "manifest.json"
    path.write_text(json.dumps({
        "schema": provsql_summarize.WORKLOAD_SCHEMA,
        "scale_factors": ["0.01"],
        "templates": ["Q03"],
        "instances": ["q001", "q002"],
        "entries": entries,
    }), encoding="utf-8")
    return path


def cell(
    root: Path,
    index: int,
    method: str,
    primary_ms: float,
    status: str = "ok",
) -> None:
    directory = root / ("cell-%s-%d" % (method, index))
    directory.mkdir(parents=True)
    runs = []
    for run_number in range(1, 6):
        run = {
            "run": run_number,
            "status": status,
            "phases": [],
        }
        if method == "PG-B":
            run["full_end_to_end_ms"] = primary_ms
            run["serialized_bytes"] = 100 * index
        else:
            run.update({
                "artifact_complete_total_ms": primary_ms,
                "native_database_total_ms": primary_ms - 5.0,
                "phases": [
                    {"name": "provenance_construction", "client_wall_ms": 10.0 * index},
                    {"name": "pqe_compute", "client_wall_ms": 5.0 * index},
                ],
            })
        runs.append(run)
    value = {
        "schema": provsql_summarize.CELL_SCHEMA,
        "status": "ok" if status == "ok" else status,
        "query_id": "sf0p01-Q03-q%03d" % index,
        "scale_factor": "0.01",
        "template": "Q03",
        "instance": "q%03d" % index,
        "method": method,
        "protocol": {
            "warmups": 1,
            "measured_runs": 5,
            "primary_statistic": "median",
        },
        "runs": runs,
    }
    (directory / "cell.json").write_text(json.dumps(value), encoding="utf-8")


class TpchProvsqlSummarizeTest(unittest.TestCase):
    def test_complete_groups_use_medians_and_distinct_totals(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workload = manifest(root)
            cells = root / "cells"
            cells.mkdir()
            cell(cells, 1, "PG-B", 10.0)
            cell(cells, 2, "PG-B", 30.0)
            cell(cells, 1, "ProvSQL", 40.0)
            cell(cells, 2, "ProvSQL", 60.0)

            result = provsql_summarize.summarize(
                workload, [cells], expected_methods=("PG-B", "ProvSQL")
            )

        pg_b, provsql = result["groups"]
        self.assertEqual(20.0, pg_b["median_primary_total_ms"])
        self.assertIsNone(pg_b["median_native_database_total_ms"])
        self.assertEqual(50.0, provsql["median_primary_total_ms"])
        self.assertEqual(45.0, provsql["median_native_database_total_ms"])
        self.assertEqual(15.0, provsql["median_provenance_construction_ms"])
        self.assertEqual("median per cell, then median across selected query instances", result["aggregation"])

    def test_timeout_suppresses_the_publication_median(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workload = manifest(root)
            cells = root / "cells"
            cells.mkdir()
            cell(cells, 1, "ProvSQL", 40.0)
            cell(cells, 2, "ProvSQL", 600000.0, status="timeout")

            result = provsql_summarize.summarize(
                workload, [cells], expected_methods=("ProvSQL",)
            )

        group = result["groups"][0]
        self.assertFalse(group["complete"])
        self.assertEqual(1, group["timeout_instances"])
        self.assertIsNone(group["median_primary_total_ms"])
        self.assertEqual(40.0, group["available_median_primary_total_ms"])

    def test_single_instance_selection_ignores_unselected_cells(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workload = manifest(root)
            cells = root / "cells"
            cells.mkdir()
            cell(cells, 1, "PG-B", 10.0)

            result = provsql_summarize.summarize(
                workload,
                [cells],
                expected_methods=("PG-B",),
                expected_instances=("q001",),
            )

        group = result["groups"][0]
        self.assertTrue(group["complete"])
        self.assertEqual(10.0, group["median_primary_total_ms"])
        self.assertEqual("median of five measured executions for q001", result["aggregation"])

    def test_counterfactual_limit_uses_observed_phase_timings(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workload = manifest(root)
            cells = root / "cells"
            cells.mkdir()
            cell(cells, 1, "ProvSQL", 650010.0)
            cell(cells, 2, "ProvSQL", 20.0)
            for path in cells.rglob("cell.json"):
                value = json.loads(path.read_text(encoding="utf-8"))
                for run in value["runs"]:
                    for phase in run["phases"]:
                        phase.update({"status": "ok", "timeout_s": 3000.0})
                path.write_text(json.dumps(value), encoding="utf-8")
            first = cells / "cell-ProvSQL-1" / "cell.json"
            value = json.loads(first.read_text(encoding="utf-8"))
            value["runs"][0]["phases"][0]["client_wall_ms"] = 650000.0
            first.write_text(json.dumps(value), encoding="utf-8")

            result = provsql_summarize.summarize(
                workload,
                [cells],
                expected_methods=("ProvSQL",),
                counterfactual_phase_timeout_s=600.0,
            )

        group = result["groups"][0]
        self.assertEqual(1, group["counterfactual_timeout_instances"])
        self.assertEqual(1, group["counterfactual_complete_instances"])
        self.assertEqual(0, group["counterfactual_unknown_instances"])


if __name__ == "__main__":
    unittest.main()
