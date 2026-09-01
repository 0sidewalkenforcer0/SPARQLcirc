"""Regression tests for the derived ProvSQL TPC-H workload."""

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock


MODULE_PATH = Path(__file__).resolve().parents[1] / "tpch" / "provsql_workload.py"
SPEC = importlib.util.spec_from_file_location("tpch_provsql_workload", MODULE_PATH)
provsql_workload = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(provsql_workload)


PARAMETERS = {
    1: ["90"],
    3: ["BUILDING", "1995-03-15"],
    4: ["1993-08-01"],
    5: ["EUROPE", "1994-01-01"],
    6: ["1994-01-01", "0.05", "24"],
    7: ["FRANCE", "GERMANY"],
    8: ["BRAZIL", "AMERICA", "ECONOMY ANODIZED STEEL"],
    10: ["1994-02-01"],
    12: ["MAIL", "SHIP", "1994-01-01"],
    14: ["1994-03-01"],
    15: ["1995-06-01"],
    19: ["Brand#11", "Brand#22", "Brand#33", "4", "15", "28"],
}


CORRECTIONS = {
    8: ["Q08 nation parameter is used only by the removed aggregate expression"],
    19: ["Q19 release template omitted # in the first brand marker"],
}


def source_manifest(path: Path) -> Path:
    entries = []
    for query_id in provsql_workload.workload.TEMPLATE_IDS:
        entries.append({
            "query_id": "sf0p01-Q%02d-q001" % query_id,
            "scale_factor": "0.01",
            "template": "Q%02d" % query_id,
            "instance": "q001",
            "seed": 1,
            "parameters": PARAMETERS[query_id],
            "artifact_corrections": CORRECTIONS.get(query_id, []),
            "base_query": "sf0p01/Q%02d/q001/base.rq" % query_id,
        })
    value = {
        "schema": "tpch-nonaggregate-workload-v1",
        "tpch_version": "3.0.1",
        "entries": entries,
    }
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


class TpchProvsqlWorkloadTest(unittest.TestCase):
    def generate(self, root: Path):
        source = source_manifest(root / "source.json")
        output = root / "provsql"
        with mock.patch.object(provsql_workload.workload, "audit_manifest"):
            result = provsql_workload.generate(source, output, instances=("q001",))
        return output, result

    def test_generates_all_twelve_parameterized_sql_pairs(self):
        with tempfile.TemporaryDirectory() as directory:
            output, result = self.generate(Path(directory))
            audit = provsql_workload.audit(output / "manifest.json")
            q3 = (output / "sf0p01/Q03/q001/base.sql").read_text(encoding="utf-8")
            q3_answers = (
                output / "sf0p01/Q03/q001/answers.sql"
            ).read_text(encoding="utf-8")
            q19 = (output / "sf0p01/Q19/q001/base.sql").read_text(encoding="utf-8")

        self.assertEqual(12, len(result["entries"]))
        self.assertEqual(12, audit["entry_count"])
        self.assertIn("c.c_mktsegment = 'BUILDING'", q3)
        self.assertEqual(1, q3_answers.count("GROUP BY"))
        self.assertIn('d."order"', q3_answers)
        self.assertIn("p.p_brand = 'Brand#11'", q19)
        self.assertNotIn("Brand13", q19)

    def test_unwrapped_and_grouped_queries_keep_separate_semantics(self):
        base = 'SELECT 1::integer AS "x" FROM lineitem;\n'
        grouped = provsql_workload.answer_query(base, ("x",))
        self.assertNotIn("GROUP BY", base)
        self.assertIn('GROUP BY d."x"', grouped)
        self.assertIn('d."x" AS "x"', grouped)

    def test_audit_rejects_changed_sql(self):
        with tempfile.TemporaryDirectory() as directory:
            output, _ = self.generate(Path(directory))
            query = output / "sf0p01/Q06/q001/base.sql"
            query.write_text("SELECT 2 AS x;\n", encoding="utf-8")
            with self.assertRaisesRegex(
                provsql_workload.ProvsqlWorkloadError, "base SQL differs"
            ):
                provsql_workload.audit(output / "manifest.json")


if __name__ == "__main__":
    unittest.main()
