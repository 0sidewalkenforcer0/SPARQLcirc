"""r9.8 Wikidata operator breadth — the full SPARQL fragment on a real KG (claims A/B/C at scale).

Beyond the single WD-path point, this shows the WHOLE fragment — BGP-join, UNION, OPTIONAL, MINUS,
and two property paths (P279+, P131+) — running on real Wikidata (P106/P27/P279/P131 extracted from
the 2.13 B `latest-truthy` dump, Standard-reified), on a STOCK engine, with the fixed O(N) compiler.
The non-monotone (OPTIONAL/MINUS, ⊖) and property-path operators — red — are exactly the fragment
NPCS/SPARQLprov cannot represent; here they run exactly at KG scale.

Data: reference/wikidata/phase2_breadth.csv. Output -> figures/final/result_r9_8_wikidata_breadth.{pdf,png}.
"""
import csv, os
import numpy as np
import figstyle as fs
from figstyle import SP_NPCS, SP_CIRCUIT, GRAY, plt

HERE = os.path.dirname(os.path.abspath(__file__))
CSV = os.path.join(HERE, "..", "reference", "wikidata", "phase2_breadth.csv")
OUT = os.path.join(HERE, "figures", "final")
CREATOR = "sparqlcirc/presentation/make_wikidata_figure.py"
# red = the fragment NPCS/SPARQLprov cannot represent (non-monotone + paths)
UNIQUE = {"OPTIONAL", "MINUS", "PATH-P279+", "PATH-P131+"}
LABEL = {"BGP-join": "BGP\njoin", "UNION": "UNION", "OPTIONAL": "OPT", "MINUS": "MINUS",
         "PATH-P279+": "P279+", "PATH-P131+": "P131+"}


def main():
    rows = list(csv.DictReader(open(CSV)))
    x = np.arange(len(rows))
    colors = [SP_CIRCUIT if r["operator"] in UNIQUE else SP_NPCS for r in rows]
    labels = [LABEL.get(r["operator"], r["operator"]) for r in rows]
    fig, axes = plt.subplots(1, 2, figsize=(fs.FIG_WIDTH, 2.75))

    def bars(ax, field, ylabel, title):
        vals, toolarge = [], []
        for i, r in enumerate(rows):
            v = r.get(field)
            if r["status"] == "ok" and v:
                vals.append(float(v))
            else:
                vals.append(np.nan); toolarge.append(i)
        ax.bar(x, vals, 0.62, color=colors, edgecolor="white", linewidth=0.3, zorder=3)
        ax.set_yscale("log")
        for i in toolarge:
            ax.scatter([i], [ax.get_ylim()[0] * 2], marker="v", s=22, color=GRAY, zorder=4)
        fs.frame(ax)
        ax.set_xticks(x, labels, fontsize=6.2)
        ax.set_ylabel(ylabel); ax.set_title(title, pad=4)
        return vals

    tvals = bars(axes[0], "total_ms", "End-to-end PQE (ms)", "Exact PQE per operator")
    for i, r in enumerate(rows):                     # annotate answer counts
        if r["status"] == "ok" and r.get("answers"):
            axes[0].annotate(f"{int(r['answers']):,}", (i, tvals[i]), xytext=(0, 3),
                             textcoords="offset points", ha="center", fontsize=5.0, color=GRAY)
    bars(axes[1], "gates", "Circuit gates", "Shared circuit size")
    fs.panel_label(axes[0], 0, x=-0.17); fs.panel_label(axes[1], 1, x=-0.17)

    handles = [fs.patch(SP_NPCS, "monotone (BGP / UNION)"),
               fs.patch(SP_CIRCUIT, "non-monotone (OPT/MINUS) + paths — baselines ✗"),
               plt.Line2D([0], [0], color=GRAY, marker="v", linestyle="none", label="construct too-large")]
    fs.top_legend(fig, handles, [h.get_label() for h in handles], ncol=3, y=0.97)
    fs.footer(fig, "Full SPARQL fragment on real Wikidata (P106/P27/P279/P131 from the 2.13 B truthy dump, "
                   "Standard-reified), stock engine + O(N) compiler. Red = the non-monotone (⊖) + property-path "
                   "fragment NPCS/SPARQLprov cannot represent — here exact at KG scale. Answer counts annotated.")
    fig.subplots_adjust(left=0.085, right=0.995, bottom=0.13, top=0.84, wspace=0.27)
    fs.save(fig, "result_r9_8_wikidata_breadth", OUT, creator=CREATOR)


if __name__ == "__main__":
    main()
