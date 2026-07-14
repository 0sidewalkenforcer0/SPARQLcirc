"""Completely offline regressions for the R9.2 timing/parity harness."""

import csv
import contextlib
import hashlib
import io
import json
import multiprocessing
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import types
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
REF = os.path.dirname(HERE)
sys.path.insert(0, REF)
sys.path.insert(0, HERE)
import circuit_io
import circuit_cache
import paper_construction_matrix as pcm
import summarize_brnc
import verify_brnc_parity as parity


class _StreamingResponse:
    """urlopen-compatible response that always makes socket-like progress."""

    def __init__(self, payload, interval, chunk_count, counter):
        self.payload = payload
        self.interval = interval
        self.chunk_count = chunk_count
        self.counter = counter
        self.index = 0

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def read(self, _size=-1):
        if self.index >= self.chunk_count:
            return b""
        if self.interval:
            time.sleep(self.interval)
        self.index += 1
        with self.counter.get_lock():
            self.counter.value += 1
        return self.payload


class fake_urlopen:
    """Fork-inherited, network-free streaming HTTP simulation."""

    def __init__(self, payload=b"x\n<urn:test:a>\n", interval=0, chunk_count=1):
        self.payload = payload
        self.interval = interval
        self.chunk_count = chunk_count
        self.counter = multiprocessing.get_context("fork").Value("i", 0)

    def __call__(self, _request, timeout=None):
        return _StreamingResponse(
            self.payload, self.interval, self.chunk_count, self.counter
        )


class _BytesResponse:
    def __init__(self, payload):
        self.payload = payload
        self.sent = False

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def read(self, _size=-1):
        if self.sent:
            return b""
        self.sent = True
        return self.payload


class queued_urlopen:
    """Capture ordered query/update requests and serve deterministic responses."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    def __call__(self, request, timeout=None):
        self.requests.append(request)
        if not self.responses:
            raise AssertionError("unexpected HTTP request")
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return _BytesResponse(response)


class sequenced_urlopen:
    """Fork-visible finite response sequence for warmup/measured assertions."""

    def __init__(self, *payloads):
        self.payloads = payloads
        self.counter = multiprocessing.get_context("fork").Value("i", 0)

    def __call__(self, _request, timeout=None):
        with self.counter.get_lock():
            index = self.counter.value
            self.counter.value += 1
        if index >= len(self.payloads):
            raise AssertionError("unexpected HTTP request")
        payload = self.payloads[index]
        if isinstance(payload, BaseException):
            raise payload
        return _BytesResponse(payload)


RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
PRIVATE_ROWS = (
    b"<urn:sc:row:1> <urn:sc:message> <urn:sc:msg:1> .\n"
    b"<urn:sc:row:1> <urn:sc:gate> <urn:g:p> .\n"
)
PARTIAL_CIRCUIT = (
    b"<urn:g:p> <http://www.w3.org/1999/02/22-rdf-syntax-ns#type> "
    b"<urn:circuit:Plus> .\n"
)
ANSWER_CIRCUIT = (
    b"<urn:g:p> <urn:circuit:feeds> <urn:g:a> .\n"
    b"<urn:g:a> <http://www.w3.org/1999/02/22-rdf-syntax-ns#type> "
    b"<urn:circuit:Plus> .\n"
    b"<urn:g:a> <urn:circuit:answer> \"A\" .\n"
)


def _locked_increment(path):
    with pcm.invocation_file_lock(path, timeout=5):
        try:
            value = int(Path(path).read_text(encoding="ascii"))
        except FileNotFoundError:
            value = 0
        time.sleep(0.05)
        Path(path).write_text(str(value + 1), encoding="ascii")


def _descendant_group_worker(pid_path):
    pcm._new_process_group()
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"]
    )
    Path(pid_path).write_text(str(child.pid), encoding="ascii")
    child.returncode = 0  # parent test owns process-group cleanup; suppress destructor warning


def _parity_merge_worker(path, engine):
    row = {name: "" for name in parity.COLS}
    row.update(
        {
            "protocol": pcm.PROTOCOL,
            "commit": pcm.COMMIT,
            "batch_id": "a" * 64,
            "engine": engine,
            "engine_version": "test",
            "scale": "10M",
            "class": "L",
            "template": "L1",
            "instance": "00",
            "query_sha256": hashlib.sha256(engine.encode()).hexdigest(),
            "base_endpoint_sha256": "1" * 64,
            "reified_endpoint_sha256": "2" * 64,
            "update_endpoint_sha256": "3" * 64,
            "base_data_identity_sha256": "4" * 64,
            "reified_data_identity_sha256": "5" * 64,
            "update_for": "reified",
            "access_mode": "writable",
            "base_data_name": "base-data",
            "reified_data_name": "reified-data",
            "update_canary_sha256": "9" * 64,
            "store_instance_sha256": "6" * 64,
            "store_discriminator_sha256": "7" * 64,
            "tool_sha256": "8" * 64,
            "java_runtime_sha256": "a" * 64,
            "run_identity_sha256": hashlib.sha256((engine + ":run").encode()).hexdigest(),
            "br_multiset_equal": True,
            "nc_keys_equal": True,
        }
    )
    parity.merge_parity_rows(path, [row])


def _minimal_frozen_manifest(kind):
    spec = pcm.freeze_inputs.MANIFESTS[kind]
    key = {
        name: {
            "suite": "suite",
            "scale": "10M",
            "class": "L",
            "template": "L1",
            "instance": "00",
            "dataset": "data",
            "form": "bound",
            "bound": "true",
        }[name]
        for name in spec["key"]
    }
    return {
        "kind": kind,
        "schema": list(spec["columns"]),
        "bytes": 1,
        "sha256": ("1" if kind == "path" else "2") * 64,
        "rows": 1,
        "queries": [
            {
                "key": key,
                "query_file": f"paper/queries/{kind}.rq",
                "query_sha256": "3" * 64,
            }
        ],
    }


def _formal_frozen_document():
    freeze = pcm.freeze_inputs
    base_url = "http://localhost:7200/base"
    reified_url = "http://localhost:7200/reified"
    update_url = reified_url + "/statements"
    stores, _urls = freeze.validate_store_specs(
        [
            (
                "graphdb",
                "10M",
                "10.7.6",
                "writable",
                "base-data",
                "reified-data",
                base_url,
                reified_url,
                update_url,
            )
        ]
    )
    stores[0]["update_canary"] = {
        "protocol": freeze.CANARY_PROTOCOL,
        "insert_visible": True,
        "delete_invisible": True,
    }
    sentinels = [
        {
            "engine": "graphdb",
            "scale": "10M",
            "role": role,
            "kind": "ask",
            "query_sha256": "4" * 64,
            "expected_fingerprint": fingerprint,
            "observed_fingerprint": fingerprint,
        }
        for role, fingerprint in (
            ("base", freeze.sentinel_fingerprint("ask", False)),
            ("reified", freeze.sentinel_fingerprint("ask", True)),
        )
    ]
    return freeze.build_batch(
        pcm.PROTOCOL,
        {"commit": pcm.COMMIT, "clean": True},
        [_minimal_frozen_manifest("path"), _minimal_frozen_manifest("workload")],
        [
            {"name": "base-data", "bytes": 1, "sha256": "7" * 64},
            {"name": "reified-data", "bytes": 2, "sha256": "8" * 64},
        ],
        stores,
        sentinels,
        [
            {
                "name": pcm.FROZEN_TOOL_NAME,
                "bytes": 3,
                "sha256": "9" * 64,
            },
            {
                "name": pcm.FROZEN_JAVA_RUNTIME_NAME,
                "bytes": 4,
                "sha256": "a" * 64,
            },
        ],
        batch_profile="formal",
    )


@unittest.skipUnless(
    "fork" in multiprocessing.get_all_start_methods(),
    "offline process-injection tests require fork",
)
class HardDeadlineTests(unittest.TestCase):
    def test_whole_cell_times_out_while_response_keeps_streaming(self):
        # Every chunk arrives far sooner than the socket timeout.  A per-socket
        # timeout therefore never fires; only the parent cell deadline can stop it.
        stream = fake_urlopen(interval=0.015, chunk_count=500)
        with mock.patch.object(pcm._NO_PROXY_OPENER, "open", stream):
            started = time.monotonic()
            result = pcm.time_method(
                "B",
                "SELECT ?x WHERE { ?x ?p ?o }",
                "http://offline.invalid/sparql",
                "http://offline.invalid/sparql",
                timeout=0.25,
                warmups=0,
                runs=1,
            )
            elapsed = time.monotonic() - started
        self.assertGreater(stream.counter.value, 2)
        self.assertEqual(result["status"], "timeout")
        self.assertIsNotNone(result["rewrite_ms"])
        self.assertLess(elapsed, 0.8)
        self.assertIn("whole cell", result["note"])

    def test_c_steps_share_one_deadline(self):
        # One finite response fits; two only fit if the timeout is incorrectly
        # reset for step 2.  The C cell must time out under its shared budget.
        def two_step_plan(_query, timeout=None):
            return ["CONSTRUCT WHERE { ?s ?p ?o }", "CONSTRUCT WHERE { ?s ?p ?o }"]

        payload = b"<urn:s> <urn:p> <urn:o> .\n"
        stream = fake_urlopen(payload=payload, interval=0.035, chunk_count=5)
        with mock.patch.object(pcm._NO_PROXY_OPENER, "open", stream), mock.patch.object(
            pcm, "c_construct_plan", two_step_plan
        ):
            result = pcm.time_method(
                "C",
                "SELECT ?s WHERE { ?s ?p ?o }",
                "http://offline.invalid/sparql",
                "http://offline.invalid/sparql",
                timeout=0.27,
                warmups=0,
                runs=1,
            )
        self.assertEqual(result["status"], "timeout")

    def test_warmup_and_timed_run_share_one_deadline(self):
        # Each finite response fits a 170ms budget in isolation.  The warm-up
        # consumes most of that budget, so the measured run must not get a reset.
        stream = fake_urlopen(interval=0.02, chunk_count=5)
        with mock.patch.object(pcm._NO_PROXY_OPENER, "open", stream):
            result = pcm.time_method(
                "B",
                "SELECT ?x WHERE { ?x ?p ?o }",
                "http://offline.invalid/sparql",
                "http://offline.invalid/sparql",
                timeout=0.17,
                warmups=1,
                runs=1,
            )
        self.assertEqual(result["status"], "timeout")
        self.assertGreater(stream.counter.value, 5)  # timed request was entered

    def test_rewrite_time_is_recorded(self):
        def delayed_rewrite(query):
            time.sleep(0.04)
            return query

        stream = fake_urlopen()
        with mock.patch.object(pcm._NO_PROXY_OPENER, "open", stream), mock.patch.object(
            pcm, "q_reify", delayed_rewrite
        ):
            result = pcm.time_method(
                "R",
                "SELECT ?x WHERE { ?x ?p ?o }",
                "http://offline.invalid/sparql",
                "http://offline.invalid/sparql",
                timeout=2.0,
                warmups=0,
                runs=1,
            )
        self.assertEqual(result["status"], "ok")
        self.assertIsNotNone(result["rewrite_ms"])
        self.assertGreaterEqual(result["rewrite_ms"], 30.0)
        self.assertEqual(len(result["samples"]), 1)

    def test_c_parse_covers_decode_dedup_and_binding_without_network_overlap(self):
        original_merge = pcm._merge_circuit_chunks
        original_parse = pcm.parse_circuit

        def delayed_merge(chunks, unique):
            time.sleep(0.025)
            return original_merge(chunks, unique)

        def delayed_parse(lines, include_keys=False):
            time.sleep(0.025)
            return original_parse(lines, include_keys=include_keys)

        def one_step(_query, timeout=None):
            return ["CONSTRUCT WHERE { ?s ?p ?o }"]

        stream = fake_urlopen(payload=b"<urn:s> <urn:p> <urn:o> .\n")
        with mock.patch.object(pcm._NO_PROXY_OPENER, "open", stream), mock.patch.object(
            pcm, "c_construct_plan", one_step
        ), mock.patch.object(pcm, "_merge_circuit_chunks", delayed_merge), mock.patch.object(
            pcm, "parse_circuit", delayed_parse
        ):
            result = pcm.time_method(
                "C",
                "SELECT ?s WHERE { ?s ?p ?o }",
                "http://offline.invalid/sparql",
                "http://offline.invalid/sparql",
                timeout=2.0,
                warmups=0,
                runs=1,
            )
        self.assertEqual(result["status"], "ok")
        self.assertGreaterEqual(result["c_parse"][0], 45.0)
        self.assertGreaterEqual(
            result["construct_total"][0] + 0.001,
            result["samples"][0]
            + result["c_parse"][0]
            + result["c_protocol"][0],
        )
        self.assertAlmostEqual(
            result["construct_total"][0],
            result["samples"][0]
            + result["c_parse"][0]
            + result["c_protocol"][0]
            + result["construct_unattributed"][0],
            places=2,
        )

    @unittest.skipUnless(os.name == "posix", "process-group semantics are POSIX-only")
    def test_reaper_kills_descendants_after_group_leader_exits(self):
        ctx = multiprocessing.get_context("fork")
        with tempfile.TemporaryDirectory() as directory:
            pid_path = os.path.join(directory, "descendant.pid")
            proc = ctx.Process(target=_descendant_group_worker, args=(pid_path,))
            proc.start()
            proc.join(2)
            self.assertFalse(proc.is_alive())
            child_pid = int(Path(pid_path).read_text(encoding="ascii"))
            self.assertTrue(pcm._process_group_alive(proc.pid))
            self.assertTrue(pcm._kill_worker(proc))
            self.assertFalse(pcm._process_group_alive(proc.pid))
            with self.assertRaises(ProcessLookupError):
                os.kill(child_pid, 0)


class FactoredConstructionProtocolTests(unittest.TestCase):
    @staticmethod
    def _plan():
        feedback = (
            "CONSTRUCT { ?row <urn:sc:message> <urn:sc:msg:1> ; "
            "<urn:sc:gate> ?gate . ?gate a <urn:circuit:Plus> } WHERE {}"
        )
        answer = (
            "CONSTRUCT { ?gate <urn:circuit:answer> \"A\" } "
            "WHERE { ?row <urn:sc:message> <urn:sc:msg:1> }"
        )
        return pcm.ConstructionPlan(
            [
                pcm.ConstructionStep(feedback, True, "base"),
                pcm.ConstructionStep(answer, False, "answers"),
            ],
            requested_mode="factored",
            effective_mode="factored",
        )

    def test_plan_parser_recovers_modes_labels_and_feedback(self):
        stderr = """# ---- construction mode: requested=factored, effective=factored ----
# ---- circuit construction plan: 2 CONSTRUCT(s) ----
# --- step 1 ---
PREFIX c: <urn:circuit:>
CONSTRUCT { ?r <urn:sc:message> <urn:sc:msg:1> . }
WHERE { ?s ?p ?o }
# step label: base[0]
# --- step 2 ---
PREFIX c: <urn:circuit:>
CONSTRUCT { ?g c:answer \"A\" . }
WHERE { ?r <urn:sc:message> <urn:sc:msg:1> . }
# step label: answers
# circuit triples: 3
"""
        completed = types.SimpleNamespace(returncode=0, stderr=stderr, stdout="")
        with mock.patch.object(pcm.subprocess, "run", return_value=completed):
            plan = pcm.c_construct_plan("SELECT * WHERE { ?s ?p ?o }")
        self.assertEqual((plan.requested_mode, plan.effective_mode), ("factored", "factored"))
        self.assertEqual([step.label for step in plan.steps], ["base[0]", "answers"])
        self.assertEqual([step.feedback for step in plan.steps], [True, False])
        # Sequence compatibility retained for verify scripts written for list[str].
        self.assertTrue(all(isinstance(query, str) for query in plan))

    def test_multistep_feedback_is_nonempty_private_only_and_cleaned(self):
        http = queued_urlopen(
            PRIVATE_ROWS + PARTIAL_CIRCUIT,
            b"",  # INSERT DATA response
            ANSWER_CIRCUIT,
            b"",  # DELETE DATA cleanup response
        )
        with mock.patch.object(pcm._NO_PROXY_OPENER, "open", http):
            result = pcm._execute_c_once(
                "http://query.invalid",
                "http://update.invalid",
                self._plan(),
                time.monotonic() + 2,
            )

        _network, nbytes, _parse, gates, _edges, answers, _times, _keys, meta = result
        self.assertEqual((gates, answers), (2, 1))
        self.assertGreater(nbytes, 0)
        self.assertEqual(meta["feedback_triples"], 2)
        self.assertGreaterEqual(meta["client_protocol_ms"], 0.0)
        self.assertEqual(meta["network_scope"], "construct+feedback+cleanup")
        self.assertEqual(len(http.requests), 4)
        insert = http.requests[1].data.decode()
        cleanup = http.requests[3].data.decode()
        self.assertTrue(insert.startswith("INSERT DATA"))
        self.assertTrue(cleanup.startswith("DELETE DATA"))
        self.assertIn("<urn:sc:message>", insert)
        self.assertIn("<urn:sc:gate>", insert)
        self.assertNotIn("urn:circuit:Plus", insert)
        self.assertEqual(
            http.requests[1].get_header("Content-type"),
            "application/sparql-update",
        )

    def test_update_chunking_has_exact_line_membership(self):
        triples = {
            '<urn:r:1> <urn:sc:message> <urn:m:1> .',
            '<urn:r:1> <urn:sc:value> "x" .',
            '<urn:r:10> <urn:sc:message> <urn:m:10> .',
            '<urn:r:10> <urn:sc:value> "x" .',
            '<urn:r:2> <urn:sc:message> <urn:m:2> .',
            '<urn:r:2> <urn:sc:value> "x" .',
        }
        bodies = list(pcm._update_bodies("INSERT", triples, chunk_size=2))
        self.assertEqual(len(bodies), 3)
        recovered = set()
        for body in bodies:
            block = body.split("{\n", 1)[1].rsplit("\n}", 1)[0]
            lines = block.splitlines()
            self.assertEqual(len(lines), 2)
            self.assertTrue(any(" <urn:sc:message> " in line for line in lines))
            recovered.update(lines)
        self.assertEqual(recovered, triples)

    def test_second_step_failure_still_deletes_private_workspace(self):
        http = queued_urlopen(
            PRIVATE_ROWS,
            b"",  # INSERT succeeds
            pcm.urllib.error.URLError("offline failure"),
            b"",  # cleanup still succeeds
        )
        with mock.patch.object(pcm._NO_PROXY_OPENER, "open", http):
            with self.assertRaises(pcm.PostFailure) as raised:
                pcm._execute_c_once(
                    "http://query.invalid",
                    "http://update.invalid",
                    self._plan(),
                    time.monotonic() + 2,
                )
        self.assertEqual(len(http.requests), 4)
        self.assertTrue(http.requests[-1].data.decode().startswith("DELETE DATA"))
        self.assertIn("cleanup=ok", raised.exception.detail)

    def test_legacy_flat_list_needs_no_update_endpoint(self):
        http = queued_urlopen(ANSWER_CIRCUIT)
        with mock.patch.object(pcm._NO_PROXY_OPENER, "open", http):
            result = pcm._execute_c_once(
                "http://query.invalid",
                None,
                ["CONSTRUCT { ?s ?p ?o } WHERE { ?s ?p ?o }"],
                time.monotonic() + 2,
            )
        self.assertEqual(result[5], 1)
        self.assertEqual(result[-1]["effective_mode"], "flat")
        self.assertEqual(len(http.requests), 1)

    def test_read_only_cell_explicitly_requests_flat(self):
        selected = []

        def flat_plan(_query, timeout=None, construction=None):
            selected.append(construction)
            return ["CONSTRUCT { ?s ?p ?o } WHERE { ?s ?p ?o }"]

        http = queued_urlopen(ANSWER_CIRCUIT)
        with mock.patch.object(pcm, "c_construct_plan", flat_plan), mock.patch.object(
            pcm._NO_PROXY_OPENER, "open", http
        ):
            result = pcm._time_method_impl(
                "C",
                "SELECT * WHERE { ?s ?p ?o }",
                "http://base.invalid",
                "http://reified.invalid",
                time.monotonic() + 2,
                warmups=0,
                runs=1,
                read_only=True,
            )
        self.assertEqual(selected, ["flat"])
        self.assertEqual(result["status"], "ok")
        self.assertIn("effective=flat", result["note"])

    def test_feedback_without_update_endpoint_is_explicitly_unsupported(self):
        with self.assertRaises(pcm.UnsupportedConstruction):
            pcm._execute_c_once(
                "http://query.invalid",
                None,
                self._plan(),
                time.monotonic() + 2,
            )

    def test_orphan_preflight_failure_is_fail_stop(self):
        failure = pcm.PostFailure("network", "cleanup endpoint unavailable")
        with mock.patch.object(pcm, "_orphan_cleanup", side_effect=failure), mock.patch.object(
            pcm, "c_construct_plan"
        ) as rewrite:
            result = pcm.time_method(
                "C",
                "SELECT * WHERE { ?s ?p ?o }",
                "http://base.invalid",
                "http://reified.invalid",
                update_ep="http://update.invalid",
                timeout=1,
                warmups=0,
                runs=1,
            )
        self.assertEqual(result["status"], "err:cleanup")
        self.assertEqual(result["cell_wall_ms"], 0.0)
        self.assertIn("not started", result["note"])
        rewrite.assert_not_called()

    @unittest.skipUnless(
        "fork" in multiprocessing.get_all_start_methods(),
        "offline process-injection tests require fork",
    )
    def test_parent_sweeps_orphans_after_hard_kill(self):
        def one_step(_query, timeout=None):
            return ["CONSTRUCT { ?s ?p ?o } WHERE { ?s ?p ?o }"]

        stream = fake_urlopen(interval=0.015, chunk_count=500)
        with mock.patch.object(pcm._NO_PROXY_OPENER, "open", stream), mock.patch.object(
            pcm, "c_construct_plan", one_step
        ), mock.patch.object(pcm, "_orphan_cleanup", return_value=2.5) as cleanup:
            result = pcm.time_method(
                "C",
                "SELECT * WHERE { ?s ?p ?o }",
                "http://base.invalid",
                "http://reified.invalid",
                update_ep="http://update.invalid",
                timeout=0.20,
                warmups=0,
                runs=1,
            )
        self.assertEqual(result["status"], "timeout")
        self.assertEqual(cleanup.call_count, 2)  # preflight + post-SIGKILL
        outside = result["protocol_metrics"]["outside_cell_cleanup"]
        self.assertEqual(outside["orphan_preflight_status"], "ok")
        self.assertEqual(outside["orphan_postkill_status"], "ok")
        self.assertIn("outside-cell post-kill", result["note"])

    def test_orphan_sweep_is_standard_update_with_hard_wrapper(self):
        with mock.patch.object(pcm, "post_timed", return_value=(3.0, 0, 0, None)) as post:
            elapsed = pcm._orphan_cleanup("http://update.invalid", timeout=1.5)
        self.assertEqual(elapsed, 3.0)
        args, kwargs = post.call_args
        self.assertEqual(args[0], "http://update.invalid")
        self.assertIn("?row <urn:sc:message> ?message", args[1])
        self.assertIn("DELETE { ?row ?p ?o }", args[1])
        self.assertNotIn("STRSTARTS", args[1])
        self.assertEqual(kwargs["content_type"], "application/sparql-update")
        self.assertEqual(kwargs["timeout"], 1.5)

    def test_parity_reuses_factored_feedback_executor_and_hygiene(self):
        executed = (
            1.0,
            10,
            0.1,
            2,
            1,
            1,
            0,
            {"answer-key"},
            {},
        )
        with mock.patch.object(pcm, "_orphan_cleanup", return_value=1.0) as cleanup, mock.patch.object(
            pcm, "c_construct_plan", return_value=self._plan()
        ) as rewrite, mock.patch.object(
            pcm, "_execute_c_once", return_value=executed
        ) as execute:
            keys, gates = parity.execute_c_plan(
                "http://query.invalid",
                "SELECT * WHERE { ?s ?p ?o }",
                timeout=2,
                update_endpoint="http://update.invalid",
            )
        self.assertEqual((keys, gates), ({"answer-key"}, 1))
        self.assertEqual(cleanup.call_count, 1)
        self.assertEqual(rewrite.call_args.kwargs["construction"], "factored")
        self.assertEqual(execute.call_args.args[1], "http://update.invalid")
        self.assertTrue(execute.call_args.kwargs["hard_http"])

    def test_hard_parity_executor_feedback_and_cleanup_use_killable_posts(self):
        responses = [
            (1.0, 3, len(PRIVATE_ROWS + PARTIAL_CIRCUIT), (PRIVATE_ROWS + PARTIAL_CIRCUIT).decode()),
            (1.0, 0, 0, None),
            (1.0, 3, len(ANSWER_CIRCUIT), ANSWER_CIRCUIT.decode()),
            (1.0, 0, 0, None),
        ]
        with mock.patch.object(pcm, "post_timed", side_effect=responses) as post:
            result = pcm._execute_c_once(
                "http://query.invalid",
                "http://update.invalid",
                self._plan(),
                time.monotonic() + 2,
                hard_http=True,
            )
        self.assertEqual(result[5], 1)
        self.assertEqual(post.call_count, 4)
        self.assertEqual(
            post.call_args_list[1].kwargs["content_type"],
            "application/sparql-update",
        )
        self.assertEqual(
            post.call_args_list[3].kwargs["content_type"],
            "application/sparql-update",
        )

    def test_parity_read_only_explicitly_uses_flat_without_updates(self):
        flat = pcm.ConstructionPlan(
            [pcm.ConstructionStep("CONSTRUCT WHERE {}", False)],
            "flat",
            "flat",
        )
        executed = (0, 0, 0, 0, 0, 0, 0, set(), {})
        with mock.patch.object(pcm, "_orphan_cleanup") as cleanup, mock.patch.object(
            pcm, "c_construct_plan", return_value=flat
        ) as rewrite, mock.patch.object(
            pcm, "_execute_c_once", return_value=executed
        ):
            parity.execute_c_plan(
                "http://query.invalid",
                "SELECT * WHERE {}",
                read_only=True,
            )
        cleanup.assert_not_called()
        self.assertEqual(rewrite.call_args.kwargs["construction"], "flat")

    def test_feedback_step_with_zero_private_rows_fails_protocol(self):
        plan = pcm.ConstructionPlan(
            [pcm.ConstructionStep("CONSTRUCT WHERE {}", True, "must-feed")],
            requested_mode="factored",
            effective_mode="factored",
        )
        with mock.patch.object(pcm._NO_PROXY_OPENER, "open", queued_urlopen(ANSWER_CIRCUIT)):
            with self.assertRaises(pcm.ConstructionProtocolError):
                pcm._execute_c_once(
                    "http://query.invalid",
                    "http://update.invalid",
                    plan,
                    time.monotonic() + 2,
                )

    def test_invalid_utf8_and_invalid_ntriples_fail_closed(self):
        for payload in (b"\xff\n", b"not an N-Triples statement\n"):
            with self.assertRaises((UnicodeDecodeError, ValueError)):
                pcm._merge_circuit_chunks([payload], set())

    @unittest.skipUnless(
        "fork" in multiprocessing.get_all_start_methods(),
        "offline process-injection tests require fork",
    )
    def test_cache_uses_only_first_measured_successful_circuit(self):
        def circuit(label):
            return (
                f"<urn:g:{label}> <{RDF_TYPE}> <urn:circuit:Plus> .\n"
                f"<urn:g:{label}> <urn:circuit:answer> \"{label}\" .\n"
            ).encode()

        def flat_plan(_query, timeout=None):
            return ["CONSTRUCT { ?s ?p ?o } WHERE { ?s ?p ?o }"]

        responses = sequenced_urlopen(
            circuit("warmup"),
            PRIVATE_ROWS + circuit("first-measured"),
            circuit("first-measured"),
        )
        metadata = {
            "commit": "d" * 40,
            "batch_id": "9" * 64,
            "query_sha256": "d" * 64,
            "engine": "offline",
            "scale": "10M",
            "class": "L",
            "template": "L1",
            "instance": "00",
        }
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(
            pcm._NO_PROXY_OPENER, "open", responses
        ), mock.patch.object(pcm, "c_construct_plan", flat_plan):
            result = pcm.time_method(
                "C",
                "SELECT * WHERE { ?s ?p ?o }",
                "http://base.invalid",
                "http://reified.invalid",
                timeout=2,
                warmups=1,
                runs=2,
                cache_dir=directory,
                cache_metadata=metadata,
            )
            self.assertEqual(result["status"], "ok")
            self.assertEqual(responses.counter.value, 3)
            protocol = result["protocol_metrics"]
            self.assertTrue(protocol["measured_repetitions_consistent"])
            self.assertEqual(len(protocol["c_warmup_signatures"]), 1)
            self.assertNotEqual(
                protocol["c_warmup_signatures"][0],
                protocol["measured_semantic_signature_sha256"],
            )
            self.assertEqual(len(result["c_protocol"]), 2)
            for network, parse, protocol_ms, total in zip(
                result["samples"],
                result["c_parse"],
                result["c_protocol"],
                result["construct_total"],
            ):
                self.assertGreaterEqual(
                    total + 0.001, network + parse + protocol_ms
                )
            descriptor = result["cache"]
            self.assertEqual(
                descriptor["circuit_sha256"], result["circuit_sha256"]
            )
            payload = Path(descriptor["circuit_path"]).read_bytes()
            self.assertEqual(
                payload,
                circuit_cache.canonical_bytes(
                    circuit("first-measured").splitlines()
                ),
            )
            self.assertNotIn(b"urn:sc:", payload)
            self.assertNotIn(b"warmup", payload)
            sidecar = json.loads(
                Path(descriptor["metadata_path"]).read_text(encoding="utf-8")
            )
            self.assertEqual(sidecar["schema"], circuit_cache.SCHEMA)
            self.assertEqual(len(sidecar["producer_observations"]), 1)
            observation = sidecar["producer_observations"][0]
            for name, value in metadata.items():
                self.assertEqual(observation[name], value)
            self.assertEqual(observation["construction_requested"], "flat")
            self.assertEqual(observation["construction_effective"], "flat")
            self.assertEqual(
                descriptor["producer_observation_sha256"],
                observation["producer_observation_sha256"],
            )
            self.assertEqual(len(list(Path(directory).glob("*.nt"))), 1)
            self.assertEqual(len(list(Path(directory).glob("*.json"))), 1)

    @unittest.skipUnless(
        "fork" in multiprocessing.get_all_start_methods(),
        "offline process-injection tests require fork",
    )
    def test_measured_circuit_nondeterminism_fails_without_cache(self):
        def flat_plan(_query, timeout=None):
            return ["CONSTRUCT { ?s ?p ?o } WHERE { ?s ?p ?o }"]

        responses = sequenced_urlopen(
            ANSWER_CIRCUIT,
            ANSWER_CIRCUIT.replace(b"urn:g:a", b"urn:g:different"),
        )
        metadata = {
            "commit": "d" * 40,
            "batch_id": "9" * 64,
            "query_sha256": "f" * 64,
        }
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(
            pcm._NO_PROXY_OPENER, "open", responses
        ), mock.patch.object(pcm, "c_construct_plan", flat_plan):
            result = pcm.time_method(
                "C",
                "SELECT * WHERE { ?s ?p ?o }",
                "http://base.invalid",
                "http://reified.invalid",
                timeout=2,
                warmups=0,
                runs=2,
                cache_dir=directory,
                cache_metadata=metadata,
            )
            self.assertEqual(result["status"], "answer-mismatch")
            self.assertEqual(list(Path(directory).iterdir()), [])

    @unittest.skipUnless(
        "fork" in multiprocessing.get_all_start_methods(),
        "offline process-injection tests require fork",
    )
    def test_failed_cell_never_populates_external_cache(self):
        def flat_plan(_query, timeout=None):
            return ["CONSTRUCT { ?s ?p ?o } WHERE { ?s ?p ?o }"]

        responses = sequenced_urlopen(
            ANSWER_CIRCUIT,
            pcm.urllib.error.URLError("second measured run failed"),
        )
        metadata = {
            "commit": "d" * 40,
            "batch_id": "9" * 64,
            "query_sha256": "e" * 64,
        }
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(
            pcm._NO_PROXY_OPENER, "open", responses
        ), mock.patch.object(pcm, "c_construct_plan", flat_plan):
            result = pcm.time_method(
                "C",
                "SELECT * WHERE { ?s ?p ?o }",
                "http://base.invalid",
                "http://reified.invalid",
                timeout=2,
                warmups=0,
                runs=2,
                cache_dir=directory,
                cache_metadata=metadata,
            )
            self.assertNotEqual(result["status"], "ok")
            self.assertEqual(list(Path(directory).iterdir()), [])


class AnswerIdentityTests(unittest.TestCase):
    @staticmethod
    def _json_term(kind, value, **extra):
        return {"type": kind, "value": value, **extra}

    def test_json_and_circuit_keys_preserve_all_term_kinds(self):
        xsd_integer = "http://www.w3.org/2001/XMLSchema#integer"
        rows = [
            {"v": self._json_term("uri", "urn:same")},
            {"v": self._json_term("literal", "urn:same")},
            {"v": self._json_term("literal", "7", datatype=xsd_integer)},
            {"v": self._json_term("literal", "bonjour", **{"xml:lang": "FR"})},
            {},  # v is unbound
        ]
        for row in rows:
            row[pcm.PROVENANCE_VAR] = self._json_term("literal", "provenance")
        payload = {
            "head": {"vars": ["v", pcm.PROVENANCE_VAR]},
            "results": {"bindings": rows},
        }
        n_keys = parity.candidate_key_set(payload)

        raw_terms = [
            "<urn:same>",
            '"urn:same"',
            f'"7"^^<{xsd_integer}>',
            '"bonjour"@FR',
            None,
        ]
        triples = []
        for index, raw in enumerate(raw_terms):
            gate, binding = f"urn:g:{index}", f"urn:b:{index}"
            triples.extend(
                [
                    f"<{gate}> <urn:circuit:binding> <{binding}> .",
                    f'<{binding}> <urn:circuit:var> "v" .',
                ]
            )
            if raw is not None:
                triples.append(f"<{binding}> <urn:circuit:val> {raw} .")
        _, answer_gates, bindings = circuit_io.parse(triples)
        c_keys = {
            circuit_io.answer_key(bindings.get(gate, {})) for gate in answer_gates
        }

        self.assertEqual(n_keys, c_keys)
        self.assertEqual(len(n_keys), 5)
        # Same lexical spelling, but IRI and literal remain two answers.
        self.assertNotEqual(
            pcm.canonical_json_term(rows[0]["v"]),
            pcm.canonical_json_term(rows[1]["v"]),
        )
        self.assertEqual(
            pcm.canonical_json_term(rows[2]["v"]),
            circuit_io.canon_term(raw_terms[2]),
        )
        self.assertEqual(
            pcm.canonical_json_term(rows[3]["v"]),
            circuit_io.canon_term(raw_terms[3]),
        )
        self.assertEqual(pcm.canonical_json_term(None), "u")

    def test_same_count_different_answers_is_a_mismatch(self):
        left = pcm.normalized_csv_multiset("x\nurn:a\nurn:b\n")
        right = pcm.normalized_csv_multiset("x\nurn:a\nurn:c\n")
        self.assertEqual(sum(left.values()), sum(right.values()))
        self.assertNotEqual(
            pcm.multiset_evidence(left)["answer_fingerprint"],
            pcm.multiset_evidence(right)["answer_fingerprint"],
        )
        self.assertFalse(parity.compare_multisets(left, right))

        key = ("graphdb", "10M", "L", "L1", "00")
        methods = {method: {"status": "ok", "answers": "2"} for method in "BRNC"}
        exact = {
            key: {
                "protocol": pcm.PROTOCOL,
                "br_multiset_equal": "False",
                "br_kind": "term-aware-binding-multiset-v1",
                "nc_keys_equal": "False",
                "nc_kind": "term-aware-candidate-set-v1",
            }
        }
        br, nc, source = summarize_brnc.parity_states(key, methods, exact, {})
        self.assertEqual((br, nc, source), ("mismatch", "mismatch", "term-aware-json"))

    def test_candidate_comparison_does_not_use_full_world_rows(self):
        # OPTIONAL-like candidate: one answer has y unbound.  B/R is deliberately
        # unrelated and must not enter the N/C comparison.
        payload = {
            "head": {"vars": ["x", "y", pcm.PROVENANCE_VAR]},
            "results": {
                "bindings": [
                    {
                        "x": self._json_term("uri", "urn:x"),
                        pcm.PROVENANCE_VAR: self._json_term("literal", "p"),
                    }
                ]
            },
        }
        candidates = parity.candidate_key_set(payload)
        only = next(iter(candidates))
        self.assertIn("y=u", only)
        self.assertNotIn(pcm.PROVENANCE_VAR, only)
        self.assertTrue(parity.compare_candidate_sets(candidates, set(candidates)))

    def test_capture_safe_provenance_alias_does_not_drop_user_variable(self):
        generated = "__npcs0_finalprovennacevariable"
        payload = {
            "head": {"vars": [pcm.PROVENANCE_VAR, generated]},
            "results": {
                "bindings": [
                    {
                        pcm.PROVENANCE_VAR: self._json_term("uri", "urn:user-value"),
                        generated: self._json_term("literal", "provenance"),
                    }
                ]
            },
        }
        rewritten = (
            "SELECT ?finalprovennacevariable "
            "(GROUP_CONCAT(?p) AS ?__npcs0_finalprovennacevariable) WHERE {}"
        )
        keys = parity.candidate_key_set(payload, rewritten_query=rewritten)
        only = next(iter(keys))
        self.assertIn("finalprovennacevariable=i\x1furn:user-value", only)
        self.assertNotIn(generated, only)

        csv_text = (
            "finalprovennacevariable,__npcs0_finalprovennacevariable\n"
            "urn:user-value,provenance\n"
        )
        csv_candidates = pcm.npcs_csv_candidate_multiset(
            csv_text, rewritten_query=rewritten
        )
        self.assertEqual(
            list(csv_candidates),
            [(('finalprovennacevariable', 'urn:user-value'),)],
        )

    def test_formal_gate_fails_closed_on_unverified(self):
        unverified = [{"br_multiset_equal": None, "nc_keys_equal": True}]
        self.assertEqual(parity.gate_exit_code(unverified), 1)
        self.assertEqual(
            parity.gate_exit_code(unverified, allow_unverified=True), 0
        )
        mismatch = [{"br_multiset_equal": False, "nc_keys_equal": True}]
        self.assertEqual(
            parity.gate_exit_code(mismatch, allow_unverified=True), 1
        )
        verified = [{"br_multiset_equal": True, "nc_keys_equal": True}]
        self.assertEqual(parity.gate_exit_code(verified), 0)
        self.assertEqual(parity.gate_exit_code([]), 1)
        self.assertEqual(parity.gate_exit_code([], allow_unverified=True), 0)

    def test_parity_run_gates_the_complete_merged_active_batch(self):
        good = {"br_multiset_equal": True, "nc_keys_equal": True}
        old_bad = {"br_multiset_equal": "False", "nc_keys_equal": "True"}
        args = types.SimpleNamespace(
            batch_id="a" * 64,
            engine="graphdb",
            engines=None,
            scale="10M",
            scales=None,
            out="unused.csv",
            allow_unverified=False,
            exploratory=True,
        )
        with mock.patch.object(parity, "run_one", return_value=[good]), mock.patch.object(
            parity, "merge_parity_rows", return_value=[good, old_bad]
        ):
            self.assertEqual(parity.run(args), 1)


class CheckpointTests(unittest.TestCase):
    def _row(self, method="B", status="ok", samples=None, template="L1"):
        samples = [1.0] if samples is None else samples
        evidence = pcm.multiset_evidence(
            pcm.normalized_csv_multiset("x\nurn:a\n")
        )
        identity = {
            "batch_id": "b" * 64,
            "base_endpoint_sha256": "1" * 64,
            "reified_endpoint_sha256": "2" * 64,
            "update_endpoint_sha256": "3" * 64,
            "base_data_identity_sha256": "4" * 64,
            "reified_data_identity_sha256": "5" * 64,
            "update_for": "reified",
            "access_mode": "writable",
            "base_data_name": "base-data",
            "reified_data_name": "reified-data",
            "update_canary_sha256": "a" * 64,
            "store_instance_sha256": "7" * 64,
            "store_discriminator_sha256": "8" * 64,
            "tool_sha256": "9" * 64,
            "java_runtime_sha256": "a" * 64,
            "run_identity_sha256": "6" * 64,
        }
        summary = pcm.stat(samples) if samples else None
        status_evidence = {
            "kind": "test-terminal" if status in pcm.TERMINAL_STATUSES else "retryable-test",
            "message": status,
        }
        row = {name: "" for name in pcm.COLS}
        row.update(
            {
                "commit": pcm.COMMIT,
                "engine": "graphdb",
                "engine_version": "test",
                "scale": "10M",
                "class": "L",
                "template": template,
                "instance": "00",
                "query_sha256": "a" * 64,
                "method": method,
                "implementation": "test",
                "status": status,
                "warmups": "0",
                "runs": "1",
                "timeout_s": "1.0",
                "rewrite_ms": "1.25",
                "samples_json": json.dumps(samples),
                "median_ms": str(round(summary["median"], 1)) if summary else "",
                "min_ms": str(round(summary["min"], 1)) if summary else "",
                "max_ms": str(round(summary["max"], 1)) if summary else "",
                "mean_ms": str(round(summary["mean"], 1)) if summary else "",
                "sd_ms": str(round(summary["sd"], 1)) if summary else "",
                "protocol": pcm.PROTOCOL,
                **identity,
                **evidence,
                "notes": pcm.pack_note(
                    status,
                    {
                        **evidence,
                        **identity,
                        "status": status,
                        "status_evidence": status_evidence,
                    },
                    cell_wall_ms=2.0,
                ),
            }
        )
        return row

    @staticmethod
    def _expected_identity(row):
        names = (
            "commit", "batch_id", "protocol", "query_sha256", "engine",
            "engine_version", "scale", "base_endpoint_sha256",
            "reified_endpoint_sha256", "update_endpoint_sha256",
            "base_data_identity_sha256", "reified_data_identity_sha256",
            "update_for", "access_mode", "base_data_name", "reified_data_name",
            "update_canary_sha256", "store_instance_sha256",
            "store_discriminator_sha256", "tool_sha256", "java_runtime_sha256",
            "run_identity_sha256",
        )
        return {name: row[name] for name in names}

    def test_latest_failure_is_retryable_and_overdeadline_ok_is_invalid(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "checkpoint.csv")
            with open(path, "w", newline="") as fh:
                writer = csv.DictWriter(fh, fieldnames=pcm.COLS)
                writer.writeheader()
                writer.writerow(self._row())
            key = ("graphdb", "10M", "L", "L1", "00", "a" * 64, "B")
            self.assertIn(
                key,
                pcm.load_done(
                    path,
                    warmups=0,
                    runs=1,
                    timeout=1,
                    current_batch_id="b" * 64,
                ),
            )

            with open(path, "a", newline="") as fh:
                writer = csv.DictWriter(fh, fieldnames=pcm.COLS)
                writer.writerow(self._row(status="err:network"))
                writer.writerow(self._row(template="L2", samples=[1001.0]))
            done = pcm.load_done(
                path,
                warmups=0,
                runs=1,
                timeout=1,
                current_batch_id="b" * 64,
            )
            self.assertNotIn(key, done)  # latest transient failure can be retried
            over_key = ("graphdb", "10M", "L", "L2", "00", "a" * 64, "B")
            self.assertNotIn(over_key, done)  # never retain >timeout as ok

            # A torn physical record is ignored instead of poisoning all resumes.
            with open(path, "a") as fh:
                fh.write('"unterminated')
            list(pcm._checkpoint_rows(path))

    def test_checkpoint_protocol_numbers_require_canonical_csv_spelling(self):
        row = self._row()
        for field, bad in (
            ("warmups", "+0"),
            ("warmups", "00"),
            ("runs", "01"),
            ("runs", " 1"),
            ("timeout_s", "nan"),
            ("timeout_s", "1"),
            ("timeout_s", "1e0"),
            ("timeout_s", " 1.0"),
        ):
            with self.subTest(field=field, bad=bad):
                tampered = dict(row)
                tampered[field] = bad
                self.assertFalse(
                    pcm.checkpoint_complete(
                        tampered,
                        warmups=0,
                        runs=1,
                        timeout=1,
                        current_batch_id="b" * 64,
                    )
                )

    def test_formal_resume_requires_the_exact_physical_slot_prefix(self):
        first = self._row(template="L1")
        second = self._row(template="L2")
        expected = {
            pcm._row_key(first): self._expected_identity(first),
            pcm._row_key(second): self._expected_identity(second),
        }

        def write(path, rows):
            with open(path, "w", newline="") as fh:
                writer = csv.DictWriter(fh, fieldnames=pcm.COLS)
                writer.writeheader()
                writer.writerows(rows)

        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "checkpoint.csv")
            write(path, [first])
            self.assertEqual(
                pcm.load_done(
                    path,
                    warmups=0,
                    runs=1,
                    timeout=1,
                    expected_identities=expected,
                    current_commit=pcm.COMMIT,
                    current_batch_id="b" * 64,
                ),
                {pcm._row_key(first)},
            )
            for rows in ([second], [second, first], [first, first]):
                write(path, rows)
                with self.subTest(keys=[pcm._row_key(row) for row in rows]):
                    with self.assertRaises(ValueError):
                        pcm.load_done(
                            path,
                            warmups=0,
                            runs=1,
                            timeout=1,
                            expected_identities=expected,
                            current_commit=pcm.COMMIT,
                            current_batch_id="b" * 64,
                        )

    def test_resume_is_bound_to_commit_batch_endpoint_data_and_query(self):
        row = self._row()
        key = ("graphdb", "10M", "L", "L1", "00", "a" * 64, "B")
        expected = {
            name: row[name]
            for name in (
                "commit",
                "batch_id",
                "protocol",
                "query_sha256",
                "engine",
                "engine_version",
                "scale",
                "base_endpoint_sha256",
                "reified_endpoint_sha256",
                "update_endpoint_sha256",
                "base_data_identity_sha256",
                "reified_data_identity_sha256",
                "update_for",
                "access_mode",
                "base_data_name",
                "reified_data_name",
                "update_canary_sha256",
                "store_instance_sha256",
                "store_discriminator_sha256",
                "tool_sha256",
                "java_runtime_sha256",
                "run_identity_sha256",
            )
        }
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "checkpoint.csv")
            with open(path, "w", newline="") as fh:
                writer = csv.DictWriter(fh, fieldnames=pcm.COLS)
                writer.writeheader()
                writer.writerow(row)
            self.assertIn(
                key,
                pcm.load_done(
                    path,
                    warmups=0,
                    runs=1,
                    timeout=1,
                    expected_identities={key: expected},
                    current_commit=pcm.COMMIT,
                    current_batch_id="b" * 64,
                ),
            )
            for field in (
                "commit",
                "batch_id",
                "query_sha256",
                "engine_version",
                "base_endpoint_sha256",
                "reified_data_identity_sha256",
                "run_identity_sha256",
            ):
                changed = dict(expected)
                changed[field] = "different"
                with self.assertRaises(ValueError, msg=field):
                    pcm.load_done(
                        path,
                        warmups=0,
                        runs=1,
                        timeout=1,
                        expected_identities={key: changed},
                        current_commit=(
                            changed["commit"] if field == "commit" else pcm.COMMIT
                        ),
                        current_batch_id=(
                            changed["batch_id"]
                            if field == "batch_id"
                            else "b" * 64
                        ),
                    )

    def test_checkpoint_recomputes_all_c_summaries_and_terminal_evidence(self):
        row = self._row(method="C")
        row.update(
            {
                "construction_requested": "factored",
                "construction_effective": "factored",
                "circuit_sha256": "c" * 64,
                "c_parse_median_ms": "2.0",
                "c_protocol_median_ms": "3.0",
                "construct_total_ms": "7.0",
                "construct_unattributed_median_ms": "1.0",
            }
        )
        metadata = pcm.unpack_note(row)
        metadata.update(
            {
                "construction_requested": "factored",
                "construction_effective": "factored",
                "circuit_sha256": "c" * 64,
                "c_parse_samples": [2.0],
                "c_protocol_samples": [3.0],
                "construct_total_samples": [7.0],
                "construct_unattributed_samples": [1.0],
            }
        )
        row["notes"] = pcm.pack_note("ok", metadata, cell_wall_ms=10.0)
        self.assertTrue(
            pcm.checkpoint_complete(
                row,
                warmups=0,
                runs=1,
                timeout=1,
                current_batch_id="b" * 64,
            )
        )
        for field in (
            "median_ms",
            "min_ms",
            "max_ms",
            "mean_ms",
            "sd_ms",
            "c_parse_median_ms",
            "c_protocol_median_ms",
            "construct_total_ms",
            "construct_unattributed_median_ms",
        ):
            tampered = dict(row)
            tampered[field] = "999.0"
            self.assertFalse(
                pcm.checkpoint_complete(
                    tampered,
                    warmups=0,
                    runs=1,
                    timeout=1,
                    current_batch_id="b" * 64,
                ),
                field,
            )

        timeout_row = self._row(status="timeout", samples=[])
        self.assertTrue(
            pcm.checkpoint_complete(
                timeout_row,
                warmups=0,
                runs=1,
                timeout=1,
                current_batch_id="b" * 64,
            )
        )
        self.assertFalse(
            pcm.checkpoint_complete(
                timeout_row, warmups=0, runs=1, timeout=1, current_batch_id=None
            )
        )
        broken = dict(timeout_row)
        broken_meta = pcm.unpack_note(broken)
        broken_meta.pop("status_evidence")
        broken["notes"] = pcm.pack_note(
            "timeout", broken_meta, cell_wall_ms=2.0
        )
        self.assertFalse(
            pcm.checkpoint_complete(
                broken,
                warmups=0,
                runs=1,
                timeout=1,
                current_batch_id="b" * 64,
            )
        )
        contaminated = dict(timeout_row)
        contaminated_meta = pcm.unpack_note(contaminated)
        contaminated_meta["outside_cell_cleanup"] = {
            "orphan_postkill_status": "failed"
        }
        contaminated["notes"] = pcm.pack_note(
            "timeout", contaminated_meta, cell_wall_ms=2.0
        )
        self.assertFalse(
            pcm.checkpoint_complete(
                contaminated,
                warmups=0,
                runs=1,
                timeout=1,
                current_batch_id="b" * 64,
            )
        )

    def test_formal_c_resume_revalidates_exact_cache_producer_identity(self):
        row = self._row(method="C")
        row.update(
            {
                "construction_requested": "factored",
                "construction_effective": "factored",
                "c_parse_median_ms": "2.0",
                "c_protocol_median_ms": "3.0",
                "construct_total_ms": "7.0",
                "construct_unattributed_median_ms": "1.0",
            }
        )
        cache_identity_names = (
            "commit", "batch_id", "protocol", "query_sha256", "engine",
            "engine_version", "scale", "class", "template", "instance",
            "base_endpoint_sha256", "reified_endpoint_sha256",
            "update_endpoint_sha256", "base_data_identity_sha256",
            "reified_data_identity_sha256", "update_for", "access_mode",
            "base_data_name", "reified_data_name", "update_canary_sha256",
            "store_instance_sha256", "store_discriminator_sha256", "tool_sha256",
            "java_runtime_sha256", "run_identity_sha256",
            "construction_requested", "construction_effective",
        )
        with tempfile.TemporaryDirectory() as directory:
            stored = circuit_cache.store(
                directory,
                ["<urn:g> <urn:p> <urn:o> ."],
                {name: row[name] for name in cache_identity_names},
            )
            row.update(
                {
                    "circuit_sha256": stored["circuit_sha256"],
                    "circuit_cache_path": stored["circuit_path"],
                    "circuit_cache_metadata_path": stored["metadata_path"],
                    "circuit_cache_observation_sha256": stored[
                        "producer_observation_sha256"
                    ],
                    "circuit_cache_sidecar_sha256": stored["sidecar_sha256"],
                }
            )
            metadata = pcm.unpack_note(row)
            metadata.update(
                {
                    "construction_requested": "factored",
                    "construction_effective": "factored",
                    "circuit_sha256": stored["circuit_sha256"],
                    "c_parse_samples": [2.0],
                    "c_protocol_samples": [3.0],
                    "construct_total_samples": [7.0],
                    "construct_unattributed_samples": [1.0],
                }
            )
            row["notes"] = pcm.pack_note("ok", metadata, cell_wall_ms=10.0)
            self.assertTrue(
                pcm.checkpoint_complete(
                    row,
                    warmups=0,
                    runs=1,
                    timeout=1,
                    require_circuit_cache=True,
                    current_batch_id="b" * 64,
                )
            )
            changed = dict(row, engine_version="different")
            self.assertFalse(
                pcm.checkpoint_complete(
                    changed,
                    warmups=0,
                    runs=1,
                    timeout=1,
                    require_circuit_cache=True,
                    current_batch_id="b" * 64,
                )
            )
    def test_manifest_query_hash_is_recomputed_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            paper = os.path.join(directory, "paper")
            os.mkdir(paper)
            query_path = os.path.join(directory, "query.rq")
            query = b"SELECT * WHERE { ?s ?p ?o }\n"
            with open(query_path, "wb") as fh:
                fh.write(query)
            manifest = os.path.join(paper, "workload_manifest.csv")
            with open(manifest, "w", newline="") as fh:
                writer = csv.DictWriter(
                    fh,
                    fieldnames=pcm.freeze_inputs.MANIFESTS["workload"]["columns"],
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "suite": "test",
                        "class": "L",
                        "template": "L1",
                        "instance": "00",
                        "query_file": "query.rq",
                        "query_sha256": hashlib.sha256(query).hexdigest(),
                        "scale": "10M",
                        "bound_policy": "fixed",
                        "notes": "",
                    }
                )
            with mock.patch.object(pcm, "HERE", paper), mock.patch.object(
                pcm, "REF", directory
            ):
                rows = pcm.load_manifest()
                self.assertEqual(len(rows), 1)
                with open(query_path, "ab") as fh:
                    fh.write(b"# changed\n")
                with self.assertRaises(RuntimeError):
                    pcm.read_query_verified(rows[0])
                with self.assertRaises(RuntimeError):
                    pcm.load_manifest()

    def test_open_writer_atomically_upgrades_old_csv_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "legacy-checkpoint.csv")
            old_fields = list(pcm.LEGACY_COLS)
            old_row = {name: "" for name in old_fields}
            old_row.update(
                {
                    "commit": "old",
                    "engine": "graphdb",
                    "scale": "10M",
                    "class": "L",
                    "template": "L1",
                    "instance": "00",
                    "query_sha256": "a" * 64,
                    "method": "B",
                    "status": "timeout",
                    "notes": "legacy-note",
                }
            )
            with open(path, "w", newline="") as fh:
                writer = csv.DictWriter(fh, fieldnames=old_fields)
                writer.writeheader()
                writer.writerow(old_row)
            output, _writer = pcm._open_writer(path)
            output.close()
            with open(path, newline="") as fh:
                reader = csv.DictReader(fh)
                rows = list(reader)
                header = reader.fieldnames
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["notes"], "legacy-note")
            for name in (
                "construction_requested",
                "construction_effective",
                "circuit_sha256",
                "circuit_cache_path",
            ):
                self.assertIn(name, header)
                self.assertEqual(rows[0][name], "")

    def test_summarizer_rejects_legacy_csv_and_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            source = os.path.join(directory, "legacy.csv")
            output = os.path.join(directory, "summary.csv")
            parity_file = os.path.join(directory, "missing-parity.csv")
            row = {name: "" for name in pcm.LEGACY_COLS}
            row.update(
                {
                    "engine": "graphdb",
                    "scale": "10M",
                    "class": "L",
                    "template": "L1",
                    "instance": "00",
                    "query_sha256": "old",
                    "method": "B",
                    "status": "ok",
                    "warmups": "1",
                    "runs": "5",
                    "timeout_s": "300",
                    "samples_json": "[1,1,1,1,1]",
                }
            )
            with open(source, "w", newline="") as fh:
                writer = csv.DictWriter(fh, fieldnames=pcm.LEGACY_COLS)
                writer.writeheader()
                writer.writerow(row)
            self.assertEqual(
                summarize_brnc._timing_cells(source, current_batch_id="b" * 64),
                {},
            )
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(
                    summarize_brnc.main(
                        ["--src", source, "--parity", parity_file, "--out", output]
                    ),
                    1,
                )
                self.assertEqual(
                    summarize_brnc.main(
                        [
                            "--src", source,
                            "--parity", parity_file,
                            "--out", output,
                            "--allow-mismatch",
                        ]
                    ),
                    1,
                )
                self.assertEqual(
                    summarize_brnc.main(
                        [
                            "--src", source,
                            "--parity", parity_file,
                            "--out", output,
                            "--allow-unverified",
                        ]
                    ),
                    0,
                )

    def test_summarizer_requires_frozen_formal_sampling_protocol(self):
        with tempfile.TemporaryDirectory() as directory:
            source = os.path.join(directory, "timing.csv")

            # A perfectly well-formed v3 checkpoint from a one-run smoke test
            # must not silently enter the formal five-run/300-second table.
            exploratory = self._row(samples=[1.0])
            with open(source, "w", newline="") as fh:
                writer = csv.DictWriter(fh, fieldnames=pcm.COLS)
                writer.writeheader()
                writer.writerow(exploratory)
            self.assertEqual(summarize_brnc._timing_cells(source), {})

            formal = self._row(samples=[1.0] * summarize_brnc.FORMAL_RUNS)
            formal_metadata = pcm.unpack_note(formal)
            formal.update(
                {
                    "warmups": "1",
                    "runs": str(summarize_brnc.FORMAL_RUNS),
                    "timeout_s": str(summarize_brnc.FORMAL_TIMEOUT),
                    "samples_json": json.dumps(
                        [1.0] * summarize_brnc.FORMAL_RUNS
                    ),
                    "notes": pcm.pack_note(
                        "ok",
                        formal_metadata,
                        cell_wall_ms=10.0,
                    ),
                }
            )
            with open(source, "w", newline="") as fh:
                writer = csv.DictWriter(fh, fieldnames=pcm.COLS)
                writer.writeheader()
                writer.writerow(formal)
            cells = summarize_brnc._timing_cells(
                source, current_batch_id="b" * 64
            )
            self.assertIn(("graphdb", "10M", "L", "L1", "00"), cells)

    def test_timing_completion_is_exact_nonempty_and_timeout_is_publishable(self):
        profile = {
            "warmups": 0,
            "runs": 1,
            "timeout_s": 1.0,
            "update_chunk_triples": pcm.FORMAL_UPDATE_CHUNK_TRIPLES,
            "orphan_cleanup_timeout_s": pcm.FORMAL_ORPHAN_CLEANUP_TIMEOUT,
        }
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "timing.csv")
            timeout_row = self._row(status="timeout", samples=[])
            key = pcm._row_key(timeout_row)
            expected = {key: self._expected_identity(timeout_row)}
            with open(path, "w", newline="") as fh:
                writer = csv.DictWriter(fh, fieldnames=pcm.COLS)
                writer.writeheader()
                writer.writerow(timeout_row)
            proof = pcm.finalize_timing_completion(
                path,
                expected_identities=expected,
                commit=pcm.COMMIT,
                batch_id="b" * 64,
                profile=profile,
                engines=("graphdb",),
                scales=("10M",),
                methods=("B",),
                classes=pcm.FORMAL_CLASSES,
                require_circuit_cache=False,
            )
            self.assertEqual(proof["terminal_status_counts"], {"timeout": 1})
            payload, rows = pcm._strict_timing_snapshot(path)
            self.assertEqual(
                pcm.verify_completion_sidecar(
                    path,
                    expected_schema="r9-timing-completion-v1",
                    label="R9 timing completion sidecar",
                    csv_payload=payload,
                    csv_rows=len(rows),
                )["expected_cells"],
                1,
            )
            completion = Path(pcm._completion_path(path))
            valid_bytes = completion.read_bytes()
            valid_document = json.loads(valid_bytes)
            mutations = []
            wrong_type = dict(valid_document, expected_cells=1.0)
            mutations.append(pcm._sealed_json(wrong_type))
            bool_counter = dict(valid_document, csv_rows=True)
            mutations.append(pcm._sealed_json(bool_counter))
            extra_field = dict(valid_document, unexpected="forged")
            mutations.append(pcm._sealed_json(extra_field))
            for forged in mutations:
                completion.write_bytes(pcm._canonical_json_bytes(forged))
                with self.subTest(forged=sorted(set(forged) - set(valid_document))):
                    with self.assertRaises(ValueError):
                        pcm.verify_completion_sidecar(
                            path,
                            expected_schema="r9-timing-completion-v1",
                            label="R9 timing completion sidecar",
                            csv_payload=payload,
                            csv_rows=len(rows),
                        )
                    with self.assertRaises(ValueError):
                        pcm.finalize_timing_completion(
                            path,
                            expected_identities=expected,
                            commit=pcm.COMMIT,
                            batch_id="b" * 64,
                            profile=profile,
                            engines=("graphdb",),
                            scales=("10M",),
                            methods=("B",),
                            classes=pcm.FORMAL_CLASSES,
                            require_circuit_cache=False,
                        )
            completion.write_text(
                json.dumps(valid_document, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                pcm.verify_completion_sidecar(
                    path,
                    expected_schema="r9-timing-completion-v1",
                    label="R9 timing completion sidecar",
                    csv_payload=payload,
                    csv_rows=len(rows),
                )
            completion.write_bytes(valid_bytes)
            with open(path, "ab") as fh:
                fh.write(b"tamper\n")
            with self.assertRaises(ValueError):
                pcm.verify_completion_sidecar(
                    path,
                    expected_schema="r9-timing-completion-v1",
                    label="R9 timing completion sidecar",
                )

    def test_timing_completion_rejects_zero_extra_and_correctness_terminal(self):
        profile = {
            "warmups": 0,
            "runs": 1,
            "timeout_s": 1.0,
            "update_chunk_triples": pcm.FORMAL_UPDATE_CHUNK_TRIPLES,
            "orphan_cleanup_timeout_s": pcm.FORMAL_ORPHAN_CLEANUP_TIMEOUT,
        }

        def finalize(path, rows, expected):
            with open(path, "w", newline="") as fh:
                writer = csv.DictWriter(fh, fieldnames=pcm.COLS)
                writer.writeheader()
                writer.writerows(rows)
            return pcm.finalize_timing_completion(
                path,
                expected_identities=expected,
                commit=pcm.COMMIT,
                batch_id="b" * 64,
                profile=profile,
                engines=("graphdb",),
                scales=("10M",),
                methods=("B",),
                classes=pcm.FORMAL_CLASSES,
                require_circuit_cache=False,
            )

        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "timing.csv")
            with self.assertRaises(RuntimeError):
                finalize(path, [], {})
            good = self._row()
            expected = {pcm._row_key(good): self._expected_identity(good)}
            extra = self._row(template="L2")
            with self.assertRaises(ValueError):
                finalize(path, [good, extra], expected)
            mismatch = self._row(status="answer-mismatch", samples=[])
            mismatch_expected = {
                pcm._row_key(mismatch): self._expected_identity(mismatch)
            }
            with self.assertRaises(RuntimeError):
                finalize(path, [mismatch], mismatch_expected)
            transient = self._row(status="err:http", samples=[])
            transient_expected = {
                pcm._row_key(transient): self._expected_identity(transient)
            }
            with self.assertRaises(RuntimeError):
                finalize(path, [transient], transient_expected)

    def test_canonical_circuit_gate_detects_cross_engine_disagreement(self):
        def row(engine, digest, scale="10M"):
            return {
                "commit": pcm.COMMIT,
                "protocol": pcm.PROTOCOL,
                "batch_id": "b" * 64,
                "engine": engine,
                "scale": scale,
                "class": "L",
                "template": "L1",
                "instance": "00",
                "query_sha256": "a" * 64,
                "method": "C",
                "status": "ok",
                "circuit_sha256": digest,
            }

        agreeing = [row("graphdb", "c" * 64), row("oxigraph", "c" * 64)]
        proof = pcm.canonical_circuit_gate(
            agreeing, commit=pcm.COMMIT, batch_id="b" * 64
        )
        self.assertEqual(
            proof["10M/" + "a" * 64]["engines"], ["graphdb", "oxigraph"]
        )
        with self.assertRaises(ValueError):
            pcm.canonical_circuit_gate(
                [row("graphdb", "c" * 64), row("oxigraph", "d" * 64)],
                commit=pcm.COMMIT,
                batch_id="b" * 64,
            )
        # The circuit is data-dependent, so different scales are deliberately
        # not compared with each other.
        pcm.canonical_circuit_gate(
            [row("graphdb", "c" * 64, "10M"), row("graphdb", "d" * 64, "100M")],
            commit=pcm.COMMIT,
            batch_id="b" * 64,
        )

    def test_tail_repair_uses_only_a_torn_final_record(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "timing.csv")
            good = io.StringIO(newline="")
            writer = csv.DictWriter(good, fieldnames=pcm.COLS)
            writer.writeheader()
            writer.writerow(self._row())
            complete = good.getvalue().encode("utf-8")
            Path(path).write_bytes(complete + b'"torn')
            pcm._repair_checkpoint_tail(path)
            self.assertEqual(Path(path).read_bytes(), complete)

            malformed = complete + b"one,two\n" + b'"torn'
            Path(path).write_bytes(malformed)
            before = Path(path).read_bytes()
            with self.assertRaises(ValueError):
                pcm._repair_checkpoint_tail(path)
            self.assertEqual(Path(path).read_bytes(), before)


class MatrixConfigurationTests(unittest.TestCase):
    @staticmethod
    def _strict_parity_row():
        row = {name: "" for name in parity.COLS}
        row.update(
            {
                "protocol": pcm.PROTOCOL,
                "commit": pcm.COMMIT,
                "batch_id": "a" * 64,
                "engine": "graphdb",
                "engine_version": "test",
                "scale": "10M",
                "class": "L",
                "template": "L1",
                "instance": "00",
                "query_sha256": "b" * 64,
                "base_endpoint_sha256": "1" * 64,
                "reified_endpoint_sha256": "2" * 64,
                "update_endpoint_sha256": "3" * 64,
                "base_data_identity_sha256": "4" * 64,
                "reified_data_identity_sha256": "5" * 64,
                "update_for": "reified",
                "access_mode": "writable",
                "base_data_name": "base-data",
                "reified_data_name": "reified-data",
                "update_canary_sha256": "9" * 64,
                "store_instance_sha256": "6" * 64,
                "store_discriminator_sha256": "7" * 64,
                "tool_sha256": "8" * 64,
                "java_runtime_sha256": "a" * 64,
                "run_identity_sha256": "c" * 64,
                "b_rows": "1",
                "r_rows": "1",
                "br_multiset_equal": "True",
                "br_kind": "term-aware-binding-multiset-v1",
                "br_b_fingerprint": "d" * 64,
                "br_r_fingerprint": "d" * 64,
                "n_candidates": "1",
                "c_candidates": "1",
                "n_answer_rows": "1",
                "c_answer_gates": "1",
                "nc_keys_equal": "True",
                "nc_kind": "term-aware-candidate-set-v1",
                "n_fingerprint": "e" * 64,
                "c_fingerprint": "e" * 64,
                "n_distinct": "1",
                "c_distinct": "1",
                "nc_count_equal": "True",
            }
        )
        return row

    def test_formal_profile_is_fixed_and_exploratory_overrides_are_explicit(self):
        names = (
            "PCM_WARMUPS", "PCM_RUNS", "PCM_TIMEOUT_S",
            "PCM_UPDATE_CHUNK_TRIPLES", "PCM_ORPHAN_CLEANUP_TIMEOUT_S",
        )

        def args(**changes):
            values = {
                "exploratory": False,
                "warmups": None,
                "runs": None,
                "timeout": None,
                "update_chunk_triples": None,
                "orphan_cleanup_timeout": None,
            }
            values.update(changes)
            return types.SimpleNamespace(**values)

        with mock.patch.dict(os.environ, {name: "" for name in names}, clear=False):
            for name in names:
                os.environ.pop(name, None)
            self.assertEqual(
                pcm.resolve_run_profile(args()),
                {
                    "warmups": 1,
                    "runs": 5,
                    "timeout_s": 300.0,
                    "update_chunk_triples": 1000,
                    "orphan_cleanup_timeout_s": 15.0,
                },
            )
            with self.assertRaises(ValueError):
                pcm.resolve_run_profile(args(warmups=2))
            exploratory = pcm.resolve_run_profile(
                args(
                    exploratory=True,
                    warmups=0,
                    runs=1,
                    timeout=2.0,
                    update_chunk_triples=7,
                    orphan_cleanup_timeout=0.5,
                )
            )
            self.assertEqual(exploratory["runs"], 1)
            self.assertEqual(exploratory["update_chunk_triples"], 7)

    def test_hidden_git_index_bits_fail_closed(self):
        normal = types.SimpleNamespace(returncode=0, stdout=b"H safe\0")
        hidden_assume = types.SimpleNamespace(returncode=0, stdout=b"h hidden\0")
        hidden_skip = types.SimpleNamespace(returncode=0, stdout=b"S skipped\0")
        with mock.patch.object(pcm.subprocess, "run", return_value=normal):
            pcm.validate_no_hidden_index_bits()
        for result in (hidden_assume, hidden_skip):
            with mock.patch.object(pcm.subprocess, "run", return_value=result):
                with self.assertRaises(RuntimeError):
                    pcm.validate_no_hidden_index_bits()

    def test_frozen_java_and_jar_execute_via_verified_descriptors(self):
        with tempfile.TemporaryDirectory() as directory:
            java = Path(directory, "java")
            jar = Path(directory, "tool.jar")
            java.write_bytes(b"java-runtime")
            jar.write_bytes(b"jar-bytes")
            snapshots = {
                pcm.FROZEN_JAVA_RUNTIME_NAME: pcm._snapshot_tool(
                    java, pcm.FROZEN_JAVA_RUNTIME_NAME
                ),
                pcm.FROZEN_TOOL_NAME: pcm._snapshot_tool(
                    jar, pcm.FROZEN_TOOL_NAME
                ),
            }
            completed = types.SimpleNamespace(returncode=0, stdout="ok", stderr="")
            with mock.patch.object(pcm, "_FORMAL_TOOL_SNAPSHOTS", snapshots), mock.patch.object(
                pcm.subprocess, "run", return_value=completed
            ) as run:
                self.assertIs(
                    pcm._run_java(["-jar", "{frozen-jar}"]), completed
                )
            command = run.call_args.args[0]
            options = run.call_args.kwargs
            self.assertEqual(command[0], str(java.resolve()))
            self.assertTrue(command[2].startswith("/proc/self/fd/"))
            self.assertTrue(options["executable"].startswith("/proc/self/fd/"))
            self.assertEqual(len(options["pass_fds"]), 2)

            alias = Path(directory, "java-hardlink")
            os.link(java, alias)
            with self.assertRaises(pcm.freeze_inputs.FreezeError):
                pcm._snapshot_tool(java, pcm.FROZEN_JAVA_RUNTIME_NAME)

        with tempfile.TemporaryDirectory() as directory:
            java = Path(directory, "java")
            jar = Path(directory, "tool.jar")
            java.write_bytes(b"java-runtime")
            jar.write_bytes(b"jar-before")
            snapshots = {
                pcm.FROZEN_JAVA_RUNTIME_NAME: pcm._snapshot_tool(
                    java, pcm.FROZEN_JAVA_RUNTIME_NAME
                ),
                pcm.FROZEN_TOOL_NAME: pcm._snapshot_tool(
                    jar, pcm.FROZEN_TOOL_NAME
                ),
            }

            def mutate(*_args, **_kwargs):
                jar.write_bytes(b"jar-after")
                return types.SimpleNamespace(returncode=0, stdout="ok", stderr="")

            with mock.patch.object(pcm, "_FORMAL_TOOL_SNAPSHOTS", snapshots), mock.patch.object(
                pcm.subprocess, "run", side_effect=mutate
            ), self.assertRaises(RuntimeError):
                pcm._run_java(["-jar", "{frozen-jar}"])

    def test_no_proxy_opener_and_single_link_endpoint_lock(self):
        self.assertIsInstance(pcm._NO_PROXY_HANDLER, pcm.U.ProxyHandler)
        self.assertEqual(pcm._NO_PROXY_HANDLER.proxies, {})
        identity = "f" * 64
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(
            pcm, "ENDPOINT_LOCK_DIRECTORY", directory
        ):
            lock = Path(directory, identity + ".lock")
            lock.write_text("lock", encoding="ascii")
            os.link(lock, Path(directory, "alias"))
            with self.assertRaises(ValueError):
                with pcm.endpoint_lock(identity):
                    pass
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory, "result.csv")
            lock = Path(str(output) + ".invocation.lock")
            lock.write_text("lock", encoding="ascii")
            os.link(lock, Path(directory, "invocation-lock-alias"))
            with self.assertRaises(ValueError):
                with pcm.invocation_file_lock(output):
                    pass
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(
            pcm, "ENDPOINT_LOCK_DIRECTORY", directory
        ):
            target = Path(directory, "target")
            target.write_text("lock", encoding="ascii")
            Path(directory, identity + ".lock").symlink_to(target)
            with self.assertRaises(ValueError):
                with pcm.endpoint_lock(identity):
                    pass

    def test_manifest_rows_are_consumed_from_validator_snapshot(self):
        record = _minimal_frozen_manifest("workload")
        with mock.patch.object(
            pcm.freeze_inputs, "validate_manifest", return_value=record
        ), mock.patch.object(
            pcm, "_read_stable_bytes", side_effect=AssertionError("reopened")
        ):
            rows = pcm.load_manifest(verify_files=True)
        self.assertEqual(rows[0]["query_sha256"], "3" * 64)

    def test_formal_manifest_coverage_requires_every_scale_class(self):
        args = types.SimpleNamespace(
            classes=",".join(pcm.FORMAL_CLASSES),
            frozen_document=None,
            exploratory=False,
        )
        with mock.patch.object(
            pcm,
            "load_manifest",
            return_value=[
                {
                    "scale": "10M",
                    "class": "L",
                    "template": "L1",
                    "instance": "00",
                    "query_sha256": "a" * 64,
                }
            ],
        ), self.assertRaises(RuntimeError):
            parity._expected_parity_cells(args, ["graphdb"], ["10M"])

    def test_parity_tail_repair_discards_only_the_incomplete_suffix(self):
        rendered = io.StringIO(newline="")
        writer = csv.DictWriter(rendered, fieldnames=parity.COLS)
        writer.writeheader()
        writer.writerow({name: "" for name in parity.COLS})
        complete = rendered.getvalue().encode("utf-8")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "parity.csv")
            path.write_bytes(complete + b'"torn')
            parity._repair_parity_tail(path)
            self.assertEqual(path.read_bytes(), complete)

            malformed = complete + b"one,two\n" + b'"torn'
            path.write_bytes(malformed)
            before = path.read_bytes()
            with self.assertRaises(ValueError):
                parity._repair_parity_tail(path)
            self.assertEqual(path.read_bytes(), before)

    def test_parity_completion_proves_exact_profile_coverage(self):
        row = {name: "" for name in parity.COLS}
        row.update(
            {
                "protocol": pcm.PROTOCOL,
                "commit": pcm.COMMIT,
                "batch_id": "a" * 64,
                "engine": "graphdb",
                "engine_version": "test",
                "scale": "10M",
                "class": "L",
                "template": "L1",
                "instance": "00",
                "query_sha256": "b" * 64,
                "base_endpoint_sha256": "1" * 64,
                "reified_endpoint_sha256": "2" * 64,
                "update_endpoint_sha256": "3" * 64,
                "base_data_identity_sha256": "4" * 64,
                "reified_data_identity_sha256": "5" * 64,
                "update_for": "reified",
                "access_mode": "writable",
                "base_data_name": "base-data",
                "reified_data_name": "reified-data",
                "update_canary_sha256": "9" * 64,
                "store_instance_sha256": "6" * 64,
                "store_discriminator_sha256": "7" * 64,
                "tool_sha256": "8" * 64,
                "java_runtime_sha256": "a" * 64,
                "run_identity_sha256": "c" * 64,
                "br_multiset_equal": True,
                "br_kind": "term-aware-binding-multiset-v1",
                "br_b_fingerprint": "d" * 64,
                "br_r_fingerprint": "d" * 64,
                "nc_keys_equal": True,
                "nc_kind": "term-aware-candidate-set-v1",
                "n_fingerprint": "e" * 64,
                "c_fingerprint": "e" * 64,
                "b_rows": 1,
                "r_rows": 1,
                "n_candidates": 1,
                "c_candidates": 1,
                "n_answer_rows": 1,
                "c_answer_gates": 1,
                "n_distinct": 1,
                "c_distinct": 1,
                "nc_count_equal": True,
            }
        )
        expected = {
            parity._parity_cell_key(row): {
                name: row[name] for name in pcm.IDENTITY_FIELDS
            }
        }
        args = types.SimpleNamespace(
            batch_id="a" * 64,
            classes=",".join(pcm.FORMAL_CLASSES),
            cap=parity.FORMAL_CAP,
            timeout=parity.FORMAL_TIMEOUT,
        )
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "parity.csv")
            parity._publish_parity_csv(path, [row])
            proof = parity.finalize_parity_completion(
                path, [row], expected, args, ["graphdb"], ["10M"]
            )
            payload, rows = parity._parity_snapshot(path)
            verified = pcm.verify_completion_sidecar(
                path,
                expected_schema="r9-parity-completion-v1",
                label="R9 parity completion sidecar",
                csv_payload=payload,
                csv_rows=len(rows),
            )
            self.assertEqual(verified, proof)

            extra = dict(row, engine="oxigraph", run_identity_sha256="f" * 64)
            parity._publish_parity_csv(path, [row, extra])
            with self.assertRaises((ValueError, RuntimeError)):
                parity.finalize_parity_completion(
                    path, [row, extra], expected, args, ["graphdb"], ["10M"]
                )

    def test_formal_parity_validator_rejects_identity_truth_fingerprint_and_count_forgery(self):
        row = self._strict_parity_row()
        key = parity._parity_cell_key(row)
        expected = {
            key: {name: row[name] for name in pcm.IDENTITY_FIELDS}
        }
        parity.validate_formal_parity_rows(
            [row], expected, commit=pcm.COMMIT, batch_id="a" * 64
        )
        attacks = (
            ("tool_sha256", "f" * 64),
            ("run_identity_sha256", "f" * 64),
            ("br_multiset_equal", "1"),
            ("nc_keys_equal", "ok"),
            ("nc_count_equal", "TRUE"),
            ("br_kind", "count-only"),
            ("br_r_fingerprint", "f" * 64),
            ("c_fingerprint", "not-a-sha"),
            ("b_rows", "+1"),
            ("n_candidates", "01"),
            ("c_candidates", "2"),
            ("n_distinct", "2"),
        )
        for field, value in attacks:
            forged = dict(row)
            forged[field] = value
            with self.subTest(field=field, value=value):
                with self.assertRaises(ValueError):
                    parity.validate_formal_parity_rows(
                        [forged],
                        expected,
                        commit=pcm.COMMIT,
                        batch_id="a" * 64,
                    )

    def test_expected_parity_identity_is_built_from_the_bound_registry(self):
        endpoints = {
            "base_endpoint_sha256": "1" * 64,
            "reified_endpoint_sha256": "2" * 64,
            "update_endpoint_sha256": "3" * 64,
            "base_data_identity_sha256": "4" * 64,
            "reified_data_identity_sha256": "5" * 64,
            "update_for": "reified",
            "access_mode": "writable",
            "base_data_name": "base-data",
            "reified_data_name": "reified-data",
            "update_canary_sha256": "6" * 64,
            "store_instance_sha256": "7" * 64,
            "store_discriminator_sha256": "8" * 64,
            "engine_version": "frozen-engine",
        }
        config = {
            "version": "ignored-runtime-label",
            "tool_sha256": "9" * 64,
            "java_runtime_sha256": "a" * 64,
            "10M": endpoints,
        }
        manifest = [
            {
                "scale": "10M",
                "class": "L",
                "template": "L1",
                "instance": "00",
                "query_sha256": "b" * 64,
            }
        ]
        args = types.SimpleNamespace(
            classes="L",
            frozen_document=object(),
            exploratory=True,
            registry={"graphdb": config},
            batch_id="c" * 64,
        )
        with mock.patch.object(pcm, "load_manifest", return_value=manifest):
            observed = parity._expected_parity_identities(
                args, ["graphdb"], ["10M"]
            )
        identity = next(iter(observed.values()))
        self.assertEqual(identity["engine_version"], "frozen-engine")
        self.assertEqual(identity["tool_sha256"], "9" * 64)
        self.assertEqual(identity["java_runtime_sha256"], "a" * 64)
        self.assertEqual(identity["base_data_identity_sha256"], "4" * 64)

    def test_formal_completion_pair_is_reconsumed_end_to_end(self):
        row_factory = CheckpointTests()
        cache_identity_names = (
            "commit", "batch_id", "protocol", "query_sha256", "engine",
            "engine_version", "scale", "class", "template", "instance",
            "base_endpoint_sha256", "reified_endpoint_sha256",
            "update_endpoint_sha256", "base_data_identity_sha256",
            "reified_data_identity_sha256", "update_for", "access_mode",
            "base_data_name", "reified_data_name", "update_canary_sha256",
            "store_instance_sha256", "store_discriminator_sha256", "tool_sha256",
            "java_runtime_sha256", "run_identity_sha256",
            "construction_requested", "construction_effective",
        )
        with tempfile.TemporaryDirectory() as directory:
            timing_path = os.path.join(directory, "timing.csv")
            parity_path = os.path.join(directory, "parity.csv")
            cache_dir = os.path.join(directory, "cache")
            timing_rows = []
            expected_timing = {}
            b_rows = {}
            for cls in pcm.FORMAL_CLASSES:
                query_sha = hashlib.sha256(cls.encode("ascii")).hexdigest()
                for method in pcm.FORMAL_METHODS:
                    row = row_factory._row(method=method, samples=[1.0] * 5)
                    row.update(
                        {
                            "class": cls,
                            "template": cls + "1",
                            "query_sha256": query_sha,
                            "warmups": "1",
                            "runs": "5",
                            "timeout_s": "300.0",
                        }
                    )
                    metadata = pcm.unpack_note(row)
                    if method == "C":
                        row.update(
                            {
                                "construction_requested": "factored",
                                "construction_effective": "factored",
                                "c_parse_median_ms": "2.0",
                                "c_protocol_median_ms": "3.0",
                                "construct_total_ms": "7.0",
                                "construct_unattributed_median_ms": "1.0",
                            }
                        )
                        stored = circuit_cache.store(
                            cache_dir,
                            [f"<urn:g:{cls}> <urn:p> <urn:o> ."],
                            {name: row[name] for name in cache_identity_names},
                        )
                        row.update(
                            {
                                "circuit_sha256": stored["circuit_sha256"],
                                "circuit_cache_path": stored["circuit_path"],
                                "circuit_cache_metadata_path": stored[
                                    "metadata_path"
                                ],
                                "circuit_cache_observation_sha256": stored[
                                    "producer_observation_sha256"
                                ],
                                "circuit_cache_sidecar_sha256": stored[
                                    "sidecar_sha256"
                                ],
                            }
                        )
                        metadata.update(
                            {
                                "construction_requested": "factored",
                                "construction_effective": "factored",
                                "circuit_sha256": stored["circuit_sha256"],
                                "c_parse_samples": [2.0] * 5,
                                "c_protocol_samples": [3.0] * 5,
                                "construct_total_samples": [7.0] * 5,
                                "construct_unattributed_samples": [1.0] * 5,
                            }
                        )
                    row["notes"] = pcm.pack_note(
                        "ok", metadata, cell_wall_ms=20.0
                    )
                    timing_rows.append(row)
                    expected_timing[pcm._row_key(row)] = (
                        row_factory._expected_identity(row)
                    )
                    if method == "B":
                        b_rows[cls] = row

            with open(timing_path, "w", newline="") as fh:
                writer = csv.DictWriter(fh, fieldnames=pcm.COLS)
                writer.writeheader()
                writer.writerows(timing_rows)
            pcm.finalize_timing_completion(
                timing_path,
                expected_identities=expected_timing,
                commit=pcm.COMMIT,
                batch_id="b" * 64,
                profile={
                    "warmups": 1,
                    "runs": 5,
                    "timeout_s": 300.0,
                    "update_chunk_triples": 1000,
                    "orphan_cleanup_timeout_s": 15.0,
                },
                engines=("graphdb",),
                scales=("10M",),
                methods=pcm.FORMAL_METHODS,
                classes=pcm.FORMAL_CLASSES,
                require_circuit_cache=True,
            )

            parity_rows = []
            for cls, timing_row in b_rows.items():
                row = {name: "" for name in parity.COLS}
                for name in (
                    "protocol", "commit", "batch_id", "engine", "engine_version",
                    "scale", "class", "template", "instance", "query_sha256",
                    "base_endpoint_sha256", "reified_endpoint_sha256",
                    "update_endpoint_sha256", "base_data_identity_sha256",
                    "reified_data_identity_sha256", "update_for", "access_mode",
                    "base_data_name", "reified_data_name", "update_canary_sha256",
                    "store_instance_sha256", "store_discriminator_sha256",
                    "tool_sha256", "java_runtime_sha256", "run_identity_sha256",
                ):
                    row[name] = timing_row[name]
                row.update(
                    {
                        "br_multiset_equal": True,
                        "br_kind": "term-aware-binding-multiset-v1",
                        "br_b_fingerprint": "d" * 64,
                        "br_r_fingerprint": "d" * 64,
                        "b_rows": 1,
                        "r_rows": 1,
                        "nc_keys_equal": True,
                        "nc_kind": "term-aware-candidate-set-v1",
                        "n_fingerprint": "e" * 64,
                        "c_fingerprint": "e" * 64,
                        "n_candidates": 1,
                        "c_candidates": 1,
                        "n_answer_rows": 1,
                        "c_answer_gates": 1,
                        "n_distinct": 1,
                        "c_distinct": 1,
                        "nc_count_equal": True,
                    }
                )
                parity_rows.append(row)
            parity._publish_parity_csv(parity_path, parity_rows)
            parity.finalize_parity_completion(
                parity_path,
                parity_rows,
                {
                    parity._parity_cell_key(row): {
                        name: row[name] for name in pcm.IDENTITY_FIELDS
                    }
                    for row in parity_rows
                },
                types.SimpleNamespace(
                    batch_id="b" * 64,
                    classes=",".join(pcm.FORMAL_CLASSES),
                    cap=parity.FORMAL_CAP,
                    timeout=parity.FORMAL_TIMEOUT,
                ),
                ["graphdb"],
                ["10M"],
            )
            timing_proof, observed_timing, observed_parity = (
                summarize_brnc._validate_completion_pair(
                    timing_path, parity_path
                )
            )
            self.assertEqual(timing_proof["expected_cells"], 24)
            self.assertEqual(len(observed_timing), 24)
            self.assertEqual(len(observed_parity), 6)
            summary_path = os.path.join(directory, "summary.csv")
            rendered, failures, unverified = summarize_brnc.summarize(
                timing_path,
                parity_path,
                summary_path,
                stream=io.StringIO(),
            )
            self.assertEqual(len(rendered), 6)
            self.assertEqual(failures, [])
            self.assertEqual(unverified, [])
            self.assertTrue(Path(summary_path).is_file())

    def test_frozen_batch_rejects_forgery_short_commit_profile_and_canary(self):
        freeze = pcm.freeze_inputs
        document = _formal_frozen_document()
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "frozen.json")

            def load(value):
                Path(path).write_text(
                    json.dumps(value, sort_keys=True), encoding="utf-8"
                )
                return freeze.load_frozen_batch(
                    path,
                    expected_commit=pcm.COMMIT,
                    expected_protocol=pcm.PROTOCOL,
                    required_data=("base-data", "reified-data"),
                    required_tools=(
                        pcm.FROZEN_TOOL_NAME,
                        pcm.FROZEN_JAVA_RUNTIME_NAME,
                    ),
                    required_stores=(("graphdb", "10M"),),
                    require_formal=True,
                )

            self.assertEqual(load(document)["batch_id"], document["batch_id"])
            forged = json.loads(json.dumps(document))
            forged["batch_id"] = "0" * 64
            with self.assertRaises(freeze.FreezeError):
                load(forged)
            short = json.loads(json.dumps(document))
            short["identity"]["git"]["commit"] = "deadbee"
            short["batch_id"] = freeze.canonical_batch_id(short["identity"])
            with self.assertRaises(freeze.FreezeError):
                load(short)
            exploratory = json.loads(json.dumps(document))
            exploratory["identity"]["batch_profile"] = "exploratory"
            exploratory["batch_id"] = freeze.canonical_batch_id(
                exploratory["identity"]
            )
            with self.assertRaises(freeze.FreezeError):
                load(exploratory)
            bad_canary = json.loads(json.dumps(document))
            bad_canary["identity"]["stores"][0]["update_canary"][
                "delete_invisible"
            ] = False
            bad_canary["batch_id"] = freeze.canonical_batch_id(
                bad_canary["identity"]
            )
            with self.assertRaises(freeze.FreezeError):
                load(bad_canary)

    def test_frozen_registry_uses_canonical_store_data_access_and_canary(self):
        document = _formal_frozen_document()
        runtime = {
            "graphdb": {
                "version": "pseudo-default-must-not-survive",
                "profile": {},
                "read_only": True,
                "10M": {
                    "base": "HTTP://LOCALHOST:7200/base",
                    "reified": "http://localhost:7200/reified",
                    "update": "http://localhost:7200/reified/statements",
                    "base_data_identity": "pseudo-base",
                    "reified_data_identity": "pseudo-reified",
                    "update_for": "reified",
                },
            }
        }
        with mock.patch.object(
            pcm.freeze_inputs,
            "hash_file",
            return_value={"bytes": 3, "sha256": "9" * 64},
        ):
            bound = pcm.bind_frozen_registry(
                document, ["graphdb"], ["10M"], ["C"], registry=runtime
            )
        config, endpoints = bound["graphdb"], bound["graphdb"]["10M"]
        self.assertEqual(config["version"], "10.7.6")
        self.assertEqual(endpoints["access_mode"], "writable")
        self.assertFalse(endpoints["read_only"])
        self.assertEqual(endpoints["base_data_name"], "base-data")
        self.assertEqual(endpoints["reified_data_name"], "reified-data")
        self.assertNotEqual(
            endpoints["base_data_identity_sha256"],
            pcm.identity_sha256("pseudo-base"),
        )
        self.assertRegex(endpoints["update_canary_sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(config["tool_sha256"], "9" * 64)

        mismatched = json.loads(json.dumps(runtime))
        mismatched["graphdb"]["10M"]["reified"] = (
            "http://localhost:7200/wrong"
        )
        with mock.patch.object(
            pcm.freeze_inputs,
            "hash_file",
            return_value={"bytes": 3, "sha256": "9" * 64},
        ), self.assertRaises(pcm.freeze_inputs.FreezeError):
            pcm.bind_frozen_registry(
                document, ["graphdb"], ["10M"], ["C"], registry=mismatched
            )

    def test_formal_context_requires_frozen_input_path(self):
        with self.assertRaises(pcm.freeze_inputs.FreezeError):
            pcm.load_formal_context(
                ["graphdb"],
                ["10M"],
                ["C"],
                environ={"PCM_BATCH_ID": "a" * 64},
            )
        with self.assertRaisesRegex(
            pcm.freeze_inputs.FreezeError, "PCM_JAVA_BIN"
        ):
            pcm.load_formal_context(
                ["graphdb"],
                ["10M"],
                ["C"],
                environ={
                    "PCM_BATCH_ID": "a" * 64,
                    "PCM_FROZEN_INPUTS": "/does/not/matter-before-java-gate",
                },
            )

    def test_http_4xx_classification_is_retryable_except_real_feature_errors(self):
        for code in (401, 403, 404, 409, 413, 429):
            result = pcm._failure_result(
                pcm.PostFailure("http", f"HTTP {code}: timeout unsupported memory")
            )
            self.assertEqual(result["status"], "err:http", code)
        self.assertEqual(
            pcm._failure_result(
                pcm.PostFailure("http", "HTTP 408: request timeout")
            )["status"],
            "err:http",
        )
        self.assertEqual(
            pcm._failure_result(
                pcm.PostFailure("http", "HTTP 400: feature not supported")
            )["status"],
            "unsupported",
        )
        self.assertEqual(
            pcm._failure_result(
                pcm.PostFailure("http", "HTTP 400: malformed generated query")
            )["status"],
            "err:http",
        )
        self.assertEqual(
            pcm._failure_result(
                pcm.PostFailure("http", "HTTP 415: unsupported media type")
            )["status"],
            "err:http",
        )
        self.assertEqual(
            pcm._failure_result(
                pcm.PostFailure(
                    "http", "HTTP 503: timeout unsupported out of memory"
                )
            )["status"],
            "err:http",
        )

    def test_circuitrun_requires_strict_construction_marker(self):
        stderr = "# --- step 1 ---\nCONSTRUCT { ?s ?p ?o } WHERE { ?s ?p ?o }\n"
        completed = types.SimpleNamespace(returncode=0, stderr=stderr, stdout="")
        with mock.patch.object(pcm.subprocess, "run", return_value=completed):
            with self.assertRaises(pcm.ConstructionProtocolError):
                pcm.c_construct_plan("SELECT * WHERE {}", construction="factored")

        completed.stderr = (
            "construction mode: requested=factored, effective=flat\n" + stderr
        )
        with mock.patch.object(pcm.subprocess, "run", return_value=completed):
            with self.assertRaises(pcm.ConstructionProtocolError):
                pcm.c_construct_plan("SELECT * WHERE {}", construction="factored")

    def test_endpoint_lock_canonicalizes_aliases_and_accepts_store_identity(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(
            pcm, "ENDPOINT_LOCK_DIRECTORY", directory
        ):
            with pcm.endpoint_lock("HTTP://LOCALHOST:80/repo") as first:
                pass
            with pcm.endpoint_lock("http://localhost/repo") as second:
                pass
            self.assertEqual(first["lock_path"], second["lock_path"])
            identity = "f" * 64
            with pcm.endpoint_lock(identity) as frozen:
                pass
            self.assertTrue(frozen["lock_path"].endswith(identity + ".lock"))

    def test_endpoint_lock_namespace_ignores_per_process_environment_override(self):
        identity = "f" * 64
        with tempfile.TemporaryDirectory() as directory:
            fixed = os.path.join(directory, "fixed")
            first_override = os.path.join(directory, "first")
            second_override = os.path.join(directory, "second")
            with mock.patch.object(
                pcm, "ENDPOINT_LOCK_DIRECTORY", fixed
            ), mock.patch.dict(
                os.environ, {"PCM_ENDPOINT_LOCK_DIR": first_override}
            ):
                with pcm.endpoint_lock(identity) as first:
                    pass
            with mock.patch.object(
                pcm, "ENDPOINT_LOCK_DIRECTORY", fixed
            ), mock.patch.dict(
                os.environ, {"PCM_ENDPOINT_LOCK_DIR": second_override}
            ):
                with pcm.endpoint_lock(identity) as second:
                    pass
            self.assertEqual(first["lock_path"], second["lock_path"])
            self.assertTrue(first["lock_path"].startswith(fixed + os.sep))
            self.assertFalse(os.path.exists(first_override))
            self.assertFalse(os.path.exists(second_override))

    @unittest.skipUnless(
        "fork" in multiprocessing.get_all_start_methods(),
        "concurrency regression requires fork",
    )
    def test_invocation_file_lock_serializes_two_processes(self):
        ctx = multiprocessing.get_context("fork")
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "counter")
            workers = [ctx.Process(target=_locked_increment, args=(path,)) for _ in range(2)]
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join(5)
                self.assertEqual(worker.exitcode, 0)
            self.assertEqual(Path(path).read_text(encoding="ascii"), "2")

    @unittest.skipUnless(
        "fork" in multiprocessing.get_all_start_methods(),
        "concurrency regression requires fork",
    )
    def test_parity_merge_lock_preserves_two_processes(self):
        ctx = multiprocessing.get_context("fork")
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "parity.csv")
            workers = [
                ctx.Process(target=_parity_merge_worker, args=(path, engine))
                for engine in ("one", "two")
            ]
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join(5)
                self.assertEqual(worker.exitcode, 0)
            with open(path, newline="") as fh:
                rows = list(csv.DictReader(fh))
            self.assertEqual({row["engine"] for row in rows}, {"one", "two"})

    def test_full_commit_and_formal_output_ignore_gate(self):
        self.assertRegex(pcm.COMMIT, r"^[0-9a-f]{40}$")
        with self.assertRaises(ValueError):
            pcm._prepare_artifact_path(
                os.path.join("reference", "paper", "formal-unignored.csv"),
                exploratory=False,
            )
        with tempfile.TemporaryDirectory() as directory:
            external = pcm._prepare_artifact_path(
                os.path.join(directory, "formal.csv"), exploratory=False
            )
            self.assertTrue(external.startswith(directory))
        accepted = pcm._prepare_artifact_path(
            os.path.join("artifacts", "r9", "test-ignore", "formal.csv")
        )
        self.assertIn(os.path.join("artifacts", "r9"), accepted)

    def test_clean_git_identity_is_full_clean_and_stable(self):
        ok = types.SimpleNamespace(returncode=0, stdout=pcm.COMMIT + "\n")
        clean = types.SimpleNamespace(returncode=0, stdout="")
        with mock.patch.object(pcm, "_git", side_effect=[ok, clean, ok]):
            self.assertEqual(pcm.clean_git_identity(), pcm.COMMIT)
        short = types.SimpleNamespace(returncode=0, stdout="deadbee\n")
        with mock.patch.object(pcm, "_git", side_effect=[short]):
            with self.assertRaises(RuntimeError):
                pcm.clean_git_identity()
        changed = types.SimpleNamespace(returncode=0, stdout="f" * 40 + "\n")
        with mock.patch.object(pcm, "_git", side_effect=[ok, clean, changed]):
            with self.assertRaises(RuntimeError):
                pcm.clean_git_identity()

    def test_endpoint_registration_rejects_aliases_and_wrong_update_role(self):
        config = {"read_only": False}
        valid = {
            "base": "http://example.invalid/base",
            "reified": "http://example.invalid/reified",
            "update": "http://example.invalid/reified/update",
            "base_data_identity": "base-v1",
            "reified_data_identity": "reified-v1",
            "update_for": "reified",
        }
        pcm.validate_endpoint_registration(config, valid, require_update=True)
        alias = dict(valid, reified=valid["base"] + "/")
        with self.assertRaises(ValueError):
            pcm.validate_endpoint_registration(config, alias, require_update=True)
        wrong = dict(valid, update_for="base")
        with self.assertRaises(ValueError):
            pcm.validate_endpoint_registration(config, wrong, require_update=True)
        missing = dict(valid, update=None)
        with self.assertRaises(ValueError):
            pcm.validate_endpoint_registration(config, missing, require_update=True)

    def test_endpoint_lock_times_out_instead_of_sharing_workspace(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(
            pcm, "ENDPOINT_LOCK_DIRECTORY", directory
        ):
            with pcm.endpoint_lock("http://example.invalid/update", timeout=1):
                with self.assertRaises(pcm.EndpointLockTimeout):
                    with pcm.endpoint_lock(
                        "http://example.invalid/update", timeout=0.05
                    ):
                        pass

    def test_four_engines_have_nonempty_defaults_and_env_overrides(self):
        registry = pcm.build_engine_registry({})
        self.assertEqual(
            set(registry), {"graphdb", "oxigraph", "qlever", "millenniumdb"}
        )
        for engine in registry.values():
            for scale in ("10M", "100M"):
                self.assertTrue(engine[scale]["base"].startswith("http://localhost:"))
                self.assertTrue(engine[scale]["reified"].startswith("http://localhost:"))
                self.assertNotEqual(engine[scale]["base"], engine[scale]["reified"])
                if not engine["read_only"]:
                    self.assertTrue(
                        engine[scale]["update"].startswith("http://localhost:")
                    )
                else:
                    self.assertIsNone(engine[scale]["update"])
        override = pcm.build_engine_registry(
            {
                "PCM_QLEVER_100M_BASE_ENDPOINT": "http://example.invalid/base",
                "PCM_QLEVER_100M_UPDATE_ENDPOINT": "http://example.invalid/update",
            }
        )
        self.assertEqual(
            override["qlever"]["100M"]["base"], "http://example.invalid/base"
        )
        self.assertEqual(
            override["qlever"]["100M"]["update"],
            "http://example.invalid/update",
        )

    def test_parity_merge_retains_other_engine_scale_combinations(self):
        def row(engine, scale, note):
            result = {name: "" for name in parity.COLS}
            result.update(
                {
                    "protocol": pcm.PROTOCOL,
                    "commit": pcm.COMMIT,
                    "batch_id": "a" * 64,
                    "engine": engine,
                    "engine_version": "test",
                    "scale": scale,
                    "class": "L",
                    "template": "L1",
                    "instance": "00",
                    "query_sha256": "b" * 64,
                    "base_endpoint_sha256": "1" * 64,
                    "reified_endpoint_sha256": "2" * 64,
                    "update_endpoint_sha256": "3" * 64,
                    "base_data_identity_sha256": "4" * 64,
                    "reified_data_identity_sha256": "5" * 64,
                    "update_for": "reified",
                    "access_mode": "writable",
                    "base_data_name": "base-data",
                    "reified_data_name": "reified-data",
                    "update_canary_sha256": "9" * 64,
                    "store_instance_sha256": "6" * 64,
                    "store_discriminator_sha256": "7" * 64,
                    "tool_sha256": "8" * 64,
                    "java_runtime_sha256": "a" * 64,
                    "run_identity_sha256": hashlib.sha256(
                        f"{engine}:{scale}".encode()
                    ).hexdigest(),
                    "br_multiset_equal": True,
                    "nc_keys_equal": True,
                    "notes": note,
                }
            )
            return result

        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "parity.csv")
            parity.merge_parity_rows(
                path,
                [row("graphdb", "10M", "g"), row("oxigraph", "100M", "o")],
            )
            parity.merge_parity_rows(
                path,
                [row("qlever", "10M", "q"), row("graphdb", "10M", "g-new")],
            )
            changed = row("graphdb", "10M", "g-new-query")
            changed["query_sha256"] = "c" * 64
            changed["run_identity_sha256"] = "d" * 64
            parity.merge_parity_rows(path, [changed])
            with open(path, newline="") as fh:
                rows = list(csv.DictReader(fh))
            self.assertEqual(len(rows), 3)
            by_key = {(r["engine"], r["scale"]): r for r in rows}
            self.assertEqual(
                by_key[("graphdb", "10M")]["notes"], "g-new-query"
            )
            self.assertEqual(
                by_key[("graphdb", "10M")]["query_sha256"], "c" * 64
            )
            self.assertIn(("oxigraph", "100M"), by_key)
            self.assertIn(("qlever", "10M"), by_key)


if __name__ == "__main__":
    unittest.main()
