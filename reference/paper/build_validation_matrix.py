"""R9.1 — normalized validation matrix: every engine x every semantic pattern (+ separate path block).

Two suites, never mixed:
  * gallery : the 13 E1 non-path shapes (_gallery_shapes.E1_NONPATH). Per (shape, engine): byte-identity
              status vs the current-jar RDF4J reference (from engines/e10_byte_identity.csv), plus the
              shape's reference circuit_triples + circuit_sha256 and its E1 WMC==PWE oracle error.
  * path    : the property-path shapes. These use the client-driven iterative writable protocol, so they
              are verified on the RDF4J reference (verify_engine_paths / e_paths.csv). Read-only engines
              (e.g. QLever) cannot stage the frontier -> the cell is N/A, never a blank success.

Acceptance encoded here:
  - a gallery cell is `ok` only if e10 says byte-identical AND the shape's WMC==PWE error < 1e-9;
  - circuit_sha256 is the content-addressed hash of the deduped reference circuit (same value every engine
    must reproduce -- that is what "byte-identical" means), so one column suffices for all engines;
  - path rows are a SEPARATE block; unsupported/not-run path cells are explicit N/A.

  python3 build_validation_matrix.py            # writes reference/paper/validation_matrix.csv
"""
import os, sys, csv, hashlib

HERE = os.path.dirname(os.path.abspath(__file__))
REF = os.path.dirname(HERE)
sys.path.insert(0, REF)
sys.path.insert(0, os.path.join(REF, "engines"))          # _gallery_shapes lives under engines/
import verify_gallery as vg
from _gallery_shapes import E1_NONPATH

ENGINES = ["graphdb", "oxigraph", "qlever", "millenniumdb"]
E10 = os.path.join(REF, "engines", "e10_byte_identity.csv")
EPATHS = os.path.join(REF, "watdiv", "e_paths.csv")

def canon(nt):
    return "\n".join(sorted({l for l in nt.splitlines() if l.strip().endswith(" .")}))

def ref_circuit_nt(shape):
    """The RDF4J reference circuit N-Triples for a gallery shape (java CircuitRun Standard gallery.ttl)."""
    import subprocess
    return subprocess.run(["java", "-cp", vg.JAR, "npcs.circuit.CircuitRun", "Standard",
                           f"{vg.G}/gallery.ttl", f"{vg.G}/{shape}.sparql"],
                          capture_output=True, text=True, check=True).stdout

def wmc_pwe_error(shape):
    """max_k |circuit_wmc[k] - pwe[k]| over the E1 term-aware answer keys (the correctness oracle)."""
    cw, tw = vg.circuit_wmc(shape), vg.pwe(shape)
    keys = set(cw) | set(tw)
    return max((abs(cw.get(k, 0.0) - tw.get(k, 0.0)) for k in keys), default=0.0)

def load_e10():
    with open(E10) as fh:
        return {r["shape"]: r for r in csv.DictReader(fh)}

def main():
    e10 = load_e10()
    rows = []

    # ---------- gallery suite (non-path) ----------
    for shape in E1_NONPATH:
        if shape not in e10:
            raise RuntimeError(f"{shape} missing from e10_byte_identity.csv")
        nt = ref_circuit_nt(shape)
        c = canon(nt)
        sha = hashlib.sha256(c.encode()).hexdigest()[:16]
        triples = len(c.splitlines())
        err = wmc_pwe_error(shape)
        oracle_ok = err < 1e-9
        for eng in ENGINES:
            e_status = e10[shape].get(eng, "").strip()
            byte_ok = e_status == "OK"
            status = "ok" if (byte_ok and oracle_ok) else ("byte-diff" if not byte_ok else "wmc!=pwe")
            note = "" if byte_ok else f"e10={e_status}"
            rows.append(dict(suite="gallery", pattern=shape, engine=eng, status=status,
                             circuit_triples=triples, circuit_sha256=sha,
                             wmc_pwe_max_abs_error=f"{err:.2e}", notes=note))

    # ---------- path suite (separate block) ----------
    # Reference (RDF4J embedded, == GraphDB's engine) circuit is compiled+WMC-checked by verify_engine_paths;
    # e_paths.csv records its per-shape build result. The client-driven iterative writable protocol is NOT
    # run on read-only engines, so only the reference column is a result here; the rest are explicit N/A.
    WRITABLE_REF = "graphdb"                                    # RDF4J-backed reference endpoint
    path_rows = []
    if os.path.exists(EPATHS):
        with open(EPATHS) as fh:
            for r in csv.DictReader(fh):
                path_rows.append(r)
    for r in path_rows:
        shape = r["query"]
        st = r.get("status", "").strip()
        for eng in ENGINES:
            if eng == WRITABLE_REF:
                status = "ok" if st == "ok" else st or "unknown"
                triples = ""
                note = f"reference (RDF4J); reach_nodes={r.get('reach_nodes','?')} rounds={r.get('rounds','?')}"
                gates = r.get("gates", "")
            else:
                status = "N/A"; triples = ""; gates = ""
                # RESULTS.md: the client-driven iterative path protocol needs a WRITABLE endpoint (frontier
                # staging). QLever is read-only -> genuinely unsupported. Oxigraph is writable-capable; the
                # HTTP path run is pending (reference verified on RDF4J). MDB writability unverified here.
                note = {"qlever": "read-only endpoint: writable path protocol unsupported",
                        "oxigraph": "writable-capable; HTTP iterative path run pending (RDF4J ref verified)",
                        "millenniumdb": "writability for path protocol unverified; not run"}[eng]
            rows.append(dict(suite="path", pattern=shape, engine=eng, status=status,
                             circuit_triples=gates, circuit_sha256="",
                             wmc_pwe_max_abs_error="", notes=note))

    out = os.path.join(HERE, "validation_matrix.csv")
    cols = ["suite", "pattern", "engine", "status", "circuit_triples", "circuit_sha256",
            "wmc_pwe_max_abs_error", "notes"]
    with open(out, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols); w.writeheader(); w.writerows(rows)

    # summary
    g = [r for r in rows if r["suite"] == "gallery"]
    ok = sum(1 for r in g if r["status"] == "ok")
    print(f"gallery: {ok}/{len(g)} cells ok  ({len(E1_NONPATH)} shapes x {len(ENGINES)} engines)")
    worst = max((float(r["wmc_pwe_max_abs_error"]) for r in g), default=0.0)
    print(f"gallery: worst WMC-PWE abs error = {worst:.2e}  (oracle threshold 1e-9)")
    p = [r for r in rows if r["suite"] == "path"]
    pref = [r for r in p if r["engine"] == "graphdb"]
    print(f"path:    {sum(1 for r in pref if r['status']=='ok')}/{len(pref)} reference shapes ok; "
          f"{sum(1 for r in p if r['status']=='N/A')} N/A cells (read-only / protocol-not-run)")
    print(f"wrote {out}  ({len(rows)} rows)")

if __name__ == "__main__":
    main()
