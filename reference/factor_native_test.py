"""Verify the ENGINE-NATIVE factored construction (factor_native, run as SPARQL
INSERT passes on rdflib):
  - it runs as N SPARQL passes on an unmodified engine;
  - the circuit it materializes is polynomial (matches client-side factor.py);
  - it is correct: equal to factor.py as a Boolean function on random worlds, and
    WMC == PWE at small W (where exact WMC is feasible -- the layered instance is
    high-treewidth, so BDD-WMC is only run at W=2).
"""
import sys, random
from collections import defaultdict
sys.setrecursionlimit(1_000_000)
import rdflib
from rdflib import RDF, URIRef
import gates, factor, compile_bdd, wmc, factor_native
random.seed(0)

CC = "urn:circuit:"; D = "urn:d:"
RS, RP, RO = (f"http://www.w3.org/1999/02/22-rdf-syntax-ns#{x}" for x in ("subject", "predicate", "object"))
def short(x): return str(x).replace(D, "")

def layered(k, W):
    g = rdflib.Graph(); base = {}
    def add(tok, s, p, o):
        g.add((URIRef(D+tok), URIRef(RS), URIRef(D+s)))
        g.add((URIRef(D+tok), URIRef(RP), URIRef(D+p)))
        g.add((URIRef(D+tok), URIRef(RO), URIRef(D+o)))
        base[tok] = (s, p, o)
    for a in range(W): add(f"e0_{a}", "S", "p", f"n1_{a}")
    for i in range(1, k):
        for a in range(W):
            for b in range(W): add(f"e{i}_{a}_{b}", f"n{i}_{a}", "p", f"n{i+1}_{b}")
    npat = [(D+"S", D+"p", "?v1")] + [("?v"+str(i), D+"p", "?v"+str(i+1)) for i in range(1, k)]
    dslpat = [("S", "p", "?v1")] + [("?v"+str(i), "p", "?v"+str(i+1)) for i in range(1, k)]
    return g, npat, [f"v{k}"], base, dslpat, ["?v"+str(k)]

def extract(g):
    Cp, Ct, Cin, Cfe, Can = (URIRef(CC+x) for x in ("Plus", "Times", "in", "feeds", "answer"))
    typ = {}; pc = defaultdict(set); tc = defaultdict(set); ans = {}
    for s, p, o in g:
        if p == RDF.type and o in (Cp, Ct): typ[s] = o
        elif p == Cfe: pc[o].add(s)
        elif p == Cin: tc[s].add(o)
        elif p == Can: ans[str(o)] = str(s)   # answer-key -> root
    circ = {}; ref = set()
    for n, t in typ.items():
        ch = tuple(sorted(str(x) for x in (pc[n] if t == Cp else tc[n])))
        circ[str(n)] = (("plus" if t == Cp else "times"), ch); ref |= set(ch)
    for r in ref:
        if r not in circ: circ[r] = ("leaf", short(r))
    return circ, ans

def ev(circ, n, asn, memo):
    if n in memo: return memo[n]
    op, pl = circ[n]
    if op == "leaf": r = asn[pl]
    elif op == "times": r = all(ev(circ, c, asn, memo) for c in pl)
    else: r = any(ev(circ, c, asn, memo) for c in pl)   # plus (no minus in BGP factoring)
    memo[n] = r; return r

def pkey(k): return frozenset((p.split("=")[0], short(p.split("=")[1])) for p in k.split("|")[1:])

print(f"{'k,W':>6} {'passes':>6} {'native ⊗,⊕':>12} {'factor.py ⊗,⊕':>14} {'rand-world ≡ factor.py':>22} {'WMC==PWE(W=2)':>13}")
for (k, W) in [(3, 2), (3, 3), (4, 2), (4, 4), (5, 3)]:
    g, npat, nout, base, dslpat, dslout = layered(k, W)
    passes, _ = factor_native.build(g, npat, nout)
    circ, roots = extract(g)
    nt = sum(1 for op, _ in circ.values() if op == "times")
    npl = sum(1 for op, _ in circ.values() if op == "plus")
    # client-side factor.py
    cc = gates.Circuit(); fac = factor.factored_bgp(cc, dslpat, base, set(dslout))
    fac = {frozenset((v.lstrip("?"), vv) for v, vv in kk): gg for kk, gg in fac.items()}
    fnt = sum(1 for op, _ in cc.gates.values() if op == "times"); fnp = sum(1 for op, _ in cc.gates.values() if op == "plus")
    # random-world functional equivalence: native circuit vs factor.py circuit
    toks = list(base); nkeys = {pkey(k2): r for k2, r in roots.items()}
    common = set(nkeys) & set(fac); okeq = True
    for _ in range(500):
        asn = {t: random.random() < 0.5 for t in toks}
        mn, mf = {}, {}
        if any(ev(circ, nkeys[kk], asn, mn) != ev(cc.gates, fac[kk], asn, mf) for kk in common) or set(nkeys) != set(fac):
            okeq = False; break
    # WMC vs PWE only at W==2 (feasible)
    wp = "-"
    if W == 2:
        P = {t: 0.5 for t in base}
        truth = {frozenset((v.lstrip("?"), vv) for v, vv in kk): p for kk, p in wmc.pwe(("bgp", dslpat), dslout, base, P).items()}
        wn = {kk: compile_bdd.probability(circ, r, P)[0] for kk, r in nkeys.items()}
        wp = str(all(abs(wn.get(kk, 0) - truth.get(kk, 0)) < 1e-9 for kk in set(wn) | {x for x, v in truth.items() if v > 1e-12}))
    print(f"{str((k,W)):>6} {passes:>6} {str((nt,npl)):>12} {str((fnt,fnp)):>14} {str(okeq):>22} {wp:>13}")
