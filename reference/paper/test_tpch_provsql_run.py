"""Regression tests for the resumable ProvSQL TPC-H runner."""

import argparse
import importlib.util
from pathlib import Path
import tempfile
import time
import unittest


MODULE_PATH = Path(__file__).resolve().parents[1] / "tpch" / "provsql_run.py"
SPEC = importlib.util.spec_from_file_location("tpch_provsql_run", MODULE_PATH)
provsql_run = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(provsql_run)


ENTRY = {
    "query_id": "sf0p01-Q03-q001",
    "template": "Q03",
    "instance": "q001",
    "answer_columns": ["order", "o_orderdate", "o_shippriority"],
}


class TpchProvsqlRunTest(unittest.TestCase):
    def test_construction_and_pqe_keep_the_timed_stages_separate(self):
        construction = provsql_run.construction_sql(
            'SELECT 1 AS "order" GROUP BY 1;',
            "tpch_sf0p01",
            "spcirc_roots_q03_q001_r001",
            "spcirc_probabilities_q03_q001_r001",
            600.0,
        )
        pqe = provsql_run.pqe_sql(
            "tpch_sf0p01",
            "spcirc_roots_q03_q001_r001",
            "spcirc_probabilities_q03_q001_r001",
            ENTRY["answer_columns"],
            600.0,
        )

        self.assertIn("SET provsql.active TO on", construction)
        self.assertIn("SET max_parallel_workers_per_gather TO 0", construction)
        self.assertIn("gates_before", construction)
        self.assertIn("gates_after", construction)
        self.assertNotIn("probability_evaluate", construction)
        self.assertIn("SET provsql.active TO off", pqe)
        self.assertIn("probability_evaluate(provsql)", pqe)
        self.assertNotIn("probability_evaluate(provsql,", pqe)
        self.assertIn("provsql.last_eval_method", pqe)

    def test_fresh_token_stage_resets_all_eight_tables_and_probabilities(self):
        sql = provsql_run.reset_tokens_sql("tpch_sf0p01", 600.0)

        self.assertEqual(8, sql.count("remove_provenance"))
        self.assertEqual(8, sql.count("add_provenance"))
        self.assertEqual(8, sql.count("set_prob"))
        self.assertIn('FROM "tpch_sf0p01"."lineitem" AS t', sql)

    def test_default_warmup_executes_the_grouped_query_without_provenance(self):
        sql = provsql_run.deterministic_warmup_sql(
            'SELECT "order" FROM orders GROUP BY "order";',
            "tpch_sf0p01",
            600.0,
        )

        self.assertIn("SET provsql.active TO off", sql)
        self.assertIn('SELECT "order" FROM orders GROUP BY "order"', sql)
        self.assertNotIn("add_provenance", sql)
        self.assertNotIn("remove_provenance", sql)

    def test_cli_defaults_to_the_scalable_warmup_policy(self):
        args = provsql_run._parser().parse_args([
            "--manifest", "workload.json",
            "--dataset", "dataset.json",
            "--scale", "0.01",
            "--out", "results",
            "--psql", "psql",
            "--dsn", "postgresql://example/tpch",
        ])

        self.assertEqual("deterministic-query", args.warmup_policy)
        self.assertEqual(1, args.warmups)
        self.assertEqual(5, args.runs)
        self.assertEqual(3000.0, args.query_timeout)
        self.assertEqual(3000.0, args.pqe_timeout)
        self.assertEqual(3000.0, args.measured_total_timeout)

    def test_shared_budget_reduces_the_next_phase_timeout(self):
        started = time.perf_counter() - 4.0
        timeout, remaining = provsql_run._budgeted_timeout(
            600.0, 10.0, started
        )

        self.assertIsNotNone(remaining)
        self.assertGreater(timeout, 5.0)
        self.assertLessEqual(timeout, 6.0)

    def test_shared_budget_timeout_is_identified_in_phase_metadata(self):
        phase = {"status": "timeout", "timeout_s": 5.0}
        provsql_run._annotate_budget(phase, 600.0, 3000.0, 5.0)

        self.assertEqual("measured-total-budget", phase["timeout_source"])
        self.assertEqual(3000.0, phase["measured_total_timeout_s"])

    def test_formal_dataset_requires_an_auditable_gate_baseline(self):
        args = argparse.Namespace(
            scale="0.01",
            psql=Path(__file__),
            methods=["PG-B", "ProvSQL"],
            query_ids=[],
            warmups=1,
            runs=5,
        )
        workload = {
            "schema": "tpch-provsql-workload-v1",
            "tpch_version": "3.0.1",
            "scale_factors": ["0.01"],
        }
        dataset = {
            "schema": provsql_run.provsql_prepare.SCHEMA,
            "tpch_version": "3.0.1",
            "scale_factor": "0.01",
            "postgresql_version": "18.4",
            "probability_seed": 42,
            "probability_scheme": "md5-52-event-v1",
            "row_counts": {table: 1 for table in provsql_run.provsql_prepare.TABLES},
        }

        with self.assertRaisesRegex(provsql_run.ProvsqlRunError, "gate baseline"):
            provsql_run._validate(args, workload, dataset)

        dataset["materialized_gates_after_load"] = 10
        provsql_run._validate(args, workload, dataset)

    def test_circuit_metric_walk_deduplicates_nodes_before_counting_edges(self):
        sql = provsql_run.circuit_metrics_sql(
            "tpch_sf0p01", "spcirc_roots_q03_q001_r001", 600.0
        )

        self.assertIn("WITH RECURSIVE reachable", sql)
        self.assertIn("\n  UNION\n", sql)
        self.assertNotIn("UNION ALL", sql)
        self.assertIn("sum(cardinality(provsql.get_children(node)))", sql)

    def test_psql_launcher_arguments_precede_normal_psql_options(self):
        command = provsql_run._psql_command(
            Path("/usr/bin/apptainer"),
            ("exec", "/images/provsql.sif", "/usr/bin/psql"),
            "postgresql://test@127.0.0.1/tpch",
            Path("phase.sql"),
        )

        self.assertEqual(
            [
                str(Path("/usr/bin/apptainer")), "exec", "/images/provsql.sif",
                "/usr/bin/psql", "-X", "--dbname",
                "postgresql://test@127.0.0.1/tpch", "-q", "-A", "-t",
                "-F", "\t", "-v", "ON_ERROR_STOP=1", "-f", "phase.sql",
            ],
            command,
        )

    def test_csv_and_circuit_outputs_are_auditable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            csv_path = root / "roots.csv"
            csv_path.write_text(
                "order,provenance_root\n1,a\n2,a\n3,b\n", encoding="utf-8"
            )
            metrics_path = root / "metrics.tsv"
            metrics_path.write_text(
                "roots\t3\t2\ncircuit\t7\t8\n", encoding="utf-8"
            )

            csv_summary = provsql_run._csv_summary(
                csv_path, required_column="provenance_root"
            )
            metrics = provsql_run._parse_metrics(metrics_path)

        self.assertEqual(3, csv_summary["rows"])
        self.assertEqual(2, metrics["distinct_roots"])
        self.assertEqual(15, metrics["reachable_nodes_plus_edges"])

    def test_partial_cell_is_not_silently_reused(self):
        with tempfile.TemporaryDirectory() as directory:
            cell = Path(directory) / "cell"
            cell.mkdir()
            (cell / "construction.stderr").write_text("partial", encoding="utf-8")

            with self.assertRaisesRegex(provsql_run.ProvsqlRunError, "partial cell"):
                provsql_run._cell_record(cell)


if __name__ == "__main__":
    unittest.main()
