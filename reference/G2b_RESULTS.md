# G2b — NPCS (per-answer strings) vs ours (shared circuit): construction head-to-head

Runs the **actual rewriters** (not E2's cost model), same bound query + same GraphDB WatDiv (32.7 M
reified) + same protocol. `NpcsRewriter` (`App Standard query`) emits a `GROUP_CONCAT` SELECT that
materializes each answer's provenance as a **string**; ours emits the CONSTRUCT that builds the
**shared circuit**. We POST each, timing eval and measuring output size. `g2b_npcs_vs_ours.csv`.

| query | answers | NPCS eval_ms | NPCS bytes | ours eval_ms | circuit (gates+edges) | **size win** |
|---|--:|--:|--:|--:|--:|--:|
| S-star (bound)     |      2 |     7 |     2 587 |    11 |    272 | 9.5× |
| P2-path (bound)    |     13 |    40 |     1 726 |     8 |     65 | 26.6× |
| P2-unbound (all)   | 149 998 | 9 302 | **19 935 124** | 16 652 | 749 990 | **26.6×** |

## Findings

- **Compactness (validates E2 on the real system).** Our shared circuit is **10–27× smaller** than
  NPCS's per-answer strings — measured by running NPCS's *actual* rewrite, not a model. On P2-unbound
  the baseline emits **≈ 20 MB** of provenance strings for the same query our circuit encodes in ~750 k
  gate+edge triples. The ratio grows with #answers (more derivations ⇒ more repeated substrings in the
  flat strings; the circuit shares them once).
- **Construction time — honest.** NPCS is *faster to construct* at scale (P2-unbound: 9.3 s vs our
  16.7 s, ≈1.8×): its `GROUP_CONCAT` string concatenation is cheaper than our per-gate **SHA256
  content-addressing**. That addressing overhead (E3's `c_overhead`) is exactly what *buys* a compact,
  content-addressed, **WMC-able** circuit — where NPCS's output is bulky and not directly compilable.
- **The decisive difference is what happens next.** NPCS stops at the string — **no probability**. Our
  circuit goes on to PQE: compile+WMC is near-free on the shared circuit (G3: 149 ms for all 14 908
  TPC-H Q3 answers). So the head-to-head is *faster-but-bulkier provenance with no PQE* (NPCS) vs
  *compact circuit + exact PQE* (ours) — and a per-answer PQE completion of NPCS's strings pays
  Θ(N·S) (E11).

## Caveats

- Shared machine; NPCS's `GROUP_CONCAT` can hit engine string-length limits at very high answer counts
  (here 19.9 MB completed on GraphDB).
- The ideal G2b graph is **WDBench's own curated Wikidata** (matches NPCS exactly); its download was
  blocked to automation, so this runs the head-to-head on **WatDiv at the same 32.7 M scale** the
  baselines use. Reification is Standard (3× blow-up; SPARQL-star would shrink both sides — G7).
