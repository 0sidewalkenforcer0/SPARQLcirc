"""Regression tests for the measured-only TPC-H Figure 7 table."""

import importlib.util
from pathlib import Path
import unittest


MODULE_PATH = Path(__file__).resolve().parents[1] / "tpch" / "build_figure7_data.py"
SPEC = importlib.util.spec_from_file_location("tpch_figure7_data", MODULE_PATH)
figure7 = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(figure7)


def summaries():
    rdf = []
    provsql = []
    for template in figure7.TEMPLATES:
        for scale in figure7.PAPER_SCALE_FACTORS:
            for engine in ("graphdb", "oxigraph"):
                for method in ("C-flat", "C-factorised"):
                    rdf.append({
                        "template": template,
                        "scale_factor": scale,
                        "engine": engine,
                        "method": method,
                        "complete": "True",
                        "timeout_instances": "0",
                        "failed_instances": "0",
                        "median_component_method_e2e_ms": "12.5",
                    })
            provsql.append({
                "template": template,
                "scale_factor": scale,
                "method": "ProvSQL",
                "complete": "True",
                "timeout_instances": "0",
                "failed_instances": "0",
                "median_primary_total_ms": "7.5",
            })
    return rdf, provsql


class TpchFigure7DataTest(unittest.TestCase):
    def test_builds_fixed_nine_point_measured_matrix(self):
        rdf, provsql = summaries()
        rows = figure7.build_rows(rdf, provsql)

        self.assertEqual(12 * 9 * 5, len(rows))
        self.assertEqual({"measured"}, {row["data_kind"] for row in rows})
        self.assertEqual(
            set(figure7.PAPER_SCALE_FACTORS),
            {row["scale_factor"] for row in rows},
        )
        self.assertNotIn("Q09", {row["template"] for row in rows})
        self.assertIn("SPARQLcirc (factored)", {row["mode"] for row in rows})

    def test_timeout_is_retained_as_an_empty_plot_point(self):
        rdf, provsql = summaries()
        target = rdf[0]
        target["complete"] = "False"
        target["timeout_instances"] = "1"
        target["median_component_method_e2e_ms"] = ""

        rows = figure7.build_rows(rdf, provsql)
        result = next(row for row in rows if (
            row["template"] == target["template"]
            and row["scale_factor"] == target["scale_factor"]
            and row["engine"] == "GraphDB 10.7.6"
            and row["mode"] == "SPARQLcirc (flat)"
        ))
        self.assertEqual("timeout", result["status"])
        self.assertEqual("", result["runtime_ms"])

    def test_missing_series_is_rejected_instead_of_predicted(self):
        rdf, provsql = summaries()
        with self.assertRaisesRegex(figure7.FigureDataError, "rows missing"):
            figure7.build_rows(rdf[:-1], provsql)


if __name__ == "__main__":
    unittest.main()
