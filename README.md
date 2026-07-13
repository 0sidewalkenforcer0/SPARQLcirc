# SPARQL_circ

**Native probabilistic query evaluation for SPARQL via compiled provenance circuits.**

SPARQL_circ rewrites a SPARQL query so that an **unmodified** SPARQL engine, when it
runs the rewritten query, materializes a single **shared, content-addressed provenance
circuit** — an RDF DAG of ⊕ (union), ⊗ (join), and ⊖ (difference/OPTIONAL/MINUS) gates
that represents the provenance of *all* answers at once. The circuit is then compiled
(OBDD / SDD / d-DNNF) and **weighted-model-counted** for *exact* probabilistic query
evaluation.

This contrasts with NPCS / SPARQLprov, which serialize each answer's provenance as a
**string** (shared subterms repeated, size growing with the number of derivations), and
with ProvSQL, which requires a **modified** database engine. Here the engine is stock;
the circuit is a normal RDF graph, so RDF set-semantics deduplicates shared gates
automatically.

The engine output is specifically a **Boolean event circuit for PQE**. Because identical RDF edges
are set-deduplicated, it does not preserve free-semiring coefficients (`x²`, `2x`); the Python
algebraic reference does preserve that multiplicity. This distinction is immaterial to exact Boolean
WMC, where repeated event operands are idempotent, and is documented in `TECHREPORT.md` §3.2.

**Scope.** ABox only (no TBox), currently over one default graph. `GRAPH`, `FROM`, and
`FROM NAMED` are rejected fail-fast because the reification layout does not encode graph context.
**Property paths** are supported via recursive provenance in the
absorptive semiring PosBool (arbitrary-length `+`/`*` and `/ | ^ ?`); reachability is a level-indexed
fixpoint whose gates stay an acyclic, polynomial DAG even on cyclic graphs (Python reference: all
operators; engine: `+`/`*` single predicate, **IRI frontier only** — blank-node/literal path nodes are
not yet supported — on any writable SPARQL 1.1 endpoint; see `TECHREPORT.md` §4.6). Negated
property sets `!(...)` are out of scope. Non-monotone support is built on one ⊖ (monus / anti-join) primitive, **DIFF**:
`OPTIONAL(P1,P2) = (P1 AND P2) ∪ (P1 DIFF P2)`, and user-level `MINUS(P1,P2) = P1 DIFF P2`
when the operands share a variable, else a no-op (W3C MINUS's domain-intersection guard).
MINUS operands may be **BGPs, UNIONs, or OPTIONALs**, on either side, nested, and **chained**
(`A MINUS P MINUS Q`): a `normalize()` pass reduces each composite operand algebraically to the
verified BGP plan — `(A∪B) MINUS P → (A MINUS P)∪(B MINUS P)`; a UNION right operand → per-branch
subtrahends; `(A OPT B) MINUS P → (Join(A,B) MINUS P) ∪ ((A DIFF B) MINUS P)`; `P MINUS (C OPT D) →
P MINUS C`; `(A MINUS P) MINUS Q → A MINUS (P∪Q)`. Verified by `reference/verify_gallery.py`
(`minus`, `minus_disjoint`, `minus_union`, `minus_p2union`, `minus_chain`, `opt_left`,
`opt_right`, `distinct`) as `circuit WMC == possible-world enumeration`, the composite cases
checked against rdflib's own W3C evaluation. **Safely rejected** (loud error, never mis-answered;
the string rewriter handles them): **right-nested MINUS** `A MINUS (P MINUS Q)` (introduces a
join), a cross-product OPTIONAL **as a MINUS operand** (`(A OPT B) MINUS P` with `A`,`B` sharing no
variable — a *bare* cross-product OPTIONAL is fully supported, via the unguarded DIFF in `optionalPlan`),
and a MINUS operand sharing an OPTIONAL's *inner* variable. Solution-sequence modifiers **LIMIT/OFFSET/ORDER BY are rejected**; **DISTINCT is an
implicit no-op** (answer gates are already a set).

## Repository layout

```
sparqlcirc/
├── engine/       Java: the γ rewriter (SPARQL → CONSTRUCT that builds the circuit)
│   ├── src/                 npcs.rewrite (NPCS-style string rewriter) +
│   │                        npcs.circuit (CircuitRewriter → CONSTRUCT plan, CircuitRun)
│   ├── examples/            runnable BGP / UNION / OPTIONAL / MINUS examples
│   └── verify/              correctness oracle + consistency diff vs. the original
├── reference/    Python: reference circuit, compilers, WMC, and all evaluation
│   ├── gates.py             collision-resistant content-addressed circuit constructors
│   ├── gamma.py             client-side circuit builder (bgp/union/join/optional/minus)
│   ├── factor.py            factored construction (variable elimination)
│   ├── compile_bdd.py       self-contained ROBDD + WMC (zero-dependency)
│   ├── compile_sdd.py       SDD via PySDD (optional)
│   ├── wmc.py               exact WMC + possible-world-enumeration oracle
│   ├── bench*.py            evaluation: compactness, deployed-engine timings
│   └── watdiv_*.py          real-KG WatDiv run (flat vs. factored)
├── LICENSE       Apache-2.0
└── NOTICE        attribution (clean-room reimplementation of NPCS) + dependencies
```

## Quick start

**Build the rewriter** (Java 11+, Maven; runtime depends on Eclipse RDF4J + SLF4J,
with JUnit used only for tests):

```bash
cd engine
mvn -q package            # -> target/npcs-rewrite.jar
./examples/run_examples.sh
```

**Build a circuit on the stock in-memory engine** (drug running example):

```bash
cd engine
java -jar target/npcs-rewrite.jar circuit \
     Standard examples/circuit/drug.reified.ttl examples/circuit/drug3hop.sparql \
     2>plan.txt >circuit.nt   # -> 25-triple circuit (19 core gates + 6 c:binding recovery; paper Fig. 2)
# plan.txt = the CONSTRUCT plan;  circuit.nt = the materialized circuit (N-Triples)
```

**Compile + weighted model count the circuit generated immediately above** (Python 3.9+,
standard library only):

```bash
# continuing from engine/
cd ../reference
python3 pqe.py --circuit ../engine/circuit.nt \
  --probabilities data/drug.probabilities.json
python3 tests.py          # independent 171-case reference-semantics battery
```

Or run the complete local construction → ROBDD → answer-probability path through one
command (after `mvn package`):

```bash
# from reference/
python3 pqe.py --jar ../engine/target/npcs-rewrite.jar \
  --data data/drug.reified.ttl --query queries/drug3hop.sparql \
  --probabilities data/drug.probabilities.json
```

To evaluate an existing circuit, replace the `--jar/--data/--query` options with
`--circuit data/drug.circuit.nt`. Output is JSON with term-aware RDF bindings,
probabilities, and compiled BDD sizes.

For the complete clean-build regression used by CI, run `python3 reference/quick_verify.py`
from the repository root after packaging the JAR.

See `engine/README.md` and `reference/README.md` for details.

## From circuit back to answers (post-processing)

The rewritten query is a **CONSTRUCT**, so the engine returns an RDF graph rather than a
result table — but the SELECT bindings are not lost. Each answer's root ⊕ (`c:Plus`) gate
carries its projected binding as **structured RDF** that preserves the exact RDF term
(IRI vs literal, datatype, language tag, bound/unbound):

```
<urn:g:a:…> a c:Plus ;
    c:binding [ c:var "y" ; c:val <…#Bob>  ] ,
              [ c:var "c" ; c:val <…#Rome> ] .            # ?y=Bob,   ?c=Rome
<urn:g:a:…> a c:Plus ;
    c:binding [ c:var "y" ; c:val <…#Carol> ] ,
              [ c:var "c" ] .                             # ?y=Carol, ?c UNBOUND (no c:val)
```

The gate IRI is a **collision-resistant, term-type-aware identity hash** (`SHA256` of a
kind-tagged, per-part-hashed serialization of the binding), so two answers that differ only by
term type — an IRI `<…#foo>` vs a literal `"…#foo"`, `"1"^^xsd:integer` vs `"1"^^xsd:string`,
`@en` vs `@fr`, or bound vs unbound — get **distinct** gates. The client step is:

1. find every gate with `c:binding` (equivalently, a `c:answer` label) → the answer roots;
2. for each, read its `c:binding` nodes → `c:var` (the variable) and `c:val` (the RDF term);
   a binding with **no `c:val` means that variable is unbound** (e.g. an unmatched OPTIONAL);
3. compile the sub-circuit rooted there and weighted-model-count it → that row's probability.

This reconstructs the ordinary SELECT table plus a probability column — the CONSTRUCT output is a
**superset** of the SELECT output. Each gate *also* carries a readable
**`c:answer "A|var=value|…"`** literal, but that is a **display/debug label only**: it is built
from `STR()` and is **not** injective (an IRI and a same-lexical literal render identically), so
**do not use `c:answer` to identify or de-duplicate answers** — use the gate IRI / `c:binding`.
`reference/verify_gallery.py` and `reference/verify_answer_keys.py` do exactly this.

## Reproducing the evaluation

| what | command (in `reference/`) |
|---|---|
| Compactness: shared circuit vs. per-answer strings | `python3 bench.py` |
| Deployed-engine timings (needs a running GraphDB) | `python3 bench_engine.py` |
| Real-KG WatDiv, flat vs. factored construction | `python3 watdiv_factor.py` (set `WATDIV_NT`) |
| d4 d-DNNF compilation (Linux/x86) | see `reference/D4_ON_LINUX.md` |

The WatDiv data (http://dsg.uwaterloo.ca/watdiv/) and GraphDB
(https://graphdb.ontotext.com/) are obtained separately — see `NOTICE`.

Step-by-step instructions, expected outputs, and data acquisition are in
**`REPRODUCE.md`**.

## Citing / attribution

The rewriter in `engine/` is an independent **clean-room reimplementation** of the
query rewriting from **NPCS** (Asma et al., *NPCS: Native Provenance Computation for
SPARQL*, WWW 2024); it is written from the published algorithm and does not include or
derive from the original NPCS source. See `NOTICE` for the full citation and the list
of third-party dependencies.

## License

Apache License 2.0 — see `LICENSE`.
