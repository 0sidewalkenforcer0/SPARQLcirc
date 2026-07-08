"""Experiment A: does a structure-aware vtree (or PySDD's dynamic `minimize`) let
SDD reach/beat OBDD on the tractable island? Give BOTH compilers a fair
structure-aware variable order, then sweep SDD vtrees.

Finding: with a reasonable order, OBDD is <= every SDD variant (including
`minimize`) on all tested families. SDD size is highly vtree-sensitive (caveat
#1: balanced vs minimize differ several-fold), but even its best does not beat
OBDD at these scales. So on M4 there is no instance where SDD beats OBDD; the
d-DNNF advantage (O(n*2^{O(tw)}) vs OBDD's n^{O(tw)}) is asymptotic and needs a
better partitioner (d4, on Linux/x86) and/or larger n to show — genuinely
justifying d4 as a baseline, not ceremony.
"""
import gates, factor, compile_bdd, compile_sdd

def layered(k, W):
    data = {}
    for a in range(W): data[f"e0_{a}"] = ("S", "p", f"n1_{a}")
    for i in range(1, k):
        for a in range(W):
            for b in range(W): data[f"e{i}_{a}_{b}"] = (f"n{i}_{a}", "p", f"n{i+1}_{b}")
    return data, [("S", "p", "?v1")] + [(f"?v{i}", "p", f"?v{i+1}") for i in range(1, k)], [f"?v{k}"]

def structord(circ, root):
    return sorted(compile_bdd.leaf_order(circ, root))   # group tokens by layer via name-sort

def run(name, circ, root, P):
    so = structord(circ, root)
    ob_fa = compile_bdd.probability(circ, root, P)[1]
    ob_st = compile_bdd.probability(circ, root, P, order=so)[1]
    sd_b  = compile_sdd.compile(circ, root, P, vtree="balanced")[1]
    sd_st = compile_sdd.compile(circ, root, P, order=so, vtree="balanced")[1]
    sd_m  = compile_sdd.compile(circ, root, P, vtree="balanced", minimize=True)[1]
    print(f"{name:>11} {ob_fa:>9} {ob_st:>10} {sd_b:>8} {sd_st:>9} {sd_m:>8}   "
          f"{'OBDD(str) <= all SDD' if ob_st <= min(sd_b, sd_st, sd_m) else 'SDD wins somewhere'}")

if __name__ == "__main__":
    print(f"{'case':>11} {'OBDD(fa)':>9} {'OBDD(str)':>10} {'SDD-bal':>8} {'SDD(str)':>9} {'SDD-min':>8}")
    for W in [3, 4]:
        d, p, o = layered(4, W); c = gates.Circuit(); fac = factor.factored_bgp(c, p, d, set(o))
        run(f"layered W{W}", c.gates, next(iter(fac.values())), {t: 0.5 for t in d})
    for k in [6, 9, 12]:
        d, p, o = layered(k, 2); c = gates.Circuit(); fac = factor.factored_bgp(c, p, d, set(o))
        run(f"chain k{k}", c.gates, next(iter(fac.values())), {t: 0.5 for t in d})
