"""Verify engine-materialized non-monotone circuits (MINUS/OPTIONAL) compute correct probabilities:
WMC over the RDF circuit == possible-world enumeration. Answers are recovered TERM-AWARE from the
structured c:binding nodes (via circuit_io), not the lossy c:answer string."""
import sys, os
import wmc, compile_bdd, circuit_io

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = {"u1": ("Alice", "likes", "pasta"), "u2": ("Alice", "likes", "pasta"),
        "u3": ("Alice", "livesIn", "Italy"), "u4": ("Bob", "likes", "pasta")}
P = {"u1": 0.5, "u2": 0.3, "u3": 0.7, "u4": 0.6}
Pf = {"urn:d:" + k: v for k, v in P.items()}                          # circuit leaves are urn:d: token IRIs

EXAMPLES = {
    "minus": dict(nt="data/minus.circuit.nt", sel=["?x"],
                  q=("minus", ("bgp", [("?x", "likes", "pasta")]),
                              ("bgp", [("?x", "livesIn", "Italy")]))),
    "optional": dict(nt="data/optional.circuit.nt", sel=["?x", "?c"],
                     q=("optional", ("bgp", [("?x", "likes", "pasta")]),
                                    ("bgp", [("?x", "livesIn", "?c")]))),
}

def run(name):
    spec = EXAMPLES[name]
    circ, answers, bindings = circuit_io.parse(open(os.path.join(HERE, spec["nt"])).read())
    cw = {circuit_io.answer_key(bindings[g]): compile_bdd.wmc_enum(circ, g, Pf) for g in answers}
    truth = {}
    for k, p in wmc.pwe(spec["q"], spec["sel"], BASE, P).items():
        if p > 1e-12:
            d = dict(k)                                               # base values are IRIs under urn:d:
            truth[circuit_io.answer_key({sv.lstrip("?"): (circuit_io.canon_iri("urn:d:" + d[sv]) if sv in d else "u")
                                         for sv in spec["sel"]})] = p
    keys = set(cw) | set(truth)
    print(f"=== {name} ===")
    ok = True
    for k in sorted(keys):
        cp, tp = cw.get(k, 0.0), truth.get(k, 0.0)
        flag = "OK" if abs(cp - tp) < 1e-9 else "MISMATCH"
        if flag != "OK": ok = False
        print(f"  {k:44} circuit={cp:.6f}  PWE={tp:.6f}  {flag}")
    return ok

if __name__ == "__main__":
    names = sys.argv[1:] or ["minus", "optional"]
    allok = all(run(n) for n in names)
    print("\nALL MATCH" if allok else "\nFAILURES")
    sys.exit(0 if allok else 1)
