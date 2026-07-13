# SPARQL_circ — experiment results, organized for the talk

**Thesis.** Rewrite a SPARQL query so an **unmodified** SPARQL 1.1 engine materializes one **shared,
content-addressed provenance circuit** (⊕/⊗/⊖ gates); the client compiles it and weighted-model-counts it
for **exact probabilistic query evaluation (PQE)**, including non-monotone OPTIONAL/MINUS and property
paths. The competitor that also does exact PQE (ProvSQL) needs a **forked PostgreSQL**; the how-provenance
systems (NPCS, SPARQLprov) emit per-answer strings and **stop before any probability**.

Four claims the evaluation defends:

| | Claim | Answered by |
|---|---|---|
| **A** | Unmodified-engine construction (stock SPARQL builds the circuit) | E3, E10, G3, G7, G10 |
| **B** | Compactness by sharing (shared circuit ≪ per-answer strings — *when there is sharing*) | E2, E11, G2b |
| **C** | Exact PQE incl. non-monotone | E1, E6, G6, E11, R8.3, property paths |
| **D** | Tractability tied to treewidth | **E4**, E5 |

---

## What question does each experiment answer?

| Exp | The question it answers | Headline result (current numbers) | Claim | Fig |
|---|---|---|:--:|:--:|
| **E1** correctness | Are the probabilities **exact**? | WMC == possible-world enumeration, exact for every operator; 171/171 reference checks | C | — |
| **E2** compactness | *How much* smaller is the shared circuit than per-answer strings, and **when**? | grows with depth: shallow ≈ 0.4–0.9× (≈ strings), **deep-12 = 201×** | B | 3 |
| **E3** construction scaling | Can an **unmodified** engine build it, at what overhead, does it scale? | build ≈ 1.6–6.8× plain-query; S-star 31 ms @10 M → 515 ms @100 M (near-linear) | A | — |
| **E4** compile vs treewidth | Is compile cost governed by **treewidth**, and does a real d-DNNF beat our OBDD? | bounded-tw: d-DNNF ≤ 5 270 nodes while OBDD hits 299 k then **times out (>300 s)**; growing-tw both wall, d-DNNF later & smaller; **d4 count == expected 34/34** | D | 1,2 |
| **E6** non-monotone | MINUS at scale — correct + feasible on a stock engine? | ⊖ built at 10 M/100 M; WMC == PWE (Δ ≤ 1.1e-16); baselines can't produce these probabilities | C | — |
| **E8** Wikidata 2.13 B | Full fragment on a **billion-triple real KG**? | 33/41 single queries build (8 too-large/OOM); circuits up to 772 k gates; `P279+`/`P131+` run | A,B,C | — |
| **E10** multi-engine | Is the circuit a property of the **rewrite**, not the engine? | **byte-identical** circuit on GraphDB / Fuseki / Oxigraph / QLever / MillenniumDB (Java, Rust, C++) | A | — |
| **E11** shared vs per-answer | Same answers as per-answer how-provenance, but **cheaper**? | identical probs (Δ = 0); shared Θ(N+S) vs per-answer Θ(N·S) → **~9× @ N=1000**, up to ~29× (layered-4×4) | B,C | 4 |
| **G2b** NPCS vs ours (honest) | Is our circuit smaller than NPCS strings? | on **selective** queries **no** — ours ~1.7× more elements, ~12× more bytes. Compactness is a *deep/reconvergent* property (E2/E11), not universal | B(–) | — |
| **G3** end-to-end latency | Where does the PQE time go? | S-star 12 ms · TPC-H Q3 **6.40 s** (construct 3.08 + compile 3.30 + **WMC 0.036**) · WD-path **2.14 s** (compile ~1 ms). **WMC is never the cost** | A | 6 |
| **G4 / G2a / R8.3** vs ProvSQL | vs the strongest baseline — same result, what latency? | **exact parity, max_abs_error = 0.0**; TPC-H Q3 ours faster on all 5 segments (3.5–6.4 s vs 5.0–7.6 s); reconvergent: ours faster @SF0.01, ProvSQL @SF0.1 | A,C | 5 |
| **G6** d4 on real circuits | Do **three independent** methods agree on real circuits? | OBDD == PWE == d4, **26/26** (incl. all 16 property paths) | C | 7 |
| **G7** reification | Does the reification scheme matter? | SPARQL-star = 1 triple/fact vs Standard 3× (1.9× fewer bytes); **circuit identical either way** | A | — |
| **G8** space/memory | Footprint at billion-triple scale? | WD-path over 2.13 B: peak RSS **166 MB**, 2.3 s | A | — |
| **G10** complex class | Does the WatDiv **Complex (C)** class build? | C1 (8-pattern) @10 M: 8 answers, 168 gates+edges, 4.5 s → full L/S/F/C taxonomy | A | — |
| **paths** (E-paths / ablation) | Property paths (baselines **cannot** do) — correct, polynomial, and why the design? | P +/*/alt/all on friendOf; polynomial circuit where naive walk is infinite; **3-variant ablation**: merged = *wrong* → shared = correct-but-slow → isolated (PathIsoSeq) = correct + fast | C | — |

---

## Figures (in `figures/`)

1. **`fig1_E4_bounded_treewidth`** — the flagship. At fixed tw=2, the fixed-order OBDD blows up and **times
   out (>300 s)** past ~126 tokens, while the d-DNNF stays polynomial. *This is why knowledge compilation.*
2. **`fig2_E4_growing_treewidth`** — as treewidth grows, **both** compilers hit the #P wall (2^Θ(tw)) — the
   honest limit — but d-DNNF walls later and smaller. Tractability is governed by tw, exactly as predicted.
3. **`fig3_E2_compactness`** — the shared circuit ≈ per-answer strings on shallow queries and reaches **201×**
   on deep ones. The compactness claim is *conditional on sharing* — stated honestly.
4. **`fig4_E11_shared_vs_peranswer`** — one shared compile (Θ(N+S)) vs per-answer (Θ(N·S)): **same
   probabilities**, growing time win (~9× at N=1000). This is the "we do PQE, they can't / would pay N×" point.
5. **`fig5_provsql_headtohead`** — vs ProvSQL (modified PostgreSQL) on TPC-H Q3: **same exact probabilities,
   comparable/slightly-faster latency, no engine fork.**
6. **`fig6_G3_pqe_breakdown`** — end-to-end latency: **the weighted count is never the cost (≤ 36 ms)**; Q3's
   compile is a *pure-Python variable-ordering* artifact (removable with a native compiler).
7. **`fig7_G6_correctness`** — OBDD = PWE = d4 on 26/26 real circuits (incl. every property-path answer):
   correctness on the actual workloads, three independent ways.

---

## Suggested 6-figure narrative for the talk

1. **Setup / correctness** — Fig 7: exact on real circuits, three independent methods agree.
2. **Why a circuit (compactness)** — Fig 3: shared circuit vs strings, up to 201× on deep queries.
3. **Why PQE is feasible (shared compile)** — Fig 4: one compile for all answers, ~9× vs per-answer.
4. **The theory (treewidth governs cost)** — Fig 1 + Fig 2: d-DNNF beats OBDD at bounded tw; both wall at growing tw.
5. **Vs the strongest baseline** — Fig 5: same exact PQE as ProvSQL, no engine fork.
6. **End-to-end reality** — Fig 6: WMC is trivial; the residual cost is a removable Python ordering step.

---

## Honest caveats (say these before a reviewer does)

- **Compactness (B) is conditional.** On selective/low-sharing queries the RDF circuit is *larger* than NPCS
  strings (G2b) — the win is deep/reconvergent queries (E2, E11). Do not claim a universal size win.
- **TPC-H Q3's 3.3 s "compile" is a pure-Python variable-ordering cost**, not the ROBDD build and not WMC
  (36 ms). A native compiler / linear ordering heuristic removes it. The *weighted count is never the cost.*
- **Some Wikidata queries are too-large/OOM** (8 of 41 singles) — the KG-scale story is selective queries +
  a small reachable subgraph, not arbitrary dense queries.
- **Property-path frontier is IRI-only** (blank-node/literal path nodes not yet supported).
- **The "same-binary d4v2" ProvSQL head-to-head is author-gated** (d4v2 won't build: proprietary PaToH +
  KaHyPar). It is *not* a correctness blocker — exact parity with ProvSQL is already established
  (`max_abs_error = 0.0`, R8.3); the pending item is only pinning the identical compiler on both sides.

---

## Provenance / reproduce

- Numbers are the committed results in `reference/*.csv`, `reference/watdiv/*.csv`, and the per-experiment
  `reference/*_RESULTS.md`. Timing is the **canonical 5-run table** (`reference/CANONICAL_TIMINGS.md`,
  current HEAD, post-`PathIsoSeq`); older timing tables are superseded (`HISTORICAL_TIMINGS.md`).
- Figures regenerate from those CSVs: `cd presentation && python3 make_figures.py`.
