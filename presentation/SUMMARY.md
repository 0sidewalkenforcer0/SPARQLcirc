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
| **E2** compactness | *How much* smaller is the shared circuit than per-answer strings, and **when**? | grows with depth: shallow ≈ 0.4–0.9× (≈ strings), **deep-12 = 201×** | B | 2a |
| **E3** construction scaling | Can an **unmodified** engine build it, at what overhead, does it scale? | circuit CONSTRUCT ≈ 1.6–6.8× the clean-room NPCS-compatible SELECT (the legacy `plain_ms` name is misleading); S-star 31 ms @10 M → 515 ms @100 M | A | 3a–b |
| **E4** compile vs treewidth | Is compile cost governed by **treewidth**, and does a real d-DNNF beat our OBDD? | bounded-tw: d-DNNF ≤ 5 270 nodes while OBDD hits 299 k then **hits the 120 s timeout** (≥126 tokens); growing-tw both grow exponentially, d-DNNF smaller from tw≈5; **d4 == OBDD on 32/32 where both completed, + 3 more where OBDD timed out** | D | 1 |
| **E6** non-monotone | MINUS at scale — correct + feasible on a stock engine? | ⊖ built at 10 M/100 M; WMC == PWE (Δ ≤ 1.1e-16); baselines can't produce these probabilities | C | 3a–b |
| **E8** Wikidata 2.13 B | Full fragment on a **billion-triple real KG**? | **31/41** single queries build directly on the 2.13 B corpus (9 too-large + 1 OOM); up to ≈ **772 k derivations** (≈ 1 M gates) | A,B,C | 3c |
| **E10** multi-engine | Is the circuit a property of the **rewrite**, not the engine? | **byte-identical** circuit on **4 engines** — GraphDB / Oxigraph / QLever / MillenniumDB (Java, Rust, C++), 13 shapes × 4 = 52 checks | A | — |
| **E11** shared vs per-answer | Same answers as per-answer how-provenance, but **cheaper**? | identical probs (Δ = 0); shared Θ(N+S) vs per-answer Θ(N·S) → **~9× @ N=1000**, up to ~29× (layered-4×4) | B,C | 2b |
| **G2b** NPCS reimplementation vs ours (honest) | Is our circuit smaller than per-answer NPCS-style strings? | on **selective** queries **no** — ours ~1.7× more elements, ~12× more bytes. Compactness is a *deep/reconvergent* property (E2/E11), not universal | B(–) | 2c–d |
| **G3** end-to-end latency | Where does the PQE time go? | S-star 12 ms · TPC-H Q3 **6.45 s** (construct 3.10 + compile 3.33 + **WMC 0.035**) · WD-path **2.16 s** (compile ~1 ms). **WMC ≤ 36 ms everywhere**; Q3 dominated by the pure-Python variable ordering | A | 4b |
| **G4 / G2a / R8.3** vs ProvSQL | vs the strongest baseline — same result, what latency? | **exact parity, max_abs_error = 0.0**; TPC-H Q3 ours faster on all 5 segments (3.5–6.4 s vs 5.0–7.6 s); reconvergent: ours faster @SF0.01, ProvSQL @SF0.1 | A,C | 4a |
| **G6** d4 on real circuits | Do **three independent evaluation routes** (OBDD, PWE, d4) over the **same** emitted circuit agree? | OBDD == PWE == d4, **26/26 sampled answer circuits** (Q3: 8 of its answers; incl. all 16 property paths) | C | T1 |
| **G7** reification | Does the reification scheme matter? | SPARQL-star = 1 triple/fact vs Standard 3× (1.9× fewer bytes); **circuit identical either way** | A | — |
| **G8** space/memory | Footprint at billion-triple scale? | WD-path over the 60 M P279/P131 subgraph (from the 2.13 B corpus): peak RSS **166 MB**, 2.3 s | A | — |
| **G10** complex class | Does the WatDiv **Complex (C)** class build? | C1 (8-pattern) @10 M: 8 answers, 168 gates+edges, 4.5 s → full L/S/F/C taxonomy | A | — |
| **paths** (E-paths / ablation) | Property paths (baselines **cannot** do) — correct, polynomial, and why the design? | validated at scale: single-predicate **`p+`** (`P279+`, `P131+`) and **`p*`**, WMC==PWE; single-level compound closures work on small graphs (gallery); **nested/arbitrary compound fail-fast** (guarded); frontier **IRI-only**; dense cyclic `friendOf+` currently fails (suspected request-size/transport issue, root cause unconfirmed). **3-variant ablation**: merged = *wrong* → shared = correct-but-slow → isolated (PathIsoSeq) = correct + fast | C | — |

---

## Paper figures (PDF + 300-dpi PNG in `figures/final/`)

1. **`paper_fig1_compilation`** — two-panel E4 figure: fixed-treewidth timeout behavior and growing-treewidth
   behavior use the same axes and compiler encoding.
2. **`paper_fig2_sharing`** — a SPARQLprov/NPCS-style four-panel story: synthetic reconvergence, shared
   compilation, and an honest direct NPCS comparison in construction time and serialized output.
3. **`paper_fig3_construction`** — grouped query-template comparisons on WatDiv 10M/100M (S/F/L/M),
   plus the real Wikidata 2.13B reach/scale scatter.
4. **`paper_fig4_pqe`** — grouped ProvSQL latency comparison and a normalized SPARQLprov-style
   decomposition of construction, compilation, and WMC time.
5. **`paper_table1_validation`** — OBDD/PWE/d4 maximum-error table for the 26 sampled answer circuits.

Full design rationale, source-paper comparison, and manuscript-ready captions are in
[`PAPER_FIGURES.md`](PAPER_FIGURES.md). The manuscript figures now live in `figures/final/`, rendered
through the shared `figstyle` grammar (identical to the `figures/drafts/` layout drafts). The old
top-level `fig1_...fig7_...` and `paper_...` PNGs have been retired in favor of that folder.

Two sets live in `figures/final/` (see [`figures/final/README.md`](figures/final/README.md)): the compact
composite `paper_fig*` above, and the **drafts-structure** `result_r9_*` figures — the full-result
SPARQLprov/NPCS-style layouts (`make_result_figures.py`) that carry the complete experiment matrix,
with `DATA PENDING` on sub-panels awaiting the ROUND-9 server run. The per-template/per-engine flagships
(construction, e2e, multisource) stay in `figures/drafts/` until that run lands.

---

## Suggested paper results narrative

1. **Can a stock SPARQL engine construct the representation?** — Fig. 3.
2. **When does sharing pay, and when does it not?** — Fig. 2.
3. **Does compilation follow the predicted structural boundary?** — Fig. 1.
4. **Does the full PQE pipeline remain competitive with ProvSQL?** — Fig. 4.
5. **Do independent evaluators agree on the emitted circuits?** — Table 1.

---

## Honest caveats (say these before a reviewer does)

- **Compactness (B) is conditional.** On selective/low-sharing queries the RDF circuit is *larger* than NPCS
  strings (G2b) — the win is deep/reconvergent queries (E2, E11). Do not claim a universal size win.
- **TPC-H Q3's 3.3 s "compile" is the current pure-Python variable ordering** — not the ROBDD build and not
  WMC (36 ms). A native compiler / linear-ordering heuristic *should* reduce it (not yet measured). WMC is
  negligible everywhere (≤ 36 ms).
- **Some Wikidata queries are too-large/OOM** (9 too-large + 1 OOM of 41 singles) — the KG-scale story is
  selective queries + a small reachable subgraph, not arbitrary dense queries.
- **"Wikidata 2.13 B" needs care:** the E8 *non-path* singles run directly on the 2.13 B corpus; the
  *property-path* results (WD-path) are on a **60 M-triple P279/P131 subgraph extracted from** it.
- **Property paths:** validated at scale for single-predicate `p+`/`p*` (P279, P131); compound closures are
  gallery-only / partly fail-fast; frontier is IRI-only; dense cyclic `friendOf+` currently fails on the
  endpoint — a request-size/transport issue is suspected, but the HTTP method and root cause still need
  wire-level confirmation (RDF4J auto-POSTs long queries, so the curl GET/POST test alone doesn't prove it).
- **Property-path frontier is IRI-only** (blank-node/literal path nodes not yet supported).
- **The "same-binary d4v2" ProvSQL head-to-head is author-gated** (d4v2 won't build: proprietary PaToH +
  KaHyPar). It is *not* a correctness blocker — exact parity with ProvSQL is already established
  (`max_abs_error = 0.0`, R8.3); the pending item is only pinning the identical compiler on both sides.

---

## Provenance / reproduce

- Numbers are the committed results in `reference/*.csv`, `reference/watdiv/*.csv`, and the per-experiment
  `reference/RESULTS.md`. Timing is the **canonical 5-run table** (`reference/CANONICAL_TIMINGS.md`,
  current HEAD, post-`PathIsoSeq`) and is the only place headline wall-clock numbers live.
- Figures regenerate from those CSVs: `cd presentation && python3 make_figures.py`.
