from __future__ import annotations

import csv
from decimal import Decimal
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

import controlled_mechanisms as cm
import freeze_inputs as freeze
import d4_pipeline


try:
    HAS_CUDD = importlib.util.find_spec("dd.cudd") is not None
except ModuleNotFoundError:
    HAS_CUDD = False
HAS_D4V2 = cm.DEFAULT_D4V2.is_file()


def _frozen_document(commit, protocol=cm.FORMAL_PROTOCOL, d4_sha256=None,
                     extra_tools=(), formal=False):
    manifests = []
    for index, kind in enumerate(("workload", "path"), 1):
        manifests.append({
            "kind": kind,
            "schema": list(freeze.MANIFESTS[kind]["columns"]),
            "bytes": index,
            "sha256": str(index) * 64,
            "rows": 1,
            "queries": [{
                "key": {name: "x" for name in freeze.MANIFESTS[kind]["key"]},
                "query_file": "paper/queries/%s.rq" % kind,
                "query_sha256": str(index + 2) * 64,
            }],
        })
    tools = []
    if d4_sha256 is not None:
        tools.append({"name": "d4v2", "bytes": 123, "sha256": d4_sha256})
    tools.extend(extra_tools)
    data = [{"name": "synthetic-control", "bytes": 1, "sha256": "8" * 64}]
    stores = []
    sentinels = []
    if formal:
        data.extend([
            {"name": "base-data", "bytes": 2, "sha256": "6" * 64},
            {"name": "reified-data", "bytes": 3, "sha256": "7" * 64},
        ])
        stores, _ = freeze.validate_store_specs([(
            "graphdb", "10M", "10.7.6", "read-only",
            "base-data", "reified-data",
            "http://localhost:7200/repositories/base",
            "http://localhost:7200/repositories/reified", "-",
        )])
        sentinels = [
            {
                "engine": "graphdb", "scale": "10M", "role": role,
                "kind": "ask", "query_sha256": "4" * 64,
                "expected_fingerprint": fingerprint,
                "observed_fingerprint": fingerprint,
            }
            for role, fingerprint in (
                ("base", freeze.sentinel_fingerprint("ask", False)),
                ("reified", freeze.sentinel_fingerprint("ask", True)),
            )
        ]
    return freeze.build_batch(
        protocol, {"commit": commit, "clean": True}, manifests,
        data, stores, sentinels, tools,
        batch_profile="formal" if formal else "exploratory")


def _write_document(directory, document, name="frozen.json"):
    path = Path(directory) / name
    path.write_text(json.dumps(document, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _exploratory_command(output, *, size=2, runs=1, timeout=10,
                         formal_strict=False):
    command = [
        sys.executable, str(Path(cm.__file__).resolve()),
        "--output", str(output), "--experiments", "construction",
        "--construction-shapes", "chain", "--construction-sizes", str(size),
        "--warmups", "0", "--runs", str(runs), "--timeout", str(timeout),
        "--batch-id", "1" * 64, "--expected-protocol", cm.FORMAL_PROTOCOL,
        "--allow-unfrozen", "--allow-dirty",
    ]
    if formal_strict:
        command.append("--formal-strict")
    return command


def _spawn_descendant_and_wait(task):
    child = subprocess.Popen([
        sys.executable, "-c", "import time; time.sleep(30)",
    ])
    Path(task["pid_file"]).write_text(str(child.pid), encoding="ascii")
    time.sleep(30)
    return {"status": "ok"}


def _pid_is_live(pid):
    stat_path = Path("/proc") / str(pid) / "stat"
    try:
        fields = stat_path.read_text(encoding="ascii").split()
    except OSError:
        return False
    return len(fields) > 2 and fields[2] != "Z"


def _rewrite_csv(path, mutate):
    with Path(path).open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    mutate(rows)
    with Path(path).open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=cm.FIELDS)
        writer.writeheader()
        writer.writerows(rows)


class ControlledMechanismsTests(unittest.TestCase):
    def test_frozen_manifest_is_canonical_and_binds_protocol_git_and_d4(self):
        commit = "b" * 40
        d4_sha = "c" * 64
        document = _frozen_document(commit, d4_sha256=d4_sha)
        with tempfile.TemporaryDirectory() as directory:
            path = _write_document(directory, document)
            result = cm.validate_frozen_inputs(
                path, batch_id=document["batch_id"], expected_protocol=cm.FORMAL_PROTOCOL,
                current_commit=commit, require_d4=True, d4_sha256=d4_sha,
                d4_tool_name="d4v2", required_data=("synthetic-control",),
                require_formal=False,
            )
            self.assertEqual(result["batch_id"], document["batch_id"])
            self.assertEqual(result["frozen_inputs_sha256"],
                             hashlib.sha256(path.read_bytes()).hexdigest())
            with self.assertRaisesRegex(ValueError, "refuses an exploratory"):
                cm.validate_frozen_inputs(
                    path, batch_id=None, expected_protocol=cm.FORMAL_PROTOCOL,
                    current_commit=commit, require_d4=True, d4_sha256=d4_sha,
                    d4_tool_name="d4v2")
            with self.assertRaisesRegex(ValueError, "requested data"):
                cm.validate_frozen_inputs(
                    path, batch_id=None, expected_protocol=cm.FORMAL_PROTOCOL,
                    current_commit=commit, require_d4=False, d4_sha256="",
                    d4_tool_name="d4v2", required_data=("missing",),
                    require_formal=False)

            malformed = [
                dict(document, schema="wrong"),
                dict(document, batch_id="0" * 64),
            ]
            for index, bad in enumerate(malformed):
                with self.subTest(index=index):
                    bad_path = _write_document(directory, bad, "bad%d.json" % index)
                    with self.assertRaises(ValueError):
                        cm.validate_frozen_inputs(
                            bad_path, batch_id=None, expected_protocol=cm.FORMAL_PROTOCOL,
                            current_commit=commit, require_d4=True, d4_sha256=d4_sha,
                            d4_tool_name="d4v2", require_formal=False)

            with self.assertRaisesRegex(ValueError, "protocol"):
                cm.validate_frozen_inputs(
                    path, batch_id=None, expected_protocol="other",
                    current_commit=commit, require_d4=True, d4_sha256=d4_sha,
                    d4_tool_name="d4v2", require_formal=False)
            with self.assertRaisesRegex(ValueError, "Git"):
                cm.validate_frozen_inputs(
                    path, batch_id=None, expected_protocol=cm.FORMAL_PROTOCOL,
                    current_commit="d" * 40, require_d4=True, d4_sha256=d4_sha,
                    d4_tool_name="d4v2", require_formal=False)
            dirty_identity = json.loads(json.dumps(document))
            dirty_identity["identity"]["git"]["clean"] = False
            dirty_identity["batch_id"] = freeze.canonical_batch_id(
                dirty_identity["identity"])
            dirty_path = _write_document(directory, dirty_identity, "dirty-freeze.json")
            with self.assertRaisesRegex(ValueError, "Git"):
                cm.validate_frozen_inputs(
                    dirty_path, batch_id=None, expected_protocol=cm.FORMAL_PROTOCOL,
                    current_commit=commit, require_d4=True, d4_sha256=d4_sha,
                    d4_tool_name="d4v2", require_formal=False)
            with self.assertRaisesRegex(ValueError, "d4"):
                cm.validate_frozen_inputs(
                    path, batch_id=None, expected_protocol=cm.FORMAL_PROTOCOL,
                    current_commit=commit, require_d4=True, d4_sha256="e" * 64,
                    d4_tool_name="d4v2", require_formal=False)

        cm.validate_git_identity(commit, "false", False)
        cm.validate_git_identity(commit, "true", True)
        with self.assertRaises(RuntimeError):
            cm.validate_git_identity(commit, "true", False)

    def test_output_must_be_external_or_git_ignored(self):
        with self.assertRaises(ValueError):
            cm.validate_output_destination(cm.HERE / "not-ignored-controlled.csv")
        accepted = cm.validate_output_destination(
            cm.HERE / "artifacts" / "controlled.csv")
        self.assertEqual(accepted.name, "controlled.csv")

    def test_cli_requires_frozen_inputs_unless_explicitly_exploratory(self):
        with tempfile.TemporaryDirectory() as directory:
            command = _exploratory_command(Path(directory) / "formal.csv")
            command.remove("--allow-unfrozen")
            result = subprocess.run(command, capture_output=True, text=True, timeout=30)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("formal runs require --frozen-inputs", result.stderr)

    def test_formal_protocol_schedule_seed_timeout_and_group_grids_are_fixed(self):
        args = cm.parser().parse_args([])
        args.experiments = ("construction",)
        args.construction_shapes = cm.FORMAL_CONSTRUCTION_SHAPES
        args.construction_sizes = cm.FORMAL_CONSTRUCTION_SIZES
        args.numerical_depths = cm.FORMAL_NUMERICAL_DEPTHS
        args.bounded_depths = cm.FORMAL_BOUNDED_DEPTHS
        args.growing_widths = cm.FORMAL_GROWING_WIDTHS
        args.required_data = ()
        args.formal_run = True
        cm.validate_formal_configuration(args)

        mutations = {
            "expected_protocol": "r9.2-frozen-identity-v6",
            "warmups": 0,
            "runs": 1,
            "timeout": 119.0,
            "seed": cm.FORMAL_SEED + 1,
            "construction_shapes": ("chain",),
            "construction_sizes": (2,),
            "numerical_depths": (8,),
            "bounded_depths": (4,),
            "growing_widths": (2,),
            "d4_tool_name": "renamed-d4",
            "required_data": ("arbitrary",),
        }
        for field, value in mutations.items():
            with self.subTest(field=field):
                changed = mock.Mock(**vars(args))
                for name, original in vars(args).items():
                    setattr(changed, name, original)
                setattr(changed, field, value)
                with self.assertRaisesRegex(ValueError, field):
                    cm.validate_formal_configuration(changed)

        exploratory = mock.Mock(**vars(args))
        for name, original in vars(args).items():
            setattr(exploratory, name, original)
        exploratory.formal_run = False
        exploratory.warmups = 0
        exploratory.runs = 1
        exploratory.construction_shapes = ("chain",)
        cm.validate_formal_configuration(exploratory)

    def test_formal_git_audit_rejects_assume_unchanged_and_skip_worktree(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)

            def git(*arguments):
                return subprocess.run(
                    ["git", "-C", str(repo), *arguments], check=True,
                    capture_output=True, text=True, timeout=10,
                )

            git("init", "-q")
            git("config", "user.name", "Controlled Test")
            git("config", "user.email", "controlled@example.invalid")
            tracked = repo / "tracked source.py"
            tracked.write_text("original\n", encoding="utf-8")
            git("add", "--", tracked.name)
            git("commit", "-qm", "fixture")
            cm.validate_no_hidden_index_bits(repo)

            git("update-index", "--assume-unchanged", "--", tracked.name)
            tracked.write_text("hidden assume-unchanged edit\n", encoding="utf-8")
            self.assertEqual(git("status", "--porcelain=v1").stdout, "")
            with self.assertRaisesRegex(RuntimeError, "assume-unchanged"):
                cm.validate_no_hidden_index_bits(repo)

            tracked.write_text("original\n", encoding="utf-8")
            git("update-index", "--no-assume-unchanged", "--", tracked.name)
            cm.validate_no_hidden_index_bits(repo)
            git("update-index", "--skip-worktree", "--", tracked.name)
            tracked.write_text("hidden skip-worktree edit\n", encoding="utf-8")
            self.assertEqual(git("status", "--porcelain=v1").stdout, "")
            with self.assertRaisesRegex(RuntimeError, "skip-worktree"):
                cm.validate_no_hidden_index_bits(repo)

    def test_frozen_runtime_tools_bind_cudd_extension_and_python_binary(self):
        commit = "b" * 40
        actual = {
            cm.CUDD_TOOL_NAME: {"bytes": 11, "sha256": "a" * 64},
            cm.PYTHON_TOOL_NAME: {"bytes": 12, "sha256": "b" * 64},
        }
        document = _frozen_document(
            commit,
            extra_tools=[
                {"name": name, "bytes": value["bytes"],
                 "sha256": value["sha256"]}
                for name, value in actual.items()
            ],
        )
        with tempfile.TemporaryDirectory() as directory:
            path = _write_document(directory, document)
            result = cm.validate_frozen_inputs(
                path, batch_id=document["batch_id"],
                expected_protocol=cm.FORMAL_PROTOCOL,
                current_commit=commit, require_d4=False, d4_sha256="",
                d4_tool_name=cm.D4_TOOL_NAME, require_formal=False,
                actual_tools=actual,
            )
            self.assertEqual(result["batch_id"], document["batch_id"])
            forged = dict(actual)
            forged[cm.CUDD_TOOL_NAME] = {"bytes": 11, "sha256": "c" * 64}
            with self.assertRaisesRegex(ValueError, "dd-cudd-extension"):
                cm.validate_frozen_inputs(
                    path, batch_id=document["batch_id"],
                    expected_protocol=cm.FORMAL_PROTOCOL,
                    current_commit=commit, require_d4=False, d4_sha256="",
                    d4_tool_name=cm.D4_TOOL_NAME, require_formal=False,
                    actual_tools=forged,
                )

    def test_formal_freeze_requires_fixed_runtime_and_d4_logical_tools(self):
        commit = "c" * 40
        actual = {
            cm.CUDD_TOOL_NAME: {"bytes": 11, "sha256": "a" * 64},
            cm.PYTHON_TOOL_NAME: {"bytes": 12, "sha256": "b" * 64},
            cm.D4_TOOL_NAME: {"bytes": 13, "sha256": "d" * 64},
        }
        document = _frozen_document(
            commit,
            d4_sha256=actual[cm.D4_TOOL_NAME]["sha256"],
            extra_tools=[
                {"name": name, "bytes": value["bytes"],
                 "sha256": value["sha256"]}
                for name, value in actual.items()
                if name != cm.D4_TOOL_NAME
            ],
            formal=True,
        )
        # The legacy helper assigns the frozen d4 record a fixed fixture size.
        actual[cm.D4_TOOL_NAME]["bytes"] = 123
        with tempfile.TemporaryDirectory() as directory:
            path = _write_document(directory, document)
            result = cm.validate_frozen_inputs(
                path, batch_id=document["batch_id"],
                expected_protocol=cm.FORMAL_PROTOCOL,
                current_commit=commit, require_d4=True,
                d4_sha256=actual[cm.D4_TOOL_NAME]["sha256"],
                d4_tool_name=cm.D4_TOOL_NAME, require_formal=True,
                actual_tools=actual,
            )
            self.assertEqual(result["batch_id"], document["batch_id"])
            with self.assertRaisesRegex(ValueError, "fixed as d4v2"):
                cm.validate_frozen_inputs(
                    path, batch_id=document["batch_id"],
                    expected_protocol=cm.FORMAL_PROTOCOL,
                    current_commit=commit, require_d4=True,
                    d4_sha256=actual[cm.D4_TOOL_NAME]["sha256"],
                    d4_tool_name="renamed", require_formal=True,
                    actual_tools=actual,
                )

    def test_all_construction_shapes_flat_factored_semantic_parity(self):
        for shape in cm.CONSTRUCTION_SHAPES:
            results = {}
            for mode in ("flat", "factored"):
                results[mode] = cm.construction_attempt({
                    "shape": shape, "size": 2, "construction_mode": mode,
                    "seed": 7,
                })
                self.assertEqual(results[mode]["status"], "ok")
                self.assertGreater(results[mode]["gates"], 0)
                self.assertGreater(results[mode]["circuit_bytes"], 0)
                self.assertNotIn("tw≈", results[mode]["notes"])
            self.assertEqual(results["flat"]["semantic_checksum"],
                             results["factored"]["semantic_checksum"], shape)

    def test_numerical_profiles_match_decimal_oracle_in_both_modes(self):
        for profile in ("uniform", "nonuniform", "extreme"):
            results = []
            for mode in ("shared", "per-root"):
                result = cm.numerical_attempt({
                    "depth": 6, "profile": profile, "seed": 11,
                    "compile_mode": mode, "backend": "oracle",
                })
                self.assertEqual(result["status"], "ok", (profile, mode, result))
                self.assertEqual(result["numerical_classification"], "within-tolerance")
                results.append(result)
            self.assertEqual(results[0]["probability_checksum"],
                             results[1]["probability_checksum"])

    def test_extreme_default_depth_is_representable_and_underflow_is_fatal(self):
        _circ, roots, weights, _decimal_weights = cm.numerical_instance(
            512, "extreme", 11)
        self.assertTrue(all(exact != 0 for _gate, exact in roots.values()))
        interior = [value for value in weights.values() if 0.0 < value < 1.0]
        self.assertTrue(interior)
        self.assertIn(float(Decimal.from_float(cm.math.nextafter(1.0, 0.0))), interior)

        underflow = cm.numerical_error_report({"x": 0.0}, {"x": Decimal("1e-400")})
        self.assertFalse(underflow["ok"])
        self.assertEqual(underflow["underflow_count"], 1)
        self.assertEqual(underflow["numerical_classification"],
                         "underflow-nonzero-exact-to-zero")
        relative_only_failure = cm.numerical_error_report(
            {"x": 2e-20}, {"x": Decimal("1e-20")})
        self.assertFalse(relative_only_failure["ok"])
        self.assertEqual(relative_only_failure["numerical_classification"],
                         "tolerance-exceeded")

        class ZeroBatch:
            metrics = {
                "compile_ms": 0.0, "wmc_ms": 0.0,
                "compiled_nodes_unique": 0, "compiled_nodes_sum_roots": 0,
                "manager_memory_bytes": 0,
                "manager_peak_live_nodes_upper_bound": 0,
            }

            @staticmethod
            def wmc_many(_weights):
                return {"depth:2": 0.0, "depth:4": 0.0}

        with mock.patch.object(cm.compiler, "compile_many", return_value=ZeroBatch()):
            mismatch = cm.numerical_attempt({
                "depth": 4, "profile": "uniform", "seed": 11,
                "compile_mode": "shared", "backend": "oracle",
            })
        self.assertEqual(mismatch["status"], "numerical-mismatch")
        self.assertIn(mismatch["status"], cm.FATAL_STATUSES)

        if HAS_CUDD:
            for profile in ("uniform", "nonuniform", "extreme"):
                result = cm.numerical_attempt({
                    "depth": 512, "profile": profile, "seed": 11,
                    "compile_mode": "shared", "backend": "cudd",
                })
                self.assertEqual(result["status"], "ok", result)
                self.assertEqual(result["underflow_count"], 0)

    @unittest.skipUnless(HAS_CUDD, "native CUDD unavailable")
    def test_native_cudd_numerical_and_treewidth_control(self):
        _version, artifacts = cm._discover_cudd_runtime()
        self.assertRegex(artifacts[cm.CUDD_TOOL_NAME]["sha256"], r"^[0-9a-f]{64}$")
        self.assertRegex(artifacts[cm.PYTHON_TOOL_NAME]["sha256"], r"^[0-9a-f]{64}$")
        numerical = cm.run_killable({
            "kind": "numerical", "depth": 16, "profile": "nonuniform",
            "seed": 13, "compile_mode": "shared",
        }, timeout=10)
        self.assertEqual(numerical["status"], "ok")
        instance = cm.treewidth_instance("bounded", 3, 13)
        self.assertEqual(instance["tw_evidence"], cm.treewidth_evidence.SCHEMA)
        bounds = cm.treewidth_evidence.verify_evidence(
            instance["encoded"], instance["treewidth_document"]
        )
        self.assertEqual(
            bounds,
            (instance["treewidth_lower_bound"],
             instance["treewidth_upper_bound"]),
        )
        self.assertNotIn("tw≈", instance["control_note"])
        self.assertIn("certified treewidth interval", instance["control_note"])
        cudd = cm.run_killable({"kind": "treewidth-cudd", "instance": instance},
                               timeout=10)
        self.assertEqual(cudd["status"], "ok")
        self.assertGreaterEqual(cudd["source_to_result_ms"],
                                cudd["backend_compile_ms"])
        self.assertIn("source-to-compiled-result", cudd["timing_scope"])

    @unittest.skipUnless(HAS_D4V2, "pinned d4v2 unavailable")
    def test_pinned_d4v2_ignores_environment_override_and_records_wall_boundary(self):
        instance = cm.treewidth_instance("bounded", 2, 17)
        with mock.patch.dict(os.environ, {"D4V2_DDNNF_CMD": "/bin/false"}):
            result = cm.run_killable({
                "kind": "treewidth-d4v2", "instance": instance,
                "d4v2_bin": str(cm.DEFAULT_D4V2), "timeout": 20,
            }, timeout=20)
        self.assertEqual(result["status"], "ok", result)
        self.assertNotIn("compile_ms", result)
        self.assertGreater(result["backend_compile_ms"], 0)
        self.assertGreaterEqual(result["source_to_result_ms"],
                                result["backend_compile_ms"])
        self.assertEqual(result["d4_argv_sha256"], cm.D4_ARGV_SHA256)
        self.assertTrue(result["process_group_reaped"])

    @unittest.skipUnless(
        HAS_D4V2 and HAS_CUDD,
        "patched d4v2 and native CUDD required",
    )
    def test_patched_d4v2_depth16_matches_production_cudd(self):
        instance = cm.treewidth_instance("bounded", 16, cm.FORMAL_SEED)
        snapshot = cm._snapshot_file(cm.DEFAULT_D4V2, "patched d4v2")
        d4 = cm.run_killable({
            "kind": "treewidth-d4v2",
            "instance": instance,
            "d4v2_bin": str(cm.DEFAULT_D4V2),
            "d4v2_signature": snapshot["signature"],
            "timeout": 30,
        }, timeout=30)
        cudd = cm.run_killable({
            "kind": "treewidth-cudd", "instance": instance,
        }, timeout=30)
        self.assertEqual(d4["status"], "ok", d4)
        self.assertEqual(cudd["status"], "ok", cudd)
        self.assertAlmostEqual(
            d4["probability_sum"], cudd["probability_sum"], places=12
        )

    def test_hermetic_fake_d4_success_path_uses_fixed_argv_and_wmc(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake = root / "fake-d4v2"
            calls = root / "calls"
            fake.write_text(
                "#!/usr/bin/env python3\n"
                "import pathlib, sys\n"
                "args = sys.argv[1:]\n"
                "cnf = pathlib.Path(args[args.index('-i') + 1])\n"
                "assert 'p cnf ' in cnf.read_text(encoding='utf-8')\n"
                "assert '-m' not in args and '--dump-ddnnf' not in args, args\n"
                "out = pathlib.Path(args[args.index('--dump-file') + 1])\n"
                "out.write_text('o 1 0\\nt 2 0\\n1 2 1 0\\n', encoding='utf-8')\n"
                "pathlib.Path(%r).write_text('called\\n', encoding='ascii')\n"
                % str(calls),
                encoding="utf-8",
            )
            fake.chmod(0o755)
            snapshot = cm._snapshot_file(fake, "fake d4v2")
            instance = cm.treewidth_instance("bounded", 2, 17)
            encoded = cm.export_cnf.export(
                instance["circ"], next(iter(instance["roots"].values())),
                instance["weights"],
            )
            first_node = next(
                node for node, variable in encoded["var_of"].items()
                if variable == 1
            )
            op, token = instance["circ"][first_node]
            self.assertEqual(op, "leaf")
            with mock.patch.dict(os.environ, {"D4V2_DDNNF_CMD": "/bin/false"}):
                result = cm.run_killable({
                    "kind": "treewidth-d4v2", "instance": instance,
                    "d4v2_bin": str(fake), "d4v2_signature": snapshot["signature"],
                    "timeout": 10,
                }, timeout=10)
            self.assertEqual(result["status"], "ok", result)
            self.assertAlmostEqual(
                result["probability_sum"], instance["weights"][token], places=15
            )
            self.assertEqual(
                (result["treewidth_lower_bound"],
                 result["treewidth_upper_bound"]),
                (instance["treewidth_lower_bound"],
                 instance["treewidth_upper_bound"]),
            )
            self.assertEqual(
                result["treewidth_cnf_sha256"],
                instance["treewidth_cnf_sha256"],
            )
            self.assertEqual(result["d4_argv_sha256"], cm.D4_ARGV_SHA256)
            self.assertTrue(result["process_group_reaped"])
            self.assertEqual(calls.read_text(encoding="ascii"), "called\n")

    @unittest.skipUnless(HAS_CUDD, "native CUDD required for CLI treewidth cell")
    def test_cli_returns_nonzero_for_backend_error(self):
        with tempfile.TemporaryDirectory() as directory:
            fake = Path(directory) / "failing-d4v2"
            fake.write_text("#!/bin/sh\nexit 2\n", encoding="utf-8")
            fake.chmod(0o755)
            digest = hashlib.sha256(fake.read_bytes()).hexdigest()
            output = Path(directory) / "error.csv"
            command = [
                sys.executable, str(Path(cm.__file__).resolve()),
                "--output", str(output), "--experiments", "treewidth",
                "--bounded-depths", "2", "--growing-widths", "2",
                "--warmups", "0", "--runs", "1", "--timeout", "5",
                "--batch-id", "2" * 64, "--expected-protocol", cm.FORMAL_PROTOCOL,
                "--allow-unfrozen", "--allow-dirty", "--d4v2-bin", str(fake),
                "--expected-d4-sha256", digest,
            ]
            result = subprocess.run(command, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 1, result.stderr)
            summary = json.loads(result.stdout)
            self.assertEqual(summary["status"], "failed")
            self.assertGreater(summary["fatal_failures_total"], 0)

    def test_worker_exit_without_payload_is_fatal(self):
        if "fork" not in cm.multiprocessing.get_all_start_methods():
            self.skipTest("fork unavailable")
        original = cm._dispatch

        def crash(_task):
            os._exit(7)

        cm._dispatch = crash
        try:
            result = cm.run_killable(
                {"kind": "construction"}, timeout=2,
                context=cm.multiprocessing.get_context("fork"))
        finally:
            cm._dispatch = original
        self.assertEqual(result["status"], "worker-exit")
        self.assertIn(result["status"], cm.FATAL_STATUSES)
        self.assertTrue(result["process_group_reaped"])

    def test_signal_kill_is_not_called_oom_without_memory_evidence(self):
        self.assertEqual(cm._subprocess_failure_status(-9, ""), "killed-signal")
        self.assertEqual(
            cm._subprocess_failure_status(-9, "fatal: std::bad_alloc"), "oom"
        )
        self.assertIn("killed-signal", cm.FATAL_STATUSES)

    def test_unreaped_process_group_is_fatal_cleanup_error(self):
        if "fork" not in cm.multiprocessing.get_all_start_methods():
            self.skipTest("fork unavailable")
        cleanup = {
            "cleanup_ms": 1.0,
            "cleanup_action": "sigkill-process-group",
            "process_group_reaped": False,
        }
        with mock.patch.object(cm, "_cleanup_attempt", return_value=cleanup):
            result = cm.run_killable(
                {
                    "kind": "construction", "shape": "chain", "size": 2,
                    "construction_mode": "flat", "seed": 1,
                },
                timeout=2,
                context=cm.multiprocessing.get_context("fork"),
            )
        self.assertEqual(result["status"], "cleanup-error")
        self.assertFalse(result["process_group_reaped"])
        self.assertIn(result["status"], cm.FATAL_STATUSES)

    def test_timeout_reports_real_cleanup_wall_and_reaps_descendants(self):
        if "fork" not in cm.multiprocessing.get_all_start_methods():
            self.skipTest("fork unavailable")
        original = cm._dispatch
        with tempfile.TemporaryDirectory() as directory:
            pid_file = Path(directory) / "pid"
            cm._dispatch = _spawn_descendant_and_wait
            try:
                result = cm.run_killable(
                    {"kind": "construction", "pid_file": str(pid_file)}, timeout=0.1,
                    context=cm.multiprocessing.get_context("fork"))
            finally:
                cm._dispatch = original
            self.assertEqual(result["status"], "timeout")
            self.assertGreaterEqual(result["attempt_wall_ms"], 100.0)
            self.assertGreaterEqual(result["cleanup_ms"], 0.0)
            self.assertIn("process-group", result["cleanup_action"])
            self.assertTrue(result["process_group_reaped"], result)
            if pid_file.exists():
                pid = int(pid_file.read_text())
                deadline = time.time() + 1
                while _pid_is_live(pid) and time.time() < deadline:
                    time.sleep(0.01)
                self.assertFalse(_pid_is_live(pid))

    def test_checkpoint_repairs_torn_tail_and_rejects_duplicate_terminal_rows(self):
        row = {field: "" for field in cm.FIELDS}
        row.update({"schema": cm.SCHEMA, "status": "timeout", "experiment": "x"})
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "attempts.csv"
            cm.append_checkpoint(path, row)
            with path.open("ab") as handle:
                handle.write(b"torn,row")
            rows = cm.load_checkpoint(path)
            self.assertEqual(len(rows), 1)
            with path.open(newline="", encoding="utf-8") as handle:
                self.assertEqual(next(csv.reader(handle)), cm.FIELDS)
            cm.append_checkpoint(path, row)
            with self.assertRaisesRegex(ValueError, "duplicate terminal"):
                cm.load_checkpoint(path)

    def test_checkpoint_rejects_noncanonical_width_and_physical_csv(self):
        row = {field: "" for field in cm.FIELDS}
        row.update({"schema": cm.SCHEMA, "status": "timeout", "experiment": "x"})
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            canonical_path = root / "canonical.csv"
            cm.append_checkpoint(canonical_path, row)
            canonical = canonical_path.read_bytes()
            header, data, empty = canonical.split(b"\r\n")
            self.assertEqual(empty, b"")

            buffer = io.StringIO(newline="")
            writer = csv.DictWriter(
                buffer, fieldnames=cm.FIELDS, quoting=csv.QUOTE_ALL,
                lineterminator="\r\n",
            )
            writer.writeheader()
            writer.writerow(row)
            mutations = {
                "extra-column": header + b"\r\n" + data + b",UNDECLARED\r\n",
                "missing-column": header + b"\r\n" + data.rsplit(b",", 1)[0] + b"\r\n",
                "lf-only": canonical.replace(b"\r\n", b"\n"),
                "quote-all": buffer.getvalue().encode("utf-8"),
            }
            for name, payload in mutations.items():
                with self.subTest(name=name):
                    path = root / (name + ".csv")
                    path.write_bytes(payload)
                    with self.assertRaises(ValueError):
                        cm.load_checkpoint(path)

    def test_output_lock_checkpoint_and_completion_reject_aliases(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target"
            target.write_text("x", encoding="utf-8")

            symlink_output = root / "symlink.csv"
            symlink_output.symlink_to(target)
            with self.assertRaisesRegex(ValueError, "symlink|hardlink"):
                cm.validate_output_destination(symlink_output)

            hard_output = root / "hard.csv"
            os.link(target, hard_output)
            with self.assertRaisesRegex(ValueError, "symlink|hardlink"):
                cm.validate_output_destination(hard_output)

            checkpoint_link = root / "checkpoint.csv"
            checkpoint_link.symlink_to(target)
            with self.assertRaises(ValueError):
                cm.load_checkpoint(checkpoint_link)
            checkpoint_link.unlink()
            os.link(target, checkpoint_link)
            with self.assertRaises(ValueError):
                cm.load_checkpoint(checkpoint_link)
            checkpoint_link.unlink()

            completion_output = root / "completion.csv"
            completion_link = cm._completion_path(completion_output)
            completion_link.symlink_to(target)
            with self.assertRaises(ValueError):
                cm._load_completion(completion_output)
            completion_link.unlink()
            os.link(target, completion_link)
            with self.assertRaises(ValueError):
                cm._load_completion(completion_output)
            completion_link.unlink()

            output = root / "lock-target.csv"
            lock = output.with_name(output.name + ".lock")
            lock.symlink_to(target)
            with self.assertRaises(ValueError):
                with cm.invocation_lock(output):
                    pass
            lock.unlink()
            os.link(target, lock)
            with self.assertRaises(ValueError):
                with cm.invocation_lock(output):
                    pass

            protected = root / "frozen-tool.bin"
            protected.write_bytes(b"protected input")
            snapshot = cm._snapshot_file(protected, "protected fixture")
            with self.assertRaisesRegex(ValueError, "must not alias"):
                cm._reject_output_input_aliases(protected, [snapshot])

    def test_checkpoint_resume_rejects_incomplete_ok_and_forged_completion(self):
        tampered_fields = (
            "semantic_checksum", "attempt_wall_ms", "gates", "cleanup_ms",
            "process_group_reaped", "parity",
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for field in tampered_fields:
                with self.subTest(field=field):
                    output = root / (field + ".csv")
                    command = _exploratory_command(output)
                    first = subprocess.run(
                        command, capture_output=True, text=True, timeout=30
                    )
                    self.assertEqual(first.returncode, 0, first.stderr)
                    cm._completion_path(output).unlink()
                    _rewrite_csv(
                        output,
                        lambda rows, field=field: rows[0].__setitem__(field, ""),
                    )
                    resumed = subprocess.run(
                        command, capture_output=True, text=True, timeout=30
                    )
                    self.assertEqual(resumed.returncode, 2, resumed.stderr)
                    self.assertIn("controlled_mechanisms: ERROR", resumed.stderr)

            output = root / "completion.csv"
            command = _exploratory_command(output)
            first = subprocess.run(command, capture_output=True, text=True, timeout=30)
            self.assertEqual(first.returncode, 0, first.stderr)
            sidecar = cm._completion_path(output)
            document = json.loads(sidecar.read_text(encoding="utf-8"))
            document["rows"] += 1
            sidecar.write_text(
                json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )
            resumed = subprocess.run(command, capture_output=True, text=True, timeout=30)
            self.assertEqual(resumed.returncode, 2, resumed.stderr)
            self.assertIn("completion sidecar", resumed.stderr)

    def test_completed_checkpoint_torn_append_is_rejected_without_repair(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "completed.csv"
            command = _exploratory_command(output)
            first = subprocess.run(command, capture_output=True, text=True, timeout=30)
            self.assertEqual(first.returncode, 0, first.stderr)
            before = output.read_bytes()
            with output.open("ab") as handle:
                handle.write(b"UNDECLARED-TORN-TAIL")
            tampered = output.read_bytes()
            resumed = subprocess.run(command, capture_output=True, text=True, timeout=30)
            self.assertEqual(resumed.returncode, 2, resumed.stderr)
            self.assertIn("controlled_mechanisms: ERROR", resumed.stderr)
            self.assertEqual(output.read_bytes(), tampered)
            self.assertNotEqual(tampered, before)

    def test_completion_sidecar_comparison_is_type_sensitive(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            mutations = {
                "rows-float": lambda document: document.__setitem__(
                    "rows", float(document["rows"])
                ),
                "bytes-float": lambda document: document["checkpoint"].__setitem__(
                    "csv_bytes", float(document["checkpoint"]["csv_bytes"])
                ),
                "bool-as-int": lambda document: document["run_config"]["freeze"].__setitem__(
                    "allow_unfrozen", 1
                ),
            }
            for name, mutate in mutations.items():
                with self.subTest(name=name):
                    output = root / (name + ".csv")
                    command = _exploratory_command(output)
                    first = subprocess.run(
                        command, capture_output=True, text=True, timeout=30
                    )
                    self.assertEqual(first.returncode, 0, first.stderr)
                    sidecar = cm._completion_path(output)
                    document = json.loads(sidecar.read_text(encoding="utf-8"))
                    mutate(document)
                    sidecar.write_text(
                        json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n",
                        encoding="utf-8",
                    )
                    resumed = subprocess.run(
                        command, capture_output=True, text=True, timeout=30
                    )
                    self.assertEqual(resumed.returncode, 2, resumed.stderr)
                    self.assertIn("completion", resumed.stderr)

    def test_checkpoint_numeric_metrics_require_canonical_exact_types(self):
        mutations = {
            "negative-zero": ("attempt_wall_ms", "-0"),
            "integer-exponent": ("gates", "1e0"),
            "binary64-rounded-integer": ("tokens", "9007199254740992.1"),
            "rss-exponent": ("process_self_peak_rss_bytes", "1e308"),
            "numeric-whitespace": ("build_ms", " 1.0"),
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, (field, value) in mutations.items():
                with self.subTest(name=name):
                    output = root / (name + ".csv")
                    command = _exploratory_command(output)
                    first = subprocess.run(
                        command, capture_output=True, text=True, timeout=30
                    )
                    self.assertEqual(first.returncode, 0, first.stderr)
                    cm._completion_path(output).unlink()
                    _rewrite_csv(
                        output,
                        lambda rows, field=field, value=value: rows[0].__setitem__(
                            field, value
                        ),
                    )
                    resumed = subprocess.run(
                        command, capture_output=True, text=True, timeout=30
                    )
                    self.assertEqual(resumed.returncode, 2, resumed.stderr)
                    self.assertIn("checkpoint", resumed.stderr)

    def test_checkpoint_rejects_isolated_not_run_and_noncanonical_failure_tail(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            isolated = root / "isolated.csv"
            command = _exploratory_command(isolated)
            result = subprocess.run(command, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            cm._completion_path(isolated).unlink()
            _rewrite_csv(
                isolated,
                lambda rows: rows[0].update({
                    "status": "not-run", "parity": "unverified",
                    "notes": cm.NOT_RUN_NOTE,
                }),
            )
            resumed = subprocess.run(command, capture_output=True, text=True, timeout=30)
            self.assertEqual(resumed.returncode, 2, resumed.stderr)
            self.assertIn("isolated not-run", resumed.stderr)

            boundary = root / "boundary.csv"
            boundary_command = _exploratory_command(
                boundary, runs=2, timeout=0.001
            )
            initial = subprocess.run(
                boundary_command, capture_output=True, text=True, timeout=30
            )
            self.assertEqual(initial.returncode, 0, initial.stderr)
            cm._completion_path(boundary).unlink()

            def forge_tail(rows):
                not_run = next(row for row in rows if row["status"] == "not-run")
                not_run["notes"] = "forged not-run"

            _rewrite_csv(boundary, forge_tail)
            resumed = subprocess.run(
                boundary_command, capture_output=True, text=True, timeout=30
            )
            self.assertEqual(resumed.returncode, 2, resumed.stderr)
            self.assertIn("exact not-run", resumed.stderr)

    def test_cross_process_invocation_lock_prevents_duplicate_or_lost_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "concurrent.csv"
            command = _exploratory_command(output)
            first = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     text=True)
            second = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                      text=True)
            first_out, first_err = first.communicate(timeout=30)
            second_out, second_err = second.communicate(timeout=30)
            self.assertEqual(first.returncode, 0, first_err)
            self.assertEqual(second.returncode, 0, second_err)
            rows = cm.load_checkpoint(output)
            self.assertEqual(len(rows), 2)  # flat + factored, exactly once each
            self.assertEqual(len({_key(row) for row in rows}), 2)
            summaries = [json.loads(first_out), json.loads(second_out)]
            self.assertEqual(sorted(item["attempted"] for item in summaries), [0, 2])

    def test_construction_only_records_and_completes_python_runtime_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "construction.csv"
            result = subprocess.run(
                _exploratory_command(output), capture_output=True, text=True,
                timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            rows = cm.load_checkpoint(output)
            self.assertTrue(rows)
            self.assertEqual({row["cudd_extension_sha256"] for row in rows}, {""})
            python_hashes = {row["python_runtime_sha256"] for row in rows}
            self.assertEqual(len(python_hashes), 1)
            self.assertRegex(next(iter(python_hashes)), r"^[0-9a-f]{64}$")
            sidecar = json.loads(
                cm._completion_path(output).read_text(encoding="utf-8")
            )
            self.assertEqual(
                sidecar["run_config"]["backend"]["python_runtime"]["sha256"],
                next(iter(python_hashes)),
            )

    def test_legacy_d4_pipeline_is_explicitly_exploratory_and_ci_runs_contract(self):
        self.assertEqual(
            d4_pipeline.EVIDENCE_CLASSIFICATION,
            "exploratory-unfrozen-environment-command-template",
        )
        workflow = (cm.REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("python reference/verify_level1_harness.py", workflow)

    def test_mixed_run_config_is_rejected_and_failed_cells_are_not_retried(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "config.csv"
            first = subprocess.run(_exploratory_command(output, size=2),
                                   capture_output=True, text=True, timeout=30)
            self.assertEqual(first.returncode, 0, first.stderr)
            mixed = subprocess.run(_exploratory_command(output, size=3),
                                   capture_output=True, text=True, timeout=30)
            self.assertNotEqual(mixed.returncode, 0)
            self.assertIn("checkpoint mixes", mixed.stderr)

            boundary = Path(directory) / "boundary.csv"
            command = _exploratory_command(boundary, runs=2, timeout=0.001)
            initial = subprocess.run(command, capture_output=True, text=True, timeout=30)
            self.assertEqual(initial.returncode, 0, initial.stderr)
            before = boundary.read_bytes()
            resumed = subprocess.run(command, capture_output=True, text=True, timeout=30)
            self.assertEqual(resumed.returncode, 0, resumed.stderr)
            self.assertEqual(boundary.read_bytes(), before)
            rows = cm.load_checkpoint(boundary)
            by_method = {}
            for row in rows:
                by_method.setdefault(row["method"], []).append(row["status"])
            self.assertEqual(set(by_method), {"flat", "factored"})
            for statuses in by_method.values():
                self.assertEqual(statuses, ["timeout", "not-run"])

            strict_output = Path(directory) / "strict.csv"
            strict = subprocess.run(
                _exploratory_command(strict_output, runs=1, timeout=0.001,
                                     formal_strict=True),
                capture_output=True, text=True, timeout=30)
            self.assertEqual(strict.returncode, 1, strict.stderr)
            summary = json.loads(strict.stdout)
            self.assertTrue(summary["strict_mode"])
            self.assertGreater(summary["resource_boundaries_total"], 0)


def _key(row):
    return (row["run_config_sha256"], row["instance_id"], row["method"],
            row["phase"], row["rep"])


if __name__ == "__main__":
    unittest.main()
