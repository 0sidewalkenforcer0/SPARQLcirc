# G8 — space & memory at scale

Three numbers a systems reviewer asks for, on the real circuits. `g8_space_memory.py` → `g8_space_memory.csv`.

## Peak build memory (RSS)

| build | graph | heap cap | **peak client RSS** | wall |
|---|---|--:|--:|--:|
| Wikidata `P279+` path (G1) | **2.13 B triples** | 8 g | **134 MB** | 2.3 s |

**The client footprint is bounded by the *reachable subgraph*, not the graph size.** Building a property
path on a **2.13-billion-triple** store, the `CircuitRun` client peaks at **134 MB** — orders below the
8 g cap — because the G1 frontier-restricted protocol pulls only the reachable subgraph and materializes
gates back into the store. The 2.13 B triples live in GraphDB (its own 60 g heap); the circuit builder
does not hold them. This is the memory result behind G1's "paths at KG scale."

## On-disk circuit size (N-Triples)

| query | answers | gates+edges | circuit bytes | NPCS string bytes (G2b) | shared-OBDD nodes |
|---|--:|--:|--:|--:|--:|
| watdiv-Sstar     |      2 |    272 |   35.6 KB |  2.6 KB |    34 |
| tpch-Q3          | 14 908 | 89 448 |   15.4 MB |    —    | 44 724 |
| watdiv-P2unbound | 149 998 | 749 990 | **133 MB** | **19.9 MB** | (n/a — large) |

## Findings

- **The compiled form is tiny; the N-Triples serialization is IRI-heavy.** The circuit stores each gate
  as content-addressed triples with **SHA-256 IRIs** (`urn:g:t:<64 hex>`), ≈ 178 bytes/triple — so the
  raw N-Triples dump is large (P2-unbound 133 MB). But the **compiled** representation the PQE actually
  uses is small: the shared OBDD for all 14 908 Q3 answers is **44 724 nodes**, and G6's d-DNNFs are
  3–171 nodes. Space that matters for *evaluation* is the compiled size, not the serialized DAG.
- **Raw-byte vs NPCS is reconvergence-dependent — exactly the E11 boundary.** On **P2-unbound**
  (149 998 answers, *low* cross-answer sharing — independent 2-paths) the flat NPCS strings (19.9 MB) are
  *smaller* in raw bytes than our SHA-256-IRI N-Triples (133 MB): with little sharing to amortize, the
  per-gate IRI overhead dominates. This is the **same boundary as E11's compile-win**: the circuit's
  space advantage materializes with **reconvergence** (recursion/paths, where NPCS strings duplicate
  shared sub-derivations across every answer and blow up super-linearly), and on low-sharing shapes the
  flat form can be smaller. The circuit's *invariant* advantages hold regardless: **(i)** structural
  non-redundancy (each distinct derivation stored once, not per-answer), **(ii)** a compact compiled form,
  **(iii)** it is **WMC-able** — NPCS strings are not. (E2's "compactness" is the structural/factored
  measure; G8 adds the raw-serialization axis honestly.)
- **A compact store would erase the IRI overhead.** Interning gate IRIs to integer node-ids (the circuit
  is a pure DAG of content hashes) collapses the 178 B/triple to a few bytes/edge; the SHA-256 IRIs are a
  *portability* choice (content-addressing lets any engine dedup identically, E10), not a storage-optimal
  one.

## Caveats

- Peak RSS is the client `CircuitRun` process (VmHWM via `/proc`, `/usr/bin/time` absent here); GraphDB's
  server RSS is separate and holds the base graph. The 134 MB is the *builder's* footprint — the point
  the frontier protocol makes.
- P2-unbound's shared-OBDD node count is omitted (the pure-Python compile over 149 998 roots is the
  client bottleneck — see G2a/G6; a native compiler / d4 handles it).
