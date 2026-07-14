"""Auditable treewidth bounds for Tseitin-CNF primal graphs.

The module is deliberately independent of the compiler and experiment harness.
It consumes the ``nvars`` and ``clauses`` fields returned by
``reference/export_cnf.py`` and constructs the standard CNF primal graph: one
vertex per DIMACS variable and an edge between every pair of variables that
co-occur in a clause.

Two deterministic proof objects are emitted:

* a min-fill elimination ordering, which certifies an upper bound; and
* a minor-min-width contraction sequence, whose intermediate minimum degrees
  certify a lower bound by minor monotonicity of treewidth.

Both verifiers rebuild the primal graph from the original CNF and replay every
step.  The JSON SHA-256 values use sorted keys, compact separators, UTF-8, and
no non-finite numbers, so certificates can be audited outside the process that
created them.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from typing import Any, Dict, FrozenSet, List, Optional, Set, Tuple


SCHEMA = "tseitin-cnf-primal-treewidth-evidence-v1"
GRAPH_SCHEMA = "cnf-primal-graph-v1"
UPPER_CERTIFICATE_SCHEMA = "min-fill-elimination-certificate-v1"
LOWER_CERTIFICATE_SCHEMA = "minor-min-width-certificate-v1"
GRAPH_DEFINITION = (
    "simple undirected CNF primal graph: vertices are DIMACS variables "
    "1..nvars; two distinct vertices are adjacent iff they co-occur in at "
    "least one clause"
)
UPPER_METHOD = (
    "deterministic-min-fill: minimize (missing-neighbor-edges, degree, vertex)"
)
LOWER_METHOD = (
    "deterministic-minor-min-width: choose minimum (degree, vertex), then "
    "contract into the neighbor minimizing (degree, vertex)"
)

_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")


class EvidenceError(ValueError):
    """Raised when a CNF or treewidth certificate is malformed or invalid."""


def _strict_int(value: Any, label: str, minimum: Optional[int] = None,
                maximum: Optional[int] = None) -> int:
    if type(value) is not int:  # ``bool`` is intentionally not an integer here.
        raise EvidenceError("%s must be an integer (bool is forbidden)" % label)
    if minimum is not None and value < minimum:
        raise EvidenceError("%s must be >= %d" % (label, minimum))
    if maximum is not None and value > maximum:
        raise EvidenceError("%s must be <= %d" % (label, maximum))
    return value


def _strict_sha256(value: Any, label: str) -> str:
    if type(value) is not str or _SHA256_RE.fullmatch(value) is None:
        raise EvidenceError("%s must be a lowercase hexadecimal SHA-256" % label)
    return value


def _require_mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise EvidenceError("%s must be a mapping" % label)
    if any(type(key) is not str for key in value):
        raise EvidenceError("%s keys must be strings" % label)
    return value


def _require_exact_keys(value: Mapping[str, Any], expected: Set[str],
                        label: str) -> None:
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise EvidenceError(
            "%s keys differ (missing=%r, extra=%r)" % (label, missing, extra)
        )


def _require_list(value: Any, label: str) -> List[Any]:
    if type(value) is not list:
        raise EvidenceError("%s must be a JSON list" % label)
    return value


def canonical_json_bytes(value: Any) -> bytes:
    """Return the single canonical JSON representation used for all hashes."""
    try:
        text = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise EvidenceError("value is not canonical-JSON serializable: %s" % exc)
    return text.encode("utf-8")


def canonical_json_sha256(value: Any) -> str:
    """Hash a value using :func:`canonical_json_bytes`."""
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def validate_cnf(nvars: Any, clauses: Any) -> Tuple[int, Tuple[Tuple[int, ...], ...]]:
    """Strictly validate and freeze an export_cnf-style CNF.

    Empty clauses are legal CNF clauses.  A variable may occur at most once in
    a clause, irrespective of sign; this rejects both repeated literals and
    tautological ``x / -x`` pairs.  Repeated clauses remain legal because they
    do not make the DIMACS input or its primal graph ambiguous.
    """
    count = _strict_int(nvars, "nvars", minimum=1)
    if not isinstance(clauses, Sequence) or isinstance(
            clauses, (str, bytes, bytearray)):
        raise EvidenceError("clauses must be a finite sequence")

    frozen: List[Tuple[int, ...]] = []
    for clause_index, clause in enumerate(clauses):
        if not isinstance(clause, Sequence) or isinstance(
                clause, (str, bytes, bytearray)):
            raise EvidenceError("clause %d must be a finite sequence" % clause_index)
        seen_variables: Set[int] = set()
        literals: List[int] = []
        for literal_index, literal in enumerate(clause):
            lit = _strict_int(
                literal,
                "clause %d literal %d" % (clause_index, literal_index),
            )
            if lit == 0:
                raise EvidenceError(
                    "clause %d literal %d must not be zero" %
                    (clause_index, literal_index)
                )
            variable = abs(lit)
            if variable > count:
                raise EvidenceError(
                    "clause %d literal %d is outside 1..nvars" %
                    (clause_index, literal_index)
                )
            if variable in seen_variables:
                raise EvidenceError(
                    "clause %d repeats variable %d" % (clause_index, variable)
                )
            seen_variables.add(variable)
            literals.append(lit)
        frozen.append(tuple(literals))
    return count, tuple(frozen)


def _extract_export(encoded: Any) -> Tuple[int, Tuple[Tuple[int, ...], ...]]:
    document = _require_mapping(encoded, "exported CNF")
    if "nvars" not in document or "clauses" not in document:
        raise EvidenceError("exported CNF must contain nvars and clauses")
    nvars, clauses = validate_cnf(document["nvars"], document["clauses"])
    if "nclauses" in document:
        declared = _strict_int(document["nclauses"], "nclauses", minimum=0)
        if declared != len(clauses):
            raise EvidenceError(
                "nclauses=%d does not match %d clauses" % (declared, len(clauses))
            )
    return nvars, clauses


def primal_graph(nvars: Any, clauses: Any) -> Dict[int, FrozenSet[int]]:
    """Build the validated simple primal graph as immutable adjacency sets."""
    count, frozen = validate_cnf(nvars, clauses)
    adjacency: Dict[int, Set[int]] = {vertex: set() for vertex in range(1, count + 1)}
    for clause in frozen:
        variables = sorted(abs(literal) for literal in clause)
        for left_index, left in enumerate(variables):
            for right in variables[left_index + 1:]:
                adjacency[left].add(right)
                adjacency[right].add(left)
    return {vertex: frozenset(sorted(neighbors))
            for vertex, neighbors in adjacency.items()}


def _mutable_graph(nvars: int, clauses: Tuple[Tuple[int, ...], ...]) -> Dict[int, Set[int]]:
    immutable = primal_graph(nvars, clauses)
    return {vertex: set(neighbors) for vertex, neighbors in immutable.items()}


def _edge_list(graph: Mapping[int, Set[int] | FrozenSet[int]]) -> List[List[int]]:
    return [[left, right] for left in sorted(graph)
            for right in sorted(graph[left]) if left < right]


def _graph_payload(nvars: int, clauses: Tuple[Tuple[int, ...], ...]) -> Dict[str, Any]:
    graph = _mutable_graph(nvars, clauses)
    return {
        "schema": GRAPH_SCHEMA,
        "definition": GRAPH_DEFINITION,
        "nodes": nvars,
        "edges": _edge_list(graph),
    }


def graph_sha256(nvars: Any, clauses: Any) -> str:
    """Return the canonical hash of the exact primal graph."""
    count, frozen = validate_cnf(nvars, clauses)
    return canonical_json_sha256(_graph_payload(count, frozen))


def _fill_edges(graph: Mapping[int, Set[int]], vertex: int) -> List[List[int]]:
    neighbors = sorted(graph[vertex])
    return [[left, right]
            for index, left in enumerate(neighbors)
            for right in neighbors[index + 1:]
            if right not in graph[left]]


def _eliminate(graph: Dict[int, Set[int]], vertex: int,
               fill_edges: Sequence[Sequence[int]]) -> None:
    neighbors = set(graph[vertex])
    for pair in fill_edges:
        left, right = pair
        graph[left].add(right)
        graph[right].add(left)
    for neighbor in neighbors:
        graph[neighbor].remove(vertex)
    del graph[vertex]


def min_fill_upper_certificate(nvars: Any, clauses: Any) -> Dict[str, Any]:
    """Construct a deterministic min-fill elimination upper-bound certificate."""
    count, frozen = validate_cnf(nvars, clauses)
    graph = _mutable_graph(count, frozen)
    ordering: List[int] = []
    steps: List[Dict[str, Any]] = []
    bound = 0
    while graph:
        candidates = []
        for vertex in sorted(graph):
            fill = _fill_edges(graph, vertex)
            candidates.append((len(fill), len(graph[vertex]), vertex, fill))
        _fill_count, degree, vertex, fill_edges = min(candidates)
        neighbors = sorted(graph[vertex])
        ordering.append(vertex)
        steps.append({
            "vertex": vertex,
            "neighbors": neighbors,
            "fill_edges": fill_edges,
            "width": degree,
        })
        bound = max(bound, degree)
        _eliminate(graph, vertex, fill_edges)
    return {
        "schema": UPPER_CERTIFICATE_SCHEMA,
        "method": UPPER_METHOD,
        "graph_sha256": canonical_json_sha256(_graph_payload(count, frozen)),
        "ordering": ordering,
        "steps": steps,
        "bound": bound,
    }


def _check_hash(certificate: Mapping[str, Any], expected_hash: Optional[Any],
                label: str) -> None:
    if expected_hash is None:
        return
    digest = _strict_sha256(expected_hash, label)
    actual = canonical_json_sha256(certificate)
    if actual != digest:
        raise EvidenceError("%s does not match certificate" % label)


def verify_upper_certificate(nvars: Any, clauses: Any, certificate: Any,
                             certificate_sha256: Optional[Any] = None) -> int:
    """Replay and verify a min-fill certificate; return its certified bound."""
    count, frozen = validate_cnf(nvars, clauses)
    cert = _require_mapping(certificate, "upper certificate")
    _require_exact_keys(cert, {
        "schema", "method", "graph_sha256", "ordering", "steps", "bound",
    }, "upper certificate")
    if cert["schema"] != UPPER_CERTIFICATE_SCHEMA or cert["method"] != UPPER_METHOD:
        raise EvidenceError("upper certificate schema or method is not supported")
    expected_graph_hash = canonical_json_sha256(_graph_payload(count, frozen))
    if _strict_sha256(cert["graph_sha256"], "upper graph_sha256") != expected_graph_hash:
        raise EvidenceError("upper certificate is bound to a different primal graph")
    ordering = _require_list(cert["ordering"], "upper ordering")
    steps = _require_list(cert["steps"], "upper steps")
    bound = _strict_int(cert["bound"], "upper bound", minimum=0,
                        maximum=max(0, count - 1))
    if len(ordering) != count or len(steps) != count:
        raise EvidenceError("upper certificate must contain one step per vertex")
    _check_hash(cert, certificate_sha256, "upper certificate_sha256")

    graph = _mutable_graph(count, frozen)
    replayed_order: List[int] = []
    replayed_bound = 0
    for index, raw_step in enumerate(steps):
        step = _require_mapping(raw_step, "upper step %d" % index)
        _require_exact_keys(step, {
            "vertex", "neighbors", "fill_edges", "width",
        }, "upper step %d" % index)
        candidate_rows = []
        for candidate in sorted(graph):
            candidate_fill = _fill_edges(graph, candidate)
            candidate_rows.append((
                len(candidate_fill), len(graph[candidate]), candidate, candidate_fill,
            ))
        _fill_count, expected_degree, expected_vertex, expected_fill = min(candidate_rows)
        vertex = _strict_int(step["vertex"], "upper step vertex", minimum=1,
                             maximum=count)
        if vertex != expected_vertex:
            raise EvidenceError("upper step %d is not the deterministic min-fill choice" % index)
        neighbors = _require_list(step["neighbors"], "upper step neighbors")
        if neighbors != sorted(graph[vertex]):
            raise EvidenceError("upper step %d has incorrect neighbors" % index)
        for neighbor_index, neighbor in enumerate(neighbors):
            _strict_int(neighbor, "upper neighbor %d" % neighbor_index,
                        minimum=1, maximum=count)
        fill_edges = _require_list(step["fill_edges"], "upper step fill_edges")
        if fill_edges != expected_fill:
            raise EvidenceError("upper step %d has incorrect fill edges" % index)
        for edge_index, edge in enumerate(fill_edges):
            pair = _require_list(edge, "upper fill edge %d" % edge_index)
            if len(pair) != 2:
                raise EvidenceError("upper fill edge %d must have two vertices" % edge_index)
            _strict_int(pair[0], "upper fill edge left", minimum=1, maximum=count)
            _strict_int(pair[1], "upper fill edge right", minimum=1, maximum=count)
        width = _strict_int(step["width"], "upper step width", minimum=0,
                            maximum=max(0, count - 1))
        if width != expected_degree or width != len(neighbors):
            raise EvidenceError("upper step %d has incorrect width" % index)
        replayed_order.append(vertex)
        replayed_bound = max(replayed_bound, width)
        _eliminate(graph, vertex, fill_edges)

    if ordering != replayed_order:
        raise EvidenceError("upper ordering does not match the replayed steps")
    for index, vertex in enumerate(ordering):
        _strict_int(vertex, "upper ordering %d" % index, minimum=1, maximum=count)
    if replayed_bound != bound:
        raise EvidenceError("upper bound does not match maximum elimination width")
    return bound


def _lower_choice(graph: Mapping[int, Set[int]]) -> Tuple[int, int, Optional[int], List[int]]:
    vertex = min(graph, key=lambda item: (len(graph[item]), item))
    degree = len(graph[vertex])
    if degree == 0:
        return vertex, degree, None, []
    target = min(
        graph[vertex],
        key=lambda neighbor: (len(graph[neighbor]), neighbor),
    )
    common = sorted(graph[vertex].intersection(graph[target]))
    return vertex, degree, target, common


def _contract(graph: Dict[int, Set[int]], vertex: int, target: int) -> None:
    merged_neighbors = (graph[vertex] | graph[target]) - {vertex, target}
    for neighbor in merged_neighbors:
        graph[neighbor].discard(vertex)
        graph[neighbor].add(target)
    del graph[vertex]
    graph[target] = set(merged_neighbors)


def minor_min_width_lower_certificate(nvars: Any, clauses: Any) -> Dict[str, Any]:
    """Construct a deterministic minor-min-width lower-bound certificate."""
    count, frozen = validate_cnf(nvars, clauses)
    graph = _mutable_graph(count, frozen)
    steps: List[Dict[str, Any]] = []
    bound = 0
    while graph:
        vertex, degree, target, common = _lower_choice(graph)
        neighbors = sorted(graph[vertex])
        bound = max(bound, degree)
        if target is None:
            action = "delete-isolated"
            del graph[vertex]
        else:
            action = "contract-edge"
            _contract(graph, vertex, target)
        steps.append({
            "vertex": vertex,
            "degree": degree,
            "neighbors": neighbors,
            "action": action,
            "target": target,
            "common_neighbors": common,
        })
    return {
        "schema": LOWER_CERTIFICATE_SCHEMA,
        "method": LOWER_METHOD,
        "graph_sha256": canonical_json_sha256(_graph_payload(count, frozen)),
        "steps": steps,
        "bound": bound,
    }


def verify_lower_certificate(nvars: Any, clauses: Any, certificate: Any,
                             certificate_sha256: Optional[Any] = None) -> int:
    """Replay and verify a minor-min-width certificate; return its lower bound."""
    count, frozen = validate_cnf(nvars, clauses)
    cert = _require_mapping(certificate, "lower certificate")
    _require_exact_keys(cert, {
        "schema", "method", "graph_sha256", "steps", "bound",
    }, "lower certificate")
    if cert["schema"] != LOWER_CERTIFICATE_SCHEMA or cert["method"] != LOWER_METHOD:
        raise EvidenceError("lower certificate schema or method is not supported")
    expected_graph_hash = canonical_json_sha256(_graph_payload(count, frozen))
    if _strict_sha256(cert["graph_sha256"], "lower graph_sha256") != expected_graph_hash:
        raise EvidenceError("lower certificate is bound to a different primal graph")
    steps = _require_list(cert["steps"], "lower steps")
    bound = _strict_int(cert["bound"], "lower bound", minimum=0,
                        maximum=max(0, count - 1))
    if not steps or len(steps) > count:
        raise EvidenceError("lower certificate has an impossible number of steps")
    _check_hash(cert, certificate_sha256, "lower certificate_sha256")

    graph = _mutable_graph(count, frozen)
    replayed_bound = 0
    for index, raw_step in enumerate(steps):
        if not graph:
            raise EvidenceError("lower certificate continues after the graph is empty")
        step = _require_mapping(raw_step, "lower step %d" % index)
        _require_exact_keys(step, {
            "vertex", "degree", "neighbors", "action", "target",
            "common_neighbors",
        }, "lower step %d" % index)
        expected_vertex, expected_degree, expected_target, expected_common = _lower_choice(graph)
        vertex = _strict_int(step["vertex"], "lower step vertex", minimum=1,
                             maximum=count)
        degree = _strict_int(step["degree"], "lower step degree", minimum=0,
                             maximum=max(0, count - 1))
        if vertex != expected_vertex or degree != expected_degree:
            raise EvidenceError("lower step %d is not the deterministic min-width choice" % index)
        neighbors = _require_list(step["neighbors"], "lower step neighbors")
        if neighbors != sorted(graph[vertex]):
            raise EvidenceError("lower step %d has incorrect neighbors" % index)
        for neighbor_index, neighbor in enumerate(neighbors):
            _strict_int(neighbor, "lower neighbor %d" % neighbor_index,
                        minimum=1, maximum=count)
        common = _require_list(step["common_neighbors"], "lower common_neighbors")
        if common != expected_common:
            raise EvidenceError("lower step %d has incorrect common neighbors" % index)
        for common_index, neighbor in enumerate(common):
            _strict_int(neighbor, "lower common neighbor %d" % common_index,
                        minimum=1, maximum=count)
        action = step["action"]
        if type(action) is not str:
            raise EvidenceError("lower action must be a string")
        target = step["target"]
        if expected_target is None:
            if action != "delete-isolated" or target is not None:
                raise EvidenceError("lower isolated step %d has incorrect action" % index)
            del graph[vertex]
        else:
            checked_target = _strict_int(target, "lower contraction target",
                                         minimum=1, maximum=count)
            if action != "contract-edge" or checked_target != expected_target:
                raise EvidenceError("lower step %d has incorrect contraction target" % index)
            if checked_target not in graph[vertex]:
                raise EvidenceError("lower step %d does not contract an edge" % index)
            _contract(graph, vertex, checked_target)
        replayed_bound = max(replayed_bound, degree)

    if graph:
        raise EvidenceError("lower certificate stops before the graph is empty")
    if replayed_bound != bound:
        raise EvidenceError("lower bound does not match the replayed minimum degrees")
    return bound


def certify_cnf(nvars: Any, clauses: Any) -> Dict[str, Any]:
    """Return bounds, proof objects, and canonical hashes for a validated CNF."""
    count, frozen = validate_cnf(nvars, clauses)
    graph_payload = _graph_payload(count, frozen)
    upper_certificate = min_fill_upper_certificate(count, frozen)
    lower_certificate = minor_min_width_lower_certificate(count, frozen)
    upper = verify_upper_certificate(count, frozen, upper_certificate)
    lower = verify_lower_certificate(count, frozen, lower_certificate)
    if lower > upper:
        raise EvidenceError("certified lower bound exceeds certified upper bound")
    return {
        "schema": SCHEMA,
        "graph_definition": GRAPH_DEFINITION,
        "nodes": count,
        "edges": len(graph_payload["edges"]),
        "clauses": len(frozen),
        "cnf_sha256": canonical_json_sha256({
            "nvars": count,
            "clauses": [list(clause) for clause in frozen],
        }),
        "graph_sha256": canonical_json_sha256(graph_payload),
        "lower": lower,
        "upper": upper,
        "lower_certificate_sha256": canonical_json_sha256(lower_certificate),
        "upper_certificate_sha256": canonical_json_sha256(upper_certificate),
        "lower_certificate": lower_certificate,
        "upper_certificate": upper_certificate,
    }


def analyze_export(encoded: Any) -> Dict[str, Any]:
    """Certify the ``nvars``/``clauses`` fields returned by ``export_cnf.export``."""
    nvars, clauses = _extract_export(encoded)
    return certify_cnf(nvars, clauses)


def verify_evidence(encoded: Any, evidence: Any) -> Tuple[int, int]:
    """Independently verify a complete evidence document against an exported CNF."""
    nvars, clauses = _extract_export(encoded)
    document = _require_mapping(evidence, "treewidth evidence")
    _require_exact_keys(document, {
        "schema", "graph_definition", "nodes", "edges", "clauses",
        "cnf_sha256", "graph_sha256", "lower", "upper",
        "lower_certificate_sha256", "upper_certificate_sha256",
        "lower_certificate", "upper_certificate",
    }, "treewidth evidence")
    if document["schema"] != SCHEMA or document["graph_definition"] != GRAPH_DEFINITION:
        raise EvidenceError("treewidth evidence schema or graph definition is unsupported")

    graph_payload = _graph_payload(nvars, clauses)
    edges = len(graph_payload["edges"])
    if _strict_int(document["nodes"], "evidence nodes", minimum=1) != nvars:
        raise EvidenceError("evidence node count does not match CNF")
    if _strict_int(document["edges"], "evidence edges", minimum=0) != edges:
        raise EvidenceError("evidence edge count does not match primal graph")
    if _strict_int(document["clauses"], "evidence clauses", minimum=0) != len(clauses):
        raise EvidenceError("evidence clause count does not match CNF")
    expected_cnf_hash = canonical_json_sha256({
        "nvars": nvars,
        "clauses": [list(clause) for clause in clauses],
    })
    if _strict_sha256(document["cnf_sha256"], "evidence cnf_sha256") != expected_cnf_hash:
        raise EvidenceError("evidence is bound to a different CNF")
    expected_graph_hash = canonical_json_sha256(graph_payload)
    if _strict_sha256(document["graph_sha256"], "evidence graph_sha256") != expected_graph_hash:
        raise EvidenceError("evidence is bound to a different primal graph")

    lower_hash = _strict_sha256(
        document["lower_certificate_sha256"], "lower certificate_sha256"
    )
    upper_hash = _strict_sha256(
        document["upper_certificate_sha256"], "upper certificate_sha256"
    )
    lower = verify_lower_certificate(
        nvars, clauses, document["lower_certificate"], lower_hash
    )
    upper = verify_upper_certificate(
        nvars, clauses, document["upper_certificate"], upper_hash
    )
    if _strict_int(document["lower"], "evidence lower", minimum=0) != lower:
        raise EvidenceError("evidence lower bound differs from certificate")
    if _strict_int(document["upper"], "evidence upper", minimum=0) != upper:
        raise EvidenceError("evidence upper bound differs from certificate")
    if lower > upper:
        raise EvidenceError("certified lower bound exceeds certified upper bound")
    return lower, upper


__all__ = [
    "EvidenceError",
    "GRAPH_DEFINITION",
    "GRAPH_SCHEMA",
    "LOWER_CERTIFICATE_SCHEMA",
    "SCHEMA",
    "UPPER_CERTIFICATE_SCHEMA",
    "analyze_export",
    "canonical_json_bytes",
    "canonical_json_sha256",
    "certify_cnf",
    "graph_sha256",
    "min_fill_upper_certificate",
    "minor_min_width_lower_certificate",
    "primal_graph",
    "validate_cnf",
    "verify_evidence",
    "verify_lower_certificate",
    "verify_upper_certificate",
]
