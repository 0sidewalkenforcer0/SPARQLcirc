"""Factored provenance-circuit construction by variable elimination (sum-product).

The flat construction (gamma.eval_bgp) builds one ⊗ gate per full derivation, so
a query with many derivations yields a circuit of size ~ #derivations
(|data|^{#patterns}). Factored construction eliminates the non-output join
variables one at a time (each elimination = one "pass"): join the relations that
mention the variable (⊗), then marginalize it (⊕). Intermediate ⊗/⊕ gates are
content-addressed and shared, so the circuit stays polynomial (|data|^{w+1} for
treewidth w) while representing the SAME provenance polynomial.

A relation is (vars: tuple, rows: {value-tuple -> gate id}) over a Circuit `c`.
This is the algorithm behind the engine-native multi-pass CONSTRUCT/INSERT plan:
each elimination step is a join+group-by that materializes the new ⊗/⊕ gates.
"""
from collections import defaultdict


def _pattern_vars(pat):
    seen = []
    for t in pat:
        if isinstance(t, str) and t.startswith("?") and t not in seen:
            seen.append(t)
    return tuple(seen)


def base_relations(c, patterns, data):
    rels = []
    for (s, p, o) in patterns:
        vs = _pattern_vars((s, p, o))
        rows = defaultdict(list)
        for tok, (ts, tp, to) in data.items():
            b = {}; ok = True
            for pv, tv in ((s, ts), (p, tp), (o, to)):
                if isinstance(pv, str) and pv.startswith("?"):
                    if pv in b and b[pv] != tv: ok = False; break
                    b[pv] = tv
                elif pv != tv:
                    ok = False; break
            if ok:
                rows[tuple(b[v] for v in vs)].append(c.leaf(tok))
        rels.append((vs, {k: c.plus(v) for k, v in rows.items()}))
    return rels


def join(c, A, B):
    va, ra = A; vb, rb = B
    shared = [v for v in va if v in vb]
    outv = list(va) + [v for v in vb if v not in va]
    ia = {v: va.index(v) for v in va}
    ib = {v: vb.index(v) for v in vb}
    idx = defaultdict(list)
    for kb, gb in rb.items():
        idx[tuple(kb[ib[v]] for v in shared)].append((kb, gb))
    res = {}
    for ka, ga in ra.items():
        for kb, gb in idx.get(tuple(ka[ia[v]] for v in shared), ()):
            merged = tuple(ka[ia[v]] if v in ia else kb[ib[v]] for v in outv)
            g = c.times([ga, gb])
            res[merged] = c.plus([res[merged], g]) if merged in res else g
    return (tuple(outv), res)


def marginalize(c, R, x):
    v, r = R
    keep = tuple(u for u in v if u != x)
    idxs = [v.index(u) for u in keep]
    grp = defaultdict(list)
    for key, g in r.items():
        grp[tuple(key[i] for i in idxs)].append(g)
    return (keep, {k: c.plus(gs) for k, gs in grp.items()})


def factored_bgp(c, patterns, data, out_vars):
    """Return {frozenset((var,val)) over out_vars -> gate id}."""
    rels = base_relations(c, patterns, data)
    allv = set().union(*[set(v) for v, _ in rels]) if rels else set()
    # Stable lexical tie-breaking makes the generated factored DAG reproducible
    # across PYTHONHASHSEED values when two variables have the same min-fill cost.
    elim = sorted(x for x in allv if x not in out_vars)

    while elim:
        # min-fill-ish: eliminate the var whose relations span the fewest vars
        def cost(x):
            u = set()
            for v, _ in rels:
                if x in v: u |= set(v)
            return len(u)
        x = min(elim, key=lambda v: (cost(v), v)); elim.remove(x)
        involved = [r for r in rels if x in r[0]]
        rest = [r for r in rels if x not in r[0]]
        j = involved[0]
        for r in involved[1:]:
            j = join(c, j, r)
        rels = rest + [marginalize(c, j, x)]

    if not rels:
        return {frozenset(): c.CONST1}
    fin = rels[0]
    for r in rels[1:]:
        fin = join(c, fin, r)
    v, r = fin
    out = defaultdict(list)
    for key, g in r.items():
        out[frozenset((v[i], key[i]) for i in range(len(v)) if v[i] in out_vars)].append(g)
    return {k: c.plus(gs) for k, gs in out.items()}
