# E11 — per-answer knowledge compilation (NPCS/SPARQLprov how-provenance) vs our shared circuit

NPCS and SPARQLprov emit how-provenance **per answer** (a polynomial / string) and do **not** compute
probabilities. E11 *completes* them into a PQE pipeline with **our** knowledge compiler, so both sides use
the **same** compiler and the only variable is the representation. This answers *"is your win the shared
circuit, or your compiler?"*, cross-validates correctness, and quantifies the compile-time win at scale.

Held constant: query, data, leaf probabilities, and compiler. Sides:
- **OURS** = the factored shared circuit (E2/E5), compiled **once** into a shared ROBDD;
- **THEIRS** = the per-answer flat sum-of-products (the NPCS/SPARQLprov string), each answer compiled alone.

Run: `python3 e11_per_answer_vs_shared.py` → `e11_results.csv`, `e11_scale.csv`, `e11_minus.csv`.

## Result 1 — representation win (order-independent)

| instance | answers | deriv | T_string | T_circuit | **repr_win** | compiled (worst-case order) |
|---|--:|--:|--:|--:|--:|--:|
| drug | 2 | 3 | 9 | 25 | 0.4× | 1.00× |
| prefix-d8×N16 | 16 | 16 | 144 | 93 | 1.5× | 1.00× |
| layered-4×3 | 3 | 81 | 324 | 147 | 2.2× | 1.00× |
| layered-4×4 | 4 | 256 | 1024 | 256 | 4.0× | 1.00× |
| **deep-8×2** | 2 | 256 | 2048 | 156 | **13.1×** | 1.00× |

`repr_win = T_string / T_circuit` grows with sharing (13.1× here, 201× at `deep-12×2` in E2): our factored
circuit is far more compact than the per-answer strings the baselines must emit/store/transmit. This win
is order-independent.

**The `compiled_win = 1.00×` column is a worst-case artifact, not a law.** It uses a DFS variable order
that places the *shared* structure as a **prefix** (top of the order). A BDD only merges a shared
sub-function that sits at the **bottom** of the order (a common *suffix*); a shared prefix leading to
different suffixes does not merge — so with this order, per-answer and shared compile to the same size.
Result 2 flips the order and the reuse appears.

## Result 2 — compile-time win at scale (the "compile once vs N times" effect)

`N` answers sharing a depth-8 sub-provenance (compiled size `S`), compiled with a **sharing-friendly**
order (selectors on top, shared chain at the bottom, so its sub-BDD merges). `e11_scale.csv`:

| N | shared size | per-answer size | size win | shared ms | per-answer ms | **time win** |
|--:|--:|--:|--:|--:|--:|--:|
| 50 | 58 | 450 | 7.8× | 0.4 | 2.6 | 6.0× |
| 200 | 208 | 1800 | 8.7× | 1.2 | 8.5 | 6.9× |
| 500 | 508 | 4500 | 8.9× | 4.3 | 26.7 | 6.2× |
| 1000 | 1008 | 9000 | 8.9× | 6.8 | 59.5 | **8.7×** |

**The mechanism.** With `N` answers sharing a sub-provenance of compiled size `S`:
- **per-answer** rebuilds that shared sub-BDD **once per answer** → **Θ(N·S)**;
- **ours** (one shared pass, hash-consed) builds it **once**, all `N` answers point to it → **Θ(N + S)**.

So per-answer does ~`S`× more work; the **absolute saving grows linearly with N** (≈ `S·(N−1)` ops). This
is exactly the "1000 answers → we compile once, they compile 1000 times" intuition: at N=1000 we compile
in 6.8 ms vs their 59.5 ms, and the gap widens with N.

**Conditions (kept honest):**
- Needs **actual cross-answer sharing**. Fully independent answers give `N` disjoint functions with nothing
  to reuse (N calls ≈ one call over a disjoint union) — no win. Shared provenance is the common case and
  the premise of a shared circuit.
- For an **OBDD** the win is **order-realizable** (Result 1 vs Result 2 = same instances, different order).
  **d-DNNF / d4** picks its own decomposition/vtree and is far more robust to order — and it is what we use
  for the real compilation (E4) — so the win is more reliable there than with our naive-order OBDD.

## Result 3 — SPARQLprov's MINUS how-provenance compiles to the WRONG probability

Disjoint-operand MINUS (`{?x likes ?y} MINUS {?z owns ?w}`, no shared variable):

| answer | ours (guarded, W3C) | SPARQLprov (unguarded DIFF) | PWE (truth) | verdict |
|---|--:|--:|--:|---|
| ?x=A, ?y=X | 0.5000 | 0.2500 | 0.5000 | ours OK; **SPARQLprov WRONG** |
| ?x=B, ?y=Y | 0.5000 | 0.2500 | 0.5000 | ours OK; **SPARQLprov WRONG** |

SPARQLprov's released rewriter realizes MINUS as an *unguarded* DIFF; on disjoint operands it
over-subtracts, so its how-provenance yields **0.25 instead of 0.50**. Ours (the W3C shared-variable guard)
matches possible-world enumeration — the MINUS bug as a **measured wrong probability**.

## Why E11 matters for the paper

1. **Compile-time win at scale** (Result 2): a shared pass over `N` answers is Θ(N+S) vs Θ(N·S) per-answer
   — the shared circuit is compiled once, not once per answer. The saving grows with N (when answers share
   provenance), on top of the representation win.
2. **Representation win** (Result 1): up to 201× more compact than per-answer strings (E2), the cost the
   baselines pay to *produce* their output regardless of the PQE back-end.
3. **Correctness cross-validation**: our PQE == compiling the baselines' own provenance (Δ ≤ 1e-16), the
   NPCS/SPARQLprov analogue of E7's ProvSQL parity — **except** SPARQLprov's MINUS, which is wrong.
4. **Honest caveat**: for an OBDD the compile-time win is order-dependent (Result 1's naive order hides it);
   d-DNNF/d4 is robust to this. State it — a reviewer will check.
