"""Shapley attribution and why-not explanations on the shared provenance circuit.

Both are read off the SAME compiled circuit the PQE pipeline already builds
(gates.Circuit / circuit_io), so they need no extra machinery beyond the
d-DNNF/ROBDD compiler.

Shapley (Def. "the endogenous triples X are the players; v(S) = Boolean lineage
of the answer on coalition S"):
    Shapley_i = sum_{S subseteq X\\{i}} |S|!(n-1-|S|)!/n! * (phi(S u {i}) - phi(S)).
We compute it two ways and this module's experiment checks they agree exactly:
  * ``shapley_bruteforce`` -- the definition, enumerating coalitions (ground truth).
  * ``shapley_circuit``    -- the tractable route the paper claims: on the compiled
    ROBDD (a deterministic, decomposable circuit) via the integral identity
        Shapley_i = \\int_0^1  [ Pr(phi | x_i=1, others iid p)
                               - Pr(phi | x_i=0, others iid p) ]  dp,
    since \\int_0^1 p^k (1-p)^{n-1-k} dp = k!(n-1-k)!/n! is exactly the Shapley
    weight.  Each Pr(.) is a weighted model count with every other variable given
    the *symbolic* weight p, i.e. a single linear pass over the circuit returning a
    polynomial in p; the difference is integrated term by term.  This is
    polynomial in the compiled circuit (cf. Arenas et al., SHAP on d-D circuits).
    Exact rational arithmetic (fractions.Fraction) -> the two routes match to 0.

Why-not (Def./Example "the subtrahend of the answer's minus-gate is exactly the
derivations whose presence removes it"): ``why_not`` returns, per minus-gate in
the answer cone, the base triples in its subtrahend cone -- the edges whose
absence would admit the excluded binding.
"""
from fractions import Fraction
from itertools import combinations
from math import factorial

import compile_bdd


# --------------------------------------------------------------------------- #
# circuit introspection (works on a gates.Circuit().gates dict, the format
# circuit_io/compile_bdd already consume)
# --------------------------------------------------------------------------- #
def cone_leaves(circ, root):
    """Leaf tokens reachable from ``root`` -- the players X of the game."""
    seen, toks = set(), set()
    def dfs(n):
        if n in seen:
            return
        seen.add(n)
        op, pl = circ[n]
        if op == "leaf":
            toks.add(pl)
        elif op in ("times", "plus"):
            for c in pl:
                dfs(c)
        elif op == "minus":
            dfs(pl[0]); dfs(pl[1])
    dfs(root)
    return sorted(toks)


def boolean_eval(circ, root, true_tokens):
    """Boolean lineage phi(S): True iff ``root`` holds when exactly the tokens in
    ``true_tokens`` are present.  minus = m AND NOT s (the Boolean reading)."""
    true_tokens = set(true_tokens)
    memo = {}
    def go(n):
        if n in memo:
            return memo[n]
        op, pl = circ[n]
        if op == "leaf":
            r = pl in true_tokens
        elif op == "const":
            r = bool(pl)
        elif op == "times":
            r = all(go(c) for c in pl)
        elif op == "plus":
            r = any(go(c) for c in pl)
        elif op == "minus":
            r = go(pl[0]) and not go(pl[1])
        else:
            raise ValueError(op)
        memo[n] = r
        return r
    return go(root)


# --------------------------------------------------------------------------- #
# Shapley -- brute force (ground truth)
# --------------------------------------------------------------------------- #
def shapley_bruteforce(circ, root, players=None):
    X = players if players is not None else cone_leaves(circ, root)
    n = len(X)
    if n == 0:
        return {}
    out = {}
    for i in X:
        rest = [t for t in X if t != i]
        acc = Fraction(0)
        for k in range(len(rest) + 1):
            w = Fraction(factorial(k) * factorial(n - 1 - k), factorial(n))
            for S in combinations(rest, k):
                Sset = set(S)
                gain = int(boolean_eval(circ, root, Sset | {i})) - int(boolean_eval(circ, root, Sset))
                if gain:
                    acc += w * gain
        out[i] = acc
    return out


# --------------------------------------------------------------------------- #
# Shapley -- tractable, on the compiled ROBDD (integral identity)
# --------------------------------------------------------------------------- #
def _padd(a, b):
    m = max(len(a), len(b))
    return [(a[i] if i < len(a) else Fraction(0)) + (b[i] if i < len(b) else Fraction(0))
            for i in range(m)]


def _pscale(a, s):
    return [c * s for c in a]


def _pshift(a):  # multiply by p  (raise every degree by one)
    return [Fraction(0)] + list(a)


def _wmc_poly(bdd, node, fix_level, fix_val):
    """WMC of ``node`` as a polynomial in p: variable at ``fix_level`` is fixed to
    ``fix_val`` (weight 1 on that branch); every other variable gets weight p.
    Skipped variables contribute (1-p)+p = 1 automatically, as in ordinary WMC."""
    memo = {}
    def go(n):
        if n == bdd.FALSE:
            return [Fraction(0)]
        if n == bdd.TRUE:
            return [Fraction(1)]
        if n in memo:
            return memo[n]
        lv, lo, hi = bdd.nodes[n]
        if lv == fix_level:
            r = go(hi) if fix_val else go(lo)
        else:
            lo_p, hi_p = go(lo), go(hi)
            # (1-p)*lo + p*hi
            r = _padd(_padd(lo_p, _pscale(_pshift(lo_p), Fraction(-1))), _pshift(hi_p))
        memo[n] = r
        return r
    return go(node)


def _integrate_0_1(poly):
    """\\int_0^1 sum c_k p^k dp = sum c_k/(k+1)."""
    return sum((poly[k] / (k + 1) for k in range(len(poly))), Fraction(0))


def shapley_circuit(circ, root, players=None):
    """Exact Shapley via one ROBDD compile + 2 polynomial WMC passes per player.
    Polynomial in the compiled circuit size (times n for the poly arithmetic)."""
    X = players if players is not None else cone_leaves(circ, root)
    if not X:
        return {}
    order = compile_bdd.leaf_order(circ, root)
    # players not appearing on any path are still legal variables (Shapley 0);
    # give them a level so the fixed-cofactor logic is uniform.
    for t in X:
        if t not in order:
            order.append(t)
    bdd = compile_bdd.ROBDD(order)
    node = compile_bdd.compile_root(circ, root, bdd, {})
    out = {}
    for i in X:
        lv = bdd.oi[i]
        d = _padd(_wmc_poly(bdd, node, lv, 1),
                  _pscale(_wmc_poly(bdd, node, lv, 0), Fraction(-1)))
        out[i] = _integrate_0_1(d)
    return out


# --------------------------------------------------------------------------- #
# why-not (subtrahend of the answer's minus-gate)
# --------------------------------------------------------------------------- #
def why_not(circ, root):
    """For each minus-gate reachable from ``root``, the base triples in its
    subtrahend cone: the derivations whose presence excludes the binding."""
    seen, out = set(), []
    def dfs(n):
        if n in seen:
            return
        seen.add(n)
        op, pl = circ[n]
        if op == "minus":
            out.append((n, cone_leaves(circ, pl[1])))
            dfs(pl[0]); dfs(pl[1])
        elif op in ("times", "plus"):
            for c in pl:
                dfs(c)
    dfs(root)
    return out


# --------------------------------------------------------------------------- #
def max_abs_diff(a, b):
    keys = set(a) | set(b)
    return max((abs(a.get(k, Fraction(0)) - b.get(k, Fraction(0))) for k in keys),
               default=Fraction(0))
