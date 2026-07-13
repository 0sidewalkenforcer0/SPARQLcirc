"""R9.3 — sharing boundary & actual NPCS-vs-SPARQLcirc comparison, REUSING R9.2's N/C responses.

Reads reference/paper/construction_brnc.csv (from paper_construction_matrix.py) and joins, per
(engine, scale, class, template, instance), the N_clean row with the C row. No query is re-issued: the
sizes were captured during R9.2 (N: `npcs_token_occurrences` + `response_bytes` of the provenance CSV;
C: `gates`+`edges` + `response_bytes` of the deduplicated circuit N-Triples).

Ratios are ALWAYS in matched units (the R8.2 metric-hygiene rule — never bytes/elements):
  * structural  = NPCS token occurrences / (SPARQLcirc gates+edges)   [elements / elements]
  * serialized  = NPCS CSV bytes / SPARQLcirc N-Triples bytes         [bytes / bytes]
  * construct   = N_clean median ms / C median ms                     [ms / ms]
A ratio < 1 means NPCS is smaller/faster on that query (the honest low-sharing counterexample; kept, not
hidden). NPCS/C unsupported on an engine -> that instance is N/A; time is never inferred across engines.

N_clean is the clean-room NpcsRewriter (a *reimplementation* of the NPCS rules), not the authors' binary
-- labeled as such in every output row.

  python3 build_sharing_npcs.py            # writes reference/paper/sharing_npcs.csv
"""
import os, csv, json, statistics

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "construction_brnc.csv")
OUT = os.path.join(HERE, "sharing_npcs.csv")

def num(x):
    try: return float(x)
    except (TypeError, ValueError): return None

def ratio(a, b):
    a, b = num(a), num(b)
    return round(a / b, 4) if (a is not None and b not in (None, 0)) else None

def main():
    if not os.path.exists(SRC):
        raise SystemExit(f"{SRC} not found — run paper_construction_matrix.py (R9.2) first")
    with open(SRC) as fh:
        rows = list(csv.DictReader(fh))
    # index by cell -> {method: row}
    cells = {}
    for r in rows:
        key = (r["engine"], r["scale"], r["class"], r["template"], r["instance"])
        cells.setdefault(key, {})[r["method"]] = r

    out = []
    for (engine, scale, cls, tmpl, inst), m in sorted(cells.items()):
        n, c = m.get("N"), m.get("C")
        n_ok = n and n["status"] == "ok"
        c_ok = c and c["status"] == "ok"
        note = ""
        if not n_ok:
            note = f"N_clean {n['status'] if n else 'not-run'} -> N/A"
        elif not c_ok:
            note = f"C {c['status'] if c else 'not-run'} -> N/A"
        npcs_tok = n["npcs_token_occurrences"] if n_ok else None
        npcs_bytes = n["response_bytes"] if n_ok else None
        gates = c["gates"] if c_ok else None
        edges = c["edges"] if c_ok else None
        circ_elems = (num(gates) + num(edges)) if (c_ok and gates and edges) else None
        circ_bytes = c["response_bytes"] if c_ok else None
        out.append(dict(
            engine=engine, scale=scale, **{"class": cls}, template=tmpl, instance=inst,
            implementation="N_clean (NPCS reimplementation) vs SPARQLcirc",
            answers=(c["answers"] if c_ok else (n["answers"] if n_ok else None)),
            derivations=(c["derivations"] if c_ok else None),
            npcs_token_occurrences=npcs_tok, npcs_csv_bytes=npcs_bytes,
            circuit_gates=gates, circuit_edges=edges, circuit_nt_bytes=circ_bytes,
            n_median_ms=(n["median_ms"] if n_ok else None),
            c_median_ms=(c["median_ms"] if c_ok else None),
            structural_ratio_npcs_over_circ=ratio(npcs_tok, circ_elems),
            serialized_ratio_npcs_over_circ=ratio(npcs_bytes, circ_bytes),
            construct_ratio_n_over_c=ratio(n["median_ms"] if n_ok else None,
                                           c["median_ms"] if c_ok else None),
            notes=note))

    cols = ["engine", "scale", "class", "template", "instance", "implementation", "answers", "derivations",
            "npcs_token_occurrences", "npcs_csv_bytes", "circuit_gates", "circuit_edges", "circuit_nt_bytes",
            "n_median_ms", "c_median_ms", "structural_ratio_npcs_over_circ",
            "serialized_ratio_npcs_over_circ", "construct_ratio_n_over_c", "notes"]
    with open(OUT, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols); w.writeheader(); w.writerows(out)

    # honest aggregate: how many instances show NPCS smaller (ratio<1) vs SPARQLcirc smaller, per axis
    def summarize(field, label):
        vals = [num(r[field]) for r in out if num(r[field]) is not None]
        if not vals:
            print(f"  {label}: no comparable cells yet"); return
        lt1 = sum(1 for v in vals if v < 1)
        print(f"  {label}: n={len(vals)} median={statistics.median(vals):.3f}  "
              f"NPCS-smaller(<1)={lt1}  SPARQLcirc-smaller(>=1)={len(vals)-lt1}")
    print(f"wrote {OUT}  ({len(out)} cells)")
    summarize("structural_ratio_npcs_over_circ", "structural (tokens/elems)")
    summarize("serialized_ratio_npcs_over_circ", "serialized (bytes/bytes)  ")
    summarize("construct_ratio_n_over_c",       "construct  (N ms / C ms)  ")

if __name__ == "__main__":
    main()
