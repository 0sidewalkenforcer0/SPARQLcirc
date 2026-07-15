# RDF-star reification vs Standard reification — WatDiv 10M (construction + storage)

Follows NPCS's use of alternative reifications. **The provenance circuit is reification-independent**
(proven byte-identical: `reference/verify_g7_circuit_equiv.py` + a real-WatDiv sample), so switching the
input reification changes only **input storage** and **construction**, never the circuit / WMC / answer
probabilities. This page adds the **deployed-engine, real-WatDiv-10M** evidence.

Data: `watdiv-data/watdiv.10M.{reified.nt (Standard), star.ttls (RDF-star, via reify.py --star)}`.
Engines: GraphDB 10.7.6 (`watdiv` = Standard 32.7M; `watdivstar10m` = RDF-star), Oxigraph 0.5.9.
Queries: source-bound (selective) S-star / L-path / F-snow / M-minus. Numbers → `rdfstar_10m.csv`.

## How NPCS reports (for comparability)
NPCS (Asma et al., WWW'24) runs **4 reifications — standard / rdfstar / namedgraph / wikidata — × scale
{10M,100M,200M} × engine {GraphDB, Stardog}**, and reports **query EXECUTION TIME (ms)**. It does **not**
report a reification byte/triple size comparison. So the byte/triple numbers below are ours; the
time numbers are the NPCS-comparable axis.

## Construction + correctness (GraphDB, flat BGP; MINUS via the DIFF plan)
| shape | Standard build_ms | RDF-star build_ms | gates | answers | circuit sha256 (std ⟺ star) |
|---|--:|--:|--:|--:|:--:|
| S-star  |  544 |  695 | 56 |  2 | `824c46ed…` **identical** |
| L-path  |  589 |  537 | 90 | 45 | `ce5759cc…` **identical** |
| F-snow  |  508 |  467 |  7 |  1 | `c7127239…` **identical** |
| M-minus | 1293 |  506 | 10 |  2 | `1bdbcfd8…` **identical** |

- **Every shape: Standard and RDF-star produce the byte-identical circuit** (content-addressed gate IRIs),
  on a deployed engine at 10M — including non-monotone MINUS.
- Construction time is **comparable** (star faster on L/F/M, slightly slower on S-star). RDF-star's benefit
  is storage, not a construction speed-up.

## Cross-engine byte-identity witness (Oxigraph, RDF-star)
S-star on Oxigraph (RDF-star) → same circuit `824c46ed…` as GraphDB. Build **365 s** (~525× slower than
GraphDB's 695 ms): Oxigraph's in-SPARQL `SHA256`/`CONCAT` in the CONSTRUCT does not scale (cf. E10). So
Oxigraph is a correctness witness; GraphDB carries the timing/scale line.

## Storage — TWO metrics, kept distinct (engine-dependent in-store!)
| metric | Standard | RDF-star | ratio |
|---|--:|--:|--:|
| file bytes @10M (serialized)        | 3.6 G | 2.1 G | 1.7× |
| file bytes @100M                    | 37 G  | 21 G  | 1.75× |
| in-store triples @10M · **GraphDB** | 32.7 M (3/fact) | **10.9 M (1/fact)** | **3×** |
| in-store triples @10M · **Oxigraph**| ≈32.7 M | **21.8 M (2/fact)** | 1.5× |

**Caveat (report honestly):** the serialized file is 1 line/fact, but *in-store* the base triple `s p o`
may also be asserted. **GraphDB** keeps the quoted triple as one embedded statement → **1 physical
triple/fact (3× fewer)**; **Oxigraph** additionally asserts the base triple → **2/fact (1.5× fewer)**.
Either way RDF-star beats Standard's 3/fact, but "1 vs 3" holds only at the file level and only on GraphDB.

## Status
Done: 10M, all four BGP+MINUS shapes, GraphDB (Standard vs RDF-star) + Oxigraph witness.
Pending: 100M scale (GraphDB), Wikidata-style on the real Wikidata graph. Named-graph reification needs
engine support (`QueryGuard` currently rejects GRAPH/FROM NAMED) — deferred.
