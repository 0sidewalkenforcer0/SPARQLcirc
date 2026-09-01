"""Static contract checks for the unified Wikidata 141-query workload."""

import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
WORKLOAD = ROOT / "reference" / "wdbench" / "workloads" / "wikidata-141"


class Wikidata141WorkloadTest(unittest.TestCase):
    def test_counts_methods_and_paths_match_the_formal_matrix(self):
        manifest = json.loads(
            (WORKLOAD / "manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual("wikidata-141-workload-v1", manifest["schema"])
        self.assertEqual(
            {
                "single_bgp": 49,
                "multiple_bgp": 37,
                "optional": 50,
                "property_path": 5,
                "total": 141,
            },
            manifest["counts"],
        )
        entries = manifest["entries"]
        self.assertEqual(141, len(entries))
        self.assertEqual(141, len({entry["query_id"] for entry in entries}))
        self.assertEqual(
            549, sum(len(entry["applicable_methods"]) for entry in entries)
        )
        self.assertEqual(
            {"path-02", "path-05", "path-08", "path-11", "path-14"},
            {
                entry["query_id"]
                for entry in entries
                if entry["category"] == "property_path"
            },
        )
        for entry in entries:
            query = (WORKLOAD / entry["query"]).resolve()
            self.assertTrue(query.is_file(), query)


if __name__ == "__main__":
    unittest.main()
