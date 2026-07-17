"""E2 — CIRCUIT_PERSIST named-graph portability across engines.

CIRCUIT_PERSIST/CIRCUIT_GRAPH materialise the finished circuit into a per-run NAMED GRAPH
(urn:circuit:run:<hash>) via SPARQL UPDATE (INSERT DATA / CLEAR GRAPH). This was only ever exercised on
GraphDB; named-graph writes are engine-specific, so this gate runs the round-trip on ANY writable SPARQL 1.1
endpoint and asserts:
  (1) build the circuit on the engine with CIRCUIT_PERSIST=1 into a known named graph;
  (2) the named graph holds EXACTLY the emitted circuit triples (persisted count == stdout count);
  (3) NO circuit gate leaked into the default graph (only the loaded base data lives there);
  (4) CLEAR GRAPH removes exactly that graph -> the named graph is empty, base data untouched.

Engine-agnostic: pass the query + update endpoints. Run per engine (GraphDB, Oxigraph, Fuseki, ...);
read-only engines (QLever/MillenniumDB) cannot persist and are out of scope by design.

Usage: verify_persist_portability.py <label> <query_ep> <update_ep> [scheme] [data_file] [query_file]
"""
import os, sys, subprocess
import urllib.request as U, urllib.parse as UP

HERE = os.path.dirname(os.path.abspath(__file__))
JAR = os.path.join(HERE, "..", "engine", "target", "npcs-rewrite.jar")
EX = os.path.join(HERE, "..", "engine", "examples")
GRAPH = "urn:circuit:persisttest"


def q(ep, query):
    req = U.Request(ep, data=query.encode(), method="POST")
    req.add_header("Content-Type", "application/sparql-query"); req.add_header("Accept", "text/csv")
    return U.urlopen(req, timeout=120).read().decode()


def count(ep, where):
    rows = q(ep, f"SELECT (COUNT(*) AS ?c) WHERE {{ {where} }}").splitlines()
    return int(rows[1]) if len(rows) > 1 and rows[1].strip() else 0


def update(update_ep, stmt):
    req = U.Request(update_ep, data=UP.urlencode({"update": stmt}).encode(), method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    U.urlopen(req, timeout=120).read()


def main():
    if len(sys.argv) < 4:
        print(__doc__); sys.exit(2)
    label, query_ep, update_ep = sys.argv[1:4]
    scheme = sys.argv[4] if len(sys.argv) > 4 else "Standard"
    data_file = sys.argv[5] if len(sys.argv) > 5 else os.path.join(EX, "data", "example.standard.ttl")
    query_file = sys.argv[6] if len(sys.argv) > 6 else os.path.join(EX, "queries", "monotonic", "and.sparql")

    env = {**os.environ, "CIRCUIT_PERSIST": "1", "CIRCUIT_GRAPH": GRAPH,
           "CIRCUIT_UPDATE_ENDPOINT": update_ep}
    cmd = ["java", "-cp", JAR, "npcs.circuit.CircuitRun", scheme, data_file, query_file, query_ep]
    r = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=600)
    if r.returncode:
        print(f"[{label}] FAIL: CircuitRun rc={r.returncode}\n{r.stderr[-1500:]}"); sys.exit(1)
    emitted = len([l for l in r.stdout.splitlines() if l.strip().endswith(" .")])
    persisted = next((int(l.split("persisted")[1].split()[0])
                      for l in r.stderr.splitlines() if "persisted" in l and "named graph" in l), None)

    gate_where = "?s a ?t . FILTER(?t IN (<urn:circuit:Times>, <urn:circuit:Plus>, <urn:circuit:Minus>))"
    checks = []
    in_graph = count(query_ep, f"GRAPH <{GRAPH}> {{ ?s ?p ?o }}")
    checks.append(("named graph holds the circuit", in_graph == emitted and emitted > 0,
                   f"graph={in_graph} emitted={emitted} persisted={persisted}"))
    gates_before = count(query_ep, gate_where)   # note: some engines' default dataset is the union of all graphs
    # CLEAR GRAPH removes exactly that graph
    update(update_ep, f"CLEAR GRAPH <{GRAPH}>")
    after = count(query_ep, f"GRAPH <{GRAPH}> {{ ?s ?p ?o }}")
    checks.append(("CLEAR GRAPH empties the run graph", after == 0, f"graph-after-clear={after}"))
    # THE leak test (engine-agnostic): if any gate had leaked into the default graph, clearing ONLY the run
    # graph would leave it behind. Zero gates anywhere after the clear => every gate lived in the run graph.
    gates_after = count(query_ep, gate_where)
    checks.append(("all gates lived in the run graph (none leaked)", gates_after == 0,
                   f"gates before-clear={gates_before}, after-clear={gates_after}"))
    # base data survived the clear (default graph still non-empty)
    base = count(query_ep, "?s ?p ?o")
    checks.append(("base data untouched by cleanup", base > 0, f"triples remaining={base}"))

    ok = all(c[1] for c in checks)
    print(f"[{label}] CIRCUIT_PERSIST portability ({scheme}): {'PASS' if ok else 'FAIL'}")
    for name, good, detail in checks:
        print(f"    {'ok ' if good else 'FAIL'} {name}  ({detail})")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
