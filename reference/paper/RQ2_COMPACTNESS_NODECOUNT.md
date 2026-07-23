# RQ2 compactness — real per-template node counts for Fig. `fig:compact`

## Why this run exists
The paper's compactness figure plots **representation size as a node count** — NPCS vs our flat vs
our factored circuit — per WatDiv template (C,F,L,O,S) at **10M and 100M**, plus a synthetic
reconvergence sweep.

- The **reconvergence half is already real** (`reference/watdiv/unbound_factored_vs_flat.csv` +
  an analytic NPCS count).
- The **WatDiv half is currently PLACEHOLDER**: the flat bars use real operator gate counts, but the
  **NPCS and factored bars are synthesized**. This task produces the real WatDiv node counts so the
  placeholder can be swapped out.

Only the *size* (node counts) is needed here, and it is deterministic (content-addressed), so a
single measured run per cell suffices.

## Prerequisites
- Harness on `feat/rdfstar-reification`, commit **`11ce4f4`** or later
  (`PROTOCOL = r9.2-frozen-identity-v9`). It adds, for method **N**, the columns
  `npcs_oplus`, `npcs_ominus`, `npcs_leaves` next to `npcs_token_occurrences`; and for method **C**,
  `leaves`/`plus`/`minus`/`nodes` inside the `structure_signature` object in the `notes` JSON.
- `cd engine && mvn -q package` (build `target/npcs-rewrite.jar`); `cd reference && python3 verify_all.py` green.
- GraphDB with WatDiv **10M** and **100M** reified (Standard reification) loaded, base + reified
  endpoints. For the **factored** pass, a **writable** repo + UPDATE endpoint (factored uses feedback
  INSERT).

## Node-count definition (what the figure consumes)
Per template, node = each leaf and each ⊗/⊕/⊖ once (edges excluded):

| series | value | method / source |
|---|---|---|
| NPCS | `npcs_token_occurrences` + `npcs_oplus` + `npcs_ominus` + `npcs_leaves` | method **N** columns |
| flat | `structure_signature.nodes` (= leaves + operators) | method **C**, `construction_effective=flat`, from `notes` JSON |
| factored | `structure_signature.nodes` | method **C**, `construction_effective=factored`, from `notes` JSON |

## E-C1 — WatDiv per-template node counts (essential)

Classes **C,F,L,O,S** (the figure dropped MINUS/M — add `,M` if you want it back). Scales 10M,100M.
`--warmups 0 --runs 1` because size is deterministic; use the defaults (1/5) if you also want citable
construction timing (that feeds RQ3/RQ5, not this figure). Endpoint env vars follow
`PCM_<ENGINE>_<SCALE>_<ROLE>_ENDPOINT`.

### (a) NPCS + flat  (read-only, force flat)
```bash
export PCM_JAVA_BIN=java
export PCM_FORCE_FLAT=1
export PCM_GRAPHDB_10M_BASE_ENDPOINT=http://localhost:7200/repositories/watdiv10m
export PCM_GRAPHDB_10M_REIFIED_ENDPOINT=http://localhost:7200/repositories/watdiv10m
export PCM_GRAPHDB_100M_BASE_ENDPOINT=http://localhost:7200/repositories/watdiv100m
export PCM_GRAPHDB_100M_REIFIED_ENDPOINT=http://localhost:7200/repositories/watdiv100m
python3 reference/paper/paper_construction_matrix.py \
    --engines graphdb --scales 10M,100M --classes C,F,L,O,S --methods N,C \
    --warmups 0 --runs 1 \
    --out reference/paper/nodecount_flat_10m_100m.csv
```
Gives, per template: the **N** row (real `npcs_*` node counts) and the **C** row with
`construction_effective=flat` (flat `structure_signature.nodes`).

### (b) factored  (writable endpoint, no force-flat)
```bash
unset PCM_FORCE_FLAT
export PCM_GRAPHDB_10M_BASE_ENDPOINT=http://localhost:7200/repositories/watdiv10m
export PCM_GRAPHDB_10M_REIFIED_ENDPOINT=http://localhost:7200/repositories/watdiv10m
export PCM_GRAPHDB_10M_UPDATE_ENDPOINT=http://localhost:7200/repositories/watdiv10m/statements
export PCM_GRAPHDB_100M_BASE_ENDPOINT=http://localhost:7200/repositories/watdiv100m
export PCM_GRAPHDB_100M_REIFIED_ENDPOINT=http://localhost:7200/repositories/watdiv100m
export PCM_GRAPHDB_100M_UPDATE_ENDPOINT=http://localhost:7200/repositories/watdiv100m/statements
python3 reference/paper/paper_construction_matrix.py \
    --engines graphdb --scales 10M,100M --classes C,F,L,O,S --methods C \
    --warmups 0 --runs 1 \
    --out reference/paper/nodecount_factored_10m_100m.csv
```
C rows here have `construction_effective=factored` and the factored `structure_signature.nodes`.
(Expect a few too-large/timeout cells, e.g. C3 and the biggest O templates — that is honest data;
leave them empty.)

### (c) validate the NPCS leaf tokenizer (one-time)
`_npcs_node_counts` counts NPCS leaves by splitting the provenance string on the delimiter class
`[⊕⊗⊖(),]`. WatDiv statement IRIs should contain none of those, but confirm once: dump a single NPCS
provenance cell and check `npcs_leaves` equals the number of statement-id occurrences in it. If a real
WatDiv IRI embeds `(`, `)` or `,`, widen the split class in `_npcs_node_counts` and re-run (a).

## E-C2 — measured reconvergence NPCS (OPTIONAL)
The reconvergence NPCS bars use the **analytic exact** count `D·(k+1)+answers` with
`D = answers·W^(k−1)`, which is exact for the fully-specified layered family, so it is already
defensible. To *measure* it too, extend `reference/unbound_factored_vs_flat.py` to also run the NPCS
reimplementation on the same layered `.ttl` + k-hop `.rq` and count its leaf+⊗+⊕ nodes into a new
column. Not required for the figure.

## Deliverables (commit + push)
- `reference/paper/nodecount_flat_10m_100m.csv`
- `reference/paper/nodecount_factored_10m_100m.csv`

Push these back; the paper-side generator `figures/plot_compactness.py` will then be pointed at these
CSVs (real NPCS/flat/factored) instead of synthesizing the WatDiv half. Nothing else in the figure or
the §Compactness text changes.
