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
| SF | answers | ProvSQL PQE (modified PG) | ours (stock engine) | ProvSQL ms/answer |
|--:|--:|--:|--:|--:|
| 0.01 | 14 908 | 3 556 ms | 1 655 ms | 0.238 |
| 0.1  | 125 154 | 30 254 ms | 12 643 ms | 0.242 |
| 0.3  | 367 475 | 87 284 ms | _(e9 SF0.3 — see below)_ | 0.238 |

**ProvSQL is strikingly linear in answer count: ~0.24 ms/answer at every scale.** At SF 0.01 and 0.1 ours
(stock engine + client compiler, **no engine fork**) is **~2× faster** than ProvSQL's honest per-answer PQE.

## Honest caveat at SF 0.3
Our **flat `naryrel`** Q3 circuit at SF 0.3 is ~5 M circuit triples (367 k answers × ~13), past e9's 4 M
safety cap; the CONSTRUCT itself is at the edge of feasibility (the flat construct grows linearly with
answers — our known construction cost). ProvSQL's integrated PQE has no separate circuit to materialise.
See the r9.7 figure for exactly which points are measured vs walled.

## Figure
`presentation/figures/final/result_r9_7_provsql_tpch.{pdf,png}`: (a) 5 matched Q3 market segments
(ours vs ProvSQL, 5-run); (b) the PQE scale trend above. Generator `presentation/make_result_figures.py`.
