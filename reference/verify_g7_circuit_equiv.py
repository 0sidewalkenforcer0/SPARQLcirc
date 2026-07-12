"""G7 circuit-equivalence regression: the emitted PROVENANCE CIRCUIT is byte-identical under Standard
and SPARQL-star reification.

Previously this was argued via `RunExample`, which runs the NpcsRewriter (per-answer provenance STRING)
— so it only showed the STRING provenance matched, not the RDF circuit. This runs the actual
`CircuitRun` (the CircuitRewriter -> RDF circuit pipeline) on the paper example under BOTH schemes and
canonical-diffs the emitted circuits:
  • byte-identity : sorted N-Triples lines are equal (gate IRIs are content-addressed by token IRIs,
    which are the same ex:u_i in both encodings, so the whole circuit coincides);
  • structural    : circuit_io.parse gives the same gate DAG and the same answer-gate set;
so the reification scheme is a pure front-end choice with ZERO effect on the compiled circuit.
"""
import subprocess, os, sys
import circuit_io

HERE = os.path.dirname(os.path.abspath(__file__))
JAR  = os.path.join(HERE, "..", "engine", "target", "npcs-rewrite.jar")
DATA = os.path.join(HERE, "..", "engine", "examples", "data")
QDIR = os.path.join(HERE, "..", "engine", "examples", "queries")

# (label, query file) — one per operator class; all non-path (paths are Standard-only by design).
QUERIES = [
    ("and",      "monotonic/and.sparql"),
    ("union",    "monotonic/union.sparql"),
    ("optional", "nonmonotonic/optional.sparql"),
    ("minus",    "nonmonotonic/minus.sparql"),
]

def run(scheme, data_file, query_file):
    return subprocess.run(["java", "-cp", JAR, "npcs.circuit.CircuitRun", scheme,
                           os.path.join(DATA, data_file), os.path.join(QDIR, query_file)],
                          capture_output=True, text=True, check=True).stdout

def main():
    if not os.path.exists(JAR):
        print("jar not built:", JAR); sys.exit(2)
    allok = True
    for label, qf in QUERIES:
        std  = run("Standard",    "example.standard.ttl", qf)
        star = run("SPARQL_Star", "example.star.ttls",    qf)
        # (1) byte-identity of the canonicalized (sorted) N-Triples circuit.
        byte_ok = sorted(std.splitlines()) == sorted(star.splitlines())
        # (2) structural: same gate DAG + same answer-gate set (via the shared parser).
        c1, a1, _ = circuit_io.parse(std)
        c2, a2, _ = circuit_io.parse(star)
        struct_ok = c1 == c2 and a1 == a2
        ok = byte_ok and struct_ok
        allok &= ok
        n = len([l for l in std.splitlines() if l.strip()])
        print(f"[{label:9}] CircuitRun Standard == SPARQL_Star : byte-identical={byte_ok} "
              f"struct-identical={struct_ok}  ({n} triples, {len(a1)} answer gates)  {'OK' if ok else 'FAIL'}")
        if not ok:
            import difflib
            for d in list(difflib.unified_diff(sorted(std.splitlines()), sorted(star.splitlines()), lineterm=""))[:8]:
                print("     ", d)
    print("\nALL OK (circuit is reification-independent)" if allok else "\nFAILURES")
    sys.exit(0 if allok else 1)

if __name__ == "__main__":
    main()
