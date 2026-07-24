# Construction cost is engine-dependent, not method-dependent

_RQ3/RQ5 supporting analysis. Reified WatDiv 100M. Single measured run per cell (`--warmups 0 --runs 1`); construction time is `cell_wall_ms`. Circuit is byte-identical across engines (see `oxigraph-100m/BYTEID_100M.md`), so any time difference is the host engine's query evaluation, not the SPARQLcirc circuit._

## Same circuit, same cell, two engines (GraphDB vs Oxigraph, 100M)

| cell | answers | circuit nodes | GraphDB build | Oxigraph build | slowdown |
|---|---:|---:|---:|---:|---:|
| LL3 | 3 | 20 | 0.38 s | 1166.0 s | 3059x |
| FF5 | 38 | 304 | 0.48 s | 59.8 s | 124x |
| FF2 | 13 | 130 | 0.86 s | 104.7 s | 121x |
| LL1 | 4 | 20 | 0.41 s | 12.5 s | 31x |
| FF3 | 19 | 122 | 2.56 s | 25.8 s | 10x |
| LL4 | 299 | 1196 | 1.83 s | 13.4 s | 7x |
| FF4 | 103 | 299 | 25.09 s | 124.1 s | 5x |
| FF1 | 11 | 50 | 5.50 s | 16.2 s | 3x |
| LL2 | 961 | 3845 | 69.90 s | 30.6 s | 0.4x (faster) |

## The cost is query evaluation, not circuit construction

The decisive case is **LL3: 3 answers, a 20-node circuit, yet 19 minutes on Oxigraph (0.38 s on
GraphDB, 3059x).** The emitted circuit is trivial, so the time is not circuit emission and not the
SHA256 content-addressing (only 20 gates to hash). It is spent *evaluating the SPARQL query that
locates the provenance witnesses over the store*. Three factors compound:

1. **Reification multiplies the joins.** The store uses Standard reification: each logical triple is
   stored as 3-4 triples (`rdf:subject`/`predicate`/`object`). Matching one triple pattern is a
   3-way self-join, so a k-triple query becomes a ~3k-way join over ~327M reified triples. The
   construction CONSTRUCTs are inherently join-heavy.

2. **Oxigraph's query planner is comparatively simple.** Oxigraph is a lightweight Rust/RocksDB store;
   its join optimizer is far less sophisticated than GraphDB's (statistics-driven RDF4J) or QLever's
   (purpose-built for large joins with cardinality estimation). A poor join order materializes an
   enormous intermediate result even when the final answer set is tiny -- LL3 collapses to 3 answers
   only *after* a large intermediate blows up.

3. **The effect is not uniform, which is the tell.** LL2 (961 answers) is actually *faster* on
   Oxigraph than GraphDB (0.4x), and FF1 is only 3x. Oxigraph is not constantly slow; specific
   reified-join shapes trigger the pathology -- the signature of bad join-order choices, not a
   constant per-triple overhead.

Corroborating 10M portability datapoint (E10): on one large circuit (P2-unbound, 149998 derivations)
the *identical* circuit was produced by QLever in 3.2 s, MillenniumDB in 17.4 s, and Oxigraph in
493 s (~150x). QLever, also read-only, is the fastest -- so this is about the query engine, not
read-only vs writable.

## Framing for the paper

Byte-identity is a **correctness / portability** property and is engine-independent: every engine
emits the same content-addressed circuit. Construction **time**, in contrast, is a property of the
host engine's SPARQL query optimizer, not of SPARQLcirc. The recommended narrative:

> The provenance circuit is a deterministic, content-addressed artifact: any conformant SPARQL 1.1
> engine materializes it byte-for-byte identically (verified on GraphDB, Oxigraph, QLever, and
> MillenniumDB at 10M, and on GraphDB+Oxigraph at 100M). Construction throughput, however, depends on
> the engine's join evaluation over the reified store and varies by orders of magnitude across
> engines and query shapes; it should be read as a property of the chosen engine, not of the method.

So: report byte-identity as the universal (asserted) result; report construction time conditionally
("on engine X, under reification, cost scales with join evaluation and ranges from ... to ..."). Do
not average Oxigraph's pathological cells into a headline "construction cost" number -- separate the
correctness claim from the per-engine timing.

_Note: numbers above are from the cleanly-built F/L cells shared by both engines. The O/S/M cells
(join-heaviest) are being measured in the Oxigraph continuation and mostly hit the 1200 s cap, which
is itself evidence of the same query-evaluation bottleneck._

