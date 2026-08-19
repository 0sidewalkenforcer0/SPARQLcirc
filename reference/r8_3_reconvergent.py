"""R8.3 — ProvSQL vs ours on a RECONVERGENT TPC-H query (shared base tokens per answer).

Q3 (G2a) has one 3-token product per answer -> trivial 0.5^3 = 0.125; it tests execution compatibility,
not shared-circuit WMC. This query projects to ?cust:

    SELECT ?cust WHERE { ?cust <c_mktsegment> "BUILDING" . ?order <o_cust> ?cust }   (tpch/skeletons/Qrecon.rq)

so a building customer with K orders has provenance  (+)_k ( cust (x) order_k )  — the *cust token is
shared* across all K product terms (reconvergent). Correct P = P(cust AND (OR orders)) = 0.5*(1-0.5^K),
which VARIES with K in [0.375, 0.5]. A naive per-answer product-sum  Σ_k P(cust)P(order_k) = 0.25*K
double-counts and exceeds 1 for K>=4 — the case the shared circuit (and ProvSQL) must get right.

Ours: CircuitRewriter naryrel -> shared circuit (cust leaf shared) -> WMC.
ProvSQL: GROUP BY c_custkey with probability_evaluate(provenance()) (its semiring (+)-aggregates the group).

  LD_LIBRARY_PATH=$CONDA_PREFIX/lib PGHOST=$WS/pgsock PGPORT=54320 python3 r8_3_reconvergent.py
"""
import os, sys, subprocess, csv, statistics
sys.setrecursionlimit(1_000_000)
import g3_pqe_latency as g3
from experiment_timeouts import QUERY_TIMEOUT_S

GDB = "http://localhost:7200/repositories"
Q = "tpch/skeletons/Qrecon.rq"
WARMUP = 1
RUNS = int(os.environ.get("R8_3_RUNS", "5"))

import re

def _custkey(answer_key):
    """c_custkey from ours' answer key 'A|cust=<canon-term>' — trailing integer of the cust value (the
    TPC-H customer IRI/literal ends in its custkey). Falls back to the whole key if none."""
    m = re.search(r"(\d+)\D*$", answer_key)
    return m.group(1) if m else answer_key

def ours(repo):
    circ, ans, cms = g3.construct_bgp(f"{GDB}/{repo}", "naryrel", open(Q).read())
    comp, wmc, n, ok, probs = g3.compile_wmc(circ, ans)              # probs = the TIMED SHARED-compile WMC map
    per = {}
    for key, probability in probs.items():                           # keyed by c_custkey (NOT sorted values)
        ck = _custkey(key)
        if ck in per:
            raise ValueError(f"customer-key collision while decoding answer bindings: {ck}")
        per[ck] = probability                                        # keep full precision until final reporting
    return dict(answers=len(probs), construct=cms, compile=comp, wmc=wmc,
                total=cms + comp + wmc, per=per, valid=ok == n)

def _parse_provsql_rows(stdout):
    """Parse explicitly tagged `P|custkey|probability` / `K|custkey|count` rows.

    Tags make the protocol unambiguous even when a probability happens to be exactly 0 or 1; the old
    integer-vs-float inference could misclassify such a probability as an order count.
    """
    p_by, k_by = {}, {}
    for line in stdout.splitlines():
        # tolerate this ProvSQL build's aggregate-provenance display: the probability value may print as
        # "0.375 (*)" with a trailing "|<uuid>" column; capture P/K + custkey + value, ignore the rest.
        m = re.match(r"^([PK])\|(\d+)\|([\d.eE+-]+)", line.strip())
        if not m:
            continue
        kind, ck, raw = m.groups()
        target = p_by if kind == "P" else k_by
        if ck in target:
            raise ValueError(f"duplicate ProvSQL {kind} row for customer {ck}")
        target[ck] = float(raw) if kind == "P" else int(raw)
    return p_by, k_by

def provsql(schema):
    # Per-customer probability AND an INDEPENDENT order count K (so the closed-form check is not circular).
    base = (f"FROM {schema}.customer c,{schema}.orders o "
            f"WHERE o.o_custkey=c.c_custkey AND c.c_mktsegment='BUILDING' GROUP BY c.c_custkey")
    sql = (f"SET statement_timeout='{QUERY_TIMEOUT_S}s';\n"
           f"SET search_path={schema},public,provsql;\n\\timing on\n"
           f"CREATE TEMP TABLE r AS SELECT c.c_custkey, probability_evaluate(provenance()) p {base};\n\\timing off\n"
           f"\\pset format unaligned\n\\pset tuples_only on\n\\pset fieldsep '|'\n"
           f"SELECT 'P', c_custkey, p FROM r ORDER BY c_custkey;\n"
           f"SELECT 'K', c.c_custkey, count(*) k {base} ORDER BY c.c_custkey;\n")  # independent K per customer
    env = dict(os.environ)
    r = subprocess.run([os.path.join(env.get("CONDA_PREFIX", ""), "bin", "psql"), "-d", "provsqltest"],
                       input=sql, capture_output=True, text=True, env=env,
                       timeout=QUERY_TIMEOUT_S)
    if r.returncode != 0:
        raise RuntimeError(f"ProvSQL query failed (exit {r.returncode}): {r.stderr[-500:]}")
    ms = [float(m) for m in re.findall(r"Time:\s+([\d.]+)\s+ms", r.stdout)]      # CREATE TABLE = the real work
    if not ms:
        raise RuntimeError("ProvSQL query produced no timing record")
    p_by, k_by = _parse_provsql_rows(r.stdout)
    return dict(total=ms[0], answers=len(p_by), per=p_by, korder=k_by)

def parity(o, p):
    """RIGOROUS: same customer-key SET, per-customer probability agreement, and a non-circular closed-form
    check using ProvSQL's INDEPENDENT order count K."""
    ok, pk, kk = set(o["per"]), set(p["per"]), set(p.get("korder", {}))
    common = ok & pk
    err = max((abs(o["per"][k] - p["per"][k]) for k in common), default=None)
    all_keys_match = ok == pk == kk
    cf = (max(abs(o["per"][k] - 0.5 * (1 - 0.5 ** p["korder"][k]))                 # K from ProvSQL, not the circuit
              for k in ok) if all_keys_match and ok else None)
    return dict(keys_match=(ok == pk), k_keys_match=(ok == kk),
                only_ours=len(ok - pk), only_provsql=len(pk - ok), missing_k=len(ok - kk), extra_k=len(kk - ok),
                max_abs_error=err, cf_maxerr=cf,
                agree=(o.get("valid", True) and all_keys_match and err is not None and err < 1e-6
                       and cf is not None and cf < 1e-6))

def _r(x, n=9):
    return None if x is None else round(x, n)

def _stat(xs):
    return dict(median=statistics.median(xs), min=min(xs), max=max(xs), mean=statistics.mean(xs),
                sd=statistics.stdev(xs) if len(xs) > 1 else 0.0)

if __name__ == "__main__":
    if RUNS < 1:
        raise RuntimeError("R8_3_RUNS must be >= 1")
    rows = []
    for repo, schema, sf in [("tpch001", "g2a", "0.01"), ("tpch01", "g2a1", "0.1")]:
        ot, oc, okc, ow, pt = [], [], [], [], []
        first_o = first_p = None
        for i in range(WARMUP + RUNS):
            o = ours(repo); p = provsql(schema)
            if o["answers"] != p["answers"]:
                raise RuntimeError(f"SF{sf}: answer-count mismatch ours={o['answers']} provsql={p['answers']}")
            if i == 0:
                first_o, first_p = o, p
            else:
                ot.append(o["total"]); oc.append(o["construct"]); okc.append(o["compile"]); ow.append(o["wmc"])
                pt.append(p["total"])
        o, p = first_o, first_p
        par = parity(o, p)
        if not par["agree"]:
            raise RuntimeError(f"SF{sf}: keyed probability parity failed: {par}")
        os_, ocs, oks, ows, ps = map(_stat, (ot, oc, okc, ow, pt))
        print(f"SF{sf}: OURS answers={o['answers']} total={os_['median']:.0f}ms "
              f"[{os_['min']:.0f}-{os_['max']:.0f}]  |  PROVSQL total={ps['median']:.0f}ms "
              f"[{ps['min']:.0f}-{ps['max']:.0f}]  ({WARMUP} warm-up + {RUNS} timed)")
        print(f"       PARITY ours==ProvSQL per-customer? {par['agree']}  keys_match={par['keys_match']} "
              f"k_keys_match={par['k_keys_match']} (only-ours {par['only_ours']}, only-provsql {par['only_provsql']}, "
              f"missing-K {par['missing_k']}, extra-K {par['extra_k']}) max_abs_error={par['max_abs_error']} "
              f"| ours==closed-form(indep K)? maxerr={par['cf_maxerr']}")
        rows.append(dict(sf=sf, runs=RUNS, ours_answers=o["answers"], provsql_answers=p["answers"],
                         ours_total_median_ms=_r(os_["median"], 3), ours_total_min_ms=_r(os_["min"], 3),
                         ours_total_max_ms=_r(os_["max"], 3), ours_total_sd_ms=_r(os_["sd"], 3),
                         provsql_total_median_ms=_r(ps["median"], 3), provsql_total_min_ms=_r(ps["min"], 3),
                         provsql_total_max_ms=_r(ps["max"], 3), provsql_total_sd_ms=_r(ps["sd"], 3),
                         construct_median_ms=_r(ocs["median"], 3), compile_median_ms=_r(oks["median"], 3),
                         wmc_median_ms=_r(ows["median"], 3),
                         ours_valid=o["valid"], keys_match=par["keys_match"], k_keys_match=par["k_keys_match"],
                         only_ours=par["only_ours"], only_provsql=par["only_provsql"],
                         missing_k=par["missing_k"], extra_k=par["extra_k"],
                         max_abs_error=_r(par["max_abs_error"]), cf_maxerr=_r(par["cf_maxerr"]),
                         agree=par["agree"]))
    out = os.environ.get("R8_3_OUT", "r8_3_reconvergent.csv")   # the keyed-parity evidence file the task needs
    with open(out, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)
    print(f"\nwrote {out}  (sf, answers, timings, keys_match, k_keys_match, max_abs_error, cf_maxerr, agree)")
