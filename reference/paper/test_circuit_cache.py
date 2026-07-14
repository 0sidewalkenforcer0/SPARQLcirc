import json
import hashlib
import multiprocessing
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import circuit_cache


def _concurrent_store(directory, engine):
    circuit_cache.store(
        directory,
        ["<urn:g> <urn:p> <urn:o> ."],
        {
            "query_sha256": "a" * 64,
            "commit": "b" * 40,
            "batch_id": "c" * 64,
            "engine": engine,
        },
    )


def _hold_stem_lock(directory, stem, ready, release):
    try:
        with circuit_cache._stem_lock(directory, stem):
            ready.set()
            release.wait(3)
    except ValueError as exc:
        if "stem lock" not in str(exc):
            raise


class CircuitCacheTests(unittest.TestCase):
    @staticmethod
    def _rewrite_sidecar(path, document):
        for observation in document.get("producer_observations", []):
            body = dict(observation)
            body.pop("producer_observation_sha256", None)
            observation["producer_observation_sha256"] = circuit_cache._digest(body)
        body = dict(document)
        body.pop("sidecar_sha256", None)
        document["sidecar_sha256"] = circuit_cache._digest(body)
        Path(path).write_bytes(circuit_cache._canonical_json(document) + b"\n")

    def test_canonical_store_is_idempotent_and_excludes_messages(self):
        lines = [
            "<urn:g:b> <urn:circuit:in> <urn:t:2> .",
            "<urn:row> <urn:sc:message> <urn:msg> .",
            "<urn:g:a> <urn:circuit:in> <urn:t:1> .",
            "<urn:g:b> <urn:circuit:in> <urn:t:2> .",
        ]
        metadata = {
            "query_sha256": "a" * 64,
            "commit": "d" * 40,
            "batch_id": "1" * 64,
            "engine": "test",
        }
        with tempfile.TemporaryDirectory() as directory:
            first = circuit_cache.store(directory, lines, metadata)
            second = circuit_cache.store(directory, reversed(lines), metadata)
            self.assertEqual(first, second)
            payload = Path(first["circuit_path"]).read_text(encoding="utf-8")
            self.assertEqual(payload, "\n".join(sorted(set((lines[0], lines[2])))) + "\n")
            self.assertNotIn("urn:sc:", payload)
            self.assertEqual(circuit_cache.verify(
                first["circuit_path"], first["circuit_sha256"]
            )["circuit_triples"], 2)
            sidecar = json.loads(Path(first["metadata_path"]).read_text(encoding="utf-8"))
            self.assertEqual(sidecar["format"], "canonical-sorted-ntriples-v1")
            self.assertEqual(sidecar["schema"], circuit_cache.SCHEMA)
            self.assertRegex(sidecar["sidecar_sha256"], circuit_cache.SHA256)

            other = dict(metadata, engine="other")
            circuit_cache.store(directory, lines, other)
            circuit_cache.store(directory, reversed(lines), other)
            sidecar = json.loads(Path(first["metadata_path"]).read_text(encoding="utf-8"))
            self.assertEqual(len(sidecar["producer_observations"]), 2)
            self.assertEqual(
                {item["engine"] for item in sidecar["producer_observations"]},
                {"test", "other"},
            )

    def test_rejects_non_statements_and_detects_tampering(self):
        with self.assertRaises(ValueError):
            circuit_cache.canonical_bytes(["not n-triples"])
        with tempfile.TemporaryDirectory() as directory:
            result = circuit_cache.store(
                directory,
                ["<urn:g> <urn:p> <urn:o> ."],
                {
                    "query_sha256": "b" * 64,
                    "commit": "c" * 40,
                    "batch_id": "2" * 64,
                },
            )
            Path(result["circuit_path"]).write_text("<urn:x> <urn:p> <urn:o> .\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                circuit_cache.verify(result["circuit_path"], result["circuit_sha256"])

    def test_requires_frozen_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                circuit_cache.store(
                    directory,
                    [],
                    {"query_sha256": "bad", "commit": "x", "batch_id": "3" * 64},
                )

    def test_identity_type_rejection_cannot_corrupt_existing_cache(self):
        metadata = {
            "query_sha256": "1" * 64,
            "commit": "2" * 40,
            "batch_id": "3" * 64,
        }
        with tempfile.TemporaryDirectory() as directory:
            stored = circuit_cache.store(
                directory, ["<urn:g> <urn:p> <urn:o> ."], metadata
            )
            forged = {name: int(value) for name, value in metadata.items()}
            with self.assertRaises(ValueError):
                circuit_cache.store(
                    directory, ["<urn:g> <urn:p> <urn:o> ."], forged
                )
            loaded = circuit_cache.load_sidecar(
                stored["metadata_path"], stored["circuit_path"]
            )
            self.assertEqual(len(loaded["observations"]), 1)

    def test_sidecar_rejects_type_confusion_nonfinite_and_extra_keys(self):
        metadata = {
            "query_sha256": "4" * 64,
            "commit": "5" * 40,
            "batch_id": "6" * 64,
            "engine": "test",
        }
        cases = (
            ([], lambda doc: (
                doc.__setitem__("circuit_triples", False),
                doc["producer_observations"][0].__setitem__(
                    "circuit_triples", False
                ),
            )),
            (["<urn:g> <urn:p> <urn:o> ."], lambda doc: (
                doc.__setitem__("circuit_bytes", float(doc["circuit_bytes"])),
                doc["producer_observations"][0].__setitem__(
                    "circuit_bytes",
                    float(doc["producer_observations"][0]["circuit_bytes"]),
                ),
            )),
            (["<urn:g> <urn:p> <urn:o> ."], lambda doc: doc.__setitem__(
                "unexpected", "field"
            )),
        )
        for lines, mutate in cases:
            with self.subTest(lines=lines), tempfile.TemporaryDirectory() as directory:
                stored = circuit_cache.store(directory, lines, metadata)
                sidecar = Path(stored["metadata_path"])
                document = json.loads(sidecar.read_text(encoding="utf-8"))
                mutate(document)
                self._rewrite_sidecar(sidecar, document)
                with self.assertRaises(ValueError):
                    circuit_cache.load_sidecar(sidecar, stored["circuit_path"])

        with tempfile.TemporaryDirectory() as directory:
            stored = circuit_cache.store(
                directory, ["<urn:g> <urn:p> <urn:o> ."], metadata
            )
            sidecar = Path(stored["metadata_path"])
            payload = sidecar.read_bytes().replace(b'"engine":"test"', b'"engine":NaN')
            sidecar.write_bytes(payload)
            with self.assertRaises(ValueError):
                circuit_cache.load_sidecar(sidecar, stored["circuit_path"])

    def test_load_sidecar_returns_one_fd_pair_generation_snapshots(self):
        with tempfile.TemporaryDirectory() as directory:
            first = circuit_cache.store(
                directory,
                ["<urn:g> <urn:p> <urn:o> ."],
                {
                    "query_sha256": "7" * 64,
                    "commit": "8" * 40,
                    "batch_id": "9" * 64,
                    "engine": "one",
                },
            )
            second = circuit_cache.store(
                directory,
                ["<urn:g> <urn:p> <urn:o> ."],
                {
                    "query_sha256": "7" * 64,
                    "commit": "8" * 40,
                    "batch_id": "9" * 64,
                    "engine": "two",
                },
            )
            loaded = circuit_cache.load_sidecar(
                second["metadata_path"], first["circuit_path"]
            )
            payload = Path(first["circuit_path"]).read_bytes()
            sidecar = Path(second["metadata_path"]).read_bytes()
            self.assertEqual(
                loaded["payload_snapshot"]["sha256"], hashlib.sha256(payload).hexdigest()
            )
            self.assertEqual(
                loaded["sidecar_snapshot"]["sha256"], hashlib.sha256(sidecar).hexdigest()
            )
            self.assertEqual(loaded["sidecar_snapshot"]["bytes"], len(sidecar))
            self.assertEqual(
                {item["engine"] for item in loaded["observations"]}, {"one", "two"}
            )

    def test_payload_sidecar_and_stem_lock_must_be_single_link(self):
        metadata = {
            "query_sha256": "e" * 64,
            "commit": "f" * 40,
            "batch_id": "1" * 64,
        }
        lines = ["<urn:g> <urn:p> <urn:o> ."]
        with tempfile.TemporaryDirectory() as directory:
            stored = circuit_cache.store(directory, lines, metadata)
            os.link(stored["circuit_path"], Path(directory, "payload-alias"))
            with self.assertRaises(ValueError):
                circuit_cache.verify(stored["circuit_path"])

        with tempfile.TemporaryDirectory() as directory:
            stored = circuit_cache.store(directory, lines, metadata)
            os.link(stored["metadata_path"], Path(directory, "sidecar-alias"))
            with self.assertRaises(ValueError):
                circuit_cache.load_sidecar(
                    stored["metadata_path"], stored["circuit_path"]
                )

        with tempfile.TemporaryDirectory() as directory:
            payload = circuit_cache.canonical_bytes(lines)
            stem = "%s-%s" % (
                metadata["query_sha256"],
                hashlib.sha256(payload).hexdigest(),
            )
            lock = Path(directory, "." + stem + ".lock")
            lock.write_text("lock", encoding="ascii")
            os.link(lock, Path(directory, "lock-alias"))
            with self.assertRaises(ValueError):
                circuit_cache.store(directory, lines, metadata)

    def test_payload_sidecar_pair_is_revalidated_on_the_same_descriptors(self):
        with tempfile.TemporaryDirectory() as directory:
            stored = circuit_cache.store(
                directory,
                ["<urn:g> <urn:p> <urn:o> ."],
                {
                    "query_sha256": "2" * 64,
                    "commit": "3" * 40,
                    "batch_id": "4" * 64,
                },
            )
            original = circuit_cache._validate_sidecar_payload

            def replace_after_validation(*args, **kwargs):
                result = original(*args, **kwargs)
                replacement = Path(directory, "replacement.nt")
                replacement.write_bytes(Path(stored["circuit_path"]).read_bytes())
                os.replace(replacement, stored["circuit_path"])
                return result

            with mock.patch.object(
                circuit_cache,
                "_validate_sidecar_payload",
                side_effect=replace_after_validation,
            ), self.assertRaises(ValueError):
                circuit_cache.load_sidecar(
                    stored["metadata_path"], stored["circuit_path"]
                )
            with self.assertRaises(ValueError):
                circuit_cache.store(
                    directory,
                    [],
                    {"query_sha256": "c" * 64, "batch_id": "3" * 64},
                )
            with self.assertRaises(ValueError):
                circuit_cache.store(
                    directory,
                    [],
                    {
                        "query_sha256": "c" * 64,
                        "commit": "deadbee",
                        "batch_id": "3" * 64,
                    },
                )
            with self.assertRaises(ValueError):
                circuit_cache.store(
                    directory,
                    [],
                    {"query_sha256": "c" * 64, "commit": "x"},
                )

    @unittest.skipUnless(
        "fork" in multiprocessing.get_all_start_methods(),
        "cache concurrency regression requires fork",
    )
    def test_stem_lock_preserves_two_concurrent_producers(self):
        ctx = multiprocessing.get_context("fork")
        with tempfile.TemporaryDirectory() as directory:
            workers = [
                ctx.Process(target=_concurrent_store, args=(directory, engine))
                for engine in ("one", "two")
            ]
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join(5)
                self.assertEqual(worker.exitcode, 0)
            sidecars = list(Path(directory).glob("*.json"))
            self.assertEqual(len(sidecars), 1)
            document = json.loads(sidecars[0].read_text(encoding="utf-8"))
            producers = document["producer_observations"]
            self.assertEqual({item["engine"] for item in producers}, {"one", "two"})

    @unittest.skipUnless(
        "fork" in multiprocessing.get_all_start_methods(),
        "cache lock replacement regression requires fork",
    )
    def test_replacing_stem_lock_does_not_admit_second_writer(self):
        ctx = multiprocessing.get_context("fork")
        with tempfile.TemporaryDirectory() as directory:
            stem = "a" * 64 + "-" + "b" * 64
            first_ready, first_release = ctx.Event(), ctx.Event()
            second_ready, second_release = ctx.Event(), ctx.Event()
            first = ctx.Process(
                target=_hold_stem_lock,
                args=(directory, stem, first_ready, first_release),
            )
            first.start()
            self.assertTrue(first_ready.wait(1))
            lock = Path(directory, "." + stem + ".lock")
            replacement = Path(directory, "replacement.lock")
            replacement.write_text("attacker", encoding="ascii")
            os.replace(replacement, lock)
            second = ctx.Process(
                target=_hold_stem_lock,
                args=(directory, stem, second_ready, second_release),
            )
            second.start()
            try:
                self.assertFalse(second_ready.wait(0.15))
                first_release.set()
                self.assertTrue(second_ready.wait(1))
            finally:
                first_release.set()
                second_release.set()
                first.join(2)
                second.join(2)
            self.assertFalse(first.is_alive())
            self.assertFalse(second.is_alive())


if __name__ == "__main__":
    unittest.main()
