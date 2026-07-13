"""G2b — NPCS (per-answer how-provenance) vs ours (shared circuit): CONSTRUCTION head-to-head,
running the repository's executable clean-room NPCS reimplementation (not just E2's cost model),
same query + same engine + same data.  This is not the NPCS authors' official artifact.

NPCS side : `App Standard query` -> the NPCS rewrite = a SELECT that GROUP_CONCATs each answer's
            derivations into a per-answer provenance STRING; we POST it and measure eval time + the final
            CSV representation bytes (the complete response body).
Ours side : the CircuitRewriter CONSTRUCT plan -> the shared circuit; POST it, measure eval time +
            circuit size (gates+edges) + serialized N-Triples bytes.

Same bound queries as E3 (selective, matches the baselines). Reports per query in THREE
DIMENSIONALLY-SEPARATE comparisons — NEVER bytes÷graph-elements (that ratio is meaningless):
  • STRUCTURAL   : NPCS flat token-occurrences (T_string = Σ each product's ACTUAL token inputs — arity
                   derived from the circuit, not hardcoded) vs our shared gates+edges (T_circ). The claim.
  • SERIALIZED   : NPCS string bytes vs our N-Triples bytes — SAME unit. Honest caveat: our RDF
                   serialization is frequently LARGER here; compactness is structural, not byte-count.
  • COMPILED     : compiled d-DNNF/OBDD nodes — measured in G6/E4, not duplicated here.

  SPARQLCIRC_ENDPOINT=http://localhost:7200/repositories/watdiv python3 g2b_npcs_vs_ours.py
"""
import os, sys, time, subprocess, tempfile, csv
import urllib.request as U
import e3_run
from e6_minus import plan_constructs, parse_circuit, counts, JAR, post
from experiment_timeouts import QUERY_TIMEOUT_S

def npcs_rewrite(qtext):
    qf = tempfile.NamedTemporaryFile("w", suffix=".rq", delete=False); qf.write(qtext); qf.close()
    r = subprocess.run(["java", "-jar", JAR, "Standard", "path", qf.name], capture_output=True, text=True)
    return r.stdout.strip()

def run_select(select):
    req = U.Request(e3_run.EP, data=select.encode(), method="POST")
    req.add_header("Content-Type", "application/sparql-query"); req.add_header("Accept", "text/csv")
    t = time.time(); raw = U.urlopen(req, timeout=QUERY_TIMEOUT_S).read(); ms = (time.time()-t)*1000
    body = raw.decode("utf-8", "replace")
    rows = [l for l in body.splitlines()[1:] if l.strip()]
    return ms, rows, len(raw)                                        # len(raw) = full serialized payload (bytes)

def npcs_side(qtext):
    sel = npcs_rewrite(qtext)
    ms, rows, raw_bytes = run_select(sel)
    # Final SERIALIZED representation bytes = full CSV body (header + rows + newlines). This fixes the
    # earlier char-count + row-only measure (NPCS provenance has multi-byte ⊕/⊗/⊖). The ours side measures
    # its final deduplicated N-Triples model, not multi-CONSTRUCT network traffic; both columns therefore
    # compare final representation sizes in bytes, not symmetric raw HTTP transfer volume.
    return ms, len(rows), raw_bytes                                   # eval_ms, answers, serialized payload bytes

def ours_side(qtext):
    cons = plan_constructs(qtext)
    t = time.time(); triples = set()
    for c in cons:
        _, b = post(c)
        triples.update(l for l in b.decode("utf-8", "replace").splitlines() if l.endswith(" ."))
    ms = (time.time()-t)*1000
    circ, ans, typ = parse_circuit(triples)
    tms, plus, minus, edges, answers = counts(circ, ans, typ)
    gates_edges = tms + plus + minus + edges                 # STRUCTURAL: shared circuit size (T_circ)
    # STRUCTURAL: flat token-occurrences = Σ over products of their ACTUAL token inputs (NOT a hardcoded
    # arity — a 2-pattern P2 product contributes 2, a 3-pattern S-star product contributes 3).
    t_string = sum(1 for _, (op, pl) in circ.items() if op == "times"
                   for c in pl if circ.get(c, ("",))[0] == "leaf")
    ours_bytes = sum(len(l.encode("utf-8")) + 1 for l in triples)  # final deduplicated N-Triples representation
    return ms, answers, gates_edges, t_string, ours_bytes

def main():
    qdir = os.environ.get("G2B_QDIR", "engines/bound")       # bound (selective) queries, like E3
    import glob
    files = sorted(glob.glob(f"{qdir}/*.rq"))
    repo = e3_run.EP.rsplit("/", 1)[-1]
    print(f"G2b — clean-room NPCS reimplementation vs our shared circuit (construction), repo '{repo}'\n")
    print(f"{'query':14} {'ans':>5} | {'STRUCTURAL: T_string':>20} {'T_circ':>7} {'share':>6} | "
          f"{'SERIALIZED: npcs_B':>18} {'ours_B':>8} {'B_ratio':>7} | {'npcs_ms':>7} {'ours_ms':>7}")
    print("  (structural share = flat token-occurrences ÷ shared gates+edges; serialized ratio is bytes÷bytes; "
          "NEVER bytes÷elements)")
    rows = []
    for f in files:
        q = open(f).read(); name = os.path.splitext(os.path.basename(f))[0]
        try:
            n_ms, n_ans, n_bytes = npcs_side(q)
            o_ms, o_ans, o_size, o_tstring, o_bytes = ours_side(q)
        except Exception as ex:
            print(f"  {name}: {type(ex).__name__}: {ex}"); continue
        share = round(o_tstring / o_size, 2) if o_size else 0        # STRUCTURAL compactness (same unit: graph elements)
        b_ratio = round(n_bytes / o_bytes, 2) if o_bytes else 0      # SERIALIZED (same unit: bytes) — may be <1 (ours larger)
        print(f"{name:14} {n_ans:>5} | {o_tstring:>20} {o_size:>7} {share:>5}x | "
              f"{n_bytes:>18} {o_bytes:>8} {b_ratio:>6}x | {n_ms:>7.0f} {o_ms:>7.0f}")
        rows.append(dict(query=name, answers=n_ans,
                         struct_T_string=o_tstring, struct_T_circ=o_size, struct_share=share,
                         serial_npcs_bytes=n_bytes, serial_ours_bytes=o_bytes, serial_byte_ratio=b_ratio,
                         npcs_eval_ms=round(n_ms), ours_eval_ms=round(o_ms)))
    with open("g2b_npcs_vs_ours.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)
    print("\nwrote g2b_npcs_vs_ours.csv  |  STRUCTURAL share is the compactness claim (shared DAG vs flat "
          "how-provenance); SERIALIZED bytes are reported honestly (our RDF is often larger). The NPCS "
          "reimplementation emits "
          "per-answer strings and stops (no probability); we emit the shared circuit and go on to PQE (G3).")

if __name__ == "__main__":
    main()
