# E11 — per-answer knowledge compilation (NPCS/SPARQLprov how-provenance) vs our shared circuit

NPCS and SPARQLprov emit how-provenance **per answer** (a polynomial / string) and do **not** compute
probabilities. E11 *completes* them into a PQE pipeline with **our** knowledge compiler, so both sides use
the **same** compiler and the only variable is the representation. This answers the sharpest reviewer
question — *"is your win the shared circuit, or your compiler?"* — and cross-validates correctness.

Held constant: query, data, leaf probabilities, variable order, and compiler. Sides:
- **OURS** = the factored shared circuit (E2/E5), compiled **once** into a shared ROBDD;
- **THEIRS** = the per-answer flat sum-of-products (the NPCS/SPARQLprov string), each answer compiled alone.

Run: `python3 e11_per_answer_vs_shared.py` → `e11_results.csv`, `e11_minus.csv` (pure Python, zero deps).

## Result 1 — the win is REPRESENTATION-side, not compilation-side

| instance | answers | deriv | T_string | T_circuit | **repr_win** | compiled (ours = theirs) | **compiled_win** | parity |
|---|--:|--:|--:|--:|--:|--:|--:|--:|
| drug | 2 | 3 | 9 | 25 | 0.4× | 8 | 1.00× | 0 |
| prefix-d8×N16 | 16 | 16 | 144 | 93 | 1.5× | 144 | 1.00× | 0 |
| layered-4×3 | 3 | 81 | 324 | 147 | 2.2× | 763 | 1.00× | 0 |
| layered-4×4 | 4 | 256 | 1024 | 256 | 4.0× | 4676 | 1.00× | 0 |
| **deep-8×2** | 2 | 256 | 2048 | 156 | **13.1×** | 643 | 1.00× | 0 |

(E2 extends `repr_win` to **201×** at `deep-12×2`; its naive-order OBDD blows up — the E4 caveat — so the
*compiled* cell there is #P-bound on **both** sides and is omitted. `deep-8×2` shows the pattern.)

**Reading it:**
- **`repr_win = T_string / T_circuit`** grows with sharing (13.1× here, 201× in E2): our factored shared
  circuit is far more compact than the per-answer strings NPCS/SPARQLprov must emit.
- **`compiled_win = 1.00×`, exactly, everywhere.** Knowledge compilation is inherently **per answer** —
  distinct answers are distinct Boolean functions and share no interior BDD nodes — so the shared circuit
  does **not** shrink the compiled artifact, and the compiler's ite-cache makes compile cost
  representation-independent.
- **`parity = 0` (Δ ≤ 1e-16).** Our shared-circuit PQE gives the **same** probability as compiling their
  per-answer how-provenance — an independent confirmation that our circuit is provenance-equivalent to
  NPCS/SPARQLprov, and (vs PWE, where enumerable) exact.

**Conclusion.** The advantage is **not a compiler trick** (the compiler is neutral: `compiled_win = 1×`).
It is the **representation**: an *unmodified engine* builds our compact shared circuit (E3), whereas the
per-answer how-provenance the baselines emit is up to **201× larger** to materialize / store / transmit
(E2). Given the provenance, PQE costs the same on either — but *producing* it is where the baselines pay.

## Result 2 — SPARQLprov's MINUS how-provenance compiles to the WRONG probability

Disjoint-operand MINUS (`{?x likes ?y} MINUS {?z owns ?w}`, no shared variable). Compiling each system's
per-answer how-provenance with our compiler:

| answer | ours (guarded, W3C) | SPARQLprov (unguarded DIFF) | PWE (truth) | verdict |
|---|--:|--:|--:|---|
| ?x=A, ?y=X | 0.5000 | 0.2500 | 0.5000 | ours OK; **SPARQLprov WRONG** |
| ?x=B, ?y=Y | 0.5000 | 0.2500 | 0.5000 | ours OK; **SPARQLprov WRONG** |

SPARQLprov's released rewriter realizes MINUS as an *unguarded* DIFF (`A OPTIONAL B`); on disjoint operands
it over-subtracts, so its how-provenance yields **0.25 instead of 0.50**. Ours (the W3C shared-variable
guard) matches possible-world enumeration. This is the concrete **probabilistic** consequence of the MINUS
bug — not merely a structural mismatch, but a wrong answer once you compute a probability from it.

## Why E11 matters for the paper

1. **Neutralizes the "it's just your compiler" objection** — compiled size/cost is identical (1.00×); the
   contribution is the representation an unmodified engine builds, not the compilation step.
2. **Cross-validates correctness** against the baselines' own provenance semantics (Δ ≤ 1e-16), the
   NPCS/SPARQLprov analogue of E7's ProvSQL parity.
3. **Turns the MINUS bug into a measured wrong probability**, not just a code-level finding.
