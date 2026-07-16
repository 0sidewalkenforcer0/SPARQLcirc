# Factored circuit construction — theory, limits, and implementation plan

*What factoring is, exactly which SPARQL fragment it applies to and why, what it fundamentally *cannot* do,
how the current algorithm handles the hard cases, and a prioritized plan for finishing it. Grounded in
`reference/factor.py`, `reference/factor_native.py`, `reference/gates.py`, and
`engine/src/main/java/npcs/circuit/CircuitRewriter.java`.*

## 0. TL;DR

- **Flat** construction builds **one ⊗ per derivation**, so the circuit is `∝ #derivations = |data|^{#patterns}`.
- **Factored** construction is **variable elimination (sum-product)**: eliminate each non-output join
  variable once (⊗ the relations that mention it, then ⊕-marginalize it). The circuit becomes
  `∝ |data|^{tw+1}` (**polynomial for bounded treewidth**) and denotes the **same provenance polynomial** —
  so the probability is unchanged (E5: `WMC(flat) == WMC(factored) == PWE`).
- It applies to the **monotone conjunctive fragment (BGP)**; extends to **projection** (marginalize) and
  **UNION** (⊕). For **MINUS/OPTIONAL** you factor the *operands* and keep the non-monotone **⊖ at the top**.
  **Property paths** use a different (recursive, content-addressed) sharing mechanism.
- **Not universal:** polynomial factoring exists **iff the lineage has bounded treewidth** (≈ the query is
  *safe*, Dalvi–Suciu). High-treewidth provenance has **no** small circuit — a #P wall (E4).
- **Status:** flat is the **Java engine default for every operator**; factored is a **Python-only reference**
  (`factor.py` + `factor_native.py`), **BGP-only**.

---

## 1. Background: two independent cost knobs, and the semiring

PQE has two costs that move independently:

1. **Construction** (the engine builds the circuit) `∝ #derivations D`; for depth `d`, branching `b`,
   `D ~ b^d`. This is what factoring attacks.
2. **Compilation + WMC** `∝ 2^{Θ(tw)}` in the *treewidth* of the lineage (E4) — a separate axis, **not**
   fixed by factoring.

The circuit is Boolean: `⊗ = ∧`, `⊕ = ∨`, `⊖(a,b) = a ∧ ¬b`. The **monotone fragment** (everything but `⊖`)
lives in the positive/absorptive semiring `(∧, ∨)` (PosBool), which satisfies **distributivity**
`a ⊗ (b ⊕ c) = (a ⊗ b) ⊕ (a ⊗ c)`. Factoring is an identity in that semiring; `⊖` is the one operator that
leaves it.

---

## 2. Theory: factoring = variable elimination

### 2.1 The identity (`reference/factor.py`)

To eliminate a non-output variable `x`: **join** (⊗) all relations mentioning `x`, then **marginalize**
(⊕ over `x`'s values) — i.e. pull a shared factor out of a sum, the reverse of distributivity.

```
base_relations   : each triple pattern → { binding-tuple : ⊕(matching tokens) }
join(A, B)       : ⊗ on the shared vars, ⊕-accumulate on key collision
marginalize(R, x): group by the remaining vars, ⊕ the gates in each group
factored_bgp     : while elim: pick x (min-fill-ish), join relations with x, marginalize x; then join the rest
```

### 2.2 Why it is correct

`∧` distributes over `∨` and both are commutative/associative, so the factored expression and the flat
sum-of-products denote the **same Boolean function** (same polynomial in `N[X]`; `gates.py.plus` keeps
duplicates — no idempotence — so counting semirings are preserved too). Value unchanged; only the
representation differs. `watdiv_factor.py` (E5) asserts `WMC(flat) == WMC(factored) == PWE`.

### 2.3 Why it is small

1. **Early marginalization** turns `∏deg` into `∑deg` (marginalize an existential *before* the next join).
2. **Content-addressed sharing** (`gates.py._put`, key `sha("TIMES|"+sorted(children))`): structurally equal
   intermediate gates collapse to one id, so a partial product shared by many derivations is stored once.
   Flat never *forms* those partial products, so it cannot share them.

With a min-fill order realizing tree-width `w`, every intermediate relation has `≤ w+1` variables →
`O(|data|^{w+1})`, polynomial for bounded `w`, vs flat `|data|^{#patterns}`.

### 2.4 The algebraic boundary

Variable elimination is valid **only in the commutative semiring** `(⊗, ⊕)`. Once `⊖` (monus, `∧¬`) enters,
distributivity in the required direction fails → you cannot eliminate a variable *across* a `⊖`. You can
still factor each `⊖`-operand (a monotone BGP/UNION) and apply `⊖` on top.

---

## 3. Scope: what factors, what does not (operator by operator)

| SPARQL operator | Circuit op | Factors? | Note |
|---|---|:--:|---|
| **BGP** (conjunction) | ⊗ | ✅ full variable elimination | the only place with the `|data|^{#patterns}` blow-up |
| **Projection / DISTINCT** | ⊕ | ✅ built in | it *is* `marginalize` |
| **UNION** | ⊕ | ✅ in principle | a union of BGPs = ⊕ of the branches' factored circuits |
| **MINUS / OPTIONAL** | ⊖ (`a∧¬b`) | ⚠️ operands only | non-monotone: factor each operand, apply `⊖` at the top — cannot eliminate across `⊖` |
| **Property paths** (`+`/`*`) | recursive | ❌ different mechanism | a level-indexed fixpoint (G1); sharing = content-addressed reach/base gates across levels, not variable elimination |
| **FILTER / BIND / aggregation** | — | — | out of scope for the whole system |

---

## 4. Limits — not all how-provenance factors (a #P wall)

Factoring is **not universal**. Three strictly nested levels:

| level | condition | meaning |
|---|---|---|
| ① *some* circuit exists | always | the flat sum-of-products is one (possibly exponential) |
| ② **polynomial** circuit | ⟺ **bounded treewidth** of the lineage | variable elimination → `|data|^{tw+1}`; growing tw → exponential |
| ③ **read-once** | co-occurrence graph is P₄-free | each token appears once → linear, exact by independence; a strict subset |

`read-once ⊊ bounded-treewidth (poly-factorable) ⊊ all provenance`.

- **The #P wall.** When treewidth grows (grid / clique / dense many-to-many joins), the provenance
  polynomial has **no** polynomial-size circuit — a poly circuit would give poly-time WMC, contradicting
  #P-hardness (unless FP = #P). So high-treewidth how-provenance is inherently exponential **in any
  representation**; E4's growing-tw family shows **both** OBDD and d-DNNF blow up — the wall is the *result*,
  not a compiler weakness.
- **Safe/unsafe dichotomy (Dalvi–Suciu).** For UCQ on tuple-independent DBs, PQE is either PTIME (**safe** →
  a poly factoring exists) or #P-hard (**unsafe** → no poly factoring unless FP = #P). "Which how-provenance
  factors small" ≈ "which queries are safe."
- **Non-monotone boundary (orthogonal to treewidth).** Everything above is the monotone semiring; with `⊖`
  you cannot eliminate *across* the difference regardless of treewidth — a limit of *algebraic structure*.

So factoring is "turn exponential-in-#patterns into polynomial-in-treewidth **when treewidth is bounded**",
**not** "eliminate #P-hardness."

---

## 5. Worked example (the `∏ → ∑` win)

Data: `x1` has 2 hobbies (`t1,t2`) and 3 skills (`t3,t4,t5`), each token `p = 0.5`.
Query: `SELECT ?x WHERE { ?x :hobby ?s . ?x :skill ?k }` (project `?x`; `?s,?k` existential).

- **Flat**: engine join → 2×3 = **6 rows** → `⊕( ⊗(t1,t3),⊗(t1,t4),⊗(t1,t5),⊗(t2,t3),⊗(t2,t4),⊗(t2,t5) )`
  — 6 ⊗, 12 gates / 18 edges. (Not read-once: `t1` appears 3×.)
- **Factored**: marginalize `?s` → `H = ⊕(t1,t2)`; marginalize `?k` → `S = ⊕(t3,t4,t5)`; join → `⊗(H,S)`
  — 1 ⊗ + 2 ⊕, 8 gates / 7 edges. (Read-once: each token once.)
- Same probability `P(H)·P(S) = 0.75 × 0.875 = 0.65625`. Gap scales as `∏deg = dᵏ` vs `∑deg = k·d`:
  2×3 → 6 vs 5; 3 branches deg 10 → 1000 vs 30; layered depth-12 (E2 `deep-12×2`) → 49 152 vs 244 (**201×**).

---

## 6. Current implementation status

| | flat | factored |
|---|---|---|
| code | `CircuitRewriter.bgp()` (BGP), `productPlus()`/`minusRoot()` (MINUS/OPTIONAL), the reach loop (paths) — **all Java, engine-native** | `factor.py` (in-memory) + `factor_native.py` (multi-pass SPARQL-`INSERT` prototype) |
| passes | one CONSTRUCT (BGP); small fixed plan (MINUS/paths) | one join+group-by **pass per eliminated variable** |
| operators | **all** | **BGP only** (`factor_native.py`: *"MINUS/OPTIONAL keep the flat engine-native plan"*) |
| shipped in the Java engine? | **yes** | **no** — reference/prototype |

So the compactness results (E2 up to 201×, E5) come from the **Python factored** construction; the deployed
Java engine builds **flat** for every operator. `factor_native.py` shows factored *can* run on an unmodified
engine (pure SPARQL `INSERT` passes, `SHA256`-addressed row/gate IRIs), but it is not wired into
`CircuitRewriter`.

---

## 7. How the algorithm handles the hard cases

### 7.1 Non-bounded treewidth — degrade honestly, stay exact

No trick beats #P; the algorithm caps and records rather than fake a result:

- **Construction** (flat, ∝ #derivations): if the collected circuit exceeds `MAXTRIP` (4 M triples) it is
  recorded **`too-large`** (E8 has 9 such single queries), not crashed.
- **Compilation** (fixed-order OBDD): over `E4_TIMEOUT` (120 s) or the memory cap → recorded
  **`obdd-timeout` / OOM** — *"the ceiling IS the data point"* (E4).
- **d4 (d-DNNF)** is order-robust and pushes the frontier further than OBDD (compiles instances OBDD times
  out on), but does **not** remove the wall.
- **Exact-only, no approximation fallback.** The portfolio is read-once → PWE → CNF→d4 → OBDD, all *exact*;
  there is **no** Monte-Carlo / sampling path (ProvSQL has one; we deliberately do not). So a hard instance
  is *exact or a recorded cap*, never a wrong probability. Whatever compiles satisfies `WMC == PWE` (G6).

### 7.2 With `⊖` (MINUS/OPTIONAL) — operands built separately, one `⊖` on top, unhandled shapes fail-fast

`CircuitRewriter` builds (matching the §2.4 boundary exactly):
```
productPlus(P1) → ⊕_{P1}      productPlus(P2) → ⊕_{P2}      subFeeds → ⊕_{sub}
minusRoot       → ?m a c:Minus ; c:minuend ?p1 ; c:subtrahend ?sub ; c:feeds ?ans     # ONE top-level ⊖
```

- Each operand is a (currently **flat**) `⊕-of-⊗`; they combine via a **single top-level `⊖ = a∧¬b`** — i.e.
  "build operands, keep `⊖` at the top."
- `normalize()` first reduces composite shapes (UNION-as-operand, chained MINUS, OPTIONAL-as-operand);
  W3C MINUS (guarded DIFF) and OPTIONAL (`(A∧B) ∪ (A DIFF B)`, unguarded) are distinguished.
- **Residuals that can't be reduced fail-fast** (`UnsupportedOperationException`): right-nested
  `A MINUS (P MINUS Q)`, pathological OPTIONAL-as-operand, cross-product OPTIONAL — never silently wrong.
- `⊖` is a plain Boolean function → OBDD/d-DNNF/PWE compute it exactly → `WMC == PWE` (`verify_nonmono`, G6).
- Factoring is **not** applied across `⊖` (cannot be); the operands are flat today and could later be
  factored **without affecting correctness** (the `⊖` stays at the root — see §8).

---

## 8. Implementation recommendations (prioritized)

1. **Decide the positioning first (paper-level).** Core system contribution or reference optimization? If
   core, it must be in the Java engine; if not, label E2/E5 as a *reference-implementation optimization* and
   keep the Java engine flat. The rest assumes "core."
2. **Port factored BGP into `CircuitRewriter` as a multi-pass plan.** Generalize the single-pass `bgp()` to
   one CONSTRUCT/`INSERT` per eliminated variable — `factor_native.py` already prototypes the SPARQL (message
   relations as RDF, joined by later passes, `SHA256`-addressed row/gate IRIs). Needs a **writable endpoint**.
3. **Factor MINUS/OPTIONAL operands.** Replace flat `productPlus(P1)`/`productPlus(P2)` with the factored
   sub-plan per operand, keeping the top-level `⊖` unchanged. Correctness unaffected (operands are monotone).
4. **UNION as ⊕ of factored branches** (content-addressed, so shared branches dedup).
5. **Pick the elimination order deliberately** (min-fill / tree-decomposition); the realized width sets the
   `|data|^{w+1}` bound. Expose it and benchmark order sensitivity.
6. **Keep property paths on their own protocol** (level-indexed content-addressed reach); do not force
   variable elimination onto the recursive closure.
7. **Measure the pass/round-trip trade-off.** Factored is `k` passes (≈ `k` round-trips remotely) vs flat's
   single CONSTRUCT; on shallow/low-sharing queries flat can win end-to-end. Report the crossover.
8. **Lock the invariant in CI.** `WMC(flat) == WMC(factored) == PWE` on every shape (extends E5 + gallery),
   so a factoring bug shows up as a probability mismatch, not a silent wrong answer.

---

## 9. Correctness & the probability invariant

For every tested instance, build **both** circuits and assert `WMC(flat) == WMC(factored) == PWE`
(`watdiv_factor.py`; extend to MINUS operands and UNION as they are factored).

**Factoring never changes the probability.** Flat and factored denote the same function, and WMC is
function-determined → the value is identical. The one requirement — for **both** — is a *correlation-aware*
WMC (OBDD / d-DNNF / PWE), **not** naive gate arithmetic: shared tokens make circuits non-read-once, and a
naive per-answer product-sum is wrong (R8.3: it exceeds 1 on the reconvergent query). If anything, factoring
*helps* — it can make a circuit read-once (§5: `⊗(⊕,⊕)` is read-once; flat's `⊕(⊗…)` is not), which permits
the cheap linear-exact evaluation. Factoring is only ever a *representation* change; a value change is a bug.

---

## 10. Code map

| concern | file |
|---|---|
| content-addressed DAG (`leaf/times/plus/minus`, `_put` dedup) | `reference/gates.py` |
| factored algorithm (base_relations / join / marginalize / factored_bgp) | `reference/factor.py` |
| engine-native factored prototype (SPARQL-`INSERT` passes) | `reference/factor_native.py` |
| flat BGP (shipped Java default) | `engine/.../CircuitRewriter.java` — `bgp()` |
| flat MINUS/OPTIONAL (`productPlus`/`subFeeds`/`minusRoot`) + paths (reach loop) + fail-fast (`normalize`/`assertPureBgp`) | same file |
| flat-vs-factored comparison + WMC cross-check (E5) | `reference/watdiv_factor.py`, `reference/factor_demo.py` |
| high-tw caps / timeouts (`too-large`, `E4_TIMEOUT`) | `reference/e8_wikidata.py`, `reference/e6_minus.py`, `reference/e4_sweep.py` |
