from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
import random
import sys
import tempfile
import unittest


REFERENCE = Path(__file__).resolve().parents[1]
if str(REFERENCE) not in sys.path:
    sys.path.insert(0, str(REFERENCE))

import npcs_postprocess as pp


PROV = "finalprovennacevariable"
REQUIRE_CUDD = "--require-cudd" in sys.argv
if REQUIRE_CUDD:
    sys.argv.remove("--require-cudd")


def _uri(value: str) -> dict[str, str]:
    return {"type": "uri", "value": value}


def _literal(value: str) -> dict[str, str]:
    return {"type": "literal", "value": value}


def _response(rows: list[tuple[str, str]], reverse: bool = False) -> bytes:
    bindings = [
        {"x": _uri("urn:answer:" + answer), PROV: _literal(provenance)}
        for answer, provenance in rows
    ]
    if reverse:
        bindings.reverse()
    value = {"head": {"vars": ["x", PROV]}, "results": {"bindings": bindings}}
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


class ParserTest(unittest.TestCase):
    def test_gallery_optional_forms_and_empty_subtrahend(self) -> None:
        examples = {
            "matched": "⊕(⊕((⊗t1,t6,)))",
            "minus": "⊕(⊕((⊖⊕((⊗t1,)),⊕((⊗t6,)),)))",
            "empty-minus": "⊕(⊕((⊖⊕((⊗t2,)),,)))",
        }
        roots = {}
        dag = pp.MultiRootDag()
        for name, source in examples.items():
            raw, raw_root = pp.parse_expression(source, token_pattern=__import__("re").compile(r"^t[0-9]+$"))
            normalized, root = pp.normalize_boolean(raw, raw_root)
            roots[name] = dag.add_answer(name, normalized, root)
        dag.validate()
        expression_root = dag.children(roots["empty-minus"])[0]
        self.assertEqual(dag.nodes[expression_root], pp.DagNode("leaf", "t2"))
        self.assertNotEqual(
            dag.children(roots["matched"]),
            dag.children(roots["minus"]),
        )

    def test_empty_sum_product_and_idempotence(self) -> None:
        cases = {
            "⊕()": ("const", False),
            "(⊗,)": ("const", True),
            "⊕(urn:t:1 urn:t:1)": ("leaf", "urn:t:1"),
        }
        for source, expected in cases.items():
            with self.subTest(source=source):
                raw, raw_root = pp.parse_expression(source)
                normalized, root = pp.normalize_boolean(raw, raw_root)
                self.assertEqual(
                    (normalized.nodes[root].op, normalized.nodes[root].payload), expected
                )

    def test_malformed_and_wrong_token_are_rejected(self) -> None:
        with self.assertRaises(pp.ProvenanceFormatError):
            pp.parse_expression("⊕((⊗urn:t:1,)")
        with self.assertRaises(pp.ProvenanceFormatError):
            pp.parse_expression("(⊖urn:t:1)")
        with self.assertRaises(pp.ProvenanceFormatError):
            pp.parse_expression(
                "⊕((⊗bad-token,))",
                token_pattern=__import__("re").compile(r"^urn:t:[0-9]+$"),
            )
        for malformed in (
            "⊕(urn:t:1,urn:t:2)",
            "(⊗urn:t:1,,urn:t:2,)",
            "(⊗urn:t:1 urn:t:2,)",
            "(⊖urn:t:1 urn:t:2,urn:t:3,)",
            "(⊖urn:t:1,urn:t:2,,)",
        ):
            with self.subTest(malformed=malformed):
                with self.assertRaises(pp.ProvenanceFormatError):
                    pp.parse_expression(malformed)

    def test_deep_unary_aggregation_is_iterative(self) -> None:
        depth = 2500
        source = "⊕(" * depth + "urn:t:1" + ")" * depth
        raw, raw_root = pp.parse_expression(source)
        normalized, root = pp.normalize_boolean(raw, raw_root)
        self.assertEqual(normalized.nodes[root], pp._Node("leaf", "urn:t:1"))


class GlobalHashConsTest(unittest.TestCase):
    ROWS = [
        (
            "a1",
            "⊕((⊗urn:t:1,urn:t:2,)(⊗urn:t:1,urn:t:3,))",
        ),
        ("a2", "⊕((⊗urn:t:1,urn:t:2,))"),
    ]

    def test_query_global_dag_and_all_four_core_sizes(self) -> None:
        result = pp.process_response_bytes(
            _response(self.ROWS), token_regex=r"^urn:t:[0-9]+$"
        )
        metrics = result.metrics
        self.assertEqual((metrics["tree_nodes"], metrics["tree_edges"]), (11, 9))
        self.assertEqual(
            (metrics["normalized_tree_nodes"], metrics["normalized_tree_edges"]),
            (10, 8),
        )
        self.assertEqual((metrics["hc_nodes"], metrics["hc_edges"]), (8, 8))
        self.assertEqual(
            (metrics["hc_expression_nodes"], metrics["hc_expression_edges"]),
            (6, 6),
        )
        self.assertEqual(
            (metrics["answer_root_nodes"], metrics["answer_root_edges"]),
            (2, 2),
        )
        self.assertEqual(metrics["root_count"], 2)
        self.assertEqual(metrics["factor_status"], "not_implemented")
        self.assertIsNone(metrics["factor_ms"])
        roots = {
            json.loads(key)[0][1][1].split(":")[-1]: root
            for key, root in result.dag.roots.items()
        }
        a1_expression = result.dag.children(roots["a1"])[0]
        a2_expression = result.dag.children(roots["a2"])[0]
        self.assertIn(a2_expression, result.dag.children(a1_expression))

    def test_equal_provenance_keeps_distinct_answer_roots(self) -> None:
        result = pp.process_response_bytes(_response([
            ("a1", "⊕(urn:t:1)"),
            ("a2", "⊕(urn:t:1)"),
        ]))
        result.dag.validate()
        roots = list(result.dag.roots.values())
        self.assertEqual(len(set(roots)), 2)
        self.assertEqual(result.dag.children(roots[0]), result.dag.children(roots[1]))
        self.assertEqual(
            (
                result.metrics["hc_expression_nodes"],
                result.metrics["hc_expression_edges"],
                result.metrics["answer_root_nodes"],
                result.metrics["answer_root_edges"],
                result.metrics["hc_nodes"],
                result.metrics["hc_edges"],
            ),
            (1, 0, 2, 2, 3, 2),
        )

    def test_seeded_operator_mix_preserves_every_boolean_function(self) -> None:
        generator = random.Random(20260822)
        tokens = ["urn:t:%d" % index for index in range(1, 9)]

        def expression(depth: int) -> str:
            if depth == 0 or generator.random() < 0.25:
                return generator.choice(tokens)
            operation = generator.choice(("plus", "times", "minus"))
            if operation == "plus":
                children = [expression(depth - 1) for _ in range(generator.randint(1, 3))]
                return "⊕(" + " ".join(children) + ")"
            if operation == "times":
                children = [expression(depth - 1) for _ in range(generator.randint(1, 3))]
                return "(⊗" + ",".join(children) + ",)"
            return "(⊖" + expression(depth - 1) + "," + expression(depth - 1) + ",)"

        rows = [
            ("random-%02d" % index, "⊕(" + expression(3) + ")")
            for index in range(32)
        ]
        result = pp.process_response_bytes(
            _response(rows), token_regex=r"^urn:t:[0-9]+$"
        )
        weights = {token: 0.1 + generator.random() * 0.8 for token in result.dag.tokens()}
        wrapped, _metrics = pp.compile_and_wmc(result, "oracle", weights)

        import compiler

        expression_roots = {
            key: result.dag.children(root)[0]
            for key, root in result.dag.roots.items()
        }
        compiled = compiler.compile_many(
            result.dag.compiler_circuit(),
            expression_roots,
            mode="shared",
            backend="oracle",
            order=result.dag.tokens(),
            record_order_fingerprint=False,
        )
        unwrapped = compiled.wmc_many(weights)
        self.assertEqual(wrapped.keys(), unwrapped.keys())
        for answer_key in wrapped:
            self.assertAlmostEqual(wrapped[answer_key], unwrapped[answer_key])

    def test_response_row_order_does_not_change_canonical_dag(self) -> None:
        first = pp.process_response_bytes(_response(self.ROWS))
        second = pp.process_response_bytes(_response(self.ROWS, reverse=True))
        self.assertEqual(first.dag.document(), second.dag.document())

    def test_shared_compiler_and_probabilities_without_digest_metrics(self) -> None:
        result = pp.process_response_bytes(_response(self.ROWS))
        weights = {token: 0.5 for token in result.dag.tokens()}
        import compiler

        original = compiler._order_fingerprint
        compiler._order_fingerprint = lambda _order: self.fail(
            "the no-fingerprint path attempted to compute an order digest"
        )
        try:
            values, metrics = pp.compile_and_wmc(result, "oracle", weights)
        finally:
            compiler._order_fingerprint = original
        by_answer = {
            json.loads(key)[0][1][1].split(":")[-1]: value
            for key, value in values.items()
        }
        self.assertAlmostEqual(by_answer["a1"], 0.375)
        self.assertAlmostEqual(by_answer["a2"], 0.25)
        self.assertEqual(metrics["manager_count"], 1)
        self.assertFalse(
            any(
                key.lower().endswith(("_sha", "_sha1", "_sha256", "_sha512"))
                or "digest" in key.lower()
                for key in metrics
            )
        )

    def test_per_answer_dags_match_shared_probabilities_without_sharing(self) -> None:
        extracted = pp.extract_response_bytes(_response(self.ROWS))
        shared = pp.build_global_dag(extracted)
        weights = {token: 0.5 for token in shared.dag.tokens()}
        shared_values, _shared_metrics = pp.compile_and_wmc(
            shared, "oracle", weights
        )
        per_answer = pp.compile_answers_per_answer(
            pp.extract_response_bytes(_response(self.ROWS)),
            "oracle",
            probability_values=weights,
        )
        self.assertEqual(shared_values.keys(), per_answer.probabilities.keys())
        for answer_key in shared_values:
            self.assertAlmostEqual(
                shared_values[answer_key], per_answer.probabilities[answer_key]
            )
        self.assertEqual(per_answer.metrics["postprocess_mode"], "per-answer")
        self.assertEqual(per_answer.metrics["compiler"]["mode"], "per-answer")
        self.assertEqual(per_answer.metrics["compiler"]["manager_count"], 2)
        self.assertEqual(
            per_answer.metrics["compiler"]["concurrent_manager_count"], 1
        )
        self.assertEqual(per_answer.metrics["compiler"]["sharing_savings_nodes"], 0)
        self.assertEqual(len(per_answer.answer_metrics), 2)
        self.assertGreater(
            per_answer.metrics["local_hc_total"], shared.metrics["hc_total"]
        )

    def test_per_answer_cudd_matches_shared_cudd_when_available(self) -> None:
        import compiler

        extracted = pp.extract_response_bytes(_response(self.ROWS))
        shared = pp.build_global_dag(extracted)
        weights = {token: 0.5 for token in shared.dag.tokens()}
        try:
            shared_values, _shared_metrics = pp.compile_and_wmc(
                shared, "cudd", weights
            )
            per_answer = pp.compile_answers_per_answer(
                pp.extract_response_bytes(_response(self.ROWS)),
                "cudd",
                probability_values=weights,
            )
        except compiler.BackendUnavailable as exc:
            if REQUIRE_CUDD:
                self.fail("production CUDD backend is unavailable: %s" % exc)
            self.skipTest(str(exc))
        self.assertEqual(shared_values.keys(), per_answer.probabilities.keys())
        for answer_key in shared_values:
            self.assertAlmostEqual(
                shared_values[answer_key], per_answer.probabilities[answer_key]
            )
        self.assertEqual(per_answer.metrics["compiler"]["manager_count"], 2)

    def test_monus_compiles_as_and_not_with_shared_oracle(self) -> None:
        result = pp.process_response_bytes(_response([
            ("minus", "⊕(⊕((⊖⊕((⊗urn:t:1,)),⊕((⊗urn:t:2,)),)))"),
        ]))
        values, _metrics = pp.compile_and_wmc(
            result,
            "oracle",
            {"urn:t:1": 0.5, "urn:t:2": 0.5},
        )
        self.assertAlmostEqual(next(iter(values.values())), 0.25)

    def test_monus_compiles_with_cudd_when_available(self) -> None:
        import compiler

        result = pp.process_response_bytes(_response([
            ("minus", "⊕(⊕((⊖⊕((⊗urn:t:1,)),⊕((⊗urn:t:2,)),)))"),
        ]))
        try:
            values, metrics = pp.compile_and_wmc(
                result,
                "cudd",
                {"urn:t:1": 0.5, "urn:t:2": 0.5},
            )
        except compiler.BackendUnavailable as exc:
            if REQUIRE_CUDD:
                self.fail("production CUDD backend is unavailable: %s" % exc)
            self.skipTest(str(exc))
        self.assertAlmostEqual(next(iter(values.values())), 0.25)
        self.assertEqual(metrics["backend"], "cudd")

    def test_duplicate_answer_rows_are_merged_without_losing_derivations(self) -> None:
        raw = _response([("same", "⊕(urn:t:1)"), ("same", "⊕(urn:t:2)")])
        extracted = pp.extract_response_bytes(raw)
        self.assertEqual(len(extracted.answers), 1)
        self.assertEqual(extracted.metrics["response_row_count"], 2)
        self.assertEqual(extracted.metrics["duplicate_answer_rows"], 1)
        self.assertEqual(extracted.metrics["duplicate_answer_keys"], 1)

        shared = pp.build_global_dag(extracted)
        shared_values, _metrics = pp.compile_and_wmc(
            shared,
            "oracle",
            {"urn:t:1": 0.5, "urn:t:2": 0.5},
        )
        self.assertAlmostEqual(next(iter(shared_values.values())), 0.75)

        per_answer = pp.compile_answers_per_answer(
            pp.extract_response_bytes(raw),
            "oracle",
            probability_values={"urn:t:1": 0.5, "urn:t:2": 0.5},
        )
        self.assertEqual(per_answer.metrics["answer_count"], 1)
        self.assertAlmostEqual(next(iter(per_answer.probabilities.values())), 0.75)

    def test_artifacts_are_complete_and_never_overwritten(self) -> None:
        result = pp.process_response_bytes(_response(self.ROWS))
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            elapsed = pp.persist_postprocess(result, output)
            self.assertGreaterEqual(elapsed, 0.0)
            lines = (output / "npcs-provenance.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 2)
            answer_metrics = (output / "npcs-answer-metrics.jsonl").read_text(
                encoding="utf-8"
            ).splitlines()
            self.assertEqual(len(answer_metrics), 2)
            document = json.loads((output / "npcs-hc-dag.json").read_text(encoding="utf-8"))
            self.assertEqual(document["schema"], "npcs-pp-hc-dag-v2")
            expected = result.dag.document()
            expected["context"] = {}
            self.assertEqual(document, expected)
            with self.assertRaises(FileExistsError):
                pp.persist_postprocess(result, output)


class ResponseBoundaryTest(unittest.TestCase):
    def test_malformed_sparql_json_shapes_and_nonliteral_provenance_are_rejected(self) -> None:
        for value in ([], {"head": None, "results": {}}, {"head": {"vars": []}}):
            with self.subTest(value=value):
                with self.assertRaises(pp.ProvenanceFormatError):
                    pp.process_response_bytes(json.dumps(value).encode("utf-8"))
        value = {
            "head": {"vars": ["x", PROV]},
            "results": {"bindings": [{"x": _uri("urn:a"), PROV: _uri("urn:t:1")}]},
        }
        with self.assertRaises(pp.ProvenanceFormatError):
            pp.process_response_bytes(json.dumps(value).encode("utf-8"))

    def test_generated_provenance_name_and_unbound_answer(self) -> None:
        generated = "__npcs0_finalprovennacevariable"
        value = {
            "head": {"vars": ["x", "optional", generated]},
            "results": {
                "bindings": [
                    {
                        "x": {"type": "literal", "value": "München", "xml:lang": "DE"},
                        generated: _literal("⊕(urn:t:1)"),
                    }
                ]
            },
        }
        result = pp.process_response_bytes(
            json.dumps(value, ensure_ascii=False).encode("utf-8")
        )
        self.assertEqual(result.provenance_variable, generated)
        key = json.loads(result.answers[0].answer_key)
        self.assertEqual(key[0][0], "optional")
        self.assertEqual(key[0][1], ["unbound"])
        self.assertEqual(key[1][1][-1], "de")

    def test_answer_terms_are_structural_and_do_not_delimiter_collide(self) -> None:
        first = {"type": "literal", "value": "a" + pp.US + "b", "datatype": "c"}
        second = {"type": "literal", "value": "a", "datatype": "b" + pp.US + "c"}
        self.assertNotEqual(
            pp._answer_key({"x": first}, ["x"]),
            pp._answer_key({"x": second}, ["x"]),
        )

    def test_cli_persists_raw_provenance_before_rejecting_bad_expression(self) -> None:
        malformed = _response([("bad", "⊕((⊗urn:t:1,)")])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            response = root / "response.json"
            response.write_bytes(malformed)
            output = root / "run"
            with contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    pp.main([
                        str(response),
                        "--out", str(output),
                        "--query-id", "L1-00",
                        "--run-id", "measured-1",
                        "--engine", "test",
                    ])
            self.assertTrue((output / "npcs-provenance.jsonl").is_file())
            self.assertFalse((output / "npcs-hc-dag.json").exists())

    def test_cli_shared_oracle_persists_complete_contextual_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            response = root / "response.json"
            response.write_bytes(_response(GlobalHashConsTest.ROWS))
            output = root / "run"
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(pp.main([
                    str(response),
                    "--out", str(output),
                    "--query-id", "L1-00",
                    "--run-id", "measured-1",
                    "--engine", "test",
                    "--backend", "oracle",
                    "--uniform-probability", "0.5",
                ]), 0)
            metrics = json.loads((output / "metrics.json").read_text(encoding="utf-8"))
            self.assertEqual(metrics["context"]["query_id"], "L1-00")
            self.assertEqual(
                metrics["timing_scope"], "offline_from_complete_response_file"
            )
            self.assertGreaterEqual(metrics["response_read_ms"], 0.0)
            self.assertGreaterEqual(metrics["pp_hc_total_ms"], 0.0)
            self.assertGreaterEqual(metrics["pp_hc_build_wall_ms"], 0.0)
            self.assertAlmostEqual(
                metrics["pp_hc_total_ms"],
                sum(
                    metrics[key]
                    for key in (
                        "response_read_ms",
                        "json_decode_ms",
                        "extract_sort_ms",
                        "raw_provenance_persist_ms",
                        "parse_ms",
                        "boolean_normalize_ms",
                        "global_hash_cons_ms",
                        "dag_validate_ms",
                        "dag_artifact_persist_ms",
                    )
                ),
            )
            self.assertNotIn("full_end_to_end_ms", metrics)
            self.assertEqual(metrics["compiler"]["manager_count"], 1)
            self.assertGreaterEqual(metrics["compiler"]["probability_load_ms"], 0.0)
            self.assertGreaterEqual(metrics["compiler"]["pqe_total_ms"], 0.0)
            self.assertGreaterEqual(metrics["compiler"]["pqe_wall_ms"], 0.0)
            self.assertAlmostEqual(
                metrics["compiler"]["pqe_total_ms"],
                sum(
                    metrics["compiler"][key]
                    for key in (
                        "probability_load_ms",
                        "compile_wall_ms",
                        "wmc_wall_ms",
                        "variable_order_persist_ms",
                        "probability_persist_ms",
                    )
                ),
            )
            self.assertEqual(metrics["token_occurrences_per_answer"]["count"], 2)
            self.assertGreater(metrics["raw_provenance_jsonl_bytes"], 0)
            self.assertGreater(metrics["hc_dag_json_bytes"], 0)
            self.assertGreater(metrics["compiler"]["probability_jsonl_bytes"], 0)
            self.assertTrue((output / "variable-order.txt").is_file())
            self.assertEqual(
                len((output / "probabilities.jsonl").read_text(encoding="utf-8").splitlines()),
                2,
            )

    def test_cli_per_answer_oracle_persists_metrics_and_probabilities(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            response = root / "response.json"
            response.write_bytes(_response(GlobalHashConsTest.ROWS))
            output = root / "run"
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(pp.main([
                    str(response),
                    "--out", str(output),
                    "--query-id", "L1-00",
                    "--run-id", "measured-1",
                    "--engine", "test",
                    "--postprocess-mode", "per-answer",
                    "--backend", "oracle",
                    "--uniform-probability", "0.5",
                ]), 0)
            metrics = json.loads((output / "metrics.json").read_text(encoding="utf-8"))
            self.assertEqual(metrics["context"]["method"], "NPCS+PP-per-answer")
            self.assertEqual(metrics["postprocess_mode"], "per-answer")
            self.assertEqual(metrics["compiler"]["mode"], "per-answer")
            self.assertEqual(metrics["compiler"]["manager_count"], 2)
            self.assertEqual(metrics["compiler"]["concurrent_manager_count"], 1)
            self.assertGreaterEqual(metrics["pp_per_answer_total_ms"], 0.0)
            self.assertGreaterEqual(metrics["compiler"]["pqe_total_ms"], 0.0)
            self.assertFalse((output / "npcs-hc-dag.json").exists())
            self.assertEqual(
                len((output / "npcs-answer-metrics.jsonl").read_text(
                    encoding="utf-8"
                ).splitlines()),
                2,
            )
            self.assertEqual(
                len((output / "probabilities.jsonl").read_text(
                    encoding="utf-8"
                ).splitlines()),
                2,
            )

    def test_cli_per_answer_without_pqe_builds_local_dags_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            response = root / "response.json"
            response.write_bytes(_response(GlobalHashConsTest.ROWS))
            output = root / "run"
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(pp.main([
                    str(response),
                    "--out", str(output),
                    "--query-id", "L1-00",
                    "--run-id", "measured-1",
                    "--engine", "test",
                    "--postprocess-mode", "per-answer",
                    "--backend", "none",
                ]), 0)
            metrics = json.loads((output / "metrics.json").read_text(encoding="utf-8"))
            self.assertEqual(metrics["postprocess_mode"], "per-answer")
            self.assertEqual(metrics["schema"], "npcs-pp-per-answer-metrics-v2")
            self.assertNotIn("compiler", metrics)
            self.assertGreater(metrics["local_hc_total"], 0)
            self.assertIn("stage_peak_memory", metrics)
            self.assertFalse((output / "variable-order.txt").exists())
            self.assertFalse((output / "probabilities.jsonl").exists())


class StreamedInputTest(unittest.TestCase):
    def test_duplicate_streamed_answer_rows_are_merged(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "npcs-provenance.jsonl"
            answer_key = json.dumps(
                [["x", ["literal", "3.0", "http://www.w3.org/2001/XMLSchema#decimal", ""]]],
                separators=(",", ":"),
            )
            records = [
                {"answer_key": answer_key, "provenance": "⊕(urn:t:1)"},
                {"answer_key": answer_key, "provenance": "⊕(urn:t:2)"},
            ]
            source.write_text(
                "".join(json.dumps(item) + "\n" for item in records),
                encoding="utf-8",
            )
            evidence = root / "evidence.json"
            evidence.write_text(json.dumps({
                "schema": "sparql-results-stream-v1",
                "answer_evidence_mode": "npcs-provenance",
                "variables": ["x", PROV],
                "solution_rows": 2,
                "response_bytes": 321,
            }), encoding="utf-8")

            extracted = pp.extract_provenance_jsonl(source, evidence)
            self.assertEqual(len(extracted.answers), 1)
            self.assertEqual(extracted.metrics["response_row_count"], 2)
            self.assertEqual(extracted.metrics["duplicate_answer_rows"], 1)
            self.assertEqual(extracted.metrics["duplicate_answer_keys"], 1)
            result = pp.build_global_dag(extracted)
            values, _metrics = pp.compile_and_wmc(
                result,
                "oracle",
                {"urn:t:1": 0.5, "urn:t:2": 0.5},
            )
            self.assertAlmostEqual(next(iter(values.values())), 0.75)

    def test_cli_consumes_compact_provenance_without_copying_raw_response(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "npcs-provenance.jsonl"
            records = []
            for answer, provenance in GlobalHashConsTest.ROWS:
                records.append({
                    "answer_key": json.dumps(
                        [["x", ["iri", "urn:answer:" + answer]]],
                        separators=(",", ":"),
                    ),
                    "provenance": provenance,
                    "utf8_bytes": len(provenance.encode("utf-8")),
                })
            source.write_text(
                "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in records),
                encoding="utf-8",
            )
            evidence = root / "evidence.json"
            evidence.write_text(json.dumps({
                "schema": "sparql-results-stream-v1",
                "answer_evidence_mode": "npcs-provenance",
                "variables": ["x", PROV],
                "solution_rows": 2,
                "response_bytes": 1234,
            }), encoding="utf-8")
            output = root / "run"
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(pp.main([
                    "--provenance-jsonl", str(source),
                    "--response-evidence", str(evidence),
                    "--out", str(output),
                    "--query-id", "L1-00",
                    "--run-id", "measured-1",
                    "--engine", "test",
                    "--backend", "none",
                    "--token-regex", r"^urn:t:[0-9]+$",
                ]), 0)
            metrics = json.loads((output / "metrics.json").read_text(encoding="utf-8"))
            self.assertEqual(
                "offline_from_compact_streamed_provenance", metrics["timing_scope"]
            )
            self.assertFalse(metrics["raw_response_persisted"])
            self.assertTrue(
                (output / "npcs-provenance.jsonl").samefile(source)
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
