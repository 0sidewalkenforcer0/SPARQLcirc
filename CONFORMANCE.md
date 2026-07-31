# Conformance: paper ↔ implementation

Audit of whether the code implements what the VLDB'27 draft
(`SPARQLcircPaper/Jingcheng_s_VLDB27.pdf`) says it does. Scope: **semantics only**
(fragment, rewriting rules, circuit structure, path construction, correctness
statements, compilation). Reported experimental numbers are not re-derived here.

There are **two** implementations, and they are audited separately because they
differ from each other as well as from the paper:

| | what it is | where |
|---|---|---|
| **engine** | the artifact §5 evaluates: SPARQL 1.1 CONSTRUCT plans on an unmodified endpoint | `engine/src/main/java/npcs/` (4.1k lines) |
| **reference** | the Python algebraic reference + oracle | `reference/{gamma,gates,factor,wmc,circuit_io,export_cnf,compiler}.py` (~2k lines of semantic core) |

Claim IDs (P1.1 … P6.6) index the paper's atomic statements; §3 and §4 are the
semantic sections (pp. 2–6). Verdicts: **OK** · **DIFFERS** (semantics preserved,
structure/mechanism differs) · **GAP** (paper claims more than the code does) ·
**BEYOND** (code does more than the paper covers) · **CONTRADICTS** (the two
disagree on an answer's Boolean function or gate identity).

---

## Summary

Nothing in the path-free monotone core is wrong. The rewriting for joins,
unions, projection and filters does what Def. 4.7 says, the ⊖ semantics match
Eq. (3), and the factored plan implements the variable elimination of Prop. 4.9.

Eleven items did not conform. Two **CONTRADICTED** the paper and are demonstrated
below with executable evidence; the rest are generality gaps, structural
divergences that change reported node counts, and a compilation mechanism the
paper describes but the code does not implement.

**Item 2 is fixed** (see below); the answer gate now carries Def. 4.6's pattern
tag. Item 1 is to be corrected on the paper side.

| # | Item | Verdict | Fix side |
|---|---|---|---|
| **1** | `e*` / `e?` zero-length root is `g⊤` in the paper, "source occurs in the graph" in both implementations | CONTRADICTS | paper (code is deliberate + documented) |
| **2** | ~~Answer-gate identifier drops the pattern tag θ ⇒ distinct queries mint identical answer gates~~ | **FIXED** | code |
| **3** | W3C MINUS is implemented and evaluated; the paper proves only algebraic Diff and disclaims the domain-disjointness rewriting | GAP | paper |
| **4** | Thm. 4.13 "arbitrary compositions"; the engine required pure-BGP join operands | **FIXED in code** | code |
| **5** | Closure atoms composed with other operators (Def. 4.7.2, Lem. 4.12, `I_C`, bind-join) are not implemented | GAP | paper |
| **6** | Skolemization of blank nodes (Def. §4.2) is not implemented anywhere | GAP | code (or paper) |
| **7** | Tseitin `T(C)` compiled once + conditioned on `y_r`; the d4 path compiles one CNF **per answer root** | GAP | paper |
| **8** | ⊖ minuend child *set* (Def. 4.2) is never built; both implementations interpose a ⊕ marginal | DIFFERS | paper |
| **9** | Emitted RDF vocabulary and key encoding are not the ones in Def. 4.6 / Def. 4.7 | DIFFERS | paper |
| **10** | Python reference: `⊕` keeps duplicate children (bag), paper Def. 4.3 uses child sets | DIFFERS | reference |
| **11** | TPC-H/ProvSQL comparison runs at **per-row** provenance granularity, not the per-triple model of §1/§3 | GAP | paper |

Plus four **BEYOND** items (code supports what the paper excludes): zero-or-one
`e?`, nested closure in the Python reference, unbound-source closure, and
compound closure-free `e` in the engine.

---

## 1. `e*` zero-length root: `g⊤` vs "the source occurs" — CONTRADICTS

**Paper.** Def. 4.7 clause 2: for `⋄ = *` the plan contributes `ρ ∪ {?y ↦ s}` with
gate term `g_C(ρ,s) = g⊤`. §4.3: "For `e*`, the root is `g⊤` when `v = s`."
Thm. 4.11: "Adding the zero-length root yields the analogous equivalence."
`g⊤ = ⊗(∅)` is the constant ⊤, so `Pr(?y = s) = 1` in every world.

**Code.** Both implementations use a *terms-in-graph* reading: `(u,u)` holds iff
`u` occurs in the graph, so the zero-length gate is the ⊕ of the tokens of all
triples mentioning `u`.

- engine — [CircuitRewriter.java:1089-1096](engine/src/main/java/npcs/circuit/CircuitRewriter.java#L1089-L1096) (`zeroLenConstruct`), [CircuitRewriter.java:290-331](engine/src/main/java/npcs/circuit/CircuitRewriter.java#L290-L331) (`zeroLengthPlan` for `e?`)
- reference — [gamma.py:172-183](reference/gamma.py#L172-L183) (`_zerolen`)

**Evidence.** `G = {t1 = (A,p,B)}`, `Pr(t1) = 0.9`, query `A p* ?y`:

```
answer {'?y': 'B'} -> Pr = 0.9
answer {'?y': 'A'} -> Pr = 0.9      # paper requires 1.0
```

**Why the validation misses it.** §5's correctness check (P6.6) compares against
exhaustive possible-world enumeration, but the oracle
[wmc.py:68-83](reference/wmc.py#L68-L83) encodes *the same* terms-in-graph
convention (`clo |= {(u,u) for tr in T ...}` over the world's **active** triples).
The check therefore cannot separate the two readings.

**Assessment.** The implementation choice is the defensible one — under W3C
semantics a zero-length path ranges over the terms of the graph, and a term that
occurs in no triple of a world is not a term of that world — and it is already
documented as a qualified reading in
[TECHREPORT.md:222-224](TECHREPORT.md#L222). The paper's `g⊤` is the thing to
change: §4.3, Def. 4.7 clause 2 and Thm. 4.11 all state it.

---

## 2. Answer-gate identifier drops the pattern tag — CONTRADICTS → FIXED

**Paper.** Def. 4.6: `id_⊕^θ(v1..vk) = (⊕, θ, τ(v1),…,τ(vk))`, where θ is the
canonical serialization of the subpattern; "Pattern tags distinguish gates
belonging to different algebra nodes." Def. 4.7 clause 5: the projection gate is
`id_⊕^P(W̄)` — keyed by the algebra node **and** the projected tuple.

**Code.** The answer gate uses the constant tag `"A"` and the binding only —
no pattern tag:

```java
q.append(bindIri(ans, "urn:g:a:", idKey(W, "A")));   // CircuitRewriter.java:348, 464
```
[CircuitRewriter.java:732-737](engine/src/main/java/npcs/circuit/CircuitRewriter.java#L732-L737),
same key in [FactoredBgpRewriter.java:329](engine/src/main/java/npcs/circuit/FactoredBgpRewriter.java#L329).

So the answer-gate IRI is a function of `(projected variable names, bound terms)`
alone. Two semantically unrelated queries mint the same root.

**Evidence.** Two different queries over `reference/data/drug.reified.ttl`:

```
Q_A: SELECT ?z WHERE { <urn:d:Aspirin> <urn:d:iw> ?z }
Q_B: SELECT ?z WHERE { ?z <urn:d:iw> <urn:d:Metformin> }

Q_A  ?z=Warfarin -> <urn:g:a:2298a38e…380e>
Q_B  ?z=Warfarin -> <urn:g:a:2298a38e…380e>     # byte-identical
```

Merging the two circuits (which `CIRCUIT_PERSIST` into a shared store, or any
multi-query circuit cache, does) silently corrupts both:

```
Q_A     ?z=Warfarin  0.92      (= p1)
Q_B     ?z=Warfarin  0.87      (= p2)
merged  ?z=Warfarin  0.9896    (= 1-(1-0.92)(1-0.87))   ← wrong for both
```

**Why it is a code defect and not an over-specified paper.** The asymmetry in
Def. 4.6 — `id_⊗` carries no pattern tag, `id_⊕^θ` and `id_⊖^θ` do — is principled.
A ⊗ gate's identifier *is* its sorted child multiset, so equal identifier implies
equal Boolean function and a collision across queries is safe sharing. A ⊕/⊖
identifier is `(θ, binding)` and says nothing about the `c:feeds`/`c:minuend` edges
that will later accumulate on it, so a collision is aliasing. Every other ⊕/⊖ in
the implementation already carries a tag — `P1@`/`P2@`/`SUB@`/`M@` from
`bgpSemanticKey`, `BASE@`/`MARG@` from `FactoredBgpRewriter`, `R|<fp>` from the
path plan, the latter added with an explicit comment about cross-query
contamination. The answer gate was the sole exception.

### The fix

`CircuitRewriter` now computes one pattern tag per query, `"A@" + SHA256(θ)`, where
θ is a canonical prefix serialization of the **normalized** body plus the projected
variable tuple (`querySemanticKey` / `answerTag`). Commutative operators are sorted
inside θ — BGP conjunction via `bgpSemanticKey`, UNION alternatives here — so a
harmless re-association by the parser cannot change it. The tag is threaded to every
answer-gate site so they stay byte-identical: `bgp`, `minusRoot`, `zeroLengthPlan`,
`FactoredBgpRewriter.answerQuery`, and `PathQuery.projectAnswers` (paths had the same
defect at the answer level even though their reach gates were already isolated).

The two properties pull in opposite directions and both are now pinned by tests:

* **isolation across queries** — `CircuitFilterTest.filterOnANonProjectedVariableDoesNotAliasTheUnfilteredAnswer`
* **convergence within one query** — `CircuitRewriterTest.answerGatesAreIsolatedPerQueryButSharedAcrossBranchesOfOne`
  (UNION branches, and flat vs factored, still land on one shared root)

Measured effect on the two demonstrations above, and on two MINUS queries sharing a
left operand:

```
Q_A / Q_B      Q_A ?z=Warfarin 0.92, Q_B 0.87, merged now keeps 4 distinct roots
filtered pair  unfiltered 0.4375, filtered 0.25, merged now keeps both

gate class   shared pre-fix  shared post-fix   expected
⊗                         2                2   share (same derivations)
⊕_{P1}                    2                2   share (same operand pattern)
⊕_{P2} / SUB / ⊖          0                0   isolate
answer ⊕                  2                0   isolate  ← the fix
```

Only the answer layer moved: in `reference/data/drug.circuit.nt` all three ⊗ gate
IRIs are byte-identical to before, and the only other changed lines are the
`c:feeds` edges that point at the new roots.

**A consequence worth stating in the paper.** θ includes the operand's FILTERs, so
a filtered and an unfiltered query no longer share an answer root. That is *required*
rather than incidental: with the condition on a non-projected variable the two
queries give one binding genuinely different derivation sets (0.25 against 0.4375
above). Def. 4.7 clause 7's `g_{Filter_φ(P')} = g_{P'}` therefore holds for the
filter's own gate but must not be read as "an enclosing projection's tag ignores the
filter". A pre-existing test asserted the stronger reading and has been corrected.

**Residual, unchanged by the fix.** θ is derived from the query, not from the graph,
so the same query over two different base graphs still mints the same answer-gate
IRI (with different children). Under the paper's fixed-`G` model this is out of
scope; `P1@`/`SUB@`/`M@` have always had the same property. A cross-dataset circuit
store would need a dataset identifier in the scope.

**Fixture churn.** Every answer-gate IRI changed, so `reference/data/*.circuit.nt`,
the plan captures, and `reference/graphdb/*` were regenerated. `verify_all.py`,
`quick_verify.py` (171/171 fresh-circuit checks, paths included) and the 23 Java
tests pass, and the GraphDB-built circuit is byte-identical to the in-memory one.

---

## 3. W3C MINUS is implemented and measured; only Diff is proved — GAP

**Paper.** §3: "Our formal results concern this algebraic difference operator;
they do not assert a separate rewriting for the domain-disjointness condition of
W3C Minus." Def. 4.7 clause 6 and Thm. 4.13 are about `Diff`.

**Code.** Both implementations implement the *guarded* W3C operator for
user-level `MINUS`, and unguarded `Diff` only as OPTIONAL's negative branch:

- engine — `minusPlan` skips right branches that share no variable with `P1`, and
  makes MINUS a no-op when none overlap
  ([CircuitRewriter.java:362-387](engine/src/main/java/npcs/circuit/CircuitRewriter.java#L362-L387));
  `optionalPlan` is explicitly unguarded
  ([CircuitRewriter.java:471-492](engine/src/main/java/npcs/circuit/CircuitRewriter.java#L471-L492))
- reference — `eval_minus(..., guard=True)` by default, `guard=False` from
  `eval_optional` ([gamma.py:86-119](reference/gamma.py#L86-L119))

The reduction is sound (`MINUS = Diff` behind a shared-variable guard for BGP
operands, since every variable is then always bound) and is written up in
[TECHREPORT.md §5](TECHREPORT.md#L255).

**Assessment.** The code is right; the paper under-claims and then relies on the
un-proved operator: §5.1/§5.2 report five WatDiv **minus** templates M1–M5, §5.3
attributes the 100M construction failures to minus, and §5.4's `Mminus` skeleton
is a MINUS. The one-sentence disclaimer in §3 should become the small lemma the
tech report already contains, or the evaluation should stop calling these MINUS.

---

## 4. Composition generality — GAP

**Paper.** Thm. 4.13: "For every query `Q` formed from triple patterns and
source-bound closure atoms **by the supported operators**". Def. 4.7 clause 3
allows `Join(P1,…,Pn)` over arbitrary subpatterns.

**Code (engine).** Join operands must be pure BGPs. `assertPureBgp`
([CircuitRewriter.java:830-852](engine/src/main/java/npcs/circuit/CircuitRewriter.java#L830-L852))
rejects a nested `Union`/`LeftJoin`/`Difference`/`Extension`/subquery/property
path in BGP position. Composite MINUS operands are first rewritten by four
algebraic identities in `normalize`
([CircuitRewriter.java:153-226](engine/src/main/java/npcs/circuit/CircuitRewriter.java#L153-L226)),
and three residual shapes are rejected outright: right-nested
`A MINUS (P MINUS Q)`, and two OPTIONAL-as-MINUS-operand shapes.

**Code (reference).** `gamma.eval_q` **is** fully compositional
([gamma.py:225-233](reference/gamma.py#L225-L233)) — `eval_join`/`eval_union`/
`eval_minus` recurse into arbitrary subqueries. So the Python reference matches
Thm. 4.13 and the engine does not.

### Closed in code, not in the paper

Rather than scope Thm. 4.13 down, the engine was made compositional. Three changes,
each byte-identical on every shape that already worked:

1. **`Join(A∪B, Z) ≡ (A⋈Z) ∪ (B⋈Z)`** — a UNION may be a join operand.
2. **`A OPT B ≡ Join(A,B) ∪ (A DIFF B)`**, §3's own definition, applied in
   `normalize` instead of in a dedicated planner. Both disjuncts are *total* on a
   fixed scope, which is what lets an OPTIONAL sit under or over another operator.
   Guarded (user MINUS) and unguarded (OPTIONAL's anti-join) differences then
   coexist, so the kind travels on the node as `UnguardedDifference`.
3. **Operand materialization** — Def. 4.7's `reif(P, g_P)` for a composite `P`. The
   paper already says what it means, in clause 2, for closure atoms: "a relation
   whose ordinary columns contain the extensions and whose gate column contains the
   corresponding root". A `Difference` in join-operand position is now planned as
   usual but published as a private `urn:sc:` row relation carrying its binding
   columns and its ⊖ gate, and the join reads that relation, contributing the ⊖ gate
   as a single ⊗ child. The mechanism is `FactoredBgpRewriter`'s message relations,
   already exercised by every factored query.

Materialization applies to a `Difference`'s minuend and subtrahend as well as to a
join's operands, so it composes to any depth: `planOperand` and `diffCore` recurse
through each other, and a composite operand's marginal ⊕ is a sink over the single
gate its relation already carries.

**Every composition of Join, Union, Filter, MINUS and OPTIONAL now builds.** Not a
hand-written matrix: the shapes are ENUMERATED. All 86,016 binary-operator trees up
to five operators build in both construction modes, as do all 4,495 trees up to
three constructors once FILTER is added as a unary one — the latter pinned as
`CircuitRewriterTest.everyCompositionUpToThreeConstructorsBuilds`. Boolean functions
are checked against possible-world enumeration, the Python reference, and (for
filters, which the Python DSL does not model) an independent rdflib oracle that runs
the plain query over every possible world.

Enumeration was necessary, not decorative. The hand-written matrix stopped at two
levels of nesting and missed that three OPTIONALs put a JOIN in operand position; it
had no filters at all and missed three more shapes. Both gaps were found by accident
before the sweep existed.

The most consequential shapes this unlocked are the ones ordinary queries hit:
**two OPTIONALs**, and an operator written anywhere in its group rather than only
last. The W3C group translation folds left to right, so `{ A OPTIONAL {B} . C }`
parses to `Join(LeftJoin(A,B), C)` while `{ A . C OPTIONAL {B} }` parses to
`LeftJoin(Join(A,C), B)` — only the second used to build, and nothing told the user
to reorder.

Two facts worth keeping:

* θ must be computed from the query's own algebra, not from the normalized body.
  Keying it on the normal form would move every answer-gate IRI whenever
  `normalize` changes — expanding OPTIONAL did exactly that before the fix.
* The chained-difference identity `(A ∖ B) ∖ P ≡ A ∖ (B ∪ P)` may only merge the
  two subtrahends when both removals ask the same question of a candidate: same
  kind, or an unguarded inner under a guarded outer. A guarded inner under an
  unguarded outer keeps the nesting and is materialized instead.

**Paper-side remainder.** The normal form is still not described anywhere. §4.2
should state it, because it is what makes Def. 4.7 executable: expand OPTIONAL,
push UNION to the top, materialize a composite operand as a relation. Presenting
`reif(P, g_P)` uniformly — inline for a triple pattern, a materialized relation for
anything else — would also remove the special-case feel of clause 2.

---

## 5. Closure atoms in context — GAP

**Paper.** Def. 4.7 clause 2 defines a closure atom against an input relation
`I_C` supplied by its bind-join predecessor; §4.3 says "a source variable from an
enclosing pattern invokes the same construction for each source binding"; the
materialized `reif(C, g_C)` "lets the enclosing cases consume path roots in the
same manner as triple-pattern gates" (Lem. 4.12, then Thm. 4.13).

**Code (engine).** A path must be the entire query pattern:

```java
if (!(outerProjection(te).getArg() instanceof ArbitraryLengthPath))
    throw new UnsupportedOperationException(
        "Property path must be the whole pattern for now (no join/union/minus with a path yet).");
```
[CircuitRewriter.java:938-939](engine/src/main/java/npcs/circuit/CircuitRewriter.java#L938-L939)

There is no `I_C`, no bind-join, and no per-source-binding invocation. The
subject either is a constant (then a client-side BFS discovers `V_s`
and restricts the base relation) or is a free variable (then the base relation is
all-pairs) — [CircuitRun.java:298-343](engine/src/main/java/npcs/circuit/CircuitRun.java#L298-L343).

**Code (reference).** `eval_path` composes freely, but by evaluating the full
all-pairs relation and filtering — not by the paper's bind-join construction
([gamma.py:211-222](reference/gamma.py#L211-L222)).

**Assessment.** §5 is consistent with the artifact (all reported path queries are
standalone: `Q7397 wdt:P279+ ?y`, `Q60 wdt:P131+ ?y`, `p+`/`p*` on a WatDiv
subgraph). §4.2/§4.3's `I_C` machinery, the bound-source condition of §3
(P1.5/P1.6), and Lem. 4.12's role in Thm. 4.13 describe a construction that does
not exist yet. §5's "the implementation does not yet stratify nested closures"
caveat covers strictly less than the actual gap.

Related **BEYOND** items in the same area:

- **Unbound source.** §3 excludes "closure atoms that violate the bound-source
  condition". The engine supports a variable source by materializing the
  all-pairs base ([CircuitRewriter.java:1110-1111](engine/src/main/java/npcs/circuit/CircuitRewriter.java#L1110-L1111)),
  and the Python reference always computes all-pairs. Both go outside the fragment
  the theorems cover.
- **Compound `e` in the engine.** §4.3 states "the implementation evaluated in
  Section 5 specializes to `e = p`". It does not: `pathQuery` decomposes a compound
  sub-path into UNION branches of statement patterns with the endpoints substituted
  by `?u`/`?v`, and builds one ⊗ per branch derivation
  ([CircuitRewriter.java:945-990](engine/src/main/java/npcs/circuit/CircuitRewriter.java#L945-L990)).
  That covers `/`, `|`, `^`, but it is *not* the `b^e_uv` recursion of Eq. (4) —
  no midpoint enumeration over `D_G`, so Lem. 4.10's `O(|e||D_G|³)` bound does not
  describe it.
- **Nested closure.** §3 excludes it and the engine rejects it fail-fast (via
  `unionBranches → collect → assertPureBgp`). The Python reference **accepts** it —
  `eval_pexpr('plus', …)` recurses into any sub-expression including another
  closure ([gamma.py:203-206](reference/gamma.py#L203-L206)).
- **Zero-or-one `e?`.** §3 excludes it. Both implementations support it
  (`zeroLengthPlan`; `gamma` `('opt', e)`).

---

## 6. Skolemization is not implemented — GAP

**Paper.** §4.2: "The rewriting creates no blank nodes. Before loading either
endpoint, the client applies one injective skolemization map `sk : B_G → I` whose
image lies in a fresh IRI namespace to the input blank nodes and stores its
inverse … the client applies `sk⁻¹` to projected answer terms."

**Code.** No skolemization step exists. `grep -ri skolem` over the whole
repository returns two hits, both about a *future* fix for blank-node path
frontiers. Blank nodes reach the gate key as their engine-assigned label:

```java
IF(isBlank(?v), CONCAT("b", SHA256(STR(?v))), …)   // termHash
```
[CircuitRewriter.java:613-622](engine/src/main/java/npcs/circuit/CircuitRewriter.java#L613-L622)

**Assessment.** Blank-node labels are not stable across stores, so for any graph
containing blank nodes the byte-identity result of §5.2 (RQ2) does not follow.
The WatDiv/TPC-H/Wikidata inputs happen to be blank-node-free, so no measurement
is affected — but the claim as stated is unsupported until either `sk` is
implemented or §5.2 is scoped to ground graphs.

---

## 7. Tseitin encoding: compiled once, or once per answer? — GAP

**Paper.** §4.1: build `T(C)` with a fresh `y_g` per gate, weight `Pr(x_t)` /
`1 − Pr(x_t)` on base literals and 1 on both aux literals; "After compiling `T(C)`
once, conditioning the compiled form on `y_r = ⊤` gives the weighted model count
of answer root `r` **without recompilation**."

**Code.** Neither the OBDD path nor the d-DNNF path does this.

- **d-DNNF / d4 path.** `export_cnf.export(circ, root, P)` walks the cone of **one**
  root and appends a unit clause asserting it
  ([export_cnf.py:20-77](reference/export_cnf.py#L20-L77)). Every caller
  (`compile_portfolio`, `g6_d4_real`, `e4_sweep`, `level1_d4_headtohead`,
  `paper/treewidth_evidence`) calls it once per answer. This is precisely the
  per-answer-cone scheme §5.4 Fig. 8(c) is supposed to be the *baseline* for.
- **OBDD / CUDD path (production).** `compile_many(mode="shared")` uses one CUDD
  manager and one memo over the source DAG, keeping the roots as separate BDD
  outputs ([compiler.py:405-536](reference/compiler.py#L405-L536)). This does
  achieve "compile once for all answers", but by BDD-node sharing, not by Tseitin
  conditioning. No `y_r` conditioning exists anywhere.

**Consequence for §5.3.** `tw_T(C)`, "the treewidth of the primal graph of the
specified Tseitin encoding", is measured on the **per-root** CNF that
`export_cnf` produces ([paper/treewidth_evidence.py](reference/paper/treewidth_evidence.py)),
not on `T(C)` for the whole circuit. The quantity plotted in Fig. 7 is well
defined, but it is not the one §4.1 defines.

**Assessment.** Rewrite §4.1's Tseitin paragraph to describe what the artifact
does — a shared multi-root compilation for the OBDD path, a per-root weighted CNF
for the d4 path — or implement the conditioning. As written, the paragraph
describes a third mechanism that is in neither.

---

## 8. ⊖ minuend child set is never built — DIFFERS

**Paper.** Def. 4.2: an ⊖-gate has a minuend-child **set** `pos(g)` and one
subtrahend child. Def. 4.7 clause 6 keys `g_P = id_⊖^P(v̄_P1)` and emits
`c:minuend g_P1` per row of `reif(P1, g_P1)`, so all of `P1`'s derivations
accumulate in `pos(g)`.

**Code.** Both implementations interpose an explicit ⊕ marginal and make it the
single minuend:

- engine — `marginalPlus` builds `⊕_{P1}(V1)`, `minusRoot` emits
  `⊖(⊕_{P1}, ⊕_{sub})` ([CircuitRewriter.java:446-511](engine/src/main/java/npcs/circuit/CircuitRewriter.java#L446-L511))
- reference — `minus(m, s)` is strictly binary
  ([gates.py:71-76](reference/gates.py#L71-L76)), consumed as
  `('minus', (m, s))` by `circuit_io`, `export_cnf` and `compiler`

Semantically identical (`⊖({⊕(C)}, d) ≡ ⊖(C, d)` under Eq. (3)) but one extra gate
per difference answer, and `pos(g)` as a set is dead notation.

Two more structural divergences of the same kind, all semantics-preserving but all
changing the node counts Fig. 3 / Fig. 4 report:

- **n-ary ⊗ in the factored plan.** Prop. 4.9's `q_z(α,a) = ⊗{F(α ∪ {z↦a})}` is
  n-ary; both implementations fold the involved factors with **binary** joins
  ([FactoredBgpRewriter.java:199-201](engine/src/main/java/npcs/circuit/FactoredBgpRewriter.java#L199-L201),
  [factor.py:89-94](reference/factor.py#L89-L94)), producing a chain of 2-child ⊗
  gates.
- **Base factor rows.** §4.2 says a base factor's row "points to the leaf gate
  `x_{θ(t_j)}`". Both wrap it in a ⊕ first
  ([FactoredBgpRewriter.java:217-244](engine/src/main/java/npcs/circuit/FactoredBgpRewriter.java#L217-L244),
  [factor.py:41](reference/factor.py#L41)) — necessary only when one binding has
  several occurrence tokens.

---

## 9. Emitted vocabulary and key encoding ≠ Def. 4.6 / Def. 4.7 — DIFFERS

**Vocabulary.** Def. 4.7 clauses 3–5 all write `c:in`:
`{g_P a c:Plus ; c:in g_{P_i}}`. The implementation emits `c:in` for ⊗ only, and
reverses the edge for ⊕: `g_{P_i} c:feeds g_P`. `circuit_io.parse` reads ⊕
children from the inverted `feeds` index
([circuit_io.py:100-115](reference/circuit_io.py#L100-L115)). `c:minuend` and
`c:subtrahend` do match. Undocumented in the paper: `c:feeds`, `c:answer`,
`c:binding`, `c:var`, `c:val`, `c:rlvl`, `c:rpath`, `c:rfrom`, `c:rto`, and the
private `urn:sc:*` factor-message relations.

**Key encoding.** Def. 4.6 specifies "one-byte kind tag, then decimal byte length,
then colon, then component bytes", with a literal's lexical form, datatype and
language tag as three separate length-prefixed components. That framing appears in
the code only in the *compile-time* fingerprints (`part(v) = v.length() + ":" + v`,
[CircuitRewriter.java:694-696](engine/src/main/java/npcs/circuit/CircuitRewriter.java#L694-L696)).
The keys that actually name gates are built inside SPARQL as concatenations of
fixed-width SHA-256 hashes of each part:

```
termHash(v) = SHA256("v=" ‖ IF(isLiteral, "l" ‖ SHA256(STR) ‖ SHA256(DATATYPE) ‖ SHA256(LCASE(LANG)), …))
id⊗         = "T" ‖ "|" ‖ sorted child SHA-256 hex …
```

Both are injective (fixed width instead of length prefixes), and the paper's
"the theory treats canonical keys as injective symbolic identifiers" covers the
substitution. But Def. 4.6 as written does not describe the artifact, and its
lower-cased language tag (P3.4) is the one detail the code does implement
(`LCASE(LANG(...))`). One conformance detail is genuinely missing: the paper's
`id_⊗` has no pattern tag and the code agrees; the paper's `id_⊕^θ` has one and
the code's answer gate does not — see item 2.

**Stale citations.** Code comments cite an earlier numbering: `Filters.java` and
`assertPureBgp` cite "Def. 4.5, clause 6" for the filter rule (now Def. 4.7 clause
7), `Reification.java` cites "Definition 4.2" for `Reify` (now the `reif` of §4.2).

---

## 10. Python reference uses bag `⊕`, the paper uses child sets — DIFFERS

Def. 4.3 uses child **sets**, and §4.1 justifies it: "repeated derivations with
identical Boolean provenance do not change the answer event". The engine gets this
for free (RDF is set-semantic, and `circuit_io.parse` sorts children into tuples).
The Python reference deliberately keeps duplicates:

```python
cs = sorted(cs)   # commutative; duplicates KEPT (no idempotence: g⊕g = 2g in N[X])
```
[gates.py:62-69](reference/gates.py#L62-L69)

Harmless for WMC (`a ∨ a = a`), but it means the two implementations do not count
the same nodes — relevant wherever the reference produces the sizes in Fig. 3/4.
Two related reference-only divergences: `⊕` gates are content-addressed **by their
children** rather than by `(θ, binding)` as in Def. 4.6, so the reference merges
strictly more than the paper's scheme; and the path constructors apply absorption
`a + (a·b) = a` ([gates.py:85-96](reference/gates.py#L85-L96)), a simplification
Eq. (4)–(6) do not contain.

---

## 11. TPC-H comparison runs at per-row granularity — GAP

§1 and §3 fix the data model: "every triple is a Bernoulli random variable",
`X = {x_t | t ∈ G}`. §5.4 compares against ProvSQL on TPC-H without qualifying it.
The TPC-H pipeline uses the `naryrel` scheme, in which the provenance token is the
**subject** (the row entity), the data stays unreified, and every triple about a
row shares that row's token:

```java
NARYREL: "s p o . BIND(s AS ?prov)"   // Reification.java:70-79
```
[e9_tpch.py](reference/e9_tpch.py), [r8_3_reconvergent.py:37](reference/r8_3_reconvergent.py#L37),
documented in [REIFICATION.md](REIFICATION.md).

This is the right call for a fair comparison — it matches SPARQLprov's n-ary
direct mapping and ProvSQL's per-tuple granularity — but §5.4 should say so,
because per-row uncertainty is a different probabilistic model from the one §3
defines. Two of the five reification schemes the code ships (`naryrel`,
`Wikidata`) are not among the three the paper names.

---

## What conforms

For the record, the following were checked and match.

| Claim | Verdict | Where |
|---|---|---|
| P1.1 fragment = TP + Join/Union/Proj/Filter/Diff | OK | `assertPureBgp`, `branchPlan`, `gamma.eval_q` |
| P1.2 `Opt = Union(Join, Diff)` | OK | `optionalPlan`, `gamma.eval_optional` |
| P1.3 aggregation / Extend / filtered left join excluded | OK | `assertPureBgp` rejects `Extension`; `normalize` rejects the genuine filtered left join and pushes a right-only condition into the operand |
| P1.8 path set semantics | OK | content-addressed gates; `oplus` dedup |
| P1.10 Diff subtrahend over all compatible ν | OK | `subFeeds` joins P1×P2 on shared vars; `gamma._compatible` |
| P1.11 `a ↛ b = a ∧ ¬b` | OK | `wmc._boolean`, `export_cnf` minus clauses, `compiler._compile_roots` |
| P2.2 empty ⊕ = ⊥, empty ⊗ = ⊤ | OK | `gates.plus/times` → CONST0/CONST1; `export_cnf` empty-OR/empty-AND clauses |
| P2.3 Eq. (3) | OK | all three evaluators |
| P2.5 set-valued children | OK (engine) / DIFFERS (reference, item 10) | `circuit_io.parse` sorts into tuples |
| P2.6 congruence merging | OK | content addressing throughout |
| P3.1 RDF-star / standard reification / named graphs | OK | `Reification` enum |
| P3.3 SHA-256 digest prefixed by operator kind | OK | `urn:g:t:` / `urn:g:s:` / `urn:g:a:` / `urn:g:m:` / `urn:g:r:` prefixes |
| P3.4 lower-cased language tag in τ | OK | `SHA256(LCASE(LANG(...)))` |
| P3.7 `id_⊗` = sorted children, no pattern tag | OK | `emitSortedProdKey`, `gates.times` |
| P3.8 `reif(P)` preserves filters | OK | `Filters.of` + `reify(Block…)`; rejects a condition its own group does not bind |
| P3.9 `reif(Diff(P1,P2)) = reif(P1)` | OK | `minusRoot` reifies only `L` |
| P3.11 triple pattern contributes no query | OK | |
| P3.12 Join → one CONSTRUCT, `c:Times`/`c:in` | OK | `bgp` |
| P3.13 Union → one CONSTRUCT per branch into a shared ⊕ | OK | `branchPlan` |
| P3.16 subtrahend typed `c:Plus` unconditionally (absent subtrahend ⇒ empty ⊕ = ⊥) | OK | `minusRoot` emits `?sub a c:Plus` in the template |
| P3.17 Filter builds no gate, changes no gate identity | OK (engine, modulo the fingerprint note below) | `Filters` |
| P3.19 outer projection gate is the root | OK | |
| P3.20 acyclic; non-path steps order-independent | OK | content addressing + level indices |
| P4.2/P4.3 `U_z`, `q_z`, `h_z` | OK | `eliminate`, `factor.factored_bgp` |
| P4.4 min current scope | OK | both compute `\|U_z ∪ {z}\|`, a uniform +1 ⇒ identical ranking |
| P4.6 private namespace, one INSERT per pass | OK | `urn:sc:*`, `CircuitRun.executeConstructionPlan` |
| P4.7 read-only endpoint uses the flat plan | OK | `CIRCUIT_READONLY` → error directing to `--construction=flat` |
| P5.2–P5.5 base circuit shape | OK (reference) / DIFFERS (engine, item 5) | `gamma.eval_pexpr` |
| P5.8/P5.9 level recurrence | OK | `PathQuery.step` (A)+(B) = Eq. (6); `gamma._closure` |
| P5.10 level-indexed keys ⇒ acyclic on cyclic data | OK (engine) | `reachIri` keys on `(fp, level, from, to)`; the reference instead relies on construction order |
| P5.12 one CONSTRUCT for products, one for unions per level | OK | `PathQuery.step` returns exactly two |
| P6.1 round bound | OK | `cap = \|V_s\| − 1`, conservative w.r.t. the paper's `n` |
| P6.4/P6.5 exact WMC, no approximation fallback | OK | `compiler`, `ddnnf_wmc`; failures are resource errors |

One caveat on P3.17: the paper says `g_{Filter_φ(P')} = g_{P'}`, i.e. a filter
leaves gate identity untouched. The engine folds an operand's rendered filters into
the `bgpSemanticKey` that tags the `⊕_{P1}` / `⊕_{P2}` / `SUB` / `M` gates
([CircuitRewriter.java:668-672](engine/src/main/java/npcs/circuit/CircuitRewriter.java#L668-L672)),
and now into the answer gate's θ as well. That is *required*, not incidental: two
operands differing only by a filter denote different relations, so pouring both into
one ⊕ would be wrong — inside one query for `A MINUS (P FILTER φ1)` against
`A MINUS (P FILTER φ2)`, and across queries whenever the condition touches a
non-projected variable (item 2). Clause 7 is sound for the filter's *own* gate; it
should not be read as saying an enclosing ⊕'s pattern tag ignores the filter.

---

## Also outside the paper

Rejections the engine performs that §3's exclusion list does not mention:
`LIMIT`/`OFFSET`/`ORDER BY` ([CircuitRewriter.java:891-906](engine/src/main/java/npcs/circuit/CircuitRewriter.java#L891-L906)),
`FROM`/`FROM NAMED` and `GRAPH` patterns
([QueryGuard.java](engine/src/main/java/npcs/rewrite/QueryGuard.java)),
`EXISTS`/`NOT EXISTS` and any FILTER expression outside a rendered SPARQL-1.1 core
([Filters.render](engine/src/main/java/npcs/circuit/Filters.java#L126-L174)),
a path whose subject and object are the same variable, and a path under any
reification scheme other than `Standard`. `DISTINCT` is accepted as a no-op.
