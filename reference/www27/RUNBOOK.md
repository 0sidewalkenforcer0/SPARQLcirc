# WWW'27 experiments E1 and E2: runbook

Branch `www27-experiments` (forked from the artifact snapshot `452e905`).
Nothing here has been run on WatDiv 10M or Wikidata yet; the code was checked
on toy data and loopback endpoints only (see "Local verification" at the end).

* **E1** NPCS under comparable conditions on WatDiv 10M: original NPCS jar
  (N-orig) next to our re-implementation (N), all methods in one layout,
  endpoint-only timing, warm and cold cache, medians and ratios.
* **E2** End-to-end PQE for NPCS: endpoint, client-side merge of the NPCS
  strings into one DAG, compilation (CUDD shared manager, d4v2 per root),
  WMC, under the same budget as the circuit methods, on WatDiv and Wikidata.

## 1. What changed relative to the published protocol

| Aspect | Published runs (artifact `452e905`) | WWW'27 runs (this branch) |
| --- | --- | --- |
| NPCS rewriter | re-implementation only (`npcs-rewrite.jar rewrite`) | N-orig = original `ReifySparqlByte.jar` on BGP queries; N = re-implementation |
| Layout of R, N, C | `--scheme SPARQL_Star` = asserted triple **plus** occurrence lookup (one extra join per pattern) | chosen explicitly: `--layout lookup-only` (`SPARQL_Star_Pure`, the original jar's only RDF-star layout) or `asserted-plus-lookup` |
| Endpoint timer | `endpoint_e2e_ms`: worker start, query read, **rewrite** (fresh JVM for N, rdflib import for R), request, streamed TSV handling | `endpoint_only_ms`: HTTP exchange only (definition in section 6); the old window is kept as `legacy_endpoint_e2e_ms` |
| Store of B | plain store only | B (plain store) **and** B-mixed (same text on the mixed store) |
| Cache | warm (1 warm-up + 5) | warm (1 + 5) or cold (hook before every execution, 0 or 1 warm-up + 5) |
| Statistic | figure CSV used `successful_mean_ms` (mean) | median of the 5 measured executions per cell, median of instances per template, ratios per template, median of ratios; no mean anywhere |
| NPCS PQE | never run (`--pqe-backend none`, Wikidata PQE batch only for C) | N-shared DAG through CUDD and d4 with the circuits' budget rule |
| Unsatisfiable answer roots | reported with probability 0 | dropped after compilation for every method (CUDD/oracle: the root is the false terminal; d4: the d-DNNF has no model, decided structurally); counted as `unsatisfiable_roots_dropped`; `--keep-unsatisfiable` restores the old output |
| Zero-length pair of `e*`/`e?` at a constant or bound source | "the source occurs in the world" (the ⊕ of the tokens that mention it) | `g_⊤ = ⊗(∅)`, the W3C reading, as in the paper and the Lean model. This changes the WatDiv `P-star` template and all five Wikidata closure queries (`wdbench-property-paths-5` are `*` closures): the source's own answer now has probability 1, and its circuit has one ⊤ gate instead of the incident-token gates, so their published circuit hashes and PQE times do not carry over |
| FILTER on skolemized data | `isBlank`, `isIRI`, `STR` rendered as written | rendered against the skolem namespace `urn:sk:`, so a filter answers as on the graph with blank nodes; gate keys of queries with these built-ins change |

Defaults of `watdiv10m_runner.py`, `run_cell.py`, `run_shard.py`,
`run_pqe_batch.py` and `npcs_postprocess.py` still reproduce the published
protocol; every new behaviour is opt-in and recorded in each `cell.json`
(`protocol.timing_protocol`, `layout`, `store`, `cache_mode`,
`cache_reset_command`, `pqe_backend`).

## 2. Environment

```bash
export REPO=/path/to/sparqlcirc-www27            # this branch
export JAR=$REPO/engine/target/npcs-rewrite.jar
export NPCS_ORIG_JAR=/path/to/ReifySparqlByte.jar  # used by the runner and batch as --orig-jar default
export WORKLOAD=/data/watdiv/workload-10m        # published WatDiv 10M workload root
export DATASET=/data/watdiv/sf1/dataset.json     # published WatDiv scale-1 dataset manifest
export RESULTS=/results/www27                    # new, empty result root
export D4=/opt/d4v2/d4                           # pinned d4v2 binary (same as the published C d4 runs)
export GDB=http://127.0.0.1:7200/repositories    # GraphDB with repositories base and mixed
export GRAPHDB_PID=...                           # PID of the GraphDB JVM (warm runs)
```

| Variable | Read by | Meaning |
| --- | --- | --- |
| `NPCS_ORIG_JAR` | `watdiv10m_runner.py --orig-jar`, `watdiv_e1_batch.py --orig-jar`, tests | original NPCS jar; required for N-orig |
| `D4V2=1` | `reference/d4_pipeline.py` | d4v2 command syntax (`d4_from_artifact.py` sets it itself; set it when calling the d4 tests with a d4v2 binary) |
| `D4V2_DDNNF_CMD`, `D4_DDNNF_CMD` | `reference/d4_pipeline.py` | override of the d4 command template, only for non-standard builds |
| `WWW27_D4` | `reference/www27/test_*.py` | run the d4 cases of the tests with this binary |
| `SPARQLCIRC_RUN_DIR`, `SPARQLCIRC_RUN_ID`, `SPARQLCIRC_QUERY_ID`, `SPARQLCIRC_ENGINE`, `SPARQLCIRC_METHOD`, `SPARQLCIRC_BASE_ENDPOINT`, `SPARQLCIRC_MIXED_ENDPOINT` | the cold-cache hook | exported by the runner to `--cache-reset-command` |

Python 3.11 with `reference/requirements-production.txt` (`dd==0.6.0`, CUDD)
and `reference/requirements-optional.txt` (rdflib: R and the N-orig query
gate); Java 11+, Maven 3.6+.

## 3. Checklist before the real runs

Run on a compute allocation, not a login node.

1. Record the code and tools.
   ```bash
   cd $REPO && git rev-parse HEAD && git status --short     # must be clean
   mvn -q -f engine/pom.xml package                         # also runs the JUnit suite
   sha256sum "$NPCS_ORIG_JAR"   # expect 328916c1c20294ddf492b6146bc0e8e542b2f7bc73abcb6d551ef64de6f17dce
   java -version; python3 --version; sha256sum "$D4"         # keep with the results; the runners compute no digests
   ```
2. Tests (all must pass; the N-orig cases run because `NPCS_ORIG_JAR` is set).
   ```bash
   python3 reference/verify_compiler.py --require-cudd
   python3 reference/paper/test_npcs_postprocess.py --require-cudd
   python3 reference/paper/test_watdiv10m_runner.py
   python3 reference/www27/test_runner_www27.py
   python3 reference/www27/test_e1_batch_summary.py
   D4V2=1 WWW27_D4=$D4 python3 reference/www27/test_npcs_pqe_toy.py
   WWW27_D4=$D4 python3 reference/www27/test_pqe_posthoc.py
   D4V2=1 WWW27_D4=$D4 python3 reference/www27/test_unsat_roots.py
   ```
   `test_unsat_roots.py` with the pinned d4v2 is the check that d4v2 writes a
   d-DNNF with a false node for an unsatisfiable root, as d4 v1 does. If it
   writes no file instead, `d4_compile_once` stops with "d4 produced no
   d-DNNF"; report it before the runs rather than treating it as a timeout.
   `test_watdiv10m_runner.py` has one known failure on macOS only
   (`/var` vs `/private/var` in `test_circuit_offline_resume_reuses_the_saved_circuit`);
   on Linux it must pass.
3. Stores. Same GraphDB version, heap, `-XX:ActiveProcessorCount`, page cache
   and CONCAT/GROUP_CONCAT length setting as the published runs (NPCS
   provenance strings are long; a truncated string fails post-processing).
   Both repositories loaded from `$DATASET`: `base` = `base.nt`, `mixed` =
   `mixed-rdfstar11.ttls`. Check the C cell protocol of the published GraphDB
   run (`cell-config.json`: `c_endpoint_protocol`, update endpoint) and use the
   same values below.
4. Prepare all query texts (no endpoint is contacted):
   ```bash
   python3 reference/www27/watdiv_e1_batch.py prepare \
     --workload $WORKLOAD --layout lookup-only --jar $JAR --orig-jar $NPCS_ORIG_JAR \
     --out $RESULTS/e1-prepare/lookup-only
   ```
   Inspect `prepare-report.json`. Expected from the template definitions (not
   yet observed on the generated instances): `prepare_errors: []`,
   `n_orig.supported: 60` (L, S, F, C templates x 3 instances),
   `n_orig.unsupported: 30` with `refusal_reasons` `{"MINUS": 15, "OPTIONAL": 15}`,
   `n_orig.equivalent_to_reimplementation: 60`, `n_orig.not_equivalent: []`,
   and every `rdf4j_parse` of N-orig `"ok"`. Any other count needs a look at
   the listed query before the endpoint runs.
5. Smoke run of one template on every engine (all methods, 1 instance):
   ```bash
   python3 reference/www27/watdiv_e1_batch.py run ... --template L1 --template O1 --instance 00 \
     --out $RESULTS/smoke/<engine>
   ```
   (other arguments as in section 4). Check: every cell `ok` except
   `O1/00/N-orig` = `unsupported`; `endpoint_only_ms <= legacy_endpoint_e2e_ms`
   in every run; the summary (section 4.4) reports no unequal answer pairs.
   This is the first time the original jar's output (`<<\t?s <p> <o>>>`) meets
   Fuseki and Oxigraph; a parse error there shows up here.
6. Cold mode only: run the hook by hand once, confirm it needs no password
   (`sudo -n true`), that the store comes back, and decide whether OS caches
   can be dropped on the allocated node. If not, the hook only restarts the
   store; say so in the paper. The command is stored in every cold cell.

## 4. E1 commands (WatDiv 10M)

### 4.1 GraphDB, lookup-only layout, warm cache, endpoint-only timing

```bash
python3 reference/www27/watdiv_e1_batch.py run \
  --workload $WORKLOAD --dataset $DATASET \
  --layout lookup-only --timing-protocol endpoint-only \
  --engine graphdb-10.7.6 --out $RESULTS/e1/graphdb/lookup-only/warm \
  --base-endpoint $GDB/base --mixed-endpoint $GDB/mixed \
  --update-endpoint $GDB/mixed/statements \
  --jar $JAR --orig-jar $NPCS_ORIG_JAR \
  --cache-mode warm --warmups 1 --runs 5 \
  --engine-pid $GRAPHDB_PID --continue-after-failure
```

Matrix per engine and layout: 30 ordinary templates x 3 instances x 7
methods (B, B-mixed, R, N-orig, N, C-flat, C-factorised) + 2 path templates
x 3 instances x 3 methods (B, B-mixed, C-path) = 648 cells, of which the 30
N-orig cells of O1-O5 and M1-M5 end as `unsupported` without a request. The
batch is resumable: rerun the same command after an interruption; a partial
cell directory without `cell.json` must be moved away first. A C failure that
leaves the store dirty stops the batch (`recovery_required`).

### 4.2 Same, asserted-plus-lookup layout (to connect to the published numbers)

B and B-mixed do not depend on the layout; leave them out so that the summary
sees each of them once:

```bash
python3 reference/www27/watdiv_e1_batch.py run \
  ... --layout asserted-plus-lookup --out $RESULTS/e1/graphdb/asserted-plus-lookup/warm \
  --method R --method N --method C-flat --method C-factorised --method C-path
```

### 4.3 Cold cache

Other arguments as in 4.1, except `--engine-pid` (the PID changes with every
restart; the runner rereads `--engine-pid-file` before each execution and
refuses both options together):

```bash
python3 reference/www27/watdiv_e1_batch.py run \
  ... --layout lookup-only --out $RESULTS/e1/graphdb/lookup-only/cold \
  --cache-mode cold --warmups 0 --runs 5 \
  --cache-reset-command "$RESULTS/cold-reset-graphdb.sh" \
  --cache-reset-timeout 900 --cache-ready-probe ask --cache-ready-timeout 900 \
  --engine-pid-file /run/graphdb.pid
```

The hook runs before every execution (warm-up included) and before the
execution's clock starts; afterwards the runner sends `ASK {}` to the method's
endpoint until it answers. Example hook (adapt; never run by the code itself):

```sh
#!/bin/sh
# cold-reset-graphdb.sh: stop the store, drop the OS page cache, start the store.
set -eu
sudo -n systemctl stop graphdb            # or the deployment's stop command
sync
echo 3 | sudo -n tee /proc/sys/vm/drop_caches >/dev/null
sudo -n systemctl start graphdb           # must write the new PID to /run/graphdb.pid
```

Both GraphDB repositories live in one GraphDB process, so one restart resets
the plain and the mixed store. For Fuseki/Oxigraph with one process per store,
the hook can use `SPARQLCIRC_METHOD` (B uses the plain store, every other
method the mixed store).

### 4.4 Summaries

```bash
python3 reference/www27/watdiv_e1_summarize.py --workload $WORKLOAD \
  --cells $RESULTS/e1/graphdb/lookup-only/warm \
  --cells $RESULTS/e1/graphdb/asserted-plus-lookup/warm \
  --json $RESULTS/e1/graphdb-warm-summary.json \
  --csv $RESULTS/e1/graphdb-warm-templates.csv \
  --ratios-csv $RESULTS/e1/graphdb-warm-ratios.csv
```

Repeat with `--window legacy` (other output names) to summarize the old
window measured in the same executions, and with `--c-field c_request_ms_sum`
for C's summed CONSTRUCT request times. Cold and warm cells may be passed
together; they form separate groups.

### 4.5 Fuseki and Oxigraph

Same commands with `--engine fuseki-5.4.0` / `--engine oxigraph-<version>`,
their plain and mixed query endpoints, `--update-endpoint` of the mixed store,
`--c-endpoint-protocol sparql`, and their PIDs. Use the endpoint URLs and
versions of the published runs (their `batch-config.json`). The published
Oxigraph cells are labelled `oxigraph-0.4.11` while the paper says 0.5.9;
record the version actually started.

## 5. E2 commands

### 5.1 WatDiv: PQE over the E1 artifacts

E1 N, N-orig and C cells keep their artifacts (`<run>/pp/npcs-hc-dag.json`,
`<run>/circuit.nt`); PQE runs afterwards on the client, no endpoint needed.

```bash
python3 reference/www27/watdiv_pqe_posthoc.py run \
  --cells $RESULTS/e1/graphdb/lookup-only/warm \
  --out $RESULTS/e2/watdiv/graphdb/lookup-only/cudd \
  --backend cudd --probability-seed 42 --complete-method-timeout 600 --continue-after-failure

python3 reference/www27/watdiv_pqe_posthoc.py run \
  --cells $RESULTS/e1/graphdb/lookup-only/warm \
  --out $RESULTS/e2/watdiv/graphdb/lookup-only/d4 \
  --backend d4 --d4 $D4 --probability-seed 42 --complete-method-timeout 600 --continue-after-failure

python3 reference/www27/watdiv_pqe_posthoc.py compare \
  --pqe $RESULTS/e2/watdiv/graphdb/lookup-only/cudd \
  --pqe $RESULTS/e2/watdiv/graphdb/lookup-only/d4 \
  --json $RESULTS/e2/watdiv/graphdb/lookup-only/parity.json
```

`parity.json` must list no `disagreements`: N, N-orig, C-flat, C-factorised
(and both backends) must give the same probability for every answer of a
query (relative tolerance 1e-9). An alternative is in-line CUDD PQE inside the
E1 executions (`watdiv_e1_batch.py run ... --pqe-backend cudd
--probability-seed 42`); the post-hoc route keeps the E1 timings free of PQE
and gives d4 the same inputs.

### 5.2 Wikidata

(a) From the published construction roots, if their N-paired cells still hold
`<run>/N-shared/pp/npcs-hc-dag.json`:

```bash
for shard in 0 1 2; do
  for backend in cudd d4; do
    python3 reference/wdbench/run_pqe_batch.py \
      --manifest reference/wdbench/workloads/wikidata-141/manifest.json \
      --construction-cells /results/wikidata/shard-$shard \
      --source-root $REPO --out $RESULTS/e2/wikidata/pqe-$backend-shard-$shard \
      --backend $backend --d4 $D4 --shard-index $shard --probability-seed 42 \
      --method N-shared --method C-flat --method C-factorised --method C-path \
      --continue-after-failure
  done
done
```

(b) Fresh construction cells in the lookup-only layout with endpoint-only
timing, then (a) on the new roots:

```bash
python3 reference/wdbench/run_shard.py \
  --manifest reference/wdbench/workloads/wikidata-141/manifest.json \
  --shard-index 0 --result-root $RESULTS/wikidata/shard-0 \
  --source-root $REPO --session-id graphdb-www27-001 --graphdb-pid "$GRAPHDB_PID" \
  --mixed-endpoint $GDB/wikidata-mixed --update-endpoint $GDB/wikidata-mixed/statements \
  --jar $JAR --java java --reified-data /data/wikidata/occurrences.ttls \
  --warmups 1 --runs 5 --timeout 600 \
  --scheme SPARQL_Star_Pure --timing-protocol endpoint-only
```

Stage table (Figure 6 input; N rows have `method = N`, the existing R script
plots only the C rows):

```bash
python3 reference/wdbench/summarize_pqe.py \
  --manifest reference/wdbench/workloads/wikidata-141/manifest.json \
  --cells $RESULTS/e2/wikidata/pqe-cudd-shard-0 ... --cells $RESULTS/e2/wikidata/pqe-d4-shard-2 \
  --out $RESULTS/e2/wikidata/query_stage_times.csv
```

## 6. Timing definitions

* `endpoint_only_ms` (B, B-mixed, R, N, N-orig; endpoint-only protocol): client
  clock from immediately before the HTTP POST is opened until the read that
  returns end-of-body. Included: TCP connect, sending the request, the
  endpoint's parsing, planning, evaluation and serialization, and the
  transfer of the whole body into client memory. Not included: rewriting
  (done before the first execution, `prepared-query.rq`), reading the query
  file, result parsing, answer/provenance extraction, writing files. A body
  above `--endpoint-buffer-bytes` (default 1 GiB) is appended to a spool file
  from that point; such runs have `response_spilled_to_disk: true` and the
  summary counts them per cell.
* `headers_ms`, `first_body_byte_ms`: offsets inside that window.
* `legacy_endpoint_e2e_ms` (endpoint-only protocol): the published window,
  measured in the same execution: worker start, query read, this execution's
  rewrite (fresh JVM for N and N-orig, rdflib for R), the exchange, and the
  post-processing of the response. Its rewrite output must equal the prepared
  text byte for byte, otherwise the execution fails with
  `prepared-query-mismatch`.
* `endpoint_only_ms` for C: CircuitRun's `construction_ms` (every CONSTRUCT of
  the plan with streamed client-side parsing and classification, private-row
  feedback, workspace cleanup, final normalization; no JVM start, plan
  generation, repository set-up, serialization, persistence). Also recorded:
  `c_request_ms_sum` (sum of per-CONSTRUCT `query_ms` + `feedback_ms`) and
  `legacy_endpoint_e2e_ms` (CircuitRun process start to persisted circuit).
* E2 stages (`stage_medians_ms` in the PQE cells): `endpoint_ms` (WatDiv: the
  E1 cell's primary field; Wikidata: the worker process wall observed by
  `run_cell.py`, which is also the budget basis there; `endpoint_request_ms`
  holds the worker's own figure), `parse_merge_ms` (N only: parse, Boolean
  normalization, query-global hash-consing, DAG persistence), `circuit_parsing_ms`
  (inside the PQE process: artifact load, probability preparation, result
  persistence), `compile_ms` (CUDD: `compile_wall_ms`; d4: CNF export + d4),
  `wmc_ms`, `full_pipeline_ms` = sum. Budget: the complete-method deadline
  (600 s) minus endpoint (and parse+merge for N) is what the PQE process gets.
* Medians: every statistic is a median of the five measured executions; the
  E1 summary takes the median over instances per template, forms ratios per
  template, and reports the median of the per-template ratios.

## 7. Outputs

E1 cell (`cells/<template>/<instance>/<method>/`): `cell.json`
(`watdiv-brnc-cell-v1`; new fields `protocol.{timing_protocol, layout, store,
cache_mode, cache_reset_command, cache_ready_probe, pqe_backend}`,
`prepared_query`, `summary.endpoint_only_ms`, `summary.legacy_endpoint_e2e_ms`;
`status` adds `unsupported` and `prepare-error`), `prepared-query.rq`/`.json`
(N-orig also `reimplementation-query.rq`, `gate`, `rdf4j_parse`,
`reimplementation_equivalent`), and one directory per execution with
`run.json` (`cache_reset` in cold mode), `endpoint-result.json`, `rewritten.rq`,
`response/` (TSV evidence, NPCS provenance rows), `pp/` (N: `npcs-hc-dag.json`,
answer records) or `circuit.nt` + `c-stages.jsonl` (C).

E1 summary CSV (`--csv`), one row per engine, timing protocol, cache mode,
template and method: `engine, timing_protocol, cache_mode, template,
display_method, raw_method, layout, time_field, expected_instances,
measured_endpoint_ok, endpoint_timeout, unsupported, failed,
successful_median_ms, plotted_successful_median_ms, plot_kind, ratio_vs_B,
ratio_vs_B_mixed, ratio_vs_R` (`ratio_vs_R` uses R of the same layout).
Ratios CSV (`--ratios-csv`): `engine, timing_protocol, cache_mode,
display_method, raw_method, layout, reference, templates_with_ratio,
median_of_template_ratios, min_template_ratio, max_template_ratio`. The JSON
adds per-cell medians and `answer_parity` (B/B-mixed/R answer multisets and
N/N-orig answer sets on measured-01). `plot_figure3.py` only knows the
published five methods and three engines; the new rows need a new plot.

E2 WatDiv PQE cell (`<out>/cells/<template>/<instance>/<method>/<backend>/cell.json`,
`www27-watdiv-pqe-cell-v1`): `status`, `measured_successes`,
`stage_medians_ms` (section 6), per-run records with the PQE metrics and
`probabilities.jsonl` per run. `parity.json`: `checked_pairs`,
`disagreements`, `checks`. Wikidata PQE cells keep `wikidata-circuit-pqe-cell-v1`
with `method = N-shared` for NPCS and the stage fields of section 6.

## 8. Local verification (2026-10-16) and what is still open

Checked locally (macOS, Java 17, Python 3.13, no CUDD, d4 v1 arm64 build):
loopback tests of the runner (endpoint-only window, spooling, B-mixed,
N-orig refusal before any request, cold-cache hook and its failure path,
PID file), a loopback E1 batch (prepare, run, resume, summary with medians
and ratios), the toy E2 equality NPCS DAG = C-flat = C-factorised =
possible-world enumeration for BGP, UNION, OPTIONAL and MINUS in both layouts
(oracle BDD and d4 v1), and the WatDiv/Wikidata PQE runners on real toy
artifacts.

Added later on 2026-10-16, also checked locally:

- Unsatisfiable roots: `test_unsat_roots.py` (oracle BDD and d4 v1). On
  engine circuits for `x p y MINUS { x p y }` every candidate root is dropped,
  and on `x p y MINUS { x p <B> }` exactly the answer that possible-world
  enumeration never produces is dropped, while a satisfiable root of
  probability 0 is kept.
- Zero-length root: JUnit `theZeroLengthRootOfABoundSourceIsTop`,
  `optionalPathFromAConstantDenotesTheQueryEventInEveryWorld` (RDF4J's W3C
  evaluator as oracle, every world), `tests.py` cases `path_star_src`,
  `path_star_absent`, `path_opt_tgt`.
- FILTER under skolemization: JUnit `filtersSeeSkolemIrisAsTheBlankNodesTheyReplace`
  (seven filters, both plans, oracle = RDF4J over the original graph with its
  blank node, including the meta-review's `isBlank` example).
- The original jar's output on Oxigraph (pyoxigraph 0.5.8): parsed, every
  answer has provenance, and the merged DAG equals possible-world enumeration
  on the toy BGP queries (`test_original_jar_output_runs_on_oxigraph`).
- N-orig coverage of the public NPCS workloads by `npcs_orig.static_gate`
  (no jar, no endpoint): Wikidata `npcs-public-136` = 86 accepted (49 single,
  37 multiple), 50 refused (all OPTIONAL); NPCS WatDiv `queries/Basic/watdiv`
  = 138 accepted (L 50, S 53, F 32, C 3), 50 refused (all OPTIONAL), one
  empty file (`L3`). The E1 counts of section 3.4 concern the generated
  instances and remain open.

Not verified until the cluster run: any WatDiv 10M or Wikidata measurement;
CUDD (`dd.cudd` is not installed locally); the pinned d4v2; C cells against a
live endpoint in the lookup-only layout (only in-memory RDF4J); the original
jar's output on Fuseki; the cold-cache hook on real stores; the expected
N-orig counts of section 3.4 on the generated instances.
