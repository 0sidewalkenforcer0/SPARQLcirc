# Result notes — baselines, rigor, space, and correctness at scale

Findings behind the evaluation, grouped by what they measure. One entry per topic; the working
codename each was written under (G2a, G2b, …) is kept in the heading so older commits and CSV names
still resolve. **Every headline timing lives in [`CANONICAL_TIMINGS.md`](CANONICAL_TIMINGS.md)** — the
tables below deliberately carry no wall-clock numbers, only what the measurement *showed*. The
research-question index is [`EVALUATION_MAP.md`](EVALUATION_MAP.md).

---

## 1. ProvSQL head-to-head — exact parity, no engine fork *(G2a, R8.3)*

ProvSQL (a modified PostgreSQL) is the only baseline that also computes probabilities, so it is the
one system we can compare *exact PQE* against end to end.

**Setup.** Official TPC-H `dbgen` at SF 0.01 / 0.1 / 0.3. ProvSQL side: the `.tbl` files loaded into
PostgreSQL (`g2a`/`g2a1` schemas), `add_provenance` → `set_prob(…, 0.5)` →
`probability(provenance())` per answer ([`tpch/g2a_provsql.sql`](tpch/g2a_provsql.sql), ProvSQL
1.11.0-dev). Our side: the *same* `.tbl` mapped to RDF by [`tpch/tbl_to_rdf.py`](tpch/tbl_to_rdf.py),
loaded into GraphDB, **per-row (naryrel)** provenance — the granularity ProvSQL and SPARQLprov use.
Query: TPC-H **Q3 SPJ**, filter-free, projecting `(o_orderkey, l_linenumber)`. Per-token p = 0.5 on
both sides.

**Findings.**

- **Probability parity at benchmark scale.** Both systems return exactly `0.5³ = 0.1250` for every one
  of the **14 908** (SF 0.01) and **125 154** (SF 0.1) Q3 answers. E7 had validated this agreement on
  3 hand-built instances; this holds it against ProvSQL's own possible-world semantics on real join
  output.
- **Reconvergent lineage — parity *definitively* established (R8.3).** Q3's answers are a single
  3-token product, so `0.125` is trivially right and tests execution compatibility rather than
  shared-circuit WMC. `tpch/skeletons/Qrecon.rq` (`SELECT ?cust WHERE { ?cust c_mktsegment "BUILDING" .
  ?order o_cust ?cust }`) gives each answer `⊕ₖ(cust ⊗ orderₖ)` with the **cust token shared** across
  all K products, so the truth is `0.5·(1−0.5ᴷ) ∈ [0.375, 0.5]`, varying with K. Keyed by `c_custkey`,
  ours equals ProvSQL (`max_abs_error = 0.0`, `keys_match`) **and** equals the closed form
  (`cf_maxerr = 0.0`), at SF 0.01 and SF 0.1.
- **A naive per-answer product-sum is provably wrong here.** `Σₖ P(cust)·P(orderₖ) = 0.25·K` exceeds 1
  for **243/247** (SF 0.01) and **2058/2086** (SF 0.1) answers. Both the shared circuit and ProvSQL's
  semiring avoid it; a per-derivation baseline that multiplied-then-summed would not. This is *why* a
  real WMC over a shared circuit is needed.
- **The advantage is architectural, not latency.** We compute ProvSQL's probabilities on a **stock,
  unmodified SPARQL engine** emitting SPARQL-1.1-only CONSTRUCTs (byte-identical on 4 engines, E10)
  versus ProvSQL's **forked PostgreSQL** (C extension, custom aggregates, a `provenance` column type).
  The fragments also differ: ours covers property paths and full SPARQL at KG scale, ProvSQL covers
  relational aggregation (out of scope here). Complementary, not a race. Ours is currently also faster
  per query at these scales (see CANONICAL_TIMINGS), but that is not the claim.
- **Both scale ~linearly with join output** for this tree-join shape (no reconvergence; cf. §4).

**Caveats.**

- **Cold vs warm matters and is easy to get wrong.** An early cold ProvSQL first call (3.6 s) and a
  cold SF 0.1 `CREATE TABLE` (29.4 s) were both artifacts; all cited numbers are warm medians.
- **A `count(*)` wrapper lets PostgreSQL prune the evaluator.** The old ProvSQL target did not consume
  its projected probability, so the planner dropped the probability column and timed only the join.
  The honest target is `sum(probability_evaluate(provenance()))` with a `sum = 0.125·n` checksum per
  run; `g4_rigor.py` / `g4_instances.py` enforce it and refuse partial CSVs.
- We compare exact evaluator to exact evaluator; ProvSQL's `weightmc` d-DNNF path is not benchmarked.
  Shared HPC box, so absolute wall-clock is order-of-magnitude.

## 2. NPCS per-answer how-provenance vs the shared circuit *(G2b)*

Our executable **clean-room NPCS reimplementation** (`App Standard query`) against our CONSTRUCT plan,
same bound queries, same GraphDB WatDiv (32.7 M reified). `g2b_npcs_vs_ours.py` →
`g2b_npcs_vs_ours.csv`. This is not a run of the NPCS authors' official artifact, so it is labelled
"NPCS reimplementation" unless parity with a pinned official release is verified.

**Metric hygiene.** An earlier version divided *NPCS bytes ÷ our gate count* and called it "10–27×
smaller" — dimensionless nonsense. Sizes are three separate comparisons, never mixed: **structural**
(elements vs elements), **serialized** (bytes vs bytes), **compiled** (nodes vs nodes).

| query | answers | **structural** NPCS-occ / ours-g+e | **serialized** NPCS-B / ours-B |
|---|--:|--:|--:|
| S-star (bound)  |       2 | 162 / 272 = **0.6×** | 2 730 / 36 488 = **0.07×** |
| P2-path (bound) |      13 | 26 / 65 = **0.4×** | 1 836 / 22 770 = **0.08×** |
| P2-unbound      | 149 998 | 299 996 / 749 990 = **0.4×** | 20.8 MB / 262.8 MB = **0.08×** |

(structural = NPCS flat token-occurrences ÷ our shared gates+edges, with `T_string` = *actual* per-product
tokens (`e6fa2c7`); serialized = raw UTF-8 string bytes ÷ N-Triples bytes. Ratio < 1 ⇒ **ours larger**.)

**Findings.**

- **On these selective, low-sharing queries our circuit is NOT smaller — it is larger**, ~1.7–2.5× in
  structural elements and ~12× in serialized bytes. NPCS's flat per-answer token list is compact here
  because there is little cross-answer sharing to amortize the DAG's answer/product-gate and IRI
  overhead (SHA-256 content-addressed IRIs, ≈180 B/triple).
- **The compactness claim is *structural* and materializes with RECONVERGENCE**, not on these queries.
  E2's up-to-201× is on recursive/reconvergent workloads where flat strings duplicate shared
  sub-derivations per answer. The bound P2/S-star queries sit on the low-sharing side and show the
  honest opposite. Do not cite this as a size win.
- **Construction: NPCS is faster at scale** (P2-unbound 3.3 s vs our 28.9 s). Our plan pays per-gate
  SHA-256 content addressing — the cost that *buys* a compact, content-addressed, **WMC-able** circuit
  that dedups identically across engines.
- **The decisive difference is PQE, not size or construct speed.** NPCS emits per-answer strings and
  stops; we compile and WMC (184 ms for all 14 908 TPC-H Q3 answers). Completing NPCS per answer pays
  Θ(N·S) — see §4.

**Caveat.** Standard reification (3× blow-up; RDF-star halves it structurally, §7). WDBench's curated
Wikidata is the ideal substrate but its download is blocked to automation, so this runs at the same
32.7 M WatDiv scale the baselines use.

## 3. SPARQLprov's released rewriter, built and run *(G5)*

Backs the `T_string` cost model with the real system rather than only our reimplementation.
**Status: DONE.**

- Source: `SPARQLprov-experiments.zip` from <https://relweb.cs.aau.dk/sparqlprov/> (2021 artifact).
  SPARQLprov is a **C++ query rewriter** over an SPM polynomial semiring (`SPMPolynomial.{hpp,cpp}`,
  `rewritter.cpp`), not GProM-based.
- Build: `cmake .. -DCMAKE_POLICY_VERSION_MINIMUM=3.5 -DCMAKE_PREFIX_PATH=$CONDA_PREFIX && make`
  (its CMakeLists pins CMake 2.8 and a hardcoded `/usr/local/boost_1_76_0`; the policy shim plus conda
  Boost on `CPATH` fix it). Unit tests **`SPMPolynomialTest` 9/9, no errors**.
- Run: `./rewrite <scheme> <query.sparql>`, scheme ∈ `n` (named graphs) / `s` (standard) / `w`
  (Wikidata) — the same schemes our `Reification` supports.

**What it emits.** A SELECT that materializes the per-answer how-provenance polynomial as URI-encoded
strings via `BIND(IRI(CONCAT(...)))`: one `?prov_sum_sum_product_i_statement` per triple pattern,
combined into `?prov_sum_sum_product` (⊗), `?prov_sum_sum` and `?prov_sum` (⊕) — an explicit
sum-of-products, one row per answer. MINUS becomes an SPM **difference** operator
(`?prov_sum_difference_1_*` minuend, `_2_*` subtrahend), i.e. a monus. Rewritten-query blow-up:

| shape | input chars | rewritten chars | ratio |
|---|--:|--:|--:|
| watdiv/S1 (star)       | 566 | 4 619 | 8.2× |
| watdiv/L1 (linear)     | 233 | 1 790 | 7.7× |
| watdiv/F3 (snowflake)  | 424 | 3 229 | 7.6× |
| wikidata/star          | 423 | 2 912 | 6.9× |
| wikidata/minus         | 850 | 6 916 | 8.1× |

**Findings.** The `T_string` baseline is backed by the real system: SPARQLprov materializes a
per-answer sum-of-products (with a difference operator for MINUS) **as query results**, exactly the
class §2 measures. **It stops at provenance** — the rewriting ends at the `BIND(IRI(CONCAT(...)))` that
*names* the provenance; there is no model counting. Same boundary as NPCS.

**Caveat.** Measured here is the *rewriting* and its size. End-to-end `T_string` result bytes on data
need SPARQLprov's own reified layout on Virtuoso named graphs; §2 already gives result-byte numbers for
the same class. The built binaries live under `sparqlprov/SPARQLprov-experiments/SPARQLprov/build/`
(external artifact, not committed).

## 4. Completing the baselines into a PQE pipeline *(E11)*

NPCS and SPARQLprov emit how-provenance per answer and compute no probabilities. E11 *completes* them
with **our** compiler, so both sides use the same compiler and the only variable is the representation.
This answers "is the win the shared circuit, or your compiler?".
`e11_per_answer_vs_shared.py` → `e11_results.csv`, `e11_scale.csv`, `e11_minus.csv`, `e11_real.csv`.

### Result 1 — representation win (order-independent)

| instance | answers | deriv | T_string | T_circuit | **repr_win** | compiled (worst-case order) |
|---|--:|--:|--:|--:|--:|--:|
| drug | 2 | 3 | 9 | 25 | 0.4× | 1.00× |
| prefix-d8×N16 | 16 | 16 | 144 | 93 | 1.5× | 1.00× |
| layered-4×3 | 3 | 81 | 324 | 147 | 2.2× | 1.00× |
| layered-4×4 | 4 | 256 | 1024 | 256 | 4.0× | 1.00× |
| **deep-8×2** | 2 | 256 | 2048 | 156 | **13.1×** | 1.00× |

`repr_win = T_string / T_circuit` grows with sharing (201× at `deep-12×2` in E2). The
`compiled_win = 1.00×` column is a **worst-case artifact, not a law**: that DFS order places the shared
structure as a *prefix*, and a BDD only merges a shared sub-function sitting at the *bottom* of the
order. Flip the order and the reuse appears — Result 2.

### Result 2 — compile-time win at scale

`N` answers sharing a depth-8 sub-provenance (compiled size `S`), sharing-friendly order:

| N | shared size | per-answer size | size win | shared ms | per-answer ms | **time win** |
|--:|--:|--:|--:|--:|--:|--:|
| 50 | 58 | 450 | 7.8× | 0.4 | 2.6 | 6.0× |
| 200 | 208 | 1800 | 8.7× | 1.2 | 8.5 | 6.9× |
| 500 | 508 | 4500 | 8.9× | 4.3 | 26.7 | 6.2× |
| 1000 | 1008 | 9000 | 8.9× | 6.8 | 59.5 | **8.7×** |

**Mechanism.** Per-answer rebuilds the shared sub-BDD once per answer → **Θ(N·S)**; ours hash-conses it
once and all `N` answers point at it → **Θ(N + S)**. The absolute saving grows linearly with N.
**Conditions:** it needs actual cross-answer sharing (fully independent answers give nothing to reuse),
and for an OBDD the win is order-realizable (Results 1 and 2 are the same instances under different
orders); d-DNNF/d4 picks its own vtree and is far more robust.

### Result 3 — SPARQLprov's MINUS compiles to the WRONG probability

Disjoint-operand MINUS (`{?x likes ?y} MINUS {?z owns ?w}`, no shared variable):

| answer | ours (guarded, W3C) | SPARQLprov (unguarded DIFF) | PWE (truth) | verdict |
|---|--:|--:|--:|---|
| ?x=A, ?y=X | 0.5000 | 0.2500 | 0.5000 | ours OK; **SPARQLprov WRONG** |
| ?x=B, ?y=Y | 0.5000 | 0.2500 | 0.5000 | ours OK; **SPARQLprov WRONG** |

SPARQLprov realizes MINUS as an *unguarded* DIFF; on disjoint operands it over-subtracts. Ours (the W3C
shared-variable guard, see [`../docs/CONFORMANCE.md`](../docs/CONFORMANCE.md) §3) matches possible-world
enumeration — the MINUS bug as a *measured wrong probability*.

### Result 4 — on real data the win is co-extensive with reconvergence

| query | answers | deriv | repr_win | note |
|---|--:|--:|--:|---|
| WatDiv friendOf 1/2/3-hop | 6 / 24 / 77 | ≈ answers | 1.0 / 0.48 / 0.63× | random graph → tree-like |
| TPC-H Q3 → (order, line) | 784 | 784 | 0.59× | 1 derivation/answer (pure join) |
| TPC-H Q3 → order | 205 | 784 | 0.75× | lineitems sum per order |
| TPC-H Q3 → cust | 12 | 784 | 0.84× | deepest sum, still ≤ 1 |

Both are tree-structured joins: every derivation ends in a distinct token, so `#derivations ≈ #tokens`
and there is no reconvergence to exploit. Projecting to a coarser grain raises the ratio monotonically
(0.59 → 0.84×) but never past 1, at any scale.

**The boundary.** The representation and PQE win requires `#derivations ≫ #tokens` — **reconvergence** —
which in SPARQL comes from **recursion, i.e. property paths** (circuit gates `~n²` vs `~e·(n−2)!` simple
paths, unbounded), *precisely the fragment NPCS/SPARQLprov cannot express*. So: on tree joins (most
relational/RDF BGPs) PQE is a **tie**, and we win on native construction, engine-agnosticism and correct
non-monotone evaluation; on recursive paths the win is **unbounded** and the baselines cannot compete at
all. Not a universal speedup — a decisive one exactly where SPARQL provenance blows up.

## 5. Correctness of the real circuits *(G6)*

The end-to-end runs use our fixed-order OBDD, but E1 only ever checked `WMC == PWE` on the gallery and
synthetic families. This closes that on the **real** WatDiv / TPC-H / Wikidata-path circuits:
brute-force **possible-world enumeration** (no compilation, no variable order) plus a **d4** d-DNNF
compile. `g6_d4_real.py` → `g6_d4.csv`, on the current `PathIsoSeq` jar. d4 is used as `-dDNNF` followed
by a **local linear WMC of the dump** (`ddnnf_wmc.py`), never `d4 -mc`.

| query | dataset | answers | sampled | **OBDD == PWE** | d4 == OBDD | d-DNNF nodes (med) |
|---|---|--:|--:|:--:|:--:|--:|
| watdiv-Sstar    | WatDiv 32.7 M           |      2 |  2 | **2/2** | 2/2 | 171 |
| tpch-Q3         | TPC-H 1.26 M            | 14 908 |  8 | **8/8** | 8/8 |   2 |
| wikidata-WDpath | Wikidata 2.13 B (P279+) |     16 | 16 | **16/16** | 16/16 |   3 |

**Findings.**

- **Real-circuit probabilities are ground-truth correct and order-independent** — 26/26 sampled answers,
  including all 16 reconvergent property-path answers. PWE uses no compilation and no variable order, so
  the headline numbers do not depend on the OBDD heuristic.
- **`PathIsoSeq` removed the reconvergent-path blow-up.** The `2e58788` un-merging briefly produced huge
  WD-path cones (19 → 233 tokens); per-path fingerprint isolation (`579a7c8`) collapses them to **≤ 20
  tokens**, so the fixed-order OBDD compiles them trivially and PWE covers all 16. The
  "order-robust d4 for paths" motivation is gone for these paths; E4's synthetic high-treewidth families
  remain the real order-robustness case.
- **The d4-v1 `-mc` over-count is resolved by computing the WMC ourselves.** d4's *compilation* was
  always sound; only its weighted-count post-processing mis-applied external weights (one big cone gave
  d4 = 0.125 vs OBDD = PWE = 0.015625). Compiling with `-dDNNF` and running our own linear WMC over the
  dump matches the OBDD 26/26.
- **No d4-v2 is needed** *(supersedes the "level-1 d4v2 head-to-head" status note)*. The d4-v2 tasks
  existed only to fix that weighted-count over-count on large reconvergent path CNFs; the local d-DNNF
  WMC resolves it without d4-v2, whose build is author-gated anyway.

## 6. Space and memory at scale *(G8)*

`g8_space_memory.py` → `g8_space_memory.csv`, post-`2e58788` jar.

**Peak build memory.** Building the Wikidata `P279+` path on a **2.13-billion-triple** store, the
`CircuitRun` client peaks at **161 MB** RSS (8 g cap, 2.6 s wall). The client footprint is bounded by
the **reachable subgraph, not the graph size**: the frontier-restricted protocol pulls only what the
path reaches and materializes gates back into the store, so the 2.13 B triples stay in GraphDB's own
heap. This is the memory result behind "paths at KG scale".

**On-disk circuit size (N-Triples).**

| query | answers | gates+edges | circuit bytes | NPCS string bytes | shared-OBDD nodes |
|---|--:|--:|--:|--:|--:|
| watdiv-Sstar     |       2 |     272 |  36.5 KB |  2.6 KB |    34 |
| tpch-Q3          |  14 908 |  89 448 |  28.0 MB |    —    | 44 724 |
| watdiv-P2unbound | 149 998 | 749 990 | **263 MB** | **19.9 MB** | (n/a — large) |

**Findings.**

- **Provenance structure is unchanged by the identity fix; raw bytes roughly doubled.** `gates+edges` is
  identical pre/post-`2e58788` for these BGP shapes; the N-Triples growth (P2-unbound 133 → 263 MB) is
  purely the `urn:circuit:binding` / `c:var` / `c:val` **answer-recovery metadata**, a separable
  recoverability feature.
- **The compiled form is tiny; the serialization is IRI-heavy.** Gates are content-addressed with
  SHA-256 IRIs (≈180 B/triple), so the raw dump is large, but the representation the PQE consumes is
  small: one shared OBDD of 44 724 nodes for all 14 908 Q3 answers, d-DNNFs of tens of nodes.
- **Raw bytes vs NPCS is reconvergence-dependent** — the same boundary as §4. On low-sharing P2-unbound
  the flat strings are *smaller* in raw bytes. The circuit's invariant wins are structural
  non-redundancy, a compact compiled form, and being **WMC-able** at all.
- **A compact store would erase the IRI overhead.** Interning gate IRIs to integer node ids collapses
  ~180 B/triple to a few bytes/edge; SHA-256 IRIs are a *portability* choice (any engine dedups
  identically), not a storage-optimal one.

**Caveat.** Peak RSS is the client `CircuitRun` process (VmHWM via `/proc`); GraphDB's server RSS is
separate and holds the base graph.

## 7. Reification: RDF-star vs Standard *(G7)*

Standard reification turns `s p o` into three triples; on RDF-star engines the same fact is one quoted
triple `<< s p o >> occ:occurrenceOf t`. On a 100 k-triple WatDiv sample:

| encoding | bytes | vs raw | B/fact | triples/fact |
|---|--:|--:|--:|--:|
| raw `.nt`            | 11.67 MB | 1.00× | 117 | 1 |
| **Standard reified** | 32.24 MB | 2.76× | 322 | **3** |
| **SPARQL-star**      | 17.06 MB | 1.46× | 170 | **1** |

**3× fewer triples and 1.89× fewer bytes** (bytes shrink less than triples because SPARQL-star still
spells out `s`, `p`, `o` once, while Standard repeats the token IRI three times plus three `rdf:`
predicates).

**The circuit is reification-independent, verified on the actual RDF circuit.**
`verify_g7_circuit_equiv.py` runs the full `CircuitRun` pipeline under both schemes and canonical-diffs
the emitted circuits — sorted N-Triples byte-identity *and* identical gate DAG:

| query | operator class | circuit triples | Standard ⟺ SPARQL-star |
|---|---|--:|:--:|
| `and`      | monotone conjunction    | 13 | **byte- + struct-identical** ✓ |
| `union`    | monotone disjunction    | 30 | **byte- + struct-identical** ✓ |
| `optional` | non-monotone (OPTIONAL) | 60 | **byte- + struct-identical** ✓ |
| `minus`    | non-monotone (MINUS)    | 40 | **byte- + struct-identical** ✓ |

Gate IRIs are content-addressed by token IRIs, identical in both encodings, so the entire circuit
coincides byte for byte with no reliance on iteration order. The reification scheme changes **how base
facts are addressed in the store**, not the provenance structure — so a ~3× data-size reduction on
RDF-star engines is free, and everything downstream (compile, WMC, cross-engine byte-identity) is
unchanged.

**Caveat.** SPARQL-star needs an RDF-star store (GraphDB, Oxigraph, Jena ✓; QLever and MillenniumDB use
Standard — which is why cross-engine byte-identity is run under Standard).

## 8. Workload completeness: WatDiv Complex (C) *(G10)*

The construction workload covered WatDiv **L/S/F** plus our **P/M**; the NPCS/SPARQLprov taxonomy also
has **C (complex)**. [`watdiv/C-complex.rq`](watdiv/C-complex.rq) is WatDiv template **C1** (8 triple
patterns: a 3-way star on `?v0` + a review chain + an actor join) — the defining "correlated stars
joined by chains" shape.

| query | scale | answers | ⊗ | ⊕ | gates+edges |
|---|---|--:|--:|--:|--:|
| C-complex (C1) | WatDiv 10 M (32.7 M reified) | 8 | 16 | 8 | 168 |

**Findings.** The complex category builds correctly (valid circuit, no ⊖), so the machinery is not
limited to the L/S/F shapes; with C added the WatDiv workload spans the **full L/S/F/C taxonomy** the
baselines use plus our path (P) and MINUS (M) extensions. Complex joins are selective, so their
circuits are small — the opposite end of the spectrum from unbound P2 (149 998 answers, §6), built by
the same CONSTRUCT plan.

**Deferred.** C1 *unbound* at 100 M did not finish in the session's time box; a **bound** C1 is the right
way to get selective 100 M points. Mechanical follow-up, not a blocker.

## 9. Timing protocol *(G4)*

The protocol every headline timing is measured under; the numbers themselves are in
[`CANONICAL_TIMINGS.md`](CANONICAL_TIMINGS.md).

- **1 warm-up + 5 timed runs** per number; report **median [min–max]** and mean ± sd.
- Uniform **300 s** query budget / **120 s** per compile attempt (`experiment_timeouts.py`); a single
  psql session for ProvSQL, timed with `\timing` rather than process start-up.
- **Environment logged with every run**, and the host disclosed as shared.
- **≥5 instances per shape** on top of the 5-run variance (`g4_instances.py` → `g4_instances.csv`):
  TPC-H Q3 × 5 mktsegments and WatDiv S-star × 5 users.

**What the rigor pass established.** Within-instance variance is small (sd ≤ 2 % of the median on every
instance), so the headline numbers are stable rather than lucky runs, and PQE is construct-dominated
with compile+WMC both small and stable. It also **caught two over-claims**, which is the point: a cold
ProvSQL first call that made us look ~2× faster, and later a pruned `count(*)` ProvSQL target that made
ProvSQL look faster. Both are corrected in §1 and in CANONICAL_TIMINGS.

**Caveats.** Warm-cache steady state by design; a cold-start column would be a different and larger set
of numbers. Shared machine, so a fully quiescent table needs an isolated node — the *relative* results
held here despite background load. Construction scaling (E3, WatDiv 10 M/100 M) was already 5-run
averaged and is not re-measured under this pass.
