"""Generate the paper figures from the committed experiment CSVs.

The plots follow the visual grammar used by the SPARQLprov, NPCS, and ProvSQL
evaluations: one experimental question per figure, small multiples for changing
conditions, explicit timeout/failure marks, log scales for wide runtime/size
ranges, and captions kept outside the artwork.  Every plotted value comes from
a committed CSV under ``reference/``.

Outputs are written as vector PDF plus 300-dpi PNG:

    cd presentation && python3 make_figures.py
"""

import csv
import os

os.environ.setdefault("MPLCONFIGDIR", os.path.join("/tmp", "sparqlcirc-matplotlib"))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


HERE = os.path.dirname(os.path.abspath(__file__))
REF = os.path.join(HERE, "..", "reference")
OUT = os.path.join(HERE, "figures")
os.makedirs(OUT, exist_ok=True)

# Okabe-Ito: color-blind safe, with redundant marker/line/hatch encodings.
BLUE = "#0072B2"
SKY = "#56B4E9"
GREEN = "#009E73"
ORANGE = "#E69F00"
VERMILLION = "#D55E00"
PURPLE = "#CC79A7"
BLACK = "#222222"
GRAY = "#6B6B6B"
LIGHT_GRAY = "#D9D9D9"

plt.rcParams.update(
    {
        "font.family": "sans-serif",
        "font.sans-serif": ["DejaVu Sans", "Arial", "Helvetica"],
        "font.size": 7.4,
        "axes.labelsize": 8.0,
        "axes.titlesize": 8.2,
        "xtick.labelsize": 7.0,
        "ytick.labelsize": 7.0,
        "legend.fontsize": 6.8,
        "axes.linewidth": 0.7,
        "lines.linewidth": 1.25,
        "lines.markersize": 4.2,
        "grid.color": "#D8DEE5",
        "grid.linewidth": 0.45,
        "grid.alpha": 0.75,
        "axes.axisbelow": True,
        "pdf.fonttype": 42,
        "ps.fonttype": 42,
        "savefig.bbox": "tight",
        "savefig.pad_inches": 0.025,
    }
)


def rd(rel):
    with open(os.path.join(REF, rel), encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def save(fig, stem):
    """Save vector artwork for the paper and a high-resolution preview."""
    fig.savefig(
        os.path.join(OUT, f"{stem}.pdf"),
        metadata={"Creator": "sparqlcirc/presentation/make_figures.py"},
        facecolor="white",
        transparent=False,
    )
    fig.savefig(os.path.join(OUT, f"{stem}.png"), dpi=300, facecolor="white", transparent=False)
    plt.close(fig)
    print("wrote", os.path.join("figures", f"{stem}.pdf/.png"))


def style_ax(ax, grid="y"):
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.grid(True, axis=grid)
    ax.tick_params(direction="out", length=2.5, width=0.6)


def panel_label(ax, label):
    ax.text(
        -0.12,
        1.10,
        label,
        transform=ax.transAxes,
        ha="left",
        va="top",
        fontsize=9.2,
        fontweight="bold",
    )


def grouped_columns(
    ax,
    x,
    left,
    right,
    left_label,
    right_label,
    *,
    log=False,
    width=0.34,
):
    """Draw the compact, hatched system comparison used in the baseline papers."""
    bottom = 1.0 if log else 0.0
    left_height = np.asarray(left, dtype=float) - bottom
    right_height = np.asarray(right, dtype=float) - bottom
    bars_left = ax.bar(
        x - width / 2,
        left_height,
        width,
        bottom=bottom,
        color="white",
        edgecolor=GRAY,
        linewidth=0.85,
        hatch="///",
        label=left_label,
        zorder=3,
    )
    bars_right = ax.bar(
        x + width / 2,
        right_height,
        width,
        bottom=bottom,
        color=BLUE,
        edgecolor=BLACK,
        linewidth=0.65,
        hatch="...",
        label=right_label,
        zorder=3,
    )
    return bars_left, bars_right


def fig_compilation():
    """E4: compiler size at fixed and growing treewidth."""
    rows = rd("watdiv/e4_results.csv")
    bounded = [x for x in rows if x["family"] == "bounded_tw2"]
    growing = [x for x in rows if x["family"] == "growing_tw_layer"]

    fig, axes = plt.subplots(1, 2, figsize=(7.15, 2.65))

    ax = axes[0]
    all_n = np.array([int(x["n_tokens"]) for x in bounded])
    dd = np.array([int(x["ddnnf_nodes"]) for x in bounded])
    obdd_rows = [x for x in bounded if x["obdd_size"]]
    timeout_rows = [x for x in bounded if x["status"] == "obdd-timeout"]
    ax.plot(
        [int(x["n_tokens"]) for x in obdd_rows],
        [int(x["obdd_size"]) for x in obdd_rows],
        color=VERMILLION,
        marker="o",
        label="fixed-order OBDD",
    )
    ax.plot(all_n, dd, color=BLUE, marker="s", linestyle="--", label="d-DNNF (d4)")
    timeout_y = 7.2e5
    ax.scatter(
        [int(x["n_tokens"]) for x in timeout_rows],
        [timeout_y] * len(timeout_rows),
        color=VERMILLION,
        marker="v",
        s=28,
        label="OBDD timeout (120 s)",
        zorder=4,
    )
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_ylim(3, 1.05e6)
    ax.set_xlabel("Input circuit size (tokens)")
    ax.set_ylabel("Compiled size (nodes)")
    ax.set_title("Fixed treewidth (tw = 2)", pad=4)
    style_ax(ax)
    panel_label(ax, "a")

    ax = axes[1]
    tw = np.array([int(x["tw"]) for x in growing])
    ax.plot(
        tw,
        [int(x["obdd_size"]) for x in growing],
        color=VERMILLION,
        marker="o",
        label="fixed-order OBDD",
    )
    ax.plot(
        tw,
        [int(x["ddnnf_nodes"]) for x in growing],
        color=BLUE,
        marker="s",
        linestyle="--",
        label="d-DNNF (d4)",
    )
    ax.set_yscale("log")
    ax.set_xlabel("Treewidth")
    ax.set_ylabel("Compiled size (nodes)")
    ax.set_title("Growing treewidth (depth = 4)", pad=4)
    ax.set_xticks(tw)
    style_ax(ax)
    panel_label(ax, "b")

    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="upper center", ncol=3, frameon=False, bbox_to_anchor=(0.5, 1.04))
    fig.subplots_adjust(left=0.085, right=0.995, bottom=0.20, top=0.82, wspace=0.31)
    save(fig, "paper_fig1_compilation")


def fig_sharing():
    """E2/E11/G2b: the sharing regime and a direct NPCS comparison."""
    bench = rd("bench.csv")
    layered = [x for x in bench if x["instance"].startswith("layered")]
    deep = [x for x in bench if x["instance"].startswith("deep")]
    scale = rd("e11_scale.csv")
    g2b = rd("g2b_npcs_vs_ours.csv")

    fig, axes = plt.subplots(2, 2, figsize=(7.15, 4.55))
    axes = axes.ravel()

    ax = axes[0]
    ax.plot(
        [int(x["derivations"]) for x in layered],
        [float(x["sharing"]) for x in layered],
        color=BLUE,
        marker="o",
        label="layered",
    )
    ax.plot(
        [int(x["derivations"]) for x in deep],
        [float(x["sharing"]) for x in deep],
        color=GREEN,
        marker="s",
        linestyle="--",
        label="deep",
    )
    ax.axhline(1.0, color=GRAY, linestyle=":", linewidth=0.9)
    best = max(deep, key=lambda x: float(x["sharing"]))
    ax.annotate(
        f"{float(best['sharing']):.0f}×",
        (int(best["derivations"]), float(best["sharing"])),
        xytext=(-4, 6),
        textcoords="offset points",
        ha="right",
        color=GREEN,
        fontweight="bold",
    )
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_ylim(0.7, 320)
    ax.set_xlabel("Derivations")
    ax.set_ylabel("Per-answer / shared size")
    ax.set_title("Synthetic reconvergence", pad=4)
    ax.legend(frameon=False, loc="upper left")
    style_ax(ax)
    panel_label(ax, "a")

    ax = axes[1]
    n_answers = [int(x["N"]) for x in scale]
    ax.plot(
        n_answers,
        [float(x["shared_ms"]) for x in scale],
        color=BLUE,
        marker="o",
        label="shared circuit",
    )
    ax.plot(
        n_answers,
        [float(x["perans_ms"]) for x in scale],
        color=VERMILLION,
        marker="s",
        linestyle="--",
        label="per-answer completion",
    )
    last = scale[-1]
    ax.annotate(
        f"{float(last['time_win']):.1f}×",
        (int(last["N"]), float(last["perans_ms"])),
        xytext=(-3, 6),
        textcoords="offset points",
        ha="right",
        color=VERMILLION,
        fontweight="bold",
    )
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_ylim(0.3, 100)
    ax.set_xlabel("Answers")
    ax.set_ylabel("Compile + WMC (ms)")
    ax.set_title("Compile once vs per answer", pad=4)
    ax.legend(frameon=False, loc="upper left")
    style_ax(ax)
    panel_label(ax, "b")

    ax = axes[2]
    by_query = {x["query"]: x for x in g2b}
    order = ["S-star", "P2-path", "P2-unbound"]
    labels = ["S-star", "P2 bound", "P2 all"]
    x = np.arange(len(order))
    npcs_ms = [float(by_query[q]["npcs_eval_ms"]) for q in order]
    ours_ms = [float(by_query[q]["ours_eval_ms"]) for q in order]
    grouped_columns(ax, x, npcs_ms, ours_ms, "NPCS reimplementation", "SPARQLcirc", log=True)
    for i, (npcs_value, ours_value) in enumerate(zip(npcs_ms, ours_ms)):
        ax.text(
            i + 0.17,
            ours_value * 1.28,
            f"{ours_value / npcs_value:.1f}×",
            ha="center",
            va="bottom",
            fontsize=6.2,
            color=BLUE,
        )
    ax.set_yscale("log")
    ax.set_ylim(1, 1.2e5)
    ax.set_xticks(x, labels)
    ax.set_ylabel("Construction time (ms, log scale)")
    ax.set_title("NPCS-compatible construction", pad=4)
    ax.legend(frameon=False, loc="upper left", ncol=2)
    style_ax(ax)
    panel_label(ax, "c")

    ax = axes[3]
    npcs_kib = [float(by_query[q]["serial_npcs_bytes"]) / 1024.0 for q in order]
    ours_kib = [float(by_query[q]["serial_ours_bytes"]) / 1024.0 for q in order]
    grouped_columns(ax, x, npcs_kib, ours_kib, "NPCS reimplementation", "SPARQLcirc", log=True)
    for i, (npcs_value, ours_value) in enumerate(zip(npcs_kib, ours_kib)):
        ax.text(
            i + 0.17,
            ours_value * 1.28,
            f"{ours_value / npcs_value:.1f}×",
            ha="center",
            va="bottom",
            fontsize=6.2,
            color=BLUE,
        )
    ax.set_yscale("log")
    ax.set_ylim(1, 8e5)
    ax.set_xticks(x, labels)
    ax.set_ylabel("Serialized output (KiB, log scale)")
    ax.set_title("Actual result volume", pad=4)
    style_ax(ax)
    panel_label(ax, "d")

    fig.subplots_adjust(left=0.085, right=0.995, bottom=0.12, top=0.94, hspace=0.48, wspace=0.32)
    save(fig, "paper_fig2_sharing")


def fig_construction():
    """E3/E6/E8: stock-engine overhead by template, scale, and real-KG reach."""
    datasets = [
        ("WatDiv 10M", rd("watdiv/e3_10M.csv"), rd("watdiv/e6_minus_10M.csv")),
        ("WatDiv 100M", rd("watdiv/e3_100M.csv"), rd("watdiv/e6_minus_100M.csv")),
    ]
    e8 = rd("watdiv/e8_wikidata.csv")

    fig, axes = plt.subplots(1, 3, figsize=(7.15, 2.85), gridspec_kw={"width_ratios": [1.0, 1.0, 1.15]})
    query_order = ["S-star", "F-snow", "L-path", "M-minus"]
    query_labels = ["S", "F", "L", "M"]

    for panel, (title, rows, minus_rows) in enumerate(datasets):
        ax = axes[panel]
        by_query = {x["query"]: x for x in rows}
        by_query["M-minus"] = next(x for x in minus_rows if x["query"] == "M-minus")
        x = np.arange(len(query_order))
        # Legacy E3/E6 call this column plain_ms, but the harness obtains it via
        # get_npcs(): it is the NPCS provenance SELECT, not the original query.
        npcs = [float(by_query[q]["plain_ms"]) for q in query_order]
        circuit = [float(by_query[q]["build_ms"]) for q in query_order]
        grouped_columns(ax, x, npcs, circuit, "NPCS reimplementation", "circuit CONSTRUCT", log=True)
        for i, q in enumerate(query_order):
            overhead = float(by_query[q]["c_overhead"])
            ax.text(
                i + 0.17,
                circuit[i] * 1.20,
                f"{overhead:.1f}×",
                ha="center",
                va="bottom",
                fontsize=6.2,
                color=BLUE,
            )
        ax.set_yscale("log")
        ax.set_ylim(1, 2000)
        ax.set_xticks(x, query_labels)
        ax.set_title(title, pad=4)
        ax.set_xlabel("Query template")
        if panel == 0:
            ax.set_ylabel("Execution time (ms, log scale)")
        style_ax(ax)
        panel_label(ax, chr(ord("a") + panel))

    ax = axes[2]
    ok = [x for x in e8 if x["status"] == "ok" and int(x["deriv"]) > 0]
    ax.scatter(
        [int(x["deriv"]) for x in ok],
        [float(x["build_ms"]) / 1000.0 for x in ok],
        facecolors="white",
        edgecolors=BLUE,
        linewidths=0.9,
        s=25,
        marker="o",
    )
    status_counts = {}
    for row in e8:
        status_counts[row["status"]] = status_counts.get(row["status"], 0) + 1
    ax.text(
        0.03,
        0.97,
        f"{status_counts.get('ok', 0)}/{len(e8)} completed\n{status_counts.get('too-large', 0)} too-large; "
        f"{status_counts.get('err:MemoryError', 0)} OOM",
        transform=ax.transAxes,
        ha="left",
        va="top",
        fontsize=6.7,
        color=BLACK,
    )
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlabel("Derivations")
    ax.set_ylabel("Circuit construction (s)")
    ax.set_title("Wikidata 2.13B", pad=4)
    style_ax(ax)
    panel_label(ax, "c")

    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="upper center", ncol=2, frameon=False, bbox_to_anchor=(0.35, 1.01))
    fig.subplots_adjust(left=0.075, right=0.995, bottom=0.19, top=0.79, wspace=0.38)
    save(fig, "paper_fig3_construction")


def fig_pqe():
    """G4/G3: direct ProvSQL comparison and an overhead decomposition."""
    instances = [x for x in rd("g4_instances.csv") if x["shape"] == "tpch-Q3-SPJ"]
    rigor = rd("g4_rigor.csv")

    fig, axes = plt.subplots(1, 2, figsize=(7.15, 2.75), gridspec_kw={"width_ratios": [1.08, 1.0]})

    ax = axes[0]
    x = np.arange(len(instances))
    ours = np.array([float(row["ours_median_ms"]) / 1000 for row in instances])
    prov = np.array([float(row["provsql_median_ms"]) / 1000 for row in instances])
    ours_sd = np.array([float(row["ours_sd_ms"]) / 1000 for row in instances])
    prov_sd = np.array([float(row["provsql_sd_ms"]) / 1000 for row in instances])
    width = 0.36
    ax.bar(
        x - width / 2,
        ours,
        width,
        yerr=ours_sd,
        capsize=2.3,
        color=BLUE,
        edgecolor=BLACK,
        linewidth=0.65,
        hatch="...",
        label="SPARQLcirc",
        zorder=3,
    )
    ax.bar(
        x + width / 2,
        prov,
        width,
        yerr=prov_sd,
        capsize=2.3,
        color="white",
        edgecolor=VERMILLION,
        linewidth=1.0,
        hatch="///",
        label="ProvSQL",
        zorder=3,
    )
    short_labels = ["Auto.", "Build.", "Furn.", "House.", "Mach."]
    ax.set_xticks(x, short_labels)
    ax.set_xlabel("TPC-H Q3 market segment")
    ax.set_ylabel("End-to-end PQE (s)")
    ax.set_ylim(0, max(prov + prov_sd) * 1.15)
    ax.set_title("Direct system comparison", pad=4)
    ax.legend(frameon=False, loc="upper right", ncol=2)
    style_ax(ax)
    panel_label(ax, "a")

    def row(query, stage):
        return next(x for x in rigor if x["system"] == "ours" and x["query"] == query and x["stage"] == stage)

    ax = axes[1]
    workloads = [("S-star", "watdiv-Sstar"), ("Q3", "tpch-Q3"), ("P279+", "wikidata-WDpath")]
    stages = [("construct", "construct", BLUE), ("compile", "compile", ORANGE), ("wmc", "WMC", GREEN)]
    values = {
        stage: np.array([float(row(q, stage)["median_ms"]) for _, q in workloads])
        for stage, _, _ in stages
    }
    stage_sum = sum(values.values())
    left = np.zeros(len(workloads))
    y = np.arange(len(workloads))
    for stage, display, color in stages:
        fraction = values[stage] / stage_sum
        ax.barh(
            y,
            fraction,
            left=left,
            height=0.52,
            color=color,
            edgecolor="white",
            linewidth=0.55,
            label=display,
            zorder=3,
        )
        left += fraction
    totals = [float(row(q, "total")["median_ms"]) for _, q in workloads]
    for yi, total in enumerate(totals):
        display = f"{total / 1000:.2f} s" if total >= 1000 else f"{total:.1f} ms"
        ax.text(1.025, yi, display, ha="left", va="center", fontsize=6.7, fontweight="bold")
    q3_y = 1
    construct_mid = (values["construct"][q3_y] / stage_sum[q3_y]) / 2
    compile_left = values["construct"][q3_y] / stage_sum[q3_y]
    compile_mid = compile_left + (values["compile"][q3_y] / stage_sum[q3_y]) / 2
    ax.text(construct_mid, q3_y, "construct", ha="center", va="center", color="white", fontsize=6.8)
    ax.text(compile_mid, q3_y, "compile", ha="center", va="center", color=BLACK, fontsize=6.8)
    ax.set_xlim(0, 1.24)
    ax.set_xticks([0, 0.25, 0.5, 0.75, 1.0], ["0", "25", "50", "75", "100"])
    ax.set_yticks(y, [x[0] for x in workloads])
    ax.invert_yaxis()
    ax.set_xlabel("Share of end-to-end time (%)")
    ax.set_ylabel("Workload")
    ax.set_title("Where the time goes (WMC ≤ 0.6%)", pad=4)
    style_ax(ax)
    panel_label(ax, "b")

    fig.subplots_adjust(left=0.075, right=0.985, bottom=0.20, top=0.84, wspace=0.34)
    save(fig, "paper_fig4_pqe")


def validation_table():
    """G6: compact vector table; a table is clearer than overlapping parity points."""
    rows = rd("g6_d4.csv")
    families = [
        ("WatDiv S-star", "watdiv-Sstar"),
        ("TPC-H Q3", "tpch-Q3"),
        ("Wikidata paths", "wikidata-WDpath"),
    ]
    body = []
    total = 0
    max_obdd = 0.0
    max_d4 = 0.0
    for label, family in families:
        pts = [x for x in rows if x["query"] == family]
        obdd_error = max(abs(float(x["obdd_wmc"]) - float(x["pwe"])) for x in pts)
        d4_error = max(abs(float(x["d4_wmc"]) - float(x["pwe"])) for x in pts)
        body.append(
            [
                label,
                str(len(pts)),
                "0" if obdd_error == 0 else f"{obdd_error:.1e}",
                "0" if d4_error == 0 else f"{d4_error:.1e}",
            ]
        )
        total += len(pts)
        max_obdd = max(max_obdd, obdd_error)
        max_d4 = max(max_d4, d4_error)
    body.append(
        [
            "All sampled answers",
            str(total),
            "0" if max_obdd == 0 else f"{max_obdd:.1e}",
            "0" if max_d4 == 0 else f"{max_d4:.1e}",
        ]
    )

    columns = ["Workload", "Answers checked", "max |OBDD − PWE|", "max |d4 − PWE|"]
    fig, ax = plt.subplots(figsize=(7.15, 1.55))
    ax.axis("off")
    table = ax.table(
        cellText=body,
        colLabels=columns,
        loc="center",
        cellLoc="center",
        colWidths=[0.29, 0.18, 0.265, 0.265],
    )
    table.auto_set_font_size(False)
    table.set_fontsize(7.5)
    table.scale(1, 1.45)
    for (row_i, col_i), cell in table.get_celld().items():
        cell.set_edgecolor(LIGHT_GRAY)
        cell.set_linewidth(0.55)
        if row_i == 0:
            cell.set_facecolor("#EAF2F8")
            cell.set_text_props(fontweight="bold")
        elif row_i == len(body):
            cell.set_text_props(fontweight="bold")
    save(fig, "paper_table1_validation")


if __name__ == "__main__":
    fig_compilation()
    fig_sharing()
    fig_construction()
    fig_pqe()
    validation_table()
    print("\nAll paper figures written to figures/ (PDF + 300-dpi PNG).")
