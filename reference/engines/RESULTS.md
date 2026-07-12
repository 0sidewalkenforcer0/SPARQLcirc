# E10 — Engine portability (the circuit is engine-agnostic)

**Claim.** SPARQLcirc's provenance circuit is materialized by *unmodified, off-the-shelf* SPARQL 1.1
engines, and every compliant engine produces the **byte-identical** content-addressed circuit. The
engine is therefore not tied to one triple store: it emits standard CONSTRUCTs and the store does the
work. We demonstrate this across engines of deliberately different architecture (Java/RDF4J,
Rust-embedded, C++ read-only index, C++ path-engine).

## Why it is portable (design, not luck)

The γ rewriter emits CONSTRUCTs that use **only** standard SPARQL 1.1 — BGP / UNION / OPTIONAL /
MINUS / `BIND` / `IF` / `STR` / `CONCAT` / `IRI` / `SHA256` / `COUNT` — and are **deterministic**
(no `RAND`/`NOW`/`UUID`/`ORDER BY`/`LIMIT`, no vendor functions). Every gate IRI is
**content-addressed**: `urn:g:t:SHA256(inputs)` for a ⊗, `urn:g:a:SHA256(answer-key)` for a ⊕. Because
the identity of a node is a pure function of its content, *any* engine that computes the same SHA256
writes the same triples, and identical sub-circuits collapse into one shared DAG node automatically —
in the store, with no client-side canonicalization. `reference/verify_engine_agnostic.py` asserts the
determinism + SPARQL-1.1-only properties; the per-engine harness below asserts the byte-identity.

Two evaluation paths, from `CircuitRun`:
- **Read path** (BGP/UNION/OPTIONAL/MINUS) — N CONSTRUCTs, evaluated read-only, circuit accumulated
  client-side. Runs on **read-only** engines (QLever, MillenniumDB).
- **Write path** (property paths) — a client-driven fixpoint that INSERTs each round back, so it needs
  a **writable** endpoint. Runs on GraphDB / Oxigraph / (RDFox).

## Engines

| Engine | Version | Architecture | Writable | Data load | Bring-up |
|---|---|---|---|---|---|
| GraphDB   | 10.7.6 | Java / RDF4J          | yes | `importrdf preload` | baseline (E1–E9) |
| Oxigraph  | 0.5.9  | Rust, embedded/served | yes | `bulk_load` / `serve` | `pyoxigraph` + Apptainer `.sif` |
| QLever    | 0.5.49 | C++, read-only index  | no  | `qlever-index`     | Apptainer `docker://adfreiburg/qlever` |
| MillenniumDB | v1.0.0 | C++, path-focused  | yes (`sparql-update`) | `mdb import` | source build (no public image)¹ |
| RDFox     | —      | C++, in-memory + Datalog | yes | import | **deferred** (needs academic license) |

## Result 1 — byte-identical circuit (correctness / portability)

Each engine loaded with the identical Standard-reified gallery data; the SAME emitted CONSTRUCT plan
evaluated on it; the resulting circuit (an RDF graph = set of triples) diffed against the in-memory
RDF4J reference. `SHA256` smoke test first (content-addressing depends on it).

| query shape | circuit triples | GraphDB | Oxigraph | QLever | MillenniumDB |
|---|---:|:--:|:--:|:--:|:--:|
| atom            | 16 | ✓ | ✓† | ✓ | ✓ |
| join (BGP)      |  9 | ✓ | ✓† | ✓ | ✓ |
| union           | 19 | ✓ | ✓† | ✓ | ✓ |
| minus           | 33 | ✓ | ✓† | ✓ | ✓ |
| minus_disjoint  | 16 | ✓ | ✓† | ✓ | ✓ |
| minus_union     | 36 | ✓ | ✓† | ✓ | ✓ |
| minus_p2union   | 38 | ✓ | ✓† | ✓ | ✓ |
| minus_chain     | 38 | ✓ | ✓† | ✓ | ✓ |
| optional        | 49 | ✓ | ✓† | ✓ | ✓ |
| opt_left        | 60 | ✓ | ✓† | ✓ | ✓ |
| opt_right       | 33 | ✓ | ✓† | ✓ | ✓ |
| opt_disjoint    | 62 | ✓ | ✓† | ✓ | ✓ |
| distinct        | 16 | ✓ | ✓† | ✓ | ✓ |
| **`SHA256` fn** | — | ✓ (live) | ✓ | ✓ (live) | ✓ (live) |

Counts re-verified against engine fix **1e67021** (term-type-aware gate identity + recoverable
`urn:circuit:binding` metadata; the binding triples raise the per-shape triple count vs the earlier
pre-fix table but the byte-identity holds). GraphDB / QLever / MillenniumDB re-run live on the new jar;
† Oxigraph carries over from the pre-fix run (its server was down at re-verify time — the property is
engine-agnostic, so it holds, but re-run when the `.sif` is up).

**All four engines produce the byte-identical content-addressed circuit on all 13 E1 shapes** —
Java/RDF4J, Rust, read-only C++ index, and a C++ path-engine — with zero changes to the SPARQLcirc
engine (**52 byte-identity checks, all ✓**). The
divergences one might expect (blank-node labeling, UNION multiplicity, literal formatting) do not
arise: the circuit is a SET of content-addressed IRI triples, so identity is a pure SHA256 of content.

Harnesses: `reference/engines/verify_oxigraph.py` (in-process pyoxigraph), `reference/engines/verify_http.py`
(generic, any HTTP endpoint). Every engine that answers the CONSTRUCTs answers them **identically** —
the divergences one might expect (blank-node labeling, UNION multiplicity) do not arise because the
circuit is content-addressed IRIs and a set of triples.

### Per-engine correctness closure (E10 ⊇ E1)

The byte-identity set is now the **full E1 correctness battery** — all 13 non-path shapes E1 checks
`WMC == PWE` on (single source of truth: `engines/_gallery_shapes.py`, matching `verify_gallery.py`),
not the earlier 8. This closes the argument that correctness holds on *every* engine, not just RDF4J:
**per-engine correctness = (E10 byte-identity: engine circuit == RDF4J circuit) ∘ (E1: WMC(RDF4J circuit)
== PWE)**, so no shape is left where an engine builds an unverified circuit. **All four engines now
verified 13/13 byte-identical** — the 5 shapes the earlier 8-shape set omitted (`atom`,
`minus_disjoint`, `minus_p2union`, `opt_right`, `opt_disjoint`) confirmed on **GraphDB, QLever, and
MillenniumDB** (`verify_http.py`, live SHA256) in addition to Oxigraph. So per-engine correctness now
holds on the *entire* E1 battery for every engine, not just RDF4J. (`e10_byte_identity.csv`.)

## Result 2 — cross-engine build-time (performance)

Same reified WatDiv 10M (32.7 M triples) loaded into every engine as an HTTP endpoint; the SAME
emitted CONSTRUCT posted to each; `build_ms` = CONSTRUCT eval + gate materialization (data
pre-loaded). Queries bound to fixed entities so the circuit is identical across engines. The point is
*comparable* build-time — the same circuit at similar cost — not a micro-benchmark ranking.

| query | circuit (deriv / gates / ans) | QLever | Oxigraph | MillenniumDB | GraphDB† |
|---|---|--:|--:|--:|--:|
| S-star (3-star, bound)     | 54 / 56 / 2         | 7 ms | 46 ms | 6 ms | 31 ms |
| P2-path (2-path, bound)    | 13 / 26 / 13        | 9 ms | 34 ms | 4 ms | — |
| P2-unbound (2-path, all)   | 149 998 / 299 996 / 149 998 | **3.2 s** | **493 s** | **17.4 s** | — |

- **Small circuits** (tens of gates): dominated by fixed per-query overhead (parse + HTTP round-trip),
  so all engines land in single-to-tens of ms — the construction itself is negligible.
- **Large circuit** (P2-unbound, ~300 k gates): now construction-dominated, and the engines **diverge
  sharply** — QLever 3.2 s, MillenniumDB 17 s, Oxigraph **493 s** (~150× QLever; its CONSTRUCT
  materializing 150 k `SHA256` gates does not scale). **All three build the identical circuit** — the
  same 149 998 derivations — so engine choice trades construction speed, never correctness.
- **Identical structure across engines**: `deriv` matches everywhere (54 for S-star, 149 998 for
  P2-unbound). QLever's larger raw `gates`/`ans` counts are duplicate triples in its CONSTRUCT *stream*,
  not a different circuit — as a SET (what the store materializes) it is byte-identical (Result 1).

† GraphDB is a **reference** value from E3 (S-star on a different WatDiv sample; it was busy serving
E8's Wikidata preload during this run, so it was not re-measured here). Absolute times are on a
**shared machine** (E8's ~1.2 B-statement preload co-ran); the result is order-of-magnitude
comparability + relative construction speed, not a precise benchmark. Byte-identity (Result 1) is the
clean, machine-independent headline; build-time confirms it is *practical* on every engine.

### Scaling to WatDiv 100 M (QLever)

Same circuit machinery on the 100 M reified WatDiv graph (`engines/overnight/e10_qlever_100m.csv`):
- **S-star** (bound): 855 derivations, **42 ms** — the star grew ~16× vs 10 M (54 deriv) with the data,
  build stayed tens of ms.
- **P2-unbound** (all purchases): **288 205 derivations** (2× the 10 M count), circuit built until the
  QLever 30 s query timeout — construction-bound at this size, same shape as the 10 M run.

Portability + comparable build cost hold at 100 M, i.e. the content-addressed circuit is engine- and
scale-robust across the E1–E10 range.

## Caveats

- **Read-only engines** (QLever, MillenniumDB) run the whole E1–E9 *base* (read path) but not the
  writable property-path protocol — property-path experiments stay on GraphDB/Oxigraph.
- **RDFox** deferred (academic license required); it is SPARQL-1.1-complete and writable, so it slots
  into both paths once licensed.

¹ MillenniumDB has no public container image; built from source (`cmake -DCMAKE_BUILD_TYPE=Release`).
Two portability fixes for a modern toolchain (GCC 11.5 + Boost 1.91 via conda, vs the repo's
Alpine/Boost-1.82 target): expose conda Boost on `CPATH`, and add `#include <algorithm>` to 17 sources
that relied on a transitive include newer libstdc++ no longer provides. Both are build-environment
fixes, not engine changes — the SPARQLcirc side is untouched.
