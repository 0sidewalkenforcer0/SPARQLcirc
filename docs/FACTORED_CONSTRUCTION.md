# Factored circuit construction — theory, limits, and implementation plan

*What factoring is, exactly which SPARQL fragment it applies to and why, what it fundamentally *cannot* do,
how the current algorithm handles the hard cases, and a prioritized plan for finishing it. Grounded in
`engine/src/main/java/npcs/circuit/FactoredBgpRewriter.java` (engine-native factored BGP),
`CircuitRewriter.java`, `reference/factor.py`, `reference/factor_native.py`, and `reference/gates.py`.*

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
- **Status (verified):** factored BGP is now the **Java engine default** (`ConstructionMode.FACTORED`,
  `FactoredBgpRewriter` — one CONSTRUCT per elimination step, the same algorithm as `factor.py`). **Flat** is
  kept for **ablations and read-only endpoints**. UNION/MINUS/OPTIONAL still use the flat operator plan; paths
  use the recursive reach protocol. Confirmed on the drug BGP: factored and flat emit **byte-identical answer
  gates** and **equal WMC** (`0.774297708` for the reconvergent answer). `factor.py`/`factor_native.py` are now
  the reference/oracle for the same algorithm.

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
| **BGP** (conjunction) | ⊗ | ✅ full variable elimination | the `|data|^{#patterns}` blow-up — **engine-native & the default** (`FactoredBgpRewriter`) |
| **Projection / DISTINCT** | ⊕ | ✅ built in | it *is* `marginalize` |
| **UNION** | ⊕ | ✅ in principle | a union of BGPs = ⊕ of the branches' factored circuits |
| **MINUS / OPTIONAL** | ⊖ (`a∧¬b`) | ⚠️ operands only | non-monotone: factor each operand, apply `⊖` at the top — cannot eliminate across `⊖` |
| **Property paths** (`+`/`*`) | recursive | ❌ different mechanism | a level-indexed fixpoint (G1); sharing = content-addressed reach/base gates across levels, not variable elimination |
| **FILTER** | — (no gate) | ❌ flat only | supported system-wide, but a filtered BGP falls back to the flat plan: the condition belongs in the operand's group, and the factored passes exchange materialized relations instead of one group |
| **Output-only BIND** | — (no gate) | ❌ flat only | supported as a compatibility extension; a target used by a later triple pattern is rejected |
| **Aggregation** | — | — | out of scope for the whole system |

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

Factored BGP is **implemented in the Java engine and is the default** (`ConstructionMode.FACTORED`, `6ba3ebd`);
flat is retained for **ablations and read-only endpoints**.

| | factored (default) | flat (ablation / read-only) |
|---|---|---|
| operators | **pure BGP** (`constructionPlan()` → `FactoredBgpRewriter` when `isPureBgp`); UNION/MINUS/OPTIONAL/paths fall back to flat/reach | **all** (BGP / UNION / MINUS / OPTIONAL; paths via the reach loop) |
| code | `FactoredBgpRewriter` (engine-native), dispatched by `CircuitRewriter.constructionPlan()` | `CircuitRewriter.bgp()` / `branchPlan()` / `productPlus()`+`minusRoot()` / reach loop |
| passes | **one CONSTRUCT per elimination step** (base → join → marginalize → answers), run as a `CircuitConstructionPlan` of `Step`s | one CONSTRUCT (BGP); small fixed plan (MINUS/paths) |
| engine requirement | **writable endpoint** — each step INSERTs a private `urn:sc:` message relation fed back by `CircuitRun` before the next (`requiresFeedback()`); read-only ⇒ `--construction=flat` | any SPARQL 1.1 endpoint, incl. read-only (QLever/MillenniumDB) |
| reference / oracle | `reference/factor.py` (in-memory, same min-scope elimination) + `factor_native.py` (SPARQL-`INSERT` prototype) | `reference/gamma.py` |

Properties of the engine-native factored plan (verified on the drug BGP):
- **Same algorithm as `factor.py`** — min-scope variable elimination (⊗ the relations mentioning the chosen
  variable, ⊕-marginalize it), then join the residual relations and emit answers.
- **Content-addressed and byte-compatible with flat** — products `urn:g:t:`, marginalization sums `urn:g:s:`,
  answers `urn:g:a:`; `FactoredBgpRewriter.termHash` is byte-for-byte identical to `CircuitRewriter`'s answer
  identity, so factored and flat emit the **same answer-gate IRIs** and the **same WMC** (drug BGP: both give
  `0.774297708` for the reconvergent answer). Factoring changes the representation, never the value.
- **Concurrency-isolated** — message relations are namespaced by a per-invocation `workspaceId` hash so
  parallel factored plans can't consume each other's rows; **gate IRIs do not depend on it**, so the emitted
  circuit is workspace-independent (byte-identical across runs and engines).

So E2/E5 compactness is now reproducible by the **Java engine itself** on a writable endpoint, not only the
Python reference; `factor_native.py` remains the standalone proof that the construction runs as pure SPARQL
`INSERT` passes. (On shallow/low-sharing shapes factored can be *larger* than flat — see rec. 7.)

**Update (2026-07-17) — source-restriction pushdown for selective BGPs.** A bound/selective query originally
made factored over-build catastrophically, not just by a constant: only the pattern carrying the constant was
restricted, so an interior chain edge materialized its **entire unrestricted** relation (WatDiv L-path 10M:
**299 762 gates / 186 s**, touching 299 647 base tokens for 45 answers; 100M chains were `too-large`).
`FactoredBgpRewriter` now **semi-joins each base relation to the rest of a selective BGP** (reify the other
patterns as context) so only full-match rows materialize; unbound BGPs keep plain base scans, so the design
regime is untouched. L-path 10M dropped to **143 gates / 513 ms** (49 base tokens == flat), WMC unchanged.
Full regime map + numbers: `reference/FACTORED_REGIMES.md`, `reference/watdiv/rdfstar_factored_vs_flat.csv`,
`reference/watdiv/unbound_factored_vs_flat.csv`.

**Update (2026-08-07, extended 2026-08-17) — construction-cost changes, and which published numbers
they move.** Each one keeps a switch that restores the previous behaviour, because most of them
change a measured quantity. Rows 1–3 landed 2026-08-07; row 4 landed 2026-08-17 and is the only one
that changes gate IRIs, so every path-circuit size predates it.
Correctness is checked by the possible-world oracle in `engine/.../CircuitSemanticsTest.java` (see
`docs/…` and the class comment) plus `verify_composition.py` / `verify_gallery.py` for probabilities.

| # | change | effect on the circuit | switch to restore the old behaviour |
|---|--------|----------------------|-------------------------------------|
| 1 | **one-pass base relations.** The semi-join restriction each base relation of a *selective* BGP needs IS the whole BGP, so the k base queries had the same WHERE and were k evaluations of one join. One CONSTRUCT now publishes all k. | **byte-identical** | `CIRCUIT_ONE_PASS_BASE=0` / `-Dsparqlcirc.perPatternBase=true` |
| 2 | **step parallelism.** A plan's independent steps run concurrently, scheduled by the real dependency DAG (`Step.reads()/writes()`); an undeclared step is a barrier both ways. | **byte-identical** | default is `--parallelism=1` (sequential) |
| 3 | **restricted subtrahend marginal.** `⊕_{P2}` bindings compatible with no minuend binding got gates no answer can reach, so the whole right operand was materialized. Now semi-joined to the minuend (skipped for domain-disjoint operands, where it would be a cross product). The **minuend is never restricted** — each of its bindings is an answer. | **fewer triples**, all of them unreachable from every answer gate; reachable circuit and every probability unchanged | `CIRCUIT_RESTRICT_SUBTRAHEND=0` / `-Dsparqlcirc.unrestrictedSubtrahendMarginal=true` |
| 4 | **exact closure levels** (2026-08-17). `reach^k` holds the paths of exactly `k+1` edges and the answer/row projection unions every non-base level, instead of carrying `reach^k` into `reach^{k+1}` so a single-level projection can read the last one. The carry cost twice: it republished each pair once per remaining round as a one-input `⊕`, and republishing **renamed** the pair (the level is in the gate key), so the next round's composition built a second `⊗` for a derivation it had already multiplied — content addressing merges syntactically identical subcircuits, not semantically equivalent ones. Dropping the carry removes both. The client also stops as soon as a round produces no gates, which the carry had masked (with it, a round is never empty). | **different gate IRIs**, so every path-circuit size moves; same answers and same probabilities | `CIRCUIT_EXACT_LEVELS=0` / `-Dsparqlcirc.cumulativeLevels=true` |

Measured (1 on GraphDB 10.7.6 / WatDiv 10M Standard-reified; 2 and 3 in-process, since a flat operator
plan on that store runs past 900 s per query):

```
1  selective BGP, construction_ms       L1 481→327  S2 12002→4897  F1 816→536  S1 1102→687
2  flat operator plan, p=4              MINUS 1.34x  OPTIONAL 1.68x  OPT+MINUS 1.77x  UNION3 1.73x
   factored operator plan, p=4/8        MINUS 1.96x  two OPTIONALs 2.06x/2.47x  unbound star-4 1.15x
3  selective minuend, 40k right operand OPTIONAL 2220→38 ms (162 900→3 300 triples)
4  path gallery, 9 shapes, in-process   1427→1093 triples (23.4%); p? and p* unchanged (p? has no
                                        rounds; p*'s zero-length gates already act as the carry)
   triples vs path depth, chain L       L=3 1.73x  L=10 3.25x  L=20 5.42x   (O(L²) → O(L))
   GraphDB 10.7.6, isolated scratch repo, best of 3 — MEASURED BEFORE the RDF-star path merge,
   re-run before quoting: Wikidata wdt:P279+ from Q7397 (17 nodes, depth 7) 1794→576 ms, 2566→426
   triples, 16→6 rounds · chain of 30  3181→1566 ms, 5217→687 triples
                                        MINUS    2030→27 ms (161 500→1 900 triples)
```

**Three interactions worth knowing.**
- Change 1 turned a *selective* single BGP into a pure **chain** (measured DAG widths for S1/L1/F1 and a
  bound 4-chain: 1 at every level), so change 2 buys nothing there. Its wins are on unbound BGPs and on
  factored *operator* queries, where each operand's marginal is an independent sub-DAG.
- Change 3's win is proportional to how much of the right operand the minuend cannot reach, so it is ~0 on
  the opposite shape. WatDiv M1 is `likes MINUS (purchase join)` — a millions-of-triples minuend against a
  small subtrahend — and stays intractable in flat mode. That is a property of flat construction.
- Parallelism affects flat and factored **asymmetrically** (a pure-BGP flat plan is one CONSTRUCT and gains
  nothing; operator queries gain more in flat), so a parallel configuration must be reported separately or
  flat-vs-factored stops being comparable.

**Numbers that need re-running before being quoted:** construction time for the bound (S/L/F) classes was
measured with k base passes and is now pessimistic by the factors above; circuit **size** for the M and O
classes was counting gates no answer can reach (98 % of the emitted triples in the fixture above), so it
gets smaller. Neither affects circuit-identity artifacts, which are byte-identical.

---

## 7. How the algorithm handles the hard cases

### 7.1 Non-bounded treewidth — degrade honestly, stay exact

No trick beats #P; the algorithm caps and records rather than fake a result:

- **Construction**: if the collected circuit exceeds `MAXTRIP` (4 M triples) it is recorded **`too-large`**
  (E8 has 9 such single queries), not crashed. Factored keeps BGP construction `∝ |data|^{tw+1}`, but at high
  treewidth that is large too; the cap is on the materialized circuit either way (factored or flat).
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

1. **Positioning is settled — it is a core system contribution.** Factored BGP is the shipped engine default
   (`ConstructionMode.FACTORED`), not a reference-only optimization; E2/E5 compactness is a property of the
   Java engine, reproducible on any writable endpoint.
2. **~~Port factored BGP into the Java engine.~~ Done** (`6ba3ebd`; hardened by `72fb887`/`11d8a07`).
   `FactoredBgpRewriter` emits one CONSTRUCT per eliminated variable (base → join → marginalize → answers) as a
   `CircuitConstructionPlan`, dispatched from `CircuitRewriter.constructionPlan()` for pure BGPs. Needs a
   **writable endpoint** — each step INSERTs a private `urn:sc:` message relation fed back by `CircuitRun`
   (`requiresFeedback()`); read-only engines fall back to `--construction=flat`.
3. **Factor MINUS/OPTIONAL operands.** Replace flat `productPlus(P1)`/`productPlus(P2)` with the factored
   sub-plan per operand, keeping the top-level `⊖` unchanged. Correctness unaffected (operands are monotone).
4. **UNION as ⊕ of factored branches** (content-addressed, so shared branches dedup).
5. **Elimination order — improve beyond min-scope.** Min-scope is implemented (eliminate the variable whose
   relation-scope union is smallest); for skewed data compute/approximate a real tree decomposition and
   eliminate in that order — the realized width sets the `|data|^{w+1}` bound. Benchmark order sensitivity.
6. **Keep property paths on their own protocol** (level-indexed content-addressed reach); do not force
   variable elimination onto the recursive closure.
7. **Measure the pass/round-trip trade-off** (now directly measurable in-engine — R9 construction evidence).
   Factored is `k` feedback passes (≈ `k` round-trips remotely) vs flat's single CONSTRUCT, so on shallow/
   low-sharing shapes flat can win end-to-end (e.g. the linear 3-hop drug BGP: factored 72 triples vs flat 25,
   no early marginalization possible). Report the crossover — factored's win is on star/high-fan-out shapes.
   **Measured (`reference/FACTORED_REGIMES.md`):** with source-restriction pushdown (§6 update), bound
   selective shapes are now S-star **9.5×** (874→92 gates @100M), L-path/F-snow a **tie** (158/26 vs 249/35),
   MINUS identical. The decisive win is the **unbound reconvergent** regime the bound cells never exercised —
   `unbound_factored_vs_flat.py`: flat exponential vs factored polynomial, **26.4× by k=7** (65 552 vs 2 480).
8. **~~Source-restriction pushdown~~ Done** (`5472a41`) — see §6 update; verified `WMC(flat)==WMC(factored)`
   (max 6e-17) and unbound gate counts byte-identical before/after (scoped strictly to selective queries).
9. **Lock the invariant in CI.** `WMC(flat) == WMC(factored) == PWE` on every shape (extends E5 + gallery),
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
| **engine-native factored BGP** (multi-pass variable elimination, **default**) | `engine/.../FactoredBgpRewriter.java`, dispatched by `CircuitRewriter.constructionPlan()` |
| construction mode + plan types + inter-pass feedback | `engine/.../ConstructionMode.java`, `CircuitConstructionPlan.java`, `CircuitRun.java` |
| flat BGP (ablation / read-only route) | `engine/.../CircuitRewriter.java` — `bgp()` / `branchPlan()` |
| flat MINUS/OPTIONAL (`productPlus`/`subFeeds`/`minusRoot`) + paths (reach loop) + fail-fast (`normalize`/`assertPureBgp`) | same file |
| flat-vs-factored comparison + WMC cross-check (E5) | `reference/watdiv_factor.py`, `reference/factor_demo.py` |
| high-tw caps / timeouts (`too-large`, `E4_TIMEOUT`) | `reference/e8_wikidata.py`, `reference/e6_minus.py`, `reference/e4_sweep.py` |
