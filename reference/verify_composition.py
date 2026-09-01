#!/usr/bin/env python3
"""Differential check that a COMPOSED query's circuit denotes the right event.

Thm. 4.13 covers every composition of the supported operators, and the engine builds
them by expanding OPTIONAL, pushing UNION upward and materializing a composite
operand as a private row relation. That machinery is easy to get subtly wrong in a
way that still produces a plausible circuit, so each shape is compared against two
INDEPENDENT oracles rather than against itself:

* ``gamma`` + ``wmc.pwe`` — the Python algebraic reference and possible-world
  enumeration, for the operator shapes its DSL models (join / union / minus /
  optional).
* ``rdflib`` over every possible world — the plain query, no provenance, evaluated
  by a third-party SPARQL implementation. This is the only oracle for FILTER, which
  the Python DSL does not model, and FILTER placement is precisely where a
  mis-hoisted condition silently deletes correct answers.

Both construction modes are checked: flat and factored must agree with the oracles
and with each other.

A note on the rdflib oracle, because it bit us: probability accrues per WORLD per
ANSWER, not per result row. A union whose branches both match yields the same answer
twice in one world; adding the world's weight per row inflates the probability (it
reported 1.0 where the truth was 0.75). Deduplicate before accumulating.

    python3 reference/verify_composition.py
"""
from __future__ import annotations

import itertools
import subprocess
import sys
from pathlib import Path

import circuit_io
import compile_bdd
import gamma
import gates
import wmc

ROOT = Path(__file__).resolve().parents[1]
JAR = ROOT / "engine" / "target" / "npcs-rewrite.jar"
D = "urn:d:"
P = 0.5

# One graph for every check: two subjects, four predicates, so operands overlap
# partially and OPTIONAL/MINUS have both matched and unmatched cases.
TOKENS = {
    "t1": ("a", "p0", "b"),
    "t2": ("a", "p1", "c"),
    "t3": ("a", "p2", "m"),
    "t4": ("g", "p0", "e"),
    "t5": ("g", "p1", "zz"),
}

A = ("bgp", [("?x", "p0", "?v0")])
B = ("bgp", [("?x", "p1", "?v1")])
C = ("bgp", [("?x", "p2", "?v2")])

# (name, SPARQL, gamma query or None, projected variables)
CASES = [
    ("join(union, bgp)",
     "SELECT ?x WHERE { { { ?x <urn:d:p0> ?v0 . } UNION { ?x <urn:d:p1> ?v1 . } } ?x <urn:d:p2> ?v2 . }",
     ("join", ("union", A, B), C), ["?x"]),
    ("join(minus, bgp)",
     "SELECT ?x WHERE { { ?x <urn:d:p0> ?v0 . MINUS { ?x <urn:d:p1> ?v1 . } } ?x <urn:d:p2> ?v2 . }",
     ("join", ("minus", A, B), C), ["?x"]),
    ("join(optional, bgp)",
     "SELECT ?x ?v1 WHERE { { ?x <urn:d:p0> ?v0 . OPTIONAL { ?x <urn:d:p1> ?v1 . } } ?x <urn:d:p2> ?v2 . }",
     ("join", ("optional", A, B), C), ["?x", "?v1"]),
    ("optional(union, bgp)",
     "SELECT ?x WHERE { { { ?x <urn:d:p0> ?v0 . } UNION { ?x <urn:d:p1> ?v1 . } } OPTIONAL { ?x <urn:d:p2> ?v2 . } }",
     ("optional", ("union", A, B), C), ["?x"]),
    ("optional(bgp, union)",
     "SELECT ?x WHERE { ?x <urn:d:p0> ?v0 . OPTIONAL { { ?x <urn:d:p1> ?v1 . } UNION { ?x <urn:d:p2> ?v2 . } } }",
     ("optional", A, ("union", B, C)), ["?x"]),
    ("two optionals",
     "SELECT ?x ?v1 ?v2 WHERE { ?x <urn:d:p0> ?v0 . OPTIONAL { ?x <urn:d:p1> ?v1 . } "
     "OPTIONAL { ?x <urn:d:p2> ?v2 . } }",
     ("optional", ("optional", A, B), C), ["?x", "?v1", "?v2"]),
    ("optional(bgp, optional)",
     "SELECT ?x ?v1 ?v2 WHERE { ?x <urn:d:p0> ?v0 . OPTIONAL { ?x <urn:d:p1> ?v1 . "
     "OPTIONAL { ?x <urn:d:p2> ?v2 . } } }",
     ("optional", A, ("optional", B, C)), ["?x", "?v1", "?v2"]),
    ("optional(minus, bgp)",
     "SELECT ?x ?v2 WHERE { { ?x <urn:d:p0> ?v0 . MINUS { ?x <urn:d:p1> ?v1 . } } "
     "OPTIONAL { ?x <urn:d:p2> ?v2 . } }",
     ("optional", ("minus", A, B), C), ["?x", "?v2"]),
    ("optional(bgp, minus)",
     "SELECT ?x ?v1 WHERE { ?x <urn:d:p0> ?v0 . OPTIONAL { ?x <urn:d:p1> ?v1 . "
     "MINUS { ?x <urn:d:p2> ?v2 . } } }",
     ("optional", A, ("minus", B, C)), ["?x", "?v1"]),
    ("right-nested minus",
     "SELECT ?x WHERE { ?x <urn:d:p0> ?v0 . MINUS { ?x <urn:d:p1> ?v1 . "
     "MINUS { ?x <urn:d:p2> ?v2 . } } }",
     ("minus", A, ("minus", B, C)), ["?x"]),
    ("chained minus",
     "SELECT ?x WHERE { { ?x <urn:d:p0> ?v0 . MINUS { ?x <urn:d:p1> ?v1 . } } "
     "MINUS { ?x <urn:d:p2> ?v2 . } }",
     ("minus", ("minus", A, B), C), ["?x"]),
    # FILTER: rdflib only. The condition's PLACEMENT is what is under test.
    ("filter over a union",
     "SELECT ?x WHERE { { { ?x <urn:d:p0> ?v0 . } UNION { ?x <urn:d:p1> ?v1 . } } "
     "FILTER(?x != <urn:d:zzz>) }",
     None, ["?x"]),
    ("filter after an optional",
     "SELECT ?x ?v1 WHERE { ?x <urn:d:p0> ?v0 . OPTIONAL { ?x <urn:d:p1> ?v1 . } "
     "FILTER(?v0 != <urn:d:e>) }",
     None, ["?x", "?v1"]),
    ("filter over a composite join",
     "SELECT ?x ?v2 WHERE { { ?x <urn:d:p0> ?v0 . MINUS { ?x <urn:d:p1> ?v1 . } } "
     "?x <urn:d:p2> ?v2 . FILTER(?v2 != <urn:d:zzz>) }",
     None, ["?x", "?v2"]),
    ("filter inside a minus operand",
     "SELECT ?x ?v0 WHERE { { ?x <urn:d:p0> ?v0 . FILTER(?v0 != <urn:d:e>) "
     "MINUS { ?x <urn:d:p1> ?v1 . } } ?x <urn:d:p2> ?v2 . }",
     None, ["?x", "?v0"]),
    # The condition restricts which rows REMOVE; lifting it out of the subtrahend
    # would also delete answers, so this pins that it is not lifted.
    ("filter inside a subtrahend",
     "SELECT ?x ?v0 WHERE { ?x <urn:d:p0> ?v0 . MINUS { ?x <urn:d:p1> ?v0 . "
     "FILTER(?v0 != <urn:d:zz>) } }",
     None, ["?x", "?v0"]),
]


def _round(mapping):
    return {k: round(v, 9) for k, v in sorted(mapping.items()) if v > 1e-12}


def _as_iris(mapping):
    """The gamma DSL binds bare names; the engine and rdflib bind full IRIs. One key space."""
    return {tuple(sorted((var, D + value) for var, value in key)): probability
            for key, probability in mapping.items()}


def _rdflib_pwe(query, selected, tokens=None):
    """Every possible world, the plain query, a third-party SPARQL engine."""
    import rdflib

    tokens = TOKENS if tokens is None else tokens
    names = list(tokens)
    weight = P ** len(names)                 # uniform: every token has probability P
    accumulated = {}
    for bits in itertools.product((0, 1), repeat=len(names)):
        graph = rdflib.Graph()
        for name, bit in zip(names, bits):
            if bit:
                s, p, o = tokens[name]
                graph.add((rdflib.URIRef(D + s), rdflib.URIRef(D + p), rdflib.URIRef(D + o)))
        seen = set()                          # set semantics: one answer, one event
        for row in graph.query(query):
            binding = row.asdict()
            seen.add(tuple(sorted((v, str(binding[v[1:]]))
                                  for v in selected
                                  if binding.get(v[1:]) is not None)))
        for key in seen:
            accumulated[key] = accumulated.get(key, 0.0) + weight
    return _round(accumulated)


def _gamma_pwe(query, selected):
    plain = {name: TOKENS[name] for name in TOKENS}
    weights = {name: P for name in TOKENS}
    truth = wmc.pwe(query, selected, plain, weights)
    return _round(_as_iris({tuple(sorted(k)): v for k, v in truth.items()}))


def _gamma_circuit(query, selected):
    plain = {name: TOKENS[name] for name in TOKENS}
    weights = {name: P for name in TOKENS}
    circuit = gates.Circuit()
    table = gamma.eval_q(circuit, query, plain)
    return _round(_as_iris({tuple(sorted(k)): wmc.prob(circuit, g, weights)
                            for k, g in gamma.project(circuit, table, selected).items()}))


def _engine(sparql, selected, mode, workdir, tokens=None, datafile="data.ttl"):
    tokens = TOKENS if tokens is None else tokens
    query_file = workdir / "query.sparql"
    query_file.write_text(sparql + "\n", encoding="utf-8")
    completed = subprocess.run(
        ["java", "-cp", str(JAR), "npcs.circuit.CircuitRun", "--construction=" + mode,
         "Standard", str(workdir / datafile), str(query_file)],
        capture_output=True, text=True, check=True)
    circ, answers, bindings = circuit_io.parse(completed.stdout)
    weights = {D + name: P for name in tokens}
    out = {}
    for gate in answers:
        key = tuple(sorted((("?" + v), value.split(circuit_io.US)[-1])
                           for v, value in bindings[gate].items() if value != "u"))
        out[key] = compile_bdd.probability(circ, gate, weights)[0]
    return _round(out)


def _write_data(workdir, tokens, filename):
    lines = ["@prefix d: <urn:d:> .",
             "@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> ."]
    for name, (s, p, o) in tokens.items():
        lines.append(f"d:{s} d:{p} d:{o} .")
        lines.append(f"d:{name} rdf:subject d:{s} ; rdf:predicate d:{p} ; rdf:object d:{o} .")
    (workdir / filename).write_text("\n".join(lines) + "\n", encoding="utf-8")


# A chain plus two side edges, so a closure atom has something to compose with.
PATH_TOKENS = {
    "t1": ("a", "p", "b"),
    "t2": ("b", "p", "c"),
    "t3": ("c", "q", "z"),
    "t4": ("b", "q", "y"),
}

# Def. 4.7 clause 2 says an atom's materialized relation is consumed "in the same manner as
# triple-pattern gates". rdflib is the oracle: it implements property paths natively, and the
# Python DSL's path support does not model composition with the rest of the algebra.
PATH_CASES = [
    ("closure atom alone",
     "SELECT ?y WHERE { <urn:d:a> <urn:d:p>+ ?y }", ["?y"]),
    ("closure JOIN bgp",
     "SELECT ?y ?z WHERE { <urn:d:a> <urn:d:p>+ ?y . ?y <urn:d:q> ?z }", ["?y", "?z"]),
    ("closure UNION bgp",
     "SELECT ?y WHERE { { <urn:d:a> <urn:d:p>+ ?y } UNION { <urn:d:a> <urn:d:q> ?y } }", ["?y"]),
    ("closure MINUS bgp",
     "SELECT ?y WHERE { <urn:d:a> <urn:d:p>+ ?y MINUS { ?y <urn:d:q> ?z } }", ["?y"]),
    ("closure OPTIONAL bgp",
     "SELECT ?y ?z WHERE { <urn:d:a> <urn:d:p>+ ?y OPTIONAL { ?y <urn:d:q> ?z } }", ["?y", "?z"]),
    ("bgp MINUS closure",
     "SELECT ?y WHERE { ?y <urn:d:q> ?z MINUS { <urn:d:a> <urn:d:p>+ ?y } }", ["?y"]),
    ("closure with FILTER",
     "SELECT ?y WHERE { <urn:d:a> <urn:d:p>+ ?y FILTER(?y != <urn:d:c>) }", ["?y"]),
    ("variable-source closure JOIN bgp",
     "SELECT ?x ?y WHERE { ?x <urn:d:p>+ ?y . ?x <urn:d:q> ?w }", ["?x", "?y"]),
    ("zero-or-more closure JOIN bgp",
     "SELECT ?y ?z WHERE { <urn:d:a> <urn:d:p>* ?y . ?y <urn:d:q> ?z }", ["?y", "?z"]),
]


def main() -> int:
    import tempfile

    if not JAR.is_file():
        print("verify_composition: engine/target/npcs-rewrite.jar is missing; run "
              "mvn -q -f engine/pom.xml package", file=sys.stderr)
        return 1
    try:
        import rdflib  # noqa: F401
    except ImportError:
        # The FILTER cases have no second oracle, so a missing rdflib is a
        # weaker gate, not a passing one. Fail closed, and say what to install.
        print("verify_composition: rdflib is missing and it is the only oracle for "
              "the FILTER cases; run python -m pip install 'rdflib>=6.3,<8'",
              file=sys.stderr)
        return 1
    failures = 0
    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        _write_data(workdir, TOKENS, "data.ttl")
        _write_data(workdir, PATH_TOKENS, "paths.ttl")
        for name, sparql, query, selected in CASES:
            oracles = {"rdflib-pwe": _rdflib_pwe(sparql, selected)}
            if query is not None:
                oracles["gamma-pwe"] = _gamma_pwe(query, selected)
                oracles["gamma-circuit"] = _gamma_circuit(query, selected)
            results = {mode: _engine(sparql, selected, mode, workdir)
                       for mode in ("flat", "factorised")}
            disagreeing = [label for label, expected in oracles.items()
                           if any(expected != got for got in results.values())]
            if disagreeing:
                failures += 1
                print(f"  [FAIL] {name}")
                for mode, got in results.items():
                    print(f"           engine {mode:8} {got}")
                for label, expected in oracles.items():
                    print(f"           {label:16} {expected}")
            else:
                print(f"  [OK ] {name:32} {len(oracles)} oracle(s), "
                      f"{len(next(iter(results.values())))} answers")
        for name, sparql, selected in PATH_CASES:
            truth = _rdflib_pwe(sparql, selected, PATH_TOKENS)
            results = {mode: _engine(
                           sparql, selected, mode, workdir, PATH_TOKENS, "paths.ttl")
                       for mode in ("flat", "factorised")}
            if any(truth != got for got in results.values()):
                failures += 1
                print(f"  [FAIL] {name}")
                for mode, got in results.items():
                    print(f"           engine {mode:8} {got}")
                print(f"           rdflib-pwe       {truth}")
            else:
                print(f"  [OK ] {name:32} rdflib oracle, {len(truth)} answers")
    print("COMPOSITION DIFFERENTIAL " + ("ALL OK" if failures == 0 else f"{failures} FAILED"))
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
