"""R9.2 formal answer-parity pass (term-aware and independent of timing).

For every frozen manifest instance:

* B vs R compares the complete canonical binding **multiset**.  Results JSON
  preserves IRI/literal, datatype, language, blank-node, and unbound identity.
* N vs C compares the canonical candidate-answer **set**.  N's provenance
  column is removed; C is decoded from structured ``c:binding/c:var/c:val``.

There is intentionally no B/R-to-N/C equality assertion.  In particular,
OPTIONAL and MINUS provenance construction may enumerate a candidate domain
that is not the full-world B/R result domain.
"""

import argparse
import collections
import contextlib
import csv
import hashlib
import io
import json
import math
import os
import re
import sys
import tempfile
import time

sys.setrecursionlimit(1_000_000)
HERE = os.path.dirname(os.path.abspath(__file__))
REF = os.path.dirname(HERE)
sys.path.insert(0, REF)
sys.path.insert(0, HERE)
import paper_construction_matrix as pcm

FORMAL_CAP = 500_000
FORMAL_TIMEOUT = pcm.FORMAL_TIMEOUT
CAP = FORMAL_CAP


def _with_cap(query, cap):
    query = query.rstrip()
    if not re.search(r"\bLIMIT\s+\d+\b", query, flags=re.IGNORECASE):
        query += f"\nLIMIT {cap + 1}"
    return query


def fetch_json(endpoint, query, cap=CAP, timeout=pcm.TIMEOUT):
    """Fetch a SELECT as Results JSON; return (payload, row_count, capped)."""
    _, _, _, text = pcm.post_timed(
        endpoint,
        _with_cap(query, cap),
        "application/sparql-results+json",
        keep=True,
        timeout=timeout,
    )
    payload = json.loads(text)
    rows = payload.get("results", {}).get("bindings", [])
    return payload, len(rows), len(rows) > cap


def binding_multiset(payload, drop_vars=()):
    """Canonical, term-aware binding multiset (delegates to the timing module)."""
    return pcm.json_binding_multiset(payload, drop_vars=drop_vars)


def candidate_key_set(payload, provenance_var=None, rewritten_query=None):
    variables = payload.get("head", {}).get("vars", [])
    provenance_var = provenance_var or pcm.provenance_output_variable(
        variables, rewritten_query=rewritten_query
    )
    return set(binding_multiset(payload, drop_vars=(provenance_var,)))


def compare_multisets(left, right, capped=False):
    return None if capped else left == right


def compare_candidate_sets(left, right, capped=False):
    return None if capped else set(left) == set(right)


def execute_c_plan(
    endpoint,
    qtext,
    timeout=pcm.TIMEOUT,
    update_endpoint=None,
    read_only=False,
    lock_identity=None,
):
    """Run the same feedback/cleanup protocol as the timing pass."""
    hygiene = bool(update_endpoint) and not read_only
    lock = (
        pcm.endpoint_lock(lock_identity or endpoint)
        if hygiene
        else contextlib.nullcontext()
    )
    with lock:
        if hygiene:
            # Parity is unmeasured; use the same fail-stop orphan preflight
            # without charging it to the query's shared timeout.
            pcm._orphan_cleanup(update_endpoint)
        deadline = time.monotonic() + timeout
        construction = "flat" if read_only else "factored"
        try:
            plan = pcm.c_construct_plan(
                qtext,
                timeout=pcm._remaining(deadline),
                construction=construction,
            )
            plan = pcm._normalize_c_plan(plan)
            if read_only and plan.requires_feedback:
                raise pcm.UnsupportedConstruction(
                    "read-only parity endpoint returned a feedback-requiring flat plan"
                )
            result = pcm._execute_c_once(
                endpoint,
                update_endpoint,
                plan,
                deadline,
                hard_http=True,
            )
            return result[7], result[5]
        except BaseException as primary:
            if hygiene:
                try:
                    pcm._orphan_cleanup(update_endpoint)
                except BaseException as cleanup:
                    pcm._raise_with_cleanup(primary, cleanup, cleanup_attempted=True)
            raise


def _fingerprint_multiset(counter):
    return pcm.multiset_evidence(counter, kind="term-aware-binding-multiset-v1")[
        "answer_fingerprint"
    ]


def _fingerprint_set(values):
    return pcm.set_evidence(values)["answer_fingerprint"]


def _bool_text(value):
    return {True: "OK", False: "MISMATCH", None: "capped/err"}[value]


def gate_exit_code(rows, allow_unverified=False):
    """Fail closed: a correctness gate must never turn missing evidence green."""
    if not rows:
        return 0 if allow_unverified else 1
    def state(value):
        if value is True or value == "True":
            return True
        if value is False or value == "False":
            return False
        return None

    states = [
        state(row.get(field))
        for row in rows
        for field in ("br_multiset_equal", "nc_keys_equal")
    ]
    mismatch = any(value is False for value in states)
    unverified = any(value is None for value in states)
    return 1 if mismatch or (unverified and not allow_unverified) else 0


COLS = [
    "protocol",
    "commit",
    "batch_id",
    "engine",
    "engine_version",
    "scale",
    "class",
    "template",
    "instance",
    "query_sha256",
    "base_endpoint_sha256",
    "reified_endpoint_sha256",
    "update_endpoint_sha256",
    "base_data_identity_sha256",
    "reified_data_identity_sha256",
    "update_for",
    "access_mode",
    "base_data_name",
    "reified_data_name",
    "update_canary_sha256",
    "store_instance_sha256",
    "store_discriminator_sha256",
    "tool_sha256",
    "java_runtime_sha256",
    "run_identity_sha256",
    "b_rows",
    "r_rows",
    "br_multiset_equal",
    "br_kind",
    "br_b_fingerprint",
    "br_r_fingerprint",
    "n_candidates",
    "c_candidates",
    "n_answer_rows",
    "c_answer_gates",
    "nc_keys_equal",
    "nc_kind",
    "n_fingerprint",
    "c_fingerprint",
    # Backwards-readable aliases used by the first R9.2 scripts.
    "n_distinct",
    "c_distinct",
    "nc_count_equal",
    "notes",
]


def run_one(args, engine, scale):
    config = args.registry.get(engine)
    if not config or scale not in config:
        raise SystemExit(f"unregistered engine/scale: {engine}/{scale}")
    endpoints = config[scale]
    pcm.validate_endpoint_registration(
        config,
        endpoints,
        require_update=not endpoints.get(
            "read_only", config.get("read_only", False)
        ),
    )
    classes = set(filter(None, args.classes.split(",")))
    manifest = [
        row
        for row in pcm.load_manifest(frozen_document=args.frozen_document)
        if row["scale"] == scale and row["class"] in classes
    ]

    output = []
    print(
        f"{'cls/tmpl':10} {'B':>8} {'R':>8} {'B==R bindings':16} "
        f"{'Nkeys':>8} {'Ckeys':>8} {'N==C keys':11} note"
    )
    for row in manifest:
        cls, template, instance = row["class"], row["template"], row["instance"]
        identity = pcm.cell_identity(
            engine,
            scale,
            row["query_sha256"],
            config,
            endpoints,
            batch_id=args.batch_id,
        )
        qtext = pcm.read_query_verified(row)
        notes = []
        b_count = r_count = n_rows = c_gates = None
        b_counter = r_counter = collections.Counter()
        n_keys = c_keys = set()
        br_equal = nc_equal = None
        b_fp = r_fp = n_fp = c_fp = ""

        # B/R: exact term-aware binding multiset.
        try:
            b_json, b_count, b_capped = fetch_json(
                endpoints["base"], pcm.q_base(qtext), args.cap, args.timeout
            )
            r_json, r_count, r_capped = fetch_json(
                endpoints["reified"], pcm.q_reify(qtext), args.cap, args.timeout
            )
            b_counter = binding_multiset(b_json)
            r_counter = binding_multiset(r_json)
            b_fp, r_fp = _fingerprint_multiset(b_counter), _fingerprint_multiset(r_counter)
            br_equal = compare_multisets(
                b_counter, r_counter, capped=b_capped or r_capped
            )
            if b_capped or r_capped:
                notes.append(f"B/R capped@{args.cap}; equality not asserted")
        except Exception as ex:
            notes.append(f"B/R err:{type(ex).__name__}:{str(ex)[:80]}")

        # N/C: exact term-aware candidate answer keys.  Do not compare to B/R.
        try:
            n_query = pcm.q_npcs(qtext, timeout=args.timeout)
            n_json, n_rows, n_capped = fetch_json(
                endpoints["reified"],
                n_query,
                args.cap,
                args.timeout,
            )
            n_keys = candidate_key_set(n_json, rewritten_query=n_query)
            n_fp = _fingerprint_set(n_keys)
            if n_capped:
                notes.append(f"N capped@{args.cap}; N/C equality not asserted")
        except Exception as ex:
            n_capped = True
            notes.append(f"N err:{type(ex).__name__}:{str(ex)[:80]}")
        try:
            c_keys, c_gates = execute_c_plan(
                endpoints["reified"],
                qtext,
                timeout=args.timeout,
                update_endpoint=endpoints.get("update"),
                read_only=endpoints.get(
                    "read_only", config.get("read_only", False)
                ),
                lock_identity=identity.get("store_instance_sha256"),
            )
            c_fp = _fingerprint_set(c_keys)
        except Exception as ex:
            c_keys = set()
            c_gates = None
            notes.append(f"C err:{type(ex).__name__}:{str(ex)[:80]}")
        if n_rows is not None and c_gates is not None:
            nc_equal = compare_candidate_sets(n_keys, c_keys, capped=n_capped)

        print(
            f"{cls+'/'+template:10} {str(b_count):>8} {str(r_count):>8} "
            f"{_bool_text(br_equal):16} {len(n_keys):>8} {len(c_keys):>8} "
            f"{_bool_text(nc_equal):11} {'; '.join(notes)}"
        )
        output.append(
            {
                "protocol": pcm.PROTOCOL,
                "commit": pcm.COMMIT,
                "batch_id": args.batch_id,
                "query_sha256": row["query_sha256"],
                "engine": engine,
                "engine_version": config["version"],
                "scale": scale,
                "class": cls,
                "template": template,
                "instance": instance,
                "b_rows": b_count,
                "r_rows": r_count,
                "br_multiset_equal": br_equal,
                "br_kind": "term-aware-binding-multiset-v1",
                "br_b_fingerprint": b_fp,
                "br_r_fingerprint": r_fp,
                "n_candidates": len(n_keys) if n_rows is not None else None,
                "c_candidates": len(c_keys) if c_gates is not None else None,
                "n_answer_rows": n_rows,
                "c_answer_gates": c_gates,
                "nc_keys_equal": nc_equal,
                "nc_kind": "term-aware-candidate-set-v1",
                "n_fingerprint": n_fp,
                "c_fingerprint": c_fp,
                "n_distinct": len(n_keys) if n_rows is not None else None,
                "c_distinct": len(c_keys) if c_gates is not None else None,
                "nc_count_equal": nc_equal,
                "notes": "; ".join(notes),
                **identity,
            }
        )

    return output


def _parity_key(row):
    names = (
        "protocol",
        "commit",
        "batch_id",
        "engine",
        "scale",
        "class",
        "template",
        "instance",
        "query_sha256",
        "engine_version",
        "base_endpoint_sha256",
        "reified_endpoint_sha256",
        "update_endpoint_sha256",
        "base_data_identity_sha256",
        "reified_data_identity_sha256",
        "update_for",
        "access_mode",
        "base_data_name",
        "reified_data_name",
        "update_canary_sha256",
        "store_instance_sha256",
        "store_discriminator_sha256",
        "tool_sha256",
        "java_runtime_sha256",
        "run_identity_sha256",
    )
    key = tuple(row.get(name, "") for name in names)
    if not all(key):
        return None
    if (
        not pcm.COMMIT_RE.fullmatch(str(row.get("commit")))
        or not pcm.BATCH_ID_RE.fullmatch(str(row.get("batch_id")))
        or not pcm.BATCH_ID_RE.fullmatch(str(row.get("query_sha256")))
    ):
        return None
    return key


def _parity_logical_key(row):
    names = ("engine", "scale", "class", "template", "instance")
    key = tuple(row.get(name, "") for name in names)
    return key if all(key) else None


def _parity_cell_key(row):
    names = ("engine", "scale", "class", "template", "instance", "query_sha256")
    key = tuple(row.get(name, "") for name in names)
    return key if all(key) else None


def _parse_parity_payload(payload):
    """Strictly parse already-snapshotted complete parity CSV bytes."""
    if not payload:
        return []
    try:
        text = payload.decode("utf-8", "strict")
    except UnicodeDecodeError as ex:
        raise ValueError("R9 parity checkpoint is not UTF-8") from ex
    physical = text.splitlines(keepends=True)
    parsed = []
    for line_no, raw in enumerate(physical, 1):
        try:
            records = list(csv.reader([raw], strict=True))
        except csv.Error as ex:
            raise ValueError(
                f"R9 parity checkpoint has malformed CSV at line {line_no}"
            ) from ex
        if len(records) != 1:
            raise ValueError("R9 parity checkpoint contains a multiline record")
        parsed.append(records[0])
    if not parsed or parsed[0] != COLS:
        raise ValueError("R9 parity checkpoint schema is not protocol v7")
    rows = []
    for line_no, values in enumerate(parsed[1:], 2):
        if len(values) != len(COLS):
            raise ValueError(
                f"R9 parity checkpoint row {line_no} has the wrong field count"
            )
        rows.append(dict(zip(COLS, values)))
    return rows


def _parity_snapshot(path, *, allow_torn_tail=False):
    """Read and parse the exact stable CSV bytes used for parity decisions."""
    if not os.path.lexists(path):
        return b"", []
    payload = pcm._read_stable_bytes(path, "R9 parity checkpoint")
    if not payload:
        return payload, []
    if not payload.endswith((b"\n", b"\r")):
        if not allow_torn_tail:
            raise ValueError("R9 parity checkpoint has a torn final record")
        boundary = payload.rfind(b"\n")
        payload = payload[: boundary + 1] if boundary >= 0 else b""
    return payload, _parse_parity_payload(payload)


def _repair_parity_tail(path):
    if not os.path.lexists(path):
        return
    descriptor = pcm._open_single_link(
        path, os.O_RDWR, "R9 parity checkpoint"
    )
    try:
        before = pcm._validate_opened_single_link(
            path, descriptor, "R9 parity checkpoint"
        )
        payload = pcm._read_open_descriptor(
            path, descriptor, "R9 parity checkpoint"
        )
        if not payload or payload.endswith((b"\n", b"\r")):
            return
        boundary = payload.rfind(b"\n")
        complete = payload[: boundary + 1] if boundary >= 0 else b""
        # A malformed complete record is corruption, not a repairable tail.
        _parse_parity_payload(complete)
        os.ftruncate(descriptor, boundary + 1 if boundary >= 0 else 0)
        os.fsync(descriptor)
        after = pcm._validate_opened_single_link(
            path, descriptor, "R9 parity checkpoint"
        )
        if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
            raise ValueError("R9 parity inode changed during tail repair")
    finally:
        os.close(descriptor)
    pcm._fsync_directory(os.path.dirname(os.path.abspath(path)) or ".")


def _publish_parity_csv(path, rows):
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    rendered = io.StringIO(newline="")
    writer = csv.DictWriter(
        rendered, fieldnames=COLS, extrasaction="ignore", restval=""
    )
    writer.writeheader()
    writer.writerows(rows)
    payload = rendered.getvalue().encode("utf-8")
    temporary = tempfile.NamedTemporaryFile(
        "wb", prefix=".brnc-parity-", suffix=".tmp", dir=directory, delete=False
    )
    temporary_path = temporary.name
    try:
        with temporary:
            temporary.write(payload)
            temporary.flush()
            os.fsync(temporary.fileno())
            if os.fstat(temporary.fileno()).st_nlink != 1:
                raise ValueError("R9 parity temporary gained a hardlink")
        if os.path.lexists(path):
            descriptor = pcm._open_single_link(
                path, os.O_RDONLY, "R9 parity checkpoint"
            )
            os.close(descriptor)
        os.replace(temporary_path, path)
        descriptor = pcm._open_single_link(
            path, os.O_RDONLY, "R9 parity checkpoint"
        )
        try:
            if os.fstat(descriptor).st_size != len(payload):
                raise ValueError("R9 parity publication size mismatch")
        finally:
            os.close(descriptor)
        pcm._fsync_directory(directory)
    finally:
        try:
            os.unlink(temporary_path)
        except FileNotFoundError:
            pass


def merge_parity_rows(path, new_rows, _lock_held=False):
    """Atomically merge/replace exact cells while retaining other combinations."""
    if not _lock_held:
        os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
        with pcm.invocation_file_lock(path):
            return merge_parity_rows(path, new_rows, _lock_held=True)
    _repair_parity_tail(path)
    merged = {}
    batch_ids = {row.get("batch_id") for row in new_rows if row.get("batch_id")}
    active_batch = next(iter(batch_ids)) if batch_ids else pcm.require_batch_id()
    if len(batch_ids) > 1:
        raise ValueError("cannot merge parity rows from multiple batch IDs")
    _payload, existing_rows = _parity_snapshot(path)
    for row in existing_rows:
        if (
            row.get("protocol") != pcm.PROTOCOL
            or row.get("commit") != pcm.COMMIT
            or row.get("batch_id") != active_batch
        ):
            raise ValueError(
                "R9 parity checkpoint mixes commit/protocol/batch identities"
            )
        key = _parity_key(row)
        if key is None:
            raise ValueError("R9 parity checkpoint contains an incomplete key")
        merged[key] = row
    for row in new_rows:
        key = _parity_key(row)
        if key is None:
            raise ValueError(f"parity row has incomplete key: {row!r}")
        logical = _parity_logical_key(row)
        for old_key, old_row in list(merged.items()):
            if _parity_logical_key(old_row) == logical and old_key != key:
                del merged[old_key]
        merged[key] = row
    _publish_parity_csv(path, [merged[key] for key in sorted(merged)])
    return [merged[key] for key in sorted(merged)]


def _requested(single, multiple, default):
    raw = multiple if multiple else (single or default)
    return [value.strip() for value in raw.split(",") if value.strip()]


def _expected_parity_cells(args, engines, scales):
    classes = set(filter(None, getattr(args, "classes", ",".join(pcm.FORMAL_CLASSES)).split(",")))
    manifest = pcm.load_manifest(
        frozen_document=getattr(args, "frozen_document", None)
    )
    if not getattr(args, "exploratory", False):
        missing_dimensions = [
            (scale, cls)
            for scale in scales
            for cls in classes
            if not any(
                row["scale"] == scale and row["class"] == cls
                for row in manifest
            )
        ]
        if missing_dimensions:
            raise RuntimeError(
                "formal parity manifest lacks %d selected scale/class dimension(s)"
                % len(missing_dimensions)
            )
    cells = [
        (
            engine,
            scale,
            row["class"],
            row["template"],
            row["instance"],
            row["query_sha256"],
        )
        for engine in engines
        for scale in scales
        for row in manifest
        if row["scale"] == scale and row["class"] in classes
    ]
    if len(cells) != len(set(cells)):
        raise ValueError("formal parity slot schedule contains a duplicate")
    return set(cells)


def _expected_parity_identities(args, engines, scales):
    """Build formal parity slots directly from the frozen registry/manifest."""
    cells = _expected_parity_cells(args, engines, scales)
    registry = getattr(args, "registry", None)
    if not isinstance(registry, dict):
        raise ValueError("formal parity identity validation requires a frozen registry")
    expected = {}
    for key in sorted(cells):
        engine, scale, _cls, _template, _instance, query_sha = key
        config = registry.get(engine)
        endpoints = config.get(scale) if isinstance(config, dict) else None
        if not isinstance(endpoints, dict):
            raise ValueError(f"missing frozen parity identity for {engine}/{scale}")
        expected[key] = pcm.cell_identity(
            engine,
            scale,
            query_sha,
            config,
            endpoints,
            batch_id=args.batch_id,
        )
    return expected


def validate_formal_parity_rows(
    rows, expected_identities, *, commit, batch_id, require_full=True
):
    """Validate exact formal evidence and its complete frozen cell identity."""
    planned = list(expected_identities)
    if require_full and len(rows) != len(planned):
        raise RuntimeError(
            "formal parity coverage is %d/%d cells" % (len(rows), len(planned))
        )
    if len(rows) > len(planned):
        raise ValueError("formal parity contains more rows than planned cells")
    for index, row in enumerate(rows):
        if set(row) != set(COLS):
            raise ValueError(f"formal parity row {index + 1} schema mismatch")
        key = _parity_cell_key(row)
        if key != planned[index]:
            raise ValueError(
                "formal parity rows are sparse, reordered, or duplicated at row %d"
                % (index + 1)
            )
        expected = expected_identities[key]
        for name in pcm.IDENTITY_FIELDS:
            if row.get(name) != expected.get(name):
                raise ValueError(
                    "formal parity row %d has a non-frozen %s identity"
                    % (index + 1, name)
                )
        if row.get("commit") != commit or row.get("batch_id") != batch_id:
            raise ValueError("formal parity row has the wrong completion identity")
        if (
            row.get("engine") != key[0]
            or row.get("scale") != key[1]
            or tuple(row.get(name) for name in ("class", "template", "instance"))
            != key[2:5]
            or row.get("query_sha256") != key[5]
        ):
            raise ValueError("formal parity row key/identity mismatch")
        if (
            row.get("br_multiset_equal") != "True"
            or row.get("nc_keys_equal") != "True"
            or row.get("nc_count_equal") != "True"
            or row.get("br_kind") != "term-aware-binding-multiset-v1"
            or row.get("nc_kind") != "term-aware-candidate-set-v1"
        ):
            raise ValueError("formal parity row lacks canonical True/kind evidence")
        fingerprints = (
            row.get("br_b_fingerprint"),
            row.get("br_r_fingerprint"),
            row.get("n_fingerprint"),
            row.get("c_fingerprint"),
        )
        if any(
            type(value) is not str or not pcm.BATCH_ID_RE.fullmatch(value)
            for value in fingerprints
        ) or fingerprints[0] != fingerprints[1] or fingerprints[2] != fingerprints[3]:
            raise ValueError("formal parity fingerprints are invalid or disagree")
        count_names = (
            "b_rows",
            "r_rows",
            "n_candidates",
            "c_candidates",
            "n_answer_rows",
            "c_answer_gates",
            "n_distinct",
            "c_distinct",
        )
        counts = {name: pcm._canonical_uint(row.get(name)) for name in count_names}
        if any(value is None for value in counts.values()):
            raise ValueError("formal parity counts are not canonical nonnegative integers")
        if (
            counts["b_rows"] != counts["r_rows"]
            or counts["n_candidates"] != counts["c_candidates"]
            or counts["n_candidates"] != counts["n_distinct"]
            or counts["c_candidates"] != counts["c_distinct"]
            or counts["n_candidates"] > counts["n_answer_rows"]
        ):
            raise ValueError("formal parity counts/aliases do not correspond")
    return tuple(planned[: len(rows)])


def finalize_parity_completion(path, rows, expected_identities, args, engines, scales):
    payload, exact_rows = _parity_snapshot(path)
    if not expected_identities:
        raise RuntimeError("formal parity shard has zero expected cells")
    exact_identity_keys = [_parity_key(row) for row in exact_rows]
    supplied_identity_keys = [_parity_key(row) for row in rows]
    if (
        any(key is None for key in exact_identity_keys)
        or len(set(exact_identity_keys)) != len(exact_identity_keys)
        or set(exact_identity_keys) != set(supplied_identity_keys)
    ):
        raise ValueError("parity completion did not use the published CSV cell set")
    validate_formal_parity_rows(
        exact_rows,
        expected_identities,
        commit=pcm.COMMIT,
        batch_id=args.batch_id,
    )
    profile = {
        "engines": list(engines),
        "scales": list(scales),
        "classes": list(filter(None, args.classes.split(","))),
        "cap": args.cap,
        "timeout_s": args.timeout,
        "update_chunk_triples": pcm.FORMAL_UPDATE_CHUNK_TRIPLES,
        "orphan_cleanup_timeout_s": pcm.FORMAL_ORPHAN_CLEANUP_TIMEOUT,
    }
    identity_records = pcm._ordered_identity_records(expected_identities)
    expected_keys = [record["key"] for record in identity_records]
    document = pcm._sealed_json(
        {
            "schema": "r9-parity-completion-v1",
            "protocol": pcm.PROTOCOL,
            "commit": pcm.COMMIT,
            "batch_id": args.batch_id,
            "profile": profile,
            "profile_sha256": pcm._canonical_digest(profile),
            "expected_cells": len(expected_identities),
            "expected_keys": expected_keys,
            "expected_keys_sha256": pcm._canonical_digest(expected_keys),
            "expected_identities": identity_records,
            "expected_identities_sha256": pcm._canonical_digest(identity_records),
            "completed_cells": len(expected_identities),
            "csv_sha256": hashlib.sha256(payload).hexdigest(),
            "csv_bytes": len(payload),
            "csv_rows": len(exact_rows),
        }
    )
    pcm._validate_completion_document(
        document,
        expected_schema="r9-parity-completion-v1",
        label="R9 parity completion sidecar",
    )
    completion = pcm._completion_path(path)
    if os.path.lexists(completion):
        existing = pcm.verify_completion_sidecar(
            path,
            expected_schema="r9-parity-completion-v1",
            label="R9 parity completion sidecar",
            csv_payload=payload,
            csv_rows=len(exact_rows),
        )
        if pcm._canonical_json_bytes(existing) != pcm._canonical_json_bytes(document):
            raise ValueError(
                "R9 parity completion sidecar does not bind the current CSV/profile"
            )
    else:
        pcm._atomic_publish_json(
            completion, document, "R9 parity completion sidecar"
        )
        pcm.verify_completion_sidecar(
            path,
            expected_schema="r9-parity-completion-v1",
            label="R9 parity completion sidecar",
            csv_payload=payload,
            csv_rows=len(exact_rows),
        )
    return document


def run(args):
    args.batch_id = pcm.require_batch_id(args.batch_id)
    engines = _requested(getattr(args, "engine", None), getattr(args, "engines", None), "graphdb")
    scales = _requested(getattr(args, "scale", None), getattr(args, "scales", None), "10M")
    expected_identities = (
        {}
        if getattr(args, "exploratory", False)
        else _expected_parity_identities(args, engines, scales)
    )
    if (
        not getattr(args, "exploratory", False)
        and os.path.lexists(pcm._completion_path(args.out))
    ):
        try:
            _payload, existing = _parity_snapshot(args.out)
            finalize_parity_completion(
                args.out, existing, expected_identities, args, engines, scales
            )
        except (ValueError, RuntimeError) as ex:
            print(f"FORMAL COMPLETION FAILED: {ex}", file=sys.stderr)
            return 1
        print(f"verified complete {args.out}")
        return 0
    output = []
    for engine in engines:
        for scale in scales:
            print(f"\n[{engine} {scale}] term-aware parity")
            output.extend(run_one(args, engine, scale))
    merged = merge_parity_rows(args.out, output, _lock_held=True)
    br_fail = [row for row in output if row["br_multiset_equal"] is False]
    nc_fail = [row for row in output if row["nc_keys_equal"] is False]
    unverified = [
        row
        for row in output
        if row["br_multiset_equal"] is None or row["nc_keys_equal"] is None
    ]
    print(f"\nwrote {args.out} ({len(output)} checked; {len(merged)} retained total)")
    print(
        f"B==R term-aware multiset: "
        f"{sum(row['br_multiset_equal'] is True for row in output)} OK, "
        f"{len(br_fail)} MISMATCH"
    )
    print(
        f"N==C term-aware candidate set: "
        f"{sum(row['nc_keys_equal'] is True for row in output)} OK, "
        f"{len(nc_fail)} MISMATCH"
    )
    if unverified:
        print(
            f"UNVERIFIED: {len(unverified)} instances (error/capped); "
            + ("allowed for exploratory collection" if args.allow_unverified else "formal gate fails closed")
        )
    # The formal gate covers every retained row for this active
    # commit/protocol/batch, including cells collected by earlier invocations.
    exit_code = gate_exit_code(merged, allow_unverified=args.allow_unverified)
    if not getattr(args, "exploratory", False):
        try:
            finalize_parity_completion(
                args.out, merged, expected_identities, args, engines, scales
            )
        except (ValueError, RuntimeError) as ex:
            print(f"FORMAL COMPLETION FAILED: {ex}", file=sys.stderr)
            return 1
    return exit_code


def _assert_output_identity(path, commit, batch_id):
    _payload, rows = _parity_snapshot(path)
    for row in rows:
        if (
            row.get("protocol") != pcm.PROTOCOL
            or row.get("commit") != commit
            or row.get("batch_id") != batch_id
        ):
            raise ValueError(
                "formal parity output contains a different commit/protocol/batch"
            )


def main(argv=None):
    parser = argparse.ArgumentParser(
        epilog=(
            "Formal parity requires PCM_FROZEN_INPUTS + PCM_BATCH_ID and a clean full Git HEAD. "
            "Use --exploratory only for explicit smoke tests."
        )
    )
    parser.add_argument("--engine", help="single-engine compatibility option")
    parser.add_argument(
        "--engines", help="comma list: graphdb,oxigraph,qlever,millenniumdb"
    )
    parser.add_argument("--scale", help="single-scale compatibility option")
    parser.add_argument("--scales", help="comma-separated scale list")
    parser.add_argument("--classes", default="L,S,F,C,O,M")
    parser.add_argument("--cap", type=int, default=CAP)
    parser.add_argument("--timeout", type=float, default=FORMAL_TIMEOUT)
    parser.add_argument("--out")
    parser.add_argument("--exploratory", action="store_true")
    parser.add_argument(
        "--allow-unverified",
        action="store_true",
        help="exploratory mode: allow error/capped cells, but never a verified mismatch",
    )
    args = parser.parse_args(argv)
    if args.cap <= 0 or not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("--cap and --timeout must be positive")
    if args.allow_unverified and not args.exploratory:
        parser.error("--allow-unverified requires --exploratory")
    engines = _requested(args.engine, args.engines, "graphdb")
    scales = _requested(args.scale, args.scales, "10M")
    classes = [value.strip() for value in args.classes.split(",") if value.strip()]
    if len(set(engines)) != len(engines) or len(set(scales)) != len(scales):
        parser.error("engine/scale shards must not contain duplicates")
    if len(set(classes)) != len(classes):
        parser.error("--classes must not contain duplicates")
    unknown_engines = set(engines) - set(pcm.ENGINES)
    if unknown_engines:
        parser.error(f"unknown engines: {sorted(unknown_engines)}")
    unknown_classes = set(classes) - set(pcm.FORMAL_CLASSES)
    if unknown_classes:
        parser.error(f"unknown classes: {sorted(unknown_classes)}")
    if not args.exploratory:
        if args.cap != FORMAL_CAP or args.timeout != FORMAL_TIMEOUT:
            parser.error(
                "formal parity fixes cap=500000 and timeout=300; "
                "overrides require --exploratory"
            )
        if tuple(classes) != pcm.FORMAL_CLASSES:
            parser.error(
                "formal parity shards may select only engine/scale; "
                "every shard must cover classes L,S,F,C,O,M"
            )
    formal_commit = None
    try:
        try:
            if args.exploratory:
                args.batch_id = pcm.require_batch_id()
                args.registry = pcm.bind_exploratory_registry(
                    engines, scales, args.batch_id
                )
                args.frozen_document = None
            else:
                formal_commit = pcm.clean_git_identity()
                if formal_commit != pcm.COMMIT:
                    raise RuntimeError(
                        "module Git commit differs from invocation-start HEAD"
                    )
                context = pcm.load_formal_context(
                    engines, scales, ("B", "R", "N", "C")
                )
                args.batch_id = context["batch_id"]
                args.registry = context["registry"]
                args.frozen_document = context["document"]
                pcm._FORMAL_TOOL_SNAPSHOTS = context["tool_snapshots"]
            run_root = os.path.join(
                pcm.DEFAULT_ARTIFACT_ROOT,
                ("exploratory-" if args.exploratory else "") + args.batch_id,
            )
            args.out = pcm._prepare_artifact_path(
                args.out or os.path.join(run_root, "brnc_parity.csv"),
                exploratory=args.exploratory,
            )
        except (ValueError, RuntimeError, pcm.freeze_inputs.FreezeError) as ex:
            parser.error(str(ex))

        with pcm.invocation_file_lock(args.out):
            _repair_parity_tail(args.out)
            if not args.exploratory:
                _assert_output_identity(args.out, formal_commit, args.batch_id)
            return run(args)
    finally:
        if formal_commit is not None:
            try:
                for snapshot in (pcm._FORMAL_TOOL_SNAPSHOTS or {}).values():
                    pcm._verify_tool_snapshot(snapshot)
                pcm.verify_git_end(formal_commit)
            finally:
                pcm._FORMAL_TOOL_SNAPSHOTS = None


if __name__ == "__main__":
    raise SystemExit(main())
