# Reproducing the SPARQL_circ results

This guide reproduces every claim in the paper from the code in this repository.
The core pipeline (build a circuit, compile it, weighted-model-count it) is
**zero-dependency** and runs natively on macOS (Apple Silicon) and Linux; the
external baselines (GraphDB, PySDD, d4) are optional and clearly marked.

## Prerequisites

| For | Need |
|---|---|
| `engine/` (the rewriter) | Java 11+ and Maven (dependencies fetched from Maven Central) |
| `reference/` core (circuit, ROBDD, WMC, factored, benchmarks) | Python 3.9+ (**standard library only**) |
| SDD baseline (optional) | `pip install pysdd` (Apache-2.0, arm64/x86 wheels) |
| d4 d-DNNF baseline (optional) | a Linux/x86 box — see `reference/D4_ON_LINUX.md` |
| Deployed-engine + real-KG runs (optional) | GraphDB 10.x running on `localhost:7200` |

## 1. Quick verify (~2 min, no external tools)

```bash
# build the rewriter
cd engine && mvn -q package            # -> target/npcs-rewrite.jar

# rewrite a query and let a stock in-memory engine materialize the circuit
java -cp target/npcs-rewrite.jar npcs.circuit.CircuitRun \
     Standard examples/circuit/drug.reified.ttl examples/circuit/drug3hop.sparql \
     2>plan.txt >circuit.nt            # circuit.nt = 25-triple circuit (19 core gates + 6 c:binding recovery; paper Fig. 2)

# compile + WMC + cross-check against possible-world enumeration
cd ../reference
python3 verify_all.py                  # expect: ... ALL OK
python3 wmc.py                         # exact WMC == enumeration on the example circuits
```

`verify_all.py` printing `ALL OK` establishes: circuits are correct, gate IDs are
canonical (no congruent ⊗), and WMC == possible-world enumeration.

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

- The default compiler `reference/compile_bdd.py` is a self-contained ROBDD — it needs
  no native libraries and runs on Apple Silicon and x86 alike.
- `d4` bundles the PATOH partitioner, which is x86_64-only; run the d4 figure on a
  Linux/x86 machine (`D4_ON_LINUX.md`). PySDD stands in on Apple Silicon.
