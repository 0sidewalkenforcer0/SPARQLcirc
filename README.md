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

**Scope.** ABox only (no TBox); property paths are out of scope (deferred to follow-up
work). Non-monotone support is built on one ⊖ (monus / anti-join) primitive, **DIFF**:
`OPTIONAL(P1,P2) = (P1 AND P2) ∪ (P1 DIFF P2)`, and user-level `MINUS(P1,P2) = P1 DIFF P2`
when the operands share a variable, else a no-op (W3C MINUS's domain-intersection guard).
MINUS operands may be **BGPs, UNIONs, or OPTIONALs**, on either side, nested, and **chained**
(`A MINUS P MINUS Q`): a `normalize()` pass reduces each composite operand algebraically to the
verified BGP plan — `(A∪B) MINUS P → (A MINUS P)∪(B MINUS P)`; a UNION right operand → per-branch
subtrahends; `(A OPT B) MINUS P → (Join(A,B) MINUS P) ∪ (A MINUS (B∪P))`; `P MINUS (C OPT D) →
P MINUS C`; `(A MINUS P) MINUS Q → A MINUS (P∪Q)`. Verified by `reference/verify_gallery.py`
(`minus`, `minus_disjoint`, `minus_union`, `minus_p2union`, `minus_chain`, `opt_left`,
`opt_right`, `distinct`) as `circuit WMC == possible-world enumeration`, the composite cases
checked against rdflib's own W3C evaluation. **Safely rejected** (loud error, never mis-answered;
the string rewriter handles them): **right-nested MINUS** `A MINUS (P MINUS Q)` (introduces a
join), a cross-product OPTIONAL operand, and a MINUS operand sharing an OPTIONAL's *inner*
variable. Solution-sequence modifiers **LIMIT/OFFSET/ORDER BY are rejected**; **DISTINCT is an
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
│   ├── gates.py             collision-free content-addressed circuit constructors
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

**Build the rewriter** (Java 11+, Maven; depends only on Eclipse RDF4J + SLF4J):

```bash
cd engine
mvn -q package            # -> target/npcs-rewrite.jar
./examples/run_examples.sh
```

**Build a circuit on the stock in-memory engine** (drug running example):

```bash
cd engine
java -cp target/npcs-rewrite.jar npcs.circuit.CircuitRun \
     Standard examples/circuit/drug.reified.ttl examples/circuit/drug3hop.sparql \
     2>plan.txt >circuit.nt   # -> 19-triple shared circuit (paper Fig. 2)
# plan.txt = the CONSTRUCT plan;  circuit.nt = the materialized circuit (N-Triples)
```

**Compile + weighted model count** (Python 3.9+, standard library only for the
zero-dependency path):

```bash
cd reference
python3 wmc.py            # exact WMC vs. possible-world enumeration on the examples
python3 verify_all.py     # end-to-end correctness across all example circuits
```

See `engine/README.md` and `reference/README.md` for details.

## From circuit back to answers (post-processing)

The rewritten query is a **CONSTRUCT**, so the engine returns an RDF graph rather than a
result table — but the SELECT bindings are not lost. Each answer's root ⊕ (`c:Plus`) gate
carries a `c:answer` literal that encodes the projected variables:

```
<urn:g:a:…> a c:Plus ; c:answer "A|y=…Bob|c=…Rome" .    # ?y=Bob, ?c=Rome
<urn:g:a:…> a c:Plus ; c:answer "A|y=…Carol|c=NULL" .   # ?y=Carol, ?c unbound
```

Format `A|<var>=<value>|…` (unbound OPTIONALs → `NULL`); the gate IRI is `hash(answer_key)`,
so the key is injective on the binding — two answers never collide. The client step is:

1. find every gate with a `c:answer` property → the answer roots;
2. parse the literal → the SELECT binding for that row;
3. compile the sub-circuit rooted there and weighted-model-count it → that row's probability.

This reconstructs the ordinary SELECT table plus a probability column — the CONSTRUCT output
is a **superset** of the SELECT output (bindings in `c:answer` + the provenance circuit).
`reference/watdiv_run.py` and `reference/verify_gallery.py` do exactly this.

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
