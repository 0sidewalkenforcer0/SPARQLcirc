"""Shared visual grammar for the SPARQLcirc paper figures.

Extracted from ``make_round9_drafts.py`` (the ROUND-9 layout drafts) so the real
data figures render with the *same* grammar as the drafts:

* one canonical text width (``FIG_WIDTH``); only height varies with panel count;
* a SPARQLprov-inspired palette with a fixed B/R/N/C semantic mapping;
* light-grid frames, bold lowercase panel letters, gray footer captions that carry
  the design rationale, and shared frameless top legends;
* dual vector-PDF + 300-dpi-PNG output with publisher-safe embedded fonts.

Both generators import this module.  ``make_round9_drafts.py`` passes ``draft=True``
to ``save`` (stamping ``DATA PENDING`` / ``LAYOUT DRAFT — NO DATA``); the real
figures pass ``draft=False``.  Apart from that watermark the two sets are identical
by construction.
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

# --------------------------------------------------------------------- geometry
FIG_WIDTH = 7.15  # inches; the paper text width.  Every figure uses it.

# ---------------------------------------------------------------------- palette
# SPARQLprov-inspired: quiet pastel base bars, a saturated blue, a warm red accent
# on a light grid.  Every color carries a redundant marker/linestyle too.
SP_BASE = "#D9E2F0"      # B: base query        (pale blue)
SP_REIFIED = "#FDBF6F"   # R: reified base      (warm orange)
SP_NPCS = "#2B8CBE"      # N: NPCS / baseline   (saturated blue)
SP_CIRCUIT = "#E34A33"   # C: SPARQLcirc / ours (warm red)
SP_GRID = "#E6E6E6"

# neutral tones + extra series colors for >4-way line plots
VERMILLION = "#D55E00"
GRAY = "#6B6B6B"
MID_GRAY = "#A6A6A6"
LIGHT_GRAY = "#D9D9D9"
BLACK = "#222222"
SERIES = [SP_NPCS, SP_CIRCUIT, SP_REIFIED, "#6A51A3", "#31A354", "#636363"]
SERIES_MARKERS = ["o", "s", "^", "D", "v", "P"]

TIMEOUT_LS = (0, (1.4, 2.2))  # dotted rule used for every timeout line
SPINE = "#CFCFCF"

# canonical bar width by group cardinality (from the drafts)
_BAR_WIDTH = {1: 0.5, 2: 0.34, 3: 0.24, 4: 0.19}


def apply_style():
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


apply_style()


def save(fig, stem, out_dir, draft=False, creator="sparqlcirc/presentation/figstyle.py"):
    """Save a vector figure and a 300-dpi preview into ``out_dir``.

    ``draft=True`` stamps the unmistakable ``LAYOUT DRAFT — NO DATA`` flag and marks
    the PDF metadata so a draft can never be mistaken for a result figure.
    """
    os.makedirs(out_dir, exist_ok=True)
    subject = "Layout draft only; contains no experimental measurements" if draft else \
              "Paper figure; every plotted value comes from a committed reference/ CSV"
    if draft:
        fig.text(0.995, 0.006, "LAYOUT DRAFT — NO DATA", ha="right", va="bottom",
                 fontsize=6.3, color=VERMILLION, fontweight="bold")
    metadata = {"Creator": creator, "Subject": subject}
    fig.savefig(os.path.join(out_dir, f"{stem}.pdf"), metadata=metadata, facecolor="white")
    fig.savefig(os.path.join(out_dir, f"{stem}.png"), dpi=300, facecolor="white")
    plt.close(fig)
    print("wrote", os.path.join(os.path.basename(out_dir), f"{stem}.pdf/.png"))


def panel_label(ax, index, x=-0.13, y=1.10):
    """Bold lowercase panel letter; ``index`` may be an int (0->'a') or a letter."""
    letter = ascii_lowercase[index] if isinstance(index, int) else index
    ax.text(x, y, letter, transform=ax.transAxes, ha="left", va="top",
            fontsize=9.2, fontweight="bold")


def pending(ax, text="DATA PENDING", y=0.55):
    ax.text(0.5, y, text, transform=ax.transAxes, ha="center", va="center",
            fontsize=7.0, color=MID_GRAY, fontweight="bold",
            bbox={"boxstyle": "round,pad=0.23", "fc": "white", "ec": LIGHT_GRAY, "lw": 0.6},
            zorder=10)


def frame(ax, grid_axis="both"):
    """Light SPARQLprov-style frame: pale spines, major grid, no minor grid."""
    ax.grid(True, which="major", axis=grid_axis, color=SP_GRID, linewidth=0.55)
    ax.grid(False, which="minor")
    for spine in ax.spines.values():
        spine.set_color(SPINE)
        spine.set_linewidth(0.55)
    ax.tick_params(length=2.0, width=0.45, color="#777777")


def light_log_axis(ax, xlabel, ylabel, title, logx=True, logy=True):
    """Frame for ordered scale experiments (log axes + light grid)."""
    if logx:
        ax.set_xscale("log")
    if logy:
        ax.set_yscale("log")
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    if title:
        ax.set_title(title, pad=4)
    frame(ax)


def footer(fig, text, x=0.01, y=0.010):
    """One-line gray caption carrying the design/method rationale, outside the artwork."""
    fig.text(x, y, text, ha="left", va="bottom", fontsize=6.2, color=GRAY)


def suptitle(fig, text, y=0.985, fontsize=9.2):
    fig.suptitle(text, y=y, fontsize=fontsize, fontweight="bold")


def top_legend(fig, handles, labels, ncol, y=0.94, **kw):
    """Shared frameless horizontal legend above the panels."""
    fig.legend(handles, labels, ncol=ncol, frameon=False, loc="upper center",
               bbox_to_anchor=(0.5, y), **kw)


def grouped_bars(ax, x, series_values, colors, labels, *, width=None, log=True,
                 yerr=None, timeout=None):
    """Thin solid grouped bars with white separators (the drafts' bar idiom).

    ``series_values`` is a list of equal-length height arrays (one per method).
    Colors follow the fixed B/R/N/C mapping; ours (SPARQLcirc) is ``SP_CIRCUIT``.
    """
    n = len(series_values)
    width = width or _BAR_WIDTH.get(n, 0.8 / n)
    offsets = (np.arange(n) - (n - 1) / 2.0) * width
    bars = []
    for i, (vals, color, offset, label) in enumerate(zip(series_values, colors, offsets, labels)):
        err = yerr[i] if yerr is not None else None
        bars.append(ax.bar(x + offset, np.asarray(vals, dtype=float), width, color=color,
                           edgecolor="white", linewidth=0.3, label=label, zorder=3,
                           yerr=err, capsize=2.2 if err is not None else 0))
    if log:
        ax.set_yscale("log")
    if timeout is not None:
        ax.axhline(timeout, color="#555555", linestyle=TIMEOUT_LS, linewidth=0.8, zorder=2)
    frame(ax)
    return bars


def patch(color, label):
    return Patch(facecolor=color, edgecolor="none", label=label)


def timeout_handle(label):
    return Line2D([0], [0], color="#555555", linestyle=TIMEOUT_LS, label=label)
