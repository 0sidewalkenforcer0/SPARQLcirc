# RQ3 byte-identity at 100M — Oxigraph vs GraphDB

circuit_sha256 of the flat circuit, built independently on each engine over the same reified WatDiv 100M.

| cell | identical | circuit_sha256 |
|---|---|---|
| FF1 | ✓ | `57d0c632f5cd8e44e0f2a01d4a37e6cb…` |
| FF2 | ✓ | `f26139cae7a9692a2838a20fd01905e3…` |
| FF3 | ✓ | `f207c0a00082018b703df0d83098bf58…` |
| FF4 | ✓ | `4d1917acf6a8f4ba2cec030de209c95a…` |
| FF5 | ✓ | `76dcaf1041b8189fd188bbb42838ce63…` |
| LL1 | ✓ | `4ef4d3922d50e7e0953b8afb47240498…` |
| LL2 | ✓ | `35d15df64205e5280d0c1f070a238b2b…` |
| LL3 | ✓ | `859ad29edf5220d2f8a344caa1ae953c…` |
| LL4 | ✓ | `6e357be51063612b79512719e90439be…` |

**9/9 byte-identical** (partial: proof run stopped after LL4; L5+O,S,M in continuation). Extends the 4-engine identity from 10M to 100M on an independent engine (Oxigraph 0.5.x, Rust) vs GraphDB (Java/RDF4J).
