"""Read a d4/c2d d-DNNF dump and evaluate it once, without invoking d4 again.

Two interchange formats are accepted:

* classic c2d NNF: ``nnf ...`` followed by ``L``/``A``/``O`` nodes;
* d4 edge format: ``o|a|t|f <id> 0`` node declarations plus
  ``<parent> <child> [edge literals] 0`` arcs.

``input_weights`` contains only the original probabilistic input variables as
``{dimacs_var: (p_true, p_false)}``.  Tseitin auxiliary literals are projected
away (weight 1 for either sign), matching ProvSQL's parse-back of compiler
output.  The compiled circuit is deterministic and decomposable, so WMC is a
single bottom-up sum/product pass.  Original input weights are probabilities
and therefore sum to one; explicit smoothing does not change the numeric WMC.
"""
from dataclasses import dataclass


class NNFError(ValueError):
    pass


def _lit_weight(lit, input_weights):
    pair = input_weights.get(abs(lit))
    if pair is None:                         # projected Tseitin auxiliary
        return 1.0
    return pair[0] if lit > 0 else pair[1]


@dataclass(frozen=True)
class EvalResult:
    probability: float
    nodes: int
    edges: int
    format: str


def _clean(text):
    return [line.strip() for line in text.splitlines()
            if line.strip() and not line.lstrip().startswith(("c ", "#"))]


def _classic(lines, input_weights):
    if lines and lines[0].lower().startswith("nnf "):
        lines = lines[1:]
    values, edges = [], 0
    for line in lines:
        p = line.split()
        tag = p[0].upper()
        try:
            if tag == "L" and len(p) == 2:
                values.append(_lit_weight(int(p[1]), input_weights))
            elif tag == "A" and len(p) >= 2:
                n = int(p[1]); children = [int(x) for x in p[2:]]
                if len(children) != n:
                    raise NNFError(f"AND child-count mismatch: {line}")
                if any(c < 0 or c >= len(values) for c in children):
                    raise NNFError(f"AND references a non-earlier node: {line}")
                v = 1.0
                for c in children: v *= values[c]
                values.append(v); edges += n
            elif tag == "O" and len(p) >= 3:
                # p[1] is the optional decision-variable annotation.
                n = int(p[2]); children = [int(x) for x in p[3:]]
                if len(children) != n:
                    raise NNFError(f"OR child-count mismatch: {line}")
                if any(c < 0 or c >= len(values) for c in children):
                    raise NNFError(f"OR references a non-earlier node: {line}")
                values.append(sum(values[c] for c in children)); edges += n
            else:
                raise NNFError(f"unsupported classic-NNF line: {line}")
        except (ValueError, IndexError) as ex:
            raise NNFError(f"malformed classic-NNF line: {line}") from ex
    if not values:
        raise NNFError("empty classic NNF")
    return EvalResult(values[-1], len(values), edges, "classic")


def _d4(lines, input_weights):
    kinds, arcs = {}, {}
    declared = []
    edges = 0
    for line in lines:
        p = line.split()
        if p[0].lower() in ("o", "a", "t", "f"):
            if len(p) < 2:
                raise NNFError(f"malformed d4 node: {line}")
            node = int(p[1]); kind = p[0].lower()
            if node in kinds:
                raise NNFError(f"duplicate d4 node {node}")
            kinds[node] = kind; declared.append(node); arcs[node] = []
            continue
        try:
            nums = [int(x) for x in p]
        except ValueError as ex:
            raise NNFError(f"malformed d4 arc: {line}") from ex
        if len(nums) < 3 or nums[-1] != 0:
            raise NNFError(f"malformed d4 arc: {line}")
        parent, child, lits = nums[0], nums[1], nums[2:-1]
        arcs.setdefault(parent, []).append((child, lits)); edges += 1
    if not kinds:
        raise NNFError("empty d4 NNF")
    unknown_parents = set(arcs) - set(kinds)
    if unknown_parents:
        raise NNFError(f"d4 arcs use undeclared parents: {sorted(unknown_parents)}")
    unknown = {c for xs in arcs.values() for c, _ in xs if c not in kinds}
    if unknown:
        raise NNFError(f"d4 arcs reference undeclared nodes: {sorted(unknown)}")
    terminal_arcs = sorted(n for n, kind in kinds.items()
                           if kind in ("t", "f") and arcs.get(n))
    if terminal_arcs:
        raise NNFError(f"d4 terminal nodes have outgoing arcs: {terminal_arcs}")
    children = {c for xs in arcs.values() for c, _ in xs}
    roots = [n for n in declared if n not in children]
    if len(roots) != 1:
        raise NNFError(f"expected one d4 root, found {roots}")
    memo, active = {}, set()

    def ev(node):
        if node in memo: return memo[node]
        if node in active: raise NNFError("cycle in d4 NNF")
        active.add(node)
        kind = kinds[node]
        if kind == "t": value = 1.0
        elif kind == "f": value = 0.0
        else:
            terms = []
            for child, lits in arcs.get(node, ()):
                v = ev(child)
                for lit in lits: v *= _lit_weight(lit, input_weights)
                terms.append(v)
            value = sum(terms) if kind == "o" else _product(terms)
        active.remove(node); memo[node] = value
        return value

    return EvalResult(ev(roots[0]), len(kinds), edges, "d4")


def _product(xs):
    out = 1.0
    for x in xs: out *= x
    return out


def evaluate_text(text, input_weights):
    """Return :class:`EvalResult` for NNF text and original-input weights."""
    lines = _clean(text)
    if not lines:
        raise NNFError("empty NNF output")
    first = lines[0].split()[0]
    if first.lower() == "nnf" or first in ("L", "A", "O"):
        return _classic(lines, input_weights)
    if first in ("o", "a", "t", "f"):
        return _d4(lines, input_weights)
    raise NNFError(f"unknown NNF format: {lines[0]}")


def evaluate_file(path, input_weights):
    with open(path) as fh:
        return evaluate_text(fh.read(), input_weights)


def weights_from_dimacs(path):
    """Read ``c p weight <lit> <weight> 0`` comments into input-weight pairs.

    Auxiliary variables with (1,1) may be included; they are numerically neutral. Missing signs are rejected.
    """
    by_var = {}
    with open(path) as fh:
        for line in fh:
            p = line.split()
            if len(p) == 6 and p[:3] == ["c", "p", "weight"]:
                lit, weight = int(p[3]), float(p[4])
                pair = by_var.setdefault(abs(lit), [None, None])
                pair[0 if lit > 0 else 1] = weight
    bad = [v for v, pair in by_var.items() if None in pair]
    if bad:
        raise NNFError(f"DIMACS variables missing one literal weight: {bad}")
    return {v: tuple(pair) for v, pair in by_var.items()}
