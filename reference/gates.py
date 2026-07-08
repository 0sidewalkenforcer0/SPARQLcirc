"""Shared provenance circuit with content-addressed (hash-consed) gates.

Model (VLDB draft, Def. "Provenance circuit"): a DAG of gates, each a
  - leaf  (a base-triple token, or a constant 0/1),
  - plus  (variadic, commutative/associative ⊕),
  - times (variadic, commutative/associative ⊗),
  - minus (binary ⊖(minuend, subtrahend), NOT commutative).

Content addressing: a gate's id is a deterministic hash of its operator and its
*canonicalized* children.  Congruent gates (same op + same children up to the
Def-"Gate congruence" simplifications) therefore get the SAME id and are stored
once -> the circuit is a maximally shared DAG.

Collision-freeness (the issue.txt concern): child order is canonicalized by
*sorting the child ids* (fixed-width sha1 hex strings), NOT by an order-invariant
SUM/COUNT aggregate.  A commutative gate's key is  op | sorted(child_ids)  with a
delimiter that cannot occur inside a sha1 hex id, so the serialization is
injective on the child multiset (duplicates are kept -> no false idempotence);
distinct gates collide only under a sha1 collision.
"""
import hashlib


def _sha(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()


class Circuit:
    def __init__(self):
        # id -> (op, payload):
        #   ('leaf', token) | ('const', 0|1) | ('plus'|'times', tuple(child_ids)) | ('minus', (m,s))
        self.gates = {}
        self.CONST0 = self._put(_sha("CONST|0"), ("const", 0))
        self.CONST1 = self._put(_sha("CONST|1"), ("const", 1))

    def _put(self, gid, node):
        prev = self.gates.get(gid)
        if prev is not None and prev != node:
            raise RuntimeError(f"hash collision: {gid} -> {prev} vs {node}")
        self.gates[gid] = node
        return gid

    # ---- constructors (each canonicalizes, then content-addresses) ----
    def leaf(self, token: str) -> str:
        return self._put(_sha("LEAF|" + token), ("leaf", token))

    def times(self, children):
        cs = [c for c in children if c != self.CONST1]          # unit:  a⊗1 = a
        if any(self.gates[c] == ("const", 0) for c in cs):      # annih: a⊗0 = 0
            return self.CONST0
        if not cs:
            return self.CONST1
        if len(cs) == 1:
            return cs[0]
        cs = sorted(cs)                                         # commutative -> canonical order
        return self._put(_sha("TIMES|" + "|".join(cs)), ("times", tuple(cs)))

    def plus(self, children):
        cs = [c for c in children if c != self.CONST0]          # unit: a⊕0 = a
        if not cs:
            return self.CONST0
        if len(cs) == 1:
            return cs[0]
        cs = sorted(cs)   # commutative; duplicates KEPT (no idempotence: g⊕g = 2g in N[X])
        return self._put(_sha("PLUS|" + "|".join(cs)), ("plus", tuple(cs)))

    def minus(self, m: str, s: str) -> str:
        if s == self.CONST0:                                    # a⊖0 = a
            return m
        if m == self.CONST0:                                    # 0⊖b = 0
            return self.CONST0
        return self._put(_sha("MINUS|" + m + "|" + s), ("minus", (m, s)))

    # ---- introspection ----
    def leaves(self):
        return sorted({pl for _, (op, pl) in self.gates.items() if op == "leaf"})

    def stats(self):
        from collections import Counter
        c = Counter(op for op, _ in self.gates.values())
        return dict(c)

    def fanout(self):
        """id -> number of distinct parents (sharing witness)."""
        from collections import Counter
        cnt = Counter()
        for op, pl in self.gates.values():
            kids = pl if op in ("plus", "times") else (pl if op == "minus" else ())
            for k in kids:
                cnt[k] += 1
        return cnt
