# RQ3 byte-identity at 100M — cross-engine summary

Each engine independently constructs the flat provenance circuit over reified WatDiv **100M**;
its `circuit_sha256` (SHA-256 over canonical N-Triples) is compared to GraphDB (Java/RDF4J).
Read-only engines cover monotone+OPTIONAL (F/L/O/S); the writable pair also covers MINUS.

| engine | language | mode | comparable cells | byte-identical |
|---|---|---|---:|---:|
| Oxigraph 0.5.x | Rust | writable | 21 | **21/21** |
| QLever | C++ | read-only | 20 | **20/20** |
| MillenniumDB v1.0.0 | C++ | read-only | 22 | **22/22** |

**At 100M the content-addressed circuit is byte-for-byte identical across GraphDB, Oxigraph,
QLever, and MillenniumDB** on every comparable F/L/O/S cell. With the 4-engine identity at 10M
(same four engines, plus MINUS for the writable pair), the provenance artifact is engine-independent
at both scales — a pure-SPARQL-1.1, deterministic output, not tied to any implementation.

Construction *time* is a per-engine property, not the method's: e.g. all F/L/O/S at 100M took
~3.5 min on QLever and ~12 min on MillenniumDB but hours on Oxigraph (its CONSTRUCT+SHA256 join
evaluation does not scale) — see `CONSTRUCTION_COST_ENGINE.md`. MillenniumDB emits the non-standard
`\'` N-Triples escape; `circuit_cache.canonical_bytes` normalizes it so identity is serialization-
agnostic. Data: `{graphdb,oxigraph,qlever,millenniumdb}-100m/`.
