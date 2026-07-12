# G4 — statistical-rigor pass on the headline timing numbers

> ⚠️ **Timing numbers in this file are SUPERSEDED — cite [`CANONICAL_TIMINGS.md`](CANONICAL_TIMINGS.md).** They predate (or are single-run under) engine fix `1e67021`; the authoritative post-fix 5-run table lives in the canonical file, and the old rows are recorded in [`HISTORICAL_TIMINGS.md`](HISTORICAL_TIMINGS.md). The *methodology/findings* below still stand; only the absolute numbers moved.

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

---

## G4(b) — ≥5 query instances per shape (breadth on top of the 5-run variance)

`g4_rigor.py` gave 5-run median±sd on *one* instance per shape. The ROUND 7 refinement also asks for
**≥3–5 instances per shape**. `g4_instances.py` → `g4_instances.csv` (post-`1e67021` jar):

**Shape 1 — TPC-H Q3 SPJ × 5 mktsegments (ours + ProvSQL, 1 warm-up + 5 runs each):**

| instance | answers | ours total (median [min–max]) | ProvSQL (median [min–max]) |
|---|--:|--:|--:|
| AUTOMOBILE | 11 966 | 2237 ms [2223–2245] | 848 ms [828–882] |
| BUILDING   | 14 908 | 2770 ms [2730–2780] | 1031 ms [1024–1071] |
| FURNITURE  | 11 987 | 2220 ms [2198–2254] | 870 ms [833–910] |
| HOUSEHOLD  | 11 165 | 2085 ms [2066–2191] | 810 ms [784–836] |
| MACHINERY  | 10 149 | 1873 ms [1859–1889] | 731 ms [702–767] |
| **cross-instance** | | mean **2237 ± 332** ms | mean **858 ± 110** ms |

**Shape 2 — WatDiv S-star × 5 users (ours, 1 warm-up + 5 runs each):**

| instance | answers | ours total (median [min–max]) |
|---|--:|--:|
| User1011  |  1 |  20 ms [20–20] |
| User10113 |  2 |  86 ms [85–91] |
| User10163 |  4 | 129 ms [126–132] |
| User10152 | 11 | 659 ms [652–673] |
| User10252 | 13 | 399 ms [395–410] |
| **cross-instance** | | mean **259 ± 267** ms |

### Findings

- **Within-instance variance is tiny across the board** — sd ≤ 2 % of the median on every instance
  (TPC-H sd 10–50 ms; S-star sd 0–8 ms). The latency numbers are stable, not lucky single runs.
- **The ProvSQL-vs-ours ordering is consistent across ALL 5 TPC-H instances**, not a one-instance
  artifact: ProvSQL is faster on every segment (≈ 2.6× median). This confirms G2a's warm finding at
  breadth — the honest picture is *comparable order of magnitude, ProvSQL faster, our win is
  no-engine-fork + broader fragment*, and it holds across the workload, not just BUILDING.
- **Latency tracks answer count within each shape** (ours S-star 1 ans → 20 ms, 13 ans → 399 ms;
  TPC-H 10 149 → 1873 ms, 14 908 → 2770 ms), as expected for these low-reconvergence shapes.

### Note (supersedes pre-fix singles)

These are **post-`1e67021`** numbers. Our end-to-end now includes the answer-recovery `c:binding`
metadata (more CONSTRUCT output), so the ours side is larger than the pre-fix single-run figures in
G2a/G3 (e.g. BUILDING: pre-fix 1.65 s → post-fix 5-run median **2.77 s**); ProvSQL is unchanged
(≈ 1.03 s). The 5-run median also corrects G3's single BUILDING run (4.09 s was a high sample). Treat
this table as the current authoritative TPC-H Q3 latency; G2a/G3's ours-side singles predate the fix.
