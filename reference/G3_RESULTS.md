# G3 — end-to-end PQE latency (construct → compile → WMC)

> ⚠️ **Timing numbers in this file are SUPERSEDED — cite [`CANONICAL_TIMINGS.md`](CANONICAL_TIMINGS.md).** They predate (or are single-run under) engine fix `1e67021`; the authoritative post-fix 5-run table lives in the canonical file, and the old rows are recorded in [`HISTORICAL_TIMINGS.md`](HISTORICAL_TIMINGS.md). The *methodology/findings* below still stand; only the absolute numbers moved.

NPCS/SPARQLprov produce how-provenance and **stop** — they compute no probability. SPARQLcirc runs the
full probabilistic-query-evaluation pipeline. E3 timed *construction* and E4/E11 timed *compile+WMC*
apart; **G3 joins them into one end-to-end wall-clock** (all answers), on the loaded GraphDB repos
(WatDiv / TPC-H / Wikidata, the last a **property path** now that G1 makes paths scale).
`g3_pqe_latency.py` → `g3_pqe.csv`. Per-token p = 0.5; compile = the **shared** ROBDD (compile once,
E11), not per-answer. **Regenerated on the post-`1e67021` jar** (term-type-aware gate identity).

| query | dataset | answers | construct | compile (shared) | WMC | **total PQE** |
|---|---|--:|--:|--:|--:|--:|
| watdiv-Sstar       | WatDiv 32.7 M reified        |     2 |   20 ms |    2 ms |  0 ms | **22 ms** |
| tpch-Q3 (naryrel)  | TPC-H 1.26 M                 | 14908 | 3860 ms |  177 ms | 57 ms | **4.09 s** |
| wikidata-WDpath    | Wikidata 2.13 B (`P279+`, G1)|    16 | 3358 ms | 3871 ms |  7 ms | **7.24 s** |

All probabilities valid (WMC ∈ [0,1]); exactness is E1/E11/G6 (WMC == PWE, re-verified post-fix).

## Findings

- **For BGP / tree-join PQE, construct dominates and compile+WMC is near-free.** TPC-H Q3: the engine
  builds the shared circuit in 3.86 s, then **compile+WMC for all 14 908 answers is 234 ms (≈ 6 %)** —
  the stage the how-provenance baselines lack, essentially free through the shared circuit (a per-answer
  completion pays Θ(N·S), E11 Result 2). WatDiv star is the same shape (compile 2 ms).
- **For a recursive property path, the compile is now a real cost — and that is the point of G6.** After
  `1e67021` un-merges the reach-states the old key wrongly collapsed, WD-path (`Q7397 wdt:P279+`) is the
  **correct, reconvergent** circuit: end-to-end **7.24 s** on the **2.13 B-triple** graph, of which the
  **naive-order OBDD compile is 3.87 s**. Reconvergent paths are exactly the high-fan-in case where a
  fixed variable order is expensive — so an **order-robust d-DNNF compiler (G6 / d4) is genuinely needed
  here, not optional**. (The pre-fix number hid this: the buggy merged circuit compiled in ~1 ms because
  it was under-counting the provenance.)
- **The baseline column is "construct only."** NPCS/SPARQLprov reach the provenance and stop; the
  compile+WMC columns above are the PQE they do not perform. ProvSQL (E7/G2a) does compute probabilities,
  but on a *modified* PostgreSQL; ours is a stock SPARQL engine + a client compiler.

## Caveats

- Construct times are on the shared box; treat as order-of-magnitude (E10 note). Construct grew vs the
  pre-fix table because the fix adds `urn:circuit:binding` recovery triples (more CONSTRUCT output).
- **Compile is our naive-order OBDD.** Low-treewidth shapes (star, TPC-H tree join) compile in ms; the
  **reconvergent path does not** (3.87 s) — this is the case G6's order-robust d-DNNF targets. (G6 also
  found d4-v1's *weighted count* unreliable on these large path CNFs; the trusted WMC is OBDD, validated
  == PWE. So paths currently pay the OBDD compile; a working order-robust WMC is the open optimization.)
- Single run per query here (latency shape); **G4** adds 5-run median±sd on these same numbers.
