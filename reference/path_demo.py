"""Property-path provenance demo: a cyclic graph where naive per-answer walk
enumeration is INFINITE, yet the shared level-indexed circuit is POLYNOMIAL and its
WMC equals the exact possible-world probability.

Construction (see gamma.eval_pexpr / gamma._closure): path provenance lives in the
absorptive semiring PosBool(X) (idempotent + absorptive circ.oplus/otimes); a transitive
path e+ is a level-indexed fixpoint reach^{k+1} = reach^k (+) reach^k∘edge, whose gates
reference only the previous round -> an acyclic DAG even on cyclic data. Recursive
sharing keeps it O(|V_s|.|E_s|) for a bound source.
"""
import gates, gamma, wmc

# A cyclic 3-node graph: A->B, B->C, A->C (shortcut), C->A (back edge).  Cycles
# A->C->A and A->B->C->A make naive walk enumeration infinite.
DATA = {"e1": ("A", "p", "B"), "e2": ("B", "p", "C"),
        "e3": ("A", "p", "C"), "e4": ("C", "p", "A")}
P = {"e1": 0.9, "e2": 0.8, "e3": 0.5, "e4": 0.7}
Q = ("path", "?x", ("plus", ("edge", "p")), "?y")          # ?x  p+  ?y   (all reachable pairs)

circ = gates.Circuit()
table = gamma.eval_q(circ, Q, DATA)
print("cyclic graph, query ?x p+ ?y  (naive walk enumeration: INFINITE)")
print(f"  circuit gates: {circ.stats()}  total={len(circ.gates)}   answers(pairs)={len(table)}")
print("\n  pair (x,y)     P(circuit-WMC)   P(possible-world)   match")
truth = wmc.pwe(Q, ["?x", "?y"], DATA, P)
allok = True
for k, g in sorted(gamma.project(circ, table, ["?x", "?y"]).items(), key=str):
    b = dict(k); cp = wmc.prob(circ, g, P); tp = truth.get(k, 0.0)
    ok = abs(cp - tp) < 1e-9; allok &= ok
    print(f"  {b['?x']}->{b['?y']}           {cp:.6f}        {tp:.6f}       {'OK' if ok else 'MISMATCH'}")

# --- size scaling: infinite/exponential paths, polynomial circuit ---
def ring(n):    # one big cycle n0->n1->...->n0 ; infinite walks
    return {f"e{i}": (f"n{i}", "p", f"n{(i + 1) % n}") for i in range(n)}
def clique(n):  # complete digraph ; super-exponentially many simple paths + cycles
    return {f"e{i}_{j}": (f"n{i}", "p", f"n{j}")
            for i in range(n) for j in range(n) if i != j}

print("\nsize scaling of the ?x p+ ?y circuit (naive #paths is infinite/super-exponential):")
print("  ring   |V|=|E|=n :   n   gates   gates/n^2")
for n in (8, 16, 32, 64):
    c = gates.Circuit(); gamma.eval_q(c, Q, ring(n))
    print(f"                    {n:4}  {len(c.gates):6}    {len(c.gates) / n ** 2:.3f}")
print("  clique |E|=n(n-1):   n   gates   (simple paths ~ e*(n-2)!)")
for n in (3, 4, 5, 6):
    c = gates.Circuit(); gamma.eval_q(c, Q, clique(n))
    print(f"                    {n:4}  {len(c.gates):6}")

print("\n" + ("ALL OK" if allok else "FAILURES"))
