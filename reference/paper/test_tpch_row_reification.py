"""Regression tests for TPC-H per-row RDF-star 1.1 reification."""

import importlib.util
from pathlib import Path
import tempfile
import unittest


MODULE_PATH = Path(__file__).resolve().parents[1] / "tpch" / "reify_rows.py"
SPEC = importlib.util.spec_from_file_location("tpch_reify_rows", MODULE_PATH)
tpch_reify_rows = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(tpch_reify_rows)


BASE = "http://example.org/"
RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
OCCURRENCE_OF = "http://example.org/occurrenceOf"

INPUT = """\
<http://example.org/Customer/1> <http://www.w3.org/1999/02/22-rdf-syntax-ns#type> <http://example.org/Customer> .
<http://example.org/Customer/1> <http://example.org/c_name> "Customer One" .
<http://example.org/Order/2> <http://www.w3.org/1999/02/22-rdf-syntax-ns#type> <http://example.org/Order> .
<http://example.org/Order/2> <http://example.org/o_cust> <http://example.org/Customer/1> .
"""


class TpchRowReificationTest(unittest.TestCase):
    def convert(self, source_text=INPUT):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "tpch.nt"
            output = root / "tpch.mixed.ttls"
            source.write_text(source_text, encoding="utf-8")
            counts = tpch_reify_rows.reify_rows(source, output)
            return counts, output.read_text(encoding="utf-8").splitlines()

    def test_keeps_all_base_triples_and_adds_one_old_rdfstar_record_per_row(self):
        counts, lines = self.convert()

        self.assertEqual((4, 2, 6), counts)
        self.assertEqual(INPUT.splitlines()[0], lines[0])
        self.assertEqual(
            "<< <http://example.org/Customer/1> "
            "<http://www.w3.org/1999/02/22-rdf-syntax-ns#type> "
            "<http://example.org/Customer> >> "
            "<http://example.org/occurrenceOf> "
            "<http://example.org/Customer/1> .",
            lines[1],
        )
        self.assertNotIn("rdf:reifies", "\n".join(lines))
        self.assertFalse(any("<<(" in line for line in lines))

    def test_attribute_triples_are_not_independently_reified(self):
        _, lines = self.convert()

        occurrence_lines = [line for line in lines if "<%s>" % OCCURRENCE_OF in line]
        self.assertEqual(2, len(occurrence_lines))
        self.assertFalse(any("c_name" in line for line in occurrence_lines))
        self.assertFalse(any("o_cust" in line for line in occurrence_lines))

    def test_rejects_multiple_type_tokens_for_one_row(self):
        duplicate = INPUT + (
            "<http://example.org/Customer/1> <%(rdf_type)s> "
            "<%(base)sOther> .\n"
            % {"rdf_type": RDF_TYPE, "base": BASE}
        )
        with self.assertRaisesRegex(ValueError, "more than one rdf:type"):
            self.convert(duplicate)

    def test_rejects_non_ntriples_input(self):
        with self.assertRaisesRegex(ValueError, "not a ground N-Triples statement"):
            self.convert("@prefix ex: <http://example.org/> .\n")


if __name__ == "__main__":
    unittest.main()
