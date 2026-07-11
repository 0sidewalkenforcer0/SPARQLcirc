"""E7 - head-to-head: ProvSQL (modified PostgreSQL) vs SPARQL_circ (unmodified engine),
on the SAME data + SAME per-triple probabilities + SAME query. Both compute the EXACT
answer probability by knowledge compilation; E7 reports time/space to that probability and
the qualitative axis that is our actual contribution: no engine modification.

For each instance:
  ProvSQL side  : load (s,p,o) rows into `e7t`, add_provenance (one gate/row = our token),
                  set_prob per row, run the BGP as a self-join with GROUP BY, and read
                  probability(provenance()) -- ProvSQL builds + compiles its circuit in-DB.
  SPARQL_circ   : build the shared provenance circuit with the Python reference (gamma) and
                  weighted-model-count each answer (compile_bdd) -- the same Boolean function.
Then assert the probabilities MATCH (parity) and tabulate time + circuit size.

Prereqs: the ProvSQL PostgreSQL running; env PGHOST/PGPORT/PGDATABASE (default provsqltest).
Run from provsql/ with the sparqlcirc env active. Reference modules are imported from ../reference.
"""
import os, sys, time, random
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "reference"))
import psycopg2                      # provided by conda 'psycopg2' or 'psycopg'
import gates, gamma, compile_bdd
try:
    from tqdm import tqdm
except ImportError:
    def tqdm(x, **k): return x
    tqdm.write = staticmethod(lambda *a, **k: print(*a))

random.seed(11)
PGDB = os.environ.get("PGDATABASE", "provsqltest")

# ---- instances: (name, triples[(tok,s,p,o)], patterns[(s,p,o)], proj[vars]) ----
def drug():
    E = [("p1","Aspirin","iw","Warfarin"),("p2","Warfarin","iw","Metformin"),
         ("p3","Metformin","iw","Omeprazole"),("p4","Aspirin","iw","Ibuprofen"),
         ("p5","Ibuprofen","iw","Metformin"),("p6","Warfarin","iw","Lisinopril"),
         ("p7","Lisinopril","iw","Clopidogrel"),("p8","Clopidogrel","iw","Aspirin")]
    pats = [("Aspirin","iw","?x"),("?x","iw","?y"),("?y","iw","?z")]
    return ("drug-3hop", E, pats, ["?z"])

def star(nusers, deg):
    """WatDiv-star shape: users who like+subscribe (2-pattern star), sharing users -> ans provenance."""
    E, tid = [], 0
    for u in range(nusers):
        for k in range(deg):
            E.append((f"t{tid}", f"U{u}", "likes", f"I{u}_{k}")); tid += 1
        E.append((f"t{tid}", f"U{u}", "subscribes", f"S{u}")); tid += 1
    pats = [("?u","likes","?i"),("?u","subscribes","?s")]
    return (f"star-u{nusers}-d{deg}", E, pats, ["?u"])

def chain(n, fanout):
    """layered path A -> L1 -> ... -> Ln, each layer `fanout` wide and fully connected to the
    next -> each answer has many derivations that SHARE tokens (the correlation both systems
    must handle without double-counting)."""
    E, tid, prev = [], 0, ["A"]
    for i in range(n):
        cur = [f"L{i+1}_{b}" for b in range(fanout)]
        for s in prev:
            for o in cur:
                E.append((f"t{tid}", s, "e", o)); tid += 1
        prev = cur
    pats = [("A", "e", "?v1")] + [(f"?v{i}", "e", f"?v{i+1}") for i in range(1, n)]
    return (f"layer-n{n}-f{fanout}", E, pats, [f"?v{n}"])

INSTANCES = [drug(), star(6, 3), chain(4, 2)]

def provsql_probs(cur, name, E, probs, pats, proj):
    cur.execute("SET search_path = \"$user\", public, provsql;")
    cur.execute("DROP TABLE IF EXISTS e7t CASCADE;")
    cur.execute("CREATE TABLE e7t(s text, p text, o text, proba double precision);")
    for tok, s, p, o in E:
        cur.execute("INSERT INTO e7t VALUES (%s,%s,%s,%s)", (s, p, o, probs[tok]))
    cur.execute("SELECT add_provenance('e7t');")
    cur.execute("SELECT set_prob(provenance(), proba) FROM e7t;")
    # build the BGP self-join + GROUP BY, reading probability(provenance())
    cols = {0: "s", 1: "p", 2: "o"}
    where, first = [], {}
    for i, pat in enumerate(pats):
        for c, term in enumerate(pat):
            ref = f"t{i}.{cols[c]}"
            if isinstance(term, str) and term.startswith("?"):
                if term in first: where.append(f"{ref} = {first[term]}")
                else: first[term] = ref
            else: where.append(f"{ref} = '{term}'")
    sel = ", ".join(f"{first[v]} AS {v[1:]}" for v in proj)
    frm = ", ".join(f"e7t t{i}" for i in range(len(pats)))
    grp = ", ".join(first[v] for v in proj)
    sql = f"SELECT {sel}, probability(provenance()) AS prob FROM {frm} WHERE " + \
          " AND ".join(where) + f" GROUP BY {grp}"
    t = time.time(); cur.execute(sql)
    names = [d[0] for d in cur.description]                 # [proj..., prob, provsql(auto-appended)]
    pi = names.index("prob")
    res = {}
    for r in cur.fetchall():                                # key = projected values in proj order
        res[tuple(str(r[names.index(v[1:])]) for v in proj)] = float(r[pi])
    return (time.time() - t) * 1000, res

def sparqlcirc_probs(E, probs, pats, proj):
    data = {tok: (s, p, o) for tok, s, p, o in E}
    t = time.time()
    circ = gates.Circuit()
    table = gamma.project(circ, gamma.eval_q(circ, ("bgp", pats), data), proj)
    res, tot = {}, 0
    for binding, root in table.items():
        prob, size = compile_bdd.probability(circ.gates, root, probs)[:2]
        b = dict(binding)
        vals = tuple(str(b.get(v)) for v in proj)          # key in proj order, matching ProvSQL
        res[vals] = prob; tot += size
    return (time.time() - t) * 1000, res, len(circ.gates), tot

def main():
    conn = psycopg2.connect(dbname=PGDB, host=os.environ.get("PGHOST"), port=os.environ.get("PGPORT"))
    conn.autocommit = True; cur = conn.cursor()
    print(f"{'instance':>14} {'#triples':>8} {'#ans':>5} {'provsql_ms':>11} {'circ_ms':>8} "
          f"{'circ_gates':>10} {'max|Δp|':>9} {'match':>6} | engine-mod?")
    import csv
    rows = []
    for name, E, pats, proj in tqdm(INSTANCES, desc="E7 head-to-head", unit="inst"):
        probs = {tok: round(random.uniform(0.3, 0.9), 3) for tok, *_ in E}
        pg_ms, pg = provsql_probs(cur, name, E, probs, pats, proj)
        c_ms, cc, gates_n, comp_sz = sparqlcirc_probs(E, probs, pats, proj)
        # align answer keys (single projected var -> value)
        keys = set(pg) | set(cc)
        maxd = max((abs(float(pg.get(k, 0)) - float(cc.get(k, 0))) for k in keys), default=0.0)
        match = maxd < 1e-6
        tqdm.write(f"{name:>14} {len(E):>8} {len(keys):>5} {pg_ms:>11.1f} {c_ms:>8.1f} "
                   f"{gates_n:>10} {maxd:>9.2e} {('OK' if match else 'X'):>6} | "
                   f"ProvSQL=YES ours=NO")
        rows.append(dict(instance=name, n_triples=len(E), answers=len(keys),
                         provsql_ms=round(pg_ms, 1), sparqlcirc_ms=round(c_ms, 1),
                         circ_gates=gates_n, compiled_size=comp_sz, max_prob_diff=maxd,
                         match=match, provsql_needs_engine_mod=True, ours_needs_engine_mod=False))
    with open(os.path.join(os.path.dirname(__file__), "..", "reference", "watdiv", "e7_results.csv"),
              "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)
    ok = sum(r["match"] for r in rows)
    print(f"\n{ok}/{len(rows)} instances: ProvSQL probability == SPARQL_circ probability (exact PQE parity).")
    print("Axis A (the contribution): ProvSQL needs a modified PostgreSQL; SPARQL_circ runs on a stock engine.")
    print("wrote reference/watdiv/e7_results.csv")

if __name__ == "__main__":
    main()
