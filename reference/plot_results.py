"""Publication-style figures for the SPARQL_circ evaluation (E2/E3/E4/E7), in the idiom of
the provenance-DB papers (SPARQLprov, NPCS, ProvSQL): grouped bars + log-scale scaling curves.

Reads the CSVs written by the overnight run and writes PNG (view) + PDF (paper) into
reference/watdiv/figures/. Colour = Okabe-Ito (colourblind-safe); identity is carried by
marker + linestyle too, never colour alone. Baselines/others = orange, ours = blue.
Run from reference/ with the sparqlcirc env active (matplotlib + numpy)."""
import os, csv
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
W = os.path.join(HERE, "watdiv")
FIG = os.path.join(W, "figures"); os.makedirs(FIG, exist_ok=True)

# Okabe-Ito
OURS = "#0072B2"      # blue  - SPARQL_circ (shared circuit / d-DNNF)
BASE = "#E69F00"      # orange- baseline (per-answer strings / OBDD / ProvSQL)
ALT  = "#009E73"      # green
RED  = "#D55E00"      # vermillion
GREY = "#666666"
plt.rcParams.update({
    "font.size": 12, "axes.titlesize": 13, "axes.labelsize": 12, "legend.fontsize": 11,
    "axes.spines.top": False, "axes.spines.right": False, "figure.dpi": 120,
    "axes.grid": True, "grid.alpha": 0.25, "grid.linewidth": 0.7, "savefig.bbox": "tight",
})

def num(x):
    try: return float(x)
    except (TypeError, ValueError): return None

def read(fn):
    p = os.path.join(W, fn) if not os.path.isabs(fn) else fn
    if not os.path.exists(p): return []
    return list(csv.DictReader(open(p)))

def save(fig, name):
    for ext in ("png", "pdf"):
        fig.savefig(os.path.join(FIG, f"{name}.{ext}"), dpi=200)
    plt.close(fig); print(f"  wrote figures/{name}.png/.pdf")

# ---------------------------------------------------------------- E2 compactness
def fig_compactness():
    rows = read(os.path.join(HERE, "bench.csv"))
    if not rows: return
    rows = [r for r in rows if num(r["derivations"])]
    rows.sort(key=lambda r: num(r["derivations"]))
    names = [r["instance"] for r in rows]
    Tstr = [num(r["T_string"]) for r in rows]
    Tcirc = [num(r["T_circuit"]) for r in rows]
    x = np.arange(len(names)); w = 0.4
    fig, ax = plt.subplots(figsize=(8.6, 4.4))
    ax.bar(x - w/2, Tstr, w, color=BASE, label="per-answer strings (NPCS / SPARQLprov)", zorder=3)
    ax.bar(x + w/2, Tcirc, w, color=OURS, label="shared circuit (SPARQL_circ)", zorder=3)
    ax.set_yscale("log")
    for i, r in enumerate(rows):                          # direct-label the sharing ratio
        ratio = num(r["sharing"])
        ax.annotate(f"{ratio:g}×", (i, max(Tstr[i], Tcirc[i])), textcoords="offset points",
                    xytext=(0, 4), ha="center", fontsize=9,
                    color=(RED if ratio >= 2 else GREY), fontweight=("bold" if ratio >= 10 else "normal"))
    ax.set_xticks(x); ax.set_xticklabels(names, rotation=30, ha="right", fontsize=9)
    ax.set_ylabel("provenance size (token occurrences)")
    ax.set_title("E2  Compactness: shared circuit vs. per-answer strings\n"
                 "(× = string/circuit sharing ratio; deep queries → orders of magnitude)")
    ax.legend(frameon=False, loc="upper left")
    save(fig, "E2_compactness")

# ---------------------------------------------------------------- E4 compile vs treewidth
def fig_compile():
    rows = read("e4_results.csv")
    if not rows: return
    def series(fam, xkey):
        rs = [r for r in rows if r["family"] == fam]
        rs.sort(key=lambda r: num(r[xkey]))
        return rs
    fig, (axA, axB) = plt.subplots(1, 2, figsize=(11.6, 4.6))

    # Panel A: bounded tw=2, growing n -> d-DNNF stays small where OBDD explodes/walls
    b = series("bounded_tw2", "n_tokens")
    xn = [num(r["n_tokens"]) for r in b]
    obdd = [num(r["obdd_size"]) for r in b]
    ddnnf = [num(r["ddnnf_nodes"]) for r in b]
    xo = [x for x, y in zip(xn, obdd) if y]; yo = [y for y in obdd if y]
    xd = [x for x, y in zip(xn, ddnnf) if y]; yd = [y for y in ddnnf if y]
    axA.plot(xo, yo, "o-", color=BASE, lw=2, ms=7, label="OBDD (ours, variable order)", zorder=3)
    axA.plot(xd, yd, "s--", color=OURS, lw=2, ms=7, label="d-DNNF (d4)", zorder=3)
    wall = [num(r["n_tokens"]) for r in b if r["status"] == "obdd-timeout"]
    if wall and yo:
        axA.scatter(wall, [max(yo) * 1.6] * len(wall), marker="x", s=90, color=RED, zorder=4,
                    label="OBDD did not compile (>cap)")
    axA.set_xscale("log"); axA.set_yscale("log")
    axA.set_xlabel("n = #tokens (lineage size)"); axA.set_ylabel("compiled size (nodes)")
    axA.set_title("(a) bounded treewidth (tw=2)\nd-DNNF stays small; OBDD explodes then walls")
    axA.legend(frameon=False, fontsize=9.5, loc="upper left")

    # Panel B: growing tw (layered, depth=4) -> both blow up exponentially
    g = series("growing_tw_layer", "tw")
    xt = [num(r["tw"]) for r in g]
    axB.plot(xt, [num(r["obdd_size"]) for r in g], "o-", color=BASE, lw=2, ms=7, label="OBDD (ours)", zorder=3)
    axB.plot(xt, [num(r["ddnnf_nodes"]) for r in g], "s--", color=OURS, lw=2, ms=7, label="d-DNNF (d4)", zorder=3)
    axB.set_yscale("log")
    axB.set_xlabel("treewidth tw"); axB.set_ylabel("compiled size (nodes)")
    axB.set_title("(b) growing treewidth (layered, depth 4)\nboth blow up 2^Θ(tw) — the #P wall")
    axB.legend(frameon=False, fontsize=9.5, loc="upper left")
    fig.suptitle("E4  Knowledge compilation vs. treewidth  (all d4-WMC == our OBDD == exact)", y=1.02)
    save(fig, "E4_compile_vs_treewidth")

# ---------------------------------------------------------------- E3 construction scaling
def fig_construction():
    def pts(fn):
        return [(num(r["deriv"]), num(r["build_ms"])) for r in read(fn)
                if r.get("status") == "ok" and num(r.get("deriv")) and num(r.get("build_ms"))]
    groups = [("10M, bound (selective)", "e3_10M.csv", OURS, "o", True),
              ("100M, bound (selective)", "e3_100M.csv", RED, "D", True),
              ("10M, unbound (full query)", "e3_10M_unbound.csv", BASE, "s", False)]
    fig, ax = plt.subplots(figsize=(7.8, 5.2))
    allx = []
    for label, fn, col, mk, filled in groups:
        p = pts(fn)
        if not p: continue
        xs, ys = zip(*p); allx += list(xs)
        ax.scatter(xs, ys, s=90, marker=mk, facecolors=(col if filled else "none"),
                   edgecolors=col, linewidths=1.8, label=label, zorder=3)
    if allx:                                              # slope-1 (linear) guide
        lo, hi = min(allx), max(allx)
        gx = np.array([lo, hi]); k = 0.6
        ax.plot(gx, k * gx, ":", color=GREY, lw=1.5, label="linear (slope 1) guide", zorder=1)
    ax.set_xscale("log"); ax.set_yscale("log")
    ax.set_xlabel("#derivations materialised by the engine"); ax.set_ylabel("circuit build time (ms)")
    ax.set_title("E3  Construction scaling on a stock engine (GraphDB)\n"
                 "build time ∝ #derivations across 5 orders of magnitude (10M → 100M)")
    ax.legend(frameon=False, loc="upper left", fontsize=10)
    save(fig, "E3_construction_scaling")

# ---------------------------------------------------------------- E7 vs ProvSQL
def fig_provsql():
    """E7 is a *deployability* result, not a speed race (EVALUATION.md: 'do NOT claim we count
    faster' -- and the two timings are not comparable: ProvSQL builds+compiles in-DB, our client
    compiles a tiny circuit). So we show the measured fact -- EXACT probability parity -- and the
    qualitative axis, as a comparison matrix in the idiom of the ProvSQL/SPARQLprov feature tables."""
    rows = read("e7_results.csv")
    if not rows: return
    n = len(rows); nmatch = sum(1 for r in rows if str(r.get("match")).lower() == "true")
    dmax = max((num(r.get("max_prob_diff")) or 0.0) for r in rows)
    criteria = [                                          # (label, ProvSQL, ours)
        ("Exact answer probability (PQE)",                 True,  True),
        ("Probability == the other system (measured)",     True,  True),
        ("Knowledge compilation (circuit → d-DNNF)",       True,  True),
        ("Non-monotone OPTIONAL / MINUS",                  True,  True),
        ("Native RDF / SPARQL (no relational remodelling)",False, True),
        ("Runs on an UNMODIFIED engine",                   False, True),
    ]
    fig, ax = plt.subplots(figsize=(10.2, 5.0)); ax.axis("off")
    ax.set_xlim(0, 1); ax.set_ylim(0, 1)
    ax.text(0.5, 0.98, "E7  Head-to-head vs. ProvSQL — exact probabilistic query evaluation",
            ha="center", va="top", fontsize=13, fontweight="bold")
    ax.text(0.5, 0.905, f"all {nmatch}/{n} instances: SPARQL_circ probability == ProvSQL, "
            f"exact to float precision (max |Δp| = {dmax:.0e}).  The difference is deployability:",
            ha="center", va="top", fontsize=10.5, color=ALT)
    cx1, cx2, lx = 0.62, 0.83, 0.03
    ytop = 0.80; rh = 0.108
    ax.text(cx1, ytop + 0.045, "ProvSQL", ha="center", fontsize=11.5, fontweight="bold", color=BASE)
    ax.text(cx2, ytop + 0.045, "SPARQL_circ", ha="center", fontsize=11.5, fontweight="bold", color=OURS)
    ax.plot([lx, 0.9], [ytop + 0.02, ytop + 0.02], color=GREY, lw=1)
    for i, (label, a, b) in enumerate(criteria):
        y = ytop - i * rh
        if i % 2 == 0:
            ax.add_patch(plt.Rectangle((lx - 0.01, y - rh/2), 0.90, rh, color="#000000", alpha=0.035, lw=0))
        ax.text(lx, y, label, ha="left", va="center", fontsize=11)
        for cx, val in ((cx1, a), (cx2, b)):
            ax.text(cx, y, "✓" if val else "✗", ha="center", va="center", fontsize=16,
                    color=(ALT if val else RED), fontweight="bold")
    ax.text(0.5, 0.02, "Timings (both sub-10 ms on these instances) are NOT a fair speed comparison — "
            "ProvSQL builds+compiles in-DB, our client compiles the returned circuit — so E7 is framed "
            "as parity + deployability, per the pre-registration.",
            ha="center", va="bottom", fontsize=8.5, color=GREY, style="italic", wrap=True)
    save(fig, "E7_vs_provsql")

if __name__ == "__main__":
    print("generating figures ->", FIG)
    fig_compactness(); fig_compile(); fig_construction(); fig_provsql()
    print("done.")
