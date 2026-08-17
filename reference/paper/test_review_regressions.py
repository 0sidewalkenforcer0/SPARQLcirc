"""Focused regressions for resolved whole-repository review findings."""

import os
from pathlib import Path
import importlib.util
import subprocess
import sys
import tempfile
import time
import types
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parent
REFERENCE = HERE.parent
ROOT = REFERENCE.parent
sys.path.insert(0, str(REFERENCE))
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "presentation"))

import compile_portfolio
import circuit_io
import circuit_cache
import ddnnf_wmc
import e3_run
import e6_minus
import e8_wikidata
import e11_per_answer_vs_shared as e11
import factor
import factor_native
import g8_space_memory
import gates
import pqe
import reify_query
import watdiv_run
import bind_manifest
import paper_construction_matrix as pcm


class ReviewRegressionTest(unittest.TestCase):

    def test_minus_edges_are_included_in_structure_count(self):
        circuit = """
<urn:g:m> <http://www.w3.org/1999/02/22-rdf-syntax-ns#type> <urn:circuit:Minus> .
<urn:g:m> <urn:circuit:minuend> <urn:t:left> .
<urn:g:m> <urn:circuit:subtrahend> <urn:t:right> .
"""
        gates_count, edges, _answers, _times, stats = pcm.parse_circuit(
            circuit.splitlines()
        )
        self.assertEqual(1, gates_count)
        self.assertEqual(1, stats["minus"])
        self.assertEqual(2, edges)

    def test_read_once_accepts_boolean_constant_roots(self):
        circuit = {
            "zero": ("const", 0),
            "one": ("const", 1),
        }
        self.assertEqual(0.0, compile_portfolio.prob_read_once(circuit, "zero", {}))
        self.assertEqual(1.0, compile_portfolio.prob_read_once(circuit, "one", {}))
        with self.assertRaisesRegex(ValueError, "invalid Boolean constant"):
            compile_portfolio.prob_read_once({"bad": ("const", 2)}, "bad", {})

    def test_empty_factored_bgp_returns_the_unit_binding(self):
        circuit = gates.Circuit()
        self.assertEqual(
            {frozenset(): circuit.CONST1},
            factor.factored_bgp(circuit, [], {}, set()),
        )

    def test_native_factored_rows_keep_rdf_term_types(self):
        import rdflib

        graph = rdflib.Graph()
        rdf = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
        for suffix, value in (
            ("iri", rdflib.URIRef("urn:value")),
            ("literal", rdflib.Literal("urn:value")),
        ):
            token = rdflib.URIRef("urn:token:" + suffix)
            graph.add((token, rdflib.URIRef(rdf + "subject"), rdflib.URIRef("urn:s")))
            graph.add((token, rdflib.URIRef(rdf + "predicate"), rdflib.URIRef("urn:p")))
            graph.add((token, rdflib.URIRef(rdf + "object"), value))

        factor_native.build(graph, [("urn:s", "urn:p", "?value")], ["value"])
        rows = {
            subject
            for subject, _predicate, _object in graph.triples(
                (None, rdflib.URIRef(factor_native.MV + "value"), None)
            )
        }
        self.assertEqual(2, len(rows))

    def test_d4_rejects_undeclared_parent_and_terminal_arcs(self):
        with self.assertRaisesRegex(ddnnf_wmc.NNFError, "undeclared parents"):
            ddnnf_wmc.evaluate_text("t 1 0\nf 2 0\n99 2 0\n", {})
        with self.assertRaisesRegex(ddnnf_wmc.NNFError, "terminal nodes"):
            ddnnf_wmc.evaluate_text("t 1 0\nf 2 0\n1 2 0\n", {})

    def test_circuit_parser_rejects_conflicting_functional_values(self):
        prefix = (
            "<urn:g:m> <http://www.w3.org/1999/02/22-rdf-syntax-ns#type> "
            "<urn:circuit:Minus> .\n"
        )
        operands = [
            "<urn:g:m> <urn:circuit:minuend> <urn:t:left> .",
            "<urn:g:m> <urn:circuit:minuend> <urn:t:other> .",
            "<urn:g:m> <urn:circuit:subtrahend> <urn:t:right> .",
        ]
        messages = []
        for ordered in (operands, list(reversed(operands))):
            with self.assertRaises(circuit_io.CircuitFormatError) as raised:
                circuit_io.parse(prefix + "\n".join(ordered))
            messages.append(str(raised.exception))
        self.assertEqual(messages[0], messages[1])
        self.assertIn("conflicting values", messages[0])

        with self.assertRaisesRegex(circuit_io.CircuitFormatError, "subtrahend.*missing"):
            circuit_io.parse(prefix + operands[0])

    def test_canonical_terms_decode_iriref_unicode_escapes(self):
        self.assertEqual(
            circuit_io.canon_term("<urn:value:x>"),
            circuit_io.canon_term(r"<urn:value:\u0078>"),
        )
        self.assertEqual(
            circuit_io.canon_term('"x"^^<urn:type:x>'),
            circuit_io.canon_term(r'"x"^^<urn:type:\u0078>'),
        )
        self.assertEqual(
            circuit_io.canon_term("<urn:sk:78>"),
            circuit_io.canon_term(r"<urn:sk:\u0037\u0038>"),
        )

    def test_cache_normalizes_only_an_unescaped_apostrophe(self):
        nonstandard = r'''<urn:s> <urn:p> "O\'Brien" .'''
        plain = '''<urn:s> <urn:p> "O'Brien" .'''
        escaped_backslash = r'''<urn:s> <urn:p> "O\\'Brien" .'''
        self.assertEqual(plain, circuit_cache._normalize_escaped_apostrophe(nonstandard))
        self.assertEqual(
            escaped_backslash,
            circuit_cache._normalize_escaped_apostrophe(escaped_backslash),
        )

    def test_utf8_preprocessors_and_watdiv_argument_validation(self):
        environment = dict(os.environ)
        environment.update({"LC_ALL": "C", "PYTHONUTF8": "0", "PYTHONCOERCECLOCALE": "0"})
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            watdiv_input = root / "watdiv.nt"
            watdiv_output = root / "watdiv.ttls"
            watdiv_input.write_text(
                '<urn:city:München> <urn:label> "€" .\n', encoding="utf-8"
            )
            watdiv_script = REFERENCE / "watdiv" / "reify.py"
            subprocess.run(
                [sys.executable, str(watdiv_script), str(watdiv_input), str(watdiv_output), "--star"],
                check=True,
                capture_output=True,
                env=environment,
            )
            self.assertIn("München", watdiv_output.read_text(encoding="utf-8"))
            invalid = subprocess.run(
                [
                    sys.executable,
                    str(watdiv_script),
                    str(watdiv_input),
                    str(watdiv_output),
                    "--scheme",
                    "SPARQL_Star",
                ],
                capture_output=True,
                env=environment,
            )
            self.assertNotEqual(0, invalid.returncode)

            wikidata_input = root / "wikidata.nt"
            wikidata_output = root / "wikidata-reified.nt"
            wikidata_input.write_text(
                '<urn:München> <http://www.wikidata.org/prop/direct/P1> "€" .\n',
                encoding="utf-8",
            )
            subprocess.run(
                [
                    sys.executable,
                    str(REFERENCE / "wikidata" / "reify_wikidata.py"),
                    str(wikidata_input),
                    str(wikidata_output),
                ],
                check=True,
                capture_output=True,
                env=environment,
            )
            self.assertIn("München", wikidata_output.read_text(encoding="utf-8"))

            tables = root / "tables"
            tables.mkdir()
            (tables / "region.tbl").write_text("0|München|€|\n", encoding="utf-8")
            tpch_output = root / "tpch.nt"
            subprocess.run(
                [
                    sys.executable,
                    str(REFERENCE / "tpch" / "tbl_to_rdf.py"),
                    str(tables),
                    str(tpch_output),
                ],
                check=True,
                capture_output=True,
                env=environment,
            )
            rendered = tpch_output.read_text(encoding="utf-8")
            self.assertIn("München", rendered)
            self.assertIn("€", rendered)

    def test_circuit_io_verifier_survives_an_ascii_console(self):
        environment = dict(os.environ)
        environment["PYTHONIOENCODING"] = "ascii"
        completed = subprocess.run(
            [sys.executable, "-B", "verify_circuit_io.py"],
            cwd=str(REFERENCE),
            capture_output=True,
            env=environment,
        )
        self.assertEqual(0, completed.returncode, completed.stderr.decode("ascii", "replace"))

    def test_explicit_matrix_csv_precedes_committed_inputs(self):
        fake_numpy = types.ModuleType("numpy")
        fake_figstyle = types.ModuleType("figstyle")
        for name in ("SP_BASE", "SP_REIFIED", "SP_NPCS", "SP_CIRCUIT"):
            setattr(fake_figstyle, name, name)
        fake_figstyle.plt = mock.Mock()
        module_path = ROOT / "presentation" / "make_matrix_figures.py"
        spec = importlib.util.spec_from_file_location("review_make_matrix_figures", module_path)
        figures = importlib.util.module_from_spec(spec)
        with mock.patch.dict(
            sys.modules,
            {"numpy": fake_numpy, "figstyle": fake_figstyle},
        ):
            spec.loader.exec_module(figures)

        with tempfile.TemporaryDirectory() as directory:
            explicit = os.path.join(directory, "new.csv")
            Path(explicit).write_text("engine,scale\n", encoding="utf-8")
            with mock.patch.dict(os.environ, {"PCM_MATRIX_CSV": explicit}):
                self.assertEqual([explicit], figures.find_csvs())

    def test_one_shot_watdiv_plan_is_explicitly_flat_and_singular(self):
        completed = mock.Mock(
            stderr="PREFIX c: <urn:circuit:>\nCONSTRUCT {} WHERE {}\n# circuit triples: 0\n"
        )
        with mock.patch.object(watdiv_run.subprocess, "run", return_value=completed) as run:
            self.assertIn("CONSTRUCT", watdiv_run.get_construct("query.rq"))
        command = run.call_args.args[0]
        self.assertIn("--construction=flat", command)
        self.assertTrue(run.call_args.kwargs["check"])

        completed.stderr = (
            "PREFIX c: <urn:circuit:>\nCONSTRUCT {} WHERE {}\n"
            "PREFIX c: <urn:circuit:>\nCONSTRUCT {} WHERE {}\n# circuit triples: 0\n"
        )
        with mock.patch.object(watdiv_run.subprocess, "run", return_value=completed):
            with self.assertRaisesRegex(RuntimeError, "exactly one flat CONSTRUCT"):
                watdiv_run.get_construct("query.rq")

    def test_e3_binding_probe_failure_never_runs_the_unbound_query(self):
        query = "SELECT ?s WHERE { ?s <urn:p> <urn:o> . }"
        with mock.patch.object(e3_run.U, "urlopen", side_effect=OSError("offline")):
            with self.assertRaisesRegex(e3_run.BindingProbeError, "lookup failed"):
                e3_run.bind_source(query)

        with mock.patch.object(e3_run, "BOUND", True), \
             mock.patch.object(
                 e3_run,
                 "bind_source",
                 side_effect=e3_run.BindingProbeError("no safe binding"),
             ), \
             mock.patch.object(e3_run, "get_construct") as construct:
            row = e3_run.run_query("q", query, 1)
        self.assertEqual("err:binding-probe", row["status"])
        construct.assert_not_called()

    def test_e8_uses_one_warmup_and_exactly_runs_measurements(self):
        builds = [
            (10, ["warmup"], False),
            (20, ["first"], False),
            (40, ["second"], False),
        ]
        with mock.patch.object(e8_wikidata, "RUNS", 2), \
             mock.patch.object(e8_wikidata, "plan_wikidata", return_value=(["plan"], True)), \
             mock.patch.object(e8_wikidata, "build", side_effect=builds) as build, \
             mock.patch.object(e8_wikidata, "parse_circuit", return_value=({}, {}, {})), \
             mock.patch.object(e8_wikidata, "counts", return_value=(0, 0, 0, 0, 0)), \
             mock.patch.object(e8_wikidata, "t_string", return_value=0):
            row = e8_wikidata.run_query("single", "q", "SELECT * WHERE {}", False)
        self.assertEqual(3, build.call_count)
        self.assertEqual(30, row["build_ms"])

    def test_e8_rejects_a_missing_query_directory_before_truncating_output(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "existing.csv"
            output.write_text("keep me\n", encoding="utf-8")
            environment = {"E8_QDIR": str(Path(directory) / "missing"), "E8_OUT": str(output)}
            with mock.patch.dict(os.environ, environment, clear=False):
                with self.assertRaisesRegex(ValueError, "E8_QDIR"):
                    e8_wikidata.main()
            self.assertEqual("keep me\n", output.read_text(encoding="utf-8"))

            query_dir = Path(directory) / "queries"
            for category in ("single", "multiple", "optional"):
                (query_dir / category).mkdir(parents=True)
            (query_dir / "single" / "q.sparql").write_text(
                "SELECT * WHERE {}\n", encoding="utf-8"
            )
            with mock.patch.dict(
                os.environ,
                {"E8_QDIR": str(query_dir), "E8_OUT": str(output)},
                clear=False,
            ):
                with self.assertRaisesRegex(ValueError, "multiple, optional"):
                    e8_wikidata.main()
            self.assertEqual("keep me\n", output.read_text(encoding="utf-8"))

    def test_probability_parity_requires_the_complete_answer_key_set(self):
        self.assertAlmostEqual(0.25, e11.probability_parity({"a": 0.5}, {"a": 0.25}))
        with self.assertRaisesRegex(ValueError, "answer-key mismatch"):
            e11.probability_parity({"a": 0.5}, {"b": 0.5})

    def test_binding_manifest_fails_on_http_errors_and_falls_back_on_first_timeout(self):
        failed = mock.Mock(returncode=22, stderr="HTTP 500", stdout="server error")
        with mock.patch.object(bind_manifest.subprocess, "run", return_value=failed) as run:
            with self.assertRaisesRegex(RuntimeError, "curl rc=22"):
                bind_manifest.sparql("repo", "SELECT * WHERE {}")
        self.assertIn("--fail-with-body", run.call_args.args[0])

        with mock.patch.object(
            bind_manifest, "single_triple_candidates", return_value=["urn:c1", "urn:c2"]
        ), mock.patch.object(
            bind_manifest, "full_match", side_effect=bind_manifest.ProbeTimeout("slow")
        ) as full_match, mock.patch.object(
            bind_manifest, "full_pattern_probe", return_value="urn:c1"
        ):
            chosen, note = bind_manifest.probe_binding("T", "query", "urn:placeholder", "repo")
        self.assertEqual("urn:c1", chosen)
        self.assertIn("timed out", note)
        self.assertEqual(1, full_match.call_count)

    def test_reification_uses_fresh_internal_variables_and_preserves_slice(self):
        query = (
            "SELECT ?__t1 ?value WHERE { "
            "?__t1 <urn:p> ?value . OPTIONAL { ?value <urn:q> ?other } "
            "} LIMIT 1 OFFSET 2"
        )
        rewritten = reify_query.reify(query)
        self.assertIn("?__t2 <" + reify_query.RS + "subject> ?__t1", rewritten)
        self.assertNotIn("?__t1 <" + reify_query.RS + "subject> ?__t1", rewritten)
        self.assertIn("LIMIT 1", rewritten)
        self.assertIn("OFFSET 2", rewritten)
        reify_query.parseQuery(rewritten)

    def test_reification_serializes_control_characters_as_valid_sparql_terms(self):
        query = 'SELECT ?s WHERE { ?s <urn:p> "line\\nreturn\\rquote\\\"slash\\\\" . }'
        rewritten = reify_query.reify(query)
        algebra = reify_query.translateQuery(reify_query.parseQuery(rewritten)).algebra
        node = algebra["p"]
        while getattr(node, "name", "") != "BGP":
            node = node["p"]
        objects = [obj for _subject, _predicate, obj in node["triples"]]
        literal = next(obj for obj in objects if isinstance(obj, reify_query.Literal))
        self.assertEqual('line\nreturn\rquote"slash\\', str(literal))

    def test_experiment_plan_runner_checks_exit_deadline_and_cleans_query_file(self):
        completed = mock.Mock(
            stderr=(
                "# --- step 1 ---\n"
                "PREFIX c: <urn:circuit:>\nCONSTRUCT {} WHERE {}\n"
                "# circuit triples: 0\n"
            )
        )
        with mock.patch.object(e6_minus.subprocess, "run", return_value=completed) as run:
            plan = e6_minus.emit_construct_plan("SELECT * WHERE {}", "Standard")
        self.assertEqual(1, len(plan))
        query_file = Path(run.call_args.args[0][-1])
        self.assertFalse(query_file.exists())
        self.assertTrue(run.call_args.kwargs["check"])
        self.assertEqual(e6_minus.PLAN_TIMEOUT, run.call_args.kwargs["timeout"])

        captured = []
        def time_out(command, **_kwargs):
            captured.append(Path(command[-1]))
            raise subprocess.TimeoutExpired(command, 1)

        with mock.patch.object(e6_minus.subprocess, "run", side_effect=time_out):
            with self.assertRaises(subprocess.TimeoutExpired):
                e6_minus.emit_construct_plan("SELECT * WHERE {}", "Standard")
        self.assertFalse(captured[0].exists())

        partial = subprocess.CalledProcessError(
            1,
            ["java"],
            stderr="# --- step 1 ---\nPREFIX c: <urn:circuit:>\nCONSTRUCT {} WHERE {}",
        )
        with mock.patch.object(e6_minus.subprocess, "run", side_effect=partial):
            with self.assertRaises(subprocess.CalledProcessError):
                e6_minus.emit_construct_plan("SELECT * WHERE {}", "Standard")

    @unittest.skipUnless(os.name == "posix", "process-group cleanup is POSIX-specific")
    def test_pqe_jar_deadline_reaps_descendants(self):
        program = (
            "import subprocess,sys,time; "
            "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); "
            "print(child.pid, flush=True); time.sleep(60)"
        )
        with self.assertRaises(subprocess.TimeoutExpired) as raised:
            pqe._run_circuit_process([sys.executable, "-c", program], 0.2)
        child_pid = int(raised.exception.output.decode("ascii").strip())
        deadline = time.monotonic() + 2
        while Path(f"/proc/{child_pid}").exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertFalse(Path(f"/proc/{child_pid}").exists())

    @unittest.skipUnless(os.name == "posix", "RSS sampling uses Linux /proc")
    def test_g8_process_measurement_is_bounded_and_checks_exit_status(self):
        with self.assertRaises(subprocess.TimeoutExpired):
            g8_space_memory._measure_process(
                [sys.executable, "-c", "import time; time.sleep(60)"], 0.2
            )
        with self.assertRaisesRegex(RuntimeError, "rc=7"):
            g8_space_memory._measure_process(
                [sys.executable, "-c", "import sys; sys.exit(7)"], 2
            )

    def test_e8_reports_plan_failure_without_building_a_circuit(self):
        with mock.patch.object(
            e8_wikidata,
            "plan_wikidata",
            side_effect=subprocess.TimeoutExpired(["java"], 1),
        ), mock.patch.object(e8_wikidata, "build") as build:
            row = e8_wikidata.run_query("single", "q", "SELECT * WHERE {}", False)
        self.assertEqual("err:plan:TimeoutExpired", row["status"])
        build.assert_not_called()

    def test_release_docs_cover_the_current_generators_and_planner(self):
        reference_readme = (REFERENCE / "README.md").read_text(encoding="utf-8")
        self.assertIn("CircuitRun` defaults to", reference_readme)
        self.assertNotIn("not a default of the end-to-end Java system", reference_readme)

        figure_readme = (ROOT / "presentation" / "figures" / "final" / "README.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("python3 make_wikidata_figure.py", figure_readme)

        pqe_figure = (ROOT / "presentation" / "make_pqe_figure.py").read_text(encoding="utf-8")
        self.assertIn("e11_summary =", pqe_figure)
        self.assertNotIn("up to 8.2× at 1000 answers", pqe_figure)


if __name__ == "__main__":
    unittest.main()
