# WatDiv 10M B/R/N/C and PQE Experiment

This document summarizes the infrastructure available for the planned WatDiv
10M evaluation and the integration work that remains. It describes an
experiment in preparation; it does not report benchmark results.

## Experimental scope

The experiment compares provenance representations and construction strategies
on one deduplicated WatDiv 10M dataset. It addresses four questions:

1. How much time, memory, and data movement are added by the B, R, N, and C
   stages?
2. How do flat and factored circuit construction differ in construction cost,
   intermediate state, final circuit size, and PQE cost?
3. How much sharing can NPCS recover after the endpoint has produced and
   transmitted per-answer provenance strings, and how does that compare with
   sharing created directly during circuit construction?
4. How does the dedicated property-path circuit plan behave on queries with
   repeated reachability structure?

Because the dataset size is fixed, the results describe method behavior at
10M triples rather than scale-factor trends. A separate experiment would be
needed for claims about asymptotic scaling.

## Method variants

| Variant | Input | Operation | Primary output |
|---|---|---|---|
| B | Deduplicated WatDiv RDF | Original SELECT query | Answer-binding multiset |
| R | RDF-star occurrence graph | Triple-pattern rewrite without provenance aggregation | Answer-binding multiset |
| N | RDF-star occurrence graph | `NpcsRewriter` with per-answer provenance-string aggregation | Answer bindings and provenance strings |
| C-flat | RDF-star occurrence graph | Flat circuit construction | One shared RDF circuit |
| C-factored | RDF-star occurrence graph | Factored circuit construction | One shared RDF circuit |
| C-path | RDF-star occurrence graph | Iterative property-path construction | One shared path circuit |

The NPCS extensions begin only after N has returned a complete response. They
are reported separately from the N endpoint method:

| Variant | Starting artifact | Operation | Current status |
|---|---|---|---|
| NPCS+PP-HC | Complete N response | Parse, Boolean-normalize, and hash-cons all answer roots into one query-global DAG | Implemented |
| NPCS+PP-HC+F | Hash-consed DAG | Apply a frozen, semantics-preserving algebraic factorization | Not implemented |
| NPCS+PP-HC+PQE | Hash-consed DAG | Compile all roots in one manager and run WMC | Implemented |
| NPCS+PP-HC+F+PQE | Factorized DAG | Compile all roots in one manager and run WMC | Conditional on the factorization track |

This naming keeps the sources of sharing distinct. `NPCS+PP-HC` recovers only
exactly equal subexpressions; any benefit from algebraic factorization belongs
to the separate `NPCS+PP-HC+F` result.

The non-path workload includes B, R, N, C-flat, and C-factored. The property-
path workload includes B and C-path because the current R and N rewriters do
not accept the required path algebra nodes. Unsupported combinations remain
capability results rather than timing observations.

## Workload organization

The new evaluation uses a separate, versioned workload rather than replacing
the existing regression and reproduction assets.

| Asset | Role in the evaluation | Rationale |
|---|---|---|
| `engine/src/test`, `reference/tests.py`, and `reference/quick_verify.py` | Semantic regression gates | They cover query rewriting, circuit semantics, and the Maven-to-WMC path independently of the performance protocol. |
| `reference/paper/queries/watdiv/{10M,100M}` | Historical frozen workloads | Most templates have one `instance=00`; existing result artifacts still refer to these files. |
| Existing workload manifests and result CSV files | Reproduction records | Their query identities and measurement conventions belong to earlier experiments. |
| `reference/paper/watdiv10m_runner.py` | Single-cell implementation | It records the current timing boundaries, timeout behavior, circuit artifacts, and offline stages used by this protocol. |
| New queries, metrics, and results | A separate 281-query batch | Versioned storage keeps query identities, repetition counts, and measurement definitions unambiguous. |

## Engines and execution matrix

The main evaluation fixes GraphDB 10.7.6, Apache Jena Fuseki 5.4.0, and
Oxigraph 0.4.11. These versions retain the RDF-star behavior expected by the
occurrence encoding. A common smoke suite covers quoted-triple lookup, token
bindings, update and cleanup behavior, and circuit equivalence before timing
begins.

A lab-local 1+1 toy preflight has exercised B, R, N, C-flat, C-factored, and
C-path against all three pinned engine versions. It also checked direct
candidate-answer equality, private-state cleanup, and the required structured C
records. Compute-node tests also cover fresh-cell skipping and offline recovery
from saved response and circuit artifacts. This is compatibility evidence only;
its timings are not benchmark results. The external Slurm wrappers and their
output remain outside the repository.

Each executable `query × engine × variant` combination forms one cell. The
formal run selects instances `00` through `02` and contains one warm-up
execution followed by one measured execution. Every endpoint execution has
its own 600-second deadline. For C, that deadline
covers the complete construction plan, including all CONSTRUCT requests and
feedback steps, rather than resetting for each step.

The matrix contains:

- `75 × 3 × 5 = 1,125` non-path cells;
- `10 × 3 × 2 = 60` property-path cells;
- `1,185` cells and `2,370` endpoint executions after the two executions per
  cell are included. `P-plus-all` has one fixed query, so the selected workload
  contains 85 rather than 87 distinct queries.

The measured value remains available as a raw observation and is the primary
result for a successful cell. No within-cell dispersion statistic is claimed
from a single measurement. Warm-up values are stored separately. Timeout,
memory exhaustion, unsupported features, correctness mismatches, and
infrastructure failures retain distinct status codes.

## Measurement boundaries

Endpoint timing uses client-observed boundaries that are available across all
three engines. B, R, and N record request duration, time to first byte, response
drain time, response bytes, decoding, canonicalization, and artifact storage.
C records the complete `CircuitRun` wall interval, its reported construction
interval, requested and effective mode, plan size, path-round information,
final circuit bytes, circuit decoding, answer-reachable structure, and PQE.
With `CIRCUIT_STRUCTURED_TIMING=1`, `CircuitRun` also emits versioned JSON stage
records. The cell runner enables this mode, validates the records against the
logged plan, and stores them as `c-stages.jsonl`; it does not infer step times
from human-readable log text. A C run without the required records fails the
measurement protocol.

The C records separate query reading, plan generation, repository setup, data
readiness, normalization, circuit serialization, optional named-graph
persistence, and endpoint cleanup. Every ordinary construction step reports
the endpoint query, result splitting, workspace registration, feedback update,
client merge, and step wall intervals, together with emitted, circuit, message,
and workspace triple counts. Closure plans report their top-level wall interval
and each nested path CONSTRUCT; dedicated property paths additionally report
source discovery, reachable-subgraph BFS rounds, fixpoint CONSTRUCTs, and
per-source completion. Workspace cleanup has its own duration and batch count.
The maximum recorded workspace and circuit counts provide structural peaks;
per-step server RSS remains the responsibility of the external cgroup sampler.
Steps in the same parallel schedule level overlap, so their durations are raw
observations and are never summed to reconstruct wall time. Structured logging
overhead is measured and retained in the construction-completion record.

`N_request_ms` spans the HTTP request through receipt of the final response
byte. It combines query processing, server-side serialization, transport, and
client receipt; the common endpoint interface cannot separate those activities
into mutually exclusive intervals. Time to first byte and response drain time
are supplementary observations, not estimates of pure engine and network time.
`N_endpoint_e2e_ms` is a direct outer-runner wall measurement from rewrite
start through atomic persistence of the complete raw response. If response
bytes are persisted while they arrive, that work is already inside this wall
interval and is not added a second time.

The post-processor reports only offline measurements from the saved response:

- `pp_hc_total_ms` is the sum of response-file read, JSON decode, binding
  extraction and ordering, raw-provenance persistence, parsing, Boolean
  normalization, global hash-consing, DAG validation, and DAG-artifact
  persistence;
- `pp_hc_build_wall_ms` directly measures the same offline pipeline from the
  start of response-file reading through persistence of the DAG artifacts;
- `compiler.pqe_total_ms` is the sum of probability loading, shared compilation,
  all-root WMC, variable-order persistence, and probability-result persistence;
- `compiler.pqe_wall_ms` directly measures that PQE pipeline.

The outer runner derives full method totals from consecutive, non-overlapping
wall intervals:

```text
NPCS_PP_HC_build_e2e_ms = N_endpoint_e2e_ms + pp_hc_build_wall_ms
NPCS_PP_HC_PQE_e2e_ms   = NPCS_PP_HC_build_e2e_ms + compiler.pqe_wall_ms
```

The factorization variants add a separately measured factorization interval
between HC and PQE. The post-processor does not emit a field named full
end-to-end because it has no access to the preceding endpoint interval. C uses
the same boundary principle: its build total includes plan generation, every
construction and feedback request, required client-side RDF work, cleanup, and
final circuit persistence; circuit decode, validation, shared compilation, WMC,
and result persistence remain separately observable offline stages.

Each paired offline NPCS or C pipeline has its own query-level deadline,
independent of the endpoint's 600-second budget. Component timers separate
decode, normalization, structural interning, compilation, WMC, and persistence
within that pipeline. Process-local CPU and peak RSS are recorded by the
runner. Remote-engine RSS, cgroup totals, temporary-store peaks, and Slurm job
figures are attached by the cluster deployment wrapper.

Structural measurements use the same node-plus-edge convention throughout:

- raw NPCS occurrence trees before normalization;
- normalized trees before global interning;
- the query-global hash-consed DAG;
- an optional factorized DAG;
- answer-reachable C-flat, C-factored, and C-path circuits;
- compiled graph sizes for the shared multi-root manager.

## Correctness criteria

B and R are compared as term-aware answer-binding multisets. N and C are first
compared after removing their provenance representation. C-flat and C-factored
then undergo per-answer Boolean-function comparison under one variable order.
The same comparison relates the NPCS hash-consed roots to the corresponding C
roots. PQE values use an absolute tolerance of `1e-12` plus a relative tolerance
of `1e-9`; canonical Boolean-function equality remains the primary semantic
criterion.

Cross-engine and repeated-run checks compare complete canonical records rather
than filenames or digests. Mismatched cells remain in the failure report but do
not contribute to performance summaries for semantically correct executions.

## Execution environment and artifacts

All preparation, compatibility, pilot, endpoint, and PQE jobs run on Slurm
compute nodes. A cell starts from the same read-only store snapshot and uses a
private node-local workspace. Engine concurrency, CPU affinity, memory limits,
JVM or Rust settings, locale, and software versions are fixed in the batch
manifest.

Run records include the repository commit and dirty state, dataset and query
identifiers, engine and method versions, warm-up or measured index, timing and
resource fields, status, exit code, correctness evidence, and Slurm job
identifiers. Artifacts are written through partial files, flushed to storage,
validated, and then renamed atomically. Archival copies from node-local storage
to NFS remain outside method timing.

The experiment harness does not add SHA-256 checksums or other file digests.
The SHA-256 expressions used internally by SPARQLcirc to construct gate IRIs
are part of method C and remain included in its measured cost.

## Implemented components

### Workload snapshot and audit

`reference/paper/watdiv10m_workload.py` creates a new workload directory and
leaves existing batches unchanged. The generated workload contains:

- ten instances of each official WatDiv 0.6 template: `L1`–`L5`, `S1`–`S7`,
  `F1`–`F5`, and `C1`–`C3`;
- ten instances of each reconstructed OPTIONAL extension, `O1`–`O5`;
- ten source-bound instances of each property-path extension, `P-plus`,
  `P-star`, and `P-alt`;
- one all-pairs instance of `P-plus-all`.

The resulting batch contains 250 non-path query instances and 31 path query
instances. Its audit checks the complete query-id set, per-template counts,
file presence, byte counts, line counts, and exact agreement with the frozen
WatDiv emission transcript. WatDiv may repeat a sampled binding, and templates
without placeholders necessarily emit identical query text; those instances
are retained in emission order rather than replaced by a uniqueness filter.
The WatDiv model, `saved.txt`, template files, and path-source table are copied
into the batch.
The generator also verifies that WatDiv did not change `saved.txt` during query
instantiation. Completed snapshots are made read-only. No experiment-level
file digest is computed.

The `O1`–`O5` files are repository reconstructions rather than members of the
official 20-template WatDiv workload. The `P-*` files are repository-specific
property-path extensions. These classifications are stored in the workload
metadata and remain separate in result tables.

### NPCS post-processing and shared PQE

`reference/npcs_postprocess.py` converts a complete NPCS SPARQL Results JSON
response into a query-level, multi-root Boolean DAG. Its current pipeline is:

1. The response layer validates the SPARQL JSON structure and identifies the
   provenance variable.
2. Term-aware canonical keys preserve the RDF type and lexical details of every
   answer binding.
3. Each complete provenance polynomial is persisted as JSONL before expression
   processing begins.
4. The parser accepts the concrete `Prov.java` syntax, including `⊕`, `⊗`,
   `⊖`, trailing commas, empty monus right operands, and whitespace-separated
   aggregation terms.
5. Boolean translation represents `⊕` as OR, `⊗` as AND, and `a ⊖ b` as
   `a AND NOT b`.
6. Normalization covers constants, associativity, commutativity, idempotence,
   and double negation.
7. Each answer first produces an occurrence tree; exact structural interning
   then spans every answer in the query.
8. The resulting multi-root DAG and per-answer metrics are validated and
   persisted atomically.
9. Optional PQE compiles all roots in one manager and evaluates them with the
   Python oracle or the production CUDD backend.

The metrics distinguish raw tree size, normalized forest size, and globally
shared DAG size. They include nodes, edges, token occurrences, serialized
bytes, per-answer distributions, and parse, normalization, interning,
compilation, and WMC times. Variable order is persisted as plain text. Exact
nested tuples provide structural identity, so this path does not compute an
additional content digest.

The CLI starts from a fully downloaded response file and currently decodes the
complete JSON document in memory. Endpoint timing, raw-response capture, and
process deadlines are supplied by the outer experiment runner; cgroup-level
resource sampling remains a deployment concern. Its timing scope is recorded
as `offline_from_complete_response_file`; `response_read_ms` and
`pp_hc_build_wall_ms` are top-level fields, while `probability_load_ms`,
`pqe_total_ms`, and `pqe_wall_ms` appear in the nested `compiler` record. These
fields make the local boundaries explicit without claiming access to endpoint-
internal timings.

Hash-consing only merges structurally equal subexpressions. Algebraic
factorization is outside the current implementation; its metrics use
`factor_status: "not_implemented"` and `factor_ms: null`.

### Single-cell runner and C-side processing

`reference/circuit_io.py` reads both the current native circuit encoding and
the earlier explicit encoding. `reference/paper/watdiv10m_runner.py` executes
one `query x engine x method` cell. It provides the following boundaries:

- one new artifact directory for every warm-up or measured execution;
- an independent process-group deadline for every endpoint execution and a
  separate deadline for its paired offline pipeline;
- streamed SPARQL Results JSON persistence with request, first-byte, drain,
  response-size, and atomic-finalization measurements for B, R, and N;
- exactly one NPCS post-processing invocation for each saved N response;
- strict `C-flat`, `C-factored`, and dedicated `C-path` execution through
  `CircuitRun`, with requested/effective mode validation and no silent fallback;
- durable circuits, term-aware answer records, answer-reachable `|V|+|E|`, and
  optional shared CUDD or oracle PQE for C;
- direct, complete-content comparison of measured answer records and repeated
  C circuit artifacts, without experiment-level digests;
- validated per-step C timing and structural counts in `c-stages.jsonl`;
- immutable offline/PQE retries from a saved response or circuit through the
  `resume-offline` command;
- raw observations plus median, mean, standard deviation, extrema, and IQR for
  the measured executions.

The runner stops after an endpoint failure or a repeated-run correctness
mismatch because the endpoint may contain partial private state. An offline or
PQE failure does not mutate the engine, so later endpoint executions continue
and retain their immutable inputs. `resume-offline --cell CELL` creates an
`offline-resume-NNN` directory and reruns only failed or missing offline stages;
the original run records are unchanged. It then recomputes direct answer parity,
C circuit parity, and the measured summaries from the effective artifacts. It
does not resume an endpoint request or a CONSTRUCT in flight.

The lab-local launcher uses this boundary to skip completed cells, retry
offline work from saved artifacts, or restore a clean engine and start a new
immutable cell attempt after an endpoint failure. Slurm submission, engine
lifecycle commands, and cgroup sampling remain outside the repository.

## Remaining integration work

The timed evaluation still depends on the following components:

- an objectively selected ten-source path table derived from the exact
  deduplicated WatDiv 10M `friendOf` graph;
- one real WatDiv 0.6 generation run on a compute node, followed by audit and
  archival of the 281 concrete queries;
- completion of the lab-local Slurm manifests, engine reset commands, resource
  sampling, and result merging around the external cell/offline resume helper;
- parser differential tests using responses from all three engines, followed
  by large-answer, truncated-response, timeout, memory, storage, and recovery
  tests;
- a pilot that confirms cluster capacity and wall-time assumptions.

## Validation and example invocations

The repository-level checks for the implemented components are:

```bash
mvn -q -f engine/pom.xml package
python reference/tests.py
python reference/quick_verify.py
python reference/paper/test_npcs_postprocess.py
python reference/paper/test_watdiv10m_workload.py
python reference/paper/test_watdiv10m_runner.py
```

Failed offline/PQE stages of an otherwise preserved cell can be retried with:

```bash
python reference/paper/watdiv10m_runner.py resume-offline \
  --cell /node-local/cells/graphdb/C-factored/L1-00
```

The command exits successfully only when every configured endpoint artifact and
effective offline result is present and measured-run answer/circuit parity
holds. Missing endpoint executions require a fresh cell attempt after restoring
the engine.

A workload snapshot is generated and audited with:

```bash
python reference/paper/watdiv10m_workload.py generate \
  --watdiv /absolute/path/to/run_watdiv06.sh \
  --model /absolute/path/to/watdiv-0.6/model/wsdbm-data-model.txt \
  --state /absolute/path/to/matching/saved.txt \
  --official-testsuite /absolute/path/to/watdiv-0.6/testsuite \
  --path-sources /absolute/path/to/path-sources.tsv \
  --dataset-id watdiv-10m-deduplicated-v1 \
  --out /absolute/read-only/batches/watdiv10m-formal-v1

python reference/paper/watdiv10m_workload.py audit \
  /absolute/read-only/batches/watdiv10m-formal-v1
```

A single B cell can then be run as follows:

```bash
python reference/paper/watdiv10m_runner.py \
  --query /absolute/batch/queries/L1-00.rq \
  --query-id L1-00 \
  --engine graphdb-10.7.6 \
  --method B \
  --base-endpoint http://127.0.0.1:7200/repositories/watdiv-base \
  --out /node-local/cells/graphdb/B/L1-00
```

For `N`, add the mixed asserted-plus-token endpoint and JAR, select `--pqe-backend cudd`, and
provide either `--uniform-probability 0.5` or a probability file. For C, use
`C-flat`, `C-factored`, or `C-path` and add the corresponding mixed file as
`--reified-data`; factored and path
cells also require `--update-endpoint`. The defaults are one warm-up, one
measured execution, the `SPARQL_Star` occurrence scheme, and independent
600-second endpoint and offline deadlines. Under the default scheme, every
fact is present once as an asserted triple and once as an RDF-star occurrence
record. `--scheme Standard` instead expects the asserted triple plus the three
standard-reification records. The formal runner does not mix these layouts or
silently fall back to the historical token-only stores.

The path-source input contains ten distinct source rows with this schema:

```text
source_id	iri	stratum	reachable_count	max_hops	selection_method	selection_seed
```

For an NPCS response already captured by the endpoint runner, post-processing
and PQE are invoked with:

```bash
python reference/npcs_postprocess.py raw-response.json \
  --out /node-local/run-artifacts/L1-00/graphdb/measured-01/pp \
  --query-id L1-00 \
  --run-id measured-01 \
  --engine graphdb-10.7.6 \
  --token-regex '^urn:t:[0-9]+$' \
  --backend cudd \
  --uniform-probability 0.5
```

The outer runner supplies the independent offline-stage deadline and records
CPU time, peak RSS, scratch-space usage, and exit status. The process-local RSS
reported by the post-processor complements rather than replaces Slurm or
cgroup measurements.
