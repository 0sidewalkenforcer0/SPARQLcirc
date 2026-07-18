"""Phase 2 — operator breadth on real Wikidata (claims A/B/C at KG scale, beyond the single path query).

Runs the six WD-*.rq queries through OUR gamma rewriter with the fixed O(N) compiler and records
construct + compile+WMC + answers + circuit size, 1 warm-up + RUNS timed:
  - BGP-join (WD-star), UNION (WD-union), OPTIONAL (WD-opt), MINUS (WD-minus)  -> on `wdreal`
    (Standard-reified P106/P27 truthy subset extracted from the 2.13 B latest-truthy dump)
  - property paths P279+ (WD-path), P131+ (WD-path2)                            -> on `wdpaths`
The whole non-monotone + path fragment on real Wikidata, on a STOCK engine. Correctness of these
operators is established separately (validation_matrix: WMC==PWE 0.0; E1/E6). Writes
reference/wikidata/phase2_breadth.csv. Run from reference/ with the engine jar + repos loaded.
"""
import os, sys, re, csv, time, statistics, subprocess, tempfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")
import e3_run
import e11_per_answer_vs_shared as e11
import g3_pqe_latency as g3
from e6_minus import build, parse_circuit, counts, JAR, EMPTY

RUNS = int(os.environ.get("P2_RUNS", "3"))
GDB = "http://localhost:7200/repositories"
med = statistics.median
HERE = os.path.dirname(os.path.abspath(__file__))


def plan_std(qtext):
    qf = tempfile.NamedTemporaryFile("w", suffix=".rq", delete=False); qf.write(qtext); qf.close()
    r = subprocess.run(["java", "-cp", JAR, "npcs.circuit.CircuitRun", "--construction=flat", "Standard",
                        EMPTY.name, qf.name], capture_output=True, text=True)
    out = []
    for ch in re.split(r"# --- step \d+ ---", r.stderr)[1:]:
        ch = ch.split("# ---- ")[0].split("# circuit triples")[0].strip()
        if ch.startswith(("PREFIX", "CONSTRUCT")):
            out.append(ch)
    return out


def run_bgp(repo, qtext):
    e3_run.EP = f"{GDB}/{repo}"
    cons = plan_std(qtext)
    tot, nans, gsz = [], None, None
    for i in range(RUNS + 1):
        t = time.time(); _, triples, cap = build(cons); cms = (time.time() - t) * 1000
        if cap:
            return {"status": "too-large"}
        circ, ans, typ = parse_circuit(triples)
        roots = {j: g for j, g in enumerate(sorted(ans))}
        P = {n: 0.5 for n, (op, pl) in circ.items() if op == "leaf"}
        t = time.time(); order = e11.global_order(circ, roots); e11.compile_shared(circ, roots, P, order)
        cw = (time.time() - t) * 1000
        nans = len(roots); gsz = len(circ)
        if i:
            tot.append(cms + cw)
    return {"status": "ok", "answers": nans, "gates": gsz, "total_ms": round(med(tot)),
            "total_min": round(min(tot)), "total_max": round(max(tot))}


def run_path(repo, qfile):
    ep = f"{GDB}/{repo}"
    tot, nans = [], None
    for i in range(RUNS + 1):
        circ, ans, cms = g3.construct_path(ep, qfile)
        comp, w, n, ok, _ = g3.compile_wmc(circ, ans)
        nans = n
        if i:
            tot.append(cms + comp + w)
    return {"status": "ok", "answers": nans, "gates": len(circ), "total_ms": round(med(tot)),
            "total_min": round(min(tot)), "total_max": round(max(tot))}


def main():
    jobs = [
        ("WD-star", "BGP-join", "bgp", "wdreal"),
        ("WD-union", "UNION", "bgp", "wdreal"),
        ("WD-opt", "OPTIONAL", "bgp", "wdreal"),
        ("WD-minus", "MINUS", "bgp", "wdreal"),
        ("WD-path", "PATH-P279+", "path", "wdpaths"),
        ("WD-path2", "PATH-P131+", "path", "wdpaths"),
    ]
    rows = []
    for name, op, kind, repo in jobs:
        qf = os.path.join(HERE, f"{name}.rq")
        try:
            r = run_bgp(repo, open(qf).read()) if kind == "bgp" else run_path(repo, qf)
        except Exception as ex:
            r = {"status": "err:" + type(ex).__name__}
        r.update({"query": name, "operator": op, "repo": repo})
        rows.append(r)
        print(f"  {name:9} {op:11} {repo:8} ans={r.get('answers','?'):>7} gates={r.get('gates','?'):>8} "
              f"total={r.get('total_ms','?')} ms ({r['status']})", flush=True)
    cols = ["query", "operator", "repo", "status", "answers", "gates", "total_ms", "total_min", "total_max"]
    out = os.path.join(HERE, "phase2_breadth.csv")
    with open(out, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore", restval=""); w.writeheader(); w.writerows(rows)
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
