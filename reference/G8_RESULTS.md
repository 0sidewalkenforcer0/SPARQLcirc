# G8 — space & memory at scale

Three numbers a systems reviewer asks for, on the real circuits. `g8_space_memory.py` →
`g8_space_memory.csv`. **Regenerated on the post-`1e67021` jar.**

## Peak build memory (RSS)

| build | graph | heap cap | **peak client RSS** | wall |
|---|---|--:|--:|--:|
| Wikidata `P279+` path (G1) | **2.13 B triples** | 8 g | **161 MB** | 2.6 s |

**The client footprint is bounded by the *reachable subgraph*, not the graph size.** Building a property
path on a **2.13-billion-triple** store, the `CircuitRun` client peaks at **161 MB** — orders below the
8 g cap — because the G1 frontier-restricted protocol pulls only the reachable subgraph and materializes
gates back into the store. The 2.13 B triples live in GraphDB (its own 60 g heap); the circuit builder
does not hold them. (Was 134 MB pre-fix; slightly higher now because `1e67021` un-merges the path
reach-states into the correct, larger circuit.) This is the memory result behind G1's "paths at KG scale."

## On-disk circuit size (N-Triples)

| query | answers | gates+edges | circuit bytes | NPCS string bytes (G2b) | shared-OBDD nodes |
|---|--:|--:|--:|--:|--:|
| watdiv-Sstar     |      2 |    272 |   36.5 KB |  2.6 KB |    34 |
| tpch-Q3          | 14 908 | 89 448 |   28.0 MB |    —    | 44 724 |
| watdiv-P2unbound | 149 998 | 749 990 | **263 MB** | **19.9 MB** | (n/a — large) |

## Findings

- **Provenance structure unchanged by the fix; raw bytes ≈ 2× from binding metadata.** `gates+edges`
  (⊗/⊕/⊖ + wires, what `parse_circuit` counts) is **identical to pre-fix** (S-star 272, Q3 89 448,
  P2-unbound 749 990) — the fix does not change the provenance DAG for these BGP shapes. Raw N-Triples
  roughly **doubled** (P2-unbound 133 → 263 MB, Q3 15 → 28 MB) purely because `1e67021` adds
  `urn:circuit:binding` / `c:var` / `c:val` **answer-recovery metadata** triples. That metadata is a
  recoverability feature, separable from the provenance the PQE consumes.
- **The compiled form is tiny; the serialization is IRI-heavy.** Gates are content-addressed with
  **SHA-256 IRIs** (`urn:g:t:<64 hex>`, ≈ 180 B/triple), so the raw dump is large — but the **compiled**
  representation the PQE uses is small: the shared OBDD for all 14 908 Q3 answers is **44 724 nodes**,
  G6's d-DNNFs are tens of nodes. Space that matters for *evaluation* is the compiled size.
- **Raw-byte vs NPCS is reconvergence-dependent — the E11 boundary.** On **P2-unbound** (149 998 answers,
  *low* cross-answer sharing) the flat NPCS strings (19.9 MB) are *smaller* in raw bytes than our
  SHA-256-IRI N-Triples: with little sharing to amortize, per-gate IRI overhead dominates. Same boundary
  as E11's compile-win — the circuit's space advantage materializes with **reconvergence** (recursion/
  paths, where NPCS strings duplicate shared sub-derivations per answer and blow up); on low-sharing
  shapes the flat form can be smaller. The circuit's *invariant* wins: **(i)** structural non-redundancy,
  **(ii)** a compact compiled form, **(iii)** it is **WMC-able** — NPCS strings are not.
- **A compact store would erase the IRI overhead.** Interning gate IRIs to integer node-ids collapses the
  ~180 B/triple to a few bytes/edge; the SHA-256 IRIs are a *portability* choice (content-addressing lets
  any engine dedup identically, E10), not a storage-optimal one.

## Caveats

- Peak RSS is the client `CircuitRun` process (VmHWM via `/proc`, `/usr/bin/time` absent here); GraphDB's
  server RSS is separate and holds the base graph. The 161 MB is the *builder's* footprint.
- P2-unbound's shared-OBDD node count is omitted (the pure-Python compile over 149 998 roots is the
  client bottleneck — G2a/G6; a native compiler / d4 handles it).
