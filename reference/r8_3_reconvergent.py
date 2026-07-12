"""R8.3 — ProvSQL vs ours on a RECONVERGENT TPC-H query (shared base tokens per answer).

Q3 (G2a) has one 3-token product per answer -> trivial 0.5^3 = 0.125; it tests execution compatibility,
not shared-circuit WMC. This query projects to ?cust:

    SELECT ?cust WHERE { ?cust <c_mktsegment> "BUILDING" . ?order <o_custkey> ?cust }   (tpch/skeletons/Qrecon.rq)

so a building customer with K orders has provenance  (+)_k ( cust (x) order_k )  — the *cust token is
shared* across all K product terms (reconvergent). Correct P = P(cust AND (OR orders)) = 0.5*(1-0.5^K),
which VARIES with K in [0.375, 0.5]. A naive per-answer product-sum  Σ_k P(cust)P(order_k) = 0.25*K
double-counts and exceeds 1 for K>=4 — the case the shared circuit (and ProvSQL) must get right.

Ours: CircuitRewriter naryrel -> shared circuit (cust leaf shared) -> WMC.
ProvSQL: GROUP BY c_custkey with probability(provenance()) (its semiring (+)-aggregates the group).

  LD_LIBRARY_PATH=$CONDA_PREFIX/lib PGHOST=$WS/pgsock PGPORT=54320 python3 r8_3_reconvergent.py
"""
import os, sys, time, subprocess
sys.setrecursionlimit(1_000_000)
import g3_pqe_latency as g3, compile_bdd

GDB = "http://localhost:7200/repositories"
Q = "tpch/skeletons/Qrecon.rq"

def ours(repo):
    circ, ans, cms = g3.construct_bgp(f"{GDB}/{repo}", "naryrel", open(Q).read())
    comp, wmc, n, ok = g3.compile_wmc(circ, ans)
    P = {circ[x][1]: 0.5 for x in circ if circ[x][0] == "leaf"}
    roots = {k: v for v, k in ans.items()}
    ps, naive_gt1 = [], 0
    for key, node in roots.items():
        p, _ = compile_bdd.probability(circ, node, P); ps.append(p)
        k = len(compile_bdd.leaf_order(circ, node)) - 1          # orders (tokens minus shared cust)
        if 0.25 * k > 1.0: naive_gt1 += 1
    return dict(answers=len(ans), construct=round(cms), compile=round(comp), wmc=round(wmc),
                total=round(cms + comp + wmc), pmin=round(min(ps), 5), pmax=round(max(ps), 5),
                valid=ok == n, naive_gt1=naive_gt1)

def provsql(schema):
    sql = (f"SET search_path={schema},public,provsql;\n\\timing on\n"
           f"CREATE TEMP TABLE r AS SELECT c.c_custkey, probability(provenance()) p "
           f"FROM {schema}.customer c,{schema}.orders o "
           f"WHERE o.o_custkey=c.c_custkey AND c.c_mktsegment='BUILDING' GROUP BY c.c_custkey;\n"
           f"\\timing off\nSELECT count(*) FROM r;\n")
    env = dict(os.environ)
    r = subprocess.run([os.path.join(env.get("CONDA_PREFIX", ""), "bin", "psql"), "-d", "provsqltest"],
                       input=sql, capture_output=True, text=True, env=env, timeout=300)
    import re
    ms = [float(m) for m in re.findall(r"Time:\s+([\d.]+)\s+ms", r.stdout)]
    cnt = re.findall(r"^\s*(\d+)\s*$", r.stdout, re.M)
    return dict(total=round(ms[-1]) if ms else None, answers=int(cnt[-1]) if cnt else None)

if __name__ == "__main__":
    for repo, schema, sf in [("tpch001", "g2a", "0.01"), ("tpch01", "g2a1", "0.1")]:
        o = ours(repo); p = provsql(schema)
        print(f"SF{sf}: OURS answers={o['answers']} total={o['total']}ms (construct {o['construct']}+compile "
              f"{o['compile']}+wmc {o['wmc']}) p in [{o['pmin']},{o['pmax']}] valid={o['valid']} "
              f"naive>1 for {o['naive_gt1']}/{o['answers']}  |  PROVSQL answers={p['answers']} total={p['total']}ms")
