# RQ3 byte-identity at 100M — Oxigraph vs GraphDB

Independently constructed flat provenance circuits over the same reified WatDiv **100M**; we compare `circuit_sha256` (SHA-256 over the sorted-unique N-Triples of the emitted circuit). Oxigraph 0.5.x (Rust/RocksDB) vs GraphDB 10.7.6 (Java/RDF4J).

| class | oxi built | both-built (comparable) | byte-identical |
|---|---|---|---|
| F | 5/5 | 5 | **5/5** |
| L | 4/5 (rest timeout) | 4 | **4/4** |
| O | 5/5 | 5 | **5/5** |
| S | 7/7 | 7 | **7/7** |
| M | 0/5 (rest timeout) | 0 | **0/0** |

**Total: 21/21 byte-identical** on every cell both engines built.

## Reading

- **F, L, O, S (monotone BGP + OPTIONAL): every comparable cell is byte-identical.** This extends the
  4-engine identity established at 10M (GraphDB/Oxigraph/QLever/MillenniumDB) to 100M on a second,
  independent engine.
- **MINUS (M) timed out on Oxigraph at 100M** (1200 s/cell cap). This is a *construction-cost* limit,
  not a correctness one: MINUS circuits are the heaviest to build and Oxigraph's join evaluation is
  far slower than GraphDB's (see `../CONSTRUCTION_COST_ENGINE.md`). At 10M the writable-pair MINUS
  circuits were byte-identical (GraphDB = Oxigraph); at 100M Oxigraph cannot build them within the cap.
- Content-addressing is plain `SHA256` over canonical N-Triples; the circuit is a certified
  SPARQL-1.1-only, deterministic artifact, so identity is expected and here confirmed at scale.

_Data: `oxi_100m_byteid.csv` (proof: F,L) + `oxi_100m_byteid_losm.csv` (L,O,S,M). Single measured
run/cell. GraphDB reference: `../graphdb-100m/graphdb_100m_assembled.csv`._
