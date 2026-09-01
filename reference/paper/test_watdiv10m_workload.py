from __future__ import annotations

import csv
import json
from pathlib import Path
import sys
import tempfile
import unittest


PAPER = Path(__file__).resolve().parent
if str(PAPER) not in sys.path:
    sys.path.insert(0, str(PAPER))

import watdiv10m_workload as workload


class SplitTest(unittest.TestCase):
    def test_split_exact_sequence(self) -> None:
        text = (
            "SELECT ?x WHERE { <urn:a> <urn:p> ?x . }\n\n"
            "SELECT ?x WHERE { <urn:b> <urn:p> ?x . }\n"
        )
        queries = workload.split_generated_queries(text, 2)
        self.assertEqual(len(queries), 2)
        self.assertTrue(queries[0].startswith("SELECT"))

    def test_split_preserves_duplicates_and_rejects_prefix_noise(self) -> None:
        query = "SELECT ?x WHERE { <urn:a> <urn:p> ?x . }\n"
        self.assertEqual(workload.split_generated_queries(query + query, 2), [query, query])
        with self.assertRaises(workload.WorkloadError):
            workload.split_generated_queries("warning\n" + query, 1)


class FreezeTest(unittest.TestCase):
    def test_repository_path_sources_are_complete_and_evidenced(self) -> None:
        sources = workload.read_path_sources(workload.DEFAULT_PATH_SOURCES)
        self.assertEqual(len(sources), 10)
        self.assertEqual(len({source.iri for source in sources}), 10)
        self.assertEqual({source.selection_seed for source in sources}, {"20260820"})

    def _inputs(self, root: Path) -> dict[str, Path]:
        watdiv = root / "watdiv06"
        watdiv.write_text("test runner stub\n", encoding="utf-8")
        model = root / "model.txt"
        model.write_text("# model\n", encoding="utf-8")
        state = root / "saved.txt"
        state.write_text("state line\n", encoding="utf-8")
        testsuite = root / "testsuite"
        testsuite.mkdir()
        for template in workload.OFFICIAL_TEMPLATES:
            (testsuite / (template + ".txt")).write_text(
                "SELECT * WHERE { ?s ?p ?o . }\n", encoding="utf-8"
            )
        sources = root / "path-sources.tsv"
        with sources.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=workload.SOURCE_COLUMNS,
                delimiter="\t",
                lineterminator="\n",
            )
            writer.writeheader()
            for index in range(10):
                writer.writerow({
                    "source_id": "source-%02d" % index,
                    "iri": "http://example.test/User%d" % index,
                    "stratum": "s%d" % (index // 2),
                    "reachable_count": index * 10,
                    "max_hops": index,
                    "selection_method": "frozen-unit-test",
                    "selection_seed": "17",
                })
        return {
            "watdiv": watdiv,
            "model": model,
            "state": state,
            "official_testsuite": testsuite,
            "path_sources_file": sources,
        }

    @staticmethod
    def _runner(command: list[str], _cwd: Path) -> tuple[bytes, bytes]:
        template = Path(command[3]).stem
        indices = [0, 0] + list(range(2, 10)) if template == "L1" else list(range(10))
        text = "".join(
            "SELECT ?x WHERE { <urn:%s:%d> <urn:p> ?x . }\n\n" % (template, index)
            for index in indices
        )
        return text.encode("utf-8"), b""

    def test_freeze_and_audit_complete_331_query_batch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inputs = self._inputs(root)
            output = root / "formal-batch"
            audit = workload.freeze_workload(
                **inputs,
                output=output,
                dataset_id="unit-watdiv-10m",
                runner=self._runner,
                make_read_only=False,
            )
            self.assertEqual(audit["query_count"], 331)
            self.assertEqual(workload.audit_workload(output), audit)
            metadata = json.loads((output / "workload.json").read_text(encoding="utf-8"))
            self.assertFalse(metadata["old_repository_queries_modified"])
            self.assertEqual(metadata["counts"]["queries"], 331)
            self.assertEqual(metadata["generator"]["duplicate_policy"],
                             "preserve-emitted-instances-in-order")
            self.assertEqual(audit["repeated_query_instances"], 1)
            with (output / "query-list.tsv").open(encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle, delimiter="\t"))
            self.assertEqual(len(rows), 331)
            self.assertEqual(sum(row["template"] == "P-plus-all" for row in rows), 1)
            self.assertEqual(sum(row["template"] == "O1" for row in rows), 10)
            self.assertEqual(sum(row["template"] == "M1" for row in rows), 10)
            with self.assertRaises(FileExistsError):
                workload.freeze_workload(
                    **inputs,
                    output=output,
                    dataset_id="unit-watdiv-10m",
                    runner=self._runner,
                    make_read_only=False,
                )

    def test_audit_detects_divergence_from_generation_transcript(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inputs = self._inputs(root)
            output = root / "formal-batch"
            workload.freeze_workload(
                **inputs,
                output=output,
                dataset_id="unit-watdiv-10m",
                runner=self._runner,
                make_read_only=False,
            )
            first = output / "queries" / "nonpath" / "L1" / "00.rq"
            third = output / "queries" / "nonpath" / "L1" / "02.rq"
            third.write_bytes(first.read_bytes())
            with self.assertRaises(workload.WorkloadError):
                workload.audit_workload(output)

    def test_generation_rejects_state_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inputs = self._inputs(root)

            def mutating_runner(command: list[str], cwd: Path) -> tuple[bytes, bytes]:
                (cwd / "saved.txt").write_text("mutated\n", encoding="utf-8")
                return self._runner(command, cwd)

            with self.assertRaises(workload.WorkloadError):
                workload.freeze_workload(
                    **inputs,
                    output=root / "formal-batch",
                    dataset_id="unit-watdiv-10m",
                    runner=mutating_runner,
                    make_read_only=False,
                )

    def test_source_file_requires_ten_distinct_evidenced_sources(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_file = self._inputs(root)["path_sources_file"]
            sources = workload.read_path_sources(source_file)
            self.assertEqual(len(sources), 10)
            lines = source_file.read_text(encoding="utf-8").splitlines()
            source_file.write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")
            with self.assertRaises(workload.WorkloadError):
                workload.read_path_sources(source_file)


if __name__ == "__main__":
    unittest.main(verbosity=2)
