#!/usr/bin/env python3
"""Seeded differential correctness gate for construction, compilation, and WMC.

This is deliberately an *offline* verifier rather than a performance benchmark.
It has two independent halves:

* small random provenance DAGs are checked against exact Decimal possible-world
  enumeration, the bundled Python ROBDD oracle, and CUDD in shared/per-root mode;
* small pure BGPs are materialized by the Java engine in factored and flat mode,
  then compared by term-aware answer identity, every possible world, and WMC.

The only stdout payload is a JSON summary, making the gate suitable for CI and
artifact automation.  Diagnostics from Java are captured, not forwarded.
"""
from __future__ import annotations

import argparse
from decimal import Decimal, localcontext
import itertools
import json
from pathlib import Path
import random
import subprocess
import sys
import tempfile
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

import circuit_io
import compile_bdd
import compiler


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_JAR = ROOT / "engine" / "target" / "npcs-rewrite.jar"
RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
XSD = "http://www.w3.org/2001/XMLSchema#"


def _children(node: Tuple[str, Any]) -> Tuple[str, ...]:
    op, payload = node
    if op in ("leaf", "const"):
        return ()
    if op in ("plus", "times"):
        return tuple(payload)
    if op == "minus":
        return tuple(payload)
    raise ValueError("unknown operation %r" % (op,))


def _postorder(circ: Mapping[str, Tuple[str, Any]], roots: Iterable[str]) -> List[str]:
    """Return a non-recursive child-before-parent order and reject cycles."""
    order: List[str] = []
    state: Dict[str, int] = {}
    for root in roots:
        stack = [(root, False)]
        while stack:
            gate, expanded = stack.pop()
            mark = state.get(gate, 0)
            if expanded:
                if mark == 2:
                    continue
                state[gate] = 2
                order.append(gate)
                continue
            if mark == 2:
                continue
            if mark == 1:
                raise ValueError("cycle at gate %s" % gate)
            if gate not in circ:
                raise ValueError("missing gate %s" % gate)
            state[gate] = 1
            stack.append((gate, True))
            for child in reversed(_children(circ[gate])):
                child_mark = state.get(child, 0)
                if child_mark == 1:
                    raise ValueError("cycle at gate %s" % child)
                if child_mark != 2:
                    stack.append((child, False))
    return order


def _evaluate_order(circ: Mapping[str, Tuple[str, Any]], order: Sequence[str],
                    assignment: Mapping[str, bool]) -> Dict[str, bool]:
    values: Dict[str, bool] = {}
    for gate in order:
        op, payload = circ[gate]
        if op == "leaf":
            values[gate] = assignment[payload]
        elif op == "const":
            values[gate] = bool(payload)
        elif op == "plus":
            values[gate] = any(values[child] for child in payload)
        elif op == "times":
            values[gate] = all(values[child] for child in payload)
        elif op == "minus":
            values[gate] = values[payload[0]] and not values[payload[1]]
        else:
            raise ValueError("unknown operation %r" % (op,))
    return values


def _decimal_wmc(circ: Mapping[str, Tuple[str, Any]], roots: Mapping[str, str],
                 weights: Mapping[str, Decimal]) -> Tuple[Dict[str, Decimal], int]:
    variables = compiler.deterministic_order(circ, roots)
    order = _postorder(circ, roots.values())
    totals = {key: Decimal(0) for key in roots}
    world_count = 0
    with localcontext() as ctx:
        ctx.prec = 100
        for bits in itertools.product((False, True), repeat=len(variables)):
            assignment = dict(zip(variables, bits))
            world_weight = Decimal(1)
            for variable, bit in zip(variables, bits):
                probability = weights[variable]
                world_weight *= probability if bit else Decimal(1) - probability
            values = _evaluate_order(circ, order, assignment)
            for key, root in roots.items():
                if values[root]:
                    totals[key] += world_weight
            world_count += 1
    return totals, world_count


def _error(actual: float, expected: Decimal) -> Tuple[Decimal, Decimal]:
    if not 0.0 <= actual <= 1.0:
        raise AssertionError("probability outside [0,1]: %.17g" % actual)
    absolute = abs(Decimal.from_float(actual) - expected)
    relative = absolute / abs(expected) if expected else absolute
    return absolute, relative


def _error_bucket() -> Dict[str, Decimal]:
    return {"abs": Decimal(0), "rel": Decimal(0)}


def _record_errors(bucket: Dict[str, Decimal], actual: Mapping[str, float],
                   expected: Mapping[str, Decimal], label: str,
                   abs_tolerance: Decimal, rel_tolerance: Decimal) -> None:
    if set(actual) != set(expected):
        raise AssertionError("%s changed root keys" % label)
    for key in expected:
        absolute, relative = _error(actual[key], expected[key])
        bucket["abs"] = max(bucket["abs"], absolute)
        bucket["rel"] = max(bucket["rel"], relative)
        if absolute > abs_tolerance and relative > rel_tolerance:
            raise AssertionError(
                "%s/%s error abs=%s rel=%s actual=%.17g exact=%s"
                % (label, key, absolute, relative, actual[key], expected[key]))


def _random_dag(rng: random.Random, case_index: int,
                variable_count: int, random_gate_count: int
                ) -> Tuple[Dict[str, Tuple[str, Any]], Dict[str, str], Dict[str, Decimal]]:
    """Generate an acyclic, shared circuit with mandatory operator coverage."""
    prefix = "c%04d" % case_index
    circ: Dict[str, Tuple[str, Any]] = {
        prefix + ":zero": ("const", 0),
        prefix + ":one": ("const", 1),
    }
    leaves = []
    for index in range(variable_count):
        gate = "%s:l%02d" % (prefix, index)
        token = "urn:fuzz:%s:t%02d" % (prefix, index)
        circ[gate] = ("leaf", token)
        leaves.append(gate)

    # Every case includes PLUS, TIMES, MINUS, constants, fan-out sharing, and
    # duplicate output roots.  Constants are intentionally not simplified.
    mandatory = [
        (prefix + ":plus", ("plus", (leaves[0], leaves[1], prefix + ":zero"))),
        (prefix + ":times", ("times", (prefix + ":plus", leaves[2], prefix + ":one"))),
        (prefix + ":minus", ("minus", (prefix + ":times", leaves[3]))),
        (prefix + ":share-a", ("plus", (prefix + ":minus", leaves[4]))),
        (prefix + ":share-b", ("times", (prefix + ":minus", leaves[5]))),
    ]
    for gate, node in mandatory:
        circ[gate] = node

    candidates = list(circ)
    for index in range(random_gate_count):
        gate = "%s:r%03d" % (prefix, index)
        op = rng.choice(("plus", "times", "minus"))
        if op == "minus":
            payload: Any = tuple(rng.sample(candidates, 2))
        else:
            arity = rng.randint(2, min(4, len(candidates)))
            # Sampling with replacement stresses repeated inputs without
            # changing source-DAG acyclicity.
            payload = tuple(rng.choice(candidates) for _ in range(arity))
        circ[gate] = (op, payload)
        candidates.append(gate)

    final = candidates[-1]
    roots = {
        "final": final,
        "duplicate-final": final,
        "shared-left": prefix + ":share-a",
        "shared-right": prefix + ":share-b",
        "constant-false": prefix + ":zero",
        "constant-true": prefix + ":one",
    }

    near_zero = Decimal("1e-30")
    near_one = Decimal(1) - near_zero
    palette = [Decimal(0), Decimal(1), near_zero, near_one]
    weights: Dict[str, Decimal] = {}
    for index, leaf in enumerate(leaves):
        token = circ[leaf][1]
        if index < len(palette):
            weights[token] = palette[index]
        else:
            # Decimal text is the source of truth; avoid importing binary RNG
            # approximation into the high-precision reference.
            weights[token] = Decimal(rng.randint(1, 999999)) / Decimal(1000000)
    return circ, roots, weights


def _run_dag_suite(args: argparse.Namespace) -> Dict[str, Any]:
    rng = random.Random(args.seed)
    tolerances = (Decimal(args.abs_tolerance), Decimal(args.rel_tolerance))
    labels = ("enum", "oracle-shared", "oracle-per-root",
              "cudd-shared", "cudd-per-root")
    errors = {label: _error_bucket() for label in labels}
    worlds = 0
    roots_checked = 0
    op_coverage = {op: 0 for op in ("leaf", "const", "plus", "times", "minus")}
    cudd_status = "ok"

    # Probe once so an optional backend cannot turn a later case into a partial
    # and ambiguous skip.
    try:
        compiler.compile_many({}, {}, backend="cudd")
    except compiler.BackendUnavailable as exc:
        if args.require_cudd:
            raise
        cudd_status = "skipped: %s" % exc

    for case_index in range(args.dag_cases):
        variable_count = rng.randint(6, args.max_variables)
        circ, roots, decimal_weights = _random_dag(
            rng, case_index, variable_count, args.random_gates)
        for op, _ in circ.values():
            op_coverage[op] += 1
        if roots["final"] != roots["duplicate-final"]:
            raise AssertionError("generator lost duplicate roots")
        fanout: Dict[str, int] = {}
        for node in circ.values():
            for child in _children(node):
                fanout[child] = fanout.get(child, 0) + 1
        if max(fanout.values(), default=0) < 2:
            raise AssertionError("generator produced no shared gate")

        exact, case_worlds = _decimal_wmc(circ, roots, decimal_weights)
        weights = {key: float(value) for key, value in decimal_weights.items()}
        enum = {key: compile_bdd.wmc_enum(circ, root, weights)
                for key, root in roots.items()}
        _record_errors(errors["enum"], enum, exact, "enum",
                       tolerances[0], tolerances[1])

        compiled: Dict[str, compiler.CompiledBatch] = {}
        for mode in ("shared", "per-root"):
            batch = compiler.compile_many(circ, roots, mode=mode, backend="oracle")
            compiled["oracle-" + mode] = batch
            result = batch.wmc_many(weights)
            _record_errors(errors["oracle-" + mode], result, exact,
                           "oracle-" + mode, tolerances[0], tolerances[1])

        if (compiled["oracle-shared"].metrics["order_sha256"]
                != compiled["oracle-per-root"].metrics["order_sha256"]):
            raise AssertionError("oracle modes used different global orders")

        if cudd_status == "ok":
            for mode in ("shared", "per-root"):
                batch = compiler.compile_many(circ, roots, mode=mode, backend="cudd")
                result = batch.wmc_many(weights)
                _record_errors(errors["cudd-" + mode], result, exact,
                               "cudd-" + mode, tolerances[0], tolerances[1])
                if batch.metrics["manager_reorderings"] != 0:
                    raise AssertionError("fixed-order CUDD unexpectedly reordered")
                if (batch.metrics["order_sha256"]
                        != compiled["oracle-shared"].metrics["order_sha256"]):
                    raise AssertionError("backends used different global orders")

        worlds += case_worlds
        roots_checked += len(roots)

    deep: Dict[str, Any]
    if cudd_status == "ok" and args.deep_depth:
        circ = {"one": ("const", 1)}
        weights = {}
        previous = "one"
        for index in range(args.deep_depth):
            leaf = "deep:l%05d" % index
            gate = "deep:g%05d" % index
            token = "urn:fuzz:deep:t%05d" % index
            circ[leaf] = ("leaf", token)
            circ[gate] = ("times", (previous, leaf))
            weights[token] = 0.999
            previous = gate
        batch = compiler.compile_many(circ, {"deep": previous}, backend="cudd")
        actual = batch.wmc_many(weights)["deep"]
        with localcontext() as ctx:
            ctx.prec = 100
            expected = Decimal("0.999") ** args.deep_depth
        absolute, relative = _error(actual, expected)
        if absolute > tolerances[0] and relative > tolerances[1]:
            raise AssertionError("deep CUDD WMC mismatch")
        if batch.metrics["wmc_visited_nodes"] != args.deep_depth:
            raise AssertionError("deep CUDD traversal was not one node per variable")
        deep = {
            "status": "ok",
            "depth": args.deep_depth,
            "wmc_visited_nodes": batch.metrics["wmc_visited_nodes"],
            "max_abs_error": str(absolute),
            "max_rel_error": str(relative),
        }
    else:
        reason = "disabled" if not args.deep_depth else "CUDD unavailable"
        deep = {"status": "skipped", "reason": reason, "depth": args.deep_depth}

    rendered_errors = {
        label: {"max_abs": str(bucket["abs"]), "max_rel": str(bucket["rel"])}
        for label, bucket in errors.items()
        if not label.startswith("cudd-") or cudd_status == "ok"
    }
    return {
        "status": "ok",
        "seed": args.seed,
        "case_count": args.dag_cases,
        "world_count": worlds,
        "root_evaluations": roots_checked,
        "operator_coverage": op_coverage,
        "cudd": cudd_status,
        "deep_nonrecursive": deep,
        "errors": rendered_errors,
    }


# A term is stored in N-Triples/Turtle spelling, e.g. ``<urn:x>`` or
# ``"chat"@en``.  This lets the simple oracle retain RDF term identity without
# depending on rdflib.
Fact = Tuple[str, str, str, str]
Pattern = Tuple[str, str, str]


def _iri(value: str) -> str:
    return "<%s>" % value


def _bgp_cases() -> List[Dict[str, Any]]:
    p = _iri("urn:bgp:p")
    q = _iri("urn:bgp:q")
    return [
        {
            "name": "ground-constant",
            "facts": [
                ("g0", _iri("urn:bgp:s"), p, _iri("urn:bgp:o")),
                ("g1", _iri("urn:bgp:s"), p, _iri("urn:bgp:other")),
            ],
            "patterns": [(_iri("urn:bgp:s"), p, _iri("urn:bgp:o"))],
            "select": [],
            "weights": ["0.37", "1"],
        },
        {
            "name": "duplicate-selfjoin",
            "facts": [
                ("s0", _iri("urn:bgp:a"), p, _iri("urn:bgp:b")),
                ("s1", _iri("urn:bgp:a"), p, _iri("urn:bgp:b")),
                ("s2", _iri("urn:bgp:c"), p, _iri("urn:bgp:d")),
            ],
            "patterns": [("?s", p, "?o"), ("?s", p, "?o")],
            "select": ["?s", "?o"],
            "weights": ["0.2", "0.61", "0"],
        },
        {
            "name": "disconnected",
            "facts": [
                ("d0", _iri("urn:bgp:a"), p, _iri("urn:bgp:b")),
                ("d1", _iri("urn:bgp:c"), p, _iri("urn:bgp:d")),
                ("d2", _iri("urn:bgp:x"), q, _iri("urn:bgp:y")),
                ("d3", _iri("urn:bgp:u"), q, _iri("urn:bgp:v")),
            ],
            "patterns": [("?left", p, "?mid"), ("?right", q, "?out")],
            "select": ["?left", "?out"],
            "weights": ["0.15", "0.999999999999999", "0.73", "1e-15"],
        },
        {
            "name": "project-away-join",
            "facts": [
                ("p0", _iri("urn:bgp:a"), p, _iri("urn:bgp:j")),
                ("p1", _iri("urn:bgp:c"), p, _iri("urn:bgp:j")),
                ("p2", _iri("urn:bgp:j"), q, _iri("urn:bgp:z")),
                ("p3", _iri("urn:bgp:j"), q, _iri("urn:bgp:w")),
            ],
            "patterns": [("?source", p, "?join"), ("?join", q, "?answer")],
            "select": ["?answer"],
            "weights": ["0.4", "0.6", "0.8", "0.3"],
        },
        {
            "name": "typed-language-and-term-kind",
            "facts": [
                ("t0", _iri("urn:bgp:a"), p, _iri("urn:bgp:same")),
                ("t1", _iri("urn:bgp:b"), p, '"urn:bgp:same"'),
                ("t2", _iri("urn:bgp:c"), p,
                 '"7"^^<http://www.w3.org/2001/XMLSchema#integer>'),
                ("t3", _iri("urn:bgp:d"), p, '"chat"@EN'),
            ],
            "patterns": [("?source", p, "?value")],
            "select": ["?value"],
            "weights": ["0", "1", "1e-20", "0.99999999999999999999"],
        },
    ]


def _match(pattern: Pattern, triple: Tuple[str, str, str],
           binding: Mapping[str, str]) -> Optional[Dict[str, str]]:
    out = dict(binding)
    for expected, actual in zip(pattern, triple):
        if expected.startswith("?"):
            known = out.get(expected)
            if known is not None and known != actual:
                return None
            out[expected] = actual
        elif expected != actual:
            return None
    return out


def _bgp_answers(patterns: Sequence[Pattern], select: Sequence[str],
                 facts: Sequence[Fact], active: Sequence[bool]) -> Set[str]:
    rows: List[Dict[str, str]] = [{}]
    triples = [(s, p, o) for enabled, (_, s, p, o) in zip(active, facts) if enabled]
    for pattern in patterns:
        next_rows = []
        for row in rows:
            for triple in triples:
                matched = _match(pattern, triple, row)
                if matched is not None:
                    next_rows.append(matched)
        rows = next_rows
    answers = set()
    for row in rows:
        binding = {variable[1:]: circuit_io.canon_term(row.get(variable))
                   for variable in select}
        answers.add(circuit_io.answer_key(binding))
    return answers


def _query_text(case: Mapping[str, Any]) -> str:
    projection = " ".join(case["select"]) if case["select"] else "*"
    body = " ".join("%s %s %s ." % pattern for pattern in case["patterns"])
    return "SELECT %s WHERE { %s }\n" % (projection, body)


def _data_text(case: Mapping[str, Any]) -> str:
    lines = ["@prefix rdf: <%s> ." % RDF]
    for token, subject, predicate, obj in case["facts"]:
        lines.append("%s %s %s ." % (subject, predicate, obj))
        lines.append(
            "%s rdf:subject %s ; rdf:predicate %s ; rdf:object %s ."
            % (_iri("urn:bgp:token:" + token), subject, predicate, obj))
    return "\n".join(lines) + "\n"


def _java_circuit(java_bin: str, jar: Path, mode: str, data: Path,
                  query: Path, timeout: float) -> str:
    command = [
        java_bin, "-jar", str(jar), "circuit", "--construction=" + mode,
        "Standard", str(data), str(query),
    ]
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True,
                            timeout=timeout)
    if result.returncode:
        raise RuntimeError(
            "Java %s construction failed (%d): %s"
            % (mode, result.returncode, result.stderr[-3000:]))
    nt = "\n".join(line for line in result.stdout.splitlines()
                   if line.strip().endswith(" .")) + "\n"
    if nt == "\n":
        raise RuntimeError("Java %s construction emitted no N-Triples" % mode)
    return nt


def _answer_roots(nt: str) -> Tuple[Dict[str, str], Dict[str, Tuple[str, Any]]]:
    circ, answers, bindings = circuit_io.parse(nt)
    roots: Dict[str, str] = {}
    for gate in answers:
        key = circuit_io.answer_key(bindings[gate])
        if key in roots and roots[key] != gate:
            raise AssertionError("duplicate gates for term-aware answer %r" % key)
        roots[key] = gate
    return roots, circ


def _world_value(circ: Mapping[str, Tuple[str, Any]], root: str,
                 active_tokens: Set[str]) -> bool:
    order = _postorder(circ, (root,))
    assignment = {
        payload: payload in active_tokens
        for op, payload in circ.values() if op == "leaf"
    }
    return _evaluate_order(circ, order, assignment)[root]


def _run_engine_suite(args: argparse.Namespace) -> Dict[str, Any]:
    jar = Path(args.jar).resolve()
    if not jar.is_file():
        if args.require_engine or args.engine_only:
            raise RuntimeError("engine JAR is missing: %s" % jar)
        return {"status": "skipped", "reason": "engine JAR is missing", "jar": str(jar)}

    cases = _bgp_cases()
    if args.engine_cases is not None:
        cases = cases[:args.engine_cases]
    total_worlds = 0
    total_answers = 0
    root_identity_cases = 0
    max_errors = {"flat": _error_bucket(), "factored": _error_bucket()}
    abs_tolerance = Decimal(args.abs_tolerance)
    rel_tolerance = Decimal(args.rel_tolerance)

    with tempfile.TemporaryDirectory(prefix="sparqlcirc-differential-") as directory:
        temporary = Path(directory)
        for case_index, case in enumerate(cases):
            data_path = temporary / ("case-%02d.ttl" % case_index)
            query_path = temporary / ("case-%02d.rq" % case_index)
            data_path.write_text(_data_text(case), encoding="utf-8")
            query_path.write_text(_query_text(case), encoding="utf-8")
            outputs = {
                mode: _java_circuit(args.java_bin, jar, mode, data_path, query_path,
                                    args.engine_timeout)
                for mode in ("flat", "factored")
            }
            parsed = {mode: _answer_roots(nt) for mode, nt in outputs.items()}
            flat_roots, flat_circ = parsed["flat"]
            factored_roots, factored_circ = parsed["factored"]
            if set(flat_roots) != set(factored_roots):
                raise AssertionError(
                    "%s term-aware answer mismatch flat=%r factored=%r"
                    % (case["name"], sorted(flat_roots), sorted(factored_roots)))
            if flat_roots == factored_roots:
                root_identity_cases += 1
            else:
                raise AssertionError("%s answer root gate identities differ" % case["name"])

            facts: Sequence[Fact] = case["facts"]
            full_world_answers = _bgp_answers(
                case["patterns"], case["select"], facts, [True] * len(facts))
            if set(flat_roots) != full_world_answers:
                raise AssertionError(
                    "%s candidate answers differ from the independent BGP evaluator: "
                    "circuit=%r oracle=%r"
                    % (case["name"], sorted(flat_roots), sorted(full_world_answers)))
            decimal_weights = {
                _iri("urn:bgp:token:" + fact[0])[1:-1]: Decimal(weight)
                for fact, weight in zip(facts, case["weights"])
            }
            float_weights = {key: float(value) for key, value in decimal_weights.items()}
            exact = {key: Decimal(0) for key in flat_roots}
            with localcontext() as ctx:
                ctx.prec = 100
                for bits in itertools.product((False, True), repeat=len(facts)):
                    expected = _bgp_answers(case["patterns"], case["select"], facts, bits)
                    weight = Decimal(1)
                    active_tokens: Set[str] = set()
                    for bit, fact in zip(bits, facts):
                        token = "urn:bgp:token:" + fact[0]
                        probability = decimal_weights[token]
                        weight *= probability if bit else Decimal(1) - probability
                        if bit:
                            active_tokens.add(token)
                    for key in flat_roots:
                        truth = key in expected
                        flat_value = _world_value(flat_circ, flat_roots[key], active_tokens)
                        factored_value = _world_value(
                            factored_circ, factored_roots[key], active_tokens)
                        if flat_value != truth or factored_value != truth:
                            raise AssertionError(
                                "%s/%s world %s: truth=%s flat=%s factored=%s"
                                % (case["name"], key, bits, truth, flat_value, factored_value))
                        if truth:
                            exact[key] += weight
                    total_worlds += 1

            for mode, (roots, circ) in parsed.items():
                actual = {
                    key: compile_bdd.wmc_enum(circ, root, float_weights)
                    for key, root in roots.items()
                }
                _record_errors(max_errors[mode], actual, exact,
                               "engine-%s-%s" % (case["name"], mode),
                               abs_tolerance, rel_tolerance)
            total_answers += len(flat_roots)

    return {
        "status": "ok",
        "jar": str(jar),
        "case_count": len(cases),
        "case_names": [case["name"] for case in cases],
        "world_count": total_worlds,
        "answer_count": total_answers,
        "term_aware_root_identity_cases": root_identity_cases,
        "errors": {
            mode: {"max_abs": str(bucket["abs"]), "max_rel": str(bucket["rel"])}
            for mode, bucket in max_errors.items()
        },
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=20260713)
    parser.add_argument("--dag-cases", type=int, default=24)
    parser.add_argument("--max-variables", type=int, default=8)
    parser.add_argument("--random-gates", type=int, default=18)
    parser.add_argument("--deep-depth", type=int, default=2500,
                        help="zero disables the deep iterative CUDD case")
    parser.add_argument("--require-cudd", action="store_true")
    parser.add_argument("--jar", default=str(DEFAULT_JAR))
    parser.add_argument("--java-bin", default="java",
                        help="Java executable used for the engine differential suite")
    parser.add_argument("--skip-engine", action="store_true")
    parser.add_argument("--engine-only", action="store_true")
    parser.add_argument("--require-engine", action="store_true")
    parser.add_argument("--engine-cases", type=int,
                        help="run only the first N fixed BGP cases")
    parser.add_argument("--engine-timeout", type=float, default=60.0)
    parser.add_argument("--abs-tolerance", default="1e-12")
    parser.add_argument("--rel-tolerance", default="1e-10")
    parser.add_argument("--json-output", type=Path,
                        help="also write the machine-readable summary to this path")
    parser.add_argument("--pretty", action="store_true")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.skip_engine and args.engine_only:
        parser.error("--skip-engine and --engine-only are mutually exclusive")
    if args.dag_cases < 1 or args.max_variables < 6 or args.random_gates < 1:
        parser.error("dag-cases>=1, max-variables>=6, and random-gates>=1 are required")
    if args.engine_cases is not None and not 1 <= args.engine_cases <= len(_bgp_cases()):
        parser.error("--engine-cases must be between 1 and %d" % len(_bgp_cases()))

    summary: Dict[str, Any] = {
        "schema": "sparqlcirc-differential-v1",
        "status": "ok",
        "config": {
            "seed": args.seed,
            "abs_tolerance": args.abs_tolerance,
            "rel_tolerance": args.rel_tolerance,
        },
    }
    try:
        if args.engine_only:
            summary["dag"] = {"status": "skipped", "reason": "--engine-only"}
        else:
            summary["dag"] = _run_dag_suite(args)
        if args.skip_engine:
            summary["engine"] = {"status": "skipped", "reason": "--skip-engine"}
        else:
            summary["engine"] = _run_engine_suite(args)
    except (AssertionError, compiler.BackendUnavailable, KeyError, OSError,
            RuntimeError, subprocess.SubprocessError, TypeError, ValueError) as exc:
        summary["status"] = "failed"
        summary["error"] = {"type": type(exc).__name__, "message": str(exc)}

    encoded = json.dumps(summary, indent=2 if args.pretty else None,
                         sort_keys=True, ensure_ascii=False)
    print(encoded)
    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(encoded + "\n", encoding="utf-8")
    return 0 if summary["status"] == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
