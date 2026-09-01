"""Convert TPC-H ``.tbl`` files to SPARQLprov-compatible RDF.

The original SPARQLprov converter writes one compressed Turtle file per table.
This port writes a single N-Triples file, but deliberately preserves the same
RDF graph: row IRIs, predicates, foreign-key edges, omitted primary-key
properties, and XSD literal datatypes all match ``tbl_to_rdf.rb``.

Usage: ``python3 tbl_to_rdf.py <tbl-dir> <out.nt>``
"""

import os
import sys


BASE = "http://example.org/"
RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
XSD = "http://www.w3.org/2001/XMLSchema#"

# The insertion order matches SPARQLprov's Ruby converter. Each entry contains
# the input columns followed by the columns used to construct the row IRI.
SCHEMA = {
    "part": (
        ("p_partkey", "p_name", "p_mfgr", "p_brand", "p_type", "p_size",
         "p_container", "p_retailprice", "p_comment"),
        ("p_partkey",),
    ),
    "region": (
        ("r_regionkey", "r_name", "r_comment"),
        ("r_regionkey",),
    ),
    "nation": (
        ("n_nationkey", "n_name", "n_regionkey", "n_comment"),
        ("n_nationkey",),
    ),
    "supplier": (
        ("s_suppkey", "s_name", "s_address", "s_nationkey", "s_phone",
         "s_acctbal", "s_comment"),
        ("s_suppkey",),
    ),
    "partsupp": (
        ("ps_partkey", "ps_suppkey", "ps_availqty", "ps_supplycost",
         "ps_comment"),
        ("ps_partkey", "ps_suppkey"),
    ),
    "customer": (
        ("c_custkey", "c_name", "c_address", "c_nationkey", "c_phone",
         "c_acctbal", "c_mktsegment", "c_comment"),
        ("c_custkey",),
    ),
    "orders": (
        ("o_orderkey", "o_custkey", "o_orderstatus", "o_totalprice",
         "o_orderdate", "o_orderpriority", "o_clerk", "o_shippriority",
         "o_comment"),
        ("o_orderkey",),
    ),
    "lineitem": (
        ("l_orderkey", "l_partkey", "l_suppkey", "l_linenumber",
         "l_quantity", "l_extendedprice", "l_discount", "l_tax",
         "l_returnflag", "l_linestatus", "l_shipdate", "l_commitdate",
         "l_receiptdate", "l_shipinstruct", "l_shipmode", "l_comment"),
        ("l_orderkey", "l_linenumber"),
    ),
}

ENTITY = {
    "part": "Part",
    "region": "Region",
    "nation": "Nation",
    "supplier": "Supplier",
    "partsupp": "PartSupp",
    "customer": "Customer",
    "orders": "Order",
    "lineitem": "LineItem",
}

# input column -> (SPARQLprov predicate, referenced table)
FOREIGN_KEYS = {
    "nation": {"n_regionkey": ("n_region", "region")},
    "supplier": {"s_nationkey": ("s_nation", "nation")},
    "partsupp": {
        "ps_partkey": ("ps_part", "part"),
        "ps_suppkey": ("ps_supp", "supplier"),
    },
    "customer": {"c_nationkey": ("c_nation", "nation")},
    "orders": {"o_custkey": ("o_cust", "customer")},
    "lineitem": {
        "l_orderkey": ("l_order", "orders"),
        "l_partkey": ("l_part", "part"),
        "l_suppkey": ("l_supp", "supplier"),
    },
}

# SPARQLprov encodes single-column primary keys only in the subject IRI. The
# composite-key tables still emit their key components as relations/attributes.
OMITTED_COLUMNS = {
    "p_partkey",
    "r_regionkey",
    "n_nationkey",
    "s_suppkey",
    "c_custkey",
    "o_orderkey",
}

DATATYPES = {
    "p_size": "integer",
    "p_retailprice": "decimal",
    "s_acctbal": "decimal",
    "ps_availqty": "integer",
    "ps_supplycost": "decimal",
    "c_acctbal": "decimal",
    "o_totalprice": "decimal",
    "o_orderdate": "date",
    "o_shippriority": "integer",
    "l_linenumber": "integer",
    "l_quantity": "decimal",
    "l_extendedprice": "decimal",
    "l_discount": "decimal",
    "l_tax": "decimal",
    "l_shipdate": "date",
    "l_commitdate": "date",
    "l_receiptdate": "date",
}

ESCAPES = {
    "\\": "\\\\",
    '"': '\\"',
    "\t": "\\t",
    "\b": "\\b",
    "\n": "\\n",
    "\r": "\\r",
    "\f": "\\f",
}


def escape_literal(value):
    """Return an N-Triples-safe lexical form without surrounding quotes."""
    escaped = []
    for character in value:
        if character in ESCAPES:
            escaped.append(ESCAPES[character])
        elif ord(character) < 0x20 or ord(character) == 0x7F:
            escaped.append(f"\\u{ord(character):04X}")
        else:
            escaped.append(character)
    return "".join(escaped)


def iri(local_name):
    return f"<{BASE}{local_name}>"


def literal(value, datatype=None):
    term = f'"{escape_literal(value)}"'
    if datatype is not None:
        term += f"^^<{XSD}{datatype}>"
    return term


def convert(tbl_dir, output_path):
    """Convert all available TPC-H tables and return the triple count."""
    triple_count = 0
    with open(output_path, "w", encoding="utf-8", newline="\n") as output:
        for table, (columns, key_columns) in SCHEMA.items():
            input_path = os.path.join(tbl_dir, f"{table}.tbl")
            if not os.path.exists(input_path):
                continue

            entity = ENTITY[table]
            foreign_keys = FOREIGN_KEYS.get(table, {})
            with open(input_path, encoding="utf-8") as source:
                for line in source:
                    values = line.rstrip("\r\n").split("|")
                    if len(values) < len(columns):
                        continue

                    row = dict(zip(columns, values))
                    key = "/".join(row[column] for column in key_columns)
                    subject = iri(f"{entity}/{key}")
                    output.write(f"{subject} <{RDF_TYPE}> {iri(entity)} .\n")
                    triple_count += 1

                    for column in columns:
                        if column in OMITTED_COLUMNS:
                            continue

                        value = row[column]
                        if column in foreign_keys:
                            predicate, referenced_table = foreign_keys[column]
                            referenced_entity = ENTITY[referenced_table]
                            obj = iri(f"{referenced_entity}/{value}")
                        else:
                            predicate = column
                            obj = literal(value, DATATYPES.get(column))
                        output.write(f"{subject} {iri(predicate)} {obj} .\n")
                        triple_count += 1

    return triple_count


def main(tbl_dir, output_path):
    triple_count = convert(tbl_dir, output_path)
    print(f"wrote {triple_count} triples -> {output_path}")
    return triple_count


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("usage: tbl_to_rdf.py <tbl-dir> <out.nt>")
        sys.exit(2)
    main(sys.argv[1], sys.argv[2])
