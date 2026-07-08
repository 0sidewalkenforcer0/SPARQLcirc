"""Real d-DNNF-family compilation via SDD (PySDD), for scalable exact WMC.

SDD (Sentential Decision Diagram) is a *structured* deterministic, decomposable
NNF -- a subclass of d-DNNF, strictly more succinct than OBDD, poly-size for
bounded-treewidth lineage with a good vtree. Runs natively on Apple Silicon
(pip install pysdd); used because d4's bundled PATOH partitioner is x86_64-only.

Build the SDD directly from the provenance circuit (no CNF/Tseitin needed): the
leaves are the token variables, so WMC over the SDD with token probabilities is
the answer probability. compile() returns (wmc, sdd_size).
"""
from pysdd.sdd import SddManager, Vtree
import compile_bdd


def _build(mgr, idx, circ, root):
    memo = {}
    def go(n):
        if n in memo:
            return memo[n]
        op, pl = circ[n]
        if op == "leaf":
            r = mgr.literal(idx[pl])
        elif op == "const":
            r = mgr.true() if pl else mgr.false()
        elif op == "times":
            r = mgr.true()
            for c in pl:
                r = r & go(c)
        elif op == "plus":
            r = mgr.false()
            for c in pl:
                r = r | go(c)
        elif op == "minus":
            r = go(pl[0]) & ~go(pl[1])
        else:
            raise ValueError(op)
        memo[n] = r
        return r
    return go(root)


def _size(node):
    for attr in ("size", "count"):
        f = getattr(node, attr, None)
        if callable(f):
            try:
                return f()
            except Exception:
                pass
    return -1


def compile(circ, root, P, order=None, vtree="balanced", minimize=False):
    order = order or compile_bdd.leaf_order(circ, root)
    n = len(order)
    idx = {t: i + 1 for i, t in enumerate(order)}
    if vtree in ("balanced", "right", "left", "vertical"):
        vt = Vtree(var_count=n, vtree_type=vtree)
        mgr = SddManager.from_vtree(vt)
    else:
        mgr = SddManager(var_count=n)
    node = _build(mgr, idx, circ, root)
    if minimize:
        mgr.minimize()
    w = node.wmc(log_mode=False)
    for t, i in idx.items():
        p = P[t]
        w.set_literal_weight(mgr.literal(i), p)
        w.set_literal_weight(mgr.literal(-i), 1 - p)
    return w.propagate(), _size(node)
