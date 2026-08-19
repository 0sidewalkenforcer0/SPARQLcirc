# SPARQL_circ — Technical Report

*Native probabilistic query evaluation for SPARQL via compiled provenance circuits.*
Precursor to the VLDB'27 paper. Author: Jingcheng Wu.

> **Status labels used throughout.** **[impl]** implemented and unit-verified in this repo;
> **[pilot]** measured at small/prototype scale; **[planned]** designed/staged, not yet run at
> scale. Do not present [pilot]/[planned] items as finished results in the paper.

---

## 1. Problem and contribution

### 1.1 Probabilistic query evaluation (PQE) for SPARQL
A **probabilistic knowledge graph** is an RDF graph in which each triple `τ` carries an
independent probability `p(τ) ∈ [0,1]` that it holds. Under the **tuple-independent** model this
induces a distribution over `2^n` *possible worlds* (each of the `n` triples present or absent).
For a SPARQL `SELECT` query `Q` and a candidate answer (a variable binding) `μ`, PQE asks for

```
Pr[μ is an answer] = Σ_{worlds W : μ ∈ Q(W)}  Π_{τ∈W} p(τ) · Π_{τ∉W} (1−p(τ)).
```

PQE is **#P-hard** in general (it subsumes weighted model counting). The essential difficulty is
**correlation**: an answer is usually derivable in several ways that *share* triples, so one may
not simply multiply/add per-derivation probabilities — a shared triple must be counted once.

### 1.2 The gap this work closes
- **NPCS** (Asma et al., WWW'24) and **SPARQLprov** rewrite `Q` so an engine returns, per answer,
  a *provenance string* (a spm-semiring expression spelled out as text). They do **not** compute
  probabilities, and the strings repeat shared subterms (size grows with #derivations).
- **ProvSQL** (Senellart et al.) *does* exact PQE by building a provenance circuit and
  knowledge-compiling it — but inside a **modified PostgreSQL**, over relations, not RDF/SPARQL.

**Contribution.** A query rewriting `γ` that makes an **unmodified** SPARQL engine materialize a
single **shared, content-addressed provenance circuit** (an RDF DAG of ⊕/⊗/⊖ gates) for *all*
answers at once; the client then knowledge-compiles the circuit and weighted-model-counts it for
**exact** PQE, **including the non-monotone fragment** (OPTIONAL/MINUS via ⊖). No engine
modification, no relational remodeling; the circuit is ordinary RDF, so the engine's set-semantics
deduplicates shared gates automatically.

### 1.3 Positioning (one line each)
- vs NPCS/SPARQLprov: we produce a *shared circuit* + actual *probabilities*, not per-answer strings.
- vs ProvSQL: same exactness/tractability, but on a *stock* triplestore, native to RDF/SPARQL.
- vs pSPARQL (Fang 2019): that is fuzzy/t-norm scoring, **not** possible-world probability — a
  semantics-level non-competitor.

---

## 2. Scope

- **Data:** ABox only (no TBox/reasoning), one default graph. `GRAPH`, `FROM`, and `FROM NAMED`
  are rejected fail-fast; the current token layouts do not encode graph context.
- **Queries:** `SELECT`; the algebra fragment **BGP/AND, FILTER, UNION, OPTIONAL, MINUS**, **property
  paths** (arbitrary-length `+`/`*`, and `/ | ^ ?`) + projection (§4.6), plus the implementation-only
  output-binding compatibility extension described below.
- **`FILTER`:** supported in the circuit rewriter. It builds no gate and renames none (`g_{σφ(P)} = g_P`),
  so the rewriting carries each operand's conditions into that operand's reified group and a filtered
  circuit is a **sub-circuit** of the unfiltered one. Two limits, both fail-fast: a condition outside the
  renderable SPARQL 1.1 core (notably `EXISTS`/`NOT EXISTS`, which carry a pattern — hence provenance —
  of their own) and a condition referencing a variable its own group does not bind. A `FILTER` inside an
  `OPTIONAL` group becomes the W3C left-join condition; when it mentions only the OPTIONAL's own
  variables it is pushed back onto that operand, and otherwise (the genuine **filtered left join**, whose
  condition spans both operands) it is rejected. Filtered BGPs use the flat plan (the factored passes
  have no single group for the condition). The **NPCS string rewriter has no filter rule** and rejects
  `FILTER` outright.
- **Output-only `BIND` (implementation compatibility extension):** accepted when the target is not
  used by a later triple pattern. Extend changes solution bindings but not their provenance annotation,
  so the expression is carried into the flat reified group without adding a gate. This covers the
  constants, aliases, `YEAR`, and arithmetic expressions in SPARQLprov's TPC-H non-aggregate queries.
  A join-constraining `BIND` is rejected because moving it after the reified patterns would change the
  query. This extension is outside the paper's formal fragment (claim P1.3).
- **Excluded (by design):** general/join-constraining `BIND`, in-query aggregation, sub-`SELECT`, `VALUES`, and negated
  property sets `!(...)` — **rejected** (fail-fast, never silently mis-handled).
- **Solution modifiers:** `LIMIT`/`OFFSET`/`ORDER BY` are **rejected** (they do not apply to a
  materialized circuit of all answers); `DISTINCT` is an **implicit no-op** (answer gates are a set).
- **Input/output form:** input is a `SELECT`; the rewritten query is a **`CONSTRUCT`** that builds
  the circuit as RDF. The SELECT bindings are preserved in structured `c:binding`/`c:var`/`c:val` nodes
  (§4.5); the `c:answer` literal is only a human-readable debug label.

---

## 3. Data model and provenance semiring

### 3.1 Reification (tokens)
Each base triple is given an identity (**token**) via a reification scheme so a triple pattern can
bind the matching statement's id to a fresh variable `?fprovN`:

- **Standard** (default): `t rdf:subject s ; rdf:predicate p ; rdf:object o .` — the token `t` is
  the statement node. Cost: **3×** triple blow-up.
- **SPARQL-star**: `<< s p o >> :occurrenceOf t .` — compact alternative on RDF-star-native engines.
  (`:occurrenceOf` is a placeholder predicate; it must match the data's convention.)

A token is the unit of uncertainty: one independent Boolean variable with probability `p(t)`.

### 3.2 Algebraic provenance and the PQE quotient
The algebraic specification lives in the free **semiring-with-monus** `N[X]` over the token set `X`, with:
- **⊕** (sum) — alternative derivations / UNION;
- **⊗** (product) — joint use / JOIN;
- **⊖** (monus) — difference / anti-join (DIFF; and the negative branch of OPTIONAL/MINUS).

For **PQE** we use the Boolean/event abstraction: **⊗ ↦ ∧, ⊕ ↦ ∨, ⊖(a,b) ↦ a ∧ ¬b**, and
`Pr[·]` = weighted model count (WMC) with weights `p(t)`, `1−p(t)`. This is exact for
tuple-independent PQE (the Boolean function is the possible-world indicator; WMC is its expectation).

The two representations in this repository deliberately stop at different points. The Python algebraic
reference (`reference/gates.py`) retains repeated children and can therefore represent `N[X]`
multiplicity. The engine-materialized RDF circuit is the **Boolean/event quotient used for PQE**:
`c:in` and `c:feeds` are ordinary, unindexed RDF edges, so two identical edges collapse under RDF set
semantics. Thus `x⊗x` is read as `x∧x=x` and two identical derivations as one event. This is lossless for
PQE, but the RDF graph cannot be used to reconstruct polynomial coefficients such as `x²` or `2x`.
Faithful `N[X]` interchange would require occurrence/index nodes on circuit edges. **[impl, verified]**

---

## 4. The provenance circuit and the γ rewriting

### 4.1 Boolean event circuit as RDF
Vocabulary `urn:circuit:` — node types `Times`, `Plus`, `Minus`; edges `in` (⊗→leaf), `feeds`
(gate→parent-⊕), `minuend`/`subtrahend` (⊖ operands), `answer` (⊕→its answer key). A **leaf** is a
token node (no wrapper). The circuit is a set of N-Triples; RDF set-semantics deduplicates any gate
or identical edge emitted more than once. This is the serialized Boolean quotient described in §3.2,
not a multiplicity-preserving `N[X]` exchange format.

### 4.2 Content-addressing (the sharing mechanism)
Every gate's IRI is a deterministic hash of its *meaning*:
```
gateIRI = IRI( prefix + SHA256(canonicalKey) )         -- computed inside SPARQL
```
Equal gates ⇒ equal IRIs ⇒ the engine stores them once ⇒ **sharing is automatic and engine-side**,
with no coordination. Prefixes: `urn:g:t:` (⊗), `urn:g:a:` (answer ⊕), and `urn:g:p1: p2: sub: m:`
(MINUS internals).

**Canonical ⊗ key (collision-resistant, order-independent).** A product's key is a function of its
sorted child sequence (including repeats), not their order (so a self-join's two orderings collapse).
We SHA256 each child
token to fixed-width hex, **sort the hashes with a comparator (bubble-sort) network expressed in
pure SPARQL 1.1** (`BIND(IF(?a<=?b, …))`), then hash the concatenation:
`CONCAT("T","|",h_(1),…,"|",h_(k))`. Fixed-width hex makes the serialization injective before the
final SHA-256 and closes NPCS's ambiguous-concatenation hole; the resulting IRI is collision-resistant,
not mathematically collision-free. Repeated RDF edges are still folded by the Boolean serialization
boundary in §3.2. **[impl]**

**⊗ may share across queries, ⊕/⊖ may not.** A ⊗ gate's IRI *is* its sorted child multiset, so equal
IRI ⇒ equal Boolean function and two queries that share a derivation may safely share the gate. A ⊕/⊖
IRI is `(pattern tag, binding)` and says nothing about the `c:feeds`/`c:minuend` edges that later
accumulate on it, so an IRI shared by two *different* algebra nodes is **aliasing, not sharing** — it
ORs unrelated functions together. Hence Def. 4.6 gives `id_⊗` no pattern tag and `id_⊕^θ`/`id_⊖^θ` one,
and every ⊕/⊖ here carries one: `P1@`/`P2@`/`SUB@`/`M@` (`bgpSemanticKey` over the operand's patterns
*and* its FILTERs), `BASE@`/`MARG@` (factored internals), `R|<fp>` (path reach/base, plus a `c:rpath`
guard on every match pattern), and `A@<θ>` for the answer ⊕. **[impl, verified]**

**Answer identity & recovery.** An answer ⊕'s IDENTITY is its query's pattern tag `A@SHA256(θ)` — θ a
canonical prefix serialization of the normalized body and the projected variable tuple, with commutative
operators (BGP conjunction, UNION alternatives) sorted so a parser re-association cannot change it —
followed by a term-type-aware key (`idKey`): per projected
variable, a kind-tagged (IRI / blank / literal-with-datatype-and-lang / unbound) SHA-256 component, so
distinct solutions — IRI vs same-lexical literal, differing datatype/lang, bound vs unbound — get distinct
gate IRIs. θ is ONE value per query, so a UNION's branches, a MINUS root, a factored BGP and a flat BGP
still converge on the SAME answer ⊕ — that convergence is what makes the circuit shared — while two
different queries never do. This is **collision-resistant** (modulo SHA-256), *not* mathematically
injective; the injective part is the delimiter-free serialization before the hash. Note θ is derived
from the query, not the graph: the same query over two different base graphs mints the same answer IRI
with different children, so a cross-dataset circuit store would need a dataset id in the scope.
The recoverable binding is carried by structured
`c:binding`/`c:var`/`c:val` nodes (the real RDF term; a variable unbound in that solution has no `c:val`).
A literal `c:answer = "A|v=<val>|…"` is *also* emitted but is only a **human-readable debug label** —
consumers recover answers from `c:binding`, never by parsing that string.

### 4.3 β and γ
`γ` reuses NPCS's rewriting skeleton `β` (reify each triple pattern, bind `?fprovN`) but replaces
the *string* operators `ProvProd/ProvAggSum/ProvDiff` with **gate constructors** emitted as
`CONSTRUCT` templates. A query compiles to a **plan** = a list of CONSTRUCTs whose union is the
circuit. Two implementations exist:
- **`NpcsRewriter`** (string path) — reproduces NPCS's textual provenance; BGP output verified
  **byte-identical** to the original `ReifySparqlByte.jar` on 139/139 WatDiv queries (both schemes).
  Used as a reference/baseline. Handles the full fragment recursively (incl. all nested MINUS).
- **`CircuitRewriter`** (circuit path) — **the contribution**; emits the CONSTRUCT plan. Stateless
  (content-addressed, no gensym counters). *(`NpcsRewriter` uses mutable gensym counters and is
  therefore not thread-safe; one instance per rewrite.)*

### 4.4 Per-operator construction

**BGP** — 1 CONSTRUCT. Reify all patterns (tokens `a0…a_{k-1}`); one `Times` over the tokens
(canonical key), feeding a `Plus` keyed by `W`:
```
CONSTRUCT { ?t a c:Times ; c:in ?a0 ; … ; c:feeds ?ans .   ?ans a c:Plus ; c:answer ?anskey . }
WHERE    { <reified patterns> ; <comparator-network BINDs> ;
           BIND(<A|W=…> AS ?anskey) ; BIND(IRI(g:t:+SHA256(<sorted ⊗ key>)) AS ?t) ;
           BIND(IRI(g:a:+SHA256(?anskey)) AS ?ans) }
```
Each matching derivation → one ⊗; all derivations of an answer feed one ⊕ (grouping by `W` is
implicit via the shared, content-addressed `?ans`). **[impl, verified]**

**UNION** — branch-wise. `branchPlan(A) ++ branchPlan(B)`, both keyed by the same `W`. The two
branches feed **one shared answer ⊕** (content-addressed by the binding) — that shared Plus *is* the
⊕. (Regression note: an early version had no `Union` case and silently compiled UNION as a JOIN;
now covered + tested.) **[impl, verified]**

**MINUS** — guarded DIFF (see §5 for the semantics). `minusPlan`:
- `⊕_{P1}(V1)` — products of the left BGP `P1`, grouped by `V1 = vars(P1)`;
- for **each** right branch `Rb` (a UNION right operand is split into branches) that shares a
  variable with `P1`: `⊕_{P2}(V2)` and a CONSTRUCT connecting compatible `⊕_{P2}(μ') → ⊕_{sub}(μ)`
  (natural join on shared vars); since `⊕_{sub}` is content-addressed by `V1`, all branches feed one
  gate — μ is removed iff it matches **some** branch;
- `⊖(⊕_{P1}(μ), ⊕_{sub}(μ)) → answer(W)`.
- If no branch shares a variable with `P1`, MINUS is a **no-op** (returns `P1`). **[impl, verified]**

**OPTIONAL** — `A OPTIONAL B ≡ (A AND B) ∪ (A DIFF B)`: one AND-branch CONSTRUCT over `A∪B`
(a BGP), plus the DIFF plan (the MINUS internals **without** the shared-variable guard — the
negative branch of OPTIONAL is plain anti-join). **[impl, verified]**

### 4.5 Post-processing (recovering the SELECT answers)
The CONSTRUCT output is an RDF graph, but the SELECT table is not lost: it is a **superset** of it.
The client (a) finds every answer gate (the ⊕ gates carrying `c:binding`), (b) reads its
`c:binding`/`c:var`/`c:val` nodes → the term-aware binding (`c:val` absent ⇒ that variable is unbound),
(c) compiles the sub-circuit rooted there and WMCs it → that row's
probability. Result = the ordinary SELECT result table + a probability column. **[impl]**

### 4.6 Recursive provenance for property paths

Property paths need the provenance of *reachability*, which is recursive — a pair `(u,v)` may be
connected by unboundedly many walks (infinitely many on a cyclic graph). SPARQL_circ evaluates paths
in the **absorptive semiring PosBool(X)** (⊕, ⊗ idempotent, absorption `a ⊕ (a⊗b) = a`), so paths have
**set semantics**: a reachable pair appears once regardless of the number of paths, and alternative-path
duplicates (`:p|:q`) collapse. The Python algebraic reference applies these stronger PosBool
simplifications only inside path subcircuits; its non-path constructors retain `N[X]` multiplicity.
The engine's serialized RDF output, for paths and non-path queries alike, is evaluated through the
Boolean/PQE quotient in §3.2. **[impl, verified]**

**Level-indexed fixpoint.** An arbitrary-length path `e+` is the transitive closure computed by a
level-indexed iteration
```
reach^0(u,v)     = edge_e(u,v)
reach^{k+1}(u,v) = reach^k(u,v)  ⊕  ⊕_w [ reach^k(u,w) ⊗ edge_e(w,v) ]
```
The level `k` is baked into each reach gate's identity, so a level-(k+1) gate references only level-≤k
gates: the emitted circuit is an **acyclic DAG even when the data graph has cycles** (where naive walk
enumeration is infinite). **Recursive sharing** — `reach^{k+1}(u,v)` references the single gate
`reach^k(u,w)`, never an expanded sum of paths — keeps the circuit **polynomial**: for a bound source
`O(|V_s|·|E_s|)` gates (≤ `|V_s|` levels, `O(|E_s|)` work per level; all-pairs multiplies by `|V|`).
Simple-path collapse (a simple path has ≤ `|V|-1` edges) bounds the iteration to `|V|-1` rounds; longer,
non-simple walks add no probability mass under the Boolean/PosBool reading. **[impl, verified]**

**Operators** (compositional on pair-relations): `e1/e2` = relational compose; `e1|e2` = union
(absorptive ⊕ dedups); `^e` = swap endpoints; `e+` = the closure above; `e* = e+ ⊕` zero-length;
`e? = e ⊕` zero-length. Zero-length uses the **terms-in-graph** reading (a node relates to itself iff it
occurs in the graph); engines differ here, so the correctness statement is *qualified to this reading*.
The path circuit is Boolean/PosBool and compiles + WMCs through the **same** backend as the rest; shared
edge tokens are counted once (correlation), exactly as for BGP sharing.

**Two realizations.**
- *Python reference* (`reference/gamma.py`, DSL node `('path', subj, pathexpr, obj)`): all operators
  `/ | ^ + * ?`, all endpoint modes. Verified circuit-WMC == possible-world enumeration on cyclic graphs
  and across probability assignments (`reference/tests.py`, `reference/path_demo.py`).
- *Engine* (`CircuitRewriter`/`CircuitRun`): SPARQL 1.1 has no provenance-exposing recursion, so a
  **client-driven iterative protocol** issues one CONSTRUCT per level, feeds each round's reach gates
  back into the store, and loops to the simple-path bound. reach gates are keyed by `(level, from, to)`
  (the level keeps the RDF DAG acyclic); the (possibly compound) sub-path is materialized once as an
  all-pairs base relation, and composition ⊗ gates are content-addressed by sorted child hashes. reach⁰
  is seeded from the base **restricted to the source when it is bound**, so a bound source is
  single-source `O(|V_s|·|E_s|)` while a variable source stays all-pairs (a factor of `|V|` larger —
  confirmed by ring-scaling). Engine scope: `+`/`*` over a single predicate **or a compound sub-path**
  (`/`, `|`, `^`), the zero-or-one `?` (via `ZeroLengthPath`), all endpoint combinations, Standard
  reification — verified against the Python reference / possible-world enumeration on cyclic graphs
  (`reference/verify_engine_paths.py`: `(p/q)+`, `(p|q)+`, `(^p)+`, `:p?`, …). The circuit can be
  built on **any SPARQL 1.1 engine** (`CircuitRun` endpoint mode → GraphDB/Fuseki), since the emitted
  CONSTRUCTs are deterministic and use only standard SPARQL 1.1 (checked by
  `reference/verify_engine_agnostic.py`); the byte-identical cross-engine run needs a running endpoint.
  **Nested** closures (a closure whose sub-path itself contains a closure, or a closure joined with
  other patterns) and SPARQL-star reification for paths are future work. **[impl, verified]**

**Size, empirically** (`reference/path_demo.py`): on a cyclic ring the `?x p+ ?y` circuit has `≈|V|²`
gates (`gates/|V|² → 1`); on a clique it stays polynomial while the number of simple paths is
`~e·(|V|-2)!`.

---

## 5. Non-monotone semantics: DIFF vs MINUS (a subtle, paper-worthy point)

There are **three** difference-related constructs; conflating them is a known trap (and an NPCS
over-claim we correct):

| construct | what | remove μ iff |
|---|---|---|
| **DIFF** (algebra anti-join; negative half of OPTIONAL) | not a user keyword | ∃ compatible μ' |
| **OPTIONAL** (LeftJoin) | user keyword | = Join ∪ DIFF |
| **MINUS** (SPARQL 1.1) | user keyword | ∃ compatible μ' **AND** `dom(μ)∩dom(μ') ≠ ∅` |

`⊖` (monus) is the provenance of **DIFF**; it is *not* directly W3C MINUS. DIFF and MINUS diverge
exactly when operands are **compatible but domain-disjoint** (no shared bound variable): DIFF
removes, MINUS keeps (no-op).

**Reduction (our §4 lemma), for BGP operands** (every variable always bound, so
`dom(μ)∩dom(μ') = vars(P1)∩vars(P2)` statically):
```
MINUS(P1,P2) = P1                     if vars(P1)∩vars(P2) = ∅        (no-op)
             = DIFF(P1,P2)            otherwise
```
So MINUS is DIFF behind a **shared-variable guard**; when they share a variable the W3C
domain-intersection condition is automatically met and MINUS collapses to the (verified) DIFF
machinery. `OPTIONAL` uses **unguarded** DIFF (its negative branch must remove domain-disjoint
matches). This is why the guard is applied to user-MINUS only. **[impl, verified]**

**Composite MINUS operands** are reduced algebraically to the above by `normalize()` (all
PQE-valid; Boolean identities in brackets):
```
(A∪B) MINUS P        ≡ (A MINUS P) ∪ (B MINUS P)                 [(a∨b)∧¬s = (a∧¬s)∨(b∧¬s)]
P MINUS (C∪D)        →  per-branch subtrahends into one ⊕_sub    [remove iff matches C or D]
(A OPT B) MINUS P    ≡ (Join(A,B) MINUS P) ∪ ((A DIFF B) MINUS P)   [DIFF on B, not MINUS — see below]
P MINUS (C OPT D)    ≡ P MINUS C                                 [P shares no D-only var; matched⊕unmatched=always]
(A MINUS P) MINUS Q  ≡ A MINUS (P∪Q)                             [(A∖P)∖Q = A∖(P∪Q)]
```
The `(A OPT B) MINUS P` line uses **DIFF, not MINUS, on B**. `A OPT B`'s negative branch is the
*unguarded* `A DIFF B` (it removes a match even when the operands' domains are disjoint), so folding
B into a **guarded** MINUS would be wrong when A,B share no variable — take A={?x↦1}, B={?w↦2}, P=∅:
the correct answer is {?x↦1,?w↦2}, but `A MINUS B` is a no-op on disjoint domains and would leave a
spurious {?x↦1}. `normalize()` therefore realizes the second disjunct as `A MINUS (B∪P)` **only under
a shared-variable guard** — where `A DIFF B = A MINUS B`, so the two forms coincide — and **rejects**
the no-shared-var case (the cross-product OPTIONAL residual below). The emitted plan is thus always
the DIFF form; `A MINUS (B∪P)` is merely how the shared-var case reuses the verified MINUS machinery.

The `P MINUS (C OPT D) ≡ P MINUS C` identity is verified **at the provenance level**, not just the
answer set: the optional D-part washes out because, per C-solution, `matched ⊕ unmatched = always`.
**[impl, verified]**

**Residuals** — safely **rejected** by the circuit (loud error, never mis-answered; the string path
handles them): right-nested `A MINUS (P MINUS Q)` (which is `A∖(P∖Q) = (A∖P)∪(A∩Q)`, introducing a
join — not a pure MINUS reduction); a **cross-product OPTIONAL as a MINUS operand** (`(A OPT B) MINUS P`
with `A`,`B` sharing no variable — a *bare* cross-product OPTIONAL is fully supported via the unguarded
DIFF in `optionalPlan`, not rejected); a MINUS operand sharing an OPTIONAL's **inner** variable. These are pathological and
essentially never occur in real queries.

**Correctness abstraction.** `⊖(a,b) ↦ a∧¬b`, giving "μ present iff its P1-derivation holds and no
removing derivation holds" — exactly W3C MINUS/OPTIONAL under the possible-world semantics.

---

## 6. Engine-agnosticism and the fail-fast guard

- The rewritten CONSTRUCTs use only standard SPARQL 1.1 (BGP, UNION, OPTIONAL, aggregation,
  `BIND`, built-in `SHA256`). The **same** plan runs on any SPARQL 1.1 endpoint; verified to produce
  the **byte-identical circuit** on in-memory **RDF4J** and deployed **GraphDB 10.7**. **[impl, verified]**
- **Litmus test for "unmodified engine":** point the system at a fresh, unpatched store (or a
  different vendor) and it works with standard SPARQL; no plugin/UDF/patch/recompile. ProvSQL fails
  this (it forks PostgreSQL); we pass.
- **Fail-fast guard** (`assertPureBgp`): anything outside {Join, Filter, output-only Extension,
  StatementPattern} in a BGP position (subquery, property path, an unsupported nested operand) throws
  `UnsupportedOperationException` rather than being silently dropped by `StatementPatternCollector`.
  `Filter` and output-only `Extension` are admitted, and their expressions are validated and rendered
  separately — so neither operation is silently dropped.
  This is what prevents the class of silent-semantics bugs (e.g. a dropped FILTER, or the historical
  UNION-as-join). **[impl, verified]**

---

## 7. Knowledge compilation and WMC

The materialized circuit is a Boolean-function representation; compiling it to a **d-DNNF-family**
target makes WMC **linear in the compiled size**.

- **`compile_bdd.py`** — a self-contained, zero-dependency **ROBDD** + WMC (Apple-Silicon native).
  Default path; used for all correctness cross-checks. **[impl, verified]**
- **`compile_sdd.py`** — **SDD** via PySDD (real UCLA library). A structured d-DNNF-family compiler,
  used as a stronger baseline. **[impl]**
- **`export_cnf.py` + `d4_pipeline.py`** — Tseitin → weighted DIMACS → the **d4** d-DNNF compiler,
  with a manifest of expected WMC (verified by an independent brute-force counter). d4 bundles the
  x86_64-only PATOH partitioner, so this leg runs on **Linux/x86**. **[planned — staged, see `D4_ON_LINUX.md`]**

**Tractability = treewidth (corrected statement).** For a lineage of treewidth `tw` over `n`
tokens: d-DNNF compilation is `O(n · 2^{O(tw)})` — *linear in n*; OBDD only attains the *pathwidth*
bound, and since `pw ≤ O(tw·log n)` (Korach–Solel), OBDD is `n^{O(tw)}` — *polynomial but with `tw`
in the exponent*. So for **bounded tw both are polynomial**; the gap is **polynomial-degree**
(`n^1` vs `n^{O(tw)}`), not exponential. The exponential SDD≻OBDD separation (Bova 2016) needs
**unbounded** tree-structured tw. Do **not** claim an OBDD blow-up on a bounded-tw family, nor hang
tractability on OBDD. **[analysis + pilot; d-DNNF advantage is asymptotic — needs d4 at scale to
exhibit]**

---

## 8. Factored construction (variable elimination)

The **flat** construction materializes one ⊗ per full derivation (degree = #patterns), so a query
with existential joins blows up (a star projecting out `a,b,c` yields `∏ deg` products per hub).
**Factored** construction eliminates one join variable at a time (join = ⊗, then marginalize = ⊕),
keeping the circuit polynomial of **degree treewidth+1**; the star collapses to `∑ deg`.

- `factor.py` — reference variable elimination (min-fill-ish ordering). **[impl, verified]**
- `factor_native.py` — **engine-native** factored construction as SPARQL `INSERT` passes on an
  unmodified engine (message relations materialized as RDF), so the same idea runs on GraphDB.
  **[impl, verified small-scale]**
- Result **[pilot]**: on layered chains, flat `W^k` vs factored `(k−1)W²`; on the real WatDiv 51k
  subset, flat→factored gate reduction **2.9×** (star), **1.8×** (snowflake), **1.0×** (path — all
  vars projected, nothing to eliminate). WMC provably unchanged (spot-checked identical).

---

## 9. Cost model and predicted trends (pre-registered)

**Two independent knobs.** Construction cost ∝ **#derivations `D`** (`~ b^depth` for branching `b`);
compilation+WMC ∝ **`2^{O(tw)}`** (treewidth of the lineage). Compactness ratio
`T_string/T_circuit ~ b^depth / poly(tw,n)`.

| shape | join graph / tw | #deriv | sharing (factored) | build | compile+WMC | bottleneck |
|---|---|---|---|---|---|---|
| path / linear (L) | path, **tw 1** | `b^ℓ` | `~b^ℓ/ℓ` ↑ | ∝`D` (flat) / poly (fact.) | linear | construction |
| star (S) | star, **tw 1** | `d^k` | `~∏/∑` ↑ | ∝`d^k` / `k·d` | trivial | construction (flat) |
| snowflake (F) | tree, **tw 1–2** | `∏ b^ℓ` | high | ∝`D` / poly | small | construction |
| cyclic / complex (C) | few cycles, **tw 2–3** | data-dep | moderate | ∝`D` | grows w/ tw | balanced |
| grid k×k *(synthetic)* | grid, **tw = k** | large | — | ∝`D` | **2^Θ(k) wall** | compilation |

**Regime boundaries.** Compactness ≈1 until `D`/answer exceeds O(1), then ~exponential in
depth/branching (pilot anchors: WatDiv shallow ≈0.5×; layered depth-12 = **201×**). Compile cliff at
`tw ≈ 20–25`. **WatDiv's S/L/F/C are all low-tw (1–3)** → the standard workload stresses
construction/compactness, not compilation — which is *why* the treewidth study needs synthetic
families (`gen_families.py`), not WatDiv.

---

## 10. Evaluation plan (E1–E7)

Pre-registered in `EVALUATION.md` with per-experiment datasets and predicted results. Two "size"
axes: **data scale** (KG size — only construction/deployability cares) and **provenance scale**
(circuit size + treewidth — governs PQE, decoupled from KG size).

| Exp | proves | dataset | scale | status |
|---|---|---|---|---|
| E1 correctness | exactness | tiny enumerable + `gen_families` | ≤25 tokens | **[impl]** `verify_gallery`/`verify_engine_native` |
| E2 compactness | shared circuit ≪ strings | WatDiv 100M + `gen_families` deep | 100M; 10⁶ deriv | **[pilot]** `bench.py` (201× deep) |
| E3 construction scaling | deployability | WatDiv 10M/100M/1B + real KG | up to 1B | **[pilot]** `bench_engine.py` (GraphDB, 2-hop + 51k) |
| E4 compile vs tw | tractability | `gen_families` only (layered/grid) | tw 1→~25 | **[done]** OBDD + **d4** (`watdiv/e4_results.csv`, d4 on the Linux server; `d4_wmc == expected`) |
| E5 factored vs flat | poly construction | WatDiv 100M star/snowflake + `gen_families` | 100M | **[pilot]** `watdiv_factor.py` |
| E6 non-monotone | exact ⊖ | enumerable + WatDiv/real MINUS/OPTIONAL | ≤25 + 100M | **[impl]** small **and** 10M/100M/TPC-H SF1 (`e6_minus_*.csv`) |
| E7 vs baselines | overall thesis | WatDiv 10M–100M (baseline-limited) + real KG | ≤ what ProvSQL finishes | **[pilot]** toy ProvSQL (`e7_results.csv`); **[planned]** at scale = G2a/G3 |

**"E7 baseline-limited."** A fair head-to-head runs at the largest scale *every* system finishes;
that ceiling is set by the baselines (ProvSQL isn't billion-scale; NPCS/SPARQLprov strings blow up
on deep queries). Present E7 both as a fixed-scale table *and* as a curve showing *where each
baseline OOMs/times out while ours continues* — the wall is itself the result.

**Probability source (threat to validity — state it).** Real probabilistic KGs are scarce; use
extraction confidences (NELL), Wikidata reference ranks, or calibrated synthetic. E1/E2/E4/E5 are
probability-independent (correctness/size), so random weights suffice; E6/E7 use a realistic source.

**Datasets on disk:** `pilot/data/watdiv.10M.nt` (1.4 GB), `watdiv.100M.nt` (15 GB) + `official_q_100M`
(25 queries). **Local disk caveat:** ~25 GB free ⇒ 100M reified (~30–45 GB) does **not** fit here;
100M/1B reify+load+run is a server task; the largest local tier is 10M.

---

## 11. Correctness evidence (what is actually verified)

- **`verify_gallery.py`** — for 12 query shapes (atom, join, union, minus, minus_disjoint,
  minus_union, minus_p2union, minus_chain, opt_left, opt_right, distinct, optional) it builds the
  engine circuit, WMCs each answer over all `2^6` worlds, and checks **`circuit WMC == possible-world
  enumeration`**. The composite MINUS/OPTIONAL shapes are checked against **rdflib's own W3C
  MINUS/OPTIONAL evaluation** (an independent oracle). The three **FILTER** shapes (filter,
  filter_optional, filter_minus) are checked the same way, against rdflib. Plus 6 **rejection guards**
  (FILTER on the string rewriter, FILTER EXISTS on both, LIMIT, right-nested MINUS,
  cross-product OPTIONAL-in-MINUS). **[impl — all pass]**
- **`verify_engine_native.py`** — the drug running example on the deployed-engine circuit:
  `Clopidogrel = 0.358800`, `Omeprazole = 0.774298`, both equal to possible-world enumeration
  (note `Omeprazole ≠ 0.8308` — the shared edge `p3` is counted once). **[impl]**
- **`verify_all.py`** / **`verify_nonmono.py`** — fixture structure/sharing counts and non-monotone
  correctness vs PWE across drug/selfjoin/minus/optional. Java source-level tests separately cover
  product-key canonicalization and independent MINUS/OPTIONAL operator identities. **[impl]**
- **Engine-agnostic:** GraphDB and RDF4J emit the identical drug circuit (19 triples; `p1`,`p3`
  shared). **[impl]**

---

## 12. Complexity summary

- **Rewrite (γ):** `O(query size + arity²)` for the current implementation; the emitted bubble-sort
  comparator network adds `O(arity²)` `BIND`s per product.
- **Construction (engine):** dominated by the engine's join evaluation, ∝ #derivations `D`
  materialized; near-linear in data size for fixed query shape. **[pilot]** GraphDB build was
  sub-second→low-seconds at ~10⁴ triples; scaling to 10⁶–10⁷ is E3.
- **Compile+WMC (client):** `O(compiled size)`; compiled size `n·2^{O(tw)}` (d-DNNF) — tractable for
  bounded tw, `#P`-hard (exponential) for unbounded tw.
- **Compactness:** `T_circuit` polynomial (factored) vs `T_string` ∝ `D` (exponential in depth on
  layered/cyclic data).

---

## 13. Limitations and honest caveats (put these in the paper's scope/threats)

1. **Reification blow-up** — Standard reification triples the data (100M → ~300M); report it, and
   note SPARQL-star as the compact alternative on RDF-star engines.
2. **Serialization vs structure** — the circuit's N-Triples bytes are inflated by 64-hex SHA256 gate
   IRIs (7–15× vs a string CSV on shallow queries). This is a *serialization* artifact (removable by
   relabeling gates with short local ids after construction), **not** the structural compactness
   claim. Report compactness *structurally* (gates+edges vs `Σ derivations × arity`), not as bytes.
3. **Shallow queries** — on shallow tree-like queries the *flat* circuit ≈ strings structurally
   (~0.5×); the compactness win needs deep/recurring derivations (factored, or high sharing). Don't
   claim unconditional compactness.
4. **PQE tractability is treewidth-bounded** — high-tw queries are `#P`-hard for everyone.
5. **MINUS residuals** — right-nested `A MINUS (P MINUS Q)` and two pathological OPTIONAL-operand
   shapes are rejected (safely) by the circuit; the string path handles them.
6. **d4 d-DNNF (run on the server, Linux/x86).** E4 confirms d-DNNF ≪ OBDD at bounded treewidth
   (OBDD ~300k nodes then times out at layered-d32-w2, while d-DNNF stays ≤ ~5k to d64). **Caveat:**
   our pure-Python OBDD uses a naive DFS variable order, so it blows up *faster* than the theoretical
   `n^{O(tw)}` even at fixed tw — the d-DNNF≻OBDD conclusion holds (and is stronger empirically), but do
   **not** claim the OBDD achieves `n^{O(tw)}`; a treewidth-optimal order would, and we do not build one.
   WMC underflows to `0.0` on long chains — report in log-space.
7. **Content-addressing** relies on SHA256 collision-resistance (a standard assumption).
8. **Two rewriters** — the *string* rewriter (NpcsRewriter) reproduces NPCS and handles the full
   fragment (incl. all nested MINUS); the *circuit* rewriter (the contribution) covers
   BGP/UNION/OPTIONAL/chained-MINUS operands, property paths, and rejects the three residuals.
9. **Property-path scope** — the Python reference covers all operators `/ | ^ + * ?`. The *engine* emits
   `+`/`*` over a single predicate or a compound sub-path (`/ | ^`), the zero-or-one `?`, all endpoint
   modes (single-source `O(|V_s|·|E_s|)` when the source is bound), Standard reification (verified on
   cyclic graphs). Remaining engine gaps: **nested** closures (a closure whose sub-path contains a
   closure, or a closure joined with other patterns) and SPARQL-star reification for paths. Zero-length
   semantics are qualified to the terms-in-graph reading. Cross-engine byte-identity uses `CircuitRun`
   endpoint mode; it needs a running SPARQL 1.1 endpoint (e.g. GraphDB), not bundled here.
10. **Non-selective queries at 100M** — unbound (all-pairs) BGP shapes at 100M exceed GraphDB's HTTP
    CONSTRUCT transfer/timeout (E3 unbound-100M failed with IncompleteRead/http; 10M completed but slow,
    e.g. star ≈ 8 min). The circuit route targets *selective/bounded* queries at scale; huge result sets
    need streaming/pagination (future work).
11. **Property-path loop cost** — `CircuitRun` bounds the reach loop by `|V_s|−1` rounds, where `V_s` is
    the source's *reachable* subgraph (discovered live from the reach gates), **not** the global node
    count — so a bounded/sparse-reach path query is feasible and stays *exact* (a simple path in the
    reachable subgraph has ≤ `|V_s|−1` edges). For a **bound source** the base is now materialized **only
    from the reachable subgraph** (G1: a live client-side frontier BFS discovers `V_s`, then a
    `VALUES ?u {…}` clause restricts each base CONSTRUCT — the all-pairs base is never built, avoiding the
    KG-scale OOM). Residual costs: a **variable** source still materializes the all-pairs base (`O(|E|)`),
    and a densely-connected reachable set still needs `~|V_s|` rounds (inherent to exact reachability
    provenance). Not yet done: nested closures; reducing the variable-source all-pairs base; and — a scope
    boundary, not just a cost — the frontier is **IRI-only**: the client BFS reads each node via
    `Value.stringValue()` and re-wraps it as `<…>`, so blank-node / literal path nodes are coerced to IRIs
    and a path *through* such a node can be missed. All benchmark paths are IRI→IRI; general frontiers need
    skolemization or a typed `VALUES`.
12. **RDF multiplicity boundary** — the engine output is a Boolean lineage/PQE circuit. Ordinary RDF
    edges have no occurrence index, so it does not preserve coefficients of the source `N[X]`
    polynomial. That limitation does not change tuple-independent PQE, where repeated event operands
    are idempotent; a general polynomial-provenance export would need explicit occurrence nodes.

---

## 14. Related work (cite + distinguish)

| system | engine | provenance form | exact PQE | datasets evaluated | UNION | OPT | MINUS | paths | aggreg. |
|---|---|---|---|---|---|---|---|---|---|
| **NPCS** (Asma et al., WWW'24) | stock RDF (GraphDB/Stardog) | per-answer spm strings | ✗ | WatDiv, **Wikidata/WDBench (~2B)** — *RDF-native only* | ✓ | ✓ | ✓ | ✗ | ✗ |
| **SPARQLprov** (Galárraga et al., PVLDB'21) | stock RDF | per-answer spm strings | ✗ | WatDiv (100M), **TPC-H** (→RDF), Wikidata (942M) | ✓ | ✓ | ✓† | ✗ | ✓ (§4.4) |
| **ProvSQL** (Senellart et al., VLDB'18) | **modified** PostgreSQL | shared circuit | ✓ | **TPC-H** | ✓ | ⋈⁺ | ✓ (monus) | ✗ | ✓ |
| **SPARQL_circ (ours)** | **stock RDF** | **shared circuit (RDF DAG)** | ✓ | WatDiv, Wikidata, TPC-H (skeleton) | ✓ | ✓ | ✓ | **✓** | ✗ |

†SPARQLprov's released rewriter realizes MINUS as *unguarded* DIFF (`A OPTIONAL B`) — sound on its
shared-variable benchmark, over-subtracts on disjoint operands (confirmed by counterexample). ⋈⁺ = ProvSQL
is relational (SPJU + difference + aggregation); OPTIONAL maps to left-outer-join, recursive paths are not
expressible. **TPC-H is used only by the relational-heritage systems (SPARQLprov via direct mapping,
ProvSQL natively); NPCS never runs it** — it is RDF-native (WatDiv + Wikidata/WDBench). So our TPC-H (E9)
is a comparison vs SPARQLprov + ProvSQL, *not* NPCS; the NPCS comparison lives on WatDiv + Wikidata (E8).

**Takeaways.** Only ProvSQL shares our *shared-circuit + exact PQE*, but it needs a **modified PostgreSQL**;
we deliver the same on a **stock RDF engine**. NPCS/SPARQLprov run unmodified but emit *per-answer* strings
(shared subterms repeated) with no probabilities. **Property-path provenance is uniquely ours**; the one
capability the baselines have that we do not is **aggregation** (ProvSQL, and SPARQLprov §4.4).

Lineage/other: **spm/Geerts (2016)** — algebraic SPARQL provenance, the ⊕/⊗/⊖ semantics + OPTIONAL-via-DIFF
decomposition; we reproduce NPCS's `β` rewriting and replace the leaf ops with gate constructors.
**Green et al. (2007)** — provenance semirings (positive fragment). **Grahne–Thomo (2020)** — provenance
for regular path queries (basis for our path construction). **pSPARQL (Fang 2019)** — fuzzy, not
possible-world; non-competitor. Companion **ProbSPARQL** — the probability model we compute, not a competitor.

---

## 15. Reproducibility
Repo `sparqlcirc/` (Apache-2.0): `engine/` (Java rewriter, RDF4J 4.2.1 + SLF4J), `reference/`
(Python: circuit, `compile_bdd`/`compile_sdd`, `wmc`, `factor*`, benchmarks, `gen_families`,
`verify_*`), `provsql/` (E7 harness). `README.md`, `EVALUATION.md`, `REPRODUCE.md`, `NOTICE`
(clean-room attribution to NPCS). Quick verify: `cd engine && mvn -q package`, then
`cd ../reference && python3 verify_gallery.py` (→ `ALL OK`). The `engine/examples/gallery/` set is
the minimal-operator corpus each `verify_gallery` probe uses.

---

### Appendix A — gate vocabulary (`urn:circuit:`)
`Times` (⊗) with `in`→token(s); `Plus` (⊕) with `feeds`←children and optional `answer`←key literal;
`Minus` (⊖) with `minuend`←⊕, `subtrahend`←⊕, `feeds`→answer ⊕. Gate IRIs: `urn:g:t:` ⊗,
`urn:g:a:` answer ⊕, `urn:g:{p1,p2,sub,m}:` MINUS internals.

### Appendix B — worked example (OPTIONAL, drug-free social KG)
`SELECT ?y ?c WHERE { :Alice :knows ?y OPTIONAL { ?y :city ?c } }` over `t1:Alice knows Bob`,
`t2:Alice knows Carol`, `t6:Bob city Rome`:
```
(Bob, Rome) | ⊗(t1,t6)                 -- AND branch (matched)
(Bob, ∅)    | ⊖(⊕⊗t1, ⊕⊗t6)            -- DIFF branch (Bob-without-city; present iff t1∧¬t6)
(Carol, ∅)  | ⊖(⊕⊗t2, ∅)               -- DIFF branch (Carol has no city; = t2)
```
The full NPCS-method rewrite + this executed output is in `engine/examples/npcs_optional.expected.txt`;
the circuit-method side-by-side is the operator gallery in `reference/workflow.html`.
