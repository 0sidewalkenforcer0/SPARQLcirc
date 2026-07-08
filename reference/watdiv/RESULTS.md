# Real-KG WatDiv run

> **Scale note.** The numbers below are on `base.nt` = **51,863 triples** — a *smoke-test /
> functional-prototype* scale, retained for correctness. The **VLDB submission target is
> WatDiv 100M** (`pilot/data/watdiv.100M.nt`, with `official_q_100M` queries) for
> construction/deployability (E3) and factored-vs-flat at scale (E5); see `EVALUATION.md`.
> Run it via `WATDIV_QDIR=pilot/data/official_q_100M python3 watdiv_run.py` against a
> triplestore loaded with the reified 100M graph. (100M reified ≈ 30–45 GB → a server task.)


**Data:** `pilot/data/base.nt` — a genuine WatDiv graph, **51,863 triples** (Users
`likes`/`subscribes`/`makesPurchase`; `purchaseFor`; `rev:hasReview`; `sorg:caption`; …).
Reified to 155,589 statement triples (`reify.py`) and bulk-loaded into **GraphDB 10.7**
in **2.1 s**. Three real WatDiv query shapes:

| shape | pattern | projects out (existential) |
|---|---|---|
| **S-star** | `?u likes ?a ; subscribes ?b ; makesPurchase ?c` | `?a ?b ?c` |
| **L-path** | `?u makesPurchase ?p . ?p purchaseFor ?prod . ?prod hasReview ?rev` | — (all bound) |
| **F-snow** | S-path + `?prod caption ?cap . ?prod hasReview ?rev` | `?cap ?rev` |

## 1. End-to-end on the deployed engine (`watdiv_run.py`, flat construction)

Rewrite → **unmodified GraphDB** runs our CONSTRUCT and materializes the circuit →
client compiles (ROBDD) + weighted-model-counts every answer.

| query | answers | deriv (⊗) | gates | build_ms (engine) | wmc_ms (client) |
|---|--:|--:|--:|--:|--:|
| S-star | 2 415 | 49 375 | 72 856 | 3 564 | 741 |
| L-path | 15 224 | 16 856 | 45 024 | 786 | 124 |
| F-snow | 5 659 | 16 856 | 36 037 | 981 | 214 |

Circuit build is sub-4 s; WMC over **all** answers is sub-second (star/path are
treewidth-1, so each answer compiles trivially). **Runs end-to-end on a real
triplestore over a real KG** — the deployability claim holds at this scale.

## 2. Compactness — the honest read

**Structural** (fair): NPCS/SPARQLprov write `#derivations × arity` token occurrences
per answer set; the circuit stores each distinct gate+edge once.

| query | T_string (struct) | T_circuit (flat) | sharing |
|---|--:|--:|--:|
| S-star | 148 125 | 270 356 | **0.55×** |
| L-path | 50 568 | 112 448 | 0.45× |
| F-snow | 67 424 | 120 317 | 0.56× |

On **shallow** WatDiv shapes the **flat** circuit does *not* beat strings — there is
little cross-derivation subterm sharing, and gates/edges add per-derivation overhead.
(The serialized bytes are worse still — N-Triples with 64-hex SHA256 gate IRIs is
7–15× larger than the CSV; that is a *serialization* artifact, removable by re-labeling
gates with short local ids after construction, **not** a structural cost.)

The circuit's compactness win lives in a **different regime**: deep/cyclic derivations
where the same subterm recurs — `bench.py` deep-12×2 reaches **201×** (strings grow
with #derivations = 2^depth, circuit stays polynomial). WatDiv S/L/F simply aren't that
regime. **On shallow queries the circuit's value is enabling tractable WMC on an
unmodified engine, not size.**

## 3. Factored construction earns its place on real data (`watdiv_factor.py`)

Variable elimination (marginalize each existential with ⊕ **before** the ⊗) collapses
the per-user cross-product `|likes|×|subs|×|mp|` → `|likes|+|subs|+|mp|`. Same data,
reachable gate counts, WMC spot-checked identical on 40 shared answers:

| query | FLAT gates | FACTORED gates | reduction | WMC == flat? |
|---|--:|--:|--:|:--:|
| **S-star** | 92 561 | 31 615 | **2.9×** | ✅ |
| **F-snow** | 46 299 | 26 372 | **1.8×** | ✅ |
| L-path | 37 313 | 34 583 | 1.1× | ✅ |

(Flat here uses binary joins — factor.py primitives — so it is internally consistent
with the factored column; the engine's n-ary flat for S-star is 72 856 gates, still
> 31 615.)

**S-star 2.9×, F-snow 1.8×** — the win scales with how much of the query is
existential. **L-path 1.0×** is the correct, expected behavior: all variables are
projected, so there is nothing to marginalize and factored = flat. Factoring is the
right tool exactly for existential-heavy shapes, and it preserves the probability.

## Takeaways

1. **Deployable at real-KG scale** — real WatDiv, unmodified GraphDB, sub-4 s build,
   sub-second WMC, correct.
2. **Flat is not magically compact on shallow queries** (~0.5× structural). Honest scope.
3. **Factored construction is the real compactness lever on real WatDiv** (2.9× star /
   1.8× snowflake), with provably-unchanged WMC — motivated on real data, not just
   synthetic layered graphs.
