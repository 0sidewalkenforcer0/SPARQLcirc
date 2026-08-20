# Reification schemes — design decision, baseline comparison, and RDF-star vs RDF 1.2

Where each experiment's reification scheme comes from, why, and how it relates to the
baselines and to the incoming RDF 1.2 standard. Companion to `EVALUATION.md` / `TECHREPORT.md`.

## TL;DR (the decision)

`γ` is **parameterized by the reification scheme** (`Reify`, `engine/.../rewrite/Reification.java`).
Because the emitted circuit is **byte-identical across schemes** (G7), the
physical layout and its matching leaf query affect storage and query planning,
but not the circuit event or its probabilities. Data preparation and query
rewriting must select the same layout.

| experiment | scheme | why / matches |
|---|---|---|
| WatDiv (E3, E10, G10) | **Standard** | lowest common denominator — the mixed layout is ordinary RDF and loads on every SPARQL 1.1 store |
| WatDiv, compact variant | **SPARQL_Star** (RDF-star) | 2 physical statements/fact instead of Standard's 4 in the current mixed layout; matches NPCS's token encoding |
| TPC-H (E9, G4, R8.3) | **naryrel** (per-row token) | matches **SPARQLprov**'s Direct-Mapping n-ary + **ProvSQL**'s per-tuple granularity |
| Wikidata (E8, G8, paths) | **Wikidata** (`p:`/`ps:` statement) | matches **NPCS**'s "Wikidatareal" scheme |

**Do not switch everything to RDF-star.** Standard is doing real work: it is the only scheme
that all four E10 engines accept. Dropping Standard would shrink E10's
"4 independent codebases incl. C++" claim to ~2 engines and break the path experiments. The
right posture is **Standard as portable default + RDF-star on GraphDB for the NPCS-comparable,
compact run** — free, because the circuit is scheme-invariant.

## What the baselines use

- **SPARQLprov** — `Reify` is a *parameter*. Benchmarks the "three most popular schemes":
  **named graphs** (its default, on Virtuoso), **standard reification**, and **Wikidata**; TPC-H via a
  **Direct-Mapping n-ary** encoding. Reports **standard reification as the most expensive** (most
  extra triples). Head-to-head vs NPCS: *Virtuoso + named graphs*.
- **NPCS** — leads with **RDF-star** (SPARQL-star nested patterns): "a natural way to do it, but …
  supports any reification scheme." Also evaluates **named graphs** and the **Wikidata** scheme;
  does **not** use standard reification.
- **ProvSQL** — relational, not RDF; runs TPC-H natively with **per-tuple** provenance granularity
  (what our `naryrel` mirrors).

Both RDF baselines are theory-parametric in `Reify`; the difference is emphasis (NPCS → RDF-star,
SPARQLprov → named graphs). Neither's primary is standard reification.

## Our supported schemes (`Reification.java`)

| scheme | leaf encoding of `(s,p,o)` → token `?t` | token is | matches |
|---|---|---|---|
| `Standard` | `s p o . ?t rdf:subject s ; rdf:predicate p ; rdf:object o` (4 statements) | the statement node | asserted data plus RDF 1.0 reification |
| `SPARQL_Star` | `s p o . << s p o >> :occurrenceOf ?t` (2 statements) | the linked token | asserted data plus NPCS-style RDF-star occurrence link |
| `NamedGraph` | `s p o . GRAPH ?t { s p o }` (default graph plus one named-graph quad) | the graph name | asserted data plus named-graph token |
| `Wikidata` | `s p:P ?t . ?t ps:P o` | the statement node | NPCS "Wikidatareal" |
| `naryrel` | `s p o . BIND(s AS ?t)` (data stays plain) | the subject/row | SPARQLprov, ProvSQL |

The upper spm-semiring layer (⊗/⊕/⊖) is identical across layouts; only the leaf match differs.

## Default and historical layouts

The ordinary Java scheme names (`Standard`, `SPARQL_Star`, and `NamedGraph`)
select the mixed layout shown above. The asserted triple is emitted immediately
next to its token lookup in the leaf rewriter. This preserves the algebra scope
of BGP, OPTIONAL, UNION, and MINUS patterns and gives the endpoint an ordinary
triple pattern on which to apply its normal indexes and join planning.

`reference/watdiv/reify.py` also writes mixed data by default. The old
reification-only layout remains available solely for controlled reproduction:

| mixed default | historical query name | data-generator option |
|---|---|---|
| `Standard` | `Standard_Pure` | `--pure` |
| `SPARQL_Star` | `SPARQL_Star_Pure` | `--star --pure` |
| `NamedGraph` | `NamedGraph_Pure` | `--namedgraph --pure` |

`reference/reify_query.py --pure` provides the same explicit R-control variant.
Mixed and pure data/query layouts must not be crossed: a mixed query against a
pure store has no asserted triples to match.

## Engine support (why Standard is the portable default)

From `reference/engines/README.md`:

| engine | RDF-star? | paths? | reification used |
|---|:--:|:--:|---|
| GraphDB (primary; shared w/ NPCS) | ✅ | ✅ | Standard / SPARQL\* |
| Fuseki (Jena; shared w/ SPARQLprov) | ✅ | ✅ | Standard / SPARQL\* |
| Oxigraph (Rust; independent → byte-identity) | ✅ | ✅ | Standard / SPARQL\* |
| **QLever** (Wikidata scale) | ❌ | ❌ | **Standard only** |
| **MillenniumDB** (property-path SOTA) | ❌ | ❌ | **Standard only** |

E10's four-engine byte-identity (GraphDB, Oxigraph, QLever, MillenniumDB — Java/Rust/C++) is only
possible because Standard reification loads everywhere. That universality — "plain SPARQL 1.1, so
the circuit builds even on a store with no RDF-star" — is itself a selling point over NPCS, whose
primary path depends on an RDF-star engine.

## RDF-star (2021) vs RDF 1.2 — the difference

`SPARQL_Star` above uses the **2021 RDF-star** quoted triple `<< s p o >>`. The **RDF 1.2**
successor (W3C Candidate Recommendation, 2026-04) reworks reification with **triple terms +
`rdf:reifies` + reifiers**. There is no single proper noun for the RDF 1.2 mechanism; refer to it
as *"RDF 1.2 triple-term reification (via `rdf:reifies` / reifiers)"*.

Two problems the 2021 quoted triple has, which RDF 1.2 fixes — shown on one fact stated by two
sources with different confidences:

**RDF-star 2021** — `<< s p o >>` denotes *the triple itself* (a content-addressed term, not a
freshly-minted node):
```turtle
<< :Aspirin :interactsWith :Warfarin >> :source :DrugBank  ; :confidence 0.92 .
<< :Aspirin :interactsWith :Warfarin >> :source :TextMiner ; :confidence 0.71 .
```
1. **Assertion ambiguous** — does this assert the base triple? Mode-dependent in 2021; engines diverged.
2. **Occurrence collapse** — both lines annotate the *same* term → `:source {DrugBank, TextMiner}`,
   `:confidence {0.92, 0.71}` merge; you can no longer tell which confidence came from which source.
   (Standard reification does **not** have this problem: `_:s1`/`_:s2` are distinct minted nodes.)

**RDF 1.2** — `<<( s p o )>>` is an *unasserted triple term*; a **reifier** identifies each occurrence:
```turtle
:Aspirin :interactsWith :Warfarin .                              # assert the fact (explicit)
_:occ1 rdf:reifies <<( :Aspirin :interactsWith :Warfarin )>> ;   # reifier for occurrence 1
       :source :DrugBank ; :confidence 0.92 .
_:occ2 rdf:reifies <<( :Aspirin :interactsWith :Warfarin )>> ;   # a distinct reifier, same term
       :source :TextMiner ; :confidence 0.71 .
```
Assertion is explicit and orthogonal (`{| … |}` asserts; `<<( )>>`/`rdf:reifies`/`~` do not), and
`_:occ1 ≠ _:occ2` restore occurrence identity.

| | `<<…>>` denotes | occurrence identity | assertion | compact |
|---|---|:--:|---|:--:|
| Standard reification | a minted statement node | ✅ | explicit (never asserts) | ✗ (3 triples) |
| RDF-star 2021 | the triple (content term) | ✗ (collapses) | ambiguous | ✅ |
| RDF 1.2 | triple term + reifier node | ✅ | explicit, orthogonal | ✅ |

RDF 1.2 = Standard's *node-per-occurrence* + RDF-star's *compact native syntax*.

## Forward-compatibility

Our provenance token is **exactly an RDF 1.2 reifier**: a per-occurrence identifier you attach
probability/source to. `SPARQL_Star`'s `<< s p o >> :occurrenceOf ?t` is the **pre-1.2** hand-rolled
form (`:occurrenceOf` ≈ `rdf:reifies`, `?t` ≈ the reifier). Native RDF 1.2 support is therefore one
additional `Reify` case (`?t rdf:reifies <<( s p o )>>`), with no change to the circuit layer.

**Recommendation for the paper:** run experiments on **RDF-star (2021)** where an RDF-star engine is
available (mature support; matches NPCS) and **Standard** everywhere else (portability; E10; paths);
**cite RDF 1.2** as the incoming standard our token maps onto, but do not run on it (CR-stage, engine
support nascent, no baseline uses it).
