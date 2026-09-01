#!/usr/bin/env python3
"""Offline and loopback regressions for the WatDiv single-cell runner."""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import signal
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock


HERE = Path(__file__).resolve().parent
REFERENCE = HERE.parent
sys.path.insert(0, str(REFERENCE))
sys.path.insert(0, str(HERE))

import watdiv10m_runner as runner


RESULT = json.dumps({
    "head": {"vars": ["x"]},
    "results": {
        "bindings": [
            {"x": {"type": "uri", "value": "urn:value:a"}},
            {"x": {"type": "uri", "value": "urn:value:a"}},
            {
                "x": {
                    "type": "literal",
                    "value": "chat",
                    "xml:lang": "EN",
                }
            },
        ]
    },
}, separators=(",", ":")).encode("utf-8")

TSV_RESULT = (
    "?x\n"
    "<urn:value:a>\n"
    "<urn:value:a>\n"
    '"chat"@EN\n'
).encode("utf-8")

NPCS_TSV_RESULT = (
    "?x\t?finalprovennacevariable\n"
    '<urn:answer:a>\t"⊕((⊗urn:t:1,urn:t:2,))"\n'
    '<urn:answer:b>\t"⊕((⊗urn:t:2,))"\n'
).encode("utf-8")

NPCS_GRAPHDB_TSV_RESULT = (
    "?x\t?finalprovennacevariable\n"
    "<urn:answer:a>\t⊕((⊗urn:t:1,urn:t:2,))\n"
).encode("utf-8")

GRAPHDB_BARE_ANSWER = "Reit   Schöllnach"
GRAPHDB_BARE_ANSWER_TSV_RESULT = (
    "?x\n" + GRAPHDB_BARE_ANSWER + "\n"
).encode("utf-8")
NPCS_GRAPHDB_BARE_ANSWER_TSV_RESULT = NPCS_GRAPHDB_TSV_RESULT.replace(
    b"<urn:answer:a>", GRAPHDB_BARE_ANSWER.encode("utf-8")
)


class _Endpoint:
    def __init__(
        self,
        payload: bytes = RESULT,
        delay_s: float = 0.0,
        chunk_delay_s: float = 0.0,
    ):
        state = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                self.rfile.read(length)
                state.requests += 1
                if state.delay_s:
                    time.sleep(state.delay_s)
                try:
                    self.send_response(200)
                    self.send_header("Content-Type", runner.JSON_RESULTS)
                    self.send_header("Content-Length", str(len(state.payload)))
                    self.end_headers()
                    split = 1 if state.chunk_delay_s else max(1, len(state.payload) // 2)
                    self.wfile.write(state.payload[:split])
                    self.wfile.flush()
                    if state.chunk_delay_s:
                        time.sleep(state.chunk_delay_s)
                    self.wfile.write(state.payload[split:])
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def log_message(self, _format, *_args):
                pass

        self.payload = payload
        self.delay_s = delay_s
        self.chunk_delay_s = chunk_delay_s
        self.requests = 0
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        host, port = self.server.server_address
        return "http://%s:%d/query" % (host, port)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_exc):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


def _config(query: Path, endpoint: str, method: str = "B"):
    return {
        "schema": runner.SCHEMA,
        "query": str(query),
        "query_id": "L1-00",
        "engine": "loopback-test",
        "method": method,
        "scheme": "SPARQL_Star",
        "base_endpoint": endpoint,
        "reified_endpoint": endpoint,
        "update_endpoint": None,
        "c_endpoint_protocol": "sparql",
        "jar": None,
        "java": "java",
        "reified_data": None,
        "warmups": 1,
        "runs": 2,
        "endpoint_timeout_s": 5.0,
        "offline_timeout_s": 5.0,
        "complete_method_timeout_s": None,
        "stop_after_warmup_offline_failure": False,
        "pqe_backend": "none",
        "npcs_postprocess_mode": "shared",
        "probabilities": None,
        "uniform_probability": None,
        "token_regex": runner.DEFAULT_TOKEN_REGEX,
        "c_parallelism": 1,
        "c_read_only": False,
        "skip_bnode_check": False,
    }


def _forbidden_result_key(value):
    if isinstance(value, dict):
        for key, child in value.items():
            lowered = str(key).lower()
            if "checksum" in lowered or "digest" in lowered or lowered.endswith(
                ("_sha", "_sha1", "_sha256", "_sha512")
            ):
                return str(key)
            found = _forbidden_result_key(child)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _forbidden_result_key(child)
            if found:
                return found
    return None


class RunnerUnitTest(unittest.TestCase):
    def test_c_path_has_a_valid_circuitrun_construction_option(self):
        self.assertEqual(
            "factorised", runner._circuit_construction_mode("C-path")
        )

    def test_only_endpoint_writing_c_methods_request_cleanup(self):
        self.assertFalse(runner._circuit_requires_cleanup("C-flat"))
        self.assertTrue(runner._circuit_requires_cleanup("C-factorised"))
        self.assertTrue(runner._circuit_requires_cleanup("C-path"))

    def test_factorised_accepts_native_rdf4j_protocol_without_update_url(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            query = root / "query.rq"
            query.write_text("SELECT * WHERE { ?s ?p ?o }", encoding="utf-8")
            jar = root / "runner.jar"
            jar.write_bytes(b"jar")
            data = root / "data.ttl"
            data.write_text("", encoding="utf-8")
            config = _config(query, "http://localhost/repositories/test", "C-factorised")
            config.update(
                jar=str(jar),
                reified_data=str(data),
                c_endpoint_protocol="rdf4j",
            )
            runner._validate_config(config)

    def test_factorised_generic_sparql_requires_update_url(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            query = root / "query.rq"
            query.write_text("SELECT * WHERE { ?s ?p ?o }", encoding="utf-8")
            jar = root / "runner.jar"
            jar.write_bytes(b"jar")
            data = root / "data.ttl"
            data.write_text("", encoding="utf-8")
            config = _config(query, "http://localhost/query", "C-factorised")
            config.update(jar=str(jar), reified_data=str(data))
            with self.assertRaisesRegex(runner.RunnerError, "requires --update-endpoint"):
                runner._validate_config(config)

    def test_c_stage_parser_rejects_explicit_cleanup_failure(self):
        events = [
            "query_read", "plan_generation", "repository_init", "data_ready",
            "construct_step", "workspace_cleanup", "normalization",
            "construction_complete", "serialization", "named_graph_persist",
            "endpoint_cleanup", "run_complete",
        ]
        records = []
        for event in events:
            record = {
                "schema": runner.C_STAGE_SCHEMA,
                "event": event,
                "duration_ms": 0.1,
            }
            if event == "endpoint_cleanup":
                record["success"] = False
            records.append(runner.C_STAGE_PREFIX + json.dumps(record))

        with self.assertRaises(runner.StageError) as raised:
            runner._parse_c_stage_records("\n".join(records), "C-flat", 1, 0)

        self.assertEqual("c-cleanup-error", raised.exception.status)
        self.assertEqual(
            ["endpoint_cleanup"], raised.exception.fields["cleanup_events"]
        )

    def test_cleanup_failure_has_stable_normalized_cause(self):
        failure = runner._normalized_failure({
            "run_id": "measured-01",
            "phase": "measured",
            "status": "c-cleanup-error",
            "endpoint": {
                "status": "c-cleanup-error",
                "detail": "endpoint cleanup failed",
            },
            "offline": None,
        })

        self.assertIsNotNone(failure)
        self.assertEqual("STORE_CLEANUP_ERROR", failure["cause"])
        self.assertEqual("endpoint", failure["stage"])

    def test_cli_defaults_match_formal_protocol(self):
        args = runner._run_parser().parse_args([
            "--query", "query.rq",
            "--query-id", "L1-00",
            "--engine", "test",
            "--method", "B",
            "--base-endpoint", "http://127.0.0.1:3030/query",
            "--out", "cell",
        ])
        self.assertEqual("SPARQL_Star", args.scheme)
        self.assertEqual(1, args.warmups)
        self.assertEqual(5, args.runs)
        self.assertEqual("median", args.primary_statistic)
        self.assertEqual(600.0, args.endpoint_timeout)
        self.assertEqual(600.0, args.offline_timeout)
        self.assertIsNone(args.complete_method_timeout)
        self.assertFalse(args.stop_after_warmup_offline_failure)
        self.assertEqual("shared", args.npcs_postprocess_mode)

    def test_complete_method_deadline_is_shared_with_offline_work(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            query = root / "query.rq"
            query.write_text(
                "SELECT ?x WHERE { ?x <urn:p> <urn:o> }\n", encoding="utf-8"
            )
            config = _config(query, "http://unused.invalid/query")
            config.update({
                "warmups": 1,
                "runs": 0,
                "endpoint_timeout_s": 20.0,
                "offline_timeout_s": 30.0,
                "complete_method_timeout_s": 10.0,
            })
            endpoint = {
                "schema": runner.ENDPOINT_SCHEMA,
                "status": "ok",
                "endpoint": {"endpoint_e2e_ms": 4000.0},
            }
            offline = {
                "schema": runner.OFFLINE_SCHEMA,
                "status": "offline-timeout",
                "offline_timeout_s": 6.0,
                "detail": "fixture timeout",
            }
            observed = {}

            def fake_endpoint(execution, config_path, _run_dir):
                observed["endpoint_timeout_s"] = execution["endpoint_timeout_s"]
                observed["execution_config"] = json.loads(
                    config_path.read_text(encoding="utf-8")
                )
                return endpoint

            def fake_offline(execution, _run_dir, _run_id):
                observed["offline_timeout_s"] = execution["offline_timeout_s"]
                return offline

            with mock.patch.object(
                runner, "_run_endpoint_worker", side_effect=fake_endpoint
            ), mock.patch.object(
                runner, "_run_offline", side_effect=fake_offline
            ), mock.patch.object(
                runner.time, "perf_counter", side_effect=(100.0, 104.0, 105.0)
            ):
                result = runner._run_cell(config, root / "cell")

        self.assertEqual(10.0, observed["endpoint_timeout_s"])
        self.assertEqual(10.0, observed["execution_config"]["endpoint_timeout_s"])
        self.assertEqual(6.0, observed["offline_timeout_s"])
        self.assertEqual(
            10.0,
            result["protocol"]["complete_method_timeout_s_per_execution"],
        )

    def test_per_answer_ablation_requires_npcs_but_not_pqe(self):
        base = [
            "--query", "query.rq",
            "--query-id", "L1-00",
            "--engine", "test",
            "--base-endpoint", "http://127.0.0.1:3030/query",
            "--out", "cell",
            "--npcs-postprocess-mode", "per-answer",
        ]
        parser = runner._run_parser()
        with self.assertRaisesRegex(ValueError, "only valid for method N"):
            runner._configuration(parser.parse_args(base + ["--method", "B"]))
        config = runner._configuration(parser.parse_args(base + ["--method", "N"]))
        self.assertEqual("none", config["pqe_backend"])
        self.assertEqual("per-answer", config["npcs_postprocess_mode"])

    def test_per_answer_method_time_uses_complete_offline_wall(self):
        endpoint = {"endpoint": {"endpoint_e2e_ms": 7.0}}
        offline = {"metrics": {
            "postprocess_mode": "per-answer",
            "timing_scope": "offline_from_complete_response_file",
            "offline_wall_ms": 13.0,
            "compiler": {"pqe_total_ms": 5.0},
        }}
        self.assertEqual(20.0, runner._component_method_e2e(endpoint, offline))

    def test_term_aware_response_records_keep_bag_multiplicity(self):
        records, metrics = runner._canonical_response_records(RESULT)
        self.assertEqual(3, metrics["solution_rows"])
        self.assertEqual(2, metrics["distinct_binding_count"])
        self.assertEqual([1, 2], sorted(item["multiplicity"] for item in records))
        literal = next(item for item in records if item["multiplicity"] == 1)
        self.assertEqual(
            ["literal", "chat", runner.circuit_io.RDF_LANGSTRING, "en"],
            literal["binding"][0][1],
        )

    def test_factorised_protocol_accepts_declared_flat_plan(self):
        text = """# ---- construction mode: requested=factorised, effective=flat ----
# ---- explicit fallback: an unanchored BGP would materialize global base relations; using the flat plan ----
# construction_ms: 12
"""
        # Mode validation succeeds; the deliberately abbreviated fixture then fails at the next,
        # independent protocol gate because it contains no structured timing records.
        with self.assertRaisesRegex(runner.StageError, "structured C timing"):
            runner._parse_c_stderr(text, "C-factorised")

    def test_c_protocol_requires_structured_stage_records(self):
        text = """# ---- construction mode: requested=flat, effective=flat ----
# ---- circuit construction plan: 1 CONSTRUCT(s) ----
# --- step 1 ---
# ---- circuit encoding: native_ids=128bit, direct_bindings=true, inferred_types=true; final_triples=4 -> 3, collapsed_unary_plus=1, omitted_types=0 ----
# construction_ms: 12
"""
        with self.assertRaises(runner.StageError):
            runner._parse_c_stderr(text, "C-flat")

    def test_digest_fields_are_rejected(self):
        with self.assertRaises(runner.RunnerError):
            runner._assert_no_digest_fields({"result_sha256": "not-allowed"})

    def test_ttfb_is_measured_at_the_first_payload_byte(self):
        with tempfile.TemporaryDirectory() as temporary, _Endpoint(
            chunk_delay_s=0.25
        ) as endpoint:
            metrics = runner._stream_query_response(
                endpoint.url,
                "SELECT * WHERE { ?s ?p ?o }",
                Path(temporary) / "response.json",
                time.monotonic() + 2.0,
            )
            self.assertLess(metrics["ttfb_ms"], 150.0)
            self.assertGreater(metrics["response_drain_ms"], 150.0)

    def test_tsv_terms_preserve_language_datatype_and_escapes(self):
        self.assertEqual(
            ["literal", "chat", runner.circuit_io.RDF_LANGSTRING, "en"],
            runner.sparql_results_tsv.term_key('"chat"@EN'),
        )
        self.assertEqual(
            ["literal", "a\tb\n", runner.circuit_io.XSD_STRING, ""],
            runner.sparql_results_tsv.term_key('"a\\tb\\n"'),
        )

    def test_streamed_tsv_keeps_exact_small_multiset_without_raw_response(self):
        with tempfile.TemporaryDirectory() as temporary, _Endpoint(
            payload=TSV_RESULT
        ) as endpoint:
            root = Path(temporary)
            query = root / "query.rq"
            query.write_text("SELECT ?x WHERE { ?x <urn:p> <urn:o> }\n", encoding="utf-8")
            config = _config(query, endpoint.url)
            config.update({
                "warmups": 0,
                "runs": 1,
                "response_mode": "stream-tsv",
                "exact_response_row_limit": 10,
            })
            result = runner._run_cell(config, root / "cell")
            run = root / "cell" / "measured-01"
            self.assertEqual("ok", result["status"])
            self.assertFalse((run / "raw-response.json").exists())
            self.assertTrue((run / "response" / "evidence.json").is_file())
            self.assertTrue((run / "offline" / "answer-records.jsonl").is_file())
            self.assertEqual(
                "exact-multiset",
                result["runs"][0]["endpoint"]["endpoint"]["answer_evidence_mode"],
            )
            self.assertEqual(3, result["runs"][0]["offline"]["metrics"]["solution_rows"])

    def test_streamed_tsv_accepts_graphdb_bare_string_answers(self):
        with tempfile.TemporaryDirectory() as temporary, _Endpoint(
            payload=GRAPHDB_BARE_ANSWER_TSV_RESULT
        ) as endpoint:
            root = Path(temporary)
            metrics = runner._stream_query_response_tsv(
                endpoint.url,
                "SELECT ?x WHERE { ?x <urn:p> <urn:o> }",
                root / "response",
                time.monotonic() + 5.0,
                "B",
                10,
                {
                    "query_id": "bare-answer",
                    "run_id": "measured-01",
                    "engine": "graphdb-test",
                    "method": "B",
                },
            )
            self.assertEqual(1, metrics["solution_rows"])
            record = json.loads(
                (root / "response" / "answer-records.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()[0]
            )
            self.assertEqual(
                [["x", ["literal", GRAPHDB_BARE_ANSWER, runner.circuit_io.XSD_STRING, ""]]],
                record["binding"],
            )

    def test_streamed_tsv_drops_large_answer_content_but_keeps_cardinality(self):
        with tempfile.TemporaryDirectory() as temporary, _Endpoint(
            payload=TSV_RESULT
        ) as endpoint:
            root = Path(temporary)
            query = root / "query.rq"
            query.write_text("SELECT ?x WHERE { ?x <urn:p> <urn:o> }\n", encoding="utf-8")
            config = _config(query, endpoint.url)
            config.update({
                "warmups": 0,
                "runs": 1,
                "response_mode": "stream-tsv",
                "exact_response_row_limit": 2,
            })
            result = runner._run_cell(config, root / "cell")
            run = root / "cell" / "measured-01"
            self.assertEqual("ok", result["status"])
            self.assertFalse((run / "response" / "answer-records.jsonl").exists())
            self.assertTrue((run / "offline" / "answer-summary.json").is_file())
            self.assertFalse(result["runs"][0]["answer_content_verified"])
            summary = runner._read_json(run / "offline" / "answer-summary.json")
            self.assertEqual(3, summary["solution_rows"])

    def test_streamed_npcs_response_feeds_postprocessing_without_raw_json(self):
        with tempfile.TemporaryDirectory() as temporary, _Endpoint(
            payload=NPCS_TSV_RESULT
        ) as endpoint:
            run = Path(temporary) / "measured-01"
            run.mkdir()
            metrics = runner._stream_query_response_tsv(
                endpoint.url,
                "SELECT * WHERE { ?s ?p ?o }",
                run / "response",
                time.monotonic() + 5.0,
                "N",
                10,
                {
                    "query_id": "L1-00",
                    "run_id": "measured-01",
                    "engine": "loopback-test",
                    "method": "N",
                },
            )
            config = {
                "method": "N",
                "response_mode": "stream-tsv",
                "offline_timeout_s": 5.0,
                "query_id": "L1-00",
                "engine": "loopback-test",
                "token_regex": runner.DEFAULT_TOKEN_REGEX,
                "npcs_postprocess_mode": "shared",
                "pqe_backend": "none",
                "probabilities": None,
                "uniform_probability": None,
            }
            offline = runner._run_offline(config, run, "measured-01")
            self.assertEqual(2, metrics["solution_rows"])
            self.assertEqual("ok", offline["status"])
            self.assertFalse((run / "raw-response.json").exists())
            self.assertTrue((run / "pp" / "answer-records.jsonl").is_file())
            self.assertTrue(
                (run / "pp" / "npcs-provenance.jsonl").samefile(
                    run / "response" / "npcs-provenance.jsonl"
                )
            )

    def test_streamed_npcs_accepts_graphdb_bare_provenance_and_answer_text(self):
        with tempfile.TemporaryDirectory() as temporary, _Endpoint(
            payload=NPCS_GRAPHDB_BARE_ANSWER_TSV_RESULT
        ) as endpoint:
            root = Path(temporary)
            metrics = runner._stream_query_response_tsv(
                endpoint.url,
                "SELECT * WHERE { ?s ?p ?o }",
                root / "response",
                time.monotonic() + 5.0,
                "N",
                10,
                {
                    "query_id": "L1-00",
                    "run_id": "measured-01",
                    "engine": "graphdb-test",
                    "method": "N",
                },
            )
            self.assertEqual({"bare-text": 1}, metrics["provenance_field_encodings"])
            record = json.loads(
                (root / "response" / "npcs-provenance.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()[0]
            )
            self.assertEqual("⊕((⊗urn:t:1,urn:t:2,))", record["provenance"])
            self.assertEqual(
                json.dumps(
                    [["x", ["literal", GRAPHDB_BARE_ANSWER, runner.circuit_io.XSD_STRING, ""]]],
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                record["answer_key"],
            )
            with self.assertRaises(runner.sparql_results_tsv.TsvResultsError):
                runner.sparql_results_tsv.term_key("unquoted answer text")


class RunnerCellSmokeTest(unittest.TestCase):
    def test_loopback_b_cell_has_independent_runs_and_direct_parity(self):
        with tempfile.TemporaryDirectory() as temporary, _Endpoint() as endpoint:
            root = Path(temporary)
            query = root / "query.rq"
            query.write_text("SELECT ?x WHERE { ?x <urn:p> <urn:o> }\n", encoding="utf-8")
            result = runner._run_cell(_config(query, endpoint.url), root / "cell")
            self.assertEqual("ok", result["status"])
            self.assertEqual("SPARQL_Star", result["scheme"])
            self.assertEqual("SPARQL_Star", result["runs"][0]["endpoint"]["scheme"])
            self.assertEqual(3, endpoint.requests)
            self.assertEqual(2, result["summary"]["measured_successes"])
            self.assertEqual(2, result["summary"]["endpoint_e2e_ms"]["count"])
            self.assertIsNone(_forbidden_result_key(result))
            for run_id in ("warmup-01", "measured-01", "measured-02"):
                run = root / "cell" / run_id
                self.assertTrue((run / "raw-response.json").is_file())
                self.assertTrue((run / "offline" / "answer-records.jsonl").is_file())
                self.assertTrue((run / "run.json").is_file())
            measured = result["runs"][-1]
            self.assertTrue(measured["answer_records_equal_first_measured"])
            self.assertEqual(2, measured["offline"]["metrics"]["distinct_binding_count"])

    def test_offline_failure_keeps_endpoint_runs_and_resumes_without_requerying(self):
        with tempfile.TemporaryDirectory() as temporary, _Endpoint() as endpoint:
            root = Path(temporary)
            query = root / "query.rq"
            query.write_text("SELECT ?x WHERE { ?x <urn:p> <urn:o> }\n", encoding="utf-8")
            config = _config(query, endpoint.url)
            config.update({"warmups": 0, "runs": 2})
            failed = {
                "schema": runner.OFFLINE_SCHEMA,
                "status": "offline-timeout",
                "offline_timeout_s": config["offline_timeout_s"],
                "detail": "injected test failure",
            }
            with mock.patch.object(runner, "_run_offline", return_value=failed):
                initial = runner._run_cell(config, root / "cell")
            self.assertEqual("incomplete", initial["status"])
            self.assertEqual(2, len(initial["runs"]))
            self.assertEqual(2, endpoint.requests)

            resumed = runner._resume_offline_cell(root / "cell")
            self.assertEqual("ok", resumed["status"])
            self.assertEqual("offline-resume-001", resumed["attempt"])
            self.assertTrue(resumed["answer_records_equal_across_measured_runs"])
            self.assertEqual(2, endpoint.requests)
            for run_id in ("measured-01", "measured-02"):
                resumed_run = root / "cell" / "offline-resume-001" / run_id
                self.assertTrue((resumed_run / "offline-result.json").is_file())
                self.assertTrue((resumed_run / "offline" / "answer-records.jsonl").is_file())
                original = runner._read_json(root / "cell" / run_id / "run.json")
                self.assertEqual("offline-timeout", original["offline"]["status"])

            already_complete = runner._resume_offline_cell(root / "cell")
            self.assertEqual("ok", already_complete["status"])
            self.assertIsNone(already_complete["attempt"])
            self.assertFalse((root / "cell" / "offline-resume-002").exists())

    def test_optional_warmup_offline_fail_fast_skips_measured_endpoint(self):
        with tempfile.TemporaryDirectory() as temporary, _Endpoint() as endpoint:
            root = Path(temporary)
            query = root / "query.rq"
            query.write_text("SELECT ?x WHERE { ?x <urn:p> <urn:o> }\n", encoding="utf-8")
            config = _config(query, endpoint.url)
            config["stop_after_warmup_offline_failure"] = True
            failed = {
                "schema": runner.OFFLINE_SCHEMA,
                "status": "offline-timeout",
                "offline_timeout_s": config["offline_timeout_s"],
                "detail": "injected warmup failure",
            }
            with mock.patch.object(runner, "_run_offline", return_value=failed):
                result = runner._run_cell(config, root / "cell")
            self.assertEqual("incomplete", result["status"])
            self.assertEqual(1, len(result["runs"]))
            self.assertEqual(1, endpoint.requests)
            self.assertTrue(
                result["protocol"]["stop_after_warmup_offline_failure"]
            )
            self.assertFalse((root / "cell" / "measured-01").exists())

    @unittest.skipUnless(os.name == "posix", "POSIX signal return codes required")
    def test_child_native_signal_is_preserved(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = runner._run_child(
                [
                    sys.executable,
                    "-c",
                    "import os,signal; os.kill(os.getpid(), signal.SIGSEGV)",
                ],
                root / "stdout",
                root / "stderr",
                5.0,
            )
            self.assertFalse(result["timed_out"])
            self.assertEqual(-signal.SIGSEGV, result["returncode"])
            self.assertEqual(signal.SIGSEGV, result["signal_number"])
            self.assertEqual("SIGSEGV", result["signal_name"])

    def test_pqe_native_crash_has_substage_and_stable_cause(self):
        failure = runner._normalized_failure({
            "run_id": "warmup-01",
            "phase": "warmup",
            "status": "offline-crash",
            "endpoint": {"status": "ok"},
            "offline": {
                "status": "offline-crash",
                "detail": "offline process terminated by SIGSEGV",
                "substage": "pqe_compile",
                "signal": signal.SIGSEGV,
                "signal_name": "SIGSEGV",
            },
        })
        self.assertEqual("PQE_NATIVE_CRASH", failure["cause"])
        self.assertEqual("pqe_compile", failure["substage"])
        self.assertEqual(signal.SIGSEGV, failure["signal"])

    def test_offline_native_crash_reads_the_surviving_stage_journal(self):
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary) / "run"
            run_dir.mkdir()
            (run_dir / "circuit.nt").write_text("", encoding="utf-8")

            def crash_child(*_args, **_kwargs):
                output = run_dir / "offline"
                output.mkdir()
                runner._append_json_line(output / "offline-stage-events.jsonl", {
                    "schema": runner.OFFLINE_STAGE_SCHEMA,
                    "event": "start",
                    "stage": "pqe_compile",
                })
                return {
                    "returncode": -signal.SIGSEGV,
                    "timed_out": False,
                    "signal_number": signal.SIGSEGV,
                    "signal_name": "SIGSEGV",
                }

            config = {
                "method": "C-flat",
                "offline_timeout_s": 5.0,
                "pqe_backend": "cudd",
                "probabilities": None,
                "uniform_probability": 0.5,
            }
            with mock.patch.object(runner, "_run_child", side_effect=crash_child):
                result = runner._run_offline(config, run_dir, "warmup-01")
            self.assertEqual("offline-crash", result["status"])
            self.assertEqual("pqe_compile", result["substage"])
            self.assertEqual("SIGSEGV", result["signal_name"])
            self.assertEqual(
                "offline-stage-events.jsonl",
                result["stage_state"]["journal"],
            )

    def test_circuit_offline_resume_reuses_the_saved_circuit(self):
        with tempfile.TemporaryDirectory() as temporary:
            cell = Path(temporary) / "cell"
            run_dir = cell / "measured-01"
            run_dir.mkdir(parents=True)
            (run_dir / "circuit.nt").write_text(
                """<urn:g:t> <urn:circuit:in> <urn:t:1> .
<urn:g:t> <urn:circuit:feeds> <urn:g:a> .
<urn:g:a> <urn:circuit:answerRoot> "vars:78" .
<urn:g:a> <urn:circuit:bind:78> <urn:value:a> .
""",
                encoding="utf-8",
            )
            config = {
                "schema": runner.SCHEMA,
                "method": "C-flat",
                "query_id": "resume-circuit",
                "engine": "saved-artifact-test",
                "warmups": 0,
                "runs": 1,
                "offline_timeout_s": 5.0,
                "pqe_backend": "oracle",
                "probabilities": None,
                "uniform_probability": 0.5,
            }
            failed = {
                "schema": runner.OFFLINE_SCHEMA,
                "status": "offline-timeout",
                "offline_timeout_s": 5.0,
                "detail": "injected",
            }
            source_run = {
                "schema": runner.RUN_SCHEMA,
                "run_id": "measured-01",
                "phase": "measured",
                "index": 1,
                "status": "offline-timeout",
                "endpoint": {
                    "schema": runner.ENDPOINT_SCHEMA,
                    "status": "ok",
                    "endpoint": {"endpoint_e2e_ms": 7.0},
                },
                "offline": failed,
            }
            runner._atomic_json(cell / "cell-config.json", config)
            runner._atomic_json(cell / "cell.json", {
                "schema": runner.SCHEMA,
                "status": "incomplete",
                "runs": [source_run],
            })

            resumed = runner._resume_offline_cell(cell)
            self.assertEqual("ok", resumed["status"])
            self.assertTrue(resumed["circuits_equal_across_measured_runs"])
            artifact = cell / "offline-resume-001" / "measured-01"
            self.assertTrue((artifact / "offline" / "metrics.json").is_file())
            self.assertTrue((artifact / "offline" / "answer-records.jsonl").is_file())
            persisted = runner._read_json(cell / "offline-resume-001" / "resume.json")
            self.assertEqual(str(cell / "offline-resume-001" / "resume.json"), persisted["manifest"])

    def test_hard_timeout_stops_the_cell_before_measured_runs(self):
        with tempfile.TemporaryDirectory() as temporary, _Endpoint(delay_s=1.0) as endpoint:
            root = Path(temporary)
            query = root / "query.rq"
            query.write_text("SELECT ?x WHERE { ?x <urn:p> <urn:o> }\n", encoding="utf-8")
            config = _config(query, endpoint.url)
            config["endpoint_timeout_s"] = 0.2
            started = time.perf_counter()
            result = runner._run_cell(config, root / "cell")
            elapsed = time.perf_counter() - started
            self.assertEqual("incomplete", result["status"])
            self.assertEqual(1, len(result["runs"]))
            self.assertEqual("timeout", result["runs"][0]["status"])
            self.assertEqual("TO", result["failure"]["cause"])
            self.assertEqual("endpoint", result["failure"]["stage"])
            self.assertFalse(result["recovery_required"])
            self.assertEqual(
                "SPARQL_Star", result["runs"][0]["endpoint"]["scheme"]
            )
            self.assertLess(elapsed, 0.9)
            self.assertFalse((root / "cell" / "measured-01").exists())

    @unittest.skipUnless(os.name == "posix", "fake Java executable uses a POSIX shebang")
    def test_c_timeout_requires_store_recovery(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            query = root / "query.rq"
            query.write_text("SELECT ?x WHERE { ?x <urn:p> <urn:o> }\n", encoding="utf-8")
            jar = root / "runner.jar"
            jar.write_bytes(b"test placeholder")
            data = root / "data.nt"
            data.write_text("<urn:s> <urn:p> <urn:o> .\n", encoding="utf-8")
            fake_java = root / "fake-java"
            fake_java.write_text(
                "#!/usr/bin/env python3\nimport time\ntime.sleep(10)\n",
                encoding="utf-8",
            )
            fake_java.chmod(0o755)
            config = _config(query, "http://unused.invalid/query", method="C-flat")
            config.update({
                "jar": str(jar),
                "java": str(fake_java),
                "reified_data": str(data),
                "warmups": 1,
                "runs": 1,
                "endpoint_timeout_s": 0.2,
                "c_read_only": True,
            })
            result = runner._run_cell(config, root / "cell")
            self.assertEqual("incomplete", result["status"])
            self.assertTrue(result["recovery_required"])
            self.assertEqual("TO", result["failure"]["cause"])
            self.assertEqual("endpoint", result["failure"]["stage"])
            self.assertEqual("warmup-01", result["failure"]["run_id"])

    @unittest.skipUnless(os.name == "posix", "fake Java executable uses a POSIX shebang")
    def test_c_flat_cell_persists_and_parses_the_circuit(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            query = root / "query.rq"
            query.write_text("SELECT ?x WHERE { ?x <urn:p> <urn:o> }\n", encoding="utf-8")
            jar = root / "runner.jar"
            jar.write_bytes(b"test placeholder")
            data = root / "data.nt"
            data.write_text("<urn:s> <urn:p> <urn:o> .\n", encoding="utf-8")
            fake_java = root / "fake-java"
            fake_java.write_text(
                """#!/usr/bin/env python3
import json
import sys
if "SPARQL_Star" not in sys.argv:
    raise SystemExit(9)
sys.stdout.write('''<urn:g:t> <urn:circuit:in> <urn:t:1> .
<urn:g:t> <urn:circuit:feeds> <urn:g:a> .
<urn:g:a> <urn:circuit:answerRoot> "vars:78" .
<urn:g:a> <urn:circuit:bind:78> <urn:value:a> .
''')
sys.stderr.write('''# ---- construction mode: requested=flat, effective=flat ----
# ---- circuit construction plan: 1 CONSTRUCT(s) ----
# --- step 1 ---
# step label: flat
# ---- circuit encoding: native_ids=128bit, direct_bindings=true, inferred_types=true; final_triples=6 -> 4, collapsed_unary_plus=1, omitted_types=2 ----
# construction_ms: 3
# circuit triples: 4
# heap_peak: used_bytes=1234, committed_bytes_at_peak=4096, max_bytes=8192, samples=7, interval_ms=100
''')
events = [
    "query_read", "plan_generation", "repository_init", "data_ready",
    "construct_step", "workspace_cleanup", "normalization",
    "construction_complete", "serialization", "named_graph_persist",
    "endpoint_cleanup", "run_complete",
]
for event in events:
    record = {"schema": "sparqlcirc-c-stage-v1", "event": event, "duration_ms": 0.1}
    if event == "plan_generation":
        record["strategy_fragments"] = ["direct-derivations"]
    sys.stderr.write("# sc-stage " + json.dumps(record, separators=(",", ":")) + "\\n")
""",
                encoding="utf-8",
            )
            fake_java.chmod(0o755)
            config = _config(query, "http://unused.invalid/query", method="C-flat")
            config.update({
                "jar": str(jar),
                "java": str(fake_java),
                "reified_data": str(data),
                "warmups": 0,
                "runs": 2,
                "c_read_only": True,
                "pqe_backend": "oracle",
                "uniform_probability": 0.5,
            })
            result = runner._run_cell(config, root / "cell")
            self.assertEqual("ok", result["status"])
            self.assertEqual(2, result["summary"]["measured_successes"])
            second = result["runs"][1]
            self.assertTrue(second["circuit_equal_first_measured"])
            structure = second["offline"]["metrics"]["answer_reachable_circuit"]
            self.assertEqual({"nodes": 3, "edges": 2, "total": 5}, {
                key: structure[key] for key in ("nodes", "edges", "total")
            })
            self.assertEqual("flat", second["endpoint"]["endpoint"]["effective_mode"])
            self.assertEqual(
                ["direct-derivations"],
                second["endpoint"]["endpoint"]["strategy_fragments"],
            )
            self.assertEqual(
                1234,
                second["endpoint"]["endpoint"]["jvm_heap_peak"]["peak_used_bytes"],
            )
            timing = second["endpoint"]["endpoint"]["structured_timing"]
            self.assertTrue(timing["complete"])
            self.assertEqual(1, timing["event_counts"]["construct_step"])
            stage_file = root / "cell" / "measured-02" / "c-stages.jsonl"
            self.assertEqual(12, len(stage_file.read_text(encoding="utf-8").splitlines()))
            self.assertEqual(
                "oracle", second["offline"]["metrics"]["compiler"]["backend"]
            )
            self.assertTrue((root / "cell" / "measured-02" / "offline" / "probabilities.jsonl").is_file())
            self.assertIsNone(_forbidden_result_key(result))

    @unittest.skipUnless(os.name == "posix", "fake Java executable uses a POSIX shebang")
    def test_c_flat_cell_accepts_an_empty_answer_circuit(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            query = root / "query.rq"
            query.write_text("SELECT ?x WHERE { ?x <urn:p> <urn:o> }\n", encoding="utf-8")
            jar = root / "runner.jar"
            jar.write_bytes(b"test placeholder")
            data = root / "data.nt"
            data.write_text("<urn:s> <urn:p> <urn:o> .\n", encoding="utf-8")
            fake_java = root / "fake-java"
            fake_java.write_text(
                """#!/usr/bin/env python3
import json
import sys
if "SPARQL_Star" not in sys.argv:
    raise SystemExit(9)
sys.stderr.write('''# ---- construction mode: requested=flat, effective=flat ----
# ---- circuit construction plan: 1 CONSTRUCT(s) ----
# --- step 1 ---
# step label: flat
# ---- circuit encoding: native_ids=128bit, direct_bindings=true, inferred_types=true; final_triples=0 -> 0, collapsed_unary_plus=0, omitted_types=0 ----
# construction_ms: 3
# circuit triples: 0
''')
events = [
    "query_read", "plan_generation", "repository_init", "data_ready",
    "construct_step", "workspace_cleanup", "normalization",
    "construction_complete", "serialization", "named_graph_persist",
    "endpoint_cleanup", "run_complete",
]
for event in events:
    record = {"schema": "sparqlcirc-c-stage-v1", "event": event, "duration_ms": 0.1}
    sys.stderr.write("# sc-stage " + json.dumps(record, separators=(",", ":")) + "\\n")
""",
                encoding="utf-8",
            )
            fake_java.chmod(0o755)
            config = _config(query, "http://unused.invalid/query", method="C-flat")
            config.update({
                "jar": str(jar),
                "java": str(fake_java),
                "reified_data": str(data),
                "warmups": 1,
                "runs": 1,
                "c_read_only": True,
                "pqe_backend": "oracle",
                "uniform_probability": 0.5,
            })
            result = runner._run_cell(config, root / "cell")
            self.assertEqual("ok", result["status"])
            self.assertEqual(1, result["summary"]["measured_successes"])
            measured = result["runs"][1]
            self.assertTrue(measured["endpoint"]["endpoint"]["empty_circuit"])
            self.assertEqual(0, measured["endpoint"]["endpoint"]["circuit_bytes"])
            self.assertEqual(0, measured["offline"]["metrics"]["answer_count"])
            self.assertEqual(
                {"nodes": 0, "edges": 0, "total": 0},
                {
                    key: measured["offline"]["metrics"]["answer_reachable_circuit"][key]
                    for key in ("nodes", "edges", "total")
                },
            )
            self.assertEqual(0, (root / "cell" / "measured-01" / "circuit.nt").stat().st_size)
            self.assertEqual(
                "",
                (root / "cell" / "measured-01" / "offline" / "answer-records.jsonl").read_text(
                    encoding="utf-8"
                ),
            )
            self.assertIsNone(_forbidden_result_key(result))

    def test_npcs_answer_records_deduplicate_streamed_source_rows(self):
        with tempfile.TemporaryDirectory() as temporary:
            pp = Path(temporary)
            answer_key = json.dumps(
                [["x", ["literal", "3.0", "http://www.w3.org/2001/XMLSchema#decimal", ""]]],
                separators=(",", ":"),
            )
            (pp / "npcs-provenance.jsonl").write_text(
                "".join(
                    json.dumps({"answer_key": answer_key, "provenance": provenance})
                    + "\n"
                    for provenance in ("⊕(urn:t:1)", "⊕(urn:t:2)")
                ),
                encoding="utf-8",
            )
            metrics = runner._persist_npcs_answer_records(pp)
            self.assertEqual(metrics["source_answer_record_count"], 2)
            self.assertEqual(metrics["answer_count"], 1)
            self.assertEqual(metrics["duplicate_answer_records"], 1)
            self.assertEqual(
                len((pp / "answer-records.jsonl").read_text().splitlines()), 1
            )

    def test_circuit_offline_merges_duplicate_canonical_answer_roots(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            circuit = root / "circuit.nt"
            circuit.write_text(
                '''<urn:t:1> <urn:circuit:feeds> <urn:g:a:1> .
<urn:g:a:1> <urn:circuit:answerRoot> "vars:78" .
<urn:g:a:1> <urn:circuit:bind:78> "3.0"^^<http://www.w3.org/2001/XMLSchema#decimal> .
<urn:t:2> <urn:circuit:feeds> <urn:g:a:2> .
<urn:g:a:2> <urn:circuit:answerRoot> "vars:78" .
<urn:g:a:2> <urn:circuit:bind:78> "3.0"^^<http://www.w3.org/2001/XMLSchema#decimal> .
''',
                encoding="utf-8",
            )
            metrics = runner._process_circuit(
                circuit,
                root / "offline",
                "oracle",
                None,
                0.5,
            )
            self.assertEqual(metrics["raw_answer_root_count"], 2)
            self.assertEqual(metrics["answer_count"], 1)
            self.assertEqual(
                metrics["answer_root_normalization"]["merge_plus_nodes"], 1
            )
            self.assertEqual(
                metrics["answer_root_normalization"]["merge_plus_edges"], 2
            )
            self.assertEqual(
                {"nodes": 5, "edges": 4, "total": 9},
                {
                    key: metrics["answer_reachable_circuit"][key]
                    for key in ("nodes", "edges", "total")
                },
            )
            probability = json.loads(
                (root / "offline" / "probabilities.jsonl").read_text(
                    encoding="utf-8"
                )
            )
            self.assertAlmostEqual(probability["probability"], 0.75)
            events = [
                json.loads(line)
                for line in (
                    root / "offline" / "offline-stage-events.jsonl"
                ).read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(metrics["offline_stage_event_count"], len(events))
            self.assertEqual("circuit_decode", events[0]["stage"])
            self.assertEqual("start", events[0]["event"])
            self.assertEqual("pqe_artifact_persist", events[-1]["stage"])
            self.assertEqual("complete", events[-1]["event"])
            state = runner._offline_stage_state(root / "offline")
            self.assertIsNone(state["active_substage"])
            self.assertEqual("pqe_artifact_persist", state["last_completed_substage"])

    @unittest.skipUnless(os.name == "posix", "fake Java executable uses a POSIX shebang")
    def test_n_cell_pairs_each_response_with_one_postprocess_and_pqe(self):
        payload = json.dumps({
            "head": {"vars": ["x", "finalprovennacevariable"]},
            "results": {"bindings": [{
                "x": {"type": "uri", "value": "urn:value:a"},
                "finalprovennacevariable": {
                    "type": "literal",
                    "value": "⊕((⊗urn:t:1,))",
                },
            }]},
        }, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        with tempfile.TemporaryDirectory() as temporary, _Endpoint(payload=payload) as endpoint:
            root = Path(temporary)
            query = root / "query.rq"
            query.write_text("SELECT ?x WHERE { ?x <urn:p> <urn:o> }\n", encoding="utf-8")
            jar = root / "runner.jar"
            jar.write_bytes(b"test placeholder")
            fake_java = root / "fake-java"
            fake_java.write_text(
                """#!/usr/bin/env python3
import sys
if "SPARQL_Star" not in sys.argv:
    raise SystemExit(9)
sys.stdout.write('SELECT ?x ?finalprovennacevariable WHERE { ?x <urn:p> <urn:o> }\\n')
""",
                encoding="utf-8",
            )
            fake_java.chmod(0o755)
            config = _config(query, endpoint.url, method="N")
            config.update({
                "jar": str(jar),
                "java": str(fake_java),
                "warmups": 0,
                "runs": 1,
                "pqe_backend": "oracle",
                "uniform_probability": 0.5,
            })
            result = runner._run_cell(config, root / "cell")
            self.assertEqual("ok", result["status"])
            self.assertEqual(1, endpoint.requests)
            run = result["runs"][0]
            self.assertEqual(3, run["offline"]["metrics"]["tree_nodes"])
            self.assertEqual(2, run["offline"]["metrics"]["tree_edges"])
            self.assertEqual(2, run["offline"]["metrics"]["hc_nodes"])
            self.assertEqual(1, run["offline"]["metrics"]["hc_expression_nodes"])
            self.assertEqual(1, run["offline"]["metrics"]["answer_root_nodes"])
            self.assertEqual("oracle", run["offline"]["metrics"]["compiler"]["backend"])
            pp = root / "cell" / "measured-01" / "pp"
            self.assertTrue((pp / "answer-records.jsonl").is_file())
            self.assertTrue((pp / "probabilities.jsonl").is_file())
            self.assertIsNone(_forbidden_result_key(result))


if __name__ == "__main__":
    unittest.main()
