# R8.3 — ProvSQL vs ours on a RECONVERGENT query + SF 0.1 end-to-end

The review noted G2a/E7's Q3 has a single 3-token product per answer → probability is trivially
`0.5³ = 0.125`; it validates *execution compatibility* but not *shared-circuit WMC*. R8.3 adds a query
with **multiple derivations per answer that share a base token** (reconvergent lineage) and completes the
**SF 0.1 end-to-end** on our side. `r8_3_reconvergent.py` / `tpch/skeletons/Qrecon.rq` → `r8_3_reconvergent.csv`.

## The query (reconvergent)

```sparql
SELECT ?cust WHERE { ?cust <c_mktsegment> "BUILDING" . ?order <o_custkey> ?cust }
```
Projecting to `?cust`, a building customer with **K orders** has provenance `⊕ₖ (cust ⊗ orderₖ)` — the
**cust token is shared** across all K product terms. Correct `P = P(cust ∧ (⋁ orders)) = 0.5·(1−0.5ᴷ)`,
which **varies with K in [0.375, 0.5]** (not a constant). A naive per-answer product-sum
`Σₖ P(cust)·P(orderₖ) = 0.25·K` **double-counts** the shared `cust` and exceeds 1 for K ≥ 4.

## Results (current HEAD, 5-run: 1 warm-up + 5 timed; `90c3c3c` timer boundaries)

| scale | answers | ours total (construct/compile/WMC) | ProvSQL total | p range | ours valid | naive-would-be > 1 |
|---|--:|--:|--:|--:|:--:|--:|
| SF 0.01 |  247 | **443 ms** [435–457] (259 / 182 / 2) | 733 ms [730–772] | [0.375, 0.500] | 247/247 ✓ | 243/247 |
| SF 0.1  | 2086 | **12.9 s** [12.8–13.2] (2327 / **10600** / 22) | **6.7 s** [6.5–6.8] | [0.375, 0.500] | 2086/2086 ✓ | 2058/2086 |

## Findings

- **Shared-circuit WMC on reconvergent lineage — parity now DEFINITIVELY established.** The answer's
  provenance is a *sum of products sharing a base token* (not Q3's single product). This run's keyed check
  (`r8_3_reconvergent.py`, by `c_custkey`) shows ours **== ProvSQL** `probability_evaluate(provenance())`
  per customer (`max_abs_error = 0.0`, `keys_match`, identical answer sets) **and** ours **== the closed
  form** `0.5·(1−0.5ᴷ)` with K from an independent count (`cf_maxerr = 0.0`). Both at SF 0.01 and SF 0.1.
  So ours computes exactly the right reconvergent probabilities, verified two independent ways.
- **A naive per-answer product-sum is provably wrong here.** `0.25·K` exceeds 1 for **243/247** (SF 0.01)
  and **2058/2086** (SF 0.1) answers — impossible probabilities. Both the shared circuit (⊗ with a shared
  `cust` leaf feeding a ⊕ of orders) and ProvSQL's semiring avoid this; a per-derivation baseline that
  multiplied-then-summed would not. This is *why* the shared circuit / real WMC is needed.
- **SF 0.1 end-to-end is complete on our side (R8.3) — full 5-run pipeline.** 2086 answers, ours
  **12.9 s** vs ProvSQL **6.7 s**. **Latency is scale-dependent:** ours is faster at SF 0.01 (443 ms vs
  733 ms) but **ProvSQL is faster at SF 0.1** — under the `90c3c3c` boundaries our total now folds in the
  pure-Python **variable ordering**, which dominates the larger reconvergent circuit (SF 0.1 compile
  **10.6 s** of the 12.9 s; WMC itself is 22 ms). That ordering is a removable implementation cost (native
  compiler / linear heuristic), not the method. The robust, framing-level claim is the **same exact
  probabilities on a stock, unforked engine** (parity verified above); latency ordering is query- and
  scale-dependent and secondary.

## Caveats / notes

- Q3's **SF 0.1** "ours" cell stays **construction-only** (its 125 154-answer circuit is the pure-Python
  compile bottleneck; native/d4 compile is the follow-up). R8.3's SF 0.1 end-to-end is delivered via this
  *reconvergent* query, whose smaller circuit compiles — and is the more informative SF 0.1 datapoint
  anyway (it tests shared-circuit WMC, which Q3 does not).
- ProvSQL times are warm; `probability(provenance())` under `GROUP BY` (its semiring ⊕-aggregates the
  group). Absolute times order-of-magnitude (shared box, see CANONICAL_TIMINGS env).
