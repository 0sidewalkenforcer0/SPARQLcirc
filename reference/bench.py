"""Evaluation harness — the headline metric: SHARED CIRCUIT vs PER-ANSWER STRINGS.

NPCS / SPARQLprov serialize each answer's provenance as a string: every derivation
is spelled out and shared subterms are repeated, so the total size is
  T_string  = Σ_answers Σ_derivations (product arity)   [token occurrences].
Our shared circuit stores each distinct gate once:
  T_circuit = #gates + #edges of the DAG.
On families where derivations share structure, T_string grows fast (exponential in
query depth for layered graphs) while T_circuit stays polynomial — the compactness
win, and the reason a probability computation is even feasible. Also times the
rewrite and (where tractable) compile+WMC.

Engine note: GraphDB and in-memory RDF4J emit the identical circuit (verified), so
size/sharing are engine-independent and measured here via the fast in-memory path.
"""
import sys, time, csv
sys.setrecursionlimit(1_000_000)
import gates, factor, compile_bdd, wmc

def layered(k, W):
    data = {}
    for a in range(W): data[f"e0_{a}"] = ("S", "p", f"n1_{a}")
    for i in range(1, k):
        for a in range(W):
            for b in range(W): data[f"e{i}_{a}_{b}"] = (f"n{i}_{a}", "p", f"n{i+1}_{b}")
    return data, [("S", "p", "?v1")] + [(f"?v{i}", "p", f"?v{i+1}") for i in range(1, k)], [f"?v{k}"]

def circuit_size(c, roots):
    seen = set(); edges = 0
    st = list(roots.values())
    while st:
        n = st.pop()
        if n in seen: continue
        seen.add(n); op, pl = c.gates[n]
        kids = pl if op in ("plus", "times") else (pl if op == "minus" else ())
        edges += len(kids); st += list(kids)
    return len(seen), edges

def run(name, data, pats, out):
    P = {t: 0.5 for t in data}
    nderiv = len(wmc._plain_eval(("bgp", pats), set(data.values())))   # one product per derivation
    t0 = time.time(); c = gates.Circuit(); fac = factor.factored_bgp(c, pats, data, set(out))
    t_build = (time.time() - t0) * 1000
    nans = len(fac)
    nodes, edges = circuit_size(c, fac)
    T_string = nderiv * len(pats)                               # token occurrences in the strings
    T_circuit = nodes + edges
    # compile + WMC (skip when the function is #P-hard / blows up: high treewidth)
    wtime, ans_prob = "-", ""
    tok = len(data)
    if tok <= 26 and nderiv <= 400:                            # keep WMC feasible
        t1 = time.time()
        for r in fac.values(): compile_bdd.probability(c.gates, r, P)
        wtime = f"{(time.time()-t1)*1000:.0f}"
    ratio = T_string / T_circuit
    row = dict(instance=name, answers=nans, derivations=nderiv,
               T_string=T_string, T_circuit=T_circuit, sharing=round(ratio, 1),
               build_ms=round(t_build, 1), wmc_ms=wtime)
    print(f"{name:12} ans={nans:>4} deriv={nderiv:>6}  T_string={T_string:>7}  "
          f"T_circuit={T_circuit:>5}  sharing={ratio:>6.1f}x  build={t_build:>6.1f}ms  wmc={wtime:>5}ms")
    return row

if __name__ == "__main__":
    print("=== compactness: per-answer strings (NPCS) vs shared circuit (ours) ===")
    rows = []
    # drug running example
    dd = {"p1":("Aspirin","iw","Warfarin"),"p2":("Warfarin","iw","Metformin"),
          "p3":("Metformin","iw","Omeprazole"),"p4":("Aspirin","iw","Ibuprofen"),
          "p5":("Ibuprofen","iw","Metformin"),"p6":("Warfarin","iw","Lisinopril"),
          "p7":("Lisinopril","iw","Clopidogrel"),"p8":("Clopidogrel","iw","Aspirin")}
    rows.append(run("drug", dd, [("Aspirin","iw","?x"),("?x","iw","?y"),("?y","iw","?z")], ["?z"]))
    print("--- layered k=4, growing width (sharing grows ~ W^2) ---")
    for W in [2, 3, 4, 6, 8]:
        d, p, o = layered(4, W); rows.append(run(f"layered-4x{W}", d, p, o))
    print("--- width-2, growing depth (#derivations = 2^depth, circuit stays linear) ---")
    for k in [4, 8, 12]:
        d, p, o = layered(k, 2); rows.append(run(f"deep-{k}x2", d, p, o))
    with open("bench.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)
    print("\nwrote bench.csv")
