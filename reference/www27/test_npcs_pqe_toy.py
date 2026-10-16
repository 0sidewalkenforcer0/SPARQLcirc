#!/usr/bin/env python3
"""E2 correctness on toy data: NPCS merged DAG = SPARQLcirc circuit = possible worlds.

For each query (BGP chain, BGP self-join, UNION, OPTIONAL, OPTIONAL under a
projection, MINUS) and each layout (asserted-plus-lookup ``SPARQL_Star`` and
lookup-only ``SPARQL_Star_Pure``):

* N: our jar rewrites the query; the rewritten text runs on an in-memory RDF4J
  store (``npcs.LocalSelect``); the SPARQL JSON response goes through
  npcs_postprocess.py (parse, Boolean normalization, query-global
  hash-consing) and the merged DAG is compiled and evaluated twice: in
  process (``compile_and_wmc`` with the DFS variable order) and through the
  post-hoc artifact path (``pqe_from_artifact.py --kind npcs-shared``);
* N-orig (BGP queries only, when $NPCS_ORIG_JAR is set): the same with the
  original jar's rewrite;
* C-flat and C-factorised: CircuitRun on the same data file, evaluated with
  ``pqe_from_artifact.py --kind circuit``;
* ground truth: exhaustive possible-world enumeration of the plain query
  (``reference/wmc.py``) over the 10 independent occurrence events.

All answer probabilities must agree within 1e-9, answer keys included.  The
oracle backend (pure-Python ROBDD) is used so the test needs no native
library.  With ``WWW27_D4=/path/to/d4`` the d4 path (``d4_from_artifact.py``,
one CNF per root, now with ``not`` gates for NPCS DAGs) is checked as well;
locally that is the exploratory d4 v1 CLI, the cluster uses the pinned d4v2.
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

import d4_from_artifact
import event_probabilities
try:
    import pyoxigraph
except ImportError:
    pyoxigraph = None
import npcs_orig
import npcs_postprocess
import pqe_from_artifact
import wmc


JAR = ROOT / "engine" / "target" / "npcs-rewrite.jar"
ORIG_JAR = Path(os.environ["NPCS_ORIG_JAR"]) if os.environ.get("NPCS_ORIG_JAR") else None
D4 = Path(os.environ["WWW27_D4"]) if os.environ.get("WWW27_D4") else None
OCC = "http://example.org/occurrenceOf"
TOLERANCE = 1e-9
D = "urn:d:"
TRIPLES = [
    ("A", "p", "B"), ("A", "p", "D"), ("B", "q", "C"), ("D", "q", "C"), ("A", "r", "E"),
    ("A", "p", "F"), ("G", "p", "B"), ("B", "q", "H"), ("A", "r", "B"),
]
# urn:t:9 is a second, independent occurrence of the first triple.
EVENTS = {"urn:t:%d" % index: triple for index, triple in enumerate(TRIPLES)}
EVENTS["urn:t:9"] = TRIPLES[0]


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


def pattern(subject: str, predicate: str, obj: str):
    term = lambda value: value if value.startswith("?") else iri(value)
    return term(subject), iri(predicate), term(obj)


def sparql_pattern(subject: str, predicate: str, obj: str) -> str:
    term = lambda value: value if value.startswith("?") else "<%s>" % iri(value)
    return "%s <%s> %s ." % (term(subject), iri(predicate), term(obj))


CASES = {
    "bgp-chain": (
        ["?x", "?z"],
        "SELECT ?x ?z WHERE { %s %s }" % (sparql_pattern("?x", "p", "?y"), sparql_pattern("?y", "q", "?z")),
        ("bgp", [pattern("?x", "p", "?y"), pattern("?y", "q", "?z")]),
        True,
    ),
    "bgp-selfjoin": (
        ["?d"],
        "SELECT ?d WHERE { %s %s }" % (sparql_pattern("?x", "p", "?d"), sparql_pattern("?y", "p", "?d")),
        ("bgp", [pattern("?x", "p", "?d"), pattern("?y", "p", "?d")]),
        True,
    ),
    "union": (
        ["?x"],
        "SELECT ?x WHERE { { %s } UNION { %s } }" % (sparql_pattern("A", "p", "?x"), sparql_pattern("A", "r", "?x")),
        ("union", ("bgp", [pattern("A", "p", "?x")]), ("bgp", [pattern("A", "r", "?x")])),
        False,
    ),
    "optional": (
        ["?x", "?y", "?z"],
        "SELECT ?x ?y ?z WHERE { %s OPTIONAL { %s } }" % (sparql_pattern("?x", "p", "?y"), sparql_pattern("?y", "q", "?z")),
        ("optional", ("bgp", [pattern("?x", "p", "?y")]), ("bgp", [pattern("?y", "q", "?z")])),
        False,
    ),
    "optional-projected": (
        ["?x"],
        "SELECT ?x WHERE { %s OPTIONAL { %s } }" % (sparql_pattern("?x", "p", "?y"), sparql_pattern("?y", "q", "?z")),
        ("optional", ("bgp", [pattern("?x", "p", "?y")]), ("bgp", [pattern("?y", "q", "?z")])),
        False,
    ),
    "minus": (
        ["?x", "?y"],
        "SELECT ?x ?y WHERE { %s MINUS { %s } }" % (sparql_pattern("?x", "p", "?y"), sparql_pattern("?y", "q", "?z")),
        ("minus", ("bgp", [pattern("?x", "p", "?y")]), ("bgp", [pattern("?y", "q", "?z")])),
        False,
    ),
}


def answer_key(binding, selection) -> str:
    values = dict(binding)
    pairs = [
        [variable[1:], ["iri", values[variable]] if variable in values else ["unbound"]]
        for variable in sorted(selection)
    ]
    return json.dumps(pairs, ensure_ascii=False, separators=(",", ":"))


def ground_truth(case: str, weights) -> dict:
    selection, _text, dsl, _bgp = CASES[case]
    events = {token: tuple(iri(part) for part in triple) for token, triple in EVENTS.items()}
    truth = wmc.pwe(dsl, selection, events, weights)
    return {answer_key(answer, selection): value for answer, value in truth.items() if value > 1e-15}


def nonzero(values: dict) -> dict:
    return {key: value for key, value in values.items() if value > 1e-15}


def java(*arguments: str) -> bytes:
    completed = subprocess.run(["java", *arguments], capture_output=True, timeout=120, check=False)
    if completed.returncode != 0:
        raise AssertionError("java %s failed: %s" % (arguments[:3], completed.stderr.decode()[-2000:]))
    return completed.stdout


@unittest.skipUnless(JAR.is_file(), "needs engine/target/npcs-rewrite.jar")
class NpcsPqeToyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temporary.name)
        cls.data = cls.root / "toy.ttls"
        cls.data.write_text(data_text(), encoding="utf-8")
        cls.weights = event_probabilities.event_weights(sorted(EVENTS), 42)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def _query_file(self, case: str) -> Path:
        path = self.root / ("%s.rq" % case)
        if not path.exists():
            path.write_text(CASES[case][1] + "\n", encoding="utf-8")
        return path

    def _npcs(self, case: str, rewritten: str, label: str) -> dict:
        rewritten_path = self.root / ("%s.%s.rq" % (case, label))
        rewritten_path.write_text(rewritten, encoding="utf-8")
        response = java("-cp", str(JAR), "npcs.LocalSelect", str(self.data), str(rewritten_path))
        result = npcs_postprocess.process_response_bytes(response, token_regex=r"^urn:t:[0-9]+$")
        order = npcs_postprocess.variable_order(result, "dfs-first-appearance")
        in_process, _metrics = npcs_postprocess.compile_and_wmc(
            result, "oracle", {token: self.weights[token] for token in result.dag.tokens()},
            order=order,
        )
        output = self.root / ("%s.%s.pp" % (case, label))
        npcs_postprocess.persist_postprocess(result, output, {"query_id": case})
        artifact = self._artifact_probabilities("npcs-shared", output / "npcs-hc-dag.json",
                                                "%s.%s" % (case, label))
        self.assertEqual(set(in_process), set(artifact))
        for key in in_process:
            self.assertAlmostEqual(in_process[key], artifact[key], delta=TOLERANCE)
        if D4 is not None:
            self._check_d4("npcs-shared", output / "npcs-hc-dag.json", artifact, "%s.%s" % (case, label))
        return artifact

    def _artifact_probabilities(self, kind: str, source: Path, label: str) -> dict:
        output = self.root / ("%s.%s.pqe" % (label, kind))
        pqe_from_artifact.evaluate(kind, source, output, "oracle", None, None, {},
                                   probability_seed=42)
        rows = [json.loads(line) for line in
                (output / "probabilities.jsonl").read_text(encoding="utf-8").splitlines()]
        return {row["answer_key"]: float(row["probability"]) for row in rows}

    def _check_d4(self, kind: str, source: Path, expected: dict, label: str) -> None:
        output = self.root / ("%s.%s.d4" % (label, kind))
        d4_from_artifact.evaluate(source, output, D4, None, 120.0, {}, 0.05,
                                  probability_seed=42, kind=kind)
        rows = [json.loads(line) for line in
                (output / "probabilities.jsonl").read_text(encoding="utf-8").splitlines()]
        values = {row["answer_key"]: float(row["probability"]) for row in rows}
        self.assertEqual(set(expected), set(values), label)
        for key in expected:
            self.assertAlmostEqual(expected[key], values[key], delta=1e-6, msg=label)

    def _circuit(self, case: str, scheme: str, construction: str) -> dict:
        nt = java("-cp", str(JAR), "npcs.circuit.CircuitRun", "--construction=%s" % construction,
                  scheme, str(self.data), str(self._query_file(case)))
        path = self.root / ("%s.%s.%s.circuit.nt" % (case, scheme, construction))
        path.write_bytes(nt)
        values = self._artifact_probabilities("circuit", path, "%s.%s.%s" % (case, scheme, construction))
        if D4 is not None:
            self._check_d4("circuit", path, values, "%s.%s.%s" % (case, scheme, construction))
        return values

    def _assert_same(self, expected: dict, actual: dict, label: str) -> None:
        expected, actual = nonzero(expected), nonzero(actual)
        self.assertEqual(sorted(expected), sorted(actual), label)
        for key in expected:
            self.assertAlmostEqual(expected[key], actual[key], delta=TOLERANCE, msg="%s %s" % (label, key))

    def test_npcs_dag_equals_circuit_equals_possible_worlds(self):
        checked = 0
        for case, (_selection, _text, _dsl, is_bgp) in CASES.items():
            truth = ground_truth(case, self.weights)
            self.assertTrue(truth, case)
            for scheme in ("SPARQL_Star", "SPARQL_Star_Pure"):
                with self.subTest(case=case, scheme=scheme):
                    rewritten = java("-jar", str(JAR), "rewrite", scheme, "path",
                                     str(self._query_file(case))).decode("utf-8")
                    self._assert_same(truth, self._npcs(case, rewritten, "N-" + scheme), "N " + case)
                    for construction in ("flat", "factorised"):
                        self._assert_same(truth, self._circuit(case, scheme, construction),
                                          "C-%s %s" % (construction, case))
                    checked += 1
            if is_bgp and ORIG_JAR is not None:
                with self.subTest(case=case, method="N-orig"):
                    text, _metrics = npcs_orig.run_original(
                        ORIG_JAR, "SPARQL_Star", self._query_file(case))
                    self.assertIsNone(npcs_orig.check_rewritten(text))
                    self._assert_same(truth, self._npcs(case, text, "N-orig"), "N-orig " + case)
        self.assertEqual(len(CASES) * 2, checked)

    def test_lookup_only_and_inline_circuits_are_identical(self):
        for case in CASES:
            for construction in ("flat", "factorised"):
                with self.subTest(case=case, construction=construction):
                    inline = java("-cp", str(JAR), "npcs.circuit.CircuitRun",
                                  "--construction=%s" % construction, "SPARQL_Star",
                                  str(self.data), str(self._query_file(case)))
                    pure = java("-cp", str(JAR), "npcs.circuit.CircuitRun",
                                "--construction=%s" % construction, "SPARQL_Star_Pure",
                                str(self.data), str(self._query_file(case)))
                    self.assertEqual(sorted(inline.splitlines()), sorted(pure.splitlines()))

    @unittest.skipUnless(ORIG_JAR is not None and pyoxigraph is not None,
                         "needs $NPCS_ORIG_JAR and pyoxigraph")
    def test_original_jar_output_runs_on_oxigraph(self):
        """The original jar writes quoted triples with tabs inside ``<< >>``; Oxigraph must parse
        them and bind the provenance of every answer, and the merged DAG must equal the possible
        worlds, as it does on RDF4J."""
        store = pyoxigraph.Store()
        store.load(data_text().encode("utf-8"), format=pyoxigraph.RdfFormat.TURTLE)
        for case, (_selection, _text, _dsl, is_bgp) in CASES.items():
            if not is_bgp:
                continue
            with self.subTest(case=case):
                text, _metrics = npcs_orig.run_original(ORIG_JAR, "SPARQL_Star", self._query_file(case))
                self.assertIsNone(npcs_orig.check_rewritten(text))
                body = store.query(text).serialize(format=pyoxigraph.QueryResultsFormat.JSON)
                result = npcs_postprocess.process_response_bytes(body, token_regex=r"^urn:t:[0-9]+$")
                order = npcs_postprocess.variable_order(result, "dfs-first-appearance")
                values, _metrics = npcs_postprocess.compile_and_wmc(
                    result, "oracle", {token: self.weights[token] for token in result.dag.tokens()},
                    order=order,
                )
                self._assert_same(ground_truth(case, self.weights), values, "N-orig/Oxigraph " + case)


if __name__ == "__main__":
    unittest.main()
