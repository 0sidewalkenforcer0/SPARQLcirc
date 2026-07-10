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
