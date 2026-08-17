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
import csv, glob, os, tempfile
from e6_minus import build, parse_circuit, counts, emit_construct_plan, wmc_pwe_check, t_string

RUNS = int(os.environ.get("E8_RUNS", "3"))
if RUNS < 1:
    raise ValueError("E8_RUNS must be a positive integer")

def plan_wikidata(qtext):
    """Emit the CONSTRUCT plan for a query under the Wikidata scheme (in-memory on empty data).
    Returns (constructs, ok). ok=False if the rewriter rejects it (e.g. variable predicate)."""
    out = emit_construct_plan(qtext, "Wikidata", allow_unsupported=True)
    return out, bool(out)

def run_query(cat, name, qtext, do_wmc):
    try:
        constructs, ok = plan_wikidata(qtext)
    except Exception as ex:
        return dict(category=cat, query=name, status=f"err:plan:{type(ex).__name__}")
    if not ok:
        return dict(category=cat, query=name, status="skip:var-predicate")
    try:
        _warmup_ms, triples, capped = build(constructs)   # untimed warmup also supplies the circuit
    except Exception as ex:
        return dict(category=cat, query=name, status=f"err:{type(ex).__name__}")
    if capped:
        return dict(category=cat, query=name, status="too-large")
    circ, ans, typ = parse_circuit(triples)
    times, plus, minus, edges, answers = counts(circ, ans, typ)
    samples = []
    for _ in range(RUNS):
        ms, _, measured_capped = build(constructs)
        if measured_capped:
            return dict(category=cat, query=name, status="too-large")
        samples.append(ms)
    build_ms = sum(samples) / len(samples)
    gates = times + plus + minus
    diff = wmc_pwe_check(circ, ans) if do_wmc else None
    return dict(category=cat, query=name, status="ok", plan=len(constructs), build_ms=round(build_ms),
                deriv=times, gates=gates, edges=edges, answers=answers,
                share=round(t_string(circ) / (gates + edges), 3) if gates + edges else 0,
                wmc_pwe=(f"{diff:.1e}" if diff is not None else ""))

def main():
    repo = os.environ.get("WATDIV_REPO", "wikidata")
    qdir = os.environ.get("E8_QDIR")
    out = os.environ.get("E8_OUT", "watdiv/e8_wikidata.csv")
    if not qdir or not os.path.isdir(qdir):
        raise ValueError("E8_QDIR must name an existing NPCS Wikidata query directory")
    categories = ("single", "multiple", "optional")
    query_files = {cat: sorted(glob.glob(f"{qdir}/{cat}/*.sparql")) for cat in categories}
    empty = [cat for cat, files in query_files.items() if not files]
    if empty:
        raise ValueError(f"E8_QDIR has no .sparql queries for: {', '.join(empty)}")
    print(f"E8 - NPCS Wikidata queries on repo '{repo}' (Wikidata scheme, {RUNS}-run avg)\n")
    cols = ["category", "query", "status", "plan", "build_ms", "deriv", "gates", "edges", "answers", "share", "wmc_pwe"]
    out_dir = os.path.dirname(os.path.abspath(out))
    fd, temporary = tempfile.mkstemp(prefix=os.path.basename(out) + ".", suffix=".tmp", dir=out_dir)
    rows = []
    try:
        with os.fdopen(fd, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore", restval="")
            w.writeheader(); fh.flush()
            for cat in categories:
                files = query_files[cat]
                okc = 0
                for i, f in enumerate(files):
                    name = f"{cat}/{os.path.splitext(os.path.basename(f))[0]}"
                    with open(f, encoding="utf-8") as query_file:
                        query_text = query_file.read()
                    r = run_query(cat, name, query_text, do_wmc=(i < 3))
                    rows.append(r); w.writerow(r); fh.flush()
                    if r["status"] == "ok":
                        okc += 1
                        print(f"  [{name:14}] build={r['build_ms']:>6}ms deriv={r['deriv']:>4} gates={r['gates']:>5} "
                              f"ans={r['answers']:>4} share={r['share']}x "
                              f"{('WMC==PWE Δ='+r['wmc_pwe']) if r['wmc_pwe'] else ''}")
                    else:
                        print(f"  [{name:14}] {r['status']}")
                print(f"  --- {cat}: {okc}/{len(files)} ok ---\n")
        os.replace(temporary, out)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise
    ok = sum(1 for r in rows if r["status"] == "ok")
    print(f"wrote {out}  ({ok}/{len(rows)} queries ran)")

if __name__ == "__main__":
    main()
