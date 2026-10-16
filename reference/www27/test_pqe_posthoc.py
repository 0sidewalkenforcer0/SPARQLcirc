#!/usr/bin/env python3
"""Regression for the E2 PQE runners on real toy artifacts.

The artifacts are produced by the production code (our jar, npcs.LocalSelect,
npcs_postprocess.py, CircuitRun) for a MINUS query over the toy data of
test_npcs_pqe_toy.py, then laid out as WatDiv E1 cells and as Wikidata
run_cell.py cells.  The test runs

* reference/www27/watdiv_pqe_posthoc.py run/compare (N and C-flat cells),
* reference/wdbench/run_pqe.py on an N-paired and a C-flat cell,
* reference/wdbench/run_pqe_batch.py --method N-shared --method C-flat,
* reference/wdbench/summarize_pqe.py on the result,

with the pure-Python ``oracle`` backend, and checks the probabilities against
possible-world enumeration.  With WWW27_D4 (and, for a d4 v1 build,
D4V2_DDNNF_CMD='{d4} {cnf} -dDNNF -pv=NO -out={out}') the d4 backend is run too.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest import mock


HERE = Path(__file__).resolve().parent
REFERENCE = HERE.parent
ROOT = REFERENCE.parent
for _path in (HERE, REFERENCE, REFERENCE / "wdbench", REFERENCE / "paper"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import event_probabilities
import npcs_postprocess
import run_pqe
import run_pqe_batch
import summarize_pqe
import test_npcs_pqe_toy as toy
import watdiv_pqe_posthoc as posthoc


RUN_IDS = ("warmup-01",) + tuple("measured-%02d" % index for index in range(1, 6))
CASE = "minus"
D4 = Path(os.environ["WWW27_D4"]) if os.environ.get("WWW27_D4") else None


def _json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=1), encoding="utf-8")


def _probabilities(path: Path) -> dict:
    return {
        row["answer_key"]: float(row["probability"])
        for row in map(json.loads, path.read_text(encoding="utf-8").splitlines())
    }


@unittest.skipUnless(toy.JAR.is_file(), "needs engine/target/npcs-rewrite.jar")
class PqePosthocTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        root = Path(cls.temporary.name)
        cls.root = root
        data = root / "toy.ttls"
        data.write_text(toy.data_text(), encoding="utf-8")
        query = root / "q.rq"
        query.write_text(toy.CASES[CASE][1] + "\n", encoding="utf-8")
        rewritten = root / "n.rq"
        rewritten.write_bytes(toy.java("-jar", str(toy.JAR), "rewrite", "SPARQL_Star_Pure",
                                       "path", str(query)))
        response = toy.java("-cp", str(toy.JAR), "npcs.LocalSelect", str(data), str(rewritten))
        result = npcs_postprocess.process_response_bytes(response, token_regex=r"^urn:t:[0-9]+$")
        cls.pp = root / "pp"
        npcs_postprocess.persist_postprocess(result, cls.pp, {"query_id": "M1-00"})
        cls.circuit = root / "circuit.nt"
        cls.circuit.write_bytes(toy.java(
            "-cp", str(toy.JAR), "npcs.circuit.CircuitRun", "--construction=flat",
            "SPARQL_Star_Pure", str(data), str(query)))
        weights = event_probabilities.event_weights(sorted(toy.EVENTS), 42)
        cls.truth = toy.ground_truth(CASE, weights)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    # ----------------------------------------------------------- WatDiv E1 cells

    def _watdiv_cells(self, root: Path) -> Path:
        cells = root / "e1" / "cells" / "M1" / "00"
        for method in ("N", "C-flat"):
            runs = []
            for run_id in RUN_IDS:
                run = cells / method / run_id
                if method == "N":
                    shutil.copytree(self.pp, run / "pp")
                else:
                    run.mkdir(parents=True)
                    shutil.copyfile(self.circuit, run / "circuit.nt")
                runs.append({
                    "run_id": run_id,
                    "phase": run_id.split("-")[0],
                    "status": "ok",
                    "endpoint": {"status": "ok", "endpoint": {"endpoint_only_ms": 10.0,
                                                              "legacy_endpoint_e2e_ms": 300.0}},
                    "offline": {"status": "ok", "metrics": {"pp_hc_build_wall_ms": 2.0}},
                })
            _json(cells / method / "cell.json", {
                "schema": "watdiv-brnc-cell-v1", "status": "ok", "query_id": "M1-00",
                "engine": "toy", "method": method, "scheme": "SPARQL_Star_Pure",
                "workload": "watdiv",
                "protocol": {"measured_runs": 5, "timing_protocol": "endpoint-only"},
                "runs": runs,
            })
        return root / "e1"

    def _posthoc(self, e1: Path, out: Path, backend: str) -> None:
        arguments = ["run", "--cells", str(e1), "--out", str(out), "--backend", backend]
        if backend == "d4":
            arguments += ["--d4", str(D4)]
        with mock.patch("sys.stdout"):
            self.assertEqual(0, posthoc.main(arguments))

    def test_watdiv_posthoc_matches_possible_worlds_for_n_and_c(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            e1 = self._watdiv_cells(root)
            backends = ["oracle"] + (["d4"] if D4 else [])
            for backend in backends:
                self._posthoc(e1, root / backend, backend)
                for method, parse_merge in (("N", 2.0), ("C-flat", 0.0)):
                    cell = json.loads((root / backend / "cells" / "M1" / "00" / method / backend /
                                       "cell.json").read_text(encoding="utf-8"))
                    self.assertEqual("ok", cell["status"], method)
                    stages = cell["stage_medians_ms"]
                    self.assertEqual(10.0, stages["endpoint_ms"])
                    self.assertEqual(parse_merge, stages["parse_merge_ms"])
                    run = cell["runs"][1]
                    self.assertAlmostEqual(
                        run["full_pipeline_ms"],
                        10.0 + parse_merge + run["circuit_parsing_ms"] + run["compilation_and_wmc_ms"],
                        places=5,
                    )
                    values = _probabilities(root / backend / "cells" / "M1" / "00" / method /
                                            backend / "measured-01" / "pqe" / "probabilities.jsonl")
                    self.assertEqual(sorted(self.truth), sorted(toy.nonzero(values)))
                    for key, value in self.truth.items():
                        self.assertAlmostEqual(value, values[key], delta=1e-6)
            parity = posthoc.compare([root / backend for backend in backends])
            self.assertEqual([], parity["disagreements"])
            self.assertEqual(2 * len(backends) - 1, parity["checked_pairs"])

    # ------------------------------------------------------- Wikidata run_cell cells

    def _wikidata_cells(self, root: Path) -> dict:
        protocol = {"warmups": 1, "measured_runs": 5, "primary_statistic": "median"}
        n_dir = root / "wd" / "cells" / "single-01" / "N_paired"
        runs, common = [], []
        for run_id in RUN_IDS:
            shutil.copytree(self.pp, n_dir / run_id / "N-shared" / "pp")
            runs.append({
                "run_id": run_id, "phase": run_id.split("-")[0], "status": "ok",
                "endpoint_observed_wall_ms": 400.0, "component_e2e_ms": 450.0,
                "offline": {"status": "ok", "metrics": {"pp_hc_build_wall_ms": 30.0}},
                "artifact": "%s/N-shared" % run_id,
            })
            common.append({"run_id": run_id, "endpoint": {
                "status": "ok", "endpoint": {"endpoint_only_ms": 12.0}}})
        _json(n_dir / "cell.json", {
            "schema": "wikidata-method-cell-v1", "physical_method": "N-paired",
            "query_id": "single-01", "status": "ok", "protocol": protocol,
            "methods": {"N-shared": {"status": "ok", "runs": runs},
                        "N-per-answer": {"status": "ok", "runs": []}},
            "common_runs": common,
        })
        c_dir = root / "wd" / "cells" / "single-01" / "C_flat"
        c_runs = []
        for run_id in RUN_IDS:
            (c_dir / run_id).mkdir(parents=True)
            shutil.copyfile(self.circuit, c_dir / run_id / "circuit.nt")
            c_runs.append({"run_id": run_id, "phase": run_id.split("-")[0], "status": "ok",
                           "endpoint": {"status": "ok"}, "endpoint_observed_wall_ms": 900.0})
        _json(c_dir / "cell.json", {
            "schema": "wikidata-method-cell-v1", "physical_method": "C-flat",
            "query_id": "single-01", "status": "ok", "protocol": protocol, "runs": c_runs,
        })
        return {"N": n_dir / "cell.json", "C": c_dir / "cell.json"}

    def test_wikidata_run_pqe_accepts_npcs_cells(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            cells = self._wikidata_cells(root)
            for label, cell in cells.items():
                out = root / ("pqe-" + label)
                with mock.patch("sys.stdout"):
                    run_pqe.main(["--source-root", str(ROOT), "--cell", str(cell), "--out", str(out),
                                  "--backend", "oracle"])
                result = json.loads((out / "cell.json").read_text(encoding="utf-8"))
                self.assertEqual("ok", result["status"], label)
                values = _probabilities(out / "measured-01" / "pqe" / "probabilities.jsonl")
                for key, value in self.truth.items():
                    self.assertAlmostEqual(value, values[key], delta=1e-9)
            n = json.loads((root / "pqe-N" / "cell.json").read_text(encoding="utf-8"))
            self.assertEqual("N-shared", n["method"])
            stages = n["stage_medians_ms"]
            self.assertEqual(400.0, stages["endpoint_ms"])
            self.assertEqual(12.0, stages["endpoint_request_ms"])
            self.assertEqual(50.0, stages["parse_merge_ms"])
            for run in n["runs"]:
                self.assertAlmostEqual(run["compilation_and_wmc_ms"],
                                       run["compile_ms"] + run["wmc_ms"], places=5)

            manifest = root / "manifest.json"
            _json(manifest, {"schema": "wikidata-141-workload-v1", "entries": [{
                "query_id": "single-01", "category": "single", "shard": 0,
                "applicable_methods": ["N-per-answer", "N-shared", "C-flat", "C-factorised"],
            }]})
            batch_out = root / "batch"
            with mock.patch("sys.stdout"):
                run_pqe_batch.main([
                    "--manifest", str(manifest), "--construction-cells", str(root / "wd"),
                    "--source-root", str(ROOT), "--out", str(batch_out), "--backend", "oracle",
                    "--method", "N-shared", "--method", "C-flat",
                ])
            config = json.loads((batch_out / "batch-config.json").read_text(encoding="utf-8"))
            self.assertEqual(["N-shared", "C-flat"], config["methods"])
            # only the selected methods are expected; C-factorised was not selected
            self.assertEqual([], config["construction_cells_absent_or_failed"])
            collected = summarize_pqe.collect([batch_out])
            self.assertEqual({("single-01", "C-flat", "oracle"), ("single-01", "N-shared", "oracle")},
                             set(collected))


if __name__ == "__main__":
    unittest.main()
