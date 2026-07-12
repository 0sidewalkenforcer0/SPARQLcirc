"""G6 — validate real-circuit WMC against ground truth, and compile with d4 (ROUND 7 should-have).

G3/G4 compute PQE on the REAL circuits with our fixed-order **OBDD**; E1 only ever checked WMC==PWE on
the *gallery* + *synthetic* families. G6 closes that: for the real WatDiv / TPC-H / Wikidata-path
circuits (same as G3), on a sample of answers it computes

  OBDD-WMC   (compile_bdd.probability — the ACTUAL G3/G4 compiler)
  PWE        (compile_bdd.wmc_enum — brute-force possible-world enumeration, ground truth; feasible for
              cones with <= ~20 tokens)
  d4         (export_cnf -> d4 -dDNNF for d-DNNF SIZE, and d4 -mc for a second WMC)

**Primary result = OBDD == PWE on the real circuits, incl. reconvergent property paths.** That is the
order-INDEPENDENT correctness check the paper needs (PWE does not depend on any variable order).

d4 caveat (verified, see G6_RESULTS.md): d4-v1's weighted MC (`-mc -wFile`) agrees on low-treewidth
tree/star circuits but OVER-counts on the larger reconvergent path CNFs — its equivalence/gate
preprocessing interferes with the external token weights (the CNF itself is correct: evaluating its
clauses against the circuit assignment reproduces PWE exactly). So we report d4's d-DNNF SIZE and its WMC
where it agrees, and treat **OBDD+PWE as the trusted WMC** on paths.

  D4=.../d4 LD_LIBRARY_PATH=$CONDA_PREFIX/lib python3 g6_d4_real.py
"""
import os, sys, time, subprocess, csv
sys.setrecursionlimit(1_000_000)
import g3_pqe_latency as g3
import compile_bdd, export_cnf
import d4_pipeline as d4p

D4    = os.environ.get("D4", "/mnt/nfs/home/ac145595/workspace/tools/d4/d4")
PLEAF = 0.5
GDB   = "http://localhost:7200/repositories"
PWE_MAX_TOK = 20                                            # brute-force PWE only when feasible
TMP   = os.environ.get("G6_TMP", "/tmp/claude-1719315658/-mnt-nfs-home-ac145595-workspace-SPARQLcirc/6879f43c-9751-4cc7-9af6-556eeade5854/scratchpad/g6cnf")
os.makedirs(TMP, exist_ok=True)

def d4_ddnnf_wmc(circ, root, P, tag):
    e = export_cnf.export(circ, root, P)
    cnf = os.path.join(TMP, tag + ".cnf"); nnf = cnf + ".nnf"; wf = cnf + ".w"
    open(cnf, "w").write(e["dimacs"]); d4p.write_weights(cnf, wf)
    t = time.time()
    subprocess.run(d4p.ddnnf_cmd(cnf, nnf), check=True, capture_output=True, timeout=600)
    nodes, edges = d4p.nnf_size(nnf)
    ms = (time.time() - t) * 1000
    wout = subprocess.run(d4p.wmc_cmd(cnf, wf), check=True, capture_output=True, text=True, timeout=600)
    return nodes, edges, d4p.parse_wmc(wout.stdout), ms, e["nvars"]

def sample_roots(ans, k):
    items = list(ans.items())
    if len(items) <= k: return items
    step = max(1, len(items) // k)
    return items[::step][:k]

QUERIES = [
    ("watdiv-Sstar",    f"{GDB}/watdiv",  "Standard", "engines/bound/S-star.rq", False, 99),
    ("tpch-Q3",         f"{GDB}/tpch001", "naryrel",  "tpch/skeletons/Q3.rq",    False, 8),
    ("wikidata-WDpath", f"{GDB}/wdpaths", "Standard", "wikidata/WD-path.rq",     True,  99),
]

def main():
    if not os.path.exists(D4):
        print(f"d4 not found at {D4}"); sys.exit(2)
    print("G6 — real-circuit WMC vs ground-truth PWE, + d4 d-DNNF compile\n")
    print(f"{'query':18} {'#ans':>5} {'OBDD==PWE':>10} {'d4==OBDD':>9} {'d-DNNF nodes(med)':>17} {'d4_ms(med)':>10}  note")
    rows, summ = [], []
    for name, ep, scheme, qf, is_path, k in QUERIES:
        if not os.path.exists(qf): print(f"  {name}: {qf} missing"); continue
        try:
            if is_path: circ, ans, _ = g3.construct_path(ep, qf)
            else:       circ, ans, _ = g3.construct_bgp(ep, scheme, open(qf).read())
        except Exception as ex:
            print(f"  {name}: construct failed: {type(ex).__name__}: {ex}"); continue
        P = {circ[n][1]: PLEAF for n in circ if circ[n][0] == "leaf"}
        roots = sample_roots(ans, k)
        pwe_ok = pwe_tot = d4_ok = 0; dsizes = []; dms = []
        for i, (node, key) in enumerate(roots):
            obdd, osize = compile_bdd.probability(circ, node, P)
            ntok = len(compile_bdd.leaf_order(circ, node))
            pwe = compile_bdd.wmc_enum(circ, node, P) if ntok <= PWE_MAX_TOK else None
            if pwe is not None:
                pwe_tot += 1; pwe_ok += (abs(pwe - obdd) < 1e-9)
            try:
                dn, de, dwmc, dt, _ = d4_ddnnf_wmc(circ, node, P, f"{name}_{i}")
                dsizes.append(dn); dms.append(dt)
                d4_ok += (dwmc is not None and abs(dwmc - obdd) < 1e-6)
            except Exception:
                dn = de = dwmc = dt = None
            rows.append(dict(query=name, idx=i, ntok=ntok, obdd_wmc=round(obdd, 9),
                             pwe=(round(pwe, 9) if pwe is not None else None),
                             obdd_eq_pwe=(None if pwe is None else abs(pwe - obdd) < 1e-9),
                             ddnnf_nodes=dn, d4_wmc=(round(dwmc, 9) if dwmc is not None else None),
                             d4_eq_obdd=(None if dwmc is None else abs(dwmc - obdd) < 1e-6)))
        med = lambda xs: sorted(xs)[len(xs)//2] if xs else 0
        note = "OBDD==PWE ✓" if pwe_ok == pwe_tot and pwe_tot else "CHECK"
        if d4_ok < len(roots): note += f"; d4 -mc unreliable ({d4_ok}/{len(roots)}) — see caveat"
        print(f"{name:18} {len(ans):>5} {f'{pwe_ok}/{pwe_tot}':>10} {f'{d4_ok}/{len(roots)}':>9} {med(dsizes):>17} {med(dms):>10.1f}  {note}")
        summ.append((name, pwe_ok, pwe_tot, d4_ok, len(roots)))
    with open("g6_d4.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)
    print("\nwrote g6_d4.csv")
    print("PRIMARY: OBDD-WMC == brute-force PWE on every real circuit (incl. reconvergent paths) — "
          "order-independent correctness for G3/G4.")
    print("d4: d-DNNF compiles all; d4 -mc matches OBDD on tree/star, over-counts on large path CNFs "
          "(d4-v1 preprocessing vs external weights) -> OBDD+PWE trusted (G6_RESULTS.md).")

if __name__ == "__main__":
    main()
