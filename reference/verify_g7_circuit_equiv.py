"""G7 circuit-equivalence regression: the emitted PROVENANCE CIRCUIT is byte-identical under Standard,
SPARQL-star, AND named-graph reification.

Previously this was argued via `RunExample`, which runs the NpcsRewriter (per-answer provenance STRING)
— so it only showed the STRING provenance matched, not the RDF circuit. This runs the actual
`CircuitRun` (the CircuitRewriter -> RDF circuit pipeline) on the paper example under EVERY token-aligned
scheme and canonical-diffs each against Standard:
  • byte-identity : sorted N-Triples lines are equal (gate IRIs are content-addressed by token IRIs,
    which are the same ex:u_i in all encodings, so the whole circuit coincides);
  • structural    : circuit_io.parse gives the same gate DAG and the same answer-gate set;
so the reification scheme is a pure front-end choice with ZERO effect on the compiled circuit.

Coverage: Standard vs {SPARQL_Star, NamedGraph}, over the four operator classes (AND/UNION/OPTIONAL/MINUS).
This closes the E1 gap where NAMED_GRAPH had only a hand-written 2-input byte-identity check: it now goes
through the same battery as RDF-star. (Wikidata's tokens are urn:wds:N, not ex:u_i, so it is NOT byte-
identical by construction — it is covered structurally + by WMC in wikidata/WIKIDATA_REIF_EQUIV.md. Property
paths stay Standard-only by design, so this battery is BGP/MINUS/OPTIONAL/UNION.)
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

# Token-aligned schemes checked byte-for-byte against Standard: (scheme name, its example data file).
ALT_SCHEMES = [
    ("SPARQL_Star", "example.star.ttls"),
    ("NamedGraph",  "example.namedgraph.nq"),
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
        std = run("Standard", "example.standard.ttl", qf)
        c0, a0, _ = circuit_io.parse(std)
        n = len([l for l in std.splitlines() if l.strip()])
        for scheme, dfile in ALT_SCHEMES:
            alt = run(scheme, dfile, qf)
            # (1) byte-identity of the canonicalized (sorted) N-Triples circuit.
            byte_ok = sorted(std.splitlines()) == sorted(alt.splitlines())
            # (2) structural: same gate DAG + same answer-gate set (via the shared parser).
            c1, a1, _ = circuit_io.parse(alt)
            struct_ok = c0 == c1 and a0 == a1
            ok = byte_ok and struct_ok
            allok &= ok
            print(f"[{label:9}] Standard == {scheme:11}: byte-identical={byte_ok} "
                  f"struct-identical={struct_ok}  ({n} triples, {len(a0)} answer gates)  {'OK' if ok else 'FAIL'}")
            if not ok:
                import difflib
                for d in list(difflib.unified_diff(sorted(std.splitlines()), sorted(alt.splitlines()), lineterm=""))[:8]:
                    print("     ", d)
    print("\nALL OK (circuit is reification-independent)" if allok else "\nFAILURES")
    sys.exit(0 if allok else 1)

if __name__ == "__main__":
    main()
