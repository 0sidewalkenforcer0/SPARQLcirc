"""Regression tests for adapted SPARQLprov n-ary-row queries."""

import importlib.util
from pathlib import Path
import unittest


MODULE_PATH = Path(__file__).resolve().parents[1] / "tpch" / "sparqlprov_rows.py"
SPEC = importlib.util.spec_from_file_location("tpch_sparqlprov_rows", MODULE_PATH)
sparqlprov_rows = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sparqlprov_rows)


class SparqlprovRowsTest(unittest.TestCase):
    def test_single_row_exposes_the_row_as_the_sum_statement(self):
        base = (
            "BASE <http://example.org/>\n"
            "PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>\n"
            "SELECT ?supplier ?lineitem WHERE {\n"
            "  ?lineitem <l_supp> ?supplier .\n"
            "}\n"
        )
        inline = (
            "SELECT ?supplier ?lineitem WHERE {\n"
            "  << ?lineitem <http://www.w3.org/1999/02/22-rdf-syntax-ns#type> ?t >> "
            "<http://example.org/occurrenceOf> ?token .\n"
            "  ?lineitem <l_supp> ?supplier .\n"
            "}\n"
        )

        rewritten = sparqlprov_rows.rewrite(base, inline)

        self.assertIn("?prov_sum_statement", rewritten)
        self.assertIn("BIND (?lineitem AS ?prov_sum_statement)", rewritten)
        self.assertNotIn("?prov_sum_product", rewritten)
        self.assertIn("xsd:string(?supplier)", rewritten)
        self.assertIn("xsd:string(?lineitem)", rewritten)

    def test_multi_row_builds_product_and_constant_projection_has_one_sum(self):
        base = (
            "BASE <http://example.org/>\n"
            "PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>\n"
            "SELECT (1 AS ?x) WHERE {\n"
            "  ?lineitem <l_part> ?part .\n"
            "  ?part <p_type> ?type .\n"
            "}\n"
        )
        inline = (
            "SELECT (1 AS ?x) WHERE {\n"
            "  << ?lineitem <http://www.w3.org/1999/02/22-rdf-syntax-ns#type> ?t1 >> "
            "<http://example.org/occurrenceOf> ?token1 .\n"
            "  << ?part <http://www.w3.org/1999/02/22-rdf-syntax-ns#type> ?t2 >> "
            "<http://example.org/occurrenceOf> ?token2 .\n"
            "  ?lineitem <l_part> ?part .\n"
            "  ?part <p_type> ?type .\n"
            "}\n"
        )

        rewritten = sparqlprov_rows.rewrite(base, inline)

        self.assertIn("?prov_sum_product_1_statement", rewritten)
        self.assertIn("?prov_sum_product_2_statement", rewritten)
        self.assertIn("http://example.org/p_Sum_product/", rewritten)
        self.assertIn('URI("http://example.org/p_Sum/")', rewritten)
        self.assertNotIn("xsd:string(?x)", rewritten)


if __name__ == "__main__":
    unittest.main()
