# SPARQLcirc: Exact Probabilistic SPARQL via Provenance Circuits Built by Unmodified Engines

[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
![Java](https://img.shields.io/badge/Java-11%2B-orange.svg)
![Maven](https://img.shields.io/badge/Maven-3.6%2B-blue.svg)
![Python](https://img.shields.io/badge/Python-3.9%2B-blue.svg)
![SPARQL](https://img.shields.io/badge/SPARQL-1.1-green.svg)
![Eclipse RDF4J](https://img.shields.io/badge/Eclipse%20RDF4J-4.2.1-red.svg)
![Status](https://img.shields.io/badge/status-research%20prototype-yellow.svg)

**Native probabilistic query evaluation for SPARQL via compiled provenance circuits**

SPARQLcirc rewrites a SPARQL query so that an **unmodified** SPARQL engine, when it runs the
rewritten query, materializes a single **shared, content-addressed provenance circuit**: an RDF DAG
of ⊕ (union), ⊗ (join), and ⊖ (difference / OPTIONAL / MINUS) gates that represents the provenance
of *all* answers at once. The circuit is then compiled (OBDD / SDD / d-DNNF) and
**weighted-model-counted** for *exact* probabilistic query evaluation. This contrasts with NPCS /
SPARQLprov, which serialize each answer's provenance as a **string** (shared subterms repeated, size
growing with the number of derivations), and with ProvSQL, which requires a **modified** database
engine. Here the engine is stock, and the circuit is a normal RDF graph, so RDF set semantics
deduplicates shared gates automatically.

---

## Table of Contents

- [Requirements](#requirements)
- [Health Check (Recommended)](#health-check-recommended)
- [Installation](#installation)
- [Quick Verification](#quick-verification)
- [Running Against a Deployed SPARQL Endpoint](#running-against-a-deployed-sparql-endpoint)
- [Usage Examples](#usage-examples)
- [Available Commands](#available-commands)
- [Supported SPARQL Fragment](#supported-sparql-fragment)
- [Probabilistic Data Formats](#probabilistic-data-formats)
- [From Circuit Back to Answers](#from-circuit-back-to-answers)
- [Benchmark Reproduction](#benchmark-reproduction)
- [Project Structure](#project-structure)
- [Troubleshooting](#troubleshooting)
- [Citing and Attribution](#citing-and-attribution)
- [License](#license)

---

## Requirements

| Requirement | Version | Check Command |
|-------------|---------|---------------|
| Java (JDK)  | **11 or higher** | `java -version` |
| Maven       | **3.6 or higher** | `mvn -version` |
| Python (core: circuit, ROBDD oracle, WMC, benchmarks) | **3.9 or higher**, standard library only | `python3 --version` |
| Python (production CUDD compile and WMC) | **3.11 or higher** | `python3 --version` |

### Optional Components

| Component | Needed for | How to obtain |
|-----------|------------|---------------|
| CUDD (`dd==0.6.0`) | production BDD compilation and batch WMC | `pip install -r reference/requirements-production.txt` |
| PySDD | SDD compilation baseline | `pip install pysdd` |
| d4 | d-DNNF compilation baseline (Linux/x86 only) | see [reference/D4_ON_LINUX.md](reference/D4_ON_LINUX.md) |
| GraphDB 10.x | deployed-engine timings and real-KG runs | https://graphdb.ontotext.com/ (free edition, JDK 11+, port 7200) |
| WatDiv data | real-KG scaling experiments | http://dsg.uwaterloo.ca/watdiv/ (not bundled, see [NOTICE](NOTICE)) |

Any SPARQL 1.1 endpoint that accepts `CONSTRUCT` works in place of GraphDB. The circuit
construction uses no engine-specific features.

### Installing Java 11 (if needed)

**macOS (Homebrew):**
```bash
brew install --cask temurin@11
```

**Ubuntu/Debian:**
```bash
sudo apt install openjdk-11-jdk
```

**Windows:**
Download from [Adoptium](https://adoptium.net/temurin/releases/?version=11)

---

## Health Check (Recommended)

After building the JAR, run the one-command smoke verifier:

```bash
mvn -q -f engine/pom.xml package        # -> engine/target/npcs-rewrite.jar
python3 reference/quick_verify.py       # expect: QUICK VERIFY ALL OK
```

What it checks:

- the standard-library reference correctness battery and the WMC self-test
- an offline `pqe.py` CLI regression
- a live `CircuitRun` invocation, consuming the N-Triples emitted by *that* invocation
- structured answer-binding parsing, then compilation of each fresh answer circuit
- WMC against possible-world enumeration on the freshly built circuit
- the documented `pqe.py --jar ...` path end to end, covering the fat-JAR dispatcher

It deliberately does **not** read the checked-in `reference/data/*.circuit.nt` fixtures, so a pass
means the current Java sources really do produce correct circuits. Both `reference/tests.py` and
`reference/wmc.py` exit non-zero on a mismatch, so the same commands are safe CI gates.

---

## Installation

### Step 1: Clone the Repository

```bash
git clone <repository-url>
cd SPARQLcirc
```

### Step 2: Build the Rewriter

```bash
mvn -q -f engine/pom.xml package
```

This produces `engine/target/npcs-rewrite.jar`, a self-contained fat JAR whose `Main-Class` is the
`npcs.App` dispatcher. Runtime dependencies are Eclipse RDF4J 4.2.1 and SLF4J, fetched from Maven
Central; JUnit is used only for tests.

**Expected output:**
```
[INFO] BUILD SUCCESS
```

> ⏱️ **Note:** the first build downloads the RDF4J dependency tree and takes a few minutes.

### Step 3: Install the Production Compiler (optional)

The core Python reference runs on the standard library alone. For production BDD compilation and
batch WMC, install the native CUDD wheel on Python 3.11+:

```bash
python3 -m pip install -r reference/requirements-production.txt
```

Optional extra baselines (SDD) are listed in `reference/requirements-optional.txt`.

---

## Quick Verification

### Reference Correctness Battery

```bash
python3 reference/tests.py       # 171/171 answer-probability checks vs possible-world enumeration
python3 reference/wmc.py         # standalone WMC self-test
```

### Full Fresh-Circuit Smoke Test

```bash
python3 reference/quick_verify.py
```

### Java Unit Tests

```bash
mvn -f engine/pom.xml test
```

### Non-Monotone and Gallery Verification

```bash
cd reference
python3 verify_nonmono.py        # OPTIONAL / MINUS via the single ⊖ primitive, vs enumeration
python3 verify_gallery.py        # circuit WMC == possible-world enumeration on the query gallery
python3 verify_answer_keys.py    # term-aware answer-key injectivity
```

`verify_gallery.py` checks the composite MINUS and OPTIONAL cases against rdflib's own W3C
evaluation, so the algebraic normalization is validated against a third-party implementation, not
only against itself.

---

## Running Against a Deployed SPARQL Endpoint

The whole point of the approach is that the *engine* builds the circuit. To exercise that path, point
the CLI at a live endpoint instead of the bundled in-memory RDF4J store.

### 1. Start the Store and Load Reified Data

Start GraphDB (or any SPARQL 1.1 endpoint) and create a repository, then load a **reified** dataset:

```bash
cd reference
python3 watdiv/reify.py /path/to/base.nt watdiv/base.reified.nt
# create a repo named "watdiv" (see watdiv/repo.ttl for the repository config),
# then load watdiv/base.reified.nt into it
```

**Endpoint URLs (GraphDB defaults):**
| Endpoint | URL |
|----------|-----|
| SPARQL Query | `http://localhost:7200/repositories/watdiv` |
| SPARQL Update | `http://localhost:7200/repositories/watdiv/statements` |
| Workbench UI | `http://localhost:7200/` |

### 2. Build a Circuit on the Deployed Engine

```bash
java -jar engine/target/npcs-rewrite.jar circuit \
     Standard data.ttl query.rq http://localhost:7200/repositories/watdiv > circuit.nt
```

### 3. Evaluate It

```bash
cd reference
python3 pqe.py --circuit ../circuit.nt --probabilities data/drug.probabilities.json
```

`pqe.py` also accepts `--endpoint` directly, so a single command can construct on the remote engine
and then compile and count locally.

> **Note:** the default `factored` construction mode needs a **writable** endpoint for its private,
> per-invocation `urn:sc:*` message rows (removed after the plan completes; gate identities stay
> deterministic and session-independent). On a read-only endpoint use `--construction=flat`.

---

## Usage Examples

### Build a Circuit Locally (Java CLI)

The drug running example, on the stock in-memory engine:

```bash
cd engine
java -jar target/npcs-rewrite.jar circuit \
     Standard examples/circuit/drug.reified.ttl examples/circuit/drug3hop.sparql \
     2>plan.txt >circuit.nt
# circuit.nt = the materialized circuit (N-Triples): 25 triples
#              (19 core gates + 6 binding-recovery triples; paper Fig. 2)
# plan.txt   = the CONSTRUCT plan that produced it
```

### Compile and Weighted-Model-Count It (Python CLI)

```bash
cd ../reference
python3 pqe.py --circuit ../engine/circuit.nt \
  --probabilities data/drug.probabilities.json
```

### One-Command End-to-End Path

Construction → ROBDD → answer probabilities, in a single invocation:

```bash
cd reference
python3 pqe.py --jar ../engine/target/npcs-rewrite.jar \
  --data data/drug.reified.ttl --query queries/drug3hop.sparql \
  --probabilities data/drug.probabilities.json
```

Output is JSON with term-aware RDF bindings, probabilities, and compiled BDD sizes.

### Run the Bundled Example Scripts

```bash
cd engine
./examples/run_examples.sh       # runnable BGP / UNION / OPTIONAL / MINUS examples
```

### Example Query Gallery

`engine/examples/gallery/` holds the queries used by `reference/verify_gallery.py`:

| File | Description |
|------|-------------|
| `join.sparql`, `atom.sparql` | plain BGP baselines |
| `optional.sparql`, `opt_left.sparql`, `opt_right.sparql` | OPTIONAL through the ⊖ primitive |
| `opt_xprod.sparql`, `opt_disjoint.sparql` | cross-product and disjoint OPTIONAL |
| `minus.sparql`, `minus_disjoint.sparql` | MINUS, including the W3C domain-intersection no-op |
| `minus_union.sparql`, `minus_p2union.sparql`, `minus_chain.sparql` | composite and chained MINUS operands |
| `minus_rnested.sparql` | right-nested MINUS, the fail-fast rejection case |
| `pathalt.sparql`, `pathcompound.ttl` | property-path operators |
| `filter.sparql`, `filter_optional.sparql`, `filter_minus.sparql` | FILTER in a BGP, an OPTIONAL operand, and a MINUS subtrahend |
| `distinct.sparql`, `limit.sparql`, `filter_exists_unsupported.sparql` | modifier handling and rejections |

---

## Available Commands

### Engine CLI (`engine/target/npcs-rewrite.jar`)

| Command | Description |
|---------|-------------|
| `circuit <Standard\|SPARQL_Star> <data.ttl> <query.rq> [endpoint]` | **Main contribution.** Make the engine materialize the shared RDF event circuit. Defaults to `--construction=factored` |
| `circuit --construction=factored ...` | Production default: deterministic min-scope variable-elimination plan, run as several standard SPARQL 1.1 `CONSTRUCT` passes. Needs a writable endpoint |
| `circuit --construction=flat ...` | Ablation and read-only-endpoint route: one product per full derivation |
| `rewrite <Standard\|SPARQL_Star> query "<sparql text>"` | NPCS-compatible provenance-**string** baseline (clean-room reimplementation) |
| `rewrite <Standard\|SPARQL_Star> path <query.sparql>` | Same baseline, reading the query from a file |
| `<Standard\|SPARQL_Star> query\|path <arg>` | Historical three-argument baseline form, still accepted |

`Standard` uses RDF reification; `SPARQL_Star` uses quoted triples. See
[REIFICATION.md](REIFICATION.md) for the trade-off.

### `pqe.py` Options

| Option | Description |
|--------|-------------|
| `--circuit CIRCUIT` | evaluate an existing N-Triples circuit |
| `--jar JAR` | build the circuit first, using the packaged engine JAR (mutually exclusive with `--circuit`) |
| `--data DATA` | reified Turtle input (required with `--jar`) |
| `--query QUERY` | SELECT query file (required with `--jar`) |
| `--probabilities FILE` | **required.** JSON object mapping complete token IRIs to probabilities in [0, 1] |
| `--scheme {Standard,SPARQL_Star}` | reification scheme |
| `--endpoint ENDPOINT` | optional remote SPARQL query endpoint |
| `--compile-mode {shared,per-root}` | one shared CUDD manager (default) or one manager per answer root |
| `--oracle` | testing only: use the bundled pure-Python ROBDD instead of production CUDD |

### Python Reference Modules

| Module | Role |
|--------|------|
| `reference/gates.py` | collision-resistant content-addressed circuit constructors (`leaf/times/plus/minus`) |
| `reference/gamma.py` | client-side circuit builder (`bgp / union / join / optional / minus`, plus `project`) |
| `reference/factor.py`, `factor_native.py` | factored construction by variable elimination |
| `reference/compiler.py` | production CUDD batch compiler (shared and per-root modes) |
| `reference/compile_bdd.py` | dependency-free ROBDD correctness oracle |
| `reference/compile_sdd.py` | SDD compilation via PySDD (optional) |
| `reference/wmc.py` | exact WMC plus the possible-world-enumeration oracle |
| `reference/export_cnf.py`, `d4_pipeline.py` | CNF export and the d4 d-DNNF pipeline |
| `reference/circuit_io.py` | RDF circuit parsing and serialization |
| `reference/demo.py`, `path_demo.py`, `factor_demo.py` | the paper's running examples |

The bundled pure-Python ROBDD is a **correctness oracle, not a second production compiler**.
Dependency-free smoke tests invoke it explicitly with `--oracle`.

---

## Supported SPARQL Fragment

**Scope.** ABox only (no TBox), currently over one default graph.

### Supported

| Construct | Notes |
|-----------|-------|
| BGP, `UNION`, `JOIN` | full support, maximal gate sharing via RDF set semantics |
| `OPTIONAL` | `OPTIONAL(P1,P2) = (P1 AND P2) ∪ (P1 DIFF P2)`; bare cross-product OPTIONAL included |
| `MINUS` | `MINUS(P1,P2) = P1 DIFF P2` when the operands share a variable, else a no-op (W3C domain-intersection guard) |
| Composite `MINUS` operands | BGPs, UNIONs, or OPTIONALs, on either side, nested, and **chained** (`A MINUS P MINUS Q`) |
| `FILTER` | builds no gate and renames none, so a filtered circuit is a **sub-circuit** of the unfiltered one; each operand's conditions are carried into that operand's reified group. A filtered BGP uses the flat plan |
| Property paths | `+`/`*` arbitrary length plus `/`, `\|`, `^`, `?` in the absorptive semiring PosBool; reachability is a level-indexed fixpoint whose gates stay an acyclic polynomial DAG even on cyclic graphs |
| `DISTINCT` | implicit no-op, since answer gates are already a set |

A `normalize()` pass reduces each composite MINUS operand algebraically to the verified BGP plan:
`(A∪B) MINUS P → (A MINUS P) ∪ (B MINUS P)`; a UNION right operand becomes per-branch subtrahends;
`(A OPT B) MINUS P → (Join(A,B) MINUS P) ∪ ((A DIFF B) MINUS P)`; `P MINUS (C OPT D) → P MINUS C`;
`(A MINUS P) MINUS Q → A MINUS (P∪Q)`.

Non-monotone support rests entirely on one ⊖ (monus / anti-join) primitive, **DIFF**.

### Rejected (loud error, never mis-answered)

| Construct | Reason |
|-----------|--------|
| `GRAPH`, `FROM`, `FROM NAMED` | the reification layout does not encode graph context |
| `LIMIT` / `OFFSET` / `ORDER BY` | solution-sequence modifiers are not provenance-preserving here |
| right-nested `MINUS` `A MINUS (P MINUS Q)` | introduces a join; the string rewriter handles it |
| cross-product `OPTIONAL` **as a MINUS operand** | `(A OPT B) MINUS P` with `A`, `B` sharing no variable |
| a MINUS operand sharing an OPTIONAL's *inner* variable | not reducible to the verified plan |
| `FILTER EXISTS` / `NOT EXISTS`, and any condition outside the SPARQL 1.1 core | the condition carries a pattern, hence provenance, of its own; a condition the rewriting cannot render back into the group is refused rather than dropped |
| a `FILTER` referencing a variable its own group does not bind | hoisting it to the enclosing group would change its value |
| the W3C **filtered left join** | an `OPTIONAL`'s condition spanning *both* operands. A condition over the OPTIONAL's own variables is pushed onto that operand and supported |
| `BIND`, aggregation, sub-`SELECT`, `VALUES` | out of scope |
| negated property sets `!(...)` | out of scope |

Engine-side property paths are currently `+`/`*` over a single predicate with an **IRI frontier
only** (blank-node and literal path nodes are not yet supported) on any writable SPARQL 1.1
endpoint; the Python reference covers all operators. See [TECHREPORT.md](TECHREPORT.md) §4.6.

### Engine vs. Reference Semantics

The engine output is specifically a **Boolean event circuit for PQE**. Because identical RDF edges
are set-deduplicated, it does not preserve free-semiring coefficients (`x²`, `2x`), whereas the
Python algebraic reference does preserve that multiplicity. The distinction is immaterial to exact
Boolean WMC, where repeated event operands are idempotent, and is documented in
[TECHREPORT.md](TECHREPORT.md) §3.2.

---

## Probabilistic Data Formats

### Input: Reified Probabilistic ABox

Each base triple is reified so that its statement IRI can carry a probability. That statement IRI is
the provenance **token** and becomes a leaf of the circuit:

```turtle
@prefix d:   <urn:d:> .
@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .

d:p1 rdf:subject d:Aspirin  ; rdf:predicate d:iw ; rdf:object d:Warfarin  .
d:p2 rdf:subject d:Warfarin ; rdf:predicate d:iw ; rdf:object d:Metformin .
```

`reference/watdiv/reify.py` converts a plain N-Triples file into this form. With
`--scheme SPARQL_Star` the same information is carried by quoted triples instead.

### Probability Assignment

A JSON object from the **complete token IRI** to a number in [0, 1]:

```json
{
  "urn:d:p1": 0.92,
  "urn:d:p2": 0.87,
  "urn:d:p3": 0.85
}
```

### Output: The Circuit Vocabulary

The circuit is an ordinary RDF graph in the `urn:circuit:` namespace (`c:` below):

| Term | Meaning |
|------|---------|
| `c:Times` | ⊗ gate (join) |
| `c:Plus` | ⊕ gate (union); an answer root when it carries `c:binding` |
| `c:Minus` | ⊖ gate (difference / OPTIONAL / MINUS) |
| `c:in` | gate input edge (child gate or leaf token) |
| `c:feeds` | gate output edge |
| `c:binding` | link from an answer root to one projected-variable binding node |
| `c:var` | the variable name of a binding node |
| `c:val` | the bound RDF term; **absent means unbound** |
| `c:answer` | human-readable display label only, **not** an identifier |

Gate IRIs are `urn:g:t:<sha256>` for internal gates and `urn:g:a:<sha256>` for answer roots.

---

## From Circuit Back to Answers

The rewritten query is a **CONSTRUCT**, so the engine returns an RDF graph rather than a result
table, but the SELECT bindings are not lost. Each answer's root ⊕ gate carries its projected binding
as **structured RDF** that preserves the exact RDF term (IRI vs. literal, datatype, language tag,
bound vs. unbound):

```
<urn:g:a:…> a c:Plus ;
    c:binding [ c:var "y" ; c:val <…#Bob>  ] ,
              [ c:var "c" ; c:val <…#Rome> ] .            # ?y=Bob,   ?c=Rome
<urn:g:a:…> a c:Plus ;
    c:binding [ c:var "y" ; c:val <…#Carol> ] ,
              [ c:var "c" ] .                             # ?y=Carol, ?c UNBOUND (no c:val)
```

The gate IRI is a **collision-resistant, term-type-aware identity hash** (`SHA256` of a kind-tagged,
per-part-hashed serialization of the binding), so two answers that differ only by term type get
**distinct** gates: an IRI `<…#foo>` vs. a literal `"…#foo"`, `"1"^^xsd:integer` vs.
`"1"^^xsd:string`, `@en` vs. `@fr`, or bound vs. unbound.

The client-side recovery step is:

1. find every gate with `c:binding` (equivalently, a `c:answer` label) → the answer roots;
2. for each, read its `c:binding` nodes → `c:var` (the variable) and `c:val` (the RDF term); a
   binding with **no `c:val` means that variable is unbound**, for example an unmatched OPTIONAL;
3. compile the sub-circuit rooted there and weighted-model-count it → that row's probability.

This reconstructs the ordinary SELECT table plus a probability column, so the CONSTRUCT output is a
**superset** of the SELECT output. The `c:answer "A|var=value|…"` literal each gate also carries is a
display and debug label only: it is built from `STR()` and is **not** injective (an IRI and a
same-lexical literal render identically), so **never use `c:answer` to identify or de-duplicate
answers** — use the gate IRI or `c:binding`. `reference/verify_gallery.py` and
`reference/verify_answer_keys.py` do exactly this.

---

## Benchmark Reproduction

Step-by-step instructions, expected outputs, and hardware notes are in
**[REPRODUCE.md](REPRODUCE.md)**; the experiment-to-claim map is in
**[EVALUATION.md](EVALUATION.md)**.

### 1. No External Services

All commands run from `reference/`.

| Result | Command | ~time |
|--------|---------|-------|
| **Compactness**: shared circuit vs. per-answer strings (`deep-12×2` = 201×) | `python3 bench.py` → `bench.csv` | seconds |
| **Non-monotone** correctness (OPTIONAL/MINUS via ⊖ vs. enumeration) | `python3 verify_nonmono.py` | seconds |
| **Factored vs. flat** construction | `python3 factor_demo.py` | seconds |
| **Per-answer vs. shared** representation at scale | `python3 e11_per_answer_vs_shared.py` | seconds |
| **Reification-scheme** cost (`Standard` vs. `SPARQL_Star`) | `python3 g7_reification.py` | seconds |

### 2. Deployed Engine (needs GraphDB)

| Result | Command | ~time |
|--------|---------|-------|
| Deployed-engine construction timings | `python3 bench_engine.py` → `bench_engine/results.csv` | minutes |
| PQE latency breakdown | `python3 g3_pqe_latency.py` | minutes |
| Per-instance rigor sweep | `python3 g4_rigor.py` | minutes |

### 3. Real-KG and Compiler Baselines

```bash
cd reference
# a) reify a WatDiv N-Triples file into statement form
python3 watdiv/reify.py /path/to/base.nt watdiv/base.reified.nt
# b) start GraphDB on localhost:7200, create a repo "watdiv", load watdiv/base.reified.nt
#    (see watdiv/repo.ttl for the repository config)
# c) run the star / path / snowflake shapes through the full pipeline
python3 watdiv_run.py                  # -> build_ms, wmc_ms, sizes per shape
```

| Result | Command | Needs |
|--------|---------|-------|
| Real-KG WatDiv, flat vs. factored | `WATDIV_NT=/path/to/base.nt python3 watdiv_factor.py` | WatDiv data |
| Real-KG WatDiv, end-to-end | `python3 watdiv_run.py` | GraphDB + data |
| d4 d-DNNF scaling | `python3 export_cnf.py`, then follow [reference/D4_ON_LINUX.md](reference/D4_ON_LINUX.md) | Linux/x86 |
| Compiler portfolio comparison (OBDD / SDD / d-DNNF) | `python3 compile_portfolio.py` | PySDD, d4 (optional) |
| ProvSQL head-to-head | see [provsql/README.md](provsql/README.md) | PostgreSQL + ProvSQL |
| NPCS-baseline head-to-head | `python3 g2b_npcs_vs_ours.py` | — |

Expected numbers and their interpretation live in [reference/RESULTS.md](reference/RESULTS.md)
(baselines, rigor, space, correctness), [reference/CANONICAL_TIMINGS.md](reference/CANONICAL_TIMINGS.md)
(the one table to cite for timings) and `reference/watdiv/RESULTS.md`. Figures for the paper are
regenerated from `presentation/` (`make_figures.py`, `make_matrix_figures.py`, and friends).

### Data Acquisition

The WatDiv data (http://dsg.uwaterloo.ca/watdiv/) and GraphDB (https://graphdb.ontotext.com/) are
obtained separately, as described in [NOTICE](NOTICE). The paper uses a WatDiv subset of **51,863
triples** (`base.nt`); any WatDiv N-Triples file works with `watdiv_factor.py` and `reify.py` once
`WATDIV_NT` points at it.

### Consistency With the Original NPCS (optional)

`engine/verify/diff_harness.py` differential-tests our rewriter against the original NPCS artifact,
which is **not** included (see [NOTICE](NOTICE)). Set `NPCS_ORIG_JAR` and `NPCS_QDIR` to run it. The
harness compares query structure after capture-avoiding gensym normalization; generated variable
spellings are intentionally not an API.

---

## Project Structure

```
SPARQLcirc/
├── engine/                       # Java: the γ rewriter (SPARQL -> circuit-building CONSTRUCT)
│   ├── src/main/java/npcs/
│   │   ├── App.java              # fat-JAR subcommand dispatcher
│   │   ├── circuit/              # CircuitRewriter -> CONSTRUCT plan, CircuitRun
│   │   └── rewrite/              # NPCS-style provenance-string baseline
│   ├── examples/                 # runnable BGP / UNION / OPTIONAL / MINUS examples + gallery
│   ├── verify/                   # correctness oracle + differential diff vs. the original NPCS
│   └── pom.xml                   # Maven build (RDF4J 4.2.1, shaded JAR)
├── reference/                    # Python: reference circuit, compilers, WMC, evaluation
│   ├── gates.py gamma.py         # content-addressed gates + circuit construction
│   ├── factor.py                 # factored construction (variable elimination)
│   ├── compiler.py compile_*.py  # CUDD production compiler, ROBDD oracle, SDD
│   ├── wmc.py                    # exact WMC + possible-world-enumeration oracle
│   ├── pqe.py                    # user-facing JSON CLI
│   ├── tests.py verify_*.py      # correctness batteries and regression gates
│   ├── bench*.py e*.py g*.py     # evaluation harnesses (compactness, timings, scaling)
│   ├── watdiv/ wikidata/ tpch/   # dataset harnesses and repo configs
│   ├── RESULTS.md                # result notes: baselines, rigor, space, correctness
│   ├── CANONICAL_TIMINGS.md      # the single citable timing table
│   ├── EVALUATION_MAP.md         # research question -> experiment -> artifact -> takeaway
│   └── paper/                    # paper-facing harnesses and frozen-input regressions
├── presentation/                 # figure and table generation for the paper
├── provsql/                      # external ProvSQL baseline (PostgreSQL schema + runner)
├── HOWITWORKS.md                 # narrative walkthrough of the construction
├── TECHREPORT.md                 # formal semantics, proofs, engine/reference divergences
├── REPRODUCE.md                  # step-by-step reproduction instructions
├── EVALUATION.md                 # evaluation plan and claim-to-experiment map
├── REIFICATION.md                # Standard vs. SPARQL_Star reification trade-off
├── FACTORED_CONSTRUCTION.md      # the factored construction plan in detail
├── BASELINE_COVERAGE.md          # what each baseline does and does not cover
├── CONFORMANCE.md                # audit of the paper's claims against both implementations
├── LICENSE                       # Apache-2.0
└── NOTICE                        # attribution (clean-room NPCS reimplementation) + dependencies
```

Module-level documentation lives in [engine/README.md](engine/README.md) and
[reference/README.md](reference/README.md).

---

## Troubleshooting

### "BUILD FAILURE" during the Maven build
Confirm Java 11+ and that Maven can reach Maven Central for the RDF4J tree:
```bash
java -version   # should show 11 or higher
mvn -f engine/pom.xml -U package
```

### `ModuleNotFoundError: No module named 'dd'` or a CUDD import error
The production compiler needs Python 3.11+ and the native CUDD wheel:
```bash
python3 --version                                            # must be 3.11+
python3 -m pip install -r reference/requirements-production.txt
```
To run without CUDD at all, use the bundled oracle: `python3 reference/pqe.py --oracle ...`.

### Factored construction fails on a read-only endpoint
`factored` mode writes private, per-invocation `urn:sc:*` message rows and fails fast when
`CIRCUIT_READONLY=1`. Either grant update rights or switch mode:
```bash
java -jar engine/target/npcs-rewrite.jar circuit --construction=flat Standard data.ttl query.rq
```

### "Connection refused" against the endpoint
Make sure the store is up and the repository exists:
```bash
curl -I http://localhost:7200/repositories/watdiv
```

### A query is rejected with a loud error
That is by design, not a bug: see [Rejected](#rejected-loud-error-never-mis-answered) above. The
system refuses constructs it cannot answer exactly rather than returning a wrong probability. For
the rejected MINUS shapes, the NPCS string rewriter (`rewrite` subcommand) still applies.

### `watdiv_factor.py` reports no data
Set the dataset path explicitly:
```bash
WATDIV_NT=/path/to/base.nt python3 reference/watdiv_factor.py
```

---

## Citing and Attribution

The rewriter in `engine/` is an independent **clean-room reimplementation** of the query rewriting
from **NPCS** (Asma et al., *NPCS: Native Provenance Computation for SPARQL*, WWW 2024). It is
written from the published algorithm and does not include or derive from the original NPCS source.
See [NOTICE](NOTICE) for the full citation and the list of third-party dependencies.

---

## License

This project is licensed under the Apache License 2.0. See [LICENSE](LICENSE) for details.
