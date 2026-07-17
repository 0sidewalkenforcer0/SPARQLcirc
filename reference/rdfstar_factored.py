"""D2 — FLAT vs FACTORED deployed-engine (GraphDB) construction time for the WatDiv reification schemes.

The committed rdfstar_{10m,100m}.csv report FLAT construction only (flat = read-only, one product per
derivation — it was chosen so the numbers could be taken against the already-loaded production repos
with zero writes). Per the D2 decision we KEEP those flat numbers and ADD the FACTORED (production,
variable-elimination) counterpart, so the paper can analyse/compare both. This harness runs BOTH modes
on the SAME source-bound query per (scale, scheme, shape) — a paired comparison on an identical source.

Isolation ("scratch" intent without a 37 G reload): we run against the loaded deployed repos with
CIRCUIT_SKIP_LOAD=1 (data already bulk-loaded), so no data is written. Flat is read-only by construction.
Factored's feedback workspace (urn:sc:*) is auto-removed in CircuitRun's finally block; we ALSO assert the
repo triple-count is byte-identical before/after every shape, proving the production data is untouched.

Timing basis: CircuitRun prints `# construction_ms` = wall time of the on-engine plan execution ONLY
(excludes JVM startup and data load), measured identically for flat and factored. build_ms = mean of RUNS.

Binding: bind_source's finder uses Standard reification (rdf:subject/predicate/object), so we bind ONCE on
the Standard repo and reuse that base-pattern query for BOTH schemes (entity IRIs are reification-independent;
CircuitRun re-reifies internally per --scheme). Env: RSF_RUNS (default 3), RSF_SCALES (default "10M,100M"),
RSF_OUT (default watdiv/rdfstar_factored_vs_flat.csv), GDB (default http://localhost:7200).
"""
import os, sys, csv, time, hashlib, subprocess, tempfile
import urllib.request as U, urllib.parse as UP
import e3_run, circuit_io

JAR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "engine", "target", "npcs-rewrite.jar"))
GDB = os.environ.get("GDB", "http://localhost:7200")
DATA = "/mnt/nfs/home/ac145595/workspace/watdiv-data"
RUNS = int(os.environ.get("RSF_RUNS", "3"))
WARMUP = int(os.environ.get("RSF_WARMUP", "1"))   # 0 for the heavy matrix (some factored cells take minutes)
SCALES = os.environ.get("RSF_SCALES", "10M,100M").split(",")
OUT = os.environ.get("RSF_OUT", "watdiv/rdfstar_factored_vs_flat.csv")
SHAPES = os.environ.get("RSF_SHAPES", "S-star,L-path,F-snow,M-minus").split(",")
# Bounded client heap: factored construction accumulates the circuit + feedback workspace in memory, and a
# high-fan-out source at 100M can blow up. Cap it so a blowup OOMs FAST (recorded as too-large) instead of
# consuming the shared login node's RAM. Per-cell wall cap likewise bounds a slow cell.
XMX = os.environ.get("RSF_XMX", "10g")
CELL_TIMEOUT = int(os.environ.get("RSF_CELL_TIMEOUT", "300"))

# (scale, scheme) -> (repo id, data file for format detection; NOT loaded thanks to CIRCUIT_SKIP_LOAD)
CFG = {
    ("10M",  "Standard"):    ("watdiv",         f"{DATA}/watdiv.10M.reified.nt"),
    ("10M",  "SPARQL_Star"): ("watdivstar10m",  f"{DATA}/watdiv.10M.star.ttls"),
    ("100M", "Standard"):    ("watdiv100m",     f"{DATA}/watdiv.100M.reified.nt"),
    ("100M", "SPARQL_Star"): ("watdivstar100m", f"{DATA}/watdiv.100M.star.ttls"),
}
COLS = ["scale", "scheme", "shape", "mode", "source", "build_ms", "times", "plus", "minus",
        "gates", "answers", "circuit_triples", "circuit_sha256", "repo_isolated"]


def ep(repo):
    return f"{GDB}/repositories/{repo}"


def repo_size(repo):
    return int(U.urlopen(ep(repo) + "/size", timeout=60).read().decode().strip())


def cleanup_workspace(repo):
    """Self-heal after a factored timeout/OOM: a SIGKILL bypasses CircuitRun's finally, so the run can
    leave its urn:sc:* feedback workspace behind. Delete exactly those triples (urn:sc: is the factored
    message namespace, never base data) to restore isolation. Best-effort."""
    try:
        req = U.Request(ep(repo) + "/statements",
                        data=UP.urlencode({"update": 'DELETE { ?s ?p ?o } WHERE { ?s ?p ?o '
                                           'FILTER(STRSTARTS(STR(?p),"urn:sc:")) }'}).encode(),
                        method="POST")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        U.urlopen(req, timeout=300).read()
    except Exception as e:
        print(f"    (workspace cleanup on {repo} failed: {e})", flush=True)


def bind_on_standard(scale, qtext):
    """Bind the source on the Standard repo (the only reification bind_source's finder understands)."""
    e3_run.EP = ep(CFG[(scale, "Standard")][0])
    return e3_run.bind_source(qtext)


def circuit_sha(lines):
    """Canonical circuit digest: sha256 over the SORTED unique gate triples — order-independent, so
    Standard and RDF-star (which emit the same content-addressed gates) hash identically when equivalent."""
    uniq = sorted(set(l for l in lines if l.strip().endswith(" .")))
    return hashlib.sha256("\n".join(uniq).encode("utf-8")).hexdigest()


def run_circuit(mode, scheme, datafile, qfile, repo):
    """One CircuitRun on the deployed endpoint (data pre-loaded). Returns (construction_ms, circuit_lines)."""
    env = {**os.environ, "CIRCUIT_SKIP_LOAD": "1"}
    cmd = ["java", f"-Xmx{XMX}", "-cp", JAR, "npcs.circuit.CircuitRun", f"--construction={mode}",
           scheme, datafile, qfile, ep(repo)]
    r = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=CELL_TIMEOUT)
    ms = None
    for line in r.stderr.splitlines():
        if line.startswith("# construction_ms:"):
            ms = int(line.split(":", 1)[1].strip())
    if ms is None:
        tail = r.stderr[-1500:]
        if "OutOfMemory" in r.stderr or "GC overhead" in r.stderr:
            raise MemoryError(f"OOM (>-Xmx{XMX}) for {mode}/{scheme}/{repo}")
        raise RuntimeError(f"no construction_ms for {mode}/{scheme}/{repo} (rc={r.returncode}); stderr tail:\n{tail}")
    return ms, r.stdout.splitlines()


def measure(mode, scheme, datafile, qfile, repo):
    """Warmup + RUNS timed samples; return averaged build_ms + parsed circuit stats from the last run."""
    samples, last_lines = [], None
    for k in range(WARMUP + RUNS):
        ms, lines = run_circuit(mode, scheme, datafile, qfile, repo)
        if k >= WARMUP:
            samples.append(ms)
        last_lines = lines
    circ, answers, _ = circuit_io.parse(last_lines)
    times = sum(1 for op, _ in circ.values() if op == "times")
    plus = sum(1 for op, _ in circ.values() if op == "plus")
    minus = sum(1 for op, _ in circ.values() if op == "minus")
    return dict(build_ms=round(sum(samples) / len(samples)), times=times, plus=plus, minus=minus,
                gates=times + plus + minus, answers=len(answers),
                circuit_triples=len([l for l in last_lines if l.strip().endswith(" .")]),
                circuit_sha256=circuit_sha(last_lines))


def main():
    scales = [s for s in SCALES if s]
    print(f"D2 — flat vs factored deployed construction, scales={scales}, shapes={SHAPES}, {RUNS}-run avg, "
          f"-Xmx{XMX}, cell_timeout={CELL_TIMEOUT}s\n", flush=True)
    rows = []
    # Incremental write: flush every row so a kill/OOM never loses completed cells.
    fh = open(OUT, "w", newline="")
    w = csv.DictWriter(fh, fieldnames=COLS, extrasaction="ignore", restval=""); w.writeheader(); fh.flush()
    for scale in scales:
        for shape in SHAPES:
            qtext = open(f"watdiv/{shape}.rq").read()
            bq, iri = bind_on_standard(scale, qtext)
            if not iri:
                print(f"  [{scale}/{shape}] could not bind a source — skip", flush=True); continue
            src = iri.rsplit("/", 1)[-1]
            qf = tempfile.NamedTemporaryFile("w", suffix=".rq", delete=False); qf.write(bq); qf.close()
            for scheme in ("Standard", "SPARQL_Star"):
                repo, datafile = CFG[(scale, scheme)]
                before = repo_size(repo)
                for mode in ("flat", "factored"):
                    try:
                        st = measure(mode, scheme, datafile, qf.name, repo)
                    except (MemoryError, subprocess.TimeoutExpired, RuntimeError) as e:
                        note = "too-large:OOM" if isinstance(e, MemoryError) else \
                               "too-large:timeout" if isinstance(e, subprocess.TimeoutExpired) else "error"
                        cleanup_workspace(repo)   # a SIGKILL'd factored run may have leaked its urn:sc:* workspace
                        after = repo_size(repo)
                        row = dict(scale=scale, scheme=scheme, shape=shape, mode=mode, source=src,
                                   build_ms="", times="", plus="", minus="", gates="", answers="",
                                   circuit_triples="", circuit_sha256=note, repo_isolated=(after == before))
                        rows.append(row); w.writerow(row); fh.flush()
                        print(f"  [{scale}/{scheme:11}/{shape:7}/{mode:8}] {note}: {str(e)[:80]} "
                              f"{'ISO-OK' if after == before else '!!REPO-CHANGED'}", flush=True)
                        continue
                    after = repo_size(repo)
                    isolated = (after == before)
                    row = dict(scale=scale, scheme=scheme, shape=shape, mode=mode, source=src,
                               repo_isolated=isolated, **st)
                    rows.append(row); w.writerow(row); fh.flush()
                    print(f"  [{scale}/{scheme:11}/{shape:7}/{mode:8}] build={st['build_ms']:>6}ms "
                          f"⊗={st['times']:>4} ⊕={st['plus']:>4} ⊖={st['minus']:>3} gates={st['gates']:>5} "
                          f"ans={st['answers']:>3} sha={st['circuit_sha256'][:8]} "
                          f"{'ISO-OK' if isolated else '!!REPO-CHANGED %d->%d' % (before, after)}", flush=True)
    fh.close()
    print(f"\nwrote {OUT}  ({len(rows)} rows)", flush=True)
    # Cross-scheme reification-independence WITHIN each mode: Standard sha == SPARQL_Star sha.
    print("\nreification-independence (Standard sha == RDF-star sha, per scale/shape/mode):", flush=True)
    by = {}
    for r in rows:
        if r["circuit_sha256"] not in ("", "err") and not r["circuit_sha256"].startswith("too-large"):
            by.setdefault((r["scale"], r["shape"], r["mode"]), {})[r["scheme"]] = r["circuit_sha256"]
    for k, d in sorted(by.items()):
        if "Standard" in d and "SPARQL_Star" in d:
            ok = d["Standard"] == d["SPARQL_Star"]
            print(f"  {k[0]:>4} {k[1]:7} {k[2]:8}: {'IDENTICAL' if ok else 'DIFFER'} {d['Standard'][:8]}", flush=True)


if __name__ == "__main__":
    main()
