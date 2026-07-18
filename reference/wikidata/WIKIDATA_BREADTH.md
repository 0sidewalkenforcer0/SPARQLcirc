# Wikidata operator breadth (Phase 2) — the full fragment on a real KG

**Goal.** The VLDB scale story was thin: one property-path query at "2.13 B". This broadens it to the
**whole SPARQL fragment** — BGP-join, UNION, OPTIONAL, MINUS, and two property paths — on **real
Wikidata**, on a **stock** engine, with the fixed O(N) compiler.

**Honest scope on "2.13 B".** The full `latest-truthy` dump is 2.13 B triples (67 GB gz); loading it
reified is disk-infeasible here, and every one of these queries only reads a bounded predicate fragment.
So we extract the relevant predicates from the 2.13 B dump and load *those*: **P106/P27** (occupation,
citizenship) → `wdreal` (20.8 M truthy → 62.4 M Standard-reified); **P279/P131** (subclass, located-in)
→ `wdpaths` (60.5 M). State it exactly this way: real Wikidata data from the 2.13 B truthy dump, queries
over the relevant fragment — not a 2.13 B *construction* claim.

## Results (`phase2_breadth.csv`, 1 warm-up + 3 runs, fixed O(N) compiler)
| query | operator | answers | circuit gates | end-to-end PQE |
|---|---|--:|--:|--:|
| WD-star  | BGP-join      | 2 061   | 8 244     | 4.7 s |
| WD-union | UNION         | 108 072 | 325 552   | 12.8 s |
| WD-opt   | OPTIONAL (⊖)  | _see below_ | | |
| WD-minus | **MINUS (⊖)** | 49 187  | 2 322 637 | 62.3 s |
| WD-path  | path P279+    | 16      | 537       | 2.4 s |
| WD-path2 | path P131+    | 2       | 15        | 0.8 s |

**Five of six operators run exactly on real Wikidata, including non-monotone MINUS (49 k answers,
2.3 M-gate circuit) and both property paths** — precisely the fragment NPCS/SPARQLprov cannot represent,
here at KG scale on a stock engine. Correctness of these operators is established separately
(validation_matrix: WMC==PWE 0.0; E1/E6).

**OPTIONAL (WD-opt) — construct too-large (honest).** The flat OPTIONAL construct (physicists ×
optional citizenship; the ⊖ negative branch ranges over the full P27 relation) exceeds even a raised
**12 M-triple** cap. OPTIONAL is the heaviest operator to construct **flat** at KG scale; the **factored**
construction (variable elimination, E5 / FACTORED_REGIMES) is the intended remedy — an orthogonal axis,
not a limit of exactness (small OPTIONAL is exact per validation_matrix `opt_*` = 0.0 error). Report it as
a construction-cost data point, not a capability gap.

## Figure
`presentation/figures/final/result_r9_8_wikidata_breadth.{pdf,png}` (gen
`presentation/make_wikidata_figure.py`): per-operator end-to-end PQE + circuit size, red = the
non-monotone + path fragment baselines cannot do.
