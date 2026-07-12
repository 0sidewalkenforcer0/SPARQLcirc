# G7 — SPARQL-star reification (shrinks the Standard 3× blow-up; circuit unchanged)

E8/E10 used **Standard** reification (a triple `s p o` → `t rdf:subject s ; rdf:predicate p ; rdf:object o`,
a 3× blow-up). On RDF-star engines the same fact is one quoted triple `<< s p o >> occ:occurrenceOf t`.
G7 quantifies the saving and confirms the emitted **provenance circuit is identical** either way.

## Reification size (WatDiv, 100 k-triple sample)

| encoding | bytes | vs raw | B/fact | triples/fact |
|---|--:|--:|--:|--:|
| raw `.nt`             | 11.67 MB | 1.00× | 117 | 1 |
| **Standard reified**  | 32.24 MB | 2.76× | 322 | **3** |
| **SPARQL-star**       | 17.06 MB | 1.46× | 170 | **1** (quoted triple) |

**SPARQL-star reification is 3× fewer triples and 1.89× fewer bytes than Standard.** The store indexes
one triple-term per fact instead of three reification triples, and the base triple `s p o` remains
directly queryable. (Bytes shrink 1.89× rather than 3× because each SPARQL-star line still spells out
`s`, `p`, `o` once, whereas Standard repeats the token IRI 3× plus three `rdf:` predicates.)

## The circuit is reification-independent (verified)

Same query built under **both** schemes on the paper example (`example.standard.ttl` vs
`example.star.ttls`), RDF-star parsed as Turtle-star by the engine:

| query | SPARQL-star provenance | Standard provenance | identical? |
|---|---|---|:--:|
| `and` (monotone)      | `⊕((⊗u1,u3,) (⊗u2,u3,))` | `⊕((⊗u1,u3,) (⊗u2,u3,))` | ✓ |
| `minus` (non-monotone)| `⊕((⊖⊕((⊗u1,)(⊗u2,)),⊕((⊗u3,)),))` … | identical | ✓ |
| `optional`            | 3 answers, `⊗`/`⊕`/`⊖` over u1–u4 | identical | ✓ |

Same ⊕/⊗/⊖ gate structure, same tokens `u1…u4` — only answer *ordering* differs (set iteration). The
reification scheme changes **how base facts are addressed in the store**, not the provenance structure
the CONSTRUCTs build. So everything downstream (compile, WMC, PQE, byte-identity across engines) is
unchanged.

## Findings

- **A free ~3× data-size reduction on RDF-star engines**, at zero cost to the method: identical circuit,
  identical probabilities. E8's Standard-reification footprint (the 3× blow-up the reviewer flagged) is a
  choice for maximally-portable engines, not a property of the approach — GraphDB/Oxigraph (RDF-star)
  take the compact SPARQL-star input and emit the same circuit.
- **Reification is orthogonal to provenance.** Confirmed across monotone (`and`), non-monotone (`minus`),
  and `optional` shapes. This is why E10's byte-identity and E1's WMC==PWE carry over unchanged to the
  SPARQL-star encoding.

## Caveats

- Byte ratio measured on a 100 k-triple WatDiv sample; triples/fact (3× → 1×) is exact by construction.
- SPARQL-star needs an RDF-star store (GraphDB 10.x, Oxigraph, Jena ✓; QLever/MillenniumDB use Standard —
  hence E10 uses Standard for cross-engine byte-identity). The circuit-equivalence above is what lets a
  deployment pick either without affecting results.
