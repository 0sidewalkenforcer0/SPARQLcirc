"""R9.2 (D2) — answer-parity verifier for the B/R/N/C decomposition, run as a SEPARATE pass so a
legitimate bag-vs-set / OPTIONAL difference never aborts the timing harness.

Per (scale, class, template) in the frozen manifest:
  * B vs R  — the CRITICAL control check: B (base) and R (reification-only) must return the SAME canonical
    answer MULTISET (reification is algebra-preserving). Both are plain CSV from the same engine, so an exact
    sorted-row multiset comparison is term-faithful. This is what proves R is a faithful control for B.
  * N vs C  — NPCS and SPARQLcirc emit one provenance object per DISTINCT answer, so we compare distinct
    answer counts: |distinct N answer-tuples| == |C answer gates|. (For OPTIONAL, distinct(N/C) may exceed
    distinct(B)'s projected tuples when an OPTIONAL var toggles bound/unbound — reported, not failed.)

Large results are capped (LIMIT) and reported as count-only, never silently truncated. Reuses the timing
harness's exact query generators so the verified queries are byte-identical to what R9.2 times.

  python3 verify_brnc_parity.py --scale 10M            # writes brnc_parity.csv
"""
import os, sys, csv, argparse, subprocess, collections
sys.setrecursionlimit(1_000_000)
HERE = os.path.dirname(os.path.abspath(__file__)); REF = os.path.dirname(HERE)
sys.path.insert(0, REF); sys.path.insert(0, HERE)
import circuit_io
import paper_construction_matrix as pcm

CAP = 500_000                                                # max rows fetched for a full multiset compare

def fetch_csv(ep, body, cap=CAP):
    """POST a SELECT, return (data_rows_as_tuples, capped?). Drops the CSV header line."""
    q = body.rstrip()
    if "limit" not in q.lower():
        q += f"\nLIMIT {cap + 1}"
    _, _, _, text = pcm.post_timed(ep, q, "text/csv", keep=True)
    lines = text.splitlines()
    if not lines:
        return [], False
    rows = [tuple(next(csv.reader([ln]))) for ln in lines[1:]]     # parse each CSV data line
    return rows, len(rows) > cap

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--scale", default="10M")
    ap.add_argument("--classes", default="L,S,F,C,O,M")
    args = ap.parse_args()
    eps = pcm.ENGINES["graphdb"][args.scale]
    classes = set(args.classes.split(","))
    with open(os.path.join(HERE, "workload_manifest.csv")) as fh:
        manifest = [r for r in csv.DictReader(fh) if r["scale"] == args.scale and r["class"] in classes]

    out = []
    print(f"{'cls/tmpl':10} {'B':>7} {'R':>7} {'B==R multiset':14} {'N#':>6} {'C#':>6} {'N==C':6}  note")
    for row in manifest:
        cls, tmpl = row["class"], row["template"]
        qtext = open(os.path.join(REF, row["query_file"])).read()
        note = []
        # --- B vs R : canonical multiset ---
        try:
            B, bc = fetch_csv(eps["base"], pcm.q_base(qtext))
            R, rc = fetch_csv(eps["reified"], pcm.q_reify(qtext))
            capped = bc or rc
            br_equal = (collections.Counter(B) == collections.Counter(R)) if not capped else None
            if capped: note.append(f"B/R capped@{CAP}; count-only")
        except Exception as ex:
            B = R = []; br_equal = None; note.append(f"B/R err:{type(ex).__name__}")
        # --- N vs C : distinct answer counts ---
        try:
            Nrows, nc_cap = fetch_csv(eps["reified"], pcm.q_npcs(qtext))
            n_distinct = len({r[:-1] for r in Nrows})           # drop the trailing provenance column
            if nc_cap: note.append(f"N capped@{CAP}")
        except Exception as ex:
            n_distinct = None; note.append(f"N err:{type(ex).__name__}")
        try:
            plan = pcm.c_construct_plan(qtext)
            tri = []
            for b in plan:
                _, _, _, text = pcm.post_timed(eps["reified"], b, "application/n-triples", keep=True)
                tri.extend(l for l in text.splitlines() if l.endswith(" ."))
            _, ans_gates, _ = circuit_io.parse(set(tri))
            c_distinct = len(ans_gates)
        except Exception as ex:
            c_distinct = None; note.append(f"C err:{type(ex).__name__}")
        nc_equal = (n_distinct == c_distinct) if (n_distinct is not None and c_distinct is not None) else None
        brs = {True: "OK", False: "MISMATCH", None: "capped/err"}[br_equal]
        ncs = {True: "OK", False: "DIFF", None: "-"}[nc_equal]
        print(f"{cls+'/'+tmpl:10} {len(B):>7} {len(R):>7} {brs:14} {str(n_distinct):>6} {str(c_distinct):>6} "
              f"{ncs:6}  {'; '.join(note)}")
        out.append(dict(scale=args.scale, **{"class": cls}, template=tmpl, b_rows=len(B), r_rows=len(R),
                        br_multiset_equal=br_equal, n_distinct=n_distinct, c_distinct=c_distinct,
                        nc_count_equal=nc_equal, notes="; ".join(note)))

    outp = os.path.join(HERE, "brnc_parity.csv")
    cols = ["scale", "class", "template", "b_rows", "r_rows", "br_multiset_equal",
            "n_distinct", "c_distinct", "nc_count_equal", "notes"]
    with open(outp, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols); w.writeheader(); w.writerows(out)
    br_fail = [r for r in out if r["br_multiset_equal"] is False]
    nc_fail = [r for r in out if r["nc_count_equal"] is False]
    print(f"\nwrote {outp}  ({len(out)} templates)")
    print(f"B==R multiset: {sum(1 for r in out if r['br_multiset_equal'] is True)} OK, "
          f"{len(br_fail)} MISMATCH  (the reification-only control faithfulness check)")
    print(f"N==C distinct: {sum(1 for r in out if r['nc_count_equal'] is True)} OK, {len(nc_fail)} DIFF "
          f"(DIFF is expected for OPTIONAL bound/unbound multiplicity)")
    for r in br_fail:
        print(f"   B!=R: {r['class']}/{r['template']}  b={r['b_rows']} r={r['r_rows']}")

if __name__ == "__main__":
    main()
