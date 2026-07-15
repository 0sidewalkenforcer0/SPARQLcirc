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

## Scope notes
- `CircuitRun`'s property-path route is Standard-reification-only, so the WIKIDATA scheme is exercised on a
  **BGP** (a 2-pattern subclass chain), not a `P279+` path.
- The full p:/ps: statements dump (`p279_p131.statements.nt`, 3.9 G) exists for a deployed-engine run; this
  page is the in-memory correctness/equivalence check (no engine bring-up needed).
