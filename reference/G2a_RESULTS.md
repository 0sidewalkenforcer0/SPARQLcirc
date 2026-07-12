# G2a — ProvSQL (modified PostgreSQL) vs ours (stock engine + circuit): PQE head-to-head on TPC-H

> ⚠️ **Timing numbers in this file are SUPERSEDED — cite [`CANONICAL_TIMINGS.md`](CANONICAL_TIMINGS.md).** They predate (or are single-run under) engine fix `1e67021`; the authoritative post-fix 5-run table lives in the canonical file, and the old rows are recorded in [`HISTORICAL_TIMINGS.md`](HISTORICAL_TIMINGS.md). The *methodology/findings* below still stand; only the absolute numbers moved.

E7 already showed our probabilities match **ProvSQL** — but on **3 toy instances**. G2a upgrades that
to the **shared TPC-H benchmark** (the relational workload ProvSQL was built for) at **14 908 / 125 154
answers**, and turns it into a *timed* head-to-head: same data, same query, same per-token weight, exact
PQE on both sides. Per ROUND 7, this folds into G3 (the end-to-end latency story) and is framed as
**comparable latency without an engine fork — not a speed win** (ProvSQL is a shared-circuit *peer*).

> **Timing note (read first).** All timings here are the **G4-rigorous warm** numbers (1 warm-up + 5
> timed runs, median [min–max]). An earlier draft of this file quoted a **cold** ProvSQL first-call
> (3.6 s) and wrongly concluded "ours ~2× faster"; the G4 rigor pass (see `G4_RESULTS.md`) corrected it —
> warm/steady-state ProvSQL is **~1.05 s** and is in fact *faster* than us. The robust result is
> **probability parity**, not a latency win.

## Setup

- **Data.** Official TPC-H `dbgen` at **SF 0.01** and **SF 0.1**. ProvSQL side: the `.tbl` files loaded
  straight into PostgreSQL tables (`g2a` / `g2a1` schemas). Our side: the *same* `.tbl` mapped to RDF by
  [`tpch/tbl_to_rdf.py`](tpch/tbl_to_rdf.py) and loaded into GraphDB (`tpch001` / `tpch01`) — **per-row**
  (naryrel) provenance, the granularity ProvSQL/SPARQLprov use (a *tuple* is the uncertain unit).
- **Query.** TPC-H **Q3 SPJ** — `customer ⋈ orders ⋈ lineitem`, `c_mktsegment = 'BUILDING'`, projecting
  `(o_orderkey, l_linenumber)`. Filter-free / non-aggregate (same skeleton as E9 / G3).
- **Weight.** Per-token **p = 0.5**, uniform, both sides.
- **ProvSQL PQE.** `add_provenance('tbl')` → `set_prob(provenance(), 0.5)` (per base row) →
  `probability(provenance())` per answer, inside PostgreSQL (ProvSQL 1.11.0-dev). [`tpch/g2a_provsql.sql`](tpch/g2a_provsql.sql).
- **Our PQE.** `CircuitRewriter` naryrel CONSTRUCT → **shared** ROBDD compile (once) → WMC every answer
  root — the G3 pipeline, on a **stock GraphDB** + a client compiler.

## Results (warm, G4 protocol)

| scale | answers | ProvSQL PQE (modified PG) | ours PQE (stock engine, G3) | ProvSQL p | ours p |
|---|--:|--:|--:|--:|--:|
| SF 0.01 (60 k lineitems)  |  14 908 | **1.05 s** [1.02–1.09] | **1.65 s** [1.64–1.67] | 0.1250 | 0.1250 |
| SF 0.1  (600 k lineitems) | 125 154 | **9.8 s** [9.4–10.0]   | 12.6 s construct + near-free compile | 0.1250 | 0.1250 |

*(ours SF 0.01 = G4 tpch-Q3: construct 1471 + compile 149 + WMC 35 ms. ours SF 0.1 = 12.6 s engine
construct of the 876 k-triple circuit; compile+WMC is Θ(N+S), near-free in principle but our pure-Python
compiler is the client bottleneck at that size — a native compiler / d4 (G6) removes it.)*

## Findings

- **Probability parity at benchmark scale — the result.** Both systems return **exactly 0.1250 = 0.5³**
  for *every* Q3 answer (each answer is `customer ⊗ order ⊗ lineitem`, three independent tokens). E7
  validated this agreement on 3 hand-built instances; G2a shows it holds against ProvSQL's own
  possible-world semantics across **14 908 and 125 154** real join outputs. Our circuit + WMC computes
  the *same* number ProvSQL's modified PostgreSQL does, at TPC-H scale.
- **Comparable latency — ProvSQL is modestly faster (warm), and that's fine.** Same order of magnitude
  at both scales; warm, ProvSQL is ~1.5× faster (SF 0.01: 1.05 s vs 1.65 s) — as ROUND 7 anticipated.
  ProvSQL evaluates provenance *inside* a tuned relational engine's operators; we build a circuit over a
  general-purpose SPARQL engine and compile client-side. We do **not** claim a speed win over ProvSQL.
- **The advantage is architectural, not latency.** We compute ProvSQL's probabilities on a **stock,
  unmodified SPARQL engine** — the emitted CONSTRUCTs are SPARQL-1.1-only and yield a **byte-identical
  circuit on 4 engines** (E10) — versus ProvSQL's **forked PostgreSQL** (a C extension, custom
  aggregates, a `provenance` column type). And the *fragments* differ: our method covers **property
  paths + full SPARQL** at KG scale (G1/G3/E10) that a relational engine does not address, while ProvSQL
  covers relational **aggregation** (our G9, out of scope). Complementary, not a race.
- **Both scale ~linearly with the join output.** ProvSQL 1.05 → 9.8 s (≈ 9×) and our construct 1.65 →
  12.6 s (≈ 7.6×) for 10× data (answers ≈ 8.4×) — PQE cost tracks #answers for this tree-join shape
  (no reconvergence; cf. E11).

## Caveats

- **Cold vs warm matters and is easy to get wrong** (this file's own first draft did). The cited numbers
  are warm medians over 5 runs after a warm-up; a cold first-call is ~3× slower on the ProvSQL side. G4
  fixes the protocol for all headline timings.
- ProvSQL `probability(provenance())` is its exact evaluator; we did not benchmark its `weightmc`
  d-DNNF path — exact vs exact. Shared HPC box (see `G4_RESULTS.md` env log); treat absolute times as
  order-of-magnitude. Our SF 0.1 client compile is a pure-Python artifact (see note above).
- ProvSQL is the **right** relational baseline — it *does* compute probabilities, unlike NPCS/SPARQLprov,
  which stop at how-provenance (G2b/G3). G2a's point is that we match it **without modifying the engine**
  and over a **broader query fragment** — not that we beat its latency.
