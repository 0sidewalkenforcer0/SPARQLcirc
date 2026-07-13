# Headline numbers (slide-ready tables)

All values are the committed results (`reference/*.csv`, `reference/watdiv/*.csv`) and the canonical 5-run
timing table (`reference/CANONICAL_TIMINGS.md`, current HEAD, post-`PathIsoSeq`).

## T1 · End-to-end PQE latency (G3, 5-run median)
| query | dataset | answers | construct | compile | WMC | **total** |
|---|---|--:|--:|--:|--:|--:|
| WatDiv S-star | WatDiv 32.7 M | 2 | 10 ms | 2 ms | 0 ms | **12 ms** |
| TPC-H Q3 | TPC-H SF 0.01 (1.26 M) | 14 908 | 3 080 ms | 3 300 ms* | 36 ms | **6.40 s** |
| Wikidata WD-path (`P279+`) | Wikidata 2.13 B | 16 | 2 144 ms | 1 ms | 0 ms | **2.14 s** |

\* Q3 "compile" is the pure-Python **variable ordering** over ~45 k tokens (removable); the ROBDD build + WMC are tiny. WMC ≤ 36 ms everywhere.

## T2 · vs ProvSQL (modified PostgreSQL) — same exact probabilities, no engine fork
**TPC-H Q3-SPJ, per c_mktsegment (G4, 5-run median):**
| segment | answers | ours | ProvSQL |
|---|--:|--:|--:|
| AUTOMOBILE | 11 966 | **4.52 s** | 5.97 s |
| BUILDING | 14 908 | **6.42 s** | 7.60 s |
| FURNITURE | 11 987 | **4.58 s** | 6.05 s |
| HOUSEHOLD | 11 165 | **4.07 s** | 5.59 s |
| MACHINERY | 10 149 | **3.50 s** | 5.00 s |

**Reconvergent query (R8.3), per-customer probability parity `max_abs_error = 0.0`:**
| scale | answers | ours | ProvSQL | parity |
|---|--:|--:|--:|:--:|
| SF 0.01 | 247 | **0.44 s** | 0.73 s | ✓ exact |
| SF 0.1 | 2 086 | 12.95 s | **6.74 s** | ✓ exact |

*(At SF 0.1 ProvSQL is faster — our pure-Python ordering grows with the circuit; probabilities identical either way.)*

## T3 · Compile size vs treewidth (E4) — d-DNNF (d4) vs fixed-order OBDD
**Bounded treewidth (tw=2), growing size:**
| #tokens | OBDD nodes | d-DNNF nodes |
|--:|--:|--:|
| 14 | 44 | 40 |
| 62 | 24 897 | 666 |
| 94 | 299 481 | 1 193 |
| 126 | **times out (>300 s)** | 2 067 |
| 254 | **times out** | 5 270 |

**Growing treewidth (depth 4):**
| tw | OBDD nodes | d-DNNF nodes |
|--:|--:|--:|
| 4 | 960 | 1 169 |
| 6 | 26 502 | 11 908 |
| 8 | 375 501 | 211 964 |

d4 weighted count == expected on **34/34** instances.

## T4 · Compactness: shared circuit vs per-answer strings (E2)
| instance | #derivations | per-answer string | shared circuit | **compactness** |
|---|--:|--:|--:|--:|
| drug (shallow) | 3 | 9 | 25 | 0.4× |
| layered-4×8 | 4 096 | 16 384 | 992 | 16.5× |
| deep-8×2 | 256 | 2 048 | 156 | 13.1× |
| deep-12×2 | 4 096 | 49 152 | 244 | **201.4×** |

## T5 · Shared compile vs per-answer how-provenance (E11) — identical probabilities (Δ=0)
| #answers N | shared ms | per-answer ms | **time win** |
|--:|--:|--:|--:|
| 50 | 0.4 | 2.6 | 6.0× |
| 200 | 1.2 | 8.5 | 6.9× |
| 500 | 4.3 | 26.7 | 6.2× |
| 1 000 | 6.8 | 59.5 | **8.7×** |

## T6 · Construction on an unmodified engine (E3) — build overhead vs plain query
| query | 10 M build (overhead) | 100 M build (overhead) |
|---|--:|--:|
| S-star | 31 ms (3.2×) | 515 ms (6.8×) |
| F-snow | 12 ms (1.8×) | 19 ms (2.1×) |
| L-path | 15 ms (1.9×) | 23 ms (1.6×) |

## T7 · Correctness & portability
- **G6:** OBDD = PWE = d4 on **26/26** real circuits (2 WatDiv S-star + 8 TPC-H Q3 + 16 Wikidata paths).
- **E1:** 171/171 reference WMC == possible-world enumeration; exact for all operators.
- **E10:** byte-identical circuit across **GraphDB, Fuseki, Oxigraph, QLever, MillenniumDB** (Java / Rust / C++).
- **E6:** non-monotone MINUS at 10 M / 100 M, WMC == PWE (Δ ≤ 1.1e-16).
- **G7:** SPARQL-star reification = 1 triple/fact vs Standard 3× (1.9× fewer bytes); circuit identical.

## T8 · Scope / real-KG reach
- **E8 Wikidata 2.13 B:** 33/41 single queries build (8 too-large/OOM); largest circuit ~772 k gates.
- **G8:** WD-path over 2.13 B — peak RSS **166 MB**, 2.3 s.
- **G10:** WatDiv Complex (C1, 8-pattern) builds at 10 M — full L/S/F/C taxonomy covered.
- **Property paths:** P +/*/alt/all — the operator class NPCS/SPARQLprov/ProvSQL do **not** support.
