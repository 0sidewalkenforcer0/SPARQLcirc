"""r9.2b multisource — content-addressed cross-source DEDUP vs flat per-source how-provenance.

Multi-source setting: the SAME query is answered over K sources that partially OVERLAP (share
derivations — e.g. mirrored/duplicated facts, common reference data). Content-addressing stores each
DISTINCT derivation ONCE (a shared gate, identified by SHA256 of its content), so the circuit scales
with the size of the UNION of derivations. Flat per-source how-provenance (NPCS/SPARQLprov) emits each
source's derivations INDEPENDENTLY, repeating every shared derivation once per source — so it scales
with the SUM over sources. The gap is exactly the cross-source redundancy.

This is E11's cross-ANSWER sharing win, re-run along the cross-SOURCE axis. It is a content-addressing
result (representation + downstream PQE), *orthogonal* to construction time (our known weakness): it is
why one shared circuit is the right structure when provenance spans multiple overlapping sources — the
"100M × 2 sources" stress case, kept a distinct-sources scenario, NOT a merged 200M scale point.

Measures reused from E11 (`repr_size` = gates+edges of a DAG): T_circuit = one shared DAG over all
sources' answers (full cross-source + cross-answer dedup); T_flat = Σ over answers of that answer's own
DAG (per-answer dedup only, NO cross-source sharing = the flat NPCS/SPARQLprov representation).

Pure Python, zero deps. Run: `python3 multisource_dedup.py [--selftest]`.
"""
import os, csv, time, argparse
import compile_bdd
import e11_per_answer_vs_shared as e11

HERE = os.path.dirname(os.path.abspath(__file__))


def build_multisource(N, d, K, overlap):
    """K sources, N answers each; a fraction `overlap` of every source's derivations are drawn from a
    COMMON shared pool (identical leaves -> identical content -> ONE gate); the rest are source-unique.
    Content-addressed node identity: a derivation over the same leaf tokens is the same gate everywhere.
    Returns (circ, roots): circ[node]=(op,payload) in E11 format; roots[(k,i)] = that answer's gate."""
    circ, roots = {}, {}
    n_shared = int(round(overlap * N))

    def add_deriv(deriv_id, leaf_tokens):
        for t in leaf_tokens:
            circ[t] = ("leaf", t)                       # content-addressed leaf (same token -> same gate)
        node = f"T:{deriv_id}"
        if node not in circ:
            circ[node] = ("times", list(leaf_tokens))   # conjunctive (BGP) derivation of d facts
        return node

    shared = [add_deriv(f"shared/{j}", [f"shared/{j}/{a}" for a in range(d)]) for j in range(n_shared)]
    for k in range(K):
        for i in range(N):
            roots[(k, i)] = shared[i] if i < n_shared \
                else add_deriv(f"src{k}/{i}", [f"src{k}/{i}/{a}" for a in range(d)])
    return circ, roots


def measure(N, d, K, overlap, with_compile=True):
    """Representation sizes (always) and end-to-end compile+WMC (optional) for OURS vs FLAT."""
    circ, roots = build_multisource(N, d, K, overlap)
    t_circuit = e11.repr_size(circ, roots)                                  # one shared DAG (all sources)
    t_flat = sum(e11.repr_size(circ, {key: r}) for key, r in roots.items())  # per-answer, no cross dedup
    rec = {"N": N, "d": d, "sources": K, "overlap": overlap,
           "t_circuit": t_circuit, "t_flat": t_flat,
           "size_dedup": round(t_flat / t_circuit, 3) if t_circuit else None,
           "distinct_gates": len(circ), "answers": len(roots)}
    if with_compile:
        P = {n: 0.5 for n, (op, _) in circ.items() if op == "leaf"}
        order = e11.global_order(circ, roots)
        t = time.time(); e11.compile_shared(circ, roots, P, order)          # OURS: compile once, shared
        rec["shared_ms"] = round((time.time() - t) * 1000.0, 3)
        t = time.time()                                                     # FLAT: per answer, independent
        for r in roots.values():
            sub, sub_root = e11.flatten_sop(circ, r)
            o = compile_bdd.leaf_order(sub, sub_root)
            bdd = compile_bdd.ROBDD(o)
            bdd.wmc(compile_bdd.compile_root(sub, sub_root, bdd, {}), P)
        rec["perans_ms"] = round((time.time() - t) * 1000.0, 3)
        rec["time_dedup"] = round(rec["perans_ms"] / rec["shared_ms"], 3) if rec["shared_ms"] else None
    return rec


def run_all(N=200, d=4, out=None):
    out = out or os.path.join(HERE, "multisource_dedup.csv")
    rows = []
    for ov in (0.0, 0.25, 0.5, 0.75, 1.0):          # 2 sources, overlap sweep (the "2 sources" panel)
        rows.append({"sweep": "overlap2", **measure(N, d, 2, ov)})
    for K in (1, 2, 3, 4, 5, 6):                     # K sources at fixed overlap (dedup grows with K)
        rows.append({"sweep": "ksources", **measure(N, d, K, 0.5)})
    cols = ["sweep", "N", "d", "sources", "overlap", "answers", "distinct_gates",
            "t_circuit", "t_flat", "size_dedup", "shared_ms", "perans_ms", "time_dedup"]
    with open(out, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore"); w.writeheader(); w.writerows(rows)
    for r in rows:
        print(f"  {r['sweep']:<9} K={r['sources']} ov={r['overlap']:.2f} "
              f"size {r['t_flat']:>7}->{r['t_circuit']:>7} ({r['size_dedup']}×)  "
              f"pqe {r.get('perans_ms')}->{r.get('shared_ms')} ({r.get('time_dedup')}×)")
    print(f"wrote {out}")


def selftest():
    a = measure(100, 4, 2, 0.0, with_compile=False)   # no overlap -> no cross-source dedup
    b = measure(100, 4, 2, 1.0, with_compile=False)   # identical sources -> ~2× dedup
    print("selftest overlap0:", a["size_dedup"], "overlap1:", b["size_dedup"])
    assert abs(a["size_dedup"] - 1.0) < 0.02, a
    assert b["size_dedup"] > 1.9, b
    c = measure(100, 4, 4, 1.0, with_compile=False)   # 4 identical sources -> ~4× dedup
    assert c["size_dedup"] > 3.8, c
    print("selftest OK — dedup 1.0× at overlap 0, ~2× at overlap 1 (2 src), ~4× at 4 identical sources")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--N", type=int, default=200)
    ap.add_argument("--d", type=int, default=4)
    args, _ = ap.parse_known_args()
    selftest() if args.selftest else run_all(N=args.N, d=args.d)
