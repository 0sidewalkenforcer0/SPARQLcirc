"""Real-data paper figures in the ROUND-9 *drafts* structure.

Unlike the composite ``make_figures.py`` figures, these reproduce the STRUCTURE of
``make_round9_drafts.py`` (per-experiment, full-matrix layouts in the SPARQLprov/NPCS
idiom) and fill each panel from a committed ``reference/`` CSV.  Panels whose data is
still pending the ROUND-9 server run keep the drafts' geometry and show a
``DATA PENDING`` mark, so the figure already *carries* the complete-result layout.

Rendered through ``figstyle`` (identical grammar to the drafts).  Output -> figures/final/.

    cd presentation && python3 make_result_figures.py
"""

import csv
import os

import numpy as np

import figstyle as fs
from figstyle import GRAY, SP_CIRCUIT, SP_NPCS, plt

HERE = os.path.dirname(os.path.abspath(__file__))
REF = os.path.join(HERE, "..", "reference")
OUT = os.path.join(HERE, "figures", "final")
CREATOR = "sparqlcirc/presentation/make_result_figures.py"


def rd(rel):
    with open(os.path.join(REF, rel), encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------- R9.4 knowledge compilation
def fig_compilation_scale():
    """Drafts r9_4 structure (2x3: latency / size / memory x fixed / growing tw), real E4.

    OBDD vs d-DNNF(d4) compiled SIZE is fully real; OBDD compile LATENCY (secs, 120 s cap)
    is real; compiler peak RSS is not in e4_results.csv -> those two panels stay PENDING.
    """
    rows = rd("watdiv/e4_results.csv")
    fixed = sorted((r for r in rows if r["family"] == "bounded_tw2"), key=lambda r: int(r["n_tokens"]))
    growing = sorted((r for r in rows if r["family"] == "growing_tw_layer"), key=lambda r: int(r["tw"]))

    fig, axes = plt.subplots(2, 3, figsize=(fs.FIG_WIDTH, 4.15))

    def latency_panel(ax, data, xs, xlabel, title, logx):
        ok = [(x, float(r["secs"])) for x, r in zip(xs, data) if r["status"] != "obdd-timeout"]
        to = [(x, 120.0) for x, r in zip(xs, data) if r["status"] == "obdd-timeout"]
        if ok:
            ax.plot([p[0] for p in ok], [p[1] for p in ok], color=SP_CIRCUIT, marker="s",
                    linestyle="--", label="fixed-order OBDD")
        if to:
            ax.scatter([p[0] for p in to], [p[1] for p in to], color=SP_CIRCUIT, marker="v",
                       s=26, zorder=4, label="OBDD timeout (120 s)")
        ax.axhline(120.0, color="#555555", linestyle=fs.TIMEOUT_LS, linewidth=0.8)
        fs.light_log_axis(ax, xlabel, "OBDD compile time (s)", title, logx=logx)

    def size_panel(ax, data, xs, xlabel, title, logx):
        obdd = [(x, int(r["obdd_size"])) for x, r in zip(xs, data) if r["obdd_size"]]
        dd = [(x, int(r["ddnnf_nodes"])) for x, r in zip(xs, data) if r["ddnnf_nodes"]]
        ax.plot([p[0] for p in obdd], [p[1] for p in obdd], color=SP_CIRCUIT, marker="s",
                linestyle="--", label="fixed-order OBDD")
        ax.plot([p[0] for p in dd], [p[1] for p in dd], color=SP_NPCS, marker="o", label="d-DNNF (d4)")
        fs.light_log_axis(ax, xlabel, "Compiled nodes", title, logx=logx)

    def memory_pending(ax, xlabel, title, logx):
        fs.light_log_axis(ax, xlabel, "Compiler peak RSS (MiB)", title, logx=logx)
        fs.pending(ax, "RSS\nDATA PENDING", y=0.6)

    fx = [int(r["n_tokens"]) for r in fixed]
    gx = [int(r["tw"]) for r in growing]
    latency_panel(axes[0, 0], fixed, fx, "Input circuit size (tokens)", "Fixed treewidth: latency", True)
    size_panel(axes[0, 1], fixed, fx, "Input circuit size (tokens)", "Fixed treewidth: size", True)
    memory_pending(axes[0, 2], "Input circuit size (tokens)", "Fixed treewidth: memory", True)
    latency_panel(axes[1, 0], growing, gx, "Treewidth", "Growing treewidth: latency", False)
    size_panel(axes[1, 1], growing, gx, "Treewidth", "Growing treewidth: size", False)
    memory_pending(axes[1, 2], "Treewidth", "Growing treewidth: memory", False)
    for ax in (axes[1, 0], axes[1, 1], axes[1, 2]):
        ax.set_xticks(gx)

    for i, ax in enumerate(axes.ravel()):
        fs.panel_label(ax, i, x=0.015, y=0.985)
    handles, labels = axes[0, 1].get_legend_handles_labels()
    handles.append(fs.timeout_handle("120 s timeout"))
    labels.append("120 s timeout")
    fs.top_legend(fig, handles, labels, ncol=3, y=1.005)
    fs.footer(fig, "d-DNNF stays polynomial at fixed treewidth (OBDD walls at 120 s); both blow up as treewidth grows. "
                   "Compiler RSS awaits the memory-only runs.")
    fig.subplots_adjust(left=0.085, right=0.995, bottom=0.14, top=0.91, hspace=0.47, wspace=0.36)
    fs.save(fig, "result_r9_4_compilation_scale", OUT, creator=CREATOR)


# ------------------------------------------------------- R9.3b sharing crossover
def fig_sharing_crossover():
    """Drafts r9_3b structure (1x2: representation crossover + shared compilation), real E2/E11."""
    bench = rd("bench.csv")
    layered = sorted((r for r in bench if r["instance"].startswith("layered")),
                     key=lambda r: int(r["derivations"]))
    deep = sorted((r for r in bench if r["instance"].startswith("deep")),
                  key=lambda r: int(r["derivations"]))
    scale = sorted(rd("e11_scale.csv"), key=lambda r: int(r["N"]))

    fig, axes = plt.subplots(1, 2, figsize=(fs.FIG_WIDTH, 2.65))

    ax = axes[0]
    ax.plot([int(r["derivations"]) for r in layered], [float(r["sharing"]) for r in layered],
            color=SP_NPCS, marker="o", label="layered")
    ax.plot([int(r["derivations"]) for r in deep], [float(r["sharing"]) for r in deep],
            color=SP_CIRCUIT, marker="s", linestyle="--", label="deep")
    ax.axhline(1.0, color=GRAY, linestyle=fs.TIMEOUT_LS, linewidth=0.9)
    if deep:
        best = max(deep, key=lambda r: float(r["sharing"]))
        ax.annotate(f"{float(best['sharing']):.0f}×",
                    (int(best["derivations"]), float(best["sharing"])),
                    xytext=(-4, 6), textcoords="offset points", ha="right",
                    color=SP_CIRCUIT, fontweight="bold")
    fs.light_log_axis(ax, "Derivations / repeated subterms", "Per-answer / shared size",
                      "Sharing crossover")
    ax.legend(frameon=False, loc="upper left")
    fs.panel_label(ax, 0, x=-0.18)

    ax = axes[1]
    ax.plot([int(r["N"]) for r in scale], [float(r["shared_ms"]) for r in scale],
            color=SP_NPCS, marker="o", label="shared compile once")
    ax.plot([int(r["N"]) for r in scale], [float(r["perans_ms"]) for r in scale],
            color=SP_CIRCUIT, marker="s", linestyle="--", label="per-answer completion")
    if scale:
        last = scale[-1]
        ax.annotate(f"{float(last['time_win']):.1f}×", (int(last["N"]), float(last["perans_ms"])),
                    xytext=(-3, 6), textcoords="offset points", ha="right",
                    color=SP_CIRCUIT, fontweight="bold")
    fs.light_log_axis(ax, "Answers", "Compile + WMC (ms)", "One shared pass vs per answer")
    ax.legend(frameon=False, loc="upper left")
    fs.panel_label(ax, 1, x=-0.18)

    fs.footer(fig, "Engine-independent controlled families; real-query size ratios are on the per-engine storage pages.")
    fig.subplots_adjust(left=0.085, right=0.995, bottom=0.20, top=0.90, wspace=0.29)
    fs.save(fig, "result_r9_3b_sharing_crossover", OUT, creator=CREATOR)


# ------------------------------------------------------- R9.7 ProvSQL / TPC-H
def fig_provsql_tpch():
    """Drafts r9_7 structure (1x2: matched cells + scale trend), real G4.

    The five TPC-H Q3 segments (ours vs ProvSQL, 5-run median +/- sd) are real; a clean
    full-PQE scale-factor sweep is not committed (g2a mixes full-PQE and construct-only
    points), so the trend panel stays PENDING.
    """
    g4 = [r for r in rd("g4_instances.csv") if r["shape"] == "tpch-Q3-SPJ"]
    fig, axes = plt.subplots(1, 2, figsize=(fs.FIG_WIDTH, 2.55))

    ax = axes[0]
    x = np.arange(len(g4))
    ours = np.array([float(r["ours_median_ms"]) / 1000 for r in g4])
    prov = np.array([float(r["provsql_median_ms"]) / 1000 for r in g4])
    ours_sd = np.array([float(r["ours_sd_ms"]) / 1000 for r in g4])
    prov_sd = np.array([float(r["provsql_sd_ms"]) / 1000 for r in g4])
    fs.grouped_bars(ax, x, [ours, prov], [SP_CIRCUIT, SP_NPCS], ["SPARQLcirc", "ProvSQL"],
                    log=False, yerr=[ours_sd, prov_sd])
    ax.set_xticks(x, ["Auto.", "Build.", "Furn.", "House.", "Mach."])
    ax.set_ylim(0, float(max(prov + prov_sd)) * 1.15)
    ax.set_xlabel("TPC-H Q3 market segment")
    ax.set_ylabel("End-to-end PQE (s)")
    ax.set_title("Matched Q3 segments", pad=4)
    ax.legend(frameon=False, loc="upper left", ncol=1)
    fs.panel_label(ax, 0, x=-0.18)

    ax = axes[1]
    fs.light_log_axis(ax, "TPC-H scale factor", "End-to-end PQE (ms)", "Matched scale trend")
    ax.set_xlim(0.008, 1.2)
    ax.set_ylim(100, 2e4)
    fs.pending(ax, "SCALE SWEEP\nDATA PENDING", y=0.6)
    fs.panel_label(ax, 1, x=-0.18)

    fs.footer(fig, "Probability parity (ours == ProvSQL, exact) is a LaTeX table; sf 0.01 is matched full-PQE, full sweep pending.")
    fig.subplots_adjust(left=0.085, right=0.995, bottom=0.21, top=0.86, wspace=0.29)
    fs.save(fig, "result_r9_7_provsql_tpch", OUT, creator=CREATOR)


# ------------------------------------------------------- R9.4b compilation over real classes
def fig_compilation_patterns():
    """Drafts r9_4b structure (1x2: latency + size over real query classes), real G3.

    d-DNNF (d4) compile time and compiled size for the three real workloads are committed;
    per-class OBDD is not, so each panel notes d4-only.
    """
    g3 = rd("g3_pqe.csv")
    order = ["watdiv-Sstar", "tpch-Q3", "wikidata-WDpath"]
    labels = ["S-star", "TPC-H Q3", "P279+"]
    by = {r["query"]: r for r in g3}
    x = np.arange(len(order))
    fig, axes = plt.subplots(1, 2, figsize=(fs.FIG_WIDTH, 2.55))

    ax = axes[0]
    fs.grouped_bars(ax, x, [[float(by[q]["compile_ms"]) for q in order]], [SP_NPCS],
                    ["d-DNNF (d4)"], log=True)
    ax.set_xticks(x, labels)
    ax.set_xlabel("Query class")
    ax.set_ylabel("Compile time (ms)")
    ax.set_title("Real-class compile latency", pad=4)
    ax.legend(frameon=False, loc="upper left")
    fs.panel_label(ax, 0, x=-0.16)

    ax = axes[1]
    fs.grouped_bars(ax, x, [[int(by[q]["compiled"]) for q in order]], [SP_NPCS],
                    ["d-DNNF (d4)"], log=True)
    ax.set_xticks(x, labels)
    ax.set_xlabel("Query class")
    ax.set_ylabel("Compiled nodes")
    ax.set_title("Real-class compiled size", pad=4)
    fs.panel_label(ax, 1, x=-0.16)

    fs.footer(fig, "Client-side compilation on real query classes (d4); each instance appears once. Per-class OBDD is pending.")
    fig.subplots_adjust(left=0.085, right=0.995, bottom=0.21, top=0.86, wspace=0.29)
    fs.save(fig, "result_r9_4b_compilation_patterns", OUT, creator=CREATOR)


# ------------------------------------------------------- R9.3 storage ratio (counterexamples)
def fig_storage_ratio():
    """Drafts r9_3 structure (NPCS/SPARQLcirc size ratio), real G2b.

    Structural and serialized-byte ratios on the three measured low-sharing queries.
    Ratio < 1 means SPARQLcirc is *larger* -- the honest counterexample to r9_3b's wins.
    Per-engine, per-template ratios need the server matrix (kept as the r9_3 drafts).
    """
    g2b = rd("g2b_npcs_vs_ours.csv")
    order = ["S-star", "P2-path", "P2-unbound"]
    labels = ["S-star", "P2 bound", "P2 all"]
    by = {r["query"]: r for r in g2b}
    x = np.arange(len(order))
    structural = [float(by[q]["struct_T_string"]) / float(by[q]["struct_T_circ"]) for q in order]
    byte_ratio = [float(by[q]["serial_npcs_bytes"]) / float(by[q]["serial_ours_bytes"]) for q in order]

    fig, ax = plt.subplots(figsize=(fs.FIG_WIDTH, 2.35))
    fs.grouped_bars(ax, x, [structural, byte_ratio], [SP_NPCS, SP_CIRCUIT],
                    ["structural (tokens)", "serialized (bytes)"], log=True)
    ax.axhline(1.0, color="#555555", linestyle=fs.TIMEOUT_LS, linewidth=0.8)
    ax.set_ylim(0.03, 3)
    ax.set_xticks(x, labels)
    ax.set_xlabel("Query")
    ax.set_ylabel("NPCS / SPARQLcirc size")
    ax.set_title("Representation size on low-sharing queries (ratio < 1 → SPARQLcirc larger)", pad=4)
    ax.legend(frameon=False, loc="upper left", ncol=2)  # empty band above the break-even line
    fs.footer(fig, "Honest counterexamples to the reconvergent wins (r9_3b); per-engine per-template ratios are on the pending r9_3 pages.")
    fig.subplots_adjust(left=0.085, right=0.995, bottom=0.24, top=0.86)
    fs.save(fig, "result_r9_3_storage_ratio", OUT, creator=CREATOR)


# ------------------------------------------------------- R9.2c data-scale (WatDiv 10M/100M)
def fig_data_scale():
    """Drafts r9_2c structure (1x3: time / size / RSS vs WatDiv scale), real E3.

    Construction time and circuit size at 10M and 100M for the bound S/F/L shapes are
    committed; builder peak RSS is not, so that panel stays PENDING.
    """
    e10 = {r["query"]: r for r in rd("watdiv/e3_10M.csv")}
    e100 = {r["query"]: r for r in rd("watdiv/e3_100M.csv")}
    shapes = ["S-star", "F-snow", "L-path"]
    scale = np.array([10.0, 100.0])
    fig, axes = plt.subplots(1, 3, figsize=(fs.FIG_WIDTH, 2.55))
    for idx, shape in enumerate(shapes):
        color, marker = fs.SERIES[idx], fs.SERIES_MARKERS[idx]
        build = [float(e10[shape]["build_ms"]), float(e100[shape]["build_ms"])]
        size = [int(e10[shape]["gates"]) + int(e10[shape]["edges"]),
                int(e100[shape]["gates"]) + int(e100[shape]["edges"])]
        axes[0].plot(scale, build, color=color, marker=marker, label=shape)
        axes[1].plot(scale, size, color=color, marker=marker, label=shape)
    fs.light_log_axis(axes[0], "WatDiv triples (millions)", "Construction time (ms)", "Construction")
    fs.light_log_axis(axes[1], "WatDiv triples (millions)", "Gates + edges", "Circuit growth")
    fs.light_log_axis(axes[2], "WatDiv triples (millions)", "Builder peak RSS (MiB)", "Client memory")
    fs.pending(axes[2], "RSS\nDATA PENDING", y=0.6)
    for i, ax in enumerate(axes):
        ax.set_xticks(scale, ["10M", "100M"])
        fs.panel_label(ax, i, x=-0.22)
    fs.suptitle(fig, "GraphDB — data-scale construction (bound shapes)", y=0.99, fontsize=9.0)
    handles, labels = axes[0].get_legend_handles_labels()
    fs.top_legend(fig, handles, labels, ncol=3, y=0.91)
    fs.footer(fig, "GraphDB, bound S/F/L at 10M and 100M; 1B and per-engine points join when the server run lands.")
    fig.subplots_adjust(left=0.085, right=0.995, bottom=0.20, top=0.75, wspace=0.34)
    fs.save(fig, "result_r9_2c_data_scale", OUT, creator=CREATOR)


# ------------------------------------------------------- R9.6 property paths
def fig_paths():
    """Drafts r9_6 structure (1x3: construct / circuit / RSS), real E-paths.

    Four path operators at reach 80 (categorical, not a reach sweep -> bars). Builder RSS
    and the reach-scale sweep are not committed, so those stay PENDING.
    """
    rows = [r for r in rd("watdiv/e_paths.csv") if r["status"] == "ok"]
    labels = [r["query"] for r in rows]
    x = np.arange(len(rows))
    build = [float(r["build_ms"]) for r in rows]
    size = [int(r["gates"]) + int(r["edges"]) for r in rows]
    fig, axes = plt.subplots(1, 3, figsize=(fs.FIG_WIDTH, 2.55))
    fs.grouped_bars(axes[0], x, [build], [SP_CIRCUIT], ["SPARQLcirc"], log=True)
    axes[0].set_ylabel("Construction time (ms)")
    axes[0].set_title("Iterative construction", pad=4)
    fs.grouped_bars(axes[1], x, [size], [SP_CIRCUIT], ["SPARQLcirc"], log=True)
    axes[1].set_ylabel("Gates + edges")
    axes[1].set_title("Circuit size", pad=4)
    fs.light_log_axis(axes[2], "Reachable nodes", "Builder peak RSS (MiB)", "Client memory")
    fs.pending(axes[2], "RSS + REACH SWEEP\nDATA PENDING", y=0.6)
    for i, ax in enumerate(axes):
        if i < 2:
            ax.set_xticks(x, labels, rotation=25)
            ax.set_xlabel("Path operator")
        fs.panel_label(ax, i, x=-0.22)
    fs.suptitle(fig, "GraphDB — property-path circuits (reach 80)", y=0.99, fontsize=9.0)
    fs.footer(fig, "Four single-predicate path operators at fixed reach; the reach-scale sweep and RSS join with the server run.")
    fig.subplots_adjust(left=0.085, right=0.995, bottom=0.24, top=0.80, wspace=0.34)
    fs.save(fig, "result_r9_6_paths", OUT, creator=CREATOR)


if __name__ == "__main__":
    # Engine-independent / cross-system figures live here.
    fig_compilation_scale()      # r9_4  (treewidth families, client-side)
    fig_sharing_crossover()      # r9_3b (controlled sharing families)
    fig_provsql_tpch()           # r9_7  (TPC-H vs ProvSQL)
    fig_compilation_patterns()   # r9_4b (compile over real query classes)
    fig_paths()                  # r9_6  (property-path operators; until a full-dim path run)
    # NOTE: storage (r9_3) and data-scale (r9_2c) are now full-dimension per-engine and
    # generated from the B/R/N/C matrix in make_matrix_figures.py (superseding the earlier
    # collapsed 3-query / 3-shape versions here).
    print(f"\nResult figures (drafts structure, real data) written to {os.path.relpath(OUT, HERE)}/.")
