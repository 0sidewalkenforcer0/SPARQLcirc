"""Generate the presentation figures from the committed experiment CSVs.

Reproducible: reads reference/*.csv and reference/watdiv/*.csv (the actual committed results) and writes
PNGs into presentation/figures/. The one non-CSV source is the canonical 5-run timing table (it lives in
reference/CANONICAL_TIMINGS.md); its 3 rows are transcribed below with a provenance comment.

    cd presentation && python3 make_figures.py
"""
import os, csv
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REF  = os.path.join(HERE, "..", "reference")
OUT  = os.path.join(HERE, "figures")
os.makedirs(OUT, exist_ok=True)

plt.rcParams.update({"font.size": 11, "axes.grid": True, "grid.alpha": 0.30,
                     "figure.dpi": 140, "savefig.bbox": "tight", "axes.axisbelow": True})
OURS, BASE, ALT = "#1f77b4", "#d62728", "#2ca02c"      # ours=blue, baseline/OBDD=red, d-DNNF/aux=green


def rd(rel):
    with open(os.path.join(REF, rel)) as f:
        return list(csv.DictReader(f))

def save(fig, name):
    p = os.path.join(OUT, name)
    fig.savefig(p); plt.close(fig); print("wrote", os.path.relpath(p, HERE))


# ---- Fig 1: E4 — compile size vs #tokens at BOUNDED treewidth (tw=2): OBDD explodes+times out, d-DNNF flat
def fig_e4_bounded():
    r = [x for x in rd("watdiv/e4_results.csv") if x["family"] == "bounded_tw2"]
    n = [int(x["n_tokens"]) for x in r]
    dd = [int(x["ddnnf_nodes"]) for x in r]
    obdd_n = [int(x["n_tokens"]) for x in r if x["obdd_size"]]
    obdd = [int(x["obdd_size"]) for x in r if x["obdd_size"]]
    fig, ax = plt.subplots(figsize=(6.4, 4.2))
    ax.plot(obdd_n, obdd, "o-", color=BASE, label="OBDD (fixed-order)")
    ax.plot(n, dd, "s-", color=ALT, label="d-DNNF (d4)")
    tout = min(int(x["n_tokens"]) for x in r if x["status"] == "obdd-timeout")
    ax.axvline(tout, color=BASE, ls=":", alpha=0.6)
    ax.text(tout, ax.get_ylim()[1] * 0.4, "  OBDD times out\n  (>300 s) →", color=BASE, fontsize=9, va="center")
    ax.set_yscale("log"); ax.set_xscale("log")
    ax.set_xlabel("circuit size  (#tokens)"); ax.set_ylabel("compiled size  (nodes, log)")
    ax.set_title("E4 — bounded treewidth (tw=2): d-DNNF stays polynomial,\nfixed-order OBDD blows up")
    ax.legend(loc="lower right")
    save(fig, "fig1_E4_bounded_treewidth.png")


# ---- Fig 2: E4 — compile size vs treewidth (growing tw): both grow, d-DNNF below OBDD
def fig_e4_growing():
    r = [x for x in rd("watdiv/e4_results.csv") if x["family"] == "growing_tw_layer"]
    tw = [int(x["tw"]) for x in r]
    obdd = [int(x["obdd_size"]) for x in r]
    dd = [int(x["ddnnf_nodes"]) for x in r]
    fig, ax = plt.subplots(figsize=(6.4, 4.2))
    ax.plot(tw, obdd, "o-", color=BASE, label="OBDD")
    ax.plot(tw, dd, "s-", color=ALT, label="d-DNNF (d4)")
    ax.set_yscale("log")
    ax.set_xlabel("treewidth  tw"); ax.set_ylabel("compiled size  (nodes, log)")
    ax.set_title("E4 — growing treewidth: BOTH hit the #P wall (2^Θ(tw)),\nd-DNNF later & smaller")
    ax.legend(loc="upper left")
    save(fig, "fig2_E4_growing_treewidth.png")


# ---- Fig 3: E2 — compactness (string/circuit) grows with #derivations (sharing)
def fig_e2_compactness():
    r = rd("bench.csv")
    lay = [x for x in r if x["instance"].startswith("layered")]
    deep = [x for x in r if x["instance"].startswith("deep")]
    fig, ax = plt.subplots(figsize=(6.4, 4.2))
    ax.plot([int(x["derivations"]) for x in lay], [float(x["sharing"]) for x in lay],
            "o-", color=OURS, label="layered (width 2–8)")
    ax.plot([int(x["derivations"]) for x in deep], [float(x["sharing"]) for x in deep],
            "s-", color=ALT, label="deep (depth 4–12)")
    ax.axhline(1.0, color="gray", ls="--", alpha=0.6); ax.text(20, 1.05, "break-even (1×)", color="gray", fontsize=9)
    ax.annotate("201×", (4096, 201.4), textcoords="offset points", xytext=(-4, 6), color=ALT, fontweight="bold")
    ax.set_xscale("log"); ax.set_yscale("log")
    ax.set_xlabel("#derivations  D  (grows with query depth/branching)")
    ax.set_ylabel("compactness  =  per-answer strings ÷ shared circuit")
    ax.set_title("E2 — sharing pays off with depth:\nflat ≈ strings, deep queries → 100×+ smaller circuit")
    ax.legend(loc="upper left")
    save(fig, "fig3_E2_compactness.png")


# ---- Fig 4: E11 — shared compile vs per-answer, wall-clock vs #answers
def fig_e11_shared():
    r = rd("e11_scale.csv")
    N = [int(x["N"]) for x in r]
    fig, ax = plt.subplots(figsize=(6.4, 4.2))
    ax.plot(N, [float(x["perans_ms"]) for x in r], "s-", color=BASE, label="per-answer (NPCS/SPARQLprov style)  Θ(N·S)")
    ax.plot(N, [float(x["shared_ms"]) for x in r], "o-", color=OURS, label="shared circuit (ours)  Θ(N+S)")
    ax.set_xlabel("#answers  N"); ax.set_ylabel("compile+WMC time  (ms)")
    ax.set_title("E11 — one shared compile vs per-answer:\nsame probabilities (Δ=0), ~9× faster at N=1000")
    ax.legend(loc="upper left")
    save(fig, "fig4_E11_shared_vs_peranswer.png")


# ---- Fig 5: ProvSQL head-to-head (G4) — TPC-H Q3 across 5 segments, ours faster
def fig_provsql():
    r = [x for x in rd("g4_instances.csv") if x["shape"] == "tpch-Q3-SPJ"]
    seg = [x["instance"][:4] for x in r]
    ours = [float(x["ours_median_ms"]) / 1000 for x in r]
    prov = [float(x["provsql_median_ms"]) / 1000 for x in r]
    x = np.arange(len(seg)); w = 0.38
    fig, ax = plt.subplots(figsize=(6.8, 4.2))
    ax.bar(x - w/2, ours, w, color=OURS, label="ours (stock SPARQL engine)")
    ax.bar(x + w/2, prov, w, color=BASE, label="ProvSQL (modified PostgreSQL)")
    ax.set_xticks(x); ax.set_xticklabels(seg)
    ax.set_ylabel("PQE latency  (s, 5-run median)")
    ax.set_xlabel("TPC-H Q3-SPJ  ·  c_mktsegment instance")
    ax.set_title("ProvSQL head-to-head (G4): exact PQE, no engine fork\nours comparable / slightly faster on all 5")
    ax.legend(loc="upper right")
    save(fig, "fig5_provsql_headtohead.png")


# ---- Fig 6: G3 canonical end-to-end PQE breakdown (WMC is tiny; Q3 compile = pure-Python ordering)
def fig_pqe_breakdown():
    # transcribed from reference/CANONICAL_TIMINGS.md (current HEAD, 5-run): construct / compile / WMC (ms)
    rows = [("WatDiv S-star\n(2 ans)", 10, 2, 0),
            ("TPC-H Q3\n(14 908 ans)", 3080, 3300, 36),
            ("Wikidata WD-path\n(P279+, 16 ans)", 2144, 1, 0)]
    labels = [r[0] for r in rows]
    construct = np.array([r[1] for r in rows]) / 1000
    compile_ = np.array([r[2] for r in rows]) / 1000
    wmc = np.array([r[3] for r in rows]) / 1000
    x = np.arange(len(rows))
    fig, ax = plt.subplots(figsize=(6.8, 4.2))
    ax.bar(x, construct, 0.55, color=OURS, label="construct (engine + RDF parse)")
    ax.bar(x, compile_, 0.55, bottom=construct, color="#ff7f0e", label="compile (ROBDD + variable ordering)")
    ax.bar(x, wmc, 0.55, bottom=construct + compile_, color=ALT, label="WMC (weighted count)")
    for i, r in enumerate(rows):
        ax.text(i, (r[1]+r[2]+r[3])/1000 + 0.12, f"WMC={r[3]} ms", ha="center", fontsize=8.5, color=ALT)
    ax.set_xticks(x); ax.set_xticklabels(labels, fontsize=9)
    ax.set_ylim(0, 7.6)
    ax.set_ylabel("end-to-end PQE latency  (s)")
    ax.set_title("G3 — end-to-end PQE breakdown (5-run):\nweighted count is never the cost (≤36 ms); Q3 compile = pure-Python ordering")
    ax.legend(loc="upper left", fontsize=9)
    save(fig, "fig6_G3_pqe_breakdown.png")


# ---- Fig 7: G6 — correctness on REAL circuits: d4 == OBDD == PWE, 26/26
def fig_correctness():
    r = rd("g6_d4.csv")
    fams = sorted(set(x["query"] for x in r))
    cmap = {f: c for f, c in zip(fams, [OURS, "#ff7f0e", ALT])}
    fig, ax = plt.subplots(figsize=(5.6, 4.6))
    for f in fams:
        pts = [x for x in r if x["query"] == f]
        ax.scatter([float(x["pwe"]) for x in pts], [float(x["obdd_wmc"]) for x in pts],
                   s=46, color=cmap[f], label=f"{f}  ({len(pts)})", alpha=0.8, edgecolor="white", linewidth=0.5)
    ax.plot([0, 1], [0, 1], "--", color="gray", alpha=0.7, label="y = x")
    ax.set_xlabel("possible-world enumeration  (ground truth)")
    ax.set_ylabel("our OBDD-WMC")
    ax.set_title("G6 — exact on real circuits:\nOBDD = PWE = d4, 26/26 (incl. all 16 property paths)")
    ax.legend(loc="upper left", fontsize=9)
    ax.set_xlim(-0.03, 1.03); ax.set_ylim(-0.03, 1.03)
    save(fig, "fig7_G6_correctness.png")


if __name__ == "__main__":
    fig_e4_bounded(); fig_e4_growing(); fig_e2_compactness(); fig_e11_shared()
    fig_provsql(); fig_pqe_breakdown(); fig_correctness()
    print("\nAll figures written to", os.path.relpath(OUT, HERE) + "/")
