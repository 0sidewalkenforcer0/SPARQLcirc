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

## Regime 2 — SOURCE-BOUND selective query: factored is safe, flat suffices
The deployed rdfstar/wikidata experiments bind the source for isolation (`RDFSTAR_RESULTS.md`,
`wikidata/WIKIDATA_REIF_EQUIV.md`). Binding makes flat already tiny; with source-restriction pushdown (below)
factored is now competitive everywhere:
- **star (local fan-out at the bound hub)** — S-star: a single user likes/subscribes/buys many things, so even
  bound there is a local cross-product; factored folds the arms → **9.5× smaller** (100M: 874→92 gates).
- **selective chain** — L-path/F-snow: no cross-product to fold (each hop is ~1:few), so flat is already
  minimal (100M 158/26 gates) and factored **ties** it (100M 249/35 gates — same order, a few extra ⊕ from
  the marginal structure). Answers + WMC identical to flat (verified, max diff 6e-17).

100M paired (`rdfstar_factored_vs_flat.csv`, all ISO-OK, Standard≡RDF-star per mode):

| shape | flat gates | factored gates | verdict |
|---|--:|--:|:--|
| S-star (reconvergent) | 874 | 92 | factored 9.5× |
| L-path (chain) | 158 | 249 | tie |
| F-snow (snowflake) | 26 | 35 | tie |
| M-minus (non-monotone) | 15 | 15 | identical (operator plan) |

## The rule
> factored wins when eliminating a join variable folds a **cross-product** into shared marginals — unbound
> reconvergent joins (flat exponential, Regime 1), or bound queries with local fan-out (stars). On a selective
> chain there is no cross-product, so factored ties flat. It never loses now that the intermediate is
> source-restricted.

## Source-restriction pushdown (DONE)
The bound-chain blowup used to be real: without pushdown, factored's elimination message for an interior
variable spanned the *full unrestricted* relation (L-path 10M: **299 762 gates / 186 s**, touching 299 647
base tokens vs flat's 49). Fixed in `FactoredBgpRewriter`: when the BGP carries a constant subject/object the
query is SELECTIVE, so each base CONSTRUCT is **semi-joined to the rest of the BGP** (reify the other patterns
as context) — only rows that participate in a full match materialise. L-path 10M dropped to **143 gates /
513 ms**, touching **49 base tokens (== flat)**; correctness unchanged (WMC parity with flat). Unbound BGPs
(no constant) keep plain base scans, so Regime 1's win is untouched (verified: identical factored gate counts
before/after). This is the same "restrict the base relation to the source's reach" idea as the property-path
route.
