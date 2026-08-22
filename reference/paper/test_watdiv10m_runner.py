#!/usr/bin/env python3
"""Offline and loopback regressions for the WatDiv single-cell runner."""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
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
        "jar": None,
        "java": "java",
        "reified_data": None,
        "warmups": 1,
        "runs": 2,
        "endpoint_timeout_s": 5.0,
        "offline_timeout_s": 5.0,
        "pqe_backend": "none",
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
        self.assertEqual(1, args.runs)
        self.assertEqual(600.0, args.endpoint_timeout)
        self.assertEqual(600.0, args.offline_timeout)

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

    def test_c_protocol_rejects_fallback(self):
        text = """# ---- construction mode: requested=factored, effective=flat ----
# ---- explicit fallback: unsupported algebra ----
# construction_ms: 12
"""
        with self.assertRaises(runner.StageError):
            runner._parse_c_stderr(text, "C-factored")

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
            self.assertEqual(
                "SPARQL_Star", result["runs"][0]["endpoint"]["scheme"]
            )
            self.assertLess(elapsed, 0.9)
            self.assertFalse((root / "cell" / "measured-01").exists())

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
            self.assertEqual(1, run["offline"]["metrics"]["hc_nodes"])
            self.assertEqual("oracle", run["offline"]["metrics"]["compiler"]["backend"])
            pp = root / "cell" / "measured-01" / "pp"
            self.assertTrue((pp / "answer-records.jsonl").is_file())
            self.assertTrue((pp / "probabilities.jsonl").is_file())
            self.assertIsNone(_forbidden_result_key(result))


if __name__ == "__main__":
    unittest.main()
