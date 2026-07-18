"""r9.2b multisource figure — content-addressed cross-source dedup (representation + PQE).

The honest multi-source story is NOT construction time (our known weakness) but the content-addressing
win: each DISTINCT derivation is stored once across sources, so the shared circuit scales with the UNION
while flat per-source how-provenance (NPCS/SPARQLprov) scales with the SUM.

(a) two sources, overlap 0→1: flat stays at the sum; the circuit shrinks toward the union (2× at full
    overlap). (b) K sources at 50% overlap: flat grows linearly, the circuit sub-linearly (→2× asymptote).

Data: reference/multisource_dedup.csv (engine-free, self-test-checked). Rendered through figstyle.
Output -> figures/final/result_r9_2b_multisource.{pdf,png}.
"""
import csv, os
import figstyle as fs
from figstyle import SP_NPCS, SP_CIRCUIT, plt

HERE = os.path.dirname(os.path.abspath(__file__))
CSV = os.path.join(HERE, "..", "reference", "multisource_dedup.csv")
OUT = os.path.join(HERE, "figures", "final")
CREATOR = "sparqlcirc/presentation/make_multisource_figure.py"


def main():
    rows = list(csv.DictReader(open(CSV)))
    ov = [r for r in rows if r["sweep"] == "overlap2"]
    ks = [r for r in rows if r["sweep"] == "ksources"]
    fig, axes = plt.subplots(1, 2, figsize=(fs.FIG_WIDTH, 2.7))

    # (a) two sources, overlap sweep
    ax = axes[0]
    x = [float(r["overlap"]) * 100 for r in ov]
    flat = [int(r["t_flat"]) for r in ov]
    circ = [int(r["t_circuit"]) for r in ov]
    ax.fill_between(x, circ, flat, color=SP_CIRCUIT, alpha=0.07, zorder=1)
    ax.plot(x, flat, color=SP_NPCS, marker="o", label="flat per-source (NPCS/SPARQLprov)", zorder=3)
    ax.plot(x, circ, color=SP_CIRCUIT, marker="s", label="shared circuit (content-addressed)", zorder=3)
    ax.annotate(f"{float(ov[-1]['size_dedup']):.1f}×", (x[-1], circ[-1]), xytext=(-4, 8),
                textcoords="offset points", ha="right", color=SP_CIRCUIT, fontweight="bold")
    fs.frame(ax)
    ax.set_xlabel("Cross-source overlap (%)"); ax.set_ylabel("Representation size (gates + edges)")
    ax.set_title("Two sources: dedup grows with overlap", pad=4)
    ax.set_ylim(0, max(flat) * 1.12)
    ax.legend(frameon=False, loc="lower left", fontsize=6.0)
    fs.panel_label(ax, 0, x=-0.20)

    # (b) K sources at fixed overlap
    ax = axes[1]
    k = [int(r["sources"]) for r in ks]
    flat = [int(r["t_flat"]) for r in ks]
    circ = [int(r["t_circuit"]) for r in ks]
    ax.fill_between(k, circ, flat, color=SP_CIRCUIT, alpha=0.07, zorder=1)
    ax.plot(k, flat, color=SP_NPCS, marker="o", label="flat per-source (Σ sources)", zorder=3)
    ax.plot(k, circ, color=SP_CIRCUIT, marker="s", label="shared circuit (∪ sources)", zorder=3)
    ax.annotate(f"{float(ks[-1]['size_dedup']):.2f}×", (k[-1], circ[-1]), xytext=(-2, 9),
                textcoords="offset points", ha="right", color=SP_CIRCUIT, fontweight="bold")
    fs.frame(ax)
    ax.set_xlabel("Number of sources (50% overlap)"); ax.set_ylabel("Representation size (gates + edges)")
    ax.set_title("More sources: flat repeats, circuit dedups", pad=4)
    ax.set_xticks(k); ax.set_ylim(0, max(flat) * 1.12)
    ax.legend(frameon=False, loc="upper left", fontsize=6.0)
    fs.panel_label(ax, 1, x=-0.20)

    ov1 = next(r for r in ov if r["overlap"] == "1.0")
    fs.footer(fig, "Content-addressing stores each derivation once across sources (circuit ~ ∪; flat ~ Σ) — a "
                   f"representation win orthogonal to construction time; it also compiles once, giving "
                   f"{float(ov1['time_dedup']):.1f}× faster end-to-end PQE at full overlap (see r9.5).")
    fig.subplots_adjust(left=0.085, right=0.995, bottom=0.20, top=0.90, wspace=0.28)
    fs.save(fig, "result_r9_2b_multisource", OUT, creator=CREATOR)


if __name__ == "__main__":
    main()
