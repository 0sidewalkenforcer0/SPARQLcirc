"""E4 - compile+WMC vs treewidth (the compiler-scaling figure), memory-safe.

Sweeps the treewidth-controlled families from gen_families, builds each answer's
provenance circuit with the FACTORED builder (factor.py; polynomial in tw -- the
flat gamma builder is ~2^depth), exports the weighted CNF, and compiles it with d4
to a d-DNNF -- recording d-DNNF size vs our OBDD size against (n, tw).

Prediction (EVALUATION.md E4): bounded tw -> d-DNNF linear in n while OBDD is
n^{O(tw)}; growing tw -> both blow up (2^{Theta(tw)}). The blow-up is REAL and would
OOM the box, so each instance runs in a subprocess under an address-space cap
(E4_MEM_GB, default 8) + the canonical 120 s compilation timeout; an instance
that exceeds either is recorded as OOM / timeout -- that ceiling IS the data point.

Run from reference/ with the sparqlcirc env active:
    D4=/path/to/d4 LD_LIBRARY_PATH=$CONDA_PREFIX/lib python3 e4_sweep.py
Writes watdiv/e4_results.csv. Progress via tqdm; per-instance lines via tqdm.write.
"""
import os, re, csv, subprocess, random, resource, time
import multiprocessing as mp
import gen_families, factor, gates, export_cnf, compile_bdd
import d4_pipeline as d4p
from experiment_timeouts import COMPILE_TIMEOUT_S, CompilationTimeout, compilation_timeout
try:
    from tqdm import tqdm
except ImportError:                                   # graceful fallback if tqdm absent
    def tqdm(x, **k): return x
    tqdm.write = staticmethod(lambda *a, **k: print(*a))

MEM_CAP = int(os.environ.get("E4_MEM_GB", "8")) * 1024 ** 3
TIMEOUT = COMPILE_TIMEOUT_S
D4 = os.environ.get("D4", "d4")
CNFDIR = os.environ.get("E4_CNFDIR", "/tmp/e4cnf")
os.makedirs(CNFDIR, exist_ok=True)

def parse_ttl(ttl):
    data = {}
    for m in re.finditer(r":(\w+)\s+rdf:subject\s+:(\S+)\s*;\s*rdf:predicate\s+:(\S+)\s*;\s*rdf:object\s+:(\S+)\s*\.", ttl):
        tok, s, p, o = m.groups()
        data[tok] = (s, p, o)
    return data

def parse_query(q):
    proj = re.search(r"SELECT\s+(.+?)\s+WHERE", q, re.S).group(1).split()
    body = q[q.find("{") + 1: q.rfind("}")]
    pats = []
    for stmt in body.split("."):
        parts = stmt.split()
        if len(parts) == 3:
            s, p, o = (t[1:] if t.startswith(":") else t for t in parts)
            pats.append((s, p, o))
    return proj, pats

def _worker(ttl, q, meta, qout):
    """Runs in a child process under a hard address-space cap so a compile blow-up
    raises MemoryError (or dies) here, never in the parent driver. d4 (d-DNNF) runs
    FIRST and posts a partial result, THEN the pure-Python OBDD -- so if the OBDD
    blows up/times out we still keep the d-DNNF size (d-DNNF compiles where OBDD can't)."""
    try:
        resource.setrlimit(resource.RLIMIT_AS, (MEM_CAP, MEM_CAP))
        data = parse_ttl(ttl); proj, pats = parse_query(q)
        c = gates.Circuit()
        table = factor.factored_bgp(c, pats, data, set(proj))
        if not table:
            qout.put({"status": "no-answers"}); return
        root = next(iter(table.values()))
        P = {c.gates[g][1]: round(random.uniform(0.3, 0.9), 3) for g in c.gates if c.gates[g][0] == "leaf"}
        e = export_cnf.export(c.gates, root, P)
        cnf = os.path.join(CNFDIR, meta["name"] + ".cnf"); open(cnf, "w").write(e["dimacs"])
        nnf = cnf + ".nnf"
        base = {"cnf_vars": e["nvars"], "factored_gates": len(c.gates)}
        # (1) d4 d-DNNF first (fast, memory-light)
        try:
            subprocess.run(d4p.ddnnf_cmd(cnf, nnf), check=True, capture_output=True, timeout=TIMEOUT)
            import ddnnf_wmc
            iw = {e["var_of"][n]: (P[pl], 1.0 - P[pl])
                  for n, (op, pl) in c.gates.items() if op == "leaf" and n in e["var_of"]}
            ev = ddnnf_wmc.evaluate_file(nnf, iw)
            ddnnf_nodes, d4wmc = ev.nodes, ev.probability
        except Exception:
            ddnnf_nodes, d4wmc = None, None
        base.update({"ddnnf_nodes": ddnnf_nodes, "d4_wmc": round(d4wmc, 6) if d4wmc is not None else None,
                     "rss_d4_mib": resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss // 1024})  # d4 peak
        qout.put({**base, "status": "partial", "obdd_size": None})     # kept if OBDD later dies
        # (2) OBDD (pure Python; the part that blows up on high tw / large n)
        with compilation_timeout(TIMEOUT):
            prob, obdd_size = compile_bdd.probability(c.gates, root, P)[:2]
        qout.put({**base, "status": "ok", "obdd_size": obdd_size, "expected": round(prob, 6),
                  "rss_mib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024,  # our OBDD builder peak
                  "match": d4wmc is not None and abs(d4wmc - prob) < 1e-6})
    except CompilationTimeout:
        qout.put({"status": "obdd-timeout"})
    except MemoryError:
        qout.put({"status": "OOM-obdd"})
    except Exception as ex:
        qout.put({"status": f"err:{type(ex).__name__}"})

def run_instance(family, ttl, q, meta):
    import queue as _q
    qout = mp.Queue()
    p = mp.Process(target=_worker, args=(ttl, q, meta, qout))
    # Operational watchdog for two sequential compiler attempts (d4 then OBDD). Each attempt has its own
    # canonical TIMEOUT above; the extra 30 s only covers process startup/result delivery.
    t0 = time.time(); p.start(); p.join(2 * TIMEOUT + 30)
    killed = p.is_alive()
    if killed:
        p.terminate(); p.join()
    items = []                                        # drain: worker posts partial (d4) then full (OBDD)
    while True:
        try: items.append(qout.get(timeout=1))
        except _q.Empty: break
    if items:
        r = {}
        for it in items: r.update(it)                 # merge: base+d-DNNF (partial) then OBDD/OOM status
        if r.get("status") == "partial":              # OBDD didn't finish; keep the d-DNNF we got
            r["status"] = "obdd-timeout" if killed else "obdd-oom"
    else:
        r = {"status": "timeout" if killed else "OOM/killed"}
    r.update({"family": family, "name": meta["name"], "n_tokens": meta["tokens"],
              "tw": meta["tw"], "deriv": str(meta["deriv"]), "secs": round(time.time() - t0, 1)})
    return r

# Bigger for the overnight curve (set E4_QUICK=1 for the short version). The growing-tw
# families intentionally hit the memory/time cap -- that ceiling is the tractability wall.
if os.environ.get("E4_QUICK"):
    SWEEPS = [
        ("chain_tw1",        [gen_families.chain(n) for n in (4, 16, 64, 128)]),
        ("bounded_tw2",      [gen_families.layered(d, 2) for d in (2, 8, 16, 32)]),
        ("growing_tw_layer", [gen_families.layered(4, w) for w in (2, 4, 6, 7)]),
        ("growing_tw_grid",  [gen_families.grid(k) for k in (2, 3, 4, 5)]),
    ]
else:
    SWEEPS = [
        ("chain_tw1",        [gen_families.chain(n) for n in (4, 8, 16, 32, 64, 128, 256, 512)]),
        ("bounded_tw2",      [gen_families.layered(d, 2) for d in (2, 4, 8, 16, 24, 32, 48, 64)]),
        ("bounded_tw3",      [gen_families.layered(d, 3) for d in (2, 3, 4, 6, 8, 10, 12)]),
        ("growing_tw_layer", [gen_families.layered(4, w) for w in (2, 3, 4, 5, 6, 7, 8)]),
        ("growing_tw_grid",  [gen_families.grid(k) for k in (2, 3, 4, 5, 6)]),
    ]
COLS = ["family", "name", "n_tokens", "tw", "deriv", "status", "secs",
        "factored_gates", "obdd_size", "ddnnf_nodes", "cnf_vars", "d4_wmc", "expected", "match",
        "rss_mib", "rss_d4_mib"]

def main():
    instances = [(fam, ttl, q, meta) for fam, insts in SWEEPS for (ttl, q, meta) in insts]
    rows = []
    hdr = f"{'family':<18}{'name':<15}{'n':>5}{'tw':>4}{'status':>10}{'secs':>6}{'OBDD':>9}{'dDNNF':>8}{'chk':>4}"
    tqdm.write(hdr); tqdm.write("-" * len(hdr))
    for fam, ttl, q, meta in tqdm(instances, desc="E4 compile sweep", unit="inst"):
        r = run_instance(fam, ttl, q, meta)
        rows.append(r)
        tqdm.write(f"{r['family']:<18}{r['name']:<15}{r['n_tokens']:>5}{str(r['tw']):>4}"
                   f"{r['status']:>10}{r['secs']:>6}{str(r.get('obdd_size','-')):>9}"
                   f"{str(r.get('ddnnf_nodes','-')):>8}{('OK' if r.get('match') else '-'):>4}")
    out = "watdiv/e4_results.csv"
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLS, extrasaction="ignore"); w.writeheader(); w.writerows(rows)
    ok = sum(1 for r in rows if r["status"] == "ok")
    print(f"\nwrote {out}  ({ok}/{len(rows)} compiled; rest OOM/timeout = the tractability wall).")

if __name__ == "__main__":
    main()
