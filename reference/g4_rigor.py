"""G4 — statistical-rigor pass on the HEADLINE timing numbers (ROUND 7 refinement of the perf claims).

The external review flagged that our headline *timings* were single-run on a shared box. G4 re-measures
them under a fixed protocol: **1 warm-up + 5 timed runs**, report **median + min/max + mean ± sd**, a
uniform **300 s** timeout, and a **logged environment** (hardware / heap / concurrent jobs / cache note).
Functional & size results (byte-identity, WMC==PWE, circuit sizes) are NOT re-run — only timings.

Targets = the numbers a reader would cite:
  - G3 end-to-end PQE latency: watdiv-Sstar, tpch-Q3 (flagship), wikidata-WDpath (2.13 B, property path)
  - G2a ProvSQL Q3 (SF 0.01) — the strong-baseline head-to-head
Each ours-row is construct -> shared compile (ROBDD) -> WMC (the G3 pipeline, reused verbatim).

  D4=.../d4 LD_LIBRARY_PATH=$CONDA_PREFIX/lib PGHOST=$WS/pgsock PGPORT=54320 python3 g4_rigor.py
"""
import os, sys, time, subprocess, statistics, csv, re
sys.setrecursionlimit(1_000_000)
import g3_pqe_latency as g3
import e3_run

WARMUP = 1
RUNS   = int(os.environ.get("G4_RUNS", "5"))
GDB    = "http://localhost:7200/repositories"
TIMEOUT = 300

def stat(xs):
    xs = [x for x in xs if x is not None]
    if not xs: return None
    return dict(median=statistics.median(xs), min=min(xs), max=max(xs),
                mean=statistics.mean(xs), sd=(statistics.stdev(xs) if len(xs) > 1 else 0.0), n=len(xs))

def sh(c):
    try: return subprocess.run(c, shell=True, capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception: return "?"

def env_log():
    host = sh("hostname"); cores = sh("nproc")
    mem  = sh("grep -E 'MemTotal|MemAvailable' /proc/meminfo").replace("\n", "  ")
    load = sh("cat /proc/loadavg")
    heap = sh("ps aux | grep -oE 'Xm[sx][0-9]+[gm]' | sort -u").replace("\n", " ")
    conc = sh("ps -eo pcpu,user,comm --sort=-pcpu | awk 'NR>1 && $1>3' | head -4").replace("\n", " ; ")
    print("# G4 environment")
    print(f"#   host      : {host}")
    print(f"#   cpu cores : {cores}")
    print(f"#   mem       : {mem}")
    print(f"#   loadavg   : {load}")
    print(f"#   gdb heap  : {heap}")
    print(f"#   concurrent (top cpu >3%): {conc}")
    print(f"#   protocol  : {WARMUP} warm-up + {RUNS} timed; median + min/max + mean+/-sd; {TIMEOUT}s timeout")
    print(f"#   cache     : warm (repos loaded, daemons up); NOT a cold-start measurement")
    print()

def ours_runs(name, ep, scheme, qf, is_path):
    """1 warm-up + RUNS timed runs of the full G3 pipeline. Returns per-stage lists + answers."""
    construct, compile_, wmc, total, answers = [], [], [], [], None
    for i in range(WARMUP + RUNS):
        try:
            if is_path: circ, ans, cms = g3.construct_path(ep, qf)
            else:       circ, ans, cms = g3.construct_bgp(ep, scheme, open(qf).read())
            comp, w, n, ok, _ = g3.compile_wmc(circ, ans)
        except Exception as ex:
            print(f"  {name} run {i}: {type(ex).__name__}: {ex}"); return None
        if i >= WARMUP:
            construct.append(cms); compile_.append(comp); wmc.append(w); total.append(cms + comp + w)
        answers = len(ans)
    return dict(answers=answers, construct=stat(construct), compile=stat(compile_),
                wmc=stat(wmc), total=stat(total))

def provsql_runs(schema, mktseg="BUILDING"):
    """Time ProvSQL Q3 probability() in ONE psql session: WARMUP+RUNS repeats, parse 'Time: X ms'."""
    sql = f"SET search_path={schema},public,provsql;\n\\timing on\n"
    q = (f"SELECT count(*) FROM (SELECT o.o_orderkey, l.l_linenumber, probability(provenance()) p "
         f"FROM {schema}.customer c,{schema}.orders o,{schema}.lineitem l "
         f"WHERE o.o_custkey=c.c_custkey AND l.l_orderkey=o.o_orderkey AND c.c_mktsegment='{mktseg}') s;\n")
    sql += q * (WARMUP + RUNS)
    env = dict(os.environ)
    r = subprocess.run([os.path.join(env.get("CONDA_PREFIX", ""), "bin", "psql"), "-d", "provsqltest"],
                       input=sql, capture_output=True, text=True, env=env, timeout=TIMEOUT * (WARMUP + RUNS))
    times = [float(m) for m in re.findall(r"Time:\s+([\d.]+)\s+ms", r.stdout)]
    if len(times) < WARMUP + RUNS:
        print(f"  provsql: only {len(times)} timings parsed\n{r.stderr[-300:]}"); return None
    return dict(total=stat(times[WARMUP:WARMUP + RUNS]))

def fmt(s):
    if not s: return "  --  "
    return f"{s['median']:.0f} [{s['min']:.0f}-{s['max']:.0f}]"

def main():
    env_log()
    print(f"{'query':20} {'answers':>7} {'construct_ms med[min-max]':>26} {'compile_ms':>16} {'wmc_ms':>13} {'TOTAL_ms':>16}")
    rows = []
    OURS = [
        ("watdiv-Sstar",    f"{GDB}/watdiv",  "Standard", "engines/bound/S-star.rq", False),
        ("tpch-Q3",         f"{GDB}/tpch001", "naryrel",  "tpch/skeletons/Q3.rq",    False),
        ("wikidata-WDpath", f"{GDB}/wdpaths", "Standard", "wikidata/WD-path.rq",     True),
    ]
    for name, ep, scheme, qf, is_path in OURS:
        if not os.path.exists(qf): print(f"  {name}: {qf} missing"); continue
        r = ours_runs(name, ep, scheme, qf, is_path)
        if not r: continue
        print(f"{name:20} {r['answers']:>7} {fmt(r['construct']):>26} {fmt(r['compile']):>16} {fmt(r['wmc']):>13} {fmt(r['total']):>16}")
        for stage in ("construct", "compile", "wmc", "total"):
            s = r[stage]
            if s: rows.append(dict(system="ours", query=name, stage=stage, answers=r["answers"],
                                   median_ms=round(s["median"], 1), min_ms=round(s["min"], 1),
                                   max_ms=round(s["max"], 1), mean_ms=round(s["mean"], 1),
                                   sd_ms=round(s["sd"], 1), runs=s["n"]))
    # ProvSQL Q3 (SF0.01 = schema g2a) — the strong baseline
    p = provsql_runs("g2a")
    if p:
        print(f"{'provsql-Q3 (SF0.01)':20} {'14908':>7} {'--':>26} {'--':>16} {'--':>13} {fmt(p['total']):>16}")
        s = p["total"]
        rows.append(dict(system="provsql", query="tpch-Q3", stage="total", answers=14908,
                         median_ms=round(s["median"], 1), min_ms=round(s["min"], 1), max_ms=round(s["max"], 1),
                         mean_ms=round(s["mean"], 1), sd_ms=round(s["sd"], 1), runs=s["n"]))
    with open("g4_rigor.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)
    print(f"\nwrote g4_rigor.csv  |  {WARMUP} warm-up + {RUNS} timed; median [min-max] ms.")

if __name__ == "__main__":
    main()
