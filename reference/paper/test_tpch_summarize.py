"""Regression tests for median aggregation of TPC-H cell artifacts."""

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


MODULE_PATH = Path(__file__).resolve().parents[1] / "tpch" / "summarize.py"
SPEC = importlib.util.spec_from_file_location("tpch_summarize", MODULE_PATH)
tpch_summarize = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(tpch_summarize)


def manifest(root):
    entries = [
        {
            "query_id": "sf0p01-Q01-q%03d" % index,
            "scale_factor": "0.01",
            "template": "Q01",
            "instance": "q%03d" % index,
        }
        for index in (1, 2, 3)
    ]
    value = {
        "scale_factors": ["0.01"],
        "templates": ["Q01"],
        "rdf_star_profile": "RDF-star 1.1 quoted triple plus occurrenceOf",
        "rdf_star_12_permitted": False,
        "provenance_granularity": "one token per TPC-H row rdf:type marker",
        "entries": entries,
    }
    path = root / "manifest.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def cell(root, index, endpoint_ms, method_ms, status="ok"):
    directory = root / ("cell-%d" % index)
    directory.mkdir(parents=True)
    run_status = status
    value = {
        "schema": tpch_summarize.CELL_SCHEMA,
        "status": "ok" if status == "ok" else "incomplete",
        "query_id": "sf0p01-Q01-q%03d" % index,
        "workload": "tpch",
        "engine": "graphdb",
        "method": "N",
        "scheme": "SPARQL_Star_Row",
        "protocol": {
            "warmups": 1,
            "measured_runs": 5,
            "primary_statistic": "median",
        },
        "runs": [
            {
                "run_id": "warmup-01",
                "phase": "warmup",
                "status": "ok",
                "run_wall_ms": 9999.0,
            },
            *[{
                "run_id": "measured-%02d" % run,
                "phase": "measured",
                "status": run_status,
                "component_method_e2e_ms": method_ms,
                "endpoint": {
                    "endpoint": {
                        "endpoint_e2e_ms": endpoint_ms,
                        "response_bytes": 100 * index,
                    }
                },
                "offline": {"metrics": {"pp_hc_build_wall_ms": index}},
            } for run in range(1, 6)],
        ],
    }
    (directory / "cell.json").write_text(json.dumps(value), encoding="utf-8")


class TpchSummarizeTest(unittest.TestCase):
    def test_complete_group_uses_median_of_measured_runs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workload = manifest(root)
            cells = root / "cells"
            cells.mkdir()
            cell(cells, 1, 10.0, 15.0)
            cell(cells, 2, 20.0, 25.0)
            cell(cells, 3, 60.0, 65.0)

            result = tpch_summarize.summarize(workload, [cells], ["graphdb"], ["N"])

        group = result["groups"][0]
        self.assertTrue(group["complete"])
        self.assertEqual(20.0, group["median_endpoint_e2e_ms"])
        self.assertEqual(25.0, group["median_component_method_e2e_ms"])
        self.assertEqual(
            200.0,
            group["complete_metric_medians"]["endpoint.endpoint.response_bytes"],
        )
        self.assertNotIn("warmup", " ".join(group["complete_metric_medians"]))

    def test_timeout_suppresses_publication_median_but_keeps_diagnostic_median(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workload = manifest(root)
            cells = root / "cells"
            cells.mkdir()
            cell(cells, 1, 10.0, 15.0)
            cell(cells, 2, 20.0, 25.0)
            cell(cells, 3, 600000.0, None, status="timeout")

            result = tpch_summarize.summarize(workload, [cells], ["graphdb"], ["N"])

        group = result["groups"][0]
        self.assertFalse(group["complete"])
        self.assertEqual(1, group["timeout_instances"])
        self.assertIsNone(group["median_endpoint_e2e_ms"])
        self.assertEqual(15.0, group["available_median_endpoint_e2e_ms"])
        self.assertEqual({}, group["complete_metric_medians"])

    def test_rejects_non_row_rdfstar_cells(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workload = manifest(root)
            cells = root / "cells"
            cells.mkdir()
            cell(cells, 1, 10.0, 15.0)
            path = cells / "cell-1" / "cell.json"
            value = json.loads(path.read_text(encoding="utf-8"))
            value["scheme"] = "SPARQL_Star"
            path.write_text(json.dumps(value), encoding="utf-8")

            with self.assertRaisesRegex(tpch_summarize.SummaryError, "SPARQL_Star_Row"):
                tpch_summarize.summarize(workload, [cells], ["graphdb"], ["N"])

    def test_single_instance_selection_does_not_require_unrun_instances(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workload = manifest(root)
            cells = root / "cells"
            cells.mkdir()
            cell(cells, 1, 10.0, 15.0)

            result = tpch_summarize.summarize(
                workload, [cells], ["graphdb"], ["N"], ["q001"]
            )

        group = result["groups"][0]
        self.assertTrue(group["complete"])
        self.assertEqual(1, group["expected_instances"])
        self.assertEqual(10.0, group["median_endpoint_e2e_ms"])
        self.assertEqual("median of five measured executions for q001", result["aggregation"])

    def test_successful_offline_resume_is_used_without_replacing_source_cell(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workload = manifest(root)
            cells = root / "cells"
            cells.mkdir()
            cell(cells, 1, 10.0, 15.0)
            cell(cells, 2, 20.0, 25.0)
            cell(cells, 3, 30.0, None, status="offline-timeout")
            source = cells / "cell-3"
            resumed = []
            for run_number in range(1, 6):
                run_id = "measured-%02d" % run_number
                run = source / "offline-resume-001" / run_id
                run.mkdir(parents=True)
                (run / "offline-result.json").write_text(
                    json.dumps({
                        "status": "ok",
                        "metrics": {"pp_hc_build_wall_ms": 3.0},
                    }),
                    encoding="utf-8",
                )
                resumed.append({
                    "run_id": run_id,
                    "offline_status": "ok",
                    "offline_artifact_run": "offline-resume-001/%s" % run_id,
                    "component_method_e2e_ms": 30.0 + run_number,
                })
            (run.parent / "resume.json").write_text(
                json.dumps({
                    "schema": tpch_summarize.OFFLINE_RESUME_SCHEMA,
                    "status": "ok",
                    "runs": resumed,
                }),
                encoding="utf-8",
            )

            result = tpch_summarize.summarize(
                workload, [cells], ["graphdb"], ["N"]
            )

        group = result["groups"][0]
        self.assertTrue(group["complete"])
        self.assertEqual(20.0, group["median_endpoint_e2e_ms"])
        self.assertEqual(25.0, group["median_component_method_e2e_ms"])


if __name__ == "__main__":
    unittest.main()
