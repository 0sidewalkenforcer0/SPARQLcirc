# G3 — end-to-end PQE latency (construct → compile → WMC)

NPCS/SPARQLprov produce how-provenance and **stop** — they compute no probability. SPARQLcirc runs the
full probabilistic-query-evaluation pipeline. E3 timed *construction* and E4/E11 timed *compile+WMC*
apart; **G3 joins them into one end-to-end wall-clock** (all answers), on the loaded GraphDB repos
(WatDiv / TPC-H / Wikidata, the last a **property path** now that G1 makes paths scale).
`g3_pqe_latency.py` → `g3_pqe.csv`. Per-token p = 0.5; compile = the **shared** ROBDD (compile once,
E11), not per-answer.

| query | dataset | answers | construct | compile (shared) | WMC | **total PQE** |
|---|---|--:|--:|--:|--:|--:|
| watdiv-Sstar       | WatDiv 32.7 M reified        |     2 |   13 ms |   2 ms |  0 ms | **15 ms** |
| tpch-Q3 (naryrel)  | TPC-H 1.26 M                 | 14908 | 1500 ms | 149 ms | 35 ms | **1.68 s** |
| wikidata-WDpath    | Wikidata 2.13 B (`P279+`, G1)|    16 | 2136 ms |   1 ms |  0 ms | **2.14 s** |

All probabilities valid (WMC ∈ [0,1]); exactness is E1/E11 (WMC == PWE).

## Findings

- **PQE is construct-dominated.** The engine building the shared circuit is the cost; the **compile +
  WMC — the stage the baselines lack — is near-free**: **149 ms to compile+WMC all 14 908 TPC-H Q3
  answers** through the shared circuit. A per-answer completion (what a baseline would need to turn its
  how-provenance into a probability) pays Θ(N·S) — orders of magnitude more (E11 Result 2).
- **Full PQE on a recursive query at KG scale.** WD-path (`Q7397 wdt:P279+`) runs end-to-end in **2.14 s
  on the 2.13 B-triple** Wikidata graph — construct 2.1 s (the G1 reachable-subgraph BFS + iterative
  rounds), compile+WMC ~1 ms. Property paths are the fragment NPCS/SPARQLprov cannot express at all.
- **The baseline column is "construct only."** NPCS/SPARQLprov reach the provenance and stop; the
  compile+WMC columns above are the PQE they do not perform. ProvSQL (E7) does compute probabilities,
  but on a *modified* PostgreSQL; ours is a stock SPARQL engine + a client compiler.

## Caveats

- Construct times are on the shared box; treat as order-of-magnitude (per the E10 note). The compile
  is our naive-order OBDD — d4/d-DNNF (G6) is order-robust and would be used for high-treewidth cases
  (WatDiv/TPC-H/these paths are low-tw, so the OBDD compile is already ms).
- Single run per query here (latency shape, not a benchmarked mean); G4 adds repeats + variance.

> **Engine fix 1e67021 (mid-session):** the term-type-aware gate-identity fix adds `urn:circuit:binding`
> metadata triples (raising raw circuit/triple counts) and un-merges property-path reach-states the old key
> wrongly collapsed. The **correctness spine re-verified on the rebuilt jar** — answer counts unchanged
> (S-star 2, Q3 14908, WD-path 16), OBDD==PWE still holds, E10 byte-identity still 13/13 on 3 engines.
> **Absolute sizes/times in this file predate the fix** (esp. the property-path row) and should be
> regenerated in a clean pass; the conclusions are unaffected.
