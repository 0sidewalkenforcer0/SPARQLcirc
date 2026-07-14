from __future__ import annotations

import csv
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import time
import unittest
from unittest import mock

import compiler_granularity as cg


def _checkpoint_args(**overrides):
    values = {
        "batch_id": "b" * 64,
        "frozen_inputs_sha256": "f" * 64,
        "protocol": cg.FORMAL_PROTOCOL,
        "run_config_sha256": "9" * 64,
        "formal_run": False,
        "profile": cg.SYNTHETIC_PROFILE,
        "failure_policy": cg.FAILURE_POLICY,
        "git_commit": "d" * 40,
        "git_dirty": "true",
        "backend_version": "dd=test;cudd=test",
        "cudd_extension_sha256": "c" * 64,
        "cudd_extension_bytes": 123,
        "python_runtime_sha256": "8" * 64,
        "python_runtime_bytes": 456,
        "seed": 19,
        "warmups": 0,
        "runs": 1,
        "timeout": 120.0,
        "include_dynamic": False,
        "continue_after_failure": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _ok_checkpoint_row(instance, mode, args, checksum="a" * 64):
    weights, weights_sha = cg.fixed_weights(instance.order, args.seed)
    row = cg._blank_row(
        instance, mode, "fixed", "measured", 0, args, weights_sha
    )
    roots = len(instance.roots)
    if mode == "shared":
        managers, concurrent = 1, 1
        semantics = "one-shared-manager"
        unique, root_sum = 5, 8
    elif mode == "per-root-retained":
        managers = concurrent = roots
        semantics = "sum-of-simultaneously-retained-independent-managers"
        unique = root_sum = 8
    else:
        managers, concurrent = roots, 1
        semantics = "max-of-one-live-sequential-manager"
        unique = root_sum = 8
    row.update({
        "status": "ok",
        "attempt_wall_ms": 12.0,
        "attempt_worker_ms": 10.0,
        "prepare_ms": 1.0,
        "backend_compile_ms": 2.0,
        "inspect_ms": 1.0,
        "source_to_result_ms": 4.0,
        "timing_unattributed_ms": 3.0,
        "timing_scope": "prepare | backend | inspect | WMC | teardown",
        "compile_ms": 4.0,
        "compile_wall_ms": 4.0,
        "wmc_ms": 1.0,
        "wmc_wall_ms": 2.0,
        "teardown_ms": 1.0,
        "compiled_nodes_unique": unique,
        "compiled_nodes_sum_roots": root_sum,
        "sharing_savings_nodes": root_sum - unique,
        "sharing_ratio": root_sum / unique,
        "manager_count": managers,
        "concurrent_manager_count": concurrent,
        "manager_memory_bytes": 1024,
        "manager_memory_semantics": semantics,
        "memory_aggregation": "explicit-test-aggregation",
        "manager_peak_live_nodes_upper_bound": 16,
        "manager_peak_live_nodes_max": 16,
        "manager_current_nodes": 8,
        "manager_reorderings": 0,
        "manager_reordering_seconds": 0.0,
        "process_max_rss_bytes": 4096,
        "probability_sum": 0.5,
        "probability_checksum": "e" * 64,
        "probability_checksum_12dp": checksum,
        "parity": "baseline",
        "cleanup_ms": 0.1,
        "cleanup_action": "none",
        "process_group_reaped": True,
        "notes": "",
    })
    return row


def _serialized(row):
    return {field: str(row.get(field, "")) for field in cg.FIELDS}


def _hold_invocation_lock(path, ready, release):
    with cg._invocation_lock(Path(path), timeout=2):
        ready.set()
        release.wait(2)


def _hold_invocation_lock_through_replacement(path, ready, release):
    try:
        _hold_invocation_lock(path, ready, release)
    except ValueError as exc:
        if "invocation lock" not in str(exc):
            raise


try:
    HAS_CUDD = importlib.util.find_spec("dd.cudd") is not None
except ModuleNotFoundError:
    HAS_CUDD = False


class CompilerGranularityTests(unittest.TestCase):
    def test_formal_output_destination_and_cross_process_lock(self):
        ignored = cg.HERE.parents[1] / "artifacts" / "compiler-test.csv"
        self.assertEqual(cg._validate_output_destination(ignored, False), ignored.resolve())
        with self.assertRaises(RuntimeError):
            cg._validate_output_destination(cg.HERE / "tracked-result.csv", False)

        if "fork" not in cg.multiprocessing.get_all_start_methods():
            return
        context = cg.multiprocessing.get_context("fork")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "locked.csv"
            ready, release = context.Event(), context.Event()
            process = context.Process(
                target=_hold_invocation_lock,
                args=(str(path), ready, release),
            )
            process.start()
            self.assertTrue(ready.wait(1))
            try:
                with self.assertRaises(TimeoutError):
                    with cg._invocation_lock(path, timeout=0.05):
                        pass
            finally:
                release.set()
                process.join(2)
            self.assertFalse(process.is_alive())

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "replaced-lock.csv"
            ready, release = context.Event(), context.Event()
            process = context.Process(
                target=_hold_invocation_lock_through_replacement,
                args=(str(path), ready, release),
            )
            process.start()
            self.assertTrue(ready.wait(1))
            lock = path.with_name(path.name + ".lock")
            replacement = Path(directory) / "replacement.lock"
            replacement.write_text("attacker", encoding="ascii")
            os.replace(replacement, lock)
            try:
                with self.assertRaises(TimeoutError):
                    with cg._invocation_lock(path, timeout=0.05):
                        pass
            finally:
                release.set()
                process.join(2)
            self.assertFalse(process.is_alive())

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
        for result in [*results.values(), control_shared]:
            phase_sum = sum(result[field] for field in (
                "prepare_ms", "backend_compile_ms", "inspect_ms"
            ))
            self.assertGreaterEqual(result["source_to_result_ms"] + 1e-6, phase_sum)
            self.assertAlmostEqual(
                result["attempt_worker_ms"],
                result["source_to_result_ms"]
                + result["wmc_wall_ms"]
                + result["teardown_ms"]
                + result["timing_unattributed_ms"],
                places=6,
            )
            self.assertEqual(result["compile_wall_ms"], result["source_to_result_ms"])

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
        for result in [*results.values(), dynamic]:
            self.assertAlmostEqual(
                result["attempt_worker_ms"],
                result["source_to_result_ms"]
                + result["wmc_wall_ms"]
                + result["teardown_ms"]
                + result["timing_unattributed_ms"],
                places=6,
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
        self.assertGreaterEqual(result["attempt_wall_ms"], 100.0)
        self.assertLess(result["attempt_wall_ms"], 1000.0)

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
                 "commit": "d" * 40,
                 "batch_id": "b" * 64},
            )
            with mock.patch.object(
                cg.circuit_cache,
                "load_sidecar",
                wraps=cg.circuit_cache.load_sidecar,
            ) as load:
                instance = cg.cache_instance(
                    Path(descriptor["circuit_path"]), root_limit=1
                )
            self.assertEqual(load.call_count, 1)
            self.assertEqual(
                instance.source_sidecar_sha256,
                hashlib.sha256(Path(descriptor["metadata_path"]).read_bytes()).hexdigest(),
            )
            with self.assertRaises(ValueError):
                cg.cache_instance(
                    Path(descriptor["circuit_path"]), expected_commit="e" * 40)
            with self.assertRaises(ValueError):
                cg.cache_instance(
                    Path(descriptor["circuit_path"]),
                    allowed_query_sha256=("f" * 64,),
                )
            os.link(descriptor["circuit_path"], Path(directory) / "payload-hardlink.nt")
            with self.assertRaises(ValueError):
                cg.cache_instance(Path(descriptor["circuit_path"]))
        self.assertEqual(instance.source, "canonical-cache")
        self.assertEqual(instance.circuit_sha256, descriptor["circuit_sha256"])
        self.assertEqual(len(instance.roots), 1)
        self.assertEqual(instance.order, ("urn:token:x",))
        self.assertEqual(instance.source_batch_id, "b" * 64)
        self.assertRegex(instance.source_sidecar_sha256, r"^[0-9a-f]{64}$")
        self.assertGreater(instance.source_sidecar_bytes, 0)
        self.assertRegex(instance.source_observation_sha256, r"^[0-9a-f]{64}$")

    def test_formal_profiles_reject_every_exploratory_override(self):
        args = cg.parser().parse_args(["--frozen-inputs", "/tmp/frozen.json"])
        args.formal_run = True
        cg._validate_formal_configuration(args)
        for field, value in (
            ("warmups", 2),
            ("runs", 4),
            ("seed", 1),
            ("timeout", 119.0),
            ("include_dynamic", False),
            ("allow_unsafe_output", True),
            ("allow_cache_commit_mismatch", True),
            ("continue_after_failure", True),
            ("allow_unfrozen", True),
            ("allow_dirty", True),
            ("sizes", [8, 32]),
            ("roots", 4),
            ("families", "sharing"),
            ("cache", ["/tmp/unexpected.nt"]),
            ("no_synthetic", True),
        ):
            modified = copy.copy(args)
            setattr(modified, field, value)
            with self.assertRaises(ValueError, msg=field):
                cg._validate_formal_configuration(modified)

        real = cg.parser().parse_args([
            "--frozen-inputs", "/tmp/frozen.json",
            "--profile", cg.REAL_CACHE_PROFILE,
            "--no-synthetic", "--cache", "/tmp/cache.nt",
            "--cache-query-sha256", "a" * 64,
        ])
        real.formal_run = True
        cg._validate_formal_configuration(real)
        missing_allowlist = copy.copy(real)
        missing_allowlist.cache_query_sha256 = []
        with self.assertRaises(ValueError):
            cg._validate_formal_configuration(missing_allowlist)

    def test_safe_artifact_snapshots_and_output_aliases(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            artifact = directory / "runtime.so"
            artifact.write_bytes(b"first")
            snapshot = cg._snapshot_file(artifact, "test runtime")
            with self.assertRaises(ValueError):
                cg._reject_output_input_aliases(artifact, [snapshot])
            artifact.write_bytes(b"second")
            with self.assertRaises(RuntimeError):
                cg._verify_snapshot(snapshot)

            target = directory / "target.csv"
            target.write_text("x", encoding="utf-8")
            symlink = directory / "symlink.csv"
            symlink.symlink_to(target)
            with self.assertRaises(ValueError):
                cg._validate_output_destination(symlink, True)
            hardlink = directory / "hardlink.csv"
            os.link(target, hardlink)
            with self.assertRaises(ValueError):
                cg._validate_output_destination(hardlink, True)

    def test_frozen_runtime_artifact_mismatch_is_fatal(self):
        with tempfile.TemporaryDirectory() as directory:
            frozen = Path(directory) / "frozen.json"
            frozen.write_text("{}\n", encoding="utf-8")
            args = SimpleNamespace(
                frozen_inputs=frozen,
                allow_unfrozen=False,
                batch_id=None,
                expected_protocol=cg.FORMAL_PROTOCOL,
                git_commit="d" * 40,
                formal_run=True,
                _actual_tools={
                    cg.PYTHON_TOOL_NAME: {"sha256": "a" * 64, "bytes": 10},
                    cg.CUDD_TOOL_NAME: {"sha256": "b" * 64, "bytes": 20},
                },
            )
            document = {
                "batch_id": "c" * 64,
                "identity": {"protocol": cg.FORMAL_PROTOCOL},
            }
            with (
                mock.patch.object(cg.freeze_inputs, "load_frozen_batch",
                                  return_value=document),
                mock.patch.object(
                    cg.freeze_inputs, "frozen_tool",
                    return_value={"sha256": "0" * 64, "bytes": 10},
                ),
            ):
                with self.assertRaises(RuntimeError):
                    cg._resolve_frozen_identity(args)

    def test_formal_git_audit_rejects_hidden_index_flags(self):
        hidden = SimpleNamespace(stdout=b"s hidden.py\0")
        with mock.patch.object(cg.subprocess, "run", return_value=hidden):
            with self.assertRaises(RuntimeError):
                cg._validate_no_hidden_index_bits()

    def test_checkpoint_rejects_forged_metrics_parity_and_schedule(self):
        instance = cg.synthetic_instance("sharing", size=3, root_count=2)
        args = _checkpoint_args()
        baseline = _serialized(_ok_checkpoint_row(instance, "shared", args))
        cg.validate_checkpoint([baseline], [instance], args)

        forged = dict(baseline, sharing_savings_nodes="999")
        with self.assertRaises(ValueError):
            cg.validate_checkpoint([forged], [instance], args)
        forged = dict(baseline, parity="ok")
        with self.assertRaises(ValueError):
            cg.validate_checkpoint([forged], [instance], args)
        skipped_first_slot = _serialized(
            _ok_checkpoint_row(instance, "per-root-retained", args)
        )
        skipped_first_slot["parity"] = "baseline"
        with self.assertRaises(ValueError):
            cg.validate_checkpoint([skipped_first_slot], [instance], args)

    def test_checkpoint_completion_binds_exact_payload_and_run_config(self):
        instance = cg.synthetic_instance("sharing", size=3, root_count=2)
        args = _checkpoint_args()
        args.run_config = {"profile": args.profile, "test": True}
        rows = []
        for index, mode in enumerate(cg.MODES):
            row = _ok_checkpoint_row(instance, mode, args)
            row["parity"] = "baseline" if index == 0 else "ok"
            rows.append(_serialized(row))
        cg.validate_checkpoint(rows, [instance], args, require_complete=True)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "result.csv"
            for row in rows:
                cg.append_checkpoint(output, row)
            cg.write_completion(output, rows, args)
            cg.validate_completion(output, rows, args, required=True)
            with output.open("ab") as handle:
                handle.write(b"forged\n")
            with self.assertRaises(ValueError):
                cg.validate_completion(output, rows, args, required=True)

    def test_checkpoint_rejects_width_type_torn_completion_and_float_rounding(self):
        instance = cg.synthetic_instance("sharing", size=3, root_count=2)
        args = _checkpoint_args()
        args.run_config = {"profile": args.profile, "test": True}
        row = _serialized(_ok_checkpoint_row(instance, "shared", args))
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "result.csv"
            cg.append_checkpoint(output, row)
            original = output.read_bytes()

            header, body = original.split(b"\r\n", 1)
            output.write_bytes(header + b",extra\r\n" + body.rstrip(b"\r\n") + b",x\r\n")
            with self.assertRaises(ValueError):
                cg.load_checkpoint(output)
            output.write_bytes(original)

            columns = list(csv.reader([header.decode("utf-8")]))[0]
            values = list(csv.reader([body.decode("utf-8").strip()]))[0]
            with output.open("w", encoding="utf-8", newline="") as handle:
                handle.write(
                    ",".join(columns[:-1])
                    + "\r\n"
                    + ",".join(values[:-1])
                    + "\r\n"
                )
            with self.assertRaises(ValueError):
                cg.load_checkpoint(output)
            output.write_bytes(original)

            cg.write_completion(output, [row], args)
            completion = cg._completion_path(output)
            document = json.loads(completion.read_text(encoding="utf-8"))
            document["rows"] = 1.0
            completion.write_bytes(cg._canonical_json_bytes(document))
            with self.assertRaises(ValueError):
                cg.validate_completion(output, [row], args, required=True)

            completion.unlink()
            cg.write_completion(output, [row], args)
            with output.open("ab") as handle:
                handle.write(b"torn")
            before = output.read_bytes()
            with self.assertRaises(ValueError):
                cg.validate_completion(output, [row], args, required=True)
            self.assertEqual(output.read_bytes(), before)

        forged = dict(row, attempt_wall_ms=str((1 << 53) + 1))
        with self.assertRaises(ValueError):
            cg.validate_checkpoint([forged], [instance], args)

    def test_failed_cell_resume_requires_canonical_not_run_suffix(self):
        instance = cg.synthetic_instance("sharing", size=2, root_count=2)
        args = _checkpoint_args(runs=2)
        weights, weights_sha = cg.fixed_weights(instance.order, args.seed)
        failed = cg._blank_row(
            instance, "shared", "fixed", "measured", 0, args, weights_sha
        )
        failed.update({
            "status": "timeout", "attempt_wall_ms": 120000,
            "cleanup_ms": 1, "cleanup_action": "sigterm-process-group",
            "process_group_reaped": True, "parity": "unverified",
            "notes": "deadline",
        })
        not_run = cg._blank_row(
            instance, "shared", "fixed", "measured", 1, args, weights_sha
        )
        not_run.update({
            "status": "not-run", "parity": "unverified", "notes": cg.NOT_RUN_NOTE,
        })
        rows = [_serialized(failed), _serialized(not_run)]
        cg.validate_checkpoint(rows, [instance], args)
        retried = _serialized(_ok_checkpoint_row(instance, "shared", args))
        retried["rep"] = "1"
        retried["parity"] = "baseline"
        with self.assertRaises(ValueError):
            cg.validate_checkpoint([rows[0], retried], [instance], args)


if __name__ == "__main__":
    unittest.main()
