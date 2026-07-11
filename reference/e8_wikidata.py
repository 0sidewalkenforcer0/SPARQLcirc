"""E8 - NPCS Wikidata queries on our shared circuit, using the native Wikidata statement
reification (matches NPCS's Wikidatareal). Runs NPCS's own Basic single/multiple/optional query
set (real wdt: predicates) through our gamma rewriter with the `Wikidata` scheme: the emitted
CONSTRUCT(s) are posted to GraphDB and the circuit deduped/counted (BGP = 1 CONSTRUCT; OPTIONAL =
multi-CONSTRUCT). Records build_ms, deriv, gates, edges, answers, share per category, and a
WMC == PWE spot-check on small circuits. Queries with a VARIABLE predicate are skipped (statement
reification needs a constant wdt: predicate) and reported.

Env: WATDIV_REPO (the GraphDB repo, e.g. wikidata); E8_QDIR (NPCS Basic/wikidata dir);
E8_RUNS (default 3); E8_OUT (csv). Run from reference/ with the engine jar + the data loaded.
Reuses the multi-CONSTRUCT plan/build/count/WMC machinery from e6_minus.
"""
import os, re, sys, glob, csv, subprocess, tempfile, time
import e3_run
from e6_minus import build, parse_circuit, counts, wmc_pwe_check, JAR, EMPTY, post

RUNS = int(os.environ.get("E8_RUNS", "3"))

def plan_wikidata(qtext):
    """Emit the CONSTRUCT plan for a query under the Wikidata scheme (in-memory on empty data).
    Returns (constructs, ok). ok=False if the rewriter rejects it (e.g. variable predicate)."""
    qf = tempfile.NamedTemporaryFile("w", suffix=".rq", delete=False); qf.write(qtext); qf.close()
    r = subprocess.run(["java", "-cp", JAR, "npcs.circuit.CircuitRun", "Wikidata", EMPTY.name, qf.name],
                       capture_output=True, text=True)
    if "Exception" in r.stderr or "Unsupported" in r.stderr:
        return [], False
    chunks = re.split(r"# --- step \d+ ---", r.stderr)
    out = []
    for ch in chunks[1:]:
        ch = ch.split("# ---- ")[0].split("# circuit triples")[0].strip()
        if ch.startswith("PREFIX") or ch.startswith("CONSTRUCT"):
            out.append(ch)
    return out, bool(out)

def run_query(cat, name, qtext, do_wmc):
    constructs, ok = plan_wikidata(qtext)
    if not ok:
        return dict(category=cat, query=name, status="skip:var-predicate")
    try:
        _, triples, capped = build(constructs)
    except Exception as ex:
        return dict(category=cat, query=name, status=f"err:{type(ex).__name__}")
    if capped:
        return dict(category=cat, query=name, status="too-large")
    circ, ans, typ = parse_circuit(triples)
    times, plus, minus, edges, answers = counts(circ, ans, typ)
    samples = []
    for k in range(RUNS + 1):
        ms, _, _ = build(constructs)
        if k: samples.append(ms)
    build_ms = sum(samples) / len(samples)
    gates = times + plus + minus
    diff = wmc_pwe_check(circ, ans) if do_wmc else None
    return dict(category=cat, query=name, status="ok", plan=len(constructs), build_ms=round(build_ms),
                deriv=times, gates=gates, edges=edges, answers=answers,
                share=round(times * 3 / (gates + edges), 3) if gates + edges else 0,
                wmc_pwe=(f"{diff:.1e}" if diff is not None else ""))

def main():
    repo = os.environ.get("WATDIV_REPO", "wikidata")
    qdir = os.environ.get("E8_QDIR")
    out = os.environ.get("E8_OUT", "watdiv/e8_wikidata.csv")
    print(f"E8 - NPCS Wikidata queries on repo '{repo}' (Wikidata scheme, {RUNS}-run avg)\n")
    rows = []
    for cat in ("single", "multiple", "optional"):
        files = sorted(glob.glob(f"{qdir}/{cat}/*.sparql"))
        okc = 0
        for i, f in enumerate(files):
            name = f"{cat}/{os.path.splitext(os.path.basename(f))[0]}"
            r = run_query(cat, name, open(f).read(), do_wmc=(i < 3))   # WMC-check first 3 per cat
            rows.append(r)
            if r["status"] == "ok":
                okc += 1
                print(f"  [{name:14}] build={r['build_ms']:>6}ms deriv={r['deriv']:>4} gates={r['gates']:>5} "
                      f"ans={r['answers']:>4} share={r['share']}x "
                      f"{('WMC==PWE Δ='+r['wmc_pwe']) if r['wmc_pwe'] else ''}")
            else:
                print(f"  [{name:14}] {r['status']}")
        print(f"  --- {cat}: {okc}/{len(files)} ok ---\n")
    cols = ["category", "query", "status", "plan", "build_ms", "deriv", "gates", "edges", "answers", "share", "wmc_pwe"]
    with open(out, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore", restval=""); w.writeheader(); w.writerows(rows)
    ok = sum(1 for r in rows if r["status"] == "ok")
    print(f"wrote {out}  ({ok}/{len(rows)} queries ran)")

if __name__ == "__main__":
    main()
