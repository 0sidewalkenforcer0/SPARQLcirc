#!/usr/bin/env python3
"""Regression tests for isolated PQE over persisted provenance artifacts."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest


HERE = Path(__file__).resolve().parent
REFERENCE = HERE.parent
sys.path.insert(0, str(REFERENCE))

import pqe_from_artifact as artifact_pqe


ANSWER_KEY = '[["x",["iri","urn:value"]]]'


def _read_probability(path: Path) -> float:
    rows = path.read_text(encoding="utf-8").splitlines()
    if len(rows) != 1:
        raise AssertionError("expected one probability row")
    return float(json.loads(rows[0])["probability"])


class ArtifactPqeTest(unittest.TestCase):
    def test_npcs_shared_dag_and_circuit_have_equal_wmc(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dag = root / "npcs-hc-dag.json"
            dag.write_text(
                json.dumps({
                    "schema": "npcs-pp-hc-dag-v2",
                    "nodes": [
                        {"id": 0, "op": "leaf", "token": "urn:t:1"},
                        {"id": 1, "op": "or", "children": [0]},
                    ],
                    "roots": [{"answer_key": ANSWER_KEY, "root": 1}],
                }),
                encoding="utf-8",
            )
            circuit = root / "circuit.nt"
            circuit.write_text(
                "<urn:answer> <http://www.w3.org/1999/02/22-rdf-syntax-ns#type> "
                "<urn:circuit:Plus> .\n"
                "<urn:t:1> <urn:circuit:feeds> <urn:answer> .\n"
                '<urn:answer> <urn:circuit:answerRoot> "vars:78" .\n'
                "<urn:answer> <urn:circuit:bind:78> <urn:value> .\n",
                encoding="utf-8",
            )

            npcs_out = root / "npcs-pqe"
            c_out = root / "c-pqe"
            npcs_metrics = artifact_pqe.evaluate(
                "npcs-shared", dag, npcs_out, "oracle", None, 0.5,
                {"method": "N-shared"},
            )
            c_metrics = artifact_pqe.evaluate(
                "circuit", circuit, c_out, "oracle", None, 0.5,
                {"method": "C-factorised"},
            )

            self.assertEqual(npcs_metrics["answer_count"], 1)
            self.assertEqual(c_metrics["answer_count"], 1)
            self.assertEqual(npcs_metrics["source_total"], 3)
            self.assertEqual(c_metrics["source_total"], 3)
            self.assertAlmostEqual(
                _read_probability(npcs_out / "probabilities.jsonl"), 0.5
            )
            self.assertAlmostEqual(
                _read_probability(c_out / "probabilities.jsonl"), 0.5
            )
            self.assertEqual(
                (npcs_out / "variable-order.txt").read_text(encoding="utf-8"),
                "urn:t:1\n",
            )
            self.assertNotIn("digest", json.dumps(npcs_metrics).lower())
            self.assertNotIn("checksum", json.dumps(c_metrics).lower())

    def test_npcs_dag_must_be_topological(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dag = root / "bad.json"
            dag.write_text(json.dumps({
                "schema": "npcs-pp-hc-dag-v2",
                "nodes": [{"id": 0, "op": "not", "child": 0}],
                "roots": [{"answer_key": ANSWER_KEY, "root": 0}],
            }), encoding="utf-8")
            with self.assertRaisesRegex(
                artifact_pqe.ArtifactPqeError, "not topological"
            ):
                artifact_pqe.evaluate(
                    "npcs-shared", dag, root / "out", "oracle", None, 0.5, {}
                )

    def test_output_directory_is_immutable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "dag.json"
            source.write_text("{}", encoding="utf-8")
            output = root / "existing"
            output.mkdir()
            with self.assertRaisesRegex(
                artifact_pqe.ArtifactPqeError, "refusing to reuse"
            ):
                artifact_pqe.evaluate(
                    "npcs-shared", source, output, "oracle", None, 0.5, {}
                )


if __name__ == "__main__":
    unittest.main()
