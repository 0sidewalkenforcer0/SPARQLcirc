# Flat vs factored construction — when each wins

Two construction modes emit the SAME circuit semantics (same answers, same WMC) but different circuit
*structure*:

- **flat** = sum-of-products: one ⊗ per derivation, ⊕ them per answer. Size ≈ #derivations ≈ **product**
  of per-join-variable branching. Read-only (one CONSTRUCT per derivation), no feedback.
- **factored** = variable elimination: marginalise each non-answer join variable into a shared ⊕, keep ⊗
  only across eliminated boundaries. Size ≈ **sum** of the per-boundary marginal relations. Needs a
  writable endpoint (feeds each message relation back).

factored's win = **folding a cross-product into a sum of marginals**. It helps iff a cross-product exists to
fold; when the flat form is already minimal, there is nothing to save.

## Regime 1 — UNBOUND reconvergent conjunctive query: factored wins (its design case)
Unbound k-hop chain over a fully-connected layered graph (W nodes/layer, maximal reconvergence). Every
(v0, vk) answer has W^(k-1) derivations, so flat enumerates ~W^(k+1) products — **exponential in the path
length** — while factored's interior-variable elimination is **polynomial** (~k·W²). In-memory CircuitRun,
W=4 (`unbound_factored_vs_flat.py` → `watdiv/unbound_factored_vs_flat.csv`):

| k | flat gates | factored gates | flat/factored | flat ms | factored ms |
|--:|--:|--:|--:|--:|--:|
| 2 | 80 | 160 | 0.5× | 131 | 203 |
| 3 | 272 | 400 | 0.7× | 195 | 317 |
| 4 | 1 040 | 752 | 1.4× | 355 | 444 |
| 5 | 4 112 | 1 216 | 3.4× | 843 | 582 |
| 6 | 16 400 | 1 792 | 9.2× | 2 114 | 692 |
| 7 | 65 552 | 2 480 | **26.4×** | 6 866 | 855 |

flat ×4 per hop (exponential); factored gate-count differences 240/352/464/576/688 → quadratic. Both size
AND build time diverge; answers 16/16 identical in both modes. **This is the regime factored is for** — and
the gap grows without bound in the path length.

## Regime 2 — SOURCE-BOUND selective query: flat usually suffices
The deployed rdfstar/wikidata experiments bind the source for isolation (`RDFSTAR_RESULTS.md`,
`wikidata/WIKIDATA_REIF_EQUIV.md`). Binding makes flat already tiny, so:
- **star (local fan-out at the bound hub)** — S-star: a single user likes/subscribes/buys many things, so even
  bound there is a local cross-product; factored folds the arms → **9.5× smaller** (100M: 874→92 gates).
- **selective chain** — L-path/F-snow: no cross-product to fold (each hop is ~1:few), flat is already
  minimal (90/7 gates). An *ideal* factored would only tie; the *current* implementation actively blows up
  (L-path 10M: 299 762 gates) because its elimination messages are **not source-restricted** — it
  marginalises the full unrestricted relation (touches 299 647 base tokens vs flat's 49) before filtering to
  the source. This is an implementation gap, not a property of factored-on-chains (see below).

## The rule
> factored wins when eliminating a join variable folds a **cross-product** into shared marginals — unbound
> reconvergent joins (flat exponential), or bound queries with local fan-out (stars). It offers nothing on a
> selective chain (flat already minimal), and the current build even over-materialises there.

## Known gap → optimisation
The bound-chain blowup is fixable: **push the source (and already-eliminated bindings) into the elimination
message CONSTRUCTs**, the same "restrict the base relation to the source's reachable set" trick already used
on the property-path route. With that, bound selective chains drop from ~300k gates to ~flat (a tie), and the
net factored win stays where it belongs — Regime 1.
