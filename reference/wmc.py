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
