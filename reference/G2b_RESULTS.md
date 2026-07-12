# G2b — NPCS (per-answer how-provenance) vs ours (shared circuit): construction, THREE separate metrics

Runs the **actual `NpcsRewriter`** (`App Standard query`) vs our CONSTRUCT plan, same bound queries +
same GraphDB WatDiv (32.7 M reified), post-`1e67021`. `g2b_npcs_vs_ours.py` → `g2b_npcs_vs_ours.csv`.

> **Metric hygiene (R8.2).** An earlier version divided *NPCS bytes ÷ our gate-count* and reported it as
> "10–27× smaller" — that is dimensionless nonsense. Sizes are reported as **three separate comparisons**,
> never mixed: **structural** (elements vs elements), **serialized** (bytes vs bytes), **compiled** (nodes
> vs nodes). Two of them show **ours is larger** on these queries — stated honestly.

| query | answers | **structural** NPCS-occ / ours-g+e | **serialized** NPCS-B / ours-B | construct NPCS / ours |
|---|--:|--:|--:|--:|
| S-star (bound)     |      2 | 162 / 272 = **0.6×** | 2 730 / 36 488 = **0.07×** | 4 ms / 10 ms |
| P2-path (bound)    |     13 | 26 / 65 = **0.4×** | 1 836 / 22 770 = **0.08×** | 6 ms / 6 ms |
| P2-unbound (all)   | 149 998 | 299 996 / 749 990 = **0.4×** | 20.8 MB / 262.8 MB = **0.08×** | 3.2 s / 28.9 s |

(structural = NPCS flat token-occurrences ÷ our shared gates+edges — corrected `T_string` = *actual*
per-product tokens, `406ddbe`; serialized = raw UTF-8 string bytes ÷ N-Triples bytes; compiled = G6/E4,
not duplicated. ratio < 1 ⇒ **ours larger**.)

## Findings (honest)

- **On these selective / low-sharing queries, our shared circuit is NOT smaller — it is larger** on both
  axes: **~1.7–2.5× more structural elements** (share 0.4–0.6×) and **~12× more serialized bytes** (0.08×;
  SHA-256 content-addressed IRIs, ~180 B/triple). NPCS's flat per-answer token list is compact here because these
  queries have **little cross-answer sharing** to amortize the DAG's answer/product-gate + IRI overhead.
- **The compactness claim is *structural* and materializes with RECONVERGENCE — not on these queries.**
  E2's up-to-201× compactness is on **recursive / reconvergent** workloads where NPCS's flat strings
  duplicate shared sub-derivations across every answer and blow up super-linearly, while the shared DAG
  stores each once (same E11 boundary as the compile-win and the byte-win in G8). The bound P2/S-star
  queries here sit on the *low-sharing* side, so they show the honest opposite. Do not cite G2b as a
  size win.
- **Construct: NPCS is faster at scale** (P2-unbound 3.3 s vs our 28.9 s). Our plan pays per-gate
  **SHA-256 content-addressing** (E3's `c_overhead`) — the cost that *buys* a compact, content-addressed,
  **WMC-able** circuit that dedups identically across engines (E10).
- **The decisive difference is not size or construct speed — it is PQE.** NPCS emits per-answer strings
  and **stops (no probability)**. We go on to compile + WMC (CANONICAL_TIMINGS: 184 ms for all 14 908
  TPC-H Q3 answers). A per-answer completion of NPCS's strings pays Θ(N·S) (E11). That, plus property
  paths / full-SPARQL and cross-engine byte-identity, is the contribution — not a construction-size win.

## Caveats

- Reification is Standard (3× blow-up; SPARQL-star halves it structurally — G7). WDBench's own curated
  Wikidata is the ideal G2b substrate (download-blocked to automation); this runs at the same 32.7 M
  WatDiv scale the baselines use. Compiled-size comparison is in G6/E4 (tiny d-DNNFs), not repeated here.
