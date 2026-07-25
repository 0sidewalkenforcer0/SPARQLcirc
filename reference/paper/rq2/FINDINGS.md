# RQ2 compactness (fig:compact) — findings & data status

Node-count deliverables for the three-series compactness figure (NPCS vs flat circuit vs factored),
per WatDiv template (C,F,L,O,S) at 10M & 100M. Node = each leaf and each ⊗/⊕/⊖ once (edges excluded).
Deterministic (content-addressed) so one measured run per cell. Metric verified against the persisted
`.nt`: `structure_signature.nodes` = `leaves + operators` from the final circuit (not construction work).

## Data status
| series | 10M | 100M | source |
|---|---|---|---|
| NPCS (method N) | done 24/25 | done (assembled, N ok 26/30 → 21/25 for C,F,L,O,S) | Q2 / Q1 |
| flat circuit (C, effective=flat) | done 24/25 | done | Q2 / Q1 |
| factored (C, effective=factored) | done: L/S/F full, C=CC1 (FF1-4 backfilled, Q4b) | done: F/L/S 17/17, C empty (expected) | Q3 / Q4 |

Deliverables: `nodecount_flat_10m_100m.csv` (DONE), `nodecount_factored_10m_100m.csv` (DONE, 40 rows / 35 ok; 10M F backfilled).
Reconvergence half of the figure is already real: `reference/watdiv/unbound_factored_vs_flat.csv`.

## NPCS tokenizer validated (spec E-C1c)
`_npcs_node_counts` splits provenance cells on `[⊕⊗⊖(),]+`. Checked 3673 circuit leaf atoms and a
200k-line slice of `watdiv.10M.reified.nt`: **0** contain any delimiter. WatDiv IRIs are clean HTTP
IRIs (`.../wsdbm/User71713`), so `npcs_leaves` is an exact statement-id count. No widening needed.

## 10M three-series node counts
| cell | NPCS | flat circuit | factored | note |
|---|---:|---:|---:|---|
| CC1 | 160 | 84 | 997190 | circuit smaller (sharing) |
| CC2 | 0 | 0 | — | equal; factored err:ConstructionProtocolError |
| CC3 | — | — | — | factored err:worker-reap |
| FF1 | 16 | 11 | — | circuit smaller (sharing); factored err:cleanup |
| FF2 | 10 | 10 | — | equal; factored err:cleanup |
| FF3 | 24 | 21 | — | circuit smaller (sharing); factored err:cleanup |
| FF4 | 748 | 183 | — | circuit smaller (sharing); factored err:cleanup |
| FF5 | 232 | 232 | 522 | equal |
| LL1 | 10 | 10 | 18 | equal |
| LL2 | 180 | 145 | 254 | circuit smaller (sharing) |
| LL3 | 196 | 108 | 123 | circuit smaller (sharing) |
| LL4 | 116 | 116 | 174 | equal |
| LL5 | 190 | 153 | 268 | circuit smaller (sharing) |
| OO1 | 361 | 7806 | — | flat OPTIONAL blows up vs NPCS |
| OO2 | 476 | 337385 | — | flat OPTIONAL blows up vs NPCS |
| OO3 | 1906 | 9235 | — | flat OPTIONAL blows up vs NPCS |
| OO4 | 5557 | 19571 | — | flat OPTIONAL blows up vs NPCS |
| OO5 | 476 | 337336 | — | flat OPTIONAL blows up vs NPCS |
| SS1 | 55 | 23 | 71 | circuit smaller (sharing) |
| SS2 | 1530 | 1530 | 3570 | equal |
| SS3 | 1782 | 1106 | 2258 | circuit smaller (sharing) |
| SS4 | 6 | 6 | 12 | equal |
| SS5 | 156 | 156 | 364 | equal |
| SS6 | 10 | 10 | 22 | equal |
| SS7 | 5 | 5 | 9 | equal |

## Key findings (for the §Compactness narrative)
1. **flat circuit vs NPCS (the headline):** on BGP shapes (F/L/S) the flat circuit is equal to or
   *smaller* than NPCS thanks to sub-circuit sharing (FF4 183 vs 748, LL3 108 vs 196, SS3 1106 vs
   1782); many small cells are equal. This is the compactness win on the monotone fragment.
2. **factored is the staged/feedback construction, NOT a compaction.** On *bound* WatDiv it emits more
   explicit structure than flat (verified: same query, flat 55 subj / 200 tri vs factored 116 / 296),
   so factored ≥ flat here — and *catastrophically* larger on some shapes: factored CC1 is a verified
   199 MB / 1.39M-triple / ~997k-node circuit for just **8 answers** (flat: 84 nodes). Its genuine
   compaction win is a *different regime* (unbound reconvergent
   joins) captured by the reconvergence sweep. Expect empty factored cells for C3 and the big O
   templates — honest data (protocol/cleanup/memory), per the run spec.
3. **OPTIONAL caveat (author's call for the text):** the flat circuit is *much larger* than NPCS on
   OPTIONAL (OO2/OO5 ≈ 337k vs 476). This is because the flat build materialises the non-monotone ⊖
   witnesses exactly, whereas NPCS's compact string does **not** support exact non-monotone PQE. So the
   small NPCS-O count is for a strictly weaker computation. The figure will show this gap; the text
   should frame it as "the circuit pays a size cost to gain exact non-monotone evaluation that NPCS
   cannot provide," rather than as NPCS being more compact. Consider whether to keep O in the
   node-count figure or move the OPTIONAL story to the capability/correctness result.

## E-C1b non-monotone coverage (2026-07-24)

Spec `b35a5fd` asked for factored on OPTIONAL and N+flat+factored on MINUS.

### factored on OPTIONAL / MINUS: BLOCKED (engine limitation)
`--methods C` without `PCM_FORCE_FLAT` on O and M returns **`ConstructionProtocolError` (requested=factored, effective=flat) on all 10 cells**. CircuitRun emits a factored (feedback) plan only for pure BGP (C/F/L/S); for OPTIONAL/MINUS it produces a flat plan, and the harness flags the mismatch. So factored-O/M is not a run gap but an **engine capability gap** (paper_construction_matrix.py:1725,1787). Realising the spec's idea (compress reconvergent OPTIONAL via factoring) needs CircuitRun to support factored variable-elimination on the non-monotone positive part.

### MINUS: NPCS vs flat circuit (real, runnable) — flat is competitive-to-smaller

| cell | NPCS | flat circuit | flat vs NPCS |
|---|---:|---:|---|
| M1 | 566321 | — | flat timeout |
| M2 | 1278365 | 1250853 | 0.98x |
| M3 | 3564246 | 1347804 | 0.38x |
| M4 | 21175727 | — | flat timeout |
| M5 | 1078995 | 1131231 | 1.05x |

**On MINUS the flat circuit is <= NPCS** where both build: MM3 flat 1.35M vs NPCS 3.56M (2.6x smaller), MM2/MM5 ~equal; NPCS MINUS is very large (MM4 21.2M). This is the **opposite of OPTIONAL** (flat > NPCS): the circuit's sub-structure sharing wins on MINUS even without factoring. So for the figure, MINUS enters as **NPCS vs flat** and favors the circuit; OPTIONAL remains the caveat pending factored-O engine support.

## E-C1b factored O/M now runs (engine fix) — but factored ≈ flat on WatDiv (2026-07-25)

The engine now supports factored construction for OPTIONAL/MINUS/UNION (was flat-only; commit 9df1028).
Verified exact: factored MINUS/OPTIONAL WMC == possible-world enumeration == flat (Δ=0); 10/10 unit
tests; flat-mode output unchanged. So factored O/M can now enter the figure.

**Result: on WatDiv, factored ≈ flat (no compaction).** True node counts (leaves+gates), 10M:

| cell | NPCS | flat | factored | fac/flat | note |
|---|---:|---:|---:|---:|---|
| OO1 | 361 | 7806 | 7868 | 1.01 | |
| OO2 | 476 | 337385 | 337439 | 1.00 | flat/factored ≫ NPCS |
| OO3 | 1906 | 9235 | 9293 | 1.01 | |
| OO4 | 5557 | 19571 | 20432 | 1.04 | |
| OO5 | 476 | 337336 | 337351 | 1.00 | flat/factored ≫ NPCS |
| MM5 | 1078995 | 1131231 | 1131231 | 1.00 | ≤ NPCS |
| MM1/MM4 | — | (heavy) | timeout | — | factored MINUS too costly @900s |
| MM2/MM3 | 1.28M/3.56M | 1.25M/1.35M | err:cleanup | — | factored MINUS caps where flat built |

**Why:** WatDiv OPTIONAL/MINUS templates are bound and selective, so their derivations do not share
sub-structure — there is nothing for variable elimination to factor, hence factored = flat (same as the
bound-BGP finding). Factoring's real compaction win is the *reconvergent* regime (unbound joins), which
the synthetic sweep `reference/watdiv/unbound_factored_vs_flat.csv` already captures.

**Consequence for the figure/§Compactness:** the b35a5fd hypothesis (factored compresses OPTIONAL on
WatDiv) does **not** hold. For O/M on WatDiv, factored = flat, so the figure can use flat; the OPTIONAL
caveat (flat/factored-O ≫ NPCS, because NPCS lacks exact non-monotone PQE) **remains** and should be
framed as size-for-capability. factored-MINUS is additionally more expensive to construct at scale.

## MINUS @100M — tractability boundary (2026-07-25)

Partial (GraphDB @90g): M2 flat=12.35M nodes, M3 flat=13.38M nodes built; M1 NPCS=5.62M built;
**M4 OOM-crashed GraphDB** (its 10M NPCS was already 21.2M nodes → ~200M at 100M), which blocks M5
(the harness runs templates in order and can't isolate one). So clean flat-vs-NPCS MINUS pairs at 100M
are not obtainable — MINUS@100M circuits are 12–21M+ nodes and hit the engine memory limit. The clean
compactness result for MINUS is at 10M (flat ≤ NPCS). This 100M behaviour is a legitimate **tractability
boundary** data point (report honestly; ties to the WMC/#P-hardness limit discussion).
