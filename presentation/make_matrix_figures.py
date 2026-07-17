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


def load(path):
    """-> {engine: {scale: {(class,template): {method: (median_ms, status)}}}} and timeout_s."""
    data, timeout_s = {}, 300.0
    if not path or not os.path.exists(path):
        return data, timeout_s
    with open(path, encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh):
            eng, sc = r["engine"], r["scale"]
            key = (r["class"], r["template"])
            cell = data.setdefault(eng, {}).setdefault(sc, {}).setdefault(key, {})
            try:
                median = float(r["median_ms"]) if r["median_ms"] else None
            except ValueError:
                median = None
            cell[r["method"]] = (median, r.get("status", ""))
            try:
                timeout_s = max(timeout_s, float(r.get("timeout_s") or 0)) or timeout_s
            except ValueError:
                pass
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
            median, status = val
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


def main():
    path = find_csv()
    data, timeout_s = load(path)
    timeout_ms = timeout_s * 1000.0
    templates = full_templates()
    print(f"matrix CSV: {path or '(none yet)'}  ({len(templates)} templates on x-axis)")
    for engine in ALL_ENGINES:
        cells = sum(len(v) for v in data.get(engine, {}).values())
        fig_construction_engine(engine, data, timeout_ms, templates)
        print(f"  {engine}: {cells} template-cells filled")


if __name__ == "__main__":
    main()
