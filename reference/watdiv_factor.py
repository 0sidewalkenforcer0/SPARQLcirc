"""Real-KG WatDiv: FLAT vs FACTORED circuit construction on the same base.nt
(51,863 real triples). WatDiv star/snowflake project out existential variables
(?a,?b,?c,?cap,?rev), so flat builds the full per-user cross-product while
factored (variable elimination: marginalize each existential with ⊕ before the
⊗) collapses |likes|×|subs|×|mp| to |likes|+|subs|+|mp|. We report reachable
gate/edge counts for both and spot-check that WMC is identical (same function)."""
import os, sys, time, random
from collections import defaultdict
sys.setrecursionlimit(1_000_000); sys.path.insert(0, ".")
import gates, factor, compile_bdd
random.seed(5)

W = "http://db.uwaterloo.ca/~galuc/wsdbm/"
LIKES, SUBS, MP = W + "likes", W + "subscribes", W + "makesPurchase"
PF, HR, CAP = W + "purchaseFor", "http://purl.org/stuff/rev#hasReview", "http://schema.org/caption"

def load(path):
    data = {}
    with open(path) as f:
        for i, line in enumerate(f):
            line = line.strip()
            if not line.endswith("."): continue
            s, p, o = line[:-1].strip().split(" ", 2)
            strip = lambda t: t[1:-1] if t.startswith("<") and t.endswith(">") else t
            data[f"t{i}"] = (strip(s), strip(p), strip(o))
    return data

def flat_bgp(c, patterns, data, out_vars):
    rels = factor.base_relations(c, patterns, data)
    fin = rels[0]
    for r in rels[1:]: fin = factor.join(c, fin, r)  # join all, never marginalize
    v, r = fin
    out = defaultdict(list)
    for key, g in r.items():
        out[frozenset((v[i], key[i]) for i in range(len(v)) if v[i] in out_vars)].append(g)
    return {k: c.plus(gs) for k, gs in out.items()}

def reach(gd, roots):
    seen, edges, st = set(), 0, list(roots)
    while st:
        n = st.pop()
        if n in seen: continue
        seen.add(n); op, pl = gd[n]
        kids = pl if op in ("plus", "times") else (pl if op == "minus" else ())
        edges += len(kids); st += list(kids)
    return len(seen), edges

QUERIES = [
    ("S-star", [("?u", LIKES, "?a"), ("?u", SUBS, "?b"), ("?u", MP, "?c")], {"?u"}),
    ("L-path", [("?u", MP, "?p"), ("?p", PF, "?prod"), ("?prod", HR, "?rev")], {"?u", "?prod", "?rev"}),
    ("F-snow", [("?u", MP, "?p"), ("?p", PF, "?prod"), ("?prod", CAP, "?cap"), ("?prod", HR, "?rev")], {"?u", "?prod"}),
]

def main():
    base = os.environ.get("WATDIV_NT", "")  # raw WatDiv N-Triples subset (not shipped; see watdiv/RESULTS.md)
    if not base or not os.path.exists(base):
        sys.exit("set WATDIV_NT to a WatDiv N-Triples file, e.g. WATDIV_NT=base.nt python3 watdiv_factor.py")
    data = load(base)
    print(f"loaded {len(data)} triples\n")
    print(f"{'query':>8} {'answers':>7} | {'FLAT gates':>10} {'edges':>7} {'ms':>5} | "
          f"{'FACT gates':>10} {'edges':>7} {'ms':>5} | {'gate x':>6} | {'wmc chk':>8}")
    for name, pats, outv in QUERIES:
        cf = gates.Circuit(); t = time.time()
        fa = flat_bgp(cf, pats, data, outv); tf = (time.time() - t) * 1000
        fg, fe = reach(cf.gates, fa.values())
        cx = gates.Circuit(); t = time.time()
        xa = factor.factored_bgp(cx, pats, data, outv); tx = (time.time() - t) * 1000
        xg, xe = reach(cx.gates, xa.values())
        # WMC spot check: require identical answer sets before sampling their probabilities.
        toks = {t for _, (op, t) in cf.gates.items() if op == "leaf"} | \
               {t for _, (op, t) in cx.gates.items() if op == "leaf"}
        P = {tk: round(random.uniform(0.2, 0.9), 3) for tk in toks}
        keys = list(fa)
        samp = keys if len(keys) <= 40 else random.sample(keys, 40)
        ok = set(fa) == set(xa) and all(
            abs(compile_bdd.probability(cf.gates, fa[k], P)[0] -
                compile_bdd.probability(cx.gates, xa[k], P)[0]) < 1e-9
            for k in samp
        )
        print(f"{name:>8} {len(fa):>7} | {fg:>10} {fe:>7} {tf:>5.0f} | "
              f"{xg:>10} {xe:>7} {tx:>5.0f} | {fg/max(xg,1):>5.1f}x | {'OK' if ok else 'FAIL':>8}")

if __name__ == "__main__":
    main()
