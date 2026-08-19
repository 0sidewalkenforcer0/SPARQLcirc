from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
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
        self.assertEqual(dag.nodes[roots["empty-minus"]], pp.DagNode("leaf", "t2"))
        self.assertNotEqual(roots["matched"], roots["minus"])

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
        self.assertEqual((metrics["hc_nodes"], metrics["hc_edges"]), (6, 6))
        self.assertEqual(metrics["root_count"], 2)
        self.assertEqual(metrics["factor_status"], "not_implemented")
        self.assertIsNone(metrics["factor_ms"])
        roots = list(result.dag.roots.values())
        self.assertIn(2, roots)  # the complete t1&t2 subtree is a root and shared by a1

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

    def test_duplicate_answer_key_is_rejected(self) -> None:
        raw = _response([("same", "⊕(urn:t:1)"), ("same", "⊕(urn:t:2)")])
        with self.assertRaises(pp.ProvenanceFormatError):
            pp.process_response_bytes(raw)

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
            self.assertEqual(document["schema"], "npcs-pp-hc-dag-v1")
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
