# G5 — real SPARQLprov rewriter (built + run locally)

**Goal:** run SPARQLprov's *released* rewriter to back the `T_string` cost model with the real system,
not just our NPCS reimplementation. **Status: DONE** — built and run from the author-provided artifact.

## Artifact + build

- Source: `SPARQLprov-experiments.zip` from <https://relweb.cs.aau.dk/sparqlprov/> (2021 artifact).
  SPARQLprov is a **C++ query-rewriter** over an **SPM polynomial semiring** (`SPMPolynomial.{hpp,cpp}`,
  `rewritter.cpp`) — *not* GProM-based.
- Build (`SPARQLprov/`): `cmake .. -DCMAKE_POLICY_VERSION_MINIMUM=3.5 -DCMAKE_PREFIX_PATH=$CONDA_PREFIX`
  then `make` (CMakeLists pins CMake 2.8 + a hardcoded `/usr/local/boost_1_76_0`; the policy shim +
  conda Boost on `CPATH` fix it). Unit tests: **`SPMPolynomialTest` → 9/9, "No errors detected".**
- Run: `./rewrite <scheme> <query.sparql>`, scheme ∈ `n` (named graphs) / `s` (standard reif) / `w`
  (Wikidata) — matches our Reification schemes.

## What the real rewriter emits

`./rewrite s queries/wikidata/star.sparql` returns a **SELECT** that, per answer, materializes the
**how-provenance polynomial** as URI-encoded strings via `BIND(IRI(CONCAT(...)))`: one
`?prov_sum_sum_product_i_statement` var per triple pattern (the reified statement node), combined into
`?prov_sum_sum_product` (a ⊗ / product), `?prov_sum_sum` and `?prov_sum` (⊕ / sums) — i.e. an explicit
**sum-of-products** expression, one row per answer. For MINUS it emits an SPM **difference** operator:
`?prov_sum_difference_1_*` (minuend provenance) and `?prov_sum_difference_2_*` (subtrahend), i.e. a monus.

Rewritten-query blow-up (input → rewritten, chars):

| shape | input | rewritten | ratio |
|---|--:|--:|--:|
| watdiv/S1 (star)    | 566 | 4619 | 8.2× |
| watdiv/L1 (linear)  | 233 | 1790 | 7.7× |
| watdiv/F3 (snowflake)| 424 | 3229 | 7.6× |
| wikidata/star       | 423 | 2912 | 6.9× |
| wikidata/minus      | 850 | 6916 | 8.1× |

## Findings

- **The `T_string` baseline is now backed by the real system.** SPARQLprov materializes a per-answer
  sum-of-products (with a difference operator for MINUS) provenance polynomial **as query results** —
  exactly the "per-answer how-provenance string" class E2/G2b modeled and G2b measured via the real
  `NpcsRewriter`. Confirmed on the actual SPARQLprov binary, its own WatDiv/Wikidata query set.
- **It stops at provenance — no probability.** The rewriting ends at `BIND(IRI(CONCAT(...)))` that
  *names* the provenance; there is no model counting / WMC. Same boundary as NPCS: SPARQLprov and NPCS
  compute **how-provenance and stop**; our contribution is the compile+WMC (PQE) neither performs
  (CANONICAL_TIMINGS: 184 ms compile+WMC for all 14 908 TPC-H Q3 answers).
- **MINUS is an SPM `difference` (monus), materialized inline.** SPARQLprov models the non-monotone case
  in its polynomial (unlike a plain how-provenance semiring). Whether its inline-difference rewriting is
  *semantically guarded* vs our ⊖ gate (E11 Result 3) needs running both on data and diffing against PWE
  — a follow-up (the SPARQLprov queries expect its own reified layout + Virtuoso named graphs).

## Caveats

- Measured here: the **rewriting** (query → provenance-SELECT, and its size). The end-to-end `T_string`
  **result bytes on data** need loading SPARQLprov's reified dataset + running the SELECT (its harness
  targets Virtuoso named graphs); G2b already gives result-byte numbers for the same *class* via the real
  NpcsRewriter (P2-unbound 19.9 MB). Running SPARQLprov's SELECTs on our GraphDB is the natural extension.
- 2021 artifact built with a CMake-policy shim + conda Boost 1.91; `rewrite`/`SPMPolynomialTest` binaries
  under `sparqlprov/SPARQLprov-experiments/SPARQLprov/build/` (not committed — external artifact).
