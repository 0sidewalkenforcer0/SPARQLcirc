"""Cross-engine byte-identity proof on OXIGRAPH (pyoxigraph, in-process) — the 2nd engine.

For each gallery query we (1) build the circuit in-memory via RDF4J (CircuitRun), (2) take the SAME
emitted SPARQL-1.1 CONSTRUCT plan and evaluate it on an Oxigraph Store loaded with the identical
reified data, then (3) diff the canonicalized triple sets. Identical => the content-addressed circuit
is engine-agnostic (Oxigraph computes byte-identical SHA256 node IRIs). Covers the read path
(BGP/MINUS/OPTIONAL/UNION); property paths need the writable iterative protocol (separate).
"""
import os, re, subprocess, sys
from pyoxigraph import Store, RdfFormat

HERE = os.path.dirname(os.path.abspath(__file__))
JAR  = os.path.join(HERE, "..", "..", "engine", "target", "npcs-rewrite.jar")
G    = os.path.join(HERE, "..", "..", "engine", "examples", "gallery")
DATA = f"{G}/gallery.ttl"
EMPTY = os.path.join(HERE, "_empty.ttl"); open(EMPTY, "w").close()

def rdf4j_circuit(query_file):
    r = subprocess.run(["java", "-cp", JAR, "npcs.circuit.CircuitRun", "Standard", DATA, query_file],
                       capture_output=True, text=True, check=True)
    return r.stdout

def plan(query_file):
    r = subprocess.run(["java", "-cp", JAR, "npcs.circuit.CircuitRun", "Standard", EMPTY, query_file],
                       capture_output=True, text=True)
    out = []
    for ch in re.split(r"# --- step \d+ ---", r.stderr)[1:]:
        ch = ch.split("# ---- ")[0].split("# circuit triples")[0].strip()
        if ch.startswith("PREFIX") or ch.startswith("CONSTRUCT"):
            out.append(ch)
    return out

def oxigraph_circuit(query_file):
    store = Store()
    store.load(path=DATA, format=RdfFormat.TURTLE)
    lines = []
    for c in plan(query_file):
        for t in store.query(c):                       # QueryTriples -> Triple
            lines.append(f"{t.subject} {t.predicate} {t.object} .")
    return "\n".join(lines)

def canon(nt):
    # a circuit is an RDF graph = a SET of triples; dedup (Oxigraph's CONSTRUCT stream can emit a
    # triple twice when two UNION/OPTIONAL branches derive the same gate, RDF4J collapses to a Model).
    return "\n".join(sorted({l for l in nt.splitlines() if l.strip().endswith(" .")}))

def main():
    from _gallery_shapes import E1_NONPATH as queries   # full E1 correctness set (byte-identity == E1 coverage)
    allok = True
    print(f"Oxigraph {__import__('pyoxigraph').__version__} vs in-memory RDF4J — byte-identical circuit?\n")
    for q in queries:
        qf = f"{G}/{q}.sparql"
        if not os.path.exists(qf):
            continue
        a = canon(rdf4j_circuit(qf)); b = canon(oxigraph_circuit(qf))
        ok = a == b; allok &= ok
        n = len(a.splitlines())
        print(f"  [{q:12}] {'OK  byte-identical' if ok else 'FAIL'}  ({n} circuit triples)")
        if not ok:
            da = set(a.splitlines()) - set(b.splitlines()); db = set(b.splitlines()) - set(a.splitlines())
            if da: print(f"      only RDF4J: {list(da)[:2]}")
            if db: print(f"      only Oxigraph: {list(db)[:2]}")
    print("\nALL byte-identical" if allok else "\nDIVERGENCES")
    sys.exit(0 if allok else 1)

if __name__ == "__main__":
    main()
