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
- **`paper_fig*` / `paper_table1`** — compact composite layouts, kept as space-efficient
  alternatives for the paper body. Generator: `../../make_figures.py`.

Regenerate: `cd presentation && python3 make_matrix_figures.py && python3 make_result_figures.py && python3 make_figures.py`

## Real construction matrix (GraphDB) — the flagship

`result_r9_2_construction_graphdb` is **real data**: the flat B/R/N/C construction-time matrix
built by `reference/paper/paper_construction_matrix.py` (`PCM_FORCE_FLAT=1`, warmup + 5 runs,
**300 s** cap). Coverage **10M 114/120, 100M 110/120** (C column 27/30, 24/30). The ~10 ▼ are
genuinely too-large even at 300 s — chiefly C3 (4.24M answers at 100M). `result_r9_3_storage_graphdb`
and `result_r9_2c_data_scale_graphdb` derive from the same matrix. The other three engines
(`_oxigraph/_qlever/_millenniumdb`) show `DATA PENDING` — see below.

## Manifest

| figure | generator | structure | data status |
|---|---|---|---|
| `result_r9_2_construction_graphdb` | matrix | 2×30×B/R/N/C, 2 scales | **real** (10M 114/120, 100M 110/120) |
| `result_r9_3_storage_graphdb` | matrix | NPCS/circuit size ratio/template, 2 scales | **real** (mostly <1 = selective counterexamples) |
| `result_r9_2c_data_scale_graphdb` | matrix | time/size/RSS vs scale per class | **real** time+size; **RSS pending** |
| `result_r9_{2,3,2c}_{oxigraph,qlever,millenniumdb}` | matrix | same | **DATA PENDING** (engines not loaded) |
| `result_r9_4_compilation_scale` | result | 2×3 latency/size/RSS × fixed/growing tw | size+OBDD latency real; **RSS pending** |
| `result_r9_3b_sharing_crossover` | result | crossover + shared compile | **real** |
| `result_r9_7_provsql_tpch` | result | matched cells + scale trend | Q3 segments real; **scale sweep pending** |
| `result_r9_4b_compilation_patterns` | result | latency + size over real classes | d-DNNF real; **OBDD pending** |
| `result_r9_6_paths` | result | construct/circuit/RSS (path operators) | construct+size real; **RSS+sweep pending** |
| `paper_fig1..4`, `paper_table1` | composite | compact 2×2 / 1×3 / table | real |

## Pending (need infrastructure not available this session)

- **Other engines** (`_oxigraph/_qlever/_millenniumdb`): each needs base+reified WatDiv loaded
  at 10M+100M (~60 G/engine). Oxigraph's in-SPARQL SHA256 makes the C method impractically slow
  (E10: S-star 365 s), so it would give B/R/N only. Cross-engine byte-identity is already proven
  in E10. Run recipe + fix are in the `r9-construction-matrix` memory.
- **`draft_r9_5_e2e_<engine>`** (per-template end-to-end PQE stages) and
  **`draft_r9_2b_multisource_<engine>`** (100M × 2 sources): not yet run; remain in `../drafts/`.
