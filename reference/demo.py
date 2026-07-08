"""Demo: build the shared provenance circuit for the paper's running example and
verify probabilistic evaluation against possible-world enumeration."""
import gates, gamma, wmc

# ---- drug-interaction KG (Fig. 1): token -> (subject, 'iw', object), prob ----
EDGES = {
    "p1": ("Aspirin",     "iw", "Warfarin",    0.92),
    "p2": ("Warfarin",    "iw", "Metformin",   0.87),
    "p3": ("Metformin",   "iw", "Omeprazole",  0.85),
    "p4": ("Aspirin",     "iw", "Ibuprofen",   0.78),
    "p5": ("Ibuprofen",   "iw", "Metformin",   0.71),
    "p6": ("Warfarin",    "iw", "Lisinopril",  0.65),
    "p7": ("Lisinopril",  "iw", "Clopidogrel", 0.60),
    "p8": ("Clopidogrel", "iw", "Aspirin",     0.55),   # cycle edge (unused by 3-hop)
}
DATA = {k: (s, p, o) for k, (s, p, o, _) in EDGES.items()}
P    = {k: pr for k, (_, _, _, pr) in EDGES.items()}

# 3-step interaction query:  Aspirin iw ?x . ?x iw ?y . ?y iw ?z   SELECT ?z
Q   = ("bgp", [("Aspirin", "iw", "?x"), ("?x", "iw", "?y"), ("?y", "iw", "?z")])
SEL = ["?z"]

circ = gates.Circuit()
table = gamma.eval_q(circ, Q, DATA)
roots = gamma.project(circ, table, SEL)

print("=== answers, provenance-root probability, vs PWE ===")
truth = wmc.pwe(Q, SEL, DATA, P)
for k, g in sorted(roots.items(), key=lambda kv: str(kv[0])):
    ans = dict(k).get("?z", "?")
    print(f"  ?z={ans:12} P(circuit)={wmc.prob(circ, g, P):.5f}   P(PWE)={truth.get(k,0.0):.5f}")

print("\n=== circuit sharing ===")
print("  gate counts:", circ.stats())
fo = circ.fanout()
shared = [(circ.gates[g], n) for g, n in fo.items() if n >= 2]
print(f"  gates with fan-out >= 2 (shared): {len(shared)}")
for (node, n) in sorted(shared, key=lambda x: -x[1])[:6]:
    label = node[1] if node[0] == "leaf" else node[0]
    print(f"    {node[0]:6} {str(label):14} referenced by {n} parents")

ok, total, fails = wmc.check(circ, table, SEL, Q, DATA, P)
print(f"\n=== correctness: {ok}/{total} answers match PWE ===")
for f in fails:
    print("   MISMATCH", f)
