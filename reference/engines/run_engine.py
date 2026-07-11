#!/usr/bin/env python3
"""Run SPARQL_circ circuit construction on a chosen engine and record a comparable CSV row.

Reads the engine profile from engines.json, sets the per-engine CircuitRun env vars, invokes
CircuitRun over one or more query files, and parses gates/answers/build_ms + a canonical circuit
hash from its output. Non-path queries run on ANY engine; property paths only on writable engines
(read-only engines are auto-skipped for path queries).

The circuit is content-addressed, so the SAME query on any engine must yield the SAME `circuit_sha256`
(computed over the sorted triple set, order-independent) -- that is the engine-agnostic byte-identity check.

Usage:
  python3 run_engine.py --engine fuseki --data watdiv/slice.reified.ttl \\
      --queries watdiv/S-star.rq watdiv/M-minus.rq watdiv/P-plus.rq --out engines/results_fuseki.csv
Endpoints come from engines.json (edit to your deployment) or --query-endpoint/--update-endpoint.
For small data, add --load to INSERT it via CircuitRun; otherwise the data is assumed bulk-loaded.
NOTE: this driver buffers stdout, so use it for the byte-identity / moderate runs; the billion-scale
streaming path is e3_run.py.
"""
import argparse, csv, hashlib, json, os, re, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
JAR = os.path.abspath(os.path.join(HERE, "..", "..", "engine", "target", "npcs-rewrite.jar"))
REG = json.load(open(os.path.join(HERE, "engines.json")))
REG.pop("_comment", None)

def run_one(prof, scheme, data, qfile, load):
    env = dict(os.environ)
    if prof.get("readonly"):
        env["CIRCUIT_READONLY"] = "1"                       # implies skip-load in CircuitRun
    elif prof.get("update"):
        env["CIRCUIT_UPDATE_ENDPOINT"] = prof["update"]
    if not load and not prof.get("readonly"):
        env["CIRCUIT_SKIP_LOAD"] = "1"                      # assume bulk-loaded unless --load
    cmd = ["java", "-cp", JAR, "npcs.circuit.CircuitRun", scheme, data, qfile, prof["query"]]
    t = time.time()
    r = subprocess.run(cmd, capture_output=True, text=True, env=env)
    ms = (time.time() - t) * 1000
    if "Unresolved compilation" in r.stderr:
        return {"status": "ENGINE-BUILD-BROKEN"}
    if "ERROR: property-path queries need a WRITABLE" in r.stderr:
        return {"status": "skip-readonly-path"}
    if r.returncode != 0:
        last = (r.stderr.strip().splitlines() or ["rc=%d" % r.returncode])[-1]
        return {"status": "error", "msg": last[:200]}
    types = re.findall(r"<urn:circuit:(Times|Plus|Minus)>", r.stdout)
    lines = sorted(l for l in r.stdout.splitlines() if l.endswith(" ."))
    sha = hashlib.sha256("\n".join(lines).encode()).hexdigest()[:16]
    ct = re.search(r"# circuit triples: (\d+)", r.stderr)
    pm = re.search(r"reachable-nodes=(\d+), rounds=(\d+)", r.stderr)
    return {"status": "ok", "build_ms": round(ms),
            "circuit_triples": int(ct.group(1)) if ct else len(lines),
            "times": types.count("Times"), "plus": types.count("Plus"), "minus": types.count("Minus"),
            "answers": r.stdout.count("<urn:circuit:answer>"),
            "reach_nodes": pm.group(1) if pm else "", "rounds": pm.group(2) if pm else "",
            "circuit_sha256": sha}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", required=True, choices=sorted(REG))
    ap.add_argument("--scheme", help="Standard | SPARQL_Star (default: engine's first supported)")
    ap.add_argument("--data", required=True, help="reified data file (RDF-star fmt detection; loaded only with --load)")
    ap.add_argument("--queries", nargs="+", required=True)
    ap.add_argument("--out")
    ap.add_argument("--load", action="store_true", help="INSERT the data via CircuitRun (small data only)")
    ap.add_argument("--query-endpoint"); ap.add_argument("--update-endpoint")
    a = ap.parse_args()
    prof = dict(REG[a.engine])
    if a.query_endpoint: prof["query"] = a.query_endpoint
    if a.update_endpoint: prof["update"] = a.update_endpoint
    scheme = a.scheme or (prof.get("reification") or ["Standard"])[0]
    if scheme == "SPARQL_Star" and not prof.get("rdfstar"):
        sys.exit(f"[{a.engine}] no RDF-star support; use --scheme Standard")
    out = a.out or os.path.join(HERE, f"results_{a.engine}.csv")
    print(f"engine={a.engine}  scheme={scheme}  query={prof['query']}"
          + ("  [READ-ONLY: non-path only]" if prof.get("readonly") else ""))
    rows = []
    for q in a.queries:
        res = run_one(prof, scheme, a.data, q, a.load)
        res.update(engine=a.engine, query=os.path.splitext(os.path.basename(q))[0], scheme=scheme)
        tail = ""
        if res["status"] == "ok":
            g = res["times"] + res["plus"] + res["minus"]
            tail = f"  build={res['build_ms']}ms gates={g} ans={res['answers']} sha={res['circuit_sha256']}"
        print(f"  [{res['query']}] {res['status']}{tail}")
        rows.append(res)
    cols = ["engine", "query", "scheme", "status", "build_ms", "circuit_triples",
            "times", "plus", "minus", "answers", "reach_nodes", "rounds", "circuit_sha256", "msg"]
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore", restval=""); w.writeheader(); w.writerows(rows)
    print(f"wrote {out}")

if __name__ == "__main__":
    main()
