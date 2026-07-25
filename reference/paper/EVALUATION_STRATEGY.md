# Evaluation strategy — what the experiments let us claim (VLDB)

_Working note for writing the evaluation section. Maps each research question to the concrete
evidence we already have, the claim strength (universal/asserted vs conditional), where the data
lives, and how to phrase it. Numbers are from the current runs (single measured run per cell unless
noted); update as more land._

## Positioning (the 30-second pitch)
Exact probabilistic query evaluation (PQE) for a **wider SPARQL fragment than any prior exact method**
— including non-monotone OPTIONAL/MINUS and recursive property paths — via a **shared,
content-addressed provenance circuit** that **any off-the-shelf SPARQL 1.1 engine builds byte-for-byte
identically**, then compiles to d-DNNF for fast exact weighted model counting (WMC).

---

## The questions the evaluation answers (ranked by strength for VLDB)

### Q6 — External baseline: vs ProvSQL, the SOTA exact-PQE system (STRONG — I was wrong to list this as a gap)
*How does end-to-end exact PQE compare to the strongest external system (ProvSQL)?*
- Evidence (`reference/g2a_provsql_vs_ours.csv`): FAIR uncontended 3-run medians, WatDiv SF 0.01/0.1/0.3.
  **SPARQLcirc is ~2.7x faster than ProvSQL end-to-end with identical probabilities** (SF0.3: ours 70.1s
  vs ProvSQL 185.0s, ratio 2.64x; SF0.1 2.71x; SF0.01 2.88x; both p=0.125, ProvSQL default agrees).
- A more controlled per-answer head-to-head (`reference/level1_d4_headtohead.py`) forces BOTH systems
  through the SAME compiler (pinned d4v2) at per-answer granularity, isolating the compiler so the
  comparison is of the provenance representation, not the compiler. Runnable (postgres is up, ProvSQL at
  `tools/provsql`, d4v2 at `tools/d4v2`); needs the `provsqltest` DB reachable + D4/D4V2 env. Not yet
  re-run this campaign — a refinement, since g2a already gives the headline.
- Phrase: "against ProvSQL, the strongest exact-PQE baseline, SPARQLcirc computes the same exact
  probabilities ~2.7x faster end-to-end; a compiler-controlled per-answer comparison isolates the win to
  the shared content-addressed representation."

### Q1 — Expressiveness / correctness (STRONG; universal, assert)
*Can we compute **exact** probabilities for queries prior exact-PQE methods cannot — non-monotone
OPTIONAL/MINUS and recursive paths?*
- Evidence: reference implementation with `verify_all` green; circuit explicitly encodes the
  non-monotone difference gate (⊖); WMC over the compiled d-DNNF yields exact values.
- This is the differentiator: not "faster" but "prior art cannot do it at all."
- Artifacts: `reference/` reference implementation + verifiers; `reference/g3_pqe.csv`,
  `reference/g6_d4.csv` (d4 d-DNNF compile).

### Q5 — Exact-PQE performance + sharing (STRONG; the performance headline)
*Once compiled, how fast is exact PQE, and does the shared circuit help?*
- Evidence (`pqe_stages_{flat,factored}_{10m,100m}.csv`): exact WMC is **sub-millisecond median**
  (0.6 ms @10M, 1.3 ms @100M) and **≤ ~79 ms even at 100M with 3213 answers**. The **shared** circuit
  beats per-answer evaluation by a median ~1.3× and up to ~2× on large answer sets
  (e.g. L2@100M 961 answers: 11.3 ms shared vs 23.5 ms per-answer).
- Phrase as: "exact PQE at interactive latency, scaling to 100M; sharing amortises across answers."

### Q3 — Engine independence / reproducibility (STRONG; universal, assert)
*Is the provenance artifact tied to a custom engine?*
- Evidence: `circuit_sha256` (SHA-256 over canonical N-Triples) is **byte-identical across 4 engines
  at 10M** (GraphDB, Oxigraph, QLever, MillenniumDB) and **at 100M on a second independent engine**
  (Oxigraph/Rust vs GraphDB/Java: **21/21** on every comparable F/L/O/S cell).
- The circuit is a certified SPARQL-1.1-only, deterministic artifact — no engine surgery.
- Artifacts: `rq3/*-10m/`, `rq3/oxigraph-100m/BYTEID_100M.md`.

### Q2 — Compactness (CONDITIONAL; frame carefully)
*Is the shared circuit more compact than the SOTA rewriting baseline (NPCS)?*
- Evidence (`nodecount_flat_10m_100m.csv`): on monotone BGP the circuit is ≤ NPCS via sub-structure
  sharing (FF4 183 vs 748, LL3 108 vs 196; many equal).
- **Caveat to own, not hide:** on OPTIONAL the flat circuit is far larger than NPCS (OO2 ~337k vs 476)
  because NPCS's compact string does **not** support exact non-monotone PQE. Phrase as "the circuit
  pays size to gain exact non-monotone evaluation NPCS cannot provide," not "always smaller."
- The `factored` series is staged construction, not a compaction; its win is the reconvergence sweep
  (`reference/watdiv/unbound_factored_vs_flat.csv` + density sweep), not bound WatDiv. See
  `rq2/FINDINGS.md`.

### Q4 — Construction scalability (CONDITIONAL)
*Does construction scale?*
- Evidence: 100M construction is feasible; cost is the **host engine's query evaluation**, not the
  method. Same byte-identical circuit: LL3 = 19 min on Oxigraph vs 0.38 s on GraphDB (3059×), while
  LL2 is *faster* on Oxigraph — a planner pathology on reified joins, not a constant overhead.
- Phrase construction time conditionally, per engine; do **not** average Oxigraph's pathological
  cells into a headline number. See `rq3/CONSTRUCTION_COST_ENGINE.md`.

---

## Strongest three claims to lead with
1. **Expressiveness** (Q1): exact PQE over non-monotone + recursive paths — prior exact methods can't.
2. **Interactive exact PQE + measured sharing** (Q5): sub-ms to tens-of-ms, scales to 100M.
3. **Pure-SPARQL, cross-engine byte-identity** (Q3): 4 engines @10M, 2 @100M — deployable/reproducible.

## Gaps that most affect accept/reject (address before submission)
1. **External end-to-end PQE baseline — LARGELY DONE (correction).** `g2a_provsql_vs_ours.csv` already
   compares against ProvSQL (the SOTA exact-PQE system): ours ~2.7x faster, exact-agree, WatDiv SF
   0.01-0.3. To strengthen: extend to larger scale + more query shapes, and re-run the compiler-controlled
   `level1_d4_headtohead.py`. This is a refinement, no longer the top risk.
2. **Synthetic data only.** WatDiv + synthetic probabilities. Add ≥1 real uncertain KG and justify the
   probability model.
3. **Tractability boundary unstated.** WMC is #P-hard; d-DNNF can blow up. Characterise when it is
   tractable (vs answer count / structure) and report honest failures — e.g. MINUS at 100M on
   Oxigraph timed out; the biggest OPTIONAL/complex cells cap. Present these as the boundary, openly.

## Data / artifact index
| result | file |
|---|---|
| exact PQE stage timings | `pqe_stages_{flat,factored}_{10m,100m}.csv`, `reference/g3_pqe.csv`, `reference/g6_d4.csv` |
| compactness node counts | `nodecount_flat_10m_100m.csv`, `nodecount_factored_10m_100m.csv`, `rq2/FINDINGS.md` |
| reconvergence sweep (factored win) | `reference/watdiv/unbound_factored_vs_flat.csv` + density sweep |
| byte-identity 10M (4 engines) | `rq3/{graphdb,oxigraph,qlever,millenniumdb}-10m/` |
| byte-identity 100M (2 engines) | `rq3/graphdb-100m/`, `rq3/oxigraph-100m/BYTEID_100M.md` |
| construction cost analysis | `rq3/CONSTRUCTION_COST_ENGINE.md` |
