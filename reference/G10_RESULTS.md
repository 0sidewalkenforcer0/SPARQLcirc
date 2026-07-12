# G10 — comparability completeness: WatDiv Complex (C) category

E3/E6 covered WatDiv **L/S/F** (linear/star/snowflake) + our **P/M** (paths/minus); the NPCS/SPARQLprov
taxonomy also has **C (complex)**. G10 adds the C category so the workload matches the baselines' exactly.

## Complex-C query + result

[`watdiv/C-complex.rq`](watdiv/C-complex.rq) is WatDiv template **C1** (8 triple patterns: a 3-way star
on `?v0` (`caption`/`text`/`contentRating`) + a review chain (`hasReview → title / reviewer`) + an actor
join (`?v7 sorg:actor ?v6`)) — the defining "multiple correlated stars joined by chains" shape.

| query | scale | answers | ⊗ (Times) | ⊕ (Plus) | gates+edges | build |
|---|---|--:|--:|--:|--:|--:|
| C-complex (C1) | WatDiv 10 M (32.7 M reified) | 8 | 16 | 8 | 168 | 4.5 s |

Built with the post-**1e67021** jar via `CircuitRun` endpoint mode on the loaded `watdiv` repo.

## Findings

- **The C (complex) category builds correctly.** The 8-pattern complex join yields a valid provenance
  circuit (8 answers, 168 gates+edges, no ⊖) — the machinery is not limited to the L/S/F shapes E3 used;
  it handles the correlated multi-star + chain + join shape that stresses join provenance the most. With
  C added, the WatDiv workload now spans the **full L/S/F/C taxonomy** the baselines use, plus our
  **property-path (P)** and **MINUS (M)** extensions.
- **Complex joins are selective ⇒ small circuits.** C1's actor-join is highly selective (8 answers), so
  the circuit is compact — the opposite end of the spectrum from the unbound P2 (149 998 answers, G8).
  Both build with the same CONSTRUCT plan.

## Caveats / deferred

- **100 M and 200 M scale points deferred.** C1 *unbound* on WatDiv 100 M did not finish in the
  session's time-box (the complex join fans out at 100 M); a **bound** C1 (E3-style, `?v0` fixed to an
  entity) is the right way to get selective 100 M/200 M points, and the **200 M** scale additionally
  needs generating + reifying + loading ~200 M triples (multi-hour, `watdiv-data/watdiv` generator is
  present). Both are mechanical follow-ups, not blockers — the C *category* is demonstrated at 10 M here.
- Single run (construction shape, not a benchmarked mean); G4's protocol applies when these become
  headline timings.
