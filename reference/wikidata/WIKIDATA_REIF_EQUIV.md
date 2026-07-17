# WIKIDATA reification scheme — correctness + structural equivalence (real Wikidata)

Extends the reification-independence result to NPCS's **Wikidata** reification scheme, on **real Wikidata
data** (the P279 `subclassOf` subgraph). Complements the WatDiv Standard-vs-RDF-star result
(`reference/watdiv/RDFSTAR_RESULTS.md`) — that covered `standard`/`rdfstar`; this covers `wikidata`.

## Setup
- Data slice: 20,000 `wdt:P279` (subclassOf) edges from `wikidata-data/p279_p131.wdt.nt`.
- Reified two ways from the SAME slice (fact N ↔ same edge under both):
  - **Standard**: `reify.py` → `urn:t:N rdf:subject/predicate/object …` (60,000 triples).
  - **Wikidata**: `reify_wikidata.py` → `s p:P279 urn:wds:N . urn:wds:N ps:P279 o` (40,000 triples); the
    statement node `urn:wds:N` is the provenance token (matches NPCS's "Wikidatareal").
- Query (source-bound 2-hop subclass chain), run in-memory via `CircuitRun` under each scheme:
  `SELECT ?y ?z WHERE { wd:Q764 wdt:P279 ?y . ?y wdt:P279 ?z }`

## Result (Standard scheme ⟺ WIKIDATA scheme)
| check | Standard | Wikidata | equal? |
|---|--:|--:|:--:|
| answers (term-aware) | 2 | 2 | **yes** |
| gate histogram | leaf 19999 · times 2 · plus 19987 | leaf 19999 · times 2 · plus 19987 | **yes (isomorphic)** |
| per-answer WMC (same fact → same prob) | 0.360625 / 0.43 | 0.360625 / 0.43 | **yes** |

**Conclusion.** The WIKIDATA reification scheme produces a **structurally isomorphic, WMC-equivalent**
circuit to Standard on real Wikidata data. The circuits are not *byte*-identical here only because the
provenance token IRIs differ by scheme (`urn:t:N` vs the statement node `urn:wds:N`) — that changes leaf
names, not the gate DAG or the probabilities. (On WatDiv the tokens were aligned, so Standard↔RDF-star came
out byte-identical; see `RDFSTAR_RESULTS.md`.)

## Deployed-engine construction time (GraphDB)
Same bound 2-hop query, `--construction flat`, warm (3 runs), on the loaded repos:
| scheme | repo | build_ms (3 runs) | answers | circuit |
|---|---|--:|--:|--:|
| Standard | `wdpaths` (60.5 M, urn:st: reified) | 516 / 477 / 481 | 2 | 2⊗ · 2⊕ · 24 tri |
| Wikidata | `wdstatements` (40.3 M, p:/ps:) | 484 / 477 / 492 | 2 | 2⊗ · 2⊕ · 24 tri |

Warm, the two schemes construct in ~equal time (~480 ms); same answers, isomorphic circuit (different token
IRIs → different sha). Cold first-touch: Standard 2128 ms (the larger 60 M repo activates slower) vs
Wikidata 488 ms.

## Flat vs factored (D2 — keep both)
The row above is `--construction flat`; per the D2 "keep both" decision we add the factored counterpart on a
bound 2-hop P279 chain with **endpoint-only** projection (`SELECT ?z WHERE { <Q8> wdt:P279 ?y . ?y wdt:P279 ?z }`)
so `?y` is a pure interior join variable factored must eliminate. Harness `reference/wikidata_factored.py`,
numbers → `wikidata_factored_vs_flat.csv`.

| scheme | repo | flat | factored |
|---|---|--:|--:|
| Standard | `wdpaths` | 2 gates / 58 ms | **too-large** (>120 s cap) |
| Wikidata | `wdstatements` | 2 gates / 56 ms | **too-large** |

Same story as WatDiv L-path: factored over-materialises the chain's interior variable — its elimination
message spans the *full unrestricted* P279 relation (5.2 M edges), not `Q8`'s neighbourhood — while flat keeps
the whole product anchored at the bound source (2 gates). So for shallow chains **flat is the right mode**,
on real Wikidata as on WatDiv (cf. `../watdiv/RDFSTAR_RESULTS.md`).

Isolation note (E2): a factored cell that hits the cap is SIGKILLed, which bypasses `CircuitRun`'s `finally`
and can leave its `urn:sc:*` feedback workspace in the repo. The harness now self-heals (deletes `urn:sc:*`
after a timed-out cell); the deployed repos return to their base size. This is the concrete motivation for
routing factored feedback into a named graph (the deliberately-unfinished half of CIRCUIT_PERSIST).

## Scope notes
- `CircuitRun`'s property-path route is Standard-reification-only, so the WIKIDATA scheme is exercised on a
  **BGP** (a 2-pattern subclass chain), not a `P279+` path.
- The full p:/ps: statements dump (`p279_p131.statements.nt`, 3.9 G) exists for a deployed-engine run; this
  page is the in-memory correctness/equivalence check (no engine bring-up needed).
