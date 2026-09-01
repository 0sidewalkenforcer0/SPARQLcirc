"""Regression tests for WatDiv mixed-data generation."""

import importlib.util
from pathlib import Path
import tempfile
import unittest


MODULE_PATH = Path(__file__).resolve().parents[1] / "watdiv" / "reify.py"
SPEC = importlib.util.spec_from_file_location("watdiv_reify", MODULE_PATH)
watdiv_reify = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(watdiv_reify)


INPUT = """\
<urn:a> <urn:p> <urn:b> .
<urn:a> <urn:label> "a literal with spaces" .
"""


class WatdivReifyTests(unittest.TestCase):
    def convert(self, scheme, pure=False):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "input.nt"
            output = root / "output.nq"
            source.write_text(INPUT, encoding="utf-8")
            counts = watdiv_reify.reify_file(
                source, output, scheme=scheme, pure=pure
            )
            return counts, output.read_text(encoding="utf-8").splitlines()

    def test_rdfstar_defaults_to_asserted_plus_occurrence(self):
        counts, lines = self.convert(watdiv_reify.RDF_STAR)

        self.assertEqual((2, 4), counts)
        self.assertEqual("<urn:a> <urn:p> <urn:b> .", lines[0])
        self.assertEqual(
            "<< <urn:a> <urn:p> <urn:b> >> "
            "<http://example.org/occurrenceOf> <urn:t:0> .",
            lines[1],
        )
        self.assertEqual(
            '<urn:a> <urn:label> "a literal with spaces" .', lines[2]
        )

    def test_standard_defaults_to_four_statements_per_fact(self):
        counts, lines = self.convert(watdiv_reify.STANDARD)

        self.assertEqual((2, 8), counts)
        self.assertEqual("<urn:a> <urn:p> <urn:b> .", lines[0])
        self.assertIn("<urn:t:0> <" + watdiv_reify.RS + "subject> <urn:a> .", lines)

    def test_named_graph_defaults_to_default_and_token_graph(self):
        counts, lines = self.convert(watdiv_reify.NAMED_GRAPH)

        self.assertEqual((2, 4), counts)
        self.assertEqual("<urn:a> <urn:p> <urn:b> .", lines[0])
        self.assertEqual("<urn:a> <urn:p> <urn:b> <urn:t:0> .", lines[1])

    def test_pure_mode_reproduces_the_historical_rdfstar_layout(self):
        counts, lines = self.convert(watdiv_reify.RDF_STAR, pure=True)

        self.assertEqual((2, 2), counts)
        self.assertTrue(all(line.startswith("<< ") for line in lines))
        self.assertFalse(any(line == "<urn:a> <urn:p> <urn:b> ." for line in lines))


if __name__ == "__main__":
    unittest.main()
