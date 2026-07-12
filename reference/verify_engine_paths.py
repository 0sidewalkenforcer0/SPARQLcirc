"""Verify the ENGINE-materialized property-path circuit.

Run CircuitRun (unmodified in-memory RDF4J, client-driven iterative protocol) for a range
of property-path shapes on CYCLIC graphs, load the emitted RDF circuit, compile it to a BDD
and weighted-model-count each answer, and check it equals the exact possible-world
probability (independent oracle in wmc.py). The graphs are cyclic, so naive per-answer walk
enumeration is infinite; the emitted circuit is finite and its compile+WMC terminates (a
cycle in the gate graph would make compile_bdd recurse forever -- so this also checks the
level-indexing kept the DAG acyclic). Covers +, *, all endpoint modes, and closures over
compound sub-paths (sequence /, alternative |, inverse ^)."""
import subprocess, os, sys
import compile_bdd, wmc, circuit_io

HERE = os.path.dirname(os.path.abspath(__file__))
JAR  = os.path.join(HERE, "..", "engine", "target", "npcs-rewrite.jar")
G    = os.path.join(HERE, "..", "engine", "examples", "gallery")
EX   = "http://example.org/paper#"

# mirror engine/examples/gallery/pathcyc.ttl and pathcompound.ttl
CYC = {EX + "e1": (EX + "A", "p", EX + "B"), EX + "e2": (EX + "B", "p", EX + "C"),
       EX + "e3": (EX + "A", "p", EX + "C"), EX + "e4": (EX + "C", "p", EX + "A")}
CYC_P = {EX + "e1": .9, EX + "e2": .8, EX + "e3": .5, EX + "e4": .7}
CMP = {EX + "e1": (EX + "A", "p", EX + "B"), EX + "e2": (EX + "B", "q", EX + "C"),
       EX + "e3": (EX + "C", "p", EX + "D"), EX + "e4": (EX + "D", "q", EX + "A")}
CMP_P = {EX + "e1": .9, EX + "e2": .8, EX + "e3": .7, EX + "e4": .6}

def engine(query_file, data_file, P):
    nt = subprocess.run(["java", "-cp", JAR, "npcs.circuit.CircuitRun", "Standard",
                         f"{G}/{data_file}", f"{G}/{query_file}"],
                        capture_output=True, text=True, check=True).stdout
    return circuit_io.answer_probs(nt, P, compile_bdd.probability)     # term-aware answer keys via c:binding

def oracle(expr, subj, obj, sel, data, P):
    q = ("path", subj, expr, obj)
    out = {}
    for fs, v in wmc.pwe(q, sel, data, P).items():
        if v > 1e-12:
            d = dict(fs)                                               # path endpoints are IRIs -> canon_iri
            out[circuit_io.answer_key({w.lstrip("?"): circuit_io.canon_iri(d[w]) for w in sel})] = round(v, 10)
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
    ("pathopt.sparql",       "pathcyc.ttl",      CYC, CYC_P, ("opt", E("p")),                      "?x",     "?y", ["?x", "?y"]),  # zero-or-one :p?
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
