"""G2b — NPCS (per-answer how-provenance) vs ours (shared circuit): CONSTRUCTION head-to-head,
running the ACTUAL rewriters (not just E2's cost model), same query + same engine + same data.

NPCS side : `App Standard query` -> the NPCS rewrite = a SELECT that GROUP_CONCATs each answer's
            derivations into a per-answer provenance STRING; we POST it and measure eval time + total
            output bytes (Σ per-answer string lengths = the real T_string).
Ours side : the CircuitRewriter CONSTRUCT plan -> the shared circuit; POST it, measure eval time +
            circuit size (gates+edges).

Same bound queries as E3 (selective, matches the baselines). Reports per query: answers, NPCS eval_ms
+ output bytes, ours eval_ms + circuit size, and the size ratio (NPCS_bytes / our_gates+edges).

  SPARQLCIRC_ENDPOINT=http://localhost:7200/repositories/watdiv python3 g2b_npcs_vs_ours.py
"""
import os, sys, time, subprocess, tempfile, csv
import urllib.request as U
import e3_run
from e6_minus import plan_constructs, parse_circuit, counts, JAR, post

def npcs_rewrite(qtext):
    qf = tempfile.NamedTemporaryFile("w", suffix=".rq", delete=False); qf.write(qtext); qf.close()
    r = subprocess.run(["java", "-jar", JAR, "Standard", "path", qf.name], capture_output=True, text=True)
    return r.stdout.strip()

def run_select(select):
    req = U.Request(e3_run.EP, data=select.encode(), method="POST")
    req.add_header("Content-Type", "application/sparql-query"); req.add_header("Accept", "text/csv")
    t = time.time(); body = U.urlopen(req, timeout=300).read().decode("utf-8", "replace"); ms = (time.time()-t)*1000
    rows = [l for l in body.splitlines()[1:] if l.strip()]
    return ms, rows

def npcs_side(qtext):
    sel = npcs_rewrite(qtext)
    ms, rows = run_select(sel)
    return ms, len(rows), sum(len(r) for r in rows)          # eval_ms, answers, total provenance bytes

def ours_side(qtext):
    cons = plan_constructs(qtext)
    t = time.time(); triples = set()
    for c in cons:
        _, b = post(c)
        triples.update(l for l in b.decode("utf-8", "replace").splitlines() if l.endswith(" ."))
    ms = (time.time()-t)*1000
    circ, ans, typ = parse_circuit(triples)
    tms, plus, minus, edges, answers = counts(circ, ans, typ)
    return ms, answers, tms + plus + minus + edges           # eval_ms, answers, circuit gates+edges

def main():
    qdir = os.environ.get("G2B_QDIR", "engines/bound")       # bound (selective) queries, like E3
    import glob
    files = sorted(glob.glob(f"{qdir}/*.rq"))
    repo = e3_run.EP.rsplit("/", 1)[-1]
    print(f"G2b — NPCS per-answer strings vs our shared circuit (construction), repo '{repo}'\n")
    print(f"{'query':14} {'answers':>7} | {'NPCS eval_ms':>12} {'NPCS bytes':>11} | {'ours eval_ms':>12} {'circuit g+e':>11} | {'size_win':>8}")
    rows = []
    for f in files:
        q = open(f).read(); name = os.path.splitext(os.path.basename(f))[0]
        try:
            n_ms, n_ans, n_bytes = npcs_side(q)
            o_ms, o_ans, o_size = ours_side(q)
        except Exception as ex:
            print(f"  {name}: {type(ex).__name__}: {ex}"); continue
        win = round(n_bytes / o_size, 2) if o_size else 0
        print(f"{name:14} {n_ans:>7} | {n_ms:>12.0f} {n_bytes:>11} | {o_ms:>12.0f} {o_size:>11} | {win:>7}x")
        rows.append(dict(query=name, answers=n_ans, npcs_eval_ms=round(n_ms), npcs_bytes=n_bytes,
                         ours_eval_ms=round(o_ms), circuit_size=o_size, size_win=win))
    with open("g2b_npcs_vs_ours.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)
    print("\nwrote g2b_npcs_vs_ours.csv  |  NPCS emits per-answer strings and stops (no probability); "
          "we emit the shared circuit and go on to PQE (G3).")

if __name__ == "__main__":
    main()
