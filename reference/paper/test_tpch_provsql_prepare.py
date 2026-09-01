"""Regression tests for TPC-H loading into PostgreSQL with ProvSQL."""

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


MODULE_PATH = Path(__file__).resolve().parents[1] / "tpch" / "provsql_prepare.py"
SPEC = importlib.util.spec_from_file_location("tpch_provsql_prepare", MODULE_PATH)
provsql_prepare = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(provsql_prepare)


def dataset(root: Path) -> Path:
    tables = {}
    table_root = root / "tbl"
    table_root.mkdir()
    for table in provsql_prepare.TABLES:
        path = table_root / (table + ".tbl")
        path.write_text("fixture|\n", encoding="utf-8")
        tables[table] = {"path": "tbl/%s.tbl" % table, "bytes": path.stat().st_size}
    metadata = {
        "tpch_version": "3.0.1",
        "scale_factor": "0.01",
        "tables": tables,
    }
    path = root / "dataset.json"
    path.write_text(json.dumps(metadata), encoding="utf-8")
    return path


class TpchProvsqlPrepareTest(unittest.TestCase):
    def test_load_script_streams_tbl_and_sets_one_probability_per_row(self):
        with tempfile.TemporaryDirectory() as directory:
            script = provsql_prepare.load_sql(dataset(Path(directory)), "tpch_sf0p01")

        self.assertEqual(8, script.count(r"\copy "))
        self.assertEqual(8, script.count("DROP COLUMN _trailer"))
        self.assertEqual(8, script.count("ADD PRIMARY KEY"))
        self.assertEqual(8, script.count("provsql.add_provenance"))
        self.assertEqual(8, script.count("provsql.set_prob"))
        self.assertEqual(8, script.count("PERFORM provsql.set_prob"))
        self.assertEqual(8, script.count("substr(md5("))
        self.assertIn("sparqlcirc-event-probability-v1|42|", script)
        self.assertIn("http://example.org/LineItem/", script)
        self.assertNotIn(r"\o /dev/null", script)
        self.assertIn('SET search_path TO "tpch_sf0p01", public, provsql;', script)

    def test_rejects_an_unsafe_schema_identifier(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(
                provsql_prepare.ProvsqlPreparationError, "unsafe PostgreSQL identifier"
            ):
                provsql_prepare.load_sql(dataset(Path(directory)), "tpch;DROP")

    def test_inventory_parser_requires_versions_and_all_tables(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "inventory.tsv"
            rows = [
                "postgresql_version\t18.4",
                "provsql_version\t1.12.0",
                "materialized_gates\t10",
            ]
            rows.extend("%s\t1" % table for table in provsql_prepare.TABLES)
            path.write_text("\n".join(rows) + "\n", encoding="utf-8")
            values = provsql_prepare._parse_inventory(path)

        self.assertEqual("1.12.0", values["provsql_version"])
        self.assertEqual("10", values["materialized_gates"])
        self.assertEqual("1", values["lineitem"])

    def test_formal_postgresql_version_is_18_4(self):
        self.assertEqual("18.4", provsql_prepare.POSTGRESQL_VERSION)

    def test_row_event_expression_matches_the_rdf_mapping(self):
        expression = provsql_prepare.row_event_expression("lineitem")

        self.assertIn("http://example.org/LineItem/", expression)
        self.assertIn("t.l_orderkey::text", expression)
        self.assertIn("t.l_linenumber::text", expression)


if __name__ == "__main__":
    unittest.main()
