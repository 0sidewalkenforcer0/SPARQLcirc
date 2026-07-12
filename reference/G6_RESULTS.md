# G6 — real-circuit WMC validated against ground truth (+ d4 d-DNNF)

G3/G4 run PQE on the **real** circuits with our fixed-order **OBDD**, but E1 only ever checked
`WMC == PWE` on the *gallery* + *synthetic* families. G6 closes that: on the real WatDiv / TPC-H /
Wikidata-path circuits (same as G3) it cross-checks the OBDD against **brute-force possible-world
enumeration (PWE)** — which uses *no variable order at all* — and compiles each with **d4** to a d-DNNF.
`g6_d4_real.py` → `g6_d4.csv`. **Regenerated on current HEAD (`PathIsoSeq` jar).** d4 = one `-dDNNF`
compile per answer then a **local linear WMC of that dump** (`ddnnf_wmc.py`) — *not* d4-v1's `-mc`, which
over-counted (see caveat). PWE + OBDD for cones ≤ 20 tokens (2^tok brute force); d4 for cones ≤ 40.

## Results

| query | dataset | #answers | sampled | **OBDD == PWE** | d4 == OBDD | d-DNNF nodes (med) | d4 (med) |
|---|---|--:|--:|:--:|:--:|--:|--:|
| watdiv-Sstar    | WatDiv 32.7 M           |     2 |  2 | **2/2** | 2/2 | 171 | 42 ms |
| tpch-Q3         | TPC-H 1.26 M            | 14 908 |  8 | **8/8** | 8/8 |   2 | 43 ms |
| wikidata-WDpath | Wikidata 2.13 B (P279+) |    16 | 16 | **16/16** | 16/16 |  3 | 35 ms |

**OBDD == PWE on every sampled answer (26/26), and d4's d-DNNF (locally WMC'd) == OBDD on every one
(26/26)** — including **all 16 property-path answers**. Both fixes converged: `PathIsoSeq` shrank the
WD-path cones to **≤ 20 tokens** (so PWE and d4 cover them all), and the local d-DNNF WMC replaces the
buggy `d4 -mc`.

## Findings

- **Real-circuit probabilities are ground-truth-correct and order-independent.** OBDD-WMC == brute-force
  PWE on **every sampled answer (26/26)**, including all 16 reconvergent property-path answers. PWE uses no
  compilation and no variable order, so the G3/G4 numbers do not depend on the OBDD heuristic. Extends
  E1's `WMC == PWE` from the gallery/synthetic families to the actual WatDiv / TPC-H / Wikidata circuits.
- **`PathIsoSeq` removed the reconvergent-path blow-up.** The `1e67021` un-merging briefly produced huge
  WD-path cones (19 → 233 tokens, OBDD compile 3.9 s — see `HISTORICAL_TIMINGS.md`); the per-path
  fingerprint isolation (`7882a1e`) collapses them back to **≤ 20 tokens** (OBDD compile ~1 ms, G3/CANONICAL).
  So the "order-robust-d4-for-paths" motivation is **gone for these paths** — the OBDD handles them
  trivially, and PWE/d4 now cover **all 16**. (E4's synthetic high-treewidth families remain the real
  d4/order-robustness case; property paths at this scale are low-treewidth after isolation.)
- **The d4-v1 `-mc` over-count is resolved by computing the WMC ourselves.** We compile with `d4 -dDNNF`
  (structure only) and run a **local linear WMC over the dumped d-DNNF** (`ddnnf_wmc.py`), which matches
  OBDD **26/26** — including cases where d4-v1's own `-mc` previously over-counted. d4's *compilation* was
  always sound; only its weighted-count post-processing mis-applied external weights.

## Caveats

- The **d4-v1 `-mc`** over-count (a big cone once gave d4 = 0.125 vs OBDD = PWE = 0.015 625) is recorded in
  git history; it is now **avoided** by never using `d4 -mc` — the CNF/d-DNNF are correct, so our local
  d-DNNF WMC is exact. No d4-v2 is required for these workloads.
- PWE is 2^tokens; with `PathIsoSeq` every real cone here is ≤ 20 tokens, so PWE covers them directly (no
  transitive argument needed). Larger high-treewidth instances live in E4's synthetic families.
