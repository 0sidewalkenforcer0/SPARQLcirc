"""Regression tests for immutable TPC-H RDF-star 1.1 dataset preparation."""

import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest import mock


MODULE_PATH = Path(__file__).resolve().parents[1] / "tpch" / "prepare_data.py"
SPEC = importlib.util.spec_from_file_location("tpch_prepare_data", MODULE_PATH)
tpch_prepare_data = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(tpch_prepare_data)


ROWS = {
    "part": "1|Part|Manufacturer|Brand#1|TYPE|1|SM BOX|1.00|Comment|\n",
    "region": "1|EUROPE|Comment|\n",
    "nation": "1|GERMANY|1|Comment|\n",
    "supplier": "1|Supplier|Address|1|Phone|1.00|Comment|\n",
    "partsupp": "1|1|1|1.00|Comment|\n",
    "customer": "1|Customer|Address|1|Phone|1.00|BUILDING|Comment|\n",
    "orders": "1|1|O|1.00|1995-01-01|1-URGENT|Clerk|0|Comment|\n",
    "lineitem": (
        "1|1|1|1|1.00|1.00|0.01|0.01|N|O|1995-01-02|1995-01-01|"
        "1995-01-03|DELIVER IN PERSON|AIR|Comment|\n"
    ),
}


def fake_dbgen(_dbgen, _dbgen_directory, scale, table_directory):
    for table, row in ROWS.items():
        (table_directory / (table + ".tbl")).write_text(row, encoding="utf-8")
    (table_directory.parent / "dbgen.stdout").write_text("generated\n", encoding="utf-8")
    (table_directory.parent / "dbgen.stderr").write_text("", encoding="utf-8")
    return {
        "argv": ["dbgen", "-f", "-s", scale],
        "exit_code": 0,
        "stdout": "dbgen.stdout",
        "stderr": "dbgen.stderr",
    }


class TpchPrepareDataTest(unittest.TestCase):
    def prepare(self, root):
        dbgen = root / "dbgen"
        dbgen.write_text("fixture", encoding="utf-8")
        dbgen_directory = root / "dbgen-source"
        dbgen_directory.mkdir()
        output = root / "sf0p01"
        with mock.patch.object(tpch_prepare_data, "_run_dbgen", side_effect=fake_dbgen):
            metadata = tpch_prepare_data.prepare(
                output, "0.01", "3.0.1", dbgen, dbgen_directory
            )
        return output, metadata

    def test_prepares_base_and_per_row_legacy_rdfstar_layouts(self):
        with tempfile.TemporaryDirectory() as directory:
            output, metadata = self.prepare(Path(directory))
            mixed = (output / "mixed-rdfstar11.ttls").read_text(encoding="utf-8")
            audit = tpch_prepare_data.audit(output / "dataset.json")

        self.assertEqual(8, metadata["layouts"]["mixed"]["row_occurrence_statement_count"])
        self.assertEqual(8, mixed.count("<http://example.org/occurrenceOf>"))
        self.assertIn("<< <http://example.org/Customer/1>", mixed)
        self.assertNotIn("rdf:reifies", mixed)
        self.assertNotIn("<<(", mixed)
        self.assertFalse(metadata["rdf_star_12_permitted"])
        self.assertTrue(metadata["fractional_scale_factor"])
        self.assertEqual("ok", audit["status"])

    def test_refuses_to_reuse_a_completed_scale_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output, _ = self.prepare(root)
            with self.assertRaisesRegex(tpch_prepare_data.PreparationError, "refusing to reuse"):
                tpch_prepare_data.prepare(
                    output, "0.01", "3.0.1", root / "dbgen", root / "dbgen-source"
                )

    def test_audit_rejects_rdf12_reification(self):
        with tempfile.TemporaryDirectory() as directory:
            output, _ = self.prepare(Path(directory))
            mixed = output / "mixed-rdfstar11.ttls"
            text = mixed.read_text(encoding="utf-8")
            mixed.write_text(
                text + "<urn:r> <http://www.w3.org/1999/02/22-rdf-syntax-ns#reifies> <urn:t> .\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(tpch_prepare_data.PreparationError, "RDF 1.2"):
                tpch_prepare_data.audit(output / "dataset.json")


if __name__ == "__main__":
    unittest.main()
