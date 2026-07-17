"""D2 (wikidata) — FLAT vs FACTORED deployed construction for the WIKIDATA reification scheme.

Companion to rdfstar_factored.py. WIKIDATA_REIF_EQUIV.md records FLAT construction time for a bound 2-hop
subclass chain on the two loaded repos (Standard on wdpaths, Wikidata on wdstatements). Per the D2 "keep
both" decision we ADD the FACTORED counterpart on the SAME bound query, so the paper can compare both.

Query: a 2-hop P279 (subclass-of) chain with ONLY the endpoint projected — `?x wdt:P279 ?y . ?y wdt:P279 ?z`
SELECT ?z — so ?y is a pure INTERIOR join variable that factored eliminates (the star/chain distinction that
made L-path blow up: this is the wikidata 2-hop chain analogue). ?x is source-bound (bind_source, Standard
finder on wdpaths — entity IRIs are reification-independent, reused for both schemes; CircuitRun re-reifies).

Isolation, timing, and byte-identity checks are inherited from rdfstar_factored (CIRCUIT_SKIP_LOAD=1,
before/after repo /size assertion, `# construction_ms`). Env: WKF_RUNS (default 3), WKF_XMX (10g),
WKF_CELL_TIMEOUT (300s), WKF_OUT (wikidata/wikidata_factored_vs_flat.csv).
"""
import os, csv, tempfile, subprocess
import e3_run, circuit_io
import rdfstar_factored as R

R.XMX = os.environ.get("WKF_XMX", "10g")
R.CELL_TIMEOUT = int(os.environ.get("WKF_CELL_TIMEOUT", "300"))
R.WARMUP = 0
R.RUNS = int(os.environ.get("WKF_RUNS", "3"))
OUT = os.environ.get("WKF_OUT", "wikidata/wikidata_factored_vs_flat.csv")

# (scheme) -> (repo id, CircuitRun scheme name). Data is pre-loaded (SKIP_LOAD), so the data-file arg is a
# harmless stub used only for RDF-format detection, which we do not exercise here.
DUMMY = os.path.join(os.path.dirname(__file__), "data", "drug.reified.ttl")
CFG = {
    "Standard": ("wdpaths",       "Standard"),   # 60.5M, urn:st: Standard-reified P279/P131
    "Wikidata": ("wdstatements",  "Wikidata"),   # 40.3M, p:/ps: statement-reified
}
QUERY = ("PREFIX wdt: <http://www.wikidata.org/prop/direct/>\n"
         "SELECT ?z WHERE { ?x wdt:P279 ?y . ?y wdt:P279 ?z }\n")
COLS = ["scheme", "repo", "mode", "source", "build_ms", "times", "plus", "minus",
        "gates", "answers", "circuit_triples", "circuit_sha256", "repo_isolated"]


def main():
    print(f"D2 wikidata — flat vs factored (2-hop P279 chain, endpoint-only), {R.RUNS}-run, -Xmx{R.XMX}\n", flush=True)
    # Bind ?x on the Standard repo (bind_source's finder is Standard-reification-only), reuse for both schemes.
    e3_run.EP = R.ep("wdpaths")
    bq, iri = e3_run.bind_source(QUERY)
    if not iri:
        print("could not bind a 2-hop P279 source on wdpaths — abort"); return
    src = iri.rsplit("/", 1)[-1]
    print(f"bound source ?x = {iri}\n{bq}\n", flush=True)
    qf = tempfile.NamedTemporaryFile("w", suffix=".rq", delete=False); qf.write(bq); qf.close()
    rows = []
    fh = open(OUT, "w", newline=""); w = csv.DictWriter(fh, fieldnames=COLS, extrasaction="ignore", restval="")
    w.writeheader(); fh.flush()
    for scheme, (repo, scheme_name) in CFG.items():
        before = R.repo_size(repo)
        for mode in ("flat", "factored"):
            try:
                st = R.measure(mode, scheme_name, DUMMY, qf.name, repo)
            except (MemoryError, subprocess.TimeoutExpired, RuntimeError) as e:
                note = "too-large:OOM" if isinstance(e, MemoryError) else \
                       "too-large:timeout" if isinstance(e, subprocess.TimeoutExpired) else "error"
                R.cleanup_workspace(repo)   # a SIGKILL'd factored run may have leaked its urn:sc:* workspace
                after = R.repo_size(repo)
                row = dict(scheme=scheme, repo=repo, mode=mode, source=src, circuit_sha256=note,
                           repo_isolated=(after == before))
                rows.append(row); w.writerow(row); fh.flush()
                print(f"  [{scheme:9}/{mode:8}] {note}: {str(e)[:70]} "
                      f"{'ISO-OK' if after == before else '!!REPO-CHANGED'}", flush=True)
                continue
            after = R.repo_size(repo)
            isolated = (after == before)
            row = dict(scheme=scheme, repo=repo, mode=mode, source=src, repo_isolated=isolated, **st)
            rows.append(row); w.writerow(row); fh.flush()
            print(f"  [{scheme:9}/{mode:8}] build={st['build_ms']:>6}ms ⊗={st['times']:>3} ⊕={st['plus']:>5} "
                  f"⊖={st['minus']:>2} gates={st['gates']:>5} ans={st['answers']:>3} "
                  f"sha={st['circuit_sha256'][:8]} {'ISO-OK' if isolated else '!!REPO-CHANGED'}", flush=True)
    fh.close()
    print(f"\nwrote {OUT} ({len(rows)} rows)", flush=True)


if __name__ == "__main__":
    main()
