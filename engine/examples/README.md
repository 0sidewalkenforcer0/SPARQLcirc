# Paper example data + monotonic / non-monotonic queries

This directory reproduces the running example of the NPCS paper
(Asma et al., *NPCS: Native Provenance Computation for SPARQL*, WWW'24,
**Example 3.2**) and exercises NPCS rewriting on **monotonic** and
**non-monotonic** SPARQL queries.

## Data

The paper's example (verbatim):

```
(Alice, likes,   pasta)   with sources u1, u2     ->  (u1 ⊕ u2)
(Alice, livesIn, Italy)   with source  u3
```

To make UNION / MINUS / OPTIONAL return non-trivial answers, two more
individuals are added: **Bob** likes pasta (u4) but has no known country, and
**Carol** lives in Italy (u5) but has no known food.

The data is provided in the current mixed layout under both token encodings:

| File | Encoding | Statement → token link |
|---|---|---|
| `data/example.standard.ttl` | asserted triples + RDF standard reification | `s p o` and `uK rdf:subject/predicate/object …` |
| `data/example.star.ttls`    | asserted triples + SPARQL-star / RDF-star | `s p o` and `<< s p o >> <http://example.org/occurrenceOf> uK` |

The `uK` statement nodes are the provenance tokens (`?fprov` binds to them).
Use the `_Pure` Java scheme aliases only with older fixtures that omit the
asserted triples.

## Queries

Monotonic queries use only join (⊗) and union (⊕); **non-monotonic** queries add
difference/optional, which need the **monus** (⊖) — the operator that motivates
NPCS's spm-semiring (a plain commutative semiring cannot express it).

| File | Kind | Operators |
|---|---|---|
| `queries/monotonic/and.sparql`        | monotonic     | AND (⊗)          |
| `queries/monotonic/union.sparql`      | monotonic     | UNION (⊕)        |
| `queries/nonmonotonic/minus.sparql`   | non-monotonic | MINUS (⊖)        |
| `queries/nonmonotonic/optional.sparql`| non-monotonic | OPTIONAL (⊕ of AND and ⊖) |

## Run

```
mvn -q package          # from the project root, builds target/npcs-rewrite.jar
examples/run_examples.sh
```

or a single case:

```
java -cp target/npcs-rewrite.jar npcs.RunExample \
     Standard examples/data/example.standard.ttl examples/queries/monotonic/and.sparql
```

`RunExample` prints the rewritten query, then each answer with its provenance
polynomial, evaluated in an in-memory store.

## Expected provenance (both schemes agree)

`⊗` = product, `⊕` = sum, `⊖` = monus. (The output is the raw `CONCAT` encoding;
`⊕((⊗u1,u3)(⊗u2,u3))` reads as `(u1⊗u3) ⊕ (u2⊗u3)` = `(u1⊕u2)⊗u3`.)

```
monotonic/and       Alice                 -> (u1⊕u2)⊗u3            ← the paper's Example 3.2 answer
monotonic/union     Alice                 -> (u1⊕u2) ⊕ u3
                    Bob                   -> u4
                    Carol                 -> u5
nonmonotonic/minus  Bob                   -> u4 ⊖ ∅   = u4         (kept: not in Italy)
                    Alice                 -> (u1⊕u2) ⊖ u3          (monus non-empty → 0: excluded)
nonmonotonic/opt.   Alice, country=Italy  -> (u1⊕u2)⊗u3            (P2 matched)
                    Bob,   country=∅      -> u4 ⊖ ∅  = u4          (P2 unbound)
                    Alice, country=∅      -> (u1⊕u2) ⊖ u3          (monus non-empty → 0)
```

The monus rows that "evaluate to 0" are produced by the rewriting as provenance
expressions; a downstream spm-semiring evaluator drops them. This is exactly the
paper's `OPTIONAL(P1,P2) = (P1 AND P2) ⊕ (P1 DIFF P2)` semantics.
