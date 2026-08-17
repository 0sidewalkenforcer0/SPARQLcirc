"""E9 - TPC-H SPJ/MINUS skeletons on our shared circuit, per-row (`naryrel`) provenance, timed
CLEANLY against a GraphDB repo whose TPC-H direct mapping is pre-loaded (only the CONSTRUCT eval +
gate materialisation is measured, not data load). Mirrors e8_wikidata.py but with the `naryrel`
scheme. Records build_ms, deriv, gates, edges, minus, answers, share per skeleton, and a WMC == PWE
spot-check on any circuit small enough to enumerate.

Env: WATDIV_REPO (the pre-loaded GraphDB repo, e.g. tpch001); E9_SK (skeleton dir, default tpch/
skeletons); E9_RUNS (default 5); E9_OUT (csv). Run from reference/ with the engine jar + data loaded.
"""
import csv, glob, os
from e6_minus import build, parse_circuit, counts, emit_construct_plan, wmc_pwe_check, t_string

RUNS = int(os.environ.get("E9_RUNS", "5"))

def plan_naryrel(qtext):
    """CONSTRUCT plan for a skeleton under the naryrel scheme (in-memory on empty data)."""
    out = emit_construct_plan(qtext, "naryrel", allow_unsupported=True)
    return out, bool(out)

def run_skeleton(name, qtext, do_wmc):
    try:
        constructs, ok = plan_naryrel(qtext)
    except Exception as ex:
        return dict(query=name, status=f"err:plan:{type(ex).__name__}")
    if not ok:
        return dict(query=name, status="skip:unsupported")
    try:
        _, triples, capped = build(constructs)
    except Exception as ex:
        return dict(query=name, status=f"err:{type(ex).__name__}")
    if capped:
        return dict(query=name, status="too-large")
    circ, ans, typ = parse_circuit(triples)
    times, plus, minus, edges, answers = counts(circ, ans, typ)
    samples = []
    for k in range(RUNS + 1):
        ms, _, _ = build(constructs)
        if k: samples.append(ms)
    build_ms = sum(samples) / len(samples)
    gates = times + plus + minus
    diff = wmc_pwe_check(circ, ans) if do_wmc else None
    return dict(query=name, status="ok", plan=len(constructs), build_ms=round(build_ms),
                deriv=times, gates=gates, minus=minus, edges=edges, answers=answers,
                share=round(t_string(circ) / (gates + edges), 3) if gates + edges else 0,
                wmc_pwe=(f"{diff:.1e}" if diff is not None else ""))

def main():
    repo = os.environ.get("WATDIV_REPO", "tpch001")
    skdir = os.environ.get("E9_SK", os.path.join(os.path.dirname(__file__), "tpch", "skeletons"))
    out = os.environ.get("E9_OUT", "tpch/e9_%s.csv" % repo)
    files = sorted(glob.glob(f"{skdir}/*.rq"))
    print(f"E9 - TPC-H skeletons on repo '{repo}' (naryrel scheme, {RUNS}-run avg), {len(files)} skeletons\n")
    rows = []
    for i, f in enumerate(files):
        name = os.path.splitext(os.path.basename(f))[0]
        r = run_skeleton(name, open(f).read(), do_wmc=(i < 2))     # WMC-check first 2 (small enough)
        rows.append(r)
        if r["status"] == "ok":
            print(f"  [{name:8}] build={r['build_ms']:>6}ms deriv={r['deriv']:>6} gates={r['gates']:>6} "
                  f"minus={r['minus']:>6} ans={r['answers']:>6} share={r['share']}x "
                  f"{('WMC==PWE Δ='+r['wmc_pwe']) if r['wmc_pwe'] else ''}")
        else:
            print(f"  [{name:8}] {r['status']}")
    cols = ["query", "status", "plan", "build_ms", "deriv", "gates", "minus", "edges", "answers", "share", "wmc_pwe"]
    with open(out, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore", restval=""); w.writeheader(); w.writerows(rows)
    ok = sum(1 for r in rows if r["status"] == "ok")
    print(f"\nwrote {out}  ({ok}/{len(rows)} skeletons ran)")

if __name__ == "__main__":
    main()
