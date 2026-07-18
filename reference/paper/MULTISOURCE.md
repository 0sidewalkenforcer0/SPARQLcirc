# Multisource — content-addressed cross-source dedup (r9.2b)

**Question.** What does content-addressing buy when provenance spans multiple, partially-overlapping
sources (mirrored facts, shared reference data, the "100M × 2 sources" stress case)?

**Reframing (honest).** The r9.2b *draft* was a per-engine **construction-time** bar chart. But the
matrix already established that construction time is our **weakness** (6–8× the NPCS provenance SELECT),
so a construction-time multisource chart would only restate that. The real multi-source result is a
**content-addressing / representation** win, orthogonal to construction time:

> Each **distinct** derivation is stored **once** across all sources (a shared gate, identified by SHA256
> of its content), so the circuit scales with the **UNION** of derivations. Flat per-source how-provenance
> (NPCS/SPARQLprov) emits every source's derivations **independently**, repeating each shared derivation
> once per source — it scales with the **SUM**. The gap is exactly the cross-source redundancy.

This is E11's cross-**answer** sharing win, re-run along the cross-**source** axis.

## Harness
`reference/multisource_dedup.py` (pure Python, zero deps, `--selftest` passes). Measures reuse E11's
`repr_size` (gates + edges of a DAG): **T_circuit** = one shared DAG over all sources' answers (full
cross-source + cross-answer dedup) vs **T_flat** = Σ over answers of that answer's own DAG (per-answer
dedup only, no cross-source sharing = the flat NPCS/SPARQLprov representation). Also compiles both
(shared-once vs per-answer) for the end-to-end PQE time.

## Findings (`multisource_dedup.csv`, N=300, d=4)
### Two sources, overlap 0 → 1
| overlap | T_flat | T_circuit | size dedup | PQE time dedup |
|--:|--:|--:|--:|--:|
| 0%  | 5400 | 5400 | 1.00× | 1.6× |
| 50% | 5400 | 4050 | 1.33× | 2.2× |
| 100%| 5400 | 2700 | **2.00×** | **2.9×** |

Identical sources → the circuit is half the size (each derivation once) and compiles ~3× faster.

### K sources at 50% overlap
1→6 sources: T_flat grows **linearly** (2700 → 16200); T_circuit **sub-linearly** (2700 → 9450);
size dedup 1.0× → **1.71×** (→ 2× asymptote at 50% overlap, `K/(o+K(1-o)) → 1/(1-o)`); PQE time dedup
1.8× → 2.7×.

## Bottom line (positioning)
Multi-source is a **representation** argument, not a speed-of-construction one: content-addressing makes
one shared circuit the right structure for provenance that spans overlapping sources — size ~ union not
sum, and it compiles once for a 2–3× end-to-end PQE win. State it as such; do **not** dress it up as a
construction-time result.

## Figure & data
- `presentation/figures/final/result_r9_2b_multisource.{pdf,png}` — generator
  `presentation/make_multisource_figure.py`: (a) overlap sweep, (b) K-source sweep.
- `reference/multisource_dedup.csv` — the sweep (for later analysis).

## Deferred (engine-level, if ever wanted)
A literal 100M × 2 named-graph **construction** run is *not* done: no distinct 2nd source exists (WatDiv
only; the 200M generator was never built), 2×37G reified loads carry the same OOM risk that capped the
matrix C column, and it would only re-show the construction weakness. Recipe if needed: load two WatDiv
partitions into two named graphs, a spanning query with source attribution (the named-graph reification
scheme already tracks the graph), then B/R/N/C via `paper_construction_matrix.py` with `PCM_FORCE_FLAT`.
