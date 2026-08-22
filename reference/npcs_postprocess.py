#!/usr/bin/env python3
"""Post-process the provenance strings returned by the NPCS rewriter.

The NPCS rewriter returns one spm-semiring provenance string per answer.  This
module turns one complete SPARQL Results JSON response into a query-global,
multi-root Boolean circuit DAG:

    response -> expression trees -> Boolean normalization
             -> hash-consed expression DAG -> explicit answer roots

Every textual occurrence is counted in the pre-hash-consing tree metrics.  A
single exact structural-interning table is then shared by every answer of the
query.  Equal tuple tokens remain one Boolean variable.  Structural identity is
established by complete Python tuple/string equality; no cryptographic digest is
computed or stored.

Each answer receives one non-interned unary ``or`` root after expression
hash-consing.  This is the Boolean counterpart of SPARQLcirc's per-answer
``Plus`` gate: it preserves answer identity and bindings even when distinct
answers have the same provenance expression.

The optional algebraic-factorization track is intentionally not implemented
here.  Hash-consing merges equal subtrees; it does not rewrite
``(a & b) | (a & c)`` into ``a & (b | c)``.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import re
import sys
import time
from typing import Any, Dict, Iterable, Iterator, List, Mapping, Optional, Pattern, Sequence, Tuple


US = "\x1f"
XSD_STRING = "http://www.w3.org/2001/XMLSchema#string"
RDF_LANGSTRING = "http://www.w3.org/1999/02/22-rdf-syntax-ns#langString"
LEGACY_PROVENANCE_VARIABLE = "finalprovennacevariable"


class ProvenanceFormatError(ValueError):
    """The endpoint response or an NPCS polynomial violates the accepted format."""


@dataclass(frozen=True)
class _Node:
    op: str
    payload: Any


class _Arena:
    """An occurrence arena: adding an equal node still creates a new occurrence."""

    def __init__(self) -> None:
        self.nodes: List[_Node] = []

    def add(self, op: str, payload: Any) -> int:
        node_id = len(self.nodes)
        self.nodes.append(_Node(op, payload))
        return node_id

    def children(self, node_id: int) -> Tuple[int, ...]:
        node = self.nodes[node_id]
        if node.op in ("leaf", "const"):
            return ()
        if node.op in ("plus", "times", "and", "or"):
            return tuple(node.payload)
        if node.op == "minus":
            return tuple(node.payload)
        if node.op == "not":
            return (node.payload,)
        raise ProvenanceFormatError("unknown expression operation: %r" % (node.op,))

    def reachable(self, root: int) -> set[int]:
        seen: set[int] = set()
        stack = [root]
        while stack:
            node_id = stack.pop()
            if node_id in seen:
                continue
            if node_id < 0 or node_id >= len(self.nodes):
                raise ProvenanceFormatError("expression references an invalid node id")
            seen.add(node_id)
            stack.extend(self.children(node_id))
        return seen

    def stats(self, root: int) -> Tuple[int, int]:
        reachable = self.reachable(root)
        edges = sum(len(self.children(node_id)) for node_id in reachable)
        return len(reachable), edges


@dataclass
class _Frame:
    op: str
    offset: int
    groups: List[List[int]]


def _lex(source: str) -> Iterator[Tuple[str, Optional[str], int]]:
    """Tokenize the concrete strings emitted by ``npcs.rewrite.Prov``.

    GROUP_CONCAT uses whitespace between adjacent expressions.  Commas delimit
    product and monus operands.  WatDiv's frozen occurrence tokens are
    ``urn:t:<integer>`` and therefore contain none of these delimiters.
    """
    index = 0
    length = len(source)
    while index < length:
        if source[index].isspace():
            index += 1
            continue
        if source.startswith("⊕(", index):
            yield "open", "plus", index
            index += 2
            continue
        opened = None
        for marker, operation in (("(⊗", "times"), ("(⊖", "minus"), ("(⊕", "plus")):
            if source.startswith(marker, index):
                opened = operation
                marker_length = len(marker)
                break
        if opened is not None:
            yield "open", opened, index
            index += marker_length
            continue
        character = source[index]
        if character == ",":
            yield "comma", None, index
            index += 1
            continue
        if character == ")":
            yield "close", None, index
            index += 1
            continue
        if character == "(" or character in "⊕⊗⊖":
            raise ProvenanceFormatError(
                "unexpected provenance delimiter at character %d" % index
            )
        start = index
        while index < length:
            character = source[index]
            if character.isspace() or character in "(),⊕⊗⊖":
                break
            index += 1
        if index == start:
            raise ProvenanceFormatError("cannot tokenize character %d" % start)
        yield "atom", source[start:index], start


def _slot(arena: _Arena, expressions: Sequence[int]) -> int:
    if not expressions:
        return arena.add("plus", ())
    if len(expressions) == 1:
        return expressions[0]
    # GROUP_CONCAT can place several subtrahend polynomials in one monus slot.
    # Their spm/Boolean meaning is a sum/disjunction.
    return arena.add("plus", tuple(expressions))


def parse_expression(source: str, token_pattern: Optional[Pattern[str]] = None) -> Tuple[_Arena, int]:
    """Parse one complete NPCS polynomial into a non-sharing occurrence tree."""
    if not isinstance(source, str) or not source.strip():
        raise ProvenanceFormatError("provenance string is empty")
    arena = _Arena()
    frames: List[_Frame] = []
    root: Optional[int] = None

    def attach(node_id: int, offset: int) -> None:
        nonlocal root
        if frames:
            frames[-1].groups[-1].append(node_id)
            return
        if root is not None:
            raise ProvenanceFormatError(
                "multiple top-level provenance expressions near character %d" % offset
            )
        root = node_id

    last_offset = 0
    for kind, value, offset in _lex(source):
        last_offset = offset
        if kind == "open":
            frames.append(_Frame(str(value), offset, [[]]))
            continue
        if kind == "atom":
            token = str(value)
            if token_pattern is not None and token_pattern.fullmatch(token) is None:
                raise ProvenanceFormatError(
                    "tuple token %r does not match the frozen token grammar at character %d"
                    % (token, offset)
                )
            attach(arena.add("leaf", token), offset)
            continue
        if kind == "comma":
            if not frames:
                raise ProvenanceFormatError("comma outside an operator at character %d" % offset)
            frames[-1].groups.append([])
            continue
        if kind == "close":
            if not frames:
                raise ProvenanceFormatError("unmatched ')' at character %d" % offset)
            frame = frames.pop()
            if frame.op == "plus":
                if len(frame.groups) != 1:
                    raise ProvenanceFormatError(
                        "sum contains an unexpected comma at character %d" % frame.offset
                    )
                node_id = arena.add("plus", tuple(frame.groups[0]))
            elif frame.op == "times":
                groups = list(frame.groups)
                if len(groups) > 1 and not groups[-1]:
                    groups.pop()
                if groups == [[]]:
                    children = ()
                elif any(len(group) != 1 for group in groups):
                    raise ProvenanceFormatError(
                        "product has an empty or adjacent operand at character %d" % frame.offset
                    )
                else:
                    children = tuple(group[0] for group in groups)
                node_id = arena.add("times", children)
            elif frame.op == "minus":
                groups = list(frame.groups)
                if len(groups) == 3 and not groups[-1]:
                    groups.pop()
                if len(groups) != 2:
                    raise ProvenanceFormatError(
                        "monus must have exactly two operand slots at character %d" % frame.offset
                    )
                if len(groups[0]) != 1:
                    raise ProvenanceFormatError(
                        "monus left slot must contain one expression at character %d" % frame.offset
                    )
                left = groups[0][0]
                right = _slot(arena, groups[1])
                node_id = arena.add("minus", (left, right))
            else:
                raise ProvenanceFormatError("unknown operator: %s" % frame.op)
            attach(node_id, offset)
            continue
        raise AssertionError("unreachable lexer token")

    if frames:
        raise ProvenanceFormatError(
            "unterminated %s operator opened at character %d"
            % (frames[-1].op, frames[-1].offset)
        )
    if root is None:
        raise ProvenanceFormatError("provenance string has no expression near character %d" % last_offset)
    return arena, root


class _NormalizedArena(_Arena):
    """A per-answer normalized occurrence tree with exact sortable shape keys."""

    def __init__(self) -> None:
        super().__init__()
        self.keys: List[Any] = []

    def add_keyed(self, op: str, payload: Any, key: Any) -> int:
        node_id = self.add(op, payload)
        self.keys.append(key)
        return node_id


def _add_const(arena: _NormalizedArena, value: bool) -> int:
    return arena.add_keyed("const", bool(value), ("const", bool(value)))


def _make_not(arena: _NormalizedArena, child: int) -> int:
    node = arena.nodes[child]
    if node.op == "const":
        return _add_const(arena, not bool(node.payload))
    if node.op == "not":
        return int(node.payload)
    return arena.add_keyed("not", child, ("not", arena.keys[child]))


def _make_nary(arena: _NormalizedArena, op: str, initial: Iterable[int]) -> int:
    children: List[int] = []
    for child in initial:
        node = arena.nodes[child]
        if node.op == op:
            children.extend(node.payload)
        else:
            children.append(child)

    identity = True if op == "and" else False
    annihilator = False if op == "and" else True
    filtered: List[int] = []
    for child in children:
        node = arena.nodes[child]
        if node.op == "const":
            value = bool(node.payload)
            if value == annihilator:
                return _add_const(arena, annihilator)
            if value == identity:
                continue
        filtered.append(child)

    filtered.sort(key=lambda node_id: arena.keys[node_id])
    unique: List[int] = []
    previous: Optional[Any] = None
    for child in filtered:
        key = arena.keys[child]
        if previous is None or key != previous:
            unique.append(child)
            previous = key
    if not unique:
        return _add_const(arena, identity)
    if len(unique) == 1:
        return unique[0]
    key = (op, tuple(arena.keys[child] for child in unique))
    return arena.add_keyed(op, tuple(unique), key)


def normalize_boolean(raw: _Arena, root: int) -> Tuple[_NormalizedArena, int]:
    """Apply the frozen Boolean abstraction without cross-occurrence sharing."""
    normalized = _NormalizedArena()
    mapped: List[int] = []
    for node in raw.nodes:
        if node.op == "leaf":
            token = str(node.payload)
            mapped.append(normalized.add_keyed("leaf", token, ("leaf", token)))
        elif node.op == "plus":
            mapped.append(_make_nary(normalized, "or", (mapped[child] for child in node.payload)))
        elif node.op == "times":
            mapped.append(_make_nary(normalized, "and", (mapped[child] for child in node.payload)))
        elif node.op == "minus":
            left, right = node.payload
            negated = _make_not(normalized, mapped[right])
            mapped.append(_make_nary(normalized, "and", (mapped[left], negated)))
        else:
            raise ProvenanceFormatError("unknown raw operation: %s" % node.op)
    return normalized, mapped[root]


@dataclass(frozen=True)
class DagNode:
    op: str
    payload: Any


class MultiRootDag:
    """Intern expressions globally and retain one explicit root per answer."""

    def __init__(self) -> None:
        self.nodes: List[DagNode] = []
        self.roots: Dict[str, int] = {}
        self._lookup: Dict[Tuple[str, Any], int] = {}
        self._answer_root_ids: set[int] = set()

    def _intern(self, op: str, payload: Any) -> int:
        key = (op, payload)
        existing = self._lookup.get(key)
        if existing is not None:
            return existing
        node_id = len(self.nodes)
        self.nodes.append(DagNode(op, payload))
        self._lookup[key] = node_id
        return node_id

    def add_answer(self, answer_key: str, arena: _NormalizedArena, root: int) -> int:
        if answer_key in self.roots:
            raise ProvenanceFormatError("duplicate canonical answer key: %s" % answer_key)
        reachable = arena.reachable(root)
        local_to_global: Dict[int, int] = {}
        for local_id in sorted(reachable):
            node = arena.nodes[local_id]
            if node.op in ("leaf", "const"):
                payload = node.payload
            elif node.op == "not":
                payload = local_to_global[int(node.payload)]
            elif node.op in ("and", "or"):
                payload = tuple(local_to_global[child] for child in node.payload)
            else:
                raise ProvenanceFormatError("unknown normalized operation: %s" % node.op)
            local_to_global[local_id] = self._intern(node.op, payload)
        expression_root = local_to_global[root]
        answer_root = len(self.nodes)
        self.nodes.append(DagNode("or", (expression_root,)))
        self._answer_root_ids.add(answer_root)
        self.roots[answer_key] = answer_root
        return answer_root

    def children(self, node_id: int) -> Tuple[int, ...]:
        node = self.nodes[node_id]
        if node.op in ("leaf", "const"):
            return ()
        if node.op == "not":
            return (int(node.payload),)
        if node.op in ("and", "or"):
            return tuple(node.payload)
        raise ProvenanceFormatError("unknown DAG operation: %s" % node.op)

    def validate(self) -> None:
        if len(self._lookup) + len(self._answer_root_ids) != len(self.nodes):
            raise ProvenanceFormatError(
                "expression interning table, answer roots, and node array disagree"
            )
        if set(self.roots.values()) != self._answer_root_ids:
            raise ProvenanceFormatError("answer root index and root mapping disagree")
        for node_id in range(len(self.nodes)):
            for child in self.children(node_id):
                if child < 0 or child >= node_id:
                    raise ProvenanceFormatError(
                        "DAG is not topological at node %d -> %d" % (node_id, child)
                    )
        for answer_key, root in self.roots.items():
            if root < 0 or root >= len(self.nodes):
                raise ProvenanceFormatError("invalid root for answer %s" % answer_key)
            node = self.nodes[root]
            if node.op != "or" or len(node.payload) != 1:
                raise ProvenanceFormatError(
                    "answer %s does not reference a unary or root" % answer_key
                )

    def stats(self) -> Tuple[int, int]:
        return len(self.nodes), sum(len(self.children(node_id)) for node_id in range(len(self.nodes)))

    def expression_stats(self) -> Tuple[int, int]:
        nodes = len(self.nodes) - len(self._answer_root_ids)
        edges = sum(
            len(self.children(node_id))
            for node_id in range(len(self.nodes))
            if node_id not in self._answer_root_ids
        )
        return nodes, edges

    def answer_root_stats(self) -> Tuple[int, int]:
        nodes = len(self._answer_root_ids)
        edges = sum(len(self.children(node_id)) for node_id in self._answer_root_ids)
        return nodes, edges

    def tokens(self) -> Tuple[str, ...]:
        return tuple(sorted(str(node.payload) for node in self.nodes if node.op == "leaf"))

    def compiler_circuit(self) -> Dict[int, Tuple[str, Any]]:
        operations = {"and": "times", "or": "plus"}
        circuit: Dict[int, Tuple[str, Any]] = {}
        for node_id, node in enumerate(self.nodes):
            operation = operations.get(node.op, node.op)
            circuit[node_id] = (operation, node.payload)
        return circuit

    def node_document(self, node_id: int) -> Dict[str, Any]:
        node = self.nodes[node_id]
        item: Dict[str, Any] = {"id": node_id, "op": node.op}
        if node.op == "leaf":
            item["token"] = node.payload
        elif node.op == "const":
            item["value"] = bool(node.payload)
        elif node.op == "not":
            item["child"] = int(node.payload)
        else:
            item["children"] = list(node.payload)
        return item

    def root_documents(self) -> Iterator[Dict[str, Any]]:
        for key in sorted(self.roots):
            yield {"answer_key": key, "root": self.roots[key]}

    def document(self) -> Dict[str, Any]:
        return {
            "schema": "npcs-pp-hc-dag-v2",
            "nodes": [self.node_document(node_id) for node_id in range(len(self.nodes))],
            "roots": list(self.root_documents()),
        }


@dataclass(frozen=True)
class RawAnswer:
    answer_key: str
    provenance: str


@dataclass(frozen=True)
class ExtractedResponse:
    provenance_variable: str
    answers: List[RawAnswer]
    metrics: Dict[str, Any]


@dataclass
class PostprocessResult:
    provenance_variable: str
    answers: List[RawAnswer]
    dag: MultiRootDag
    answer_metrics: List[Dict[str, Any]]
    metrics: Dict[str, Any]


def _term_key(binding: Optional[Mapping[str, Any]]) -> List[str]:
    if binding is None:
        return ["unbound"]
    kind = binding.get("type")
    value = binding.get("value")
    if not isinstance(value, str):
        raise ProvenanceFormatError("SPARQL JSON term has no string value")
    if kind == "uri":
        return ["iri", value]
    if kind == "bnode":
        return ["bnode", value]
    if kind in ("literal", "typed-literal"):
        language = str(binding.get("xml:lang") or binding.get("lang") or "").lower()
        datatype = binding.get("datatype") or (RDF_LANGSTRING if language else XSD_STRING)
        return ["literal", value, str(datatype), language]
    raise ProvenanceFormatError("unsupported SPARQL JSON term type: %r" % (kind,))


def _answer_key(row: Mapping[str, Any], answer_variables: Sequence[str]) -> str:
    pairs = [[variable, _term_key(row.get(variable))] for variable in answer_variables]
    return json.dumps(pairs, ensure_ascii=False, separators=(",", ":"))


def _provenance_variable(variables: Sequence[str], explicit: Optional[str]) -> str:
    if explicit is not None:
        if explicit not in variables:
            raise ProvenanceFormatError(
                "requested provenance variable %r is not in the response head" % explicit
            )
        return explicit
    candidates = [
        variable for variable in variables
        if variable == LEGACY_PROVENANCE_VARIABLE
        or variable.endswith("_" + LEGACY_PROVENANCE_VARIABLE)
    ]
    if len(candidates) != 1:
        raise ProvenanceFormatError(
            "expected exactly one NPCS provenance column; found %r" % candidates
        )
    return candidates[0]


def _milliseconds(started: float) -> float:
    return (time.perf_counter() - started) * 1000.0


def _nearest_rank(values: Sequence[float], probability: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(probability * len(ordered)) - 1)
    return ordered[index]


def _distribution(values: Sequence[float]) -> Dict[str, Any]:
    return {
        "count": len(values),
        "sum": sum(values),
        "p50": _nearest_rank(values, 0.50),
        "p95": _nearest_rank(values, 0.95),
        "p99": _nearest_rank(values, 0.99),
        "max": max(values) if values else None,
        "quantile_rule": "nearest-rank",
    }


def extract_response_bytes(
    raw_response: bytes,
    provenance_variable: Optional[str] = None,
) -> ExtractedResponse:
    """Decode and canonically order a complete SPARQL Results JSON response."""
    decode_started = time.perf_counter()
    try:
        payload = json.loads(raw_response.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProvenanceFormatError("response is not valid UTF-8 SPARQL Results JSON") from exc
    json_decode_ms = _milliseconds(decode_started)

    extract_started = time.perf_counter()
    if not isinstance(payload, dict):
        raise ProvenanceFormatError("SPARQL Results JSON top level is not an object")
    head = payload.get("head")
    results = payload.get("results")
    if not isinstance(head, dict) or not isinstance(results, dict):
        raise ProvenanceFormatError("response lacks SPARQL head or results objects")
    variables = head.get("vars")
    bindings = results.get("bindings")
    if not isinstance(variables, list) or not all(isinstance(item, str) for item in variables):
        raise ProvenanceFormatError("response head.vars is not a list of variable names")
    if not isinstance(bindings, list) or not all(isinstance(item, dict) for item in bindings):
        raise ProvenanceFormatError("response results.bindings is not a list of rows")
    provenance = _provenance_variable(variables, provenance_variable)
    answer_variables = sorted(variable for variable in variables if variable != provenance)
    answers: List[RawAnswer] = []
    seen_keys: set[str] = set()
    for row_number, row in enumerate(bindings, 1):
        value = row.get(provenance)
        if (
            not isinstance(value, dict)
            or value.get("type") not in ("literal", "typed-literal")
            or not isinstance(value.get("value"), str)
        ):
            raise ProvenanceFormatError(
                "row %d has no string provenance binding ?%s" % (row_number, provenance)
            )
        key = _answer_key(row, answer_variables)
        if key in seen_keys:
            raise ProvenanceFormatError(
                "response contains duplicate canonical answer key at row %d" % row_number
            )
        seen_keys.add(key)
        answers.append(RawAnswer(key, value["value"]))
    answers.sort(key=lambda answer: answer.answer_key)
    extract_sort_ms = _milliseconds(extract_started)

    provenance_sizes = [len(answer.provenance.encode("utf-8")) for answer in answers]
    return ExtractedResponse(
        provenance,
        answers,
        {
            "raw_response_bytes": len(raw_response),
            "provenance_payload_bytes": sum(provenance_sizes),
            "provenance_bytes_per_answer": _distribution(provenance_sizes),
            "json_decode_ms": json_decode_ms,
            "extract_sort_ms": extract_sort_ms,
        },
    )


def build_global_dag(
    extracted: ExtractedResponse,
    token_regex: Optional[str] = None,
    retain_provenance: bool = True,
) -> PostprocessResult:
    """Parse each saved polynomial and accumulate one query-global exact DAG."""

    try:
        token_pattern = re.compile(token_regex) if token_regex is not None else None
    except re.error as exc:
        raise ProvenanceFormatError("invalid token regular expression") from exc

    dag = MultiRootDag()
    parse_ms = 0.0
    normalize_ms = 0.0
    hash_cons_ms = 0.0
    tree_nodes = tree_edges = 0
    normalized_nodes = normalized_edges = 0
    max_tree_nodes = max_normalized_nodes = 0
    answer_metrics: List[Dict[str, Any]] = []
    for answer_index, answer in enumerate(extracted.answers):
        started = time.perf_counter()
        raw_tree, raw_root = parse_expression(answer.provenance, token_pattern)
        answer_parse_ms = _milliseconds(started)
        parse_ms += answer_parse_ms
        raw_reachable = raw_tree.reachable(raw_root)
        nodes = len(raw_reachable)
        edges = sum(len(raw_tree.children(node_id)) for node_id in raw_reachable)
        token_occurrences = sum(
            raw_tree.nodes[node_id].op == "leaf" for node_id in raw_reachable
        )
        tree_nodes += nodes
        tree_edges += edges
        max_tree_nodes = max(max_tree_nodes, nodes)

        started = time.perf_counter()
        normalized, normalized_root = normalize_boolean(raw_tree, raw_root)
        answer_normalize_ms = _milliseconds(started)
        normalize_ms += answer_normalize_ms
        normalized_reachable = normalized.reachable(normalized_root)
        normalized_answer_nodes = len(normalized_reachable)
        normalized_answer_edges = sum(
            len(normalized.children(node_id)) for node_id in normalized_reachable
        )
        normalized_nodes += normalized_answer_nodes
        normalized_edges += normalized_answer_edges
        max_normalized_nodes = max(max_normalized_nodes, normalized_answer_nodes)

        before_nodes = len(dag.nodes)
        started = time.perf_counter()
        global_root = dag.add_answer(answer.answer_key, normalized, normalized_root)
        answer_hash_cons_ms = _milliseconds(started)
        hash_cons_ms += answer_hash_cons_ms
        added_nodes = len(dag.nodes) - before_nodes
        added_edges = sum(
            len(dag.children(node_id)) for node_id in range(before_nodes, len(dag.nodes))
        )
        if added_nodes < 1 or added_edges < 1:
            raise ProvenanceFormatError("answer root materialization is incomplete")
        answer_metrics.append({
            "answer_key": answer.answer_key,
            "provenance_utf8_bytes": len(answer.provenance.encode("utf-8")),
            "token_occurrences": token_occurrences,
            "tree_nodes": nodes,
            "tree_edges": edges,
            "tree_total": nodes + edges,
            "normalized_tree_nodes": normalized_answer_nodes,
            "normalized_tree_edges": normalized_answer_edges,
            "normalized_tree_total": normalized_answer_nodes + normalized_answer_edges,
            "hc_nodes_added": added_nodes,
            "hc_edges_added": added_edges,
            "hc_expression_nodes_added": added_nodes - 1,
            "hc_expression_edges_added": added_edges - 1,
            "answer_root_nodes_added": 1,
            "answer_root_edges_added": 1,
            "expression_root": dag.children(global_root)[0],
            "root": global_root,
            "parse_ms": answer_parse_ms,
            "boolean_normalize_ms": answer_normalize_ms,
            "global_hash_cons_ms": answer_hash_cons_ms,
        })
        if not retain_provenance:
            # The CLI has already fsync'ed the raw JSONL. Drop each
            # complete string as soon as its temporary trees are consumed.
            extracted.answers[answer_index] = RawAnswer(answer.answer_key, "")

    validate_started = time.perf_counter()
    dag.validate()
    dag_validate_ms = _milliseconds(validate_started)
    hc_nodes, hc_edges = dag.stats()
    hc_expression_nodes, hc_expression_edges = dag.expression_stats()
    answer_root_nodes, answer_root_edges = dag.answer_root_stats()
    metrics: Dict[str, Any] = {
        "schema": "npcs-pp-hc-metrics-v2",
        "answer_count": len(extracted.answers),
        "root_count": len(dag.roots),
        "provenance_variable": extracted.provenance_variable,
        "tree_nodes": tree_nodes,
        "tree_edges": tree_edges,
        "tree_total": tree_nodes + tree_edges,
        "tree_nodes_max_answer": max_tree_nodes,
        "normalized_tree_nodes": normalized_nodes,
        "normalized_tree_edges": normalized_edges,
        "normalized_tree_total": normalized_nodes + normalized_edges,
        "normalized_tree_nodes_max_answer": max_normalized_nodes,
        "hc_nodes": hc_nodes,
        "hc_edges": hc_edges,
        "hc_total": hc_nodes + hc_edges,
        "hc_expression_nodes": hc_expression_nodes,
        "hc_expression_edges": hc_expression_edges,
        "hc_expression_total": hc_expression_nodes + hc_expression_edges,
        "answer_root_nodes": answer_root_nodes,
        "answer_root_edges": answer_root_edges,
        "answer_root_total": answer_root_nodes + answer_root_edges,
        "leaf_token_count": len(dag.tokens()),
        "tree_over_hc_ratio": (
            (tree_nodes + tree_edges) / (hc_nodes + hc_edges)
            if hc_nodes + hc_edges else None
        ),
        "tree_over_expression_hc_ratio": (
            (tree_nodes + tree_edges) / (hc_expression_nodes + hc_expression_edges)
            if hc_expression_nodes + hc_expression_edges else None
        ),
        "parse_ms": parse_ms,
        "boolean_normalize_ms": normalize_ms,
        "global_hash_cons_ms": hash_cons_ms,
        "dag_validate_ms": dag_validate_ms,
        "factor_status": "not_implemented",
        "factor_ms": None,
    }
    metrics.update(extracted.metrics)
    metrics["token_occurrences_per_answer"] = _distribution(
        [item["token_occurrences"] for item in answer_metrics]
    )
    metrics["tree_total_per_answer"] = _distribution(
        [item["tree_total"] for item in answer_metrics]
    )
    metrics["normalized_tree_total_per_answer"] = _distribution(
        [item["normalized_tree_total"] for item in answer_metrics]
    )
    metrics["parse_ms_per_answer"] = _distribution(
        [item["parse_ms"] for item in answer_metrics]
    )
    metrics["boolean_normalize_ms_per_answer"] = _distribution(
        [item["boolean_normalize_ms"] for item in answer_metrics]
    )
    metrics["global_hash_cons_ms_per_answer"] = _distribution(
        [item["global_hash_cons_ms"] for item in answer_metrics]
    )
    return PostprocessResult(
        extracted.provenance_variable,
        extracted.answers,
        dag,
        answer_metrics,
        metrics,
    )


def process_response_bytes(
    raw_response: bytes,
    provenance_variable: Optional[str] = None,
    token_regex: Optional[str] = None,
) -> PostprocessResult:
    """Convenience in-memory boundary used by tests and small callers."""
    extracted = extract_response_bytes(raw_response, provenance_variable)
    return build_global_dag(extracted, token_regex)


def _atomic_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".partial")
    with temporary.open("wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _atomic_json_lines(path: Path, values: Iterable[Any]) -> None:
    """Write JSONL incrementally so large provenance strings are not copied."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".partial")
    with temporary.open("wb") as handle:
        for value in values:
            handle.write(_json_bytes(value))
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _atomic_utf8_lines(path: Path, values: Iterable[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".partial")
    with temporary.open("wb") as handle:
        for value in values:
            if "\n" in value or "\r" in value:
                raise ValueError("line-oriented artifact value contains a newline")
            handle.write(value.encode("utf-8"))
            handle.write(b"\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _atomic_dag_json(
    path: Path,
    dag: MultiRootDag,
    context: Mapping[str, str],
) -> None:
    """Persist the canonical DAG without materializing a second in-memory copy."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".partial")
    with temporary.open("wb") as handle:
        handle.write(b'{"context":')
        handle.write(json.dumps(
            context, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8"))
        handle.write(b',"nodes":[')
        for node_id in range(len(dag.nodes)):
            if node_id:
                handle.write(b",")
            handle.write(json.dumps(
                dag.node_document(node_id),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8"))
        handle.write(b'],"roots":[')
        for index, root in enumerate(dag.root_documents()):
            if index:
                handle.write(b",")
            handle.write(json.dumps(
                root, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode("utf-8"))
        handle.write(b'],"schema":"npcs-pp-hc-dag-v2"}\n')
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _record_context(context: Optional[Mapping[str, str]]) -> Dict[str, str]:
    result = dict(context or {})
    for key, value in result.items():
        if not key or not isinstance(value, str) or not value or "\n" in value or "\r" in value:
            raise ValueError("artifact context must contain non-empty single-line strings")
    return result


def persist_raw_provenance(
    answers: Sequence[RawAnswer],
    target: Path,
    context: Optional[Mapping[str, str]] = None,
) -> float:
    started = time.perf_counter()
    if target.exists():
        raise FileExistsError("refusing to overwrite raw provenance artifact: %s" % target)
    record_context = _record_context(context)
    _atomic_json_lines(
        target,
        (dict(record_context, **{
            "answer_key": answer.answer_key,
            "provenance": answer.provenance,
            "utf8_bytes": len(answer.provenance.encode("utf-8")),
        })
        for answer in answers
        ),
    )
    return _milliseconds(started)


def persist_dag_artifacts(
    result: PostprocessResult,
    output: Path,
    context: Optional[Mapping[str, str]] = None,
) -> float:
    started = time.perf_counter()
    dag_target = output / "npcs-hc-dag.json"
    answer_target = output / "npcs-answer-metrics.jsonl"
    for target in (dag_target, answer_target):
        if target.exists():
            raise FileExistsError("refusing to overwrite post-processing artifact: %s" % target)
    record_context = _record_context(context)
    _atomic_dag_json(dag_target, result.dag, record_context)
    _atomic_json_lines(
        answer_target,
        (dict(record_context, **item) for item in result.answer_metrics),
    )
    return _milliseconds(started)


def persist_postprocess(
    result: PostprocessResult,
    output: Path,
    context: Optional[Mapping[str, str]] = None,
) -> float:
    """Convenience persistence; the CLI uses the two ordered stages."""
    raw_ms = persist_raw_provenance(
        result.answers, output / "npcs-provenance.jsonl", context
    )
    dag_ms = persist_dag_artifacts(result, output, context)
    return raw_ms + dag_ms


def _weights(path: Optional[Path], uniform: Optional[float], tokens: Sequence[str]) -> Dict[str, float]:
    if (path is None) == (uniform is None):
        raise ValueError("choose exactly one of --probabilities or --uniform-probability")
    if path is not None:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("probability file must be a JSON object")
        result = value
    else:
        if uniform is None or not math.isfinite(uniform) or not 0.0 <= uniform <= 1.0:
            raise ValueError("uniform probability must be finite and in [0, 1]")
        result = {token: float(uniform) for token in tokens}
    missing = [token for token in tokens if token not in result]
    if missing:
        raise ValueError("probability file is missing %d token(s)" % len(missing))
    selected: Dict[str, float] = {}
    for token in tokens:
        try:
            probability = float(result[token])
        except (TypeError, ValueError) as exc:
            raise ValueError("probability for %s is not numeric" % token) from exc
        if not math.isfinite(probability) or not 0.0 <= probability <= 1.0:
            raise ValueError("probability for %s is not finite and in [0, 1]" % token)
        selected[token] = probability
    return selected


def compile_and_wmc(
    result: PostprocessResult,
    backend: str,
    weights: Mapping[str, float],
) -> Tuple[Dict[str, float], Dict[str, Any]]:
    """Compile every DAG root in one manager, explicitly disabling digests."""
    import compiler

    order = result.dag.tokens()
    started = time.perf_counter()
    compiled = compiler.compile_many(
        result.dag.compiler_circuit(),
        result.dag.roots,
        mode="shared",
        backend=backend,
        order=order,
        record_order_fingerprint=False,
    )
    compile_wall_ms = _milliseconds(started)
    started = time.perf_counter()
    probabilities = compiled.wmc_many(weights)
    wmc_wall_ms = _milliseconds(started)
    metrics = dict(compiled.metrics)
    metrics["compile_wall_ms"] = compile_wall_ms
    metrics["wmc_wall_ms"] = wmc_wall_ms
    if any(
        key.lower().endswith(("_sha", "_sha1", "_sha256", "_sha512"))
        or "digest" in key.lower()
        for key in metrics
    ):
        raise RuntimeError("digest-bearing compiler metric leaked into the no-digest experiment path")
    return probabilities, metrics


def _peak_rss_bytes() -> Optional[int]:
    try:
        import resource
    except ImportError:
        return None
    value = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    # Linux reports KiB; macOS reports bytes.
    return value if sys.platform == "darwin" else value * 1024


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("response", type=Path, help="complete SPARQL Results JSON response")
    parser.add_argument("--out", required=True, type=Path, help="new per-run artifact directory")
    parser.add_argument("--query-id", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--engine", required=True)
    parser.add_argument("--provenance-variable")
    parser.add_argument(
        "--token-regex",
        help="full-match validation for tuple tokens; WatDiv uses ^urn:t:[0-9]+$",
    )
    parser.add_argument("--backend", choices=("none", "oracle", "cudd"), default="none")
    parser.add_argument("--probabilities", type=Path)
    parser.add_argument("--uniform-probability", type=float)
    args = parser.parse_args(argv)

    response = args.response.resolve()
    if not response.is_file() or response.stat().st_size == 0:
        parser.error("response file is missing or empty: %s" % response)
    output = args.out.resolve()
    if output.exists():
        parser.error("refusing to reuse a per-run output directory: %s" % output)
    if args.backend == "none" and (
        args.probabilities is not None or args.uniform_probability is not None
    ):
        parser.error("probabilities require --backend oracle or --backend cudd")
    if args.backend != "none" and (
        (args.probabilities is None) == (args.uniform_probability is None)
    ):
        parser.error("choose exactly one of --probabilities or --uniform-probability")
    context = _record_context({
        "query_id": args.query_id,
        "run_id": args.run_id,
        "engine": args.engine,
        "method": "NPCS+PP-HC",
    })
    output.mkdir(parents=True)
    metrics_target = output / "metrics.json"
    probability_target = output / "probabilities.jsonl"

    pp_hc_started = time.perf_counter()
    response_read_started = time.perf_counter()
    raw_response = response.read_bytes()
    response_read_ms = _milliseconds(response_read_started)
    extracted = extract_response_bytes(
        raw_response, provenance_variable=args.provenance_variable
    )
    raw_persist_ms = persist_raw_provenance(
        extracted.answers,
        output / "npcs-provenance.jsonl",
        context,
    )
    try:
        result = build_global_dag(
            extracted,
            token_regex=args.token_regex,
            retain_provenance=False,
        )
    except ProvenanceFormatError as exc:
        parser.error(str(exc))
    dag_persist_ms = persist_dag_artifacts(result, output, context)
    pp_hc_build_wall_ms = _milliseconds(pp_hc_started)
    result.metrics["context"] = context
    result.metrics["timing_scope"] = "offline_from_complete_response_file"
    result.metrics["response_read_ms"] = response_read_ms
    result.metrics["raw_provenance_persist_ms"] = raw_persist_ms
    result.metrics["dag_artifact_persist_ms"] = dag_persist_ms
    result.metrics["artifact_persist_ms"] = raw_persist_ms + dag_persist_ms
    result.metrics["raw_provenance_jsonl_bytes"] = (
        output / "npcs-provenance.jsonl"
    ).stat().st_size
    result.metrics["hc_dag_json_bytes"] = (output / "npcs-hc-dag.json").stat().st_size
    result.metrics["answer_metrics_jsonl_bytes"] = (
        output / "npcs-answer-metrics.jsonl"
    ).stat().st_size
    result.metrics["pp_hc_total_ms"] = sum(
        float(result.metrics[key])
        for key in (
            "response_read_ms",
            "json_decode_ms",
            "extract_sort_ms",
            "raw_provenance_persist_ms",
            "parse_ms",
            "boolean_normalize_ms",
            "global_hash_cons_ms",
            "dag_validate_ms",
            "dag_artifact_persist_ms",
        )
    )
    result.metrics["pp_hc_build_wall_ms"] = pp_hc_build_wall_ms

    if args.backend != "none":
        pqe_started = time.perf_counter()
        try:
            probability_load_started = time.perf_counter()
            probabilities = _weights(
                args.probabilities.resolve() if args.probabilities is not None else None,
                args.uniform_probability,
                result.dag.tokens(),
            )
            probability_load_ms = _milliseconds(probability_load_started)
            values, compiler_metrics = compile_and_wmc(result, args.backend, probabilities)
        except (ImportError, RuntimeError, ValueError, OSError) as exc:
            parser.error(str(exc))
        variable_order = result.dag.tokens()
        variable_order_started = time.perf_counter()
        _atomic_utf8_lines(
            output / "variable-order.txt",
            variable_order,
        )
        compiler_metrics["variable_order_persist_ms"] = _milliseconds(variable_order_started)
        compiler_metrics["variable_order_bytes"] = (
            output / "variable-order.txt"
        ).stat().st_size
        probability_started = time.perf_counter()
        _atomic_json_lines(
            probability_target,
            (
                dict(context, answer_key=key, probability=values[key])
                for key in sorted(values)
            ),
        )
        probability_persist_ms = _milliseconds(probability_started)
        pqe_wall_ms = _milliseconds(pqe_started)
        compiler_metrics["probability_persist_ms"] = probability_persist_ms
        compiler_metrics["probability_jsonl_bytes"] = probability_target.stat().st_size
        compiler_metrics["probability_load_ms"] = probability_load_ms
        compiler_metrics["pqe_total_ms"] = sum(
            float(compiler_metrics[key])
            for key in (
                "probability_load_ms",
                "compile_wall_ms",
                "wmc_wall_ms",
                "variable_order_persist_ms",
                "probability_persist_ms",
            )
        )
        compiler_metrics["pqe_wall_ms"] = pqe_wall_ms
        result.metrics["compiler"] = compiler_metrics

    result.metrics["process_peak_rss_bytes"] = _peak_rss_bytes()
    _atomic_bytes(metrics_target, _json_bytes(result.metrics))
    print(json.dumps(result.metrics, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
