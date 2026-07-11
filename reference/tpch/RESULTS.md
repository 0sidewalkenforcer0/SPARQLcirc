# E9 — TPC-H (relational-derived RDF, per-row provenance)

Reproduces how **SPARQLprov** stresses a provenance engine on the relational TPC-H benchmark
(<https://www.tpc.org/tpch/>): the relational instance is directly mapped to RDF and queried with
the non-aggregate, filter-free **SPJ / MINUS skeletons** of the TPC-H templates, so the measured
cost is provenance-circuit construction over relational-shaped joins — not SQL aggregation.

## What E9 proves

The circuit machinery is **not WatDiv- or Wikidata-specific**. Given relational data mapped to RDF
and the **per-row** provenance granularity that ProvSQL/SPARQLprov use (a *tuple* is the uncertain
unit, not a triple), the same ⊕/⊗/⊖ construction:

1. builds over the star/chain/snowflake join shapes that TPC-H is designed to exercise;
2. produces **shared** structure — one customer row's token feeds every order and lineitem
   derivation that joins through it, so the circuit is a DAG, not a tree;
3. supports **non-monotone** TPC-H fragments (anti-joins / `NOT EXISTS` → `MINUS` → ⊖ gates);
4. yields probabilities that match exhaustive possible-world enumeration.

## Setup

- **Data.** Official `dbgen` (`tools/tpch-dbgen`) at scale factors 0.01 / 0.1 (/1). The `.tbl`
  files are directly mapped by [`tbl_to_rdf.py`](tbl_to_rdf.py) exactly as SPARQLprov's
  `tbl_to_rdf` (see [`README.md`](README.md)):
  - row → entity IRI `<Table/PK>` (composite keys joined: `<LineItem/1-1>`, `<PartSupp/2-3>`);
  - column → predicate = the bare column name under `BASE <http://example.org/>` (`<c_mktsegment>`);
  - foreign key → object edge to the referenced entity (`<Order/1> <o_custkey> <Customer/370>`);
  - `<Table/PK> a <Table>` per row.
  SF 0.01 → **1,255,420 triples**.
- **Provenance granularity.** The engine's **`naryrel`** reification scheme (added for E9;
  [`Reification.java`](../../engine/src/main/java/npcs/rewrite/Reification.java)): the token is the
  **subject** (the row entity), the data stays plain, so every triple about a row shares that row's
  token → provenance is **per-row**, exactly ProvSQL/SPARQLprov tuple granularity.
- **Queries.** Non-aggregate, filter-free skeletons of the TPC-H templates in
  [`skeletons/`](skeletons/): the join graph + the string-equality selections, with aggregation,
  `ORDER BY`, `LIMIT`, and numeric/date `FILTER` removed (they are orthogonal to provenance and are
  SPARQLprov's "base non-aggregate" fragment). Templates that are *only* aggregation (4, 13, 15, 17,
  18, 20, 21, 22) are omitted; the representative SPJ shapes and one MINUS anti-join are kept.

## Prediction (theory)

- Per-row construction is linear in the number of join witnesses: `gates ≈ 2 × derivations`
  (one ⊗ per witness + one ⊕ into its answer), `answers ≤ derivations`.
- Row tokens are **shared across derivations** (a BUILDING customer with *k* orders contributes one
  leaf referenced by *k* ⊗ gates) → the circuit is a DAG whose leaf count is the number of
  *distinct rows touched*, well below the derivation count.
- A `MINUS` skeleton emits one ⊖ per surviving left row → non-monotone gates appear on TPC-H.
- WMC == PWE on any instance small enough to enumerate.

## Results — SF 0.01, in-memory (`CircuitRun naryrel`, 1.26 M triples)

| skeleton | join shape                                   | deriv | gates | ⊖ | answers |
|----------|----------------------------------------------|------:|------:|---:|-------:|
| Q3       | customer(BUILDING) ⋈ orders ⋈ lineitem       | 14908 | 29816 | 0 | 14908 |
| Q10      | customer ⋈ orders ⋈ lineitem(returned)       | 14902 | 29804 | 0 | 14902 |
| Q5       | customer ⋈ orders ⋈ lineitem ⋈ supplier ⋈ nation ⋈ region (nation-closed) | 2333 | 4666 | 0 | 2333 |
| Q9       | part ⋈ partsupp ⋈ lineitem ⋈ orders ⋈ supplier ⋈ nation | 60175 | 120350 | 0 | 60175 |
| Mminus   | (orders ⋈ customer) **MINUS** customer(BUILDING) | 15337 | 61674 | **15000** | 1000 |

- `gates ≈ 2 × deriv` as predicted (each witness = one ⊗ + one ⊕). Q9's six-way join produces the
  largest circuit (60 k derivations); the nation-closed Q5 the smallest (2.3 k).
- **Mminus** materialises **15 000 ⊖ gates** — the non-monotone TPC-H fragment builds natively.
- **Correctness.** Tiny-instance WMC == PWE: max |Δ| = **5.6 × 10⁻¹⁷** (5 row tokens, 2 answers,
  all 2⁵ worlds enumerated) — per-row provenance is exact.

> **build_ms caveat.** In-memory `CircuitRun` reloads all 1.26 M triples per run (~10 s), so
> in-memory wall-clock is *load-dominated* and **not** a construction-time metric. Clean build times
> (data pre-loaded in GraphDB, only query eval + gate materialisation timed) and the SF 0.01 → 0.1 →
> 1 scaling curve are collected via GraphDB after the E8 Wikidata run frees it (SF 0.1 = 12 M
> triples already staged at `tpch-data/tpch.sf01.nt`).

## Caveats (stated, matching SPARQLprov's non-aggregate fragment)

- **No aggregation** — SPJ/MINUS skeletons only. TPC-H's `SUM`/`COUNT`/`AVG`/`GROUP BY` are not
  provenance operations; SPARQLprov measures the same base non-aggregate fragment.
- **No numeric/date `FILTER`** — dropped (orthogonal to circuit shape). String-equality selections
  (`c_mktsegment = "BUILDING"`, `l_returnflag = "R"`) are kept as they change the join witnesses.
- **Per-row (`naryrel`) granularity** — a tuple is the uncertain unit, matching ProvSQL and
  SPARQLprov; contrast E1–E8, where a *triple* (statement) is the unit.
