"""Probabilistic evaluation on the shared circuit + an independent PWE oracle.

prob(): exact probability of a root gate = weighted model count of the circuit's
Boolean abstraction (leaf->var, plus->OR, times->AND, minus(a,b)->a AND NOT b),
computed by enumerating token worlds (exponential; a d-DNNF compiler replaces
this for scale).  Evaluation is memoized over the DAG within each world, so a
shared gate is computed once per world regardless of fan-out.

pwe(): ground truth by possible-world enumeration using PLAIN SPARQL semantics
(no provenance) over the base triples each world activates.
"""
from itertools import product
from collections import defaultdict
import sys
import gamma


# ----------------------------- circuit WMC -----------------------------------
def _boolean(circ, gid, asn, memo):
    if gid in memo:
        return memo[gid]
    op, pl = circ.gates[gid]
    if op == "leaf":
        r = bool(asn[pl])
    elif op == "const":
        r = bool(pl)
    elif op == "plus":
        r = any(_boolean(circ, c, asn, memo) for c in pl)
    elif op == "times":
        r = all(_boolean(circ, c, asn, memo) for c in pl)
    elif op == "minus":
        r = _boolean(circ, pl[0], asn, memo) and not _boolean(circ, pl[1], asn, memo)
    else:
        raise ValueError(op)
    memo[gid] = r
    return r


def prob(circ, root, P):
    toks = circ.leaves()
    total = 0.0
    for bits in product((0, 1), repeat=len(toks)):
        asn = dict(zip(toks, bits))
        if _boolean(circ, root, asn, {}):
            w = 1.0
            for t in toks:
                w *= P[t] if asn[t] else (1 - P[t])
            total += w
    return total


# ----------------------------- PWE ground truth ------------------------------
def _path_pairs(pexpr, T):
    """Set of (u,v) pairs connected by path expr `pexpr` over active triples T --
    an INDEPENDENT plain-reachability oracle (no provenance), for cross-checking the
    gamma path circuit.  SET semantics; zero-length uses the terms-in-graph reading."""
    kind = pexpr[0]
    if kind == "edge":
        return {(s, o) for (s, p, o) in T if p == pexpr[1]}
    if kind == "inv":
        return {(v, u) for (u, v) in _path_pairs(pexpr[1], T)}
    if kind == "alt":
        return _path_pairs(pexpr[1], T) | _path_pairs(pexpr[2], T)
    if kind == "seq":
        R2 = _path_pairs(pexpr[2], T); idx = {}
        for (v, w) in R2: idx.setdefault(v, set()).add(w)
        return {(u, w) for (u, v) in _path_pairs(pexpr[1], T) for w in idx.get(v, ())}
    if kind in ("plus", "star", "opt"):
        R = _path_pairs(pexpr[1], T)
        if kind == "opt":
            return R | {(u, u) for tr in T for u in (tr[0], tr[2])}
        idx = {}                                          # transitive closure
        for (u, v) in R: idx.setdefault(u, set()).add(v)
        clo = set(R); changed = True
        while changed:
            changed = False
            for (u, v) in list(clo):
                for w in idx.get(v, ()):
                    if (u, w) not in clo:
                        clo.add((u, w)); changed = True
        if kind == "star":
            clo |= {(u, u) for tr in T for u in (tr[0], tr[2])}
        return clo
    raise ValueError("path op: " + kind)


def _plain_eval(q, T):
    """Plain SPARQL bindings (list of dicts) over a set T of active (s,p,o) triples."""
    t = q[0]
    if t == "bgp":
        sols = [{}]
        for (s, p, o) in q[1]:
            nxt = []
            for sol in sols:
                for (ts, tp, to) in T:
                    b = dict(sol); ok = True
                    for pv, tv in ((s, ts), (p, tp), (o, to)):
                        if isinstance(pv, str) and pv.startswith("?"):
                            if pv in b and b[pv] != tv:
                                ok = False; break
                            b[pv] = tv
                        elif pv != tv:
                            ok = False; break
                    if ok:
                        nxt.append(b)
            sols = nxt
        return sols
    if t == "union":
        return _plain_eval(q[1], T) + _plain_eval(q[2], T)
    if t == "join":
        out = []
        for a in _plain_eval(q[1], T):
            for b in _plain_eval(q[2], T):
                if all(a[v] == b[v] for v in a if v in b):
                    out.append({**a, **b})
        return out
    if t == "minus":
        rhs = _plain_eval(q[2], T); out = []
        for a in _plain_eval(q[1], T):
            if not any((set(a) & set(b)) and all(a[v] == b[v] for v in a if v in b) for b in rhs):
                out.append(a)
        return out
    if t == "optional":
        rhs = _plain_eval(q[2], T); out = []
        for a in _plain_eval(q[1], T):
            ext = [b for b in rhs if all(a[v] == b[v] for v in a if v in b)]
            out.extend([{**a, **b} for b in ext] if ext else [a])
        return out
    if t == "path":
        subj, pexpr, obj = q[1], q[2], q[3]
        out = []
        for (u, v) in _path_pairs(pexpr, T):
            b = {}
            if isinstance(subj, str) and subj.startswith("?"): b[subj] = u
            elif subj != u: continue
            if isinstance(obj, str) and obj.startswith("?"): b[obj] = v
            elif obj != v: continue
            out.append(b)
        return out
    raise ValueError(t)


def _answers(q, sel, T):
    return {frozenset((v, b[v]) for v in sel if v in b) for b in _plain_eval(q, T)}


def pwe(q, sel, data, P):
    toks = list(data)
    prob_map = defaultdict(float)
    for bits in product((0, 1), repeat=len(toks)):
        active = {data[t] for t, bit in zip(toks, bits) if bit}
        w = 1.0
        for t, bit in zip(toks, bits):
            w *= P[t] if bit else (1 - P[t])
        for a in _answers(q, sel, active):
            prob_map[a] += w
    return dict(prob_map)


def check(circ, table, sel, q, data, P):
    """Compare circuit-WMC vs PWE for every answer; return (ok, total, failures)."""
    truth = pwe(q, sel, data, P)
    circuit_ans = {k: prob(circ, g, P) for k, g in gamma.project(circ, table, sel).items()}
    keys = set(circuit_ans) | {k for k, v in truth.items() if v > 1e-12}
    ok, fails = 0, []
    for k in keys:
        cp = circuit_ans.get(k, 0.0); tp = truth.get(k, 0.0)
        if abs(cp - tp) < 1e-9:
            ok += 1
        else:
            fails.append((dict(k), round(cp, 6), round(tp, 6)))
    return ok, len(keys), fails


def _selftest():
    """Exercise correlation, negation, and cyclic reachability against PWE.

    This deliberately builds fresh in-memory circuits rather than reading the
    checked-in engine fixtures.  The Java-to-RDF integration is covered by
    ``quick_verify.py``; this probe keeps ``python wmc.py`` dependency-free and
    useful on its own.
    """
    import gates

    cases = [
        (
            "shared-correlation",
            {
                "p1": ("A", "p", "B"),
                "p2": ("B", "q", "C"),
                "p3": ("A", "r", "B"),
            },
            ["?z"],
            ("union",
             ("bgp", [("A", "p", "?x"), ("?x", "q", "?z")]),
             ("bgp", [("A", "r", "?x"), ("?x", "q", "?z")])),
            {"p1": .9, "p2": .8, "p3": .6},
        ),
        (
            "minus",
            {
                "k1": ("A", "knows", "B"),
                "k2": ("A", "knows", "C"),
                "b1": ("A", "blocks", "B"),
            },
            ["?y"],
            ("minus",
             ("bgp", [("A", "knows", "?y")]),
             ("bgp", [("A", "blocks", "?y")])),
            {"k1": .8, "k2": .7, "b1": .4},
        ),
        (
            "cyclic-path",
            {
                "e1": ("A", "p", "B"),
                "e2": ("B", "p", "C"),
                "e3": ("C", "p", "A"),
            },
            ["?y"],
            ("path", "A", ("plus", ("edge", "p")), "?y"),
            {"e1": .9, "e2": .8, "e3": .7},
        ),
    ]

    passed = True
    for name, data, sel, q, weights in cases:
        circ = gates.Circuit()
        table = gamma.eval_q(circ, q, data)
        ok, total, failures = check(circ, table, sel, q, data, weights)
        case_ok = ok == total and not failures
        passed &= case_ok
        print(f"  [{'OK' if case_ok else 'FAIL'}] {name:18} {ok}/{total}")
        for failure in failures[:3]:
            print("       mismatch", failure)
    print("WMC SELFTEST", "OK" if passed else "FAILED")
    return passed


if __name__ == "__main__":
    try:
        sys.exit(0 if _selftest() else 1)
    except Exception as exc:
        print(f"WMC SELFTEST ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise
