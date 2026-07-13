"""R9.2 analysis — the SPARQLprov-style B / R-B / N-R / C-R decomposition table + answer-parity check.

Reads construction_brnc.csv and, per (engine, scale, class, template, instance), assembles the four medians
and the SIGNED deltas (never clamped):
    reification  = R - B      npcs_string = N - R      circuit = C - R
So NPCS total = B + (R-B) + (N-R) = N ; SPARQLcirc total = B + (R-B) + (C-R) = C  (construction only).

Answer-parity gate (the R9.2 correctness requirement, verified here post-hoc from recorded counts):
    B and R are the SAME multiset  -> B_answers == R_answers   (bag semantics, reification-preserving)
    N and C are per DISTINCT answer -> N_answers == C_answers   (set; each == distinct(B) <= B_answers)
Mismatches are printed loudly. Rows where a method is not `ok` are reported as such (never silently 0).

  python3 summarize_brnc.py            # prints table + parity; writes brnc_decomposition.csv
"""
import os, csv, statistics

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "construction_brnc.csv")
OUT = os.path.join(HERE, "brnc_decomposition.csv")

def num(x):
    try: return float(x)
    except (TypeError, ValueError): return None

def main():
    with open(SRC) as fh:
        rows = list(csv.DictReader(fh))
    cells = {}
    for r in rows:
        cells.setdefault((r["engine"], r["scale"], r["class"], r["template"], r["instance"]), {})[r["method"]] = r

    out, parity_fail = [], []
    print(f"{'engine':8} {'scale':5} {'cls':3} {'tmpl':4} {'ans(B/R,N/C)':14} "
          f"{'B':>7} {'R':>7} {'N':>8} {'C':>8}   {'R-B':>7} {'N-R':>8} {'C-R':>8}")
    for key in sorted(cells):
        eng, scale, cls, tmpl, inst = key
        m = cells[key]
        def med(x): return num(m[x]["median_ms"]) if x in m and m[x]["status"] == "ok" else None
        def ans(x): return m[x]["answers"] if x in m and m[x]["status"] == "ok" else None
        B, R, N, C = med("B"), med("R"), med("N"), med("C")
        bA, rA, nA, cA = ans("B"), ans("R"), ans("N"), ans("C")
        # parity (only when both sides ok)
        if bA is not None and rA is not None and bA != rA:
            parity_fail.append(f"{key}: B_ans={bA} != R_ans={rA} (multiset should match)")
        if nA is not None and cA is not None and nA != cA:
            parity_fail.append(f"{key}: N_ans={nA} != C_ans={cA} (distinct-answer count should match)")
        d = lambda a, b: round(a - b, 1) if (a is not None and b is not None) else None
        stat = {k: (m[k]["status"] if k in m else "-") for k in "BRNC"}
        anss = f"{bA}/{rA},{nA}/{cA}"
        def cell(v, st): return f"{v:.0f}" if v is not None else st
        print(f"{eng:8} {scale:5} {cls:3} {tmpl:4} {anss:14} "
              f"{cell(B,stat['B']):>7} {cell(R,stat['R']):>7} {cell(N,stat['N']):>8} {cell(C,stat['C']):>8}   "
              f"{str(d(R,B)):>7} {str(d(N,R)):>8} {str(d(C,R)):>8}")
        out.append(dict(engine=eng, scale=scale, **{"class": cls}, template=tmpl, instance=inst,
                        b_ms=B, r_ms=R, n_ms=N, c_ms=C, reif_delta=d(R, B), npcs_delta=d(N, R),
                        circ_delta=d(C, R), b_ans=bA, r_ans=rA, n_ans=nA, c_ans=cA,
                        status_B=stat["B"], status_R=stat["R"], status_N=stat["N"], status_C=stat["C"]))

    cols = ["engine", "scale", "class", "template", "instance", "b_ms", "r_ms", "n_ms", "c_ms",
            "reif_delta", "npcs_delta", "circ_delta", "b_ans", "r_ans", "n_ans", "c_ans",
            "status_B", "status_R", "status_N", "status_C"]
    with open(OUT, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols); w.writeheader(); w.writerows(out)
    print(f"\nwrote {OUT}  ({len(out)} cells)")
    if parity_fail:
        print(f"\n!!! {len(parity_fail)} ANSWER-PARITY MISMATCHES:")
        for f in parity_fail[:20]:
            print("   " + f)
    else:
        print("answer-parity: all comparable cells OK (B==R multiset, N==C distinct)")

if __name__ == "__main__":
    main()
