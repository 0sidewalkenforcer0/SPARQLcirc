"""Compilation for exact probabilistic query evaluation: OBDD (bundled) vs SDD
(PySDD, a real structured d-DNNF compiler, arm64-native).

Findings:
 (1) both agree with each other and with PWE on the engine-materialized circuits;
 (2) exact WMC tractability is governed by the *treewidth* of the lineage, NOT by
     OBDD-vs-d-DNNF: on bounded-treewidth lineage both are polynomial; on
     high-treewidth lineage both blow up (exact WMC is #P-hard there -- no
     compiler, d4 included, is polynomial).

(d4 itself is x86_64-only on this Mac -- its bundled PATOH partitioner has no
arm64 build -- so SDD stands in as the real d-DNNF-family compiler on M4.)
"""
import rdflib
import gates, factor, compile_bdd, compile_sdd, wmc, verify_all

C = rdflib.Namespace("urn:circuit:"); D = "urn:d:"
def short(x): return str(x).replace(D, "")

def to_circ(nt):
    typ, feeds, tin, minus, ans = verify_all.load(nt)
    circ = {}
    for t, ls in tin.items():
        for l in ls: circ[l] = ("leaf", short(l))
    for n, tp in typ.items():
        if tp == C.Times:  circ[n] = ("times", tuple(sorted(tin.get(n, ()))))
        elif tp == C.Plus: circ[n] = ("plus", tuple(sorted(feeds.get(n, ()))))
        elif tp == C.Minus: m = minus[n]; circ[n] = ("minus", (m["m"], m["s"]))
    ref = set()
    for op, pl in circ.values():
        if op in ("times", "plus"): ref |= set(pl)
        elif op == "minus": ref |= {pl[0], pl[1]}
    for r in ref: circ.setdefault(r, ("plus", ()))
    return circ, ans

print("=== (1) SDD == OBDD == PWE on engine-materialized circuits ===")
for name in ["drug", "selfjoin", "minus", "optional"]:
    s = verify_all.REG[name]; circ, ans = to_circ(s["nt"]); P = s["P"]
    truth = {frozenset((v.lstrip("?"), vv) for v, vv in k): p
             for k, p in wmc.pwe(s["q"], s["sel"], s["base"], P).items()}
    for root, key in ans.items():
        ps, ss = compile_sdd.compile(circ, root, P)
        pb, sb = compile_bdd.probability(circ, root, P)
        k = verify_all.parse_key(key); tp = truth.get(k, 0.0)
        ok = abs(ps - pb) < 1e-9 and abs(ps - tp) < 1e-9
        print(f"  [{name:8}] {str(dict(k)):24} SDD={ps:.6f}(sz{ss}) OBDD={pb:.6f}(sz{sb}) PWE={tp:.6f} {'OK' if ok else 'FAIL'}")

print("\n=== (2) bounded-treewidth (shared hub): both compilers polynomial ===")
def hub(N):
    c = gates.Circuit(); t0 = c.leaf("t0")
    return c.gates, c.plus([c.times([t0, c.leaf(f"t{i}")]) for i in range(1, N)])
print(f"{'N':>4} {'OBDD sz':>8} {'SDD sz':>8}")
for N in [10, 20, 40, 60]:
    circ, root = hub(N); P = {f"t{i}": 0.5 for i in range(N)}
    _, sb = compile_bdd.probability(circ, root, P); _, ss = compile_sdd.compile(circ, root, P)
    print(f"{N:>4} {sb:>8} {ss:>8}   linear -> polynomial")

print("\n=== (3) high-treewidth (layered complete-bipartite, tw~W): both blow up ===")
def layered(k, W):
    data = {}
    for a in range(W): data[f"e0_{a}"] = ("S", "p", f"n1_{a}")
    for i in range(1, k):
        for a in range(W):
            for b in range(W): data[f"e{i}_{a}_{b}"] = (f"n{i}_{a}", "p", f"n{i+1}_{b}")
    return data, [("S", "p", "?v1")] + [(f"?v{i}", "p", f"?v{i+1}") for i in range(1, k)], [f"?v{k}"]
print(f"{'W':>3} {'factored ⊗':>10} {'OBDD sz':>8} {'SDD sz':>8}  (exact WMC #P-hard here)")
for W in [2, 3, 4, 5]:
    data, pats, out = layered(4, W)
    c = gates.Circuit(); fac = factor.factored_bgp(c, pats, data, set(out))
    P = {t: 0.5 for t in data}; root = next(iter(fac.values()))
    nt = sum(1 for o, _ in c.gates.values() if o == "times")
    _, sb = compile_bdd.probability(c.gates, root, P); _, ss = compile_sdd.compile(c.gates, root, P)
    print(f"{W:>3} {nt:>10} {sb:>8} {ss:>8}")
print("\n(circuit stays polynomial via factoring; the FUNCTION is hard to compile "
      "because treewidth is high -- fundamental, independent of the compiler.)")
