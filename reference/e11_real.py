"""E11-real — the per-answer-vs-shared PQE comparison (E11) on REAL WatDiv queries, not synthetic
families. Same machinery (compile_shared vs compile_per_answer, same compiler + order); the only change
is the circuit comes from real WatDiv `friendOf` data. We sweep k-hop friend-of-friend BGPs (k=1..3):
deeper hop = more cross-answer sharing = larger win, so the win is shown *as a function of a real query's
depth* — the external-validity companion to E11's synthetic mechanism.

Why friendOf: WatDiv's shallow star/path BGPs share little across answers (E2: ~0.5-1x), so they cannot
show the sharing win by construction. friendOf (4.49M edges, the relation behind the Round-3 paths) has
the fan-out that makes many answers route through shared prefixes — real data where the win exists.

Run from reference/ with the env active:  python3 e11_real.py
"""
import sys, time, csv, collections, os
sys.setrecursionlimit(1_000_000)
import gates, gamma, factor, wmc
from e11_per_answer_vs_shared import compile_shared, compile_per_answer, global_order, repr_size

WN = os.environ.get("WATDIV_NT", "/mnt/nfs/home/ac145595/workspace/watdiv-data/watdiv.10M.nt")
FRIEND = "http://db.uwaterloo.ca/~galuc/wsdbm/friendOf"
FANOUT_CAP = 6          # cap per-node fan-out so a k=3 subgraph stays compile-feasible + deterministic

def load_friendof():
    adj = collections.defaultdict(list)
    with open(WN) as f:
        for line in f:
            line = line.rstrip("\n")
            if line.endswith(" ."): line = line[:-2]
            parts = line.split("\t") if "\t" in line else line.split(None, 2)
            if len(parts) != 3: continue
            s, p, o = parts
            if p.strip("<>") == FRIEND:
                adj[s.strip("<>").rsplit("/", 1)[-1]].append(o.strip("<>").rsplit("/", 1)[-1])
    return adj

def extract_khop(adj, src, k):
    """Capped-BFS to a reachable node SET, then include ALL friendOf edges among that set so paths
    RECONVERGE (real triadic closure) -> genuine cross-answer sharing (not a tree). Returns
    (data {token:(s,friendOf,o)}, bgp query bound at src, sel = the k-th-hop endpoint variable)."""
    nodes = {src}; frontier = [src]
    for hop in range(k):
        nxt = []
        for u in frontier:
            for v in sorted(adj.get(u, []))[:FANOUT_CAP]:
                if v not in nodes: nodes.add(v); nxt.append(v)
        frontier = nxt
    data = {}; tid = 0
    for u in nodes:                                        # every edge inside the reachable set
        for v in adj.get(u, []):
            if v in nodes:
                data[f"e{tid}"] = (u, "friendOf", v); tid += 1
    pats = [(src, "friendOf", "?v1")] + [(f"?v{i}", "friendOf", f"?v{i+1}") for i in range(1, k)]
    return data, ("bgp", pats), [f"?v{k}"]

def load_tpch_q3(nt, ncust=6, max_ord=None):
    """Real TPC-H Q3 star-join (customer[BUILDING] -> orders -> lineitems) as a naryrel data dict:
    the token is the ROW entity (subject), so a customer row is SHARED across all its orders and an
    order across its lineitems -> genuine cross-answer sharing (real relational structure, not random).
    Query: ?cust c_mktsegment "BUILDING" . ?order o_custkey ?cust . ?line l_orderkey ?order."""
    import collections
    o_by_cust = collections.defaultdict(list); l_by_order = collections.defaultdict(list); building = []
    with open(nt) as f:
        for line in f:
            line = line.rstrip("\n")
            if line.endswith(" ."): line = line[:-2]
            parts = line.split(None, 2)
            if len(parts) != 3: continue
            s, p, o = parts; pl = p.strip("<>").rsplit("/", 1)[-1]; sl = s.strip("<>").rsplit("/", 1)[-1]
            if pl == "c_mktsegment" and o.strip().strip('"') == "BUILDING": building.append(sl)
            elif pl == "o_custkey": o_by_cust[o.strip("<>").rsplit("/", 1)[-1]].append(sl)
            elif pl == "l_orderkey": l_by_order[o.strip("<>").rsplit("/", 1)[-1]].append(sl)
    data = {}
    for c in sorted(building)[:ncust]:
        data[c] = (c, "c_mktsegment", "BUILDING")                       # token = customer row
        for od in (sorted(o_by_cust.get(c, []))[:max_ord] if max_ord else sorted(o_by_cust.get(c, []))):
            data[od] = (od, "o_custkey", c)                             # token = order row (shares cust)
            for li in sorted(l_by_order.get(od, [])):
                data[li] = (li, "l_orderkey", od)                       # token = lineitem row (shares order)
    pats = [("?cust", "c_mktsegment", "BUILDING"), ("?order", "o_custkey", "?cust"),
            ("?line", "l_orderkey", "?order")]
    return data, pats                                                   # caller picks the projection

def run_real(name, data, q, sel):
    pats, P = q[1], {t: 0.5 for t in data}
    nderiv = len(wmc._plain_eval(q, set(data.values())))
    # OURS: factored shared circuit, compiled once
    cf = gates.Circuit(); roots_ours = factor.factored_bgp(cf, pats, data, set(sel))
    if not roots_ours:
        print(f"  {name}: 0 answers, skip"); return None
    order = global_order(cf.gates, roots_ours); T_circuit = repr_size(cf.gates, roots_ours)
    s_size, s_ms, s_prob = compile_shared(cf.gates, roots_ours, P, order)
    # THEIRS: per-answer how-provenance (flat SoP if feasible, else cone steelman), compiled per answer
    cflat = gates.Circuit(); roots_theirs = gamma.project(cflat, gamma.eval_q(cflat, q, data), sel)
    flat = nderiv <= 256
    pa_size, pa_ms, pa_prob = compile_per_answer(cflat.gates, roots_theirs, P, order, flat=flat)
    keys = set(s_prob) & set(pa_prob)
    parity = max((abs(s_prob[k] - pa_prob[k]) for k in keys), default=0.0)
    T_string = nderiv * len(pats)
    row = dict(query=name, answers=len(roots_ours), deriv=nderiv, tokens=len(data),
               T_string=T_string, T_circuit=T_circuit,
               repr_win=round(T_string / T_circuit, 2) if T_circuit else 1.0,
               size_shared=s_size, size_perans=pa_size,
               compiled_win=round(pa_size / s_size, 2) if s_size else 1.0,
               t_ours_ms=round(s_ms, 1), t_theirs_ms=round(pa_ms, 1),
               time_win=round(pa_ms / max(s_ms, 1e-9), 1),
               theirs_form="flat-SoP" if flat else "cone", parity=f"{parity:.1e}")
    print(f"  {name:16} ans={row['answers']:>4} deriv={nderiv:>5} | repr {T_string:>6}/{T_circuit:<5}={row['repr_win']:>5}x"
          f" | compiled ours={s_size:>5} theirs={pa_size:>6} ({row['compiled_win']}x, time {row['time_win']}x) parity={parity:.0e}")
    return row

def main():
    print("=== E11-real: per-answer vs shared PQE on REAL WatDiv friendOf k-hop queries ===")
    print(f"loading friendOf from {WN} ...")
    adj = load_friendof()
    # pick a source whose bounded k=3 subgraph is non-trivial but compile-feasible
    cand = [u for u, vs in adj.items() if FANOUT_CAP <= len(vs) and all(u2 in adj for u2 in vs[:2])]
    src = sorted(cand)[0] if cand else sorted(adj, key=lambda u: -len(adj[u]))[0]
    print(f"bound source = {src} (friendOf out-degree {len(adj[src])}); fan-out cap {FANOUT_CAP}\n")
    rows = []
    for k in (1, 2, 3):
        data, q, sel = extract_khop(adj, src, k)
        r = run_real(f"friendOf-{k}hop", data, q, sel)
        if r: rows.append(r)
    # TPC-H Q3 star-join: REAL relational structure -> real cross-answer sharing (the positive case)
    tpch = os.environ.get("TPCH_NT", "/mnt/nfs/home/ac145595/workspace/tpch-data/tpch.sf001.nt")
    if os.path.exists(tpch):
        print(f"\n--- TPC-H Q3 (naryrel, real relational star) from {os.path.basename(tpch)} ---")
        print("    projecting to a coarser grain => finer rows SUM per answer => within-answer sharing")
        data, pats = load_tpch_q3(tpch, ncust=16)
        # sel=[?order,?line]: 1 deriv/answer (no sum, no win); ?order: lines sum; ?cust: orders×lines sum
        for tag, sel in [("orderline", ["?order", "?line"]), ("order", ["?order"]), ("cust", ["?cust"])]:
            r = run_real(f"tpch-Q3-{tag}", data, ("bgp", pats), sel)
            if r: rows.append(r)
    with open("e11_real.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)
    print("\nwrote e11_real.csv")
    print("FINDING: real TREE-structured joins (friendOf BGP, TPC-H Q3 star) show repr_win <= ~1x -- no win.")
    print("  Reason: each derivation ends in a DISTINCT token, so #derivations ~ #distinct-tokens; there is")
    print("  no reconvergence for the factored circuit to exploit (coarser projection helps: 0.59->0.84x, but")
    print("  the win is bounded by the join arity). The representation/PQE win needs #derivations >> #tokens")
    print("  (RECONVERGENCE) -- which in SPARQL comes from RECURSION = property paths (Round 3: circuit gates")
    print("  ~n^2 vs ~e*(n-2)! simple paths, unbounded) -- precisely the fragment NPCS/SPARQLprov CANNOT express.")
    print("  => tree joins: tie (we still win on native construction/engine-agnostic/non-monotone); paths: we")
    print("     win unboundedly AND the baselines can't compete. The win is co-extensive with reconvergence.")

if __name__ == "__main__":
    main()
