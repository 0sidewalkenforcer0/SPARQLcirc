# Re-validation campaign — protocol, coverage, and live status

Systematic re-measurement of every number in the paper's evaluation section, run on the
machine the paper describes. This file is the protocol of record: it states what was run,
under which budget, and what each cell is allowed to prove. Started 2026-08-01, repo at
`ba08ec0`.

## Protocol

| Parameter | Value | Note |
|---|---|---|
| Per-build budget | **600 s** | matches the paper's stated construction budget |
| Warm-ups / measured runs | **1 / 1** | the paper's own protocol is 1 / 5 |
| `--timeout` (per **cell**) | **1200 s** | = 2 × 600 s; the harness deadline covers *all* executions in a cell |
| Compilation attempt | **120 s** | unchanged, and consistent across all three sources |
| Timed-out cells | recorded, then re-run **unbounded** in Phase G | yields the complete construction time |
| d4 | **d4v2** (`tools/d4v2/scripts/d4_static`, `D4V2=1`) | the paper cites d4v2 |

### What the budget change means

The published matrix ran under `FORMAL_TIMEOUT = 500` covering 1 warm-up + 5 runs — **six**
executions inside one 500 s cell deadline, so each build effectively had ~83 s. The paper's
prose says 600 s. Neither reading reproduces the other, and the 600 s figure was never what
executed. This campaign gives each build a real 600 s, which is roughly **7× more permissive
than what produced the published numbers**. More cells will therefore build. Build/fail counts
from this campaign are *not* directly comparable to the paper's 27/30 and 24/30 until they are
re-derived at a matched per-build budget — which the recorded per-build times make possible
without re-running.

Every cell records: actual build time, outcome, and failure reason (timeout / OOM / request-size
/ protocol error).

### Timeout values found in disagreement (resolved here in favour of 600 s)

| Source | Value |
|---|---|
| Paper §5 Setup and the Figure 5 legend | 600 s |
| `paper_construction_matrix.py` `FORMAL_TIMEOUT` | 500 s |
| `reference/experiment_timeouts.py` `QUERY_TIMEOUT_S` | 300 s |
| `docs/REPRODUCE.md` (claims it is what the matrix uses) | 300 s |

`docs/REPRODUCE.md` contradicts the harness it documents. Both it and the paper need to be
brought to whichever value is finally adopted.

## Dataset ground truth (verified against the running store, not against documentation)

| Repository | Triples | Meaning |
|---|---|---|
| `watdivbase` | 10,916,457 | WatDiv 10M, base |
| `watdiv` | 32,749,371 | the same, Standard-reified (exactly 3×) |
| `watdivstar10m` | 10,916,457 | the same, RDF-star (exactly 1×) |
| `watdiv100mbase` | 108,997,714 | WatDiv 100M, base |
| `watdiv100m` | 326,993,142 | Standard-reified (exactly 3×) |
| `wdpaths` | 60,460,482 | P279/P131, Standard-reified over 20,153,494 facts |
| `wdreal` | 62,447,154 | P106/P27 |
| `wdstatements` | 40,306,982 | |
| `wikidata` | 2,126,677,196 | statement-reified, **108 predicates only**, = 1,063,338,598 facts |
| `tpch001` / `tpch01` / `tpch03` | 1,255,420 / 12,532,869 / 37,570,726 | plain, no reification |

`wdpaths` carried 12,284 triples of circuit residue from an earlier run; removed before this
campaign (verified back to 60,460,482). Every other store was already clean.

## Coverage — the 36 claims of the evaluation section

| ID | Claim | Phase |
|---|---|---|
| V1 | WMC == exhaustive possible worlds, every answer under 20 tuples | A |
| V2 | path-free agreement with ProvSQL | D |
| V3 | paths validated by enumeration only | A, D |
| C1–C8 | compactness: 0.84/0.80 median, F4 183:748, M3 2.6×, RC6 202×, flat-vs-factored 25×/5×/1.7×/3.0× | A |
| C9 | M4 exhausts memory at 100M | F |
| P1 | 13-shape gallery, two engines, one circuit | A |
| P2 | WatDiv templates agree across engines, 27/30 at 10M | B, C |
| P3 | three per-triple encodings give identical circuits | A |
| P4 | 327M → 109M under RDF-star | 0 |
| P5–P11 | construction cost: 3.3×/6.2×, 143×/300×, 6.3×/8.5×, absolutes, 27/30 built, F4/F5 | B |
| P12 | F2 164×, O4 reversed 5×, 6 of 20 reverse | C |
| P13 | Wikidata 31/41 build, median 40 ms, largest 3.05M triples | E |
| P14 | building turns on a fixed subject or object | E |
| R1, R2 | Q7397 2.14 s, Q60 838 ms | D |
| R3 | cyclic subgraph 79 rounds, 6,638 / 12,960 gates | A |
| R4 | friendOf+ fails on an endpoint request-size limit | D |
| R5 | nested closures rejected, not approximated | A |
| T1, T2, T5 | 254 variables / 9.2×10^18 derivations, 211,964 / 375,501 nodes, budget markers | D |
| T3 | min-fill query-graph bounds, 29 at 1 and 1 at 2 | A |
| T4 | minus circuits of 12–13M nodes at 100M | F |
| E1, E2 | six TPC-H skeletons against ProvSQL, closed form 0.5(1−0.5^K) | D |
| E3, E4 | stage split, client-side median 0.48 / 1.34 ms | D, F |
| E5 | per-answer / shared compile ratio 1.27× / 8.4× | D, F |

## Phases

| Phase | Content | Engine |
|---|---|---|
| 0 | store counts, jar rebuild, environment capture, this file | GraphDB (read) |
| A | deterministic units: correctness gate, all of §5.1, T3, R3, R5, P1, P3 | local |
| B | GraphDB 10M B/R/N/C matrix — L,S,F,O,M then C isolated — then the parity gate | GraphDB, exclusive |
| C | Oxigraph 10M matrix and cross-engine byte identity | Oxigraph, exclusive |
| D | e4_sweep, TPC-H against ProvSQL, e11, paths on both stores, g3_pqe_latency | GraphDB + PostgreSQL |
| E | Wikidata 2.13B construction reach | GraphDB |
| F | 100M, entered automatically if the earlier phases finish early | both |
| G | every timed-out or OOM cell re-run unbounded, into its own table | mixed |
| H | reconciliation of all 36 claims, into `docs/CONFORMANCE.md` | — |

Phases run sequentially; timed phases never overlap, so no cell is measured under contention.

## Failure policy

Nothing stops the campaign. Every failure is skipped, recorded, and reported.

- **Cell timeout / OOM / request-size**: record the reason, continue, re-run unbounded in Phase G.
- **Correctness red light**: (a) environment or scaffolding problems are fixed and re-run;
  (b) a real regression is fixed only if `mvn test` **and** `engine/verify/plan-identity.sh` both
  stay green; (c) a fix that would move the plan-identity baseline is **not** applied — accepting
  a new baseline would silently redefine the circuits this campaign exists to check, so it is
  written up and left for a human; (d) a red light that means the *paper* is wrong goes into the
  reconciliation table.
- **GraphDB down**: restart at most twice with the recorded launch parameters, requiring both a
  17-repository listing and a correct `watdivbase` count before continuing; otherwise skip the
  GraphDB-dependent phases.
- **Disk**: below 20 GB, keep running and stop persisting the regenerable circuit cache; on a real
  write failure, reclaim only regenerable artifacts; if still short, downgrade the scale
  (100M → 10M, TPC-H to SF 0.01, Wikidata to the 60M store) and mark those cells. Source data,
  store directories, result CSVs and tracked files are never deleted.

## Environment

- GraphDB 10.7.6 at `-Xmx90g`, `-Dgraphdb.home=workspace/graphdb-home` (the `GDB_HOME`
  environment variable is ignored; the system property is required or the server starts with no
  repositories).
- PostgreSQL with ProvSQL, socket `workspace/pgsock`, **port 5433** — `g2a_provsql.sql` documents
  54320, which is stale. Schemas `g2a`, `g2a1`, `g2a3` hold SF 0.01, 0.1, 0.3.
- Oxigraph 0.5.9 via `tools/images/oxigraph.sif`; indexes `tools/oxi-watdiv` (10M reified),
  `oxi-watdiv-base`, `oxi-watdiv100m`, `oxi-watdiv100m-base`, `oxi-watdiv10m-star`.
- `PCM_FORCE_FLAT=1` is mandatory for the matrix: without it the C method requests factored
  construction, which only supports pure BGPs, and every O and M cell aborts.
- Harness Python is the Miniconda base interpreter, not the `sparqlcirc` environment.
- Storage: workspace is under a 1.0 TB quota with ~189 GB free; compiler intermediates and the
  circuit cache go to local scratch.

## Live status

Updated as phases complete.

| Phase | Status | Result |
|---|---|---|
| 0 | running | |
| A | pending | |
| B | pending | |
| C | pending | |
| D | pending | |
| E | pending | |
| F | pending | |
| G | pending | |
| H | pending | |
