"""Regression tests for reproducible WatDiv dataset preparation."""

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock


MODULE_PATH = Path(__file__).resolve().parents[1] / "watdiv" / "prepare_data.py"
SPEC = importlib.util.spec_from_file_location("watdiv_prepare_data", MODULE_PATH)
watdiv_prepare_data = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(watdiv_prepare_data)


RAW = """\
<urn:b>\t<urn:p>\t\"two words\" .
<urn:a> <urn:p> <urn:o> .
<urn:a> <urn:p> <urn:o> .
"""


def fake_generator(_watdiv, _model, scale, working_directory, raw_path):
    with raw_path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(RAW)
    (working_directory / "saved.txt").write_text("state\n", encoding="utf-8")
    (working_directory / "generator.stderr").write_text("", encoding="utf-8")
    return {
        "argv": ["watdiv", "-d", "model.txt", scale],
        "exit_code": 0,
        "stderr": "generator.stderr",
    }


def fake_deduplicate(raw_path, base_path, _sort_command, _sort_temporary_directory):
    lines = sorted(set(raw_path.read_text(encoding="utf-8").splitlines()))
    with base_path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(lines) + "\n")
    (base_path.parent / "sort.stderr").write_text("", encoding="utf-8")
    return {
        "command": ["sort", "-u", "--", "generator.raw.nt"],
        "stderr": "sort.stderr",
        "locale": "C",
    }


def metadata_keys(value):
    if isinstance(value, dict):
        for key, child in value.items():
            yield str(key).lower()
            yield from metadata_keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from metadata_keys(child)


class WatdivPrepareDataTest(unittest.TestCase):
    def prepare(self, root):
        watdiv = root / "watdiv"
        watdiv.write_text("fixture", encoding="utf-8")
        model = root / "model.txt"
        model.write_text("model\n", encoding="utf-8")
        output = root / "10M"
        with mock.patch.object(
            watdiv_prepare_data, "_run_generator", side_effect=fake_generator
        ), mock.patch.object(
            watdiv_prepare_data, "_deduplicate", side_effect=fake_deduplicate
        ):
            metadata = watdiv_prepare_data.prepare(
                output=output,
                scale="100",
                watdiv_version="0.6",
                watdiv=watdiv,
                model=model,
            )
        return output, metadata

    def test_generates_deduplicated_base_and_mixed_rdfstar11_layouts(self):
        with tempfile.TemporaryDirectory() as directory:
            output, metadata = self.prepare(Path(directory))
            base = (output / "base.nt").read_text(encoding="utf-8")
            mixed = (output / "mixed-rdfstar11.ttls").read_text(encoding="utf-8")
            model = (output / "model.txt").read_text(encoding="utf-8")
            raw_output_exists = (output / "generator.raw.nt").exists()
            audit = watdiv_prepare_data.audit(output / "dataset.json")

        self.assertEqual(2, metadata["layouts"]["base"]["statement_count"])
        self.assertEqual(1, metadata["deduplication"]["duplicates_removed"])
        self.assertIn("unavailable", metadata["generator_seed_control"])
        self.assertIn("new random WatDiv sample", metadata["rerun_semantics"])
        self.assertEqual(2, base.count("\n"))
        self.assertEqual(2, mixed.count("<http://example.org/occurrenceOf>"))
        self.assertIn("<urn:t:0>", mixed)
        self.assertIn("<urn:t:1>", mixed)
        self.assertFalse(metadata["generator"]["raw_output_retained"])
        self.assertFalse(raw_output_exists)
        self.assertEqual("model\n", model)
        self.assertEqual("ok", audit["status"])

    def test_metadata_contains_no_digest_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            output, _ = self.prepare(Path(directory))
            metadata = json.loads((output / "dataset.json").read_text(encoding="utf-8"))

        forbidden = ("checksum", "digest", "sha", "md5")
        self.assertFalse(
            any(any(marker in key for marker in forbidden) for key in metadata_keys(metadata))
        )

    def test_audit_rejects_changed_occurrence_tokens(self):
        with tempfile.TemporaryDirectory() as directory:
            output, _ = self.prepare(Path(directory))
            mixed_path = output / "mixed-rdfstar11.ttls"
            mixed = mixed_path.read_text(encoding="utf-8")
            with mixed_path.open("w", encoding="utf-8", newline="\n") as handle:
                handle.write(mixed.replace("<urn:t:0>", "<urn:t:9>"))
            with self.assertRaisesRegex(
                watdiv_prepare_data.PreparationError, "occurrence statement differs"
            ):
                watdiv_prepare_data.audit(output / "dataset.json")

    def test_rejects_fractional_data_scale(self):
        with self.assertRaisesRegex(
            watdiv_prepare_data.PreparationError, "positive integer"
        ):
            watdiv_prepare_data._validate_scale("0.1")

    def test_rejects_malformed_generator_output(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "bad.nt"
            with source.open("w", encoding="utf-8", newline="\n") as handle:
                handle.write("not an N-Triples statement\n")
            with self.assertRaisesRegex(
                watdiv_prepare_data.PreparationError, "invalid WatDiv N-Triples"
            ):
                watdiv_prepare_data._validate_ntriples(source)


if __name__ == "__main__":
    unittest.main()
