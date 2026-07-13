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
    ax.text(tout, ax.get_ylim()[1] * 0.4, "  OBDD hits the\n  120 s timeout →", color=BASE, fontsize=9, va="center")
    ax.set_yscale("log"); ax.set_xscale("log")
    ax.set_xlabel("circuit size  (#tokens)"); ax.set_ylabel("compiled size  (nodes, log)")
    ax.set_title("E4 — bounded treewidth (tw=2): d-DNNF stays polynomial while the\nfixed-order OBDD blows up (motivates order-robust d-DNNF compilation)")
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
    ax.set_title("E4 — growing treewidth: both compiled forms grow exponentially in tw;\nd-DNNF becomes smaller from tw ≈ 5")
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
    ax.set_ylabel("STRUCTURAL compactness  =  string tokens ÷ circuit (gates+edges)")
    ax.set_title("E2 — STRUCTURAL sharing (tokens vs gates+edges, NOT serialized bytes):\nflat ≈ strings, deep queries → 100×+  (on selective queries our RDF bytes are larger — see G2b)")
    ax.legend(loc="upper left")
    save(fig, "fig3_E2_compactness.png")


# ---- Fig 4: E11 — shared compile vs per-answer, wall-clock vs #answers
def fig_e11_shared():
    r = rd("e11_scale.csv")
    N = [int(x["N"]) for x in r]
    fig, ax = plt.subplots(figsize=(6.4, 4.2))
    ax.plot(N, [float(x["perans_ms"]) for x in r], "s-", color=BASE, label="simulated per-answer, same OBDD compiler  Θ(N·S)")
    ax.plot(N, [float(x["shared_ms"]) for x in r], "o-", color=OURS, label="shared circuit (ours)  Θ(N+S)")
    ax.set_xlabel("#answers  N"); ax.set_ylabel("compile+WMC time  (ms)")
    ax.set_title("E11 — shared vs per-answer compile (SYNTHETIC shared-prefix family):\nsame probabilities (Δ=0), ~9× faster at N=1000  (real tree-joins: representation win ≤1)")
    ax.legend(loc="upper left")
    save(fig, "fig4_E11_shared_vs_peranswer.png")


# ---- Fig 5: ProvSQL head-to-head (G4) — TPC-H Q3 across 5 segments, ours faster
def fig_provsql():
    r = [x for x in rd("g4_instances.csv") if x["shape"] == "tpch-Q3-SPJ"]
    seg = [x["instance"][:4] for x in r]
    ours = [float(x["ours_median_ms"]) / 1000 for x in r]
    prov = [float(x["provsql_median_ms"]) / 1000 for x in r]
    ours_sd = [float(x["ours_sd_ms"]) / 1000 for x in r]
    prov_sd = [float(x["provsql_sd_ms"]) / 1000 for x in r]
    x = np.arange(len(seg)); w = 0.38
    fig, ax = plt.subplots(figsize=(6.8, 4.4))
    ax.bar(x - w/2, ours, w, yerr=ours_sd, capsize=3, color=OURS, label="ours (stock SPARQL engine)")
    ax.bar(x + w/2, prov, w, yerr=prov_sd, capsize=3, color=BASE, label="ProvSQL (modified PostgreSQL)")
    ax.set_xticks(x); ax.set_xticklabels(seg)
    ax.set_ylabel("PQE latency  (s, 5-run median ± sd)")
    ax.set_xlabel("TPC-H Q3-SPJ  ·  c_mktsegment instance")
    ax.set_title("Forced probability evaluation on TPC-H Q3 (G4):\nours faster on all 5 segments — exact PQE, no engine fork")
    ax.legend(loc="upper right")
    ax.text(0.02, 0.02, "per-answer probability parity (max_abs_error = 0) verified separately on a\nreconvergent query — R8.3, not this Q3 chart",
            transform=ax.transAxes, fontsize=8, color="#555", va="bottom")
    save(fig, "fig5_provsql_headtohead.png")


# ---- Fig 6: G3 canonical end-to-end PQE breakdown (WMC is tiny; Q3 compile = pure-Python ordering)
def fig_pqe_breakdown():
    g = rd("g4_rigor.csv")                                          # 5-run medians, current HEAD (not hardcoded)
    def stage(q, s):
        return next(float(x["median_ms"]) for x in g
                    if x["system"] == "ours" and x["query"] == q and x["stage"] == s)
    qs = [("WatDiv S-star\n(2 ans)", "watdiv-Sstar"),
          ("TPC-H Q3\n(14 908 ans)", "tpch-Q3"),
          ("Wikidata WD-path\n(P279+, 16 ans)", "wikidata-WDpath")]
    labels = [a for a, _ in qs]
    construct = np.array([stage(q, "construct") for _, q in qs]) / 1000
    compile_ = np.array([stage(q, "compile") for _, q in qs]) / 1000
    wmc = np.array([stage(q, "wmc") for _, q in qs]) / 1000
    x = np.arange(len(qs))
    fig, ax = plt.subplots(figsize=(6.8, 4.4))
    ax.bar(x, construct, 0.55, color=OURS, label="construct (engine CONSTRUCT + RDF parse)")
    ax.bar(x, compile_, 0.55, bottom=construct, color="#ff7f0e", label="compile (variable ordering + ROBDD build)")
    ax.bar(x, wmc, 0.55, bottom=construct + compile_, color=ALT, label="WMC (weighted count)")
    for i, (_, q) in enumerate(qs):
        ax.text(i, construct[i] + compile_[i] + wmc[i] + 0.12, f"WMC={stage(q,'wmc'):.0f} ms",
                ha="center", fontsize=8.5, color=ALT)
    ax.set_xticks(x); ax.set_xticklabels(labels, fontsize=9)
    ax.set_ylim(0, max(construct + compile_ + wmc) * 1.18)
    ax.set_ylabel("end-to-end PQE latency  (s, 5-run median)")
    ax.set_title("G3 — end-to-end PQE breakdown (g4_rigor 5-run):\nWMC ≤ 36 ms in all three; TPC-H Q3 is dominated by the current pure-Python variable ordering")
    ax.legend(loc="upper right", fontsize=9)
    save(fig, "fig6_G3_pqe_breakdown.png")


# ---- Fig 7: G6 — correctness on REAL circuits: d4 == OBDD == PWE, 26/26
def fig_correctness():
    # A residual TABLE, not a scatter: the 8 Q3 answers all sit at p=0.125 (overlap), and a scatter can't
    # show d4 vs PWE — the table reports max |method − ground-truth| per workload, which IS the claim.
    r = rd("g6_d4.csv")
    fams = ["watdiv-Sstar", "tpch-Q3", "wikidata-WDpath"]
    body, tot_n, tot_o, tot_d = [], 0, 0.0, 0.0
    for f in fams:
        pts = [x for x in r if x["query"] == f]
        mo = max(abs(float(x["obdd_wmc"]) - float(x["pwe"])) for x in pts)
        md = max(abs(float(x["d4_wmc"]) - float(x["pwe"])) for x in pts)
        body.append([f, str(len(pts)), f"{mo:.0e}", f"{md:.0e}"])
        tot_n += len(pts); tot_o = max(tot_o, mo); tot_d = max(tot_d, md)
    body.append(["all", str(tot_n), f"{tot_o:.0e}", f"{tot_d:.0e}"])
    col = ["workload", "# circuits", "max|OBDD−PWE|", "max|d4−PWE|"]
    fig, ax = plt.subplots(figsize=(7.6, 2.9)); ax.axis("off")
    t = ax.table(cellText=body, colLabels=col, loc="center", cellLoc="center",
                 colWidths=[0.30, 0.16, 0.27, 0.27])
    t.auto_set_font_size(False); t.set_fontsize(10.5); t.scale(1, 1.7)
    for j in range(len(col)):
        t[0, j].set_facecolor("#e8eef5"); t[0, j].set_text_props(fontweight="bold")
        t[len(body), j].set_text_props(fontweight="bold")
    ax.set_title("G6 — exact on 26 sampled answer circuits (incl. all 16 property paths):\n"
                 "OBDD = PWE = d4, three independent methods, max error 0", pad=16)
    save(fig, "fig7_G6_correctness.png")


if __name__ == "__main__":
    fig_e4_bounded(); fig_e4_growing(); fig_e2_compactness(); fig_e11_shared()
    fig_provsql(); fig_pqe_breakdown(); fig_correctness()
    print("\nAll figures written to", os.path.relpath(OUT, HERE) + "/")
