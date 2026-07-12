# G5 — real SPARQLprov artifact (status: needs author's artifact pointer)

**Goal (ROUND 6/7 should-have):** run SPARQLprov's *released rewriter* locally to back the `T_string`
cost model with the real system, not just our NPCS reimplementation.

## Status: partially covered by G2b; SPARQLprov-specific leg deferred

- **The NPCS side is already done with the REAL tool.** [`g2b_npcs_vs_ours.py`](g2b_npcs_vs_ours.py)
  runs the **actual `NpcsRewriter`** (`java -jar … Standard path`) — not E2's cost model — on the same
  GraphDB WatDiv, emitting the real `GROUP_CONCAT` per-answer provenance strings and measuring their
  bytes (P2-unbound: **19.9 MB** of strings). NPCS and SPARQLprov are the **same how-provenance /
  no-PQE class** (BASELINE_COVERAGE.md), so G2b already backs `T_string` with a real system of that class.
- **The SPARQLprov-specific rewriter was not run** this session. A GitHub search surfaced several
  candidate repos (`rhasan/sparql-provenance`, `Conal-Tuohy/PROV-RIF-SPARQL`, `PR0CK0/ProvTracer`, …)
  but **none could be confidently identified as the exact SPARQLprov baseline** from the paper. Cloning
  and building an *unverified* research artifact risks producing numbers attributed to "SPARQLprov" that
  are actually a different system — worse than no number.

## What's needed to finish G5

1. The author's pointer to the **exact SPARQLprov artifact** (repo / Zenodo DOI / release) — the paper
   cites a specific one.
2. Then: build locally, run its rewriter on the shared TPC-H / WatDiv skeletons, and drop the measured
   `T_string` (+ its MINUS handling, which E11 Result 3 notes is *unguarded* → the wrong-MINUS finding)
   next to G2b's NPCS column.

Until then G2b (real NpcsRewriter) is the honest real-system datapoint for the how-provenance baselines;
this file records the SPARQLprov-specific leg as an author-gated follow-up rather than guessing an artifact.
