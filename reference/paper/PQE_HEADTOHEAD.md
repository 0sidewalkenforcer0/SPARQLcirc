# PQE head-to-head — completing NPCS/SPARQLprov into a PQE pipeline (r9.5)

**Question.** NPCS/SPARQLprov emit per-answer *how-provenance* and do **not** compute probabilities.
We *complete* them into a PQE pipeline with the **same** knowledge compiler (so the only variable is the
representation), and compare **end-to-end PQE**:

- **OURS** = compile the shared content-addressed circuit **once** + WMC.
- **THEIRS** = compile **each answer's** flat sum-of-products how-provenance **independently** + WMC, summed.

Harness: `reference/pqe_headtohead.py` (client-side core `pqe_stages`, engine-free, self-test passes;
front end builds each template's circuit with `CircuitRun --construction {flat,factored}`).
Compile+WMC are **client-side and engine-independent** (the circuit is byte-identical across engines,
E10), so PQE runs **once**; per-engine end-to-end = that engine's construct time (matrix) + these stages.

## Findings (honest)

### 1. Capability win — NON-MONOTONE (clean, decisive)
`pqe_stages_flat_10m.csv`: the **5 OPTIONAL (O) templates are non-monotone** (⊖) →
`npcs-cannot-represent-nonmonotone`. MINUS (M) likewise. **NPCS/SPARQLprov flat sum-of-products has
no representation for ⊖ at all** — there is no NPCS PQE number to compare, at any scale. SPARQLcirc does it.

### 2. Amortization win on WatDiv — MODEST (and why)
On the monotone, construct-feasible templates, per-answer / shared PQE is only **~1.3× median (2.6× max)**
at 10M flat, **2–3×** for the larger S templates (SS2 255 ans: 2.1×; SS3 128 ans: 3.0×). It does **not**
explode on WatDiv because:
- the **bound** manifest queries are selective (feasible templates have ≤ ~255 answers), and
- the **many-answer** templates (C3 @434k; the M/MINUS set) are **construct-too-large within 180 s under
  BOTH flat and factored** — for the C (Complex, 8-pattern) class the *construct itself* is the cost, not
  the answer count (CC1 @16 answers also times out). See `pqe_stages_factored_10m.csv`.

So WatDiv's construct-feasible set is too selective to show the per-answer explosion, and its high-answer
set is not construct-feasible. **WatDiv is the wrong vehicle for the amortization win.**

### 3. Amortization win — DRAMATIC on the controlled families (the right vehicle)
`e11_scale.csv` (E11, in-memory, same compiler both sides, answers that genuinely share provenance):
per-answer / shared **time_win grows with answer count** — 1.0× (N=50) → 4.7× (200) → 6.1× (500) →
**8.2× (N=1000)**, bounded only by the shared subterm size. This is the "compile once vs compile N times"
result, and it is where the shared circuit pays off.

## The honest bottom line (for positioning)
- **Non-monotone**: SPARQLcirc uniquely enables exact PQE; NPCS/SPARQLprov cannot represent it. ✅
- **High answer-sharing / unbound**: shared-circuit PQE amortizes (E11: up to 8.2× at 1000, growing). ✅
- **Selective monotone (WatDiv-bound)**: construct dominates (WMC ≤ 0.6%) and our construct is 6–8× the
  NPCS provenance SELECT; the modest (~1.3–3×) PQE amortization does **not** recoup it → **NPCS is
  end-to-end faster there.** State this plainly; do **not** claim "faster than NPCS on selective queries."

Claim to make: *exact PQE including non-monotone (which NPCS/SPARQLprov cannot do), and amortization at
answer-scale* — not construction speed.

## Data (for later analysis)
- `pqe_stages_flat_10m.csv` — 10M flat: per-template answers, shared vs per-answer PQE ms, per-answer status.
- `pqe_stages_factored_10m.csv` — 10M factored (S built; C construct-too-large even factored).
- `e11_scale.csv` — synthetic amortization curve (N vs shared_ms / perans_ms / time_win).
- `construction_matrix_*.csv` — the 4-engine × 2-scale construct times to combine per engine.
- (100M flat PQE: `pqe_stages_flat_100m.csv` when the run completes.)
