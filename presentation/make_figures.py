"""Generate the paper figures from the committed experiment CSVs.

Rendered through ``figstyle`` — the shared visual grammar extracted from the
ROUND-9 layout drafts (``make_round9_drafts.py``): one canonical text width,
the SPARQLprov-inspired B/R/N/C palette, light-grid frames, bold panel letters,
gray footer captions, and dual PDF + 300-dpi PNG output with embedded fonts.
The real figures are byte-for-byte the same grammar as the drafts, minus the
``DATA PENDING`` watermark.  Every plotted value comes from a committed CSV under
``reference/``.

Outputs are written under ``figures/final/``:

    cd presentation && python3 make_figures.py
"""

import csv
import os

import numpy as np

import figstyle as fs
from figstyle import (
    BLACK, GRAY, LIGHT_GRAY, SP_CIRCUIT, SP_NPCS, SP_REIFIED, plt,
)

HERE = os.path.dirname(os.path.abspath(__file__))
REF = os.path.join(HERE, "..", "reference")
OUT = os.path.join(HERE, "figures", "final")
os.makedirs(OUT, exist_ok=True)

CREATOR = "sparqlcirc/presentation/make_figures.py"


def rd(rel):
    with open(os.path.join(REF, rel), encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def save(fig, stem):
    fs.save(fig, stem, OUT, draft=False, creator=CREATOR)


def fig_compilation():
    """E4: compiler size at fixed and growing treewidth."""
    rows = rd("watdiv/e4_results.csv")
    bounded = [x for x in rows if x["family"] == "bounded_tw2"]
    growing = [x for x in rows if x["family"] == "growing_tw_layer"]

    fig, axes = plt.subplots(1, 2, figsize=(fs.FIG_WIDTH, 2.65))

    ax = axes[0]
    all_n = np.array([int(x["n_tokens"]) for x in bounded])
    dd = np.array([int(x["ddnnf_nodes"]) for x in bounded])
    obdd_rows = [x for x in bounded if x["obdd_size"]]
    timeout_rows = [x for x in bounded if x["status"] == "obdd-timeout"]
    ax.plot(
        [int(x["n_tokens"]) for x in obdd_rows],
        [int(x["obdd_size"]) for x in obdd_rows],
        color=SP_CIRCUIT, marker="s", linestyle="--", label="fixed-order OBDD",
    )
    ax.plot(all_n, dd, color=SP_NPCS, marker="o", label="d-DNNF (d4)")
    timeout_y = 7.2e5
    ax.scatter(
        [int(x["n_tokens"]) for x in timeout_rows],
        [timeout_y] * len(timeout_rows),
        color=SP_CIRCUIT, marker="v", s=28, label="OBDD timeout (120 s)", zorder=4,
    )
    ax.set_ylim(3, 1.05e6)
    fs.light_log_axis(ax, "Input circuit size (tokens)", "Compiled size (nodes)",
                      "Fixed treewidth (tw = 2)")
    fs.panel_label(ax, 0)

    ax = axes[1]
    tw = np.array([int(x["tw"]) for x in growing])
    ax.plot(tw, [int(x["obdd_size"]) for x in growing], color=SP_CIRCUIT, marker="s",
            linestyle="--", label="fixed-order OBDD")
    ax.plot(tw, [int(x["ddnnf_nodes"]) for x in growing], color=SP_NPCS, marker="o",
            label="d-DNNF (d4)")
    ax.set_xticks(tw)
    fs.light_log_axis(ax, "Treewidth", "Compiled size (nodes)",
                      "Growing treewidth (depth = 4)", logx=False)
    fs.panel_label(ax, 1)

    handles, labels = axes[0].get_legend_handles_labels()
    fs.top_legend(fig, handles, labels, ncol=3, y=1.04)
    fs.footer(fig, "d-DNNF (d4) stays polynomial at fixed treewidth; both blow up as treewidth grows.")
    fig.subplots_adjust(left=0.085, right=0.995, bottom=0.20, top=0.82, wspace=0.31)
    save(fig, "paper_fig1_compilation")


def fig_sharing():
    """E2/E11/G2b: the sharing regime and a direct NPCS comparison."""
    bench = rd("bench.csv")
    layered = [x for x in bench if x["instance"].startswith("layered")]
    deep = [x for x in bench if x["instance"].startswith("deep")]
    scale = rd("e11_scale.csv")
    g2b = rd("g2b_npcs_vs_ours.csv")

    fig, axes = plt.subplots(2, 2, figsize=(fs.FIG_WIDTH, 4.55))
    axes = axes.ravel()

    ax = axes[0]
    ax.plot([int(x["derivations"]) for x in layered], [float(x["sharing"]) for x in layered],
            color=SP_NPCS, marker="o", label="layered")
    ax.plot([int(x["derivations"]) for x in deep], [float(x["sharing"]) for x in deep],
            color=SP_CIRCUIT, marker="s", linestyle="--", label="deep")
    ax.axhline(1.0, color=GRAY, linestyle=fs.TIMEOUT_LS, linewidth=0.9)
    best = max(deep, key=lambda x: float(x["sharing"]))
    ax.annotate(f"{float(best['sharing']):.0f}×",
                (int(best["derivations"]), float(best["sharing"])),
                xytext=(-4, 6), textcoords="offset points", ha="right",
                color=SP_CIRCUIT, fontweight="bold")
    ax.set_ylim(0.7, 320)
    fs.light_log_axis(ax, "Derivations", "Per-answer / shared size", "Synthetic reconvergence")
    ax.legend(frameon=False, loc="upper left")
    fs.panel_label(ax, 0)

    ax = axes[1]
    n_answers = [int(x["N"]) for x in scale]
    ax.plot(n_answers, [float(x["shared_ms"]) for x in scale], color=SP_NPCS, marker="o",
            label="shared circuit")
    ax.plot(n_answers, [float(x["perans_ms"]) for x in scale], color=SP_CIRCUIT, marker="s",
            linestyle="--", label="per-answer completion")
    last = scale[-1]
    ax.annotate(f"{float(last['time_win']):.1f}×", (int(last["N"]), float(last["perans_ms"])),
                xytext=(-3, 6), textcoords="offset points", ha="right",
                color=SP_CIRCUIT, fontweight="bold")
    ax.set_ylim(0.3, 100)
    fs.light_log_axis(ax, "Answers", "Compile + WMC (ms)", "Compile once vs per answer")
    ax.legend(frameon=False, loc="upper left")
    fs.panel_label(ax, 1)

    by_query = {x["query"]: x for x in g2b}
    order = ["S-star", "P2-path", "P2-unbound"]
    labels = ["S-star", "P2 bound", "P2 all"]
    x = np.arange(len(order))

    ax = axes[2]
    npcs_ms = [float(by_query[q]["npcs_eval_ms"]) for q in order]
    ours_ms = [float(by_query[q]["ours_eval_ms"]) for q in order]
    fs.grouped_bars(ax, x, [npcs_ms, ours_ms], [SP_NPCS, SP_CIRCUIT],
                    ["NPCS reimplementation", "SPARQLcirc"], log=True)
    ax.set_ylim(1, 1.2e5)
    for i, (npcs_value, ours_value) in enumerate(zip(npcs_ms, ours_ms)):
        ax.text(i + 0.17, ours_value * 1.28, f"{ours_value / npcs_value:.1f}×",
                ha="center", va="bottom", fontsize=6.2, color=BLACK)
    ax.set_xticks(x, labels)
    ax.set_ylabel("Construction time (ms)")
    ax.set_title("NPCS-compatible construction", pad=4)
    ax.legend(frameon=False, loc="upper left", ncol=1)  # narrow: stays left of the P2-all annotation
    fs.panel_label(ax, 2)

    ax = axes[3]
    npcs_kib = [float(by_query[q]["serial_npcs_bytes"]) / 1024.0 for q in order]
    ours_kib = [float(by_query[q]["serial_ours_bytes"]) / 1024.0 for q in order]
    fs.grouped_bars(ax, x, [npcs_kib, ours_kib], [SP_NPCS, SP_CIRCUIT],
                    ["NPCS reimplementation", "SPARQLcirc"], log=True)
    ax.set_ylim(1, 8e5)
    for i, (npcs_value, ours_value) in enumerate(zip(npcs_kib, ours_kib)):
        ax.text(i + 0.17, ours_value * 1.28, f"{ours_value / npcs_value:.1f}×",
                ha="center", va="bottom", fontsize=6.2, color=BLACK)
    ax.set_xticks(x, labels)
    ax.set_ylabel("Serialized output (KiB)")
    ax.set_title("Actual result volume", pad=4)
    fs.panel_label(ax, 3)

    fs.footer(fig, "Sharing pays off on reconvergent queries; on selective ones our RDF volume is larger (see G2b).")
    fig.subplots_adjust(left=0.085, right=0.995, bottom=0.13, top=0.94, hspace=0.48, wspace=0.32)
    save(fig, "paper_fig2_sharing")


def fig_construction():
    """E3/E6/E8: stock-engine overhead by template, scale, and real-KG reach."""
    datasets = [
        ("WatDiv 10M", rd("watdiv/e3_10M.csv"), rd("watdiv/e6_minus_10M.csv")),
        ("WatDiv 100M", rd("watdiv/e3_100M.csv"), rd("watdiv/e6_minus_100M.csv")),
    ]
    e8 = rd("watdiv/e8_wikidata.csv")

    fig, axes = plt.subplots(1, 3, figsize=(fs.FIG_WIDTH, 2.85),
                             gridspec_kw={"width_ratios": [1.0, 1.0, 1.15]})
    query_order = ["S-star", "F-snow", "L-path", "M-minus"]
    query_labels = ["S", "F", "L", "M"]

    for panel, (title, rows, minus_rows) in enumerate(datasets):
        ax = axes[panel]
        by_query = {x["query"]: x for x in rows}
        by_query["M-minus"] = next(x for x in minus_rows if x["query"] == "M-minus")
        x = np.arange(len(query_order))
        # plain_ms is obtained via get_npcs(): the NPCS provenance SELECT, not the base query.
        npcs = [float(by_query[q]["plain_ms"]) for q in query_order]
        circuit = [float(by_query[q]["build_ms"]) for q in query_order]
        fs.grouped_bars(ax, x, [npcs, circuit], [SP_NPCS, SP_CIRCUIT],
                        ["NPCS reimplementation", "circuit CONSTRUCT"], log=True)
        for i, q in enumerate(query_order):
            overhead = float(by_query[q]["c_overhead"])
            ax.text(i + 0.17, circuit[i] * 1.20, f"{overhead:.1f}×", ha="center", va="bottom",
                    fontsize=6.2, color=BLACK)
        ax.set_ylim(1, 2000)
        ax.set_xticks(x, query_labels)
        ax.set_title(title, pad=4)
        ax.set_xlabel("Query template")
        if panel == 0:
            ax.set_ylabel("Execution time (ms, log scale)")
        fs.panel_label(ax, panel)

    ax = axes[2]
    ok = [x for x in e8 if x["status"] == "ok" and int(x["deriv"]) > 0]
    ax.scatter([int(x["deriv"]) for x in ok], [float(x["build_ms"]) / 1000.0 for x in ok],
               facecolors="white", edgecolors=SP_CIRCUIT, linewidths=0.9, s=25, marker="o")
    status_counts = {}
    for row in e8:
        status_counts[row["status"]] = status_counts.get(row["status"], 0) + 1
    ax.text(0.03, 0.97,
            f"{status_counts.get('ok', 0)}/{len(e8)} completed\n"
            f"{status_counts.get('too-large', 0)} too-large; {status_counts.get('err:MemoryError', 0)} OOM",
            transform=ax.transAxes, ha="left", va="top", fontsize=6.7, color=BLACK)
    fs.light_log_axis(ax, "Derivations", "Circuit construction (s)", "Wikidata 2.13B")
    fs.panel_label(ax, 2)

    handles, labels = axes[0].get_legend_handles_labels()
    # off-center: the 3rd panel (Wikidata scatter) has no legend entries of its own
    fig.legend(handles, labels, loc="upper center", ncol=2, frameon=False, bbox_to_anchor=(0.35, 1.01))
    fs.footer(fig, "Circuit CONSTRUCT runs on the stock engine at a small constant overhead; real-KG reach on Wikidata.")
    fig.subplots_adjust(left=0.075, right=0.995, bottom=0.19, top=0.79, wspace=0.38)
    save(fig, "paper_fig3_construction")


def fig_pqe():
    """G4/G3: direct ProvSQL comparison and an overhead decomposition."""
    instances = [x for x in rd("g4_instances.csv") if x["shape"] == "tpch-Q3-SPJ"]
    rigor = rd("g4_rigor.csv")

    fig, axes = plt.subplots(1, 2, figsize=(fs.FIG_WIDTH, 2.75),
                             gridspec_kw={"width_ratios": [1.08, 1.0]})

    ax = axes[0]
    x = np.arange(len(instances))
    ours = np.array([float(row["ours_median_ms"]) / 1000 for row in instances])
    prov = np.array([float(row["provsql_median_ms"]) / 1000 for row in instances])
    ours_sd = np.array([float(row["ours_sd_ms"]) / 1000 for row in instances])
    prov_sd = np.array([float(row["provsql_sd_ms"]) / 1000 for row in instances])
    fs.grouped_bars(ax, x, [ours, prov], [SP_CIRCUIT, SP_NPCS], ["SPARQLcirc", "ProvSQL"],
                    log=False, yerr=[ours_sd, prov_sd])
    short_labels = ["Auto.", "Build.", "Furn.", "House.", "Mach."]
    ax.set_xticks(x, short_labels)
    ax.set_xlabel("TPC-H Q3 market segment")
    ax.set_ylabel("End-to-end PQE (s)")
    ax.set_ylim(0, max(prov + prov_sd) * 1.15)
    ax.set_title("Direct system comparison", pad=4)
    ax.legend(frameon=False, loc="upper right", ncol=2)
    fs.panel_label(ax, 0)

    def row(query, stage):
        return next(x for x in rigor if x["system"] == "ours" and x["query"] == query and x["stage"] == stage)

    ax = axes[1]
    workloads = [("S-star", "watdiv-Sstar"), ("Q3", "tpch-Q3"), ("P279+", "wikidata-WDpath")]
    stages = [("construct", "construct", SP_NPCS), ("compile", "compile", SP_REIFIED),
              ("wmc", "WMC", SP_CIRCUIT)]
    values = {stage: np.array([float(row(q, stage)["median_ms"]) for _, q in workloads])
              for stage, _, _ in stages}
    stage_sum = sum(values.values())
    left = np.zeros(len(workloads))
    y = np.arange(len(workloads))
    for stage, display, color in stages:
        fraction = values[stage] / stage_sum
        ax.barh(y, fraction, left=left, height=0.52, color=color, edgecolor="white",
                linewidth=0.55, label=display, zorder=3)
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
    ax.set_yticks(y, [w[0] for w in workloads])
    ax.invert_yaxis()
    ax.set_xlabel("Share of end-to-end time (%)")
    ax.set_ylabel("Workload")
    ax.set_title("Where the time goes (WMC ≤ 0.6%)", pad=4)
    fs.frame(ax, grid_axis="x")
    fs.panel_label(ax, 1)

    fs.footer(fig, "SPARQLcirc matches ProvSQL's exact PQE end-to-end; construction dominates, WMC is negligible.")
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
        body.append([label, str(len(pts)),
                     "0" if obdd_error == 0 else f"{obdd_error:.1e}",
                     "0" if d4_error == 0 else f"{d4_error:.1e}"])
        total += len(pts)
        max_obdd = max(max_obdd, obdd_error)
        max_d4 = max(max_d4, d4_error)
    body.append(["All sampled answers", str(total),
                 "0" if max_obdd == 0 else f"{max_obdd:.1e}",
                 "0" if max_d4 == 0 else f"{max_d4:.1e}"])

    columns = ["Workload", "Answers checked", "max |OBDD − PWE|", "max |d4 − PWE|"]
    fig, ax = plt.subplots(figsize=(fs.FIG_WIDTH, 1.55))
    ax.axis("off")
    table = ax.table(cellText=body, colLabels=columns, loc="center", cellLoc="center",
                     colWidths=[0.29, 0.18, 0.265, 0.265])
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
    print(f"\nAll paper figures written to {os.path.relpath(OUT, HERE)}/ (PDF + 300-dpi PNG).")
