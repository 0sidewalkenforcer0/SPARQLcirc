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

import re

def _custkey(answer_key):
    """c_custkey from ours' answer key 'A|cust=<canon-term>' — trailing integer of the cust value (the
    TPC-H customer IRI/literal ends in its custkey). Falls back to the whole key if none."""
    m = re.search(r"(\d+)\D*$", answer_key)
    return m.group(1) if m else answer_key

def ours(repo):
    circ, ans, cms = g3.construct_bgp(f"{GDB}/{repo}", "naryrel", open(Q).read())
    comp, wmc, n, ok, probs = g3.compile_wmc(circ, ans)              # probs = the TIMED SHARED-compile WMC map
    per = {_custkey(key): round(p, 6) for key, p in probs.items()}   # keyed by c_custkey (NOT sorted values)
    return dict(answers=len(probs), construct=round(cms), compile=round(comp), wmc=round(wmc),
                total=round(cms + comp + wmc), per=per, valid=ok == n)

def provsql(schema):
    # Per-customer probability AND an INDEPENDENT order count K (so the closed-form check is not circular).
    base = (f"FROM {schema}.customer c,{schema}.orders o "
            f"WHERE o.o_custkey=c.c_custkey AND c.c_mktsegment='BUILDING' GROUP BY c.c_custkey")
    sql = (f"SET search_path={schema},public,provsql;\n\\timing on\n"
           f"CREATE TEMP TABLE r AS SELECT c.c_custkey, probability(provenance()) p {base};\n\\timing off\n"
           f"\\pset format unaligned\n\\pset tuples_only on\n\\pset fieldsep '|'\n"
           f"SELECT c_custkey, p FROM r ORDER BY c_custkey;\n"
           f"SELECT c.c_custkey, count(*) k {base} ORDER BY c.c_custkey;\n")   # independent K per customer
    env = dict(os.environ)
    r = subprocess.run([os.path.join(env.get("CONDA_PREFIX", ""), "bin", "psql"), "-d", "provsqltest"],
                       input=sql, capture_output=True, text=True, env=env, timeout=300)
    ms = [float(m) for m in re.findall(r"Time:\s+([\d.]+)\s+ms", r.stdout)]      # CREATE TABLE = the real work
    p_by, k_by = {}, {}
    for line in r.stdout.splitlines():                              # p rows are non-integer floats; K rows are ints
        m = re.match(r"^(\d+)\|([\d.eE+-]+)$", line.strip())
        if not m: continue
        ck, val = m.group(1), float(m.group(2))
        (k_by if val == int(val) else p_by)[ck] = (int(val) if val == int(val) else round(val, 6))
    return dict(total=round(ms[0]) if ms else None, answers=len(p_by), per=p_by, korder=k_by)

def parity(o, p):
    """RIGOROUS: same customer-key SET, per-customer probability agreement, and a non-circular closed-form
    check using ProvSQL's INDEPENDENT order count K."""
    ok, pk = set(o["per"]), set(p["per"])
    common = ok & pk
    err = max((abs(o["per"][k] - p["per"][k]) for k in common), default=None)
    cf = max((abs(o["per"][k] - 0.5 * (1 - 0.5 ** p["korder"][k]))                 # K from ProvSQL, not the circuit
              for k in o["per"] if k in p.get("korder", {})), default=None)
    return dict(keys_match=(ok == pk), only_ours=len(ok - pk), only_provsql=len(pk - ok),
                max_abs_error=err, cf_maxerr=cf,
                agree=(ok == pk and err is not None and err < 1e-6 and cf is not None and cf < 1e-6))

if __name__ == "__main__":
    for repo, schema, sf in [("tpch001", "g2a", "0.01"), ("tpch01", "g2a1", "0.1")]:
        o = ours(repo); p = provsql(schema); par = parity(o, p)
        print(f"SF{sf}: OURS answers={o['answers']} total={o['total']}ms (construct {o['construct']}+compile "
              f"{o['compile']}+wmc {o['wmc']})  |  PROVSQL answers={p['answers']} total={p['total']}ms")
        print(f"       PARITY ours==ProvSQL per-customer? {par['agree']}  keys_match={par['keys_match']} "
              f"(only-ours {par['only_ours']}, only-provsql {par['only_provsql']}) max_abs_error={par['max_abs_error']} "
              f"| ours==closed-form(indep K)? maxerr={par['cf_maxerr']}")
