# Historical timings — SUPERSEDED, do not cite

These tables predate engine fix **`1e67021`** (or were single-run) and are retained only for provenance.
**Cite `CANONICAL_TIMINGS.md` instead.** Kept here so the RESULTS narrative files (G3/G4/G2a) can drop
their conflicting in-file numbers without losing the record (R8.1).

## Why superseded

`1e67021` (term-type-aware gate identity) fixed a STR-collision that **merged distinct answer/reach
states** and added `urn:circuit:binding` answer-recovery metadata. Effect on timings: property-path
circuits are now correctly larger (un-merged reach states → the OBDD compile is seconds, not ~1 ms), and
BGP construct grew (more CONSTRUCT output). So every pre-fix number below is measuring a *different
(under-merged) circuit* and must not be quoted.

## Superseded rows

| query | source (pre-fix / single-run) | reported total | canonical (post-fix 5-run) |
|---|---|--:|--:|
| tpch-Q3        | `G4_RESULTS.md` (pre-fix, 5-run, "stable & citable") | 1.654 s | **2.78 s** |
| tpch-Q3        | `G3_RESULTS.md` (post-fix but **single run**, high sample) | 4.09 s | **2.78 s** |
| wikidata-WDpath| `G4_RESULTS.md` (pre-fix) | 2.127 s, compile 1 ms | **8.04 s, compile 5.75 s** |
| wikidata-WDpath| `G3_RESULTS.md` (post-fix single run) | 7.24 s, compile 3.87 s | **8.04 s [7.69–8.14]** |
| watdiv-Sstar   | `G4_RESULTS.md` (pre-fix) | 12 ms | 12 ms (unchanged) |
| tpch-Q3 ours   | `G2a_RESULTS.md` (pre-fix warm) | 1.65 s | **2.78 s** |

Notes:
- The pre-fix WD-path compile (~1 ms) was small only because the circuit was **wrong** (reach states
  merged → provenance under-counted). The post-fix 5.75 s is the correct reconvergent circuit.
- G3's single-run post-fix TPC-H (4.09 s) was a high sample; the 5-run median (2.78 s) is authoritative.
- ProvSQL: cold-first-call 3.6 s (G2a first draft) and cold `CREATE TABLE` SF 0.1 = 29.4 s were both
  **cold** artifacts; warm canonical is 1.03 s (SF 0.01) / ~9.8 s (SF 0.1). Do not cite the cold numbers.
