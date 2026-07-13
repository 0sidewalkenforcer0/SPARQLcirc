"""B-iv: real engine-side timings on a deployed GraphDB, at growing data size.

Query: 2-hop join  SELECT ?x ?z WHERE { ?x :e ?y . ?y :e ?z }  (treewidth 1, so
compile+WMC stays feasible). Data: random sparse directed graphs of growing size.
Per size we time, on the actual GraphDB server:
  load_ms         - bulk-load the reified triples
  build_ms        - GraphDB runs OUR CONSTRUCT and materializes the circuit
  npcs_ms/bytes   - GraphDB runs the NPCS SELECT (string provenance) -> string size
  wmc_ms          - client compiles the circuit + weighted model counts (small sizes)
and records circuit triples + #answers. This is the deployed-engine systems table.
"""
import sys, time, csv, random, subprocess, urllib.request as U
sys.setrecursionlimit(1_000_000)
sys.path.insert(0, ".")
import compile_bdd
from experiment_timeouts import QUERY_TIMEOUT_S

GDB = "http://localhost:7200"; REPO = "bench"
RS = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
CONSTRUCT = open("bench_engine/twohop.construct.rq").read()
NPCS = open("bench_engine/twohop.npcs.rq").read()
random.seed(7)

LOAD_TIMEOUT_S = 1200                                  # operational data load, not a timed query cell

def post(path, body, ctype, accept=None, timeout=QUERY_TIMEOUT_S):
    req = U.Request(GDB + path, data=body.encode(), method="POST")
    req.add_header("Content-Type", ctype)
    if accept: req.add_header("Accept", accept)
    t = time.time(); r = U.urlopen(req, timeout=timeout); data = r.read()
    return (time.time() - t) * 1000, data

def clear():
    try: U.urlopen(U.Request(f"{GDB}/repositories/{REPO}/statements", method="DELETE"), timeout=120)
    except Exception: pass

def make_repo():
    cfg = f'''@prefix rep: <http://www.openrdf.org/config/repository#> .
@prefix sr: <http://www.openrdf.org/config/repository/sail#> .
@prefix sail:<http://www.openrdf.org/config/sail#> .
@prefix graphdb: <http://www.ontotext.com/config/graphdb#> .
[] a rep:Repository ; rep:repositoryID "{REPO}" ; rdfs:label "{REPO}" ;
  rep:repositoryImpl [ rep:repositoryType "graphdb:SailRepository" ;
    sr:sailImpl [ sail:sailType "graphdb:Sail" ; graphdb:ruleset "empty" ] ] .'''
    open("bench_engine/repo.ttl", "w").write(cfg)
    subprocess.run(["curl", "-s", "-X", "DELETE", f"{GDB}/repositories/{REPO}"], capture_output=True)
    subprocess.run(["curl", "-s", "-X", "POST", f"{GDB}/rest/repositories",
                    "-F", "config=@bench_engine/repo.ttl"], capture_output=True)

def gen_ttl(N, deg):
    """random sparse graph: N nodes, ~deg out-edges each; reified with tokens."""
    lines = ["@prefix d: <urn:d:> .", f"@prefix rdf: <{RS}> ."]
    tid = 0
    for u in range(N):
        for _ in range(deg):
            w = random.randint(0, N - 1)
            lines.append(f"d:t{tid} rdf:subject d:n{u} ; rdf:predicate d:e ; rdf:object d:n{w} .")
            tid += 1
    return "\n".join(lines) + "\n", tid

def parse_circuit(nt):
    typ, feeds, tin, ans = {}, {}, {}, {}
    for line in nt.splitlines():
        if not line.endswith(" ."): continue
        parts = line[:-2].split(None, 2)
        if len(parts) < 3: continue
        s, p, o = parts[0].strip("<>"), parts[1].strip("<>"), parts[2].strip()
        if p == RS + "type":
            typ[s] = o.strip("<>")
        elif p == "urn:circuit:feeds":
            feeds.setdefault(o.strip("<>"), set()).add(s)
        elif p == "urn:circuit:in":
            tin.setdefault(s, set()).add(o.strip("<>"))
        elif p == "urn:circuit:answer":
            ans[s] = o
    circ = {}; ref = set()
    for n, t in typ.items():
        if t.endswith("Times"): circ[n] = ("times", tuple(sorted(tin.get(n, ())))); ref |= tin.get(n, set())
        elif t.endswith("Plus"): circ[n] = ("plus", tuple(sorted(feeds.get(n, ())))); ref |= feeds.get(n, set())
    for r in ref:
        circ.setdefault(r, ("leaf", r.replace("urn:d:", "")))
    return circ, ans

def npcs_bytes(csvdata):
    # SPARQL CSV: header line then rows; the provenance column is the long one.
    txt = csvdata.decode("utf-8", "replace")
    return len(txt), txt.count("\n")

def main():
    make_repo()
    print(f"{'N':>5} {'edges':>6} {'triples':>7} | {'load_ms':>7} {'build_ms':>8} {'circuit_tr':>10} {'ans':>5} | "
          f"{'npcs_ms':>7} {'npcs_KB':>7} | {'wmc_ms':>7}")
    rows = []
    for N, deg in [(100, 3), (200, 3), (400, 3), (800, 3), (1500, 3)]:
        ttl, E = gen_ttl(N, deg)
        clear()
        load_ms, _ = post(f"/repositories/{REPO}/statements", ttl, "text/turtle",
                          timeout=LOAD_TIMEOUT_S)
        build_ms, circ_nt = post(f"/repositories/{REPO}", CONSTRUCT, "application/sparql-query", "application/n-triples")
        circ_tr = circ_nt.count(b" .\n") + circ_nt.count(b" .")  # rough triple count
        circ, ans = parse_circuit(circ_nt.decode("utf-8", "replace"))
        npcs_ms, npcs_data = post(f"/repositories/{REPO}", NPCS, "application/sparql-query", "text/csv")
        nbytes, _ = npcs_bytes(npcs_data)
        wmc_ms = "-"
        if N <= 400:
            P = {c[1]: round(random.uniform(0.2, 0.9), 3) for n, c in circ.items() if c[0] == "leaf"}
            t = time.time()
            for r in ans: compile_bdd.probability(circ, r, P)
            wmc_ms = f"{(time.time()-t)*1000:.0f}"
        row = dict(N=N, edges=E, triples=3 * E, load_ms=round(load_ms), build_ms=round(build_ms),
                   circuit_triples=len(circ_nt.decode().splitlines()), answers=len(ans),
                   npcs_ms=round(npcs_ms), npcs_kb=round(nbytes / 1024, 1), wmc_ms=wmc_ms)
        rows.append(row)
        print(f"{N:>5} {E:>6} {3*E:>7} | {round(load_ms):>7} {round(build_ms):>8} {row['circuit_triples']:>10} "
              f"{len(ans):>5} | {round(npcs_ms):>7} {row['npcs_kb']:>7} | {str(wmc_ms):>7}")
    with open("bench_engine/results.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)
    print("\nwrote bench_engine/results.csv")

if __name__ == "__main__":
    main()
