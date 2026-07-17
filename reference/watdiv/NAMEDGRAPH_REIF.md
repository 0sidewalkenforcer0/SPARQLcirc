# Named-graph reification — completing NPCS's 4-scheme matrix (standard / rdfstar / wikidata / namedgraph)

The 4th reification scheme NPCS compares. Each base triple lives in its **own named graph**, and the graph
name **is** the provenance token, so the emitted reify pattern is `GRAPH ?prov { s p o }` — plain SPARQL 1.1,
so it runs on any store with named-graph support (**no SPARQL-engine modification**; only a scheme
definition added to our rewriter, exactly like the other three).

## What was added (engine)
- `Reification.NAMED_GRAPH` (`npcs/rewrite/Reification.java`): `reify(sp, prov)` → `GRAPH ?prov { s p o }`;
  `fromName("NamedGraph"|"namedgraph")`. A leaf-encoding change only — the ⊗/⊕/⊖ layer is untouched, like
  the other schemes.
- `CircuitRun`: recognizes `.nq` (N-Quads) / `.trig` for loading quad data.
- `pom.xml`: adds `rdf4j-rio-nquads` (the N-Quads Rio parser was not previously on the classpath).
- `reify.py --namedgraph`: emits N-Quads (`<s> <p> <o> <urn:t:N> .`), one quad/fact, graph = token.

> Note: `QueryGuard` does **not** block this. It rejects GRAPH/FROM only in the *user's* query; the
> `GRAPH ?prov {…}` here is in the *generated* CONSTRUCT, which the guard never inspects. (Earlier notes
> claiming this needed engine surgery / a guard change were wrong.)

## Verification (in-memory, no engine bring-up)
With the provenance token aligned to `urn:t:N` (same as Standard `reify.py`), the circuit is **byte-identical**
to Standard — the strongest form of reification-independence:

| data | Standard sha | NamedGraph sha | equal? |
|---|--:|--:|:--:|
| tiny chain (A-p-B, B-q-C), `?x :p ?y . ?y :q ?z` | `e0002463…` | `e0002463…` | **byte-identical** |
| real Wikidata P279 slice (20k edges), bound 2-hop | `32d7f288…` | `32d7f288…` | **byte-identical** |

Regression: `verify_g7_circuit_equiv.py` now runs NamedGraph as a first-class scheme in the battery —
Standard == NamedGraph is asserted **byte-identical + structurally identical across all four operator classes**
(AND / UNION / OPTIONAL / MINUS), alongside Standard == RDF-star, using `example.namedgraph.nq` (tokens aligned
to `ex:u_i`). This closes the earlier gap where NamedGraph had only the two hand-written inputs below.

## Scope
Like the WIKIDATA/RDF-star schemes, the property-path route stays Standard-only, so NamedGraph is exercised
on BGPs. Data is loaded as quads (`.nq`); each triple sits in its `urn:t:N` graph.

## Reification matrix now covered
| scheme | status | evidence |
|---|---|---|
| standard   | ✓ baseline | throughout |
| rdfstar    | ✓ 10M+100M, byte-identical, 3×/2× storage | `RDFSTAR_RESULTS.md` |
| wikidata   | ✓ real Wikidata, structural+WMC equivalence | `../wikidata/WIKIDATA_REIF_EQUIV.md` |
| **namedgraph** | ✓ byte-identical (synthetic + real Wikidata) | this file |
