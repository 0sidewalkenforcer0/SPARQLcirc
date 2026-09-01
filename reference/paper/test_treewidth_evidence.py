"""Independent tests for auditable CNF-primal treewidth evidence."""

from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path
from unittest import mock


HERE = Path(__file__).resolve().parent
REFERENCE = HERE.parent
for location in (str(HERE), str(REFERENCE)):
    if location not in sys.path:
        sys.path.insert(0, location)

import controlled_mechanisms as controlled
import export_cnf
import treewidth_evidence as evidence


def _edge_cnf(nvars, edges):
    return {
        "nvars": nvars,
        "nclauses": len(edges),
        "clauses": [[left, right] for left, right in edges],
    }


def _path(nvars):
    return _edge_cnf(nvars, [(vertex, vertex + 1)
                             for vertex in range(1, nvars)])


def _cycle(nvars):
    return _edge_cnf(
        nvars,
        [(vertex, vertex + 1) for vertex in range(1, nvars)] + [(nvars, 1)],
    )


def _clique(nvars):
    return _edge_cnf(
        nvars,
        [(left, right) for left in range(1, nvars + 1)
         for right in range(left + 1, nvars + 1)],
    )


class TreewidthEvidenceTests(unittest.TestCase):
    def test_primal_graph_uses_all_declared_nodes_and_clause_cliques(self):
        graph = evidence.primal_graph(5, [[1, -2, 4], [-2, 3], [], [5]])
        self.assertEqual(set(graph), {1, 2, 3, 4, 5})
        self.assertEqual(graph[1], frozenset({2, 4}))
        self.assertEqual(graph[2], frozenset({1, 3, 4}))
        self.assertEqual(graph[3], frozenset({2}))
        self.assertEqual(graph[4], frozenset({1, 2}))
        self.assertEqual(graph[5], frozenset())

    def test_cnf_validation_rejects_bool_zero_range_and_repeated_variable(self):
        invalid = [
            (True, []),
            (0, []),
            (2, 17),
            (2, [17]),
            (2, [[True]]),
            (2, [[0]]),
            (2, [[3]]),
            (2, [[-3]]),
            (2, [[1, 1]]),
            (2, [[1, -1]]),
        ]
        for nvars, clauses in invalid:
            with self.subTest(nvars=nvars, clauses=clauses):
                with self.assertRaises(evidence.EvidenceError):
                    evidence.validate_cnf(nvars, clauses)

        # Repeated clauses are valid DIMACS and induce the same simple graph.
        nvars, clauses = evidence.validate_cnf(2, [[1, 2], [1, 2]])
        self.assertEqual(nvars, 2)
        self.assertEqual(len(clauses), 2)

    def test_export_validation_rejects_missing_or_inconsistent_counts(self):
        with self.assertRaisesRegex(evidence.EvidenceError, "nvars and clauses"):
            evidence.analyze_export({"nvars": 2})
        with self.assertRaisesRegex(evidence.EvidenceError, "nclauses"):
            evidence.analyze_export({
                "nvars": 2, "nclauses": 2, "clauses": [[1, 2]],
            })
        with self.assertRaises(evidence.EvidenceError):
            evidence.analyze_export({
                "nvars": 2, "nclauses": False, "clauses": [],
            })

    def test_known_clique_path_and_cycle_widths_are_exact(self):
        cases = [
            ("clique", _clique(6), 5),
            ("path", _path(7), 1),
            ("cycle", _cycle(7), 2),
            ("singleton", {"nvars": 1, "nclauses": 0, "clauses": []}, 0),
        ]
        for name, encoded, expected in cases:
            with self.subTest(name=name):
                result = evidence.analyze_export(encoded)
                self.assertEqual((result["lower"], result["upper"]),
                                 (expected, expected))
                self.assertEqual(
                    evidence.verify_evidence(encoded, result),
                    (expected, expected),
                )

    def test_formal_layered_grid_has_deterministic_certified_bounds(self):
        expected = {
            ("bounded", 4): (3, 3),
            ("bounded", 8): (3, 3),
            ("bounded", 16): (3, 3),
            ("growing", 2): (3, 3),
            ("growing", 3): (4, 7),
            ("growing", 4): (5, 7),
        }
        for (family, size), bounds in expected.items():
            with self.subTest(family=family, size=size):
                instance = controlled.treewidth_instance(
                    family, size, controlled.FORMAL_SEED
                )
                root = next(iter(instance["roots"].values()))
                encoded = export_cnf.export(
                    instance["circ"], root, instance["weights"]
                )
                first = evidence.analyze_export(encoded)
                second = evidence.analyze_export(encoded)
                self.assertEqual((first["lower"], first["upper"]), bounds)
                self.assertEqual(first, second)
                self.assertEqual(evidence.verify_evidence(encoded, first), bounds)

    def test_formal_task_grid_rejects_certificate_interval_drift(self):
        args = controlled.parser().parse_args([])
        args.experiments = ("treewidth",)
        args.bounded_depths = controlled.FORMAL_BOUNDED_DEPTHS
        args.growing_widths = controlled.FORMAL_GROWING_WIDTHS
        args.formal_run = True
        args.d4v2_path = "/frozen/d4v2"
        args._d4_snapshot = None
        tasks = controlled.build_tasks(args)
        self.assertEqual(len(tasks), 12)
        observed = {}
        for task in tasks:
            instance = task["instance"]
            observed[(task["family"], task["size"])] = (
                instance["treewidth_lower_bound"],
                instance["treewidth_upper_bound"],
            )
        self.assertEqual(observed, controlled.FORMAL_TREEWIDTH_INTERVALS)

        with mock.patch.dict(
            controlled.FORMAL_TREEWIDTH_INTERVALS,
            {("bounded", 4): (2, 2)},
        ):
            with self.assertRaisesRegex(RuntimeError, "certificate interval changed"):
                controlled.build_tasks(args)

    def test_evidence_exposes_auditable_schema_counts_and_hashes(self):
        encoded = _cycle(5)
        result = evidence.analyze_export(encoded)
        self.assertEqual(result["schema"], evidence.SCHEMA)
        self.assertEqual(result["graph_definition"], evidence.GRAPH_DEFINITION)
        self.assertEqual(result["nodes"], 5)
        self.assertEqual(result["edges"], 5)
        self.assertEqual(result["clauses"], 5)
        for field in (
            "cnf_sha256", "graph_sha256", "lower_certificate_sha256",
            "upper_certificate_sha256",
        ):
            self.assertRegex(result[field], r"^[0-9a-f]{64}$")
        self.assertEqual(
            result["lower_certificate_sha256"],
            evidence.canonical_json_sha256(result["lower_certificate"]),
        )
        self.assertEqual(
            result["upper_certificate_sha256"],
            evidence.canonical_json_sha256(result["upper_certificate"]),
        )

    def test_evidence_and_certificates_survive_canonical_json_round_trip(self):
        encoded = _cycle(8)
        original = evidence.analyze_export(encoded)
        decoded = json.loads(evidence.canonical_json_bytes(original).decode("utf-8"))
        self.assertEqual(decoded, original)
        self.assertEqual(evidence.verify_evidence(encoded, decoded), (2, 2))

    def test_canonical_hash_is_key_order_independent_and_rejects_nan(self):
        self.assertEqual(
            evidence.canonical_json_sha256({"b": 2, "a": [1]}),
            evidence.canonical_json_sha256({"a": [1], "b": 2}),
        )
        with self.assertRaises(evidence.EvidenceError):
            evidence.canonical_json_sha256({"bad": float("nan")})

    def test_upper_certificate_tampering_is_rejected_even_with_new_hash(self):
        encoded = _path(6)
        result = evidence.analyze_export(encoded)
        certificate = copy.deepcopy(result["upper_certificate"])
        certificate["steps"][0]["vertex"] = 2
        certificate["ordering"][0] = 2
        digest = evidence.canonical_json_sha256(certificate)
        with self.assertRaisesRegex(evidence.EvidenceError, "min-fill choice"):
            evidence.verify_upper_certificate(
                encoded["nvars"], encoded["clauses"], certificate, digest
            )

        certificate = copy.deepcopy(result["upper_certificate"])
        certificate["steps"][0]["fill_edges"] = [[2, 3]]
        digest = evidence.canonical_json_sha256(certificate)
        with self.assertRaisesRegex(evidence.EvidenceError, "fill edges"):
            evidence.verify_upper_certificate(
                encoded["nvars"], encoded["clauses"], certificate, digest
            )

    def test_lower_certificate_tampering_is_rejected_even_with_new_hash(self):
        encoded = _cycle(6)
        result = evidence.analyze_export(encoded)
        certificate = copy.deepcopy(result["lower_certificate"])
        certificate["steps"][0]["target"] = 6
        digest = evidence.canonical_json_sha256(certificate)
        with self.assertRaisesRegex(evidence.EvidenceError, "contraction target"):
            evidence.verify_lower_certificate(
                encoded["nvars"], encoded["clauses"], certificate, digest
            )

        certificate = copy.deepcopy(result["lower_certificate"])
        certificate["steps"][0]["degree"] += 1
        digest = evidence.canonical_json_sha256(certificate)
        with self.assertRaisesRegex(evidence.EvidenceError, "min-width choice"):
            evidence.verify_lower_certificate(
                encoded["nvars"], encoded["clauses"], certificate, digest
            )

    def test_hash_tampering_and_unknown_fields_are_rejected(self):
        encoded = _clique(4)
        result = evidence.analyze_export(encoded)
        with self.assertRaisesRegex(evidence.EvidenceError, "does not match"):
            evidence.verify_upper_certificate(
                encoded["nvars"], encoded["clauses"],
                result["upper_certificate"], "0" * 64,
            )

        tampered = copy.deepcopy(result)
        tampered["unexpected"] = 1
        with self.assertRaisesRegex(evidence.EvidenceError, "keys differ"):
            evidence.verify_evidence(encoded, tampered)

    def test_certificates_are_bound_to_the_original_primal_graph(self):
        source = _path(5)
        different = _cycle(5)
        result = evidence.analyze_export(source)
        with self.assertRaisesRegex(evidence.EvidenceError, "different primal graph"):
            evidence.verify_upper_certificate(
                different["nvars"], different["clauses"],
                result["upper_certificate"],
            )
        with self.assertRaisesRegex(evidence.EvidenceError, "different primal graph"):
            evidence.verify_lower_certificate(
                different["nvars"], different["clauses"],
                result["lower_certificate"],
            )

    def test_complete_evidence_rejects_bound_count_and_certificate_changes(self):
        encoded = _cycle(5)
        result = evidence.analyze_export(encoded)
        mutations = []
        changed = copy.deepcopy(result)
        changed["edges"] += 1
        mutations.append(changed)
        changed = copy.deepcopy(result)
        changed["lower"] -= 1
        mutations.append(changed)
        changed = copy.deepcopy(result)
        changed["lower_certificate"]["bound"] -= 1
        mutations.append(changed)
        for index, changed in enumerate(mutations):
            with self.subTest(index=index):
                with self.assertRaises(evidence.EvidenceError):
                    evidence.verify_evidence(encoded, changed)


if __name__ == "__main__":
    unittest.main(verbosity=2)
