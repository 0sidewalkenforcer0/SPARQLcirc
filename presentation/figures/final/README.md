# Final paper figures

Every figure here is rendered through the shared `figstyle` grammar — the same visual grammar
as the `../drafts/` layout drafts (`make_round9_drafts.py`): one 7.15-inch text width, the
SPARQLprov B/R/N/C palette, light-grid frames, bold panel letters, gray footer captions, and
vector PDF + 300-dpi PNG with embedded fonts. Three generators write here:

- **`result_r9_2/3/2c_<engine>`** — full-dimension **per-engine** figures (2 scales × 30
  templates × B/R/N/C) from the committed construction matrix. Generator:
  `../../make_matrix_figures.py` reading `reference/paper/construction_matrix_{10m,100m}.csv`.
- **`result_r9_*` (engine-independent)** — drafts-structure figures from controlled/cross-system
  CSVs. Generator: `../../make_result_figures.py`.
- **`result_r9_5_pqe_headtohead`** — completes NPCS/SPARQLprov into a full PQE pipeline (same
  compiler both sides) and compares end-to-end. Generator: `../../make_pqe_figure.py` reading
  `reference/e11_scale.csv` + `reference/paper/pqe_stages_flat_{10m,100m}.csv`.
- **`result_r9_2b_multisource`** — content-addressed cross-source dedup (representation ~ ∪ vs flat
  ~ Σ). Generator: `../../make_multisource_figure.py` reading `reference/multisource_dedup.csv`.
- **`result_r9_5_e2e_<engine>`** — assembled per-engine end-to-end PQE: real construct (that engine's
  matrix, method C) + real compile+WMC (pqe_stages, engine-independent per E10), with NPCS end-to-end
  overlaid and OPTIONAL ✗. Generator: `../../make_e2e_figure.py`. (The illustrative
  `draft_r9_5_e2e_<engine>` in `../drafts/` is deliberately kept alongside it.)
- **`paper_fig*` / `paper_table1`** — compact composite layouts, kept as space-efficient
  alternatives for the paper body. Generator: `../../make_figures.py`.

Regenerate: `cd presentation && python3 make_matrix_figures.py && python3 make_result_figures.py && python3 make_pqe_figure.py && python3 make_multisource_figure.py && python3 make_e2e_figure.py && python3 make_figures.py`

## Real construction matrix (GraphDB) — the flagship

`result_r9_2_construction_graphdb` is **real data**: the flat B/R/N/C construction-time matrix
built by `reference/paper/paper_construction_matrix.py` (`PCM_FORCE_FLAT=1`, warmup + 5 runs,
**300 s** cap). Coverage **10M 114/120, 100M 110/120** (C column 27/30, 24/30). The ~10 ▼ are
genuinely too-large even at 300 s — chiefly C3 (4.24M answers at 100M). `result_r9_3_storage_graphdb`
and `result_r9_2c_data_scale_graphdb` derive from the same matrix. QLever and Oxigraph have real
pages too (below); MillenniumDB shows `DATA PENDING`.

## Manifest

| figure | generator | structure | data status |
|---|---|---|---|
| `result_r9_2_construction_graphdb` | matrix | 2×30×B/R/N/C, 2 scales | **real** (10M 114/120, 100M 110/120) |
| `result_r9_3_storage_graphdb` | matrix | NPCS/circuit size ratio/template, 2 scales | **real** (mostly <1 = selective counterexamples) |
| `result_r9_2c_data_scale_graphdb` | matrix | time/size/RSS vs scale per class | **real** time+size; **RSS pending** |
| `result_r9_2_construction_qlever` | matrix | 2×30×B/R/N/C, 2 scales | **real** (10M 112/120, 100M 109/120; byte-identical to GraphDB) |
| `result_r9_2_construction_oxigraph` | matrix | 2×30×B/R/N/C | **real 10M** (102/120; B 30/30, C 20/30 — slow SHA256 ▼ the rest; 100M pending) |
| `result_r9_{2,3,2c}_millenniumdb` | matrix | same | **DATA PENDING** (see below) |
| `result_r9_4_compilation_scale` | result | 2×3 latency/size/RSS × fixed/growing tw | size+OBDD latency real; **RSS pending** |
| `result_r9_3b_sharing_crossover` | result | crossover + shared compile | **real** |
| `result_r9_7_provsql_tpch` | result | matched cells + scale trend | Q3 segments real; **scale sweep pending** |
| `result_r9_4b_compilation_patterns` | result | latency + size over real classes | d-DNNF real; **OBDD pending** |
| `result_r9_6_paths` | result | construct/circuit/RSS (path operators) | construct+size real; **RSS+sweep pending** |
| `result_r9_5_pqe_headtohead` | pqe | E11 amortization + WatDiv per-template ratio + non-monotone ✗ | **real** (E11 8.2×@1000; WatDiv ≈1.3× median; 5 OPTIONAL ✗) |
| `result_r9_2b_multisource` | multisource | cross-source dedup: overlap sweep + K-source sweep | **real** (2× at full overlap; 1.71× @6 sources; 2.9× PQE) |
| `result_r9_5_e2e_{graphdb,qlever,oxigraph,millenniumdb}` | e2e | per-engine end-to-end PQE (construct + compile+WMC), 2 scales | **real** (construct dominates; NPCS lower on selective; OPTIONAL ✗) |
| `paper_fig1..4`, `paper_table1` | composite | compact 2×2 / 1×3 / table | real |

## Three real engines; the rest pending

`result_r9_2_construction_{graphdb,qlever,oxigraph}` are real. GraphDB and QLever cover **both
scales** (10M + 100M); Oxigraph covers 10M. Remaining:

- **Oxigraph 100M**: only a 100M *base* store exists (no reified) → can't do R/N/C at 100M.
  Its in-SPARQL SHA256 is slow (E10: S-star 365 s) so even at 10M the larger-circuit C cells ▼.
- **MillenniumDB**: needs a fresh import (read-only, fast, SHA256 works per E10 → would give a
  full C column like QLever). Cross-engine byte-identity already proven in E10.
  Run recipes + the flat-C fix are in the `r9-construction-matrix` memory.
- **`result_r9_5_pqe_headtohead`** (engine-independent amortization + capability),
  **`result_r9_2b_multisource`** (cross-source dedup), and **`result_r9_5_e2e_<engine>`** (assembled
  per-engine end-to-end PQE, all 4 engines) are **real** — see above and
  `reference/paper/{PQE_HEADTOHEAD,MULTISOURCE}.md`. The head-to-head and multisource are reframed to
  content-addressing wins (representation + PQE), *not* construction time.
- **Both versions kept for r9.5 e2e**: the assembled `result_r9_5_e2e_<engine>` (real data) *and* the
  illustrative `draft_r9_5_e2e_<engine>` (in `../drafts/`) — kept side by side by request.
- The literal per-engine 100M × 2 construction bar (`draft_r9_2b_multisource_<engine>`) is intentionally
  superseded by the engine-independent dedup result (see `MULTISOURCE.md` for the rationale + recipe).
