# RQ3 byte-identity at 100M — cross-engine summary

Independently constructed flat provenance circuits over reified WatDiv **100M**; each engine's
`circuit_sha256` is compared against GraphDB (Java/RDF4J). Read-only QLever covers the
monotone+OPTIONAL fragment (no MINUS); Oxigraph (writable) also covers MINUS where it builds.

| engine | language | comparable cells | byte-identical |
|---|---|---:|---:|
| Oxigraph 0.5.x | Rust | 21 | **21/21** |
| QLever | C++ | 20 | **20/20** |

**At 100M: GraphDB = Oxigraph = QLever, byte-for-byte on every comparable F/L/O/S cell**
(plus MINUS for the GraphDB=Oxigraph writable pair where it builds). Combined with the 4-engine
identity at 10M (which adds MillenniumDB), the content-addressed circuit is engine-independent at
scale. Construction *time* varies widely by engine (QLever built all F/L/O/S in ~3.5 min vs
Oxigraph's hours — see CONSTRUCTION_COST_ENGINE.md), but the emitted artifact is identical.

_Data: graphdb-100m/, oxigraph-100m/, qlever-100m/. MillenniumDB-100M pending (4th engine)._
