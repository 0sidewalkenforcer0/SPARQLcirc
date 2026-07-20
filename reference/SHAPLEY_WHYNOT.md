# Shapley attribution + why-not on the shared circuit (implemented + validated)

Both explanation tasks are read off the **same** compiled circuit the PQE pipeline
already builds — no extra machinery. Code: `reference/shapley.py`; experiment:
`reference/shapley_experiment.py` → `reference/shapley_whynot.csv`.

## Shapley — two routes, exact agreement
- **Ground truth** `shapley_bruteforce`: the definition, enumerating all coalitions
  `S ⊆ X\{i}`, `Shapley_i = Σ_S |S|!(n-1-|S|)!/n! · (φ(S∪{i}) − φ(S))`, over the
  Boolean lineage `φ` (⊗=AND, ⊕=OR, ⊖=AND-NOT).
- **Tractable** `shapley_circuit`: on the compiled **ROBDD** (deterministic +
  decomposable) via the integral identity
  `Shapley_i = ∫₀¹ [ Pr(φ | x_i=1, others iid p) − Pr(φ | x_i=0, others iid p) ] dp`,
  because `∫₀¹ pᵏ(1−p)^{n-1-k} dp = k!(n-1-k)!/n!` is exactly the Shapley weight.
  Each `Pr(·)` is one weighted model count with every other variable given the
  **symbolic** weight `p` (a polynomial returned by a single linear pass over the
  circuit); the difference is integrated term by term. Polynomial in the compiled
  circuit size (cf. Arenas et al. 2021, SHAP on d-D circuits). Exact rationals.

## Result (`shapley_whynot.csv`) — the E1 gallery + the drug example
- **59 answer circuits** across the whole non-monotone fragment (AND / UNION /
  MINUS / OPTIONAL / self-join / shared-prefix / composite MINUS).
- `shapley_circuit == shapley_bruteforce` **exactly (max_abs_diff = 0) on all 59**;
  Shapley **efficiency** (`Σ_i Shapley_i = φ(X)`) holds on all.
- Sanity, drug example `Warfarin = p1 ⊖ p2`: **Shapley(p1)=+1/2, Shapley(p2)=−1/2**
  (the subtrahend edge contributes negatively). `share_union` gives the shared
  prefix `t1=2/3` vs `t2=t3=1/6`; `selfjoin` is symmetric `s1=s2=1/2`.

## Why-not — subtrahend of the answer's ⊖-gate
`why_not(circ, root)` returns, per minus-gate in the answer cone, the base triples
in its **subtrahend** cone — the derivations whose presence excludes the binding.
Reported on **all 12 difference answers** (MINUS/OPTIONAL); e.g. Alice's MINUS answer
`u1,u2 ⊖ u3` → why-not `u3` (the `livesIn Italy` triple), `minus_union` A → `t3,t5`
(the two subtrahend branches). Matches Example (Warfarin why-not = `p2`).

Reproduce: `cd reference && python3 shapley_experiment.py` (or `--selftest`).
