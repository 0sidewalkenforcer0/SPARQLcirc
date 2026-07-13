"""R9.2 — SPARQLprov-style B / R / N_clean / C construction-timing decomposition (HIGHEST priority).

Four ALTERNATIVE executions of each frozen manifest query, timed under one protocol on one engine:

  B       base SELECT over the UNREIFIED graph                        (endpoint: base repo)
  R       reification-only SELECT (reify_query; algebra-preserving,   (endpoint: reified repo)
          no provenance) -- the missing control that isolates R-B
  N_clean NPCS-compatible provenance SELECT (App `Standard query`,    (endpoint: reified repo)
          the clean-room NpcsRewriter -> per-answer ⊕/⊗ strings)
  C       SPARQLcirc CONSTRUCT plan (App CircuitRun -> shared circuit) (endpoint: reified repo)

Decompositions (deltas derived AFTER aggregation; never clamp a negative delta to zero):
  NPCS:       B + (R-B) + (N-R) = N            SPARQLcirc: B + (R-B) + (C-R) = C
  full PQE:   construct_total(C) + compile + WMC          (compile/WMC added once, elsewhere: G3/G4)

Timing boundaries (per the task): rewrite_ms (N/C query generation) is a DIAGNOSTIC, not engine time.
`*_engine_ms` = POST immediately-before-send .. final response byte. For C additionally `c_parse_ms` =
parse/dedup circuit + recover answer bindings; construct_total_ms = c_engine_ms + c_parse_ms.

Protocol: 1-2 warm-ups + 5 timed runs, 300 s per-cell timeout (experiment_timeouts.QUERY_TIMEOUT_S),
report median/min/max/mean/sd + raw samples. Statuses are results: ok | unsupported | timeout | oom |
answer-mismatch | not-run. Checkpointed: one CSV row per (engine,scale,class,template,instance,method)
cell; a killed run resumes without repeating a completed 5-run cell.

  GraphDB only, 10M:   python3 paper_construction_matrix.py --engines graphdb --scales 10M
  full:                python3 paper_construction_matrix.py --engines graphdb,oxigraph,qlever,millenniumdb --scales 10M,100M
"""
import os, sys, csv, json, time, argparse, subprocess, tempfile, statistics, socket, re
import urllib.request as U, urllib.error
sys.setrecursionlimit(1_000_000)

HERE = os.path.dirname(os.path.abspath(__file__))
REF = os.path.dirname(HERE)
sys.path.insert(0, REF)
import reify_query
from experiment_timeouts import QUERY_TIMEOUT_S

JAR = os.path.join(REF, "..", "engine", "target", "npcs-rewrite.jar")
TIMEOUT = QUERY_TIMEOUT_S
WARMUPS = int(os.environ.get("PCM_WARMUPS", "1"))
RUNS = int(os.environ.get("PCM_RUNS", "5"))
RS = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
COMMIT = subprocess.run(["git", "-C", REF, "rev-parse", "--short", "HEAD"],
                        capture_output=True, text=True).stdout.strip() or "?"

# --- engine registry: (base endpoint, reified endpoint) per scale. Add engines/scales here. ---
GDB = "http://localhost:7200/repositories"
ENGINES = {
    "graphdb": {
        "version": "GraphDB (RDF4J)",
        "10M":  {"base": f"{GDB}/watdivbase",     "reified": f"{GDB}/watdiv"},
        "100M": {"base": f"{GDB}/watdiv100mbase",  "reified": f"{GDB}/watdiv100m"},
    },
    # oxigraph/qlever/millenniumdb registered when their base+reified repos are confirmed loaded.
}

# ---------- HTTP POST with POST->final-byte timing + byte/row counting ----------
def post_timed(endpoint, body, accept, keep=False, timeout=TIMEOUT):
    """POST a SPARQL query; drain the whole response. Returns (engine_ms, n_lines, n_bytes, text_or_None).
    engine_ms is measured from immediately-before-send through the final response byte (full drain)."""
    req = U.Request(endpoint, data=body.encode(), method="POST")
    req.add_header("Content-Type", "application/sparql-query")
    req.add_header("Accept", accept)
    buf = [] if keep else None
    t = time.time(); nb = 0; nl = 0
    resp = U.urlopen(req, timeout=timeout)                    # per-call socket timeout (see cumulative deadline)
    for raw in resp:                                          # iterate to final byte == full response read
        nb += len(raw); nl += 1
        if keep:
            buf.append(raw)
    ms = (time.time() - t) * 1000
    text = b"".join(buf).decode("utf-8", "replace") if keep else None
    return ms, nl, nb, text

def csv_rows(nl):
    """CSV answer count = data lines minus the header line (>=0)."""
    return max(0, nl - 1)

# ---------- method query generators ----------
def q_base(qtext):
    return qtext

def q_reify(qtext):
    return reify_query.reify(qtext)

def q_npcs(qtext):
    """Clean-room NpcsRewriter provenance SELECT (App `Standard query`)."""
    r = subprocess.run(["java", "-jar", JAR, "Standard", "query", qtext],
                       capture_output=True, text=True)
    if r.returncode != 0 or not r.stdout.strip():
        raise RuntimeError(f"NpcsRewriter failed rc={r.returncode}: {r.stderr[-300:]}")
    return r.stdout

def c_construct_plan(qtext):
    """CircuitRewriter CONSTRUCT plan (App CircuitRun on empty data -> stderr plan)."""
    tf = tempfile.NamedTemporaryFile("w", suffix=".rq", delete=False); tf.write(qtext); tf.close()
    try:
        r = subprocess.run(["java", "-cp", JAR, "npcs.circuit.CircuitRun", "Standard",
                            os.path.join(REF, "bench_engine", "tiny.ttl"), tf.name],
                           capture_output=True, text=True)
    finally:
        os.unlink(tf.name)
    if r.returncode != 0:
        raise RuntimeError(f"CircuitRun rewrite failed rc={r.returncode}: {r.stderr[-300:]}")
    out = []
    for ch in re.split(r"# --- step \d+ ---", r.stderr)[1:]:
        ch = ch.split("# ---- ")[0].split("# circuit triples")[0].strip()
        if ch.startswith(("PREFIX", "CONSTRUCT")):
            out.append(ch)
    # some builds emit one block without step markers -> fall back to the PREFIX c: .. plan grep
    if not out:
        grab = False; cur = []
        for l in r.stderr.splitlines():
            if l.startswith("PREFIX c:"): grab = True
            if l.startswith("# circuit triples"): grab = False
            if grab: cur.append(l)
        if cur:
            out = ["\n".join(cur)]
    if not out:
        raise RuntimeError("empty CONSTRUCT plan")
    return out

def parse_circuit(nt_lines):
    """Count gates/edges and answer gates from the circuit N-Triples (like watdiv_run.parse_circuit)."""
    typ, feeds, tin, ans = {}, {}, {}, {}
    for line in nt_lines:
        if not line.endswith(" ."): continue
        s, p, o = line[:-2].split(None, 2)
        s = s.strip("<>"); p = p.strip("<>"); o = o.strip()
        if p == RS + "type": typ[s] = o.strip("<>")
        elif p == "urn:circuit:feeds": feeds.setdefault(o.strip("<>"), set()).add(s)
        elif p == "urn:circuit:in": tin.setdefault(s, set()).add(o.strip("<>"))
        elif p == "urn:circuit:answer": ans[s] = o
    gates = sum(1 for t in typ.values() if t.endswith(("Times", "Plus", "Minus")))
    times = sum(1 for t in typ.values() if t.endswith("Times"))     # "derivations" (R9.3)
    edges = sum(len(v) for v in tin.values()) + sum(len(v) for v in feeds.values())
    return gates, edges, len(ans), times

# ---------- one timed cell (warmups + RUNS) ----------
def stat(xs):
    return dict(median=statistics.median(xs), min=min(xs), max=max(xs), mean=statistics.mean(xs),
                sd=(statistics.stdev(xs) if len(xs) > 1 else 0.0))

def time_method(method, qtext, base_ep, reified_ep):
    """Return dict(status, answers, samples[ms], response_bytes, c_parse_samples, gates, edges, note)."""
    ep = base_ep if method == "B" else reified_ep
    accept = "application/n-triples" if method == "C" else "text/csv"
    # generate the (rewritten) query once; rewrite time is diagnostic, not part of engine timing.
    # A rewrite/App failure is a per-cell result, never a whole-run crash.
    rw = time.time()
    try:
        if method == "B":   bodies = [q_base(qtext)]
        elif method == "R": bodies = [q_reify(qtext)]
        elif method == "N": bodies = [q_npcs(qtext)]
        elif method == "C": bodies = c_construct_plan(qtext)
        else: raise ValueError(method)
    except Exception as ex:
        return dict(status=f"err:rewrite:{type(ex).__name__}", answers=None, samples=[],
                    response_bytes=None, c_parse=[], gates=None, edges=None, note=str(ex)[:120])
    rewrite_ms = round((time.time() - rw) * 1000, 1)          # N/C query generation (diagnostic, not engine time)

    samples, cparse, ans, rbytes, gates, edges, deriv, ntok = [], [], None, None, None, None, None, None
    for i in range(WARMUPS + RUNS):
        try:
            if method == "C":
                eng_ms = 0.0; uniq = set()                     # dedup circuit triples ACROSS all steps (D4)
                deadline = time.time() + TIMEOUT               # ONE 300s budget for the WHOLE plan (D3)
                for b in bodies:                              # POST each CONSTRUCT step within the shared budget
                    remain = deadline - time.time()
                    if remain <= 0:
                        raise socket.timeout("cumulative CONSTRUCT-plan budget exhausted")
                    ms, nl, bts, text = post_timed(ep, b, accept, keep=True, timeout=remain)
                    eng_ms += ms
                    uniq.update(l for l in text.splitlines() if l.endswith(" ."))
                tri = uniq
                nb = sum(len(l.encode()) + 1 for l in uniq)   # deduplicated N-Triples byte count (D4)
                pt = time.time(); g, e, a, deriv = parse_circuit(tri); parse_ms = (time.time() - pt) * 1000
            elif method == "N" and i == WARMUPS:              # keep body ONCE to count ⊗ token occurrences
                eng_ms, nl, nb, text = post_timed(ep, bodies[0], accept, keep=True)
                a = csv_rows(nl); parse_ms = 0.0; g = e = None; ntok = text.count("⊗")
            else:
                eng_ms, nl, nb, _ = post_timed(ep, bodies[0], accept, keep=False)
                a = csv_rows(nl); parse_ms = 0.0; g = e = None
        except (socket.timeout, TimeoutError):
            return dict(status="timeout", answers=None, samples=[], response_bytes=None,
                        c_parse=[], gates=None, edges=None, note=f">{TIMEOUT}s")
        except urllib.error.URLError as ue:
            reason = getattr(ue, "reason", ue)
            if isinstance(reason, (socket.timeout, TimeoutError)):
                return dict(status="timeout", answers=None, samples=[], response_bytes=None,
                            c_parse=[], gates=None, edges=None, note=f">{TIMEOUT}s")
            body = ue.read()[:200].decode("utf-8", "replace") if hasattr(ue, "read") else str(reason)
            st = "oom" if "memory" in body.lower() or "heap" in body.lower() else "unsupported"
            return dict(status=st, answers=None, samples=[], response_bytes=None,
                        c_parse=[], gates=None, edges=None, note=body.strip()[:120])
        except Exception as ex:
            return dict(status=f"err:{type(ex).__name__}", answers=None, samples=[], response_bytes=None,
                        c_parse=[], gates=None, edges=None, note=str(ex)[:120])
        if i >= WARMUPS:
            samples.append(round(eng_ms, 2)); cparse.append(round(parse_ms, 2))
        ans, rbytes, gates, edges = a, nb, g, e
    return dict(status="ok", answers=ans, samples=samples, response_bytes=rbytes,
                c_parse=cparse, gates=gates, edges=edges, derivations=deriv, ntok=ntok,
                rewrite_ms=rewrite_ms, note="")


def load_manifest():
    with open(os.path.join(HERE, "workload_manifest.csv")) as fh:
        return list(csv.DictReader(fh))

CELL_KEY = ("engine", "scale", "class", "template", "instance", "method")

def load_done(out):
    if not os.path.exists(out):
        return set()
    with open(out) as fh:
        return {tuple(r[k] for k in CELL_KEY) for r in csv.DictReader(fh)}

COLS = ["commit", "engine", "engine_version", "scale", "class", "template", "instance", "query_sha256",
        "method", "implementation", "status", "answers", "median_ms", "min_ms", "max_ms", "mean_ms",
        "sd_ms", "warmups", "runs", "timeout_s", "response_bytes", "c_parse_median_ms", "gates", "edges",
        "derivations", "npcs_token_occurrences", "rewrite_ms", "samples_json", "notes"]

IMPL = {"B": "base-select", "R": "reification-only", "N": "N_clean (NPCS reimplementation)",
        "C": "SPARQLcirc CircuitRewriter"}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--engines", default="graphdb")
    ap.add_argument("--scales", default="10M")
    ap.add_argument("--classes", default="L,S,F,C,O,M")
    ap.add_argument("--methods", default="B,R,N,C")
    ap.add_argument("--out", default=os.path.join(HERE, "construction_brnc.csv"))
    args = ap.parse_args()
    engines = args.engines.split(","); scales = args.scales.split(",")
    classes = set(args.classes.split(",")); methods = args.methods.split(",")

    manifest = [r for r in load_manifest() if r["class"] in classes]
    done = load_done(args.out)
    new = not os.path.exists(args.out)
    fh = open(args.out, "a", newline=""); w = csv.DictWriter(fh, fieldnames=COLS)
    if new: w.writeheader()

    for engine in engines:
        cfg = ENGINES.get(engine)
        if not cfg:                                            # unregistered engine -> explicit not-run rows,
            cfg = {"version": f"{engine} (not registered: base+reified endpoints unknown)"}   # never silent skip
            print(f"[{engine}] not registered -> emitting explicit not-run rows")
        for scale in scales:
            eps = cfg.get(scale)
            for row in [r for r in manifest if r["scale"] == scale]:
                cls, tmpl, inst = row["class"], row["template"], row["instance"]
                qtext = open(os.path.join(REF, row["query_file"])).read()
                # answer counts recorded per method; rigorous term-aware B==R / N==C parity is a separate
                # pass (verify_brnc_parity.py) so a legitimate bag-vs-set / OPTIONAL difference never aborts timing.
                for method in methods:
                    key = (engine, scale, cls, tmpl, inst, method)
                    if key in done:
                        continue
                    if eps is None:
                        rec = dict(status="not-run", answers=None, samples=[], response_bytes=None,
                                   c_parse=[], gates=None, edges=None, note=f"{scale} endpoints not registered")
                    else:
                        t0 = time.time()
                        rec = time_method(method, qtext, eps["base"], eps["reified"])
                        rec.setdefault("wall", time.time() - t0)
                    s = stat(rec["samples"]) if rec["samples"] else None
                    cp = statistics.median(rec["c_parse"]) if rec["c_parse"] else None
                    w.writerow(dict(
                        commit=COMMIT, engine=engine, engine_version=cfg["version"], scale=scale,
                        **{"class": cls}, template=tmpl, instance=inst, query_sha256=row["query_sha256"],
                        method=method, implementation=IMPL[method], status=rec["status"],
                        answers=rec["answers"],
                        median_ms=round(s["median"], 1) if s else None,
                        min_ms=round(s["min"], 1) if s else None, max_ms=round(s["max"], 1) if s else None,
                        mean_ms=round(s["mean"], 1) if s else None, sd_ms=round(s["sd"], 1) if s else None,
                        warmups=WARMUPS, runs=RUNS, timeout_s=TIMEOUT, response_bytes=rec["response_bytes"],
                        c_parse_median_ms=round(cp, 1) if cp is not None else None,
                        gates=rec["gates"], edges=rec["edges"], derivations=rec.get("derivations"),
                        npcs_token_occurrences=rec.get("ntok"), rewrite_ms=rec.get("rewrite_ms"),
                        samples_json=json.dumps(rec["samples"]), notes=rec["note"]))
                    fh.flush()
                    md = f"{s['median']:.0f}ms" if s else rec["status"]
                    print(f"  [{engine} {scale} {cls}/{tmpl} {method}] {rec['status']:14} "
                          f"ans={rec['answers']} {md}" + (f"  gates={rec['gates']}" if rec['gates'] else ""))
    fh.close()
    print(f"\nwrote/appended {args.out}")

if __name__ == "__main__":
    main()
