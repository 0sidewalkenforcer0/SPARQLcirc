"""Focused regressions for Standard and RDF-star query reification."""

import contextlib
import io
import os
import sys
import tempfile
import unittest


HERE = os.path.dirname(os.path.abspath(__file__))
REF = os.path.dirname(HERE)
sys.path.insert(0, REF)

import reify_query


QUERY = """\
PREFIX ex: <http://example.org/>
SELECT DISTINCT ?s ?o WHERE {
  { ?s ex:p ?o . }
  UNION
  { ?s ex:q ?o . }
  OPTIONAL { ?o ex:label ?label . }
  MINUS { ?s ex:blocked ?o . }
}
"""


class ReifyQueryTests(unittest.TestCase):
    def test_scheme_aliases_are_normalized(self):
        self.assertEqual(reify_query.normalize_scheme("standard"), "Standard")
        for alias in ("SPARQL_Star", "sparql-star", "rdfstar", "rdf-star"):
            self.assertEqual(reify_query.normalize_scheme(alias), "SPARQL_Star")
        with self.assertRaisesRegex(ValueError, "unsupported reification scheme"):
            reify_query.normalize_scheme("unknown")

    def test_standard_reification_remains_the_default(self):
        default = reify_query.reify(QUERY)
        explicit = reify_query.reify(QUERY, "Standard")

        self.assertEqual(default, explicit)
        self.assertEqual(default.count(f"<{reify_query.RS}subject>"), 4)
        self.assertEqual(default.count(f"<{reify_query.RS}predicate>"), 4)
        self.assertEqual(default.count(f"<{reify_query.RS}object>"), 4)
        for operator in ("UNION", "OPTIONAL", "MINUS"):
            self.assertIn(operator, default)

    def test_rdfstar_reification_preserves_the_query_algebra(self):
        rdfstar = reify_query.reify(QUERY, "SPARQL_Star")

        self.assertTrue(rdfstar.startswith("SELECT DISTINCT ?s ?o WHERE"))
        self.assertEqual(rdfstar.count("<< "), 4)
        self.assertEqual(rdfstar.count(f"<{reify_query.OCCURRENCE_OF}>"), 4)
        self.assertNotIn(f"<{reify_query.RS}subject>", rdfstar)
        for operator in ("UNION", "OPTIONAL", "MINUS"):
            self.assertIn(operator, rdfstar)
        for forbidden in ("GROUP_CONCAT", "SHA256", "urn:g:", "CONSTRUCT"):
            self.assertNotIn(forbidden, rdfstar)

    def test_cli_accepts_an_explicit_rdfstar_scheme(self):
        with tempfile.NamedTemporaryFile(
            "w", suffix=".rq", encoding="utf-8", delete=False
        ) as handle:
            handle.write(QUERY)
            query_path = handle.name
        try:
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                status = reify_query.main(
                    ["--scheme", "rdf-star", query_path]
                )
        finally:
            os.unlink(query_path)

        self.assertEqual(status, 0)
        self.assertIn("<< ", output.getvalue())
        self.assertIn(f"<{reify_query.OCCURRENCE_OF}>", output.getvalue())


if __name__ == "__main__":
    unittest.main()
