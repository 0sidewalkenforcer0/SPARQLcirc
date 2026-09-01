"""Regression tests for the frozen TPC-H query workload and sharding tools."""

import argparse
import importlib.util
import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest import mock


MODULE_PATH = Path(__file__).resolve().parents[1] / "tpch" / "workload.py"
SPEC = importlib.util.spec_from_file_location("tpch_workload", MODULE_PATH)
tpch_workload = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(tpch_workload)


PARAMETERS = {
    1: ["90"],
    3: ["BUILDING", "1995-03-15"],
    4: ["1994-01-01"],
    5: ["EUROPE", "1994-01-01"],
    6: ["1994-01-01", "0.05", "24"],
    7: ["FRANCE", "GERMANY"],
    8: ["BRAZIL", "AMERICA", "ECONOMY ANODIZED STEEL"],
    10: ["1994-01-01"],
    12: ["MAIL", "SHIP", "1994-01-01"],
    14: ["1994-01-01"],
    15: ["1994-01-01"],
    19: ["Brand#11", "Brand#22", "Brand#33", "1", "10", "20"],
}


class TpchWorkloadTest(unittest.TestCase):
    def test_default_scales_are_the_nine_formal_points(self):
        self.assertEqual(9, len(tpch_workload.DEFAULT_SCALES))
        observed = [float(value) for value in tpch_workload.DEFAULT_SCALES]
        expected = [10 ** (index / 4 - 2) for index in range(0, 9)]
        for left, right in zip(observed, expected):
            self.assertTrue(math.isclose(left, right, rel_tol=1e-14))

    def test_all_nonaggregate_templates_accept_qgen_parameters(self):
        for query_id in tpch_workload.TEMPLATE_IDS:
            template = (
                tpch_workload.DEFAULT_TEMPLATES / ("Q%02d.rq" % query_id)
            ).read_text(encoding="utf-8")
            query, corrections = tpch_workload.instantiate(
                template, query_id, PARAMETERS[query_id]
            )
            self.assertTrue(query.endswith("\n"), "Q%02d" % query_id)
            self.assertIn("SELECT", query, "Q%02d" % query_id)
            if query_id in (8, 19):
                self.assertEqual(1, len(corrections), "Q%02d" % query_id)
            else:
                self.assertEqual([], corrections, "Q%02d" % query_id)

        q6, _ = tpch_workload.instantiate(
            (tpch_workload.DEFAULT_TEMPLATES / "Q06.rq").read_text(encoding="utf-8"),
            6,
            PARAMETERS[6],
        )
        self.assertIn("0.04", q6)
        self.assertIn("0.06", q6)
        q19, _ = tpch_workload.instantiate(
            (tpch_workload.DEFAULT_TEMPLATES / "Q19.rq").read_text(encoding="utf-8"),
            19,
            PARAMETERS[19],
        )
        self.assertIn("Brand#11", q19)
        self.assertNotIn("Brand13", q19)

    def test_audit_requires_old_rdfstar_row_lookups(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            entries = []
            for query_id in tpch_workload.TEMPLATE_IDS:
                artifact = root / ("Q%02d" % query_id)
                artifact.mkdir()
                (artifact / "base.rq").write_text(
                    "SELECT ?row WHERE { ?row <urn:p> ?value . }\n", encoding="utf-8"
                )
                (artifact / "row.rq").write_text(
                    "SELECT ?row WHERE {\n"
                    "  << ?row <http://www.w3.org/1999/02/22-rdf-syntax-ns#type> ?type >> "
                    "<http://example.org/occurrenceOf> ?token .\n"
                    "  ?row <urn:p> ?value .\n}\n",
                    encoding="utf-8",
                )
                (artifact / "sparqlprov.rq").write_text(
                    "SELECT ?row ?prov_sum ?prov_sum_statement WHERE {\n"
                    "  ?row <urn:p> ?value .\n"
                    "  BIND (?row AS ?prov_sum_statement)\n"
                    "  BIND (IRI('http://example.org/p_Sum/') AS ?prov_sum)\n"
                    "}\n",
                    encoding="utf-8",
                )
                (artifact / "qgen.stdout").write_text("parameter\n", encoding="utf-8")
                (artifact / "parameters.json").write_text("{}\n", encoding="utf-8")
                entries.append({
                    "query_id": "sf1-Q%02d-q001" % query_id,
                    "base_query": str((artifact / "base.rq").relative_to(root)),
                    "row_inline_query": str((artifact / "row.rq").relative_to(root)),
                    "sparqlprov_query": str(
                        (artifact / "sparqlprov.rq").relative_to(root)
                    ),
                    "qgen_stdout": str((artifact / "qgen.stdout").relative_to(root)),
                    "parameter_record": str((artifact / "parameters.json").relative_to(root)),
                })
            manifest = {
                "schema": tpch_workload.SCHEMA,
                "scale_factors": ["1"],
                "instances_per_template_scale": 1,
                "rdf_star_profile": "RDF-star 1.1 quoted triple plus occurrenceOf",
                "rdf_star_12_permitted": False,
                "provenance_granularity": "one token per TPC-H row rdf:type marker",
                "query_layout": "hybrid-inline, one occurrence lookup per distinct row subject",
                "entries": entries,
            }
            manifest_path = root / "manifest.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            row_query = (root / "Q01" / "row.rq").read_text(encoding="utf-8")
            with mock.patch.object(tpch_workload.inline_rows, "inline_rows", return_value=row_query):
                self.assertEqual("ok", tpch_workload.audit_manifest(manifest_path)["status"])
            bad = root / "Q01" / "row.rq"
            bad.write_text(
                "SELECT ?row WHERE {\n"
                "  << ?row <http://www.w3.org/1999/02/22-rdf-syntax-ns#type> ?type >> "
                "<http://example.org/occurrenceOf> ?token .\n"
                "  ?r <http://www.w3.org/1999/02/22-rdf-syntax-ns#reifies> ?term .\n"
                "}\n",
                encoding="utf-8",
            )
            with mock.patch.object(tpch_workload.inline_rows, "inline_rows", return_value=row_query):
                with self.assertRaisesRegex(tpch_workload.WorkloadError, "RDF 1.2"):
                    tpch_workload.audit_manifest(manifest_path)

    def test_shards_each_scale_engine_batch_once(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            entries = [
                {"query_id": "sf%s-Q01-q001" % scale.replace(".", "p"),
                 "scale_factor": scale, "instance": "q001"}
                for scale in tpch_workload.DEFAULT_SCALES
            ]
            manifest = {
                "schema": tpch_workload.SCHEMA,
                "scale_factors": list(tpch_workload.DEFAULT_SCALES),
                "instances_per_template_scale": 0,
                "rdf_star_profile": "RDF-star 1.1 quoted triple plus occurrenceOf",
                "rdf_star_12_permitted": False,
                "provenance_granularity": "one token per TPC-H row rdf:type marker",
                "query_layout": "hybrid-inline, one occurrence lookup per distinct row subject",
                "entries": [],
            }
            # Sharding only needs the scale/query mapping after a successful audit.
            manifest_path = root / "manifest.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            output = root / "shards.json"
            args = argparse.Namespace(
                manifest=manifest_path,
                workers=("a", "b", "c"),
                instances=tpch_workload.DEFAULT_INSTANCES,
                engines=tpch_workload.DEFAULT_ENGINES,
                methods=tpch_workload.DEFAULT_METHODS,
                out=output,
            )
            with mock.patch.object(
                tpch_workload, "audit_manifest", return_value={"status": "ok"}
            ):
                # Restore minimal entries after bypassing the artifact-focused audit.
                manifest["entries"] = entries
                manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
                result = tpch_workload.shard_manifest(args)

        batches = [batch for worker in result["workers"] for batch in worker["batches"]]
        self.assertEqual(18, len(batches))
        self.assertEqual(18, len({batch["batch_id"] for batch in batches}))
        self.assertTrue(all(batch["cell_count"] == 6 for batch in batches))
        self.assertEqual(["q001"], result["instances"])


if __name__ == "__main__":
    unittest.main()
