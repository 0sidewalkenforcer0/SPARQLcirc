# Evaluation plan — SPARQL_circ

This is a pre-registration: for every experiment we fix the setup **and write the
predicted result with its cost model before running**, so a surprising number reads
as a finding (or a bug) rather than a moving target. Each experiment maps to one
claim from the paper.

Unless an experiment explicitly measures loading or is a short correctness probe, the canonical wall-clock
limits are a **300 s query-side hard budget** and **120 s per OBDD or d4/d-DNNF compilation attempt**
(`reference/experiment_timeouts.py`). Single-shot/legacy scripts apply the query budget per execution;
the R9.2 B/R/N/C protocol applies it once to the whole method cell, including rewrite, warm-ups,
measured runs, response drains, and all C-plan steps. Timeout observations remain in result tables and plots.

## Claims the evaluation must defend

- **A. Unmodified-engine construction** — a stock SPARQL engine builds the shared circuit (vs ProvSQL's modified PostgreSQL).
- **B. Compactness by sharing** — one shared circuit ≪ per-answer strings (NPCS/SPARQLprov), and that is *why* PQE is feasible.
- **C. Exact PQE, including non-monotone** — OPTIONAL/MINUS via ⊖, exact probabilities.
- **D. Tractability tied to treewidth** — compile+WMC cost governed by tw; factored construction keeps the circuit polynomial.

| Exp | Proves | Predicted headline |
|---|---|---|
| E1 Correctness | C | WMC == possible-world enumeration, exact, all operators |
| E2 Compactness vs strings | B | flat ≈ strings on shallow (≈0.5–1×); factored/deep → 10²–10³×, unbounded |
| E3 Construction scaling | A | build ≈ small const × plain-query time; near-linear; engine-agnostic circuit *(pre-registered target; legacy `plain_ms` actually measured NPCS, so ROUND 9 adds true B/R controls)* |
| E4 Compile+WMC vs treewidth | D | bounded-tw: d-DNNF linear in n, OBDD n^{O(tw)}; growing-tw: all blow up |
| E5 Factored vs flat | D | star ≈ ∏deg/∑deg; deep chain ≈ W^{k−2}; path = 1× |
| E6 Non-monotone | C | correct; ⊖ cost linear in operand size; baselines can't do it |
| E7 End-to-end vs baselines | A,B,C | vs NPCS/SPARQLprov: PQE they lack (decode cost); vs ProvSQL: comparable PQE, unmodified engine |
| E8 Wikidata (real-KG scale) | A,B,C | full fragment + **property paths** on a 10⁸–10⁹-triple real KG; matches SPARQLprov (942M) & NPCS (WDBench); paths (`P279+`/`P131+`) the baselines cannot do |
| E9 TPC-H (relational→RDF scale) | A,B | **non-aggregate SPJ/MINUS skeleton** at 1.2M–123M (= SPARQLprov "base non-aggregate"); construction scaling on relational-derived RDF; aggregation out of scope. **Compares vs SPARQLprov + ProvSQL only — NPCS never ran TPC-H (it is RDF-native)** |
| E10 Multi-engine byte-identity | A | same query → **identical `circuit_sha256`** on GraphDB + Fuseki + Oxigraph (independent codebases, incl. non-Java Rust); non-path construction at Wikidata scale on QLever. The circuit is a property of the *rewrite*, not the engine |
| E11 Per-answer vs shared PQE | B,C | compile NPCS/SPARQLprov per-answer how-provenance with **our** compiler: same probabilities (Δ≤1e-16); shared pass is **Θ(N+S) vs Θ(N·S)** per-answer (compile once vs N times → time win grows with N, ~9× at N=1000) plus the E2 representation win; SPARQLprov's MINUS provenance → **wrong** probability |

---

## Predicted trends by query shape (pre-registered — compare results against this)

**Two costs, two independent knobs:**
- **Construction** (engine builds the circuit) ∝ **#derivations `D`**; for query depth `d`,
  branching `b`, `D ~ b^d`. Factored construction cuts the intermediate to the *frontier* →
  polynomial in treewidth.
- **Compilation + WMC** ∝ compiled size `~ n·2^{O(tw)}` (d-DNNF) / `n^{O(tw)}` (OBDD),
  `tw` = treewidth of the lineage; WMC linear in that.
- **Compactness** (circuit vs strings): `T_string ∝ D ~ b^d`, `T_circuit(factored) ∝
  poly(tw,n)` → ratio `~ b^d/poly`, unbounded in depth/branching, ≈1 when `D` small.

So **treewidth governs compile; depth/branching governs construction & compactness** — they
move independently.

| shape | join graph / tw | #deriv `D` | sharing (fact.) | build | compile+WMC | bottleneck |
|---|---|---|---|---|---|---|
| single triple | tw 1 | 1 | 1× | O(#ans) | trivial | — |
| path / linear (L) len ℓ | path, **tw 1** | `b^ℓ` | `~b^ℓ/ℓ` ↑ | flat ∝`D` / fact. ∝`ℓ·W` | **linear** | construction |
| star (S) breadth k, deg d | star, **tw 1** | `d^k` | `~∏/∑` ↑ | flat ∝`d^k` / fact. ∝`k·d` | trivial | construction (flat) |
| snowflake (F) | tree, **tw 1–2** | `∏ b^ℓ` | high ↑ | flat ∝`D` / fact. poly | small | construction |
| cycle len ℓ | 1 cycle, **tw 2** | data-dep | moderate | ∝`D` | const× path | balanced |
| complex (C) | few cycles, **tw 2–3** | data-dep | moderate | ∝`D` | grows w/ tw | compile bites |
| grid k×k *(synthetic)* | grid, **tw = k↑** | large | — | ∝`D` | **2^Θ(k) wall** | compilation |
| clique k *(synthetic)* | clique, **tw = k−1** | large | — | ∝`D` | 2^Θ(k) (earliest) | compilation |

**Predicted regime boundaries:**
- Compactness ratio ≈1 until `D`/answer exceeds ~O(1), then ~exponential in depth/branching
  (anchors: WatDiv shallow ≈0.5×; layered depth-12 = 201×).
- Compile cliff at **tw ≈ 20–25** (2^tw memory wall). WatDiv S/L/F/C are all **low tw (1–3)**
  → compile uniformly cheap; the wall appears only in synthetic grid/clique — why **E4 is
  synthetic-only**.
- Construction ∝ `D` (flat) → deep/broad queries make construction the bottleneck even when
  compile is trivial → **factored is essential precisely for S/L/F**.
- Data size N (fixed shape): #answers ∝ N, build ∝ N, per-answer tw ~const → total compile ∝ N.

**Falsification plan (formal vs theory):** WatDiv S/L/F with *expensive* compile ⇒ real-data
tw higher than expected (a finding) or a bug; compactness ≈1 on a *deep* query ⇒ factoring
not firing (a UNION-as-join-class bug); build not ∝ `D` ⇒ engine optimization worth
explaining; grid not walling by tw~25 ⇒ tw estimate off or compiler beats the bound. WatDiv
classes map as: **S = star (tw1), L = linear (tw1), F = snowflake (tw1–2), C = complex
(tw2–3)** — the standard workload is low-tw, so it stresses construction/compactness, not
compilation.

---

## E1 — Correctness / exactness  *(status: piloted — `verify_gallery.py`, `verify_engine_native.py`)*

- **Proves:** C. The full pipeline computes the *exact* probability.
- **Setup:** instances small enough for possible-world enumeration (≤ ~25 tokens) as ground truth; random BGP/UNION/OPTIONAL/MINUS × random sparse data × random leaf probabilities; cross-check OBDD, SDD, d4 agree.
- **Metric:** max |WMC − PWE|.
- **Prediction:** **0** up to float ε for every operator — the Boolean abstraction (⊗→∧, ⊕→∨, ⊖(a,b)→a∧¬b) is exactly the possible-world indicator; WMC is its expectation.
- **Success:** all instances exact. **Risk:** a non-monotone failure = a semantics bug (this is how the UNION-as-join bug was caught).

## E2 — Compactness: shared circuit vs per-answer strings  *(status: piloted — `bench.py`)*

- **Proves:** B.
- **Independent variables:** query depth; data structure (star / path / layered / grid / cyclic).
- **Baselines:** NPCS reproduction (string), SPARQLprov.
- **Metrics:** `T_string = Σ_ans Σ_deriv arity` vs `T_circuit = gates+edges` (flat and factored); serialized bytes; #derivations.
- **Cost model:** flat circuit ≈ `D·(arity+2) + L` where `D`=#derivations, `L`=#distinct leaves; strings ≈ `D·arity`. So **flat ≈ strings, slightly worse**, unless whole products recur. The big win is **shared subexpressions**, captured only by *factored* construction: layered depth-`k` width-2 has `D=2^{k−1}` (strings exponential) but factored circuit `Θ(k·W²)`.
- **Prediction:** sharing ratio ≈ 0.5–1× on shallow tree-like queries; **→ 201× at depth 12** (pilot), unbounded in depth for layered/cyclic. Plot ratio vs depth to show the crossover.
- **Success:** monotone growth of ratio with derivation-sharing; ≥ two orders of magnitude on the deep family. **Risk:** none to correctness; the honest message is "conditional on sharing," not universal.

## E3 — Construction scaling on a deployed, unmodified engine  *(status: piloted — `bench_engine.py`, `watdiv_run.py`)*

> **Measurement correction.** The pre-registered comparison below is against the original query, but the
> legacy `e3_run.py` implementation calls `get_npcs()` for its `plain_ms` column. Existing ratios are
> therefore CONSTRUCT/NPCS-provenance-SELECT, not CONSTRUCT/B. The R9 construction matrix introduces
> separate B (base), R (reification-only), N (NPCS), and C (circuit CONSTRUCT) measurements.

- **Proves:** A + deployability.
- **Independent variables:** data size (**WatDiv 10M / 100M / 1B**, see *E3 scale plan* below); engine (GraphDB for ≤100M, a lighter store for 1B).
- **Metrics:** circuit-build wall-clock (engine runs our CONSTRUCT); circuit size; #answers. The implemented
  legacy comparison is against the NPCS provenance SELECT (`plain_ms` is a misnomer), so its ratio is
  `CONSTRUCT/NPCS`; ROUND 9 separately measures the true B/R/N/C controls.
- **Cost model:** build is dominated by the engine's join evaluation (materializing derivations), plus
  `O(arity²)` `BIND`s for the current bubble-sort comparator network and per-gate `SHA256` work. For
  fixed query arity this remains a constant-factor overhead, so `build ≈ c · T_plain`, near-linear in
  the number of derivations.
- **Prediction:** sub-second→low-seconds at 10⁶–10⁷ (pilot: 420 ms / 13.5k triples); **byte-identical circuits across engines** (deterministic content-addressing); `c` a small constant.
- **Success:** near-linear scaling, `c` reported explicitly. **Risk:** if `SHA256` in SPARQL is slow on an engine, `c` could be several×; measure and report per engine.

## E4 — Compilation + WMC vs treewidth  *(status: DONE on synthetic families — `watdiv/e4_results.csv`, run with **d4** on the Linux/x86 server. Shows the predicted scaling: at bounded treewidth the d-DNNF stays small (≤ 5 270 nodes at n = 254) while the fixed-order OBDD blows up (299 k nodes at n = 94, `obdd-timeout` by n ≥ 126); at growing treewidth both hit the #P wall; `d4_wmc == expected` on every tractable instance. This is the public-compiler / knowledge-compilation win, and it delivered. Open follow-ups: (a) d4-**v1** over-counts LARGE real reconvergent cones, so G6 keeps d4 size-only there — refresh those with **d4-v2**; (b) pin the ProvSQL comparison to the same compiler stack. See `reference/RESULTS.md` §5.)*

- **Proves:** D — treewidth is the tractability parameter; a real d-DNNF compiler realizes it.
- **Families (from `reference/gen_families.py`):** bounded-tw growing-n (`layered(depth↑, width=2)`, `chain`); growing-tw (`layered(depth=3, width=k↑)`, `grid(k)`).
- **Compilers:** our OBDD (`compile_bdd.py`), SDD (`compile_sdd.py`), **d4** (`d4_pipeline.py`, `D4_ON_LINUX.md`).
- **Metrics:** compiled-form size (nodes), compile time, WMC time — vs tw and n.
- **Cost model / prediction:**
  - **Bounded tw, n→∞:** d-DNNF `O(n·2^{O(tw)})` = **linear in n**; OBDD gets only the pathwidth bound and `pw ≤ O(tw·log n)` ⇒ OBDD `n^{O(tw)}` — polynomial but *degree tw*. Expect d4 **linear**, OBDD **bending upward**, with a **crossover** as n grows.
  - **Growing tw (grid/wide-layered):** *all* compilers exponential `2^{Θ(tw)}` — the #P-hardness wall; d4 reaches larger tw before OOM than OBDD.
- **Honest hedge:** at *small* scale on tree-like islands, OBDD ≤ SDD already observed (PySDD's heuristic vtree doesn't realize the asymptotic gain). d4's win is **asymptotic** — push n/tw far enough to show it, else the claim is only "both tractable for bounded tw."
- **Success:** d-DNNF linear-in-n on bounded-tw; exponential wall at growing tw for all.

## E5 — Factored vs flat construction  *(status: piloted — `factor.py`, `watdiv_factor.py`; extend with `gen_families.py`)*

- **Proves:** D — factored (variable elimination) keeps the circuit polynomial (degree tw+1) where flat is degree #patterns.
- **Setup:** WatDiv star/snowflake (existentials projected out) + `layered`/`star` from `gen_families.py`; sweep degree/width; assert WMC(flat)==WMC(factored)==PWE; measure gates, build time, downstream compile+WMC.
- **Prediction:** star projecting out `a,b,c`: flat `∏deg`, factored `∑deg` → ratio ≈ `∏deg/∑deg` (pilot: **2.9×** at base WatDiv density, more when denser). Deep chain `k`,width `W`: flat `W^k`, factored `(k−1)W²` → ratio `W^{k−2}/(k−1)`, unbounded. Path (all vars projected): **1×** (nothing to eliminate; pilot 1.1×).
- **Success:** ratio matches `∏deg/∑deg` on stars; WMC unchanged; the 1× null case reproduced.

## E6 — Non-monotone operators  *(status: piloted — `verify_gallery.py`, `verify_nonmono.py`)*

- **Proves:** C + separates us from monotone-only provenance.
- **Setup:** MINUS/OPTIONAL-heavy queries on real + synthetic; WMC vs PWE; report plan cost (MINUS: guarded DIFF, one sub-plan per overlapping UNION branch; OPTIONAL: AND-branch + DIFF; composite operands reduced by normalize) and circuit size.
- **Prediction:** exact; ⊖/sub gates add cost linear in operand sizes; tractability class unchanged (⊖→a∧¬b compiles like any Boolean). **Qualitative:** SPARQLprov (monotone semiring) and NPCS (strings, no PQE) can't produce these probabilities — a "we can, they can't" result.

## E7 — End-to-end vs baselines  *(status: piloted — toy ProvSQL head-to-head done (`e7_results.csv`, 3 instances); the at-scale comparison folds into G2a/G3 and is in flight on the server)*

- **Proves:** A + B + C together.
- **Metric:** total time & space **to the exact answer probability**, plus a qualitative "requires engine modification?" column.
- **Baselines & predicted positioning:**
  - **NPCS / SPARQLprov:** emit *strings*, not probabilities → to get a probability you must **decode** the string into a factored form; measure the decode cost (grows with string size, which E2 shows explodes on deep queries). *They do not do PQE; we do, natively.*
  - **ProvSQL** (strongest — also builds a provenance circuit + knowledge-compiles): map RDF→relations and run the controlled Level-1 harness (`level1_d4_headtohead.py`): per answer, semantically equivalent Tseitin CNFs, the **same pinned d4v2 binary**, then linear d-DNNF evaluation. Measure rather than presume comparable time; do **not** claim byte-identical CNFs or that we count faster. Our win is axis A: unmodified, engine-agnostic, native RDF/SPARQL, no relational remodeling, no PostgreSQL fork. Shared compilation is evaluated separately in E11.

---

## Datasets — which one each experiment uses

Two independent size axes: **data scale** (KG size — matters only for construction) and
**provenance scale** (circuit size + lineage treewidth — governs PQE tractability, and is
decoupled from KG size). Each experiment lives on one axis.

| Exp | Dataset | Scale | Rationale |
|---|---|---|---|
| E1 | tiny random graphs + small `gen_families` | ≤ 25 tokens | ground truth = possible-world enum = 2ⁿ; must be enumerable |
| E2 | (a) WatDiv **100M** + `official_q_100M`; (b) `gen_families` layered/deep | 100M; families→10⁶ deriv | structural/engine-independent; real = honest ~0.5×, families = unbounded win |
| E3 | WatDiv **10M/100M/1B** + real KG | up to **1B** | the deployability / "is a system" scale |
| E4 | `gen_families` only (layered, grid) | tokens 10→10⁶, tw 1→~25 | WatDiv cannot sweep tw; + record tw distribution of real 100M lineages |
| E5 | (a) WatDiv **100M** star/snowflake; (b) `gen_families` star | 100M; degree sweep | existential blow-up on real data + controlled ∏deg/∑deg |
| E6 | small enumerable + WatDiv **100M**/real-KG OPTIONAL/MINUS | ≤25 + 100M | exactness vs PWE, then scale |
| E7 | WatDiv **10M–100M** (baseline-limited) + real-KG subset | ≤ what ProvSQL/PG finish | fair head-to-head; show where string baselines OOM |
| E8 | Wikidata truthy dump / WDBench graph | **10⁸–10⁹** triples | real-KG scale; comparability with SPARQLprov (942M) & NPCS; property paths on `P279`/`P131` hierarchies (single-source, bounded reach). Queries: `reference/wikidata/*.rq` |
| E9 | TPC-H → RDF (direct mapping) | 1.2M–123M (SF `10^{i/4-2}`) | comparability with SPARQLprov & ProvSQL **only (NPCS never ran TPC-H)**; **non-aggregate, filter-free SPJ/MINUS only**; **per-row (`naryrel`) provenance**. Plan: `reference/tpch/README.md` |
| E10 | WatDiv slice + Wikidata | 10⁷ / 10⁹ | engine-agnostic Claim A across independent engines — GraphDB, Fuseki (SPARQLprov's), Oxigraph (Rust), QLever (scale), MillenniumDB (paths). Plan: `reference/engines/` |
| E11 | E2 families + shared-prefix N-sweep | N≤1000 | compile-time win at scale (Θ(N+S) vs Θ(N·S), ~9× at N=1000) + representation win up to 13× (201× in E2); correctness parity; SPARQLprov MINUS wrong. Plan: `reference/RESULTS.md` §4 |

- **`base.nt` (51K) is retired** to a CI/smoke correctness fixture — it is *not* an experiment dataset (3–4 orders below the VLDB bar).
- **In-memory `watdiv_factor.py` is small-scale only** (can't hold 100M in RAM); the 100M E5 runs through the engine (CONSTRUCT on GraphDB), not the Python in-memory factor.
- **Real KG** (Wikidata truthy subset / DBpedia / YAGO) for external validity in E3/E6/E7.
- **Probability source (threat to validity — state explicitly):** probabilistic KGs are scarce. Use extraction confidences (NELL), Wikidata reference ranks, or calibrated synthetic. E1/E2/E4/E5 are probability-independent (correctness/size) → random fine; E6/E7 use a realistic source.

### E3 scale plan (the big-data experiment)

Available on disk (`pilot/data/`): `watdiv.10M.nt` (1.4 GB), `watdiv.100M.nt` (**15 GB**),
query instances in `official_q/` and `official_q_100M/`. Generator: `pilot/tools/gen_watdiv.py`.

```bash
# 1. (1B tier only) generate with the WatDiv generator; 10M/100M already present
# 2. reify: <s> <p> <o> .  ->  <t> rdf:subject <s> ; rdf:predicate <p> ; rdf:object <o> .
python3 reference/watdiv/reify.py pilot/data/watdiv.100M.nt watdiv.100M.reified.nt
# 3. bulk-load into a triplestore, create repo "watdiv", POST the reified N-Triples
# 4. run the pipeline (engine builds the circuit via our CONSTRUCT):
WATDIV_QDIR=pilot/data/official_q_100M python3 reference/watdiv_run.py
```

**Engine by tier:** GraphDB for ≤ 100M; for **1B** use a lighter-footprint store
(Virtuoso, Jena TDB2, or RDFox) — the circuit CONSTRUCT uses only standard SPARQL 1.1, so
it is engine-portable (already shown identical on RDF4J + GraphDB).

**Reification blow-up (a real cost to report):** each triple → 3 statement triples, so 100M
→ 300M reified ≈ 30–45 GB on disk, plus index. Report it, and note the `SPARQL_Star`
reification scheme as the compact alternative on RDF-star-native engines.

> ⚠️ **Feasibility on this workstation:** ~25 GB free — 100M reified (~30–45 GB) does **not
> fit** here. The scripts are pointed at the 100M assets, but the 100M/1B reify+load+run is a
> **server task**. The largest tier that fits locally is **10M** (~4.5 GB reified).

## Controlled variables

Vary one at a time: query depth, treewidth, #answers, #derivations-per-answer, data size.

## Reproduction pointers

| Exp | script(s) |
|---|---|
| E1 | `reference/verify_gallery.py`, `reference/verify_engine_native.py` |
| E2 | `reference/bench.py` |
| E3 | `reference/bench_engine.py`, `reference/watdiv_run.py` |
| E4 | `reference/gen_families.py` → `compile_bdd.py` / `compile_sdd.py` / `export_cnf.py` + `d4_pipeline.py` (`D4_ON_LINUX.md`) |
| E5 | `reference/factor.py`, `reference/watdiv_factor.py`, `reference/gen_families.py` |
| E6 | `reference/verify_gallery.py`, `reference/verify_nonmono.py` |
| E7 | `provsql/` (ProvSQL harness), NPCS/SPARQLprov decode |

## The one caveat baked into every claim

The compactness/factoring wins are **conditional on derivation-sharing and low treewidth** — exactly the regime where PQE is tractable at all. On shallow tree-like queries the flat circuit ≈ strings and PQE is easy for everyone; on high-treewidth queries *everyone* is #P-hard. The defensible thesis is therefore not "always smaller/faster," but: **on an unmodified engine we produce the shared circuit that makes exact PQE feasible exactly when it is theoretically feasible (bounded tw), including the non-monotone fragment — which no unmodified-engine baseline does.**
