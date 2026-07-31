"""Round 3 - property-path construction (our contribution) on a bounded friendOf subgraph of real
WatDiv edges, via CircuitRun's iterative protocol (reachable-set round bound |V_s|-1). Runs the four
P-*.rq operators, records reachable-nodes / rounds / gates+edges / build_ms / answers, reports the
single-source vs all-pairs gate ratio (~|V|), and spot-checks circuit-WMC == possible-world
enumeration on a TINY subgraph. Full-scale single-user reach is a giant friendOf component
(infeasible per user) -- see the note; this bounded subgraph gives the exact, verifiable result.

Usage: python3 e_paths.py <subgraph.reified.nt> <SRC-localname> [<tiny.reified.nt> <tiny-SRC>]
"""
import os
import subprocess, time, re, sys, os, itertools, random

# P-plus-all is the all-pairs (free-endpoint) query this experiment exists to contrast with
# the single-source ones, and §3 excludes that construction, so the engine gates it behind an
# opt-in. Request it explicitly rather than letting the harness fail.
PATH_ENV = dict(os.environ, CIRCUIT_UNBOUND_PATHS="1")
from experiment_timeouts import QUERY_TIMEOUT_S
JAR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "engine", "target", "npcs-rewrite.jar"))
C = "urn:circuit:"; RS = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
SUB = sys.argv[1]; SRC = sys.argv[2]
QUERIES = ["P-plus", "P-star", "P-alt", "P-plus-all"]   # single-source ×3, all-pairs ×1

def circuit_run(data, qtext, timeout=QUERY_TIMEOUT_S):
    qf = "/tmp/_p.rq"; open(qf, "w").write(qtext)
    t = time.time()
    r = subprocess.run(["java", "-cp", JAR, "npcs.circuit.CircuitRun", "Standard", data, qf],
                       capture_output=True, text=True, timeout=timeout, env=PATH_ENV)
    return (time.time() - t) * 1000, r.stdout, r.stderr

def counts(nt):
    typ = tin = feeds = 0; ans = 0; Times = Plus = 0
    edges = 0
    for line in nt.splitlines():
        if line.endswith(" ."):
            if C + "Times>" in line: Times += 1
            elif C + "Plus>" in line: Plus += 1
            elif C + "answer>" in line: ans += 1
            elif C + "in>" in line or C + "feeds>" in line: edges += 1
    return Times, Plus, ans, edges

def run_all():
    print(f"Round 3 - property paths on friendOf subgraph  (source={SRC})\n")
    rows = []
    for name in QUERIES:
        q = open(f"watdiv/{name}.rq").read()
        q = re.sub(r"wsdbm:User\d+", f"wsdbm:{SRC}", q)   # point single-source queries at SRC
        try:
            ms, nt, err = circuit_run(SUB, q)
        except subprocess.TimeoutExpired:
            print(f"  [{name}] TIMEOUT"); rows.append(dict(query=name, status="timeout")); continue
        m = re.search(r"reachable-nodes=(\d+), rounds=(\d+)", err)
        reach, rounds = (int(m.group(1)), int(m.group(2))) if m else (None, None)
        Times, Plus, ans, edges = counts(nt)
        gates = Times + Plus
        print(f"  [{name:11}] reach-nodes={reach} rounds={rounds}  build={ms:.0f}ms  "
              f"⊗={Times} ⊕={Plus} gates={gates} edges={edges} answers={ans}")
        rows.append(dict(query=name, status="ok", reach_nodes=reach, rounds=rounds,
                         build_ms=round(ms), times=Times, plus=Plus, gates=gates, edges=edges, answers=ans))
    ok = {r["query"]: r for r in rows if r.get("status") == "ok"}
    if "P-plus" in ok and "P-plus-all" in ok and ok["P-plus"]["gates"]:
        ratio = ok["P-plus-all"]["gates"] / ok["P-plus"]["gates"]
        print(f"\n  all-pairs / single-source gate ratio = {ratio:.1f}×  (≈ |V|={ok['P-plus']['reach_nodes']})")
    import csv
    cols = ["query", "status", "reach_nodes", "rounds", "build_ms", "times", "plus", "gates", "edges", "answers"]
    with open("watdiv/e_paths.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore", restval=""); w.writeheader(); w.writerows(rows)
    print("wrote watdiv/e_paths.csv")

def wmc_pwe_tiny(tiny, tinysrc):
    """circuit-WMC == possible-world enumeration on a TINY friend subgraph (P-plus)."""
    import compile_bdd
    q = f"PREFIX wsdbm: <http://db.uwaterloo.ca/~galuc/wsdbm/>\nSELECT ?y WHERE {{ wsdbm:{tinysrc} wsdbm:friendOf+ ?y }}\n"
    _, nt, _ = circuit_run(tiny, q)
    # parse into circ dict
    typ, feeds, tin, ans = {}, {}, {}, {}
    for line in nt.splitlines():
        if not line.endswith(" ."): continue
        s, p, o = line[:-2].split(None, 2); s = s.strip("<>"); p = p.strip("<>"); o = o.strip()
        if p == RS + "type": typ[s] = o.strip("<>")
        elif p == C + "feeds": feeds.setdefault(o.strip("<>"), set()).add(s)
        elif p == C + "in": tin.setdefault(s, set()).add(o.strip("<>"))
        elif p == C + "answer": ans[s] = o
    circ = {}
    for n, t in typ.items():
        if t.endswith("Times"): circ[n] = ("times", tuple(sorted(tin.get(n, ()))))
        elif t.endswith("Plus"): circ[n] = ("plus", tuple(sorted(feeds.get(n, ()))))
    ref = set()
    for op, pl in circ.values(): ref |= set(pl)
    for r in ref: circ.setdefault(r, ("leaf", r))
    leaves = sorted({circ[n][1] for n in circ if circ[n][0] == "leaf"})
    if len(leaves) > 20:
        print(f"  tiny subgraph has {len(leaves)} tokens (>20) - too big for PWE; pick a smaller one"); return
    random.seed(3); P = {t: round(random.uniform(0.3, 0.9), 3) for t in leaves}
    def ev(nid, w):
        op, pl = circ[nid]
        return w[pl] if op == "leaf" else (all(ev(c, w) for c in pl) if op == "times" else any(ev(c, w) for c in pl))
    worst = 0.0
    for root in ans:
        wmc = compile_bdd.probability(circ, root, P)[0]; pwe = 0.0
        for bits in itertools.product((0, 1), repeat=len(leaves)):
            w = dict(zip(leaves, bits))
            if ev(root, w):
                pr = 1.0
                for t in leaves: pr *= P[t] if w[t] else 1 - P[t]
                pwe += pr
        worst = max(worst, abs(wmc - pwe))
    print(f"\n  WMC == PWE spot-check (tiny, {len(leaves)} tokens, {len(ans)} answers): max|Δ| = {worst:.1e}  "
          f"{'OK' if worst < 1e-9 else 'FAIL'}")

if __name__ == "__main__":
    run_all()
    if len(sys.argv) >= 5:
        wmc_pwe_tiny(sys.argv[3], sys.argv[4])
