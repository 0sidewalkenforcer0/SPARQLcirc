# Main Review Remediation Log

This log records every working-tree change made in response to the whole-repository review. Changes remain uncommitted until they pass the listed verification and are explicitly approved for commit.

## Existing change carried into this pass

### PATH-FEEDBACK-BATCHING — batch property-path feedback writes

- Status: verified before this remediation pass; still uncommitted.
- Finding context: large path-round relations could overflow an endpoint request buffer during one `RepositoryConnection.add(Model)` call.
- Files: `engine/src/main/java/npcs/circuit/CircuitRun.java`.
- Change: route path feedback through the existing 5,000-statement batching helper.
- Verification already completed: Maven package job 46988; controlled property-path job 46994; WatDiv 10M diagnostic job 47000.

## Review findings resolved in this pass

### PY-PATH-SAME-ENDPOINT (P1) — enforce compatible bindings for repeated path variables

- Source: Python review, “对同名路径端点执行一致性检查”.
- Files: `reference/gamma.py`, `reference/wmc.py`, `reference/tests.py`.
- Resolution: both circuit evaluation and the possible-world oracle use the same compatibility check; a second occurrence of `?x` may not overwrite a different value.
- Regression: a non-self edge does not satisfy `?x :p+ ?x`, while a self-loop does.
- Status: verified by Slurm job 47042.

### JAVA-EMPTY-GROUP-BY (P2) — omit an empty GROUP BY clause

- Source: Java review, “省略空变量列表后的 GROUP BY”.
- Files: `engine/src/main/java/npcs/rewrite/NpcsRewriter.java`, `engine/src/test/java/npcs/circuit/CircuitRewriterTest.java`.
- Resolution: append `GROUP BY` only when the grouping-variable list is non-empty.
- Regression: ground BGP and ground UNION rewrites contain no `GROUP BY` and parse as SPARQL.
- Status: verified by Slurm job 47042.

### R9-MINUS-EDGE-COUNT (P1) — count both directed Minus inputs

- Source: Python review, “把 Minus 的两个有向边计入 R9 结构统计”.
- Status: retired with the legacy R9 construction harness.

### PORTFOLIO-CONSTANT-ROOT (P2) — evaluate Boolean constants

- Source: Python review, “支持 portfolio 中的 Boolean 常量根”.
- Files: `reference/compile_portfolio.py`, `reference/paper/test_review_regressions.py`.
- Resolution: read-once evaluation returns 0.0/1.0 for valid constants and rejects other payloads.
- Status: verified by Slurm job 47042.

### FACTOR-EMPTY-BGP (P2) — return the SPARQL unit mapping

- Source: Python review, “为空 BGP 生成单位映射”.
- Files: `reference/factor.py`, `reference/paper/test_review_regressions.py`.
- Resolution: an empty factored BGP returns one empty binding rooted at `CONST1`.
- Status: verified by Slurm job 47042.

### D4-ARC-VALIDATION (P2) — reject undeclared parents and terminal arcs

- Source: Python review, “拒绝 d4 弧中的未声明父节点”.
- Files: `reference/ddnnf_wmc.py`, `reference/paper/test_review_regressions.py`.
- Resolution: validate both arc endpoints and forbid outgoing arcs from terminal nodes.
- Status: verified by Slurm job 47042.

### MATRIX-CSV-PRECEDENCE (P2) — honor an explicit input override

- Source: integration review, “让 PCM_MATRIX_CSV 优先于已提交矩阵”.
- Files: `presentation/make_matrix_figures.py`, `reference/paper/test_review_regressions.py`.
- Resolution: `PCM_MATRIX_CSV` is checked before committed or artifact fallbacks.
- Status: verified by Slurm job 47042.

### README-RDFSTAR-FLAG (P2) — document the implemented WatDiv option

- Source: integration review, “使用 reify.py 实际支持的 --star”.
- Files: `README.md`.
- Resolution: replace the unsupported `--scheme SPARQL_Star` spelling with `--star`.
- Verification: covered by the WatDiv reifier smoke command in this pass.
- Status: verified by Slurm job 47042.

## Review findings implemented in the second pass

### JAVA-UTF8-BOM (P2) — accept UTF-8 query files with a leading BOM

- Source: integration review, “去除查询文件开头的 UTF-8 BOM”.
- Files: `engine/src/main/java/npcs/Utf8Text.java`, `App.java`, `RunExample.java`, `circuit/CircuitRun.java`, `circuit/PathIsoSeq.java`, and `CircuitRewriterTest.java`.
- Resolution: all query-file entry points share one UTF-8 reader that removes only the first U+FEFF.
- Regression: a BOM-prefixed query parses while a U+FEFF inside its literal remains present.
- Status: verified by Slurm job 47045.

### JAVA-UTF8-STDOUT (P2) — write CLI text independently of the platform charset

- Source: Java review, “用 UTF-8 字节写出重写结果”.
- Files: `engine/src/main/java/npcs/Utf8Text.java`, `App.java`, `RunExample.java`.
- Resolution: rewritten queries and example output use explicit UTF-8 bytes.
- Status: verified by Slurm job 47045.

### CIRCUIT-FUNCTIONAL-PREDICATES (P1/P2) — reject conflicting or incomplete RDF circuit fields

- Source: Python and integration reviews, “拒绝多值的函数型 circuit 谓词” / “拒绝多值或残缺的 RDF 电路结构”.
- Files: `reference/circuit_io.py`, `reference/paper/test_review_regressions.py`.
- Resolution: collect RDF values as sets, validate deterministic cardinalities for type, Minus operands, binding variable/value, and reject duplicate variables per answer gate.
- Regression: reversing conflicting `c:minuend` statements produces the same error; a missing subtrahend is rejected.
- Status: verified by Slurm job 47045.

### CIRCUIT-IRI-UCHAR (P2) — decode Unicode escapes in IRIREF and datatype IRIs

- Source: Python review, “解码 IRIREF 与 datatype 中的 Unicode 转义”.
- Files: `reference/circuit_io.py`, `reference/paper/test_review_regressions.py`.
- Resolution: decode only legal `\\u`/`\\U` IRI escapes, reject non-scalar values, and canonicalize escaped/direct spellings identically.
- Status: verified by Slurm job 47045.

### PREPROCESSOR-UTF8 (P2) — make RDF preprocessing explicitly UTF-8

- Source: integration review, “以 UTF-8 读写 RDF 数据预处理文件”.
- Files: `reference/watdiv/reify.py`, `reference/wikidata/reify_wikidata.py`, `reference/tpch/tbl_to_rdf.py`, `reference/paper/test_review_regressions.py`.
- Resolution: all RDF/table sources and generated RDF outputs specify UTF-8.
- Regression: WatDiv, Wikidata, and TPC-H fixtures containing `München` and `€` succeed under the C locale with Python UTF-8 mode disabled.
- Status: verified by Slurm job 47045.

### WATDIV-ARGUMENT-VALIDATION (P2) — reject unknown or conflicting reifier options

- Source: integration review, “使用 reify.py 实际支持的 --star”.
- Files: `reference/watdiv/reify.py`, `reference/paper/test_review_regressions.py`.
- Resolution: replace option filtering with `argparse` and a mutually exclusive `--star` / `--namedgraph` group.
- Regression: `--star` emits RDF-star; the old unsupported `--scheme SPARQL_Star` spelling exits nonzero.
- Status: verified by Slurm job 47045.

### CACHE-APOSTROPHE-NORMALIZATION (P2) — preserve lexical backslash plus apostrophe

- Source: Python review, “不要全局替换 N-Triples 的转义撇号”.
- Files: `reference/paper/circuit_cache.py`, `reference/paper/test_review_regressions.py`.
- Resolution: normalize MillenniumDB's non-standard `\\'` only when it is an unescaped apostrophe escape inside a literal; preserve a serialized lexical backslash followed by apostrophe.
- Status: verified by Slurm job 47045.

### VERIFY-CONSOLE-ENCODING (P2) — avoid UnicodeEncodeError on restricted consoles

- Source: integration review, “兼容非 UTF-8 Windows 控制台”.
- Files: `reference/verify_circuit_io.py`, `reference/paper/test_review_regressions.py`.
- Resolution: configure stdout with `backslashreplace` when supported.
- Regression: the verifier completes with `PYTHONIOENCODING=ascii`.
- Status: verified by Slurm job 47045.

## Review findings implemented in the third pass

### ONE-SHOT-FLAT-PLAN (P1) — make single-POST runners request one flat CONSTRUCT

- Source: integration review, “为单次 POST 的 WatDiv 流程显式选择 flat” and “在跨引擎验证器中执行反馈或改用 flat”.
- Files: `reference/watdiv_run.py`, `reference/engines/verify_http.py`, `reference/engines/verify_oxigraph.py`, `reference/paper/test_review_regressions.py`.
- Resolution: all one-shot runners pass `--construction=flat`, require a successful Java process, and reject plans containing zero or multiple CONSTRUCT queries instead of concatenating an invalid or feedback-dependent plan.
- Regression: the WatDiv runner passes the flat option and rejects a two-CONSTRUCT diagnostic stream.
- Status: verified by Slurm job 47048.

### E3-BINDING-PROBE-FAIL-CLOSED (P1) — never execute an unbound query after a failed safety probe

- Source: Python review, “绑定探测失败时不要执行未绑定 E3 查询”.
- Files: `reference/e3_run.py`, `reference/paper/test_review_regressions.py`.
- Resolution: distinguish an inapplicable binding from a failed or empty lookup; the latter produces `err:binding-probe` before any circuit construction request.
- Regression: network failure raises the dedicated error and `run_query` does not call `get_construct`.
- Status: verified by Slurm job 47048.

### E8-REPETITION-AND-INPUT (P2) — measure exactly RUNS samples after one warmup

- Source: Python/integration review, “让 E8_RUNS 精确控制计时样本数” and “在验证 E8 输入后再覆盖结果文件”.
- Files: `reference/e8_wikidata.py`, `reference/paper/test_review_regressions.py`.
- Resolution: require a positive run count, perform one unmeasured warmup followed by exactly `E8_RUNS` measured builds, validate all three query categories before writing, and atomically replace the output CSV only after a completed run.
- Regression: two requested measurements cause three builds total and average only the final two; a missing directory or empty expected category leaves an existing output file untouched.
- Status: verified by Slurm job 47050.

### COMPLETE-ANSWER-KEY-PARITY (P2) — reject missing or extra answers before probability comparison

- Source: Python review, “比较完整 answer key 集合后再判 parity”.
- Files: `reference/e11_per_answer_vs_shared.py`, `reference/e11_real.py`, `reference/factor_demo.py`, `reference/watdiv_factor.py`, `reference/paper/test_review_regressions.py`.
- Resolution: E11 raises on unequal answer-key sets; flat/factored demonstrations require identical sets before sampling probabilities.
- Regression: disjoint nonempty answer-key sets are rejected rather than reported as zero difference.
- Status: verified by Slurm job 47048.

### BIND-MANIFEST-HTTP-AND-TIMEOUT (P2) — fail on HTTP errors and preserve minimum-candidate determinism

- Source: Python review, “将绑定探测的 HTTP 错误视为失败” and “超时后不要跳过更小的绑定候选”.
- Status: retired with the legacy workload-binding harness.

## Review findings implemented in the fourth pass

### REIFY-QUERY-VARIABLE-HYGIENE (P2) — avoid capture by internal statement variables

- Source: Python review, “避免 reification 内部变量捕获用户变量”.
- Files: `reference/reify_query.py`, `reference/paper/test_review_regressions.py`.
- Resolution: initialize the statement-variable generator with every variable in the parsed query algebra and skip conflicting `__tN` names.
- Regression: a query that uses `?__t1` keeps that user binding and starts generated statement variables at `?__t2`.
- Status: verified by Slurm job 47050.

### REIFY-QUERY-SLICE (P2) — preserve LIMIT and OFFSET

- Source: Python review, “在 reification 重写中保留 LIMIT 与 OFFSET”.
- Files: `reference/reify_query.py`, `reference/paper/test_review_regressions.py`.
- Resolution: retain the translated algebra's `Slice` length and start values and serialize them as solution modifiers on the rewritten SELECT.
- Regression: `LIMIT 1 OFFSET 2` is present in the rewritten query and the result parses again.
- Status: verified by Slurm job 47050.

### REIFY-QUERY-TERM-SERIALIZATION (P2) — serialize legal SPARQL RDF terms

- Source: Python review, “使用合法的 SPARQL RDF term 序列化”.
- Files: `reference/reify_query.py`, `reference/paper/test_review_regressions.py`.
- Resolution: delegate URI, blank-node, and literal rendering to RDFLib's RDF-term serializer instead of escaping only backslashes and quotes.
- Regression: literals containing newline, carriage return, quote, and backslash survive parse→reify→parse with the identical lexical value.
- Status: verified by Slurm job 47050.

## Review findings implemented in the fifth pass

### EXPERIMENT-JAVA-PLAN-FAIL-CLOSED (P2) — reject failed, partial, or stalled plan generation

- Source: Python review, “传播实验 Java 重写器的超时和非零退出”.
- Files: `reference/e6_minus.py`, `reference/e8_wikidata.py`, `reference/e9_tpch.py`, `reference/paper/test_review_regressions.py`.
- Resolution: E6/E8/E9 share one flat-plan runner with the canonical query deadline, `check=True`, UTF-8 temporary query files, guaranteed temporary-file cleanup, explicit empty-plan rejection, and narrowly recognized unsupported-query handling. E8/E9 record plan failures as error rows before any endpoint build.
- Regression: successful execution supplies a complete plan and removes its query file; timeout and nonzero exit propagate and also clean up; E8 does not invoke `build` after a plan timeout.
- Status: verified by Slurm job 47051.

## Review findings implemented in the sixth pass

### CI-PLAN-IDENTITY (P2) — run the frozen construction-plan guard in CI

- Source: integration review, “补全 CI 的计划身份基线检查”.
- Attempted files: `.github/workflows/ci.yml`, `reference/paper/test_review_regressions.py`.
- Result: Slurm job 47052 proved that the committed baseline already differs from the current planner (for example, factored C2 changes from 26 to 17 CONSTRUCT steps). The guard correctly failed before the remaining checks.
- Decision boundary: updating `engine/verify/plan-identity.baseline` would approve a change to already-measured circuit identities and is therefore not a mechanical repair. Leaving the new CI step in place would make every current CI run fail.
- Status: deferred for explicit baseline review; the CI insertion and its regression assertion were reverted, while this failed attempt remains recorded here.

### DOC-FACTORED-JAVA-STATUS (P2) — describe the integrated Java factored default

- Source: integration review, “更新 factored Java 集成状态说明”.
- Files: `reference/README.md`, `reference/paper/test_review_regressions.py`.
- Resolution: replace the obsolete Python-only prototype statement with the current Java default and explicit flat option.
- Status: verified by Slurm job 47053.

### FINAL-FIGURE-WIKIDATA-COMMAND (P2) — include the Wikidata generator in full redraws

- Source: integration review, “将 Wikidata 图纳入完整重绘命令”.
- Files: `presentation/figures/final/README.md`, `reference/paper/test_review_regressions.py`.
- Resolution: add `make_wikidata_figure.py` to the documented regeneration pipeline.
- Status: verified by Slurm job 47053.

### E11-DYNAMIC-FOOTER (P3) — derive the reported amortization from CSV data

- Source: integration review, “从 CSV 动态生成 E11 倍数说明”.
- Files: `presentation/make_pqe_figure.py`, `reference/paper/test_review_regressions.py`.
- Resolution: build the footer's E11 multiplier and answer count from the final sorted CSV row, matching the already-data-driven annotation.
- Status: verified by Slurm job 47053.

## Review findings implemented in the seventh pass

### NPCS-FAIL-CLOSED-ALGEBRA (P1) — do not flatten unsupported VALUES, SERVICE, or filtered OPTIONAL

- Source: Java review, “对 VALUES 和 SERVICE 直接拒绝或保留” and “保留 OPTIONAL 的 LeftJoin 条件”.
- Files: `engine/src/main/java/npcs/rewrite/NpcsRewriter.java`, `engine/src/test/java/npcs/circuit/CircuitRewriterTest.java`.
- Resolution: classify VALUES and SERVICE as non-BGP operators and reject a LeftJoin condition explicitly, rather than letting `StatementPatternCollector` silently drop them.
- Regression: all three shapes fail with an explicit unsupported-pattern diagnostic; an ordinary OPTIONAL remains supported by the existing suite.
- Status: verified by Slurm job 47054.

### COMPOSITE-FILTER-ANSWER-IDENTITY (P1) — include a composite FILTER condition in the answer tag

- Source: Java review, “将复合 FILTER 条件纳入答案门标签”.
- Files: `engine/src/main/java/npcs/circuit/CircuitRewriter.java`, `engine/src/test/java/npcs/circuit/CircuitFilterTest.java`.
- Resolution: when a filtered composite cannot be represented as one BGP key, recursively fingerprint its operand and rendered condition instead of returning the constant `OTHERFilter` key.
- Regression: two FILTER-over-UNION queries with the same projected binding but different conditions produce disjoint answer roots in flat and factored modes.
- Status: verified by Slurm job 47054.

### MINUS-OUTPUT-SCOPE (P1) — base MINUS shortcuts on exported bindings

- Source: Java review, “改用输出作用域判定 MINUS 优化”.
- Files: `engine/src/main/java/npcs/circuit/CircuitRewriter.java`, `engine/src/test/java/npcs/circuit/CircuitSemanticsTest.java`.
- Resolution: the OPTIONAL-subtrahend shortcut and chained-difference guard now use `scopeOf`, so variables mentioned only inside a subtrahend are not treated as exported bindings.
- Regression: a D-only variable hidden syntactically inside C's own MINUS is checked against RDF4J over every possible world in both construction modes.
- Status: verified by Slurm job 47054.

### PATH-FRONTIER-RDF-TERMS (P1) — preserve RDF types during reachable-subgraph BFS

- Source: Java review, “保留属性路径前沿值的 RDF 类型”.
- Files: `engine/src/main/java/npcs/circuit/CircuitRewriter.java`, `engine/src/main/java/npcs/circuit/CircuitRun.java`, `engine/src/test/java/npcs/circuit/CircuitSemanticsTest.java`.
- Resolution: frontier/reachable sets retain RDF4J `Value` objects and render typed SPARQL VALUES with `NTriplesUtil`, instead of converting every reached value to an IRI-shaped string.
- Regression: a one-hop path ending in a literal builds successfully and reports the literal binding exactly.
- Status: verified by Slurm job 47054.

### DETERMINISTIC-CIRCUIT-ORDER (P2) — sort statements before N-Triples serialization

- Source: Java review, “对最终 N-Triples 做规范排序”.
- Files: `engine/src/main/java/npcs/circuit/CircuitRun.java`, `engine/src/test/java/npcs/circuit/CircuitSemanticsTest.java`.
- Resolution: sort statements by their canonical N-Triples line before passing them to the existing N-Triples writer.
- Regression: models with opposite insertion orders produce byte-identical output in lexical statement order.
- Status: verified by Slurm job 47054.

### REPOSITORY-FINALLY (P2) — close the repository after every main-path failure

- Source: Java review, “在 finally 中关闭 Repository”.
- Files: `engine/src/main/java/npcs/circuit/CircuitRun.java`.
- Resolution: wrap the full connection/load/build/serialize section in an outer `try/finally` whose finally always calls `repo.shutDown()`.
- Status: verified by Slurm job 47054.

## Review findings implemented in the eighth pass

### QUOTED-TRIPLE-GATE-IDENTITY (P1) — distinguish RDF-star bindings

- Source: Java review, “为引用三元组生成不同的项哈希”.
- Files: `engine/src/main/java/npcs/circuit/CircuitRewriter.java`, `FactoredBgpRewriter.java`, and `CircuitSemanticsTest.java`.
- Resolution: the non-SPARQL-1.1 term branch hashes `STR(term)`, whose SPARQL-star contract is the quoted triple's canonical N-Triples-star lexical form, instead of using one constant `x` for every quoted triple. Flat and factored implementations remain byte-compatible.
- Regression: two nested quoted-triple bindings must produce two answer roots and retain two values in both construction modes.
- Investigation: RDF4J probe jobs 47055 and 47056 confirmed canonical `STR`, subject/predicate/object access, and quoted-triple component matching on the repository's actual fat JAR.
- Status: verified by Slurm job 47059.

### RECURSIVE-RDFSTAR-SKOLEMIZATION (P1) — handle blank nodes inside quoted triples

- Source: Java review, “递归 Skolemize 引用三元组内的空白节点”.
- Files: `engine/src/main/java/npcs/rewrite/Skolem.java`, `engine/src/main/java/npcs/circuit/CircuitRun.java`, and `SkolemTest.java`.
- Resolution: recursively rebuild quoted triples during streaming Skolemization. On `CIRCUIT_SKIP_LOAD` with the RDF-star scheme, inspect the subject/object constituents of quoted triples recursively in addition to the ordinary top-level blank-node ASK.
- Regression: nested blank nodes are detected before transformation and absent afterward.
- Status: verified by Slurm job 47059.

### FACTOR-NATIVE-RDF-TERM-IDENTITY (P2) — preserve binding kinds in materialized row keys

- Source: Python review, “在原生因子化键中保留 RDF term 类型”.
- Files: `reference/factor_native.py`, `reference/paper/test_review_regressions.py`.
- Resolution: replace raw `STR` row keys with fixed-width hashes that distinguish unbound, IRI, blank node, literal lexical/datatype/language, and quoted triple values.
- Regression: an IRI and literal with the same lexical text materialize two distinct rows.
- Status: verified by Slurm job 47059.

### PQE-JAR-HARD-DEADLINE (P1) — bound and reap circuit construction

- Source: Python review, “为 --jar 构造进程设置硬超时”.
- Files: `reference/pqe.py`, `reference/paper/test_review_regressions.py`.
- Resolution: add a positive `--timeout` using the repository's `QUERY_TIMEOUT_S` default, run the JVM in its own POSIX process group, and terminate/reap the group on timeout before returning an error.
- Regression: a fake JVM parent and sleeping descendant are both gone after the deadline.
- Status: verified by Slurm job 47059.

### G8-PROCESS-LIFECYCLE (P2) — bound and validate the measured Java process

- Source: Python review, “限时并检查 G8 Java 子进程”.
- Files: `reference/g8_space_memory.py`, `reference/paper/test_review_regressions.py`.
- Resolution: sample RSS only until the canonical query deadline, terminate/reap the POSIX process group on timeout, and reject a nonzero exit with retained diagnostics.
- Regression: a sleeper times out and an immediate exit 7 is rejected.
- Status: verified by Slurm job 47059.

## Review findings implemented in the ninth pass

### NPCS-MINUS-UNION-DOMAIN-GUARD (P1) — guard heterogeneous UNION branches independently

- Source: Java review, “按 UNION 分支应用 MINUS 共享域保护”.
- Files: `engine/src/main/java/npcs/rewrite/NpcsRewriter.java` and `engine/src/test/java/npcs/circuit/CircuitRewriterTest.java`.
- Resolution: distribute a heterogeneous left UNION and flatten a right UNION into branches. Only right branches whose exported binding domain overlaps the current left branch enter the subtrahend; all overlapping branches still feed one aggregated difference.
- Regression: a disjoint left branch survives a matching right branch, and a disjoint right branch cannot remove a left solution when the overlapping branch does not match.
- Status: verified by Slurm jobs 47061 and 47062.

## Review findings implemented in the tenth pass

### COMPOSITE-OPTIONAL-PATH (P2) — materialize `p?` inside supported operators

- Source: Java review, “将 p? 识别为可组合的路径操作数”.
- Files: `engine/src/main/java/npcs/circuit/CircuitRewriter.java` and `engine/src/test/java/npcs/circuit/CircuitSemanticsTest.java`.
- Resolution: unwrap only RDF4J's parser-generated identity `Distinct(Projection(Union(...)))` expansion for `p?`, treat its zero-length branch as a composite operand, and materialize that branch as a private row relation carrying the existing terms-in-graph occurrence gate. Real scope-changing projections remain guarded.
- Regression: `p?` composes with JOIN, FILTER, OPTIONAL, and MINUS in both construction modes and both Standard/RDF-star leaf encodings, checked answer-by-answer against RDF4J over every possible world.
- Status: verified by focused Slurm job 47069 and full-regression job 47070.

## Deferred findings

Findings that require a compatibility, scope, persistence-format, timeout-policy, or experiment-protocol decision are intentionally not changed in this pass. They remain open in the original review reports.

## Verification history

### Slurm job 47041 — focused batch, first attempt

- Maven package and Java tests: passed.
- `reference/tests.py`: passed, 173/173.
- New review regressions: 4/5 passed; the CSV-precedence test failed during module import because NumPy is not installed in the production environment.
- Interpretation: this is a test-isolation failure and independently confirms the open presentation-dependency finding; the regression was changed to stub plotting-only imports before loading `find_csvs`.
- `quick_verify.py`: not reached because the runner is fail-fast.

### Slurm job 47042 — focused batch, corrected test isolation

- State: `COMPLETED`, exit code `0:0`, elapsed `00:01:15`, MaxRSS `146064K`, node `aisa-gpuB03`.
- Maven package and JUnit: passed.
- `reference/tests.py`: passed, 173/173.
- `reference/paper/test_review_regressions.py`: passed, 5/5.
- `reference/quick_verify.py`: passed, including the fresh Java circuit, PQE CLI, composition differential, and Skolem round trip.
- Log: `/mnt/nfs/home/st196528/research/sparqlcirc/reproduction/logs/sc-review-fix1b-47042.log`.

### Slurm job 47044 — second batch, first attempt

- State: `FAILED`, exit code `1:0`, elapsed `00:00:07`, node `aisa-gpuB03`.
- Failure stage: Java compilation, before any test ran.
- Cause: the BOM-reader refactor removed `StandardCharsets` from `CircuitRun`, but its SHA-256 helper still uses `StandardCharsets.UTF_8`.
- Correction: restore that import; no behavior change.
- Log: `/mnt/nfs/home/st196528/research/sparqlcirc/reproduction/logs/sc-review-fix2-47044.log`.

### Slurm job 47045 — second batch, corrected Java import

- State: `COMPLETED`, exit code `0:0`, elapsed `00:01:19`, MaxRSS `197368K`, node `aisa-gpuB03`.
- Maven package and JUnit: passed.
- `reference/tests.py`: passed, 173/173.
- `reference/paper/test_review_regressions.py`: passed, including strict circuit parsing, Unicode IRI canonicalization, cache escaping, locale-independent preprocessors, WatDiv option validation, and restricted-console output.
- `reference/quick_verify.py`: passed, including the fresh Java circuit, PQE CLI, composition differential, and Skolem round trip.
- Log: `/mnt/nfs/home/st196528/research/sparqlcirc/reproduction/logs/sc-review-fix2b-47045.log`.

### Slurm job 47047 — third batch, first submission attempt

- State: `FAILED`, exit code `127:0`, elapsed `00:00:00`, node `aisa-gpuB02`.
- Failure stage: environment startup, before compilation or tests.
- Cause: the runner was submitted directly, so the non-interactive batch environment did not expose Maven (`mvn: command not found`).
- Correction: resubmit the unchanged runner through the established `sparqlcirc` Conda environment.
- Log: `/mnt/nfs/home/st196528/research/sparqlcirc/reproduction/logs/sc-review-fix3-47047.log`.

### Slurm job 47048 — third batch in the established environment

- State: `COMPLETED`, exit code `0:0`, elapsed `00:01:20`, MaxRSS `101156K`, node `aisa-gpuB02`.
- Maven package and JUnit: passed.
- `reference/tests.py`: passed, 173/173.
- `reference/paper/test_review_regressions.py`: passed, including flat-plan enforcement, E3 fail-closed binding, exact E8 repetition, complete answer-key parity, and manifest HTTP/timeout handling.
- Real Java flat-plan smoke and `reference/factor_demo.py`: passed.
- `reference/quick_verify.py`: passed, including fresh Java circuit construction, PQE CLI, composition differential, and Skolem round trip.
- Log: `/mnt/nfs/home/st196528/research/sparqlcirc/reproduction/logs/sc-review-fix3b-47048.log`.

### Slurm job 47049 — fourth batch, first regression assertion

- State: `FAILED`, exit code `1:0`, elapsed `00:00:24`, node `aisa-gpuB02`.
- Maven package, JUnit, and `reference/tests.py` (173/173): passed.
- Failure: the new RDF-term test rejected a raw newline in the generated text even though RDFLib correctly emitted a legal SPARQL long string (`"""..."""`) and reparsed it successfully.
- Correction: compare the reparsed literal's complete lexical value instead of requiring one particular legal escaping form.
- `quick_verify.py`: not reached because the runner is fail-fast.
- Log: `/mnt/nfs/home/st196528/research/sparqlcirc/reproduction/logs/sc-review-fix4-47049.log`.

### Slurm job 47050 — fourth batch with semantic literal assertion

- State: `COMPLETED`, exit code `0:0`, elapsed `00:01:21`, MaxRSS `69296K`, node `aisa-gpuB02`.
- Maven package and JUnit: passed.
- `reference/tests.py`: passed, 173/173.
- `reference/paper/test_review_regressions.py`: passed, 18/18, including variable hygiene, Slice preservation, RDF-term lexical round trip, E8 category validation, and atomic output preservation.
- Real Java flat-plan smoke and `reference/factor_demo.py`: passed.
- `reference/quick_verify.py`: passed, including fresh Java circuit construction, PQE CLI, composition differential, and Skolem round trip.
- Log: `/mnt/nfs/home/st196528/research/sparqlcirc/reproduction/logs/sc-review-fix4b-47050.log`.

### Slurm job 47051 — fifth batch

- State: `COMPLETED`, exit code `0:0`, elapsed `00:01:20`, MaxRSS `188032K`, node `aisa-gpuB02`.
- Maven package and JUnit: passed.
- `reference/tests.py`: passed, 173/173.
- `reference/paper/test_review_regressions.py`: passed, 20/20, including successful/timeout/nonzero Java plan handling, temporary-query cleanup, and E8 fail-closed status.
- Real Java flat-plan smoke and `reference/factor_demo.py`: passed.
- `reference/quick_verify.py`: passed, including fresh Java circuit construction, PQE CLI, composition differential, and Skolem round trip.
- Log: `/mnt/nfs/home/st196528/research/sparqlcirc/reproduction/logs/sc-review-fix5-47051.log`.

### Slurm job 47052 — sixth batch, frozen-plan guard exposed baseline drift

- State: `FAILED`, exit code `1:0`, elapsed `00:01:18`, MaxRSS `115644K`, node `aisa-gpuB02`.
- Maven package passed; the newly inserted frozen-plan guard then failed before the Python suites were reached.
- Failure: `engine/verify/plan-identity.sh` reported that the committed plan baseline already differs from the current planner, including C2 factored changing from 26 to 17 CONSTRUCT steps.
- Interpretation: the new CI wiring is mechanically correct, but cannot be enabled without either reverting the planner drift or explicitly approving and regenerating the measured baseline. Neither action is judgment-free.
- Correction: revert only the new CI invocation and its static assertion; retain the documentation and data-driven figure fixes, then verify those separately.
- Log: `/mnt/nfs/home/st196528/research/sparqlcirc/reproduction/logs/sc-review-fix6-47052.log`.

### Slurm job 47053 — sixth batch without the deferred baseline decision

- State: `COMPLETED`, exit code `0:0`, elapsed `00:01:20`, MaxRSS `114996K`, node `aisa-gpuB02`.
- Maven package and JUnit: passed.
- `reference/tests.py`: passed, 173/173.
- `reference/paper/test_review_regressions.py`: passed, including current factored-default documentation, the complete final-figure regeneration command, and the data-driven E11 footer.
- Real Java flat-plan smoke and `reference/factor_demo.py`: passed.
- `reference/quick_verify.py`: passed, including fresh Java circuit construction, PQE CLI, composition differential, and Skolem round trip.
- Log: `/mnt/nfs/home/st196528/research/sparqlcirc/reproduction/logs/sc-review-fix6b-47053.log`.

### Slurm job 47054 — seventh batch

- State: `COMPLETED`, exit code `0:0`, elapsed `00:02:25`, MaxRSS `8012560K`, node `aisa-gpuB02`.
- Maven package and JUnit: passed.
- Deep Java semantic suite (`-Dsparqlcirc.deepSemantics=true`): passed.
- `reference/tests.py`: passed, 173/173.
- `reference/paper/test_review_regressions.py`: passed.
- Real Java flat-plan smoke and `reference/factor_demo.py`: passed.
- `reference/quick_verify.py`: passed, including fresh Java circuit construction, PQE CLI, composition differential, property-path checks, and Skolem round trip.
- Log: `/mnt/nfs/home/st196528/research/sparqlcirc/reproduction/logs/sc-review-fix7-47054.log`.

### Slurm job 47057 — eighth batch, first compilation attempt

- State: `FAILED`, exit code `1:0`, elapsed `00:00:08`, node `aisa-gpuB02`.
- Failure stage: Java test compilation, before any test ran.
- Cause: the new quoted-triple regression stored the string returned by the existing `bindingsOf` helper in a `Set<Value>`.
- Correction: use `Set<String>`, matching the helper's established return type; production code is unchanged.
- Log: `/mnt/nfs/home/st196528/research/sparqlcirc/reproduction/logs/sc-review-fix8-47057.log`.

### Slurm job 47058 — eighth batch, first generated-query execution

- State: `FAILED`, exit code `1:0`, elapsed `00:00:14`, node `aisa-gpuB02`.
- Maven compilation passed; JUnit reached generated-query parsing.
- Failure: the RDF-star fallback added three nested function calls but only two additional closing parentheses, leaving the outer `IF` unclosed in every generated term-identity expression.
- Correction: close all `STR`, `SHA256`, `CONCAT`, and four nested `IF` calls in both flat and factored generators before rerunning the unchanged semantic suite.
- Log: `/mnt/nfs/home/st196528/research/sparqlcirc/reproduction/logs/sc-review-fix8b-47058.log`.

### Slurm job 47059 — eighth batch with valid term-identity expressions

- State: `COMPLETED`, exit code `0:0`, elapsed `00:02:22`, MaxRSS `8359240K`, node `aisa-gpuB02`.
- Maven package and JUnit: passed.
- Deep Java semantic suite (`-Dsparqlcirc.deepSemantics=true`): passed.
- `reference/tests.py`: passed, 173/173.
- `reference/paper/test_review_regressions.py`: passed, including RDF-term row identity and both process-group deadline regressions.
- `reference/factor_native_test.py` and PQE CLI loading: passed.
- `reference/quick_verify.py`: passed, including fresh Java circuit construction, PQE CLI, composition differential, property paths, and Skolem round trip.
- Log: `/mnt/nfs/home/st196528/research/sparqlcirc/reproduction/logs/sc-review-fix8c-47059.log`.

### Slurm job 47060 — ninth batch, first NPCS MINUS assertion

- State: `FAILED`, exit code `1:0`, elapsed `00:00:09`, node `aisa-gpuB02`.
- Maven compilation passed and the focused regression executed.
- Failure: the test expected a semantically removed left row to be absent, but NPCS intentionally retains candidate rows and represents removal in the emitted `⊖` provenance polynomial.
- Correction: assert the NPCS contract directly: a disjoint left branch has no `⊖`, and a disjoint right branch contributes no token to the overlapping branch's subtrahend.
- Log: `/mnt/nfs/home/st196528/research/sparqlcirc/reproduction/logs/sc-review-minus-47060.log`.

### Slurm job 47061 — ninth batch, corrected focused assertion

- State: `COMPLETED`, exit code `0:0`, elapsed `00:00:07`, node `aisa-gpuB02`.
- Focused test `CircuitRewriterTest#npcsMinusGuardsEachHeterogeneousUnionBranch`: passed for heterogeneous UNIONs on both MINUS sides.
- Log: `/mnt/nfs/home/st196528/research/sparqlcirc/reproduction/logs/sc-review-minus2-47061.log`.

### Slurm job 47062 — ninth batch, full regression

- State: `COMPLETED`, exit code `0:0`, elapsed `00:02:17`, MaxRSS `9223428K`, node `aisa-gpuB02`.
- Maven package and JUnit: passed.
- Deep Java semantic suite (`-Dsparqlcirc.deepSemantics=true`): passed.
- `reference/tests.py`: passed, 173/173.
- Review regressions, native factoring, PQE CLI loading, and `reference/quick_verify.py`: passed.
- Log: `/mnt/nfs/home/st196528/research/sparqlcirc/reproduction/logs/sc-review-fix9-47062.log`.

### Slurm job 47063 — tenth batch, first optional-path wrapper check

- State: `FAILED`, exit code `1:0`, elapsed `00:00:10`, node `aisa-gpuB02`.
- Maven compilation passed and the focused semantic regression executed.
- Failure: the first identity-projection check compared RDF4J's raw binding-name sets. The `p?` union's one-hop branch includes an anonymous constant-predicate name such as `_const_...`, while the parser-generated projection intentionally removes that internal name.
- Correction: require every projected user binding to keep the same source/target name and compare it with `scopeOf` (which excludes anonymous/value variables), rather than with the raw internal binding-name set. A real scope-changing or renaming projection is still not unwrapped.
- Log: `/mnt/nfs/home/st196528/research/sparqlcirc/reproduction/logs/sc-review-popt-47063.log`.

### Slurm job 47065 — tenth batch, second optional-path wrapper check

- State: `FAILED`, exit code `1:0`, elapsed `00:00:09`, node `aisa-gpuB01`.
- Maven compilation passed and the focused semantic regression executed.
- Failure: in this RDF4J version an identity `ProjectionElem` stores a null legacy target name and exposes the effective target through `getName()`. Comparing source to the nullable legacy target therefore rejected every parser-generated `p?` projection.
- Correction: compare `getName()` with the source name; an `AS` rename still differs and remains visible to the normal subquery guard.
- Log: `/mnt/nfs/home/st196528/research/sparqlcirc/reproduction/logs/sc-review-popt2-47065.log`.

### Slurm job 47066 — tenth batch, stale compiled class after restaging

- State: `FAILED`, exit code `1:0`, elapsed `00:00:05`, node `aisa-gpuB01`.
- Failure: the focused test again reached the old `Distinct` rejection even though the corrected source had been copied immediately before submission.
- Diagnosis: after the job, `engine/target/classes/npcs/circuit/CircuitRewriter.class` still had timestamp `05:44:10` and `javap` showed the previous `getSourceName()`/nullable-`getTargetName()` comparison, while the source timestamp was `05:45:51` and contained the corrected `getName()`/`getSourceName()` comparison. The run therefore exercised stale bytecode during the immediate NFS restage/submission window, not the corrected condition.
- Correction: restage the unchanged corrected source, allow the shared filesystem state to settle, and rerun the focused Maven test as a new Slurm job.
- Log: `/mnt/nfs/home/st196528/research/sparqlcirc/reproduction/logs/sc-review-popt3-47066.log`.

### Slurm job 47067 — tenth batch, first materialized zero-length rows

- State: `FAILED`, exit code `1:0`, elapsed `00:00:10`, node `aisa-gpuB01`.
- Maven compiled the corrected wrapper recognition and the focused test reached semantic comparison.
- Failure: with only the `q(n0,z)` token present, the circuit incorrectly returned zero-length rows for `y = n1`, `n2`, and `z` as well as `n0`.
- Cause: `zeroLengthRows` keyed its private row and occurrence `Plus` gate with the exported variables (`?x`, `?y`) before binding those variables to the internal endpoint `?u`. Every graph term therefore shared one unbound-key row; its multiple value properties cross-producted when the relation was read.
- Correction: bind every exported zero-length endpoint column to `?u` before computing the row and gate identifiers. Each term now has one content-addressed row, while one source token may still witness both of its endpoint-occurrence gates.
- Log: `/mnt/nfs/home/st196528/research/sparqlcirc/reproduction/logs/sc-review-popt4-47067.log`.

### Slurm job 47068 — tenth batch, non-vacuity guard

- State: `FAILED`, exit code `1:0`, elapsed `00:00:10`, node `aisa-gpuB01`.
- The generated circuits matched RDF4J for every tested world; the harness then rejected the MINUS shape because it had no answer in any world.
- Cause: `?x :q ?z MINUS { ?x :p? ?y }` necessarily removes every left row under the project's occurrence-based zero-length semantics: the left `:q` fact itself proves that `?x` occurs, hence the right zero-hop branch always matches.
- Correction: make the right operand `{ ?x :p? ?y . ?y :r :w }` and add one optional `:r` fact. The fixture now has worlds where the left row survives and worlds where the zero-hop match removes it, so the MINUS comparison is non-vacuous without changing production code.
- Log: `/mnt/nfs/home/st196528/research/sparqlcirc/reproduction/logs/sc-review-popt5-47068.log`.

### Slurm job 47069 — tenth batch, focused semantic verification

- State: `COMPLETED`, exit code `0:0`, elapsed `00:00:08`, node `aisa-gpuB01`.
- Focused test `CircuitSemanticsTest#optionalPathAtomsComposeWithEverySupportedOperator`: passed.
- Coverage: JOIN, FILTER, OPTIONAL, and MINUS; flat and factored construction; Standard and RDF-star reification; every possible world compared answer-by-answer with RDF4J.
- Log: `/mnt/nfs/home/st196528/research/sparqlcirc/reproduction/logs/sc-review-popt6-47069.log`.

### Slurm job 47070 — tenth batch, full regression

- State: `COMPLETED`, exit code `0:0`, elapsed `00:02:14`, MaxRSS `9653576K`, node `aisa-gpuB01`.
- Maven package and JUnit: passed.
- Deep Java semantic suite (`-Dsparqlcirc.deepSemantics=true`): passed.
- `reference/tests.py`: passed, 173/173.
- `reference/paper/test_review_regressions.py`: passed, 24/24.
- Native factoring tests and PQE CLI loading: passed.
- `reference/quick_verify.py`: passed, including WMC, fresh Java circuit construction, PQE CLI, composition differential, property paths, and Skolem round trip.
- Log: `/mnt/nfs/home/st196528/research/sparqlcirc/reproduction/logs/sc-review-fix10-47070.log`.
