"""r9.6 property-path REACHABILITY SWEEP — how circuit construction scales with the reachable set.

The single-instance E-paths run (e_paths.py) reports one reach_nodes per operator. This sweep varies the
reachable set size over orders of magnitude at ~fixed diameter, running the SAME operator (friendOf+),
so construct time / circuit size / builder peak RSS can be plotted against |reachable nodes| — the
result_r9_6_paths scaling curve.

Controlled family: a layered friendOf DAG, D layers of width s (reach ≈ D·s), fan-in f between layers
(so answers have multiple derivations → ⊕ gates, a realistic multi-path lineage), diameter = D fixed.
Source = wsdbm:User0 → every layer-1 node; each deeper node ← f random nodes in the previous layer.

Builder peak RSS is polled from /proc/<pid>/VmHWM of the CircuitRun child (no /usr/bin/time on this box).
Each instance is capped at the paper's 300 s; an instance that exceeds it is recorded as timeout — that
ceiling is a data point. Writes reference/watdiv/e_paths_sweep.csv.

Run:  LD_LIBRARY_PATH=$CONDA_PREFIX/lib python3 e_paths_sweep.py
"""
import os, sys, re, csv, time, random, subprocess, threading, tempfile
from experiment_timeouts import QUERY_TIMEOUT_S

HERE = os.path.dirname(os.path.abspath(__file__))
JAR = os.path.abspath(os.path.join(HERE, "..", "engine", "target", "npcs-rewrite.jar"))
C = "urn:circuit:"; RS = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
W = "http://db.uwaterloo.ca/~galuc/wsdbm/"
FRIEND = W + "friendOf"
TIMEOUT = 300.0                                        # paper standard, per instance
D = 4                                                  # depth; CircuitRun does reach-1 protocol rounds
FANIN = 2
# reach ≈ 4·s+1; the iterative protocol runs |V_s|-1 rounds so construct is ~quadratic in reach and
# walls at ~300 answers within the 300 s cap -> sample the feasible range densely + one wall point.
SIZES = [3, 6, 12, 18, 25, 37, 50, 62, 150]           # reach ≈ 13,25,49,73,101,149,201,249, +wall


def gen_dag_reified(s, seed):
    """Layered friendOf DAG: User0 -> all of layer 1; each deeper node <- FANIN random prev-layer nodes.
    Returns (path, n_nodes). Reified as rdf:subject/predicate/object triples (the Standard scheme input)."""
    rnd = random.Random(seed)
    layers, nid = [[0]], 1                             # layer 0 = source User0
    for _ in range(D):
        layers.append(list(range(nid, nid + s))); nid += s
    edges = []
    for a in layers[1]:                               # source -> every layer-1 node
        edges.append((0, a))
    for k in range(1, D):                             # layer k -> layer k+1 (fan-in FANIN)
        for b in layers[k + 1]:
            for a in rnd.sample(layers[k], min(FANIN, len(layers[k]))):
                edges.append((a, b))
    fd, path = tempfile.mkstemp(suffix=f".s{s}.reified.nt", dir="/tmp"); os.close(fd)
    with open(path, "w") as fh:
        for t, (a, b) in enumerate(edges):
            fh.write(f"<urn:t:{t}> <{RS}subject> <{W}User{a}> .\n")
            fh.write(f"<urn:t:{t}> <{RS}predicate> <{FRIEND}> .\n")
            fh.write(f"<urn:t:{t}> <{RS}object> <{W}User{b}> .\n")
    return path, nid - 1, len(edges)


def run_with_rss(cmd, timeout):
    """Run CircuitRun, polling /proc/<pid>/VmHWM for the child's peak RSS (MiB)."""
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    peak = [0]
    def poll():
        while p.poll() is None:
            try:
                with open(f"/proc/{p.pid}/status") as st:
                    for line in st:
                        if line.startswith("VmHWM:"):
                            peak[0] = max(peak[0], int(line.split()[1])); break
            except Exception:
                pass
            time.sleep(0.05)
    th = threading.Thread(target=poll, daemon=True); th.start()
    try:
        out, err = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        p.kill(); p.communicate()          # reap the child so it never orphans/accumulates
        raise
    return out, err, peak[0] // 1024


def counts(nt):
    Times = Plus = ans = edges = 0
    for line in nt.splitlines():
        if not line.endswith(" ."):
            continue
        if C + "Times>" in line: Times += 1
        elif C + "Plus>" in line: Plus += 1
        elif C + "answer>" in line: ans += 1
        elif C + "in>" in line or C + "feeds>" in line: edges += 1
    return Times, Plus, ans, edges


def main():
    q = os.path.join("/tmp", "_psweep.rq")
    open(q, "w").write(f"PREFIX wsdbm: <{W}>\nSELECT ?y WHERE {{ wsdbm:User0 wsdbm:friendOf+ ?y }}\n")
    rows = []
    for s in SIZES:
        data, nodes, gen_edges = gen_dag_reified(s, seed=100 + s)
        cmd = ["java", "-cp", JAR, "npcs.circuit.CircuitRun", "Standard", data, q]
        rec = {"width": s, "graph_nodes": nodes, "graph_edges": gen_edges}
        try:
            t = time.time()
            out, err, rss = run_with_rss(cmd, TIMEOUT)
            ms = (time.time() - t) * 1000.0
            m = re.search(r"reachable-nodes=(\d+), rounds=(\d+)", err)
            reach, rounds = (int(m.group(1)), int(m.group(2))) if m else (None, None)
            Times, Plus, ans, edges = counts(out)
            rec.update(status="ok", reach_nodes=reach, rounds=rounds, build_ms=round(ms),
                       gates=Times + Plus, edges=edges, answers=ans, rss_mib=rss)
        except subprocess.TimeoutExpired:
            rec.update(status="timeout")
        except Exception as ex:
            rec.update(status="err:" + type(ex).__name__)
        finally:
            try: os.remove(data)
            except OSError: pass
        rows.append(rec)
        print(f"  s={s:<6} reach={rec.get('reach_nodes')} rounds={rec.get('rounds')} "
              f"build={rec.get('build_ms')}ms gates={rec.get('gates')} edges={rec.get('edges')} "
              f"ans={rec.get('answers')} rss={rec.get('rss_mib')}MiB ({rec['status']})", flush=True)
    cols = ["width", "graph_nodes", "graph_edges", "status", "reach_nodes", "rounds",
            "build_ms", "gates", "edges", "answers", "rss_mib"]
    out = os.path.join(HERE, "watdiv", "e_paths_sweep.csv")
    with open(out, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore", restval=""); w.writeheader(); w.writerows(rows)
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
