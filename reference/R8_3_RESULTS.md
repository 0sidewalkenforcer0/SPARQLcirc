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

## Results (post-`1e67021` jar; ProvSQL warm)

| scale | answers | ours total (construct/compile/WMC) | ProvSQL total | p range | ours valid | naive-would-be > 1 |
|---|--:|--:|--:|--:|:--:|--:|
| SF 0.01 |  247 | **322 ms** (238 / 81 / 2) | 770 ms | [0.375, 0.500] | 247/247 ✓ | 243/247 |
| SF 0.1  | 2086 | **2.76 s** (1998 / 740 / 22) | 6.45 s | [0.375, 0.500] | 2086/2086 ✓ | 2058/2086 |

## Findings

- **Shared-circuit WMC validated on genuinely reconvergent lineage.** Ours and ProvSQL return the **same
  answer set and the same probabilities** (247 / 2086 answers, p ∈ [0.375, 0.5] tracking K) — now on a
  query where the answer's provenance is a *sum of products sharing a base token*, not Q3's single
  product. This is the case that actually exercises correct handling of shared lineage.
- **A naive per-answer product-sum is provably wrong here.** `0.25·K` exceeds 1 for **243/247** (SF 0.01)
  and **2058/2086** (SF 0.1) answers — impossible probabilities. Both the shared circuit (⊗ with a shared
  `cust` leaf feeding a ⊕ of orders) and ProvSQL's semiring avoid this; a per-derivation baseline that
  multiplied-then-summed would not. This is *why* the shared circuit / real WMC is needed.
- **SF 0.1 end-to-end is complete on our side (R8.3).** The reconvergent circuit is small (2086 answers)
  so our pure-Python compile handles it: full pipeline **2.76 s** (vs ProvSQL 6.45 s). Here **ours is
  faster** — the mirror of Q3 (tree join, 14 908 answers, ProvSQL faster): ProvSQL pays to evaluate the
  ⊕-of-products lineage, we pay construct + a cheap compile. Latency ordering is query-dependent; the
  invariant is *same probabilities, on a stock engine* (G2a framing).

## Caveats / notes

- Q3's **SF 0.1** "ours" cell stays **construction-only** (its 125 154-answer circuit is the pure-Python
  compile bottleneck; native/d4 compile is the follow-up). R8.3's SF 0.1 end-to-end is delivered via this
  *reconvergent* query, whose smaller circuit compiles — and is the more informative SF 0.1 datapoint
  anyway (it tests shared-circuit WMC, which Q3 does not).
- ProvSQL times are warm; `probability(provenance())` under `GROUP BY` (its semiring ⊕-aggregates the
  group). Absolute times order-of-magnitude (shared box, see CANONICAL_TIMINGS env).
