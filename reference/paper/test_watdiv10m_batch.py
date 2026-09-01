"""Regression tests for the formal WatDiv 10M matrix contract."""

import importlib.util
from pathlib import Path
import unittest


HERE = Path(__file__).resolve().parent
MODULE = HERE / "watdiv10m_batch.py"
SPEC = importlib.util.spec_from_file_location("watdiv10m_batch", MODULE)
batch = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(batch)


class Watdiv10mBatchTest(unittest.TestCase):
    def test_default_matrix_has_462_physical_cells_per_engine(self):
        rows = [
            {"template": template, "instance": instance}
            for template in batch.FORMAL_TEMPLATES
            for instance in batch.FORMAL_INSTANCES
        ]
        count = sum(
            len(batch._methods_for(row, batch.ALL_METHODS)) for row in rows
        )
        self.assertEqual(30, len(batch.workload.NON_PATH_TEMPLATES))
        self.assertEqual(32, len(batch.FORMAL_TEMPLATES))
        self.assertEqual(462, count)

    def test_path_and_non_path_method_domains_are_disjoint(self):
        ordinary = batch._methods_for(
            {"template": "L1"}, batch.ALL_METHODS
        )
        path = batch._methods_for(
            {"template": "P-plus"}, batch.ALL_METHODS
        )
        self.assertEqual(batch.NON_PATH_METHODS, ordinary)
        self.assertEqual(batch.PATH_METHODS, path)
        self.assertNotIn("C-path", ordinary)
        self.assertNotIn("R", path)
        self.assertNotIn("N", path)

    def test_formal_defaults_are_one_plus_five(self):
        parser = batch._parser()
        defaults = {
            action.dest: action.default for action in parser._actions
        }
        self.assertEqual(1, defaults["warmups"])
        self.assertEqual(5, defaults["runs"])
        self.assertEqual(600.0, defaults["complete_method_timeout"])


if __name__ == "__main__":
    unittest.main()
