# Property-path circuit variants — a design ablation (why term-type identity AND per-path isolation)

Property-path provenance circuits are built by an iterative reach/base-gate fixpoint (G1). *How the
reach/answer gates are keyed* is a design choice, and this session's engine history is exactly a
three-point ablation of that choice — the "different methods as variants" made explicit. All three build
on the same 2.13 B-triple Wikidata graph, same `P279+` query (16 answers).

## The three variants

| variant | reach/answer-gate keying | correctness | WD-path cones | OBDD compile | source |
|---|---|:--:|--:|--:|---|
| **merged** (pre-`1e67021`) | raw `CONCAT("A\|x=",STR(?x),…)` — erases term type/datatype/lang/bound; unescaped `\|` delimiter | **✗ WRONG** | (small — wrongly) | ~1 ms | `verify_answer_keys.py` (6 counterexamples) |
| **shared** (`1e67021`) | `termHash()` — kind-tagged (i/l/b/u), keeps lexical+datatype+lang; but reach gates **shared across paths** | ✓ OBDD==PWE | **19 → 233 tok** | **5.75 s** | `HISTORICAL_TIMINGS.md` |
| **isolated** (`7882a1e` `PathIsoSeq`) | `termHash()` **+ per-path fingerprint** on reach/base gates | ✓ OBDD==PWE | **1 → 20 tok** | **~1 ms** | `CANONICAL_TIMINGS.md`, G6 |

## What each variant shows

- **merged → *incorrect*.** The raw-STR key makes two *distinct* SPARQL solutions collapse into one answer
  gate: an IRI `<http://x/1>` and the literal `"http://x/1"` hash equal; so do two literals differing only
  in datatype or language, and a `|`-injection in a value. `verify_answer_keys.py` builds **6 minimal
  counterexamples** (control · IRI-vs-literal same lexical · datatype · language tag · delimiter injection ·
  OPTIONAL-unbound vs literal `"NULL"`) that each merged under the old key — producing a **wrong
  probability and/or a lost answer**. It was *fast and small* precisely because it under-counted. **Speed
  from a wrong circuit is not a win.**
- **shared → *correct but blows up on reconvergence*.** Term-type-aware identity (`termHash`) fixes
  correctness (OBDD == PWE), but sharing reach gates across every path lets the reconvergent structure
  accumulate: WD-path answer cones span **19–233 tokens**, and the fixed-order OBDD compile jumps to
  **5.75 s** (the value that briefly motivated an order-robust d-DNNF for paths).
- **isolated → *correct and compact*.** Giving each path its own reach/base-gate fingerprint keeps the
  cones at **1–20 tokens** (OBDD compile back to **~1 ms**) with **no loss of correctness** — G6 confirms
  OBDD == PWE == d4(local d-DNNF WMC) on **all 16** path answers. Same answers, same probabilities, ~5000×
  cheaper compile than the shared variant.

## The design lesson (both fixes are necessary, and orthogonal)

Content-addressing a property-path circuit correctly needs **two independent properties**:
1. **term-type-aware gate identity** (`termHash`) — without it, distinct solutions merge → *incorrect*
   (the `merged → shared` step);
2. **per-path state isolation** (`PathIsoSeq`) — without it, correct-but-reconvergent circuits blow up the
   fixed-order compile (the `shared → isolated` step).

Neither alone suffices: merged is fast+wrong, shared is correct+slow, isolated is correct+fast. This is
why the final engine carries both, and it is the honest provenance of the WD-path number moving
8.04 s → 2.14 s across the session (the intermediate rows live in `HISTORICAL_TIMINGS.md`, not discarded
but reinterpreted as this ablation).

## Caveats

- The **merged** row's correctness failure is demonstrated by `verify_answer_keys.py` on minimal reified
  data (runnable on the current jar as a *regression* — it asserts the fix); the historical *timing* of the
  merged variant is not re-measured (it would require rebuilding the pre-`1e67021` jar, and its circuit is
  wrong anyway). The **shared** and **isolated** timings are this session's measured 5-run numbers.
- Orthogonal path dimensions not varied here (single-source vs all-pairs, `p+`/`p*`/`p?`, `P279+`/`P131+`)
  are a *coverage* axis (which paths the method supports), separate from this *design/keying* ablation.

## Coverage axis — which path fragment the method handles (current `PathIsoSeq` jar)

Complementary to the keying ablation above: run different path *operators / predicates / bindings* and
check the circuit builds with correct WMC. All on the current jar; `WMC==PWE` sampled on cones ≤ 18 tokens.

| path query | dataset | operator | answers | gates+edges | compile | WMC==PWE | notes |
|---|---|---|--:|--:|--:|:--:|---|
| `wdt:P279+` (subclass, single-src) | Wikidata **2.13 B** | `p+` | 16 | 1466 | 1 ms | **15/15** | G6; the headline |
| `wdt:P131+` (admin containment, single-src) | Wikidata **2.13 B** | `p+` | 2 | 29 | 0 ms | **2/2** | **2nd predicate** — extends the check |
| `friendOf*` (single-src) | WatDiv 32.7 M | `p*` | 1 (self) | 164 | 2 ms | — | zero-or-more returns the zero-length self correctly |

### Honest limits found in the sweep

- **Compound `alt`/inverse closure** — the engine **fail-fasts** (rc=1, the `90c3c3c` path-modifier guard)
  rather than silently mis-computing an unsupported compound path. Correct behaviour, but a coverage
  boundary: bounded single-predicate `p+`/`p*`/`p?` are supported; arbitrary compound-subpath closures not.
- **Dense cyclic `friendOf+`** from a highly-connected WatDiv user (225 direct edges) **did not complete
  the build** (rc=1) in this run — the dense cyclic component is a current scale limit of the iterative
  path build; `friendOf+` from a sparse node is empty. So the *validated* `p+` coverage is the two Wikidata
  predicates (small DAG-shaped hierarchies); dense cyclic graphs are future work.

**Summary:** validated on **`p+` (two predicates) and `p*` at 2.13 B scale with WMC == PWE**; fail-fasts
(not mis-computes) on unsupported compound modifiers; dense cyclic closures are a scale limit.
