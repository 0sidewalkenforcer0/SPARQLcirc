"""G8 — space & memory at scale (ROUND 7 should-have).

Three numbers a systems reviewer asks for, on the REAL circuits:
  1. on-disk circuit bytes  — the materialized circuit as N-Triples (Σ line bytes), + gates+edges;
                              compared to NPCS's per-answer provenance STRING bytes (G2b) at the same query.
  2. compiled size          — shared-ROBDD nodes for ALL answers (one structure, E11) + d-DNNF nodes (G6).
  3. peak build RSS         — Maximum resident set size of the construction process (/usr/bin/time -v),
                              headline = Wikidata P279+ path on the 2.13 B graph under an 8 GB heap (G1).

  LD_LIBRARY_PATH=$CONDA_PREFIX/lib python3 g8_space_memory.py
"""
import os, sys, time, subprocess, csv, re, tempfile
sys.setrecursionlimit(1_000_000)
import g3_pqe_latency as g3
import compile_bdd
from e6_minus import parse_circuit, counts, JAR, EMPTY, post
import e3_run

GDB   = "http://localhost:7200/repositories"
PLEAF = 0.5

# NPCS per-answer string bytes for the same queries (from G2b / g2b_npcs_vs_ours.csv) — for the ratio.
NPCS_BYTES = {"watdiv-Sstar": 2587, "watdiv-P2unbound": 19935124}

def construct_bytes(ep, scheme, qtext):
    """POST the CONSTRUCT plan, collect circuit triples, return (circ, ans, nt_bytes, gates_edges)."""
    e3_run.EP = ep
    cons = g3.plan(scheme, qtext)
    triples = set()
    for c in cons:
        _, b = post(c)
        triples.update(l for l in b.decode("utf-8", "replace").splitlines() if l.endswith(" ."))
    nt_bytes = sum(len(l) + 1 for l in triples)
    circ, ans, typ = parse_circuit(triples)
    tms, plus, minus, edges, answers = counts(circ, ans, typ)
    return circ, ans, nt_bytes, tms + plus + minus + edges

def shared_obdd_nodes(circ, ans):
    """One ROBDD, all answer roots share the unique-table (E11 shared compile). Total distinct nodes."""
    roots = {key: node for node, key in ans.items()}
    from e11_per_answer_vs_shared import global_order
    order = global_order(circ, roots)
    bdd = compile_bdd.ROBDD(order); memo = {}
    seen = set()
    for key, r in roots.items():
        n = compile_bdd.compile_root(circ, r, bdd, memo)
        stack = [n]
        while stack:
            x = stack.pop()
            if x in seen or x in (bdd.TRUE, bdd.FALSE): continue
            seen.add(x); _, lo, hi = bdd.nodes[x]; stack += [lo, hi]
    return len(seen)

def peak_rss_path(ep, qf, heap="8g"):
    """Peak RSS of the CircuitRun path build by polling /proc/<pid>/status VmHWM
    (/usr/bin/time is absent here). Returns (peak_rss_mb, wall_s)."""
    cmd = ["java", f"-Xmx{heap}", "-cp", JAR, "npcs.circuit.CircuitRun",
           "Standard", EMPTY.name, qf, ep]
    t = time.time()
    p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    peak_kb = 0
    while p.poll() is None:
        try:
            for line in open(f"/proc/{p.pid}/status"):
                if line.startswith("VmHWM"):
                    peak_kb = max(peak_kb, int(line.split()[1])); break
        except Exception:
            pass
        time.sleep(0.15)
    p.wait()
    return (peak_kb / 1024 if peak_kb else None), time.time() - t

SOB_MAX = int(os.environ.get("G8_SOB_MAX", "20000"))       # shared-OBDD only when cheap (pure-Python)

def main():
    print("G8 — space & memory at scale\n")
    rows = []
    print("## peak build memory (RSS) — headline: property path on 2.13B graph")
    print(f"{'query':18} {'graph':>10} {'heap':>5} {'peak_RSS_MB':>11} {'wall_s':>7}")
    for name, ep, qf, heap, graph in [
        ("wikidata-WDpath", f"{GDB}/wdpaths", "wikidata/WD-path.rq", "8g", "2.13B"),
    ]:
        if not os.path.exists(qf): print(f"  {name}: {qf} missing"); continue
        rss, wall = peak_rss_path(ep, qf, heap)
        print(f"{name:18} {graph:>10} {heap:>5} {(rss or 0):>11.0f} {wall:>7.1f}")
        rows.append(dict(query=name, peak_rss_mb=round(rss) if rss else None, heap=heap, graph=graph,
                         wall_s=round(wall, 1)))
    print("\n## circuit space (on-disk N-Triples)")
    print(f"{'query':18} {'answers':>7} {'gates+edges':>11} {'circuit_bytes':>13} {'NPCS_bytes':>11} {'space_win':>9} {'sharedOBDD':>10}")
    SPACE = [
        ("watdiv-Sstar",     f"{GDB}/watdiv",  "Standard", "engines/bound/S-star.rq"),
        ("watdiv-P2unbound", f"{GDB}/watdiv",  "Standard", "engines/bound/P2-unbound.rq"),
        ("tpch-Q3",          f"{GDB}/tpch001", "naryrel",  "tpch/skeletons/Q3.rq"),
    ]
    for name, ep, scheme, qf in SPACE:
        if not os.path.exists(qf): print(f"  {name}: {qf} missing"); continue
        try:
            circ, ans, nb, ge = construct_bytes(ep, scheme, open(qf).read())
            sob = shared_obdd_nodes(circ, ans) if len(ans) <= SOB_MAX else None
        except Exception as ex:
            print(f"  {name}: {type(ex).__name__}: {ex}"); continue
        npb = NPCS_BYTES.get(name); win = round(npb / nb, 1) if npb else None
        print(f"{name:18} {len(ans):>7} {ge:>11} {nb:>13} {str(npb or '-'):>11} {str(win or '-')+'x':>9} {str(sob or 'n/a-large'):>10}")
        rows.append(dict(query=name, answers=len(ans), gates_edges=ge, circuit_bytes=nb,
                         npcs_bytes=npb, space_win=win, shared_obdd_nodes=sob))
    with open("g8_space_memory.csv", "w", newline="") as f:
        keys = ["query","answers","gates_edges","circuit_bytes","npcs_bytes","space_win",
                "shared_obdd_nodes","peak_rss_mb","heap","graph","wall_s"]
        w = csv.DictWriter(f, fieldnames=keys); w.writeheader()
        for r in rows: w.writerow({k: r.get(k, "") for k in keys})
    print("\nwrote g8_space_memory.csv")

if __name__ == "__main__":
    main()
