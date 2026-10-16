#!/usr/bin/env python3
"""Regressions for the WWW'27 additions to the WatDiv cell runner.

Covered: the lookup-only layout, N-orig gating, B-mixed, the endpoint-only
timing protocol (prepared text, timed window, spooling), and the cold-cache
hooks.  Everything runs against loopback HTTP fixtures; the tests that need
real jars are skipped when ``engine/target/npcs-rewrite.jar`` or
``$NPCS_ORIG_JAR`` is absent.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest import mock


HERE = Path(__file__).resolve().parent
REFERENCE = HERE.parent
PAPER = REFERENCE / "paper"
ROOT = REFERENCE.parent
for path in (REFERENCE, PAPER):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import watdiv10m_runner as runner
import test_watdiv10m_runner as fixtures


OUR_JAR = ROOT / "engine" / "target" / "npcs-rewrite.jar"
ORIG_JAR = Path(os.environ["NPCS_ORIG_JAR"]) if os.environ.get("NPCS_ORIG_JAR") else None
BGP_QUERY = "SELECT ?x WHERE { ?x <urn:p> <urn:o> }\n"
OPTIONAL_QUERY = "SELECT ?x ?y WHERE { ?x <urn:p> <urn:o> OPTIONAL { ?x <urn:q> ?y } }\n"


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def _endpoint_only(config: dict) -> dict:
    config.update({"timing_protocol": "endpoint-only", "warmups": 0, "runs": 2})
    return config


class LayoutTest(unittest.TestCase):
    def test_lookup_only_r_has_no_asserted_pattern(self):
        query = "SELECT ?x WHERE { ?x <urn:p> <urn:o> . ?x <urn:q> ?y }"
        inline = runner._reification_text(query, "SPARQL_Star")
        pure = runner._reification_text(query, "SPARQL_Star_Pure")
        self.assertIn("?x <urn:p> <urn:o> .", inline)
        self.assertNotIn("?x <urn:p> <urn:o> .", pure)
        self.assertEqual(2, pure.count("<http://example.org/occurrenceOf>"))
        self.assertEqual(2, inline.count("<http://example.org/occurrenceOf>"))

    def test_layout_names(self):
        self.assertEqual("lookup-only", runner._layout("SPARQL_Star_Pure"))
        self.assertEqual("asserted-plus-lookup", runner._layout("SPARQL_Star"))
        self.assertEqual("row-inline", runner._layout("SPARQL_Star_Row"))

    def test_n_orig_requires_the_lookup_only_layout(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            config = fixtures._config(_write(root / "q.rq", BGP_QUERY), "http://x/q", "N-orig")
            config.update(jar=str(_write(root / "ours.jar", "")),
                          orig_jar=str(_write(root / "orig.jar", "")))
            with self.assertRaisesRegex(runner.RunnerError, "lookup-only layout"):
                runner._validate_config(config)
            config["scheme"] = "SPARQL_Star_Pure"
            runner._validate_config(config)
            config["orig_jar"] = None
            with self.assertRaisesRegex(runner.RunnerError, "NPCS_ORIG_JAR"):
                runner._validate_config(config)

    def test_cold_mode_requires_an_explicit_hook(self):
        with tempfile.TemporaryDirectory() as raw:
            config = fixtures._config(_write(Path(raw) / "q.rq", BGP_QUERY), "http://x/q")
            config["cache_mode"] = "cold"
            with self.assertRaisesRegex(runner.RunnerError, "cache-reset-command"):
                runner._validate_config(config)


class NOrigGateTest(unittest.TestCase):
    def test_optional_is_refused_before_any_request(self):
        with tempfile.TemporaryDirectory() as raw, fixtures._Endpoint(
            payload=fixtures.NPCS_TSV_RESULT
        ) as endpoint:
            root = Path(raw)
            config = fixtures._config(_write(root / "q.rq", OPTIONAL_QUERY), endpoint.url, "N-orig")
            config.update(
                scheme="SPARQL_Star_Pure",
                jar=str(_write(root / "ours.jar", "")),
                orig_jar=str(_write(root / "orig.jar", "")),
                response_mode="stream-tsv",
            )
            result = runner._run_cell(config, root / "cell")
            self.assertEqual("unsupported", result["status"])
            self.assertIn("OPTIONAL", result["unsupported_reason"])
            self.assertEqual([], result["runs"])
            self.assertEqual(0, endpoint.requests)
            prepared = runner._read_json(root / "cell" / "prepared-query.json")
            self.assertEqual("unsupported", prepared["status"])
            self.assertEqual(["OPTIONAL"], prepared["gate"]["features"])
            self.assertIsNone(fixtures._forbidden_result_key(result))
            with self.assertRaisesRegex(runner.RunnerError, "no endpoint request"):
                runner._resume_offline_cell(root / "cell")

    def test_cli_exit_code_marks_a_refusal(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            arguments = [
                "--query", str(_write(root / "q.rq", "SELECT ?x WHERE { { ?x <urn:p> ?y } UNION { ?x <urn:q> ?y } }")),
                "--query-id", "U-00", "--engine", "loopback", "--method", "N-orig",
                "--scheme", "SPARQL_Star_Pure", "--reified-endpoint", "http://127.0.0.1:9/q",
                "--jar", str(_write(root / "ours.jar", "")),
                "--orig-jar", str(_write(root / "orig.jar", "")),
                "--out", str(root / "cell"),
            ]
            with mock.patch("sys.stdout"):
                self.assertEqual(3, runner.main(arguments))
            cell = runner._read_json(root / "cell" / "cell.json")
            self.assertIn("UNION", cell["unsupported_reason"])

    @unittest.skipUnless(OUR_JAR.is_file() and ORIG_JAR is not None and ORIG_JAR.is_file(),
                         "needs engine/target/npcs-rewrite.jar and $NPCS_ORIG_JAR")
    def test_bgp_is_rewritten_by_the_original_and_matches_the_reimplementation(self):
        query = (
            "PREFIX wsdbm: <http://db.uwaterloo.ca/~galuc/wsdbm/>\n"
            "SELECT ?v0 ?v2 WHERE { ?v0 wsdbm:subscribes wsdbm:Website1 . "
            "?v0 wsdbm:likes ?v2 . }\n"
        )
        with tempfile.TemporaryDirectory() as raw, fixtures._Endpoint(
            payload=fixtures.NPCS_TSV_RESULT
        ) as endpoint:
            root = Path(raw)
            config = _endpoint_only(fixtures._config(_write(root / "q.rq", query), endpoint.url, "N-orig"))
            config.update(scheme="SPARQL_Star_Pure", jar=str(OUR_JAR), orig_jar=str(ORIG_JAR),
                          response_mode="stream-tsv")
            result = runner._run_cell(config, root / "cell")
            self.assertEqual("ok", result["status"], json.dumps(result.get("failure")))
            prepared = result["prepared_query"]
            self.assertTrue(prepared["reimplementation_equivalent"])
            self.assertEqual("ok", prepared["rdf4j_parse"])
            text = (root / "cell" / "prepared-query.rq").read_text(encoding="utf-8")
            self.assertIn("AS ?finalprovennacevariable", text)
            self.assertNotIn("Byte Array", text)
            self.assertEqual(2, endpoint.requests)
            run = root / "cell" / "measured-01"
            self.assertEqual(text, (run / "rewritten.rq").read_text(encoding="utf-8"))
            self.assertTrue((run / "pp" / "answer-records.jsonl").is_file())
            self.assertEqual("npcs-original-jar-rewrite", result["runs"][0]["endpoint"]["rewrite"]["kind"])


class EndpointOnlyTest(unittest.TestCase):
    def test_b_times_the_exchange_and_keeps_the_legacy_window(self):
        with tempfile.TemporaryDirectory() as raw, fixtures._Endpoint(delay_s=0.2) as endpoint:
            root = Path(raw)
            config = _endpoint_only(fixtures._config(_write(root / "q.rq", BGP_QUERY), endpoint.url))
            result = runner._run_cell(config, root / "cell")
            self.assertEqual("ok", result["status"])
            self.assertEqual(BGP_QUERY, (root / "cell" / "prepared-query.rq").read_text(encoding="utf-8"))
            self.assertEqual(2, endpoint.requests)
            for run in result["runs"]:
                metrics = run["endpoint"]["endpoint"]
                self.assertNotIn("endpoint_e2e_ms", metrics)
                self.assertGreaterEqual(metrics["endpoint_only_ms"], 200.0)
                self.assertGreaterEqual(metrics["legacy_endpoint_e2e_ms"], metrics["endpoint_only_ms"])
                self.assertEqual("endpoint-only", metrics["timing_protocol"])
                self.assertFalse(metrics["response_spilled_to_disk"])
                self.assertIn("endpoint_only_scope", metrics)
                self.assertTrue((root / "cell" / run["run_id"] / "raw-response.json").is_file())
            summary = result["summary"]
            self.assertNotIn("endpoint_e2e_ms", summary)
            self.assertEqual(2, summary["endpoint_only_ms"]["count"])
            values = summary["endpoint_only_ms"]["values"]
            self.assertAlmostEqual(sorted(values)[0] / 2 + sorted(values)[1] / 2,
                                   summary["endpoint_only_ms"]["median"])
            self.assertEqual("endpoint-only", result["protocol"]["timing_protocol"])
            self.assertEqual("plain", result["protocol"]["store"])
            self.assertIsNone(fixtures._forbidden_result_key(result))
            self.assertTrue(result["runs"][-1]["answer_records_equal_first_measured"])

    def test_large_response_is_spooled_and_still_complete(self):
        with tempfile.TemporaryDirectory() as raw, fixtures._Endpoint() as endpoint:
            root = Path(raw)
            config = _endpoint_only(fixtures._config(_write(root / "q.rq", BGP_QUERY), endpoint.url))
            config["endpoint_buffer_bytes"] = 16
            result = runner._run_cell(config, root / "cell")
            self.assertEqual("ok", result["status"])
            run = root / "cell" / "measured-01"
            metrics = result["runs"][0]["endpoint"]["endpoint"]
            self.assertTrue(metrics["response_spilled_to_disk"])
            self.assertEqual(fixtures.RESULT, (run / "raw-response.json").read_bytes())
            self.assertFalse((run / "response.spool").exists())
            self.assertEqual(2, result["runs"][0]["offline"]["metrics"]["distinct_binding_count"])

    def test_streamed_tsv_is_parsed_after_the_window(self):
        with tempfile.TemporaryDirectory() as raw, fixtures._Endpoint(
            payload=fixtures.TSV_RESULT, chunk_delay_s=0.2
        ) as endpoint:
            root = Path(raw)
            config = _endpoint_only(fixtures._config(_write(root / "q.rq", BGP_QUERY), endpoint.url))
            config.update(response_mode="stream-tsv", exact_response_row_limit=10, endpoint_buffer_bytes=8)
            result = runner._run_cell(config, root / "cell")
            self.assertEqual("ok", result["status"])
            metrics = result["runs"][0]["endpoint"]["endpoint"]
            self.assertGreaterEqual(metrics["endpoint_only_ms"], 200.0)
            self.assertEqual(3, metrics["solution_rows"])
            self.assertFalse(metrics["raw_response_persisted"])
            run = root / "cell" / "measured-01"
            self.assertFalse((run / "response.spool").exists())
            self.assertTrue((run / "offline" / "answer-records.jsonl").is_file())

    def test_b_mixed_queries_the_mixed_store(self):
        with tempfile.TemporaryDirectory() as raw, fixtures._Endpoint() as plain, \
                fixtures._Endpoint() as mixed:
            root = Path(raw)
            config = _endpoint_only(fixtures._config(_write(root / "q.rq", BGP_QUERY), plain.url, "B-mixed"))
            config["reified_endpoint"] = mixed.url
            result = runner._run_cell(config, root / "cell")
            self.assertEqual("ok", result["status"])
            self.assertEqual(0, plain.requests)
            self.assertEqual(2, mixed.requests)
            self.assertEqual("mixed", result["protocol"]["store"])
            self.assertEqual("plain", result["protocol"]["layout"])

    def test_a_rewrite_that_differs_from_the_prepared_text_fails_the_run(self):
        with tempfile.TemporaryDirectory() as raw, fixtures._Endpoint() as endpoint:
            root = Path(raw)
            config = fixtures._config(_write(root / "q.rq", BGP_QUERY), endpoint.url)
            config.update(timing_protocol="endpoint-only",
                          prepared_query=str(_write(root / "prepared.rq", "SELECT * WHERE {}")))
            run = root / "run"
            run.mkdir()
            result = runner._endpoint_run(config, run)
            self.assertEqual("prepared-query-mismatch", result["status"])
            self.assertEqual(0, endpoint.requests)

    def test_c_reports_construction_as_endpoint_time_and_keeps_process_wall(self):
        with tempfile.TemporaryDirectory() as raw:
            run = Path(raw)
            config = fixtures._config(_write(run / "q.rq", BGP_QUERY), "http://x/q", "C-flat")
            config["timing_protocol"] = "endpoint-only"
            fake = {"construction_ms": 12.5, "endpoint_e2e_ms": 900.0,
                    "timing_scope": "process", "c_request_ms_sum": 10.0}
            with mock.patch.object(runner, "_run_c", return_value=dict(fake)):
                result = runner._endpoint_run(config, run)
            metrics = result["endpoint"]
            self.assertEqual(12.5, metrics["endpoint_only_ms"])
            self.assertEqual(900.0, metrics["legacy_endpoint_e2e_ms"])
            self.assertNotIn("endpoint_e2e_ms", metrics)
            self.assertEqual(12.5, runner._primary_endpoint_ms(result))

    @unittest.skipUnless(OUR_JAR.is_file(), "needs engine/target/npcs-rewrite.jar")
    def test_n_lookup_only_prepared_text_is_sent(self):
        with tempfile.TemporaryDirectory() as raw, fixtures._Endpoint(
            payload=fixtures.NPCS_TSV_RESULT
        ) as endpoint:
            root = Path(raw)
            config = _endpoint_only(fixtures._config(_write(root / "q.rq", BGP_QUERY), endpoint.url, "N"))
            config.update(scheme="SPARQL_Star_Pure", jar=str(OUR_JAR), response_mode="stream-tsv")
            result = runner._run_cell(config, root / "cell")
            self.assertEqual("ok", result["status"])
            text = (root / "cell" / "prepared-query.rq").read_text(encoding="utf-8")
            self.assertIn("<< ?x <urn:p> <urn:o> >>", text)
            self.assertNotIn("\t?x <urn:p> <urn:o> .", text)
            self.assertEqual("lookup-only", result["protocol"]["layout"])
            self.assertTrue((root / "cell" / "measured-02" / "pp" / "answer-records.jsonl").is_file())


class ColdCacheTest(unittest.TestCase):
    def test_hook_runs_before_every_execution_and_waits_for_the_store(self):
        with tempfile.TemporaryDirectory() as raw, fixtures._Endpoint() as endpoint:
            root = Path(raw)
            log = root / "hook.log"
            config = fixtures._config(_write(root / "q.rq", BGP_QUERY), endpoint.url)
            config.update(
                timing_protocol="endpoint-only",
                cache_mode="cold",
                cache_reset_command='echo "$SPARQLCIRC_RUN_ID $SPARQLCIRC_METHOD" >> "%s"' % log,
            )
            result = runner._run_cell(config, root / "cell")
            self.assertEqual("ok", result["status"])
            self.assertEqual(
                ["warmup-01 B", "measured-01 B", "measured-02 B"],
                log.read_text(encoding="utf-8").splitlines(),
            )
            # one ASK {} readiness probe plus one query per execution
            self.assertEqual(6, endpoint.requests)
            for run in result["runs"]:
                self.assertEqual("ok", run["cache_reset"]["status"])
                self.assertEqual("ok", run["cache_reset"]["ready"]["status"])
            self.assertEqual("cold", result["protocol"]["cache_mode"])

    def test_failing_hook_stops_the_cell_without_querying(self):
        with tempfile.TemporaryDirectory() as raw, fixtures._Endpoint() as endpoint:
            root = Path(raw)
            config = fixtures._config(_write(root / "q.rq", BGP_QUERY), endpoint.url)
            config.update(cache_mode="cold", cache_reset_command="echo boom >&2; exit 7")
            result = runner._run_cell(config, root / "cell")
            self.assertEqual("incomplete", result["status"])
            self.assertEqual(1, len(result["runs"]))
            self.assertEqual("cache-reset-error", result["runs"][0]["status"])
            self.assertEqual(7, result["runs"][0]["cache_reset"]["returncode"])
            self.assertIn("boom", result["runs"][0]["cache_reset"]["detail"])
            self.assertEqual(0, endpoint.requests)

    def test_engine_pid_file_is_reread_for_each_execution(self):
        with tempfile.TemporaryDirectory() as raw, fixtures._Endpoint() as endpoint:
            root = Path(raw)
            pid_file = _write(root / "engine.pid", "%d\n" % os.getpid())
            config = fixtures._config(_write(root / "q.rq", BGP_QUERY), endpoint.url)
            config.update(warmups=0, runs=1, engine_pid_file=str(pid_file))
            result = runner._run_cell(config, root / "cell")
            self.assertEqual("ok", result["status"])
            execution = runner._read_json(root / "cell" / "measured-01" / "execution-config.json")
            self.assertEqual(os.getpid(), execution["engine_pid"])


if __name__ == "__main__":
    unittest.main()
