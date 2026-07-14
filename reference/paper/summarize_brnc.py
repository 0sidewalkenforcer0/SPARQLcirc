"""Summarize R9.2 B/R/N/C timing and join the formal answer-parity gate.

Timing deltas are signed and never clamped::

    reification = R - B       NPCS provenance = N - R
    circuit provenance = C - R

Parity is never inferred from answer counts.  The preferred source is
``brnc_parity.csv`` from ``verify_brnc_parity.py`` (term-aware Results JSON and
structured circuit bindings).  If that file is absent, B/R alone may fall back
to matching normalized-CSV multiset fingerprints embedded by the timing
harness.  N/C has no lossy CSV fallback and remains ``unverified``.
"""

import argparse
import collections
import contextlib
import csv
import io
import os
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import paper_construction_matrix as pcm
import verify_brnc_parity as parity_gate

DEFAULT_SRC = os.path.join(HERE, "construction_brnc.csv")
DEFAULT_PARITY = os.path.join(HERE, "brnc_parity.csv")
DEFAULT_OUT = os.path.join(HERE, "brnc_decomposition.csv")
FORMAL_WARMUPS = pcm.FORMAL_WARMUPS
FORMAL_RUNS = pcm.FORMAL_RUNS
FORMAL_TIMEOUT = pcm.FORMAL_TIMEOUT


def num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_bool(value):
    if isinstance(value, bool):
        return value
    value = str(value or "").strip().lower()
    if value in ("true", "1", "yes", "ok"):
        return True
    if value in ("false", "0", "no", "mismatch", "diff"):
        return False
    return None


def _read_csv(path):
    if not path or not os.path.exists(path):
        return []
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh))


def _timing_cells(path, current_batch_id=None, rows=None):
    try:
        current_batch_id = pcm.require_batch_id(current_batch_id)
    except ValueError:
        return {}
    latest = {}
    for row in (pcm._checkpoint_rows(path) if rows is None else rows) or ():
        required = ("engine", "scale", "class", "template", "instance", "method")
        if not all(row.get(name) for name in required):
            continue
        try:
            warmups = int(row.get("warmups"))
            runs = int(row.get("runs"))
            timeout = float(row.get("timeout_s"))
        except (TypeError, ValueError):
            continue
        # Formal R9 tables use the frozen protocol, not whatever repetition
        # counts a row happens to claim. Short exploratory runs remain valid
        # checkpoints for their collector, but cannot enter the paper summary.
        if (warmups != FORMAL_WARMUPS or runs != FORMAL_RUNS
                or abs(timeout - FORMAL_TIMEOUT) > 1e-9):
            continue
        if not pcm.checkpoint_complete(
            row,
            warmups=warmups,
            runs=FORMAL_RUNS,
            timeout=FORMAL_TIMEOUT,
            current_commit=pcm.COMMIT,
            current_batch_id=current_batch_id,
        ):
            continue
        key = tuple(row[name] for name in required[:-1])
        latest.setdefault(key, {})[row["method"]] = row
    return latest


def _parity_maps(path, rows=None, current_commit=None, current_batch_id=None):
    exact, legacy = {}, {}
    for row in (_read_csv(path) if rows is None else rows):
        if current_commit is not None and row.get("commit") != current_commit:
            continue
        if current_batch_id is not None and row.get("batch_id") != current_batch_id:
            continue
        base = (row.get("scale"), row.get("class"), row.get("template"))
        if all(base):
            legacy[base] = row
        exact_key = (
            row.get("engine"),
            row.get("scale"),
            row.get("class"),
            row.get("template"),
            row.get("instance"),
        )
        if all(exact_key):
            exact[exact_key] = row
    return exact, legacy


def _validate_completion_pair(src, parity_path):
    """Return exact audited rows after checking both completion proofs."""
    timing_payload, timing_rows = pcm._strict_timing_snapshot(src)
    timing = pcm.verify_completion_sidecar(
        src,
        expected_schema="r9-timing-completion-v1",
        label="R9 timing completion sidecar",
        csv_payload=timing_payload,
        csv_rows=len(timing_rows),
    )
    parity_payload, parity_rows = parity_gate._parity_snapshot(parity_path)
    parity = pcm.verify_completion_sidecar(
        parity_path,
        expected_schema="r9-parity-completion-v1",
        label="R9 parity completion sidecar",
        csv_payload=parity_payload,
        csv_rows=len(parity_rows),
    )
    if (
        timing["commit"] != pcm.COMMIT
        or parity["commit"] != pcm.COMMIT
        or timing["batch_id"] != parity["batch_id"]
        or timing["commit"] != parity["commit"]
    ):
        raise ValueError("timing/parity completion identities do not match current Git")
    timing_profile = timing["profile"]
    parity_profile = parity["profile"]
    if (
        timing_profile.get("warmups") != pcm.FORMAL_WARMUPS
        or timing_profile.get("runs") != pcm.FORMAL_RUNS
        or float(timing_profile.get("timeout_s", -1)) != pcm.FORMAL_TIMEOUT
        or timing_profile.get("update_chunk_triples")
        != pcm.FORMAL_UPDATE_CHUNK_TRIPLES
        or float(timing_profile.get("orphan_cleanup_timeout_s", -1))
        != pcm.FORMAL_ORPHAN_CLEANUP_TIMEOUT
        or tuple(timing_profile.get("classes", ())) != pcm.FORMAL_CLASSES
        or set(timing_profile.get("methods", ())) != set(pcm.FORMAL_METHODS)
        or timing_profile.get("require_circuit_cache") is not True
        or parity_profile.get("cap") != parity_gate.FORMAL_CAP
        or float(parity_profile.get("timeout_s", -1))
        != parity_gate.FORMAL_TIMEOUT
        or parity_profile.get("update_chunk_triples")
        != pcm.FORMAL_UPDATE_CHUNK_TRIPLES
        or float(parity_profile.get("orphan_cleanup_timeout_s", -1))
        != pcm.FORMAL_ORPHAN_CLEANUP_TIMEOUT
        or tuple(parity_profile.get("classes", ())) != pcm.FORMAL_CLASSES
        or set(timing_profile.get("engines", ()))
        != set(parity_profile.get("engines", ()))
        or set(timing_profile.get("scales", ()))
        != set(parity_profile.get("scales", ()))
    ):
        raise ValueError("timing/parity completion profiles are not the formal R9 profile")

    timing_latest = {}
    timing_expected = pcm.completion_expected_identities(timing)
    pcm.validate_checkpoint_prefix(
        timing_rows, timing_expected, require_full=True
    )
    for row in timing_rows:
        key = pcm._row_key(row)
        metadata = pcm.unpack_note(row)
        if (
            key is None
            or row.get("commit") != timing["commit"]
            or row.get("protocol") != pcm.PROTOCOL
            or row.get("batch_id") != timing["batch_id"]
            or metadata.get("protocol") != pcm.PROTOCOL
            or metadata.get("batch_id") != timing["batch_id"]
        ):
            raise ValueError("timing CSV contains an invalid completion identity")
        timing_latest[key] = row
    if len(timing_latest) != timing["expected_cells"] or any(
        not pcm.checkpoint_complete(
            row,
            warmups=pcm.FORMAL_WARMUPS,
            runs=pcm.FORMAL_RUNS,
            timeout=pcm.FORMAL_TIMEOUT,
            require_circuit_cache=True,
            expected_identity=timing_expected[pcm._row_key(row)],
            current_commit=timing["commit"],
            current_batch_id=timing["batch_id"],
        )
        for row in timing_latest.values()
    ):
        raise ValueError("timing completion rows no longer pass the formal checkpoint gate")
    status_counts = dict(
        sorted(
            collections.Counter(
                row["status"] for row in timing_latest.values()
            ).items()
        )
    )
    if status_counts != timing.get("terminal_status_counts"):
        raise ValueError("timing completion status coverage mismatch")
    canonical = pcm.canonical_circuit_gate(
        timing_rows, commit=timing["commit"], batch_id=timing["batch_id"]
    )
    if canonical != timing.get("canonical_circuits"):
        raise ValueError("timing completion canonical-circuit proof mismatch")

    parity_expected = pcm.completion_expected_identities(parity)
    parity_gate.validate_formal_parity_rows(
        parity_rows,
        parity_expected,
        commit=parity["commit"],
        batch_id=parity["batch_id"],
    )
    parity_cells = list(parity_expected)
    timing_cells = {key[:-1] for key in timing_latest}
    if timing_cells != set(parity_cells) or timing["expected_cells"] != (
        len(pcm.FORMAL_METHODS) * parity["expected_cells"]
    ):
        raise ValueError("timing/parity completion cell coverage does not correspond")
    timing_cell_identities = {}
    for key, identity in timing_expected.items():
        cell = key[:-1]
        previous = timing_cell_identities.setdefault(cell, identity)
        if previous != identity:
            raise ValueError("timing methods disagree on frozen cell identity")
    if timing_cell_identities != parity_expected:
        raise ValueError("timing/parity frozen endpoint/data/tool identities differ")
    return timing, timing_rows, parity_rows


def _formal_parity(key, exact, legacy, field, expected_kind):
    row = exact.get(key) or legacy.get((key[1], key[2], key[3]))
    kind_field = "br_kind" if field == "br_multiset_equal" else "nc_kind"
    if (
        row is None
        or field not in row
        or row.get("protocol") != pcm.PROTOCOL
        or row.get(kind_field) != expected_kind
    ):
        return None
    value = parse_bool(row.get(field))
    return value


def _csv_br_fallback(b_row, r_row):
    if not b_row or not r_row:
        return None
    if b_row.get("status") != "ok" or r_row.get("status") != "ok":
        return None
    b_meta, r_meta = pcm.unpack_note(b_row), pcm.unpack_note(r_row)
    expected = "csv-binding-multiset-v1"
    if b_meta.get("answer_kind") != expected or r_meta.get("answer_kind") != expected:
        return None
    b_fp, r_fp = b_meta.get("answer_fingerprint"), r_meta.get("answer_fingerprint")
    if not b_fp or not r_fp:
        return None
    return (
        b_fp == r_fp
        and str(b_meta.get("answer_key_count")) == str(r_meta.get("answer_key_count"))
    )


def parity_states(key, methods, exact, legacy):
    """Return (B/R state, N/C state, source); never compare answer counts."""
    br = _formal_parity(
        key, exact, legacy, "br_multiset_equal", "term-aware-binding-multiset-v1"
    )
    nc = _formal_parity(
        key, exact, legacy, "nc_keys_equal", "term-aware-candidate-set-v1"
    )
    source = "term-aware-json" if (br is not None or nc is not None) else "none"
    if br is None:
        br = _csv_br_fallback(methods.get("B"), methods.get("R"))
        if br is not None:
            source = "normalized-csv"
    render = lambda value: "ok" if value is True else ("mismatch" if value is False else "unverified")
    return render(br), render(nc), source


def summarize(src, parity, out, stream=None, require_completion=True):
    if stream is None:
        stream = sys.stdout
    completion_error = None
    if require_completion:
        try:
            with contextlib.ExitStack() as locks:
                for locked_path in sorted(
                    {os.path.abspath(src), os.path.abspath(parity)}
                ):
                    locks.enter_context(pcm.invocation_file_lock(locked_path))
                timing_proof, timing_rows, parity_rows = _validate_completion_pair(
                    src, parity
                )
            current_batch_id = timing_proof["batch_id"]
            cells = _timing_cells(
                src, current_batch_id=current_batch_id, rows=timing_rows
            )
            exact, legacy = _parity_maps(
                parity,
                rows=parity_rows,
                current_commit=timing_proof["commit"],
                current_batch_id=current_batch_id,
            )
        except (OSError, TypeError, ValueError, RuntimeError) as ex:
            completion_error = str(ex)
            cells, exact, legacy = {}, {}, {}
    else:
        cells = _timing_cells(src)
        exact, legacy = _parity_maps(parity)
    output, failures, unverified = [], [], []
    print(
        f"{'engine':8} {'scale':5} {'cls':3} {'tmpl':5} {'ans(B/R,N/C)':16} "
        f"{'B':>8} {'R':>8} {'N':>8} {'C':>8} {'R-B':>8} {'N-R':>8} {'C-R':>8} "
        f"{'BR':>10} {'NC':>10}",
        file=stream,
    )
    for key in sorted(cells):
        engine, scale, cls, template, instance = key
        methods = cells[key]

        def median(method):
            row = methods.get(method)
            return num(row.get("median_ms")) if row and row.get("status") == "ok" else None

        def answers(method):
            row = methods.get(method)
            return row.get("answers") if row and row.get("status") == "ok" else None

        b, r, n, c = (median(method) for method in "BRNC")
        ba, ra, na, ca = (answers(method) for method in "BRNC")
        c_row = methods.get("C")
        c_meta = pcm.unpack_note(c_row) if c_row else {}
        c_parse = num(c_row.get("c_parse_median_ms")) if c_row else None
        if c_parse is None and c_meta.get("c_parse_samples"):
            c_parse = round(statistics.median(map(float, c_meta["c_parse_samples"])), 1)
        construct_total = num(c_row.get("construct_total_ms")) if c_row else None
        if construct_total is None and c_meta.get("construct_total_samples"):
            construct_total = round(
                statistics.median(map(float, c_meta["construct_total_samples"])), 1
            )
        delta = lambda left, right: (
            round(left - right, 1) if left is not None and right is not None else None
        )
        statuses = {
            method: methods[method]["status"] if method in methods else "-"
            for method in "BRNC"
        }
        br_parity, nc_parity, parity_source = parity_states(
            key, methods, exact, legacy
        )
        if br_parity == "mismatch":
            failures.append(f"{key}: B/R term-aware binding multiset differs")
        if nc_parity == "mismatch":
            failures.append(f"{key}: N/C term-aware candidate key set differs")
        if br_parity == "unverified" or nc_parity == "unverified":
            unverified.append(key)

        def render_timing(value, status):
            return f"{value:.0f}" if value is not None else status

        answer_text = f"{ba}/{ra},{na}/{ca}"
        print(
            f"{engine:8} {scale:5} {cls:3} {template:5} {answer_text:16} "
            f"{render_timing(b, statuses['B']):>8} {render_timing(r, statuses['R']):>8} "
            f"{render_timing(n, statuses['N']):>8} {render_timing(c, statuses['C']):>8} "
            f"{str(delta(r, b)):>8} {str(delta(n, r)):>8} {str(delta(c, r)):>8} "
            f"{br_parity:>10} {nc_parity:>10}",
            file=stream,
        )
        output.append(
            {
                "engine": engine,
                "scale": scale,
                "class": cls,
                "template": template,
                "instance": instance,
                "b_ms": b,
                "r_ms": r,
                "n_ms": n,
                "c_ms": c,
                "c_parse_ms": c_parse,
                "construct_total_ms": construct_total,
                "reif_delta": delta(r, b),
                "npcs_delta": delta(n, r),
                "circ_delta": delta(c, r),
                "b_ans": ba,
                "r_ans": ra,
                "n_ans": na,
                "c_ans": ca,
                "status_B": statuses["B"],
                "status_R": statuses["R"],
                "status_N": statuses["N"],
                "status_C": statuses["C"],
                "br_parity": br_parity,
                "nc_parity": nc_parity,
                "parity_source": parity_source,
            }
        )

    columns = [
        "engine", "scale", "class", "template", "instance", "b_ms", "r_ms", "n_ms",
        "c_ms", "c_parse_ms", "construct_total_ms", "reif_delta", "npcs_delta", "circ_delta", "b_ans", "r_ans", "n_ans",
        "c_ans", "status_B", "status_R", "status_N", "status_C", "br_parity",
        "nc_parity", "parity_source",
    ]
    out = pcm._prepare_artifact_path(out, exploratory=not require_completion)
    rendered = io.StringIO(newline="")
    writer = csv.DictWriter(rendered, fieldnames=columns)
    writer.writeheader()
    writer.writerows(output)
    with pcm.invocation_file_lock(out):
        pcm._atomic_publish_bytes(
            out,
            rendered.getvalue().encode("utf-8"),
            "R9 decomposition summary",
        )
    print(f"\nwrote {out} ({len(output)} cells)", file=stream)
    if failures:
        print(f"\n!!! {len(failures)} ANSWER-PARITY MISMATCHES:", file=stream)
        for failure in failures[:20]:
            print("   " + failure, file=stream)
    else:
        print("answer-parity: no verified mismatch", file=stream)
    if unverified:
        print(
            f"parity-unverified: {len(unverified)} cells (run verify_brnc_parity.py)",
            file=stream,
        )
    if not output:
        print(
            "timing-unverified: no current-protocol rows with required evidence",
            file=stream,
        )
    if completion_error:
        print(f"completion-unverified: {completion_error}", file=stream)
        unverified.append(("completion", completion_error))
    return output, failures, unverified


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--src", default=DEFAULT_SRC)
    parser.add_argument("--parity", default=DEFAULT_PARITY)
    parser.add_argument("--out")
    parser.add_argument(
        "--allow-mismatch",
        action="store_true",
        help="allow verified mismatches; does not allow unverified/missing evidence",
    )
    parser.add_argument(
        "--allow-unverified",
        action="store_true",
        help="allow unverified/missing timing or parity evidence",
    )
    args = parser.parse_args(argv)
    args.out = args.out or os.path.join(
        os.path.dirname(os.path.abspath(args.src)), "brnc_decomposition.csv"
    )
    try:
        output, failures, unverified = summarize(
            args.src,
            args.parity,
            args.out,
            require_completion=not args.allow_unverified,
        )
    except (OSError, ValueError, RuntimeError) as ex:
        print(f"summary failed: {ex}", file=sys.stderr)
        return 1
    mismatch_failed = bool(failures) and not args.allow_mismatch
    unverified_failed = (not output or bool(unverified)) and not args.allow_unverified
    return 1 if mismatch_failed or unverified_failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
