# E8 — Wikidata (NPCS comparison at native-statement scale)

Reproduces NPCS's setup: native Wikidata **statement reification** (the engine's `Wikidata` scheme,
matching NPCS's *Wikidatareal*), NPCS's own Basic query set (WDBench single/multiple/optional), on a
real Wikidata graph. Goal: show the shared-circuit construction runs on real Wikidata at scale.

## Data

Official Wikidata **truthy** dump (`latest-truthy.nt.gz`, 71 GB gz), filtered to the NPCS query
predicates, reified into statement form (`s p:P wds . wds ps:P o`, `reify_wikidata.py`):
**subset.statements.nt = 224 GB, ≈ 2.13 B statement-triples.**

## Preload (the infrastructure result)

GraphDB `importrdf preload`. **First attempt OOM'd at `GDB_HEAP_SIZE=28g`** (the 956 M-entity pool +
sort buffers exceed 28 g). **Retry at 80 g succeeded:** `PRELOAD_OK`, wikidata repo size =
**2,126,677,196**. Lesson (recorded): preloading ~2 B reified statements needs ≥ ~60–80 g heap; the
entity pool alone is ~11 GB.

## Result 1 — construction on the 2.13 B graph (NPCS single queries)

Circuit CONSTRUCTs posted to the loaded repo; per-query 30 s cap (`E6_POST_TIMEOUT`); incremental CSV.
Partial run (2 h wall-cap covered the `single` category, 41/49). `reference/watdiv/e8_wikidata.csv`.

| metric | value |
|---|---|
| queries that built a circuit | **31 / 41** (single) |
| build time (ok) | min **4 ms**, median **28 ms**, max **62 s** |
| circuit size (derivations) | min 0, median 214, max **772 812** |
| capped (circuit > 4 M triples) | 9 `too-large` |
| out-of-memory (client accumulation) | 1 |
| correctness | **WMC == PWE Δ = 2.2×10⁻¹⁶** on a small circuit (`single/02`) ✅ |

**Selective NPCS queries build circuits in tens of ms on the 2.13 B graph** — construction is native
and scales. **Non-selective queries** (e.g. `?x wdt:P105 wd:Q7432` = *all species*, millions of
matches) are expensive: the circuit reaches millions of gates and hits the `too-large` / timeout caps.
That breadth is an artifact of our predicate filter (broader than WDBench's curated graph); the clean
NPCS comparison would use WDBench's curated subset (what NPCS itself measured).

## Result 2 — property paths at Wikidata scale (limitation)

Extracted **P279 (subclass, 5.2 M edges) + P131 (contained-in, 14.9 M edges)** from the truthy graph,
Standard-reified (60 M triples), loaded into a `wdpaths` repo. WD-path (`Q7397 wdt:P279+`, software's
superclasses) and WD-path2 (`Q60 wdt:P131+`, NYC's administrative containment) via the client-driven
iterative fixpoint (endpoint mode). The sources exist (Q7397 → 4 P279 edges, Q60 → 1 P131 edge), but
both **exhausted heap** (`OutOfMemoryError`): the per-round queries over 60 M Standard-reified triples
plus client-side circuit accumulation do not scale to this size.

**Status:** property-path provenance is demonstrated + **verified (WMC == PWE)** at moderate scale on
real WatDiv `friendOf` and synthetic ring/clique graphs (Round 3 — where naive walk enumeration is
factorial/infinite and the circuit stays polynomial). Scaling the iterative protocol to a 60 M-edge
Wikidata relation (streaming rounds, frontier-only queries, bounded accumulation) is future work.

## Caveats

- **Partial NPCS run** — `single` category only (2 h cap); broad queries capped. Not a full 135-query
  head-to-head. The point demonstrated is *construction on a real 2.13 B-triple Wikidata graph*.
- **Broad filter** — our subset is larger/broader than WDBench's curated graph; for a like-for-like
  NPCS comparison, use WDBench's own graph.
