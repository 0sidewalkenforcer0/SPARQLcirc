"""Real-KG run: WatDiv queries over a reified WatDiv graph on a live triplestore.
Per query, on the server: rewrite -> engine builds the circuit -> client compile+WMC,
with the NPCS string query for the sharing comparison.

Data target (VLDB): WatDiv 100M (pilot/data/watdiv.100M.nt) reified into repo "watdiv",
queries from pilot/data/official_q_100M via WATDIV_QDIR. base.nt (51K) is a smoke-test only.
Env: WATDIV_REPO (default "watdiv"); WATDIV_QDIR (dir of *.rq/*.sparql; unset -> built-in
S/L/F smoke shapes); WATDIV_SCHEME (default "Standard"; use "Standard_Pure"
only with a historical token-only store)."""
import os, sys, time, glob, subprocess, random, urllib.request as U
sys.setrecursionlimit(1_000_000); sys.path.insert(0, ".")
import compile_bdd
from experiment_timeouts import QUERY_TIMEOUT_S
random.seed(3)

GDB = "http://localhost:7200"
REPO = os.environ.get("WATDIV_REPO", "watdiv")
JAR = "../engine/target/npcs-rewrite.jar"
SCHEME = os.environ.get("WATDIV_SCHEME", "Standard")
RS = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"

def post(body, ctype, accept):
    req = U.Request(f"{GDB}/repositories/{REPO}", data=body.encode(), method="POST")
    req.add_header("Content-Type", ctype); req.add_header("Accept", accept)
    t = time.time(); data = U.urlopen(req, timeout=QUERY_TIMEOUT_S).read()
    return (time.time() - t) * 1000, data

def get_construct(qfile):
    r = subprocess.run(["java", "-cp", JAR, "npcs.circuit.CircuitRun", "--construction=flat",
                        SCHEME, "bench_engine/tiny.ttl", qfile], capture_output=True,
                       text=True, check=True)
    out, p = [], False
    plans = 0
    for l in r.stderr.splitlines():
        if l.startswith("PREFIX c:"):
            p = True
            plans += 1
        if l.startswith("# circuit triples"): p = False
        if p: out.append(l)
    if plans != 1:
        raise RuntimeError(f"one-shot WatDiv execution requires exactly one flat CONSTRUCT; got {plans}")
    return "\n".join(out)

def get_npcs(qfile):
    return subprocess.run(["java", "-jar", JAR, SCHEME, "query", open(qfile).read()],
                          capture_output=True, text=True).stdout

def _arity(q):
    """Rough #triple-patterns in the WHERE (for the T_string metric on arbitrary queries)."""
    body = q[q.find("{") + 1: q.rfind("}")]
    return max(1, body.count(" ."))

def parse_circuit(nt):
    typ, feeds, tin, ans = {}, {}, {}, {}
    for line in nt.splitlines():
        if not line.endswith(" ."): continue
        s, p, o = line[:-2].split(None, 2)
        s = s.strip("<>"); p = p.strip("<>"); o = o.strip()
        if p == RS + "type": typ[s] = o.strip("<>")
        elif p == "urn:circuit:feeds": feeds.setdefault(o.strip("<>"), set()).add(s)
        elif p == "urn:circuit:in": tin.setdefault(s, set()).add(o.strip("<>"))
        elif p == "urn:circuit:answer": ans[s] = o
    circ = {}; ref = set()
    for n, t in typ.items():
        if t.endswith("Times"): circ[n] = ("times", tuple(sorted(tin.get(n, ())))); ref |= tin.get(n, set())
        elif t.endswith("Plus"): circ[n] = ("plus", tuple(sorted(feeds.get(n, ())))); ref |= feeds.get(n, set())
    for r in ref: circ.setdefault(r, ("leaf", r))
    return circ, ans

def type_counts(circ):
    times = sum(1 for op, _ in circ.values() if op == "times")
    plus = sum(1 for op, _ in circ.values() if op == "plus")
    leaves = sum(1 for op, _ in circ.values() if op == "leaf")
    edges = sum(len(pl) for op, pl in circ.values() if op in ("times", "plus"))
    return times, plus, leaves, edges

def main():
    # arity = #triple patterns in the query (NPCS writes `arity` tokens per derivation)
    qdir = os.environ.get("WATDIV_QDIR")                 # e.g. pilot/data/official_q_100M
    if qdir:
        QS = [(os.path.basename(f), f, _arity(open(f).read()))
              for f in sorted(glob.glob(f"{qdir}/*.rq") + glob.glob(f"{qdir}/*.sparql"))]
    else:                                                # built-in smoke shapes (base.nt)
        QS = [("S-star", "watdiv/S-star.rq", 3), ("L-path", "watdiv/L-path.rq", 3),
              ("F-snow", "watdiv/F-snow.rq", 4)]
    print(f"{'query':>8} {'ans':>6} {'deriv(⊗)':>9} {'gates':>7} {'edges':>7} | {'build_ms':>8} {'wmc_ms':>7} | "
          f"{'T_str':>7} {'T_circ':>7} {'share':>6} || {'npcs_KB':>7} {'circ_KB':>7}")
    print(f"{'':>8} {'':>6} {'':>9} {'':>7} {'':>7} | {'engine':>8} {'client':>7} | "
          f"{'struct':>7} {'struct':>7} {'struct':>6} || {'(csv)':>7} {'(nt/hash)':>7}")
    for name, qf, arity in QS:
        cons = get_construct(qf)
        build_ms, circ_nt = post(cons, "application/sparql-query", "application/n-triples")
        circ, ans = parse_circuit(circ_nt.decode("utf-8", "replace"))
        times, plus, leaves, edges = type_counts(circ)
        gates_ = len(circ)
        # compile + WMC (sample up to 300 answers to bound time; low-tw so each is cheap)
        roots = list(ans); samp = roots if len(roots) <= 300 else random.sample(roots, 300)
        P = {c[1]: round(random.uniform(0.3, 0.95), 3) for n, c in circ.items() if c[0] == "leaf"}
        t = time.time()
        for r in samp: compile_bdd.probability(circ, r, P)
        wmc_ms = (time.time() - t) * 1000 * (len(roots) / max(len(samp), 1))  # extrapolated to all
        # STRUCTURAL compactness (fair): NPCS writes #derivations x arity token occurrences;
        # circuit stores each distinct gate/edge once. (byte columns are serialization only:
        # NPCS=CSV, circuit=N-Triples with 64-hex SHA256 gate IRIs -> not comparable as bytes.)
        T_str = times * arity
        T_circ = gates_ + edges
        share = T_str / T_circ if T_circ else 0
        npcs = get_npcs(qf)
        _, ndata = post(npcs, "application/sparql-query", "text/csv")
        print(f"{name:>8} {len(ans):>6} {times:>9} {gates_:>7} {edges:>7} | {build_ms:>8.0f} "
              f"{wmc_ms:>7.0f} | {T_str:>7} {T_circ:>7} {share:>5.2f}x || {len(ndata)/1024:>7.1f} {len(circ_nt)/1024:>7.1f}")

if __name__ == "__main__":
    main()
