# SPARQL_circ engine and NPCS baseline

This module contains two modes. `npcs.circuit` is SPARQL_circ's main contribution:
it makes a stock SPARQL engine materialize a shared RDF event circuit for exact PQE.
`npcs.rewrite` is a clean-room Java reimplementation of the **NPCS** provenance-string
baseline (Asma et al., *NPCS: Native Provenance Computation for SPARQL*, WWW'24),
following that paper's **Definitions 4.1 / 4.2 / 4.6**.

The baseline rewrites a SELECT query so that an extra projected variable
(`?finalprovennacevariable`) carries the spm-semiring how-provenance polynomial
of each answer, computed natively by the SPARQL endpoint via `BIND` /
`GROUP_CONCAT`.

Built on the **same RDF4J version (4.2.1)** as the original NPCS project. The
NPCS differential harness compares query structure after capture-avoiding gensym
normalization; generated variable spellings are intentionally not an API.

## Build

```
mvn -q package
# -> target/npcs-rewrite.jar  (self-contained fat jar, Main-Class npcs.App dispatcher)
```

## Run

```
# Main contribution: construct an RDF event circuit on in-memory RDF4J.
java -jar target/npcs-rewrite.jar circuit \
  Standard ../reference/data/drug.reified.ttl ../reference/queries/drug3hop.sparql

# NPCS-compatible provenance-string baseline.
java -jar target/npcs-rewrite.jar rewrite <Standard|SPARQL_Star> query "<sparql text>"
java -jar target/npcs-rewrite.jar rewrite <Standard|SPARQL_Star> path path/to/query.sparql

# The historical three-argument baseline form remains accepted.
java -jar target/npcs-rewrite.jar <Standard|SPARQL_Star> query "<sparql text>"
java -jar target/npcs-rewrite.jar <Standard|SPARQL_Star> path  path/to/query.sparql
```

### Circuit construction modes

`circuit` defaults to the production `factored` mode. For a pure BGP it runs a
deterministic min-scope variable-elimination plan as several standard SPARQL 1.1
`CONSTRUCT` passes. Only private, per-invocation `urn:sc:*` message rows are fed
back to the endpoint; they are removed after the plan, while the emitted circuit
gate identities stay deterministic and independent of the session.

```
# Production/default: engine-native factored BGP construction.
java -jar target/npcs-rewrite.jar circuit --construction=factored \
  Standard data.ttl query.rq [endpoint]

# Ablation and read-only endpoint route: one product per full derivation.
java -jar target/npcs-rewrite.jar circuit --construction=flat \
  Standard data.ttl query.rq [endpoint]
```

Factored BGP construction needs a writable endpoint for the private intermediate
messages and fails fast when `CIRCUIT_READONLY=1`. `UNION`, `OPTIONAL`, and
`MINUS` currently use the established flat operator plan even when factored mode
was requested; this fallback is printed explicitly. Property paths retain their
separate iterative construction protocol.

Conceptual NPCS output (generated variables are shortened here for readability):

```
SELECT ?v0 ?v1 (CONCAT("⊕(", GROUP_CONCAT(?fjoin0), ")") AS ?finalprovennacevariable)
WHERE {
    ?fprov0 <http://www.w3.org/1999/02/22-rdf-syntax-ns#subject>   ?v0 .
    ?fprov0 <http://www.w3.org/1999/02/22-rdf-syntax-ns#predicate> <http://purl.org/goodrelations/includes> .
    ?fprov0 <http://www.w3.org/1999/02/22-rdf-syntax-ns#object>    ?v1 .
    BIND (CONCAT("(⊗", STR(?fprov0), ",", ")") AS ?fjoin0) .
}
GROUP BY ?v0 ?v1
```

## The rewriting (Def 4.1 / 4.2 / 4.6)

The spm-semiring operators are emitted as `CONCAT(...)` string expressions —
the provenance is **not** computed in Java, the endpoint evaluates it:

| Operator | Symbol | SPARQL algebra | Code |
|---|---|---|---|
| product (join)      | `(⊗ ...)` | `Join`        | `Prov.prod`   |
| sum (union)         | `(⊕ ...)` | `Union`       | `Prov.sum`    |
| monus (diff/minus)  | `(⊖ ...)` | `Difference`  | `Prov.diff`   |
| aggregate-sum (group / duplicate answers) | `⊕( ... )` | `GROUP BY` | `Prov.aggSum` |

Each triple pattern `s p o` is bound to a fresh statement id `?fprovN` via the
selected **reification scheme** (leaf `Reify`):

- **Standard** — `?fprovN rdf:subject s ; rdf:predicate p ; rdf:object o .`
- **SPARQL_Star** — `<< s p o >> <http://example.org/occurrenceOf> ?fprovN .`

For a basic graph pattern this yields the optimized single-`GROUP BY` form of
Definition 4.6: one `BIND(ProvProd(?fprov0,…,?fprovn) AS ?fjoin0)` and an outer
`(ProvAggSum(?fjoin0) AS ?finalprovennacevariable)` grouped by the projected
variables.

## Consistency with the original NPCS

`verify/diff_harness.py` runs both this JAR and the original
`ReifySparqlByte.jar` over the WatDiv query set and compares the rewritten
queries **up to semantic equivalence** — i.e. after (a) renaming provenance
gensyms (`?fprovN`, `?fjoinN`, …) by order of first appearance and (b) sorting
the arguments of the commutative operators ⊗ and ⊕ (the non-commutative monus ⊖
is left intact). This is the correct notion of consistency: `fprov`/`fjoin` are
fresh variable names and ⊗/⊕ are commutative, so these differences are cosmetic.

```
python3 verify/diff_harness.py                 # both schemes
python3 verify/diff_harness.py Standard         # one scheme
```

### Latest result

```
WatDiv Basic: 189 files, 139 pure-BGP
scheme=Standard    : MATCH 138/139, mismatch 0, error 1
scheme=SPARQL_Star : MATCH 138/139, mismatch 0, error 1
```

All 139 real BGP queries are semantically identical to the original NPCS output
for both schemes. The single "error" is `L3/10.sparql`, which is an **empty
file** that both this JAR and the original reject identically.

## NPCS string-baseline scope / status

- **BGP (conjunctive):** implemented and verified **semantically identical to the
  original NPCS** — 139/139 WatDiv Basic BGP queries, both schemes (see above).
- **UNION / OPTIONAL / MINUS:** implemented **per the paper** (Def 4.2 rules 4/5
  and `P1 OPTIONAL P2 ≡ (P1 AND P2) UNION (P1 DIFF P2)`). Verified two ways:
  - *Well-formedness:* all 50 WatDiv OPTIONAL queries rewrite to valid SPARQL
    for both schemes (`verify/validate_optional.py` → 50/50 PARSE_OK).
  - *Execution equivalence:* `npcs.ExecCheck` loads a reified toy dataset into an
    in-memory store, runs the rewrites, and the emitted provenance matches the
    hand-computed spm-semiring expressions (AND ⊗, sum ⊕, monus ⊖).

    ```
    java -cp target/npcs-rewrite.jar npcs.ExecCheck
    ```
  - *Independent semantic oracle* (`verify/correctness_oracle.py`): for a range
    of queries (AND / UNION / OPTIONAL / MINUS, chain joins, multi-pattern
    branches, MINUS-with-UNION subtrahend, parallel-edge token sharing) it
    compares, per answer, the ground-truth answer probability from
    **possible-world enumeration** (plain SPARQL over every token subset) against
    the probability obtained by weighted-model-counting the rewriting's own
    provenance under its Boolean abstraction (⊗→∧, ⊕→∨, ⊖(a,b)→a∧¬b). Latest:
    **120/120** checks pass (9 queries × 2 schemes × 3 probability assignments).

    Note: for OPTIONAL/UNION the original NPCS *diverges from the paper* (fragile
    traversal; plain UNION is partly-unreified/unaggregated), so consistency here
    is established by paper-correctness + execution, not by string-diffing the
    original.
- **Not implemented (rejected with a clear error, never silently wrong):**
  `FILTER`, `BIND`, `VALUES`, sub-SELECT, and OPTIONAL/UNION whose join branch
  contains a nested non-BGP (e.g. nested OPTIONAL, UNION-inside-OPTIONAL). None
  appear in the WatDiv Standard/SPARQL-star query set. (MINUS/OPTIONAL/UNION with
  BGP operands, and MINUS with a UNION subtrahend, *are* supported and verified.)
- **Default graph only:** `GRAPH`, `FROM`, and `FROM NAMED` are rejected rather than
  silently losing their graph/dataset semantics. The circuit rewriter has the same guard.
- **Solution modifiers dropped by the string baseline:** `DISTINCT`, `ORDER BY`, `LIMIT`/`OFFSET` on the
  input query are ignored (the provenance GROUP BY already deduplicates; ordering
  a provenance-annotated relation is out of scope). The circuit route instead rejects
  `ORDER BY` and `LIMIT/OFFSET`; `DISTINCT` is an implicit no-op.

## Layout

```
src/main/java/npcs/
├── App.java            fat-JAR dispatcher: circuit / rewrite / legacy baseline form
├── ExecCheck.java      in-memory execution-equivalence check
├── circuit/            CircuitRewriter plans + CircuitRun execution entry
└── rewrite/
    ├── Prov.java       Def 4.1 operators: ⊕( , (⊗ , (⊕ , (⊖
    ├── Terms.java      RDF term/IRI rendering (prefix expansion)
    ├── Reification.java  Standard + SPARQL-star leaf Reify
    └── NpcsRewriter.java recursive β (Def 4.2/4.6)
verify/
├── diff_harness.py       BGP: semantic diff vs original ReifySparqlByte.jar
├── validate_optional.py  OPTIONAL: well-formedness of rewritten output
├── results.txt           latest BGP diff result
└── optional_results.txt  latest OPTIONAL validation result
```
