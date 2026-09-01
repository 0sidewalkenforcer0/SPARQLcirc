"""Regression for compile_portfolio (the ProvSQL-style exact-probability portfolio).

Checks:
  1. On the engine-materialized gallery circuits, portfolio == our OBDD == possible-world enumeration
     (the independent oracle), whichever method the portfolio picks.
  2. read-once path: on a synthetic read-once tree, method == "read-once" and the value is exact
     (== OBDD == closed form).
  3. read-once DETECTION matters: on a circuit with a shared token the naive read-once formula is WRONG,
     and the portfolio must NOT use it (falls to possible-worlds) and stays correct.
  4. compilation-path ENCODING: the Tseitin CNF the d4 path feeds (export_cnf) weighted-model-counts to
     the same value (validated here via the CNF's own brute-force WMC, so the encoding is checked even
     though d4 itself is x86-only / server-run).
The d4 binary path (method "compilation-d4") is exercised on the server (D4 set); here D4 is unset so the
general fallback is the OBDD — still cross-checked against PWE.
"""
import sys
import compile_portfolio as portf
import compile_bdd, export_cnf, circuit_io, verify_all

TOL = 1e-9


def main():
    ok = True

    # (1) gallery circuits: portfolio == OBDD == PWE
    print("=== (1) gallery: portfolio == OBDD == PWE ===")
    for name in ["drug", "selfjoin", "minus", "optional"]:
        s = verify_all.REG[name]
        circ, answers, bindings = circuit_io.parse(open(s["nt"]).read())
        Pf = {"urn:d:" + k: v for k, v in s["P"].items()}
        for g in sorted(answers):
            pp, method = portf.probability(circ, g, Pf)
            pb = compile_bdd.probability(circ, g, Pf)[0]
            pe = compile_bdd.wmc_enum(circ, g, Pf)
            good = abs(pp - pb) < TOL and abs(pp - pe) < TOL
            ok &= good
            print(f"  [{name:8}] {circuit_io.answer_key(bindings[g]):30} portfolio={pp:.6f} "
                  f"({method:15}) OBDD={pb:.6f} PWE={pe:.6f} {'OK' if good else 'FAIL'}")

    # (2) synthetic READ-ONCE tree:  ⊕( ⊗(a,b), ⊗(c,d) ),  a,b,c,d distinct
    print("\n=== (2) read-once path (distinct leaves) ===")
    ro = {"a": ("leaf", "a"), "b": ("leaf", "b"), "c": ("leaf", "c"), "d": ("leaf", "d"),
          "t1": ("times", ("a", "b")), "t2": ("times", ("c", "d")), "r": ("plus", ("t1", "t2"))}
    P = {"a": 0.5, "b": 0.4, "c": 0.3, "d": 0.2}
    closed = 1 - (1 - 0.5 * 0.4) * (1 - 0.3 * 0.2)
    detect_ro = portf.is_read_once(ro, "r")
    pp, method = portf.probability(ro, "r", P)
    pb = compile_bdd.probability(ro, "r", P)[0]
    c2 = detect_ro and method == "read-once" and abs(pp - closed) < TOL and abs(pp - pb) < TOL
    ok &= c2
    print(f"  is_read_once={detect_ro} method={method} portfolio={pp:.6f} closed={closed:.6f} "
          f"OBDD={pb:.6f} {'OK' if c2 else 'FAIL'}")

    # (3) SHARED token:  ⊕( ⊗(a,b), ⊗(a,c) ) = a∧(b∨c).  Naive read-once OVER-counts; must not be used.
    print("\n=== (3) read-once detection prevents the shared-variable error ===")
    sh = {"a": ("leaf", "a"), "b": ("leaf", "b"), "c": ("leaf", "c"),
          "t1": ("times", ("a", "b")), "t2": ("times", ("a", "c")), "r": ("plus", ("t1", "t2"))}
    Ps = {"a": 0.5, "b": 0.4, "c": 0.3}
    correct = 0.5 * (1 - (1 - 0.4) * (1 - 0.3))               # P(a)·P(b∨c) = 0.29
    wrong_ro = portf.prob_read_once(sh, "r", Ps)              # 1-(1-ab)(1-ac) = 0.32  (WRONG: ignores shared a)
    pp, method = portf.probability(sh, "r", Ps)
    pb = compile_bdd.probability(sh, "r", Ps)[0]
    c3 = (not portf.is_read_once(sh, "r")) and method != "read-once" \
        and abs(pp - correct) < TOL and abs(pp - pb) < TOL and abs(wrong_ro - 0.32) < TOL
    ok &= c3
    print(f"  is_read_once={portf.is_read_once(sh, 'r')} method={method} portfolio={pp:.6f} "
          f"correct={correct:.6f} (naive-read-once would give {wrong_ro:.6f}) OBDD={pb:.6f} {'OK' if c3 else 'FAIL'}")

    # (4) compilation-path ENCODING: the Tseitin CNF d4 would consume WMC-s to the same value.
    print("\n=== (4) Tseitin CNF (the d4 path input) encodes the same function ===")
    e = export_cnf.export(sh, "r", Ps)
    cnf_wmc = export_cnf.cnf_wmc_bruteforce(e["nvars"], e["clauses"], e["weights"])
    c4 = abs(cnf_wmc - correct) < TOL
    ok &= c4
    print(f"  CNF-WMC={cnf_wmc:.6f} == correct={correct:.6f}  {'OK' if c4 else 'FAIL'}  "
          f"(d4 runs this CNF on the server; encoding validated here)")

    print("\nALL OK" if ok else "\nFAILURES")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
