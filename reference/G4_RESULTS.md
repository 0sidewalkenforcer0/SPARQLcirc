# G4 — statistical-rigor pass on the headline timing numbers

The external review flagged that our headline *timings* were single-run on a shared machine. G4 fixes
the protocol for every number a reader would cite and re-measures under it. `g4_rigor.py` → `g4_rigor.csv`.

## Protocol

- **1 warm-up + 5 timed runs** per number; report **median [min–max]** and **mean ± sd**.
- Uniform **300 s** timeout; single psql session for ProvSQL (query time via `\timing`, not process
  start-up); the ours pipeline reuses the **G3** harness verbatim (construct → shared ROBDD → WMC).
- **Environment logged** with every run (below). Functional & size results (byte-identity, WMC==PWE,
  circuit sizes) are unchanged and NOT re-run — only *timings*.

## Environment (logged at run time)

```
host      : aisa-mgmt01.ki.uni-stuttgart.de      (shared HPC login/management node)
cpu cores : 32       mem : 131 GB total, ~43 GB available
loadavg   : 0.27 / 1.19 / 1.50   (1/5/15 min — lightly loaded)
gdb heap  : -Xms60g -Xmx60g       (GraphDB, ~1 core busy)
concurrent: other users' VSCode-server + a PyCharm + an ML eval (logged, not killable — shared box)
cache     : WARM (repos loaded, daemons up) — this is steady-state, NOT a cold-start measurement
```

This is a **shared** box, so absolute wall-clock is order-of-magnitude; the **breakdown** and **relative**
claims (compile+WMC ≪ construct; ProvSQL vs ours same order) are what the variance below shows to be robust.

## Results

| system | query | dataset | answers | construct | compile | WMC | **total (median [min–max])** |
|---|---|---|--:|--:|--:|--:|--:|
| ours    | watdiv-Sstar    | WatDiv 32.7 M          |     2 | 10 ms  | 1 ms   | 0 ms  | **12 ms [12–17]** |
| ours    | tpch-Q3         | TPC-H 1.26 M           | 14 908 | 1471 ms | 149 ms | 35 ms | **1654 ms [1637–1672]** (sd 13) |
| ours    | wikidata-WDpath | Wikidata 2.13 B (P279+)|    16 | 2125 ms | 1 ms   | 0 ms  | **2127 ms [2017–2132]** (sd 49) |
| ProvSQL | tpch-Q3         | TPC-H (SF 0.01)        | 14 908 | — | — | — | **1091 ms [1048–1184]** (sd 53) |

## Findings

- **The headline numbers are stable and citable.** Over 5 timed runs the spread is tight: tpch-Q3 total
  **1654 ms, sd 13 (±0.8 %)**; WD-path **2127 ms, sd 49 (±2.3 %)**; ProvSQL Q3 **1091 ms, sd 53 (±4.8 %)**.
  Even on a shared box the variance is small enough that the claims do not hinge on a lucky run.
- **PQE is construct-dominated — confirmed under rigor.** For tpch-Q3 the **compile + WMC** stage (the
  one the how-provenance baselines lack) is **184 ms of 1654 ms ≈ 11 %**, and its own variance is
  negligible (compile sd 3 ms, WMC sd 0.4 ms). G3's "compile+WMC is near-free on the shared circuit"
  survives the 5-run pass: it is both small *and* stable.
- **Rigor caught an over-claim (the point of G4).** G2a's first draft cited a **cold** ProvSQL
  first-call (~3.6 s) and concluded "ours ≈ 2× faster." Warm/steady-state ProvSQL Q3 is **1.05–1.09 s** —
  actually **~1.5× faster than us** (1.65 s). Corrected framing (G2a, ROUND 7): **comparable latency, no
  speed win**; ProvSQL is a peer that is modestly faster warm, and our contribution is doing the *same*
  exact PQE on a **stock, unforked SPARQL engine** over a **broader fragment** (paths / full SPARQL).
  SF 0.1 likewise corrected: ProvSQL warm ≈ 9.8 s, not the cold 29.4 s.
- **Property paths at KG scale are real.** WD-path on the **2.13 B-triple** Wikidata graph is a stable
  **2.13 s** end-to-end (5 runs, sd 49 ms) — construct-dominated, compile+WMC ~1 ms. This is the fragment
  NPCS/SPARQLprov cannot express and ProvSQL (relational) does not address.

## Caveats

- **Warm-cache steady-state**, by design (we cite steady-state latency, and cold-vs-warm was the very
  thing that produced the G2a over-claim). A cold-start column would be a different, larger set of
  numbers dominated by JVM/engine/plan-cache warm-up.
- Shared machine (env logged). For a fully quiescent citable table, re-run on an isolated node; the
  *relative* results (breakdown %, ProvSQL-vs-ours ordering) are unlikely to change — they held here
  despite background load.
- E3 (construction scaling, WatDiv 10 M/100 M) was **already** 5-run-averaged and is not re-listed here;
  G4 covers the numbers that were single-run (G3 end-to-end + the G2a ProvSQL head-to-head).
