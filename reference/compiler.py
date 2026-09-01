"""Production multi-root BDD compilation.

The public operation is :func:`compile_many`: it compiles a mapping of answer
identifiers to circuit roots either into one shared CUDD manager or into one
manager per root.  Roots remain distinct outputs; multi-root compilation never
combines them with a Boolean OR/AND.

``backend="cudd"`` is the production path.  ``backend="oracle"`` exists only
for dependency-free correctness tests and wraps :mod:`compile_bdd`.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
import time
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple
import warnings

import compile_bdd


class BackendUnavailable(RuntimeError):
    """Raised when the requested native compiler cannot be imported."""


def _stable_text(value: Any) -> str:
    """A stable ordering key for the value shapes used by circuit builders."""
    if value is None or isinstance(value, (bool, int, float, str, bytes)):
        return type(value).__name__ + ":" + repr(value)
    if isinstance(value, (tuple, list)):
        return type(value).__name__ + "[" + ",".join(_stable_text(x) for x in value) + "]"
    if isinstance(value, (set, frozenset)):
        return type(value).__name__ + "{" + ",".join(
            sorted(_stable_text(x) for x in value)) + "}"
    if isinstance(value, dict):
        pairs = sorted((_stable_text(k), _stable_text(v)) for k, v in value.items())
        return "dict{" + ",".join(k + "=" + v for k, v in pairs) + "}"
    return type(value).__module__ + "." + type(value).__qualname__ + ":" + repr(value)


def _children(op: str, payload: Any) -> Tuple[Any, ...]:
    if op in ("leaf", "const"):
        return ()
    if op == "not":
        return (payload,)
    if op in ("times", "plus"):
        return tuple(payload)
    if op == "minus":
        if len(payload) != 2:
            raise ValueError("minus gate must have exactly two children")
        return payload[0], payload[1]
    raise ValueError("unknown circuit operation: %r" % (op,))


def _sorted_children(op: str, payload: Any) -> Tuple[Any, ...]:
    children = _children(op, payload)
    if op in ("times", "plus"):
        return tuple(sorted(children, key=_stable_text))
    return children


def deterministic_order(circ: Mapping[Any, Tuple[str, Any]],
                        roots: Mapping[Any, Any]) -> Tuple[str, ...]:
    """Deterministic DFS first-appearance order over the union of root cones."""
    order: List[str] = []
    tokens = set()
    seen = set()
    root_values = [roots[key] for key in sorted(roots, key=_stable_text)]
    for root in root_values:
        stack = [root]
        while stack:
            node = stack.pop()
            if node in seen:
                continue
            seen.add(node)
            try:
                op, payload = circ[node]
            except KeyError as exc:
                raise ValueError("circuit references missing gate %r" % (node,)) from exc
            if op == "leaf":
                if not isinstance(payload, str):
                    raise TypeError("BDD variable names must be strings: %r" % (payload,))
                if payload not in tokens:
                    tokens.add(payload)
                    order.append(payload)
                continue
            children = _sorted_children(op, payload)
            stack.extend(reversed(children))
    return tuple(order)


def _source_stats(circ: Mapping[Any, Tuple[str, Any]], roots: Iterable[Any]) -> Tuple[int, int]:
    seen = set()
    edges = 0
    stack = list(roots)
    while stack:
        node = stack.pop()
        if node in seen:
            continue
        seen.add(node)
        try:
            op, payload = circ[node]
        except KeyError as exc:
            raise ValueError("circuit references missing gate %r" % (node,)) from exc
        children = _children(op, payload)
        edges += len(children)
        stack.extend(children)
    return len(seen), edges


def _order_fingerprint(order: Sequence[str]) -> str:
    digest = hashlib.sha256()
    for variable in order:
        encoded = variable.encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    return digest.hexdigest()


def _compile_roots(circ: Mapping[Any, Tuple[str, Any]], roots: Iterable[Any],
                   manager: Any, backend: str) -> Dict[Any, Any]:
    """Iterative source-DAG compilation with one memo for all supplied roots."""
    memo: Dict[Any, Any] = {}
    visiting = set()

    if backend == "cudd":
        true, false = manager.true, manager.false
        var = manager.var
        conjunction = lambda left, right: left & right
        disjunction = lambda left, right: left | right
        negate = lambda value: ~value
    else:
        true, false = manager.TRUE, manager.FALSE
        var = manager.var
        conjunction = manager.AND
        disjunction = manager.OR
        negate = manager.NOT

    def finish(node: Any) -> Any:
        op, payload = circ[node]
        if op == "leaf":
            return var(payload)
        if op == "const":
            if payload not in (0, 1, False, True):
                raise ValueError("const payload must be Boolean: %r" % (payload,))
            return true if payload else false
        children = _sorted_children(op, payload)
        if op == "times":
            result = true
            # The variable order follows ``children``.  Build the suffix first
            # and prepend each earlier child so that a wide monotone gate grows
            # in variable-order direction.  Folding the same list left to
            # right repeatedly applies a later variable above an existing
            # earlier-variable chain in CUDD, which turns a simple wide gate
            # into quadratic Apply work and can exhaust the native stack.
            for child in reversed(children):
                result = conjunction(memo[child], result)
            return result
        if op == "plus":
            result = false
            for child in reversed(children):
                result = disjunction(memo[child], result)
            return result
        if op == "not":
            return negate(memo[children[0]])
        if op == "minus":
            return conjunction(memo[children[0]], negate(memo[children[1]]))
        raise ValueError("unknown circuit operation: %r" % (op,))

    result = {}
    for root in roots:
        if root not in circ:
            raise ValueError("unknown circuit root %r" % (root,))
        stack = [(root, False)]
        while stack:
            node, expanded = stack.pop()
            if node in memo:
                continue
            if expanded:
                visiting.remove(node)
                memo[node] = finish(node)
                continue
            if node in visiting:
                raise ValueError("cycle detected in circuit at gate %r" % (node,))
            try:
                op, payload = circ[node]
            except KeyError as exc:
                raise ValueError("circuit references missing gate %r" % (node,)) from exc
            visiting.add(node)
            stack.append((node, True))
            for child in reversed(_sorted_children(op, payload)):
                if child in visiting:
                    raise ValueError("cycle detected in circuit at gate %r" % (child,))
                if child not in memo:
                    stack.append((child, False))
        result[root] = memo[root]
    return result


def _is_cudd_negated(node: Any) -> bool:
    try:
        return bool(node.negated)
    except AttributeError:
        return bool(int(node) & 1)


def _cudd_regular(node: Any) -> Any:
    return ~node if _is_cudd_negated(node) else node


def _cudd_size_many(manager: Any, roots: Iterable[Any]) -> int:
    """Physical CUDD decision nodes, with the complement flag masked out."""
    seen = set()
    stack = list(roots)
    while stack:
        node = stack.pop()
        if node == manager.false or node == manager.true:
            continue
        regular = _cudd_regular(node)
        key = int(regular) & ~1
        if key in seen:
            continue
        seen.add(key)
        _, low, high = manager.succ(regular)
        if low is None or high is None:
            raise RuntimeError("CUDD returned a non-decision node during traversal")
        stack.extend((low, high))
    return len(seen)


def _probability(weights: Mapping[str, float], variable: str) -> float:
    try:
        raw = weights[variable]
    except KeyError as exc:
        raise KeyError("missing probability for token %r" % (variable,)) from exc
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise TypeError("probability for %r must be numeric" % (variable,))
    value = float(raw)
    if not math.isfinite(value) or not 0.0 <= value <= 1.0:
        raise ValueError("probability for %r is outside [0, 1]: %r" % (variable, raw))
    return value


def _cudd_wmc_many(manager: Any, roots: Mapping[Any, Any],
                   weights: Mapping[str, float]) -> Tuple[Dict[Any, float], int]:
    # Keep the probability of both the function and its complement at every
    # edge.  CUDD represents many rare functions as complemented edges.  The
    # tempting implementation ``P(~u) = 1.0 - P(u)`` catastrophically loses a
    # small but perfectly representable probability when ``P(u)`` rounds to
    # 1.0 (for example a 64-variable conjunction around 1e-28).  Propagating
    # ``(P(true), P(false))`` pairs makes complementation an exact tuple swap
    # and never recovers a rare event by subtracting nearly equal floats.
    memo: Dict[int, Tuple[float, float]] = {}
    visited = set()
    forks: Dict[int, Tuple[int, Any, Any]] = {}

    def value_pair(node: Any) -> Tuple[float, float]:
        if node == manager.false:
            return 0.0, 1.0
        if node == manager.true:
            return 1.0, 0.0
        return memo[int(node)]

    # A CUDD complement is an edge flag.  `succ(~u)` returns the children of
    # regular `u`, so a complemented node is an explicit dependency on `~node`
    # followed by `1 - value`.  The stack keeps this semantics without Python
    # recursion and shares one memo across the complete output vector.
    stack = [(root, False) for root in reversed(list(roots.values()))]
    while stack:
        node, expanded = stack.pop()
        if node == manager.false or node == manager.true:
            continue
        signed_key = int(node)
        if signed_key in memo:
            continue
        if expanded:
            if _is_cudd_negated(node):
                regular_true, regular_false = value_pair(~node)
                memo[signed_key] = regular_false, regular_true
                continue
            level, low, high = forks.pop(signed_key)
            variable = manager.var_at_level(level)
            probability = _probability(weights, variable)
            low_true, low_false = value_pair(low)
            high_true, high_false = value_pair(high)
            memo[signed_key] = (
                (1.0 - probability) * low_true + probability * high_true,
                (1.0 - probability) * low_false + probability * high_false,
            )
            continue

        stack.append((node, True))
        if _is_cudd_negated(node):
            regular = ~node
            if (regular != manager.false and regular != manager.true
                    and int(regular) not in memo):
                stack.append((regular, False))
            continue

        physical_key = signed_key & ~1
        visited.add(physical_key)
        level, low, high = manager.succ(node)
        if low is None or high is None:
            raise RuntimeError("CUDD returned a non-decision node during WMC")
        forks[signed_key] = (level, low, high)
        for child in (high, low):
            if (child != manager.false and child != manager.true
                    and int(child) not in memo):
                stack.append((child, False))

    return ({key: value_pair(root)[0] for key, root in roots.items()}, len(visited))


@dataclass
class _ManagerGroup:
    manager: Any
    roots: Dict[Any, Any]
    order: Tuple[str, ...]


class CompiledBatch:
    """A set of independent output roots backed by one or more BDD managers."""

    def __init__(self, backend: str, mode: str, groups: List[_ManagerGroup],
                 root_sizes: Dict[Any, int], metrics: Dict[str, Any]):
        self.backend = backend
        self.mode = mode
        self._groups = groups
        self.roots: Dict[Any, Any] = {}
        for group in groups:
            for key, root in group.roots.items():
                self.roots[key] = root
        self._root_sizes = root_sizes
        self.metrics = metrics

    def root_size(self, key: Any) -> int:
        return self._root_sizes[key]

    def root_sizes(self) -> Dict[Any, int]:
        return {key: self.root_size(key) for key in self.roots}

    def wmc_many(self, weights: Mapping[str, float]) -> Dict[Any, float]:
        """Evaluate all outputs; shared mode reuses one node-value memo."""
        started = time.perf_counter()
        result: Dict[Any, float] = {}
        visited = 0
        for group in self._groups:
            if self.backend == "cudd":
                values, count = _cudd_wmc_many(group.manager, group.roots, weights)
                visited += count
            else:
                values = group.manager.wmc_many(group.roots, weights)
                count = group.manager.size_many(group.roots.values())
                visited += count
            result.update(values)
        self.metrics["wmc_ms"] = (time.perf_counter() - started) * 1000.0
        self.metrics["wmc_visited_nodes"] = visited
        return result


def _load_cudd() -> Tuple[Any, str]:
    try:
        import dd.cudd as cudd
    except (ImportError, OSError) as exc:
        raise BackendUnavailable(
            "CUDD backend is unavailable; install Python 3.11+ dependencies with "
            "'python -m pip install -r reference/requirements-production.txt'. "
            "Use --oracle only for correctness tests."
        ) from exc
    return cudd.BDD, str(getattr(cudd, "__version__", "unknown"))


def _manager_metrics(backend: str, groups: Sequence[_ManagerGroup]) -> Dict[str, Any]:
    if backend != "cudd" or not groups:
        return {
            "manager_memory_bytes": None,
            "manager_peak_live_nodes_upper_bound": None,
            "manager_peak_live_nodes_max": None,
            "manager_current_nodes": None,
            "manager_reorderings": 0,
            "manager_reordering_seconds": 0.0,
        }
    memory = 0.0
    peak_live_sum = 0
    peak_live_max = 0
    current_nodes = 0
    reorderings = 0
    reorder_seconds = 0.0
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        for group in groups:
            stats = group.manager.statistics(exact_node_count=True)
            memory += float(stats.get("mem", 0.0))
            group_peak = int(stats.get("peak_live_nodes", 0))
            # Per-root mode deliberately retains all independent managers in
            # the returned batch.  Summing their individual high-water marks
            # is the conservative batch peak; the max is also exposed so the
            # two quantities cannot be mistaken for each other in experiments.
            peak_live_sum += group_peak
            peak_live_max = max(peak_live_max, group_peak)
            current_nodes += int(stats.get("n_nodes", 0))
            reorderings += int(stats.get("n_reorderings", 0))
            reorder_seconds += float(stats.get("reordering_time", 0.0))
    return {
        "manager_memory_bytes": int(memory),
        "manager_peak_live_nodes_upper_bound": peak_live_sum,
        "manager_peak_live_nodes_max": peak_live_max,
        "manager_current_nodes": current_nodes,
        "manager_reorderings": reorderings,
        "manager_reordering_seconds": reorder_seconds,
    }


def compile_many(circ: Mapping[Any, Tuple[str, Any]], roots: Mapping[Any, Any],
                 mode: str = "shared", backend: str = "cudd",
                 order: Optional[Sequence[str]] = None,
                 dynamic_reordering: bool = False,
                 record_order_fingerprint: bool = True) -> CompiledBatch:
    """Compile ``answer key -> circuit root`` into a multi-output BDD batch.

    ``shared`` uses one manager and one source-gate memo for the complete root
    vector.  ``per-root`` uses one manager per output.  Both modes derive the
    same deterministic global variable order; per-root managers receive the
    subsequence needed by their root, so mode comparisons do not conflate
    cross-root sharing with a different ordering heuristic.  Set
    ``record_order_fingerprint=False`` for protocols that prohibit computing
    digests; in that mode no order digest is computed or included in metrics.
    """
    if mode not in ("shared", "per-root"):
        raise ValueError("compile mode must be 'shared' or 'per-root'")
    if backend not in ("cudd", "oracle"):
        raise ValueError("backend must be 'cudd' or 'oracle'")
    if backend == "oracle" and dynamic_reordering:
        raise ValueError("the correctness oracle does not support dynamic reordering")
    if not isinstance(roots, Mapping):
        raise TypeError("roots must be a mapping from answer identifiers to gates")

    # Loading a native extension is process start-up, not knowledge-compilation
    # work.  Keep it outside `compile_ms` so the first measured batch is
    # comparable to later batches in the same process.
    backend_version = "bundled"
    manager_type = None
    if backend == "cudd":
        manager_type, backend_version = _load_cudd()

    source_started = time.perf_counter()
    prepare_started = source_started
    support_order = deterministic_order(circ, roots)
    support = set(support_order)
    if order is None:
        global_order = support_order
    else:
        global_order = tuple(order)
        if any(not isinstance(variable, str) for variable in global_order):
            raise TypeError("all BDD variable names must be strings")
        if len(set(global_order)) != len(global_order):
            raise ValueError("variable order contains duplicates")
        missing = support - set(global_order)
        if missing:
            preview = ", ".join(sorted(missing)[:5])
            raise ValueError("variable order omits %d token(s): %s" % (len(missing), preview))

    source_gates, source_edges = _source_stats(circ, roots.values())
    root_keys = sorted(roots, key=_stable_text)
    local_orders: Dict[Any, Tuple[str, ...]] = {}
    if mode == "per-root":
        for key in root_keys:
            local_support = set(deterministic_order(circ, {key: roots[key]}))
            local_orders[key] = tuple(
                variable for variable in global_order if variable in local_support
            )
    prepare_ms = (time.perf_counter() - prepare_started) * 1000.0
    groups: List[_ManagerGroup] = []

    def new_manager(local_order: Tuple[str, ...]) -> Any:
        if backend == "cudd":
            manager = manager_type()
            manager.configure(reordering=bool(dynamic_reordering))
            if local_order:
                manager.declare(*local_order)
            return manager
        return compile_bdd.ROBDD(local_order)

    backend_started = time.perf_counter()
    if not roots:
        pass
    elif mode == "shared":
        manager = new_manager(global_order)
        source_to_compiled = _compile_roots(
            circ, (roots[key] for key in root_keys), manager, backend)
        root_map = {key: source_to_compiled[roots[key]] for key in root_keys}
        groups.append(_ManagerGroup(manager, root_map, global_order))
    else:
        for key in root_keys:
            root = roots[key]
            local_order = local_orders[key]
            manager = new_manager(local_order)
            compiled = _compile_roots(circ, (root,), manager, backend)[root]
            groups.append(_ManagerGroup(manager, {key: compiled}, local_order))
    backend_compile_ms = (time.perf_counter() - backend_started) * 1000.0

    inspect_started = time.perf_counter()
    compiled_unique = 0
    root_node_sum = 0
    root_sizes: Dict[Any, int] = {}
    for group in groups:
        if backend == "cudd":
            compiled_unique += _cudd_size_many(group.manager, group.roots.values())
            sizes = {key: _cudd_size_many(group.manager, (root,))
                     for key, root in group.roots.items()}
        else:
            compiled_unique += group.manager.size_many(group.roots.values())
            sizes = {key: group.manager.size(root) for key, root in group.roots.items()}
        root_sizes.update(sizes)
        root_node_sum += sum(sizes.values())

    manager_metrics = _manager_metrics(backend, groups)
    inspect_ms = (time.perf_counter() - inspect_started) * 1000.0
    source_to_result_ms = (time.perf_counter() - source_started) * 1000.0
    metrics: Dict[str, Any] = {
        "backend": backend,
        "backend_version": backend_version,
        "mode": mode,
        "dynamic_reordering": bool(dynamic_reordering),
        "root_count": len(roots),
        "manager_count": len(groups),
        "variable_count": len(support),
        "declared_variable_count": len(global_order),
        "source_gate_count": source_gates,
        "source_edge_count": source_edges,
        "compiled_nodes_unique": compiled_unique,
        "compiled_nodes_sum_roots": root_node_sum,
        "sharing_savings_nodes": root_node_sum - compiled_unique,
        "sharing_ratio": (root_node_sum / compiled_unique if compiled_unique else 1.0),
        # `compile_ms` remains as a backwards-compatible alias for the full
        # source-to-result boundary.  Publication harnesses use the explicit
        # phase fields below and do not compare this alias across backends.
        "compile_ms": source_to_result_ms,
        "prepare_ms": prepare_ms,
        "backend_compile_ms": backend_compile_ms,
        "inspect_ms": inspect_ms,
        "source_to_result_ms": source_to_result_ms,
        "wmc_ms": None,
        "wmc_visited_nodes": None,
    }
    if record_order_fingerprint:
        metrics["order_sha256"] = _order_fingerprint(global_order)
    metrics.update(manager_metrics)
    return CompiledBatch(backend, mode, groups, root_sizes, metrics)
