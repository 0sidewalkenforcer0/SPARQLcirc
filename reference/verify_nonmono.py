"""Verify engine-materialized non-monotone circuits (MINUS/OPTIONAL) compute
correct probabilities: WMC over the RDF circuit == possible-world enumeration."""
import sys, itertools, rdflib
import wmc  # provcircuit/wmc.py: general pwe(query, sel, data, P)

C = rdflib.Namespace("urn:circuit:")
RDFT = rdflib.RDF.type
D = "urn:d:"

BASE = {"u1": ("Alice", "likes", "pasta"), "u2": ("Alice", "likes", "pasta"),
        "u3": ("Alice", "livesIn", "Italy"), "u4": ("Bob", "likes", "pasta")}
P = {"u1": 0.5, "u2": 0.3, "u3": 0.7, "u4": 0.6}

EXAMPLES = {
    "minus": dict(nt="data/minus.circuit.nt", sel=["?x"],
                  q=("minus", ("bgp", [("?x", "likes", "pasta")]),
                              ("bgp", [("?x", "livesIn", "Italy")]))),
    "optional": dict(nt="data/optional.circuit.nt", sel=["?x", "?c"],
                     q=("optional", ("bgp", [("?x", "likes", "pasta")]),
                                    ("bgp", [("?x", "livesIn", "?c")]))),
}


def load(nt):
    g = rdflib.Graph().parse(nt, format="nt")
    typ, feeds, tin, minus, ans = {}, {}, {}, {}, {}
    for s, p, o in g:
        if p == RDFT: typ[s] = o
        elif p == C.feeds: feeds.setdefault(o, set()).add(s)      # o is Plus; s feeds it
        elif p == C["in"]: tin.setdefault(s, set()).add(o)        # s is Times; o is leaf
        elif p == C.minuend: minus.setdefault(s, {})["m"] = o
        elif p == C.subtrahend: minus.setdefault(s, {})["s"] = o
        elif p == C.answer: ans[s] = str(o)
    return typ, feeds, tin, minus, ans


def val(node, typ, feeds, tin, minus, asn, memo):
    if node in memo: return memo[node]
    t = typ.get(node)
    if t == C.Times:
        r = all(asn[str(l).replace(D, "")] for l in tin.get(node, ()))
    elif t == C.Plus:
        r = any(val(c, typ, feeds, tin, minus, asn, memo) for c in feeds.get(node, ()))
    elif t == C.Minus:
        m = minus[node]
        r = val(m["m"], typ, feeds, tin, minus, asn, memo) and not val(m["s"], typ, feeds, tin, minus, asn, memo)
    else:  # untyped leaf token
        r = asn[str(node).replace(D, "")]
    memo[node] = r
    return r


def parse_key(k):
    # "A|x=d:Alice|c=NULL" -> frozenset{('x','Alice')}  (NULL / unbound dropped)
    out = []
    for part in k.split("|")[1:]:
        var, _, v = part.partition("=")
        if v != "NULL":
            out.append((var, v.replace(D, "")))
    return frozenset(out)


def run(name):
    spec = EXAMPLES[name]
    typ, feeds, tin, minus, ans = load(spec["nt"])
    toks = list(BASE)
    # circuit WMC per answer root
    circ = {}
    for root, key in ans.items():
        tot = 0.0
        for bits in itertools.product((0, 1), repeat=len(toks)):
            asn = dict(zip(toks, bits))
            if val(root, typ, feeds, tin, minus, asn, {}):
                w = 1.0
                for t in toks: w *= P[t] if asn[t] else 1 - P[t]
                tot += w
        circ[parse_key(key)] = tot
    # ground truth
    truth = {frozenset((v.lstrip("?"), val_) for (v, val_) in k): p
             for k, p in wmc.pwe(spec["q"], spec["sel"], BASE, P).items()}
    keys = set(circ) | {k for k, v in truth.items() if v > 1e-12}
    print(f"=== {name} ===")
    ok = True
    for k in sorted(keys, key=str):
        cp, tp = circ.get(k, 0.0), truth.get(k, 0.0)
        flag = "OK" if abs(cp - tp) < 1e-9 else "MISMATCH"
        if flag != "OK": ok = False
        print(f"  {dict(k)!s:34} circuit={cp:.6f}  PWE={tp:.6f}  {flag}")
    return ok


if __name__ == "__main__":
    names = sys.argv[1:] or ["minus", "optional"]
    allok = all(run(n) for n in names)
    print("\nALL MATCH" if allok else "\nFAILURES")
    sys.exit(0 if allok else 1)
