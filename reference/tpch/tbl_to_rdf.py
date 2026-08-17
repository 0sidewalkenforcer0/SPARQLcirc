"""E9 - TPC-H direct mapping to RDF, reproducing SPARQLprov's tbl_to_rdf (row->entity IRI,
column->bare predicate under BASE <http://example.org/>, foreign key->edge to the referenced
entity, one `<Entity> a <Table>` per row). The data is PLAIN (no reification) -- provenance is
taken PER ROW via the engine's `naryrel` scheme (token = the row entity), matching SPARQLprov's
naryrel + ProvSQL's per-tuple granularity.

Usage:  python3 tbl_to_rdf.py <tbl-dir> <out.nt>   (reads *.tbl produced by dbgen)
"""
import sys, os, glob

BASE = "http://example.org/"
# table -> (columns, primary-key columns, {fk-column: referenced-table})
SCHEMA = {
    "region":   (["r_regionkey", "r_name", "r_comment"], ["r_regionkey"], {}),
    "nation":   (["n_nationkey", "n_name", "n_regionkey", "n_comment"], ["n_nationkey"], {"n_regionkey": "region"}),
    "supplier": (["s_suppkey", "s_name", "s_address", "s_nationkey", "s_phone", "s_acctbal", "s_comment"],
                 ["s_suppkey"], {"s_nationkey": "nation"}),
    "customer": (["c_custkey", "c_name", "c_address", "c_nationkey", "c_phone", "c_acctbal", "c_mktsegment", "c_comment"],
                 ["c_custkey"], {"c_nationkey": "nation"}),
    "part":     (["p_partkey", "p_name", "p_mfgr", "p_brand", "p_type", "p_size", "p_container", "p_retailprice", "p_comment"],
                 ["p_partkey"], {}),
    "partsupp": (["ps_partkey", "ps_suppkey", "ps_availqty", "ps_supplycost", "ps_comment"],
                 ["ps_partkey", "ps_suppkey"], {"ps_partkey": "part", "ps_suppkey": "supplier"}),
    "orders":   (["o_orderkey", "o_custkey", "o_orderstatus", "o_totalprice", "o_orderdate",
                  "o_orderpriority", "o_clerk", "o_shippriority", "o_comment"],
                 ["o_orderkey"], {"o_custkey": "customer"}),
    "lineitem": (["l_orderkey", "l_partkey", "l_suppkey", "l_linenumber", "l_quantity", "l_extendedprice",
                  "l_discount", "l_tax", "l_returnflag", "l_linestatus", "l_shipdate", "l_commitdate",
                  "l_receiptdate", "l_shipinstruct", "l_shipmode", "l_comment"],
                 ["l_orderkey", "l_linenumber"], {"l_orderkey": "orders", "l_partkey": "part", "l_suppkey": "supplier"}),
}
ENTITY = {"region": "Region", "nation": "Nation", "supplier": "Supplier", "customer": "Customer",
          "part": "Part", "partsupp": "PartSupp", "orders": "Order", "lineitem": "LineItem"}

def esc(s):
    return s.replace("\\", "\\\\").replace('"', '\\"')

def main(tbldir, out):
    n = 0
    with open(out, "w", encoding="utf-8", newline="\n") as g:
        for table, (cols, pk, fks) in SCHEMA.items():
            path = os.path.join(tbldir, f"{table}.tbl")
            if not os.path.exists(path):
                continue
            ecls = ENTITY[table]
            with open(path, encoding="utf-8") as source:
                for line in source:
                    vals = line.rstrip("\n").split("|")
                    if len(vals) < len(cols):
                        continue
                    row = dict(zip(cols, vals))
                    pkval = "-".join(row[k] for k in pk)
                    subj = f"<{BASE}{ecls}/{pkval}>"
                    g.write(f"{subj} <http://www.w3.org/1999/02/22-rdf-syntax-ns#type> <{BASE}{ecls}> .\n"); n += 1
                    for c in cols:
                        v = row[c]
                        if c in fks:
                            g.write(f"{subj} <{BASE}{c}> <{BASE}{ENTITY[fks[c]]}/{v}> .\n")
                        else:
                            g.write(f'{subj} <{BASE}{c}> "{esc(v)}" .\n')
                        n += 1
    print(f"wrote {n} triples -> {out}")

if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("usage: tbl_to_rdf.py <tbl-dir> <out.nt>"); sys.exit(2)
    main(sys.argv[1], sys.argv[2])
