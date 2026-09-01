"""Fail-fast regression: sequence modifiers (LIMIT/OFFSET/ORDER BY) are REJECTED, not silently ignored,
on BOTH the plain-BGP path (plan()) AND the property-path path (pathQuery()).

SPARQL_circ computes the FULL set of answer probabilities; a modifier that trims/reorders the solution
sequence has no circuit meaning, so it must fail-fast. plan() always called rejectSequenceModifiers();
pathQuery() did not, so `SELECT ?y WHERE {:A :p+ ?y} LIMIT 1` used to build the full circuit silently
(the Slice wraps the Projection while the path stays the Projection's child). Now both reject.
"""
import subprocess, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
JAR  = os.path.join(HERE, "..", "engine", "target", "npcs-rewrite.jar")
G    = os.path.join(HERE, "..", "engine", "examples", "gallery")
DATA = os.path.join(G, "pathcyc.ttl")
EX   = "http://example.org/paper#"

CASES = [  # (label, query) — each must be rejected
    ("bgp + LIMIT",     f"PREFIX : <{EX}>\nSELECT ?y WHERE {{ :A :p ?y }} LIMIT 1\n"),
    ("bgp + OFFSET",    f"PREFIX : <{EX}>\nSELECT ?y WHERE {{ :A :p ?y }} OFFSET 2\n"),
    ("path :p+ + LIMIT",  f"PREFIX : <{EX}>\nSELECT ?y WHERE {{ :A :p+ ?y }} LIMIT 1\n"),
    ("path :p* + ORDER",  f"PREFIX : <{EX}>\nSELECT ?y WHERE {{ :A :p* ?y }} ORDER BY ?y\n"),
]

def main():
    if not os.path.exists(JAR):
        print("jar not built:", JAR); sys.exit(2)
    import tempfile
    allok = True
    for label, q in CASES:
        qf = tempfile.NamedTemporaryFile("w", suffix=".rq", delete=False); qf.write(q); qf.close()
        r = subprocess.run(["java", "-cp", JAR, "npcs.circuit.CircuitRun", "Standard", DATA, qf.name],
                           capture_output=True, text=True)
        rejected = r.returncode != 0 and ("modifier" in r.stderr.lower() or "LIMIT/OFFSET" in r.stderr)
        allok &= rejected
        print(f"[{label:18}] rejected={rejected} (exit {r.returncode})  {'OK' if rejected else 'FAIL — silently accepted!'}")
    print("\nALL OK" if allok else "\nFAILURES")
    sys.exit(0 if allok else 1)

if __name__ == "__main__":
    main()
