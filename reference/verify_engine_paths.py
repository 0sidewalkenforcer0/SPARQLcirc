"""Verify the ENGINE-materialized property-path circuit.

Run CircuitRun (unmodified in-memory RDF4J, client-driven iterative protocol) for a range
of property-path shapes on CYCLIC graphs, load the emitted RDF circuit, compile it to a BDD
and weighted-model-count each answer, and check it equals the exact possible-world
probability (independent oracle in wmc.py). The graphs are cyclic, so naive per-answer walk
enumeration is infinite; the emitted circuit is finite and its compile+WMC terminates (a
cycle in the gate graph would make compile_bdd recurse forever -- so this also checks the
level-indexing kept the DAG acyclic). Covers +, *, all endpoint modes, and closures over
compound sub-paths (sequence /, alternative |, inverse ^)."""
import subprocess, os, sys, rdflib
import compile_bdd, wmc

HERE = os.path.dirname(os.path.abspath(__file__))
JAR  = os.path.join(HERE, "..", "engine", "target", "npcs-rewrite.jar")
G    = os.path.join(HERE, "..", "engine", "examples", "gallery")
EX   = "http://example.org/paper#"
RDFT = rdflib.RDF.type
C    = rdflib.Namespace("urn:circuit:")

# mirror engine/examples/gallery/pathcyc.ttl and pathcompound.ttl
CYC = {EX + "e1": (EX + "A", "p", EX + "B"), EX + "e2": (EX + "B", "p", EX + "C"),
       EX + "e3": (EX + "A", "p", EX + "C"), EX + "e4": (EX + "C", "p", EX + "A")}
CYC_P = {EX + "e1": .9, EX + "e2": .8, EX + "e3": .5, EX + "e4": .7}
CMP = {EX + "e1": (EX + "A", "p", EX + "B"), EX + "e2": (EX + "B", "q", EX + "C"),
       EX + "e3": (EX + "C", "p", EX + "D"), EX + "e4": (EX + "D", "q", EX + "A")}
CMP_P = {EX + "e1": .9, EX + "e2": .8, EX + "e3": .7, EX + "e4": .6}

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

def engine(query_file, data_file, P):
    nt = subprocess.run(["java", "-cp", JAR, "npcs.circuit.CircuitRun", "Standard",
                         f"{G}/{data_file}", f"{G}/{query_file}"],
                        capture_output=True, text=True, check=True).stdout
    circ, ans = load(nt)
    return {key: round(compile_bdd.probability(circ, gate, P)[0], 10) for gate, key in ans.items()}

def oracle(expr, subj, obj, sel, data, P):
    q = ("path", subj, expr, obj)
    out = {}
    for fs, v in wmc.pwe(q, sel, data, P).items():
        if v > 1e-12:
            d = dict(fs)
            out["A" + "".join("|" + w.lstrip("?") + "=" + d[w] for w in sel)] = round(v, 10)
    return out

E = lambda p: ("edge", p)
# (query file, data file, DATA, P, path expr, subject term, object term, SELECT var order)
TESTS = [
    ("pathplus.sparql",      "pathcyc.ttl",      CYC, CYC_P, ("plus", E("p")),                     EX + "A", "?y", ["?y"]),
    ("pathstar.sparql",      "pathcyc.ttl",      CYC, CYC_P, ("star", E("p")),                     EX + "A", "?y", ["?y"]),
    ("pathplus_free.sparql", "pathcyc.ttl",      CYC, CYC_P, ("plus", E("p")),                     "?x",     "?y", ["?x", "?y"]),
    ("pathseq.sparql",       "pathcompound.ttl", CMP, CMP_P, ("plus", ("seq", E("p"), E("q"))),    EX + "A", "?y", ["?y"]),
    ("pathalt.sparql",       "pathcompound.ttl", CMP, CMP_P, ("plus", ("alt", E("p"), E("q"))),    "?x",     "?y", ["?x", "?y"]),
    ("pathinv.sparql",       "pathcompound.ttl", CMP, CMP_P, ("plus", ("inv", E("p"))),            "?x",     "?y", ["?x", "?y"]),
]

if __name__ == "__main__":
    allok = True
    for qf, df, data, P, expr, subj, obj, sel in TESTS:
        eng, tru = engine(qf, df, P), oracle(expr, subj, obj, sel, data, P)
        keys = sorted(set(eng) | set(tru))
        ok = all(abs(eng.get(k, 0.0) - tru.get(k, 0.0)) < 1e-9 for k in keys)
        allok &= ok
        print(f"[{qf:20}] answers={len(tru):2}  engine-WMC == PWE? {'OK' if ok else 'MISMATCH'}")
        for k in keys:
            f = "" if abs(eng.get(k, 0.) - tru.get(k, 0.)) < 1e-9 else "   <-- MISMATCH"
            print(f"    {k:52} engine={eng.get(k, 0.):.6f}  pwe={tru.get(k, 0.):.6f}{f}")
    print("\nALL OK" if allok else "\nFAILURES")
    sys.exit(0 if allok else 1)
