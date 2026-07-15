# Factored circuit construction — theory, scope, and implementation plan

*What factoring is, exactly which SPARQL fragment it applies to and why, the current implementation
status, and a prioritized plan for finishing it. Grounded in `reference/factor.py`,
`reference/factor_native.py`, `reference/gates.py`, and `engine/.../CircuitRewriter.java`.*

## 0. TL;DR

- **Flat** construction builds **one ⊗ per derivation**, so the circuit is `∝ #derivations = |data|^{#patterns}`
  — exponential in query size.
- **Factored** construction is **variable elimination (sum-product)**: eliminate each non-output join
  variable once (⊗ the relations that mention it, then ⊕-marginalize it away). The circuit becomes
  `∝ |data|^{tw+1}` (**polynomial for bounded treewidth**) while representing the **same provenance
  polynomial** — verified `WMC(flat) == WMC(factored) == PWE` (E5).
- It applies to the **monotone conjunctive fragment (BGP)**; it extends cleanly to **projection**
  (marginalize) and **UNION** (⊕). For **MINUS/OPTIONAL** you factor the *operands* and keep the
  non-monotone **⊖ at the top**. **Property paths** are recursive and use a *different* sharing mechanism.
- **Status:** flat is the **Java engine default for every operator**; factored is a **Python-only
  reference** (`factor.py` in-memory + `factor_native.py` SPARQL-`INSERT` prototype), and **BGP-only**.

---

## 1. Background: two independent cost knobs, and the semiring

PQE has two costs that move independently:

1. **Construction** (the engine builds the circuit) `∝ #derivations D`. For query depth `d`, branching `b`,
   `D ~ b^d`. This is what factoring attacks.
2. **Compilation + WMC** `∝ 2^{Θ(tw)}` in the *treewidth* of the lineage — a separate axis (E4), *not*
   fixed by factoring.

The provenance circuit is a Boolean circuit with gates `⊗ = ∧`, `⊕ = ∨`, `⊖(a,b) = a ∧ ¬b`. The
**monotone fragment** — everything except `⊖` — lives in the positive/absorptive semiring `(∧, ∨)`
(PosBool). Factoring is an identity **in that semiring**; `⊖` is the one operator that leaves it.

---

## 2. Theory: factoring = variable elimination

### 2.1 The identity

A BGP's provenance is a sum-of-products over all derivations. Factoring rewrites it using **distributivity**
`a ⊗ (b ⊕ c) = (a ⊗ b) ⊕ (a ⊗ c)` *in reverse* — pulling a shared factor out of a sum. Concretely, to
eliminate a non-output variable `x`: **join** (⊗) all relations mentioning `x`, then **marginalize**
(⊕ over `x`'s values). `reference/factor.py`:

```
base_relations : each triple pattern → { binding-tuple : ⊕(matching tokens) }
join(A, B)     : ⊗ on the shared vars, ⊕-accumulate on key collision
marginalize(R, x): group by the remaining vars, ⊕ the gates in each group
factored_bgp   : while elim: pick x (min-fill-ish), join relations with x, marginalize x; then join the rest
```

### 2.2 Why it is correct

Because `∧` distributes over `∨` and both are commutative/associative, the factored expression and the
flat sum-of-products denote the **same Boolean function** (equivalently the same provenance polynomial in
`N[X]`; `gates.py.plus` keeps duplicates — *no* idempotence — so counting semirings are preserved too). So
WMC is identical: **factoring changes the representation, not the value.** This is exactly what
`watdiv_factor.py` (E5) asserts: `WMC(flat) == WMC(factored) == PWE`.

### 2.3 Why it is small

Two mechanisms:

1. **Early marginalization** turns `∏deg` into `∑deg`: an existential joined *after* combining costs a
   product; marginalized *first* it costs a sum.
2. **Content-addressed sharing** (`gates.py._put`, key = `sha("TIMES|"+sorted(child-ids))`): structurally
   equal intermediate gates collapse to one id, so a partial product shared by many derivations is stored
   **once**. Flat never *forms* those partial products, so it cannot share them.

Result: with a min-fill elimination order realizing tree-width `w`, every intermediate relation has
`≤ w+1` variables, so the circuit is `O(|data|^{w+1})` — polynomial for bounded `w`, vs the flat
`|data|^{#patterns}`.

### 2.4 The algebraic boundary

Variable elimination is valid **only in the commutative semiring** `(⊗, ⊕)`. The moment `⊖` (monus /
`∧¬`) enters, distributivity in the required direction fails, so you cannot eliminate a variable *across*
a `⊖`. You can still factor each `⊖`-operand (each is a monotone BGP/UNION) and apply `⊖` on top.

---

## 3. Scope: what factors, what does not (operator by operator)

| SPARQL operator | Circuit op | Factors? | Note |
|---|---|:--:|---|
| **BGP** (conjunction) | ⊗ | ✅ full variable elimination | the only place with the `|data|^{#patterns}` blow-up — the win |
| **Projection / DISTINCT** | ⊕ | ✅ built in | it *is* `marginalize` (⊕ over the projected-out variable) |
| **UNION** | ⊕ | ✅ in principle | a union of BGPs = ⊕ of the branches' factored circuits; monotone, composes |
| **MINUS / OPTIONAL** | ⊖ (`a∧¬b`) | ⚠️ operands only | non-monotone: factor each operand (BGP/UNION), apply `⊖` at the top — cannot eliminate across `⊖` |
| **Property paths** (`+`/`*`) | recursive | ❌ different mechanism | a level-indexed fixpoint, not a fixed join tree; sharing comes from content-addressed reach/base gates across levels (G1), not variable elimination |
| **FILTER / BIND / aggregation** | — | — | out of scope for the whole system |

**Two clarifications.**

- **MINUS/OPTIONAL is not "unfactorable" — its *combination* is.** `P1 MINUS P2` = `factored(P1) ⊖
  factored(P2)`; the operands are BGPs/UNIONs whose *own* join blow-up is exactly the factoring case. So
  the right design factors the operands and leaves `⊖` at the root. (Today the operands are built flat.)
- **Paths already stay polynomial by a different route.** The reach protocol keeps reach gates keyed by
  `(level, from, to)` and content-addressed, so equal sub-paths are shared across levels — a fixpoint-level
  sharing, orthogonal to BGP variable elimination. Do **not** try to force factoring onto paths.

---

## 4. Current implementation status

| | flat | factored |
|---|---|---|
| code | `CircuitRewriter.bgp()` (BGP), `productPlus()` (MINUS/OPTIONAL operands), the reach loop (paths) — **all Java, engine-native** | `factor.py` (in-memory algorithm) + `factor_native.py` (multi-pass SPARQL-`INSERT` prototype, rdflib) |
| passes | one CONSTRUCT (BGP); a small fixed plan (MINUS/paths) | one join+group-by **pass per eliminated variable** |
| operators | **all** (BGP / UNION / MINUS / OPTIONAL / paths) | **BGP only** (`factor_native.py`: *"MINUS/OPTIONAL keep the flat engine-native plan"*) |
| in the shipped Java engine? | **yes** | **no** — reference/prototype |

So the compactness results (E2 up to 201×, E5) come from the **Python factored** construction; the deployed
Java engine builds **flat** for every operator. `factor_native.py` shows factored *can* run on an
unmodified engine (pure SPARQL `INSERT` passes with `SHA256`-addressed row/gate IRIs), but it is not wired
into `CircuitRewriter`.

---

## 5. Worked example (the `∏ → ∑` win)

Data: `x1` has 2 hobbies (`t1,t2`) and 3 skills (`t3,t4,t5`), each token `p=0.5`.
Query: `SELECT ?x WHERE { ?x :hobby ?s . ?x :skill ?k }` (project `?x`; `?s,?k` existential).

- **Flat**: engine join → 2×3 = **6 rows** → `⊕( ⊗(t1,t3), ⊗(t1,t4), ⊗(t1,t5), ⊗(t2,t3), ⊗(t2,t4),
  ⊗(t2,t5) )` — **6 ⊗**, 12 gates / 18 edges.
- **Factored**: marginalize `?s` → `H = ⊕(t1,t2)`; marginalize `?k` → `S = ⊕(t3,t4,t5)`; join → `⊗(H,S)`
  — **1 ⊗ + 2 ⊕**, 8 gates / 7 edges.
- Same probability: `P(H)·P(S) = 0.75 × 0.875 = 0.65625` (flat's DNF and factored's `⊗(⊕,⊕)` are the same
  function). The gap is `∏deg = dᵏ` vs `∑deg = k·d`: 2×3 → 6 vs 5; 3 branches deg 10 → 1000 vs 30; layered
  depth-12 (E2 `deep-12×2`) → 49 152 vs 244 (**201×**).

---

## 6. Implementation recommendations (prioritized)

1. **Decide the positioning first (paper-level).** Is factored a *core system contribution* or a
   *reference optimization*? If core, it must be in the Java engine; if not, label E2/E5 explicitly as
   "reference-implementation optimization" and keep the Java engine flat. Everything below assumes "core".

2. **Port factored BGP into the Java `CircuitRewriter` as a multi-pass plan.** Generalize the single-pass
   `bgp()` into a variable-elimination plan that emits one CONSTRUCT/`INSERT` per eliminated variable —
   `factor_native.py` already prototypes the exact SPARQL (message relations materialized as RDF, joined by
   later passes, row/gate IRIs `SHA256`-addressed so equal bindings/gates share). Requirements: a **writable
   endpoint** (each pass INSERTs its message relation) and the existing content-addressing.

3. **Factor MINUS/OPTIONAL operands.** Replace the flat `productPlus(P1)` / `productPlus(P2)` with the
   factored sub-plan for each operand, keeping the top-level `⊖`/DIFF unchanged. Correctness is unaffected
   (operands are monotone BGP/UNION); this is where MINUS at scale currently pays the flat blow-up.

4. **Handle UNION as ⊕ of factored branches.** A `UNION` of BGPs → factor each branch, combine with a
   content-addressed `⊕`; shared sub-branches dedup automatically.

5. **Pick the elimination order deliberately.** `factored_bgp` uses a min-fill-ish `min(elim, key=cost)`;
   for real queries compute/approximate a tree decomposition and eliminate in that order — the realized
   width is what determines the `|data|^{w+1}` bound. Expose it and benchmark order sensitivity.

6. **Keep property paths on their own protocol.** Do not attempt variable elimination on the recursive
   closure; document the level-indexed content-addressed reach sharing as the path-specific mechanism.

7. **Measure the pass/round-trip trade-off.** Factored is `k` passes (≈ `k` engine round-trips for a
   remote endpoint) vs flat's single CONSTRUCT. On shallow/low-sharing queries flat can win end-to-end even
   though it builds a bigger circuit — report the crossover, don't assume factored is always faster.

8. **Lock the correctness invariant in CI.** Keep `WMC(flat) == WMC(factored) == PWE` as a regression on
   every shape (extends `watdiv_factor.py`/E5 + the gallery), so any factoring bug shows up as a
   probability mismatch, not a silent wrong answer.

---

## 7. Correctness protocol (must hold whenever factored is used)

For every tested instance: build **both** flat and factored circuits, and assert
`WMC(flat) == WMC(factored) == possible-world enumeration`. `watdiv_factor.py` does this on WatDiv
star/snowflake; extend it to MINUS operands and UNION once those are factored. The invariant is the whole
safety net — factoring is only ever a *representation* change, so a value change is a bug.

---

## 8. Code map

| concern | file |
|---|---|
| content-addressed DAG (`leaf/times/plus/minus`, `_put` dedup) | `reference/gates.py` |
| factored algorithm (base_relations / join / marginalize / factored_bgp) | `reference/factor.py` |
| engine-native factored prototype (SPARQL-`INSERT` passes) | `reference/factor_native.py` |
| flat BGP (shipped Java default) | `engine/src/main/java/npcs/circuit/CircuitRewriter.java` — `bgp()` |
| flat MINUS/OPTIONAL operands / paths (shipped) | same file — `productPlus()`, the reach loop |
| flat-vs-factored comparison + WMC cross-check (E5) | `reference/watdiv_factor.py`, `reference/factor_demo.py` |
