"""Phase 3 (claim D, real data) — treewidth of the REAL workload queries' join graphs.

Claim D says compile+WMC tractability is governed by the lineage's treewidth, and that real SPARQL
workloads are low-treewidth (so PQE is tractable where it matters). E4 shows the tw dependence on
SYNTHETIC families; this measures the tw DISTRIBUTION of the actual 30-template workload
(C/F/L/S/O/M × WatDiv), upgrading D from "synthetic" to "the real queries are low-tw".

Method (structural, engine-free): parse each query with rdflib, collect every triple pattern across
BGP / OPTIONAL / MINUS / UNION branches, build the primal (variable-interaction) graph — the variables
of one triple pattern form a clique — and compute the induced width by min-fill elimination (an upper
bound on treewidth; exact for the small tw values here). Property-path predicates connect their
(subject, object) endpoints as one edge. Constants are fixed, not elimination variables.

Writes reference/paper/treewidth_workload.csv. Run: python3 treewidth_workload.py
"""
import os, csv, collections
from rdflib.plugins.sparql import prepareQuery
from rdflib.term import Variable

HERE = os.path.dirname(os.path.abspath(__file__))
CLASS_ORDER = ["C", "F", "L", "S", "O", "M"]


def collect_triples(node, out):
    """Walk the rdflib algebra collecting every BGP's triple list (BGP/OPTIONAL/MINUS/UNION branches)."""
    if not hasattr(node, "name"):
        return
    if node.name == "BGP":
        out.extend(node["triples"])
        return
    for key in ("p", "p1", "p2", "expr", "graph"):
        try:
            child = node[key]
        except (KeyError, TypeError):
            continue
        collect_triples(child, out)


def primal_graph(triples):
    """Variables of each triple pattern form a clique in the primal graph."""
    adj = collections.defaultdict(set)
    for s, p, o in triples:
        vs = [t for t in (s, p, o) if isinstance(t, Variable)]
        for a in vs:
            adj[a]  # ensure present
            for b in vs:
                if a != b:
                    adj[a].add(b)
    return {v: set(ns) for v, ns in adj.items()}


def treewidth_minfill(adj):
    g = {v: set(ns) for v, ns in adj.items()}
    width = 0
    while g:
        def fill(v):
            ns = g[v]
            return sum(1 for a in ns for b in ns if a != b and b not in g.get(a, ()))
        v = min(g, key=lambda v: (fill(v), len(g[v])))
        ns = list(g[v])
        width = max(width, len(ns))
        for a in ns:
            for b in ns:
                if a != b:
                    g[a].add(b)
        for a in ns:
            g[a].discard(v)
        del g[v]
    return width


def main():
    man = list(csv.DictReader(open(os.path.join(HERE, "paper", "workload_manifest.csv"))))
    seen, rows = set(), []
    for r in man:
        key = (r["class"], r["template"], r["scale"])
        if (r["class"], r["template"]) in seen:
            continue
        seen.add((r["class"], r["template"]))
        qf = os.path.join(HERE, r["query_file"])
        if not os.path.exists(qf):
            continue
        try:
            q = prepareQuery(open(qf).read())
            triples = []
            collect_triples(q.algebra, triples)
            adj = primal_graph(triples)
            tw = treewidth_minfill(adj) if adj else 0
            rows.append({"class": r["class"], "template": r["template"], "n_vars": len(adj),
                         "n_patterns": len(triples), "treewidth": tw})
        except Exception as ex:
            rows.append({"class": r["class"], "template": r["template"], "n_vars": "",
                         "n_patterns": "", "treewidth": f"err:{type(ex).__name__}"})
    rows.sort(key=lambda x: (CLASS_ORDER.index(x["class"]) if x["class"] in CLASS_ORDER else 9,
                             int("".join(c for c in x["template"] if c.isdigit()) or 0)))
    out = os.path.join(HERE, "paper", "treewidth_workload.csv")
    with open(out, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["class", "template", "n_vars", "n_patterns", "treewidth"])
        w.writeheader(); w.writerows(rows)
    tws = [r["treewidth"] for r in rows if isinstance(r["treewidth"], int)]
    dist = collections.Counter(tws)
    print(f"treewidth distribution over {len(tws)} workload templates: {dict(sorted(dist.items()))}")
    print(f"  max tw = {max(tws)}, median = {sorted(tws)[len(tws)//2]}")
    by_class = collections.defaultdict(list)
    for r in rows:
        if isinstance(r["treewidth"], int):
            by_class[r["class"]].append(r["treewidth"])
    for c in CLASS_ORDER:
        if by_class[c]:
            print(f"  class {c}: tw {min(by_class[c])}-{max(by_class[c])} (n={len(by_class[c])})")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
