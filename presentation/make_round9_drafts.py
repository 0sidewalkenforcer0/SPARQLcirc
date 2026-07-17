"""Render data-free layout drafts for the ROUND-9 paper figures.

These files are deliberately *not* result figures.  They contain no measurements:
deterministic illustrative geometry demonstrates the intended visual grammar and
will be discarded when the server CSVs arrive.  Every page is watermarked
``LAYOUT DRAFT — NO DATA`` and is written under ``figures/drafts`` so it cannot
be confused with the current ``paper_fig*.pdf`` artifacts.

Run from this directory:

    python3 make_round9_drafts.py

When the server CSVs arrive, preserve the panel geometry and replace the slot
helpers with aggregations described in ``ROUND9_DRAFT_FIGURES.md``.
"""

import os
from string import ascii_lowercase

os.environ.setdefault("MPLCONFIGDIR", os.path.join("/tmp", "sparqlcirc-matplotlib"))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
import numpy as np


HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "figures", "drafts")
os.makedirs(OUT, exist_ok=True)

# Okabe--Ito plus neutral tones.  Every color has a redundant hatch/marker.
VERMILLION = "#D55E00"
GRAY = "#6B6B6B"
MID_GRAY = "#A6A6A6"
LIGHT_GRAY = "#D9D9D9"

ENGINES = ["GraphDB", "Oxigraph", "QLever", "MillenniumDB"]
CLASSES = ["L", "S", "F", "C", "O", "M"]
WATDIV_TEMPLATES = [
    "C1", "C2", "C3",
    "F1", "F2", "F3", "F4", "F5",
    "L1", "L2", "L3", "L4", "L5",
    "S1", "S2", "S3", "S4", "S5", "S6", "S7",
    "O1", "O2", "O3", "O4", "O5",
]
# SPARQLprov-inspired solid palette.  The source paper uses quiet pastel base
# bars, a saturated blue, and a warm red/orange accent on a light grid.
SP_BASE = "#D9E2F0"
SP_REIFIED = "#FDBF6F"
SP_NPCS = "#2B8CBE"
SP_CIRCUIT = "#E34A33"
SP_GRID = "#E6E6E6"

plt.rcParams.update(
    {
        "font.family": "sans-serif",
        "font.sans-serif": ["DejaVu Sans", "Arial", "Helvetica"],
        "font.size": 7.2,
        "axes.labelsize": 7.8,
        "axes.titlesize": 8.1,
        "xtick.labelsize": 6.7,
        "ytick.labelsize": 6.7,
        "legend.fontsize": 6.7,
        "axes.linewidth": 0.7,
        "lines.linewidth": 1.2,
        "lines.markersize": 4.0,
        "grid.color": "#D8DEE5",
        "grid.linewidth": 0.45,
        "grid.alpha": 0.72,
        "axes.axisbelow": True,
        "pdf.fonttype": 42,
        "ps.fonttype": 42,
        "savefig.bbox": "tight",
        "savefig.pad_inches": 0.035,
    }
)


def save(fig, stem):
    """Save a vector draft and a 300-dpi preview with unmistakable metadata."""
    fig.text(
        0.995,
        0.006,
        "LAYOUT DRAFT — NO DATA",
        ha="right",
        va="bottom",
        fontsize=6.3,
        color=VERMILLION,
        fontweight="bold",
    )
    metadata = {
        "Creator": "sparqlcirc/presentation/make_round9_drafts.py",
        "Subject": "Layout draft only; contains no experimental measurements",
    }
    fig.savefig(os.path.join(OUT, f"{stem}.pdf"), metadata=metadata, facecolor="white")
    fig.savefig(os.path.join(OUT, f"{stem}.png"), dpi=300, facecolor="white")
    plt.close(fig)
    print("wrote", os.path.join("figures", "drafts", f"{stem}.pdf/.png"))


def panel_label(ax, index, x=-0.13, y=1.10):
    ax.text(
        x,
        y,
        ascii_lowercase[index],
        transform=ax.transAxes,
        ha="left",
        va="top",
        fontsize=9.2,
        fontweight="bold",
    )


def pending(ax, text="DATA PENDING", y=0.55):
    ax.text(
        0.5,
        y,
        text,
        transform=ax.transAxes,
        ha="center",
        va="center",
        fontsize=7.0,
        color=MID_GRAY,
        fontweight="bold",
        bbox={"boxstyle": "round,pad=0.23", "fc": "white", "ec": LIGHT_GRAY, "lw": 0.6},
        zorder=10,
    )


def layout_only_runtimes(scale_index, engine_index):
    """Deterministic visual scaffolding; deliberately not experimental data."""
    i = np.arange(len(WATDIV_TEMPLATES), dtype=float)
    # Mild variation makes widths, overlaps, and timeout marks inspectable.  The
    # watermark and panel annotation make clear that these heights are discarded.
    base = 32.0 * (1.0 + 0.20 * (i % 5)) * (1.0 + 0.08 * engine_index)
    base *= 1.0 if scale_index == 0 else 4.2
    reified = base * (1.35 + 0.08 * ((i + 1) % 3))
    npcs = reified * (2.1 + 0.45 * ((i + engine_index) % 4))
    circuit = reified * (1.55 + 0.30 * ((i + 2 * engine_index) % 3))
    return [base, reified, npcs, circuit]


def sparqlprov_bar_axis(ax, scale_index, engine_index, methods=4):
    """Thin grouped bars, light grid, log axis, and dotted timeout."""
    x = np.arange(len(WATDIV_TEMPLATES))
    width = 0.19 if methods == 4 else 0.24
    values = layout_only_runtimes(scale_index, engine_index)
    colors = [SP_BASE, SP_REIFIED, SP_NPCS, SP_CIRCUIT]
    if methods == 3:
        values = values[1:]
        colors = colors[1:]
    offsets = (np.arange(methods) - (methods - 1) / 2.0) * width
    for vals, color, offset in zip(values, colors, offsets):
        ax.bar(x + offset, vals, width=width, color=color, edgecolor="white",
               linewidth=0.25, zorder=3)
    ax.set_yscale("log")
    ax.set_ylim(10, 500_000)
    ax.axhline(300_000, color="#555555", linestyle=(0, (1.4, 2.2)), linewidth=0.8, zorder=2)
    ax.set_xlim(-0.7, len(x) - 0.3)
    ax.set_xticks(x, WATDIV_TEMPLATES)
    ax.grid(True, which="major", axis="both", color=SP_GRID, linewidth=0.55)
    ax.grid(False, which="minor")
    for spine in ax.spines.values():
        spine.set_color("#CFCFCF")
        spine.set_linewidth(0.55)
    ax.tick_params(length=2.0, width=0.45, color="#777777")
    ax.text(0.5, 0.55, "ILLUSTRATIVE HEIGHTS — DATA PENDING", transform=ax.transAxes,
            ha="center", va="center", color="#888888", fontsize=7.0, fontweight="bold",
            bbox=dict(boxstyle="round,pad=0.28", facecolor="white", edgecolor="#D8D8D8", alpha=0.90))


def fig_construction_engine(engine):
    """R9.2: one SPARQLprov-style 10M/100M construction figure per engine."""
    engine_index = ENGINES.index(engine)
    fig, axes = plt.subplots(2, 1, figsize=(7.15, 3.65), sharex=True, sharey=True)
    for row, (ax, scale) in enumerate(zip(axes, ("WatDiv 10M", "WatDiv 100M"))):
        sparqlprov_bar_axis(ax, row, engine_index)
        ax.set_title(scale, fontsize=7.2, pad=2.5)
        ax.set_ylabel("Runtime (ms)")
    axes[0].tick_params(axis="x", labelbottom=False)
    axes[1].set_xlabel("Query template")
    fig.suptitle(engine, y=0.985, fontsize=9.2, fontweight="bold")
    legend = [
        Patch(facecolor=SP_BASE, edgecolor="none", label="B: base query"),
        Patch(facecolor=SP_REIFIED, edgecolor="none", label="R: reified base"),
        Patch(facecolor=SP_NPCS, edgecolor="none", label="N: NPCS"),
        Patch(facecolor=SP_CIRCUIT, edgecolor="none", label="C: SPARQLcirc"),
        Line2D([0], [0], color="#555555", linestyle=(0, (1.4, 2.2)), label="300 s timeout"),
    ]
    fig.legend(legend, [item.get_label() for item in legend], ncol=5, frameon=False,
               loc="upper center", bbox_to_anchor=(0.5, 0.94), columnspacing=1.3, handlelength=1.7)
    fig.text(0.01, 0.010,
             "SPARQLprov-style draft: thin grouped raw B/R/N/C bars; illustrative heights will be replaced.",
             ha="left", va="bottom", fontsize=6.2, color=GRAY)
    fig.subplots_adjust(left=0.09, right=0.995, bottom=0.135, top=0.82, hspace=0.24)
    save(fig, f"draft_r9_2_construction_{engine.lower()}")


def fig_multisource_engine(engine):
    """R9.2b: source-multiplicity stress remains separate from scale figures."""
    engine_index = ENGINES.index(engine)
    fig, ax = plt.subplots(figsize=(7.15, 2.15))
    sparqlprov_bar_axis(ax, 1, engine_index, methods=3)
    ax.set_title(f"{engine} — 100M × 2 sources (stress case, not a 200M scale point)",
                 fontsize=8.2, pad=6, fontweight="bold")
    ax.set_xlabel("Query template")
    ax.set_ylabel("Runtime (ms)")
    legend = [
        Patch(facecolor=SP_REIFIED, edgecolor="none", label="R: reified base"),
        Patch(facecolor=SP_NPCS, edgecolor="none", label="N: NPCS"),
        Patch(facecolor=SP_CIRCUIT, edgecolor="none", label="C: SPARQLcirc"),
        Line2D([0], [0], color="#555555", linestyle=(0, (1.4, 2.2)), label="300 s timeout"),
    ]
    fig.legend(legend, [item.get_label() for item in legend], ncol=4, frameon=False,
               loc="upper center", bbox_to_anchor=(0.5, 0.90))
    fig.text(0.01, 0.010,
             "Final caption will report logical facts, provenance statements, and physical RDF triples.",
             ha="left", va="bottom", fontsize=6.2, color=GRAY)
    fig.subplots_adjust(left=0.09, right=0.995, bottom=0.23, top=0.70)
    save(fig, f"draft_r9_2b_multisource_{engine.lower()}")


def light_log_axis(ax, xlabel, ylabel, title):
    """SPARQLprov-like line-plot frame for ordered scale experiments."""
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    ax.set_title(title, pad=4)
    ax.grid(True, which="major", color=SP_GRID, linewidth=0.55)
    ax.grid(False, which="minor")
    for spine in ax.spines.values():
        spine.set_color("#CFCFCF")
        spine.set_linewidth(0.55)
    ax.tick_params(length=2.0, width=0.45, color="#777777")


def fig_data_scale_engine(engine):
    """Data-scale time/size/RSS figure; one independently readable page per engine."""
    engine_index = ENGINES.index(engine)
    x = np.array([10, 100, 1000], dtype=float)
    colors = [SP_NPCS, SP_CIRCUIT, SP_REIFIED, "#6A51A3", "#31A354", "#636363"]
    markers = ["o", "s", "^", "D", "v", "P"]
    fig, axes = plt.subplots(1, 3, figsize=(7.15, 2.55))
    for class_index, (query_class, color, marker) in enumerate(zip(CLASSES, colors, markers)):
        class_factor = 1.0 + 0.28 * class_index + 0.08 * engine_index
        time = 18.0 * class_factor * (x / 10.0) ** (0.78 + 0.025 * class_index)
        size = 900.0 * class_factor * (x / 10.0) ** (0.88 + 0.018 * class_index)
        rss = 70.0 * class_factor * (x / 10.0) ** (0.18 + 0.008 * class_index)
        for ax, values in zip(axes, (time, size, rss)):
            ax.plot(x, values, color=color, marker=marker, linewidth=1.0,
                    label=query_class, zorder=3)
    light_log_axis(axes[0], "WatDiv triples (millions)", "Construction time (ms)", "Construction")
    light_log_axis(axes[1], "WatDiv triples (millions)", "Gates + edges", "Circuit growth")
    light_log_axis(axes[2], "WatDiv triples (millions)", "Builder peak RSS (MiB)", "Client memory")
    for i, ax in enumerate(axes):
        ax.set_xticks(x, ["10M", "100M", "1B"])
        pending(ax, "ILLUSTRATIVE\nDATA PENDING", y=0.72)
        panel_label(ax, i, x=-0.22)
    fig.suptitle(f"{engine} — data-scale construction", y=0.99, fontsize=9.0, fontweight="bold")
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, ncol=6, frameon=False, loc="upper center",
               bbox_to_anchor=(0.5, 0.91), columnspacing=1.4)
    fig.text(0.01, 0.010,
             "100M×2 sources is excluded; a real-KG point is reported separately, not joined to WatDiv.",
             ha="left", va="bottom", fontsize=6.2, color=GRAY)
    fig.subplots_adjust(left=0.085, right=0.995, bottom=0.23, top=0.75, wspace=0.34)
    save(fig, f"draft_r9_2c_data_scale_{engine.lower()}")


def storage_ratio_axis(ax, scale_index, engine_index):
    """Three same-unit NPCS/SPARQLcirc ratios; categorical points stay unconnected."""
    i = np.arange(len(WATDIV_TEMPLATES), dtype=float)
    scale_factor = 1.0 + 0.35 * scale_index
    engine_factor = 1.0 + 0.06 * engine_index
    structural = (0.45 + 0.36 * (i % 6)) * scale_factor * engine_factor
    native_bytes = (0.07 + 0.045 * (i % 7)) * scale_factor
    normalized_bytes = (0.55 + 0.42 * ((i + 2) % 6)) * scale_factor * engine_factor
    x = np.arange(len(WATDIV_TEMPLATES))
    series = [
        (structural, SP_NPCS, "o", "structural elements"),
        (native_bytes, SP_REIFIED, "^", "native UTF-8 bytes"),
        (normalized_bytes, SP_CIRCUIT, "s", "normalized short-ID bytes"),
    ]
    for offset, (values, color, marker, label) in zip((-0.20, 0.0, 0.20), series):
        ax.scatter(x + offset, values, s=12, color=color, marker=marker,
                   edgecolors="white", linewidths=0.25, label=label, zorder=3)
    ax.axhline(1.0, color="#555555", linestyle=(0, (1.4, 2.2)), linewidth=0.8)
    ax.set_yscale("log")
    ax.set_ylim(0.03, 30)
    ax.set_xlim(-0.7, len(x) - 0.3)
    ax.set_xticks(x, WATDIV_TEMPLATES)
    ax.set_ylabel("NPCS / SPARQLcirc size")
    ax.grid(True, which="major", axis="both", color=SP_GRID, linewidth=0.55)
    ax.grid(False, which="minor")
    for spine in ax.spines.values():
        spine.set_color("#CFCFCF")
        spine.set_linewidth(0.55)
    pending(ax, "ILLUSTRATIVE RATIOS — DATA PENDING", y=0.78)


def fig_sharing_engine(engine):
    """R9.3: external-validity size ratios, split by engine and scale."""
    engine_index = ENGINES.index(engine)
    fig, axes = plt.subplots(2, 1, figsize=(7.15, 3.50), sharex=True, sharey=True)
    for row, (ax, scale) in enumerate(zip(axes, ("WatDiv 10M", "WatDiv 100M"))):
        storage_ratio_axis(ax, row, engine_index)
        ax.set_title(scale, fontsize=7.2, pad=2.5)
    axes[0].tick_params(axis="x", labelbottom=False)
    axes[1].set_xlabel("Query template")
    fig.suptitle(f"{engine} — NPCS representation vs shared circuit",
                 y=0.985, fontsize=9.0, fontweight="bold")
    handles, labels = axes[0].get_legend_handles_labels()
    handles.append(Line2D([0], [0], color="#555555", linestyle=(0, (1.4, 2.2))))
    labels.append("equal size")
    fig.legend(handles, labels, ncol=4, frameon=False, loc="upper center",
               bbox_to_anchor=(0.5, 0.925), columnspacing=1.4)
    fig.text(0.01, 0.010,
             "Ratio > 1 means SPARQLcirc is smaller; low-sharing counterexamples remain visible.",
             ha="left", va="bottom", fontsize=6.2, color=GRAY)
    fig.subplots_adjust(left=0.095, right=0.995, bottom=0.14, top=0.80, hspace=0.25)
    save(fig, f"draft_r9_3_storage_{engine.lower()}")


def fig_sharing_crossover():
    """Controlled explanation: representation crossover plus shared compilation."""
    fig, axes = plt.subplots(1, 2, figsize=(7.15, 2.65))
    derivations = np.array([10, 100, 1_000, 10_000, 100_000, 1_000_000], dtype=float)
    axes[0].plot(derivations, 0.42 * (derivations / 10.0) ** 0.28,
                 color=SP_NPCS, marker="o", label="structural")
    axes[0].plot(derivations, 0.08 * (derivations / 10.0) ** 0.36,
                 color=SP_REIFIED, marker="^", linestyle="--", label="native bytes")
    axes[0].plot(derivations, 0.55 * (derivations / 10.0) ** 0.31,
                 color=SP_CIRCUIT, marker="s", linestyle=":", label="normalized bytes")
    axes[0].axhline(1.0, color="#555555", linestyle=(0, (1.4, 2.2)), linewidth=0.8)
    light_log_axis(axes[0], "Derivations / repeated subterms", "NPCS / SPARQLcirc size",
                   "Sharing crossover")
    axes[0].legend(frameon=False, loc="upper left")

    answers = np.array([1, 10, 100, 1000], dtype=float)
    axes[1].plot(answers, 0.9 + 0.011 * answers, color=SP_NPCS, marker="o",
                 label="shared compile once")
    axes[1].plot(answers, 0.7 + 0.085 * answers, color=SP_CIRCUIT, marker="s",
                 linestyle="--", label="per-answer completion")
    light_log_axis(axes[1], "Answers", "Compile + WMC (ms)", "One shared pass vs per answer")
    axes[1].legend(frameon=False, loc="upper left")
    for i, ax in enumerate(axes):
        pending(ax, "ILLUSTRATIVE\nDATA PENDING", y=0.78)
        panel_label(ax, i, x=-0.18)
    fig.text(0.01, 0.012,
             "Engine-independent controlled families; real-query size ratios are shown on separate engine pages.",
             ha="left", va="bottom", fontsize=6.2, color=GRAY)
    fig.subplots_adjust(left=0.085, right=0.995, bottom=0.20, top=0.90, wspace=0.29)
    save(fig, "draft_r9_3b_sharing_crossover")


def compiler_lines(ax, x, obdd, d4, xlabel, ylabel, title, timeout=False, logx=True):
    ax.plot(x, obdd, color=SP_CIRCUIT, marker="s", linestyle="--", label="fixed-order OBDD")
    ax.plot(x, d4, color=SP_NPCS, marker="o", label="d-DNNF (d4)")
    if logx:
        light_log_axis(ax, xlabel, ylabel, title)
    else:
        ax.set_yscale("log")
        ax.set_xlabel(xlabel)
        ax.set_ylabel(ylabel)
        ax.set_title(title, pad=4)
        ax.grid(True, which="major", color=SP_GRID, linewidth=0.55)
        ax.grid(False, which="minor")
        for spine in ax.spines.values():
            spine.set_color("#CFCFCF")
            spine.set_linewidth(0.55)
    if timeout:
        ax.axhline(120_000, color="#555555", linestyle=(0, (1.4, 2.2)), linewidth=0.8)


def fig_compilation_scale():
    """R9.4/E4: time, output size, and compiler RSS on two independent scale axes."""
    fig, axes = plt.subplots(2, 3, figsize=(7.15, 4.15))
    gates = np.array([100, 1_000, 10_000, 100_000], dtype=float)
    compiler_lines(axes[0, 0], gates, 0.12 * gates ** 1.22, 0.09 * gates ** 0.92,
                   "Input gates", "Compile time (ms)", "Fixed treewidth: latency", timeout=True)
    compiler_lines(axes[0, 1], gates, 1.5 * gates ** 1.13, 1.2 * gates ** 0.94,
                   "Input gates", "Compiled nodes", "Fixed treewidth: size")
    compiler_lines(axes[0, 2], gates, 30 + 0.04 * gates ** 0.85, 35 + 0.02 * gates ** 0.78,
                   "Input gates", "Compiler peak RSS (MiB)", "Fixed treewidth: memory")

    tw = np.array([1, 2, 4, 6, 8, 10], dtype=float)
    compiler_lines(axes[1, 0], tw, 18 * 2.45 ** tw, 22 * 1.92 ** tw,
                   "Treewidth", "Compile time (ms)", "Growing treewidth: latency", timeout=True,
                   logx=False)
    compiler_lines(axes[1, 1], tw, 35 * 2.20 ** tw, 42 * 1.72 ** tw,
                   "Treewidth", "Compiled nodes", "Growing treewidth: size", logx=False)
    compiler_lines(axes[1, 2], tw, 45 * 1.66 ** tw, 48 * 1.42 ** tw,
                   "Treewidth", "Compiler peak RSS (MiB)", "Growing treewidth: memory", logx=False)
    for row in range(2):
        for col in range(3):
            ax = axes[row, col]
            pending(ax, "ILLUSTRATIVE\nDATA PENDING", y=0.75)
            # A six-panel grid leaves too little inter-panel space for external
            # letters; keep them just inside the upper-left corner instead.
            panel_label(ax, row * 3 + col, x=0.015, y=0.985)
    handles, labels = axes[0, 0].get_legend_handles_labels()
    handles.append(Line2D([0], [0], color="#555555", linestyle=(0, (1.4, 2.2))))
    labels.append("120 s timeout")
    fig.legend(handles, labels, ncol=3, frameon=False, loc="upper center",
               bbox_to_anchor=(0.5, 1.005))
    fig.text(0.01, 0.010,
             "One canonical circuit per instance; compiler memory is measured in separate memory-only runs.",
             ha="left", va="bottom", fontsize=6.2, color=GRAY)
    fig.subplots_adjust(left=0.085, right=0.995, bottom=0.14, top=0.91, hspace=0.47, wspace=0.36)
    save(fig, "draft_r9_4_compilation_scale")


def fig_compilation_patterns():
    """R9.4: external validity over real query classes, not repeated by source engine."""
    labels = CLASSES + ["Path"]
    x = np.arange(len(labels))
    width = 0.34
    obdd_time = 55 * (1.0 + 0.72 * x) ** 1.65
    d4_time = 42 * (1.0 + 0.48 * x) ** 1.42
    obdd_nodes = 180 * (1.0 + 0.84 * x) ** 2.0
    d4_nodes = 150 * (1.0 + 0.54 * x) ** 1.65
    fig, axes = plt.subplots(1, 2, figsize=(7.15, 2.55))
    for ax, obdd, d4, ylabel, title in (
        (axes[0], obdd_time, d4_time, "Compile time (ms)", "Real-pattern latency"),
        (axes[1], obdd_nodes, d4_nodes, "Compiled nodes", "Real-pattern size"),
    ):
        ax.bar(x - width / 2, obdd, width, color=SP_CIRCUIT, edgecolor="white", linewidth=0.3,
               label="fixed-order OBDD")
        ax.bar(x + width / 2, d4, width, color=SP_NPCS, edgecolor="white", linewidth=0.3,
               label="d-DNNF (d4)")
        ax.set_yscale("log")
        ax.set_xticks(x, labels)
        ax.set_xlabel("Query class")
        ax.set_ylabel(ylabel)
        ax.set_title(title, pad=4)
        ax.grid(True, which="major", axis="y", color=SP_GRID, linewidth=0.55)
        ax.grid(False, which="minor")
        for spine in ax.spines.values():
            spine.set_color("#CFCFCF")
            spine.set_linewidth(0.55)
        pending(ax, "ILLUSTRATIVE HEIGHTS\nDATA PENDING", y=0.78)
    panel_label(axes[0], 0, x=-0.16)
    panel_label(axes[1], 1, x=-0.16)
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, ncol=2, frameon=False, loc="upper center", bbox_to_anchor=(0.5, 1.01))
    fig.text(0.01, 0.010,
             "Client-side compilation is engine-independent; each query instance appears once.",
             ha="left", va="bottom", fontsize=6.2, color=GRAY)
    fig.subplots_adjust(left=0.085, right=0.995, bottom=0.21, top=0.82, wspace=0.29)
    save(fig, "draft_r9_4b_compilation_patterns")


def e2e_stage_axis(ax, scale_index, engine_index):
    """Grouped stage bars on a log axis; total is a separate marker, never a log-stacked bar."""
    x = np.arange(len(WATDIV_TEMPLATES))
    construct = layout_only_runtimes(scale_index, engine_index)[3]
    compile_ms = 0.32 * construct * (1.0 + 0.18 * (x % 4))
    wmc = 0.045 * construct * (1.0 + 0.10 * ((x + 1) % 3))
    total = construct + compile_ms + wmc
    width = 0.22
    for offset, values, color, label in (
        (-width, construct, SP_NPCS, "construct + parse"),
        (0.0, compile_ms, SP_REIFIED, "compile"),
        (width, wmc, SP_CIRCUIT, "WMC"),
    ):
        ax.bar(x + offset, values, width, color=color, edgecolor="white", linewidth=0.25,
               label=label, zorder=3)
    ax.scatter(x, total, color="#222222", marker="D", s=8, label="end-to-end total", zorder=4)
    ax.set_yscale("log")
    ax.set_ylim(1, 500_000)
    ax.axhline(300_000, color="#555555", linestyle=(0, (1.4, 2.2)), linewidth=0.8)
    ax.set_xlim(-0.7, len(x) - 0.3)
    ax.set_xticks(x, WATDIV_TEMPLATES)
    ax.set_ylabel("Runtime (ms)")
    ax.grid(True, which="major", axis="both", color=SP_GRID, linewidth=0.55)
    ax.grid(False, which="minor")
    for spine in ax.spines.values():
        spine.set_color("#CFCFCF")
        spine.set_linewidth(0.55)
    pending(ax, "ILLUSTRATIVE HEIGHTS — DATA PENDING", y=0.76)


def fig_e2e_engine(engine):
    """R9.5: one complete exact-PQE stage figure per source engine."""
    engine_index = ENGINES.index(engine)
    fig, axes = plt.subplots(2, 1, figsize=(7.15, 3.55), sharex=True, sharey=True)
    for row, (ax, scale) in enumerate(zip(axes, ("WatDiv 10M", "WatDiv 100M"))):
        e2e_stage_axis(ax, row, engine_index)
        ax.set_title(scale, fontsize=7.2, pad=2.5)
    axes[0].tick_params(axis="x", labelbottom=False)
    axes[1].set_xlabel("Query template")
    fig.suptitle(f"{engine} — end-to-end exact PQE", y=0.985, fontsize=9.0, fontweight="bold")
    handles, labels = axes[0].get_legend_handles_labels()
    handles.append(Line2D([0], [0], color="#555555", linestyle=(0, (1.4, 2.2))))
    labels.append("300 s construction cap")
    fig.legend(handles, labels, ncol=5, frameon=False, loc="upper center",
               bbox_to_anchor=(0.5, 0.925), columnspacing=1.2)
    fig.text(0.01, 0.010,
             "Grouped stages avoid misleading stacking on a log axis; diamonds mark the arithmetic total.",
             ha="left", va="bottom", fontsize=6.2, color=GRAY)
    fig.subplots_adjust(left=0.09, right=0.995, bottom=0.14, top=0.80, hspace=0.25)
    save(fig, f"draft_r9_5_e2e_{engine.lower()}")


def fig_paths_engine(engine):
    """R9.6: ordered reachability scaling only; support breadth is a LaTeX table."""
    engine_index = ENGINES.index(engine)
    reachable = np.array([10, 100, 1_000, 10_000, 100_000], dtype=float)
    shapes = [
        ("sparse", SP_NPCS, "o", "-", 1.0),
        ("cyclic", SP_REIFIED, "^", "--", 1.65),
        ("dense", SP_CIRCUIT, "s", ":", 2.45),
    ]
    fig, axes = plt.subplots(1, 3, figsize=(7.15, 2.55))
    for label, color, marker, linestyle, factor in shapes:
        engine_factor = 1.0 + 0.10 * engine_index
        construct = 4.5 * factor * engine_factor * reachable ** 0.83
        circuit = 7.0 * factor * reachable ** 1.02
        rss = 42.0 * engine_factor + 0.90 * factor * reachable ** 0.48
        for ax, values in zip(axes, (construct, circuit, rss)):
            ax.plot(reachable, values, color=color, marker=marker, linestyle=linestyle,
                    linewidth=1.0, label=label)
    light_log_axis(axes[0], "Reachable nodes", "Construction time (ms)", "Iterative construction")
    light_log_axis(axes[1], "Reachable nodes", "Gates + edges", "Circuit growth")
    light_log_axis(axes[2], "Reachable nodes", "Builder peak RSS (MiB)", "Client memory")
    for i, ax in enumerate(axes):
        pending(ax, "ILLUSTRATIVE\nDATA PENDING", y=0.74)
        panel_label(ax, i, x=-0.22)
    fig.suptitle(f"{engine} — property-path reachability scale", y=0.99,
                 fontsize=9.0, fontweight="bold")
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, ncol=3, frameon=False, loc="upper center",
               bbox_to_anchor=(0.5, 0.91), columnspacing=1.5)
    fig.text(0.01, 0.010,
             "Pattern support/limitations are typeset in LaTeX; curves vary reachable size and density/cycles.",
             ha="left", va="bottom", fontsize=6.2, color=GRAY)
    fig.subplots_adjust(left=0.085, right=0.995, bottom=0.23, top=0.75, wspace=0.35)
    save(fig, f"draft_r9_6_paths_{engine.lower()}")


def fig_provsql_tpch():
    """Matched ProvSQL subset: runtime trends only; exact parity is a LaTeX table."""
    fig, axes = plt.subplots(1, 2, figsize=(7.15, 2.55))
    scale = np.array([0.01, 0.03, 0.1, 0.3, 1.0], dtype=float)
    axes[0].plot(scale, 95 * (scale / 0.01) ** 0.82, color=SP_CIRCUIT, marker="s",
                 label="SPARQLcirc")
    axes[0].plot(scale, 130 * (scale / 0.01) ** 0.91, color=SP_NPCS, marker="o",
                 label="ProvSQL")
    light_log_axis(axes[0], "TPC-H scale factor", "End-to-end runtime (ms)", "Matched scale trend")

    queries = ["Q3", "Qrecon", "correlated"]
    x = np.arange(len(queries))
    width = 0.34
    ours = np.array([180, 430, 1200], dtype=float)
    provsql = np.array([240, 620, 1500], dtype=float)
    axes[1].bar(x - width / 2, ours, width, color=SP_CIRCUIT, edgecolor="white",
                linewidth=0.3, label="SPARQLcirc")
    axes[1].bar(x + width / 2, provsql, width, color=SP_NPCS, edgecolor="white",
                linewidth=0.3, label="ProvSQL")
    axes[1].set_yscale("log")
    axes[1].set_xticks(x, queries)
    axes[1].set_xlabel("Matched relational query")
    axes[1].set_ylabel("End-to-end runtime (ms)")
    axes[1].set_title("Canonical matched cells", pad=4)
    axes[1].grid(True, which="major", axis="y", color=SP_GRID, linewidth=0.55)
    axes[1].grid(False, which="minor")
    for spine in axes[1].spines.values():
        spine.set_color("#CFCFCF")
        spine.set_linewidth(0.55)
    for i, ax in enumerate(axes):
        pending(ax, "ILLUSTRATIVE\nDATA PENDING", y=0.76)
        panel_label(ax, i, x=-0.18)
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, ncol=2, frameon=False, loc="upper center", bbox_to_anchor=(0.5, 1.01))
    fig.text(0.01, 0.010,
             "Only matched TPC-H cells are plotted; probability parity and key-set agreement are a LaTeX table.",
             ha="left", va="bottom", fontsize=6.2, color=GRAY)
    fig.subplots_adjust(left=0.085, right=0.995, bottom=0.21, top=0.82, wspace=0.29)
    save(fig, "draft_r9_7_provsql_tpch")


if __name__ == "__main__":
    for engine in ENGINES:
        fig_construction_engine(engine)
        fig_multisource_engine(engine)
        fig_data_scale_engine(engine)
        fig_sharing_engine(engine)
        fig_e2e_engine(engine)
        fig_paths_engine(engine)
    fig_sharing_crossover()
    fig_compilation_scale()
    fig_compilation_patterns()
    fig_provsql_tpch()
    print("\nAll ROUND-9 layout drafts written to figures/drafts/ (PDF + 300-dpi PNG).")
