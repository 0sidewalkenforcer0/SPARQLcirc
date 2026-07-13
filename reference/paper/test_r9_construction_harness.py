"""Completely offline regressions for the R9.2 timing/parity harness."""

import csv
import contextlib
import io
import json
import multiprocessing
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
REF = os.path.dirname(HERE)
sys.path.insert(0, REF)
sys.path.insert(0, HERE)
import circuit_io
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


@unittest.skipUnless(
    "fork" in multiprocessing.get_all_start_methods(),
    "offline process-injection tests require fork",
)
class HardDeadlineTests(unittest.TestCase):
    def test_whole_cell_times_out_while_response_keeps_streaming(self):
        # Every chunk arrives far sooner than the socket timeout.  A per-socket
        # timeout therefore never fires; only the parent cell deadline can stop it.
        stream = fake_urlopen(interval=0.015, chunk_count=500)
        with mock.patch.object(pcm.U, "urlopen", stream):
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
        with mock.patch.object(pcm.U, "urlopen", stream), mock.patch.object(
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
        with mock.patch.object(pcm.U, "urlopen", stream):
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
        with mock.patch.object(pcm.U, "urlopen", stream), mock.patch.object(
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
        with mock.patch.object(pcm.U, "urlopen", stream), mock.patch.object(
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
        self.assertAlmostEqual(
            result["construct_total"][0],
            result["samples"][0] + result["c_parse"][0],
            places=2,
        )


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


class CheckpointTests(unittest.TestCase):
    def _row(self, method="B", status="ok", samples=None, template="L1"):
        evidence = pcm.multiset_evidence(
            pcm.normalized_csv_multiset("x\nurn:a\n")
        )
        row = {name: "" for name in pcm.COLS}
        row.update(
            {
                "commit": "test",
                "engine": "graphdb",
                "engine_version": "test",
                "scale": "10M",
                "class": "L",
                "template": template,
                "instance": "00",
                "query_sha256": "abc",
                "method": method,
                "implementation": "test",
                "status": status,
                "warmups": "0",
                "runs": "1",
                "timeout_s": "1",
                "rewrite_ms": "1.25",
                "samples_json": json.dumps([1.0] if samples is None else samples),
                "protocol": pcm.PROTOCOL,
                **evidence,
                "notes": pcm.pack_note("", evidence, cell_wall_ms=2.0),
            }
        )
        return row

    def test_latest_failure_is_retryable_and_overdeadline_ok_is_invalid(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "checkpoint.csv")
            with open(path, "w", newline="") as fh:
                writer = csv.DictWriter(fh, fieldnames=pcm.COLS)
                writer.writeheader()
                writer.writerow(self._row())
            key = ("graphdb", "10M", "L", "L1", "00", "abc", "B")
            self.assertIn(key, pcm.load_done(path, warmups=0, runs=1, timeout=1))

            with open(path, "a", newline="") as fh:
                writer = csv.DictWriter(fh, fieldnames=pcm.COLS)
                writer.writerow(self._row(status="err:network"))
                writer.writerow(self._row(template="L2", samples=[1001.0]))
            done = pcm.load_done(path, warmups=0, runs=1, timeout=1)
            self.assertNotIn(key, done)  # latest transient failure can be retried
            over_key = ("graphdb", "10M", "L", "L2", "00", "abc", "B")
            self.assertNotIn(over_key, done)  # never retain >timeout as ok

            # A torn physical record is ignored instead of poisoning all resumes.
            with open(path, "a") as fh:
                fh.write('"unterminated')
            list(pcm._checkpoint_rows(path))

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
            self.assertEqual(summarize_brnc._timing_cells(source), {})
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
            formal.update(
                {
                    "warmups": "1",
                    "runs": str(summarize_brnc.FORMAL_RUNS),
                    "timeout_s": str(summarize_brnc.FORMAL_TIMEOUT),
                    "samples_json": json.dumps(
                        [1.0] * summarize_brnc.FORMAL_RUNS
                    ),
                    "notes": pcm.pack_note("", pcm.multiset_evidence(
                        pcm.normalized_csv_multiset("x\nurn:a\n")
                    ), cell_wall_ms=10.0),
                }
            )
            with open(source, "w", newline="") as fh:
                writer = csv.DictWriter(fh, fieldnames=pcm.COLS)
                writer.writeheader()
                writer.writerow(formal)
            cells = summarize_brnc._timing_cells(source)
            self.assertIn(("graphdb", "10M", "L", "L1", "00"), cells)


class MatrixConfigurationTests(unittest.TestCase):
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
        override = pcm.build_engine_registry(
            {"PCM_QLEVER_100M_BASE_ENDPOINT": "http://example.invalid/base"}
        )
        self.assertEqual(
            override["qlever"]["100M"]["base"], "http://example.invalid/base"
        )

    def test_parity_merge_retains_other_engine_scale_combinations(self):
        def row(engine, scale, note):
            result = {name: "" for name in parity.COLS}
            result.update(
                {
                    "protocol": pcm.PROTOCOL,
                    "engine": engine,
                    "scale": scale,
                    "class": "L",
                    "template": "L1",
                    "instance": "00",
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
            with open(path, newline="") as fh:
                rows = list(csv.DictReader(fh))
            self.assertEqual(len(rows), 3)
            by_key = {(r["engine"], r["scale"]): r for r in rows}
            self.assertEqual(by_key[("graphdb", "10M")]["notes"], "g-new")
            self.assertIn(("oxigraph", "100M"), by_key)
            self.assertIn(("qlever", "10M"), by_key)


if __name__ == "__main__":
    unittest.main()
