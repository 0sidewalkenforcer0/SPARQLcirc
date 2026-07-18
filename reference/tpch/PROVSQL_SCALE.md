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

## Scale trend — ONE protocol, fixed compiler (`g2a_provsql_vs_ours.csv`)
Both sides 3-run under a single protocol: **ProvSQL** = forced-eval `sum(probability_evaluate(provenance()))`
(the honest per-answer PQE, not the pruned `count(*)`); **ours** = end-to-end construct + shared ROBDD
compile + WMC with the **O(N) variable ordering** (`1eb35bf` — the earlier "3.3 s compile" was an O(N²)
list-membership scan; fixing it changed nothing but speed, order/WMC identical).

| SF | answers | ProvSQL PQE | ours end-to-end | **ours faster** |
|--:|--:|--:|--:|--:|
| 0.01 | 14 908 | 7 389 ms | 2 589 ms (con 2346 + comp+WMC 243) | **2.85×** |
| 0.1  | 125 154 | 63 959 ms | 22 632 ms (con 19913 + comp+WMC 2719) | **2.83×** |
| 0.3  | 367 475 | 187 567 ms | 87 530 ms (con 78394 + comp+WMC 9136) | **2.14×** |

**Ours is 2.1–2.85× faster than ProvSQL at every scale — honestly**, on a **stock** SPARQL engine with
**no kernel fork**, computing the **same exact** probabilities (parity: every Q3 answer = 0.5³ = 0.125,
`max_abs_error = 0`). compile+WMC now scales **O(N)** (243 → 2719 → 9136 ms), no longer the O(N²) blow-up.

This supersedes the earlier "comparable, no speed win" framing (which was inflated on our side by the
O(N²) ordering) **and** the protocol-mixed r9.7 draft. Positioning still leads with the unforked / broader
-fragment axis; the per-query speed advantage is now a clean secondary result, not a liability.

## SF 0.3 detail (ours)
367 475 answers, 734 950 gates, 1 469 900 edges (~2.2 M circuit elements) — the flat circuit grows
linearly with answers (our known construction cost). It is past e9's *default* 4 M-triple safety cap
(`E6_MAXTRIP`), not a hard limit; raised, it completes. In the consistent-protocol table above, ours SF0.3
= construct 78 394 + compile+WMC 9 136 = **87.5 s**, vs ProvSQL 187.6 s (**2.14×**). (compile+WMC is a
clean **O(N)** 9.1 s here, not the old O(N²) blow-up.)

## Figure
`presentation/figures/final/result_r9_7_provsql_tpch.{pdf,png}`: (a) 5 matched Q3 market segments
(ours vs ProvSQL, 5-run); (b) the PQE scale trend above. Generator `presentation/make_result_figures.py`.
