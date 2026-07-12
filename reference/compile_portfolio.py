"""Exact-probability PORTFOLIO for a provenance circuit — the SAME method + logic ProvSQL uses.

Motivation (see SERVER_TASK "E4 / compiler"). ProvSQL, the strongest baseline, computes exact probability
with a *cost-ranked exact portfolio*: it tries the cheapest applicable exact method first and only falls
back to an external d-DNNF knowledge compiler (d4 / c2d / dsharp) for hard instances. Our head-to-head
should use the SAME stack, so the compiler is a shared, non-contentious component and the only difference
is the data model (stock SPARQL 1.1 / RDF vs a forked PostgreSQL / relations) — not a bespoke ROBDD.

This module mirrors that chain on OUR provenance circuit (`circuit_io.parse` format):
  1. read-once      -> linear bottom-up eval; each token appears once => siblings are over disjoint
                       variables => independent, so ⊗=∏p, ⊕=1−∏(1−p), ⊖=p_m·(1−p_s) are EXACT.   O(size)
  2. possible-worlds-> brute-force 2^n enumeration for <= SMALL tokens (compile_bdd.wmc_enum).
  3. compilation    -> Tseitin CNF (export_cnf, same encoding ProvSQL uses for non-read-once circuits)
                       -> external d-DNNF compiler **d4** (D4 env; use d4-v2) -> weighted model count.
  fallback          -> our OBDD (compile_bdd.probability) when d4 is unavailable (arm64 / no D4 set).

NOTE: tree-decomposition — the step ProvSQL puts between (2) and (3) for bounded-treewidth circuits — is
a documented TODO here (needs a TD library); its absence only means we reach `compilation` slightly
earlier, never a wrong answer. OBDD + possible-world enumeration remain the INDEPENDENT correctness
oracle (E1/G6); this module does not replace them.
"""
import os, subprocess, tempfile
import compile_bdd, export_cnf

SMALL = int(os.environ.get("PORTFOLIO_PWE_MAX", "20"))       # possible-worlds only below this #tokens (2^20 = 1M)


def _children(circ, n):
    op, pl = circ[n]
    if op in ("times", "plus"): return list(pl)
    if op == "minus": return [pl[0], pl[1]]
    return []


def _reach(circ, root):
    stk, seen = [root], set()
    while stk:
        n = stk.pop()
        if n in seen: continue
        seen.add(n)
        stk.extend(_children(circ, n))
    return seen


def is_read_once(circ, root):
    """True iff the cone at `root` is a tree with distinct leaves (every reachable node is referenced as a
    child at most once). Read-once => every token appears once => the linear independence eval is EXACT."""
    ref = {}
    for n in _reach(circ, root):
        for c in _children(circ, n):
            ref[c] = ref.get(c, 0) + 1
    return all(v <= 1 for v in ref.values())


def _leaves(circ, root):
    return {circ[n][1] for n in _reach(circ, root) if circ[n][0] == "leaf"}


def prob_read_once(circ, root, P):
    """Exact probability of a READ-ONCE circuit by one bottom-up pass (independence holds)."""
    def ev(n):
        op, pl = circ[n]
        if op == "leaf":  return P[pl]
        if op == "times":
            r = 1.0
            for c in pl: r *= ev(c)
            return r
        if op == "plus":
            r = 1.0
            for c in pl: r *= (1.0 - ev(c))
            return 1.0 - r
        if op == "minus": return ev(pl[0]) * (1.0 - ev(pl[1]))
        raise ValueError("unexpected gate op: " + op)
    return ev(root)


def d4_wmc(circ, root, P, d4bin=None):
    """Compilation path: Tseitin CNF (export_cnf) -> d4 d-DNNF + weighted count. Returns (wmc, ddnnf_nodes)
    or None if d4 is unavailable / fails. Reuses d4_pipeline's version-aware d4 invocation (v1 or v2)."""
    if d4bin is None:
        d4bin = os.environ.get("D4")
    if not d4bin:
        return None
    import d4_pipeline as d4p                                  # lazy: only when d4 is actually used
    e = export_cnf.export(circ, root, P)
    d = tempfile.mkdtemp(prefix="portf_")
    cnf = os.path.join(d, "c.cnf"); open(cnf, "w").write(e["dimacs"])
    nnf, wf = cnf + ".nnf", cnf + ".w"
    try:
        subprocess.run(d4p.ddnnf_cmd(cnf, nnf), check=True, capture_output=True, timeout=600)
        d4p.write_weights(cnf, wf)
        out = subprocess.run(d4p.wmc_cmd(cnf, wf), check=True, capture_output=True, text=True, timeout=600)
        wmc = d4p.parse_wmc(out.stdout)
        nodes = d4p.nnf_size(nnf)[0] if os.path.exists(nnf) else None
        return (wmc, nodes) if wmc is not None else None
    except Exception:
        return None


def probability(circ, root, P):
    """Exact probability + the method used, choosing the cheapest applicable exact method (ProvSQL-style).
    Returns (prob, method) where method in {read-once, possible-worlds, compilation-d4, obdd-fallback}."""
    if is_read_once(circ, root):
        return prob_read_once(circ, root, P), "read-once"
    if len(_leaves(circ, root)) <= SMALL:
        return compile_bdd.wmc_enum(circ, root, P), "possible-worlds"
    r = d4_wmc(circ, root, P)                                  # Tseitin CNF -> d4 (if D4 set)
    if r is not None:
        return r[0], "compilation-d4"
    return compile_bdd.probability(circ, root, P)[0], "obdd-fallback"   # portable fallback (no d4)
