"""E3 - construction scaling on a deployed, unmodified engine (GraphDB), memory-safe.

For each WatDiv query we AUTO-BIND the source (subject of the first triple) to a real
entity found by a fast LIMIT-1 lookup, so the circuit stays SELECTIVE (matches how the
NPCS/SPARQLprov baselines run the official templates, and keeps the client bounded --
the unbound query at 10M builds a multi-million-gate circuit that OOM'd the old
full-parse harness). We then run the circuit CONSTRUCT on GraphDB and STREAM the
N-Triples response line-by-line, counting gate types in O(1) memory; we time the build
vs the plain NPCS SELECT and report c=build/plain and structural compactness. Entities
are found per-repo, so the same harness works at 10M and 100M (different entity sets).

A build exceeding E3_TIMEOUT (default 300s) is recorded as `timeout`. Set E3_BOUND=0 to
run the raw unbound queries (heavy; streaming keeps it memory-safe but expect timeouts).

Env: WATDIV_REPO (default watdiv); WATDIV_QDIR (dir of *.rq; unset -> S/L/F/M shapes);
E3_OUT (csv path, default watdiv/e3_results.csv). Run from reference/ with the env active.
"""
import os, re, sys, time, glob, socket, tempfile, csv
import urllib.request as U, urllib.parse as UP, urllib.error
from watdiv_run import get_construct, get_npcs, _arity
try:
    from tqdm import tqdm
except ImportError:
    def tqdm(x, **k): return x
    tqdm.write = staticmethod(lambda *a, **k: print(*a))

GDB = "http://localhost:7200"
REPO = os.environ.get("WATDIV_REPO", "watdiv")
EP = f"{GDB}/repositories/{REPO}"
TIMEOUT = int(os.environ.get("E3_TIMEOUT", "300"))
BOUND = os.environ.get("E3_BOUND", "1") != "0"
RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
MARK = [(b"urn:circuit:Times", "Times"), (b"urn:circuit:Plus", "Plus"),
        (b"urn:circuit:answer", "answer"), (b"urn:circuit:feeds", "feeds"), (b"urn:circuit:in>", "in")]

def _prefixes(q):
    return dict(re.findall(r"PREFIX\s+(\w+):\s*<([^>]+)>", q))

def _expand(term, pfx):
    if term.startswith("?") or term.startswith("<"):
        return term
    if ":" in term:
        p, loc = term.split(":", 1)
        if p in pfx:
            return f"<{pfx[p]}{loc}>"
    return term

def _positive_triples(q, pfx):
    """Parse the WHERE BGP (part before any MINUS/OPTIONAL) into expanded (s,p,o)."""
    body = q[q.find("{") + 1:]
    body = re.split(r"\bMINUS\b|\bOPTIONAL\b", body, 1)[0]
    body = body[:body.rfind("}")] if "}" in body else body
    trips = []
    for stmt in body.split("."):
        parts = stmt.split()
        if len(parts) == 3:
            trips.append(tuple(_expand(t, pfx) for t in parts))
    return trips

def bind_source(q):
    """Bind the first triple's subject var to a real entity (found by a LIMIT-1 lookup over
    the reified positive patterns) so the query becomes SELECTIVE. The bound var is dropped
    from the projection (it is now a constant) and substituted ONLY in the WHERE clause; if
    that empties the projection (e.g. a star whose only answer var is the hub), the first
    remaining object variable is projected instead. Returns (bound_query, iri) or (q, None)."""
    q = re.sub(r"#[^\n]*", "", q)                          # strip SPARQL comments (may contain '{' or 'MINUS')
    if "+" in q or "*" in q or "/" in q.split("{")[-1]:
        return q, None                                     # property path -> not this flow
    pfx = _prefixes(q)
    trips = _positive_triples(q, pfx)
    if not trips or not trips[0][0].startswith("?"):
        return q, None
    src = trips[0][0]
    where = " ".join(f"?f{i} <{RDF}subject> {s} ; <{RDF}predicate> {p} ; <{RDF}object> {o} ."
                     for i, (s, p, o) in enumerate(trips))
    finder = f"SELECT {src} WHERE {{ {where} }} LIMIT 1"
    try:
        data = UP.urlencode({"query": finder}).encode()
        req = U.Request(EP, data=data, method="POST")
        req.add_header("Content-Type", "application/x-www-form-urlencoded"); req.add_header("Accept", "text/csv")
        rows = U.urlopen(req, timeout=60).read().decode().splitlines()
    except Exception:
        return q, None
    if len(rows) < 2 or not rows[1].strip():
        return q, None                                     # no entity satisfies the full pattern
    iri = rows[1].strip().strip('"')
    msel = re.search(r"SELECT\s+(.+?)\s+WHERE", q, re.S)
    proj = [v for v in msel.group(1).split() if v.startswith("?") and v != src]
    if not proj:
        for s, p, o in trips:
            if o.startswith("?") and o != src:
                proj = [o]; break
    wbody = re.sub(re.escape(src) + r"\b", f"<{iri}>", q[q.find("{"):])   # substitute in WHERE only
    header = "".join(f"PREFIX {k}: <{v}>\n" for k, v in pfx.items())
    return f"{header}SELECT {' '.join(proj) or '*'} WHERE {wbody}", iri

def post_stream(body, ctype, accept, count):
    req = U.Request(EP, data=body.encode(), method="POST")
    req.add_header("Content-Type", ctype); req.add_header("Accept", accept)
    t = time.time(); cnt = {k: 0 for _, k in MARK}; cnt["lines"] = 0
    for raw in U.urlopen(req, timeout=TIMEOUT):
        cnt["lines"] += 1
        if count:
            for needle, key in MARK:
                if needle in raw:
                    cnt[key] += 1; break
    return (time.time() - t) * 1000, cnt

def run_query(name, qtext, arity):
    src = None
    if BOUND:
        qtext, src = bind_source(qtext)
    tf = tempfile.NamedTemporaryFile("w", suffix=".rq", delete=False)
    tf.write(qtext); tf.close()
    tag = f"{name}" + (f" [bound {src.rsplit('/', 1)[-1]}]" if src else " [unbound]")
    tqdm.write(f"  -> {tag}: building circuit (<= {TIMEOUT}s) ...")
    cons = get_construct(tf.name)
    if not cons.strip():
        tqdm.write(f"  -> {tag}: ERROR empty CONSTRUCT (rewriter rejected/failed on this query)")
        return dict(query=name, bound=bool(src), status="err:empty-construct")
    try:
        runs = int(os.environ.get("E3_RUNS", "1"))          # warmup + (runs) timed; avg build_ms
        samples = []
        for k in range(runs + (1 if runs > 1 else 0)):
            build_ms, c = post_stream(cons, "application/sparql-query", "application/n-triples", True)
            if not (runs > 1 and k == 0):                    # drop warmup
                samples.append(build_ms)
        build_ms = sum(samples) / len(samples)
    except (socket.timeout, TimeoutError):
        tqdm.write(f"  -> {tag}: BUILD TIMEOUT (> {TIMEOUT}s)")
        return dict(query=name, bound=bool(src), status="timeout", build_ms=TIMEOUT * 1000)
    except urllib.error.URLError as ue:
        if isinstance(ue.reason, (socket.timeout, TimeoutError)):
            tqdm.write(f"  -> {tag}: BUILD TIMEOUT (> {TIMEOUT}s)")
            return dict(query=name, bound=bool(src), status="timeout", build_ms=TIMEOUT * 1000)
        body = ue.read()[:200].decode("utf-8", "replace") if hasattr(ue, "read") else str(ue.reason)
        tqdm.write(f"  -> {tag}: HTTP ERROR {body}")
        return dict(query=name, bound=bool(src), status="err:http")
    except Exception as ex:
        tqdm.write(f"  -> {tag}: ERROR {type(ex).__name__}: {ex}")
        return dict(query=name, bound=bool(src), status=f"err:{type(ex).__name__}")
    times, plus, edges, answers = c["Times"], c["Plus"], c["in"] + c["feeds"], c["answer"]
    gates = times + plus
    T_str, T_circ = times * arity, gates + edges
    try:
        plain_ms, _ = post_stream(get_npcs(tf.name), "application/sparql-query", "text/csv", False)
    except Exception:
        plain_ms = float("nan")
    co = build_ms / plain_ms if plain_ms == plain_ms and plain_ms else float("nan")
    tqdm.write(f"  -> {tag}: {build_ms/1000:.1f}s  ({times} deriv, {gates} gates, {answers} ans)")
    return dict(query=name, bound=bool(src), status="ok", build_ms=round(build_ms),
                plain_ms=round(plain_ms) if plain_ms == plain_ms else None,
                c_overhead=round(co, 2) if co == co else None, deriv=times, gates=gates,
                edges=edges, answers=answers, T_string=T_str, T_circ=T_circ,
                share=round(T_str / T_circ, 3) if T_circ else 0)

def main():
    qdir = os.environ.get("WATDIV_QDIR")
    if qdir:
        QS = [(os.path.splitext(os.path.basename(f))[0], open(f).read(), _arity(open(f).read()))
              for f in sorted(glob.glob(f"{qdir}/*.rq") + glob.glob(f"{qdir}/*.sparql"))]
    else:
        # monotone single-CONSTRUCT shapes (the E3 scaling set). MINUS is a multi-CONSTRUCT
        # plan (E6) and property paths need the iterative CircuitRun flow -- both out of the
        # one-shot POST scope handled here.
        QS = [(n, open(f"watdiv/{n}.rq").read(), a) for n, a in
              [("S-star", 3), ("F-snow", 4), ("L-path", 3)]]
    out = os.environ.get("E3_OUT", "watdiv/e3_results.csv")
    print(f"E3 construction on GraphDB repo '{REPO}'  (bound={BOUND}, timeout {TIMEOUT}s)\n")
    rows = [run_query(n, q, a) for n, q, a in tqdm(QS, desc=f"E3 {REPO}", unit="q")]
    cols = ["query", "bound", "status", "build_ms", "plain_ms", "c_overhead", "deriv",
            "gates", "edges", "answers", "T_string", "T_circ", "share"]
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore"); w.writeheader(); w.writerows(rows)
    print(f"\nwrote {out}")

if __name__ == "__main__":
    main()
