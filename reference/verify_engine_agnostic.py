"""Engine-agnosticism evidence for the property-path circuit.

The claim: an UNMODIFIED SPARQL 1.1 engine materializes the SAME circuit, so the circuit is
engine-agnostic. The emitted CONSTRUCTs use ONLY standard SPARQL 1.1 (BGP / UNION / BIND / IF /
CONCAT / STR / IRI / SHA256 / COUNT) and are deterministic (no ORDER BY / LIMIT / RAND / NOW /
UUID), so ANY compliant engine computes the identical content-addressed triple set. This checks:
  (1) determinism  -- two independent runs produce a byte-identical (canonicalized) circuit;
  (2) sparql-1.1   -- the emitted plan uses no engine-specific / nondeterministic construct.
With SPARQLCIRC_ENDPOINT set to a WRITABLE SPARQL 1.1 endpoint (e.g. GraphDB at
http://localhost:7200/repositories/<repo>), it ALSO builds the circuit there (CircuitRun's
endpoint mode) and diffs it against the in-memory RDF4J circuit -- the actual byte-identical
cross-engine check. GraphDB is not bundled; start it (see reference/bench_engine.py for a repo
config) and export SPARQLCIRC_ENDPOINT to run leg (3)."""
import subprocess, os, sys

HERE  = os.path.dirname(os.path.abspath(__file__))
JAR   = os.path.join(HERE, "..", "engine", "target", "npcs-rewrite.jar")
G     = os.path.join(HERE, "..", "engine", "examples", "gallery")
DATA  = f"{G}/pathcompound.ttl"
QUERY = f"{G}/pathalt.sparql"                       # a compound-closure path (exercises the full protocol)

def run(endpoint=None):
    cmd = ["java", "-cp", JAR, "npcs.circuit.CircuitRun", "Standard", DATA, QUERY]
    if endpoint: cmd.append(endpoint)
    r = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return r.stdout, r.stderr

def canon(nt):                                      # order-independent triple set
    return "\n".join(sorted(l for l in nt.splitlines() if l.strip().endswith(" .")))

# (1) determinism
a, plan = run(); b, _ = run()
det = canon(a) == canon(b)
print(f"[determinism ] two runs byte-identical? {'OK' if det else 'FAIL'}  "
      f"({len(canon(a).splitlines())} triples)")

# (2) SPARQL-1.1-only: scan the emitted CONSTRUCTs for engine-specific / nondeterministic tokens
BAD = ["RAND(", "NOW(", "UUID(", "STRUUID(", "SAMPLE(", "ORDER BY", "LIMIT ", "OFFSET ",
       "ontotext", "rdf4j", "apf:", "afn:", "<java:", "fn:"]
STD = ["SHA256(", "CONCAT(", "STR(", "IRI(", "BIND(", "IF("]
bad = [t for t in BAD if t in plan]
std = [t.rstrip("(") for t in STD if t in plan]
one_one = not bad and len(std) >= 5
print(f"[sparql-1.1  ] only standard SPARQL 1.1? {'OK' if one_one else 'FAIL'}  "
      f"(std used: {std}; non-standard: {bad or 'none'})")

# (3) optional cross-engine byte-identity
ep = os.environ.get("SPARQLCIRC_ENDPOINT")
xeng = True
if ep:
    try:
        c, _ = run(ep)
        xeng = canon(a) == canon(c)
        print(f"[cross-engine] endpoint circuit == in-memory circuit? {'OK' if xeng else 'FAIL'}  ({ep})")
    except subprocess.CalledProcessError as e:
        xeng = False
        tail = (e.stderr or "").strip().splitlines()
        print(f"[cross-engine] endpoint run FAILED: {tail[-1] if tail else e}")
else:
    print("[cross-engine] SKIPPED  (export SPARQLCIRC_ENDPOINT=<writable SPARQL 1.1 repo> for the "
          "byte-identical GraphDB/Fuseki check)")

print("\nALL OK" if (det and one_one and xeng) else "\nFAILURES")
sys.exit(0 if (det and one_one and xeng) else 1)
