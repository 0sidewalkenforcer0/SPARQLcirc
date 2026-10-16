#!/usr/bin/env python3
"""Unsatisfiable answer roots are removed after compilation.

The difference rewriting matches the minuend alone, so it emits a root for
every mapping of the minuend, including mappings that every possible world
removes.  For ``Diff(P, P)`` every such root denotes ``A and not A``.  The
paper (Section 3.3 of the WWW 2027 draft) has the client drop every root whose
compiled function is unsatisfiable.  These tests check that:

* ``CompiledBatch.unsatisfiable_keys`` reports exactly the roots that compile
  to false, independently of the probabilities;
* ``ddnnf_wmc.satisfiable_text`` decides satisfiability of d4 and classic
  d-DNNF dumps structurally;
* ``pqe_from_artifact.evaluate`` (and ``d4_from_artifact.evaluate`` when a d4
  binary is available) drop exactly the roots that possible-world enumeration
  never produces, on circuits that the engine builds for MINUS queries, and
  keep a satisfiable root whose probability is 0.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


HERE = Path(__file__).resolve().parent
REFERENCE = HERE.parent
ROOT = REFERENCE.parent
if str(REFERENCE) not in sys.path:
    sys.path.insert(0, str(REFERENCE))

import compiler
import d4_from_artifact
import ddnnf_wmc
import pqe_from_artifact
import wmc


JAR = ROOT / "engine" / "target" / "npcs-rewrite.jar"
D4 = Path(os.environ["WWW27_D4"]) if os.environ.get("WWW27_D4") else None
OCC = "http://example.org/occurrenceOf"
D = "urn:d:"
TRIPLES = [
    ("A", "p", "B"), ("A", "p", "D"), ("G", "p", "B"), ("H", "p", "D"), ("H", "p", "B"),
]
EVENTS = {"urn:t:%d" % index: triple for index, triple in enumerate(TRIPLES)}


def iri(name: str) -> str:
    return D + name


def data_text() -> str:
    lines = []
    for subject, predicate, obj in TRIPLES:
        lines.append("<%s> <%s> <%s> ." % (iri(subject), iri(predicate), iri(obj)))
    for token, (subject, predicate, obj) in EVENTS.items():
        lines.append("<< <%s> <%s> <%s> >> <%s> <%s> ."
                     % (iri(subject), iri(predicate), iri(obj), OCC, token))
    return "\n".join(lines) + "\n"


def java(*arguments: str) -> bytes:
    completed = subprocess.run(["java", *arguments], capture_output=True, timeout=120, check=False)
    if completed.returncode != 0:
        raise AssertionError("java %s failed: %s" % (arguments[:3], completed.stderr.decode()[-2000:]))
    return completed.stdout


class UnsatisfiableKeysTest(unittest.TestCase):
    CIRCUIT = {
        "x1": ("leaf", "x1"),
        "x2": ("leaf", "x2"),
        "x3": ("leaf", "x3"),
        "a": ("times", ("x1", "x2")),
        "contradiction": ("minus", ("a", "a")),
        "zero-weight": ("times", ("x1", "x3")),
        "plain": ("plus", ("x1", "x2")),
        "top": ("times", ()),
        "bottom": ("plus", ()),
    }
    ROOTS = {
        "contradiction": "contradiction",
        "zero-weight": "zero-weight",
        "plain": "plain",
        "top": "top",
        "bottom": "bottom",
    }

    def test_oracle_reports_exactly_the_false_roots(self):
        for mode in ("shared", "per-root"):
            with self.subTest(mode=mode):
                batch = compiler.compile_many(self.CIRCUIT, self.ROOTS, mode=mode, backend="oracle")
                self.assertEqual(["bottom", "contradiction"], batch.unsatisfiable_keys())

    def test_report_does_not_depend_on_probabilities(self):
        batch = compiler.compile_many(self.CIRCUIT, self.ROOTS, backend="oracle")
        values = batch.wmc_many({"x1": 0.5, "x2": 0.5, "x3": 0.0})
        self.assertEqual(0.0, values["zero-weight"])
        self.assertNotIn("zero-weight", batch.unsatisfiable_keys())
        self.assertEqual(1.0, values["top"])


def _cudd_available() -> bool:
    try:
        import dd.cudd  # noqa: F401
    except ImportError:
        return False
    return True


class CuddUnsatisfiableKeysTest(unittest.TestCase):
    @unittest.skipUnless(_cudd_available(), "needs dd.cudd")
    def test_cudd_reports_the_same_roots_as_the_oracle(self):
        oracle = compiler.compile_many(UnsatisfiableKeysTest.CIRCUIT, UnsatisfiableKeysTest.ROOTS,
                                       backend="oracle")
        cudd = compiler.compile_many(UnsatisfiableKeysTest.CIRCUIT, UnsatisfiableKeysTest.ROOTS,
                                     backend="cudd")
        self.assertEqual(oracle.unsatisfiable_keys(), cudd.unsatisfiable_keys())


class DdnnfSatisfiableTest(unittest.TestCase):
    def test_d4_format(self):
        satisfiable = "o 1 0\no 2 0\nt 3 0\n2 3 -1 2 0\n2 3 1 0\n1 2 0\n"
        unsatisfiable = "o 1 0\nf 2 0\n1 2 1 0\n"
        contradictory_arc = "o 1 0\nt 2 0\n1 2 1 -1 0\n"
        conjunction = "a 1 0\nt 2 0\nf 3 0\n1 2 1 0\n1 3 2 0\n"
        self.assertTrue(ddnnf_wmc.satisfiable_text(satisfiable))
        self.assertFalse(ddnnf_wmc.satisfiable_text(unsatisfiable))
        self.assertFalse(ddnnf_wmc.satisfiable_text(contradictory_arc))
        self.assertFalse(ddnnf_wmc.satisfiable_text(conjunction))

    def test_classic_format(self):
        satisfiable = "nnf 4 3 2\nL 1\nL -2\nA 2 0 1\nO 0 1 2\n"
        unsatisfiable = "nnf 3 1 1\nL 1\nO 0 0\nA 2 0 1\n"
        self.assertTrue(ddnnf_wmc.satisfiable_text(satisfiable))
        self.assertFalse(ddnnf_wmc.satisfiable_text(unsatisfiable))

    def test_satisfiable_dump_with_zero_weight_is_still_satisfiable(self):
        dump = "o 1 0\nt 2 0\n1 2 1 0\n"
        self.assertTrue(ddnnf_wmc.satisfiable_text(dump))
        self.assertEqual(0.0, ddnnf_wmc.evaluate_text(dump, {1: (0.0, 1.0)}).probability)


@unittest.skipUnless(JAR.is_file(), "needs engine/target/npcs-rewrite.jar")
class EngineUnsatisfiableRootsTest(unittest.TestCase):
    """Circuits the engine builds for MINUS queries, against possible worlds."""

    CASES = {
        "minus-self": (
            ["?x", "?y"],
            "SELECT ?x ?y WHERE { ?x <%s> ?y MINUS { ?x <%s> ?y } }" % (iri("p"), iri("p")),
            ("minus", ("bgp", [("?x", iri("p"), "?y")]), ("bgp", [("?x", iri("p"), "?y")])),
        ),
        "minus-partial": (
            ["?x"],
            "SELECT ?x WHERE { ?x <%s> ?y MINUS { ?x <%s> <%s> } }" % (iri("p"), iri("p"), iri("B")),
            ("minus", ("bgp", [("?x", iri("p"), "?y")]), ("bgp", [("?x", iri("p"), iri("B"))])),
        ),
    }

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temporary.name)
        cls.data = cls.root / "toy.ttls"
        cls.data.write_text(data_text(), encoding="utf-8")
        cls.weights = {token: 0.5 for token in EVENTS}
        cls.weights["urn:t:3"] = 0.0

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def _circuit(self, case: str, construction: str) -> Path:
        query = self.root / ("%s.rq" % case)
        query.write_text(self.CASES[case][1] + "\n", encoding="utf-8")
        nt = java("-cp", str(JAR), "npcs.circuit.CircuitRun", "--construction=%s" % construction,
                  "SPARQL_Star", str(self.data), str(query))
        path = self.root / ("%s.%s.circuit.nt" % (case, construction))
        path.write_bytes(nt)
        return path

    def _probabilities_file(self) -> Path:
        path = self.root / "probabilities.json"
        path.write_text(json.dumps(self.weights), encoding="utf-8")
        return path

    @staticmethod
    def _rows(output: Path) -> list:
        return [json.loads(line) for line in
                (output / "probabilities.jsonl").read_text(encoding="utf-8").splitlines()]

    def _possible_answers(self, case: str) -> set:
        selection, _text, dsl = self.CASES[case]
        events = {token: tuple(iri(part) for part in triple) for token, triple in EVENTS.items()}
        positive = {token: 0.5 for token in EVENTS}
        truth = wmc.pwe(dsl, selection, events, positive)
        return {tuple(sorted(dict(answer).items())) for answer, value in truth.items() if value > 0.0}

    @staticmethod
    def _answer(row: dict) -> tuple:
        out = []
        for name, value in row["binding"]:
            if value[0] == "iri":
                out.append(("?" + name, value[1]))
        return tuple(sorted(out))

    def test_dropped_roots_are_exactly_the_impossible_answers(self):
        probabilities = self._probabilities_file()
        for case in self.CASES:
            possible = self._possible_answers(case)
            for construction in ("flat", "factorised"):
                with self.subTest(case=case, construction=construction):
                    circuit = self._circuit(case, construction)
                    kept = self.root / ("%s.%s.kept" % (case, construction))
                    kept_metrics = pqe_from_artifact.evaluate(
                        "circuit", circuit, kept, "oracle", probabilities, None, {},
                        drop_unsatisfiable=False)
                    dropped = self.root / ("%s.%s.dropped" % (case, construction))
                    metrics = pqe_from_artifact.evaluate(
                        "circuit", circuit, dropped, "oracle", probabilities, None, {})
                    reported = {self._answer(row) for row in self._rows(dropped)}
                    candidates = {self._answer(row) for row in self._rows(kept)}
                    self.assertEqual(possible, reported)
                    self.assertEqual(len(candidates) - len(reported),
                                     metrics["unsatisfiable_roots_dropped"])
                    self.assertGreater(metrics["unsatisfiable_roots_dropped"], 0)
                    self.assertEqual(0, kept_metrics["unsatisfiable_roots_dropped"])
                    if D4 is not None:
                        d4_output = self.root / ("%s.%s.d4" % (case, construction))
                        d4_metrics = d4_from_artifact.evaluate(
                            circuit, d4_output, D4, None, 120.0, {}, 0.05,
                            probabilities=probabilities)
                        self.assertEqual(reported, {self._answer(row) for row in self._rows(d4_output)})
                        self.assertEqual(metrics["unsatisfiable_roots_dropped"],
                                         d4_metrics["unsatisfiable_roots_dropped"])

    def test_zero_probability_answer_is_kept(self):
        probabilities = self._probabilities_file()
        circuit = self._circuit("minus-partial", "flat")
        output = self.root / "zero.pqe"
        pqe_from_artifact.evaluate("circuit", circuit, output, "oracle", probabilities, None, {})
        values = {self._answer(row): row["probability"] for row in self._rows(output)}
        h_answer = (("?x", iri("H")),)
        self.assertIn(h_answer, values)
        self.assertEqual(0.0, values[h_answer])


if __name__ == "__main__":
    unittest.main()
