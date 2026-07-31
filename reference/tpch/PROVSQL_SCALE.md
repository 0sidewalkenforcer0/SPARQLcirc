# ProvSQL vs ours — TPC-H Q3 PQE scale sweep (r9.7)

Extends the G2a head-to-head (`../RESULTS.md` §1) from 2 scale factors to a **scale trend**, and
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

## Scale trend — FAIR, uncontended, 3-run (`g2a_provsql_vs_ours.csv`)
Both sides measured with **nothing else running** (load ≈ 0.1), 1 warm-up + 3 timed, median. **ProvSQL** =
`sieve` (its practical in-process exact method; the `NULL` default auto-picks the same and agrees within
noise). **Ours** = end-to-end construct + shared ROBDD compile + WMC with the **O(N)** ordering (`1eb35bf`).

| SF | answers | ProvSQL (sieve) | ours end-to-end | **ours faster** |
|--:|--:|--:|--:|--:|
| 0.01 | 14 908 | 7 573 ms | 2 629 ms | **2.88×** |
| 0.1  | 125 154 | 63 165 ms | 23 320 ms | **2.71×** |
| 0.3  | 367 475 | 184 952 ms | 70 079 ms | **2.64×** |

**Ours is 2.6–2.9× faster, consistently, computing the same exact probabilities** (parity: every Q3
answer = 0.5³ = 0.125, `max_abs_error = 0`).

### Why — and the honest caveat (a reviewer WILL ask "they forked the engine, why are you faster?")
- **The engine fork buys ProvSQL capability, not probability-speed.** It captures provenance natively
  (a gate per intermediate tuple) — that *adds* per-operator overhead; the #P-hard probability step is a
  separate algorithm both systems pay for.
- **The gap is per-answer overhead, not engine-avoidance.** ProvSQL's API evaluates probability
  **per answer row** — 14 908 in-database `probability_evaluate` calls (recursive over a gate table). Ours
  does **one in-memory batch WMC** over the shared circuit (0.24 s at SF0.01). For Q3 (answers barely
  share) the win is the in-memory-batch-vs-per-row-in-DB overhead, not cross-answer amortization.
- **Caveat to state:** this is vs ProvSQL's `sieve`. Its `compilation` (d4) method is **not wired to batch**
  in this deployment (it shells out to d4 **per answer** → 62 s / 1 000, a config artifact, not
  representative). A ProvSQL that batched d4 over the shared circuit could narrow the gap. So claim
  "faster with each system's practical exact method", not "fundamentally faster".

### Measurement-integrity note (do not repeat)
An earlier draft reported these while a 67 GB extraction ran concurrently (inflating ProvSQL to ~7.4 s
*and* our SF0.3 construct to 87.5 s → understated 2.14×). A brief mid-check hit a single warm ProvSQL
outlier (4.9 s) that suggested "only 1.9×". The **uncontended 3-run above is authoritative**: ~2.6–2.9×.
Positioning still leads with the unforked / broader-fragment axis; speed is a clean, honest secondary.

## SF 0.3 detail (ours)
367 475 answers, 734 950 gates, 1 469 900 edges (~2.2 M circuit elements) — the flat circuit grows
linearly with answers (our known construction cost). It is past e9's *default* 4 M-triple safety cap
(`E6_MAXTRIP`), not a hard limit; raised, it completes. In the consistent-protocol table above, ours SF0.3
= construct 78 394 + compile+WMC 9 136 = **87.5 s**, vs ProvSQL 187.6 s (**2.14×**). (compile+WMC is a
clean **O(N)** 9.1 s here, not the old O(N²) blow-up.)

## Figure
`presentation/figures/final/result_r9_7_provsql_tpch.{pdf,png}`: (a) 5 matched Q3 market segments
(ours vs ProvSQL, 5-run); (b) the PQE scale trend above. Generator `presentation/make_result_figures.py`.
