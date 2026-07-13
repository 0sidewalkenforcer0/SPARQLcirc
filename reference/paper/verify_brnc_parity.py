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
import csv
import json
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
import circuit_io
import paper_construction_matrix as pcm

CAP = 500_000


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


def execute_c_plan(endpoint, qtext, timeout=pcm.TIMEOUT):
    """Rewrite and drain all C steps under one shared parity-pass deadline."""
    deadline = time.monotonic() + timeout
    plan = pcm.c_construct_plan(qtext, timeout=pcm._remaining(deadline))
    triples = set()
    for body in plan:
        _, _, _, text = pcm.post_timed(
            endpoint,
            body,
            "application/n-triples",
            keep=True,
            timeout=pcm._remaining(deadline),
        )
        triples.update(line for line in text.splitlines() if line.strip().endswith(" ."))
        pcm._remaining(deadline)
    _, answers, bindings = circuit_io.parse(triples)
    keys = {circuit_io.answer_key(bindings.get(gate, {})) for gate in answers}
    pcm._remaining(deadline)
    return keys, len(answers)


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
    mismatch = any(
        row.get(field) is False
        for row in rows
        for field in ("br_multiset_equal", "nc_keys_equal")
    )
    unverified = any(
        row.get(field) is None
        for row in rows
        for field in ("br_multiset_equal", "nc_keys_equal")
    )
    return 1 if mismatch or (unverified and not allow_unverified) else 0


COLS = [
    "protocol",
    "engine",
    "scale",
    "class",
    "template",
    "instance",
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
    config = pcm.ENGINES.get(engine)
    if not config or scale not in config:
        raise SystemExit(f"unregistered engine/scale: {engine}/{scale}")
    endpoints = config[scale]
    classes = set(filter(None, args.classes.split(",")))
    with open(os.path.join(HERE, "workload_manifest.csv"), newline="") as fh:
        manifest = [
            row
            for row in csv.DictReader(fh)
            if row["scale"] == scale and row["class"] in classes
        ]

    output = []
    print(
        f"{'cls/tmpl':10} {'B':>8} {'R':>8} {'B==R bindings':16} "
        f"{'Nkeys':>8} {'Ckeys':>8} {'N==C keys':11} note"
    )
    for row in manifest:
        cls, template, instance = row["class"], row["template"], row["instance"]
        with open(os.path.join(REF, row["query_file"])) as query_fh:
            qtext = query_fh.read()
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
                endpoints["reified"], qtext, timeout=args.timeout
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
                "engine": engine,
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
            }
        )

    return output


def _parity_key(row):
    names = ("engine", "scale", "class", "template", "instance")
    key = tuple(row.get(name, "") for name in names)
    return key if all(key) else None


def merge_parity_rows(path, new_rows):
    """Atomically merge/replace exact cells while retaining other combinations."""
    merged = {}
    if os.path.exists(path):
        with open(path, newline="") as fh:
            for row in csv.DictReader(fh):
                key = _parity_key(row)
                if key is not None:
                    merged[key] = row
    for row in new_rows:
        key = _parity_key(row)
        if key is None:
            raise ValueError(f"parity row has incomplete key: {row!r}")
        merged[key] = row
    directory = os.path.dirname(os.path.abspath(path)) or "."
    tmp = tempfile.NamedTemporaryFile(
        "w", newline="", prefix=".brnc-parity-", suffix=".tmp", dir=directory, delete=False
    )
    try:
        writer = csv.DictWriter(tmp, fieldnames=COLS, extrasaction="ignore", restval="")
        writer.writeheader()
        writer.writerows(merged[key] for key in sorted(merged))
        tmp.flush()
        os.fsync(tmp.fileno())
        tmp.close()
        os.replace(tmp.name, path)
    finally:
        if not tmp.closed:
            tmp.close()
        try:
            os.unlink(tmp.name)
        except FileNotFoundError:
            pass
    return [merged[key] for key in sorted(merged)]


def _requested(single, multiple, default):
    raw = multiple if multiple else (single or default)
    return [value.strip() for value in raw.split(",") if value.strip()]


def run(args):
    engines = _requested(getattr(args, "engine", None), getattr(args, "engines", None), "graphdb")
    scales = _requested(getattr(args, "scale", None), getattr(args, "scales", None), "10M")
    output = []
    for engine in engines:
        for scale in scales:
            print(f"\n[{engine} {scale}] term-aware parity")
            output.extend(run_one(args, engine, scale))
    merged = merge_parity_rows(args.out, output)
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
    return gate_exit_code(output, allow_unverified=args.allow_unverified)


def main(argv=None):
    parser = argparse.ArgumentParser(
        epilog=(
            "Endpoint pairs use the same independent-instance defaults/overrides as the timing "
            "harness: PCM_<ENGINE>_<SCALE>_{BASE,REIFIED}_ENDPOINT."
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
    parser.add_argument("--timeout", type=float, default=pcm.TIMEOUT)
    parser.add_argument("--out", default=os.path.join(HERE, "brnc_parity.csv"))
    parser.add_argument(
        "--allow-unverified",
        action="store_true",
        help="exploratory mode: allow error/capped cells, but never a verified mismatch",
    )
    args = parser.parse_args(argv)
    if args.cap <= 0 or args.timeout <= 0:
        parser.error("--cap and --timeout must be positive")
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
