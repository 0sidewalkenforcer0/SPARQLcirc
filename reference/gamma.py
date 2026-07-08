"""gamma: build the shared provenance circuit for a SPARQL query over a
token-labeled (reified, probabilistic) ABox.

Reference builder that realizes the spm-semiring circuit semantics of the paper.
It evaluates the query over the base triples (each carrying a provenance token)
and applies the gate constructors of gates.Circuit, so congruent sub-provenance
is shared across derivations AND across answers (content addressing in gates.py).

Query DSL (non-monotone fragment; property paths deliberately excluded):
  ('bgp',      [ (s,p,o), ... ])      # variables start with '?'
  ('union',    q1, q2)
  ('join',     q1, q2)                # explicit AND (used by optional)
  ('optional', q1, q2)               # q1 OPTIONAL q2  =  (q1 AND q2) UNION (q1 DIFF q2)
  ('minus',    q1, q2)               # q1 DIFF q2

Each eval returns a dict:  binding (frozenset of (var,value)) -> root gate id.
"""
from collections import defaultdict


def _match_bgp(patterns, data):
    """All matches of a BGP. data: token -> (s,p,o).
    Yields (binding_dict, tuple_of_tokens_in_pattern_order)."""
    results = [({}, ())]
    for (s, p, o) in patterns:
        nxt = []
        for (b, toks) in results:
            for tok, (ts, tp, to) in data.items():
                nb = dict(b); ok = True
                for pv, tv in ((s, ts), (p, tp), (o, to)):
                    if isinstance(pv, str) and pv.startswith("?"):
                        if pv in nb and nb[pv] != tv:
                            ok = False; break
                        nb[pv] = tv
                    elif pv != tv:
                        ok = False; break
                if ok:
                    nxt.append((nb, toks + (tok,)))
        results = nxt
    return results


def eval_bgp(circ, patterns, data):
    groups = defaultdict(list)
    for (b, toks) in _match_bgp(patterns, data):
        key = frozenset(b.items())
        groups[key].append(circ.times([circ.leaf(t) for t in toks]))
    return {k: circ.plus(v) for k, v in groups.items()}


def _compatible(a, b):
    return all(a[v] == b[v] for v in a if v in b)


def eval_join(circ, qa, qb, data):
    A, B = eval_q(circ, qa, data), eval_q(circ, qb, data)
    g = defaultdict(list)
    for ka, ga in A.items():
        da = dict(ka)
        for kb, gb in B.items():
            db = dict(kb)
            if _compatible(da, db):
                merged = frozenset({**da, **db}.items())
                g[merged].append(circ.times([ga, gb]))
    return {k: circ.plus(v) for k, v in g.items()}


def eval_union(circ, qa, qb, data):
    A, B = eval_q(circ, qa, data), eval_q(circ, qb, data)
    g = defaultdict(list)
    for k, gate in A.items():
        g[k].append(gate)
    for k, gate in B.items():
        g[k].append(gate)
    return {k: circ.plus(v) for k, v in g.items()}


def eval_minus(circ, qa, qb, data):
    """q1 DIFF q2: keep each q1 binding, subtract the summed provenance of the
    q2 bindings that share a bound variable and are compatible."""
    A, B = eval_q(circ, qa, data), eval_q(circ, qb, data)
    out = {}
    for ka, ga in A.items():
        da = dict(ka)
        subs = []
        for kb, gb in B.items():
            db = dict(kb)
            if (set(da) & set(db)) and _compatible(da, db):
                subs.append(gb)
        out[ka] = circ.minus(ga, circ.plus(subs) if subs else circ.CONST0)
    return out


def eval_optional(circ, qa, qb, data):
    """q1 OPTIONAL q2  =  (q1 AND q2)  UNION  (q1 DIFF q2)."""
    joined = eval_join(circ, qa, qb, data)   # extended bindings (q2 matched)
    diffed = eval_minus(circ, qa, qb, data)  # q1-only bindings (q2 unmatched)
    g = defaultdict(list)
    for k, gate in joined.items():
        g[k].append(gate)
    for k, gate in diffed.items():
        g[k].append(gate)
    return {k: circ.plus(v) for k, v in g.items()}


def eval_q(circ, q, data):
    t = q[0]
    if t == "bgp":      return eval_bgp(circ, q[1], data)
    if t == "join":     return eval_join(circ, q[1], q[2], data)
    if t == "union":    return eval_union(circ, q[1], q[2], data)
    if t == "minus":    return eval_minus(circ, q[1], q[2], data)
    if t == "optional": return eval_optional(circ, q[1], q[2], data)
    raise ValueError("unknown op: " + t)


def project(circ, table, sel):
    """Project answers onto SELECT vars `sel` (list of '?x'), summing provenance
    of bindings that agree on the projected (bound) variables."""
    g = defaultdict(list)
    for k, gate in table.items():
        pk = frozenset((v, val) for (v, val) in k if v in sel)
        g[pk].append(gate)
    return {k: circ.plus(v) for k, v in g.items()}
