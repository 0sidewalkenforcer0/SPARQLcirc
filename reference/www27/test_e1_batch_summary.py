#!/usr/bin/env python3
"""Loopback regression for the E1 batch (prepare and run) and its summary.

A two-query workload (one BGP, one OPTIONAL instance) is run through the real
runner subprocesses against a loopback endpoint.  The workload audit and the
dataset audit are replaced by fixtures; everything else is the production
code path.  Requires rdflib and engine/target/npcs-rewrite.jar; the N-orig
BGP cells additionally need $NPCS_ORIG_JAR.
"""
from __future__ import annotations

import csv
import json
import os
from pathlib import Path
import statistics
import sys
import tempfile
import unittest
from unittest import mock


HERE = Path(__file__).resolve().parent
REFERENCE = HERE.parent
PAPER = REFERENCE / "paper"
ROOT = REFERENCE.parent
for _path in (HERE, REFERENCE, PAPER):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import test_watdiv10m_runner as fixtures
import watdiv_e1_batch as batch
import watdiv_e1_summarize as summary


OUR_JAR = ROOT / "engine" / "target" / "npcs-rewrite.jar"
ORIG_JAR = Path(os.environ["NPCS_ORIG_JAR"]) if os.environ.get("NPCS_ORIG_JAR") else None
PREFIX = "PREFIX wsdbm: <http://db.uwaterloo.ca/~galuc/wsdbm/>\n"
QUERIES = {
    ("L1", "00"): PREFIX + "SELECT ?x WHERE { ?x wsdbm:likes wsdbm:Product1 . }\n",
    ("O1", "00"): PREFIX + (
        "SELECT ?x ?y WHERE { ?x wsdbm:likes wsdbm:Product1 . "
        "OPTIONAL { ?x wsdbm:friendOf ?y } }\n"
    ),
}


def _workload(root: Path):
    rows = []
    for (template, instance), text in QUERIES.items():
        relative = Path("queries") / template / ("%s.rq" % instance)
        (root / relative).parent.mkdir(parents=True, exist_ok=True)
        (root / relative).write_text(text, encoding="utf-8")
        rows.append({
            "query_id": "%s-%s" % (template, instance),
            "template": template,
            "instance": instance,
            "query_file": str(relative),
        })
    return rows


@unittest.skipUnless(OUR_JAR.is_file(), "needs engine/target/npcs-rewrite.jar")
class E1BatchTest(unittest.TestCase):
    def _args(self, *extra):
        return batch._parser().parse_args(list(extra))

    def test_prepare_then_run_then_summarize(self):
        methods = ["B", "B-mixed", "R", "N"] + (["N-orig"] if ORIG_JAR else [])
        with tempfile.TemporaryDirectory() as raw, fixtures._Endpoint(
            payload=fixtures.NPCS_TSV_RESULT
        ) as endpoint:
            root = Path(raw)
            workload_root = root / "workload"
            rows = _workload(workload_root)
            mixed = root / "data" / "mixed.ttls"
            mixed.parent.mkdir()
            mixed.write_text("", encoding="utf-8")
            dataset = root / "data" / "dataset.json"
            dataset.write_text(json.dumps({
                "scale_factor": "1",
                "rdf_star_profile": "RDF-star 1.1 quoted triple plus occurrenceOf",
                "layouts": {"mixed": {"path": "mixed.ttls"}},
            }), encoding="utf-8")
            common = ["--workload", str(workload_root), "--layout", "lookup-only",
                      "--jar", str(OUR_JAR), "--template", "L1", "--template", "O1",
                      "--instance", "00"]
            for method in methods:
                common += ["--method", method]
            if ORIG_JAR:
                common += ["--orig-jar", str(ORIG_JAR)]
            with mock.patch.object(batch.published, "_read_workload_rows", return_value=rows), \
                    mock.patch.object(batch.prepare_data, "audit", return_value={}), \
                    mock.patch.object(summary.published, "_read_workload_rows", return_value=rows):
                report = batch.prepare(self._args("prepare", "--out", str(root / "prep"), *common))
                self.assertEqual([], report["prepare_errors"])
                if ORIG_JAR:
                    self.assertEqual(1, report["n_orig"]["supported"])
                    self.assertEqual(1, report["n_orig"]["unsupported"])
                    self.assertEqual(1, report["n_orig"]["equivalent_to_reimplementation"])
                    self.assertEqual({"OPTIONAL": 1}, report["n_orig"]["refusal_reasons"])
                pure_text = (root / "prep" / "prepared" / "L1" / "00" / "N" /
                             "prepared-query.rq").read_text(encoding="utf-8")
                self.assertIn("<< ?x <http://db.uwaterloo.ca/~galuc/wsdbm/likes>", pure_text)
                self.assertNotIn("\t?x <http://db.uwaterloo.ca/~galuc/wsdbm/likes>", pure_text)

                out = root / "run"
                result = batch.run(self._args(
                    "run", "--out", str(out), "--dataset", str(dataset),
                    "--engine", "loopback", "--base-endpoint", endpoint.url,
                    "--mixed-endpoint", endpoint.url, "--timing-protocol", "endpoint-only",
                    *common,
                ))
                self.assertEqual("terminal", result["status"])
                counts = result["status_counts"]
                self.assertEqual(len(methods) * 2 - (1 if ORIG_JAR else 0), counts.get("ok"))
                if ORIG_JAR:
                    self.assertEqual(1, counts["unsupported"])
                    refused = json.loads((out / "cells" / "O1" / "00" / "N-orig" / "cell.json")
                                         .read_text(encoding="utf-8"))
                    self.assertIn("OPTIONAL", refused["unsupported_reason"])
                    self.assertEqual([], refused["runs"])
                config = json.loads((out / "batch-config.json").read_text(encoding="utf-8"))
                self.assertEqual("SPARQL_Star_Pure", config["schemes"]["N"])
                self.assertEqual("endpoint-only", config["protocol"]["timing_protocol"])
                # resuming reuses every terminal cell
                again = batch.run(self._args(
                    "run", "--out", str(out), "--dataset", str(dataset),
                    "--engine", "loopback", "--base-endpoint", endpoint.url,
                    "--mixed-endpoint", endpoint.url, "--timing-protocol", "endpoint-only",
                    *common,
                ))
                self.assertEqual(result, again)

                summarized = summary.summarize(
                    workload_root, [out], templates=("L1", "O1"), instances=("00",)
                )
            by_key = {(row["template"], row["raw_method"]): row
                      for row in summarized["template_rows"]}
            b = by_key[("L1", "B")]
            cell = json.loads((out / "cells" / "L1" / "00" / "B" / "cell.json").read_text(
                encoding="utf-8"))
            expected = statistics.median(
                run["endpoint"]["endpoint"]["endpoint_only_ms"]
                for run in cell["runs"] if run["phase"] == "measured"
            )
            self.assertAlmostEqual(expected, b["successful_median_ms"], places=5)
            n = by_key[("L1", "N")]
            self.assertAlmostEqual(
                n["successful_median_ms"] / b["successful_median_ms"], n["ratio_vs_B"], places=5
            )
            self.assertEqual("lookup-only", n["layout"])
            if ORIG_JAR:
                self.assertEqual("unsupported", by_key[("O1", "N-orig")]["plot_kind"])
                self.assertEqual(1, by_key[("O1", "N-orig")]["unsupported"])
            ratios = {(row["raw_method"], row["reference"]): row for row in summarized["ratio_rows"]}
            n_vs_b = ratios[("N", "B")]
            self.assertEqual(2, n_vs_b["templates_with_ratio"])
            per_template = [by_key[(template, "N")]["ratio_vs_B"] for template in ("L1", "O1")]
            self.assertAlmostEqual(statistics.median(per_template),
                                   n_vs_b["median_of_template_ratios"], places=5)
            self.assertEqual([], summarized["answer_parity"]["unequal_pairs"])
            self.assertGreater(summarized["answer_parity"]["checked_pairs"], 0)

            summary.write_outputs(summarized, root / "s.json", root / "s.csv", root / "r.csv")
            with (root / "s.csv").open(encoding="utf-8") as handle:
                header = next(csv.reader(handle))
            self.assertIn("successful_median_ms", header)
            self.assertNotIn("successful_mean_ms", header)


if __name__ == "__main__":
    unittest.main()
