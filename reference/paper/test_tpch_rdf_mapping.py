"""Regression tests for the SPARQLprov-compatible TPC-H RDF mapping."""

import importlib.util
from pathlib import Path
import tempfile
import unittest


CONVERTER_PATH = Path(__file__).resolve().parents[1] / "tpch" / "tbl_to_rdf.py"
SKELETON_DIRECTORY = CONVERTER_PATH.parent / "skeletons"
SPEC = importlib.util.spec_from_file_location("tbl_to_rdf", CONVERTER_PATH)
tbl_to_rdf = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(tbl_to_rdf)

BASE = "http://example.org/"
RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
XSD = "http://www.w3.org/2001/XMLSchema#"

ROWS = {
    "part": "1|Part name|Manufacturer|Brand#1|STANDARD TYPE|7|SM BOX|12.34|Part comment|\n",
    "region": "2|EUROPE|Region comment|\n",
    "nation": "3|GERMANY|2|Nation comment|\n",
    "supplier": "4|Supplier|Address|3|10-123-456|45.67|Supplier comment|\n",
    "partsupp": "1|4|8|9.10|Part-supplier comment|\n",
    "customer": "5|Customer|Address|3|20-123-456|11.12|BUILDING|Customer comment|\n",
    "orders": "6|5|O|100.25|1995-03-16|1-URGENT|Clerk#1|0|Order comment|\n",
    "lineitem": (
        "6|1|4|2|17.00|200.50|0.04|0.02|N|O|1995-03-18|1995-03-17|"
        "1995-03-20|DELIVER IN PERSON|AIR|Line-item comment|\n"
    ),
}


def iri(local_name):
    return f"<{BASE}{local_name}>"


def literal(value, datatype=None):
    suffix = f"^^<{XSD}{datatype}>" if datatype else ""
    return f'"{value}"{suffix}'


def triple(subject, predicate, obj):
    predicate_iri = f"<{RDF_TYPE}>" if predicate == "type" else iri(predicate)
    return f"{iri(subject)} {predicate_iri} {obj} ."


EXPECTED_PROPERTIES = {
    "Part/1": [
        ("type", iri("Part")),
        ("p_name", literal("Part name")),
        ("p_mfgr", literal("Manufacturer")),
        ("p_brand", literal("Brand#1")),
        ("p_type", literal("STANDARD TYPE")),
        ("p_size", literal("7", "integer")),
        ("p_container", literal("SM BOX")),
        ("p_retailprice", literal("12.34", "decimal")),
        ("p_comment", literal("Part comment")),
    ],
    "Region/2": [
        ("type", iri("Region")),
        ("r_name", literal("EUROPE")),
        ("r_comment", literal("Region comment")),
    ],
    "Nation/3": [
        ("type", iri("Nation")),
        ("n_name", literal("GERMANY")),
        ("n_region", iri("Region/2")),
        ("n_comment", literal("Nation comment")),
    ],
    "Supplier/4": [
        ("type", iri("Supplier")),
        ("s_name", literal("Supplier")),
        ("s_address", literal("Address")),
        ("s_nation", iri("Nation/3")),
        ("s_phone", literal("10-123-456")),
        ("s_acctbal", literal("45.67", "decimal")),
        ("s_comment", literal("Supplier comment")),
    ],
    "PartSupp/1/4": [
        ("type", iri("PartSupp")),
        ("ps_part", iri("Part/1")),
        ("ps_supp", iri("Supplier/4")),
        ("ps_availqty", literal("8", "integer")),
        ("ps_supplycost", literal("9.10", "decimal")),
        ("ps_comment", literal("Part-supplier comment")),
    ],
    "Customer/5": [
        ("type", iri("Customer")),
        ("c_name", literal("Customer")),
        ("c_address", literal("Address")),
        ("c_nation", iri("Nation/3")),
        ("c_phone", literal("20-123-456")),
        ("c_acctbal", literal("11.12", "decimal")),
        ("c_mktsegment", literal("BUILDING")),
        ("c_comment", literal("Customer comment")),
    ],
    "Order/6": [
        ("type", iri("Order")),
        ("o_cust", iri("Customer/5")),
        ("o_orderstatus", literal("O")),
        ("o_totalprice", literal("100.25", "decimal")),
        ("o_orderdate", literal("1995-03-16", "date")),
        ("o_orderpriority", literal("1-URGENT")),
        ("o_clerk", literal("Clerk#1")),
        ("o_shippriority", literal("0", "integer")),
        ("o_comment", literal("Order comment")),
    ],
    "LineItem/6/2": [
        ("type", iri("LineItem")),
        ("l_order", iri("Order/6")),
        ("l_part", iri("Part/1")),
        ("l_supp", iri("Supplier/4")),
        ("l_linenumber", literal("2", "integer")),
        ("l_quantity", literal("17.00", "decimal")),
        ("l_extendedprice", literal("200.50", "decimal")),
        ("l_discount", literal("0.04", "decimal")),
        ("l_tax", literal("0.02", "decimal")),
        ("l_returnflag", literal("N")),
        ("l_linestatus", literal("O")),
        ("l_shipdate", literal("1995-03-18", "date")),
        ("l_commitdate", literal("1995-03-17", "date")),
        ("l_receiptdate", literal("1995-03-20", "date")),
        ("l_shipinstruct", literal("DELIVER IN PERSON")),
        ("l_shipmode", literal("AIR")),
        ("l_comment", literal("Line-item comment")),
    ],
}

EXPECTED_GRAPH = {
    triple(subject, predicate, obj)
    for subject, properties in EXPECTED_PROPERTIES.items()
    for predicate, obj in properties
}


class TpchRdfMappingTest(unittest.TestCase):
    def convert_fixture(self, root):
        for table, row in ROWS.items():
            (root / f"{table}.tbl").write_text(row, encoding="utf-8")
        output = root / "tpch.nt"
        triple_count = tbl_to_rdf.convert(root, output)
        return output, triple_count

    def test_matches_sparqlprov_graph(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            output, triple_count = self.convert_fixture(root)
            lines = output.read_text(encoding="utf-8").splitlines()

        self.assertEqual(63, triple_count)
        self.assertEqual(63, len(lines))
        self.assertEqual(EXPECTED_GRAPH, set(lines))

    def test_escapes_ntriples_literals(self):
        value = 'quote=" backslash=\\ tab=\t newline=\n'
        self.assertEqual(
            'quote=\\" backslash=\\\\ tab=\\t newline=\\n',
            tbl_to_rdf.escape_literal(value),
        )

    def test_committed_skeletons_use_sparqlprov_foreign_keys(self):
        legacy_predicates = {
            "c_nationkey",
            "l_orderkey",
            "l_partkey",
            "l_suppkey",
            "n_regionkey",
            "o_custkey",
            "ps_partkey",
            "ps_suppkey",
            "s_nationkey",
        }
        for path in SKELETON_DIRECTORY.glob("*.rq"):
            query = path.read_text(encoding="utf-8")
            for predicate in legacy_predicates:
                self.assertNotIn(f"<{predicate}>", query, path.name)

if __name__ == "__main__":
    unittest.main()
