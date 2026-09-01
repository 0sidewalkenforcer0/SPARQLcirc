"""Regression tests for ProvSQL TPC-H answer-binding parity."""

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock


MODULE_PATH = Path(__file__).resolve().parents[1] / "tpch" / "provsql_verify.py"
SPEC = importlib.util.spec_from_file_location("tpch_provsql_verify", MODULE_PATH)
provsql_verify = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(provsql_verify)


def fixtures(root: Path, mismatched: bool = False):
    manifest = root / "manifest.json"
    manifest.write_text(json.dumps({
        "schema": "tpch-provsql-workload-v1",
        "entries": [{
            "query_id": "sf0p01-Q03-q001",
            "template": "Q03",
            "instance": "q001",
            "scale_factor": "0.01",
            "answer_columns": ["order", "date"],
        }],
    }), encoding="utf-8")
    cells = root / "cells"
    for method in ("PG-B", "ProvSQL"):
        cell = cells / "Q03" / "q001" / method
        run = cell / "runs" / "run001"
        run.mkdir(parents=True)
        (cell / "cell.json").write_text(json.dumps({
            "schema": "tpch-provsql-cell-v1",
            "status": "ok",
            "query_id": "sf0p01-Q03-q001",
            "method": method,
            "protocol": {"warmups": 1, "measured_runs": 5},
            "runs": [{"status": "ok"} for _ in range(5)],
        }), encoding="utf-8")
    (cells / "Q03/q001/PG-B/runs/run001/answers.csv").write_text(
        "order,date\no1,1995-01-01\no1,1995-01-01\no2,1995-01-02\n",
        encoding="utf-8",
    )
    second = "o3" if mismatched else "o2"
    (cells / "Q03/q001/ProvSQL/runs/run001/roots.csv").write_text(
        "order,date,provenance_root\n"
        "o1,1995-01-01,00000000-0000-0000-0000-000000000001\n"
        "%s,1995-01-02,00000000-0000-0000-0000-000000000002\n" % second,
        encoding="utf-8",
    )
    (cells / "Q03/q001/ProvSQL/runs/run001/probabilities.csv").write_text(
        "order,date,probability\n"
        "o1,1995-01-01,0.125\n"
        "%s,1995-01-02,0.5\n" % second,
        encoding="utf-8",
    )
    records = root / "answer-records.jsonl"
    records.write_text(
        json.dumps({
            "binding": [
                ["date", ["literal", "1995-01-01", "http://www.w3.org/2001/XMLSchema#date", ""]],
                ["order", ["iri", "o1"]],
            ],
            "multiplicity": 2,
        }) + "\n" + json.dumps({
            "binding": [
                ["date", ["literal", "1995-01-02", "http://www.w3.org/2001/XMLSchema#date", ""]],
                ["order", ["iri", "o2"]],
            ],
            "multiplicity": 1,
        }) + "\n",
        encoding="utf-8",
    )
    return manifest, cells, records


class TpchProvsqlVerifyTest(unittest.TestCase):
    def test_decimal_sql_lexical_forms_use_rdf_value_semantics(self):
        signature = (
            "literal",
            "http://www.w3.org/2001/XMLSchema#decimal",
            "",
        )

        self.assertEqual("-17.1", provsql_verify._normalize_sql_value("-17.10", signature))
        self.assertEqual("0", provsql_verify._normalize_sql_value("0.00", signature))

    def test_duplicate_bag_rows_collapse_to_matching_answer_roots(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest, cells, _records = fixtures(root)
            with mock.patch.object(provsql_verify.provsql_workload, "audit"):
                result = provsql_verify.verify(
                    manifest, "sf0p01-Q03-q001", cells, root / "parity.json"
                )

        self.assertEqual("ok", result["status"])
        self.assertEqual(3, result["pg_b"]["rows"])
        self.assertEqual(2, result["pg_b"]["distinct_bindings"])
        self.assertEqual(2, result["provsql_roots"]["rows"])

    def test_binding_mismatch_is_recorded_as_a_failed_check(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest, cells, _records = fixtures(root, mismatched=True)
            with mock.patch.object(provsql_verify.provsql_workload, "audit"):
                result = provsql_verify.verify(
                    manifest, "sf0p01-Q03-q001", cells, root / "parity.json"
                )

        self.assertEqual("failed", result["status"])
        self.assertEqual(1, result["binding_set_differences"]["base_minus_roots"])
        self.assertEqual(1, result["binding_set_differences"]["roots_minus_base"])

    def test_optional_sparql_records_check_exact_bag_multiplicity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest, cells, records = fixtures(root)
            with mock.patch.object(provsql_verify.provsql_workload, "audit"):
                result = provsql_verify.verify(
                    manifest,
                    "sf0p01-Q03-q001",
                    cells,
                    root / "parity.json",
                    sparql_records=records,
                )

        self.assertEqual("ok", result["status"])
        self.assertEqual(3, result["sparql_b"]["rows"])
        self.assertEqual(
            {"pg_b_minus_sparql_b": 0, "sparql_b_minus_pg_b": 0,
             "multiplicity_mismatches": 0},
            result["sparql_bag_differences"],
        )


if __name__ == "__main__":
    unittest.main()
