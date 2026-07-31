# SPARQL_circ — Python reference, compilers, and evaluation

Implementation of the VLDB draft's core: build a **shared, content-addressed
provenance circuit** for a SPARQL query over a token-labeled probabilistic ABox,
then do exact **probabilistic query evaluation** on it. The Python reference covers
the non-monotone fragment plus property-path operators `/ | ^ + * ?`; the engine
scope and its endpoint restrictions are documented in `TECHREPORT.md` §4.6.

## Modules
- `gates.py` — the circuit DAG + **collision-resistant content-addressed** gate
  constructors (`leaf/times/plus/minus`). Congruent gates (same op + canonicalized
  children) collapse to one id → maximal sharing. Canonical id = `sha1(op | sorted(child-ids))`,
  duplicates kept (no idempotence, since `g⊕g = 2g` in `N[X]`). This is the
  unambiguous pre-hash serialization that fixes the `issue.txt` SUM+COUNT concern.
- `gamma.py` — builds the shared circuit for the non-monotone fragment
  (`bgp / union / join / optional / minus`), mirroring spm-semiring semantics
  (`OPTIONAL = (P1 AND P2) UNION (P1 DIFF P2)`), one root per answer, plus `project`.
- `wmc.py` — `prob()` = exact probability = WMC of the circuit's Boolean
  abstraction (`⊗→∧, ⊕→∨, ⊖(a,b)→a∧¬b`), memoized over the DAG; `pwe()` = ground
  truth by possible-world enumeration; `check()` compares them.
- `demo.py` — the paper's drug running example (Fig. 1/2).
- `compiler.py` — production CUDD adapter: one root map, shared/per-root modes,
  batch WMC, and reproducible compilation metrics.
- `compile_bdd.py` — dependency-free ROBDD correctness oracle.
- `pqe.py` — user-facing JSON CLI for existing circuits or a fresh Java construction.
- `tests.py` — correctness battery.

## Run
```
python3 demo.py      # reproduces Fig. 2: p1, p3 shared; probs match PWE
python3 tests.py     # 171/171 answer-probability checks vs PWE
python3 -m pip install -r requirements-production.txt
python3 pqe.py --circuit data/drug.circuit.nt --probabilities data/drug.probabilities.json
# default: shared multi-root CUDD; optional: --compile-mode per-root
```

## Status
- ✅ Circuit model + content-addressed sharing (collision-resistant ids).
- ✅ Non-monotone fragment (OPTIONAL/MINUS via ⊖ gates) — the moat.
- ✅ Exact PQE, verified == possible-world enumeration (171/171).
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
java -jar target/npcs-rewrite.jar circuit \
     Standard ../reference/data/drug.reified.ttl ../reference/queries/drug3hop.sparql \
     > /tmp/drug.circuit.nt
# compile and WMC exactly that newly built circuit:
cd ../reference && python3 pqe.py --circuit /tmp/drug.circuit.nt \
     --probabilities data/drug.probabilities.json
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

## Canonical, collision-resistant ⊗ gate ids in SPARQL — done ✅

`CircuitRewriter` emits a **comparator network** (bubble sort in pure SPARQL 1.1
`IF/CONCAT/STR/<=`) that hashes each ⊗-child with `SHA256`, sorts the fixed-width
hex hashes, and concatenates them (delimiter-safe) before the final `SHA256` gate
IRI. Before the final SHA-256, a ⊗-gate's key is a **canonical, order-independent,
injective serialization** of its sorted child sequence — closing the `issue.txt`
ambiguous-concatenation concern (no SUM/COUNT). The resulting id is collision-resistant.
Products differing only in derivation order (e.g. a self-join's two orderings)
collapse to one shared gate.

The engine's emitted RDF is consumed as a **Boolean event circuit for PQE**. Identical
`c:in`/`c:feeds` triples are set-deduplicated, so this RDF interchange does not preserve
`N[X]` coefficients such as `x²` or `2x`; `gates.py` does retain that algebraic
multiplicity. Boolean WMC is unchanged because repeated event operands are idempotent.

`verify_all.py` checks per example both correctness (WMC == PWE) and stable
fixture-level Times counts (the self-join count is the derivation-order sharing regression):
```
[drug]     correctness=OK  Times-gates=3/3 structure=OK Boolean-child-set-aliases=0
[selfjoin] correctness=OK  Times-gates=3/3 structure=OK Boolean-child-set-aliases=0
[minus]    correctness=OK  Times-gates=4/4 structure=OK Boolean-child-set-aliases=0
[optional] correctness=OK  Times-gates=6/6 structure=OK Boolean-child-set-aliases=0
```
RDF child sets alone cannot prove multiset-key uniqueness because `[x]` and `[x,x]`
serialize to the same unindexed edge set; the Java source-level tests exercise key
fingerprinting and independent MINUS/OPTIONAL identities directly.
⊕/answer/group/reach gate keys are now **collision-resistant + term-type-aware**: each binding is
kind-tagged (IRI/literal/blank/unbound) and per-part `SHA256`-hashed before concatenation (`termHash`,
same discipline as the product-gate key), so distinct RDF terms — IRI vs same-lexical literal, differing
datatype/language tag, or bound-vs-unbound — never collapse to one gate. Answer **recovery** is via the
structured `c:binding`/`c:var`/`c:val` nodes (which preserve the RDF term losslessly); the readable
`c:answer "A|var=value|…"` literal is a **debug label only** — it is `STR()`-based and *not* injective, so
do not key or de-duplicate on it. Regression: `verify_answer_keys.py`; term-aware oracle: `verify_gallery.py`.

## Production knowledge compilation (CUDD ROBDD) — done ✅

`compiler.py` compiles every answer root into a native **CUDD ROBDD** and computes
all probabilities by weighted model counting linear in the union of compiled
nodes. The default `shared` mode uses one CUDD manager, one source-gate memo, and
one WMC memo for the complete output vector. `per-root` uses an isolated manager
per answer while preserving the same deterministic global variable order. Roots
remain distinct handles; they are never collapsed with OR/AND.

`compile_bdd.py` implements the same Boolean abstraction in pure Python for
correctness tests. It is explicitly an oracle, not a competing production backend.

```
python3 bdd_demo.py
# (1) BDD-WMC == enumeration == PWE on every engine circuit
# (2) scaling: shared-hub circuit, N=100 leaves -> 2^100 worlds, BDD-WMC in ~5 ms
```

Run `python3 verify_compiler.py` to check shared/per-root parity, constant roots,
MINUS/complemented edges, cross-root sharing, and a depth-2100 circuit. Without
CUDD installed, this script still runs the oracle contract and reports the native
backend as unavailable.

`pqe.py` includes a `compilation` object in its JSON output. It records the
backend/version and mode, root/manager/variable counts, deterministic order
fingerprint, source gates/edges, compile and batch-WMC times, CUDD memory and
reordering statistics, the union-reachable compiled nodes, the sum of per-root
nodes, and their sharing difference. Compiled nodes are counted physically, so
CUDD's complemented edge bit does not create a second node.

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

## Production compiler and research baselines

The system exposes one production compiler: CUDD through `compiler.py`. The
choice visible to normal execution is compilation granularity (`shared` or
`per-root`), not a portfolio of unrelated compilers. `compile_bdd.py`,
`compile_sdd.py`, and d4 remain correctness/theory/evaluation baselines only.

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

Practical upshot: the production path uses a mature native OBDD implementation,
while the *tractability claim* must still be stated against d-DNNF theory (not
OBDD). PySDD and d4 measurements evaluate that theory; they are not user-facing
production choices.

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
GraphDB returns the **same 25-triple circuit** as RDF4J (19 core gates + 6 c:binding recovery; 3 Times, 2 Plus, p1 & p3
each shared across 2 gates); compiling it gives **Clopidogrel 0.358800, Omeprazole
0.774298 = PWE**. So the method is engine-agnostic on a *deployed* endpoint, end to
end. (Gotcha baked into the script: extract the CONSTRUCT with explicit file
redirection — `2>plan.txt >/dev/null` — because zsh's MULTIOS tees both streams.)

**d4 export (Tseitin CNF).** `export_cnf.py` writes a weighted DIMACS `cnf/*.cnf`
per circuit + `cnf/manifest.json` (expected WMC + our OBDD size), verified here by
an independent brute-force WMC == PWE. `d4_pipeline.py` + `D4_ON_LINUX.md` are the
turnkey Linux step: build d4, compile each CNF once → d-DNNF size + local linear WMC of the dump, check vs the
manifest → the `ddnnf_nodes` vs `obdd_size` scaling figure (the comparison
experiment A showed cannot be produced on Apple Silicon — d4's PATOH is x86_64-only).
`level1_d4_headtohead.py` is the separate controlled ProvSQL comparison: per answer, both systems feed
semantically equivalent Tseitin CNFs to the same pinned d4v2 binary; our normal shared compile remains G3/E11.

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
1. **Scale study — remaining**: ~~real KGs (WatDiv)~~ ✅; ~~Wikidata 2.13B statement graph~~ ✅ (E8);
   still open — **property paths at Wikidata scale = G1** (the frontier-only reachable-subgraph loop has
   landed; the large-reachable-set run is still pending — current path results are on *small* reachable
   subgraphs), and head-to-heads with **SPARQLprov decode cost = G5** and **ProvSQL at scale = G2a/G3**.
2. **d4 scaling figure** on a Linux/x86 box (CNFs + pipeline ready) = **G6**; note d4 WMC is
   compiled-size-only on real path circuits until the 8/16-vs-OBDD/PWE discrepancy is resolved.
3. **Factored construction into the Java `npcs.circuit` pipeline** — flat γ is Java; the factored
   (variable-elimination) passes are currently Python/rdflib-driven SPARQL. Until integrated, factored
   construction is a **reference/prototype optimization**, not a default of the end-to-end Java system.
