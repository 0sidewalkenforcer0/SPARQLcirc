# Final paper figures

Every figure here is rendered through the shared `figstyle` grammar — the same visual
grammar as the `../drafts/` layout drafts (`make_round9_drafts.py`): one 7.15-inch text
width, the SPARQLprov B/R/N/C palette, light-grid frames, bold panel letters, gray footer
captions, and vector PDF + 300-dpi PNG with embedded fonts. Two sets live here:

- **`result_r9_*`** — the ROUND-9 **drafts structure** (per-experiment, full-result layouts
  in the SPARQLprov/NPCS idiom), filled from committed `reference/` CSVs. Sub-panels whose
  data awaits the ROUND-9 server run keep the layout and show `DATA PENDING`. Generator:
  `../../make_result_figures.py`.
- **`paper_fig*` / `paper_table1`** — the compact composite layout (2×2 / 1×3), kept as
  space-efficient alternatives for the paper body. Generator: `../../make_figures.py`.

Regenerate: `cd presentation && python3 make_result_figures.py && python3 make_figures.py`

## Manifest

| figure | set | structure | source CSV(s) | data status |
|---|---|---|---|---|
| `result_r9_4_compilation_scale`    | drafts | 2×3 latency/size/RSS × fixed/growing tw | `watdiv/e4_results.csv` | size + OBDD latency real; **RSS pending** |
| `result_r9_3b_sharing_crossover`   | drafts | 1×2 representation crossover + shared compile | `bench.csv`, `e11_scale.csv` | **real** |
| `result_r9_7_provsql_tpch`         | drafts | 1×2 matched cells + scale trend | `g4_instances.csv` | matched Q3 segments real; **scale sweep pending** |
| `result_r9_4b_compilation_patterns`| drafts | 1×2 latency + size over real classes | `g3_pqe.csv` | d-DNNF real; **per-class OBDD pending** |
| `result_r9_3_storage_ratio`        | drafts | grouped ratio bars (low-sharing counterexamples) | `g2b_npcs_vs_ours.csv` | **real** |
| `result_r9_2c_data_scale`          | drafts | 1×3 time/size/RSS vs WatDiv scale | `watdiv/e3_10M.csv`, `e3_100M.csv` | time + size real (10M/100M); **RSS + 1B pending** |
| `result_r9_6_paths`                | drafts | 1×3 construct/circuit/RSS (path operators) | `watdiv/e_paths.csv` | construct + size real; **RSS + reach sweep pending** |
| `paper_fig1_compilation`           | composite | 1×2 fixed/growing tw | `watdiv/e4_results.csv` | real |
| `paper_fig2_sharing`               | composite | 2×2 reconvergence/compile/NPCS | `bench.csv`, `e11_scale.csv`, `g2b_npcs_vs_ours.csv` | real |
| `paper_fig3_construction`          | composite | 1×3 10M/100M/Wikidata | `watdiv/e3_*.csv`, `e6_minus_*.csv`, `e8_wikidata.csv` | real |
| `paper_fig4_pqe`                   | composite | 1×2 ProvSQL + stage decomposition | `g4_instances.csv`, `g4_rigor.csv` | real |
| `paper_table1_validation`          | composite | table | `g6_d4.csv` | real |

## Flagships still in `../drafts/` (pending the server matrix)

The per-template × per-engine × B/R/N/C figures cannot be filled from committed CSVs — they
need the ROUND-9 server run. They stay as data-free drafts until then; their `make_result_figures.py`
slots are ready to fill (preserve panel geometry, swap the pending mark for the aggregation):

- `draft_r9_2_construction_<engine>` (25-template B/R/N/C construction, 10M/100M)
- `draft_r9_5_e2e_<engine>` (per-template end-to-end PQE stages)
- `draft_r9_2b_multisource_<engine>` (100M × 2 sources stress)
