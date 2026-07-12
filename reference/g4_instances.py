"""G4 (b) — ≥3-5 query INSTANCES per shape (the breadth the ROUND 7 G4 refinement asked for, on top of
the 5-run median±sd of g4_rigor.py). We vary the TPC-H Q3 SPJ shape across all 5 mktsegments — 5 distinct
instances — on BOTH ours (construct->compile->WMC) and ProvSQL, each 1 warm-up + 5 timed runs. Reports
per-instance median [min-max] and the cross-instance mean±sd (does the latency hold across instances, not
just runs?). Post-1e67021 jar. `g4_instances.csv`.

  D4=.. LD_LIBRARY_PATH=$CONDA_PREFIX/lib PGHOST=$WS/pgsock PGPORT=54320 python3 g4_instances.py
"""
import os, re, sys, statistics, csv
sys.setrecursionlimit(1_000_000)
import g3_pqe_latency as g3
import g4_rigor
import e3_run

GDB = "http://localhost:7200/repositories"
SEGMENTS = ["AUTOMOBILE", "BUILDING", "FURNITURE", "HOUSEHOLD", "MACHINERY"]
# 5 WatDiv users that satisfy the S-star pattern (likes/subscribes/makesPurchase) — 2nd shape, 5 instances.
STAR_USERS = ["User10113", "User1011", "User10152", "User10163", "User10252"]
WARMUP, RUNS = 1, 5

def st(xs):
    return dict(median=statistics.median(xs), min=min(xs), max=max(xs),
                mean=statistics.mean(xs), sd=(statistics.stdev(xs) if len(xs) > 1 else 0.0))

def ours_end_to_end(ep, scheme, q):
    """1 warm-up + RUNS timed full-pipeline runs; returns (answers, stats-over-totals)."""
    totals, ans_n = [], None
    for i in range(WARMUP + RUNS):
        circ, ans, cms = g3.construct_bgp(ep, scheme, q)
        comp, wmc, n, ok, _ = g3.compile_wmc(circ, ans)
        if i >= WARMUP: totals.append(cms + comp + wmc)
        ans_n = len(ans)
    return ans_n, st(totals)

def ours_instance(seg):
    q = open("tpch/skeletons/Q3.rq").read().replace('"BUILDING"', f'"{seg}"')
    return ours_end_to_end(f"{GDB}/tpch001", "naryrel", q)

def star_instance(user):
    q = re.sub(r"User\d+", user, open("engines/bound/S-star.rq").read())
    return ours_end_to_end(f"{GDB}/watdiv", "Standard", q)

def main():
    print("G4(b) — TPC-H Q3 SPJ across 5 mktsegment instances; ours + ProvSQL; 1 warm-up + 5 runs\n")
    print(f"{'instance (segment)':20} {'answers':>7} {'ours total ms med[min-max]':>28} {'ProvSQL ms med[min-max]':>25}")
    rows, ours_meds, prov_meds = [], [], []
    for seg in SEGMENTS:
        try:
            n, o = ours_instance(seg)
        except Exception as ex:
            raise RuntimeError(f"{seg}: ours failed") from ex
        p = g4_rigor.provsql_runs("g2a", seg)
        pt = p["total"]
        ours_meds.append(o["median"]);  prov_meds.append(pt["median"])
        of = f"{o['median']:.0f} [{o['min']:.0f}-{o['max']:.0f}]"
        pf = f"{pt['median']:.0f} [{pt['min']:.0f}-{pt['max']:.0f}]"
        print(f"{seg:20} {n:>7} {of:>28} {pf:>25}")
        rows.append(dict(shape="tpch-Q3-SPJ", instance=seg, answers=n,
                         ours_median_ms=round(o["median"], 1), ours_min_ms=round(o["min"], 1),
                         ours_max_ms=round(o["max"], 1), ours_sd_ms=round(o["sd"], 1),
                         provsql_median_ms=round(pt["median"], 1),
                         provsql_sd_ms=round(pt["sd"], 1), runs=RUNS))
    # cross-instance summary
    om = st(ours_meds); pm = st([x for x in prov_meds if x is not None])
    print(f"\ncross-instance (n={len(ours_meds)} instances):")
    print(f"  ours    median-of-medians {om['median']:.0f} ms, mean {om['mean']:.0f} ± {om['sd']:.0f} (min {om['min']:.0f}, max {om['max']:.0f})")
    print(f"  ProvSQL median-of-medians {pm['median']:.0f} ms, mean {pm['mean']:.0f} ± {pm['sd']:.0f} (min {pm['min']:.0f}, max {pm['max']:.0f})")

    # 2nd shape: WatDiv S-star across 5 user instances (ours only)
    print(f"\n{'S-star instance (user)':22} {'answers':>7} {'ours total ms med[min-max]':>28}")
    star_meds = []
    for u in STAR_USERS:
        try:
            n, o = star_instance(u)
        except Exception as ex:
            raise RuntimeError(f"{u}: failed") from ex
        star_meds.append(o["median"])
        of = f"{o['median']:.0f} [{o['min']:.0f}-{o['max']:.0f}]"
        print(f"{u:22} {n:>7} {of:>28}")
        rows.append(dict(shape="watdiv-Sstar", instance=u, answers=n,
                         ours_median_ms=round(o["median"], 1), ours_min_ms=round(o["min"], 1),
                         ours_max_ms=round(o["max"], 1), ours_sd_ms=round(o["sd"], 1),
                         provsql_median_ms=None, provsql_sd_ms=None, runs=RUNS))
    if star_meds:
        sm = st(star_meds)
        print(f"  cross-instance (n={len(star_meds)}): median-of-medians {sm['median']:.0f} ms, "
              f"mean {sm['mean']:.0f} ± {sm['sd']:.0f} (min {sm['min']:.0f}, max {sm['max']:.0f})")

    expected_rows = len(SEGMENTS) + len(STAR_USERS)
    if len(rows) != expected_rows:
        raise RuntimeError(f"refusing partial G4-instances CSV: {len(rows)}/{expected_rows} rows")
    with open("g4_instances.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)
    print("\nwrote g4_instances.csv")

if __name__ == "__main__":
    main()
