import contextlib
import copy
import csv
import hashlib
import io
import json
import multiprocessing
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
import urllib.error
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import freeze_inputs as freeze


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _slow_sentinel_worker(_conn, _endpoint, _query, _kind, _timeout):
    time.sleep(2)


def _slow_canary_worker(
    _conn, _reified_endpoint, _update_endpoint, _subject, _deadline
):
    time.sleep(2)


def _try_store_lock(queue, identity):
    try:
        with freeze._store_lock(identity, 0.12):
            queue.put("entered")
    except freeze.FreezeError:
        queue.put("blocked")


def _atomic_writer(queue, gate, output, document, hold):
    gate.wait()

    def validate(_staging_path):
        time.sleep(hold)

    try:
        freeze.atomic_write_json(
            output, document, staged_validation=validate
        )
        queue.put("ok")
    except freeze.FreezeError:
        queue.put("error")


class _Response:
    def __init__(self, payload):
        self.payload = payload
        self.offset = 0

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, size=-1):
        if self.offset >= len(self.payload):
            return b""
        end = len(self.payload) if size < 0 else min(len(self.payload), self.offset + size)
        block = self.payload[self.offset:end]
        self.offset = end
        return block


def _safe_endpoint(
    digest,
    *,
    origin=None,
    path=None,
    parent=None,
    leaf="other",
):
    return {
        "endpoint_sha256": digest,
        "origin_sha256": origin or "a" * 64,
        "path_sha256": path or digest,
        "parent_path_sha256": parent or "b" * 64,
        "path_leaf_kind": leaf,
        "scheme": "http",
        "port": 7200,
        "host_class": "loopback",
    }


def _valid_frozen_document():
    manifests = []
    for index, kind in enumerate(("workload", "path"), 1):
        key = {name: "x" for name in freeze.MANIFESTS[kind]["key"]}
        manifests.append({
            "kind": kind,
            "schema": list(freeze.MANIFESTS[kind]["columns"]),
            "bytes": index,
            "sha256": str(index) * 64,
            "rows": 1,
            "queries": [{
                "key": key,
                "query_file": f"paper/queries/{kind}.rq",
                "query_sha256": str(index + 2) * 64,
            }],
        })
    base_fp = freeze.sentinel_fingerprint("ask", False)
    reified_fp = freeze.sentinel_fingerprint("ask", True)
    query_sha = "4" * 64
    stores = [{
        "engine": "graphdb",
        "scale": "10M",
        "engine_version": "10.7.6",
        "endpoints": {
            "base": _safe_endpoint("5" * 64),
            "reified": _safe_endpoint(
                "6" * 64, origin="c" * 64, path="d" * 64
            ),
            "update": _safe_endpoint(
                "7" * 64,
                origin="c" * 64,
                parent="d" * 64,
                leaf="statements",
            ),
        },
        "access_mode": "writable",
        "base_data_name": "watdiv-base",
        "reified_data_name": "watdiv-reified",
        "update_binding": "strict-reified-statements-child",
        "update_canary": {
            "protocol": freeze.CANARY_PROTOCOL,
            "insert_visible": True,
            "delete_invisible": True,
        },
    }]
    sentinels = [
        {
            "engine": "graphdb", "scale": "10M", "role": "base", "kind": "ask",
            "query_sha256": query_sha, "expected_fingerprint": base_fp,
            "observed_fingerprint": base_fp,
        },
        {
            "engine": "graphdb", "scale": "10M", "role": "reified", "kind": "ask",
            "query_sha256": query_sha, "expected_fingerprint": reified_fp,
            "observed_fingerprint": reified_fp,
        },
    ]
    return freeze.build_batch(
        "r9-v6",
        {"commit": "a" * 40, "clean": True},
        manifests,
        [
            {"name": "watdiv-base", "bytes": 3, "sha256": "8" * 64},
            {"name": "watdiv-reified", "bytes": 4, "sha256": "a" * 64},
        ],
        stores,
        sentinels,
        [{"name": "d4", "bytes": 4, "sha256": "9" * 64}],
    )


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "reference"
        (self.root / "paper" / "queries").mkdir(parents=True)
        (self.root / "watdiv").mkdir()
        self.work_query = self.root / "paper" / "queries" / "q.rq"
        self.path_query = self.root / "watdiv" / "p.rq"
        self.work_query.write_text("SELECT * WHERE {}\n", encoding="utf-8")
        self.path_query.write_text("SELECT * WHERE { ?s ?p+ ?o }\n", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def _manifest(self, kind, rows):
        path = Path(self.tmp.name) / f"{kind}.csv"
        with path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=freeze.MANIFESTS[kind]["columns"])
            writer.writeheader()
            writer.writerows(rows)
        return path

    def _workload_row(self):
        return {
            "suite": "w", "class": "L", "template": "L1", "instance": "00",
            "query_file": "paper/queries/q.rq",
            "query_sha256": _sha(self.work_query.read_bytes()), "scale": "10M",
            "bound_policy": "fixed", "notes": "",
        }

    def _path_row(self):
        return {
            "suite": "p", "class": "P", "template": "P+", "form": "p+",
            "bound": "source=x", "query_file": "watdiv/p.rq",
            "query_sha256": _sha(self.path_query.read_bytes()),
            "dataset": "watdiv-10M", "notes": "",
        }

    def test_validates_both_manifest_schemas_and_query_hashes(self):
        workload = freeze.validate_manifest(
            self._manifest("workload", [self._workload_row()]), "workload", self.root
        )
        path = freeze.validate_manifest(
            self._manifest("path", [self._path_row()]), "path", self.root
        )
        self.assertEqual((workload["rows"], path["rows"]), (1, 1))
        self.assertEqual(workload["queries"][0]["query_sha256"], _sha(self.work_query.read_bytes()))
        serialized = json.dumps((workload, path))
        self.assertNotIn(self.tmp.name, serialized)

    def test_rejects_duplicate_key_hash_mismatch_missing_and_escape(self):
        row = self._workload_row()
        with self.assertRaisesRegex(freeze.FreezeError, "duplicate logical key"):
            freeze.validate_manifest(
                self._manifest("workload", [row, dict(row)]), "workload", self.root
            )

        bad = dict(row, query_sha256="0" * 64)
        with self.assertRaisesRegex(freeze.FreezeError, "hash mismatch"):
            freeze.validate_manifest(
                self._manifest("workload", [bad]), "workload", self.root
            )

        missing = dict(row, query_file="paper/queries/missing.rq")
        with self.assertRaisesRegex(freeze.FreezeError, "missing or escapes"):
            freeze.validate_manifest(
                self._manifest("workload", [missing]), "workload", self.root
            )

        outside = Path(self.tmp.name) / "outside.rq"
        outside.write_text("SELECT * WHERE {}", encoding="utf-8")
        escape = dict(row, query_file="../outside.rq", query_sha256=_sha(outside.read_bytes()))
        with self.assertRaisesRegex(
            freeze.FreezeError, "missing or escapes|not canonical"
        ):
            freeze.validate_manifest(
                self._manifest("workload", [escape]), "workload", self.root
            )

    def test_rejects_schema_drift_and_empty_manifest(self):
        empty = self._manifest("workload", [])
        with self.assertRaisesRegex(freeze.FreezeError, "empty"):
            freeze.validate_manifest(empty, "workload", self.root)
        malformed = Path(self.tmp.name) / "malformed.csv"
        malformed.write_text("suite,query_file,query_sha256\n", encoding="utf-8")
        with self.assertRaisesRegex(freeze.FreezeError, "schema/header"):
            freeze.validate_manifest(malformed, "workload", self.root)

    def test_producer_rejects_every_noncanonical_query_path(self):
        row = self._workload_row()
        for query_file in (
            "./paper/queries/q.rq",
            "paper//queries/q.rq",
            "paper/queries/../queries/q.rq",
        ):
            candidate = dict(row, query_file=query_file)
            with self.subTest(query_file=query_file), self.assertRaisesRegex(
                freeze.FreezeError, "not canonical"
            ):
                freeze.validate_manifest(
                    self._manifest("workload", [candidate]),
                    "workload",
                    self.root,
                )

    def test_manifest_hash_and_csv_parse_share_one_open_descriptor(self):
        manifest = self._manifest("workload", [self._workload_row()])
        real_open = open
        manifest_path = os.path.abspath(manifest)
        opens = []

        def tracked_open(path, *args, **kwargs):
            if os.path.abspath(os.fspath(path)) == manifest_path:
                opens.append(path)
            return real_open(path, *args, **kwargs)

        with mock.patch("builtins.open", side_effect=tracked_open):
            freeze.validate_manifest(manifest, "workload", self.root)
        self.assertEqual(len(opens), 1)

    def test_captured_query_root_rejects_symlink_retargeting(self):
        alternate = Path(self.tmp.name) / "alternate"
        alternate.mkdir()
        alias = Path(self.tmp.name) / "query-root"
        alias.symlink_to(self.root, target_is_directory=True)
        captured = freeze._capture_directory(alias, "query root")
        alias.unlink()
        alias.symlink_to(alternate, target_is_directory=True)
        with self.assertRaisesRegex(freeze.FreezeError, "identity changed"):
            freeze._verify_directory(captured)

    def test_rejects_short_rows_and_blank_or_padded_semantic_columns(self):
        row = self._workload_row()
        columns = freeze.MANIFESTS["workload"]["columns"]
        short = Path(self.tmp.name) / "short.csv"
        short.write_text(
            ",".join(columns) + "\n" + ",".join(row[name] for name in columns[:-1]) + "\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(freeze.FreezeError, "missing fields"):
            freeze.validate_manifest(short, "workload", self.root)

        for invalid in (
            dict(row, bound_policy=""),
            dict(row, bound_policy=" fixed"),
            dict(row, scale="10M "),
        ):
            with self.assertRaisesRegex(freeze.FreezeError, "semantic fields"):
                freeze.validate_manifest(
                    self._manifest("workload", [invalid]), "workload", self.root
                )

        notes_are_nonsemantic = dict(row, notes="  ")
        validated = freeze.validate_manifest(
            self._manifest("workload", [notes_are_nonsemantic]), "workload", self.root
        )
        self.assertEqual(validated["rows"], 1)


class FileAndEndpointTests(unittest.TestCase):
    def test_explicit_files_are_streamed_named_and_path_free(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "secret-data-name.nt"
            path.write_bytes(b"abc" * 1000)
            result = freeze.hash_named_paths([f"watdiv={path}"], "data file")
        self.assertEqual(result, [{
            "name": "watdiv", "bytes": 3000, "sha256": _sha(b"abc" * 1000)
        }])
        self.assertNotIn("secret-data-name", json.dumps(result))
        with self.assertRaisesRegex(freeze.FreezeError, "duplicate"):
            freeze.hash_named_paths(["x=/tmp/a", "x=/tmp/b"], "data file")

    def test_formal_endpoint_rejects_userinfo_query_and_fragment_without_leaks(self):
        safe, canonical = freeze.endpoint_identity(
            "http://private.example:7200/repositories/base"
        )
        rendered = json.dumps(safe)
        self.assertRegex(safe["endpoint_sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(canonical.path, "/repositories/base")
        for secret in ("private.example", "repositories"):
            self.assertNotIn(secret, rendered)

        invalid = (
            "http://alice:secret@private.example:7200/repositories/base",
            "http://private.example:7200/repositories/base?token=secret",
            "http://private.example:7200/repositories/base?",
            "http://private.example:7200/repositories/base#fragment-secret",
            "http://private.example:7200/repositories/base#",
            "http://private.example:0/repositories/base",
            "http://private.example:/repositories/base",
        )
        for endpoint in invalid:
            with self.subTest(endpoint=endpoint), self.assertRaises(freeze.FreezeError) as raised:
                freeze.endpoint_identity(endpoint)
            for secret in ("alice", "secret", "private.example", "repositories", "token"):
                self.assertNotIn(secret, str(raised.exception))

    def test_store_requires_distinct_base_reified_and_unique_key(self):
        same = [[
            "graphdb", "10M", "10.7.6", "writable", "base-data", "reified-data",
            "http://localhost:7200/repo",
            "http://localhost:7200/repo", "http://localhost:7200/repo",
        ]]
        with self.assertRaisesRegex(freeze.FreezeError, "identical"):
            freeze.validate_store_specs(same)
        valid = [[
            "graphdb", "10M", "10.7.6", "writable", "base-data", "reified-data",
            "http://localhost:7200/base",
            "http://localhost:7200/reified", "http://localhost:7200/reified/statements",
        ]]
        stores, urls = freeze.validate_store_specs(valid)
        self.assertEqual(len(stores), 1)
        self.assertEqual(
            stores[0]["update_binding"], "strict-reified-statements-child"
        )
        self.assertEqual(set(urls), {
            ("graphdb", "10M", "base"),
            ("graphdb", "10M", "reified"),
            ("graphdb", "10M", "update"),
        })
        with self.assertRaisesRegex(freeze.FreezeError, "duplicate store"):
            freeze.validate_store_specs(valid + valid)

    def test_update_endpoint_must_be_provably_bound_to_reified(self):
        shared = [[
            "graphdb", "10M", "10.7.6", "writable", "base-data", "reified-data",
            "http://localhost:7200/base",
            "http://localhost:7200/reified", "http://localhost:7200/reified",
        ]]
        stores, _ = freeze.validate_store_specs(shared)
        self.assertEqual(stores[0]["update_binding"], "canonical-same-as-reified")

        for update in (
            "http://localhost:7200/unrelated/statements",
            "http://127.0.0.1:7200/reified/statements",
            "http://localhost:7201/reified/statements",
        ):
            invalid = [[
                "graphdb", "10M", "10.7.6", "writable", "base-data", "reified-data",
                "http://localhost:7200/base",
                "http://localhost:7200/reified", update,
            ]]
            with self.assertRaisesRegex(freeze.FreezeError, "not provably bound"):
                freeze.validate_store_specs(invalid)

        ambiguous = [[
            "graphdb", "10M", "10.7.6", "writable", "base-data", "reified-data",
            "http://localhost:7200/base",
            "http://localhost:7200/reified/%2e%2e/other",
            "http://localhost:7200/reified/%2e%2e/other/statements",
        ]]
        with self.assertRaisesRegex(freeze.FreezeError, "not provably bound"):
            freeze.validate_store_specs(ambiguous)

    def test_ctime_snapshot_recheck_and_output_alias_rejection(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "input.bin"
            source.write_bytes(b"immutable")
            source.chmod(0o600)
            snapshots = []
            freeze.hash_file(source, label="test input", snapshots=snapshots)
            source.chmod(0o640)
            with self.assertRaisesRegex(freeze.FreezeError, "changed before output"):
                freeze.verify_snapshots(snapshots)

            with self.assertRaisesRegex(freeze.FreezeError, "aliases"):
                freeze._reject_output_alias(source, [source])
            alias = Path(directory) / "hardlink.json"
            os.link(source, alias)
            with self.assertRaisesRegex(freeze.FreezeError, "aliases"):
                freeze._reject_output_alias(alias, [source])


class SentinelTests(unittest.TestCase):
    @staticmethod
    def _opener(payload):
        return lambda _request, timeout=None: _Response(payload)

    def test_ask_and_count_fingerprints_are_semantic_and_stable(self):
        ask = freeze._probe_once(
            "http://unused", "ASK {}", "ask", 1,
            opener=self._opener(b'{"boolean":true}'),
        )
        count_payload = json.dumps({
            "head": {"vars": ["count"]},
            "results": {"bindings": [{
                "count": {"type": "literal", "value": "0007"}
            }]},
        }).encode()
        count = freeze._probe_once(
            "http://unused", "SELECT (COUNT(*) AS ?count) WHERE {}", "count", 1,
            opener=self._opener(count_payload),
        )
        self.assertEqual(ask, {
            "status": "ok", "observed_fingerprint": freeze.sentinel_fingerprint("ask", True)
        })
        self.assertEqual(
            count["observed_fingerprint"], freeze.sentinel_fingerprint("count", 7)
        )

    def test_distinguishes_http_closed_and_invalid_response(self):
        def http(_request, timeout=None):
            raise urllib.error.HTTPError("redacted", 401, "auth", {}, io.BytesIO())

        def closed(_request, timeout=None):
            raise urllib.error.URLError(ConnectionRefusedError())

        http_result = freeze._probe_once("http://unused", "ASK {}", "ask", 1, opener=http)
        closed_result = freeze._probe_once("http://unused", "ASK {}", "ask", 1, opener=closed)
        invalid = freeze._probe_once(
            "http://unused", "ASK {}", "ask", 1, opener=self._opener(b"not-json")
        )
        self.assertEqual(http_result, {"status": "http", "http_status": 401})
        self.assertEqual(closed_result["status"], "closed")
        self.assertEqual(invalid["status"], "error")

    def test_default_http_opener_does_not_follow_redirects(self):
        handler = freeze._NoRedirect()
        request = freeze.U.Request("http://127.0.0.1/redirect")
        self.assertIsNone(
            handler.redirect_request(request, None, 302, "redirect", {}, "/target")
        )
        redirect = urllib.error.HTTPError(
            "redacted", 302, "redirect", {"Location": "/target"}, io.BytesIO()
        )
        with mock.patch.object(freeze._NO_REDIRECT_OPENER, "open", side_effect=redirect):
            result = freeze._probe_once(
                "http://127.0.0.1/redirect", "ASK {}", "ask", 1
            )
        self.assertEqual(result, {"status": "http", "http_status": 302})

    def test_explicit_sentinel_kind_and_query_file_size_cap(self):
        self.assertEqual(freeze._sentinel_query("ASK {}", "ask")[1], "ask")
        self.assertEqual(
            freeze._sentinel_query(
                "SELECT (COUNT(*) AS ?n) WHERE {}", "count"
            )[1],
            "count",
        )
        with self.assertRaisesRegex(freeze.FreezeError, "begin with ASK"):
            freeze._sentinel_query("SELECT * WHERE {} # ASK", "ask")
        with self.assertRaisesRegex(freeze.FreezeError, "COUNT"):
            freeze._sentinel_query("SELECT * WHERE {} # COUNT(*)", "count")

        oversized_stat = SimpleNamespace(
            st_dev=1, st_ino=2, st_mode=stat.S_IFREG | 0o600,
            st_size=freeze.MAX_SENTINEL_BYTES + 1,
            st_mtime_ns=3, st_ctime_ns=4,
        )
        stream = mock.MagicMock()
        stream.__enter__.return_value = stream
        stream.fileno.return_value = 5
        with (
            mock.patch("builtins.open", return_value=stream),
            mock.patch.object(freeze.os, "fstat", return_value=oversized_stat),
        ):
            with self.assertRaisesRegex(freeze.FreezeError, "safety cap"):
                freeze._sentinel_query("@oversized.rq", "ask")
        stream.read.assert_not_called()

    @unittest.skipUnless(
        "fork" in multiprocessing.get_all_start_methods(), "hard-timeout injection needs fork"
    )
    def test_public_probe_has_a_hard_wall_timeout(self):
        with mock.patch.object(freeze, "_sentinel_worker", _slow_sentinel_worker):
            started = time.monotonic()
            result = freeze.probe_sentinel(
                "http://127.0.0.1:1", "ASK {}", "ask", 0.05
            )
            elapsed = time.monotonic() - started
        self.assertEqual(result["status"], "timeout")
        self.assertLess(elapsed, 0.8)

    def test_store_requires_successful_base_and_reified_sentinels(self):
        stores, urls = freeze.validate_store_specs([[
            "graphdb", "10M", "10.7.6", "read-only", "base-data", "reified-data",
            "http://localhost:7200/base",
            "http://localhost:7200/reified", "-",
        ]])
        base_expected = freeze.sentinel_fingerprint("ask", False)
        reified_expected = freeze.sentinel_fingerprint("ask", True)
        specs = [
            ["graphdb", "10M", "base", "ask", "ASK {}", base_expected],
            ["graphdb", "10M", "reified", "ask", "ASK {}", reified_expected],
        ]
        def distinguished(endpoint, _query, _kind, _timeout):
            fingerprint = base_expected if endpoint.endswith("/base") else reified_expected
            return {"status": "ok", "observed_fingerprint": fingerprint}

        with mock.patch.object(freeze, "probe_sentinel", side_effect=distinguished):
            records = freeze.run_sentinels(specs, stores, urls, 1)
            self.assertEqual(len(records), 2)
            with self.assertRaisesRegex(freeze.FreezeError, "lacks.*reified"):
                freeze.run_sentinels(specs[:1], stores, urls, 1)

        identical_specs = [
            ["graphdb", "10M", "base", "ask", "ASK {}", reified_expected],
            ["graphdb", "10M", "reified", "ask", "ASK {}", reified_expected],
        ]
        identical = {"status": "ok", "observed_fingerprint": reified_expected}
        with mock.patch.object(freeze, "probe_sentinel", return_value=identical):
            with self.assertRaisesRegex(freeze.FreezeError, "role-discriminating"):
                freeze.run_sentinels(identical_specs, stores, urls, 1)

        different_query_specs = [
            ["graphdb", "10M", "base", "ask", "ASK { FILTER(false) }", base_expected],
            ["graphdb", "10M", "reified", "ask", "ASK {}", reified_expected],
        ]
        with mock.patch.object(freeze, "probe_sentinel", side_effect=distinguished):
            with self.assertRaisesRegex(freeze.FreezeError, "role-discriminating"):
                freeze.run_sentinels(different_query_specs, stores, urls, 1)

    def test_writable_canary_performs_insert_visible_delete_absent(self):
        connection = mock.Mock()
        visible = {
            "status": "ok",
            "observed_fingerprint": freeze.sentinel_fingerprint("ask", True),
        }
        absent = {
            "status": "ok",
            "observed_fingerprint": freeze.sentinel_fingerprint("ask", False),
        }
        with (
            mock.patch.object(
                freeze, "_update_once", side_effect=[{"status": "ok"}, {"status": "ok"}]
            ) as update,
            mock.patch.object(freeze, "_probe_once", side_effect=[visible, absent]) as ask,
        ):
            freeze._canary_worker(
                connection,
                "http://reified.invalid/query",
                "http://reified.invalid/update",
                "urn:sc:freeze-canary:test",
                time.monotonic() + 2,
            )
        self.assertEqual(update.call_count, 2)
        self.assertEqual(ask.call_count, 2)
        connection.send.assert_called_once_with(("ok",))
        connection.close.assert_called_once()

    def test_network_opener_does_not_inherit_environment_proxies(self):
        proxy_handlers = [
            handler
            for handler in freeze._NO_REDIRECT_OPENER.handlers
            if isinstance(handler, freeze.U.ProxyHandler)
        ]
        self.assertTrue(
            not proxy_handlers
            or all(handler.proxies == {} for handler in proxy_handlers)
        )

    @unittest.skipUnless(
        "fork" in multiprocessing.get_all_start_methods(),
        "hard-timeout injection needs fork",
    )
    def test_canary_deadline_includes_lock_wait_worker_and_cleanup(self):
        @contextlib.contextmanager
        def delayed_lock(_identity, _budget):
            time.sleep(0.18)
            yield

        started = time.monotonic()
        with (
            mock.patch.object(freeze, "_store_lock", side_effect=delayed_lock),
            mock.patch.object(freeze, "_canary_worker", _slow_canary_worker),
            mock.patch.object(
                freeze, "_hard_update", return_value={"status": "ok"}
            ),
            self.assertRaisesRegex(freeze.FreezeError, "canary failed"),
        ):
            freeze.run_update_canary(
                "http://localhost/query",
                "http://localhost/update",
                "a" * 64,
                0.3,
            )
        self.assertLess(time.monotonic() - started, 0.45)

    def test_store_lock_rejects_path_identity_and_hardlink_alias(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(
            os.environ, {"SPARQLCIRC_FREEZE_LOCK_DIR": directory}
        ):
            with self.assertRaisesRegex(freeze.FreezeError, "SHA-256"):
                with freeze._store_lock("../other", 0.1):
                    pass

            identity = "a" * 64
            lock_path = Path(directory) / f"{identity}.lock"
            lock_path.write_text("", encoding="utf-8")
            alias = Path(directory) / "alias.lock"
            os.link(lock_path, alias)
            with self.assertRaisesRegex(freeze.FreezeError, "lock setup"):
                with freeze._store_lock(identity, 0.1):
                    pass

    @unittest.skipUnless(
        "fork" in multiprocessing.get_all_start_methods(),
        "lock replacement regression needs fork",
    )
    def test_store_lock_file_replacement_cannot_create_second_critical_section(self):
        context = multiprocessing.get_context("fork")
        identity = "b" * 64
        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(
            os.environ, {"SPARQLCIRC_FREEZE_LOCK_DIR": directory}
        ):
            with freeze._store_lock(identity, 1):
                lock_path = Path(directory) / f"{identity}.lock"
                lock_path.unlink()
                descriptor = os.open(
                    lock_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
                )
                os.close(descriptor)
                queue = context.Queue()
                process = context.Process(
                    target=_try_store_lock, args=(queue, identity)
                )
                process.start()
                process.join(2)
                self.assertFalse(process.is_alive())
                self.assertEqual(queue.get(timeout=1), "blocked")
                process.close()


class GitAndBatchTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("git"), "git executable required")
    def test_git_identity_requires_full_clean_commit(self):
        with tempfile.TemporaryDirectory() as directory:
            subprocess.run(["git", "init", "-q", directory], check=True)
            subprocess.run(["git", "-C", directory, "config", "user.name", "Test"], check=True)
            subprocess.run(
                [
                    "git", "-C", directory, "config", "user.email",
                    "test@example.invalid",
                ],
                check=True,
            )
            Path(directory, "tracked.txt").write_text("frozen", encoding="utf-8")
            subprocess.run(["git", "-C", directory, "add", "tracked.txt"], check=True)
            subprocess.run(["git", "-C", directory, "commit", "-qm", "frozen"], check=True)
            identity = freeze.git_identity(directory)
            self.assertRegex(identity["commit"], r"^[0-9a-f]{40}$")
            self.assertTrue(identity["clean"])
            subprocess.run(
                ["git", "-C", directory, "update-index", "--assume-unchanged", "tracked.txt"],
                check=True,
            )
            Path(directory, "tracked.txt").write_text("hidden", encoding="utf-8")
            with self.assertRaisesRegex(freeze.FreezeError, "hidden-worktree"):
                freeze.git_identity(directory)
            subprocess.run(
                ["git", "-C", directory, "update-index", "--no-assume-unchanged", "tracked.txt"],
                check=True,
            )
            Path(directory, "tracked.txt").write_text("frozen", encoding="utf-8")
            subprocess.run(
                ["git", "-C", directory, "update-index", "--skip-worktree", "tracked.txt"],
                check=True,
            )
            Path(directory, "tracked.txt").write_text("hidden-again", encoding="utf-8")
            with self.assertRaisesRegex(freeze.FreezeError, "hidden-worktree"):
                freeze.git_identity(directory)
            subprocess.run(
                ["git", "-C", directory, "update-index", "--no-skip-worktree", "tracked.txt"],
                check=True,
            )
            Path(directory, "tracked.txt").write_text("frozen", encoding="utf-8")
            Path(directory, "dirty.txt").write_text("dirty", encoding="utf-8")
            with self.assertRaisesRegex(freeze.FreezeError, "not clean"):
                freeze.git_identity(directory)

    def test_git_identity_sandwich_rejects_head_movement(self):
        first = subprocess.CompletedProcess([], 0, stdout="a" * 40 + "\n", stderr="")
        status = subprocess.CompletedProcess([], 0, stdout="", stderr="")
        flags = subprocess.CompletedProcess([], 0, stdout="H tracked.txt\0", stderr="")
        second = subprocess.CompletedProcess([], 0, stdout="b" * 40 + "\n", stderr="")
        with mock.patch.object(
            freeze.subprocess, "run", side_effect=[first, status, flags, second]
        ):
            with self.assertRaisesRegex(freeze.FreezeError, "HEAD changed"):
                freeze.git_identity("unused")

    @unittest.skipUnless(shutil.which("git"), "git executable required")
    def test_repository_binding_rejects_a_clean_unrelated_repository(self):
        with tempfile.TemporaryDirectory() as directory:
            subprocess.run(["git", "init", "-q", directory], check=True)
            with self.assertRaisesRegex(
                freeze.FreezeError, "producer source repository"
            ):
                freeze.validate_repository_binding(directory)

    @unittest.skipUnless(shutil.which("git"), "git executable required")
    def test_exactly_ignored_formal_target_allows_only_bound_stage_temp(self):
        document = _valid_frozen_document()
        with tempfile.TemporaryDirectory() as directory:
            repository = Path(directory)
            subprocess.run(["git", "init", "-q", directory], check=True)
            subprocess.run(
                ["git", "-C", directory, "config", "user.name", "Test"],
                check=True,
            )
            subprocess.run(
                ["git", "-C", directory, "config", "user.email", "test@example.invalid"],
                check=True,
            )
            (repository / ".gitignore").write_text(
                "freeze.json\n", encoding="utf-8"
            )
            subprocess.run(
                ["git", "-C", directory, "add", ".gitignore"], check=True
            )
            subprocess.run(
                ["git", "-C", directory, "commit", "-qm", "fixture"],
                check=True,
            )
            output = repository / "freeze.json"

            def validate(staging_path):
                freeze.git_identity(
                    repository, allowed_untracked=(staging_path,)
                )

            freeze.atomic_write_json(
                output, document, staged_validation=validate
            )
            self.assertTrue(freeze.git_identity(repository)["clean"])

    def test_batch_id_is_order_independent_stable_and_atomically_written(self):
        git = {"commit": "a" * 40, "clean": True}
        manifests = [
            {"kind": "workload", "sha256": "b" * 64},
            {"kind": "path", "sha256": "c" * 64},
        ]
        files = [{"name": "watdiv", "bytes": 3, "sha256": "d" * 64}]
        tools = [{"name": "d4v2", "bytes": 4, "sha256": "e" * 64}]
        stores = [{"engine": "graphdb", "scale": "10M", "engine_version": "10.7.6",
                   "endpoints": {"base": {"endpoint_sha256": "f" * 64}}}]
        sentinels = [{"engine": "graphdb", "scale": "10M", "role": "base",
                      "query_sha256": "1" * 64, "expected_fingerprint": "2" * 64,
                      "observed_fingerprint": "2" * 64, "kind": "ask"}]
        first = freeze.build_batch("r9-v4", git, manifests, files, stores, sentinels, tools)
        second = freeze.build_batch(
            "r9-v4", git, list(reversed(manifests)), files, stores, sentinels, tools
        )
        self.assertEqual(first["batch_id"], second["batch_id"])
        self.assertRegex(first["batch_id"], r"^[0-9a-f]{64}$")
        with tempfile.TemporaryDirectory() as directory:
            writable = _valid_frozen_document()
            output = Path(directory) / "freeze.json"
            freeze.atomic_write_json(output, writable)
            before = output.stat()
            freeze.atomic_write_json(output, writable)
            after = output.stat()
            self.assertEqual(
                json.loads(output.read_text())["batch_id"], writable["batch_id"]
            )
            self.assertEqual((before.st_ino, before.st_mtime_ns), (after.st_ino, after.st_mtime_ns))
            conflict = dict(writable, batch_id="0" * 64)
            with self.assertRaisesRegex(
                freeze.FreezeError, "conflicting bytes|canonical batch_id"
            ):
                freeze.atomic_write_json(output, conflict)
            self.assertEqual(
                json.loads(output.read_text())["batch_id"], writable["batch_id"]
            )
            self.assertEqual(list(Path(directory).glob("*.tmp")), [])

    def test_atomic_output_rejects_symlink_and_existing_hardlink_alias(self):
        document = _valid_frozen_document()
        encoded = (
            json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2)
            + "\n"
        ).encode("utf-8")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = root / "original.json"
            original.write_bytes(encoded)
            hardlink = root / "hardlink.json"
            os.link(original, hardlink)
            with self.assertRaisesRegex(freeze.FreezeError, "conflicting bytes"):
                freeze.atomic_write_json(hardlink, document)

            symlink = root / "symlink.json"
            symlink.symlink_to(original)
            with self.assertRaisesRegex(freeze.FreezeError, "conflicting bytes"):
                freeze.atomic_write_json(symlink, document)

            output = root / "new.json"
            freeze.atomic_write_json(output, document)
            self.assertEqual(output.stat().st_nlink, 1)

    def test_staged_output_is_unloadable_and_validation_failure_rolls_back(self):
        document = _valid_frozen_document()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "formal.json"
            observed_links = []

            def inspect_stage(_staging_path):
                observed_links.append(output.stat().st_nlink)
                with self.assertRaisesRegex(
                    freeze.FreezeError, "single-link|aliased"
                ):
                    freeze.load_frozen_batch(output)

            freeze.atomic_write_json(
                output, document, staged_validation=inspect_stage
            )
            self.assertEqual(observed_links, [2])
            self.assertEqual(
                freeze.load_frozen_batch(output)["batch_id"], document["batch_id"]
            )

            failed = root / "failed.json"

            def reject_stage(_staging_path):
                raise freeze.FreezeError("post-write validation failed")

            with self.assertRaisesRegex(freeze.FreezeError, "post-write"):
                freeze.atomic_write_json(
                    failed, document, staged_validation=reject_stage
                )
            self.assertFalse(failed.exists())

            unexpected = root / "unexpected.json"

            def crash_stage(_staging_path):
                raise RuntimeError("unexpected validator crash")

            with self.assertRaisesRegex(RuntimeError, "validator crash"):
                freeze.atomic_write_json(
                    unexpected, document, staged_validation=crash_stage
                )
            self.assertFalse(unexpected.exists())

            blocked = root / "blocked-rollback.json"
            real_unlink = os.unlink

            def deny_target_unlink(path, *args, **kwargs):
                if path == blocked.name:
                    raise PermissionError("injected rollback failure")
                return real_unlink(path, *args, **kwargs)

            with mock.patch.object(
                freeze.os, "unlink", side_effect=deny_target_unlink
            ), self.assertRaisesRegex(freeze.FreezeError, "roll back"):
                freeze.atomic_write_json(
                    blocked, document, staged_validation=reject_stage
                )
            self.assertEqual(blocked.stat().st_nlink, 2)
            with self.assertRaisesRegex(freeze.FreezeError, "single-link"):
                freeze.load_frozen_batch(blocked)

    def test_postcommit_close_and_unlock_faults_preserve_success_semantics(self):
        document = _valid_frozen_document()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            real_close = os.close
            for fault_call in (1, 2, 3):
                output = root / f"close-{fault_call}.json"
                calls = []

                def flaky_close(descriptor):
                    calls.append(descriptor)
                    real_close(descriptor)
                    if len(calls) == fault_call:
                        raise OSError("injected postcommit close failure")

                with self.subTest(close_call=fault_call), mock.patch.object(
                    freeze.os, "close", side_effect=flaky_close
                ):
                    freeze.atomic_write_json(output, document)
                self.assertGreaterEqual(len(calls), 3)
                self.assertEqual(
                    freeze.load_frozen_batch(output)["batch_id"],
                    document["batch_id"],
                )

            output = root / "unlock.json"
            real_flock = freeze.fcntl.flock

            def flaky_unlock(descriptor, operation):
                result = real_flock(descriptor, operation)
                if operation == freeze.fcntl.LOCK_UN:
                    raise OSError("injected postcommit unlock failure")
                return result

            with mock.patch.object(
                freeze.fcntl, "flock", side_effect=flaky_unlock
            ):
                freeze.atomic_write_json(output, document)
            self.assertEqual(
                freeze.load_frozen_batch(output)["batch_id"], document["batch_id"]
            )

            output = root / "existing-unlink.json"
            freeze.atomic_write_json(output, document)
            real_unlink = os.unlink

            def fail_staging_unlink(path, *args, **kwargs):
                if str(path).endswith(".tmp"):
                    raise OSError("injected committed staging unlink failure")
                return real_unlink(path, *args, **kwargs)

            with mock.patch.object(
                freeze.os, "unlink", side_effect=fail_staging_unlink
            ):
                freeze.atomic_write_json(output, document)
            self.assertEqual(
                freeze.load_frozen_batch(output)["batch_id"], document["batch_id"]
            )
            leftovers = list(root.glob(f".{output.name}.*.tmp"))
            self.assertEqual(len(leftovers), 1)
            leftovers[0].unlink()

            failed = root / "precommit-failure.json"

            def reject_stage(_staging_path):
                raise freeze.FreezeError("injected precommit validation failure")

            def always_fault_close(descriptor):
                real_close(descriptor)
                raise OSError("injected uncommitted close failure")

            with mock.patch.object(
                freeze.os, "close", side_effect=always_fault_close
            ), self.assertRaises(freeze.FreezeError):
                freeze.atomic_write_json(
                    failed, document, staged_validation=reject_stage
                )
            self.assertFalse(failed.exists())

    def test_atomic_writer_detects_replacement_during_final_unlink(self):
        document = _valid_frozen_document()
        attacker_document = copy.deepcopy(document)
        attacker_document["identity"]["protocol"] = "attacker-valid"
        attacker_document["batch_id"] = freeze.canonical_batch_id(
            attacker_document["identity"]
        )
        attacker = (
            json.dumps(
                attacker_document,
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
            )
            + "\n"
        ).encode("utf-8")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "target.json"
            real_unlink = os.unlink
            replaced = []

            def replacing_unlink(path, *args, **kwargs):
                if not replaced and str(path).endswith(".tmp"):
                    real_unlink(path, *args, **kwargs)
                    directory_fd = kwargs["dir_fd"]
                    real_unlink(output.name, dir_fd=directory_fd)
                    descriptor = os.open(
                        output.name,
                        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                        0o600,
                        dir_fd=directory_fd,
                    )
                    try:
                        os.write(descriptor, attacker)
                    finally:
                        os.close(descriptor)
                    replaced.append(True)
                    return
                return real_unlink(path, *args, **kwargs)

            with mock.patch.object(
                freeze.os, "unlink", side_effect=replacing_unlink
            ), self.assertRaisesRegex(
                freeze.FreezeError, "final frozen output"
            ):
                freeze.atomic_write_json(output, document)
            self.assertFalse(output.exists())

    @unittest.skipUnless(
        "fork" in multiprocessing.get_all_start_methods(),
        "concurrent publication regression needs fork",
    )
    def test_concurrent_identical_writers_are_idempotent(self):
        context = multiprocessing.get_context("fork")
        document = _valid_frozen_document()
        with tempfile.TemporaryDirectory() as directory:
            output = str(Path(directory) / "concurrent.json")
            queue = context.Queue()
            gate = context.Event()
            processes = [
                context.Process(
                    target=_atomic_writer,
                    args=(queue, gate, output, document, 0.08),
                )
                for _ in range(2)
            ]
            for process in processes:
                process.start()
            gate.set()
            for process in processes:
                process.join(3)
                self.assertFalse(process.is_alive())
            self.assertEqual(
                sorted(queue.get(timeout=1) for _ in processes), ["ok", "ok"]
            )
            for process in processes:
                process.close()
            self.assertEqual(
                freeze.load_frozen_batch(output)["batch_id"], document["batch_id"]
            )

    def test_output_parent_retarget_and_oversize_or_invalid_documents_fail_closed(self):
        document = _valid_frozen_document()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first"
            second = root / "second"
            first.mkdir()
            second.mkdir()
            alias = root / "alias"
            alias.symlink_to(first, target_is_directory=True)
            output = alias / "freeze.json"

            def retarget(_staging_path):
                alias.unlink()
                alias.symlink_to(second, target_is_directory=True)

            with self.assertRaisesRegex(freeze.FreezeError, "directory identity"):
                freeze.atomic_write_json(
                    output, document, staged_validation=retarget
                )
            self.assertFalse((first / "freeze.json").exists())
            self.assertFalse((second / "freeze.json").exists())

            capped = root / "capped.json"
            with mock.patch.object(freeze, "MAX_FROZEN_BATCH_BYTES", 128):
                with self.assertRaisesRegex(freeze.FreezeError, "safety cap"):
                    freeze.atomic_write_json(capped, document)
            self.assertFalse(capped.exists())

            invalid = root / "invalid.json"
            with self.assertRaisesRegex(freeze.FreezeError, "top-level schema"):
                freeze.atomic_write_json(invalid, {"not": "a frozen batch"})
            self.assertFalse(invalid.exists())

    def test_formal_mode_requires_inputs_and_rechecks_git_after_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            args = SimpleNamespace(
                repo_root=directory,
                query_root=directory,
                workload_manifest=str(Path(directory) / "workload.csv"),
                path_manifest=str(Path(directory) / "path.csv"),
                protocol="r9-v6",
                data=[], tool=[], store=[], sentinel=[], sentinel_timeout=1,
                output=str(Path(directory) / "freeze.json"),
                allow_empty_inputs=False,
            )
            with self.assertRaisesRegex(freeze.FreezeError, "formal mode"):
                freeze.freeze(args)

            args.allow_empty_inputs = True
            first_git = {"commit": "a" * 40, "clean": True}
            second_git = {"commit": "b" * 40, "clean": True}
            manifest = {
                "kind": "workload", "schema": [], "bytes": 1,
                "sha256": "1" * 64, "rows": 1, "queries": [],
            }
            with (
                mock.patch.object(
                    freeze, "validate_repository_binding", return_value=Path(directory)
                ),
                mock.patch.object(freeze, "_require_in_repository"),
                mock.patch.object(
                    freeze, "git_identity", side_effect=[first_git, second_git]
                ) as git,
                mock.patch.object(freeze, "validate_manifest", return_value=manifest),
                mock.patch.object(freeze, "hash_named_paths", return_value=[]),
                mock.patch.object(freeze, "validate_store_specs", return_value=([], {})),
                mock.patch.object(freeze, "run_sentinels", return_value=[]),
            ):
                with self.assertRaisesRegex(freeze.FreezeError, "Git identity changed"):
                    freeze.freeze(args)
            self.assertEqual(git.call_count, 2)
            self.assertFalse(Path(args.output).exists())

    def test_store_access_mode_controls_update_registration(self):
        read_only = [[
            "qlever", "10M", "1", "read-only", "base-data", "reified-data",
            "http://localhost:7002/query", "http://localhost:7001/query", "-",
        ]]
        stores, _ = freeze.validate_store_specs(read_only)
        self.assertEqual(stores[0]["update_binding"], "absent")

        with self.assertRaisesRegex(freeze.FreezeError, "read-only.*must not"):
            freeze.validate_store_specs([read_only[0][:-1] + [
                "http://localhost:7001/update"
            ]])
        writable_missing = [
            "oxigraph", "10M", "1", "writable", "base-data", "reified-data",
            "http://localhost:7879/query", "http://localhost:7878/query", "-",
        ]
        with self.assertRaisesRegex(freeze.FreezeError, "writable.*requires"):
            freeze.validate_store_specs([writable_missing])
        sibling = [writable_missing[:-1] + ["http://localhost:7878/update"]]
        stores, _ = freeze.validate_store_specs(sibling)
        self.assertEqual(
            stores[0]["update_binding"], "strict-query-update-sibling"
        )
        identical = [
            {"name": "base-data", "bytes": 1, "sha256": "a" * 64},
            {"name": "reified-data", "bytes": 1, "sha256": "a" * 64},
        ]
        with self.assertRaisesRegex(freeze.FreezeError, "byte-identical"):
            freeze.validate_store_data_references(
                stores, identical, formal=True
            )

    def test_formal_freeze_runs_canary_and_rechecks_git_after_publish(self):
        template = _valid_frozen_document()["identity"]
        stores = copy.deepcopy(template["stores"])
        stores[0]["update_canary"] = None
        endpoint_urls = {
            ("graphdb", "10M", "base"): "http://localhost:7200/base",
            ("graphdb", "10M", "reified"): "http://localhost:7200/reified",
            ("graphdb", "10M", "update"): "http://localhost:7200/reified/statements",
        }
        clean = {"commit": "a" * 40, "clean": True}
        evidence = {
            "protocol": freeze.CANARY_PROTOCOL,
            "insert_visible": True,
            "delete_invisible": True,
        }
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "formal.json"
            args = SimpleNamespace(
                repo_root=directory,
                query_root=directory,
                workload_manifest="workload.csv",
                path_manifest="path.csv",
                protocol="r9-v6",
                data=["watdiv-base=/data-a", "watdiv-reified=/data-b"],
                tool=["d4=/tool"],
                store=[["unused"] * 9],
                sentinel=[["unused"] * 6],
                sentinel_timeout=1,
                output=str(output),
                allow_empty_inputs=False,
            )
            with (
                mock.patch.object(
                    freeze, "validate_repository_binding", return_value=Path(directory)
                ),
                mock.patch.object(freeze, "_require_in_repository"),
                mock.patch.object(freeze, "validate_formal_output_location"),
                mock.patch.object(freeze, "git_identity", side_effect=[clean, clean, clean]) as git,
                mock.patch.object(
                    freeze, "validate_manifest", side_effect=template["manifests"]
                ),
                mock.patch.object(
                    freeze,
                    "hash_named_paths",
                    side_effect=[template["data_files"], template["tool_binaries"]],
                ),
                mock.patch.object(
                    freeze, "validate_store_specs", return_value=(stores, endpoint_urls)
                ),
                mock.patch.object(
                    freeze, "run_sentinels", return_value=template["store_sentinels"]
                ),
                mock.patch.object(
                    freeze, "run_update_canary", return_value=evidence
                ) as canary,
            ):
                document = freeze.freeze(args)
            self.assertEqual(git.call_count, 3)
            self.assertEqual(canary.call_count, 2)
            self.assertEqual(document["identity"]["batch_profile"], "formal")
            self.assertEqual(
                freeze.load_frozen_batch(output)["batch_id"], document["batch_id"]
            )

            failed_output = Path(directory) / "formal-failed.json"
            args.output = str(failed_output)
            failed_stores = copy.deepcopy(template["stores"])
            failed_stores[0]["update_canary"] = None
            changed = {"commit": "b" * 40, "clean": True}
            with (
                mock.patch.object(
                    freeze, "validate_repository_binding", return_value=Path(directory)
                ),
                mock.patch.object(freeze, "_require_in_repository"),
                mock.patch.object(freeze, "validate_formal_output_location"),
                mock.patch.object(
                    freeze, "git_identity", side_effect=[clean, clean, changed]
                ),
                mock.patch.object(
                    freeze, "validate_manifest", side_effect=template["manifests"]
                ),
                mock.patch.object(
                    freeze,
                    "hash_named_paths",
                    side_effect=[template["data_files"], template["tool_binaries"]],
                ),
                mock.patch.object(
                    freeze,
                    "validate_store_specs",
                    return_value=(failed_stores, endpoint_urls),
                ),
                mock.patch.object(
                    freeze,
                    "run_sentinels",
                    return_value=template["store_sentinels"],
                ),
                mock.patch.object(
                    freeze, "run_update_canary", return_value=evidence
                ),
                self.assertRaisesRegex(freeze.FreezeError, "while publishing"),
            ):
                freeze.freeze(args)
            self.assertFalse(failed_output.exists())

    @unittest.skipUnless(shutil.which("git"), "git executable required")
    def test_formal_output_must_be_external_or_git_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = Path(directory) / "repo"
            repository.mkdir()
            subprocess.run(["git", "init", "-q", str(repository)], check=True)
            (repository / ".gitignore").write_text("artifacts/\n", encoding="utf-8")
            (repository / "artifacts").mkdir()
            freeze.validate_formal_output_location(
                repository / "artifacts" / "freeze.json", repository
            )
            freeze.validate_formal_output_location(
                Path(directory) / "external.json", repository
            )
            with self.assertRaisesRegex(freeze.FreezeError, "Git-ignored"):
                freeze.validate_formal_output_location(
                    repository / "unignored.json", repository
                )
            with self.assertRaisesRegex(freeze.FreezeError, "Git metadata"):
                freeze.validate_formal_output_location(
                    repository / ".git" / "freeze.json", repository
                )

            tracked = repository / "tracked.json"
            tracked.write_text("{}\n", encoding="utf-8")
            subprocess.run(
                ["git", "-C", str(repository), "add", "tracked.json"], check=True
            )
            with self.assertRaisesRegex(freeze.FreezeError, "tracked path"):
                freeze.validate_formal_output_location(tracked, repository)

    def test_cli_failure_does_not_create_output_or_leak_internal_detail(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(
            freeze, "validate_repository_binding", return_value=Path(freeze.__file__).parents[2]
        ), mock.patch.object(
            freeze, "git_identity", side_effect=freeze.FreezeError("Git worktree is not clean")
        ), contextlib.redirect_stderr(io.StringIO()) as stderr:
            output = Path(directory) / "must-not-exist.json"
            rc = freeze.main([
                "--protocol", "r9-v4", "--allow-empty-inputs", "--output", str(output)
            ])
            self.assertEqual(rc, 2)
            self.assertFalse(output.exists())
            self.assertIn("not clean", stderr.getvalue())


class FrozenBatchValidatorTests(unittest.TestCase):
    def _write(self, directory, name, document):
        path = Path(directory) / name
        path.write_text(
            json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        return path

    def test_load_validates_digest_commit_protocol_and_named_lookup(self):
        document = _valid_frozen_document()
        with tempfile.TemporaryDirectory() as directory:
            path = self._write(directory, "frozen.json", document)
            loaded = freeze.load_frozen_batch(
                path,
                expected_commit="a" * 40,
                expected_protocol="r9-v6",
                required_data=("watdiv-base", "watdiv-reified"),
                required_tools=("d4",),
                required_stores=(("graphdb", "10M"),),
            )
        self.assertEqual(freeze.frozen_tool(loaded, "d4")["sha256"], "9" * 64)
        self.assertEqual(
            freeze.frozen_data(loaded, "watdiv-base")["sha256"], "8" * 64
        )
        self.assertEqual(
            freeze.frozen_store(loaded, "graphdb", "10M")["engine_version"],
            "10.7.6",
        )
        self.assertEqual(
            loaded["batch_id"], freeze.canonical_batch_id(loaded["identity"])
        )
        with self.assertRaisesRegex(freeze.FreezeError, "requested tool"):
            freeze.frozen_tool(loaded, "missing")
        with self.assertRaisesRegex(freeze.FreezeError, "requested store"):
            freeze.frozen_store(loaded, "missing", "10M")

    def test_load_rejects_tampering_schema_dirty_git_and_expectation_mismatch(self):
        document = _valid_frozen_document()
        tampered = copy.deepcopy(document)
        tampered["identity"]["protocol"] = "other"
        dirty = copy.deepcopy(document)
        dirty["identity"]["git"]["clean"] = False
        dirty["batch_id"] = freeze.canonical_batch_id(dirty["identity"])
        wrong_schema = copy.deepcopy(document)
        wrong_schema["schema_version"] += 1
        zero_tool = copy.deepcopy(document)
        zero_tool["identity"]["tool_binaries"][0]["bytes"] = 0
        zero_tool["batch_id"] = freeze.canonical_batch_id(zero_tool["identity"])
        with tempfile.TemporaryDirectory() as directory:
            cases = (
                ("tampered.json", tampered, "batch_id"),
                ("dirty.json", dirty, "Git identity"),
                ("schema.json", wrong_schema, "schema/version"),
                ("zero-tool.json", zero_tool, "invalid hash record"),
            )
            for name, candidate, message in cases:
                with self.subTest(name=name):
                    path = self._write(directory, name, candidate)
                    with self.assertRaisesRegex(freeze.FreezeError, message):
                        freeze.load_frozen_batch(path)
            valid_path = self._write(directory, "valid.json", document)
            with self.assertRaisesRegex(freeze.FreezeError, "expected commit"):
                freeze.load_frozen_batch(valid_path, expected_commit="b" * 40)
            with self.assertRaisesRegex(freeze.FreezeError, "expected protocol"):
                freeze.load_frozen_batch(valid_path, expected_protocol="r9-v7")

    def test_load_rejects_duplicate_json_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "duplicate.json"
            path.write_text(
                '{"schema":"x","schema":"y","schema_version":1,'
                '"batch_id":"' + "0" * 64 + '","identity":{}}\n',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(freeze.FreezeError, "duplicate object keys"):
                freeze.load_frozen_batch(path)

    def test_load_rejects_aliases_exploratory_by_default_and_missing_data(self):
        document = _valid_frozen_document()
        exploratory = copy.deepcopy(document)
        exploratory["identity"]["batch_profile"] = "exploratory"
        exploratory["batch_id"] = freeze.canonical_batch_id(exploratory["identity"])
        with tempfile.TemporaryDirectory() as directory:
            valid = self._write(directory, "valid.json", document)
            symlink = Path(directory) / "symlink.json"
            symlink.symlink_to(valid)
            hardlink = Path(directory) / "hardlink.json"
            os.link(valid, hardlink)
            for alias in (symlink, hardlink):
                with self.subTest(alias=alias.name), self.assertRaisesRegex(
                    freeze.FreezeError, "single-link|aliased"
                ):
                    freeze.load_frozen_batch(alias)
            hardlink.unlink()

            exploratory_path = self._write(
                directory, "exploratory.json", exploratory
            )
            with self.assertRaisesRegex(freeze.FreezeError, "refuses.*exploratory"):
                freeze.load_frozen_batch(exploratory_path)
            loaded = freeze.load_frozen_batch(
                exploratory_path, require_formal=False
            )
            self.assertEqual(loaded["identity"]["batch_profile"], "exploratory")
            with self.assertRaisesRegex(freeze.FreezeError, "requested data"):
                freeze.load_frozen_batch(valid, required_data=("missing",))

    def test_load_rejects_noncanonical_query_paths_data_refs_and_binding_witness(self):
        document = _valid_frozen_document()
        cases = []
        escaped = copy.deepcopy(document)
        escaped["identity"]["manifests"][0]["queries"][0][
            "query_file"
        ] = "../escape.rq"
        cases.append(("escaped.json", escaped, "query identity"))

        missing_data = copy.deepcopy(document)
        missing_data["identity"]["stores"][0]["base_data_name"] = "missing"
        cases.append(("missing-data.json", missing_data, "missing formal data"))

        wrong_origin = copy.deepcopy(document)
        wrong_origin["identity"]["stores"][0]["endpoints"]["update"][
            "origin_sha256"
        ] = "f" * 64
        cases.append(("wrong-origin.json", wrong_origin, "binding is inconsistent"))

        wrong_public_origin = copy.deepcopy(document)
        wrong_public_origin["identity"]["stores"][0]["endpoints"]["update"][
            "port"
        ] = 9999
        cases.append(
            ("wrong-public-origin.json", wrong_public_origin, "binding is inconsistent")
        )

        same_path_witness = copy.deepcopy(document)
        same_path_witness["identity"]["stores"][0]["endpoints"]["update"][
            "path_sha256"
        ] = same_path_witness["identity"]["stores"][0]["endpoints"]["reified"][
            "path_sha256"
        ]
        cases.append(
            ("same-path-witness.json", same_path_witness, "binding is inconsistent")
        )

        identical_data = copy.deepcopy(document)
        identical_data["identity"]["data_files"][1]["sha256"] = identical_data[
            "identity"
        ]["data_files"][0]["sha256"]
        cases.append(("identical-data.json", identical_data, "byte-identical"))

        impossible_ask = copy.deepcopy(document)
        impossible_ask["identity"]["store_sentinels"][0][
            "expected_fingerprint"
        ] = "e" * 64
        impossible_ask["identity"]["store_sentinels"][0][
            "observed_fingerprint"
        ] = "e" * 64
        cases.append(("impossible-ask.json", impossible_ask, "ASK sentinel"))

        with tempfile.TemporaryDirectory() as directory:
            for name, candidate, message in cases:
                candidate["batch_id"] = freeze.canonical_batch_id(
                    candidate["identity"]
                )
                path = self._write(directory, name, candidate)
                with self.subTest(name=name), self.assertRaisesRegex(
                    freeze.FreezeError, message
                ):
                    freeze.load_frozen_batch(path)


if __name__ == "__main__":
    unittest.main()
