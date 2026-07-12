# Canonical timing table (current HEAD, 5-run) — the ONE table to cite

**Single authoritative source for every headline timing (R8.1).** Regenerated on **current HEAD** under
the corrected timer boundaries. Older tables in `G3_RESULTS.md`, `G4_RESULTS.md`, `G2a_RESULTS.md` are
superseded → `HISTORICAL_TIMINGS.md` (do not cite). No query appears with two different totals across the repo.

## Provenance of these numbers

- **Jar:** engine @ current HEAD — incl. **`PathIsoSeq`** (per-path reach/base-gate fingerprint isolation,
  `7882a1e`) + `1e67021` (term-type-aware identity + `c:binding`); rebuilt **2026-07-12 23:56**.
- **Timer boundaries (`90c3c3c`):** *construct* = engine CONSTRUCTs + RDF parse + answer recovery;
  *compile* = **variable ordering + ROBDD build/init**; *WMC* = weighted count. Wider than the pre-`90c3c3c`
  split (which is why Q3's compile jumps from 148 ms to 3.3 s — the global variable ordering is now counted).
- **Protocol (G4):** 1 warm-up + **5 timed runs**, median [min–max], 300 s timeout. `g4_rigor.py` → `g4_rigor.csv`.
- **Environment:** `aisa-mgmt01`, 32 cores, 131 GB; **shared** HPC box (jobs logged); **warm** cache;
  GraphDB `-Xmx60g`. Absolute wall-clock order-of-magnitude; breakdown/relative claims are the robust ones.

## Ours — end-to-end PQE (construct → shared ROBDD compile → WMC), all answers

| query | dataset | answers | construct | compile | WMC | **total (median [min–max])** |
|---|---|--:|--:|--:|--:|--:|
| watdiv-Sstar       | WatDiv 32.7 M reified         |     2 |   10 ms |    2 ms |  0 ms | **12 ms [11–12]** |
| tpch-Q3 (naryrel)  | TPC-H SF 0.01 (1.26 M)        | 14 908 | 3080 ms | **3300 ms** | 36 ms | **6.40 s [6.40–6.44]** |
| wikidata-WDpath    | Wikidata 2.13 B (`P279+`, G1) |    16 | 2144 ms |    1 ms |  0 ms | **2.14 s [2.10–2.21]** |

- **`PathIsoSeq` fixes the recursive-path compile blowup.** WD-path is now **2.14 s, compile ~1 ms** (was
  8.04 s / compile 5.75 s pre-isolation): the per-path fingerprint keeps reach/base gates from accumulating
  across paths, so cones are **1–20 tokens** (not 19–233) and the fixed-order OBDD compiles trivially.
  16 answers, **OBDD==PWE 15/15** — correctness holds. The order-robust-d4 motivation *for paths* is
  largely removed (it now applies to Q3's ordering step, below — not to paths).
- **TPC-H Q3 compile is now ordering-dominated — honest under the corrected boundaries.** compile = **3.3 s
  of 6.4 s** is the pure-Python **variable ordering** over ~45 k tokens (counted in compile per `90c3c3c`),
  *not* the ROBDD build and *not* the weighted count (WMC 36 ms). This is a pure-Python-implementation cost
  (a native compiler / linear ordering heuristic removes it); it **supersedes** the earlier "compile+WMC
  near-free" phrasing for Q3. WMC itself is tiny everywhere (≤ 36 ms) — the weighted count is never the cost.

## Strong baseline — ProvSQL (modified PostgreSQL), same TPC-H Q3

| query | scale | answers | ProvSQL PQE (median [min–max]) | ours (above) |
|---|---|--:|--:|--:|
| tpch-Q3 | SF 0.01 | 14 908 | **pending corrected 5-run** | 6.40 s |

The previous 1.06 s G4 row is retired: its `count(*)` wrapper did not consume the projected probability and
could let PostgreSQL prune `probability_evaluate`. `g4_rigor.py` now selects `count(*),sum(p)`; re-run its
ProvSQL row before making a Q3 speed claim. The independent G2a `CREATE TEMP TABLE ... probability_evaluate`
artifact did consume probabilities, but it used different historical timer boundaries and is not substituted
into this canonical table.

## Instance breadth (R8.1 "≥3–5 instances/shape") — see `g4_instances.csv`

- **TPC-H Q3 × 5 mktsegments:** both ours and ProvSQL instance timings are pending the corrected
  `g4_instances.py` re-run (the old ProvSQL target could be pruned).
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
- **✓ `wikidata-WDpath` re-measured on the `PathIsoSeq` jar** (done): total dropped **8.04 s → 2.14 s**
  (compile 5.75 s → 1 ms) — the isolation fix shrinks the reconvergent cones (19–233 → 1–20 tokens), so the
  total did *not* hold; the main table above is the current-HEAD value. 16 answers, OBDD==PWE 15/15.
- **Instance breadth (`g4_instances.py`) and Qrecon (R8.3) rows still show pre-`90c3c3c`-boundary numbers**
  — a light re-run under the new boundaries is the remaining refresh (the main 3-row table + ProvSQL are
  current-HEAD).
- **WatDiv 200 M: dropped** — the 2014 generator segfaults on the modern toolchain; 10 M / 100 M stand.
