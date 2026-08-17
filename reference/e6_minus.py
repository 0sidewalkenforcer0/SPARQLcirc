"""Round 2A - non-monotone MINUS construction at scale on a deployed engine (GraphDB).

MINUS emits a MULTI-CONSTRUCT plan (⊕_P1, ⊕_P2, a compatible-join connect, ⊖) — which the single-POST
one-shot flow cannot send as one query (the concatenated PREFIXes are malformed). Here we extract the
plan from CircuitRun's stderr, POST each CONSTRUCT separately, DEDUP the returned triples (the source
is auto-bound so the circuit is selective/tiny), and count the ⊖ circuit. build_ms = summed POST time
(warmup + E6_RUNS avg), excluding JVM — comparable to e3_run. Also spot-checks circuit WMC == PWE.

Env: WATDIV_REPO (default watdiv), E6_RUNS (default 5), E6_OUT. Run from reference/ with the engine jar.
"""
import os, re, sys, time, subprocess, tempfile, csv, itertools, random
import urllib.request as U, urllib.parse as UP
import e3_run                                           # bind_source uses e3_run.EP (set by WATDIV_REPO)
from watdiv_run import get_npcs
import compile_bdd, circuit_io
from experiment_timeouts import QUERY_TIMEOUT_S

JAR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "engine", "target", "npcs-rewrite.jar"))
EMPTY = tempfile.NamedTemporaryFile("w", suffix=".ttl", delete=False); EMPTY.write(""); EMPTY.close()
RUNS = int(os.environ.get("E6_RUNS", "5"))
BOUND = os.environ.get("E6_BOUND", "1") != "0"       # bound=selective; 0=raw unbound query
MAXTRIP = int(os.environ.get("E6_MAXTRIP", "4000000"))  # safety cap on the collected circuit
POST_TIMEOUT = QUERY_TIMEOUT_S                         # canonical per-CONSTRUCT performance-cell limit
PLAN_TIMEOUT = QUERY_TIMEOUT_S                         # Java rewrite/construction-plan deadline
RS = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"; C = "urn:circuit:"

def emit_construct_plan(query_text, scheme, allow_unsupported=False):
    """Run CircuitRun with a hard deadline and return a complete flat CONSTRUCT plan."""
    qf = tempfile.NamedTemporaryFile("w", suffix=".rq", delete=False, encoding="utf-8")
    try:
        qf.write(query_text); qf.close()
        try:
            r = subprocess.run(
                ["java", "-cp", JAR, "npcs.circuit.CircuitRun", "--construction=flat",
                 scheme, EMPTY.name, qf.name],
                capture_output=True, text=True, check=True, timeout=PLAN_TIMEOUT,
            )
        except subprocess.CalledProcessError as ex:
            if allow_unsupported and "Unsupported" in (ex.stderr or ""):
                return []
            raise
    finally:
        try:
            os.unlink(qf.name)
        except FileNotFoundError:
            pass
    if allow_unsupported and "Unsupported" in r.stderr:
        return []
    if "Exception" in r.stderr:
        raise RuntimeError("CircuitRun reported an exception despite a zero exit status")
    chunks = re.split(r"# --- step \d+ ---", r.stderr)
    out = []
    for ch in chunks[1:]:
        ch = ch.split("# ---- ")[0].split("# circuit triples")[0].strip()
        if ch.startswith("PREFIX") or ch.startswith("CONSTRUCT"):
            out.append(ch)
    if not out:
        raise RuntimeError("CircuitRun emitted no CONSTRUCT plan")
    return out


def plan_constructs(bound_query):
    """Emit the Standard multi-CONSTRUCT plan used by the E6 experiment."""
    return emit_construct_plan(bound_query, "Standard")

def post(construct, accept="application/n-triples"):
    req = U.Request(e3_run.EP, data=construct.encode(), method="POST")
    req.add_header("Content-Type", "application/sparql-query"); req.add_header("Accept", accept)
    t = time.time(); body = U.urlopen(req, timeout=POST_TIMEOUT).read(); return (time.time() - t) * 1000, body

def build(constructs):
    """POST each plan CONSTRUCT, accumulate the DEDUPED triple set; return (build_ms, triples, capped)."""
    ms = 0.0; triples = set(); capped = False
    for c in constructs:
        dt, body = post(c)
        ms += dt
        for line in body.decode("utf-8", "replace").splitlines():
            if line.endswith(" ."):
                triples.add(line)
                if len(triples) > MAXTRIP:
                    capped = True; return ms, triples, capped
    return ms, triples, capped

def parse_circuit(triples):
    """triples (N-Triples lines) -> (circ, ans, typ) via the shared circuit_io parser. `ans` is now
    keyed gate -> TERM-AWARE answer key (circuit_io.answer_key over c:binding), so consumers that invert
    it (g3/g8: {key: gate}) no longer re-merge two distinct answers that share a c:answer STRING. This
    one function backs e6_minus + g3 + g8 + g4 + g6 + e8 + e9."""
    circ, answers, bindings = circuit_io.parse(triples)
    ans = {g: circuit_io.answer_key(bindings[g]) for g in answers}
    typ = {g: op.capitalize() for g, (op, _) in circ.items()}          # counts() checks .endswith('Times')/...
    return circ, ans, typ

def counts(circ, ans, typ):
    times = sum(1 for t in typ.values() if t.endswith("Times"))
    plus = sum(1 for t in typ.values() if t.endswith("Plus"))
    minus = sum(1 for t in typ.values() if t.endswith("Minus"))
    edges = sum(len(pl) if op in ("times", "plus") else 2 for op, pl in circ.values() if op != "leaf")
    return times, plus, minus, edges, len(ans)

def t_string(circ):
    """NPCS per-answer string size, in token occurrences = Σ over derivation (⊗) gates of their ACTUAL
    LEAF inputs (base tokens) — a 2-pattern product contributes 2, a 3-pattern S-star product 3;
    intermediate-gate inputs are NOT tokens. Replaces the old fixed `times*3` proxy that assumed every
    derivation was a 3-way reified triple. This is byte-for-byte G2b's canonical definition
    (g2b_npcs_vs_ours.py), so e6/e8/e9 now share ONE compactness metric with G2b/E11/bench."""
    return sum(1 for op, pl in circ.values() if op == "times"
               for c in pl if circ.get(c, ("",))[0] == "leaf")

def wmc_pwe_check(circ, ans):
    """Assign random probs to the leaf tokens, WMC each answer via compile_bdd, and compare to a
    brute-force possible-world enumeration over the tokens (only if few enough to enumerate)."""
    leaves = sorted({circ[n][1] for n in circ if circ[n][0] == "leaf"})
    if not ans or len(leaves) > 20:
        return None
    random.seed(5); P = {t: round(random.uniform(0.2, 0.9), 3) for t in leaves}
    def ev(nid, world):                                  # evaluate the Boolean function of a gate
        op, pl = circ[nid]
        if op == "leaf": return world[pl]
        if op == "times": return all(ev(c, world) for c in pl)
        if op == "plus":  return any(ev(c, world) for c in pl)
        if op == "minus": return ev(pl[0], world) and not ev(pl[1], world)
        return False
    worst = 0.0
    for root in ans:
        wmc = compile_bdd.probability(circ, root, P)[0]
        pwe = 0.0
        for bits in itertools.product((0, 1), repeat=len(leaves)):
            world = dict(zip(leaves, bits))
            if ev(root, world):
                w = 1.0
                for t in leaves: w *= P[t] if world[t] else 1 - P[t]
                pwe += w
        worst = max(worst, abs(wmc - pwe))
    return worst

COLS = ["query", "repo", "mode", "status", "plan_constructs", "build_ms", "plain_ms", "c_overhead",
        "deriv", "minus_gates", "gates", "edges", "answers", "T_string", "T_circ", "share", "wmc_pwe_maxdiff"]

def run(name):
    repo = os.environ.get("WATDIV_REPO", "watdiv")
    q = open(f"watdiv/{name}.rq").read()
    if BOUND:
        bq, iri = e3_run.bind_source(q)
        if not iri:
            print(f"  [{name}] could not bind a source; skip (try E6_BOUND=0)"); return None
        src = iri.rsplit("/", 1)[-1]
    else:
        bq, src = q, None
    bqf = tempfile.NamedTemporaryFile("w", suffix=".rq", delete=False); bqf.write(bq); bqf.close()
    constructs = plan_constructs(bq)
    _, triples, capped = build(constructs)               # one build for the circuit structure
    mode = f"bound {src}" if src else "unbound"
    if capped:
        print(f"  [{name}/{mode}] circuit > {MAXTRIP} triples (one-shot HTTP cap) — recorded as too-large")
        return dict(query=name, repo=repo, mode=mode, status="too-large")
    circ, ans, typ = parse_circuit(triples)
    times, plus, minus, edges, answers = counts(circ, ans, typ)
    if BOUND and minus == 0:
        print(f"  [{name}/{mode}] NOTE: binding the source degenerated the MINUS (single shared var) — "
              f"reporting, but the non-monotone case needs E6_BOUND=0")
    samples = []                                         # timed builds: warmup + RUNS
    for k in range(RUNS + 1):
        ms, _, _ = build(constructs)
        if k: samples.append(ms)
    build_ms = sum(samples) / len(samples)
    try:                                                 # plain NPCS string query on the SAME query
        plain_ms, _ = post(get_npcs(bqf.name), accept="text/csv")
    except Exception:
        plain_ms = float("nan")
    gates = times + plus + minus
    T_str, T_circ = t_string(circ), gates + edges
    diff = wmc_pwe_check(circ, ans)
    print(f"  [{name}/{mode}] plan={len(constructs)} build={build_ms:.0f}ms deriv(⊗)={times} "
          f"minus(⊖)={minus} gates={gates} ans={answers} "
          f"WMC==PWE Δ={('%.1e' % diff) if diff is not None else 'n/a'}")
    return dict(query=name, repo=repo, mode=mode, status="ok", plan_constructs=len(constructs),
                build_ms=round(build_ms), plain_ms=(round(plain_ms) if plain_ms == plain_ms else None),
                c_overhead=(round(build_ms/plain_ms, 2) if plain_ms == plain_ms and plain_ms else None),
                deriv=times, minus_gates=minus, gates=gates, edges=edges, answers=answers,
                T_string=T_str, T_circ=T_circ, share=round(T_str/T_circ, 3) if T_circ else 0,
                wmc_pwe_maxdiff=(f"{diff:.1e}" if diff is not None else "n/a"))

def main():
    repo = os.environ.get("WATDIV_REPO", "watdiv")
    out = os.environ.get("E6_OUT", "watdiv/e6_minus.csv")
    print(f"Round 2A - MINUS construction on repo '{repo}'  (bound={BOUND}, {RUNS}-run avg)\n")
    rows = [r for r in (run(n) for n in ("M-minus", "M-minus2")) if r]
    if rows:
        with open(out, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=COLS, extrasaction="ignore", restval=""); w.writeheader(); w.writerows(rows)
        print(f"\nwrote {out}")

if __name__ == "__main__":
    main()
