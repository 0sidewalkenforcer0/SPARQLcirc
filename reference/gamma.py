"""gamma: build the shared provenance circuit for a SPARQL query over a
token-labeled (reified, probabilistic) ABox.

Reference builder that realizes the spm-semiring circuit semantics of the paper.
It evaluates the query over the base triples (each carrying a provenance token)
and applies the gate constructors of gates.Circuit, so congruent sub-provenance
is shared across derivations AND across answers (content addressing in gates.py).

Query DSL (non-monotone fragment):
  ('bgp',      [ (s,p,o), ... ])      # variables start with '?'
  ('union',    q1, q2)
  ('join',     q1, q2)                # explicit AND (used by optional)
  ('optional', q1, q2)               # q1 OPTIONAL q2  =  (q1 AND q2) UNION (q1 DIFF q2)
  ('minus',    q1, q2)               # q1 DIFF q2
  ('path',     subj, pathexpr, obj)  # SPARQL 1.1 property path; subj/obj var or const

pathexpr grammar (SET semantics, absorptive semiring PosBool -- circ.oplus/otimes):
  ('edge', p)          one predicate p              ('inv',  e)      inverse    ^e
  ('seq',  e1, e2)     sequence    e1/e2            ('alt',  e1, e2) alternative e1|e2
  ('plus', e)          one-or-more e+               ('star', e)      zero-or-more e*
  ('opt',  e)          zero-or-one  e?
Negated property sets !(...) are out of scope.

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


def eval_minus(circ, qa, qb, data, guard=True):
    """q1 DIFF q2: keep each q1 binding, subtract the summed provenance of the
    compatible q2 bindings.

    guard=True  -> W3C MINUS: only subtract when the two bindings also share a
                   variable (the domain-intersection guard); disjoint operands
                   are a no-op. This is user-level MINUS.
    guard=False -> the raw (unguarded) anti-join used as OPTIONAL's negative
                   branch: subtract every compatible q2, even domain-disjoint
                   ones. (For shared-variable operands the two agree, since
                   compatible bindings then always share the bound variable.)"""
    A, B = eval_q(circ, qa, data), eval_q(circ, qb, data)
    out = {}
    for ka, ga in A.items():
        da = dict(ka)
        subs = []
        for kb, gb in B.items():
            db = dict(kb)
            if (not guard or (set(da) & set(db))) and _compatible(da, db):
                subs.append(gb)
        out[ka] = circ.minus(ga, circ.plus(subs) if subs else circ.CONST0)
    return out


def eval_optional(circ, qa, qb, data):
    """q1 OPTIONAL q2  =  (q1 AND q2)  UNION  (q1 DIFF q2), the DIFF being UNGUARDED."""
    joined = eval_join(circ, qa, qb, data)                # extended bindings (q2 matched)
    diffed = eval_minus(circ, qa, qb, data, guard=False)  # q1-only bindings (q2 unmatched)
    g = defaultdict(list)
    for k, gate in joined.items():
        g[k].append(gate)
    for k, gate in diffed.items():
        g[k].append(gate)
    return {k: circ.plus(v) for k, v in g.items()}


# ---------------- property paths (SET semantics, absorptive PosBool) ----------------
# A path expression evaluates to a PAIR-RELATION {(u,v): gate} in PosBool(X): (+)/(*)
# are idempotent+absorptive (circ.oplus/circ.otimes), so a reachable pair appears once
# regardless of #paths and alternative-path duplicates (e.g. :p|:q) collapse.

def _nodes(data):
    ns = set()
    for (s, p, o) in data.values():
        ns.add(s); ns.add(o)
    return ns

def _add(circ, rel, u, v, g):                 # rel[(u,v)] (+)= g  (idempotent)
    rel[(u, v)] = circ.oplus([rel[(u, v)], g]) if (u, v) in rel else g

def _compose(circ, R1, R2):                   # relational compose R1 then R2  (e1/e2)
    by_src = {}
    for (v, w), g in R2.items():
        by_src.setdefault(v, []).append((w, g))
    out = {}
    for (u, v), g1 in R1.items():
        for (w, g2) in by_src.get(v, ()):
            _add(circ, out, u, w, circ.otimes([g1, g2]))
    return out

def _closure(circ, R, nodes):
    """Transitive closure of R via a LEVEL-INDEXED fixpoint (Bellman-Ford style):
    reach^0 = R;  reach^{k+1} = reach^k (+) (reach^k compose R).  Each round's gates
    reference only the previous round's gates, so the emitted circuit is an ACYCLIC DAG
    even when the data graph has CYCLES.  |V| rounds capture every simple path
    (<= |V|-1 edges); longer (non-simple) walks add no probability mass under the
    Boolean/PosBool reading.  Recursive sharing (reach^{k+1}(u,v) references the single
    gate reach^k(u,v)) keeps this to O(|V_s| . |E_s|) gates for a bound source."""
    by_src = {}
    for (v, w), g in R.items():
        by_src.setdefault(v, []).append((w, g))
    reach = dict(R)
    for _ in range(max(1, len(nodes))):
        new = dict(reach); changed = False
        for (u, v), guv in reach.items():         # reach = previous round (read-only)
            for (w, gvw) in by_src.get(v, ()):
                prod = circ.otimes([guv, gvw])
                key = (u, w)
                ng = circ.oplus([new[key], prod]) if key in new else prod
                if new.get(key) != ng:
                    new[key] = ng; changed = True
        reach = new
        if not changed:
            break
    return reach

def _zerolen(circ, data):                     # {(u,u): "u occurs in graph"} (terms-in-graph)
    rel = {}
    for u in _nodes(data):
        rel[(u, u)] = circ.oplus([circ.leaf(t) for t, (s, p, o) in data.items()
                                  if s == u or o == u])
    return rel

def _with_zerolen(circ, rel, data):
    out = dict(rel)
    for k, g in _zerolen(circ, data).items():
        _add(circ, out, k[0], k[1], g)
    return out

def eval_pexpr(circ, e, data):
    """Evaluate a path expression to a pair-relation {(u,v): gate} in PosBool."""
    kind = e[0]
    if kind == "edge":
        rel = {}
        for tok, (s, p, o) in data.items():
            if p == e[1]:
                _add(circ, rel, s, o, circ.leaf(tok))
        return rel
    if kind == "inv":
        return {(v, u): g for (u, v), g in eval_pexpr(circ, e[1], data).items()}
    if kind == "alt":
        out = dict(eval_pexpr(circ, e[1], data))
        for (u, v), g in eval_pexpr(circ, e[2], data).items():
            _add(circ, out, u, v, g)
        return out
    if kind == "seq":
        return _compose(circ, eval_pexpr(circ, e[1], data), eval_pexpr(circ, e[2], data))
    if kind == "plus":
        return _closure(circ, eval_pexpr(circ, e[1], data), _nodes(data))
    if kind == "star":
        return _with_zerolen(circ, _closure(circ, eval_pexpr(circ, e[1], data), _nodes(data)), data)
    if kind == "opt":
        return _with_zerolen(circ, eval_pexpr(circ, e[1], data), data)
    raise ValueError("unknown path op: " + kind)

def eval_path(circ, subj, pexpr, obj, data):
    """Bind a property-path pattern (subj pathexpr obj); subj/obj are var ('?x') or const."""
    rel = eval_pexpr(circ, pexpr, data)
    groups = defaultdict(list)
    for (u, v), g in rel.items():
        b = {}
        if isinstance(subj, str) and subj.startswith("?"): b[subj] = u
        elif subj != u: continue
        if isinstance(obj, str) and obj.startswith("?"): b[obj] = v
        elif obj != v: continue
        groups[frozenset(b.items())].append(g)
    return {k: circ.oplus(v) for k, v in groups.items()}


def eval_q(circ, q, data):
    t = q[0]
    if t == "bgp":      return eval_bgp(circ, q[1], data)
    if t == "join":     return eval_join(circ, q[1], q[2], data)
    if t == "union":    return eval_union(circ, q[1], q[2], data)
    if t == "minus":    return eval_minus(circ, q[1], q[2], data)
    if t == "optional": return eval_optional(circ, q[1], q[2], data)
    if t == "path":     return eval_path(circ, q[1], q[2], q[3], data)
    raise ValueError("unknown op: " + t)


def project(circ, table, sel):
    """Project answers onto SELECT vars `sel` (list of '?x'), summing provenance
    of bindings that agree on the projected (bound) variables."""
    g = defaultdict(list)
    for k, gate in table.items():
        pk = frozenset((v, val) for (v, val) in k if v in sel)
        g[pk].append(gate)
    return {k: circ.plus(v) for k, v in g.items()}
