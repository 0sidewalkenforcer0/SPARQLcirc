# SPARQL_circ — experiment designs, predictions & results (E1–E7)

*Precursor to VLDB '27. Every experiment was **pre-registered** in `EVALUATION.md` (setup + predicted
result fixed before running); this document pairs each design with the numbers measured on the server,
so a surprising value reads as a finding, not a moving target.*

**Environment.** GraphDB 10.7.6 (WatDiv 10M = 32,749,371 reified triples; 100M = 326,993,142) · d4 v1
(Linux/x86, bundled PATOH) · PostgreSQL 18 + ProvSQL 1.11 · our zero-dependency ROBDD + WMC.
**Correctness gate:** every scale number is trusted only after `circuit WMC == possible-world
enumeration` on the small checks, and `d4 / ProvSQL WMC == our OBDD` per instance.

## Claims the evaluation defends

| | Claim | Experiments |
|---|---|---|
| **A** | An **unmodified** SPARQL engine builds the shared circuit (vs ProvSQL's modified PostgreSQL) | E3, E7 |
| **B** | **Compactness by sharing** — one shared circuit ≪ per-answer strings, and that is *why* PQE is feasible | E2 |
| **C** | **Exact PQE, including non-monotone** OPTIONAL/MINUS via ⊖ | E1, E6 |
| **D** | **Tractability tied to treewidth**; factored construction keeps the circuit polynomial | E4, E5 |

---

## E1 — Correctness / exactness  *(proves C)*

- **Setup.** Instances small enough for possible-world enumeration (PWE) as ground truth (≤ ~25 tokens);
  BGP / UNION / OPTIONAL / MINUS × random sparse data × random leaf probabilities. Metric: `max|WMC − PWE|`.
- **Predicted.** **0** up to float ε for every operator — the Boolean abstraction (⊗→∧, ⊕→∨, ⊖(a,b)→a∧¬b)
  *is* the possible-world indicator, and WMC is its expectation.
- **Actual.** `circuit == PWE` on every gallery shape; `tests.py` 171/171; property paths verified engine-side.

  | shape | answers | circuit == PWE | shape | answers | circuit == PWE |
  |---|---|---|---|---|---|
  | atom | 2 | ✅ | minus_disjoint | 2 | ✅ |
  | join | 1 | ✅ | minus_union | 2 | ✅ |
  | union | 2 | ✅ | minus_p2union | 2 | ✅ |
  | minus | 2 | ✅ | optional / opt_left / opt_right | — | ✅ |

- **Verdict.** ✅ error 0 for all operators. *Source:* `verify_gallery.py`, `verify_engine_native.py`, `tests.py`.

---

## E2 — Compactness: shared circuit vs per-answer strings  *(proves B — the core claim)*

- **Setup.** `T_string = Σ_answers Σ_derivations arity` (NPCS/SPARQLprov write `arity` token occurrences per
  derivation, repeating shared subterms) vs `T_circuit = gates + edges` (each distinct gate stored once).
  Independent variable: query depth / structure.
- **Predicted (cost model).** flat circuit ≈ `D·(arity+2)+L` ≈ strings on **shallow** tree-like queries
  (~0.5–1×); the win is **shared subexpressions**, captured by factored construction: a layered depth-`k`
  width-2 family has `D = 2^{k−1}` (strings exponential) but a circuit of `Θ(k·W²)`. Pre-registered anchor:
  **depth-12 ⇒ 201×**, unbounded in depth.
- **Actual.**

  | instance | derivations | T_string | T_circuit | **sharing** |
  |---|--:|--:|--:|--:|
  | drug (3-hop) | 3 | 9 | 25 | 0.4× |
  | layered-4×2 | 16 | 64 | 68 | 0.9× |
  | deep-4×2 | 16 | 64 | 68 | 0.9× |
  | layered-4×3 | 81 | 324 | 147 | 2.2× |
  | layered-4×4 | 256 | 1024 | 256 | 4.0× |
  | deep-8×2 | 256 | 2048 | 156 | 13.1× |
  | layered-4×6 | 1296 | 5184 | 564 | 9.2× |
  | layered-4×8 | 4096 | 16384 | 992 | 16.5× |
  | **deep-12×2** | **4096** | **49152** | **244** | **201.4×** |

- **Verdict.** ✅ 0.4–0.9× shallow → **201.4×** at depth-12 — the anchor hit exactly; circuit stays ~flat
  (156→244) while strings explode (2048→49152). *Source:* `bench.py` → `bench.csv`; fig `figures/E2_compactness`.

---

## E3 — Construction scaling on a deployed, unmodified engine  *(proves A)*

- **Setup.** The stock engine runs our CONSTRUCT and materialises the circuit. Source auto-bound (selective,
  as the baselines run the official WatDiv templates); the N-Triples response is **streamed** and counted in
  O(1) memory. Metrics: circuit-build wall-clock, size, #answers, vs the plain NPCS SELECT (`c = build/plain`).
- **Predicted.** Build dominated by the engine's join evaluation ∝ #derivations, plus a per-gate
  `SHA256`/comparator overhead `O(arity·log arity)` ⇒ `build ≈ c·T_plain`, near-linear; sub-second→seconds at
  10⁶–10⁷; **byte-identical circuits across engines**; `c` a small constant.
- **Actual — bound (selective) S/L/F, 5-run average.**

  | query | scale | build_ms | plain_ms | c | deriv | gates | edges | answers | sharing |
  |---|---|--:|--:|--:|--:|--:|--:|--:|--:|
  | S-star | 10M | 31 | 10 | 3.2 | 252 | 254 | 1008 | 2 | 0.60× |
  | F-snow | 10M | 12 | 7 | 1.8 | 6 | 7 | 30 | 1 | 0.65× |
  | L-path | 10M | 15 | 8 | 1.9 | 45 | 90 | 180 | 45 | 0.50× |
  | S-star | **100M** | 515 | 76 | 6.8 | 855 | 874 | 3420 | 19 | 0.60× |
  | F-snow | **100M** | 19 | 9 | 2.1 | 25 | 26 | 125 | 1 | 0.66× |
  | L-path | **100M** | 23 | 14 | 1.6 | 79 | 158 | 316 | 79 | 0.50× |

- **Actual — unbound (full-query) at 10M** (memory-safe streaming; the largest circuits built by a stock engine):

  | query | build_ms | deriv | gates | edges | answers | sharing |
  |---|--:|--:|--:|--:|--:|--:|
  | F-snow | 10,618 | 62,863 | 64,890 | 314,315 | 2,027 | 0.66× |
  | L-path | 123,901 | 938,669 | 1,834,500 | 3,754,676 | 895,831 | 0.50× |
  | S-star | 485,953 | 267,784 | 268,583 | 1,071,136 | 799 | 0.60× |

- **Verdict.** ✅ build ∝ #derivations across **5 orders of magnitude** (slope ≈ 1 on log-log); 100M selective
  sub-second (19–515 ms); `c` a small constant (1.6–6.8); cross-engine **byte-identity** confirmed
  (`verify_engine_agnostic`: GraphDB circuit == in-memory RDF4J). *Source:* `e3_run.py` →
  `e3_{10M,100M}{,_unbound}.csv`; fig `figures/E3_construction_scaling`.
- **Honest boundary.** The *unbound* query at 100M exceeds the engine's heap while building the circuit — the
  wall that motivates the selective / factored path at billion-scale (recorded, not hidden).

---

## E4 — Knowledge compilation vs treewidth  *(proves D)*

- **Setup.** Factored circuits over treewidth-controlled families (`gen_families.py`); compile with **d4**
  (d-DNNF) and our **OBDD**; each instance in a memory-capped subprocess (a blow-up is recorded as
  `obdd-timeout`, not a crash). Metric: compiled size vs `n` and `tw`. Every compiled instance: d4-WMC == our
  OBDD WMC == exact.
- **Predicted.** For lineage treewidth `tw`: d-DNNF `O(n·2^{O(tw)})` — **linear in n**; OBDD attains only the
  pathwidth bound and `pw ≤ O(tw·log n)` ⇒ OBDD `n^{O(tw)}` — polynomial with `tw` in the exponent. So
  **bounded tw:** d-DNNF ≈ linear, OBDD polynomial (widening gap); **growing tw:** both `2^{Θ(tw)}`.
- **Actual — bounded treewidth (tw = 2, n grows).**

  | n (tokens) | OBDD | d-DNNF | match |
  |--:|--:|--:|:--:|
  | 6 | 4 | 6 | ✅ |
  | 14 | 44 | 40 | ✅ |
  | 30 | 473 | 162 | ✅ |
  | 62 | 24,897 | 666 | ✅ |
  | 94 | **299,481** | 1,193 | ✅ |
  | 126 | *did not compile* | 2,067 | (d-DNNF only) |
  | 190 | *did not compile* | 3,230 | (d-DNNF only) |
  | 254 | *did not compile* | 5,270 | (d-DNNF only) |

- **Actual — growing treewidth.**

  | family | tw | n | OBDD | d-DNNF |
  |---|--:|--:|--:|--:|
  | layered (depth 4) | 2 | 14 | 44 | 40 |
  | | 4 | 52 | 960 | 1,169 |
  | | 6 | 114 | 26,502 | 11,908 |
  | | 8 | 200 | **375,501** | **211,964** |
  | grid | 3 | 12 | 28 | 30 |
  | | 4 | 24 | 57 | 124 |
  | | 5 | 40 | 96 | 1,069 |
  | | 6 | 60 | 145 | 8,104 |

  *(chain, tw = 1: OBDD = n for n = 4…512, d-DNNF = **2** constant — the read-once sanity case.)*

- **Verdict.** ✅ Bounded tw: OBDD explodes to 299k and stops compiling by n ≥ 126, while d-DNNF stays ≤ 5,270
  and keeps going — the d-DNNF ≫ OBDD advantage made concrete. Growing tw: both blow up `2^{Θ(tw)}`.
  *Source:* `e4_sweep.py` → `e4_results.csv`; fig `figures/E4_compile_vs_treewidth`.
- **Honest caveats.**
  - **Our OBDD blows up *faster* than the theoretical `n^{O(tw)}`** — the ROBDD uses a naive DFS variable
    order, not an optimised one, so the observed curve is an *upper* witness, not the `n^{O(tw)}` bound itself.
    This makes the d-DNNF ≻ OBDD conclusion **stronger**, but we do **not** claim the OBDD realises `n^{O(tw)}`.
  - **Grid family:** our OBDD stays *smaller* than d4's d-DNNF (145 vs 8,104 at tw = 6) — the "small-scale
    vtree" effect the pre-registration flagged; the clean separation shows on the layered/bounded-tw families.
  - **Long AND-chains** (chain-32…512, tw = 1) underflow the WMC to `0.0` — a float artifact of multiplying
    many probabilities, not a correctness issue (the compiled sizes, the point of E4, are unaffected).

---

## E5 — Factored vs flat construction  *(proves D)*

- **Setup.** Variable elimination (`factor.py`) vs the flat one-⊗-per-derivation build; assert
  `WMC(flat) == WMC(factored) == PWE`; measure ⊗-gate counts. Metric: flat/factored ratio.
- **Predicted.** A star projecting out existentials: flat `∏deg`, factored `∑deg` (pilot: WatDiv star 2.9×).
  A deep chain depth `k` width `W`: flat `W^k`, factored `(k−1)W²` ⇒ ratio ≈ **`W^{k−2}`**, unbounded. A path
  (all vars projected): **1×**.
- **Actual — layered chain (k = 4), flat = `W^4` vs factored = `3W²`.**

  | width W | #edges | flat ⊗ | factored ⊗ | **flat/factored** |
  |--:|--:|--:|--:|--:|
  | 2 | 14 | 16 | 12 | 1.3× |
  | 3 | 30 | 81 | 27 | 3.0× |
  | 4 | 52 | 256 | 48 | 5.3× |
  | 6 | 114 | 1,296 | 108 | 12.0× |
  | 8 | 200 | 4,096 | 192 | 21.3× |
  | 10 | 310 | 10,000 | 300 | 33.3× |
  | 14 | 602 | 38,416 | 588 | 65.3× |

  Boolean equivalence checked on 2,000 random worlds (W = 3…8): `flat == factored`. Drug 3-hop:
  `flat == factored == PWE` (Clopidogrel 0.358800, Omeprazole 0.774298).
- **Verdict.** ✅ ratio follows `W^{k−2}` (= `W²/3` at k = 4: W = 14 → 65.3×), unbounded; WMC provably unchanged.
  *Source:* `factor_demo.py` (WatDiv star 2.9× remains an in-memory pilot).

---

## E6 — Non-monotone operators  *(proves C — separates us from monotone-only provenance)*

- **Setup.** MINUS/OPTIONAL-heavy shapes (incl. composite operands reduced by `normalize()`); WMC vs PWE, the
  composite cases cross-checked against **rdflib's own W3C** MINUS/OPTIONAL evaluation.
- **Predicted.** Exact: `⊖(a,b) ↦ a∧¬b` gives "μ present iff its P1-derivation holds and no removing
  derivation holds" — precisely W3C MINUS/OPTIONAL under possible worlds; ⊖/sub gates add cost linear in
  operand size; tractability class unchanged. Qualitatively **SPARQLprov (monotone semiring) and NPCS
  (strings, no PQE) cannot produce these probabilities**.
- **Actual.** `minus`, `minus_disjoint`, `minus_union`, `minus_p2union`, `optional`, `opt_left`, `opt_right`
  all `circuit == PWE`; e.g. MINUS `Carol` = 0.400000, OPTIONAL/UNION branches exact. Rejection guards
  (FILTER, LIMIT, right-nested MINUS, cross-product OPT-in-MINUS) fire loudly rather than mis-answer.
- **Verdict.** ✅ exact non-monotone PQE — the "we can, they can't" result. *Source:* `verify_gallery.py`,
  `verify_nonmono.py`.

---

## E7 — Head-to-head vs ProvSQL  *(proves A + B + C together)*

- **Setup.** Same data + same per-triple probabilities + same query through **ProvSQL**
  (`probability(provenance())`, in-DB) and **SPARQL_circ** (client compile + WMC). Metric: agreement of the
  exact probability, plus the qualitative "requires engine modification?" axis.
- **Predicted.** **Comparable PQE** when both knowledge-compile (do **not** claim we count faster — the two
  timings are not comparable: ProvSQL builds+compiles in-DB, our client compiles the returned circuit). Our
  win is **axis A**: unmodified, engine-agnostic, native RDF/SPARQL, no relational remodelling, no PG fork.
- **Actual.**

  | instance | #triples | answers | circuit gates | **max \|Δp\|** | match |
  |---|--:|--:|--:|--:|:--:|
  | drug-3hop | 8 | 2 | 13 | 1.1×10⁻¹⁶ | ✅ |
  | star-u6-d3 | 24 | 6 | 50 | 1.1×10⁻¹⁶ | ✅ |
  | layer-n4-f2 | 14 | 2 | 34 | 1.7×10⁻¹⁶ | ✅ |

  | criterion | ProvSQL | SPARQL_circ |
  |---|:--:|:--:|
  | Exact answer probability (PQE) | ✅ | ✅ |
  | Probability == the other system | ✅ | ✅ |
  | Knowledge compilation | ✅ (in-DB) | ✅ (client) |
  | Non-monotone OPTIONAL / MINUS | ✅ | ✅ |
  | Native RDF / SPARQL (no relational remodelling) | ❌ | ✅ |
  | **Runs on an UNMODIFIED engine** | ❌ (PG extension) | ✅ (stock SPARQL 1.1) |

- **Verdict.** ✅ 3/3 exact parity (Δ at float precision, ~2×10⁻¹⁶); identical exactness/tractability class,
  **without touching the engine**. *Source:* `provsql/e7_run.py` → `e7_results.csv`; fig `figures/E7_vs_provsql`.

---

## Honest caveats / threats to validity

1. **E4 grid nuance** — OBDD < d-DNNF on the grid family at these sizes (small-scale vtree effect, predicted).
2. **E3 unbound @100M** — exceeds the engine heap; the selective/factored path is what scales.
3. **E5 WatDiv star (2.9×)** is an in-memory pilot; the synthetic families give the clean unbounded curve.
4. **E1/E6 are small-scale by design** — PWE ground truth needs `2ⁿ` enumeration, so instances must be tiny.
5. **Reification blow-up** — Standard reification triples the data (100M → 327M); SPARQL-star is the compact
   alternative on RDF-star engines.
6. **Serialization vs structure** — compactness is reported *structurally* (gates+edges vs `Σ deriv × arity`),
   not as N-Triples bytes (which are inflated by 64-hex SHA256 gate IRIs).

## Reproduction

```bash
# env: source .../conda.sh && conda activate sparqlcirc; export LD_LIBRARY_PATH=$CONDA_PREFIX/lib
cd reference
python3 verify_gallery.py verify_nonmono.py        # E1, E6   (correctness gate)
python3 bench.py                                    # E2  -> bench.csv
D4=.../tools/d4/d4 python3 e4_sweep.py              # E4  -> watdiv/e4_results.csv
python3 factor_demo.py                              # E5
WATDIV_REPO=watdiv     python3 e3_run.py            # E3 @10M  -> watdiv/e3_10M.csv
WATDIV_REPO=watdiv100m python3 e3_run.py            # E3 @100M -> watdiv/e3_100M.csv
cd ../provsql && python3 e7_run.py                  # E7  -> watdiv/e7_results.csv
cd ../reference && python3 plot_results.py          # figures/*.{png,pdf}
```
The whole suite (incl. the 100M load) is orchestrated, memory-capped and logged by
`workspace/run_overnight.sh`.
