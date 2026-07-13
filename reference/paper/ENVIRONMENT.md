# VLDB frozen-batch environment contract

This file defines the environment that must be frozen before producing citable VLDB numbers.  It also
records the audit performed on **2026-07-13**.  The audit is an inventory, not a claim that the current
working tree or every service is ready: the repository and d4v2 source checkout were dirty, and official
NPCS was absent.  The selected production Python does contain native CUDD, the pinned d4v2 executable is
available, and GraphDB query/update were live-tested from the main execution namespace.  No commit or store
sentinel from this audit is nevertheless declared the final frozen batch.

The machine-readable record is produced by `capture_environment.py`.  That record, stored next to the raw
results, is authoritative for the eventual Git commit, binary hashes, runtime values, and endpoint probe.
This document is the human-readable contract and explains what a directory-presence check cannot prove.

## Freeze gate

A performance batch is publishable only if all applicable items below pass before the first warm-up and
remain unchanged through the last measured run:

1. The SPARQLcirc worktree is clean; its 40-character Git commit is recorded.
2. Python, `dd`, native `dd.cudd`, Java, Maven, GraphDB, and every external compiler/baseline used by the
   cell have an exact version, source revision, or SHA-256 in `environment.json`.
3. `<production-python> reference/verify_compiler.py` passes in the same Python environment used by the
   batch.  Merely installing pure-Python `dd` is not sufficient: `dd.cudd` must import and be exercised.
4. Every input file is non-empty and has a separately frozen checksum manifest.  Every database endpoint
   responds from the same network namespace as its harness and passes an expected-count/sentinel-query
   check.  A store directory alone is not evidence that the right dataset or schema is loaded.
5. The GraphDB edition, heap, repository ID, one-worker/core policy, JVM, warm-cache policy, CPU affinity,
   and thread-control variables are fixed.  They must not change between competing methods in a cell.
6. The canonical protocol is one unreported warm-up followed by five measured runs, reporting median,
   minimum, maximum, mean, and standard deviation.  Query-side cells have one **300 s** budget and each
   compile attempt has a **120 s** budget, as defined in `reference/experiment_timeouts.py`.
7. The batch runs in a quiescent allocation with no simultaneous large load/index/compile jobs.  Capture
   the volatile fields before and after each group.  If the host is shared, disclose that fact and treat
   absolute wall-clock values as non-isolated rather than silently mixing them with dedicated runs.

If a gate is inapplicable, the result must say why; it must not silently turn into zero, timeout, or an
alternative implementation.

## Audited host and resource envelope

| field | 2026-07-13 audit | frozen-batch requirement |
|---|---:|---|
| OS | AlmaLinux 9.7 | Record distribution, kernel, architecture |
| kernel / architecture | 5.14.0-611.54.6.el9_7.x86_64 / x86_64 | Keep fixed within a timing comparison |
| CPU | AMD EPYC 7302 16-Core Processor | Record exact model |
| topology | 1 socket, 16 physical cores, 2 threads/core, 32 logical CPUs | Record visible and allowed CPUs |
| affinity at audit | logical CPUs 0–31 | Pin explicitly for compiler comparisons; do not infer isolation from visibility |
| NUMA / L3 | 1 NUMA node / 128 MiB L3 | Record if the execution host changes |
| physical RAM | 134,385,029,120 bytes (about 125.2 GiB) | Record `MemTotal`; also capture free memory as volatile data |
| swap | 51,539,603,456 bytes (48 GiB) | Keep swap policy fixed; flag any swapping during a run |
| open-file limit | 524,288 soft and hard | Record with each new allocation |
| stack limit | 8 MiB soft; hard unlimited | Record with each new allocation |
| CPU/address-space limits | unlimited in the audit process | Record cgroup and rlimit values, not just host capacity |
| cgroup | v2; effective cpuset 0–31; quota controllers not exposed in this sandbox view | A missing controller file means unknown, not unlimited |
| repository filesystem | NFS | Do not put compiler scratch or per-run temporary CNFs on NFS |
| scratch | local `/tmp` on the root volume | Check free bytes before a batch; retain only manifest/hash-backed artifacts |

The collector's child sandbox had workspace-write access only to the repository and temporary space,
read-only access elsewhere, and disabled network visibility.  Separately, the main execution namespace
successfully exercised GraphDB's localhost query and update endpoints on port 7200.  That establishes live
query/update service availability at audit time, but not that every named repository contains the frozen
dataset; final endpoint and sentinel probes must still run in the measurement harness's namespace.

Historical G4 timing used a shared management/login node with warm repositories and other users' processes.
GraphDB used `-Xms60g -Xmx60g` and was observed at roughly one busy core.  Those measurements are useful
historical context, but the new frozen batch must not describe that host as dedicated.  The Wikidata load
previously needed a larger operational heap (80 GiB); loading is not a reported query cell, and its heap
must not be mixed into a 60 GiB query-timing cell without disclosure.

## Toolchain and compiler inventory

Paths are intentionally represented as `<repo>`, `<tools-root>`, `<data-root>`, `<graphdb-home>`, and
`<artifact-root>`.  Absolute user paths are inputs to the collector and are never serialized.

| component | audit value | state for frozen batch |
|---|---|---|
| Python | CPython 3.11.15 from the explicit production interpreter | Available; collector probes this interpreter rather than its own process |
| production package | `dd==0.6.0` in `reference/requirements-production.txt` | Installed and importable in production Python |
| native CUDD | `dd.cudd`, CUDD 3.0.0 | Installed and importable; still run `verify_compiler.py` before the frozen batch |
| Java | Temurin OpenJDK 21.0.8+9 LTS | Available through an explicit `<java-bin>`; record runtime build |
| Maven | Apache Maven 3.9.11 | Available through an explicit `<maven-bin>` and the audited Java home |
| GraphDB | 10.7.6 Free/Lite | Distribution present; localhost query and update live-test passed in the main namespace |
| GraphDB worker policy | experiment declaration: one worker/core for the reported Free/Lite cells | Record it as this campaign's configuration, not a product-wide guarantee or a claim that the 32-CPU host is isolated |
| d4v2 source | Git `15eff31962466804a48374826b9e5a746fc2766e` | Checkout present but dirty (three entries); source rebuild is not freeze-eligible |
| pinned d4v2 executable | `scripts/d4_static`, SHA-256 `9b2ca0a3969ea61d159e1cc5ace20f675346a83cc75fbd9dc7c902d8597bbad5` | **Available and executable; this is the d4v2 binary used by the harness** |
| d4 v1 executable | SHA-256 `e213179d82510be56b2606d8b27c862bf12e010921fc81fabdc6b29b03b6cba1` | Present; bare audit shell lacks `libgmpxx.so.4`, so pin its runtime library environment too |
| ProvSQL source | Git `48decaf2d4f8f203ae3d4b9ce3c2e9aa0982a424` | Clean checkout present; service not observable in sandbox |
| SPARQLprov | official 2021 release artifact | Built `rewrite` present, SHA-256 `78d869efc262d9cc67b657212d69ed30b6c5b1ea90ec37e0b9a7b0ab0d50fa55` |
| SPARQLprov tests | `SPMPolynomialTest` | Present, SHA-256 `701de8107cc72301912322f638b0f9baf931117dce8e925a97aa323414d51d20`; documented 9/9 pass |
| official NPCS | author-provided jar | **Missing; `NPCS_ORIG_JAR` unset** |

The dirty d4v2 source tree and the pinned executable are separate facts.  Rebuilding the current checkout
remains blocked by its absent KaHyPar tree and proprietary `3rdParty/patoh/libpatoh.a`, but that does not
make the already-built, hash-pinned `scripts/d4_static` unavailable.  Invoke that binary exactly as:

```text
d4_static -i {cnf} -m ddnnf-compiler --dump-ddnnf {out}
```

The final Level-1 comparison must register this same SHA-256 on both sides and retain the invocation in its
artifact.  A source revision alone, or a different bundled d4 executable, is not an equivalent identity.

The SPARQLprov tree came from a release archive rather than a Git checkout.  Its two built binary hashes are
recorded above, but the original source archive SHA-256 is still required for the final artifact manifest.

## Data and store inventory

Raw-file status means only “regular, non-zero file.”  Store status means only “directory exists.”  A final
`ready` decision additionally requires checksums, endpoint reachability, repository configuration, and
expected cardinalities.

| logical input | exact/apparent size at audit | inventory state | missing validation or pair |
|---|---:|---|---|
| WatDiv 10M base N-Triples | 1,542,624,409 B | non-zero raw file | checksum manifest |
| WatDiv 10M reified N-Triples | 3,856,329,334 B | non-zero raw file | checksum manifest |
| WatDiv 100M base N-Triples | 15,599,074,048 B | non-zero raw file | checksum manifest |
| WatDiv 100M reified N-Triples | 39,027,242,370 B | non-zero raw file | checksum manifest |
| WatDiv 200M base/reified | 0 B / 0 B | **incomplete placeholders; treat as missing** | regenerate both; never report as available |
| TPC-H SF 0.01 RDF | 112,498,906 B | non-zero raw file | checksum and endpoint count |
| TPC-H SF 0.1 RDF | 1,138,268,330 B | non-zero raw file | checksum and endpoint count |
| TPC-H SF 1 RDF | 11,528,736,074 B | non-zero raw file | raw data exists; GraphDB SF1 store absent |
| GraphDB `watdivbase` | about 557 MiB | directory present | endpoint/count validation |
| GraphDB `watdiv` | about 2.4 GiB | directory present | endpoint/count validation |
| GraphDB `watdiv100m` | about 24 GiB | one directory present | role and separate base/reified pair not demonstrated |
| GraphDB `tpch001` / `tpch01` | about 137 MiB / 771 MiB | directories present | endpoint/count validation |
| GraphDB `wikidata` | about 207 GiB | directory present; documented 2.13 B statement triples | raw dump identity/checksum and live count |
| GraphDB `wdpaths` | about 5.9 GiB | directory present | endpoint/count validation |
| Oxigraph WatDiv | about 2.7 GiB | one store directory present | live endpoint and base/reified pairing not demonstrated |
| QLever WatDiv | about 556 MiB | one index directory present | live endpoint, index metadata, and pair not demonstrated |
| MillenniumDB WatDiv | about 1.4 GiB | one store directory present | live endpoint and pair not demonstrated |

The 10M GraphDB directories appear to provide the intended base/reified pair.  The 100M and cross-engine
directories do not yet demonstrate two independently validated base and reified stores, so a final R9
paired comparison must either build the missing side or mark the cell N/A.  `wikidata` and `wdpaths`
exist, but their upstream raw dump provenance is not frozen.  Scratch repositories `gallery` and `test`
must not be substituted for a named scale dataset.

For each frozen repository, retain a small validation artifact containing: engine/version, logical store
name, input SHA-256 list, successful health query, expected triple/statement count, sentinel query result,
reification scheme, load command/options, and completion time.  Do not infer any of these from disk size.

## Services and ports

These are the configured localhost defaults in `reference/engines/engines.json` and the ProvSQL harness.
They are not claims that a service was listening during the sandbox audit.

| service | port | frozen-batch role/condition |
|---|---:|---|
| GraphDB | 7200 | query/update live-test passed on 2026-07-13; rerun repository sentinel before freezing |
| Fuseki | 3030 | SPARQLprov-compatible baseline; load and endpoint not yet certified |
| Oxigraph | 7878 | independent engine validation |
| QLever | 7001 | safe default is read-only/non-path at scale |
| MillenniumDB | 1234 | read-only comparison in current registry |
| Virtuoso | 8890 | native SPARQLprov baseline endpoint; not yet certified |
| Stardog | 5820 | native NPCS baseline endpoint; official jar/license deployment missing |
| PostgreSQL + ProvSQL | 54320 | Unix socket directory supplied separately by `PGHOST` |

Use `--probe-local` only from the namespace that will execute the benchmark.  The main-namespace GraphDB
query/update success is the audit fact; a child network sandbox can still report “closed or isolated.”  A
successful TCP connection is only a transport check and must still be followed by the dataset sentinel
checks above.

## Environment-variable whitelist

The collector records only this allow-list.  It emits set/unset state and sanitized metadata, never raw
paths, URL user-info/query strings, credentials, proxy settings, tokens, arbitrary variables, host name,
user name, or home directory.

| variables | purpose | audit state / freeze rule |
|---|---|---|
| `JAVA_HOME`, `MAVEN_HOME` | Java/Maven selection | unset in sandbox; explicit CLI binaries were audited |
| `PYTHONHASHSEED` | deterministic Python hashing | unset; set and record for final batch if any code depends on hash iteration |
| `CIRCUIT_UPDATE_ENDPOINT`, `SPARQLCIRC_ENDPOINT` | engine endpoints | unset; collector retains only scheme/port/loopback class and credential presence |
| `CIRCUIT_SKIP_LOAD`, `CIRCUIT_READONLY`, `CIRCUIT_CLEANUP` | harness mutation policy | unset; freeze per engine/cell |
| `WATDIV_REPO` | logical GraphDB repository | unset; must match the validated repository manifest |
| `WATDIV_NT` | raw input path | unset; collector emits only existence and size |
| `D4`, `D4V2` | external compiler selection | unset in collector shell; the pinned d4v2 path/hash above is available and must be selected explicitly |
| `PGHOST`, `PGPORT` | ProvSQL socket and port | unset in sandbox; canonical port is 54320 |
| `GRAPHDB_HOME`, `GDB_HEAP`, `GDB_HEAP_SIZE` | GraphDB deployment/heap | unset in sandbox; final query heap must be fixed |
| `LD_LIBRARY_PATH` | native CUDD/d4 runtime | unset; collector records presence only, never contents |
| `OMP_NUM_THREADS`, `OPENBLAS_NUM_THREADS`, `MKL_NUM_THREADS` | hidden native parallelism | unset; set explicitly (normally 1) for controlled compiler comparisons |
| `NPCS_ORIG_JAR` | official NPCS artifact | unset; never point it at Maven Shade's local `original-npcs-rewrite.jar` |

`engine/target/original-npcs-rewrite.jar` is the pre-shaded local project jar created by Maven Shade.  It is
not an author-provided NPCS artifact and must not satisfy the official-NPCS gate.

## Isolation and execution rules

- Run one large store/batch at a time.  Loading, indexing, GraphDB compaction, d4 compilation, and another
  timing harness must not overlap.
- Keep GraphDB Free/Lite at the declared one-worker/core policy.  Pin GraphDB, the Java client, and the
  CUDD/d4 worker to documented, non-overlapping CPU sets when simultaneous processes are unavoidable.
- Fix heap and JVM options before warming.  Reject a run that swaps, OOMs, changes CPU affinity, restarts a
  daemon, recompacts a store, or crosses the hard timeout.
- Use warm-cache steady state consistently: start daemon, validate store, perform one unreported warm-up,
  then run the five measurements without reloading.  Cold-start numbers belong in a separate labelled row.
- Capture load average, available RAM, disk free space, affinity/cgroup state, and service probe immediately
  before and after a batch.  Preserve stdout, stderr, exit status, timeout status, and raw per-run samples.
- Use local scratch for compiler intermediates.  Record compiler input SHA-256 and output statistics; remove
  scratch only after the citable artifacts have been copied and hashed.
- Do not run publishable timing inside the Codex network/filesystem sandbox.  It is suitable for code tests
  and inventory only because the endpoint namespace and filesystem policy differ from the daemon context.

## Official baseline gaps

| baseline/cell | evidence already present | remaining gap for a final official row |
|---|---|---|
| d4v2 Level-1 | pinned executable and invocation are available; source checkout is inventoried | register the same hashed executable in ProvSQL, run the controlled 1+5 comparison, and retain raw outputs |
| ProvSQL | clean source checkout and exact-probability parity are documented | live service not certified in this audit; same-binary d4v2 Level-1 run remains to be executed |
| SPARQLprov | official 2021 rewriter built; tests and query-rewrite measurements exist | archive SHA missing; end-to-end execution/result bytes on its loaded Virtuoso/reified data remain undone |
| NPCS | clean-room `NpcsRewriter` comparison exists | official author jar is absent, so official performance cells must be N/A until supplied and hashed |
| Fuseki/Virtuoso/Stardog | ports/configuration are documented | no validated frozen deployment/store in this audit |
| Oxigraph/QLever/MillenniumDB | WatDiv store/index directories exist | endpoint health, input identity, scheme, count, and paired base/reified stores are not all demonstrated |

These gaps do not invalidate already documented semantic checks; they limit what may be labelled an
official head-to-head performance comparison.  Missing official artifacts must remain N/A, not be replaced
with the clean-room implementation under the same label.

## Re-capturing the environment

The collector is standard-library-only and takes every machine-specific path as an input.  A representative
pre-batch command is:

```bash
<python-env>/bin/python reference/paper/capture_environment.py \
  --repo-root <repo> \
  --data-root <data-root> \
  --tools-root <tools-root> \
  --graphdb-home <graphdb-home> \
  --graphdb-install <tools-root>/graphdb-10.7.6 \
  --graphdb-edition free-lite \
  --graphdb-worker-cores 1 \
  --python-bin <python-env>/bin/python \
  --java-bin <java-home>/bin/java \
  --maven-bin <maven-home>/bin/mvn \
  --d4v2-source <tools-root>/d4v2 \
  --d4v2-bin <tools-root>/d4v2/scripts/d4_static \
  --d4-bin <tools-root>/d4/d4 \
  --provsql-source <tools-root>/provsql \
  --sparqlprov-root <baseline-root>/sparqlprov/SPARQLprov-experiments \
  --npcs-orig-jar <baseline-root>/official-npcs.jar \
  --probe-local \
  --include-volatile \
  --format json \
  --output <artifact-root>/environment.json
```

Omit `--npcs-orig-jar` rather than pointing it at a substitute.  Keep `--d4v2-bin` on the pinned
`scripts/d4_static` and require its SHA match; absence or mismatch is a hard Level-1 failure.
`--scan-store-sizes` is optional and may walk very large stores; directory presence and apparent size still
do not establish readiness.  For a readable review copy, repeat with `--format markdown`; the JSON remains
the canonical artifact.  Run once immediately before and once immediately after the batch, and verify that
all stable fields match while retaining the volatile before/after values.
