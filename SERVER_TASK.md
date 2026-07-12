# SPARQLcirc — server experiment task (read me first)

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
| **G2b — WatDiv leg** (just landed) | `g2b_npcs_vs_ours.csv` — **real NpcsRewriter** vs ours on WatDiv 32.7M: **10–27× more compact** (validates E2 on the real system); NPCS ~1.8× faster to build. **Do NOT re-run the WatDiv comparison** — only the WDBench-curated leg remains (below) |

Where a DONE row says "the SCALE version is Gx", that Gx is a **new experiment below**, not a re-run of the
toy/partial one already done.

## 🔄 Must-haves — ALREADY in ROUND 6 (continue; do NOT restart if running)
**G1** paths-at-scale · **G2a** ProvSQL/TPC-H · **G2b** *(WatDiv leg DONE ↑; only the WDBench-curated graph
remains — it was download-blocked, so unblock the graph or descope that leg)* · **G3** end-to-end latency ·
**G4** rigor · (should-have) **G5 G6 G7 G8 G10** · (scope) **G9**. Definitions unchanged — see ROUND 6 above.
If you have already started any of these, keep going; the notes below only *refine* them.
Note: **G5 (NPCS side) is now partly done** — `g2b_npcs_vs_ours.py` runs the *real* NpcsRewriter; only the
**SPARQLprov** artifact still needs measuring.

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
