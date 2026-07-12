# Canonical timing table (post-`1e67021`, 5-run) — the ONE table to cite

**This is the single authoritative source for every headline timing number (R8.1).** Any timing quoted
in the paper / EVALUATION / TECHREPORT must come from here. Older tables in `G3_RESULTS.md`,
`G4_RESULTS.md`, and `G2a_RESULTS.md` are **pre-`1e67021` and superseded** — collected in
`HISTORICAL_TIMINGS.md`, marked *do not cite*. No query appears with two different totals across the repo.

## Provenance of these numbers

- **Jar:** engine @ `1e67021` (term-type-aware gate identity + `urn:circuit:binding`); rebuilt
  2026-07-12 18:33. ⚠ **`7882a1e` (property-path state isolation) later changed the PATH engine** —
  reach/base gate IRIs, an added `c:rpath` triple per reach gate, and every path match query. The **BGP
  rows below (watdiv-Sstar, tpch-Q3) are unaffected and current**; the **wikidata-WDpath row predates
  `7882a1e`** (†) and must be re-measured on the isolation-fixed jar before it is cited — circuit
  topology is unchanged so compile/WMC should hold, but construct time + triple count shift slightly.
- **Protocol (G4):** 1 warm-up + **5 timed runs**, report **median [min–max]**, 300 s timeout.
- **Environment:** `aisa-mgmt01.ki.uni-stuttgart.de`, 32 cores, 131 GB; **shared** HPC box (other users'
  jobs logged, not killable); **warm** cache (repos loaded, daemons up) — steady-state, not cold-start.
  GraphDB `-Xmx60g`. Absolute wall-clock is order-of-magnitude; breakdown/relative claims are the robust ones.
- Harnesses: `g3_pqe_latency.py` (breakdown), `g4_instances.py` (per-instance), `g4_rigor.py` (protocol).

## Ours — end-to-end PQE (construct → shared ROBDD compile → WMC), all answers

| query | dataset | answers | construct | compile | WMC | **total (median [min–max])** |
|---|---|--:|--:|--:|--:|--:|
| watdiv-Sstar       | WatDiv 32.7 M reified         |     2 |   10 ms |    2 ms |  0 ms | **12 ms [11–12]** |
| tpch-Q3 (naryrel)  | TPC-H SF 0.01 (1.26 M)        | 14 908 | 2598 ms |  148 ms | 36 ms | **2.78 s [2.78–2.80]** |
| wikidata-WDpath **†** | Wikidata 2.13 B (`P279+`, G1) |    16 | 2308 ms | **5750 ms** | 10 ms | **8.04 s [7.69–8.14]** |

- **Tree/star PQE is construct-dominated; compile+WMC is near-free.** TPC-H Q3: compile+WMC = 184 ms of
  2.78 s (≈ 7 %) for all 14 908 answers — the stage the how-provenance baselines lack. Star: 2 ms.
- **The recursive path is compile-dominated (5.75 s of 8.04 s).** Post-`1e67021` un-merges the reach
  states a STR-collision had collapsed, so WD-path is the *correct, larger, reconvergent* circuit; the
  fixed-order OBDD compile is now the cost. This is the case an **order-robust d-DNNF** (G6/d4 follow-up)
  targets — WMC stays authoritative via **OBDD + PWE** (G6), d4 is compiled-size-only (R8.5).

## Strong baseline — ProvSQL (modified PostgreSQL), same TPC-H Q3

| query | scale | answers | ProvSQL PQE (median [min–max]) | ours (above) |
|---|---|--:|--:|--:|
| tpch-Q3 | SF 0.01 | 14 908 | **1.03 s [1.02–1.07]** | 2.78 s |

Warm, 5-run (`g4_instances.py`, mktsegment=BUILDING). **ProvSQL is faster** (see G2a framing: comparable
order of magnitude; our contribution is the *same* exact PQE on a **stock, unforked** SPARQL engine over a
**broader fragment**, not a latency win). Our post-fix total includes `c:binding` answer-recovery metadata.

## Instance breadth (R8.1 "≥3–5 instances/shape") — see `g4_instances.csv`

- **TPC-H Q3 × 5 mktsegments** (ours + ProvSQL): ours mean **2237 ± 332 ms**, ProvSQL **858 ± 110 ms**;
  ProvSQL faster on all 5; within-instance sd ≤ 2 %.
- **WatDiv S-star × 5 users**: 20 → 659 ms, tracking answer count; within-instance sd 0–8 ms.

## Reconvergent query + SF 0.1 end-to-end (R8.3) — see `R8_3_RESULTS.md`

`SELECT ?cust WHERE { ?cust c_mktsegment "BUILDING" . ?order o_custkey ?cust }` — per-answer provenance
`⊕ₖ(cust⊗orderₖ)` with a **shared** cust token (reconvergent; p ∈ [0.375, 0.5], not Q3's 0.125).

| query | scale | answers | ours total ‡ | ProvSQL total ‡ |
|---|---|--:|--:|--:|
| Qrecon (reconvergent) | SF 0.01 |  247 | 322 ms | 770 ms |
| Qrecon (reconvergent) | SF 0.1  | 2086 | 2.76 s | 6.45 s |

A naive per-answer product-sum would exceed 1 for 243/247 (SF 0.01) and 2058/2086 (SF 0.1); the shared
circuit (and ProvSQL) get it right. **‡ These totals are single observations, not the 5-run protocol** —
treat the ours-vs-ProvSQL speed gap as provisional until re-run under the protocol above.
**Probability parity:** `r8_3_reconvergent.py` now verifies ours **== the closed form** `0.5·(1−0.5^K)`
per answer (definitive, key-independent) **and** compares the sorted per-answer probability list against
ProvSQL's `probability(provenance())` rows (`max_abs_error < 1e-6`). The earlier artifact only read
ProvSQL's `count(*)`, so the "ours == ProvSQL probabilities" claim was **not** established by it — the
corrected script establishes it on the next endpoint re-run.

## Still open

- TPC-H Q3 **SF 0.1 / SF 1** full-pipeline ours: Q3 SF 0.1 stays **construction-only** (125 154-answer
  circuit = pure-Python compile bottleneck; native/d4 compile is the follow-up). SF 1 not loaded.
  (R8.3's SF 0.1 end-to-end is delivered via Qrecon above, whose smaller circuit compiles.)
- **† Re-measure `wikidata-WDpath` on the `7882a1e` isolation-fixed jar** (R8.1) — the current row was
  built before the per-path fingerprint / `c:rpath` change; topology is unchanged so the total should hold,
  but it is not literally on current HEAD until re-run.
- **WatDiv 200 M: dropped** — the 2014 generator segfaults on the modern toolchain; 10 M / 100 M stand.
