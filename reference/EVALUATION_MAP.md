# SPARQLcirc — evaluation map (VLDB)

Single-page cross-reference: **research question ↔ experiment ↔ artifact ↔ takeaway**. Doubles as the
reproduction index. Every scale number is trusted only after the correctness gate: `circuit WMC ==
possible-world enumeration` on the small checks, and `d4 / ProvSQL / CUDD / oracle WMC == our OBDD` per
instance. Environment: GraphDB 10.7.6 (WatDiv 10M = 32,749,371 reified triples; 100M = 326,993,142) · d4 v1
(Linux/x86) · PostgreSQL 18 + ProvSQL 1.11 · zero-dependency ROBDD + WMC.

## Pre-registered claims (from `watdiv/EXPERIMENTS.md` / `docs/EVALUATION.md`)
| | Claim | Backed by |
|---|---|---|
| **A** | An **unmodified** SPARQL engine builds the shared circuit | E3, E7, E10, G2a, reification×4, persist-portability |
| **B** | **Compactness by sharing** — shared circuit ≪ per-answer strings, and that is *why* PQE is feasible | E2, E11, G2b, G5, G8 |
| **C** | **Exact PQE**, including non-monotone OPTIONAL/MINUS (⊖) and property paths | E1, E6, E7, G6, verify_differential, path suite |
| **D** | **Tractability tied to treewidth**; factored construction keeps the circuit polynomial | E4, E5, FACTORED_REGIMES, unbound-factored |

---

## RQ1 — Exactness / correctness  *(claim C)*
> Does the circuit compute exact probabilities, over the full non-monotone + path fragment?

| Exp | Takeaway (headline) | Artifact | Status |
|---|---|---|---|
| **E1** | `circuit WMC == PWE` for every gallery operator; `tests.py` 171/171; error 0 (float ε) | `verify_gallery.py`, `verify_engine_native.py`, `tests.py` | ✅ |
| **E6** | MINUS/OPTIONAL (incl. composite operands via `normalize()`) exact — the "we can, they can't" result | `e6_minus.py`, `verify_nonmono.py` → `watdiv/e6_minus*.csv` | ✅ |
| **verify_differential** | 24 random DAGs × 5 independent engines (enum / oracle-ROBDD / CUDD shared+per-root / d4) agree to 1e-16; 5 engine cases incl. ground-constant/disconnected/self-join/project-away | `verify_differential.py` | ✅ |
| **G6** | real WatDiv circuit WMC == ground truth; d4 d-DNNF == our OBDD | `g6_d4_real.py` → `g6_d4.csv`, `RESULTS.md` | ✅ |
| **E7** | exact parity vs ProvSQL, 3/3, Δ≈2e-16 | `watdiv/e7_results.csv`, `watdiv/EXPERIMENTS.md` §E7 | ✅ |
| **paths** | p+/p*/p?/^/\| iterative fixpoint correct; per-path fingerprint isolates concurrent path queries | `verify_engine_paths.py`, `verify_path_isolation.py`, `PATH_VARIANTS.md`, `watdiv/e_paths.csv` | ✅ |

## RQ2 — Unmodified-engine construction, portability, reification-independence  *(claim A)*
> Can a stock engine build it? Is the circuit invariant to engine and to reification scheme?

| Exp | Takeaway | Artifact | Status |
|---|---|---|---|
| **E3** | on stock GraphDB, build ∝ #derivations across **5 orders of magnitude** (log-log slope ≈ 1); 100M selective built by an unmodified store | `e3_run.py` → `watdiv/e3_{10M,100M}{,_unbound}.csv` | ✅ |
| **E10** | 4 architecturally-different engines (GraphDB/Oxigraph/QLever/MillenniumDB) emit the **byte-identical** content-addressed circuit | `engines/run_engine.py` → `engines/e10_byte_identity.csv`, `engines/RESULTS.md` | ✅ |
| **reification ×4** | Standard ≡ RDF-star ≡ NamedGraph **byte-identical** across AND/UNION/OPTIONAL/MINUS; Wikidata structural + WMC equivalent | `verify_g7_circuit_equiv.py` → `g7_reification.csv`; `watdiv/RDFSTAR_RESULTS.md`, `watdiv/NAMEDGRAPH_REIF.md`, `wikidata/WIKIDATA_REIF_EQUIV.md` | ✅ |
| **G7** | RDF-star shrinks Standard's 3× storage blow-up; circuit unchanged | `RESULTS.md`, `watdiv/rdfstar_{10m,100m}.csv` | ✅ |
| **persist portability** | `CIRCUIT_PERSIST` named-graph round-trip (persist→read→CLEAR→no leak) PASS on GraphDB **and** Oxigraph | `verify_persist_portability.py` | ✅ |
| **E7 / G2a** | baseline ProvSQL needs a **modified** PostgreSQL; we run stock — the A contrast | `watdiv/e7_results.csv`, `g2a_provsql_vs_ours.csv`, `RESULTS.md` | ✅ |

## RQ3 — Compactness by sharing  *(claim B, the core selling point)*
> How much smaller is the shared circuit than per-answer provenance strings, and when?

| Exp | Takeaway | Artifact | Status |
|---|---|---|---|
| **E2** | `T_string` / `T_circuit`: 0.4–0.9× shallow → **201× at depth-12**; circuit stays ~flat | `bench.py` → `bench.csv`, `watdiv/EXPERIMENTS.md` §E2 | ✅ |
| **E11** | representation win is **order-independent** (same compiler both sides → "win is the circuit, not the compiler"); quantifies the **reconvergence boundary**; real WatDiv/TPC-H | `e11_per_answer_vs_shared.py`, `e11_real.py` → `e11_results.csv`, `e11_scale.csv`, `e11_real.csv`, `RESULTS.md` | ✅ |
| **G2b** | NPCS per-answer how-provenance vs shared, **3 independent metrics** (structural / serialized / compiled) | `g2b_npcs_vs_ours.py` → `g2b_npcs_vs_ours.csv`, `RESULTS.md` | ✅ |
| **G5** | real SPARQLprov rewriter built + run locally for the comparison | `g5_sparqlprov_rewrite.csv`, `RESULTS.md` | ✅ |
| **G8** | space & memory at scale; the win materializes with **reconvergence** | `g8_space_memory.py` → `g8_space_memory.csv`, `RESULTS.md` | ✅ |

## RQ4 — Tractability, knowledge compilation, factored construction  *(claim D)*
> Does compilation stay tractable with treewidth? Does factored keep the circuit polynomial, and when does it win?

| Exp | Takeaway | Artifact | Status |
|---|---|---|---|
| **E4** | bounded-tw: OBDD explodes to 299k and stops compiling by n≥126, while **d-DNNF stays ≤5,270**; d4 WMC == OBDD per instance | `e4_sweep.py`, `gen_families.py` → `watdiv/e4_results.csv`, `watdiv/EXPERIMENTS.md` §E4 | ✅ |
| **E5** | factored/flat ratio ≈ `W^{k−2}` (unbounded; W=14,k=4 → 65×); WMC provably unchanged | `factor.py`, `compile_compare.py`, `watdiv/EXPERIMENTS.md` §E5 | ✅ |
| **FACTORED_REGIMES (unbound)** | **factored's design win**: unbound reconvergent k-hop — flat exponential vs factored polynomial, **26.4× by k=7** (65,552 vs 2,480 gates) | `unbound_factored_vs_flat.py` → `watdiv/unbound_factored_vs_flat.csv`, `FACTORED_REGIMES.md` | ✅ |
| **flat-vs-factored (bound)** | with **source-restriction pushdown**, bound selective chains tie flat instead of blowing up (L-path 299,762→143 gates; WMC parity); stars still win 9.5× | `rdfstar_factored.py`, `wikidata_factored.py` → `watdiv/rdfstar_factored_vs_flat.csv`, `wikidata/wikidata_factored_vs_flat.csv` | ✅ |

## RQ5 — End-to-end performance & scale  *(cross-cutting, practicality)*
| Exp | Takeaway | Artifact | Status |
|---|---|---|---|
| **G3** | end-to-end PQE latency, broken into construct → compile → WMC | `g3_pqe_latency.py` → `g3_pqe.csv`, `RESULTS.md` | ✅ |
| **G4** | statistical rigor on headline timings (warmup + ≥5 runs, dispersion) — answers the single-run review flag | `g4_rigor.py`, `g4_instances.py` → `g4_rigor.csv`, `g4_instances.csv`, `RESULTS.md` | ✅ |
| **G8** | memory footprint at scale | `g8_space_memory.py` → `g8_space_memory.csv` | ✅ |
| **G2a** | end-to-end latency **comparable** to ProvSQL (positioned as a peer, not a speed claim) | `g2a_provsql_vs_ours.csv`, `RESULTS.md` | ✅ |

## RQ6 — Baselines & coverage completeness  *(A + B + C together)*
| Baseline / axis | Takeaway | Artifact | Status |
|---|---|---|---|
| **ProvSQL** (modified PG) | exact parity, comparable latency, no engine fork on our side | E7, G2a | ✅ |
| **NPCS** (per-answer how-provenance) | shared ≪ per-answer | G2b, E11 | ✅ |
| **SPARQLprov** | real rewriter comparison | G5 | ✅ |
| **WatDiv Complex (C)** | comparability completeness — baselines compared across all query categories, not cherry-picked | `watdiv/g10_complex.csv`, `RESULTS.md` | ✅ |

---

## Reproduction quick-start
```
cd reference
python3 tests.py                       # RQ1  171/171
python3 verify_differential.py         # RQ1  5-backend differential
python3 verify_g7_circuit_equiv.py     # RQ2  reification-independence (4 schemes)
python3 verify_persist_portability.py GraphDB <query_ep> <update_ep>   # RQ2 persist
python3 bench.py                        # RQ3  compactness (E2)
python3 unbound_factored_vs_flat.py    # RQ4  factored design win
WATDIV_REPO=watdiv python3 e3_run.py   # RQ2/5 deployed construction (needs GraphDB)
```

---

## Claim strength and framing (for writing the evaluation section)

What each question lets us assert, how strong the assertion is, and where the data lives. Numbers below
are from the paper-campaign runs (`reference/paper/`), one measured run per cell unless noted.

**Positioning.** Exact PQE for a **wider SPARQL fragment than any prior exact method** — non-monotone
OPTIONAL/MINUS and recursive property paths — via a shared, content-addressed provenance circuit that
**any off-the-shelf SPARQL 1.1 engine builds byte-for-byte identically**, then compiled to d-DNNF for
exact WMC.

| # | Question | Strength | Evidence and phrasing |
|---|---|---|---|
| **Expressiveness** | exact probabilities for queries prior exact methods cannot express | **strong, universal** | ⊖ gate + WMC over the compiled d-DNNF, `verify_all` green. The differentiator is not "faster" but "prior art cannot do it at all". |
| **Exact-PQE performance + sharing** | how fast is exact PQE once compiled | **strong** | `pqe_stages_{flat,factored}_{10m,100m}.csv`: WMC **sub-millisecond median** (0.6 ms @10M, 1.3 ms @100M), ≤ ~79 ms at 100M with 3213 answers; the **shared** circuit beats per-answer by ~1.3× median, up to ~2× on large answer sets (L2@100M, 961 answers: 11.3 ms vs 23.5 ms). "Interactive exact PQE, scaling to 100M; sharing amortises across answers." |
| **Engine independence** | is the artifact tied to a custom engine | **strong, universal** | `circuit_sha256` byte-identical across **4 engines at 10M** and at **100M** (`paper/rq3/BYTEID_100M_SUMMARY.md`). A certified SPARQL-1.1-only deterministic artifact, no engine surgery. |
| **External baseline** | vs ProvSQL, the SOTA exact-PQE system | **strong** | `g2a_provsql_vs_ours.csv`, fair uncontended 3-run medians at SF 0.01/0.1/0.3: same exact probabilities, ours ~2.7× faster end to end (SF 0.3: 70.1 s vs 185.0 s). A compiler-controlled per-answer run (`level1_d4_headtohead.py`, pinned d4v2) isolates the win to the representation; it needs a TPC-H reload into ProvSQL first. |
| **Compactness** | more compact than the SOTA rewriting baseline | **conditional — frame carefully** | On monotone BGP the circuit is ≤ NPCS via sub-structure sharing; **on OPTIONAL it is far larger** (OO2 ~337k vs 476) because NPCS's compact string does not support exact non-monotone PQE. Phrase as "pays size to gain exact non-monotone evaluation NPCS cannot provide", never "always smaller". `paper/rq2/FINDINGS.md`. |
| **Construction scalability** | does construction scale | **conditional** | 100M is feasible; the cost is the **host engine's query evaluation**, not the method (same byte-identical circuit: LL3 19 min on Oxigraph vs 0.38 s on GraphDB, while LL2 is *faster* on Oxigraph — a planner pathology, not a constant overhead). Report per engine; do not average the pathological cells. `paper/rq3/CONSTRUCTION_COST_ENGINE.md`. |

**Lead with three:** expressiveness (exact PQE over non-monotone + recursive paths), interactive exact
PQE with measured sharing, and pure-SPARQL cross-engine byte-identity.

**Gaps that most affect accept/reject.** (1) Synthetic data — WatDiv plus synthetic probabilities; add at
least one real uncertain KG and justify the probability model. (2) The tractability boundary is
unstated — WMC is #P-hard and d-DNNF can blow up, so characterise when it is tractable and report the
honest failures (MINUS at 100M timing out on Oxigraph, the biggest OPTIONAL/complex cells capping) as
the boundary rather than omitting them. (3) The external head-to-head is done but would be stronger at
larger scale and over more query shapes.

## Threats to validity / open gaps
1. **Scale ceiling** — E3 goes to 100M; 200M deferred (generator needs Boost). State the ceiling explicitly.
2. **Elimination order** — E4/factored use a heuristic min-scope order; optimal treewidth order is NP-hard. Do not claim optimality.
3. **e8 (Wikidata NPCS queries)** — partial run superseded by G2b; cite G2b, not e8, for the compactness number.
4. **Deployed factored isolation** — factored writes a session workspace to the default graph; harnesses self-heal and assert byte-identical repo size, but a shared store still benefits from named-graph feedback (unfinished half of CIRCUIT_PERSIST).
