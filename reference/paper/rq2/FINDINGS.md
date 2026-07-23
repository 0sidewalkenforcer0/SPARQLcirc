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
| factored (C, effective=factored) | done 9 BGP + expected empties | **running (Q4)** | Q3 / Q4 |

Deliverables: `nodecount_flat_10m_100m.csv` (DONE, pushed), `nodecount_factored_10m_100m.csv` (pending Q4).
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
