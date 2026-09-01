"""Regression tests for the TPC-H scale-by-engine batch driver."""

import argparse
import importlib.util
from pathlib import Path
import tempfile
import unittest


MODULE_PATH = Path(__file__).resolve().parents[1] / "tpch" / "run_batch.py"
SPEC = importlib.util.spec_from_file_location("tpch_run_batch", MODULE_PATH)
tpch_run_batch = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(tpch_run_batch)


def arguments(root):
    jar = root / "npcs-rewrite.jar"
    jar.write_bytes(b"fixture")
    return argparse.Namespace(
        manifest=root / "manifest.json",
        dataset=root / "dataset.json",
        scale="0.01",
        engine="graphdb-10.7.6",
        methods=("B", "R", "P", "N", "C-flat", "C-factorised"),
        query_ids=None,
        out=root / "results",
        base_endpoint="http://127.0.0.1/base",
        mixed_endpoint="http://127.0.0.1/mixed",
        update_endpoint="http://127.0.0.1/update",
        jar=jar,
        java="java",
        warmups=1,
        runs=5,
        endpoint_timeout=600.0,
        offline_timeout=600.0,
        complete_method_timeout=3000.0,
        pqe_backend="cudd",
        probability_seed=42,
        continue_after_failure=False,
    )


ENTRY = {
    "query_id": "sf0p01-Q03-q001",
    "template": "Q03",
    "instance": "q001",
    "base_query": "sf0p01/Q03/q001/base.rq",
    "row_inline_query": "sf0p01/Q03/q001/row-inline.rq",
    "sparqlprov_query": "sf0p01/Q03/q001/sparqlprov.rq",
}


class TpchRunBatchTest(unittest.TestCase):
    def test_r_uses_frozen_hybrid_inline_rdfstar11_query(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args = arguments(root)
            command = tpch_run_batch._runner_command(
                args, ENTRY, "R", root / "cell", root / "workload", root / "mixed.ttls"
            )

        self.assertIn("SPARQL_Star_Row", command)
        self.assertIn("--expected-r-query", command)
        self.assertIn("row-inline.rq", " ".join(command))
        self.assertIn("--reified-endpoint", command)
        self.assertNotIn("--pqe-backend", command)

    def test_n_and_c_use_row_scheme_and_pqe_while_c_is_single_core(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args = arguments(root)
            for method in ("N", "C-flat", "C-factorised"):
                command = tpch_run_batch._runner_command(
                    args, ENTRY, method, root / method, root / "workload", root / "mixed.ttls"
                )
                self.assertIn("SPARQL_Star_Row", command)
                self.assertIn("--pqe-backend", command)
                self.assertIn("cudd", command)
                self.assertIn("--probability-seed", command)
                seed = command.index("--probability-seed")
                self.assertEqual("42", command[seed + 1])
                parallelism = command.index("--c-parallelism")
                self.assertEqual("1", command[parallelism + 1])
                deadline = command.index("--complete-method-timeout")
                self.assertEqual("3000.0", command[deadline + 1])
            self.assertIn("--update-endpoint", command)

    def test_formal_defaults_use_one_warmup_five_runs_and_complete_method_budget(self):
        args = tpch_run_batch._parser().parse_args([
            "--manifest", "manifest.json",
            "--dataset", "dataset.json",
            "--scale", "0.1",
            "--engine", "graphdb-10.7.6",
            "--out", "results",
            "--base-endpoint", "http://127.0.0.1/base",
        ])

        self.assertEqual(1, args.warmups)
        self.assertEqual(5, args.runs)
        self.assertEqual(3000.0, args.endpoint_timeout)
        self.assertEqual(3000.0, args.offline_timeout)
        self.assertEqual(3000.0, args.complete_method_timeout)

    def test_p_uses_frozen_sparqlprov_query_on_the_base_store(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args = arguments(root)
            command = tpch_run_batch._runner_command(
                args, ENTRY, "P", root / "P", root / "workload", root / "mixed.ttls"
            )

        self.assertIn("sparqlprov.rq", " ".join(command))
        self.assertIn("--base-endpoint", command)
        self.assertNotIn("--reified-endpoint", command)
        self.assertNotIn("--pqe-backend", command)

    def test_partial_cell_is_not_silently_reused(self):
        with tempfile.TemporaryDirectory() as directory:
            cell = Path(directory) / "cell"
            cell.mkdir()
            (cell / "worker.stderr").write_text("partial", encoding="utf-8")
            with self.assertRaisesRegex(tpch_run_batch.BatchError, "partial cell"):
                tpch_run_batch._cell_record(cell)

    def test_pilot_query_filter_preserves_manifest_order(self):
        manifest = {"entries": [
            {"query_id": "sf0p01-Q03-q001", "scale_factor": "0.01"},
            {"query_id": "sf0p01-Q03-q002", "scale_factor": "0.01"},
            {"query_id": "sf0p1-Q03-q001", "scale_factor": "0.1"},
        ]}

        selected = tpch_run_batch._select_entries(
            manifest, "0.01", ("sf0p01-Q03-q002", "sf0p01-Q03-q001")
        )

        self.assertEqual(
            ["sf0p01-Q03-q001", "sf0p01-Q03-q002"],
            [entry["query_id"] for entry in selected],
        )

    def test_unknown_pilot_query_id_is_rejected(self):
        manifest = {"entries": [
            {"query_id": "sf0p01-Q03-q001", "scale_factor": "0.01"},
        ]}

        with self.assertRaisesRegex(tpch_run_batch.BatchError, "not present"):
            tpch_run_batch._select_entries(
                manifest, "0.01", ("sf0p01-Q03-q999",)
            )


if __name__ == "__main__":
    unittest.main()
