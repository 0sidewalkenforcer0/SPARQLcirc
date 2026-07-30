# TPC-H queries (large-scale, relational→RDF — comparability with SPARQLprov & ProvSQL)

TPC-H is the **relational** benchmark used by **SPARQLprov** (via RDF direct mapping, 1.2M–123M triples,
scale factors `10^{i/4-2}`) and **ProvSQL** (native SQL). Running it gives a scale + comparability
datapoint at the venue's expected size. **NPCS never uses TPC-H** — it is RDF-native (WatDiv +
Wikidata/WDBench), so E9 is a comparison vs **SPARQLprov + ProvSQL only**; the NPCS comparison lives on
Wikidata (E8, `reference/wikidata/`).

## ⚠️ Scope caveats (state all three in the results)
1. **No aggregation.** TPC-H is aggregation-heavy (SUM/COUNT/AVG + GROUP BY); our circuit does not cover
   aggregation (out of scope — TECHREPORT §2). We run only the **non-aggregate SPJ + MINUS skeleton** —
   joins/selections/anti-joins without the final GROUP BY/SUM — directly comparable to SPARQLprov's
   **"base non-aggregate"** (their Fig. 3), not to their aggregate numbers.
2. **No FILTER in the recorded runs.** SPARQLprov's `*_non_aggregate` queries still carry range/date
   FILTERs; the runs recorded here drop them and use the **pure BGP-join skeleton** — thinner still.
   Say so explicitly. (The circuit rewriter *does* support FILTER now — TECHREPORT.md §2 — so this is a
   property of these committed measurements, not of the system; re-running with the FILTERs restored
   would make the comparison tighter.)
3. **Per-row provenance** (see below) — the uncertain unit is a *row*, not a triple.

Wikidata (`reference/wikidata/`) is the full-fit large-scale dataset; TPC-H here is comparability-only.

## How SPARQLprov maps TPC-H (their `bin/tbl_to_rdf.rb` — reproduce this exactly)
Textbook direct mapping, one translator per table:
- **row → entity IRI** `<Table/PK>` (composite keys join the components: `<LineItem/order/linenumber>`,
  `<PartSupp/part/supp>`).
- **column → predicate** = the bare column name, relative to `BASE <http://example.org/>` —
  `<o_orderdate>`, `<l_discount>`, `<c_mktsegment>` (**not** `tpch:table#col`).
- **foreign key → object edge to the referenced entity IRI**: `<Order/456> <o_cust> <Customer/789>`,
  `<LineItem/../..> <l_order> <Order/456>`. So relational joins become plain BGP joins over shared IRIs.
- **`<Table/PK> a <Table>`** per row; literals typed `xsd:integer` / `xsd:decimal` / `xsd:date` / string.

### Provenance granularity — PER ROW (matches SPARQLprov `naryrel` + ProvSQL)
SPARQLprov's TPC-H reification is **n-ary-relationship (`naryrel`)**: the provenance **token is the row
entity itself** (`BIND(?order AS ?..._statement)`), *not* a reified triple — one token per row (per-tuple
provenance, exactly ProvSQL's granularity). Our default is per-*triple*, which is **wrong here**: a row's
attributes are not independently uncertain, and per-triple inflates the token count ~10×. **So reify per
row**: mark only the per-row **`<Table/PK> a <Table>`** triple as the token and keep attribute triples
deterministic; each query must then touch `?x a <Table>` for every row it depends on. (Alternative: add a
`naryrel` reification scheme to the engine — token = row entity — to match SPARQLprov token-for-token.)

## Plan (server)
1. Generate TPC-H with `dbgen` at SPARQLprov's scale factors (`10^{i/4-2}`, i=1..8 → 1.2M–123M triples).
2. **Direct-map** to RDF reproducing the mapping above — a `tbl_to_rdf.py` port (SPARQLprov ships the Ruby
   original; we still need the ~50-line Python port). Keep the IRIs/vocab stable.
3. Reify **per row** (token = the `a <Table>` triple), load into GraphDB, run the **non-aggregate,
   filter-free** SPJ/MINUS skeletons of the SPARQLprov fragment (they omit templates 4,13,15,17,18,20,21,22).
4. Report build_ms / gates / edges / answers / share, comparable to SPARQLprov base-non-aggregate.

Example skeleton (Q3-like, non-aggregate, filter-free; SPARQLprov's actual vocab):
```sparql
BASE <http://example.org/>
SELECT ?order ?lineitem WHERE {
  ?customer a <Customer> ; <c_mktsegment> "BUILDING" .
  ?order    a <Order>    ; <o_cust> ?customer .
  ?lineitem a <LineItem> ; <l_order> ?order .
}
```
The `a <Table>` triples are the per-row provenance tokens; `<c_mktsegment>` / `<o_cust>` / `<l_order>`
carry the joins. (SPARQLprov's original Q3 additionally FILTERs on `o_orderdate` / `l_shipdate` — dropped.)
