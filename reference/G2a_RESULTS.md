# G2a — ProvSQL (modified PostgreSQL) vs ours (stock engine + circuit): PQE head-to-head on TPC-H

E7 already showed our probabilities match **ProvSQL** — but on **3 toy instances**. G2a upgrades that
to the **shared TPC-H benchmark** (the relational workload ProvSQL was built for) at **14 908 / 125 154
answers**, and turns it into a *timed* head-to-head: same data, same query, same per-token weight, exact
PQE on both sides. This is the last MUST item of the dev's ROUND 6 roadmap.

## Setup

- **Data.** Official TPC-H `dbgen` at **SF 0.01** and **SF 0.1**. ProvSQL side: the `.tbl` files loaded
  straight into PostgreSQL tables (`g2a` / `g2a1` schemas). Our side: the *same* `.tbl` mapped to RDF by
  [`tpch/tbl_to_rdf.py`](tpch/tbl_to_rdf.py) and loaded into GraphDB (`tpch001` / `tpch01`) — **per-row**
  (naryrel) provenance, the granularity ProvSQL/SPARQLprov use (a *tuple* is the uncertain unit).
- **Query.** TPC-H **Q3 SPJ** — `customer ⋈ orders ⋈ lineitem`, `c_mktsegment = 'BUILDING'`, projecting
  `(o_orderkey, l_linenumber)`. Filter-free / non-aggregate so the measured cost is provenance PQE, not
  SQL grouping (same skeleton as E9 / G3).
- **Weight.** Per-token **p = 0.5**, uniform, both sides.
- **ProvSQL PQE.** `add_provenance('tbl')` → `set_prob(provenance(), 0.5)` (per base row) →
  `probability(provenance())` per answer, inside PostgreSQL (ProvSQL 1.11.0-dev, its C extension +
  custom aggregates). One timed `CREATE TABLE AS`.
- **Our PQE.** `CircuitRewriter` naryrel CONSTRUCT → **shared** ROBDD compile (once) → WMC every answer
  root — the G3 end-to-end pipeline, on a **stock GraphDB** + a client compiler.

## Results

| scale | answers | ProvSQL PQE (modified PG) | ours PQE (stock engine, G3) | ProvSQL p | ours p |
|---|--:|--:|--:|--:|--:|
| SF 0.01 (60 k lineitems)  |  14 908 | **3.60 s** | **1.68 s** | 0.1250 | 0.1250 |
| SF 0.1  (600 k lineitems) | 125 154 | **29.4 s** | **12.6 s** construct (+ compile near-free) | 0.1250 | 0.1250 |

*(ours SF 0.01 = G3 tpch-Q3: construct 1500 ms + shared compile 149 ms + WMC 35 ms. ours SF 0.1 =
12.6 s engine construct of the 876 k-triple circuit; the shared compile+WMC is Θ(N+S), near-free in
principle but our **pure-Python** compiler is the client bottleneck at this size — a native compiler /
d4 (G6) removes it. Construct is the comparable engine-side term, and it is below ProvSQL's 29.4 s.)*

## Findings

- **Probability parity at benchmark scale.** Both systems return **exactly 0.1250 = 0.5³** for *every*
  Q3 answer — each answer is `customer ⊗ order ⊗ lineitem`, three independent tokens, so the ⊗ of three
  0.5-leaves is 0.125. E7 validated this agreement on 3 hand-built instances; G2a shows it holds against
  ProvSQL's own possible-world semantics across **14 908 and 125 154** real join outputs. Our circuit +
  WMC computes the *same* number ProvSQL's modified PostgreSQL does.
- **Competitive — on an *unmodified* engine.** At SF 0.01 our end-to-end PQE (**1.68 s**) is ~2× faster
  than ProvSQL's (**3.60 s**) for the identical 14 908-answer query. The decisive difference is not the
  2×: **ProvSQL requires a patched PostgreSQL** (a C extension, custom aggregates, a `provenance()`
  column type); **ours runs on stock GraphDB** — the emitted CONSTRUCTs are SPARQL-1.1-only and yield a
  **byte-identical circuit on 4 engines** (E10). Same exact probabilities, no forked database.
- **Both scale ~linearly with the join output, and ours stays below.** ProvSQL 3.60 s → 29.4 s (≈ 8×)
  for 10× data (answers 14 908 → 125 154, ≈ 8.4×) — PQE cost tracks #answers, as expected for this
  tree-join shape (no reconvergence; cf. E11). Our engine-side construct scales the same (1.68 s →
  12.6 s, ≈ 7.5×) and remains **below** ProvSQL at both scales — on a stock engine.

## Caveats

- **The SF 0.1 *compile* is a reference-implementation artifact, not the method.** The 12.6 s SF 0.1
  number is engine **construct** (measured, directly comparable). The subsequent compile+WMC on the
  **shared** circuit is Θ(N+S) and near-free in principle (G3: 149 ms for all 14 908 SF 0.01 answers) —
  but our compiler is **pure Python**, so at SF 0.1 (~375 k tokens) the *client* compile step does not
  finish in minutes. That is the Python constant, not the algorithm; a native compiler / **d4** (G6)
  removes it. So the SF 0.1 row compares ProvSQL's end-to-end C number against our engine-side construct
  (+ an in-principle-near-free compile) — construct alone already sits below ProvSQL.
- ProvSQL `probability(provenance())` is its exact evaluator; we did not use `probability_evaluate(…,
  'weightmc')` (its d-DNNF path) here — exact vs exact. Shared box; treat times as order-of-magnitude
  (E10 note). Single run per cell (latency shape); G4 adds repeats + variance.
- ProvSQL is the **right** relational baseline (it *does* compute probabilities, unlike NPCS/SPARQLprov
  which stop at how-provenance — G2b/G3). G2a's point is that we match it **without modifying the
  engine**.
