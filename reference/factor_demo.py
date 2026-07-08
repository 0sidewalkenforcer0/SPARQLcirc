"""Factored (variable-elimination, multi-pass) vs flat circuit construction.

(A) correctness: flat == factored == PWE.
(B) size: on a layered-graph chain, flat grows as |data|^{#patterns}; factored
    stays polynomial of FIXED degree (treewidth+1), independent of chain length.
(C) functional equivalence flat==factored at larger W via random worlds.

Note: the factored *circuit* is polynomial, but compiling it to the ROBDD in
compile_bdd for exact WMC can still blow up on functions like layered
reachability (OBDD is strictly less succinct than d-DNNF). The bounded-treewidth
=> polynomial guarantee is for d-DNNF; use a d-DNNF compiler (e.g. d4) for
scalable exact WMC. Construction (this file) is decoupled from compilation.
"""
import sys, random
sys.setrecursionlimit(1_000_000)
import gates, gamma, factor, compile_bdd, wmc, verify_all
random.seed(1)

def sizes(c):
    return (sum(1 for op, _ in c.gates.values() if op == "times"),
            sum(1 for op, _ in c.gates.values() if op == "plus"))

def wmap(c, tab, P):
    return {k: compile_bdd.probability(c.gates, g, P)[0] for k, g in tab.items()}

def ev(circ, node, asn, memo):
    if node in memo: return memo[node]
    op, pl = circ[node]
    if op == "leaf": r = asn[pl]
    elif op == "const": r = bool(pl)
    elif op == "times": r = all(ev(circ, c, asn, memo) for c in pl)
    elif op == "plus": r = any(ev(circ, c, asn, memo) for c in pl)
    else: r = ev(circ, pl[0], asn, memo) and not ev(circ, pl[1], asn, memo)
    memo[node] = r; return r

def layered(k, W):
    data = {}
    for a in range(W):
        data[f"e0_{a}"] = ("S", "p", f"n1_{a}")
    for i in range(1, k):
        for a in range(W):
            for b in range(W):
                data[f"e{i}_{a}_{b}"] = (f"n{i}_{a}", "p", f"n{i+1}_{b}")
    return data, [("S", "p", "?v1")] + [(f"?v{i}", "p", f"?v{i+1}") for i in range(1, k)], [f"?v{k}"]


print("=== (A) drug 3-hop: flat == factored == PWE ===")
s = verify_all.REG["drug"]; pats = s["q"][1]; data = s["base"]; P = s["P"]; out = ["?z"]
cf = gates.Circuit(); flat = gamma.project(cf, gamma.eval_bgp(cf, pats, data), out)
cx = gates.Circuit(); fac = factor.factored_bgp(cx, pats, data, set(out))
wf, wx = wmap(cf, flat, P), wmap(cx, fac, P)
truth = {frozenset((v.lstrip("?"), vv) for v, vv in k): p for k, p in wmc.pwe(s["q"], out, data, P).items()}
for k in sorted(wf, key=str):
    kk = frozenset((v.lstrip("?"), vv) for v, vv in k)
    good = abs(wf[k]-wx[k]) < 1e-9 and abs(wf[k]-truth.get(kk,0)) < 1e-9
    print(f"   {str(dict(k)):24} flat={wf[k]:.6f} factored={wx[k]:.6f} PWE={truth.get(kk,0):.6f} {'OK' if good else 'FAIL'}")
print(f"   gates(times,plus): flat={sizes(cf)}  factored={sizes(cx)}")

print("\n=== (B) layered chain (k=4): circuit size, flat=W^4 vs factored=3W^2 ===")
k = 4
print(f"{'W':>3} {'#edges':>7} {'flat(t,p)':>14} {'factored(t,p)':>16} {'flat/fac ⊗':>11}")
for W in [2, 3, 4, 6, 8, 10, 14]:
    data, pats, out = layered(k, W)
    cf = gates.Circuit(); gamma.project(cf, gamma.eval_bgp(cf, pats, data), out)
    cx = gates.Circuit(); factor.factored_bgp(cx, pats, data, set(out))
    tf, tx = sizes(cf)[0], sizes(cx)[0]
    print(f"{W:>3} {len(data):>7} {str(sizes(cf)):>14} {str(sizes(cx)):>16} {tf/max(tx,1):>10.1f}x")

print("\n=== (C) flat == factored as Boolean functions (2000 random worlds) ===")
for W in [3, 4, 6, 8]:
    data, pats, out = layered(k, W)
    cf = gates.Circuit(); flat = gamma.project(cf, gamma.eval_bgp(cf, pats, data), out)
    cx = gates.Circuit(); fac = factor.factored_bgp(cx, pats, data, set(out))
    toks = list(data); keys = set(flat) & set(fac); ok = True
    for _ in range(2000):
        asn = {t: random.random() < 0.5 for t in toks}
        mf, mx = {}, {}
        if any(ev(cf.gates, flat[kk], asn, mf) != ev(cx.gates, fac[kk], asn, mx) for kk in keys):
            ok = False; break
    print(f"   W={W}: answers={len(keys)}  flat⊗={sizes(cf)[0]} factored⊗={sizes(cx)[0]}  agree -> {'OK' if ok else 'FAIL'}")
