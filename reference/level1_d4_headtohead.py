"""Level-1 ProvSQL head-to-head: per-answer forced CNF -> d4v2 on both systems.

This is deliberately SEPARATE from G3/E11: G3 measures SPARQLcirc's normal shared-ROBDD pipeline and E11
measures the shared-compilation advantage.  Here both systems use per-answer/per-group granularity so the
external compiler is controlled:

* ours: materialise the shared RDF circuit once, then for every answer export its Tseitin CNF, invoke d4v2
  ONCE to dump a d-DNNF, and WMC that dump locally;
* ProvSQL: first materialise each answer's provenance token, then force the registered CNF-only compiler
  ``probability_evaluate(token, 'compilation', 'd4v2-cnf')``.

Protocol: one warm-up + five timed runs by default. Any failed answer, missing row, probability mismatch or
partial query aborts with a non-zero exit.  ``LEVEL1_MAX_ANSWERS=N`` is an explicit smoke-test sample and is
labelled as such; never cite a sampled run as the headline.

  D4=/path/d4v2 D4V2=1 PGHOST=... PGPORT=... python3 level1_d4_headtohead.py
"""
import csv
import hashlib
import os
import re
import shutil
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass

import compile_portfolio
import g3_pqe_latency as g3


RUNS = int(os.environ.get("LEVEL1_RUNS", "5"))
WARMUP = 1
MAX_ANSWERS = int(os.environ.get("LEVEL1_MAX_ANSWERS", "0"))
PROVSQL_COMPILER = os.environ.get("LEVEL1_PROVSQL_COMPILER", "d4v2-cnf")
PLEAF = 0.5
GDB = "http://localhost:7200/repositories"
TOL = 1e-6
# This is a watchdog for one PostgreSQL batch that may launch thousands of per-answer compiler calls; it
# is not the per-compilation cutoff used by R9.4. Our directly launched d4 calls use the canonical 120 s
# limit through compile_portfolio.d4_compile_once().
PROVSQL_BATCH_TIMEOUT_S = 3600


@dataclass(frozen=True)
class Spec:
    name: str
    repo: str
    schema: str
    qfile: str
    kind: str
    expected_answers: int


SPECS = [
    Spec("tpch-Q3-sf001", "tpch001", "g2a", "tpch/skeletons/Q3.rq", "q3", 14908),
    Spec("Qrecon-sf001", "tpch001", "g2a", "tpch/skeletons/Qrecon.rq", "qrecon", 247),
    Spec("Qrecon-sf01", "tpch01", "g2a1", "tpch/skeletons/Qrecon.rq", "qrecon", 2086),
]


def _parts(answer_key):
    if answer_key == "A": return {}
    if not answer_key.startswith("A|"):
        raise ValueError(f"unexpected answer key: {answer_key!r}")
    return dict(item.split("=", 1) for item in answer_key[2:].split("|"))


def _iri_tail(canon):
    if not canon.startswith("i\x1f"):
        raise ValueError(f"expected an IRI answer term, got {canon!r}")
    return canon[2:].rsplit("/", 1)[-1]


def answer_id(kind, answer_key):
    p = _parts(answer_key)
    if kind == "qrecon":
        return _iri_tail(p["cust"])
    order = _iri_tail(p["order"])
    line_order, line = _iri_tail(p["line"]).rsplit("-", 1)
    if line_order != order:
        raise ValueError(f"Q3 binding disagrees on order id: {answer_key!r}")
    return f"{order}:{line}"


def _selected_roots(kind, ans):
    roots = {answer_id(kind, key): node for node, key in ans.items()}
    if len(roots) != len(ans):
        raise RuntimeError("answer-id collision while preparing Level-1 roots")
    items = sorted(roots.items())
    return dict(items[:MAX_ANSWERS] if MAX_ANSWERS else items)


def ours_once(spec, progress=True):
    circ, ans, construct_ms = g3.construct_bgp(
        f"{GDB}/{spec.repo}", "naryrel", open(spec.qfile).read())
    t = time.perf_counter()
    roots = _selected_roots(spec.kind, ans)
    construct_ms += (time.perf_counter() - t) * 1000          # answer-id recovery/selection is system work
    P = {payload: PLEAF for op, payload in circ.values() if op == "leaf"}
    probs, encode_ms, compile_ms, wmc_ms, nnf_nodes = {}, 0.0, 0.0, 0.0, 0
    for i, (key, root) in enumerate(roots.items(), 1):
        r = compile_portfolio.d4_compile_once(circ, root, P)
        probs[key] = r["probability"]
        encode_ms += r["encode_ms"]; compile_ms += r["compile_ms"]; wmc_ms += r["wmc_ms"]
        nnf_nodes += r["ddnnf_nodes"]
        if progress and (i == len(roots) or i % 250 == 0):
            print(f"    ours d4: {i}/{len(roots)} answers", flush=True)
    compile_stage_ms = encode_ms + compile_ms + wmc_ms
    return dict(answers=len(roots), construct_ms=construct_ms, encode_ms=encode_ms, compile_ms=compile_ms,
                wmc_ms=wmc_ms, compile_stage_ms=compile_stage_ms, total_ms=construct_ms + compile_stage_ms,
                nnf_nodes=nnf_nodes, per=probs)


def _psql():
    candidate = os.path.join(os.environ.get("CONDA_PREFIX", ""), "bin", "psql")
    return candidate if os.path.isfile(candidate) else (shutil.which("psql") or candidate)


def _provsql_source(spec):
    if spec.kind == "q3":
        return ("SELECT (o.o_orderkey::text || ':' || l.l_linenumber::text) key, "
                "provenance() prov, NULL::integer k "
                f"FROM {spec.schema}.customer c, {spec.schema}.orders o, {spec.schema}.lineitem l "
                "WHERE o.o_custkey=c.c_custkey AND l.l_orderkey=o.o_orderkey "
                "AND c.c_mktsegment='BUILDING'")
    return ("SELECT c.c_custkey::text key, provenance() prov, count(*)::integer k "
            f"FROM {spec.schema}.customer c, {spec.schema}.orders o "
            "WHERE o.o_custkey=c.c_custkey AND c.c_mktsegment='BUILDING' "
            "GROUP BY c.c_custkey")


def provsql_sql(spec, emit_probabilities):
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", PROVSQL_COMPILER):
        raise ValueError(f"unsafe LEVEL1_PROVSQL_COMPILER: {PROVSQL_COMPILER!r}")
    d4 = os.path.realpath(os.environ["D4"]).replace("'", "''")
    selection = f" ORDER BY key LIMIT {MAX_ANSWERS}" if MAX_ANSWERS else ""
    emit = ("SELECT 'P', key, p, COALESCE(k::text,'') FROM l1prob ORDER BY key;\n"
            if emit_probabilities else "")
    return ("\\set ON_ERROR_STOP on\n"
            f"SET search_path={spec.schema},public,provsql;\n"
            "DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM provsql.tools WHERE name='" + PROVSQL_COMPILER +
            "' AND available AND executable='" + d4 + "' AND input_formats=ARRAY['dimacs-cnf']::text[] "
            "AND COALESCE(argtpl_circuit,'')='') THEN RAISE EXCEPTION 'missing/invalid CNF-only compiler " +
            PROVSQL_COMPILER + "'; END IF; END $$;\n"
            "\\timing on\n"
            "CREATE TEMP TABLE l1prov AS SELECT * FROM (" + _provsql_source(spec) +
            f") src{selection};\n"
            "\\timing off\n\\timing on\n"
            "CREATE TEMP TABLE l1prob AS SELECT key, k, "
            "probability_evaluate(prov, 'compilation', '" + PROVSQL_COMPILER + "') p FROM l1prov;\n"
            "\\timing off\n\\pset format unaligned\n\\pset tuples_only on\n\\pset fieldsep '|'\n"
            "SELECT 'S', count(*), min(p), max(p), sum(p) FROM l1prob;\n" + emit)


def provsql_once(spec, emit_probabilities=False):
    proc = subprocess.run([_psql(), "-d", "provsqltest"], input=provsql_sql(spec, emit_probabilities),
                          capture_output=True, text=True, env=dict(os.environ),
                          timeout=PROVSQL_BATCH_TIMEOUT_S)
    if proc.returncode != 0:
        raise RuntimeError(f"ProvSQL Level-1 failed (rc={proc.returncode}): {proc.stderr[-1500:]}")
    times = [float(x) for x in re.findall(r"Time:\s+([\d.]+)\s+ms", proc.stdout)]
    if len(times) != 2:
        raise RuntimeError(f"expected exactly construct + compile timings, got {times}: {proc.stdout[-1000:]}")
    summary, per = parse_provsql_rows(proc.stdout)
    if summary is None:
        raise RuntimeError("ProvSQL Level-1 produced no S summary row")
    if emit_probabilities and len(per) != summary["answers"]:
        raise RuntimeError(f"ProvSQL probability map incomplete: {len(per)} != {summary['answers']}")
    return dict(**summary, construct_ms=times[0], encode_ms=None, compile_ms=None, wmc_ms=None,
                compile_stage_ms=times[1],
                total_ms=sum(times), per=per)


def parse_provsql_rows(stdout):
    """Parse tagged Level-1 summary/probability rows (kept separate for offline regression)."""
    summary = None; per = {}
    for line in stdout.splitlines():
        p = line.strip().split("|")
        if len(p) == 5 and p[0] == "S":
            summary = dict(answers=int(p[1]), p_min=float(p[2]), p_max=float(p[3]), p_sum=float(p[4]))
        elif len(p) == 4 and p[0] == "P":
            if p[1] in per: raise RuntimeError(f"duplicate ProvSQL key: {p[1]}")
            per[p[1]] = (float(p[2]), int(p[3]) if p[3] else None)
    return summary, per


def parity(spec, ours, provsql):
    op, pp = ours["per"], {k: v[0] for k, v in provsql["per"].items()}
    keys_match = set(op) == set(pp)
    maxerr = max((abs(op[k] - pp[k]) for k in set(op) & set(pp)), default=None)
    closed = None
    if keys_match and op:
        if spec.kind == "q3":
            closed = max(abs(v - 0.125) for v in op.values())
        else:
            closed = max(abs(op[k] - 0.5 * (1.0 - 0.5 ** provsql["per"][k][1])) for k in op)
    agree = keys_match and maxerr is not None and maxerr < TOL and closed is not None and closed < TOL
    return dict(keys_match=keys_match, max_abs_error=maxerr, closed_form_max_error=closed, agree=agree)


def _stats(xs):
    return dict(median=statistics.median(xs), min=min(xs), max=max(xs), mean=statistics.mean(xs),
                sd=statistics.stdev(xs) if len(xs) > 1 else 0.0)


def _write(path, rows):
    if not rows: return
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""): h.update(block)
    return h.hexdigest()


def main():
    if RUNS < 1:
        raise RuntimeError("LEVEL1_RUNS must be >= 1")
    if os.environ.get("D4V2") != "1":
        raise RuntimeError("Level-1 requires D4V2=1")
    if not os.environ.get("D4") or not os.path.isabs(os.environ["D4"]):
        raise RuntimeError("D4 must be the absolute path of the pinned d4v2 binary")
    d4_sha256 = _sha256(os.environ["D4"])
    selected = os.environ.get("LEVEL1_ONLY")
    specs = [s for s in SPECS if not selected or s.name in selected.split(",")]
    if not specs:
        raise RuntimeError(f"LEVEL1_ONLY selected no known query: {selected}")
    scope = f"sample-{MAX_ANSWERS}" if MAX_ANSWERS else "full"
    print(f"Level-1 per-answer d4v2: {WARMUP} warm-up + {RUNS} timed; scope={scope}; "
          f"sha256={d4_sha256}")
    raw, summary_rows = [], []
    for spec in specs:
        expected = min(spec.expected_answers, MAX_ANSWERS) if MAX_ANSWERS else spec.expected_answers
        first_o = first_p = None
        print(f"\n[{spec.name}] expected answers={expected}")
        for run in range(WARMUP + RUNS):
            print(f"  run {run + 1}/{WARMUP + RUNS} ({'warm-up' if run < WARMUP else 'timed'})")
            o = ours_once(spec); p = provsql_once(spec, emit_probabilities=(run == 0))
            if o["answers"] != expected or p["answers"] != expected:
                raise RuntimeError(f"{spec.name}: incomplete answers ours={o['answers']} provsql={p['answers']} expected={expected}")
            if run == 0:
                first_o, first_p = o, p
            else:
                idx = run - WARMUP + 1
                for system, x in (("ours", o), ("provsql", p)):
                    raw.append(dict(query=spec.name, scope=scope, d4_sha256=d4_sha256, run=idx, system=system,
                                    answers=x["answers"], construct_ms=round(x["construct_ms"], 3),
                                    encode_ms=(round(x["encode_ms"], 3) if x["encode_ms"] is not None else None),
                                    d4_compile_ms=(round(x["compile_ms"], 3) if x["compile_ms"] is not None else None),
                                    wmc_ms=(round(x["wmc_ms"], 3) if x["wmc_ms"] is not None else None),
                                    compile_stage_ms=round(x["compile_stage_ms"], 3),
                                    total_ms=round(x["total_ms"], 3),
                                    ddnnf_nodes=(x.get("nnf_nodes") if system == "ours" else None)))
                _write(os.environ.get("LEVEL1_RAW_OUT", "level1_d4_runs.csv"), raw)  # checkpoint long runs
        par = parity(spec, first_o, first_p)
        if not par["agree"]:
            raise RuntimeError(f"{spec.name}: Level-1 probability parity failed: {par}")
        for system in ("ours", "provsql"):
            rows = [r for r in raw if r["query"] == spec.name and r["system"] == system]
            cs, ks, ts = (_stats([r[f] for r in rows])
                          for f in ("construct_ms", "compile_stage_ms", "total_ms"))
            summary_rows.append(dict(query=spec.name, scope=scope, d4_sha256=d4_sha256,
                                     system=system, answers=expected, runs=RUNS,
                                     construct_median_ms=round(cs["median"], 3),
                                     compile_stage_median_ms=round(ks["median"], 3),
                                     total_median_ms=round(ts["median"], 3),
                                     total_min_ms=round(ts["min"], 3), total_max_ms=round(ts["max"], 3),
                                     total_sd_ms=round(ts["sd"], 3), keys_match=par["keys_match"],
                                     max_abs_error=par["max_abs_error"],
                                     closed_form_max_error=par["closed_form_max_error"], agree=par["agree"]))
        _write(os.environ.get("LEVEL1_OUT", "level1_d4_headtohead.csv"), summary_rows)
        print(f"  parity OK: max_abs_error={par['max_abs_error']} closed_form={par['closed_form_max_error']}")
    print("\nALL LEVEL-1 RUNS COMPLETE" + (" (SMOKE SAMPLE — NOT HEADLINE)" if MAX_ANSWERS else ""))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as ex:
        print(f"\nFATAL: {type(ex).__name__}: {ex}", file=sys.stderr)
        sys.exit(1)
