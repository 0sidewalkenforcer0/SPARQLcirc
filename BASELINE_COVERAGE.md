# Baseline coverage — SPARQLprov / NPCS experiment dimensions vs ours

The baselines' experiments measure one thing: the cost + correctness of *producing* how-provenance
(per-answer strings). SPARQL_circ has that layer **plus** a PQE layer (compile → WMC → probability) they
do not have. So the shared dimensions map across, a few of theirs are irrelevant to us, and ~half of
*our* experiments have no baseline analogue at all. Companion to `TECHREPORT.md §14` (related-work table)
and `EVALUATION.md`; gap IDs (G1–G10) are defined in `SERVER_TASK.md` ROUND 6.

## SPARQLprov dimensions → us

| Dimension | Need? | Our coverage | Status |
|---|---|---|---|
| stage: rewrite / execute / **decode** | partial | rewrite (negligible) + construct (E3); **no decode** — circuit is emitted as RDF, not per-answer strings; extra stage = compile+WMC | ◑ own breakdown (G3); ✗ their decode |
| reification: named-graph / Wikidata / standard | yes | Standard (E1–E10), Wikidata (E8), SPARQL\* (piloted) | ◑ clean cmp (G7) |
| query versions B / R / P | B vs P | E3 `c_overhead` = circuit / plain | ✓ (R not essential) |
| engine: Virtuoso / Fuseki | yes | E10 — 4 engines + byte-identity | ✓ exceeded |
| query types L / S / F / **C** / O | yes | E3 (S/L/F), E6/R2A (O), **C1 built at WatDiv 10M (G10, `G10_RESULTS.md`)** | ✓ full L/S/F/C/O taxonomy (C at 10M; 200M-C pending) |
| TPC-H, **aggregate** vs non-aggregate | non-agg only | E9 (non-agg, SF 0.01–1) | ✗ agg out-of-scope (G9) |
| baselines: SPARQLprov / **TripleProv / GProM** | no | our distinguishing baseline is ProvSQL (PQE) | ✗ don't need — same class |

## NPCS dimensions → us

| Dimension | Need? | Our coverage | Status |
|---|---|---|---|
| original vs rewritten provenance query | yes | E3 `c_overhead` | ✓ |
| NPCS vs SPARQLprov | vs us | E2 cost model, E11 (NPCS reimpl) | ◑ full 3-way (G2b/G5) |
| GraphDB vs Stardog | yes | E10 (4 engines) | ✓ exceeded |
| RDF-star vs named graphs | yes | reification schemes | ◑ clean cmp (G7) |
| WatDiv 10M / 100M / **200M** | yes | E3 (10M, 100M) | ◑ 200M = G10 |
| query types L / S / F / **C** / O | yes | E3/E6 + **C1 (G10, WatDiv 10M)** | ✓ C done (10M) |
| WDBench on Wikidata (~15 B) | yes | E8 (2.13 B, partial + broad filter) | ◑ full WDBench (G2b) |
| monotone vs non-monotone optional | yes — a strength | E1 / E6 / R2A (⊖) | ✓ + PQE they can't |

## Dimensions we do NOT need (4)

1. **Decoding** — we never parse per-answer provenance strings; the circuit is emitted directly as RDF.
   Their decode cost is a point *in our favor* (E11: the per-answer string is the expensive representation).
2. **Aggregate provenance** (TPC-H aggregate) — out of scope; declare it (G9).
3. **TripleProv / GProM baselines** — same "how-provenance, no PQE" class as NPCS/SPARQLprov (GProM is
   relational). Cite in related work; don't run. Our distinguishing baseline is **ProvSQL** (exact PQE).
4. **The R (reified, no-provenance) query version** — our overhead story is plain-vs-circuit (P/B).

## No baseline analogue — our PQE layer

E2 (compactness), E4 (compile vs treewidth), E7 (vs ProvSQL), E11 (per-answer vs shared), R3/E8 (property
paths). Neither baseline computes probabilities, so this ~half of the evaluation is measured intrinsically
or against ProvSQL — and it is the half that makes the paper.
