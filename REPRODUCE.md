# Reproducing the SPARQL_circ results

This guide reproduces every claim in the paper from the code in this repository.
Circuit construction and the reference correctness oracle are **zero-dependency**.
The production compile/WMC path uses the native CUDD wheel and runs on macOS
(Apple Silicon) and Linux; external baselines are optional and clearly marked.

## Prerequisites

| For | Need |
|---|---|
| `engine/` (the rewriter) | Java 11+ and Maven (dependencies fetched from Maven Central) |
| `reference/` core (circuit, ROBDD, WMC, factored, benchmarks) | Python 3.9+ (**standard library only**) |
| Production CUDD compile/WMC | Python 3.11+; `pip install -r reference/requirements-production.txt` |
| SDD baseline (optional) | `pip install pysdd` (Apache-2.0, arm64/x86 wheels) |
| d4 d-DNNF baseline (optional) | a Linux/x86 box — see `reference/D4_ON_LINUX.md` |
| Deployed-engine + real-KG runs (optional) | GraphDB 10.x running on `localhost:7200` |

The reference-oracle CI path deliberately performs no `pip install`. Production
CUDD and research dependencies are separated into `requirements-production.txt`
and `requirements-optional.txt`; CI also runs a dedicated native-CUDD job.

For citable performance runs, `reference/experiment_timeouts.py` is the single source of truth:
the query-side budget is **300 s**, and each OBDD or d4/d-DNNF compilation attempt is capped at
**120 s**. Single-shot/legacy query harnesses apply 300 s per execution; the R9.2 B/R/N/C matrix
applies one hard 300 s deadline to the entire method cell (rewrite, warm-ups, measured runs, full
response drains, and all C-plan steps). Short correctness probes and untimed dataset loading may use
different operational watchdogs.

## 1. Quick verify (~2 min, no external services)

```bash
# from the repository root: build the current Java sources
mvn -q -f engine/pom.xml package       # -> engine/target/npcs-rewrite.jar

# one smoke command: reference tests + WMC self-test + live Java/RDF pipeline
python3 reference/quick_verify.py       # expect: QUICK VERIFY ALL OK
```

The smoke runner first exercises the standard-library reference checks and an
offline `pqe.py` CLI regression. It then invokes `CircuitRun`, consumes the
N-Triples emitted by that exact invocation, parses its structured answer bindings,
compiles each fresh answer circuit with the explicit Python oracle, and checks WMC against possible-world
enumeration. Finally it runs the documented `pqe.py --jar ...` command end to end,
so both the fat-JAR dispatcher and the user-facing construction branch are covered.
It does **not** read the checked-in `reference/data/*.circuit.nt` fixtures. Both
`reference/tests.py` and `reference/wmc.py` now exit non-zero on a mismatch or
exception, so the same commands are safe CI gates.

GitHub Actions repeats the standard-library-only Python checks on Python 3.9 and
3.12, runs the CUDD shared/per-root contract on Python 3.11, then performs a clean
Maven build followed by the fresh-circuit smoke path.
No Maven wrapper JAR or generated circuit is committed.

## 2. Reproduce the evaluation tables

All commands run from `reference/`.

| Paper result | Command | Needs | ~time |
|---|---|---|---|
| **Compactness** (shared circuit vs. per-answer strings; `deep-12×2` = 201×) | `python3 bench.py` → `bench.csv` | — | seconds |
| **Non-monotone** correctness (OPTIONAL/MINUS via ⊖ vs. PWE) | `python3 verify_nonmono.py` | — | seconds |
| **Factored vs. flat** construction | `python3 factor_demo.py` | — | seconds |
| **Deployed-engine timings** (GraphDB runs our CONSTRUCT) | `python3 bench_engine.py` → `bench_engine/results.csv` | GraphDB | minutes |
| **Real-KG WatDiv, flat vs. factored** | `WATDIV_NT=/path/to/base.nt python3 watdiv_factor.py` | WatDiv data | ~1 min |
| **Real-KG WatDiv, end-to-end on GraphDB** | see step 3 below | GraphDB + data | minutes |
| **d4 d-DNNF scaling** | `python3 export_cnf.py` then follow `D4_ON_LINUX.md` | Linux/x86 | minutes |

## 3. Real-KG WatDiv end-to-end (optional, needs GraphDB)

```bash
cd reference
# a) reify a WatDiv N-Triples file into statement form
python3 watdiv/reify.py /path/to/base.nt watdiv/base.reified.nt
# b) start GraphDB on localhost:7200, create a repo "watdiv", load watdiv/base.reified.nt
#    (see watdiv/repo.ttl for the repository config)
# c) run the star / path / snowflake shapes through the full pipeline
python3 watdiv_run.py                  # -> build_ms, wmc_ms, sizes per shape
```

See `watdiv/RESULTS.md` for the expected numbers and their interpretation.

## Getting the data (not bundled)

- **WatDiv** — generate with the WatDiv data generator (http://dsg.uwaterloo.ca/watdiv/).
  The paper uses a subset of **51,863 triples** (`base.nt`). Any WatDiv N-Triples file
  works with `watdiv_factor.py` / `reify.py`; set `WATDIV_NT` to its path.
- **GraphDB** — free edition from https://graphdb.ontotext.com/ ; boots on JDK 11+,
  default port 7200. Any SPARQL 1.1 endpoint that accepts CONSTRUCT works — the circuit
  construction uses no engine-specific features.

## Consistency with the original NPCS (optional)

`engine/verify/diff_harness.py` differential-tests our rewriter against the original
NPCS. It requires the original artifact, which is **not** included (see `NOTICE`); set
`NPCS_ORIG_JAR` and `NPCS_QDIR` to run it.

## Hardware notes

- The production compiler is CUDD through `reference/compiler.py`; the CLI defaults
  to one shared manager and accepts `--compile-mode per-root` for isolated managers.
- `reference/compile_bdd.py` is the portable correctness oracle used by smoke tests.
- `d4` bundles the PATOH partitioner, which is x86_64-only; run the d4 figure on a
  Linux/x86 machine (`D4_ON_LINUX.md`). PySDD stands in on Apple Silicon.
