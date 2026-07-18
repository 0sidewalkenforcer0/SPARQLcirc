"""r9.5 PQE head-to-head figure — completing NPCS/SPARQLprov into a PQE pipeline.

(a) Amortization on controlled high-sharing families (E11): one shared compile-pass vs per-answer
    completion, both with our compiler -> the win grows with answer count (up to ~8x @1000).
(b) On real WatDiv the same head-to-head is honest: per-answer/shared is only ~1.3-4x (bound queries
    are selective, per-answer provenance is simple), and OPTIONAL/MINUS templates are non-monotone ->
    NPCS/SPARQLprov cannot represent them at all (capability gap, marked ✗).

Rendered through figstyle. Output -> figures/final/result_r9_5_pqe_headtohead.{pdf,png}.
"""
import csv, os
import numpy as np
import figstyle as fs
from figstyle import SP_NPCS, SP_CIRCUIT, GRAY, plt

HERE = os.path.dirname(os.path.abspath(__file__))
REF = os.path.join(HERE, "..", "reference")
OUT = os.path.join(HERE, "figures", "final")
CREATOR = "sparqlcirc/presentation/make_pqe_figure.py"
CLASS_ORDER = ["C", "F", "L", "S", "O", "M"]


def rd(rel):
    p = os.path.join(REF, rel)
    return list(csv.DictReader(open(p))) if os.path.exists(p) else []


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def main():
    fig, axes = plt.subplots(1, 2, figsize=(fs.FIG_WIDTH, 2.75), gridspec_kw={"width_ratios": [1.0, 1.25]})

    # (a) E11 amortization curve
    ax = axes[0]
    e = sorted(rd("e11_scale.csv"), key=lambda r: int(r["N"]))
    if e:
        N = [int(r["N"]) for r in e]
        ax.plot(N, [_f(r["shared_ms"]) for r in e], color=SP_NPCS, marker="o", label="shared (compile once)")
        ax.plot(N, [_f(r["perans_ms"]) for r in e], color=SP_CIRCUIT, marker="s", linestyle="--",
                label="per-answer (NPCS-completed)")
        last = e[-1]
        mid = (_f(last["shared_ms"]) * _f(last["perans_ms"])) ** 0.5  # gap midpoint on log scale
        ax.annotate(f"{_f(last['time_win']):.1f}×", (int(last["N"]), mid),
                    xytext=(-6, 0), textcoords="offset points", ha="right", va="center",
                    color=SP_CIRCUIT, fontweight="bold")
    fs.light_log_axis(ax, "Answers (shared sub-provenance)", "Compile + WMC (ms)",
                      "Controlled families (E11): amortization")
    ax.legend(frameon=False, loc="upper left")
    fs.panel_label(ax, 0, x=-0.20)

    # (b) WatDiv per-template ratio + non-monotone capability gap
    ax = axes[1]
    def load_ratio(fn):
        d = {}
        for r in rd(fn):
            key = (r["class"], r["template"])
            if r.get("perans_status") == "ok":
                s, p = _f(r["shared_pqe_ms"]), _f(r["perans_pqe_ms"])
                if s and p:
                    d[key] = ("ratio", p / s)
            elif r.get("perans_status") == "npcs-cannot-represent-nonmonotone":
                d[key] = ("nonmono", None)
        return d
    d10, d100 = load_ratio("paper/pqe_stages_flat_10m.csv"), load_ratio("paper/pqe_stages_flat_100m.csv")
    keys = sorted(set(d10) | set(d100),
                  key=lambda k: (CLASS_ORDER.index(k[0]) if k[0] in CLASS_ORDER else 9,
                                 int("".join(c for c in k[1] if c.isdigit()) or 0)))
    x = np.arange(len(keys))
    ax.axhspan(7, 24, color=SP_CIRCUIT, alpha=0.05, zorder=0)  # "cannot represent" band
    first_nm = next((i for i, k in enumerate(keys)
                     if d10.get(k, (None,))[0] == "nonmono" or d100.get(k, (None,))[0] == "nonmono"), len(keys))
    for d, color, off, lbl in [(d10, SP_NPCS, -0.12, "10M"), (d100, SP_CIRCUIT, 0.12, "100M")]:
        rx, ry, nx = [], [], []
        for i, k in enumerate(keys):
            v = d.get(k)
            if not v:
                continue
            if v[0] == "ratio":
                rx.append(i + off); ry.append(v[1])
            else:
                nx.append(i + off)
        if rx:
            ax.scatter(rx, ry, s=16, color=color, edgecolors="white", linewidths=0.3, label=lbl, zorder=3)
        if nx:
            ax.scatter(nx, [12] * len(nx), marker="x", s=24, color=color, zorder=4)
    ax.annotate("OPTIONAL: non-monotone (⊖)\nNPCS/SPARQLprov ✗ cannot represent",
                xy=(first_nm - 0.5, 12), xytext=(-6, 0), textcoords="offset points",
                ha="right", va="center", fontsize=5.8, color=SP_CIRCUIT,
                arrowprops=dict(arrowstyle="->", color=SP_CIRCUIT, lw=0.7))
    ax.axhline(1.0, color="#555555", linestyle=fs.TIMEOUT_LS, linewidth=0.8)
    ax.annotate("break-even", (0, 1.0), xytext=(2, -8), textcoords="offset points",
                fontsize=5.4, color="#555555", va="top")
    ax.set_yscale("log"); ax.set_ylim(0.7, 24)
    ax.set_xticks(x, [t for _c, t in keys], rotation=90, fontsize=5.4)
    ax.set_ylabel("per-answer / shared PQE")
    ax.set_title("Real WatDiv: modest ratio; ✗ = NPCS cannot represent", pad=4, fontsize=8)
    ax.legend(frameon=False, loc="upper left", ncol=2, fontsize=6.2, columnspacing=1.0)
    fs.frame(ax)
    fs.panel_label(ax, 1, x=-0.14)

    fs.footer(fig, "Completing NPCS/SPARQLprov into PQE with the same compiler: amortization is dramatic on high-sharing "
                   "families (E11, up to 8.2× at 1000 answers), modest on selective WatDiv (≈1.3× median); OPTIONAL is "
                   "non-monotone (⊖) — NPCS/SPARQLprov cannot represent it at all.")
    fig.subplots_adjust(left=0.08, right=0.995, bottom=0.30, top=0.86, wspace=0.28)
    fs.save(fig, "result_r9_5_pqe_headtohead", OUT, creator=CREATOR)


if __name__ == "__main__":
    main()
