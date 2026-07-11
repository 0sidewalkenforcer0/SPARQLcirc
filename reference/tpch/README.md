# TPC-H queries (large-scale, relational→RDF — comparability with SPARQLprov & ProvSQL)

TPC-H is what **SPARQLprov** (RDF direct mapping, 1.2M–123M triples, scale factors `10^{i/4-2}`) and
**ProvSQL** (native SQL) evaluated, so running it gives a scale + comparability datapoint at the venue's
expected size.

## ⚠️ Scope caveat (important)
TPC-H is **aggregation-heavy** (SUM/COUNT/AVG + GROUP BY), and SPARQLcirc's circuit **does not cover
aggregation** (out of scope — see TECHREPORT §2). So on TPC-H we run only the **non-aggregate SPJ +
MINUS skeleton** of each query — i.e. the joins/selections/anti-joins *without* the final GROUP BY/SUM.
This is directly comparable to SPARQLprov's **"base non-aggregate"** measurements (their Fig. 3), not to
their aggregate numbers. It demonstrates **construction scaling on relational-derived RDF**, not
aggregate provenance. Wikidata (`reference/wikidata/`) is the full-fit large-scale dataset; TPC-H here is
comparability-only.

## Plan (server)
1. Generate TPC-H with `dbgen` at scale factors matching SPARQLprov (`10^{i/4-2}`, i=1..8 → 1.2M–123M triples).
2. **Direct-map** the tables to RDF: each row → a subject; each column → a predicate
   `tpch:<table>#<column>`; foreign keys become IRIs so joins become BGP joins. (SPARQLprov ships such a
   converter; we do not yet — a small converter is needed. Keep the mapping IRIs stable.)
3. Reify the RDF (`reference/watdiv/reify.py`), load into GraphDB, run the **non-aggregate** SPJ/MINUS
   skeletons of the SPARQLprov TPC-H fragment (they omit templates 4,13,15,17,18,20,21,22).
4. Report build_ms / gates / edges / answers / share, comparable to SPARQLprov base-non-aggregate.

Example skeleton (Q3-like, non-aggregate join; adapt predicates to the converter's vocab):
```sparql
PREFIX tpch: <http://example.org/tpch#>
SELECT ?o ?l WHERE {
  ?c tpch:customer#c_mktsegment "BUILDING" .
  ?o tpch:orders#o_custkey ?c .
  ?l tpch:lineitem#l_orderkey ?o .
}
```
