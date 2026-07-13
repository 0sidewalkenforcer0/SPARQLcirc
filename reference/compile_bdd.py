"""Knowledge compilation for probabilistic query evaluation: compile a shared
provenance circuit into an ROBDD (a subclass of d-DNNF) and compute the answer
probability by weighted model counting in time LINEAR in the compiled size --
replacing the exponential possible-world enumeration.

Pure Python, zero dependencies (runs natively on Apple Silicon / M4).

A circuit is a dict  node -> one of
   ('leaf', tokenName) | ('const', 0|1) | ('times', [child]) | ('plus', [child]) | ('minus', (m, s))
(the format of `gates.Circuit.gates`; `circuit_io.py` loads engine N-Triples).
"""
import itertools


# ------------------------------- ROBDD ---------------------------------------
class ROBDD:
    """Reduced ordered BDD (no complemented edges) with unique + ite caches."""
    FALSE, TRUE = 0, 1

    def __init__(self, order):
        self.oi = {v: i for i, v in enumerate(order)}   # var -> level
        self.ov = list(order)                            # level -> var
        self.nodes = {}                                  # id -> (level, low, high)
        self.unique = {}
        self.next = 2
        self.itec = {}

    def mk(self, level, low, high):
        if low == high:
            return low
        key = (level, low, high)
        n = self.unique.get(key)
        if n is None:
            n = self.next; self.next += 1
            self.nodes[n] = key; self.unique[key] = n
        return n

    def var(self, name):
        return self.mk(self.oi[name], self.FALSE, self.TRUE)

    def _top(self, *ns):
        return min(self.nodes[n][0] for n in ns if n > 1)

    def _co(self, n, level, branch):
        if n <= 1:
            return n
        lv, lo, hi = self.nodes[n]
        if lv == level:
            return hi if branch else lo
        return n

    def ite(self, f, g, h):
        if f == self.TRUE:  return g
        if f == self.FALSE: return h
        if g == h:          return g
        if g == self.TRUE and h == self.FALSE: return f
        key = (f, g, h)
        r = self.itec.get(key)
        if r is not None:
            return r
        level = self._top(f, g, h)
        hi = self.ite(self._co(f, level, 1), self._co(g, level, 1), self._co(h, level, 1))
        lo = self.ite(self._co(f, level, 0), self._co(g, level, 0), self._co(h, level, 0))
        r = self.mk(level, lo, hi)
        self.itec[key] = r
        return r

    def AND(self, a, b): return self.ite(a, b, self.FALSE)
    def OR(self, a, b):  return self.ite(a, self.TRUE, b)
    def NOT(self, a):    return self.ite(a, self.FALSE, self.TRUE)

    def size(self, root):
        seen = set(); st = [root]
        while st:
            n = st.pop()
            if n <= 1 or n in seen: continue
            seen.add(n); _, lo, hi = self.nodes[n]; st += [lo, hi]
        return len(seen)

    def wmc(self, root, P):
        """P(root = TRUE): one memoized bottom-up pass, linear in BDD size."""
        memo = {}
        def go(n):
            if n == self.FALSE: return 0.0
            if n == self.TRUE:  return 1.0
            if n in memo: return memo[n]
            lv, lo, hi = self.nodes[n]
            p = P[self.ov[lv]]
            r = (1 - p) * go(lo) + p * go(hi)
            memo[n] = r
            return r
        return go(root)


# ------------------------- compile a circuit into a BDD ----------------------
def leaf_order(circ, root):
    """Variable order = leaf tokens in DFS first-appearance order from root."""
    order, seen = [], set()
    def dfs(n):
        if n in seen: return
        seen.add(n)
        op, pl = circ[n]
        if op == "leaf":
            if pl not in order: order.append(pl)
        elif op in ("times", "plus"):
            for c in pl: dfs(c)
        elif op == "minus":
            dfs(pl[0]); dfs(pl[1])
    dfs(root)
    return order


def compile_root(circ, root, bdd, memo):
    if root in memo:
        return memo[root]
    op, pl = circ[root]
    if op == "leaf":
        r = bdd.var(pl)
    elif op == "const":
        r = bdd.TRUE if pl else bdd.FALSE
    elif op == "times":
        r = bdd.TRUE
        for c in pl: r = bdd.AND(r, compile_root(circ, c, bdd, memo))
    elif op == "plus":
        r = bdd.FALSE
        for c in pl: r = bdd.OR(r, compile_root(circ, c, bdd, memo))
    elif op == "minus":
        r = bdd.AND(compile_root(circ, pl[0], bdd, memo),
                    bdd.NOT(compile_root(circ, pl[1], bdd, memo)))
    else:
        raise ValueError(op)
    memo[root] = r
    return r


def probability(circ, root, P, order=None):
    order = order or leaf_order(circ, root)
    bdd = ROBDD(order)
    node = compile_root(circ, root, bdd, {})
    return bdd.wmc(node, P), bdd.size(node)


# ------------------------- reference enumeration WMC -------------------------
def wmc_enum(circ, root, P):
    toks = leaf_order(circ, root)
    def val(n, asn, memo):
        if n in memo: return memo[n]
        op, pl = circ[n]
        if op == "leaf": r = asn[pl]
        elif op == "const": r = bool(pl)
        elif op == "times": r = all(val(c, asn, memo) for c in pl)
        elif op == "plus": r = any(val(c, asn, memo) for c in pl)
        elif op == "minus": r = val(pl[0], asn, memo) and not val(pl[1], asn, memo)
        memo[n] = r; return r
    tot = 0.0
    for bits in itertools.product((0, 1), repeat=len(toks)):
        asn = dict(zip(toks, bits))
        if val(root, asn, {}):
            w = 1.0
            for t in toks: w *= P[t] if asn[t] else 1 - P[t]
            tot += w
    return tot
