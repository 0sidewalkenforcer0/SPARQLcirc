"""Read a d4/c2d d-DNNF dump and evaluate it once, without invoking d4 again.

Two interchange formats are accepted:

* classic c2d NNF: ``nnf ...`` followed by ``L``/``A``/``O`` nodes;
* d4 edge format: ``o|a|t|f <id> 0`` node declarations plus
  ``<parent> <child> [edge literals] 0`` arcs.

``input_weights`` contains only the original probabilistic input variables as
``{dimacs_var: (p_true, p_false)}``.  For a Tseitin CNF, use
``evaluate_tseitin_*`` with the complete DIMACS weight map.  d4v2 omits free
variables from its edge dump, so those helpers normalize auxiliary weights to
``(0.5, 0.5)`` during a numerically scaled pass and restore the global Tseitin
factor afterward.  This preserves the intended ``(1, 1)`` auxiliary weights
without requiring the dump to name every branch-local free variable.
"""
from dataclasses import dataclass
import math


class NNFError(ValueError):
    pass


def _lit_weight(lit, input_weights):
    pair = input_weights.get(abs(lit))
    if pair is None:                         # projected Tseitin auxiliary
        return 1.0
    return pair[0] if lit > 0 else pair[1]


def _scaled(value):
    if value == 0.0:
        return (0.0, 0)
    mantissa, exponent = math.frexp(value)
    return (mantissa, exponent)


def _scaled_sum(values):
    nonzero = [value for value in values if value[0] != 0.0]
    if not nonzero:
        return (0.0, 0)
    exponent = max(value[1] for value in nonzero)
    mantissa = math.fsum(
        math.ldexp(value[0], value[1] - exponent) for value in nonzero
    )
    normalized, adjustment = math.frexp(mantissa)
    return (normalized, exponent + adjustment)


def _scaled_product(values):
    result = (0.5, 1)
    for value in values:
        if value[0] == 0.0:
            return (0.0, 0)
        mantissa, adjustment = math.frexp(result[0] * value[0])
        result = (mantissa, result[1] + value[1] + adjustment)
    return result


def _sum(values, scaled):
    return _scaled_sum(values) if scaled else sum(values)


def _product(values, scaled=False):
    if scaled:
        return _scaled_product(values)
    out = 1.0
    for value in values:
        out *= value
    return out


def _weight(lit, input_weights, scaled):
    value = _lit_weight(lit, input_weights)
    return _scaled(value) if scaled else value


def _finish(value, scaled, scale_exponent):
    if not scaled:
        return value
    try:
        return math.ldexp(value[0], value[1] + scale_exponent)
    except OverflowError as exc:
        raise NNFError("normalized Tseitin WMC overflowed") from exc


@dataclass(frozen=True)
class EvalResult:
    probability: float
    nodes: int
    edges: int
    format: str


def _clean(text):
    return [line.strip() for line in text.splitlines()
            if line.strip() and not line.lstrip().startswith(("c ", "#"))]


def _classic(lines, input_weights, scaled=False, scale_exponent=0):
    if lines and lines[0].lower().startswith("nnf "):
        lines = lines[1:]
    values, edges = [], 0
    for line in lines:
        p = line.split()
        tag = p[0].upper()
        try:
            if tag == "L" and len(p) == 2:
                values.append(_weight(int(p[1]), input_weights, scaled))
            elif tag == "A" and len(p) >= 2:
                n = int(p[1]); children = [int(x) for x in p[2:]]
                if len(children) != n:
                    raise NNFError(f"AND child-count mismatch: {line}")
                if any(c < 0 or c >= len(values) for c in children):
                    raise NNFError(f"AND references a non-earlier node: {line}")
                values.append(_product((values[c] for c in children), scaled)); edges += n
            elif tag == "O" and len(p) >= 3:
                # p[1] is the optional decision-variable annotation.
                n = int(p[2]); children = [int(x) for x in p[3:]]
                if len(children) != n:
                    raise NNFError(f"OR child-count mismatch: {line}")
                if any(c < 0 or c >= len(values) for c in children):
                    raise NNFError(f"OR references a non-earlier node: {line}")
                values.append(_sum((values[c] for c in children), scaled)); edges += n
            else:
                raise NNFError(f"unsupported classic-NNF line: {line}")
        except (ValueError, IndexError) as ex:
            raise NNFError(f"malformed classic-NNF line: {line}") from ex
    if not values:
        raise NNFError("empty classic NNF")
    return EvalResult(_finish(values[-1], scaled, scale_exponent),
                      len(values), edges, "classic")


def _d4(lines, input_weights, scaled=False, scale_exponent=0):
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
        if kind == "t": value = _scaled(1.0) if scaled else 1.0
        elif kind == "f": value = _scaled(0.0) if scaled else 0.0
        else:
            terms = []
            for child, lits in arcs.get(node, ()):
                factors = [ev(child)]
                factors.extend(_weight(lit, input_weights, scaled) for lit in lits)
                terms.append(_product(factors, scaled))
            value = _sum(terms, scaled) if kind == "o" else _product(terms, scaled)
        active.remove(node); memo[node] = value
        return value

    return EvalResult(_finish(ev(roots[0]), scaled, scale_exponent),
                      len(kinds), edges, "d4")


def _evaluate_text(text, input_weights, scaled=False, scale_exponent=0):
    lines = _clean(text)
    if not lines:
        raise NNFError("empty NNF output")
    first = lines[0].split()[0]
    if first.lower() == "nnf" or first in ("L", "A", "O"):
        return _classic(lines, input_weights, scaled, scale_exponent)
    if first in ("o", "a", "t", "f"):
        return _d4(lines, input_weights, scaled, scale_exponent)
    raise NNFError(f"unknown NNF format: {lines[0]}")


def evaluate_text(text, input_weights):
    """Return :class:`EvalResult` for NNF text and original-input weights."""
    return _evaluate_text(text, input_weights)


def _normalized_tseitin_weights(dimacs_weights):
    normalized = {}
    auxiliary_count = 0
    for variable, pair in dimacs_weights.items():
        if not isinstance(variable, int) or variable <= 0 or len(pair) != 2:
            raise NNFError("invalid Tseitin DIMACS weight entry")
        positive, negative = float(pair[0]), float(pair[1])
        if not all(math.isfinite(value) and value >= 0.0
                   for value in (positive, negative)):
            raise NNFError(f"invalid weights for DIMACS variable {variable}")
        if positive == 1.0 and negative == 1.0:
            normalized[variable] = (0.5, 0.5)
            auxiliary_count += 1
        elif math.isclose(positive + negative, 1.0,
                          rel_tol=1e-12, abs_tol=1e-15):
            normalized[variable] = (positive, negative)
        else:
            raise NNFError(
                f"DIMACS variable {variable} is neither probabilistic nor a (1,1) auxiliary"
            )
    # d4v2 can branch on its unused internal variable zero.  Giving both
    # signs half weight prevents that implementation detail from doubling WMC.
    normalized[0] = (0.5, 0.5)
    return normalized, auxiliary_count


def evaluate_tseitin_text(text, dimacs_weights):
    """Evaluate a d-DNNF dump for a weighted Tseitin CNF exactly.

    ``dimacs_weights`` must cover every declared CNF variable.  Original
    Bernoulli variables have weights summing to one; gate auxiliaries use
    ``(1, 1)``.  The scaled pass remains stable when thousands of auxiliaries
    would make a direct ``0.5 ** n`` normalization underflow.
    """
    normalized, auxiliary_count = _normalized_tseitin_weights(dimacs_weights)
    return _evaluate_text(
        text, normalized, scaled=True, scale_exponent=auxiliary_count
    )


def evaluate_file(path, input_weights):
    with open(path) as fh:
        return evaluate_text(fh.read(), input_weights)


def evaluate_tseitin_file(path, dimacs_weights):
    with open(path) as fh:
        return evaluate_tseitin_text(fh.read(), dimacs_weights)


def weights_from_dimacs(path):
    """Read ``c p weight <lit> <weight> 0`` comments into input-weight pairs.

    Auxiliary variables with ``(1,1)`` are retained for
    :func:`evaluate_tseitin_file`. Missing signs are rejected.
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
