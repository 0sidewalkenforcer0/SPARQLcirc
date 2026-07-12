# G6 — real-circuit WMC validated against ground truth (+ d4 d-DNNF)

G3/G4 run PQE on the **real** circuits with our fixed-order **OBDD**, but E1 only ever checked
`WMC == PWE` on the *gallery* + *synthetic* families. G6 closes that: on the real WatDiv / TPC-H /
Wikidata-path circuits (same as G3) it cross-checks the OBDD against **brute-force possible-world
enumeration (PWE)** — which uses *no variable order at all* — and compiles each with **d4** to a d-DNNF.
`g6_d4_real.py` → `g6_d4.csv`. **Regenerated on the post-`1e67021` jar.** PWE + OBDD only for cones with
≤ 20 tokens (2^tok brute force); d4 only for cones ≤ 40 (see the path note).

## Results

| query | dataset | #answers | tractable cones | **OBDD == PWE** | d4 == OBDD | d-DNNF nodes (med) | d4 (med) |
|---|---|--:|--:|:--:|:--:|--:|--:|
| watdiv-Sstar    | WatDiv 32.7 M           |     2 |  2 | **2/2** | 2/2 | 171 | 40 ms |
| tpch-Q3         | TPC-H 1.26 M            | 14 908 |  8 | **8/8** | 8/8 |   2 | 33 ms |
| wikidata-WDpath | Wikidata 2.13 B (P279+) |    16 |  4 | **4/4** | 4/4 |  20 | 34 ms |

**OBDD == PWE on every tractable cone (14/14), and d4 == OBDD on every one it compiled (14/14).**

## Findings

- **The real-circuit probabilities are ground-truth-correct and order-independent — re-confirmed post-fix.**
  OBDD-WMC equals brute-force PWE on every cone small enough to enumerate: **14/14** across the three
  workloads, including the small reconvergent property-path cones. PWE uses no compilation and no variable
  order, so the G3/G4 numbers do not depend on the OBDD heuristic. Extends E1's `WMC == PWE` from the
  gallery/synthetic families to the actual WatDiv / TPC-H / Wikidata circuits.
- **The property-path cones grew (correctly) and are the order-robust-compile case.** After `1e67021`
  un-merges the reach-states the old key collapsed, WD-path's 16 answer cones span **19 → 233 tokens**
  (only 4 are ≤ 20). These large reconvergent cones are exactly where a **fixed-order OBDD is expensive**
  (G3: the WD-path OBDD compile is now **3.9 s**, up from a pre-fix ~1 ms on the under-merged circuit) and
  where **d4-v1's weighted count is unreliable** (over-counts — see caveat). So the honest state: WMC on
  paths is correct (OBDD, PWE-validated on the tractable cones) but the *compile* of large reconvergent
  paths wants a working **order-robust d-DNNF** (d4v2 / our own d-DNNF WMC) — the clearest open
  optimization the fix surfaced.
- **d4 is reliable where it's tractable.** On cones ≤ 40 tokens (all of star/Q3, the 4 small WD-path
  cones) d4's weighted count matches the OBDD exactly (14/14), and d-DNNFs are small (2–171 nodes,
  ~33–40 ms). d4 is a sound second compiler in the low/medium-treewidth regime.

## Caveats

- **d4-v1 over-counts on the *large* reconvergent path CNFs** (verified earlier: a big cone gave
  d4 = 0.125 vs OBDD = PWE = 0.015 625; the exported CNF is correct — its clauses reproduce PWE — but
  d4-v1's equivalence/gate preprocessing mis-applies the external token weights). This regeneration
  therefore **caps d4 at ≤ 40 tokens**; the trusted WMC on big path cones is OBDD (PWE-validated on the
  small ones). Follow-up: **d4v2** (`D4V2=1` in `d4_pipeline.py`) or WMC d4's `-dDNNF` output ourselves.
- PWE is 2^tokens, feasible only for cones ≤ 20; big cones are validated transitively (same OBDD code,
  PWE-confirmed on all small cones + the full gallery/synthetic E1 set).
