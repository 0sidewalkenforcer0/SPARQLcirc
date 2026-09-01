"""Regression tests for TPC-H row-level hybrid-inline query generation."""

import importlib.util
from pathlib import Path
import unittest


MODULE_PATH = Path(__file__).resolve().parents[1] / "tpch" / "inline_rows.py"
SPEC = importlib.util.spec_from_file_location("tpch_inline_rows", MODULE_PATH)
tpch_inline_rows = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(tpch_inline_rows)


QUERY = """\
SELECT ?order ?year WHERE {
  ?order <http://example.org/o_cust> ?customer .
  ?order <http://example.org/o_orderdate> ?date .
  ?customer <http://example.org/c_mktsegment> "BUILDING" .
  FILTER(?date < "1995-03-17"^^<http://www.w3.org/2001/XMLSchema#date>)
  BIND(YEAR(?date) AS ?year)
}
"""


@unittest.skipUnless(tpch_inline_rows.RDFLIB_AVAILABLE, "rdflib is optional locally")
class TpchInlineRowsTest(unittest.TestCase):
    def test_adds_one_occurrence_per_distinct_subject_and_preserves_query_body(self):
        rewritten = tpch_inline_rows.inline_rows(QUERY)

        self.assertEqual(2, rewritten.count("<http://example.org/occurrenceOf>"))
        self.assertIn("<< ?order <" + tpch_inline_rows.RDF_TYPE, rewritten)
        self.assertIn("<< ?customer <" + tpch_inline_rows.RDF_TYPE, rewritten)
        self.assertEqual(1, rewritten.count("<http://example.org/o_orderdate>"))
        self.assertIn("FILTER(?date <", rewritten)
        self.assertIn("BIND(YEAR(?date) AS ?year)", rewritten)
        self.assertNotIn("rdf:reifies", rewritten)
        self.assertNotIn("<<(", rewritten)

    def test_generated_variables_do_not_capture_query_variables(self):
        collision = QUERY.replace("SELECT ?order ?year", "SELECT ?order ?year ?__tpch_row_type_1")
        rewritten = tpch_inline_rows.inline_rows(collision)

        self.assertIn("?__tpch_row_type_2", rewritten)

    def test_rejects_multiple_bgp_scopes(self):
        query = "SELECT ?x WHERE { { ?x <urn:p> ?y } UNION { ?x <urn:q> ?z } }"
        with self.assertRaisesRegex(ValueError, "exactly one BGP"):
            tpch_inline_rows.inline_rows(query)

    def test_rejects_rdf12_reification(self):
        query = QUERY.replace(
            "WHERE {",
            "WHERE { ?r <http://www.w3.org/1999/02/22-rdf-syntax-ns#reifies> ?t .",
        )
        with self.assertRaisesRegex(ValueError, "RDF 1.2"):
            tpch_inline_rows.inline_rows(query)


if __name__ == "__main__":
    unittest.main()
