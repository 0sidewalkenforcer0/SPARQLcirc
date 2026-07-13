# SPARQLcirc — server experiment task (read me first)

> **⇒ START HERE (2026-07-13). The current executable task is `# ROUND 9 — PAPER FIGURE COVERAGE` at the
> END of this file.** ROUND 1–8 and the old `RE-RUN CHECKLIST` are a **historical log** (kept for
> provenance) — do NOT execute them top-to-bottom. The expensive ROUND-8 re-runs were completed before
> ROUND 9 was written. When an older section disagrees with ROUND 9, **ROUND 9 wins.** Superseded facts:
> • Do **not** ask the old WatDiv generator for a native 200 M graph (it segfaults). ROUND 9 optionally
>   recreates NPCS's different **200M-multisource stress case** by duplicating each 100 M logical fact with
>   a second provenance identifier; it is not a third ordinary scale point.
> • **G2b is NOT a byte-size win** — ROUND 7's "10–27× more compact" is retracted; the RDF N-Triples circuit
>   is usually byte-*larger*, compactness is *structural* only.
> • The canonical 5-run rows, forced-evaluation ProvSQL Q3, keyed R8.3 parity, G2b/G8 byte metrics, and G6
>   one-pass d-DNNF checks are already committed. ROUND 9 fills the **engine × query-pattern coverage grid**;
>   it does not invalidate those results.

**You are a coding agent on a server** that has the resources the dev laptop lacked: disk
for large WatDiv, CPU, a Linux/x86 box (for d4), and ideally **GraphDB** and **PostgreSQL+ProvSQL**.
Your job: run the SPARQLcirc **evaluation** — especially the parts that were blocked on a laptop —
and write the numbers back to the repo.

**What SPARQLcirc is (one paragraph).** It rewrites a SPARQL query so an **unmodified** SPARQL 1.1
engine, running the rewritten `CONSTRUCT`, materializes a single **shared, content-addressed
provenance circuit** (an RDF DAG of ⊕/⊗/⊖ gates). The client then compiles it (OBDD / d-DNNF) and
**weighted-model-counts** it for **exact probabilistic query evaluation (PQE)**, including
non-monotone OPTIONAL/MINUS and (new) property paths. Axis of difference vs baselines: a *shared
circuit + exact probabilities on a stock engine*. NPCS/SPARQLprov emit **per-answer provenance
strings** (no probabilities); ProvSQL needs a **modified PostgreSQL**.

**Canonical timeout policy (all new citable runs):** one timed **SELECT/CONSTRUCT cell = 300 s**; one
**OBDD or d4/d-DNNF compilation attempt = 120 s**. Both values come from
`reference/experiment_timeouts.py`. A short health/correctness probe may fail earlier, and an untimed data
load may have a larger operational watchdog, but neither exception is a reported performance cell.

**Read these repo docs before starting:** `TECHREPORT.md` (§10 evaluation plan, §4 construction,
§4.6 paths, §5 DIFF/MINUS), `EVALUATION.md` (E1–E7, each with a *pre-registered predicted result* —
compare your numbers to it), `REPRODUCE.md` (exact commands + data acquisition), `provsql/README.md`
(E7 harness), `reference/D4_ON_LINUX.md` (E4).

---

## 1. Build + smoke test (do this first, ~2 min)
```bash
cd engine && mvn -q package                 # -> target/npcs-rewrite.jar
cd ../reference
python3 verify_all.py                       # expect: ALL OK
python3 tests.py                            # 171/171
python3 verify_gallery.py                   # ALL OK
python3 verify_engine_paths.py              # ALL OK  (property paths)
python3 verify_engine_agnostic.py           # determinism + SPARQL-1.1-only OK
python3 verify_ddnnf_wmc.py                 # one-compile d-DNNF evaluator OK
python3 verify_level1_harness.py            # forced per-answer d4v2 harness OK
```
If any of these is not green, STOP and report — do not trust scale numbers until the toolchain checks out.

## 2. Datasets — MATCH the baselines (so the comparison is fair)
Get the data (NOT in the repo; see `REPRODUCE.md` "Getting the data"). Keep it **OUTSIDE** the repo
directory — `.gitignore` only covers `*.reified.nt`, so a raw multi-GB `.nt` could be `git add`-ed by
accident.

- **WatDiv** (http://dsg.uwaterloo.ca/watdiv/) at **10M / 100M / 200M** triples.
  *Why these:* NPCS used 10/100/200M; SPARQLprov used 100M — so 10/100/200M covers both.
  Reify with `reference/watdiv/reify.py <base.nt> <base.reified.nt>`.
- **Wikidata** (real KG; both baselines used it — NPCS via WDBench queries). Optional / stretch.
- **Query shapes** — ready-made in `reference/watdiv/` (all use real WatDiv `wsdbm:` predicates):
  - monotone: `L-path.rq` (linear), `S-star.rq` (star), `F-snow.rq` (snowflake)
  - **non-monotone (MINUS):** `M-minus.rq` (likes ∖ purchased; compound 2-pattern subtrahend),
    `M-minus2.rq` (purchasers ∖ likers; single shared var)
  - **property paths** (over `wsdbm:friendOf`, a User→User edge): `P-plus.rq` (bound source,
    single-source `friendOf+`), `P-plus-all.rq` (free/all-pairs `friendOf+`), `P-star.rq`
    (`friendOf*`), `P-alt.rq` (compound closure `(friendOf|^friendOf)+` = undirected reachability).
  - **How to run each:** monotone + MINUS go through the one-shot flow (`watdiv_run.py`, which POSTs a
    single CONSTRUCT). **PROPERTY PATHS require `CircuitRun`'s client-driven iterative protocol** (a
    single CONSTRUCT can't express recursion), so run them as:
    ```bash
    java -cp engine/target/npcs-rewrite.jar npcs.circuit.CircuitRun \
         Standard /data/watdiv/base.reified.nt reference/watdiv/P-plus.rq >circ.nt   # in-memory RDF4J
    #   ...or add a 4th arg = writable SPARQL endpoint (GraphDB) to build it there.
    ```

## 3. Experiments (priority order — predictions in `EVALUATION.md`)
Run from `reference/`. Each script writes a CSV; record what matches/deviates from the prediction.

1. **E3 — construction scaling (WatDiv 10/100/200M).** The unmodified engine builds the circuit;
   measure **build time** and **circuit size** (gates+edges) vs NPCS per-answer strings
   (`Σ derivations × arity`).
   - `WATDIV_NT=/data/watdiv/base.nt python3 watdiv_factor.py`  (flat vs factored, no engine)
   - end-to-end on GraphDB: `python3 watdiv/reify.py …` then start GraphDB, load, `python3 watdiv_run.py`
   - *Predicted:* near-linear build ≈ small const × plain-query time; shared circuit ≪ strings on
     deep/recurring shapes (flat ≈ strings on shallow).
2. **E7 — ProvSQL head-to-head (needs PostgreSQL + ProvSQL).** `provsql/` harness (`schema.sql`,
   `rdf_to_sql.py`). Compare *time & space to the exact answer probability*; our axis = **unmodified
   engine**, theirs = modified PostgreSQL.
3. **E4 — d4 d-DNNF scaling (this is why we need Linux/x86; PATOH is x86-only).**
   `python3 export_cnf.py` then follow `reference/D4_ON_LINUX.md`; `python3 d4_pipeline.py`.
   *Predicted:* d-DNNF linear-in-n on bounded treewidth; OBDD `n^{O(tw)}`; both blow up on growing tw.
4. **Engine-agnostic byte-identity (needs GraphDB — matches NPCS's engine).** Build the circuit on
   GraphDB via `CircuitRun`'s endpoint mode and diff vs in-memory RDF4J:
   `SPARQLCIRC_ENDPOINT=http://localhost:7200/repositories/<repo> python3 verify_engine_agnostic.py`
   (the endpoint must be writable — the path protocol INSERTs each round back).
5. **Property paths at scale (our contribution).** Run the `reference/watdiv/P-*.rq` files over
   `wsdbm:friendOf` via `CircuitRun` (see §2 for the exact command; paths need the iterative protocol,
   not `watdiv_run.py`). Report circuit size (gates+edges) and build time for `P-plus.rq` (single-source)
   vs `P-plus-all.rq` (all-pairs) — the ratio should grow ~`|V|` — and confirm the circuit stays
   **polynomial** where naive per-answer walk expansion is infinite/exponential. `python3 path_demo.py`
   shows the shape + the ring/clique scaling on synthetic data.

## 4. Environment setup
- **Java 11+, Maven** (engine). **Python 3.9+** (`reference/` core is stdlib-only; `rdflib` for the
  oracles; `pysdd` optional for the SDD baseline).
- **GraphDB 10.x** on `localhost:7200` (free edition, https://graphdb.ontotext.com/) — E3 end-to-end,
  engine-agnostic leg, and to match NPCS. Repo config: `reference/watdiv/repo.ttl`.
- **PostgreSQL + ProvSQL** (E7 only).
- **d4** on Linux/x86 (E4 only; see `reference/D4_ON_LINUX.md`).
- **WatDiv generator** for the data.

## 5. What to report back
Write results into `reference/watdiv/RESULTS.md` (+ the CSVs the scripts emit: `bench.csv`,
`bench_engine/results.csv`, `reference/watdiv/results.csv`). For each experiment record: the setup,
the numbers, and **whether they match `EVALUATION.md`'s prediction** — a mismatch is a *finding or a
bug*; say which. Then:
```bash
git add reference/watdiv/RESULTS.md reference/*.csv reference/watdiv/results.csv   # NOT the datasets
git commit -m "server: E3/E4/E7 + engine-agnostic results"
git push                                     # normal push; the dev side pulls
```

## 6. Guardrails
- **Never commit datasets or generated circuits** (multi-GB). Keep them outside the repo dir; rely on
  `.gitignore` (`*.reified.nt`) only as a backstop, not a license to `git add -A`.
- **Comparable protocol:** 300 s timeout per query (matches SPARQLprov), average over ≥5 runs after a
  warmup (matches both baselines).
- **Correctness before scale:** every circuit's WMC must equal possible-world enumeration on the small
  checks (`verify_*`) before trusting any scale number.
- **Report honestly:** if GraphDB / ProvSQL / d4 isn't available, say so and skip that experiment —
  do not fabricate numbers. Note any silent cap (top-N queries, sampling, no-retry).

## 7. Baseline facts (why these datasets/engines) — for comparability
| | SPARQLprov (PVLDB'21) | NPCS (WWW'24) |
|---|---|---|
| Synthetic | WatDiv 100M (L/S/F/C + 5 OPTIONAL templates) | WatDiv 10M/100M/200M (named-graph + RDF-star reif.) |
| Relational→RDF | TPC-H 1.2M–123M | — |
| Real KG | Wikidata 942M (star/union/minus) | Wikidata via WDBench |
| Engines | Virtuoso 7.2.5.1, Fuseki 3.17 | GraphDB, Stardog |
Both emit **per-answer provenance strings, no probabilities**. SPARQLprov's released rewriter realizes
MINUS as *unguarded* DIFF (`A OPTIONAL B`) — correct only when operands share a variable. Our target:
the **same datasets**, but producing a **shared circuit + exact probabilities on a stock engine**.

---

# ROUND 2 — follow-up (after dev review of the round-1 results)

Round 1 ran E1–E7 on the monotone **S/L/F** shapes and validated the core claims (E2 compactness:
deep-12x2 = 201×; E4 d-DNNF ≪ OBDD; E7 exact-probability match vs ProvSQL). Two gaps to close:

## A. MINUS at scale — RUNNABLE NOW, please do
The non-monotone contribution was not run. Run `reference/watdiv/M-minus.rq` and `M-minus2.rq` at
**10M and 100M** via the one-shot flow (they are BGP+MINUS, no recursion — the standard route works).
Caveat: `watdiv_run.py` globs `*.rq` when `WATDIV_QDIR=reference/watdiv` is set, which also picks up
the `P-*.rq` PATH files — those will **fail** one-shot (paths need `CircuitRun`), so either run the two
`M-*.rq` explicitly or temporarily move `P-*.rq` out of the glob dir. Extend `RESULTS.md` with a
**MINUS** table (build_ms, plain_ms, c_overhead, deriv, gates, edges, answers, share) and confirm
WMC == possible-world enumeration on a small check.

## B. Property paths — DEFERRED (blocked on a dev fix; do NOT attempt full-scale)
`CircuitRun` currently runs `N−1` rounds where `N` = the **global** distinct-node count
(TECHREPORT §13.11). On WatDiv that is ~10^6 rounds → the path loop would hang. So do **not** run the
`P-*.rq` files on the full reified WatDiv. The dev side will add an **early-stop / reachable-subgraph
round bound**; a Round-3 note will follow to run the paths on the `friendOf` subgraph then. (Optional
toy proof-of-concept only: extract a few-hundred-triple friend graph, reify it, run
`CircuitRun … P-plus.rq` on that — but label it a toy, not a scale number.)

## C. Honest caveats now in the write-up (no action needed)
TECHREPORT §13: (6) our OBDD blows up *faster* than `n^{O(tw)}` due to a naive DFS variable order —
the d-DNNF≻OBDD conclusion still holds; (10) unbound/all-pairs shapes at 100M exceed GraphDB's HTTP
CONSTRUCT limits; (11) the path round-bound above.

---

# ROUND 3 — property paths are now runnable

The Round-2 §B blocker is fixed: `CircuitRun` now bounds the reach loop by `|V_s|-1` rounds, where
`V_s` is the source's *reachable* subgraph (discovered live), not the global node count — so a
bounded-source path is feasible and exact. Also: the base relation is built from the path's predicate
only (e.g. friendOf edges), not the whole KG, so you can run directly on the reified WatDiv (no
subgraph extraction needed). **`git pull` first** to get the fix.

Run the paths via `CircuitRun` (iterative), e.g.:
```bash
java -cp engine/target/npcs-rewrite.jar npcs.circuit.CircuitRun \
     Standard /data/watdiv/base.reified.nt reference/watdiv/P-plus.rq  2>plan.txt >circ.nt
grep "property-path plan" plan.txt      # reports reachable-nodes + rounds actually run
```
For each of `P-plus.rq` (single-source), `P-plus-all.rq` (all-pairs — 10M only), `P-star.rq`,
`P-alt.rq`, record in a **"Property paths"** section of `RESULTS.md`: reachable-nodes, rounds,
gates+edges, build_ms, answers, and a WMC == possible-world spot-check on a small friend subgraph.
Report `P-plus` (single-source) vs `P-plus-all` (all-pairs) gate counts — the ratio should grow ~`|V|`.
Caveat: if `User0`'s friendOf reach is a large connected component, `|V_s|` (hence rounds) is large —
that's inherent to exact reachability provenance; pick a user with bounded reach, or use 10M, and note it.

---

# ROUND 4 — large-scale datasets: Wikidata + TPC-H (VLDB-scale comparability)

VLDB expects large-data results, and the baselines used these exact datasets. Add both. `git pull` first.

## A. Wikidata (PRIMARY — full fit; matches SPARQLprov 942M + NPCS/WDBench)
Data: the Wikidata **truthy** dump (`wdt:` direct predicates) or the WDBench graph (matches NPCS). Reify
(`reference/watdiv/reify.py` works on any `.nt`), load into GraphDB. **Keep the dump OUTSIDE the repo.**
Queries: `reference/wikidata/*.rq` (real `wdt:` predicates; see `reference/wikidata/README.md`).
- One-shot flow (set `WATDIV_QDIR=reference/wikidata`, but **exclude** `WD-path*.rq` — they need CircuitRun):
  `WD-star`, `WD-union`, `WD-minus`, `WD-opt`.
- Paths via `CircuitRun` (iterative, reachable-bounded, single-source small reach): `WD-path` (`P279+`
  superclasses of software), `WD-path2` (`P131+` containers of NYC). Report reachable-nodes + rounds.
Report a **"Wikidata"** section in `RESULTS.md` (build_ms, gates, edges, answers, share; paths:
reachable-nodes, rounds, gates, build). This is the headline large-scale + real-KG property-path result.

## B. TPC-H (SECONDARY — comparability, aggregation-limited)
See `reference/tpch/README.md`. TPC-H is aggregation-heavy and we do NOT do aggregation, so run only the
**non-aggregate SPJ/MINUS skeleton** (= SPARQLprov "base non-aggregate"). Needs a `dbgen`→RDF
direct-mapping converter we don't ship yet — write a small one, or, if time-constrained, report that TPC-H
was descoped in favour of the Wikidata full-fit result. Scale factors `10^{i/4-2}` (1.2M–123M) to match SPARQLprov.

**Priority: Wikidata first (high value); TPC-H if time permits.** Both are E8/E9 in `EVALUATION.md`.

---

# ROUND 5 — multi-engine study (strengthen Claim A: unmodified, engine-agnostic)

Everything lives in `reference/engines/` (registry `engines.json`, driver `run_engine.py`, per-engine
setup docs, hub `README.md`). `CircuitRun` is now engine-configurable via env vars
(`CIRCUIT_UPDATE_ENDPOINT`, `CIRCUIT_SKIP_LOAD`, `CIRCUIT_READONLY`) — GraphDB behaviour unchanged when unset.

**Four engines added** (+ Virtuoso/Stardog registry entries for the baselines):
- **Fuseki** (writable, SPARQLprov's engine; already in `pilot/tools/apache-jena-fuseki-5.2.0`)
- **Oxigraph** (writable, independent Rust implementation)
- **QLever** (read-only, Wikidata-scale non-path)
- **MillenniumDB** (read-only, property-path SOTA — non-path for now)

**Constraint:** non-path (BGP/UNION/MINUS/OPTIONAL) runs on ANY engine; property paths need a WRITABLE
engine (the iterative loop INSERTs each round). Read-only engines auto-skip path queries (exit 3). The
planned VALUES-inline path loop would unlock QLever/MillenniumDB for bounded paths — NOT yet implemented.

**Two results to produce:**
1. **Byte-identity (E10, Claim A).** Load the *same* WatDiv slice, run the same query set on **GraphDB +
   Fuseki + Oxigraph**; `circuit_sha256` must be identical across all three (independent codebases, one
   non-Java). `python3 engines/run_engine.py --engine {graphdb,fuseki,oxigraph} --data <slice> --queries ...`
2. **Scale non-path.** Run non-path Wikidata queries on **QLever** at full-Wikidata scale (E8 large-scale
   construction datapoint). MillenniumDB = same, plus cite it for path-answer performance.

Rebuild the engine first (`cd engine && mvn -q package`) so the env-var support is present, then `git pull`.

---

# ROUND 6 — VLDB gap roadmap (prioritized experiment backlog)

Full status + rationale in the experiment report + `BASELINE_COVERAGE.md` + `EVALUATION.md`. This is the
actionable backlog. The MUST-HAVEs close the two soft spots a reviewer will probe: the unique property-path
contribution does not yet scale, and the head-to-head comparisons are toy-scale/partial.

## Must-have (blocking a competitive submission)
- **G1 — property paths at scale.** Implement the frontier-restricted / VALUES-inline iterative loop
  (compose only from the newly-reached frontier; bounded accumulation), then re-run `P279+` / `P131+` on
  Wikidata (currently OOMs, E8). Also unlocks read-only QLever/MillenniumDB. **HIGHEST VALUE.**
- **G2a — ProvSQL on TPC-H, SF 0.01→1.** Same SPJ/MINUS skeletons as E9; PQE parity + construction time
  side-by-side up to 125M — upgrades E7 from 3 toy instances (8–24 triples) to the shared benchmark.
- **G2b — full NPCS on curated WDBench.** NPCS's own graph + full query set; NPCS (reimpl or jar, locally)
  vs us: construction time + output size (shared circuit vs per-answer strings) at equal scale (E8 is
  partial: single category, broad predicate filter).
- **G3 — end-to-end PQE latency.** One harness: construct → compile (d4) → WMC, total wall-clock on
  WatDiv / TPC-H / Wikidata, tabulated vs baselines (we currently time construction and compile+WMC apart).
- **G4 — statistical rigor.** ≥3–5 query shapes per pattern, repeated runs (mean/variance); more
  bound-survivable MINUS shapes (R2A currently 1) and more path shapes (R3).

## Should-have
- **G5** measured SPARQLprov output numbers — run its released rewriter from the artifact, locally (not
  shipped), to back the T_string cost model with the real system, not just our NPCS reimpl.
- **G6** d4 / d-DNNF on the *real* WatDiv / Wikidata / TPC-H circuits at scale (E4 is synthetic tw families);
  redo E11's compile-time win with d4 so it is order-robust, not OBDD-order-dependent.
- **G7** SPARQL-star reification at scale on RDF-star engines (E8 used Standard = 3x blowup).
- **G8** space & memory at scale (on-disk circuit bytes, compiled d-DNNF size, peak RSS).
- **G10 — comparability completeness.** Add WatDiv **Complex (C)** queries to E3/E6 and a **200M** scale
  point, so the workload matches NPCS/SPARQLprov exactly (taxonomy L/S/F/C/O; scale 10/100/200M). Cheap.

## Scope decision (state explicitly)
- **G9 — aggregation.** SPARQLprov (§4.4) and ProvSQL both do it; we do not. Either add a basic aggregation
  gate, or **declare out of scope with justification** (orthogonal to the shared-circuit/PQE contribution)
  in the limitations section — a reviewer will ask.

## Dimensions we deliberately do NOT run (see BASELINE_COVERAGE.md)
Decoding (we emit RDF, not per-answer strings) · aggregate provenance (G9) · TripleProv/GProM baselines
(same how-provenance/no-PQE class as NPCS/SPARQLprov; cite, don't run) · the R reified-no-provenance query
version (our overhead is plain-vs-circuit).

---

# ROUND 7 — reviewer-calibrated status & DE-DUP (READ FIRST)

An external systems-paper review + our own audit reached the SAME verdict: **breadth is strong, but the
must-have evidence loops are not closed** (property-path scale, strong-baseline fairness, end-to-end
latency, statistical rigor). We agree with ~100% of the diagnosis; we have EXECUTED ~none of the
must-haves — they are backlog, not results. This section de-duplicates so you **do NOT re-run** what is
already done or already in flight from ROUND 6.

## Legend
✅ DONE — results committed → **SKIP**.  🔄 ROUND 6 — already sent; if started, **CONTINUE, don't restart**.
🆕 NEW — added this round → **RUN**.

## ✅ DONE — SKIP (results already in the repo; do NOT re-run)
| Exp | Artifact / note |
|---|---|
| E1 correctness | `verify_gallery` + `tests.py` 171/171; **13-shape byte-identity closure** committed (Oxigraph 13/13 local) |
| E2 compactness | `bench.csv` (up to 201×) |
| E3 construction scaling | WatDiv 10M/100M `e3_*.csv` (already 5-run avg) |
| E4 treewidth d4-vs-OBDD | `e4_results.csv` (synthetic families) |
| E5 factored vs flat | `factor_demo` |
| E6 non-monotone gallery | `verify_nonmono` |
| E7 vs ProvSQL | `e7_results.csv` — **3 toy instances only; the SCALE version is G2a (RUN), not a re-run of this** |
| Round 2A MINUS 10M/100M | `e6_minus_*.csv` |
| Round 3 paths | `e_paths.csv`, `path_demo` — **80-node + synthetic only; the SCALE version is G1 (RUN)** |
| E8 Wikidata 2.13B | `e8_wikidata.csv` — **partial (single 31/41, broad filter); the clean version is G2b (RUN)** |
| E9 TPC-H SF 0.01–1 | `e9_*.csv` |
| E10 4-engine byte-identity (8 shapes) | `engines/RESULTS.md`, `engines/timing/*.csv` |
| E11 + E11-real | `e11_*.csv` (reconvergence boundary) |
| **G2b — WatDiv leg** (just landed) | `g2b_npcs_vs_ours.csv` — committed **clean-room NPCS reimplementation** vs ours on WatDiv 32.7M. The historical “10–27× more compact” wording was invalid because it mixed bytes and graph elements; `reference/G2b_RESULTS.md` contains the corrected same-unit result. **Do NOT re-run this legacy comparison** — R9.2 replaces its timing design. |

Where a DONE row says "the SCALE version is Gx", that Gx is a **new experiment below**, not a re-run of the
toy/partial one already done.

## 🔄 Must-haves — ALREADY in ROUND 6 (continue; do NOT restart if running)
**G1** paths-at-scale · **G2a** ProvSQL/TPC-H · **G2b** *(WatDiv leg DONE ↑; only the WDBench-curated graph
remains — it was download-blocked, so unblock the graph or descope that leg)* · **G3** end-to-end latency ·
**G4** rigor · (should-have) **G5 G6 G7 G8 G10** · (scope) **G9**. Definitions unchanged — see ROUND 6 above.
If you have already started any of these, keep going; the notes below only *refine* them.
Note: **G5 (NPCS side) is now partly done** — `g2b_npcs_vs_ours.py` runs the committed *clean-room*
`NpcsRewriter`, not the authors' official artifact; only the **SPARQLprov** artifact still needs measuring.

## 🆕 NEW this round — RUN (not in ROUND 6)
1. **E10 byte-identity — 13-shape re-run on GraphDB / QLever / MillenniumDB.** `verify_http.py` now
   iterates the full E1 set (`engines/_gallery_shapes.py`); **Oxigraph is already re-verified 13/13**.
   Only the 5 shapes not yet diffed on those 3 engines are new: `atom`, `minus_disjoint`, `minus_p2union`,
   `opt_right`, `opt_disjoint`. Cheap; closes per-engine correctness (E10 ⊇ E1). *This is the only genuinely
   new "run" — everything else is a refinement of a ROUND 6 must-have.*

## Refinements to ROUND 6 (apply when you run those items)
2. **G4 — tighten the protocol on ALL headline perf numbers** (was under-specified). Per number: ≥3–5
   instances/shape, 1–2 warm-ups + **5 timed runs**, report **median + min/max (or mean ± sd)**, uniform
   **300 s** timeout, and **log the environment** (cold/warm cache, hardware, heap, concurrent jobs). The
   review explicitly flagged E8/E10's "shared machine / GraphDB preload co-running / different WatDiv
   sample" caveats — **re-run the headline perf numbers in a quiescent environment** so they are citable.
   (Functional/size results already committed do NOT need re-running — only the *timing* numbers do.)
3. **G1 is CODE-then-RUN, not a blind re-run.** Implement the **frontier-only** iterative loop first
   (compose only from the newly-reached frontier + bounded accumulation) — the dev side can do this — THEN
   run Wikidata `P279+` / `P131+` (single-source *and* all-pairs), reporting time / peak RSS / circuit size /
   rounds + a WMC==PWE spot check. If it still won't scale, demote property paths from headline to prototype.
4. **G2a folds into G3.** Run ProvSQL and us on the SAME TPC-H SPJ/MINUS skeletons (SF 0.01→1) through the
   *full* pipeline and report end-to-end — don't produce a separate ProvSQL-only table. Frame: comparable
   latency **without an engine fork**, not a speed win (ProvSQL is a shared-circuit peer, may be faster).

## Priority
1. **G1** (after frontier-only code) — unblocks the headline. 2. **G3+G2a** end-to-end incl. ProvSQL/TPC-H.
3. **G4** rigor pass (quiescent env) over headline timings. 4. **E10 13-shape** finish (cheap). 5. **G2b/G5**
(real NPCS/SPARQLprov artifacts), **G6** (d4 on real circuits), **G10** (C + 200M), G7, G8.
Non-server (author decisions): paper positioning (systems vs foundations) and **G9** aggregation scope.

---

# FIX NOTE (answer-key collision) — regenerate gate-IRI references

`CircuitRewriter` answer/group/reach gate IRIs now use a **collision-resistant, term-type-aware** identity
key (`termHash`: kind-tagged + per-part-hashed, distinguishes IRI/literal/datatype/lang/unbound), and
answer gates additionally emit **`c:binding`/`c:var`/`c:val`** recovery nodes (real RDF term preserved).
`c:answer` is kept as a readable label. Fixes distinct SPARQL solutions merging into one gate (was:
IRI-vs-literal / datatype / lang / delimiter / unbound-vs-"NULL"). Regression: `reference/verify_answer_keys.py`.

**Impact on in-flight runs:**
- **Probabilities / WMC / answer *counts* are UNCHANGED** on the benchmark queries (all project distinguishing
  IRIs — see BASELINE analysis); this only *fixes* the latent collision + adds recoverable bindings.
- **`urn:g:a:` / `urn:g:r:` / group-gate IRIs CHANGED** (new key) and **circuit triple counts shifted**
  (+`c:binding` nodes). So: **regenerate any stored E10 byte-identity reference hashes** and expected
  triple counts. Cross-engine byte-identity **still holds** (the new key is standard-SPARQL-1.1 +
  deterministic — verified on Oxigraph); the E10 13-shape re-run should just use the NEW hashes.
- `urn:g:t:` product-gate IRIs are unchanged.
Verified green after the fix: tests.py 171/171, verify_gallery, verify_oxigraph (byte-identical),
verify_engine_paths (WMC==PWE), verify_engine_agnostic (SPARQL-1.1-only), verify_answer_keys (6/6).

---

# ROUND 8 — collapse the conflicting timing tables into ONE authoritative result (READ FIRST)

A second external review of `c4ec949` confirmed the ROUND 7 diagnosis and found a **new, concrete
reporting hazard**: after the answer-key fix (`1e67021`) the repo now carries **two mutually
inconsistent "authoritative" perf tables**, and one still calls its (pre-fix) numbers "stable and
citable". This round is mostly a **RE-RUN + RETIRE-STALE** pass, not new experiments. Dev side has
started the matching local fixes (path-state isolation, G2b metric, docs) — see the FIX NOTE style commits.

## The conflict (must be resolved before any number is quoted in the paper)
Same query, two tables, different "authoritative" totals:

| query | `G4_RESULTS.md` (labeled *stable & citable*) | `G3_RESULTS.md` (post-`1e67021`) |
|---|---|---|
| tpch-Q3 (TPC-H 1.26 M, 14 908 ans) | **1.654 s** (construct 1471 ms) | **4.09 s** (construct 3860 ms) |
| wikidata-WDpath (P279+) | **2.127 s**, compile **1 ms** | **7.24 s**, compile **3.87 s** |

The WD-path jump is *expected and correct*: the fix un-merged reach states that a STR-collision had
previously collapsed, so the post-fix circuit is genuinely bigger → slower to compile. **G4's row is
pre-fix and must not be cited.** G3 (post-fix) is closer to authoritative but was not run under the
G4 5-run rigor protocol.

## 🆕 R8.1 — produce ONE canonical, post-fix, 5-run timing table (TOP PRIORITY)
Re-run **every headline timing number on the current jar** (state the commit) under the G4 protocol
(≥3–5 instances/shape, 1–2 warm-ups + **5 timed runs**, median + min/max, 300 s timeout, logged
quiescent environment), covering:
- **WD-path** `P279+` / `P131+`, single-source *and* all-pairs (this is also G1's number);
- **TPC-H Q3** naryrel, SF 0.01 → 1 (construct + compile + WMC, the *full* pipeline);
- **G6** d4-on-real-circuits, **G8** space/memory, **four-engine E10** timing.

Then **move `G3_RESULTS.md` + `G4_RESULTS.md` + `G2a`'s stale rows into a single `HISTORICAL_TIMINGS.md`
appendix** clearly marked "pre-`1e67021`, superseded — do not cite", and have EVALUATION/TECHREPORT point
only at the new canonical table. Every row: jar commit + environment line. **No query may appear with two
different totals across the repo.**

## R8.2 — G2b / G8 size metrics: never divide bytes by graph elements
`g2b_npcs_vs_ours.py`'s `size_win = NPCS_bytes / (gates+edges)` is dimensionless-nonsense and was
reported as "10–27× smaller". Dev has corrected the script to emit **three separate** comparisons; when
you re-run G2b/G8 use the corrected script and report them separately, never as one ratio:
- **structural**: NPCS token-occurrences vs our gates+edges;
- **serialized bytes**: NPCS string bytes vs our N-Triples bytes — and **state honestly that ours is
  currently LARGER** here (≈133 MB vs 19.9 MB on P2-unbound); the compactness story is *structural*, not
  serialized-byte;
- **compiled**: compiled nodes vs compiled nodes.

## R8.3 — ProvSQL comparison must exercise correlated / reconvergent lineage
The current G2a/E7 Q3 has a single 3-token derivation per answer → probability is trivially `0.5³ =
0.125`; it validates *execution compatibility* but not *shared-circuit WMC*. Add **one TPC-H query with
multiple derivations per answer that share base tokens** (reconvergent lineage — the case where a
naive per-answer product would double-count and the shared circuit must not), run it through the full
pipeline on **both** ProvSQL and us, and complete the **SF 0.1 end-to-end on our side** (the current
SF 0.1 "ours" cell is construction-only — either run compile+WMC or label it `construction only`).

## R8.4 — E10: make the "four-engine post-fix" claim literally true
`engines/RESULTS.md` headline says "all four engines, 52 byte-identity checks ✓ (post-fix)" but the
footnote admits **Oxigraph is a pre-fix carry-over** (its server was down at re-verify). Either re-verify
**Oxigraph's 13 shapes on the post-fix jar** (then the headline is true), or relabel to "3 engines live
post-fix + Oxigraph pre-fix". Do not leave the table and footnote contradicting each other.

## R8.5 — d4 on real path circuits: keep the honest stance in the canonical table
d4-v1 WMC still disagrees with OBDD/PWE on **8 of 16** WD-path answers (already recorded honestly).
In the R8.1 canonical table, keep **OBDD + PWE authoritative for path probability** and mark d4 as
**compiled-size-only** until the discrepancy is resolved (newer d4 build / encoding audit). Do not quote
d4 WMC as a path result.

## Priority
1. **R8.1** canonical 5-run table + retire stale (unblocks *every* cited number).
2. **R8.3** correlated ProvSQL query + SF 0.1 end-to-end.
3. **R8.2** corrected size metrics on G2b/G8 re-run. 4. **R8.4** Oxigraph post-fix. 5. **R8.5** stance.
Dev-side, in parallel (no server needed): property-path per-run state isolation, G2b script metric,
G7 real `CircuitRun` circuit-diff, doc-status sync, `circuit_io` N-Triples unescaping.

## ROUND 8 ADDENDUM (after the dev-side path-isolation + G2b-arity fixes landed — `7882a1e`, `46d8660`)
A second review round found two things that affect your re-runs:
1. **Re-run the canonical `wikidata-WDpath` row on the post-`7882a1e` jar.** R8.1's `CANONICAL_TIMINGS.md`
   was built at `cc59f0a`, *before* `7882a1e` changed the property-path engine (reach/base gate IRIs now
   carry a per-path fingerprint + a `c:rpath` triple; every path match query changed). The BGP rows
   (watdiv-Sstar, tpch-Q3) are unaffected — **only the WD-path row** needs re-measuring. Topology is
   unchanged (fingerprint only re-namespaces gates), so the ~8 s total should hold; construct + triple
   count shift slightly. The row is marked `†` in the canonical table until you re-run it.
2. **G2b: R8.2 already regenerated the RESULTS honestly (good) — one script↔results nit remains.** Your
   `18674af` rewrote `G2b_RESULTS.md` into the 3-metric, "ours is larger" version (correct). The dev side
   then fixed one thing R8.2 did *not*: the committed script still hardcoded `t_string = tms*3`; it now
   sums each product's **actual** token inputs (2-pattern P2 is no longer over-counted 50 %). Remaining
   reconciliation for the NEXT g2b re-run: the RESULTS table presents **NPCS-occ / ours-(g+e)** but the
   committed script only emits **ours-flat-tokens / ours-(g+e)** — `npcs_side` does not count NPCS token
   occurrences, so the script as committed does not reproduce the RESULTS' NPCS-occ column. Either add an
   NPCS-occurrence count to `npcs_side`, or switch the RESULTS structural column to ours-flat-tokens.
   Either way the arity is now correct; the "ours is larger, compactness is structural/reconvergent"
   conclusion is unaffected.
Note: the `:p+`/`:p*` fingerprint-collision the review flagged is **fixed** in the engine (`star` is now in
the fingerprint) and covered by a real same-endpoint sequential regression (`PathIsoSeq` + `verify_path_isolation.py`),
so path re-runs on a shared writable repo are safe against cross-query contamination.

## ROUND 8 ADDENDUM-2 (a 4th review; dev-side code/doc fixes landed at `90c3c3c` + this commit)
Two genuine code bugs were fixed (unbound-UNION answer loss; path bypassing LIMIT/OFFSET) with regressions
(`verify_union_hetero.py`, `verify_reject_modifiers.py`). The rest were **code/doc fixes whose numbers still
need a re-run** — please regenerate these on the endpoint:
1. **Canonical timings — ALL rows are now stale, not just WD-path.** `90c3c3c` moved RDF parse into the
   *construct* timer and variable ordering into the *compile* timer, so the stored watdiv-Sstar / tpch-Q3 /
   Qrecon numbers were measured under the OLD metric. Regenerate the **whole** table (1 warm-up + 5 runs)
   on the current harness; `CANONICAL_TIMINGS.md` now flags every row provisional.
2. **R8.3 parity is now RIGOROUS in code but unrun.** `r8_3_reconvergent.py` was rewritten to: use the
   **timed shared-compile** WMC map (not a separate per-answer recompile), key ours by `c_custkey`, fetch
   ProvSQL's per-customer `probability(provenance())` **and** an independent order-count K, then check
   (a) equal customer-key sets, (b) per-customer `max_abs_error < 1e-6`, (c) ours == `0.5·(1−0.5^K)` with
   **K from ProvSQL, not the circuit** (removes the earlier circular check). Re-run to populate
   `r8_3_reconvergent.csv` with the `cf_maxerr` / `max_abs_error` / parity fields; `R8_3_RESULTS.md` no
   longer claims keyed parity until then. (Verify the `_custkey` extraction matches your naryrel customer
   IRIs; adjust the regex if `?cust` is not a trailing-integer term.)
   Offline guard: `verify_experiment_harness.py` checks the shared-WMC return contract, rejects missing-K
   and customer-key mismatches, and tests the explicitly tagged ProvSQL row parser without an endpoint.
3. **G2b/G8 bytes** — NPCS now measures its complete final CSV body (`len(body_bytes)`); ours measures the
   final deduplicated N-Triples circuit model. These are final serialized-representation sizes in the same
   byte unit, not symmetric multi-request network traffic. Re-run to refresh `g2b_npcs_vs_ours.csv` / `g8_*`.
Scope boundaries now stated in public docs (not just code): property paths are **IRI-frontier only**
(README §Scope, TECHREPORT §4.6 item 11); `CIRCUIT_CLEANUP=1` is **scratch-endpoint only** (unsafe for a
shared content-addressed store) — behaviour unchanged, warning added.

---

# E4 / compiler alignment — CURRENT DECISION (d4-v2, Level 1)

**CORRECTION to an earlier note in this file: E4 is NOT missing.** `watdiv/e4_results.csv` is committed
and was run with **d4** on the Linux server; it shows the predicted scaling (bounded-tw d-DNNF ≤ 5 270
nodes at n = 254 while the OBDD hits 299 k and `obdd-timeout` by n ≥ 126; growing-tw both blow up, and on
the *grid* family d4's d-DNNF is even smaller than the OBDD), with `d4_wmc == expected` on every tractable
instance. So the public-compiler / knowledge-compilation payoff **was** delivered (E4). Disregard the prior
"E4 has no committed results" text.

**Decision:** the ProvSQL head-to-head uses **Level 1: per-answer Tseitin CNF → the exact same pinned d4v2
binary on both sides**. It is implemented in `reference/level1_d4_headtohead.py`; do not retrofit it into
G3. G3 remains our normal shared-ROBDD end-to-end result, and E11 separately measures the shared-compilation
advantage. Level 2 (matching ProvSQL's complete automatic portfolio, including tree decomposition) is deferred.

Important precision: both sides use semantically equivalent Tseitin CNFs, the same compiler binary and the
same per-answer granularity. Do **not** claim byte-identical clauses or "the only difference is the data model"
unless `tseytin_cnf()` is additionally normalised and compared. System-specific circuit/CNF generation remains
part of the measured systems.

Our forced path invokes d4 **once** to dump a d-DNNF, then `ddnnf_wmc.py` evaluates that dump in one linear
pass. It does not invoke d4 a second time for WMC. ProvSQL must use a CNF-only registry entry named
`d4v2-cnf`; its built-in `d4v2` entry may prefer native BC-S1.2 circuit input and is therefore not the
strict CNF-vs-CNF control.

E4/G6 with d4v2 remain a **verification**, not a presumed fix. The scripts now WMC the d4-produced d-DNNF
locally, bypassing the old d4-v1 external weighted-count path that over-counted some large cones.

---

# RE-RUN CHECKLIST (which experiments the recent code changes invalidate)

**Map — code change → what it altered → what it invalidates:**
| change (commit) | altered | invalidates |
|---|---|---|
| **timer boundaries** (`90c3c3c`) | construct now folds in RDF-parse/answer-recovery; compile now folds in variable-ordering + ROBDD init — both were previously *un*timed, so old totals under-counted | **G3, G4, R8.3, G2a** (ours-side timings) |
| **R8.3 rewrite** (`77d067f`) | keyed per-customer parity + independent K + uses the timed shared-compile map | **R8.3** (committed CSV has no parity fields) |
| **G2b arity + raw bytes** (`406ddbe`,`fc5e4a1`) | `T_string` = actual per-product tokens; NPCS bytes = raw HTTP payload (UTF-8) | **G2b** |
| **G8 UTF-8 bytes** (`fc5e4a1`) | N-Triples byte count now UTF-8 | **G8** |
| **path fingerprint** (`7882a1e`) | reach/base gate IRIs re-namespaced + one `c:rpath` triple/reach-gate; **gates+edges and compile/WMC UNCHANGED**, construct time + byte-identity refs shift | path **byte-identity refs**; WD-path **construct** time (folds into the G3/G4 re-run) |
| **ansKey BOUND-safe + c:binding detection** (`90c3c3c`) | `c:answer` now always emitted (incl. unbound-projected answers); WMC values UNCHANGED | **E10 stored hashes/counts** for OPTIONAL/MINUS/UNION shapes (the *property* still holds); nothing numeric |
| **Level-1 d4v2 harness** | per-answer CNF→one pinned d4v2 on both systems; local linear WMC of ours' dump | **NEW:** G2a/Qrecon Level-1; E4/G6 d4v2 verification |
| **ProvSQL timing consumption** | G4 now selects `sum(p)` so PostgreSQL cannot prune `probability_evaluate` | **G4 ProvSQL Q3 row only** |

## ALREADY DONE — do NOT re-run (status corrected)
`5b34378` already re-ran the **5-run CANONICAL table on current HEAD** (post-90c3c3c timer boundaries):
the 3 ours-side main rows (watdiv-Sstar, tpch-Q3, wikidata-WDpath) are current — see
`CANONICAL_TIMINGS.md` ("current HEAD, 5-run"). Notable shifts it found: **WD-path 8.04 s → 2.14 s**
(PathIsoSeq fingerprint shrank the reconvergent cones; compile 5.75 s → ~1 ms) and **tpch-Q3 → 6.40 s**
(the 90c3c3c boundary now counts variable ordering). Do not discard those expensive real-data rows.

## MUST re-run (still stale)
- [ ] **g4_instances.py** — `python3 g4_instances.py` → `g4_instances.csv` (instance-breadth rows are still
      pre-boundary; it imports g3's timers). ‼ The 1-warm-up + 5-run protocol lives in **`g4_rigor.py` /
      `g4_instances.py`** — `g3_pqe_latency.py` runs each query **once** (a breakdown probe) and is NOT the
      source of the canonical table; do not use it for the 5-run numbers.
- [ ] **G4 ProvSQL Q3 row** — run `python3 g4_rigor.py` once under the normal 1+5 protocol and replace the
      ProvSQL row. The old `SELECT count(*) FROM (SELECT probability(...) p ...)` could prune unused `p`;
      the harness now uses `count(*),sum(p)` and therefore necessarily evaluates every probability.
- [ ] **R8.3** — `python3 r8_3_reconvergent.py` → `r8_3_reconvergent.csv` (**CSV writer now added**). ✔ check:
      columns `keys_match, k_keys_match, max_abs_error, cf_maxerr, agree` present; ours == closed form
      (independent K) and == ProvSQL per `c_custkey`; the script now enforces **1 warm-up + 5 timed runs**.
- [ ] **G2b** — `python3 g2b_npcs_vs_ours.py` → `g2b_npcs_vs_ours.csv`; rewrite the `G2b_RESULTS.md` table. ✔
      three separate metrics (structural / serialized-bytes / compiled), NO `size_win`; compare final CSV
      bytes against final deduplicated N-Triples bytes, not symmetric network payloads.
- [ ] **G8** — `python3 g8_space_memory.py` → `g8_space_memory.csv` (UTF-8 bytes).

## COMPILER ALIGNMENT — Level 1 is implemented (run separately from G3/E11)
The earlier wording ("route ours through `compile_portfolio` AND pin ProvSQL to `'compilation'`/d4, call it
'same portfolio, same compiler'") is **methodologically inconsistent** — do not run it verbatim:
- our `compile_portfolio` **auto-selects** (read-once / PWE / d4 / OBDD) while ProvSQL would be **pinned to
  compilation-only** → not the same choice;
- `compile_portfolio` has **no tree-decomposition** stage → it is ProvSQL-*inspired*, not identical;
- `compile_portfolio.probability()` is **per-root**, but G3's canonical number compiles **all answers into
  ONE shared ROBDD** — per-answer portfolio calls lose that sharing and change both meaning and cost.

Register a CNF-only ProvSQL tool (superuser; replace the absolute path):
```sql
SELECT provsql.register_tool(
  name=>'d4v2-cnf', executable=>'/ABS/PATH/d4v2', operations=>ARRAY['compile'],
  input_formats=>ARRAY['dimacs-cnf'], output_format=>'ddnnf-nnf', parser=>'nnf',
  argtpl=>'-i {in} --dump-file {out}', argtpl_circuit=>NULL, enabled=>true);
SELECT name,executable,input_formats,argtpl,argtpl_circuit,available
FROM provsql.tools WHERE name='d4v2-cnf';
```
Then smoke-test before the full 14,908-answer run:
```bash
D4=/ABS/PATH/d4v2 D4V2=1 LEVEL1_RUNS=1 LEVEL1_MAX_ANSWERS=10 \
  python3 level1_d4_headtohead.py
D4=/ABS/PATH/d4v2 D4V2=1 python3 level1_d4_headtohead.py
```
The script verifies the registry uses that **same absolute binary**, records its SHA-256, accepts CNF only,
runs 1+5, checkpoints `level1_d4_runs.csv`, writes `level1_d4_headtohead.csv`, and exits non-zero on any missing answer or parity
failure. A `sample-N` row is smoke-only and must never be cited as the full result.

- **Do NOT fold in our shared-compile advantage here.** "Compile ONCE for all answers, Θ(N+S) vs per-answer
  Θ(N·S)" is a SEPARATE result — **E11** — not this head-to-head.
- OBDD + PWE remain the independent correctness oracle (E1/G6).
(Level 2 — matching ProvSQL's full auto-portfolio incl. tree-decomposition — remains deferred.)

## d4-v2 — VERIFY (do not presume "d4-v2 fixes it")
- [ ] **E4.1 / G6 with d4-v2 is a *verification*** of whether d4-v2 fixes d4-v1's weighted over-count — NOT a
      preset fact. Refresh `e4_sweep.py` (sizes should match; confirm the weighted counts) and re-run
      `g6_d4_real.py`; RECORD whether v2 agrees with OBDD/PWE.
- [ ] ⚠ **The large reconvergent WD-path cones may no longer exist.** Per `5b34378`, PathIsoSeq collapsed the
      WD-path cones to 1–20 tokens (compile ~1 ms) — so "d4-v2 on the **>40-token** WD-path cones" is likely
      **moot** and the order-robust-d4-*for-paths* motivation is largely gone. First CHECK whether any
      >40-token path cone still exists; if none, drop that sub-task and note it. (E4's synthetic high-treewidth
      families remain the real d4 case.)

## CHEAP reference regen (the property still holds — just refresh stored values)
- [ ] **E10 byte-identity** — diff each shape's current-jar N-Triples against its stored reference and refresh
      **only the shapes that actually changed** (an answer with an unbound projected var now gains a `c:answer`
      triple). Do NOT blanket-refresh all OPTIONAL/MINUS/UNION shapes. The property (all engines byte-identical
      on one jar) is UNAFFECTED — only the stored hashes/counts move for the handful of affected shapes.

## DO NOT re-run (unaffected — do not spend server time)
- **E1 correctness** — validated locally (171/171 + all verifiers green); identity/recovery changed, WMC values did not.
- **E2 compactness** (`bench.csv`) — structural cost-model, non-path.
- **E3 construction** (`e3_*.csv`) — size metric is `gates+edges` (excludes `c:binding`/`c:answer`); non-path; timed via `watdiv_run`, not g3.
- **E5 factored**, **E11** (`e11_*.csv`) — `gates+edges` unaffected by the recovery-triple additions.
- **G7 reification** — local `CircuitRun` circuit-diff, byte-identical under both schemes; unaffected.
- **E4 core scaling result** — already done + correct with d4-v1 (`watdiv/e4_results.csv`); the d4-v2 pass above is a *confirmation*, not a redo.

---

# ROUND 9 — PAPER FIGURE COVERAGE (CURRENT EXECUTABLE TASK)

## Goal and non-goals

The current results establish the core claims, but the paper figures do not yet have the complete visual
coverage used by SPARQLprov/NPCS: **engine × query-pattern × scale** small multiples with failures retained.
Fill that grid with a single, checkpointed protocol. Do not re-run the already-current canonical Q3/R8.3/G6
rows merely to rename them, and do not manufacture a rectangular matrix by silently dropping unsupported
cells. Record `unsupported`, `timeout`, `oom`, and `not-run` explicitly.

This round distinguishes two suites; never mix them under one `query_pattern` label:

1. **Semantic gallery (validation):** the 13 E1 non-path shapes in
   `reference/engines/_gallery_shapes.py` (`atom`, `join`, `union`, the OPTIONAL/MINUS variants, `distinct`),
   plus a separate property-path gallery. These are tiny implementation checks, not a performance workload.
2. **Performance workload:** WatDiv classes **L/S/F/C/O/M**. L/S/F/C are the standard WatDiv classes,
   O is the five OPTIONAL templates used by SPARQLprov/NPCS, and M is our explicit MINUS class. Property
   paths are a separate performance suite because they use the iterative writable-endpoint protocol.

Before a long run, create `reference/paper/` and make every harness append/checkpoint one CSV row per timed
cell. A killed process must resume without repeating completed 5-run cells. Every row must contain the git
commit, engine/version, dataset id, query file SHA-256, bound values, status, timeout, warm-up count, run
count, and raw per-run samples (JSON is acceptable for the sample column).

## R9.0 — pull, build, smoke, and freeze the workload manifest

```bash
git pull --ff-only
cd engine && mvn -q package && cd ../reference
python3 verify_all.py
python3 tests.py
python3 verify_gallery.py
python3 verify_engine_paths.py
python3 verify_experiment_harness.py
```

Then create and commit `reference/paper/workload_manifest.csv` with:

```text
suite,class,template,instance,query_file,query_sha256,scale,bound_policy,notes
```

- Import the actual WatDiv L/S/F/C templates and the five O templates from the baseline artifacts where
  licensing permits; do not silently invent different O queries and call them the baseline suite.
- Include M separately. Use **all six classes L/S/F/C/O/M** in the performance matrix. Prefer five or more
  concrete instances per class; if the artifact only yields fewer runnable instances, record the exact count.
- Apply one deterministic binding policy across engines. Store the chosen RDF terms in the manifest so all
  engines execute the same query, not each engine's own `LIMIT 1` result.
- Paths get their own manifest (`p+`, `p*`, inverse/alternative, and every currently accepted compound form).
- Keep datasets outside the repository.

## R9.1 — validation matrix: every engine × every semantic pattern

The non-path part is largely available already (`engines/e10_byte_identity.csv`: 13 shapes × GraphDB,
Oxigraph, QLever, MillenniumDB = 52 checks). Re-run a cell only if the current-jar reference differs. Produce
one normalized `reference/paper/validation_matrix.csv`:

```text
suite,pattern,engine,status,circuit_triples,circuit_sha256,wmc_pwe_max_abs_error,notes
```

Acceptance:

- all supported non-path cells are byte-identical to the same current-jar reference;
- every reference circuit used in the matrix has an E1 WMC==PWE oracle result;
- path rows are a **separate block** and run only on engines where the iterative protocol actually works;
- unsupported/not-run path cells stay visible as `N/A`, never as blank successes.

The paper artifact for this RQ is a matrix/table, not a bar chart: equal-height “correct=1” bars contain no
information. A narrow side column may show circuit-triple count.

## R9.2 — SPARQLprov-style B/R/N/C timing decomposition (HIGHEST-PRIORITY NEW RUN)

### Definitions — do not reuse the legacy `plain_ms` name

SPARQLprov measured three *alternative executions*: `B` (original query on base data), `R` (the same query
over reified data, but without provenance), and `P` (the provenance query over reified data). Its stacked
shares are `B`, `R-B`, and `P-R`; **B/R/P are not three sequential stages that are added together.**

Our faithful analogue uses four alternatives:

- **B — base query:** the original SELECT over the unreified RDF graph.
- **R — reification-only query:** preserve the original SPARQL algebra, but replace each triple pattern by
  the selected reification scheme's statement lookup. No token output, GROUP_CONCAT, SHA256, gate IRI, or
  CONSTRUCT template. This is the missing control.
- **N_clean — NPCS-compatible provenance SELECT:** the committed `NpcsRewriter` output, producing
  per-answer provenance strings. Its JavaDoc correctly calls it a **clean-room implementation** of the NPCS
  rules; it is not the authors' official binary.
- **N_official (preferred baseline where buildable):** the authors' NPCS artifact
  (`https://github.com/ZubariaForthAcc/NPCS`). Pin its commit/version and run it through the same endpoint
  protocol. If it cannot be built, keep `N_clean`, validate polynomial/answer parity on the small gallery,
  and label it “NPCS reimplementation” in every paper artifact.
- **C — SPARQLcirc circuit CONSTRUCT:** the actual `CircuitRewriter` plan, producing the shared circuit.

The legacy E3/E6 column called `plain_ms` is **N_clean**, not B: those harnesses call the clean-room
`get_npcs()`. Never relabel the old value as original-query time or as an official-NPCS measurement. In new
artifacts use an `implementation` column plus `b_ms`, `r_ms`, `n_ms`, and `c_engine_ms`.

Implement `reference/paper_construction_matrix.py` (or an equivalently named, documented harness) and a
small algebra-preserving reification-only rewriter for R. It must support the six performance classes,
including UNION/OPTIONAL/MINUS structure, and have parser/unit tests before server timing. A textual regex
replacement that breaks nested SPARQL algebra is not acceptable.

### Timing boundaries

For each B/R/N/C execution use the same HTTP client, endpoint host, timeout, warm-up policy, and response
drain. Record:

- `rewrite_ms` separately for N/C query generation (diagnostic; not part of B/R/N/C engine execution);
- `*_engine_ms`: POST immediately before send through the final response byte read;
- `c_parse_ms`: parse/deduplicate the final circuit and recover answer bindings;
- `construct_total_ms = c_engine_ms + c_parse_ms`;
- later, end-to-end adds `compile_ms + wmc_ms` to `construct_total_ms` exactly once.

Do **not** report “B + P + construct.” For SPARQLcirc, C already is provenance computation fused with
circuit construction. The valid decompositions are:

```text
NPCS:       baseline B  + reification (R-B) + string provenance (N-R) = N
SPARQLcirc: baseline B  + reification (R-B) + circuit provenance/construction (C-R) = C
full PQE:   construct_total + compile + WMC
```

Store the raw B/R/N/C medians as primary data. Derive deltas only after aggregation. If `R<B`, `N<R`, or
`C<R` because of optimizer choices/noise, do not clamp the delta to zero and do not draw a false positive
stack: use grouped raw columns or signed deltas for that panel.

### Matrix and protocol

- Engines: **GraphDB, Oxigraph, QLever, MillenniumDB**. Attempt every non-path cell. Capability failures
  are results (`unsupported` with the endpoint error), not reasons to delete an engine panel.
- Scales: ordinary WatDiv **10M and 100M**.
- Patterns: all **L/S/F/C/O/M** classes in the frozen manifest.
- Per concrete query/method: 1–2 warm-ups + **5 timed runs**, 300 s timeout, quiescent machine; report median,
  min, max, mean, and SD. Preserve timeouts at the 300 s plot boundary.
- Maintain separate base and reified repositories built from the same logical facts. Verify B and R return
  the same canonical answer multiset before timing; for N/C verify the same answer keys as B/R.

Write `reference/paper/construction_brnc.csv`. Required columns include:

```text
commit,engine,engine_version,scale,class,template,instance,query_sha256,method,implementation,status,
answers,median_ms,min_ms,max_ms,mean_ms,sd_ms,warmups,runs,timeout_s,response_bytes,
c_parse_median_ms,gates,edges,notes
```

Preferred paper figure: one double-column `figure*`, **2 rows (10M/100M) × 4 engine columns**, identical
query-class order and one shared legend. For each class, pair NPCS and SPARQLcirc totals and decompose each
as B / R-B / N-R or C-R when the observed deltas are non-negative. This is the direct SPARQLprov-style
overhead experiment. It also provides the NPCS-vs-SPARQLcirc construction cells for R9.3.

### NPCS-style 200M-multisource stress case — separate, not a scale point

Do not call the broken WatDiv generator. Mirror NPCS's construction: start from the 100M logical facts,
duplicate each fact in the provenance-bearing representation, and assign a **different second provenance
identifier**. Record all three counts: unique logical facts, provenance statements, and physical RDF triples
(Standard reification stores several physical triples per statement).

This changes source multiplicity, so `R-B` is no longer a pure reification overhead. Label the dataset
`100M×2-sources` / `200M-multisource`, show it in a separate stress panel, and report raw R/N/C values; do
not place it on the ordinary 10M→100M scale line or claim a clean 2× data-scaling result.

## R9.3 — sharing boundary and actual NPCS comparison

Reuse R9.2 N/C responses; do not issue a second long query merely to count bytes. Write
`reference/paper/sharing_npcs.csv` with, per engine/query instance:

- answers and derivations;
- NPCS token occurrences and final UTF-8 CSV bytes;
- SPARQLcirc gates+edges and final deduplicated N-Triples bytes;
- N/C construction medians from R9.2;
- explicit ratios in same units only (elements/elements, bytes/bytes).

Aggregate and plot **all L/S/F/C/O/M classes**, retaining low-sharing counterexamples. The existing E2/E11
synthetic reconvergence curves remain the controlled explanation; the new matrix supplies external validity.
NPCS may be unsupported on an engine; record N/A. Do not infer its time from another engine.

## R9.4 — compilation: all real query classes, but do NOT repeat identical circuits per engine

Compilation is client-side. R9.1 already establishes that engines emit the same circuit, so recompiling the
same bytes four times is pseudoreplication, not cross-engine evidence. Use one canonical emitted circuit per
manifest instance and write `reference/paper/compile_patterns.csv` for all **L/S/F/C/O/M + path** classes:

```text
class,template,instance,input_gates,input_edges,cnf_vars,cnf_clauses,compiler,status,
compile_median_ms,compile_sd_ms,compiled_nodes,wmc_ms,wmc_pwe_max_abs_error,timeout_s
```

- Compare OBDD and d4/d-DNNF with 1+5 where feasible; preserve timeout.
- Apply the canonical **120 s per compilation attempt** to both compilers. Enforce an in-process OBDD limit
  in a killable worker process; checking elapsed time only after compilation returns is not a timeout.
- Keep E4's treewidth-controlled line plots as the scalability evidence.
- Add a categorical grouped-column panel over the real query classes for compiled nodes/time.
- WMC must agree with the available PWE/OBDD oracle on sampled small roots.

## R9.5 — end-to-end PQE matrix and the applicable ProvSQL subset

For each supported non-path R9.2 C cell, run the full pipeline and write `reference/paper/e2e_matrix.csv`:

```text
engine,scale,class,template,instance,status,answers,construct_total_ms,compile_ms,wmc_ms,
total_ms,median_ms,sd_ms,probability_checksum
```

Use the same circuit/answer set and report construct/compile/WMC without overlap. Facet the SPARQLcirc-only
figure by engine and query class. Because compile/WMC are engine-independent for an identical circuit, the
caption must say that engine variation primarily reflects construction; do not present repeated compile
numbers as independent compiler measurements.

ProvSQL is an applicable baseline only for the matched TPC-H relational queries. Reuse the already-current
forced-evaluation Q3 and keyed R8.3/Qrecon results; add scale/instance cells only if absent. Never manufacture
ProvSQL bars for WatDiv graph patterns or property paths. Unsupported comparison cells must say `N/A`.

## R9.6 — property-path matrix (separate writable protocol)

Write `reference/paper/path_matrix.csv` covering every currently accepted path pattern from the frozen path
manifest on every engine for which the iterative protocol actually completes. Record:

```text
engine,path_pattern,status,source,reachable_nodes,rounds,answers,gates,edges,construct_ms,
compile_ms,wmc_ms,peak_rss_mb,circuit_sha256,wmc_pwe_max_abs_error,notes
```

- Keep non-writable/unsupported cells visible.
- Apply the canonical **300 s** cap to the complete per-query iterative construction protocol and the
  canonical **120 s** cap to each subsequent compiler attempt.
- Separate semantic pattern breadth (`p+`, `p*`, inverse/alternative/accepted compound forms) from scaling.
- For scaling, vary reachable-set size and graph density/cycles; use lines, not categorical bars.
- State the IRI-frontier boundary and current compound/nested limitations in the result file.

## R9.7 — output, audit, and commit discipline

Create `reference/paper/PAPER_RESULTS.md` and `reference/paper/ENVIRONMENT.md`. Before committing:

1. assert every CSV row maps to a manifest query hash;
2. assert B/R answer multisets and N/C answer-key sets agree for every `ok` cell;
3. assert run counts and timeout policy are uniform or explicitly explained;
4. ensure all failed/unsupported cells survived aggregation;
5. run `python3 verify_experiment_harness.py`, `python3 tests.py`, and `git diff --check`;
6. never commit datasets, endpoint databases, raw multi-GB responses, or generated circuits.

Suggested execution priority:

1. R9.0 manifest + R rewriter/unit tests;
2. R9.2 at 10M (all engines/classes) — validates the protocol and produces the main paper figure;
3. R9.2 at 100M with checkpoint/resume;
4. R9.1 path coverage + R9.6;
5. R9.3/R9.4/R9.5 derived and compiler runs;
6. optional `200M-multisource` stress panel last.

Commit scripts/tests before starting multi-day runs so failures are reproducible. Commit result CSVs and
environment logs in small batches. Push normally after each completed scale; do not wait for every optional
cell before preserving completed work.
