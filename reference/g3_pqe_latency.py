"""G3 — end-to-end PQE latency (ROUND 6). ONE harness measuring the FULL probabilistic-query-evaluation
wall-clock as a single number with its breakdown: CONSTRUCT (engine builds the shared circuit) ->
COMPILE (d4 -> d-DNNF, per answer) -> WMC (d4 weighted model count). Previously E3 timed construction
and E4/E11 timed compile+WMC apart; this joins them. NPCS/SPARQLprov stop after producing provenance
(the CONSTRUCT/decode analogue) and compute NO probability -> the compile+WMC columns are the PQE stage
they lack. Runs on the loaded GraphDB repos (WatDiv / TPC-H / Wikidata incl. a property path via G1).

  D4=.../d4 LD_LIBRARY_PATH=$CONDA_PREFIX/lib python3 g3_pqe_latency.py
"""
import os, sys, time, subprocess, tempfile, csv, re
sys.setrecursionlimit(1_000_000)
import e3_run
from e6_minus import parse_circuit, JAR, EMPTY, post
import compile_bdd
from e11_per_answer_vs_shared import global_order

GDB = "http://localhost:7200/repositories"
PLEAF = 0.5                                                # per-token probability (uniform)

def plan(scheme, qtext):
    """Emit the CONSTRUCT plan (in-memory on empty data) for a scheme (Standard / naryrel)."""
    qf = tempfile.NamedTemporaryFile("w", suffix=".rq", delete=False); qf.write(qtext); qf.close()
    r = subprocess.run(["java", "-cp", JAR, "npcs.circuit.CircuitRun", scheme, EMPTY.name, qf.name],
                       capture_output=True, text=True)
    out = []
    for ch in re.split(r"# --- step \d+ ---", r.stderr)[1:]:
        ch = ch.split("# ---- ")[0].split("# circuit triples")[0].strip()
        if ch.startswith(("PREFIX", "CONSTRUCT")): out.append(ch)
    return out

def construct_bgp(endpoint, scheme, qtext):
    e3_run.EP = endpoint                                   # post CONSTRUCTs here
    cons = plan(scheme, qtext)
    t = time.time(); triples = set()
    for c in cons:
        _, body = post(c)
        triples.update(l for l in body.decode("utf-8", "replace").splitlines() if l.endswith(" ."))
    circ, ans, _ = parse_circuit(triples)                  # RDF decode + answer recovery IS part of construction
    ms = (time.time() - t) * 1000                          # (NOTE: the Java rewrite in plan() above is not timed)
    return circ, ans, ms

def construct_path(endpoint, qfile):
    t = time.time()
    r = subprocess.run(["java", "-Xmx8g", "-cp", JAR, "npcs.circuit.CircuitRun", "Standard",
                        EMPTY.name, qfile, endpoint], capture_output=True, text=True)
    triples = set(l for l in r.stdout.splitlines() if l.endswith(" ."))
    circ, ans, _ = parse_circuit(triples)                  # include RDF decode in the timed construction
    ms = (time.time() - t) * 1000                          # (path mode DOES include the JVM+rewrite; BGP does not)
    return circ, ans, ms

def compile_wmc(circ, ans):
    """OUR pipeline: compile the SHARED circuit ONCE (one ROBDD, cross-answer node sharing) then WMC
    every answer root. Returns (compile_ms, wmc_ms, n_answers, n_valid). (This is the shared-compile of
    E11 Result 2 — Θ(N+S), not the per-answer Θ(N·S) a baseline would pay.)"""
    P = {circ[n][1]: PLEAF for n in circ if circ[n][0] == "leaf"}
    roots = {key: node for node, key in ans.items()}                 # parse_circuit gives {node:key}; invert
    t = time.time()
    order = global_order(circ, roots)                                # variable ordering IS part of compilation
    bdd = compile_bdd.ROBDD(order); memo = {}; nodes = {}
    for key, r in roots.items():
        nodes[key] = compile_bdd.compile_root(circ, r, bdd, memo)     # shared unique-table + memo
    comp = (time.time() - t) * 1000
    t = time.time(); probs = {key: bdd.wmc(n, P) for key, n in nodes.items()}; wmcms = (time.time() - t) * 1000
    ok = sum(1 for p in probs.values() if -1e-9 <= p <= 1.0 + 1e-9)
    return comp, wmcms, len(roots), ok

QUERIES = [
    ("watdiv-Sstar", f"{GDB}/watdiv",  "Standard", "engines/bound/S-star.rq",  False),
    ("tpch-Q3",      f"{GDB}/tpch001", "naryrel",  "tpch/skeletons/Q3.rq",     False),
    ("wikidata-WDpath", f"{GDB}/wdpaths", "Standard", "wikidata/WD-path.rq",   True),
]

def main():
    print("G3 — end-to-end PQE latency: construct -> shared compile (ROBDD) -> WMC (all answers)\n")
    print(f"{'query':18} {'answers':>7} {'construct_ms':>12} {'compile_ms':>11} {'wmc_ms':>8} {'total_ms':>9}  note")
    rows = []
    for name, ep, scheme, qf, is_path in QUERIES:
        if not os.path.exists(qf): print(f"  {name}: {qf} missing, skip"); continue
        try:
            if is_path: circ, ans, cms = construct_path(ep, qf)
            else:       circ, ans, cms = construct_bgp(ep, scheme, open(qf).read())
        except Exception as ex:
            print(f"  {name}: construct failed: {type(ex).__name__}: {ex}"); continue
        comp, wmcms, n, ok = compile_wmc(circ, ans)
        total = cms + comp + wmcms
        print(f"{name:18} {len(ans):>7} {cms:>12.0f} {comp:>11.0f} {wmcms:>8.0f} {total:>9.0f}  {ok}/{n} probs valid")
        rows.append(dict(query=name, answers=len(ans), construct_ms=round(cms), compile_ms=round(comp),
                         wmc_ms=round(wmcms), total_ms=round(total), compiled=n, wmc_ok=ok))
    with open("g3_pqe.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)
    print("\nwrote g3_pqe.csv  |  NPCS/SPARQLprov stop after 'construct' (provenance, no probability) — "
          "compile+WMC is the PQE stage they lack.")

if __name__ == "__main__":
    main()
