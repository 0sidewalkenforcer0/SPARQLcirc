#!/usr/bin/env python3
"""Render Public-136 scatterplots using the NPCS Figure 5 construction."""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
from typing import Optional, Sequence

import matplotlib

matplotlib.use("Agg")
import matplotlib.font_manager as font_manager
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.lines import Line2D
from matplotlib.ticker import FixedLocator, FuncFormatter, NullLocator


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
FIGURES = ROOT / "figures"

GRID_COLOR = "#D0D0D0"
SECONDARY_TEXT_COLOR = "#4D4D4D"
TIMEOUT_COLOR = "#FA0000"
GRID_LINEWIDTH = 0.22
MARKER_LINEWIDTH = 0.34
PNG_DPI = 300
EXPORT_PAD_INCHES = 0.01
TIMEOUT_MS = 600_000.0
TIME_AXIS_LOWER_MS = 1.0e2
TIME_AXIS_UPPER_MS = 6.5e5
TIME_TICKS = (1.0e2, 1.0e3, 1.0e4, 1.0e5)
REFERENCE_FIGSIZE = (5.65, 2.05)

# Marker shapes retain the NPCS Figure 5 construction.  The selected colours
# are the coral, teal, and magenta series palette used by the TPC-H figures.
SERIES_STYLES: dict[str, dict[str, object]] = {
    "NPCS": {
        "color": "#F8766D",
        "marker": "x",
        "size": 17.0,
        "legend_size": 4.7,
    },
    "SPARQLcirc (flat)": {
        "color": "#00BFC4",
        "marker": "^",
        "size": 22.0,
        "legend_size": 5.3,
    },
    "SPARQLcirc (factored)": {
        "color": "#CC79A7",
        "marker": "o",
        "size": 16.0,
        "legend_size": 4.7,
    },
}

STYLE_REFERENCE = {
    "paper": "NPCS: Native Provenance Computation for SPARQL",
    "figure": 5,
    "doi": "10.1145/3589334.3645557",
    "public_repository": "https://github.com/ZubariaForthAcc/NPCS",
    "public_figure5_plot_code_available": False,
    "reference_panel_pixels": [1727, 479],
    "reference_panel_embedded_ppi": 247,
}


def read_csv(path: Path) -> list[dict[str, str]]:
    # PowerShell's historical CSV exports may carry a UTF-8 BOM.  ``utf-8-sig``
    # accepts both BOM and plain UTF-8 files without changing field names.
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def choose_reference_sans_font() -> str:
    for family in (
        "Arial",
        "Helvetica",
        "Nimbus Sans",
        "Liberation Sans",
    ):
        try:
            font_manager.findfont(
                font_manager.FontProperties(family=family),
                fallback_to_default=False,
            )
        except ValueError:
            continue
        return family
    return "DejaVu Sans"


def configure_style() -> str:
    family = choose_reference_sans_font()
    plt.rcParams.update(
        {
            "font.family": family,
            "font.size": 7.4,
            "axes.labelsize": 8.3,
            "xtick.labelsize": 7.0,
            "ytick.labelsize": 7.0,
            "legend.fontsize": 7.5,
            "axes.labelcolor": "#000000",
            "text.color": "#000000",
            "xtick.color": SECONDARY_TEXT_COLOR,
            "ytick.color": SECONDARY_TEXT_COLOR,
            "axes.axisbelow": True,
            "axes.linewidth": 0.0,
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
            "svg.fonttype": "none",
        }
    )
    return family


def scientific_tick(value: float, _: int) -> str:
    if value <= 0 or not math.isfinite(value):
        return ""
    exponent = round(math.log10(value))
    if not math.isclose(value, 10**exponent):
        return ""
    return f"1e{exponent:+03d}"


def select_rank_labels(values: list[int], target: int = 7) -> dict[int, str]:
    if not values:
        return {}
    count = min(target, len(values))
    indices = sorted(
        {
            int(round(position))
            for position in np.linspace(0, len(values) - 1, count)
        }
    )
    return {index: str(values[index]) for index in indices}


def ranked_tick_formatter(labels: dict[int, str]) -> FuncFormatter:
    def formatter(value: float, _: int) -> str:
        index = int(round(value))
        if not math.isclose(value, index, abs_tol=1e-8):
            return ""
        return labels.get(index, "")

    return FuncFormatter(formatter)


def style_ranked_axis(
    ax: plt.Axes,
    *,
    ranked_counts: list[int],
    x_label: str,
) -> None:
    positions = np.arange(len(ranked_counts), dtype=float)
    labels = select_rank_labels(ranked_counts)

    ax.set_facecolor("white")
    ax.set_yscale("log")
    ax.set_xlim(-0.6, len(ranked_counts) - 0.4)
    ax.set_ylim(TIME_AXIS_LOWER_MS, TIME_AXIS_UPPER_MS)
    ax.xaxis.set_major_locator(FixedLocator(positions))
    ax.xaxis.set_major_formatter(ranked_tick_formatter(labels))
    ax.xaxis.set_minor_locator(NullLocator())
    ax.yaxis.set_major_locator(FixedLocator(TIME_TICKS))
    ax.yaxis.set_major_formatter(FuncFormatter(scientific_tick))
    ax.yaxis.set_minor_locator(NullLocator())
    ax.grid(
        axis="x",
        which="major",
        color=GRID_COLOR,
        linewidth=GRID_LINEWIDTH,
    )
    ax.grid(
        axis="y",
        which="major",
        color=GRID_COLOR,
        linewidth=GRID_LINEWIDTH,
    )
    ax.tick_params(
        axis="both",
        which="both",
        length=0,
        colors=SECONDARY_TEXT_COLOR,
        pad=3.0,
    )
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.set_xlabel(x_label)
    ax.set_ylabel("runtime (ms)")


def plot_series(
    ax: plt.Axes,
    *,
    positions: list[int] | np.ndarray,
    values: list[float],
    label: str,
) -> None:
    style = SERIES_STYLES[label]
    common = {
        "x": positions,
        "y": values,
        "s": style["size"],
        "marker": style["marker"],
        "linewidths": MARKER_LINEWIDTH,
        "alpha": 1.0,
        "zorder": 3,
    }
    if style["marker"] == "x":
        ax.scatter(c=style["color"], **common)
    else:
        ax.scatter(
            facecolors="none",
            edgecolors=style["color"],
            **common,
        )


def series_handles(labels: list[str]) -> list[Line2D]:
    handles: list[Line2D] = []
    for label in labels:
        style = SERIES_STYLES[label]
        handles.append(
            Line2D(
                [0],
                [0],
                marker=style["marker"],
                linestyle="none",
                color=style["color"],
                markerfacecolor="none",
                markeredgecolor=style["color"],
                markeredgewidth=MARKER_LINEWIDTH,
                markersize=style["legend_size"],
                label=label,
            )
        )
    return handles


def draw_timeout(ax: plt.Axes) -> None:
    ax.axhline(
        TIMEOUT_MS,
        color=TIMEOUT_COLOR,
        linewidth=0.58,
        linestyle=(0, (4.0, 4.0)),
        zorder=2,
    )


def finish_ranked_figure(
    fig: plt.Figure,
    ax: plt.Axes,
    *,
    legend_labels: list[str],
    output_stem: str,
) -> None:
    ax.legend(
        handles=series_handles(legend_labels),
        loc="lower center",
        bbox_to_anchor=(0.5, 1.015),
        ncol=len(legend_labels),
        frameon=False,
        handletextpad=0.42,
        columnspacing=1.15,
        labelspacing=0.0,
        borderaxespad=0.0,
    )
    fig.subplots_adjust(left=0.10, right=0.995, bottom=0.22, top=0.82)
    save_figure(fig, output_stem)


def render_ranked_endpoint_comparison(
    flat_rows: list[dict[str, str]],
    factored_rows: list[dict[str, str]],
) -> dict[str, object]:
    by_query: dict[str, dict[str, object]] = {}
    for label, rows in (
        ("SPARQLcirc (flat)", flat_rows),
        ("SPARQLcirc (factored)", factored_rows),
    ):
        for row in rows:
            query_id = row["query_id"]
            npcs_ms = float(row["npcs_endpoint_e2e_ms"])
            entry = by_query.setdefault(
                query_id,
                {
                    "query_id": query_id,
                    "npcs_ms": npcs_ms,
                    "times": {},
                },
            )
            if not math.isclose(float(entry["npcs_ms"]), npcs_ms, abs_tol=1e-9):
                raise ValueError(f"conflicting NPCS endpoint times for {query_id}")
            times = entry["times"]
            if not isinstance(times, dict):
                raise TypeError(f"invalid method time map for {query_id}")
            if label in times:
                raise ValueError(f"duplicate {label} endpoint time for {query_id}")
            times[label] = float(row["sparqlcirc_endpoint_e2e_ms"])

    ranked = sorted(
        by_query.values(),
        key=lambda row: (float(row["npcs_ms"]), str(row["query_id"])),
    )
    if not ranked:
        raise ValueError("no plottable endpoint comparison rows")

    positions = np.arange(len(ranked), dtype=int)
    npcs_times = [float(row["npcs_ms"]) for row in ranked]
    npcs_time_labels = [int(round(value)) for value in npcs_times]

    fig, ax = plt.subplots(figsize=REFERENCE_FIGSIZE)
    style_ranked_axis(
        ax,
        ranked_counts=npcs_time_labels,
        x_label="NPCS endpoint time (ms)",
    )
    plot_series(ax, positions=positions, values=npcs_times, label="NPCS")
    for label in ("SPARQLcirc (flat)", "SPARQLcirc (factored)"):
        method_positions: list[int] = []
        method_times: list[float] = []
        for position, row in enumerate(ranked):
            times = row["times"]
            if not isinstance(times, dict):
                raise TypeError(f"invalid method time map at ranked position {position}")
            if label in times:
                method_positions.append(position)
                method_times.append(float(times[label]))
        plot_series(
            ax,
            positions=method_positions,
            values=method_times,
            label=label,
        )
    draw_timeout(ax)
    finish_ranked_figure(
        fig,
        ax,
        legend_labels=[
            "NPCS",
            "SPARQLcirc (flat)",
            "SPARQLcirc (factored)",
        ],
        output_stem="figure_4",
    )

    method_summary: dict[str, object] = {}
    for label in ("SPARQLcirc (flat)", "SPARQLcirc (factored)"):
        ratios: list[float] = []
        for row in ranked:
            times = row["times"]
            if not isinstance(times, dict):
                raise TypeError(f"invalid method time map for {row['query_id']}")
            if label in times:
                ratios.append(float(times[label]) / float(row["npcs_ms"]))
        method_summary[label] = {
            "points": len(ratios),
            "faster_than_npcs": sum(ratio < 1.0 for ratio in ratios),
            "median_over_npcs": float(np.median(ratios)),
        }

    both = sum(
        isinstance(row["times"], dict) and len(row["times"]) == 2 for row in ranked
    )
    return {
        "queries": len(ranked),
        "series_points": {
            "NPCS": len(ranked),
            "SPARQLcirc (flat)": len(flat_rows),
            "SPARQLcirc (factored)": len(factored_rows),
        },
        "availability": {
            "both_sparqlcirc_methods": both,
            "flat_only": len(flat_rows) - both,
            "factored_only": len(factored_rows) - both,
        },
        "ranked_by": "NPCS endpoint_e2e_ms ascending; equal horizontal spacing",
        "npcs_time_min_ms": min(npcs_times),
        "npcs_time_max_ms": max(npcs_times),
        "method_vs_npcs": method_summary,
    }


def log_correlation(rows: list[dict[str, str]]) -> float | None:
    positive = [row for row in rows if int(row["derivations_total"]) > 0]
    if len(positive) < 2:
        return None
    x = np.log10([float(row["derivations_total"]) for row in positive])
    y = np.log10([float(row["endpoint_e2e_ms"]) for row in positive])
    return float(np.corrcoef(x, y)[0, 1])


def render_ranked_derivations(rows: list[dict[str, str]]) -> dict[str, object]:
    by_query: dict[str, dict[str, object]] = {}
    for row in rows:
        query_id = row["query_id"]
        derivations = int(row["derivations_total"])
        entry = by_query.setdefault(
            query_id,
            {
                "query_id": query_id,
                "derivations_total": derivations,
                "times": {},
            },
        )
        if int(entry["derivations_total"]) != derivations:
            raise ValueError(f"conflicting derivation counts for {query_id}")
        times = entry["times"]
        if not isinstance(times, dict):
            raise TypeError(f"invalid time map for {query_id}")
        times[row["method"]] = float(row["endpoint_e2e_ms"])

    ranked = sorted(
        by_query.values(),
        key=lambda row: (int(row["derivations_total"]), str(row["query_id"])),
    )
    counts = [int(row["derivations_total"]) for row in ranked]
    position_by_query = {
        str(row["query_id"]): index for index, row in enumerate(ranked)
    }

    property_path_rows = [
        row for row in rows if row.get("category") == "property_path"
    ]
    if len(property_path_rows) != 5:
        raise ValueError(
            f"expected five Property Path points, found {len(property_path_rows)}"
        )

    # Retain the normal factored circles and recolour only the five vertical
    # query-position rules that correspond to Property Path queries.
    fig_lines, ax_lines = plt.subplots(figsize=REFERENCE_FIGSIZE)
    style_ranked_axis(
        ax_lines,
        ranked_counts=counts,
        x_label="Derivations across all answers",
    )
    for row in property_path_rows:
        ax_lines.axvline(
            position_by_query[row["query_id"]],
            color=str(SERIES_STYLES["SPARQLcirc (factored)"]["color"]),
            linewidth=MARKER_LINEWIDTH,
            alpha=0.82,
            zorder=1.5,
        )
    for method in ("SPARQLcirc (flat)", "SPARQLcirc (factored)"):
        method_rows = [row for row in rows if row["method"] == method]
        plot_series(
            ax_lines,
            positions=[position_by_query[row["query_id"]] for row in method_rows],
            values=[float(row["endpoint_e2e_ms"]) for row in method_rows],
            label=method,
        )
    draw_timeout(ax_lines)
    finish_ranked_figure(
        fig_lines,
        ax_lines,
        legend_labels=["SPARQLcirc (flat)", "SPARQLcirc (factored)"],
        output_stem=(
            "figure_5"
        ),
    )

    method_summary: dict[str, object] = {
        "queries": len(ranked),
        "ranked_by": "derivations_total ascending; equal horizontal spacing",
        "derivation_count_min": min(counts),
        "derivation_count_max": max(counts),
        "property_path_highlight": {
            "queries": [row["query_id"] for row in property_path_rows],
            "vertical_rule": (
                "full-height factored-colour rule at each Property Path query position"
            ),
        },
    }
    for method in ("SPARQLcirc (flat)", "SPARQLcirc (factored)"):
        subset = [row for row in rows if row["method"] == method]
        method_summary[method] = {
            "points": len(subset),
            "cflat_proxy_points": sum(
                row["count_source"] == "C-flat answer-root feeds" for row in subset
            ),
            "cpath_proxy_points": sum(
                row["count_source"] == "C-path answer-root feeds" for row in subset
            ),
            "zero_derivation_points": sum(
                int(row["derivations_total"]) == 0 for row in subset
            ),
            "log10_pearson_r_positive_derivations": log_correlation(subset),
        }
    return method_summary


def save_figure(fig: plt.Figure, stem: str) -> None:
    FIGURES.mkdir(parents=True, exist_ok=True)
    (FIGURES / stem).parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(
        FIGURES / f"{stem}.png",
        dpi=PNG_DPI,
        facecolor="white",
        bbox_inches="tight",
        pad_inches=EXPORT_PAD_INCHES,
        metadata={"Software": None},
    )
    fig.savefig(
        FIGURES / f"{stem}.pdf",
        facecolor="white",
        bbox_inches="tight",
        pad_inches=EXPORT_PAD_INCHES,
        metadata={
            "Title": None,
            "Author": None,
            "Subject": None,
            "Keywords": None,
            "Creator": None,
            "Producer": None,
            "CreationDate": None,
            "ModDate": None,
        },
    )
    plt.close(fig)


def main(argv: Optional[Sequence[str]] = None) -> None:
    global DATA, FIGURES
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    DATA = args.data.resolve()
    output_root = args.out.resolve()
    FIGURES = output_root / "figures"
    FIGURES.mkdir(parents=True, exist_ok=True)
    font = configure_style()
    flat_rows = read_csv(DATA / "npcs_vs_c_flat_endpoint.csv")
    factored_rows = read_csv(DATA / "npcs_vs_c_factored_endpoint.csv")
    derivation_rows = read_csv(DATA / "derivations_vs_c_endpoint_e2e.csv")
    property_path_points = DATA / "property_path_factored_points.csv"
    if (
        property_path_points.exists()
        and not any(row.get("category") == "property_path" for row in derivation_rows)
    ):
        derivation_rows.extend(read_csv(property_path_points))

    results = {
        "schema": "wikidata-scatterplots-v1",
        "font_family": font,
        "style_reference": {
            **STYLE_REFERENCE,
            "implementation": (
                "Matplotlib reconstruction from the two losslessly embedded "
                "Figure 5 raster panels; the public NPCS repository contains "
                "the Wikidata result workbooks but no Figure 5 plot script"
            ),
            "plot_construction": (
                "queries sorted by the x metric and placed at equal horizontal "
                "intervals; actual metric values appear only at selected ticks; "
                "one vertical grid rule per query; method-encoded series"
            ),
            "series_styles": {
                label: {
                    "marker": style["marker"],
                    "color": style["color"],
                    "open_marker": style["marker"] != "x",
                }
                for label, style in SERIES_STYLES.items()
            },
            "major_grid": GRID_COLOR,
            "major_grid_linewidth_pt": GRID_LINEWIDTH,
            "marker_linewidth_pt": MARKER_LINEWIDTH,
            "palette_reference": "the shared paper-figure coral/teal/magenta palette",
            "legend_layout": "single row above the plotting area",
            "export_bbox": "tight",
            "export_pad_inches": EXPORT_PAD_INCHES,
            "png_dpi": PNG_DPI,
            "density_compensation": (
                "lighter, thinner grid and smaller, thinner markers compensate "
                "for 87--102 ranked query positions within one paper-width panel"
            ),
            "secondary_text": SECONDARY_TEXT_COLOR,
            "font_sizes_pt": {
                "base": 7.4,
                "axis_label": 8.3,
                "tick_label": 7.0,
                "legend": 7.5,
            },
        },
        "timeout_ms": TIMEOUT_MS,
        "timeout_rendering": (
            "red dashed deadline rule only; unsuccessful records are not "
            "substituted with the deadline or plotted as successful points"
        ),
        "category_encoding": (
            "none; query categories are pooled, matching NPCS Figure 5, and "
            "marker/color encode methods"
        ),
        "endpoint_metric": (
            "endpoint_e2e_ms: query read/rewrite through complete NPCS TSV drain; "
            "CircuitRun plan/CONSTRUCT execution through streamed circuit persistence"
        ),
        "endpoint_rank_metric": (
            "NPCS endpoint_e2e_ms ascending; queries remain equally spaced and "
            "selected x ticks show rounded milliseconds"
        ),
        "derivation_plot_metric": (
            "SPARQLcirc endpoint_e2e_ms: CircuitRun start through plan/CONSTRUCT "
            "execution, streamed response handling, and atomic circuit persistence; "
            "excludes offline processing and PQE"
        ),
        "derivation_count_metric": (
            "NPCS outer-sum operands where a retained provenance stream exists; "
            "eight fallback queries use distinct C-flat feeds into answer roots; "
            "five property-path queries use distinct C-path feeds into answer roots; "
            "proxy rows use their normal method marker"
        ),
        "figure_01": render_ranked_endpoint_comparison(
            flat_rows,
            factored_rows,
        ),
        "figure_02": render_ranked_derivations(derivation_rows),
    }
    with (output_root / "figure_manifest.json").open("w", encoding="utf-8") as handle:
        json.dump(results, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    print(json.dumps(results, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
