# VLDB experiment execution plan

This is the executable plan for the final evaluation campaign.  A result is
paper-eligible only when it was produced from the frozen experiment commit,
maps to a manifest query hash, records failures as rows, and passes the audit
gate below.  Existing CSV files from older commits are diagnostic inputs, not
final measurements.

## Invariants

- Freeze construction and compilation before timing.  The production pipeline
  is engine-native factored construction (flat is an ablation) followed by the
  CUDD multi-output compiler (shared by default, per-root as the controlled
  alternative).  d4, PySDD, and the Python ROBDD remain evaluation/oracle
  baselines, not user-facing compiler choices.
- Never combine output roots with a Boolean gate.  `shared` means one CUDD
  manager, one variable order, and shared source/BDD/WMC memoization for a root
  map; `per-root` means independent managers with the same global relative
  order.
- Use the committed query manifests and deterministic bindings on every engine.
  Preserve `unsupported`, `timeout`, `oom`, `answer-mismatch`, and `not-run`.
- Use one warm-up plus five measured executions unless a row explicitly records
  a different protocol.  The whole query-side cell has a 300 s deadline; each
  compiler attempt has a 120 s deadline.
- Datasets, endpoint indexes, multi-GB responses, and circuit caches stay
  outside Git.  CSV rows record their identifiers, hashes, versions, and the
  frozen Git commit.
- At each stage boundary: validate, make a scoped commit, run
  `git pull --rebase origin main`, repeat affected checks if the rebase changes
  code, and `git push origin main`.

## Stage gates

### S0 — repository and environment baseline (complete)

Run the clean Java build, the 171-case reference suite, live Java/RDF circuit
smoke tests, R9 harness tests, and diff hygiene.  Inventory services, datasets,
compiler binaries, disk, memory, and versions.  Preserve unrelated worktree
files.  Record any result-generating process still tied to an older commit.

Acceptance: all baseline tests pass and every pre-existing change is either
committed in a scoped change or explicitly excluded.

### S1 — freeze the production pipeline

1. Integrate `factored` and `flat` construction modes in the Java pipeline.
   Pure BGPs use deterministic min-scope variable elimination; unsupported
   operators report an explicit flat fallback.  Intermediate message rows are
   session-scoped and cleaned on success and failure.
2. Integrate `compiler.compile_many(circuit, roots, mode)` using CUDD.  Validate
   constants, complemented edges, MINUS roots, shared subgraphs, empty root
   maps, deep DAGs, deterministic ordering, and batch WMC.  The Python ROBDD is
   invoked only through an explicit oracle flag.
3. Make every live correctness harness understand nested factored gates, and
   retain a separate flat-ablation regression.
4. Update the R9 construction harness so a factored multi-pass plan exports
   step metadata, feeds back only private message triples, includes feedback and
   cleanup in the measured construction boundary, and saves one canonical
   circuit per manifest instance in external scratch storage.

Acceptance: Java package/tests, `quick_verify.py`, `verify_all.py`,
`verify_compiler.py` with native CUDD, semantic gallery, engine-native/path
checks, R9 harness tests, and `git diff --check` all pass.

### S2 — correctness and robustness freeze

- Differentially test factored versus flat BGPs on generated sparse graphs,
  constants/repeated variables/self-joins, zero and multiple matches, projected
  and existential variables, and term kinds.  For enumerable cases require
  factored = flat = possible-world enumeration for every answer root.
- Differentially test CUDD shared/per-root versus the ROBDD/PWE oracle with
  random DAGs containing PLUS, TIMES, MINUS, constants, duplicate roots, and
  non-uniform probabilities.  Include probabilities near 0 and 1 and report
  maximum absolute/relative error.
- Exercise cleanup after injected failures, concurrent session isolation,
  deterministic circuit hashes, deep non-recursive traversal, timeouts, and
  memory metrics.
- Freeze `workload_manifest.csv`, `path_manifest.csv`, environment metadata,
  probability seeds, and external cache layout.  Write the frozen commit into
  every subsequent row.

Acceptance: zero semantic mismatches; numerical error within the declared
tolerance; all manifests and seeds committed.

### S3 — controlled mechanism experiments

Run before the large stores so regressions are cheap to diagnose:

| experiment | independent variables | primary outcomes |
|---|---|---|
| construction | factored/flat × star/layered/chain/cycle × size | passes, gates, edges, bytes, build time, peak RSS, probability parity |
| compilation granularity | shared/per-root × answer count × sharing depth | compile/WMC time, unique/summed nodes, memory, sharing savings |
| ordering | fixed deterministic vs CUDD reordering on the same circuits | compile time, nodes, peak memory; select and freeze the production default |
| treewidth | bounded-width and growing-width families | CUDD and d4 time/size, explicit 120 s walls |
| numerical stability | uniform/non-uniform/extreme weights × circuit depth | absolute/relative error versus high-precision/enumerable oracle |

Use at least five timed repetitions for tractable cells, retain all timeouts,
and store raw samples.  These experiments establish why factoring and shared
multi-root compilation help; null and counterexample regimes must remain in the
plots.

### S4 — WatDiv construction, sharing, compilation, and end-to-end PQE

The frozen performance manifest contains 30 instances per scale:
`L=5, S=7, F=5, C=3, O=5, M=5`.

1. Provision paired base/reified stores from the same logical facts.  Run one
   engine at a time to avoid memory interference.
2. Run GraphDB 10M first and validate B/R answer-multiset and N/C candidate-key
   parity.  Then run GraphDB 100M, followed by Oxigraph, QLever, and
   MillenniumDB at each available scale.  Capability failures remain rows.
3. For every method cell record B, R, N-clean, and C raw engine medians plus raw
   samples; C additionally records all-pass parse/feedback/cleanup time,
   requested/effective construction mode, gates, edges, bytes, and circuit hash.
4. Derive same-unit NPCS/circuit sharing measurements from the captured N/C
   responses.  Never compare bytes with graph elements.
5. Compile one canonical circuit per manifest instance, not once per engine.
   Run production CUDD shared/per-root; sample d4 as the independent d-DNNF
   baseline.  Reuse the same compiled results in the end-to-end table.

Nominal size: `4 engines × 2 scales × 30 queries × 4 methods = 960` cells.
Unavailable paired stores are provisioned before their batch; genuinely
unsupported engines/scales are recorded rather than silently removed.

### S5 — external validity and baselines

- Normalize the 13-shape semantic gallery across GraphDB, Oxigraph, QLever, and
  MillenniumDB and require byte identity for supported cells.
- Fix POST/chunked transport for the writable property-path protocol, expand the
  path manifest, and run semantic breadth plus reachable-size/density/cycle
  scaling.  Read-only engine cells are explicit `N/A`.
- Run Wikidata P279/P131 paths on the existing subgraph and the supported
  full-Wikidata query subset.  Record the IRI-frontier and compound-path limits.
- Re-run the matched TPC-H Q3/Qrecon subset against ProvSQL with forced result
  evaluation and the pinned compiler protocol.  Reuse other current TPC-H rows
  only when their code/data/protocol hashes still match the frozen manifest.
- Run the built official SPARQLprov rewriter/size checks.  Run its endpoint
  baseline and official NPCS only if the required artifacts/stores can be
  pinned; otherwise publish explicit `N/A` and retain the correctly labelled
  clean-room NPCS implementation.  Do not fabricate cross-domain ProvSQL bars.
- Run d4-v2 with
  `-i {cnf} -m ddnnf-compiler --dump-ddnnf {out}` and pin the binary SHA-256.

Acceptance: every claimed baseline has an identifiable artifact/version and
comparable workload; absent official assets are reported as limitations.

### S6 — audit and resource/sensitivity analysis

Check that every row maps to a manifest hash and frozen commit, all successful
cells have the required run count, B/R and N/C parity evidence is present,
compiler time is not duplicated across identical engine circuits, and failure
rows survive aggregation.  Summarize peak RSS, disk/index size, reification
expansion, response bytes, and CUDD/d4 resource limits.  Repeat probability
sensitivity with fixed circuits so construction is not re-timed.

Acceptance: the audit command exits zero and produces a machine-readable list
of included/excluded rows with reasons.

### S7 — aggregate and visualize (last)

Only audit-approved CSVs feed figures.  Generate:

1. B/R/N/C raw or signed-overhead small multiples by engine and scale;
2. factored-versus-flat scaling with parity and null regimes;
3. shared-versus-per-root CUDD time, nodes, and memory;
4. real-pattern CUDD/d4 compile time and compiled size;
5. treewidth-controlled scaling with timeout walls;
6. end-to-end construction/compile/WMC decomposition;
7. sharing boundary in bytes/bytes and elements/elements;
8. path breadth/scaling and a separate validation matrix;
9. baseline capability and artifact-status tables.

Figures include raw-data paths, frozen commit, sample counts, uncertainty, and
timeouts.  Finish with a clean rebuild, all verification suites, figure
regeneration from scratch, scoped result/figure commits, pull/rebase, and push.

## Expected wall clock and resource policy

The final campaign is multi-day.  The 960-cell WatDiv matrix has an 80-hour
sequential timeout ceiling before store provisioning; realistic end-to-end wall
clock including 100M indexes, compilation, paths, and baselines is roughly
7--14 days on the current host.  Run one large store at a time, checkpoint after
every cell, commit after every completed engine/scale, and never wait until the
whole campaign finishes to preserve valid partial results.
