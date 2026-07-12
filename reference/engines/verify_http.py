"""Generic cross-engine byte-identity verifier over HTTP — works against ANY SPARQL 1.1 endpoint
(QLever / MillenniumDB / Oxigraph-server / GraphDB / Fuseki ...) that has the gallery reified data
pre-loaded. Same idea as verify_oxigraph.py but the CONSTRUCTs are POSTed to a live endpoint.

  SPARQLCIRC_ENDPOINT=http://localhost:PORT/...  python3 verify_http.py [engine-name]

Steps: (0) a SHA256 smoke test — the one make-or-break function for the content-addressing (QLever
and MillenniumDB are the unknowns); (1) per gallery query, POST the SAME emitted CONSTRUCT plan and
diff the canonical (deduped) circuit against the in-memory RDF4J circuit. Read path only
(BGP/MINUS/OPTIONAL/UNION) — property paths need the writable protocol, run on writable engines.
"""
import os, re, subprocess, sys
import urllib.request as U

HERE  = os.path.dirname(os.path.abspath(__file__))
JAR   = os.path.join(HERE, "..", "..", "engine", "target", "npcs-rewrite.jar")
G     = os.path.join(HERE, "..", "..", "engine", "examples", "gallery")
DATA  = f"{G}/gallery.ttl"
EMPTY = os.path.join(HERE, "_empty.ttl"); open(EMPTY, "w").close()
EP    = os.environ.get("SPARQLCIRC_ENDPOINT")
NAME  = sys.argv[1] if len(sys.argv) > 1 else (EP or "endpoint")
SHA256_OF_X = "2d711642b726b04401627ca9fbac32f5c8530fb1903cc4db02258717921a4881"

def rdf4j_circuit(qf):
    r = subprocess.run(["java", "-cp", JAR, "npcs.circuit.CircuitRun", "Standard", DATA, qf],
                       capture_output=True, text=True, check=True)
    return r.stdout

def plan(qf):
    r = subprocess.run(["java", "-cp", JAR, "npcs.circuit.CircuitRun", "Standard", EMPTY, qf],
                       capture_output=True, text=True)
    out = []
    for ch in re.split(r"# --- step \d+ ---", r.stderr)[1:]:
        ch = ch.split("# ---- ")[0].split("# circuit triples")[0].strip()
        if ch.startswith("PREFIX") or ch.startswith("CONSTRUCT"):
            out.append(ch)
    return out

def sparql(query, accept):
    req = U.Request(EP, data=query.encode(), method="POST")
    req.add_header("Content-Type", "application/sparql-query"); req.add_header("Accept", accept)
    return U.urlopen(req, timeout=120).read().decode("utf-8", "replace")

def engine_circuit(qf):
    return "\n".join(sparql(c, "application/n-triples") for c in plan(qf))

def canon(nt):
    return "\n".join(sorted({l for l in nt.splitlines() if l.strip().endswith(" .")}))

def main():
    if not EP:
        print("set SPARQLCIRC_ENDPOINT to the engine's query endpoint (gallery data pre-loaded)"); sys.exit(2)
    print(f"[{NAME}] {EP}\n")
    # (0) SHA256 smoke test — the content-addressing depends on it
    try:
        r = sparql('SELECT (SHA256("x") AS ?h) WHERE {}', "text/csv")
        ok = SHA256_OF_X in r.lower()
        print(f"  [SHA256 support] {'OK' if ok else 'FAIL — content-addressing impossible'}  (got: {r.strip().splitlines()[-1][:70]})")
        if not ok:
            print("\nengine lacks SHA256 -> cannot materialize the content-addressed circuit"); sys.exit(1)
    except Exception as ex:
        print(f"  [SHA256 support] ERROR posting to endpoint: {ex}"); sys.exit(1)
    # (1) byte-identity per gallery query — the FULL E1 correctness set (so byte-identity ⊇ E1 coverage)
    from _gallery_shapes import E1_NONPATH as queries
    allok = True
    for q in queries:
        qf = f"{G}/{q}.sparql"
        if not os.path.exists(qf):
            continue
        try:
            a = canon(rdf4j_circuit(qf)); b = canon(engine_circuit(qf))
        except Exception as ex:
            print(f"  [{q:12}] ERROR {type(ex).__name__}: {ex}"); allok = False; continue
        ok = a == b; allok &= ok
        print(f"  [{q:12}] {'OK  byte-identical' if ok else 'FAIL'}  ({len(a.splitlines())} circuit triples)")
        if not ok:
            da = set(a.splitlines()) - set(b.splitlines()); db = set(b.splitlines()) - set(a.splitlines())
            if da: print(f"      only RDF4J: {list(da)[:2]}")
            if db: print(f"      only {NAME}: {list(db)[:2]}")
    print(f"\n[{NAME}] ALL byte-identical" if allok else f"\n[{NAME}] DIVERGENCES")
    sys.exit(0 if allok else 1)

if __name__ == "__main__":
    main()
