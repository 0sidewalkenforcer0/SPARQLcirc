# Real workload treewidth (Phase 3, claim D on real data)

**Claim D** — tractability is governed by the lineage's treewidth. E4 shows the *dependence* on synthetic
tw-controlled families (bounded tw → poly d-DNNF; growing tw → #P wall). This measures the tw
**distribution of the actual workload**, so the paper can say the real queries live in the tractable zone.

## Method (structural, engine-free)
`reference/treewidth_workload.py`: parse each of the 30 workload templates (C/F/L/S/O/M × WatDiv, from
`workload_manifest.csv`) with rdflib, collect every triple pattern across BGP/OPTIONAL/MINUS/UNION branches,
build the **primal (variable-interaction) graph** — the variables of one triple pattern form a clique — and
compute the induced width by **min-fill elimination** (an upper bound on treewidth; exact at these small
values). Property paths connect their (subject, object) endpoints; constants are fixed, not elimination vars.

## Result (`treewidth_workload.csv`)
| | value |
|---|---|
| distribution over 30 templates | **tw 1: 29, tw 2: 1** |
| max tw | **2** |
| median tw | **1** |
| per class | C 1, F 1, L 1, S 1, O 1, M 1–2 |

**Every real workload query is tw ≤ 2** — squarely inside E4's bounded-treewidth regime where d-DNNF stays
polynomial and PQE is tractable. The single tw-2 case is a MINUS query with a small cycle. This is exactly
the pre-registered prediction (docs/EVALUATION.md: "WatDiv S/L/F/C are low tw 1–3 → compile uniformly cheap; the
wall appears only in synthetic grid/clique — why E4 is synthetic-only").

## Honest scope
This is the **query join-graph** treewidth (the standard structural tractability parameter, and the upper
bound the theory uses). The **lineage** can add data-dependent reconvergence on top; that orthogonal axis is
what E2/E11 (sharing / reconvergence) and E4 (the compiled-size sweep) measure directly. Together: real
queries are structurally low-tw, and where the lineage does reconverge, the shared circuit + KC is exactly
the mechanism that keeps it tractable.
