"""Verify the ENGINE-materialized property-path circuit.

Run CircuitRun (unmodified in-memory RDF4J, client-driven iterative protocol) for
:A :p+ ?y and :A :p* ?y on a CYCLIC graph, load the emitted RDF circuit, compile it
to a BDD and weighted-model-count each answer, and check it equals the exact
possible-world probability (independent oracle in wmc.py). The graph is cyclic, so
naive per-answer walk enumeration is infinite; the emitted circuit is finite and its
compile+WMC terminates (a cycle in the gate graph would make compile_bdd recurse
forever -- so this also checks the level-indexing kept the DAG acyclic)."""
import subprocess, os, sys, rdflib
import compile_bdd, wmc

HERE = os.path.dirname(os.path.abspath(__file__))
JAR  = os.path.join(HERE, "..", "engine", "target", "npcs-rewrite.jar")
G    = os.path.join(HERE, "..", "engine", "examples", "gallery")
EX   = "http://example.org/paper#"
RDFT = rdflib.RDF.type
C    = rdflib.Namespace("urn:circuit:")

# must mirror engine/examples/gallery/pathcyc.ttl
DATA = {EX + "e1": (EX + "A", "p", EX + "B"), EX + "e2": (EX + "B", "p", EX + "C"),
        EX + "e3": (EX + "A", "p", EX + "C"), EX + "e4": (EX + "C", "p", EX + "A")}
P    = {EX + "e1": .9, EX + "e2": .8, EX + "e3": .5, EX + "e4": .7}

def load(nt_text):
    """Parse the engine .nt into a gates.Circuit-style dict (Times may have gate children)."""
    g = rdflib.Graph().parse(data=nt_text, format="nt")
    kind, feeders, cin, ans = {}, {}, {}, {}
    for s, p, o in g:
        if p == RDFT and o == C.Plus:  kind[str(s)] = "plus"
        elif p == RDFT and o == C.Times: kind[str(s)] = "times"
        elif p == C.feeds: feeders.setdefault(str(o), []).append(str(s))   # s feeds o
        elif p == C["in"]: cin.setdefault(str(s), []).append(str(o))
        elif p == C.answer: ans[str(s)] = str(o)
    circ = {}
    for n, k in kind.items():
        if k == "times":
            kids = []
            for c in cin.get(n, []):
                kids.append(c)
                if c not in kind: circ[c] = ("leaf", c)                    # token leaf
            circ[n] = ("times", tuple(kids))
        else:
            circ[n] = ("plus", tuple(feeders.get(n, [])))
    return circ, ans

def engine(query_file):
    nt = subprocess.run(["java", "-cp", JAR, "npcs.circuit.CircuitRun", "Standard",
                         f"{G}/pathcyc.ttl", f"{G}/{query_file}"],
                        capture_output=True, text=True, check=True).stdout
    circ, ans = load(nt)
    return {key: round(compile_bdd.probability(circ, gate, P)[0], 10) for gate, key in ans.items()}

def oracle(op):
    q = ("path", EX + "A", (op, ("edge", "p")), "?y")
    truth = wmc.pwe(q, ["?y"], DATA, P)
    return {"A|y=" + next(iter(k))[1]: round(v, 10) for k, v in truth.items() if v > 1e-12}

if __name__ == "__main__":
    allok = True
    for qf, op in [("pathplus.sparql", "plus"), ("pathstar.sparql", "star")]:
        eng, tru = engine(qf), oracle(op)
        keys = sorted(set(eng) | set(tru))
        ok = all(abs(eng.get(k, 0.0) - tru.get(k, 0.0)) < 1e-9 for k in keys)
        allok &= ok
        print(f"[{qf:16}] answers={len(tru)}  engine-WMC == PWE? {'OK' if ok else 'MISMATCH'}")
        for k in keys:
            f = "" if abs(eng.get(k, 0.) - tru.get(k, 0.)) < 1e-9 else "   <-- MISMATCH"
            print(f"    {k:42} engine={eng.get(k, 0.):.6f}  pwe={tru.get(k, 0.):.6f}{f}")
    print("\nALL OK" if allok else "\nFAILURES")
    sys.exit(0 if allok else 1)
