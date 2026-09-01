#!/usr/bin/env python3
"""Render the WatDiv 10M endpoint matrix in SPARQLprov Figure 2 style.

The plot intentionally adapts the visual grammar of the official SPARQLprov
Figure 2 reproduction script while retaining the measurements and terminology
of the SPARQLcirc WatDiv 10M run:

* one facet row per endpoint engine;
* five bars per ordinary template: B, R, N, C-flat, and C-factored;
* two rightmost path-template groups, P+ and P*, with B and C-path bars;
* the displayed C-factored series comes from the improved raw method
  ``C-factorised`` (also called C-factored_v2 elsewhere in the report);
* formal 1+5 inputs use the median endpoint time; explicitly named mean
  columns from earlier result summaries remain accepted;
* a method/template with zero successful runs is represented, as in the
  SPARQLprov plot, by a full-height bar ending on the 600 s timeout line.

The script reads an explicitly supplied normalized CSV and writes all generated
artifacts to an explicitly supplied result directory.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
from typing import Dict, List, Mapping, Sequence, Tuple

import matplotlib

matplotlib.use("Agg")
import matplotlib.font_manager as font_manager
import matplotlib.pyplot as plt
from matplotlib.collections import LineCollection
from matplotlib.patches import Patch
from matplotlib.ticker import FixedLocator, LogFormatterMathtext, NullFormatter


SCRIPT_DIR = Path(__file__).resolve().parent

ENGINE_ORDER = (
    "graphdb-10.7.6",
    "fuseki-5.4.0",
    "oxigraph-0.5.9",
)
ENGINE_LABELS = {
    "graphdb-10.7.6": "GraphDB 10.7.6",
    "fuseki-5.4.0": "Fuseki 5.4.0",
    "oxigraph-0.5.9": "Oxigraph 0.5.9",
}

# Figure 2 orders the original WatDiv templates C, F, L, O, S.  The M templates
# are specific to this experiment and are appended without disturbing that order.
NONPATH_TEMPLATE_ORDER = (
    "C1", "C2", "C3",
    "F1", "F2", "F3", "F4", "F5",
    "L1", "L2", "L3", "L4", "L5",
    "O1", "O2", "O3", "O4", "O5",
    "S1", "S2", "S3", "S4", "S5", "S6", "S7",
    "M1", "M2", "M3", "M4", "M5",
)
PATH_TEMPLATE_ORDER = ("P-plus", "P-star")
TEMPLATE_ORDER = NONPATH_TEMPLATE_ORDER + PATH_TEMPLATE_ORDER
TEMPLATE_LABELS = {
    "P-plus": "P+",
    "P-star": "P*",
}

NONPATH_DISPLAY_METHODS = ("B", "R", "N", "C-flat", "C-factored")
PATH_DISPLAY_METHODS = ("B", "C-path")
DISPLAY_METHODS = NONPATH_DISPLAY_METHODS + ("C-path",)

# Preserve one bar width across the figure.  The two-bar path groups need
# different center distances from a five-bar group and from another two-bar
# group in order to leave the same visible edge-to-edge gap.
GROUP_WIDTH = 0.90
BAR_WIDTH = GROUP_WIDTH / len(NONPATH_DISPLAY_METHODS)
BAR_EDGE_WIDTH = 0.25
PATH_TO_PATH_CENTER_SPACING = 0.60
PATH_GROUP_EDGE_GAP = round(
    PATH_TO_PATH_CENTER_SPACING - len(PATH_DISPLAY_METHODS) * BAR_WIDTH,
    10,
)
NONPATH_TO_PATH_CENTER_SPACING = round(
    len(NONPATH_DISPLAY_METHODS) * BAR_WIDTH / 2.0
    + PATH_GROUP_EDGE_GAP
    + len(PATH_DISPLAY_METHODS) * BAR_WIDTH / 2.0,
    10,
)
DISPLAY_LABELS = {
    "B": "Base",
    "R": "Reification",
    "N": "NPCS",
    "C-flat": "SPARQLcirc (flat)",
    "C-factored": "SPARQLcirc (factored)",
    "C-path": "SPARQLcirc (path)",
}

# Exact colors from the official SPARQLprov Figure 2 R/ggplot script.  The first
# three form its Fuseki B/R/P sequence; the final two are its Virtuoso R/P pair.
COLORS = {
    "B": "#ECE7F2",          # SPARQLprov: Fuseki B
    "R": "#A6BDDB",          # SPARQLprov: Fuseki R
    "N": "#2B8CBE",          # SPARQLprov: Fuseki P
    "C-flat": "#FDBB84",     # SPARQLprov: Virtuoso R
    "C-factored": "#E34A33", # SPARQLprov: Virtuoso P
    # C-path is not a separate legend series; reuse the factored SPARQLcirc red.
    "C-path": "#E34A33",
}

FIGURE_STEM = "figure_3"
FIGURE_FONT_SCALE = 1.5
FIGURE_HEIGHT_SCALE = 1.5
DISPLAY_TO_RAW_METHOD = {
    "B": "B",
    "R": "R",
    "N": "N",
    "C-flat": "C-flat",
    "C-factored": "C-factorised",
    "C-path": "C-path",
}


def methods_for_template(template: str) -> Tuple[str, ...]:
    if template in PATH_TEMPLATE_ORDER:
        return PATH_DISPLAY_METHODS
    return NONPATH_DISPLAY_METHODS


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--timeout-s", type=float, default=600.0)
    parser.add_argument(
        "--formats",
        default="png,pdf",
        help="Comma-separated output formats supported by Matplotlib.",
    )
    return parser.parse_args()


def read_csv(path: Path) -> List[Dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def finite_number(value: str, *, field: str, key: Tuple[str, str, str]) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"missing or invalid {field} for {key}: {value!r}") from error
    if not math.isfinite(parsed):
        raise ValueError(f"non-finite {field} for {key}: {value!r}")
    return parsed


def integer(value: str, *, field: str, key: Tuple[str, str, str]) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"missing or invalid {field} for {key}: {value!r}") from error
    if parsed < 0:
        raise ValueError(f"negative {field} for {key}: {parsed}")
    return parsed


def load_plot_values(
    path: Path,
) -> Tuple[Dict[Tuple[str, str, str], float], str]:
    """Load and validate the complete engine/method/template plot matrix."""
    if not path.is_file():
        raise FileNotFoundError(f"missing local plot table: {path}")

    rows = read_csv(path)
    if not rows:
        raise ValueError(f"empty local plot table: {path}")

    required_fields = {
        "engine",
        "template",
        "display_method",
        "raw_method",
        "expected_instances",
        "measured_endpoint_ok",
        "endpoint_timeout",
        "plot_kind",
    }
    median_fields = {"successful_median_ms", "plotted_successful_median_ms"}
    mean_fields = {"successful_mean_ms", "plotted_successful_mean_ms"}
    if median_fields.issubset(rows[0]):
        value_fields = tuple(sorted(median_fields))
        statistic = "median"
    elif mean_fields.issubset(rows[0]):
        value_fields = tuple(sorted(mean_fields))
        statistic = "mean"
    else:
        required_fields |= median_fields
        value_fields = ("successful_median_ms", "plotted_successful_median_ms")
        statistic = "median"
    missing_fields = required_fields.difference(rows[0])
    if missing_fields:
        raise ValueError(
            f"local plot table is missing columns: {sorted(missing_fields)}"
        )

    values: Dict[Tuple[str, str, str], float] = {}
    for row in rows:
        engine = row["engine"]
        template = row["template"]
        method = row["display_method"]
        key = (engine, method, template)

        if key in values:
            raise ValueError(f"duplicate local plot row: {key}")
        if engine not in ENGINE_ORDER:
            raise ValueError(f"unknown engine in local plot table: {engine!r}")
        if template not in TEMPLATE_ORDER:
            raise ValueError(f"unknown template in local plot table: {template!r}")
        if method not in DISPLAY_METHODS:
            raise ValueError(f"unknown display method in local plot table: {method!r}")
        if row["raw_method"] != DISPLAY_TO_RAW_METHOD[method]:
            raise ValueError(
                f"unexpected raw method for {key}: {row['raw_method']!r}"
            )

        expected = integer(
            row["expected_instances"], field="expected_instances", key=key
        )
        measured = integer(
            row["measured_endpoint_ok"], field="measured_endpoint_ok", key=key
        )
        timed_out = integer(
            row["endpoint_timeout"], field="endpoint_timeout", key=key
        )
        if expected <= 0:
            raise ValueError(f"non-positive expected count for {key}: {expected}")
        if measured + timed_out != expected:
            raise ValueError(
                f"incomplete endpoint accounting for {key}: measured={measured}, "
                f"timeout={timed_out}, expected={expected}"
            )

        if measured:
            successful_field = "successful_%s_ms" % statistic
            plotted_field = "plotted_successful_%s_ms" % statistic
            successful = finite_number(
                row[successful_field], field=successful_field, key=key
            )
            plotted = finite_number(
                row[plotted_field],
                field=plotted_field,
                key=key,
            )
            if successful <= 0 or plotted <= 0:
                raise ValueError(f"non-positive successful runtime for {key}")
            if not math.isclose(successful, plotted, rel_tol=1e-12, abs_tol=1e-12):
                raise ValueError(f"plotted value differs from successful {statistic} for {key}")
            if row["plot_kind"] not in ("successful-mean-bar", "successful-median-bar"):
                raise ValueError(f"unexpected plot kind for successful row {key}")
            values[key] = plotted
        else:
            if row[value_fields[0]].strip() or row[value_fields[1]].strip():
                raise ValueError(f"zero-success row contains a runtime for {key}")
            if row["plot_kind"] != "timeout-bar":
                raise ValueError(f"unexpected plot kind for zero-success row {key}")
            values[key] = math.nan

    expected_keys = {
        (engine, method, template)
        for engine in ENGINE_ORDER
        for template in TEMPLATE_ORDER
        for method in methods_for_template(template)
    }
    actual_keys = set(values)
    if actual_keys != expected_keys:
        missing = sorted(expected_keys - actual_keys)
        extra = sorted(actual_keys - expected_keys)
        raise ValueError(f"incomplete local plot matrix; missing={missing}, extra={extra}")
    return values, statistic


def choose_times_font() -> str:
    for family in (
        "Times New Roman",
        "Times",
        "Nimbus Roman",
        "Nimbus Roman No9 L",
        "Liberation Serif",
    ):
        try:
            font_manager.findfont(
                font_manager.FontProperties(family=family), fallback_to_default=False
            )
        except ValueError:
            continue
        return family
    return "DejaVu Serif"


def configure_matplotlib(font_family: str) -> None:
    plt.rcParams.update(
        {
            "font.family": font_family,
            "font.size": 11.0 * FIGURE_FONT_SCALE,
            "axes.labelsize": 11.0 * FIGURE_FONT_SCALE,
            "axes.titlesize": 9.4 * FIGURE_FONT_SCALE,
            "xtick.labelsize": 8.8 * FIGURE_FONT_SCALE,
            "ytick.labelsize": 8.8 * FIGURE_FONT_SCALE,
            "legend.fontsize": 9.2 * FIGURE_FONT_SCALE,
            "axes.axisbelow": True,
            "axes.linewidth": 0.0,
            "mathtext.fontset": "stix",
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
            "svg.fonttype": "none",
        }
    )


def render_figure(
    values: Mapping[Tuple[str, str, str], float],
    *,
    timeout_ms: float,
    font_family: str,
) -> plt.Figure:
    configure_matplotlib(font_family)

    # Keep the established width while increasing the height so that text
    # remains body-sized when the figure is placed at page width.
    figure_width = 10.0 * len(TEMPLATE_ORDER) / len(NONPATH_TEMPLATE_ORDER)
    fig, axes = plt.subplots(
        len(ENGINE_ORDER),
        1,
        figsize=(figure_width, figure_width / 3.0 * FIGURE_HEIGHT_SCALE),
        sharex=True,
        sharey=True,
    )

    nonpath_x = [float(index) for index in range(len(NONPATH_TEMPLATE_ORDER))]
    first_path_x = nonpath_x[-1] + NONPATH_TO_PATH_CENTER_SPACING
    x = nonpath_x + [
        first_path_x,
        first_path_x + PATH_TO_PATH_CENTER_SPACING,
    ]
    template_offsets = {}
    for template in TEMPLATE_ORDER:
        methods = methods_for_template(template)
        template_offsets[template] = {
            method: (index - (len(methods) - 1) / 2.0) * BAR_WIDTH
            for index, method in enumerate(methods)
        }

    major_y = (1e1, 1e3, 1e5)
    minor_y = (1e0, 1e2, 1e4)
    formatter = LogFormatterMathtext(base=10)

    for ax, engine in zip(axes, ENGINE_ORDER):
        for method in DISPLAY_METHODS:
            bar_positions = []
            bar_heights = []
            for position, template in zip(x, TEMPLATE_ORDER):
                if method not in methods_for_template(template):
                    continue
                offset = template_offsets[template][method]
                value = values[(engine, method, template)]
                if math.isfinite(value):
                    bar_positions.append(position + offset)
                    bar_heights.append(value)
                else:
                    bar_positions.append(position + offset)
                    bar_heights.append(timeout_ms)
            if bar_positions:
                ax.bar(
                    bar_positions,
                    bar_heights,
                    width=BAR_WIDTH,
                    color=COLORS[method],
                    edgecolor="none",
                    linewidth=0.0,
                    zorder=3,
                )

        # Draw outlines once per template group.  Drawing a complete rectangle
        # around every touching bar makes PDF renderers paint coincident shared
        # edges twice, so those separators look heavier than in the PNG.  The
        # single-pass outline below is geometrically equivalent but contains
        # only one vector segment at each shared boundary.
        outline_segments = []
        for position, template in zip(x, TEMPLATE_ORDER):
            methods = methods_for_template(template)
            centers = [
                position + template_offsets[template][method]
                for method in methods
            ]
            heights = []
            for method in methods:
                value = values[(engine, method, template)]
                heights.append(value if math.isfinite(value) else timeout_ms)

            left_edges = [center - BAR_WIDTH / 2.0 for center in centers]
            right_edges = [center + BAR_WIDTH / 2.0 for center in centers]
            outline_segments.extend(
                [
                    ((left, height), (right, height))
                    for left, right, height in zip(left_edges, right_edges, heights)
                ]
            )

            boundaries = [left_edges[0], *right_edges]
            boundary_heights = [
                heights[0],
                *[
                    max(left_height, right_height)
                    for left_height, right_height in zip(heights, heights[1:])
                ],
                heights[-1],
            ]
            outline_segments.extend(
                [
                    ((boundary, 1.0), (boundary, height))
                    for boundary, height in zip(boundaries, boundary_heights)
                ]
            )

        ax.add_collection(
            LineCollection(
                outline_segments,
                colors="#000000",
                linewidths=BAR_EDGE_WIDTH,
                capstyle="butt",
                joinstyle="miter",
                zorder=3.2,
            )
        )

        ax.set_yscale("log")
        ax.set_ylim(1.0, timeout_ms * 1.5)
        ax.set_xlim(-0.60, x[-1] + 0.40)
        ax.yaxis.set_major_locator(FixedLocator(major_y))
        ax.yaxis.set_major_formatter(formatter)
        ax.yaxis.set_minor_locator(FixedLocator(minor_y))
        ax.yaxis.set_minor_formatter(NullFormatter())
        ax.grid(which="major", axis="both", color="#EBEBEB", linewidth=0.55)
        ax.grid(which="minor", axis="y", color="#EBEBEB", linewidth=0.35)
        ax.axhline(
            timeout_ms,
            color="#000000",
            linestyle=(0, (1.0, 3.0)),
            linewidth=0.8,
            zorder=4,
        )
        ax.set_title(ENGINE_LABELS[engine], pad=1.5, color="#222222")
        ax.tick_params(axis="both", which="both", length=0, colors="#4D4D4D")
        for spine in ax.spines.values():
            spine.set_visible(False)

    axes[-1].set_xticks(
        x,
        [TEMPLATE_LABELS.get(template, template) for template in TEMPLATE_ORDER],
    )
    axes[-1].tick_params(axis="x", pad=2.0)

    handles = [
        Patch(
            facecolor=COLORS[method],
            edgecolor="#000000",
            linewidth=BAR_EDGE_WIDTH,
            label=DISPLAY_LABELS[method],
        )
        for method in NONPATH_DISPLAY_METHODS
    ]
    fig.legend(
        handles=handles,
        labels=[DISPLAY_LABELS[method] for method in NONPATH_DISPLAY_METHODS],
        ncol=len(NONPATH_DISPLAY_METHODS),
        loc="upper center",
        bbox_to_anchor=(0.53, 0.995),
        frameon=False,
        handlelength=1.35,
        handleheight=1.0,
        columnspacing=1.05,
        handletextpad=0.45,
        borderaxespad=0.0,
    )
    fig.text(0.018, 0.49, "runtime (ms)", rotation=90, ha="center", va="center")
    fig.text(0.535, 0.040, "query template", ha="center", va="center")
    fig.subplots_adjust(
        left=0.073,
        right=0.997,
        bottom=0.165,
        top=0.830,
        hspace=0.42,
    )
    return fig


def main() -> None:
    args = parse_args()
    if not math.isfinite(args.timeout_s) or args.timeout_s <= 0:
        raise ValueError("--timeout-s must be a positive finite number")

    formats = tuple(
        item.strip().lower() for item in args.formats.split(",") if item.strip()
    )
    if not formats:
        raise ValueError("--formats must contain at least one output format")

    data_path = args.data.resolve()
    out_root = args.out.resolve()
    figures_dir = out_root / "figures"
    figures_dir.mkdir(parents=True, exist_ok=True)

    timeout_ms = args.timeout_s * 1000.0
    values, statistic = load_plot_values(data_path)
    font_family = choose_times_font()
    figure = render_figure(values, timeout_ms=timeout_ms, font_family=font_family)

    generated: List[str] = []
    metadata = {
        "Title": "WatDiv 10M endpoint runtimes by engine and query template",
        "Creator": Path(__file__).name,
        "Subject": (
            "SPARQLprov Figure 2 style; C-factored uses C-factorised v2 data; "
            "P+/P* compare B with C-path"
        ),
    }
    for extension in formats:
        filename = {
            "png": f"{FIGURE_STEM}.png",
            "pdf": "figure_3.pdf",
        }.get(extension, f"{FIGURE_STEM}.{extension}")
        target = figures_dir / filename
        save_options: Dict[str, object] = {"facecolor": "white"}
        if extension == "png":
            save_options["dpi"] = 600
        if extension == "pdf":
            save_options["metadata"] = metadata
        figure.savefig(target, **save_options)
        generated.append(target.relative_to(out_root).as_posix())
    plt.close(figure)

    try:
        plot_data_reference = data_path.relative_to(out_root).as_posix()
    except ValueError:
        plot_data_reference = str(data_path)

    manifest = {
        "schema": "watdiv10m-sparqlprov-figure2-style-v5",
        "self_contained_plot_input": True,
        "plot_data": plot_data_reference,
        "timeout_s": args.timeout_s,
        "runtime_aggregation": (
            "%s over successful endpoint measurements, as named by the input table"
            % statistic
        ),
        "zero_success_encoding": "full-height bar ending on the timeout line",
        "display_to_raw_method": {
            DISPLAY_LABELS[method]: raw_method
            for method, raw_method in DISPLAY_TO_RAW_METHOD.items()
        },
        "engines": list(ENGINE_ORDER),
        "engine_display_labels": ENGINE_LABELS,
        "templates": list(TEMPLATE_ORDER),
        "template_display_labels": TEMPLATE_LABELS,
        "path_group_spacing": PATH_TO_PATH_CENTER_SPACING,
        "ordinary_to_first_path_group_spacing": NONPATH_TO_PATH_CENTER_SPACING,
        "group_edge_gap": PATH_GROUP_EDGE_GAP,
        "bar_width": BAR_WIDTH,
        "bar_outline": (
            "single-pass vector boundaries; each shared edge is drawn once"
        ),
        "colors": COLORS,
        "legend_methods": list(NONPATH_DISPLAY_METHODS),
        "path_color_reuse": {
            "raw_method": "C-path",
            "same_color_as": "C-factored",
            "separate_legend_entry": False,
        },
        "font_family": font_family,
        "font_scale": FIGURE_FONT_SCALE,
        "height_scale": FIGURE_HEIGHT_SCALE,
        "source_style": (
            "SPARQLprov official experiment-1.Rmd, Figure 2: Times, minimal grid, "
            "log10 milliseconds, top legend, black dotted timeout"
        ),
        "generated": generated,
    }
    manifest_path = out_root / "figure_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    print(f"verified plot input: {plot_data_reference}")
    print(f"font: {font_family}")
    successful_bars = sum(math.isfinite(value) for value in values.values())
    timeout_bars = len(values) - successful_bars
    print(f"method/template slots: {len(values)}")
    print(f"successful-{statistic} bars: {successful_bars}")
    print(f"zero-success timeout bars: {timeout_bars}")
    for item in generated:
        print(f"wrote {item}")
    print(f"wrote {manifest_path.relative_to(out_root)}")


if __name__ == "__main__":
    main()
