# provcircuit — shared provenance circuits for probabilistic SPARQL (reference impl)

Implementation of the VLDB draft's core: build a **shared, content-addressed
provenance circuit** for a SPARQL query over a token-labeled probabilistic ABox,
then do exact **probabilistic query evaluation** on it. Property paths are out of
scope (deferred to the recursive follow-up).

## Modules
- `gates.py` — the circuit DAG + **collision-free content-addressed** gate
  constructors (`leaf/times/plus/minus`). Congruent gates (same op + canonicalized
  children) collapse to one id → maximal sharing. Canonical id = `sha1(op | sorted(child-ids))`,
  duplicates kept (no idempotence, since `g⊕g = 2g` in `N[X]`). This is the
  collision-free fix for the `issue.txt` SUM+COUNT concern.
- `gamma.py` — builds the shared circuit for the non-monotone fragment
  (`bgp / union / join / optional / minus`), mirroring spm-semiring semantics
  (`OPTIONAL = (P1 AND P2) UNION (P1 DIFF P2)`), one root per answer, plus `project`.
- `wmc.py` — `prob()` = exact probability = WMC of the circuit's Boolean
  abstraction (`⊗→∧, ⊕→∨, ⊖(a,b)→a∧¬b`), memoized over the DAG; `pwe()` = ground
  truth by possible-world enumeration; `check()` compares them.
- `demo.py` — the paper's drug running example (Fig. 1/2).
- `tests.py` — correctness battery.

## Run
```
python3 demo.py      # reproduces Fig. 2: p1, p3 shared; probs match PWE
python3 tests.py     # 66/66 answer-probability checks vs PWE
```

## Status
- ✅ Circuit model + content-addressed sharing (collision-free).
- ✅ Non-monotone fragment (OPTIONAL/MINUS via ⊖ gates) — the moat.
- ✅ Exact PQE, verified == possible-world enumeration (66/66).
- ✅ Reproduces the running-example shared circuit (Fig. 2).

## Engine-native construction (the paper's headline claim) — BGP done ✅

Built on the NPCS Java rewriter (`../engine`), `npcs.circuit.CircuitRewriter`
emits a **CONSTRUCT** query that makes an **unmodified** SPARQL engine materialize
the shared circuit as RDF — ProvProd/ProvAggSum replaced by ⊗/⊕ gate constructors
with content-addressed gate IRIs (`IRI("urn:g:t:"+SHA256(...))`). Leaves are the
`?fprov` tokens, so shared leaves dedupe automatically via RDF set semantics.

```
# run the emitted CONSTRUCT on an in-memory engine, get the circuit as N-Triples:
cd ../engine && mvn -q package
java -cp target/npcs-rewrite.jar npcs.circuit.CircuitRun \
     Standard ../provcircuit/data/drug.reified.ttl ../provcircuit/queries/drug3hop.sparql \
     > ../provcircuit/data/drug.circuit.nt
# verify WMC over the engine-built circuit == PWE:
cd ../provcircuit && python3 verify_engine_native.py     # -> p1,p3 shared; ALL MATCH
```

Reproduces Fig. 2 (p1, p3 shared across gates) and both answer probabilities match PWE.

## Engine-native non-monotone (MINUS / OPTIONAL) — done ✅

`CircuitRewriter.plan()` compiles a query to a **plan** of CONSTRUCT queries
(per-operator materialization) that an unmodified engine runs to build the ⊗/⊕/⊖
circuit:
- **MINUS** (4 CONSTRUCTs): P1 products→⊕_{P1}; P2 products→⊕_{P2}; compatible
  ⊕_{P2}→⊕_{sub}; ⊖(⊕_{P1},⊕_{sub})→answer. Empty ⊕_{sub} ⇒ ⊖ = minuend.
- **OPTIONAL** (5 CONSTRUCTs) = the MINUS plan + one AND-branch over P1∪P2
  (`P1 OPTIONAL P2 = (P1 AND P2) UNION (P1 DIFF P2)`).

```
java -cp ../engine/target/npcs-rewrite.jar npcs.circuit.CircuitRun \
     Standard data/nonmono.reified.ttl queries/minus.sparql    > data/minus.circuit.nt
java -cp ../engine/target/npcs-rewrite.jar npcs.circuit.CircuitRun \
     Standard data/nonmono.reified.ttl queries/optional.sparql > data/optional.circuit.nt
python3 verify_nonmono.py       # MINUS + OPTIONAL: ALL MATCH PWE
```

## Canonical, collision-free ⊗ gates in SPARQL — done ✅

`CircuitRewriter` emits a **comparator network** (bubble sort in pure SPARQL 1.1
`IF/CONCAT/STR/<=`) that hashes each ⊗-child with `SHA256`, sorts the fixed-width
hex hashes, and concatenates them (delimiter-safe) before the final `SHA256` gate
IRI. A ⊗-gate's content address is thus a **canonical, order-independent,
injective** function of its child *multiset* — closing the `issue.txt`
multiset-hash concern (no SUM/COUNT; sorted-hash serialization is injective).
Products differing only in derivation order (e.g. a self-join's two orderings)
collapse to one shared gate.

`verify_all.py` checks per example both correctness (WMC == PWE) and
canonicalization (#Times-gates == #distinct child-multisets ⇒ no congruent ⊗):
```
[drug]     correctness=OK  Times=3 distinct-multisets=3 canonical=YES
[selfjoin] correctness=OK  Times=3 distinct-multisets=3 canonical=YES   (was 4 without sorting)
[minus]    correctness=OK  Times=4 distinct-multisets=4 canonical=YES
[optional] correctness=OK  Times=6 distinct-multisets=6 canonical=YES
```
⊕/answer/group/reach gate keys are now **collision-resistant + term-type-aware**: each binding is
kind-tagged (IRI/literal/blank/unbound) and per-part `SHA256`-hashed before concatenation (`termHash`,
same discipline as the product-gate key), so distinct RDF terms — IRI vs same-lexical literal, differing
datatype/language tag, or bound-vs-unbound — never collapse to one gate. Answer **recovery** is via the
structured `c:binding`/`c:var`/`c:val` nodes (which preserve the RDF term losslessly); the readable
`c:answer "A|var=value|…"` literal is a **debug label only** — it is `STR()`-based and *not* injective, so
do not key or de-duplicate on it. Regression: `verify_answer_keys.py`; term-aware oracle: `verify_gallery.py`.

## Knowledge compilation (d-DNNF via ROBDD) — done ✅

`compile_bdd.py` compiles a shared provenance circuit into a **reduced ordered
BDD** (a subclass of d-DNNF) and computes the answer probability by **weighted
model counting linear in the compiled size** — replacing the 2^n possible-world
enumeration. Pure Python, **zero dependencies, native on Apple Silicon / M4**
(no c2d/d4 binary needed; those are optional for standard-format d-DNNF numbers).

```
python3 bdd_demo.py
# (1) BDD-WMC == enumeration == PWE on every engine circuit
# (2) scaling: shared-hub circuit, N=100 leaves -> 2^100 worlds, BDD-WMC in ~5 ms
```

Note on platform: c2d is Linux-x86_64-only (needs Docker on M4); d4 builds from
source on arm64; PySDD / `dd` are pip-installable and native. The bundled ROBDD
avoids all of that.

## Factored (multi-pass) construction — done ✅ (`factor.py`)

`factor.factored_bgp` builds the circuit by **variable elimination** (one pass per
eliminated join variable): join the relations mentioning the variable (⊗), then
marginalize it (⊕); intermediate ⊗/⊕ gates are content-addressed and shared.
The circuit stays polynomial of **fixed degree treewidth+1**, versus the flat
construction's degree-`#patterns`, while representing the same provenance.

`factor_demo.py`:
```
(A) drug 3-hop:  flat == factored == PWE   (factoring adds a couple gates on tiny queries)
(B) layered chain k=4, circuit ⊗-gates:
      W    flat(=W^4)   factored(=3W^2)   ratio
      8       4096          192           21x
     14      38416          588           65x     (ratio grows with W)
(C) flat == factored as Boolean functions, 2000 random worlds, W up to 8: OK
```

Construction (polynomial ✅) and *compilation* are decoupled — see next section.

## Compilation to a compiled circuit for scalable WMC (`compile_bdd.py`, `compile_sdd.py`)

Two compilers turn a provenance circuit into a compiled form where WMC is one
linear pass, replacing 2^n enumeration:
- `compile_bdd.py` — a self-contained **ROBDD** (OBDD ⊆ d-DNNF), zero deps, native on M4.
- `compile_sdd.py` — an **SDD** via PySDD, a *real structured d-DNNF-family compiler*
  (arm64-native). Used because **d4 is x86_64-only on this Mac** (its bundled PATOH
  partitioner has no arm64 build; d4 needs a Linux/x86 box or Rosetta toolchain).

### The precise picture (corrected — do not hang tractability on OBDD)

The tractability parameter is **treewidth**, and the compiler that *achieves* the
bound is **d-DNNF/SDD**, not OBDD:
- treewidth `k` ⇒ d-DNNF of size `O(n · 2^{O(k)})` — **linear in n**.
- OBDD only achieves the weaker **pathwidth** bound `O(n · 2^{O(pw)})`; and since
  `pw ≤ O(tw · log n)` (Korach–Solel), bounded treewidth still gives OBDD
  `n^{O(tw)}` — **polynomial, but treewidth in the exponent**.
- So within bounded treewidth *both* are polynomial; the gap is **polynomial
  degree** (d-DNNF `n^1` vs OBDD `n^{O(tw)}`), not exponential. A
  bounded-treewidth / high-pathwidth family with *super-polynomial* OBDD **cannot
  exist** (bounded tw ⇒ pw = O(log n) ⇒ poly OBDD). The exponential SDD≻OBDD
  separation (Bova 2016) needs **unbounded** but tree-structured treewidth —
  outside this paper's tractable island.

`compile_compare.py`, `vtree_ladder.py` confirm this and, importantly, **caveat #1**:
```
SDD == OBDD == PWE on every engine circuit.
high-treewidth (layered, tw~W): both blow up (exact WMC #P-hard; no compiler helps).
bounded-treewidth, giving BOTH compilers a fair structure-aware order:
    case         OBDD(str)   SDD-bal   SDD(str)   SDD-minimize
    layered W3      142        1159       687         356
    layered W4      718        4201      4343        1063
    chain  k9        76        1574       882         368
    chain  k12      349        5627      1868         444
```
SDD size is hugely vtree-sensitive (balanced vs minimize differ several-fold —
caveat #1), but **OBDD with a reasonable order is ≤ every SDD variant, including
`minimize`, on every family**. So there is **no instance here where SDD beats
OBDD**; an earlier apparent SDD win was an OBDD bad-order artifact. The d-DNNF
advantage (`O(n·2^{O(tw)})` vs OBDD's `n^{O(tw)}`) is **asymptotic** (larger n /
higher tw) and PySDD's heuristics do not realize it at these scales. This is
exactly why **d4 is a real baseline, not ceremony**: its hypergraph partitioner
targets a treewidth-good decomposition that PySDD misses, and is the tool to show
the d-DNNF advantage empirically — run on a Linux/x86 box (d4 is x86_64-only here:
bundled PATOH has no arm64 build).

Practical upshot: on M4, the **bundled OBDD (with a structure-aware order) is the
better compiler** for the accessible tractable-island instances; the *tractability
claim* still rests on d-DNNF theory (not OBDD), and the crisp d-DNNF-vs-OBDD
comparison is deferred to d4-at-scale on Linux.

## Engine-native factored construction — done ✅ (`factor_native.py`)

Variable elimination run as a sequence of **SPARQL INSERT passes on an unmodified
engine** (rdflib here; the same SPARQL runs on GraphDB/RDF4J), generalizing the
flat engine-native γ (`npcs.circuit.CircuitRewriter`) to the multi-pass plan.
Message relations are materialized as RDF (`urn:m:msg/g`, `urn:mv:VAR`); each pass
is a base (⊕ per binding), a join (⊗, content-addressed, sorted 2-child key), or a
marginalize (⊕ per remaining binding). Left-deep with early marginalization →
running message stays small (frontier) → polynomial.

`factor_native_test.py` (BGP; MINUS/OPTIONAL stay on the flat plan):
```
 k,W  passes  native ⊗,⊕   factor.py ⊗,⊕   ≡factor.py   WMC==PWE(W=2)
(3,2)   8      (8,26)        (8,4)            True          True
(4,4)  11     (48,172)      (48,12)           True           -
(5,3)  14     (36,171)      (36,12)           True           -
```
`passes` grows with query size not data; ⊗-gates = (k-1)W² match `factor.py`
(polynomial); functionally identical on random worlds; WMC==PWE where feasible.
(The native left-deep plan makes a few more ⊕ gates than `factor.py`'s grouping —
still polynomial; a minor optimization.)

## Deployed engine (GraphDB) + d4 export — done / turnkey ✅

**Real deployed triple store.** `graphdb_harness.sh` runs the flat engine-native
CONSTRUCT on a **GraphDB 10.7** server (not in-memory): create repo → load reified
data → POST our CONSTRUCT → get the circuit back as N-Triples. On the drug example
GraphDB returns the **same 19-triple circuit** as RDF4J (3 Times, 2 Plus, p1 & p3
each shared across 2 gates); compiling it gives **Clopidogrel 0.358800, Omeprazole
0.774298 = PWE**. So the method is engine-agnostic on a *deployed* endpoint, end to
end. (Gotcha baked into the script: extract the CONSTRUCT with explicit file
redirection — `2>plan.txt >/dev/null` — because zsh's MULTIOS tees both streams.)

**d4 export (Tseitin CNF).** `export_cnf.py` writes a weighted DIMACS `cnf/*.cnf`
per circuit + `cnf/manifest.json` (expected WMC + our OBDD size), verified here by
an independent brute-force WMC == PWE. `d4_pipeline.py` + `D4_ON_LINUX.md` are the
turnkey Linux step: build d4, compile each CNF → d-DNNF size + WMC, check vs the
manifest → the `ddnnf_nodes` vs `obdd_size` scaling figure (the comparison
experiment A showed cannot be produced on Apple Silicon — d4's PATOH is x86_64-only).

## Evaluation — headline metric: shared circuit vs per-answer strings (`bench.py`)

NPCS/SPARQLprov serialize each answer's provenance as a string (every derivation
spelled out, shared subterms repeated): total size `T_string = Σ_answers Σ_derivations arity`.
Our shared circuit stores each distinct gate once: `T_circuit = #gates + #edges`.
`bench.py` (engine-independent — GraphDB and RDF4J emit the identical circuit):
```
instance      answers  derivations  T_string  T_circuit  sharing   build
drug                2          3          9        25       0.4x    0.1ms   ← trivial query: circuit has a small overhead
layered-4×4         4        256       1024       256       4.0x    0.4ms
layered-4×8         8       4096      16384       992      16.5x    1.0ms
deep-8×2            2        256       2048       156      13.1x    0.3ms
deep-12×2           2       4096      49152       244     201.4x    0.6ms   ← 2^11 paths/answer: strings blow up, circuit stays linear
```
The point: as soon as derivations share structure, the string representation grows
with the number of derivations (exponential in query depth for `deep-k×2`), while
the shared circuit stays polynomial — 200× smaller at depth 12, and the gap widens.
On trivial queries (drug) the circuit carries a small constant overhead — honest,
and irrelevant at scale. (WMC feasibility is the separate treewidth story above.)

### Deployed-engine timings (GraphDB) — `bench_engine.py`

2-hop join over random sparse graphs of growing size, timed on a live GraphDB 10.7
server (load → engine runs our CONSTRUCT → client compile+WMC), with the NPCS
string query for comparison:
```
   N  triples  load_ms  build_ms  circuit_tr  answers  npcs_ms  npcs_KB  wmc_ms
 100      900       93       183        5310      864       29       44       4
 400     3600       67       172       21495     3561       31      187      16
 800     7200       90       289       43057     7136       36      383       -
1500    13500      119       420       80910    13464       55      738       -
```
Engine circuit-build scales ~linearly with data and stays **sub-second** (420 ms
at 13.5k triples, materializing an 80k-triple circuit); client compile+WMC is a
few ms at the sizes run. (2-hop has little shared structure, so here the circuit
is not much smaller than the string — the compactness win shows on *deep* queries,
`deep-12×2` above. This table is the "runs on a deployed engine, at scale, end to
end" evidence.)

### Real-KG WatDiv run — `watdiv_run.py`, `watdiv_factor.py` (see `watdiv/RESULTS.md`)

A genuine WatDiv graph (`pilot/data/base.nt`, **51,863 triples**) reified to 155,589
statement triples, loaded into GraphDB in 2.1 s. Real star / path / snowflake shapes:

```
  query    ans  deriv(⊗)   gates   build_ms  wmc_ms |  T_str  T_circ  share(struct)
 S-star   2415     49375   72856      3564     741  | 148125  270356    0.55x
 L-path  15224     16856   45024       786     124  |  50568  112448    0.45x
 F-snow   5659     16856   36037       981     214  |  67424  120317    0.56x
```
Runs **end-to-end on a deployed triplestore over a real KG**: sub-4 s engine build,
sub-second client WMC (star/path are treewidth-1), correct. Honest finding: on these
**shallow** shapes the *flat* circuit does **not** beat strings structurally (~0.5×) —
little cross-derivation sharing; the compactness win is the *deep* regime above
(201×). Here the circuit's value is enabling tractable WMC on an unmodified engine.

**Factored construction is the real lever on real data** — variable elimination
collapses the per-user existential cross-product, WMC provably unchanged:
```
  query   FLAT gates  FACTORED gates  reduction  WMC==flat
 S-star       92561          31615      2.9x        yes
 F-snow       46299          26372      1.8x        yes
 L-path       37313          34583      1.1x        yes   (all vars bound; nothing to eliminate)
```
2.9× on star / 1.8× on snowflake, scaling with how existential the shape is — the
factored contribution motivated on real WatDiv, not just synthetic layered graphs.

## Not yet (next steps)
1. **Scale study — remaining**: ~~real KGs (WatDiv)~~ ✅ done (above); still open —
   Wikidata slice from `pilot/`, and a head-to-head with SPARQLprov (decode cost) and
   ProvSQL (relational encoding).
2. **d4 scaling figure** on a Linux/x86 box (CNFs + pipeline are ready).
3. Port `factor_native` passes into the Java `npcs.circuit` package (flat γ is Java;
   factored passes are currently Python/rdflib-driven SPARQL).
