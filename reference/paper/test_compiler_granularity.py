from __future__ import annotations

import csv
import hashlib
import importlib.util
from pathlib import Path
import tempfile
import time
import unittest

import compiler_granularity as cg


try:
    HAS_CUDD = importlib.util.find_spec("dd.cudd") is not None
except ModuleNotFoundError:
    HAS_CUDD = False


class CompilerGranularityTests(unittest.TestCase):
    def test_synthetic_families_have_controlled_support_and_order(self):
        sharing = cg.synthetic_instance("sharing", size=4, root_count=3)
        disjoint = cg.synthetic_instance("no-sharing", size=4, root_count=3)
        self.assertEqual(len(sharing.roots), 3)
        self.assertEqual(len(sharing.order), 7)       # three private + four common
        self.assertEqual(len(disjoint.order), 12)    # four variables per root
        self.assertEqual(len(set(sharing.order)), len(sharing.order))
        self.assertEqual(len(set(disjoint.order)), len(disjoint.order))
        # Each local support is a subsequence of the one frozen global order.
        for instance in (sharing, disjoint):
            for key, root in instance.roots.items():
                support = set(cg.compiler.deterministic_order(instance.circ, {key: root}))
                local = tuple(variable for variable in instance.order if variable in support)
                self.assertEqual(set(local), support)

    def test_fixed_weights_and_checksums_are_reproducible(self):
        instance = cg.synthetic_instance("sharing", size=3, root_count=2)
        first, first_sha = cg.fixed_weights(instance.order, 7)
        second, second_sha = cg.fixed_weights(reversed(instance.order), 7)
        self.assertEqual(first, second)
        self.assertEqual(first_sha, second_sha)
        self.assertTrue(all(0.05 <= value <= 0.95 for value in first.values()))
        vector = {"b": 0.2, "a": 0.1}
        self.assertEqual(
            cg.probability_checksums(vector),
            cg.probability_checksums(dict(reversed(list(vector.items())))),
        )

    def test_oracle_exercises_all_physical_modes_and_sequential_peak_semantics(self):
        instance = cg.synthetic_instance("sharing", size=5, root_count=4)
        weights, _ = cg.fixed_weights(instance.order, 11)
        results = {
            mode: cg.execute_attempt(instance, weights, mode, "fixed", backend="oracle")
            for mode in cg.MODES
        }
        checksums = {result["probability_checksum_12dp"] for result in results.values()}
        self.assertEqual(len(checksums), 1)
        self.assertGreater(results["shared"]["sharing_savings_nodes"], 0)
        retained = results["per-root-retained"]
        sequential = results["per-root-sequential"]
        self.assertEqual(retained["manager_count"], len(instance.roots))
        self.assertEqual(retained["concurrent_manager_count"], len(instance.roots))
        self.assertEqual(sequential["manager_count"], len(instance.roots))
        self.assertEqual(sequential["concurrent_manager_count"], 1)
        self.assertEqual(
            sequential["manager_memory_semantics"],
            "max-of-one-live-sequential-manager",
        )
        self.assertEqual(sequential["sharing_savings_nodes"], 0)
        control = cg.synthetic_instance("no-sharing", size=5, root_count=4)
        control_weights, _ = cg.fixed_weights(control.order, 11)
        control_shared = cg.execute_attempt(
            control, control_weights, "shared", "fixed", backend="oracle")
        self.assertEqual(control_shared["sharing_savings_nodes"], 0)

    @unittest.skipUnless(HAS_CUDD, "native CUDD wrapper unavailable")
    def test_cudd_shared_retained_and_sequential_probability_parity(self):
        instance = cg.synthetic_instance("sharing", size=5, root_count=3)
        weights, _ = cg.fixed_weights(instance.order, 13)
        results = {
            mode: cg.run_killable(
                instance, weights, mode, "fixed", timeout=10, backend="cudd")
            for mode in cg.MODES
        }
        self.assertTrue(all(result["status"] == "ok" for result in results.values()))
        self.assertEqual(
            len({result["probability_checksum_12dp"] for result in results.values()}), 1)
        self.assertGreater(results["shared"]["sharing_savings_nodes"], 0)
        self.assertEqual(results["per-root-sequential"]["concurrent_manager_count"], 1)
        dynamic = cg.run_killable(
            instance, weights, "shared", "dynamic", timeout=10, backend="cudd")
        self.assertEqual(dynamic["status"], "ok")
        self.assertEqual(
            dynamic["probability_checksum_12dp"],
            results["shared"]["probability_checksum_12dp"],
        )

    def test_killable_worker_retains_timeout(self):
        instance = cg.synthetic_instance("sharing", size=2, root_count=2)
        weights, _ = cg.fixed_weights(instance.order, 17)
        original = cg.execute_attempt

        def slow(*_args, **_kwargs):
            time.sleep(2)
            return {}

        # Fork is required for this test's local monkeypatch to reach the child.
        if "fork" not in cg.multiprocessing.get_all_start_methods():
            self.skipTest("fork multiprocessing unavailable")
        cg.execute_attempt = slow
        try:
            result = cg.run_killable(
                instance, weights, "shared", "fixed", timeout=0.1,
                backend="oracle", context=cg.multiprocessing.get_context("fork"),
            )
        finally:
            cg.execute_attempt = original
        self.assertEqual(result["status"], "timeout")
        self.assertLessEqual(result["attempt_wall_ms"], 100.0)

    def test_append_only_checkpoint_and_parity(self):
        instance = cg.synthetic_instance("no-sharing", size=2, root_count=2)
        weights, weights_sha = cg.fixed_weights(instance.order, 19)

        class Args:
            seed = 19
            warmups = 1
            runs = 5
            timeout = 120.0

        result = cg.execute_attempt(instance, weights, "shared", "fixed", backend="oracle")
        result["status"] = "ok"
        row = cg._blank_row(instance, "shared", "fixed", "measured", 0, Args, weights_sha)
        row.update(result)
        row["parity"] = "baseline"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "checkpoint.csv"
            cg.append_checkpoint(path, row)
            loaded = cg.load_checkpoint(path)
            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded[0]["schema"], cg.SCHEMA)
            self.assertEqual(loaded[0]["probability_checksum"], result["probability_checksum"])
            with path.open(newline="", encoding="utf-8") as handle:
                self.assertEqual(next(csv.reader(handle)), cg.FIELDS)
            with path.open("ab") as handle:
                handle.write(b"torn,partial")
            self.assertEqual(len(cg.load_checkpoint(path)), 1)
            self.assertTrue(path.read_bytes().endswith(b"\n"))
        baseline = {
            "probability_checksum_12dp": str(result["probability_checksum_12dp"]),
            "probability_sum": str(result["probability_sum"]),
        }
        self.assertEqual(cg._parity(result, baseline), "ok")
        wrong = dict(result, probability_checksum_12dp="0" * 64)
        self.assertEqual(cg._parity(wrong, baseline), "mismatch")

    def test_canonical_cache_loading_and_root_limit(self):
        statements = [
            "<urn:g:a> <http://www.w3.org/1999/02/22-rdf-syntax-ns#type> <urn:circuit:Plus> .",
            "<urn:token:x> <urn:circuit:feeds> <urn:g:a> .",
            '<urn:g:a> <urn:circuit:answer> "x" .',
            "<urn:g:a> <urn:circuit:binding> <urn:binding:x> .",
            '<urn:binding:x> <urn:circuit:var> "x" .',
            "<urn:binding:x> <urn:circuit:val> <urn:value:x> .",
        ]
        with tempfile.TemporaryDirectory() as directory:
            descriptor = cg.circuit_cache.store(
                directory,
                statements,
                {"query_sha256": hashlib.sha256(b"query").hexdigest(),
                 "commit": "test-commit"},
            )
            instance = cg.cache_instance(Path(descriptor["circuit_path"]), root_limit=1)
            with self.assertRaises(ValueError):
                cg.cache_instance(
                    Path(descriptor["circuit_path"]), expected_commit="different-commit")
        self.assertEqual(instance.source, "canonical-cache")
        self.assertEqual(instance.circuit_sha256, descriptor["circuit_sha256"])
        self.assertEqual(len(instance.roots), 1)
        self.assertEqual(instance.order, ("urn:token:x",))


if __name__ == "__main__":
    unittest.main()
