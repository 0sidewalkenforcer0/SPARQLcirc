# G6 — real-circuit WMC validated against ground truth (+ d4 d-DNNF)

G3/G4 run PQE on the **real** circuits with our fixed-order **OBDD**, but E1 only ever checked
`WMC == PWE` on the *gallery* and *synthetic* families. The reviewer's worry was order-sensitivity of
the naive OBDD. G6 closes both: on the real WatDiv / TPC-H / Wikidata-path circuits (same as G3) it
cross-checks the OBDD against **brute-force possible-world enumeration (PWE)** — which uses *no variable
order at all* — and additionally compiles each with **d4** to a d-DNNF. `g6_d4_real.py` → `g6_d4.csv`.

## Results

| query | dataset | #answers | sampled | **OBDD == PWE** | d4 == OBDD | d-DNNF nodes (med) | d4 compile (med) |
|---|---|--:|--:|:--:|:--:|--:|--:|
| watdiv-Sstar    | WatDiv 32.7 M           |     2 |  2 | **2/2** | 2/2 | 171 | 39 ms |
| tpch-Q3         | TPC-H 1.26 M            | 14 908 |  8 | **8/8** | 8/8 |   2 | 33 ms |
| wikidata-WDpath | Wikidata 2.13 B (P279+) |    16 | 16 | **16/16** | 8/16 | 3 | 34 ms |

*(PWE enumerated for cones with ≤ 20 tokens — all sampled answers here qualify. Sampling is spread
across the answer list; for Q3, 8 answers of 14 908.)*

## Findings

- **The real-circuit probabilities are ground-truth-correct and order-independent.** OBDD-WMC equals
  brute-force PWE on **every** sampled answer — **26/26**, including all 16 **reconvergent property-path**
  answers on the 2.13 B-triple graph. PWE evaluates the circuit over every possible world with no
  compilation and no variable order, so this is the strongest correctness statement available and it
  says the G3/G4 numbers do **not** depend on the OBDD's variable-ordering heuristic for these
  workloads. This extends E1's `WMC == PWE` guarantee from the gallery/synthetic families to the actual
  WatDiv / TPC-H / Wikidata circuits.
- **d4 compiles all real circuits to compact d-DNNFs.** Sizes are tiny (S-star 171 nodes, Q3 2, path
  answers 3 — these workloads are low-treewidth, as expected), compile ~33–39 ms. On the low-treewidth
  **tree/star** circuits d4's weighted count matches the OBDD exactly (10/10). For the high-treewidth
  regime where OBDD *order* would matter, the reference remains E4's synthetic d-DNNF-size study; G6
  shows the real workloads sit in the easy regime.
- **A d4-v1 weighted-MC caveat (verified, honest).** On the larger **reconvergent path** CNFs, d4-v1's
  `-mc -wFile` **over-counts** (8/16 agree). This is **not** a circuit or encoding error: evaluating the
  exported CNF's clauses against the circuit's own assignment reproduces PWE exactly (e.g. a k=5 cone:
  clauses→0.03125 = PWE = OBDD, but `d4 -mc`→0.125). d4's log shows "equivalence simplification /
  0 decisions" — its gate/equivalence preprocessing appears to drop or mis-apply the external token
  weights on these gate-structured formulas (it is correct on the gallery toys, the hand CNFs
  0.5/0.125/0.75/0.015625, and the tree/star reals). **We therefore trust OBDD + PWE for path WMC.**

## Caveats / follow-ups

- The d4-v1 over-count is a **tooling** issue, quarantined to `d4 -mc` on large gate-CNFs. Follow-ups:
  (a) re-run with **d4v2** (`D4V2=1` path in `d4_pipeline.py`) or disable d4's preprocessing; (b) WMC the
  d-DNNF **ourselves** (parse d4's `-dDNNF` output and count) to keep d4's compilation while trusting our
  count; (c) **spot-check E4's d4-WMC** numbers against OBDD/PWE — E4 matched, so its synthetic families
  likely stay under the trigger, but G6 motivates confirming it.
- Per-answer d4 (one CNF per answer) is the Θ(N·S) a baseline would pay (E11); the shared OBDD is
  Θ(N+S). G6 samples answers for the correctness check, not to WMC all 14 908 Q3 answers per-answer.

> **Engine fix 1e67021 (mid-session):** the term-type-aware gate-identity fix adds `urn:circuit:binding`
> metadata triples (raising raw circuit/triple counts) and un-merges property-path reach-states the old key
> wrongly collapsed. The **correctness spine re-verified on the rebuilt jar** — answer counts unchanged
> (S-star 2, Q3 14908, WD-path 16), OBDD==PWE still holds, E10 byte-identity still 13/13 on 3 engines.
> **Absolute sizes/times in this file predate the fix** (esp. the property-path row) and should be
> regenerated in a clean pass; the conclusions are unaffected.
