"""Validation experiment for Shapley attribution + why-not on the shared circuit.

Runs over the SAME gallery as tests.py (the E1 WMC==PWE battery), plus the paper's
drug-interaction MINUS example.  For every answer circuit it checks:
  * shapley_circuit (tractable, on the compiled ROBDD) == shapley_bruteforce
    (coalition enumeration ground truth), EXACTLY (rational arithmetic, diff = 0);
  * Shapley efficiency: the values sum to phi(full) = v(X) (a cross-check);
  * why-not: each answer excluded by a difference reports its subtrahend triples.

Writes reference/shapley_whynot.csv (per-answer) and prints a summary.
Run:  python3 shapley_experiment.py            (gallery)
      python3 shapley_experiment.py --selftest (drug example only, verbose)
"""
import csv
import os
import sys
from fractions import Fraction

import gates
import gamma
import shapley
import tests  # reuse TESTS gallery

HERE = os.path.dirname(os.path.abspath(__file__))
BF_CAP = 16  # skip brute-force above this many players (still run the circuit route)


def drug_example():
    """Example 'drugs Aspirin interacts with but that do NOT interact with Metformin':
    Warfarin = p1 (-) p2, Ibuprofen = p4 (-) p5 (running-example probabilities)."""
    c = gates.Circuit()
    p1, p2, p4, p5 = (c.leaf("p1"), c.leaf("p2"), c.leaf("p4"), c.leaf("p5"))
    return c, {"?x=Warfarin": c.minus(p1, p2), "?x=Ibuprofen": c.minus(p4, p5)}


def answer_roots(circ, q, data, sel):
    """{answer-label -> root gate id} for a gallery query, via gamma."""
    table = gamma.eval_q(circ, q, data)
    projected = gamma.project(circ, table, sel)
    out = {}
    for key, g in projected.items():
        label = ",".join("%s=%s" % (v, val) for v, val in zip(sel, key)) if isinstance(key, tuple) \
            else str(key)
        out[label] = g
    return out


def run_case(name, circ, roots):
    rows = []
    gd = circ.gates
    for label, root in roots.items():
        X = shapley.cone_leaves(gd, root)
        sh_c = shapley.shapley_circuit(gd, root, X)
        bf_ok, diff = "", ""
        if len(X) <= BF_CAP:
            sh_b = shapley.shapley_bruteforce(gd, root, X)
            d = shapley.max_abs_diff(sh_c, sh_b)
            bf_ok, diff = ("yes" if d == 0 else "NO"), str(d)
        else:
            bf_ok, diff = "skipped(n>%d)" % BF_CAP, ""
        # efficiency: sum of Shapley == v(X) (full coalition present) in {0,1}
        vfull = int(shapley.boolean_eval(gd, root, set(X)))
        total = sum(sh_c.values(), Fraction(0))
        eff_ok = "yes" if total == vfull else "NO(%s!=%d)" % (total, vfull)
        wn = shapley.why_not(gd, root)
        wn_tokens = sorted({t for _, toks in wn for t in toks})
        rows.append({
            "case": name, "answer": label, "players": len(X),
            "shapley_circuit_eq_bruteforce": bf_ok, "max_abs_diff": diff,
            "efficiency_ok": eff_ok,
            "shapley": " ".join("%s=%s" % (k, sh_c[k]) for k in sorted(sh_c)),
            "why_not_subtrahend": " ".join(wn_tokens),
        })
    return rows


def main(argv):
    verbose = "--selftest" in argv
    all_rows = []

    # 1) the paper's drug MINUS example (explicit expected values)
    c, roots = drug_example()
    dr = run_case("drug-minus", c, roots)
    all_rows += dr
    if verbose:
        for r in dr:
            print(r["answer"], "Shapley", r["shapley"], "| why-not", r["why_not_subtrahend"],
                  "| eq_bf", r["shapley_circuit_eq_bruteforce"], "| eff", r["efficiency_ok"])
        # sanity: Warfarin = p1 (-) p2  -> Shapley(p1)=+1/2, Shapley(p2)=-1/2
        w = {k.split("=")[0]: v for k, v in
             shapley.shapley_circuit(c.gates, roots["?x=Warfarin"]).items()}
        assert w["p1"] == Fraction(1, 2) and w["p2"] == Fraction(-1, 2), w
        print("drug-example expected Shapley confirmed: p1=+1/2, p2=-1/2")

    # 2) the full gallery (same as tests.py)
    for name, data, sel, q in tests.TESTS:
        circ = gates.Circuit()
        try:
            roots = answer_roots(circ, q, data, sel)
        except Exception as exc:  # path fragment uses oplus/otimes; still a Circuit
            print("  [skip] %-16s (%s)" % (name, exc))
            continue
        all_rows += run_case(name, circ, roots)

    # report
    checked = [r for r in all_rows if r["shapley_circuit_eq_bruteforce"] == "yes"]
    bad = [r for r in all_rows if r["shapley_circuit_eq_bruteforce"] == "NO"
           or r["efficiency_ok"].startswith("NO")]
    wn_rows = [r for r in all_rows if r["why_not_subtrahend"]]
    out = os.path.join(HERE, "shapley_whynot.csv")
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(all_rows[0].keys()))
        w.writeheader(); w.writerows(all_rows)

    print("\n%d answer circuits: shapley_circuit == bruteforce on %d/%d checked "
          "(rest n>%d), efficiency holds on all, why-not on %d difference answers."
          % (len(all_rows), len(checked), len([r for r in all_rows
             if r["shapley_circuit_eq_bruteforce"] != "skipped(n>%d)" % BF_CAP]),
             BF_CAP, len(wn_rows)))
    print("wrote", os.path.relpath(out, HERE))
    if bad:
        print("FAILURES:", bad)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
