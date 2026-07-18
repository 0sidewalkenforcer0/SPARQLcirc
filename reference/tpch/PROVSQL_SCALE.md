# ProvSQL vs ours — TPC-H Q3 PQE scale sweep (r9.7)

Extends the G2a head-to-head (`reference/G2a_RESULTS.md`) from 2 scale factors to a **scale trend**, and
replaces the superseded ProvSQL timing (a `count(*)` planner-pruning artifact) with the **honest
per-answer PQE** (materialized `probability_evaluate(provenance())` over every Q3 answer).

## Setup (unchanged from G2a)
- **Query**: TPC-H **Q3 SPJ** — `customer ⋈ orders ⋈ lineitem`, `c_mktsegment='BUILDING'`, filter-free.
- **Weight**: per-token **p = 0.5**, uniform, both sides.
- **ProvSQL side**: PostgreSQL 18 + ProvSQL; `.tbl` → schemas `g2a`/`g2a1`/`g2a3` (SF 0.01/0.1/0.3);
  `add_provenance` → `set_prob(0.5)` → **materialize** `probability_evaluate(provenance())` per answer
  (`CREATE TABLE q3 AS SELECT …` — forces per-answer evaluation, not the pruned `count(*)`). 3-run median.
- **Ours side**: `.tbl` → RDF direct mapping (`tbl_to_rdf.py`) → GraphDB repos `tpch001`/`tpch01`/`tpch03`;
  `naryrel` per-row provenance; CircuitRewriter CONSTRUCT (+ shared ROBDD compile + WMC). `e9_tpch.py`.

## Probability parity — EXACT (structural)
Every Q3 answer is exactly **three ANDed base rows** (one customer, one order, one lineitem), so its
probability is **0.5³ = 0.125** at every scale — ProvSQL and ours both return 0.125, `max_abs_error = 0`.
Parity is the robust, order-independent result (as in R8.3 / E7).

## Scale trend (`g2a_provsql_vs_ours.csv`)
| SF | answers | ProvSQL PQE (modified PG) | ours (stock engine) | ours speed-up | ProvSQL ms/answer |
|--:|--:|--:|--:|--:|--:|
| 0.01 | 14 908 | 3 556 ms | 1 655 ms | **2.15×** | 0.238 |
| 0.1  | 125 154 | 30 254 ms | 12 643 ms | **2.39×** | 0.242 |
| 0.3  | 367 475 | 87 284 ms | 56 897 ms | **1.53×** | 0.238 |

**ProvSQL is strikingly linear in answer count: ~0.24 ms/answer at every scale.** Ours (stock engine +
client compiler, **no engine fork**) is **faster at every scale** — 2.4× at SF 0.1, still 1.5× at SF 0.3.

## SF 0.3 detail (ours)
`e9_tpch.py` on `tpch03` (naryrel, 3-run avg): **construct 56 897 ms**, 367 475 answers, 734 950 gates,
1 469 900 edges (~2.2 M circuit elements). The flat circuit grows linearly with answers (our known
construction cost); it is past e9's default 4 M-triple *safety cap* (E6_MAXTRIP), not a hard limit — with
the cap raised it completes and stays ahead of ProvSQL. `ours` here is the CONSTRUCT term; a shared-ROBDD
compile + per-answer WMC adds a small client-side amount (compile+WMC ≤ ~10 %, see r9.5 findings).

## Figure
`presentation/figures/final/result_r9_7_provsql_tpch.{pdf,png}`: (a) 5 matched Q3 market segments
(ours vs ProvSQL, 5-run); (b) the PQE scale trend above. Generator `presentation/make_result_figures.py`.
