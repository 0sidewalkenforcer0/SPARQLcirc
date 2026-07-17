"""Full-dimension construction figures (drafts r9_2 structure), real B/R/N/C matrix.

Reads the R9 construction matrix CSV produced by
``reference/paper/paper_construction_matrix.py`` (one row per
engine × scale × class/template × method cell) and renders, PER ENGINE, the drafts'
r9_2 layout: two stacked scale panels (10M / 100M), the WatDiv query templates on the
x-axis, and grouped B/R/N/C bars — exactly the SPARQLprov/NPCS structure, with real data.

Cells not yet in the CSV are left as gaps; a scale/engine with no rows at all shows the
drafts' ``DATA PENDING`` mark, so the figure carries the full matrix and fills as the
background run advances. Timeout/too-large cells are drawn at the timeout rule.

    PCM_MATRIX_CSV=<path> python3 make_matrix_figures.py     # else newest under artifacts/r9/
"""

import csv
import glob
import os

import numpy as np

import figstyle as fs
from figstyle import SP_BASE, SP_REIFIED, SP_NPCS, SP_CIRCUIT, plt

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
OUT = os.path.join(HERE, "figures", "final")

ENGINE_TITLE = {"graphdb": "GraphDB", "oxigraph": "Oxigraph",
                "qlever": "QLever", "millenniumdb": "MillenniumDB"}
ALL_ENGINES = ["graphdb", "oxigraph", "qlever", "millenniumdb"]
SCALES = ["10M", "100M"]
CLASS_ORDER = ["C", "F", "L", "S", "O", "M"]  # x-axis grouping, drafts order
METHODS = [("B", SP_BASE, "B: base query"),
           ("R", SP_REIFIED, "R: reified base"),
           ("N", SP_NPCS, "N: NPCS"),
           ("C", SP_CIRCUIT, "C: SPARQLcirc")]
CREATOR = "sparqlcirc/presentation/make_matrix_figures.py"


MANIFEST = os.path.join(ROOT, "reference", "paper", "workload_manifest.csv")


def find_csv():
    if os.environ.get("PCM_MATRIX_CSV"):
        return os.environ["PCM_MATRIX_CSV"]
    cands = glob.glob(os.path.join(ROOT, "artifacts", "r9", "*", "construction_brnc.csv"))
    return max(cands, key=os.path.getmtime) if cands else None


def full_templates():
    """The complete (class, template) x-axis from the workload manifest, so the figure
    always shows the full 25-template structure and fills in as the run advances."""
    seen = {}
    if os.path.exists(MANIFEST):
        with open(MANIFEST, encoding="utf-8", newline="") as fh:
            for r in csv.DictReader(fh):
                seen[(r["class"], r["template"])] = True
    keys = list(seen) or [(c, f"{c}{n}") for c in CLASS_ORDER for n in range(1, 6)]
    def sort_key(k):
        cls, tmpl = k
        num = int("".join(ch for ch in tmpl if ch.isdigit()) or 0)
        ci = CLASS_ORDER.index(cls) if cls in CLASS_ORDER else len(CLASS_ORDER)
        return (ci, num)
    return sorted(set(keys), key=sort_key)


def _f(v):
    try:
        return float(v) if v not in (None, "") else None
    except ValueError:
        return None


def load(path):
    """-> {engine: {scale: {(class,template): {method: {...}}}}} and timeout_s.

    Each method dict carries median_ms, status, gates, edges, npcs_tokens, answers so the
    same matrix feeds the construction (time), storage (size ratio), and data-scale figures.
    """
    data, timeout_s = {}, 300.0
    if not path or not os.path.exists(path):
        return data, timeout_s
    with open(path, encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh):
            eng, sc = r["engine"], r["scale"]
            key = (r["class"], r["template"])
            cell = data.setdefault(eng, {}).setdefault(sc, {}).setdefault(key, {})
            cell[r["method"]] = {
                "median": _f(r.get("median_ms")), "status": r.get("status", ""),
                "gates": _f(r.get("gates")), "edges": _f(r.get("edges")),
                "npcs_tokens": _f(r.get("npcs_token_occurrences")), "answers": _f(r.get("answers")),
            }
            timeout_s = max(timeout_s, _f(r.get("timeout_s")) or 0) or timeout_s
    return data, timeout_s


def draw_scale_panel(ax, templates, cells, timeout_ms):
    x = np.arange(len(templates))
    width = 0.19
    offsets = (np.arange(4) - 1.5) * width
    for (method, color, _label), off in zip(METHODS, offsets):
        heights, xs, to_x = [], [], []
        for i, key in enumerate(templates):
            val = cells.get(key, {}).get(method)
            if not val:
                continue
            median, status = val["median"], val["status"]
            if median is not None and status == "ok":
                heights.append(median); xs.append(i + off)
            elif status and status != "ok":
                to_x.append(i + off)   # timeout / too-large / error -> drawn at the rule
        if xs:
            ax.bar(xs, heights, width, color=color, edgecolor="white", linewidth=0.25, zorder=3)
        if to_x:
            ax.scatter(to_x, [timeout_ms] * len(to_x), marker="v", s=10, color=color, zorder=4)
    ax.set_yscale("log")
    ax.set_ylim(10, max(timeout_ms * 1.6, 500_000))
    ax.axhline(timeout_ms, color="#555555", linestyle=fs.TIMEOUT_LS, linewidth=0.8, zorder=2)
    ax.set_xlim(-0.7, len(templates) - 0.3)
    ax.set_xticks(x, [t for _c, t in templates])
    ax.set_ylabel("Runtime (ms)")
    fs.frame(ax, grid_axis="both")


def fig_construction_engine(engine, data, timeout_ms, templates):
    scale_map = data.get(engine, {})
    fig, axes = plt.subplots(2, 1, figsize=(fs.FIG_WIDTH, 3.9), sharex=True)
    for ax, scale in zip(axes, SCALES):
        cells = scale_map.get(scale, {})
        if cells:
            draw_scale_panel(ax, templates, cells, timeout_ms)
        else:
            ax.set_yscale("log"); ax.set_ylim(10, 500_000)
            ax.set_xticks(np.arange(len(templates)), [t for _c, t in templates])
            ax.set_ylabel("Runtime (ms)"); fs.frame(ax)
            fs.pending(ax, f"{scale} — DATA PENDING", y=0.5)
        ax.set_title(f"WatDiv {scale}", fontsize=7.2, pad=2.5)
    axes[0].tick_params(axis="x", labelbottom=False)
    axes[1].set_xlabel("Query template")
    fs.suptitle(fig, ENGINE_TITLE.get(engine, engine), y=0.985)
    handles = [fs.patch(c, lbl) for _m, c, lbl in METHODS] + [fs.timeout_handle("timeout / too-large")]
    labels = [h.get_label() for h in handles]
    fs.top_legend(fig, handles, labels, ncol=5, y=0.945, columnspacing=1.2, handlelength=1.6)
    n_cells = sum(len(v) for v in scale_map.values())
    fs.footer(fig, f"Grouped B/R/N/C construction time per template ({n_cells} cells filled); "
                   "timeouts at the rule, missing cells fill as the run advances.")
    fig.subplots_adjust(left=0.09, right=0.995, bottom=0.135, top=0.82, hspace=0.24)
    fs.save(fig, f"result_r9_2_construction_{engine}", OUT, creator=CREATOR)


def fig_storage_engine(engine, data, templates):
    """Full-dimension storage/sharing: NPCS tokens / SPARQLcirc (gates+edges) per template.

    ratio > 1 -> the shared circuit is smaller (sharing win); < 1 -> larger (selective
    counterexample). Per engine, both scales, all templates (the drafts r9_3 structure).
    """
    scale_map = data.get(engine, {})
    fig, axes = plt.subplots(2, 1, figsize=(fs.FIG_WIDTH, 3.7), sharex=True)
    for ax, scale in zip(axes, SCALES):
        cells = scale_map.get(scale, {})
        xs, ys = [], []
        for i, key in enumerate(templates):
            n = cells.get(key, {}).get("N")
            c = cells.get(key, {}).get("C")
            if n and c and n["status"] == "ok" and c["status"] == "ok" and n["npcs_tokens"] and c["gates"]:
                denom = (c["gates"] or 0) + (c["edges"] or 0)
                if denom > 0:
                    xs.append(i); ys.append(n["npcs_tokens"] / denom)
        if xs:
            ax.bar(xs, ys, 0.6, color=SP_CIRCUIT, edgecolor="white", linewidth=0.25, zorder=3)
            ax.axhline(1.0, color="#555555", linestyle=fs.TIMEOUT_LS, linewidth=0.8)
            ax.set_yscale("log"); ax.set_ylim(0.3, max(30, max(ys) * 1.5))
        else:
            ax.set_yscale("log"); ax.set_ylim(0.3, 30)
            fs.pending(ax, f"{scale} — DATA PENDING", y=0.5)
        ax.set_xlim(-0.7, len(templates) - 0.3)
        ax.set_xticks(range(len(templates)), [t for _c, t in templates])
        ax.set_ylabel("NPCS / circuit size")
        ax.set_title(f"WatDiv {scale}", fontsize=7.2, pad=2.5)
        fs.frame(ax)
    axes[0].tick_params(axis="x", labelbottom=False)
    axes[1].set_xlabel("Query template")
    fs.suptitle(fig, f"{ENGINE_TITLE.get(engine, engine)} — representation size (NPCS vs shared circuit)", y=0.985, fontsize=9.0)
    fs.footer(fig, "Structural token ratio per template; > 1 means the shared circuit is smaller (sharing win), < 1 a selective counterexample.")
    fig.subplots_adjust(left=0.09, right=0.995, bottom=0.135, top=0.86, hspace=0.24)
    fs.save(fig, f"result_r9_3_storage_{engine}", OUT, creator=CREATOR)


def fig_datascale_engine(engine, data):
    """Full-dimension data-scale: construct time + circuit size vs WatDiv scale, per class.

    Aggregates the C (SPARQLcirc) cells per query class at each scale. Lines appear once
    both scales are present; RSS stays pending (not in the construction matrix).
    """
    scale_map = data.get(engine, {})
    scales_present = [s for s in SCALES if scale_map.get(s)]
    xvals = {"10M": 10.0, "100M": 100.0}
    fig, axes = plt.subplots(1, 3, figsize=(fs.FIG_WIDTH, 2.55))
    for ci, cls in enumerate(CLASS_ORDER):
        color, marker = fs.SERIES[ci], fs.SERIES_MARKERS[ci]
        xs, t_ys, s_ys = [], [], []
        for sc in scales_present:
            cvals = [v["C"] for k, v in scale_map[sc].items() if k[0] == cls and v.get("C") and v["C"]["status"] == "ok"]
            if not cvals:
                continue
            times = [c["median"] for c in cvals if c["median"] is not None]
            sizes = [(c["gates"] or 0) + (c["edges"] or 0) for c in cvals if c["gates"]]
            if times:
                xs.append(xvals[sc]); t_ys.append(sum(times) / len(times)); s_ys.append(sum(sizes) / max(len(sizes), 1))
        if xs:
            axes[0].plot(xs, t_ys, color=color, marker=marker, label=cls)
            axes[1].plot(xs, s_ys, color=color, marker=marker, label=cls)
    fs.light_log_axis(axes[0], "WatDiv triples (millions)", "Construct time (ms, mean/class)", "Construction")
    fs.light_log_axis(axes[1], "WatDiv triples (millions)", "Gates + edges (mean/class)", "Circuit growth")
    fs.light_log_axis(axes[2], "WatDiv triples (millions)", "Builder peak RSS (MiB)", "Client memory")
    fs.pending(axes[2], "RSS\nDATA PENDING", y=0.6)
    for i, ax in enumerate(axes):
        ax.set_xticks([xvals[s] for s in SCALES], SCALES)
        fs.panel_label(ax, i, x=-0.22)
        if len(scales_present) < 2 and i < 2:
            fs.pending(ax, "2nd scale\nPENDING", y=0.3)
    fs.suptitle(fig, f"{ENGINE_TITLE.get(engine, engine)} — data-scale construction (C, per class)", y=0.99, fontsize=9.0)
    handles, labels = axes[0].get_legend_handles_labels()
    if handles:
        fs.top_legend(fig, handles, labels, ncol=6, y=0.91, columnspacing=1.2)
    fs.footer(fig, "Mean SPARQLcirc construct time / circuit size per query class vs WatDiv scale; lines close when 100M lands.")
    fig.subplots_adjust(left=0.085, right=0.995, bottom=0.20, top=0.75, wspace=0.34)
    fs.save(fig, f"result_r9_2c_data_scale_{engine}", OUT, creator=CREATOR)


def main():
    path = find_csv()
    data, timeout_s = load(path)
    timeout_ms = timeout_s * 1000.0
    templates = full_templates()
    print(f"matrix CSV: {path or '(none yet)'}  ({len(templates)} templates on x-axis)")
    for engine in ALL_ENGINES:
        cells = sum(len(v) for v in data.get(engine, {}).values())
        fig_construction_engine(engine, data, timeout_ms, templates)
        fig_storage_engine(engine, data, templates)
        fig_datascale_engine(engine, data)
        print(f"  {engine}: {cells} template-cells filled")


if __name__ == "__main__":
    main()
