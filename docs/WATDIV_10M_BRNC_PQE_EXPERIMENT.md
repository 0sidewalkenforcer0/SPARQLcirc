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
| `reference/paper/paper_construction_matrix.py` | Reusable implementation source | Its rewrite and execution code remains useful, while its timing and timeout model differs from the new protocol. |
| New queries, metrics, and results | A separate 281-query batch | Versioned storage keeps query identities, repetition counts, and measurement definitions unambiguous. |

## Engines and execution matrix

The main evaluation fixes GraphDB 10.7.6, Apache Jena Fuseki 5.4.0, and
Oxigraph 0.4.11. These versions retain the RDF-star behavior expected by the
occurrence encoding. A common smoke suite covers quoted-triple lookup, token
bindings, update and cleanup behavior, and circuit equivalence before timing
begins.

Each executable `query × engine × variant` combination forms one cell. A cell
contains one warm-up execution followed by five measured executions. Every
endpoint execution has its own 1,200-second deadline. For C, that deadline
covers the complete construction plan, including all CONSTRUCT requests and
feedback steps, rather than resetting for each step.

The matrix contains:

- `250 × 3 × 5 = 3,750` non-path cells;
- `31 × 3 × 2 = 186` property-path cells;
- `3,936` cells and `23,616` endpoint executions after the six executions per
  cell are included.

The five measured values remain available as raw observations. The primary
summary for a successful cell is the median, accompanied by the mean, standard
deviation, minimum, maximum, and interquartile range. Warm-up values are stored
separately. Timeout, memory exhaustion, unsupported features, correctness
mismatches, and infrastructure failures retain distinct status codes.

## Measurement boundaries

Endpoint timing uses client-observed boundaries that are available across all
three engines. B, R, and N record request duration, time to first byte, response
drain time, response bytes, decoding, canonicalization, and artifact storage.
C records plan generation, each construction or feedback step, client-side RDF
processing, merge, cleanup, and final circuit storage. Both method-core and
end-to-end totals are reported.

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

The offline NPCS and C pipelines use separate query-level deadlines for decode,
normalization, structural interning, optional factorization, compilation, and
WMC. These deadlines are independent of the endpoint's 1,200-second budget.
The resource record combines wall time, CPU time, client and engine peak RSS,
temporary storage, endpoint workspace size, serialized bytes, and Slurm/cgroup
measurements.

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

The resulting batch contains 250 non-path queries and 31 path queries. Its
audit checks the complete query-id set, per-template counts, file presence,
concrete query uniqueness, byte counts, and line counts. The WatDiv model,
`saved.txt`, template files, and path-source table are copied into the batch.
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
complete JSON document in memory. Endpoint timing, raw-response capture,
process deadlines, and cgroup-level resource sampling therefore remain
responsibilities of the outer experiment runner. Its timing scope is recorded
as `offline_from_complete_response_file`; `response_read_ms` and
`pp_hc_build_wall_ms` are top-level fields, while `probability_load_ms`,
`pqe_total_ms`, and `pqe_wall_ms` appear in the nested `compiler` record. These
fields make the local boundaries explicit without claiming access to endpoint-
internal timings.

Hash-consing only merges structurally equal subexpressions. Algebraic
factorization is outside the current implementation; its metrics use
`factor_status: "not_implemented"` and `factor_ms: null`.

### Existing circuit reader and PQE support

`reference/circuit_io.py` reads both the current native circuit encoding and
the earlier explicit encoding. `reference/pqe.py` already provides shared
multi-root compilation and WMC. The remaining C-side work is experiment-runner
integration: durable artifact storage, answer-reachable structural counts,
stage-level timing, and resource measurements.

## Remaining integration work

The timed evaluation still depends on the following components:

- an objectively selected ten-source path table derived from the exact
  deduplicated WatDiv 10M `friendOf` graph;
- one real WatDiv 0.6 generation run on a compute node, followed by audit and
  archival of the 281 concrete queries;
- a B/R/N/C endpoint runner with one warm-up and five measured executions per
  cell, with an independent 1,200-second deadline for every execution;
- structured C metrics for requested and effective construction mode,
  fallback reason, plan steps, feedback, cleanup, and workspace peaks;
- HTTP measurements for N covering request duration, time to first byte,
  response drain time, and response bytes;
- C-side integration with `circuit_io.py` and `pqe.py`, including durable
  circuit artifacts and answer-reachable size measurements;
- a common occurrence-encoding smoke suite for GraphDB 10.7.6, Fuseki 5.4.0,
  and Oxigraph 0.4.11;
- Slurm jobs for preparation, compatibility checks, pilot runs, endpoint
  cells, PQE, resource sampling, and result merging;
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
```

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
