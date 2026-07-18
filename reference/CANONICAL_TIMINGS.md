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
| tpch-Q3 (naryrel)  | TPC-H SF 0.01 (1.26 M)        | 14 908 | 2380 ms | **239 ms** | (in compile) | **2.63 s [2.58–2.66]** |
| wikidata-WDpath    | Wikidata 2.13 B (`P279+`, G1) |    16 | 2144 ms |    1 ms |  0 ms | **2.14 s [2.10–2.21]** |

- **`PathIsoSeq` fixes the recursive-path compile blowup.** WD-path is now **2.14 s, compile ~1 ms** (was
  8.04 s / compile 5.75 s pre-isolation): the per-path fingerprint keeps reach/base gates from accumulating
  across paths, so cones are **1–20 tokens** (not 19–233) and the fixed-order OBDD compiles trivially.
  16 answers, **OBDD==PWE 15/15** — correctness holds. The order-robust-d4 motivation *for paths* is
  largely removed (it now applies to Q3's ordering step, below — not to paths).
- **TPC-H Q3 compile+WMC is back to near-free — the earlier "3.3 s ordering" was an O(N²) bug (fixed
  `1eb35bf`).** `leaf_order`/`global_order` used `if pl not in order` (linear list scan per leaf) = O(N²);
  at ~45 k tokens that was 9.9 s in isolation. Replaced by set-backed membership → **O(N), 14 ms** for the
  *identical* variable order (byte-for-byte the same DFS first-appearance list), so **WMC is unchanged**
  (re-verified: tests.py 171/171, verify_differential 24 DAGs × 5 backends 1e-16). Q3 compile+WMC is now
  **239 ms of 2.63 s** (5-run), construct-dominated again. WMC itself is tiny everywhere (≤ 36 ms).

## Strong baseline — ProvSQL (modified PostgreSQL), same TPC-H Q3

| query | scale | answers | ProvSQL PQE (median [min–max]) | ours (above) | ours speed-up |
|---|---|--:|--:|--:|--:|
| tpch-Q3 | SF 0.01 | 14 908 | **7.74 s [7.32–7.86]** | **2.63 s** | **2.9×** |

**Ours ~3× faster — honestly, after the O(N²) ordering fix.** Both numbers are 5-run under one protocol
(ProvSQL forced-eval `sum(probability_evaluate(provenance()))`, consumed-probability checksum `sum=0.125·n`
verified per run; ours = construct + shared ROBDD compile + WMC with the O(N) ordering, `1eb35bf`). The
previous "comparable (6.45 vs 7.46 s)" was inflated on our side by the removable O(N²) ordering scan — with
that gone, ours is **2.63 s** and genuinely faster. Probability **parity is exact** (both 0.125·n). Framing
(G2a): the *same* exact PQE on a **stock, unforked** engine over a **broader fragment**, now also faster
per-query at this scale — but the contribution is the unforked/broader-fragment axis, not the speed race.
(The ProvSQL count(*)=1.06 s row was a planner pruning artifact — do not cite it.)

## Instance breadth (R8.1 "≥3–5 instances/shape") — see `g4_instances.csv`

- **TPC-H Q3 × 5 mktsegments** (5-run each; ProvSQL now forced-eval): ours median-of-medians **4.5 s**
  (mean 4.6 ± 1.1 s, range 3.5–6.4 s), ProvSQL **6.0 s** (mean 6.0 ± 1.0 s, 5.0–7.6 s) — **ours faster on
  all 5 segments**. The old "ProvSQL faster" (mean 858 ms) was the pruned `count(*)` target; the honest
  forced-`sum(probability_evaluate)` numbers put ours ahead here.
- **WatDiv S-star × 5 users**: median-of-medians **131 ms** (20 → 665 ms, tracking answer count);
  within-instance sd 0–8 ms.

## Reconvergent query + SF 0.1 end-to-end (R8.3) — see `R8_3_RESULTS.md`

`SELECT ?cust WHERE { ?cust c_mktsegment "BUILDING" . ?order o_custkey ?cust }` — per-answer provenance
`⊕ₖ(cust⊗orderₖ)` with a **shared** cust token (reconvergent; p ∈ [0.375, 0.5], not Q3's 0.125).

| query | scale | answers | ours total (median [min–max]) | ProvSQL total | faster |
|---|---|--:|--:|--:|:--:|
| Qrecon (reconvergent) | SF 0.01 |  247 | **443 ms** [435–457] | 733 ms [730–772] | **ours** |
| Qrecon (reconvergent) | SF 0.1  | 2086 | 12.9 s [12.8–13.2] | **6.7 s** [6.5–6.8] | ProvSQL |

5-run (1 warm-up + 5), current HEAD. A naive per-answer product-sum would exceed 1 for 243/247 (SF 0.01)
and 2058/2086 (SF 0.1); the shared circuit (and ProvSQL) get it right.
**Probability parity — definitively established this run** (`r8_3_reconvergent.py`, keyed by `c_custkey`):
ours **== the closed form** `0.5·(1−0.5^K)` per answer (independent K, `cf_maxerr = 0.0`) **and** ours
**== ProvSQL** `probability_evaluate(provenance())` per customer (`max_abs_error = 0.0`, keys_match).
**Timing is scale-dependent:** ours is faster at SF 0.01 but **ProvSQL is faster at SF 0.1** — under the
`90c3c3c` boundaries our total now includes the pure-Python variable ordering, which grows with the
circuit (SF 0.1: ~12 s of ordering). The robust result is the **exact-probability parity**, not the speed.

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
